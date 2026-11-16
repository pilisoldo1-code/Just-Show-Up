"""Novedades (fase 1 de notificaciones): avisos calculados en el momento, SOLO LECTURA.

No hay tabla de notificaciones ni marca de «leída»: cada novedad es algo que hoy está pendiente y se
arma con datos que ya existen (las mismas tablas y funciones que muestran Mis inscripciones, Mis facturas,
Asistencias y Facturación). Cuando se resuelve (se confirma la oferta, se paga la factura, se registra la
asistencia), la novedad desaparece sola.

Reglas:
- Nunca se escribe en la base: no se llama a `actualizar_listas_espera`, `sincronizar_borradores`
  ni a la conciliación de Mercado Pago (esas las siguen haciendo las pantallas de cada sección).
- Todo con la sesión de la persona (RLS) y, además, filtrado por la empresa activa: una persona con
  membresía en dos empresas solo ve las novedades de la empresa con la que entró.
- Cada bloque es independiente: si uno falla, los demás se muestran igual.

Fase A (aviso dentro de la app de las 10 notificaciones de la alumna): los plazos configurables se leen con
`novedades_parametros()` (tabla `notificacion_parametros`); si la función todavía no existe en la base, se usan
los valores por defecto de `PARAMETROS`.
"""
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from supabase import Client

from app import clases, facturas

URGENTE = "urgente"    # tiene un plazo o ya venció
PENDIENTE = "pendiente"  # algo para hacer, sin apuro inmediato
AVISO = "aviso"        # informativo

ETIQUETAS = {URGENTE: ("Urgente", "badge-rojo"), PENDIENTE: ("Pendiente", "badge-espera"), AVISO: ("Aviso", "badge-off")}
ORDEN = {URGENTE: 0, PENDIENTE: 1, AVISO: 2}

RECORDATORIO_MINUTOS = 60  # recordatorio de clase: fijo por ahora (decisión del 16/11)
# Plazos configurables sin tocar el código (tabla notificacion_parametros); estos son los valores por defecto.
PARAMETROS = {"factura_aviso_dias": 3, "video_aviso_horas": 3, "reserva_aviso_horas": 24, "pago_aviso_dias": 3}
_LIMITES = {"factura_aviso_dias": (1, 30), "video_aviso_horas": (1, 23), "reserva_aviso_horas": (1, 168),
            "pago_aviso_dias": (1, 30)}
MEDIOS_PAGO = {"efectivo": "efectivo", "transferencia": "transferencia", "mercado_pago": "Mercado Pago"}


def _novedad(nivel: str, titulo: str, detalle: str, enlace: str, accion: str, lineas: list[str] | None = None) -> dict:
    etiqueta, badge = ETIQUETAS[nivel]
    return {"nivel": nivel, "etiqueta": etiqueta, "badge": badge, "titulo": titulo, "detalle": detalle,
            "enlace": enlace, "accion": accion, "lineas": lineas or []}


def _de_empresa(consulta, empresa_id: int | None):
    """La RLS ya limita a la empresa activa; el filtro explícito es una segunda barrera."""
    return consulta.eq("id_empresa", empresa_id) if empresa_id is not None else consulta


def _clase_txt(c: dict) -> str:
    f = date.fromisoformat(c["fecha"])
    tipo = (c.get("tipo_clase") or {}).get("nombre") or "Clase"
    return f"{tipo} · {clases.nombre_dia(f)} {f.day}/{f.month} {c['hora_inicio'][:5]}"


def _mes(mes: int, anio: int) -> str:
    """«octubre 2026»: va dentro de una oración."""
    return facturas.mes_txt(mes, anio).lower()


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def _momento(valor: str) -> datetime:
    return datetime.fromisoformat(valor).astimezone(clases.TZ)


