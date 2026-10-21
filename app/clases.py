from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from supabase import Client

TZ = ZoneInfo("America/Montevideo")
DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
# DIAS_ORDEN son los valores guardados en la base (sin tilde); esto es lo que se muestra.
NOMBRES_DIA = {
    "lunes": "Lunes", "martes": "Martes", "miercoles": "Miércoles", "jueves": "Jueves",
    "viernes": "Viernes", "sabado": "Sábado", "domingo": "Domingo",
}


def nombre_dia(dia: str | date) -> str:
    clave = DIAS_ORDEN[dia.weekday()] if isinstance(dia, date) else dia
    return NOMBRES_DIA.get(clave, clave.capitalize())
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
        .select("*, horario(activo, dia_semana), tipo_clase(nombre, imagen_url)")
        .gte("fecha", inicio.isoformat())
        .lte("fecha", fin.isoformat())
        .in_("estado", ["programada", "cancelada"])
        .execute()
        .data
    )
    clases = [c for c in clases if c["horario"] and c["horario"]["activo"]]
    clases.sort(key=lambda c: (c["fecha"], c["hora_inicio"]))
    if not clases:
        return []

    ids = [c["id"] for c in clases]
    ahora_dt = ahora()
    # Vence ofertas pasadas de los 30 minutos y ofrece los lugares libres (la base lo hace
    # con la clase bloqueada). Solo hace falta para las clases que todavía no empezaron.
    futuras = [c["id"] for c in clases if c["estado"] == "programada" and _inicio_clase(c) > ahora_dt]
    if futuras:
        sb.rpc("actualizar_listas_espera", {"p_ids": futuras}).execute()
    inscriptas = {
        r["id_clase"]
        for r in sb.table("reserva").select("id_clase")
        .eq("id_alumna", alumna_id).eq("estado", "confirmada").in_("id_clase", ids)
        .execute().data
    }
    # Posición actual (entre las que siguen en la lista) y oferta vigente de esta alumna.
    en_espera = {r["id_clase"]: r for r in sb.rpc("mi_lista_espera", {"p_ids": ids}).execute().data}
    ofertas = {r["id_clase"]: r["cantidad"] for r in sb.rpc("ofertas_vigentes", {"p_ids": ids}).execute().data}

    # Las alumnas solo ven sus propias reservas: el total por clase lo cuenta la base.
    confirmadas = {
        r["id_clase"]: r["cantidad"]
        for r in sb.rpc("reservas_confirmadas", {"p_ids": ids}).execute().data
    }

    tarjetas = []
    for c in clases:
        inicio_dt = _inicio_clase(c)
        cupo_max = c["cupo_max"]   # cada clase guarda su cupo (no cambia si se edita el tipo)
        # Un lugar ofrecido a la lista de espera queda guardado: no se muestra como libre.
        disponibles = max(0, cupo_max - confirmadas.get(c["id"], 0) - ofertas.get(c["id"], 0))
        espera = en_espera.get(c["id"])
        fecha = date.fromisoformat(c["fecha"])

        if c["estado"] == "cancelada":
            estado = "cancelada"
        elif inicio_dt <= ahora_dt:
            estado = "pasada"
        elif c["id"] in inscriptas:
            estado = "inscripta"
        elif espera and espera["estado"] == "ofrecido":
            estado = "oferta"
        elif espera:
            estado = "espera"
        elif disponibles == 0:
            estado = "completa"
        else:
            estado = "disponible"

        tarjetas.append(
            {
                "id": c["id"],
                "fecha": c["fecha"],
                "dia": nombre_dia(c["horario"]["dia_semana"]),
                "fecha_txt": f"{fecha.day}/{fecha.month}",
                "hora": c["hora_inicio"][:5],
                "duracion": c["duracion"],
                "precio": c["precio"],
                "cupo_max": cupo_max,
                "tipo": c["tipo_clase"]["nombre"] if c.get("tipo_clase") else "",
                "imagen": c["tipo_clase"]["imagen_url"] if c.get("tipo_clase") else None,
                "disponibles": disponibles,
                "estado": estado,
                "posicion_espera": espera["posicion"] if espera else None,
                "oferta_vence": _hora_local(espera["oferta_vence_en"]) if espera and espera["oferta_vence_en"] else "",
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
                "oferta_en": [],
                "canceladas_en": [],
                "todas_pasadas": True,
                "sin_clases_disponibles": True,
            },
        )
        d["horas"].append(c["hora"])
        d.setdefault("tipos", {})[c["hora"]] = c["tipo"]
        if c["estado"] != "pasada":
            d["todas_pasadas"] = False
        if c["estado"] not in ("pasada", "cancelada"):
            d["sin_clases_disponibles"] = False
        if c["estado"] == "cancelada":
            d["canceladas_en"].append(c["hora"])
        if c["estado"] == "inscripta":
            d["inscripta_en"].append(c["hora"])
        if c["estado"] == "espera":
            d["espera_en"].append(c["hora"])
        if c["estado"] == "oferta":
            d["oferta_en"].append(c["hora"])
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


