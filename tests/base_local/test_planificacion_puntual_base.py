"""Cancelar clases puntuales (checkboxes) en un Postgres LOCAL descartable, con todas las
migraciones del repo (incluida la nueva `crear_bloqueos_puntuales`). No toca Supabase ni usa
datos reales.

Uso (con las dependencias de requirements-dev.txt):
    python tests/base_local/test_planificacion_puntual_base.py
"""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import localdb  # noqa: E402
from localdb import conectar, huella, q  # noqa: E402
import psycopg2  # noqa: E402

fallas = total_casos = 0


def ok(n, c, extra=""):
    global fallas, total_casos
    total_casos += 1
    fallas += not c
    print(("OK   " if c else "FALLA") + " " + n + (f"  [{extra}]" if extra and not c else ""))


# ---------- base: todas las migraciones + datos sintéticos ----------
srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        cur.execute(open(a).read())
q(su, localdb.RELOJ)

usuarias = {}
for nombre in ("Profe", "Uno", "Dos", "Tres"):
    uid = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (uid, f"{nombre.lower()}@prueba.invalid", f'{{"nombre": "{nombre}", "apellido": "Prueba"}}'))
    usuarias[nombre] = uid
PROF = usuarias["Profe"]
q(su, "update profiles set rol = 'profesora' where id = %s", (PROF,))
# desde multiempresa (parte 4) los roles salen solo de la membresía
q(su, "update membresia set es_admin = true, es_profesora = true, es_alumna = false where id_usuario = %s", (PROF,))
A1, A2, A3 = (usuarias[n] for n in ("Uno", "Dos", "Tres"))

GIM = q(su, "select id from tipo_clase where nombre = 'Gimnasia'", uno=True)["id"]

LUNES = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=1)  # lunes futuro
MARTES = LUNES + timedelta(days=1)


def horario(dia, hora):
    return q(su, """insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo)
                     values (%s, %s, %s, true) returning id""", (dia, hora, GIM), True)["id"]


def clase_de(id_horario, fecha, hora, estado="programada"):
    return q(su, """insert into clase (id_horario, fecha, hora_inicio, cantidad_inscriptas, estado,
                                       duracion, precio, cupo_max, id_tipo_clase)
                     values (%s, %s, %s, 0, %s, 60, 800, 7, %s) returning id""",
             (id_horario, fecha, hora, estado, GIM), True)["id"]


def reservar(alumna, id_clase):
    return q(su, "insert into reserva (id_alumna, id_clase, estado) values (%s, %s, 'confirmada') returning id",
             (alumna, id_clase), True)["id"]


def anotar_espera(alumna, id_clase, estado="en_espera"):
    return q(su, """insert into listadeespera (id_alumna, id_clase, estado, posicion)
                     values (%s, %s, %s, 1) returning id""", (alumna, id_clase, estado), True)["id"]


H8, H9, H17 = horario("lunes", "08:00"), horario("lunes", "09:00"), horario("lunes", "17:00")
H_MAR = horario("martes", "08:00")

TABLAS = ["horario", "clase", "reserva", "listadeespera", "bloqueo", "tipo_clase"]


def foto():
    f = {t: huella(su, t) for t in TABLAS}
    f["profiles"] = huella(su, "profiles", "id, nombre, apellido, mail, rol")
    return f


def profesora(uid=PROF):
    return conectar(uri, uid, "authenticated")


def rpc(conn, nombre, **params):
    args = ", ".join(f"{k} => %s" for k in params)
    with conn.cursor() as cur:
        cur.execute(f"select public.{nombre}({args})", list(params.values()))
        return cur.fetchone()[0]


# ---------- 1. cancelar una sola clase ----------
c8 = clase_de(H8, LUNES.isoformat(), "08:00")
c9 = clase_de(H9, LUNES.isoformat(), "09:00")
c17 = clase_de(H17, LUNES.isoformat(), "17:00")
reservar(A1, c8)
reservar(A2, c8)
anotar_espera(A3, c8)

pcon = profesora()
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[c8], p_motivo="Consulta médica")
ok("cancelar una sola clase: resultado ok", r["resultado"] == "ok")
ok("cancelar una sola clase: cuenta 1 clase, 2 reservas, 1 en espera",
   (r["clases"], r["reservas"], r["espera"]) == (1, 2, 1), r)
