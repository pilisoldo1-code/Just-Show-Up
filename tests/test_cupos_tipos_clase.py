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
