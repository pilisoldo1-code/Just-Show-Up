"""Controles para que las pruebas de Videos On Demand nunca toquen producción.

Producción se reconoce por huellas (sha256) para no escribir sus identificadores en el repo. Reconocer una URL no
alcanza: además, el programa local, el servidor (VIDEOS_ENTORNO) y la base (video_parametros.entorno) tienen que
coincidir en el mismo entorno, y una base recién migrada queda cerrada hasta configurarlo a propósito.
"""
import hashlib
import hmac
import os
from urllib.parse import unquote, urlparse

HUELLAS_PRODUCCION = {
    "b876de330340b21a5dc25a639a9a40b2d7266dbb7db26f7982a6ef0a22205fc8",
    "e050af1051bb53a852bb650f2bf80e8ffbc5d5a218ddbff4c0d613371b14c90c",
    "41dcb3ec2a68f5edce8a58c5149a882df4718a3e796a30f9528045287ff74da6",
}
PREFIJO_PRUEBA = "pruebas-"
HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}


class DestinoNoPermitido(Exception):
    pass


def es_produccion(identificador: str | None) -> bool:
    texto = (identificador or "").strip().lower()
    return hashlib.sha256(texto.encode()).hexdigest() in HUELLAS_PRODUCCION


def ref_de_url(url: str | None) -> str | None:
    """Identificador del proyecto en una URL de la API de Supabase."""
    host = (urlparse(url or "").hostname or "").lower()
    return host.split(".")[0] if host.endswith(".supabase.co") else None


def ref_de_conexion(dsn: str | None) -> str | None:
    """Identificador del proyecto en una conexión a Postgres: directa (db.REF.supabase.co) o por pooler (postgres.REF)."""
    partes = urlparse(dsn or "")
    host = (partes.hostname or "").lower()
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return host.split(".")[1]
    usuario = unquote(partes.username or "")
    return usuario.split(".", 1)[1].lower() if usuario.startswith("postgres.") else None


def url_es_produccion(url: str | None) -> bool:
    return es_produccion(ref_de_url(url)) or es_produccion(urlparse(url or "").hostname)


def es_local(url: str | None) -> bool:
    return (urlparse(url or "").hostname or "").lower() in HOSTS_LOCALES


def exigir_proyecto_prueba(url: str | None, proyecto: str | None) -> None:
    """Corta si el destino no es local ni exactamente el proyecto de prueba confirmado."""
    if es_local(url):
        return
    if url_es_produccion(url):
        raise DestinoNoPermitido("Ese destino es producción: no se hace nada.")
    ref = ref_de_url(url)
    if ref is None:
        raise DestinoNoPermitido("El destino no es local ni un proyecto de Supabase reconocible.")
    if not proyecto or proyecto.strip().lower() != ref:
        raise DestinoNoPermitido("Hay que confirmar con --proyecto el identificador exacto del proyecto de prueba.")


def exigir_conexion_prueba(dsn: str | None, proyecto: str | None) -> None:
    if es_local(dsn):
        return
    ref = ref_de_conexion(dsn)
    if ref is None:
        raise DestinoNoPermitido("No se reconoce el proyecto de esa conexión.")
    if es_produccion(ref):
        raise DestinoNoPermitido("Esa conexión es de producción: no se hace nada.")
    if not proyecto or proyecto.strip().lower() != ref:
        raise DestinoNoPermitido("Hay que confirmar con --proyecto el identificador exacto del proyecto de prueba.")


def servidor_admite(entorno: str) -> bool:
    """Si este servidor acepta al programa local. Un entorno de prueba nunca corre en Vercel ni contra producción."""
    propio = os.environ.get("VIDEOS_ENTORNO", "")
    if not propio or not hmac.compare_digest(entorno.encode(), propio.encode()):
        return False
    if propio.startswith(PREFIJO_PRUEBA):
        destinos = (os.environ.get("SUPABASE_URL"), os.environ.get("VIDEOS_SUPABASE_URL"))
        return not os.environ.get("VERCEL") and not any(url_es_produccion(u) for u in destinos)
    return True
