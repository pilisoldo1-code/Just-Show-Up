"""Datos personales (cédula, fecha de nacimiento, vencimiento del carné): con multiempresa solo los ve la
persona misma y la administradora de su empresa, a través de la función datos_personales de la base."""
import logging

from supabase import Client

PRIVADOS = ("cedula", "fecha_nacimiento", "fecha_vencimiento_carne_salud")


def datos_personales(sb: Client, ids) -> dict:
    """id -> {cedula, fecha_nacimiento, fecha_vencimiento_carne_salud} de las personas que la sesión puede ver."""
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    try:
        filas = sb.rpc("datos_personales", {"p_ids": ids}).execute().data or []
    except Exception:
        # Antes de aplicar la parte 4 la función no existe y las políticas de profiles ya filtran por empresa.
        logging.info("datos_personales no disponible: se leen de profiles")
        filas = sb.table("profiles").select("id, " + ", ".join(PRIVADOS)).in_("id", ids).execute().data
    return {f["id"]: {k: f.get(k) for k in PRIVADOS} for f in filas}


def nombres_profesoras(sb: Client) -> dict:
    """id -> nombre de las profesoras de la empresa activa (lo pueden ver también las alumnas)."""
    try:
        return {f["id"]: f["nombre"] for f in sb.rpc("profesoras_empresa", {}).execute().data or []}
    except Exception:
        logging.info("profesoras_empresa no disponible todavía")  # antes de aplicar la parte 4
        return {}


def completar(sb: Client, filas: list[dict]) -> list[dict]:
    """Agrega los datos personales a cada fila (las que no se pueden ver quedan vacías)."""
    privados = datos_personales(sb, [f["id"] for f in filas])
    for f in filas:
        f.update(privados.get(f["id"], dict.fromkeys(PRIVADOS)))
    return filas
