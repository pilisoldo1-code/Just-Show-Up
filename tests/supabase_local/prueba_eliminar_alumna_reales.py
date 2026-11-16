"""«Eliminar alumna» y «Dejar inactiva» contra el Supabase LOCAL + Mailpit, con la app completa por HTTP, Supabase Auth
real, RLS, la lista de espera automática y las notificaciones encendidas. Solo datos ficticios; nunca producción.

Comprueba:
- Dejar inactiva / reactivar: misma cuenta, mismo historial (sin cambios respecto de antes).
- Eliminar: vista previa, cancela solo las reservas futuras (sin cobro), el lugar pasa a la siguiente de la lista de
  espera (y le llega el correo «¡Se liberó un lugar!»), sale de las listas de espera, desaparece del listado, su cuenta
  queda archivada (correo interno .invalid, bloqueada, no puede entrar) y su historial (reservas pasadas, asistencias,
  facturas, pagos, avisos) queda intacto y ligado a la cuenta archivada. Archivarla no manda ningún correo.
- Volver a crearla con el MISMO correo: cuenta NUEVA (otro id), llega la invitación ORIGINAL, configura su contraseña,
  entra con el historial vacío, reserva y le llega «Reserva confirmada».
- Si el archivado en Auth falla: la eliminación no se presenta como terminada, el alta con ese correo se frena y
  «Reintentar» la completa.
- Alumna de dos empresas: no se puede eliminar; la otra empresa no cambia. Una profesora común no puede eliminar.

Correr sobre una base recién reiniciada (con smtp_port = 54325 en [local_smtp]).
Uso:  python tests/supabase_local/prueba_eliminar_alumna_reales.py
"""
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notif_local import (CLAVE, SMTP_LOCAL, api, cuenta, entrar, entrar_superadmin, estado, mailpit_mensajes,  # noqa: E402
                         mailpit_vaciar, ok, sql, terminar)
from _correo_local import SITIO, activar_por_correo  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, clases, correos, main  # noqa: E402
from scripts import empresa_demo  # noqa: E402

ok("base recién reiniciada (sin cuentas)", sql("select count(*) n from auth.users", uno=True)["n"] == 0)
admin_api = auth.cliente_admin()
ahora = clases.ahora().replace(second=0, microsecond=0)
hoy = ahora.date()
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]

# ---------- escenario ----------
ADM = cuenta("profesora@prueba.invalid", "Laura", "admin", JSU)
PROF = cuenta("profe.jsu@prueba.invalid", "Pía", "profesora", JSU)
BERTA = cuenta("berta.jsu@prueba.invalid", "Berta", "alumna", JSU)
CLARA = cuenta("clara.jsu@prueba.invalid", "Clara", "alumna", JSU)
COMPARTIDA = cuenta("alumna@prueba.invalid", "Alma", "alumna", JSU)
TIPO = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
           "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]
CLAVE_SA = empresa_demo.crear_superadmin_local(empresa_demo.BaseSupabase())
empresa_demo.crear(empresa_demo.BaseSupabase(), hoy, guardar_contrasenas=False)  # la compartida queda en dos empresas

AJ = TestClient(main.app, base_url=SITIO)
r = AJ.post("/login", data={"lugar": "Just Show Up", "mail": "profesora@prueba.invalid", "contrasena": CLAVE},
            follow_redirects=False)
ok("la administradora inicia sesión", r.status_code == 303 and "login" not in r.headers.get("location", ""))

# Ana: alumna con correo real (no .invalid), creada por invitación como en producción
MAIL_ANA = f"ana.{uuid.uuid4().hex[:6]}@ejemplo.test"
CLAVE_ANA = "ClaveAna-" + uuid.uuid4().hex[:8]
mailpit_vaciar()
r = AJ.post("/profesora/alumnas", data={"nombre": "Ana", "apellido": "Pérez", "mail": MAIL_ANA, "rol": "alumna",
                                        "cedula": "1.234.567-2"}, follow_redirects=False)
activar_por_correo(estado, main.app, MAIL_ANA, CLAVE_ANA)
ANA = sql("select id::text from profiles where mail = %s", (MAIL_ANA,), True)["id"]
ok("Ana se crea por invitación y activa su cuenta", ANA and entrar("Just Show Up", MAIL_ANA, CLAVE_ANA) is not None)


