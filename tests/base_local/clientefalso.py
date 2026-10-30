"""Cliente "tipo supabase-py" contra el Postgres local, con el rol y la sesión de una usuaria
(RLS real). Soporta lo que usa app/facturas.py: table().select().eq/in_/gte/lt/order().execute()
y rpc(). Las fechas y los números vuelven como los devolvería PostgREST (texto ISO y números)."""
import datetime
import decimal
import uuid

import psycopg2
import psycopg2.extras


def _json(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json(x) for x in v]
    return v


class _Res:
    def __init__(self, data):
        self.data = data


class _Consulta:
    def __init__(self, cli, tabla):
        self.cli, self.tabla, self.cols, self.donde, self.args, self.orden = cli, tabla, "*", [], [], []

    def select(self, cols):
        self.cols = cols
        return self

    def _f(self, sql, *a):
        self.donde.append(sql)
        self.args.extend(a)
        return self

    def eq(self, c, v):
        return self._f(f"{c} = %s", v)

    def gte(self, c, v):
        return self._f(f"{c} >= %s", v)

    def lt(self, c, v):
        return self._f(f"{c} < %s", v)

    def in_(self, c, vs):
        vs = list(vs)
        if not vs:
            return self._f("false")
        return self._f(f"{c} in ({', '.join(['%s'] * len(vs))})", *vs)

    def order(self, c, desc=False):
        self.orden.append(f"{c} {'desc' if desc else 'asc'}")
        return self

    def execute(self):
        sql = f"select {self.cols} from public.{self.tabla}"
        if self.donde:
            sql += " where " + " and ".join(self.donde)
        if self.orden:
            sql += " order by " + ", ".join(self.orden)
        with self.cli.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, self.args)
            return _Res([_json(dict(r)) for r in cur.fetchall()])


class _Rpc:
    def __init__(self, cli, nombre, params):
        self.cli, self.nombre, self.params = cli, nombre, params

    def execute(self):
        args = ", ".join(f"{k} => %s" for k in self.params)
        with self.cli.conn.cursor() as cur:
            cur.execute("select bool_or(proretset) from pg_proc where pronamespace = 'public'::regnamespace and proname = %s",
                        (self.nombre,))
            devuelve_filas = cur.fetchone()[0]
        if devuelve_filas:  # como PostgREST: una lista de filas
            with self.cli.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"select * from public.{self.nombre}({args})", list(self.params.values()))
                return _Res([_json(dict(r)) for r in cur.fetchall()])
        with self.cli.conn.cursor() as cur:
            cur.execute(f"select public.{self.nombre}({args})", list(self.params.values()))
            return _Res(_json(cur.fetchone()[0]))


class ClienteFalso:
    def __init__(self, uri, uid=None, rol="authenticated", ahora=None):
        self.conn = psycopg2.connect(uri)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(f"set role {rol}")
            if uid:
                cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))
        if ahora:
            self.reloj(ahora)

    def reloj(self, ahora):
        with self.conn.cursor() as cur:
            cur.execute("select set_config('prueba.ahora', %s, false)", (ahora,))

    def table(self, t):
        return _Consulta(self, t)

    def rpc(self, nombre, params):
        return _Rpc(self, nombre, params)
