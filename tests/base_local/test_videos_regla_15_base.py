"""Regla de los 15 minutos (migración 20261111000000_videos_regla_15_minutos) en un Postgres LOCAL descartable con todas
las migraciones y datos sintéticos: el mismo caso antes y después de la migración, que una grabación registrada con
«Empezar a grabar» a la hora de una clase presencial SIN grabación activada se publique, que una clase CON grabación
activada siga siendo ambigua, que no haya asociaciones incorrectas (otra profesora, otra empresa, segunda grabación,
solicitud cancelada o vencida, reintentos), que solo cambie esa función y ningún dato, y vuelta atrás exacta.
No toca Supabase.

Uso:  python tests/base_local/test_videos_regla_15_base.py
"""
import json
import os
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

MIGRACION = "20261111000000_videos_regla_15_minutos.sql"
VUELTA = os.path.join(os.path.dirname(os.path.dirname(AQUI)), "supabase/pendientes/vuelta_atras_videos_regla_15_minutos.sql")
HUELLA_PROD = "c789cf0e693ee53d2b1dbed2691d533f"  # md5(pg_get_functiondef) de _video_evaluar en producción, 10/11/2026
fallas = total_casos = 0


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


FUNCIONES = ("select p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' f, md5(pg_get_functiondef(p.oid)) h "
             "from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prokind = 'f'")
EVALUAR = "select md5(pg_get_functiondef('public._video_evaluar(uuid,timestamptz,integer,bigint)'::regprocedure)) h"

srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
todas = localdb.migraciones()
assert os.path.basename(todas[-1]) == MIGRACION, "esta migración tiene que ser la última"
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in todas[:-1]:
        cur.execute(open(a).read())
funciones_antes = {f["f"]: f["h"] for f in q(su, FUNCIONES)}
ok("antes de migrar, _video_evaluar es idéntica a la de producción", q(su, EVALUAR, uno=True)["h"] == HUELLA_PROD)


def cuenta(mail, id_empresa, rol):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].title(), "apellido": "Prueba",
                                      "id_empresa": id_empresa, "rol_empresa": rol})))
    q(su, "update public.profiles set empresa_activa = %s where id = %s", (id_empresa, i))
    q(su, "insert into public.video_agente (id_profesora, nombre, token_hash) values (%s, 'Notebook', %s)",
      (i, uuid.uuid4().hex * 2))
    return i


e1 = q(su, "select id from public.empresa where codigo = 'JSU'", uno=True)["id"]
e2 = q(su, "insert into public.empresa (nombre, codigo) values ('Pilates Centro', 'PIL') returning id", uno=True)["id"]
prof1, prof2, prof3, prof4 = (cuenta(f"prof{n}@prueba.invalid", e1, "profesora") for n in (1, 2, 3, 4))
prof5 = cuenta("prof5@prueba.invalid", e2, "profesora")
admin1 = cuenta("admin1@prueba.invalid", e1, "admin")
tipo = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
             "values (%s, 'PRUEBA-40', 40, 800, 7) returning id", (e1,), uno=True)["id"]
tipo2 = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
              "values (%s, 'PRUEBA-PIL', 40, 800, 7) returning id", (e2,), uno=True)["id"]
srvc = ClienteFalso(uri, None, rol="service_role")
cli = {p: ClienteFalso(uri, p) for p in (prof1, prof2, prof3, prof4, prof5)}
administradora1 = ClienteFalso(uri, admin1)
hc = q(su, "select to_char((now() + interval '60 minutes') at time zone 'America/Montevideo', 'HH24:MI') h, "
           "((now() + interval '60 minutes') at time zone 'America/Montevideo')::date f", uno=True)


def clase_de(profesora, empresa=None, tipo_id=None):
    empresa, tipo_id = empresa or e1, tipo_id or tipo
    h = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, cupo_max, "
              "precio, activo) values (%s, %s, %s, 'lunes', %s, 40, 7, 800, true) returning id",
          (empresa, profesora, tipo_id, hc["h"]), uno=True)["id"]
    c = q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, duracion, precio, "
              "cupo_max) values (%s, %s, %s, %s, %s, %s, 40, 800, 7) returning id",
          (empresa, profesora, h, tipo_id, hc["f"], hc["h"]), uno=True)["id"]
    return h, c


def ahora_mas(minutos):
    return q(su, "select to_char(now() + make_interval(mins => %s), 'YYYY-MM-DD\"T\"HH24:MI:SSOF') t", (minutos,), uno=True)["t"]


