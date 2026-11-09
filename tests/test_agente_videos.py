"""Programa local de Videos On Demand: división de la lista HLS, detección de archivos completos, reintentos y
que no se duplique nada. Sin FFmpeg, Zoom ni red: la compresión y el servidor se simulan.

Uso: python -m unittest tests.test_agente_videos -v
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agente"))
import agente_videos as ag  # noqa: E402


def lista(*largos):
    lineas, inicio = ["#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD"], 0
    for n in largos:
        lineas += ["#EXTINF:6.0,", f"#EXT-X-BYTERANGE:{n}@{inicio}", "salida.ts"]
        inicio += n
    return "\n".join(lineas + ["#EXT-X-ENDLIST"]) + "\n"


class Dividir(unittest.TestCase):
    def test_no_corta_segmentos_y_reinicia_los_rangos(self):
        partes, texto = ag.dividir_lista(lista(40, 40, 40), "salida.ts", 100)
        self.assertEqual(partes, [{"nombre": "parte-00.ts", "inicio": 0, "bytes": 80},
                                  {"nombre": "parte-01.ts", "inicio": 80, "bytes": 40}])
        self.assertIn("#EXT-X-BYTERANGE:40@40\nparte-00.ts", texto)
        self.assertIn("#EXT-X-BYTERANGE:40@0\nparte-01.ts", texto)
        self.assertNotIn("salida.ts", texto)
        self.assertIn("#EXT-X-ENDLIST", texto)

    def test_ninguna_parte_pasa_el_maximo(self):
        partes, _ = ag.dividir_lista(lista(*[300_000] * 400), "salida.ts")
        self.assertEqual(sum(p["bytes"] for p in partes), 120_000_000)
        self.assertEqual(len(partes), 3)
        self.assertTrue(all(p["bytes"] <= ag.TAMANO_MAXIMO_PARTE for p in partes))

    def test_segmento_demasiado_grande(self):
        with self.assertRaises(ag.ErrorAgente):
            ag.dividir_lista(lista(200), "salida.ts", 100)

    def test_lista_vacia_o_sin_rangos(self):
        with self.assertRaises(ag.ErrorAgente):
            ag.dividir_lista("#EXTM3U\n", "salida.ts")
        with self.assertRaises(ag.ErrorAgente):
            ag.dividir_lista("#EXTM3U\n#EXTINF:6,\nsalida.ts\n", "salida.ts")

    def test_cortar_reconstruye_el_original(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            datos = bytes(range(120))
            (d / "salida.ts").write_bytes(datos)
            partes, _ = ag.dividir_lista(lista(40, 40, 40), "salida.ts", 100)
            ag.cortar_archivo(d / "salida.ts", partes, d)
            self.assertEqual((d / "parte-00.ts").read_bytes() + (d / "parte-01.ts").read_bytes(), datos)


class Zoom(unittest.TestCase):
    def test_hora_de_la_carpeta_con_la_zona_de_la_computadora(self):
        anterior = os.environ.get("TZ")
        os.environ["TZ"] = "America/Montevideo"
        time.tzset()
        try:
            self.assertEqual(ag.hora_de_carpeta("2026-11-16 08.01.23 Clase").isoformat(), "2026-11-16T08:01:23-03:00")
        finally:
            os.environ.pop("TZ") if anterior is None else os.environ.update(TZ=anterior)
            time.tzset()
        self.assertIsNone(ag.hora_de_carpeta("otra cosa"))

    def test_no_depende_de_zoneinfo(self):
        # en Windows zoneinfo falla sin el paquete tzdata: el programa no debe necesitarlo
        fuente = Path(ag.__file__).read_text()
        self.assertNotIn("zoneinfo", fuente.lower())

    def test_ejemplo_no_apunta_a_produccion(self):
        carpeta = Path(ag.__file__).parent
        config = json.loads((carpeta / "config.ejemplo.json").read_text())
        self.assertIn("127.0.0.1", config["url"])
        for archivo in carpeta.iterdir():
            if archivo.is_file() and archivo.suffix != ".pyc":
                self.assertNotIn("vercel.app", archivo.read_text(errors="ignore"), archivo.name)

    def reunion(self, d, **archivos):
        r = Path(d) / "2026-11-16 08.01.23 Clase"
        r.mkdir()
        for nombre, tamano in archivos.items():
            with (r / nombre.replace("_mp4", ".mp4").replace("_m4a", ".m4a")).open("wb") as f:
                f.truncate(tamano)
        return r

    def test_un_video_con_audio_y_archivos_de_zoom(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.reunion(d, grande_mp4=5_000_000, chico_mp4=10, audio_m4a=9_000_000)
            (r / "double_click_to_convert_01.zoom").write_bytes(b"1")
            (Path(d) / "sin_video").mkdir()
            self.assertEqual([(v.name, n) for v, n in ag.buscar_grabaciones(Path(d))], [("grande.mp4", 1)])

    def test_cuenta_varios_videos_de_la_misma_reunion(self):
        with tempfile.TemporaryDirectory() as d:
            self.reunion(d, zoom_0_mp4=5_000_000, zoom_1_mp4=4_000_000)
            self.assertEqual(ag.buscar_grabaciones(Path(d))[0][1], 2)


class Candado(unittest.TestCase):
    def test_una_sola_ejecucion(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "candado.sqlite"
            primero = ag.tomar_candado(ruta)
            self.assertIsNotNone(primero)
            self.assertIsNone(ag.tomar_candado(ruta))
            primero.close()
            segundo = ag.tomar_candado(ruta)
            self.assertIsNotNone(segundo)
            segundo.close()


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = Path(t.name)
        self.zoom = self.d / "zoom"
        self.reunion = self.zoom / "2026-11-16 08.01.23 Clase"
        self.reunion.mkdir(parents=True)
        self.video = self.reunion / "zoom_0.mp4"
        self.video.write_bytes(b"contenido")
        self.trabajo = self.d / "trabajo"
        self.trabajo.mkdir()
        self.cola = ag.abrir_cola(self.trabajo / "cola.sqlite")
        self.addCleanup(self.cola.close)

    def fila(self):
        return self.cola.execute("select * from grabacion").fetchone()


class Estable(Base):
    def test_espera_tres_minutos_sin_cambios(self):
        self.assertFalse(ag.estable(self.cola, self.video, 1000))
        self.assertFalse(ag.estable(self.cola, self.video, 1100))
        self.assertTrue(ag.estable(self.cola, self.video, 1180))

    def test_si_crece_vuelve_a_esperar(self):
        ag.estable(self.cola, self.video, 1000)
        self.video.write_bytes(b"contenido mas largo")
        self.assertFalse(ag.estable(self.cola, self.video, 1200))
        self.assertFalse(ag.estable(self.cola, self.video, 1300))
        self.assertTrue(ag.estable(self.cola, self.video, 1380))

    def test_vacio_no_se_toma(self):
        self.video.write_bytes(b"")
        ag.estable(self.cola, self.video, 1000)
        self.assertFalse(ag.estable(self.cola, self.video, 2000))


class Cola(Base):
    def setUp(self):
        super().setUp()
        self.t0 = datetime.fromisoformat("2026-11-16T09:00:00").astimezone().timestamp()

    def pasar(self, ahora, servidor=None):
        ag.pasada(servidor or mock.Mock(), self.zoom, self.trabajo, self.cola, self.t0 + ahora)

    def test_no_procesa_un_archivo_recien_aparecido(self):
        with mock.patch.object(ag, "procesar") as procesar:
            self.pasar(0)
        procesar.assert_not_called()

    def test_publicado_no_se_vuelve_a_procesar(self):
        with mock.patch.object(ag, "procesar", return_value=("publicado", 7)) as procesar:
            self.pasar(0)
            self.pasar(200)
            self.pasar(9000)
        self.assertEqual(procesar.call_count, 1)
        self.assertEqual((self.fila()["estado"], self.fila()["id_video"]), ("publicado", 7))

    def test_sin_internet_reintenta_con_espera(self):
        error = urllib.error.URLError("sin conexión")
        with mock.patch.object(ag, "procesar", side_effect=error) as procesar:
            self.pasar(0)
            self.pasar(200)
            self.pasar(230)
            self.assertEqual(procesar.call_count, 1)
            self.pasar(261)
            self.assertEqual(procesar.call_count, 2)
        self.assertEqual((self.fila()["estado"], self.fila()["intentos"]), ("esperando", 2))

    def test_decisiones_finales_no_se_reintentan(self):
        for resultado in ("no_autorizada", "clase_ambigua", "duracion_fuera_de_rango", "ya_publicado"):
            with self.subTest(resultado):
                self.cola.execute("delete from grabacion")
                with mock.patch.object(ag, "procesar", return_value=(resultado, None)) as procesar:
                    self.pasar(0)
                    self.pasar(200)
                    self.pasar(9000)
                self.assertEqual(procesar.call_count, 1)
                self.assertEqual(self.fila()["estado"], resultado)

    def test_sin_clase_reintenta_y_despues_se_rinde(self):
        for resultado in ("sin_clase", "sin_espacio"):
            with self.subTest(resultado):
                self.cola.execute("delete from grabacion")
                with mock.patch.object(ag, "procesar", return_value=(resultado, None)) as procesar:
                    self.pasar(0)
                    self.pasar(200)
                    self.assertEqual(self.fila()["estado"], "esperando")
                    self.pasar(300)
                    self.assertEqual(procesar.call_count, 1)
                    self.pasar(200 + ag.ESPERA_DECISION_SEG + 1)
                    self.assertEqual(procesar.call_count, 2)
                    self.pasar(25 * 3600)
                self.assertEqual(self.fila()["estado"], resultado)

    def test_sin_clase_que_aparece_despues_se_publica(self):
        with mock.patch.object(ag, "procesar", side_effect=[("sin_clase", None), ("publicado", 7)]):
            self.pasar(0)
            self.pasar(200)
            self.pasar(200 + ag.ESPERA_DECISION_SEG + 1)
        self.assertEqual(self.fila()["estado"], "publicado")

    def test_varios_videos_no_se_adivina(self):
        with (self.reunion / "zoom_0.mp4").open("wb") as f:
            f.truncate(5_000_000)
        with (self.reunion / "zoom_1.mp4").open("wb") as f:
            f.truncate(4_000_000)
        with mock.patch.object(ag, "procesar") as procesar:
            self.pasar(0)
            self.pasar(200)
        procesar.assert_not_called()
        self.assertEqual(self.fila()["estado"], "varios_archivos")

    def test_grabacion_antigua_no_se_procesa(self):
        with mock.patch.object(ag, "procesar") as procesar:
            self.pasar(80 * 3600)
            self.pasar(80 * 3600 + 200)
        procesar.assert_not_called()
        self.assertEqual(self.fila()["estado"], "antigua")

    def test_esperas_crecientes(self):
        self.assertEqual([ag.espera_reintento(i) for i in (1, 2, 3, 4, 9)], [60, 300, 900, 3600, 3600])


class Procesar(Base):
    PARTES = [{"nombre": "parte-00.ts", "inicio": 0, "bytes": 5}]
    HUELLA = hashlib.sha256(b"contenido").hexdigest()

    def servidor(self, consulta=None, registrar=None, publicar=None):
        s = mock.Mock()
        self.rutas = []

        def post(ruta, datos=None):
            self.rutas.append(ruta)
            if ruta == "consultar":
                self.consulta = datos
                return consulta or {"resultado": "autorizada", "id_clase": 3, "id_empresa": 1}
            if ruta == "registrar":
                self.enviado = datos
                return registrar or {"resultado": "ok", "id_video": 7}
            if ruta.endswith("urls-subida"):
                return {"resultado": "ok", "urls": {"parte-00.ts": "https://x/subir"}}
            if ruta.endswith("error"):
                return {"resultado": "ok"}
            return publicar or {"resultado": "ok"}
        s.post.side_effect = post
        return s

    def correr(self, servidor, duracion=2400):
        carpeta = self.trabajo / self.HUELLA
        carpeta.mkdir(exist_ok=True)
        (carpeta / "parte-00.ts").write_bytes(b"12345")
        with mock.patch.object(ag, "duracion_segundos", return_value=duracion), \
                mock.patch.object(ag, "comprimir", return_value=(self.PARTES, "#EXTM3U\n")) as comprimir, \
                mock.patch.object(ag, "subir_parte") as subir:
            self.comprimir = comprimir
            return ag.procesar(servidor, self.video, self.trabajo), subir

    def test_camino_completo_consulta_antes_de_comprimir(self):
        (resultado, id_video), subir = self.correr(self.servidor())
        self.assertEqual((resultado, id_video), ("publicado", 7))
        self.assertEqual(self.rutas[:2], ["consultar", "registrar"])
        self.assertEqual(self.consulta["duracion_seg"], 2400)
        self.assertEqual(self.enviado["partes"], [{"nombre": "parte-00.ts", "bytes": 5}])
        subir.assert_called_once()
        self.assertFalse((self.trabajo / self.HUELLA).exists())

    def test_no_autorizada_no_comprime_ni_sube(self):
        for resultado in ("no_autorizada", "sin_clase", "clase_ambigua", "duracion_fuera_de_rango"):
            with self.subTest(resultado):
                (r, _), subir = self.correr(self.servidor({"resultado": resultado}))
                self.assertEqual(r, resultado)
                self.comprimir.assert_not_called()
                subir.assert_not_called()
                self.assertEqual(self.rutas, ["consultar"])

    def test_la_duracion_la_decide_el_servidor(self):
        (r, _), _ = self.correr(self.servidor({"resultado": "duracion_fuera_de_rango"}), duracion=300)
        self.assertEqual(self.consulta["duracion_seg"], 300)
        self.assertEqual(r, "duracion_fuera_de_rango")

    def test_ya_publicado_al_registrar_no_sube(self):
        (resultado, _), subir = self.correr(self.servidor(registrar={"resultado": "ya_publicado", "id_video": 7}))
        self.assertEqual(resultado, "ya_publicado")
        subir.assert_not_called()

    def test_si_no_publica_avisa_y_conserva_las_partes(self):
        with self.assertRaises(ag.ErrorAgente):
            self.correr(self.servidor(publicar={"resultado": "partes_incompletas"}))
        self.assertIn("7/error", self.rutas)
        self.assertTrue((self.trabajo / self.HUELLA / "parte-00.ts").exists())

    def test_reintento_no_recomprime(self):
        carpeta = self.trabajo / "h"
        carpeta.mkdir()
        (carpeta / "manifiesto.json").write_text(json.dumps({"partes": self.PARTES, "lista_hls": "#EXTM3U\n"}))
        with mock.patch.object(ag.subprocess, "run") as run:
            self.assertEqual(ag.comprimir(self.video, carpeta)[1], "#EXTM3U\n")
        run.assert_not_called()


class Permisos(Base):
    def test_sin_permiso_no_cierra_el_programa(self):
        config = self.d / "config.json"
        config.write_text(json.dumps({"url": "http://127.0.0.1:8000", "token": "t", "entorno": "pruebas-abcd1234",
                                      "carpeta_zoom": str(self.zoom), "carpeta_trabajo": str(self.d / "t2")}))
        with mock.patch.object(sys, "argv", ["x", "--config", str(config), "--una-vez"]), \
                mock.patch.object(ag.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(ag.Servidor, "comprobar_entorno"), \
                mock.patch.object(ag, "pasada", side_effect=PermissionError("Operation not permitted")) as pasada, \
                mock.patch.object(ag.logging, "basicConfig"), self.assertLogs(level="ERROR") as logs:
            self.assertEqual(ag.main(), 0)
        pasada.assert_called_once()
        self.assertIn("Acceso total al disco", logs.output[0])


class Proteccion(Base):
    PRODUCCION = {"url": "https://just-show-up-theta.vercel.app", "entorno": "pruebas-abcd1234", "token": "t"}
    LOCAL = {"url": "http://127.0.0.1:8000", "entorno": "pruebas-abcd1234", "token": "t"}

    def arrancar(self, config, *extra):
        ruta = self.d / "config.json"
        ruta.write_text(json.dumps({**config, "carpeta_zoom": str(self.zoom), "carpeta_trabajo": str(self.d / "t3")}))
        with mock.patch.object(sys, "argv", ["x", "--config", str(ruta), "--una-vez", *extra]), \
                mock.patch.object(ag.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(ag.logging, "basicConfig"), \
                mock.patch.object(ag, "pasada") as pasada:
            return ag.main(), pasada

    def test_rechaza_produccion_aunque_el_entorno_parezca_de_prueba(self):
        codigo, pasada = self.arrancar(self.PRODUCCION)
        self.assertEqual(codigo, 2)
        pasada.assert_not_called()

    def test_validacion_de_la_configuracion(self):
        malas = [
            {"url": "http://127.0.0.1:8000"},  # sin entorno
            {"url": "http://127.0.0.1:8000", "entorno": "produccion-abcd1234"},
            {"url": "https://otro-sitio.com", "entorno": "pruebas-abcd1234"},
            self.PRODUCCION,
        ]
        for config in malas:
            with self.assertRaises(ag.EntornoNoAutorizado, msg=config):
                ag.validar_config(config, False)
        ag.validar_config(self.LOCAL, False)
        ag.validar_config({"url": "http://192.168.1.20:8000", "entorno": "pruebas-abcd1234"}, False)

    def test_produccion_exige_bandera_entorno_y_https(self):
        with self.assertRaises(ag.EntornoNoAutorizado):
            ag.validar_config({"url": "https://x.com", "entorno": "pruebas-abcd1234"}, True)
        with self.assertRaises(ag.EntornoNoAutorizado):
            ag.validar_config({"url": "http://x.com", "entorno": "produccion-abcd1234"}, True)
        ag.validar_config({"url": "https://x.com", "entorno": "produccion-abcd1234"}, True)

    def test_si_el_servidor_esta_en_otro_entorno_se_detiene(self):
        with mock.patch.object(ag.Servidor, "post", return_value={"entorno": "pruebas-otroentorno"}):
            codigo, pasada = self.arrancar(self.LOCAL)
        self.assertEqual(codigo, 2)
        pasada.assert_not_called()

    def test_si_el_servidor_rechaza_se_detiene_sin_reintentar(self):
        error = urllib.error.HTTPError("http://x", 403, "no", {}, None)
        with mock.patch.object(ag.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ag.EntornoNoAutorizado):
                ag.Servidor("http://127.0.0.1:8000", "t", "pruebas-abcd1234").post("entorno")

    def test_rechazo_durante_una_pasada_no_queda_como_reintento(self):
        servidor = mock.Mock()
        with mock.patch.object(ag, "procesar", side_effect=ag.EntornoNoAutorizado("no")):
            ag.pasada(servidor, self.zoom, self.trabajo, self.cola, 1000)
            with self.assertRaises(ag.EntornoNoAutorizado):
                ag.pasada(servidor, self.zoom, self.trabajo, self.cola, 1200)

    def test_manda_la_cabecera_de_entorno(self):
        respuesta = mock.MagicMock()
        respuesta.__enter__.return_value = respuesta
        with mock.patch.object(ag.urllib.request, "urlopen", return_value=respuesta) as urlopen, \
                mock.patch.object(ag.json, "load", return_value={"entorno": "pruebas-abcd1234"}):
            ag.Servidor("http://127.0.0.1:8000", "t", "pruebas-abcd1234").comprobar_entorno()
        self.assertEqual(urlopen.call_args.args[0].get_header("X-video-entorno"), "pruebas-abcd1234")

    def test_no_sube_a_produccion_aunque_el_servidor_lo_pida(self):
        servidor = ag.Servidor("http://127.0.0.1:8000", "t", "pruebas-abcd1234")
        parte = self.d / "parte.ts"
        parte.write_bytes(b"1")
        with mock.patch.object(ag.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(ag.EntornoNoAutorizado):
                ag.subir_parte(servidor, "https://just-show-up-theta.vercel.app/subir", parte)
        urlopen.assert_not_called()

    def test_mismas_huellas_que_el_servidor(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app import entorno
        self.assertEqual(ag.HUELLAS_PRODUCCION, entorno.HUELLAS_PRODUCCION)


class VerificarComprimido(Base):
    """Un MP4 cortado puede declarar 40 minutos: lo que vale es la duración del contenido comprimido."""

    def ffmpeg_falso(self, declarada=2400.0, origen=None, salida=None, errores="", miniatura=b"\xff\xd8\xff" + b"j" * 4000):
        origen = {"video": 2400.0, "audio": 2400.0} if origen is None else origen
        salida = {"video": 2400.0, "audio": 2399.8} if salida is None else salida
        self.llamadas = []

        def flujos(d):
            return json.dumps({"streams": [{"codec_type": t, "duration": str(v)} for t, v in d.items()]})

        def run(args, **_):
            self.llamadas.append(args)
            r = mock.Mock(returncode=0, stdout="", stderr="")
            if args[0] == "ffprobe" and "format=duration" in args:
                r.stdout = f"{declarada}\n"
            elif args[0] == "ffprobe":
                r.stdout = flujos(salida if args[-1].endswith(".ts") else origen)
            elif "null" in args:
                r.stderr = errores
            elif "-frames:v" in args:  # miniatura: None = FFmpeg falla
                if miniatura is None:
                    r.returncode, r.stderr = 1, "no se pudo extraer el fotograma"
                else:
                    Path(args[-1]).write_bytes(miniatura)
            else:  # compresión: deja el .ts y su lista como FFmpeg
                lista = Path(args[-1])
                (lista.parent / "salida.ts").write_bytes(b"x" * 300)
                lista.write_text("#EXTM3U\n#EXTINF:6.0,\n#EXT-X-BYTERANGE:300@0\nsalida.ts\n#EXT-X-ENDLIST\n")
            return r
        return mock.patch.object(ag.subprocess, "run", side_effect=run)

    def comprimir(self, carpeta="salida", **kwargs):
        carpeta = self.d / carpeta
        with self.ffmpeg_falso(**kwargs):
            return ag.comprimir(self.video, carpeta), carpeta

    def test_grabacion_completa_se_acepta(self):
        (partes, _), carpeta = self.comprimir()
        self.assertEqual([p["nombre"] for p in partes], ["parte-00.ts"])
        self.assertTrue((carpeta / "manifiesto.json").exists())
        self.assertFalse((carpeta / "manifiesto.json.tmp").exists())
        self.assertTrue(any("null" in a for a in self.llamadas))  # se decodificó entero

    def test_declara_40_minutos_pero_tiene_20_se_rechaza_y_no_deja_nada(self):
        with self.assertRaisesRegex(ag.ErrorAgente, "Grabación incompleta: el video dura 1200 s"):
            self.comprimir(salida={"video": 1199.5, "audio": 1199.2})
        carpeta = self.d / "salida"
        self.assertEqual(sorted(p.name for p in carpeta.iterdir()), [])  # ni partes, ni lista, ni manifiesto

    def test_audio_incompleto_o_faltante_se_rechaza(self):
        with self.assertRaisesRegex(ag.ErrorAgente, "el audio dura 1800 s"):
            self.comprimir(salida={"video": 2400.0, "audio": 1800.0})
        with self.assertRaisesRegex(ag.ErrorAgente, "no tiene audio"):
            self.comprimir(salida={"video": 2400.0})

    def test_original_sin_audio_no_exige_audio(self):
        (partes, _), _ = self.comprimir(origen={"video": 2400.0}, salida={"video": 2400.0})
        self.assertEqual(len(partes), 1)

    def test_tolerancia_sin_falsos_rechazos(self):
        self.comprimir("justo", salida={"video": 2400.0 - ag.TOLERANCIA_DURACION_SEG, "audio": 2399.5})
        with self.assertRaises(ag.ErrorAgente):
            self.comprimir("pasado", salida={"video": 2400.0 - ag.TOLERANCIA_DURACION_SEG - 0.5, "audio": 2399.5})

    def test_errores_al_decodificar_se_rechaza(self):
        with self.assertRaisesRegex(ag.ErrorAgente, "tiene errores"):
            self.comprimir(errores="[h264 @ 0x1] error while decoding MB 3 7")
        self.assertFalse((self.d / "salida" / "manifiesto.json").exists())

    def test_rechazo_en_procesar_no_consulta_registrar_ni_sube(self):
        servidor = mock.Mock()
        servidor.post.side_effect = lambda ruta, datos=None: {"resultado": "autorizada"} if ruta == "consultar" else self.fail(ruta)
        with self.ffmpeg_falso(salida={"video": 1199.5, "audio": 1199.2}), mock.patch.object(ag, "subir_parte") as subir:
            with self.assertRaises(ag.ErrorAgente):
                ag.procesar(servidor, self.video, self.trabajo)
        subir.assert_not_called()
        self.assertEqual([c.args[0] for c in servidor.post.call_args_list], ["consultar"])


class Miniatura(VerificarComprimido):
    def test_se_genera_al_10_por_ciento_en_480x270(self):
        (_, _), carpeta = self.comprimir()
        self.assertTrue(ag.miniatura_valida(carpeta / ag.MINIATURA))
        llamada = next(a for a in self.llamadas if "-frames:v" in a)
        self.assertEqual(llamada[llamada.index("-ss") + 1], "240.0")  # 10 % de 2400 s
        self.assertIn("pad=480:270", " ".join(llamada))

    def test_si_falla_el_video_sigue_sin_miniatura(self):
        (partes, _), carpeta = self.comprimir(miniatura=None)
        self.assertEqual(len(partes), 1)
        self.assertTrue((carpeta / "manifiesto.json").exists())
        self.assertFalse((carpeta / ag.MINIATURA).exists())

    def test_imagen_invalida_se_descarta(self):
        for mala in (b"GIF89a" + b"x" * 4000, b"\xff\xd8\xff" + b"x" * 200_000, b"\xff\xd8\xff"):
            with self.subTest(len(mala)):
                (_, _), carpeta = self.comprimir(f"mala-{len(mala)}", miniatura=mala)
                self.assertFalse((carpeta / ag.MINIATURA).exists())

    def test_rechazo_del_video_tambien_borra_la_miniatura(self):
        with self.assertRaises(ag.ErrorAgente):
            self.comprimir(salida={"video": 1199.5, "audio": 1199.2})
        self.assertEqual(list((self.d / "salida").iterdir()), [])

    def correr_procesar(self, con_miniatura):
        servidor = mock.Mock()
        enviados = {}
        def post(ruta, datos=None):
            enviados[ruta] = datos
            return {"consultar": {"resultado": "autorizada"}, "registrar": {"resultado": "no_autorizada"}}.get(ruta, {})
        servidor.post.side_effect = post
        with self.ffmpeg_falso(miniatura=b"\xff\xd8\xff" + b"j" * 4000 if con_miniatura else None):
            ag.procesar(servidor, self.video, self.trabajo)
        return enviados["registrar"]

    def test_se_envia_con_el_registro_solo_si_existe(self):
        import base64
        datos = self.correr_procesar(True)
        self.assertEqual(base64.b64decode(datos["miniatura"])[:3], b"\xff\xd8\xff")
        for carpeta in self.trabajo.iterdir():
            if carpeta.is_dir():
                import shutil; shutil.rmtree(carpeta)
        self.assertNotIn("miniatura", self.correr_procesar(False))

    def test_reintento_reutiliza_la_miniatura_sin_volver_a_generarla(self):
        self.correr_procesar(True)  # el registro «no_autorizada» deja la carpeta con su manifiesto
        self.llamadas.clear()
        servidor = mock.Mock()
        enviados = {}
        servidor.post.side_effect = lambda ruta, datos=None: enviados.setdefault(ruta, datos) and {} or (
            {"resultado": "autorizada"} if ruta == "consultar" else {"resultado": "no_autorizada"})
        with mock.patch.object(ag.subprocess, "run", side_effect=lambda args, **_: mock.Mock(
                returncode=0, stdout="2400.0\n", stderr="")) as run:
            ag.procesar(servidor, self.video, self.trabajo)
        self.assertFalse(any("-frames:v" in c.args[0] for c in run.call_args_list))  # ni comprime ni genera
        self.assertIn("miniatura", enviados["registrar"])


class ProbarArchivo(Base):
    def test_no_se_conecta_a_nada(self):
        partes = [{"nombre": "parte-00.ts", "inicio": 0, "bytes": 1000}]
        salida = self.d / "salida"
        salida.mkdir()
        with mock.patch.object(ag, "duracion_segundos", return_value=2400), \
                mock.patch.object(ag, "comprimir", return_value=(partes, "#EXTM3U\n")), \
                mock.patch.object(ag.urllib.request, "urlopen") as urlopen:
            self.assertEqual(ag.probar_archivo(self.video, salida), 0)
        urlopen.assert_not_called()
        self.assertTrue((salida / "lista.m3u8").exists())


if __name__ == "__main__":
    unittest.main()
