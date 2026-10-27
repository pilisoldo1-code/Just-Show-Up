"""Facturación mensual (módulo 6): rutas, permisos, validaciones y lógica pura.
Sin base: se mockea app.facturas. Las reglas de la base se prueban aparte en un Postgres local.

Uso: python -m unittest tests.test_facturas -v
"""

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, facturas, main
from app.auth import Usuario
from app.clases import TZ

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")
AHORA = datetime(2026, 6, 30, 19, 30, tzinfo=TZ)
D = Decimal

LINEAS = [
    {"id": 10, "fecha_txt": "Lunes 29/6", "fecha_corta": "29/06/2026", "hora": "08:00", "tipo": "Gimnasia",
     "precio_original": D("800.00"), "ajuste_tipo": "importe", "importe_final": D("600.00"), "ajuste": D("-200.00"),
     "ajuste_txt": "Descuento", "motivo": "llegó tarde"},
    {"id": 11, "fecha_txt": "Martes 30/6", "fecha_corta": "30/06/2026", "hora": "18:00", "tipo": "Pilates",
     "precio_original": D("950.00"), "ajuste_tipo": "ninguno", "importe_final": D("950.00"), "ajuste": D("0.00"),
     "ajuste_txt": "", "motivo": ""},
]


def factura(**cambios):
    f = {
        "id": 1, "id_alumna": "x", "alumna": "Ana Uno", "mes": 6, "anio": 2026, "mes_txt": "Junio 2026",
        "estado_factura": "borrador", "estado_pago": "pendiente", "estado_factura_txt": "Borrador",
        "estado_pago_txt": "Pendiente de pago", "cantidad_clases": 2, "subtotal_original": D("1750.00"),
        "total_lineas": D("1550.00"), "descuento_tipo": "porcentaje", "descuento_valor": 10,
        "descuento_monto": D("155.00"), "total": D("1395.00"), "descuento_txt": "10%", "descuento_motivo": "frecuente",
        "version": 7, "numero": None, "confirmada_txt": "", "lineas": LINEAS, "pago": None, "pagos_anulados": [],
        "historial": [{"en": "30/06/2026 12:00", "por": "Profe Prueba", "accion": "Ajuste de clase",
                       "texto": "Lunes 29/6 08:00: $800.00 → $600.00", "motivo": "llegó tarde"}],
        "reemplazada_por": None, "reemplaza": None, "reemplaza_a": None, "reemplazada_por_id": None, "reemplaza_id": None,
        "anulada_txt": "", "cedula_txt": "1.234.567-2", "identificacion_fija": False,
    }
    f.update(cambios)
    return f


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


class Acceso(Base):
    RUTAS_GET = ["/profesora/resumenes", "/profesora/resumenes/1", "/profesora/resumenes/confirmar-mes?mes=6&anio=2026"]
    RUTAS_POST = {
        "/profesora/resumenes/1/lineas/10/ajustar": {"version": 1, "tipo": "bonificada", "motivo": "xxxx"},
        "/profesora/resumenes/1/lineas/10/quitar-ajuste": {"version": 1, "motivo": "xxxx"},
        "/profesora/resumenes/1/descuento": {"version": 1, "tipo": "monto", "valor": "10", "motivo": "xxxx"},
        "/profesora/resumenes/1/confirmar": {"version": 1},
        "/profesora/resumenes/confirmar-mes": {"mes": 6, "anio": 2026, "firma": "x"},
        "/profesora/resumenes/1/pagar": {"monto": "1", "medio_pago": "efectivo", "fecha": "2026-06-30"},
        "/profesora/resumenes/1/pagos/3/anular": {"motivo": "xxxx"},
    }

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        for url in self.RUTAS_GET + ["/mis-facturas"]:
            r = self.client.get(url, follow_redirects=False)
            self.assertTrue(r.headers["location"].startswith("/login"), url)

    def test_alumna_no_entra_ni_ejecuta_nada_de_la_profesora(self):
        self.como(ALUMNA)
        with mock.patch.object(main, "facturas") as f:
            for url in self.RUTAS_GET:
                r = self.client.get(url, follow_redirects=False)
                self.assertTrue(r.headers["location"].startswith("/?"), url)
            for url, datos in self.RUTAS_POST.items():
                r = self.post(url, datos)
                self.assertTrue(r.headers["location"].startswith("/?"), url)
        f.assert_not_called()
        self.assertFalse([c for c in f.mock_calls if not c[0].startswith(("MOTIVO", "MEDIOS"))])

    def test_profesora_no_entra_a_mis_facturas(self):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "mis_facturas") as mf:
            r = self.client.get("/mis-facturas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        mf.assert_not_called()

    def test_menu_dice_facturacion(self):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "sincronizar"), mock.patch.object(facturas, "facturas_del_mes", return_value=[]), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={}):
            r = self.client.get("/profesora/resumenes?mes=6&anio=2026")
        self.assertIn(">Facturación</a>", r.text)


