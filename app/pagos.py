"""Resumen mensual y pagos (módulo 6, sin Mercado Pago).

Las escrituras van por funciones de la base (generar_resumenes, registrar_pago,
anular_pago), que validan el rol, que el mes esté cerrado y el monto exacto.
El mes en curso se calcula acá en vivo como provisorio, sin guardar nada.
"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from supabase import Client

from app.clases import TZ, nombre_dia

MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]
MEDIOS_PAGO = {"efectivo": "Efectivo", "transferencia": "Transferencia"}
MOTIVO_MIN, MOTIVO_MAX = 3, 300

_PAGO_CAMPOS = "id, monto, medio_pago, fecha, estado, creado_en, anulado_en, motivo_anulacion"
_PAGO_PERSONAS = (
    "registrado:profiles!pago_registrado_por_fkey(nombre, apellido), "
    "anulador:profiles!pago_anulado_por_fkey(nombre, apellido)"
)
_DETALLE = "resumen_mensual_detalle(fecha, hora_inicio, tipo_nombre, precio)"


def mes_anterior(mes: int, anio: int) -> tuple[int, int]:
    return (12, anio - 1) if mes == 1 else (mes - 1, anio)


def mes_siguiente(mes: int, anio: int) -> tuple[int, int]:
    return (1, anio + 1) if mes == 12 else (mes + 1, anio)


def mes_txt(mes: int, anio: int) -> str:
    return f"{MESES[mes].capitalize()} {anio}"


def mes_cerrado(mes: int, anio: int, hoy: date) -> bool:
    """Igual que _mes_cerrado de la base: el mes en curso y los futuros están abiertos."""
    return (anio, mes) < (hoy.year, hoy.month)


def parsear_monto(texto: str) -> Decimal | None:
    try:
        monto = Decimal(str(texto).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return monto if monto.is_finite() and monto > 0 else None


def _fecha_txt(iso: str) -> str:
    f = date.fromisoformat(iso[:10])
    return f"{f.day:02d}/{f.month:02d}/{f.year}"


def _persona(p: dict | None) -> str:
    return f"{p['nombre']} {p['apellido']}" if p else ""


def _detalle(filas: list[dict]) -> list[dict]:
    salida = []
    for d in sorted(filas or [], key=lambda d: (d["fecha"], d["hora_inicio"])):
        f = date.fromisoformat(d["fecha"])
        salida.append({
            "fecha_txt": f"{nombre_dia(f)} {f.day}/{f.month}",
            "hora": d["hora_inicio"][:5],
            "tipo": d.get("tipo_nombre") or "Clase",
            "precio": float(d["precio"]),
            "dictada": d.get("dictada", True),
        })
    return salida


def _pagos(filas: list[dict]) -> tuple[dict | None, list[dict]]:
    """Separa el pago vigente (como mucho uno) de los anulados, del más nuevo al más viejo."""
    vigente, anulados = None, []
    for p in sorted(filas or [], key=lambda p: p["id"], reverse=True):
        dato = {
            "id": p["id"],
            "monto": float(p["monto"]),
            "medio": MEDIOS_PAGO.get(p["medio_pago"], p["medio_pago"]),
            "fecha": _fecha_txt(p["fecha"]),
            "registrado_en": _fecha_txt(p["creado_en"]),
            "registrado_por": _persona(p.get("registrado")),
        }
        if p["estado"] == "vigente":
            vigente = dato
        else:
            dato.update(
                anulado_en=_fecha_txt(p["anulado_en"]),
                anulado_por=_persona(p.get("anulador")),
                motivo=p.get("motivo_anulacion") or "",
            )
            anulados.append(dato)
    return vigente, anulados


def resumenes(sb: Client, mes: int, anio: int) -> list[dict]:
    filas = (
        sb.table("resumen_mensual")
        .select(f"id, cantidad_clases, total, estado, profiles(nombre, apellido), {_DETALLE}, "
                f"pago({_PAGO_CAMPOS}, {_PAGO_PERSONAS})")
        .eq("mes", mes).eq("anio", anio)
        .execute().data
    )
    filas.sort(key=lambda r: (r["profiles"]["apellido"].lower(), r["profiles"]["nombre"].lower()))
    salida = []
    for r in filas:
        vigente, anulados = _pagos(r.get("pago"))
        salida.append({
            "id": r["id"],
            "alumna": _persona(r["profiles"]),
            "cantidad_clases": r["cantidad_clases"],
            "total": float(r["total"]),
            "total_exacto": str(r["total"]),
            "estado": r["estado"],
            "detalle": _detalle(r.get("resumen_mensual_detalle")),
            "pago": vigente,
            "anulados": anulados,
        })
    return salida


def mis_facturas(sb: Client, alumna_id: str) -> list[dict]:
    filas = (
        sb.table("resumen_mensual")
        .select(f"id, mes, anio, cantidad_clases, total, estado, {_DETALLE}, pago({_PAGO_CAMPOS})")
        .eq("id_alumna", alumna_id)
        .order("anio", desc=True).order("mes", desc=True)
        .execute().data
    )
    salida = []
    for r in filas:
        vigente, _ = _pagos(r.get("pago"))
        salida.append({
            "id": r["id"],
            "mes_txt": mes_txt(r["mes"], r["anio"]),
            "cantidad_clases": r["cantidad_clases"],
            "total": float(r["total"]),
            "estado": r["estado"],
            "detalle": _detalle(r.get("resumen_mensual_detalle")),
            "pago": vigente,
        })
    return salida


# ---------- mes en curso (provisorio, solo lectura) ----------

def _reservas_del_mes(sb: Client, mes: int, anio: int, alumna_id: str | None) -> list[dict]:
    inicio = date(anio, mes, 1)
    mes_fin, anio_fin = mes_siguiente(mes, anio)
    fin = date(anio_fin, mes_fin, 1)
    consulta = (
        sb.table("reserva")
        .select("id, id_alumna, profiles(nombre, apellido), "
                "clase!inner(fecha, hora_inicio, precio, estado, tipo_clase(nombre))")
        .eq("estado", "confirmada").eq("clase.estado", "programada")
        .gte("clase.fecha", inicio.isoformat()).lt("clase.fecha", fin.isoformat())
    )
    if alumna_id:
        consulta = consulta.eq("id_alumna", alumna_id)
    return consulta.execute().data


def armar_provisorio(filas: list[dict], ahora: datetime) -> list[dict]:
    """Agrupa por alumna las reservas confirmadas del mes: las ya dictadas (lo que se
    cobraría hoy, misma regla que _cobrables en la base) y las que faltan."""
    por_alumna: dict[str, dict] = {}
    for r in filas:
        c = r["clase"]
        if r.get("estado", "confirmada") != "confirmada" or c["estado"] != "programada":
            continue
        inicio = datetime.combine(date.fromisoformat(c["fecha"]), time.fromisoformat(c["hora_inicio"]), tzinfo=TZ)
        a = por_alumna.setdefault(r["id_alumna"], {
            "id_alumna": r["id_alumna"], "alumna": _persona(r.get("profiles")),
            "dictadas": 0, "total_dictadas": 0.0, "proximas": 0, "total_proximas": 0.0, "filas": [],
        })
        clave = "dictadas" if inicio <= ahora else "proximas"
        a[clave] += 1
        a["total_" + clave] += float(c["precio"])
        a["filas"].append({"fecha": c["fecha"], "hora_inicio": c["hora_inicio"], "precio": c["precio"],
                           "tipo_nombre": (c.get("tipo_clase") or {}).get("nombre"),
                           "dictada": inicio <= ahora})
    salida = []
    for a in por_alumna.values():
        a["detalle"] = _detalle(a.pop("filas"))
        salida.append(a)
    salida.sort(key=lambda a: a["alumna"].lower())
    return salida


def provisorio(sb: Client, mes: int, anio: int, ahora: datetime, alumna_id: str | None = None) -> list[dict]:
    return armar_provisorio(_reservas_del_mes(sb, mes, anio, alumna_id), ahora)


# ---------- escrituras ----------

def generar(sb: Client, mes: int, anio: int) -> tuple[bool, str]:
    resultado = sb.rpc("generar_resumenes", {"p_mes": mes, "p_anio": anio}).execute().data
    if resultado["resultado"] != "ok":
        return False, {
            "no_autorizada": "Solo la profesora puede generar resúmenes.",
            "datos_invalidos": "Mes o año inválido.",
            "mes_abierto": "El mes todavía no terminó: los resúmenes se generan cuando cierra el mes.",
        }.get(resultado["resultado"], "No se pudieron generar los resúmenes.")
    n = resultado["generados"]
    if n == 0:
        return True, "No hay clases para resumir en este mes (los resúmenes pagados no se modifican)."
    return True, f"Se {'generó' if n == 1 else 'generaron'} {n} {'resumen' if n == 1 else 'resúmenes'}."


def registrar_pago(sb: Client, id_resumen: int, monto: Decimal, medio: str, fecha: date) -> tuple[bool, str]:
    resultado = sb.rpc(
        "registrar_pago",
        {"p_id_resumen": id_resumen, "p_monto": str(monto), "p_medio": medio, "p_fecha": fecha.isoformat()},
    ).execute().data
    return {
        "ok": (True, "Pago registrado."),
        "ya_pagado": (False, "Ese resumen ya estaba pagado: no se registró otro pago."),
        "no_existe": (False, "No encontramos ese resumen."),
        "mes_abierto": (False, "El mes todavía no terminó: no se puede cobrar."),
        "monto_distinto": (False, "El monto tiene que ser exactamente el total del resumen. "
                                  "Si recién se regeneró, recargá la página."),
        "fecha_futura": (False, "La fecha del pago no puede ser posterior a hoy."),
        "datos_invalidos": (False, "Revisá el medio de pago y la fecha."),
        "no_autorizada": (False, "Solo la profesora puede registrar pagos."),
    }.get(resultado["resultado"], (False, "No se pudo registrar el pago."))


def anular_pago(sb: Client, id_pago: int, motivo: str) -> tuple[bool, str]:
    resultado = sb.rpc("anular_pago", {"p_id_pago": id_pago, "p_motivo": motivo}).execute().data
    return {
        "ok": (True, "Pago anulado. El resumen volvió a pendiente y el pago queda en el historial."),
        "ya_anulado": (False, "Ese pago ya estaba anulado."),
        "no_existe": (False, "No encontramos ese pago."),
        "motivo_invalido": (False, f"Escribí el motivo de la anulación ({MOTIVO_MIN} a {MOTIVO_MAX} caracteres)."),
        "no_autorizada": (False, "Solo la profesora puede anular pagos."),
    }.get(resultado["resultado"], (False, "No se pudo anular el pago."))
