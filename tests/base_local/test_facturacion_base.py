"""Facturación mensual (módulo 6) y cédula en un Postgres LOCAL descartable, con reloj simulado.
No toca Supabase ni usa datos reales: aplica todas las migraciones del repo y carga datos sintéticos.
Prueba las funciones y la seguridad (RLS) de la base y también el código real de app/facturas.py
y app/factura_pdf.py (con un cliente falso en lugar de PostgREST).

Uso (con las dependencias de requirements-dev.txt):  python tests/base_local/test_facturacion_base.py
"""
import os
import re
import sys
import threading
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

sys.path.insert(0, localdb.REPO)
from app import factura_pdf, facturas  # noqa: E402

fallas = total_casos = 0


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


def texto_pdf(datos: bytes) -> str:
    partes = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", datos, re.S):
        try:
            partes.append(zlib.decompress(m.group(1)).decode("latin-1"))
        except zlib.error:
            partes.append(m.group(1).decode("latin-1"))
    return "\n".join(partes)


# ---------- base: migraciones anteriores + datos sintéticos ----------
srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
NUEVAS = [a for a in localdb.migraciones() if a.endswith(("_profiles_cedula.sql", "_resumen_mensual_pagos.sql"))]
assert len(NUEVAS) == 2, NUEVAS
# Las que dependen de facturación (Mercado Pago) van después de ella.
# Todas las posteriores a facturación (Mercado Pago, vencimiento, multiempresa...) van después de ella.
_BASE_FACT = os.path.basename([a for a in NUEVAS if a.endswith("_resumen_mensual_pagos.sql")][0])
DEPENDIENTES = [a for a in localdb.migraciones() if os.path.basename(a) > _BASE_FACT and a not in NUEVAS]
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if a not in NUEVAS and a not in DEPENDIENTES:
            cur.execute(open(a).read())

usuarias = {}
for nombre in ("Profe", "Uno", "Dos", "Tres", "Cuatro", "Cinco"):
    uid = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (uid, f"{nombre.lower()}@prueba.invalid", f'{{"nombre": "{nombre}", "apellido": "Prueba"}}'))
    usuarias[nombre] = uid
prof = usuarias["Profe"]
q(su, "update profiles set rol = 'profesora' where id = %s", (prof,))
A1, A2, A3, A4, A5 = (usuarias[n] for n in ("Uno", "Dos", "Tres", "Cuatro", "Cinco"))
gim = q(su, "select id from tipo_clase where nombre = 'Gimnasia'", uno=True)["id"]
H = q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo) values ('lunes', '08:00', %s, true) returning id", (gim,), True)["id"]
contador = [0]


def clase(fecha, hora, precio, tipo=None, estado="programada"):
    contador[0] += 1
    with su.cursor() as cur:  # cada clase con un horario propio por la restricción (id_horario, fecha)
        cur.execute("set session_replication_role = replica")
        cur.execute("""insert into horario (id, dia_semana, hora_inicio, id_tipo_clase, activo, duracion, cupo_max, precio)
                       overriding system value values (%s, 'domingo', '05:00', %s, false, 60, 7, 1)""", (900000 + contador[0], gim))
        cur.execute("""insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, precio, cupo_max, id_tipo_clase)
                       values (%s, %s, %s, 0, %s, 60, %s, 7, %s) returning id""", (900000 + contador[0], fecha, hora, estado, precio, tipo or gim))
        i = cur.fetchone()[0]
        cur.execute("set session_replication_role = origin")
    return i


def reserva(alumna, id_clase, estado="confirmada", bloqueo=None):
    return q(su, "insert into reserva (id_alumna, id_clase, estado, id_bloqueo) values (%s, %s, %s, %s) returning id",
             (alumna, id_clase, estado, bloqueo), True)["id"]


# una clase futura real (para comprobar que reservar y cancelar siguen funcionando)
pasado_manana = date.today() + timedelta(days=3)
fut = clase(pasado_manana.isoformat(), "08:00", 800)
reserva(A1, fut)
q(su, "insert into asistencia (id_reserva, estado) select id, 'presente' from reserva limit 1")

TABLAS = ["horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo", "tipo_clase"]
COLS_PERFIL = "id, nombre, apellido, mail, telefono, fecha_nacimiento, fecha_vencimiento_carne_salud, rol"
COLS = {t: ", ".join(c["column_name"] for c in q(su, """select column_name from information_schema.columns
          where table_schema = 'public' and table_name = %s order by ordinal_position""", (t,))) for t in TABLAS}
antes = {t: huella(su, t, COLS[t]) for t in TABLAS}  # columnas anteriores (las posteriores agregan columnas)
antes["profiles"] = huella(su, "profiles", COLS_PERFIL)
with su.cursor() as cur:
    for a in NUEVAS + DEPENDIENTES:
        cur.execute(open(a).read())
ok("las migraciones nuevas (cédula y facturación) se aplican después de todas las demás", True)
despues = {t: huella(su, t, COLS[t]) for t in TABLAS}
despues["profiles"] = huella(su, "profiles", COLS_PERFIL)
ok("las migraciones no modifican ningún dato existente", despues == antes)
q(su, localdb.RELOJ)
ANON = conectar(uri, None, "anon")


def P(t):
    return ClienteFalso(uri, prof, ahora=t)


def AL(uid, t):
    return ClienteFalso(uri, uid, ahora=t)


def rpc(c, f, **kw):
    return c.rpc(f, kw).execute().data


def leer(c, t, **eq):
    x = c.table(t).select("*")
    for k, v in eq.items():
        x = x.eq(k, v)
    return x.execute().data


yoga = q(su, "insert into tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max) values ('PRUEBA-Yoga', '', 60, 951, 7) returning id", uno=True)["id"]
c1 = clase("2026-06-29", "08:00", 800)
c2 = clase("2026-06-29", "09:00", 951, yoga)
c3 = clase("2026-06-30", "08:00", 800)
c4 = clase("2026-06-30", "18:00", 800)
c5 = clase("2026-06-10", "08:00", 800)
c6 = clase("2026-06-11", "08:00", 800, estado="cancelada")
c8 = clase("2026-06-15", "08:00", 700)
for c in (c1, c2, c3, c4):
    reserva(A1, c)
