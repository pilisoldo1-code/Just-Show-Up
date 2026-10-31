"""Multiempresa, parte 3c (la empresa es de cada sesión), en un Postgres LOCAL descartable con la COPIA de
producción y las partes 1 a 3b. Simula dispositivos con sesiones distintas (session_id del token, como
Supabase): una persona en Just Show Up en un dispositivo y en Pilates Centro en otro, sin que una sesión
cambie la otra. También: cerrar sesión, intentos de usar o pisar la sesión de otra persona, sesiones del
código anterior (respaldo por perfil), el código real de app.auth y la vuelta atrás. No toca Supabase.

Uso:  python tests/base_local/test_multiempresa_3c_base.py "<carpeta del respaldo antes de multiempresa>"
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
M3C = [a for a in localdb.migraciones() if a.endswith("_multiempresa_3c_sesiones.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_multiempresa_3c.sql")
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
        if os.path.basename(M1) <= os.path.basename(a) < os.path.basename(M3C):
            cur.execute(open(a).read())


def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].replace(".", " ").title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
DEMO, SOFIA = uid("alumna@prueba.invalid"), uid("sofia@demo.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
E2 = ClienteFalso(uri, SUPER).rpc("crear_empresa", {"p_nombre": "Pilates Centro", "p_codigo": "PIL"}).execute().data["id"]
ADMIN2 = cuenta("admin.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})
ClienteFalso(uri, ADMIN2).rpc("asignar_membresia", {"p_id_usuario": DEMO, "p_es_admin": False, "p_es_profesora": False,
                                                    "p_es_alumna": True}).execute()
# clases y reservas de las dos empresas para ver qué ve cada sesión
T2 = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, activo, id_empresa) "
           "values ('Pilates', 50, 900, 6, true, %s) returning id", (E2,), True)["id"]
H2 = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
           "values ('martes', '18:00', %s, true, %s, %s) returning id", (T2, ADMIN2, E2), True)["id"]
C2 = q(su, "insert into clase (id_horario, fecha, hora_inicio, duracion, precio, cupo_max, id_tipo_clase, estado, id_profesora, id_empresa) "
           "values (%s, '2026-11-03', '18:00', 50, 900, 6, %s, 'programada', %s, %s) returning id", (H2, T2, ADMIN2, E2), True)["id"]
q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (DEMO, C2, E2))
estructura_3b = huellas_estructura()
with su.cursor() as cur:
    cur.execute(open(M3C).read())
ok("la parte 3c se aplica sobre la copia de producción (con las partes 1 a 3b)", True)

empresas = lambda cli, t: {f["id_empresa"] for f in cli.table(t).select("id_empresa").execute().data}

# ---------- dos dispositivos ----------
COMPU, CEL = uuid.uuid4(), uuid.uuid4()
A = ClienteFalso(uri, DEMO, sesion=COMPU)
B = ClienteFalso(uri, DEMO, sesion=CEL)
ok("dispositivo nuevo sin lugar elegido: para el servidor no tiene lugar (vuelve a iniciar sesión)",
   rpc(A, "empresa_de_sesion") is None)
ok("hasta la parte 4, la base usa el perfil como respaldo (solo su propia empresa, para el código anterior)",
   empresas(A, "clase") <= {E1})
ok("computadora: entra a Just Show Up", rpc(A, "entrar_a_empresa", p_lugar="Just Show Up") == {"resultado": "ok", "id": E1})
ok("celular: entra a Pilates Centro", rpc(B, "entrar_a_empresa", p_lugar="pilates centro") == {"resultado": "ok", "id": E2})
ok("la computadora sigue en Just Show Up (el celular no la cambió)",
   rpc(A, "empresa_de_sesion") == E1 and empresas(A, "clase") == {E1} and empresas(A, "reserva") <= {E1})
ok("el celular ve solo Pilates Centro", empresas(B, "clase") == {E2} and empresas(B, "reserva") == {E2})
ok("la computadora no ve la reserva de Pilates Centro",
   C2 not in {r["id_clase"] for r in A.table("reserva").select("id_clase").execute().data})
ok("entrar no modifica el perfil (no hay empresa compartida entre dispositivos)",
   q(su, "select empresa_activa from profiles where id = %s", (DEMO,), True)["empresa_activa"] == E1)
ok("después de que el celular vuelve a entrar, la computadora sigue igual",
   rpc(B, "entrar_a_empresa", p_lugar="Pilates Centro")["resultado"] == "ok" and rpc(A, "empresa_de_sesion") == E1)
B2 = ClienteFalso(uri, DEMO, sesion=CEL)  # otro pedido de la misma sesión (p. ej. después de renovar el token)
ok("la misma sesión conserva su lugar en pedidos siguientes", rpc(B2, "empresa_de_sesion") == E2)

# ---------- cerrar sesión ----------
rpc(B, "salir_de_empresa")
ok("cerrar sesión en el celular lo deja sin lugar", rpc(B, "empresa_de_sesion") is None)
ok("cerrar sesión en el celular no afecta la computadora", rpc(A, "empresa_de_sesion") == E1)
VIEJA = uuid.uuid4()
V = ClienteFalso(uri, DEMO, sesion=VIEJA)
rpc(V, "entrar_a_empresa", p_lugar="Pilates Centro")
q(su, "delete from auth.sessions where id = %s", (str(VIEJA),))  # Supabase borra la sesión al cerrarla
rpc(A, "entrar_a_empresa", p_lugar="Just Show Up")
ok("al entrar se limpian las sesiones que ya no existen",
   q(su, "select count(*) n from sesion_empresa where session_id = %s", (str(VIEJA),), True)["n"] == 0
   and q(su, "select count(*) n from sesion_empresa where session_id = %s", (str(COMPU),), True)["n"] == 1)

# ---------- intentos indebidos ----------
S = ClienteFalso(uri, SOFIA, sesion=uuid.uuid4())
ok("una sesión de otra persona no puede pisar el lugar de esta sesión",
   ClienteFalso(uri, SOFIA, sesion=COMPU).rpc("entrar_a_empresa", {"p_lugar": "Just Show Up"}).execute().data["resultado"] == "ok"
   and q(su, "select id_usuario::text u, id_empresa from sesion_empresa where session_id = %s", (str(COMPU),), True)
   == {"u": DEMO, "id_empresa": E1})
ok("con el session_id de otra persona no se obtiene su lugar",
   rpc(ClienteFalso(uri, SOFIA, sesion=COMPU), "empresa_de_sesion") is None)
ok("elegir una empresa ajena por la función: no_autorizada", rpc(S, "elegir_empresa", p_id_empresa=E2) == "no_autorizada")
for accion, sql in (("leer", "select * from public.sesion_empresa"),
                    ("escribir", f"insert into public.sesion_empresa values ('{uuid.uuid4()}', '{SOFIA}', {E2})")):
    try:
        with S.conn.cursor() as cur:
            cur.execute(sql)
        ok(f"nadie puede {accion} sesion_empresa directo", False)
    except psycopg2.Error as e:
        ok(f"nadie puede {accion} sesion_empresa directo", e.pgcode == "42501")
sin_sesion = ClienteFalso(uri, None)
with sin_sesion.conn.cursor() as cur:
    cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (SOFIA,))
    cur.execute("select set_config('request.jwt.claims', %s, false)", ('{"sub": "%s"}' % SOFIA,))
ok("un token sin session_id no puede entrar a un lugar", rpc(sin_sesion, "entrar_a_empresa", p_lugar="Just Show Up")
   == {"resultado": "no_autorizada"})
q(su, "update membresia set activa = false where id_usuario = %s and id_empresa = %s", (DEMO, E2))
B3 = ClienteFalso(uri, DEMO, sesion=uuid.uuid4())
rpc(B3, "elegir_empresa", p_id_empresa=E1)
q(su, "insert into sesion_empresa (session_id, id_usuario, id_empresa) values (%s, %s, %s)", (str(uuid.uuid4()), DEMO, E2))
ok("membresía desactivada: una sesión que estaba en ese lugar deja de ver", rpc(ClienteFalso(uri, DEMO, sesion=CEL), "empresa_de_sesion") is None)
q(su, "update membresia set activa = true where id_usuario = %s and id_empresa = %s", (DEMO, E2))

# ---------- sesiones del código anterior ----------
LEG = ClienteFalso(uri, SOFIA, sesion=uuid.uuid4())
q(su, "delete from sesion_empresa where session_id = %s", (LEG.sesion,))
ok("sesión del código anterior (sin lugar guardado): sigue viendo su empresa del perfil",
   empresas(LEG, "clase") == {E1} and rpc(LEG, "empresa_de_sesion") is None)


# ---------- código real de la sesión (app.auth) ----------
class ClienteSesion(ClienteFalso):
    def __init__(self, uid_, sesion):
        super().__init__(uri, uid_, sesion=sesion)
        self.auth = mock.MagicMock()
        self.auth.get_user.return_value.user.id = uid_
        self.postgrest = mock.MagicMock()


def usuario(uid_, sesion):
    with mock.patch.object(auth, "cliente_nuevo", return_value=ClienteSesion(uid_, sesion)):
        return auth.usuario_de_acceso("token")


rpc(ClienteFalso(uri, DEMO, sesion=CEL), "entrar_a_empresa", p_lugar="Pilates Centro")
ua, ub = usuario(DEMO, COMPU), usuario(DEMO, CEL)
ok("app.auth: cada dispositivo carga su lugar", (ua.empresa_nombre, ub.empresa_nombre) == ("Just Show Up", "Pilates Centro"))
ok("app.auth: dispositivo sin lugar → volver a iniciar sesión", usuario(DEMO, uuid.uuid4()).sin_empresa)
with mock.patch.object(auth, "cliente_con_token", side_effect=lambda t: ClienteFalso(uri, DEMO, sesion=t)):
    auth.salir_de_empresa(str(CEL))
ok("app.auth.salir_de_empresa olvida solo el lugar de esa sesión",
   usuario(DEMO, CEL).sin_empresa and usuario(DEMO, COMPU).empresa_nombre == "Just Show Up")

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261031999994', 'multiempresa_3c_sesiones')")
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'multiempresa_3c_sesiones'", uno=True)["n"] == 0)
final = huellas_estructura()
ok("vuelta atrás: la estructura vuelve a ser idéntica a la de la parte 3b", final == estructura_3b,
   {k: (final.get(k), v) for k, v in estructura_3b.items() if final.get(k) != v})

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
