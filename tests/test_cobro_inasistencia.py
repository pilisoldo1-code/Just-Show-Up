"""Cobro del 25 % por inasistencia: cálculo en Python (igual al de la base), líneas y totales de las facturas, avisos de
asistencias sin registrar antes de confirmar, Mis facturas, PDF, Mi historial y la fecha de inicio por empresa (solo la
superadministradora). La base se simula; sus reglas se prueban en tests/base_local/test_cobro_inasistencia_base.py.

Uso: python -m unittest tests.test_cobro_inasistencia -v
"""
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from unittest import mock

from app import clases, facturas, main
from app.auth import Usuario
from app.clases import TZ
from tests.test_facturas import ALUMNA, PROFESORA, Base, _texto_pdf, factura

SUPER = Usuario(id="s", mail="super@prueba.invalid", nombre="Super", apellido="Admin", rol="alumna", token="t",
                es_superadmin=True, con_membresias=True)


def fila(id_, dia, precio, pct=100, asistencia=None, ajuste="ninguno", importe=None):
    base = facturas.importe_base(precio, pct)
    return {"id": id_, "fecha": f"2026-11-{dia:02d}", "hora_inicio": "08:00:00", "tipo_nombre": "Gimnasia",
            "precio_original": precio, "porcentaje": pct, "asistencia": asistencia, "ajuste_tipo": ajuste,
            "importe_final": base if importe is None else importe}


FILAS = [fila(1, 3, 800, 25, "ausente"), fila(2, 4, 800, 100, "presente"), fila(3, 5, 800),
         fila(4, 6, 850, 25, "ausente", "importe", D("100")), fila(5, 7, 800, 100, "ausente")]  # la 5: antes de la fecha


def con_lineas(**cambios):
    f = factura(mes=11, anio=2026, mes_txt="Noviembre 2026", subtotal_original=D("4050.00"), total_lineas=D("2700.00"),
                descuento_tipo=None, descuento_monto=D("0.00"), total=D("2700.00"), descuento_txt="", **cambios)
    return facturas._con_lineas(f, [{**facturas._linea(l), "motivo": ""} for l in FILAS])


