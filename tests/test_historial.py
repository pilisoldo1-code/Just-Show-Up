"""Mi historial: las reservas que canceló la alumna no aparecen (siguen guardadas en la base), las que canceló la
profesora o la empresa aparecen como «Cancelada», y todo va de la clase más reciente a la más antigua.

Uso: python -m unittest tests.test_historial -v
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from app import clases, main
from app.auth import Usuario

TZ = ZoneInfo("America/Montevideo")


def clase(fecha, hora="19:00:00"):
    return {"id": hash(fecha + hora) % 10000, "fecha": fecha, "hora_inicio": hora, "duracion": 60, "precio": 800}


RESERVAS = [
    {"id": 1, "estado": "confirmada", "id_bloqueo": None, "asistencia": [{"estado": "presente"}], "clase": clase("2026-10-30")},
    {"id": 2, "estado": "confirmada", "id_bloqueo": None, "asistencia": [{"estado": "ausente"}], "clase": clase("2026-10-26")},
    {"id": 3, "estado": "confirmada", "id_bloqueo": None, "asistencia": [], "clase": clase("2026-10-23")},
    {"id": 4, "estado": "cancelada", "id_bloqueo": None, "asistencia": [], "clase": clase("2026-10-28")},   # la alumna
    {"id": 5, "estado": "cancelada", "id_bloqueo": 7, "asistencia": [], "clase": clase("2026-10-27")},      # la profesora
    {"id": 6, "estado": "cancelada", "id_bloqueo": None, "asistencia": [], "clase": clase("2026-11-10")},   # la alumna, futura
    {"id": 7, "estado": "cancelada", "id_bloqueo": 8, "asistencia": [], "clase": clase("2026-10-26", "08:00:00")},
]


class Historial(unittest.TestCase):
    def setUp(self):
        sb = mock.MagicMock()
        tablas = {"reserva": mock.MagicMock(), "listadeespera": mock.MagicMock()}
        tablas["reserva"].select.return_value.eq.return_value.execute.return_value.data = RESERVAS
        tablas["listadeespera"].select.return_value.eq.return_value.in_.return_value.execute.return_value.data = []
        sb.table.side_effect = lambda t: tablas[t]
        sb.rpc.return_value.execute.return_value.data = []
        with mock.patch.object(clases, "ahora", return_value=datetime(2026, 11, 2, 10, 0, tzinfo=TZ)):
            self.datos = clases.mis_inscripciones(sb, "alu")
        self.historial = self.datos["historial"]

    def test_las_canceladas_por_la_alumna_no_aparecen(self):
        fechas = [c["fecha"] for c in self.historial]
        self.assertNotIn("2026-10-28", fechas)
        self.assertNotIn("2026-11-10", fechas)

    def test_las_canceladas_por_la_profesora_aparecen_como_cancelada(self):
        canceladas = [c for c in self.historial if c["cancelada_por_profesora"]]
        self.assertEqual({c["fecha"] for c in canceladas}, {"2026-10-27", "2026-10-26"})
        self.assertTrue(all(c["estado_txt"] == "Cancelada" for c in canceladas))

    def test_orden_de_la_mas_reciente_a_la_mas_antigua_sin_agrupar_por_estado(self):
        self.assertEqual([(c["fecha"], c["hora"]) for c in self.historial],
                         [("2026-10-30", "19:00"), ("2026-10-27", "19:00"), ("2026-10-26", "19:00"),
                          ("2026-10-26", "08:00"), ("2026-10-23", "19:00")])
        self.assertEqual([c["estado_txt"] for c in self.historial],
                         ["Presente", "Cancelada", "Ausente", "Cancelada", "Sin registrar"])

    def test_la_tarjeta_cancelada_muestra_solo_estado_fecha_hora_y_duracion(self):
        html = main.templates.get_template("mi_historial.html").render(
            request=SimpleNamespace(url=SimpleNamespace(path="/mi-historial")),
            usuario=Usuario(id="a", mail="a@prueba.invalid", nombre="Ana", apellido="P", rol="alumna", token="t"),
            historial=self.historial, presentes=1, ausentes=1)
        tarjetas = html.split('<li class="historial-item">')[1:]
        cancelada = tarjetas[1]
        self.assertIn(">Cancelada</span>", cancelada)
        self.assertIn("19:00 · 60 min", cancelada)
        self.assertNotIn("$", cancelada)
        self.assertIn("$800", tarjetas[0])  # las demás conservan su precio
        for texto in ("por la profesora", "la empresa", "title="):
            self.assertNotIn(texto, html)


if __name__ == "__main__":
    unittest.main()