class PantallaMes(Base):
    def test_durante_el_mes_solo_provisorio(self):
        self.como(PROFESORA)
        en_curso = datetime(2026, 6, 20, 12, 0, tzinfo=TZ)
        prov = [{"id_alumna": "x", "alumna": "Ana Uno", "dictadas": 3, "total_dictadas": D("2400.00"),
                 "proximas": 2, "total_proximas": D("1600.00"), "detalle": []}]
        with mock.patch.object(main.clases, "ahora", return_value=en_curso), \
                mock.patch.object(facturas, "provisorio", return_value=prov) as p, \
                mock.patch.object(facturas, "sincronizar") as sinc:
            r = self.client.get("/profesora/resumenes?mes=6&anio=2026")
        sinc.assert_not_called()
        p.assert_called_once()
        self.assertIn("Provisorio", r.text)
        self.assertIn("Hasta hoy: 3 clases · $2400.00", r.text)
        self.assertIn("desde el último día del mes (30/6)", r.text)
        self.assertNotIn("Confirmar todas", r.text)

    def test_ultimo_dia_sincroniza_y_lista(self):
        self.como(PROFESORA)
        previa = {"resultado": "ok", "periodo_finalizado": True, "cantidad": 1, "total": D("1395.00"), "firma": "f"}
        with mock.patch.object(facturas, "sincronizar") as sinc, \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[factura()]), \
                mock.patch.object(facturas, "previsualizar_mes", return_value=previa):
            r = self.client.get("/profesora/resumenes?mes=6&anio=2026")
        sinc.assert_called_once_with("SB", 6, 2026)
        for texto in ("Ana Uno", "$1395.00", "Original: $1750.00", "Borrador", "Confirmar todas las facturas del mes (1)",
                      "desde el 1/7/2026"):
            self.assertIn(texto, r.text)

    def test_con_clases_sin_terminar_no_ofrece_confirmar(self):
        self.como(PROFESORA)
        previa = {"resultado": "ok", "periodo_finalizado": False, "cantidad": 1, "total": D("1"), "firma": "f"}
        with mock.patch.object(facturas, "sincronizar"), \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[factura()]), \
                mock.patch.object(facturas, "previsualizar_mes", return_value=previa):
            r = self.client.get("/profesora/resumenes?mes=6&anio=2026")
        self.assertIn("Todavía hay clases de Junio 2026 sin terminar", r.text)
        self.assertNotIn('href="/profesora/resumenes/confirmar-mes', r.text)

    def test_mes_futuro_vuelve_al_actual(self):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "sincronizar") as sinc, \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[]), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={}):
            self.client.get("/profesora/resumenes?mes=3&anio=2027")
        sinc.assert_called_once_with("SB", 6, 2026)

    def test_error_de_base_muestra_aviso(self):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "sincronizar", side_effect=RuntimeError("caída")), self.assertLogs(level="ERROR"):
            r = self.client.get("/profesora/resumenes?mes=6&anio=2026")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No pudimos cargar la facturación", r.text)


