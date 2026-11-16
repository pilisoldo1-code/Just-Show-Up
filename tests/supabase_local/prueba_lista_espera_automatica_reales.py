"""Notificaciones, fase D (lista de espera automática) contra el Supabase LOCAL + Mailpit. Solo datos ficticios.

Comprueba que, sin que nadie abra la plataforma, la tarea de cada minuto vence las ofertas de 30 minutos, ofrece el lugar
a la siguiente de la lista en orden, nunca deja sobrecupo ni reservas duplicadas, avisa a la siguiente (Novedades y
correo), respeta su interruptor, no toca clases canceladas ni empezadas, aísla Just Show Up de Pilates Centro, aguanta
carreras con las alumnas y con otra tarea, y funciona de punta a punta con pg_cron.

Correr sobre una base recién reiniciada. Uso:  python tests/supabase_local/prueba_lista_espera_automatica_reales.py
"""
import os
import sys
import threading
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notif_local import (DB_URL, RAIZ, SMTP_LOCAL, api, cuenta, entrar, entrar_superadmin, mailpit_mensajes,  # noqa: E402
                         mailpit_vaciar, ok, sql, terminar)
import psycopg2  # noqa: E402
from app import auth, clases, correos  # noqa: E402
from scripts import empresa_demo  # noqa: E402

ok("base recién reiniciada (sin cuentas)", sql("select count(*) n from auth.users", uno=True)["n"] == 0)
admin = auth.cliente_admin()
ahora = clases.ahora().replace(second=0, microsecond=0)
hoy = ahora.date()
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
cuenta("profesora@prueba.invalid", "Paula", "admin", JSU)
PROF = cuenta("profe.jsu@prueba.invalid", "Pía", "profesora", JSU)
AL = [cuenta(f"alu{i}.espera@prueba.invalid", f"Alu{i}", "alumna", JSU) for i in range(1, 9)]
ALMA = cuenta("alumna@prueba.invalid", "Alma", "alumna", JSU)
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]
CLAVE_SA = empresa_demo.crear_superadmin_local(empresa_demo.BaseSupabase())
empresa_demo.crear(empresa_demo.BaseSupabase(), hoy, guardar_contrasenas=False)
PIL = sql("select id from empresa where codigo = 'PIL'", uno=True)["id"]
SA = entrar_superadmin("superadmin@prueba.invalid", CLAVE_SA)
AJ = entrar("Just Show Up", "profesora@prueba.invalid")
S = {i: entrar("Just Show Up", f"alu{i + 1}.espera@prueba.invalid") for i in range(8)}
ok("cuentas, Pilates Centro y sesiones listas", PIL and SA and AJ and all(S.values()))
contador = iter(range(1, 10000))


def clase(cupo=1, dias=2, empresa=JSU, profesora=None, tipo=None, activo=True):
    n = next(contador)
    fecha = hoy + timedelta(days=dias)
    hora = f"{6 + n % 15:02d}:{(n * 7) % 60:02d}"
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "(%s, %s, %s, %s, %s, %s) returning id",
            (DIAS[fecha.weekday()], hora, tipo or TIPO, activo, profesora or PROF, empresa), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
               "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, %s, %s, %s, %s) returning id",
               (h, fecha, hora, cupo, tipo or TIPO, empresa, profesora or PROF), True)["id"]


def escenario(cupo, reservadas, esperando, empresa=JSU, **kw):
    """Clase llena con `reservadas` y una lista de espera en ese orden (posiciones 1, 2, 3...)."""
    c = clase(cupo=cupo, empresa=empresa, **kw)
    for a in reservadas:
        sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (a, c, empresa))
    sql("update clase set cantidad_inscriptas = %s where id = %s", (len(reservadas), c))
    for pos, a in enumerate(esperando, 1):
        sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, %s, 'en_espera', %s)",
            (a, c, pos, empresa))
    return c


def lista(c):
    return {f["id_alumna"]: f["estado"] for f in sql("select id_alumna, estado from listadeespera where id_clase = %s", (c,))}


def vencer(c):
    """Simula que pasaron los 30 minutos de la oferta."""
    sql("update listadeespera set oferta_vence_en = now() - interval '1 minute' where id_clase = %s and estado = 'ofrecido'", (c,))


