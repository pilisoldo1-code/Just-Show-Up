"""Videos On Demand: textos, lista firmada, reproductor, permisos de las rutas, configuración, programa local y limpieza.
La base y Storage se simulan; las reglas de la base se prueban en tests/base_local/test_videos_base.py.

Uso: python -m unittest tests.test_videos -v
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import auth, main, videos
from app.auth import Usuario

ALUMNA = Usuario(id="a", mail="a@prueba.invalid", nombre="Ana", apellido="P", rol="alumna", token="t")
ADMIN = Usuario(id="d", mail="d@prueba.invalid", nombre="Ada", apellido="P", rol="profesora", token="t")
PROFESORA = Usuario(id="p", mail="p@prueba.invalid", nombre="Pía", apellido="P", rol="alumna", token="t", empresa_id=1,
                    con_membresias=True, es_docente=True)
SUPERADMIN = Usuario(id="s", mail="s@prueba.invalid", nombre="Sol", apellido="P", rol="alumna", token="t",
                     es_superadmin=True, con_membresias=True)
FILA = {"id": 7, "clase": "Gimnasia", "fecha": "2026-11-16", "hora_inicio": "08:00:00", "profesora": "Pía Pérez",
        "duracion_seg": 2340, "segundos_restantes": 5400}
ENTORNO = "pruebas-abcd1234"
LISTA = "#EXTM3U\n#EXT-X-BYTERANGE:10@0\nparte-00.ts\n#EXT-X-BYTERANGE:5@0\nparte-01.ts\n#EXT-X-ENDLIST"
PARTES = [{"nombre": "parte-00.ts", "bytes": 10}, {"nombre": "parte-01.ts", "bytes": 5}]


class Rpc:
    """Cliente falso: responde según el nombre de la función y guarda las llamadas."""
    def __init__(self, respuestas):
        self.respuestas, self.llamadas = respuestas, []

    def rpc(self, nombre, params):
        self.llamadas.append((nombre, params))
        return mock.Mock(execute=lambda: mock.Mock(data=self.respuestas.get(nombre)))


class Textos(unittest.TestCase):
    def test_restante(self):
        self.assertEqual(videos.texto_restante(5 * 3600 + 20 * 60), "Disponible por 5 h 20 min más")
        self.assertEqual(videos.texto_restante(600), "Disponible por 10 min más")
        self.assertEqual(videos.texto_restante(10), "Disponible por 1 min más")

    def test_duracion_real_sin_redondear_a_40(self):
        self.assertEqual(videos.texto_duracion(2400), "40 min")
        self.assertEqual(videos.texto_duracion(2340), "39 min")
        self.assertEqual(videos.texto_duracion(2395), "39 min 55 s")

    def test_tarjeta_con_dia_y_tilde(self):
        fila = dict(FILA, fecha="2026-11-18")
        self.assertEqual(videos._tarjeta(fila)["fecha_txt"], "Miércoles 18/11")


class Lista(unittest.TestCase):
    def reproducir(self, segundos, firmadas=None):
        sb = Rpc({"video_para_firmar": {"resultado": "ok", "id_empresa": 2, "partes": PARTES, "lista_hls": LISTA,
                                       "segundos_restantes": segundos}})
        bucket = mock.Mock()
        bucket.create_signed_urls.return_value = firmadas if firmadas is not None else [
            {"path": f"2/7/{p['nombre']}", "signedURL": f"https://x/{p['nombre']}?token=z"} for p in PARTES]
        with mock.patch.object(videos, "almacen", return_value=bucket):
            return videos.lista_para_reproducir(sb, 7), bucket

    def test_firma_solo_las_partes(self):
        (estado, lista), _ = self.reproducir(5000)
        self.assertEqual(estado, "ok")
        self.assertIn("https://x/parte-00.ts?token=z", lista)
        self.assertIn("#EXT-X-BYTERANGE:10@0", lista)
        self.assertNotIn("\nparte-00.ts", lista)

    def test_url_no_dura_mas_que_el_tope(self):
        _, bucket = self.reproducir(80000)
        self.assertEqual(bucket.create_signed_urls.call_args.args[1], videos.TTL_URL_MAXIMO)

    def test_url_no_supera_el_vencimiento(self):
        _, bucket = self.reproducir(120)
        self.assertEqual(bucket.create_signed_urls.call_args.args[1], 120 - videos.MARGEN_URL_SEG)

    def test_sobre_el_vencimiento_no_firma(self):
        for restante in (0, 1, videos.MARGEN_URL_SEG):
            (estado, _), bucket = self.reproducir(restante)
            self.assertEqual(estado, "no_disponible")
            bucket.create_signed_urls.assert_not_called()

    def test_parte_sin_firmar_no_reproduce(self):
        (estado, _), _ = self.reproducir(500, [{"path": "2/7/parte-00.ts", "signedURL": "https://x/a"},
                                               {"path": "2/7/parte-01.ts", "signedURL": None}])
        self.assertEqual(estado, "no_disponible")

    def test_rechazo_de_la_base_no_firma(self):
        for resultado in ("sin_iniciar", "limite_usuaria", "no_disponible"):
            sb = Rpc({"video_para_firmar": {"resultado": resultado}})
            with mock.patch.object(videos, "almacen") as almacen:
                self.assertEqual(videos.lista_para_reproducir(sb, 7)[0], resultado)
            almacen.assert_not_called()


class Pantallas(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        p = mock.patch.object(main, "_sb", return_value=Rpc({"videos_disponibles": [FILA]}))
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        for url in ("/videos", "/videos/7", "/videos/7/lista.m3u8"):
            r = self.client.get(url, follow_redirects=False)
            self.assertEqual(r.status_code, 303, url)
            self.assertTrue(r.headers["location"].startswith("/login"))

    def test_superadmin_no_entra(self):
        self.como(SUPERADMIN)
        with mock.patch.object(videos, "lista_para_reproducir") as lista:
            r = self.client.get("/videos/7/lista.m3u8", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        lista.assert_not_called()

    def test_alumna_profesora_y_administradora_ven_la_lista(self):
        for usuario in (ALUMNA, PROFESORA, ADMIN):
            self.como(usuario)
            r = self.client.get("/videos")
            self.assertEqual(r.status_code, 200)
            for texto in ("Gimnasia", "Lunes 16/11", "Pía Pérez", "39 min", "Disponible por 1 h 30 min más"):
                self.assertIn(texto, r.text)

    def test_sin_videos_estado_vacio(self):
        self.como(ALUMNA)
        with mock.patch.object(main, "_sb", return_value=Rpc({"videos_disponibles": []})):
            self.assertIn("No hay videos disponibles", self.client.get("/videos").text)

    def test_video_ajeno_o_vencido_vuelve_a_la_lista(self):
        self.como(ALUMNA)
        with mock.patch.object(main, "_sb", return_value=Rpc({"videos_disponibles": []})):
            r = self.client.get("/videos/99", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/videos?"))

    def test_abrir_la_pagina_no_inicia_ni_descarga(self):
        self.como(ALUMNA)
        with mock.patch.object(videos, "iniciar") as iniciar, mock.patch.object(videos, "lista_para_reproducir") as lista:
            r = self.client.get("/videos/7")
        iniciar.assert_not_called()
        lista.assert_not_called()
        self.assertIn('preload="none"', r.text)
        self.assertIn("playsinline", r.text)
        video = r.text[r.text.index("<video"):r.text.index("</video>")]
        self.assertNotIn(" src=", video)
        self.assertNotIn("autoplay", video)

    def test_reproductor_elige_webkit_y_no_canplaytype_solo(self):
        self.como(ALUMNA)
        html = self.client.get("/videos/7").text
        self.assertLess(html.index("!esWebKit() && window.Hls"), html.index('video.canPlayType("application/vnd.apple.mpegurl")'))
        self.assertIn('integrity="sha384-', html)

    def test_iniciar_y_sus_rechazos(self):
        self.como(ALUMNA)
        for estado, codigo in (("ok", 204), ("no_disponible", 404), ("limite_usuaria", 429), ("sin_presupuesto", 429)):
            with mock.patch.object(videos, "iniciar", return_value=estado):
                self.assertEqual(self.client.post("/videos/7/iniciar").status_code, codigo, estado)

    def test_progreso(self):
        self.como(ALUMNA)
        with mock.patch.object(videos, "progreso", return_value="ok") as progreso:
            r = self.client.post("/videos/7/progreso", data={"segundos": 60})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(progreso.call_args.args[1:], (7, 60))

    def test_lista_hls_y_sus_rechazos(self):
        self.como(ALUMNA)
        with mock.patch.object(videos, "lista_para_reproducir", return_value=("ok", "#EXTM3U\n")):
            r = self.client.get("/videos/7/lista.m3u8")
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(r.headers["cache-control"], "no-store")
        for estado, codigo in (("no_disponible", 404), ("sin_iniciar", 409), ("limite_usuaria", 429)):
            with mock.patch.object(videos, "lista_para_reproducir", return_value=(estado, "")):
                self.assertEqual(self.client.get("/videos/7/lista.m3u8").status_code, codigo)

    def test_textos_no_prometen_exactitud(self):
        self.como(ALUMNA)
        html = self.client.get("/videos/7").text.lower()
        self.assertIn("límite estimado", html)
        self.assertNotIn("40 minutos", html)

    def test_falla_de_la_base_muestra_aviso(self):
        self.como(ALUMNA)
        with mock.patch.object(main, "_sb", side_effect=RuntimeError("caída")), self.assertLogs(level="ERROR"):
            self.assertIn("No pudimos cargar los videos", self.client.get("/videos").text)


class Configuracion(unittest.TestCase):
    HORARIOS = [
        {"id": 2, "dia_semana": "miercoles", "hora_inicio": "08:00:00", "clase": "Pilates", "profesora": "Pía", "duracion": 40,
         "programable": True, "activa": True},
        {"id": 1, "dia_semana": "lunes", "hora_inicio": "09:00:00", "clase": "Yoga", "profesora": "Pía", "duracion": 40,
         "programable": True, "activa": False},
        {"id": 3, "dia_semana": "lunes", "hora_inicio": "10:00:00", "clase": "Gimnasia", "profesora": "Pía", "duracion": 60,
         "programable": False, "activa": False},
    ]
    GRABACIONES = [
        {"id": 9, "clase": "Pilates", "fecha": "2026-11-16", "hora_inicio": "08:00:00", "estado": "error", "vencido": False,
         "nunca_publicado": True, "intentos": 3, "ultimo_error": "falló https://x.supabase.co/a?token=secreto1",
         "limpieza_intentos": 0, "limpieza_error": None},
        {"id": 8, "clase": "Yoga", "fecha": "2026-11-15", "hora_inicio": "09:00:00", "estado": "publicado", "vencido": True,
         "nunca_publicado": False, "intentos": 1, "ultimo_error": None, "limpieza_intentos": 2,
         "limpieza_error": "Siguen en Storage: parte-00.ts"},
    ]

    def setUp(self):
        self.client = TestClient(main.app)
        self.sb = Rpc({"videos_configuracion": self.HORARIOS, "video_programar": {"resultado": "ok"}, "videos_disponibles": [],
                       "videos_ultimas_grabaciones": self.GRABACIONES})
        p = mock.patch.object(main, "_sb", return_value=self.sb)
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def test_la_configuracion_de_horarios_ya_no_se_muestra(self):
        # se retiró de la pantalla (09/11): redirige a Videos sin leer ni cambiar nada de lo guardado
        for usuario in (ADMIN, PROFESORA, ALUMNA):
            self.como(usuario)
            self.sb.llamadas.clear()
            r = self.client.get("/videos/configuracion", follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/videos"))
            self.assertIn(self.client.post("/videos/configuracion/1", data={"activa": "true"},
                                           follow_redirects=False).status_code, (404, 405))
            self.assertFalse({"videos_configuracion", "video_programar"} & {n for n, _ in self.sb.llamadas})

    def test_nadie_ve_el_boton_de_horarios(self):
        for usuario in (ADMIN, PROFESORA, ALUMNA):
            self.como(usuario)
            html = self.client.get("/videos").text
            self.assertNotIn("/videos/configuracion", html)
            self.assertNotIn("Elegir qué horarios se graban", html)

    def test_estado_y_errores_de_las_grabaciones_en_videos(self):
        self.como(ADMIN)
        html = self.client.get("/videos").text
        for texto in ("Estado y errores de las grabaciones", "Con error, se reintenta", "3 intentos",
                      "Vencido, falta borrar archivos", "Limpieza (2 intentos)", "Siguen en Storage"):
            self.assertIn(texto, html)
        self.assertNotIn("secreto1", html)
        self.assertNotIn("supabase.co", html)
        self.assertNotIn("Elegir qué horarios se graban", html)

    def test_solo_la_administradora_ve_el_estado_de_las_grabaciones(self):
        for usuario, ve in ((ADMIN, True), (PROFESORA, False), (ALUMNA, False)):
            self.como(usuario)
            self.sb.llamadas.clear()
            html = self.client.get("/videos").text
            self.assertEqual("Estado y errores de las grabaciones" in html, ve)
            self.assertEqual("videos_ultimas_grabaciones" in {n for n, _ in self.sb.llamadas}, ve)

    def test_estados_de_las_grabaciones(self):
        base = {"nunca_publicado": False, "vencido": False}
        self.assertEqual(videos._estado_grabacion({**base, "estado": "eliminado"})[0], "Vencido, archivos borrados")
        self.assertEqual(videos._estado_grabacion({**base, "estado": "eliminado", "nunca_publicado": True})[0], "Abandonada, sin publicar")
        self.assertEqual(videos._estado_grabacion({**base, "estado": "publicado"})[0], "Publicado")


class Mensajes(unittest.TestCase):
    def test_saca_lo_sensible(self):
        for sensible in ("https://abc.supabase.co/storage/v1/object/upload/sign/x?token=eyJhbGci",
                         "/Users/ana/Documents/Zoom/clase.mp4", "C:\\Users\\ana\\zoom.mp4", "Bearer abcdef",
                         "sb_secret_abc123", "eyJhbGciOiJIUzI1NiJ9.e30.abc", "a" * 40):
            self.assertNotIn(sensible, videos.limpiar_mensaje(f"falló {sensible} al subir"), sensible)

    def test_conserva_lo_util_y_recorta(self):
        self.assertEqual(videos.limpiar_mensaje("No se pudo publicar: partes_incompletas"), "No se pudo publicar: partes_incompletas")
        self.assertEqual(videos.limpiar_mensaje(None), "")
        self.assertLessEqual(len(videos.limpiar_mensaje("palabra " * 200)), 300)


class Menu(unittest.TestCase):
    def html(self, usuario):
        request = SimpleNamespace(url=SimpleNamespace(path="/videos"))
        return main.templates.get_template("_menu.html").render(request=request, usuario=usuario)

    def test_la_administradora_lo_tiene_en_sus_secciones_y_no_en_la_cuenta(self):
        html = self.html(ADMIN)
        secciones = html[html.index('id="menu-principal"'):html.index("</nav>")]
        self.assertEqual(secciones.count("<a "), 10)
        self.assertIn('href="/videos"', secciones)
        self.assertIn('<header class="menu menu-largo">', html)  # 10 secciones: desplegable hasta 1219 px
        cuenta = html[html.index('class="cuenta-opciones"'):]
        self.assertNotIn('href="/videos"', cuenta[:cuenta.index("</details>")])

    def test_alumna_y_profesora_no_usan_el_menu_largo(self):
        for usuario in (ALUMNA, PROFESORA):
            self.assertIn('<header class="menu">', self.html(usuario))

    def test_alumna_y_profesora_lo_tienen_en_sus_secciones(self):
        for usuario in (ALUMNA, PROFESORA):
            html = self.html(usuario)
            self.assertIn('href="/videos"', html[html.index('id="menu-principal"'):html.index("</nav>")])

    def test_superadministradora_no_lo_tiene(self):
        self.assertNotIn('href="/videos"', self.html(SUPERADMIN))


JPEG = b"\xff\xd8\xff\xe0" + b"x" * 5000


class Tabla:
    """Lectura de la columna miniatura con la sesión: devuelve lo que dejaría pasar la política de la base."""
    def __init__(self, filas):
        self.filas, self.consultas = filas, []

    def table(self, nombre):
        self.consultas.append(nombre)
        ejecutar = mock.Mock(execute=lambda: mock.Mock(data=self.filas))
        return mock.Mock(select=lambda *_: mock.Mock(eq=lambda *_: ejecutar))


class Miniaturas(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.admin = Rpc({"video_agente_identificar": "p", "video_registrar": {"resultado": "ok", "id_video": 7},
                          "video_guardar_miniatura": {"resultado": "ok"}})
        for objeto, nombre, valor in ((auth, "cliente_admin", self.admin),):
            p = mock.patch.object(objeto, nombre, return_value=valor); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"VIDEOS_ENTORNO": ENTORNO, "SUPABASE_URL": "http://local"}); p.start(); self.addCleanup(p.stop)
        self.cabecera = {"Authorization": "Bearer secreto", "X-Video-Entorno": ENTORNO}

    def registrar(self, miniatura):
        import base64
        cuerpo = dict(Agente.CUERPO, **({"miniatura": miniatura if isinstance(miniatura, str) else base64.b64encode(miniatura).decode()}
                                        if miniatura is not None else {}))
        r = self.client.post("/api/videos/agente/registrar", json=cuerpo, headers=self.cabecera)
        return r, [x for x in self.admin.llamadas if x[0] == "video_guardar_miniatura"]

    def test_registro_con_miniatura_valida_la_guarda(self):
        r, guardadas = self.registrar(JPEG)
        self.assertEqual(r.json()["resultado"], "ok")
        self.assertEqual(len(guardadas), 1)
        params = guardadas[0][1]
        self.assertEqual((params["p_id_video"], params["p_id_profesora"]), (7, "p"))  # la profesora sale del token
        self.assertEqual(params["p_miniatura"], "\\x" + JPEG.hex())

    def test_miniatura_invalida_se_descarta_y_el_video_sigue(self):
        for mala in (b"GIF89a" + b"x" * 5000, b"\xff\xd8\xff" + b"x" * 200_000, b"\xff\xd8\xff", "no es base64!!"):
            with self.subTest(len(mala)):
                self.admin.llamadas.clear()
                r, guardadas = self.registrar(mala)
                self.assertEqual(r.json()["resultado"], "ok")
                self.assertEqual(guardadas, [])

    def test_sin_miniatura_el_registro_no_cambia(self):
        r, guardadas = self.registrar(None)
        self.assertEqual(r.json()["resultado"], "ok")
        self.assertEqual(guardadas, [])

    def test_si_el_registro_no_es_ok_no_se_guarda(self):
        self.admin.respuestas["video_registrar"] = {"resultado": "no_autorizada"}
        _, guardadas = self.registrar(JPEG)
        self.assertEqual(guardadas, [])

    def test_si_falla_guardarla_el_registro_sigue_ok(self):
        original = self.admin.rpc
        def rpc(nombre, params):
            if nombre == "video_guardar_miniatura":
                raise RuntimeError("base caída")
            return original(nombre, params)
        with mock.patch.object(self.admin, "rpc", side_effect=rpc):
            r = self.client.post("/api/videos/agente/registrar", headers=self.cabecera,
                                 json=dict(Agente.CUERPO, miniatura=__import__("base64").b64encode(JPEG).decode()))
        self.assertEqual(r.json()["resultado"], "ok")

    def ver(self, usuario, filas):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario); p.start(); self.addCleanup(p.stop)
        sb = Tabla(filas)
        with mock.patch.object(main, "_sb", return_value=sb):
            return self.client.get("/videos/7/miniatura.jpg", follow_redirects=False), sb

    def test_la_alumna_de_la_empresa_la_ve_sin_cache(self):
        r, sb = self.ver(ALUMNA, [{"miniatura": "\\x" + JPEG.hex()}])
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", JPEG))
        self.assertIn("no-store", r.headers["cache-control"])  # nunca queda guardada en el navegador
        self.assertEqual(sb.consultas, ["video"])  # con la sesión de la persona: decide la política de la base

    def test_otra_empresa_vencido_o_sin_miniatura_da_404(self):
        for filas in ([], [{"miniatura": None}], [{"miniatura": "\\x" + b"GIF89a".hex()}], [{"miniatura": "\\xzz"}]):
            with self.subTest(filas):
                r, _ = self.ver(ALUMNA, filas)
                self.assertEqual(r.status_code, 404)

    def test_sin_sesion_o_superadmin_no(self):
        for usuario in (None, SUPERADMIN):
            r, sb = self.ver(usuario, [{"miniatura": "\\x" + JPEG.hex()}])
            self.assertEqual(r.status_code, 303)
            self.assertEqual(sb.consultas, [])

    def test_biblioteca_con_miniatura_icono_y_respaldo(self):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=ALUMNA); p.start(); self.addCleanup(p.stop)
        with mock.patch.object(main, "_sb", return_value=Rpc({"videos_disponibles": [FILA]})):
            html = self.client.get("/videos").text
        tarjeta = html[html.index('class="video-card video-card-biblioteca"'):html.index("</a>", html.index("video-card-biblioteca"))]
        self.assertIn('href="/videos/7"', html)
        self.assertIn('src="/videos/7/miniatura.jpg"', tarjeta)
        self.assertIn('onerror="this.remove()"', tarjeta)  # sin miniatura queda el fondo con el ícono
        self.assertIn('class="video-play"', tarjeta)
        for texto in ("Gimnasia", "Lunes 16/11", "Pía Pérez", "39 min", "Disponible por 1 h 30 min más"):
            self.assertIn(texto, tarjeta)
        css = Path("app/static/style.css").read_text()
        self.assertIn("aspect-ratio: 16 / 9;", css)


class Agente(unittest.TestCase):
    CUERPO = {"inicio": "2026-11-16T08:01:00-03:00", "huella": "a" * 64, "duracion_seg": 2400, "tamano_bytes": 15,
              "partes": PARTES, "lista_hls": LISTA}

    def setUp(self):
        self.client = TestClient(main.app)
        self.admin = Rpc({"video_agente_identificar": "p", "video_registrar": {"resultado": "ok", "id_video": 7},
                          "video_consultar": {"resultado": "no_autorizada"},
                          "video_de_agente": {"id": 7, "id_empresa": 2, "estado": "subiendo", "partes": PARTES},
                          "video_publicar": {"resultado": "ok"}, "videos_a_limpiar": []})
        p = mock.patch.object(auth, "cliente_admin", return_value=self.admin)
        p.start()
        self.addCleanup(p.stop)
        self.cabecera = {"Authorization": "Bearer secreto", "X-Video-Entorno": ENTORNO}
        p = mock.patch.dict(os.environ, {"VIDEOS_ENTORNO": ENTORNO, "SUPABASE_URL": "http://local"})
        p.start()
        self.addCleanup(p.stop)

    def test_sin_token_o_con_token_malo(self):
        sin_token = {"X-Video-Entorno": ENTORNO}
        self.assertEqual(self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=sin_token).status_code, 401)
        self.admin.respuestas["video_agente_identificar"] = None
        r = self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=self.cabecera)
        self.assertEqual(r.status_code, 401)

    def test_sin_entorno_o_con_otro_entorno_rechaza_antes_de_mirar_el_token(self):
        for cabecera in ({"Authorization": "Bearer secreto"},
                         {"Authorization": "Bearer secreto", "X-Video-Entorno": "pruebas-otroentorno"}):
            r = self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=cabecera)
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.admin.llamadas, [])

    def test_servidor_sin_entorno_configurado_rechaza_todo(self):
        with mock.patch.dict(os.environ, {"VIDEOS_ENTORNO": ""}):
            r = self.client.post("/api/videos/agente/entorno", headers=self.cabecera)
        self.assertEqual(r.status_code, 403)

    def test_la_base_tambien_decide_el_entorno(self):
        self.client.post("/api/videos/agente/entorno", headers=self.cabecera)
        self.assertEqual(self.admin.llamadas[0][1]["p_entorno"], ENTORNO)

    def test_informa_el_entorno(self):
        r = self.client.post("/api/videos/agente/entorno", headers=self.cabecera)
        self.assertEqual(r.json(), {"entorno": ENTORNO})

    def test_error_se_guarda_sin_datos_sensibles(self):
        self.client.post("/api/videos/agente/7/error", headers=self.cabecera,
                         json={"mensaje": "falló https://x.supabase.co/a?token=abc en /Users/ana/zoom/clase.mp4"})
        mensaje = dict(self.admin.llamadas)["video_error"]["p_mensaje"]
        self.assertNotIn("token=abc", mensaje)
        self.assertNotIn("/Users/ana", mensaje)

    def test_el_token_se_compara_por_huella(self):
        self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=self.cabecera)
        self.assertEqual(self.admin.llamadas[0][1]["p_token_hash"], videos.huella_token("secreto"))

    def test_la_profesora_sale_del_token_y_no_del_pedido(self):
        cuerpo = dict(self.CUERPO, id_profesora="otra", id_empresa=99)
        self.client.post("/api/videos/agente/registrar", json=cuerpo, headers=self.cabecera)
        params = dict(self.admin.llamadas)["video_registrar"]
        self.assertEqual(params["p_id_profesora"], "p")
        self.assertNotIn("p_id_empresa", params)

    def test_consultar_pregunta_a_la_base_con_la_profesora_del_token(self):
        r = self.client.post("/api/videos/agente/consultar", headers=self.cabecera,
                             json={"inicio": "2026-11-16T08:01:00-03:00", "duracion_seg": 2340, "id_profesora": "otra"})
        self.assertEqual(r.json(), {"resultado": "no_autorizada"})
        self.assertEqual(dict(self.admin.llamadas)["video_consultar"]["p_id_profesora"], "p")

    def test_consultar_sin_token(self):
        r = self.client.post("/api/videos/agente/consultar", json={"inicio": "x", "duracion_seg": 1},
                             headers={"X-Video-Entorno": ENTORNO})
        self.assertEqual(r.status_code, 401)

    def test_video_de_otra_profesora(self):
        self.admin.respuestas["video_de_agente"] = None
        r = self.client.post("/api/videos/agente/7/urls-subida", headers=self.cabecera)
        self.assertEqual(r.status_code, 404)

    def test_urls_solo_para_lo_que_falta(self):
        bucket = mock.Mock()
        bucket.list.return_value = [{"name": "parte-00.ts", "metadata": {"size": 10}},
                                    {"name": "parte-01.ts", "metadata": {"size": 2}}]
        bucket.create_signed_upload_url.return_value = {"signed_url": "https://x/subir"}
        with mock.patch.object(videos, "almacen", return_value=bucket):
            r = self.client.post("/api/videos/agente/7/urls-subida", headers=self.cabecera).json()
        self.assertEqual(r["urls"], {"parte-01.ts": "https://x/subir"})
        bucket.remove.assert_called_once_with(["2/7/parte-01.ts"])

    def test_no_publica_si_falta_una_parte(self):
        bucket = mock.Mock()
        bucket.list.return_value = [{"name": "parte-00.ts", "metadata": {"size": 10}}]
        with mock.patch.object(videos, "almacen", return_value=bucket):
            r = self.client.post("/api/videos/agente/7/publicar", headers=self.cabecera).json()
        self.assertEqual(r["resultado"], "partes_incompletas")
        self.assertNotIn("video_publicar", dict(self.admin.llamadas))

    def test_no_publica_si_una_parte_tiene_otro_tamano(self):
        bucket = mock.Mock()
        bucket.list.return_value = [{"name": "parte-00.ts", "metadata": {"size": 10}},
                                    {"name": "parte-01.ts", "metadata": {"size": 4}}]
        with mock.patch.object(videos, "almacen", return_value=bucket):
            r = self.client.post("/api/videos/agente/7/publicar", headers=self.cabecera).json()
        self.assertEqual(r["resultado"], "partes_incompletas")

    def test_regresion_a1_en_error_decide_la_base(self):
        self.admin.respuestas["video_de_agente"]["estado"] = "error"
        self.admin.respuestas["video_publicar"] = {"resultado": "no_subiendo"}
        with mock.patch.object(videos, "almacen"):
            r = self.client.post("/api/videos/agente/7/publicar", headers=self.cabecera).json()
        self.assertEqual(r["resultado"], "no_subiendo")

    def test_publica_con_todas_las_partes(self):
        bucket = mock.Mock()
        bucket.list.return_value = [{"name": p["nombre"], "metadata": {"size": p["bytes"]}} for p in PARTES]
        with mock.patch.object(videos, "almacen", return_value=bucket):
            r = self.client.post("/api/videos/agente/7/publicar", headers=self.cabecera).json()
        self.assertEqual(r["resultado"], "ok")


class Limpieza(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_sin_secreto_configurado_no_corre(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": ""}):
            self.assertEqual(self.client.get("/api/videos/limpieza").status_code, 503)

    def test_secreto_incorrecto(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "bien"}):
            r = self.client.get("/api/videos/limpieza", headers={"Authorization": "Bearer mal"})
        self.assertEqual(r.status_code, 401)

    def limpiar(self, listado, remove=None):
        admin = Rpc({"videos_a_limpiar": [{"id": 7, "id_empresa": 2, "partes": PARTES}]})
        bucket = mock.Mock()
        bucket.list.return_value = listado
        bucket.remove.side_effect = remove
        with mock.patch.object(auth, "cliente_admin", return_value=admin), \
                mock.patch.object(videos, "almacen", return_value=bucket):
            return videos.limpiar(), admin, bucket

    def test_borra_verifica_y_marca(self):
        resultado, admin, bucket = self.limpiar([])
        self.assertEqual(resultado, {"eliminados": 1, "errores": 0})
        bucket.remove.assert_called_once_with(["2/7/parte-00.ts", "2/7/parte-01.ts"])
        self.assertIn("video_marcar_eliminado", dict(admin.llamadas))

    def test_regresion_a7_si_falla_el_borrado_registra_y_no_marca(self):
        with self.assertLogs(level="ERROR"):
            resultado, admin, _ = self.limpiar([], RuntimeError("caída"))
        self.assertEqual(resultado, {"eliminados": 0, "errores": 1})
        llamadas = dict(admin.llamadas)
        self.assertNotIn("video_marcar_eliminado", llamadas)
        self.assertEqual(llamadas["video_limpieza_error"], {"p_id_video": 7, "p_mensaje": "caída"})

    def test_si_quedan_archivos_no_marca(self):
        with self.assertLogs(level="ERROR"):
            resultado, admin, _ = self.limpiar([{"name": "parte-01.ts", "metadata": {"size": 5}}])
        self.assertEqual(resultado["errores"], 1)
        self.assertNotIn("video_marcar_eliminado", dict(admin.llamadas))
        self.assertIn("parte-01.ts", dict(admin.llamadas)["video_limpieza_error"]["p_mensaje"])

    def test_el_cron_informa_el_resultado(self):
        with mock.patch.object(videos, "limpiar", return_value={"eliminados": 2, "errores": 1}), \
                mock.patch.dict(os.environ, {"CRON_SECRET": "bien"}):
            r = self.client.get("/api/videos/limpieza", headers={"Authorization": "Bearer bien"})
        self.assertEqual(r.json(), {"eliminados": 2, "errores": 1})


class Configuraciones(unittest.TestCase):
    def test_ningun_ejemplo_apunta_a_produccion(self):
        raiz = Path(__file__).resolve().parent.parent
        for archivo in (raiz / ".env.example", raiz / "agente" / "config.ejemplo.json"):
            self.assertNotIn("vercel.app", archivo.read_text())

    def test_script_del_token_se_niega_fuera_de_localhost(self):
        raiz = Path(__file__).resolve().parent.parent
        fuente = (raiz / "scripts" / "video_agente.py").read_text()
        self.assertIn("--remoto", fuente)


if __name__ == "__main__":
    unittest.main()


class GrabacionesIndependientes(unittest.TestCase):
    """Contenido grabado solo para la biblioteca: pantalla de la docente, rutas y permisos (la base, en
    tests/base_local/test_videos_independientes_base.py)."""
    MIAS = [{"id": 3, "titulo": "Yoga suave", "descripcion": None, "estado": "esperando", "creada_en": "2026-11-16T10:00:00+00:00",
             "vence_espera_en": "2026-11-16T13:00:00+00:00", "vence_en": None, "id_video": None},
            {"id": 2, "titulo": "Estiramientos", "descripcion": None, "estado": "publicada", "creada_en": "2026-11-15T10:00:00+00:00",
             "vence_espera_en": "2026-11-15T13:00:00+00:00", "vence_en": "2026-11-16T11:00:00+00:00", "id_video": 9}]

    def setUp(self):
        self.client = TestClient(main.app)
        especial = {**FILA, "id": 9, "clase": "Estiramientos", "independiente": True, "tipo": "Yoga"}
        self.sb = Rpc({"videos_disponibles": [FILA, especial], "mis_grabaciones_independientes": self.MIAS,
                       "grabacion_independiente_crear": {"resultado": "ok", "id": 4, "vence_espera_en": "2026-11-16T16:00:00+00:00"},
                       "grabacion_independiente_cancelar": {"resultado": "ok"}})
        p = mock.patch.object(main, "_sb", return_value=self.sb)
        p.start()
        self.addCleanup(p.stop)
        self.tipos = [{"id": 5, "nombre": "Yoga", "activo": True}, {"id": 6, "nombre": "Pilates", "activo": True}]
        p = mock.patch.object(videos.tipos_clase, "activos", side_effect=lambda sb: self.tipos)
        p.start()
        self.addCleanup(p.stop)

    def como(self, usuario):
        p = mock.patch.object(auth, "_usuario_cacheado", return_value=usuario)
        p.start()
        self.addCleanup(p.stop)

    def llamadas(self, nombre):
        return [params for n, params in self.sb.llamadas if n == nombre]

    def test_la_docente_ve_el_formulario_y_sus_grabaciones(self):
        for usuario in (PROFESORA, ADMIN):
            self.como(usuario)
            r = self.client.get("/videos")
            self.assertEqual(r.status_code, 200)
            for texto in ("Grabar una nueva clase", 'action="/videos/grabaciones"', "Nombre de la clase", "Tipo de clase",
                          '<option value="5">Yoga</option>', '<option value="6">Pilates</option>', "Empezar a grabar",
                          "Mis clases grabadas", "Yoga suave", "Pendiente de grabación",
                          "Tenés tiempo hasta las 10:00 para comenzar a grabar.", "/videos/grabaciones/3/cancelar",
                          'href="/videos/9">Ver video', "Accedé a tus clases grabadas durante las 24 horas posteriores",
                          "Registrá una clase por vez"):
                self.assertIn(texto, r.text)

    def test_empezar_a_grabar_explica_los_pasos_y_no_promete_que_graba(self):
        self.como(PROFESORA)
        html = self.client.get("/videos").text
        for texto in ("Abrir zoom.us", "Unirse", "Grabar", "Finalizar la reunión para todos", "ID personal de reunión de Zoom",
                      "zoommtg://zoom.us/join?action=join&confno=", 'class="btn btn-chico abrir-zoom"',
                      "no puede ver si Zoom está grabando"):
            self.assertIn(texto, html)
        self.assertNotIn("Grabando", html)
        campo = html[html.index('id="zoom-id"') - 200:html.index('id="zoom-id"') + 200]
        self.assertNotIn("name=", campo.split("<input")[-1].split(">")[0])  # el ID de Zoom no se manda al servidor

    def test_la_alumna_no_ve_el_formulario_ni_se_consultan_grabaciones(self):
        self.sb.llamadas.clear()
        self.como(ALUMNA)
        r = self.client.get("/videos")
        self.assertNotIn("Grabar una nueva clase", r.text)
        self.assertEqual(self.llamadas("mis_grabaciones_independientes"), [])

    def test_la_tarjeta_marca_la_grabacion_especial(self):
        self.como(ALUMNA)
        r = self.client.get("/videos")
        self.assertEqual(r.text.count("Clase grabada"), 1)
        self.assertIn("Estiramientos", r.text)
        self.assertIn('<span class="video-dato">Yoga</span>', r.text)

    def test_crear_pasa_el_titulo_limpio_y_avisa_la_hora_local(self):
        self.como(PROFESORA)
        r = self.client.post("/videos/grabaciones", data={"titulo": "  Yoga   suave ", "id_tipo_clase": "6", "descripcion": " "},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.llamadas("grabacion_independiente_crear"),
                         [{"p_titulo": "Yoga suave", "p_id_tipo_clase": 6, "p_descripcion": None}])
        self.assertIn("ok=1", r.headers["location"])
        self.assertIn("13%3A00", r.headers["location"])  # 16:00 UTC = 13:00 en Montevideo

    def test_crear_con_titulo_corto_no_llama_a_la_base(self):
        self.como(PROFESORA)
        r = self.client.post("/videos/grabaciones", data={"titulo": "Yo", "id_tipo_clase": "5"}, follow_redirects=False)
        self.assertIn("ok=0", r.headers["location"])
        self.assertEqual(self.llamadas("grabacion_independiente_crear"), [])

    def test_mensajes_de_la_base(self):
        self.como(PROFESORA)
        for resultado, texto in (("sin_computadora", "computadora autorizada"), ("ya_hay_una", "Ya ten%C3%A9s una clase esperando")):
            self.sb.respuestas["grabacion_independiente_crear"] = {"resultado": resultado}
            r = self.client.post("/videos/grabaciones", data={"titulo": "Yoga suave", "id_tipo_clase": "5"}, follow_redirects=False)
            self.assertIn("ok=0", r.headers["location"])
            self.assertIn(texto.replace(" ", "+"), r.headers["location"])

    def test_tipo_obligatorio_y_de_la_lista_activa_de_su_empresa(self):
        self.como(PROFESORA)
        for valor in ("", "abc", "99"):  # vacío, no numérico, o que no está entre los activos de su empresa
            r = self.client.post("/videos/grabaciones", data={"titulo": "Yoga suave", "id_tipo_clase": valor}, follow_redirects=False)
            self.assertIn("ok=0", r.headers["location"], valor)
            self.assertIn("tipo+de+clase+activo", r.headers["location"], valor)
        self.assertEqual(self.llamadas("grabacion_independiente_crear"), [])

    def test_sin_tipos_activos_no_hay_formulario_ni_se_puede_crear(self):
        self.tipos = []
        self.como(PROFESORA)
        r = self.client.get("/videos")
        self.assertNotIn('action="/videos/grabaciones"', r.text)
        self.assertIn("No hay tipos de clase activos", r.text)
        r = self.client.post("/videos/grabaciones", data={"titulo": "Yoga suave", "id_tipo_clase": "5"}, follow_redirects=False)
        self.assertIn("ok=0", r.headers["location"])
        self.assertIn("No+hay+tipos+de+clase+activos", r.headers["location"])
        self.assertEqual(self.llamadas("grabacion_independiente_crear"), [])

    def test_el_detalle_muestra_el_tipo_de_la_clase_grabada(self):
        self.como(ALUMNA)
        self.sb.respuestas["videos_disponibles"] = [{**FILA, "id": 9, "clase": "Estiramientos", "independiente": True, "tipo": "Yoga"}]
        r = self.client.get("/videos/9")
        self.assertIn("Yoga · Lunes 16/11", r.text)

    def test_la_alumna_no_puede_crear_ni_cancelar(self):
        self.como(ALUMNA)
        for url, datos in (("/videos/grabaciones", {"titulo": "Yoga suave"}), ("/videos/grabaciones/3/cancelar", {})):
            r = self.client.post(url, data=datos, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertNotIn("/videos?", r.headers["location"])
        self.assertEqual(self.llamadas("grabacion_independiente_crear") + self.llamadas("grabacion_independiente_cancelar"), [])

    def test_sin_sesion_va_al_login(self):
        self.como(None)
        r = self.client.post("/videos/grabaciones", data={"titulo": "Yoga suave"}, follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_cancelar(self):
        self.como(PROFESORA)
        r = self.client.post("/videos/grabaciones/3/cancelar", follow_redirects=False)
        self.assertEqual(self.llamadas("grabacion_independiente_cancelar"), [{"p_id": 3}])
        self.assertIn("ok=1", r.headers["location"])
        self.sb.respuestas["grabacion_independiente_cancelar"] = {"resultado": "no_se_puede"}
        r = self.client.post("/videos/grabaciones/3/cancelar", follow_redirects=False)
        self.assertIn("ok=0", r.headers["location"])

    def test_ultimas_grabaciones_marca_las_especiales(self):
        fila = {"id": 9, "clase": "Estiramientos", "independiente": True, "fecha": "2026-11-16", "hora_inicio": "10:05:00",
                "estado": "publicado", "vencido": False, "nunca_publicado": False, "intentos": 1, "ultimo_error": None,
                "limpieza_intentos": 0, "limpieza_error": None}
        salida = videos.ultimas_grabaciones(Rpc({"videos_ultimas_grabaciones": [fila]}))
        self.assertTrue(salida[0]["independiente"])
        self.assertEqual((salida[0]["clase"], salida[0]["hora"]), ("Estiramientos", "10:05"))
