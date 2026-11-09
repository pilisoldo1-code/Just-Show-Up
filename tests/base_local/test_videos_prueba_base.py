"""Scripts de las pruebas reales de Videos On Demand, en un Postgres LOCAL descartable: aplicar las migraciones (orden,
reanudar, se niega si la base no es la esperada) y cargar los datos ficticios con sus reglas reales. No toca Supabase.

Uso:  python tests/base_local/test_videos_prueba_base.py
"""
import os
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(AQUI)), "scripts"))
os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
import videos_prueba_datos as datos  # noqa: E402
import videos_prueba_migrar as migrar  # noqa: E402
from app import entorno  # noqa: E402

fallas = total_casos = 0


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def rechaza(conn, archivos):
    try:
        migrar.aplicar(conn, archivos)
        return False
    except entorno.DestinoNoPermitido:
        return True


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
ARCHIVOS, problemas = migrar.revisar()
ok("revisión sin problemas", problemas == [], problemas)


ROLES_SHIM = "create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;"


def base_nueva(nombre, con_shim=True):
    q(su, f"create database {nombre}")
    otra = uri.replace("/postgres?", f"/{nombre}?")
    c = psycopg2.connect(otra)
    c.autocommit = True
    if con_shim:
        with c.cursor() as cur:
            cur.execute("select exists (select 1 from pg_roles where rolname = 'anon')")
            # los roles son de todo el servidor: solo la primera base los crea
            cur.execute(localdb.SHIM.replace(ROLES_SHIM, "") if cur.fetchone()[0] else localdb.SHIM)
    return c, otra


# ---------- se niega si la base no es la esperada ----------
c, _ = base_nueva("sin_supabase", con_shim=False)
ok("sin los esquemas de Supabase no aplica", rechaza(c, ARCHIVOS))
c.close()
c, _ = base_nueva("con_tablas")
with c.cursor() as cur:
    cur.execute("create table public.ajena (id int)")
ok("con tablas ajenas no aplica", rechaza(c, ARCHIVOS))
with c.cursor() as cur:
    cur.execute("select to_regclass('supabase_migrations.schema_migrations')")
    ok("y no escribió nada", cur.fetchone()[0] is None)
c.close()

# ---------- se detiene en la que falla y retoma ----------
with tempfile.TemporaryDirectory() as d:
    a, b = Path(d) / "20260101000000_buena.sql", Path(d) / "20260102000000_mala.sql"
    a.write_text("create table public.buena (id int); create schema if not exists auth; create schema if not exists storage;")
    b.write_text("create table public.mala (id int); select 1/0;")
    c, _ = base_nueva("retoma")
    try:
        migrar.aplicar(c, [a, b])
        ok("una migración con error corta", False)
    except psycopg2.Error:
        ok("una migración con error corta", True)
    c.autocommit = True
    ok("la que falló no dejó nada", q(c, "select to_regclass('public.mala') t", uno=True)["t"] is None
       and q(c, "select to_regclass('public.buena') t", uno=True)["t"] == "buena")
    b.write_text("create table public.mala (id int);")
    ok("corregida, retoma solo desde ahí", migrar.aplicar(c, [a, b]) == [b.name])
    c.close()

# ---------- aplicar todas ----------
c, uri_prueba = base_nueva("completa")
hechas = migrar.aplicar(c, ARCHIVOS)
ok("aplica las migraciones en orden", hechas == [a.name for a in ARCHIVOS], len(hechas))
c.autocommit = True
ok("repetir no hace nada", migrar.aplicar(c, ARCHIVOS) == [])
c.autocommit = True
ok("registra cada una", q(c, "select count(*) n from supabase_migrations.schema_migrations", uno=True)["n"] == len(ARCHIVOS))
ok("el entorno arranca cerrado", q(c, "select entorno from public.video_parametros", uno=True)["entorno"] is None)