def tick():
    return sql("select notificaciones_tick() t", uno=True)["t"]


def confirmadas(c):
    return sql("select count(*) n from reserva where id_clase = %s and estado = 'confirmada'", (c,), True)["n"]


def ofrecidas_vigentes(c):
    return sql("select count(*) n from listadeespera where id_clase = %s and estado = 'ofrecido' and oferta_vence_en > now()",
               (c,), True)["n"]


def liberar(alumna_idx, c):
    return api(S[alumna_idx]).rpc("cancelar_reserva", {"p_id_clase": c}).execute().data


# ---------- 1) interruptor y permisos ----------
ok("arranca apagada", sql("select lista_espera_automatica l from notificacion_parametros", uno=True)["l"] is False)
ok("ni la alumna ni la administradora la encienden",
   api(S[0]).rpc("notificaciones_lista_espera_activar", {"p_activo": True}).execute().data == "sin_permiso"
   and api(AJ).rpc("notificaciones_lista_espera_activar", {"p_activo": True}).execute().data == "sin_permiso")
C1 = escenario(1, [AL[0]], [AL[1], AL[2], AL[3]])
liberar(0, C1)
ok("como siempre: al cancelar la reservada se le ofrece a la primera de la lista", lista(C1)[AL[1]] == "ofrecido")
vencer(C1)
r = tick()
ok("apagada: la oferta vencida queda sin procesar aunque corra la tarea", r["resultado"] == "apagado"
   and lista(C1)[AL[1]] == "ofrecido" and lista(C1)[AL[2]] == "en_espera", r)
ok("la superadministradora la enciende",
   api(SA).rpc("notificaciones_lista_espera_activar", {"p_activo": True}).execute().data == "ok")

# ---------- 2) oferta vencida sin que nadie abra la plataforma ----------
r = tick()
oferta = sql("select oferta_vence_en from listadeespera where id_clase = %s and id_alumna = %s", (C1, AL[2]), True)
ok("la oferta vencida deja de estar disponible («expirado») y se ofrece a la SIGUIENTE en orden",
   lista(C1) == {AL[1]: "expirado", AL[2]: "ofrecido", AL[3]: "en_espera"} and r["clases_lista_espera"] >= 1, (lista(C1), r))
ok("la nueva oferta dura 30 minutos", 29 * 60 <= (oferta["oferta_vence_en"] - sql("select now() n", uno=True)["n"]).total_seconds() <= 30 * 60)
ok("la que dejó vencer la oferta ya no puede confirmarla",
   api(S[1]).rpc("confirmar_oferta", {"p_id_clase": C1}).execute().data == "vencida" and confirmadas(C1) == 0)
ok("la siguiente la ve en Novedades (¡Se liberó un lugar para vos!)", "Se liberó un lugar para vos" in S[2].get("/novedades").text)
ok("la siguiente confirma su lugar", api(S[2]).rpc("confirmar_oferta", {"p_id_clase": C1}).execute().data == "ok"
   and confirmadas(C1) == 1)
tick()
ok("con la clase llena no se ofrece nada más (sin sobrecupo)", lista(C1)[AL[3]] == "en_espera" and confirmadas(C1) == 1
   and ofrecidas_vigentes(C1) == 0)
ok("confirmar dos veces no duplica la reserva", api(S[2]).rpc("confirmar_oferta", {"p_id_clase": C1}).execute().data != "ok"
   and sql("select count(*) n from reserva where id_clase = %s and id_alumna = %s", (C1, AL[2]), True)["n"] == 1)

# orden con alguien que salió de la lista en el medio, y cupo de 2
C2 = escenario(2, [AL[0], AL[1]], [AL[2], AL[3], AL[4], AL[5]])
api(S[3]).rpc("salir_lista_espera", {"p_id_clase": C2}).execute()
liberar(0, C2)
liberar(1, C2)
ok("cupo 2: se ofrece a las dos primeras", lista(C2)[AL[2]] == "ofrecido" and lista(C2)[AL[4]] == "ofrecido")
vencer(C2)
tick()
ok("vencen las dos y se ofrece a las dos siguientes saltando a la que salió de la lista",
   lista(C2) == {AL[2]: "expirado", AL[3]: "retirado", AL[4]: "expirado", AL[5]: "ofrecido"}, lista(C2))
