"""Correos automáticos (notificaciones, fases B y C): configuración, contenido, despachador y endpoint, sin base ni SMTP.

Uso: python -m unittest tests.test_correos -v
"""
import os
import smtplib
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, correos, main
from app.auth import Usuario

SECRETO = "x" * 40
ENTORNO = {"NOTIFICACIONES_SECRETO": SECRETO, "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "465",
           "SMTP_USUARIO": "cuenta@gmail.com", "SMTP_CONTRASENA": "clave-de-aplicacion", "SMTP_REMITENTE": "cuenta@gmail.com",
           "SMTP_SEGURIDAD": "ssl"}
CLASE = {"nombre": "Ana", "tipo_clase": "Pilates", "fecha": "2026-11-18", "hora_inicio": "18:00:00"}
FACTURA = {"nombre": "Ana", "numero": "JSU-2026-10-0001", "mes": 10, "anio": 2026, "total": "1237.50", "vence_el": "2026-11-10"}
DATOS = {
    "clase_cancelada": CLASE, "recordatorio_clase": CLASE, "reserva_confirmada": CLASE,
    "lugar_disponible": {**CLASE, "oferta_vence_en": "2026-11-18T20:30:00+00:00"},
    "factura_nueva": FACTURA, "factura_proxima": FACTURA, "factura_vencida": FACTURA, "factura_vencida_2": FACTURA,
    "pago_registrado": {**FACTURA, "monto": "800.00", "medio_pago": "mercado_pago"},
}


def fila(tipo, id_=1, empresa="Pilates Centro", datos=None):
    return {"id": id_, "tipo": tipo, "mail": "ana@ejemplo.com", "empresa": empresa,
            "sitio": "https://just-show-up-theta.vercel.app", "datos": DATOS[tipo] if datos is None else datos}


class AdminFalso:
    def __init__(self, filas):
        self.filas, self.resultados = filas, []

    def rpc(self, nombre, params):
        if nombre == "notificaciones_reclamar":
            datos, self.filas = self.filas, []
        else:
            self.resultados.append((params["p_id"], params["p_resultado"], params.get("p_error")))
            datos = "ok"
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=datos))


class SmtpFalso:
    def __init__(self, fallar_en=None, error=None):
        self.enviados, self.fallar_en, self.error, self.cerrado = [], fallar_en, error, False

    def send_message(self, m):
        if len(self.enviados) + 1 == self.fallar_en:
            self.enviados.append(None)
            raise self.error
        self.enviados.append(m)

    def quit(self):
        self.cerrado = True


class Configuracion(unittest.TestCase):
    def test_gmail_completo(self):
        with mock.patch.dict(os.environ, ENTORNO, clear=False):
            c = correos.configuracion()
        self.assertEqual((c.host, c.puerto, c.seguridad), ("smtp.gmail.com", 465, "ssl"))

    def test_falta_algo_no_despacha(self):
        for quitar in ("NOTIFICACIONES_SECRETO", "SMTP_HOST", "SMTP_REMITENTE", "SMTP_CONTRASENA"):
            with self.subTest(quitar=quitar), mock.patch.dict(os.environ, {**ENTORNO, quitar: ""}):
                with self.assertRaises(correos.SinConfigurar):
                    correos.configuracion()

    def test_secreto_corto(self):
        with mock.patch.dict(os.environ, {**ENTORNO, "NOTIFICACIONES_SECRETO": "corto"}):
            with self.assertRaises(correos.SinConfigurar):
                correos.configuracion()

    def test_sin_cifrar_solo_en_este_equipo(self):
        local = {**ENTORNO, "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "54325", "SMTP_SEGURIDAD": "ninguna",
                 "SMTP_USUARIO": "", "SMTP_CONTRASENA": ""}
        with mock.patch.dict(os.environ, local):
            self.assertEqual(correos.configuracion().seguridad, "ninguna")
        with mock.patch.dict(os.environ, {**local, "SMTP_HOST": "smtp.gmail.com"}):
            with self.assertRaises(correos.SinConfigurar):
                correos.configuracion()

    def test_la_configuracion_no_muestra_secretos(self):
        with mock.patch.dict(os.environ, ENTORNO):
            texto = repr(correos.configuracion())
        self.assertNotIn("clave-de-aplicacion", texto)
        self.assertNotIn(SECRETO, texto)

    def test_secreto(self):
        self.assertTrue(correos.secreto_valido(f"Bearer {SECRETO}", SECRETO))
        for cabecera in ("", SECRETO, f"Bearer {SECRETO}x", "Bearer "):
            self.assertFalse(correos.secreto_valido(cabecera, SECRETO))
        self.assertFalse(correos.secreto_valido("Bearer ", ""))

    def test_el_error_guardado_no_tiene_correos(self):
        e = smtplib.SMTPRecipientsRefused({"ana@ejemplo.com": (550, b"no")})
        self.assertNotIn("ana@ejemplo.com", correos.limpiar_error(e))
        self.assertLessEqual(len(correos.limpiar_error(Exception("x" * 1000))), 300)