def confirmar_oferta(sb: Client, clase_id: int) -> tuple[bool, str]:
    resultado = sb.rpc("confirmar_oferta", {"p_id_clase": clase_id}).execute().data
    return {
        "ok": (True, "¡Listo! Confirmaste tu lugar en la clase."),
        "ya_reservada": (False, "Ya tenés esta clase reservada."),
        "vencida": (False, "La oferta venció: pasaron los 30 minutos y el lugar se ofreció a la siguiente de la lista."),
        "sin_oferta": (False, "No tenés un lugar ofrecido en esta clase."),
    }.get(resultado, (False, "La clase ya no está disponible."))


def rechazar_oferta(sb: Client, clase_id: int) -> tuple[bool, str]:
    resultado = sb.rpc("rechazar_oferta", {"p_id_clase": clase_id}).execute().data
    if resultado == "ok":
        return True, "Listo, liberaste el lugar para la siguiente de la lista. Saliste de la lista de espera."
    return False, "No tenés un lugar ofrecido en esta clase (puede que la oferta ya haya vencido)."


def salir_lista_espera(sb: Client, clase_id: int) -> tuple[bool, str]:
    resultado = sb.rpc("salir_lista_espera", {"p_id_clase": clase_id}).execute().data
    if resultado == "ok":
        return True, "Saliste de la lista de espera."
    return False, "No estabas en la lista de espera de esta clase."


def _hora_local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%H:%M")


def _item_clase(c: dict, **extra) -> dict:
    fecha = date.fromisoformat(c["fecha"])
    return {
        "fecha": c["fecha"],
        "dia": nombre_dia(fecha),
        "fecha_txt": f"{fecha.day}/{fecha.month}",
        "hora": c["hora_inicio"][:5],
        "duracion": c["duracion"],
        "precio": c["precio"],
        "inicio": _inicio_clase(c),
        **extra,
    }


def _asistencia_de(reserva: dict) -> str | None:
    a = reserva.get("asistencia")
    if isinstance(a, list):
        a = a[0] if a else None
    return a["estado"] if a else None


def _estado_historial(reserva: dict) -> tuple[str, str]:
    if reserva["estado"] == "cancelada":
        if reserva.get("id_bloqueo") is not None:
            return "Cancelada por la profesora", "badge-espera"
        return "Cancelada", "badge-off"
    return {
        "presente": ("Presente", "badge-on"),
        "ausente": ("Ausente", "badge-ausente"),
    }.get(_asistencia_de(reserva), ("Sin registrar", "badge-off"))


