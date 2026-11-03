"""Aislamiento entre empresas (y entre profesoras) de las consultas nuevas de Estadísticas
(ocupación, tipos más elegidos), en un Postgres LOCAL descartable con dos empresas sintéticas.

Esto prueba la política RLS real de clase/reserva/asistencia (de la que depende
app/estadisticas.py) con los mismos filtros que usan `_leer_clases_terminadas` y
`_reservas_confirmadas_de`: que una profesora común nunca ve otra empresa ni (para
asistencia) otra profesora, y que una administradora ve toda su empresa y nada de la otra.
La lógica de qué id_profesora elegir según el rol (si es administradora o no) ya se prueba
con mocks en tests/test_estadisticas.py (clase Permisos); acá se prueba el límite real de la
base del que esa lógica depende.

Uso (con las dependencias de requirements-dev.txt):
    python tests/base_local/test_estadisticas_multiempresa_base.py
"""
import os
import sys
import uuid
from datetime import date, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        cur.execute(open(a).read())
q(su, localdb.RELOJ)

AYER = (date.today() - timedelta(days=1)).isoformat()


def persona(mail, empresa_id, admin=False, profe=False, alumna=False):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].title(), "apellido": "Prueba"})))
    q(su, "insert into membresia (id_usuario, id_empresa, es_admin, es_profesora, es_alumna) "
          "values (%s, %s, %s, %s, %s) on conflict (id_usuario, id_empresa) do update "
          "set es_admin = excluded.es_admin, es_profesora = excluded.es_profesora, es_alumna = excluded.es_alumna",
      (i, empresa_id, admin, profe, alumna))
    # ClienteFalso siembra sesion_empresa a partir de este valor al conectar (ver como()).
    q(su, "update profiles set empresa_activa = %s where id = %s", (empresa_id, i))
    return i


def empresa(nombre, codigo):
    return q(su, "insert into empresa (nombre, codigo) values (%s, %s) returning id", (nombre, codigo), True)["id"]


def tipo(nombre, empresa_id):
    return q(su, "insert into tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max, id_empresa) "
                "values (%s, '', 60, 800, 4, %s) returning id", (nombre, empresa_id), True)["id"]


def horario_de(dia, id_tipo, empresa_id, id_profesora):
    return q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_empresa, id_profesora) "
                "values (%s, '08:00', %s, true, %s, %s) returning id",
             (dia, id_tipo, empresa_id, id_profesora), True)["id"]


def clase_terminada(id_horario, id_tipo, empresa_id, id_profesora, fecha=AYER):
    return q(su, "insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado, duracion, "
                "precio, cupo_max, id_tipo_clase, id_empresa, id_profesora) "
                "values (%s, %s, '08:00', 0, 'programada', 60, 800, 4, %s, %s, %s) returning id",
             (id_horario, fecha, id_tipo, empresa_id, id_profesora), True)["id"]


def reserva(alumna, id_clase, empresa_id, estado="confirmada"):
    return q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, %s, %s) returning id",
             (alumna, id_clase, estado, empresa_id), True)["id"]