vencer(C2)
tick()
ok("si no queda nadie esperando, las ofertas solo vencen (el lugar queda libre para reservar)",
   lista(C2)[AL[5]] == "expirado" and ofrecidas_vigentes(C2) == 0 and confirmadas(C2) == 0)

# ---------- 3) clases canceladas, empezadas y oferta vigente ----------
C3 = escenario(1, [AL[0]], [AL[1], AL[2]])
liberar(0, C3)
api(AJ).rpc("crear_bloqueos_puntuales", {"p_ids_clase": [C3], "p_motivo": "PRUEBA lista automática"}).execute()
vencer(C3)
tick()
ok("clase cancelada por la profesora: no se ofrece a nadie", AL[2] not in lista(C3) or lista(C3)[AL[2]] != "ofrecido", lista(C3))
en10 = ahora + timedelta(minutes=10)
C4 = escenario(1, [AL[0]], [AL[1], AL[2], AL[3]])
liberar(0, C4)  # con tiempo: se le ofrece a la primera
sql("update clase set fecha = %s, hora_inicio = %s where id = %s", (en10.date(), en10.strftime("%H:%M"), C4))
vencer(C4)
tick()
ok("cerca del inicio, la oferta automática vence al empezar la clase (no a los 30 minutos)",
   lista(C4)[AL[2]] == "ofrecido"
   and sql("select oferta_vence_en <= now() + interval '11 minutes' x from listadeespera where id_clase = %s "
           "and estado = 'ofrecido'", (C4,), True)["x"], lista(C4))
sql("update clase set fecha = %s, hora_inicio = %s where id = %s", (hoy - timedelta(days=1), "10:00", C4))
vencer(C4)
tick()
ok("clase ya empezada: la tarea no la toca", lista(C4)[AL[3]] == "en_espera")
C5 = escenario(1, [AL[0]], [AL[1], AL[2]])
liberar(0, C5)
tick()
ok("oferta todavía vigente: la tarea no la vence antes de tiempo", lista(C5) == {AL[1]: "ofrecido", AL[2]: "en_espera"})

# ---------- 4) carreras ----------
resultados = []
for ronda in range(5):
    c = escenario(1, [AL[0]], [AL[1], AL[2], AL[3]])
    liberar(0, c)
    sql("update listadeespera set oferta_vence_en = now() + interval '1 second' where id_clase = %s and estado = 'ofrecido'", (c,))
    time.sleep(0.9 + ronda * 0.05)

    def confirmar():
        resultados.append(api(S[1]).rpc("confirmar_oferta", {"p_id_clase": c}).execute().data)

    def correr_tick():
        con = psycopg2.connect(DB_URL)
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute("select notificaciones_tick()")
        con.close()

    hilos = [threading.Thread(target=confirmar), threading.Thread(target=correr_tick)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    tick()
    ok(f"carrera {ronda + 1} (la alumna confirma justo cuando vence y corre la tarea): nunca sobrecupo ni dos lugares",
       confirmadas(c) + ofrecidas_vigentes(c) <= 1
       and (confirmadas(c) == 1) == (lista(c)[AL[1]] == "confirmado")
       and (lista(c)[AL[1]] == "confirmado" or lista(c)[AL[2]] == "ofrecido"), (lista(c), confirmadas(c), resultados[-1]))

c = escenario(2, [AL[0], AL[1]], [AL[2], AL[3], AL[4]])
liberar(0, c)
vencer(c)
hilos = [threading.Thread(target=lambda: liberar(1, c)), threading.Thread(target=tick)]
[h.start() for h in hilos]
[h.join() for h in hilos]
tick()
ok("carrera entre una cancelación y la tarea: se ofrecen exactamente los 2 lugares libres, en orden",
   confirmadas(c) == 0 and ofrecidas_vigentes(c) == 2 and lista(c)[AL[2]] == "expirado"
   and lista(c)[AL[3]] == "ofrecido" and lista(c)[AL[4]] == "ofrecido", lista(c))

con1 = psycopg2.connect(DB_URL)
con1.autocommit = False
with con1.cursor() as cur:
    cur.execute("select pg_advisory_xact_lock(hashtext('notificaciones_tick'))")
ok("dos tareas a la vez: la segunda no hace nada", tick()["resultado"] == "ocupado")
con1.rollback()
con1.close()

# ---------- 5) correo a la siguiente y aislamiento entre empresas ----------
sql("update notificacion_parametros set correo_permitir_ficticios = true, correo_tope_hora = 100")  # solo en local
api(SA).rpc("notificaciones_correo_activar", {"p_activo": True}).execute()
sql("update notificacion_envio set estado = 'descartado' where estado = 'pendiente'")  # lo de los pasos anteriores
mailpit_vaciar()
C6 = escenario(1, [AL[0]], [AL[5], AL[6]])
liberar(0, C6)
vencer(C6)
CARO = sql("select id from profiles where mail = %s", (empresa_demo.PROFESORA[0],), True)["id"]
TIPO_PIL = sql("select id from tipo_clase where id_empresa = %s order by id limit 1", (PIL,), True)["id"]
INES = sql("select id from profiles where mail = %s", (empresa_demo.INES,), True)["id"]
C_PIL = escenario(1, [INES], [ALMA], empresa=PIL, profesora=CARO, tipo=TIPO_PIL)
sql("update reserva set estado = 'cancelada', fecha_cancelacion = now() where id_clase = %s", (C_PIL,))
sql("update listadeespera set estado = 'ofrecido', oferta_vence_en = now() - interval '1 minute' where id_clase = %s", (C_PIL,))
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) select id, %s, 2, 'en_espera', %s "
    "from profiles where mail = %s", (C_PIL, PIL, empresa_demo.ALUMNAS[2][0]))
