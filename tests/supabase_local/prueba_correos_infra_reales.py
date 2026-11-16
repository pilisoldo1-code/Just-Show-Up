"""Notificaciones, fase B (infraestructura de correos) contra el Supabase LOCAL + Mailpit. Solo datos ficticios; nunca
producción ni correos reales (la URL tiene que ser localhost y el SMTP es Mailpit en 127.0.0.1:54325).

Comprueba: permisos de la tabla y de las funciones, interruptor general, preferencia en Mi perfil, prevención de
duplicados, envío real a Mailpit con una sola conexión, correos ficticios, topes por hora y por día, errores, reintentos
con espera, máximo de intentos, tomas colgadas, dos despachadores y dos tareas a la vez, seguridad del endpoint y el
recorrido completo pg_cron → pg_net → servidor → Mailpit.

Correr sobre una base recién reiniciada. Uso:  python tests/supabase_local/prueba_correos_infra_reales.py
"""
import os
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notif_local import (CLAVE, DB, DB_URL, RAIZ, SMTP_LOCAL, api, como_servicio, cuenta, destinatarios, entrar,  # noqa: E402
                         entrar_superadmin, falla_permiso, mailpit_cabeceras, mailpit_mensajes, mailpit_vaciar, ok, sql,
                         terminar)
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, correos, main  # noqa: E402
from scripts import empresa_demo  # noqa: E402

ok("base recién reiniciada (sin cuentas)", sql("select count(*) n from auth.users", uno=True)["n"] == 0)
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADM = cuenta("profesora@prueba.invalid", "Paula", "admin", JSU)
ALUMNAS = [cuenta(f"alu{i}.correos@prueba.invalid", f"Alu{i}", "alumna", JSU) for i in range(1, 7)]
CLAVE_SA = empresa_demo.crear_superadmin_local(empresa_demo.BaseSupabase())
SA = entrar_superadmin("superadmin@prueba.invalid", CLAVE_SA)
AJ = entrar("Just Show Up", "profesora@prueba.invalid")
A1 = entrar("Just Show Up", "alu1.correos@prueba.invalid")
ok("inician sesión la superadministradora, la administradora y una alumna", all([SA, AJ, A1]))
admin = auth.cliente_admin()
ref = iter(range(1000, 100000))


PROF = cuenta("profe.correos@prueba.invalid", "Pía", "profesora", JSU)
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def reserva_real(alumna):
    """Una reserva confirmada real de una clase futura (desde la fase C, cada aviso se revalida contra la base)."""
    n = next(ref)
    fecha = __import__("datetime").date.today() + __import__("datetime").timedelta(days=3 + n % 20)
    hora = f"{6 + n % 14:02d}:{(n // 14) % 60:02d}"
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "(%s, %s, %s, false, %s, %s) returning id", (DIAS[fecha.weekday()], hora, TIPO, PROF, JSU), True)["id"]
    c = sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
            "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, 7, %s, %s, %s) returning id",
            (h, fecha, hora, TIPO, JSU, PROF), True)["id"]
    return sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
               (alumna, c, JSU), True)["id"]


def encolar(alumna, tipo="reserva_confirmada", prioridad=5):
    # si la tarea de cada minuto ya lo encoló (pg_cron en el punto 12), se usa ese mismo registro
    return sql("insert into notificacion_envio (id_empresa, id_usuario, tipo, referencia, prioridad) values "
               "(%s, %s, %s, %s, %s) on conflict (tipo, referencia, canal) do update set actualizado_en = now() "
               "returning id", (JSU, alumna, tipo, reserva_real(alumna), prioridad), True)


def estado(id_):
    return sql("select * from notificacion_envio where id = %s", (id_,), True)


def limpiar_cola():
    # las reservas de la vuelta anterior se cancelan (como si las cancelara la alumna): la tarea no las vuelve a encolar
    sql("update reserva set estado = 'cancelada', fecha_cancelacion = now() where estado = 'confirmada'")
    sql("delete from notificacion_envio")


