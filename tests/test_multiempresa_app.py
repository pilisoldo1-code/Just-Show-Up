"""Multiempresa en el servidor: inicio de sesión con el nombre del lugar, roles por empresa (y combinados),
menú, alta de personas y panel de la superadministradora. Sin base (se mockea Supabase); las reglas de la base se prueban en tests/base_local.

Uso: python -m unittest tests.test_multiempresa_app -v
"""

import unittest
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main
from app.auth import Usuario
from app.clases import TZ

def persona(**cambios):
    datos = dict(id="u", mail="u@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t",
                 con_membresias=True, empresa_id=1, empresa_nombre="Just Show Up", empresa_codigo="JSU")
    datos.update(cambios)
    return Usuario(**datos)


ADMIN = persona(id="ad", rol="profesora", nombre="Ada", es_admin=True, es_docente=True)
PROFE = persona(id="pr", rol="profesora", nombre="Paula", es_docente=True)  # profesora, no administradora
ALUMNA_DOS = persona(id="al", es_alumna=True)  # alumna en dos empresas: la sesión solo conoce la suya
PROFE_ALUMNA = persona(id="pa", rol="profesora", es_docente=True, es_alumna=True)
ADMIN_ALUMNA = persona(id="aa", rol="profesora", es_admin=True, es_docente=True, es_alumna=True)
SIN_ELEGIR = persona(id="al", es_alumna=False, empresa_id=None, empresa_nombre="")
SUPER = persona(id="su", rol="alumna", nombre="Sara", es_superadmin=True, empresa_id=None, empresa_nombre="")
AHORA = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.sb = mock.MagicMock()
        for objeto, nombre, valor in ((main, "_sb", self.sb), (main.clases, "ahora", AHORA)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def get(self, url):
        return self.client.get(url, follow_redirects=False)

    def post(self, url, datos):
        return self.client.post(url, data=datos, follow_redirects=False)

    def rpc(self, valor):
        self.sb.rpc.return_value.execute.return_value.data = valor


class Roles(unittest.TestCase):
    def test_compatibilidad_sin_membresias(self):
        p = Usuario(id="p", mail="m", nombre="n", apellido="a", rol="profesora")
        self.assertTrue(p.es_admin and p.es_profesora and not p.es_alumna)
        a = Usuario(id="a", mail="m", nombre="n", apellido="a", rol="alumna")
        self.assertTrue(a.es_alumna and not a.es_profesora and not a.es_admin)

    def test_destino(self):
        self.assertEqual(auth.destino(SUPER), "/admin/empresas")
        self.assertTrue(auth.destino(SIN_ELEGIR).startswith("/login?"))
        self.assertEqual(auth.destino(ADMIN), "/administracion")
        self.assertEqual(auth.destino(ADMIN_ALUMNA), "/administracion")  # administración tiene prioridad
        self.assertEqual(auth.destino(PROFE), "/profesora/horarios")
        self.assertEqual(auth.destino(PROFE_ALUMNA), "/profesora/horarios")  # panel de profesora primero
        self.assertEqual(auth.destino(ALUMNA_DOS), "/")
        self.assertFalse(hasattr(ALUMNA_DOS, "empresas"))  # la sesión no lleva la lista de empresas de la cuenta

    def test_rol_de_profiles_no_cuenta_con_membresias(self):
        # con membresías, el rol viejo de profiles no da permisos: solo los de la empresa activa
        u = persona(rol="profesora")
        self.assertFalse(u.es_admin or u.es_profesora or u.es_alumna)


class Permisos(Base):
    SOLO_ADMIN_GET = ["/profesora/resumenes", "/profesora/tipos-clase", "/profesora/alumnas/nueva"]
    SOLO_ADMIN_POST = {
        "/profesora/alumnas": {"nombre": "Eva", "apellido": "Uno", "mail": "eva@prueba.invalid"},
        "/profesora/resumenes/1/confirmar": {"version": 1},
        "/profesora/resumenes/1/pagar": {"monto": "1", "medio_pago": "efectivo", "fecha": "2026-11-01"},
        "/profesora/alumnas/x/cedula": {"cedula": ""},
        "/profesora/alumnas/x/reiniciar-contrasena": {},
    }

    def test_profesora_no_admin_no_entra_a_lo_de_administradora(self):
        self.como(PROFE)
        for url in self.SOLO_ADMIN_GET:
            r = self.get(url)
            self.assertEqual(r.status_code, 303, url)
            self.assertTrue(r.headers["location"].startswith("/profesora/horarios?"), url)
        for url, datos in self.SOLO_ADMIN_POST.items():
            r = self.post(url, datos)
            self.assertEqual(r.status_code, 303, url)
            self.assertTrue(r.headers["location"].startswith("/profesora/horarios?"), url)
        self.sb.rpc.assert_not_called()

    def test_alumna_y_superadmin_no_entran_a_profesora(self):
        self.como(ALUMNA_DOS)
        self.assertTrue(self.get("/profesora/horarios").headers["location"].startswith("/?"))
        self.como(SUPER)
        self.assertEqual(self.get("/profesora/horarios").headers["location"], "/admin/empresas")
        self.assertEqual(self.get("/").headers["location"], "/admin/empresas")

    def test_sin_empresa_vuelve_a_iniciar_sesion(self):
        self.como(SIN_ELEGIR)
        with mock.patch.object(auth, "cerrar_sesion") as cs:
            for url in ("/", "/mis-facturas", "/profesora/horarios", "/administracion"):
                r = self.get(url)
                self.assertTrue(r.headers["location"].startswith("/login?"), url)
                self.assertIn("sb_acceso=", r.headers.get("set-cookie", ""), url)  # se borran las cookies
        self.assertTrue(cs.called)
        self.assertEqual(self.get("/empresa/elegir").status_code, 404)  # ya no existe el selector

    def test_panel_de_administracion_solo_administradora(self):
        self.como(PROFE)
        self.assertTrue(self.get("/administracion").headers["location"].startswith("/profesora/horarios?"))
        self.como(ALUMNA_DOS)
        self.assertTrue(self.get("/administracion").headers["location"].startswith("/?"))
        self.como(ADMIN)
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        r = self.get("/administracion")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Panel de administración", r.text)
        self.assertIn("Just Show Up", r.text)
        for texto in ("Alumnas activas", "Profesoras activas", "Clases semanales activas"):
            self.assertIn(texto, r.text)
        for texto in ("Clases hoy", "clases hoy", "Facturas por cobrar", "facturas por cobrar", "panel-seccion"):
            self.assertNotIn(texto, r.text)  # sin indicadores viejos ni tarjetas que repiten el menú

    def test_indicadores_del_panel(self):
        membresias = [
            {"id_usuario": "a1", "es_alumna": True, "es_profesora": False, "activa": True},
            {"id_usuario": "a2", "es_alumna": True, "es_profesora": False, "activa": False},  # inactiva: no cuenta
            {"id_usuario": "ap", "es_alumna": True, "es_profesora": True, "activa": True},   # dos roles: una vez en cada número
            {"id_usuario": "ad", "es_alumna": False, "es_profesora": True, "activa": True},  # administradora y profesora
            {"id_usuario": "ad2", "es_alumna": False, "es_profesora": False, "activa": True},  # solo administradora
        ]
        horarios = [{"id": 1, "activo": True}, {"id": 2, "activo": True}, {"id": 3, "activo": False}]
        self.assertEqual(main.indicadores_admin(membresias, horarios), {"alumnas": 2, "profesoras": 2, "clases_semanales": 2})
        self.assertEqual(main.indicadores_admin([], []), {"alumnas": 0, "profesoras": 0, "clases_semanales": 0})

    def test_indicadores_filtran_por_la_empresa_activa(self):
        self.como(ADMIN)
        self.get("/administracion")
        filtros = [c.args for c in self.sb.table.return_value.select.return_value.eq.call_args_list]
        self.assertIn(("id_empresa", ADMIN.empresa_id), filtros)

    def test_un_solo_menu_por_rol(self):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        # un solo rol por empresa: nadie tiene enlaces para pasar a otro panel, y lo de otro rol no se abre
        for u, destino in ((PROFE, "/profesora/horarios"), (ADMIN, "/administracion")):
            self.como(u)
            self.assertEqual(self.get("/mis-facturas").headers["location"], destino)
            r = self.get("/profesora/alumnas")
            for texto in ("menu-otro-rol", "Mis clases", "Reservar clases", "Panel de"):
                self.assertNotIn(texto, r.text)
        self.como(ALUMNA_DOS)
        with mock.patch.object(main.facturas, "facturas_alumna", return_value=[], create=True):
            r = self.get("/mi-historial")
        for texto in ("menu-otro-rol", "Panel de", "/profesora/"):
            self.assertNotIn(texto, r.text)
        # datos anteriores a la migración (una sesión armada con dos roles): igual ve un solo menú, sin enlaces
        self.como(PROFE_ALUMNA)
        self.assertNotIn("menu-otro-rol", self.get("/profesora/alumnas").text)

    def test_panel_de_empresas_solo_superadmin(self):
        for u in (ADMIN, PROFE, ALUMNA_DOS):
            self.como(u)
            self.assertEqual(self.get("/admin/empresas").status_code, 303)
            self.assertEqual(self.post("/admin/empresas", {"nombre": "X", "codigo": "XX"}).status_code, 303)
        self.sb.rpc.assert_not_called()


class Menu(Base):
    def test_admin_ve_todo(self):
        self.como(ADMIN)
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        r = self.get("/profesora/alumnas")
        for texto in ("Tipos de clase", "Facturación", "Just Show Up", "+ Nueva alumna", ">Profesoras</a>"):
            self.assertIn(texto, r.text)
        self.assertNotIn("Cambiar de empresa", r.text)

    def test_profesora_no_ve_facturacion_ni_tipos(self):
        self.como(PROFE)
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        r = self.get("/profesora/alumnas")
        self.assertEqual(r.status_code, 200)
        for texto in ("Tipos de clase", "Facturación", "Nueva persona"):
            self.assertNotIn(texto, r.text)
        for texto in (">Mis horarios</a>", "Asistencias", "Planificación", "Estadísticas"):
            self.assertIn(texto, r.text)

    def test_el_menu_solo_muestra_la_empresa_de_la_sesion(self):
        self.como(ALUMNA_DOS)
        with mock.patch.object(main.facturas, "facturas_alumna", return_value=[], create=True):
            r = self.get("/mi-historial")
        self.assertIn("Just Show Up", r.text)
        for texto in ("Pilates", "Cambiar de empresa", "/empresa/elegir"):
            self.assertNotIn(texto, r.text)

    RESUMEN = [{"id": 1, "nombre": "Just Show Up", "codigo": "JSU", "activa": True, "profesoras": 1, "alumnas": 9,
                "administradoras": "Profe Prueba <profesora@prueba.invalid>"}]
    ADMINS = [{"id": "u1", "nombre": "Profe", "apellido": "Prueba", "mail": "profesora@prueba.invalid", "activa": True,
               "pendientes": ["horarios"]},
              {"id": "u2", "nombre": "Vieja", "apellido": "Admin", "mail": "vieja@prueba.invalid", "activa": False, "pendientes": []}]

    def responder(self):
        r = {"resumen_empresas": self.RESUMEN, "administradoras_empresa": self.ADMINS}
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(**{"execute.return_value.data": r[nombre]})

    def test_superadmin_solo_ve_empresas(self):
        self.como(SUPER)
        self.responder()
        r = self.get("/admin/empresas")
        self.assertEqual(r.status_code, 200)
        # tarjetas con datos generales y cantidades; nada de las administradoras ni sus acciones
        for texto in ("Just Show Up", "JSU", "Gestionar empresa", 'href="/admin/empresas/1"', "+ Nueva empresa",
                      "<dt>Profesoras</dt><dd>1</dd>", "<dt>Alumnas</dt><dd>9</dd>", "<dt>Administradoras</dt><dd>1</dd>"):
            self.assertIn(texto, r.text)
        for texto in ("profesora@prueba.invalid", "Pasar a profesora", "Reiniciar contraseña", "También da clases",
                      ">Horarios</a>", "Facturación", "Mis facturas", "<table"):
            self.assertNotIn(texto, r.text)

    def test_gestionar_empresa(self):
        self.como(SUPER)
        self.responder()
        r = self.get("/admin/empresas/1")
        self.assertEqual(r.status_code, 200)
        for texto in ("← Volver a empresas", "Datos generales", "Editar datos", "Desactivar empresa", "Asignar administradora",
                      "profesora@prueba.invalid", "Pasar a profesora", ">Desactivar</button>", "Reiniciar contraseña", "Reactivar",
                      'class="menu-acciones"', "<dt>Administradoras</dt><dd>1</dd>"):
            self.assertIn(texto, r.text)
        self.assertEqual(r.text.count("Pasar a profesora"), 1)  # solo la activa; la inactiva tiene «Reactivar»
        self.assertNotIn("También da clases", r.text)
        self.assertIn("<dt>Estado</dt><dd>Activa</dd>", r.text)  # solo el estado, sin explicación
        self.assertNotIn("mailto:", r.text)  # el correo no se repite en el menú de acciones
        self.assertIn('<button type="button" class="correo" title="profesora@prueba.invalid"', r.text)
        # única administradora activa: no se le puede quitar el rol (opciones deshabilitadas, con el motivo)
        self.assertIn('<button type="submit" disabled>Pasar a profesora</button>', r.text)
        self.assertIn('<button type="submit" class="accion-peligro" disabled>Desactivar</button>', r.text)
        self.assertIn("Es la única administradora activa", r.text)
        # la baja siempre pasa por la vista previa (cancela sus clases próximas): ya no se bloquea por sus horarios
        self.assertIn('action="/admin/empresas/1/administradoras/u1/baja" method="get"', r.text)
        self.assertNotIn("tiene horarios activos", r.text)
        self.assertIn("en todas las empresas donde usa Just Show Up", r.text)
        self.RESUMEN = [{**self.RESUMEN[0], "activa": False}]
        self.responder()
        r = self.get("/admin/empresas/1")
        self.assertIn("<dt>Estado</dt><dd>Inactiva</dd>", r.text)
        self.assertIn("Reactivar empresa", r.text)

    def test_acciones_deshabilitadas_segun_el_caso(self):
        self.como(SUPER)
        self.ADMINS = [{**self.ADMINS[0]}, {**self.ADMINS[1], "activa": True, "pendientes": []}]
        self.responder()
        r = self.get("/admin/empresas/1")
        # dos activas: las dos pueden pasar a profesora o darse de baja (con vista previa, aunque tenga horarios)
        self.assertEqual(r.text.count('<button type="submit" >Pasar a profesora</button>'), 2)
        self.assertEqual(r.text.count('class="accion-peligro" >Desactivar</button>'), 2)
        self.assertNotIn("primero hay que resolver", r.text)
        self.assertNotIn("Es la única administradora activa", r.text)

    def test_empresa_inexistente_vuelve_al_listado(self):
        self.como(SUPER)
        self.responder()
        r = self.get("/admin/empresas/99")
        self.assertEqual(r.headers["location"].split("?")[0], "/admin/empresas")
        self.assertIn("ok=0", r.headers["location"])

    def test_acciones_de_una_empresa_vuelven_a_su_ficha(self):
        self.como(SUPER)
        self.rpc("ok")
        self.assertTrue(self.post("/admin/empresas/2/editar", {"nombre": "Pilates", "codigo": "PIL"})
                        .headers["location"].startswith("/admin/empresas/2?ok=1"))
        self.assertTrue(self.post("/admin/empresas/2/estado", {"activa": 0}).headers["location"].startswith("/admin/empresas/2?"))

    def test_otros_roles_no_entran_a_la_ficha(self):
        for u in (ADMIN, PROFE, ALUMNA_DOS):
            self.como(u)
            self.assertEqual(self.get("/admin/empresas/1").status_code, 303)
        self.sb.rpc.assert_not_called()

class ListaPersonas(Base):
    def preparar(self, membresias, perfiles):
        tabla = self.sb.table.return_value
        tabla.select.return_value.eq.return_value.execute.return_value.data = membresias  # administradora: todas
        tabla.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            m for m in membresias if m["activa"]]  # profesora: solo activas
        tabla.select.return_value.in_.return_value.execute.return_value.data = perfiles

    MEMB = [{"id_usuario": "a1", "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": True},
            {"id_usuario": "p1", "es_admin": False, "es_profesora": True, "es_alumna": False, "activa": True},
            {"id_usuario": "ap", "es_admin": True, "es_profesora": False, "es_alumna": False, "activa": True},
            {"id_usuario": "x1", "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": False}]
    PERFILES = [{"id": "a1", "nombre": "Eva", "apellido": "Uno", "mail": "e@x.uy"},
                {"id": "p1", "nombre": "Pia", "apellido": "Dos", "mail": "p@x.uy"},
                {"id": "ap", "nombre": "Ana", "apellido": "Tres", "mail": "a@x.uy"},
                {"id": "x1", "nombre": "Ines", "apellido": "Cero", "mail": "i@x.uy"}]

    def test_alumnas_solo_quien_tiene_el_rol_alumna(self):
        self.como(ADMIN)
        self.preparar(self.MEMB, self.PERFILES)
        r = self.get("/profesora/alumnas")
        self.assertIn("<h1>Alumnas</h1>", r.text)
        self.assertIn("+ Nueva alumna", r.text)
        for nombre in ("Eva", "Ines"):
            self.assertIn(nombre, r.text)
        for nombre in ("Pia", "Ana"):  # profesora y administradora: no son alumnas
            self.assertNotIn(nombre, r.text)
        self.assertIn("Inactiva", r.text)  # la administradora ve también las inactivas
        self.assertLess(r.text.index("Eva"), r.text.index("Ines"))  # activas primero

    def test_profesoras_incluye_administradoras_solo_para_consultar(self):
        self.como(ADMIN)
        self.preparar(self.MEMB, self.PERFILES)
        r = self.get("/profesora/profesoras")
        self.assertIn("<h1>Profesoras</h1>", r.text)
        self.assertIn("+ Nueva profesora", r.text)
        self.assertIn("<td>Profesora</td>", r.text)
        self.assertIn("<td>Administradora</td>", r.text)  # un solo rol
        self.assertIn("las otras administradoras solo se consultan", r.text)
        # el mismo botón para todas: lo que se puede hacer lo decide la ficha (y el servidor)
        self.assertIn('href="/profesora/profesoras/p1">Gestionar</a>', r.text)
        self.assertIn('href="/profesora/profesoras/ap">Gestionar</a>', r.text)
        self.assertNotIn(">Ver<", r.text)
        self.assertNotIn("Reiniciar contraseña", r.text)  # está en la ficha
        for nombre in ("Eva", "Ines"):
            self.assertNotIn(nombre, r.text)
        self.assertNotIn("Cédula", r.text)  # la cédula se ve en la ficha, no en la lista de profesoras

    def test_profesoras_es_solo_de_la_administradora(self):
        self.como(PROFE)
        self.assertEqual(self.get("/profesora/profesoras").status_code, 303)
        self.assertEqual(self.get("/profesora/profesoras/nueva").status_code, 303)

    def test_lista_de_alumnas_corta_y_ficha_aparte(self):
        self.como(ADMIN)
        self.preparar(self.MEMB, self.PERFILES)
        r = self.get("/profesora/alumnas")
        # escritorio: apellido, nombre, estado y «Gestionar alumna»; celular: filas compactas con «Gestionar»
        self.assertIn("<th>Apellido</th>", r.text)
        self.assertIn('href="/profesora/alumnas/a1">Gestionar alumna', r.text)
        self.assertIn('aria-label="Gestionar a Eva Uno">Gestionar</a>', r.text)
        for texto in ("Cédula", "Carné", "e@x.uy", "Reiniciar contraseña", "<th>Rol</th>"):
            self.assertNotIn(texto, r.text)  # todo eso está en la ficha
        self.sb.rpc.assert_not_called()  # la lista ya no pide datos personales
        self.assertEqual(self.sb.table.return_value.select.call_args_list[-1].args[0], main.COLUMNAS_ALUMNA)

    def test_profesora_no_pide_ni_ve_la_cedula(self):
        self.como(PROFE)
        self.preparar(self.MEMB, [{**self.PERFILES[0], "telefono": "099123456"}] + self.PERFILES[1:])
        r = self.get("/profesora/alumnas")
        self.assertNotIn("Ines", r.text)  # una profesora solo ve el contacto de las activas
        self.assertNotIn("Editar", r.text)
        self.assertEqual(self.sb.table.return_value.select.call_args_list[-1].args[0], main.COLUMNAS_PERSONA_PROFESORA)
        self.assertNotIn("cedula", main.COLUMNAS_PERSONA_PROFESORA)
        for texto in ("Cédula", "cédula", "Reiniciar contraseña", "Carné", "1.234.567"):
            self.assertNotIn(texto, r.text)
        self.sb.rpc.assert_not_called()  # ni siquiera pide los datos personales
        self.assertIn("099123456", r.text)


