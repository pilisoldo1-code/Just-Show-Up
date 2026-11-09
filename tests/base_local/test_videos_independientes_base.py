"""Grabaciones independientes de Videos On Demand (migración 20261110000000_videos_grabaciones_independientes) en un
Postgres LOCAL descartable con todas las migraciones y datos sintéticos: quién las crea, validaciones, una por profesora,
asociación inequívoca (plazo, profesora, ambigüedad con clases y entre solicitudes), duraciones distintas, registro y
reintentos, publicación, vencimiento a las 24 h, limpieza, cancelación, multiempresa, espacio ocupado, que no toque
reservas/asistencias/facturación/planificación, que las clases programadas sigan igual y vuelta atrás exacta.
No toca Supabase.

Uso:  python tests/base_local/test_videos_independientes_base.py
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

MIGRACION = "20261110000000_videos_grabaciones_independientes.sql"
VUELTA = os.path.join(os.path.dirname(os.path.dirname(AQUI)),
                      "supabase/pendientes/vuelta_atras_videos_grabaciones_independientes.sql")
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


def como(cli, sentencia, args=None):
    try:
        with cli.conn.cursor() as cur:
            cur.execute(sentencia, args)
            return cur.fetchall() if cur.description else None
    except psycopg2.Error as e:
        return {"error": e.pgcode}


HUELLA_FUNCIONES = ("select string_agg(p.proname || ':' || md5(pg_get_functiondef(p.oid)), ',' order by p.proname, p.oid) s "
                    "from pg_proc p where p.pronamespace = 'public'::regnamespace")

srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
todas = localdb.migraciones()
assert os.path.basename(todas[-1]) == MIGRACION, "esta migración tiene que ser la última"
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in todas[:-1]:
        cur.execute(open(a).read())
FUNCIONES_ANTES = q(su, HUELLA_FUNCIONES, uno=True)["s"]
with su.cursor() as cur:
    cur.execute(open(todas[-1]).read())


def cuenta(mail, id_empresa, rol):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].title(), "apellido": "Prueba",
                                      "id_empresa": id_empresa, "rol_empresa": rol})))
    q(su, "update public.profiles set empresa_activa = %s where id = %s", (id_empresa, i))
    return i


def agente(profesora):
    q(su, "insert into public.video_agente (id_profesora, nombre, token_hash) values (%s, 'Notebook', %s)",
      (profesora, uuid.uuid4().hex * 2))


e1 = q(su, "select id from public.empresa where codigo = 'JSU'", uno=True)["id"]
e2 = q(su, "insert into public.empresa (nombre, codigo) values ('Pilates Centro', 'PIL') returning id", uno=True)["id"]
prof1, prof1b = cuenta("prof1@prueba.invalid", e1, "profesora"), cuenta("prof1b@prueba.invalid", e1, "profesora")
prof2 = cuenta("prof2@prueba.invalid", e2, "profesora")
admin1, admin2 = cuenta("admin1@prueba.invalid", e1, "admin"), cuenta("admin2@prueba.invalid", e2, "admin")
alu1, alu2 = cuenta("alu1@prueba.invalid", e1, "alumna"), cuenta("alu2@prueba.invalid", e2, "alumna")
agente(prof1)
agente(prof2)


def tipo(id_empresa, nombre, activo=True, duracion=60):
    return q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max, activo) "
                 "values (%s, %s, %s, 800, 7, %s) returning id", (id_empresa, nombre, duracion, activo), uno=True)["id"]


TIPO1, TIPO1_INACTIVO, TIPO2 = tipo(e1, "Yoga"), tipo(e1, "Pilates viejo", activo=False), tipo(e2, "Pilates mat", duracion=50)

srvc = ClienteFalso(uri, None, rol="service_role")
anon = ClienteFalso(uri, None, rol="anon")
profesora1, profesora1b, profesora2 = ClienteFalso(uri, prof1), ClienteFalso(uri, prof1b), ClienteFalso(uri, prof2)
administradora1, administradora2 = ClienteFalso(uri, admin1), ClienteFalso(uri, admin2)
alumna1, alumna2 = ClienteFalso(uri, alu1), ClienteFalso(uri, alu2)


def ahora_mas(minutos):
    return q(su, "select to_char(now() + make_interval(mins => %s), 'YYYY-MM-DD\"T\"HH24:MI:SSOF') t", (minutos,), uno=True)["t"]


def consultar(profesora, inicio, duracion=2400):
    return rpc(srvc, "video_consultar", p_id_profesora=profesora, p_inicio=inicio, p_duracion_seg=duracion)


PARTES = [{"nombre": "parte-00.ts", "bytes": 40000000}]
LISTA = "#EXTM3U\nparte-00.ts\n"


def registrar(profesora, inicio, duracion=2400, tamano=40000000):
    return rpc(srvc, "video_registrar", p_id_profesora=profesora, p_inicio=inicio, p_huella=uuid.uuid4().hex * 2,
               p_duracion_seg=duracion, p_tamano_bytes=tamano,
               p_partes=json.dumps([{"nombre": "parte-00.ts", "bytes": tamano}]), p_lista_hls=LISTA)


def huella_tablas():
    filas = {}
    for t in ("reserva", "listadeespera", "asistencia", "asistencia_historial", "bloqueo", "clase", "horario",
              "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "tipo_clase"):
        filas[t] = q(su, f"select count(*) n, md5(coalesce(string_agg(to_jsonb(x)::text, ',' order by x.id), '')) h from public.{t} x",
                     uno=True)
    return filas


# ---------- estructura ----------
ok("la columna id_clase de video ahora es opcional",
   q(su, "select is_nullable from information_schema.columns where table_name = 'video' and column_name = 'id_clase'", uno=True)["is_nullable"] == "YES")
ok("un video sin clase ni grabación se rechaza",
   como(srvc, "insert into public.video (id_empresa, id_profesora, huella_sha256, duracion_seg, tamano_bytes, partes, lista_hls) "
              "values (%s, %s, %s, 60, 1, '[]', 'x')", (e1, prof1, "a" * 64)) == {"error": "23514"})
ok("parámetros por defecto: plazo 3 h, tolerancia 2 min, de 1 min a 4 h",
   q(su, "select independiente_ventana_seg v, independiente_tolerancia_seg t, independiente_min_seg mi, independiente_max_seg ma "
         "from public.video_parametros", uno=True) == {"v": 10800, "t": 120, "mi": 60, "ma": 14400})

ANTES = huella_tablas()

# ---------- quién crea ----------
ok("anon no puede crear", isinstance(rpc(anon, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Yoga"), dict)
   and rpc(anon, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Yoga").get("error") == "42501")
ok("una alumna no puede crear", rpc(alumna1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Yoga")["resultado"] == "no_autorizada")
ok("tipo de clase inactivo de su empresa: tipo_invalido",
   rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1_INACTIVO, p_titulo="Yoga")["resultado"] == "tipo_invalido")
ok("tipo de clase de OTRA empresa: tipo_invalido",
   rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO2, p_titulo="Yoga")["resultado"] == "tipo_invalido")
ok("sin tipo de clase: tipo_invalido",
   rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=None, p_titulo="Yoga")["resultado"] == "tipo_invalido")
ok("tipo inexistente: tipo_invalido",
   rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=999999, p_titulo="Yoga")["resultado"] == "tipo_invalido")
ok("una profesora sin computadora autorizada no puede (sin_computadora)",
   rpc(profesora1b, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Yoga")["resultado"] == "sin_computadora")
ok("título de 2 caracteres: datos_invalidos", rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo=" Yo ")["resultado"] == "datos_invalidos")
ok("título de 81 caracteres: datos_invalidos", rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="x" * 81)["resultado"] == "datos_invalidos")
ok("descripción de 301 caracteres: datos_invalidos",
   rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Yoga", p_descripcion="d" * 301)["resultado"] == "datos_invalidos")
r1 = rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="  Yoga suave  ", p_descripcion="  Para empezar  ")
g1 = r1.get("id")
ok("la profesora con computadora la crea", r1.get("resultado") == "ok" and g1, r1)
fila = q(su, "select id_empresa, id_profesora, titulo, id_tipo_clase, descripcion, inicio_grabacion, "
             "extract(epoch from vence_espera_en - creada_en)::int plazo from public.grabacion_independiente where id = %s", (g1,), uno=True)
ok("queda en su empresa, a su nombre, con el texto limpio y 3 horas de plazo",
   fila == {"id_empresa": e1, "id_profesora": prof1, "titulo": "Yoga suave", "id_tipo_clase": TIPO1, "descripcion": "Para empezar",
            "inicio_grabacion": None, "plazo": 10800}, fila)
ok("una segunda mientras espera: ya_hay_una", rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Pilates")["resultado"] == "ya_hay_una")
for nombre, cli in (("la profesora", profesora1), ("la administradora", administradora1), ("la alumna", alumna1)):
    r = como(cli, "insert into public.grabacion_independiente (id_empresa, id_profesora, titulo, vence_espera_en) "
                  "values (%s, %s, 'Directo', now() + interval '1 hour')", (e1, prof1))
    ok(f"{nombre} no puede escribir directo en la tabla", isinstance(r, dict) and r.get("error") == "42501", r)
ok("la base no acepta una grabación con un tipo de otra empresa, ni con la clave de servidor",
   como(srvc, "insert into public.grabacion_independiente (id_empresa, id_profesora, titulo, id_tipo_clase, vence_espera_en) "
              "values (%s, %s, 'Cruzada', %s, now() + interval '1 hour')", (e1, prof1, TIPO2)) == {"error": "23503"})
ok("ni cambiarle el tipo a uno de otra empresa",
   como(srvc, "update public.grabacion_independiente set id_tipo_clase = %s where id = %s", (TIPO2, g1)) == {"error": "23503"})
ok("la profesora no puede modificarla directo", como(profesora1, "update public.grabacion_independiente set titulo = 'Otro'") == {"error": "42501"})


def ve(cli):
    r = como(cli, "select id from public.grabacion_independiente")
    return r if isinstance(r, dict) else [x[0] for x in r]


ok("la profesora ve la suya", ve(profesora1) == [g1])
ok("otra profesora de la misma empresa no la ve", ve(profesora1b) == [])
ok("la administradora de su empresa la ve", ve(administradora1) == [g1])
ok("la administradora de otra empresa no la ve", ve(administradora2) == [])
ok("una alumna no la ve", ve(alumna1) == [])
ok("mis grabaciones: esperando, con su tipo de clase",
   [(g["id"], g["estado"], g["tipo"]) for g in rpc(profesora1, "mis_grabaciones_independientes")] == [(g1, "esperando", "Yoga")])
ok("otra profesora no la tiene en las suyas", rpc(profesora1b, "mis_grabaciones_independientes") == [])

# ---------- asociación ----------
I1 = ahora_mas(5)
r = consultar(prof1, I1)
ok("una grabación que empieza dentro del plazo se asocia a ESA solicitud y empresa",
   r == {"resultado": "autorizada", "id_grabacion": g1, "id_empresa": e1}, r)
ok("empezada 1 minuto antes de crearla (tolerancia de reloj) también", consultar(prof1, ahora_mas(-1))["resultado"] == "autorizada")
ok("empezada 5 minutos antes de crearla: sin_clase", consultar(prof1, ahora_mas(-5))["resultado"] == "sin_clase")
ok("empezada después del plazo de 3 horas: sin_clase", consultar(prof1, ahora_mas(181))["resultado"] == "sin_clase")
ok("otra profesora a la misma hora: sin_clase (no es suya)", consultar(prof2, I1)["resultado"] == "sin_clase")
ok("otra profesora de la MISMA empresa: sin_clase", consultar(prof1b, I1)["resultado"] == "sin_clase")
for seg, esperado, txt in ((30, "duracion_fuera_de_rango", "30 s"), (60, "autorizada", "1 min"), (125, "autorizada", "2 min 5 s"),
                           (2400, "autorizada", "40 min"), (7200, "autorizada", "2 h"), (14400, "autorizada", "4 h"),
                           (14401, "duracion_fuera_de_rango", "4 h + 1 s")):
    ok(f"duración {txt}: {esperado} (no se compara con ninguna clase)", consultar(prof1, I1, seg)["resultado"] == esperado)

# ambigüedad con una clase de la misma profesora
hora_clase = q(su, "select to_char((now() + interval '60 minutes') at time zone 'America/Montevideo', 'HH24:MI') h, "
                   "((now() + interval '60 minutes') at time zone 'America/Montevideo')::date f", uno=True)
t40 = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
            "values (%s, 'PRUEBA-40', 40, 800, 7) returning id", (e1,), uno=True)["id"]
h_amb = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, cupo_max, "
              "precio, activo) values (%s, %s, %s, 'lunes', %s, 40, 7, 800, true) returning id", (e1, prof1, t40, hora_clase["h"]), uno=True)["id"]
c_amb = q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, duracion, precio, "
              "cupo_max) values (%s, %s, %s, %s, %s, %s, 40, 800, 7) returning id",
          (e1, prof1, h_amb, t40, hora_clase["f"], hora_clase["h"]), uno=True)["id"]
rpc(administradora1, "video_programar", p_id_horario=h_amb, p_activa=True)
q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (h_amb,))
ANTES = huella_tablas()  # desde acá la clase de prueba ya existe: lo de abajo no tiene que tocar nada de esto
I_CLASE = ahora_mas(60)
ok("a la hora de una clase SUYA y dentro del plazo: clase_ambigua (no se adivina)", consultar(prof1, I_CLASE)["resultado"] == "clase_ambigua")
n_videos = q(su, "select count(*) n from public.video", uno=True)["n"]
ok("registrarla tampoco pasa y no crea ningún video",
   registrar(prof1, I_CLASE).get("resultado") == "clase_ambigua" and q(su, "select count(*) n from public.video", uno=True)["n"] == n_videos)
ok("lejos de la clase (55 min antes) sigue siendo de la solicitud", consultar(prof1, I1)["resultado"] == "autorizada")

# ---------- registro, reintento y una sola grabación por solicitud ----------
r = registrar(prof1, I1, duracion=2400)
v1 = r.get("id_video")
ok("registrar la crea como video de la grabación, sin clase", r.get("resultado") == "ok"
   and q(su, "select id_empresa, id_clase, id_grabacion, duracion_seg, estado from public.video where id = %s", (v1,), uno=True)
   == {"id_empresa": e1, "id_clase": None, "id_grabacion": g1, "duracion_seg": 2400, "estado": "subiendo"}, r)
ok("la solicitud queda atada a esa grabación (hora de inicio guardada)",
   q(su, "select inicio_grabacion = %s::timestamptz a from public.grabacion_independiente where id = %s", (I1, g1), uno=True)["a"])
r = registrar(prof1, I1, duracion=2401)
ok("un reintento de la misma grabación usa el mismo video", r.get("resultado") == "ok" and r.get("id_video") == v1
   and q(su, "select intentos from public.video where id = %s", (v1,), uno=True)["intentos"] == 2, r)
I2 = ahora_mas(20)
ok("OTRA grabación dentro del mismo plazo ya no se asocia a esa solicitud: sin_clase", consultar(prof1, I2)["resultado"] == "sin_clase")
ok("mis grabaciones: procesando", rpc(profesora1, "mis_grabaciones_independientes")[0]["estado"] == "procesando")
ok("no se puede cancelar una que ya tiene grabación", rpc(profesora1, "grabacion_independiente_cancelar", p_id=g1)["resultado"] == "no_se_puede")

r2 = rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Estiramientos")
g2 = r2.get("id")
ok("con la primera ya grabada puede preparar otra", r2.get("resultado") == "ok" and g2 and g2 != g1, r2)
r = consultar(prof1, I2)
ok("la grabación nueva va a la solicitud NUEVA, nunca a la anterior", r.get("id_grabacion") == g2 and r.get("resultado") == "autorizada", r)
ok("y la grabación de la primera sigue yendo a la primera", consultar(prof1, I1).get("id_grabacion") == g1)

# ---------- publicación y quién la ve ----------
r = rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof1)
ok("se publica", r.get("resultado") == "ok", r)
ok("vence exactamente 24 h después de publicarse (no desde la grabación)",
   q(su, "select vence_en - publicado_en = interval '24 hours' a from public.video where id = %s", (v1,), uno=True)["a"])


def biblioteca(cli):
    return {x["id"]: x for x in (rpc(cli, "videos_disponibles") or [])}


b = biblioteca(alumna1)
ok("la alumna de la empresa la ve con su título, marcada como grabación especial",
   v1 in b and b[v1]["clase"] == "Yoga suave" and b[v1]["independiente"] is True and b[v1]["fecha"] and b[v1]["hora_inicio"], b.get(v1))
ok("y con su tipo de clase", b[v1]["tipo"] == "Yoga")
ok("la duración es la real del video, no la del tipo (60 min)", b[v1]["duracion_seg"] == 2401)
ok("la profesora y la administradora de la empresa también", v1 in biblioteca(profesora1b) and v1 in biblioteca(administradora1))
ok("la alumna de otra empresa no", v1 not in biblioteca(alumna2))
ok("la administradora de otra empresa no", v1 not in biblioteca(administradora2))
ok("por la tabla, la alumna de otra empresa no ve la fila", como(alumna2, "select id from public.video where id = %s", (v1,)) == [])
ok("anon no ve nada", isinstance(como(anon, "select id from public.video"), dict))
ok("iniciar la reproducción funciona igual que en las clases", rpc(alumna1, "video_iniciar", p_id_video=v1).get("resultado") == "ok")
ok("y la otra empresa no puede iniciarla", rpc(alumna2, "video_iniciar", p_id_video=v1).get("resultado") != "ok")
ok("mis grabaciones: publicada, con el enlace al video",
   [(g["estado"], g["id_video"]) for g in rpc(profesora1, "mis_grabaciones_independientes") if g["id"] == g1] == [("publicada", v1)])
ok("en Últimas grabaciones de la administradora aparece con su título",
   any(x["id"] == v1 and x["clase"] == "Yoga suave" and x["independiente"] for x in rpc(administradora1, "videos_ultimas_grabaciones")))
ok("la administradora de otra empresa no la ve en sus últimas grabaciones",
   all(x["id"] != v1 for x in rpc(administradora2, "videos_ultimas_grabaciones")))

# ---------- espacio ocupado: los videos independientes cuentan también para las clases ----------
q(su, "update public.video_parametros set almacenamiento_max_bytes = 70000000, tamano_estimado_bytes = 40000000")
ok("con 40 MB de una grabación independiente y un tope de 70 MB, una clase de 40 MB no entra (sin_espacio)",
   consultar(prof1, I_CLASE)["resultado"] in ("sin_espacio", "clase_ambigua"))
rpc(profesora1, "grabacion_independiente_cancelar", p_id=g2)
ok("(sin la solicitud nueva) la clase sola: sin_espacio", consultar(prof1, I_CLASE)["resultado"] == "sin_espacio")
q(su, "update public.video_parametros set almacenamiento_max_bytes = 800000000, tamano_estimado_bytes = 130000000")
ok("con el tope normal la clase programada vuelve a autorizarse (id_clase, sin grabación)",
   consultar(prof1, I_CLASE) == {"resultado": "autorizada", "id_clase": c_amb, "id_empresa": e1})

# ---------- cancelación ----------
ok("la solicitud cancelada no toma grabaciones: sin_clase", consultar(prof1, I2)["resultado"] == "sin_clase")
ok("cancelarla de nuevo: no_se_puede", rpc(profesora1, "grabacion_independiente_cancelar", p_id=g2)["resultado"] == "no_se_puede")
r3 = rpc(profesora1, "grabacion_independiente_crear", p_id_tipo_clase=TIPO1, p_titulo="Entrenamiento")
g3 = r3.get("id")
ok("otra profesora no puede cancelar la ajena", rpc(profesora1b, "grabacion_independiente_cancelar", p_id=g3)["resultado"] == "no_encontrada")
ok("la administradora tampoco cancela la de una profesora", rpc(administradora1, "grabacion_independiente_cancelar", p_id=g3)["resultado"] == "no_encontrada")
ok("mis grabaciones muestra la cancelada", [g["estado"] for g in rpc(profesora1, "mis_grabaciones_independientes") if g["id"] == g2] == ["cancelada"])

# ---------- profesora desactivada antes de publicar ----------
I3 = ahora_mas(30)
v3 = registrar(prof1, I3, duracion=900).get("id_video")
q(su, "update public.membresia set activa = false where id_usuario = %s and id_empresa = %s", (prof1, e1))
ok("si la profesora ya no está activa, no se publica", rpc(srvc, "video_publicar", p_id_video=v3, p_id_profesora=prof1).get("resultado") == "no_autorizada")
ok("ni se asocian grabaciones nuevas", consultar(prof1, I3)["resultado"] == "no_autorizada")
q(su, "update public.membresia set activa = true where id_usuario = %s and id_empresa = %s", (prof1, e1))
ok("reactivada, se publica (15 min: distinta duración que la anterior)",
   rpc(srvc, "video_publicar", p_id_video=v3, p_id_profesora=prof1).get("resultado") == "ok")

# ---------- otra empresa ----------
rp = rpc(profesora2, "grabacion_independiente_crear", p_id_tipo_clase=TIPO2, p_titulo="Pilates en casa")
I4 = ahora_mas(10)
r = consultar(prof2, I4, 1800)
ok("la profesora de Pilates Centro tiene la suya, en SU empresa",
   rp.get("resultado") == "ok" and r == {"resultado": "autorizada", "id_grabacion": rp["id"], "id_empresa": e2}, r)
v4 = registrar(prof2, I4, duracion=1800).get("id_video")
rpc(srvc, "video_publicar", p_id_video=v4, p_id_profesora=prof2)
ok("la ven sus alumnas y no las de Just Show Up", v4 in biblioteca(alumna2) and v4 not in biblioteca(alumna1))
ok("con el tipo de clase de SU empresa", biblioteca(alumna2)[v4]["tipo"] == "Pilates mat")
ok("el video independiente de JSU sigue sin verse en Pilates", v1 not in biblioteca(alumna2) and v3 not in biblioteca(alumna2))

# ---------- vencimiento y limpieza (los mismos de siempre) ----------
q(su, "update public.video set publicado_en = now() - interval '25 hours', vence_en = now() - interval '1 hour' where id = %s", (v1,))
ok("vencido (24 h), la alumna ya no lo ve ni lo puede iniciar",
   v1 not in biblioteca(alumna1) and rpc(alumna1, "video_iniciar", p_id_video=v1).get("resultado") != "ok")
ok("los demás videos (otra grabación y otra empresa) siguen disponibles", v3 in biblioteca(alumna1) and v4 in biblioteca(alumna2))
ok("la limpieza existente lo encuentra", any(x["id"] == v1 for x in rpc(srvc, "videos_a_limpiar")))
ok("y solo los vencidos", not any(x["id"] in (v3, v4) for x in rpc(srvc, "videos_a_limpiar")))
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
ok("queda eliminado, con el registro conservado", q(su, "select estado, id_grabacion from public.video where id = %s", (v1,), uno=True)
   == {"estado": "eliminado", "id_grabacion": g1})
ok("mis grabaciones: vencida", [g["estado"] for g in rpc(profesora1, "mis_grabaciones_independientes") if g["id"] == g1] == ["vencida"])
ok("la misma grabación no se vuelve a publicar: ya_eliminado", consultar(prof1, I1)["resultado"] == "ya_eliminado")

# ---------- sin efectos fuera de Videos On Demand ----------
ok("no se tocaron reservas, listas de espera, asistencias, bloqueos, clases, horarios, facturas ni pagos", huella_tablas() == ANTES)

# ---------- las clases programadas siguen igual ----------
agente(prof1b)
t40b = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
             "values (%s, 'PRUEBA-40b', 40, 800, 7) returning id", (e1,), uno=True)["id"]
hb = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, cupo_max, "
           "precio, activo) values (%s, %s, %s, 'lunes', '08:00', 40, 7, 800, true) returning id", (e1, prof1b, t40b), uno=True)["id"]
cb = q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, duracion, precio, cupo_max) "
           "values (%s, %s, %s, %s, '2026-11-16', '08:00', 40, 800, 7) returning id", (e1, prof1b, hb, t40b), uno=True)["id"]
rpc(administradora1, "video_programar", p_id_horario=hb, p_activa=True)
q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (hb,))
ok("clase programada de 40 min: 29:59 no se publica", consultar(prof1b, "2026-11-16T08:02:00-03:00", 1799)["resultado"] == "duracion_fuera_de_rango")
r = registrar(prof1b, "2026-11-16T08:02:00-03:00", duracion=2340)
vb = r.get("id_video")
ok("y se registra como video de su clase, sin grabación independiente",
   q(su, "select id_clase, id_grabacion from public.video where id = %s", (vb,), uno=True) == {"id_clase": cb, "id_grabacion": None}, r)
ok("y se publica como siempre", rpc(srvc, "video_publicar", p_id_video=vb, p_id_profesora=prof1b).get("resultado") == "ok")
ok("con el nombre de su tipo de clase y sin la marca de especial",
   biblioteca(alumna1)[vb]["clase"] == "PRUEBA-40b" and biblioteca(alumna1)[vb]["independiente"] is False
   and biblioteca(alumna1)[vb]["tipo"] == "PRUEBA-40b")

# ---------- duraciones con Zoom Básico: clases de 60 y 50 min (parciales) e independientes cortas ----------
prof60, prof50 = cuenta("prof60@prueba.invalid", e1, "profesora"), cuenta("prof50@prueba.invalid", e2, "profesora")
for prof in (prof60, prof50):
    agente(prof)
c60 = None
for prof, empresa, dur, admin, fecha, hora in ((prof60, e1, 60, administradora1, "2026-11-17", "08:00"),
                                              (prof50, e2, 50, administradora2, "2026-11-17", "19:00")):
    tt = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
               "values (%s, %s, %s, 800, 7) returning id", (empresa, f"PRUEBA-{dur}", dur), uno=True)["id"]
    hh = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, cupo_max, "
               "precio, activo) values (%s, %s, %s, 'martes', %s, %s, 7, 800, true) returning id", (empresa, prof, tt, hora, dur), uno=True)["id"]
    cc = q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, duracion, precio, "
               "cupo_max) values (%s, %s, %s, %s, %s, %s, %s, 800, 7) returning id", (empresa, prof, hh, tt, fecha, hora, dur), uno=True)["id"]
    c60 = c60 or cc
    ok(f"un horario de {dur} min se puede activar", rpc(admin, "video_programar", p_id_horario=hh, p_activa=True)["resultado"] == "ok")
    q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (hh,))
    inicio = f"{fecha}T{hora}:30-03:00"
    for seg, esperado, txt in ((1799, "duracion_fuera_de_rango", "29:59"), (1800, "autorizada", "30:00"),
                               (2400, "autorizada", "40:00 (corte de Zoom Básico)"), (dur * 60, "autorizada", f"{dur}:00 (completa)"),
                               (dur * 60 + 60, "autorizada", f"{dur + 1}:00"), (dur * 60 + 61, "duracion_fuera_de_rango", f"{dur + 1}:01"),
                               (5, "duracion_fuera_de_rango", "5 s (vacía)")):
        r = consultar(prof, inicio, seg)
        ok(f"clase de {dur} min, grabación de {txt}: {esperado}", r["resultado"] == esperado
           and (esperado != "autorizada" or (r.get("id_clase") == cc and r.get("id_empresa") == empresa)), r)
r = registrar(prof60, "2026-11-17T08:00:30-03:00", duracion=2400)
ok("una grabación parcial de 40 min de la clase de 60 se registra con su duración REAL y se publica",
   r.get("resultado") == "ok" and q(su, "select duracion_seg, id_clase from public.video where id = %s", (r["id_video"],), uno=True)
   == {"duracion_seg": 2400, "id_clase": c60}
   and rpc(srvc, "video_publicar", p_id_video=r["id_video"], p_id_profesora=prof60).get("resultado") == "ok")
ok("vence a las 24 h desde la publicación, como todas",
   q(su, "select vence_en - publicado_en = interval '24 hours' a from public.video where id = %s", (r["id_video"],), uno=True)["a"])
TIPO60 = tipo(e1, "Gimnasia corta")
rr = rpc(ClienteFalso(uri, prof60), "grabacion_independiente_crear", p_titulo="Estiramientos rápidos", p_id_tipo_clase=TIPO60)
ini = ahora_mas(3)
for minutos in (10, 15, 20):
    ok(f"grabación independiente de {minutos} min: autorizada (no se exige 30 ni 40)",
       consultar(prof60, ini, minutos * 60) == {"resultado": "autorizada", "id_grabacion": rr["id"], "id_empresa": e1})
ok("independiente de 59 s (accidental): no", consultar(prof60, ini, 59)["resultado"] == "duracion_fuera_de_rango")

# ---------- vuelta atrás ----------
with su.cursor() as cur:
    try:
        cur.execute(open(VUELTA).read())
        se_nego = False
    except psycopg2.Error:
        se_nego = True
ok("la vuelta atrás se niega mientras haya videos de grabaciones independientes", se_nego)
q(su, "delete from public.video_reproduccion where id_video in (select id from public.video where id_grabacion is not null)")
q(su, "delete from public.video where id_grabacion is not null")
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
ok("después deja todas las funciones idénticas a como estaban", q(su, HUELLA_FUNCIONES, uno=True)["s"] == FUNCIONES_ANTES)
ok("quita la tabla y la columna", q(su, "select to_regclass('public.grabacion_independiente') t, (select count(*) from information_schema.columns "
                                       "where table_name = 'video' and column_name = 'id_grabacion') c", uno=True) == {"t": None, "c": 0})
ok("id_clase vuelve a ser obligatoria",
   q(su, "select is_nullable from information_schema.columns where table_name = 'video' and column_name = 'id_clase'", uno=True)["is_nullable"] == "NO")
ok("el video de la clase programada sigue", q(su, "select count(*) n from public.video where id = %s", (vb,), uno=True)["n"] == 1)
with su.cursor() as cur:
    cur.execute(open(todas[-1]).read())
ok("y se puede volver a aplicar", q(su, "select to_regclass('public.grabacion_independiente') t", uno=True)["t"] is not None)

print(f"\n{total_casos - fallas}/{total_casos} casos correctos")
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