# ---------- 1) permisos ----------
ok("la alumna no puede leer la tabla de envíos",
   falla_permiso(lambda: api(A1).table("notificacion_envio").select("*").execute()) or
   api(A1).table("notificacion_envio").select("*").execute().data == [])
for f, p in (("notificaciones_reclamar", {"p_lote": 5}), ("notificaciones_resultado", {"p_id": 1, "p_resultado": "enviado"}),
             ("notificaciones_tick", {}), ("notificaciones_guardar_secreto", {"p_nombre": "notificaciones_url", "p_valor": "x"}),
             ("notificaciones_resumen", {})):
    ok(f"la alumna y la administradora no pueden ejecutar {f}",
       falla_permiso(lambda: api(A1).rpc(f, p).execute()) and falla_permiso(lambda: api(AJ).rpc(f, p).execute()))
ok("el servidor (service_role) no puede ejecutar la tarea de cada minuto (solo pg_cron)",
   falla_permiso(lambda: admin.rpc("notificaciones_tick", {}).execute()))
ok("interruptor: ni la alumna ni la administradora lo encienden",
   api(A1).rpc("notificaciones_correo_activar", {"p_activo": True}).execute().data == "sin_permiso"
   and api(AJ).rpc("notificaciones_correo_activar", {"p_activo": True}).execute().data == "sin_permiso")
ok("arranca apagado y sin fecha de inicio",
   sql("select correo_activo, correo_desde from notificacion_parametros", uno=True) == {"correo_activo": False, "correo_desde": None})

# ---------- 2) preferencia en Mi perfil ----------
r = A1.get("/perfil")
ok("Mi perfil de la alumna muestra «Recibir notificaciones por correo electrónico» marcada",
   "Recibir notificaciones por correo electrónico" in r.text and 'name="recibir" value="1" checked' in r.text)
ok("Mi perfil de la administradora no muestra la preferencia (los avisos por correo son de alumnas)",
   "Recibir notificaciones por correo" not in AJ.get("/perfil").text)
A1.post("/perfil/avisos", data={}, follow_redirects=False)
ok("la alumna la desmarca y queda guardada",
   sql("select recibir_correos r from profiles where id = %s", (ALUMNAS[0],), True)["r"] is False
   and 'name="recibir" value="1" checked' not in A1.get("/perfil").text)
A1.post("/perfil/avisos", data={"recibir": "1"}, follow_redirects=False)
ok("la vuelve a marcar", sql("select recibir_correos r from profiles where id = %s", (ALUMNAS[0],), True)["r"] is True)
ok("cambiar la preferencia solo afecta su propia cuenta",
   sql("select count(*) n from profiles where recibir_correos = false", uno=True)["n"] == 0)

# ---------- 3) cola, duplicados e interruptor ----------
e1 = encolar(ALUMNAS[0])
dup = sql("insert into notificacion_envio (id_empresa, id_usuario, tipo, referencia) select id_empresa, id_usuario, tipo, "
          "referencia from notificacion_envio where id = %s on conflict do nothing returning id", (e1["id"],))
ok("el mismo aviso (tipo + referencia + canal) no se puede encolar dos veces", e1 and dup == [])
ok("con el interruptor apagado no se toma nada", admin.rpc("notificaciones_reclamar", {"p_lote": 10}).execute().data == [])
ok("con el interruptor apagado la tarea no hace nada", sql("select notificaciones_tick() t", uno=True)["t"]["resultado"] == "apagado")
ok("la superadministradora enciende el interruptor (queda la fecha desde la que rige)",
   api(SA).rpc("notificaciones_correo_activar", {"p_activo": True}).execute().data == "ok"
   and sql("select correo_desde is not null d from notificacion_parametros", uno=True)["d"])

# correos ficticios (.invalid): en producción nunca se mandan
tomados = admin.rpc("notificaciones_reclamar", {"p_lote": 10}).execute().data
ok("los correos ficticios (.invalid) no se mandan salvo en pruebas", tomados == []
   and estado(e1["id"])["estado"] == "omitido" and estado(e1["id"])["motivo"] == "correo_ficticio")