class PantallaFactura(Base):
    def ver(self, f, periodo_finalizado=True):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "factura", return_value=f), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={"periodo_finalizado": periodo_finalizado}):
            return self.client.get("/profesora/resumenes/1").text

    def test_borrador_editable_con_motivos_y_version(self):
        t = self.ver(factura())
        for texto in ("Ajustar", "Quitar ajuste", "«llegó tarde»", "Descuento general (10%)", "«frecuente»", "−$155.00",
                      "$1395.00", 'name="version" value="7"', "Confirmar factura", "Historial"):
            self.assertIn(texto, t)

    def test_borrador_con_periodo_abierto_no_se_confirma(self):
        t = self.ver(factura(), periodo_finalizado=False)
        self.assertIn("Todavía hay clases del mes sin terminar", t)
        self.assertNotIn("/profesora/resumenes/1/confirmar", t)

    def test_confirmada_bloqueada_y_cobrable(self):
        t = self.ver(factura(estado_factura="confirmada", estado_factura_txt="Confirmada", numero="JSU-2026-06-0001"))
        self.assertNotIn("Ajustar", t)
        self.assertNotIn("/descuento", t)
        self.assertIn("JSU-2026-06-0001", t)
        self.assertIn('<input type="hidden" name="monto" value="1395.00" />', t)
        self.assertNotIn('type="number" name="monto"', t)
        self.assertIn('max="2026-06-30"', t)

    def test_pagada_con_historial_de_anulados(self):
        pago = {"id": 5, "monto": D("1395.00"), "medio": "Transferencia", "fecha": "01/07/2026",
                "registrado_en": "01/07/2026 10:00", "registrado_por": "Profe Prueba"}
        anulado = {**pago, "id": 4, "medio": "Efectivo", "motivo": "medio equivocado",
                   "anulado_en": "01/07/2026 09:00", "anulado_por": "Profe Prueba"}
        t = self.ver(factura(estado_factura="confirmada", estado_pago="pagado", numero="JSU-1", pago=pago,
                             pagos_anulados=[anulado]))
        for texto in ("Pago #5", "/pagos/5/anular", "Pago #4", "«medio equivocado»"):
            self.assertIn(texto, t)

    def test_sin_cargo(self):
        t = self.ver(factura(estado_factura="confirmada", estado_pago="sin_cargo", total=D("0.00")))
        self.assertIn("sin cargo", t)
        self.assertNotIn("Registrar pago", t)

    def test_inexistente_vuelve_al_mes(self):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "factura", return_value=None):
            r = self.client.get("/profesora/resumenes/99", follow_redirects=False)
        self.assertEqual(r.status_code, 303)


