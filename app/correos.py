"""Correos automáticos (notificaciones, fases B y C): el despachador.

pg_cron llama cada minuto a `notificaciones_tick()` en la base; si hay avisos pendientes, la base avisa con pg_net a
POST /tareas/notificaciones (con el secreto de Vault). Este módulo toma un lote con `notificaciones_reclamar` (la base
ya revalidó cada aviso, la preferencia de la persona, su membresía y los topes), arma cada correo, lo manda por SMTP con
UNA sola conexión y anota el resultado con `notificaciones_resultado`.

Configuración (solo variables de entorno del servidor; nunca en el repositorio ni en la base):
- NOTIFICACIONES_SECRETO: el mismo valor guardado en Vault como `notificaciones_secreto` (32 caracteres o más).
- SMTP_HOST, SMTP_PORT, SMTP_USUARIO, SMTP_CONTRASENA, SMTP_REMITENTE y SMTP_SEGURIDAD (ssl | starttls | ninguna).
  Gmail: smtp.gmail.com, 465, ssl, la dirección de la cuenta y una contraseña de aplicación.
  «ninguna» (sin cifrar) solo se acepta contra este mismo equipo (Mailpit en las pruebas locales).
Si falta algo, el despachador no hace nada (responde 503) y los avisos esperan en la cola.
"""
import hmac
import logging
import os
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from app import clases, facturas

LOTE = 20
PRESUPUESTO_SEG = 8  # Vercel Hobby corta a los 10 s: lo que no entra, vuelve a la cola sin contar intento
HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}
SEGURIDADES = {"ssl", "starttls", "ninguna"}


class SinConfigurar(Exception):
    pass


@dataclass(frozen=True)
class Config:
    host: str
    puerto: int
    usuario: str
    contrasena: str
    remitente: str
    seguridad: str
    secreto: str

    def __repr__(self) -> str:  # nunca mostrar la contraseña ni el secreto en registros
        return f"Config(host={self.host!r}, puerto={self.puerto}, seguridad={self.seguridad!r})"


def configuracion() -> Config:
    v = {k: (os.environ.get(k) or "").strip() for k in
         ("NOTIFICACIONES_SECRETO", "SMTP_HOST", "SMTP_PORT", "SMTP_USUARIO", "SMTP_CONTRASENA", "SMTP_REMITENTE",
          "SMTP_SEGURIDAD")}
    if len(v["NOTIFICACIONES_SECRETO"]) < 32:
        raise SinConfigurar("falta NOTIFICACIONES_SECRETO (32 caracteres o más)")
    if not v["SMTP_HOST"] or not v["SMTP_REMITENTE"] or "@" not in v["SMTP_REMITENTE"]:
        raise SinConfigurar("falta SMTP_HOST o SMTP_REMITENTE")
    seguridad = v["SMTP_SEGURIDAD"].lower() or "ssl"
    if seguridad not in SEGURIDADES:
        raise SinConfigurar("SMTP_SEGURIDAD inválida")
    if seguridad == "ninguna" and v["SMTP_HOST"].lower() not in HOSTS_LOCALES:
        raise SinConfigurar("sin cifrar solo se admite contra este mismo equipo")
    if seguridad != "ninguna" and not (v["SMTP_USUARIO"] and v["SMTP_CONTRASENA"]):
        raise SinConfigurar("faltan SMTP_USUARIO o SMTP_CONTRASENA")
    try:
        puerto = int(v["SMTP_PORT"] or (465 if seguridad == "ssl" else 587))
    except ValueError:
        raise SinConfigurar("SMTP_PORT inválido") from None
    return Config(v["SMTP_HOST"], puerto, v["SMTP_USUARIO"], v["SMTP_CONTRASENA"], v["SMTP_REMITENTE"], seguridad,
                  v["NOTIFICACIONES_SECRETO"])


def secreto_valido(cabecera: str, secreto: str) -> bool:
    return bool(secreto) and hmac.compare_digest(cabecera.encode(), f"Bearer {secreto}".encode())


def limpiar_error(e: Exception) -> str:
    """Lo que se guarda del error: tipo y mensaje corto, sin direcciones de correo."""
    texto = f"{type(e).__name__}: {e}"
    texto = re.sub(r"[\w.+-]+@[\w.-]+", "<correo>", texto)
    return re.sub(r"\s+", " ", texto)[:300]


