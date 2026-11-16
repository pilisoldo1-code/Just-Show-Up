"""Notificaciones, fase C (los 9 correos automáticos) contra el Supabase LOCAL + Mailpit, con Just Show Up y Pilates
Centro (escenario real de `empresa_demo.py`). Solo datos ficticios; nunca producción ni correos reales.

Cada situación se arma con las funciones reales de la plataforma (reservas, cancelaciones, Planificación, lista de espera,
Facturación, pagos manuales y de Mercado Pago de PRUEBA simulados en la base local), después corre la tarea de cada minuto
(`notificaciones_tick()`) y el despachador manda a Mailpit. Comprueba qué correo recibe cada alumna, con qué contenido,
que nadie recibe uno que ya no corresponde, la preferencia, los cuatro roles, el aislamiento entre empresas, la hora de
envío de las facturas, el interruptor y que repetir la tarea no duplica nada.

Correr sobre una base recién reiniciada. Uso:  python tests/supabase_local/prueba_correos_avisos_reales.py
"""
import os
import sys
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notif_local import (SMTP_LOCAL, api, cuenta, entrar, entrar_superadmin, mailpit_cabeceras, mailpit_mensaje,  # noqa: E402
                         mailpit_mensajes, mailpit_vaciar, ok, sql, terminar)
from app import auth, clases, correos  # noqa: E402
from scripts import empresa_demo  # noqa: E402

ok("base recién reiniciada (sin cuentas)", sql("select count(*) n from auth.users", uno=True)["n"] == 0)
admin = auth.cliente_admin()
ahora = clases.ahora().replace(second=0, microsecond=0)
hoy = ahora.date()
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

# ---------- cuentas y escenario ----------
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADM = cuenta("profesora@prueba.invalid", "Paula", "admin", JSU)
PROF = cuenta("profe.jsu@prueba.invalid", "Pía", "profesora", JSU)
ANA = cuenta("ana.jsu@prueba.invalid", "Ana", "alumna", JSU)
BERTA = cuenta("berta.jsu@prueba.invalid", "Berta", "alumna", JSU)      # con la preferencia apagada
CLARA = cuenta("clara.jsu@prueba.invalid", "Clara", "alumna", JSU)
ALMA = cuenta("alumna@prueba.invalid", "Alma", "alumna", JSU)          # compartida con Pilates Centro
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]
CLAVE_SA = empresa_demo.crear_superadmin_local(empresa_demo.BaseSupabase())
creada = empresa_demo.crear(empresa_demo.BaseSupabase(), hoy, guardar_contrasenas=False)
PIL = sql("select id from empresa where codigo = 'PIL'", uno=True)["id"]
SA = entrar_superadmin("superadmin@prueba.invalid", CLAVE_SA)
AJ, S_ANA, S_BERTA, S_ALMA = (entrar("Just Show Up", m) for m in
                              ("profesora@prueba.invalid", "ana.jsu@prueba.invalid", "berta.jsu@prueba.invalid",
                               "alumna@prueba.invalid"))
ok("escenario: Just Show Up y Pilates Centro creados y todas inician sesión", PIL and all([SA, AJ, S_ANA, S_BERTA, S_ALMA]))
S_BERTA.post("/perfil/avisos", data={}, follow_redirects=False)
ok("Berta desactiva los avisos por correo en Mi perfil",
   sql("select recibir_correos r from profiles where id = %s", (BERTA,), True)["r"] is False)


def clase_j(fecha, hora="10:00", cupo=7, activo=True):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "(%s, %s, %s, %s, %s, %s) returning id", (DIAS[fecha.weekday()], hora, TIPO, activo, PROF, JSU), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
               "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, %s, %s, %s, %s) returning id",
               (h, fecha, hora, cupo, TIPO, JSU, PROF), True)["id"]


