"""Vencimiento de facturas (migración 20261030120000) en un Postgres LOCAL descartable que reconstruye
producción con todas las migraciones anteriores y datos sintéticos, con reloj simulado.
Comprueba: estructura igual a producción antes de migrar, datos existentes intactos, reglas de
vencimiento (días 10/11, confirmación tardía, correcciones, límite de 2 plazos, reemisión
administrativa, anterior nunca publicada), permisos, compatibilidad con el código actual (anular sin
p_tipo), pago de una factura vencida (manual y Mercado Pago de prueba) y vuelta atrás exacta.
No toca Supabase.

Uso (con las dependencias de requirements-dev.txt):
    python tests/base_local/test_vencimiento_base.py ["<carpeta del respaldo del 30/10>"]
Con la carpeta, además compara la estructura con las huellas de producción guardadas en el respaldo.
"""
import json
import os
import sys
import uuid
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1] if len(sys.argv) > 1 else None


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def rpc(c, f, **kw):
    return c.rpc(f, kw).execute().data


def rpc_error(c, f, **kw):
    try:
        rpc(c, f, **kw)
        return None
    except psycopg2.Error as e:
        return e.pgcode or "error"


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
NUEVA = [a for a in localdb.migraciones() if a.endswith("_vencimiento_facturas.sql")]
assert len(NUEVA) == 1, NUEVA
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_vencimiento_facturas.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if a not in NUEVA:
            cur.execute(open(a).read())
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
if RESPALDO:
    prod = json.load(open(os.path.join(RESPALDO, "huellas_produccion.json")))
    local = huellas_estructura()
    ok("antes de migrar: la estructura local es idéntica a la de producción (huellas del respaldo)", local == prod,
       {k: (local.get(k), v) for k, v in prod.items() if local.get(k) != v})
q(su, localdb.RELOJ)

# ---------- usuarias, clases de junio 2026 y reservas ----------
usuarias = {}
for clave, mail in (("Profe", "profe@prueba.invalid"), ("Demo", "alumna@prueba.invalid"), ("Ana", "ana@prueba.invalid"),
                    ("Bea", "bea@prueba.invalid"), ("Caro", "caro@prueba.invalid"), ("Dani", "dani@prueba.invalid"),
                    ("Eli", "eli@prueba.invalid"), ("Fede", "fede@prueba.invalid"), ("Gabi", "gabi@prueba.invalid"),
                    ("Vieja", "vieja@prueba.invalid"), ("Tarde", "tarde@prueba.invalid")):
    uid = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (uid, mail, f'{{"nombre": "{clave}", "apellido": "Prueba"}}'))
    usuarias[clave] = uid
q(su, "update profiles set rol = 'profesora' where id = %s", (usuarias["Profe"],))
gim = q(su, "select id from tipo_clase where nombre = 'Gimnasia'", uno=True)["id"]
clases = []
with su.cursor() as cur:
    cur.execute("set session_replication_role = replica")
    cur.execute("""insert into horario (id, dia_semana, hora_inicio, id_tipo_clase, activo, duracion, cupo_max, precio)
                   overriding system value values (900001, 'domingo', '05:00', %s, false, 60, 20, 800)""", (gim,))
    for dia in ("2026-06-07", "2026-06-14", "2026-06-21"):
        cur.execute("""insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase)
                       values (900001, %s, '05:00', 0, 'programada', 60, 800, 20, %s) returning id""", (dia, gim))
        clases.append(cur.fetchone()[0])
    cur.execute("set session_replication_role = origin")
ALUMNAS = [k for k in usuarias if k != "Profe"]
for k in ALUMNAS:
    for c in clases:
        q(su, "insert into reserva (id_alumna, id_clase, estado) values (%s, %s, 'confirmada')", (usuarias[k], c))

PROF = ClienteFalso(uri, usuarias["Profe"], ahora="2026-06-30 12:00-03")
rpc(PROF, "sincronizar_borradores", p_mes=6, p_anio=2026)
F = {k: q(su, "select id from resumen_mensual where id_alumna = %s and estado_factura = 'borrador'", (usuarias[k],), True)["id"]
     for k in ALUMNAS}


def fila(fid):
    return q(su, "select * from resumen_mensual where id = %s", (fid,), True)


def confirmar(fid, ahora):
    PROF.reloj(ahora)
    return rpc(PROF, "confirmar_factura", p_id=fid, p_version=fila(fid)["version"])


def anular(fid, ahora, tipo=None, motivo="error comprobado"):
    PROF.reloj(ahora)
    kw = {"p_id": fid, "p_motivo": motivo}
    if tipo:
        kw["p_tipo"] = tipo
    return rpc(PROF, "anular_factura", **kw)


