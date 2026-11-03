"""Programa local de Videos On Demand: división de la lista HLS, detección de archivos completos, reintentos y
que no se duplique nada. Sin FFmpeg, Zoom ni red: la compresión y el servidor se simulan.

Uso: python -m unittest tests.test_agente_videos -v
"""
import json
import sys
import tempfile
import unittest
import urllib.error
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
    def test_hora_de_la_carpeta_en_montevideo(self):
        h = ag.hora_de_carpeta("2026-11-16 08.01.23 Clase de Gimnasia")
        self.assertEqual(h.isoformat(), "2026-11-16T08:01:23-03:00")
        self.assertIsNone(ag.hora_de_carpeta("otra cosa"))

    def test_toma_el_mp4_mas_grande_de_cada_reunion(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / "2026-11-16 08.01.23 Clase"
            r.mkdir()
            (r / "chico.mp4").write_bytes(b"1")
            (r / "grande.mp4").write_bytes(b"123")
            (r / "audio.m4a").write_bytes(b"12345")
            (r / "double_click_to_convert_01.zoom").write_bytes(b"1")
            (Path(d) / "sin_video").mkdir()
            self.assertEqual([p.name for p in ag.buscar_grabaciones(Path(d))], ["grande.mp4"])


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
    def pasar(self, servidor, ahora):
        ag.pasada(servidor, self.zoom, self.trabajo, self.cola, ahora)

    def test_no_procesa_un_archivo_recien_aparecido(self):
        with mock.patch.object(ag, "procesar") as procesar:
            self.pasar(mock.Mock(), 1000)
        procesar.assert_not_called()

    def test_publicado_no_se_vuelve_a_procesar(self):
        with mock.patch.object(ag, "procesar", return_value=("publicado", 7)) as procesar:
            self.pasar(mock.Mock(), 1000)
            self.pasar(mock.Mock(), 1200)
            self.pasar(mock.Mock(), 9999)
        self.assertEqual(procesar.call_count, 1)
        self.assertEqual((self.fila()["estado"], self.fila()["id_video"]), ("publicado", 7))

    def test_sin_internet_reintenta_con_espera(self):
        error = urllib.error.URLError("sin conexión")
        with mock.patch.object(ag, "procesar", side_effect=error) as procesar:
            self.pasar(mock.Mock(), 1000)
            self.pasar(mock.Mock(), 1200)
            self.pasar(mock.Mock(), 1230)
            self.assertEqual(procesar.call_count, 1)
            self.pasar(mock.Mock(), 1261)
            self.assertEqual(procesar.call_count, 2)
        self.assertEqual((self.fila()["estado"], self.fila()["intentos"]), ("esperando", 2))

    def test_una_decision_del_servidor_no_se_reintenta(self):
        with mock.patch.object(ag, "procesar", return_value=("sin_clase", None)) as procesar:
            self.pasar(mock.Mock(), 1000)
            self.pasar(mock.Mock(), 1200)
            self.pasar(mock.Mock(), 9999)
        self.assertEqual(procesar.call_count, 1)
        self.assertEqual(self.fila()["estado"], "sin_clase")

    def test_esperas_crecientes(self):
        self.assertEqual([ag.espera_reintento(i) for i in (1, 2, 3, 4, 9)], [60, 300, 900, 3600, 3600])


class Procesar(Base):
    PARTES = [{"nombre": "parte-00.ts", "inicio": 0, "bytes": 5}]

    def servidor(self, registrar=None, publicar=None):
        s = mock.Mock()

        def post(ruta, datos=None):
            if ruta == "registrar":
                self.enviado = datos
                return registrar or {"resultado": "ok", "id_video": 7}
            if ruta.endswith("urls-subida"):
                return {"resultado": "ok", "urls": {"parte-00.ts": "https://x/subir"}}
            return publicar or {"resultado": "ok"}
        s.post.side_effect = post
        return s

    def correr(self, servidor, duracion=2400):
        carpeta = self.trabajo / ("a" * 64)
        carpeta.mkdir(exist_ok=True)
        (carpeta / "parte-00.ts").write_bytes(b"12345")
        with mock.patch.object(ag, "duracion_segundos", return_value=duracion), \
                mock.patch.object(ag, "comprimir", return_value=(self.PARTES, "#EXTM3U\n")), \
                mock.patch.object(ag, "subir_parte") as subir:
            return ag.procesar(servidor, self.video, "a" * 64, self.trabajo), subir

    def test_camino_completo(self):
        (resultado, id_video), subir = self.correr(self.servidor())
        self.assertEqual((resultado, id_video), ("publicado", 7))
        self.assertEqual(self.enviado["inicio"], "2026-11-16T08:01:23-03:00")
        self.assertEqual(self.enviado["partes"], [{"nombre": "parte-00.ts", "bytes": 5}])
        subir.assert_called_once()
        self.assertFalse((self.trabajo / ("a" * 64)).exists())

    def test_grabacion_corta_se_ignora(self):
        s = self.servidor()
        (resultado, _), _ = self.correr(s, duracion=300)
        self.assertEqual(resultado, "muy_corta")
        s.post.assert_not_called()

    def test_ya_publicado_no_sube(self):
        (resultado, _), subir = self.correr(self.servidor({"resultado": "ya_publicado", "id_video": 7}))
        self.assertEqual(resultado, "ya_publicado")
        subir.assert_not_called()
        self.assertIn(resultado, ag.RESULTADOS_FINALES)

    def test_si_no_publica_conserva_las_partes_para_reintentar(self):
        with self.assertRaises(ag.ErrorAgente):
            self.correr(self.servidor(publicar={"resultado": "partes_incompletas"}))
        self.assertTrue((self.trabajo / ("a" * 64) / "parte-00.ts").exists())

    def test_reintento_no_recomprime(self):
        carpeta = self.trabajo / "h"
        carpeta.mkdir()
        (carpeta / "manifiesto.json").write_text(json.dumps({"partes": self.PARTES, "lista_hls": "#EXTM3U\n"}))
        with mock.patch.object(ag.subprocess, "run") as run:
            self.assertEqual(ag.comprimir(self.video, carpeta)[1], "#EXTM3U\n")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
