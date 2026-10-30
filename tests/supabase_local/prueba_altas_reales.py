"""Pruebas REALES de multiempresa contra un Supabase LOCAL (Colima + `supabase start`, gratis, en esta
computadora): servicio de cuentas (GoTrue), API (PostgREST) y base de Supabase de verdad, con todas las
migraciones del repo. Corre la app completa por HTTP (TestClient): inicio de sesión por lugar, altas de
administradoras, profesoras y alumnas, roles combinados, cuenta compartida en dos dispositivos, cédula por
empresa e intentos de elevar permisos con tokens reales.

NUNCA se conecta a producción: se niega a correr si la URL no es localhost.

Uso (con Supabase local corriendo en la copia aislada ~/.local/share/jsu-supabase-local):
    python tests/supabase_local/prueba_altas_reales.py
"""
import json
import os
import re
import subprocess
import sys
import uuid

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
os.environ.pop("VERCEL_ENV", None)
sys.path.insert(0, RAIZ)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, main  # noqa: E402

DB = psycopg2.connect(estado["DB_URL"])
DB.autocommit = True
fallas = total = 0


def ok(nombre, condicion, extra=""):
    global fallas, total
    total += 1
    fallas += not condicion
    print(("OK   " if condicion else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not condicion else ""))


def sql(consulta, args=None, uno=False):
    with DB.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(consulta, args)
        filas = [dict(f) for f in cur.fetchall()] if cur.description else []
    return (filas[0] if filas else None) if uno else filas


def membresias(mail):
    return {f["codigo"]: (f["es_admin"], f["es_profesora"], f["es_alumna"]) for f in sql(
        "select e.codigo, m.es_admin, m.es_profesora, m.es_alumna from membresia m join empresa e on e.id = m.id_empresa "
        "join profiles p on p.id = m.id_usuario where p.mail = %s", (mail,))}


def navegador():
    return TestClient(main.app, base_url="http://testserver")


def entrar(lugar, mail, clave):
    c = navegador()
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c, r


def clave_temporal(html):
    m = re.search(r'credencial-clave">([^<]+)<', html)
    return m.group(1).strip() if m else None


CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]

# ---------- cuentas iniciales (como los scripts, con el servicio de cuentas real) ----------
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
sup_id, _ = auth.crear_cuenta("superadmin@prueba.invalid", "Administración", "General", contrasena=CLAVE, sin_empresa=True)
auth.cliente_admin().table("profiles").update({"es_superadmin": True}).eq("id", sup_id).execute()
auth.crear_cuenta("profesora@prueba.invalid", "Profe", "Prueba", rol="profesora", contrasena=CLAVE,
                  id_empresa=JSU, rol_empresa="admin_profesora")
ok("altas iniciales: superadministradora sin lugar y administradora de Just Show Up",
   membresias("superadmin@prueba.invalid") == {} and membresias("profesora@prueba.invalid") == {"JSU": (True, True, False)})

# ---------- superadministradora: acceso propio y alta de empresa ----------
SA = navegador()
r = SA.post("/admin/login", data={"mail": "superadmin@prueba.invalid", "contrasena": CLAVE}, follow_redirects=False)
ok("superadmin: entra por /admin/login y va a Empresas", r.status_code == 303 and r.headers["location"] == "/admin/empresas")
c, r = entrar("Just Show Up", "superadmin@prueba.invalid", CLAVE)
ok("superadmin: no entra por el login de lugares (mensaje genérico)", r.status_code == 401 and auth.MSG_INGRESO in r.text)
r = SA.post("/admin/empresas", data={"nombre": "Pilates Centro", "codigo": "pil", "admin_nombre": "Lucía",
                                     "admin_apellido": "Bentancor", "admin_mail": "admin.pil@prueba.invalid",
                                     "tambien_profesora": "1"})
