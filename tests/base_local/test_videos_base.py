"""Videos On Demand (migración 20261106000000_videos_on_demand) en un Postgres LOCAL descartable con todas las
migraciones y datos sintéticos: selección de horarios, duración, identificación de la clase, aislamiento entre empresas,
permisos por rol, columnas ocultas, vencimiento, reintentos sin duplicar, consumo estimado, limpieza con errores y vuelta
atrás. Storage está simulado: esto no prueba el Storage real. No toca Supabase.

Uso:  python tests/base_local/test_videos_base.py
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


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        cur.execute(open(a).read())


def cuenta(mail, id_empresa, rol):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].title(), "apellido": "Prueba",
                                      "id_empresa": id_empresa, "rol_empresa": rol})))
    q(su, "update public.profiles set empresa_activa = %s where id = %s", (id_empresa, i))
    return i


e1 = q(su, "select id from public.empresa where codigo = 'JSU'", uno=True)["id"]
e2 = q(su, "insert into public.empresa (nombre, codigo) values ('Pilates Centro', 'PIL') returning id", uno=True)["id"]
prof1, prof2 = cuenta("prof1@prueba.invalid", e1, "profesora"), cuenta("prof2@prueba.invalid", e2, "profesora")
admin1, admin2 = cuenta("admin1@prueba.invalid", e1, "admin"), cuenta("admin2@prueba.invalid", e2, "admin")
alu1, alu2 = cuenta("alu1@prueba.invalid", e1, "alumna"), cuenta("alu2@prueba.invalid", e2, "alumna")
baja = cuenta("baja@prueba.invalid", e1, "alumna")


def clase(id_empresa, profesora, cuando, hora="08:00", duracion=40):
    t = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
              "values (%s, %s, %s, 800, 7) returning id", (id_empresa, f"PRUEBA-{uuid.uuid4().hex[:8]}", duracion), uno=True)["id"]
    h = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, "
              "cupo_max, precio, activo) values (%s, %s, %s, 'lunes', %s, %s, 7, 800, true) returning id",
          (id_empresa, profesora, t, hora, duracion), uno=True)["id"]
    c = q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, "
              "duracion, precio, cupo_max) values (%s, %s, %s, %s, %s, %s, %s, 800, 7) returning id",
          (id_empresa, profesora, h, t, cuando, hora, duracion), uno=True)["id"]
    return c, h


def programar(cli, horario):
    r = rpc(cli, "video_programar", p_id_horario=horario, p_activa=True)
    q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (horario,))
    return r


FECHA = "2026-11-16"
(c1, h1), (c2, h2) = clase(e1, prof1, FECHA), clase(e2, prof2, FECHA)
srvc = ClienteFalso(uri, None, rol="service_role")
alumna1, alumna2 = ClienteFalso(uri, alu1), ClienteFalso(uri, alu2)
profesora1, profesora2 = ClienteFalso(uri, prof1), ClienteFalso(uri, prof2)
administradora1, administradora2 = ClienteFalso(uri, admin1), ClienteFalso(uri, admin2)
anon = ClienteFalso(uri, None, rol="anon")
PARTES = [{"nombre": "parte-00.ts", "bytes": 45000000}, {"nombre": "parte-01.ts", "bytes": 5000000}]
LISTA = "#EXTM3U\nparte-00.ts\nparte-01.ts\n"
INICIO = "2026-11-16T08:03:00-03:00"


def registrar(profesora, inicio=INICIO, huella=None, partes=None, tamano=50000000, duracion=2340):
    return rpc(srvc, "video_registrar", p_id_profesora=profesora, p_inicio=inicio, p_huella=huella or uuid.uuid4().hex * 2,
               p_duracion_seg=duracion, p_tamano_bytes=tamano, p_partes=json.dumps(partes or PARTES), p_lista_hls=LISTA)


def consultar(profesora, inicio=INICIO, duracion=2340):
    return rpc(srvc, "video_consultar", p_id_profesora=profesora, p_inicio=inicio, p_duracion_seg=duracion).get("resultado")


def publicar(video, profesora):
    return rpc(srvc, "video_publicar", p_id_video=video, p_id_profesora=profesora)


def estado(video):
    return q(su, "select estado from public.video where id = %s", (video,), uno=True)["estado"]


# ---------- selección de horarios ----------
ok("sin activar no se autoriza", consultar(prof1) == "no_autorizada")
ok("sin activar no se registra", registrar(prof1)["resultado"] == "no_autorizada"
   and q(su, "select count(*) n from public.video", uno=True)["n"] == 0)
ok("la alumna no activa", rpc(alumna1, "video_programar", p_id_horario=h1, p_activa=True)["resultado"] == "no_autorizada")
ok("la profesora no activa", rpc(profesora1, "video_programar", p_id_horario=h1, p_activa=True)["resultado"] == "no_autorizada")
ok("anon no activa", rpc(anon, "video_programar", p_id_horario=h1, p_activa=True).get("error") == "42501")
ok("otra empresa no activa", rpc(administradora2, "video_programar", p_id_horario=h1, p_activa=True)["resultado"] == "no_encontrado")
_, h60 = clase(e1, prof1, "2026-12-07", "11:00", 60)
ok("un horario de 60 min no se activa", rpc(administradora1, "video_programar", p_id_horario=h60, p_activa=True)["resultado"] == "duracion_no_valida")
ok("la administradora activa", programar(administradora1, h1)["resultado"] == "ok")
programar(administradora2, h2)
ok("autorizada después de activar", consultar(prof1) == "autorizada")
conf = rpc(administradora1, "videos_configuracion")
ok("la configuración lista sus horarios", {h["id"] for h in conf} >= {h1, h60} and h2 not in {h["id"] for h in conf})
ok("marca activa y programable", next(h for h in conf if h["id"] == h1)["activa"]
   and not next(h for h in conf if h["id"] == h60)["programable"])
ok("la configuración no es para profesoras", rpc(profesora1, "videos_configuracion") == [])
rpc(administradora1, "video_programar", p_id_horario=h1, p_activa=False)
ok("desactivar corta la autorización", consultar(prof1) == "no_autorizada")
rpc(administradora1, "video_programar", p_id_horario=h1, p_activa=True)
q(su, "update public.video_programacion set activa_desde = '2026-12-01' where id_horario = %s", (h1,))
ok("una clase anterior a la activación no se autoriza", consultar(prof1) == "no_autorizada")
q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (h1,))

# ---------- duración ----------
ok("34:00 entra", consultar(prof1, duracion=2040) == "autorizada")
ok("33:59 no entra", consultar(prof1, duracion=2039) == "duracion_fuera_de_rango")
ok("41:00 entra", consultar(prof1, duracion=2460) == "autorizada")
ok("41:01 no entra", consultar(prof1, duracion=2461) == "duracion_fuera_de_rango")
ok("60 segundos no se registran", registrar(prof1, duracion=60)["resultado"] == "duracion_fuera_de_rango")
q(su, "update public.video_parametros set margen_menos_seg = 60")
ok("el margen es configurable", consultar(prof1, duracion=2040) == "duracion_fuera_de_rango")
q(su, "update public.video_parametros set margen_menos_seg = 360")
ok("una sesión no lee los parámetros", como(alumna1, "select * from public.video_parametros") == {"error": "42501"})

# ---------- identificación de la clase ----------
ok("sin clase cerca de esa hora", consultar(prof1, "2026-11-16T10:00:00-03:00") == "sin_clase")
ok("otra profesora no encuentra la clase", consultar(admin1) == "sin_clase")
ok("partes que no suman el total", registrar(prof1, tamano=1)["resultado"] == "datos_invalidos")
ok("parte de más de 50 MB", registrar(prof1, partes=[{"nombre": "parte-00.ts", "bytes": 60000000}], tamano=60000000)["resultado"] == "datos_invalidos")
ok("nombre de parte con ruta", registrar(prof1, partes=[{"nombre": "../x.ts", "bytes": 50000000}])["resultado"] == "datos_invalidos")
huella1 = "a" * 64
r1 = registrar(prof1, huella=huella1)
ok("identifica la clase y su empresa", r1["resultado"] == "ok" and r1["id_empresa"] == e1, r1)
v1 = r1["id_video"]
ok("repetir no duplica", registrar(prof1, huella=huella1)["id_video"] == v1
   and q(su, "select count(*) n from public.video where id_clase = %s", (c1,), uno=True)["n"] == 1)
ok("el reintento cuenta el intento", q(su, "select intentos from public.video where id = %s", (v1,), uno=True)["intentos"] == 2)
ok("el mismo archivo en otra clase se rechaza", registrar(prof2, huella=huella1)["resultado"] == "huella_repetida")
v2 = registrar(prof2)["id_video"]
ok("video de la otra empresa", q(su, "select id_empresa from public.video where id = %s", (v2,), uno=True)["id_empresa"] == e2)
D = "2026-11-23"
(c8, h8), (c9, h9) = clase(e1, prof1, D, "08:00"), clase(e1, prof1, D, "09:00")
programar(administradora1, h8)
programar(administradora1, h9)
ok("regresión A2: 08:31 no cae en la clase de las 09:00", consultar(prof1, f"{D}T08:31:00-03:00") == "sin_clase")
ok("08:14 es de la clase de las 08:00", registrar(prof1, f"{D}T08:14:00-03:00")["resultado"] == "ok"
   and q(su, "select count(*) n from public.video where id_clase = %s", (c8,), uno=True)["n"] == 1)
c3, h3 = clase(e1, prof1, FECHA, "08:20")
programar(administradora1, h3)
ok("dos clases posibles no se adivinan", consultar(prof1, "2026-11-16T08:10:00-03:00") == "clase_ambigua")
q(su, "update public.clase set estado = 'cancelada' where id = %s", (c3,))
ok("una clase cancelada no cuenta", consultar(prof1, "2026-11-16T08:10:00-03:00") == "autorizada")
q(su, "delete from public.video where id_clase = %s", (c8,))

# ---------- espacio ----------
q(su, "update public.video_parametros set almacenamiento_max_bytes = 120000000")
c5, h5 = clase(e1, prof1, "2026-11-30")
programar(administradora1, h5)
ok("sin espacio no se autoriza", consultar(prof1, "2026-11-30T08:00:00-03:00") == "sin_espacio")
ok("sin espacio no se registra", registrar(prof1, "2026-11-30T08:00:00-03:00", tamano=50000000)["resultado"] == "sin_espacio")
q(su, "update public.video_parametros set almacenamiento_max_bytes = 800000000")

# ---------- antes de publicar ----------
ok("sin publicar no se ve", rpc(alumna1, "videos_disponibles") == [])
ok("sin publicar no se inicia", rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "no_disponible")

# ---------- publicar ----------
ok("otra profesora no publica", publicar(v1, prof2)["resultado"] == "no_encontrado")
rpc(administradora1, "video_programar", p_id_horario=h1, p_activa=False)
ok("desactivado a último momento no se publica", publicar(v1, prof1)["resultado"] == "no_autorizada" and estado(v1) == "subiendo")
rpc(administradora1, "video_programar", p_id_horario=h1, p_activa=True)
q(su, "update public.video_programacion set activa_desde = '2026-01-01' where id_horario = %s", (h1,))
rpc(srvc, "video_error", p_id_video=v1, p_id_profesora=prof1, p_mensaje="falló la subida")
ok("regresión A1: en error no se publica", publicar(v1, prof1)["resultado"] == "no_subiendo" and estado(v1) == "error")
registrar(prof1, huella=huella1)
p1 = publicar(v1, prof1)
ok("publica", p1["resultado"] == "ok")
fila = q(su, "select vence_en - publicado_en = interval '24 hours' as exacto from public.video where id = %s", (v1,), uno=True)
ok("vence exactamente 24 h después", fila["exacto"])
ok("publicar de nuevo no corre el vencimiento", publicar(v1, prof1)["vence_en"] == p1["vence_en"])
ok("registrar un publicado no lo pisa", registrar(prof1, huella=huella1)["resultado"] == "ya_publicado")
ok("consultar un publicado evita comprimir", consultar(prof1) == "ya_publicado")
publicar(v2, prof2)

# ---------- quién lo ve ----------
lista1 = rpc(alumna1, "videos_disponibles")
ok("la alumna ve el de su empresa", [v["id"] for v in lista1] == [v1], lista1)
ok("trae la duración real", lista1[0]["profesora"] == "Prof1 Prueba" and lista1[0]["duracion_seg"] == 2340, lista1)
ok("la lista no trae rutas", "partes" not in lista1[0] and "lista_hls" not in lista1[0])
ok("la alumna de otra empresa no lo ve", [v["id"] for v in rpc(alumna2, "videos_disponibles")] == [v2])
ok("ni pidiéndolo por id", rpc(alumna2, "videos_disponibles", p_id_video=v1) == [])
ok("ni iniciándolo", rpc(alumna2, "video_iniciar", p_id_video=v1)["resultado"] == "no_disponible")
ok("ni firmándolo", rpc(alumna2, "video_para_firmar", p_id_video=v1)["resultado"] == "no_disponible")
ok("ni informando progreso", rpc(alumna2, "video_progreso", p_id_video=v1, p_segundos=60)["resultado"] == "no_disponible")
ok("la profesora de otra empresa tampoco", rpc(profesora2, "video_iniciar", p_id_video=v1)["resultado"] == "no_disponible")
ok("la profesora lo ve", [v["id"] for v in rpc(profesora1, "videos_disponibles")] == [v1])
ok("la administradora lo ve", [v["id"] for v in rpc(administradora1, "videos_disponibles")] == [v1])
sup = cuenta("super@prueba.invalid", e1, "alumna")
q(su, "update public.membresia set activa = false where id_usuario = %s", (sup,))
q(su, "update public.profiles set es_superadmin = true where id = %s", (sup,))
ok("la superadministradora no lo ve", rpc(ClienteFalso(uri, sup), "videos_disponibles") == [])
ok("anon no ejecuta", rpc(anon, "videos_disponibles").get("error") == "42501"
   and rpc(anon, "video_iniciar", p_id_video=v1).get("error") == "42501")
q(su, "update public.membresia set activa = false where id_usuario = %s", (baja,))
ok("una cuenta dada de baja no lo ve", rpc(ClienteFalso(uri, baja), "videos_disponibles") == [])

# ---------- RLS directa ----------
ok("RLS: la alumna lee solo lo suyo", como(alumna1, "select id from public.video") == [(v1,)])
ok("RLS: otra empresa", como(alumna2, "select id from public.video") == [(v2,)])
ok("columnas: no lee la lista", como(alumna1, "select lista_hls from public.video") == {"error": "42501"})
ok("columnas: no lee las partes", como(alumna1, "select partes from public.video") == {"error": "42501"})
ok("anon no lee la tabla", como(anon, "select id from public.video") == {"error": "42501"})
ok("programación: la alumna no la lee", como(alumna1, "select * from public.video_programacion") == {"error": "42501"} or como(alumna1, "select * from public.video_programacion") == [])
ok("programación: solo su empresa", {r[0] for r in como(profesora2, "select id_empresa from public.video_programacion")} == {e2})
for nombre, cli in (("alumna", alumna1), ("profesora", profesora1), ("administradora", administradora1)):
    ok(f"{nombre} no modifica", como(cli, "update public.video set vence_en = now() + interval '9 days'") == {"error": "42501"})
    ok(f"{nombre} no borra", como(cli, "delete from public.video") == {"error": "42501"})
    ok(f"{nombre} no publica", rpc(cli, "video_publicar", p_id_video=v1, p_id_profesora=prof1).get("error") == "42501")
    ok(f"{nombre} no lee tokens", como(cli, "select * from public.video_agente") == {"error": "42501"})
    ok(f"{nombre} no limpia", rpc(cli, "videos_a_limpiar").get("error") == "42501")
    ok(f"{nombre} no escribe la programación", como(cli, "update public.video_programacion set activa = false") == {"error": "42501"})
ok("Storage: el bucket es privado", q(su, "select public from storage.buckets where id = 'videos-clases'", uno=True)["public"] is False)
ok("Storage: límite de 50 MB", q(su, "select file_size_limit l from storage.buckets where id = 'videos-clases'", uno=True)["l"] == 52428800)
ok("Storage: ninguna política para el bucket de videos",
   q(su, "select count(*) n from pg_policies where schemaname = 'storage' and (qual like '%videos-clases%' or with_check like '%videos-clases%')", uno=True)["n"] == 0)
ok("Storage: una sesión no lee los objetos",
   q(su, "insert into storage.objects (bucket_id, name) values ('videos-clases', '1/1/parte-00.ts') returning id", uno=True) is not None
   and como(alumna1, "select id from storage.objects where bucket_id = 'videos-clases'") == [])
q(su, "delete from storage.objects where bucket_id = 'videos-clases'")

# ---------- integridad ----------
try:
    q(su, "update public.video set id_empresa = %s where id = %s", (e2, v1))
    ok("no se puede cambiar de empresa", False)
except psycopg2.Error as e:
    ok("no se puede cambiar de empresa", e.pgcode == "23503")

# ---------- reproducción y consumo estimado ----------
filas = "select * from public.video_reproduccion where id_video = %s and id_usuario = %s"
ok("abrir la lista no alcanza: firmar exige Play", rpc(alumna1, "video_para_firmar", p_id_video=v1)["resultado"] == "sin_iniciar")
ok("abrir la pantalla no cuenta", rpc(alumna1, "videos_disponibles", p_id_video=v1) != []
   and q(su, filas, (v1, alu1)) == [])
ok("Play autoriza", rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "ok")
r = rpc(alumna1, "video_para_firmar", p_id_video=v1)
ok("después de Play entrega las partes", r["resultado"] == "ok" and len(r["partes"]) == 2 and 0 < r["segundos_restantes"] <= 86400, r)
ok("reserva inicial de un cuarto", q(su, filas, (v1, alu1), uno=True)["bytes_estimados"] == 12500000)
for _ in range(6):
    rpc(alumna1, "video_iniciar", p_id_video=v1)
ok("regresión: ya no hay tope de 3 inicios (recargar no suma)", len(q(su, filas, (v1, alu1))) == 1
   and q(su, filas, (v1, alu1), uno=True)["bytes_estimados"] == 12500000
   and rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "ok")
q(su, "update public.video_reproduccion set ultimo_progreso_en = now() - interval '10 minutes'")
ok("progreso", rpc(alumna1, "video_progreso", p_id_video=v1, p_segundos=600)["resultado"] == "ok")
fila = q(su, filas, (v1, alu1), uno=True)
ok("estima por tiempo reproducido", fila["segundos_vistos"] == 600 and fila["bytes_estimados"] == round(600 * 50000000 / 2340), fila)
rpc(alumna1, "video_progreso", p_id_video=v1, p_segundos=100000)
ok("no se puede informar más que el tiempo transcurrido", q(su, filas, (v1, alu1), uno=True)["segundos_vistos"] <= 610)
ok("progreso negativo no resta", rpc(alumna1, "video_progreso", p_id_video=v1, p_segundos=-500)["resultado"] == "ok"
   and q(su, filas, (v1, alu1), uno=True)["segundos_vistos"] >= 600)
q(su, "update public.video_parametros set tope_usuaria_bytes = 10000000")
ok("tope estimado por persona", rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "limite_usuaria"
   and rpc(alumna1, "video_para_firmar", p_id_video=v1)["resultado"] == "limite_usuaria")
q(su, "update public.video_parametros set tope_usuaria_bytes = 300000000")
ok("la usuaria no escribe su contador", como(alumna1, "delete from public.video_reproduccion") == {"error": "42501"})
ok("no puede fijar su tope", como(alumna1, "update public.video_parametros set tope_ciclo_bytes = 1") == {"error": "42501"})
q(su, "insert into public.video_reproduccion (id_empresa, id_video, id_usuario, bytes_estimados) values (%s, %s, %s, 2400000000)", (e2, v2, alu2))
ok("el tope del ciclo incluye el margen", rpc(profesora1, "video_iniciar", p_id_video=v1)["resultado"] == "sin_presupuesto")
ok("quien ya empezó puede seguir", rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "ok")
q(su, "update public.video_reproduccion set creado_en = now() - interval '40 days' where bytes_estimados = 2400000000")
ok("el ciclo siguiente se libera", rpc(profesora1, "video_iniciar", p_id_video=v1)["resultado"] == "ok")
ok("el ciclo empieza en el día configurado", q(su, "select extract(day from public._video_inicio_ciclo(1) at time zone 'America/Montevideo') d", uno=True)["d"] == 1)

# ---------- vencimiento y limpieza ----------
ok("vigente no se limpia", [p["id"] for p in rpc(srvc, "videos_a_limpiar")] == [])
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
ok("marcar eliminado no toca un vigente", estado(v1) == "publicado")
q(su, "update public.video set vence_en = now() - interval '1 second' where id = %s", (v1,))
ok("vencido no se lista", rpc(alumna1, "videos_disponibles") == [])
ok("vencido no se inicia", rpc(alumna1, "video_iniciar", p_id_video=v1)["resultado"] == "no_disponible")
ok("vencido no se firma, aunque ya hubiera empezado", rpc(alumna1, "video_para_firmar", p_id_video=v1)["resultado"] == "no_disponible")
ok("RLS: vencido no se lee", como(alumna1, "select id from public.video") == [])
ok("el bloqueo no espera a la limpieza", estado(v1) == "publicado")
ok("la otra empresa sigue intacta", [v["id"] for v in rpc(alumna2, "videos_disponibles")] == [v2])
pendientes = rpc(srvc, "videos_a_limpiar")
ok("vencido pasa a limpieza", [p["id"] for p in pendientes] == [v1] and pendientes[0]["id_empresa"] == e1)
rpc(srvc, "video_limpieza_error", p_id_video=v1, p_mensaje="x" * 900)
fila = q(su, "select estado, limpieza_intentos, length(limpieza_error) n, limpieza_ultimo_intento from public.video where id = %s", (v1,), uno=True)
ok("regresión A7: el error de limpieza queda en la base", fila["estado"] == "publicado" and fila["limpieza_intentos"] == 1
   and fila["n"] == 500 and fila["limpieza_ultimo_intento"], fila)
ok("y se reintenta", [p["id"] for p in rpc(srvc, "videos_a_limpiar")] == [v1])
q(su, "update public.video set vence_en = now() - interval '1 second' where id = %s", (v2,))
ok("un video que falla no frena a los demás", [p["id"] for p in rpc(srvc, "videos_a_limpiar", p_limite=1)] == [v2])
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
fila = q(su, "select estado, archivo_eliminado_en, publicado_en, limpieza_error from public.video where id = %s", (v1,), uno=True)
ok("conserva el registro", fila["estado"] == "eliminado" and fila["archivo_eliminado_en"] and fila["publicado_en"] and fila["limpieza_error"] is None, fila)
ok("no vuelve a limpieza", v1 not in [p["id"] for p in rpc(srvc, "videos_a_limpiar")])
ok("no se vuelve a publicar", publicar(v1, prof1)["resultado"] == "ya_eliminado")
ok("un video ya publicado y borrado no se resube", consultar(prof1) == "ya_eliminado")

# ---------- subidas abandonadas ----------
c4, h4 = clase(e1, prof1, "2026-11-17")
programar(administradora1, h4)
I4 = "2026-11-17T08:00:00-03:00"
v4 = registrar(prof1, I4, huella="b" * 64)["id_video"]
rpc(srvc, "video_error", p_id_video=v4, p_id_profesora=prof1, p_mensaje="x" * 900)
fila = q(su, "select estado, length(ultimo_error) n from public.video where id = %s", (v4,), uno=True)
ok("registra el error recortado", fila["estado"] == "error" and fila["n"] == 500)
ok("después de un error se reintenta", registrar(prof1, I4, huella="b" * 64)["id_video"] == v4 and estado(v4) == "subiendo")
q(su, "update public.video set creado_en = now() - interval '3 days' where id = %s", (v4,))
ok("regresión A6: con actividad reciente no se limpia, aunque se haya creado hace días", v4 not in [p["id"] for p in rpc(srvc, "videos_a_limpiar")])
q(su, "update public.video set actualizado_en = now() - interval '3 days' where id = %s", (v4,))
rpc(srvc, "video_de_agente", p_id_video=v4, p_id_profesora=prof1)
ok("cada contacto del programa cuenta como actividad", v4 not in [p["id"] for p in rpc(srvc, "videos_a_limpiar")])
q(su, "update public.video set actualizado_en = now() - interval '3 days' where id = %s", (v4,))
ok("sin actividad pasa a limpieza", v4 in [p["id"] for p in rpc(srvc, "videos_a_limpiar")])
rpc(srvc, "video_marcar_eliminado", p_id_video=v4)
ok("nunca publicado y limpiado: se puede volver a subir", consultar(prof1, I4) == "autorizada"
   and registrar(prof1, I4, huella="b" * 64)["id_video"] == v4 and estado(v4) == "subiendo")
fila = q(su, "select archivo_eliminado_en from public.video where id = %s", (v4,), uno=True)
ok("y deja de figurar eliminado", fila["archivo_eliminado_en"] is None)

# ---------- cambios en la clase ----------
q(su, "update public.clase set id_profesora = %s where id = %s", (admin1, c4))
q(su, "update public.video set actualizado_en = now() - interval '3 days' where id = %s", (v4,))
rpc(srvc, "video_marcar_eliminado", p_id_video=v4)
ok("cambiar la profesora de la clase no traba la limpieza", estado(v4) == "eliminado")

# ---------- agente ----------
token, ENT = "c" * 64, "pruebas-abcd1234"


def identificar(hash_token=token, ent=ENT):
    return rpc(srvc, "video_agente_identificar", p_token_hash=hash_token, p_entorno=ent)


q(su, "insert into public.video_agente (id_profesora, nombre, token_hash) values (%s, 'Notebook', %s)", (prof1, token))
ok("P2: sin entorno configurado nadie sube", identificar() is None)
q(su, "update public.video_parametros set entorno = %s", (ENT,))
ok("identifica token y entorno", identificar() == prof1)
ok("P2: otro entorno se rechaza", identificar(ent="pruebas-otroentorno") is None)
ok("P2: entorno vacío se rechaza", identificar(ent="") is None)
ok("token desconocido", identificar("d" * 64) is None)
try:
    q(su, "update public.video_parametros set entorno = 'cualquiera'")
    ok("P2: el entorno tiene formato", False)
except psycopg2.Error as e:
    ok("P2: el entorno tiene formato", e.pgcode == "23514")
ok("una sesión no ejecuta la identificación", rpc(administradora1, "video_agente_identificar", p_token_hash=token, p_entorno=ENT).get("error") == "42501")
ok("guarda el último contacto", q(su, "select ultimo_contacto from public.video_agente", uno=True)["ultimo_contacto"] is not None)
q(su, "update public.membresia set activa = false where id_usuario = %s", (prof1,))
ok("una profesora dada de baja deja de subir", identificar() is None)
q(su, "update public.membresia set activa = true where id_usuario = %s", (prof1,))
q(su, "update public.video_agente set activo = false")
ok("un token desactivado no sirve", identificar() is None)

# ---------- últimas grabaciones (P4) ----------
g1 = rpc(administradora1, "videos_ultimas_grabaciones")
ok("P4: la administradora ve las de su empresa", {g["id"] for g in g1} == {v1, v4}, g1)
ok("P4: no ve las de otra empresa", v2 not in {g["id"] for g in g1} and {g["id"] for g in rpc(administradora2, "videos_ultimas_grabaciones")} == {v2})
ok("P4: sin datos sensibles", not {"huella_sha256", "partes", "lista_hls", "tamano_bytes", "id_profesora"} & set(g1[0]))
ok("P4: la profesora no las ve", rpc(profesora1, "videos_ultimas_grabaciones") == [])
ok("P4: la alumna no las ve", rpc(alumna2, "videos_ultimas_grabaciones") == [])
ok("P4: anon no ejecuta", rpc(anon, "videos_ultimas_grabaciones").get("error") == "42501")
ok("P4: respeta el límite", len(rpc(administradora1, "videos_ultimas_grabaciones", p_limite=1)) == 1)

# ---------- cuentas de prueba ----------
total = "select coalesce(sum(bytes_estimados), 0) s from public.video_reproduccion"
antes = q(su, total, uno=True)["s"]
q(su, "delete from auth.users where id = %s", (alu1,))
ok("regresión A4: se puede borrar una alumna con reproducciones", q(su, "select 1 x from public.profiles where id = %s", (alu1,)) == [])
ok("y el consumo del ciclo se conserva", q(su, total, uno=True)["s"] == antes)
prof3 = cuenta("prof3@prueba.invalid", e1, "profesora")
q(su, "insert into public.video_agente (id_profesora, nombre, token_hash) values (%s, 'Otra', %s)", (prof3, "e" * 64))
q(su, "delete from auth.users where id = %s", (prof3,))
ok("se puede borrar una profesora con computadora autorizada", q(su, "select 1 x from public.video_agente where token_hash = %s", ("e" * 64,)) == [])

# ---------- vuelta atrás ----------
VUELTA = open(os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_videos_on_demand.sql")).read()
try:
    q(su, "update public.video set estado = 'subiendo' where id = %s", (v4,))
    q(su, VUELTA)
    ok("la vuelta atrás se niega con archivos guardados", False)
except psycopg2.Error:
    ok("la vuelta atrás se niega con archivos guardados", q(su, "select to_regclass('public.video') t", uno=True)["t"] is not None)
q(su, "update public.video set estado = 'eliminado'")
q(su, VUELTA)
ok("la vuelta atrás quita las tablas", q(su, "select to_regclass('public.video') a, to_regclass('public.video_agente') b, to_regclass('public.video_reproduccion') c, to_regclass('public.video_programacion') d, to_regclass('public.video_parametros') e", uno=True) == {"a": None, "b": None, "c": None, "d": None, "e": None})
ok("y las funciones", q(su, "select count(*) n from pg_proc where proname like '%video%' and pronamespace = 'public'::regnamespace", uno=True)["n"] == 0)

su.close()
localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos correctos")
sys.exit(1 if fallas else 0)