def reservar(alumna, id_clase, empresa=JSU, asistencia=None):
    r = sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (alumna, id_clase, empresa), True)["id"]
    sql("update clase set cantidad_inscriptas = cantidad_inscriptas + 1 where id = %s", (id_clase,))
    if asistencia:
        sql("insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, empresa))
    return r


def factura(alumna, mes, anio=2026):
    """Factura confirmada de la alumna para ese mes, con el mismo recorrido que la administradora en Facturación."""
    reservar(alumna, clase_j(date(anio, mes, 14), "07:00" if alumna == CLARA else "08:00", activo=False), asistencia="presente")
    AJ.get(f"/profesora/resumenes?mes={mes}&anio={anio}")
    f = sql("select id, version from resumen_mensual where id_alumna = %s and mes = %s and anio = %s and id_empresa = %s",
            (alumna, mes, anio, JSU), True)
    AJ.post(f"/profesora/resumenes/{f['id']}/confirmar", data={"version": f["version"]})
    return sql("select * from resumen_mensual where id = %s", (f["id"],), True)


def tick():
    return sql("select notificaciones_tick() t", uno=True)["t"]


def filas(tipo=None, usuario=None):
    q, a = "select * from notificacion_envio where true", []
    if tipo:
        q, a = q + " and tipo = %s", a + [tipo]
    if usuario:
        q, a = q + " and id_usuario = %s", a + [usuario]
    return sql(q + " order by id", a)


def fila(tipo, referencia):
    return sql("select * from notificacion_envio where tipo = %s and referencia = %s", (tipo, referencia), True)


def correos_por_envio():
    """{id de envío: {asunto, para, texto}} de todo lo que hay en Mailpit."""
    salida = {}
    for m in mailpit_mensajes():
        cab = mailpit_cabeceras(m["ID"])
        salida[int(cab["X-Jsu-Envio"][0])] = {"asunto": m["Subject"], "para": m["To"][0]["Address"],
                                              "texto": mailpit_mensaje(m["ID"])["Text"]}
    return salida


def correr():
    tick()
    return correos.despachar(admin, SMTP_LOCAL, lote=50)


# facturas (antes de encender: confirmadas ANTES de correo_desde)
F_JUL, F_AGO = factura(CLARA, 7), factura(CLARA, 8)
F_SEP_ANA = factura(ANA, 9)
F_AGO_ANA = factura(ANA, 8)
F_SEP_BERTA = factura(BERTA, 9)
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy - timedelta(days=20), F_JUL["id"]))
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy - timedelta(days=1), F_AGO["id"]))   # venció ayer
sql("update resumen_mensual set vence_el = %s where id in (%s, %s)", (hoy + timedelta(days=10), F_SEP_ANA["id"], F_AGO_ANA["id"]))
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy - timedelta(days=3), F_SEP_BERTA["id"]))
C_ANTES = clase_j(hoy + timedelta(days=4), "11:00")
R_ANTES = reservar(ANA, C_ANTES)  # reservada antes de encender: no se avisa

# ---------- 1) apagado y encendido ----------
ok("interruptor apagado: la tarea no encola nada", tick()["resultado"] == "apagado" and filas() == [])
sql("update notificacion_parametros set correo_permitir_ficticios = true, correo_tope_hora = 100")  # solo en local
api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_hora_envio", "p_valor": 23}).execute()
api(SA).rpc("notificaciones_correo_activar", {"p_activo": True}).execute()
r = tick()
ok("encendido antes de las 23: no se encola ningún aviso de facturas (hora de envío)",
   r["resultado"] == "ok" and not [f for f in filas() if f["tipo"].startswith("factura")] or ahora.hour >= 23, r)
ok("lo que pasó antes de encender (la reserva de Ana de antes) no se avisa", fila("reserva_confirmada", R_ANTES) is None)
api(SA).rpc("notificaciones_configurar", {"p_clave": "correo_hora_envio", "p_valor": 0}).execute()
sql("delete from notificacion_envio")
mailpit_vaciar()

