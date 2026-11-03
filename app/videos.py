import hashlib
import hmac
import logging
import os
from datetime import date

from supabase import Client, create_client

from app import auth, clases

BUCKET = "videos-clases"
TTL_URL_MAXIMO = 90 * 60  # alcanza para 40 minutos con pausas; igual al margen de video_reproducir
LIMITE_LIMPIEZA = 20
MSG_NO_DISPONIBLE = "Ese video ya no está disponible."
CODIGOS_REPRODUCCION = {"no_disponible": 404, "limite_usuaria": 429, "sin_presupuesto": 429}


def almacen():
    """Bucket de videos con clave de administrador. Las URLs las firma solo el servidor: el bucket no tiene
    políticas para usuarias. Con VIDEOS_SUPABASE_* los archivos viven en un proyecto aparte."""
    url, clave = os.environ.get("VIDEOS_SUPABASE_URL"), os.environ.get("VIDEOS_SUPABASE_SERVICE_KEY")
    cliente = create_client(url, clave) if url and clave else auth.cliente_admin()
    return cliente.storage.from_(BUCKET)


def ruta(id_empresa: int, id_video: int, nombre: str) -> str:
    return f"{id_empresa}/{id_video}/{nombre}"


# ---------- textos ----------

def texto_restante(segundos: int) -> str:
    horas, resto = divmod(max(segundos, 0), 3600)
    minutos = resto // 60
    if horas:
        return f"Disponible {horas} h {minutos} min más"
    return f"Disponible {max(minutos, 1)} min más"


def texto_duracion(segundos: int) -> str:
    return f"{round(segundos / 60)} minutos"


def _tarjeta(v: dict) -> dict:
    f = date.fromisoformat(v["fecha"])
    return {
        "id": v["id"],
        "clase": v["clase"],
        "fecha_txt": f"{clases.nombre_dia(f)} {f.day}/{f.month}",
        "hora": v["hora_inicio"][:5],
        "profesora": v["profesora"],
        "duracion_txt": texto_duracion(v["duracion_seg"]),
        "restante_txt": texto_restante(v["segundos_restantes"]),
        "segundos_restantes": v["segundos_restantes"],
    }


# ---------- quienes miran ----------

def disponibles(sb: Client) -> list[dict]:
    return [_tarjeta(v) for v in sb.rpc("videos_disponibles", {}).execute().data or []]


def detalle(sb: Client, id_video: int) -> dict | None:
    filas = sb.rpc("videos_disponibles", {"p_id_video": id_video}).execute().data or []
    return _tarjeta(filas[0]) if filas else None


def firmar_lista(lista: str, urls: dict[str, str]) -> str:
    """Cambia el nombre de cada parte por su URL firmada; el resto de la lista queda igual."""
    return "\n".join(urls.get(linea.strip(), linea) for linea in lista.splitlines()) + "\n"


def lista_para_reproducir(sb: Client, id_video: int) -> tuple[str, str]:
    r = sb.rpc("video_reproducir", {"p_id_video": id_video}).execute().data or {}
    if r.get("resultado") != "ok":
        return r.get("resultado", "no_disponible"), ""
    ttl = min(TTL_URL_MAXIMO, int(r["segundos_restantes"]))  # ninguna URL dura más que el video
    if ttl < 1:
        return "no_disponible", ""
    nombres = [p["nombre"] for p in r["partes"]]
    rutas = {ruta(r["id_empresa"], id_video, n): n for n in nombres}
    firmadas = almacen().create_signed_urls(list(rutas), ttl)
    urls = {rutas[f["path"]]: f["signedURL"] for f in firmadas if f.get("signedURL")}
    if set(urls) != set(nombres):
        logging.error("Faltan partes firmadas del video %s", id_video)
        return "no_disponible", ""
    return "ok", firmar_lista(r["lista_hls"], urls)


# ---------- programa local ----------

def huella_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def identificar_agente(cabecera: str) -> str | None:
    token = cabecera.removeprefix("Bearer ").strip()
    if not token:
        return None
    return auth.cliente_admin().rpc("video_agente_identificar", {"p_token_hash": huella_token(token)}).execute().data


def video_de_agente(id_video: int, id_profesora: str) -> dict | None:
    return auth.cliente_admin().rpc("video_de_agente", {"p_id_video": id_video, "p_id_profesora": id_profesora}).execute().data


def registrar(id_profesora: str, datos: dict) -> dict:
    return auth.cliente_admin().rpc("video_registrar", {
        "p_id_profesora": id_profesora, "p_inicio": datos["inicio"], "p_huella": datos["huella"],
        "p_duracion_seg": datos["duracion_seg"], "p_tamano_bytes": datos["tamano_bytes"],
        "p_partes": datos["partes"], "p_lista_hls": datos["lista_hls"],
    }).execute().data


def _subidas(video: dict, bucket) -> dict[str, int]:
    carpeta = f"{video['id_empresa']}/{video['id']}"
    return {f["name"]: (f.get("metadata") or {}).get("size", 0) for f in bucket.list(carpeta)}


def urls_subida(video: dict) -> dict:
    """URLs para las partes que faltan: un reintento no vuelve a subir lo que ya llegó entero."""
    if video["estado"] != "subiendo":
        return {"resultado": "no_subiendo", "urls": {}}
    bucket = almacen()
    subidas = _subidas(video, bucket)
    urls = {}
    for parte in video["partes"]:
        nombre = parte["nombre"]
        if subidas.get(nombre) == parte["bytes"]:
            continue
        camino = ruta(video["id_empresa"], video["id"], nombre)
        if nombre in subidas:
            bucket.remove([camino])  # quedó a medias: la subida firmada no sobrescribe
        urls[nombre] = bucket.create_signed_upload_url(camino)["signed_url"]
    return {"resultado": "ok", "urls": urls}


def publicar(video: dict, id_profesora: str) -> dict:
    if video["estado"] == "subiendo":
        subidas = _subidas(video, almacen())
        if any(subidas.get(p["nombre"]) != p["bytes"] for p in video["partes"]):
            return {"resultado": "partes_incompletas"}
    return auth.cliente_admin().rpc("video_publicar", {"p_id_video": video["id"], "p_id_profesora": id_profesora}).execute().data


def registrar_error(id_video: int, id_profesora: str, mensaje: str) -> None:
    auth.cliente_admin().rpc("video_error", {"p_id_video": id_video, "p_id_profesora": id_profesora,
                                             "p_mensaje": mensaje}).execute()


# ---------- limpieza ----------

def secreto_valido(cabecera: str, secreto: str) -> bool:
    return hmac.compare_digest(cabecera, f"Bearer {secreto}")


def limpiar() -> int:
    """Borra los archivos vencidos y conserva el registro. Si un borrado falla, el video queda para la próxima."""
    admin = auth.cliente_admin()
    pendientes = admin.rpc("videos_a_limpiar", {"p_limite": LIMITE_LIMPIEZA}).execute().data or []
    bucket = almacen() if pendientes else None
    eliminados = 0
    for v in pendientes:
        try:
            bucket.remove([ruta(v["id_empresa"], v["id"], p["nombre"]) for p in v["partes"]])
            admin.rpc("video_marcar_eliminado", {"p_id_video": v["id"]}).execute()
            eliminados += 1
        except Exception:
            logging.exception("No se pudieron borrar los archivos del video %s", v["id"])
    return eliminados


def limpiar_sin_fallar() -> None:
    try:
        limpiar()
    except Exception:
        logging.exception("Falló la limpieza de videos")
