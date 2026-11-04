"""Singular/plural de "cupo" en las pantallas de Tipos de clase (ajuste visual, sin tocar
lógica de cupos/reservas). Solo rutas y plantillas, con app.tipos_clase mockeado.

Uso: python -m unittest tests.test_cupos_tipos_clase -v
"""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main, tipos_clase
from app.auth import Usuario

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")


def tipo(**cambios):
    t = {
        "id": 1, "nombre": "Gimnasia", "descripcion": "", "imagen_url": None,
        "duracion": 60, "precio_presencial": 800, "cupo_max": 7, "activo": True,
        "horarios_activos": 1, "horarios": 1,
    }
    t.update(cambios)
    return t


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        p = mock.patch.object(main, "_sb", return_value="SB")
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(auth, "_usuario_cacheado", return_value=PROFESORA)
        p2.start()
        self.addCleanup(p2.stop)


class TarjetasTiposClase(Base):
    def test_cupo_singular(self):
        with mock.patch.object(tipos_clase, "listar", return_value=[tipo(cupo_max=1)]):
            r = self.client.get("/profesora/tipos-clase")
        self.assertIn("1 cupo<", r.text)
        self.assertNotIn("1 cupos", r.text)

    def test_cupo_plural(self):
        with mock.patch.object(tipos_clase, "listar", return_value=[tipo(cupo_max=7)]):
            r = self.client.get("/profesora/tipos-clase")
        self.assertIn("7 cupos<", r.text)

    def test_otro_tipo_con_otra_capacidad(self):
        with mock.patch.object(tipos_clase, "listar", return_value=[tipo(id=2, nombre="Pilates", cupo_max=8)]):
            r = self.client.get("/profesora/tipos-clase")
        self.assertIn("8 cupos<", r.text)

    def test_sigue_mostrando_duracion_y_precio(self):
        with mock.patch.object(tipos_clase, "listar", return_value=[tipo(duracion=45, precio_presencial=950, cupo_max=1)]):
            r = self.client.get("/profesora/tipos-clase")
        self.assertIn("45 min", r.text)
        self.assertIn("$950", r.text)
        self.assertIn("1 cupo<", r.text)

    def test_no_queda_cupo_sin_pluralizar(self):
        with mock.patch.object(tipos_clase, "listar", return_value=[tipo(cupo_max=7)]):
            r = self.client.get("/profesora/tipos-clase")
        self.assertNotIn("cupo 7", r.text)


class InactivosPlegados(Base):
    DEMO = dict(id=81, nombre="Gimnasia (demostración)", activo=False, horarios_activos=0, horarios=1)

    def listado(self, tipos):
        with mock.patch.object(tipos_clase, "listar", return_value=tipos):
            return self.client.get("/profesora/tipos-clase").text

    def test_inactivos_al_final_en_seccion_cerrada(self):
        html = self.listado([tipo(), tipo(id=82, nombre="Yoga", activo=False, horarios_activos=0)])
        corte = html.index('<details class="seccion-inactivos">')
        self.assertIn("<summary>Inactivos (1)</summary>", html[corte:])
        self.assertNotIn('<details class="seccion-inactivos" open', html)  # cerrada por defecto
        self.assertIn("Yoga", html[corte:])
        self.assertIn(">Activar</button>", html[corte:])  # las acciones se conservan
        self.assertNotIn("Yoga", html[:corte])
        self.assertIn('href="/profesora/tipos-clase/1/editar"', html[:corte])

    def test_sin_inactivos_no_hay_seccion(self):
        self.assertNotIn("seccion-inactivos", self.listado([tipo()]))

    def test_el_tipo_de_demostracion_no_aparece_ni_en_inactivos(self):
        html = self.listado([tipo(), tipo(**self.DEMO)])
        self.assertNotIn("demostración", html)
        self.assertNotIn("seccion-inactivos", html)  # sin otros inactivos, no hay sección vacía
        html = self.listado([tipo(), tipo(**self.DEMO), tipo(id=82, nombre="Yoga", activo=False)])
        self.assertIn("<summary>Inactivos (1)</summary>", html)
        self.assertNotIn("demostración", html)

    def test_la_exclusion_es_por_id_y_no_solo_por_nombre(self):
        mismo_nombre = self.listado([tipo(), tipo(**{**self.DEMO, "id": 99})])
        self.assertIn("Gimnasia (demostración)", mismo_nombre)
        otro_nombre = self.listado([tipo(), tipo(**{**self.DEMO, "nombre": "Otra"})])
        self.assertIn("Otra", otro_nombre)
        activo = self.listado([tipo(), tipo(**{**self.DEMO, "activo": True})])
        self.assertIn("Gimnasia (demostración)", activo)


class RevisarCambioDeTipo(Base):
    def _post(self, sb, datos):
        with mock.patch.object(main, "_sb", return_value=sb):
            return self.client.post("/profesora/horarios/5/editar", data=datos, follow_redirects=False)

    def test_comparacion_pluraliza_cada_lado(self):
        sb = mock.MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 5, "dia_semana": "lunes", "hora_inicio": "08:00:00", "id_tipo_clase": 1, "activo": True}
        ]
        tipos = {1: tipo(id=1, nombre="Gimnasia", cupo_max=1), 2: tipo(id=2, nombre="Pilates", cupo_max=8)}
        with mock.patch.object(tipos_clase, "obtener", side_effect=lambda _sb, tid: tipos.get(tid)), \
             mock.patch.object(tipos_clase, "vista_previa_horario", return_value={"futuras": 0, "cambian": 0, "conservan": 0}):
            r = self._post(sb, {"dia_semana": "lunes", "hora_inicio": "08:00", "id_tipo_clase": "2", "activo": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("1 cupo<", r.text)
        self.assertIn("8 cupos<", r.text)
        self.assertNotIn("cupo 1<", r.text)
        self.assertNotIn("cupo 8<", r.text)


if __name__ == "__main__":
    unittest.main()