class Formularios(Base):
    def setUp(self):
        super().setUp()
        self.como(PROFESORA)

    def test_ajuste_valida_motivo_tipo_e_importe(self):
        with mock.patch.object(facturas, "ajustar_linea") as aj:
            for datos in ({"version": 7, "tipo": "bonificada", "motivo": "ab"},
                          {"version": 7, "tipo": "regalo", "motivo": "motivo ok"},
                          {"version": 7, "tipo": "importe", "importe": "abc", "motivo": "motivo ok"},
                          {"version": 7, "tipo": "importe", "importe": "-5", "motivo": "motivo ok"},
                          {"version": 7, "tipo": "importe", "importe": "1.234", "motivo": "motivo ok"}):
                r = self.post("/profesora/resumenes/1/lineas/10/ajustar", datos)
                self.assertIn("ok=0", r.headers["location"])
        aj.assert_not_called()

    def test_ajuste_ok_pasa_decimal_y_version(self):
        with mock.patch.object(facturas, "ajustar_linea", return_value=(True, "ok")) as aj:
            r = self.post("/profesora/resumenes/1/lineas/10/ajustar",
                          {"version": 7, "tipo": "importe", "importe": "600,5", "motivo": "  llegó   tarde "})
        aj.assert_called_once_with("SB", 10, "importe", D("600.5"), "llegó tarde", 7)
        self.assertTrue(r.headers["location"].startswith("/profesora/resumenes/1?"))

    def test_bonificar_no_manda_importe(self):
        with mock.patch.object(facturas, "ajustar_linea", return_value=(True, "ok")) as aj:
            self.post("/profesora/resumenes/1/lineas/10/ajustar", {"version": 7, "tipo": "bonificada", "importe": "99", "motivo": "regalo"})
        aj.assert_called_once_with("SB", 10, "bonificada", None, "regalo", 7)

    def test_descuento(self):
        with mock.patch.object(facturas, "descuento", return_value=(True, "ok")) as d:
            self.post("/profesora/resumenes/1/descuento", {"version": 7, "tipo": "porcentaje", "valor": "10", "motivo": "frecuente"})
            self.post("/profesora/resumenes/1/descuento", {"version": 7, "tipo": "quitar", "valor": "", "motivo": "ya no"})
            r = self.post("/profesora/resumenes/1/descuento", {"version": 7, "tipo": "monto", "valor": "0", "motivo": "cero"})
        self.assertEqual(d.call_args_list, [mock.call("SB", 1, "porcentaje", D("10"), "frecuente", 7),
                                             mock.call("SB", 1, None, None, "ya no", 7)])
        self.assertIn("ok=0", r.headers["location"])

    def test_confirmar_y_confirmar_mes(self):
        with mock.patch.object(facturas, "confirmar", return_value=(True, "ok")) as c, \
                mock.patch.object(facturas, "confirmar_mes", return_value=(False, "cambio")) as cm:
            self.post("/profesora/resumenes/1/confirmar", {"version": 7})
            r = self.post("/profesora/resumenes/confirmar-mes", {"mes": 6, "anio": 2026, "firma": "abc"})
        c.assert_called_once_with("SB", 1, 7)
        cm.assert_called_once_with("SB", 6, 2026, "abc")
        self.assertIn("ok=0", r.headers["location"])

    def test_pago_valida_fecha_monto_y_medio(self):
        with mock.patch.object(facturas, "registrar_pago", return_value=(True, "ok")) as rp:
            for cambios in ({"fecha": "2026-07-01"}, {"fecha": "no"}, {"monto": "0"}, {"monto": "abc"},
                            {"medio_pago": "mercado_pago"}):
                datos = {"monto": "1395.00", "medio_pago": "efectivo", "fecha": "2026-06-30", **cambios}
                r = self.post("/profesora/resumenes/1/pagar", datos)
                self.assertIn("ok=0", r.headers["location"], cambios)
            rp.assert_not_called()
            self.post("/profesora/resumenes/1/pagar", {"monto": "1395.00", "medio_pago": "efectivo", "fecha": "2026-06-30"})
        rp.assert_called_once_with("SB", 1, D("1395.00"), "efectivo", date(2026, 6, 30))

    def test_anular_pago_exige_motivo(self):
        with mock.patch.object(facturas, "anular_pago", return_value=(True, "ok")) as ap:
            r = self.post("/profesora/resumenes/1/pagos/3/anular", {"motivo": "  "})
            self.assertIn("ok=0", r.headers["location"])
            self.post("/profesora/resumenes/1/pagos/3/anular", {"motivo": "medio  equivocado"})
        ap.assert_called_once_with("SB", 3, "medio equivocado")

    def test_vista_previa_del_mes(self):
        previa = {"resultado": "ok", "periodo_finalizado": True, "cantidad": 2, "total": D("3000.00"), "firma": "abc",
                  "desactualizadas": 0, "sin_clases": 1}
        with mock.patch.object(facturas, "previsualizar_mes", return_value=previa), \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[factura(), factura(id=2, alumna="Bea Dos"),
                                                                              factura(id=3, estado_factura="confirmada")]):
            t = self.client.get("/profesora/resumenes/confirmar-mes?mes=6&anio=2026").text
        for texto in ("Vas a confirmar <strong>2 facturas</strong>", "$3000.00", 'name="firma" value="abc"', "Bea Dos",
                      "1 borrador queda afuera"):
            self.assertIn(texto, t)


