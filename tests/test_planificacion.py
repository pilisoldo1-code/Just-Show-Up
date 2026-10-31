"""Planificación: cancelar clases puntuales (checkboxes) y menú de la profesora.
Rutas y lógica pura, con app.clases mockeado. Las reglas de la base (atomicidad, permisos,
concurrencia) se prueban aparte en un Postgres local (tests/base_local).

Uso: python -m unittest tests.test_planificacion -v
"""
import unittest
from datetime import date, datetime
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, clases, main
from app.auth import Usuario
from app.clases import TZ

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")
HOY = date(2026, 6, 15)
AHORA = datetime(2026, 6, 15, 10, 0, tzinfo=TZ)


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        for objeto, nombre, valor in ((main, "_sb", "SB"), (main.clases, "ahora", AHORA)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        for nombre, valor in (("bloqueos", {"vigentes": [], "finalizados": []}), ("horarios_para_elegir", [])):
            p = mock.patch.object(clases, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def post(self, url, datos):
        return self.client.post(url, data=datos, follow_redirects=False)


class Acceso(Base):
    def test_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.get("/profesora/planificacion?modo=puntual", follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/login"))
        r = self.post("/profesora/planificacion/puntual/cancelar", {"fecha": "2026-06-20", "motivo": "x"})
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_alumna_no_puede_cancelar(self):
        self.como(ALUMNA)
        with mock.patch.object(clases, "cancelar_clases_puntuales") as c:
            r = self.post("/profesora/planificacion/puntual/cancelar",
                          {"fecha": "2026-06-20", "ids_clase": ["1"], "motivo": "x"})
        self.assertTrue(r.headers["location"].startswith("/?"))
        c.assert_not_called()

    def test_alumna_no_ve_la_pantalla(self):
        self.como(ALUMNA)
        r = self.client.get("/profesora/planificacion?modo=puntual", follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/?"))


class PantallaPuntual(Base):
    def test_sin_fecha_no_busca_clases(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "clases_para_cancelar") as cp, mock.patch.object(clases, "generar_clases") as gc:
            r = self.client.get("/profesora/planificacion?modo=puntual")
        self.assertEqual(r.status_code, 200)
        cp.assert_not_called()
        gc.assert_not_called()
        self.assertIn("Cancelar clases puntuales", r.text)

    def test_con_fecha_futura_genera_y_lista_las_clases_de_ese_dia(self):
        self.como(PROFESORA)
        dia = [{"id": 1, "hora": "08:00", "tipo": "Gimnasia", "cancelada": False, "empezada": False, "seleccionable": True}]
        with mock.patch.object(clases, "clases_para_cancelar", return_value=dia) as cp, \
             mock.patch.object(clases, "generar_clases") as gc:
            r = self.client.get("/profesora/planificacion?modo=puntual&fecha=2026-06-22")
        self.assertEqual(r.status_code, 200)
        gc.assert_called_once()
        cp.assert_called_once_with("SB", date(2026, 6, 22), None)  # administradora: todas las clases
        self.assertIn("08:00", r.text)
        self.assertIn("Gimnasia", r.text)

    def test_fecha_pasada_se_ignora(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "clases_para_cancelar") as cp:
            r = self.client.get("/profesora/planificacion?modo=puntual&fecha=2020-01-01")
        self.assertEqual(r.status_code, 200)
        cp.assert_not_called()

    def test_fecha_invalida_no_rompe(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/planificacion?modo=puntual&fecha=no-es-una-fecha")
        self.assertEqual(r.status_code, 200)

    def test_dia_sin_clases_muestra_mensaje(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "clases_para_cancelar", return_value=[]), \
             mock.patch.object(clases, "generar_clases"):
            r = self.client.get("/profesora/planificacion?modo=puntual&fecha=2026-06-22")
        self.assertIn("No hay clases programadas ese día", r.text)

    def test_modo_desconocido_cae_a_licencia(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/planificacion?modo=otracosa")
        self.assertIn("Nuevo bloqueo", r.text)


class CancelarPuntual(Base):
    def test_ok_redirige_conservando_fecha_y_modo(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "cancelar_clases_puntuales", return_value=(True, "Se cancelaron 2 clases.")) as c:
            r = self.post("/profesora/planificacion/puntual/cancelar",
                          {"fecha": "2026-06-22", "ids_clase": ["5", "6"], "motivo": "Consulta médica"})
        self.assertEqual(r.status_code, 303)
        c.assert_called_once_with("SB", [5, 6], "Consulta médica")
        self.assertIn("modo=puntual", r.headers["location"])
        self.assertIn("fecha=2026-06-22", r.headers["location"])
        self.assertIn("ok=1", r.headers["location"])

    def test_error_de_negocio_no_rompe(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "cancelar_clases_puntuales", return_value=(False, "Elegí al menos una clase.")):
            r = self.post("/profesora/planificacion/puntual/cancelar", {"fecha": "2026-06-22", "motivo": "x"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("ok=0", r.headers["location"])

    def test_excepcion_no_rompe_y_avisa(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "cancelar_clases_puntuales", side_effect=RuntimeError("boom")):
            r = self.post("/profesora/planificacion/puntual/cancelar",
                          {"fecha": "2026-06-22", "ids_clase": ["1"], "motivo": "x"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("ok=0", r.headers["location"])


class EliminarBloqueo(Base):
    def test_conserva_modo_puntual_y_fecha(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "eliminar_bloqueo", return_value=(True, "Bloqueo eliminado.")):
            r = self.post("/profesora/planificacion/9/eliminar", {"modo": "puntual", "fecha": "2026-06-22"})
        self.assertIn("modo=puntual", r.headers["location"])
        self.assertIn("fecha=2026-06-22", r.headers["location"])

    def test_licencia_no_agrega_modo_ni_fecha(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "eliminar_bloqueo", return_value=(True, "Bloqueo eliminado.")):
            r = self.post("/profesora/planificacion/9/eliminar", {})
        self.assertNotIn("modo=", r.headers["location"])
        self.assertNotIn("fecha=", r.headers["location"])


class ClasesParaCancelar(unittest.TestCase):
    """clases.clases_para_cancelar: lógica pura de transformación, con un cliente falso."""

    def _sb(self, filas):
        sb = mock.MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value \
          .execute.return_value.data = filas
        return sb

    def test_marca_seleccionable_cancelada_y_empezada(self):
        filas = [
            {"id": 1, "hora_inicio": "18:00:00", "duracion": 60, "estado": "programada",
             "horario": {"activo": True}, "tipo_clase": {"nombre": "Gimnasia"}},
            {"id": 2, "hora_inicio": "09:00:00", "duracion": 60, "estado": "cancelada",
             "horario": {"activo": True}, "tipo_clase": {"nombre": "Gimnasia"}},
            {"id": 3, "hora_inicio": "07:00:00", "duracion": 60, "estado": "programada",
             "horario": {"activo": True}, "tipo_clase": None},
            {"id": 4, "hora_inicio": "19:00:00", "duracion": 60, "estado": "programada",
             "horario": {"activo": False}, "tipo_clase": {"nombre": "Gimnasia"}},
        ]
        with mock.patch.object(clases, "ahora", return_value=AHORA):
            r = clases.clases_para_cancelar(self._sb(filas), HOY)
        por_id = {c["id"]: c for c in r}
        self.assertEqual(set(por_id), {1, 2, 3})  # la 4 se filtra: horario inactivo
        self.assertTrue(por_id[1]["seleccionable"])
        self.assertFalse(por_id[1]["cancelada"])
        self.assertFalse(por_id[1]["empezada"])
        self.assertTrue(por_id[2]["cancelada"])
        self.assertFalse(por_id[2]["seleccionable"])
        self.assertTrue(por_id[3]["empezada"])  # 07:00 ya pasó (ahora = 10:00)
        self.assertFalse(por_id[3]["seleccionable"])
        self.assertEqual(por_id[3]["tipo"], "")


class CancelarClasesPuntualesMensajes(unittest.TestCase):
    """clases.cancelar_clases_puntuales: traducción de resultados de la base a mensajes."""

    def _sb(self, data):
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.return_value.data = data
        return sb

    def test_ok_sin_reservas_ni_espera(self):
        ok, texto = clases.cancelar_clases_puntuales(self._sb({"resultado": "ok", "clases": 1, "reservas": 0, "espera": 0}), [1], "x")
        self.assertTrue(ok)
        self.assertEqual(texto, "Se canceló 1 clase.")

    def test_ok_plural_con_reservas_y_espera(self):
        ok, texto = clases.cancelar_clases_puntuales(
            self._sb({"resultado": "ok", "clases": 2, "reservas": 3, "espera": 1}), [1, 2], "x")
        self.assertTrue(ok)
        self.assertIn("Se cancelaron 2 clases", texto)
        self.assertIn("3 reservas", texto)
        self.assertIn("1 alumna", texto)

    def test_mensajes_de_error(self):
        casos = {
            "no_autorizada": "Solo la profesora puede cancelar clases.",
            "sin_clases": "Elegí al menos una clase.",
            "motivo_vacio": "Escribí el motivo de la cancelación.",
        }
        for resultado, esperado in casos.items():
            ok, texto = clases.cancelar_clases_puntuales(self._sb({"resultado": resultado}), [1], "x")
            self.assertFalse(ok)
            self.assertEqual(texto, esperado)

    def test_clases_invalidas_explica_todo_o_nada(self):
        ok, texto = clases.cancelar_clases_puntuales(self._sb({"resultado": "clases_invalidas"}), [1, 2], "x")
        self.assertFalse(ok)
        self.assertIn("no se canceló ninguna", texto)

    def test_motivo_se_recorta(self):
        sb = self._sb({"resultado": "ok", "clases": 1, "reservas": 0, "espera": 0})
        clases.cancelar_clases_puntuales(sb, [1], "  con espacios  ")
        sb.rpc.assert_called_once_with("crear_bloqueos_puntuales", {"p_ids_clase": [1], "p_motivo": "con espacios"})

    def test_motivo_demasiado_largo_no_llega_a_la_base(self):
        sb = self._sb({"resultado": "ok", "clases": 1, "reservas": 0, "espera": 0})
        ok, texto = clases.cancelar_clases_puntuales(sb, [1], "x" * 201)
        self.assertFalse(ok)
        self.assertEqual(texto, "El motivo puede tener como máximo 200 caracteres.")
        sb.rpc.assert_not_called()


class MenuProfesora(Base):
    def test_orden_y_etiquetas(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/planificacion")
        texto = r.text
        self.assertNotIn(">Clases</a>", texto)
        self.assertIn(">Asistencias</a>", texto)
        self.assertIn(">Tipos de clase</a>", texto)
        orden = ["/profesora/horarios", "/profesora/tipos-clase", "/profesora/clases",
                 "/profesora/planificacion", "/profesora/alumnas", "/profesora/resumenes", "/profesora/estadisticas"]
        posiciones = [texto.index(f'href="{h}"') for h in orden]
        self.assertEqual(posiciones, sorted(posiciones))

    def test_resalta_la_pestana_activa(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/planificacion")
        self.assertIn('href="/profesora/planificacion" class="activo"', r.text)
        self.assertNotIn('href="/profesora/horarios" class="activo"', r.text)
        self.assertNotIn('href="/profesora/clases" class="activo"', r.text)

    def test_resalta_asistencias_tambien_en_el_detalle_de_una_clase(self):
        self.como(PROFESORA)
        with mock.patch.object(clases, "clases_del_dia", return_value=[]), \
             mock.patch.object(clases, "dias_con_clases", return_value=[HOY]), \
             mock.patch.object(clases, "generar_clases"):
            r = self.client.get("/profesora/clases")
        self.assertIn('href="/profesora/clases" class="activo"', r.text)


if __name__ == "__main__":
    unittest.main()