def mis_inscripciones(sb: Client, alumna_id: str) -> dict:
    columnas_clase = "clase(id, fecha, hora_inicio, duracion, precio)"
    reservas = (
        sb.table("reserva").select(f"id, estado, fecha_cancelacion, id_bloqueo, asistencia(estado), {columnas_clase}")
        .eq("id_alumna", alumna_id).execute().data
    )
    mias = sb.table("listadeespera").select("id_clase").eq("id_alumna", alumna_id).in_(
        "estado", ["en_espera", "ofrecido"]).execute().data
    if mias:
        sb.rpc("actualizar_listas_espera", {"p_ids": [m["id_clase"] for m in mias]}).execute()
    espera = sb.rpc("mi_lista_espera", {}).execute().data
    datos_clase = {
        c["id"]: c for c in (sb.table("clase").select("id, fecha, hora_inicio, duracion, precio")
                             .in_("id", [e["id_clase"] for e in espera]).execute().data if espera else [])
    }
    ahora_dt = ahora()
    proximas, historial, en_espera, canceladas_profesora, ofertas = [], [], [], [], []
    for r in reservas:
        item = _item_clase(r["clase"], estado=r["estado"])
        if r["estado"] == "confirmada" and item["inicio"] > ahora_dt:
            proximas.append(item)
        else:
            if r["estado"] == "cancelada" and r["id_bloqueo"] is not None and item["inicio"] > ahora_dt:
                canceladas_profesora.append(item)
            item["estado_txt"], item["badge"] = _estado_historial(r)
            historial.append(item)
    for e in espera:
        c = datos_clase.get(e["id_clase"])
        if not c:
            continue
        item = _item_clase(c, id=c["id"], posicion=e["posicion"],
                           oferta_vence=_hora_local(e["oferta_vence_en"]) if e["oferta_vence_en"] else "")
        if item["inicio"] <= ahora_dt:
            continue
        (ofertas if e["estado"] == "ofrecido" else en_espera).append(item)
    proximas.sort(key=lambda i: i["inicio"])
    en_espera.sort(key=lambda i: i["inicio"])
    ofertas.sort(key=lambda i: i["inicio"])
    canceladas_profesora.sort(key=lambda i: i["inicio"])
    historial.sort(key=lambda i: i["inicio"], reverse=True)
    return {"proximas": proximas, "en_espera": en_espera, "historial": historial,
            "canceladas_profesora": canceladas_profesora, "ofertas": ofertas}


# ---------- profesora: clases del día y asistencia ----------

def _ventana_asistencia(c: dict) -> str:
    """antes | abierta | cerrada: solo se marca mientras dura la clase."""
    inicio = _inicio_clase(c)
    ahora_dt = ahora()
    if ahora_dt < inicio:
        return "antes"
    if ahora_dt <= inicio + timedelta(minutes=c["duracion"]):
        return "abierta"
    return "cerrada"


def dias_con_clases(sb: Client, desde: date, hasta: date) -> list[date]:
    filas = (
        sb.table("clase").select("fecha, horario(activo)")
        .gte("fecha", desde.isoformat()).lte("fecha", hasta.isoformat())
        .eq("estado", "programada").execute().data
    )
    return sorted({date.fromisoformat(c["fecha"]) for c in filas if c["horario"] and c["horario"]["activo"]})


def clases_del_dia(sb: Client, fecha: date) -> list[dict]:
    filas = (
        sb.table("clase").select("*, horario(activo), tipo_clase(nombre)")
        .eq("fecha", fecha.isoformat()).eq("estado", "programada")
        .order("hora_inicio").execute().data
    )
    filas = [c for c in filas if c["horario"] and c["horario"]["activo"]]
    if not filas:
        return []
    confirmadas = {
        r["id_clase"]: r["cantidad"]
        for r in sb.rpc("reservas_confirmadas", {"p_ids": [c["id"] for c in filas]}).execute().data
    }
    return [
        {
            "id": c["id"],
            "hora": c["hora_inicio"][:5],
            "duracion": c["duracion"],
            "cupo_max": c["cupo_max"],
            "tipo": c["tipo_clase"]["nombre"] if c.get("tipo_clase") else "",
            "reservadas": confirmadas.get(c["id"], 0),
            "ventana": _ventana_asistencia(c),
        }
        for c in filas
    ]