class AltaPersonas(Base):
    ALTA = {"nombre": "Eva", "apellido": "Uno", "mail": "eva@prueba.invalid"}

    def test_cuenta_nueva_con_rol_en_la_empresa(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
            r = self.post("/profesora/alumnas", {**self.ALTA, "rol": "profesora"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cc.call_args.kwargs["id_empresa"], 1)
        self.assertEqual(cc.call_args.kwargs["rol_empresa"], "profesora")
        self.assertIn("Profesora: cuenta creada", r.text)

    def test_nunca_da_de_alta_administradoras(self):
        self.como(ADMIN)
        for rol in ("admin", "admin_profesora", "superadmin"):
            with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                    mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
                r = self.post("/profesora/alumnas", {**self.ALTA, "rol": rol})
            cc.assert_not_called()
            self.assertIn("ok=0", r.headers["location"])
            self.assertIn("administraci%C3%B3n+general", r.headers["location"])
        self.sb.rpc.assert_not_called()

    def actual(self, filas):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = filas

    def test_si_ya_es_de_la_empresa_con_otro_rol_no_se_suma(self):
        self.como(ADMIN)
        self.rpc("ok")
        for actual, texto in (({"es_admin": False, "es_profesora": True, "es_alumna": False}, "ya es profesora"),
                              ({"es_admin": True, "es_profesora": False, "es_alumna": False}, "solo la administración general")):
            self.actual([actual])
            with mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Pia", "apellido": "Dos"}), \
                    mock.patch.object(main.auth, "crear_cuenta") as cc:
                r = self.post("/profesora/alumnas", {**self.ALTA, "rol": "alumna"})
            self.assertEqual(r.status_code, 400)
            self.assertIn(texto, r.text)
            cc.assert_not_called()
        self.sb.rpc.assert_not_called()

    def test_si_ya_es_de_la_empresa_con_el_mismo_rol(self):
        self.como(ADMIN)
        self.rpc("ok")
        self.actual([{"es_admin": False, "es_profesora": False, "es_alumna": True}])
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Eva", "apellido": "Uno"}):
            r = self.post("/profesora/alumnas", {**self.ALTA, "rol": "alumna"})
        self.assertIn("ok=1", r.headers["location"])
        self.sb.rpc.assert_called_once_with("asignar_membresia", {"p_id_usuario": "x", "p_es_admin": False,
                                                                 "p_es_profesora": False, "p_es_alumna": True})

    def test_mail_existente_se_agrega_a_la_empresa(self):
        self.como(ADMIN)
        self.rpc("ok")
        self.actual([])
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Eva", "apellido": "Uno"}), \
                mock.patch.object(main.auth, "crear_cuenta") as cc:
            r = self.post("/profesora/alumnas", {**self.ALTA, "rol": "alumna"})
        cc.assert_not_called()
        self.assertEqual(r.status_code, 303)
        self.assertIn("misma+contrase", r.headers["location"])
        self.sb.rpc.assert_called_once_with("asignar_membresia", {"p_id_usuario": "x", "p_es_admin": False,
                                                                 "p_es_profesora": False, "p_es_alumna": True})

    def test_mail_de_superadmin_no_se_agrega(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": "s", "nombre": "S", "apellido": "A",
                                                                        "es_superadmin": True}):
            r = self.post("/profesora/alumnas", self.ALTA)
        self.assertEqual(r.status_code, 400)
        self.sb.rpc.assert_not_called()

    def test_metadatos_de_la_cuenta(self):
        admin = mock.MagicMock()
        admin.auth.admin.create_user.return_value.user.id = "nuevo"
        with mock.patch.object(auth, "cliente_admin", return_value=admin):
            auth.crear_cuenta("e@x.uy", "Eva", "Uno", id_empresa=2, rol_empresa="admin_profesora", contrasena="Clave123")
        meta = admin.auth.admin.create_user.call_args.args[0]["user_metadata"]
        self.assertEqual(meta, {"nombre": "Eva", "apellido": "Uno", "id_empresa": 2, "rol_empresa": "admin_profesora"})

    def test_alta_con_empresa_no_cambia_el_rol_viejo(self):
        # profiles.rol = 'profesora' haría administradora a la persona por el trigger de compatibilidad
        admin = mock.MagicMock()
        admin.auth.admin.create_user.return_value.user.id = "nuevo"
        with mock.patch.object(auth, "cliente_admin", return_value=admin):
            auth.crear_cuenta("p@x.uy", "Pia", "Dos", rol="profesora", id_empresa=2, rol_empresa="profesora")
        admin.table.assert_not_called()
        with mock.patch.object(auth, "cliente_admin", return_value=admin):
            auth.crear_cuenta("p@x.uy", "Pia", "Dos", rol="profesora")  # alta vieja (script), sin empresa
        self.assertEqual(admin.table.return_value.update.call_args.args[0], {"rol": "profesora"})


class DatosPersonales(unittest.TestCase):
    def test_usa_la_funcion_de_la_base(self):
        from app import personas
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.return_value.data = [{"id": "a", "cedula": "12345672", "fecha_nacimiento": None,
                                                          "fecha_vencimiento_carne_salud": None}]
        filas = personas.completar(sb, [{"id": "a"}, {"id": "b", "cedula": "colada"}])
        self.assertEqual(filas[0]["cedula"], "12345672")
        self.assertIsNone(filas[1]["cedula"])  # lo que la función no devuelve no se ve
        sb.table.assert_not_called()

    def test_sin_la_funcion_lee_profiles(self):
        from app import personas
        sb = mock.MagicMock()
        sb.rpc.return_value.execute.side_effect = RuntimeError("no existe")
        sb.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [{"id": "a", "cedula": "1"}]
        self.assertEqual(personas.datos_personales(sb, ["a"])["a"]["cedula"], "1")
        self.assertEqual(personas.datos_personales(sb, []), {})


class FacturaConProfesora(unittest.TestCase):
    def test_linea_y_pdf_con_profesora_y_empresa(self):
        import zlib, re
        from app import facturas, factura_pdf
        l = facturas._linea({"id": 1, "fecha": "2026-11-03", "hora_inicio": "18:00:00", "tipo_nombre": "Pilates",
                             "precio_original": 900, "importe_final": 900, "ajuste_tipo": "ninguno",
                             "profesora_nombre": "Paula Ruiz"})
        self.assertEqual(l["profesora"], "Paula Ruiz")
        f = {"numero": "PIL-2026-11-0001", "alumna": "Eva Uno", "mes_txt": "Noviembre 2026", "cantidad_clases": 1,
             "estado_factura": "confirmada", "estado_pago_txt": "Pendiente", "lineas": [l], "subtotal_original": l["precio_original"],
             "total_lineas": l["importe_final"], "total": l["importe_final"], "empresa_nombre": "Pilates Centro"}
        crudo = factura_pdf.generar(f, AHORA)
        texto = b"".join(zlib.decompress(m) for m in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", crudo, re.S))
        for t in (b"Pilates Centro", b"Profesora", b"Paula Ruiz"):
            self.assertIn(t, texto)


class ReiniciarContrasena(Base):
    ID = "11111111-1111-4111-8111-111111111111"

    def reiniciar(self, empresas, id_persona=ID):
        self.como(ADMIN)
        admin = mock.MagicMock()
        admin.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id_empresa": e} for e in empresas]
        cliente = mock.MagicMock()
        cliente.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"nombre": "Eva", "apellido": "Uno", "mail": "e@x.uy", "rol": "profesora"}]
        with mock.patch.object(auth, "cliente_admin", return_value=admin), \
                mock.patch.object(auth, "cliente_con_token", return_value=cliente), \
                mock.patch.object(auth, "reiniciar_contrasena", return_value="Nueva123") as rc:
            r = self.post(f"/profesora/alumnas/{id_persona}/reiniciar-contrasena", {})
        return r, rc

    def test_no_si_la_persona_esta_en_otra_empresa(self):
        r, rc = self.reiniciar([1, 2])
        self.assertIn("otra+empresa", r.headers["location"])
        rc.assert_not_called()

    def test_no_si_no_es_de_esta_empresa(self):
        r, rc = self.reiniciar([2])
        self.assertIn("No+encontramos", r.headers["location"])
        rc.assert_not_called()

    def test_profesora_solo_de_esta_empresa(self):
        r, rc = self.reiniciar([1])
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nueva123", r.text)
        rc.assert_called_once_with(self.ID)


