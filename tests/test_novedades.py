"""Novedades (notificaciones, fase 1): avisos calculados en el momento, solo lectura, por rol y por empresa.

Uso: python -m unittest tests.test_novedades -v
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, clases, main, novedades
from app.auth import Usuario

AHORA = datetime(2026, 11, 11, 10, 0, tzinfo=clases.TZ)
JSU, PIL = 1, 2


def alumna(empresa=JSU):
    return Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t",
                   es_alumna=True, empresa_id=empresa, empresa_nombre="Just Show Up", con_membresias=True)


def profesora():
    return Usuario(id="p", mail="profe@prueba.invalid", nombre="Pía", apellido="Prueba", rol="alumna", token="t",
                   es_docente=True, empresa_id=JSU, empresa_nombre="Just Show Up", con_membresias=True)


def administradora(empresa=JSU):
    return Usuario(id="ad", mail="admin@prueba.invalid", nombre="Ada", apellido="Prueba", rol="alumna", token="t",
                   es_admin=True, es_docente=True, empresa_id=empresa, empresa_nombre="Just Show Up",
                   con_membresias=True)


def superadmin():
    return Usuario(id="s", mail="superadmin@prueba.invalid", nombre="Sara", apellido="Prueba", rol="alumna", token="t",
                   es_superadmin=True, con_membresias=True)


class Consulta:
    """Imita la cadena de supabase-py: aplica eq / in_ sobre columnas simples y registra todo lo que se llama."""

    def __init__(self, base, tabla):
        self.base, self.tabla, self.filas = base, tabla, list(base.tablas.get(tabla, []))

    def select(self, campos):
        return self

    def eq(self, col, valor):
        self.filas = [f for f in self.filas if f.get(col) == valor]
        return self

    def in_(self, col, valores):
        self.filas = [f for f in self.filas if f.get(col) in valores]
        return self

    def execute(self):
        return SimpleNamespace(data=self.filas)

    def __getattr__(self, nombre):  # insert, update, delete, upsert...: nunca deberían llamarse
        self.base.escrituras.append((self.tabla, nombre))
        raise AssertionError(f"Novedades no debe llamar a {nombre} sobre {self.tabla}")


# Funciones de solo lectura que la alumna consulta siempre (videos y plazos): sin datos, por defecto.
RPCS_ALUMNA = {"videos_disponibles": [], "novedades_videos_iniciados": [], "novedades_parametros": {}}
# Las únicas funciones que Novedades puede llamar: todas de solo lectura.
RPCS_SOLO_LECTURA = set(RPCS_ALUMNA) | {"asistencias_pendientes_resumen", "resumen_empresas", "administradoras_empresa"}


class BaseFalsa:
    def __init__(self, tablas=None, rpcs=None):
        self.tablas, self.rpcs, self.escrituras, self.rpcs_llamadas = tablas or {}, {**RPCS_ALUMNA, **(rpcs or {})}, [], []

    def table(self, nombre):
        return Consulta(self, nombre)

    def rpc(self, nombre, params):
        self.rpcs_llamadas.append(nombre)
        valor = self.rpcs[nombre]
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=valor(params) if callable(valor) else valor))


def clase(id_, fecha, hora="18:00:00", empresa=JSU, tipo="Pilates"):
    return {"id": id_, "fecha": fecha, "hora_inicio": hora, "id_empresa": empresa, "tipo_clase": {"nombre": tipo}}


def factura(mes, vence, empresa=JSU, estado="confirmada", pago="pendiente", alumna_id="a", nombre="Marta"):
    return {"id": mes * 10 + empresa, "numero": f"JSU-2026-{mes:02d}-0001", "mes": mes, "anio": 2026, "total": "800.00",
            "vence_el": vence, "estado_factura": estado, "estado_pago": pago, "id_alumna": alumna_id,
            "id_empresa": empresa, "alumna_nombre": nombre, "alumna_apellido": "Gómez"}


SIN_PENDIENTES = {"asistencias_pendientes_resumen": {"clases": 0, "asistencias": 0, "ultima_fecha": None}}


def de(usuario, base, invitacion=lambda _id: False):
    return novedades.de_usuario(base, usuario, invitacion, ahora=AHORA)


class Alumna(unittest.TestCase):
    def test_oferta_vigente_es_urgente_y_lleva_a_mis_inscripciones(self):
        base = BaseFalsa({
            "listadeespera": [{"id_clase": 7, "id_alumna": "a", "estado": "ofrecido", "id_empresa": JSU,
                               "oferta_vence_en": "2026-11-11T13:25:00+00:00"}],
            "clase": [clase(7, "2026-11-11")],
        })
        r = de(alumna(), base)
        self.assertEqual(len(r["novedades"]), 1)
        n = r["novedades"][0]
        self.assertEqual((n["nivel"], n["enlace"]), (novedades.URGENTE, "/mis-inscripciones"))
        self.assertIn("10:25", n["detalle"])  # hora de Montevideo
        self.assertIn("Pilates", n["detalle"])

    def test_oferta_vencida_o_de_clase_empezada_no_aparece(self):
        base = BaseFalsa({
            "listadeespera": [
                {"id_clase": 7, "id_alumna": "a", "estado": "ofrecido", "id_empresa": JSU,
                 "oferta_vence_en": "2026-11-11T12:59:00+00:00"},
                {"id_clase": 8, "id_alumna": "a", "estado": "ofrecido", "id_empresa": JSU,
                 "oferta_vence_en": "2026-11-11T14:00:00+00:00"}],
            "clase": [clase(7, "2026-11-11"), clase(8, "2026-11-11", hora="09:00:00")],
        })
        self.assertEqual(de(alumna(), base)["novedades"], [])

    def test_clases_canceladas_por_la_profesora_se_agrupan(self):
        base = BaseFalsa({"reserva": [
            {"id_alumna": "a", "estado": "cancelada", "id_bloqueo": 3, "id_empresa": JSU, "clase": clase(1, "2026-11-13")},
            {"id_alumna": "a", "estado": "cancelada", "id_bloqueo": 3, "id_empresa": JSU, "clase": clase(2, "2026-11-12")},
            # la canceló ella misma: no es novedad
            {"id_alumna": "a", "estado": "cancelada", "id_bloqueo": None, "id_empresa": JSU, "clase": clase(3, "2026-11-14")},
            # ya pasó
            {"id_alumna": "a", "estado": "cancelada", "id_bloqueo": 3, "id_empresa": JSU, "clase": clase(4, "2026-11-10")},
        ]})
        n = de(alumna(), base)["novedades"]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]["nivel"], novedades.AVISO)
        self.assertIn("2 clases", n[0]["titulo"])
        self.assertTrue(n[0]["lineas"][0].startswith("Pilates · Jueves 12/11"))  # en orden de fecha

    def test_facturas_vencida_urgente_y_pendiente(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-10-10"), factura(10, "2026-11-20"),
                                              factura(8, "2026-09-10", pago="pagado")]})
        n = de(alumna(), base)["novedades"]
        self.assertEqual([x["nivel"] for x in n], [novedades.URGENTE, novedades.PENDIENTE])
        self.assertIn("setiembre", n[0]["titulo"].lower())
        self.assertIn("Nueva factura", n[1]["titulo"])
        self.assertIn("20/11/2026", n[1]["detalle"])

    def test_no_ve_nada_de_otra_empresa(self):
        """La alumna compartida entra a Just Show Up: lo de Pilates Centro no aparece aunque la base lo devolviera."""
        base = BaseFalsa({
            "listadeespera": [{"id_clase": 9, "id_alumna": "a", "estado": "ofrecido", "id_empresa": PIL,
                               "oferta_vence_en": "2026-11-11T14:00:00+00:00"}],
            "clase": [clase(9, "2026-11-11", empresa=PIL)],
            "reserva": [{"id_alumna": "a", "estado": "cancelada", "id_bloqueo": 1, "id_empresa": PIL,
                         "clase": clase(9, "2026-11-12", empresa=PIL)}],
            "resumen_mensual": [factura(10, "2026-11-01", empresa=PIL)],
        })
        self.assertEqual(de(alumna(JSU), base)["novedades"], [])
        self.assertEqual(len(de(alumna(PIL), base)["novedades"]), 3)

    def test_no_ve_avisos_de_docente_ni_de_administradora(self):
        base = BaseFalsa(rpcs={"asistencias_pendientes_resumen": {"clases": 4, "asistencias": 9, "ultima_fecha": "2026-11-10"}})
        self.assertEqual(de(alumna(), base)["novedades"], [])
        self.assertNotIn("asistencias_pendientes_resumen", base.rpcs_llamadas)


def reserva(clase_, reservada="2026-11-01T12:00:00+00:00", empresa=JSU, alumna_id="a", estado="confirmada"):
    return {"id_alumna": alumna_id, "estado": estado, "id_empresa": empresa, "fecha_reserva": reservada,
            "id_bloqueo": None, "clase": {**clase_, "estado": clase_.get("estado", "programada")}}


def video(id_, restante_seg, clase_txt="Pilates", fecha="2026-11-10", hora="18:00:00"):
    return {"id": id_, "clase": clase_txt, "fecha": fecha, "hora_inicio": hora, "segundos_restantes": restante_seg}


class AlumnaFaseA(unittest.TestCase):
    """Las 10 notificaciones de la alumna dentro de la app (fase A). AHORA = miércoles 11/11/2026 10:00 (Montevideo)."""

    def test_recordatorio_dentro_de_la_hora(self):
        base = BaseFalsa({"reserva": [reserva(clase(1, "2026-11-11", hora="10:45:00")),
                                      reserva(clase(2, "2026-11-11", hora="11:00:00")),   # justo 60 min: sí
                                      reserva(clase(3, "2026-11-11", hora="11:01:00")),   # 61 min: todavía no
                                      reserva(clase(4, "2026-11-11", hora="09:30:00"))]})  # ya empezó
        n = [x for x in de(alumna(), base)["novedades"] if x["titulo"].startswith("Tu clase empieza")]
        self.assertEqual([x["titulo"] for x in n], ["Tu clase empieza a las 10:45", "Tu clase empieza a las 11:00"])
        self.assertIn("45 minutos", n[0]["detalle"])
        self.assertEqual(n[0]["enlace"], "/dia/2026-11-11")

    def test_recordatorio_no_aparece_si_la_clase_se_cancelo_o_la_reserva_no_esta_confirmada(self):
        base = BaseFalsa({"reserva": [reserva({**clase(1, "2026-11-11", hora="10:30:00"), "estado": "cancelada"}),
                                      reserva(clase(2, "2026-11-11", hora="10:30:00"), estado="cancelada")]})
        self.assertFalse([x for x in de(alumna(), base)["novedades"] if "empieza" in x["titulo"]])

    def test_reserva_confirmada_reciente_y_sin_duplicar_con_el_recordatorio(self):
        hace_una_hora = "2026-11-11T12:00:00+00:00"
        base = BaseFalsa({"reserva": [
            reserva(clase(1, "2026-11-12"), reservada=hace_una_hora),
            reserva(clase(2, "2026-11-13"), reservada="2026-11-09T12:00:00+00:00"),  # hace 2 días: ya no
            reserva(clase(3, "2026-11-11", hora="10:30:00"), reservada=hace_una_hora),  # sale como recordatorio
        ]})
        n = de(alumna(), base)["novedades"]
        confirmadas = [x for x in n if "eserva confirmada" in x["titulo"]]
        self.assertEqual(len(confirmadas), 1)
        self.assertEqual(confirmadas[0]["titulo"], "Reserva confirmada")
        self.assertEqual(confirmadas[0]["lineas"], ["Pilates · Jueves 12/11 18:00"])
        self.assertEqual(sum("empieza" in x["titulo"] for x in n), 1)

    def test_factura_proxima_a_vencer_hoy_y_dentro_del_plazo(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-11-11"), factura(8, "2026-11-14"), factura(7, "2026-11-15")]})
        n = de(alumna(), base)["novedades"]
        titulos = [x["titulo"] for x in n]
        self.assertIn("Tu factura de setiembre 2026 vence hoy", titulos)
        self.assertIn("Tu factura de agosto 2026 vence el 14/11/2026", titulos)
        self.assertIn("Nueva factura de julio 2026", titulos)  # vence en 4 días: todavía no es «próxima»

    def test_factura_vencida_desde_el_dia_siguiente_y_no_si_esta_pagada_o_anulada(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-11-10"), factura(8, "2026-11-01", pago="pagado"),
                                              factura(7, "2026-11-01", estado="anulada")]})
        n = de(alumna(), base)["novedades"]
        self.assertEqual([x["titulo"] for x in n], ["Factura de setiembre 2026 vencida"])

    def test_facturas_vencidas_antes_que_las_proximas_a_vencer(self):
        base = BaseFalsa({"resumen_mensual": [factura(8, "2026-11-12"), factura(9, "2026-11-01"), factura(10, "2026-11-30")]})
        self.assertEqual([x["titulo"] for x in de(alumna(), base)["novedades"]],
                         ["Factura de setiembre 2026 vencida", "Tu factura de agosto 2026 vence mañana",
                          "Nueva factura de octubre 2026"])

    def test_plazo_de_factura_configurable(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-11-15")]}, {"novedades_parametros": {"factura_aviso_dias": 5}})
        self.assertIn("vence el 15/11/2026", de(alumna(), base)["novedades"][0]["titulo"])

    def test_parametros_invalidos_o_ausentes_usan_los_de_por_defecto(self):
        for leidos in ({"factura_aviso_dias": 0}, {"factura_aviso_dias": "5"}, {"factura_aviso_dias": True},
                       {"video_aviso_horas": 99}, None):
            with self.subTest(leidos=leidos):
                base = BaseFalsa(rpcs={"novedades_parametros": leidos})
                self.assertEqual(novedades.parametros(base), novedades.PARAMETROS)
        base = BaseFalsa()
        del base.rpcs["novedades_parametros"]  # la función todavía no existe en la base
        with self.assertLogs(level="WARNING"):
            self.assertEqual(novedades.parametros(base), novedades.PARAMETROS)

    def test_pago_registrado_reciente_vigente_y_propio(self):
        base = BaseFalsa({
            "pago": [{"id_resumen_mensual": 91, "monto": "800.00", "medio_pago": "mercado_pago", "estado": "vigente",
                      "creado_en": "2026-11-10T15:00:00+00:00", "id_empresa": JSU},
                     {"id_resumen_mensual": 81, "monto": "800.00", "medio_pago": "efectivo", "estado": "vigente",
                      "creado_en": "2026-11-01T15:00:00+00:00", "id_empresa": JSU},   # hace 10 días
                     {"id_resumen_mensual": 71, "monto": "800.00", "medio_pago": "efectivo", "estado": "anulado",
                      "creado_en": "2026-11-11T12:00:00+00:00", "id_empresa": JSU},   # anulado
                     {"id_resumen_mensual": 61, "monto": "800.00", "medio_pago": "efectivo", "estado": "vigente",
                      "creado_en": "2026-11-11T12:00:00+00:00", "id_empresa": JSU}],  # factura de otra alumna
            "resumen_mensual": [{**factura(9, "2026-10-10", pago="pagado"), "id": 91},
                                {**factura(8, "2026-09-10", pago="pagado"), "id": 81},
                                {**factura(7, "2026-08-10"), "id": 71},
                                {**factura(6, "2026-07-10", pago="pagado", alumna_id="otra"), "id": 61}],
        })
        n = [x for x in de(alumna(), base)["novedades"] if x["titulo"].startswith("Pago registrado")]
        self.assertEqual(len(n), 1)
        self.assertIn("setiembre", n[0]["titulo"])
        self.assertIn("Mercado Pago", n[0]["detalle"])

    def test_videos_nuevos_y_por_vencer_sin_los_ya_iniciados(self):
        base = BaseFalsa(rpcs={"videos_disponibles": [video(1, 20 * 3600), video(2, 2 * 3600), video(3, 10 * 3600),
                                                      video(4, 3600)],
                               "novedades_videos_iniciados": [3, 4]})
        n = de(alumna(), base)["novedades"]
        por_vencer = [x for x in n if "por dejar" in x["titulo"]]
        nuevos = [x for x in n if "Nuevo video" in x["titulo"]]
        self.assertEqual(len(por_vencer), 1)
        self.assertEqual(por_vencer[0]["enlace"], "/videos/2")
        self.assertIn("12:00", por_vencer[0]["detalle"])  # 10:00 + 2 h
        self.assertEqual((len(nuevos), nuevos[0]["enlace"]), (1, "/videos/1"))

    def test_plazo_de_video_configurable(self):
        base = BaseFalsa(rpcs={"videos_disponibles": [video(1, 5 * 3600)], "novedades_parametros": {"video_aviso_horas": 6}})
        self.assertIn("por dejar", de(alumna(), base)["novedades"][0]["titulo"])

    def test_las_diez_juntas_y_el_orden_por_urgencia(self):
        base = BaseFalsa({
            "listadeespera": [{"id_clase": 9, "id_alumna": "a", "estado": "ofrecido", "id_empresa": JSU,
                               "oferta_vence_en": "2026-11-11T13:25:00+00:00"}],
            "clase": [clase(9, "2026-11-11", hora="18:00:00")],
            "reserva": [reserva(clase(1, "2026-11-11", hora="10:30:00")),
                        reserva(clase(2, "2026-11-12"), reservada="2026-11-11T12:30:00+00:00"),
                        {"id_alumna": "a", "estado": "cancelada", "id_bloqueo": 4, "id_empresa": JSU,
                         "clase": clase(5, "2026-11-13")}],
            "resumen_mensual": [factura(9, "2026-11-01"), factura(8, "2026-11-12"), factura(7, "2026-11-30"),
                                {**factura(6, "2026-07-10", pago="pagado"), "id": 61}],
            "pago": [{"id_resumen_mensual": 61, "monto": "800.00", "medio_pago": "efectivo", "estado": "vigente",
                      "creado_en": "2026-11-11T12:00:00+00:00", "id_empresa": JSU}],
        }, {"videos_disponibles": [video(1, 20 * 3600), video(2, 3600)]})
        n = de(alumna(), base)["novedades"]
        self.assertEqual(len(n), 10)
        niveles = [x["nivel"] for x in n]
        self.assertEqual(niveles, sorted(niveles, key=novedades.ORDEN.get))
        self.assertEqual(novedades.contador(n), {"total": 10, "urgentes": 4})

    def test_aislamiento_en_los_dos_sentidos(self):
        """La alumna compartida: con Just Show Up no ve nada de Pilates Centro y con Pilates Centro nada de Just Show Up."""
        sigla = {JSU: "JSU", PIL: "PIL"}

        def datos(empresa):
            tipo = f"Clase{sigla[empresa]}"
            return {
                "reserva": [reserva(clase(10 + empresa, "2026-11-11", hora="10:30:00", empresa=empresa, tipo=tipo),
                                    empresa=empresa),
                            reserva(clase(20 + empresa, "2026-11-12", empresa=empresa, tipo=tipo),
                                    reservada="2026-11-11T12:30:00+00:00", empresa=empresa)],
                "resumen_mensual": [{**factura(9, "2026-11-12", empresa=empresa), "numero": f"{sigla[empresa]}-2026-09-0001"},
                                    {**factura(6, "2026-07-10", empresa=empresa, pago="pagado"), "id": 600 + empresa,
                                     "numero": f"{sigla[empresa]}-2026-06-0001"}],
                "pago": [{"id_resumen_mensual": 600 + empresa, "monto": "800.00", "medio_pago": "efectivo",
                          "estado": "vigente", "creado_en": "2026-11-11T12:00:00+00:00", "id_empresa": empresa}],
            }
        jsu, pil = datos(JSU), datos(PIL)
        base = BaseFalsa({t: jsu[t] + pil[t] for t in jsu})
        for empresa in (JSU, PIL):
            with self.subTest(empresa=empresa):
                n = de(alumna(empresa), base)["novedades"]
                self.assertEqual(len(n), 4)
                otra = sigla[PIL if empresa == JSU else JSU]
                texto = " ".join(x["titulo"] + x["detalle"] + " ".join(x["lineas"]) for x in n)
                self.assertIn(sigla[empresa], texto)
                self.assertNotIn(otra, texto)


class Docentes(unittest.TestCase):
    PEND = {"asistencias_pendientes_resumen": {"clases": 2, "asistencias": 5, "ultima_fecha": "2026-11-10"}}

    def test_profesora_ve_sus_asistencias_pendientes(self):
        base = BaseFalsa({"resumen_mensual": [factura(10, "2026-11-01", estado="borrador")]}, self.PEND)
        n = de(profesora(), base)["novedades"]
        self.assertEqual(len(n), 1)  # no ve facturación
        self.assertEqual(n[0]["enlace"], "/profesora/clases?mes=2026-11&filtro=pendientes#terminadas")
        self.assertIn("2 clases terminadas", n[0]["titulo"])

    def test_administradora_ve_asistencias_borradores_y_vencidas(self):
        base = BaseFalsa({"resumen_mensual": [
            factura(10, None, estado="borrador", nombre="Marta"),
            factura(10, "2026-11-10", nombre="Agustina"), factura(10, "2026-11-10", nombre="Beatriz"),
            factura(10, "2026-11-14", nombre="Carla"),  # todavía no vence
            factura(10, "2026-11-10", pago="pagado", nombre="Dora"),
            factura(10, "2026-11-01", empresa=PIL, nombre="Inés"),  # otra empresa
        ]}, self.PEND)
        n = de(administradora(), base)["novedades"]
        self.assertEqual([x["nivel"] for x in n], [novedades.URGENTE, novedades.PENDIENTE, novedades.PENDIENTE])
        self.assertIn("2 facturas de octubre 2026 vencidas", n[0]["titulo"])
        self.assertEqual(n[0]["lineas"], ["Agustina Gómez", "Beatriz Gómez"])
        self.assertTrue(any("1 factura de octubre 2026 en borrador" in x["titulo"] for x in n))
        self.assertTrue(all("Inés" not in " ".join(x["lineas"]) for x in n))

    def test_sin_pendientes_no_hay_novedades(self):
        self.assertEqual(de(administradora(), BaseFalsa(rpcs=SIN_PENDIENTES))["novedades"], [])


class Superadministradora(unittest.TestCase):
    def test_invitaciones_pendientes_de_administradoras_activas(self):
        base = BaseFalsa(rpcs={
            "resumen_empresas": [{"id": JSU, "nombre": "Just Show Up"}, {"id": PIL, "nombre": "Pilates Centro"}],
            "administradoras_empresa": lambda p: {
                JSU: [{"id": "x1", "nombre": "Laura", "apellido": "P", "activa": True}],
                PIL: [{"id": "x2", "nombre": "Lucía", "apellido": "B", "activa": True},
                      {"id": "x3", "nombre": "Vieja", "apellido": "Z", "activa": False}],
            }[p["p_id_empresa"]],
        })
        n = de(superadmin(), base, invitacion=lambda i: i in ("x2", "x3"))["novedades"]
        self.assertEqual(len(n), 1)
        self.assertEqual((n[0]["enlace"], n[0]["lineas"]), (f"/admin/empresas/{PIL}", ["Lucía B"]))


class SoloLecturaYErrores(unittest.TestCase):
    def test_nunca_llama_funciones_que_escriben(self):
        base = BaseFalsa({"resumen_mensual": [factura(10, "2026-11-10")]},
                         {"asistencias_pendientes_resumen": {"clases": 1, "asistencias": 1, "ultima_fecha": "2026-11-10"}})
        for u in (alumna(), profesora(), administradora()):
            de(u, base)
        self.assertEqual(base.escrituras, [])
        self.assertLessEqual(set(base.rpcs_llamadas), RPCS_SOLO_LECTURA)

    def test_un_bloque_que_falla_no_tapa_los_demas(self):
        base = BaseFalsa({"resumen_mensual": [factura(10, "2026-11-10")]})  # sin la función de asistencias
        with self.assertLogs(level="ERROR"):
            r = de(administradora(), base)
        self.assertFalse(r["completas"])
        self.assertEqual(len(r["novedades"]), 1)

    def test_contador(self):
        n = [novedades._novedad(novedades.URGENTE, "a", "", "/", "x"), novedades._novedad(novedades.AVISO, "b", "", "/", "x")]
        self.assertEqual(novedades.contador(n), {"total": 2, "urgentes": 1})


class Rutas(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(main.app)

    def con(self, usuario, base):
        return [mock.patch.object(auth, "_usuario_cacheado", return_value=usuario),
                mock.patch.object(main, "_sb", return_value=base)]

    def pedir(self, usuario, base, ruta):
        parches = self.con(usuario, base)
        for p in parches:
            p.start()
        try:
            return self.cliente.get(ruta, follow_redirects=False)
        finally:
            for p in parches:
                p.stop()

    def test_sin_sesion_va_al_login(self):
        r = self.cliente.get("/novedades", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])
        r = self.cliente.get("/novedades/contador", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_pantalla_de_la_alumna(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-10-10")]})
        with mock.patch.object(clases, "ahora", return_value=AHORA):
            r = self.pedir(alumna(), base, "/novedades")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Factura de setiembre 2026 vencida", r.text)
        self.assertIn('href="/mis-facturas"', r.text)
        self.assertIn('class="campana activo"', r.text)

    def test_pantalla_vacia(self):
        r = self.pedir(administradora(), BaseFalsa(rpcs=SIN_PENDIENTES), "/novedades")
        self.assertIn("No tenés novedades pendientes", r.text)

    def test_contador_json(self):
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-10-10"), factura(10, "2026-12-10")]})
        with mock.patch.object(clases, "ahora", return_value=AHORA):
            r = self.pedir(alumna(), base, "/novedades/contador")
        self.assertEqual(r.json(), {"total": 2, "urgentes": 1})

    def test_campana_en_el_menu_de_todos_los_roles(self):
        for u in (alumna(), profesora(), administradora(), superadmin()):
            with self.subTest(u=u.mail):
                request = SimpleNamespace(url=SimpleNamespace(path="/perfil"))
                html = main.templates.get_template("_menu.html").render(request=request, usuario=u)
                self.assertIn('href="/novedades"', html)

    def test_sin_empresa_no_hay_campana(self):
        u = Usuario(id="z", mail="z@prueba.invalid", nombre="Z", apellido="Z", rol="alumna", token="t", con_membresias=True)
        request = SimpleNamespace(url=SimpleNamespace(path="/perfil"))
        self.assertNotIn('class="campana', main.templates.get_template("_menu.html").render(request=request, usuario=u))
        r = self.pedir(u, BaseFalsa(), "/novedades/contador")
        self.assertEqual(r.json(), {"total": 0, "urgentes": 0})


class ScriptPruebaReal(unittest.TestCase):
    """scripts/prueba_novedades_real.py: solo servidor local, no sigue la redirección del login y lee la pantalla."""

    def setUp(self):
        import importlib.util, os
        ruta = os.path.join(os.path.dirname(__file__), "..", "scripts", "prueba_novedades_real.py")
        spec = importlib.util.spec_from_file_location("prueba_novedades_real", ruta)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)

    def test_solo_servidor_local(self):
        self.assertEqual(self.m.base_local("http://localhost:8000"), "http://localhost:8000")
        for url in ("https://just-show-up-theta.vercel.app", "http://just-show-up-amber.vercel.app", "https://localhost:8000"):
            with self.subTest(url=url), self.assertRaises(SystemExit):
                self.m.base_local(url)

    def test_no_sigue_la_redireccion_ni_pide_otras_pantallas(self):
        import httpx
        pagina = main.templates.get_template("novedades.html").render(
            request=SimpleNamespace(url=SimpleNamespace(path="/novedades")), usuario=alumna(), aviso="", aviso_ok=False,
            novedades=[novedades._novedad(novedades.URGENTE, "Factura de octubre 2026 vencida", "x", "/mis-facturas", "Ver"),
                       novedades._novedad(novedades.AVISO, "La profesora canceló 1 clase", "y", "/", "Ver", ["a"])])
        vistos = []

        def servidor(req):
            vistos.append((req.method, req.url.path, req.headers.get("cookie", "")))
            if req.url.path == "/login":
                return httpx.Response(303, headers=[("location", "/"), ("set-cookie", "sb_acceso=A; Path=/; HttpOnly"),
                                                    ("set-cookie", "sb_refresco=R; Path=/; HttpOnly")])
            if req.url.path == "/novedades":
                return httpx.Response(200, text=pagina)
            if req.url.path == "/novedades/contador":
                return httpx.Response(200, json={"total": 2, "urgentes": 1})
            if req.url.path == "/logout":
                return httpx.Response(303, headers=[("location", "/login"), ("set-cookie", 'sb_acceso=""; Max-Age=0; Path=/')])
            return httpx.Response(500)

        c = self.m.Cliente("http://localhost:8000", httpx.MockTransport(servidor))
        self.assertTrue(c.entrar("Just Show Up", "alumna@prueba.invalid", "x"))
        estado, html_ = c.novedades()
        self.assertEqual(self.m.titulos(html_), [("Urgente", "Factura de octubre 2026 vencida"),
                                                 ("Aviso", "La profesora canceló 1 clase")])
        self.assertEqual(c.contador(), {"total": 2, "urgentes": 1})
        c.salir()
        self.assertEqual([p for _, p, _ in vistos], ["/login", "/novedades", "/novedades/contador", "/logout"])
        self.assertIn("sb_acceso=A", vistos[1][2])
        with self.assertRaises(RuntimeError):
            c._pedir("GET", "/")


if __name__ == "__main__":
    unittest.main()
