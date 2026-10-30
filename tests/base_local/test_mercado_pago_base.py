"""Mercado Pago (solo prueba) en un Postgres LOCAL descartable: migración, permisos, Vault,
intentos de pago, registro idempotente y concurrencia. No toca Supabase ni Mercado Pago:
el resultado de cada pago "consultado" se pasa a mano, como lo haría el servidor.

Uso (con las dependencias de requirements-dev.txt):  python tests/base_local/test_mercado_pago_base.py
"""
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
TOKEN = "APP_USR-token-de-prueba-ficticio"


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def error_de(conn, sql, args=None):
    try:
        q(conn, sql, args)
        return None
    except psycopg2.Error as e:
        return e.pgcode or "error"


def rpc(c, f, **kw):
    return c.rpc(f, kw).execute().data


def rpc_error(c, f, **kw):
    try:
        rpc(c, f, **kw)
        return None
    except psycopg2.Error as e:
        return e.pgcode or "error"


# ---------- base con todas las migraciones anteriores y una factura confirmada ----------
srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
NUEVA = [a for a in localdb.migraciones() if a.endswith("_mercado_pago_prueba.sql")]
assert len(NUEVA) == 1, NUEVA
# Esta prueba cubre la migración original de Mercado Pago (cualquier alumna ficticia puede pagar).
# La restricción posterior a una sola alumna se prueba en test_mp_solo_alumna_demo_base.py.
POSTERIORES = [a for a in localdb.migraciones() if os.path.basename(a) > os.path.basename(NUEVA[0])]
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if a not in NUEVA and a not in POSTERIORES:
            cur.execute(open(a).read())
q(su, localdb.RELOJ)

usuarias = {}
for clave, mail in (("Profe", "profe@prueba.invalid"), ("Sofia", "sofia@demo.invalid"), ("Ana", "ana@prueba.invalid"),
                    ("Real", "persona.real@example.com")):
    uid = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (uid, mail, f'{{"nombre": "{clave}", "apellido": "Prueba"}}'))
    usuarias[clave] = uid
PROF, SOFIA, ANA, REAL = (usuarias[k] for k in ("Profe", "Sofia", "Ana", "Real"))
q(su, "update profiles set rol = 'profesora' where id = %s", (PROF,))
gim = q(su, "select id from tipo_clase where nombre = 'Gimnasia'", uno=True)["id"]
contador = [0]


def clase(fecha, hora, precio):
    contador[0] += 1
    with su.cursor() as cur:
        cur.execute("set session_replication_role = replica")
        cur.execute("""insert into horario (id, dia_semana, hora_inicio, id_tipo_clase, activo, duracion, cupo_max, precio)
                       overriding system value values (%s, 'domingo', '05:00', %s, false, 60, 7, 1)""", (900000 + contador[0], gim))
        cur.execute("""insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase)
                       values (%s, %s, %s, 0, 'programada', 60, %s, 7, %s) returning id""", (900000 + contador[0], fecha, hora, precio, gim))
        i = cur.fetchone()[0]
        cur.execute("set session_replication_role = origin")
    return i


for alumna in (SOFIA, ANA, REAL):
    for dia in ("2026-06-10", "2026-06-17"):
        q(su, "insert into reserva (id_alumna, id_clase, estado) values (%s, %s, 'confirmada')", (alumna, clase(dia, "08:00", 800)))

CIERRE, ANTES_PUB, PUB = "2026-06-30 23:00-03", "2026-06-30 23:30-03", "2026-07-01 09:00-03"
p = ClienteFalso(uri, PROF, ahora=CIERRE)
rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
for f in q(su, "select id, version from resumen_mensual"):
    rpc(p, "confirmar_factura", p_id=f["id"], p_version=f["version"])