fila8 = q(su, "select estado, cantidad_inscriptas from clase where id = %s", (c8,), True)
ok("la clase 08:00 queda cancelada", fila8["estado"] == "cancelada" and fila8["cantidad_inscriptas"] == 0)
reservas8 = q(su, "select estado, id_bloqueo from reserva where id_clase = %s", (c8,))
ok("las 2 reservas de la 08:00 quedan canceladas con id_bloqueo",
   all(x["estado"] == "cancelada" and x["id_bloqueo"] for x in reservas8))
espera8 = q(su, "select estado from listadeespera where id_clase = %s", (c8,), True)
ok("la lista de espera de la 08:00 queda expirada", espera8["estado"] == "expirado")

# ---------- 2. las no seleccionadas siguen activas ----------
fila9 = q(su, "select estado from clase where id = %s", (c9,), True)
fila17 = q(su, "select estado from clase where id = %s", (c17,), True)
ok("la clase 09:00 (no seleccionada) sigue programada", fila9["estado"] == "programada")
ok("la clase 17:00 (no seleccionada) sigue programada", fila17["estado"] == "programada")

bloqueos8 = q(su, "select tipo, fecha_desde, fecha_hasta, id_horario from bloqueo where id_horario = %s", (H8,))
ok("se creó exactamente un bloqueo puntual para la clase de las 08:00",
   len(bloqueos8) == 1 and bloqueos8[0]["tipo"] == "puntual"
   and bloqueos8[0]["fecha_desde"].isoformat() == LUNES.isoformat() == bloqueos8[0]["fecha_hasta"].isoformat())

# ---------- 3. cancelar varias clases del mismo día ----------
reservar(A1, c9)
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[c9, c17], p_motivo="Corte de luz")
ok("cancelar 2 clases del mismo día: resultado ok y cuenta 2 clases", r["resultado"] == "ok" and r["clases"] == 2, r)
ok("quedaron 2 bloqueos nuevos (uno por clase)",
   q(su, "select count(*) n from bloqueo where id_horario in (%s, %s)", (H9, H17), True)["n"] == 2)
ok("la 09:00 y la 17:00 quedan canceladas",
   q(su, "select estado from clase where id = %s", (c9,), True)["estado"] == "cancelada"
   and q(su, "select estado from clase where id = %s", (c17,), True)["estado"] == "cancelada")

# ---------- 4. todo o nada: una clase inválida en el grupo no cancela ninguna ----------
cA = clase_de(H_MAR, MARTES.isoformat(), "08:00")
cB = clase_de(horario("martes", "09:00"), MARTES.isoformat(), "09:00")
reservar(A1, cA)
antes = foto()
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cA, cB, 999999], p_motivo="Prueba todo o nada")
ok("id inexistente en el grupo: resultado clases_invalidas", r["resultado"] == "clases_invalidas", r)
ok("no se modificó nada (todo o nada)", foto() == antes)

# ya cancelada
cC = clase_de(horario("martes", "10:00"), MARTES.isoformat(), "10:00")
rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cC], p_motivo="Cancelación previa")
antes = foto()
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cA, cC], p_motivo="Prueba con una ya cancelada")
ok("una ya cancelada en el grupo: resultado clases_invalidas", r["resultado"] == "clases_invalidas", r)
ok("no se modificó nada", foto() == antes)

# ya empezada (en el pasado)
cPasada = clase_de(horario("miercoles", "08:00"), (date.today() - timedelta(days=1)).isoformat(), "08:00")
antes = foto()
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cA, cPasada], p_motivo="Prueba con una ya empezada")
ok("una ya empezada en el grupo: resultado clases_invalidas", r["resultado"] == "clases_invalidas", r)
ok("no se modificó nada", foto() == antes)

# de un horario desactivado
H_DESACTIVADO = horario("miercoles", "11:00")
cDesactivada = clase_de(H_DESACTIVADO, MARTES.isoformat(), "11:00")
q(su, "update horario set activo = false where id = %s", (H_DESACTIVADO,))
antes = foto()
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cA, cDesactivada], p_motivo="Prueba con horario desactivado")
ok("una de horario desactivado en el grupo: resultado clases_invalidas", r["resultado"] == "clases_invalidas", r)
ok("no se modificó nada", foto() == antes)
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cDesactivada], p_motivo="Sola, de horario desactivado")
ok("sola, de horario desactivado: también se rechaza", r["resultado"] == "clases_invalidas", r)
ok("la clase sigue programada (no se canceló)",
   q(su, "select estado from clase where id = %s", (cDesactivada,), True)["estado"] == "programada")