# ---------- contenido de cada correo ----------

def _fecha(valor: str) -> str:
    f = date.fromisoformat(valor[:10])
    return f"{clases.nombre_dia(f)} {f.day}/{f.month}"


def _clase(d: dict) -> str:
    return f"{d.get('tipo_clase') or 'Clase'} del {_fecha(d['fecha']).lower()} a las {d['hora_inicio'][:5]}"


def _hora(valor: str) -> str:
    return datetime.fromisoformat(valor).astimezone(clases.TZ).strftime("%H:%M")


def _mes(d: dict) -> str:
    return facturas.mes_txt(d["mes"], d["anio"]).lower()


def _vence(d: dict) -> str:
    f = date.fromisoformat(d["vence_el"])
    return f"{f.day:02d}/{f.month:02d}/{f.year}"


def _factura(d: dict) -> str:
    return f"{d.get('numero') or 'Factura'} · {facturas.pesos(d['total'])}"


MEDIOS = {"efectivo": "efectivo", "transferencia": "transferencia", "mercado_pago": "Mercado Pago"}

# tipo → (asunto, párrafos, texto del botón, ruta)
PLANTILLAS = {
    "clase_cancelada": lambda d: (
        "Se canceló una clase que tenías reservada",
        [f"La profesora canceló la clase de {_clase(d)}.", "No se cobra y ya no figura entre tus próximas clases."],
        "Ver mis inscripciones", "/mis-inscripciones"),
    "recordatorio_clase": lambda d: (
        f"Tu clase empieza a las {d['hora_inicio'][:5]}",
        [f"Te recordamos tu clase de {_clase(d)}.",
         "Si no podés ir, ya no se puede cancelar sin cargo (el plazo es hasta una hora antes)."],
        "Ver la clase", f"/dia/{d['fecha'][:10]}"),
    "factura_nueva": lambda d: (
        f"Ya está disponible tu factura de {_mes(d)}",
        [f"{_factura(d)}.", f"Fecha límite de pago: {_vence(d)}." if d.get("vence_el") else ""],
        "Ver mis facturas", "/mis-facturas"),
    "factura_proxima": lambda d: (
        f"Tu factura de {_mes(d)} vence el {_vence(d)}",
        [f"{_factura(d)}.", "Si ya la pagaste, no tenés que hacer nada."],
        "Ver mis facturas", "/mis-facturas"),
    "factura_vencida": lambda d: (
        f"Tu factura de {_mes(d)} está vencida",
        [f"{_factura(d)}. Venció el {_vence(d)}.", "Todavía podés pagarla. Si ya la pagaste, no tenés que hacer nada."],
        "Ver mis facturas", "/mis-facturas"),
    "factura_vencida_2": lambda d: (
        f"Recordatorio: tu factura de {_mes(d)} sigue pendiente",
        [f"{_factura(d)}. Venció el {_vence(d)}.", "Si ya la pagaste, no tenés que hacer nada."],
        "Ver mis facturas", "/mis-facturas"),
    "lugar_disponible": lambda d: (
        "¡Se liberó un lugar para vos!",
        [f"Se liberó un lugar en la clase de {_clase(d)}.",
         f"Confirmalo antes de las {_hora(d['oferta_vence_en'])}; si no, pasa a la siguiente de la lista."],
        "Confirmar el lugar", "/mis-inscripciones"),
    "reserva_confirmada": lambda d: (
        "Reserva confirmada",
        [f"Reservaste la clase de {_clase(d)}.", "Podés cancelar sin cargo hasta una hora antes del inicio."],
        "Ver mis inscripciones", "/mis-inscripciones"),
    "pago_registrado": lambda d: (
        f"Registramos el pago de tu factura de {_mes(d)}",
        [f"{d.get('numero') or 'Factura'} · {facturas.pesos(d['monto'])}"
         + (f" · {MEDIOS.get(d.get('medio_pago'), d.get('medio_pago'))}" if d.get("medio_pago") else "") + ".",
         "¡Gracias!"],
        "Ver mis facturas", "/mis-facturas"),
}


