"""Que Videos On Demand no pueda apuntar a producción por accidente: reconocimiento de destinos, servidor, scripts y las
tablas nuevas en respaldo y verificación. Sin red: los scripts se cortan antes de conectarse.

Uso: python -m unittest tests.test_entorno -v
"""
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import empresa_demo  # noqa: E402
import restaurar_datos  # noqa: E402
import videos_prueba_datos as datos  # noqa: E402
import videos_prueba_migrar as migrar  # noqa: E402
from app import entorno  # noqa: E402

# Sale de la documentación: el identificador no se repite en archivos nuevos.
REF_PRODUCCION = re.search(r"project_id (\w+)", (RAIZ / "CLAUDE.md").read_text())[1]
URL_PRODUCCION = f"https://{REF_PRODUCCION}.supabase.co"
DSN_PRODUCCION = f"postgresql://postgres.{REF_PRODUCCION}:x@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
DSN_DIRECTA = f"postgresql://postgres:x@db.{REF_PRODUCCION}.supabase.co:5432/postgres"
URL_PRUEBA = "https://proyectodeprueba.supabase.co"
DSN_PRUEBA = "postgresql://postgres.proyectodeprueba:x@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"


class Reconocimiento(unittest.TestCase):
    def test_produccion_se_reconoce(self):
        self.assertTrue(entorno.url_es_produccion(URL_PRODUCCION))
        self.assertTrue(entorno.url_es_produccion("https://just-show-up-theta.vercel.app/videos"))
        self.assertTrue(entorno.url_es_produccion("https://just-show-up-amber.vercel.app"))
        self.assertTrue(entorno.es_produccion(entorno.ref_de_conexion(DSN_PRODUCCION)))
        self.assertTrue(entorno.es_produccion(entorno.ref_de_conexion(DSN_DIRECTA)))
        self.assertTrue(entorno.url_es_produccion(URL_PRODUCCION.upper()))

    def test_lo_demas_no(self):
        for url in (URL_PRUEBA, "http://127.0.0.1:8000", "", None):
            self.assertFalse(entorno.url_es_produccion(url))

    def test_exigir_proyecto(self):
        entorno.exigir_proyecto_prueba("http://127.0.0.1:54321", None)
        entorno.exigir_proyecto_prueba(URL_PRUEBA, "proyectodeprueba")
        casos = [(URL_PRODUCCION, REF_PRODUCCION), (URL_PRUEBA, None), (URL_PRUEBA, "otro"), ("https://otro.com", "otro"), (None, None)]
        for url, proyecto in casos:
            with self.assertRaises(entorno.DestinoNoPermitido, msg=url):
                entorno.exigir_proyecto_prueba(url, proyecto)

    def test_exigir_conexion(self):
        entorno.exigir_conexion_prueba("postgresql://postgres@127.0.0.1:5432/postgres", None)
        entorno.exigir_conexion_prueba(DSN_PRUEBA, "proyectodeprueba")
        for dsn, proyecto in ((DSN_PRODUCCION, REF_PRODUCCION), (DSN_DIRECTA, REF_PRODUCCION), (DSN_PRUEBA, "otro"),
                              ("postgresql://u:x@servidor.com/db", "x")):
            with self.assertRaises(entorno.DestinoNoPermitido, msg=dsn):
                entorno.exigir_conexion_prueba(dsn, proyecto)

    def test_el_identificador_no_esta_en_los_archivos_nuevos(self):
        for ruta in ("app/entorno.py", "agente/agente_videos.py", "agente/config.ejemplo.json", "agente/README.md",
                     "agente/PRUEBAS_REALES.md", "scripts/videos_prueba_datos.py", "scripts/videos_prueba_migrar.py",
                     "scripts/video_agente.py", "tests/test_entorno.py", ".env.example"):
            self.assertNotIn(REF_PRODUCCION, (RAIZ / ruta).read_text(), ruta)


