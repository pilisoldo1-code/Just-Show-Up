"""Baja de docentes: vista previa y confirmación (administradora para profesoras, superadministradora para
administradoras), permisos del servidor y los caminos viejos que ahora pasan por la vista previa. La base se simula;
las reglas de la base se prueban en tests/base_local/test_baja_docentes_base.py.

Uso: python -m unittest tests.test_baja_docentes -v
"""
import unittest
from unittest import mock

from app import main
from tests.test_multiempresa_app import ADMIN, PROFE, SUPER, Base

ID = "22222222-2222-4222-8222-222222222222"
PREVIA = {"resultado": "ok", "firma": "f1", "reservas": 2, "alumnas": 2, "nombres": ["Ana Uno", "Eva Dos"], "espera": 1,
          "en_curso": 1, "horarios": [{"id": 5, "dia_semana": "sabado", "hora_inicio": "10:00", "tipo": "Pilates"}],
          "clases": [{"id": 9, "fecha": "2026-11-07", "hora_inicio": "10:00", "tipo": "Pilates", "reservas": 2, "espera": 1}]}


class BajaProfesora(Base):
    MEMB = {"id_usuario": ID, "es_admin": False, "es_profesora": True, "es_alumna": False, "activa": True}

    def preparar(self, **respuestas):
        t = self.sb.table.return_value.select.return_value
        t.eq.return_value.eq.return_value.execute.return_value.data = [self.MEMB]
        t.eq.return_value.execute.return_value.data = [{"id": ID, "nombre": "Pia", "apellido": "Dos", "mail": "p@x.uy",
                                                       "telefono": None}]
        self.respuestas = {"datos_personales": [], "pendientes_cambio_rol": [], "previsualizar_baja_docente": PREVIA,
                           **respuestas}
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(
            **{"execute.return_value.data": self.respuestas.get(nombre, "ok")})

    def llamadas(self):
        return [c.args[0] for c in self.sb.rpc.call_args_list]

    def test_vista_previa_muestra_lo_que_se_cancela_sin_cambiar_nada(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get(f"/profesora/profesoras/{ID}/baja")
        self.assertEqual(r.status_code, 200)
        for texto in ("Dar de baja a Pia Dos", "Sábado 10:00 · Pilates", "Sábado 7/11", "Ana Uno, Eva Dos",
                      "la clase que está en curso", 'name="firma" value="f1"', "Confirmar baja", "No se puede deshacer"):
            self.assertIn(texto, r.text)
        self.assertNotIn("dar_de_baja_docente", self.llamadas())

    def test_confirmar(self):
        self.como(ADMIN)
        self.preparar(dar_de_baja_docente={"resultado": "ok", "horarios": 1, "clases": 1, "reservas": 2, "espera": 1})
        r = self.post(f"/profesora/profesoras/{ID}/baja", {"motivo": "Se mudó", "firma": "f1"})
        self.sb.rpc.assert_any_call("dar_de_baja_docente", {"p_id_usuario": ID, "p_motivo": "Se mudó", "p_firma": "f1"})
        self.assertTrue(r.headers["location"].startswith(f"/profesora/profesoras/{ID}?ok=1"))
        self.assertIn("1+clase+cancelada", r.headers["location"])

    def test_si_algo_cambio_vuelve_a_la_vista_previa_actualizada(self):
        self.como(ADMIN)
        self.preparar(dar_de_baja_docente={"resultado": "cambio"})
        r = self.post(f"/profesora/profesoras/{ID}/baja", {"motivo": "Se mudó", "firma": "vieja"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Algo cambió desde que abriste esta pantalla", r.text)
        self.assertIn('name="firma" value="f1"', r.text)  # la firma nueva
        self.assertIn('value="Se mudó"', r.text)  # no se pierde el motivo

    def test_solo_la_administradora(self):
        for quien in (PROFE, SUPER):
            with self.subTest(quien=quien.nombre):
                self.como(quien)
                self.preparar()
                self.assertEqual(self.get(f"/profesora/profesoras/{ID}/baja").status_code, 303)
                self.post(f"/profesora/profesoras/{ID}/baja", {"motivo": "x", "firma": "f1"})
                self.assertNotIn("dar_de_baja_docente", self.llamadas())

    def test_la_base_rechaza_y_se_explica(self):
        self.como(ADMIN)
        self.preparar(previsualizar_baja_docente={"resultado": "es_administradora"})
        r = self.get(f"/profesora/profesoras/{ID}/baja")
        self.assertIn("administraci%C3%B3n+general", r.headers["location"])

    def test_dejar_inactiva_con_pendientes_lleva_a_la_baja(self):
        self.como(ADMIN)
        self.preparar(cambiar_estado_membresia="pendientes:horarios,clases_futuras")
        r = self.post(f"/profesora/profesoras/{ID}/estado", {"activa": 0})
        self.assertEqual(r.headers["location"], f"/profesora/profesoras/{ID}/baja")

    def test_solo_desde_profesoras(self):
        self.como(ADMIN)
        self.preparar()
        r = self.get(f"/profesora/alumnas/{ID}/baja")
        self.assertIn("ok=0", r.headers["location"])
        self.assertNotIn("previsualizar_baja_docente", self.llamadas())


class BajaAdministradora(Base):
    ADM = {"id": "11111111-1111-4111-8111-111111111111", "nombre": "Lia", "apellido": "Paz", "mail": "lia@prueba.invalid",
           "activa": True, "pendientes": []}

    def responder(self, **respuestas):
        respuestas.setdefault("administradoras_empresa", [self.ADM])
        respuestas.setdefault("previsualizar_baja_administradora", {**PREVIA, "nombres": []})
        self.sb.rpc.side_effect = lambda nombre, params: mock.MagicMock(**{"execute.return_value.data": respuestas.get(nombre)})

    def url(self):
        return f"/admin/empresas/2/administradoras/{self.ADM['id']}/baja"

    def test_vista_previa_y_confirmacion(self):
        self.como(SUPER)
        self.responder(dar_de_baja_administradora={"resultado": "ok", "horarios": 1, "clases": 1, "reservas": 2, "espera": 0})
        r = self.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertIn("Deja de ser administradora", r.text)
        self.assertIn("2 alumnas afectadas.", r.text)  # cantidades, sin nombres
        r = self.post(self.url(), {"motivo": "Cambio de dirección", "firma": "f1"})
        self.sb.rpc.assert_any_call("dar_de_baja_administradora", {"p_id_empresa": 2, "p_id_usuario": self.ADM["id"],
                                                                   "p_motivo": "Cambio de dirección", "p_firma": "f1"})
        self.assertTrue(r.headers["location"].startswith("/admin/empresas/2?ok=1"))

    def test_nunca_la_ultima(self):
        self.como(SUPER)
        self.responder(previsualizar_baja_administradora={"resultado": "ultima_administradora"})
        self.assertIn("ninguna+administradora", self.get(self.url()).headers["location"])

    def test_la_administradora_comun_no_entra(self):
        self.como(ADMIN)
        self.responder()
        self.assertEqual(self.get(self.url()).status_code, 303)
        self.post(self.url(), {"motivo": "x", "firma": "f1"})
        self.assertNotIn("dar_de_baja_administradora", [c.args[0] for c in self.sb.rpc.call_args_list])


class HorariosDeInactivas(Base):
    def test_activar_un_horario_de_una_inactiva_se_explica(self):
        self.como(ADMIN)
        self.sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "activo": False, "id_profesora": "x", "tipo_clase": {"activo": True, "nombre": "Pilates"}}
        self.sb.table.return_value.update.return_value.eq.return_value.execute.side_effect = Exception(
            "{'message': 'profesora_inactiva', 'code': 'P0001'}")
        r = self.post("/profesora/horarios/7/toggle", {})
        self.assertIn("profesora+inactiva", r.headers["location"])

    def test_solo_docentes_activas_para_elegir(self):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"id_usuario": "a", "es_admin": False, "es_profesora": True, "activa": True},
            {"id_usuario": "c", "es_admin": False, "es_profesora": False, "activa": True}]
        with mock.patch.object(main.personas, "nombres_profesoras", return_value={"a": "Ana", "b": "Bea (inactiva)"}):
            self.assertEqual(main._docentes_activas(self.sb, ADMIN), {"a": "Ana"})


if __name__ == "__main__":
    unittest.main()
