"""Multiempresa, parte 1 (estructura), en un Postgres LOCAL descartable que reconstruye producción con
todas las migraciones anteriores y una COPIA de los datos de producción tomada del respaldo (datos.json,
fuera del repo; la base local se borra al terminar). Comprueba: estructura igual a producción antes de
migrar, todos los datos intactos, todo asignado a la Empresa 1, membresías y profesora asignada,
filas nuevas completadas solas (cuentas, horarios, clases, reservas, facturas), vuelta atrás exacta.
No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_1_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import json
import os
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
TABLAS = ["profiles", "tipo_clase", "horario", "clase", "bloqueo", "reserva", "listadeespera", "asistencia",
          "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion",
          "mp_configuracion", "mp_intento", "mp_evento"]


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def rpc(c, f, **kw):
    return c.rpc(f, kw).execute().data


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


def columnas(t):
    return [c["column_name"] for c in q(su, "select column_name from information_schema.columns where table_schema = 'public' "
                                            "and table_name = %s order by ordinal_position", (t,))]


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
NUEVA = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")]
assert len(NUEVA) == 1, NUEVA
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_1.sql")
ANTERIORES = [a for a in localdb.migraciones() if os.path.basename(a) < os.path.basename(NUEVA[0])]
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in ANTERIORES:
        cur.execute(open(a).read())
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
prod = json.load(open(os.path.join(RESPALDO, "huellas_produccion.json")))
local = huellas_estructura()
ok("antes de migrar: la estructura local es idéntica a la de producción", local == prod,
   {k: (local.get(k), v) for k, v in prod.items() if local.get(k) != v})

# ---------- copia de los datos de producción ----------
datos = json.load(open(os.path.join(RESPALDO, "datos.json")))
with su.cursor() as cur:
    cur.execute("set session_replication_role = replica")
    for u in datos["auth_users"]:
        cur.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
                    (u["id"], u["email"], psycopg2.extras.Json(u.get("user_metadata") or {})))
    for t in reversed(TABLAS):  # datos iniciales que cargan las migraciones: los reemplaza la copia
        cur.execute(f"delete from public.{t}")
    for t in TABLAS:
        for fila in datos[t]:
            cols = list(fila)
            vals = [psycopg2.extras.Json(v) if isinstance(v, (dict, list)) else v for v in fila.values()]
            cur.execute(f"insert into public.{t} ({', '.join(cols)}) overriding system value values ({', '.join(['%s'] * len(cols))})", vals)
    cur.execute("set session_replication_role = origin")
    for t in TABLAS:
        cur.execute("""select a.attname from pg_attribute a where a.attrelid = %s::regclass and a.attidentity <> ''""", (f"public.{t}",))
        for (col,) in cur.fetchall():
            cur.execute(f"select setval(pg_get_serial_sequence('public.{t}', '{col}'), coalesce((select max({col}) from public.{t}), 0) + 1, false)")
ok("copia de producción cargada", all(len(datos[t]) == q(su, f"select count(*) n from public.{t}", uno=True)["n"] for t in TABLAS))
q(su, "select setval('public.factura_numero_seq', 1, false)")  # como producción el 30/10

COLS = {t: columnas(t) for t in TABLAS}
antes = {t: huella(su, t, ", ".join(COLS[t])) for t in TABLAS}
sec_antes = q(su, "select last_value, is_called from factura_numero_seq", uno=True)

# ---------- aplicar la parte 1 ----------
with su.cursor() as cur:
    cur.execute(open(NUEVA[0]).read())
ok("la migración se aplica sobre la copia de producción", True)
despues = {t: huella(su, t, ", ".join(COLS[t])) for t in TABLAS}
ok("ningún dato existente cambia (todas las columnas anteriores de las 16 tablas)", despues == antes,
   [t for t in TABLAS if antes[t] != despues[t]])
ok("el contador de facturas no cambia", q(su, "select last_value, is_called from factura_numero_seq", uno=True) == sec_antes)
E1 = q(su, "select * from empresa", uno=True)
ok("existe una sola empresa: «Just Show Up», código JSU, activa, Mercado Pago habilitado",
   q(su, "select count(*) n from empresa", uno=True)["n"] == 1 and E1["nombre"] == "Just Show Up" and E1["codigo"] == "JSU"
   and E1["activa"] and E1["mp_habilitado"])
for t in TABLAS[1:]:
    if t in ("mp_configuracion",):
        continue
    sin = q(su, f"select count(*) n from public.{t} where id_empresa is distinct from %s", (E1["id"],), True)["n"]
    ok(f"{t}: todas las filas son de la Empresa 1", sin == 0, sin)
perfiles = q(su, "select id, rol from profiles")
m = {r["id_usuario"]: r for r in q(su, "select * from membresia")}
ok("una membresía por cuenta, en la Empresa 1", len(m) == len(perfiles) and all(x["id_empresa"] == E1["id"] for x in m.values()))
ok("profesora → administradora y profesora; alumna → alumna",
   all((m[p["id"]]["es_admin"], m[p["id"]]["es_profesora"], m[p["id"]]["es_alumna"]) ==
       ((True, True, False) if p["rol"] == "profesora" else (False, False, True)) for p in perfiles))
ok("todas las cuentas tienen la Empresa 1 como empresa activa",
   q(su, "select count(*) n from profiles where empresa_activa is distinct from %s", (E1["id"],), True)["n"] == 0)
ok("nadie es superadministradora todavía", q(su, "select count(*) n from profiles where es_superadmin", uno=True)["n"] == 0)
PROFE = q(su, "select id from profiles where rol = 'profesora' order by id limit 1", uno=True)["id"]
ok("todos los horarios quedan asignados a la profesora actual",
   q(su, "select count(*) n from horario where id_profesora is distinct from %s", (PROFE,), True)["n"] == 0)
ok("cada clase tiene la profesora de su horario",
   q(su, "select count(*) n from clase c join horario h on h.id = c.id_horario where c.id_profesora is distinct from h.id_profesora", uno=True)["n"] == 0)
ok("las líneas de factura existentes muestran la profesora",
   q(su, "select count(*) n from resumen_mensual_detalle where id_profesora is null or profesora_nombre is null", uno=True)["n"] == 0)

# ---------- el código actual sigue funcionando: filas nuevas completadas solas ----------
NUEVA_ALUMNA = str(uuid.uuid4())
q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, 'nueva@prueba.invalid', %s)",
  (NUEVA_ALUMNA, psycopg2.extras.Json({"nombre": "Nueva", "apellido": "Prueba"})))
mn = q(su, "select * from membresia where id_usuario = %s", (NUEVA_ALUMNA,), True)
ok("cuenta nueva creada como hoy (sin empresa en los metadatos) → alumna de la Empresa 1, empresa activa",
   mn and mn["id_empresa"] == E1["id"] and mn["es_alumna"] and not mn["es_admin"]
   and q(su, "select empresa_activa from profiles where id = %s", (NUEVA_ALUMNA,), True)["empresa_activa"] == E1["id"])
NUEVA_PROFE = str(uuid.uuid4())
q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, 'profe.nueva@prueba.invalid', '{}')", (NUEVA_PROFE,))
q(su, "update profiles set rol = 'profesora' where id = %s", (NUEVA_PROFE,))
mp_ = q(su, "select * from membresia where id_usuario = %s", (NUEVA_PROFE,), True)
ok("alta de profesora como hace hoy el script (crear y cambiar rol) → administradora y profesora de la Empresa 1",
   mp_["es_admin"] and mp_["es_profesora"] and not mp_["es_alumna"])
SUPER = str(uuid.uuid4())
q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, 'superadmin@prueba.invalid', %s)",
  (SUPER, psycopg2.extras.Json({"sin_empresa": True})))
ok("cuenta marcada sin empresa → sin membresía ni empresa activa",
   q(su, "select count(*) n from membresia where id_usuario = %s", (SUPER,), True)["n"] == 0
   and q(su, "select empresa_activa from profiles where id = %s", (SUPER,), True)["empresa_activa"] is None)

MAX_CLASE = q(su, "select coalesce(max(id), 0) m from clase", uno=True)["m"]
P = ClienteFalso(uri, PROFE)
gim = q(su, "select id from tipo_clase order by id limit 1", uno=True)["id"]
P.conn.cursor().execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo) values ('sabado', '06:00', %s, true)", (gim,))
h = q(su, "select * from horario where dia_semana = 'sabado' and hora_inicio = '06:00'", uno=True)
ok("horario creado por la profesora (como hoy) → Empresa 1 y asignado a ella", h["id_empresa"] == E1["id"] and h["id_profesora"] == PROFE)
r = rpc(P, "generar_clases", p_lunes="2026-11-16")
c = q(su, "select * from clase where id_horario = %s", (h["id"],), True)
ok("generar_clases (como hoy) → la clase nueva es de la Empresa 1 y de la profesora del horario",
   c and c["id_empresa"] == E1["id"] and c["id_profesora"] == PROFE, r)
A = ClienteFalso(uri, NUEVA_ALUMNA, ahora="2026-11-10 10:00-03")
q(su, localdb.RELOJ)
r = rpc(A, "reservar", p_id_clase=c["id"])
res = q(su, "select * from reserva where id_alumna = %s", (NUEVA_ALUMNA,), True)
ok("reservar (como hoy) → reserva de la Empresa 1", res and res["id_empresa"] == E1["id"], r)
t1 = q(su, "insert into public.tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max) values ('Script', 'x', 60, 1, 5) returning *", uno=True)
ok("con una sola empresa, una fila creada sin sesión (scripts actuales) va a la Empresa 1", t1["id_empresa"] == E1["id"])
q(su, "delete from tipo_clase where id = %s", (t1["id"],))
q(su, "insert into empresa (nombre, codigo) values ('Otra', 'OTR')")
try:
    q(su, "insert into public.tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max) values ('Sin empresa', 'x', 60, 1, 5)")
    ok("con dos empresas, una fila sin sesión ni empresa se rechaza (nunca queda 'sin empresa')", False)
except psycopg2.Error as e:
    ok("con dos empresas, una fila sin sesión ni empresa se rechaza (nunca queda 'sin empresa')", e.pgcode == "23502", e.pgcode)
q(su, "delete from empresa where codigo = 'OTR'")

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999999', 'multiempresa_1_estructura')")
# las filas creadas en esta prueba se borran antes, como en producción no habría ninguna nueva
for sql in ("delete from reserva where id_alumna = %s", ):
    q(su, sql, (NUEVA_ALUMNA,))
q(su, "delete from clase where id > %s", (MAX_CLASE,))  # generar_clases crea la semana de todos los horarios
q(su, "delete from horario where id = %s", (h["id"],))
q(su, "delete from profiles where id in (%s, %s, %s)", (NUEVA_ALUMNA, NUEVA_PROFE, SUPER))
q(su, "delete from auth.users where id in (%s, %s, %s)", (NUEVA_ALUMNA, NUEVA_PROFE, SUPER))
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: los datos quedan idénticos a la copia de producción",
   {t: huella(su, t, ", ".join(COLS[t])) for t in TABLAS} == antes)
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_1_estructura'", uno=True)["n"] == 0)
q(su, """create or replace function public._ahora()
returns timestamptz
language sql
stable
set search_path = ''
as $$
    select now();
$$;""")
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de producción", final == prod,
   {k: (final.get(k), v) for k, v in prod.items() if final.get(k) != v})
with su.cursor() as cur:
    cur.execute(open(NUEVA[0]).read())
ok("después de la vuelta atrás, la migración se puede volver a aplicar", q(su, "select count(*) n from empresa", uno=True)["n"] == 1)

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
