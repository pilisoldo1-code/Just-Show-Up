"""Mi perfil editable: datos personales y cambio de correo, sin base (Supabase simulado). Las pruebas con el servicio de
cuentas real y Mailpit están en tests/supabase_local/prueba_perfil_reales.py.

Uso: python -m unittest tests.test_perfil -v
"""

import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError

from app import auth, main
from app.auth import ErrorAuth, Usuario

SITIO = "http://localhost:8000"
DATOS = {"nombre": "Ana", "apellido": "Prueba", "mail": "ana@ejemplo.test", "telefono": "099123456",
         "fecha_nacimiento": "4 de mayo de 1990", "fecha_vencimiento_carne_salud": "1 de enero de 2020",
         "cedula": "1.234.567-2", "fecha_nacimiento_iso": "1990-05-04", "fecha_vencimiento_carne_salud_iso": "2020-01-01"}


def persona(**cambios):
    datos = dict(id="u", mail="ana@ejemplo.test", nombre="Ana", apellido="Prueba", rol="alumna", token="t",
                 con_membresias=True, empresa_id=1, empresa_nombre="Just Show Up", empresa_codigo="JSU", es_alumna=True)
    datos.update(cambios)
    return Usuario(**datos)


class Base(unittest.TestCase):
    usuario = persona()

    def setUp(self):
        self.client = TestClient(main.app, base_url=SITIO)
        self.sb = mock.MagicMock()
        self.sb.rpc.return_value.execute.return_value.data = "ok"
        self.parche(auth, "_usuario_cacheado", return_value=self.usuario)
        self.parche(main, "_sb", return_value=self.sb)
        self.parche(main, "_datos_perfil", return_value=dict(DATOS, mail=self.usuario.mail))

    def parche(self, objeto, nombre, **kw):
        p = mock.patch.object(objeto, nombre, **kw)
        valor = p.start()
        self.addCleanup(p.stop)
        return valor

    def post(self, url, datos):
        return self.client.post(url, data=datos, follow_redirects=False)


class PantallaPorRol(Base):
    def campos(self, usuario):
        with mock.patch.object(auth, "_usuario_cacheado", return_value=usuario):
            return self.client.get("/perfil").text

    def test_alumna_profesora_y_administradora_editan_todo_menos_la_cedula(self):
        for u in (persona(), persona(rol="profesora", es_alumna=False, es_docente=True),
                  persona(rol="profesora", es_alumna=False, es_docente=True, es_admin=True)):
            t = self.campos(u)
            for n in ("nombre", "apellido", "telefono", "fecha_nacimiento", "fecha_vencimiento_carne_salud"):
                self.assertIn(f'name="{n}"', t)
            self.assertNotIn('name="cedula"', t)
            self.assertIn("1.234.567-2", t)
            self.assertIn('action="/perfil/correo"', t)

    def test_superadministradora_sin_fechas_ni_cedula(self):
        t = self.campos(persona(es_superadmin=True, es_alumna=False, empresa_id=None))
        self.assertIn('name="telefono"', t)
        self.assertNotIn('name="fecha_nacimiento"', t)
        self.assertNotIn("Cédula", t)
        self.assertIn('action="/perfil/correo"', t)

    def test_cuenta_invalid_no_ofrece_cambiar_el_correo(self):
        t = self.campos(persona(mail="demo@prueba.invalid"))
        self.assertNotIn('action="/perfil/correo"', t)
        self.assertIn("no pueden cambiar el correo", t)

    def test_muestra_el_cambio_pendiente(self):
        t = self.campos(persona(mail_pendiente="nueva@ejemplo.test"))
        self.assertIn("Pendiente de confirmación", t)
        self.assertIn("nueva@ejemplo.test", t)


