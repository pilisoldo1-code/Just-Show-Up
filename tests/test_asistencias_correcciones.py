"""Asistencias: pendientes dentro de la misma pantalla (filtro por mes, «Todas» por defecto), correcciones de la
administradora con motivo e historial, y los avisos de Inicio y de la confirmación de facturas. La base se simula;
las reglas de la base se prueban en tests/base_local/test_asistencias_correcciones_base.py.

Uso: python -m unittest tests.test_asistencias_correcciones -v
"""
import re
import unittest
from datetime import date
from unittest import mock

from app import clases, main
from tests.test_multiempresa_app import ADMIN, PROFE, AHORA, Base

TERMINADAS = [  # como las devuelve la base (resumen_asistencias_mes), en cualquier orden
    {"id_clase": 1, "fecha": "2026-11-02", "hora_inicio": "08:00:00", "tipo": "Gimnasia", "id_profesora": "pr", "reservas": 4,
     "sin_registrar": 0},
    {"id_clase": 2, "fecha": "2026-11-01", "hora_inicio": "09:00:00", "tipo": "Gimnasia", "id_profesora": "pr", "reservas": 5,
     "sin_registrar": 3},
    {"id_clase": 3, "fecha": "2026-11-02", "hora_inicio": "07:00:00", "tipo": "Gimnasia", "id_profesora": "pr", "reservas": 2,
     "sin_registrar": 1},
    {"id_clase": 4, "fecha": "2026-11-02", "hora_inicio": "06:00:00", "tipo": "Gimnasia", "id_profesora": "pr", "reservas": 0,
     "sin_registrar": 0},
]


class Logica(unittest.TestCase):
    def test_pendientes_primero_y_de_la_mas_reciente_a_la_mas_antigua(self):
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.return_value.data = TERMINADAS
        with mock.patch.object(clases.personas, "nombres_profesoras", return_value={"pr": "Paula Ruiz"}):
            items = clases.clases_terminadas(sb, 2026, 11)
        self.assertEqual([c["id"] for c in items], [3, 2, 1])  # sin la clase sin reservas
        self.assertEqual(items[0]["profesora"], "Paula Ruiz")

    def test_meses_del_filtro(self):
        self.assertEqual(clases.meses_asistencias(date(2026, 2, 10))[:3], [(2026, 2), (2026, 1), (2025, 12)])
        self.assertEqual(len(clases.meses_asistencias(date(2026, 2, 10))), 6)

    def test_mensajes_de_la_base(self):
        sb = mock.MagicMock()
        for codigo, texto in (("factura_confirmada", "ya está confirmada"), ("no_termino", "todavía no terminó"),
                              ("motivo_vacio", "motivo")):
            sb.rpc.return_value.execute.return_value.data = {"resultado": codigo}
            ok, aviso = clases.corregir_asistencias(sb, [{"id_reserva": 1, "estado": "presente"}], "x")
            self.assertFalse(ok)
            self.assertIn(texto, aviso)
        sb.rpc.return_value.execute.return_value.data = {"resultado": "ok", "cambios": 2}
        self.assertEqual(clases.corregir_asistencias(sb, [], "x"), (True, "Se guardaron 2 asistencias. El cambio quedó en el historial."))