def bonificar_una(fid, ahora):
    """Corrección real: bonifica la primera línea todavía cobrada."""
    PROF.reloj(ahora)
    linea = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s and ajuste_tipo = 'ninguno' order by id limit 1",
              (fid,), True)["id"]
    return rpc(PROF, "ajustar_linea", p_id_linea=linea, p_tipo="bonificada", p_importe=None,
               p_motivo="clase no reservada", p_version=fila(fid)["version"])


# ---------- datos "existentes" antes de la migración (con el código actual) ----------
ok("antes: se confirma una factura con la función actual", confirmar(F["Vieja"], "2026-06-30 12:00-03")["resultado"] == "ok")
r = anular(F["Vieja"], "2026-07-03 10:00-03")
ok("antes: anular y reemitir con la función actual (2 parámetros)", r["resultado"] == "ok", r)
VIEJA_NUEVA = r["id_nueva"]
ok("antes: confirmación tardía (15/07) con la función actual", confirmar(F["Tarde"], "2026-07-15 10:00-03")["resultado"] == "ok")

TABLAS = ["profiles", "horario", "clase", "reserva", "resumen_mensual_detalle", "factura_historial", "pago",
          "pago_anulacion", "tipo_clase", "mp_intento", "mp_evento", "mp_configuracion"]
COLS_RM = ", ".join(c["column_name"] for c in q(su, """select column_name from information_schema.columns
    where table_schema = 'public' and table_name = 'resumen_mensual' order by ordinal_position"""))
antes = {t: huella(su, t) for t in TABLAS}
antes["resumen_mensual"] = huella(su, "resumen_mensual", COLS_RM)
sec_antes = q(su, "select last_value, is_called from factura_numero_seq", uno=True)

# ---------- aplicar la migración ----------
with su.cursor() as cur:
    cur.execute(open(NUEVA[0]).read())
ok("la migración se aplica", True)
despues = {t: huella(su, t) for t in TABLAS}
despues["resumen_mensual"] = huella(su, "resumen_mensual", COLS_RM)
ok("los datos existentes no cambian (todas las columnas anteriores de todas las tablas)", despues == antes,
   [t for t in antes if antes[t] != despues[t]])
ok("el contador de facturas no cambia", q(su, "select last_value, is_called from factura_numero_seq", uno=True) == sec_antes)
ok("datos existentes: factura confirmada el 30/06 → vence el 10/07", fila(F["Vieja"])["vence_el"] == date(2026, 7, 10))
ok("datos existentes: reemisión anterior queda como administrativa (borrador, sin vencimiento)",
   fila(VIEJA_NUEVA)["tipo_reemision"] == "administrativa" and fila(VIEJA_NUEVA)["vence_el"] is None)
ok("datos existentes: confirmación tardía del 15/07 → vence el 25/07", fila(F["Tarde"])["vence_el"] == date(2026, 7, 25))
ok("datos existentes: los borradores quedan sin vencimiento",
   q(su, "select count(*) n from resumen_mensual where estado_factura = 'borrador' and vence_el is not null", uno=True)["n"] == 0)

# ---------- primera emisión ----------
ok("confirmada antes del 1 (30/06) → vence el 10/07 (regla emisión)",
   confirmar(F["Demo"], "2026-06-30 18:00-03")["resultado"] == "ok" and fila(F["Demo"])["vence_el"] == date(2026, 7, 10))
ok("confirmada el día 10 a las 23:59 → vence ese mismo día 10",
   confirmar(F["Ana"], "2026-07-10 23:59-03")["resultado"] == "ok" and fila(F["Ana"])["vence_el"] == date(2026, 7, 10))
ok("confirmada el día 11 a las 00:00 → 10 días desde la confirmación (21/07)",
   confirmar(F["Bea"], "2026-07-11 00:00-03")["resultado"] == "ok" and fila(F["Bea"])["vence_el"] == date(2026, 7, 21))
ok("10/07 23:59 de Montevideo (= 11/07 02:59 UTC) → vence el 10/07: cuenta la fecha de Montevideo",
   confirmar(F["Caro"], "2026-07-11 02:59+00")["resultado"] == "ok" and fila(F["Caro"])["vence_el"] == date(2026, 7, 10))
h = q(su, "select detalle from factura_historial where id_resumen_mensual = %s and accion = 'confirmada'", (F["Bea"],), True)["detalle"]
ok("el historial de la confirmación guarda el vencimiento y la regla",
   h.get("vence_el") == "2026-07-21" and h.get("regla_vencimiento") == "emision_tardia", h)

