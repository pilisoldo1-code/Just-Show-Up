"""«Eliminar alumna» (distinto de «Dejar inactiva»): vista previa, confirmación, archivado en Supabase Auth con reintento,
listado y ficha sin la eliminada, alta con el mismo correo y permisos. La base y Auth se simulan; las reglas reales se
prueban en tests/supabase_local/prueba_eliminar_alumna_reales.py.

Uso: python -m unittest tests.test_eliminar_alumna -v
"""
import unittest
from unittest import mock

from app import auth, main
from tests.test_multiempresa_app import ADMIN, PROFE, Base

ID = "33333333-3333-4333-8333-333333333333"
PREVIA = {"resultado": "ok", "firma": "f1",
          "reservas": [{"fecha": "2026-11-05", "hora_inicio": "18:00", "tipo": "Pilates"}],
          "espera": [{"fecha": "2026-11-06", "hora_inicio": "19:00", "tipo": "Yoga", "estado": "ofrecido"}],
          "facturas_pendientes": [{"numero": "JSU-2026-10-0002", "mes": 10, "anio": 2026, "total": "1200.00"}],
          "historial": {"reservas": 12, "asistencias": 9, "facturas": 2, "pagos": 1}}


class Eliminar(Base):
    MEMB = {"id_usuario": ID, "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": True, "eliminada_en": None}

    def preparar(self, memb=None, **respuestas):
        t = self.sb.table.return_value.select.return_value
        t.eq.return_value.eq.return_value.execute.return_value.data = [memb or self.MEMB]
        t.eq.return_value.execute.return_value.data = [{"id": ID, "nombre": "Ana", "apellido": "Uno", "mail": "ana@x.uy",
                                                       "telefono": None}]
        self.respuestas = {"datos_personales": [], "pendientes_cambio_rol": [], "previsualizar_eliminar_alumna": PREVIA,
                           "eliminar_alumna": {"resultado": "ok", "registro": 7, "reservas": 1, "espera": 1}, **respuestas}
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(
            **{"execute.return_value.data": self.respuestas.get(nombre, "ok")})

    def llamadas(self):
        return [c.args[0] for c in self.sb.rpc.call_args_list]

    def test_vista_previa_muestra_todo_y_no_cambia_nada(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get(f"/profesora/alumnas/{ID}/eliminar")
        self.assertEqual(r.status_code, 200)
        for texto in ("Eliminar a Ana Uno", "Jueves 05/11 · 18:00", "Viernes 06/11 · 19:00", "tenía un lugar ofrecido",
                      "JSU-2026-10-0002", "$1.200,00", "12 reservas", "9 asistencias registradas", "2 facturas", "1 pago",
                      "No se puede deshacer", 'name="firma" value="f1"', "ana@x.uy"):
            self.assertIn(texto, r.text)
        self.assertNotIn("eliminar_alumna", self.llamadas())

    def test_otra_empresa_no_se_puede_eliminar(self):
        self.como(ADMIN)
        self.preparar(previsualizar_eliminar_alumna={"resultado": "otra_empresa"})
        r = self.get(f"/profesora/alumnas/{ID}/eliminar")
        self.assertEqual(r.status_code, 303)
        self.assertIn("otro+lugar", r.headers["location"])

    def test_confirmar_elimina_archiva_y_libera_el_correo(self):
        self.como(ADMIN)
        self.preparar()
        with mock.patch.object(auth, "archivar_cuenta") as archivar, \
                mock.patch.object(auth, "cliente_admin") as admin:
            admin.return_value.rpc.return_value.execute.return_value.data = "ok"
            r = self.post(f"/profesora/alumnas/{ID}/eliminar", {"motivo": "Pidió la baja", "firma": "f1"})
        archivar.assert_called_once_with(ID)
        admin.return_value.rpc.assert_called_with("alumna_eliminada_correo_liberado", {"p_id": 7})
        self.assertEqual(r.status_code, 303)
        self.assertIn("ok=1", r.headers["location"])
        self.assertIn("invitaci%C3%B3n+como+alumna+nueva", r.headers["location"])

    def test_si_falla_el_archivado_no_se_presenta_como_terminada(self):
        self.como(ADMIN)
        self.preparar()
        with mock.patch.object(auth, "archivar_cuenta", side_effect=auth.ErrorAuth("caído")), \
                self.assertLogs(level="ERROR"):
            r = self.post(f"/profesora/alumnas/{ID}/eliminar", {"motivo": "x", "firma": "f1"})
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("NO+est%C3%A1+terminada", r.headers["location"])

    def test_cambio_desde_la_vista_previa_vuelve_a_mostrarla(self):
        self.como(ADMIN)
        self.preparar(eliminar_alumna={"resultado": "cambio"})
        with mock.patch.object(auth, "archivar_cuenta") as archivar:
            r = self.post(f"/profesora/alumnas/{ID}/eliminar", {"motivo": "x", "firma": "vieja"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Algo cambió", r.text)
        archivar.assert_not_called()

    def test_reintentar_libera_el_correo_pendiente(self):
        self.como(ADMIN)
        t = self.sb.table.return_value.select.return_value
        t.eq.return_value.eq.return_value.execute.return_value.data = [
            {"id": 7, "id_usuario": ID, "nombre": "Ana", "apellido": "Uno", "correo_liberado_en": None}]
        with mock.patch.object(auth, "archivar_cuenta") as archivar, mock.patch.object(auth, "cliente_admin") as admin:
            admin.return_value.rpc.return_value.execute.return_value.data = "ok"
            r = self.post("/profesora/alumnas/eliminadas/7/reintentar", {})
        archivar.assert_called_once_with(ID)
        self.assertIn("ok=1", r.headers["location"])

    def test_una_profesora_no_puede_eliminar(self):
        self.como(PROFE)
        self.preparar()
        r = self.post(f"/profesora/alumnas/{ID}/eliminar", {"motivo": "x", "firma": "f1"})
        self.assertNotIn("eliminar_alumna", self.llamadas())
        self.assertEqual(r.status_code, 303)

    def test_la_eliminada_no_tiene_ficha(self):
        self.como(ADMIN)
        self.preparar(memb={**self.MEMB, "activa": False, "eliminada_en": "2026-11-16T20:00:00+00:00"})
        r = self.get(f"/profesora/alumnas/{ID}")
        self.assertEqual(r.status_code, 303)
        self.assertIn("No+encontramos", r.headers["location"])

    def test_la_ficha_ofrece_inactivar_y_eliminar(self):
        self.como(ADMIN)
        self.preparar()
        with mock.patch.object(main, "_invitacion_pendiente", return_value=False):
            r = self.get(f"/profesora/alumnas/{ID}")
        self.assertIn("Dejar inactiva", r.text)
        self.assertIn(f'href="/profesora/alumnas/{ID}/eliminar"', r.text)


class Listado(Base):
    def test_las_eliminadas_no_aparecen_ni_activas_ni_inactivas(self):
        self.como(ADMIN)
        t = self.sb.table.return_value.select.return_value
        t.eq.return_value.execute.return_value.data = [
            {"id_usuario": "a1", "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": True, "eliminada_en": None},
            {"id_usuario": "a2", "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": False, "eliminada_en": None},
            {"id_usuario": "a3", "es_admin": False, "es_profesora": False, "es_alumna": True, "activa": False,
             "eliminada_en": "2026-11-16T20:00:00+00:00"}]
        t.in_.return_value.execute.return_value.data = [
            {"id": i, "nombre": n, "apellido": "Z", "mail": f"{i}@x.uy", "telefono": None}
            for i, n in (("a1", "Activa"), ("a2", "Inactiva"), ("a3", "Eliminada"))]
        filas = main._personas_empresa(ADMIN, "alumnas")
        self.assertEqual([f["nombre"] for f in filas], ["Activa", "Inactiva"])


class Archivo(unittest.TestCase):
    def test_correo_archivado_es_unico_y_ficticio(self):
        m = auth.mail_archivado("33333333-3333-4333-8333-333333333333")
        self.assertEqual(m, "eliminada-33333333333343338333333333333333@cuentas-eliminadas.invalid")
        self.assertTrue(auth.es_mail_ficticio(m))

    def test_archivar_usa_la_via_del_administrador_y_bloquea(self):
        with mock.patch.object(auth, "cliente_admin") as admin:
            nuevo = auth.archivar_cuenta(ID)
        admin.return_value.auth.admin.update_user_by_id.assert_called_once_with(
            ID, {"email": nuevo, "email_confirm": True, "ban_duration": auth.BLOQUEO_ARCHIVO})


class Alta(Base):
    def test_correo_de_eliminada_sin_liberar_no_reactiva_la_cuenta(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_cuenta_existente", return_value={"id": ID, "nombre": "Ana", "apellido": "Uno",
                                                                       "es_superadmin": False}), \
                mock.patch.object(main, "_cedula_en_uso", return_value=False), \
                mock.patch.object(auth, "invitar_cuenta") as invitar:
            t = self.sb.table.return_value.select.return_value
            t.eq.return_value.eq.return_value.execute.return_value.data = [
                {"es_admin": False, "es_profesora": False, "es_alumna": True, "eliminada_en": "2026-11-16T20:00:00+00:00"}]
            r = self.post("/profesora/alumnas", {"nombre": "Ana", "apellido": "Uno", "mail": "ana@x.uy", "rol": "alumna"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("todavía no quedó libre", r.text)
        invitar.assert_not_called()
        self.assertNotIn("asignar_membresia", [c.args[0] for c in self.sb.rpc.call_args_list])

    def test_correo_libre_crea_cuenta_nueva_con_invitacion(self):
        self.como(ADMIN)
        with mock.patch.object(main, "_cuenta_existente", return_value=None), \
                mock.patch.object(main, "_cedula_en_uso", return_value=False), \
                mock.patch.object(auth, "invitar_cuenta", return_value="nuevo-id") as invitar:
            r = self.post("/profesora/alumnas", {"nombre": "Ana", "apellido": "Uno", "mail": "ana@x.uy", "rol": "alumna"})
        invitar.assert_called_once()
        self.assertEqual(r.status_code, 303)
        self.assertIn("invitaci%C3%B3n", r.headers["location"])


if __name__ == "__main__":
    unittest.main()
