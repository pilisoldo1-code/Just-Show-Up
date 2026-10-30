"""Restricción de Mercado Pago a una sola alumna (alumna@prueba.invalid), en un Postgres LOCAL
descartable con todas las migraciones del repo en el orden de producción. Comprueba: antes de la
restricción (como hoy en producción), después (solo esa alumna), que no toca datos ni el historial
de pagos, permisos, y que la vuelta atrás deja la función byte a byte como en producción.
No toca Supabase ni Mercado Pago.

Uso (con las dependencias de requirements-dev.txt):  python tests/base_local/test_mp_solo_alumna_demo_base.py
"""
import hashlib
import os
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
# Huella md5 de pg_get_functiondef de crear_intento_mp en producción antes de la restricción (29/10).
HUELLA_PRODUCCION = "e6afed1eddf2e5edb1bf5d031aaa49fd"


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


def definicion():
    return q(su, "select pg_get_functiondef('public.crear_intento_mp(bigint)'::regprocedure) d", uno=True)["d"]


def permisos():
    return q(su, """select string_agg(grantee || ':' || privilege_type, ', ' order by grantee) p
                    from information_schema.routine_privileges
                    where routine_schema = 'public' and routine_name = 'crear_intento_mp'""", uno=True)["p"]


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
RESTRICCION = [a for a in localdb.migraciones() if a.endswith("_mercado_pago_solo_alumna_demo.sql")]
assert len(RESTRICCION) == 1, RESTRICCION
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_solo_alumna_demo.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if a not in RESTRICCION:
            cur.execute(open(a).read())
    # Igual que en Supabase: la tabla de migraciones aplicadas (la usa la vuelta atrás).
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
q(su, localdb.RELOJ)
ok("producción reconstruida: la función es idéntica a la de Supabase (huella del 29/10)",
   hashlib.md5(definicion().encode()).hexdigest() == HUELLA_PRODUCCION, hashlib.md5(definicion().encode()).hexdigest())

usuarias = {}
for clave, mail in (("Profe", "profe@prueba.invalid"), ("Alumna", "alumna@prueba.invalid"), ("Sofia", "sofia@demo.invalid"),
                    ("Valen", "valentina@demo.invalid"), ("Ana", "ana@prueba.invalid"), ("Real", "persona.real@example.com")):
    uid = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (uid, mail, f'{{"nombre": "{clave}", "apellido": "Prueba"}}'))
    usuarias[clave] = uid
q(su, "update profiles set rol = 'profesora' where id = %s", (usuarias["Profe"],))
gim = q(su, "select id from tipo_clase where nombre = 'Gimnasia'", uno=True)["id"]
with su.cursor() as cur:
    cur.execute("set session_replication_role = replica")
    cur.execute("""insert into horario (id, dia_semana, hora_inicio, id_tipo_clase, activo, duracion, cupo_max, precio)
                   overriding system value values (900001, 'domingo', '05:00', %s, false, 60, 7, 800)""", (gim,))
    cur.execute("""insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase)
                   values (900001, '2026-06-14', '05:00', 0, 'programada', 60, 800, 7, %s) returning id""", (gim,))
    id_clase = cur.fetchone()[0]
    cur.execute("set session_replication_role = origin")
ALUMNAS = ("Alumna", "Sofia", "Valen", "Ana", "Real")
for clave in ALUMNAS:
    q(su, "insert into reserva (id_alumna, id_clase, estado) values (%s, %s, 'confirmada')", (usuarias[clave], id_clase))
CIERRE, PUB = "2026-06-30 23:00-03", "2026-07-01 09:00-03"
prof = ClienteFalso(uri, usuarias["Profe"], ahora=CIERRE)
rpc(prof, "sincronizar_borradores", p_mes=6, p_anio=2026)
for f in q(su, "select id, version from resumen_mensual"):
    rpc(prof, "confirmar_factura", p_id=f["id"], p_version=f["version"])
fact = {f["id_alumna"]: f["id"] for f in q(su, "select id, id_alumna from resumen_mensual")}
F = {k: fact[usuarias[k]] for k in ALUMNAS}
ok("preparación: 5 facturas confirmadas y pendientes",
   q(su, "select count(*) n from resumen_mensual where estado_factura = 'confirmada' and estado_pago = 'pendiente'", uno=True)["n"] == 5)
SERV = ClienteFalso(uri, None, rol="service_role", ahora=PUB)
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=True)
cli = {k: ClienteFalso(uri, usuarias[k], ahora=PUB) for k in ALUMNAS + ("Profe",)}
ANON = ClienteFalso(uri, None, rol="anon", ahora=PUB)

# ---------- antes (como hoy en producción) ----------
ok("ANTES: una alumna demo puede iniciar un pago (lo que se quiere cerrar)",
   rpc(cli["Sofia"], "crear_intento_mp", p_id_resumen=F["Sofia"])["resultado"] == "ok")
ok("ANTES: la alumna de la demostración puede iniciar un pago",
   rpc(cli["Alumna"], "crear_intento_mp", p_id_resumen=F["Alumna"])["resultado"] == "ok")
# Un pago registrado y anulado, para comprobar que el historial no cambia.
ref = q(su, "select referencia from mp_intento where id_resumen_mensual = %s", (F["Alumna"],), True)["referencia"]
rpc(SERV, "registrar_pago_mercado_pago", p_referencia=ref, p_id_pago_mp="5001", p_estado="approved",
    p_monto="800", p_moneda="UYU", p_fecha="2026-07-01")
