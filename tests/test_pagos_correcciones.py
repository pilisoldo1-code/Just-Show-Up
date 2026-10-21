"""Correcciones del módulo 6: mes en curso provisorio, monto exacto, fecha no futura,
anulación con historial y detalle de clases (sin tocar la base: se mockea app.pagos).

Uso: python -m unittest tests.test_pagos_correcciones -v
"""

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main, pagos
from app.auth import Usuario
from app.clases import TZ

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")
AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=TZ)

DETALLE = [{"fecha_txt": "Lunes 7/9", "hora": "08:00", "tipo": "Gimnasia", "precio": 800.0, "dictada": True},
           {"fecha_txt": "Martes 8/9", "hora": "09:00", "tipo": "Yoga", "precio": 950.0, "dictada": True}]
PAGADO = {
    "id": 2, "alumna": "Bea Dos", "cantidad_clases": 2, "total": 1750.0, "total_exacto": "1750.00",
    "estado": "pagado", "detalle": DETALLE,
    "pago": {"id": 12, "monto": 1750.0, "medio": "Transferencia", "fecha": "01/10/2026",
             "registrado_en": "01/10/2026", "registrado_por": "Profe Prueba"},
    "anulados": [{"id": 11, "monto": 1750.0, "medio": "Efectivo", "fecha": "30/09/2026",
                  "registrado_en": "01/10/2026", "registrado_por": "Profe Prueba",
                  "anulado_en": "01/10/2026", "anulado_por": "Profe Prueba", "motivo": "medio equivocado"}],
}
PENDIENTE = {
    "id": 1, "alumna": "Ana Uno", "cantidad_clases": 2, "total": 1750.0, "total_exacto": "1750.00",
    "estado": "pendiente", "detalle": DETALLE, "pago": None, "anulados": [],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        for objeto, nombre, valor in ((main, "_sb", "SB"), (main.clases, "ahora", AHORA)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def post(self, url, datos):
        return self.client.post(url, data=datos, follow_redirects=False)


class MesEnCurso(Base):
    def test_mes_en_curso_es_provisorio_sin_generar_ni_cobrar(self):
        self.como(PROFESORA)
        prov = [{"id_alumna": "x", "alumna": "Ana Uno", "dictadas": 3, "total_dictadas": 2400.0,
                 "proximas": 2, "total_proximas": 1600.0, "detalle": DETALLE}]
        with mock.patch.object(pagos, "provisorio", return_value=prov) as p, \
                mock.patch.object(pagos, "resumenes") as resumenes:
            r = self.client.get("/profesora/resumenes")
        resumenes.assert_not_called()
        p.assert_called_once_with("SB", 10, 2026, AHORA)
        self.assertIn("Provisorio", r.text)
        self.assertIn("Hasta hoy: 3 clases · $2400.00", r.text)
        self.assertIn("Reservadas para lo que queda del mes: 2 · $1600.00", r.text)
        self.assertNotIn("/profesora/resumenes/generar", r.text)
        self.assertNotIn("Registrar pago", r.text)

    def test_no_se_puede_generar_el_mes_en_curso(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "generar") as generar:
            r = self.post("/profesora/resumenes/generar", {"mes": 10, "anio": 2026})
        generar.assert_not_called()
        self.assertIn("ok=0", r.headers["location"])

    def test_no_se_puede_generar_un_mes_futuro(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "generar") as generar:
            self.post("/profesora/resumenes/generar", {"mes": 1, "anio": 2027})
        generar.assert_not_called()

    def test_mes_cerrado_igual_que_la_base(self):
        hoy = date(2026, 10, 1)
        self.assertTrue(pagos.mes_cerrado(9, 2026, hoy))
        self.assertFalse(pagos.mes_cerrado(10, 2026, hoy))
        self.assertTrue(pagos.mes_cerrado(12, 2025, date(2026, 1, 1)))
        self.assertFalse(pagos.mes_cerrado(1, 2027, hoy))


class RegistrarPago(Base):
    DATOS = {"mes": 9, "anio": 2026, "monto": "1750.00", "medio_pago": "efectivo", "fecha": "2026-10-20"}

    def pagar(self, **cambios):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "registrar_pago", return_value=(True, "Pago registrado.")) as rp:
            r = self.post("/profesora/resumenes/1/pagar", {**self.DATOS, **cambios})
        return r, rp

    def test_monto_exacto_va_como_decimal(self):
        r, rp = self.pagar()
        rp.assert_called_once_with("SB", 1, Decimal("1750.00"), "efectivo", date(2026, 10, 20))
        self.assertIn("ok=1", r.headers["location"])

    def test_fecha_futura_rechazada(self):
        r, rp = self.pagar(fecha="2026-10-21")
        rp.assert_not_called()
        self.assertIn("ok=0", r.headers["location"])

    def test_monto_invalido_rechazado(self):
        for monto in ("abc", "0", "-5", "NaN", ""):
            r, rp = self.pagar(monto=monto)
            rp.assert_not_called()

    def test_medio_desconocido_rechazado(self):
        r, rp = self.pagar(medio_pago="mercado_pago")
        rp.assert_not_called()

    def test_alumna_no_puede_pagar(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "registrar_pago") as rp:
            r = self.post("/profesora/resumenes/1/pagar", self.DATOS)
        rp.assert_not_called()
        self.assertEqual(r.status_code, 303)

    def test_mensajes_de_la_base(self):
        for codigo, texto in (("monto_distinto", "exactamente el total"), ("fecha_futura", "posterior a hoy"),
                              ("mes_abierto", "no terminó"), ("ya_pagado", "ya estaba pagado")):
            sb = mock.Mock()
            sb.rpc.return_value.execute.return_value.data = {"resultado": codigo}
            ok, aviso = pagos.registrar_pago(sb, 1, Decimal("800"), "efectivo", date(2026, 10, 1))
            self.assertFalse(ok)
            self.assertIn(texto, aviso)
        self.assertEqual(sb.rpc.call_args.args[1]["p_monto"], "800")


class Anular(Base):
    def test_motivo_obligatorio(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "anular_pago") as ap:
            for motivo in ("", "  ", "ab"):
                r = self.post("/profesora/resumenes/pagos/12/anular", {"mes": 9, "anio": 2026, "motivo": motivo})
                self.assertIn("ok=0", r.headers["location"])
        ap.assert_not_called()

    def test_anular_ok(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "anular_pago", return_value=(True, "Pago anulado.")) as ap:
            r = self.post("/profesora/resumenes/pagos/12/anular",
                          {"mes": 9, "anio": 2026, "motivo": "  medio   equivocado "})
        ap.assert_called_once_with("SB", 12, "medio equivocado")
        self.assertIn("ok=1", r.headers["location"])
        self.assertIn("mes=9", r.headers["location"])

    def test_alumna_no_puede_anular(self):
        self.como(ALUMNA)
        with mock.patch.object(pagos, "anular_pago") as ap:
            self.post("/profesora/resumenes/pagos/12/anular", {"mes": 9, "anio": 2026, "motivo": "lo que sea"})
        ap.assert_not_called()


class PantallaMesCerrado(Base):
    def test_pagado_con_historial_y_pendiente_con_monto_fijo(self):
        self.como(PROFESORA)
        with mock.patch.object(pagos, "resumenes", return_value=[PENDIENTE, PAGADO]):
            r = self.client.get("/profesora/resumenes?mes=9&anio=2026")
        t = r.text
        self.assertIn("/profesora/resumenes/generar", t)
        # pendiente: el monto viaja oculto y es el total exacto; no hay campo para escribirlo
        self.assertIn('<input type="hidden" name="monto" value="1750.00" />', t)
        self.assertNotIn('type="number" name="monto"', t)
        self.assertIn('max="2026-10-20"', t)
        # pagado: pago vigente identificado, anulación con confirmación y motivo, historial
        self.assertIn("Pago #12: $1750.00 · Transferencia · 01/10/2026", t)
        self.assertIn("/profesora/resumenes/pagos/12/anular", t)
        self.assertIn("Confirmar anulación", t)
        self.assertIn("Pago #11", t)
        self.assertIn("«medio equivocado»", t)
        # detalle de clases
        for texto in ("Lunes 7/9 · 08:00", "Gimnasia", "Yoga", "$950.00"):
            self.assertIn(texto, t)


class MisFacturasDetalle(Base):
    def test_detalle_y_mes_en_curso_solo_de_la_alumna(self):
        self.como(ALUMNA)
        facturas = [{"id": 2, "mes_txt": "Setiembre 2026", "cantidad_clases": 2, "total": 1750.0,
                     "estado": "pendiente", "pago": None, "detalle": DETALLE}]
        prov = [{"id_alumna": "a", "alumna": "Ana Prueba", "dictadas": 1, "total_dictadas": 800.0,
                 "proximas": 1, "total_proximas": 800.0,
                 "detalle": [{**DETALLE[0], "fecha_txt": "Martes 20/10", "dictada": False}]}]
        with mock.patch.object(pagos, "mis_facturas", return_value=facturas) as mf, \
                mock.patch.object(pagos, "provisorio", return_value=prov) as p:
            r = self.client.get("/mis-facturas")
        mf.assert_called_once_with("SB", "a")
        p.assert_called_once_with("SB", 10, 2026, AHORA, alumna_id="a")
        t = r.text
        for texto in ("Octubre 2026", "(en curso)", "Provisorio", "todavía no se dictó",
                      "Setiembre 2026", "Lunes 7/9 · 08:00", "Yoga", "$950.00", "Total", "$1750.00"):
            self.assertIn(texto, t)


class Logica(unittest.TestCase):
    def test_pagos_separa_vigente_de_anulados(self):
        filas = [
            {"id": 11, "monto": "800.00", "medio_pago": "efectivo", "fecha": "2026-10-01", "estado": "anulado",
             "creado_en": "2026-10-01T12:00:00+00:00", "anulado_en": "2026-10-02T12:00:00+00:00",
             "motivo_anulacion": "error", "registrado": {"nombre": "P", "apellido": "Q"},
             "anulador": {"nombre": "P", "apellido": "Q"}},
            {"id": 12, "monto": "800.00", "medio_pago": "transferencia", "fecha": "2026-10-02", "estado": "vigente",
             "creado_en": "2026-10-02T12:00:00+00:00", "anulado_en": None, "motivo_anulacion": None},
        ]
        vigente, anulados = pagos._pagos(filas)
        self.assertEqual(vigente["id"], 12)
        self.assertEqual(vigente["medio"], "Transferencia")
        self.assertEqual([a["id"] for a in anulados], [11])
        self.assertEqual(anulados[0]["motivo"], "error")
        self.assertEqual(anulados[0]["anulado_en"], "02/10/2026")

    def test_provisorio_misma_regla_que_la_base(self):
        def fila(alumna, fecha, hora, precio, estado="confirmada", estado_clase="programada"):
            return {"id_alumna": alumna, "estado": estado, "profiles": {"nombre": alumna, "apellido": "X"},
                    "clase": {"fecha": fecha, "hora_inicio": hora, "precio": precio, "estado": estado_clase,
                              "tipo_clase": {"nombre": "Gimnasia"}}}
        filas = [
            fila("ana", "2026-10-05", "08:00:00", "800.00"),
            fila("ana", "2026-10-20", "10:00:00", "900.00"),        # empieza justo ahora: se cobra (<=)
            fila("ana", "2026-10-20", "11:00:00", "950.00"),        # todavía no empezó
            fila("ana", "2026-10-06", "08:00:00", "800.00", estado="cancelada"),
            fila("ana", "2026-10-07", "08:00:00", "800.00", estado_clase="cancelada"),
            fila("bea", "2026-10-28", "08:00:00", "800.00"),
        ]
        r = {a["id_alumna"]: a for a in pagos.armar_provisorio(filas, AHORA)}
        self.assertEqual((r["ana"]["dictadas"], r["ana"]["total_dictadas"]), (2, 1700.0))
        self.assertEqual((r["ana"]["proximas"], r["ana"]["total_proximas"]), (1, 950.0))
        self.assertEqual([d["dictada"] for d in r["ana"]["detalle"]], [True, True, False])
        self.assertEqual((r["bea"]["dictadas"], r["bea"]["proximas"]), (0, 1))

    def test_consulta_del_mes_en_curso_cruza_el_anio(self):
        sb = mock.MagicMock()
        consulta = sb.table.return_value.select.return_value
        for metodo in ("eq", "gte", "lt"):
            getattr(consulta, metodo).return_value = consulta
        consulta.execute.return_value.data = []
        pagos.provisorio(sb, 12, 2026, AHORA, alumna_id="a")
        sb.table.assert_called_once_with("reserva")
        consulta.gte.assert_called_once_with("clase.fecha", "2026-12-01")
        consulta.lt.assert_called_once_with("clase.fecha", "2027-01-01")
        self.assertIn(mock.call("id_alumna", "a"), consulta.eq.call_args_list)
        self.assertIn(mock.call("estado", "confirmada"), consulta.eq.call_args_list)
        self.assertIn(mock.call("clase.estado", "programada"), consulta.eq.call_args_list)

    def test_parsear_monto(self):
        self.assertEqual(pagos.parsear_monto("1600,50"), Decimal("1600.50"))
        for malo in ("", "abc", "0", "-1", "inf", "NaN"):
            self.assertIsNone(pagos.parsear_monto(malo))


if __name__ == "__main__":
    unittest.main()
