from datetime import date

from supabase import Client

MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]
MEDIOS_PAGO = {"efectivo": "Efectivo", "transferencia": "Transferencia"}


def mes_anterior(mes: int, anio: int) -> tuple[int, int]:
    return (12, anio - 1) if mes == 1 else (mes - 1, anio)


def mes_siguiente(mes: int, anio: int) -> tuple[int, int]:
    return (1, anio + 1) if mes == 12 else (mes + 1, anio)


def resumenes(sb: Client, mes: int, anio: int) -> list[dict]:
    filas = (
        sb.table("resumen_mensual")
        .select("id, cantidad_clases, total, estado, profiles(nombre, apellido), pago(monto, medio_pago, fecha)")
        .eq("mes", mes).eq("anio", anio)
        .execute().data
    )
    filas.sort(key=lambda r: (r["profiles"]["apellido"].lower(), r["profiles"]["nombre"].lower()))
    salida = []
    for r in filas:
        pago = r["pago"][0] if r.get("pago") else None
        salida.append({
            "id": r["id"],
            "alumna": f"{r['profiles']['nombre']} {r['profiles']['apellido']}",
            "cantidad_clases": r["cantidad_clases"],
            "total": r["total"],
            "estado": r["estado"],
            "pago": {
                "monto": pago["monto"],
                "medio": MEDIOS_PAGO.get(pago["medio_pago"], pago["medio_pago"]),
                "fecha": pago["fecha"],
            } if pago else None,
        })
    return salida


def mis_facturas(sb: Client, alumna_id: str) -> list[dict]:
    filas = (
        sb.table("resumen_mensual")
        .select("id, mes, anio, cantidad_clases, total, estado, pago(monto, medio_pago, fecha)")
        .eq("id_alumna", alumna_id)
        .order("anio", desc=True).order("mes", desc=True)
        .execute().data
    )
    salida = []
    for r in filas:
        pago = r["pago"][0] if r.get("pago") else None
        salida.append({
            "id": r["id"],
            "mes_txt": f"{MESES[r['mes']].capitalize()} {r['anio']}",
            "cantidad_clases": r["cantidad_clases"],
            "total": r["total"],
            "estado": r["estado"],
            "pago": {
                "monto": pago["monto"],
                "medio": MEDIOS_PAGO.get(pago["medio_pago"], pago["medio_pago"]),
                "fecha": pago["fecha"],
            } if pago else None,
        })
    return salida


def generar(sb: Client, mes: int, anio: int) -> tuple[bool, str]:
    resultado = sb.rpc("generar_resumenes", {"p_mes": mes, "p_anio": anio}).execute().data
    if resultado["resultado"] != "ok":
        return False, {
            "no_autorizada": "Solo la profesora puede generar resúmenes.",
            "datos_invalidos": "Mes o año inválido.",
        }.get(resultado["resultado"], "No se pudieron generar los resúmenes.")
    if resultado["generados"] == 0:
        return True, "No hay clases nuevas para resumir este mes."
    n = resultado["generados"]
    return True, f"Se {'generó' if n == 1 else 'generaron'} {n} {'resumen' if n == 1 else 'resúmenes'}."


def registrar_pago(sb: Client, id_resumen: int, monto: float, medio: str, fecha: date) -> tuple[bool, str]:
    resultado = sb.rpc(
        "registrar_pago",
        {"p_id_resumen": id_resumen, "p_monto": monto, "p_medio": medio, "p_fecha": fecha.isoformat()},
    ).execute().data
    return {
        "ok": (True, "Pago registrado."),
        "ya_pagado": (False, "Ese resumen ya estaba pagado."),
        "no_existe": (False, "No encontramos ese resumen."),
        "datos_invalidos": (False, "Revisá el monto y el medio de pago."),
        "no_autorizada": (False, "Solo la profesora puede registrar pagos."),
    }.get(resultado["resultado"], (False, "No se pudo registrar el pago."))