def consultar(profesora, inicio, duracion=2400):
    return rpc(srvc, "video_consultar", p_id_profesora=profesora, p_inicio=inicio, p_duracion_seg=duracion)


def registrar(profesora, inicio, duracion=2400, tamano=40000000):
    return rpc(srvc, "video_registrar", p_id_profesora=profesora, p_inicio=inicio, p_huella=uuid.uuid4().hex * 2,
               p_duracion_seg=duracion, p_tamano_bytes=tamano,
               p_partes=json.dumps([{"nombre": "parte-00.ts", "bytes": tamano}]), p_lista_hls="#EXTM3U\nparte-00.ts\n")


def solicitud(profesora, titulo="Yoga suave", tipo_id=None):
    return rpc(cli[profesora], "grabacion_independiente_crear", p_id_tipo_clase=tipo_id or tipo, p_titulo=titulo)


def huella_tablas():
    filas = {}
    for t in ("reserva", "listadeespera", "asistencia", "asistencia_historial", "bloqueo", "clase", "horario",
              "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "tipo_clase", "video_programacion"):
        filas[t] = q(su, f"select count(*) n, md5(coalesce(string_agg(to_jsonb(x)::text, ',' order by x.id), '')) h "
                         f"from public.{t} x", uno=True)
    return filas


# Escenario: prof1 tiene una clase presencial SIN grabación activada a las +60 min y una solicitud esperando.
#            prof2 tiene una clase CON grabación activada a las +60 min y una solicitud esperando.
#            prof3 tiene una clase con grabación activada y ninguna solicitud (camino de clases programadas).
#            prof4 tiene una clase sin grabación activada y ninguna solicitud.
_, c1 = clase_de(prof1)
h2, c2 = clase_de(prof2)
h3, c3 = clase_de(prof3)
_, c4 = clase_de(prof4)
for h in (h2, h3):
    rpc(administradora1, "video_programar", p_id_horario=h, p_activa=True)
    q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (h,))
g1 = solicitud(prof1)["id"]
g2 = solicitud(prof2)["id"]
I_CLASE = ahora_mas(60)

# ---------- antes de la migración (comportamiento de producción hoy) ----------
ok("ANTES: grabación registrada a la hora de una clase sin grabación activada → clase_ambigua (no se publica)",
   consultar(prof1, I_CLASE)["resultado"] == "clase_ambigua")
ok("ANTES: sin solicitud, cerca de una clase sin grabación activada → no_autorizada", consultar(prof4, I_CLASE)["resultado"] == "no_autorizada")

datos_antes = huella_tablas()
with su.cursor() as cur:
    cur.execute(open(todas[-1]).read())
funciones_despues = {f["f"]: f["h"] for f in q(su, FUNCIONES)}
cambiadas = sorted(f for f in funciones_despues if funciones_despues[f] != funciones_antes.get(f))
ok("la migración cambia solo _video_evaluar", cambiadas == ["_video_evaluar(p_id_profesora uuid, p_inicio timestamp with time zone, "
                                                            "p_duracion_seg integer, p_tamano_bytes bigint)"], cambiadas)
ok("la migración no cambia ningún dato", huella_tablas() == datos_antes)
ok("permisos de _video_evaluar sin cambios (nadie la ejecuta desde la API)",
   q(su, "select has_function_privilege('authenticated', 'public._video_evaluar(uuid,timestamptz,integer,bigint)', 'execute') a, "
         "has_function_privilege('anon', 'public._video_evaluar(uuid,timestamptz,integer,bigint)', 'execute') b", uno=True)
   == {"a": False, "b": False})

# ---------- después: lo que se arregla ----------
r = consultar(prof1, I_CLASE)
ok("DESPUÉS: la misma grabación se asocia a SU solicitud", r == {"resultado": "autorizada", "id_grabacion": g1, "id_empresa": e1}, r)
r = registrar(prof1, I_CLASE)
v1 = r.get("id_video")
pub = rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof1) if v1 else {}
fila = q(su, "select id_grabacion, id_clase, estado, id_empresa from public.video where id = %s", (v1,), uno=True) if v1 else None
ok("y se publica como grabación independiente, sin tocar la clase presencial",
   r.get("resultado") == "ok" and pub.get("resultado") == "ok"
   and fila == {"id_grabacion": g1, "id_clase": None, "estado": "publicado", "id_empresa": e1}, (r, pub, fila))
