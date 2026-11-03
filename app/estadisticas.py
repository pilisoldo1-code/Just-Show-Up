from collections import Counter
from datetime import date, timedelta

from supabase import Client

from app import clases

PERIODOS = {
    "mes": "Este mes",
    "3meses": "Últimos 3 meses",
    "todo": "Todo",
}
PERIODO_DEFECTO = "mes"
MESES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
    "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre",
]
MESES_CORTOS = [
    "", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic",
]
TOP = 3
_PAGINA = 1000  # PostgREST devuelve como máximo 1000 filas por pedido

# Tamaño del SVG de la evolución mensual (coordenadas que arma _preparar_linea).
_ANCHO_LINEA = 560
_ALTO_LINEA = 140
_PAD_LINEA = 28
_RADIO_DONA = 45


def desde_de(periodo: str, hoy: date) -> date | None:
    """Primer día del período (inclusive); None = sin límite. "3meses" son el mes actual y los dos anteriores."""
    if periodo == "mes":
        return hoy.replace(day=1)
    if periodo == "3meses":
        mes = hoy.month - 2
        anio = hoy.year
        if mes < 1:
            mes += 12
            anio -= 1
        return date(anio, mes, 1)
    return None


def _leer_asistencias(sb: Client) -> list[dict]:
    filas, inicio = [], 0
    while True:
        lote = (
            sb.table("asistencia")
            .select("estado, reserva(id_alumna, estado, profiles(nombre, apellido), "
                    "clase(fecha, hora_inicio, id_profesora, id_tipo_clase))")
            .order("id")
            .range(inicio, inicio + _PAGINA - 1)
            .execute()
            .data
        )
        filas.extend(lote)
        if len(lote) < _PAGINA:
            return filas
        inicio += _PAGINA


def _registros(filas: list[dict]) -> list[dict]:
    """Aplana cada asistencia en {estado, fecha, hora, alumna_id, alumna, id_profesora, id_tipo_clase};
    descarta las de reservas canceladas."""
    salida = []
    for a in filas:
        r = a.get("reserva") or {}
        c = r.get("clase") or {}
        if r.get("estado") != "confirmada" or not c.get("fecha"):
            continue
        p = r.get("profiles") or {}
        salida.append({
            "estado": a["estado"],
            "fecha": date.fromisoformat(c["fecha"]),
            "hora": c["hora_inicio"][:5],
            "alumna_id": r["id_alumna"],
            "alumna": f"{p.get('nombre', '')} {p.get('apellido', '')}".strip() or "Alumna sin nombre",
            "id_profesora": c.get("id_profesora"),
            "id_tipo_clase": c.get("id_tipo_clase"),
        })
    return salida


def _ganadores(conteo: Counter) -> tuple[list, int]:
    """Las claves con el máximo (puede haber empate) y ese máximo."""
    if not conteo:
        return [], 0
    maximo = max(conteo.values())
    return [k for k, v in conteo.items() if v == maximo], maximo


def _unir(textos: list[str]) -> str:
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + " y " + textos[-1]


def _top(conteo: Counter, nombres: dict) -> list[dict]:
    orden = sorted(conteo.items(), key=lambda kv: (-kv[1], nombres[kv[0]].lower()))
    return [{"nombre": nombres[k], "cantidad": v} for k, v in orden[:TOP]]


def _meses_en_rango(desde: date | None, hasta: date, regs: list[dict]) -> list[tuple[int, int]]:
    """Todos los meses entre desde y hasta (inclusive), aunque no tengan datos: así la gráfica de
    evolución tiene una escala completa y no un punto suelto por mes con asistencias.
    Si desde es None ("Todo"), arranca en el primer mes con datos (si no hay datos, no hay meses)."""
    if desde is None:
        if not regs:
            return []
        desde = min(r["fecha"] for r in regs).replace(day=1)
    anio, mes = desde.year, desde.month
    meses = []
    while (anio, mes) <= (hasta.year, hasta.month):
        meses.append((anio, mes))
        mes += 1
        if mes > 12:
            mes, anio = 1, anio + 1
    return meses