class Login(Base):
    """POST /login con lugar, mail y contraseña. Lo que decide la base (entrar_a_empresa) se simula."""

    def entrar(self, tokens=("A", "R"), empresa=1, datos=None, usuario=None):
        datos = datos or {"lugar": "  just   SHOW up ", "mail": "Ana@Prueba.Invalid ", "contrasena": "clave123"}
        efecto = tokens if isinstance(tokens, Exception) else None
        with mock.patch.object(auth, "iniciar_sesion", return_value=tokens, side_effect=efecto) as ini, \
                mock.patch.object(auth, "entrar_a_empresa", return_value=empresa) as ent, \
                mock.patch.object(auth, "usuario_de_acceso", return_value=usuario or ALUMNA_DOS) as uda, \
                mock.patch.object(auth, "cerrar_sesion") as cs:
            r = self.post("/login", datos)
        return r, ini, ent, uda, cs

    def test_ingresa_y_va_a_la_pantalla_de_su_rol(self):
        for u, destino in ((ALUMNA_DOS, "/"), (PROFE, "/profesora/horarios"), (ADMIN, "/administracion"),
                           (ADMIN_ALUMNA, "/administracion"), (PROFE_ALUMNA, "/profesora/horarios")):
            r, ini, ent, uda, cs = self.entrar(usuario=u)
            self.assertEqual(r.headers["location"], destino)
        ini.assert_called_once_with("ana@prueba.invalid", "clave123")
        ent.assert_called_once_with("A", "just SHOW up")  # espacios de más fuera; el resto lo normaliza la base
        uda.assert_called_once_with("A")  # el lugar lo guarda la base para esta sesión (session_id del token)
        cookies = r.headers.get_list("set-cookie")
        self.assertTrue(any(c.startswith("sb_acceso=") and "HttpOnly" in c for c in cookies))
        self.assertFalse(any(c.startswith("sb_empresa=") for c in cookies))
        cs.assert_not_called()

    def test_mismo_mensaje_para_cualquier_falla(self):
        # contraseña mal, lugar inexistente/desactivado o persona que no es de ese lugar: idéntico
        r1, *_ = self.entrar(tokens=auth.ErrorAuth("El mail o la contraseña no son correctos."))
        r2, _, _, _, cs = self.entrar(empresa=None)
        for r in (r1, r2):
            self.assertEqual(r.status_code, 401)
            self.assertIn(auth.MSG_INGRESO, r.text)
            self.assertNotIn("sb_acceso", r.headers.get("set-cookie", ""))
        self.assertEqual(r1.text.replace("ana@prueba.invalid", ""), r2.text.replace("ana@prueba.invalid", ""))
        cs.assert_called_once_with("A")  # la sesión de credenciales correctas en otro lugar se revoca

    def test_ningun_mensaje_menciona_empresas(self):
        r, *_ = self.entrar(empresa=None)
        for texto in ("Pilates", "pertenec", "no existe", "desactivad"):
            self.assertNotIn(texto, r.text)

    def test_campos_obligatorios(self):
        r, ini, *_ = self.entrar(datos={"lugar": "", "mail": "a@b.uy", "contrasena": "x"})
        self.assertEqual(r.status_code, 400)
        ini.assert_not_called()

    def test_errores_de_conexion_se_informan(self):
        r, *_ = self.entrar(tokens=auth.ErrorAuth(auth.MSG_CONEXION))
        self.assertEqual(r.status_code, 503)
        self.assertIn(auth.MSG_CONEXION, r.text)

    def test_formulario_tiene_los_tres_campos(self):
        r = self.get("/login")
        for campo in ('name="lugar"', 'name="mail"', 'name="contrasena"', "Ingresar"):
            self.assertIn(campo, r.text)
        self.assertNotIn("Pilates", r.text)

    def test_superadmin_no_entra_por_el_login_de_lugares(self):
        r, *_ = self.entrar(empresa=None, usuario=SUPER)
        self.assertEqual(r.status_code, 401)


