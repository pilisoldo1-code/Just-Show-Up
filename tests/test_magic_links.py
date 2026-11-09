"""Entrar con un enlace por correo (Magic Link) y «Olvidé mi contraseña», sin base (Supabase simulado).
Las pruebas con el servicio de cuentas real están en tests/supabase_local/prueba_magic_links_reales.py.

Uso: python -m unittest tests.test_magic_links -v
"""

import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError

from app import auth, main
from app.auth import Usuario

SITIO = "http://localhost:8000"  # host permitido (SITIOS_PERMITIDOS)


def persona(**cambios):
    datos = dict(id="u", mail="ana@ejemplo.test", nombre="Ana", apellido="Prueba", rol="alumna", token="t",
                 con_membresias=True, empresa_id=1, empresa_nombre="Just Show Up", empresa_codigo="JSU", es_alumna=True)
    datos.update(cambios)
    return Usuario(**datos)


def error_api(status, code):
    return AuthApiError("simulado", status, code)


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url=SITIO)
        self.sb = mock.MagicMock()
        self.parche(auth, "cliente_nuevo", return_value=self.sb)
        self.parche(auth, "_usuario_cacheado", return_value=None)

    def parche(self, objeto, nombre, **kw):
        p = mock.patch.object(objeto, nombre, **kw)
        valor = p.start()
        self.addCleanup(p.stop)
        return valor

    def post(self, url, datos, client=None):
        return (client or self.client).post(url, data=datos, follow_redirects=False)


class Pantallas(Base):
    def test_login_ofrece_el_enlace_y_admin_login_no(self):
        self.assertIn('href="/entrar-con-enlace"', self.client.get("/login").text)
        self.assertNotIn("/entrar-con-enlace", self.client.get("/admin/login").text)

    def test_formulario_pide_lugar_y_mail_sin_contrasena(self):
        r = self.client.get("/entrar-con-enlace")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="lugar"', r.text)
        self.assertIn('name="mail"', r.text)
        self.assertNotIn('type="password"', r.text)

    def test_con_sesion_va_a_su_inicio(self):
        auth._usuario_cacheado.return_value = persona()
        r = self.client.get("/entrar-con-enlace", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))


