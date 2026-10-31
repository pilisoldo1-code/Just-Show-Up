"""Recorre TODAS las pantallas de la app (profesora/administradora y alumna) contra un Supabase LOCAL real y
falla si alguna da error. Sirve para las dos versiones del código:

  viejo: el de theta (main), con las migraciones 1 a 3c aplicadas → comprueba que esas migraciones son
         compatibles con lo que está hoy en producción (login con mail y contraseña).
  nuevo: el de la rama multiempresa, con todas las migraciones (login con el nombre del lugar).

Incluye acciones: generar la semana (al abrir la grilla), reservar y cancelar. NUNCA usa producción: se niega
a correr si la URL no es localhost.

Uso:  python tests/supabase_local/recorrido_pantallas.py <carpeta del código> <viejo|nuevo>
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import timedelta

CODIGO, MODO = os.path.abspath(sys.argv[1]), sys.argv[2]
LOCAL = os.path.expanduser("~/.local/share/jsu-supabase-local")
estado = json.loads(subprocess.run(["supabase", "status", "-o", "json"], cwd=LOCAL, capture_output=True, text=True,
                                   check=True).stdout)
URL = estado["API_URL"]
if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", URL):
    raise SystemExit(f"Solo corre contra un Supabase local (URL: {URL}).")
os.environ.update(SUPABASE_URL=URL, SUPABASE_KEY=estado.get("PUBLISHABLE_KEY") or estado["ANON_KEY"],
                  SUPABASE_SERVICE_KEY=estado.get("SECRET_KEY") or estado["SERVICE_ROLE_KEY"])
os.environ.pop("VERCEL_ENV", None)
sys.path.insert(0, CODIGO)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from supabase import create_client  # noqa: E402
from app import clases, main  # noqa: E402

DB = psycopg2.connect(estado["DB_URL"])
DB.autocommit = True
fallas = total = 0


def ok(nombre, condicion, extra=""):
    global fallas, total
    total += 1
    fallas += not condicion
    print(("OK   " if condicion else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not condicion else ""))


def sql(consulta, args=None):
    with DB.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(consulta, args)
        return [dict(f) for f in cur.fetchall()] if cur.description else []


admin = create_client(URL, os.environ["SUPABASE_SERVICE_KEY"])
CLAVE = "Recorrido-" + uuid.uuid4().hex[:8]
sufijo = uuid.uuid4().hex[:6]
cuentas = {}
for quien, rol_empresa in (("profe", "admin_profesora"), ("alumna", "alumna")):
    mail = f"{quien}.{sufijo}@prueba.invalid"
    meta = {"nombre": quien.title(), "apellido": "Recorrido"}
    if MODO == "nuevo":
        jsu = sql("select id from empresa where codigo = 'JSU'")[0]["id"]
        meta.update(id_empresa=jsu, rol_empresa=rol_empresa)
    uid = admin.auth.admin.create_user({"email": mail, "password": CLAVE, "email_confirm": True, "user_metadata": meta}).user.id
    if MODO == "viejo" and quien == "profe":  # como el script viejo crear_profesora
        admin.table("profiles").update({"rol": "profesora"}).eq("id", uid).execute()
    cuentas[quien] = (mail, uid)


def entrar(quien):
    c = TestClient(main.app)
    datos = {"mail": cuentas[quien][0], "contrasena": CLAVE}
    if MODO == "nuevo":
        datos["lugar"] = "Just Show Up"
    r = c.post("/login", data=datos, follow_redirects=False)
    ok(f"{quien}: inicia sesión ({MODO})", r.status_code == 303, (r.status_code, r.text[:200]))
    return c


def visitar(c, quien, url, esperado=(200,)):
    r = c.get(url, follow_redirects=False)
    ok(f"{quien}: {url} responde {r.status_code}", r.status_code in esperado and "Internal Server Error" not in r.text,
       r.text[:300] if r.status_code >= 500 else "")
    return r


hoy = clases.ahora().date()
P = entrar("profe")
for url in ["/profesora/horarios", "/profesora/horarios/nuevo", "/profesora/tipos-clase", "/profesora/tipos-clase/nuevo",
            "/profesora/clases", "/profesora/planificacion", f"/profesora/planificacion?modo=puntual&fecha={hoy + timedelta(days=2)}",
            "/profesora/alumnas", "/profesora/alumnas/nueva", "/profesora/estadisticas", "/profesora/resumenes",
            f"/profesora/resumenes?mes={hoy.month}&anio={hoy.year}", "/perfil", "/api/horario"] + (["/administracion"] if MODO == "nuevo" else []):
    visitar(P, "profesora", url)
t = sql("select id from tipo_clase order by id limit 1")[0]["id"]
# horarios creados desde la app (prueba la escritura con la sesión de la profesora): todos los días a las 22:00.
# Se cuentan los que crea ESTA ejecución (la base puede tener los de una ejecución anterior).
previos = len(sql("select id from horario where hora_inicio = '22:00'"))
for dia in ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"):
    r = P.post("/profesora/horarios/nuevo", data={"dia_semana": dia, "hora_inicio": "22:00", "id_tipo_clase": str(t),
                                                   "activo": "true"}, follow_redirects=False)
ok("profesora: crea horarios desde la app", r.status_code == 303 and len(sql("select id from horario where hora_inicio = '22:00'")) == previos + 7,
   (r.status_code, r.text[:200]))
h = sql("select id from horario order by id limit 1")[0]["id"]
visitar(P, "profesora", f"/profesora/horarios/{h}/editar")
visitar(P, "profesora", f"/profesora/tipos-clase/{t}/editar")

A = entrar("alumna")
for url in ["/", "/?semana=1", "/mis-inscripciones", "/mi-historial", "/mis-facturas", "/perfil"]:
    visitar(A, "alumna", url)
futura = sql("select c.id, c.fecha from clase c join horario h on h.id = c.id_horario "
             "where c.estado = 'programada' and h.activo and c.fecha > current_date order by c.fecha, c.hora_inicio limit 1")
ok("la grilla generó clases de la semana (generar_clases)", bool(futura))
if futura:
    cid, fecha = futura[0]["id"], futura[0]["fecha"]
    visitar(A, "alumna", f"/dia/{fecha}")
    r = A.post(f"/clases/{cid}/inscribirse", data={"fecha": str(fecha)}, follow_redirects=False)
    ok("alumna: reserva una clase", r.status_code == 303 and
       sql("select count(*) n from reserva where id_clase = %s and id_alumna = %s and estado = 'confirmada'", (cid, cuentas["alumna"][1]))[0]["n"] == 1)
    visitar(A, "alumna", "/mis-inscripciones")
    visitar(P, "profesora", f"/profesora/clases/{cid}")
    r = A.post(f"/clases/{cid}/cancelar", data={"fecha": str(fecha)}, follow_redirects=False)
    ok("alumna: cancela la reserva", r.status_code == 303 and
       sql("select estado from reserva where id_clase = %s and id_alumna = %s", (cid, cuentas["alumna"][1]))[0]["estado"] == "cancelada")
ok("la profesora no ve pantallas de alumna con errores", P.get("/", follow_redirects=False).status_code in (200, 303))

print(f"\n{total - fallas}/{total} casos OK ({MODO})")
sys.exit(1 if fallas else 0)
