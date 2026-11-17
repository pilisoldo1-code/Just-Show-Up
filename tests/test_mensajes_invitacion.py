"""Alta de personas: el aviso dice que se mandó una invitación SOLO cuando se mandó, y el formulario no se envía dos
veces. Sin base ni correos: Supabase y Auth se simulan.

Uso: python -m unittest tests.test_mensajes_invitacion -v
"""
import threading
import time
import unittest
from unittest import mock
from urllib.parse import unquote_plus

from app import auth, main
from tests.test_multiempresa_app import ADMIN, Base

ALTA = {"nombre": "Ana", "apellido": "Uno", "mail": "ana@x.uy", "rol": "alumna"}
ENVIADO = "Se envió un correo de invitación a ana@x.uy"


def aviso(r):
    return unquote_plus(r.headers["location"])


class Mensajes(Base):
    def setUp(self):
        super().setUp()
        self.como(ADMIN)
        for nombre, valor in (("_cedula_en_uso", False),):
            p = mock.patch.object(main, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def existente(self, pendiente):
        self.sb.rpc.return_value.execute.return_value.data = "ok"
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"es_admin": False, "es_profesora": False, "es_alumna": True, "eliminada_en": None}]
        return (mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Ana", "apellido": "Uno"}),
                mock.patch.object(auth, "invitacion_pendiente", return_value=pendiente),
                mock.patch.object(auth, "invitar_cuenta"))

    # 1. alumna nueva (y profesora nueva)
    def test_alumna_nueva_dice_a_quien_se_envio(self):
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(auth, "invitar_cuenta", return_value="nuevo") as invitar:
            r = self.post("/profesora/alumnas", ALTA)
        invitar.assert_called_once()
        self.assertEqual(r.status_code, 303)
        self.assertIn(ENVIADO + " para que la alumna configure su contraseña y acceda a Just Show Up", aviso(r))
        self.assertIn("ok=1", r.headers["location"])

    def test_profesora_nueva(self):
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(auth, "invitar_cuenta", return_value="nuevo"):
            r = self.post("/profesora/alumnas", {**ALTA, "rol": "profesora"})
        self.assertIn("para que la profesora configure su contraseña", aviso(r))

    # 2. correo de una cuenta eliminada: liberado → invitación nueva; sin liberar → error, sin correo
    def test_correo_de_eliminada_ya_liberado_invita_de_nuevo(self):
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(auth, "invitar_cuenta", return_value="nuevo") as invitar:
            r = self.post("/profesora/alumnas", ALTA)
        invitar.assert_called_once()
        self.assertIn(ENVIADO, aviso(r))

    def test_correo_de_eliminada_sin_liberar_no_dice_que_envio(self):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"es_admin": False, "es_profesora": False, "es_alumna": True, "eliminada_en": "2026-11-17T17:00:00+00:00"}]
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": "x", "nombre": "Ana", "apellido": "Uno"}), \
                mock.patch.object(auth, "invitar_cuenta") as invitar:
            r = self.post("/profesora/alumnas", ALTA)
        invitar.assert_not_called()
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("Se envió un correo", r.text)

    # 5. invitación pendiente (incluye el segundo envío de un formulario que se mandó dos veces)
    def test_invitacion_pendiente(self):
        a, b, c = self.existente(pendiente=True)
        with a, b, c as invitar:
            r = self.post("/profesora/alumnas", ALTA)
        invitar.assert_not_called()
        self.assertIn("Ya se envió una invitación a ana@x.uy. La alumna todavía no configuró su contraseña.", aviso(r))
        self.assertNotIn("Se envió un correo", aviso(r))

    def test_invitacion_pendiente_profesora(self):
        a, b, c = self.existente(pendiente=True)
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"es_admin": False, "es_profesora": True, "es_alumna": False, "eliminada_en": None}]
        with a, b, c:
            r = self.post("/profesora/alumnas", {**ALTA, "rol": "profesora"})
        self.assertIn("La profesora todavía no configuró su contraseña.", aviso(r))

    # 6. cuenta activa
    def test_cuenta_activa(self):
        a, b, c = self.existente(pendiente=False)
        with a, b, c as invitar:
            r = self.post("/profesora/alumnas", ALTA)
        invitar.assert_not_called()
        self.assertIn("La alumna ya tiene una cuenta activa y quedó registrada correctamente. "
                      "Puede ingresar con su correo y contraseña.", aviso(r))

    # 7. nunca dice «se envió» si no se envió: error de Auth, error inesperado, o no se sabe si está pendiente
    def test_error_al_invitar_no_dice_que_envio(self):
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(auth, "invitar_cuenta", side_effect=auth.ErrorAuth("Se enviaron muchos correos")):
            r = self.post("/profesora/alumnas", ALTA)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("Se envió un correo", r.text)
        self.assertIn('data-cargando="Enviando invitación…"', r.text)  # el formulario vuelve, listo para reintentar

    def test_error_inesperado_no_dice_que_envio(self):
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(auth, "invitar_cuenta", side_effect=RuntimeError("caído")), \
                self.assertLogs(level="ERROR"):
            r = self.post("/profesora/alumnas", ALTA)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("Se envió un correo", r.text)

    def test_si_no_se_puede_saber_si_esta_pendiente_no_inventa_un_envio(self):
        a, _, c = self.existente(pendiente=False)
        with a, c, mock.patch.object(auth, "invitacion_pendiente", side_effect=RuntimeError("Auth caído")), \
                self.assertLogs(level="WARNING"):
            r = self.post("/profesora/alumnas", ALTA)
        self.assertEqual(r.status_code, 303)
        self.assertNotIn("Se envió un correo", aviso(r))
        self.assertNotIn("Ya se envió", aviso(r))

    # 3 y 4. formulario con un solo envío; invitación lenta (más de 10 s) y un segundo envío mientras tanto
    def test_formulario_tiene_proteccion_de_doble_envio(self):
        r = self.get("/profesora/alumnas/nueva")
        self.assertIn('data-cargando="Enviando invitación…"', r.text)
        self.assertIn("form.dataset.enviando", r.text)
        self.assertIn("boton.disabled = true", r.text)

    def test_invitacion_lenta_y_segundo_envio_en_paralelo(self):
        """Simula el caso del 17/11: la invitación tarda y llega un segundo envío. El segundo encuentra la cuenta
        pendiente y NO dice que mandó otro correo; se manda una sola invitación."""
        creada = threading.Event()
        llamadas = []

        def invitar_lento(*a, **k):
            llamadas.append(1)
            creada.set()          # la cuenta ya existe en Auth mientras el correo todavía «se manda»
            time.sleep(1.5)       # (escala reducida de los >10 s reales)
            return "nuevo"

        def cuenta(_mail):
            return {"id": "nuevo", "nombre": "Ana", "apellido": "Uno"} if creada.is_set() else None

        self.sb.rpc.return_value.execute.return_value.data = "ok"
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"es_admin": False, "es_profesora": False, "es_alumna": True, "eliminada_en": None}]
        resultados = {}
        with mock.patch.object(main, "_cuenta_existente", side_effect=cuenta), \
                mock.patch.object(auth, "invitar_cuenta", side_effect=invitar_lento), \
                mock.patch.object(auth, "invitacion_pendiente", return_value=True):
            primero = threading.Thread(target=lambda: resultados.setdefault("1", self.post("/profesora/alumnas", ALTA)))
            primero.start()
            creada.wait(5)
            resultados["2"] = self.post("/profesora/alumnas", ALTA)
            primero.join(10)
        self.assertEqual(len(llamadas), 1)
        self.assertIn(ENVIADO, aviso(resultados["1"]))
        self.assertIn("Ya se envió una invitación a ana@x.uy", aviso(resultados["2"]))
        self.assertNotIn("Se envió un correo", aviso(resultados["2"]))


if __name__ == "__main__":
    unittest.main()