class LoginSuperadmin(Base):
    def entrar(self, usuario, tokens=("A", "R")):
        with mock.patch.object(auth, "iniciar_sesion", return_value=tokens), \
                mock.patch.object(auth, "usuario_de_acceso", return_value=usuario) as uda, \
                mock.patch.object(auth, "cerrar_sesion") as cs:
            r = self.post("/admin/login", {"mail": "superadmin@prueba.invalid", "contrasena": "clave123"})
        return r, uda, cs

    def test_superadmin_entra_sin_lugar(self):
        r, uda, cs = self.entrar(SUPER)
        self.assertEqual(r.headers["location"], "/admin/empresas")
        uda.assert_called_once_with("A")
        cs.assert_not_called()
        self.assertFalse(any(c.startswith("sb_empresa=") for c in r.headers.get_list("set-cookie")))

    def test_otras_cuentas_reciben_el_error_generico(self):
        for u in (ADMIN, PROFE, ALUMNA_DOS, None):
            r, _, cs = self.entrar(u)
            self.assertEqual(r.status_code, 401)
            self.assertIn(auth.MSG_INGRESO_ADMIN, r.text)
            cs.assert_called_once_with("A")

    def test_formulario_sin_lugar(self):
        r = self.get("/admin/login")
        self.assertNotIn('name="lugar"', r.text)
        self.assertIn('action="/admin/login"', r.text)


