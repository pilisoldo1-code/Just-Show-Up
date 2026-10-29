"""Prueba la VUELTA ATRÁS de la migración de Mercado Pago en un Postgres LOCAL descartable, sobre una
copia del respaldo de producción. No toca Supabase y no modifica el respaldo (solo lo lee).

1) Reconstruye producción: migraciones de origin/main + datos del respaldo; comprueba que la
   estructura y los datos son idénticos al respaldo.
2) Varias veces: aplica la migración de Mercado Pago, la usa (token en Vault, interruptor,
   intentos y eventos), aplica supabase/pendientes/vuelta_atras_mercado_pago.sql y comprueba
   que la estructura y los datos vuelven a ser idénticos al respaldo.
3) Seguridad: con un pago de Mercado Pago registrado la vuelta atrás se detiene sin cambiar nada;
   sobre una base sin la migración no rompe nada.

Uso: python tests/base_local/vuelta_atras_mercado_pago.py "<carpeta del respaldo>"
"""
import json
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402

RESPALDO = sys.argv[1]
REPO = localdb.REPO
MIGRACION = os.path.join(REPO, "supabase/migrations/20261029120000_mercado_pago_prueba.sql")
VUELTA = os.path.join(REPO, "supabase/pendientes/vuelta_atras_mercado_pago.sql")
TABLAS = ["profiles", "tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo",
          "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion"]
ORDEN = {"profiles": "id::text", "pago_anulacion": "id_pago"}
fallas = total = 0


def ok(n, c, extra=""):
    global fallas, total
    total += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def leer(nombre):
    with open(os.path.join(RESPALDO, nombre), encoding="utf-8") as f:
        return f.read()


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, check=True).stdout


CONSULTA_HUELLAS = leer("huellas_estructura.sql")
PROD = json.loads(leer("huellas_produccion.json"))
PROD_DATOS = json.loads(leer("huellas_datos_produccion.json"))
SECUENCIA = PROD_DATOS["_secuencia_facturas"]


def huellas(conn):
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(conn, CONSULTA_HUELLAS)}


def huellas_datos(conn):
    q(conn, "set timezone = 'UTC'")
    return {t: list(q(conn, f"select count(*) n, md5(coalesce(string_agg(to_jsonb(x)::text, '|' order by {ORDEN.get(t, 'id')}), '')) h "
                            f"from public.{t} x", uno=True).values()) for t in TABLAS}


def igual_al_respaldo(conn):
    h, hd = huellas(conn), huellas_datos(conn)
    sec = q(conn, "select last_value, is_called from public.factura_numero_seq", uno=True)
    dif_e = {k: (h.get(k), PROD[k]) for k in set(PROD) | set(h) if h.get(k) != PROD.get(k)}
    dif_d = {t: (hd[t], PROD_DATOS[t]) for t in TABLAS if hd[t] != PROD_DATOS[t]}
    sec_ok = sec == {"last_value": SECUENCIA["last_value"], "is_called": SECUENCIA["is_called"]}
    return not dif_e and not dif_d and sec_ok, {"estructura": dif_e, "datos": dif_d, "secuencia": sec}


def ejecutar(conn, ruta):
    with conn.cursor() as cur:
        cur.execute(open(ruta, encoding="utf-8").read())


def vault(conn):
    return q(conn, "select count(*) n from vault.secrets where name like 'mp\\_%'", uno=True)["n"]


# ---------- 1) copia de producción ----------
srv, uri, carpeta = localdb.iniciar()
su = conectar(uri)
aplicadas = sorted(l.split("supabase/migrations/")[1] for l in git("ls-tree", "-r", "--name-only", "origin/main", "supabase/migrations/").split())
ok("origin/main tiene las 13 migraciones de producción (sin la de Mercado Pago)",
   len(aplicadas) == 13 and not any("mercado_pago" in m for m in aplicadas), aplicadas)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for m in aplicadas:
        cur.execute(git("show", f"origin/main:supabase/migrations/{m}"))
    for u in json.loads(leer("datos.json"))["auth_users"]:
        cur.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
                    (u["id"], u["email"], json.dumps(u["user_metadata"] or {})))
    cur.execute("set session_replication_role = replica")
    cur.execute("delete from public.profiles")
    cur.execute("set session_replication_role = origin")
    cur.execute(leer("restaurar_datos.sql"))
igual, dif = igual_al_respaldo(su)
ok("copia local = respaldo de producción (estructura, 13 tablas y secuencia de facturas)", igual, dif)

