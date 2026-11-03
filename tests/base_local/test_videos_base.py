"""Videos On Demand (migración 20261106000000_videos_on_demand) en un Postgres LOCAL descartable con todas las
migraciones y datos sintéticos: aislamiento entre empresas, permisos por rol, columnas ocultas, vencimiento,
identificación de la clase, reintentos sin duplicar, topes de reproducción, limpieza y vuelta atrás. No toca Supabase.

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
admin1 = cuenta("admin1@prueba.invalid", e1, "admin")
alu1, alu2 = cuenta("alu1@prueba.invalid", e1, "alumna"), cuenta("alu2@prueba.invalid", e2, "alumna")
baja = cuenta("baja@prueba.invalid", e1, "alumna")


def clase(id_empresa, profesora, cuando, hora="08:00"):
    t = q(su, "insert into public.tipo_clase (id_empresa, nombre, duracion, precio_presencial, cupo_max) "
              "values (%s, %s, 40, 800, 7) returning id", (id_empresa, f"PRUEBA-{uuid.uuid4().hex[:8]}"), uno=True)["id"]
    h = q(su, "insert into public.horario (id_empresa, id_profesora, id_tipo_clase, dia_semana, hora_inicio, duracion, "
              "cupo_max, precio, activo) values (%s, %s, %s, 'lunes', %s, 40, 7, 800, true) returning id",
          (id_empresa, profesora, t, hora), uno=True)["id"]
    return q(su, "insert into public.clase (id_empresa, id_profesora, id_horario, id_tipo_clase, fecha, hora_inicio, "
                 "duracion, precio, cupo_max) values (%s, %s, %s, %s, %s, %s, 40, 800, 7) returning id",
             (id_empresa, profesora, h, t, cuando, hora), uno=True)["id"]


FECHA = "2026-11-16"
c1, c2 = clase(e1, prof1, FECHA), clase(e2, prof2, FECHA)
srvc = ClienteFalso(uri, None, rol="service_role")
alumna1, alumna2 = ClienteFalso(uri, alu1), ClienteFalso(uri, alu2)
profesora1, administradora1 = ClienteFalso(uri, prof1), ClienteFalso(uri, admin1)
anon = ClienteFalso(uri, None, rol="anon")
PARTES = [{"nombre": "parte-00.ts", "bytes": 45000000}, {"nombre": "parte-01.ts", "bytes": 5000000}]
LISTA = "#EXTM3U\nparte-00.ts\nparte-01.ts\n"


def registrar(profesora, inicio="2026-11-16T08:03:00-03:00", huella=None, partes=None, tamano=50000000):
    return rpc(srvc, "video_registrar", p_id_profesora=profesora, p_inicio=inicio, p_huella=huella or uuid.uuid4().hex * 2,
               p_duracion_seg=2340, p_tamano_bytes=tamano, p_partes=json.dumps(partes or PARTES), p_lista_hls=LISTA)


# ---------- identificación de la clase ----------
ok("sin clase cerca de esa hora", registrar(prof1, "2026-11-16T10:00:00-03:00")["resultado"] == "sin_clase")
ok("otra profesora no encuentra la clase", registrar(admin1)["resultado"] == "sin_clase")
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
c3 = clase(e1, prof1, FECHA, "08:20")
ok("dos clases posibles no se adivinan", registrar(prof1, "2026-11-16T08:10:00-03:00")["resultado"] == "clase_ambigua")
q(su, "update public.clase set estado = 'cancelada' where id = %s", (c3,))

# ---------- antes de publicar ----------
ok("sin publicar no se ve", rpc(alumna1, "videos_disponibles") == [])
ok("sin publicar no se reproduce", rpc(alumna1, "video_reproducir", p_id_video=v1)["resultado"] == "no_disponible")

# ---------- publicar ----------
ok("otra profesora no publica", rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof2)["resultado"] == "no_encontrado")
p1 = rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof1)
ok("publica", p1["resultado"] == "ok")
fila = q(su, "select publicado_en, vence_en, vence_en - publicado_en = interval '24 hours' as exacto from public.video where id = %s", (v1,), uno=True)
ok("vence exactamente 24 h después", fila["exacto"])
ok("publicar de nuevo no corre el vencimiento", rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof1)["vence_en"] == p1["vence_en"])
ok("registrar un publicado no lo pisa", registrar(prof1, huella=huella1)["resultado"] == "ya_publicado")
rpc(srvc, "video_publicar", p_id_video=v2, p_id_profesora=prof2)

# ---------- quién lo ve ----------
lista1 = rpc(alumna1, "videos_disponibles")
ok("la alumna ve el de su empresa", [v["id"] for v in lista1] == [v1], lista1)
ok("trae los datos de la pantalla", lista1[0]["profesora"] == "Prof1 Prueba" and lista1[0]["duracion_seg"] == 2340, lista1)
ok("la lista no trae rutas", "partes" not in lista1[0] and "lista_hls" not in lista1[0])
ok("la alumna de otra empresa no lo ve", [v["id"] for v in rpc(alumna2, "videos_disponibles")] == [v2])
ok("ni pidiéndolo por id", rpc(alumna2, "videos_disponibles", p_id_video=v1) == [])
ok("ni reproduciéndolo", rpc(alumna2, "video_reproducir", p_id_video=v1)["resultado"] == "no_disponible")
ok("la profesora lo ve", [v["id"] for v in rpc(profesora1, "videos_disponibles")] == [v1])
ok("la administradora lo ve", [v["id"] for v in rpc(administradora1, "videos_disponibles")] == [v1])
ok("anon no ejecuta", isinstance(rpc(anon, "videos_disponibles"), dict) and rpc(anon, "video_reproducir", p_id_video=v1).get("error") == "42501")
q(su, "update public.membresia set activa = false where id_usuario = %s", (baja,))
ok("una cuenta dada de baja no lo ve", rpc(ClienteFalso(uri, baja), "videos_disponibles") == [])

# ---------- RLS directa ----------
ok("RLS: la alumna lee solo lo suyo", como(alumna1, "select id from public.video") == [(v1,)])
ok("RLS: otra empresa", como(alumna2, "select id from public.video") == [(v2,)])
ok("columnas: no lee la lista", como(alumna1, "select lista_hls from public.video") == {"error": "42501"})
ok("columnas: no lee las partes", como(alumna1, "select partes from public.video") == {"error": "42501"})
ok("anon no lee la tabla", como(anon, "select id from public.video") == {"error": "42501"})
for nombre, cli in (("alumna", alumna1), ("profesora", profesora1), ("administradora", administradora1)):
    ok(f"{nombre} no modifica", como(cli, "update public.video set vence_en = now() + interval '9 days'") == {"error": "42501"})
    ok(f"{nombre} no borra", como(cli, "delete from public.video") == {"error": "42501"})
    ok(f"{nombre} no publica", rpc(cli, "video_publicar", p_id_video=v1, p_id_profesora=prof1).get("error") == "42501")
    ok(f"{nombre} no lee tokens", como(cli, "select * from public.video_agente") == {"error": "42501"})
    ok(f"{nombre} no limpia", rpc(cli, "videos_a_limpiar").get("error") == "42501")
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

# ---------- reproducción y topes ----------
r = rpc(alumna1, "video_reproducir", p_id_video=v1)
ok("autoriza y entrega las partes", r["resultado"] == "ok" and len(r["partes"]) == 2 and 0 < r["segundos_restantes"] <= 86400, r)
rpc(alumna1, "video_reproducir", p_id_video=v1)
cuantos = "select count(*) n from public.video_reproduccion where id_video = %s and id_usuario = %s"
ok("reabrir enseguida no cuenta de nuevo", q(su, cuantos, (v1, alu1), uno=True)["n"] == 1)
q(su, "update public.video_reproduccion set creado_en = now() - interval '2 hours'")
rpc(alumna1, "video_reproducir", p_id_video=v1)
q(su, "update public.video_reproduccion set creado_en = now() - interval '2 hours'")
rpc(alumna1, "video_reproducir", p_id_video=v1)
q(su, "update public.video_reproduccion set creado_en = now() - interval '2 hours'")
ok("tres inicios por usuaria", q(su, cuantos, (v1, alu1), uno=True)["n"] == 3
   and rpc(alumna1, "video_reproducir", p_id_video=v1)["resultado"] == "limite_usuaria")
ok("la usuaria no escribe su contador", como(alumna1, "delete from public.video_reproduccion") == {"error": "42501"})
q(su, "insert into public.video_reproduccion (id_empresa, id_video, id_usuario, bytes_estimados) values (%s, %s, %s, 3990000000)", (e2, v2, alu2))
ok("el tope mensual es global", rpc(profesora1, "video_reproducir", p_id_video=v1)["resultado"] == "sin_presupuesto")
q(su, "update public.video_reproduccion set creado_en = now() - interval '40 days' where bytes_estimados = 3990000000")
ok("el mes siguiente se libera", rpc(profesora1, "video_reproducir", p_id_video=v1)["resultado"] == "ok")

# ---------- vencimiento y limpieza ----------
ok("vigente no se limpia", rpc(srvc, "videos_a_limpiar") == [])
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
ok("marcar eliminado no toca un vigente", q(su, "select estado from public.video where id = %s", (v1,), uno=True)["estado"] == "publicado")
q(su, "update public.video set vence_en = now() - interval '1 second' where id = %s", (v1,))
ok("vencido no se lista", rpc(alumna1, "videos_disponibles") == [])
ok("vencido no se reproduce", rpc(profesora1, "video_reproducir", p_id_video=v1)["resultado"] == "no_disponible")
ok("RLS: vencido no se lee", como(alumna1, "select id from public.video") == [])
ok("la otra empresa sigue intacta", [v["id"] for v in rpc(alumna2, "videos_disponibles")] == [v2])
pendientes = rpc(srvc, "videos_a_limpiar")
ok("vencido pasa a limpieza", [p["id"] for p in pendientes] == [v1] and pendientes[0]["id_empresa"] == e1)
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
fila = q(su, "select estado, archivo_eliminado_en, publicado_en from public.video where id = %s", (v1,), uno=True)
ok("conserva el registro", fila["estado"] == "eliminado" and fila["archivo_eliminado_en"] and fila["publicado_en"])
ok("no vuelve a limpieza", rpc(srvc, "videos_a_limpiar") == [])
ok("no se vuelve a publicar", rpc(srvc, "video_publicar", p_id_video=v1, p_id_profesora=prof1)["resultado"] == "ya_eliminado")
ok("un archivo eliminado no se resube", registrar(prof1, huella=huella1)["resultado"] == "ya_eliminado")

# ---------- errores y subidas abandonadas ----------
c4 = clase(e1, prof1, "2026-11-17")
v4 = registrar(prof1, "2026-11-17T08:00:00-03:00", huella="b" * 64)["id_video"]
rpc(srvc, "video_error", p_id_video=v4, p_id_profesora=prof1, p_mensaje="x" * 900)
fila = q(su, "select estado, length(ultimo_error) n from public.video where id = %s", (v4,), uno=True)
ok("registra el error recortado", fila["estado"] == "error" and fila["n"] == 500)
ok("después de un error se reintenta", registrar(prof1, "2026-11-17T08:00:00-03:00", huella="b" * 64)["id_video"] == v4
   and q(su, "select estado from public.video where id = %s", (v4,), uno=True)["estado"] == "subiendo")
q(su, "update public.video set creado_en = now() - interval '3 days' where id = %s", (v4,))
ok("subida abandonada pasa a limpieza", [p["id"] for p in rpc(srvc, "videos_a_limpiar")] == [v4])

# ---------- agente ----------
token = "c" * 64
q(su, "insert into public.video_agente (id_profesora, nombre, token_hash) values (%s, 'Notebook', %s)", (prof1, token))
ok("identifica el token", rpc(srvc, "video_agente_identificar", p_token_hash=token) == prof1)
ok("token desconocido", rpc(srvc, "video_agente_identificar", p_token_hash="d" * 64) is None)
ok("guarda el último contacto", q(su, "select ultimo_contacto from public.video_agente", uno=True)["ultimo_contacto"] is not None)
q(su, "update public.membresia set activa = false where id_usuario = %s", (prof1,))
ok("una profesora dada de baja deja de subir", rpc(srvc, "video_agente_identificar", p_token_hash=token) is None)
q(su, "update public.membresia set activa = true where id_usuario = %s", (prof1,))
q(su, "update public.video_agente set activo = false")
ok("un token desactivado no sirve", rpc(srvc, "video_agente_identificar", p_token_hash=token) is None)

# ---------- vuelta atrás ----------
VUELTA = open(os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_videos_on_demand.sql")).read()
try:
    q(su, VUELTA)
    ok("la vuelta atrás se niega con archivos guardados", False)
except psycopg2.Error:
    ok("la vuelta atrás se niega con archivos guardados", q(su, "select to_regclass('public.video') t", uno=True)["t"] is not None)
q(su, "update public.video set estado = 'eliminado'")
q(su, VUELTA)
ok("la vuelta atrás quita las tablas", q(su, "select to_regclass('public.video') a, to_regclass('public.video_agente') b, to_regclass('public.video_reproduccion') c", uno=True) == {"a": None, "b": None, "c": None})
ok("y las funciones", q(su, "select count(*) n from pg_proc where proname like '%video%' and pronamespace = 'public'::regnamespace", uno=True)["n"] == 0)

su.close()
localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos correctos")
sys.exit(1 if fallas else 0)
