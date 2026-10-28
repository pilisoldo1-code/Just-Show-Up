"""Mercado Pago (solo prueba): lógica del servidor y rutas, con Mercado Pago y Supabase simulados.
No hace ninguna llamada real. Uso: python -m unittest tests.test_mercadopago -v
"""
import hashlib
import hmac
import logging
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, facturas, main, mercadopago
from app.auth import Usuario

TOKEN = "APP_USR-123456-token-de-prueba-que-nunca-debe-aparecer"
VENDEDORA = "1111111111"
COMPRADORA = "2222222222"
LOGO_SHA256 = "91eebaceaf93eedd"
SOFIA = Usuario(id="s", mail="sofia@demo.invalid", nombre="Sofía", apellido="Demo", rol="alumna", token="t")
REAL = Usuario(id="r", mail="alguien@gmail.com", nombre="Real", apellido="Persona", rol="alumna", token="t")


class Respuesta:
    def __init__(self, datos, codigo=200):
        self.datos, self.status_code = datos, codigo

    def json(self):
        return self.datos

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class MercadoPagoFalso:
    """Imita la API de Mercado Pago con cuentas de prueba."""

    def __init__(self, vendedora_de_prueba=True):
        self.yo = {"id": int(VENDEDORA), "site_id": "MLU",
                   "tags": ["user_product_seller", "test_user", "normal"] if vendedora_de_prueba else ["normal"]}
        self.usuarios = {COMPRADORA: {"id": int(COMPRADORA), "nickname": "TESTUSER123"},
                         "777": {"id": 777, "nickname": "PERSONA_REAL"}}
        self.pagos = {}
        self.busquedas = {}
        self.preferencias = []
        self.link = "https://www.mercadopago.com.uy/checkout/v1/redirect?pref_id=pref-1"
        self.llamadas = []

    def pago(self, id_pago, estado="approved", monto=1600, moneda="UYU", referencia="JSU-1-abc",
             collector=VENDEDORA, pagador=COMPRADORA):
        self.pagos[str(id_pago)] = {"id": int(id_pago), "status": estado, "transaction_amount": monto,
                                    "currency_id": moneda, "external_reference": referencia,
                                    "collector_id": int(collector), "payer": {"id": int(pagador)},
                                    "date_approved": "2026-11-02T03:30:00.000-04:00", "live_mode": True}

    def get(self, url, headers=None, params=None, timeout=None):
        self.llamadas.append(("GET", url, headers))
        ruta = url.replace(mercadopago.API, "")
        if ruta == "/users/me":
            return Respuesta(self.yo)
        if ruta.startswith("/users/"):
            u = self.usuarios.get(ruta.split("/")[-1])
            return Respuesta(u or {}, 200 if u else 404)
        if ruta == "/v1/payments/search":
            return Respuesta({"results": self.busquedas.get(params["external_reference"], [])})
        if ruta.startswith("/v1/payments/"):
            p = self.pagos.get(ruta.split("/")[-1])
            return Respuesta(p or {}, 200 if p else 404)
        return Respuesta({}, 404)

    def post(self, url, json=None, headers=None, timeout=None):
        self.llamadas.append(("POST", url, headers))
        self.preferencias.append(json)
        return Respuesta({"id": "pref-1", "init_point": self.link})


class AdminFalso:
    """Imita las funciones de servidor de Supabase (service_role)."""

    def __init__(self, habilitado=True, token=TOKEN, secreto=None, registro=None):
        self.config = {"habilitado": habilitado, "access_token": token, "webhook_secret": secreto}
        self.registro = registro or {"resultado": "ok", "id_pago": 1}
        self.rpcs = []

    def rpc(self, nombre, params):
        self.rpcs.append((nombre, params))
        datos = {"mp_configuracion_servidor": self.config, "registrar_pago_mercado_pago": self.registro}.get(nombre)
        return mock.Mock(execute=mock.Mock(return_value=mock.Mock(data=datos)))

    def llamadas(self, nombre):
        return [p for n, p in self.rpcs if n == nombre]