def asistencia(id_reserva, empresa_id, estado="presente"):
    q(su, "insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (id_reserva, estado, empresa_id))


# ---------- Empresa 1: una administradora y dos profesoras ----------
E1 = empresa("Empresa Uno", "UNO")
ADM1 = persona("admin1@prueba.invalid", E1, admin=True)
PROF1A = persona("profe1a@prueba.invalid", E1, profe=True)
PROF1B = persona("profe1b@prueba.invalid", E1, profe=True)
ALU1 = persona("alumna1@prueba.invalid", E1, alumna=True)
T1 = tipo("Gimnasia", E1)
H1A = horario_de("lunes", T1, E1, PROF1A)
H1B = horario_de("martes", T1, E1, PROF1B)
C1A = clase_terminada(H1A, T1, E1, PROF1A)
C1B = clase_terminada(H1B, T1, E1, PROF1B)
R1A = reserva(ALU1, C1A, E1)
R1A_cancelada = reserva(ALU1, C1B, E1, estado="cancelada")  # no debería contar en ninguna consulta
R1B = reserva(ALU1, C1B, E1)
asistencia(R1A, E1, "presente")
asistencia(R1B, E1, "ausente")

# ---------- Empresa 2: una sola administradora-profesora, para probar que no se mezcla con la 1 ----------
E2 = empresa("Empresa Dos", "DOS")
ADM2 = persona("admin2@prueba.invalid", E2, admin=True)
ALU2 = persona("alumna2@prueba.invalid", E2, alumna=True)
T2 = tipo("Pilates", E2)
H2 = horario_de("miercoles", T2, E2, ADM2)
C2 = clase_terminada(H2, T2, E2, ADM2)
R2 = reserva(ALU2, C2, E2)
asistencia(R2, E2, "presente")


def como(uid):
    """Una sesión nueva de esa persona: ClienteFalso siembra sesion_empresa con el
    profiles.empresa_activa que le puso persona() — el mismo mecanismo real de la app."""
    return ClienteFalso(uri, uid).conn


def ids_clase_vistas(conn):
    return {c["id"] for c in q(conn, "select id from clase where estado = 'programada' and fecha <= current_date")}


def ids_reserva_confirmada_vistas(conn, ids_clase):
    if not ids_clase:
        return set()
    filas = q(conn, "select id_clase from reserva where id_clase = any(%s) and estado = 'confirmada'", (ids_clase,))
    return {f["id_clase"] for f in filas}


def ids_asistencia_vistas(conn):
    return {a["id"] for a in q(conn, "select id from asistencia")}


# ---------- profesora común de la Empresa 1 (PROF1A) ----------
p1a = como(PROF1A)
ok("profesora PROF1A ve las clases de TODA su empresa en 'clase' (RLS no filtra por profesora acá)",
   ids_clase_vistas(p1a) == {C1A, C1B})
ok("profesora PROF1A NO ve ninguna clase de la Empresa 2", C2 not in ids_clase_vistas(p1a))
# A diferencia de "clase" (sin filtro por profesora en RLS), "reserva" SÍ está acotada por
# profesora para quien no es admin: PROF1A solo ve la reserva de SU clase (C1A), no la de C1B
# (de PROF1B) aunque pida explícitamente las dos — y nunca la cancelada.
ok("profesora PROF1A ve reservas confirmadas solo de sus propias clases (RLS sí filtra acá), sin la cancelada",
   ids_reserva_confirmada_vistas(p1a, [C1A, C1B]) == {C1A})
asist_p1a = ids_asistencia_vistas(p1a)
ok("profesora PROF1A solo ve SU PROPIA asistencia (la de PROF1B queda afuera por RLS)",
   len(asist_p1a) == 1)
ok("esa asistencia es de una clase de PROF1A, no de PROF1B",
   q(p1a, "select r.id_clase from asistencia a join reserva r on r.id = a.id_reserva limit 1", uno=True)["id_clase"] == C1A)

# ---------- administradora de la Empresa 1 (ADM1) ----------
adm1 = como(ADM1)
ok("administradora ADM1 ve toda la asistencia de su empresa (las dos profesoras)",
   len(ids_asistencia_vistas(adm1)) == 2)
ok("administradora ADM1 no ve nada de la Empresa 2", C2 not in ids_clase_vistas(adm1))

# ---------- administradora de la Empresa 2 (ADM2): nunca ve nada de la Empresa 1 ----------
adm2 = como(ADM2)
ok("administradora ADM2 ve solo su propia empresa", ids_clase_vistas(adm2) == {C2})
ok("administradora ADM2 no ve clases de la Empresa 1", not ({C1A, C1B} & ids_clase_vistas(adm2)))
ok("administradora ADM2 no ve asistencias de la Empresa 1", len(ids_asistencia_vistas(adm2)) == 1)

# ---------- intento directo: pedir explícitamente filas de la otra empresa ----------
intento_cruzado = q(p1a, "select id from clase where id_empresa = %s", (E2,))
ok("pedir explícitamente id_empresa de otra empresa no devuelve nada (lo bloquea la RLS, no el filtro)",
   intento_cruzado == [])

print(f"\n{total_casos - fallas}/{total_casos} casos OK" + (f" — {fallas} FALLAS" if fallas else ""))
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