fact = {f["id_alumna"]: f for f in q(su, "select * from resumen_mensual")}
F_SOFIA, F_ANA, F_REAL = fact[SOFIA]["id"], fact[ANA]["id"], fact[REAL]["id"]
ok("preparación: 3 facturas confirmadas y pendientes de $1600",
   len(fact) == 3 and all(f["estado_factura"] == "confirmada" and f["estado_pago"] == "pendiente"
                          and float(f["total"]) == 1600 for f in fact.values()))

TABLAS = ["profiles", "horario", "clase", "reserva", "resumen_mensual", "resumen_mensual_detalle", "pago",
          "factura_historial", "tipo_clase"]
antes = {t: huella(su, t) for t in TABLAS}
with su.cursor() as cur:
    cur.execute(open(NUEVA[0]).read())
ok("la migración se aplica después de todas las demás", True)
ok("la migración no modifica ningún dato existente", {t: huella(su, t) for t in TABLAS} == antes)
ok("Mercado Pago queda APAGADO", q(su, "select habilitado, modo from mp_configuracion", uno=True) == {"habilitado": False, "modo": "prueba"})

ANON = ClienteFalso(uri, None, rol="anon")
SERV = ClienteFalso(uri, None, rol="service_role", ahora=PUB)
PROFE = ClienteFalso(uri, PROF, ahora=PUB)
AL_SOFIA = ClienteFalso(uri, SOFIA, ahora=PUB)
AL_ANA = ClienteFalso(uri, ANA, ahora=PUB)
AL_REAL = ClienteFalso(uri, REAL, ahora=PUB)

# ---------- el token: solo el servidor ----------
ok("servidor: guarda el Access Token de prueba en Vault", rpc(SERV, "mp_guardar_secreto", p_nombre="mp_access_token", p_valor=TOKEN) == "ok")
ok("servidor: nombre de secreto no permitido -> nombre_invalido",
   rpc(SERV, "mp_guardar_secreto", p_nombre="otra_cosa", p_valor="x") == "nombre_invalido")
ok("en Vault queda un solo secreto con ese nombre", q(su, "select count(*) n from vault.secrets where name = 'mp_access_token'", uno=True)["n"] == 1)
cfg = rpc(SERV, "mp_configuracion_servidor")
ok("servidor: lee la configuración (apagado + token)", cfg["habilitado"] is False and cfg["access_token"] == TOKEN)
rpc(SERV, "mp_guardar_secreto", p_nombre="mp_access_token", p_valor=TOKEN + "-renovado")
ok("servidor: reemplazar el token no duplica el secreto",
   q(su, "select count(*) n from vault.secrets where name = 'mp_access_token'", uno=True)["n"] == 1
   and rpc(SERV, "mp_configuracion_servidor")["access_token"] == TOKEN + "-renovado")
rpc(SERV, "mp_guardar_secreto", p_nombre="mp_access_token", p_valor=TOKEN)

for nombre, cli in (("sin sesión (anon)", ANON), ("alumna", AL_SOFIA), ("profesora", PROFE)):
    ok(f"{nombre}: no puede leer la configuración del servidor", rpc_error(cli, "mp_configuracion_servidor") == "42501")
    ok(f"{nombre}: no puede guardar credenciales", rpc_error(cli, "mp_guardar_secreto", p_nombre="mp_access_token", p_valor="x") == "42501")
    ok(f"{nombre}: no puede encender Mercado Pago", rpc_error(cli, "mp_cambiar_habilitado", p_habilitado=True) == "42501")
    ok(f"{nombre}: no puede registrar pagos de Mercado Pago",
       rpc_error(cli, "registrar_pago_mercado_pago", p_referencia="x", p_id_pago_mp="1", p_estado="approved",
                 p_monto="1600", p_moneda="UYU", p_fecha="2026-07-01") == "42501")
    ok(f"{nombre}: no puede leer Vault", error_de(cli.conn, "select decrypted_secret from vault.decrypted_secrets") == "42501")
    ok(f"{nombre}: no puede leer la tabla de configuración", error_de(cli.conn, "select * from public.mp_configuracion") == "42501")