class PedirEnlace(Base):
    DATOS = {"lugar": "  Just   Show Up ", "mail": " Ana@Ejemplo.test "}

    def test_faltan_datos(self):
        for datos in ({"lugar": "", "mail": "a@b.test"}, {"lugar": "JSU", "mail": ""}):
            self.assertEqual(self.post("/entrar-con-enlace", datos).status_code, 400)
        self.sb.auth.sign_in_with_otp.assert_not_called()

    def test_mail_mal_escrito(self):
        r = self.post("/entrar-con-enlace", {"lugar": "JSU", "mail": "sin-arroba"})
        self.assertEqual(r.status_code, 400)
        self.sb.auth.sign_in_with_otp.assert_not_called()

    def test_pide_el_enlace_sin_crear_cuentas_y_con_el_lugar(self):
        r = self.post("/entrar-con-enlace", self.DATOS)
        self.assertEqual(r.status_code, 200)
        self.assertIn("te enviamos un correo", r.text)
        credenciales = self.sb.auth.sign_in_with_otp.call_args.args[0]
        self.assertEqual(credenciales["email"], "ana@ejemplo.test")
        self.assertIs(credenciales["options"]["should_create_user"], False)
        self.assertEqual(credenciales["options"]["email_redirect_to"],
                         f"{SITIO}/entrar-con-enlace/confirmar?lugar=Just+Show+Up")

    def test_misma_respuesta_exista_o_no_la_cuenta(self):
        textos = []
        for efecto in (None, error_api(422, "otp_disabled"), error_api(400, "signup_disabled"),
                       error_api(429, "over_email_send_rate_limit"), error_api(500, "unexpected_failure")):
            self.sb.auth.sign_in_with_otp.side_effect = efecto
            r = self.post("/entrar-con-enlace", self.DATOS)
            textos.append((r.status_code, r.text))
        self.assertEqual(len(set(textos)), 1, "las respuestas no son idénticas")
        self.assertEqual(textos[0][0], 200)

    def test_no_manda_a_mails_ficticios(self):
        r = self.post("/entrar-con-enlace", {"lugar": "JSU", "mail": "alumna@prueba.invalid"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("te enviamos un correo", r.text)
        self.sb.auth.sign_in_with_otp.assert_not_called()

    def test_host_no_permitido_no_manda(self):
        otro = TestClient(main.app, base_url="http://sitio-ajeno.example")
        r = self.post("/entrar-con-enlace", self.DATOS, client=otro)
        self.assertEqual(r.status_code, 200)
        self.sb.auth.sign_in_with_otp.assert_not_called()

    def test_sin_conexion(self):
        self.sb.auth.sign_in_with_otp.side_effect = httpx.ConnectError("x")
        r = self.post("/entrar-con-enlace", self.DATOS)
        self.assertEqual(r.status_code, 503)
        self.assertIn(auth.MSG_CONEXION, r.text)


class ConfirmarEnlace(Base):
    def setUp(self):
        super().setUp()
        sesion = mock.MagicMock(access_token="acc", refresh_token="ref")
        self.sb.auth.verify_otp.return_value = mock.MagicMock(session=sesion)
        self.entrar = self.parche(auth, "entrar_a_empresa", return_value=1)
        self.cerrar = self.parche(auth, "cerrar_sesion")
        self.usuario = self.parche(auth, "usuario_de_acceso", return_value=persona())

    def confirmar(self, lugar="Just Show Up", token="hash123"):
        return self.post("/entrar-con-enlace/confirmar", {"lugar": lugar, "token_hash": token})

    def test_abrir_el_enlace_no_lo_gasta(self):
        r = self.client.get("/entrar-con-enlace/confirmar?lugar=Just+Show+Up")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Just Show Up", r.text)
        self.assertIn(">Entrar</button>", r.text)
        self.assertEqual(r.headers.get("referrer-policy"), "no-referrer")
        self.sb.auth.verify_otp.assert_not_called()

    def test_lugar_de_la_url_se_escapa(self):
        r = self.client.get("/entrar-con-enlace/confirmar?lugar=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertNotIn("<script>alert(1)", r.text)

    def test_entra_con_cookies_y_va_al_inicio_de_su_rol(self):
        for usuario, destino in ((persona(), "/"),
                                 (persona(es_alumna=False, es_docente=True), "/profesora/horarios"),
                                 (persona(es_alumna=False, es_docente=True, es_admin=True), "/administracion")):
            self.usuario.return_value = usuario
            r = self.confirmar()
            self.assertEqual((r.status_code, r.headers["location"]), (303, destino))
            self.assertIn(auth.COOKIE_ACCESO, r.headers.get("set-cookie", ""))
        self.sb.auth.verify_otp.assert_called_with({"token_hash": "hash123", "type": "magiclink"})
        self.entrar.assert_called_with("acc", "Just Show Up")
        self.cerrar.assert_not_called()

    def test_no_es_de_ese_lugar_cierra_la_sesion_y_401(self):
        self.entrar.return_value = None  # otra empresa, empresa inactiva, membresía inactiva o superadministradora
        r = self.confirmar(lugar="Pilates Centro")
        self.assertEqual(r.status_code, 401)
        self.assertIn(auth.MSG_ENLACE_INVALIDO, r.text)
        self.assertNotIn("set-cookie", r.headers)
        self.cerrar.assert_called_once_with("acc")

    def test_enlace_vencido_o_usado_mismo_401(self):
        self.entrar.return_value = None
        r_lugar = self.confirmar()
        self.entrar.return_value = 1
        self.sb.auth.verify_otp.side_effect = error_api(403, "otp_expired")
        r_vencido = self.confirmar()
        self.assertEqual(r_vencido.status_code, 401)
        self.assertNotIn("set-cookie", r_vencido.headers)
        self.assertEqual(r_lugar.text, r_vencido.text, "el mensaje no debe decir qué falló")

    def test_sin_lugar_no_gasta_el_enlace(self):
        r = self.confirmar(lugar="  ")
        self.assertEqual(r.status_code, 401)
        self.sb.auth.verify_otp.assert_not_called()

    def test_sin_codigo_o_codigo_gigante(self):
        for token in ("", "x" * 500):
            self.assertEqual(self.confirmar(token=token).status_code, 401)
        self.sb.auth.verify_otp.assert_not_called()

    def test_sin_conexion_deja_reintentar(self):
        self.sb.auth.verify_otp.side_effect = httpx.ConnectError("x")
        r = self.confirmar()
        self.assertEqual(r.status_code, 503)
        self.assertIn('value="hash123"', r.text)
        self.assertNotIn("set-cookie", r.headers)


class Recuperacion(Base):
    def test_misma_respuesta_tambien_si_supabase_frena_el_envio(self):
        textos = []
        for efecto in (None, error_api(429, "over_email_send_rate_limit"), error_api(400, "user_not_found")):
            self.sb.auth.reset_password_for_email.side_effect = efecto
            r = self.post("/olvide-contrasena", {"mail": "ana@ejemplo.test"})
            textos.append((r.status_code, r.text))
        self.assertEqual(len(set(textos)), 1)
        self.assertNotIn("Ya pediste varios links", textos[0][1])

    def test_sigue_mandando_el_link_de_siempre(self):
        self.post("/olvide-contrasena", {"mail": "ana@ejemplo.test"})
        self.sb.auth.reset_password_for_email.assert_called_once_with(
            "ana@ejemplo.test", {"redirect_to": f"{SITIO}/restablecer-contrasena"})

    def test_no_manda_a_mails_ficticios(self):
        r = self.post("/olvide-contrasena", {"mail": "alumna@prueba.invalid"})
        self.assertEqual(r.status_code, 200)
        self.sb.auth.reset_password_for_email.assert_not_called()

    def test_sin_conexion(self):
        self.sb.auth.reset_password_for_email.side_effect = httpx.ConnectError("x")
        self.assertEqual(self.post("/olvide-contrasena", {"mail": "ana@ejemplo.test"}).status_code, 503)


def token_con_amr(metodo):
    import base64
    import json
    carga = base64.urlsafe_b64encode(json.dumps({"amr": [{"method": metodo}]}).encode()).decode().rstrip("=")
    return f"x.{carga}.y"


class RestablecerSoloConRecuperacion(Base):
    def setUp(self):
        super().setUp()
        self.con_token = self.parche(auth, "cliente_con_token")

    def restablecer(self, acceso):
        return self.post("/restablecer-contrasena", {"acceso": acceso, "refresco": "r", "nueva": "ClaveNueva-123",
                                                     "repetir": "ClaveNueva-123"})

    def test_sesion_de_recuperacion_cambia_la_contrasena(self):
        self.con_token.return_value.rpc.return_value.execute.return_value.data = None
        r = self.restablecer(token_con_amr("otp"))
        self.assertEqual(r.status_code, 303)
        self.sb.auth.update_user.assert_called_once_with({"password": "ClaveNueva-123"})

    def test_sesion_de_enlace_de_ingreso_no_cambia_la_contrasena(self):
        # misma clase de sesión (amr otp) pero ya entró a un lugar: es de un enlace de ingreso
        self.con_token.return_value.rpc.return_value.execute.return_value.data = 1
        r = self.restablecer(token_con_amr("otp"))
        self.assertEqual(r.status_code, 400)
        self.assertIn(auth.MSG_LINK_INVALIDO, r.text)
        self.sb.auth.update_user.assert_not_called()

    def test_sesion_con_contrasena_no_cambia_la_contrasena(self):
        r = self.restablecer(token_con_amr("password"))
        self.assertEqual(r.status_code, 400)
        self.sb.auth.update_user.assert_not_called()

    def test_si_no_se_puede_comprobar_no_cambia(self):
        self.con_token.return_value.rpc.side_effect = RuntimeError("x")
        r = self.restablecer(token_con_amr("otp"))
        self.assertEqual(r.status_code, 400)
        self.sb.auth.update_user.assert_not_called()


class MailFicticio(unittest.TestCase):
    def test_dominios(self):
        for mail in ("a@prueba.invalid", "a@demo.invalid", "a@invalid", "A@PRUEBA.INVALID."):
            self.assertTrue(auth.es_mail_ficticio(mail), mail)
        for mail in ("a@gmail.com", "a@invalid.com", "a@ejemplo.test"):
            self.assertFalse(auth.es_mail_ficticio(mail), mail)


if __name__ == "__main__":
    unittest.main()
