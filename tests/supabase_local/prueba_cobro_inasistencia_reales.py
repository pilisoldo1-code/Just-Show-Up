"""Pruebas REALES del cobro del 25 % por inasistencia contra un Supabase LOCAL (Colima + `supabase start`, gratis, en
esta computadora), con la app completa por HTTP y PostgREST real: borradores con inasistencias, aviso de asistencias
sin registrar, corrección de asistencia que actualiza el borrador, confirmar, «Anular y reemitir» con la asistencia
corregida, lo que ve la alumna (Mis facturas, PDF, Mi historial), la fecha por empresa (solo la superadministradora) y
los intentos directos contra la API con tokens reales. Correr sobre una base recién reiniciada.

Uso (con Supabase local corriendo en la copia aislada ~/.local/share/jsu-supabase-local):
    python tests/supabase_local/prueba_cobro_inasistencia_reales.py
"""
import json
import os
import re
import subprocess
import sys
import uuid
import zlib
from datetime import date, timedelta

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


def texto(html):
    return " ".join(html.split())


def texto_pdf(datos: bytes) -> str:
    return "\n".join(zlib.decompress(m.group(1)).decode("latin-1")
                     for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", datos, re.S))


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
                           ("eva.pil@prueba.invalid", "Eva", "alumna"))}
PROF, ANA, EVA = (cuentas[m] for m in ("profe.pil@prueba.invalid", "ana.pil@prueba.invalid", "eva.pil@prueba.invalid"))
A, P, AL, EV = (entrar("Pilates Centro", m) for m in (ADMIN_PIL, "profe.pil@prueba.invalid",
                                                      "ana.pil@prueba.invalid", "eva.pil@prueba.invalid"))
J1 = entrar("Just Show Up", "profesora@prueba.invalid")
ok("entran la administradora, la profesora y dos alumnas de Pilates Centro, y la administradora de Just Show Up",
   all(x is not None for x in (A, P, AL, EV, J1)))

# ---------- la fecha de inicio: solo la superadministradora, nunca hacia atrás ----------
hoy = clases.ahora().date()
inicio_mes = hoy.replace(day=1)
h = texto(SA.get(f"/admin/empresas/{PIL}").text)
ok("Gestionar empresa: sección «Cobro por inasistencia», sin aplicar en una empresa nueva",
   "Cobro por inasistencia" in h and "No se aplica" in h and f'min="{inicio_mes.isoformat()}"' in h)
ok("Just Show Up ya tiene su fecha (1/11/2026)", "Rige desde el <strong>1/11/2026</strong>" in texto(SA.get(f"/admin/empresas/{JSU}").text))
r = SA.post(f"/admin/empresas/{PIL}/cobro-inasistencia", data={"desde": (inicio_mes - timedelta(days=1)).isoformat()},
            follow_redirects=False)
ok("una fecha anterior al mes en curso se rechaza", "ok=0" in r.headers["location"]
   and sql("select cobro_inasistencia_desde d from empresa where id = %s", (PIL,), True)["d"] is None)
r = SA.post(f"/admin/empresas/{PIL}/cobro-inasistencia", data={"desde": inicio_mes.isoformat()}, follow_redirects=False)
ok("desde el 1° del mes en curso se guarda", "ok=1" in r.headers["location"]
   and sql("select cobro_inasistencia_desde d from empresa where id = %s", (PIL,), True)["d"] == inicio_mes)
r = A.post(f"/admin/empresas/{PIL}/cobro-inasistencia", data={"desde": "2030-01-01"}, follow_redirects=False)
ok("la administradora de la empresa no puede cambiarla (pantalla)", r.status_code == 303
   and sql("select cobro_inasistencia_desde d from empresa where id = %s", (PIL,), True)["d"] == inicio_mes)
for nombre, cli in (("administradora", A), ("profesora", P), ("alumna", AL), ("otra empresa", J1)):
    api = auth.cliente_con_token(cli.cookies.get("sb_acceso"))
    res = api.rpc("configurar_cobro_inasistencia", {"p_id_empresa": PIL, "p_desde": "2030-01-01"}).execute().data
    ok(f"API directa con el token real ({nombre}): no autorizada", res == "no_autorizada", res)