CLAVE_ADMIN2 = clave_temporal(r.text)
ok("superadmin: crea Pilates Centro con su administradora (y profesora)",
   r.status_code == 200 and CLAVE_ADMIN2 and membresias("admin.pil@prueba.invalid") == {"PIL": (True, True, False)})
r = SA.post("/admin/empresas", data={"nombre": "PILATES  centro!", "codigo": "PIX", "admin_nombre": "X", "admin_apellido": "Y",
                                     "admin_mail": "x.y@prueba.invalid"}, follow_redirects=False)
ok("superadmin: un nombre que se escribe igual se rechaza", "ok=0" in r.headers.get("location", "")
   and not sql("select 1 from empresa where codigo = 'PIX'"))
r = SA.get("/admin/empresas")
ok("superadmin: ve cantidades y administradora, sin datos operativos",
   "Pilates Centro" in r.text and "admin.pil@prueba.invalid" in r.text and "Facturación" not in r.text)
for url in ("/", "/profesora/horarios", "/profesora/resumenes", "/administracion"):
    ok(f"superadmin: no entra a {url}", SA.get(url, follow_redirects=False).status_code == 303)

# ---------- administradora de Pilates Centro: altas por rol ----------
A2, r = entrar("pilates centro", "admin.pil@prueba.invalid", CLAVE_ADMIN2)
ok("administradora: entra escribiendo el lugar en minúsculas y va al panel", r.headers.get("location") == "/administracion")
claves, esperado = {}, {"alumna": (False, False, True), "profesora": (False, True, False),
                        "admin": (True, False, False), "admin_profesora": (True, True, False)}
for rol, flags in esperado.items():
    mail = f"nueva.{rol.replace('_', '.')}.pil@prueba.invalid"
    r = A2.post("/profesora/alumnas", data={"nombre": "Nueva", "apellido": "Persona", "mail": mail, "rol": rol})
    claves[rol] = clave_temporal(r.text)
    ok(f"alta «{rol}»: cuenta real creada con exactamente esos permisos, solo en Pilates Centro",
       claves[rol] and membresias(mail) == {"PIL": flags}, membresias(mail))
inicio = {"alumna": "/", "profesora": "/profesora/horarios", "admin": "/administracion", "admin_profesora": "/administracion"}
for rol in esperado:
    mail = f"nueva.{rol.replace('_', '.')}.pil@prueba.invalid"
    c, r = entrar("Pilates Centro", mail, claves[rol])
    ok(f"«{rol}»: entra con su contraseña temporal y va a {inicio[rol]}", r.headers.get("location") == inicio[rol], r.status_code)
    prohibidas = {"alumna": ["/profesora/horarios", "/administracion", "/profesora/resumenes"],
                  "profesora": ["/administracion", "/profesora/resumenes", "/profesora/tipos-clase", "/profesora/alumnas/nueva"],
                  "admin": [], "admin_profesora": []}[rol]
    for url in prohibidas + ["/admin/empresas"]:
        ok(f"«{rol}»: no entra a {url}", c.get(url, follow_redirects=False).status_code == 303)
    c2, r2 = entrar("Just Show Up", mail, claves[rol])
    ok(f"«{rol}»: no entra a Just Show Up (mismo mensaje genérico)", r2.status_code == 401 and auth.MSG_INGRESO in r2.text)

# ---------- roles combinados ----------
PROF_ID = sql("select id::text from profiles where mail = 'nueva.profesora.pil@prueba.invalid'", uno=True)["id"]
r = A2.post("/profesora/alumnas", data={"nombre": "Nueva", "apellido": "Persona", "mail": "nueva.profesora.pil@prueba.invalid",
                                        "rol": "alumna"}, follow_redirects=False)
ok("la profesora también pasa a ser alumna (misma cuenta)", membresias("nueva.profesora.pil@prueba.invalid") == {"PIL": (False, True, True)},
   membresias("nueva.profesora.pil@prueba.invalid"))