class SesionPorEmpresa(unittest.TestCase):
    """auth.usuario_de_acceso: el lugar es el que la base guardó para ESTA sesión (empresa_de_sesion)."""

    def cliente(self, perfil, membresia, empresa, de_sesion):
        cli = mock.MagicMock()
        cli.auth.get_user.return_value.user.id = "u"
        tablas = {"profiles": [perfil], "membresia": membresia, "empresa": empresa}
        def tabla(nombre):
            t = mock.MagicMock()
            consulta = t.select.return_value
            consulta.eq.return_value = consulta
            consulta.execute.return_value.data = tablas[nombre]
            return t
        cli.table.side_effect = tabla
        cli.rpc.return_value.execute.return_value.data = de_sesion
        return cli

    PERFIL = {"nombre": "Ana", "apellido": "P", "mail": "a@b.uy", "rol": "alumna", "es_superadmin": False, "empresa_activa": 2}

    def cargar(self, cli):
        with mock.patch.object(auth, "cliente_nuevo", return_value=cli):
            return auth.usuario_de_acceso("tok")

    def test_sesion_sin_lugar_queda_sin_empresa(self):
        u = self.cargar(self.cliente(self.PERFIL, [], [], None))
        self.assertTrue(u.sin_empresa)

    def test_usa_el_lugar_de_la_sesion_no_el_del_perfil(self):
        cli = self.cliente(self.PERFIL, [{"es_admin": False, "es_profesora": False, "es_alumna": True}],
                           [{"id": 1, "nombre": "Just Show Up", "codigo": "JSU", "activa": True}], 1)
        u = self.cargar(cli)
        cli.rpc.assert_called_once_with("empresa_de_sesion", {})  # nunca elige empresa: solo lee la de la sesión
        self.assertEqual((u.empresa_id, u.empresa_nombre, u.es_alumna), (1, "Just Show Up", True))

    def test_empresa_desactivada_no_da_nada(self):
        u = self.cargar(self.cliente(self.PERFIL, [{"es_admin": True, "es_profesora": True, "es_alumna": False}],
                                     [{"id": 1, "nombre": "X", "codigo": "JSU", "activa": False}], 1))
        self.assertTrue(u.sin_empresa)
        self.assertFalse(u.es_alumna or u.es_docente or u.es_admin)

    def test_al_cerrar_sesion_se_olvida_el_lugar(self):
        c = TestClient(main.app)
        with mock.patch.object(auth, "salir_de_empresa") as sal, mock.patch.object(auth, "cerrar_sesion") as cs:
            c.cookies.set("sb_acceso", "TOK")
            c.post("/logout", follow_redirects=False)
        sal.assert_called_once_with("TOK")
        cs.assert_called_once_with("TOK")


