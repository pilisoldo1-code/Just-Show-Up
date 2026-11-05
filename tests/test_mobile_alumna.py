"""Pantallas de la alumna en celular (Mobile alumna): resumen semanal, día, Mis inscripciones, Mi historial, Mis facturas
y Mi perfil. Solo rutas y plantillas, con la base simulada.

Uso: python -m unittest tests.test_mobile_alumna -v
"""
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, clases, main
from app.auth import Usuario

ALUMNA = Usuario(id="a", mail="alumna@prueba.invalid", nombre="Ana", apellido="Prueba", rol="alumna", token="t")


def clase(fecha="2026-11-10", hora="08:00", estado="disponible", dia="Martes", **extra):
    """Una tarjeta como las que arma clases.clases_entre."""
    c = {"id": abs(hash((fecha, hora))) % 10**6, "fecha": fecha, "dia": dia, "fecha_txt": f"{int(fecha[8:])}/{int(fecha[5:7])}",
         "hora": hora, "duracion": 50, "precio": 1200, "cupo_max": 6, "tipo": "Pilates mat", "imagen": None,
         "profesora": "Lucía Bentancor", "disponibles": 3, "estado": estado, "posicion_espera": None, "oferta_vence": "",
         "puede_cancelar": True}
    c.update(extra)
    return c


