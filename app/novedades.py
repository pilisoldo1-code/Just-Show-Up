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
"""
import logging
from collections.abc import Callable
from datetime import date, datetime

from supabase import Client

from app import clases, facturas

URGENTE = "urgente"    # tiene un plazo o ya venció
PENDIENTE = "pendiente"  # algo para hacer, sin apuro inmediato
AVISO = "aviso"        # informativo

ETIQUETAS = {URGENTE: ("Urgente", "badge-rojo"), PENDIENTE: ("Pendiente", "badge-espera"), AVISO: ("Aviso", "badge-off")}
ORDEN = {URGENTE: 0, PENDIENTE: 1, AVISO: 2}


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


def facturas_alumna(sb: Client, alumna_id: str, empresa_id: int | None, hoy: date) -> list[dict]:
    """Facturas publicadas y sin pagar (qué está publicado lo decide la RLS)."""
    filas = _de_empresa(
        sb.table("resumen_mensual").select("id, numero, mes, anio, total, vence_el, estado_factura, estado_pago")
        .eq("id_alumna", alumna_id).eq("estado_factura", "confirmada").eq("estado_pago", "pendiente"),
        empresa_id).execute().data or []
    salida = []
    for f in sorted(filas, key=lambda f: (f["anio"], f["mes"])):
        v = facturas.vencimiento(f, hoy)
        mes = _mes(f["mes"], f["anio"])
        importe = facturas.pesos(f["total"])
        if v["vencida"]:
            salida.append(_novedad(URGENTE, f"Factura de {mes} vencida",
                                   f"{f.get('numero') or ''} · {importe}. Venció el {v['vence_txt']}: podés pagarla igual.".lstrip(" ·"),
                                   "/mis-facturas", "Ver mis facturas"))
        else:
            plazo = f" Fecha límite de pago: {v['vence_txt']}." if v["vence_txt"] else ""
            salida.append(_novedad(PENDIENTE, f"Factura de {mes} para pagar",
                                   f"{f.get('numero') or ''} · {importe}.{plazo}".lstrip(" ·"),
                                   "/mis-facturas", "Ver mis facturas"))
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
            bloques += [("ofertas", ofertas_alumna, sb, usuario.id, empresa, ahora),
                        ("facturas de la alumna", facturas_alumna, sb, usuario.id, empresa, hoy),
                        ("clases canceladas", canceladas_alumna, sb, usuario.id, empresa, ahora)]
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
