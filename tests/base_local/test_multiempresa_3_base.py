"""Multiempresa, parte 3 (funciones por empresa y por rol), en un Postgres LOCAL descartable que
reconstruye producción con una COPIA de sus datos (respaldo fuera del repo). Crea la Empresa 2 con las
funciones de la superadministradora y opera las dos empresas de punta a punta: membresías, clases,
reservas, asistencia, licencias, facturas (una por alumna, empresa y mes, con profesora y numeración
propia) y Mercado Pago (solo la Empresa 1). Comprueba los permisos de cada rol y la vuelta atrás.
No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_3_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
MVD = ZoneInfo("America/Montevideo")


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def rpc(cli, f, **kw):
    try:
        return cli.rpc(f, kw).execute().data
    except psycopg2.Error as e:
        return {"error": e.pgcode}


def res(r):
    return r.get("resultado") if isinstance(r, dict) else r


def ver(cli, t):
    return cli.table(t).select("*").execute().data


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG = {n: [a for a in localdb.migraciones() if a.endswith(n)][0] for n in
       ("_multiempresa_1_estructura.sql", "_multiempresa_2_permisos.sql", "_multiempresa_3_funciones.sql")}
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_3.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(MIG["_multiempresa_1_estructura.sql"]):
            cur.execute(open(a).read())
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
copia_produccion.cargar(su, RESPALDO)
with su.cursor() as cur:
    cur.execute(open(MIG["_multiempresa_1_estructura.sql"]).read())
    cur.execute(open(MIG["_multiempresa_2_permisos.sql"]).read())
estructura_m2 = huellas_estructura()
with su.cursor() as cur:
    cur.execute(open(MIG["_multiempresa_3_funciones.sql"]).read())
ok("la parte 3 se aplica sobre la copia de producción (con las partes 1 y 2)", True)
q(su, localdb.RELOJ)
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]


def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].replace(".", " ").title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id from profiles where mail = %s", (mail,), True)["id"]
ADMIN1, DEMO, SOFIA = uid("profesora@prueba.invalid"), uid("alumna@prueba.invalid"), uid("sofia@demo.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
AHORA = "2026-11-03 10:00-03"
C = lambda i, ahora=AHORA: ClienteFalso(uri, i, ahora=ahora)

# ---------- superadministradora ----------
S = C(SUPER)
ok("superadmin: código inválido → datos_invalidos", res(rpc(S, "crear_empresa", p_nombre="Pilates Centro", p_codigo="pil")) == "datos_invalidos")
ok("superadmin: código repetido → codigo_repetido", res(rpc(S, "crear_empresa", p_nombre="Otra", p_codigo="JSU")) == "codigo_repetido")
r = rpc(S, "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")
ok("superadmin: crea la Empresa 2", res(r) == "ok", r)
E2 = r["id"]
ok("una administradora no puede crear empresas", res(rpc(C(ADMIN1), "crear_empresa", p_nombre="X", p_codigo="XX")) == "no_autorizada")
ADMIN2 = cuenta("admin.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})  # como el servidor
ok("superadmin: asigna la administradora (y profesora) de la Empresa 2",
   rpc(S, "asignar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_es_profesora=True) == "ok")
m = q(su, "select * from membresia where id_usuario = %s", (ADMIN2,), True)
ok("la administradora nueva queda con administradora + profesora en la Empresa 2", m["es_admin"] and m["es_profesora"] and m["id_empresa"] == E2)

A2 = C(ADMIN2)
PROF2 = cuenta("profe.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "profesora"})
ALU2 = cuenta("alumna.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
ok("administradora: da de alta una profesora", rpc(A2, "asignar_membresia", p_id_usuario=PROF2, p_es_admin=False, p_es_profesora=True, p_es_alumna=False) == "ok")
ok("administradora: confirma una alumna", rpc(A2, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "ok")
ok("administradora: agrega a una alumna que ya existe en otra empresa (misma cuenta)",
   rpc(A2, "asignar_membresia", p_id_usuario=DEMO, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "ok"
   and q(su, "select count(*) n from membresia where id_usuario = %s", (DEMO,), True)["n"] == 2)
ok("administradora: no puede quitarse el rol de administradora",
   rpc(A2, "asignar_membresia", p_id_usuario=ADMIN2, p_es_admin=False, p_es_profesora=True, p_es_alumna=False) == "no_puede_quitarse_admin")
P2 = C(PROF2)
ok("profesora: no puede dar de alta personas", rpc(P2, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "no_autorizada")
lista = {e["codigo"]: e for e in rpc(S, "resumen_empresas")}
ok("superadmin: resumen con profesoras, alumnas y administradora, sin datos operativos",
   lista["PIL"]["profesoras"] == 2 and lista["PIL"]["alumnas"] == 2 and "admin.pil@prueba.invalid" in lista["PIL"]["administradoras"]
   and lista["JSU"]["alumnas"] >= 10, lista)
ok("una administradora no ve el resumen de empresas", rpc(A2, "resumen_empresas") == [])

# ---------- tipos, horarios y clases de la Empresa 2 ----------
ok("profesora no administradora: no crea tipos de clase",
   res(rpc(P2, "crear_tipo_clase", p_nombre="Pilates", p_descripcion="x", p_duracion=50, p_precio=900, p_cupo=6, p_imagen_url=None)) == "no_autorizada")
r = rpc(A2, "crear_tipo_clase", p_nombre="Gimnasia", p_descripcion="Pilates mat", p_duracion=50, p_precio=900, p_cupo=6, p_imagen_url=None)
ok("administradora: crea un tipo de clase (mismo nombre que en la Empresa 1)", res(r) == "ok", r)
T2 = q(su, "select id from tipo_clase where id_empresa = %s", (E2,), True)["id"]
with A2.conn.cursor() as cur:
    cur.execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora) values ('martes', '18:00', %s, true, %s) returning id", (T2, PROF2))
    H2P = cur.fetchone()[0]
    cur.execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo) values ('jueves', '19:00', %s, true) returning id", (T2,))
    H2A = cur.fetchone()[0]
ok("administradora: crea horarios para otra profesora y para sí misma",
   q(su, "select id_profesora from horario where id = %s", (H2P,), True)["id_profesora"] == PROF2
   and q(su, "select id_profesora from horario where id = %s", (H2A,), True)["id_profesora"] == ADMIN2)
with P2.conn.cursor() as cur:
    try:
        cur.execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora) values ('lunes', '07:00', %s, true, %s)", (T2, ADMIN2))
        ok("profesora: no puede crear horarios a nombre de otra", False)
    except psycopg2.Error:
        ok("profesora: no puede crear horarios a nombre de otra", True)
P2.conn.rollback() if not P2.conn.autocommit else None
antes_e1 = q(su, "select count(*) n from clase where id_empresa = %s", (E1,), True)["n"]
q(su, "select set_config('request.jwt.claim.sub', '', false)")
ALU2C = C(ALU2)
r = rpc(ALU2C, "generar_clases", p_lunes="2026-11-09")
ok("generar_clases (alumna de la Empresa 2): crea solo clases de la Empresa 2, sin error",
   not (isinstance(r, dict) and r.get("error"))
   and q(su, "select count(*) n from clase where id_empresa = %s", (E1,), True)["n"] == antes_e1
   and q(su, "select count(*) n from clase where id_empresa = %s and fecha between '2026-11-09' and '2026-11-15'", (E2,), True)["n"] == 2, r)
CP = q(su, "select id from clase where id_horario = %s and fecha = '2026-11-10'", (H2P,), True)["id"]
CA = q(su, "select id from clase where id_horario = %s and fecha = '2026-11-12'", (H2A,), True)["id"]
r1 = rpc(C(DEMO), "elegir_empresa", p_id_empresa=E2)
ok("la alumna compartida elige la Empresa 2", r1 == "ok")
ok("elegir una empresa sin membresía → no_autorizada", rpc(C(SOFIA), "elegir_empresa", p_id_empresa=E2) == "no_autorizada")
for quien, clase in ((ALU2, CP), (DEMO, CP), (ALU2, CA)):
    rpc(C(quien), "reservar", p_id_clase=clase)
ok("reservas de la Empresa 2 creadas por sus alumnas", q(su, "select count(*) n from reserva where id_empresa = %s and estado = 'confirmada'", (E2,), True)["n"] == 3)
ok("cupos (reservas_confirmadas): la Empresa 1 no ve los de la Empresa 2", rpc(C(SOFIA), "reservas_confirmadas", p_ids=[CP, CA]) == [])
ok("cupos: la Empresa 2 sí", {x["id_clase"]: x["cantidad"] for x in rpc(C(ALU2), "reservas_confirmadas", p_ids=[CP, CA])} == {CP: 2, CA: 1})

# ---------- asistencia: profesora de la clase o administradora ----------
hoy = datetime.now(MVD)
inicio = (hoy - timedelta(minutes=5)).strftime("%H:%M")
CHOY = q(su, "insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa) "
             "values (%s, %s, %s, 0, 'programada', 50, 900, 6, %s, %s) returning id", (H2P, hoy.date(), inicio, T2, E2), True)["id"]
RH = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id", (ALU2, CHOY, E2), True)["id"]
CHOYA = q(su, "insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa) "
              "values (%s, %s, %s, 0, 'programada', 50, 900, 6, %s, %s) returning id", (H2A, hoy.date(), inicio, T2, E2), True)["id"]
RHA = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id", (ALU2, CHOYA, E2), True)["id"]
ok("profesora: marca la asistencia de su clase", rpc(P2, "marcar_asistencia", p_id_reserva=RH, p_estado="presente") == "ok")
ok("profesora: no puede marcar en la clase de otra profesora", rpc(P2, "marcar_asistencia", p_id_reserva=RHA, p_estado="presente") == "no_autorizada")
ok("administradora: marca en cualquier clase de su empresa", rpc(A2, "marcar_asistencia", p_id_reserva=RHA, p_estado="ausente") == "ok")
ok("administradora de la Empresa 1: no puede marcar en la Empresa 2", rpc(C(ADMIN1), "marcar_asistencia", p_id_reserva=RH, p_estado="ausente") == "sin_reserva")

# ---------- licencias ----------
r = rpc(P2, "crear_bloqueo", p_tipo="licencia", p_desde="2026-11-09", p_hasta="2026-11-15", p_id_horario=None, p_motivo="Licencia")
ok("profesora: licencia general cancela solo SUS clases",
   res(r) == "ok" and q(su, "select estado from clase where id = %s", (CP,), True)["estado"] == "cancelada"
   and q(su, "select estado from clase where id = %s", (CA,), True)["estado"] == "programada", r)
B_P2 = r.get("id")
ok("profesora: no puede cancelar puntualmente una clase de otra profesora",
   res(rpc(P2, "crear_bloqueos_puntuales", p_ids_clase=[CA], p_motivo="x")) == "clases_invalidas")
r = rpc(A2, "crear_bloqueo", p_tipo="licencia", p_desde="2026-11-12", p_hasta="2026-11-12", p_id_horario=None, p_motivo="Feriado")
B_A2 = r.get("id")
ok("administradora: licencia general cancela las clases de toda su empresa ese día",
   res(r) == "ok" and q(su, "select estado from clase where id = %s", (CA,), True)["estado"] == "cancelada")
e1_prog = q(su, "select count(*) n from clase where id_empresa = %s and estado = 'programada'", (E1,), True)["n"]
ok("las licencias de la Empresa 2 no tocan clases de la Empresa 1", e1_prog > 0
   and q(su, "select count(*) n from clase where id_empresa = %s and estado = 'programada'", (E1,), True)["n"] == e1_prog)
ok("profesora: no puede quitar la licencia de la administradora", res(rpc(P2, "quitar_bloqueo", p_id=B_A2)) == "no_autorizada")
r = rpc(P2, "quitar_bloqueo", p_id=B_P2)
ok("profesora: quita su licencia y su clase vuelve, con la reserva recuperada",
   res(r) == "ok" and q(su, "select estado from clase where id = %s", (CP,), True)["estado"] == "programada"
   and q(su, "select count(*) n from reserva where id_clase = %s and estado = 'confirmada'", (CP,), True)["n"] == 2, r)
r = rpc(C(ADMIN1), "crear_bloqueo", p_tipo="licencia", p_desde="2026-11-09", p_hasta="2026-11-15", p_id_horario=None, p_motivo="Vacaciones E1")
ok("licencia general de la Empresa 1: no cancela clases de la Empresa 2",
   res(r) == "ok" and q(su, "select estado from clase where id = %s", (CP,), True)["estado"] == "programada")
rpc(C(ADMIN1), "quitar_bloqueo", p_id=r.get("id"))

# ---------- facturación: una factura por alumna, empresa y mes ----------
OCT = "2026-10-31 23:00-03"
CO = q(su, "insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa, id_profesora) "
           "values (%s, '2026-10-20', '18:00', 0, 'programada', 50, 900, 6, %s, %s, %s) returning id", (H2P, T2, E2, PROF2), True)["id"]
for quien in (ALU2, DEMO):
    q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (quien, CO, E2))
ok("profesora: no puede preparar facturas", res(rpc(C(PROF2, OCT), "sincronizar_borradores", p_mes=10, p_anio=2026)) == "no_autorizada")
r = rpc(C(ADMIN2, OCT), "sincronizar_borradores", p_mes=10, p_anio=2026)
ok("administradora de la Empresa 2: prepara SOLO las facturas de su empresa",
   res(r) == "ok" and r["creadas"] == 2
   and q(su, "select count(*) n from resumen_mensual where mes = 10 and id_empresa <> %s", (E2,), True)["n"] == 0, r)
r = rpc(C(ADMIN1, OCT), "sincronizar_borradores", p_mes=10, p_anio=2026)
ok("administradora de la Empresa 1: prepara las de la Empresa 1 (la alumna compartida tiene una en cada empresa)",
   res(r) == "ok" and q(su, "select count(*) n from resumen_mensual where mes = 10 and anio = 2026 and id_alumna = %s and estado_factura <> 'anulada'",
                         (DEMO,), True)["n"] == 2, r)
rpc(C(ADMIN2, OCT), "sincronizar_borradores", p_mes=10, p_anio=2026)
ok("volver a sincronizar no duplica facturas",
   q(su, "select count(*) n from resumen_mensual where mes = 10 and anio = 2026 and id_empresa = %s", (E2,), True)["n"] == 2)
lin = q(su, """select d.profesora_nombre, btrim(p.nombre || ' ' || p.apellido) esperada
               from resumen_mensual_detalle d join clase c on c.id = d.id_clase join profiles p on p.id = c.id_profesora
               where d.id_empresa = %s""", (E2,))
ok("cada línea de factura muestra la profesora que dictó esa clase (hay clases de las dos profesoras)",
   lin and all(l["profesora_nombre"] == l["esperada"] for l in lin) and len({l["esperada"] for l in lin}) == 2, lin)
FD2 = q(su, "select id, version from resumen_mensual where id_empresa = %s and id_alumna = %s and mes = 10", (E2, DEMO), True)
r = rpc(C(ADMIN2, "2026-11-01 10:00-03"), "confirmar_factura", p_id=FD2["id"], p_version=FD2["version"])
ok("Empresa 2: número propio PIL-2026-10-0001", res(r) == "ok" and r["numero"] == "PIL-2026-10-0001", r)
FD1 = q(su, "select id, version from resumen_mensual where id_empresa = %s and id_alumna = %s and mes = 10 and estado_factura = 'borrador'", (E1, DEMO), True)
r = rpc(C(ADMIN1, "2026-11-01 10:00-03"), "confirmar_factura", p_id=FD1["id"], p_version=FD1["version"])
ok("Empresa 1: sigue su numeración JSU-2026-10-0001 (no la afecta la Empresa 2)", res(r) == "ok" and r["numero"] == "JSU-2026-10-0001", r)
ok("administradora de la Empresa 1: no puede confirmar una factura de la Empresa 2",
   res(rpc(C(ADMIN1, "2026-11-01 10:00-03"), "confirmar_factura", p_id=FD2["id"], p_version=1)) == "no_existe")
ok("profesora: no puede registrar pagos",
   res(rpc(C(PROF2, "2026-11-02 10:00-03"), "registrar_pago", p_id_resumen=FD2["id"], p_monto="900", p_medio="efectivo", p_fecha="2026-11-02")) == "no_autorizada")
ok("profesora: no ve facturas", ver(P2, "resumen_mensual") == [])

# ---------- Mercado Pago: solo la Empresa 1 ----------
SERV = ClienteFalso(uri, None, rol="service_role", ahora="2026-11-02 10:00-03")
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=True)
DM = C(DEMO, "2026-11-02 10:00-03")
rpc(DM, "elegir_empresa", p_id_empresa=E2)
ok("Mercado Pago: la factura de la Empresa 2 no se puede pagar (empresa sin Mercado Pago)", res(rpc(DM, "crear_intento_mp", p_id_resumen=FD2["id"])) == "deshabilitado")
rpc(DM, "elegir_empresa", p_id_empresa=E1)
r = rpc(DM, "crear_intento_mp", p_id_resumen=FD1["id"])
ok("Mercado Pago: la factura de la Empresa 1 sí (alumna@prueba.invalid, modo prueba)", res(r) == "ok", r)
ok("Mercado Pago: con la Empresa 1 activa no se puede iniciar el pago de una factura de la Empresa 2",
   res(rpc(DM, "crear_intento_mp", p_id_resumen=FD2["id"])) == "no_existe")
q(su, "insert into mp_intento (id_resumen_mensual, id_alumna, referencia, monto, id_empresa) values (%s, %s, 'JSU-forzado-e2', 900, %s)", (FD2["id"], DEMO, E2))
r = rpc(SERV, "registrar_pago_mercado_pago", p_referencia="JSU-forzado-e2", p_id_pago_mp="999", p_estado="approved", p_monto="900", p_moneda="UYU", p_fecha="2026-11-02")
ok("Mercado Pago: un pago aprobado sobre una factura de una empresa sin Mercado Pago no se registra (a revisar)",
   res(r) == "a_revisar" and q(su, "select estado_pago from resumen_mensual where id = %s", (FD2["id"],), True)["estado_pago"] == "pendiente", r)

# ---------- código de empresa fijo y empresa desactivada ----------
ok("superadmin: el código queda fijo después de la primera factura", rpc(S, "editar_empresa", p_id=E2, p_nombre="Pilates Centro", p_codigo="PLC") == "codigo_fijo")
ok("superadmin: el nombre se puede editar", rpc(S, "editar_empresa", p_id=E2, p_nombre="Pilates del Centro", p_codigo="PIL") == "ok")
ok("superadmin: desactiva la Empresa 2", rpc(S, "cambiar_estado_empresa", p_id=E2, p_activa=False) == "ok")
ok("empresa desactivada: su administradora no ve nada y no puede elegirla",
   ver(A2, "clase") == [] and rpc(A2, "elegir_empresa", p_id_empresa=E2) == "no_autorizada")
rpc(S, "cambiar_estado_empresa", p_id=E2, p_activa=True)
ok("administradora: desactiva a una alumna de su empresa", rpc(A2, "cambiar_estado_membresia", p_id_usuario=ALU2, p_activa=False) == "ok"
   and ver(ALU2C, "clase") == [])

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999997', 'multiempresa_3_funciones')")
try:
    with su.cursor() as cur:
        cur.execute(open(VUELTA).read())
    ok("vuelta atrás: se niega con dos empresas", False)
except psycopg2.Error:
    ok("vuelta atrás: se niega con dos empresas", True)
q(su, "rollback")
OP = ["mp_intento", "pago_anulacion", "pago", "factura_historial", "resumen_mensual_detalle", "asistencia", "listadeespera",
      "reserva", "bloqueo", "clase", "horario", "tipo_clase"]
q(su, "update resumen_mensual set reemplaza_a = null where id_empresa = %s", (E2,))
for t in OP[:5] + ["resumen_mensual"] + OP[5:]:
    q(su, f"delete from public.{t} where id_empresa = %s", (E2,))
q(su, "update profiles set empresa_activa = %s where empresa_activa = %s", (E1, E2))
q(su, "delete from membresia where id_empresa = %s", (E2,))
for i in (ADMIN2, PROF2, ALU2, SUPER):
    q(su, "delete from profiles where id = %s", (i,))
    q(su, "delete from auth.users where id = %s", (i,))
q(su, "delete from empresa where id = %s", (E2,))
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_3_funciones'", uno=True)["n"] == 0)
q(su, """create or replace function public._ahora()
returns timestamptz
language sql
stable
set search_path = ''
as $$
    select now();
$$;""")
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de la parte 2", final == estructura_m2,
   {k: (final.get(k), v) for k, v in estructura_m2.items() if final.get(k) != v})

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