def clase_j(fecha, hora, cupo=7, activo=False):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "(%s, %s, %s, %s, %s, %s) returning id", (DIAS[fecha.weekday()], hora, TIPO, activo, PROF, JSU), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
               "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, %s, %s, %s, %s) returning id",
               (h, fecha, hora, cupo, TIPO, JSU, PROF), True)["id"]


def reservar(alumna, id_clase, asistencia=None):
    r = sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (alumna, id_clase, JSU), True)["id"]
    sql("update clase set cantidad_inscriptas = (select count(*) from reserva where id_clase = %s and estado = 'confirmada') "
        "where id = %s", (id_clase, id_clase))
    if asistencia:
        sql("insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, JSU))
    return r


def factura(alumna, mes):
    reservar(alumna, clase_j(date(2026, mes, 14), "07:00"), "presente")
    AJ.get(f"/profesora/resumenes?mes={mes}&anio=2026")
    f = sql("select id, version from resumen_mensual where id_alumna = %s and mes = %s", (alumna, mes), True)
    AJ.post(f"/profesora/resumenes/{f['id']}/confirmar", data={"version": f["version"]})
    return sql("select * from resumen_mensual where id = %s", (f["id"],), True)


# historial de Ana: agosto pagada, setiembre confirmada sin pagar, una clase pasada con ausencia
F_AGO, F_SET = factura(ANA, 8), factura(ANA, 9)
api(AJ).rpc("registrar_pago", {"p_id_resumen": F_AGO["id"], "p_monto": str(F_AGO["total"]), "p_medio": "efectivo",
                               "p_fecha": hoy.isoformat()}).execute()
R_PASADA = reservar(ANA, clase_j(hoy - timedelta(days=2), "10:00"), "ausente")
# futuras: una normal y una clase de cupo 1 con Berta esperando; y Ana esperando en una clase llena de Clara
R_FUT = reservar(ANA, clase_j(hoy + timedelta(days=3), "11:00"))
C_LLENA = clase_j(hoy + timedelta(days=2), "12:00", cupo=1, activo=True)
R_LLENA = reservar(ANA, C_LLENA)
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, 1, 'en_espera', %s)",
    (BERTA, C_LLENA, JSU))
C_CLARA = clase_j(hoy + timedelta(days=4), "13:00", cupo=1)
reservar(CLARA, C_CLARA)
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, 1, 'en_espera', %s)",
    (ANA, C_CLARA, JSU))

# notificaciones y lista automática encendidas (como en producción), con Mailpit
sql("update notificacion_parametros set correo_activo = true, correo_desde = now() - interval '1 minute', "
    "lista_espera_automatica = true, correo_permitir_ficticios = true, correo_hora_envio = 0")
sql("select notificaciones_tick()")
correos.despachar(admin_api, SMTP_LOCAL, lote=50)


def huella_historial(usuario):
    return json.dumps(sql(
        "select (select json_agg(r order by r.id) from (select id, id_clase, estado, fecha_reserva, fecha_cancelacion "
        "         from reserva where id_alumna = %(u)s and id_clase not in (select id from clase where fecha >= %(h)s)) r) reservas_pasadas, "
        "       (select json_agg(a order by a.id) from (select a.* from asistencia a join reserva r on r.id = a.id_reserva "
        "         where r.id_alumna = %(u)s) a) asistencias, "
        "       (select json_agg(f order by f.id) from (select * from resumen_mensual where id_alumna = %(u)s) f) facturas, "
        "       (select json_agg(d order by d.id) from (select d.* from resumen_mensual_detalle d join resumen_mensual f "
        "         on f.id = d.id_resumen_mensual where f.id_alumna = %(u)s) d) lineas, "
        "       (select json_agg(p order by p.id) from (select p.* from pago p join resumen_mensual f on f.id = p.id_resumen_mensual "
        "         where f.id_alumna = %(u)s) p) pagos, "
        "       (select json_agg(n order by n.id) from (select id, tipo, referencia, estado from notificacion_envio "
        "         where id_usuario = %(u)s and estado = 'enviado') n) avisos_enviados",
        {"u": usuario, "h": hoy}, True), default=str, sort_keys=True)


