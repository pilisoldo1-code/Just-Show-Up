"""Modo `actualizar` del escenario de Pilates Centro (scripts/empresa_demo.py), en un Postgres LOCAL descartable con la
COPIA de producción: se crea el escenario el 3/11, se simulan cambios de un ensayo y se actualiza el 16/11 y el 17/11.
Comprueba que nunca asigna asistencias por su cuenta (solo las del archivo aprobado; las filas vacías quedan pendientes y
las reservas ajenas o ya registradas se rechazan sin aplicar nada), que solo agrega reservas, clases y la lista de espera,
que conserva lo hecho en el ensayo, que no toca facturas confirmadas, pagos, octubre ni Just Show Up, que la vista previa
no escribe nada y que repetirlo no duplica. No toca Supabase.

Uso:  python tests/base_local/test_empresa_actualizar_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import hashlib
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
from scripts import empresa_demo as ED  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
TZ = ZoneInfo("America/Montevideo")
CREACION = date(2026, 11, 3)


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


class BaseLocal:
    """La misma interfaz que BaseSupabase, como superusuaria local (equivale a la clave de administrador)."""
    local = True

    def como(self, mail, contrasena, lugar):
        cli = ClienteFalso(uri, uid(mail), ahora=f"{CREACION.isoformat()} 12:00:00-03")
        return lambda funcion, params: cli.rpc(funcion, params).execute().data

    def crear_cuenta(self, mail, nombre, apellido, contrasena, meta):
        i = str(uuid.uuid4())
        q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
          (i, mail, psycopg2.extras.Json({"nombre": nombre, "apellido": apellido, **meta})))
        return i

    def borrar_cuenta(self, i):
        q(su, "delete from auth.users where id = %s", (i,))

    def insertar(self, tabla, filas):
        salida = []
        for f in filas:
            cols = list(f)
            salida.append(q(su, f"insert into public.{tabla} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                                "returning *", [f[c] for c in cols], True))
        return [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in s.items()} for s in salida]

    def _donde(self, eq):
        return (" where " + " and ".join(f"{k} = %s" for k in eq)) if eq else "", list(eq.values())

    def leer(self, tabla, columnas, **eq):
        donde, args = self._donde(eq)
        return [{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}
                for r in q(su, f"select {columnas} from public.{tabla}{donde}", args)]

    def actualizar(self, tabla, cambios, **eq):
        donde, args = self._donde(eq)
        q(su, f"update public.{tabla} set {', '.join(f'{k} = %s' for k in cambios)}{donde}", list(cambios.values()) + args)

    def borrar(self, tabla, **eq):
        donde, args = self._donde(eq)
        q(su, f"delete from public.{tabla}{donde}", args)


def huella(sql, args=()):
    return hashlib.sha256(json.dumps(q(su, sql, args), default=str, sort_keys=True).encode()).hexdigest()


TABLAS_E = ["tipo_clase", "horario", "clase", "reserva", "asistencia", "asistencia_historial", "listadeespera", "bloqueo",
            "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion", "membresia"]


def jsu():
    return {t: huella(f"select * from public.{t} where id_empresa = %s order by 1", (E1,)) for t in TABLAS_E} | {
        "empresa": huella("select * from empresa where id = %s", (E1,)),
        "cuentas_previas": q(su, "select count(*) n from auth.users where id = any(%s::uuid[])", (CUENTAS_PREVIAS,), True)["n"]}


def pil_todo():
    return {t: huella(f"select * from public.{t} where id_empresa = %s order by 1", (E2,)) for t in TABLAS_E} | {
        "cuentas": huella("select id, email from auth.users order by id")}


def historico():
    """Lo que nunca puede cambiar: octubre (clases, reservas, asistencias), facturas, líneas, historial de facturas y pagos."""
    return {
        "oct": huella("select c.id, c.fecha, r.id, r.estado, a.estado from clase c join reserva r on r.id_clase = c.id left join "
                      "asistencia a on a.id_reserva = r.id where c.id_empresa = %s and c.fecha < '2026-11-01' order by 1, 3", (E2,)),
        "facturas": huella("select * from resumen_mensual where id_empresa = %s order by id", (E2,)),
        "lineas": huella("select * from resumen_mensual_detalle where id_empresa = %s order by id", (E2,)),
        "historial_facturas": huella("select * from factura_historial where id_empresa = %s order by id", (E2,)),
        "pagos": huella("select * from pago where id_empresa = %s order by id", (E2,)),
    }


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(M1):
            cur.execute(open(a).read())
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
copia_produccion.cargar(su, RESPALDO)
with su.cursor() as cur:
    for a in localdb.migraciones():
        if os.path.basename(a) >= os.path.basename(M1):
            cur.execute(open(a).read())
q(su, localdb.RELOJ)
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
CUENTAS_PREVIAS = [x["id"] for x in q(su, "select id::text from auth.users")]
FOTO_JSU = jsu()
db = BaseLocal()
r = ED.crear(db, CREACION, guardar_contrasenas=False)
E2 = r["empresa"]
ok("escenario creado el 3/11", r["clases"] > 0 and r["facturas"] == 5, r)
U = {m: uid(m) for m in (ED.ROCIO, ED.INES, ED.JULIA, ED.NOELIA, ED.SABRINA, ED.ALUMNA_COMPARTIDA, ED.ADMIN[0])}


def res(mail, fecha, dia):
    return q(su, "select r.id, r.estado, a.estado asistencia from reserva r join clase c on c.id = r.id_clase join horario h "
                 "on h.id = c.id_horario left join asistencia a on a.id_reserva = r.id where r.id_alumna = %s and c.fecha = %s "
                 "and h.dia_semana = %s and c.id_empresa = %s", (U[mail], fecha, dia, E2), True)


# ---------- un ensayo antes de actualizar ----------
q(su, "update asistencia set estado = 'presente' where id_reserva = %s", (res(ED.INES, date(2026, 11, 2), "lunes")["id"],))
q(su, "insert into asistencia_historial (id_empresa, id_reserva, estado_anterior, estado_nuevo, origen, motivo, por) values "
      "(%s, %s, 'ausente', 'presente', 'correccion', 'Ensayo: estaba', %s)", (E2, res(ED.INES, date(2026, 11, 2), "lunes")["id"], U[ED.ADMIN[0]]))
q(su, "update reserva set estado = 'cancelada', fecha_cancelacion = '2026-11-07 10:00-03' where id = %s",
  (res(ED.JULIA, date(2026, 11, 9), "lunes")["id"],))
r_rocio = res(ED.ROCIO, date(2026, 11, 5), "jueves")["id"]
q(su, "insert into asistencia (id_reserva, estado, id_empresa) values (%s, 'ausente', %s)", (r_rocio, E2))
EXTRA = db.crear_cuenta("extra.pilates@demo.invalid", "Extra", "Ensayo", "x", {"id_empresa": E2, "rol_empresa": "alumna"})
c_lunes9 = q(su, "select c.id from clase c join horario h on h.id = c.id_horario where c.id_empresa = %s and c.fecha = '2026-11-09' "
                 "and h.dia_semana = 'lunes'", (E2,), True)["id"]
r_extra = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (EXTRA, c_lunes9, E2), True)["id"]
h_martes = q(su, "select id from horario where id_empresa = %s and dia_semana = 'martes'", (E2,), True)["id"]
q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa, id_profesora) "
      "select id, '2026-11-24', hora_inicio, 'cancelada', 50, 900, 6, id_tipo_clase, id_empresa, id_profesora from horario where id = %s",
  (h_martes,))
HIST = historico()
ok("Just Show Up no cambió con la creación ni con el ensayo", jsu() == FOTO_JSU)

# ---------- 16/11, 12:00: vista previa sin asignaciones ----------
d16 = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)
antes = pil_todo()
plan = ED.actualizar(db, d16, aplicar=False)
PEND = {p["id_reserva"]: p for p in plan["pendientes"]}
ok("la vista previa no escribe nada y no asigna ninguna asistencia por su cuenta",
   pil_todo() == antes and plan["aplicado"] is False and plan["asistencias"] == 0 and plan["ausentes"] == [])
ok("lista las pendientes de noviembre de las alumnas del escenario (ni la compartida, ni otras cuentas, ni octubre facturado)",
   PEND and r_extra not in PEND and r_rocio not in PEND
   and all(p["alumna"] in {m for m, *_ in ED.ALUMNAS} and p["fecha"] >= "2026-11-01" for p in PEND.values()), plan["pendientes"])
ok("cada pendiente muestra cuánto se cobraría: completa si presente, 25 % si ausente",
   all(float(p["cobro_si_ausente"]) == round(float(p["precio"]) * 0.25, 2) and p["cobro_si_presente"] == p["precio"] for p in PEND.values()))
ok("el plan de reservas sigue igual: lista de espera del jueves 26/11", plan["espera"] == [date(2026, 11, 26)], plan)

# ---------- propuesta en CSV, asignación explícita y rechazos ----------
import tempfile  # noqa: E402
tmp = tempfile.mkdtemp()
ruta = os.path.join(tmp, "propuesta.csv")
ok("la propuesta tiene una fila por pendiente y la columna estado vacía",
   ED.escribir_propuesta(plan["pendientes"], ruta) == len(PEND) and ED.leer_asignaciones(ruta)[0] == {})
ines12 = res(ED.INES, date(2026, 11, 12), "jueves")["id"]
sabrina12 = res(ED.SABRINA, date(2026, 11, 12), "jueves")["id"]
import csv  # noqa: E402
filas = list(csv.DictReader(open(ruta, encoding="utf-8"), delimiter=";"))
for f in filas:  # Julieta aprueba todas salvo Sabrina 12/11 (la deja vacía); Inés 12/11 ausente
    rid = int(f["id_reserva"])
    f["estado"] = "" if rid == sabrina12 else ("ausente" if rid == ines12 else "presente")
with open(ruta, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=ED.COLUMNAS, delimiter=";"); w.writeheader(); w.writerows(filas)
ASIG, SHA = ED.leer_asignaciones(ruta)
ok("se leen solo las filas completadas", ines12 in ASIG and sabrina12 not in ASIG and len(ASIG) == len(PEND) - 1)
for nombre, malas in (("una reserva de otra cuenta", {r_extra: "presente"}), ("una reserva que ya tiene asistencia", {r_rocio: "presente"}),
                      ("una reserva de octubre facturado", {res(ED.ROCIO, date(2026, 10, 5), "lunes")["id"]: "presente"})):
    antes = pil_todo()
    try:
        ED.actualizar(db, d16, aplicar=True, asignaciones={**ASIG, **malas})
        ok(f"se rechaza {nombre}", False)
    except SystemExit:
        ok(f"se rechaza {nombre} y no se aplica nada (tampoco las demás)", pil_todo() == antes)

# ---------- 16/11: aplicar las asignaciones aprobadas ----------
r16 = ED.actualizar(db, d16, aplicar=True, asignaciones=ASIG)
ok("aplicado: exactamente las asignaciones del archivo", r16["asistencias"] == len(ASIG)
   and r16["ausentes"] == [(date(2026, 11, 12), ED.INES)] and [p["id_reserva"] for p in r16["quedan_pendientes"]] == [sabrina12], r16)
ok("Inés: ausente el 12/11 (reformer, $1.200 → $300 en el provisorio) y presente el 11/11, como se asignó",
   res(ED.INES, date(2026, 11, 12), "jueves")["asistencia"] == "ausente" and res(ED.INES, date(2026, 11, 11), "miercoles")["asistencia"] == "presente")
ok("Sabrina: la fila que se dejó vacía sigue pendiente (no se inventa)", res(ED.SABRINA, date(2026, 11, 12), "jueves")["asistencia"] is None)
ok("se conserva el ensayo: Inés presente el 2/11, Julia cancelada el 9/11, Rocío ausente el 5/11",
   res(ED.INES, date(2026, 11, 2), "lunes")["asistencia"] == "presente"
   and res(ED.JULIA, date(2026, 11, 9), "lunes") == {"id": res(ED.JULIA, date(2026, 11, 9), "lunes")["id"], "estado": "cancelada", "asistencia": None}
   and res(ED.ROCIO, date(2026, 11, 5), "jueves")["asistencia"] == "ausente")
ok("la reserva de una alumna que no es del escenario no se toca", q(su, "select count(*) n from asistencia where id_reserva = %s",
                                                                  (r_extra,), True)["n"] == 0)
ok("no se revive la cancelación de Julia (ninguna reserva nueva suya ese lunes)",
   q(su, "select count(*) n from reserva where id_alumna = %s and id_clase = %s", (U[ED.JULIA], c_lunes9), True)["n"] == 1)
hist = q(su, "select count(*) n, count(distinct id_reserva) d from asistencia_historial where id_empresa = %s and motivo = %s",
         (E2, ED.MOTIVO_ASIGNADA), True)
SIS = q(su, "select id::text, nombre, apellido, es_superadmin from profiles where mail = %s", (ED.SISTEMA[0],), True)
ok("cada asignación quedó en el historial como «asignación explícita de datos ficticios», a nombre de la cuenta técnica",
   SIS and hist["n"] == hist["d"] == r16["asistencias"]
   and q(su, "select count(*) n from asistencia_historial where motivo = %s and por <> %s", (ED.MOTIVO_ASIGNADA, SIS["id"]), True)["n"] == 0)
ok("la cuenta técnica no pertenece a ninguna empresa ni es superadministradora",
   not SIS["es_superadmin"] and q(su, "select count(*) n from membresia where id_usuario = %s", (SIS["id"],), True)["n"] == 0)
LU = ClienteFalso(uri, U[ED.ADMIN[0]])
c12 = q(su, "select c.id from clase c join horario h on h.id = c.id_horario where c.id_empresa = %s and c.fecha = '2026-11-12' "
            "and h.dia_semana = 'jueves'", (E2,), True)["id"]
vista = LU.rpc("historial_asistencias_clase", {"p_id_clase": c12}).execute().data
ok("en la pantalla, el historial de la clase dice «Carga automática (script de demostración)» y el motivo explícito",
   vista and all(x["por"] == "Carga automática (script de demostración)" and x["motivo"] == ED.MOTIVO_ASIGNADA for x in vista), vista)
ok("octubre, facturas, líneas, historial de facturas y pagos: intactos", historico() == HIST)
jue26 = q(su, "select c.id, c.cantidad_inscriptas, c.cupo_max from clase c join horario h on h.id = c.id_horario where c.id_empresa = %s "
              "and c.fecha = '2026-11-26' and h.dia_semana = 'jueves'", (E2,), True)
ok("jueves 26/11: reformer completo y Noelia primera en la lista de espera", jue26 and jue26["cantidad_inscriptas"] == jue26["cupo_max"]
   and q(su, "select id_alumna::text, posicion, estado from listadeespera where id_clase = %s", (jue26["id"],))
   == [{"id_alumna": U[ED.NOELIA], "posicion": 1, "estado": "en_espera"}], jue26)
ok("la clase cancelada en el ensayo (martes 24/11) no recibe reservas", q(su, "select count(*) n from reserva r join clase c on c.id = r.id_clase "
   "where c.id_horario = %s and c.fecha = '2026-11-24'", (h_martes,), True)["n"] == 0)
ok("la compartida tiene sus lunes en Pilates (16 y 23/11)", q(su, "select count(*) n from reserva r join clase c on c.id = r.id_clase "
   "where r.id_alumna = %s and c.id_empresa = %s and c.fecha in ('2026-11-16', '2026-11-23')", (U[ED.ALUMNA_COMPARTIDA], E2), True)["n"] == 2)
ok("nunca se pasa el cupo", q(su, "select count(*) n from clase c where c.id_empresa = %s and (select count(*) from reserva r where "
   "r.id_clase = c.id and r.estado = 'confirmada') > c.cupo_max", (E2,), True)["n"] == 0)
ok("sin clases duplicadas", q(su, "select count(*) n from (select id_horario, fecha from clase where id_empresa = %s group by 1, 2 "
                                  "having count(*) > 1) x", (E2,), True)["n"] == 0)
ok("Just Show Up idéntico", jsu() == FOTO_JSU)
antes = pil_todo()
try:
    ED.actualizar(db, d16, aplicar=True, asignaciones=ASIG)
    ok("volver a aplicar el mismo archivo se rechaza (ya no son pendientes)", False)
except SystemExit:
    ok("volver a aplicar el mismo archivo se rechaza (ya no son pendientes) sin cambiar nada", pil_todo() == antes)
r16b = ED.actualizar(db, d16, aplicar=True)
ok("repetirlo el mismo día sin asignaciones no cambia nada", pil_todo() == antes and r16b["asistencias"] == r16b["reservas"] == r16b["clases_nuevas"] == 0
   and not r16b["espera"], r16b)

# ---------- 17/11, 10:00 (día de la defensa), sin asignaciones ----------
d17 = datetime(2026, 11, 17, 10, 0, tzinfo=TZ)
r17 = ED.actualizar(db, d17, aplicar=True)
ok("17/11 sin asignaciones: no registra ninguna asistencia; lista como pendientes la de Sabrina 12/11 y las del lunes 16/11",
   r17["asistencias"] == 0 and sabrina12 in {p["id_reserva"] for p in r17["quedan_pendientes"]}
   and {p["fecha"] for p in r17["quedan_pendientes"]} == {"2026-11-12", "2026-11-16"}, r17)
ok("…Inés sigue con la inasistencia del 12/11", res(ED.INES, date(2026, 11, 12), "jueves")["asistencia"] == "ausente")
ok("…la lista de espera del 26/11 sigue igual (no se duplica)", q(su, "select count(*) n from listadeespera where id_clase = %s",
                                                                  (jue26["id"],), True)["n"] == 1)
ok("…octubre, facturas y pagos intactos; Just Show Up idéntico", historico() == HIST and jsu() == FOTO_JSU)
antes = pil_todo()
ok("repetirlo el 17/11 no cambia nada", ED.actualizar(db, d17, aplicar=True)["asistencias"] == 0 and pil_todo() == antes)

# ---------- borrar después de actualizar ----------
ED.borrar(db, guardar_contrasenas=False)
ok("borrar después de actualizar: Pilates y la cuenta técnica eliminadas, Just Show Up idéntico", jsu() == FOTO_JSU
   and q(su, "select count(*) n from empresa where codigo = 'PIL'", uno=True)["n"] == 0
   and q(su, "select count(*) n from profiles where mail = %s", (ED.SISTEMA[0],), True)["n"] == 0)
try:
    ED.actualizar(db, d17, aplicar=True)
    ok("actualizar sin la empresa se niega", False)
except SystemExit:
    ok("actualizar sin la empresa se niega", True)

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