class AlumnaFalsa:
    """Imita la sesión de la alumna (crear_intento_mp y lectura de sus intentos)."""

    def __init__(self, intento=None, intentos=()):
        self.intento = intento or {"resultado": "ok", "id_intento": 9, "referencia": "JSU-1-abc", "monto": 1600,
                                   "numero": "JSU-2026-10-0001", "mes": 10, "anio": 2026}
        self.intentos = list(intentos)
        self.rpcs = []

    def rpc(self, nombre, params):
        self.rpcs.append((nombre, params))
        return mock.Mock(execute=mock.Mock(return_value=mock.Mock(data=self.intento)))

    def table(self, nombre):
        consulta = mock.MagicMock()
        for metodo in ("select", "eq", "gte", "order"):
            getattr(consulta, metodo).return_value = consulta
        consulta.execute.return_value = mock.Mock(data=self.intentos)
        return consulta


class Base(unittest.TestCase):
    def preparar(self, mp=None, admin=None):
        mercadopago.limpiar_cache()
        self.mp = mp or MercadoPagoFalso()
        self.admin = admin or AdminFalso()
        for objeto, nombre, valor in ((mercadopago.httpx, "get", self.mp.get), (mercadopago.httpx, "post", self.mp.post)):
            p = mock.patch.object(objeto, nombre, side_effect=valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(auth, "cliente_admin", return_value=self.admin)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(mercadopago.limpiar_cache)

    def setUp(self):
        self.preparar()


class Disponibilidad(Base):
    def test_disponible_para_alumna_ficticia(self):
        self.assertTrue(mercadopago.disponible_para("sofia@demo.invalid"))
        self.assertTrue(mercadopago.disponible_para("ana@prueba.invalid"))

    def test_nunca_para_alumnas_con_mail_real(self):
        self.assertFalse(mercadopago.disponible_para("alguien@gmail.com"))
        self.assertFalse(mercadopago.disponible_para("sofia@demo.invalid.com"))

    def test_apagado(self):
        self.preparar(admin=AdminFalso(habilitado=False))
        self.assertFalse(mercadopago.disponible_para("sofia@demo.invalid"))

    def test_sin_token(self):
        self.preparar(admin=AdminFalso(token=None))
        self.assertFalse(mercadopago.disponible_para("sofia@demo.invalid"))

    def test_credencial_que_no_es_de_prueba_lo_apaga(self):
        self.preparar(mp=MercadoPagoFalso(vendedora_de_prueba=False))
        self.assertFalse(mercadopago.disponible_para("sofia@demo.invalid"))

    def test_si_la_base_falla_queda_apagado(self):
        admin = AdminFalso()
        admin.rpc = mock.Mock(side_effect=RuntimeError("sin conexión"))
        self.preparar(admin=admin)
        self.assertFalse(mercadopago.disponible_para("sofia@demo.invalid"))

    def test_sitio_publico_solo_sitios_conocidos(self):
        self.assertEqual(mercadopago.sitio_publico("just-show-up-theta.vercel.app"), "https://just-show-up-theta.vercel.app")
        self.assertIsNone(mercadopago.sitio_publico("localhost"))
        self.assertIsNone(mercadopago.sitio_publico("sitio-malicioso.com"))


class IniciarPago(Base):
    def test_crea_la_preferencia_con_el_importe_de_la_base(self):
        alumna = AlumnaFalsa()
        ok, link = mercadopago.iniciar_pago(alumna, SOFIA.mail, 1, "https://just-show-up-theta.vercel.app")
        self.assertTrue(ok)
        self.assertEqual(link, self.mp.link)
        self.assertEqual(alumna.rpcs, [("crear_intento_mp", {"p_id_resumen": 1})])
        pref = self.mp.preferencias[0]
        self.assertEqual(pref["items"][0]["unit_price"], 1600.0)
        self.assertEqual(pref["items"][0]["currency_id"], "UYU")
        self.assertEqual(pref["external_reference"], "JSU-1-abc")
        self.assertTrue(pref["binary_mode"])
        self.assertEqual(pref["payment_methods"]["excluded_payment_types"], [{"id": "ticket"}])
        self.assertTrue(pref["expires"])
        self.assertEqual(pref["back_urls"]["success"], "https://just-show-up-theta.vercel.app/mis-facturas/mercado-pago/vuelta")
        self.assertEqual(pref["auto_return"], "approved")
        self.assertTrue(pref["notification_url"].startswith("https://just-show-up-theta.vercel.app/webhooks/mercado-pago"))
        self.assertIn("PRUEBA", pref["items"][0]["title"])
        self.assertEqual(self.admin.llamadas("mp_guardar_preferencia"), [{"p_id_intento": 9, "p_preference_id": "pref-1"}])

    def test_en_la_compu_sin_vuelta_automatica(self):
        mercadopago.iniciar_pago(AlumnaFalsa(), SOFIA.mail, 1, None)
        pref = self.mp.preferencias[0]
        self.assertNotIn("back_urls", pref)
        self.assertNotIn("notification_url", pref)

    def test_factura_ya_pagada_no_llama_a_mercado_pago(self):
        ok, texto = mercadopago.iniciar_pago(AlumnaFalsa({"resultado": "ya_pagada"}), SOFIA.mail, 1, None)
        self.assertFalse(ok)
        self.assertEqual(texto, "Esa factura ya figura como pagada.")
        self.assertEqual(self.mp.preferencias, [])

    def test_alumna_real_ni_siquiera_crea_intento(self):
        alumna = AlumnaFalsa()
        ok, _ = mercadopago.iniciar_pago(alumna, REAL.mail, 1, None)
        self.assertFalse(ok)
        self.assertEqual(alumna.rpcs, [])

    def test_apagado_no_crea_intento(self):
        self.preparar(admin=AdminFalso(habilitado=False))
        alumna = AlumnaFalsa()
        ok, _ = mercadopago.iniciar_pago(alumna, SOFIA.mail, 1, None)
        self.assertFalse(ok)
        self.assertEqual(alumna.rpcs, [])

    def test_link_que_no_es_de_mercado_pago_no_se_usa(self):
        self.mp.link = "https://otro-sitio.com/pagar"
        ok, _ = mercadopago.iniciar_pago(AlumnaFalsa(), SOFIA.mail, 1, None)
        self.assertFalse(ok)

    def test_el_token_va_solo_en_el_encabezado(self):
        mercadopago.iniciar_pago(AlumnaFalsa(), SOFIA.mail, 1, None)
        metodo, url, encabezados = [x for x in self.mp.llamadas if x[0] == "POST"][0]
        self.assertNotIn(TOKEN, url)
        self.assertEqual(encabezados["Authorization"], f"Bearer {TOKEN}")


class VerificarPago(Base):
    def test_aprobado_entre_cuentas_de_prueba_se_registra_con_los_datos_de_mercado_pago(self):
        self.mp.pago(5001)
        self.assertEqual(mercadopago.verificar_pago("5001", "vuelta"), "ok")
        (params,) = self.admin.llamadas("registrar_pago_mercado_pago")
        self.assertEqual(params, {"p_referencia": "JSU-1-abc", "p_id_pago_mp": "5001", "p_estado": "approved",
                                  "p_monto": "1600", "p_moneda": "UYU", "p_fecha": "2026-11-02"})

    def test_cobrado_por_otra_cuenta_no_se_registra(self):
        self.mp.pago(5002, collector="123")
        self.assertEqual(mercadopago.verificar_pago("5002", "aviso"), "no_es_prueba")
        self.assertEqual(self.admin.llamadas("registrar_pago_mercado_pago"), [])

    def test_pagado_por_una_cuenta_real_no_se_registra(self):
        self.mp.pago(5003, pagador="777")
        self.assertEqual(mercadopago.verificar_pago("5003", "aviso"), "no_es_prueba")
        self.assertEqual(self.admin.llamadas("registrar_pago_mercado_pago"), [])

    def test_referencia_ajena_no_se_registra(self):
        self.mp.pago(5004, referencia="OTRA-COSA")
        self.assertEqual(mercadopago.verificar_pago("5004", "aviso"), "intento_desconocido")
        self.assertEqual(self.admin.llamadas("registrar_pago_mercado_pago"), [])

    def test_id_invalido_no_consulta_nada(self):
        self.assertEqual(mercadopago.verificar_pago("../users/me", "vuelta"), "error")
        self.assertEqual(self.mp.llamadas, [])

    def test_pago_inexistente(self):
        self.assertEqual(mercadopago.verificar_pago("9999", "vuelta"), "error")

    def test_si_la_vendedora_no_es_de_prueba_no_se_consulta_el_pago(self):
        self.preparar(mp=MercadoPagoFalso(vendedora_de_prueba=False))
        self.mp.pago(5005)
        self.assertEqual(mercadopago.verificar_pago("5005", "vuelta"), "no_disponible")
        self.assertFalse(any("/v1/payments/" in url for _, url, _ in self.mp.llamadas))

    def test_rechazado(self):
        self.preparar(admin=AdminFalso(registro={"resultado": "rechazado"}))
        self.mp.pago(5006, estado="rejected")
        self.assertEqual(mercadopago.verificar_pago("5006", "vuelta"), "rechazado")

    def test_se_guarda_un_evento_sin_datos_sensibles(self):
        self.mp.pago(5007)
        mercadopago.verificar_pago("5007", "aviso", "valida")
        (evento,) = self.admin.llamadas("mp_registrar_evento")
        self.assertEqual(evento["p_id_recurso"], "5007")
        self.assertNotIn(TOKEN, str(evento))


class Vuelta(Base):
    def test_sin_pago(self):
        self.assertEqual(mercadopago.resultado_vuelta(""), mercadopago.MENSAJE_SIN_PAGO)
        self.assertEqual(mercadopago.resultado_vuelta("null"), mercadopago.MENSAJE_SIN_PAGO)

    def test_aprobado(self):
        self.mp.pago(6001)
        ok, texto = mercadopago.resultado_vuelta("6001")
        self.assertTrue(ok)
        self.assertIn("pagada", texto)

    def test_rechazado(self):
        self.preparar(admin=AdminFalso(registro={"resultado": "rechazado"}))
        self.mp.pago(6002, estado="rejected")
        ok, texto = mercadopago.resultado_vuelta("6002")
        self.assertFalse(ok)
        self.assertIn("rechazado", texto)

    def test_no_se_pudo_verificar(self):
        ok, texto = mercadopago.resultado_vuelta("6003")
        self.assertFalse(ok)
        self.assertIn("verificando", texto)


class Conciliacion(Base):
    def test_registra_pagos_de_intentos_sin_resultado(self):
        self.mp.pago(7001)
        self.mp.busquedas["JSU-1-abc"] = [{"id": 7001}]
        mercadopago.conciliar(AlumnaFalsa(intentos=[{"referencia": "JSU-1-abc", "estado": "creado"}]), "s")
        self.assertEqual(len(self.admin.llamadas("registrar_pago_mercado_pago")), 1)

    def test_apagado_no_hace_nada(self):
        self.preparar(admin=AdminFalso(habilitado=False))
        mercadopago.conciliar(AlumnaFalsa(intentos=[{"referencia": "JSU-1-abc", "estado": "creado"}]), "s")
        self.assertEqual(self.mp.llamadas, [])


def firmar(data_id, request_id, ts, secreto):
    manifiesto = f"id:{data_id};request-id:{request_id};ts:{ts};"
    return f"ts={ts},v1=" + hmac.new(secreto.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()


class Avisos(Base):
    def test_firma_valida_e_invalida(self):
        f = firmar("123", "req-1", "1700000000", "secreto")
        self.assertTrue(mercadopago.firma_valida(f, "req-1", "123", "secreto"))
        self.assertFalse(mercadopago.firma_valida(f, "req-1", "124", "secreto"))
        self.assertFalse(mercadopago.firma_valida(f, "req-2", "123", "secreto"))
        self.assertFalse(mercadopago.firma_valida(f, "req-1", "123", "otro"))
        self.assertFalse(mercadopago.firma_valida(None, "req-1", "123", "secreto"))

    def test_aviso_de_pago_dispara_la_verificacion(self):
        self.mp.pago(8001)
        mercadopago.procesar_aviso({"type": "payment", "data.id": "8001"}, {}, {})
        self.assertEqual(len(self.admin.llamadas("registrar_pago_mercado_pago")), 1)

    def test_aviso_con_firma_invalida_se_ignora(self):
        self.preparar(admin=AdminFalso(secreto="secreto"))
        self.mp.pago(8002)
        mercadopago.procesar_aviso({"type": "payment", "data.id": "8002"}, {},
                                   {"x-signature": "ts=1,v1=falsa", "x-request-id": "r"})
        self.assertEqual(self.admin.llamadas("registrar_pago_mercado_pago"), [])

    def test_aviso_con_firma_valida(self):
        self.preparar(admin=AdminFalso(secreto="secreto"))
        self.mp.pago(8003)
        mercadopago.procesar_aviso({"type": "payment", "data.id": "8003"}, {},
                                   {"x-signature": firmar("8003", "r", "1", "secreto"), "x-request-id": "r"})
        self.assertEqual(len(self.admin.llamadas("registrar_pago_mercado_pago")), 1)

    def test_otros_avisos_se_ignoran(self):
        mercadopago.procesar_aviso({"type": "merchant_order", "data.id": "1"}, {}, {})
        self.assertEqual(self.mp.llamadas, [])

    def test_apagado_ignora_avisos(self):
        self.preparar(admin=AdminFalso(habilitado=False))
        self.mp.pago(8004)
        mercadopago.procesar_aviso({"type": "payment", "data.id": "8004"}, {}, {})
        self.assertEqual(self.mp.llamadas, [])


class TokenNuncaVisible(Base):
    def test_no_aparece_en_los_logs(self):
        self.mp.pago(9001)
        self.mp.pago(9002, collector="123")
        with self.assertLogs(level=logging.DEBUG) as capturado:
            logging.getLogger().debug("inicio")
            mercadopago.iniciar_pago(AlumnaFalsa(), SOFIA.mail, 1, None)
            mercadopago.verificar_pago("9001", "aviso")
            mercadopago.verificar_pago("9002", "aviso")
            mercadopago.verificar_pago("9999", "aviso")
        self.assertNotIn(TOKEN, "\n".join(capturado.output))


class Rutas(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, base_url="https://just-show-up-theta.vercel.app")
        p = mock.patch.object(main, "_sb", return_value=AlumnaFalsa())
        p.start()
        self.addCleanup(p.stop)
        self.factura = {"id": 1, "mes_txt": "Octubre 2026", "numero": "JSU-2026-10-0001", "estado_factura": "confirmada",
                        "estado_pago": "pendiente", "estado_pago_txt": "Pendiente de pago", "total": "1600.00",
                        "lineas": [], "pago": None, "reemplazada_por": None, "reemplaza": None,
                        "nombre_completo": "Sofía Demo", "cedula_txt": "no registrada", "descuento_txt": ""}
        p = mock.patch.object(facturas, "mis_facturas", return_value=[self.factura])
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_boton_para_alumna_ficticia_con_factura_pendiente(self):
        self.como(SOFIA)
        html = self.client.get("/mis-facturas").text
        self.assertIn('action="/mis-facturas/1/mercado-pago"', html)
        self.assertIn("dinero ficticio", html)
        self.assertNotIn(TOKEN, html)

    def test_boton_con_el_logo_oficial(self):
        self.como(SOFIA)
        html = self.client.get("/mis-facturas").text
        self.assertRegex(html, r'Pagar con Mercado Pago</span>\s*<img class="btn-mp-logo" src="/static/img/mercado-pago-logo.svg"')
        self.assertIn("Pagar con Mercado Pago", html)
        # orden: mes, PDF y estado arriba; total; botón de Mercado Pago; aclaración
        orden = [html.index(x) for x in ("Octubre 2026</h2>", "Descargar PDF", "Pendiente de pago", "$1600.00",
                                         "btn-mercado-pago", "dinero ficticio")]
        self.assertEqual(orden, sorted(orden))
        r = self.client.get("/static/img/mercado-pago-logo.svg")
        self.assertEqual(r.status_code, 200)
        # el archivo oficial, sin editar (Logos Mercado Pago 2025, uso digital RGB, color horizontal)
        self.assertEqual(hashlib.sha256(r.content).hexdigest()[:16], LOGO_SHA256)
        self.assertNotIn(b"<script", r.content)

    def test_sin_boton_si_esta_apagado(self):
        self.preparar(admin=AdminFalso(habilitado=False))
        self.como(SOFIA)
        self.assertNotIn("mercado-pago", self.client.get("/mis-facturas").text)

    def test_sin_boton_para_alumna_con_mail_real(self):
        self.como(REAL)
        self.assertNotIn("mercado-pago", self.client.get("/mis-facturas").text)

    def test_sin_boton_si_ya_esta_pagada(self):
        self.factura.update(estado_pago="pagado", estado_pago_txt="Pagada")
        self.como(SOFIA)
        self.assertNotIn('action="/mis-facturas/1/mercado-pago"', self.client.get("/mis-facturas").text)

    def test_pagar_redirige_a_mercado_pago(self):
        self.como(SOFIA)
        r = self.client.post("/mis-facturas/1/mercado-pago", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], self.mp.link)

    def test_pagar_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.post("/mis-facturas/1/mercado-pago", follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_vuelta_muestra_el_resultado(self):
        self.mp.pago(9101)
        self.como(SOFIA)
        r = self.client.get("/mis-facturas/mercado-pago/vuelta?payment_id=9101&status=approved", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("ok=1", r.headers["location"])

    def test_vuelta_no_le_cree_al_status_de_la_url(self):
        self.como(SOFIA)
        r = self.client.get("/mis-facturas/mercado-pago/vuelta?payment_id=9102&status=approved", follow_redirects=False)
        self.assertIn("ok=0", r.headers["location"])
        self.assertEqual(self.admin.llamadas("registrar_pago_mercado_pago"), [])

    def test_aviso_responde_200_y_verifica(self):
        self.mp.pago(9201)
        r = self.client.post("/webhooks/mercado-pago?type=payment&data.id=9201", json={"type": "payment", "data": {"id": "9201"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.admin.llamadas("registrar_pago_mercado_pago")), 1)

    def test_aviso_con_cuerpo_invalido_igual_responde(self):
        r = self.client.post("/webhooks/mercado-pago", content=b"no es json")
        self.assertEqual(r.status_code, 200)

    def test_aviso_con_error_inesperado_pide_reintento(self):
        with mock.patch.object(mercadopago, "procesar_aviso", side_effect=RuntimeError("falla")):
            r = self.client.post("/webhooks/mercado-pago?type=payment&data.id=1", json={})
        self.assertEqual(r.status_code, 500)


class FacturacionProfesora(unittest.TestCase):
    def test_medio_mercado_pago_no_aparece_en_el_registro_manual(self):
        self.assertNotIn("mercado_pago", facturas.MEDIOS_PAGO)
        self.assertEqual(facturas.NOMBRES_MEDIO["mercado_pago"], "Mercado Pago (prueba)")


if __name__ == "__main__":
    unittest.main()