class MisFacturas(Base):
    def test_lista_solo_lo_que_devuelve_la_base_sin_motivos(self):
        self.como(ALUMNA)
        f = factura(estado_factura="confirmada", numero="JSU-2026-06-0001", estado_pago="pagado", estado_pago_txt="Pagada",
                    pago={"monto": D("1395.00"), "medio": "Efectivo", "fecha": "01/07/2026"})
        with mock.patch.object(facturas, "mis_facturas", return_value=[f]) as mf:
            t = self.client.get("/mis-facturas").text
        mf.assert_called_once_with("SB", "a")
        for texto in ("Junio 2026", "JSU-2026-06-0001", "Descuento", "−$200.00", "$600.00", "Pilates",
                      "Descuento general (10%)", "$1395.00", "Pagada"):
            self.assertIn(texto, t)
        for interno in ("llegó tarde", "frecuente", "Historial", "Ajustar"):
            self.assertNotIn(interno, t)

    def test_vacio(self):
        self.como(ALUMNA)
        with mock.patch.object(facturas, "mis_facturas", return_value=[]):
            self.assertIn("Todavía no tenés facturas", self.client.get("/mis-facturas").text)


def _texto_pdf(datos: bytes) -> str:
    import re
    import zlib
    return "\n".join(zlib.decompress(m.group(1)).decode("latin-1")
                     for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", datos, re.S))


class Pdf(Base):
    def test_alumna_descarga_su_factura(self):
        self.como(ALUMNA)
        f = factura(estado_factura="confirmada", numero="JSU-2026-06-0001")
        with mock.patch.object(facturas, "mi_factura", return_value=f) as mf:
            r = self.client.get("/mis-facturas/1/pdf")
        mf.assert_called_once_with("SB", "a", 1)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertIn('filename="factura-JSU-2026-06-0001.pdf"', r.headers["content-disposition"])
        self.assertEqual(r.headers["cache-control"], "no-store")
        t = _texto_pdf(r.content)
        for texto in ("Just Show Up", "Factura mensual", "JSU-2026-06-0001", "Ana Uno", "1.234.567-2", "Junio 2026", "$ 1.395,00",
                      "-$ 200,00", "Documento simulado"):
            self.assertIn(texto, t)
        self.assertNotIn("tarde", t)
        self.assertNotIn("BORRADOR", t)

    def test_alumna_sin_acceso_vuelve_a_mis_facturas(self):
        self.como(ALUMNA)
        with mock.patch.object(facturas, "mi_factura", return_value=None):
            r = self.client.get("/mis-facturas/99/pdf", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/mis-facturas?"))

    def test_roles_cruzados(self):
        self.como(ALUMNA)
        with mock.patch.object(facturas, "factura") as fa:
            r = self.client.get("/profesora/resumenes/1/pdf", follow_redirects=False)
        fa.assert_not_called()
        self.assertEqual(r.status_code, 303)
        self.como(PROFESORA)
        with mock.patch.object(facturas, "mi_factura") as mf:
            self.client.get("/mis-facturas/1/pdf", follow_redirects=False)
        mf.assert_not_called()

    def test_profesora_borrador_y_anulada_marcados(self):
        self.como(PROFESORA)
        for estado, marca in (("borrador", "BORRADOR"), ("anulada", "ANULADA")):
            with mock.patch.object(facturas, "factura", return_value=factura(estado_factura=estado, numero="JSU-9")):
                self.assertIn(marca, _texto_pdf(self.client.get("/profesora/resumenes/1/pdf").content))


class AnularFactura(Base):
    def setUp(self):
        super().setUp()
        self.como(PROFESORA)

    def test_exige_motivo_y_lleva_al_borrador_nuevo(self):
        with mock.patch.object(facturas, "anular_factura", return_value=(True, "ok", 8)) as af:
            r = self.post("/profesora/resumenes/1/anular", {"motivo": "x"})
            self.assertIn("ok=0", r.headers["location"])
            r = self.post("/profesora/resumenes/1/anular", {"motivo": "se cobró de más"})
        af.assert_called_once_with("SB", 1, "se cobró de más")
        self.assertTrue(r.headers["location"].startswith("/profesora/resumenes/8?ok=1"))

    def test_error_vuelve_a_la_misma(self):
        with mock.patch.object(facturas, "anular_factura", return_value=(False, "tiene pago", None)):
            r = self.post("/profesora/resumenes/1/anular", {"motivo": "se cobró de más"})
        self.assertTrue(r.headers["location"].startswith("/profesora/resumenes/1?ok=0"))

    def ver(self, f):
        with mock.patch.object(facturas, "factura", return_value=f), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={"periodo_finalizado": True}):
            return self.client.get("/profesora/resumenes/1").text

    def test_pantallas(self):
        t = self.ver(factura(estado_factura="confirmada", numero="JSU-1"))
        self.assertIn("Anular y reemitir", t)
        pago = {"id": 5, "monto": D("1"), "medio": "Efectivo", "fecha": "x", "registrado_en": "x", "registrado_por": "x"}
        t = self.ver(factura(estado_factura="confirmada", numero="JSU-1", estado_pago="pagado", pago=pago))
        self.assertNotIn("Anular y reemitir", t)
        self.assertIn("primero anulá el pago", t)
        t = self.ver(factura(estado_factura="anulada", numero="JSU-1", anulada_txt="01/07/2026",
                             reemplazada_por_id=8, reemplazada_por="JSU-2"))
        self.assertIn('Reemplazada por <a class="lc-accion" href="/profesora/resumenes/8">JSU-2</a>', t)
        self.assertNotIn("Ajustar", t)
        self.assertNotIn("Registrar pago", t)


class Identificacion(Base):
    def ver(self, f):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "factura", return_value=f), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={"periodo_finalizado": True}):
            return self.client.get("/profesora/resumenes/1").text

    def test_detalle_muestra_nombre_cedula_y_periodo(self):
        t = self.ver(factura())
        for texto in ("<dt>Alumna</dt><dd>Ana Uno</dd>", "<dt>Cédula</dt><dd>1.234.567-2</dd>", "<dt>Período</dt><dd>Junio 2026</dd>",
                      "al confirmar quedan fijos"):
            self.assertIn(texto, t)

    def test_borrador_sin_cedula_avisa_pero_deja_confirmar(self):
        t = self.ver(factura(cedula_txt=""))
        self.assertIn("<dt>Cédula</dt><dd>no registrada</dd>", t)
        self.assertIn("La alumna no tiene cédula registrada", t)
        self.assertIn("/profesora/resumenes/1/confirmar", t)

    def test_confirmada_no_muestra_aviso_de_perfil(self):
        t = self.ver(factura(estado_factura="confirmada", numero="JSU-1", identificacion_fija=True, cedula_txt=""))
        self.assertIn("no registrada", t)
        self.assertNotIn("al confirmar quedan fijos", t)
        self.assertNotIn("La alumna no tiene cédula registrada", t)

    def test_vista_previa_avisa_facturas_sin_cedula(self):
        self.como(PROFESORA)
        previa = {"resultado": "ok", "periodo_finalizado": True, "cantidad": 2, "total": D("1"), "firma": "f",
                  "desactualizadas": 0, "sin_clases": 0, "sin_cedula": 1}
        with mock.patch.object(facturas, "previsualizar_mes", return_value=previa), \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[factura(), factura(id=2, cedula_txt="")]):
            t = self.client.get("/profesora/resumenes/confirmar-mes?mes=6&anio=2026").text
        self.assertIn("1 factura es de una alumna sin cédula registrada", t)
        self.assertIn("Sin cédula", t)
        self.assertIn("Confirmar 2 facturas", t)

    def test_mis_facturas_y_pdf_sin_cedula(self):
        self.como(ALUMNA)
        f = factura(estado_factura="confirmada", numero="JSU-1", identificacion_fija=True, cedula_txt="")
        with mock.patch.object(facturas, "mis_facturas", return_value=[f]):
            self.assertIn("<dt>Cédula</dt><dd>no registrada</dd>", self.client.get("/mis-facturas").text)
        with mock.patch.object(facturas, "mi_factura", return_value=f):
            self.assertIn("(no registrada)", _texto_pdf(self.client.get("/mis-facturas/1/pdf").content))

    def test_snapshot_vs_perfil_actual(self):
        perfiles = {"x": {"nombre": "Nuevo", "apellido": "Nombre", "cedula": "12345672"}}
        borrador = {"id_alumna": "x", "alumna_nombre": None}
        confirmada = {"id_alumna": "x", "alumna_nombre": "Viejo", "alumna_apellido": "Nombre", "alumna_cedula": None}
        self.assertEqual(facturas._identificacion(borrador, perfiles),
                         {"alumna": "Nuevo Nombre", "cedula_txt": "1.234.567-2", "identificacion_fija": False})
        self.assertEqual(facturas._identificacion(confirmada, perfiles),
                         {"alumna": "Viejo Nombre", "cedula_txt": "", "identificacion_fija": True})


