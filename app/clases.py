from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db import get_supabase

TZ = ZoneInfo("America/Montevideo")
DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
HORAS_LIMITE_CANCELACION = 1
MENSAJE_PLAZO = (
    "El plazo de cancelación ya finalizó. "
    "Las cancelaciones deben realizarse al menos 1 hora antes de la clase."
)

# PROVISORIO hasta que exista Supabase Auth: alumnas de prueba para elegir en la grilla.
ALUMNAS_PROVISORIAS = {
    "a0000000-0000-4000-8000-000000000001": "Alumna Uno",
    "a0000000-0000-4000-8000-000000000002": "Alumna Dos",
    "a0000000-0000-4000-8000-000000000003": "Alumna Tres",
}


def ahora() -> datetime:
    return datetime.now(TZ)


def _inicio_clase(clase: dict) -> datetime:
    return datetime.combine(
        date.fromisoformat(clase["fecha"]),
        time.fromisoformat(clase["hora_inicio"]),
        tzinfo=TZ,
    )


def generar_clases(inicio_semana: date) -> None:
    sb = get_supabase()
    horarios = sb.table("horario").select("*").eq("activo", True).execute().data
    filas = [
        {
            "id_horario": h["id"],
            "fecha": (inicio_semana + timedelta(days=DIAS_ORDEN.index(h["dia_semana"]))).isoformat(),
            "hora_inicio": h["hora_inicio"],
            "duracion": h["duracion"],
            "precio": h["precio"],
        }
        for h in horarios
    ]
    if filas:
        sb.table("clase").upsert(
            filas, on_conflict="id_horario,fecha", ignore_duplicates=True
        ).execute()


def clases_entre(inicio: date, fin: date, alumna_id: str) -> list[dict]:
    sb = get_supabase()
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

    confirmadas = Counter(
        r["id_clase"]
        for r in sb.table("reserva").select("id_clase")
        .eq("estado", "confirmada").in_("id_clase", ids).execute().data
    )

    ahora_dt = ahora()
    tarjetas = []
    for c in clases:
        inicio_dt = _inicio_clase(c)
        cupo_max = c["horario"]["cupo_max"]
        disponibles = max(0, cupo_max - confirmadas[c["id"]])
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


def _confirmadas(clase_id: int) -> int:
    return (
        get_supabase().table("reserva").select("id", count="exact")
        .eq("id_clase", clase_id).eq("estado", "confirmada").execute().count
    )


def _sincronizar_inscriptas(clase_id: int) -> None:
    get_supabase().table("clase").update(
        {"cantidad_inscriptas": _confirmadas(clase_id)}
    ).eq("id", clase_id).execute()


def _clase(clase_id: int) -> dict:
    return (
        get_supabase().table("clase").select("*, horario(cupo_max)")
        .eq("id", clase_id).single().execute().data
    )


def _reserva_activa(alumna_id: str, clase_id: int) -> list[dict]:
    return (
        get_supabase().table("reserva").select("id")
        .eq("id_alumna", alumna_id).eq("id_clase", clase_id).eq("estado", "confirmada")
        .execute().data
    )


def inscribirse(alumna_id: str, clase_id: int) -> tuple[bool, str]:
    sb = get_supabase()
    clase = _clase(clase_id)
    if clase["estado"] != "programada" or ahora() >= _inicio_clase(clase):
        return False, "La clase ya no está disponible."
    if _reserva_activa(alumna_id, clase_id):
        return False, "Ya tenés esta clase reservada."
    cupo = clase["horario"]["cupo_max"]
    if _confirmadas(clase_id) >= cupo:
        return False, "Clase completa. Podés anotarte en la lista de espera."

    nueva = sb.table("reserva").insert(
        {"id_alumna": alumna_id, "id_clase": clase_id}
    ).execute().data[0]

    if _confirmadas(clase_id) > cupo:
        sb.table("reserva").update(
            {"estado": "cancelada", "fecha_cancelacion": ahora().isoformat()}
        ).eq("id", nueva["id"]).execute()
        _sincronizar_inscriptas(clase_id)
        return False, "La clase se completó justo antes de tu reserva."

    _sincronizar_inscriptas(clase_id)
    sb.table("listadeespera").update({"estado": "confirmado"}).eq(
        "id_alumna", alumna_id
    ).eq("id_clase", clase_id).in_("estado", ["en_espera", "ofrecido"]).execute()
    return True, "¡Reserva confirmada! Tenés un lugar en la clase."


def cancelar(alumna_id: str, clase_id: int) -> tuple[bool, str]:
    reservas = _reserva_activa(alumna_id, clase_id)
    if not reservas:
        return False, "No tenés una reserva activa en esta clase."

    inicio = _inicio_clase(_clase(clase_id))
    ahora_dt = ahora()
    if ahora_dt >= inicio:
        return False, "La clase ya comenzó, no se puede cancelar la reserva."
    if ahora_dt > inicio - timedelta(hours=HORAS_LIMITE_CANCELACION):
        return False, MENSAJE_PLAZO

    # UPDATE condicionado a estado='confirmada': si dos pedidos llegan a la vez,
    # solo uno modifica la fila y el otro no encuentra nada para actualizar.
    actualizadas = (
        get_supabase().table("reserva")
        .update({"estado": "cancelada", "fecha_cancelacion": ahora_dt.isoformat()})
        .eq("id", reservas[0]["id"]).eq("estado", "confirmada")
        .execute().data
    )
    if not actualizadas:
        return False, "Esa reserva ya estaba cancelada."
    _sincronizar_inscriptas(clase_id)
    return True, "Tu reserva fue cancelada. El lugar quedó liberado."


def anotar_en_espera(alumna_id: str, clase_id: int) -> tuple[bool, str]:
    sb = get_supabase()
    clase = _clase(clase_id)
    if clase["estado"] != "programada" or ahora() >= _inicio_clase(clase):
        return False, "La clase ya no está disponible."
    if _confirmadas(clase_id) < clase["horario"]["cupo_max"]:
        return False, "Hay lugares disponibles, podés reservar la clase directamente."
    if _reserva_activa(alumna_id, clase_id):
        return False, "Ya tenés esta clase reservada."

    activas = (
        sb.table("listadeespera").select("id_alumna")
        .eq("id_clase", clase_id).in_("estado", ["en_espera", "ofrecido"]).execute().data
    )
    if any(a["id_alumna"] == alumna_id for a in activas):
        return False, "Ya estás en la lista de espera."

    posicion = len(activas) + 1
    sb.table("listadeespera").insert(
        {"id_alumna": alumna_id, "id_clase": clase_id, "posicion": posicion}
    ).execute()
    return True, f"Quedaste en la lista de espera (posición {posicion})."
