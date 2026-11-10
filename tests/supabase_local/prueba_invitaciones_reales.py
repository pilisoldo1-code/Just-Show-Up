"""Pruebas REALES de las invitaciones por correo (cuenta nueva → la persona configura su contraseña) y de «Olvidé mi
contraseña» contra un Supabase LOCAL (Colima + `supabase start` en la copia aislada ~/.local/share/jsu-supabase-local).
Los mails no salen a ningún lado: los recibe Mailpit, el buzón de prueba del Supabase local, y la prueba los lee por su API.

Usa el servicio de cuentas real (GoTrue), la API y la base con todas las migraciones del repo, y corre la app completa
por HTTP (TestClient) como `http://localhost:8000`, uno de los sitios permitidos. Crea sus propias cuentas
(`@ejemplo.test`) y una empresa con un sufijo al azar, y las borra al final.

NUNCA se conecta a producción: se niega a correr si la URL no es localhost.

Requisitos en la copia aislada (los trae supabase/config.toml del repo): plantilla `invite` con
supabase/templates/invitacion.html y `http://localhost:8000/**` en additional_redirect_urls.

Uso: python tests/supabase_local/prueba_invitaciones_reales.py
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


def mails_para(mail):
    return httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'to:"{mail}"'}).json().get("messages") or []


def esperar_mail(mail, cantidad=1, segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        lista = mails_para(mail)
        if len(lista) >= cantidad:
            return lista
        time.sleep(0.3)
    return mails_para(mail)


def mail_nuevo(mail, antes):
    """(enlace, asunto) del mail más nuevo si llegó uno más que `antes`."""
    lista = esperar_mail(mail, antes + 1)
    if len(lista) <= antes:
        return None, None
    cuerpo = httpx.get(f"{MAILPIT}/api/v1/message/{lista[0]['ID']}").json()
    m = re.search(r'href="([^"]+)"', cuerpo["HTML"])
    return (html.unescape(m.group(1)) if m else None), cuerpo["Subject"]


def codigo(enlace):
    return parse_qs(urlsplit(enlace).fragment).get("token_hash", [""])[0] if enlace else ""


def activar(token, clave):
    c = navegador()
    pagina = c.get("/activar-cuenta")
    r = c.post("/activar-cuenta", data={"token_hash": token, "nueva": clave, "repetir": clave}, follow_redirects=False)
    return pagina, r


def entrar(lugar, mail, clave):
    c = navegador()
    r = c.post("/login", data={"lugar": lugar, "mail": mail, "contrasena": clave}, follow_redirects=False)
    return c, r


def cuenta(mail):
    return sql("select id, email_confirmed_at, invited_at, encrypted_password from auth.users where email = %s", (mail,), uno=True)


def aviso(r):
    return unquote_plus(r.headers.get("location", ""))


TEMPORAL = re.compile(r"credencial-clave|contrase(ñ|n)a temporal", re.I)


# ---------- datos propios de esta prueba ----------
S = uuid.uuid4().hex[:6]
CLAVE = "ClaveDePrueba-" + uuid.uuid4().hex[:8]
httpx.delete(f"{MAILPIT}/api/v1/messages")
JSU = sql("select id from empresa where codigo = 'JSU'", uno=True)["id"]
NOMBRE_B = f"Pilates Prueba {S}"
B = sql("insert into empresa (nombre, codigo) values (%s, %s) returning id",
        (NOMBRE_B, "".join(random.choice(string.ascii_uppercase) for _ in range(5))), uno=True)["id"]
M = {k: f"{k}.{S}@ejemplo.test" for k in ("admin", "super", "nueva", "typo", "corregida", "deb", "adminb", "vence",
                                           "olvido", "cortada")}
creadas = []
try:
    for k, empresa, rol in (("admin", JSU, "admin"), ("deb", B, "alumna")):
        uid, _ = auth.crear_cuenta(M[k], "Prueba", k.title(), contrasena=CLAVE, id_empresa=empresa, rol_empresa=rol)
        creadas.append(uid)
    uid_super, _ = auth.crear_cuenta(M["super"], "Super", "Prueba", contrasena=CLAVE, sin_empresa=True)
    creadas.append(uid_super)
    auth.cliente_admin().table("profiles").update({"es_superadmin": True}).eq("id", uid_super).execute()
    hash_deb = cuenta(M["deb"])["encrypted_password"]
    adm, r = entrar("Just Show Up", M["admin"], CLAVE)
    ok("la administradora entra con su contraseña como siempre", r.status_code == 303 and r.headers["location"] == "/administracion")

    # ---------- alta de una cuenta nueva: invitación, sin contraseña visible ----------
    r = adm.post("/profesora/alumnas", data={"nombre": "Nueva", "apellido": "Alumna", "mail": M["nueva"],
                                             "telefono": "099123456", "rol": "alumna"}, follow_redirects=False)
    ok("crear alumna: vuelve a la lista avisando que se envió la invitación", r.status_code == 303
       and "invitación" in aviso(r) and M["nueva"] in aviso(r), aviso(r))
    lista = adm.get(r.headers["location"])
    ok("ninguna pantalla muestra una contraseña temporal", not TEMPORAL.search(lista.text) and not TEMPORAL.search(aviso(r)))
    c_nueva = cuenta(M["nueva"])
    creadas.append(c_nueva["id"])
    ok("la cuenta queda pendiente: invitada, sin confirmar y sin contraseña",
       c_nueva["invited_at"] and not c_nueva["email_confirmed_at"] and not c_nueva["encrypted_password"])
    m_nueva = sql("select es_alumna, es_profesora, es_admin, activa from membresia where id_usuario = %s", (c_nueva["id"],))
    ok("con su membresía de alumna SOLO en Just Show Up", m_nueva == [{"es_alumna": True, "es_profesora": False,
                                                                        "es_admin": False, "activa": True}], m_nueva)
    ok("y los datos del alta guardados", sql("select telefono from profiles where id = %s", (c_nueva["id"],), uno=True)
       == {"telefono": "099123456"})
    enlace1, asunto = mail_nuevo(M["nueva"], 0)
    ok("le llega el correo de invitación en español con el enlace a /activar-cuenta",
       asunto == "Te invitaron a Just Show Up" and enlace1 and enlace1.startswith(f"{SITIO}/activar-cuenta#token_hash="),
       (asunto, enlace1))
    _, r = entrar("Just Show Up", M["nueva"], CLAVE)
    ok("una cuenta pendiente no puede iniciar sesión (401 genérico)", r.status_code == 401 and auth.MSG_INGRESO in r.text)
    ficha = adm.get(f"/profesora/alumnas/{c_nueva['id']}")
    ok("la ficha la muestra pendiente con «Reenviar invitación» y «Anular invitación»",
       "Pendiente de activación" in ficha.text and "Reenviar invitación" in ficha.text and "Anular invitación" in ficha.text
       and "Enviar correo para cambiar la contraseña" not in ficha.text)
    ok("y su estado dice que todavía no puede iniciar sesión",
       "Pendiente de activación: todavía no puede iniciar sesión" in ficha.text and "Activa: puede iniciar sesión" not in ficha.text)

    # ---------- reenviar: misma cuenta, el enlace anterior deja de servir ----------
    r = adm.post(f"/profesora/alumnas/{c_nueva['id']}/reenviar-invitacion", follow_redirects=False)
    enlace2, _ = mail_nuevo(M["nueva"], 1)
    ok("reenviar manda otro correo", "ok=1" in r.headers.get("location", "") and enlace2 and codigo(enlace2) != codigo(enlace1))
    ok("sin duplicar la cuenta ni la membresía", sql("select count(*) n from auth.users where email = %s", (M["nueva"],), uno=True)["n"] == 1
       and sql("select count(*) n from membresia where id_usuario = %s", (c_nueva["id"],), uno=True)["n"] == 1)
    _, r = activar(codigo(enlace1), "ClaveElegida-111")
    ok("el enlace anterior ya no sirve", r.status_code == 400 and auth.MSG_INVITACION_INVALIDA in r.text)

    # ---------- activar con el enlace vigente ----------
    for _ in range(3):  # un antivirus del correo abriendo la página no gasta el enlace
        navegador().get("/activar-cuenta")
    _, r = activar(codigo(enlace2), "corta")
    ok("una contraseña demasiado corta no gasta el enlace", r.status_code == 400 and f'value="{codigo(enlace2)}"' in r.text)
    pagina, r = activar(codigo(enlace2), "ClaveElegida-111")
    ok("la página tiene el formulario y no comparte el enlace", pagina.status_code == 200
       and "Guardar contraseña" in pagina.text and pagina.headers.get("referrer-policy") == "no-referrer")
    ok("configura su contraseña y la manda a iniciar sesión", r.status_code == 303 and r.headers["location"].startswith("/login?ok=1"))
    c, r = entrar("just show up", M["nueva"], "ClaveElegida-111")
    ok("después entra normalmente con lugar, mail y su contraseña", r.status_code == 303 and r.headers["location"] == "/")
    _, r = entrar(NOMBRE_B, M["nueva"], "ClaveElegida-111")
    ok("la invitación de Just Show Up no da acceso a otra empresa (401)", r.status_code == 401)
    _, r = activar(codigo(enlace2), "OtraClave-222")
    ok("el mismo enlace no sirve dos veces", r.status_code == 400 and auth.MSG_INVITACION_INVALIDA in r.text)
    ok("y la contraseña sigue siendo la que eligió", entrar("Just Show Up", M["nueva"], "ClaveElegida-111")[1].status_code == 303)
    ficha = adm.get(f"/profesora/alumnas/{c_nueva['id']}")
    ok("ya activa: la ficha ofrece el correo para cambiar la contraseña, no reenviar",
       "Pendiente de activación" not in ficha.text and "Enviar correo para cambiar la contraseña" in ficha.text)
    r = adm.post(f"/profesora/alumnas/{c_nueva['id']}/reenviar-invitacion", follow_redirects=False)
    ok("a una cuenta activa no se le puede reenviar la invitación", "ok=0" in r.headers["location"] and "activa" in aviso(r))
    r = adm.post(f"/profesora/alumnas/{c_nueva['id']}/anular-invitacion", follow_redirects=False)
    ok("ni anular", "ok=0" in r.headers["location"] and cuenta(M["nueva"]) is not None)

    # ---------- «Reiniciar contraseña» ahora es un correo; la contraseña actual sigue ----------
    antes = len(mails_para(M["nueva"]))
    r = adm.post(f"/profesora/alumnas/{c_nueva['id']}/reiniciar-contrasena", data={"desde": "alumnas"}, follow_redirects=False)
    enlace_rec, _ = mail_nuevo(M["nueva"], antes)
    ok("«Enviar correo para cambiar la contraseña» manda el correo de recuperación y no muestra ninguna contraseña",
       "ok=1" in r.headers["location"] and enlace_rec and "/auth/v1/verify" in enlace_rec and not TEMPORAL.search(aviso(r)),
       (aviso(r), enlace_rec))
    ok("su contraseña actual sigue funcionando", entrar("Just Show Up", M["nueva"], "ClaveElegida-111")[1].status_code == 303)
    salto = httpx.get(enlace_rec, follow_redirects=False).headers.get("location", "") if enlace_rec else ""
    frag = parse_qs(urlsplit(salto).fragment)
    r = navegador().post("/restablecer-contrasena", data={"acceso": frag.get("access_token", [""])[0],
                                                          "refresco": frag.get("refresh_token", [""])[0],
                                                          "nueva": "ClaveNueva-333", "repetir": "ClaveNueva-333"},
                         follow_redirects=False)
    ok("el correo lleva al restablecer de siempre y la contraseña nueva funciona",
       salto.startswith(f"{SITIO}/restablecer-contrasena") and r.status_code == 303
       and entrar("Just Show Up", M["nueva"], "ClaveNueva-333")[1].status_code == 303)

    # ---------- invitación vencida ----------
    adm.post("/profesora/alumnas", data={"nombre": "Vence", "apellido": "Prueba", "mail": M["vence"], "rol": "profesora"})
    c_vence = cuenta(M["vence"])
    creadas.append(c_vence["id"])
    enlace_v, _ = mail_nuevo(M["vence"], 0)
    sql("update auth.users set confirmation_sent_at = now() - interval '2 hours', invited_at = now() - interval '2 hours' "
        "where id = %s", (c_vence["id"],))
    _, r = activar(codigo(enlace_v), "ClaveElegida-111")
    ok("una invitación de más de 1 hora no sirve", r.status_code == 400 and auth.MSG_INVITACION_INVALIDA in r.text)
    ok("y la cuenta sigue pendiente, sin contraseña", not cuenta(M["vence"])["encrypted_password"])
    r = adm.post(f"/profesora/profesoras/{c_vence['id']}/reenviar-invitacion", follow_redirects=False)
    enlace_v2, _ = mail_nuevo(M["vence"], 1)
    _, r2 = activar(codigo(enlace_v2), "ClaveElegida-111")
    ok("reenviada, la nueva sí sirve (profesora)", "ok=1" in r.headers["location"] and r2.status_code == 303
       and entrar("Just Show Up", M["vence"], "ClaveElegida-111")[1].headers.get("location") == "/profesora/horarios")

    # ---------- cuenta existente de otra empresa: conserva todo ----------
    antes = len(mails_para(M["deb"]))
    r = adm.post("/profesora/alumnas", data={"nombre": "Prueba", "apellido": "Deb", "mail": M["deb"], "rol": "alumna"},
                 follow_redirects=False)
    time.sleep(2)
    ok("una alumna de otra empresa se suma con su misma contraseña y sin correo",
       r.status_code == 303 and "misma contraseña" in aviso(r) and "No se le envió ningún correo" in aviso(r)
       and len(mails_para(M["deb"])) == antes, aviso(r))
    ok("su contraseña no cambió", cuenta(M["deb"])["encrypted_password"] == hash_deb)
    ok("entra a las dos empresas con sus credenciales de siempre",
       entrar(NOMBRE_B, M["deb"], CLAVE)[1].status_code == 303 and entrar("Just Show Up", M["deb"], CLAVE)[1].status_code == 303)
    roles = {f["id_empresa"]: f["es_alumna"] for f in sql("select id_empresa, es_alumna from membresia where id_usuario = "
                                                           "(select id from auth.users where email = %s)", (M["deb"],))}
    ok("con un rol en cada empresa", roles == {JSU: True, B: True}, roles)

    # ---------- mail mal escrito: anular y volver a crear ----------
    adm.post("/profesora/alumnas", data={"nombre": "Typo", "apellido": "Prueba", "mail": M["typo"], "rol": "alumna"})
    c_typo = cuenta(M["typo"])
    r = adm.post(f"/profesora/alumnas/{c_typo['id']}/anular-invitacion", follow_redirects=False)
    ok("anular una invitación pendiente borra esa cuenta y su membresía", "ok=1" in r.headers["location"]
       and cuenta(M["typo"]) is None and not sql("select 1 from membresia where id_usuario = %s", (c_typo["id"],)))
    r = adm.post("/profesora/alumnas", data={"nombre": "Typo", "apellido": "Prueba", "mail": M["corregida"], "rol": "alumna"},
                 follow_redirects=False)
    c_ok = cuenta(M["corregida"])
    creadas.append(c_ok["id"])
    ok("y se crea de nuevo con el mail correcto", "ok=1" in r.headers["location"] and mail_nuevo(M["corregida"], 0)[0])

    # ---------- .invalid ----------
    ficticia = f"ficticia.{S}@prueba.invalid"
    r = adm.post("/profesora/alumnas", data={"nombre": "Ficticia", "apellido": "Prueba", "mail": ficticia, "rol": "alumna"})
    ok("un mail .invalid nuevo no se crea desde la pantalla", r.status_code == 400 and auth.MSG_MAIL_FICTICIO in html.unescape(r.text)
       and cuenta(ficticia) is None)

    # ---------- activación interrumpida: «Olvidé mi contraseña» basta, sin la administradora ----------
    def recuperar(mail, clave):
        antes = len(mails_para(mail))
        navegador().post("/olvide-contrasena", data={"mail": mail})
        enlace, _ = mail_nuevo(mail, antes)
        salto = httpx.get(enlace, follow_redirects=False).headers.get("location", "") if enlace else ""
        frag = parse_qs(urlsplit(salto).fragment)
        r = navegador().post("/restablecer-contrasena", data={"acceso": frag.get("access_token", [""])[0],
                                                              "refresco": frag.get("refresh_token", [""])[0],
                                                              "nueva": clave, "repetir": clave}, follow_redirects=False)
        return enlace, r

    adm.post("/profesora/alumnas", data={"nombre": "Olvido", "apellido": "Prueba", "mail": M["olvido"], "rol": "alumna"})
    c_olv = cuenta(M["olvido"])
    creadas.append(c_olv["id"])
    enlace_o, r = recuperar(M["olvido"], "PorOlvide-444")
    ok("nunca abrió la invitación: «Olvidé mi contraseña» le manda el correo y elige su contraseña",
       enlace_o is not None and r.status_code == 303, (enlace_o, r.status_code))
    ok("y entra normalmente, sin que intervenga la administradora",
       entrar("Just Show Up", M["olvido"], "PorOlvide-444")[1].headers.get("location") == "/")
    adm.post("/profesora/alumnas", data={"nombre": "Cortada", "apellido": "Prueba", "mail": M["cortada"], "rol": "alumna"})
    c_cor = cuenta(M["cortada"])
    creadas.append(c_cor["id"])
    enlace_c, _ = mail_nuevo(M["cortada"], 0)
    auth.cliente_nuevo().auth.verify_otp({"token_hash": codigo(enlace_c), "type": "invite"})  # se cortó: canjeó y no guardó
    estado_cor = cuenta(M["cortada"])
    reuso = activar(codigo(enlace_c), "Cortada-555")[1].status_code
    ok("activación cortada: el enlace ya se usó y ella no tiene ninguna contraseña que conozca",
       bool(estado_cor["email_confirmed_at"]) and reuso == 400
       and entrar("Just Show Up", M["cortada"], "Cortada-555")[1].status_code == 401, (bool(estado_cor["email_confirmed_at"]), reuso))
    r = adm.post(f"/profesora/alumnas/{c_cor['id']}/reenviar-invitacion", follow_redirects=False)
    ok("(ya no figura pendiente: reenviar se rechaza con un mensaje que lleva a «Olvidé mi contraseña»)",
       "ok=0" in r.headers["location"] and "Olvidé mi contraseña" in aviso(r), aviso(r))
    enlace_c2, r = recuperar(M["cortada"], "Cortada-555")
    ok("con «Olvidé mi contraseña» recupera el acceso sola", enlace_c2 is not None and r.status_code == 303
       and entrar("Just Show Up", M["cortada"], "Cortada-555")[1].headers.get("location") == "/")

    # ---------- superadministradora: administradora nueva por invitación ----------
    sa = navegador()
    sa.post("/admin/login", data={"mail": M["super"], "contrasena": CLAVE})
    r = sa.post(f"/admin/empresas/{B}/administradora", data={"nombre": "Admin", "apellido": "Bee", "mail": M["adminb"]},
                follow_redirects=False)
    c_ab = cuenta(M["adminb"])
    if c_ab:
        creadas.append(c_ab["id"])
    enlace_ab, _ = mail_nuevo(M["adminb"], 0)
    ok("la superadministradora crea una administradora: invitación, sin contraseña visible",
       "ok=1" in r.headers["location"] and "invitación" in aviso(r) and enlace_ab and not TEMPORAL.search(aviso(r)), aviso(r))
    pagina = sa.get(f"/admin/empresas/{B}")
    ok("la ve pendiente con «Reenviar invitación»", "Pendiente de activación" in pagina.text and "Reenviar invitación" in pagina.text)
    r = sa.post(f"/admin/empresas/{B}/administradoras/{c_ab['id']}/reenviar-invitacion", follow_redirects=False)
    enlace_ab2, _ = mail_nuevo(M["adminb"], 1)
    _, r2 = activar(codigo(enlace_ab2), "ClaveElegida-111")
    ok("reenviada y activada, entra a SU empresa como administradora y a ninguna otra",
       "ok=1" in r.headers["location"] and r2.status_code == 303
       and entrar(NOMBRE_B, M["adminb"], "ClaveElegida-111")[1].headers.get("location") == "/administracion"
       and entrar("Just Show Up", M["adminb"], "ClaveElegida-111")[1].status_code == 401)

    # ---------- olvidé mi contraseña: igual que siempre ----------
    textos = set()
    for m in (M["corregida"], f"noexiste.{S}@ejemplo.test", ficticia):
        rr = navegador().post("/olvide-contrasena", data={"mail": m})
        textos.add((rr.status_code, rr.text.replace(m, "MAIL")))
    ok("«Olvidé mi contraseña» responde igual exista o no la cuenta (también una pendiente)", len(textos) == 1)
    ok("el ingreso con enlace ya no existe", navegador().get("/entrar-con-enlace").status_code == 404
       and "entrar-con-enlace" not in navegador().get("/login").text)
    ok("la cuenta de la administradora no cambió su contraseña", entrar("Just Show Up", M["admin"], CLAVE)[1].status_code == 303)
finally:
    for uid in set(filter(None, creadas)):
        sql("delete from sesion_empresa where id_usuario = %s", (uid,))
        try:
            auth.cliente_admin().auth.admin.delete_user(uid)
        except Exception as e:
            print("no se pudo borrar", uid, e)
    sql("delete from membresia where id_empresa = %s", (B,))
    sql("delete from empresa where id = %s", (B,))
    httpx.delete(f"{MAILPIT}/api/v1/messages")
    quedan = sql("select count(*) n from auth.users where email like %s", (f"%.{S}@%",), uno=True)["n"]
    print(f"limpieza: quedan {quedan} cuentas de esta prueba")

print(f"\n{total - fallas}/{total} OK")
sys.exit(1 if fallas else 0)