class Calculo(unittest.TestCase):
    def test_importe_base_redondeo_a_centesimos(self):
        self.assertEqual([facturas.importe_base(p, 25) for p in (800, 850, 900, 0, D("10.10"))],
                         [D("200.00"), D("212.50"), D("225.00"), D("0.00"), D("2.53")])
        self.assertEqual(facturas.importe_base(850, 100), D("850.00"))

    def test_porcentaje_solo_ausente_desde_la_fecha(self):
        desde = date(2026, 11, 1)
        self.assertEqual(facturas.porcentaje_cobro("2026-11-01", "ausente", desde), 25)
        self.assertEqual(facturas.porcentaje_cobro("2026-10-31", "ausente", desde), 100)  # nunca hacia atrás
        self.assertEqual(facturas.porcentaje_cobro("2026-11-05", "presente", desde), 100)
        self.assertEqual(facturas.porcentaje_cobro("2026-11-05", None, desde), 100)  # sin registrar: completa
        self.assertEqual(facturas.porcentaje_cobro("2026-11-05", "ausente", None), 100)  # empresa sin fecha

    def test_lineas_y_conteos(self):
        f = con_lineas()
        l = {x["id"]: x for x in f["lineas"]}
        self.assertEqual((l[1]["inasistencia"], l[1]["importe_base"], l[1]["ajuste"]), (True, D("200.00"), D("0.00")))
        self.assertEqual((l[4]["importe_base"], l[4]["ajuste"], l[4]["ajuste_txt"]), (D("212.50"), D("-112.50"), "Descuento"))
        self.assertEqual((l[3]["asistencia_txt"], l[5]["inasistencia"]), ("Sin registrar", False))
        self.assertEqual((f["inasistencias"], f["presentes"], f["sin_registrar"], f["descuento_inasistencias"]),
                         (2, 1, 1, D("1237.50")))

    def test_lineas_viejas_sin_columnas_nuevas(self):
        l = facturas._linea({k: v for k, v in fila(9, 3, 800).items() if k not in ("porcentaje", "asistencia")})
        self.assertEqual((l["porcentaje"], l["inasistencia"], l["importe_base"]), (100, False, D("800.00")))

    def test_provisorio_con_inasistencia(self):
        ahora = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)
        clase = lambda dia: {"fecha": f"2026-11-{dia:02d}", "hora_inicio": "08:00:00", "precio": 800, "estado": "programada"}
        filas = [{"id_alumna": "x", "alumna": "Ana", "clase": clase(3), "asistencia": "ausente", "cobro_desde": date(2026, 11, 1)},
                 {"id_alumna": "x", "alumna": "Ana", "clase": clase(4), "asistencia": None, "cobro_desde": date(2026, 11, 1)},
                 {"id_alumna": "x", "alumna": "Ana", "clase": clase(25), "asistencia": None, "cobro_desde": date(2026, 11, 1)}]
        a = facturas.armar_provisorio(filas, ahora)[0]
        self.assertEqual((a["dictadas"], a["total_dictadas"], a["inasistencias"], a["total_proximas"]),
                         (2, D("1000.00"), 1, D("800.00")))

    def test_fecha_de_la_empresa_tolera_base_sin_migrar(self):
        sb = mock.MagicMock()
        sb.table.return_value.select.return_value.in_.return_value.execute.side_effect = RuntimeError("columna")
        self.assertEqual(facturas.cobro_inasistencia_desde(sb, [1]), {})
        sb.table.return_value.select.return_value.in_.return_value.execute.side_effect = None
        sb.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"id": 1, "cobro_inasistencia_desde": "2026-11-01"}]
        self.assertEqual(facturas.cobro_inasistencia_desde(sb, [1]), {1: date(2026, 11, 1)})

    def test_historial_del_cambio_de_asistencia(self):
        t = facturas._historial_txt({"accion": "cambio_asistencia", "detalle": {
            "fecha": "2026-11-03", "hora": "08:00:00", "asistencia_antes": None, "asistencia_despues": "ausente",
            "porcentaje_antes": 100, "porcentaje_despues": 25, "antes": 800, "despues": 200}})
        self.assertEqual(t, "Martes 3/11 08:00: Sin registrar → Ausente · 100 % → 25 % · $800,00 → $200,00")


