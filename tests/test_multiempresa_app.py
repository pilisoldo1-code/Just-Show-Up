"""Multiempresa en el servidor: roles por empresa, menú, selector de empresa, alta de personas y panel de la
superadministradora. Sin base (se mockea Supabase); las reglas de la base se prueban en tests/base_local.

Uso: python -m unittest tests.test_multiempresa_app -v
"""

import unittest
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main
from app.auth import Usuario
from app.clases import TZ

EMPRESAS = [{"id": 1, "nombre": "Just Show Up", "codigo": "JSU"}, {"id": 2, "nombre": "Pilates Centro", "codigo": "PIL"}]


def persona(**cambios):
    datos = dict(id="u", mail="u@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t",
                 con_membresias=True, empresa_id=1, empresa_nombre="Just Show Up", empresa_codigo="JSU",
                 empresas=EMPRESAS[:1])
    datos.update(cambios)
    return Usuario(**datos)


ADMIN = persona(id="ad", rol="profesora", nombre="Ada", es_admin=True, es_docente=True)
PROFE = persona(id="pr", rol="profesora", nombre="Paula", es_docente=True)  # profesora, no administradora
ALUMNA_DOS = persona(id="al", es_alumna=True, empresas=EMPRESAS)
SIN_ELEGIR = persona(id="al", es_alumna=False, empresa_id=None, empresa_nombre="", empresas=EMPRESAS)
SUPER = persona(id="su", rol="alumna", nombre="Sara", es_superadmin=True, empresa_id=None, empresa_nombre="", empresas=[])
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
        self.assertEqual(auth.destino(SIN_ELEGIR), "/empresa/elegir")
        self.assertEqual(auth.destino(ADMIN), "/profesora/horarios")
        self.assertEqual(auth.destino(PROFE), "/profesora/horarios")
        self.assertEqual(auth.destino(ALUMNA_DOS), "/")

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

    def test_sin_empresa_elegida_va_al_selector(self):
        self.como(SIN_ELEGIR)
        for url in ("/", "/mis-facturas", "/profesora/horarios"):
            self.assertEqual(self.get(url).headers["location"], "/empresa/elegir", url)

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

    def test_alumna_con_dos_empresas_puede_cambiar(self):
        self.como(ALUMNA_DOS)
        r = self.get("/empresa/elegir")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cambiar de empresa", r.text)
        self.assertIn("Pilates Centro", r.text)
        self.assertIn("Estás acá", r.text)

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


class Selector(Base):
    def test_elegir_empresa(self):
        self.como(ALUMNA_DOS)
        self.rpc("ok")
        r = self.post("/empresa/elegir", {"id_empresa": 2})
        self.assertEqual(r.headers["location"], "/")
        self.sb.rpc.assert_called_once_with("elegir_empresa", {"p_id_empresa": 2})

    def test_empresa_ajena_rechazada(self):
        self.como(ALUMNA_DOS)
        self.rpc("no_autorizada")
        r = self.post("/empresa/elegir", {"id_empresa": 9})
        self.assertTrue(r.headers["location"].startswith("/empresa/elegir?"))

    def test_sin_empresas(self):
        self.como(persona(es_alumna=False, empresa_id=None, empresas=[]))
        self.assertIn("no está habilitada", self.get("/empresa/elegir").text)


class PanelEmpresas(Base):
    def test_crear_empresa_normaliza_codigo(self):
        self.como(SUPER)
        self.rpc({"resultado": "ok", "id": 2})
        r = self.post("/admin/empresas", {"nombre": " Pilates Centro ", "codigo": "pil"})
        self.sb.rpc.assert_called_once_with("crear_empresa", {"p_nombre": "Pilates Centro", "p_codigo": "PIL"})
        self.assertIn("ok=1", r.headers["location"])

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