def calcular(registros: list[dict], desde: date | None, hasta: date,
            id_profesora: str | None = None, id_tipo_clase: int | None = None) -> dict:
    regs = [
        r for r in registros
        if (desde is None or r["fecha"] >= desde) and r["fecha"] <= hasta
        and (id_profesora is None or r["id_profesora"] == id_profesora)
        and (id_tipo_clase is None or r["id_tipo_clase"] == id_tipo_clase)
    ]
    presentes = [r for r in regs if r["estado"] == "presente"]
    ausentes = [r for r in regs if r["estado"] == "ausente"]
    nombres = {r["alumna_id"]: r["alumna"] for r in regs}

    dias, n_dia = _ganadores(Counter(r["fecha"].weekday() for r in presentes))
    horarios, n_horario = _ganadores(Counter((r["fecha"].weekday(), r["hora"]) for r in presentes))
    meses, n_mes = _ganadores(Counter((r["fecha"].year, r["fecha"].month) for r in presentes))
    marcadas = len(presentes) + len(ausentes)

    evolucion = []
    for anio, mes in _meses_en_rango(desde, hasta, regs):
        pres = sum(1 for r in presentes if (r["fecha"].year, r["fecha"].month) == (anio, mes))
        aus = sum(1 for r in ausentes if (r["fecha"].year, r["fecha"].month) == (anio, mes))
        evolucion.append({"etiqueta": f"{MESES_CORTOS[mes]} {anio}", "presentes": pres, "ausentes": aus})

    return {
        "presentes": len(presentes),
        "ausentes": len(ausentes),
        "porcentaje": round(100 * len(presentes) / marcadas) if marcadas else None,
        "dia": _unir([clases.nombre_dia(clases.DIAS_ORDEN[d]) for d in sorted(dias)]) if dias else "",
        "dia_cantidad": n_dia,
        "horario": _unir([f"{clases.nombre_dia(clases.DIAS_ORDEN[d])} {h}" for d, h in sorted(horarios)])
        if horarios else "",
        "horario_cantidad": n_horario,
        "mes": _unir([f"{MESES[m]} {a}" for a, m in sorted(meses)]) if meses else "",
        "mes_cantidad": n_mes,
        "top_asistencias": _top(Counter(r["alumna_id"] for r in presentes), nombres),
        "top_faltas": _top(Counter(r["alumna_id"] for r in ausentes), nombres),
        "evolucion": evolucion,
    }


# ---------- ocupación y tipos de clase más populares (clase + reserva directo, no asistencia) ----------

def _leer_clases_terminadas(sb: Client, desde: date | None, hasta: date,
                            id_profesora: str | None, id_tipo_clase: int | None) -> list[dict]:
    """Clases programadas (no canceladas) que ya terminaron, dentro del rango. "Terminada" usa el mismo
    corte que ya usa asistencias_pendientes_resumen: inicio + duración <= ahora. No se incluyen futuras
    para no mezclar clases que todavía se están llenando con el resultado final."""
    filas, inicio = [], 0
    while True:
        consulta = (
            sb.table("clase")
            .select("id, fecha, hora_inicio, duracion, cupo_max, id_profesora, id_tipo_clase, tipo_clase(nombre)")
            .eq("estado", "programada")
            .lte("fecha", hasta.isoformat())
            .order("id")
            .range(inicio, inicio + _PAGINA - 1)
        )
        if desde is not None:
            consulta = consulta.gte("fecha", desde.isoformat())
        if id_profesora is not None:
            consulta = consulta.eq("id_profesora", id_profesora)
        if id_tipo_clase is not None:
            consulta = consulta.eq("id_tipo_clase", id_tipo_clase)
        lote = consulta.execute().data
        filas.extend(lote)
        if len(lote) < _PAGINA:
            break
        inicio += _PAGINA

    ahora_dt = clases.ahora()
    return [c for c in filas if clases._inicio_clase(c) + timedelta(minutes=c["duracion"]) <= ahora_dt]


def _reservas_confirmadas_de(sb: Client, ids_clase: list[int]) -> Counter:
    """Cuenta reservas vigentes (estado='confirmada') por clase, leídas de reserva directo
    (no de clase.cantidad_inscriptas) para no depender de ningún valor cacheado y así no contar
    nunca una reserva cancelada. A diferencia de "clase", la política RLS de "reserva" sí filtra
    por profesora para quien no es admin (verificado en tests/base_local), así que esto queda
    protegido dos veces: por el filtro de id_profesora en _leer_clases_terminadas y por la base."""
    if not ids_clase:
        return Counter()
    conteo = Counter()
    for i in range(0, len(ids_clase), _PAGINA):
        lote = (
            sb.table("reserva").select("id_clase")
            .in_("id_clase", ids_clase[i:i + _PAGINA]).eq("estado", "confirmada")
            .execute().data
        )
        conteo.update(f["id_clase"] for f in lote)
    return conteo


def _ocupacion_promedio(terminadas: list[dict], confirmadas: Counter) -> int | None:
    """Promedio simple de (reservas vigentes / cupo) por clase, no ponderado por cupo: cada clase
    pesa igual sin importar su capacidad. Con un promedio ponderado (reservas totales / cupos
    totales) una clase de cupo grande pesaría mucho más que una de cupo chico (ej. una clase
    individual de Pilates con cupo 1), y el número dejaría de responder "una clase típica, en
    promedio, ¿qué tan llena está" para responder algo dominado por las clases más grandes."""
    ratios = [confirmadas.get(c["id"], 0) / c["cupo_max"] for c in terminadas if c["cupo_max"]]
    if not ratios:
        return None
    return round(100 * sum(ratios) / len(ratios))


def _tipos_populares(terminadas: list[dict], confirmadas: Counter) -> list[dict]:
    """Reservas vigentes por tipo de clase, solo de clases ya terminadas del período elegido
    (mismo recorte que ocupación): no mezcla la demanda de clases futuras, todavía incompletas,
    con el resultado histórico."""
    conteo, nombres = Counter(), {}
    for c in terminadas:
        n = confirmadas.get(c["id"], 0)
        if not n:
            continue
        t = c.get("tipo_clase") or {}
        conteo[c["id_tipo_clase"]] += n
        nombres[c["id_tipo_clase"]] = t.get("nombre") or "Sin tipo"
    orden = sorted(conteo.items(), key=lambda kv: (-kv[1], nombres[kv[0]].lower()))
    return [{"nombre": nombres[k], "cantidad": v} for k, v in orden]