class Servidor(unittest.TestCase):
    ENT = "pruebas-abcd1234"

    def admite(self, **env):
        base = {"VIDEOS_ENTORNO": self.ENT, "SUPABASE_URL": URL_PRUEBA, "VIDEOS_SUPABASE_URL": ""}
        with mock.patch.dict(os.environ, {**base, **env}):
            os.environ.pop("VERCEL", None) if "VERCEL" not in env else None
            return entorno.servidor_admite(self.ENT)

    def test_entorno_correcto(self):
        self.assertTrue(self.admite())

    def test_sin_configurar(self):
        self.assertFalse(self.admite(VIDEOS_ENTORNO=""))

    def test_prueba_contra_la_base_de_produccion(self):
        self.assertFalse(self.admite(SUPABASE_URL=URL_PRODUCCION))
        self.assertFalse(self.admite(VIDEOS_SUPABASE_URL=URL_PRODUCCION))

    def test_prueba_desplegada_en_vercel(self):
        self.assertFalse(self.admite(VERCEL="1"))

    def test_produccion_declarada_es_una_decision_aparte(self):
        with mock.patch.dict(os.environ, {"VIDEOS_ENTORNO": "produccion-abcd1234", "SUPABASE_URL": URL_PRODUCCION}):
            self.assertTrue(entorno.servidor_admite("produccion-abcd1234"))
            self.assertFalse(entorno.servidor_admite(self.ENT))