class Pantalla(Base):
    def preparar(self):
        for nombre, valor in (("generar_clases", None), ("dias_con_clases", []), ("clases_del_dia", [])):
            p = mock.patch.object(clases, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(
            **{"execute.return_value.data": TERMINADAS if nombre == "resumen_asistencias_mes" else []})

    def test_todas_por_defecto_y_pendientes_primero(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get("/profesora/clases")
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.text, r'filtro=todas#terminadas" class="dia-chip dia-chip-activo"')
        self.assertIn("Pendientes (2)</a>", r.text)
        self.assertIn("2 clases terminadas con 4\n            asistencias sin registrar en noviembre 2026", r.text)
        self.assertLess(r.text.index("Próximos días"), r.text.index('id="terminadas"'))  # hoy y próximos días, arriba
        filas = re.findall(r'href="/profesora/clases/(\d+)"', r.text.split('id="terminadas"')[1])
        self.assertEqual(filas, ["3", "2", "1"])
        self.assertIn(">1 de 2 sin registrar<", r.text)
        self.assertIn(">Completa<", r.text)
        self.assertEqual(r.text.count(">Completar</a>"), 2)  # la administradora completa; la completa se ve

    def test_filtro_pendientes_y_otro_mes(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get("/profesora/clases?mes=2026-10&filtro=pendientes")
        self.assertIn('<option value="2026-10" selected>', r.text)
        self.assertNotIn("Próximos días", r.text)  # en un mes anterior no hay próximos días
        self.assertEqual(re.findall(r'href="/profesora/clases/(\d+)"', r.text), ["3", "2"])
        self.sb.rpc.assert_any_call("resumen_asistencias_mes", {"p_anio": 2026, "p_mes": 10})

    def test_mes_fuera_del_filtro_vuelve_al_actual(self):
        self.como(ADMIN)
        self.preparar()
        self.get("/profesora/clases?mes=2020-01")
        self.sb.rpc.assert_any_call("resumen_asistencias_mes", {"p_anio": AHORA.year, "p_mes": AHORA.month})

    def test_profesora_solo_consulta(self):
        self.como(PROFE)
        self.preparar()
        r = self.get("/profesora/clases")
        self.assertIn("solo la administración puede completar o corregir", r.text)
        self.assertNotIn(">Completar</a>", r.text)
        self.assertIn(">Ver</a>", r.text)


class Detalle(Base):
    CLASE = {"id": 7, "id_profesora": "pr", "fecha": "2026-10-30", "hora_inicio": "08:00:00", "duracion": 60,
             "estado": "programada", "tipo_clase": {"nombre": "Gimnasia"}}
    RESERVAS = [{"id": 11, "profiles": {"nombre": "Ana", "apellido": "Uno"}, "asistencia": None},
                {"id": 12, "profiles": {"nombre": "Eva", "apellido": "Dos"}, "asistencia": [{"estado": "ausente"}]}]

    def preparar(self, bloqueadas=(), historial=()):
        tabla = self.sb.table.return_value.select.return_value
        tabla.eq.return_value.execute.return_value.data = [self.CLASE]
        tabla.eq.return_value.eq.return_value.execute.return_value.data = self.RESERVAS
        tabla.eq.return_value.in_.return_value.order.return_value.execute.return_value.data = []
        datos = {"reservas_con_factura_confirmada": list(bloqueadas), "historial_asistencias_clase": list(historial),
                 "corregir_asistencias": {"resultado": "ok", "cambios": 1}}
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(**{"execute.return_value.data": datos.get(nombre)})

    def test_administradora_completa_una_clase_terminada(self):
        self.como(ADMIN)
        self.preparar(bloqueadas=[12], historial=[{"creado_en": "2026-10-30T12:00:00+00:00", "alumna": "Eva Dos",
                                                    "estado_anterior": None, "estado_nuevo": "ausente", "origen": "clase",
                                                    "motivo": None, "por": "Paula Ruiz"}])
        r = self.get("/profesora/clases/7")
        self.assertIn('action="/profesora/clases/7/corregir"', r.text)
        self.assertIn('name="motivo"', r.text)
        self.assertIn("1 sin registrar", r.text)
        self.assertRegex(r.text, r'name="estado_12" value="presente"[^>]*\n?[^>]*disabled')
        self.assertIn("La factura de este mes ya está confirmada", r.text)
        self.assertIn("Historial", r.text)
        self.assertIn("30/10 09:00 · Paula Ruiz", r.text)
        self.assertIn("(durante la clase)", r.text)

    def test_profesora_ve_sin_poder_cambiar(self):
        self.como(PROFE)
        self.preparar()
        r = self.get("/profesora/clases/7")
        self.assertNotIn("/corregir", r.text)
        self.assertNotIn('name="motivo"', r.text)
        self.assertIn("solo la administración puede", r.text)
        self.assertNotIn("Historial", r.text)
        for nombre in ("reservas_con_factura_confirmada", "historial_asistencias_clase"):
            self.assertNotIn(nombre, [c.args[0] for c in self.sb.rpc.call_args_list])

    def test_guardar_correcciones(self):
        self.como(ADMIN)
        self.preparar()
        r = self.post("/profesora/clases/7/corregir", {"estado_11": "presente", "estado_12": "presente", "motivo": "Planilla"})
        self.sb.rpc.assert_any_call("corregir_asistencias", {"p_cambios": [{"id_reserva": 11, "estado": "presente"},
                                                                           {"id_reserva": 12, "estado": "presente"}],
                                                             "p_motivo": "Planilla"})
        self.assertIn("ok=1", r.headers["location"])

    def test_la_profesora_no_puede_guardar_correcciones(self):
        self.como(PROFE)
        self.preparar()
        r = self.post("/profesora/clases/7/corregir", {"estado_11": "presente", "motivo": "x"})
        self.assertEqual(r.status_code, 303)
        self.assertNotIn("corregir_asistencias", [c.args[0] for c in self.sb.rpc.call_args_list])


class Avisos(Base):
    def test_inicio_avisa_si_hay_pendientes(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_resumen_admin", return_value={"alumnas": 1, "profesoras": 0, "clases_semanales": 2}), \
                mock.patch.object(clases, "pendientes_totales", return_value={"clases": 3, "asistencias": 7,
                                                                              "ultima_fecha": date(2026, 10, 29)}):
            r = self.get("/administracion")
        self.assertIn("3 clases terminadas tienen", r.text)
        self.assertIn('href="/profesora/clases?mes=2026-10&filtro=pendientes#terminadas"', r.text)
        self.assertEqual(r.text.count('class="panel-numero"'), 3)  # siguen siendo tres indicadores

    def test_inicio_sin_pendientes_no_muestra_nada(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_resumen_admin", return_value={"alumnas": 1, "profesoras": 0, "clases_semanales": 2}), \
                mock.patch.object(clases, "pendientes_totales", return_value={"clases": 0, "asistencias": 0, "ultima_fecha": None}):
            r = self.get("/administracion")
        self.assertNotIn("asistencias sin registrar", r.text)


if __name__ == "__main__":
    unittest.main()