# ---------- 2) situaciones después de encender ----------
# reservas: Ana reserva una clase en 3 días (aviso de reserva) y una que empieza en 30 minutos (recordatorio)
C_3D = clase_j(hoy + timedelta(days=3), "09:00")
R_3D = reservar(ANA, C_3D)
en30 = ahora + timedelta(minutes=30)
C_30 = clase_j(en30.date(), en30.strftime("%H:%M"))
R_30 = reservar(ANA, C_30)
en90 = ahora + timedelta(minutes=90)
C_90 = clase_j(en90.date(), en90.strftime("%H:%M"))
R_90 = reservar(CLARA, C_90)
# Ana reserva y cancela ella misma a tiempo: ni «reserva confirmada» ni «clase cancelada»
C_CANCELA = clase_j(hoy + timedelta(days=5), "12:00")
R_CANCELA = reservar(ANA, C_CANCELA)
api(S_ANA).rpc("cancelar_reserva", {"p_id_clase": C_CANCELA}).execute()
# Planificación cancela una clase con reservas de Clara (avisa) y Berta (preferencia apagada)
C_PLAN = clase_j(hoy + timedelta(days=2), "15:00")
R_PLAN_CLARA, R_PLAN_BERTA = reservar(CLARA, C_PLAN), reservar(BERTA, C_PLAN)
r = api(AJ).rpc("crear_bloqueos_puntuales", {"p_ids_clase": [C_PLAN], "p_motivo": "PRUEBA correos"}).execute().data
ok("Planificación cancela la clase de Clara y Berta",
   sql("select count(*) n from reserva where id_clase = %s and id_bloqueo is not null", (C_PLAN,), True)["n"] == 2, r)
# lista de espera: clase de mañana con cupo 1; Alma reservó, Ana espera; Alma cancela → se le ofrece a Ana
C_LE = clase_j(hoy + timedelta(days=1), "18:00", cupo=1)
R_LE_ALMA = reservar(ALMA, C_LE)
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, 1, 'en_espera', %s)",
    (ANA, C_LE, JSU))
api(S_ALMA).rpc("cancelar_reserva", {"p_id_clase": C_LE}).execute()
LE_ANA = sql("select * from listadeespera where id_alumna = %s and id_clase = %s", (ANA, C_LE), True)
ok("lista de espera: al cancelar Alma se le ofrece el lugar a Ana", LE_ANA["estado"] == "ofrecido")
# facturas: Clara julio vencida hace 20 días (ya le habían mandado el primer aviso hace 8), agosto vencida ayer;
# setiembre vence en 2 días; octubre se confirma ahora (nueva). Berta: vencida (pero sin correos).
F_SEP = factura(CLARA, 9)
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy + timedelta(days=2), F_SEP["id"]))
F_OCT = factura(CLARA, 10)
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy + timedelta(days=12), F_OCT["id"]))
sql("insert into notificacion_envio (id_empresa, id_usuario, tipo, referencia, estado, intentos, enviado_en, creado_en) "
    "values (%s, %s, 'factura_vencida', %s, 'enviado', 1, now() - interval '8 days', now() - interval '8 days')",
    (JSU, CLARA, F_JUL["id"]))
# pagos: manual de Ana (setiembre) y Mercado Pago de PRUEBA (agosto): primero rechazado, después aprobado
pago = api(AJ).rpc("registrar_pago", {"p_id_resumen": F_SEP_ANA["id"], "p_monto": str(F_SEP_ANA["total"]),
                                       "p_medio": "efectivo", "p_fecha": hoy.isoformat()}).execute().data
P_SEP = sql("select id from pago where id_resumen_mensual = %s and estado = 'vigente'", (F_SEP_ANA["id"],), True)["id"]
sql("update empresa set mp_habilitado = true where id = %s", (JSU,))  # solo en la base local
REF = "PRUEBA-" + uuid.uuid4().hex[:10]
sql("insert into mp_intento (id_resumen_mensual, id_alumna, referencia, monto, id_empresa) values (%s, %s, %s, %s, %s)",
    (F_AGO_ANA["id"], ANA, REF, F_AGO_ANA["total"], JSU))
rech = admin.rpc("registrar_pago_mercado_pago", {"p_referencia": REF, "p_id_pago_mp": "111", "p_estado": "rejected",
                                                 "p_monto": str(F_AGO_ANA["total"]), "p_moneda": "UYU",
                                                 "p_fecha": hoy.isoformat()}).execute().data