reserva(A1, c5, "cancelada")
reserva(A2, c1)
reserva(A2, c4)
reserva(A3, c6, "cancelada", bloqueo=999)
reserva(A5, c8)

T0, T1, T2, T3 = "2026-06-20 12:00-03", "2026-06-30 12:00-03", "2026-06-30 18:30-03", "2026-06-30 19:30-03"
PUB_ANTES, PUB = "2026-06-30 23:59:59-03", "2026-07-01 00:00-03"

# ---------- durante el mes ----------
p = P(T0)
ok("antes del último día: sincronizar -> fuera_de_revision", rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)["resultado"] == "fuera_de_revision")
ok("…y no crea nada", q(su, "select count(*) n from resumen_mensual", uno=True)["n"] == 0)
prov = {a["id_alumna"]: a for a in facturas.provisorio(p, 6, 2026, datetime.fromisoformat("2026-06-20T12:00:00-03:00"))}
ok("provisorio (app real): A1 0 dictadas (la cancelada no) y 4 próximas por 3351",
   (prov[A1]["dictadas"], prov[A1]["proximas"], str(prov[A1]["total_proximas"])) == (0, 4, "3351.00"), prov.get(A1))
ok("provisorio: A5 1 dictada; A3 (solo una cancelada) no aparece", prov[A5]["dictadas"] == 1 and A3 not in prov)

# ---------- último día, mediodía ----------
p = P(T1)
r = rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
ok("último día: sincronizar ok", r["resultado"] == "ok", r)
fs = {f["id_alumna"]: f for f in q(su, "select * from resumen_mensual where mes = 6 and anio = 2026")}
ok("borradores: A1, A2, A5 (A3 sin clases no tiene factura)", set(fs) == {A1, A2, A5}, set(fs))
ok("A1 solo con clases ya empezadas: 800 + 951 + 800 (la de las 18:00 todavía no)",
   (fs[A1]["cantidad_clases"], float(fs[A1]["total"])) == (3, 2551.0))
ok("todo en borrador y pendiente, sin foto de identificación todavía",
   all(f["estado_factura"] == "borrador" and f["estado_pago"] == "pendiente" and f["alumna_nombre"] is None for f in fs.values()))
F1, F2, F5 = fs[A1]["id"], fs[A2]["id"], fs[A5]["id"]
v = fs[A1]["version"]
r = rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
ok("re-sincronizar sin cambios no sube la versión",
   q(su, "select version from resumen_mensual where id = %s", (F1,), True)["version"] == v and r["actualizadas"] == 0)
ok("confirmar con clases del mes sin terminar: periodo_abierto",
   rpc(p, "confirmar_factura", p_id=F1, p_version=v)["resultado"] == "periodo_abierto")
pv = rpc(p, "previsualizar_confirmacion_mes", p_mes=6, p_anio=2026)
ok("vista previa: período no finalizado", pv["periodo_finalizado"] is False and pv["cantidad"] == 3)

lin = {l["id_clase"]: l for l in q(su, "select * from resumen_mensual_detalle where id_resumen_mensual = %s", (F1,))}
a = AL(A1, T1)
ok("alumna no puede ajustar", rpc(a, "ajustar_linea", p_id_linea=lin[c2]["id"], p_tipo="bonificada", p_importe=None, p_motivo="xxx", p_version=v)["resultado"] == "no_autorizada")
ok("ajuste sin motivo: motivo_invalido", rpc(p, "ajustar_linea", p_id_linea=lin[c2]["id"], p_tipo="bonificada", p_importe=None, p_motivo=" ", p_version=v)["resultado"] == "motivo_invalido")
ok("ajuste con versión vieja: version_cambio", rpc(p, "ajustar_linea", p_id_linea=lin[c2]["id"], p_tipo="bonificada", p_importe=None, p_motivo="clase de regalo", p_version=v - 1)["resultado"] == "version_cambio")
r = rpc(p, "ajustar_linea", p_id_linea=lin[c2]["id"], p_tipo="bonificada", p_importe=None, p_motivo="clase de regalo", p_version=v)
ok("bonificar una clase: total 2551 -> 1600", r["resultado"] == "ok" and float(q(su, "select total from resumen_mensual where id = %s", (F1,), True)["total"]) == 1600.0)
v = r["version"]
for imp, nombre in (("900", "mayor que el original"), ("800", "igual al original"), ("-1", "negativo"), ("100.555", "con 3 decimales")):
    ok(f"importe {nombre}: importe_invalido", rpc(p, "ajustar_linea", p_id_linea=lin[c1]["id"], p_tipo="importe", p_importe=imp, p_motivo="motivo x", p_version=v)["resultado"] == "importe_invalido")
r = rpc(p, "ajustar_linea", p_id_linea=lin[c1]["id"], p_tipo="importe", p_importe="500", p_motivo="llegó tarde", p_version=v)
v = r["version"]
l1 = q(su, "select * from resumen_mensual_detalle where id = %s", (lin[c1]["id"],), True)
ok("cambiar importe: conserva el original (800) e importe final 500", (float(l1["precio_original"]), float(l1["importe_final"]), l1["ajuste_tipo"]) == (800, 500, "importe"))
ok("descuento fijo mayor que el total: descuento_invalido", rpc(p, "descuento_general", p_id=F1, p_tipo="monto", p_valor="5000", p_motivo="exceso", p_version=v)["resultado"] == "descuento_invalido")
ok("porcentaje 150: descuento_invalido", rpc(p, "descuento_general", p_id=F1, p_tipo="porcentaje", p_valor="150", p_motivo="exceso", p_version=v)["resultado"] == "descuento_invalido")
r = rpc(p, "descuento_general", p_id=F1, p_tipo="porcentaje", p_valor="10", p_motivo="alumna frecuente", p_version=v)
f1 = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
ok("descuento 10%: líneas 1300 - 130 = 1170, original 2551",
   (float(f1["total_lineas"]), float(f1["descuento_monto"]), float(f1["total"]), float(f1["subtotal_original"])) == (1300, 130, 1170, 2551))