def pendientes(sb: Client) -> dict:
    """Asistencias sin registrar: reusa clases.pendientes_totales (ya existe para la pantalla de
    Asistencias, PR #34) en vez de escribir una función nueva. "Sin registrar" no es un valor
    guardado en asistencia.estado (que solo admite presente/ausente): es que no existe ninguna
    fila en asistencia para esa reserva confirmada de una clase ya terminada — así lo calcula
    la función de la base (asistencias_pendientes_resumen), y acá se usa igual.
    Nota: siempre es el total de la empresa (o de la profesora, si no es admin); no se recorta
    por período ni por tipo de clase ni por la profesora elegida en el filtro de "Por profesora"
    porque son pendientes de completar ahora, no una estadística histórica, y no hace falta una
    función nueva solo para eso."""
    return clases.pendientes_totales(sb)


# ---------- preparar los datos ya calculados para dibujar los SVG (sin lógica en la plantilla) ----------

def _preparar_linea(evolucion: list[dict]) -> dict | None:
    if not evolucion:
        return None
    maximo = max([m["presentes"] for m in evolucion] + [m["ausentes"] for m in evolucion] + [0])
    if maximo == 0:
        maximo = 1
    if len(evolucion) > 1:
        paso = (_ANCHO_LINEA - 2 * _PAD_LINEA) / (len(evolucion) - 1)
        xs = [_PAD_LINEA + i * paso for i in range(len(evolucion))]
    else:
        xs = [_ANCHO_LINEA / 2]
    escala = (_ALTO_LINEA - 2 * _PAD_LINEA) / maximo

    def y(v):
        return _ALTO_LINEA - _PAD_LINEA - v * escala

    marcas_y = sorted({0, maximo // 2, maximo}) if maximo > 1 else [0, 1]
    return {
        "ancho": _ANCHO_LINEA, "alto": _ALTO_LINEA, "base_y": round(y(0), 1),
        "puntos_presentes": " ".join(f"{x:.1f},{y(m['presentes']):.1f}" for x, m in zip(xs, evolucion)),
        "puntos_ausentes": " ".join(f"{x:.1f},{y(m['ausentes']):.1f}" for x, m in zip(xs, evolucion)),
        "un_mes": len(evolucion) == 1,
        "etiquetas": [{"x": round(x, 1), "texto": m["etiqueta"]} for x, m in zip(xs, evolucion)],
        "marcas_y": [{"y": round(y(v), 1), "texto": str(v)} for v in marcas_y],
    }


def _preparar_dona(presentes: int, ausentes: int) -> dict | None:
    total = presentes + ausentes
    if not total:
        return None
    circunferencia = round(2 * 3.14159265358979 * _RADIO_DONA, 1)
    return {
        "radio": _RADIO_DONA,
        "circunferencia": circunferencia,
        "dash_presentes": round(circunferencia * presentes / total, 1),
        "porcentaje": round(100 * presentes / total),
    }


def _preparar_barras(tipos_populares: list[dict]) -> list[dict]:
    if not tipos_populares:
        return []
    maximo = tipos_populares[0]["cantidad"]
    return [{**t, "porcentaje": round(100 * t["cantidad"] / maximo)} for t in tipos_populares]


def estadisticas(sb: Client, periodo: str, usuario, id_tipo_clase: int | None = None,
                 id_profesora: str | None = None) -> dict:
    """`usuario` decide el alcance real: una profesora común SIEMPRE ve solo sus propias clases,
    sin importar qué id_profesora haya llegado por parámetro (clase/reserva no están filtradas por
    profesora en RLS, a diferencia de asistencia; acá es donde eso se controla). Solo la
    administradora puede pedir otra profesora de su empresa, o nada (toda la empresa)."""
    if not usuario.es_admin:
        id_profesora = usuario.id

    hoy = clases.ahora().date()
    desde = desde_de(periodo, hoy)

    datos = calcular(_registros(_leer_asistencias(sb)), desde, hoy, id_profesora, id_tipo_clase)

    terminadas = _leer_clases_terminadas(sb, desde, hoy, id_profesora, id_tipo_clase)
    confirmadas = _reservas_confirmadas_de(sb, [c["id"] for c in terminadas])
    datos["ocupacion"] = _ocupacion_promedio(terminadas, confirmadas)
    datos["tipos_populares"] = _tipos_populares(terminadas, confirmadas)
    datos["pendientes"] = pendientes(sb)

    datos["grafica_evolucion"] = _preparar_linea(datos["evolucion"])
    datos["grafica_distribucion"] = _preparar_dona(datos["presentes"], datos["ausentes"])
    datos["grafica_tipos"] = _preparar_barras(datos["tipos_populares"])
    return datos