ok("Mercado Pago de prueba: pago rechazado (no crea pago)", rech["resultado"] == "rechazado"
   and sql("select count(*) n from pago where id_resumen_mensual = %s", (F_AGO_ANA["id"],), True)["n"] == 0, rech)
r1 = correr()
ok("pago rechazado: ningún correo de pago", not filas("pago_registrado") or
   all(f["referencia"] == P_SEP for f in filas("pago_registrado")), filas("pago_registrado"))
aprob = admin.rpc("registrar_pago_mercado_pago", {"p_referencia": REF, "p_id_pago_mp": "222", "p_estado": "approved",
                                                  "p_monto": str(F_AGO_ANA["total"]), "p_moneda": "UYU",
                                                  "p_fecha": hoy.isoformat()}).execute().data
ok("Mercado Pago de prueba: pago aprobado (crea el pago)", aprob["resultado"] == "ok", aprob)
P_MP = aprob["id_pago"]
# Pilates Centro: Alma reserva una clase futura de Pilates en la que no estaba
C_PIL = sql("select c.id, c.fecha, t.nombre from clase c join tipo_clase t on t.id = c.id_tipo_clase where c.id_empresa = %s "
            "and c.estado = 'programada' and c.fecha > %s and not exists (select 1 from reserva r where r.id_clase = c.id "
            "and r.id_alumna = %s) and c.cantidad_inscriptas < c.cupo_max order by c.fecha limit 1", (PIL, hoy + timedelta(days=1), ALMA), True)
R_PIL = reservar(ALMA, C_PIL["id"], empresa=PIL)
C_JSU_ALMA = clase_j(hoy + timedelta(days=6), "19:00")
R_JSU_ALMA = reservar(ALMA, C_JSU_ALMA)

r2 = correr()
r3 = correr()  # la segunda vuelta toma lo que generaron los pagos aprobados después
CORREOS = correos_por_envio()


def correo(tipo, referencia):
    f = fila(tipo, referencia)
    return CORREOS.get(f["id"]) if f else None


# ---------- 3) cada correo ----------
c = correo("reserva_confirmada", R_3D)
ok("1) reserva confirmada: Ana recibe el correo con la clase", c and c["para"] == "ana.jsu@prueba.invalid"
   and c["asunto"] == "Just Show Up: Reserva confirmada" and "Funcional" in c["texto"], c)
c = correo("recordatorio_clase", R_30)
ok("2) recordatorio: Ana recibe «Tu clase empieza a las HH:MM» de la clase de dentro de 30 minutos",
   c and c["asunto"] == f"Just Show Up: Tu clase empieza a las {en30.strftime('%H:%M')}", c)
ok("2) la reserva que empieza en 30 minutos no recibe además «reserva confirmada»", fila("reserva_confirmada", R_30) is None)
ok("2) la clase que empieza en hora y media todavía no tiene recordatorio (sí reserva confirmada)",
   fila("recordatorio_clase", R_90) is None and correo("reserva_confirmada", R_90))
ok("3) cancelación de la alumna: ni reserva confirmada (se descartó) ni clase cancelada",
   (fila("reserva_confirmada", R_CANCELA) is None or fila("reserva_confirmada", R_CANCELA)["estado"] == "descartado")
   and fila("clase_cancelada", R_CANCELA) is None and correo("reserva_confirmada", R_CANCELA) is None)
c = correo("clase_cancelada", R_PLAN_CLARA)
ok("4) clase cancelada por la profesora: Clara recibe el aviso", c and "Se canceló una clase" in c["asunto"]
   and c["para"] == "clara.jsu@prueba.invalid", c)
ok("4) Berta (preferencia apagada) no recibe nada: queda «omitido»", fila("clase_cancelada", R_PLAN_BERTA)["estado"] == "omitido"
   and not any(x["para"] == "berta.jsu@prueba.invalid" for x in CORREOS.values()))
c = correo("lugar_disponible", LE_ANA["id"])
ok("5) lista de espera: Ana recibe «¡Se liberó un lugar para vos!» con la hora límite", c and "Se liberó un lugar" in c["asunto"]
   and "/mis-inscripciones" in c["texto"], c)
