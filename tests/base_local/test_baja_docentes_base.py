"""Baja de docentes (migración 20261103000000_baja_docentes), en un Postgres LOCAL descartable que reconstruye
producción con una COPIA de sus datos (respaldo fuera del repo) y todas las migraciones anteriores. Comprueba:
que la migración no cambia datos y su vuelta atrás es exacta, la vista previa sin escrituras, la firma (si algo
cambió no se hace nada), qué se cancela y qué se protege (clases empezadas, asistencias, facturas, pagos), los
permisos de cada rol, que la empresa nunca quede sin administradora, los caminos que esquivaban la baja, la
reactivación, pedidos simultáneos y el aislamiento entre empresas. No toca Supabase.

Uso:  python tests/base_local/test_baja_docentes_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import threading
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


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


def rpc(cli, f, **kw):
    try:
        return cli.rpc(f, kw).execute().data
    except psycopg2.Error as e:
        return {"error": e.pgcode, "mensaje": e.pgerror}


def como(cli, sentencia, args=None):
    """SQL directo con la sesión (y RLS) de una usuaria: devuelve None o el código de error."""
    try:
        with cli.conn.cursor() as cur:
            cur.execute(sentencia, args)
        return None
    except psycopg2.Error as e:
        return (e.pgcode, (e.pgerror or "").split("\n")[0])


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


def tablas():
    return [r["table_name"] for r in q(su, "select table_name from information_schema.tables "
                                           "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]


def datos(excluir=("sesion_empresa",)):
    return {t: localdb.huella(su, t) for t in tablas() if t not in excluir}


def de_empresa(empresa):
    """Huella de todas las filas de una empresa (para comprobar que una baja en otra no la toca)."""
    res = {}
    for t in tablas():
        if q(su, "select 1 from information_schema.columns where table_schema = 'public' and table_name = %s "
                 "and column_name = 'id_empresa'", (t,)) and t != "sesion_empresa":
            filas = q(su, f"select * from public.{t} where id_empresa = %s order by 1", (empresa,))
            res[t] = localdb.hashlib.sha256(localdb.json.dumps(filas, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return res


def funciones(nombres):
    return {n: q(su, "select pg_get_functiondef(p.oid) d from pg_proc p join pg_namespace s on s.oid = p.pronamespace "
                     "where s.nspname = 'public' and p.proname = %s", (n,), True)["d"] for n in nombres}


def membresia(uid, empresa):
    r = q(su, "select es_admin, es_profesora, es_alumna, activa from membresia where id_usuario = %s and id_empresa = %s",
          (uid, empresa), True)
    return (r["es_admin"], r["es_profesora"], r["es_alumna"], r["activa"]) if r else None


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG = [a for a in localdb.migraciones() if a.endswith("_baja_docentes.sql")][0]
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_baja_docentes.sql")
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
MODIFICADAS = ["_proteger_empresa", "_clase_bloqueada", "generar_clases", "cambiar_estado_membresia", "quitar_bloqueo"]
estructura_antes, datos_antes, funciones_antes = huellas_estructura(), datos(), funciones(MODIFICADAS)
with su.cursor() as cur:
    cur.execute(open(MIG).read())
ok("la migración se aplica sobre la copia de producción", True)
ok("la migración no cambia ningún dato (solo agrega la tabla de bajas, vacía)",
   {t: h for t, h in datos().items() if t != "baja_docente"} == datos_antes and localdb.huella(su, "baja_docente")[0] == 0)
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: estructura idéntica a la de antes", huellas_estructura() == estructura_antes)
# huellas (md5 de pg_get_functiondef) de esas funciones en producción el 02/11/2026, leídas en modo lectura: la
# vuelta atrás tiene que dejarlas exactamente así (la copia local de antes trae los comentarios de los archivos)
PRODUCCION = {"_clase_bloqueada": "5b7a4e65081a404616c75e1c91a5f788", "_proteger_empresa": "d8f68d86f4cbed9939139d6126aab559",
              "cambiar_estado_membresia": "0e99680c4359b8abcff6aed3618c725f",
              "generar_clases": "7d2bb768f75c2c2b143b77f65889df82", "quitar_bloqueo": "271ec3425f7248448e7911b145ccd345"}
sin_comentarios = lambda d: {n: "\n".join(l for l in t.split("\n") if not l.strip().startswith("--")) for n, t in d.items()}
despues_vuelta = funciones(MODIFICADAS)
ok("vuelta atrás: las 5 funciones modificadas quedan idénticas a las de producción (byte a byte)",
   {n: localdb.hashlib.md5(t.encode()).hexdigest() for n, t in despues_vuelta.items()} == PRODUCCION,
   [n for n, t in despues_vuelta.items() if localdb.hashlib.md5(t.encode()).hexdigest() != PRODUCCION[n]])
ok("vuelta atrás: y con el mismo código que antes de la migración (sin contar comentarios)",
   sin_comentarios(despues_vuelta) == sin_comentarios(funciones_antes))
ok("vuelta atrás: datos idénticos", datos() == datos_antes)
with su.cursor() as cur:
    cur.execute(open(MIG).read())


# ---------- 2) datos de prueba: Empresa 2 con dos administradoras, profesoras y alumnas ----------
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
PROF2, PROF3 = m("profe.pil@prueba.invalid", "profesora"), m("otra.pil@prueba.invalid", "profesora")
ALU2, ALU3, ALU4 = m("alumna.pil@prueba.invalid", "alumna"), m("alumna3.pil@prueba.invalid", "alumna"), \
    m("alumna4.pil@prueba.invalid", "alumna")
ok("datos de prueba: dos administradoras, dos profesoras y tres alumnas en la Empresa 2",
   membresia(ADMIN2, E2) == (True, False, False, True) and membresia(PROF2, E2) == (False, True, False, True))
# PROF2 también es profesora en la Empresa 1 (para el aislamiento entre empresas)
q(su, "insert into membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna, activa) "
      "values (%s, %s, false, true, false, true)", (PROF2, E1))

ahora = datetime.now(TZ)
hoy = ahora.date()
TIPO = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) "
             "values ('PRUEBA Pilates', 60, 900, 3, %s) returning id", (E2,), True)["id"]
TIPO1 = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa, activo) "
              "values ('PRUEBA E1', 60, 1, 3, %s, false) returning id", (E1,), True)["id"]
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def horario(profesora, dia, hora, empresa=None, tipo=None, activo=True):
    return q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
                 "values (%s, %s, %s, %s, %s, %s) returning id",
             (dia, hora, tipo or TIPO, activo, profesora, empresa or E2), True)["id"]


def clase(id_horario, fecha, hora, profesora, empresa=None, tipo=None):
    return q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
                 "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 900, 3, %s, %s, %s) returning id",
             (id_horario, fecha, hora, tipo or TIPO, empresa or E2, profesora), True)["id"]


def reserva(alumna, id_clase, empresa=None):
    return q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) "
                 "returning id", (alumna, id_clase, empresa or E2), True)["id"]


en_curso = (ahora - timedelta(minutes=10)).replace(second=0, microsecond=0)
H2 = horario(PROF2, "sabado", "10:00")
H2B = horario(PROF2, "domingo", "11:00")
H3 = horario(PROF3, "sabado", "12:00")
F1 = clase(H2, hoy + timedelta(days=7), "10:00", PROF2)        # futura con reservas y lista de espera
F2 = clase(H2B, hoy + timedelta(days=8), "11:00", PROF2)       # futura sin reservas
F3 = clase(H2, hoy + timedelta(days=9), "10:00", PROF2)        # futura, la cancela una licencia antes de la baja
CURSO = clase(H2, en_curso.date(), en_curso.time(), PROF2)    # empezó hace 10 minutos: no se toca
PASADA = clase(H2, hoy - timedelta(days=5), "10:00", PROF2)    # pasada, con asistencia
OTRA = clase(H3, hoy + timedelta(days=7), "12:00", PROF3)      # de otra profesora, misma fecha
OTRA9 = clase(H3, hoy + timedelta(days=9), "12:00", PROF3)
R_F1 = [reserva(ALU2, F1), reserva(ALU3, F1), reserva(ALU4, F1)]   # F1 queda completa (cupo 3)
q(su, "update clase set cantidad_inscriptas = 3 where id = %s", (F1,))
q(su, "insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) "
      "select %s, %s, 1, 'en_espera', %s", (ADMIN2B, F1, E2))  # cualquier persona sirve como fila de espera
R_CURSO = reserva(ALU2, CURSO)
R_PASADA = reserva(ALU3, PASADA)
q(su, "insert into asistencia (id_reserva, estado, id_empresa) values (%s, 'presente', %s)", (R_PASADA, E2))
R_OTRA = reserva(ALU2, OTRA)
reserva(ALU3, OTRA9)
# en la Empresa 1, PROF2 también da una clase futura
H_E1 = horario(PROF2, "domingo", "07:00", E1, TIPO1, activo=False)
F_E1 = clase(H_E1, hoy + timedelta(days=7), "07:00", PROF2, E1, TIPO1)

A2, A2B, P2, P3, AL2, SUP = C(ADMIN2), C(ADMIN2B), C(PROF2), C(PROF3), C(ALU2), C(SUPER)
# una licencia de la administradora cancela F3 y OTRA9 antes de la baja (para probar que quitarla no reabre F3)
LIC = rpc(A2, "crear_bloqueo", p_tipo="licencia", p_desde=str(hoy + timedelta(days=9)),
          p_hasta=str(hoy + timedelta(days=9)), p_id_horario=None, p_motivo="PRUEBA licencia")
ok("licencia previa: cancela las clases de ese día (de las dos profesoras)", LIC.get("resultado") == "ok" and LIC["clases"] == 2, LIC)

# ---------- 3) permisos de la vista previa ----------
ok("profesora común no puede ver la vista previa", rpc(P3, "previsualizar_baja_docente", p_id_usuario=PROF2)["resultado"] == "no_autorizada")
ok("alumna no puede", rpc(AL2, "previsualizar_baja_docente", p_id_usuario=PROF2)["resultado"] == "no_autorizada")
ok("sin sesión (anon) ni siquiera puede ejecutarla",
   rpc(ClienteFalso(uri, None, rol="anon"), "previsualizar_baja_docente", p_id_usuario=PROF2).get("error") == "42501")
ok("la administradora de otra empresa no ve a la profesora de esta",
   rpc(C(ADMIN1), "previsualizar_baja_docente", p_id_usuario=PROF3)["resultado"] == "no_existe")
ok("la administradora no puede darse de baja a sí misma",
   rpc(A2, "previsualizar_baja_docente", p_id_usuario=ADMIN2)["resultado"] == "no_puede_desactivarse")
ok("una administradora no da de baja a otra administradora",
   rpc(A2, "previsualizar_baja_docente", p_id_usuario=ADMIN2B)["resultado"] == "es_administradora"
   and rpc(A2, "dar_de_baja_docente", p_id_usuario=ADMIN2B, p_motivo="x", p_firma="x")["resultado"] == "es_administradora")
ok("una alumna no se «da de baja» como docente",
   rpc(A2, "previsualizar_baja_docente", p_id_usuario=ALU2)["resultado"] == "no_es_profesora")
ok("la superadministradora no usa la baja de profesoras (no es administradora de la empresa)",
   rpc(SUP, "previsualizar_baja_docente", p_id_usuario=PROF2)["resultado"] == "no_autorizada")
ok("la administradora no puede usar la baja de administradoras (solo la superadministradora)",
   rpc(A2, "dar_de_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_motivo="x", p_firma="x")["resultado"]
   == "no_autorizada")

# ---------- 4) vista previa: qué se cancela, sin escribir nada ----------
antes = datos()
V = rpc(A2, "previsualizar_baja_docente", p_id_usuario=PROF2)
ok("vista previa: no escribe nada", datos() == antes)
ok("vista previa: sus 2 horarios activos", [h["id"] for h in V["horarios"]] == [H2, H2B], V["horarios"])
ok("vista previa: solo sus clases futuras programadas (no la cancelada por licencia, ni la en curso, ni la pasada)",
   [c["id"] for c in V["clases"]] == [F1, F2], V["clases"])
ok("vista previa: 3 reservas de 3 alumnas, con sus nombres, y 1 en lista de espera",
   V["reservas"] == 3 and V["alumnas"] == 3 and len(V["nombres"]) == 3 and V["espera"] == 1, V)
ok("vista previa: avisa la clase en curso (que no se cancela)", V["en_curso"] == 1, V["en_curso"])
ok("vista previa: no incluye nada de la Empresa 1", all(c["id"] != F_E1 for c in V["clases"]))

# ---------- 5) el camino viejo ya no esquiva la baja ----------
ok("«Dejar inactiva» directo se niega si tiene horarios o clases futuras",
   rpc(A2, "cambiar_estado_membresia", p_id_usuario=PROF2, p_activa=False) == "pendientes:horarios,clases_futuras"
   and membresia(PROF2, E2)[3])

# ---------- 6) firma: si algo cambió desde la vista previa, no se hace nada ----------
F2_ALU = rpc(C(ALU4), "reservar", p_id_clase=F2)
antes = datos()
r = rpc(A2, "dar_de_baja_docente", p_id_usuario=PROF2, p_motivo="PRUEBA baja", p_firma=V["firma"])
ok("una reserva nueva después de la vista previa: la baja no se hace", r["resultado"] == "cambio" and datos() == antes, r)
ok("motivo vacío: no se hace", rpc(A2, "dar_de_baja_docente", p_id_usuario=PROF2, p_motivo="  ", p_firma=V["firma"])["resultado"]
   == "motivo_vacio")
ok("firma inventada: no se hace", rpc(A2, "dar_de_baja_docente", p_id_usuario=PROF2, p_motivo="x", p_firma="abc")["resultado"]
   == "cambio" and datos() == antes)

# ---------- 7) la baja ----------
E1_antes = de_empresa(E1)
pasado_antes = {t: localdb.huella(su, t) for t in ("asistencia", "resumen_mensual", "resumen_mensual_detalle", "pago",
                                                     "pago_anulacion", "factura_historial", "mp_intento")}
V = rpc(A2, "previsualizar_baja_docente", p_id_usuario=PROF2)
r = rpc(A2, "dar_de_baja_docente", p_id_usuario=PROF2, p_motivo="PRUEBA baja", p_firma=V["firma"])
ok("la baja se hace con la vista previa al día", r == {"resultado": "ok", "horarios": 2, "clases": 2, "reservas": 4, "espera": 1}, r)
ok("su membresía queda inactiva (sigue siendo profesora)", membresia(PROF2, E2) == (False, True, False, False))
ok("sus horarios quedan inactivos",
   q(su, "select bool_or(activo) a from horario where id in %s", ((H2, H2B),), True)["a"] is False)
est = {r["id"]: r["estado"] for r in q(su, "select id, estado from clase where id in %s", ((F1, F2, F3, CURSO, PASADA, OTRA),))}
ok("sus clases futuras quedan canceladas", est[F1] == est[F2] == "cancelada", est)
ok("la clase en curso y la pasada no se tocan", est[CURSO] == est[PASADA] == "programada", est)
ok("las clases de otra profesora no se tocan", est[OTRA] == "programada")
B = q(su, "select * from bloqueo where tipo = 'baja' and id_profesora = %s", (PROF2,), True)
res = q(su, "select id, estado, id_bloqueo from reserva where id_clase in %s", ((F1, F2),))
ok("las reservas futuras quedan canceladas por la baja (no se cobran: canceladas por la empresa)",
   all(x["estado"] == "cancelada" and x["id_bloqueo"] == B["id"] for x in res) and len(res) == 4, res)
ok("la lista de espera queda expirada",
   q(su, "select estado from listadeespera where id_clase = %s", (F1,), True)["estado"] == "expirado")
ok("la reserva de la clase en curso sigue confirmada (se cobra como siempre)",
   q(su, "select estado from reserva where id = %s", (R_CURSO,), True)["estado"] == "confirmada")
ok("asistencias, facturas, pagos y Mercado Pago quedan idénticos",
   {t: localdb.huella(su, t) for t in pasado_antes} == pasado_antes)
ok("la Empresa 1 queda idéntica (también la clase de PROF2 allá) y PROF2 sigue activa allá",
   de_empresa(E1) == E1_antes and membresia(PROF2, E1) == (False, True, False, True))
aud = q(su, "select * from baja_docente where id_usuario = %s", (PROF2,), True)
ok("queda registrada (quién, motivo, horarios y cantidades)",
   aud and aud["por"] == ADMIN2 and aud["motivo"] == "PRUEBA baja" and sorted(aud["horarios"]) == [H2, H2B]
   and aud["clases"] == 2 and not aud["era_administradora"])
ok("la registración solo la leen la administradora (y la superadministradora)",
   len(A2.table("baja_docente").select("id").execute().data) == 1 and P3.table("baja_docente").select("id").execute().data == []
   and C(ADMIN1).table("baja_docente").select("id").execute().data == []
   and len(SUP.table("baja_docente").select("id").execute().data) == 1)
ok("repetir la baja: ya está inactiva", rpc(A2, "dar_de_baja_docente", p_id_usuario=PROF2, p_motivo="x", p_firma="x")["resultado"]
   == "ya_inactiva")

# ---------- 8) los caminos que reabrían algo de una docente inactiva ----------
ok("la baja no se puede eliminar desde Planificación", rpc(A2, "quitar_bloqueo", p_id=B["id"])["resultado"] == "es_baja")
r = rpc(A2, "quitar_bloqueo", p_id=LIC["id"])
est = {r["id"]: r["estado"] for r in q(su, "select id, estado from clase where id in %s", ((F3, OTRA9),))}
ok("quitar la licencia previa reabre la clase de la otra profesora, pero no la de la docente dada de baja",
   r.get("resultado") == "ok" and est[OTRA9] == "programada" and est[F3] == "cancelada", (r, est))
ok("activar un horario suyo: lo rechaza la base", como(A2, "update public.horario set activo = true where id = %s", (H2,))
   and "profesora_inactiva" in como(A2, "update public.horario set activo = true where id = %s", (H2,))[1])
ok("crear un horario para ella: lo rechaza la base",
   "profesora_inactiva" in (como(A2, "insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora) "
                                     "values ('lunes', '20:00', %s, true, %s)", (TIPO, PROF2)) or ("", ""))[1])
ok("pasarle a ella un horario activo de otra profesora: lo rechaza la base",
   "profesora_inactiva" in (como(A2, "update public.horario set id_profesora = %s where id = %s", (PROF2, H3)) or ("", ""))[1])
ok("reabrir a mano una de sus clases: lo rechaza la base",
   "profesora_inactiva" in (como(A2, "update public.clase set estado = 'programada' where id = %s", (F2,)) or ("", ""))[1])
# un horario suyo activo que se coló (por ejemplo, cargado a mano por fuera de la app): no genera clases
q(su, "set session_replication_role = replica; update horario set activo = true where id = %s; "
      "set session_replication_role = origin", (H2,))
lunes = hoy - timedelta(days=hoy.weekday())
for k in range(5):
    rpc(A2, "generar_clases", p_lunes=str(lunes + timedelta(days=7 * k)))
ok("generar clases no crea clases programadas para una docente inactiva",
   q(su, "select count(*) n from clase where id_profesora = %s and id_empresa = %s and estado = 'programada' "
         "and fecha > %s", (PROF2, E2, hoy), True)["n"] == 0)
q(su, "set session_replication_role = replica; update horario set activo = false where id = %s; "
      "set session_replication_role = origin", (H2,))
ok("reservar una de sus clases canceladas: no se puede", rpc(C(ALU2), "reservar", p_id_clase=F1) != "ok")

# ---------- 9) reactivar no restaura nada ----------
ok("reactivarla: vuelve a estar activa", rpc(A2, "cambiar_estado_membresia", p_id_usuario=PROF2, p_activa=True) == "ok"
   and membresia(PROF2, E2)[3])
ok("…sus horarios siguen inactivos y sus clases canceladas (se reactivan a mano)",
   q(su, "select bool_or(activo) a from horario where id in %s", ((H2, H2B),), True)["a"] is False
   and q(su, "select estado from clase where id = %s", (F1,), True)["estado"] == "cancelada")
ok("…y ahora sí se puede activar un horario suyo", como(A2, "update public.horario set activo = true where id = %s", (H2B,)) is None)
rpc(A2, "generar_clases", p_lunes=str(lunes + timedelta(days=14)))
ok("…que genera clases programadas (la baja ya no la cubre)",
   q(su, "select count(*) n from clase where id_horario = %s and estado = 'programada' and fecha > %s", (H2B, hoy + timedelta(days=8)),
     True)["n"] >= 1)

# ---------- 10) administradoras: solo la superadministradora, nunca la última ----------
H_A = horario(ADMIN2, "lunes", "09:00")
F_A = clase(H_A, hoy + timedelta(days=10), "09:00", ADMIN2)
reserva(ALU2, F_A)
V = rpc(SUP, "previsualizar_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2)
ok("superadministradora: vista previa con cantidades y sin nombres de alumnas",
   V["resultado"] == "ok" and V["reservas"] == 1 and V["nombres"] == [], V)
ok("la administradora común no puede ver esa vista previa",
   rpc(A2B, "previsualizar_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2)["resultado"] == "no_autorizada")
E1_antes = de_empresa(E1)
r = rpc(SUP, "dar_de_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_motivo="PRUEBA baja admin", p_firma=V["firma"])
ok("la superadministradora da de baja a una administradora (con otra activa)", r.get("resultado") == "ok" and r["clases"] == 1, r)
ok("pasa a profesora inactiva", membresia(ADMIN2, E2) == (False, True, False, False))
ok("su clase futura y su reserva quedan canceladas",
   q(su, "select estado from clase where id = %s", (F_A,), True)["estado"] == "cancelada"
   and q(su, "select estado from reserva where id_clase = %s", (F_A,), True)["estado"] == "cancelada")
ok("la Empresa 1 no se toca", de_empresa(E1) == E1_antes)
como(SUP, "update public.horario set activo = false where id = %s", (H3,))
ok("la marca de la superadministradora no queda abierta: fuera de la baja no puede tocar la empresa",
   q(su, "select activo from horario where id = %s", (H3,), True)["activo"] is True)
V = rpc(SUP, "previsualizar_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B)
ok("la última administradora activa no se puede dar de baja",
   V["resultado"] == "ultima_administradora"
   and rpc(SUP, "dar_de_baja_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_motivo="x", p_firma="x")["resultado"]
   == "ultima_administradora" and membresia(ADMIN2B, E2)[3])

# dos bajas simultáneas de las dos únicas administradoras activas: solo una puede salir
ADMIN2C = m("admin3.pil@prueba.invalid", "admin")
firmas = {a: rpc(SUP, "previsualizar_baja_administradora", p_id_empresa=E2, p_id_usuario=a)["firma"] for a in (ADMIN2B, ADMIN2C)}
resultados = {}


def baja_admin(a):
    resultados[a] = rpc(C(SUPER), "dar_de_baja_administradora", p_id_empresa=E2, p_id_usuario=a, p_motivo="PRUEBA", p_firma=firmas[a])


hilos = [threading.Thread(target=baja_admin, args=(a,)) for a in (ADMIN2B, ADMIN2C)]
[h.start() for h in hilos]
[h.join() for h in hilos]
ok("dos bajas de administradoras a la vez: una sale y la otra queda (nunca sin administradora)",
   sorted(r["resultado"] for r in resultados.values()) == ["ok", "ultima_administradora"]
   and q(su, "select count(*) n from membresia where id_empresa = %s and es_admin and activa", (E2,), True)["n"] == 1, resultados)
QUEDA = ADMIN2B if membresia(ADMIN2B, E2)[0] else ADMIN2C
AQ = C(QUEDA)

# ---------- 11) concurrencia de la baja de profesoras ----------
def nueva_profesora(n):
    p = m(f"conc{n}.pil@prueba.invalid", "profesora")
    h = horario(p, DIAS[n % 7], "18:00")
    c = clase(h, hoy + timedelta(days=12 + n), "18:00", p)
    return p, h, c


# (a) una alumna reserva mientras se da la baja: o la reserva entra antes (y la baja se frena por la firma) o
#     la clase ya está cancelada; nunca una reserva confirmada en una clase cancelada
inconsistencias, desenlaces = 0, set()
for n in range(6):
    p, h, c = nueva_profesora(n)
    firma = rpc(AQ, "previsualizar_baja_docente", p_id_usuario=p)["firma"]
    salida = {}
    t1 = threading.Thread(target=lambda: salida.update(baja=rpc(C(QUEDA), "dar_de_baja_docente", p_id_usuario=p,
                                                                  p_motivo="PRUEBA", p_firma=firma)))
    t2 = threading.Thread(target=lambda: salida.update(res=rpc(C(ALU3), "reservar", p_id_clase=c)))
    t1.start(), t2.start(), t1.join(), t2.join()
    desenlaces.add((salida["baja"]["resultado"], salida["res"] if isinstance(salida["res"], str) else "error"))
    estado = q(su, "select estado from clase where id = %s", (c,), True)["estado"]
    confirmadas = q(su, "select count(*) n from reserva where id_clase = %s and estado = 'confirmada'", (c,), True)["n"]
    activa = membresia(p, E2)[3]
    inconsistencias += (estado == "cancelada" and confirmadas > 0) or (activa == (estado == "cancelada"))
ok("reserva simultánea a la baja (6 veces): siempre consistente", inconsistencias == 0, desenlaces)

# (b) otra administradora crea un horario para ella mientras se da la baja: nunca queda inactiva con un horario activo
inconsistencias = 0
for n in range(6, 10):
    p, h, c = nueva_profesora(n)
    firma = rpc(AQ, "previsualizar_baja_docente", p_id_usuario=p)["firma"]
    t1 = threading.Thread(target=lambda: rpc(C(QUEDA), "dar_de_baja_docente", p_id_usuario=p, p_motivo="PRUEBA", p_firma=firma))
    t2 = threading.Thread(target=lambda: como(C(QUEDA), "insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, "
                                                        "activo, id_profesora) values ('martes', '21:00', %s, true, %s)", (TIPO, p)))
    t1.start(), t2.start(), t1.join(), t2.join()
    activos = q(su, "select count(*) n from horario where id_profesora = %s and activo", (p,), True)["n"]
    inconsistencias += (not membresia(p, E2)[3]) and activos > 0
ok("horario creado a la vez que la baja (4 veces): nunca una inactiva con horarios activos", inconsistencias == 0)

# (c) dos administradoras dan la misma baja a la vez: una la hace, la otra encuentra que ya está inactiva
ADMIN2D = m("admin4.pil@prueba.invalid", "admin")
p, h, c = nueva_profesora(10)
firma = rpc(AQ, "previsualizar_baja_docente", p_id_usuario=p)["firma"]
salida = []
hilos = [threading.Thread(target=lambda a=a: salida.append(rpc(C(a), "dar_de_baja_docente", p_id_usuario=p, p_motivo="PRUEBA",
                                                                 p_firma=firma)["resultado"])) for a in (QUEDA, ADMIN2D)]
[x.start() for x in hilos]
[x.join() for x in hilos]
ok("la misma baja dos veces a la vez: una sola se hace", sorted(salida) == ["ok", "ya_inactiva"], salida)
ok("…y se registra una sola vez", q(su, "select count(*) n from baja_docente where id_usuario = %s", (p,), True)["n"] == 1)

# ---------- 12) la vuelta atrás se niega si ya hay bajas ----------
try:
    with su.cursor() as cur:
        cur.execute(open(VUELTA).read())
    negada = False
except psycopg2.Error:
    with su.cursor() as cur:
        cur.execute("rollback")  # el script abre su propia transacción
    negada = True
ok("con bajas registradas, la vuelta atrás se niega (no se pierde historial)", negada
   and q(su, "select count(*) n from baja_docente", uno=True)["n"] > 0)

print(f"\n{total_casos - fallas}/{total_casos} casos OK")
srv.cleanup() if hasattr(srv, "cleanup") else None
sys.exit(1 if fallas else 0)