# ---------- correcciones ----------
confirmar(F["Dani"], "2026-06-30 18:00-03")
r = anular(F["Dani"], "2026-07-02 09:00-03", "correccion", "le cobraron una clase que no reservó")
D1 = r["id_nueva"]
ok("reemisión por corrección: el borrador guarda el tipo", fila(D1)["tipo_reemision"] == "correccion")
hist = {x["accion"]: x for x in q(su, "select accion, detalle, motivo, por from factura_historial where id_resumen_mensual in (%s, %s)",
                                       (F["Dani"], D1))}
ok("historial: anulación y reemisión con tipo, motivo y quién", hist["anulada"]["detalle"].get("tipo") == "correccion"
   and hist["reemision"]["motivo"] == "le cobraron una clase que no reservó" and hist["reemision"]["por"] == usuarias["Profe"])
bonificar_una(D1, "2026-07-02 09:05-03")
ok("corrección real confirmada el 2/11 (acá 02/07) → vence 10 días después (12/07), 1 plazo usado",
   confirmar(D1, "2026-07-02 09:10-03")["resultado"] == "ok" and fila(D1)["vence_el"] == date(2026, 7, 12)
   and fila(D1)["plazos_correccion"] == 1)
D2 = anular(D1, "2026-07-08 10:00-03", "correccion", "otra clase mal cobrada")["id_nueva"]
bonificar_una(D2, "2026-07-08 10:05-03")
confirmar(D2, "2026-07-08 10:10-03")
ok("segunda corrección real (08/07) → vence 18/07, 2 plazos usados",
   fila(D2)["vence_el"] == date(2026, 7, 18) and fila(D2)["plazos_correccion"] == 2)
D3 = anular(D2, "2026-07-15 10:00-03", "correccion", "tercera corrección legítima")["id_nueva"]
bonificar_una(D3, "2026-07-15 10:05-03")
ok("tercera corrección: se puede corregir y confirmar igual", confirmar(D3, "2026-07-15 10:10-03")["resultado"] == "ok")
ok("tercera corrección: NO genera otro plazo (conserva 18/07, sigue en 2)",
   fila(D3)["vence_el"] == date(2026, 7, 18) and fila(D3)["plazos_correccion"] == 2)
h = q(su, "select detalle from factura_historial where id_resumen_mensual = %s and accion = 'confirmada'", (D3,), True)["detalle"]
ok("el historial registra que se alcanzó el límite", h.get("regla_vencimiento") == "correccion_limite", h)

confirmar(F["Eli"], "2026-06-30 18:00-03")
E1 = anular(F["Eli"], "2026-07-02 09:00-03", "correccion", "dijo que era error pero no lo era")["id_nueva"]
confirmar(E1, "2026-07-02 09:10-03")
ok("corrección SIN cambios reales → conserva el vencimiento (10/07) y no usa plazo",
   fila(E1)["vence_el"] == date(2026, 7, 10) and fila(E1)["plazos_correccion"] == 0)

confirmar(F["Fede"], "2026-06-30 18:00-03")
G1 = anular(F["Fede"], "2026-07-02 09:00-03", "administrativa", "reemisión administrativa")["id_nueva"]
bonificar_una(G1, "2026-07-02 09:05-03")
confirmar(G1, "2026-07-02 09:10-03")
ok("reemisión ADMINISTRATIVA (aunque cambie el importe) → conserva el vencimiento (10/07)",
   fila(G1)["vence_el"] == date(2026, 7, 10) and fila(G1)["plazos_correccion"] == 0)

# Anterior anulada antes de publicarse (la alumna nunca la vio): la reemitida es su primera factura.
confirmar(F["Gabi"], "2026-06-30 12:00-03")
H1 = anular(F["Gabi"], "2026-06-30 13:00-03", "correccion", "error detectado antes de publicar")["id_nueva"]
bonificar_una(H1, "2026-06-30 13:05-03")
confirmar(H1, "2026-07-15 10:00-03")
ok("anterior nunca publicada + confirmada el 15/07 → regla de primera emisión (25/07), sin usar plazo",
   fila(H1)["vence_el"] == date(2026, 7, 25) and fila(H1)["plazos_correccion"] == 0)

# Compatibilidad con el código actual de theta (anular sin p_tipo)
confirmar(VIEJA_NUEVA, "2026-07-03 11:00-03")
ok("reemisión anterior a la migración, confirmada después → conserva el vencimiento (10/07)",
   fila(VIEJA_NUEVA)["vence_el"] == date(2026, 7, 10))
r = anular(VIEJA_NUEVA, "2026-07-04 10:00-03")
ok("código actual (anular sin p_tipo) sigue funcionando y queda como administrativa",
   r["resultado"] == "ok" and fila(r["id_nueva"])["tipo_reemision"] == "administrativa", r)