class ProfesoraSoloLoSuyo(Base):
    HORARIOS = [{"id": 1, "dia_semana": "lunes", "hora_inicio": "08:00:00", "id_profesora": "pr", "activo": True,
                 "tipo_clase": {"nombre": "Pilates", "activo": True}},
                {"id": 2, "dia_semana": "martes", "hora_inicio": "09:00:00", "id_profesora": "otra", "activo": True,
                 "tipo_clase": {"nombre": "Yoga", "activo": True}}]

    def test_horarios(self):
        self.sb.table.return_value.select.return_value.execute.return_value.data = self.HORARIOS
        self.como(PROFE)
        r = self.get("/profesora/horarios")
        self.assertIn("Pilates", r.text)
        self.assertNotIn("Yoga", r.text)
        self.como(ADMIN)
        with mock.patch.object(main.personas, "nombres_profesoras", return_value={"pr": "Paula Ruiz", "otra": "Olga"}):
            r = self.get("/profesora/horarios")
        self.assertIn("Yoga", r.text)
        self.assertIn("Olga", r.text)

    def test_no_edita_horarios_ajenos(self):
        self.sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [self.HORARIOS[1]]
        self.como(PROFE)
        self.assertEqual(self.get("/profesora/horarios/2/editar").headers["location"], "/profesora/horarios")
        r = self.post("/profesora/horarios/2/editar", {"dia_semana": "martes", "hora_inicio": "10:00", "id_tipo_clase": "1"})
        self.assertEqual(r.headers["location"], "/profesora/horarios")
        self.sb.table.return_value.update.assert_not_called()

    def test_filtros_de_clases(self):
        from datetime import date
        from app import clases
        sb = mock.MagicMock()
        consulta = sb.table.return_value.select.return_value
        consulta.gte.return_value.lte.return_value.eq.return_value.execute.return_value.data = [
            {"fecha": "2026-11-03", "id_profesora": "pr", "horario": {"activo": True}},
            {"fecha": "2026-11-04", "id_profesora": "otra", "horario": {"activo": True}}]
        self.assertEqual(clases.dias_con_clases(sb, date(2026, 11, 2), date(2026, 11, 9), "pr"), [date(2026, 11, 3)])
        self.assertEqual(len(clases.dias_con_clases(sb, date(2026, 11, 2), date(2026, 11, 9))), 2)
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 1, "dia_semana": "lunes", "hora_inicio": "08:00:00", "id_profesora": "pr"},
            {"id": 2, "dia_semana": "martes", "hora_inicio": "09:00:00", "id_profesora": "otra"}]
        self.assertEqual([h["id"] for h in clases.horarios_para_elegir(sb, "pr")], [1])

    def test_admin_elige_la_profesora_del_horario(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_docentes_activas", return_value={"pr": "Paula Ruiz"}), \
                mock.patch.object(main, "_validar_horario", return_value=({"dia_semana": "lunes", "hora_inicio": "10:00",
                                                                           "id_tipo_clase": 1}, [])):
            r = self.post("/profesora/horarios/nuevo", {"dia_semana": "lunes", "hora_inicio": "10:00", "id_tipo_clase": "1",
                                                         "id_profesora": "pr"})
            self.assertEqual(r.status_code, 303)
            fila = self.sb.table.return_value.insert.call_args.args[0]
            self.assertEqual(fila["id_profesora"], "pr")
            r = self.post("/profesora/horarios/nuevo", {"dia_semana": "lunes", "hora_inicio": "10:00", "id_tipo_clase": "1",
                                                         "id_profesora": "intrusa"})
        self.assertEqual(r.status_code, 400)


