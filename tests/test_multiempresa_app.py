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
        r = self.get("/administracion")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Panel de administración", r.text)
        for href, *_ in main.SECCIONES_ADMIN:
            self.assertIn(f'href="{href}"', r.text)

    def test_roles_combinados(self):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        # profesora que no es alumna no entra a lo de alumna; con los dos roles, sí, sin cerrar sesión
        self.como(PROFE)
        self.assertEqual(self.get("/mis-facturas").headers["location"], "/profesora/horarios")
        self.como(PROFE_ALUMNA)
        with mock.patch.object(main.facturas, "facturas_alumna", return_value=[], create=True):
            r = self.get("/mi-historial")
        self.assertNotEqual(r.status_code, 303)
        self.assertIn('href="/profesora/horarios" class="menu-otro-rol">Panel de profesora', r.text)
        self.assertIn('href="/" class="menu-otro-rol">Mis clases', self.get("/profesora/alumnas").text)
        self.como(ADMIN_ALUMNA)
        r = self.get("/mi-historial")
        self.assertIn('href="/administracion" class="menu-otro-rol">Panel de administración', r.text)
        self.como(ADMIN)
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
        for texto in ("Tipos de clase", "Facturación", "Just Show Up", "+ Nueva persona"):
            self.assertIn(texto, r.text)
        self.assertNotIn("Cambiar de empresa", r.text)

    def test_profesora_no_ve_facturacion_ni_tipos(self):
        self.como(PROFE)
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        r = self.get("/profesora/alumnas")
        self.assertEqual(r.status_code, 200)
        for texto in ("Tipos de clase", "Facturación", "Nueva persona"):
            self.assertNotIn(texto, r.text)
        for texto in ("Horarios", "Asistencias", "Planificación", "Estadísticas"):
            self.assertIn(texto, r.text)

    def test_el_menu_solo_muestra_la_empresa_de_la_sesion(self):
        self.como(ALUMNA_DOS)
        with mock.patch.object(main.facturas, "facturas_alumna", return_value=[], create=True):
            r = self.get("/mi-historial")
        self.assertIn("Just Show Up", r.text)
        for texto in ("Pilates", "Cambiar de empresa", "/empresa/elegir"):
            self.assertNotIn(texto, r.text)

    def test_superadmin_solo_ve_empresas(self):
        self.como(SUPER)
        self.rpc([{"id": 1, "nombre": "Just Show Up", "codigo": "JSU", "activa": True, "profesoras": 1, "alumnas": 9,
                   "administradoras": "Profe Prueba <profesora@prueba.invalid>"}])
        r = self.get("/admin/empresas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Profe Prueba", r.text)
        for texto in ("Horarios", "Facturación", "Mis facturas"):
            self.assertNotIn(texto, r.text)


class ListaPersonas(Base):
    def preparar(self, membresias, perfiles):
        tabla = self.sb.table.return_value
        tabla.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = membresias
        tabla.select.return_value.in_.return_value.execute.return_value.data = perfiles

    MEMB = [{"id_usuario": "a1", "es_admin": False, "es_profesora": False, "es_alumna": True},
            {"id_usuario": "p1", "es_admin": False, "es_profesora": True, "es_alumna": False}]

    def test_admin_ve_cedula_y_acciones(self):
        self.como(ADMIN)
        self.preparar(self.MEMB, [{"id": "a1", "nombre": "Eva", "apellido": "Uno", "mail": "e@x.uy"},
                                  {"id": "p1", "nombre": "Pia", "apellido": "Dos", "mail": "p@x.uy"}])
        self.rpc([{"id": "a1", "cedula": "12345672", "fecha_nacimiento": None, "fecha_vencimiento_carne_salud": "2027-01-31"}])
        r = self.get("/profesora/alumnas")
        self.sb.rpc.assert_called_once_with("datos_personales", {"p_ids": ["a1", "p1"]})
        self.assertIn("<td>1.234.567-2</td>", r.text)
        self.assertIn("<td>2027-01-31</td>", r.text)
        self.assertIn("Reiniciar contraseña", r.text)
        self.assertIn("Profesora", r.text)
        self.assertLess(r.text.index("Eva"), r.text.index("Pia"))  # alumnas primero
        self.assertEqual(self.sb.table.return_value.select.call_args_list[-1].args[0], main.COLUMNAS_ALUMNA)

    def test_profesora_no_pide_ni_ve_la_cedula(self):
        self.como(PROFE)
        self.preparar(self.MEMB, [{"id": "a1", "nombre": "Eva", "apellido": "Uno", "mail": "e@x.uy", "telefono": "099123456"},
                                  {"id": "p1", "nombre": "Pia", "apellido": "Dos", "mail": "p@x.uy"}])
        r = self.get("/profesora/alumnas")
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

    def test_rol_desconocido_queda_alumna(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(main.auth, "crear_cuenta", return_value=("id", "Temporal1")) as cc:
            self.post("/profesora/alumnas", {**self.ALTA, "rol": "superadmin"})
        self.assertEqual(cc.call_args.kwargs["rol_empresa"], "alumna")

    def test_mail_existente_se_agrega_a_la_empresa(self):
        self.como(ADMIN)
        self.rpc("ok")
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
        with mock.patch.object(main.personas, "nombres_profesoras", return_value={"pr": "Paula Ruiz"}), \
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
        self.assertEqual((cc.call_args.kwargs["id_empresa"], cc.call_args.kwargs["rol_empresa"]), (2, "admin_profesora"))

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