def clase_con_asistencia(sb: Client, clase_id: int) -> dict | None:
    filas = sb.table("clase").select("*, tipo_clase(nombre)").eq("id", clase_id).execute().data
    if not filas:
        return None
    c = filas[0]
    reservas = (
        sb.table("reserva").select("id, profiles(nombre, apellido), asistencia(estado)")
        .eq("id_clase", clase_id).eq("estado", "confirmada").execute().data
    )
    alumnas = []
    for r in reservas:
        alumnas.append({
            "id_reserva": r["id"],
            "nombre": f"{r['profiles']['nombre']} {r['profiles']['apellido']}",
            "estado": _asistencia_de(r),
        })
    alumnas.sort(key=lambda a: a["nombre"].lower())
    fecha = date.fromisoformat(c["fecha"])
    if c["estado"] == "programada" and _inicio_clase(c) > ahora():
        sb.rpc("actualizar_listas_espera", {"p_ids": [c["id"]]}).execute()
    filas_espera = (
        sb.table("listadeespera").select("posicion, estado, oferta_vence_en, profiles(nombre, apellido)")
        .eq("id_clase", clase_id).in_("estado", ["en_espera", "ofrecido"]).order("posicion").execute().data
    )
    espera = [
        {"posicion": i, "nombre": f"{e['profiles']['nombre']} {e['profiles']['apellido']}",
         "oferta_vence": _hora_local(e["oferta_vence_en"]) if e["estado"] == "ofrecido" else ""}
        for i, e in enumerate(filas_espera, start=1)
    ]
    return {
        "id": c["id"],
        "fecha": fecha,
        "titulo": f"{nombre_dia(fecha)} {fecha.day}/{fecha.month} · {c['hora_inicio'][:5]}"
                  + (f" · {c['tipo_clase']['nombre']}" if c.get("tipo_clase") else ""),
        "hora_fin": (_inicio_clase(c) + timedelta(minutes=c["duracion"])).strftime("%H:%M"),
        "hora": c["hora_inicio"][:5],
        "ventana": _ventana_asistencia(c),
        "alumnas": alumnas,
        "presentes": sum(a["estado"] == "presente" for a in alumnas),
        "ausentes": sum(a["estado"] == "ausente" for a in alumnas),
        "espera": espera,
    }


def marcar_asistencia(sb: Client, id_reserva: int, estado: str) -> tuple[bool, str]:
    if estado not in ("presente", "ausente"):
        return False, "Elegí presente o ausente."
    resultado = sb.rpc("marcar_asistencia", {"p_id_reserva": id_reserva, "p_estado": estado}).execute().data
    return {
        "ok": (True, "Asistencia guardada."),
        "fuera_de_horario": (False, "La asistencia solo se puede marcar el día de la clase, mientras dura."),
        "no_autorizada": (False, "Solo la profesora puede marcar asistencia."),
    }.get(resultado, (False, "Esa alumna ya no tiene reserva en esta clase."))


# ---------- profesora: planificación (licencias, vacaciones y cancelaciones) ----------
# Un bloqueo cancela las clases de un rango de fechas (todas o las de un horario).
# Crear y eliminar lo hacen las funciones crear_bloqueo / quitar_bloqueo de la base;
# acá solo se arma la vista previa (sin escribir nada) y se traducen los resultados.

MESES_CORTOS = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"]
LARGO_MAX_MOTIVO = 200
ESTADOS_BLOQUEO = {"proximo": "Próximo", "en_curso": "En curso", "finalizado": "Finalizado"}


def fecha_corta(f: date) -> str:
    return f"{nombre_dia(f)} {f.day} {MESES_CORTOS[f.month]}"


def _rango_texto(desde: date, hasta: date) -> str:
    return fecha_corta(desde) if desde == hasta else f"Del {fecha_corta(desde)} al {fecha_corta(hasta)}"


def _estado_bloqueo(desde: date, hasta: date, hoy: date) -> str:
    if hasta < hoy:
        return "finalizado"
    return "en_curso" if desde <= hoy else "proximo"


def horarios_para_elegir(sb: Client) -> list[dict]:
    filas = sb.table("horario").select("id, dia_semana, hora_inicio").eq("activo", True).execute().data
    filas.sort(key=lambda h: (DIAS_ORDEN.index(h["dia_semana"]), h["hora_inicio"]))
    return [{"id": h["id"], "texto": f"{nombre_dia(h['dia_semana'])} {h['hora_inicio'][:5]}"} for h in filas]


def validar_bloqueo(sb: Client, desde: date | None, hasta: date | None, id_horario: int | None,
                    motivo: str) -> list[str]:
    hoy = ahora().date()
    errores = []
    if desde is None or hasta is None:
        errores.append("Elegí la fecha de inicio y la de finalización.")
    elif desde < hoy:
        errores.append("La fecha de inicio no puede ser anterior a hoy.")
    elif hasta < desde:
        errores.append("La fecha de finalización no puede ser anterior a la de inicio.")
    if not motivo.strip():
        errores.append("Escribí el motivo del bloqueo.")
    elif len(motivo.strip()) > LARGO_MAX_MOTIVO:
        errores.append(f"El motivo puede tener como máximo {LARGO_MAX_MOTIVO} caracteres.")
    if id_horario is not None and id_horario not in {h["id"] for h in horarios_para_elegir(sb)}:
        errores.append("El horario elegido no existe o está inactivo.")
    return errores