sql("update notificacion_parametros set correo_permitir_ficticios = true")  # solo en la base local

# ---------- 4) envío real a Mailpit ----------
limpiar_cola()
mailpit_vaciar()
ids = [encolar(a)["id"] for a in ALUMNAS[:3]]
conexiones = []


def conectar_contando(c):
    conexiones.append(1)
    return correos._conectar(c)


res = correos.despachar(admin, SMTP_LOCAL, conectar=conectar_contando)
mensajes = mailpit_mensajes(esperar=5, cuantos=3)
ok("despacho: 3 tomados y 3 enviados con UNA sola conexión SMTP", res == {"tomados": 3, "enviados": 3, "errores": 0,
                                                                          "liberados": 0} and len(conexiones) == 1, res)
ok("Mailpit recibió los 3, uno por alumna",
   destinatarios(mensajes) == sorted(f"alu{i}.correos@prueba.invalid" for i in (1, 2, 3)), destinatarios(mensajes))
ok("quedan «enviado» con fecha y un intento",
   all(estado(i)["estado"] == "enviado" and estado(i)["enviado_en"] and estado(i)["intentos"] == 1 for i in ids))
cab = mailpit_cabeceras(mensajes[0]["ID"])
ok("el correo lleva la empresa en el asunto y en el remitente, y es automático",
   mensajes[0]["Subject"].startswith("Just Show Up: ") and "Just Show Up (Just Show Up)" in cab["From"][0]
   and cab.get("Auto-Submitted") == ["auto-generated"], (mensajes[0]["Subject"], cab.get("From")))
ok("volver a despachar no repite nada", correos.despachar(admin, SMTP_LOCAL)["tomados"] == 0
   and len(mailpit_mensajes()) == 3)

# ---------- 5) preferencia apagada ----------
e = encolar(ALUMNAS[1])
A2 = entrar("Just Show Up", "alu2.correos@prueba.invalid")
A2.post("/perfil/avisos", data={}, follow_redirects=False)
ok("al desactivar los correos, lo que estaba en cola queda omitido", estado(e["id"])["estado"] == "omitido"
   and estado(e["id"])["motivo"] == "preferencia")
e = encolar(ALUMNAS[1])
ok("con la preferencia apagada, un aviso nuevo se omite al tomarlo", correos.despachar(admin, SMTP_LOCAL)["tomados"] == 0
   and estado(e["id"])["estado"] == "omitido" and len(mailpit_mensajes()) == 3)
A2.post("/perfil/avisos", data={"recibir": "1"}, follow_redirects=False)

# ---------- 6) alumna dada de baja o de otra empresa ----------
sql("update membresia set activa = false where id_usuario = %s and id_empresa = %s", (ALUMNAS[5], JSU))
e = encolar(ALUMNAS[5])
correos.despachar(admin, SMTP_LOCAL)
ok("una alumna desactivada no recibe correos", estado(e["id"])["estado"] == "descartado" and len(mailpit_mensajes()) == 3)
sql("update membresia set activa = true where id_usuario = %s and id_empresa = %s", (ALUMNAS[5], JSU))

