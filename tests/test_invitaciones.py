"""Invitaciones por correo (cuenta nueva → la persona elige su contraseña), retiro del ingreso con enlace y
«Olvidé mi contraseña», sin base (Supabase simulado). Las pruebas con el servicio de cuentas real están en
tests/supabase_local/prueba_invitaciones_reales.py.

Uso: python -m unittest tests.test_invitaciones -v
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


class SinIngresoPorEnlace(Base):
    def test_login_ya_no_ofrece_entrar_con_enlace(self):
        for url in ("/login", "/admin/login"):
            self.assertNotIn("entrar-con-enlace", self.client.get(url).text)
        for url in ("/entrar-con-enlace", "/entrar-con-enlace/confirmar"):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertFalse(hasattr(auth, "pedir_enlace_ingreso") or hasattr(auth, "entrar_con_enlace"))


class Invitar(unittest.TestCase):
    """auth.invitar_cuenta / reenviar / anular con Supabase simulado."""
    def setUp(self):
        self.admin = mock.MagicMock()
        self.admin.auth.admin.invite_user_by_email.return_value = mock.MagicMock(user=mock.MagicMock(id="nuevo"))
        for nombre, valor in (("cliente_admin", self.admin),):
            p = mock.patch.object(auth, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def pendiente(self, valor):
        p = mock.patch.object(auth, "invitacion_pendiente", return_value=valor)
        p.start()
        self.addCleanup(p.stop)

    def test_invita_con_los_datos_del_alta_y_sin_contrasena(self):
        uid = auth.invitar_cuenta("ana@ejemplo.test", "Ana", "Uno", f"{SITIO}/activar-cuenta",
                                  extras={"telefono": "099123456", "fecha_nacimiento": ""}, id_empresa=1, rol_empresa="alumna")
        self.assertEqual(uid, "nuevo")
        args = self.admin.auth.admin.invite_user_by_email.call_args.args
        self.assertEqual(args[0], "ana@ejemplo.test")
        self.assertEqual(args[1], {"data": {"nombre": "Ana", "apellido": "Uno", "id_empresa": 1, "rol_empresa": "alumna"},
                                   "redirect_to": f"{SITIO}/activar-cuenta"})
        self.assertNotIn("password", str(args))
        self.admin.auth.admin.create_user.assert_not_called()
        self.admin.table.return_value.update.assert_called_once_with({"telefono": "099123456"})

    def test_nunca_a_mails_ficticios_ni_sin_sitio(self):
        with self.assertRaises(auth.ErrorAuth):
            auth.invitar_cuenta("a@prueba.invalid", "A", "B", f"{SITIO}/activar-cuenta", id_empresa=1)
        with self.assertRaises(auth.ErrorAuth):
            auth.invitar_cuenta("a@ejemplo.test", "A", "B", None, id_empresa=1)
        self.admin.auth.admin.invite_user_by_email.assert_not_called()

    def test_si_falla_el_envio_no_queda_la_cuenta(self):
        self.admin.auth.admin.invite_user_by_email.side_effect = error_api(500, "unexpected_failure")
        self.admin.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "quedo"}]
        self.pendiente(True)
        with self.assertRaises(auth.ErrorAuth):
            auth.invitar_cuenta("ana@ejemplo.test", "Ana", "Uno", f"{SITIO}/activar-cuenta", id_empresa=1)
        self.admin.auth.admin.delete_user.assert_called_once_with("quedo")

    def test_si_falla_el_envio_nunca_borra_una_cuenta_activa(self):
        self.admin.auth.admin.invite_user_by_email.side_effect = error_api(422, "email_exists")
        self.admin.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "activa"}]
        self.pendiente(False)
        with self.assertRaises(auth.ErrorAuth):
            auth.invitar_cuenta("ana@ejemplo.test", "Ana", "Uno", f"{SITIO}/activar-cuenta", id_empresa=1)
        self.admin.auth.admin.delete_user.assert_not_called()

    def test_si_falla_el_perfil_se_borra_la_cuenta(self):
        self.admin.table.return_value.update.return_value.eq.return_value.execute.side_effect = RuntimeError("x")
        with self.assertRaises(auth.ErrorAuth):
            auth.invitar_cuenta("ana@ejemplo.test", "Ana", "Uno", f"{SITIO}/activar-cuenta",
                                extras={"telefono": "099123456"}, id_empresa=1)
        self.admin.auth.admin.delete_user.assert_called_once_with("nuevo")

    def test_reenviar_solo_a_pendientes(self):
        self.pendiente(False)
        with self.assertRaises(auth.ErrorAuth):
            auth.reenviar_invitacion("u", "ana@ejemplo.test", f"{SITIO}/activar-cuenta")
        self.admin.auth.admin.invite_user_by_email.assert_not_called()

    def test_reenviar_misma_cuenta(self):
        self.pendiente(True)
        auth.reenviar_invitacion("u", "ana@ejemplo.test", f"{SITIO}/activar-cuenta")
        self.admin.auth.admin.invite_user_by_email.assert_called_once_with("ana@ejemplo.test",
                                                                           {"redirect_to": f"{SITIO}/activar-cuenta"})

    def test_anular_solo_pendientes(self):
        self.pendiente(False)
        with self.assertRaises(auth.ErrorAuth):
            auth.anular_invitacion("u")
        self.admin.auth.admin.delete_user.assert_not_called()

    def test_pendiente_segun_supabase(self):
        u = lambda **k: mock.MagicMock(user=mock.MagicMock(**k))  # noqa: E731
        casos = (({"invited_at": "x", "email_confirmed_at": None, "last_sign_in_at": None}, True),
                 ({"invited_at": "x", "email_confirmed_at": "y", "last_sign_in_at": None}, False),
                 ({"invited_at": None, "email_confirmed_at": "y", "last_sign_in_at": "z"}, False))
        for datos, esperado in casos:
            self.admin.auth.admin.get_user_by_id.return_value = u(**datos)
            self.assertEqual(auth.invitacion_pendiente("u"), esperado, datos)


class Activar(Base):
    def setUp(self):
        super().setUp()
        self.sb.auth.verify_otp.return_value = mock.MagicMock(session=mock.MagicMock(access_token="a", refresh_token="r"))

    def activar(self, nueva="ClaveNueva-123", repetir=None, token="hash123"):
        return self.post("/activar-cuenta", {"token_hash": token, "nueva": nueva, "repetir": repetir or nueva})

    def test_abrir_el_enlace_no_lo_gasta(self):
        r = self.client.get("/activar-cuenta")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Guardar contraseña", r.text)
        self.assertEqual(r.headers.get("referrer-policy"), "no-referrer")
        self.sb.auth.verify_otp.assert_not_called()

    def test_configura_la_contrasena_y_manda_a_iniciar_sesion(self):
        r = self.activar()
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login?ok=1"))
        self.sb.auth.verify_otp.assert_called_once_with({"token_hash": "hash123", "type": "invite"})
        self.sb.auth.update_user.assert_called_once_with({"password": "ClaveNueva-123"})
        self.sb.auth.sign_out.assert_called_once()
        self.assertNotIn(auth.COOKIE_ACCESO + "=a", r.headers.get("set-cookie", ""))  # no deja sesión abierta

    def test_errores_de_tipeo_no_gastan_el_enlace(self):
        for r in (self.activar(repetir="Otra-12345"), self.activar(nueva="corta")):
            self.assertEqual(r.status_code, 400)
            self.assertIn('value="hash123"', r.text)  # se puede corregir y reintentar
        self.sb.auth.verify_otp.assert_not_called()

    def test_enlace_vencido_o_usado(self):
        self.sb.auth.verify_otp.side_effect = error_api(403, "otp_expired")
        r = self.activar()
        self.assertEqual(r.status_code, 400)
        self.assertIn(auth.MSG_INVITACION_INVALIDA, r.text)
        self.assertIn("Olvidé mi contraseña", r.text)
        self.sb.auth.update_user.assert_not_called()

    def test_sin_codigo(self):
        for token in ("", "x" * 500):
            self.assertEqual(self.activar(token=token).status_code, 400)
        self.sb.auth.verify_otp.assert_not_called()


class Fichas(Base):
    """Rutas de la ficha: reenviar, anular y el correo para cambiar la contraseña (nunca se muestra una contraseña)."""
    ADMIN = persona(id="ad", rol="profesora", es_admin=True, es_docente=True, es_alumna=False)
    PERSONA = {"id": "11111111-1111-4111-8111-111111111111", "mail": "eva@ejemplo.test", "nombre": "Eva", "apellido": "Uno",
               "solo_lectura": False, "yo": False}

    def setUp(self):
        super().setUp()
        auth._usuario_cacheado.return_value = self.ADMIN
        self.parche(main, "_persona_de_empresa", return_value=dict(self.PERSONA))
        self.reenviar = self.parche(auth, "reenviar_invitacion")
        self.anular = self.parche(auth, "anular_invitacion")
        self.admin = self.parche(auth, "cliente_admin")

    def url(self, accion):
        return f"/profesora/alumnas/{self.PERSONA['id']}/{accion}"

    def test_reenviar(self):
        r = self.post(self.url("reenviar-invitacion"), {})
        self.assertIn("ok=1", r.headers["location"])
        self.reenviar.assert_called_once_with(self.PERSONA["id"], "eva@ejemplo.test", f"{SITIO}/activar-cuenta")

    def test_anular_solo_si_es_solo_de_esta_empresa_y_sin_reservas(self):
        tabla = self.admin.return_value.table.return_value.select.return_value.eq.return_value
        for empresas, reservas, anula in (([1], [], True), ([1, 2], [], False), ([1], [{"id": 9}], False)):
            self.anular.reset_mock()
            tabla.execute.return_value.data = [{"id_empresa": e} for e in empresas]
            tabla.limit.return_value.execute.return_value.data = reservas
            r = self.post(self.url("anular-invitacion"), {})
            self.assertEqual(self.anular.called, anula, (empresas, reservas))
            self.assertIn("ok=1" if anula else "ok=0", r.headers["location"])

    def test_solo_la_administradora(self):
        auth._usuario_cacheado.return_value = persona(es_alumna=False, es_docente=True)  # profesora
        for accion in ("reenviar-invitacion", "anular-invitacion"):
            self.assertEqual(self.post(self.url(accion), {}).status_code, 303)
        self.reenviar.assert_not_called()
        self.anular.assert_not_called()


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
