"""Cobro del 25 % por inasistencia (migración 20261105000000_cobro_inasistencia), en un Postgres LOCAL descartable que
reconstruye producción con una COPIA de sus datos (respaldo fuera del repo) y todas las migraciones anteriores.
Comprueba: que la migración no cambia datos (solo agrega la fecha de Just Show Up) y su vuelta atrás es exacta; el
cálculo (ausente 25 %, presente y sin registrar 100 %, redondeo, fecha de inicio por empresa); ajustes y descuentos
sobre el importe base; que un cambio de asistencia actualiza el borrador y queda en el historial; que una factura
confirmada nunca cambia; «Anular y reemitir» con la asistencia corregida sin perder historial ni tocar pagos; la
configuración de la fecha (solo la superadministradora, nunca hacia atrás); permisos; aislamiento entre empresas;
pedidos simultáneos; y que sin fecha (o antes de ella) todo se cobra como hoy. No toca Supabase.

Uso:  python tests/base_local/test_cobro_inasistencia_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import hashlib
import os
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
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
J = psycopg2.extras.Json
# md5(pg_get_functiondef) en producción el 03/11/2026 (lectura): la vuelta atrás las deja así
PRODUCCION = {
    "_cobrables(integer, integer)": "2a1a1a93e92416b894a5af9031504766",
    "_factura_al_dia(bigint)": "7dbbd564cd229b5a1ed4f57c1d97faa9",
    "sincronizar_borradores(integer, integer)": "dfd098390ee5ebec6aef78e619a7c232",
    "ajustar_linea(bigint, text, numeric, text, integer)": "ae148178f5a365e63876e4dbda5a03ec",
    "quitar_ajuste_linea(bigint, text, integer)": "4e037449c4f6a4c524875eb587eccf02",
    "anular_factura(bigint, text, text)": "a101318be72cdb4e62406ebf3fc57a2a",
}


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


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


def tablas():
    return [r["table_name"] for r in q(su, "select table_name from information_schema.tables "
                                           "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]


def datos(excluir=("sesion_empresa",)):
    return {t: localdb.huella(su, t) for t in tablas() if t not in excluir}


def md5s():
    return {f: hashlib.md5(q(su, "select pg_get_functiondef(%s::regprocedure) d", ("public." + f,), True)["d"].encode())
            .hexdigest() for f in PRODUCCION}


def acl_cobrables():
    return q(su, "select proacl::text a from pg_proc where proname = '_cobrables'", uno=True)["a"]


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG = [a for a in localdb.migraciones() if a.endswith("_cobro_inasistencia.sql")][0]
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_cobro_inasistencia.sql")
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
estructura_antes, datos_antes, acl_antes = huellas_estructura(), datos(), acl_cobrables()
COLS_DET = ", ".join(r["column_name"] for r in q(su, "select column_name from information_schema.columns where table_schema = "
                                                     "'public' and table_name = 'resumen_mensual_detalle' order by ordinal_position"))
COLS_EMP = ", ".join(r["column_name"] for r in q(su, "select column_name from information_schema.columns where table_schema = "
                                                     "'public' and table_name = 'empresa' order by ordinal_position"))
det_antes, emp_antes = localdb.huella(su, "resumen_mensual_detalle", COLS_DET), localdb.huella(su, "empresa", COLS_EMP)
ok("antes de la migración, las 6 funciones son las de producción", md5s() == PRODUCCION, md5s())
ok("…y _cobrables solo la ejecutan postgres y service_role", acl_antes == "{postgres=X/postgres,service_role=X/postgres}")
with su.cursor() as cur:
    cur.execute(open(MIG).read())
d = datos()
NUEVAS = ("empresa", "resumen_mensual_detalle")
ok("la migración no cambia ningún dato (salvo las columnas nuevas de empresas y líneas)",
   {t: v for t, v in d.items() if t not in NUEVAS} == {t: v for t, v in datos_antes.items() if t not in NUEVAS}
   and localdb.huella(su, "resumen_mensual_detalle", COLS_DET) == det_antes
   and localdb.huella(su, "empresa", COLS_EMP) == emp_antes)
ok("…las líneas existentes quedan al 100 % y sin asistencia (no se toca nada facturado)",
   q(su, "select count(*) n from resumen_mensual_detalle where porcentaje <> 100 or asistencia is not null", uno=True)["n"] == 0)
ok("…Just Show Up rige desde el 1/11/2026; las demás empresas, sin fecha",
   [(r["codigo"], r["d"]) for r in q(su, "select codigo, cobro_inasistencia_desde d from empresa order by id")]
   == [("JSU", date(2026, 11, 1))])
ok("…_cobrables sigue sin poder ejecutarse con sesión", acl_cobrables() == acl_antes)
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: estructura idéntica a la de antes", huellas_estructura() == estructura_antes)
ok("vuelta atrás: las 6 funciones idénticas byte a byte a las de producción", md5s() == PRODUCCION)
ok("vuelta atrás: permisos de _cobrables iguales", acl_cobrables() == acl_antes)
ok("vuelta atrás: datos idénticos", datos() == datos_antes)
with su.cursor() as cur:
    cur.execute(open(MIG).read())


# ---------- 2) datos de prueba: empresa «Pilates Centro», mes cerrado de hace dos meses ----------
def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, J({"nombre": mail.split("@")[0].split(".")[0].title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADMIN1 = uid("profesora@prueba.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
C = lambda i: ClienteFalso(uri, i)
E2 = rpc(C(SUPER), "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")["id"]
m = lambda mail, rol: cuenta(mail, {"id_empresa": E2, "rol_empresa": rol})
ADMIN2, PROF = m("admin.pil@prueba.invalid", "admin"), m("profe.pil@prueba.invalid", "profesora")
ALU2, ALU3, ALU4 = (m(f"alu{i}.pil@prueba.invalid", "alumna") for i in (2, 3, 4))

hoy = datetime.now(TZ).date()
MES = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)  # último día de hace dos meses
MES, ANIO = MES.month, MES.year
DESDE = date(ANIO, MES, 15)
q(su, "update empresa set cobro_inasistencia_desde = %s where id = %s", (DESDE, E2))  # en el pasado: solo por SQL
TIPO = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) "
             "values ('PRUEBA Pilates', 60, 800, 8, %s) returning id", (E2,), True)["id"]


def clase(dia, precio=800, hora=10):
    h = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
              "values ('domingo', %s, %s, false, %s, %s) returning id", (f"{hora}:00", TIPO, PROF, E2), True)["id"]
    return q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
                 "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, %s, 8, %s, %s, %s) returning id",
             (h, date(ANIO, MES, dia), f"{hora}:00", precio, TIPO, E2, PROF), True)["id"]


def reserva(alumna, id_clase, asistencia=None):
    r = q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) "
              "returning id", (alumna, id_clase, E2), True)["id"]
    if asistencia:
        q(su, "insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, E2))
    return r


C10, C14, C15, C16, C17, C19, C20 = clase(10), clase(14), clase(15), clase(16), clase(17), clase(19, 850), clase(20, 0)
R = {
    "antes": reserva(ALU2, C10, "ausente"),      # antes de la fecha de inicio → 100 %
    "dia14": reserva(ALU2, C14, "ausente"),      # el día anterior → 100 %
    "dia15": reserva(ALU2, C15, "ausente"),      # el mismo día de inicio → 25 %
    "presente": reserva(ALU2, C16, "presente"),  # 100 %
    "sin": reserva(ALU2, C17),                   # sin registrar → 100 %
    "850": reserva(ALU2, C19, "ausente"),        # 25 % de 850 = 212,50
    "cero": reserva(ALU2, C20, "ausente"),       # clase de $0 → 0
}
R3 = {"a": reserva(ALU3, C15, "ausente"), "b": reserva(ALU3, C16, "presente"), "c": reserva(ALU3, C17, "ausente")}
R4 = {"a": reserva(ALU4, C16, "ausente"), "b": reserva(ALU4, C17, "presente")}
A2_SU = ClienteFalso(uri, ADMIN2, rol="postgres")
A2, P, AL2, AL3, SUP, A1 = C(ADMIN2), C(PROF), C(ALU2), C(ALU3), C(SUPER), C(ADMIN1)


def factura(alumna, estado="borrador"):
    return q(su, "select * from resumen_mensual where id_alumna = %s and mes = %s and anio = %s and estado_factura = %s",
             (alumna, MES, ANIO, estado), True)


def lineas(fid):
    return {x["id_reserva"]: x for x in q(su, "select * from resumen_mensual_detalle where id_resumen_mensual = %s", (fid,))}


def al_dia(fid):
    with A2_SU.conn.cursor() as cur:  # sesión de la administradora (empresa activa), sin el rol authenticated
        cur.execute("select public._factura_al_dia(%s)", (fid,))
        return cur.fetchone()[0]


def corregir(r, estado, motivo="Planilla"):
    return rpc(A2, "corregir_asistencias", p_cambios=J([{"id_reserva": r, "estado": estado}]), p_motivo=motivo)


FACT = ("resumen_mensual", "resumen_mensual_detalle", "pago", "factura_historial")
datos_e1_solo = lambda: {t: q(su, f"select md5(coalesce(string_agg(x::text, ',' order by x.id), '')) h from {t} x "
                                  "where x.id_empresa = %s", (E1,), True)["h"] for t in FACT}
datos_e1 = datos_e1_solo()

# ---------- 3) cálculo ----------
s = rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
ok("la administradora arma los borradores del mes", s.get("resultado") == "ok" and s["creadas"] == 3, s)
F2 = factura(ALU2)
L = lineas(F2["id"])
esperado = {"antes": (100, "ausente", 800), "dia14": (100, "ausente", 800), "dia15": (25, "ausente", 200),
            "presente": (100, "presente", 800), "sin": (100, None, 800), "850": (25, "ausente", Decimal("212.50")),
            "cero": (25, "ausente", 0)}
for k, (pct, asis, imp) in esperado.items():
    x = L[R[k]]
    ok(f"línea «{k}»: {pct} %, asistencia {asis}, importe {imp}",
       (x["porcentaje"], x["asistencia"], x["importe_final"], x["ajuste_tipo"]) == (pct, asis, Decimal(imp), "ninguno"), x)
ok("presente y sin registrar nunca se presumen ausencia (100 %)", L[R["sin"]]["porcentaje"] == 100)
ok("totales: original 4.850, cobrado 3.612,50 (sin descuento)",
   (F2["subtotal_original"], F2["total_lineas"], F2["total"]) == (Decimal(4850), Decimal("3612.50"), Decimal("3612.50")), F2)
ok("el precio original se conserva en cada línea (para mostrar ~~$800~~ $200)", L[R["dia15"]]["precio_original"] == 800)
ok("la factura está al día con la asistencia", al_dia(F2["id"]))
ok("_cobrables no se puede ejecutar con sesión", rpc(A2, "_cobrables", p_mes=MES, p_anio=ANIO).get("error") == "42501")
ok("_importe_base: 800→200, 850→212,50, 900→225, 0→0, 100 % sin cambios",
   [q(su, "select public._importe_base(%s, %s) v", a, True)["v"] for a in ((800, 25), (850, 25), (900, 25), (0, 25), (850, 100))]
   == [200, Decimal("212.50"), 225, 0, 850])
s2 = rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
ok("sincronizar de nuevo sin cambios no toca nada", s2 == {"resultado": "ok", "creadas": 0, "actualizadas": 0}
   and factura(ALU2)["version"] == F2["version"], s2)

# ---------- 4) ajustes por clase y descuentos sobre el importe base ----------
v = lambda: factura(ALU2)["version"]
aj = lambda r, tipo, imp, version=None: rpc(A2, "ajustar_linea", p_id_linea=L[r]["id"], p_tipo=tipo, p_importe=imp,
                                             p_motivo="Prueba", p_version=version or v())
ok("«cobrar menos» en una inasistencia: no puede superar $200", aj(R["dia15"], "importe", 250)["resultado"] == "importe_invalido")
ok("…ni ser igual al importe base", aj(R["dia15"], "importe", 200)["resultado"] == "importe_invalido")
ok("…$150 sí", aj(R["dia15"], "importe", 150)["resultado"] == "ok" and lineas(F2["id"])[R["dia15"]]["importe_final"] == 150)
h = q(su, "select detalle from factura_historial where id_resumen_mensual = %s and accion = 'ajuste_linea' order by id desc",
      (F2["id"],), True)["detalle"]
ok("…el historial guarda el porcentaje y el importe anterior (200)", h["porcentaje"] == 25 and Decimal(str(h["antes"])) == 200, h)
r = rpc(A2, "quitar_ajuste_linea", p_id_linea=L[R["dia15"]]["id"], p_motivo="Prueba", p_version=v())
ok("quitar el ajuste vuelve al importe base ($200, no $800)",
   r["resultado"] == "ok" and lineas(F2["id"])[R["dia15"]]["importe_final"] == 200 and lineas(F2["id"])[R["dia15"]]["ajuste_tipo"] == "ninguno")
ok("bonificar una inasistencia: $0", aj(R["850"], "bonificada", None)["resultado"] == "ok"
   and lineas(F2["id"])[R["850"]]["importe_final"] == 0)
ok("una clase de $0 no se ajusta", aj(R["cero"], "bonificada", None)["resultado"] == "importe_invalido")
ok("en una línea al 100 %, «cobrar menos» sigue con tope $800", aj(R["presente"], "importe", 799)["resultado"] == "ok"
   and aj(R["sin"], "importe", 800)["resultado"] == "importe_invalido")
try:
    with su.cursor() as cur:
        cur.execute("update resumen_mensual_detalle set importe_final = 300, ajuste_tipo = 'importe' where id = %s",
                    (L[R["dia15"]]["id"],))
    rechazo = False
except psycopg2.errors.CheckViolation:
    rechazo = True
ok("…$300 en una línea del 25 % se rechaza", rechazo)
f = factura(ALU2)
# líneas: 800+800+200+800(→799 ajustada)+800+0+0 = 3399
ok("total de líneas con ajustes: 3.399", f["total_lineas"] == 3399, f["total_lineas"])
r = rpc(A2, "descuento_general", p_id=F2["id"], p_tipo="porcentaje", p_valor=10, p_motivo="Promo", p_version=v())
ok("descuento del 10 % sobre las líneas ya calculadas (3.399 → 3.059,10)",
   r["resultado"] == "ok" and factura(ALU2)["total"] == Decimal("3059.10"), factura(ALU2)["total"])
r = rpc(A2, "descuento_general", p_id=F2["id"], p_tipo="monto", p_valor=500, p_motivo="Promo", p_version=v())
ok("descuento de $500: 2.899", r["resultado"] == "ok" and factura(ALU2)["total"] == 2899)

# ---------- 5) cambios de asistencia en un borrador ----------
ok("con el borrador abierto la administradora corrige una asistencia", corregir(R["sin"], "ausente")["resultado"] == "ok")
ok("…el borrador queda desactualizado y no se puede confirmar",
   not al_dia(F2["id"])
   and rpc(A2, "confirmar_factura", p_id=F2["id"], p_version=v())["resultado"] == "desactualizada")
pv = rpc(A2, "previsualizar_confirmacion_mes", p_mes=MES, p_anio=ANIO)
ok("…y la vista previa del mes lo cuenta como desactualizada", pv["desactualizadas"] >= 1, pv)
s = rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
L2 = lineas(F2["id"])
ok("al sincronizar, la línea pasa a 25 % ($200)", s["actualizadas"] >= 1
   and (L2[R["sin"]]["porcentaje"], L2[R["sin"]]["asistencia"], L2[R["sin"]]["importe_final"]) == (25, "ausente", 200), L2[R["sin"]])
hh = q(su, "select detalle, motivo from factura_historial where id_resumen_mensual = %s and accion = 'cambio_asistencia' "
           "order by id", (F2["id"],))
ok("…y queda en el historial de la factura (antes → después)", len(hh) == 1 and hh[0]["detalle"]["porcentaje_antes"] == 100
   and hh[0]["detalle"]["porcentaje_despues"] == 25 and Decimal(str(hh[0]["detalle"]["despues"])) == 200, hh)
ok("…el borrador vuelve a estar al día", al_dia(F2["id"]))
corregir(R["presente"], "ausente")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
x = lineas(F2["id"])[R["presente"]]
ok("ajuste «cobrar menos» de $799 que supera el nuevo importe base: queda en $200 sin ajuste",
   (x["porcentaje"], x["importe_final"], x["ajuste_tipo"]) == (25, 200, "ninguno"), x)
corregir(R["850"], "presente")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
x = lineas(F2["id"])[R["850"]]
ok("una línea bonificada sigue bonificada aunque cambie la asistencia", (x["porcentaje"], x["importe_final"], x["ajuste_tipo"])
   == (100, 0, "bonificada"), x)
corregir(R["dia15"], "presente")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
ok("ausente → presente: vuelve a $800", lineas(F2["id"])[R["dia15"]]["importe_final"] == 800)
aj(R["dia15"], "importe", 600)
corregir(R["dia15"], "ausente")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
x = lineas(F2["id"])[R["dia15"]]
ok("un ajuste de $600 pasa al tope de $200 al quedar ausente", (x["importe_final"], x["ajuste_tipo"]) == (200, "ninguno"), x)
aj(R["dia15"], "importe", 120)
corregir(R["dia15"], "presente")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
x = lineas(F2["id"])[R["dia15"]]
ok("un ajuste de $120 se conserva si vuelve a presente", (x["porcentaje"], x["importe_final"], x["ajuste_tipo"]) == (100, 120, "importe"), x)
f = factura(ALU2)
lin = lineas(F2["id"])
ok("el total siempre es la suma de las líneas menos el descuento",
   f["total_lineas"] == sum(l["importe_final"] for l in lin.values()) and f["total"] == f["total_lineas"] - min(500, f["total_lineas"]))
ok("con el descuento en monto mayor que las líneas, el descuento tiene tope (nunca negativo)", f["total"] >= 0)
cambios = q(su, "select count(*) n from factura_historial where id_resumen_mensual = %s and accion = 'cambio_asistencia'",
            (F2["id"],), True)["n"]
ok("cada cambio de importe por asistencia quedó en el historial (4; los que no cambian el importe, no)", cambios == 4, cambios)

# ---------- 6) confirmar: la factura confirmada nunca cambia ----------
r = rpc(A2, "confirmar_factura", p_id=F2["id"], p_version=v())
ok("se confirma el borrador al día", r["resultado"] == "ok", r)
FC = factura(ALU2, "confirmada")
foto = (lineas(FC["id"]), FC["total"])
ok("después de confirmar no se puede corregir la asistencia (factura confirmada)",
   corregir(R["antes"], "presente")["resultado"] == "factura_confirmada")
q(su, "update asistencia set estado = 'presente' where id_reserva = %s", (R["dia14"],))  # aunque cambie por fuera
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
FC2 = factura(ALU2, "confirmada")
ok("…y sincronizar no toca la confirmada (líneas y total iguales)", (lineas(FC["id"]), FC2["total"]) == foto)
try:
    with A2.conn.cursor() as cur:
        cur.execute("update public.resumen_mensual_detalle set importe_final = 0 where id = %s", (L[R["antes"]]["id"],))
    directo = None
except psycopg2.Error as e:
    directo = e.pgcode
    A2.conn.rollback() if not A2.conn.autocommit else None
ok("…la administradora tampoco puede actualizar líneas directamente", directo == "42501", directo)

# ---------- 7) anular y reemitir con la asistencia corregida ----------
p = rpc(A2, "registrar_pago", p_id_resumen=FC["id"], p_monto=str(FC["total"]), p_medio="efectivo", p_fecha=str(hoy))
ok("se registra un pago de la confirmada", p.get("resultado") == "ok", p)
ok("con pago vigente no se puede anular", rpc(A2, "anular_factura", p_id=FC["id"], p_motivo="Error de asistencia",
                                              p_tipo="correccion")["resultado"] == "tiene_pago")
pago = q(su, "select id from pago where id_resumen_mensual = %s", (FC["id"],), True)
ok("se anula el pago (queda en el historial)", rpc(A2, "anular_pago", p_id_pago=pago["id"], p_motivo="Corrección").get("resultado") == "ok")
pago_antes = q(su, "select * from pago where id_resumen_mensual = %s", (FC["id"],))
r = rpc(A2, "anular_factura", p_id=FC["id"], p_motivo="Error de asistencia", p_tipo="correccion")
ok("anular y reemitir: borrador nuevo", r["resultado"] == "ok", r)
NUEVA = r["id_nueva"]
LN = lineas(NUEVA)
ok("…el borrador copia porcentaje, asistencia, ajustes e importes de cada línea",
   {k: (x["porcentaje"], x["asistencia"], x["ajuste_tipo"], x["importe_final"]) for k, x in LN.items()}
   == {k: (x["porcentaje"], x["asistencia"], x["ajuste_tipo"], x["importe_final"]) for k, x in foto[0].items()})
ok("…y el historial de cambios de asistencia (marcado como copiado)",
   q(su, "select count(*) n from factura_historial where id_resumen_mensual = %s and accion = 'cambio_asistencia' "
         "and detalle ? 'copiado_de'", (NUEVA,), True)["n"] == cambios)
ok("ahora sí se puede corregir la asistencia de ese mes", corregir(R["antes"], "presente", "Estaba, error de planilla")["resultado"] == "ok")
rpc(A2, "sincronizar_borradores", p_mes=MES, p_anio=ANIO)
LN = lineas(NUEVA)
ok("…el borrador nuevo se recalcula (la clase anterior a la fecha de inicio sigue al 100 %)",
   (LN[R["antes"]]["asistencia"], LN[R["antes"]]["importe_final"], LN[R["dia14"]]["asistencia"]) == ("presente", 800, "presente"))
f = factura(ALU2)
r = rpc(A2, "confirmar_factura", p_id=NUEVA, p_version=f["version"])
ok("se confirma la reemitida con otro número", r["resultado"] == "ok" and factura(ALU2, "confirmada")["numero"] != FC["numero"], r)
vieja = q(su, "select * from resumen_mensual where id = %s", (FC["id"],), True)
ok("la anulada conserva sus líneas y su total", vieja["estado_factura"] == "anulada" and vieja["total"] == FC["total"]
   and lineas(FC["id"]) == foto[0])
ok("los pagos no se tocaron (el anulado sigue igual)", q(su, "select * from pago where id_resumen_mensual = %s", (FC["id"],)) == pago_antes)
ok("la reemitida tiene vencimiento", factura(ALU2, "confirmada")["vence_el"] is not None)

# ---------- 8) la otra alumna: confirmación masiva y lectura de la alumna ----------
F3 = factura(ALU3)
ok("otra alumna: 200 + 800 + 200 = 1.200", F3["total"] == 1200, F3["total"])
pv = rpc(A2, "previsualizar_confirmacion_mes", p_mes=MES, p_anio=ANIO)
r = rpc(A2, "confirmar_facturas_mes", p_mes=MES, p_anio=ANIO, p_firma=pv["firma"])
ok("confirmación masiva", r.get("resultado") == "ok", r)
vis = AL3.table("resumen_mensual_detalle").select("porcentaje, asistencia, importe_final, precio_original").execute().data
ok("la alumna ve sus líneas con porcentaje y asistencia (factura publicada)",
   sorted((x["porcentaje"], x["importe_final"]) for x in vis) == [(25, 200), (25, 200), (100, 800)], vis)
ok("…y no ve el historial (motivos internos)", AL3.table("factura_historial").select("id").execute().data == [])
ok("…ni las líneas de otra alumna", all(x["porcentaje"] for x in vis) and len(vis) == 3)

# ---------- 9) sin fecha o en otra empresa: como hoy ----------
q(su, "update empresa set cobro_inasistencia_desde = null where id = %s", (E2,))
MES_B = (MES % 12) + 1
ANIO_B = ANIO + (MES == 12)
hb = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
           "values ('domingo', '11:00', %s, false, %s, %s) returning id", (TIPO, PROF, E2), True)["id"]
CB = q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
           "id_empresa, id_profesora) values (%s, %s, '11:00', 'programada', 60, 800, 8, %s, %s, %s) returning id",
       (hb, date(ANIO_B, MES_B, 5), TIPO, E2, PROF), True)["id"]
RB = reserva(ALU4, CB, "ausente")
rpc(A2, "sincronizar_borradores", p_mes=MES_B, p_anio=ANIO_B)
fb = q(su, "select * from resumen_mensual where id_alumna = %s and mes = %s and anio = %s", (ALU4, MES_B, ANIO_B), True)
ok("empresa sin fecha: una inasistencia se cobra al 100 %", fb and lineas(fb["id"])[RB]["porcentaje"] == 100
   and fb["total"] == 800, fb)
ok("las facturas, líneas, pagos e historial de Just Show Up no cambiaron con todo lo anterior",
   datos_e1_solo() == datos_e1)
ok("la administradora de Just Show Up no ve facturas de la otra empresa",
   A1.table("resumen_mensual_detalle").select("id").in_("id", [x["id"] for x in lineas(fb["id"]).values()]).execute().data == [])
s = rpc(A1, "sincronizar_borradores", p_mes=MES_B, p_anio=ANIO_B)
ok("…ni su sincronización (octubre) la toca", s.get("resultado") == "ok"
   and q(su, "select version from resumen_mensual where id = %s", (fb["id"],), True)["version"] == fb["version"])
oct_e1 = q(su, "select count(*) filter (where d.porcentaje <> 100) n25, count(*) filter (where d.asistencia = 'ausente') aus "
               "from resumen_mensual_detalle d join resumen_mensual f on f.id = d.id_resumen_mensual "
               "where f.id_empresa = %s and f.mes = %s and f.anio = %s", (E1, MES_B, ANIO_B), True)
ok("Just Show Up: las ausencias de antes del 1/11 (borradores del mes recién sincronizados) siguen al 100 %",
   oct_e1["n25"] == 0 and (MES_B, ANIO_B) < (11, 2026) and oct_e1["aus"] > 0, oct_e1)

# ---------- 10) configurar la fecha ----------
inicio_mes = hoy.replace(day=1)
cfg = lambda cli, emp, d: rpc(cli, "configurar_cobro_inasistencia", p_id_empresa=emp, p_desde=str(d) if d else None)
ok("administradora de la empresa: no autorizada", cfg(A2, E2, inicio_mes) == "no_autorizada")
ok("profesora y alumna: no autorizadas", cfg(P, E2, inicio_mes) == "no_autorizada" and cfg(AL2, E2, inicio_mes) == "no_autorizada")
ok("sin sesión no puede ni ejecutarla", isinstance(cfg(ClienteFalso(uri, None, rol="anon"), E2, inicio_mes), dict))
ok("superadministradora: una fecha anterior al mes en curso se rechaza", cfg(SUP, E2, inicio_mes - timedelta(days=1)) == "fecha_retroactiva")
ok("…desde el 1° del mes en curso sí", cfg(SUP, E2, inicio_mes) == "ok")
ok("…la misma fecha: sin cambios", cfg(SUP, E2, inicio_mes) == "sin_cambios")
ok("…una fecha futura sí", cfg(SUP, E2, inicio_mes + timedelta(days=40)) == "ok")
ok("…quitarla (vacía) mientras no rige sí", cfg(SUP, E2, None) == "ok")
ok("empresa inexistente", cfg(SUP, 999999, inicio_mes) == "no_existe")
q(su, "update empresa set cobro_inasistencia_desde = %s where id = %s", (DESDE, E2))
ok("si ya rige desde un mes anterior, no se puede cambiar ni quitar (no reescribe meses pasados)",
   cfg(SUP, E2, None) == "ya_vigente" and cfg(SUP, E2, inicio_mes + timedelta(days=40)) == "ya_vigente")
ok("la administradora lee la fecha de su empresa (para mostrarla)",
   A2.table("empresa").select("cobro_inasistencia_desde").eq("id", E2).execute().data == [{"cobro_inasistencia_desde": str(DESDE)}]
   or A2.table("empresa").select("cobro_inasistencia_desde").eq("id", E2).execute().data[0]["cobro_inasistencia_desde"] == DESDE)

# ---------- 11) pedidos simultáneos ----------
H4 = factura(ALU4)
x = lineas(H4["id"]) if H4 else {}
corregir(R4["b"], "ausente")
res = []
hilos = [threading.Thread(target=lambda: res.append(rpc(C(ADMIN2), "sincronizar_borradores", p_mes=MES, p_anio=ANIO)))
         for _ in range(3)]
[t.start() for t in hilos]
[t.join() for t in hilos]
f4 = factura(ALU4, "confirmada")
ok("tres sincronizaciones a la vez: sin errores", all(r_.get("resultado") == "ok" for r_ in res), res)
ok("…la confirmada de la alumna 4 no cambió aunque cambió su asistencia", f4 and f4["total"] == H4["total"]
   if H4 and H4["estado_factura"] == "confirmada" else True)
hist = q(su, "select count(*) n from factura_historial where accion = 'cambio_asistencia' and id_resumen_mensual = %s",
         (f4["id"],), True)["n"] if f4 else 0
ok("…y sin entradas duplicadas en su historial", hist == 0, hist)
# borrador nuevo (mes B) + corrección y sincronización simultáneas
RB2 = reserva(ALU3, CB, "presente")
q(su, "update empresa set cobro_inasistencia_desde = %s where id = %s", (date(ANIO_B, MES_B, 1), E2))
res = {}
t1 = threading.Thread(target=lambda: res.update(c=rpc(C(ADMIN2), "corregir_asistencias", p_cambios=J([{"id_reserva": RB2, "estado": "ausente"}]),
                                                      p_motivo="simultánea")))
t2 = threading.Thread(target=lambda: res.update(s=rpc(C(ADMIN2), "sincronizar_borradores", p_mes=MES_B, p_anio=ANIO_B)))
t1.start(), t2.start(), t1.join(), t2.join()
rpc(A2, "sincronizar_borradores", p_mes=MES_B, p_anio=ANIO_B)
fb3 = q(su, "select * from resumen_mensual where id_alumna = %s and mes = %s and anio = %s", (ALU3, MES_B, ANIO_B), True)
ok("corrección y sincronización a la vez: al volver a abrir el mes la línea queda al 25 % y al día",
   lineas(fb3["id"])[RB2]["importe_final"] == 200 and al_dia(fb3["id"]), res)

# ---------- 12) la vuelta atrás se niega si ya hay líneas al 25 % ----------
try:
    with su.cursor() as cur:
        cur.execute(open(VUELTA).read())
    negada = False
except psycopg2.Error:
    su.rollback() if not su.autocommit else None
    negada = True
with su.cursor() as cur:
    cur.execute("rollback")
ok("vuelta atrás con líneas al 25 %: se niega y no cambia nada",
   negada and q(su, "select count(*) n from information_schema.columns where table_name = 'resumen_mensual_detalle' "
                    "and column_name = 'porcentaje'", uno=True)["n"] == 1)

print(f"\n{total_casos - fallas}/{total_casos} OK")
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
