"""Pantallas de la alumna en celular (Mobile alumna): resumen semanal, día, Mis inscripciones, Mi historial, Mis facturas
y Mi perfil. Solo rutas y plantillas, con la base simulada.

Uso: python -m unittest tests.test_mobile_alumna -v
"""
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, clases, main
from app.auth import Usuario

ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")


def clase(fecha="2026-11-10", hora="08:00", estado="disponible", dia="Martes", **extra):
    """Una tarjeta como las que arma clases.clases_entre."""
    c = {"id": abs(hash((fecha, hora))) % 10**6, "fecha": fecha, "dia": dia, "fecha_txt": f"{int(fecha[8:])}/{int(fecha[5:7])}",
         "hora": hora, "duracion": 50, "precio": 1200, "cupo_max": 6, "tipo": "Pilates mat", "imagen": None,
         "profesora": "Lucía Bentancor", "disponibles": 3, "estado": estado, "posicion_espera": None, "oferta_vence": "",
         "puede_cancelar": True}
    c.update(extra)
    return c


def texto(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        for objeto, nombre, valor in ((main, "_sb", mock.MagicMock()), (auth, "_usuario_cacheado", ALUMNA)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def grilla(self, tarjetas):
        with mock.patch.object(clases, "generar_clases"), mock.patch.object(clases, "clases_entre", return_value=tarjetas):
            return self.client.get("/").text


class ResumenSemanal(Base):
    def test_dos_horarios(self):
        html = self.grilla([clase(hora="08:00"), clase(hora="09:00")])
        t = texto(html)
        self.assertIn("2 horarios · de 08:00 a 09:00", t)
        self.assertIn("2 horarios con lugar", t)
        self.assertIn('href="/dia/2026-11-10"', html)
        self.assertNotIn("hora-chip", html)  # ya no se listan las horas en la semana

    def test_diez_horarios_entran_en_una_tarjeta(self):
        html = self.grilla([clase(hora=f"{h:02d}:00") for h in range(7, 17)])
        self.assertIn("10 horarios · de 07:00 a 16:00", texto(html))
        self.assertEqual(html.count('class="card card-dia-link'), 1)

    def test_un_horario(self):
        self.assertIn("1 horario · 19:00", texto(self.grilla([clase(hora="19:00")])))

    def test_reserva_y_lista_de_espera_a_la_vista(self):
        t = texto(self.grilla([clase(hora="08:00", estado="inscripta"), clase(hora="09:00", estado="espera"),
                               clase(hora="10:00", estado="oferta")]))
        for x in ("Reservada 08:00", "En lista de espera 09:00", "¡Lugar disponible! 10:00", "Ver día ›"):
            self.assertIn(x, t)
        self.assertNotIn("con lugar", t)  # si tiene algo ese día, se muestra eso

    def test_dia_completo(self):
        self.assertIn("Sin lugares: podés anotarte en la lista de espera",
                      texto(self.grilla([clase(estado="completa"), clase(hora="09:00", estado="completa")])))

    def test_dia_terminado_compacto_y_accesible_sin_reservar(self):
        html = self.grilla([clase(fecha="2026-11-03", hora="08:00", estado="pasada"),
                            clase(fecha="2026-11-04", hora="08:00", estado="disponible")])
        terminado = html[html.index('href="/dia/2026-11-03"') - 200:html.index('href="/dia/2026-11-04"')]
        self.assertIn("card-finalizada", terminado)  # A1: franja compacta
        self.assertIn("Finalizado", terminado)
        self.assertNotIn("Ver día", terminado)
        self.assertNotIn("card-finalizada", html[html.index('href="/dia/2026-11-04"') - 200:])

    def test_clases_canceladas_por_la_profesora(self):
        self.assertIn("Clases canceladas por la profesora", texto(self.grilla([clase(estado="cancelada")])))


if __name__ == "__main__":
    unittest.main()