c, r = entrar("Pilates Centro", "nueva.profesora.pil@prueba.invalid", claves["profesora"])
ok("profesora y alumna: entra al panel de profesora y tiene «Mis clases»", r.headers.get("location") == "/profesora/horarios"
   and 'class="menu-otro-rol">Mis clases' in c.get("/profesora/horarios").text)
ok("profesora y alumna: puede ver sus clases de alumna sin cerrar sesión", c.get("/mis-inscripciones", follow_redirects=False).status_code == 200)
ok("profesora y alumna: sigue sin facturación", c.get("/profesora/resumenes", follow_redirects=False).status_code == 303)

# ---------- cuenta compartida y dos dispositivos ----------
A1, r = entrar("Just Show Up", "profesora@prueba.invalid", CLAVE)
r = A1.post("/profesora/alumnas", data={"nombre": "Ana", "apellido": "Prueba", "mail": "alumna@prueba.invalid", "rol": "alumna",
                                        "cedula": "1.234.567-2"})
CLAVE_ALU = clave_temporal(r.text)
r = A2.post("/profesora/alumnas", data={"nombre": "Ana", "apellido": "Prueba", "mail": "alumna@prueba.invalid", "rol": "alumna",
                                        "cedula": "1.234.567-2"}, follow_redirects=False)
ok("la misma cédula en otra empresa no avisa nada (no revela que existe allá)",
   r.status_code == 303 and "ok=1" in r.headers["location"] and "c%C3%A9dula" not in r.headers["location"].lower())
ok("alumna compartida: una sola cuenta, alumna en las dos empresas, cédula guardada en cada una",
   membresias("alumna@prueba.invalid") == {"JSU": (False, False, True), "PIL": (False, False, True)}
   and sql("select count(*) n from auth.users where email = 'alumna@prueba.invalid'", uno=True)["n"] == 1
   and [f["cedula"] for f in sql("select m.cedula from membresia m join profiles p on p.id = m.id_usuario "
                                  "where p.mail = 'alumna@prueba.invalid' order by m.id_empresa")] == ["12345672", "12345672"]
   and sql("select cedula from profiles where mail = 'alumna@prueba.invalid'", uno=True)["cedula"] is None)
COMPU, r1 = entrar("Just Show Up", "alumna@prueba.invalid", CLAVE_ALU)
CEL, r2 = entrar("Pilates Centro", "alumna@prueba.invalid", CLAVE_ALU)
ok("dos dispositivos: la computadora entra a Just Show Up y el celular a Pilates Centro",
   r1.headers.get("location") == "/" and r2.headers.get("location") == "/")
h1, h2 = COMPU.get("/mis-inscripciones").text, CEL.get("/mis-inscripciones").text
ok("cada dispositivo muestra su lugar (el celular no cambió la computadora)",
   "Just Show Up" in h1 and "Pilates Centro" not in h1 and "Pilates Centro" in h2,
   ("Pilates Centro" in h1, "Pilates Centro" in h2))
CEL.post("/logout")
ok("cerrar sesión en el celular no afecta la computadora",
   COMPU.get("/mis-inscripciones", follow_redirects=False).status_code == 200 and "Just Show Up" in COMPU.get("/mis-inscripciones").text)
for nombre, lugar, clave in (("lugar inexistente", "Gimnasio Fantasma", CLAVE_ALU), ("contraseña incorrecta", "Just Show Up", "otra-clave"),
                             ("lugar con mayúsculas y signos", "JUST-show up", CLAVE_ALU)):
    c, r = entrar(lugar, "alumna@prueba.invalid", clave)
    esperado_ok = nombre == "lugar con mayúsculas y signos"
    ok(f"login con {nombre}: {'entra' if esperado_ok else 'mensaje genérico'}",
       (r.status_code == 303) if esperado_ok else (r.status_code == 401 and auth.MSG_INGRESO in r.text))