# ---------- validaciones y permisos ----------
ok("tipo inválido → tipo_invalido", anular(F["Demo"], "2026-07-05 10:00-03", "otro")["resultado"] == "tipo_invalido")
ok("motivo vacío → motivo_invalido", anular(F["Demo"], "2026-07-05 10:00-03", "correccion", "  ")["resultado"] == "motivo_invalido")
ALU = ClienteFalso(uri, usuarias["Demo"], ahora="2026-07-20 10:00-03")
ok("una alumna no puede anular ni reemitir", rpc(ALU, "anular_factura", p_id=F["Demo"], p_motivo="xxxx", p_tipo="correccion")["resultado"] == "no_autorizada")
ANON = ClienteFalso(uri, None, rol="anon")
ok("sin sesión: sin permiso para anular", rpc_error(ANON, "anular_factura", p_id=F["Demo"], p_motivo="xxxx", p_tipo="correccion") == "42501")
for fn, kw in (("_vencimiento", {"p_id": F["Demo"], "p_fecha": "2026-07-01"}),
               ("_correccion_con_cambios", {"p_nueva": F["Demo"], "p_anterior": F["Ana"]}),
               ("_dia_vencimiento", {"p_mes": 6, "p_anio": 2026})):
    ok(f"una alumna no puede ejecutar {fn}", rpc_error(ALU, fn, **kw) == "42501")
try:
    q(ALU.conn, "update public.resumen_mensual set vence_el = '2030-01-01' where id = %s", (F["Demo"],))
    ok("una alumna no puede modificar vence_el", False)
except psycopg2.Error as e:
    ok("una alumna no puede modificar vence_el", e.pgcode == "42501")
vista = ALU.table("resumen_mensual").select("id, vence_el, tipo_reemision, plazos_correccion").execute().data
ok("la alumna lee el vencimiento de su factura publicada (RLS)",
   any(v["id"] == F["Demo"] and v["vence_el"] == "2026-07-10" for v in vista), vista)
try:
    q(su, "update resumen_mensual set vence_el = null where id = %s", (F["Demo"],))
    ok("la base exige vencimiento en toda factura confirmada", False)
except psycopg2.Error:
    ok("la base exige vencimiento en toda factura confirmada", True)

# ---------- una factura vencida se sigue pagando ----------
ok("factura vencida (hoy 20/07 > 10/07) sigue 'pendiente' en la base", fila(F["Demo"])["estado_pago"] == "pendiente")
SERV = ClienteFalso(uri, None, rol="service_role", ahora="2026-07-20 10:00-03")
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=True)
r = rpc(ALU, "crear_intento_mp", p_id_resumen=F["Demo"])
ok("vencida: alumna@prueba.invalid puede iniciar el pago con Mercado Pago de prueba", r["resultado"] == "ok", r)
PROF.reloj("2026-07-20 10:00-03")
r = rpc(PROF, "registrar_pago", p_id_resumen=F["Ana"], p_monto=str(fila(F["Ana"])["total"]), p_medio="efectivo",
        p_fecha="2026-07-20")
ok("vencida: la profesora puede registrar el pago", r["resultado"] == "ok" and fila(F["Ana"])["estado_pago"] == "pagado", r)

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261030999999', 'vencimiento_facturas')")
cols_finales = ", ".join(c for c in COLS_RM.split(", "))
datos_antes_vuelta = {t: huella(su, t) for t in TABLAS}
datos_antes_vuelta["resumen_mensual"] = huella(su, "resumen_mensual", cols_finales)
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
datos_despues_vuelta = {t: huella(su, t) for t in TABLAS}
datos_despues_vuelta["resumen_mensual"] = huella(su, "resumen_mensual", cols_finales)
ok("vuelta atrás: no modifica datos (fuera de las columnas nuevas)", datos_antes_vuelta == datos_despues_vuelta)
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'vencimiento_facturas'", uno=True)["n"] == 0)
if RESPALDO:
    # El reloj simulado reemplazó _ahora(); se restaura la original antes de comparar con producción.
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
    for fn, archivo in (("_confirmar(bigint)", "_confirmar_produccion.sql"), ("anular_factura(bigint, text)", "anular_factura_produccion.sql")):
        actual = q(su, f"select pg_get_functiondef('public.{fn}'::regprocedure) d", uno=True)["d"]
        ok(f"vuelta atrás: {fn.split('(')[0]} queda byte a byte como en producción",
           actual == open(os.path.join(RESPALDO, archivo)).read())
r = anular(G1, "2026-07-21 10:00-03")
ok("vuelta atrás: anular y reemitir funciona como antes", r["resultado"] == "ok", r)
with su.cursor() as cur:
    cur.execute(open(NUEVA[0]).read())
ok("después de la vuelta atrás, la migración se puede volver a aplicar",
   q(su, "select count(*) n from resumen_mensual where estado_factura <> 'borrador' and vence_el is null", uno=True)["n"] == 0)

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