class Scripts(unittest.TestCase):
    """Corren de verdad, con un archivo de entorno de producción: tienen que cortarse antes de conectarse."""

    def correr(self, script, *args, url=URL_PRODUCCION, dsn=DSN_PRODUCCION):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env.videos-prueba"
            env.write_text(f"SUPABASE_URL={url}\nSUPABASE_KEY=x\nSUPABASE_SERVICE_KEY=x\nVIDEOS_PRUEBA_DB_URL={dsn}\n")
            limpio = {k: v for k, v in os.environ.items() if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")}
            return subprocess.run([sys.executable, f"scripts/{script}", *args, "--env", str(env)], cwd=RAIZ, input="",
                                  capture_output=True, text=True, timeout=60, env=limpio)

    def test_datos_rechaza_produccion_aunque_se_confirme(self):
        r = self.correr("videos_prueba_datos.py", "margen", "--restaurar", "--proyecto", REF_PRODUCCION)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("producción", r.stderr)

    def test_datos_exige_confirmar_el_proyecto(self):
        r = self.correr("videos_prueba_datos.py", "margen", "--restaurar", url=URL_PRUEBA)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--proyecto", r.stderr)

    def test_migrar_rechaza_produccion_aunque_se_confirme(self):
        r = self.correr("videos_prueba_migrar.py", "--aplicar", "--proyecto", REF_PRODUCCION)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("producción", r.stderr)

    def test_migrar_rechaza_url_y_conexion_de_proyectos_distintos(self):
        r = self.correr("videos_prueba_migrar.py", "--aplicar", "--proyecto", "proyectodeprueba", url=URL_PRODUCCION, dsn=DSN_PRUEBA)
        self.assertNotEqual(r.returncode, 0)

    def test_migrar_pide_escribir_de_nuevo_el_proyecto(self):
        r = self.correr("videos_prueba_migrar.py", "--aplicar", "--proyecto", "proyectodeprueba", url=URL_PRUEBA, dsn=DSN_PRUEBA)
        self.assertNotEqual(r.returncode, 0)

    def test_migrar_sin_aplicar_no_lee_credenciales(self):
        r = subprocess.run([sys.executable, "scripts/videos_prueba_migrar.py"], cwd=RAIZ, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No se conectó a nada", r.stdout)

    def test_token_rechaza_produccion(self):
        env = {**os.environ, "SUPABASE_URL": URL_PRODUCCION, "SUPABASE_KEY": "x", "SUPABASE_SERVICE_KEY": "x"}
        for extra in ([], ["--remoto"]):
            r = subprocess.run([sys.executable, "scripts/video_agente.py", "bucket", *extra], cwd=RAIZ, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("creado", r.stdout)

    def test_no_usa_el_env_comun(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / ".env"
            ruta.write_text("SUPABASE_URL=http://127.0.0.1:1\n")
            with self.assertRaises(SystemExit):
                datos.cargar_env(str(ruta))

    def test_cargar_env_descarta_lo_que_ya_estaba(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"VIDEOS_SUPABASE_URL": URL_PRODUCCION}):
            ruta = Path(d) / ".env.videos-prueba"
            ruta.write_text(f"SUPABASE_URL={URL_PRUEBA}\n")
            datos.cargar_env(str(ruta))
            self.assertNotIn("VIDEOS_SUPABASE_URL", os.environ)
            self.assertEqual(os.environ["SUPABASE_URL"], URL_PRUEBA)


class Migraciones(unittest.TestCase):
    def carpeta(self, **archivos):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for nombre, sql in archivos.items():
            (Path(d.name) / f"{nombre}.sql").write_text(sql)
        return Path(d.name)

    def test_las_del_repo_estan_en_orden(self):
        archivos, problemas = migrar.revisar()
        self.assertEqual(problemas, [])
        self.assertEqual([a.name for a in archivos], sorted(a.name for a in archivos))
        self.assertEqual([a.name for a in archivos[-2:]],
                         ["20261106000000_videos_on_demand.sql", "20261108000000_videos_miniaturas.sql"])
        self.assertEqual(migrar.requisitos(archivos), ["auth", "storage", "vault"])

    def test_detecta_dependencia_rota(self):
        c = self.carpeta(**{"20260101000000_a": "create table public.a (x bigint references public.b(id));",
                            "20260102000000_b": "create table public.b (id bigint);"})
        self.assertTrue(any("referencia a b" in p for p in migrar.revisar(c)[1]))

    def test_detecta_nombre_invalido_y_prohibida(self):
        c = self.carpeta(**{"mal": "", "20261021000000_resumen_mensual_pagos": ""})
        problemas = " ".join(migrar.revisar(c)[1])
        self.assertIn("nombre inválido", problemas)
        self.assertIn("no debe aplicarse", problemas)

    def test_detecta_version_repetida(self):
        c = self.carpeta(**{"20260101000000_a": "", "20260101000000_b": ""})
        self.assertTrue(any("repetida" in p for p in migrar.revisar(c)[1]))


class FalsoDemo:
    def __init__(self, videos=(), sin_tablas=False, falla_archivos=False):
        self.videos, self.sin_tablas, self.falla = list(videos), sin_tablas, falla_archivos
        self.borradas, self.archivos = [], []

    def leer(self, tabla, columnas, **eq):
        if tabla == "empresa":
            return [{"id": 9, "nombre": "Pilates Centro", "mp_habilitado": False}]
        if tabla == "video_parametros" and self.sin_tablas:
            raise RuntimeError("{'code': 'PGRST205', 'message': \"Could not find the table 'public.video_parametros'\"}")
        if tabla == "video":
            return self.videos
        return []

    def borrar_archivos_video(self, video):
        if self.falla:
            raise RuntimeError("Storage caído")
        self.archivos.append(video["id"])

    def borrar(self, tabla, **eq):
        self.borradas.append(tabla)

    def borrar_cuenta(self, uid):
        pass

    def actualizar(self, *a, **k):
        pass


class BorrarEmpresa(unittest.TestCase):
    VIDEO = {"id": 1, "id_empresa": 9, "partes": [{"nombre": "parte-00.ts", "bytes": 1}], "archivo_eliminado_en": None}

    def borrar(self, db):
        with mock.patch.object(empresa_demo, "CUENTAS", []):
            empresa_demo.borrar(db, guardar_contrasenas=False)

    def test_borra_primero_los_archivos(self):
        db = FalsoDemo([self.VIDEO, {**self.VIDEO, "id": 2, "archivo_eliminado_en": "2026-11-01"}])
        self.borrar(db)
        self.assertEqual(db.archivos, [1])
        self.assertLess(db.borradas.index("video"), db.borradas.index("clase"))

    def test_si_falla_storage_no_borra_ninguna_fila(self):
        db = FalsoDemo([self.VIDEO], falla_archivos=True)
        with self.assertRaises(RuntimeError):
            self.borrar(db)
        self.assertEqual(db.borradas, [])

    def test_video_de_otra_empresa_corta(self):
        db = FalsoDemo([{**self.VIDEO, "id_empresa": 1}])
        with self.assertRaises(SystemExit):
            self.borrar(db)
        self.assertEqual((db.borradas, db.archivos), ([], []))

    def test_funciona_antes_de_la_migracion(self):
        db = FalsoDemo(sin_tablas=True)
        self.borrar(db)
        self.assertNotIn("video", db.borradas)
        self.assertIn("clase", db.borradas)

    def test_otro_error_no_se_toma_como_falta_de_migracion(self):
        with self.assertRaises(RuntimeError):
            empresa_demo._hay_videos(mock.Mock(leer=mock.Mock(side_effect=RuntimeError("sin conexión"))))


class Respaldos(unittest.TestCase):
    DATOS = {"profiles": [], "empresa": []}

    def test_restaurar_sin_tablas_de_videos(self):
        sql = restaurar_datos.generar_sql(self.DATOS, {"last_value": 1, "is_called": False})
        self.assertNotIn("public.video", sql)

    def test_restaurar_con_tablas_de_videos(self):
        fila = {"id": 1, "id_empresa": 1, "id_clase": 2}
        sql = restaurar_datos.generar_sql({**self.DATOS, "video": [fila]}, {"last_value": 1, "is_called": False})
        self.assertIn("insert into public.video ", sql)
        self.assertIn("'public.video'", sql)

    def test_los_tokens_no_se_restauran(self):
        self.assertNotIn("video_agente", restaurar_datos.VIDEOS)

    def verificar(self, tablas_video):
        def tabla(t):
            consulta = mock.MagicMock()
            consulta.select.return_value = consulta
            consulta.limit.return_value = consulta
            consulta.range.return_value = consulta
            if t.startswith("video") and tablas_video is None:
                consulta.execute.side_effect = RuntimeError("PGRST205 no existe")
            else:
                filas = {"empresa": [{"id": 1, "codigo": "JSU", "activa": True, "mp_habilitado": True}], "clase": [{"id": 5, "id_empresa": 1}]}
                filas.update(tablas_video or {})
                consulta.execute.return_value = mock.Mock(data=filas.get(t, []))
            return consulta

        cliente = mock.Mock(table=tabla)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "datos.json").write_text(json.dumps({t: [] for t in (
                "profiles", "tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo", "resumen_mensual",
                "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion", "mp_intento", "mp_evento", "mp_configuracion")}))
            salida = []
            with mock.patch("supabase.create_client", return_value=cliente), mock.patch.object(sys, "argv", ["x", d]), \
                    mock.patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": "x"}), \
                    mock.patch("builtins.print", side_effect=lambda *a, **k: salida.append(" ".join(map(str, a)))):
                try:
                    runpy.run_path(str(RAIZ / "scripts" / "verificar_despliegue.py"))
                except SystemExit:
                    pass
        return "\n".join(salida)

    def test_verificar_antes_de_la_migracion(self):
        self.assertIn("todavía no creadas", self.verificar(None))

    def test_verificar_con_videos_de_la_misma_empresa(self):
        salida = self.verificar({"video": [{"id": 1, "id_empresa": 1, "id_clase": 5}], "video_programacion": [], "video_reproduccion": []})
        self.assertIn("OK    video: 1 filas", salida)
        self.assertIn("OK    video y su clase", salida)

    def test_verificar_detecta_video_de_otra_empresa(self):
        salida = self.verificar({"video": [{"id": 1, "id_empresa": 2, "id_clase": 5}], "video_programacion": [], "video_reproduccion": []})
        self.assertIn("FALLA video: 1 filas", salida)
        self.assertIn("FALLA video y su clase", salida)


if __name__ == "__main__":
    unittest.main()
