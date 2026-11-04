import hashlib
import hmac
import logging
import os
import re
from datetime import date

from supabase import Client, create_client

from app import auth, clases, entorno

BUCKET = "videos-clases"
TTL_URL_MAXIMO = 90 * 60  # alcanza para 40 minutos con pausas; después el reproductor pide una lista nueva
MARGEN_URL_SEG = 15  # la URL se firma un momento después de calcular el tiempo: nunca debe pasar el vencimiento
LIMITE_LIMPIEZA = 20
MSG_NO_DISPONIBLE = "Ese video ya no está disponible."
CODIGOS_REPRODUCCION = {"no_disponible": 404, "sin_iniciar": 409, "limite_usuaria": 429, "sin_presupuesto": 429}
MSG_PROGRAMAR = {
    "ok": "Listo, se guardó la configuración.",
    "duracion_no_valida": "Solo se pueden grabar horarios de la duración prevista para Videos On Demand.",
    "no_encontrado": "No encontramos ese horario.",
    "no_autorizada": "Solo la administradora puede cambiar esta configuración.",
}


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
    """Duración real del archivo: no redondea a 40 minutos."""
    minutos, resto = divmod(segundos, 60)
    return f"{minutos} min {resto} s" if resto else f"{minutos} min"


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


def iniciar(sb: Client, id_video: int) -> str:
    """Al tocar Play. Abrir la página no pasa por acá."""
    return (sb.rpc("video_iniciar", {"p_id_video": id_video}).execute().data or {}).get("resultado", "no_disponible")


def progreso(sb: Client, id_video: int, segundos: int) -> str:
    r = sb.rpc("video_progreso", {"p_id_video": id_video, "p_segundos": segundos}).execute().data or {}
    return r.get("resultado", "no_disponible")


def lista_para_reproducir(sb: Client, id_video: int) -> tuple[str, str]:
    r = sb.rpc("video_para_firmar", {"p_id_video": id_video}).execute().data or {}
    if r.get("resultado") != "ok":
        return r.get("resultado", "no_disponible"), ""
    ttl = min(TTL_URL_MAXIMO, int(r["segundos_restantes"]) - MARGEN_URL_SEG)
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


# ---------- configuración (administradora) ----------

ESTADOS_GRABACION = {
    "subiendo": ("Subiendo", "badge-espera"),
    "publicado": ("Publicado", "badge-on"),
    "error": ("Con error, se reintenta", "badge-ausente"),
}


def limpiar_mensaje(mensaje: str | None) -> str:
    """Saca lo que no debe guardarse ni mostrarse: URLs firmadas, rutas de la computadora, tokens y claves."""
    texto = re.sub(r"https?://\S+", "[enlace]", mensaje or "")
    texto = re.sub(r"(?:/Users|/home|/private|/tmp|[A-Za-z]:\\)[^\s,;]*", "[ruta]", texto)
    texto = re.sub(r"\b(?:Bearer\s+\S+|sb_[a-z]+_\S+|eyJ[\w-]+\S*)", "[dato]", texto)
    texto = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[dato]", texto)
    return " ".join(texto.split())[:300]


def _estado_grabacion(g: dict) -> tuple[str, str]:
    if g["estado"] == "eliminado":
        return ("Abandonada, sin publicar", "badge-off") if g["nunca_publicado"] else ("Vencido, archivos borrados", "badge-off")
    if g["estado"] == "publicado" and g["vencido"]:
        return "Vencido, falta borrar archivos", "badge-espera"
    return ESTADOS_GRABACION.get(g["estado"], (g["estado"], "badge-off"))


def ultimas_grabaciones(sb: Client) -> list[dict]:
    salida = []
    for g in sb.rpc("videos_ultimas_grabaciones", {}).execute().data or []:
        f = date.fromisoformat(g["fecha"])
        estado_txt, badge = _estado_grabacion(g)
        salida.append({
            "clase": g["clase"],
            "fecha_txt": f"{clases.nombre_dia(f)} {f.day}/{f.month}",
            "hora": g["hora_inicio"][:5],
            "estado_txt": estado_txt,
            "badge": badge,
            "intentos": g["intentos"],
            "limpieza_intentos": g["limpieza_intentos"],
            "error": limpiar_mensaje(g["ultimo_error"]),
            "error_limpieza": limpiar_mensaje(g["limpieza_error"]),
        })
    return salida


