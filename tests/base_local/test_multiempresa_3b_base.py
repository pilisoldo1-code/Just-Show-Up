"""Multiempresa, parte 3b (acceso por nombre del lugar), en un Postgres LOCAL descartable que reconstruye
producción con una COPIA de sus datos (respaldo fuera del repo) y las partes 1 a 3. Prueba entrar_a_empresa
(nombres con mayúsculas, tildes, espacios y signos; lugar inexistente o desactivado; persona que no es del
lugar; membresía desactivada; superadministradora), nombres únicos, el código real de la sesión
(app.auth.entrar_a_empresa y usuario_de_acceso) con los permisos reales y la vuelta atrás. No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_3b_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import uuid
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
from app import auth  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]


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


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
M3B = [a for a in localdb.migraciones() if a.endswith("_multiempresa_3b_acceso.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_3b.sql")
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
        if os.path.basename(M1) <= os.path.basename(a) < os.path.basename(M3B):
            cur.execute(open(a).read())
estructura_m3 = huellas_estructura()
with su.cursor() as cur:
    cur.execute(open(M3B).read())
ok("la parte 3b se aplica sobre la copia de producción (con las partes 1 a 3)", True)


def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].replace(".", " ").title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADMIN1, DEMO, SOFIA = uid("profesora@prueba.invalid"), uid("alumna@prueba.invalid"), uid("sofia@demo.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
C = lambda i: ClienteFalso(uri, i)
S = C(SUPER)

# ---------- nombres únicos ----------
r = rpc(S, "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")
ok("superadmin: crea Pilates Centro", r.get("resultado") == "ok", r)
E2 = r["id"]
for nombre in ("just show up", "JUST SHOW UP", "Just  Show-Up", "Jüst Shów Up!", " just.show.up "):
    ok(f"no se puede crear otra empresa llamada «{nombre}»",
       rpc(S, "crear_empresa", p_nombre=nombre, p_codigo="OTRA").get("resultado") == "nombre_repetido")
ok("un nombre sin letras ni números no vale", rpc(S, "crear_empresa", p_nombre="-- !!", p_codigo="SIG").get("resultado") == "datos_invalidos")
ok("editar: no se puede renombrar Pilates Centro como «Just Show Up»",
   rpc(S, "editar_empresa", p_id=E2, p_nombre="Just Show Up", p_codigo="PIL") == "nombre_repetido")
ok("editar: sí se puede cambiar mayúsculas o espacios del propio nombre",
   rpc(S, "editar_empresa", p_id=E2, p_nombre="Pilates  CENTRO", p_codigo="PIL") == "ok")
rpc(S, "editar_empresa", p_id=E2, p_nombre="Pilates Centro", p_codigo="PIL")
try:
    q(su, "insert into empresa (nombre, codigo) values ('PILATES-centro', 'PIX')")
    ok("la base rechaza un nombre repetido aunque se inserte directo", False)
except psycopg2.Error:
    q(su, "rollback")
    ok("la base rechaza un nombre repetido aunque se inserte directo", True)
borradas = q(su, "select count(*) n from empresa", uno=True)["n"]
try:
    with S.conn.cursor() as cur:
        cur.execute("delete from public.empresa")
except psycopg2.Error:
    pass
ok("la superadministradora no puede borrar empresas", q(su, "select count(*) n from empresa", uno=True)["n"] == borradas)

# ---------- cuentas de la Empresa 2 ----------
ADMIN2 = cuenta("admin.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})
PROF2 = cuenta("profe.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "profesora"})
ALU2 = cuenta("alumna.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
rpc(C(ADMIN2), "asignar_membresia", p_id_usuario=DEMO, p_es_admin=False, p_es_profesora=False, p_es_alumna=True)

# como hace el servidor con una profesora nueva: cuenta con rol de empresa; si algo cambia profiles.rol, no asciende
q(su, "update profiles set rol = 'profesora' where id = %s", (PROF2,))
m = q(su, "select es_admin, es_profesora, es_alumna from membresia where id_usuario = %s", (PROF2,), True)
ok("una profesora creada con rol de empresa no pasa a administradora si cambia profiles.rol",
   (m["es_admin"], m["es_profesora"], m["es_alumna"]) == (False, True, False), m)
LEGADO = cuenta("legado@prueba.invalid")  # alta vieja: sin rol de empresa (script crear_profesora)
q(su, "update profiles set rol = 'profesora' where id = %s", (LEGADO,))
m = q(su, "select es_admin, es_profesora from membresia where id_usuario = %s", (LEGADO,), True)
ok("el alta vieja (script) sigue funcionando: administradora y profesora de la Empresa 1", m["es_admin"] and m["es_profesora"], m)

# ---------- entrar_a_empresa ----------
entrar = lambda i, lugar: rpc(C(i), "entrar_a_empresa", p_lugar=lugar)
NO = {"resultado": "no_autorizada"}
for lugar in ("Just Show Up", "just show up", "  JUST   show   UP ", "Just-Show Up", "Júst Show Up"):
    ok(f"alumna de la Empresa 1 entra escribiendo «{lugar}»", entrar(SOFIA, lugar) == {"resultado": "ok", "id": E1})
ok("lugar inexistente: no_autorizada", entrar(SOFIA, "Gimnasio Fantasma") == NO)
ok("lugar vacío o sin letras: no_autorizada", entrar(SOFIA, "") == NO and entrar(SOFIA, " - ") == NO)
ok("persona que no es del lugar: el mismo no_autorizada", entrar(SOFIA, "Pilates Centro") == NO)
ok("alumna en dos empresas: entra a la Empresa 1 con su nombre", entrar(DEMO, "just show up") == {"resultado": "ok", "id": E1})
ok("alumna en dos empresas: entra a la Empresa 2 con su nombre", entrar(DEMO, "pilates centro") == {"resultado": "ok", "id": E2})
ok("al entrar queda esa empresa activa", q(su, "select empresa_activa from profiles where id = %s", (DEMO,), True)["empresa_activa"] == E2)
ok("con la Empresa 2 activa solo ve clases de la Empresa 2",
   {c["id_empresa"] for c in C(DEMO).table("clase").select("id_empresa").execute().data} <= {E2})
ok("administradora y profesora de la Empresa 2 entran a su lugar",
   entrar(ADMIN2, "Pilates Centro")["resultado"] == "ok" and entrar(PROF2, "PILATES CENTRO")["resultado"] == "ok")
ok("administradora de la Empresa 2 no entra a la Empresa 1", entrar(ADMIN2, "Just Show Up") == NO)
ok("superadministradora no entra a ningún lugar", entrar(SUPER, "Just Show Up") == NO and entrar(SUPER, "Pilates Centro") == NO)
ok("sin sesión: no_autorizada", rpc(ClienteFalso(uri, None, rol="authenticated"), "entrar_a_empresa", p_lugar="Just Show Up") == NO)
ok("anon no ejecuta entrar_a_empresa", rpc(ClienteFalso(uri, None, rol="anon"), "entrar_a_empresa", p_lugar="Just Show Up") == {"error": "42501"})
q(su, "update membresia set activa = false where id_usuario = %s and id_empresa = %s", (ALU2, E2))
ok("membresía desactivada: no_autorizada", entrar(ALU2, "Pilates Centro") == NO)
q(su, "update membresia set activa = true where id_usuario = %s and id_empresa = %s", (ALU2, E2))
try:
    q(su, "update membresia set es_alumna = false where id_usuario = %s and id_empresa = %s", (ALU2, E2))
    ok("no puede existir una membresía sin ningún rol", False)
except psycopg2.Error:
    q(su, "rollback")
    ok("no puede existir una membresía sin ningún rol", True)
ok("superadmin: desactiva la Empresa 2", rpc(S, "cambiar_estado_empresa", p_id=E2, p_activa=False) == "ok")
ok("empresa desactivada: nadie entra (mismo no_autorizada)", entrar(ALU2, "Pilates Centro") == NO and entrar(ADMIN2, "Pilates Centro") == NO)
ok("empresa desactivada: la alumna compartida sigue entrando a la Empresa 1", entrar(DEMO, "Just Show Up")["resultado"] == "ok")
ok("superadmin: reactiva la Empresa 2", rpc(S, "cambiar_estado_empresa", p_id=E2, p_activa=True) == "ok"
   and entrar(ALU2, "Pilates Centro")["resultado"] == "ok")
ok("la superadministradora sigue viendo solo el resumen (sin datos operativos)",
   S.table("reserva").select("id").execute().data == [] and S.table("resumen_mensual").select("id").execute().data == []
   and S.table("asistencia").select("id").execute().data == [] and S.table("pago").select("id").execute().data == [])


# ---------- código real de la sesión (app.auth) contra la base ----------
with mock.patch.object(auth, "cliente_con_token", side_effect=lambda t: C(t)):
    ok("app.auth.entrar_a_empresa: id de la empresa o None",
       auth.entrar_a_empresa(DEMO, "Pilates centro") == E2 and auth.entrar_a_empresa(SOFIA, "Pilates Centro") is None)
# la carga de la sesión en el servidor (app.auth.usuario_de_acceso) usa la parte 3c: se prueba allí

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999995', 'multiempresa_3b_acceso')")
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
q(su, "update profiles set empresa_activa = %s where id in (%s, %s)", (E1, DEMO, SOFIA))
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_3b_acceso'", uno=True)["n"] == 0)
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de la parte 3", final == estructura_m3,
   {k: (final.get(k), v) for k, v in estructura_m3.items() if final.get(k) != v})

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
