"""Pruebas REALES de Mi perfil editable (datos personales y cambio de correo con doble confirmación) contra un Supabase
LOCAL (Colima + `supabase start` en la copia aislada ~/.local/share/jsu-supabase-local). Los mails los recibe Mailpit.

Usa GoTrue, la API y la base con todas las migraciones del repo (incluida 20261112000000_perfil_editable) y la app
completa por HTTP (TestClient) como `http://localhost:8000`. Crea sus cuentas (`@ejemplo.test`) y una empresa propia, y
las borra al final. NUNCA se conecta a producción: se niega a correr si la URL no es localhost.

Requisitos en la copia aislada (los trae supabase/config.toml del repo): plantilla `email_change` con
supabase/templates/cambio_correo.html, `double_confirm_changes = true` y `http://localhost:8000/**` permitido.

Uso: python tests/supabase_local/prueba_perfil_reales.py
"""
import html
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import uuid
from urllib.parse import parse_qs, unquote_plus, urlsplit

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL = os.path.expanduser("~/.local/share/jsu-supabase-local")
estado = json.loads(subprocess.run(["supabase", "status", "-o", "json"], cwd=LOCAL, capture_output=True, text=True,
                                   check=True).stdout)
URL = estado["API_URL"]
MAILPIT = estado.get("MAILPIT_URL") or estado["INBUCKET_URL"]
for u in (URL, MAILPIT):
    if not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", u):
        raise SystemExit(f"Esta prueba solo corre contra un Supabase local (URL recibida: {u}).")
os.environ["SUPABASE_URL"] = URL
os.environ["SUPABASE_KEY"] = estado.get("PUBLISHABLE_KEY") or estado["ANON_KEY"]
os.environ["SUPABASE_SERVICE_KEY"] = estado.get("SECRET_KEY") or estado["SERVICE_ROLE_KEY"]
os.environ.pop("VERCEL_ENV", None)
sys.path.insert(0, RAIZ)

import httpx  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from supabase import create_client  # noqa: E402
from app import auth, main  # noqa: E402

SITIO = "http://localhost:8000"
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


def mails_para(mail):
    return httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'to:"{mail}"'}).json().get("messages") or []


