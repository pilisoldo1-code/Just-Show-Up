"""Pruebas de Estadísticas (sin tocar la base: los datos se simulan).

Uso: python -m unittest tests.test_estadisticas -v
"""

import unittest
from datetime import date
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, estadisticas, main, personas, tipos_clase
from app.auth import Usuario

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")

# Con multiempresa de verdad (con_membresias=True): a diferencia de PROFESORA de arriba (que por
# compatibilidad queda como administradora), esta es una docente común, sin permisos de admin.
PROFESORA_COMUN = Usuario(id="carol", mail="carolina@prueba.invalid", nombre="Carolina", apellido="Méndez",
                          rol="profesora", token="t", con_membresias=True, es_admin=False, es_docente=True,
                          empresa_id=2, empresa_nombre="Pilates Centro")
ADMIN = Usuario(id="lucia", mail="lucia@prueba.invalid", nombre="Lucía", apellido="Bentancor",
                rol="profesora", token="t", con_membresias=True, es_admin=True, es_docente=True,
                empresa_id=2, empresa_nombre="Pilates Centro")

PENDIENTES_VACIO = {"clases": 0, "asistencias": 0, "ultima_fecha": None}


def fila(estado, fecha, hora="08:00:00", alumna="a1", nombre="Ana", apellido="Uno", reserva="confirmada",
        id_profesora="prof1", id_tipo_clase=1):
    return {
        "estado": estado,
        "reserva": {
            "id_alumna": alumna, "estado": reserva,
            "profiles": {"nombre": nombre, "apellido": apellido},
            "clase": {"fecha": fecha, "hora_inicio": hora, "id_profesora": id_profesora, "id_tipo_clase": id_tipo_clase},
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


def calcular(filas, periodo, **kw):
    return estadisticas.calcular(estadisticas._registros(filas), estadisticas.desde_de(periodo, HOY), HOY, **kw)


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

    def test_filtra_por_profesora(self):
        filas = [fila("presente", "2026-10-05", id_profesora="p1"), fila("presente", "2026-10-06", id_profesora="p2")]
        self.assertEqual(calcular(filas, "mes", id_profesora="p1")["presentes"], 1)
        self.assertEqual(calcular(filas, "mes", id_profesora="p2")["presentes"], 1)
        self.assertEqual(calcular(filas, "mes", id_profesora="otra")["presentes"], 0)

    def test_filtra_por_tipo_de_clase(self):
        filas = [fila("presente", "2026-10-05", id_tipo_clase=1), fila("presente", "2026-10-06", id_tipo_clase=2)]
        self.assertEqual(calcular(filas, "mes", id_tipo_clase=1)["presentes"], 1)
        self.assertEqual(calcular(filas, "mes", id_tipo_clase=99)["presentes"], 0)


class Evolucion(unittest.TestCase):
    """Punto 1: la evolución mensual respeta el período elegido (sin meses de más ni de menos)."""

    def test_este_mes_un_solo_mes(self):
        d = calcular(FILAS, "mes")
        self.assertEqual([m["etiqueta"] for m in d["evolucion"]], ["oct 2026"])
        self.assertEqual(d["evolucion"][0], {"etiqueta": "oct 2026", "presentes": 3, "ausentes": 3})

    def test_tres_meses_los_tres_aunque_uno_este_vacio(self):
        # agosto (1 presente), setiembre (4 presentes), octubre (3 y 3): ningún mes queda afuera.
        d = calcular(FILAS, "3meses")
        self.assertEqual([m["etiqueta"] for m in d["evolucion"]], ["ago 2026", "set 2026", "oct 2026"])

    def test_todo_arranca_en_el_primer_dato_no_antes(self):
        d = calcular(FILAS, "todo")
        self.assertEqual(d["evolucion"][0]["etiqueta"], "ago 2026")

    def test_todo_sin_datos_no_tiene_meses(self):
        self.assertEqual(calcular([], "todo")["evolucion"], [])

    def test_mes_sin_asistencias_queda_en_cero_no_se_omite(self):
        # Solo hay un dato en octubre; "3 meses" igual debe listar agosto y setiembre en 0.
        d = calcular([fila("presente", "2026-10-05")], "3meses")
        ceros = [m for m in d["evolucion"] if m["etiqueta"] in ("ago 2026", "set 2026")]
        self.assertEqual(ceros, [{"etiqueta": "ago 2026", "presentes": 0, "ausentes": 0},
                                 {"etiqueta": "set 2026", "presentes": 0, "ausentes": 0}])


class OcupacionYPopularidad(unittest.TestCase):
    def _clase(self, id, cupo_max=10, id_profesora="p1", id_tipo_clase=1, tipo="Gimnasia"):
        return {"id": id, "cupo_max": cupo_max, "id_profesora": id_profesora, "id_tipo_clase": id_tipo_clase,
                "tipo_clase": {"nombre": tipo}}

    def test_ocupacion_promedio_simple_no_ponderado(self):
        # Una clase de cupo 10 al 100% y una de cupo 2 al 0%: el promedio simple da 50%,
        # no 83% (10 de 12) como daría un promedio ponderado por cupo.
        terminadas = [self._clase(1, cupo_max=10), self._clase(2, cupo_max=2)]
        from collections import Counter
        confirmadas = Counter({1: 10, 2: 0})
        self.assertEqual(estadisticas._ocupacion_promedio(terminadas, confirmadas), 50)

    def test_ocupacion_sin_clases_terminadas_es_none(self):
        self.assertIsNone(estadisticas._ocupacion_promedio([], {}))

    def test_ocupacion_no_cuenta_reservas_canceladas(self):
        # _reservas_confirmadas_de ya filtra estado='confirmada' en la consulta; acá se prueba
        # que el cálculo usa exactamente lo que esa función devuelve (sin fallback a cantidad_inscriptas).
        from collections import Counter
        terminadas = [self._clase(1, cupo_max=4)]
        self.assertEqual(estadisticas._ocupacion_promedio(terminadas, Counter({1: 2})), 50)
        self.assertEqual(estadisticas._ocupacion_promedio(terminadas, Counter()), 0)

    def test_tipos_populares_ordenados_y_sin_ceros(self):
        from collections import Counter
        terminadas = [self._clase(1, id_tipo_clase=1, tipo="Gimnasia"), self._clase(2, id_tipo_clase=2, tipo="Pilates"),
                      self._clase(3, id_tipo_clase=3, tipo="Yoga")]
        confirmadas = Counter({1: 5, 2: 8, 3: 0})
        r = estadisticas._tipos_populares(terminadas, confirmadas)
        self.assertEqual([t["nombre"] for t in r], ["Pilates", "Gimnasia"])  # Yoga con 0 no aparece

    def test_tipos_populares_suma_varias_clases_del_mismo_tipo(self):
        from collections import Counter
        terminadas = [self._clase(1, id_tipo_clase=1, tipo="Gimnasia"), self._clase(2, id_tipo_clase=1, tipo="Gimnasia")]
        r = estadisticas._tipos_populares(terminadas, Counter({1: 3, 2: 4}))
        self.assertEqual(r, [{"nombre": "Gimnasia", "cantidad": 7}])

    def test_leer_clases_terminadas_excluye_futuras(self):
        from datetime import datetime
        sb = mock.MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.lte.return_value.order.return_value.range.return_value.execute.return_value.data = [
            self._clase(1) | {"fecha": "2026-10-19", "hora_inicio": "08:00:00", "duracion": 60},  # ya terminó
            self._clase(2) | {"fecha": "2026-10-19", "hora_inicio": "23:00:00", "duracion": 60},  # todavía no
        ]
        ahora = datetime(2026, 10, 19, 10, 0, tzinfo=estadisticas.clases.TZ)
        with mock.patch.object(estadisticas.clases, "ahora", return_value=ahora):
            r = estadisticas._leer_clases_terminadas(sb, None, date(2026, 10, 19), None, None)
        self.assertEqual([c["id"] for c in r], [1])


class Graficas(unittest.TestCase):
    def test_linea_sin_datos_es_none(self):
        self.assertIsNone(estadisticas._preparar_linea([]))

    def test_linea_un_mes_marca_un_solo_punto(self):
        g = estadisticas._preparar_linea([{"etiqueta": "oct 2026", "presentes": 3, "ausentes": 1}])
        self.assertTrue(g["un_mes"])
        self.assertEqual(len(g["puntos_presentes"].split(",")), 2)

    def test_linea_varios_meses_tiene_polilinea_y_escala(self):
        g = estadisticas._preparar_linea([
            {"etiqueta": "ago 2026", "presentes": 1, "ausentes": 0},
            {"etiqueta": "set 2026", "presentes": 4, "ausentes": 2},
        ])
        self.assertFalse(g["un_mes"])
        self.assertEqual(len(g["puntos_presentes"].split(" ")), 2)
        self.assertIn({"y": g["base_y"], "texto": "0"}, g["marcas_y"])

    def test_dona_sin_datos_es_none(self):
        self.assertIsNone(estadisticas._preparar_dona(0, 0))

    def test_dona_porcentaje(self):
        g = estadisticas._preparar_dona(3, 1)
        self.assertEqual(g["porcentaje"], 75)

    def test_barras_sin_tipos_es_vacio(self):
        self.assertEqual(estadisticas._preparar_barras([]), [])

    def test_barras_porcentaje_relativo_al_maximo(self):
        g = estadisticas._preparar_barras([{"nombre": "Pilates", "cantidad": 8}, {"nombre": "Gimnasia", "cantidad": 4}])
        self.assertEqual([b["porcentaje"] for b in g], [100, 50])


class Pendientes(unittest.TestCase):
    def test_reusa_pendientes_totales_existente(self):
        sb = mock.MagicMock()
        with mock.patch.object(estadisticas.clases, "pendientes_totales", return_value={"clases": 2, "asistencias": 5, "ultima_fecha": None}) as m:
            r = estadisticas.pendientes(sb)
        m.assert_called_once_with(sb)
        self.assertEqual(r, {"clases": 2, "asistencias": 5, "ultima_fecha": None})


class Pantalla(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        p = mock.patch.object(estadisticas, "_leer_asistencias", return_value=FILAS)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(estadisticas.clases, "ahora", return_value=mock.Mock(date=lambda: HOY))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(estadisticas, "_leer_clases_terminadas", return_value=[])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(estadisticas, "pendientes", return_value=dict(PENDIENTES_VACIO))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(tipos_clase, "listar", return_value=[])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(personas, "nombres_profesoras", return_value={})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(main, "_sb", return_value="SB")
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

    # ---------- punto 5: ocupación y popularidad no dependen de que haya asistencias ----------

    def test_ocupacion_y_tipos_se_muestran_sin_asistencias(self):
        self.como(PROFESORA)
        with mock.patch.object(estadisticas, "_leer_asistencias", return_value=[]), \
             mock.patch.object(estadisticas, "_leer_clases_terminadas",
                               return_value=[{"id": 1, "cupo_max": 10, "id_profesora": "p", "id_tipo_clase": 1,
                                              "tipo_clase": {"nombre": "Gimnasia"}}]), \
             mock.patch.object(estadisticas, "_reservas_confirmadas_de", return_value=mock.Mock(get=lambda k, d=0: 7)):
            r = self.client.get("/profesora/estadisticas")
        self.assertIn("Todavía no hay asistencias marcadas en este período.", r.text)
        self.assertIn("70%", r.text)  # ocupación: 7/10
        self.assertIn("Gimnasia", r.text)  # tipo más elegido, aunque no haya una sola asistencia


class Permisos(unittest.TestCase):
    """Punto 7: la profesora común nunca puede ver otra cosa que no sea lo suyo, ni aunque lo pida
    por parámetro; solo la administradora puede elegir "por profesora", y solo entre las de su empresa."""

    def setUp(self):
        self.client = TestClient(main.app)
        for target, attr, value in (
            (estadisticas, "_leer_asistencias", []),
            (estadisticas, "_leer_clases_terminadas", []),
            (estadisticas, "pendientes", dict(PENDIENTES_VACIO)),
            (tipos_clase, "listar", []),
        ):
            p = mock.patch.object(target, attr, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(estadisticas.clases, "ahora", return_value=mock.Mock(date=lambda: HOY))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(main, "_sb", return_value="SB")
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_profesora_comun_no_puede_pedir_otra_profesora(self):
        self.como(PROFESORA_COMUN)
        with mock.patch.object(personas, "nombres_profesoras", return_value={"otra": "Otra Profe"}), \
             mock.patch.object(estadisticas, "calcular", wraps=estadisticas.calcular) as calc:
            self.client.get("/profesora/estadisticas?profesora=otra")
        # usuario.es_admin es False: el id_profesora que llega a calcular() es el suyo, no "otra".
        self.assertEqual(calc.call_args.args[3], PROFESORA_COMUN.id)

    def test_profesora_comun_no_ve_el_selector_por_profesora(self):
        self.como(PROFESORA_COMUN)
        with mock.patch.object(personas, "nombres_profesoras", return_value={"x": "X"}) as m:
            r = self.client.get("/profesora/estadisticas")
        m.assert_not_called()  # ni se consulta: solo tiene sentido para admin
        self.assertNotIn("Toda la empresa", r.text)

    def test_admin_puede_elegir_una_profesora_de_su_empresa(self):
        self.como(ADMIN)
        with mock.patch.object(personas, "nombres_profesoras", return_value={"carol": "Carolina Méndez"}), \
             mock.patch.object(estadisticas, "calcular", wraps=estadisticas.calcular) as calc:
            r = self.client.get("/profesora/estadisticas?profesora=carol")
        self.assertEqual(calc.call_args.args[3], "carol")
        self.assertIn("Carolina Méndez", r.text)

    def test_admin_con_id_de_profesora_ajeno_cae_a_toda_la_empresa(self):
        self.como(ADMIN)
        with mock.patch.object(personas, "nombres_profesoras", return_value={"carol": "Carolina Méndez"}), \
             mock.patch.object(estadisticas, "calcular", wraps=estadisticas.calcular) as calc:
            # "intrusa" no está en la lista de profesoras de SU empresa (ni siquiera existe acá).
            self.client.get("/profesora/estadisticas?profesora=intrusa")
        self.assertIsNone(calc.call_args.args[3])

    def test_tipo_de_clase_invalido_se_ignora(self):
        self.como(PROFESORA_COMUN)
        with mock.patch.object(tipos_clase, "listar", return_value=[{"id": 1, "nombre": "Gimnasia", "activo": True}]), \
             mock.patch.object(estadisticas, "calcular", wraps=estadisticas.calcular) as calc:
            self.client.get("/profesora/estadisticas?tipo_clase=999")
        self.assertIsNone(calc.call_args.args[4])

    def test_tipo_de_clase_valido_se_usa(self):
        self.como(PROFESORA_COMUN)
        with mock.patch.object(tipos_clase, "listar", return_value=[{"id": 7, "nombre": "Pilates", "activo": True}]), \
             mock.patch.object(estadisticas, "calcular", wraps=estadisticas.calcular) as calc:
            self.client.get("/profesora/estadisticas?tipo_clase=7")
        self.assertEqual(calc.call_args.args[4], 7)

    def test_aclaracion_de_pendientes_para_administradora(self):
        self.como(ADMIN)
        r = self.client.get("/profesora/estadisticas")
        self.assertIn("Total de pendientes de la empresa, independientemente de los filtros", r.text)

    def test_aclaracion_de_pendientes_para_profesora_comun(self):
        self.como(PROFESORA_COMUN)
        r = self.client.get("/profesora/estadisticas")
        self.assertIn("Total de pendientes de tus clases, independientemente de los filtros", r.text)


class SoloLectura(unittest.TestCase):
    """Punto 9: Estadísticas no debe escribir nada en la base bajo ningún filtro."""

    def test_estadisticas_no_llama_metodos_de_escritura(self):
        sb = mock.MagicMock()
        for metodo in ("insert", "update", "upsert", "delete"):
            getattr(sb.table.return_value, metodo).side_effect = AssertionError(f"Estadísticas no debería llamar a {metodo}()")
        sb.table.return_value.select.return_value.order.return_value.range.return_value.execute.return_value.data = []
        sb.table.return_value.select.return_value.eq.return_value.lte.return_value.order.return_value.range.return_value.execute.return_value.data = []
        with mock.patch.object(estadisticas.clases, "pendientes_totales", return_value=dict(PENDIENTES_VACIO)):
            estadisticas.estadisticas(sb, "mes", ADMIN)
        # rpc() solo se usa para pendientes_totales, mockeado arriba: tampoco debería llamarse acá.
        sb.rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