class Contenido(unittest.TestCase):
    def test_los_nueve_tipos(self):
        esperado = {"clase_cancelada": "Se canceló", "recordatorio_clase": "empieza a las 18:00",
                    "reserva_confirmada": "Reserva confirmada", "lugar_disponible": "Se liberó un lugar",
                    "factura_nueva": "Ya está disponible tu factura de octubre 2026",
                    "factura_proxima": "vence el 10/11/2026", "factura_vencida": "está vencida",
                    "factura_vencida_2": "sigue pendiente", "pago_registrado": "Registramos el pago"}
        self.assertEqual(set(esperado), set(correos.PLANTILLAS))
        for tipo, texto in esperado.items():
            with self.subTest(tipo=tipo):
                m = correos.armar(fila(tipo), "cuenta@gmail.com")
                self.assertTrue(m["Subject"].startswith("Pilates Centro: "))
                self.assertIn(texto, m["Subject"])
                cuerpo = m.get_body(("plain",)).get_content()
                self.assertIn("Hola Ana:", cuerpo)
                self.assertIn("https://just-show-up-theta.vercel.app/", cuerpo)
                self.assertIn("/perfil", cuerpo)  # cómo desactivarlos
                self.assertEqual(m["Auto-Submitted"], "auto-generated")

    def test_detalles(self):
        self.assertIn("$1.237,50", correos.armar(fila("factura_nueva"), "c@gmail.com").get_body(("plain",)).get_content())
        oferta = correos.armar(fila("lugar_disponible"), "c@gmail.com").get_body(("plain",)).get_content()
        self.assertIn("antes de las 17:30", oferta)  # hora de Montevideo
        self.assertIn("/mis-inscripciones", oferta)
        pago = correos.armar(fila("pago_registrado"), "c@gmail.com").get_body(("plain",)).get_content()
        self.assertIn("Mercado Pago", pago)
        self.assertIn("/dia/2026-11-18", correos.armar(fila("recordatorio_clase"), "c@gmail.com").get_body(("plain",)).get_content())

    def test_html_escapa(self):
        m = correos.armar(fila("reserva_confirmada", datos={**CLASE, "tipo_clase": "<script>x</script>"}), "c@gmail.com")
        self.assertNotIn("<script>", m.get_body(("html",)).get_content())

    def test_datos_incompletos_mandan_el_aviso_generico(self):
        with self.assertLogs(level="WARNING"):
            m = correos.armar(fila("factura_nueva", datos={}), "c@gmail.com")
        self.assertIn("Tenés una novedad", m["Subject"])


