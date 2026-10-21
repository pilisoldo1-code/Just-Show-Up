"""Pruebas de Resúmenes y pagos (sin tocar la base: se mockea app.pagos).

Uso: python -m unittest tests.test_pagos -v
"""

import unittest
from datetime import date, datetime
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main, pagos
from app.auth import Usuario
from app.clases import TZ

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")

RESUMEN_PENDIENTE = {
    "id": 1, "alumna": "Ana Uno", "cantidad_clases": 2, "total": 1600.0,
    "estado": "pendiente", "pago": None, "total_exacto": "1600.00", "detalle": [], "anulados": [],
}
RESUMEN_PAGADO = {
    "id": 2, "alumna": "Bea Dos", "cantidad_clases": 1, "total": 800.0,
    "estado": "pagado", "total_exacto": "800.00", "detalle": [], "anulados": [],
    "pago": {"id": 7, "monto": 800.0, "medio": "Efectivo", "fecha": "05/10/2026",
             "registrado_en": "05/10/2026", "registrado_por": "Profe Prueba"},
}
HOY = date(2026, 10, 20)
AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=TZ)


class Helpers(unittest.TestCase):
    def test_mes_anterior_cruza_anio(self):
        self.assertEqual(pagos.mes_anterior(1, 2026), (12, 2025))
        self.assertEqual(pagos.mes_anterior(5, 2026), (4, 2026))

    def test_mes_siguiente_cruza_anio(self):
        self.assertEqual(pagos.mes_siguiente(12, 2026), (1, 2027))
        self.assertEqual(pagos.mes_siguiente(5, 2026), (6, 2026))


class Pantalla(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        p = mock.patch.object(main, "_sb", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(main.clases, "ahora", return_value=AHORA)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(pagos, "provisorio", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.get("/profesora/resumenes", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_alumna_no_puede_entrar(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "resumenes") as resumenes:
            r = self.client.get("/profesora/resumenes", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/?"))
        resumenes.assert_not_called()

    def test_profesora_ve_la_pantalla(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "resumenes", return_value=[RESUMEN_PENDIENTE, RESUMEN_PAGADO]):
            r = self.client.get("/profesora/resumenes?mes=9&anio=2026")
        self.assertEqual(r.status_code, 200)
        for texto in ("Resúmenes mensuales", "Ana Uno", "$1600.00", "Pendiente", "Bea Dos", "Pagado", "Efectivo"):
            self.assertIn(texto, r.text)

    def test_mes_futuro_vuelve_al_actual(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "provisorio", return_value=[]) as provisorio:
            self.client.get("/profesora/resumenes?mes=3&anio=2027")
        provisorio.assert_called_once()
        self.assertEqual(provisorio.call_args.args[1:3], (10, 2026))

    def test_sin_resumenes_muestra_vacio(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "resumenes", return_value=[]):
            r = self.client.get("/profesora/resumenes?mes=9&anio=2026")
        self.assertIn("Todavía no se generaron resúmenes", r.text)

    def test_error_de_base_muestra_aviso(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "resumenes", side_effect=RuntimeError("caída")), \
                self.assertLogs(level="ERROR"):
            r = self.client.get("/profesora/resumenes?mes=9&anio=2026")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No pudimos cargar los resúmenes", r.text)

    def test_generar_ok(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "generar", return_value=(True, "Se generaron 3 resúmenes.")):
            r = self.client.post("/profesora/resumenes/generar", data={"mes": 9, "anio": 2026},
                                  follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("Se+generaron+3+res%C3%BAmenes", r.headers["location"].replace("%2E", "."))

    def test_alumna_no_puede_generar(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "generar") as generar:
            r = self.client.post("/profesora/resumenes/generar", data={"mes": 10, "anio": 2026},
                                  follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        generar.assert_not_called()

    def test_registrar_pago_ok(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "registrar_pago", return_value=(True, "Pago registrado.")) as rp:
            r = self.client.post(
                "/profesora/resumenes/1/pagar",
                data={"mes": 10, "anio": 2026, "monto": "1600.00", "medio_pago": "efectivo", "fecha": "2026-10-20"},
                follow_redirects=False,
            )
        self.assertEqual(r.status_code, 303)
        self.assertIn("ok=1", r.headers["location"])
        rp.assert_called_once()
        self.assertEqual(rp.call_args.args[1:3], (1, 1600.0))

    def test_registrar_pago_ya_pagado(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "registrar_pago", return_value=(False, "Ese resumen ya estaba pagado.")):
            r = self.client.post(
                "/profesora/resumenes/2/pagar",
                data={"mes": 10, "anio": 2026, "monto": "800", "medio_pago": "efectivo", "fecha": "2026-10-20"},
                follow_redirects=False,
            )
        self.assertIn("ok=0", r.headers["location"])

    def test_registrar_pago_fecha_invalida(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "registrar_pago") as rp:
            r = self.client.post(
                "/profesora/resumenes/1/pagar",
                data={"mes": 10, "anio": 2026, "monto": "100", "medio_pago": "efectivo", "fecha": "no-es-fecha"},
                follow_redirects=False,
            )
        self.assertIn("ok=0", r.headers["location"])
        rp.assert_not_called()

    def test_menu_profesora_tiene_resumenes(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "resumenes", return_value=[]):
            self.assertIn('href="/profesora/resumenes"', self.client.get("/profesora/resumenes").text)


class MisFacturas(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        for objeto, nombre, valor in ((main, "_sb", None), (main.clases, "ahora", AHORA), (pagos, "provisorio", [])):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.get("/mis-facturas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_profesora_no_puede_entrar(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "mis_facturas") as mf:
            r = self.client.get("/mis-facturas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        mf.assert_not_called()

    def test_alumna_ve_sus_facturas(self):
        self.como(ALUMNA)
        facturas = [
            {"id": 1, "mes_txt": "Octubre 2026", "cantidad_clases": 2, "total": 1600.0,
             "estado": "pendiente", "pago": None, "detalle": []},
            {"id": 2, "mes_txt": "Setiembre 2026", "cantidad_clases": 4, "total": 3200.0, "detalle": [],
             "estado": "pagado", "pago": {"id": 3, "monto": 3200.0, "medio": "Transferencia", "fecha": "02/10/2026"}},
        ]
        with mock.patch.object(pagos, "mis_facturas", return_value=facturas) as mf:
            r = self.client.get("/mis-facturas")
        self.assertEqual(r.status_code, 200)
        mf.assert_called_once_with(None, "a")
        for texto in ("Octubre 2026", "$1600.00", "Pendiente", "Setiembre 2026", "Pagado", "Transferencia"):
            self.assertIn(texto, r.text)

    def test_alumna_sin_facturas_muestra_vacio(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "mis_facturas", return_value=[]):
            r = self.client.get("/mis-facturas")
        self.assertIn("Todavía no tenés facturas", r.text)

    def test_error_de_base_muestra_aviso(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "mis_facturas", side_effect=RuntimeError("caída")), \
                self.assertLogs(level="ERROR"):
            r = self.client.get("/mis-facturas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No pudimos cargar tus facturas", r.text)


if __name__ == "__main__":
    unittest.main()
