"""Registros de demostración que se conservan en la base pero no se muestran en los listados de la administración.

El tipo «Gimnasia (demostración)» y su horario del lunes 10:00 sostienen la factura de demostración de Mercado Pago
(JSU-2026-09-0001): no se borran ni se modifican, solo se excluyen de Tipos de clase y Horarios fijos. Se identifican
por su id real en producción y, además, por su nombre y por estar inactivos, para no ocultar otro registro por error.
En otra base (por ejemplo el Supabase local) los ids son otros: se indican con DEMO_TIPOS_OCULTOS y
DEMO_HORARIOS_OCULTOS (ids separados por comas).
"""
import os

NOMBRE_TIPO = "Gimnasia (demostración)"


def _ids(variable: str, produccion: set[int]) -> set[int]:
    valor = os.environ.get(variable, "").strip()
    return {int(x) for x in valor.split(",") if x.strip()} if valor else produccion


TIPOS_OCULTOS = _ids("DEMO_TIPOS_OCULTOS", {81})
HORARIOS_OCULTOS = _ids("DEMO_HORARIOS_OCULTOS", {204})


def tipo_oculto(t: dict) -> bool:
    return t.get("id") in TIPOS_OCULTOS and t.get("nombre") == NOMBRE_TIPO and not t.get("activo")


def horario_oculto(h: dict) -> bool:
    tipo = h.get("tipo_clase") or {}
    return h.get("id") in HORARIOS_OCULTOS and tipo.get("nombre") == NOMBRE_TIPO and not h.get("activo")
