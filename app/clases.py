from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from supabase import Client

TZ = ZoneInfo("America/Montevideo")
DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
HORAS_LIMITE_CANCELACION = 1
MENSAJE_PLAZO = (
    "El plazo de cancelación ya finalizó. "
    "Las cancelaciones deben realizarse al menos 1 hora antes de la clase."
)


def ahora() -> datetime:
    return datetime.now(TZ)


def _inicio_clase(clase: dict) -> datetime:
    return datetime.combine(
        date.fromisoformat(clase["fecha"]),
        time.fromisoformat(clase["hora_inicio"]),
        tzinfo=TZ,
    )


def generar_clases(sb: Client, inicio_semana: date) -> None:
    sb.rpc("generar_clases", {"p_lunes": inicio_semana.isoformat()}).execute()


def clases_entre(sb: Client, inicio: date, fin: date, alumna_id: str) -> list[dict]:
    clases = (
        sb.table("clase")
        .select("*, horario(cupo_max, activo, dia_semana)")
        .gte("fecha", inicio.isoformat())
        .lte("fecha", fin.isoformat())
        .eq("estado", "programada")
        .execute()
        .data
    )
    clases = [c for c in clases if c["horario"] and c["horario"]["activo"]]
    clases.sort(key=lambda c: (c["fecha"], c["hora_inicio"]))
    if not clases:
        return []

    ids = [c["id"] for c in clases]
    inscriptas = {
        r["id_clase"]
        for r in sb.table("reserva").select("id_clase")
        .eq("id_alumna", alumna_id).eq("estado", "confirmada").in_("id_clase", ids)
        .execute().data
    }
    en_espera = {
        r["id_clase"]: r["posicion"]
        for r in sb.table("listadeespera").select("id_clase, posicion")
        .eq("id_alumna", alumna_id).in_("estado", ["en_espera", "ofrecido"]).in_("id_clase", ids)
        .execute().data
    }

    # Las alumnas solo ven sus propias reservas: el total por clase lo cuenta la base.
    confirmadas = {
        r["id_clase"]: r["cantidad"]
        for r in sb.rpc("reservas_confirmadas", {"p_ids": ids}).execute().data
    }

    ahora_dt = ahora()
    tarjetas = []
    for c in clases:
        inicio_dt = _inicio_clase(c)
        cupo_max = c["horario"]["cupo_max"]
        disponibles = max(0, cupo_max - confirmadas.get(c["id"], 0))
        fecha = date.fromisoformat(c["fecha"])

        if inicio_dt <= ahora_dt:
            estado = "pasada"
        elif c["id"] in inscriptas:
            estado = "inscripta"
        elif c["id"] in en_espera:
            estado = "espera"
        elif disponibles == 0:
            estado = "completa"
        else:
            estado = "disponible"

        tarjetas.append(
            {
                "id": c["id"],
                "fecha": c["fecha"],
                "dia": c["horario"]["dia_semana"].capitalize(),
                "fecha_txt": f"{fecha.day}/{fecha.month}",
                "hora": c["hora_inicio"][:5],
                "duracion": c["duracion"],
                "precio": c["precio"],
                "cupo_max": cupo_max,
                "disponibles": disponibles,
                "estado": estado,
                "posicion_espera": en_espera.get(c["id"]),
                "puede_cancelar": ahora_dt <= inicio_dt - timedelta(hours=HORAS_LIMITE_CANCELACION),
            }
        )
    return tarjetas


def offset_semana(fecha: date) -> int:
    hoy = ahora().date()
    lunes_hoy = hoy - timedelta(days=hoy.weekday())
    lunes = fecha - timedelta(days=fecha.weekday())
    return (lunes - lunes_hoy).days // 7


