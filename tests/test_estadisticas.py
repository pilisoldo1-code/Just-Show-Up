"""Pruebas de Estadísticas (sin tocar la base: los datos se simulan).

Uso: python -m unittest tests.test_estadisticas -v
"""

import unittest
from datetime import date
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, estadisticas, main
from app.auth import Usuario

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")


def fila(estado, fecha, hora="08:00:00", alumna="a1", nombre="Ana", apellido="Uno", reserva="confirmada"):
    return {
        "estado": estado,
        "reserva": {
            "id_alumna": alumna, "estado": reserva,
            "profiles": {"nombre": nombre, "apellido": apellido},
            "clase": {"fecha": fecha, "hora_inicio": hora},
        },
    }


# 2026-10-05 lunes, 2026-10-07 miércoles, 2026-09-14 lunes, 2026-08-03 lunes
FILAS = [
    fila("presente", "2026-10-05", alumna="a1", nombre="Ana", apellido="Uno"),
    fila("presente", "2026-10-07", "09:00:00", alumna="a1", nombre="Ana", apellido="Uno"),
    fila("presente", "2026-10-07", "09:00:00", alumna="a2", nombre="Bea", apellido="Dos"),
    fila("ausente", "2026-10-05", alumna="a3", nombre="Caro", apellido="Tres"),
    fila("ausente", "2026-10-07", "09:00:00", alumna="a3", nombre="Caro", apellido="Tres"),
    fila("ausente", "2026-10-05", alumna="a2", nombre="Bea", apellido="Dos"),
    fila("presente", "2026-09-14", alumna="a4", nombre="Dani", apellido="Cuatro"),
    fila("presente", "2026-09-14", alumna="a2", nombre="Bea", apellido="Dos"),
    fila("presente", "2026-09-14", alumna="a3", nombre="Caro", apellido="Tres"),
    fila("presente", "2026-09-14", alumna="a1", nombre="Ana", apellido="Uno"),
    fila("presente", "2026-08-03", alumna="a1", nombre="Ana", apellido="Uno"),
    # Reserva cancelada: no cuenta aunque tenga asistencia.
    fila("presente", "2026-10-05", alumna="a5", nombre="Eva", apellido="Cinco", reserva="cancelada"),
]
HOY = date(2026, 10, 19)


def calcular(filas, periodo):
    return estadisticas.calcular(estadisticas._registros(filas), estadisticas.desde_de(periodo, HOY), HOY)


class Periodos(unittest.TestCase):
    def test_este_mes(self):
        self.assertEqual(estadisticas.desde_de("mes", HOY), date(2026, 10, 1))

    def test_tres_meses(self):
        self.assertEqual(estadisticas.desde_de("3meses", HOY), date(2026, 8, 1))

    def test_tres_meses_cruza_el_anio(self):
        self.assertEqual(estadisticas.desde_de("3meses", date(2027, 2, 10)), date(2026, 12, 1))
        self.assertEqual(estadisticas.desde_de("3meses", date(2027, 1, 31)), date(2026, 11, 1))

    def test_todo(self):
        self.assertIsNone(estadisticas.desde_de("todo", HOY))