tick()
correos.despachar(admin, SMTP_LOCAL, lote=50)
msgs = mailpit_mensajes(esperar=5, cuantos=2)
lugares = [m for m in msgs if "Se liberó un lugar" in m["Subject"]]
para = {m["To"][0]["Address"]: m["Subject"] for m in lugares}
ok("la siguiente de Just Show Up recibe el correo «¡Se liberó un lugar para vos!» en la misma vuelta",
   para.get("alu7.espera@prueba.invalid", "").startswith("Just Show Up: ¡Se liberó"), para)
ok("en Pilates Centro se procesa su propia lista y el correo sale como Pilates Centro",
   lista(C_PIL)[ALMA] == "expirado" and para.get(empresa_demo.ALUMNAS[2][0], "").startswith("Pilates Centro: ¡Se liberó"), para)
filas_vigentes = sql("select p.mail from listadeespera le join profiles p on p.id = le.id_alumna "
                     "where le.estado = 'ofrecido' and le.oferta_vence_en > now()")
vigentes = {r["mail"] for r in filas_vigentes}
ok("solo reciben aviso de lugar las ofertas vigentes (también las de antes de encender los correos); las que dejaron "
   "vencer su oferta, no", set(para) == vigentes and "alu6.espera@prueba.invalid" not in para
   and "alumna@prueba.invalid" not in para and len(lugares) == len(filas_vigentes), (sorted(para), sorted(vigentes)))

# ---------- 6) de punta a punta con pg_cron (sin correos: solo la lista) ----------
api(SA).rpc("notificaciones_correo_activar", {"p_activo": False}).execute()
with open(os.path.join(RAIZ, "supabase", "pendientes", "notificaciones_cron_activar.sql")) as f:
    sql(f.read())
C7 = escenario(1, [AL[0]], [AL[1], AL[2]])
liberar(0, C7)
vencer(C7)
fin = time.monotonic() + 150
while time.monotonic() < fin and lista(C7)[AL[2]] != "ofrecido":
    time.sleep(5)
ok("pg_cron procesa la oferta vencida sin que nadie abra la plataforma",
   lista(C7) == {AL[1]: "expirado", AL[2]: "ofrecido"}, lista(C7))
with open(os.path.join(RAIZ, "supabase", "pendientes", "notificaciones_cron_desactivar.sql")) as f:
    sql(f.read())
ok("desactivar: sin tareas y con los dos interruptores apagados",
   sql("select count(*) n from cron.job", uno=True)["n"] == 0
   and sql("select correo_activo or lista_espera_automatica x from notificacion_parametros", uno=True)["x"] is False)
terminar()