class GuardarDatos(Base):
    def test_guarda_con_la_funcion_de_la_base_y_normaliza(self):
        r = self.post("/perfil/datos", {"nombre": " Ana ", "apellido": "Pérez", "telefono": "099 123-456",
                                         "fecha_nacimiento": "1990-05-04", "fecha_vencimiento_carne_salud": "2030-01-01"})
        self.assertEqual(r.status_code, 303)
        self.sb.rpc.assert_called_once_with("editar_mis_datos", {
            "p_nombre": "Ana", "p_apellido": "Pérez", "p_telefono": "099123456", "p_fecha_nacimiento": "1990-05-04",
            "p_fecha_vencimiento_carne_salud": "2030-01-01"})

    def test_errores_juntos_y_sin_guardar(self):
        r = self.post("/perfil/datos", {"nombre": "Ana1", "apellido": "", "telefono": "123",
                                         "fecha_nacimiento": "2999-01-01", "fecha_vencimiento_carne_salud": "2021-01-01"})
        self.assertEqual(r.status_code, 400)
        self.assertGreaterEqual(r.text.count("<li>"), 5)
        self.sb.rpc.assert_not_called()

    def test_carne_vencido_sin_cambios_no_frena(self):
        r = self.post("/perfil/datos", {"nombre": "Ana", "apellido": "Prueba", "telefono": "",
                                         "fecha_nacimiento": "", "fecha_vencimiento_carne_salud": "2020-01-01"})
        self.assertEqual(r.status_code, 303)

    def test_no_recibe_ni_cambia_la_cedula(self):
        self.post("/perfil/datos", {"nombre": "Ana", "apellido": "Prueba", "cedula": "11111111"})
        self.assertNotIn("actualizar_cedula", [c.args[0] for c in self.sb.rpc.call_args_list])

    def test_si_la_base_rechaza_avisa(self):
        self.sb.rpc.return_value.execute.return_value.data = "datos_invalidos"
        r = self.post("/perfil/datos", {"nombre": "Ana", "apellido": "Prueba"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("No se pudieron guardar", r.text)


class SuperadminDatos(Base):
    usuario = persona(es_superadmin=True, es_alumna=False, empresa_id=None)

    def test_las_fechas_que_mande_se_ignoran(self):
        self.post("/perfil/datos", {"nombre": "Admin", "apellido": "General", "fecha_nacimiento": "1980-01-01"})
        args = self.sb.rpc.call_args.args[1]
        self.assertEqual(args["p_fecha_nacimiento"], "1990-05-04")  # el valor que ya tenía, sin tocar


class PedirCambio(unittest.TestCase):
    def setUp(self):
        self.cli = mock.MagicMock()
        p = mock.patch.object(auth, "cliente_nuevo", return_value=self.cli)
        p.start()
        self.addCleanup(p.stop)
        self.u = persona()

    def pedir(self, nuevo, u=None, url=f"{SITIO}/confirmar-correo"):
        return auth.pedir_cambio_correo(u or self.u, nuevo, "clave", url)

    def test_pide_con_la_contrasena_y_el_enlace_del_sitio(self):
        self.assertEqual(self.pedir(" Nueva@Ejemplo.TEST "), "nueva@ejemplo.test")
        self.cli.auth.sign_in_with_password.assert_called_once_with({"email": "ana@ejemplo.test", "password": "clave"})
        self.cli.auth.update_user.assert_called_once_with({"email": "nueva@ejemplo.test"},
                                                          {"email_redirect_to": f"{SITIO}/confirmar-correo"})
        self.cli.auth.sign_out.assert_called_once_with({"scope": "local"})

    def test_rechazos_sin_llamar_a_supabase(self):
        for nuevo, u, url in (("ana@ejemplo.test", None, "x"), ("mal", None, "x"), ("n@algo.invalid", None, "x"),
                              ("n@ejemplo.test", persona(mail="demo@prueba.invalid"), "x"), ("n@ejemplo.test", None, None)):
            with self.assertRaises(ErrorAuth):
                self.pedir(nuevo, u, url)
        self.cli.auth.update_user.assert_not_called()

    def test_contrasena_incorrecta(self):
        self.cli.auth.sign_in_with_password.side_effect = AuthApiError("x", 400, "invalid_credentials")
        with self.assertRaisesRegex(ErrorAuth, "contraseña actual"):
            self.pedir("n@ejemplo.test")
        self.cli.auth.update_user.assert_not_called()

    def test_correo_de_otra_cuenta(self):
        self.cli.auth.update_user.side_effect = AuthApiError("A user with this email address has already been registered",
                                                             422, "email_exists")
        with self.assertRaisesRegex(ErrorAuth, "otra cuenta"):
            self.pedir("n@ejemplo.test")
        self.cli.auth.sign_out.assert_called_once()

    def test_muy_seguido(self):
        self.cli.auth.update_user.side_effect = AuthApiError("x", 429, "over_email_send_rate_limit")
        with self.assertRaisesRegex(ErrorAuth, "un minuto"):
            self.pedir("n@ejemplo.test")


class Confirmar(unittest.TestCase):
    def respuesta(self, status, datos):
        return httpx.Response(status, json=datos, request=httpx.Request("POST", "http://x"))

    def confirmar(self, *respuestas):
        with mock.patch.object(auth.httpx, "post", side_effect=list(respuestas)) as post, \
                mock.patch.dict("os.environ", {"SUPABASE_URL": "http://127.0.0.1:54321", "SUPABASE_KEY": "k"}):
            return auth.confirmar_cambio_correo("abc"), post

    def test_primera_confirmacion(self):
        correo, post = self.confirmar(self.respuesta(200, {"code": 200, "msg": "Confirmation link accepted"}))
        self.assertIsNone(correo)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"], {"type": "email_change", "token_hash": "abc"})

    def test_segunda_confirmacion_cierra_la_sesion_devuelta(self):
        correo, post = self.confirmar(self.respuesta(200, {"access_token": "tok", "user": {"email": "Nueva@Ejemplo.test"}}),
                                      self.respuesta(204, {}))
        self.assertEqual(correo, "nueva@ejemplo.test")
        self.assertTrue(post.call_args.args[0].endswith("/auth/v1/logout"))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_enlace_vencido_o_invalido(self):
        for status in (403, 400, 404):
            with self.assertRaisesRegex(ErrorAuth, "venció o ya se usó"):
                self.confirmar(self.respuesta(status, {"error_code": "otp_expired"}))
        for vacio in ("", " ", "x" * 201):
            with self.assertRaises(ErrorAuth):
                auth.confirmar_cambio_correo(vacio)


class RutasConfirmar(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url=SITIO)

    def test_abrir_la_pagina_no_canjea_nada(self):
        with mock.patch.object(auth, "confirmar_cambio_correo") as c:
            r = self.client.get("/confirmar-correo")
        self.assertEqual(r.status_code, 200)
        c.assert_not_called()
        self.assertIn('content="no-referrer"', r.text)

    def test_mensajes(self):
        with mock.patch.object(auth, "confirmar_cambio_correo", return_value=None):
            self.assertIn("abrí también el enlace", self.client.post("/confirmar-correo", data={"token_hash": "a"}).text)
        with mock.patch.object(auth, "confirmar_cambio_correo", return_value="n@ejemplo.test"):
            t = self.client.post("/confirmar-correo", data={"token_hash": "a"}).text
            self.assertIn("n@ejemplo.test", t)
        with mock.patch.object(auth, "confirmar_cambio_correo", side_effect=ErrorAuth(auth.MSG_CORREO_LINK_INVALIDO)):
            r = self.client.post("/confirmar-correo", data={"token_hash": "a"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("venció o ya se usó", r.text)

    def test_cambiar_correo_exige_sesion(self):
        r = self.client.post("/perfil/correo", data={"nuevo": "n@ejemplo.test", "contrasena": "x"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))
        r = self.client.post("/perfil/datos", data={"nombre": "A", "apellido": "B"}, follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/login"))


if __name__ == "__main__":
    unittest.main()
