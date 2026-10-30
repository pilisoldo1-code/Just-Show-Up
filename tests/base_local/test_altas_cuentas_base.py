"""Auditoría de TODAS las formas de crear cuentas y asignar roles (multiempresa), en un Postgres LOCAL
descartable con la COPIA de producción y todas las migraciones. Corre el código real: las rutas de la app
(alta de personas, panel de empresas), app.auth.crear_cuenta, los scripts (crear_profesora, crear_superadmin,
empresa_demo) y los triggers reales de la base. Lo único simulado es el servicio de cuentas de Supabase
(GoTrue): un adaptador inserta en auth.users igual que él (id, mail y metadatos). Después de cada alta
compara los roles con los esperados, y prueba intentos de obtener más permisos. No toca Supabase.

Uso:  python tests/base_local/test_altas_cuentas_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import uuid
from types import SimpleNamespace
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
sys.path.insert(0, AQUI)
sys.path.insert(0, RAIZ)
os.environ.setdefault("SUPABASE_URL", "http://local")
os.environ.setdefault("SUPABASE_KEY", "local")
os.environ["SUPABASE_SERVICE_KEY"] = "local-solo-pruebas"
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from supabase_auth.errors import AuthApiError  # noqa: E402
from app import auth, main  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
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


# ---------- adaptador del servicio de cuentas (GoTrue) y de la clave de administrador ----------
class _Tabla:
    """table() con la clave de administrador: sin RLS (como service_role)."""

    def __init__(self, nombre):
        self.nombre, self.donde, self.args, self.op, self.datos, self.cols = nombre, [], [], "select", None, "*"

    def select(self, cols="*", **_):
        self.cols = cols
        return self

    def update(self, datos):
        self.op, self.datos = "update", datos
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, c, v):
        self.donde.append(f"{c} = %s"); self.args.append(v)
        return self

    def neq(self, c, v):
        self.donde.append(f"{c} <> %s"); self.args.append(v)
        return self

    def execute(self):
        donde = (" where " + " and ".join(self.donde)) if self.donde else ""
        if self.op == "select":
            filas = q(su, f"select {self.cols} from public.{self.nombre}{donde}", self.args)
            return SimpleNamespace(data=[{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in f.items()} for f in filas])
        if self.op == "update":
            q(su, f"update public.{self.nombre} set {', '.join(f'{k} = %s' for k in self.datos)}{donde}",
              list(self.datos.values()) + self.args)
        else:
            q(su, f"delete from public.{self.nombre}{donde}", self.args)
        return SimpleNamespace(data=[])


class _AuthAdmin:
    def create_user(self, datos):
        if q(su, "select 1 from auth.users where lower(email) = lower(%s)", (datos["email"],)):
            raise AuthApiError("A user with this email address has already been registered", 422, "email_exists")
        i = str(uuid.uuid4())
        q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
          (i, datos["email"], psycopg2.extras.Json(datos.get("user_metadata") or {})))
        return SimpleNamespace(user=SimpleNamespace(id=i))

    def delete_user(self, i):
        q(su, "delete from auth.users where id = %s", (i,))

    def update_user_by_id(self, i, datos):
        return None


class AdminLocal:
    auth = SimpleNamespace(admin=_AuthAdmin())

    def table(self, nombre):
        return _Tabla(nombre)


auth.cliente_admin = lambda: AdminLocal()


def membresias(mail):
    return {r["id_empresa"]: (r["es_admin"], r["es_profesora"], r["es_alumna"])
            for r in q(su, "select m.* from membresia m join profiles p on p.id = m.id_usuario where p.mail = %s", (mail,))}


def perfil(mail):
    return q(su, "select id::text, rol, es_superadmin from profiles where mail = %s", (mail,), True)


E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADMIN1 = perfil("profesora@prueba.invalid")["id"]

# ---------- 1. superadministradora con su script ----------
from scripts import crear_superadmin, crear_profesora, empresa_demo  # noqa: E402
entradas = iter(["superadmin@prueba.invalid", "", ""])
with mock.patch("builtins.input", lambda *_: next(entradas)), mock.patch("getpass.getpass", lambda *_: "ClaveLarga123"), \
        mock.patch.object(crear_superadmin.auth, "cliente_admin", lambda: AdminLocal()):
    crear_superadmin.main()
SUPER = perfil("superadmin@prueba.invalid")
ok("script crear_superadmin: es superadministradora y no pertenece a ningún lugar",
   SUPER["es_superadmin"] and membresias("superadmin@prueba.invalid") == {} and SUPER["rol"] == "alumna")


# ---------- utilidades de sesión real ----------
class ClienteSesion(ClienteFalso):
    def __init__(self, uid_, sesion):
        super().__init__(uri, uid_, sesion=sesion)
        self.auth = mock.MagicMock()
        self.auth.get_user.return_value.user.id = uid_
        self.postgrest = mock.MagicMock()


def sesion_en(uid_, lugar=None):
    s = uuid.uuid4()
    if lugar:
        r = ClienteFalso(uri, uid_, sesion=s).rpc("entrar_a_empresa", {"p_lugar": lugar}).execute().data
        assert r["resultado"] == "ok", r
    with mock.patch.object(auth, "cliente_nuevo", return_value=ClienteSesion(uid_, s)):
        u = auth.usuario_de_acceso("token")
    return u, s


cliente = TestClient(main.app)


def como(uid_, lugar=None):
    """Las rutas corren con la sesión real (lugar elegido al entrar) y su cliente de la base."""
    u, s = sesion_en(uid_, lugar)
    return mock.patch.multiple(auth, _usuario_cacheado=lambda r: u), mock.patch.object(main, "_sb", lambda _u: ClienteFalso(uri, uid_, sesion=s))


def post(uid_, lugar, url, datos):
    p1, p2 = como(uid_, lugar)
    with p1, p2:
        return cliente.post(url, data=datos, follow_redirects=False)


# ---------- 2. panel de la superadministradora: empresa nueva + administradora inicial ----------
r = post(SUPER["id"], None, "/admin/empresas", {"nombre": "Pilates Centro", "codigo": "pil", "admin_nombre": "Lia",
                                               "admin_apellido": "Paz", "admin_mail": "lia.pil@prueba.invalid"})
E2 = q(su, "select id from empresa where codigo = 'PIL'", uno=True)["id"]
ok("panel: crea la empresa y la administradora inicial (solo administradora, no profesora)",
   r.status_code == 200 and membresias("lia.pil@prueba.invalid") == {E2: (True, False, False)}, membresias("lia.pil@prueba.invalid"))
r = post(SUPER["id"], None, f"/admin/empresas/{E2}/administradora",
         {"nombre": "Ana", "apellido": "Rey", "mail": "ana.pil@prueba.invalid", "tambien_profesora": "1"})
ok("panel: segunda administradora que también da clases", membresias("ana.pil@prueba.invalid") == {E2: (True, True, False)})
ok("las cuentas creadas por el panel no son superadministradoras ni tocan profiles.rol",
   not perfil("lia.pil@prueba.invalid")["es_superadmin"] and perfil("lia.pil@prueba.invalid")["rol"] == "alumna")
ADMIN2 = perfil("lia.pil@prueba.invalid")["id"]

# ---------- 3. alta de personas por la administradora (ruta real) ----------
ESPERADO = {"alumna": (False, False, True), "profesora": (False, True, False),
            "admin": (True, False, False), "admin_profesora": (True, True, False)}
for rol, flags in ESPERADO.items():
    mail = f"{rol.replace('_', '.')}.nueva@prueba.invalid"
    r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas", {"nombre": "Nueva", "apellido": "Persona", "mail": mail, "rol": rol})
    ok(f"alta «{rol}»: tiene exactamente esos permisos y solo en Pilates Centro",
       r.status_code == 200 and membresias(mail) == {E2: flags}, (r.status_code, membresias(mail)))
    ok(f"alta «{rol}»: no es superadministradora y profiles.rol no cambió",
       perfil(mail)["es_superadmin"] is False and perfil(mail)["rol"] == "alumna")
    u, _ = sesion_en(perfil(mail)["id"], "Pilates Centro")
    ok(f"alta «{rol}»: al entrar, el servidor le da el inicio y los permisos de su rol",
       (u.es_admin, u.es_docente and not u.es_admin or u.es_admin and flags[1], u.es_alumna)[0] == flags[0]
       and u.es_alumna == flags[2] and u.inicio == {"alumna": "/", "profesora": "/profesora/horarios"}.get(rol, "/administracion"))
r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas",
         {"nombre": "Ceci", "apellido": "Uno", "mail": "ceci.pil@prueba.invalid", "rol": "alumna", "cedula": "1.234.567-2"})
ok("alta con cédula: queda en la membresía de Pilates Centro, no en el perfil",
   q(su, "select m.cedula, p.cedula pc from membresia m join profiles p on p.id = m.id_usuario where p.mail = 'ceci.pil@prueba.invalid'",
     uno=True) == {"cedula": "12345672", "pc": None})
SOFIA = perfil("sofia@demo.invalid")["id"]
cedula_sofia = "11111111"
q(su, "update membresia set cedula = %s where id_usuario = %s and id_empresa = %s", (cedula_sofia, SOFIA, E1))
r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas",
         {"nombre": "Otra", "apellido": "Persona", "mail": "otra.pil@prueba.invalid", "rol": "alumna", "cedula": "11111111"})
ok("una cédula registrada en otra empresa no da ningún aviso (no revela nada)",
   r.status_code == 200 and "cédula" not in r.text.lower().split("contraseña temporal")[0].split("<main>")[-1])
r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas",
         {"nombre": "Dup", "apellido": "Licada", "mail": "dup.pil@prueba.invalid", "rol": "alumna", "cedula": "11111111"})
ok("repetida dentro de la misma empresa: aviso y no se crea la cuenta",
   r.status_code == 400 and main.MSG_CEDULA_REPETIDA in r.text and perfil("dup.pil@prueba.invalid") is None)
DEMO = perfil("alumna@prueba.invalid")["id"]
r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas",
         {"nombre": "Ana", "apellido": "Prueba", "mail": "alumna@prueba.invalid", "rol": "alumna"})
ok("mail existente: se suma como alumna de Pilates Centro con la misma cuenta, sin tocar la Empresa 1",
   r.status_code == 303 and membresias("alumna@prueba.invalid")[E2] == (False, False, True)
   and membresias("alumna@prueba.invalid")[E1] == (False, False, True)
   and q(su, "select count(*) n from auth.users where email = 'alumna@prueba.invalid'", uno=True)["n"] == 1)
r = post(ADMIN2, "Pilates Centro", "/profesora/alumnas",
         {"nombre": "Sara", "apellido": "Admin", "mail": "superadmin@prueba.invalid", "rol": "admin"})
ok("no se puede sumar la cuenta de la superadministradora a un lugar", r.status_code == 400 and membresias("superadmin@prueba.invalid") == {})

# ---------- 4. rutas de alta con roles sin permiso ----------
PROF2 = perfil("profesora.nueva@prueba.invalid")["id"]
ALU2 = perfil("alumna.nueva@prueba.invalid")["id"]
for nombre, i in (("profesora", PROF2), ("alumna", ALU2)):
    r = post(i, "Pilates Centro", "/profesora/alumnas", {"nombre": "X", "apellido": "Y", "mail": f"x.{nombre}@prueba.invalid", "rol": "admin"})
    ok(f"{nombre}: la ruta de alta la rechaza y no se crea nada", r.status_code == 303 and perfil(f"x.{nombre}@prueba.invalid") is None)
    r = post(i, "Pilates Centro", "/admin/empresas", {"nombre": "Otra", "codigo": "OTR", "admin_nombre": "A", "admin_apellido": "B",
                                                     "admin_mail": "a.b@prueba.invalid"})
    ok(f"{nombre}: no puede crear empresas", r.status_code == 303 and not q(su, "select 1 from empresa where codigo = 'OTR'"))
r = post(ADMIN2, "Pilates Centro", "/admin/empresas", {"nombre": "Otra", "codigo": "OTR", "admin_nombre": "A", "admin_apellido": "B",
                                                      "admin_mail": "a.b@prueba.invalid"})
ok("administradora: no puede crear empresas", r.status_code == 303 and not q(su, "select 1 from empresa where codigo = 'OTR'"))


# ---------- 5. intentos directos contra la base ----------
def intento(i, sql):
    c = ClienteFalso(uri, i)
    try:
        with c.conn.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount
    except psycopg2.Error as e:
        return e.pgcode


for nombre, i in (("profesora", PROF2), ("alumna", ALU2), ("administradora", ADMIN2)):
    ok(f"{nombre}: no puede hacerse superadministradora", intento(i, f"update public.profiles set es_superadmin = true where id = '{i}'") == "42501")
    ok(f"{nombre}: no puede cambiar su profiles.rol", intento(i, f"update public.profiles set rol = 'profesora' where id = '{i}'") == "42501")
    ok(f"{nombre}: no puede cambiar su membresía directo",
       intento(i, f"update public.membresia set es_admin = true where id_usuario = '{i}'") in ("42501", 0))
    ok(f"{nombre}: no puede crearse membresías directo",
       intento(i, f"insert into public.membresia (id_usuario, id_empresa, es_admin) values ('{i}', {E1}, true)") == "42501")
P = ClienteFalso(uri, PROF2)
ok("profesora: asignar_membresia para hacerse administradora → no_autorizada",
   P.rpc("asignar_membresia", {"p_id_usuario": PROF2, "p_es_admin": True, "p_es_profesora": True, "p_es_alumna": False}).execute().data == "no_autorizada")
ok("administradora: asignar_administradora (solo superadmin) → no_autorizada",
   ClienteFalso(uri, ADMIN2).rpc("asignar_administradora", {"p_id_empresa": E1, "p_id_usuario": ADMIN2, "p_es_profesora": False}).execute().data == "no_autorizada")
ok("administradora de Pilates Centro: no puede darse rol en la Empresa 1",
   ClienteFalso(uri, ADMIN2).rpc("asignar_membresia", {"p_id_usuario": ADMIN2, "p_es_admin": True, "p_es_profesora": False,
                                                       "p_es_alumna": False}).execute().data == "no_puede_quitarse_admin"
   or E1 not in membresias("lia.pil@prueba.invalid"))
ok("después de los intentos, nadie ganó permisos",
   membresias("profesora.nueva@prueba.invalid") == {E2: (False, True, False)}
   and membresias("alumna.nueva@prueba.invalid") == {E2: (False, False, True)} and E1 not in membresias("lia.pil@prueba.invalid"))
# la persona puede editar sus propios metadatos en Supabase Auth: eso no cambia sus permisos
q(su, "update auth.users set raw_user_meta_data = raw_user_meta_data || '{\"rol_empresa\": \"admin\", \"id_empresa\": %s}' where id = %s"
      % (E1, "%s"), (PROF2,))
q(su, "update profiles set telefono = '099000111' where id = %s", (PROF2,))
ok("editar sus metadatos (rol_empresa=admin) no le da permisos", membresias("profesora.nueva@prueba.invalid") == {E2: (False, True, False)})
q(su, "update auth.users set raw_user_meta_data = raw_user_meta_data - 'rol_empresa' where id = %s", (ALU2,))
q(su, "update profiles set rol = 'profesora' where id = %s", (ALU2,))  # solo lo podría hacer el servidor
ok("aunque borrara su marca y algo cambiara profiles.rol, no gana permisos en ninguna empresa",
   membresias("alumna.nueva@prueba.invalid") == {E2: (False, False, True)}, membresias("alumna.nueva@prueba.invalid"))
ok("ya no existe la compatibilidad con profiles.rol", not q(su, "select 1 from pg_trigger where tgname = 'membresia_desde_rol'"))

# ---------- 6. scripts ----------
entradas = iter(["legado.prof@prueba.invalid", "Leti", "Gomez"])
with mock.patch("builtins.input", lambda *_: next(entradas)), mock.patch("getpass.getpass", lambda *_: "ClaveLarga123"), \
        mock.patch.object(crear_profesora.auth, "cliente_admin", lambda: AdminLocal()):
    crear_profesora.main()
ok("script crear_profesora (alta original de la profesora): administradora y profesora de la Empresa 1, como antes",
   membresias("legado.prof@prueba.invalid") == {E1: (True, True, False)})


class BaseLocal(empresa_demo.BaseSupabase):
    def __init__(self):
        self.cli = None

    def crear_cuenta(self, mail, nombre, apellido, contrasena, meta):
        return _AuthAdmin().create_user({"email": mail, "user_metadata": {"nombre": nombre, "apellido": apellido, **meta}}).user.id

    def borrar_cuenta(self, i):
        _AuthAdmin().delete_user(i)

    def insertar(self, tabla, filas):
        return [q(su, f"insert into public.{tabla} ({', '.join(f)}) values ({', '.join(['%s'] * len(f))}) returning *",
                  list(f.values()), True) for f in filas]

    def leer(self, tabla, columnas, **eq):
        return _Tabla(tabla).select(columnas).eq(*list(eq.items())[0]).execute().data if eq else _Tabla(tabla).select(columnas).execute().data

    def actualizar(self, tabla, cambios, **eq):
        t = _Tabla(tabla).update(cambios)
        for k, v in eq.items():
            t.eq(k, v)
        t.execute()

    def borrar(self, tabla, **eq):
        t = _Tabla(tabla).delete()
        for k, v in eq.items():
            t.eq(k, v)
        t.execute()


q(su, "update empresa set nombre = 'Pilates Norte', codigo = 'PIN' where id = %s", (E2,))  # libera el nombre para el script
import datetime  # noqa: E402
empresa_demo.crear(BaseLocal(), datetime.date(2026, 11, 4), guardar_contrasenas=False)
E3 = q(su, "select id from empresa where codigo = 'PIL'", uno=True)["id"]
ok("script empresa_demo: administradora y profesora / profesora / alumnas con sus permisos exactos",
   membresias(empresa_demo.ADMIN[0]) == {E3: (True, True, False)} and membresias(empresa_demo.PROFESORA[0]) == {E3: (False, True, False)}
   and all(membresias(m) == {E3: (False, False, True)} for m, *_ in empresa_demo.ALUMNAS))
ok("ninguna cuenta creada en toda la prueba quedó como superadministradora salvo la del script",
   [r["mail"] for r in q(su, "select mail from profiles where es_superadmin")] == ["superadmin@prueba.invalid"])

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
