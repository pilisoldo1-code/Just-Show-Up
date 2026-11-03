"""Videos On Demand: textos, lista firmada, permisos de las rutas, programa local y limpieza. La base y Storage se
simulan; las reglas de la base se prueban en tests/base_local/test_videos_base.py.

Uso: python -m unittest tests.test_videos -v
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")

from fastapi.testclient import TestClient

from app import auth, main, videos
from app.auth import Usuario

ALUMNA = Usuario(id="a", mail="a@prueba.invalid", nombre="Ana", apellido="P", rol="alumna", token="t")
PROFESORA = Usuario(id="p", mail="p@prueba.invalid", nombre="Pía", apellido="P", rol="profesora", token="t")
SUPERADMIN = Usuario(id="s", mail="s@prueba.invalid", nombre="Sol", apellido="P", rol="alumna", token="t",
                     es_superadmin=True, con_membresias=True)
FILA = {"id": 7, "clase": "Gimnasia", "fecha": "2026-11-16", "hora_inicio": "08:00:00", "profesora": "Pía Pérez",
        "duracion_seg": 2340, "segundos_restantes": 5400}
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
        self.assertEqual(videos.texto_restante(5 * 3600 + 20 * 60), "Disponible 5 h 20 min más")
        self.assertEqual(videos.texto_restante(600), "Disponible 10 min más")
        self.assertEqual(videos.texto_restante(10), "Disponible 1 min más")

    def test_duracion(self):
        self.assertEqual(videos.texto_duracion(2400), "40 minutos")

    def test_tarjeta_con_dia_y_tilde(self):
        fila = dict(FILA, fecha="2026-11-18")
        self.assertEqual(videos._tarjeta(fila)["fecha_txt"], "Miércoles 18/11")


class Lista(unittest.TestCase):
    def reproducir(self, segundos, firmadas=None):
        sb = Rpc({"video_reproducir": {"resultado": "ok", "id_empresa": 2, "partes": PARTES, "lista_hls": LISTA,
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
        self.assertEqual(bucket.create_signed_urls.call_args.args[1], 120)

    def test_vencido_no_firma(self):
        (estado, _), bucket = self.reproducir(0)
        self.assertEqual(estado, "no_disponible")
        bucket.create_signed_urls.assert_not_called()

    def test_parte_sin_firmar_no_reproduce(self):
        (estado, _), _ = self.reproducir(500, [{"path": "2/7/parte-00.ts", "signedURL": "https://x/a"},
                                               {"path": "2/7/parte-01.ts", "signedURL": None}])
        self.assertEqual(estado, "no_disponible")

    def test_rechazo_de_la_base_no_firma(self):
        sb = Rpc({"video_reproducir": {"resultado": "sin_presupuesto"}})
        with mock.patch.object(videos, "almacen") as almacen:
            self.assertEqual(videos.lista_para_reproducir(sb, 7)[0], "sin_presupuesto")
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

    def test_alumna_y_profesora_ven_la_lista(self):
        for usuario in (ALUMNA, PROFESORA):
            self.como(usuario)
            r = self.client.get("/videos")
            self.assertEqual(r.status_code, 200)
            for texto in ("Gimnasia", "Lunes 16/11", "Pía Pérez", "39 minutos", "Disponible 1 h 30 min más"):
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

    def test_reproductor(self):
        self.como(ALUMNA)
        r = self.client.get("/videos/7")
        self.assertIn('data-lista="/videos/7/lista.m3u8"', r.text)
        self.assertIn("playsinline", r.text)

    def test_lista_hls_y_sus_rechazos(self):
        self.como(ALUMNA)
        with mock.patch.object(videos, "lista_para_reproducir", return_value=("ok", "#EXTM3U\n")):
            r = self.client.get("/videos/7/lista.m3u8")
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(r.headers["cache-control"], "no-store")
        for estado, codigo in (("no_disponible", 404), ("limite_usuaria", 429), ("sin_presupuesto", 429)):
            with mock.patch.object(videos, "lista_para_reproducir", return_value=(estado, "")):
                self.assertEqual(self.client.get("/videos/7/lista.m3u8").status_code, codigo)

    def test_falla_de_la_base_muestra_aviso(self):
        self.como(ALUMNA)
        with mock.patch.object(main, "_sb", side_effect=RuntimeError("caída")), self.assertLogs(level="ERROR"):
            self.assertIn("No pudimos cargar los videos", self.client.get("/videos").text)


class Agente(unittest.TestCase):
    CUERPO = {"inicio": "2026-11-16T08:01:00-03:00", "huella": "a" * 64, "duracion_seg": 2400, "tamano_bytes": 15,
              "partes": PARTES, "lista_hls": LISTA}

    def setUp(self):
        self.client = TestClient(main.app)
        self.admin = Rpc({"video_agente_identificar": "p", "video_registrar": {"resultado": "ok", "id_video": 7},
                          "video_de_agente": {"id": 7, "id_empresa": 2, "estado": "subiendo", "partes": PARTES},
                          "video_publicar": {"resultado": "ok"}, "videos_a_limpiar": []})
        p = mock.patch.object(auth, "cliente_admin", return_value=self.admin)
        p.start()
        self.addCleanup(p.stop)
        self.cabecera = {"Authorization": "Bearer secreto"}

    def test_sin_token_o_con_token_malo(self):
        self.assertEqual(self.client.post("/api/videos/agente/registrar", json=self.CUERPO).status_code, 401)
        self.admin.respuestas["video_agente_identificar"] = None
        r = self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=self.cabecera)
        self.assertEqual(r.status_code, 401)

    def test_el_token_se_compara_por_huella(self):
        self.client.post("/api/videos/agente/registrar", json=self.CUERPO, headers=self.cabecera)
        self.assertEqual(self.admin.llamadas[0][1]["p_token_hash"], videos.huella_token("secreto"))

    def test_la_profesora_sale_del_token_y_no_del_pedido(self):
        cuerpo = dict(self.CUERPO, id_profesora="otra", id_empresa=99)
        self.client.post("/api/videos/agente/registrar", json=cuerpo, headers=self.cabecera)
        params = dict(self.admin.llamadas)["video_registrar"]
        self.assertEqual(params["p_id_profesora"], "p")
        self.assertNotIn("p_id_empresa", params)

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

    def test_borra_archivos_y_marca(self):
        admin = Rpc({"videos_a_limpiar": [{"id": 7, "id_empresa": 2, "partes": PARTES}]})
        bucket = mock.Mock()
        with mock.patch.object(auth, "cliente_admin", return_value=admin), \
                mock.patch.object(videos, "almacen", return_value=bucket), \
                mock.patch.dict(os.environ, {"CRON_SECRET": "bien"}):
            r = self.client.get("/api/videos/limpieza", headers={"Authorization": "Bearer bien"})
        self.assertEqual(r.json(), {"eliminados": 1})
        bucket.remove.assert_called_once_with(["2/7/parte-00.ts", "2/7/parte-01.ts"])
        self.assertIn("video_marcar_eliminado", dict(admin.llamadas))

    def test_si_falla_el_borrado_no_marca(self):
        admin = Rpc({"videos_a_limpiar": [{"id": 7, "id_empresa": 2, "partes": PARTES}]})
        bucket = mock.Mock()
        bucket.remove.side_effect = RuntimeError("caída")
        with mock.patch.object(auth, "cliente_admin", return_value=admin), \
                mock.patch.object(videos, "almacen", return_value=bucket), self.assertLogs(level="ERROR"):
            self.assertEqual(videos.limpiar(), 0)
        self.assertNotIn("video_marcar_eliminado", dict(admin.llamadas))


if __name__ == "__main__":
    unittest.main()
