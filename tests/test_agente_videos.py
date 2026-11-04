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
        config.write_text(json.dumps({"url": "http://127.0.0.1:8000", "token": "t", "carpeta_zoom": str(self.zoom),
                                      "carpeta_trabajo": str(self.d / "t2")}))
        with mock.patch.object(sys, "argv", ["x", "--config", str(config), "--una-vez"]), \
                mock.patch.object(ag.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(ag, "pasada", side_effect=PermissionError("Operation not permitted")) as pasada, \
                mock.patch.object(ag.logging, "basicConfig"), self.assertLogs(level="ERROR") as logs:
            self.assertEqual(ag.main(), 0)
        pasada.assert_called_once()
        self.assertIn("Acceso total al disco", logs.output[0])


if __name__ == "__main__":
    unittest.main()
