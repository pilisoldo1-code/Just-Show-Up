"""Tipos de clase (Gimnasia, Pilates, Yoga...): catálogo de disciplinas de la profesora.

Las escrituras van por funciones de la base (crear_tipo_clase, aplicar_cambio_tipo, ...),
que validan el rol y aplican las reglas sobre las clases futuras. Acá se validan los
formularios, se arman las vistas previas y se suben las imágenes a Supabase Storage.
"""
import logging
import uuid

from supabase import Client

from app import auth
from app.clases import DIAS_ORDEN, nombre_dia

BUCKET = "tipos-clase"
TAMANO_MAX_IMAGEN = 2 * 1024 * 1024
# Se valida el contenido real del archivo (no solo la extensión o el tipo que manda el navegador).
FIRMAS_IMAGEN = {
    "image/jpeg": (b"\xff\xd8\xff", "jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", "png"),
}
LIMITES = {"duracion": (1, 600), "cupo_max": (1, 200), "precio_presencial": (0, 1_000_000)}


def listar(sb: Client) -> list[dict]:
    tipos = sb.table("tipo_clase").select("*").order("nombre").execute().data
    horarios = sb.table("horario").select("id_tipo_clase, activo").execute().data
    for t in tipos:
        t["horarios_activos"] = sum(h["id_tipo_clase"] == t["id"] and h["activo"] for h in horarios)
        t["horarios"] = sum(h["id_tipo_clase"] == t["id"] for h in horarios)
    tipos.sort(key=lambda t: (not t["activo"], t["nombre"].lower()))
    return tipos


def obtener(sb: Client, tipo_id: int) -> dict | None:
    filas = sb.table("tipo_clase").select("*").eq("id", tipo_id).execute().data
    return filas[0] if filas else None


def activos(sb: Client) -> list[dict]:
    return sb.table("tipo_clase").select("*").eq("activo", True).order("nombre").execute().data


def validar(form: dict) -> tuple[dict, list[str]]:
    """Convierte y valida el formulario. Devuelve (datos, errores)."""
    errores = []
    datos = {"nombre": " ".join(form.get("nombre", "").split()), "descripcion": form.get("descripcion", "").strip()}
    if not datos["nombre"]:
        errores.append("Escribí el nombre del tipo de clase.")
    elif len(datos["nombre"]) > 60:
        errores.append("El nombre puede tener como máximo 60 caracteres.")
    if len(datos["descripcion"]) > 500:
        errores.append("La descripción puede tener como máximo 500 caracteres.")
    nombres = {"duracion": "La duración", "cupo_max": "El cupo máximo", "precio_presencial": "El precio"}
    for campo, (minimo, maximo) in LIMITES.items():
        texto = str(form.get(campo, "")).strip().replace(",", ".")
        try:
            valor = float(texto) if campo == "precio_presencial" else int(texto)
        except ValueError:
            errores.append(f"{nombres[campo]} tiene que ser un número.")
            continue
        if not minimo <= valor <= maximo:
            errores.append(f"{nombres[campo]} tiene que estar entre {minimo} y {maximo:,}.".replace(",", "."))
        datos[campo] = valor
    return datos, errores


def crear(sb: Client, datos: dict) -> tuple[bool, str, int | None]:
    r = sb.rpc("crear_tipo_clase", {
        "p_nombre": datos["nombre"], "p_descripcion": datos["descripcion"], "p_duracion": datos["duracion"],
        "p_precio": datos["precio_presencial"], "p_cupo": datos["cupo_max"], "p_imagen_url": None,
    }).execute().data
    if r["resultado"] == "ok":
        return True, f"Tipo de clase \"{datos['nombre']}\" creado.", r["id"]
    return False, _mensaje(r["resultado"]), None


def vista_previa(sb: Client, tipo: dict, datos: dict) -> dict:
    """Qué pasaría con las clases futuras ya generadas. Solo lectura."""
    r = sb.rpc("previsualizar_cambio_tipo", {
        "p_id": tipo["id"], "p_duracion": datos["duracion"], "p_precio": datos["precio_presencial"],
        "p_cupo": datos["cupo_max"],
    }).execute().data
    for e in r.get("excedidas", []):
        e["hora"] = e["hora"][:5]
        e["fecha_txt"] = _fecha_txt(e["fecha"])
    return r


def cambian_valores(tipo: dict, datos: dict) -> bool:
    return (tipo["duracion"] != datos["duracion"] or float(tipo["precio_presencial"]) != datos["precio_presencial"]
            or tipo["cupo_max"] != datos["cupo_max"])


def aplicar(sb: Client, tipo_id: int, datos: dict) -> tuple[bool, str]:
    r = sb.rpc("aplicar_cambio_tipo", {
        "p_id": tipo_id, "p_nombre": datos["nombre"], "p_descripcion": datos["descripcion"],
        "p_duracion": datos["duracion"], "p_precio": datos["precio_presencial"], "p_cupo": datos["cupo_max"],
    }).execute().data
    if r["resultado"] != "ok":
        return False, _mensaje(r["resultado"])
    texto = "Cambios guardados."
    if r["actualizan_todo"] or r["conservan_precio"]:
        texto += f" Clases ya generadas: {r['actualizan_todo']} con los valores nuevos"
        if r["conservan_precio"]:
            texto += f" y {r['conservan_precio']} que conservan precio y duración porque ya tienen reservas"
        texto += "."
    return True, texto