class DbLocal:
    def __init__(self, conn):
        self.conn = conn

    def crear_cuenta(self, mail, contrasena, meta):
        i = str(uuid.uuid4())
        q(self.conn, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)", (i, mail, psycopg2.extras.Json(meta)))
        q(self.conn, "update public.profiles set empresa_activa = %s where id = %s", (meta["id_empresa"], i))
        return i

    def insertar(self, tabla, fila):
        cols = ", ".join(fila)
        marcas = ", ".join(["%s"] * len(fila))
        return q(self.conn, f"insert into public.{tabla} ({cols}) values ({marcas}) returning *", list(fila.values()), uno=True)

    def leer(self, tabla, columnas, **eq):
        where = " and ".join(f"{k} = %s" for k in eq) or "true"
        return q(self.conn, f"select {columnas} from public.{tabla} where {where}", list(eq.values()))

    def actualizar(self, tabla, cambios, **eq):
        sets = ", ".join(f"{k} = %s" for k in cambios)
        where = " and ".join(f"{k} = %s" for k in eq)
        q(self.conn, f"update public.{tabla} set {sets} where {where}", list(cambios.values()) + list(eq.values()))


# ---------- datos ficticios ----------
db = DbLocal(c)
FECHA = date(2026, 12, 7)
r = datos.crear(db, FECHA, "18:00")
ok("dos empresas de prueba", {e["codigo"] for e in db.leer("empresa", "codigo")} >= {"PRUA", "PRUB"})
ok("ocho cuentas ficticias", len(r["cuentas"]) == 8 and all(m.endswith("@prueba.invalid") for m in r["cuentas"]))
ok("contraseñas distintas", len(set(r["cuentas"].values())) == 8)
ok("el entorno queda configurado", q(c, "select entorno from public.video_parametros", uno=True)["entorno"] == r["entorno"]
   and r["entorno"].startswith("pruebas-"))
tipos = db.leer("tipo_clase", "duracion", nombre="PRUEBA Clase de 40 minutos")
ok("tipo de clase de 40 minutos en cada empresa", [t["duracion"] for t in tipos] == [40, 40], tipos)
ok("clases de 40 minutos", all(x["duracion"] == 40 for x in db.leer("clase", "duracion")) and len(db.leer("clase", "id")) == 2)
ok("grabación activada", len(db.leer("video_programacion", "id", activa=True)) == 2)
ok("el token se guarda solo como huella", r["token"] not in str(db.leer("video_agente", "token_hash")))
try:
    datos.crear(db, FECHA, "18:00")
    ok("no se crea dos veces", False)
except SystemExit:
    ok("no se crea dos veces", True)

# ---------- el flujo funciona con esos datos ----------
srvc = ClienteFalso(uri_prueba, None, rol="service_role")
from app import videos  # noqa: E402
prof = srvc.rpc("video_agente_identificar", {"p_token_hash": videos.huella_token(r["token"]), "p_entorno": r["entorno"]}).execute().data
ok("el token sirve con su entorno", prof is not None)
ok("y no con otro", srvc.rpc("video_agente_identificar", {"p_token_hash": videos.huella_token(r["token"]), "p_entorno": "pruebas-otroentorno"}).execute().data is None)
consulta = {"p_id_profesora": prof, "p_inicio": "2026-12-07T18:02:00-03:00", "p_duracion_seg": 2340}
ok("la clase queda autorizada", srvc.rpc("video_consultar", consulta).execute().data["resultado"] == "autorizada")
ok("corto no entra", srvc.rpc("video_consultar", {**consulta, "p_duracion_seg": 300}).execute().data["resultado"] == "duracion_fuera_de_rango")
db.actualizar("video_parametros", {"margen_menos_seg": 2100}, id=1)
ok("margen ampliado para videos cortos", srvc.rpc("video_consultar", {**consulta, "p_duracion_seg": 300}).execute().data["resultado"] == "autorizada")
db.actualizar("video_parametros", {"margen_menos_seg": datos.MARGEN_MENOS_SEG}, id=1)
ok("restaurar el margen", srvc.rpc("video_consultar", {**consulta, "p_duracion_seg": 300}).execute().data["resultado"] == "duracion_fuera_de_rango")
datos.nueva_clase(db, date(2026, 12, 8), "19:00")
ok("agrega otra clase activada", srvc.rpc("video_consultar", {**consulta, "p_inicio": "2026-12-08T19:00:00-03:00"}).execute().data["resultado"] == "autorizada")

c.close()
su.close()
localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos correctos")
sys.exit(1 if fallas else 0)