class Calculo(unittest.TestCase):
    def test_este_mes(self):
        d = calcular(FILAS, "mes")
        self.assertEqual((d["presentes"], d["ausentes"], d["porcentaje"]), (3, 3, 50))
        self.assertEqual((d["dia"], d["dia_cantidad"]), ("Miércoles", 2))
        self.assertEqual((d["horario"], d["horario_cantidad"]), ("Miércoles 09:00", 2))
        self.assertEqual((d["mes"], d["mes_cantidad"]), ("Octubre 2026", 3))
        self.assertEqual(d["top_asistencias"], [{"nombre": "Ana Uno", "cantidad": 2}, {"nombre": "Bea Dos", "cantidad": 1}])
        self.assertEqual(d["top_faltas"], [{"nombre": "Caro Tres", "cantidad": 2}, {"nombre": "Bea Dos", "cantidad": 1}])

    def test_todo(self):
        d = calcular(FILAS, "todo")
        self.assertEqual((d["presentes"], d["ausentes"], d["porcentaje"]), (8, 3, 73))
        self.assertEqual((d["dia"], d["dia_cantidad"]), ("Lunes", 6))
        self.assertEqual((d["horario"], d["horario_cantidad"]), ("Lunes 08:00", 6))
        self.assertEqual((d["mes"], d["mes_cantidad"]), ("Setiembre 2026", 4))
        self.assertEqual([a["nombre"] for a in d["top_asistencias"]], ["Ana Uno", "Bea Dos", "Caro Tres"])
        self.assertEqual(d["top_asistencias"][0]["cantidad"], 4)

    def test_tres_meses_incluye_agosto(self):
        self.assertEqual(calcular(FILAS, "3meses")["presentes"], 8)
        self.assertEqual(calcular([fila("presente", "2026-07-31")], "3meses")["presentes"], 0)

    def test_no_cuenta_futuras(self):
        self.assertEqual(calcular([fila("presente", "2026-10-20")], "todo")["presentes"], 0)

    def test_reserva_cancelada_no_cuenta(self):
        d = calcular([fila("presente", "2026-10-05", reserva="cancelada")], "todo")
        self.assertEqual((d["presentes"], d["porcentaje"]), (0, None))

    def test_empate(self):
        d = calcular([fila("presente", "2026-10-05"), fila("presente", "2026-10-07", alumna="a2", nombre="Bea")], "mes")
        self.assertEqual(d["dia"], "Lunes y Miércoles")
        self.assertEqual(d["horario"], "Lunes 08:00 y Miércoles 08:00")

    def test_top_maximo_tres(self):
        filas = [fila("ausente", "2026-10-05", alumna=f"x{i}", nombre=f"N{i}") for i in range(5)]
        self.assertEqual(len(calcular(filas, "mes")["top_faltas"]), 3)

    def test_sin_datos(self):
        d = calcular([], "todo")
        self.assertEqual((d["presentes"], d["porcentaje"], d["dia"], d["top_asistencias"]), (0, None, "", []))

    def test_solo_ausentes(self):
        d = calcular([fila("ausente", "2026-10-05")], "mes")
        self.assertEqual((d["porcentaje"], d["dia"], d["mes"]), (0, "", ""))


class Pantalla(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        p = mock.patch.object(estadisticas, "_leer_asistencias", return_value=FILAS)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(estadisticas.clases, "ahora", return_value=mock.Mock(date=lambda: HOY))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(main, "_sb", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.get("/profesora/estadisticas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_alumna_no_puede_entrar(self):
        self.como(ALUMNA)
        with mock.patch.object(estadisticas, "estadisticas") as calc:
            r = self.client.get("/profesora/estadisticas", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/?"))
        calc.assert_not_called()

    def test_profesora_ve_la_pantalla(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/estadisticas")
        self.assertEqual(r.status_code, 200)
        for texto in ("Estadísticas", "50%", "Miércoles 09:00", "Octubre 2026", "Ana Uno", "Caro Tres"):
            self.assertIn(texto, r.text)

    def test_periodo_todo(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/estadisticas?periodo=todo")
        self.assertIn("73%", r.text)
        self.assertIn("Setiembre 2026", r.text)

    def test_periodo_invalido_usa_este_mes(self):
        self.como(PROFESORA)
        r = self.client.get("/profesora/estadisticas?periodo=xyz")
        self.assertEqual(r.status_code, 200)
        self.assertIn("50%", r.text)

    def test_error_de_base_muestra_aviso(self):
        self.como(PROFESORA)
        with mock.patch.object(estadisticas, "_leer_asistencias", side_effect=RuntimeError("caída")), \
                self.assertLogs(level="ERROR"):
            r = self.client.get("/profesora/estadisticas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No pudimos cargar las estadísticas", r.text)

    def test_menu_profesora_tiene_estadisticas(self):
        self.como(PROFESORA)
        self.assertIn('href="/profesora/estadisticas"', self.client.get("/profesora/estadisticas").text)


if __name__ == "__main__":
    unittest.main()
