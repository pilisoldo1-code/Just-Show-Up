"""Multiempresa, parte 2 (permisos por empresa), en un Postgres LOCAL descartable que reconstruye
producción y carga una COPIA de sus datos (respaldo fuera del repo). Crea una segunda empresa con
administradora, profesora, alumna propia y una alumna compartida, e intenta leer y modificar todo lo de
la otra empresa por tablas y por funciones. Además: datos intactos, el código actual sigue funcionando,
roles (administradora, profesora no administradora, alumna, superadministradora), acceso sin sesión
cerrado, contador de facturas por empresa y vuelta atrás exacta. No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_2_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import hashlib
import json
import os
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
OPERATIVAS = ["tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo", "resumen_mensual",
              "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion", "mp_intento"]


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def intento(cli, f, **kw):
    try:
        return cli.rpc(f, kw).execute().data
    except psycopg2.Error as e:
        return {"error": e.pgcode}


def ver(cli, tabla):
    try:
        return cli.table(tabla).select("*").execute().data
    except psycopg2.Error as e:
        return {"error": e.pgcode}


def foto(empresa):
    """Huella de todas las filas de una empresa en las tablas operativas."""
    partes = []
    for t in OPERATIVAS:
        filas = q(su, f"select * from public.{t} where id_empresa = %s order by 1", (empresa,))
        partes.append(json.dumps(filas, sort_keys=True, default=str))
    return hashlib.sha256("".join(partes).encode()).hexdigest()


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
M2 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_2_permisos.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_2.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(M1):
            cur.execute(open(a).read())
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
copia_produccion.cargar(su, RESPALDO)
with su.cursor() as cur:
    cur.execute(open(M1).read())
estructura_m1 = huellas_estructura()
COLS = {t: ", ".join(c["column_name"] for c in q(su, "select column_name from information_schema.columns where table_schema = 'public' "
                                                   "and table_name = %s order by ordinal_position", (t,)))
        for t in copia_produccion.TABLAS + ["empresa", "membresia"]}
antes = {t: huella(su, t, COLS[t]) for t in COLS}

with su.cursor() as cur:
    cur.execute(open(M2).read())
ok("la parte 2 se aplica sobre la copia de producción (con la parte 1)", True)
ok("ningún dato existente cambia (salvo el contador de la Empresa 1, que se inicializa)",
   {t: huella(su, t, COLS[t]) for t in COLS if t != "empresa"} == {t: v for t, v in antes.items() if t != "empresa"})
E1 = q(su, "select * from empresa where codigo = 'JSU'", uno=True)
ok("contador de la Empresa 1 = el global (próxima factura: 0001)", E1["ultimo_numero_factura"] == 0, E1["ultimo_numero_factura"])
q(su, localdb.RELOJ)

# ---------- cuentas de la Empresa 1 (copia de producción) ----------
uid = lambda mail: q(su, "select id from profiles where mail = %s", (mail,), True)["id"]
ADMIN1 = uid("profesora@prueba.invalid")
ALUMNA_DEMO = uid("alumna@prueba.invalid")
SOFIA = uid("sofia@demo.invalid")


def cuenta(mail, empresa, admin=False, profe=False, alumna=False):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].title(), "apellido": "Prueba", "id_empresa": empresa})))
    q(su, "update membresia set es_admin = %s, es_profesora = %s, es_alumna = %s where id_usuario = %s",
      (admin, profe, alumna, i))
    return i


PROF1B = cuenta("profe.b@prueba.invalid", E1["id"], profe=True)      # profesora NO administradora de la Empresa 1

# ---------- Empresa 2 con sus datos ----------
E2 = q(su, "insert into empresa (nombre, codigo) values ('Pilates Centro', 'PIL') returning *", uno=True)
ADMIN2 = cuenta("admin.pil@prueba.invalid", E2["id"], admin=True, profe=True)
PROF2 = cuenta("profe.pil@prueba.invalid", E2["id"], profe=True)
ALU2 = cuenta("alumna.pil@prueba.invalid", E2["id"], alumna=True)
q(su, "insert into membresia (id_usuario, id_empresa, es_alumna) values (%s, %s, true)", (ALUMNA_DEMO, E2["id"]))
t2 = q(su, "insert into tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max, id_empresa) "
           "values ('Gimnasia', 'Pilates', 50, 900, 6, %s) returning id", (E2["id"],), True)["id"]
h2 = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_empresa, id_profesora) "
           "values ('martes', '18:00', %s, true, %s, %s) returning id", (t2, E2["id"], PROF2), True)["id"]
c2 = q(su, "insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase, id_empresa) "
           "values (%s, '2026-11-24', '18:00', 0, 'programada', 50, 900, 6, %s, %s) returning id", (h2, t2, E2["id"]), True)["id"]
r2 = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
       (ALU2, c2, E2["id"]), True)["id"]
rd = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
       (ALUMNA_DEMO, c2, E2["id"]), True)["id"]
f2 = q(su, "insert into resumen_mensual (id_alumna, mes, anio, id_empresa) values (%s, 9, 2026, %s) returning id",
       (ALU2, E2["id"]), True)["id"]
ok("la Empresa 1 puede tener un tipo de clase con el mismo nombre que la Empresa 2 (únicos por empresa)", True)
F1 = q(su, "select id from resumen_mensual where id_empresa = %s order by id limit 1", (E1["id"],), True)["id"]
R1 = q(su, "select id from reserva where id_empresa = %s and estado = 'confirmada' order by id limit 1", (E1["id"],), True)["id"]
C1 = q(su, "select id_clase from reserva where id = %s", (R1,), True)["id_clase"]
H1 = q(su, "select id from horario where id_empresa = %s order by id limit 1", (E1["id"],), True)["id"]

AHORA = "2026-11-03 10:00-03"
CLI = {k: ClienteFalso(uri, v, ahora=AHORA) for k, v in
       (("admin1", ADMIN1), ("prof1b", PROF1B), ("sofia", SOFIA), ("demo", ALUMNA_DEMO), ("admin2", ADMIN2),
        ("prof2", PROF2), ("alu2", ALU2))}
SUPER = str(uuid.uuid4())
q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, 'superadmin@prueba.invalid', %s)",
  (SUPER, psycopg2.extras.Json({"sin_empresa": True})))
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
CLI["super"] = ClienteFalso(uri, SUPER, ahora=AHORA)

# ---------- lectura: cada una ve solo su empresa activa ----------
EMP = {"admin1": E1["id"], "prof1b": E1["id"], "sofia": E1["id"], "demo": E1["id"],
       "admin2": E2["id"], "prof2": E2["id"], "alu2": E2["id"]}
for quien, emp in EMP.items():
    filtradas = []
    for t in OPERATIVAS:
        filas = ver(CLI[quien], t)
        if isinstance(filas, dict) or any(f["id_empresa"] != emp for f in filas):
            filtradas.append(t)
    ok(f"{quien}: en las 13 tablas solo ve filas de su empresa", not filtradas, filtradas)
for t in OPERATIVAS:
    ok(f"superadministradora: no ve datos operativos ({t})", ver(CLI["super"], t) == [])
ok("superadministradora: ve la lista de empresas", len(ver(CLI["super"], "empresa")) == 2)
ok("una alumna ve solo sus empresas", {e["codigo"] for e in ver(CLI["alu2"], "empresa")} == {"PIL"}
   and {e["codigo"] for e in ver(CLI["demo"], "empresa")} == {"JSU", "PIL"})
ok("una profesora de la Empresa 2 no ve perfiles de la Empresa 1",
   all(p["id"] in (ADMIN2, PROF2, ALU2, ALUMNA_DEMO) for p in ver(CLI["prof2"], "profiles")))
ok("la profesora no administradora no ve facturas, pagos ni historial",
   all(ver(CLI["prof1b"], t) == [] for t in ("resumen_mensual", "pago", "factura_historial", "pago_anulacion", "mp_intento")))
ok("la profesora no administradora no ve reservas de clases de otra profesora", ver(CLI["prof1b"], "reserva") == [])
ok("la profesora de la Empresa 2 ve las reservas de su clase", {r["id"] for r in ver(CLI["prof2"], "reserva")} == {r2, rd})
ok("la administradora de la Empresa 2 ve su factura", [f["id"] for f in ver(CLI["admin2"], "resumen_mensual")] == [f2])

# alumna compartida: cambia de empresa
reservas_e1 = {r["id"] for r in ver(CLI["demo"], "reserva")}
q(su, "update profiles set empresa_activa = %s where id = %s", (E2["id"], ALUMNA_DEMO))
reservas_e2 = {r["id"] for r in ver(CLI["demo"], "reserva")}
ok("alumna compartida con la Empresa 2 activa: ve solo su reserva de la Empresa 2", reservas_e2 == {rd} and not (reservas_e1 & reservas_e2))
q(su, "update profiles set empresa_activa = %s where id = %s", (E1["id"], ALU2))
ok("empresa activa sin membresía (forzada): no ve nada", all(ver(CLI["alu2"], t) == [] for t in OPERATIVAS))
q(su, "update profiles set empresa_activa = %s where id = %s", (E2["id"], ALU2))
q(su, "update empresa set activa = false where id = %s", (E2["id"],))
ok("empresa desactivada: sus personas no ven nada", all(ver(CLI["admin2"], t) == [] for t in OPERATIVAS))
q(su, "update empresa set activa = true where id = %s", (E2["id"],))
q(su, "update profiles set empresa_activa = %s where id = %s", (E1["id"], ALUMNA_DEMO))

# ---------- escritura: nadie modifica otra empresa ----------
f_e1, f_e2 = foto(E1["id"]), foto(E2["id"])
ATAQUES_A_E2 = [
    ("admin1", "reservar", {"p_id_clase": c2}), ("sofia", "reservar", {"p_id_clase": c2}),
    ("sofia", "anotar_en_espera", {"p_id_clase": c2}), ("sofia", "cancelar_reserva", {"p_id_clase": c2}),
    ("admin1", "marcar_asistencia", {"p_id_reserva": r2, "p_estado": "presente"}),
    ("admin1", "crear_bloqueos_puntuales", {"p_ids_clase": [c2], "p_motivo": "ataque"}),
    ("admin1", "crear_bloqueo", {"p_tipo": "licencia", "p_desde": "2026-11-23", "p_hasta": "2026-11-25", "p_id_horario": h2, "p_motivo": "ataque"}),
    ("admin1", "anular_factura", {"p_id": f2, "p_motivo": "ataque", "p_tipo": "correccion"}),
    ("admin1", "confirmar_factura", {"p_id": f2, "p_version": 1}),
    ("admin1", "registrar_pago", {"p_id_resumen": f2, "p_monto": "1", "p_medio": "efectivo", "p_fecha": "2026-11-01"}),
    ("admin1", "cambiar_estado_tipo_clase", {"p_id": t2, "p_activo": False}),
    ("admin1", "cambiar_tipo_horario", {"p_id_horario": h2, "p_id_tipo": t2}),
    ("admin1", "actualizar_listas_espera", {"p_ids": [c2]}),
    ("demo", "crear_intento_mp", {"p_id_resumen": f2}),
]
for quien, fn, kw in ATAQUES_A_E2:
    intento(CLI[quien], fn, **kw)
ok(f"{len(ATAQUES_A_E2)} intentos desde la Empresa 1 con funciones sobre datos de la Empresa 2: no cambian nada",
   foto(E2["id"]) == f_e2)
with CLI["admin1"].conn.cursor() as cur:
    for sql, args in (("update public.horario set activo = false where id = %s", (h2,)),
                      ("update public.clase set cupo_max = 1 where id = %s", (c2,)),
                      ("delete from public.horario where id = %s", (h2,))):
        try:
            cur.execute(sql, args)
        except psycopg2.Error:
            pass
ok("escrituras directas de la Empresa 1 sobre tablas de la Empresa 2: no cambian nada", foto(E2["id"]) == f_e2)
ATAQUES_A_E1 = [
    ("alu2", "reservar", {"p_id_clase": C1}), ("alu2", "cancelar_reserva", {"p_id_clase": C1}),
    ("admin2", "marcar_asistencia", {"p_id_reserva": R1, "p_estado": "ausente"}),
    ("admin2", "crear_bloqueos_puntuales", {"p_ids_clase": [C1], "p_motivo": "ataque"}),
    ("admin2", "anular_factura", {"p_id": F1, "p_motivo": "ataque", "p_tipo": "correccion"}),
    ("admin2", "registrar_pago", {"p_id_resumen": F1, "p_monto": "800", "p_medio": "efectivo", "p_fecha": "2026-11-01"}),
    ("prof2", "cambiar_tipo_horario", {"p_id_horario": H1, "p_id_tipo": t2}),
    ("admin2", "actualizar_listas_espera", {"p_ids": [C1]}),
]
for quien, fn, kw in ATAQUES_A_E1:
    intento(CLI[quien], fn, **kw)
with CLI["admin2"].conn.cursor() as cur:
    try:
        cur.execute("update public.horario set activo = false where id = %s", (H1,))
    except psycopg2.Error:
        pass
ok(f"{len(ATAQUES_A_E1) + 1} intentos desde la Empresa 2 sobre datos de la Empresa 1: no cambian nada", foto(E1["id"]) == f_e1)

# protección general aunque una función tuviera un error: una conexión con permisos totales pero con la
# sesión de la administradora de la Empresa 1 (como dentro de una función security definer)
dentro = conectar(uri)
q(dentro, "select set_config('request.jwt.claim.sub', %s, false)", (ADMIN1,))
try:
    q(dentro, "update public.clase set cupo_max = 1 where id = %s", (c2,))
    ok("protección general: con sesión de la Empresa 1, modificar una fila de la Empresa 2 se rechaza", False)
except psycopg2.Error as e:
    ok("protección general: con sesión de la Empresa 1, modificar una fila de la Empresa 2 se rechaza", e.pgcode == "42501", e.pgcode)
try:
    q(dentro, "insert into public.reserva (id_alumna, id_clase, estado) values (%s, %s, 'confirmada')", (SOFIA, c2))
    ok("protección general: con sesión de la Empresa 1, crear una fila en la Empresa 2 se rechaza", False)
except psycopg2.Error as e:
    ok("protección general: con sesión de la Empresa 1, crear una fila en la Empresa 2 se rechaza", e.pgcode == "42501", e.pgcode)
dentro.close()
try:
    q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (SOFIA, c2, E1["id"]))
    ok("integridad: no puede existir una reserva de la Empresa 1 sobre una clase de la Empresa 2", False)
except psycopg2.Error as e:
    ok("integridad: no puede existir una reserva de la Empresa 1 sobre una clase de la Empresa 2", e.pgcode == "23503", e.pgcode)

# ---------- el código actual sigue funcionando en la Empresa 1 ----------
r = intento(CLI["admin1"], "marcar_asistencia", p_id_reserva=R1, p_estado="presente")
ok("administradora de la Empresa 1: funciones de profesora siguen respondiendo (sin error de permisos)", "error" not in r, r)
with CLI["prof1b"].conn.cursor() as cur:
    try:
        cur.execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo) values ('domingo', '07:00', "
                    "(select id from public.tipo_clase where id_empresa = %s limit 1), false) returning id_profesora", (E1["id"],))
        ok("la profesora no administradora crea un horario propio", cur.fetchone()[0] == PROF1B)
    except psycopg2.Error as e:
        ok("la profesora no administradora crea un horario propio", False, e.pgcode)
with CLI["prof1b"].conn.cursor() as cur:
    try:
        cur.execute("update public.horario set activo = false where id = %s", (H1,))
        ok("la profesora no administradora no puede modificar horarios de otra profesora", cur.rowcount == 0)
    except psycopg2.Error:
        ok("la profesora no administradora no puede modificar horarios de otra profesora", True)

# ---------- acceso sin sesión cerrado ----------
ANON = ClienteFalso(uri, None, rol="anon")
for t in ("horario", "clase", "reserva", "listadeespera"):
    ok(f"sin sesión: {t} cerrada", isinstance(ver(ANON, t), dict))

# ---------- contador de facturas por empresa ----------
n1 = q(su, "select public._siguiente_numero_factura(%s) n", (E1["id"],), True)["n"]
n2 = q(su, "select public._siguiente_numero_factura(%s) n", (E2["id"],), True)["n"]
ok("cada empresa numera desde su propio contador (Empresa 1 → 1, Empresa 2 → 1)", (n1, n2) == (1, 1), (n1, n2))
ok("el contador global ya no se usa (sigue igual)", q(su, "select last_value, is_called from factura_numero_seq", uno=True)
   == {"last_value": 1, "is_called": False})

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999998', 'multiempresa_2_permisos')")
try:
    with su.cursor() as cur:
        cur.execute(open(VUELTA).read())
    ok("vuelta atrás: se niega con dos empresas", False)
except psycopg2.Error:
    ok("vuelta atrás: se niega con dos empresas", True)
q(su, "rollback")  # el script abrió una transacción (begin) que quedó abortada
for t in reversed(OPERATIVAS):
    q(su, f"delete from public.{t} where id_empresa = %s", (E2["id"],))
q(su, "delete from horario where dia_semana = 'domingo' and hora_inicio = '07:00'")
q(su, "delete from membresia where id_empresa = %s", (E2["id"],))
for i in (PROF1B, ADMIN2, PROF2, ALU2, SUPER):
    q(su, "delete from profiles where id = %s", (i,))
    q(su, "delete from auth.users where id = %s", (i,))
q(su, "delete from empresa where id = %s", (E2["id"],))
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_2_permisos'", uno=True)["n"] == 0)
q(su, """create or replace function public._ahora()
returns timestamptz
language sql
stable
set search_path = ''
as $$
    select now();
$$;""")
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de la parte 1", final == estructura_m1,
   {k: (final.get(k), v) for k, v in estructura_m1.items() if final.get(k) != v})

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