def vista_previa_bloqueo(sb: Client, desde: date, hasta: date, id_horario: int | None) -> dict:
    """Qué pasaría si se confirma el bloqueo. Solo lee: no modifica la base."""
    consulta = (
        sb.table("clase").select("id, fecha, hora_inicio, estado, id_horario")
        .gte("fecha", desde.isoformat()).lte("fecha", hasta.isoformat())
        .in_("estado", ["programada", "cancelada"])
    )
    if id_horario is not None:
        consulta = consulta.eq("id_horario", id_horario)
    filas = sorted(consulta.execute().data, key=lambda c: (c["fecha"], c["hora_inicio"]))
    ahora_dt = ahora()
    a_cancelar = [c for c in filas if c["estado"] == "programada" and _inicio_clase(c) > ahora_dt]
    empezadas = [c for c in filas if c["estado"] == "programada" and _inicio_clase(c) <= ahora_dt]
    ya_canceladas = [c for c in filas if c["estado"] == "cancelada"]

    ids = [c["id"] for c in a_cancelar]
    alumnas: dict[int, list[str]] = {i: [] for i in ids}
    espera: dict[int, int] = {i: 0 for i in ids}
    if ids:
        for r in (sb.table("reserva").select("id_clase, profiles(nombre, apellido)")
                  .in_("id_clase", ids).eq("estado", "confirmada").execute().data):
            alumnas[r["id_clase"]].append(f"{r['profiles']['nombre']} {r['profiles']['apellido']}")
        for e in (sb.table("listadeespera").select("id_clase")
                  .in_("id_clase", ids).in_("estado", ["en_espera", "ofrecido"]).execute().data):
            espera[e["id_clase"]] += 1

    def item(c, **extra):
        f = date.fromisoformat(c["fecha"])
        return {"fecha_txt": fecha_corta(f), "hora": c["hora_inicio"][:5], **extra}

    ultima = sb.table("clase").select("fecha").order("fecha", desc=True).limit(1).execute().data
    ultima_generada = date.fromisoformat(ultima[0]["fecha"]) if ultima else None
    return {
        "a_cancelar": [item(c, alumnas=sorted(alumnas[c["id"]], key=str.lower), espera=espera[c["id"]]) for c in a_cancelar],
        "empezadas": [item(c) for c in empezadas],
        "ya_canceladas": [item(c) for c in ya_canceladas],
        "total_reservas": sum(len(v) for v in alumnas.values()),
        "total_espera": sum(espera.values()),
        "faltan_generar": ultima_generada is None or hasta > ultima_generada,
        "ultima_generada": fecha_corta(ultima_generada) if ultima_generada else "",
    }


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def confirmar_bloqueo(sb: Client, desde: date, hasta: date, id_horario: int | None, motivo: str,
                      reservas_previstas: int | None = None) -> tuple[bool, str]:
    tipo = "puntual" if id_horario is not None or desde == hasta else "licencia"
    r = sb.rpc("crear_bloqueo", {
        "p_tipo": tipo, "p_desde": desde.isoformat(), "p_hasta": hasta.isoformat(),
        "p_id_horario": id_horario, "p_motivo": motivo.strip(),
    }).execute().data
    if r["resultado"] != "ok":
        return False, {
            "no_autorizada": "Solo la profesora puede crear bloqueos.",
            "fechas_invalidas": "Revisá las fechas: el inicio tiene que ser de hoy en adelante.",
            "motivo_vacio": "Escribí el motivo del bloqueo.",
            "horario_invalido": "El horario elegido no existe.",
        }.get(r["resultado"], "No se pudo guardar el bloqueo.")
    if r["clases"] == 0:
        return True, ("Bloqueo guardado. No había clases para cancelar todavía; "
                      "las que se generen en esas fechas ya van a quedar canceladas.")
    texto = f"Bloqueo guardado. Se {'canceló' if r['clases'] == 1 else 'cancelaron'} {_plural(r['clases'], 'clase', 'clases')}"
    if r["reservas"]:
        texto += f" y {_plural(r['reservas'], 'reserva', 'reservas')} (las alumnas lo ven como cancelada por la profesora)"
    if r["espera"]:
        texto += f"; se cerró la lista de espera de {_plural(r['espera'], 'alumna', 'alumnas')}"
    texto += "."
    if reservas_previstas is not None and reservas_previstas != r["reservas"]:
        texto += (f" Las reservas cambiaron desde la vista previa (había {reservas_previstas}): "
                  "se cancelaron las que existían al confirmar.")
    return True, texto