class Cedula(Base):
    def test_validacion(self):
        from app import validaciones as v
        self.assertEqual(v.cedula("1.234.567-2"), ("12345672", None))
        self.assertEqual(v.cedula(" 1234567 2 ")[1], None)
        self.assertEqual(v.cedula(""), ("", None))
        for mala in ("12345671", "123456", "123456789", "1234567a", "1.234.567-9"):
            self.assertIsNotNone(v.cedula(mala)[1], mala)
        self.assertTrue(v.cedula_valida("9876543"[:-1] + str((10 - sum(int(d) * p for d, p in zip("0987654", (2, 9, 8, 7, 6, 3, 4))) % 10) % 10)))
        self.assertEqual(v.formatear_cedula("12345672"), "1.234.567-2")
        self.assertEqual(v.formatear_cedula(None), "")

    ALTA = {"nombre": "Ana", "apellido": "Uno", "mail": "ana@prueba.invalid", "cedula": "1.234.567-2"}

    def test_alta_con_cedula(self):
        self.como(PROFESORA)
        with mock.patch.object(main, "_cedula_en_uso", return_value=False), \
                mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
            r = self.post("/profesora/alumnas", self.ALTA)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cc.call_args.kwargs["extras"]["cedula"], "12345672")

    def test_alta_cedula_invalida_o_repetida(self):
        self.como(PROFESORA)
        with mock.patch.object(main, "_cedula_en_uso", return_value=False), mock.patch.object(main.auth, "crear_cuenta") as cc:
            r = self.post("/profesora/alumnas", {**self.ALTA, "cedula": "1.234.567-9"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("dígito verificador", r.text)
        cc.assert_not_called()
        with mock.patch.object(main, "_cedula_en_uso", return_value=True), mock.patch.object(main.auth, "crear_cuenta") as cc:
            r = self.post("/profesora/alumnas", self.ALTA)
        self.assertIn("Ya existe una alumna con esa cédula", r.text)
        cc.assert_not_called()

    def test_alta_sin_cedula_sigue_funcionando(self):
        self.como(PROFESORA)
        with mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc, \
                mock.patch.object(main, "_cedula_en_uso") as uso:
            r = self.post("/profesora/alumnas", {**self.ALTA, "cedula": ""})
        self.assertEqual(r.status_code, 200)
        uso.assert_not_called()
        self.assertEqual(cc.call_args.kwargs["extras"]["cedula"], "")

    def test_cargar_y_quitar_cedula(self):
        self.como(PROFESORA)
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.return_value.data = "ok"
        uid = "a0000000-0000-4000-8000-000000000001"
        with mock.patch.object(main, "_sb", return_value=sb):
            r = self.post(f"/profesora/alumnas/{uid}/cedula", {"cedula": "1.234.567-2"})
            self.post(f"/profesora/alumnas/{uid}/cedula", {"cedula": ""})
            sb.rpc.return_value.execute.return_value.data = "cedula_repetida"
            r2 = self.post(f"/profesora/alumnas/{uid}/cedula", {"cedula": "1.234.567-2"})
            r3 = self.post(f"/profesora/alumnas/{uid}/cedula", {"cedula": "123"})
        self.assertIn("ok=1", r.headers["location"])
        self.assertEqual(sb.rpc.call_args_list[:2], [
            mock.call("actualizar_cedula", {"p_id_alumna": uid, "p_cedula": "12345672"}),
            mock.call("actualizar_cedula", {"p_id_alumna": uid, "p_cedula": None})])
        self.assertIn("ok=0", r2.headers["location"])
        self.assertIn("ok=0", r3.headers["location"])
        self.assertEqual(sb.rpc.call_count, 3)

    def test_alumna_no_puede_cargar_cedula(self):
        self.como(ALUMNA)
        sb = mock.MagicMock()
        with mock.patch.object(main, "_sb", return_value=sb):
            r = self.post("/profesora/alumnas/a0000000-0000-4000-8000-000000000001/cedula", {"cedula": "1.234.567-2"})
        self.assertTrue(r.headers["location"].startswith("/?"))
        sb.rpc.assert_not_called()


class Logica(unittest.TestCase):
    def test_fechas(self):
        self.assertFalse(facturas.en_revision(6, 2026, date(2026, 6, 29)))
        self.assertTrue(facturas.en_revision(6, 2026, date(2026, 6, 30)))
        self.assertTrue(facturas.en_revision(2, 2028, date(2028, 2, 29)))
        self.assertEqual(facturas.ultimo_dia(12, 2025), date(2025, 12, 31))
        self.assertEqual(facturas.inicio_publicacion(12, 2025), datetime(2026, 1, 1, tzinfo=TZ))

    def test_parsear_importe(self):
        self.assertEqual(facturas.parsear_importe("600,5"), D("600.5"))
        self.assertEqual(facturas.parsear_importe("0"), D("0"))
        for malo in ("", "abc", "-1", "1.234", "NaN", "inf"):
            self.assertIsNone(facturas.parsear_importe(malo))

    def test_provisorio_misma_regla_que_la_base(self):
        def fila(alumna, f, h, precio, estado="confirmada", estado_clase="programada"):
            return {"id_alumna": alumna, "estado": estado, "alumna": alumna,
                    "clase": {"fecha": f, "hora_inicio": h, "precio": precio, "estado": estado_clase, "tipo_nombre": "Gimnasia"}}
        filas = [fila("ana", "2026-06-05", "08:00:00", 800), fila("ana", "2026-06-30", "19:30:00", 900),
                 fila("ana", "2026-06-30", "20:00:00", 950), fila("ana", "2026-06-06", "08:00:00", 800, estado="cancelada"),
                 fila("ana", "2026-06-07", "08:00:00", 800, estado_clase="cancelada")]
        a = facturas.armar_provisorio(filas, AHORA)[0]
        self.assertEqual((a["dictadas"], a["total_dictadas"], a["proximas"], a["total_proximas"]), (2, D("1700.00"), 1, D("950.00")))

    def test_historial_legible(self):
        h = {"accion": "ajuste_linea", "detalle": {"fecha": "2026-06-29", "hora": "08:00:00", "antes": 800, "despues": 600}}
        self.assertEqual(facturas._historial_txt(h), "Lunes 29/6 08:00: $800.00 → $600.00")
        h = {"accion": "descuento_general", "detalle": {"antes": {"tipo": None, "valor": None}, "despues": {"tipo": "porcentaje", "valor": 10}}}
        self.assertEqual(facturas._historial_txt(h), "sin descuento → 10%")

    def test_mensajes_de_la_base(self):
        for codigo in ("version_cambio", "periodo_abierto", "desactualizada", "cambio", "monto_distinto", "no_confirmada"):
            self.assertNotIn("Probá de nuevo", facturas.mensaje(codigo))


if __name__ == "__main__":
    unittest.main()
