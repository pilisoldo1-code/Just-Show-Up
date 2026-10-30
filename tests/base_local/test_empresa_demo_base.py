"""Script de la Empresa 2 de demostración (scripts/empresa_demo.py), en un Postgres LOCAL descartable con la
COPIA de producción y las migraciones de multiempresa 1 a 4. Crea la empresa, comprueba lo que ve cada rol
con la sesión de cada una (RLS real), que la Empresa 1 no cambia, y que `borrar` deja todo como estaba.
No toca Supabase.

Uso:  python tests/base_local/test_empresa_demo_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import hashlib
import json
import os
import sys
import uuid
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
from scripts import empresa_demo  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
HOY = date(2026, 11, 4)


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


class BaseLocal:
    """La misma interfaz que BaseSupabase, como superusuaria local (equivale a la clave de administrador)."""

    def crear_cuenta(self, mail, nombre, apellido, contrasena, meta):
        i = str(uuid.uuid4())
        q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
          (i, mail, psycopg2.extras.Json({"nombre": nombre, "apellido": apellido, **meta})))
        return i

    def borrar_cuenta(self, uid):
        q(su, "delete from auth.users where id = %s", (uid,))

    def insertar(self, tabla, filas):
        salida = []
        for f in filas:
            cols = list(f)
            salida.append(q(su, f"insert into public.{tabla} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                                "returning *", [f[c] for c in cols], True))
        return [{k: (str(v) if isinstance(v, (uuid.UUID, date)) else v) for k, v in s.items()} for s in salida]

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


TABLAS = copia_produccion.TABLAS + ["empresa", "membresia"]


def foto():
    return {t: hashlib.sha256(json.dumps(q(su, f"select * from public.{t} order by 1"), default=str, sort_keys=True).encode()).hexdigest()
            for t in TABLAS} | {"auth.users": q(su, "select count(*) n from auth.users", uno=True)["n"]}


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
antes = foto()
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
DEMO, ADMIN1 = uid("alumna@prueba.invalid"), uid("profesora@prueba.invalid")
huella_e1 = {t: q(su, f"select count(*) n, md5(coalesce(string_agg(t::text, ',' order by t::text), '')) h from public.{t} t where id_empresa = %s",
                  (E1,), True) for t in ("tipo_clase", "horario", "clase", "reserva", "asistencia", "resumen_mensual")}

db = BaseLocal()
r = empresa_demo.crear(db, HOY, guardar_contrasenas=False)
E2 = r["empresa"]
ok("crea la empresa con cuentas, clases, reservas y asistencias",
   r["cuentas"] == 7 and r["clases"] > 10 and r["reservas"] > r["clases"] and r["asistencias"] > 0, r)
ok("las contraseñas quedan solo para las dos profesoras y una alumna", len(r["contrasenas"]) == 3)
e = q(su, "select * from empresa where id = %s", (E2,), True)
ok("Pilates Centro, código PIL, Mercado Pago apagado", (e["nombre"], e["codigo"], e["mp_habilitado"]) == ("Pilates Centro", "PIL", False))
m = {x["mail"]: x for x in q(su, "select p.mail, m.* from membresia m join profiles p on p.id = m.id_usuario where m.id_empresa = %s", (E2,))}
ok("roles: administradora y profesora, otra profesora, 6 alumnas (incluida la compartida)",
   m[empresa_demo.ADMIN[0]]["es_admin"] and m[empresa_demo.ADMIN[0]]["es_profesora"]
   and m[empresa_demo.PROFESORA[0]]["es_profesora"] and not m[empresa_demo.PROFESORA[0]]["es_admin"]
   and sum(x["es_alumna"] for x in m.values()) == 6 and not any(x["es_alumna"] for x in (m[empresa_demo.ADMIN[0]], m[empresa_demo.PROFESORA[0]])))
ok("las cuentas nuevas no quedan en la Empresa 1",
   q(su, "select count(*) n from membresia m join profiles p on p.id = m.id_usuario where m.id_empresa = %s and p.mail like '%%pilates@demo.invalid'", (E1,), True)["n"] == 0)
ok("alumna@prueba.invalid queda en las dos empresas con la misma cuenta",
   q(su, "select count(*) n from membresia where id_usuario = %s and activa", (DEMO,), True)["n"] == 2)
ok("la Empresa 1 no cambia", huella_e1 == {t: q(su, f"select count(*) n, md5(coalesce(string_agg(t::text, ',' order by t::text), '')) h from public.{t} t where id_empresa = %s",
                                                  (E1,), True) for t in huella_e1})
ok("cada clase guarda la profesora de su horario",
   q(su, "select count(*) n from clase c join horario h on h.id = c.id_horario where c.id_empresa = %s and c.id_profesora is distinct from h.id_profesora", (E2,), True)["n"] == 0)
ok("los cupos no se pasan", q(su, "select count(*) n from clase c where id_empresa = %s and cantidad_inscriptas > cupo_max", (E2,), True)["n"] == 0)

# lo que ve cada una con su sesión
C = lambda i: ClienteFalso(uri, i)
P = C(uid(empresa_demo.PROFESORA[0]))
mias = P.table("clase").select("id, id_profesora").execute().data
ok("profesora: ve las clases de su empresa", len(mias) == r["clases"])
ok("profesora: solo lee reservas de sus clases",
   {x["id_clase"] for x in P.table("reserva").select("id_clase").execute().data}
   <= {c["id"] for c in mias if c["id_profesora"] == uid(empresa_demo.PROFESORA[0])})
AL = C(uid(empresa_demo.ALUMNAS[0][0]))
ok("alumna de la Empresa 2: solo sus reservas y solo clases de la Empresa 2",
   {x["id_alumna"] for x in AL.table("reserva").select("id_alumna").execute().data} <= {uid(empresa_demo.ALUMNAS[0][0])}
   and {x["id_empresa"] for x in AL.table("clase").select("id_empresa").execute().data} == {E2})
ok("alumna de la Empresa 2: ve el nombre de sus dos profesoras",
   {x["nombre"] for x in AL.rpc("profesoras_empresa", {}).execute().data} == {"Lucía Bentancor", "Carolina Méndez"})
D = C(DEMO)
ok("alumna compartida: con la Empresa 1 activa no ve clases de Pilates Centro",
   E2 not in {x["id_empresa"] for x in D.table("clase").select("id_empresa").execute().data})
D.rpc("elegir_empresa", {"p_id_empresa": E2}).execute()
ok("alumna compartida: al cambiar de empresa ve solo las de Pilates Centro",
   {x["id_empresa"] for x in D.table("clase").select("id_empresa").execute().data} == {E2})
D.rpc("elegir_empresa", {"p_id_empresa": E1}).execute()
A1 = C(ADMIN1)
ok("administradora de la Empresa 1: no ve nada de la Empresa 2",
   E2 not in {x["id_empresa"] for x in A1.table("reserva").select("id_empresa").execute().data}
   and A1.table("profiles").select("id").eq("mail", empresa_demo.ALUMNAS[0][0]).execute().data == [])
try:
    empresa_demo.crear(db, HOY, guardar_contrasenas=False)
    ok("crear dos veces se niega", False)
except SystemExit:
    ok("crear dos veces se niega", True)

empresa_demo.borrar(db, guardar_contrasenas=False)
ok("borrar deja todas las tablas idénticas a antes (Empresa 1 incluida)", foto() == antes,
   [k for k, v in foto().items() if antes.get(k) != v])
try:
    empresa_demo.borrar(db, guardar_contrasenas=False)
    ok("borrar sin empresa se niega", False)
except SystemExit:
    ok("borrar sin empresa se niega", True)

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