# ---------- 1) dejar inactiva y reactivar (sin cambios) ----------
antes_clara = huella_historial(CLARA)
AJ.post(f"/profesora/alumnas/{CLARA}/estado", data={"activa": "0"})
ok("dejar inactiva: Clara no puede entrar y sigue en el listado como inactiva",
   entrar("Just Show Up", "clara.jsu@prueba.invalid") is None and "Clara" in AJ.get("/profesora/alumnas").text)
AJ.post(f"/profesora/alumnas/{CLARA}/estado", data={"activa": "1"})
ok("reactivar: misma cuenta, mismo correo, mismo historial, vuelve a entrar",
   entrar("Just Show Up", "clara.jsu@prueba.invalid") is not None and huella_historial(CLARA) == antes_clara
   and sql("select mail from profiles where id = %s", (CLARA,), True)["mail"] == "clara.jsu@prueba.invalid")

# ---------- 2) permisos y dos empresas ----------
PIA = entrar("Just Show Up", "profe.jsu@prueba.invalid")
r = PIA.post(f"/profesora/alumnas/{ANA}/eliminar", data={"motivo": "x", "firma": "x"}, follow_redirects=False)
ok("una profesora común no puede eliminar", r.status_code in (303, 403)
   and sql("select eliminada_en from membresia where id_usuario = %s and id_empresa = %s", (ANA, JSU), True)["eliminada_en"] is None)
ok("una profesora común tampoco puede llamar a la función directamente",
   api(PIA).rpc("eliminar_alumna", {"p_id_usuario": ANA, "p_motivo": "x", "p_firma": "x"}).execute().data["resultado"]
   == "no_autorizada")
r = AJ.get(f"/profesora/alumnas/{COMPARTIDA}/eliminar", follow_redirects=False)
ok("alumna de dos empresas: no se puede eliminar (se ofrece dejarla inactiva)", r.status_code == 303
   and "otro+lugar" in r.headers["location"]
   and sql("select count(*) n from membresia where id_usuario = %s and eliminada_en is null", (COMPARTIDA,), True)["n"] == 2)

# ---------- 3) eliminar a Ana ----------
antes_ana = huella_historial(ANA)
r = AJ.get(f"/profesora/alumnas/{ANA}/eliminar")
firma = re.search(r'name="firma" value="([0-9a-f]+)"', r.text).group(1)
ok("vista previa: 2 reservas a cancelar, 1 lista de espera, 1 factura sin pagar y el historial que se conserva",
   r.status_code == 200 and ">2</dd>" in r.text and "JSU-2026-09-" in r.text and "Ana Pérez" in r.text, r.status_code)
ok("vista previa: no cambia nada", sql("select count(*) n from reserva where id_alumna = %s and estado = 'confirmada'",
                                       (ANA,), True)["n"] == 5)
mailpit_vaciar()
r = AJ.post(f"/profesora/alumnas/{ANA}/eliminar", data={"motivo": "PRUEBA eliminar", "firma": firma}, follow_redirects=False)
ok("eliminar: termina bien y lo dice", r.status_code == 303 and "ok=1" in r.headers["location"], r.headers.get("location"))
m = sql("select activa, eliminada_en, cedula from membresia where id_usuario = %s and id_empresa = %s", (ANA, JSU), True)
ok("membresía: inactiva, eliminada y sin cédula (la cédula pasó a la auditoría)",
   m["activa"] is False and m["eliminada_en"] and m["cedula"] is None)
aud = sql("select * from alumna_eliminada where id_usuario = %s", (ANA,), True)
ok("auditoría: correo original, cédula, quién, motivo y correo liberado",
   aud and aud["mail_original"] == MAIL_ANA and aud["cedula"] == "12345672" and aud["por"] is not None
   and aud["motivo"] == "PRUEBA eliminar" and aud["correo_liberado_en"] is not None and aud["reservas_canceladas"] == 2
   and aud["esperas_retiradas"] == 1 and aud["facturas_pendientes"] == 1, aud)
ok("reservas futuras canceladas (sin bloqueo: no se cobran ni generan aviso de clase cancelada)",
   sql("select count(*) n from reserva where id in (%s, %s) and estado = 'cancelada' and id_bloqueo is null",
       (R_FUT, R_LLENA), True)["n"] == 2)
ok("la reserva pasada sigue confirmada (se cobra como siempre)",
   sql("select estado from reserva where id = %s", (R_PASADA,), True)["estado"] == "confirmada")