# ---------- 2) aplicar, usar y deshacer, varias veces ----------
SERV = conectar(uri, None, "service_role")
for vuelta in (1, 2, 3):
    ejecutar(su, MIGRACION)
    hd = huellas_datos(su)
    ok(f"[{vuelta}] la migración se aplica y no cambia ningún dato existente", all(hd[t] == PROD_DATOS[t] for t in TABLAS))
    ok(f"[{vuelta}] Mercado Pago queda apagado", q(su, "select habilitado from mp_configuracion", uno=True)["habilitado"] is False)
    q(SERV, "select public.mp_guardar_secreto('mp_access_token', 'APP_USR-token-ficticio-de-prueba')")
    q(SERV, "select public.mp_guardar_secreto('mp_webhook_secret', 'secreto-ficticio')")
    q(SERV, "select public.mp_cambiar_habilitado(true)")
    q(SERV, "select public.mp_registrar_evento('aviso', 'payment', '1', 'sin_clave', 'prueba')")
    ok(f"[{vuelta}] en uso: 2 secretos en Vault, interruptor encendido y un evento", vault(su) == 2
       and q(su, "select habilitado from mp_configuracion", uno=True)["habilitado"] is True
       and q(su, "select count(*) n from mp_evento", uno=True)["n"] == 1)
    ejecutar(su, VUELTA)
    igual, dif = igual_al_respaldo(su)
    ok(f"[{vuelta}] después de la vuelta atrás: estructura y datos idénticos al respaldo", igual, dif)
    ok(f"[{vuelta}] Vault sin secretos de Mercado Pago", vault(su) == 0)
    ok(f"[{vuelta}] no quedan tablas, funciones ni columna de Mercado Pago",
       q(su, """select count(*) n from (
                  select 1 from pg_tables where schemaname = 'public' and tablename like 'mp\\_%'
                  union all select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                            where n.nspname = 'public' and (p.proname like 'mp\\_%' or p.proname like '%_mp' or p.proname like '%mercado_pago%')
                  union all select 1 from information_schema.columns where table_schema = 'public' and column_name = 'id_transaccion') x""",
         uno=True)["n"] == 0)

# ---------- 3) seguridad ----------
ejecutar(su, VUELTA)
igual, dif = igual_al_respaldo(su)
ok("la vuelta atrás sobre una base SIN la migración no rompe nada (sigue idéntica)", igual, dif)

ejecutar(su, MIGRACION)
alumna = q(su, "select id from profiles where rol = 'alumna' order by id::text limit 1", uno=True)["id"]
with su.cursor() as cur:
    cur.execute("""insert into resumen_mensual (id_alumna, mes, anio, estado_factura, estado_pago, cantidad_clases,
                     subtotal_original, total_lineas, descuento_monto, total, numero, confirmada_en, alumna_nombre, alumna_apellido)
                   values (%s, 1, 2026, 'confirmada', 'pagado', 1, 100, 100, 0, 100, 'PRUEBA-VUELTA-ATRAS', now(), 'Prueba', 'Local')
                   returning id""", (alumna,))
    fid = cur.fetchone()[0]
    cur.execute("insert into pago (id_resumen_mensual, monto, medio_pago, fecha, id_transaccion) "
                "values (%s, 100, 'mercado_pago', '2026-02-01', 'PRUEBA-1') returning id", (fid,))
    pid = cur.fetchone()[0]
antes_e = huellas(su)
try:
    ejecutar(su, VUELTA)
    detenida = False
except psycopg2.Error as e:
    detenida = "Hay pagos registrados con Mercado Pago" in str(e)
    q(su, "rollback")  # el script abre su propia transacción: queda abortada y se descarta entera
ok("con un pago de Mercado Pago registrado, la vuelta atrás se detiene", detenida)
ok("…y no cambia nada (todo o nada)", huellas(su) == antes_e
   and q(su, "select count(*) n from pago where id = %s", (pid,), True)["n"] == 1)
with su.cursor() as cur:
    cur.execute("delete from pago where id = %s", (pid,))
    cur.execute("delete from resumen_mensual where id = %s", (fid,))
ejecutar(su, VUELTA)
q(su, f"select setval('public.factura_numero_seq', {int(SECUENCIA['last_value'])}, {'true' if SECUENCIA['is_called'] else 'false'})")
for t in ("resumen_mensual", "pago"):
    q(su, f"select setval(pg_get_serial_sequence('public.{t}', 'id'), coalesce((select max(id) from public.{t}), 0) + 1, false)")
igual, dif = igual_al_respaldo(su)
ok("sin ese pago, la vuelta atrás funciona y todo queda idéntico al respaldo", igual, dif)

SERV.close()
su.close()
localdb.borrar(srv, carpeta)
print(f"\n{total - fallas}/{total} casos OK")
sys.exit(1 if fallas else 0)
