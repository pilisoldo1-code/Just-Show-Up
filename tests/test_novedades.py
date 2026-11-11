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


class BaseFalsa:
    def __init__(self, tablas=None, rpcs=None):
        self.tablas, self.rpcs, self.escrituras, self.rpcs_llamadas = tablas or {}, rpcs or {}, [], []

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
        base = BaseFalsa({"resumen_mensual": [factura(9, "2026-10-10"), factura(10, "2026-11-14"),
                                              factura(8, "2026-09-10", pago="pagado")]})
        n = de(alumna(), base)["novedades"]
        self.assertEqual([x["nivel"] for x in n], [novedades.URGENTE, novedades.PENDIENTE])
        self.assertIn("setiembre", n[0]["titulo"].lower())
        self.assertIn("14/11/2026", n[1]["detalle"])

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
        self.assertEqual(base.rpcs_llamadas, [])


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
        self.assertEqual(set(base.rpcs_llamadas), {"asistencias_pendientes_resumen"})

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