v = r["version"]
r = rpc(p, "quitar_ajuste_linea", p_id_linea=lin[c2]["id"], p_motivo="me equivoqué", p_version=v)
v = r["version"]
ok("quitar ajuste: la clase vuelve a 951 y se recalcula (2251 - 10%)",
   float(q(su, "select total from resumen_mensual where id = %s", (F1,), True)["total"]) == round(2251 * 0.9, 2))
hist = q(su, "select accion, motivo, por from factura_historial where id_resumen_mensual = %s order by id", (F1,))
ok("historial: cada ajuste con motivo y responsable",
   [h["accion"] for h in hist] == ["ajuste_linea", "ajuste_linea", "descuento_general", "ajuste_quitado"]
   and all(h["motivo"] and h["por"] == prof for h in hist))
ok("la alumna no puede leer el historial", leer(a, "factura_historial") == [])
ok("la alumna no ve su borrador (ni sus líneas)", leer(a, "resumen_mensual") == [] and leer(a, "resumen_mensual_detalle") == [])
v5 = q(su, "select version from resumen_mensual where id = %s", (F5,), True)["version"]
l5 = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s", (F5,), True)["id"]
rpc(p, "ajustar_linea", p_id_linea=l5, p_tipo="bonificada", p_importe=None, p_motivo="clase de prueba", p_version=v5)

# ---------- cédula ----------
ok("cédula: ninguna usuaria tiene cédula (no se completó nada)", q(su, "select count(*) n from profiles where cedula is not null", uno=True)["n"] == 0)
ok("dígito verificador en la base: 1.234.567-2 válida; -1 y '123' no",
   q(su, "select public._cedula_valida('12345672') a, public._cedula_valida('12345671') b, public._cedula_valida('123') c", uno=True) == {"a": True, "b": False, "c": False})
ok("la base rechaza una cédula inválida aunque se escriba directo", error_de(su, "update profiles set cedula = '12345671' where id = %s", (A3,)) == "23514")
ok("alumna no puede cargar cédulas", rpc(a, "actualizar_cedula", p_id_alumna=A1, p_cedula="12345672") == "no_autorizada")
ok("profesora carga una cédula con puntos y guion (se guarda solo con dígitos)",
   rpc(p, "actualizar_cedula", p_id_alumna=A2, p_cedula="1.234.567-2") == "ok"
   and q(su, "select cedula from profiles where id = %s", (A2,), True)["cedula"] == "12345672")
ok("cédula inválida: cedula_invalida", rpc(p, "actualizar_cedula", p_id_alumna=A1, p_cedula="1.234.567-9") == "cedula_invalida")
ok("cédula repetida entre alumnas: cedula_repetida", rpc(p, "actualizar_cedula", p_id_alumna=A1, p_cedula="12345672") == "cedula_repetida")
ok("no se le carga cédula a quien no es alumna", rpc(p, "actualizar_cedula", p_id_alumna=prof, p_cedula="98765430") == "no_existe")
ok("la alumna no puede cambiar su cédula directo",
   error_de(conectar(uri, A2, "authenticated"), f"update profiles set cedula = '12345672' where id = '{A2}'") == "42501")
# desde multiempresa (parte 4) los datos personales se leen con datos_personales(), no directo de profiles
ok("cada alumna lee solo su fila y su cédula",
   [x["cedula"] for x in rpc(AL(A2, T1), "datos_personales", p_ids=[A1, A2])] == ["12345672"]
   and [x["cedula"] for x in rpc(AL(A1, T1), "datos_personales", p_ids=[A1, A2])] == [None]
   and len(AL(A2, T1).table("profiles").select("id, nombre").execute().data) == 1)
ok("sin sesión no se lee profiles", error_de(ANON, "select cedula from profiles") is not None
   or q(ANON, "select count(*) n from profiles", uno=True)["n"] == 0)
ok("la profesora lee las cédulas de todas", len(rpc(P(T1), "datos_personales", p_ids=list(usuarias.values()))) == 6)
d2 = facturas.factura(P(T1), F2)
ok("app: el borrador muestra los datos actuales del perfil", d2["cedula_txt"] == "1.234.567-2" and not d2["identificacion_fija"]
   and d2["alumna"] == "Dos Prueba")

# ---------- 18:30: empezó la última clase ----------
p = P(T2)
rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
f1 = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
ok("18:30: la clase de las 18 se suma a A1 y A2 sin perder los ajustes",
   f1["cantidad_clases"] == 4 and float(q(su, "select importe_final from resumen_mensual_detalle where id = %s", (lin[c1]["id"],), True)["importe_final"]) == 500
   and q(su, "select cantidad_clases from resumen_mensual where id = %s", (F2,), True)["cantidad_clases"] == 2)
ok("…y sube la versión: confirmar con la versión vieja no pasa",
   f1["version"] > v and rpc(p, "confirmar_factura", p_id=F1, p_version=v)["resultado"] in ("version_cambio", "periodo_abierto"))
ok("18:30 la clase sigue en curso: no se puede confirmar", rpc(p, "confirmar_factura", p_id=F1, p_version=f1["version"])["resultado"] == "periodo_abierto")
v = f1["version"]

# ---------- 19:30: terminó todo ----------
p = P(T3)
rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
v2 = q(su, "select version from resumen_mensual where id = %s", (F2,), True)["version"]
r = rpc(p, "confirmar_factura", p_id=F2, p_version=v2)
f2 = q(su, "select * from resumen_mensual where id = %s", (F2,), True)
ok("19:30: confirmar A2: ok, con número y pendiente de pago",
   r["resultado"] == "ok" and f2["estado_factura"] == "confirmada" and f2["numero"].startswith("JSU-2026-06-") and f2["estado_pago"] == "pendiente", r)