def agrupar_por_dia(clases: list[dict]) -> list[dict]:
    dias: dict[str, dict] = {}
    for c in clases:
        d = dias.setdefault(
            c["fecha"],
            {
                "fecha": c["fecha"],
                "dia": c["dia"],
                "fecha_txt": c["fecha_txt"],
                "horas": [],
                "inscripta_en": [],
                "espera_en": [],
                "todas_pasadas": True,
            },
        )
        d["horas"].append(c["hora"])
        if c["estado"] != "pasada":
            d["todas_pasadas"] = False
        if c["estado"] == "inscripta":
            d["inscripta_en"].append(c["hora"])
        if c["estado"] == "espera":
            d["espera_en"].append(c["hora"])
    return list(dias.values())


# Reservar, cancelar y anotarse en espera los hace la base (funciones SQL) con la
# sesión de la alumna: valida cupo y plazos con la clase bloqueada, sin sobreventa.

def inscribirse(sb: Client, clase_id: int) -> tuple[bool, str]:
    resultado = sb.rpc("reservar", {"p_id_clase": clase_id}).execute().data
    return {
        "ok": (True, "¡Reserva confirmada! Tenés un lugar en la clase."),
        "ya_reservada": (False, "Ya tenés esta clase reservada."),
        "completa": (False, "Clase completa. Podés anotarte en la lista de espera."),
    }.get(resultado, (False, "La clase ya no está disponible."))


def cancelar(sb: Client, clase_id: int) -> tuple[bool, str]:
    resultado = sb.rpc("cancelar_reserva", {"p_id_clase": clase_id}).execute().data
    return {
        "ok": (True, "Tu reserva fue cancelada. El lugar quedó liberado."),
        "iniciada": (False, "La clase ya comenzó, no se puede cancelar la reserva."),
        "plazo_vencido": (False, MENSAJE_PLAZO),
    }.get(resultado, (False, "No tenés una reserva activa en esta clase."))


def anotar_en_espera(sb: Client, clase_id: int) -> tuple[bool, str]:
    r = sb.rpc("anotar_en_espera", {"p_id_clase": clase_id}).execute().data
    if r["resultado"] == "ok":
        return True, f"Quedaste en la lista de espera (posición {r['posicion']})."
    return False, {
        "hay_lugar": "Hay lugares disponibles, podés reservar la clase directamente.",
        "ya_reservada": "Ya tenés esta clase reservada.",
        "ya_en_espera": "Ya estás en la lista de espera.",
    }.get(r["resultado"], "La clase ya no está disponible.")


def _item_clase(c: dict, **extra) -> dict:
    fecha = date.fromisoformat(c["fecha"])
    return {
        "fecha": c["fecha"],
        "dia": DIAS_ORDEN[fecha.weekday()].capitalize(),
        "fecha_txt": f"{fecha.day}/{fecha.month}",
        "hora": c["hora_inicio"][:5],
        "duracion": c["duracion"],
        "precio": c["precio"],
        "inicio": _inicio_clase(c),
        **extra,
    }


def mis_inscripciones(sb: Client, alumna_id: str) -> dict:
    columnas_clase = "clase(id, fecha, hora_inicio, duracion, precio)"
    reservas = (
        sb.table("reserva").select(f"id, estado, fecha_cancelacion, {columnas_clase}")
        .eq("id_alumna", alumna_id).execute().data
    )
    espera = (
        sb.table("listadeespera").select(f"posicion, {columnas_clase}")
        .eq("id_alumna", alumna_id).in_("estado", ["en_espera", "ofrecido"]).execute().data
    )
    ahora_dt = ahora()
    proximas, historial, en_espera = [], [], []
    for r in reservas:
        item = _item_clase(r["clase"], estado=r["estado"])
        if r["estado"] == "confirmada" and item["inicio"] > ahora_dt:
            proximas.append(item)
        else:
            item["estado_txt"] = "Cancelada" if r["estado"] == "cancelada" else "Clase pasada"
            historial.append(item)
    for e in espera:
        item = _item_clase(e["clase"], posicion=e["posicion"])
        if item["inicio"] > ahora_dt:
            en_espera.append(item)
    proximas.sort(key=lambda i: i["inicio"])
    en_espera.sort(key=lambda i: i["inicio"])
    historial.sort(key=lambda i: i["inicio"], reverse=True)
    return {"proximas": proximas, "en_espera": en_espera, "historial": historial}
