"""Correcciones de asistencia (migración 20261104000000_asistencias_correcciones), en un Postgres LOCAL descartable que
reconstruye producción con una COPIA de sus datos (respaldo fuera del repo) y todas las migraciones anteriores.
Comprueba: que la migración no cambia datos y su vuelta atrás es exacta; que la profesora sigue limitada al horario de
la clase; que la administradora corrige solo clases terminadas, con motivo, todo o nada; el bloqueo por factura
confirmada; el historial; los pendientes; los permisos de cada rol; el aislamiento entre empresas; pedidos
simultáneos; y que nada se marca solo (base para el futuro cobro del 25 % por inasistencia). No toca Supabase.

Uso:  python tests/base_local/test_asistencias_correcciones_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import hashlib
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]
TZ = ZoneInfo("America/Montevideo")
# md5(pg_get_functiondef) de marcar_asistencia en producción el 03/11/2026 (lectura): la vuelta atrás la deja así
MARCAR_PRODUCCION = "28b8fb8ada6a49bc1ee6ec6c5509dfa1"


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


def como(cli, sentencia, args=None):
    try:
        with cli.conn.cursor() as cur:
            cur.execute(sentencia, args)
        return None
    except psycopg2.Error as e:
        return e.pgcode


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


def tablas():
    return [r["table_name"] for r in q(su, "select table_name from information_schema.tables "
                                           "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]


def datos(excluir=("sesion_empresa", "asistencia_historial")):
    return {t: localdb.huella(su, t) for t in tablas() if t not in excluir}


def md5_marcar():
    d = q(su, "select pg_get_functiondef('public.marcar_asistencia(bigint, text)'::regprocedure) d", uno=True)["d"]
    return hashlib.md5(d.encode()).hexdigest()


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG = [a for a in localdb.migraciones() if a.endswith("_asistencias_correcciones.sql")][0]
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_asistencias_correcciones.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(M1):
            cur.execute(open(a).read())
copia_produccion.cargar(su, RESPALDO)
with su.cursor() as cur:
    for a in localdb.migraciones():
        if os.path.basename(M1) <= os.path.basename(a) < os.path.basename(MIG):
            cur.execute(open(a).read())

# ---------- 1) la migración sobre la copia de producción y su vuelta atrás exacta ----------
estructura_antes, datos_antes = huellas_estructura(), datos()
ok("antes de la migración, marcar_asistencia es la de producción", md5_marcar() == MARCAR_PRODUCCION)
with su.cursor() as cur:
    cur.execute(open(MIG).read())
ok("la migración se aplica y no cambia ningún dato (historial vacío)",
   datos() == datos_antes and localdb.huella(su, "asistencia_historial")[0] == 0)
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: estructura idéntica a la de antes", huellas_estructura() == estructura_antes)
ok("vuelta atrás: marcar_asistencia idéntica byte a byte a la de producción", md5_marcar() == MARCAR_PRODUCCION)
ok("vuelta atrás: datos idénticos", datos() == datos_antes)
with su.cursor() as cur:
    cur.execute(open(MIG).read())


# ---------- 2) datos de prueba ----------
def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].split(".")[0].title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADMIN1 = uid("profesora@prueba.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
C = lambda i: ClienteFalso(uri, i)
E2 = rpc(C(SUPER), "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")["id"]
m = lambda mail, rol: cuenta(mail, {"id_empresa": E2, "rol_empresa": rol})
ADMIN2, ADMIN2B = m("admin.pil@prueba.invalid", "admin"), m("admin2.pil@prueba.invalid", "admin")
PROF, PROF3 = m("profe.pil@prueba.invalid", "profesora"), m("otra.pil@prueba.invalid", "profesora")
ALU2, ALU3, ALU4 = m("alu2.pil@prueba.invalid", "alumna"), m("alu3.pil@prueba.invalid", "alumna"), m("alu4.pil@prueba.invalid", "alumna")
ALU5 = m("alu5.pil@prueba.invalid", "alumna")

ahora = datetime.now(TZ)
hoy = ahora.date()
TIPO = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) "
             "values ('PRUEBA Pilates', 60, 900, 5, %s) returning id", (E2,), True)["id"]


def clase(profesora, inicio, estado="programada"):
    h = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
              "values ('domingo', %s, %s, false, %s, %s) returning id", (inicio.time(), TIPO, profesora, E2), True)["id"]
    return q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
                 "id_empresa, id_profesora) values (%s, %s, %s, %s, 60, 900, 5, %s, %s, %s) returning id",
             (h, inicio.date(), inicio.time(), estado, TIPO, E2, profesora), True)["id"]


def reserva(alumna, id_clase):
    return q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) "
                 "returning id", (alumna, id_clase, E2), True)["id"]


def asistencia(id_reserva):
    r = q(su, "select estado from asistencia where id_reserva = %s", (id_reserva,), True)
    return r["estado"] if r else None


def historial(id_reserva):
    return q(su, "select estado_anterior, estado_nuevo, origen, motivo, por::text from asistencia_historial "
                 "where id_reserva = %s order by id", (id_reserva,))


h0 = lambda d, hh: datetime.combine(d, datetime.min.time(), tzinfo=TZ).replace(hour=hh)
PASADA = clase(PROF, h0(hoy - timedelta(days=1), 10))           # terminada (ayer)
R_P = [reserva(a, PASADA) for a in (ALU2, ALU3, ALU4)]
curso_ini = (ahora - timedelta(minutes=10)).replace(second=0, microsecond=0)
CURSO = clase(PROF, curso_ini)                                   # empezó hace 10 minutos
R_C = reserva(ALU2, CURSO)
FUTURA = clase(PROF, h0(hoy + timedelta(days=2), 10))
R_F = reserva(ALU2, FUTURA)
mes_ant = ((hoy.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)).replace(day=10)  # hace dos meses
CERRADA = clase(PROF, h0(mes_ant, 10))                          # mes anterior, con factura confirmada de ALU2
R_CERR_2, R_CERR_3 = reserva(ALU2, CERRADA), reserva(ALU3, CERRADA)
CANCEL = clase(PROF, h0(hoy - timedelta(days=4), 10), estado="cancelada")
R_CANC = reserva(ALU3, CANCEL)
OTRA = clase(PROF3, h0(hoy - timedelta(days=1), 12))            # de otra profesora
R_OTRA = reserva(ALU4, OTRA)
FAC = q(su, "insert into resumen_mensual (id_alumna, mes, anio, id_empresa) values (%s, %s, %s, %s) returning id",
        (ALU2, mes_ant.month, mes_ant.year, E2), True)["id"]
q(su, "set session_replication_role = replica; update resumen_mensual set estado_factura = 'confirmada', "
      "numero = 'PRUEBA-0001', confirmada_en = now(), vence_el = current_date, alumna_nombre = 'A', alumna_apellido = 'B' "
      "where id = %s; set session_replication_role = origin", (FAC,))
ok("datos de prueba: clases pasadas, en curso, futura, cancelada, de otra profesora y una factura confirmada",
   q(su, "select estado_factura from resumen_mensual where id = %s", (FAC,), True)["estado_factura"] == "confirmada")

E1_asist = q(su, "select * from asistencia where id_empresa = %s order by id", (E1,))
A2, A2B, P, P3, AL2, SUP, A1 = C(ADMIN2), C(ADMIN2B), C(PROF), C(PROF3), C(ALU2), C(SUPER), C(ADMIN1)
facturas_antes = {t: localdb.huella(su, t) for t in ("resumen_mensual", "resumen_mensual_detalle", "pago", "factura_historial")}

# ---------- 3) la profesora: solo mientras dura la clase (sin cambios), ahora con historial ----------
ok("profesora marca durante la clase", rpc(P, "marcar_asistencia", p_id_reserva=R_C, p_estado="presente") == "ok"
   and asistencia(R_C) == "presente")
ok("…queda en el historial como marca de la clase, sin motivo",
   [(x["estado_anterior"], x["estado_nuevo"], x["origen"], x["motivo"], x["por"]) for x in historial(R_C)]
   == [(None, "presente", "clase", None, PROF)])
rpc(P, "marcar_asistencia", p_id_reserva=R_C, p_estado="presente")
ok("marcar lo mismo otra vez no agrega historial", len(historial(R_C)) == 1)
ok("cambiarla durante la clase: historial con el valor anterior",
   rpc(P, "marcar_asistencia", p_id_reserva=R_C, p_estado="ausente") == "ok"
   and [(x["estado_anterior"], x["estado_nuevo"]) for x in historial(R_C)] == [(None, "presente"), ("presente", "ausente")])
ok("profesora: clase terminada → fuera de horario (no se amplía el plazo)",
   rpc(P, "marcar_asistencia", p_id_reserva=R_P[0], p_estado="presente") == "fuera_de_horario" and asistencia(R_P[0]) is None)
ok("profesora: clase futura → fuera de horario", rpc(P, "marcar_asistencia", p_id_reserva=R_F, p_estado="presente") == "fuera_de_horario")
ok("la administradora con marcar_asistencia también está limitada al horario",
   rpc(A2, "marcar_asistencia", p_id_reserva=R_P[0], p_estado="presente") == "fuera_de_horario")

# ---------- 4) correcciones: permisos ----------
cambio = lambda r, e="presente": [{"id_reserva": r, "estado": e}]
ok("profesora común no puede corregir", rpc(P, "corregir_asistencias", p_cambios=psycopg2.extras.Json(cambio(R_P[0])),
                                            p_motivo="x")["resultado"] == "no_autorizada")
ok("alumna no puede", rpc(AL2, "corregir_asistencias", p_cambios=psycopg2.extras.Json(cambio(R_P[0])), p_motivo="x")["resultado"]
   == "no_autorizada")
ok("superadministradora no puede (no administra la empresa)",
   rpc(SUP, "corregir_asistencias", p_cambios=psycopg2.extras.Json(cambio(R_P[0])), p_motivo="x")["resultado"] == "no_autorizada")
ok("sin sesión ni siquiera puede ejecutarla",
   rpc(ClienteFalso(uri, None, rol="anon"), "corregir_asistencias", p_cambios=psycopg2.extras.Json(cambio(R_P[0])),
       p_motivo="x").get("error") == "42501")
ok("la administradora de otra empresa no encuentra la reserva",
   rpc(A1, "corregir_asistencias", p_cambios=psycopg2.extras.Json(cambio(R_P[0])), p_motivo="x")["resultado"] == "no_existe"
   and asistencia(R_P[0]) is None)
ok("nadie escribe directo en el historial", como(A2, "insert into public.asistencia_historial (id_empresa, id_reserva, "
   "estado_nuevo, origen, motivo, por) values (%s, %s, 'presente', 'correccion', 'x', %s)", (E2, R_P[0], ADMIN2)) == "42501")
ok("nadie escribe directo en asistencia", como(A2, "update public.asistencia set estado = 'presente' where id_reserva = %s",
                                                (R_C,)) == "42501")

# ---------- 5) correcciones: reglas ----------
J = psycopg2.extras.Json
antes = datos()
ok("motivo obligatorio", rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_P[0])), p_motivo="  ")["resultado"] == "motivo_vacio")
ok("solo presente o ausente (no se vuelve a «sin registrar»)",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_P[0], "sin_registrar")), p_motivo="x")["resultado"] == "estado_invalido")
ok("lista vacía: sin cambios", rpc(A2, "corregir_asistencias", p_cambios=J([]), p_motivo="x")["resultado"] == "sin_cambios")
ok("clase en curso: no terminó", rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_C)), p_motivo="x")["resultado"] == "no_termino")
ok("clase futura: no terminó", rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_F)), p_motivo="x")["resultado"] == "no_termino")
ok("clase cancelada: no se corrige", rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_CANC)), p_motivo="x")["resultado"]
   == "clase_cancelada")
r = rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_P[0]) + cambio(R_C)), p_motivo="x")
ok("todo o nada: si una no se puede, no se guarda ninguna", r["resultado"] == "no_termino" and r["id_reserva"] == R_C
   and asistencia(R_P[0]) is None)
ok("los intentos rechazados no cambiaron nada", datos() == antes)

r = rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_P[0]) + cambio(R_P[1], "ausente") + cambio(R_OTRA)),
        p_motivo="Planilla en papel")
ok("la administradora completa asistencias de clases terminadas (también de otra profesora)",
   r == {"resultado": "ok", "cambios": 3} and asistencia(R_P[0]) == "presente" and asistencia(R_P[1]) == "ausente"
   and asistencia(R_OTRA) == "presente", r)
ok("…cada una queda en el historial con motivo, quién y el valor anterior",
   [(x["estado_anterior"], x["estado_nuevo"], x["origen"], x["motivo"], x["por"]) for x in historial(R_P[1])]
   == [(None, "ausente", "correccion", "Planilla en papel", ADMIN2)])
r = rpc(A2B, "corregir_asistencias", p_cambios=J(cambio(R_P[1], "presente")), p_motivo="Llegó tarde, estaba")
ok("corregir de nuevo: el historial conserva todo (sin registrar → ausente → presente)",
   r["resultado"] == "ok" and [(x["estado_anterior"], x["estado_nuevo"], x["por"]) for x in historial(R_P[1])]
   == [(None, "ausente", ADMIN2), ("ausente", "presente", ADMIN2B)])
ok("mismo valor: sin cambios y sin historial nuevo",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_P[0])), p_motivo="x")["resultado"] == "sin_cambios"
   and len(historial(R_P[0])) == 1)

# ---------- 6) facturas confirmadas ----------
ok("reserva de un mes con la factura de la alumna confirmada: no se corrige",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_CERR_2)), p_motivo="x")["resultado"] == "factura_confirmada"
   and asistencia(R_CERR_2) is None)
ok("…otra alumna de la misma clase sin factura confirmada sí",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_CERR_3)), p_motivo="x")["resultado"] == "ok")
ids = lambda filas: sorted(x if isinstance(x, int) else next(iter(x.values())) for x in filas)
ok("la pantalla sabe cuáles están bloqueadas (solo la administradora)",
   ids(rpc(A2, "reservas_con_factura_confirmada", p_ids=[R_CERR_2, R_CERR_3, R_P[0]])) == [R_CERR_2]
   and rpc(P, "reservas_con_factura_confirmada", p_ids=[R_CERR_2]) == [],
   rpc(A2, "reservas_con_factura_confirmada", p_ids=[R_CERR_2, R_CERR_3, R_P[0]]))
FIN_MES = clase(PROF, h0((mes_ant.replace(day=28)), 19))
R_FIN = reserva(ALU2, FIN_MES)
ok("también al final de ese mes (día 28, 19:00) la factura confirmada bloquea",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_FIN)), p_motivo="x")["resultado"] == "factura_confirmada"
   and asistencia(R_FIN) is None)
q(su, "set session_replication_role = replica; update resumen_mensual set estado_factura = 'anulada', anulada_en = now() "
      "where id = %s; set session_replication_role = origin", (FAC,))
q(su, "insert into resumen_mensual (id_alumna, mes, anio, id_empresa, reemplaza_a, tipo_reemision) values "
      "(%s, %s, %s, %s, %s, 'correccion')", (ALU2, mes_ant.month, mes_ant.year, E2, FAC))
ok("anulada y reemitida (la nueva en borrador): ya se puede corregir",
   rpc(A2, "corregir_asistencias", p_cambios=J(cambio(R_CERR_2, "ausente")), p_motivo="Corrección antes de reemitir")["resultado"]
   == "ok")
fact_ahora = {t: localdb.huella(su, t) for t in ("resumen_mensual_detalle", "pago", "factura_historial")}
ok("corregir asistencias no cambia líneas, pagos ni historial de facturas",
   all(fact_ahora[t] == facturas_antes[t] for t in fact_ahora))

# ---------- 7) pendientes y lecturas por rol ----------
mes = lambda d: {"p_anio": d.year, "p_mes": d.month}
fila = lambda filas, c: next((x for x in filas if x["id_clase"] == c), None)
ra = rpc(A2, "resumen_asistencias_mes", **mes(hoy - timedelta(days=1)))
ok("administradora: las clases terminadas del mes con reservas y faltantes",
   fila(ra, PASADA) and fila(ra, PASADA)["reservas"] == 3 and fila(ra, PASADA)["sin_registrar"] == 1, fila(ra, PASADA))
ok("…sin la clase en curso, la futura ni la cancelada",
   not any(fila(ra, c) for c in (CURSO, FUTURA, CANCEL)))
rp = rpc(P, "resumen_asistencias_mes", **mes(hoy - timedelta(days=1)))
ok("profesora común: solo sus clases", fila(rp, PASADA) and not fila(rp, OTRA))
ok("alumna y otra empresa: nada", rpc(AL2, "resumen_asistencias_mes", **mes(hoy - timedelta(days=1))) == []
   and not fila(rpc(A1, "resumen_asistencias_mes", **mes(hoy - timedelta(days=1))) or [], PASADA))
tot = rpc(A2, "asistencias_pendientes_resumen")
ok("total de pendientes para el aviso de Inicio (1 de esta clase)", tot["asistencias"] >= 1 and tot["clases"] >= 1, tot)
ok("el historial lo lee solo la administradora de la empresa",
   len(A2.table("asistencia_historial").select("id").execute().data) > 0
   and P.table("asistencia_historial").select("id").execute().data == []
   and AL2.table("asistencia_historial").select("id").execute().data == []
   and A1.table("asistencia_historial").select("id").execute().data == [])
hc = rpc(A2, "historial_asistencias_clase", p_id_clase=PASADA)
ok("historial de una clase con nombres (administradora)", len(hc) == 3 and hc[0]["por"] and hc[0]["alumna"], hc[:1])
ok("…la profesora y la otra empresa no lo ven", rpc(P, "historial_asistencias_clase", p_id_clase=PASADA) == []
   and rpc(A1, "historial_asistencias_clase", p_id_clase=PASADA) == [])

# ---------- 8) nada se marca solo (base del futuro 25 % por inasistencia) ----------
lunes = hoy - timedelta(days=hoy.weekday())
rpc(A2, "generar_clases", p_lunes=str(lunes))
rpc(A2, "resumen_asistencias_mes", **mes(hoy))
ok("una reserva pendiente sigue sin asistencia (nunca se marca ausente sola)", asistencia(R_P[2]) is None)
ok("toda asistencia «ausente» tiene un registro explícito en el historial (nadie la puso automáticamente)",
   q(su, "select count(*) n from asistencia a where a.id_empresa = %s and a.estado = 'ausente' and not exists "
         "(select 1 from asistencia_historial h where h.id_reserva = a.id_reserva and h.estado_nuevo = 'ausente')",
     (E2,), True)["n"] == 0)
ok("el historial distingue marca durante la clase y corrección (para decidir el 25 % sobre ausencias explícitas)",
   {x["origen"] for x in q(su, "select distinct origen from asistencia_historial where id_empresa = %s", (E2,))}
   == {"clase", "correccion"})

# ---------- 9) pedidos simultáneos ----------
# (a) dos administradoras corrigen la misma reserva a la vez: el historial queda encadenado sin perder ningún paso
R_X = reserva(ALU4, CERRADA)
res = {}
hilos = [threading.Thread(target=lambda a=a, e=e: res.update({a: rpc(C(a), "corregir_asistencias",
                                                                       p_cambios=J(cambio(R_X, e)), p_motivo="simultánea")}))
         for a, e in ((ADMIN2, "presente"), (ADMIN2B, "ausente"))]
[h.start() for h in hilos]
[h.join() for h in hilos]
hx = historial(R_X)
ok("dos correcciones a la vez: ambas quedan, encadenadas, y el estado final es la última",
   all(r["resultado"] == "ok" for r in res.values()) and len(hx) == 2 and hx[0]["estado_anterior"] is None
   and hx[1]["estado_anterior"] == hx[0]["estado_nuevo"] and asistencia(R_X) == hx[1]["estado_nuevo"], (res, hx))

# (b) corrección mientras se confirma la factura del mes (mismo candado que la facturación): espera y la rechaza
R_Y = reserva(ALU5, CERRADA)
FAC4 = q(su, "insert into resumen_mensual (id_alumna, mes, anio, id_empresa) values (%s, %s, %s, %s) returning id",
         (ALU5, mes_ant.month, mes_ant.year, E2), True)["id"]
salida = {}


def confirmar_lento():
    c = psycopg2.connect(uri)
    with c, c.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtext('facturas_mes'), %s)", (mes_ant.year * 100 + mes_ant.month,))
        cur.execute("set local session_replication_role = replica")
        cur.execute("update resumen_mensual set estado_factura = 'confirmada', numero = 'PRUEBA-0002', confirmada_en = now(), "
                    "vence_el = current_date, alumna_nombre = 'A', alumna_apellido = 'B' where id = %s", (FAC4,))
        time.sleep(1.5)
    c.close()


t1 = threading.Thread(target=confirmar_lento)
t1.start()
time.sleep(0.3)
inicio = time.time()
salida["r"] = rpc(C(ADMIN2), "corregir_asistencias", p_cambios=J(cambio(R_Y)), p_motivo="durante la confirmación")
espera = time.time() - inicio
t1.join()
ok("corrección durante la confirmación de la factura: espera a que termine y la rechaza",
   salida["r"]["resultado"] == "factura_confirmada" and espera > 0.8 and asistencia(R_Y) is None, (salida, round(espera, 2)))

# (c) marca de la profesora durante la clase y corrección de otra clase a la vez: no se bloquean entre sí
res = {}
t1 = threading.Thread(target=lambda: res.update(p=rpc(C(PROF), "marcar_asistencia", p_id_reserva=R_C, p_estado="presente")))
t2 = threading.Thread(target=lambda: res.update(a=rpc(C(ADMIN2), "corregir_asistencias", p_cambios=J(cambio(R_P[2], "ausente")),
                                                       p_motivo="simultánea con la clase")))
t1.start(), t2.start(), t1.join(), t2.join()
ok("marca durante la clase y corrección de otra clase a la vez: las dos se guardan", res.get("p") == "ok"
   and res.get("a", {}).get("resultado") == "ok", res)

# ---------- 10) aislamiento: la Empresa 1 no cambió ----------
ok("la Empresa 1 no tiene historial y sus asistencias son idénticas",
   q(su, "select count(*) n from asistencia_historial where id_empresa = %s", (E1,), True)["n"] == 0
   and q(su, "select * from asistencia where id_empresa = %s order by id", (E1,)) == E1_asist)

# ---------- 11) vuelta atrás con historial: lo conserva ----------
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás con historial: marcar_asistencia vuelve a la de producción y el historial se conserva",
   md5_marcar() == MARCAR_PRODUCCION and q(su, "select count(*) n from asistencia_historial", uno=True)["n"] > 0)

print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