ok("al confirmar se guarda la foto de nombre, apellido y cédula",
   (f2["alumna_nombre"], f2["alumna_apellido"], f2["alumna_cedula"]) == ("Dos", "Prueba", "12345672"))
l2 = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s limit 1", (F2,), True)["id"]
ok("factura confirmada: no se puede ajustar", rpc(p, "ajustar_linea", p_id_linea=l2, p_tipo="bonificada", p_importe=None, p_motivo="intento", p_version=f2["version"])["resultado"] == "no_es_borrador")
ok("factura confirmada: no se puede cambiar el descuento", rpc(p, "descuento_general", p_id=F2, p_tipo="porcentaje", p_valor="5", p_motivo="intento", p_version=f2["version"])["resultado"] == "no_es_borrador")
foto2 = q(su, "select * from resumen_mensual where id = %s", (F2,), True)
q(su, "update clase set precio = 1 where id = %s", (c1,))
rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
ok("sincronizar no toca la factura confirmada aunque cambie el precio de la clase",
   q(su, "select * from resumen_mensual where id = %s", (F2,), True) == foto2
   and float(q(su, "select importe_final from resumen_mensual_detalle where id_resumen_mensual = %s and id_clase = %s", (F2, c1), True)["importe_final"]) == 800)
q(su, "update clase set precio = 800 where id = %s", (c1,))
v5 = q(su, "select version from resumen_mensual where id = %s", (F5,), True)["version"]
r = rpc(p, "confirmar_factura", p_id=F5, p_version=v5)
ok("factura bonificada al 100%: se confirma como 'sin cargo'", r["resultado"] == "ok" and q(su, "select estado_pago from resumen_mensual where id = %s", (F5,), True)["estado_pago"] == "sin_cargo")

c7 = clase("2026-06-30", "10:00", 800)
reserva(A1, c7)
v = q(su, "select version from resumen_mensual where id = %s", (F1,), True)["version"]
ok("si faltan clases en el borrador: confirmar -> desactualizada", rpc(p, "confirmar_factura", p_id=F1, p_version=v)["resultado"] == "desactualizada")
pv = rpc(p, "previsualizar_confirmacion_mes", p_mes=6, p_anio=2026)
ok("vista previa: cuenta la desactualizada", pv["desactualizadas"] == 1 and pv["periodo_finalizado"] is True)
ok("vista previa: avisa cuántas facturas no tienen cédula (A1)", pv["sin_cedula"] == 1, pv)
ok("confirmación masiva con una desactualizada: no confirma ninguna",
   rpc(p, "confirmar_facturas_mes", p_mes=6, p_anio=2026, p_firma=pv["firma"])["resultado"] == "desactualizada"
   and q(su, "select estado_factura from resumen_mensual where id = %s", (F1,), True)["estado_factura"] == "borrador")
rpc(p, "sincronizar_borradores", p_mes=6, p_anio=2026)
pv = rpc(p, "previsualizar_confirmacion_mes", p_mes=6, p_anio=2026)
ok("firma vieja: cambio, no confirma ninguna", rpc(p, "confirmar_facturas_mes", p_mes=6, p_anio=2026, p_firma="x")["resultado"] == "cambio")

f1 = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
lc3 = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s and id_clase = %s", (F1, c3), True)["id"]
barrera = threading.Barrier(2)


def masivo():
    c = P(T3)
    barrera.wait()
    return rpc(c, "confirmar_facturas_mes", p_mes=6, p_anio=2026, p_firma=pv["firma"])["resultado"]


def ajuste():
    c = P(T3)
    barrera.wait()
    return rpc(c, "ajustar_linea", p_id_linea=lc3, p_tipo="importe", p_importe="700", p_motivo="carrera", p_version=f1["version"])["resultado"]


with ThreadPoolExecutor(2) as ex:
    fm, fa = ex.submit(masivo), ex.submit(ajuste)
    rm, ra = fm.result(), fa.result()
f1b = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
ok(f"ajuste y confirmación masiva a la vez: gana uno solo (masiva={rm}, ajuste={ra})",
   (rm == "ok" and ra == "no_es_borrador" and f1b["estado_factura"] == "confirmada")
   or (ra == "ok" and rm == "cambio" and f1b["estado_factura"] == "borrador"))
if f1b["estado_factura"] == "borrador":
    pv = rpc(p, "previsualizar_confirmacion_mes", p_mes=6, p_anio=2026)
    r = rpc(p, "confirmar_facturas_mes", p_mes=6, p_anio=2026, p_firma=pv["firma"])
    ok("confirmación masiva con la vista previa al día: ok", r["resultado"] == "ok" and r["confirmadas"] == 1, r)
f1 = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
ok("A1 confirmada con su total correcto y la cédula vacía (no bloquea)", f1["estado_factura"] == "confirmada" and f1["alumna_cedula"] is None
   and f1["alumna_nombre"] == "Uno"
   and float(f1["total"]) == float(q(su, "select round(sum(importe_final) * 0.9, 2) t from resumen_mensual_detalle where id_resumen_mensual = %s", (F1,), True)["t"]))
inv = q(su, """select count(*) n from resumen_mensual f where total_lineas <> (select coalesce(sum(importe_final), 0) from resumen_mensual_detalle d where d.id_resumen_mensual = f.id)
               or subtotal_original <> (select coalesce(sum(precio_original), 0) from resumen_mensual_detalle d where d.id_resumen_mensual = f.id)
               or cantidad_clases <> (select count(*) from resumen_mensual_detalle d where d.id_resumen_mensual = f.id)""", uno=True)["n"]
ok("todos los totales coinciden con sus líneas", inv == 0)
nums = q(su, "select count(distinct numero) d, count(numero) n from resumen_mensual", uno=True)
ok("números de factura distintos", nums["d"] == nums["n"] and nums["n"] > 0)