class Pantallas(Base):
    def ver_admin(self, f):
        self.como(PROFESORA)
        with mock.patch.object(facturas, "factura", return_value=f), \
                mock.patch.object(facturas, "previsualizar_mes", return_value={"periodo_finalizado": True}):
            return " ".join(self.client.get("/profesora/resumenes/1").text.split())

    def test_detalle_de_la_administradora(self):
        t = self.ver_admin(con_lineas())
        for texto in ("Inasistencia · 25 %", '<s class="precio-tachado"', '$800,00</s><span class="precio-base">$200,00</span>',
                      "1 presente · 2 inasistencias (25 %) · 1 sin registrar",
                      "Incluye 2 inasistencias al 25 % (−$1.237,50 sobre el precio)",
                      "1 sin registrar: se cobra al 100 %", "/profesora/clases?mes=2026-11&filtro=pendientes#terminadas",
                      "Importe nuevo (menor que $200,00)", "vuelve a $212,50", "Sin registrar", "Presente"):
            self.assertIn(texto, t)

    def test_confirmada_sin_aviso_de_pendientes(self):
        t = self.ver_admin(con_lineas(estado_factura="confirmada", numero="JSU-2026-11-0001", estado_factura_txt="Confirmada"))
        self.assertIn("Inasistencia · 25 %", t)
        self.assertNotIn("Completar asistencias", t)

    def test_alumna_ve_inasistencia_sin_datos_internos(self):
        self.como(ALUMNA)
        f = con_lineas(estado_factura="confirmada", numero="JSU-2026-11-0001")
        with mock.patch.object(facturas, "mis_facturas", return_value=[f]):
            t = self.client.get("/mis-facturas").text
        for texto in ("Inasistencia (25 %)", '$800,00</s><span class="precio-base">$200,00</span>',
                      "Inasistencia (25 %): clase reservada que no se canceló a tiempo y a la que no asististe"):
            self.assertIn(texto, t)
        for interno in ("Sin registrar", "Incluye 2 inasistencias", "Completar asistencias", "Inasistencia · 25 %"):
            self.assertNotIn(interno, t)

    def test_alumna_sin_inasistencias_no_ve_la_nota(self):
        self.como(ALUMNA)
        f = factura(estado_factura="confirmada", numero="JSU-2026-06-0001")
        with mock.patch.object(facturas, "mis_facturas", return_value=[facturas._con_lineas(f, [facturas._linea(fila(2, 4, 800, 100, "presente"))])]):
            t = self.client.get("/mis-facturas").text
        self.assertNotIn("Inasistencia", t)

    def test_pdf_marca_la_inasistencia_y_la_explica(self):
        self.como(ALUMNA)
        f = con_lineas(estado_factura="confirmada", numero="JSU-2026-11-0001")
        with mock.patch.object(facturas, "mi_factura", return_value=f):
            t = _texto_pdf(self.client.get("/mis-facturas/1/pdf").content)
        for texto in ("Gimnasia *", "Inasist. 25 %", "Inasistencias \\(2\\) al 25 %", "-$ 1.237,50",
                      "* Inasistencia \\(25 %\\)", "Ajustes por clase", "-$ 112,50"):
            self.assertIn(texto, t)

    def test_pdf_sin_inasistencias_igual_que_antes(self):
        self.como(ALUMNA)
        with mock.patch.object(facturas, "mi_factura", return_value=factura(estado_factura="confirmada", numero="X")):
            t = _texto_pdf(self.client.get("/mis-facturas/1/pdf").content)
        self.assertNotIn("Inasist", t)
        self.assertIn("-$ 200,00", t)

    def _mes(self, pendientes):
        previa = {"resultado": "ok", "periodo_finalizado": True, "cantidad": 1, "total": D("1"), "firma": "f",
                  "desactualizadas": 0, "sin_clases": 0}
        terminadas = [{"sin_registrar": n} for n in pendientes]
        with mock.patch.object(facturas, "sincronizar"), \
                mock.patch.object(facturas, "facturas_del_mes", return_value=[factura()]), \
                mock.patch.object(facturas, "previsualizar_mes", return_value=previa), \
                mock.patch.object(clases, "clases_terminadas", return_value=terminadas):
            return tuple(" ".join(self.client.get(u).text.split()) for u in (
                "/profesora/resumenes?mes=6&anio=2026", "/profesora/resumenes/confirmar-mes?mes=6&anio=2026"))

    def test_aviso_de_pendientes_antes_de_confirmar_sin_bloquear(self):
        self.como(PROFESORA)
        for t in self._mes([3, 0, 2]):
            self.assertIn("<strong>5 asistencias sin registrar</strong>", t)
            self.assertIn("en 2 clases de Junio 2026", t)
            self.assertIn("marcala ausente (25 %). Podés confirmar igual", t)
            self.assertIn('href="/profesora/clases?mes=2026-06&amp;filtro=pendientes#terminadas">Completar asistencias', t)
        mes, previa = self._mes([1])
        self.assertIn("1 asistencia sin registrar</strong>", previa)
        self.assertIn("Confirmar 1 factura", previa)  # no bloquea
        self.assertIn("Confirmar todas las facturas del mes (1)", mes)

    def test_sin_pendientes_no_hay_aviso(self):
        self.como(PROFESORA)
        for t in self._mes([0, 0]):
            self.assertNotIn("sin registrar</strong>", t)

    def test_mi_historial_dice_que_se_factura_el_25(self):
        self.como(ALUMNA)
        item = lambda f, e: {"fecha": f, "dia": "Lunes", "fecha_txt": "x", "hora": "08:00", "duracion": 60, "precio": 800,
                             "estado_txt": e, "badge": "b"}
        hist = [item("2026-11-03", "Ausente"), item("2026-10-20", "Ausente"), item("2026-11-04", "Presente")]
        with mock.patch.object(clases, "mis_inscripciones", return_value={"historial": hist}), \
                mock.patch.object(facturas, "cobro_inasistencia_desde", return_value={None: date(2026, 11, 1)}):
            t = self.client.get("/mi-historial").text
        t = " ".join(t.split())
        self.assertEqual(t.count("Inasistencia: se factura el 25 % ($200,00)"), 1)  # solo la ausencia desde la fecha de inicio
        self.assertIn("Precio de la clase: $800,00", t)
        self.assertIn("Importe estimado: el definitivo figura en Mis facturas.", t)


