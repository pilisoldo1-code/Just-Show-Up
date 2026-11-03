"""Protecciones y reglas del escenario fijo de Pilates Centro (scripts/empresa_demo.py), sin base de datos.
El escenario completo se prueba en tests/base_local/test_empresa_demo_base.py y tests/supabase_local/prueba_empresa2_reales.py.

Uso: python -m unittest tests.test_empresa_demo -v
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from scripts import empresa_demo as ED


class Proteccion(unittest.TestCase):
    def base(self, url, **kw):
        cli = mock.MagicMock(supabase_url=url)
        with mock.patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": "x"}), mock.patch("app.auth.cliente_admin", return_value=cli):
            return ED.BaseSupabase(**kw)

    def test_solo_local_salvo_produccion_explicita(self):
        self.assertTrue(self.base("http://127.0.0.1:54321").local)
        self.assertTrue(self.base("http://localhost:54321/").local)
        with self.assertRaises(SystemExit) as e:
            self.base("https://nspgafucytcjqkqnmkry.supabase.co")
        self.assertIn("--produccion", str(e.exception))
        self.assertFalse(self.base("https://nspgafucytcjqkqnmkry.supabase.co", produccion=True).local)

    def test_superadmin_del_script_solo_en_local(self):
        db = mock.MagicMock(local=False)
        with self.assertRaises(SystemExit):
            ED.crear_superadmin_local(db)

    def test_contrasenas_en_archivo_ignorado_y_sin_mostrarse(self):
        with tempfile.TemporaryDirectory() as d:
            local, prod = Path(d) / ".env.cuentas-local", Path(d) / ".env.cuentas-prueba"
            prod.write_text("sofia@demo.invalid  XYZ\n")
            with mock.patch.dict(ED.ARCHIVOS, {"local": local, "produccion": prod}), redirect_stdout(io.StringIO()) as salida:
                ED._guardar_contrasenas({ED.ADMIN[0]: "Demo-secreta"}, local=True)
            self.assertNotIn("Demo-secreta", salida.getvalue())
            self.assertIn("Demo-secreta", local.read_text())
            self.assertEqual(prod.read_text(), "sofia@demo.invalid  XYZ\n")  # en local nunca toca el archivo de producción
            self.assertEqual(oct(local.stat().st_mode)[-3:], "600")
            with mock.patch.dict(ED.ARCHIVOS, {"local": local, "produccion": prod}):
                ED._guardar_contrasenas({}, local=True)  # borrar: quita las cuentas de Pilates
            self.assertNotIn(ED.ADMIN[0], local.read_text())


class Escenario(unittest.TestCase):
    def test_reglas_para_hoy_y_para_la_defensa(self):
        for hoy in (date(2026, 11, 3), date(2026, 11, 17)):
            p = ED.fechas(hoy)
            r = p["reglas"]
            self.assertEqual(p["inicio_mes"], date(2026, 11, 1))
            self.assertEqual(r["ines_ausente_100"][0].month, 10)   # antes de la vigencia: 100 %
            self.assertEqual(r["ines_ausente_25"][0].month, 11)    # desde la vigencia: 25 %
            self.assertEqual(r["sabrina_sin_registrar"][0].month, 11)
            self.assertEqual(r["julia_cancelada"][0].month, 10)
            self.assertTrue(all(c["fecha"] != hoy for c in p["clases"]))  # el día de hoy lo genera la app
            jueves = [c for c in p["clases"] if c["semana"] == 1 and ED.HORARIOS[c["horario"]][0] == "jueves"][0]
            self.assertEqual(len(jueves["reservas"]), ED.TIPOS[1][4])  # completa: cupo del reformer
        self.assertEqual(ED.fechas(date(2026, 11, 17))["reglas"]["ines_ausente_25"], (date(2026, 11, 16), 0))

    def test_la_compartida_solo_desde_este_mes(self):
        p = ED.fechas(date(2026, 11, 17))
        con = [c["fecha"] for c in p["clases"] if ED.ALUMNA_COMPARTIDA in c["reservas"]]
        self.assertTrue(con and all(f >= date(2026, 11, 1) for f in con))


if __name__ == "__main__":
    unittest.main()