cx = clase("2026-05-31", "08:00", 800)
rx = reserva(A4, cx)
px = P("2026-05-31 12:00-03")
rpc(px, "sincronizar_borradores", p_mes=5, p_anio=2026)
fx = q(su, "select * from resumen_mensual where id_alumna = %s and mes = 5", (A4,), True)
lx = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s", (fx["id"],), True)["id"]
rpc(px, "ajustar_linea", p_id_linea=lx, p_tipo="importe", p_importe="400", p_motivo="acuerdo", p_version=fx["version"])
q(su, "update reserva set estado = 'cancelada' where id = %s", (rx,))
rpc(px, "sincronizar_borradores", p_mes=5, p_anio=2026)
ok("una clase ajustada que deja de cobrarse se quita y queda en el historial",
   q(su, "select count(*) n from resumen_mensual_detalle where id_resumen_mensual = %s", (fx["id"],), True)["n"] == 0
   and q(su, "select count(*) n from factura_historial where id_resumen_mensual = %s and accion = 'linea_quitada'", (fx["id"],), True)["n"] == 1)
ok("un borrador sin clases no se puede confirmar", rpc(P("2026-06-02 12:00-03"), "confirmar_factura", p_id=fx["id"],
   p_version=q(su, "select version from resumen_mensual where id = %s", (fx["id"],), True)["version"])["resultado"] == "sin_clases")

# ---------- publicación ----------
ok("30/06 23:59: la alumna todavía no ve su factura confirmada", leer(AL(A2, PUB_ANTES), "resumen_mensual") == [])
a2 = AL(A2, PUB)
ok("01/07 00:00: la alumna ve su factura confirmada (y solo la suya)", [f["id"] for f in leer(a2, "resumen_mensual")] == [F2])
ok("…y sus líneas, no las de otras", {l["id_resumen_mensual"] for l in leer(a2, "resumen_mensual_detalle")} == {F2})
ok("nunca ve el historial", leer(a2, "factura_historial") == [])
ok("un borrador nunca se publica", leer(AL(A4, "2026-07-15 12:00-03"), "resumen_mensual") == [])
ok("la profesora ve todas", len(leer(P(PUB), "resumen_mensual")) == q(su, "select count(*) n from resumen_mensual", uno=True)["n"])

# la foto fija no cambia aunque cambie el perfil
q(conectar(uri, A2, "authenticated"), f"update profiles set nombre = 'Cambiado' where id = '{A2}'")
rpc(P(PUB), "actualizar_cedula", p_id_alumna=A2, p_cedula="")
mia2 = facturas.mi_factura(AL(A2, PUB), A2, F2)
ok("cambiar después el nombre (la alumna) y quitar la cédula (la profesora) no cambia la factura confirmada",
   q(su, "select alumna_nombre, alumna_cedula from resumen_mensual where id = %s", (F2,), True) == {"alumna_nombre": "Dos", "alumna_cedula": "12345672"}
   and mia2["alumna"] == "Dos Prueba" and mia2["cedula_txt"] == "1.234.567-2" and mia2["identificacion_fija"])
ok("la profesora también ve la foto en la confirmada", facturas.factura(P(PUB), F2)["alumna"] == "Dos Prueba")
q(conectar(uri, A2, "authenticated"), f"update profiles set nombre = 'Dos' where id = '{A2}'")
rpc(P(PUB), "actualizar_cedula", p_id_alumna=A2, p_cedula="12345672")

cd = clase("2025-12-31", "08:00", 800)
reserva(A3, cd)
pd = P("2025-12-31 12:00-03")
rpc(pd, "sincronizar_borradores", p_mes=12, p_anio=2025)
fd = q(su, "select * from resumen_mensual where id_alumna = %s and mes = 12 and anio = 2025", (A3,), True)
ok("31/12: borrador de diciembre, se confirma al terminar las clases del día",
   rpc(pd, "confirmar_factura", p_id=fd["id"], p_version=fd["version"])["resultado"] == "ok")
ok("31/12 23:59 no visible; 01/01/2026 00:00 visible (cambio de año)",
   leer(AL(A3, "2025-12-31 23:59:59-03"), "resumen_mensual") == [] and len(leer(AL(A3, "2026-01-01 00:00-03"), "resumen_mensual")) == 1)

# ---------- pagos ----------
p = P("2026-07-02 10:00-03")
ok("pagar un borrador: no_confirmada", rpc(p, "registrar_pago", p_id_resumen=fx["id"], p_monto="1", p_medio="efectivo", p_fecha="2026-07-02")["resultado"] == "no_confirmada")
ok("pagar una factura sin cargo: sin_cargo", rpc(p, "registrar_pago", p_id_resumen=F5, p_monto="1", p_medio="efectivo", p_fecha="2026-07-02")["resultado"] == "sin_cargo")
total2 = q(su, "select total from resumen_mensual where id = %s", (F2,), True)["total"]
ok("fecha futura (simulada): fecha_futura", rpc(p, "registrar_pago", p_id_resumen=F2, p_monto=str(total2), p_medio="efectivo", p_fecha="2026-07-03")["resultado"] == "fecha_futura")
ok("monto parcial: monto_distinto", rpc(p, "registrar_pago", p_id_resumen=F2, p_monto="1", p_medio="efectivo", p_fecha="2026-07-02")["resultado"] == "monto_distinto")
ok("medio mercado_pago: datos_invalidos", rpc(p, "registrar_pago", p_id_resumen=F2, p_monto=str(total2), p_medio="mercado_pago", p_fecha="2026-07-02")["resultado"] == "datos_invalidos")
barrera = threading.Barrier(10)


def pagar(_):
    c = P("2026-07-02 10:00-03")
    barrera.wait()
    return rpc(c, "registrar_pago", p_id_resumen=F2, p_monto=str(total2), p_medio="efectivo", p_fecha="2026-07-02")["resultado"]


