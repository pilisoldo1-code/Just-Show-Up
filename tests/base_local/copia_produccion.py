"""Carga en el Postgres LOCAL una copia de los datos de producción tomada de un respaldo (datos.json, que
vive fuera del repo). La base local es descartable: nunca se copia nada al repositorio."""
import json
import os

import psycopg2.extras

TABLAS = ["profiles", "tipo_clase", "horario", "clase", "bloqueo", "reserva", "listadeespera", "asistencia",
          "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion",
          "mp_configuracion", "mp_intento", "mp_evento"]


def cargar(su, respaldo):
    """Reemplaza los datos iniciales que cargan las migraciones por la copia de producción."""
    datos = json.load(open(os.path.join(respaldo, "datos.json")))
    with su.cursor() as cur:
        cur.execute("set session_replication_role = replica")
        for u in datos["auth_users"]:
            cur.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
                        (u["id"], u["email"], psycopg2.extras.Json(u.get("user_metadata") or {})))
        for t in reversed(TABLAS):
            cur.execute(f"delete from public.{t}")
        for t in TABLAS:
            for fila in datos[t]:
                cols = list(fila)
                vals = [psycopg2.extras.Json(v) if isinstance(v, (dict, list)) else v for v in fila.values()]
                cur.execute(f"insert into public.{t} ({', '.join(cols)}) overriding system value "
                            f"values ({', '.join(['%s'] * len(cols))})", vals)
        cur.execute("set session_replication_role = origin")
        for t in TABLAS:
            cur.execute("select a.attname from pg_attribute a where a.attrelid = %s::regclass and a.attidentity <> ''",
                        (f"public.{t}",))
            for (col,) in cur.fetchall():
                cur.execute(f"select setval(pg_get_serial_sequence('public.{t}', '{col}'), "
                            f"coalesce((select max({col}) from public.{t}), 0) + 1, false)")
        cur.execute("select setval('public.factura_numero_seq', 1, false)")  # como producción desde el 30/10
    return datos