def armar(fila: dict, remitente: str) -> EmailMessage:
    empresa, sitio = fila.get("empresa") or "Just Show Up", fila["sitio"].rstrip("/")
    try:
        asunto, parrafos, boton, ruta = PLANTILLAS[fila["tipo"]](fila["datos"])
    except (KeyError, TypeError, ValueError):
        logging.warning("Correo %s: datos incompletos, se manda el aviso genérico", fila.get("id"))
        asunto, parrafos, boton, ruta = "Tenés una novedad", ["Hay algo nuevo para vos en la plataforma."], \
            "Ver mis novedades", "/novedades"
    parrafos = [p for p in parrafos if p]
    nombre = (fila["datos"] or {}).get("nombre") or ""
    saludo = f"Hola{(' ' + nombre) if nombre else ''}:"
    enlace = f"{sitio}{ruta}"
    pie = (f"Recibís este correo porque estás inscripta en {empresa}. Si no querés recibir avisos por correo, "
           f"desactivalos en Mi perfil: {sitio}/perfil")

    m = EmailMessage()
    m["Subject"] = f"{empresa}: {asunto}"
    m["From"] = formataddr((f"{empresa} (Just Show Up)", remitente))
    m["To"] = fila["mail"]
    m["Message-ID"] = make_msgid(domain=remitente.rsplit("@", 1)[-1])
    m["Auto-Submitted"] = "auto-generated"
    m["X-JSU-Envio"] = str(fila["id"])
    m.set_content("\n\n".join([saludo, *parrafos, f"{boton}: {enlace}", "—", pie]) + "\n")
    html_parrafos = "".join(f"<p>{escape(p)}</p>" for p in parrafos)
    m.add_alternative(
        f"""<!doctype html><html lang="es"><body style="font-family:Arial,sans-serif;color:#1f2933;line-height:1.5">
<p>{escape(saludo)}</p>{html_parrafos}
<p><a href="{escape(enlace)}" style="display:inline-block;background:#5eead4;color:#0f172a;padding:10px 16px;
border-radius:8px;text-decoration:none;font-weight:bold">{escape(boton)}</a></p>
<hr style="border:0;border-top:1px solid #e5e7eb"><p style="font-size:12px;color:#6b7280">{escape(pie)}</p>
</body></html>""", subtype="html")
    return m


# ---------- envío ----------

def _conectar(c: Config) -> smtplib.SMTP:
    contexto = ssl.create_default_context()
    if c.seguridad == "ssl":
        s = smtplib.SMTP_SSL(c.host, c.puerto, timeout=10, context=contexto)
    else:
        s = smtplib.SMTP(c.host, c.puerto, timeout=10)
        if c.seguridad == "starttls":
            s.starttls(context=contexto)
    if c.usuario and c.contrasena:
        s.login(c.usuario, c.contrasena)
    return s


def despachar(admin, c: Config, lote: int = LOTE, conectar=_conectar, reloj=time.monotonic) -> dict:
    """Toma un lote y lo manda. Devuelve cuántos se enviaron, fallaron y volvieron a la cola."""
    filas = admin.rpc("notificaciones_reclamar", {"p_lote": lote}).execute().data or []
    resultado = {"tomados": len(filas), "enviados": 0, "errores": 0, "liberados": 0}
    if not filas:
        return resultado
    inicio, smtp = reloj(), None

    def anotar(fila, estado, error=None):
        admin.rpc("notificaciones_resultado", {"p_id": fila["id"], "p_resultado": estado, "p_error": error}).execute()
        resultado[{"enviado": "enviados", "error": "errores", "liberar": "liberados"}[estado]] += 1

    try:
        for i, fila in enumerate(filas):
            if reloj() - inicio > PRESUPUESTO_SEG:
                for resto in filas[i:]:
                    anotar(resto, "liberar")
                break
            try:
                mensaje = armar(fila, c.remitente)
                if smtp is None:
                    smtp = conectar(c)
                smtp.send_message(mensaje)
                anotar(fila, "enviado")
            except smtplib.SMTPRecipientsRefused as e:  # problema de esa dirección: las demás siguen
                anotar(fila, "error", limpiar_error(e))
            except OSError as e:  # incluye todos los errores de smtplib
                # sin conexión, credenciales rechazadas o el servidor frenó (por ejemplo, el límite diario de Gmail):
                # no se insiste. Este cuenta como intento; los demás vuelven a la cola sin contar.
                logging.warning("Correos: se corta el lote (%s)", limpiar_error(e))
                anotar(fila, "error", limpiar_error(e))
                for resto in filas[i + 1:]:
                    anotar(resto, "liberar")
                break
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return resultado