with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(pagar, range(10)))
ok("10 pagos simultáneos: 1 ok y 9 ya_pagado", res.count("ok") == 1 and res.count("ya_pagado") == 9, res)
pg = q(su, "select * from pago where id_resumen_mensual = %s", (F2,), True)
ok("pago con responsable; factura pagada y sigue confirmada",
   pg["registrado_por"] == prof and q(su, "select estado_factura, estado_pago from resumen_mensual where id = %s", (F2,), True) == {"estado_factura": "confirmada", "estado_pago": "pagado"})
ok("la alumna ve su pago vigente", [x["id"] for x in leer(a2, "pago")] == [pg["id"]])
ok("alumna no puede anular", rpc(a2, "anular_pago", p_id_pago=pg["id"], p_motivo="quiero")["resultado"] == "no_autorizada")
ok("anular sin motivo: motivo_invalido", rpc(p, "anular_pago", p_id_pago=pg["id"], p_motivo="")["resultado"] == "motivo_invalido")
barrera = threading.Barrier(5)


def anular(_):
    c = P("2026-07-02 10:00-03")
    barrera.wait()
    return rpc(c, "anular_pago", p_id_pago=pg["id"], p_motivo="medio equivocado")["resultado"]


with ThreadPoolExecutor(5) as ex:
    res = list(ex.map(anular, range(5)))
ok("5 anulaciones simultáneas: 1 ok y 4 ya_anulado", res.count("ok") == 1 and res.count("ya_anulado") == 4, res)
an = q(su, "select * from pago_anulacion where id_pago = %s", (pg["id"],), True)
ok("anulación con motivo, responsable y fecha; factura pendiente de pago",
   an["motivo"] == "medio equivocado" and an["anulado_por"] == prof and an["anulado_en"] is not None
   and q(su, "select estado_pago from resumen_mensual where id = %s", (F2,), True)["estado_pago"] == "pendiente")
ok("la alumna NO ve el pago anulado ni su motivo", leer(a2, "pago") == [] and leer(a2, "pago_anulacion") == [])
r = rpc(p, "registrar_pago", p_id_resumen=F2, p_monto=str(total2), p_medio="transferencia", p_fecha="2026-07-02")
ok("nuevo pago después de anular: 2 pagos identificados (uno anulado, otro vigente)",
   r["resultado"] == "ok" and [(x["estado"], x["medio_pago"]) for x in q(su, "select * from pago where id_resumen_mensual = %s order by id", (F2,))]
   == [("anulado", "efectivo"), ("vigente", "transferencia")])

# ---------- la app real contra esta base ----------
det = facturas.factura(P("2026-07-02 10:00-03"), F1)
ok("app: detalle con líneas, motivos e historial", det and len(det["lineas"]) == 5 and any(l["motivo"] == "llegó tarde" for l in det["lineas"])
   and det["historial"] and det["descuento_motivo"] == "alumna frecuente")
det2 = facturas.factura(P("2026-07-02 10:00-03"), F2)
ok("app: pagos vigente y anulado con su motivo (profesora)", det2["pago"]["medio"] == "Transferencia"
   and det2["pagos_anulados"][0]["motivo"] == "medio equivocado" and det2["pagos_anulados"][0]["anulado_por"])
mis = facturas.mis_facturas(AL(A2, PUB), A2)
ok("app: Mis facturas = solo su factura publicada, con líneas, pago y sin motivos",
   [f["id"] for f in mis] == [F2] and len(mis[0]["lineas"]) == 2 and mis[0]["pago"] and "motivo" not in mis[0]["lineas"][0])
ok("app: Mis facturas antes del día 1 = vacío", facturas.mis_facturas(AL(A2, PUB_ANTES), A2) == [])
ok("app: lista del mes con nombres y estados", len(facturas.facturas_del_mes(P(PUB), 6, 2026)) == 3)

# ---------- PDF ----------
AHORA_PDF = datetime.fromisoformat("2026-07-02T10:00:00-03:00")
mia = facturas.mi_factura(AL(A2, PUB), A2, F2)
pdf = factura_pdf.generar(mia, AHORA_PDF)
t = texto_pdf(pdf)
ok("PDF: es un PDF válido", pdf.startswith(b"%PDF"))
ok("PDF: número, período, nombre, cédula y total exacto",
   all(x in t for x in (mia["numero"], "Junio 2026", "Dos Prueba", "1.234.567-2", factura_pdf._plata(mia["total"]))))
ok("PDF: una fila por clase", t.count("Gimnasia") + t.count("PRUEBA-Yoga") == len(mia["lineas"]))
ok("PDF: estado de pago y leyenda de documento simulado", "Pagada el 02/07/2026" in t and "Documento simulado" in t)
ok("PDF: no incluye motivos internos", "medio equivocado" not in t and "tarde" not in t)
ok("una alumna no puede obtener la factura de otra", facturas.mi_factura(AL(A2, PUB), A2, F1) is None and facturas.mi_factura(AL(A1, PUB), A1, F2) is None)
ok("antes del día 1 la alumna no puede obtener su factura", facturas.mi_factura(AL(A2, PUB_ANTES), A2, F2) is None)
ok("un borrador no se puede obtener como alumna", facturas.mi_factura(AL(A4, "2026-07-15 12:00-03"), A4, fx["id"]) is None)
det1 = facturas.factura(P(PUB), F1)
pdf1 = texto_pdf(factura_pdf.generar(det1, AHORA_PDF))
ok("PDF de A1: ajuste, descuento general y total exacto",
   "-$ 300,00" in pdf1 and r"Descuento general \(10%\)" in pdf1 and factura_pdf._plata(det1["total"]) in pdf1)
ok("PDF de una factura sin cédula: 'no registrada'", "no registrada" in pdf1)
q(su, "update tipo_clase set precio_presencial = 5000 where id = %s", (gim,))
q(su, "update clase set precio = 5000 where id = %s", (c1,))
ok("cambiar después el precio del tipo y de la clase no cambia el PDF", texto_pdf(factura_pdf.generar(facturas.mi_factura(AL(A2, PUB), A2, F2), AHORA_PDF)) == t)
q(su, "update tipo_clase set precio_presencial = 800 where id = %s", (gim,))
q(su, "update clase set precio = 800 where id = %s", (c1,))
ok("PDF de un borrador (profesora) sale marcado", "BORRADOR" in texto_pdf(factura_pdf.generar(facturas.factura(P(PUB), fx["id"]), AHORA_PDF, "BORRADOR")))

