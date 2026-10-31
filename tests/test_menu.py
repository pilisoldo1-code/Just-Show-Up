"""Header responsive: secciones del menú principal y del perfil según el rol, sección activa,
botón de menú para celular y pantalla de Horarios (sin botón duplicado, tabla con scroll propio).

Uso: python -m unittest tests.test_menu -v
"""
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, clases, main
from app.auth import Usuario

PROFESORA = Usuario(id="p", mail="profe@prueba.invalid", nombre="Profe", apellido="Prueba", rol="profesora", token="t")
ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")

# PROFESORA (rol «profesora» sin membresías) es administradora y profesora: panel de administración.
SECCIONES_PROFESORA = [
    ("/administracion", "Inicio"), ("/profesora/horarios", "Horarios"), ("/profesora/tipos-clase", "Tipos de clase"),
    ("/profesora/planificacion", "Planificación"), ("/profesora/clases", "Asistencias"), ("/profesora/alumnas", "Alumnas"),
    ("/profesora/profesoras", "Profesoras"), ("/profesora/resumenes", "Facturación"), ("/profesora/estadisticas", "Estadísticas"),
]
SECCIONES_ALUMNA = [
    ("/", "Clases"), ("/mis-inscripciones", "Mis inscripciones"),
    ("/mi-historial", "Mi historial"), ("/mis-facturas", "Mis facturas"),
]


def menu_de(usuario, ruta):
    """Renderiza solo el header, como lo vería esa usuaria en esa ruta."""
    request = SimpleNamespace(url=SimpleNamespace(path=ruta))
    return main.templates.get_template("_menu.html").render(request=request, usuario=usuario)


def bloque(html, clase):
    """Contenido de <nav class="menu-links"> o <div class="cuenta-opciones">."""
    etiqueta = "nav" if clase == "menu-links" else "div"
    m = re.search(rf'<{etiqueta}[^>]*class="{clase}"[^>]*>(.*?)</{etiqueta}>', html, re.S)
    return m.group(1)


def enlaces(html):
    return re.findall(r'<a href="([^"]+)" class="[^"]*"[^>]*>([^<]+)</a>', html)


def activos(html):
    return re.findall(r'<a href="([^"]+)" class="activo"', html)


class MenuPrincipal(unittest.TestCase):
    def test_administradora_tiene_sus_nueve_secciones_en_orden(self):
        self.assertEqual(enlaces(bloque(menu_de(PROFESORA, "/profesora/horarios"), "menu-links")), SECCIONES_PROFESORA)

    def test_alumna_tiene_sus_cuatro_secciones_en_orden(self):
        self.assertEqual(enlaces(bloque(menu_de(ALUMNA, "/"), "menu-links")), SECCIONES_ALUMNA)

    def test_alumna_no_ve_secciones_de_la_profesora(self):
        self.assertNotIn("/profesora/", menu_de(ALUMNA, "/"))

    def test_profesora_no_ve_secciones_de_la_alumna(self):
        menu = bloque(menu_de(PROFESORA, "/profesora/horarios"), "menu-links")
        for href, _ in SECCIONES_ALUMNA:
            self.assertNotIn(f'href="{href}"', menu)


