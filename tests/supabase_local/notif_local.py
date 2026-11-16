"""Piezas comunes de las pruebas de notificaciones contra el Supabase LOCAL (fases B, C y D). No es una prueba.

Se niega a seguir si la API no es localhost. Mailpit (el correo de prueba de `supabase start`) recibe por SMTP en
127.0.0.1:54325 (hay que tener `smtp_port = 54325` en [local_smtp] de la copia aislada) y se consulta por su API en
127.0.0.1:54324. Nunca se manda un correo real.
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL = os.path.expanduser("~/.local/share/jsu-supabase-local")
estado = json.loads(subprocess.run(["supabase", "status", "-o", "json"], cwd=LOCAL, capture_output=True, text=True,
                                   check=True).stdout)
URL = estado["API_URL"]
if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", URL):
    raise SystemExit(f"Esta prueba solo corre contra un Supabase local (URL recibida: {URL}).")
os.environ["SUPABASE_URL"] = URL
os.environ["SUPABASE_KEY"] = estado.get("PUBLISHABLE_KEY") or estado["ANON_KEY"]
os.environ["SUPABASE_SERVICE_KEY"] = estado.get("SECRET_KEY") or estado["SERVICE_ROLE_KEY"]
for k in ("VERCEL_ENV", "VERCEL", "NOTIFICACIONES_SECRETO", "SMTP_HOST", "SMTP_PORT", "SMTP_USUARIO", "SMTP_CONTRASENA",
          "SMTP_REMITENTE", "SMTP_SEGURIDAD"):
    os.environ.pop(k, None)
sys.path.insert(0, RAIZ)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, correos, main  # noqa: E402

DB_URL = estado["DB_URL"]
DB = psycopg2.connect(DB_URL)
DB.autocommit = True
MAILPIT = "http://127.0.0.1:54324"
SMTP_LOCAL = correos.Config("127.0.0.1", 54325, "", "", "avisos@justshowup.test", "ninguna", "s" * 40)
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
_cuenta = {"fallas": 0, "total": 0}


def ok(nombre, condicion, extra=""):
    _cuenta["total"] += 1
    _cuenta["fallas"] += not condicion
    print(("OK   " if condicion else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not condicion else ""), flush=True)


def terminar():
    t, f = _cuenta["total"], _cuenta["fallas"]
    print(f"\n{t - f}/{t} comprobaciones OK" + ("" if not f else f" — {f} FALLA(S)"))
    sys.exit(1 if f else 0)


def sql(consulta, args=None, uno=False, conexion=None):
    with (conexion or DB).cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(consulta, args)
        filas = [dict(f) for f in cur.fetchall()] if cur.description else []
    return (filas[0] if filas else None) if uno else filas


def como_servicio():
    """Conexión aparte con el rol del servidor (service_role), como la usa el despachador."""
    c = psycopg2.connect(DB_URL)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("set role service_role")
    return c


def falla_permiso(funcion):
    try:
        funcion()
        return False
    except Exception:
        return True


def entrar(lugar, mail, clave=None):
    c = TestClient(main.app, base_url="http://testserver")
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave or CLAVE}, follow_redirects=False)
    return c if r.status_code == 303 and "login" not in r.headers.get("location", "") else None


def entrar_superadmin(mail, clave):
    c = TestClient(main.app, base_url="http://localhost:8000")
    r = c.post("/admin/login", data={"mail": mail, "contrasena": clave}, follow_redirects=False)
    return c if r.status_code == 303 and "login" not in r.headers.get("location", "") else None


def api(cliente):
    return auth.cliente_con_token(cliente.cookies.get("sb_acceso"))


def cuenta(mail, nombre, rol, empresa):
    return auth.crear_cuenta(mail, nombre, "Prueba", rol="profesora" if rol != "alumna" else "alumna", contrasena=CLAVE,
                             id_empresa=empresa, rol_empresa=rol)[0]


# ---------- Mailpit ----------

def mailpit_vaciar():
    httpx.delete(f"{MAILPIT}/api/v1/messages", timeout=10)


def mailpit_mensajes(esperar=0.0, cuantos=None):
    """Lista de mensajes (más nuevos primero). Si se pide `cuantos`, espera hasta `esperar` segundos a que lleguen."""
    fin = time.monotonic() + esperar
    while True:
        datos = httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 500}, timeout=10).json()
        mensajes = datos.get("messages") or []
        if cuantos is None or len(mensajes) >= cuantos or time.monotonic() >= fin:
            return mensajes
        time.sleep(1)


def mailpit_mensaje(id_):
    return httpx.get(f"{MAILPIT}/api/v1/message/{id_}", timeout=10).json()


def mailpit_cabeceras(id_):
    return httpx.get(f"{MAILPIT}/api/v1/message/{id_}/headers", timeout=10).json()


def destinatarios(mensajes):
    return sorted(m["To"][0]["Address"] for m in mensajes)