def bloqueos(sb: Client) -> dict:
    filas = sb.table("bloqueo").select("*, horario(dia_semana, hora_inicio)").order("fecha_desde").execute().data
    hoy = ahora().date()
    ahora_dt = ahora()
    if not filas:
        return {"vigentes": [], "finalizados": []}
    desde_min = min(b["fecha_desde"] for b in filas)
    hasta_max = max(b["fecha_hasta"] for b in filas)
    canceladas = (
        sb.table("clase").select("id, fecha, hora_inicio, id_horario").eq("estado", "cancelada")
        .gte("fecha", desde_min).lte("fecha", hasta_max).execute().data
    )
    ids = [b["id"] for b in filas]
    reservas = (
        sb.table("reserva").select("id_bloqueo, id_clase").in_("id_bloqueo", ids)
        .eq("estado", "cancelada").execute().data
    )

    def cubre(b, c):
        return b["fecha_desde"] <= c["fecha"] <= b["fecha_hasta"] and b["id_horario"] in (None, c["id_horario"])

    vigentes, finalizados = [], []
    for b in filas:
        desde, hasta = date.fromisoformat(b["fecha_desde"]), date.fromisoformat(b["fecha_hasta"])
        propias = [c for c in canceladas if cubre(b, c)]
        # Volverían al eliminarlo: clases futuras que ningún otro bloqueo cubre.
        vuelven = {c["id"] for c in propias if _inicio_clase(c) > ahora_dt
                   and not any(cubre(o, c) for o in filas if o["id"] != b["id"])}
        estado = _estado_bloqueo(desde, hasta, hoy)
        h = b["horario"]
        item = {
            "id": b["id"],
            "estado": estado,
            "estado_txt": ESTADOS_BLOQUEO[estado],
            "rango_txt": _rango_texto(desde, hasta),
            "alcance_txt": f"Solo {nombre_dia(h['dia_semana'])} {h['hora_inicio'][:5]}" if h else "Todas las clases",
            "motivo": b["motivo"],
            "clases": len(propias),
            "reservas": sum(r["id_bloqueo"] == b["id"] for r in reservas),
            "vuelven_clases": len(vuelven),
            "vuelven_reservas": sum(r["id_bloqueo"] == b["id"] and r["id_clase"] in vuelven for r in reservas),
        }
        (finalizados if estado == "finalizado" else vigentes).append(item)
    finalizados.reverse()
    return {"vigentes": vigentes, "finalizados": finalizados}


def eliminar_bloqueo(sb: Client, id_bloqueo: int) -> tuple[bool, str]:
    r = sb.rpc("quitar_bloqueo", {"p_id": id_bloqueo}).execute().data
    if r["resultado"] != "ok":
        return False, {
            "finalizado": "Los bloqueos finalizados se conservan como historial y no se pueden eliminar.",
            "no_autorizada": "Solo la profesora puede eliminar bloqueos.",
        }.get(r["resultado"], "No se encontró ese bloqueo.")
    texto = f"Bloqueo eliminado. {_plural(r['clases'], 'clase vuelve', 'clases vuelven')} a estar disponible{'s' if r['clases'] != 1 else ''}"
    if r["reservas"]:
        texto += f" y se {'recuperó' if r['reservas'] == 1 else 'recuperaron'} {_plural(r['reservas'], 'reserva', 'reservas')}"
    texto += "."
    if r["sin_cupo"]:
        texto += (f" {_plural(r['sin_cupo'], 'reserva no se pudo', 'reservas no se pudieron')} recuperar "
                  "porque el cupo de la clase ahora es menor.")
    return True, texto
