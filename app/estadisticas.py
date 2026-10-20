from collections import Counter
from datetime import date

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
TOP = 3
_PAGINA = 1000  # PostgREST devuelve como máximo 1000 filas por pedido


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
            .select("estado, reserva(id_alumna, estado, profiles(nombre, apellido), clase(fecha, hora_inicio))")
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
    """Aplana cada asistencia en {estado, fecha, hora, alumna_id, alumna}; descarta las de reservas canceladas."""
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


def calcular(registros: list[dict], desde: date | None, hasta: date) -> dict:
    regs = [r for r in registros if (desde is None or r["fecha"] >= desde) and r["fecha"] <= hasta]
    presentes = [r for r in regs if r["estado"] == "presente"]
    ausentes = [r for r in regs if r["estado"] == "ausente"]
    nombres = {r["alumna_id"]: r["alumna"] for r in regs}

    dias, n_dia = _ganadores(Counter(r["fecha"].weekday() for r in presentes))
    horarios, n_horario = _ganadores(Counter((r["fecha"].weekday(), r["hora"]) for r in presentes))
    meses, n_mes = _ganadores(Counter((r["fecha"].year, r["fecha"].month) for r in presentes))
    marcadas = len(presentes) + len(ausentes)

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
    }


def estadisticas(sb: Client, periodo: str) -> dict:
    hoy = clases.ahora().date()
    return calcular(_registros(_leer_asistencias(sb)), desde_de(periodo, hoy), hoy)
