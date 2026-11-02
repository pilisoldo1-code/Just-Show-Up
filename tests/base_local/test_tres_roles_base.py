"""Tres roles por empresa (migración 20261101000000_tres_roles), en un Postgres LOCAL descartable que reconstruye
producción con una COPIA de sus datos (respaldo fuera del repo) y todas las migraciones anteriores. Comprueba:
la adaptación de la administradora actual, que la vuelta atrás deja estructura y datos idénticos, la regla de un
solo rol, los permisos docentes de la administradora (con y sin clases), que una administradora común no toque a
otra administradora, la gestión exclusiva de la superadministradora (sin dejar una empresa sin administradora,
también con pedidos simultáneos), los cambios alumna ↔ profesora, la conservación del historial, el aislamiento
entre empresas y la compatibilidad con el código actual de theta. No toca Supabase.

Uso:  python tests/base_local/test_tres_roles_base.py "<carpeta del respaldo antes de multiempresa>"
"""
import os
import sys
import threading
import uuid
from datetime import timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from clientefalso import ClienteFalso  # noqa: E402

fallas = total_casos = 0
RESPALDO = sys.argv[1]


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


def sql_error(sentencia, args=None):
    try:
        q(su, sentencia, args)
        return None
    except psycopg2.Error as e:
        su.rollback()
        return e.pgcode


def huellas_estructura():
    sql = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
    return {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, sql)}