# ---------- 7) topes ----------
limpiar_cola()
mailpit_vaciar()
ok("tope por hora: la superadministradora lo pone en 2",
   api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_tope_hora", "p_valor": 2}).execute().data == "ok")
ids = [encolar(a)["id"] for a in ALUMNAS[:5]]
r1, r2 = correos.despachar(admin, SMTP_LOCAL), correos.despachar(admin, SMTP_LOCAL)
ok("tope por hora: de 5 pendientes salen 2 y el resto espera", r1["enviados"] == 2 and r2["tomados"] == 0
   and len(mailpit_mensajes(esperar=3, cuantos=2)) == 2
   and sum(estado(i)["estado"] == "pendiente" for i in ids) == 3, (r1, r2))
api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_tope_hora", "p_valor": 40}).execute()
api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_tope_dia", "p_valor": 3}).execute()
r3 = correos.despachar(admin, SMTP_LOCAL)
ok("tope diario (24 h): con 2 enviados y tope 3, sale 1 más", r3["enviados"] == 1, r3)
# lo enviado hace más de 24 horas ya no cuenta para el tope diario
sql("update notificacion_envio set enviado_en = now() - interval '25 hours' where estado = 'enviado'")
r4 = correos.despachar(admin, SMTP_LOCAL)
ok("tope diario: lo enviado hace más de 24 horas ya no cuenta", r4["enviados"] == 2, r4)
ok("límites fuera de rango se rechazan (Gmail: 500 por día)",
   api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_tope_dia", "p_valor": 600}).execute().data == "valor_invalido"
   and api(AJ).rpc("notificaciones_configurar", {"p_clave": "correo_tope_dia", "p_valor": 10}).execute().data == "sin_permiso")
api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_tope_dia", "p_valor": 200}).execute()

# ---------- 8) errores y reintentos ----------
limpiar_cola()
mailpit_vaciar()
caido = correos.Config("127.0.0.1", 9, "", "", "avisos@justshowup.test", "ninguna", "s" * 40)  # nadie escucha
ids = [encolar(a)["id"] for a in ALUMNAS[:3]]
r = correos.despachar(admin, caido)
f0 = estado(ids[0])
ok("servidor de correo caído: el primero cuenta un intento con error y los demás vuelven a la cola sin contar",
   r == {"tomados": 3, "enviados": 0, "errores": 1, "liberados": 2} and f0["estado"] == "pendiente" and f0["intentos"] == 1
   and f0["ultimo_error"] and all(estado(i)["intentos"] == 0 and estado(i)["estado"] == "pendiente" for i in ids[1:]), r)
ok("el error guardado no tiene direcciones de correo", "@" not in f0["ultimo_error"], f0["ultimo_error"])
ok("después de un error espera ~2 minutos antes de reintentar",
   sql("select proximo_intento > now() + interval '90 seconds' p from notificacion_envio where id = %s", (ids[0],), True)["p"])
sql("update notificacion_envio set proximo_intento = now() + interval '1 hour' where id = any(%s)", (ids[1:],))
sql("update notificacion_envio set proximo_intento = now() where id = %s", (ids[0],))
sql("update notificacion_parametros set correo_max_intentos = 2")
correos.despachar(admin, caido, lote=1)
sql("update notificacion_envio set proximo_intento = now() where id = any(%s)", (ids[1:],))
ok("al llegar al máximo de intentos queda «fallido» y no se reintenta", estado(ids[0])["estado"] == "fallido"
   and estado(ids[0])["intentos"] == 2)
sql("update notificacion_parametros set correo_max_intentos = 5")
r = correos.despachar(admin, SMTP_LOCAL)
ok("cuando el servidor vuelve, salen los que estaban esperando", r["enviados"] == 2
   and len(mailpit_mensajes(esperar=3, cuantos=2)) == 2, r)


class SmtpRechaza:
    def __init__(self):
        self.n = 0

    def send_message(self, m):
        self.n += 1
        if self.n == 1:
            import smtplib
            raise smtplib.SMTPRecipientsRefused({m["To"]: (550, b"no existe")})

    def quit(self):
        pass


limpiar_cola()
ids = [encolar(a)["id"] for a in ALUMNAS[:3]]
r = correos.despachar(admin, SMTP_LOCAL, conectar=lambda c: SmtpRechaza())
ok("una dirección rechazada no frena a las demás", r == {"tomados": 3, "enviados": 2, "errores": 1, "liberados": 0}, r)


def reloj_que_se_pasa():
    t = iter([0, 0, 9, 9, 9])
    return lambda: next(t, 99)


limpiar_cola()
ids = [encolar(a)["id"] for a in ALUMNAS[:3]]
r = correos.despachar(admin, SMTP_LOCAL, reloj=reloj_que_se_pasa())
ok("si se acaba el tiempo del servidor (8 s), lo que falta vuelve a la cola sin contar intento",
   r["enviados"] == 1 and r["liberados"] == 2 and sum(estado(i)["intentos"] == 0 for i in ids) == 2, r)

# toma colgada: el servidor se cortó a mitad
limpiar_cola()
e = encolar(ALUMNAS[0])
sql("update notificacion_envio set estado = 'enviando', intentos = 1, reclamado_en = now() - interval '11 minutes' "
    "where id = %s", (e["id"],))
r = correos.despachar(admin, SMTP_LOCAL)
ok("una toma colgada más de 10 minutos vuelve a la cola y se manda", r["enviados"] == 1
   and estado(e["id"])["estado"] == "enviado" and estado(e["id"])["intentos"] == 2, r)
ok("anotar un resultado de algo que no se está enviando no hace nada",
   admin.rpc("notificaciones_resultado", {"p_id": e["id"], "p_resultado": "error", "p_error": "x"}).execute().data
   == "no_corresponde" and estado(e["id"])["estado"] == "enviado")

# ---------- 9) dos despachadores y dos tareas a la vez ----------
limpiar_cola()
ids = [encolar(ALUMNAS[i % 6])["id"] for i in range(20)]
tomas = {}


def tomar(nombre):
    c = como_servicio()
    tomas[nombre] = [f["id"] for f in sql("select notificaciones_reclamar(15) r", uno=True, conexion=c)["r"]]
    c.close()


hilos = [threading.Thread(target=tomar, args=(n,)) for n in ("a", "b", "c")]
[h.start() for h in hilos]
[h.join() for h in hilos]
todos = tomas["a"] + tomas["b"] + tomas["c"]
ok("tres despachadores a la vez: nunca toman el mismo aviso y entre los tres toman los 20",
   len(todos) == len(set(todos)) == 20 and set(todos) == set(ids), {k: len(v) for k, v in tomas.items()})
sql("update notificacion_envio set estado = 'pendiente', intentos = 0, reclamado_en = null where estado = 'enviando'")

c1, c2 = como_servicio(), __import__("psycopg2").connect(DB_URL)
c1.autocommit = False
with c1.cursor() as cur:
    cur.execute("reset role")
    cur.execute("select pg_advisory_xact_lock(hashtext('notificaciones_tick'))")
c2.autocommit = True
r = sql("select notificaciones_tick() t", uno=True, conexion=c2)["t"]
c1.rollback()
c1.close()
ok("dos tareas de cada minuto a la vez: la segunda no hace nada", r["resultado"] == "ocupado", r)
r = sql("select notificaciones_tick() t", uno=True, conexion=c2)["t"]
ok("sin otra corriendo, la tarea sigue; sin Vault configurado no llama a nadie",
   r["resultado"] == "ok" and r["despachador"] in ("sin_pg_net", "sin_configurar"), r)
c2.close()

# ---------- 10) seguridad del endpoint ----------
cliente = TestClient(main.app)
ok("endpoint sin configuración: 503 y no toma nada", cliente.post("/tareas/notificaciones").status_code == 503)
SECRETO = uuid.uuid4().hex + uuid.uuid4().hex
os.environ.update({"NOTIFICACIONES_SECRETO": SECRETO, "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "54325",
                   "SMTP_SEGURIDAD": "ninguna", "SMTP_REMITENTE": "avisos@justshowup.test"})
ok("endpoint sin secreto o con uno incorrecto: 401",
   cliente.post("/tareas/notificaciones").status_code == 401
   and cliente.post("/tareas/notificaciones", headers={"Authorization": "Bearer otro"}).status_code == 401
   and cliente.post("/tareas/notificaciones", headers={"Authorization": SECRETO}).status_code == 401)
ok("endpoint con GET: no existe (405)", cliente.get("/tareas/notificaciones").status_code == 405)
os.environ["SMTP_HOST"] = "smtp.ejemplo.test"
ok("sin cifrar contra un servidor que no es este equipo: se niega (503)",
   cliente.post("/tareas/notificaciones", headers={"Authorization": f"Bearer {SECRETO}"}).status_code == 503)
os.environ["SMTP_HOST"] = "127.0.0.1"
mailpit_vaciar()
r = cliente.post("/tareas/notificaciones", headers={"Authorization": f"Bearer {SECRETO}"})
ok("endpoint con el secreto correcto: despacha la cola a Mailpit", r.status_code == 200 and r.json()["enviados"] == 20
   and len(mailpit_mensajes(esperar=5, cuantos=20)) == 20, r.text[:200])
ok("el secreto no aparece en la respuesta ni en la configuración mostrada",
   SECRETO not in r.text and SECRETO not in repr(correos.configuracion()))

# ---------- 11) Vault ----------
ok("Vault: rechaza una dirección que no es la del despachador o un secreto corto",
   admin.rpc("notificaciones_guardar_secreto", {"p_nombre": "notificaciones_url", "p_valor": "https://evil.test/x"}).execute().data
   == "valor_invalido"
   and admin.rpc("notificaciones_guardar_secreto", {"p_nombre": "notificaciones_secreto", "p_valor": "corto"}).execute().data
   == "valor_invalido"
   and admin.rpc("notificaciones_guardar_secreto", {"p_nombre": "otro", "p_valor": "x" * 40}).execute().data == "valor_invalido")
PUERTO = 8799
ok("Vault: guarda la dirección del despachador y el secreto",
   admin.rpc("notificaciones_guardar_secreto", {"p_nombre": "notificaciones_url",
                                                "p_valor": f"http://host.docker.internal:{PUERTO}/tareas/notificaciones"}).execute().data == "ok"
   and admin.rpc("notificaciones_guardar_secreto", {"p_nombre": "notificaciones_secreto", "p_valor": SECRETO}).execute().data == "ok")
ok("Vault: el secreto queda cifrado (no está en texto plano en la tabla)",
   sql("select count(*) n from vault.secrets where secret = %s", (SECRETO,), True)["n"] == 0)

# ---------- 12) recorrido completo: pg_cron → pg_net → servidor → Mailpit ----------
entorno_srv = {**os.environ, "NOTIFICACIONES_SECRETO": SECRETO}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PUERTO)],
                       cwd=RAIZ, env=entorno_srv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(3)
    with open(os.path.join(RAIZ, "supabase", "pendientes", "notificaciones_cron_activar.sql")) as f:
        sql(f.read())
    trabajos = {t["jobname"]: t["schedule"] for t in sql("select jobname, schedule from cron.job")}
    ok("activar: quedan las 2 tareas (cada minuto y la limpieza diaria)",
       trabajos == {"jsu-notificaciones": "* * * * *", "jsu-limpieza-cron": "17 3 * * *"}, trabajos)
    limpiar_cola()
    mailpit_vaciar()
    ids = [encolar(a)["id"] for a in ALUMNAS[:2]]
    mensajes = mailpit_mensajes(esperar=150, cuantos=2)
    ok("sin que nadie abra la plataforma, pg_cron llama al servidor por pg_net y los correos llegan a Mailpit",
       len(mensajes) == 2 and all(estado(i)["estado"] == "enviado" for i in ids), [estado(i)["estado"] for i in ids])
    corridas = sql("select status from cron.job_run_details d join cron.job j on j.jobid = d.jobid "
                   "where j.jobname = 'jsu-notificaciones'")
    ok("el historial de pg_cron registra las corridas sin errores", corridas and all(c["status"] == "succeeded" for c in corridas),
       corridas[-3:])
    api(SA).rpc("notificaciones_correo_activar", {"p_activo": False}).execute()
    e = encolar(ALUMNAS[3])
    time.sleep(70)
    ok("interruptor apagado: la tarea sigue corriendo pero no se manda nada", estado(e["id"])["estado"] == "pendiente"
       and len(mailpit_mensajes()) == 2)
    with open(os.path.join(RAIZ, "supabase", "pendientes", "notificaciones_cron_desactivar.sql")) as f:
        sql(f.read())
    ok("desactivar: no queda ninguna tarea programada y el interruptor queda apagado",
       sql("select count(*) n from cron.job", uno=True)["n"] == 0
       and sql("select correo_activo from notificacion_parametros", uno=True)["correo_activo"] is False)
finally:
    srv.terminate()
    srv.wait(10)

terminar()