id_pago = q(su, "select id from pago where id_resumen_mensual = %s", (F["Alumna"],), True)["id"]
rpc(ClienteFalso(uri, usuarias["Profe"], ahora=PUB), "anular_pago", p_id_pago=id_pago, p_motivo="prueba")
permisos_antes, def_antes = permisos(), definicion()
TABLAS = ["profiles", "horario", "clase", "reserva", "resumen_mensual", "resumen_mensual_detalle", "pago",
          "pago_anulacion", "factura_historial", "tipo_clase", "mp_intento", "mp_evento", "mp_configuracion"]
antes = {t: huella(su, t) for t in TABLAS}
sec_antes = q(su, "select last_value, is_called from factura_numero_seq", uno=True)

# ---------- aplicar la restricción ----------
with su.cursor() as cur:
    cur.execute(open(RESTRICCION[0]).read())
ok("la migración se aplica", True)
ok("la migración no modifica ningún dato (ni intentos, pagos, anulaciones, facturas ni el interruptor)",
   {t: huella(su, t) for t in TABLAS} == antes)
ok("la migración no cambia el contador de facturas",
   q(su, "select last_value, is_called from factura_numero_seq", uno=True) == sec_antes)
ok("permisos iguales: solo authenticated (y service_role/postgres) pueden ejecutarla", permisos() == permisos_antes, permisos())
lineas_antes = [l for l in def_antes.splitlines() if "invalid" not in l]
lineas_despues = [l for l in definicion().splitlines() if "invalid" not in l]
ok("la función solo cambia en la condición del mail", lineas_antes == lineas_despues)

# ---------- después ----------
r = rpc(cli["Alumna"], "crear_intento_mp", p_id_resumen=F["Alumna"])
ok("alumna@prueba.invalid: puede iniciar el pago de su factura", r["resultado"] == "ok" and float(r["monto"]) == 800, r)
intentos_antes = q(su, "select count(*) n from mp_intento", uno=True)["n"]
for clave, nombre in (("Sofia", "alumna demo sofia@demo.invalid"), ("Valen", "alumna demo valentina@demo.invalid"),
                      ("Ana", "otra alumna @prueba.invalid"), ("Real", "alumna con mail real")):
    ok(f"{nombre}: alumna_no_habilitada con SU factura",
       rpc(cli[clave], "crear_intento_mp", p_id_resumen=F[clave])["resultado"] == "alumna_no_habilitada")
ok("ninguna de ellas creó un intento", q(su, "select count(*) n from mp_intento", uno=True)["n"] == intentos_antes)
ok("una alumna demo tampoco puede con la factura de la alumna habilitada",
   rpc(cli["Sofia"], "crear_intento_mp", p_id_resumen=F["Alumna"])["resultado"] == "alumna_no_habilitada")
ok("la alumna habilitada no puede pagar facturas ajenas: no_existe",
   rpc(cli["Alumna"], "crear_intento_mp", p_id_resumen=F["Sofia"])["resultado"] == "no_existe")
ok("profesora: no_autorizada", rpc(cli["Profe"], "crear_intento_mp", p_id_resumen=F["Alumna"])["resultado"] == "no_autorizada")
ok("sin sesión: sin permiso (42501)", rpc_error(ANON, "crear_intento_mp", p_id_resumen=F["Alumna"]) == "42501")
q(su, "update profiles set mail = 'alumna@prueba.invalid.com' where id = %s", (usuarias["Ana"],))
ok("mail que solo se parece: alumna_no_habilitada",
   rpc(cli["Ana"], "crear_intento_mp", p_id_resumen=F["Ana"])["resultado"] == "alumna_no_habilitada")
q(su, "update profiles set mail = 'ana@prueba.invalid' where id = %s", (usuarias["Ana"],))
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=False)
ok("con Mercado Pago apagado, ni la alumna habilitada: deshabilitado",
   rpc(cli["Alumna"], "crear_intento_mp", p_id_resumen=F["Alumna"])["resultado"] == "deshabilitado")
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=True)
ok("el pago anulado y su anulación siguen intactos",
   q(su, "select estado from pago where id = %s", (id_pago,), True)["estado"] == "anulado"
   and huella(su, "pago_anulacion") == antes["pago_anulacion"])

# ---------- la migración se puede volver a aplicar (idempotente) ----------
def_restringida = definicion()
with su.cursor() as cur:
    cur.execute(open(RESTRICCION[0]).read())
ok("aplicarla dos veces deja lo mismo", definicion() == def_restringida and permisos() == permisos_antes)

# ---------- vuelta atrás ----------
q(su, "insert into supabase_migrations.schema_migrations values ('20261029999999', 'mercado_pago_solo_alumna_demo')")
datos_antes_vuelta = {t: huella(su, t) for t in TABLAS}
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: la función queda byte a byte como en producción",
   hashlib.md5(definicion().encode()).hexdigest() == HUELLA_PRODUCCION)
ok("vuelta atrás: permisos iguales a los de producción", permisos() == permisos_antes)
ok("vuelta atrás: quita la migración del registro",
   q(su, "select count(*) n from supabase_migrations.schema_migrations where name = 'mercado_pago_solo_alumna_demo'", uno=True)["n"] == 0)
ok("vuelta atrás: no modifica datos", {t: huella(su, t) for t in TABLAS} == datos_antes_vuelta)
ok("vuelta atrás: la alumna demo vuelve a poder (como antes)",
   rpc(cli["Valen"], "crear_intento_mp", p_id_resumen=F["Valen"])["resultado"] == "ok")
with su.cursor() as cur:
    cur.execute(open(RESTRICCION[0]).read())
ok("se puede volver a aplicar después de la vuelta atrás",
   definicion() == def_restringida
   and rpc(cli["Sofia"], "crear_intento_mp", p_id_resumen=F["Sofia"])["resultado"] == "alumna_no_habilitada")

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