api_al = auth.cliente_con_token(AL.cookies.get("sb_acceso"))
try:
    api_al.table("empresa").update({"cobro_inasistencia_desde": "2026-01-01"}).eq("id", PIL).execute()
except Exception:
    pass
ok("nadie escribe la fecha directo en la tabla", sql("select cobro_inasistencia_desde d from empresa where id = %s", (PIL,), True)["d"]
   == inicio_mes)

# ---------- datos: un mes cerrado (hace dos meses); la fecha de inicio se pone por SQL en la base LOCAL ----------
MES_D = (inicio_mes - timedelta(days=1)).replace(day=1) - timedelta(days=1)
MES, ANIO = MES_D.month, MES_D.year
sql("update empresa set cobro_inasistencia_desde = %s where id = %s", (date(ANIO, MES, 1), PIL))
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Pilates mat', 50, 800, 5, %s) returning id", (PIL,), True)["id"]


def clase(dia, precio=800):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "('domingo', '10:00', %s, false, %s, %s) returning id", (TIPO, PROF, PIL), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa, "
               "id_profesora) values (%s, %s, '10:00', 'programada', 50, %s, 5, %s, %s, %s) returning id",
               (h, date(ANIO, MES, dia), precio, TIPO, PIL, PROF), True)["id"]


