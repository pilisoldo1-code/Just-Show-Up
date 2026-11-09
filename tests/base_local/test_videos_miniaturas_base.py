"""Miniaturas de Videos On Demand (migración 20261108000000_videos_miniaturas) en un Postgres LOCAL descartable con todas
las migraciones y datos sintéticos: formato y tamaño, quién puede guardarlas, misma política de acceso que la grabación
(empresa, publicada, sin vencer), limpieza al eliminar, cambio de archivo y vuelta atrás. No toca Supabase.

Uso:  python tests/base_local/test_videos_miniaturas_base.py
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



JPEG = b"\xff\xd8\xff\xe0" + b"m" * 5000


def guardar(cli, video, profesora, datos=JPEG):
    return rpc(cli, "video_guardar_miniatura", p_id_video=video, p_id_profesora=profesora,
               p_miniatura=psycopg2.Binary(datos))


def lee(cli, video):
    r = como(cli, "select miniatura from public.video where id = %s", (video,))
    return r if isinstance(r, dict) else [bytes(x[0]) if x[0] is not None else None for x in r]


programar(administradora1, h1)
programar(administradora2, h2)
v1 = registrar(prof1)["id_video"]
v2 = registrar(prof2)["id_video"]

# ---------- formato y quién guarda ----------
ok("la columna rechaza lo que no es JPEG", como(su_cli := ClienteFalso(uri, None, rol="service_role"),
   "update public.video set miniatura = %s where id = %s", (psycopg2.Binary(b"GIF89a" + b"x" * 5000), v1)) == {"error": "23514"})
for nombre, cli in (("anon", anon), ("la alumna", alumna1), ("la profesora", profesora1), ("la administradora", administradora1)):
    ok(f"{nombre} no puede guardar miniaturas", guardar(cli, v1, prof1) == {"error": "42501"})
ok("el servidor la guarda para su video", guardar(srvc, v1, prof1) == {"resultado": "ok"})
ok("no para el video de otra profesora", guardar(srvc, v1, prof2) == {"resultado": "no_corresponde"})
ok("rechaza lo que no es JPEG", guardar(srvc, v2, prof2, b"GIF89a" + b"x" * 5000) == {"resultado": "invalida"})
ok("rechaza demasiado grande", guardar(srvc, v2, prof2, b"\xff\xd8\xff" + b"x" * 200_000) == {"resultado": "invalida"})
ok("rechaza demasiado chica", guardar(srvc, v2, prof2, b"\xff\xd8\xff") == {"resultado": "invalida"})
ok("rechaza vacía", rpc(srvc, "video_guardar_miniatura", p_id_video=v2, p_id_profesora=prof2, p_miniatura=None) == {"resultado": "invalida"})

# ---------- misma política que la grabación ----------
ok("sin publicar, la alumna no la lee", lee(alumna1, v1) == [])
ok("publicar no cambia", publicar(v1, prof1).get("resultado") == "ok")
ok("publicada: la alumna de la empresa la lee", lee(alumna1, v1) == [JPEG])
ok("la profesora y la administradora de la empresa también", lee(profesora1, v1) == [JPEG] and lee(administradora1, v1) == [JPEG])
ok("la alumna de otra empresa no", lee(alumna2, v1) == [])
ok("la administradora de otra empresa no", lee(administradora2, v1) == [])
ok("anon no", lee(anon, v1) == {"error": "42501"})
ok("una alumna activa de la empresa la lee", lee(ClienteFalso(uri, baja), v1) == [JPEG])
q(su, "update public.membresia set activa = false where id_usuario = %s", (baja,))
ok("la misma cuenta dada de baja ya no", lee(ClienteFalso(uri, baja), v1) == [])
ok("ya publicado no se cambia la miniatura", guardar(srvc, v1, prof1, b"\xff\xd8\xff" + b"z" * 3000) == {"resultado": "no_corresponde"})
ok("la alumna no puede escribirla", como(alumna1, "update public.video set miniatura = null where id = %s", (v1,)) == {"error": "42501"})
ok("las demás columnas siguen ocultas", como(alumna1, "select lista_hls from public.video") == {"error": "42501"})

# ---------- vencimiento y limpieza ----------
q(su, "update public.video set vence_en = now() - interval '1 second' where id = %s", (v1,))
ok("vencida: ya no se lee", lee(alumna1, v1) == [])
rpc(srvc, "video_marcar_eliminado", p_id_video=v1)
r = q(su, "select estado, miniatura from public.video where id = %s", (v1,), uno=True)
ok("la limpieza la borra con la grabación", r["estado"] == "eliminado" and r["miniatura"] is None)

# ---------- otro archivo para la misma clase ----------
ok("guardada en el video que se sube", guardar(srvc, v2, prof2) == {"resultado": "ok"})
otra = registrar(prof2)
ok("registrar otro archivo para la misma clase reutiliza la fila", otra.get("id_video") == v2)
ok("y borra la miniatura del archivo anterior",
   q(su, "select miniatura from public.video where id = %s", (v2,), uno=True)["miniatura"] is None)
ok("un reintento con el mismo archivo la conserva", guardar(srvc, v2, prof2) == {"resultado": "ok"}
   and q(su, "update public.video set intentos = intentos + 1 where id = %s returning (miniatura is not null) t", (v2,), uno=True)["t"])

# ---------- vuelta atrás ----------
funciones = lambda: {r["proname"] for r in q(su, "select proname from pg_proc where proname like 'video%' or proname like '_video%'")}
antes = funciones()
with su.cursor() as cur:
    cur.execute(open(os.path.join(os.path.dirname(os.path.dirname(AQUI)), "supabase/pendientes/vuelta_atras_videos_miniaturas.sql")).read())
ok("la vuelta atrás quita la columna", q(su, "select count(*) n from information_schema.columns where table_name = 'video' and column_name = 'miniatura'", uno=True)["n"] == 0)
ok("y solo sus funciones", antes - funciones() == {"video_guardar_miniatura", "_video_miniatura_vigente"})
ok("las grabaciones siguen", q(su, "select count(*) n from public.video", uno=True)["n"] == 2)
ok("y se puede volver a aplicar", (lambda: [su.cursor().execute(open(sorted(localdb.migraciones())[-1]).read()), True][1])())

print(f"\n{total_casos - fallas}/{total_casos} casos correctos")
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
