"""Novedades (notificaciones, fase 1) contra un Supabase LOCAL real (Colima + `supabase start`, gratis), con la app
completa por HTTP, inicio de sesión real, RLS, PostgREST (consultas anidadas) e invitaciones por Mailpit. Solo datos
ficticios. NUNCA usa producción: se niega a correr si la URL no es localhost.

Arma las dos empresas (Just Show Up con cuentas propias y Pilates Centro con `scripts/empresa_demo.py`), genera cada
situación que produce una novedad y comprueba:
- cada rol ve solo sus novedades (alumna, profesora, administradora, superadministradora);
- aislamiento: la alumna compartida y las administradoras no ven nada de la otra empresa;
- el número de la campana (`/novedades/contador`) coincide con la pantalla;
- solo lectura: abrir `/novedades` y el contador muchas veces no cambia ninguna fila (ni siquiera una oferta ya vencida,
  que la grilla sí procesaría).
Fase A (16/11): las 10 notificaciones de la alumna (recordatorio de 1 hora, reserva confirmada, factura nueva / próxima a
vencer / vencida, pago registrado, video nuevo / por vencer, además de lugar liberado y clase cancelada), los plazos
configurables (`notificacion_parametros`, solo la superadministradora los cambia) y el aislamiento de los videos.

Correr sobre una base recién reiniciada (`supabase db reset` en la copia aislada ~/.local/share/jsu-supabase-local).
Uso:  python tests/supabase_local/prueba_novedades_reales.py
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL = os.path.expanduser("~/.local/share/jsu-supabase-local")
estado = json.loads(subprocess.run(["supabase", "status", "-o", "json"], cwd=LOCAL, capture_output=True, text=True,
                                   check=True).stdout)
URL = estado["API_URL"]
if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", URL):
    raise SystemExit(f"Esta prueba solo corre contra un Supabase local (URL recibida: {URL}).")
# antes de importar la app o los scripts: load_dotenv no pisa variables ya definidas, así nunca se lee un .env real
os.environ["SUPABASE_URL"] = URL
os.environ["SUPABASE_KEY"] = estado.get("PUBLISHABLE_KEY") or estado["ANON_KEY"]
os.environ["SUPABASE_SERVICE_KEY"] = estado.get("SECRET_KEY") or estado["SERVICE_ROLE_KEY"]
os.environ.pop("VERCEL_ENV", None)
sys.path.insert(0, RAIZ)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import auth, clases, main  # noqa: E402
from scripts import empresa_demo  # noqa: E402
from scripts.prueba_novedades_real import titulos  # noqa: E402

DB = psycopg2.connect(estado["DB_URL"])
DB.autocommit = True
fallas = total = 0


def ok(nombre, condicion, extra=""):
    global fallas, total
    total += 1
    fallas += not condicion
    print(("OK   " if condicion else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not condicion else ""))


def sql(consulta, args=None, uno=False):
    with DB.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(consulta, args)
        filas = [dict(f) for f in cur.fetchall()] if cur.description else []
    return (filas[0] if filas else None) if uno else filas


def entrar(lugar, mail, clave):
    c = TestClient(main.app, base_url="http://testserver")
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c if r.status_code == 303 and "login" not in r.headers.get("location", "") else None


def entrar_superadmin(mail, clave):
    # las invitaciones solo salen desde un sitio permitido (auth.SITIOS_PERMITIDOS): el servidor local
    c = TestClient(main.app, base_url="http://localhost:8000")
    r = c.post("/admin/login", data={"mail": mail, "contrasena": clave}, follow_redirects=False)
    return c if r.status_code == 303 and "login" not in r.headers.get("location", "") else None


def api(cliente):
    return auth.cliente_con_token(cliente.cookies.get("sb_acceso"))


def _sin_acceso(consulta):
    try:
        return consulta().data == []
    except Exception:
        return True  # 42501: sin permiso sobre la tabla


def novedades_de(cliente):
    """(lista de (etiqueta, título), texto completo, contador)."""
    r = cliente.get("/novedades", follow_redirects=False)
    contador = cliente.get("/novedades/contador", follow_redirects=False).json()
    return titulos(r.text), " ".join(r.text.split()), contador, r.status_code


ok("la prueba corre contra el Supabase local", str(auth.cliente_admin().supabase_url).startswith(("http://127.0.0.1", "http://localhost")))
ok("base recién reiniciada (sin cuentas)", sql("select count(*) n from auth.users", uno=True)["n"] == 0)

hoy = clases.ahora().date()
manana, pasado, ayer = hoy + timedelta(days=1), hoy + timedelta(days=2), hoy - timedelta(days=1)
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

# ---------- 1) Just Show Up: cuentas y datos ficticios ----------
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
cuenta = lambda m, n, rol: auth.crear_cuenta(m, n, "Prueba", rol="profesora" if rol != "alumna" else "alumna", contrasena=CLAVE,
                                             id_empresa=JSU, rol_empresa=rol)[0]
ADM_J, PROF_J = cuenta("profesora@prueba.invalid", "Paula", "admin"), cuenta("profe.jsu@prueba.invalid", "Pía", "profesora")
ALU_J, ALU2_J = cuenta("alu.jsu@prueba.invalid", "Ana", "alumna"), cuenta("alu2.jsu@prueba.invalid", "Berta", "alumna")
COMPARTIDA = cuenta("alumna@prueba.invalid", "Alma", "alumna")
TIPO_J = sql("insert into tipo_clase (nombre, duracion, precio_presencial, cupo_max, id_empresa) values "
             "('Funcional', 60, 800, 7, %s) returning id", (JSU,), True)["id"]


def clase_j(fecha, hora="10:00", cupo=7, activo=False):
    h = sql("insert into horario (dia_semana, hora_inicio, id_tipo_clase, activo, id_profesora, id_empresa) values "
            "(%s, %s, %s, %s, %s, %s) returning id", (DIAS[fecha.weekday()], hora, TIPO_J, activo, PROF_J, JSU), True)["id"]
    return sql("insert into clase (id_horario, fecha, hora_inicio, estado, duracion, precio, cupo_max, id_tipo_clase, "
               "id_empresa, id_profesora) values (%s, %s, %s, 'programada', 60, 800, %s, %s, %s, %s) returning id",
               (h, fecha, hora, cupo, TIPO_J, JSU, PROF_J), True)["id"]


def reservar(alumna, id_clase, empresa, asistencia=None):
    r = sql("insert into reserva (id_alumna, id_clase, estado, id_empresa) values (%s, %s, 'confirmada', %s) returning id",
            (alumna, id_clase, empresa), True)["id"]
    if asistencia:
        sql("insert into asistencia (id_reserva, estado, id_empresa) values (%s, %s, %s)", (r, asistencia, empresa))
    return r


# facturas de octubre: Ana (se confirma y se vence), Alma (se confirma, pendiente) y Berta (queda en borrador)
C_OCT = clase_j(date(2026, 10, 20))
for a in (ALU_J, COMPARTIDA, ALU2_J):
    reservar(a, C_OCT, JSU, "presente")
AJ = entrar("Just Show Up", "profesora@prueba.invalid", CLAVE)
ok("Just Show Up: la administradora inicia sesión", AJ is not None)
AJ.get("/profesora/resumenes?mes=10&anio=2026")  # arma los borradores (pantalla de Facturación, preparación)
for a in (ALU_J, COMPARTIDA):
    f = sql("select id, version from resumen_mensual where id_alumna = %s and mes = 10 and id_empresa = %s", (a, JSU), True)
    AJ.post(f"/profesora/resumenes/{f['id']}/confirmar", data={"version": f["version"]})
F_ANA = sql("select * from resumen_mensual where id_alumna = %s and mes = 10", (ALU_J,), True)
ok("Just Show Up: facturas de octubre de Ana y Alma confirmadas, la de Berta en borrador",
   F_ANA["estado_factura"] == "confirmada"
   and sql("select estado_factura e from resumen_mensual where id_alumna = %s and mes = 10", (COMPARTIDA,), True)["e"] == "confirmada"
   and sql("select estado_factura e from resumen_mensual where id_alumna = %s and mes = 10", (ALU2_J,), True)["e"] == "borrador")
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy - timedelta(days=5), F_ANA["id"]))  # vencida (solo local)
# la de Alma, con plazo fijo a futuro: así el resultado no depende de la hora en que corre la prueba
sql("update resumen_mensual set vence_el = %s where id_alumna = %s and mes = 10 and id_empresa = %s",
    (hoy + timedelta(days=10), COMPARTIDA, JSU))

# clase terminada sin asistencia (profesora Pía): pendiente para ella y para la administradora
C_PEND = clase_j(ayer, "10:00")
reservar(ALU2_J, C_PEND, JSU)

# lista de espera: clase de mañana con cupo 1, Alma reservó, Ana espera; Alma cancela → se le ofrece a Ana
C_OFERTA = clase_j(manana, "10:00", cupo=1)
reservar(COMPARTIDA, C_OFERTA, JSU)
sql("update clase set cantidad_inscriptas = 1 where id = %s", (C_OFERTA,))
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, id_empresa) values (%s, %s, 1, 'en_espera', %s)",
    (ALU_J, C_OFERTA, JSU))
ALMA_J = entrar("Just Show Up", "alumna@prueba.invalid", CLAVE)
r = api(ALMA_J).rpc("cancelar_reserva", {"p_id_clase": C_OFERTA}).execute().data
OF = sql("select estado, oferta_vence_en from listadeespera where id_alumna = %s and id_clase = %s", (ALU_J, C_OFERTA), True)
ok("lista de espera: al cancelar Alma, se le ofrece el lugar a Ana", OF and OF["estado"] == "ofrecido", (r, OF))

# clase cancelada por la profesora: Ana tenía reserva pasado mañana; la administradora la cancela (Planificación)
C_CANC = clase_j(pasado, "11:00", activo=True)
reservar(ALU_J, C_CANC, JSU)
r = api(AJ).rpc("crear_bloqueos_puntuales", {"p_ids_clase": [C_CANC], "p_motivo": "PRUEBA novedades"}).execute().data
ok("Planificación: clase de pasado mañana cancelada (reserva de Ana con bloqueo)",
   sql("select id_bloqueo from reserva where id_alumna = %s and id_clase = %s", (ALU_J, C_CANC), True)["id_bloqueo"] is not None, r)
sql("update horario set activo = false where id = (select id_horario from clase where id = %s)", (C_CANC,))

# oferta YA VENCIDA de Alma en otra clase: Novedades no la muestra ni la procesa (la grilla sí la pasaría a «expirado»)
C_VENCIDA = clase_j(manana, "12:00", cupo=1)
reservar(ALU2_J, C_VENCIDA, JSU)
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, oferta_vence_en, id_empresa) values "
    "(%s, %s, 1, 'ofrecido', now() - interval '5 minutes', %s)", (COMPARTIDA, C_VENCIDA, JSU))

# ---------- 2) Pilates Centro y superadministradora con el script real ----------
base = empresa_demo.BaseSupabase()
CLAVE_SA = empresa_demo.crear_superadmin_local(base)
creada = empresa_demo.crear(base, hoy, guardar_contrasenas=False)
PIL = sql("select id from empresa where codigo = 'PIL'", uno=True)["id"]
CL_PIL = creada["contrasenas"]
ok("Pilates Centro creada con el escenario de demostración", PIL and empresa_demo.ADMIN[0] in CL_PIL)
SA = entrar_superadmin("superadmin@prueba.invalid", CLAVE_SA)
ok("la superadministradora inicia sesión", SA is not None)
MARTA = f"marta.novedades.{uuid.uuid4().hex[:6]}@ejemplo.test"
SA.post(f"/admin/empresas/{PIL}/administradora", data={"nombre": "Marta", "apellido": "Suárez", "mail": MARTA},
        follow_redirects=False)
ok("invitación de una administradora nueva de Pilates Centro (queda pendiente)",
   sql("select invited_at is not null and last_sign_in_at is null p from auth.users where email = %s", (MARTA,), True)["p"])

# situaciones propias de Pilates Centro (solo en la base local), para comprobar el aislamiento en los dos sentidos
uid = lambda mail: sql("select id::text from profiles where mail = %s", (mail,), True)["id"]
INES, CAROLINA = uid(empresa_demo.INES), uid(empresa_demo.PROFESORA[0])
F_INES = sql("select id from resumen_mensual where id_alumna = %s and id_empresa = %s and estado_factura = 'confirmada' "
             "and estado_pago = 'pendiente' order by id desc limit 1", (INES, PIL), True)
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy - timedelta(days=3), F_INES["id"]))
C_CARO = sql("select c.id from clase c join reserva r on r.id_clase = c.id join asistencia a on a.id_reserva = r.id "
             "where c.id_empresa = %s and c.id_profesora = %s and c.fecha < %s and c.estado = 'programada' "
             "order by c.fecha desc limit 1", (PIL, CAROLINA, hoy), True)["id"]
sql("delete from asistencia where id_reserva in (select id from reserva where id_clase = %s and estado = 'confirmada')", (C_CARO,))
C_PIL_FUT = sql("select id, fecha from clase where id_empresa = %s and fecha > %s and estado = 'programada' "
                "order by fecha limit 1", (PIL, hoy), True)
sql("insert into listadeespera (id_alumna, id_clase, posicion, estado, oferta_vence_en, id_empresa) values "
    "(%s, %s, 99, 'ofrecido', now() + interval '20 minutes', %s)", (COMPARTIDA, C_PIL_FUT["id"], PIL))
ok("Pilates Centro: factura vencida de Inés, asistencia pendiente de Carolina y lugar ofrecido a la alumna compartida",
   F_INES and C_CARO and C_PIL_FUT)

# ---------- 2b) fase A: Clara (Just Show Up) con una situación de cada notificación nueva ----------
CLARA = cuenta("clara.jsu@prueba.invalid", "Clara", "alumna")
ahora_mvd = clases.ahora().replace(second=0, microsecond=0)
en_30 = ahora_mvd + timedelta(minutes=30)
C_RECORD = clase_j(en_30.date(), en_30.strftime("%H:%M"))       # empieza dentro de la hora → recordatorio
reservar(CLARA, C_RECORD, JSU)
en_90 = ahora_mvd + timedelta(minutes=90)
C_LEJOS = clase_j(en_90.date(), en_90.strftime("%H:%M"))        # en hora y media: todavía no hay recordatorio
r_lejos = reservar(CLARA, C_LEJOS, JSU)
sql("update reserva set fecha_reserva = now() - interval '2 days' where id = %s", (r_lejos,))  # reservada hace 2 días
C_NUEVA = clase_j(hoy + timedelta(days=3), "09:00")             # reservada ahora → «Reserva confirmada»
reservar(CLARA, C_NUEVA, JSU)
# facturas de Clara: agosto (se paga ahora) y setiembre (vence en 2 días → próxima a vencer)
for mes in (8, 9):
    reservar(CLARA, clase_j(date(2026, mes, 14)), JSU, "presente")
    AJ.get(f"/profesora/resumenes?mes={mes}&anio=2026")
    f = sql("select id, version from resumen_mensual where id_alumna = %s and mes = %s and id_empresa = %s", (CLARA, mes, JSU), True)
    AJ.post(f"/profesora/resumenes/{f['id']}/confirmar", data={"version": f["version"]})
F_AGO = sql("select * from resumen_mensual where id_alumna = %s and mes = 8", (CLARA,), True)
F_SET = sql("select * from resumen_mensual where id_alumna = %s and mes = 9", (CLARA,), True)
sql("update resumen_mensual set vence_el = %s where id = %s", (hoy + timedelta(days=2), F_SET["id"]))
r = api(AJ).rpc("registrar_pago", {"p_id_resumen": F_AGO["id"], "p_monto": str(F_AGO["total"]), "p_medio": "efectivo",
                                   "p_fecha": hoy.isoformat()}).execute().data
ok("fase A: facturas de Clara confirmadas (agosto pagada ahora, setiembre pendiente)",
   F_AGO["estado_factura"] == F_SET["estado_factura"] == "confirmada"
   and sql("select estado_pago e from resumen_mensual where id = %s", (F_AGO["id"],), True)["e"] == "pagado", r)


def video_local(id_clase, empresa, profesora, horas_restantes, sufijo):
    return sql("insert into video (id_empresa, id_clase, id_profesora, estado, huella_sha256, duracion_seg, tamano_bytes, "
               "partes, lista_hls, publicado_en, vence_en) values (%s, %s, %s, 'publicado', %s, 3000, 1000, '[]', 'x', "
               "now() - interval '1 hour', now() + %s * interval '1 hour') returning id",
               (empresa, id_clase, profesora, (sufijo * 64)[:64], horas_restantes), True)["id"]


V_NUEVO = video_local(clase_j(ayer, "08:00"), JSU, PROF_J, 20, "a")
V_POR_VENCER = video_local(clase_j(ayer, "09:00"), JSU, PROF_J, 2, "b")
V_VISTO = video_local(clase_j(ayer, "07:00"), JSU, PROF_J, 20, "c")
sql("insert into video_reproduccion (id_empresa, id_video, id_usuario, segundos_vistos, bytes_estimados) "
    "values (%s, %s, %s, 60, 1000)", (JSU, V_VISTO, CLARA))   # Clara ya lo empezó a ver: no es novedad para ella
V_PIL = video_local(C_CARO, PIL, CAROLINA, 20, "d")  # video de Pilates Centro: no debe verse desde Just Show Up

# ---------- 3) sesiones de cada rol ----------
LU = entrar("Pilates Centro", empresa_demo.ADMIN[0], CL_PIL[empresa_demo.ADMIN[0]])
CA = entrar("Pilates Centro", empresa_demo.PROFESORA[0], CL_PIL[empresa_demo.PROFESORA[0]])
ANA = entrar("Just Show Up", "alu.jsu@prueba.invalid", CLAVE)
CLARA_S = entrar("Just Show Up", "clara.jsu@prueba.invalid", CLAVE)
BERTA = entrar("Just Show Up", "alu2.jsu@prueba.invalid", CLAVE)
PIA = entrar("Just Show Up", "profe.jsu@prueba.invalid", CLAVE)
ALMA_P = entrar("Pilates Centro", "alumna@prueba.invalid", CLAVE)
INES_S = entrar("Pilates Centro", empresa_demo.INES, CL_PIL[empresa_demo.INES])
ok("todas las cuentas inician sesión", all([LU, CA, ANA, BERTA, PIA, ALMA_P, INES_S, ALMA_J, AJ, SA, CLARA_S]))

# ---------- 4) solo lectura: huella antes ----------
TABLAS = [r["table_name"] for r in sql("select table_name from information_schema.tables where table_schema = 'public' "
                                       "and table_type = 'BASE TABLE' and table_name <> 'sesion_empresa' order by 1")]


def huella():
    salida = {}
    for t in TABLAS:
        filas = sql(f"select * from public.{t} order by 1")
        for f in filas:
            f.pop("empresa_activa", None)  # cambia al iniciar sesión, no es un dato de la plataforma
        salida[t] = json.dumps(filas, default=str, sort_keys=True)
    return salida


ANTES = huella()

# ---------- 5) qué ve cada rol ----------
res = {n: novedades_de(c) for n, c in
       {"ana": ANA, "berta": BERTA, "clara": CLARA_S, "alma_jsu": ALMA_J, "alma_pil": ALMA_P, "pia": PIA, "paula": AJ, "lucia": LU,
        "carolina": CA, "ines": INES_S, "super": SA}.items()}
for n, (lista, _, cont, st) in res.items():
    ok(f"{n}: /novedades responde 200 y el contador coincide con la pantalla ({len(lista)})",
       st == 200 and cont["total"] == len(lista) and cont["urgentes"] == sum(e == "Urgente" for e, _ in lista), (st, cont, lista))
    print("      " + "; ".join(f"[{e}] {t}" for e, t in lista) if lista else "      (sin novedades)")

t = lambda n: [x for _, x in res[n][0]]
FASE_A = ("Reserva confirmada", "reservas confirmadas", "Tu clase empieza", "Pago registrado", "Nuevo video",
          "videos nuevos", "Un video está por")
f1 = lambda n: [x for x in t(n) if not any(m in x for m in FASE_A)]
ana = f1("ana")
ok("alumna Ana: lugar liberado (urgente), factura vencida (urgente) y clase cancelada (aviso)",
   ana == ["¡Se liberó un lugar para vos!", "Factura de octubre 2026 vencida",
           "La profesora canceló 1 clase que tenías reservada"], ana)
ok("alumna Ana: el lugar ofrecido lleva a Mis inscripciones (con Confirmar / No me interesa) y dice la clase",
   'href="/mis-inscripciones">Confirmar el lugar' in res["ana"][1] and f"{manana.day}/{manana.month} 10:00" in res["ana"][1])
ok("alumna Berta: su factura en borrador no aparece (todavía no está publicada)", f1("berta") == [], t("berta"))
ok("alumna Berta: «Reserva confirmada» por la clase de mañana que reservó recién", "Reserva confirmada" in t("berta"),
   t("berta"))
ok("alumna compartida en Just Show Up: solo su factura de JSU para pagar; la oferta vencida no aparece",
   f1("alma_jsu") == ["Nueva factura de octubre 2026"] and "JSU-2026-10-" in res["alma_jsu"][1]
   and "PIL-" not in res["alma_jsu"][1], t("alma_jsu"))
ok("alumna compartida en Pilates Centro: solo el lugar ofrecido en Pilates, nada de Just Show Up",
   f1("alma_pil") == ["¡Se liberó un lugar para vos!"]
   and f'{C_PIL_FUT["fecha"].day}/{C_PIL_FUT["fecha"].month}' in res["alma_pil"][1]
   and "JSU-" not in res["alma_pil"][1] and "Factura" not in res["alma_pil"][1], t("alma_pil"))
ok("alumna compartida en Just Show Up: NO ve el lugar ofrecido en Pilates Centro", "¡Se liberó" not in res["alma_jsu"][1])
ok("alumna Inés (Pilates Centro): su factura vencida", "Factura de octubre 2026 vencida" in f1("ines"), t("ines"))
ok("profesora Pía: solo sus asistencias pendientes (sin facturación)",
   t("pia") == ["1 clase terminada sin asistencia registrada"], t("pia"))
ok("administradora Paula: facturas vencidas (urgente), asistencias pendientes y borrador de octubre",
   t("paula") == ["1 factura de octubre 2026 vencida sin pagar", "1 clase terminada sin asistencia registrada",
                  "1 factura de octubre 2026 en borrador"], t("paula"))
ok("administradora Paula: el nombre en la factura vencida es el de Ana, y nada de Pilates Centro",
   "Ana Prueba" in res["paula"][1] and not any(n in res["paula"][1] for n in ("Inés", "Rocío", "Lucía", "PIL-")))
ok("administradora Lucía (Pilates Centro): factura vencida de Inés y la clase pendiente de Carolina",
   "Inés Duarte" in res["lucia"][1] and any("vencida" in x for x in t("lucia"))
   and "1 clase terminada sin asistencia registrada" in t("lucia"), t("lucia"))
ok("administradora Lucía (Pilates Centro): nada de Just Show Up",
   not any(n in res["lucia"][1] for n in ("Ana Prueba", "Berta", "JSU-", "Funcional")), t("lucia"))
ok("profesora Carolina (Pilates Centro): solo su asistencia pendiente, sin facturación ni Just Show Up",
   t("carolina") == ["1 clase terminada sin asistencia registrada"] and "Funcional" not in res["carolina"][1], t("carolina"))
ok("superadministradora: la invitación pendiente de Pilates Centro (con el nombre) y nada más",
   t("super") == ["Pilates Centro: 1 administradora todavía no activó su cuenta"] and "Marta Suárez" in res["super"][1],
   t("super"))
ok("superadministradora: no ve datos de alumnas ni facturas", not any(n in res["super"][1] for n in ("Ana Prueba", "factura")))

# ---------- 5b) fase A: las notificaciones nuevas de la alumna, con datos reales ----------
clara, clara_txt = t("clara"), res["clara"][1]
ok("Clara: recordatorio de la clase que empieza en 30 minutos (urgente) y no de la que empieza en hora y media",
   f"Tu clase empieza a las {en_30.strftime('%H:%M')}" in clara and ("Urgente", f"Tu clase empieza a las {en_30.strftime('%H:%M')}") in res["clara"][0]
   and f"Tu clase empieza a las {en_90.strftime('%H:%M')}" not in clara, clara)
d3 = hoy + timedelta(days=3)
ok("Clara: «Reserva confirmada» solo por la reserva reciente (no la de hace 2 días ni la del recordatorio)",
   "Reserva confirmada" in clara and f"{d3.day}/{d3.month} 09:00" in clara_txt
   and f"{en_90.day}/{en_90.month} {en_90.strftime('%H:%M')}" not in clara_txt.split("Reserva confirmada")[-1].split("</li>")[0],
   clara)
ok("Clara: factura de setiembre próxima a vencer (urgente) y no como «nueva»",
   any(x.startswith("Tu factura de setiembre 2026 vence") for x in clara) and "Nueva factura de setiembre 2026" not in clara, clara)
ok("Clara: pago registrado de agosto, sin la factura de agosto como pendiente",
   "Pago registrado: factura de agosto 2026" in clara and not any("agosto" in x and "Pago" not in x for x in clara), clara)
ok("Clara: video por vencer (enlace al video) y video nuevo; no el que ya empezó a ver",
   "Un video está por dejar de estar disponible" in clara and f'href="/videos/{V_POR_VENCER}"' in clara_txt
   and "Nuevo video disponible" in clara and f'href="/videos/{V_NUEVO}"' in clara_txt
   and f'href="/videos/{V_VISTO}"' not in clara_txt, clara)
ok("Ana (no empezó ninguno): ve 2 videos nuevos (incluido el que Clara ya vio) y el por vencer",
   "2 videos nuevos disponibles" in t("ana") and "Un video está por dejar de estar disponible" in t("ana"), t("ana"))
ok("aislamiento de videos: la alumna compartida en Just Show Up no ve el de Pilates Centro, y en Pilates solo ve ese",
   f'/videos/{V_PIL}"' not in res["alma_jsu"][1] and f'href="/videos/{V_PIL}"' in res["alma_pil"][1]
   and not any(f'/videos/{v}"' in res["alma_pil"][1] for v in (V_NUEVO, V_POR_VENCER, V_VISTO)))
ok("docentes y superadministradora no reciben avisos de alumna (videos, pagos, reservas)",
   not any(any(m in x for m in FASE_A) for n in ("pia", "paula", "lucia", "carolina", "super") for x in t(n)))
ok("Clara: el contador cuenta todo y los urgentes primero",
   res["clara"][2]["total"] == len(clara) and [e for e, _ in res["clara"][0]] == sorted(
       [e for e, _ in res["clara"][0]], key=["Urgente", "Pendiente", "Aviso"].index))

# ---------- 6) solo lectura: después de abrir todo varias veces ----------
for _ in range(3):
    for c in (ANA, BERTA, CLARA_S, ALMA_J, ALMA_P, INES_S, PIA, AJ, LU, CA, SA):
        c.get("/novedades")
        c.get("/novedades/contador")
DESPUES = huella()
distintas = [tb for tb in TABLAS if ANTES[tb] != DESPUES[tb]]
ok(f"solo lectura: las {len(TABLAS)} tablas quedaron idénticas después de 66 pedidos a Novedades", not distintas, distintas)
ok("solo lectura: la oferta vencida de Alma sigue «ofrecido» (Novedades no procesa listas de espera)",
   sql("select estado from listadeespera where id_alumna = %s and id_clase = %s", (COMPARTIDA, C_VENCIDA), True)["estado"]
   == "ofrecido")

# ---------- 6b) plazos configurables ----------
ok("parámetros: la alumna no puede leer la tabla directo ni cambiar plazos",
   _sin_acceso(lambda: api(CLARA_S).table("notificacion_parametros").select("*").execute())
   and api(CLARA_S).rpc("notificaciones_configurar", {"p_clave": "factura_aviso_dias", "p_valor": 1}).execute().data == "sin_permiso")
ok("parámetros: la administradora tampoco los cambia",
   api(AJ).rpc("notificaciones_configurar", {"p_clave": "factura_aviso_dias", "p_valor": 1}).execute().data == "sin_permiso")
ok("parámetros: la alumna lee los valores por defecto",
   api(CLARA_S).rpc("novedades_parametros", {}).execute().data == {"factura_aviso_dias": 3, "video_aviso_horas": 3,
                                                                     "reserva_aviso_horas": 24, "pago_aviso_dias": 3})
ok("parámetros: la superadministradora rechaza valores fuera de rango o claves inexistentes",
   api(SA).rpc("notificaciones_configurar", {"p_clave": "factura_aviso_dias", "p_valor": 0}).execute().data == "valor_invalido"
   and api(SA).rpc("notificaciones_configurar", {"p_clave": "otra", "p_valor": 2}).execute().data == "clave_invalida")
ok("parámetros: la superadministradora pone 1 día de aviso de facturas y 1 hora de videos",
   api(SA).rpc("notificaciones_configurar", {"p_clave": "factura_aviso_dias", "p_valor": 1}).execute().data == "ok"
   and api(SA).rpc("notificaciones_configurar", {"p_clave": "video_aviso_horas", "p_valor": 1}).execute().data == "ok")
clara2 = titulos(CLARA_S.get("/novedades").text)
ok("sin desplegar: con 1 día, la factura que vence en 2 días vuelve a ser «nueva»; con 1 hora, el video de 2 h es «nuevo»",
   ("Pendiente", "Nueva factura de setiembre 2026") in clara2
   and not any("por dejar" in x for _, x in clara2) and ("Aviso", "2 videos nuevos disponibles") in clara2, clara2)
ok("parámetros: queda registrado quién y cuándo los cambió",
   sql("select actualizado_por::text p from notificacion_parametros", uno=True)["p"]
   == sql("select id::text from profiles where mail = 'superadmin@prueba.invalid'", uno=True)["id"])
for clave, valor in (("factura_aviso_dias", 3), ("video_aviso_horas", 3)):
    api(SA).rpc("notificaciones_configurar", {"p_clave": clave, "p_valor": valor}).execute()

# ---------- 7) sin sesión ----------
anon = TestClient(main.app, base_url="http://testserver")
ok("sin sesión: /novedades y el contador mandan al inicio de sesión",
   all(anon.get(u, follow_redirects=False).status_code == 303 for u in ("/novedades", "/novedades/contador")))

print(f"\n{total - fallas}/{total} comprobaciones OK" + ("" if not fallas else f" — {fallas} FALLA(S)"))
sys.exit(1 if fallas else 0)