def cambiar_estado(sb: Client, tipo_id: int, activo: bool) -> tuple[bool, str]:
    r = sb.rpc("cambiar_estado_tipo_clase", {"p_id": tipo_id, "p_activo": activo}).execute().data
    if r["resultado"] == "ok":
        return True, "Tipo de clase activado." if activo else "Tipo de clase desactivado."
    if r["resultado"] == "tiene_horarios_activos":
        n = r["horarios"]
        return False, (f"No se puede desactivar: tiene {n} {'horario activo' if n == 1 else 'horarios activos'}. "
                       "Primero cambiá esos horarios a otro tipo o desactivalos.")
    return False, _mensaje(r["resultado"])


def _mensaje(codigo: str) -> str:
    return {
        "no_autorizada": "Solo la profesora puede modificar los tipos de clase.",
        "nombre_repetido": "Ya existe un tipo de clase con ese nombre.",
        "datos_invalidos": "Revisá los datos: duración, precio y cupo tienen que ser válidos.",
        "no_existe": "No se encontró ese tipo de clase.",
        "tipo_inactivo": "Ese tipo de clase está desactivado.",
    }.get(codigo, "No se pudo guardar. Probá de nuevo.")


def _fecha_txt(iso: str) -> str:
    from datetime import date
    f = date.fromisoformat(iso)
    return f"{nombre_dia(f)} {f.day}/{f.month}"


# ---------- imágenes (Supabase Storage) ----------

def validar_imagen(contenido: bytes) -> tuple[str | None, str | None]:
    """Devuelve (tipo_mime, extensión) o (None, mensaje de error)."""
    if not contenido:
        return None, "Elegí una imagen."
    if len(contenido) > TAMANO_MAX_IMAGEN:
        return None, "La imagen pesa más de 2 MB."
    for mime, (firma, ext) in FIRMAS_IMAGEN.items():
        if contenido.startswith(firma):
            return mime, ext
    if contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None, "Formato no permitido: usá JPG, PNG o WebP."


def subir_imagen(sb: Client, tipo: dict, contenido: bytes) -> tuple[bool, str]:
    mime, ext = validar_imagen(contenido)
    if mime is None:
        return False, ext
    ruta = f"{tipo['id']}-{uuid.uuid4().hex[:12]}.{ext}"
    try:
        almacen = auth.cliente_admin().storage.from_(BUCKET)
        almacen.upload(ruta, contenido, {"content-type": mime})
        url = almacen.get_public_url(ruta).rstrip("?")
    except auth.ErrorAuth as e:
        return False, str(e)
    except Exception:
        logging.exception("No se pudo subir la imagen del tipo de clase")
        return False, "No se pudo subir la imagen. Probá de nuevo."
    if sb.rpc("cambiar_imagen_tipo_clase", {"p_id": tipo["id"], "p_imagen_url": url}).execute().data != "ok":
        _borrar_archivo(ruta)
        return False, "No se pudo guardar la imagen."
    _borrar_archivo_de_url(tipo.get("imagen_url"))
    return True, "Imagen actualizada."


def quitar_imagen(sb: Client, tipo: dict) -> tuple[bool, str]:
    if sb.rpc("cambiar_imagen_tipo_clase", {"p_id": tipo["id"], "p_imagen_url": None}).execute().data != "ok":
        return False, "No se pudo quitar la imagen."
    _borrar_archivo_de_url(tipo.get("imagen_url"))
    return True, "Imagen quitada."


def _borrar_archivo_de_url(url: str | None) -> None:
    marca = f"/storage/v1/object/public/{BUCKET}/"
    if url and marca in url:
        _borrar_archivo(url.split(marca, 1)[1])


def _borrar_archivo(ruta: str) -> None:
    try:
        auth.cliente_admin().storage.from_(BUCKET).remove([ruta])
    except Exception:
        logging.warning("No se pudo borrar la imagen anterior del bucket (queda huérfana).")


# ---------- horarios ----------

def vista_previa_horario(sb: Client, horario_id: int, tipo_id: int) -> dict:
    return sb.rpc("previsualizar_cambio_tipo_horario", {"p_id_horario": horario_id, "p_id_tipo": tipo_id}).execute().data


def cambiar_tipo_horario(sb: Client, horario_id: int, tipo_id: int) -> tuple[bool, str]:
    r = sb.rpc("cambiar_tipo_horario", {"p_id_horario": horario_id, "p_id_tipo": tipo_id}).execute().data
    if r["resultado"] != "ok":
        return False, _mensaje(r["resultado"])
    texto = "Horario actualizado."
    if r["cambian"] or r["conservan"]:
        texto += f" Clases ya generadas: {r['cambian']} pasan al tipo nuevo"
        if r["conservan"]:
            texto += f" y {r['conservan']} quedan como estaban porque ya tienen reservas"
        texto += "."
    return True, texto


def orden_horario(h: dict) -> tuple:
    return DIAS_ORDEN.index(h["dia_semana"]), h["hora_inicio"]