# ---------- anular y reemitir ----------
p = P("2026-07-03 10:00-03")
ok("anular un borrador: no_confirmada_anular", rpc(p, "anular_factura", p_id=fx["id"], p_motivo="error")["resultado"] == "no_confirmada_anular")
ok("anular una factura con pago vigente: tiene_pago", rpc(p, "anular_factura", p_id=F2, p_motivo="error")["resultado"] == "tiene_pago")
ok("alumna no puede anular facturas", rpc(AL(A1, "2026-07-03 10:00-03"), "anular_factura", p_id=F1, p_motivo="error")["resultado"] == "no_autorizada")
ok("anular sin motivo: motivo_invalido", rpc(p, "anular_factura", p_id=F1, p_motivo=" ")["resultado"] == "motivo_invalido")
viejo = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
LINEAS_SQL = "select id_reserva, precio_original, ajuste_tipo, importe_final from resumen_mensual_detalle where id_resumen_mensual = %s order by id_reserva"
lineas_viejas = q(su, LINEAS_SQL, (F1,))
barrera = threading.Barrier(5)


def anular_f(_):
    c = P("2026-07-03 10:00-03")
    barrera.wait()
    return rpc(c, "anular_factura", p_id=F1, p_motivo="se cobró una clase de más")


with ThreadPoolExecutor(5) as ex:
    res = list(ex.map(anular_f, range(5)))
oks = [r for r in res if r["resultado"] == "ok"]
ok("5 anulaciones simultáneas: 1 ok y un solo borrador de reemplazo",
   len(oks) == 1 and all(r["resultado"] == "no_confirmada_anular" for r in res if r not in oks)
   and q(su, "select count(*) n from resumen_mensual where reemplaza_a = %s", (F1,), True)["n"] == 1, [r["resultado"] for r in res])
N = oks[0]["id_nueva"]
f1 = q(su, "select * from resumen_mensual where id = %s", (F1,), True)
ok("la anulada conserva número, importes, foto de identificación y fecha de anulación",
   f1["estado_factura"] == "anulada" and f1["numero"] == viejo["numero"] and f1["total"] == viejo["total"]
   and f1["alumna_nombre"] == viejo["alumna_nombre"] and f1["anulada_por"] == prof and f1["anulada_en"] is not None)
ok("sus líneas no se tocan", q(su, LINEAS_SQL, (F1,)) == lineas_viejas)
n = q(su, "select * from resumen_mensual where id = %s", (N,), True)
ok("el reemplazo es un borrador con las mismas clases, ajustes, descuento y total (sin foto todavía)",
   n["estado_factura"] == "borrador" and n["reemplaza_a"] == F1 and n["numero"] is None and n["total"] == viejo["total"]
   and n["descuento_tipo"] == viejo["descuento_tipo"] and n["alumna_nombre"] is None and q(su, LINEAS_SQL, (N,)) == lineas_viejas)
detn = facturas.factura(P("2026-07-03 10:00-03"), N)
ok("app: el borrador nuevo conserva los motivos y dice a quién reemplaza",
   any(l["motivo"] == "llegó tarde" for l in detn["lineas"]) and detn["reemplaza"] == viejo["numero"] and detn["descuento_motivo"])
ok("historial: 'anulada' en la vieja (con motivo) y 'reemision' en la nueva",
   q(su, "select motivo from factura_historial where id_resumen_mensual = %s and accion = 'anulada'", (F1,), True)["motivo"] == "se cobró una clase de más"
   and q(su, "select count(*) n from factura_historial where id_resumen_mensual = %s and accion = 'reemision'", (N,), True)["n"] == 1)
ok("pagar la factura anulada: no_confirmada", rpc(p, "registrar_pago", p_id_resumen=F1, p_monto=str(viejo["total"]), p_medio="efectivo", p_fecha="2026-07-03")["resultado"] == "no_confirmada")
ok("no se puede anular dos veces", rpc(p, "anular_factura", p_id=F1, p_motivo="otra vez")["resultado"] == "no_confirmada_anular")
a1 = AL(A1, "2026-07-03 10:00-03")
vis = {f["id"]: f["estado_factura"] for f in leer(a1, "resumen_mensual")}
ok("la alumna ve la anulada (ya estaba publicada) pero no el borrador nuevo", vis == {F1: "anulada"}, vis)
mis = facturas.mis_facturas(a1, A1)
ok("app: Mis facturas muestra la anulada sin el motivo", mis[0]["estado_factura"] == "anulada" and "se cobró una clase de más" not in str(mis[0]))
ok("cargar la cédula de A1 después (9.876.543-8)", rpc(p, "actualizar_cedula", p_id_alumna=A1, p_cedula="9.876.543-8") == "ok")
ced_a1 = q(su, "select cedula from profiles where id = %s", (A1,), True)["cedula"]
lc = q(su, "select id from resumen_mensual_detalle where id_resumen_mensual = %s and id_clase = %s", (N, c3), True)["id"]
r = rpc(p, "ajustar_linea", p_id_linea=lc, p_tipo="bonificada", p_importe=None, p_motivo="corrección", p_version=n["version"])
r = rpc(p, "confirmar_factura", p_id=N, p_version=r["version"])
n = q(su, "select * from resumen_mensual where id = %s", (N,), True)
ok("corregir y confirmar el reemplazo: número nuevo y total corregido",
   r["resultado"] == "ok" and n["numero"] != viejo["numero"] and n["total"] < viejo["total"], r)
ok("el reemplazo toma la foto de identificación vigente al confirmarse (cédula cargada después)",
   n["alumna_cedula"] == ced_a1 and f1["alumna_cedula"] is None, (ced_a1, n["alumna_cedula"]))