ok("sin sesión: no puede iniciar un pago", rpc_error(ANON, "crear_intento_mp", p_id_resumen=F_SOFIA) == "42501")
ok("alumna: no puede escribir pagos ni intentos directo",
   error_de(AL_SOFIA.conn, "insert into public.mp_intento (id_resumen_mensual, id_alumna, referencia, monto) values (%s, %s, 'x', 1)",
            (F_SOFIA, SOFIA)) == "42501"
   and error_de(AL_SOFIA.conn, "update public.pago set monto = 1") == "42501")

# ---------- iniciar un pago (alumna) ----------
ok("con Mercado Pago apagado: deshabilitado", rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "deshabilitado")
ok("servidor: enciende Mercado Pago", rpc(SERV, "mp_cambiar_habilitado", p_habilitado=True) is True)
ok("antes de publicarse la factura: no_disponible",
   rpc(ClienteFalso(uri, SOFIA, ahora=ANTES_PUB), "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "no_disponible")
ok("alumna con mail real: alumna_no_habilitada", rpc(AL_REAL, "crear_intento_mp", p_id_resumen=F_REAL)["resultado"] == "alumna_no_habilitada")
ok("factura de otra alumna: no_existe", rpc(AL_ANA, "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "no_existe")
ok("profesora: no_autorizada", rpc(PROFE, "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "no_autorizada")
i1 = rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)
ok("alumna ficticia con su factura publicada: ok, con el importe de la factura",
   i1["resultado"] == "ok" and float(i1["monto"]) == 1600 and i1["referencia"].startswith(f"JSU-{F_SOFIA}-"), i1)
i2 = rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)
ok("cada intento tiene una referencia distinta", i2["referencia"] != i1["referencia"])
ok("la alumna ve sus intentos (y no los de otras)",
   len(AL_SOFIA.table("mp_intento").select("*").execute().data) == 2 and AL_ANA.table("mp_intento").select("*").execute().data == [])
ok("la profesora ve todos los intentos", len(PROFE.table("mp_intento").select("*").execute().data) == 2)
ok("sin sesión no ve intentos", error_de(ANON.conn, "select * from public.mp_intento") == "42501")
rpc(SERV, "mp_guardar_preferencia", p_id_intento=i1["id_intento"], p_preference_id="pref-1")
ok("servidor: guarda la preferencia del intento", q(su, "select preference_id from mp_intento where id = %s", (i1["id_intento"],), True)["preference_id"] == "pref-1")


def registrar(ref, id_mp, estado="approved", monto="1600", moneda="UYU", fecha="2026-07-01", cli=None):
    return rpc(cli or SERV, "registrar_pago_mercado_pago", p_referencia=ref, p_id_pago_mp=id_mp, p_estado=estado,
               p_monto=monto, p_moneda=moneda, p_fecha=fecha)


def estado_factura(fid):
    return q(su, "select estado_pago from resumen_mensual where id = %s", (fid,), True)["estado_pago"]


def pagos(fid):
    return q(su, "select * from pago where id_resumen_mensual = %s order by id", (fid,))


# ---------- registrar resultados (servidor, después de consultar a Mercado Pago) ----------
ok("referencia desconocida: intento_desconocido", registrar("JSU-no-existe", "999")["resultado"] == "intento_desconocido")
ok("pago rechazado: rechazado", registrar(i1["referencia"], "1001", "rejected")["resultado"] == "rechazado")
ok("…la factura sigue pendiente y sin pago", estado_factura(F_SOFIA) == "pendiente" and pagos(F_SOFIA) == [])
ok("…el intento queda rechazado", q(su, "select estado from mp_intento where id = %s", (i1["id_intento"],), True)["estado"] == "rechazado")
ok("pago en proceso: pendiente, sin cambios", registrar(i1["referencia"], "1002", "in_process")["resultado"] == "pendiente"
   and estado_factura(F_SOFIA) == "pendiente")
ok("aprobado con otro importe: a_revisar, sin pago", registrar(i1["referencia"], "1003", monto="1599")["resultado"] == "a_revisar"
   and pagos(F_SOFIA) == [])
q(su, "update mp_intento set estado = 'rechazado' where id = %s", (i1["id_intento"],))
ok("aprobado en otra moneda: a_revisar, sin pago", registrar(i1["referencia"], "1004", moneda="ARS")["resultado"] == "a_revisar"
   and pagos(F_SOFIA) == [])
q(su, "update mp_intento set estado = 'rechazado' where id = %s", (i1["id_intento"],))
r = registrar(i1["referencia"], "1005", fecha="2026-07-02")
ps = pagos(F_SOFIA)
ok("aprobado y correcto (reintento después de un rechazo): ok", r["resultado"] == "ok", r)
ok("…se registra un pago de Mercado Pago con su número de operación",
   len(ps) == 1 and ps[0]["medio_pago"] == "mercado_pago" and ps[0]["id_transaccion"] == "1005"
   and float(ps[0]["monto"]) == 1600 and ps[0]["estado"] == "vigente" and str(ps[0]["registrado_por"]) == SOFIA)
ok("…con fecha no futura (la de hoy si Mercado Pago informa una posterior)", str(ps[0]["fecha"]) == "2026-07-01")
ok("…la factura pasa a pagada", estado_factura(F_SOFIA) == "pagado")
ok("…el intento queda aprobado y vinculado al pago",
   q(su, "select estado, id_pago from mp_intento where id = %s", (i1["id_intento"],), True) == {"estado": "aprobado", "id_pago": ps[0]["id"]})
ok("la alumna ve el pago", len(AL_SOFIA.table("pago").select("*").eq("id_resumen_mensual", F_SOFIA).execute().data) == 1)
ok("el mismo aviso otra vez: ya_registrado, sin duplicar", registrar(i1["referencia"], "1005")["resultado"] == "ya_registrado"
   and len(pagos(F_SOFIA)) == 1)
ok("otro pago aprobado para la misma factura (otro intento): a_revisar, sin duplicar",
   registrar(i2["referencia"], "1006")["resultado"] == "a_revisar" and len(pagos(F_SOFIA)) == 1)
ok("factura pagada: no se puede iniciar otro pago", rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "ya_pagada")
ok("la profesora no puede anular la factura con el pago vigente (regla existente)",
   rpc(PROFE, "anular_factura", p_id=F_SOFIA, p_motivo="probando")["resultado"] == "tiene_pago")
ok("registro manual con medio mercado_pago: datos_invalidos (solo lo registra el servidor)",
   rpc(PROFE, "registrar_pago", p_id_resumen=F_ANA, p_monto="1600", p_medio="mercado_pago", p_fecha="2026-07-01")["resultado"] == "datos_invalidos")
ok("un pago mercado_pago sin número de operación no puede existir",
   error_de(su, "insert into pago (id_resumen_mensual, monto, medio_pago, fecha) values (%s, 1600, 'mercado_pago', '2026-07-01')", (F_ANA,)) == "23514")

# ---------- anular y volver a pagar (para ensayar varias veces) ----------
ok("la profesora anula el pago de Mercado Pago", rpc(PROFE, "anular_pago", p_id_pago=ps[0]["id"], p_motivo="ensayo de la defensa")["resultado"] == "ok")
ok("…la factura vuelve a pendiente", estado_factura(F_SOFIA) == "pendiente")
ok("el mismo pago anulado no se puede volver a registrar", registrar(i1["referencia"], "1005")["resultado"] == "ya_registrado"
   and estado_factura(F_SOFIA) == "pendiente" and len(pagos(F_SOFIA)) == 1)
i3 = rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)
ok("nuevo intento y nuevo pago aprobado: ok", registrar(i3["referencia"], "1007")["resultado"] == "ok" and estado_factura(F_SOFIA) == "pagado"
   and len([x for x in pagos(F_SOFIA) if x["estado"] == "vigente"]) == 1)

# ---------- pago manual primero ----------
i_ana = rpc(AL_ANA, "crear_intento_mp", p_id_resumen=F_ANA)
ok("la profesora registra un pago manual mientras la alumna está en Mercado Pago",
   rpc(PROFE, "registrar_pago", p_id_resumen=F_ANA, p_monto="1600", p_medio="efectivo", p_fecha="2026-07-01")["resultado"] == "ok")
ok("después llega el pago de Mercado Pago: a_revisar (no se duplica)",
   registrar(i_ana["referencia"], "2001")["resultado"] == "a_revisar" and len(pagos(F_ANA)) == 1)
ok("…queda el motivo para la profesora",
   q(su, "select detalle from mp_intento where referencia = %s", (i_ana["referencia"],), True)["detalle"] == "la factura ya tenía un pago")

# ---------- concurrencia ----------
pago_ana = pagos(F_ANA)[0]["id"]
rpc(PROFE, "anular_pago", p_id_pago=pago_ana, p_motivo="volver a probar")
i_c = rpc(AL_ANA, "crear_intento_mp", p_id_resumen=F_ANA)


def en_paralelo(llamadas):
    clis = [ClienteFalso(uri, None, rol="service_role", ahora=PUB) for _ in llamadas]
    with ThreadPoolExecutor(len(llamadas)) as ex:
        return list(ex.map(lambda par: registrar(*par[0], cli=par[1]), zip(llamadas, clis)))


res = en_paralelo([(i_c["referencia"], "3001")] * 4)
ok("el mismo pago informado 4 veces a la vez (vuelta + avisos): un solo registro",
   sorted(r["resultado"] for r in res) == ["ok", "ya_registrado", "ya_registrado", "ya_registrado"]
   and len([x for x in pagos(F_ANA) if x["estado"] == "vigente"]) == 1, res)
rpc(PROFE, "anular_pago", p_id_pago=[x for x in pagos(F_ANA) if x["estado"] == "vigente"][0]["id"], p_motivo="otra vez")
ia = rpc(AL_ANA, "crear_intento_mp", p_id_resumen=F_ANA)
ib = rpc(AL_ANA, "crear_intento_mp", p_id_resumen=F_ANA)
res = en_paralelo([(ia["referencia"], "4001"), (ib["referencia"], "4002")])
ok("dos pagos distintos aprobados a la vez para la misma factura: uno ok y otro a_revisar",
   sorted(r["resultado"] for r in res) == ["a_revisar", "ok"]
   and len([x for x in pagos(F_ANA) if x["estado"] == "vigente"]) == 1, res)

# ---------- eventos y apagado ----------
rpc(SERV, "mp_registrar_evento", p_origen="aviso", p_tipo="payment", p_id_recurso="4001", p_firma="sin_clave", p_resultado="ok")
ok("servidor: registra eventos; la profesora los ve y la alumna no",
   len(PROFE.table("mp_evento").select("*").execute().data) == 1 and AL_ANA.table("mp_evento").select("*").execute().data == [])
ok("alumna: no puede registrar eventos", rpc_error(AL_ANA, "mp_registrar_evento", p_origen="aviso", p_tipo="x",
                                                  p_id_recurso="x", p_firma="valida", p_resultado="x") == "42501")
rpc(SERV, "mp_cambiar_habilitado", p_habilitado=False)
ok("apagado de nuevo: no se pueden iniciar pagos", rpc(AL_SOFIA, "crear_intento_mp", p_id_resumen=F_SOFIA)["resultado"] == "deshabilitado")
ok("las tablas de clases y reservas no cambiaron con nada de esto",
   all(huella(su, t) == antes[t] for t in ("horario", "clase", "reserva", "profiles", "tipo_clase")))

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
