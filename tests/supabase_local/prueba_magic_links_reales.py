"""Pruebas REALES de «Entrar con un enlace por correo» (Magic Link) y «Olvidé mi contraseña» contra un Supabase LOCAL
(Colima + `supabase start` en la copia aislada ~/.local/share/jsu-supabase-local). Los mails no salen a ningún lado:
los recibe Mailpit, el buzón de prueba del Supabase local, y la prueba los lee por su API.

Usa el servicio de cuentas real (GoTrue), la API y la base con todas las migraciones del repo, y corre la app completa
por HTTP (TestClient) como `http://localhost:8000`, uno de los sitios permitidos. Crea sus propias cuentas
(`@ejemplo.test`, más una `.invalid`) y empresas con un sufijo al azar, y las borra al final.

NUNCA se conecta a producción: se niega a correr si la URL no es localhost.

Requisitos en la copia aislada (los trae supabase/config.toml del repo): plantilla `magic_link` con
supabase/templates/enlace_ingreso.html, `http://localhost:8000/**` en additional_redirect_urls y max_frequency 60s.

Uso: python tests/supabase_local/prueba_magic_links_reales.py
"""
import base64
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
from urllib.parse import parse_qs, urlsplit

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


def navegador():
    return TestClient(main.app, base_url=SITIO)


# ---------- Mailpit ----------

def mails_para(mail):
    r = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'to:"{mail}"'}).json()
    return r.get("messages") or []


def esperar_mail(mail, cantidad=1, segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        lista = mails_para(mail)
        if len(lista) >= cantidad:
            return lista
        time.sleep(0.3)
    return mails_para(mail)


def enlace_del_mail(id_mensaje):
    cuerpo = httpx.get(f"{MAILPIT}/api/v1/message/{id_mensaje}").json()
    m = re.search(r'href="([^"]+)"', cuerpo["HTML"])
    return html.unescape(m.group(1)) if m else None, cuerpo


def pedir_enlace(c, lugar, mail):
    return c.post("/entrar-con-enlace", data={"lugar": lugar, "mail": mail}, follow_redirects=False)


def enlace_nuevo(lugar, mail, c=None):
    """Pide el enlace y devuelve (url del mail, mensaje de Mailpit)."""
    antes = len(mails_para(mail))
    pedir_enlace(c or navegador(), lugar, mail)
    lista = esperar_mail(mail, antes + 1)
    if len(lista) <= antes:
        return None, None
    return enlace_del_mail(lista[0]["ID"])


def abrir_y_entrar(url, c=None, lugar_forzado=None):
    """Hace lo que hace la persona: abre el enlace (GET, no lo gasta) y toca «Entrar» (POST con el código del #)."""
    c = c or navegador()
    if not url:  # no llegó el mail: la prueba que lo use falla sin cortar las demás
        nada = type("SinRespuesta", (), {"status_code": 0, "text": "", "headers": {}})()
        return c, nada, nada
    partes = urlsplit(url)
    pagina = c.get(partes.path + ("?" + partes.query if partes.query else ""))
    token = parse_qs(partes.fragment).get("token_hash", [""])[0]
    lugar = lugar_forzado if lugar_forzado is not None else parse_qs(partes.query).get("lugar", [""])[0]
    r = c.post("/entrar-con-enlace/confirmar", data={"lugar": lugar, "token_hash": token}, follow_redirects=False)
    return c, pagina, r


def claims(token):
    carga = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))


def hash_clave(mail):
    return sql("select encrypted_password from auth.users where email = %s", (mail,), uno=True)["encrypted_password"]


def entrar_con_clave(lugar, mail, clave):
    c = navegador()
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c, r


def empresa_de(c):
    u = auth.usuario_de_acceso(c.cookies.get(auth.COOKIE_ACCESO))
    return u.empresa_codigo if u else None


# ---------- datos propios de esta prueba ----------
S = uuid.uuid4().hex[:6]
letras = lambda n: "".join(random.choice(string.ascii_uppercase) for _ in range(n))  # noqa: E731
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
httpx.delete(f"{MAILPIT}/api/v1/messages")

JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
COD_B, COD_C = letras(5), letras(5)
NOMBRE_B, NOMBRE_C = f"Pilates Prueba {S}", f"Estudio Cerrado {S}"
B = sql("insert into empresa (nombre, codigo) values (%s, %s) returning id", (NOMBRE_B, COD_B), uno=True)["id"]
C = sql("insert into empresa (nombre, codigo, activa) values (%s, %s, false) returning id", (NOMBRE_C, COD_C), uno=True)["id"]

M = {k: f"{k}.{S}@ejemplo.test" for k in ("alumna", "profesora", "admin", "dos", "solob", "inactiva", "cerrada",
                                           "super", "recupera", "frecuencia", "sesiones", "ajena",
                                           "noexiste")}
M["ficticia"] = f"ficticia.{S}@prueba.invalid"
creadas = []


def cuenta(clave_mail, empresa, rol, **kw):
    uid, _ = auth.crear_cuenta(M[clave_mail], "Prueba", "Enlace", contrasena=CLAVE, id_empresa=empresa,
                               rol_empresa=rol, **kw)
    creadas.append(uid)
    return uid


try:
    cuenta("alumna", JSU, "alumna")
    cuenta("profesora", JSU, "profesora")
    cuenta("admin", JSU, "admin")
    uid_dos = cuenta("dos", JSU, "alumna")
    sql("insert into membresia (id_usuario, id_empresa, es_alumna) values (%s, %s, true)", (uid_dos, B))
    cuenta("solob", B, "alumna")
    uid_inactiva = cuenta("inactiva", JSU, "alumna")
    sql("update membresia set activa = false where id_usuario = %s", (uid_inactiva,))
    cuenta("cerrada", C, "alumna")
    for k in ("recupera", "frecuencia", "sesiones", "ajena", "ficticia"):
        cuenta(k, JSU, "alumna")
    uid_super, _ = auth.crear_cuenta(M["super"], "Super", "Prueba", contrasena=CLAVE, sin_empresa=True)
    creadas.append(uid_super)
    auth.cliente_admin().table("profiles").update({"es_superadmin": True}).eq("id", uid_super).execute()
    usuarias_antes = sql("select count(*) n from auth.users", uno=True)["n"]
    claves_antes = {k: hash_clave(m) for k, m in M.items() if k != "noexiste"}

    # ---------- inicio de sesión con contraseña: sigue igual ----------
    for k, destino in (("alumna", "/"), ("profesora", "/profesora/horarios"), ("admin", "/administracion")):
        _, r = entrar_con_clave("Just Show Up", M[k], CLAVE)
        ok(f"contraseña: {k} entra y va a {destino}", r.status_code == 303 and r.headers["location"] == destino,
           f"{r.status_code} {r.headers.get('location')}")
    _, r = entrar_con_clave(NOMBRE_B, M["alumna"], CLAVE)
    ok("contraseña: lugar ajeno sigue dando 401 genérico", r.status_code == 401 and auth.MSG_INGRESO in r.text)
    sa = navegador()
    r = sa.post("/admin/login", data={"mail": M["super"], "contrasena": CLAVE}, follow_redirects=False)
    ok("contraseña: la superadministradora entra por /admin/login", r.status_code == 303
       and r.headers["location"] == "/admin/empresas")

    # ---------- enlace: alumna, profesora y administradora ----------
    for k, destino in (("alumna", "/"), ("profesora", "/profesora/horarios"), ("admin", "/administracion")):
        url, cuerpo = enlace_nuevo("Just Show Up", M[k])
        ok(f"enlace: a {k} le llega un mail desde Just Show Up", url is not None
           and cuerpo["Subject"] == "Tu enlace para entrar a Just Show Up")
        ok(f"enlace: el mail lleva a la página de Just Show Up con el código después del #", url is not None
           and url.startswith(f"{SITIO}/entrar-con-enlace/confirmar?lugar=Just") and "#token_hash=" in url, url)
        if not url:
            continue
        c, pagina, r = abrir_y_entrar(url)
        ok(f"enlace: {k} ve el botón «Entrar» antes de usar el enlace", pagina.status_code == 200
           and ">Entrar</button>" in pagina.text and pagina.headers.get("referrer-policy") == "no-referrer")
        ok(f"enlace: {k} entra y va a {destino}", r.status_code == 303 and r.headers.get("location") == destino,
           f"{r.status_code} {r.headers.get('location')}")
        ok(f"enlace: {k} queda en Just Show Up", empresa_de(c) == "JSU")
        amr = [m.get("method") for m in claims(c.cookies.get(auth.COOKIE_ACCESO)).get("amr", [])]
        print(f"     (amr de la sesión de enlace: {amr})")
        r2 = navegador().post("/entrar-con-enlace/confirmar", data={
            "lugar": "Just Show Up", "token_hash": parse_qs(urlsplit(url).fragment)["token_hash"][0]},
            follow_redirects=False)
        ok(f"enlace: el de {k} no sirve dos veces (401 sin cookies)", r2.status_code == 401
           and auth.COOKIE_ACCESO not in r2.headers.get("set-cookie", ""))

    # ---------- abrir el enlace (como un antivirus del correo) no lo gasta ----------
    url, _ = enlace_nuevo("Just Show Up", M["sesiones"])
    for _ in range(3):
        navegador().get(urlsplit(url).path + "?" + urlsplit(url).query)
    c_clave, r = entrar_con_clave("Just Show Up", M["sesiones"], CLAVE)  # sesión anterior, con contraseña
    c_enlace, _, r = abrir_y_entrar(url)
    ok("enlace: abrirlo varias veces sin tocar «Entrar» no lo gasta", r.status_code == 303)
    ok("enlace: la sesión que ya estaba abierta con contraseña sigue funcionando",
       c_clave.get("/perfil", follow_redirects=False).status_code == 200)
    ok("enlace: la nueva sesión también funciona", c_enlace.get("/perfil", follow_redirects=False).status_code == 200)
    uid_ses = sql("select id from auth.users where email = %s", (M["sesiones"],), uno=True)["id"]
    ok("enlace: hay dos sesiones distintas de esa persona, cada una con su lugar",
       sql("select count(*) n from sesion_empresa where id_usuario = %s", (uid_ses,), uno=True)["n"] == 2)

    # ---------- empresas y membresías ----------
    url, _ = enlace_nuevo(NOMBRE_B, M["dos"])
    c, _, r = abrir_y_entrar(url)
    ok("enlace: quien está en dos lugares entra al que escribió", r.status_code == 303 and empresa_de(c) == COD_B)
    url, _ = enlace_nuevo(NOMBRE_B, M["ajena"])
    _, _, r = abrir_y_entrar(url)
    ok("enlace: pedido para un lugar ajeno → 401 genérico", r.status_code == 401 and auth.MSG_ENLACE_INVALIDO in r.text
       and "set-cookie" not in r.headers)
    url, _ = enlace_nuevo(NOMBRE_B, M["solob"])
    _, _, r = abrir_y_entrar(url, lugar_forzado="Just Show Up")
    ok("enlace: cambiar el lugar en la URL a otra empresa no deja entrar (401)", r.status_code == 401
       and "set-cookie" not in r.headers)
    uid_solob = sql("select id from auth.users where email = %s", (M["solob"],), uno=True)["id"]
    ok("enlace: rechazado → la sesión que abrió el enlace se cerró en Supabase",
       sql("select count(*) n from auth.sessions where user_id = %s", (uid_solob,), uno=True)["n"] == 0)
    for k, lugar, que in (("inactiva", "Just Show Up", "membresía inactiva"), ("cerrada", NOMBRE_C, "empresa inactiva"),
                          ("super", "Just Show Up", "superadministradora (solo contraseña)")):
        url, _ = enlace_nuevo(lugar, M[k])
        r = abrir_y_entrar(url)[2] if url else None
        ok(f"enlace: {que} → 401 genérico", r is not None and r.status_code == 401
           and auth.MSG_ENLACE_INVALIDO in r.text and "set-cookie" not in r.headers)

    # ---------- no revela cuentas, no crea cuentas, no manda a .invalid ----------
    c = navegador()
    r_existe = pedir_enlace(c, "Just Show Up", M["frecuencia"])
    time.sleep(1)
    r_otra_vez = pedir_enlace(c, "Just Show Up", M["frecuencia"])  # antes de 60 s: Supabase lo frena
    r_no_existe = pedir_enlace(c, "Just Show Up", M["noexiste"])
    r_ficticia = pedir_enlace(c, "Just Show Up", M["ficticia"])
    textos = {r.text.replace(m, "MAIL") for r, m in ((r_existe, M["frecuencia"]), (r_otra_vez, M["frecuencia"]),
                                                     (r_no_existe, M["noexiste"]), (r_ficticia, M["ficticia"]))}
    ok("enlace: misma respuesta (200) para cuenta existente, pedido repetido, inexistente y .invalid",
       len(textos) == 1 and {r.status_code for r in (r_existe, r_otra_vez, r_no_existe, r_ficticia)} == {200})
    time.sleep(2)
    ok("enlace: el pedido repetido antes de 60 s no manda un segundo mail", len(mails_para(M["frecuencia"])) == 1)
    ok("enlace: a un mail inexistente no le llega nada", mails_para(M["noexiste"]) == [])
    ok("enlace: a una cuenta .invalid no se le manda nada", mails_para(M["ficticia"]) == [])
    ok("enlace: no se creó ninguna cuenta", sql("select count(*) n from auth.users", uno=True)["n"] == usuarias_antes
       and not sql("select 1 from auth.users where email = %s", (M["noexiste"],)))
    ok("/admin/login no ofrece el enlace", "/entrar-con-enlace" not in navegador().get("/admin/login").text)

    # ---------- contraseñas: el enlace no las cambia ----------
    ok("contraseñas: ninguna cambió por entrar con enlace",
       all(hash_clave(m) == claves_antes[k] for k, m in M.items() if k != "noexiste"))
    _, r = entrar_con_clave("Just Show Up", M["alumna"], CLAVE)
    ok("contraseñas: después de entrar con enlace, la alumna sigue entrando con su contraseña", r.status_code == 303)
    c_enl, _, _ = abrir_y_entrar(enlace_nuevo("Just Show Up", M["recupera"])[0])
    r = c_enl.post("/perfil/contrasena", data={"actual": "otra-cosa", "nueva": "Nueva-12345", "repetir": "Nueva-12345"},
                   follow_redirects=False)
    ok("contraseñas: con una sesión de enlace, Mi perfil sigue pidiendo la contraseña actual", r.status_code == 400
       and hash_clave(M["recupera"]) == claves_antes["recupera"])
    r = navegador().post("/restablecer-contrasena", data={
        "acceso": c_enl.cookies.get(auth.COOKIE_ACCESO), "refresco": c_enl.cookies.get(auth.COOKIE_REFRESCO),
        "nueva": "Nueva-12345", "repetir": "Nueva-12345"}, follow_redirects=False)
    ok("contraseñas: los datos de una sesión de enlace no sirven en «restablecer contraseña»", r.status_code == 400
       and auth.MSG_LINK_INVALIDO in r.text and hash_clave(M["recupera"]) == claves_antes["recupera"])

    # ---------- olvidé mi contraseña: de punta a punta ----------
    antes = len(mails_para(M["recupera"]))
    time.sleep(61)  # la persona pidió un enlace hace menos de un minuto
    r_ped = r = navegador().post("/olvide-contrasena", data={"mail": M["recupera"]})
    r_rep = navegador().post("/olvide-contrasena", data={"mail": M["recupera"]})  # repetido: Supabase lo frena
    lista = esperar_mail(M["recupera"], antes + 1)
    time.sleep(2)
    lista = mails_para(M["recupera"])
    ok("recuperación: el pedido responde como siempre y llega un mail", r.status_code == 200
       and "te enviamos un mail" in r.text and len(lista) >= antes + 1)
    ok("recuperación: el pedido repetido responde igual y no manda otro mail",
       r_rep.text == r.text and len(lista) == antes + 1)
    url_rec, cuerpo = enlace_del_mail(lista[0]["ID"]) if len(lista) > antes else (None, None)
    if url_rec:
        salto = httpx.get(url_rec, follow_redirects=False)
        destino = salto.headers.get("location", "")
        frag = parse_qs(urlsplit(destino).fragment)
        ok("recuperación: el link lleva a /restablecer-contrasena con la sesión en el #",
           destino.startswith(f"{SITIO}/restablecer-contrasena") and frag.get("type") == ["recovery"], destino[:80])
        acceso, refresco = frag.get("access_token", [""])[0], frag.get("refresh_token", [""])[0]
        if acceso:
            print(f"     (amr de la sesión de recuperación: {[m.get('method') for m in claims(acceso).get('amr', [])]})")
        CLAVE_NUEVA = "ClaveNueva-" + uuid.uuid4().hex[:8]
        r = navegador().post("/restablecer-contrasena", data={"acceso": acceso, "refresco": refresco,
                                                               "nueva": CLAVE_NUEVA, "repetir": CLAVE_NUEVA},
                             follow_redirects=False)
        ok("recuperación: elige la contraseña nueva", r.status_code == 303 and "ok=1" in r.headers.get("location", ""))
        ok("recuperación: entra con la contraseña nueva",
           entrar_con_clave("Just Show Up", M["recupera"], CLAVE_NUEVA)[1].status_code == 303)
        ok("recuperación: la anterior ya no sirve",
           entrar_con_clave("Just Show Up", M["recupera"], CLAVE)[1].status_code == 401)
        r = navegador().post("/restablecer-contrasena", data={"acceso": acceso, "refresco": refresco,
                                                               "nueva": "OtraMas-12345", "repetir": "OtraMas-12345"})
        ok("recuperación: el mismo link no sirve dos veces", r.status_code == 400)
    else:
        ok("recuperación: el mail trae el link", False)
    r1 = navegador().post("/olvide-contrasena", data={"mail": M["noexiste"]})
    r3 = navegador().post("/olvide-contrasena", data={"mail": M["ficticia"]})
    textos = {r1.text.replace(M["noexiste"], "MAIL"), r_ped.text.replace(M["recupera"], "MAIL"),
              r_rep.text.replace(M["recupera"], "MAIL"), r3.text.replace(M["ficticia"], "MAIL")}
    ok("recuperación: misma respuesta para existente, inexistente, repetido y .invalid (ya no dice «Ya pediste varios links»)",
       len(textos) == 1 and r1.status_code == r_rep.status_code == r3.status_code == 200)
    time.sleep(2)
    ok("recuperación: a inexistentes y .invalid no les llega nada",
       mails_para(M["noexiste"]) == [] and mails_para(M["ficticia"]) == [])
    ok("contraseñas: solo cambió la de quien la recuperó",
       all(hash_clave(m) == claves_antes[k] for k, m in M.items() if k not in ("noexiste", "recupera")))
finally:
    for uid in creadas:
        sql("delete from sesion_empresa where id_usuario = %s", (uid,))
        try:
            auth.cliente_admin().auth.admin.delete_user(uid)
        except Exception as e:
            print("no se pudo borrar", uid, e)
    sql("delete from membresia where id_empresa in (%s, %s)", (B, C))
    sql("delete from empresa where id in (%s, %s)", (B, C))
    httpx.delete(f"{MAILPIT}/api/v1/messages")
    quedan = sql("select count(*) n from auth.users where email like %s", (f"%.{S}@%",), uno=True)["n"]
    print(f"limpieza: quedan {quedan} cuentas de esta prueba")

print(f"\n{total - fallas}/{total} OK")
sys.exit(1 if fallas else 0)
