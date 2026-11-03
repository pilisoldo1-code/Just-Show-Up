"""Ensayo LOCAL de la superadministradora y la Empresa 2 («Pilates Centro»), contra un Supabase LOCAL (Colima +
`supabase start`, gratis, en esta computadora), con la app completa por HTTP y los scripts reales
(`scripts/crear_superadmin.py` y `scripts/empresa_demo.py crear|borrar`). Solo datos ficticios.

Comprueba: panel de la superadministradora (empresas, administradoras, vigencia del 25 %); las dos empresas
independientes; cada rol ve solo lo de su empresa (pantallas y API con tokens reales); reservas, clases, asistencias,
facturas y pagos separados; correcciones de asistencia y cobro del 25 % en las dos; que crear y borrar la Empresa 2 no
cambia nada de Just Show Up y no deja datos huérfanos. Correr sobre una base recién reiniciada.

Uso (con Supabase local corriendo en la copia aislada ~/.local/share/jsu-supabase-local):
    python tests/supabase_local/prueba_empresa2_reales.py
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import date, timedelta
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL = os.path.expanduser("~/.local/share/jsu-supabase-local")
estado = json.loads(subprocess.run(["supabase", "status", "-o", "json"], cwd=LOCAL, capture_output=True, text=True,
                                   check=True).stdout)
URL = estado["API_URL"]
if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", URL):
    raise SystemExit(f"Esta prueba solo corre contra un Supabase local (URL recibida: {URL}).")
# antes de importar la app o los scripts: load_dotenv no pisa variables ya definidas, así nunca se lee el .env real
os.environ["SUPABASE_URL"] = URL
os.environ["SUPABASE_KEY"] = estado.get("PUBLISHABLE_KEY") or estado["ANON_KEY"]
os.environ["SUPABASE_SERVICE_KEY"] = estado.get("SECRET_KEY") or estado["SERVICE_ROLE_KEY"]
os.environ.pop("VERCEL_ENV", None)
sys.path.insert(0, RAIZ)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, clases, main  # noqa: E402
from scripts import crear_superadmin, empresa_demo  # noqa: E402

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


def entrar(lugar, mail, clave):
    c = TestClient(main.app, base_url="http://testserver")
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c if r.status_code == 303 and "login" not in r.headers.get("location", "") else None


def api(cliente):
    return auth.cliente_con_token(cliente.cookies.get("sb_acceso"))


def texto(html):
    return " ".join(html.split())


TABLAS_EMPRESA = [r["table_name"] for r in sql(
    "select table_name from information_schema.columns where table_schema = 'public' and column_name = 'id_empresa' "
    "and table_name in (select table_name from information_schema.tables where table_schema = 'public' "
    "and table_type = 'BASE TABLE') order by 1")]


def huella_empresa(e):
    """Todas las filas de la empresa en cada tabla que tiene id_empresa (sin sesion_empresa: cada inicio de sesión agrega
    una fila, y acá también se inicia sesión en Just Show Up)."""
    return {t: json.dumps(sql(f"select * from public.{t} where id_empresa = %s order by 1", (e,)), default=str)
            for t in TABLAS_EMPRESA if t != "sesion_empresa"}


ok("la prueba corre contra el Supabase local", str(auth.cliente_admin().supabase_url).startswith(("http://127.0.0.1", "http://localhost")))
ok("ninguna tabla nueva sin id_empresa quedó afuera de la comparación", {"asistencia_historial", "baja_docente",
   "sesion_empresa", "resumen_mensual", "pago", "membresia"} <= set(TABLAS_EMPRESA), TABLAS_EMPRESA)

# ---------- 1) Just Show Up con datos ficticios (cuentas, clases, asistencias, factura y pago) ----------
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
cuenta = lambda m, n, rol: auth.crear_cuenta(m, n, "Prueba", rol="profesora" if rol != "alumna" else "alumna", contrasena=CLAVE,
                                             id_empresa=JSU, rol_empresa=rol)[0]
ADM_J, PROF_J = cuenta("profesora@prueba.invalid", "Paula", "admin"), cuenta("profe.jsu@prueba.invalid", "Pía", "profesora")
ALU_J, COMPARTIDA = cuenta("alu.jsu@prueba.invalid", "Ana", "alumna"), cuenta("alumna@prueba.invalid", "Alma", "alumna")
TIPO_J = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
             "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]


def clase_j(fecha, hora="10:00"):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "('domingo', %s, %s, false, %s, %s) returning id", (hora, TIPO_J, PROF_J, JSU), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
               "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, 7, %s, %s, %s) returning id",
               (h, fecha, hora, TIPO_J, JSU, PROF_J), True)["id"]


def reservar(alumna, id_clase, empresa, asistencia=None):
    r = sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (alumna, id_clase, empresa), True)["id"]
    if asistencia:
        sql("insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, empresa))
    return r


hoy = clases.ahora().date()
OCT = date(2026, 10, 20)
C_J_OCT, C_J_NOV = clase_j(OCT), clase_j(hoy - timedelta(days=1))
R_J = {"oct_aus": reservar(ALU_J, C_J_OCT, JSU, "ausente"), "oct_comp": reservar(COMPARTIDA, C_J_OCT, JSU, "presente"),
       "nov_aus": reservar(ALU_J, C_J_NOV, JSU, "ausente")}
AJ = entrar("Just Show Up", "profesora@prueba.invalid", CLAVE)
AJ.get("/profesora/resumenes?mes=10&anio=2026")
FJ = sql("select * from resumen_mensual where id_alumna = %s and mes = 10", (ALU_J,), True)
ok("Just Show Up: octubre facturado con la regla anterior (la ausencia del 20/10 al 100 %: $800)", FJ and FJ["total"] == 800)
AJ.post(f"/profesora/resumenes/{FJ['id']}/confirmar", data={"version": FJ["version"]})
FJ = sql("select * from resumen_mensual where id = %s", (FJ["id"],), True)
AJ.post(f"/profesora/resumenes/{FJ['id']}/pagar", data={"monto": "800.00", "medio_pago": "efectivo", "fecha": hoy.isoformat()})
ok("Just Show Up: factura confirmada (JSU-2026-10-…) y pagada", FJ["numero"].startswith("JSU-2026-10-")
   and sql("select count(*) n from pago where id_resumen_mensual = %s and estado = 'vigente'", (FJ["id"],), True)["n"] == 1)
prov = texto(AJ.get("/profesora/resumenes").text)
ok("Just Show Up: noviembre provisorio con la inasistencia al 25 % (rige desde el 1/11)", "Inasistencia · 25 %" in prov)
FOTO_JSU = huella_empresa(JSU)

# ---------- 2) superadministradora con el script real ----------
CLAVE_SA = "Super-" + uuid.uuid4().hex[:10]
respuestas = iter(["superadmin@prueba.invalid", "Administración", "General"])
with mock.patch("builtins.input", lambda *_: next(respuestas)), mock.patch("getpass.getpass", lambda *_: CLAVE_SA), \
        mock.patch("builtins.print"):
    crear_superadmin.main()
SUP = sql("select id::text, es_superadmin from profiles where mail = 'superadmin@prueba.invalid'", uno=True)
ok("crear_superadmin.py: cuenta creada, sin ninguna empresa", SUP and SUP["es_superadmin"]
   and sql("select count(*) n from membresia where id_usuario = %s", (SUP["id"],), True)["n"] == 0)
SA = TestClient(main.app, base_url="http://testserver")
r = SA.post("/admin/login", data={"mail": "superadmin@prueba.invalid", "contrasena": CLAVE_SA}, follow_redirects=False)
ok("la superadministradora entra por /admin/login", r.status_code == 303 and "/admin/empresas" in r.headers["location"])
ok("…y no puede entrar como lugar (no pertenece a ninguna empresa)",
   entrar("Just Show Up", "superadmin@prueba.invalid", CLAVE_SA) is None)

# ---------- 3) Empresa 2 con el script real ----------
db = empresa_demo.BaseSupabase()
ok("empresa_demo.py usa el Supabase local", str(db.cli.supabase_url).startswith(("http://127.0.0.1", "http://localhost")))
with mock.patch("builtins.print"):
    creada = empresa_demo.crear(db, hoy, guardar_contrasenas=False)
PIL = creada["empresa"]
CL = creada["contrasenas"]
ok("empresa_demo.py crear: Pilates Centro con cuentas, clases, reservas y asistencias",
   creada["clases"] > 0 and creada["reservas"] > 0 and creada["asistencias"] > 0, creada)
ok("…crear no cambió nada de Just Show Up", huella_empresa(JSU) == FOTO_JSU)
uid = lambda m: sql("select id::text from profiles where mail = %s", (m,), True)["id"]
ADM_P, PROF_P, ALU_P = uid("admin.pilates@demo.invalid"), uid("profe.pilates@demo.invalid"), uid("rocio.pilates@demo.invalid")

# ---------- 4) panel de la superadministradora ----------
lista = texto(SA.get("/admin/empresas").text)
ok("panel: las dos empresas con sus cifras", "Just Show Up" in lista and "Pilates Centro" in lista)
ok("panel: sin datos operativos ni personales (no muestra alumnas, cédulas ni facturas)",
   "rocio.pilates" not in lista and "Factura" not in lista)
g = texto(SA.get(f"/admin/empresas/{PIL}").text)
ok("Gestionar Pilates Centro: administradora y sección de cobro por inasistencia (sin aplicar)",
   "Lucía Bentancor" in g and "Cobro por inasistencia" in g and "No se aplica" in g)
r = SA.post(f"/admin/empresas/{PIL}/editar", data={"nombre": "Pilates Centro Demo", "codigo": "PIL"}, follow_redirects=False)
ok("editar el nombre de la empresa", "ok=1" in r.headers["location"]
   and sql("select nombre from empresa where id = %s", (PIL,), True)["nombre"] == "Pilates Centro Demo")
SA.post(f"/admin/empresas/{PIL}/editar", data={"nombre": "Pilates Centro", "codigo": "PIL"})
r = SA.post(f"/admin/empresas/{PIL}/administradora", data={"nombre": "Marta", "apellido": "Suárez",
                                                             "mail": "marta.pilates@demo.invalid"})
ADM2_P = sql("select id::text from profiles where mail = 'marta.pilates@demo.invalid'", uno=True)
ok("asignar una segunda administradora (cuenta nueva)", r.status_code == 200 and ADM2_P and sql(
   "select es_admin from membresia where id_usuario = %s and id_empresa = %s", (ADM2_P["id"], PIL), True)["es_admin"])
ADM2_P = ADM2_P["id"]
r = SA.post(f"/admin/empresas/{PIL}/administradoras/{ADM2_P}/quitar", data={"accion": "profesora"}, follow_redirects=False)
ok("pasar la segunda administradora a profesora", "ok=1" in r.headers["location"]
   and sql("select es_admin, es_profesora from membresia where id_usuario = %s", (ADM2_P,), True) == {"es_admin": False, "es_profesora": True})
r = SA.post(f"/admin/empresas/{PIL}/administradoras/{ADM_P}/quitar", data={"accion": "profesora"}, follow_redirects=False)
ok("nunca deja la empresa sin administradora activa", "ok=0" in r.headers["location"]
   and sql("select es_admin from membresia where id_usuario = %s and id_empresa = %s", (ADM_P, PIL), True)["es_admin"])
SA.post(f"/admin/empresas/{PIL}/administradora", data={"nombre": "Marta", "apellido": "Suárez", "mail": "marta.pilates@demo.invalid"})
prev = SA.get(f"/admin/empresas/{PIL}/administradoras/{ADM2_P}/baja").text
firma = re.search(r'name="firma" value="([^"]*)"', prev)
r = SA.post(f"/admin/empresas/{PIL}/administradoras/{ADM2_P}/baja", data={"motivo": "Ensayo local", "firma": firma and firma.group(1)},
            follow_redirects=False)
ok("desactivar una administradora con vista previa (queda inactiva y registrada)", firma is not None and sql(
   "select activa from membresia where id_usuario = %s and id_empresa = %s", (ADM2_P, PIL), True)["activa"] is False
   and sql("select count(*) n from baja_docente where id_empresa = %s", (PIL,), True)["n"] == 1, r.headers.get("location"))
r = SA.post(f"/admin/empresas/{PIL}/cobro-inasistencia", data={"desde": hoy.replace(day=1).isoformat()}, follow_redirects=False)
ok("vigencia del 25 % para Pilates Centro desde el 1° del mes en curso", "ok=1" in r.headers["location"]
   and sql("select cobro_inasistencia_desde d from empresa where id = %s", (PIL,), True)["d"] == hoy.replace(day=1))
r = SA.post(f"/admin/empresas/{PIL}/estado", data={"activa": 0}, follow_redirects=False)
ok("desactivar la empresa: sus usuarias no pueden entrar", "ok=1" in r.headers["location"]
   and entrar("Pilates Centro", "admin.pilates@demo.invalid", CL["admin.pilates@demo.invalid"]) is None)
SA.post(f"/admin/empresas/{PIL}/estado", data={"activa": 1})
ok("reactivarla: vuelven a entrar", entrar("Pilates Centro", "admin.pilates@demo.invalid", CL["admin.pilates@demo.invalid"]) is not None)
ok("Just Show Up sigue igual después de todo el panel", huella_empresa(JSU) == FOTO_JSU)

# ---------- 5) independencia y aislamiento por rol ----------
AP = entrar("Pilates Centro", "admin.pilates@demo.invalid", CL["admin.pilates@demo.invalid"])
PP = entrar("Pilates Centro", "profe.pilates@demo.invalid", CL["profe.pilates@demo.invalid"])
LP = entrar("Pilates Centro", "rocio.pilates@demo.invalid", CL["rocio.pilates@demo.invalid"])
CJ = entrar("Just Show Up", "alumna@prueba.invalid", CLAVE)
CP = entrar("Pilates Centro", "alumna@prueba.invalid", CLAVE)
LJ = entrar("Just Show Up", "alu.jsu@prueba.invalid", CLAVE)
ok("entran las cuentas de las dos empresas (la alumna compartida en las dos, con la misma cuenta)",
   all(x is not None for x in (AP, PP, LP, CJ, CP, LJ)))
ok("una cuenta de Pilates Centro no entra a Just Show Up (mensaje genérico)",
   entrar("Just Show Up", "admin.pilates@demo.invalid", CL["admin.pilates@demo.invalid"]) is None)
al_p, al_j = texto(AP.get("/profesora/alumnas").text), texto(AJ.get("/profesora/alumnas").text)
ok("Alumnas: cada administradora ve solo las suyas (la compartida en las dos)",
   "Rocío" in al_p and "Ana Prueba" not in al_p and "Ana Prueba" in al_j and "Rocío" not in al_j
   and "Alma" in al_p and "Alma" in al_j)
OPER = ("clase", "reserva", "asistencia", "horario", "tipo_clase", "listadeespera", "resumen_mensual", "resumen_mensual_detalle",
        "pago", "membresia", "asistencia_historial", "bloqueo")
for nombre, cli, emp in (("administradora de Pilates", AP, PIL), ("profesora de Pilates", PP, PIL), ("alumna de Pilates", LP, PIL),
                         ("compartida en Pilates", CP, PIL), ("compartida en Just Show Up", CJ, JSU),
                         ("administradora de JSU", AJ, JSU), ("alumna de JSU", LJ, JSU)):
    a, ajenas = api(cli), {}
    for t in OPER:
        try:
            filas = a.table(t).select("*").execute().data
        except Exception:
            filas = []
        # la única fila de otra empresa permitida es la propia membresía (la alumna compartida sabe a qué lugares pertenece)
        otras = [f for f in filas if f["id_empresa"] != emp and not (t == "membresia" and f["id_usuario"] in (COMPARTIDA,))]
        if otras:
            ajenas[t] = len(otras)
    ok(f"API con token real ({nombre}): ninguna fila de la otra empresa en {len(OPER)} tablas", not ajenas, ajenas)
ok("la profesora de Pilates no ve facturas ni pagos", api(PP).table("resumen_mensual").select("id").execute().data == []
   and PP.get("/profesora/resumenes", follow_redirects=False).status_code == 303)
rp = api(PP).table("reserva").select("id_clase").execute().data
mias = {c["id"] for c in sql("select id from clase where id_profesora = %s", (PROF_P,))}
ok("la profesora de Pilates solo lee reservas de SUS clases", rp and all(x["id_clase"] in mias for x in rp))
c_jsu_por_pil = AP.get(f"/profesora/clases/{C_J_OCT}", follow_redirects=False)
ok("la administradora de Pilates no abre una clase de Just Show Up", c_jsu_por_pil.status_code == 303)
res = api(CP).rpc("crear_intento_mp", {"p_id_resumen": FJ["id"]}).execute().data
ok("Mercado Pago: en Pilates Centro no se puede iniciar un pago (ni sobre una factura de JSU)",
   isinstance(res, dict) and res.get("resultado") != "ok", res)

# ---------- 6) asistencias, 25 %, facturas y pagos en Pilates Centro ----------
pasada = sql("select c.id from clase c where c.id_empresa = %s and c.fecha < %s and c.fecha >= %s and exists (select 1 from reserva r "
           "where r.id_clase = c.id and r.estado = 'confirmada' and not exists (select 1 from asistencia a where a.id_reserva = r.id "
           "and a.estado = 'ausente')) order by c.fecha desc limit 1", (PIL, hoy, hoy.replace(day=1)), True)
ok("hay una clase de Pilates ya dictada en este mes", pasada is not None)
r_pas = sql("select r.id, r.id_alumna::text from reserva r left join asistencia a on a.id_reserva = r.id "
            "where r.id_clase = %s and r.estado = 'confirmada' and coalesce(a.estado, 'presente') = 'presente' order by r.id limit 1",
            (pasada["id"],), True)
r = AP.post(f"/profesora/clases/{pasada['id']}/corregir", data={f"estado_{r_pas['id']}": "ausente", "motivo": "Ensayo: faltó"},
            follow_redirects=False)
ok("Pilates: la administradora corrige una asistencia (queda en el historial de SU empresa)", "ok=1" in r.headers["location"]
   and sql("select id_empresa from asistencia_historial where id_reserva = %s", (r_pas["id"],), True)["id_empresa"] == PIL)
r = AJ.post(f"/profesora/clases/{pasada['id']}/corregir", data={f"estado_{r_pas['id']}": "presente", "motivo": "x"},
            follow_redirects=False)
ok("la administradora de JSU no puede corregir una clase de Pilates",
   sql("select estado from asistencia where id_reserva = %s", (r_pas["id"],), True)["estado"] == "ausente")
ok("Pilates: el historial lo ve su administradora y no la de JSU",
   any(h["id_reserva"] == r_pas["id"] for h in api(AP).table("asistencia_historial").select("id_reserva").execute().data)
   and not any(h["id_reserva"] == r_pas["id"] for h in api(AJ).table("asistencia_historial").select("id_reserva").execute().data))
pv = texto(AP.get("/profesora/resumenes").text)
ok("Pilates: el provisorio del mes muestra la inasistencia al 25 % (vigencia de su empresa)", "Inasistencia · 25 %" in pv)
# para facturar un mes cerrado con el 25 % se adelanta la vigencia SOLO en esta base local (el panel no deja ir hacia atrás)
sql("update empresa set cobro_inasistencia_desde = '2026-10-01' where id = %s", (PIL,))
AP.get("/profesora/resumenes?mes=10&anio=2026")
borr = sql("select * from resumen_mensual where id_empresa = %s and mes = 10 order by total desc", (PIL,))
lineas25 = sql("select count(*) n from resumen_mensual_detalle d join resumen_mensual f on f.id = d.id_resumen_mensual "
               "where f.id_empresa = %s and d.porcentaje = 25", (PIL,), True)["n"]
aus_oct = sql("select count(*) n from asistencia a join reserva r on r.id = a.id_reserva join clase c on c.id = r.id_clase "
              "where c.id_empresa = %s and a.estado = 'ausente' and c.fecha between '2026-10-01' and '2026-10-31'", (PIL,), True)["n"]
ok("Pilates: borradores de octubre; cada ausencia de octubre al 25 %", borr and lineas25 == aus_oct, (len(borr), lineas25, aus_oct))
ok("…y Just Show Up no tiene ninguna línea al 25 % (su vigencia sigue en 1/11)",
   sql("select count(*) n from resumen_mensual_detalle where id_empresa = %s and porcentaje = 25", (JSU,), True)["n"] == 0)
fp = borr[0]
r = AP.post(f"/profesora/resumenes/{fp['id']}/confirmar", data={"version": fp["version"]}, follow_redirects=False)
fp = sql("select * from resumen_mensual where id = %s", (fp["id"],), True)
ok("Pilates: factura confirmada con su propia numeración (PIL-2026-10-…)", fp["numero"] and fp["numero"].startswith("PIL-2026-10-"),
   fp["numero"])
AP.post(f"/profesora/resumenes/{fp['id']}/pagar", data={"monto": str(fp["total"]), "medio_pago": "transferencia",
                                                        "fecha": hoy.isoformat()})
ok("Pilates: pago registrado en su empresa", sql("select id_empresa from pago where id_resumen_mensual = %s", (fp["id"],), True)
   == {"id_empresa": PIL})
ok("la administradora de JSU no abre la factura de Pilates", AJ.get(f"/profesora/resumenes/{fp['id']}", follow_redirects=False)
   .status_code == 303 or "PIL-" not in AJ.get(f"/profesora/resumenes/{fp['id']}").text)
cp_mf, cj_mf = texto(CP.get("/mis-facturas").text), texto(CJ.get("/mis-facturas").text)
ok("alumna compartida: Mis facturas en cada lugar muestra solo las de ese lugar", "PIL-" not in cj_mf and "JSU-" not in cp_mf)
ok("ni sus listas del mes muestran nada de Pilates", "PIL-" not in AJ.get("/profesora/resumenes?mes=10&anio=2026").text)
ok("Just Show Up sigue idéntico después de operar Pilates (clases, reservas, asistencias, facturas, pagos…)",
   huella_empresa(JSU) == FOTO_JSU, [t for t, v in huella_empresa(JSU).items() if v != FOTO_JSU[t]])

# ---------- 7) borrar la Empresa 2 con el script real: sin efectos en JSU y sin huérfanos ----------
REAL = auth.crear_cuenta("persona.real@ejemplo.com", "Persona", "Real", rol="alumna", contrasena=CLAVE, id_empresa=PIL,
                         rol_empresa="alumna")[0]
foto_pil = huella_empresa(PIL)
try:
    with mock.patch("builtins.print"):
        empresa_demo.borrar(db, guardar_contrasenas=False)
    negado = False
except SystemExit as e:
    negado = "no se borra nada" in str(e)
ok("borrar se niega si la empresa tiene una cuenta que no es ficticia, sin borrar nada", negado
   and huella_empresa(PIL) == foto_pil and sql("select count(*) n from empresa where id = %s", (PIL,), True)["n"] == 1)
auth.cliente_admin().auth.admin.delete_user(REAL)
mails_pil = [r["mail"] for r in sql("select p.mail from profiles p join membresia m on m.id_usuario = p.id where m.id_empresa = %s", (PIL,))]
try:
    with mock.patch("builtins.print"):
        empresa_demo.borrar(db, guardar_contrasenas=False)
    borrada = True
except Exception as e:  # noqa: BLE001
    borrada = False
    print("   error al borrar:", str(e)[:200])
ok("empresa_demo.py borrar: termina sin errores", borrada)
ok("…la empresa ya no existe", sql("select count(*) n from empresa where id = %s", (PIL,), True)["n"] == 0)
restos = {t: sql(f"select count(*) n from public.{t} where id_empresa = %s", (PIL,), True)["n"] for t in TABLAS_EMPRESA}
ok("…ninguna fila de Pilates en ninguna tabla", not any(restos.values()), {t: n for t, n in restos.items() if n})
quedan = [m for m in mails_pil if m.endswith("@demo.invalid") and sql("select 1 from auth.users where email = %s", (m,))]
ok("…ninguna cuenta de Pilates (también la creada desde el panel)", not quedan, quedan)
ok("…sin perfiles sin empresa ni sesiones colgadas", sql(
   "select count(*) n from profiles p where not p.es_superadmin and not exists (select 1 from membresia m where m.id_usuario = p.id)",
   uno=True)["n"] == 0 and sql("select count(*) n from profiles where empresa_activa is not null and empresa_activa not in "
                               "(select id from empresa)", uno=True)["n"] == 0)
ok("…la alumna compartida sigue en Just Show Up y entra", entrar("Just Show Up", "alumna@prueba.invalid", CLAVE) is not None
   and sql("select count(*) n from membresia where id_usuario = %s", (COMPARTIDA,), True)["n"] == 1)
ok("…Just Show Up idéntico a antes de crear la Empresa 2", huella_empresa(JSU) == FOTO_JSU,
   [t for t, v in huella_empresa(JSU).items() if v != FOTO_JSU[t]])
ok("…la superadministradora sigue (no es de ninguna empresa)", sql("select count(*) n from profiles where es_superadmin", uno=True)["n"] == 1)
with mock.patch("builtins.print"):
    creada2 = empresa_demo.crear(db, hoy, guardar_contrasenas=False)
ok("se puede volver a crear la Empresa 2 después de borrarla", creada2["clases"] > 0)
with mock.patch("builtins.print"):
    empresa_demo.borrar(db, guardar_contrasenas=False)
ok("…y borrarla otra vez deja Just Show Up idéntico", huella_empresa(JSU) == FOTO_JSU)

print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