ok("lista de espera: Ana sale de la de Clara («retirado»)",
   sql("select estado from listadeespera where id_alumna = %s and id_clase = %s", (ANA, C_CLARA), True)["estado"] == "retirado")
ok("lista de espera: el lugar liberado se le ofrece a Berta, en orden",
   sql("select estado from listadeespera where id_alumna = %s and id_clase = %s", (BERTA, C_LLENA), True)["estado"]
   == "ofrecido")
ok("cupos al día (sin sobrecupo)", sql("select cantidad_inscriptas n from clase where id = %s", (C_LLENA,), True)["n"] == 0)
ok("historial de la cuenta archivada idéntico (reservas pasadas, asistencias, facturas, líneas, pagos y avisos)",
   huella_historial(ANA) == antes_ana)
u = sql("select email, banned_until > now() + interval '50 years' bloqueada from auth.users where id = %s", (ANA,), True)
ok("Supabase Auth: la cuenta archivada tiene un correo interno .invalid y queda bloqueada",
   u["email"] == auth.mail_archivado(ANA) and u["bloqueada"], u)
ok("perfil: el correo se actualizó solo (trigger) y el nombre se conserva",
   sql("select mail, nombre from profiles where id = %s", (ANA,), True) == {"mail": auth.mail_archivado(ANA), "nombre": "Ana"})
ok("la cuenta archivada no puede iniciar sesión (ni con el correo viejo ni con el interno)",
   entrar("Just Show Up", MAIL_ANA, CLAVE_ANA) is None and entrar("Just Show Up", auth.mail_archivado(ANA), CLAVE_ANA) is None)
lista = AJ.get("/profesora/alumnas").text
ok("desaparece del listado (ni activas ni inactivas) y no tiene ficha",
   "Pérez" not in lista and AJ.get(f"/profesora/alumnas/{ANA}", follow_redirects=False).status_code == 303)
ok("archivar no mandó ningún correo a su dirección", not [x for x in mailpit_mensajes() if x["To"][0]["Address"] == MAIL_ANA])

# notificaciones: siguen funcionando igual
sql("select notificaciones_tick()")
correos.despachar(admin_api, SMTP_LOCAL, lote=50)
para = {x["To"][0]["Address"]: x["Subject"] for x in mailpit_mensajes(esperar=5, cuantos=1)}
ok("notificaciones: a Berta le llega «¡Se liberó un lugar para vos!»", "Se liberó un lugar" in para.get("berta.jsu@prueba.invalid", ""),
   para)
ok("notificaciones: a la cuenta archivada no le llega nada", not any(a.endswith("cuentas-eliminadas.invalid") or a == MAIL_ANA
                                                                   for a in para))

# ---------- 4) volver a crearla con el MISMO correo ----------
mailpit_vaciar()
r = AJ.post("/profesora/alumnas", data={"nombre": "Ana", "apellido": "Pérez", "mail": MAIL_ANA, "rol": "alumna",
                                        "cedula": "1.234.567-2"}, follow_redirects=False)
ok("alta con el mismo correo y la misma cédula: se acepta y dice que se envió la invitación",
   r.status_code == 303 and "invitaci" in r.headers["location"], r.headers.get("location"))
inv = [x for x in mailpit_mensajes(esperar=10, cuantos=1) if x["To"][0]["Address"] == MAIL_ANA]
ok("llega la invitación ORIGINAL de Just Show Up (no un correo de recuperación)",
   len(inv) == 1 and inv[0]["Subject"] == "Te invitaron a Just Show Up", [x["Subject"] for x in inv])
NUEVA = sql("select id::text from profiles where mail = %s", (MAIL_ANA,), True)["id"]
ok("es una cuenta NUEVA (otro identificador)", NUEVA and NUEVA != ANA)
CLAVE_NUEVA = "ClaveNueva-" + uuid.uuid4().hex[:8]
activar_por_correo(estado, main.app, MAIL_ANA, CLAVE_NUEVA)
ANA2 = entrar("Just Show Up", MAIL_ANA, CLAVE_NUEVA)
ok("configura su contraseña con la invitación y entra por primera vez", ANA2 is not None)
ok("historial vacío: sin reservas, facturas ni pagos de la cuenta anterior",
   sql("select (select count(*) from reserva where id_alumna = %(u)s) + (select count(*) from resumen_mensual where id_alumna = %(u)s) n",
       {"u": NUEVA}, True)["n"] == 0
   and "JSU-2026-09" not in ANA2.get("/mis-facturas").text and "Ausente" not in ANA2.get("/mi-historial").text)
