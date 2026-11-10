"""Pruebas REALES de las correcciones de asistencia contra un Supabase LOCAL (Colima + `supabase start`, gratis, en
esta computadora), con la app completa por HTTP: pantalla de Asistencias con pendientes (filtro por mes, «Todas» por
defecto), corrección de la administradora con motivo e historial, bloqueo por factura confirmada, lo que puede y no puede
la profesora, y los intentos directos contra la API con tokens reales (profesora, alumna, otra empresa). Correr sobre
una base recién reiniciada.

Uso (con Supabase local corriendo en la copia aislada ~/.local/share/jsu-supabase-local):
    python tests/supabase_local/prueba_asistencias_reales.py
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
from tests.supabase_local._correo_local import SITIO as SITIO_LOCAL, activar_por_correo  # noqa: E402

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
# la administradora la crea la superadministradora desde el panel: recibe una invitación, así que su correo no
# puede ser .invalid (lo recibe Mailpit) y se activa con el enlace, como en producción
ADMIN_PIL = f"admin.pil.{uuid.uuid4().hex[:6]}@ejemplo.test"
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
sup_id, _ = auth.crear_cuenta("superadmin@prueba.invalid", "Administración", "General", contrasena=CLAVE, sin_empresa=True)
auth.cliente_admin().table("profiles").update({"es_superadmin": True}).eq("id", sup_id).execute()
auth.crear_cuenta("profesora@prueba.invalid", "Profe", "Prueba", rol="profesora", contrasena=CLAVE, id_empresa=JSU,
                  rol_empresa="admin")
SA = TestClient(main.app, base_url=SITIO_LOCAL)  # sitio permitido: el panel manda la invitación
SA.post("/admin/login", data={"mail": "superadmin@prueba.invalid", "contrasena": CLAVE}, follow_redirects=False)
SA.post("/admin/empresas", data={"nombre": "Pilates Centro", "codigo": "PIL", "admin_nombre": "Lucía",
                                 "admin_apellido": "Bentancor", "admin_mail": ADMIN_PIL})
PIL = sql("select id from empresa where codigo = 'PIL'", uno=True)["id"]
ADMIN = sql("select id::text from profiles where mail = %s", (ADMIN_PIL,), uno=True)["id"]
activar_por_correo(estado, main.app, ADMIN_PIL, CLAVE)
cuentas = {m: auth.crear_cuenta(m, n, "Prueba", contrasena=CLAVE, id_empresa=PIL, rol_empresa=r)[0]
           for m, n, r in (("profe.pil@prueba.invalid", "Pía", "profesora"), ("ana.pil@prueba.invalid", "Ana", "alumna"),
                           ("eva.pil@prueba.invalid", "Eva", "alumna"), ("ines.pil@prueba.invalid", "Inés", "alumna"))}
PROF, ANA, EVA, INES = (cuentas[m] for m in ("profe.pil@prueba.invalid", "ana.pil@prueba.invalid", "eva.pil@prueba.invalid",
                                             "ines.pil@prueba.invalid"))
A, P, AL = entrar("Pilates Centro", ADMIN_PIL), entrar("Pilates Centro", "profe.pil@prueba.invalid"), \
    entrar("Pilates Centro", "ana.pil@prueba.invalid")
J1 = entrar("Just Show Up", "profesora@prueba.invalid")
ok("entran la administradora, la profesora y una alumna de Pilates Centro, y la administradora de Just Show Up",
   all(x is not None for x in (A, P, AL, J1)))

# una clase de la profesora que terminó ayer (con 3 reservas sin asistencia) y otra de hace dos meses con la factura de Ana
# confirmada; se cargan directo en la base LOCAL (las pantallas solo generan clases futuras)
hoy = clases.ahora().date()
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Pilates mat', 50, 900, 5, %s) returning id", (PIL,), True)["id"]


def clase_pasada(fecha, hora):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "('domingo', %s, %s, false, %s, %s) returning id", (hora, TIPO, PROF, PIL), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa, "
               "id_profesora) values (%s, %s, %s, 'programada', 50, 900, 5, %s, %s, %s) returning id",
               (h, fecha, hora, TIPO, PIL, PROF), True)["id"]


def reservar(alumna, id_clase):
    return sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
               (alumna, id_clase, PIL), True)["id"]


AYER = clase_pasada(hoy - timedelta(days=1), "10:00")
R = {n: reservar(a, AYER) for n, a in (("ana", ANA), ("eva", EVA), ("ines", INES))}
hace2 = ((hoy.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=10)
VIEJA = clase_pasada(hace2, "10:00")
RV_ANA, RV_EVA = reservar(ANA, VIEJA), reservar(EVA, VIEJA)
FAC = sql("insert into resumen_mensual (id_alumna, mes, anio, id_empresa) values (%s, %s, %s, %s) returning id",
          (ANA, hace2.month, hace2.year, PIL), True)["id"]
sql("set session_replication_role = replica; update resumen_mensual set estado_factura = 'confirmada', numero = 'PRUEBA-1', "
    "confirmada_en = now(), vence_el = current_date, alumna_nombre = 'Ana', alumna_apellido = 'Prueba' where id = %s; "
    "set session_replication_role = origin", (FAC,))
mes_ayer = f"{(hoy - timedelta(days=1)).year}-{(hoy - timedelta(days=1)).month:02d}"
mes_viejo = f"{hace2.year}-{hace2.month:02d}"

# ---------- la pantalla de Asistencias ----------
h = A.get(f"/profesora/clases?mes={mes_ayer}").text
ok("Asistencias abre con «Todas» y muestra la clase terminada con «3 de 3 sin registrar»",
   'filtro=todas#terminadas" class="dia-chip dia-chip-activo"' in h and ">3 de 3 sin registrar<" in h and ">Completar</a>" in h)
ok("el aviso de pendientes y el contador del filtro", "Pendientes (1)" in h and "3 asistencias sin registrar en" in h)
hp = P.get(f"/profesora/clases?mes={mes_ayer}").text
ok("la profesora ve su clase pendiente, solo para consultar", ">3 de 3 sin registrar<" in hp and ">Completar</a>" not in hp
   and "solo la administración puede completar" in hp)
ini = A.get("/administracion").text
ok("Inicio: aviso discreto con enlace a las pendientes", "asistencias sin registrar" in ini and "filtro=pendientes" in ini)

# ---------- la profesora no puede, ni por pantalla ni por la API ----------
d = P.get(f"/profesora/clases/{AYER}").text
ok("detalle para la profesora: sin formulario de corrección", "/corregir" not in d and 'name="motivo"' not in d)
r = P.post(f"/profesora/clases/{AYER}/corregir", data={f"estado_{R['ana']}": "presente", "motivo": "x"}, follow_redirects=False)
ok("la profesora no puede enviar correcciones (la ruta es de la administradora)", r.status_code == 303
   and sql("select count(*) n from asistencia where id_reserva = %s", (R["ana"],), True)["n"] == 0)
for nombre, cliente in (("profesora", P), ("alumna", AL), ("administradora de otra empresa", J1)):
    api = auth.cliente_con_token(cliente.cookies.get("sb_acceso"))
    res = api.rpc("corregir_asistencias", {"p_cambios": [{"id_reserva": R["ana"], "estado": "presente"}], "p_motivo": "x"}).execute().data
    ok(f"API directa con el token real ({nombre}): rechazada", res["resultado"] in ("no_autorizada", "no_existe"), res)
api_p = auth.cliente_con_token(P.cookies.get("sb_acceso"))
try:
    api_p.table("asistencia").insert({"id_reserva": R["ana"], "estado": "presente"}).execute()
    escribio = True
except Exception:
    escribio = False
ok("la profesora tampoco escribe directo en asistencia", not escribio)
ok("…y nada cambió", sql("select count(*) n from asistencia where id_reserva in %s", (tuple(R.values()),), True)["n"] == 0)

# ---------- la administradora completa ----------
d = A.get(f"/profesora/clases/{AYER}").text
ok("detalle para la administradora: formulario con motivo", f'action="/profesora/clases/{AYER}/corregir"' in d and 'name="motivo"' in d)
r = A.post(f"/profesora/clases/{AYER}/corregir", data={f"estado_{R['ana']}": "presente", f"estado_{R['eva']}": "ausente"},
           follow_redirects=False)
ok("sin motivo no se guarda", "ok=0" in r.headers["location"]
   and sql("select count(*) n from asistencia where id_reserva in %s", (tuple(R.values()),), True)["n"] == 0)
r = A.post(f"/profesora/clases/{AYER}/corregir", data={f"estado_{R['ana']}": "presente", f"estado_{R['eva']}": "ausente",
                                                     "motivo": "Planilla en papel"}, follow_redirects=False)
ok("con motivo se guardan las dos", "ok=1" in r.headers["location"] and "Se+guardaron+2" in r.headers["location"], r.headers["location"])
hist = sql("select estado_anterior, estado_nuevo, origen, motivo, por::text from asistencia_historial where id_reserva in %s order by id",
           (tuple(R.values()),))
ok("historial: dos correcciones con motivo y quién", len(hist) == 2 and all(x["origen"] == "correccion" and x["motivo"] == "Planilla en papel"
                                                                         and x["por"] == ADMIN for x in hist))
d = A.get(f"/profesora/clases/{AYER}").text
ok("el detalle muestra el historial y la que sigue sin registrar", "Historial" in d and "Motivo: Planilla en papel" in d
   and "1 sin registrar" in d)
ok("la profesora no lee el historial por la API", api_p.table("asistencia_historial").select("id").execute().data == [])
ok("la alumna ve solo el resultado en Mi historial", "Presente" in AL.get("/mi-historial").text
   and "Planilla" not in AL.get("/mi-historial").text)
h = A.get(f"/profesora/clases?mes={mes_ayer}&filtro=pendientes").text
ok("el filtro «Pendientes» la sigue mostrando (falta una)", ">1 de 3 sin registrar<" in h)

# ---------- factura confirmada ----------
d = A.get(f"/profesora/clases/{VIEJA}").text
ok("detalle de un mes con la factura de Ana confirmada: su fila bloqueada", "La factura de este mes ya está confirmada" in d)
r = A.post(f"/profesora/clases/{VIEJA}/corregir", data={f"estado_{RV_ANA}": "presente", f"estado_{RV_EVA}": "presente",
                                                      "motivo": "x"}, follow_redirects=False)
ok("forzar la corrección de Ana: no se guarda nada (todo o nada)", "ok=0" in r.headers["location"]
   and sql("select count(*) n from asistencia where id_reserva in %s", ((RV_ANA, RV_EVA),), True)["n"] == 0)
r = A.post(f"/profesora/clases/{VIEJA}/corregir", data={f"estado_{RV_EVA}": "presente", "motivo": "x"}, follow_redirects=False)
ok("Eva (sin factura confirmada) sí", "ok=1" in r.headers["location"])
ok("la otra empresa no ve nada de esto", "Pilates" not in J1.get(f"/profesora/clases?mes={mes_ayer}").text
   and J1.get(f"/profesora/clases/{AYER}", follow_redirects=False).status_code == 303)

print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
