"""Multiempresa, parte 4 (datos personales protegidos), en un Postgres LOCAL descartable que reconstruye
producción con una COPIA de sus datos (respaldo fuera del repo) y las partes 1 a 3. Comprueba que la cédula,
la fecha de nacimiento y el carné solo los ven la persona y la administradora de su empresa, que el código
de la app (app.personas, app.facturas) los sigue mostrando a quien corresponde y la vuelta atrás.
No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_4_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
from app import facturas, personas  # noqa: E402

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


def leer(cli, columnas, ids):
    try:
        return cli.table("profiles").select(columnas).in_("id", ids).execute().data
    except psycopg2.Error as e:
        cli.conn.rollback()
        return {"error": e.pgcode}


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG4 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_4_datos_personales.sql")][0]
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_4.sql")
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
        if os.path.basename(M1) <= os.path.basename(a) < os.path.basename(MIG4):
            cur.execute(open(a).read())
estructura_m3 = huellas_estructura()


def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].replace(".", " ").title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id from profiles where mail = %s", (mail,), True)["id"]
ADMIN1, DEMO, SOFIA = uid("profesora@prueba.invalid"), uid("alumna@prueba.invalid"), uid("sofia@demo.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
C = lambda i: ClienteFalso(uri, i)
E2 = rpc(C(SUPER), "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")["id"]
ADMIN2 = cuenta("admin.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})
PROF2 = cuenta("profe.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "profesora"})
ALU2 = cuenta("alumna.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
rpc(C(ADMIN2), "asignar_membresia", p_id_usuario=DEMO, p_es_admin=False, p_es_profesora=False, p_es_alumna=True)
q(su, "update profiles set cedula = '12345672', fecha_nacimiento = '1990-05-01' where id = %s", (ALU2,))
q(su, "update profiles set cedula = '11111111' where id = %s", (SOFIA,))
cedula_demo = q(su, "select cedula from profiles where id = %s", (DEMO,), True)["cedula"]

P2 = C(PROF2)
ok("antes de la parte 4: la profesora no administradora todavía puede leer la cédula (lo que se corrige)",
   leer(P2, "id, cedula", [ALU2]) == [{"id": ALU2, "cedula": "12345672"}])

E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
cedulas_antes = {r["id"]: r["cedula"] for r in q(su, "select id::text, cedula from profiles where cedula is not null")}
with su.cursor() as cur:
    cur.execute(open(MIG4).read())
ok("la parte 4 se aplica sobre la copia de producción (con las partes 1 a 3c)", True)
copiadas = {r["id"]: r["cedula"] for r in q(su, "select id_usuario::text id, cedula from membresia where cedula is not null and id_empresa = %s", (E1,))}
ok("las cédulas actuales pasan a la Empresa 1 (solo las de personas de la Empresa 1)",
   copiadas == {k: v for k, v in cedulas_antes.items() if k != ALU2} and SOFIA in copiadas, (copiadas, cedulas_antes))
ok("ninguna cédula se copia a la Empresa 2", q(su, "select count(*) n from membresia where id_empresa = %s and cedula is not null", (E2,), True)["n"] == 0)
A2 = C(ADMIN2)
ok("administradora de la Empresa 2: carga la cédula de su alumna", rpc(A2, "actualizar_cedula", p_id_alumna=ALU2, p_cedula="1.234.567-2") == "ok")
ok("la cédula de SOFIA (Empresa 1) se puede usar en la Empresa 2 sin aviso: no revela que existe en otro lugar",
   rpc(A2, "cedula_disponible", p_cedula="11111111") is True
   and rpc(A2, "actualizar_cedula", p_id_alumna=DEMO, p_cedula="11111111") == "ok")
ok("repetida dentro de la misma empresa sí se rechaza",
   rpc(A2, "cedula_disponible", p_cedula="11111111") is False
   and rpc(A2, "actualizar_cedula", p_id_alumna=ALU2, p_cedula="11111111") == "cedula_repetida")
cedula_demo = "11111111"
ok("una persona puede tener la cédula registrada distinta en cada empresa (sin duplicar la cuenta)",
   q(su, "select count(distinct id_usuario) n from membresia where id_usuario = %s", (DEMO,), True)["n"] == 1)
ok("la administradora de la Empresa 1 no puede cargar cédulas a personas de la Empresa 2",
   rpc(C(ADMIN1), "actualizar_cedula", p_id_alumna=ALU2, p_cedula="12345672") == "no_existe")
ok("una profesora no administradora no carga cédulas ni consulta disponibilidad",
   rpc(C(PROF2), "actualizar_cedula", p_id_alumna=ALU2, p_cedula="") == "no_autorizada"
   and rpc(C(PROF2), "cedula_disponible", p_cedula="12345672") is False)
for nombre, i in (("profesora", PROF2), ("administradora", ADMIN2), ("alumna", ALU2)):
    try:
        C(i).table("membresia").select("id_usuario, cedula").execute()
        ok(f"{nombre}: no lee la cédula de membresia directo", False)
    except psycopg2.Error as e:
        ok(f"{nombre}: no lee la cédula de membresia directo", e.pgcode == "42501")
ok("las docentes siguen leyendo las membresías de su empresa (sin cédula)",
   len(C(PROF2).table("membresia").select("id_usuario, es_alumna").execute().data) >= 3)

# ---------- lectura directa ----------
P2, AL2, A1, DE = C(PROF2), C(ALU2), C(ADMIN1), C(DEMO)
for nombre, cli in (("profesora", P2), ("administradora", A2), ("alumna", AL2)):
    for col in personas.PRIVADOS:
        ok(f"{nombre}: no lee {col} directo de profiles", leer(cli, f"id, {col}", [ALU2]) == {"error": "42501"})
ok("profesora: sigue leyendo nombre y contacto de su empresa",
   {f["id"] for f in leer(P2, "id, nombre, apellido, mail, telefono", [ALU2, DEMO, ADMIN2])} == {ALU2, DEMO, ADMIN2})
ok("profesora: no ve personas de otra empresa", leer(P2, "id, nombre", [SOFIA, ADMIN1]) == [])
ok("la sesión sigue leyendo sus columnas de siempre (login)",
   len(leer(AL2, "nombre, apellido, mail, rol, es_superadmin, empresa_activa", [ALU2])) == 1)
ok("anon no lee profiles", leer(ClienteFalso(uri, None, rol="anon"), "id, nombre", [ALU2]) == {"error": "42501"})

# ---------- datos_personales ----------
dp = lambda cli, ids: {f["id"]: f for f in rpc(cli, "datos_personales", p_ids=ids)}
ok("administradora: ve la cédula de las personas de su empresa",
   dp(A2, [ALU2, DEMO])[ALU2]["cedula"] == "12345672" and dp(A2, [ALU2, DEMO])[DEMO]["cedula"] == cedula_demo)
ok("administradora: no ve la de personas de otra empresa", dp(A2, [SOFIA, ADMIN1]) == {})
ok("administradora de la Empresa 1: no ve a la alumna de la Empresa 2", dp(A1, [ALU2]) == {})
ok("profesora no administradora: no ve datos personales ajenos", dp(P2, [ALU2, DEMO, ADMIN2]) == {})
ok("profesora: ve los suyos", list(dp(P2, [PROF2, ALU2])) == [PROF2])
ok("alumna: solo los suyos", list(dp(AL2, [ALU2, DEMO, PROF2])) == [ALU2] and dp(AL2, [ALU2])[ALU2]["fecha_nacimiento"] is not None)
ok("anon no ejecuta datos_personales", rpc(ClienteFalso(uri, None, rol="anon"), "datos_personales", p_ids=[ALU2]) == {"error": "42501"})
q(su, "update membresia set activa = false where id_usuario = %s and id_empresa = %s", (DEMO, E2))
ok("administradora: conserva la cédula de una alumna que dejó la empresa (facturas viejas)",
   dp(A2, [DEMO]).get(DEMO, {}).get("cedula") == "11111111")
q(su, "update membresia set activa = true where id_usuario = %s and id_empresa = %s", (DEMO, E2))
rpc(DE, "elegir_empresa", p_id_empresa=E2)
ok("la alumna en dos empresas ve la cédula registrada en la empresa de su sesión",
   dp(C(DEMO), [DEMO])[DEMO]["cedula"] == "11111111" and dp(C(DEMO), [DEMO])[DEMO]["cedula"] is not None)

# ---------- empresa solo por sesión (sin respaldo por perfil) ----------
NUEVO = ClienteFalso(uri, SOFIA, sesion=uuid.uuid4())
ok("después de la parte 4, una sesión sin lugar elegido no ve nada",
   NUEVO.table("clase").select("id").execute().data == [] and rpc(NUEVO, "empresa_activa") is None)
ok("al entrar con el nombre del lugar, ve su empresa", rpc(NUEVO, "entrar_a_empresa", p_lugar="Just Show Up")["resultado"] == "ok"
   and {c["id_empresa"] for c in NUEVO.table("clase").select("id_empresa").execute().data} == {E1})

# ---------- nombres de profesoras ----------
nom = lambda cli: rpc(cli, "profesoras_empresa")
ok("alumna: ve solo el nombre de las profesoras de su empresa",
   {f["id"] for f in nom(AL2)} == {ADMIN2, PROF2} and set(nom(AL2)[0]) == {"id", "nombre"})
ok("alumna de la Empresa 1: no ve profesoras de la Empresa 2", {f["id"] for f in nom(C(SOFIA))} == {ADMIN1})
ok("alumna en dos empresas: ve las de su empresa activa", {f["id"] for f in nom(C(DEMO))} == {ADMIN2, PROF2})
ok("anon no ejecuta profesoras_empresa", nom(ClienteFalso(uri, None, rol="anon")) == {"error": "42501"})
ok("app.personas: nombres de profesoras", personas.nombres_profesoras(AL2) == {f["id"]: f["nombre"] for f in nom(AL2)})

# ---------- código de la app ----------
ok("app.personas: la administradora completa la lista con la cédula",
   personas.completar(A2, [{"id": ALU2}, {"id": PROF2}])[0]["cedula"] == "12345672")
ok("app.personas: la profesora no obtiene datos personales", personas.datos_personales(P2, [ALU2]) == {})
ok("app.facturas: la administradora ve nombre y cédula para la factura",
   facturas._perfiles(A2, [ALU2])[ALU2]["cedula"] == "12345672")
ok("app.facturas: la alumna ve su nombre y cédula", facturas._perfiles(AL2, [ALU2])[ALU2]["cedula"] == "12345672")

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999996', 'multiempresa_4_datos_personales')")
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_4_datos_personales'", uno=True)["n"] == 0)
ok("vuelta atrás: el código de la app sigue funcionando sin las funciones",
   facturas._perfiles(C(ADMIN2), [ALU2])[ALU2]["cedula"] == "12345672" and personas.nombres_profesoras(C(ALU2)) == {})
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de la parte 3", final == estructura_m3,
   {k: (final.get(k), v) for k, v in estructura_m3.items() if final.get(k) != v})
permisos = q(su, "select privilege_type from information_schema.role_table_grants where table_name = 'profiles' and grantee = 'authenticated'")
ok("vuelta atrás: authenticated vuelve a tener lectura de la tabla completa", {"SELECT"} <= {p["privilege_type"] for p in permisos})

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