# ---------- 5. validaciones básicas ----------
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[], p_motivo="algo")
ok("lista vacía: resultado sin_clases", r["resultado"] == "sin_clases")
r = rpc(pcon, "crear_bloqueos_puntuales", p_ids_clase=[cA], p_motivo="   ")
ok("motivo vacío: resultado motivo_vacio", r["resultado"] == "motivo_vacio")

# ---------- 6. permisos ----------
acon = profesora(A1)
r = rpc(acon, "crear_bloqueos_puntuales", p_ids_clase=[cA], p_motivo="intento de alumna")
ok("una alumna recibe no_autorizada", r["resultado"] == "no_autorizada")

anon = conectar(uri, None, "anon")
denegado = False
try:
    rpc(anon, "crear_bloqueos_puntuales", p_ids_clase=[cA], p_motivo="anon")
except psycopg2.Error as e:
    denegado = e.pgcode == "42501"
ok("sin sesión (anon) se rechaza a nivel de permisos", denegado)

# ---------- 7. revertir individualmente (quitar_bloqueo) ----------
bloqueo9 = q(su, "select id from bloqueo where id_horario = %s", (H9,), True)["id"]
bloqueo17 = q(su, "select id from bloqueo where id_horario = %s", (H17,), True)["id"]
r = rpc(pcon, "quitar_bloqueo", p_id=bloqueo9)
ok("quitar el bloqueo de la 09:00: resultado ok y 1 clase recuperada", r["resultado"] == "ok" and r["clases"] == 1, r)
ok("la 09:00 vuelve a estar programada", q(su, "select estado from clase where id = %s", (c9,), True)["estado"] == "programada")
ok("la reserva de la 09:00 se recupera", q(su, "select estado from reserva where id_clase = %s", (c9,), True)["estado"] == "confirmada")
ok("la 17:00 sigue cancelada (bloqueo distinto, no se tocó)", q(su, "select estado from clase where id = %s", (c17,), True)["estado"] == "cancelada")

# ---------- 8. concurrencia: dos pedidos a la vez por la misma clase ----------
cConc = clase_de(horario("jueves", "08:00"), (LUNES + timedelta(days=3)).isoformat(), "08:00")
resultados = []
barrera = threading.Barrier(2)


def intentar():
    con = profesora()
    barrera.wait()
    resultados.append(rpc(con, "crear_bloqueos_puntuales", p_ids_clase=[cConc], p_motivo="concurrencia")["resultado"])
    con.close()


with ThreadPoolExecutor(max_workers=2) as ex:
    list(ex.map(lambda _: intentar(), range(2)))
oks = resultados.count("ok")
invalidas = resultados.count("clases_invalidas")
ok("concurrencia: solo uno de los dos pedidos simultáneos cancela la clase",
   oks == 1 and invalidas == 1, resultados)
canceladas_conc = q(su, "select count(*) n from clase where id = %s and estado = 'cancelada'", (cConc,), True)["n"]
ok("la clase quedó cancelada una sola vez", canceladas_conc == 1)
bloqueos_conc = q(su, "select count(*) n from bloqueo where fecha_desde = %s and fecha_hasta = %s",
                   ((LUNES + timedelta(days=3)).isoformat(), (LUNES + timedelta(days=3)).isoformat()), True)["n"]
ok("se creó un solo bloqueo para esa clase (no dos)", bloqueos_conc == 1)

# ---------- 9. no se cobra lo cancelado (la reserva queda 'cancelada', igual que una licencia) ----------
ok("las reservas canceladas por un bloqueo puntual quedan con el mismo estado que usa facturación para excluir "
   "(estado='cancelada'), igual que las licencias por período",
   q(su, "select estado from reserva where id_clase = %s and id_alumna = %s", (c8, A1), True)["estado"] == "cancelada")

print(f"\n{total_casos - fallas}/{total_casos} casos OK" + (f" — {fallas} FALLAS" if fallas else ""))
localdb.borrar(srv, directorio)
sys.exit(1 if fallas else 0)