def configuracion(sb: Client) -> list[dict]:
    filas = sb.rpc("videos_configuracion", {}).execute().data or []
    return sorted(filas, key=lambda h: (clases.DIAS_ORDEN.index(h["dia_semana"]), h["hora_inicio"]))


def programar(sb: Client, id_horario: int, activa: bool) -> str:
    r = sb.rpc("video_programar", {"p_id_horario": id_horario, "p_activa": activa}).execute().data or {}
    return r.get("resultado", "no_encontrado")


# ---------- programa local ----------

def huella_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def identificar_agente(cabecera: str, entorno_pedido: str) -> str | None:
    """La profesora dueña del token, si el programa, este servidor y la base están en el mismo entorno."""
    token = cabecera.removeprefix("Bearer ").strip()
    if not token or not entorno.servidor_admite(entorno_pedido):
        return None
    return auth.cliente_admin().rpc("video_agente_identificar", {
        "p_token_hash": huella_token(token), "p_entorno": entorno_pedido,
    }).execute().data


def video_de_agente(id_video: int, id_profesora: str) -> dict | None:
    return auth.cliente_admin().rpc("video_de_agente", {"p_id_video": id_video, "p_id_profesora": id_profesora}).execute().data


def consultar(id_profesora: str, inicio: str, duracion_seg: int) -> dict:
    """Se pregunta antes de comprimir: lo no autorizado no se procesa."""
    return auth.cliente_admin().rpc("video_consultar", {
        "p_id_profesora": id_profesora, "p_inicio": inicio, "p_duracion_seg": duracion_seg,
    }).execute().data


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


def faltantes(video: dict, subidas: dict[str, int]) -> list[str]:
    """Partes que no están o no tienen el tamaño esperado."""
    return [p["nombre"] for p in video["partes"] if subidas.get(p["nombre"]) != p["bytes"]]


def publicar(video: dict, id_profesora: str) -> dict:
    if video["estado"] == "subiendo" and faltantes(video, _subidas(video, almacen())):
        return {"resultado": "partes_incompletas"}
    return auth.cliente_admin().rpc("video_publicar", {"p_id_video": video["id"], "p_id_profesora": id_profesora}).execute().data


def registrar_error(id_video: int, id_profesora: str, mensaje: str) -> None:
    auth.cliente_admin().rpc("video_error", {"p_id_video": id_video, "p_id_profesora": id_profesora,
                                             "p_mensaje": limpiar_mensaje(mensaje)}).execute()


# ---------- limpieza ----------

def secreto_valido(cabecera: str, secreto: str) -> bool:
    return hmac.compare_digest(cabecera, f"Bearer {secreto}")


def borrar_archivos(bucket, id_empresa: int, id_video: int, partes: list[dict]) -> None:
    """Borra las partes de un video y comprueba listando el bucket: falla si queda alguna."""
    bucket.remove([ruta(id_empresa, id_video, p["nombre"]) for p in partes])
    quedan = {p["nombre"] for p in partes} & set(_subidas({"id": id_video, "id_empresa": id_empresa}, bucket))
    if quedan:
        raise RuntimeError(f"Siguen en Storage: {', '.join(sorted(quedan))}")


def limpiar() -> dict:
    """Borra los archivos vencidos y conserva el registro. No decide el acceso: eso lo hace vence_en. Lo que falla
    queda anotado en la fila y se vuelve a intentar en la próxima pasada."""
    admin = auth.cliente_admin()
    pendientes = admin.rpc("videos_a_limpiar", {"p_limite": LIMITE_LIMPIEZA}).execute().data or []
    bucket = almacen() if pendientes else None
    resultado = {"eliminados": 0, "errores": 0}
    for v in pendientes:
        try:
            borrar_archivos(bucket, v["id_empresa"], v["id"], v["partes"])
            admin.rpc("video_marcar_eliminado", {"p_id_video": v["id"]}).execute()
            resultado["eliminados"] += 1
        except Exception as e:
            logging.exception("No se pudieron borrar los archivos del video %s", v["id"])
            resultado["errores"] += 1
            admin.rpc("video_limpieza_error", {"p_id_video": v["id"], "p_mensaje": limpiar_mensaje(str(e))}).execute()
    return resultado


def limpiar_sin_fallar() -> None:
    try:
        limpiar()
    except Exception:
        logging.exception("Falló la limpieza de videos")