ok("5) Alma (que canceló) no recibe aviso de la reserva que ya no tiene", correo("reserva_confirmada", R_LE_ALMA) is None)
ok("5) anotarse en la lista de espera no manda «reserva confirmada»",
   not [f for f in filas("reserva_confirmada", ANA) if f["referencia"] not in (R_3D,)])
c = correo("factura_nueva", F_OCT["id"])
ok("6) factura nueva: Clara recibe la de octubre (confirmada después de encender)", c and "octubre 2026" in c["asunto"]
   and "Ya está disponible" in c["asunto"], c)
ok("6) las facturas confirmadas antes de encender no se avisan como «nuevas»",
   fila("factura_nueva", F_AGO["id"]) is None and fila("factura_nueva", F_JUL["id"]) is None)
c = correo("factura_proxima", F_SEP["id"])
ok("7) factura próxima a vencer: Clara recibe la de setiembre (vence en 2 días)", c and "vence el" in c["asunto"], c)
c = correo("factura_vencida", F_AGO["id"])
ok("8) factura vencida: Clara recibe la de agosto (venció ayer)", c and "está vencida" in c["asunto"], c)
c = correo("factura_vencida_2", F_JUL["id"])
ok("9) segundo aviso de vencida: Clara lo recibe 8 días después del primero", c and "sigue pendiente" in c["asunto"], c)
ok("9) la de agosto (primer aviso hoy) todavía no tiene segundo aviso", fila("factura_vencida_2", F_AGO["id"]) is None)
ok("8) Berta: su factura vencida queda omitida por su preferencia", fila("factura_vencida", F_SEP_BERTA["id"])["estado"] == "omitido")
c = correo("pago_registrado", P_SEP)
ok("10) pago manual: Ana recibe «Registramos el pago» (efectivo)", c and "Registramos el pago" in c["asunto"]
   and "efectivo" in c["texto"], c)
c = correo("pago_registrado", P_MP)
ok("10) pago de Mercado Pago de prueba aprobado: Ana recibe el correo con «Mercado Pago»", c and "Mercado Pago" in c["texto"], c)
ok("10) la factura pagada ya no recibe avisos de factura", fila("factura_nueva", F_SEP_ANA["id"]) is None
   and fila("factura_proxima", F_SEP_ANA["id"]) is None)

# ---------- 4) aislamiento y roles ----------
c_pil, c_jsu = correo("reserva_confirmada", R_PIL), correo("reserva_confirmada", R_JSU_ALMA)
ok("aislamiento: la reserva de Pilates Centro llega como «Pilates Centro» con su clase y nada de Just Show Up",
   c_pil and c_pil["asunto"].startswith("Pilates Centro: ") and C_PIL["nombre"] in c_pil["texto"]
   and "Funcional" not in c_pil["texto"] and "Just Show Up:" not in c_pil["asunto"], c_pil)
ok("aislamiento: la de Just Show Up llega como «Just Show Up» y nada de Pilates",
   c_jsu and c_jsu["asunto"].startswith("Just Show Up: ") and "Pilates" not in c_jsu["texto"], c_jsu)
ok("aislamiento: cada aviso queda en la empresa de su dato",
   fila("reserva_confirmada", R_PIL)["id_empresa"] == PIL and fila("reserva_confirmada", R_JSU_ALMA)["id_empresa"] == JSU)
docentes = ("profesora@prueba.invalid", "profe.jsu@prueba.invalid", "superadmin@prueba.invalid",
            empresa_demo.ADMIN[0], empresa_demo.PROFESORA[0])
ok("cuatro roles: administradoras, profesoras y la superadministradora no reciben correos de alumna",
   not any(x["para"] in docentes for x in CORREOS.values())
   and sql("select count(*) n from notificacion_envio n join profiles p on p.id = n.id_usuario where p.mail = any(%s)",
           (list(docentes),), True)["n"] == 0)
ok("cada correo enviado es de un aviso «enviado», y viceversa (salvo el primer aviso simulado de hace 8 días)",
   set(CORREOS) == {f["id"] for f in filas() if f["estado"] == "enviado"} - {fila("factura_vencida", F_JUL["id"])["id"]})