ok("su reintento (misma hora de inicio) no duplica: ya_publicado",
   consultar(prof1, I_CLASE) == {"resultado": "ya_publicado", "id_video": v1})

# ---------- lo que NO cambia (sin asociaciones incorrectas) ----------
ok("clase CON grabación activada + solicitud a la misma hora: sigue clase_ambigua (no se adivina)",
   consultar(prof2, I_CLASE)["resultado"] == "clase_ambigua")
ok("lejos de esa clase, la solicitud de prof2 sí se asocia", consultar(prof2, ahora_mas(5)) == {
    "resultado": "autorizada", "id_grabacion": g2, "id_empresa": e1})
r = consultar(prof3, I_CLASE)
ok("clase con grabación activada y sin solicitud: se asocia a la clase, como siempre",
   r == {"resultado": "autorizada", "id_clase": c3, "id_empresa": e1}, r)
v3 = registrar(prof3, I_CLASE).get("id_video")
rpc(srvc, "video_publicar", p_id_video=v3, p_id_profesora=prof3)
q(su, "update public.video_programacion set activa = false where id_horario = %s", (h3,))
ok("si después se desactiva el horario, el reintento de ese video sigue siendo ya_publicado (no se pierde ni se duplica)",
   consultar(prof3, I_CLASE) == {"resultado": "ya_publicado", "id_video": v3})
ok("sin solicitud, cerca de una clase sin grabación activada: no_autorizada, igual que antes (no se sube nada)",
   consultar(prof4, I_CLASE)["resultado"] == "no_autorizada")
ok("una SEGUNDA grabación de prof1 (otra hora) después de usar la solicitud: sin_clase, no se pega a la primera",
   consultar(prof1, ahora_mas(100))["resultado"] == "sin_clase")
ok("otra profesora de la misma empresa a la misma hora: sin_clase (no toma la solicitud ajena)",
   consultar(prof4, ahora_mas(5))["resultado"] == "sin_clase")
g5 = solicitud(prof5, tipo_id=tipo2)["id"]
ok("profesora de otra empresa: su video va a SU solicitud y a SU empresa",
   consultar(prof5, I_CLASE) == {"resultado": "autorizada", "id_grabacion": g5, "id_empresa": e2})
ok("una segunda solicitud mientras otra espera sigue rechazada: ya_hay_una", solicitud(prof5, "Otra", tipo_id=tipo2)["resultado"] == "ya_hay_una")
rpc(cli[prof5], "grabacion_independiente_cancelar", p_id=g5)
ok("solicitud cancelada: sin_clase", consultar(prof5, I_CLASE)["resultado"] == "sin_clase")
g4 = solicitud(prof4)["id"]
ok("empezada 5 minutos antes de registrarla: sin_clase (tolerancia de 2 minutos sin cambios)",
   consultar(prof4, ahora_mas(-5))["resultado"] == "sin_clase")
ok("empezada después del plazo de 3 horas: sin_clase", consultar(prof4, ahora_mas(181))["resultado"] == "sin_clase")
ok("dentro del plazo, aunque haya una clase suya sin grabación activada a esa hora: su solicitud",
   consultar(prof4, I_CLASE) == {"resultado": "autorizada", "id_grabacion": g4, "id_empresa": e1})
ok("duración fuera de rango sigue rechazada (30 s)", consultar(prof4, I_CLASE, 30)["resultado"] == "duracion_fuera_de_rango")
ok("las reservas, asistencias, facturación, planificación, clases y horarios no cambiaron (salvo la desactivación de la prueba)",
   {k: v for k, v in huella_tablas().items() if k != "video_programacion"}
   == {k: v for k, v in datos_antes.items() if k != "video_programacion"})

# ---------- vuelta atrás ----------
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("vuelta atrás: _video_evaluar idéntica a la de producción", q(su, EVALUAR, uno=True)["h"] == HUELLA_PROD)
ok("vuelta atrás: todas las funciones como antes de la migración",
   {f["f"]: f["h"] for f in q(su, FUNCIONES)} == funciones_antes)
ok("vuelta atrás: vuelve el comportamiento anterior (clase_ambigua con una clase sin grabación activada)",
   consultar(prof4, I_CLASE)["resultado"] == "clase_ambigua")

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} OK")
sys.exit(1 if fallas else 0)
