"""Facturación mensual y pagos (módulo 6). Simulación educativa: sin requisitos fiscales
ni pasarela de pagos.

Las escrituras van por funciones de la base (sincronizar_borradores, ajustar_linea,
descuento_general, confirmar_factura, confirmar_facturas_mes, registrar_pago, anular_pago,
anular_factura), que validan el rol, las fechas, la versión y los importes. Las lecturas
usan consultas simples (sin "embeds") con la sesión de cada usuaria: RLS decide qué ve.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from supabase import Client

from app.clases import TZ, nombre_dia

MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]
MEDIOS_PAGO = {"efectivo": "Efectivo", "transferencia": "Transferencia"}
ESTADOS_FACTURA = {"borrador": "Borrador", "confirmada": "Confirmada", "anulada": "Anulada"}
ESTADOS_PAGO = {"pendiente": "Pendiente de pago", "pagado": "Pagada", "sin_cargo": "Sin cargo"}
MOTIVO_MIN, MOTIVO_MAX = 3, 300


# ---------- fechas (iguales a las funciones de la base) ----------

def mes_anterior(mes: int, anio: int) -> tuple[int, int]:
    return (12, anio - 1) if mes == 1 else (mes - 1, anio)


def mes_siguiente(mes: int, anio: int) -> tuple[int, int]:
    return (1, anio + 1) if mes == 12 else (mes + 1, anio)


def mes_txt(mes: int, anio: int) -> str:
    return f"{MESES[mes].capitalize()} {anio}"


def primer_dia_siguiente(mes: int, anio: int) -> date:
    m, a = mes_siguiente(mes, anio)
    return date(a, m, 1)


def ultimo_dia(mes: int, anio: int) -> date:
    return primer_dia_siguiente(mes, anio) - timedelta(days=1)


def en_revision(mes: int, anio: int, hoy: date) -> bool:
    """Desde el último día del mes se preparan borradores (_mes_en_revision)."""
    return hoy >= ultimo_dia(mes, anio)


def inicio_publicacion(mes: int, anio: int) -> datetime:
    return datetime.combine(primer_dia_siguiente(mes, anio), time(0, 0), tzinfo=TZ)


# ---------- formato ----------

def parsear_importe(texto: str) -> Decimal | None:
    """Número positivo o cero con como mucho 2 decimales (acepta coma)."""
    try:
        valor = Decimal(str(texto).strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not valor.is_finite() or valor < 0 or valor != valor.quantize(Decimal("0.01")):
        return None
    return valor


def plata(valor) -> Decimal:
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def _fecha_txt(iso: str | None) -> str:
    if not iso:
        return ""
    f = date.fromisoformat(str(iso)[:10])
    return f"{f.day:02d}/{f.month:02d}/{f.year}"


def _momento_txt(iso: str | None) -> str:
    if not iso:
        return ""
    m = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)
    return f"{m.day:02d}/{m.month:02d}/{m.year} {m.hour:02d}:{m.minute:02d}"


def _dia_txt(iso: str) -> str:
    f = date.fromisoformat(str(iso)[:10])
    return f"{nombre_dia(f)} {f.day}/{f.month}"


def _nombres(sb: Client, ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    filas = sb.table("profiles").select("id, nombre, apellido").in_("id", ids).execute().data
    return {p["id"]: f"{p['nombre']} {p['apellido']}" for p in filas}


def _codigo(sb: Client, funcion: str, params: dict) -> dict:
    return sb.rpc(funcion, params).execute().data


# ---------- mes en curso (provisorio, solo lectura) ----------

def _reservas_del_mes(sb: Client, mes: int, anio: int, alumna_id: str | None) -> list[dict]:
    inicio, fin = date(anio, mes, 1), primer_dia_siguiente(mes, anio)
    clases = {c["id"]: c for c in (
        sb.table("clase").select("id, fecha, hora_inicio, precio, estado, id_tipo_clase")
        .gte("fecha", inicio.isoformat()).lt("fecha", fin.isoformat()).execute().data)}
    if not clases:
        return []
    consulta = (sb.table("reserva").select("id, id_alumna, id_clase, estado")
                .eq("estado", "confirmada").in_("id_clase", list(clases)))
    if alumna_id:
        consulta = consulta.eq("id_alumna", alumna_id)
    reservas = consulta.execute().data
    tipos = {t["id"]: t["nombre"] for t in sb.table("tipo_clase").select("id, nombre").execute().data}
    nombres = _nombres(sb, (r["id_alumna"] for r in reservas))
    filas = []
    for r in reservas:
        c = clases[r["id_clase"]]
        filas.append({
            "id_alumna": r["id_alumna"], "estado": r["estado"], "alumna": nombres.get(r["id_alumna"], ""),
            "clase": {**c, "tipo_nombre": tipos.get(c.get("id_tipo_clase"))},
        })
    return filas


def armar_provisorio(filas: list[dict], ahora: datetime) -> list[dict]:
    """Agrupa por alumna las reservas confirmadas del mes: las ya dictadas (lo que se
    cobraría hoy, misma regla que _cobrables en la base) y las que faltan."""
    por_alumna: dict[str, dict] = {}
    for r in filas:
        c = r["clase"]
        if r.get("estado", "confirmada") != "confirmada" or c["estado"] != "programada":
            continue
        inicio = datetime.combine(date.fromisoformat(c["fecha"]), time.fromisoformat(c["hora_inicio"]), tzinfo=TZ)
        dictada = inicio <= ahora
        a = por_alumna.setdefault(r["id_alumna"], {
            "id_alumna": r["id_alumna"], "alumna": r.get("alumna", ""),
            "dictadas": 0, "total_dictadas": Decimal("0"), "proximas": 0, "total_proximas": Decimal("0"), "detalle": [],
        })
        clave = "dictadas" if dictada else "proximas"
        a[clave] += 1
        a["total_" + clave] += plata(c["precio"])
        a["detalle"].append({
            "orden": (c["fecha"], c["hora_inicio"]), "fecha_txt": _dia_txt(c["fecha"]), "hora": c["hora_inicio"][:5],
            "tipo": c.get("tipo_nombre") or "Clase", "precio": plata(c["precio"]), "dictada": dictada,
        })
    for a in por_alumna.values():
        a["detalle"].sort(key=lambda d: d["orden"])
    return sorted(por_alumna.values(), key=lambda a: a["alumna"].lower())


def provisorio(sb: Client, mes: int, anio: int, ahora: datetime, alumna_id: str | None = None) -> list[dict]:
    return armar_provisorio(_reservas_del_mes(sb, mes, anio, alumna_id), ahora)


# ---------- lectura de facturas ----------

_CAMPOS_FACTURA = ("id, id_alumna, mes, anio, estado_factura, estado_pago, cantidad_clases, subtotal_original, "
                   "total_lineas, descuento_tipo, descuento_valor, descuento_monto, total, version, numero, "
                   "confirmada_en, anulada_en, reemplaza_a")


def _factura(f: dict, nombres: dict) -> dict:
    return {
        **f,
        "alumna": nombres.get(f["id_alumna"], ""),
        "mes_txt": mes_txt(f["mes"], f["anio"]),
        "estado_factura_txt": ESTADOS_FACTURA[f["estado_factura"]],
        "estado_pago_txt": ESTADOS_PAGO[f["estado_pago"]],
        "subtotal_original": plata(f["subtotal_original"]),
        "total_lineas": plata(f["total_lineas"]),
        "descuento_monto": plata(f["descuento_monto"]),
        "total": plata(f["total"]),
        "descuento_txt": _descuento_txt(f),
        "confirmada_txt": _fecha_txt(f.get("confirmada_en")),
        "anulada_txt": _fecha_txt(f.get("anulada_en")),
    }


def _descuento_txt(f: dict) -> str:
    if not f.get("descuento_tipo"):
        return ""
    if f["descuento_tipo"] == "porcentaje":
        return f"{plata(f['descuento_valor']).normalize():f}%"
    return f"${plata(f['descuento_valor'])}"


def _linea(l: dict) -> dict:
    precio, importe = plata(l["precio_original"]), plata(l["importe_final"])
    return {
        "id": l["id"], "orden": (l["fecha"], l["hora_inicio"]),
        "fecha_txt": _dia_txt(l["fecha"]), "fecha_corta": _fecha_txt(l["fecha"]), "hora": l["hora_inicio"][:5],
        "tipo": l.get("tipo_nombre") or "Clase", "precio_original": precio,
        "ajuste_tipo": l["ajuste_tipo"], "importe_final": importe, "ajuste": importe - precio,
        "ajuste_txt": {"ninguno": "", "bonificada": "Bonificada", "importe": "Descuento"}[l["ajuste_tipo"]],
    }


def facturas_del_mes(sb: Client, mes: int, anio: int) -> list[dict]:
    filas = (sb.table("resumen_mensual").select(_CAMPOS_FACTURA)
             .eq("mes", mes).eq("anio", anio).execute().data)
    nombres = _nombres(sb, (f["id_alumna"] for f in filas))
    salida = [_factura(f, nombres) for f in filas]
    salida.sort(key=lambda f: (f["estado_factura"] == "anulada", f["alumna"].lower(), f["id"]))
    return salida


_ACCIONES = {
    "ajuste_linea": "Ajuste de clase", "ajuste_quitado": "Ajuste quitado", "linea_quitada": "Clase quitada",
    "descuento_general": "Descuento general", "descuento_quitado": "Descuento general quitado",
    "confirmada": "Factura confirmada", "anulada": "Factura anulada", "reemision": "Reemisión",
}


def _historial_txt(h: dict) -> str:
    d = h.get("detalle") or {}
    clase = f"{_dia_txt(d['fecha'])} {str(d.get('hora', ''))[:5]}" if d.get("fecha") else ""
    if h["accion"] in ("ajuste_linea", "ajuste_quitado"):
        return f"{clase}: ${plata(d['antes'])} → ${plata(d['despues'])}"
    if h["accion"] == "linea_quitada":
        return f"{clase} ya no se cobra (tenía un ajuste: ${plata(d['importe_final'])})"
    if h["accion"] in ("descuento_general", "descuento_quitado"):
        def txt(x):
            if not x or not x.get("tipo"):
                return "sin descuento"
            return f"{plata(x['valor']).normalize():f}%" if x["tipo"] == "porcentaje" else f"${plata(x['valor'])}"
        return f"{txt(d.get('antes'))} → {txt(d.get('despues'))}"
    if h["accion"] == "confirmada":
        return f"Total ${plata(d.get('total', 0))}"
    if h["accion"] in ("anulada", "reemision"):
        return d.get("texto", "")
    return ""


def factura(sb: Client, factura_id: int) -> dict | None:
    """Detalle completo para la profesora (incluye historial y motivos internos)."""
    filas = sb.table("resumen_mensual").select(_CAMPOS_FACTURA).eq("id", factura_id).execute().data
    if not filas:
        return None
    f = filas[0]
    lineas = (sb.table("resumen_mensual_detalle").select("*").eq("id_resumen_mensual", factura_id)
              .execute().data)
    pagos = sb.table("pago").select("*").eq("id_resumen_mensual", factura_id).execute().data
    anulaciones = {a["id_pago"]: a for a in (
        sb.table("pago_anulacion").select("*").in_("id_pago", [p["id"] for p in pagos]).execute().data
        if pagos else [])}
    historial = (sb.table("factura_historial").select("*").eq("id_resumen_mensual", factura_id)
                 .order("id", desc=True).execute().data)
    relacionadas = (sb.table("resumen_mensual").select("id, numero, estado_factura, reemplaza_a")
                    .eq("id_alumna", f["id_alumna"]).eq("mes", f["mes"]).eq("anio", f["anio"]).execute().data)
    nombres = _nombres(sb, [f["id_alumna"]] + [h["por"] for h in historial] + [p["registrado_por"] for p in pagos]
                       + [a["anulado_por"] for a in anulaciones.values()])

    salida = _factura(f, nombres)
    motivos = {}
    for h in sorted(historial, key=lambda h: h["id"]):
        if h["accion"] == "ajuste_linea":
            motivos[h["id_linea"]] = h["motivo"]
        elif h["accion"] == "ajuste_quitado":
            motivos.pop(h["id_linea"], None)
    salida["lineas"] = sorted(({**_linea(l), "motivo": motivos.get(l["id"], "")} for l in lineas), key=lambda l: l["orden"])
    salida["descuento_motivo"] = next((h["motivo"] for h in historial if h["accion"] == "descuento_general"), "") \
        if f["descuento_tipo"] else ""
    salida["pago"], salida["pagos_anulados"] = None, []
    for p in sorted(pagos, key=lambda p: p["id"], reverse=True):
        dato = {
            "id": p["id"], "monto": plata(p["monto"]), "medio": MEDIOS_PAGO.get(p["medio_pago"], p["medio_pago"]),
            "fecha": _fecha_txt(p["fecha"]), "registrado_en": _momento_txt(p["creado_en"]),
            "registrado_por": nombres.get(p["registrado_por"], ""),
        }
        if p["estado"] == "vigente":
            salida["pago"] = dato
        else:
            a = anulaciones.get(p["id"], {})
            salida["pagos_anulados"].append({**dato, "motivo": a.get("motivo", ""),
                                             "anulado_en": _momento_txt(a.get("anulado_en")),
                                             "anulado_por": nombres.get(a.get("anulado_por"), "")})
    salida["historial"] = [{
        "en": _momento_txt(h["en"]), "por": nombres.get(h["por"], ""), "accion": _ACCIONES.get(h["accion"], h["accion"]),
        "texto": _historial_txt(h), "motivo": h.get("motivo") or "",
    } for h in historial]
    nueva = next((r for r in relacionadas if r["reemplaza_a"] == f["id"]), None)
    vieja = next((r for r in relacionadas if r["id"] == f.get("reemplaza_a")), None)
    salida["reemplazada_por_id"], salida["reemplaza_id"] = (nueva or {}).get("id"), (vieja or {}).get("id")
    salida["reemplazada_por"] = nueva and (nueva["numero"] or "un borrador nuevo")
    salida["reemplaza"] = vieja and vieja["numero"]
    return salida


def previsualizar_mes(sb: Client, mes: int, anio: int) -> dict:
    r = _codigo(sb, "previsualizar_confirmacion_mes", {"p_mes": mes, "p_anio": anio})
    if r.get("resultado") == "ok":
        r["total"] = plata(r["total"])
    return r


# ---------- escrituras de la profesora ----------

_MENSAJES = {
    "no_autorizada": "Solo la profesora puede hacer esto.",
    "datos_invalidos": "Datos inválidos.",
    "no_existe": "No encontramos esa factura.",
    "fuera_de_revision": "Los borradores se preparan desde el último día del mes.",
    "no_es_borrador": "La factura ya está confirmada: no se puede modificar.",
    "version_cambio": "La factura cambió mientras la mirabas (por ejemplo, se actualizaron las clases). "
                      "Revisala de nuevo antes de seguir.",
    "motivo_invalido": f"Escribí el motivo ({MOTIVO_MIN} a {MOTIVO_MAX} caracteres).",
    "importe_invalido": "El importe tiene que ser menor que el precio original y no negativo.",
    "sin_ajuste": "Esa clase no tiene ajuste.",
    "descuento_invalido": "Revisá el descuento: el porcentaje va de 0 a 100 y el monto no puede superar el total de las clases.",
    "sin_descuento": "La factura no tiene descuento general.",
    "periodo_abierto": "Todavía hay clases del mes sin terminar: se puede confirmar cuando termine la última.",
    "sin_clases": "La factura no tiene clases para cobrar.",
    "desactualizada": "Las clases de la factura cambiaron. Volvé a abrir el mes para actualizarla y revisala.",
    "cambio": "Algo cambió desde la vista previa (una factura se ajustó o se actualizó). No se confirmó ninguna: revisá de nuevo.",
    "nada": "No hay borradores para confirmar.",
    "ya_pagado": "Esa factura ya estaba pagada: no se registró otro pago.",
    "no_confirmada": "Solo se pueden cobrar facturas confirmadas.",
    "sin_cargo": "La factura es sin cargo: no hay nada que cobrar.",
    "monto_distinto": "El monto tiene que ser exactamente el total de la factura. Recargá la página.",
    "fecha_futura": "La fecha del pago no puede ser posterior a hoy.",
    "ya_anulado": "Ese pago ya estaba anulado.",
    "tiene_pago": "La factura tiene un pago registrado: primero anulá el pago.",
    "no_confirmada_anular": "Solo se pueden anular facturas confirmadas.",
}


def mensaje(codigo: str) -> str:
    return _MENSAJES.get(codigo, "No se pudo completar la operación. Probá de nuevo.")


def _resultado(r: dict, ok_txt: str) -> tuple[bool, str]:
    if r.get("resultado") == "ok":
        return True, ok_txt
    return False, mensaje(r.get("resultado", ""))


def sincronizar(sb: Client, mes: int, anio: int) -> dict:
    return _codigo(sb, "sincronizar_borradores", {"p_mes": mes, "p_anio": anio})


def ajustar_linea(sb: Client, linea_id: int, tipo: str, importe: Decimal | None, motivo: str, version: int):
    r = _codigo(sb, "ajustar_linea", {"p_id_linea": linea_id, "p_tipo": tipo,
                                      "p_importe": None if importe is None else str(importe),
                                      "p_motivo": motivo, "p_version": version})
    return _resultado(r, "Ajuste guardado. El total se actualizó.")


def quitar_ajuste(sb: Client, linea_id: int, motivo: str, version: int):
    r = _codigo(sb, "quitar_ajuste_linea", {"p_id_linea": linea_id, "p_motivo": motivo, "p_version": version})
    return _resultado(r, "Ajuste quitado: la clase vuelve a su precio original.")


def descuento(sb: Client, factura_id: int, tipo: str | None, valor: Decimal | None, motivo: str, version: int):
    r = _codigo(sb, "descuento_general", {"p_id": factura_id, "p_tipo": tipo,
                                          "p_valor": None if valor is None else str(valor),
                                          "p_motivo": motivo, "p_version": version})
    return _resultado(r, "Descuento general quitado." if tipo is None else "Descuento general aplicado.")


def confirmar(sb: Client, factura_id: int, version: int):
    r = _codigo(sb, "confirmar_factura", {"p_id": factura_id, "p_version": version})
    return _resultado(r, f"Factura {r.get('numero', '')} confirmada.")


def confirmar_mes(sb: Client, mes: int, anio: int, firma: str):
    r = _codigo(sb, "confirmar_facturas_mes", {"p_mes": mes, "p_anio": anio, "p_firma": firma})
    if r.get("resultado") == "ok":
        n = r["confirmadas"]
        return True, f"{n} {'factura confirmada' if n == 1 else 'facturas confirmadas'} por ${plata(r['total'])}."
    return False, mensaje(r.get("resultado", ""))


def registrar_pago(sb: Client, factura_id: int, monto: Decimal, medio: str, fecha: date):
    r = _codigo(sb, "registrar_pago", {"p_id_resumen": factura_id, "p_monto": str(monto), "p_medio": medio,
                                       "p_fecha": fecha.isoformat()})
    return _resultado(r, "Pago registrado.")


def anular_factura(sb: Client, factura_id: int, motivo: str) -> tuple[bool, str, int | None]:
    r = _codigo(sb, "anular_factura", {"p_id": factura_id, "p_motivo": motivo})
    if r.get("resultado") == "ok":
        return True, ("Factura anulada. Se creó un borrador que la reemplaza, con las mismas clases y ajustes: "
                      "corregilo y confirmalo."), r["id_nueva"]
    return False, mensaje(r.get("resultado", "")), None


def anular_pago(sb: Client, pago_id: int, motivo: str):
    r = _codigo(sb, "anular_pago", {"p_id_pago": pago_id, "p_motivo": motivo})
    return _resultado(r, "Pago anulado. La factura volvió a pendiente de pago y el pago queda en el historial.")


# ---------- alumna ----------

def mis_facturas(sb: Client, alumna_id: str) -> list[dict]:
    """Facturas publicadas de la alumna. Qué ve lo decide la base (RLS): confirmadas desde el
    día 1 del mes siguiente y anuladas que ya se habían publicado; nunca borradores ni motivos."""
    filas = (sb.table("resumen_mensual").select(_CAMPOS_FACTURA).eq("id_alumna", alumna_id)
             .order("anio", desc=True).order("mes", desc=True).execute().data)
    if not filas:
        return []
    ids = [f["id"] for f in filas]
    lineas = sb.table("resumen_mensual_detalle").select("*").in_("id_resumen_mensual", ids).execute().data
    pagos = sb.table("pago").select("id, id_resumen_mensual, monto, medio_pago, fecha, estado") \
        .in_("id_resumen_mensual", ids).eq("estado", "vigente").execute().data
    por_id = {f["id"]: f for f in filas}
    salida = []
    for f in filas:
        dato = _factura(f, {})
        dato["lineas"] = sorted((_linea(l) for l in lineas if l["id_resumen_mensual"] == f["id"]),
                                key=lambda l: l["orden"])
        pago = next((p for p in pagos if p["id_resumen_mensual"] == f["id"]), None)
        dato["pago"] = {"monto": plata(pago["monto"]), "medio": MEDIOS_PAGO.get(pago["medio_pago"], ""),
                        "fecha": _fecha_txt(pago["fecha"])} if pago else None
        nueva = next((x for x in filas if x.get("reemplaza_a") == f["id"]), None)
        dato["reemplazada_por"] = nueva["numero"] if nueva else None
        dato["reemplaza"] = por_id.get(f.get("reemplaza_a"), {}).get("numero")
        salida.append(dato)
    return salida


def mi_factura(sb: Client, alumna_id: str, factura_id: int) -> dict | None:
    return next((f for f in mis_facturas(sb, alumna_id) if f["id"] == factura_id), None)