class PanelEmpresas(Base):
    ADMIN_INICIAL = {"admin_nombre": "Lia", "admin_apellido": "Paz", "admin_mail": "lia@prueba.invalid"}

    def test_crear_empresa_con_administradora_inicial(self):
        self.como(SUPER)
        self.rpc({"resultado": "ok", "id": 2})
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
            r = self.post("/admin/empresas", {"nombre": " Pilates   Centro ", "codigo": "pil", **self.ADMIN_INICIAL})
        self.sb.rpc.assert_called_once_with("crear_empresa", {"p_nombre": "Pilates Centro", "p_codigo": "PIL"})
        self.assertEqual((cc.call_args.kwargs["id_empresa"], cc.call_args.kwargs["rol_empresa"]), (2, "admin"))
        self.assertIn("Temporal1", r.text)
        self.assertIn("Empresa «Pilates Centro» creada", r.text)

    def test_sin_administradora_inicial_no_crea_nada(self):
        self.como(SUPER)
        r = self.post("/admin/empresas", {"nombre": "Pilates Centro", "codigo": "PIL"})
        self.assertIn("ok=0", r.headers["location"])
        self.sb.rpc.assert_not_called()

    def test_nombre_repetido(self):
        self.como(SUPER)
        self.rpc({"resultado": "nombre_repetido"})
        r = self.post("/admin/empresas", {"nombre": "just-show up", "codigo": "JSX", **self.ADMIN_INICIAL})
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("iniciar+sesi", r.headers["location"])

    def test_no_hay_ruta_para_borrar_empresas(self):
        self.como(SUPER)
        self.assertEqual(self.post("/admin/empresas/2/borrar", {}).status_code, 404)
        self.assertIn(self.client.delete("/admin/empresas/2").status_code, (404, 405))

    def test_errores_traducidos(self):
        self.como(SUPER)
        self.rpc("codigo_fijo")
        r = self.post("/admin/empresas/1/editar", {"nombre": "Just Show Up", "codigo": "JS"})
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("ya+emiti", r.headers["location"])

    def test_desactivar(self):
        self.como(SUPER)
        self.rpc("ok")
        self.post("/admin/empresas/2/estado", {"activa": 0})
        self.sb.rpc.assert_called_once_with("cambiar_estado_empresa", {"p_id": 2, "p_activa": False})

    def test_administradora_nueva_muestra_contrasena(self):
        self.como(SUPER)
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
            r = self.post("/admin/empresas/2/administradora",
                          {"nombre": "Lia", "apellido": "Paz", "mail": "lia@prueba.invalid", "tambien_profesora": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Temporal1", r.text)
        self.assertIn("/admin/empresas", r.text)
        # un solo rol: la casilla vieja «también da clases» ya no existe (la administradora puede dar clases igual)
        self.assertEqual((cc.call_args.kwargs["id_empresa"], cc.call_args.kwargs["rol_empresa"]), (2, "admin"))

    def test_administradora_existente(self):
        self.como(SUPER)
        self.rpc("ok")
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Lia", "apellido": "Paz"}):
            self.post("/admin/empresas/2/administradora", {"nombre": "Lia", "apellido": "Paz", "mail": "lia@prueba.invalid"})
        self.sb.rpc.assert_called_once_with("asignar_administradora",
                                            {"p_id_empresa": 2, "p_id_usuario": "x", "p_es_profesora": False})

    def test_datos_invalidos_no_llaman_a_la_base(self):
        self.como(SUPER)
        r = self.post("/admin/empresas/2/administradora", {"nombre": "L1a", "apellido": "Paz", "mail": "no-es-mail"})
        self.assertIn("ok=0", r.headers["location"])
        self.sb.rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class EditarPersona(Base):
    ID = "22222222-2222-4222-8222-222222222222"
    MEMB = {"id_usuario": ID, "es_admin": False, "es_profesora": True, "es_alumna": False, "activa": True}

    def preparar(self, membresia=True):
        t = self.sb.table.return_value.select.return_value
        t.eq.return_value.eq.return_value.execute.return_value.data = [self.MEMB] if membresia else []
        t.eq.return_value.execute.return_value.data = [{"id": self.ID, "nombre": "Pia", "apellido": "Dos", "mail": "p@x.uy",
                                                       "telefono": "099123456"}]
        # cada función de la base responde lo suyo: datos_personales y pendientes_cambio_rol una lista, las demás «ok»
        self.respuestas = {"datos_personales": [], "pendientes_cambio_rol": []}
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(
            **{"execute.return_value.data": self.respuestas.get(nombre, "ok")})

    def test_ficha_de_una_persona_de_la_empresa(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get(f"/profesora/profesoras/{self.ID}")
        self.assertEqual(r.status_code, 200)
        for texto in ("Pia Dos", "Guardar datos", "Rol actual: <strong>Profesora</strong>", "Cambiar a alumna",
                      f'action="/profesora/profesoras/{self.ID}/baja" method="get"', ">Desactivar</button>", "p@x.uy", "<dt>Teléfono</dt><dd>099123456</dd>", "<dt>Cédula</dt><dd>No registrada</dd>",
                      "<dt>Carné de salud vence</dt>", "Reiniciar contraseña"):
            self.assertIn(texto, r.text)
        for texto in ("es_admin", "Administradora</label>", "Guardar roles"):
            self.assertNotIn(texto, r.text)  # nunca se ofrece el rol de administradora

    def test_pendientes_bloquean_el_cambio_de_rol(self):
        self.como(ADMIN)
        self.preparar()
        self.respuestas["pendientes_cambio_rol"] = ["horarios", "clases_futuras"]
        r = self.get(f"/profesora/profesoras/{self.ID}")
        self.assertIn("Para que pase a ser alumna, antes hay que resolver que", r.text)
        self.assertIn("tiene horarios activos", r.text)
        self.assertNotIn("Cambiar a alumna", r.text)
        self.respuestas["asignar_membresia"] = "pendientes:horarios"
        r = self.post(f"/profesora/profesoras/{self.ID}/roles", {"rol": "alumna"})  # la base vuelve a comprobarlo
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("horarios+activos", r.headers["location"])

    def test_otra_administradora_solo_se_consulta(self):
        self.como(ADMIN)
        self.MEMB = {**self.MEMB, "es_admin": True, "es_profesora": False}
        self.preparar()
        r = self.get(f"/profesora/profesoras/{self.ID}")
        self.assertIn("solo los cambia la administración general", r.text)
        for texto in ("Guardar datos", "Cambiar a", "Dejar inactiva", "name=\"nombre\"", "Reiniciar contraseña"):
            self.assertNotIn(texto, r.text)
        r = self.post(f"/profesora/profesoras/{self.ID}/datos", {"nombre": "X", "apellido": "Y"})
        self.assertEqual(r.status_code, 403)
        r = self.post(f"/profesora/profesoras/{self.ID}/roles", {"rol": "profesora"})
        self.assertIn("administraci%C3%B3n+general", r.headers["location"])
        r = self.post(f"/profesora/profesoras/{self.ID}/estado", {"activa": 0})
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("administraci%C3%B3n+general", r.headers["location"])
        llamadas = [c.args[0] for c in self.sb.rpc.call_args_list]
        for f in ("editar_datos_persona", "asignar_membresia", "actualizar_cedula", "cambiar_estado_membresia"):
            self.assertNotIn(f, llamadas)

    def test_persona_de_otra_empresa_no_se_ve_ni_se_toca(self):
        self.como(ADMIN)
        self.preparar(membresia=False)
        with mock.patch.object(auth, "cliente_admin") as ca:
            r1 = self.get(f"/profesora/alumnas/{self.ID}")
            r2 = self.post(f"/profesora/alumnas/{self.ID}/datos", {"nombre": "X", "apellido": "Y"})
            r3 = self.post(f"/profesora/alumnas/{self.ID}/estado", {"activa": 0})
        for r in (r1, r2, r3):
            self.assertIn("No+encontramos", r.headers["location"])
        ca.assert_not_called()
        self.sb.rpc.assert_not_called()  # ni siquiera lee sus datos personales

    def test_guardar_datos(self):
        self.como(ADMIN)
        self.preparar()
        with mock.patch.object(auth, "cliente_admin") as ca:
            r = self.post(f"/profesora/profesoras/{self.ID}/datos", {"nombre": "Pía", "apellido": "Dos", "telefono": "099 111 222",
                                                                      "cedula": "1.234.567-2"})
        self.assertIn("Datos+guardados", r.headers["location"])
        ca.assert_not_called()  # el perfil lo guarda la base con la sesión de la administradora (no la clave de servidor)
        self.sb.rpc.assert_any_call("editar_datos_persona", {
            "p_id_usuario": self.ID, "p_nombre": "Pía", "p_apellido": "Dos", "p_telefono": "099111222",
            "p_fecha_nacimiento": None, "p_fecha_vencimiento_carne_salud": None})
        self.sb.rpc.assert_any_call("actualizar_cedula", {"p_id_alumna": self.ID, "p_cedula": "12345672"})

    def test_datos_invalidos(self):
        self.como(ADMIN)
        self.preparar()
        with mock.patch.object(auth, "cliente_admin") as ca:
            r = self.post(f"/profesora/profesoras/{self.ID}/datos", {"nombre": "P1a", "apellido": "Dos", "telefono": "123"})
        self.assertEqual(r.status_code, 400)
        ca.assert_not_called()

    def test_roles_y_estado(self):
        self.como(ADMIN)
        self.preparar()
        r = self.post(f"/profesora/profesoras/{self.ID}/roles", {"rol": "alumna"})
        self.sb.rpc.assert_any_call("asignar_membresia", {"p_id_usuario": self.ID, "p_es_admin": False, "p_es_profesora": False,
                                                         "p_es_alumna": True})
        self.assertTrue(r.headers["location"].startswith(f"/profesora/alumnas/{self.ID}?ok=1"))  # pasa a la otra sección
        for rol in ("admin", "admin_profesora"):
            r = self.post(f"/profesora/profesoras/{self.ID}/roles", {"rol": rol})
            self.assertIn("ok=0", r.headers["location"])
        self.assertEqual([c.args[0] for c in self.sb.rpc.call_args_list].count("asignar_membresia"), 1)
        r = self.post(f"/profesora/profesoras/{self.ID}/estado", {"activa": 0})
        self.sb.rpc.assert_any_call("cambiar_estado_membresia", {"p_id_usuario": self.ID, "p_activa": False})
        self.assertIn("inactiva", r.headers["location"])

    def test_profesora_no_edita(self):
        self.como(PROFE)
        self.assertEqual(self.get(f"/profesora/alumnas/{self.ID}").status_code, 303)
        self.assertEqual(self.post(f"/profesora/alumnas/{self.ID}/estado", {"activa": 0}).status_code, 303)
        self.sb.rpc.assert_not_called()


class SuperadminAdministradoras(Base):
    ADM = {"id": "11111111-1111-4111-8111-111111111111", "nombre": "Lia", "apellido": "Paz", "mail": "lia@prueba.invalid",
           "activa": True, "pendientes": []}

    def responder(self, **respuestas):
        respuestas.setdefault("administradoras_empresa", [self.ADM])
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(**{"execute.return_value.data": respuestas.get(nombre)})

    def url(self, accion):
        return f"/admin/empresas/2/administradoras/{self.ADM['id']}/{accion}"

    def test_pasar_a_profesora(self):
        self.como(SUPER)
        self.responder(quitar_administradora="ok")
        r = self.post(self.url("quitar"), {"accion": "profesora"})
        self.sb.rpc.assert_any_call("quitar_administradora", {"p_id_empresa": 2, "p_id_usuario": self.ADM["id"],
                                                              "p_accion": "profesora"})
        self.assertTrue(r.headers["location"].startswith("/admin/empresas/2?ok=1"))  # vuelve a la ficha de la empresa

    def test_nunca_sin_administradora_ni_clases_sin_docente(self):
        self.como(SUPER)
        self.responder(quitar_administradora="ultima_administradora")
        self.assertIn("ninguna+administradora", self.post(self.url("quitar"), {"accion": "profesora"}).headers["location"])
        # dejarla inactiva cancela sus clases próximas: siempre va primero a la vista previa (nada cambia todavía)
        r = self.post(self.url("quitar"), {"accion": "inactiva"})
        self.assertEqual(r.headers["location"], self.url("baja"))
        self.assertNotIn("quitar_administradora", [c.args[0] for c in self.sb.rpc.call_args_list if c.args[1].get("p_accion") == "inactiva"])

    def test_persona_que_no_es_administradora_de_esa_empresa(self):
        self.como(SUPER)
        self.responder(administradoras_empresa=[])
        r = self.post(self.url("quitar"), {"accion": "profesora"})
        self.assertIn("no+es+administradora", r.headers["location"])
        self.assertNotIn("quitar_administradora", [c.args[0] for c in self.sb.rpc.call_args_list])

    def test_reactivar_y_reiniciar_contrasena(self):
        self.como(SUPER)
        self.responder(asignar_administradora="ok")
        self.assertIn("ok=1", self.post(self.url("reactivar"), {}).headers["location"])
        with mock.patch.object(main.auth, "reiniciar_contrasena", return_value="Temporal9") as rc:
            r = self.post(self.url("reiniciar-contrasena"), {})
        rc.assert_called_once_with(self.ADM["id"])
        self.assertIn("Temporal9", r.text)

    def test_solo_la_superadministradora(self):
        for u in (ADMIN, PROFE, ALUMNA_DOS):
            self.como(u)
            for accion in ("quitar", "reactivar", "reiniciar-contrasena"):
                self.assertEqual(self.post(self.url(accion), {"accion": "profesora"}).status_code, 303)
        self.sb.rpc.assert_not_called()
