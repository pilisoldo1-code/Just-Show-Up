"""Mercado Pago Checkout Pro, SOLO MODO DE PRUEBA (proyecto universitario: dinero ficticio).

- Interruptor y credenciales en la base (mp_configuracion + Supabase Vault): solo los lee el
  servidor con la clave de administrador. Si algo falta o falla, Mercado Pago queda apagado.
- Antes de crear un pago se comprueba que la cuenta que cobra sea de PRUEBA (etiqueta test_user).
  Con las cuentas de prueba actuales Mercado Pago informa live_mode = true: no sirve para
  distinguirlas, por eso se mira la cuenta que cobra y la que paga.
- Ningún pago se registra sin consultarlo antes a Mercado Pago (GET /v1/payments/{id}).
- El importe sale siempre de la factura guardada; el número de operación se registra una sola vez.
- El Access Token nunca se escribe en logs, respuestas ni plantillas.
"""
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from supabase import Client

from app import auth
from app.clases import TZ, ahora

API = "https://api.mercadopago.com"
DOMINIOS_ALUMNAS = ("@demo.invalid", "@prueba.invalid")
SITIO_CHECKOUT = "https://www.mercadopago.com.uy/"
RUTA_VUELTA = "/mis-facturas/mercado-pago/vuelta"
RUTA_AVISOS = "/webhooks/mercado-pago"
MINUTOS_PARA_PAGAR = 30
HORAS_CONCILIACION = 48
TTL_CONFIG, TTL_CUENTAS = 30, 600

_cache: dict = {}


class NoDisponible(Exception):
    """Mercado Pago apagado, sin credencial o con una cuenta que no es de prueba."""


# ---------- configuración y cuentas ----------

def _guardado(clave, ttl, calcular):
    valor, vence = _cache.get(clave, (None, 0))
    if time.monotonic() < vence:
        return valor
    valor = calcular()
    # Un fallo (None) se reintenta pronto; un resultado bueno se guarda más tiempo.
    _cache[clave] = (valor, time.monotonic() + (ttl if valor is not None else 15))
    return valor


def limpiar_cache():
    _cache.clear()


def _leer_config() -> dict | None:
    try:
        cfg = auth.cliente_admin().rpc("mp_configuracion_servidor", {}).execute().data or {}
    except Exception:
        logging.warning("Mercado Pago: no se pudo leer la configuración (queda apagado)")
        return None
    if not cfg.get("habilitado") or not cfg.get("access_token"):
        return None
    return {"token": cfg["access_token"], "secreto": cfg.get("webhook_secret") or None}


def _config() -> dict | None:
    return _guardado("config", TTL_CONFIG, _leer_config)