ok("la cuenta anterior sigue archivada con su historial", huella_historial(ANA) == antes_ana)
C_NUEVA = clase_j(hoy + timedelta(days=5), "14:00", activo=True)
res = api(ANA2).rpc("reservar", {"p_id_clase": C_NUEVA}).execute().data
mailpit_vaciar()
sql("select notificaciones_tick()")
correos.despachar(admin_api, SMTP_LOCAL, lote=50)
conf = [x for x in mailpit_mensajes(esperar=5, cuantos=1) if x["To"][0]["Address"] == MAIL_ANA]
ok("reserva como alumna nueva y le llega «Reserva confirmada»", res in ("ok", {"resultado": "ok"}) or
   sql("select count(*) n from reserva where id_alumna = %s and estado = 'confirmada'", (NUEVA,), True)["n"] == 1)
ok("el correo de confirmación llega a la cuenta nueva", conf and "Reserva confirmada" in conf[0]["Subject"],
   [x["Subject"] for x in conf])

# ---------- 5) si el archivado en Auth falla ----------
MAIL_DORA = f"dora.{uuid.uuid4().hex[:6]}@ejemplo.test"
AJ.post("/profesora/alumnas", data={"nombre": "Dora", "apellido": "Ruiz", "mail": MAIL_DORA, "rol": "alumna"})
activar_por_correo(estado, main.app, MAIL_DORA, "ClaveDora-12345")
DORA = sql("select id::text from profiles where mail = %s", (MAIL_DORA,), True)["id"]
firma = re.search(r'name="firma" value="([0-9a-f]+)"', AJ.get(f"/profesora/alumnas/{DORA}/eliminar").text).group(1)
with mock.patch.object(auth, "archivar_cuenta", side_effect=auth.ErrorAuth("Auth no responde")):
    r = AJ.post(f"/profesora/alumnas/{DORA}/eliminar", data={"motivo": "PRUEBA falla", "firma": firma}, follow_redirects=False)
ok("si falla Auth: el aviso dice que NO está terminada", "ok=0" in r.headers["location"]
   and "NO+est%C3%A1+terminada" in r.headers["location"])
ok("si falla Auth: el listado muestra «Eliminaciones sin terminar» con «Reintentar»",
   "Eliminaciones sin terminar" in AJ.get("/profesora/alumnas").text)
r = AJ.post("/profesora/alumnas", data={"nombre": "Dora", "apellido": "Ruiz", "mail": MAIL_DORA, "rol": "alumna"},
            follow_redirects=False)
ok("si falla Auth: crearla de nuevo con ese correo se frena (no reactiva la cuenta anterior)",
   r.status_code == 400 and "todavía no quedó libre" in r.text
   and sql("select activa from membresia where id_usuario = %s and id_empresa = %s", (DORA, JSU), True)["activa"] is False)
reg = sql("select id from alumna_eliminada where id_usuario = %s", (DORA,), True)["id"]
r = AJ.post(f"/profesora/alumnas/eliminadas/{reg}/reintentar", follow_redirects=False)
ok("«Reintentar» completa el archivado y libera el correo", "ok=1" in r.headers["location"]
   and sql("select correo_liberado_en is not null x from alumna_eliminada where id = %s", (reg,), True)["x"]
   and "Eliminaciones sin terminar" not in AJ.get("/profesora/alumnas").text)
mailpit_vaciar()
r = AJ.post("/profesora/alumnas", data={"nombre": "Dora", "apellido": "Ruiz", "mail": MAIL_DORA, "rol": "alumna"},
            follow_redirects=False)
ok("después del reintento, el alta crea una cuenta nueva con la invitación original",
   [x["Subject"] for x in mailpit_mensajes(esperar=10, cuantos=1) if x["To"][0]["Address"] == MAIL_DORA]
   == ["Te invitaron a Just Show Up"]
   and sql("select id::text from profiles where mail = %s", (MAIL_DORA,), True)["id"] != DORA)

# ---------- 6) la otra empresa no cambió ----------
ok("Pilates Centro: la alumna compartida sigue activa y entra",
   entrar("Pilates Centro", "alumna@prueba.invalid") is not None)
terminar()