def tablas():
    return [r["table_name"] for r in q(su, "select table_name from information_schema.tables "
                                           "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]


def datos():
    # sesion_empresa: cada ClienteFalso de la prueba abre una sesión (no es un dato que toque la migración)
    return {t: localdb.huella(su, t) for t in tablas() if t != "sesion_empresa"}


def roles(uid, empresa):
    r = q(su, "select es_admin, es_profesora, es_alumna, activa from membresia where id_usuario = %s and id_empresa = %s",
          (uid, empresa), True)
    return (r["es_admin"], r["es_profesora"], r["es_alumna"], r["activa"]) if r else None


srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
MIG = [a for a in localdb.migraciones() if a.endswith("_tres_roles.sql")][0]
M1 = [a for a in localdb.migraciones() if a.endswith("_multiempresa_1_estructura.sql")][0]
VUELTA = os.path.join(localdb.REPO, "supabase/pendientes/vuelta_atras_tres_roles.sql")
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(M1):
            cur.execute(open(a).read())
copia_produccion.cargar(su, RESPALDO)
with su.cursor() as cur:
    for a in localdb.migraciones():
        if os.path.basename(M1) <= os.path.basename(a) < os.path.basename(MIG):
            cur.execute(open(a).read())


def cuenta(mail, meta=None):
    i = str(uuid.uuid4())
    q(su, "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
      (i, mail, psycopg2.extras.Json({"nombre": mail.split("@")[0].split(".")[0].title(), "apellido": "Prueba", **(meta or {})})))
    return i


uid = lambda mail: q(su, "select id::text from profiles where mail = %s", (mail,), True)["id"]
E1 = q(su, "select id from empresa where codigo = 'JSU'", uno=True)["id"]
ADMIN1, DEMO = uid("profesora@prueba.invalid"), uid("alumna@prueba.invalid")
SUPER = cuenta("superadmin@prueba.invalid", {"sin_empresa": True})
q(su, "update profiles set es_superadmin = true where id = %s", (SUPER,))
C = lambda i: ClienteFalso(uri, i)
E2 = rpc(C(SUPER), "crear_empresa", p_nombre="Pilates Centro", p_codigo="PIL")["id"]
ADMIN2 = cuenta("admin.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})
PROF2 = cuenta("profe.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "profesora"})
PROF3 = cuenta("otra.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "profesora"})
ALU2 = cuenta("alumna.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
ALU3 = cuenta("historial.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
ALU4 = cuenta("factura.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "alumna"})
ok("antes: el modelo viejo crea a la administradora de la Empresa 2 también como profesora",
   roles(ADMIN2, E2) == (True, True, False, True) and roles(ADMIN1, E1) == (True, True, False, True))

# datos de la Empresa 2 (clases, reservas, lista de espera y facturas para probar los cambios de rol)
hoy = q(su, "select public._hoy_mvd() d", uno=True)["d"]
TIPO = q(su, "insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) "
             "values ('PRUEBA Pilates', 50, 900, 5, %s) returning id", (E2,), True)["id"]


def horario(dia, hora, profesora, activo=True):
    return q(su, "insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) "
                 "values (%s, %s, %s, %s, %s, %s) returning id", (dia, hora, TIPO, activo, profesora, E2), True)["id"]


def clase(id_horario, fecha, hora, profesora):
    return q(su, "insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
                 "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 50, 900, 5, %s, %s, %s) returning id",
             (id_horario, fecha, hora, TIPO, E2, profesora), True)["id"]


H_P2 = horario("sabado", "10:00", PROF2)
H_A2 = horario("domingo", "10:00", ADMIN2)
FUT_P2 = clase(H_P2, hoy + timedelta(days=7), "10:00", PROF2)
FUT_A2 = clase(H_A2, hoy + timedelta(days=8), "10:00", ADMIN2)
VIEJA = clase(H_P2, (hoy.replace(day=1) - timedelta(days=10)), "10:00", PROF2)  # mes anterior
q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (ALU2, FUT_P2, E2))
q(su, "insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s)", (ALU3, VIEJA, E2))
q(su, "insert into asistencia (id_reserva, estado, id_empresa) select id, 'presente', %s from reserva where id_alumna = %s",
  (E2, ALU3))
q(su, "insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, 1, 'en_espera', %s)",
  (ALU2, FUT_A2, E2))
q(su, "insert into resumen_mensual (id_alumna, mes, anio, id_empresa) values (%s, %s, %s, %s)",
  (ALU4, hoy.month, hoy.year, E2))

# ---------- 1) la migración sobre la copia de producción y su vuelta atrás exacta ----------
estructura_antes, datos_antes = huellas_estructura(), datos()
CON_PROFESORA = sorted(r["id"] for r in q(su, "select id from membresia where es_admin and es_profesora"))
SIN_PROFESORA = "id, id_usuario, id_empresa, es_admin, es_alumna, activa, cedula, creado_en"
membresia_antes = localdb.huella(su, "membresia", SIN_PROFESORA)
with su.cursor() as cur:
    cur.execute(open(MIG).read())
ok("la migración se aplica sobre la copia de producción", True)
ok("la administradora real (profesora@prueba.invalid) queda solo administradora y activa",
   roles(ADMIN1, E1) == (True, False, False, True), roles(ADMIN1, E1))
ok("se guardan exactamente las membresías adaptadas (para la vuelta atrás)",
   sorted(r["id_membresia"] for r in q(su, "select id_membresia from _tres_roles_respaldo")) == CON_PROFESORA)
ok("membresías: fuera de esa casilla, nada cambió", localdb.huella(su, "membresia", SIN_PROFESORA) == membresia_antes)
despues = datos()
ok("todas las demás tablas quedan idénticas (clases, reservas, asistencias, facturas, pagos, Mercado Pago…)",
   all(despues[t] == datos_antes[t] for t in datos_antes if t != "membresia"),
   [t for t in datos_antes if t != "membresia" and despues[t] != datos_antes[t]])
ok("la administradora conserva sus horarios y clases asignadas",
   q(su, "select count(*) n from horario where id_profesora = %s", (ADMIN1,), True)["n"] > 0)
ok("nadie de la tabla nueva de respaldo es legible con sesión",
   rpc(C(ADMIN1), "puede_gestionar_persona", p_id_usuario=ADMIN1) == "ok"
   and sql_error("set role authenticated; select * from public._tres_roles_respaldo") == "42501")
q(su, "reset role")
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
estructura_vuelta, datos_vuelta = huellas_estructura(), datos()
ok("vuelta atrás: la estructura queda idéntica a la de antes de la migración", estructura_vuelta == estructura_antes,
   {k: (estructura_vuelta.get(k), v) for k, v in estructura_antes.items() if estructura_vuelta.get(k) != v})
ok("vuelta atrás: todos los datos quedan idénticos (incluidas las membresías)", datos_vuelta == datos_antes,
   [t for t in datos_antes if datos_vuelta.get(t) != datos_antes[t]])
ok("vuelta atrás: la administradora vuelve a tener también «profesora»", roles(ADMIN1, E1) == (True, True, False, True))
with su.cursor() as cur:
    cur.execute(open(MIG).read())
ok("la migración se puede volver a aplicar después de la vuelta atrás", roles(ADMIN2, E2) == (True, False, False, True))

# ---------- 2) un solo rol por membresía ----------
for flags in ((True, True, False), (True, False, True), (False, True, True), (True, True, True), (False, False, False)):
    ok(f"la base rechaza la combinación admin/profesora/alumna = {flags}, incluso como superusuario",
       sql_error("update membresia set es_admin = %s, es_profesora = %s, es_alumna = %s where id_usuario = %s and id_empresa = %s",
                 (*flags, PROF2, E2)) == "23514")
ok("…y tampoco al insertar", sql_error("insert into membresia (id_usuario, id_empresa, es_admin, es_profesora) values (%s, %s, true, true)",
                                        (DEMO, E2)) == "23514")
ok("cada membresía de la base tiene exactamente un rol",
   q(su, "select count(*) n from membresia where (es_admin::int + es_profesora::int + es_alumna::int) <> 1", uno=True)["n"] == 0)
A1, A2, P2, AL2 = C(ADMIN1), C(ADMIN2), C(PROF2), C(ALU2)
for nombre, cli in (("administradora", A2), ("profesora", P2), ("alumna", AL2)):
    try:
        cli.conn.cursor().execute("update public.membresia set es_admin = true where id_usuario = %s", (cli_id := PROF2,))
        ok(f"{nombre}: no escribe membresías directo", False)
    except psycopg2.Error as e:
        ok(f"{nombre}: no escribe membresías directo", e.pgcode == "42501")

# ---------- 3) la administradora tiene los permisos docentes ----------
ok("administradora sin «profesora»: es docente y profesora para la base",
   rpc(A1, "es_docente") is True and rpc(A1, "es_profesora") is True and rpc(A1, "es_admin") is True)
ok("administradora con clases: sigue en la lista de profesoras de su empresa (se puede asignar horarios)",
   ADMIN1 in {f["id"] for f in rpc(A1, "profesoras_empresa")})
ok("administradora con clases: ve las reservas de sus clases",
   isinstance(A1.table("reserva").select("id").execute().data, list))
ADMIN2B = cuenta("segunda.pil@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin"})
A2B = C(ADMIN2B)
ok("administradora nueva: nace solo administradora", roles(ADMIN2B, E2) == (True, False, False, True))
ok("administradora con cero clases: es docente y no tiene horarios",
   rpc(A2B, "es_docente") is True
   and q(su, "select count(*) n from horario where id_profesora = %s", (ADMIN2B,), True)["n"] == 0)
try:
    with A2B.conn.cursor() as cur:
        cur.execute("insert into public.horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_empresa) "
                    "values ('viernes', '20:00', %s, false, %s) returning id_profesora", (TIPO, E2))
        propia = str(cur.fetchone()[0])
    ok("administradora con cero clases: puede asignarse un horario (queda a su nombre)", propia == ADMIN2B, propia)
except psycopg2.Error as e:
    ok("administradora con cero clases: puede asignarse un horario (queda a su nombre)", False, e.pgcode)

# ---------- 4) una administradora común no toca a otra administradora (ni a sí misma) ----------
ok("no puede dar el rol de administradora a una profesora",
   rpc(A2, "asignar_membresia", p_id_usuario=PROF2, p_es_admin=True, p_es_profesora=False, p_es_alumna=False) == "solo_superadmin")
ok("no puede dar el rol de administradora a una cuenta nueva en su empresa",
   rpc(A2, "asignar_membresia", p_id_usuario=DEMO, p_es_admin=True, p_es_profesora=False, p_es_alumna=False) == "solo_superadmin")
ok("no puede cambiar el rol de otra administradora",
   rpc(A2, "asignar_membresia", p_id_usuario=ADMIN2B, p_es_admin=False, p_es_profesora=True, p_es_alumna=False) == "es_administradora")
ok("no puede quitarse a sí misma el rol",
   rpc(A2, "asignar_membresia", p_id_usuario=ADMIN2, p_es_admin=False, p_es_profesora=True, p_es_alumna=False) == "es_administradora")
ok("no puede desactivar a otra administradora",
   rpc(A2, "cambiar_estado_membresia", p_id_usuario=ADMIN2B, p_activa=False) == "es_administradora")
ok("no puede desactivarse a sí misma", rpc(A2, "cambiar_estado_membresia", p_id_usuario=ADMIN2, p_activa=False) == "no_puede_desactivarse")
ok("no puede editar los datos de otra administradora",
   rpc(A2, "editar_datos_persona", p_id_usuario=ADMIN2B, p_nombre="X", p_apellido="Y", p_telefono=None,
       p_fecha_nacimiento=None, p_fecha_vencimiento_carne_salud=None) == "es_administradora")
ok("no puede cargar la cédula de otra administradora",
   rpc(A2, "actualizar_cedula", p_id_alumna=ADMIN2B, p_cedula="1.234.567-2") == "es_administradora")
ok("la base le dice que no puede gestionar a otra administradora (contraseña incluida)",
   rpc(A2, "puede_gestionar_persona", p_id_usuario=ADMIN2B) == "es_administradora")
ok("sí puede editar sus propios datos", rpc(A2, "editar_datos_persona", p_id_usuario=ADMIN2, p_nombre="Lucía", p_apellido="Bentancor",
                                            p_telefono="099111222", p_fecha_nacimiento=None, p_fecha_vencimiento_carne_salud=None) == "ok"
   and q(su, "select nombre from profiles where id = %s", (ADMIN2,), True)["nombre"] == "Lucía")
ok("sí puede gestionar profesoras y alumnas", rpc(A2, "puede_gestionar_persona", p_id_usuario=PROF2) == "ok"
   and rpc(A2, "editar_datos_persona", p_id_usuario=ALU3, p_nombre="Inés", p_apellido="Duarte", p_telefono=None,
           p_fecha_nacimiento=None, p_fecha_vencimiento_carne_salud=None) == "ok")
ok("no puede usar las funciones de la superadministradora",
   rpc(A2, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_accion="profesora") == "no_autorizada"
   and rpc(A2, "asignar_administradora", p_id_empresa=E2, p_id_usuario=PROF2, p_es_profesora=False) == "no_autorizada"
   and rpc(A2, "administradoras_empresa", p_id_empresa=E2) == [])
ok("tampoco puede ver qué le falta a otra administradora", rpc(A2, "pendientes_cambio_rol", p_id_usuario=ADMIN2B) is None)
ok("una profesora no gestiona personas",
   rpc(P2, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=False, p_es_profesora=True, p_es_alumna=False) == "no_autorizada"
   and rpc(P2, "puede_gestionar_persona", p_id_usuario=ALU2) == "no_autorizada"
   and rpc(P2, "editar_datos_persona", p_id_usuario=ALU2, p_nombre="X", p_apellido="Y", p_telefono=None,
           p_fecha_nacimiento=None, p_fecha_vencimiento_carne_salud=None) == "no_autorizada")
ok("una alumna no usa nada de esto", rpc(AL2, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=True, p_es_profesora=False,
                                         p_es_alumna=False) == "no_autorizada"
   and rpc(AL2, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_accion="profesora") == "no_autorizada")
anon = ClienteFalso(uri, None, rol="anon")
ok("anon no ejecuta ninguna de las funciones nuevas",
   all(rpc(anon, f, **kw) == {"error": "42501"} for f, kw in (
       ("puede_gestionar_persona", {"p_id_usuario": ALU2}), ("pendientes_cambio_rol", {"p_id_usuario": ALU2}),
       ("administradoras_empresa", {"p_id_empresa": E2}),
       ("quitar_administradora", {"p_id_empresa": E2, "p_id_usuario": ADMIN2, "p_accion": "profesora"}))))
ok("las funciones internas de pendientes no se pueden llamar con sesión",
   rpc(A2, "_pendientes_alumna", p_usuario=ALU2, p_empresa=E2) == {"error": "42501"})

# ---------- 5) cambios alumna ↔ profesora ----------
cambio = lambda cli, i, rol: rpc(cli, "asignar_membresia", p_id_usuario=i, p_es_admin=False,
                                 p_es_profesora=rol == "profesora", p_es_alumna=rol == "alumna")
ok("dos roles a la vez: se rechaza", rpc(A2, "asignar_membresia", p_id_usuario=ALU3, p_es_admin=False, p_es_profesora=True,
                                         p_es_alumna=True) == "un_solo_rol")
ok("sin rol: se rechaza", rpc(A2, "asignar_membresia", p_id_usuario=ALU3, p_es_admin=False, p_es_profesora=False,
                              p_es_alumna=False) == "sin_rol")
ok("la pantalla puede saber antes qué le falta a una alumna",
   sorted(rpc(A2, "pendientes_cambio_rol", p_id_usuario=ALU2)) == ["lista_espera", "reservas"])
ok("alumna → profesora: bloqueado con reservas de este mes y lista de espera vigente",
   cambio(A2, ALU2, "profesora") == "pendientes:reservas,lista_espera" and roles(ALU2, E2)[:3] == (False, False, True))
ok("alumna → profesora: bloqueado con una factura en borrador", cambio(A2, ALU4, "profesora") == "pendientes:borradores")
q(su, "update resumen_mensual set estado_factura = 'confirmada', alumna_nombre = 'F', alumna_apellido = 'P', numero = 'PIL-X', "
      "confirmada_en = now(), vence_el = current_date + 10 "
      "where id_alumna = %s", (ALU4,))
ok("alumna → profesora: bloqueado con una factura confirmada sin pagar", cambio(A2, ALU4, "profesora") == "pendientes:facturas_pendientes")
q(su, "update resumen_mensual set estado_pago = 'pagado' where id_alumna = %s", (ALU4,))
ok("alumna → profesora: con la factura pagada, se permite", cambio(A2, ALU4, "profesora") == "ok"
   and roles(ALU4, E2) == (False, True, False, True))
historial_antes = {t: localdb.huella(su, t) for t in ("reserva", "asistencia", "resumen_mensual", "clase")}
ok("alumna → profesora: con solo historial de meses anteriores, se permite",
   rpc(A2, "pendientes_cambio_rol", p_id_usuario=ALU3) == [] and cambio(A2, ALU3, "profesora") == "ok")
ok("…y su historial (reservas, asistencias, facturas, clases) queda intacto",
   {t: localdb.huella(su, t) for t in historial_antes} == historial_antes)
ok("profesora → alumna: bloqueado con horarios activos y clases futuras a su nombre",
   cambio(A2, PROF2, "alumna") == "pendientes:horarios,clases_futuras" and roles(PROF2, E2)[:3] == (False, True, False))
q(su, "update horario set activo = false where id = %s", (H_P2,))
ok("profesora → alumna: sigue bloqueado mientras una clase futura tenga reservas",
   cambio(A2, PROF2, "alumna") == "pendientes:clases_futuras")
ok("profesora → alumna: sin nada pendiente, se permite", cambio(A2, PROF3, "alumna") == "ok"
   and roles(PROF3, E2) == (False, False, True, True))
ok("cambiar de rol a una administradora no se puede ni pasando por alumna",
   cambio(A2, ADMIN2B, "alumna") == "es_administradora")
ok("el mismo rol: no cambia nada", cambio(A2, ALU2, "alumna") == "ok" and roles(ALU2, E2) == (False, False, True, True))

# ---------- 6) superadministradora ----------
S = C(SUPER)
lista = {f["id"]: f for f in rpc(S, "administradoras_empresa", p_id_empresa=E2)}
ok("ve las administradoras de la empresa con lo que impediría dejarlas inactivas",
   set(lista) == {ADMIN2, ADMIN2B} and sorted(lista[ADMIN2]["pendientes"]) == ["clases_futuras", "horarios"]
   and lista[ADMIN2B]["pendientes"] == [])
ok("pasa una administradora a profesora (conserva sus clases)",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_accion="profesora") == "ok"
   and roles(ADMIN2B, E2) == (False, True, False, True)
   and q(su, "select count(*) n from horario where id_profesora = %s", (ADMIN2B,), True)["n"] == 1)
ok("como profesora ya no administra (la base la trata como profesora)",
   rpc(A2B, "es_admin") is False and rpc(A2B, "es_docente") is True
   and rpc(A2B, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "no_autorizada")
ok("no deja la empresa sin administradora activa (pasar a profesora)",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_accion="profesora") == "ultima_administradora")
ok("no deja la empresa sin administradora activa (dejar inactiva)",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_accion="inactiva") == "ultima_administradora")
ok("vuelve a designar administradora (solo administradora)",
   rpc(S, "asignar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_es_profesora=True) == "ok"
   and roles(ADMIN2B, E2) == (True, False, False, True))
ok("dejar inactiva a una administradora con horarios y clases futuras: pide resolverlo antes",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_accion="inactiva") == "pendientes:horarios,clases_futuras"
   and roles(ADMIN2, E2) == (True, False, False, True))
ok("una alumna con algo pendiente no se convierte en administradora",
   rpc(S, "asignar_administradora", p_id_empresa=E2, p_id_usuario=ALU2, p_es_profesora=False) == "pendientes:reservas,lista_espera"
   and roles(ALU2, E2)[:3] == (False, False, True))
ok("acción inválida y persona que no es administradora",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2, p_accion="borrar") == "accion_invalida"
   and rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=PROF2, p_accion="profesora") == "no_es_administradora")

# dos pedidos simultáneos que quitarían a las dos administradoras: solo uno puede
resultados = {}
barrera = threading.Barrier(2)


def quitar(i):
    cli = C(SUPER)
    barrera.wait()
    resultados[i] = rpc(cli, "quitar_administradora", p_id_empresa=E2, p_id_usuario=i, p_accion="profesora")


hilos = [threading.Thread(target=quitar, args=(i,)) for i in (ADMIN2, ADMIN2B)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
ok("pedidos simultáneos: uno pasa y el otro ve que es la última (la empresa nunca queda sin administradora)",
   sorted(resultados.values()) == ["ok", "ultima_administradora"]
   and q(su, "select count(*) n from membresia where id_empresa = %s and es_admin and activa", (E2,), True)["n"] == 1, resultados)
quedo = ADMIN2 if roles(ADMIN2, E2)[0] else ADMIN2B
otra = ADMIN2B if quedo == ADMIN2 else ADMIN2
rpc(S, "asignar_administradora", p_id_empresa=E2, p_id_usuario=otra, p_es_profesora=False)
q(su, "update horario set activo = false where id_profesora = %s", (ADMIN2B,))
ok("dejar inactiva a una administradora sin clases: queda profesora inactiva, con su historial",
   rpc(S, "quitar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_accion="inactiva") == "ok"
   and roles(ADMIN2B, E2) == (False, True, False, False)
   and q(su, "select count(*) n from horario where id_profesora = %s", (ADMIN2B,), True)["n"] == 1)
ok("inactiva: ya no entra a esa empresa", rpc(ClienteFalso(uri, ADMIN2B, sesion=uuid.uuid4()), "entrar_a_empresa",
                                              p_lugar="Pilates Centro")["resultado"] != "ok")
ok("la administradora común no puede reactivarla como administradora; como profesora, sí",
   rpc(A2, "asignar_membresia", p_id_usuario=ADMIN2B, p_es_admin=True, p_es_profesora=False, p_es_alumna=False) == "solo_superadmin"
   and rpc(A2, "cambiar_estado_membresia", p_id_usuario=ADMIN2B, p_activa=True) == "ok"
   and roles(ADMIN2B, E2) == (False, True, False, True))

# ---------- 7) aislamiento y roles distintos en empresas distintas ----------
ok("la administradora de la Empresa 1 no gestiona personas de la Empresa 2",
   rpc(A1, "puede_gestionar_persona", p_id_usuario=ALU2) == "no_existe"
   and rpc(A1, "cambiar_estado_membresia", p_id_usuario=ALU2, p_activa=False) == "no_existe"
   and rpc(A1, "editar_datos_persona", p_id_usuario=ALU2, p_nombre="X", p_apellido="Y", p_telefono=None,
           p_fecha_nacimiento=None, p_fecha_vencimiento_carne_salud=None) == "no_existe"
   and rpc(A1, "pendientes_cambio_rol", p_id_usuario=ALU2) is None)
ok("la misma cuenta puede ser profesora en una empresa y alumna en otra",
   rpc(A1, "asignar_membresia", p_id_usuario=PROF2, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "ok"
   and roles(PROF2, E1) == (False, False, True, True) and roles(PROF2, E2)[:3] == (False, True, False)
   and q(su, "select count(*) n from auth.users where id = %s", (PROF2,), True)["n"] == 1)
ok("el cambio en una empresa no afecta su membresía en la otra",
   rpc(A1, "cambiar_estado_membresia", p_id_usuario=PROF2, p_activa=False) == "ok"
   and roles(PROF2, E2)[3] is True and roles(PROF2, E1)[3] is False)

# ---------- 8) cuentas nuevas y compatibilidad con el código de theta ----------
NUEVA_AP = cuenta("vieja.adminprofe@prueba.invalid", {"id_empresa": E2, "rol_empresa": "admin_profesora"})
LEGADO = cuenta("legado@prueba.invalid")
q(su, "update profiles set rol = 'profesora' where id = %s", (LEGADO,))
ok("cuenta creada con el valor viejo «admin_profesora»: queda solo administradora",
   roles(NUEVA_AP, E2) == (True, False, False, True))
ok("cuenta creada sin rol de empresa: alumna de la Empresa 1, como siempre", roles(LEGADO, E1) == (False, False, True, True))
ok("theta: el alta de alumnas y profesoras con las tres casillas sigue funcionando",
   rpc(A1, "asignar_membresia", p_id_usuario=ALU2, p_es_admin=False, p_es_profesora=False, p_es_alumna=True) == "ok")
ok("theta: si intenta una combinación o dar administradora, recibe un código (no un error de la base)",
   rpc(A1, "asignar_membresia", p_id_usuario=ALU3, p_es_admin=True, p_es_profesora=True, p_es_alumna=False) == "solo_superadmin"
   and rpc(A1, "asignar_membresia", p_id_usuario=ALU3, p_es_admin=False, p_es_profesora=True, p_es_alumna=True) == "un_solo_rol")
ok("theta: la superadministradora asigna administradoras con la casilla vieja «también da clases»",
   rpc(S, "asignar_administradora", p_id_empresa=E2, p_id_usuario=ADMIN2B, p_es_profesora=True) == "ok"
   and roles(ADMIN2B, E2) == (True, False, False, True))
r = {f["codigo"]: f for f in rpc(S, "resumen_empresas")}
ok("resumen de la superadministradora: las administradoras ya no cuentan como profesoras",
   r["PIL"]["profesoras"] == q(su, "select count(*) n from membresia where id_empresa = %s and activa and es_profesora", (E2,), True)["n"]
   and "Lucía" in (r["PIL"]["administradoras"] or ""))

# ---------- 9) vuelta atrás después de usar el modelo nuevo ----------
with su.cursor() as cur:
    cur.execute(open(VUELTA).read())
final = huellas_estructura()
ok("vuelta atrás después de usarla: la estructura vuelve a ser la de antes", final == estructura_antes,
   {k: (final.get(k), v) for k, v in estructura_antes.items() if final.get(k) != v})
ok("vuelta atrás después de usarla: lo creado con el modelo nuevo sigue siendo válido para el modelo anterior",
   q(su, "select count(*) n from membresia where not (es_admin or es_profesora or es_alumna)", uno=True)["n"] == 0
   and roles(ADMIN1, E1) == (True, True, False, True))

localdb.borrar(srv, directorio)
print(f"\n{total_casos - fallas}/{total_casos} casos OK")
sys.exit(1 if fallas else 0)