# ---------- 5) duplicados ----------
antes = len(filas())
for _ in range(3):
    correr()
ok("repetir la tarea y el despacho 3 veces no encola ni manda nada nuevo",
   len(filas()) == antes and len(mailpit_mensajes()) == len(CORREOS))

# ---------- 6) lo que deja de corresponder antes de mandarse ----------
C_X = clase_j(hoy + timedelta(days=8), "16:00")
R_X = reservar(CLARA, C_X)
tick()  # encola «reserva confirmada» (todavía sin mandar: el despachador no corrió)
api(entrar("Just Show Up", "clara.jsu@prueba.invalid")).rpc("cancelar_reserva", {"p_id_clase": C_X}).execute()
correos.despachar(admin, SMTP_LOCAL)
ok("reserva cancelada antes de mandarse el aviso: se descarta y no llega",
   fila("reserva_confirmada", R_X)["estado"] == "descartado" and correo("reserva_confirmada", R_X) is None)
F_NOV_PAGO = sql("select * from resumen_mensual where id = %s", (F_OCT["id"],), True)
sql("delete from notificacion_envio where tipo = 'factura_proxima' and referencia = %s", (F_OCT["id"],))
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy + timedelta(days=1), F_OCT["id"]))
tick()
api(AJ).rpc("registrar_pago", {"p_id_resumen": F_OCT["id"], "p_monto": str(F_OCT["total"]), "p_medio": "transferencia",
                               "p_fecha": hoy.isoformat()}).execute()
correos.despachar(admin, SMTP_LOCAL)
ok("factura pagada antes de mandarse «próxima a vencer»: se descarta",
   fila("factura_proxima", F_OCT["id"]) and fila("factura_proxima", F_OCT["id"])["estado"] == "descartado")
P_OCT = sql("select id from pago where id_resumen_mensual = %s and estado = 'vigente'", (F_OCT["id"],), True)["id"]
tick()
api(AJ).rpc("anular_pago", {"p_id_pago": P_OCT, "p_motivo": "PRUEBA anulado antes del correo"}).execute()
correos.despachar(admin, SMTP_LOCAL)
ok("pago anulado antes de mandarse «pago registrado»: se descarta",
   fila("pago_registrado", P_OCT)["estado"] == "descartado" and correo("pago_registrado", P_OCT) is None)

# ---------- 7) preferencia que se apaga con avisos en cola, y el interruptor ----------
C_Y = clase_j(hoy + timedelta(days=9), "16:00")
R_Y = reservar(ANA, C_Y)
tick()
S_ANA.post("/perfil/avisos", data={}, follow_redirects=False)
correos.despachar(admin, SMTP_LOCAL)
ok("Ana apaga los correos con un aviso en cola: no le llega", fila("reserva_confirmada", R_Y)["estado"] == "omitido")
S_ANA.post("/perfil/avisos", data={"recibir": "1"}, follow_redirects=False)
ok("con la preferencia apagada, los avisos se siguen viendo en la plataforma (Novedades)",
   S_BERTA.get("/novedades").status_code == 200 and "cancel" in S_BERTA.get("/novedades").text.lower())
api(SA).rpc("notificaciones_correo_activar", {"p_activo": False}).execute()
C_Z = clase_j(hoy + timedelta(days=10), "16:00")
R_Z = reservar(ANA, C_Z)
correr()
ok("interruptor apagado: no se encola ni se manda nada", fila("reserva_confirmada", R_Z) is None
   and tick()["resultado"] == "apagado")
api(SA).rpc("notificaciones_correo_activar", {"p_activo": True}).execute()
correr()
ok("al volver a encender, se avisa lo reciente que sigue correspondiendo", fila("reserva_confirmada", R_Z)
   and fila("reserva_confirmada", R_Z)["estado"] == "enviado")

ok("ningún aviso quedó fallido ni colgado", not [f for f in filas() if f["estado"] in ("fallido", "enviando")])
print("      resumen:", admin.rpc("notificaciones_resumen", {}).execute().data)
terminar()
