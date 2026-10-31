"""Vista previa de Vercel aislada: ni se despliega (vercel.json) ni, si se desplegara, atiende pedidos."""
import json
import os
import unittest
from importlib import reload
from unittest import mock

from fastapi.testclient import TestClient


class VistaPrevia(unittest.TestCase):
    def test_vercel_json_solo_despliega_main(self):
        git = json.load(open(os.path.join(os.path.dirname(__file__), "..", "vercel.json")))["git"]["deploymentEnabled"]
        self.assertEqual(git, {"**": False, "main": True})

    def test_en_vista_previa_no_atiende_ni_toca_la_base(self):
        from app import main
        with mock.patch.dict(os.environ, {"VERCEL_ENV": "preview"}):
            m = reload(main)
            with mock.patch.object(m.auth, "cliente_nuevo") as cn, mock.patch.object(m.auth, "cliente_admin") as ca:
                c = TestClient(m.app)
                for url in ("/", "/login", "/admin/login", "/profesora/horarios", "/mis-facturas"):
                    r = c.get(url, follow_redirects=False)
                    self.assertEqual(r.status_code, 503, url)
                self.assertEqual(c.post("/login", data={"lugar": "x", "mail": "a@b.uy", "contrasena": "x"}).status_code, 503)
                self.assertEqual(c.get("/api/health").status_code, 200)
            cn.assert_not_called()
            ca.assert_not_called()
        reload(main)

    def test_en_produccion_y_local_funciona_normal(self):
        from app import main
        for entorno in ({"VERCEL_ENV": "production"}, {}):
            with mock.patch.dict(os.environ, entorno, clear=False):
                os.environ.pop("VERCEL_ENV", None) if not entorno else None
                m = reload(main)
                self.assertFalse(m.ES_VISTA_PREVIA)
                self.assertEqual(TestClient(m.app).get("/login").status_code, 200)
        reload(main)


if __name__ == "__main__":
    unittest.main()