class Despachador(unittest.TestCase):
    CONFIG = correos.Config("smtp.gmail.com", 465, "u", "p", "cuenta@gmail.com", "ssl", SECRETO)

    def test_sin_pendientes_no_se_conecta(self):
        conectar = mock.Mock()
        self.assertEqual(correos.despachar(AdminFalso([]), self.CONFIG, conectar=conectar)["tomados"], 0)
        conectar.assert_not_called()

    def test_una_conexion_para_todo_el_lote(self):
        smtp, conectar = SmtpFalso(), mock.Mock()
        conectar.return_value = smtp
        admin = AdminFalso([fila("reserva_confirmada", i) for i in range(1, 4)])
        r = correos.despachar(admin, self.CONFIG, conectar=conectar)
        self.assertEqual(r, {"tomados": 3, "enviados": 3, "errores": 0, "liberados": 0})
        conectar.assert_called_once()
        self.assertTrue(smtp.cerrado)
        self.assertEqual([x[1] for x in admin.resultados], ["enviado"] * 3)

    def test_direccion_rechazada_sigue_con_las_demas(self):
        smtp = SmtpFalso(fallar_en=2, error=smtplib.SMTPRecipientsRefused({"a": (550, b"no")}))
        admin = AdminFalso([fila("reserva_confirmada", i) for i in range(1, 4)])
        r = correos.despachar(admin, self.CONFIG, conectar=lambda c: smtp)
        self.assertEqual([x[1] for x in admin.resultados], ["enviado", "error", "enviado"])
        self.assertEqual(r["enviados"], 2)

    def test_limite_de_gmail_corta_el_lote(self):
        error = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")
        smtp = SmtpFalso(fallar_en=2, error=error)
        admin = AdminFalso([fila("reserva_confirmada", i) for i in range(1, 5)])
        r = correos.despachar(admin, self.CONFIG, conectar=lambda c: smtp)
        self.assertEqual([x[1] for x in admin.resultados], ["enviado", "error", "liberar", "liberar"])
        self.assertEqual(r, {"tomados": 4, "enviados": 1, "errores": 1, "liberados": 2})

    def test_credenciales_rechazadas(self):
        def conectar(c):
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        admin = AdminFalso([fila("reserva_confirmada", i) for i in range(1, 4)])
        with self.assertLogs(level="WARNING"):
            r = correos.despachar(admin, self.CONFIG, conectar=conectar)
        self.assertEqual([x[1] for x in admin.resultados], ["error", "liberar", "liberar"])
        self.assertNotIn("p", [x[2] for x in admin.resultados])  # nunca la contraseña


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(main.app)

    def test_sin_configuracion_503(self):
        with mock.patch.dict(os.environ, {"NOTIFICACIONES_SECRETO": ""}):
            self.assertEqual(self.cliente.post("/tareas/notificaciones").status_code, 503)

    def test_secreto_incorrecto_401_y_correcto_despacha(self):
        with mock.patch.dict(os.environ, ENTORNO), mock.patch.object(correos, "despachar", return_value={"tomados": 0}) as d, \
                mock.patch.object(auth, "cliente_admin"):
            self.assertEqual(self.cliente.post("/tareas/notificaciones",
                                               headers={"Authorization": "Bearer otro"}).status_code, 401)
            d.assert_not_called()
            r = self.cliente.post("/tareas/notificaciones", headers={"Authorization": f"Bearer {SECRETO}"})
            self.assertEqual(r.status_code, 200)
            d.assert_called_once()


class PreferenciaPerfil(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(main.app)

    def usuario(self, **kw):
        return Usuario(id="a", mail="ana@ejemplo.com", nombre="Ana", apellido="P", rol="alumna", token="t",
                       empresa_id=1, empresa_nombre="Just Show Up", con_membresias=True, **kw)

    def test_guardar(self):
        sb = mock.Mock()
        sb.rpc.return_value.execute.return_value.data = "ok"
        with mock.patch.object(auth, "_usuario_cacheado", return_value=self.usuario(es_alumna=True)), \
                mock.patch.object(main, "_sb", return_value=sb):
            r = self.cliente.post("/perfil/avisos", data={}, follow_redirects=False)
            sb.rpc.assert_called_with("cambiar_preferencia_correos", {"p_recibir": False})
            self.assertIn("no+vas+a+recibir", r.headers["location"])
            self.cliente.post("/perfil/avisos", data={"recibir": "1"}, follow_redirects=False)
            sb.rpc.assert_called_with("cambiar_preferencia_correos", {"p_recibir": True})

    def test_solo_alumnas(self):
        self.assertIsNone(main._recibe_correos(self.usuario(es_admin=True, es_docente=True)))


if __name__ == "__main__":
    unittest.main()
