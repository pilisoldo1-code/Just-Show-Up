"""Pruebas REALES de la baja de docentes contra un Supabase LOCAL (Colima + `supabase start`, gratis, en esta
computadora), con la app completa por HTTP: vista previa y confirmación de la administradora y de la
superadministradora, lo que ven las alumnas, los caminos que quedaron cerrados (también intentando escribir directo
en la API con el token real) y el aislamiento con otra empresa. Correr sobre una base recién reiniciada.

Uso (con Supabase local corriendo en la copia aislada ~/.local/share/jsu-supabase-local):
    python tests/supabase_local/prueba_baja_reales.py
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import timedelta

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
from app import auth, clases, main  # noqa: E402

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


def entrar(lugar, mail):
    c = TestClient(main.app, base_url="http://testserver")
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": CLAVE}, follow_redirects=False)
    return c if r.status_code == 303 else None


def huella_empresa(empresa):
    tablas = ["horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo", "membresia", "resumen_mensual"]
    return {t: json.dumps(sql(f"select * from public.{t} where id_empresa = %s order by id", (empresa,)), default=str)
            for t in tablas}


CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
sup_id, _ = auth.crear_cuenta("superadmin@prueba.invalid", "Administración", "General", contrasena=CLAVE, sin_empresa=True)
auth.cliente_admin().table("profiles").update({"es_superadmin": True}).eq("id", sup_id).execute()
auth.crear_cuenta("profesora@prueba.invalid", "Profe", "Prueba", rol="profesora", contrasena=CLAVE, id_empresa=JSU,
                  rol_empresa="admin")
SA = TestClient(main.app, base_url="http://testserver")
SA.post("/admin/login", data={"mail": "superadmin@prueba.invalid", "contrasena": CLAVE}, follow_redirects=False)
SA.post("/admin/empresas", data={"nombre": "Pilates Centro", "codigo": "PIL", "admin_nombre": "Lucía",
                                 "admin_apellido": "Bentancor", "admin_mail": "admin.pil@prueba.invalid"})
PIL = sql("select id from empresa where codigo = 'PIL'", uno=True)["id"]
cuentas = {}
for mail, nombre, rol, empresa in (("admin.pil@prueba.invalid", "Lucía", "admin", PIL), ("admin2.pil@prueba.invalid", "Rosa", "admin", PIL),
                                    ("profe.pil@prueba.invalid", "Pía", "profesora", PIL),
                                    ("otra.pil@prueba.invalid", "Olga", "profesora", PIL),
                                    ("alumna.pil@prueba.invalid", "Ana", "alumna", PIL), ("eva.pil@prueba.invalid", "Eva", "alumna", PIL),
                                    ("profe.jsu@prueba.invalid", "Juana", "profesora", JSU)):
    if mail == "admin.pil@prueba.invalid":  # la creó la superadministradora: se le pone una clave conocida
        cuentas[mail] = sql("select id::text from profiles where mail = %s", (mail,), True)["id"]
        auth.cliente_admin().auth.admin.update_user_by_id(cuentas[mail], {"password": CLAVE})
    else:
        cuentas[mail], _ = auth.crear_cuenta(mail, nombre, "Prueba", contrasena=CLAVE, id_empresa=empresa, rol_empresa=rol)
PROF, OTRA, ADMIN, ADMIN2 = (cuentas[m] for m in ("profe.pil@prueba.invalid", "otra.pil@prueba.invalid",
                                                   "admin.pil@prueba.invalid", "admin2.pil@prueba.invalid"))
A = entrar("Pilates Centro", "admin.pil@prueba.invalid")
ok("la administradora de Pilates Centro entra", A is not None)

# un tipo de clase y horarios de mañana (hora de Montevideo) para la profesora, la otra profesora y la administradora 2
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Pilates mat', 50, 900, 2, %s) returning id", (PIL,), True)["id"]
manana = clases.ahora().date() + timedelta(days=1)
dia = clases.DIAS_ORDEN[manana.weekday()]
for prof, hora in ((PROF, "10:00"), (OTRA, "12:00"), (ADMIN2, "14:00")):
    A.post("/profesora/horarios/nuevo", data={"dia_semana": dia, "hora_inicio": hora, "id_tipo_clase": str(TIPO),
                                               "activo": "true", "id_profesora": prof}, follow_redirects=False)
H = {r["id_profesora"]: r["id"] for r in sql("select id, id_profesora::text from horario where id_empresa = %s", (PIL,))}
ok("la administradora crea los horarios por la app (uno por docente)", set(H) == {PROF, OTRA, ADMIN2})
ANA = entrar("Pilates Centro", "alumna.pil@prueba.invalid")
EVA = entrar("Pilates Centro", "eva.pil@prueba.invalid")
semana = 1 if manana.weekday() == 0 else 0
ANA.get(f"/?semana={semana}")  # genera las clases de esa semana
C = {r["id_profesora"]: r["id"] for r in sql("select id, id_profesora::text from clase where id_empresa = %s and fecha = %s",
                                              (PIL, manana))}
ok("las clases de mañana se generan al abrir la grilla", set(C) == {PROF, OTRA, ADMIN2}, C)
for c in (ANA, EVA):
    c.post(f"/clases/{C[PROF]}/inscribirse", follow_redirects=False)
ANA.post(f"/clases/{C[OTRA]}/inscribirse", follow_redirects=False)
ANA.post(f"/clases/{C[ADMIN2]}/inscribirse", follow_redirects=False)
ok("dos reservas en la clase de la profesora (completa: cupo 2)",
   sql("select count(*) n from reserva where id_clase = %s and estado = 'confirmada'", (C[PROF],), True)["n"] == 2)

# ---------- caminos viejos ----------
r = A.post(f"/profesora/profesoras/{PROF}/estado", data={"activa": "0"}, follow_redirects=False)
ok("«Dejar inactiva» con horarios y clases lleva a la vista previa y no cambia nada",
   r.headers["location"] == f"/profesora/profesoras/{PROF}/baja"
   and sql("select activa from membresia where id_usuario = %s and id_empresa = %s", (PROF, PIL), True)["activa"])
ok("la ficha ofrece «Dar de baja…»", f"/profesora/profesoras/{PROF}/baja" in A.get(f"/profesora/profesoras/{PROF}").text)

# ---------- vista previa ----------
JSU_antes = huella_empresa(JSU)
v = A.get(f"/profesora/profesoras/{PROF}/baja")
firma = re.search(r'name="firma" value="([0-9a-f]+)"', v.text).group(1)
ok("vista previa: horario, clase, 2 reservas y los nombres de las alumnas",
   v.status_code == 200 and "Pilates mat" in v.text and "Ana Prueba, Eva Prueba" in v.text
   and "<dt>Reservas que se cancelan</dt><dd>2</dd>" in v.text)
P = entrar("Pilates Centro", "otra.pil@prueba.invalid")
ok("una profesora común no abre la baja de otra", P.get(f"/profesora/profesoras/{PROF}/baja", follow_redirects=False).status_code == 303)
ok("otra empresa: la administradora de Just Show Up no la ve",
   entrar("Just Show Up", "profesora@prueba.invalid").get(f"/profesora/profesoras/{PROF}/baja", follow_redirects=False)
   .headers["location"].startswith("/profesora/profesoras?ok=0"))

# firma: Eva cancela su reserva después de la vista previa
EVA.post(f"/clases/{C[PROF]}/cancelar", follow_redirects=False)
r = A.post(f"/profesora/profesoras/{PROF}/baja", data={"motivo": "PRUEBA dejó el estudio", "firma": firma})
ok("si algo cambió, no se cancela nada y se muestra la vista previa al día",
   r.status_code == 409 and "Algo cambió" in r.text and "<dt>Reservas que se cancelan</dt><dd>1</dd>" in r.text
   and sql("select estado from clase where id = %s", (C[PROF],), True)["estado"] == "programada")
firma = re.search(r'name="firma" value="([0-9a-f]+)"', r.text).group(1)
r = A.post(f"/profesora/profesoras/{PROF}/baja", data={"motivo": "PRUEBA dejó el estudio", "firma": firma},
           follow_redirects=False)
ok("confirmar la baja", "ok=1" in r.headers["location"] and "1+clase+cancelada" in r.headers["location"], r.headers["location"])
ok("queda inactiva, sin horarios activos y con su clase cancelada",
   not sql("select activa from membresia where id_usuario = %s and id_empresa = %s", (PROF, PIL), True)["activa"]
   and not sql("select activo from horario where id = %s", (H[PROF],), True)["activo"]
   and sql("select estado from clase where id = %s", (C[PROF],), True)["estado"] == "cancelada")
ok("ya no puede iniciar sesión en Pilates Centro", entrar("Pilates Centro", "profe.pil@prueba.invalid") is None)
ok("la otra profesora y su clase siguen igual",
   sql("select estado from clase where id = %s", (C[OTRA],), True)["estado"] == "programada")
ok("Just Show Up queda idéntica", huella_empresa(JSU) == JSU_antes)
ins = ANA.get("/mis-inscripciones").text
ok("la alumna ve la clase como cancelada por la profesora en Mis inscripciones", "Cancelada por la profesora" in ins)
pl = A.get("/profesora/planificacion").text
ok("Planificación muestra la baja sin «Eliminar»",
   "Baja de una profesora" in pl and pl.count("Eliminar</button>") == 0)

# ---------- caminos cerrados, también escribiendo directo en la API con el token real ----------
r = A.post(f"/profesora/horarios/{H[PROF]}/toggle", follow_redirects=False)
ok("activar su horario desde Horarios: se explica y no se activa",
   "profesora+inactiva" in r.headers["location"] and not sql("select activo from horario where id = %s", (H[PROF],), True)["activo"])
r = A.post("/profesora/horarios/nuevo", data={"dia_semana": dia, "hora_inicio": "20:00", "id_tipo_clase": str(TIPO),
                                              "activo": "true", "id_profesora": PROF})
ok("crear un horario para ella: no aparece para elegir y se rechaza", r.status_code == 400 and "activa de tu empresa" in r.text)
token = A.cookies.get("sb_acceso")
api = auth.cliente_con_token(token)
for nombre, accion in (("activar su horario", lambda: api.table("horario").update({"activo": True}).eq("id", H[PROF]).execute()),
                       ("reabrir su clase", lambda: api.table("clase").update({"estado": "programada"}).eq("id", C[PROF]).execute()),
                       ("borrar la baja", lambda: api.rpc("quitar_bloqueo", {"p_id": sql(
                           "select id from bloqueo where tipo = 'baja'", uno=True)["id"]}).execute())):
    try:
        res = accion().data
        rechazado = isinstance(res, dict) and res.get("resultado") == "es_baja"
    except Exception as e:
        rechazado = "profesora_inactiva" in str(e) or "42501" in str(e)
    ok(f"API directa con el token de la administradora: {nombre} → rechazado", rechazado)
ok("…y nada cambió", not sql("select activo from horario where id = %s", (H[PROF],), True)["activo"]
   and sql("select estado from clase where id = %s", (C[PROF],), True)["estado"] == "cancelada")

# ---------- reactivar ----------
r = A.post(f"/profesora/profesoras/{PROF}/estado", data={"activa": "1"}, follow_redirects=False)
ok("reactivarla avisa que sus horarios siguen inactivos", "ok=1" in r.headers["location"] and "siguen+inactivos" in r.headers["location"])
ok("…y su clase sigue cancelada", sql("select estado from clase where id = %s", (C[PROF],), True)["estado"] == "cancelada")

# ---------- administradora: solo la superadministradora ----------
r = A.get(f"/admin/empresas/{PIL}/administradoras/{ADMIN2}/baja", follow_redirects=False)
ok("la administradora no entra a la baja de administradoras (vuelve a su panel)",
   r.status_code == 303 and r.headers["location"] == "/administracion", r.headers.get("location"))
v = SA.get(f"/admin/empresas/{PIL}/administradoras/{ADMIN2}/baja")
ok("superadmin: vista previa con cantidades, sin nombres de alumnas",
   v.status_code == 200 and "Deja de ser administradora" in v.text and "Ana Prueba" not in v.text)
firma = re.search(r'name="firma" value="([0-9a-f]+)"', v.text).group(1)
r = SA.post(f"/admin/empresas/{PIL}/administradoras/{ADMIN2}/baja", data={"motivo": "PRUEBA cambio", "firma": firma},
            follow_redirects=False)
ok("superadmin: confirma la baja de una administradora", "ok=1" in r.headers["location"], r.headers["location"])
ok("pasa a profesora inactiva y su clase queda cancelada",
   sql("select es_admin, es_profesora, activa from membresia where id_usuario = %s and id_empresa = %s", (ADMIN2, PIL), True)
   == {"es_admin": False, "es_profesora": True, "activa": False}
   and sql("select estado from clase where id = %s", (C[ADMIN2],), True)["estado"] == "cancelada")
r = SA.get(f"/admin/empresas/{PIL}/administradoras/{ADMIN}/baja", follow_redirects=False)
ok("la última administradora activa no se puede dar de baja", "ninguna+administradora" in r.headers["location"])
ok("Just Show Up sigue idéntica", huella_empresa(JSU) == JSU_antes)

print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