def reservar(alumna, id_clase, asistencia=None):
    r = sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (alumna, id_clase, PIL), True)["id"]
    if asistencia:
        sql("insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, PIL))
    return r


C1, C2, C3, C4 = clase(5), clase(12), clase(19, 850), clase(26)
R_AUS, R_PRE, R_850, R_SIN = reservar(ANA, C1, "ausente"), reservar(ANA, C2, "presente"), reservar(ANA, C3, "ausente"), reservar(ANA, C4)
R_EVA = reservar(EVA, C1, "presente")

# ---------- facturación del mes ----------
h = texto(A.get(f"/profesora/resumenes?mes={MES}&anio={ANIO}").text)
ok("abrir Facturación arma los borradores", "Ana Prueba" in h and "Eva Prueba" in h)
ok("aviso visible de asistencias sin registrar, con enlace a completarlas (sin bloquear)",
   "<strong>1 asistencia sin registrar</strong>" in h and f"filtro=pendientes#terminadas" in h
   and "Confirmar todas las facturas del mes" in h)
F = sql("select * from resumen_mensual where id_alumna = %s and mes = %s and anio = %s", (ANA, MES, ANIO), True)
ok("total de Ana: 200 + 800 + 212,50 + 800 = 2.012,50", F["total"] == 2012.5, F["total"])
d = texto(A.get(f"/profesora/resumenes/{F['id']}").text)
ok("detalle: «Inasistencia · 25 %», precio tachado y el resumen de asistencia",
   d.count("Inasistencia · 25 %") == 2 and '$800,00</s><span class="precio-base">$200,00</span>' in d and '$850,00</s><span class="precio-base">$212,50</span>' in d
   and "1 presente · 2 inasistencias (25 %) · 1 sin registrar" in d)
ok("detalle: aviso de la clase sin registrar con enlace", "1 sin registrar: se cobra al 100 %" in d)
pv = texto(A.get(f"/profesora/resumenes/confirmar-mes?mes={MES}&anio={ANIO}").text)
ok("vista previa del mes: el mismo aviso", "<strong>1 asistencia sin registrar</strong>" in pv and "Confirmar 2 facturas" in pv)

# completar la asistencia que faltaba (ausente) → el borrador se actualiza al volver a abrir el mes
r = A.post(f"/profesora/clases/{C4}/corregir", data={f"estado_{R_SIN}": "ausente", "motivo": "Planilla en papel"},
           follow_redirects=False)
ok("la administradora completa la asistencia que faltaba", "ok=1" in r.headers["location"])
A.get(f"/profesora/resumenes?mes={MES}&anio={ANIO}")
F = sql("select * from resumen_mensual where id = %s", (F["id"],), True)
ok("al volver a abrir el mes, el borrador pasa a 200 + 800 + 212,50 + 200 = 1.412,50", F["total"] == 1412.5, F["total"])
d = texto(A.get(f"/profesora/resumenes/{F['id']}").text)
ok("…y el historial de la factura lo registra", "Cambio de asistencia" in d and "Sin registrar → Ausente · 100 % → 25 %" in d)
ok("…sin pendientes, el aviso desaparece", "sin registrar</strong>" not in texto(A.get(f"/profesora/resumenes?mes={MES}&anio={ANIO}").text))

# ajuste y descuento
linea = sql("select id from resumen_mensual_detalle where id_reserva = %s and id_resumen_mensual = %s", (R_AUS, F["id"]), True)["id"]
r = A.post(f"/profesora/resumenes/{F['id']}/lineas/{linea}/ajustar", data={"version": F["version"], "tipo": "importe",
                                                                         "importe": "300", "motivo": "Prueba"}, follow_redirects=False)
ok("«cobrar menos» por encima del importe de la inasistencia ($300 > $200): rechazado", "ok=0" in r.headers["location"])
F = sql("select * from resumen_mensual where id = %s", (F["id"],), True)
r = A.post(f"/profesora/resumenes/{F['id']}/descuento", data={"version": F["version"], "tipo": "porcentaje", "valor": "10",
                                                             "motivo": "Promo"}, follow_redirects=False)
F = sql("select * from resumen_mensual where id = %s", (F["id"],), True)
ok("descuento del 10 % sobre las líneas ya calculadas: 1.271,25", "ok=1" in r.headers["location"] and float(F["total"]) == 1271.25,
   F["total"])

# confirmar
r = A.post(f"/profesora/resumenes/{F['id']}/confirmar", data={"version": F["version"]}, follow_redirects=False)
F = sql("select * from resumen_mensual where id = %s", (F["id"],), True)
ok("se confirma", "ok=1" in r.headers["location"] and F["estado_factura"] == "confirmada", r.headers["location"])
r = A.post(f"/profesora/clases/{C1}/corregir", data={f"estado_{R_AUS}": "presente", "motivo": "Estaba"}, follow_redirects=False)
ok("con la factura confirmada no se corrige su asistencia", "ok=0" in r.headers["location"])

# lo que ve la alumna (el mes ya está publicado)
mf = texto(AL.get("/mis-facturas").text)
ok("Mis facturas: «Inasistencia · 25 %» y «$800,00 → $200,00» junto a cada clase, sin nota larga al pie",
   mf.count("Inasistencia · 25 %") >= 3 and '$800,00</s><span class="precio-base">$200,00</span>' in mf
   and "clase reservada que no se canceló a tiempo y a la que no asististe" not in mf)
ok("…sin motivos ni datos internos", all(x not in mf for x in ("Promo", "Planilla", "Sin registrar")))
pdf = texto_pdf(AL.get(f"/mis-facturas/{F['id']}/pdf").content)
ok("PDF: marca, explicación y fila de inasistencias", "Pilates mat *" in pdf and "Inasist. 25 %" in pdf
   and "Inasistencias \\(3\\) al 25 %" in pdf and "* Inasistencia \\(25 %\\)" in pdf)
hist = texto(AL.get("/mi-historial").text)
ok("Mi historial: «se cobra el 25 % del precio» en sus 3 ausencias ($200,00, $212,50 y $200,00), sin «Importe estimado» y la nota una vez",
   hist.count("Inasistencia: se cobra el 25 % del precio ($200,00)") == 2
   and hist.count("Inasistencia: se cobra el 25 % del precio ($212,50)") == 1 and "Importe estimado" not in hist
   and hist.count("figuran en Mis facturas") == 1)
ok("Eva (presente) no ve nada de inasistencias", "Inasistencia" not in EV.get("/mis-facturas").text
   and "se cobra el 25" not in EV.get("/mi-historial").text)
api_eva = auth.cliente_con_token(EV.cookies.get("sb_acceso"))
ok("Eva no lee las líneas de Ana por la API", api_eva.table("resumen_mensual_detalle").select("id")
   .eq("id_resumen_mensual", F["id"]).execute().data == [])
try:
    api_al.table("resumen_mensual_detalle").update({"importe_final": 0}).eq("id", linea).execute()
except Exception:
    pass
ok("la alumna no puede tocar sus líneas por la API",
   float(sql("select importe_final v from resumen_mensual_detalle where id = %s", (linea,), True)["v"]) == 200)

# ---------- anular y reemitir con la asistencia corregida ----------
r = A.post(f"/profesora/resumenes/{F['id']}/pagar", data={"monto": str(F["total"]), "medio_pago": "efectivo",
                                                          "fecha": hoy.isoformat()}, follow_redirects=False)
pago = sql("select * from pago where id_resumen_mensual = %s", (F["id"],), True)
ok("pago registrado", pago and pago["estado"] == "vigente")
r = A.post(f"/profesora/resumenes/{F['id']}/anular", data={"motivo": "Error de asistencia", "tipo": "correccion"},
           follow_redirects=False)
ok("con pago vigente no se anula", "ok=0" in r.headers["location"])
A.post(f"/profesora/resumenes/{F['id']}/pagos/{pago['id']}/anular", data={"motivo": "Para corregir"}, follow_redirects=False)
r = A.post(f"/profesora/resumenes/{F['id']}/anular", data={"motivo": "Error de asistencia", "tipo": "correccion"},
           follow_redirects=False)
N = sql("select * from resumen_mensual where reemplaza_a = %s", (F["id"],), True)
ok("anular y reemitir: borrador nuevo", N is not None and N["estado_factura"] == "borrador", r.headers["location"])
r = A.post(f"/profesora/clases/{C1}/corregir", data={f"estado_{R_AUS}": "presente", "motivo": "Estaba, error de planilla"},
           follow_redirects=False)
ok("ahora se corrige la asistencia", "ok=1" in r.headers["location"])
A.get(f"/profesora/resumenes?mes={MES}&anio={ANIO}")
N = sql("select * from resumen_mensual where id = %s", (N["id"],), True)
ok("el borrador nuevo se recalcula: (800 + 800 + 212,50 + 200) − 10 % = 1.811,25", float(N["total"]) == 1811.25, N["total"])
r = A.post(f"/profesora/resumenes/{N['id']}/confirmar", data={"version": N["version"]}, follow_redirects=False)
N = sql("select * from resumen_mensual where id = %s", (N["id"],), True)
V = sql("select * from resumen_mensual where id = %s", (F["id"],), True)
ok("se confirma con otro número; la anulada conserva su total", N["estado_factura"] == "confirmada" and N["numero"] != V["numero"]
   and V["estado_factura"] == "anulada" and V["total"] == F["total"])
ok("el pago anulado sigue igual", sql("select estado, monto from pago where id = %s", (pago["id"],), True)
   == {"estado": "anulado", "monto": pago["monto"]})
mf = texto(AL.get("/mis-facturas").text)
ok("la alumna ve la nueva y la anulada marcada como reemplazada", N["numero"] in mf and "reemplazada por" in mf)

# ---------- aislamiento ----------
ok("la otra empresa no ve estas facturas", J1.get(f"/profesora/resumenes/{N['id']}", follow_redirects=False).status_code == 303
   or "Ana Prueba" not in J1.get(f"/profesora/resumenes/{N['id']}").text)
ok("ni las listas del mes", "Ana Prueba" not in J1.get(f"/profesora/resumenes?mes={MES}&anio={ANIO}").text)
ok("las facturas de Just Show Up no tienen líneas al 25 % (ninguna clase antes del 1/11)",
   sql("select count(*) n from resumen_mensual_detalle where id_empresa = %s and porcentaje <> 100", (JSU,), True)["n"] == 0)

print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