class FechaPorEmpresa(Base):
    EMPRESA = {"id": 2, "nombre": "Pilates Centro", "codigo": "PIL", "activa": True, "profesoras": 1, "alumnas": 3}

    def pantalla(self, desde):
        self.como(SUPER)
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.return_value.data = [self.EMPRESA]
        with mock.patch.object(main, "_sb", return_value=sb), mock.patch.object(main, "_admins_de_empresa", return_value=[]), \
                mock.patch.object(facturas, "cobro_inasistencia_desde", return_value={2: desde}):
            return self.client.get("/admin/empresas/2").text

    def test_superadministradora_ve_y_puede_cambiar_si_todavia_no_rige(self):
        t = self.pantalla(date(2026, 7, 1))  # AHORA = 30/6/2026
        self.assertIn("Rige desde el <strong>1/7/2026</strong>", t)
        self.assertIn('action="/admin/empresas/2/cobro-inasistencia"', t)
        self.assertIn('min="2026-06-01"', t)
        self.assertIn("No aplicar", t)

    def test_si_ya_rige_desde_un_mes_anterior_no_se_cambia(self):
        t = self.pantalla(date(2026, 5, 1))
        self.assertIn("Ya rige desde un mes anterior", t)
        self.assertNotIn('action="/admin/empresas/2/cobro-inasistencia"', t)

    def test_sin_fecha(self):
        t = self.pantalla(None)
        self.assertIn("No se aplica", t)
        self.assertIn("Aplicar desde", t)

    def test_guardar_y_mensajes_de_la_base(self):
        self.como(SUPER)
        sb = mock.MagicMock()
        with mock.patch.object(main, "_sb", return_value=sb):
            for codigo, texto in (("ok", "actualizado"), ("fecha_retroactiva", "no+puede+ser+anterior"),
                                  ("ya_vigente", "ya+rige")):
                sb.rpc.return_value.execute.return_value.data = codigo
                r = self.post("/admin/empresas/2/cobro-inasistencia", {"desde": "2026-07-01"})
                self.assertIn(texto, r.headers["location"].replace("%20", "+"))
            sb.rpc.assert_any_call("configurar_cobro_inasistencia", {"p_id_empresa": 2, "p_desde": "2026-07-01"})
            self.post("/admin/empresas/2/cobro-inasistencia", {"quitar": "1"})
            sb.rpc.assert_called_with("configurar_cobro_inasistencia", {"p_id_empresa": 2, "p_desde": None})
            r = self.post("/admin/empresas/2/cobro-inasistencia", {"desde": "no-es-fecha"})
            self.assertIn("fecha+v", r.headers["location"].replace("%20", "+"))

    def test_nadie_mas_puede_configurarla(self):
        for usuario in (PROFESORA, ALUMNA):
            self.como(usuario)
            sb = mock.MagicMock()
            with mock.patch.object(main, "_sb", return_value=sb):
                r = self.post("/admin/empresas/2/cobro-inasistencia", {"desde": "2026-07-01"})
            self.assertEqual(r.status_code, 303)
            sb.rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