def parametros(sb: Client) -> dict:
    """Los plazos configurables. Cualquier valor que falte, que no sea entero o que esté fuera de rango usa el de
    por defecto (un dato mal cargado nunca deja sin avisos)."""
    valores = dict(PARAMETROS)
    try:
        leidos = sb.rpc("novedades_parametros", {}).execute().data or {}
    except Exception:
        logging.warning("Novedades: sin novedades_parametros, se usan los plazos por defecto")
        return valores
    for clave, (minimo, maximo) in _LIMITES.items():
        v = leidos.get(clave) if isinstance(leidos, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and minimo <= v <= maximo:
            valores[clave] = v
    return valores


# ---------- alumna ----------

def ofertas_alumna(sb: Client, alumna_id: str, empresa_id: int | None, ahora: datetime) -> list[dict]:
    """Lugares ofrecidos de la lista de espera que todavía se pueden confirmar."""
    filas = _de_empresa(
        sb.table("listadeespera").select("id_clase, oferta_vence_en")
        .eq("id_alumna", alumna_id).eq("estado", "ofrecido"), empresa_id).execute().data or []
    if not filas:
        return []
    # como Mis inscripciones: la clase se lee aparte (su RLS también la limita a la empresa activa)
    datos_clase = {c["id"]: c for c in _de_empresa(
        sb.table("clase").select("id, fecha, hora_inicio, tipo_clase(nombre)")
        .in_("id", [e["id_clase"] for e in filas]), empresa_id).execute().data or []}
    salida = []
    for e in filas:
        c = datos_clase.get(e["id_clase"])
        if not c or not e.get("oferta_vence_en"):
            continue
        vence = datetime.fromisoformat(e["oferta_vence_en"]).astimezone(clases.TZ)
        if vence <= ahora or clases._inicio_clase(c) <= ahora:
            continue  # vencida: al abrir la sección, la lista de espera pasa a la siguiente
        salida.append((clases._inicio_clase(c), _novedad(
            URGENTE, "¡Se liberó un lugar para vos!",
            f"{_clase_txt(c)}. Confirmalo antes de las {vence.strftime('%H:%M')}; si no, pasa a la siguiente de la lista.",
            "/mis-inscripciones", "Confirmar el lugar")))  # ahí están Confirmar / No me interesa de todas las ofertas
    return [n for _, n in sorted(salida, key=lambda x: x[0])]


def canceladas_alumna(sb: Client, alumna_id: str, empresa_id: int | None, ahora: datetime) -> list[dict]:
    """Clases futuras que la alumna tenía reservadas y canceló la profesora (reserva con bloqueo)."""
    filas = _de_empresa(
        sb.table("reserva").select("id_bloqueo, clase(fecha, hora_inicio, tipo_clase(nombre))")
        .eq("id_alumna", alumna_id).eq("estado", "cancelada"), empresa_id).execute().data or []
    futuras = sorted((r["clase"] for r in filas if r.get("id_bloqueo") is not None and r.get("clase")
                      and clases._inicio_clase(r["clase"]) > ahora), key=clases._inicio_clase)
    if not futuras:
        return []
    return [_novedad(
        AVISO, f"La profesora canceló {_plural(len(futuras), 'clase que tenías reservada', 'clases que tenías reservadas')}",
        "No se cobran: ya no figuran entre tus próximas clases.",
        "/mis-inscripciones", "Ver mis inscripciones", [_clase_txt(c) for c in futuras])]


def facturas_alumna(sb: Client, alumna_id: str, empresa_id: int | None, hoy: date,
                    aviso_dias: int = PARAMETROS["factura_aviso_dias"]) -> list[dict]:
    """Facturas publicadas y sin pagar (qué está publicado lo decide la RLS). Cada una aparece una sola vez:
    vencida (desde el día siguiente a la fecha límite), próxima a vencer (los últimos `aviso_dias` días hasta la
    fecha límite inclusive) o nueva. Pagada o anulada, deja de aparecer."""
    filas = _de_empresa(
        sb.table("resumen_mensual").select("id, numero, mes, anio, total, vence_el, estado_factura, estado_pago")
        .eq("id_alumna", alumna_id).eq("estado_factura", "confirmada").eq("estado_pago", "pendiente"),
        empresa_id).execute().data or []
    salida = []  # (orden, novedad): vencidas, después próximas a vencer y nuevas; cada grupo por mes
    for f in sorted(filas, key=lambda f: (f["anio"], f["mes"])):
        v = facturas.vencimiento(f, hoy)
        mes = _mes(f["mes"], f["anio"])
        importe = facturas.pesos(f["total"])
        numero = f"{f.get('numero') or ''} · {importe}".lstrip(" ·")
        vence = date.fromisoformat(f["vence_el"]) if f.get("vence_el") else None
        if v["vencida"]:
            salida.append((0, _novedad(URGENTE, f"Factura de {mes} vencida",
                                   f"{numero}. Venció el {v['vence_txt']}: podés pagarla igual.",
                                   "/mis-facturas", "Ver mis facturas")))
        elif vence and vence - timedelta(days=aviso_dias) <= hoy <= vence:
            cuando = "hoy" if vence == hoy else ("mañana" if (vence - hoy).days == 1 else f"el {v['vence_txt']}")
            salida.append((1, _novedad(URGENTE, f"Tu factura de {mes} vence {cuando}",
                                   f"{numero}. Fecha límite de pago: {v['vence_txt']}.",
                                   "/mis-facturas", "Pagar o ver la factura")))
        else:
            plazo = f" Fecha límite de pago: {v['vence_txt']}." if v["vence_txt"] else ""
            salida.append((2, _novedad(PENDIENTE, f"Nueva factura de {mes}", f"{numero}.{plazo}",
                                       "/mis-facturas", "Ver mis facturas")))
    return [n for _, n in sorted(salida, key=lambda x: x[0])]  # sort estable: dentro de cada grupo, por mes


def reservas_alumna(sb: Client, alumna_id: str, empresa_id: int | None, ahora: datetime,
                    reciente_horas: int = PARAMETROS["reserva_aviso_horas"]) -> list[dict]:
    """Recordatorio (la clase empieza dentro de la próxima hora) y reservas confirmadas en las últimas
    `reciente_horas` horas para clases que todavía no empezaron. Una misma clase nunca sale en los dos."""
    filas = _de_empresa(
        sb.table("reserva").select("fecha_reserva, clase(id, fecha, hora_inicio, estado, tipo_clase(nombre))")
        .eq("id_alumna", alumna_id).eq("estado", "confirmada"), empresa_id).execute().data or []
    proximas, recientes = [], []
    desde = ahora - timedelta(hours=reciente_horas)
    for r in filas:
        c = r.get("clase")
        if not c or c.get("estado", "programada") != "programada":
            continue
        inicio = clases._inicio_clase(c)
        if inicio <= ahora:
            continue  # empezó o terminó: ya no hay nada que recordar
        if inicio - ahora <= timedelta(minutes=RECORDATORIO_MINUTOS):
            proximas.append((inicio, c))
        elif r.get("fecha_reserva") and _momento(r["fecha_reserva"]) >= desde:
            recientes.append((inicio, c))
    salida = []
    for inicio, c in sorted(proximas, key=lambda x: x[0]):
        minutos = max(1, int((inicio - ahora).total_seconds() // 60))
        salida.append(_novedad(
            URGENTE, f"Tu clase empieza a las {inicio.strftime('%H:%M')}",
            f"{_clase_txt(c)}. Faltan {_plural(minutos, 'minuto', 'minutos')}.",
            f"/dia/{c['fecha']}", "Ver la clase"))
    if recientes:
        recientes.sort(key=lambda x: x[0])
        n = len(recientes)
        salida.append(_novedad(
            AVISO, "Reserva confirmada" if n == 1 else f"{n} reservas confirmadas",
            "Podés cancelar sin cargo hasta una hora antes del inicio.",
            "/mis-inscripciones", "Ver mis inscripciones", [_clase_txt(c) for _, c in recientes]))
    return salida


def pagos_alumna(sb: Client, alumna_id: str, empresa_id: int | None, ahora: datetime,
                 aviso_dias: int = PARAMETROS["pago_aviso_dias"]) -> list[dict]:
    """Pagos vigentes registrados en los últimos `aviso_dias` días (manuales o de Mercado Pago de prueba).
    Si el pago se anula, deja de aparecer."""
    pagos = _de_empresa(
        sb.table("pago").select("id_resumen_mensual, monto, medio_pago, fecha, estado, creado_en")
        .eq("estado", "vigente"), empresa_id).execute().data or []
    desde = ahora - timedelta(days=aviso_dias)
    pagos = [p for p in pagos if p.get("creado_en") and _momento(p["creado_en"]) >= desde]
    if not pagos:
        return []
    # la RLS solo deja ver los pagos de facturas visibles; el filtro por alumna es la segunda barrera
    datos = {f["id"]: f for f in _de_empresa(
        sb.table("resumen_mensual").select("id, numero, mes, anio, id_alumna")
        .eq("id_alumna", alumna_id).in_("id", [p["id_resumen_mensual"] for p in pagos]), empresa_id).execute().data or []}
    salida = []
    for p in sorted(pagos, key=lambda p: p["creado_en"], reverse=True):
        f = datos.get(p["id_resumen_mensual"])
        if not f:
            continue
        medio = MEDIOS_PAGO.get(p.get("medio_pago"), p.get("medio_pago") or "")
        detalle = f"{f.get('numero') or ''} · {facturas.pesos(p['monto'])}".lstrip(" ·")
        salida.append(_novedad(AVISO, f"Pago registrado: factura de {_mes(f['mes'], f['anio'])}",
                               f"{detalle}{f' · {medio}' if medio else ''}. ¡Gracias!",
                               "/mis-facturas", "Ver mis facturas"))
    return salida


def videos_alumna(sb: Client, ahora: datetime, aviso_horas: int = PARAMETROS["video_aviso_horas"]) -> list[dict]:
    """Videos publicados y vigentes que la alumna todavía no empezó a ver: «nuevo» o, en sus últimas
    `aviso_horas` horas, «por vencer». La empresa la fija la base (empresa activa de la sesión)."""
    disponibles = sb.rpc("videos_disponibles", {}).execute().data or []
    if not disponibles:
        return []
    iniciados = set(sb.rpc("novedades_videos_iniciados", {}).execute().data or [])
    nuevos, por_vencer = [], []
    for v in disponibles:
        restante = v.get("segundos_restantes") or 0
        if v["id"] in iniciados or restante <= 0:
            continue
        f = date.fromisoformat(v["fecha"])
        txt = f"{v['clase']} · {clases.nombre_dia(f)} {f.day}/{f.month} {v['hora_inicio'][:5]}"
        (por_vencer if restante <= aviso_horas * 3600 else nuevos).append((restante, v["id"], txt))
    salida = []
    for restante, id_video, txt in sorted(por_vencer):
        vence = (ahora + timedelta(seconds=restante)).strftime("%H:%M")
        salida.append(_novedad(PENDIENTE, "Un video está por dejar de estar disponible",
                               f"{txt}. Podés verlo hasta las {vence}.", f"/videos/{id_video}", "Ver el video"))
    if nuevos:
        nuevos.sort(key=lambda x: x[0])
        unico = len(nuevos) == 1
        salida.append(_novedad(AVISO, "Nuevo video disponible" if unico else f"{len(nuevos)} videos nuevos disponibles",
                               "Cada video se puede ver durante 24 horas desde su publicación.",
                               f"/videos/{nuevos[0][1]}" if unico else "/videos",
                               "Ver el video" if unico else "Ver los videos", [t for _, _, t in nuevos]))
    return salida


# ---------- profesora y administradora ----------

def asistencias_pendientes(sb: Client) -> list[dict]:
    """Clases terminadas sin asistencia registrada (la base limita a las de la profesora, o toda la empresa
    para la administradora)."""
    p = clases.pendientes_totales(sb)
    if not p["clases"]:
        return []
    enlace = "/profesora/clases?filtro=pendientes#terminadas"
    if p["ultima_fecha"]:
        enlace = f"/profesora/clases?mes={p['ultima_fecha'].strftime('%Y-%m')}&filtro=pendientes#terminadas"
    return [_novedad(
        PENDIENTE, f"{_plural(p['clases'], 'clase terminada', 'clases terminadas')} sin asistencia registrada",
        f"{_plural(p['asistencias'], 'asistencia', 'asistencias')} para completar.", enlace, "Completar asistencias")]


def facturas_admin(sb: Client, empresa_id: int | None, hoy: date) -> list[dict]:
    """Borradores listos para revisar y facturas confirmadas vencidas sin pagar, agrupados por mes."""
    filas = _de_empresa(
        sb.table("resumen_mensual").select("mes, anio, vence_el, estado_factura, estado_pago, alumna_nombre, alumna_apellido")
        .in_("estado_factura", ["borrador", "confirmada"]), empresa_id).execute().data or []
    borradores: dict[tuple, int] = {}
    vencidas: dict[tuple, list[str]] = {}
    for f in filas:
        clave = (f["anio"], f["mes"])
        if f["estado_factura"] == "borrador":
            borradores[clave] = borradores.get(clave, 0) + 1
        elif f["estado_pago"] == "pendiente" and facturas.vencimiento(f, hoy)["vencida"]:
            nombre = f"{f.get('alumna_nombre') or ''} {f.get('alumna_apellido') or ''}".strip()
            vencidas.setdefault(clave, []).append(nombre)
    salida = []
    for (anio, mes), n in sorted(borradores.items()):
        salida.append(_novedad(
            PENDIENTE, f"{_plural(n, 'factura', 'facturas')} de {_mes(mes, anio)} en borrador",
            "Revisalas y confirmalas para que las alumnas puedan verlas y pagarlas.",
            f"/profesora/resumenes?mes={mes}&anio={anio}", "Ir a Facturación"))
    for (anio, mes), nombres in sorted(vencidas.items()):
        salida.append(_novedad(
            URGENTE, f"{_plural(len(nombres), 'factura', 'facturas')} de {_mes(mes, anio)} "
                     f"{'vencida' if len(nombres) == 1 else 'vencidas'} sin pagar",
            "Las alumnas todavía pueden pagarlas; podés registrar los pagos en Facturación.",
            f"/profesora/resumenes?mes={mes}&anio={anio}", "Ir a Facturación", sorted(nombres, key=str.lower)))
    return salida


# ---------- superadministradora ----------

def invitaciones_superadmin(sb: Client, invitacion_pendiente: Callable[[str], bool]) -> list[dict]:
    """Administradoras activas que todavía no activaron su cuenta (no usaron la invitación)."""
    salida = []
    for e in sb.rpc("resumen_empresas", {}).execute().data or []:
        pendientes = [f"{a['nombre']} {a['apellido']}".strip()
                      for a in sb.rpc("administradoras_empresa", {"p_id_empresa": e["id"]}).execute().data or []
                      if a.get("activa") and invitacion_pendiente(a["id"])]
        if pendientes:
            salida.append(_novedad(
                PENDIENTE, f"{e['nombre']}: {_plural(len(pendientes), 'administradora todavía no activó', 'administradoras todavía no activaron')} su cuenta",
                "Si el correo de invitación venció, podés reenviarlo desde la empresa.",
                f"/admin/empresas/{e['id']}", "Gestionar empresa", pendientes))
    return salida


# ---------- todas juntas ----------

def _bloque(nombre: str, funcion, *args) -> tuple[list[dict], bool]:
    try:
        return funcion(*args), True
    except Exception:
        logging.exception("Novedades: no se pudo armar el bloque %s", nombre)
        return [], False


def de_usuario(sb: Client, usuario, invitacion_pendiente: Callable[[str], bool], ahora: datetime | None = None) -> dict:
    """{"novedades": [...], "completas": bool}. Las urgentes primero; dentro de cada nivel, el orden de los bloques."""
    ahora = ahora or clases.ahora()
    hoy = ahora.date()
    bloques = []
    if usuario.es_superadmin:
        bloques.append(("invitaciones", invitaciones_superadmin, sb, invitacion_pendiente))
    elif not usuario.sin_empresa:
        empresa = usuario.empresa_id
        if usuario.es_alumna:
            p = parametros(sb)
            bloques += [("ofertas", ofertas_alumna, sb, usuario.id, empresa, ahora),
                        ("reservas", reservas_alumna, sb, usuario.id, empresa, ahora, p["reserva_aviso_horas"]),
                        ("facturas de la alumna", facturas_alumna, sb, usuario.id, empresa, hoy, p["factura_aviso_dias"]),
                        ("pagos", pagos_alumna, sb, usuario.id, empresa, ahora, p["pago_aviso_dias"]),
                        ("clases canceladas", canceladas_alumna, sb, usuario.id, empresa, ahora),
                        ("videos", videos_alumna, sb, ahora, p["video_aviso_horas"])]
        if usuario.es_docente or usuario.es_admin:
            bloques.append(("asistencias", asistencias_pendientes, sb))
        if usuario.es_admin:
            bloques.append(("facturación", facturas_admin, sb, empresa, hoy))
    novedades, completas = [], True
    for nombre, funcion, *args in bloques:
        items, ok = _bloque(nombre, funcion, *args)
        novedades += items
        completas = completas and ok
    novedades.sort(key=lambda n: ORDEN[n["nivel"]])  # sort estable: respeta el orden de cada bloque
    return {"novedades": novedades, "completas": completas}


def contador(novedades: list[dict]) -> dict:
    return {"total": len(novedades), "urgentes": sum(n["nivel"] == URGENTE for n in novedades)}