class SeccionActiva(unittest.TestCase):
    def test_profesora_una_sola_activa_por_seccion(self):
        for href, _ in SECCIONES_PROFESORA:
            with self.subTest(href=href):
                self.assertEqual(activos(menu_de(PROFESORA, href)), [href])

    def test_profesora_se_mantiene_en_pantallas_internas(self):
        casos = {
            "/profesora/horarios/3/editar": "/profesora/horarios",
            "/profesora/tipos-clase/2/editar": "/profesora/tipos-clase",
            "/profesora/clases/15": "/profesora/clases",
            "/profesora/resumenes/7": "/profesora/resumenes",
        }
        for ruta, esperado in casos.items():
            with self.subTest(ruta=ruta):
                self.assertEqual(activos(menu_de(PROFESORA, ruta)), [esperado])

    def test_alumna_una_sola_activa_por_seccion(self):
        for href, _ in SECCIONES_ALUMNA:
            with self.subTest(href=href):
                self.assertEqual(activos(menu_de(ALUMNA, href)), [href])

    def test_alumna_clases_activa_tambien_en_el_dia(self):
        self.assertEqual(activos(menu_de(ALUMNA, "/dia/2026-06-15")), ["/"])

    def test_perfil_no_resalta_ninguna_seccion(self):
        self.assertEqual(activos(menu_de(ALUMNA, "/perfil")), [])
        self.assertEqual(activos(menu_de(PROFESORA, "/perfil")), [])

    def test_activa_marcada_para_lectores_de_pantalla(self):
        html = menu_de(ALUMNA, "/mi-historial")
        self.assertIn('href="/mi-historial" class="activo" aria-current="page"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)


class MenuPerfil(unittest.TestCase):
    def test_alumna_solo_mi_perfil_y_cerrar_sesion(self):
        cuenta = bloque(menu_de(ALUMNA, "/"), "cuenta-opciones")
        self.assertEqual(re.findall(r'<a href="([^"]+)"', cuenta), ["/perfil"])
        self.assertIn('action="/logout"', cuenta)
        self.assertIn("Cerrar sesión", cuenta)

    def test_alumna_sin_secciones_duplicadas(self):
        html = menu_de(ALUMNA, "/")
        for href in ("/mis-inscripciones", "/mi-historial", "/mis-facturas"):
            self.assertEqual(html.count(f'href="{href}"'), 1, href)

    def test_profesora_solo_mi_perfil_y_cerrar_sesion(self):
        cuenta = bloque(menu_de(PROFESORA, "/profesora/horarios"), "cuenta-opciones")
        self.assertEqual(re.findall(r'<a href="([^"]+)"', cuenta), ["/perfil"])
        self.assertIn('action="/logout"', cuenta)


class HeaderCelular(unittest.TestCase):
    def test_boton_de_menu_accesible(self):
        html = menu_de(PROFESORA, "/profesora/horarios")
        self.assertIn('class="menu-boton"', html)
        self.assertIn('aria-controls="menu-principal"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('id="menu-principal"', html)

    def test_marca_lleva_al_inicio_de_cada_rol(self):
        self.assertIn('<a class="menu-marca" href="/administracion">Just Show Up</a>',
                      menu_de(PROFESORA, "/profesora/alumnas"))
        self.assertIn('<a class="menu-marca" href="/">Just Show Up</a>', menu_de(ALUMNA, "/mis-facturas"))


class Pantallas(unittest.TestCase):
    """Rutas reales con la base simulada: el header queda fuera de <main> y las pantallas cargan."""

    def setUp(self):
        self.client = TestClient(main.app)
        perfil_vacio = mock.MagicMock()
        perfil_vacio.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        for objeto, nombre, valor in ((main, "_sb", mock.MagicMock()), (auth, "cliente_con_token", perfil_vacio)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_header_fuera_de_main(self):
        self.como(PROFESORA)
        html = self.client.get("/perfil").text
        self.assertLess(html.index('<header class="menu">'), html.index("<main"))
        self.assertEqual(html.count('<header class="menu">'), 1)

    def test_pantallas_de_la_alumna_cargan_con_su_menu(self):
        self.como(ALUMNA)
        with mock.patch.object(clases, "mis_inscripciones",
                               return_value={"proximas": [], "en_espera": [], "historial": [], "ofertas": [],
                                             "canceladas_profesora": []}), \
             mock.patch.object(main.facturas, "mis_facturas", return_value=[]):
            for ruta in ("/mis-inscripciones", "/mi-historial", "/mis-facturas", "/perfil"):
                with self.subTest(ruta=ruta):
                    r = self.client.get(ruta)
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(enlaces(bloque(r.text, "menu-links")), SECCIONES_ALUMNA)
                    self.assertEqual(activos(r.text), [ruta] if ruta != "/perfil" else [])

    def test_horarios_sin_boton_duplicado_y_tabla_en_tarjetas(self):
        self.como(PROFESORA)
        sb = main._sb.return_value
        sb.table.return_value.select.return_value.execute.return_value.data = []
        html = self.client.get("/profesora/horarios").text
        contenido = html[html.index("<main"):]
        self.assertNotIn('href="/profesora/tipos-clase"', contenido)
        self.assertIn('href="/profesora/horarios/nuevo">+ Nuevo horario</a>', contenido)
        self.assertIn('href="/profesora/tipos-clase"', bloque(html, "menu-links"))
        # en celular cada horario pasa a ser una tarjeta (sin desplazamiento horizontal)
        self.assertRegex(contenido, r'<div class="tabla-scroll">\s*<table class="horario-table tabla-tarjetas">')


if __name__ == "__main__":
    unittest.main()