def mail_nuevo(mail, antes, segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        lista = mails_para(mail)
        if len(lista) > antes:
            cuerpo = httpx.get(f"{MAILPIT}/api/v1/message/{lista[0]['ID']}").json()
            m = re.search(r'href="([^"]+)"', cuerpo["HTML"])
            return (html.unescape(m.group(1)) if m else None), cuerpo["Subject"], cuerpo["HTML"]
        time.sleep(0.3)
    return None, None, None


def codigo(enlace):
    return parse_qs(urlsplit(enlace).fragment).get("token_hash", [""])[0] if enlace else ""


def entrar(lugar, mail, clave, admin=False):
    c = TestClient(main.app, base_url=SITIO)
    if admin:
        r = c.post("/admin/login", data={"mail": mail, "contrasena": clave}, follow_redirects=False)
    else:
        r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c, r


def aviso(r):
    return unquote_plus(r.headers.get("location", ""))


def perfil(uid):
    return sql("select nombre, apellido, telefono, fecha_nacimiento::text fn, fecha_vencimiento_carne_salud::text fc, "
               "mail, cedula from profiles where id = %s", (uid,), uno=True)


def correo_auth(uid):
    return sql("select email, email_change from auth.users where id = %s", (uid,), uno=True)


S = uuid.uuid4().hex[:6]
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
httpx.delete(f"{MAILPIT}/api/v1/messages")
JSU = sql("select id, nombre from empresa where codigo = 'JSU'", uno=True)
NOMBRE_B = f"Pilates Perfil {S}"
B = sql("insert into empresa (nombre, codigo) values (%s, %s) returning id",
        (NOMBRE_B, "".join(random.choice(string.ascii_uppercase) for _ in range(5))), uno=True)["id"]
M = {k: f"{k}.{S}@ejemplo.test" for k in ("alumna", "profe", "admin", "super", "otra", "vence", "cambia")}
FICTICIA = f"ficticia.{S}@prueba.invalid"
creadas = []
try:
    def crear(mail, empresa, rol, **kw):
        uid, _ = auth.crear_cuenta(mail, "Prueba", "Perfil", contrasena=CLAVE, id_empresa=empresa, rol_empresa=rol, **kw)
        creadas.append(uid)
        return uid

    U = {"alumna": crear(M["alumna"], JSU["id"], "alumna"), "profe": crear(M["profe"], B, "profesora"),
         "admin": crear(M["admin"], JSU["id"], "admin"), "otra": crear(M["otra"], JSU["id"], "alumna"),
         "vence": crear(M["vence"], JSU["id"], "alumna"), "cambia": crear(M["cambia"], JSU["id"], "alumna"),
         "ficticia": crear(FICTICIA, JSU["id"], "alumna")}
    uid_super, _ = auth.crear_cuenta(M["super"], "Super", "Perfil", contrasena=CLAVE, sin_empresa=True)
    creadas.append(uid_super)
    sql("update profiles set es_superadmin = true where id = %s", (uid_super,))
    sql("delete from membresia where id_usuario = %s", (uid_super,))
    # la alumna también es alumna de la empresa B (datos compartidos entre empresas)
    sql("insert into membresia (id_usuario, id_empresa, es_alumna) values (%s, %s, true) on conflict do nothing",
        (U["alumna"], B))
    sql("update membresia set cedula = '12345672' where id_usuario = %s and id_empresa = %s", (U["alumna"], JSU["id"]))

    # ---------- datos personales ----------
    c, r = entrar(JSU["nombre"], M["alumna"], CLAVE)
    ok("la alumna entra", r.status_code == 303)
    pag = c.get("/perfil")
    ok("Mi perfil muestra el formulario con todos los campos de la alumna",
       all(f'name="{n}"' in pag.text for n in ("nombre", "apellido", "telefono", "fecha_nacimiento",
                                                 "fecha_vencimiento_carne_salud")) and 'action="/perfil/correo"' in pag.text)
    ok("la cédula se muestra pero no es editable", "1.234.567-2" in pag.text and 'name="cedula"' not in pag.text)
    r = c.post("/perfil/datos", data={"nombre": "Ana María", "apellido": "Pérez-Gómez", "telefono": "099 123 456",
                                       "fecha_nacimiento": "1990-05-04", "fecha_vencimiento_carne_salud": "2030-01-31",
                                       "cedula": "11111111"}, follow_redirects=False)
    p = perfil(U["alumna"])
    ok("la alumna guarda sus datos", r.status_code == 303 and "se guardaron" in aviso(r))
    ok("quedan normalizados en la base", p["nombre"] == "Ana María" and p["apellido"] == "Pérez-Gómez"
       and p["telefono"] == "099123456" and p["fn"] == "1990-05-04" and p["fc"] == "2030-01-31")
    ced = sql("select cedula from membresia where id_usuario = %s and id_empresa = %s", (U["alumna"], JSU["id"]), uno=True)
    ok("un campo cedula agregado al formulario no cambia la cédula", ced["cedula"] == "12345672")

    antes = perfil(U["alumna"])
    r = c.post("/perfil/datos", data={"nombre": "Ana3", "apellido": "", "telefono": "12345",
                                       "fecha_nacimiento": "2999-01-01", "fecha_vencimiento_carne_salud": "2001-01-01"})
    ok("datos inválidos: 400 con todos los errores juntos", r.status_code == 400 and r.text.count("<li>") >= 5, r.status_code)
    ok("datos inválidos: no se guarda nada", perfil(U["alumna"]) == antes)

    sql("update profiles set fecha_vencimiento_carne_salud = '2020-01-01' where id = %s", (U["alumna"],))
    r = c.post("/perfil/datos", data={"nombre": "Ana María", "apellido": "Pérez-Gómez", "telefono": "098765432",
                                       "fecha_nacimiento": "1990-05-04", "fecha_vencimiento_carne_salud": "2020-01-01"},
               follow_redirects=False)
    ok("un carné ya vencido que no se toca no impide guardar lo demás",
       r.status_code == 303 and perfil(U["alumna"])["telefono"] == "098765432")

    cb, r = entrar(NOMBRE_B, M["alumna"], CLAVE)
    pb = cb.get("/perfil")
    ok("en la otra empresa ve los mismos datos (son de la cuenta)", 'value="098765432"' in pb.text and "Ana María" in pb.text)
    ok("en la otra empresa no ve la cédula de la primera", "1.234.567-2" not in pb.text)
    cb.post("/logout")

    # la función no permite tocar a otra persona y la API directa tampoco
    sb = create_client(URL, os.environ["SUPABASE_KEY"])
    sb.auth.sign_in_with_password({"email": M["otra"], "password": CLAVE})
    try:
        sb.table("profiles").update({"telefono": "091111111"}).eq("id", U["alumna"]).execute()
    except Exception:
        pass
    ok("otra persona no puede cambiar el perfil ajeno por la API", perfil(U["alumna"])["telefono"] == "098765432")
    r = sb.rpc("editar_mis_datos", {"p_nombre": "Otra", "p_apellido": "Persona", "p_telefono": None,
                                    "p_fecha_nacimiento": None, "p_fecha_vencimiento_carne_salud": None}).execute().data
    ok("editar_mis_datos solo cambia a quien la llama",
       r == "ok" and perfil(U["otra"])["nombre"] == "Otra" and perfil(U["alumna"])["nombre"] == "Ana María")
    r = sb.rpc("editar_mis_datos", {"p_nombre": "Otra", "p_apellido": "Persona", "p_telefono": "123",
                                    "p_fecha_nacimiento": None, "p_fecha_vencimiento_carne_salud": None}).execute().data
    ok("la base también rechaza un teléfono inválido", r == "datos_invalidos")
    sb.auth.sign_out()
    anon = create_client(URL, os.environ["SUPABASE_KEY"])
    try:
        anon.rpc("editar_mis_datos", {"p_nombre": "X", "p_apellido": "Y", "p_telefono": None, "p_fecha_nacimiento": None,
                                      "p_fecha_vencimiento_carne_salud": None}).execute()
        ok("sin sesión no se puede ejecutar editar_mis_datos", False)
    except Exception:
        ok("sin sesión no se puede ejecutar editar_mis_datos", True)

    for k, lugar in (("profe", NOMBRE_B), ("admin", JSU["nombre"])):
        cc, r = entrar(lugar, M[k], CLAVE)
        r = cc.post("/perfil/datos", data={"nombre": "Docente", "apellido": k.title(), "telefono": "097000000",
                                            "fecha_nacimiento": "1985-02-03", "fecha_vencimiento_carne_salud": "2031-02-03"},
                    follow_redirects=False)
        p = perfil(U[k])
        ok(f"{k}: edita sus datos", r.status_code == 303 and p["nombre"] == "Docente" and p["telefono"] == "097000000"
           and p["fc"] == "2031-02-03")
        cc.post("/logout")

    cs, r = entrar("", M["super"], CLAVE, admin=True)
    ok("la superadministradora entra", r.status_code == 303, r.status_code)
    pag = cs.get("/perfil")
    ok("superadministradora: sin fechas de alumna ni cédula", pag.status_code == 200 and 'name="nombre"' in pag.text
       and 'name="fecha_nacimiento"' not in pag.text and "Cédula" not in pag.text)
    r = cs.post("/perfil/datos", data={"nombre": "Administración", "apellido": "General", "telefono": "096000000",
                                        "fecha_nacimiento": "1980-01-01"}, follow_redirects=False)
    p = perfil(uid_super)
    ok("superadministradora: edita nombre y teléfono (las fechas se ignoran)",
       r.status_code == 303 and p["nombre"] == "Administración" and p["telefono"] == "096000000" and p["fn"] is None)

    # ---------- cambio de correo ----------
    nuevo = f"nueva.{S}@ejemplo.test"
    mails_antes = len(mails_para(M["alumna"]))
    r = c.post("/perfil/correo", data={"nuevo": nuevo, "contrasena": "mal-" + CLAVE})
    ok("contraseña incorrecta: no se pide el cambio", r.status_code == 400 and "contraseña actual no es correcta" in r.text
       and not correo_auth(U["alumna"])["email_change"])
    r = c.post("/perfil/correo", data={"nuevo": M["otra"], "contrasena": CLAVE})
    ok("correo de otra cuenta: se rechaza", r.status_code == 400 and "ya pertenece a otra cuenta" in r.text, r.text[-300:])
    ok("correo de otra cuenta: no queda pendiente", not correo_auth(U["alumna"])["email_change"])
    r = c.post("/perfil/correo", data={"nuevo": M["alumna"].upper(), "contrasena": CLAVE})
    ok("el mismo correo: se rechaza", r.status_code == 400 and "Ese ya es tu correo" in r.text)
    r = c.post("/perfil/correo", data={"nuevo": f"x.{S}@algo.invalid", "contrasena": CLAVE})
    ok("un correo .invalid nuevo: se rechaza", r.status_code == 400 and ".invalid" in r.text)
    r = c.post("/perfil/correo", data={"nuevo": "sin-arroba", "contrasena": CLAVE})
    ok("un correo mal escrito: se rechaza", r.status_code == 400)

    cf, r = entrar(JSU["nombre"], FICTICIA, CLAVE)
    pf = cf.get("/perfil")
    ok("cuenta .invalid: no ofrece cambiar el correo", 'action="/perfil/correo"' not in pf.text)
    r = cf.post("/perfil/correo", data={"nuevo": f"real.{S}@ejemplo.test", "contrasena": CLAVE})
    ok("cuenta .invalid: el servidor tampoco lo permite", r.status_code == 400
       and not correo_auth(U["ficticia"])["email_change"])
    cf.post("/logout")

    r = c.post("/perfil/correo", data={"nuevo": "  " + nuevo.upper() + " ", "contrasena": CLAVE}, follow_redirects=False)
    ok("pedido válido: avisa que hay que abrir los dos enlaces", r.status_code == 303 and "abras los dos" in aviso(r))
    ok("la dirección del aviso no lleva los correos", "@" not in aviso(r))
    ok("queda pendiente en Supabase, sin cambiar todavía", correo_auth(U["alumna"]) == {"email": M["alumna"], "email_change": nuevo})
    ok("el perfil sigue con el correo actual", perfil(U["alumna"])["mail"] == M["alumna"])
    enlace_viejo, asunto, cuerpo = mail_nuevo(M["alumna"], mails_antes)
    enlace_nuevo, _, _ = mail_nuevo(nuevo, 0)
    ok("llega un correo a la dirección actual y otro a la nueva", bool(enlace_viejo and enlace_nuevo))
    ok("plantilla en español con las dos direcciones", asunto == "Confirmá el cambio de correo en Just Show Up"
       and M["alumna"] in cuerpo and nuevo in cuerpo)
    ok("los enlaces van a /confirmar-correo con el código después del #",
       all(e.startswith(f"{SITIO}/confirmar-correo#token_hash=") for e in (enlace_viejo, enlace_nuevo)), enlace_viejo)
    ok("Mi perfil muestra el cambio pendiente", "Pendiente de confirmación" in c.get("/perfil").text and nuevo in c.get("/perfil").text)
    _, r = entrar(JSU["nombre"], nuevo, CLAVE)
    ok("mientras está pendiente no se entra con el correo nuevo", r.status_code == 401)

    anonimo = TestClient(main.app, base_url=SITIO)
    g = anonimo.get("/confirmar-correo")
    ok("abrir la página no gasta el enlace", g.status_code == 200 and correo_auth(U["alumna"])["email_change"] == nuevo)
    ok("la página no manda el Referer", g.headers.get("referrer-policy") == "no-referrer" or 'content="no-referrer"' in g.text)
    r = anonimo.post("/confirmar-correo", data={"token_hash": "codigo-inventado"})
    ok("enlace inválido: mensaje claro", r.status_code == 400 and "venció o ya se usó" in r.text)
    r = anonimo.post("/confirmar-correo", data={"token_hash": codigo(enlace_viejo)})
    ok("primera confirmación: pide abrir el otro enlace", r.status_code == 200 and "abrí también el enlace" in r.text, r.text[-400:])
    ok("con una sola confirmación el correo NO cambia", correo_auth(U["alumna"])["email"] == M["alumna"]
       and perfil(U["alumna"])["mail"] == M["alumna"])
    r = anonimo.post("/confirmar-correo", data={"token_hash": codigo(enlace_viejo)})
    ok("el mismo enlace no sirve dos veces", r.status_code == 400)
    r = anonimo.post("/confirmar-correo", data={"token_hash": codigo(enlace_nuevo)})
    ok("segunda confirmación: el cambio se completa", r.status_code == 200 and nuevo in r.text and "Listo" in r.text, r.text[-400:])
    ok("auth.users tiene el correo nuevo", correo_auth(U["alumna"])["email"] == nuevo)
    ok("profiles.mail quedó sincronizado", perfil(U["alumna"])["mail"] == nuevo)
    ok("la confirmación no deja sesión en ese navegador", "sb_acceso" not in anonimo.cookies)

    _, r = entrar(JSU["nombre"], M["alumna"], CLAVE)
    ok("con el correo anterior ya no se entra", r.status_code == 401)
    c2, r = entrar(JSU["nombre"], nuevo, CLAVE)
    ok("con el correo nuevo y la misma contraseña se entra a la primera empresa", r.status_code == 303)
    ok("Mi perfil muestra el correo nuevo y sin pendiente", nuevo in c2.get("/perfil").text
       and "Pendiente de confirmación" not in c2.get("/perfil").text)
    cb, r = entrar(NOMBRE_B, nuevo, CLAVE)
    ok("y también a la otra empresa", r.status_code == 303)
    memb = sql("select id_empresa, es_alumna, activa from membresia where id_usuario = %s order by id_empresa", (U["alumna"],))
    ok("conserva cuenta, membresías y rol en las dos empresas",
       [(m["id_empresa"], m["es_alumna"], m["activa"]) for m in memb] == sorted([(JSU["id"], True, True), (B, True, True)]))
    ok("conserva la cédula de su empresa", sql("select cedula from membresia where id_usuario = %s and id_empresa = %s",
                                                (U["alumna"], JSU["id"]), uno=True)["cedula"] == "12345672")
    r = c2.post("/perfil/contrasena", data={"actual": CLAVE, "nueva": CLAVE + "x", "repetir": CLAVE + "x"}, follow_redirects=False)
    ok("cambiar la contraseña después funciona con el correo nuevo", r.status_code == 303 and "correctamente" in aviso(r))
    ca, r = entrar(JSU["nombre"], M["admin"], CLAVE)
    ficha = ca.get(f"/profesora/alumnas/{U['alumna']}").text
    ok("la administradora ve el correo nuevo en la ficha de la alumna", nuevo in ficha and M["alumna"] not in ficha)
    ca.post("/logout")
    ok("el correo de otra persona no cambió", correo_auth(U["otra"])["email"] == M["otra"])

    # enlace vencido (más de 1 hora desde el pedido)
    nuevo_v = f"vencido.{S}@ejemplo.test"
    cv, _ = entrar(JSU["nombre"], M["vence"], CLAVE)
    antes_v = len(mails_para(M["vence"]))
    cv.post("/perfil/correo", data={"nuevo": nuevo_v, "contrasena": CLAVE})
    e1, _, _ = mail_nuevo(M["vence"], antes_v)
    sql("update auth.users set email_change_sent_at = now() - interval '2 hours' where id = %s", (U["vence"],))
    r = anonimo.post("/confirmar-correo", data={"token_hash": codigo(e1)})
    ok("enlace vencido: mensaje claro y el correo no cambia", r.status_code == 400 and "venció" in r.text
       and correo_auth(U["vence"])["email"] == M["vence"])

    # pedir otro cambio reemplaza al anterior: los enlaces viejos dejan de servir
    cc2, _ = entrar(JSU["nombre"], M["cambia"], CLAVE)
    antes_c = len(mails_para(M["cambia"]))
    cc2.post("/perfil/correo", data={"nuevo": f"primero.{S}@ejemplo.test", "contrasena": CLAVE})
    viejo1, _, _ = mail_nuevo(M["cambia"], antes_c)
    time.sleep(61)  # Supabase permite un correo por persona por minuto (como en producción)
    r = cc2.post("/perfil/correo", data={"nuevo": f"segundo.{S}@ejemplo.test", "contrasena": CLAVE}, follow_redirects=False)
    ok("un segundo pedido reemplaza al primero", r.status_code == 303
       and correo_auth(U["cambia"])["email_change"] == f"segundo.{S}@ejemplo.test")
    r = anonimo.post("/confirmar-correo", data={"token_hash": codigo(viejo1)})
    ok("el enlace del primer pedido ya no sirve", r.status_code == 400 and correo_auth(U["cambia"])["email"] == M["cambia"])
    r = cc2.post("/perfil/correo", data={"nuevo": f"tercero.{S}@ejemplo.test", "contrasena": CLAVE})
    ok("pedidos seguidos: mensaje de esperar un minuto", r.status_code == 400 and "Esperá un minuto" in r.text, r.text[-300:])
finally:
    for uid in creadas:
        try:
            auth.cliente_admin().auth.admin.delete_user(uid)
        except Exception as e:
            print("No se pudo borrar una cuenta de prueba:", type(e).__name__)
    sql("delete from membresia where id_empresa = %s", (B,))
    sql("delete from empresa where id = %s", (B,))
    httpx.delete(f"{MAILPIT}/api/v1/messages")

print(f"\n{total - fallas}/{total} OK")
sys.exit(1 if fallas else 0)