mis = {f["id"]: f for f in facturas.mis_facturas(a1, A1)}
ok("la alumna ve las dos: la anulada 'reemplazada por' la nueva, y la nueva 'reemplaza a'",
   set(mis) == {F1, N} and mis[F1]["reemplazada_por"] == n["numero"] and mis[N]["reemplaza"] == viejo["numero"])
pdfa = texto_pdf(factura_pdf.generar(mis[F1], AHORA_PDF, "ANULADA"))
ok("el PDF de la anulada sale marcado y dice por cuál se reemplazó", "ANULADA" in pdfa and n["numero"] in pdfa)

p30 = P("2026-06-30 20:00-03")
N5 = rpc(p30, "anular_factura", p_id=F5, p_motivo="no correspondía")["id_nueva"]
rpc(p30, "confirmar_factura", p_id=N5, p_version=q(su, "select version from resumen_mensual where id = %s", (N5,), True)["version"])
ok("anulada antes del día 1: la alumna ve solo la nueva", [f["id"] for f in leer(AL(A5, PUB), "resumen_mensual")] == [N5])

total_n = str(n["total"])
barrera = threading.Barrier(2)


def anular_n():
    c = P("2026-07-03 10:00-03")
    barrera.wait()
    return rpc(c, "anular_factura", p_id=N, p_motivo="carrera")["resultado"]


def pagar_n():
    c = P("2026-07-03 10:00-03")
    barrera.wait()
    return rpc(c, "registrar_pago", p_id_resumen=N, p_monto=total_n, p_medio="efectivo", p_fecha="2026-07-03")["resultado"]


with ThreadPoolExecutor(2) as ex:
    fa, fp = ex.submit(anular_n), ex.submit(pagar_n)
    ra, rp = fa.result(), fp.result()
inc = q(su, """select count(*) n from resumen_mensual f where f.estado_factura <> 'confirmada'
               and exists (select 1 from pago p where p.id_resumen_mensual = f.id and p.estado = 'vigente')""", uno=True)["n"]
ok(f"anular y pagar a la vez: gana uno y nunca queda una anulada con pago vigente (anular={ra}, pagar={rp})",
   inc == 0 and sorted([ra, rp]) in (["ok", "tiene_pago"], ["no_confirmada", "ok"]))
ok("ninguna alumna-mes tiene dos facturas no anuladas", q(su, """select count(*) n from (select id_alumna, mes, anio from resumen_mensual
   where estado_factura <> 'anulada' group by 1, 2, 3 having count(*) > 1) x""", uno=True)["n"] == 0)

# ---------- permisos ----------
TABLAS_F = ("resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion")
foto = {t: huella(su, t) for t in TABLAS_F}
foto["profiles"] = huella(su, "profiles")
intentos = {
    "marcar su factura pagada": f"update resumen_mensual set estado_pago = 'pagado' where id = {F2}",
    "confirmar directo": f"update resumen_mensual set estado_factura = 'confirmada' where id = {fx['id']}",
    "des-anular una factura": f"update resumen_mensual set estado_factura = 'confirmada', anulada_en = null where id = {F1}",
    "cambiar el nombre o la cédula de la factura": f"update resumen_mensual set alumna_nombre = 'X', alumna_cedula = null where id = {F2}",
    "bajar el total": f"update resumen_mensual set total = 0, total_lineas = 0 where id = {F2}",
    "cambiar un importe": f"update resumen_mensual_detalle set importe_final = 0 where id_resumen_mensual = {F2}",
    "borrar una línea": f"delete from resumen_mensual_detalle where id_resumen_mensual = {F2}",
    "insertar un pago": f"insert into pago (id_resumen_mensual, monto, medio_pago, fecha) values ({N}, 1, 'efectivo', '2026-07-01')",
    "anular un pago directo": f"update pago set estado = 'anulado' where id_resumen_mensual = {F2}",
    "borrar el historial": f"delete from factura_historial where id_resumen_mensual = {F1}",
    "inventar una anulación": f"insert into pago_anulacion (id_pago, motivo, anulado_por) values ({pg['id']}, 'xxx', '{prof}')",
    "cambiar la cédula de una alumna": f"update profiles set cedula = '98765430' where id = '{A2}'",
}
conns = {"alumna": conectar(uri, A2, "authenticated"), "profesora": conectar(uri, prof, "authenticated"), "sin sesión": ANON}
for quien, conn in conns.items():
    for nombre_intento, sql in intentos.items():
        ok(f"{quien}: no puede {nombre_intento} directo", error_de(conn, sql) == "42501")
for t in TABLAS_F:
    ok(f"sin sesión: no puede leer {t}", error_de(ANON, f"select * from {t}") == "42501")
for fn in ("_cobrables(6, 2026)", "_recalcular_factura(1, true)", "_confirmar(1)", f"_factura_al_dia({F1})", "_firma_mes(6, 2026)"):
    ok(f"alumna no puede ejecutar {fn.split('(')[0]}", error_de(conns["alumna"], f"select public.{fn}") == "42501")
for fn in ("sincronizar_borradores(6, 2026)", "confirmar_factura(1, 1)", "confirmar_facturas_mes(6, 2026, 'x')",
           "registrar_pago(1, 1, 'efectivo', '2026-07-01')", f"actualizar_cedula('{A1}', '12345672')", "anular_factura(1, 'xxxx')"):
    ok(f"sin sesión no puede ejecutar {fn.split('(')[0]}", error_de(ANON, f"select public.{fn}") == "42501")
foto2 = {t: huella(su, t) for t in TABLAS_F}
foto2["profiles"] = huella(su, "profiles")
ok("ningún intento cambió nada", foto2 == foto)

# ---------- compatibilidad ----------
yo = conectar(uri, A4, "authenticated")
ok("reservar y cancelar siguen funcionando", q(yo, "select public.reservar(%s) r", (fut,), True)["r"] == "ok"
   and q(yo, "select public.cancelar_reserva(%s) r", (fut,), True)["r"] == "ok")

print(f"\n{total_casos} casos · {fallas} fallas")
su.close()
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