SA.post(f"/admin/empresas/{sql('select id from empresa where codigo = %s', ('PIL',), uno=True)['id']}/estado", data={"activa": 0})
c, r = entrar("Pilates Centro", "alumna@prueba.invalid", CLAVE_ALU)
ok("empresa desactivada: mensaje genérico", r.status_code == 401 and auth.MSG_INGRESO in r.text)
ok("empresa desactivada: la sesión abierta en ese lugar deja de tener acceso",
   A2.get("/administracion", follow_redirects=False).headers.get("location", "").startswith("/login"))
SA.post(f"/admin/empresas/{sql('select id from empresa where codigo = %s', ('PIL',), uno=True)['id']}/estado", data={"activa": 1})

# ---------- intentos de elevar permisos con tokens reales ----------
from supabase import create_client  # noqa: E402
for rol in ("alumna", "profesora"):
    mail = f"nueva.{rol}.pil@prueba.invalid"
    cli = create_client(URL, os.environ["SUPABASE_KEY"])
    sesion = cli.auth.sign_in_with_password({"email": mail, "password": claves[rol]}).session
    yo = sql("select id::text from profiles where mail = %s", (mail,), uno=True)["id"]
    cli.rpc("entrar_a_empresa", {"p_lugar": "Pilates Centro"}).execute()
    intentos = {
        "hacerse superadministradora": lambda: cli.table("profiles").update({"es_superadmin": True}).eq("id", yo).execute(),
        "cambiar profiles.rol": lambda: cli.table("profiles").update({"rol": "profesora"}).eq("id", yo).execute(),
        "darse administradora en su membresía": lambda: cli.table("membresia").update({"es_admin": True}).eq("id_usuario", yo).execute(),
        "crearse una membresía en Just Show Up": lambda: cli.table("membresia").insert(
            {"id_usuario": yo, "id_empresa": JSU, "es_admin": True}).execute(),
        "asignarse administradora (función)": lambda: cli.rpc("asignar_membresia", {"p_id_usuario": yo, "p_es_admin": True,
                                                                                     "p_es_profesora": True, "p_es_alumna": True}).execute(),
        "asignar_administradora (solo superadmin)": lambda: cli.rpc("asignar_administradora", {"p_id_empresa": JSU, "p_id_usuario": yo,
                                                                                                "p_es_profesora": True}).execute(),
        "leer cédulas directo": lambda: cli.table("membresia").select("cedula").execute(),
        "leer la tabla de sesiones": lambda: cli.table("sesion_empresa").select("*").execute(),
    }
    for nombre, f in intentos.items():
        try:
            resultado = f().data
        except Exception:
            resultado = "rechazado"
        ok(f"{rol}: no puede {nombre}", resultado in ("rechazado", [], "no_autorizada"), resultado)
    # Supabase Auth deja que cada persona edite sus propios metadatos: eso no da permisos
    cli.auth.update_user({"data": {"rol_empresa": "admin", "id_empresa": JSU, "sin_empresa": True}})
    ok(f"{rol}: editar sus metadatos (rol_empresa=admin, otra empresa) no cambia nada",
       membresias(mail) == ({"PIL": (False, False, True)} if rol == "alumna" else {"PIL": (False, True, True)})
       and not sql("select es_superadmin from profiles where id = %s", (yo,), uno=True)["es_superadmin"])
    c, r = entrar("Just Show Up", mail, claves[rol])
    ok(f"{rol}: después de editar sus metadatos sigue sin poder entrar a Just Show Up", r.status_code == 401)
try:
    create_client(URL, os.environ["SUPABASE_KEY"]).auth.sign_up({"email": "intrusa@prueba.invalid", "password": "ClaveLarga123"})
    registro = "creada"
except Exception:
    registro = "rechazado"
ok("el registro público está apagado (nadie crea su propia cuenta)",
   registro == "rechazado" and not sql("select 1 from auth.users where email = 'intrusa@prueba.invalid'"))
ok("al final, la única superadministradora es la del alta inicial",
   [f["mail"] for f in sql("select mail from profiles where es_superadmin")] == ["superadmin@prueba.invalid"])

print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