def texto(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        for objeto, nombre, valor in ((main, "_sb", mock.MagicMock()), (auth, "_usuario_cacheado", ALUMNA)):
            p = mock.patch.object(objeto, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def grilla(self, tarjetas):
        with mock.patch.object(clases, "generar_clases"), mock.patch.object(clases, "clases_entre", return_value=tarjetas):
            return self.client.get("/").text


class ResumenSemanal(Base):
    def test_dos_horarios(self):
        html = self.grilla([clase(hora="08:00"), clase(hora="09:00")])
        t = texto(html)
        self.assertIn("2 horarios · de 08:00 a 09:00", t)
        self.assertIn("2 horarios con lugar", t)
        self.assertIn('href="/dia/2026-11-10"', html)
        self.assertNotIn("hora-chip", html)  # ya no se listan las horas en la semana

    def test_diez_horarios_entran_en_una_tarjeta(self):
        html = self.grilla([clase(hora=f"{h:02d}:00") for h in range(7, 17)])
        self.assertIn("10 horarios · de 07:00 a 16:00", texto(html))
        self.assertEqual(html.count('class="card card-dia-link'), 1)

    def test_un_horario(self):
        self.assertIn("1 horario · 19:00", texto(self.grilla([clase(hora="19:00")])))

    def test_reserva_y_lista_de_espera_a_la_vista(self):
        t = texto(self.grilla([clase(hora="08:00", estado="inscripta"), clase(hora="09:00", estado="espera"),
                               clase(hora="10:00", estado="oferta")]))
        for x in ("Reservada 08:00", "En lista de espera 09:00", "¡Lugar disponible! 10:00", "Ver día ›"):
            self.assertIn(x, t)
        self.assertNotIn("con lugar", t)  # si tiene algo ese día, se muestra eso

    def test_dia_completo(self):
        self.assertIn("Sin lugares: podés anotarte en la lista de espera",
                      texto(self.grilla([clase(estado="completa"), clase(hora="09:00", estado="completa")])))

    def test_dia_terminado_compacto_y_accesible_sin_reservar(self):
        html = self.grilla([clase(fecha="2026-11-03", hora="08:00", estado="pasada"),
                            clase(fecha="2026-11-04", hora="08:00", estado="disponible")])
        terminado = html[html.index('href="/dia/2026-11-03"') - 200:html.index('href="/dia/2026-11-04"')]
        self.assertIn("card-finalizada", terminado)  # A1: franja compacta
        self.assertIn("Finalizado", terminado)
        self.assertNotIn("Ver día", terminado)
        self.assertNotIn("card-finalizada", html[html.index('href="/dia/2026-11-04"') - 200:])

    def test_clases_canceladas_por_la_profesora(self):
        self.assertIn("Clases canceladas por la profesora", texto(self.grilla([clase(estado="cancelada")])))


class Dia(Base):
    def dia(self, tarjetas, desde=None):
        from app import facturas
        with mock.patch.object(clases, "generar_clases"), mock.patch.object(clases, "clases_entre", return_value=tarjetas), \
                mock.patch.object(facturas, "cobro_inasistencia_desde", return_value={None: desde}), \
                mock.patch.object(main, "_parse_fecha", return_value=__import__("datetime").date(2026, 11, 10)), \
                mock.patch.object(clases, "offset_semana", return_value=1):
            return texto(self.client.get("/dia/2026-11-10").text)

    def test_hora_limite_de_cancelacion(self):
        t = self.dia([clase(estado="inscripta", cancelar_hasta="18:00")])
        self.assertIn("Podés cancelar sin cargo hasta las 18:00.", t)

    def test_plazo_vencido_segun_la_regla_de_la_empresa(self):
        from datetime import date
        con_25 = self.dia([clase(estado="inscripta", puede_cancelar=False, cancelar_hasta="18:00")], desde=date(2026, 9, 1))
        self.assertIn("El plazo para cancelar venció a las 18:00: si no asistís, se cobra el 25 % del precio.", con_25)
        sin_regla = self.dia([clase(estado="inscripta", puede_cancelar=False, cancelar_hasta="18:00")])
        self.assertIn("si no asistís, se cobra el precio completo.", sin_regla)
        self.assertNotIn("Podés cancelar sin cargo", sin_regla)

    def test_completa_y_lista_de_espera_explican_que_pasa(self):
        self.assertIn("se le ofrece a la primera de la lista, que tiene 30 minutos para confirmarlo",
                      self.dia([clase(estado="completa", disponibles=0)]))
        t = self.dia([clase(estado="espera", posicion_espera=2)])
        self.assertIn("En lista de espera · posición 2", t)
        self.assertIn("tenés 30 minutos para confirmarlo", t)

    def test_limite_de_cancelacion_otro_dia(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Montevideo")
        self.assertEqual(clases._limite_cancelacion(datetime(2026, 11, 10, 19, 0, tzinfo=tz)), "18:00")
        self.assertEqual(clases._limite_cancelacion(datetime(2026, 11, 10, 0, 30, tzinfo=tz)), "23:30 del 9/11")


class MisInscripciones(Base):
    def pagina(self, **datos):
        base = {"proximas": [], "en_espera": [], "ofertas": [], "canceladas_profesora": [], "historial": []}
        with mock.patch.object(clases, "mis_inscripciones", return_value={**base, **datos}):
            return self.client.get("/mis-inscripciones").text

    def test_tarjeta_con_todos_los_datos(self):
        html = self.pagina(proximas=[clase(estado="confirmada")])
        t = texto(html)
        for x in ("Martes 10/11", "08:00", "Pilates mat", "Profesora: Lucía Bentancor", "Duración: 50 min",
                  "Precio: $1.200", "Reservada", "Ver o cancelar"):
            self.assertIn(x, t)
        self.assertIn('class="card card-inscripcion card-inscripta"', html)
        self.assertIn('href="/dia/2026-11-10">Ver o cancelar', html)

    def test_lista_de_espera_oferta_y_cancelada(self):
        t = texto(self.pagina(en_espera=[clase(posicion=3)], ofertas=[clase(hora="09:00", oferta_vence="07:45")],
                              canceladas_profesora=[clase(hora="10:00")]))
        for x in ("En lista de espera · posición 3", "Ver o salir de la lista", "¡Lugar disponible!",
                  "Confirmalo antes de las 07:45.", "Confirmar o liberar", "Cancelada",
                  "Estas clases no se van a dictar y no se cobran."):
            self.assertIn(x, t)

    def test_sin_reservas(self):
        self.assertIn("No tenés clases reservadas.", texto(self.pagina()))

    def test_datos_de_tipo_y_profesora(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        sb = mock.MagicMock()
        c = {"id": 1, "fecha": "2026-11-10", "hora_inicio": "08:00:00", "duracion": 50, "precio": 1200,
             "id_profesora": "p1", "tipo_clase": {"nombre": "Pilates mat"}}
        tablas = {"reserva": mock.MagicMock(), "listadeespera": mock.MagicMock()}
        tablas["reserva"].select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 9, "estado": "confirmada", "id_bloqueo": None, "asistencia": [], "clase": c}]
        tablas["listadeespera"].select.return_value.eq.return_value.in_.return_value.execute.return_value.data = []
        sb.table.side_effect = lambda t: tablas[t]
        sb.rpc.return_value.execute.return_value.data = []
        with mock.patch.object(clases, "ahora", return_value=datetime(2026, 11, 5, 10, 0, tzinfo=ZoneInfo("America/Montevideo"))), \
                mock.patch.object(clases.personas, "nombres_profesoras", return_value={"p1": "Lucía Bentancor"}):
            proxima = clases.mis_inscripciones(sb, "a")["proximas"][0]
        self.assertEqual((proxima["tipo"], proxima["profesora"]), ("Pilates mat", "Lucía Bentancor"))


class MiHistorial(Base):
    def pagina(self, historial, desde=None):
        from app import facturas
        with mock.patch.object(clases, "mis_inscripciones", return_value={"historial": historial}), \
                mock.patch.object(facturas, "cobro_inasistencia_desde", return_value={None: desde}):
            return texto(self.client.get("/mi-historial").text)

    @staticmethod
    def item(fecha, estado="Presente", hora="08:00"):
        return clase(fecha=fecha, hora=hora, estado_txt=estado, badge="badge-on", cancelada_por_profesora=False,
                     precio=900)

    def test_por_mes_el_mas_reciente_primero_y_en_orden_dentro_del_mes(self):
        # mis_inscripciones lo devuelve de la más reciente a la más antigua
        t = self.pagina([self.item("2026-11-04"), self.item("2026-11-02", hora="19:00"), self.item("2026-11-02"),
                         self.item("2026-10-28"), self.item("2026-10-06")])
        self.assertLess(t.index("Noviembre 2026"), t.index("Octubre 2026"))
        noviembre = t[t.index("Noviembre 2026"):t.index("Octubre 2026")]
        self.assertLess(noviembre.index("2/11 Presente 08:00"), noviembre.index("2/11 Presente 19:00"))
        self.assertLess(noviembre.index("2/11 Presente 19:00"), noviembre.index("4/11"))
        octubre = t[t.index("Octubre 2026"):]
        self.assertLess(octubre.index("6/10"), octubre.index("28/10"))
        self.assertIn("por mes: el más reciente primero", t)

    def test_inasistencia_con_porcentaje_e_importe_reales_y_nota_una_vez_por_mes(self):
        from datetime import date
        t = self.pagina([self.item("2026-11-04", "Ausente"), self.item("2026-11-03", "Ausente"),
                         self.item("2026-10-20", "Ausente")], desde=date(2026, 11, 1))
        self.assertEqual(t.count("Inasistencia: se cobra el 25 % del precio ($225,00)"), 2)
        self.assertEqual(t.count("figuran en Mis facturas"), 1)  # solo noviembre, una vez
        self.assertNotIn("Importe estimado", t)

    def test_sin_regla_no_hay_nota(self):
        t = self.pagina([self.item("2026-11-04", "Ausente")])
        self.assertNotIn("Inasistencia", t)
        self.assertNotIn("figuran en Mis facturas", t)


class MisFacturas(Base):
    def pagina(self, **factura):
        from app import facturas, mercadopago
        f = {"id": 1, "numero": "PIL-2026-10-0001", "mes": 10, "anio": 2026, "periodo_txt": "Octubre 2026",
             "estado_factura": "confirmada", "estado_pago": "pendiente", "estado_pago_txt": "Pendiente",
             "estado_pago_clase": "badge-espera", "vence_txt": "10/11/2026", "vencida": False, "pago": None,
             "lineas": [{"fecha_txt": "Lunes 5/10", "hora": "08:00", "tipo": "Pilates mat", "profesora": "Lucía Bentancor",
                         "inasistencia": False, "precio_original": 900, "ajuste_tipo": "ninguno", "importe_final": 900}],
             "cantidad_clases": 1, "total_lineas": 900, "total": 900, "descuento_tipo": None, "inasistencias": 0}
        f.update(factura)
        with mock.patch.object(facturas, "mis_facturas", return_value=[f]), \
                mock.patch.object(mercadopago, "disponible_para", return_value=False):
            return self.client.get("/mis-facturas").text

    def test_fecha_limite_de_pago(self):
        self.assertIn("Fecha límite de pago: 10/11/2026", texto(self.pagina()))
        self.assertIn("Fecha límite de pago: 10/11/2026 (vencida: podés pagarla igual)", texto(self.pagina(vencida=True)))

    def test_tipo_y_profesora_en_renglones_distintos_solo_para_la_alumna(self):
        html = self.pagina()
        self.assertIn('<span class="ft-tipo">Pilates mat</span><span class="nota-chica ft-prof">Lucía Bentancor</span>', html)
        css = open("app/static/style.css").read()
        self.assertIn(".factura-alumna .factura-tabla tbody .ft-prof {\n        display: block;", css)
        self.assertIn(".factura-alumna .factura-tabla tbody .ft-prof::before {\n        content: none;", css)


class MiPerfil(Base):
    def test_telefono_sin_enlace_y_ojo_en_cada_contrasena(self):
        datos = {"nombre": "Ana", "apellido": "Prueba", "mail": "alumna@prueba.invalid", "telefono": "099123456",
                 "cedula": None, "fecha_nacimiento": None, "fecha_vencimiento_carne_salud": None}
        with mock.patch.object(main, "_datos_perfil", return_value=datos):
            html = self.client.get("/perfil").text
        self.assertIn('<meta name="format-detection" content="telephone=no" />', html)
        self.assertEqual(html.count('type="password"'), 3)
        self.assertIn("campo-clave", html)
        self.assertIn("aria-label', 'Mostrar contraseña'", html)

    def test_login_y_restablecer_tambien_tienen_el_ojo(self):
        for ruta in ("/login", "/restablecer-contrasena"):
            with mock.patch.object(auth, "_usuario_cacheado", return_value=None):
                self.assertIn("ojo-clave", self.client.get(ruta).text, ruta)


class EnvioUnico(Base):
    def test_botones_del_dia_dicen_que_estan_haciendo_y_no_se_envian_dos_veces(self):
        with mock.patch.object(clases, "generar_clases"), mock.patch.object(clases, "clases_entre", return_value=[
                clase(estado="disponible")]), mock.patch.object(main, "_parse_fecha",
                return_value=__import__("datetime").date(2026, 11, 10)), mock.patch.object(clases, "offset_semana", return_value=1):
            html = self.client.get("/dia/2026-11-10").text
        self.assertIn('data-cargando="Reservando…">Reservar clase', html)
        self.assertIn("form.dataset.enviando", html)
        self.assertIn('class="volver-semana"', html)

    def test_guardar_contrasena(self):
        with mock.patch.object(main, "_datos_perfil", return_value={}):
            html = self.client.get("/perfil").text
        self.assertIn('class="btn btn-tactil" data-cargando="Guardando…">Guardar contraseña', html)
        self.assertIn("form.dataset.enviando", html)


if __name__ == "__main__":
    unittest.main()