def _get(token: str, ruta: str, params: dict | None = None) -> dict:
    r = httpx.get(API + ruta, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _vendedora(token: str) -> str | None:
    """Id de la cuenta que cobra, SOLO si es una cuenta de prueba de Uruguay."""
    def calcular():
        try:
            yo = _get(token, "/users/me")
        except Exception:
            logging.warning("Mercado Pago: no se pudo verificar la cuenta vendedora")
            return None
        if "test_user" not in (yo.get("tags") or []) or yo.get("site_id") != "MLU":
            logging.error("Mercado Pago: la credencial NO es de una cuenta de prueba de Uruguay; queda apagado")
            return None
        return str(yo["id"])
    return _guardado(("vendedora", hashlib.sha256(token.encode()).hexdigest()), TTL_CUENTAS, calcular)


def _es_compradora_de_prueba(token: str, id_usuario) -> bool:
    if not id_usuario:
        return False

    def calcular():
        try:
            u = _get(token, f"/users/{id_usuario}")
        except Exception:
            return None
        return str(u.get("nickname", "")).upper().startswith("TESTUSER")
    return bool(_guardado(("pagador", str(id_usuario)), TTL_CUENTAS, calcular))


def _activo() -> tuple[str, str]:
    """(token, id de la vendedora de prueba) o NoDisponible."""
    cfg = _config()
    vendedora = cfg and _vendedora(cfg["token"])
    if not vendedora:
        raise NoDisponible()
    return cfg["token"], vendedora


def alumna_permitida(mail: str) -> bool:
    return (mail or "").lower().endswith(DOMINIOS_ALUMNAS)


def disponible_para(mail: str) -> bool:
    if not alumna_permitida(mail):
        return False
    try:
        _activo()
        return True
    except NoDisponible:
        return False


def sitio_publico(host: str | None) -> str | None:
    """Dirección del sitio para la vuelta y los avisos: solo sitios conocidos y públicos."""
    if host in auth.SITIOS_PERMITIDOS and host not in ("localhost", "127.0.0.1"):
        return f"https://{host}"
    return None


# ---------- iniciar el pago (alumna) ----------

MENSAJES_INICIO = {
    "deshabilitado": "El pago con Mercado Pago no está disponible en este momento.",
    "no_autorizada": "Solo la alumna puede pagar su factura.",
    "alumna_no_habilitada": "El pago con Mercado Pago no está disponible para tu cuenta.",
    "no_existe": "No encontramos esa factura.",
    "no_disponible": "Esa factura todavía no se puede pagar.",
    "ya_pagada": "Esa factura ya figura como pagada.",
    "sin_cargo": "Esa factura no tiene nada para pagar.",
}
ERROR_GENERICO = "No pudimos iniciar el pago con Mercado Pago. Probá de nuevo en unos minutos."


def iniciar_pago(sb_alumna: Client, mail: str, factura_id: int, sitio: str | None) -> tuple[bool, str]:
    """(True, link del checkout) o (False, mensaje para la alumna)."""
    if not alumna_permitida(mail):
        return False, MENSAJES_INICIO["alumna_no_habilitada"]
    try:
        token, _ = _activo()
    except NoDisponible:
        return False, MENSAJES_INICIO["deshabilitado"]
    try:
        r = sb_alumna.rpc("crear_intento_mp", {"p_id_resumen": factura_id}).execute().data or {}
    except Exception:
        logging.exception("Mercado Pago: no se pudo crear el intento")
        return False, ERROR_GENERICO
    if r.get("resultado") != "ok":
        return False, MENSAJES_INICIO.get(r.get("resultado"), ERROR_GENERICO)

    vence = datetime.now(timezone.utc) + timedelta(minutes=MINUTOS_PARA_PAGAR)
    cuerpo = {
        "items": [{"id": str(factura_id), "title": f"Factura {r.get('numero') or ''} - Just Show Up (PRUEBA, dinero ficticio)",
                   "quantity": 1, "unit_price": float(r["monto"]), "currency_id": "UYU"}],
        "external_reference": r["referencia"],
        "binary_mode": True,
        "payment_methods": {"excluded_payment_types": [{"id": "ticket"}], "installments": 1},
        "expires": True,
        "expiration_date_to": vence.isoformat(timespec="milliseconds"),
    }
    if sitio:
        cuerpo["back_urls"] = {k: sitio + RUTA_VUELTA for k in ("success", "failure", "pending")}
        cuerpo["auto_return"] = "approved"
        cuerpo["notification_url"] = sitio + RUTA_AVISOS + "?source_news=webhooks"
    try:
        resp = httpx.post(API + "/checkout/preferences", json=cuerpo, timeout=15,
                          headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": r["referencia"]})
        resp.raise_for_status()
        pref = resp.json()
    except Exception:
        logging.warning("Mercado Pago: no se pudo crear la preferencia")
        return False, ERROR_GENERICO
    link = pref.get("init_point") or ""
    if not link.startswith(SITIO_CHECKOUT):
        logging.error("Mercado Pago: link de pago inesperado; no se redirige")
        return False, ERROR_GENERICO
    try:
        auth.cliente_admin().rpc("mp_guardar_preferencia", {"p_id_intento": r["id_intento"],
                                                            "p_preference_id": pref.get("id")}).execute()
    except Exception:
        logging.warning("Mercado Pago: no se pudo guardar la preferencia (el pago igual se verifica)")
    return True, link


# ---------- verificar y registrar (servidor) ----------

def _fecha_pago(p: dict) -> str:
    momento = p.get("date_approved") or p.get("date_created")
    try:
        return datetime.fromisoformat(momento).astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return ahora().date().isoformat()


def _evento(origen: str, tipo: str | None, id_recurso, firma: str, resultado: str):
    try:
        auth.cliente_admin().rpc("mp_registrar_evento", {
            "p_origen": origen, "p_tipo": tipo, "p_id_recurso": None if id_recurso is None else str(id_recurso),
            "p_firma": firma, "p_resultado": resultado}).execute()
    except Exception:
        logging.warning("Mercado Pago: no se pudo guardar el evento")


def verificar_pago(id_pago, origen: str, firma: str = "no_aplica") -> str:
    """Consulta el pago a Mercado Pago y, si corresponde, lo registra. Devuelve el resultado:
    ok | ya_registrado | rechazado | pendiente | a_revisar | intento_desconocido | no_es_prueba
    | no_disponible | error."""
    id_pago = str(id_pago or "").strip()
    if not id_pago.isdigit():
        return "error"
    try:
        token, vendedora = _activo()
    except NoDisponible:
        return "no_disponible"
    try:
        p = _get(token, f"/v1/payments/{id_pago}")
    except Exception:
        logging.warning("Mercado Pago: no se pudo consultar el pago %s", id_pago)
        _evento(origen, "payment", id_pago, firma, "error_consulta")
        return "error"
    pagador = (p.get("payer") or {}).get("id")
    if str(p.get("collector_id")) != vendedora or not _es_compradora_de_prueba(token, pagador):
        logging.error("Mercado Pago: el pago %s no es entre cuentas de prueba; no se registra", id_pago)
        _evento(origen, "payment", id_pago, firma, "no_es_prueba")
        return "no_es_prueba"
    referencia = p.get("external_reference") or ""
    if not referencia.startswith("JSU-"):
        _evento(origen, "payment", id_pago, firma, "intento_desconocido")
        return "intento_desconocido"
    try:
        r = auth.cliente_admin().rpc("registrar_pago_mercado_pago", {
            "p_referencia": referencia, "p_id_pago_mp": str(p["id"]), "p_estado": p.get("status"),
            "p_monto": str(p.get("transaction_amount")), "p_moneda": p.get("currency_id"),
            "p_fecha": _fecha_pago(p)}).execute().data or {}
    except Exception:
        logging.exception("Mercado Pago: no se pudo registrar el pago %s", id_pago)
        _evento(origen, "payment", id_pago, firma, "error_registro")
        return "error"
    resultado = r.get("resultado", "error")
    _evento(origen, "payment", id_pago, firma, resultado + (f": {r['motivo']}" if r.get("motivo") else ""))
    return resultado


MENSAJES_VUELTA = {
    "ok": (True, "¡Pago aprobado! Tu factura ya figura como pagada. (Pago de prueba con dinero ficticio.)"),
    "ya_registrado": (True, "¡Pago aprobado! Tu factura ya figura como pagada."),
    "rechazado": (False, "El pago fue rechazado. Tu factura sigue pendiente: podés intentar de nuevo."),
    "pendiente": (False, "Estamos verificando tu pago con Mercado Pago. Revisá Mis facturas en unos minutos."),
    "a_revisar": (False, "Recibimos tu pago, pero no pudimos asociarlo automáticamente a la factura. "
                         "La profesora lo va a revisar."),
}
MENSAJE_SIN_PAGO = (False, "No se completó ningún pago. Tu factura sigue pendiente.")
MENSAJE_VERIFICANDO = (False, "Estamos verificando tu pago con Mercado Pago. Revisá Mis facturas en unos minutos.")


def resultado_vuelta(id_pago: str) -> tuple[bool, str]:
    """Al volver de Mercado Pago: nunca se le cree a la URL, se consulta el pago."""
    if not id_pago or id_pago in ("null", "undefined"):
        return MENSAJE_SIN_PAGO
    return MENSAJES_VUELTA.get(verificar_pago(id_pago, "vuelta"), MENSAJE_VERIFICANDO)


def conciliar(sb_alumna: Client, alumna_id: str) -> None:
    """Si la alumna cerró Mercado Pago sin volver o el aviso todavía no llegó: busca en Mercado
    Pago los pagos de sus intentos recientes sin resultado y los registra."""
    try:
        token, _ = _activo()
    except NoDisponible:
        return
    desde = (datetime.now(timezone.utc) - timedelta(hours=HORAS_CONCILIACION)).isoformat()
    try:
        intentos = (sb_alumna.table("mp_intento").select("referencia, estado, creado_en")
                    .eq("id_alumna", alumna_id).eq("estado", "creado").gte("creado_en", desde).execute().data)
    except Exception:
        logging.warning("Mercado Pago: no se pudieron leer los intentos para conciliar")
        return
    for intento in intentos[:5]:
        try:
            pagos = _get(token, "/v1/payments/search", {"external_reference": intento["referencia"]}).get("results") or []
        except Exception:
            continue
        for p in pagos[:3]:
            verificar_pago(p.get("id"), "conciliacion")


# ---------- avisos (webhooks) ----------

def firma_valida(x_signature: str | None, x_request_id: str | None, data_id: str, secreto: str) -> bool:
    """Valida x-signature (HMAC-SHA256) según la documentación de Mercado Pago."""
    partes = dict(p.strip().split("=", 1) for p in (x_signature or "").split(",") if "=" in p)
    ts, v1 = partes.get("ts"), partes.get("v1")
    if not ts or not v1:
        return False
    manifiesto = f"id:{data_id.lower() if data_id.isalnum() else data_id};"
    if x_request_id:
        manifiesto += f"request-id:{x_request_id};"
    manifiesto += f"ts:{ts};"
    esperado = hmac.new(secreto.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)


def procesar_aviso(params: dict, cuerpo: dict, encabezados) -> None:
    """El aviso solo dispara una consulta a Mercado Pago: lo que se registra sale de esa consulta."""
    cfg = _config()
    if not cfg:
        return
    tipo = params.get("type") or params.get("topic") or (cuerpo or {}).get("type") or (cuerpo or {}).get("topic")
    data_id = params.get("data.id") or params.get("id") or str(((cuerpo or {}).get("data") or {}).get("id") or "")
    if tipo != "payment" or not data_id:
        return
    firma = "sin_clave"
    if cfg.get("secreto"):
        if not firma_valida(encabezados.get("x-signature"), encabezados.get("x-request-id"), data_id, cfg["secreto"]):
            _evento("aviso", tipo, data_id, "invalida", "ignorado")
            return
        firma = "valida"
    verificar_pago(data_id, "aviso", firma)
