"""Programa local de Videos On Demand: corre en la computadora donde la profesora da la clase con Zoom.

Mira la carpeta de grabaciones y espera a que el archivo esté completo. Le pregunta al servidor si esa clase está
autorizada ANTES de comprimir: lo que no lo está no se procesa ni se sube. Lo autorizado se comprime con FFmpeg, se
divide en partes de hasta 45 MB, se sube a Supabase y se publica. Si algo falla, reintenta más tarde sin duplicar: el
estado queda en un SQLite local y el servidor identifica cada archivo por su huella.

Solo necesita Python 3.10+ y FFmpeg (sin paquetes extra). Usa la zona horaria de la computadora, que tiene que ser la
de las clases. Configuración e instalación: agente/README.md.

Uso:
    python agente_videos.py             # queda mirando la carpeta
    python agente_videos.py --una-vez   # una sola pasada (para el Programador de tareas de Windows)
"""
import argparse
import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

AQUI = Path(__file__).resolve().parent
TAMANO_MAXIMO_PARTE = 45_000_000
# Diferencia aceptada entre la duración que declara el MP4 y la del contenido que realmente se comprimió.
TOLERANCIA_DURACION_SEG = 3
# Miniatura de la biblioteca: un fotograma JPEG de 480x270 (16:9). Es opcional: si falla, el video se publica igual.
MINIATURA = "miniatura.jpg"
MINIATURA_MIN_BYTES, MINIATURA_MAX_BYTES = 1000, 102_400
SEGUNDOS_ESTABLE = 180
SEGUNDOS_ENTRE_PASADAS = 60
ESPERAS_REINTENTO_SEG = [60, 300, 900, 3600]
ESPERA_DECISION_SEG = 1800
HORAS_REINTENTO_DECISION = 24
ANTIGUEDAD_MAXIMA_HORAS = 72
TAMANO_MINIMO_VIDEO = 1_000_000
# Las clases se generan cuando alguien abre ciertas pantallas: una clase puede aparecer después, y el espacio se libera.
RESULTADOS_REINTENTABLES = {"sin_clase", "sin_espacio"}
PROGRAMAS = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}
# Mismas huellas que app/entorno.py: este programa se instala solo, sin el resto de la app.
HUELLAS_PRODUCCION = {
    "b876de330340b21a5dc25a639a9a40b2d7266dbb7db26f7982a6ef0a22205fc8",
    "e050af1051bb53a852bb650f2bf80e8ffbc5d5a218ddbff4c0d613371b14c90c",
    "41dcb3ec2a68f5edce8a58c5149a882df4718a3e796a30f9528045287ff74da6",
}
COMPRESION = [
    "-vf", "scale=-2:360,fps=24", "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-maxrate", "340k",
    "-bufsize", "680k", "-g", "144", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
    "-f", "hls", "-hls_time", "6", "-hls_segment_type", "mpegts", "-hls_flags", "single_file",
    "-hls_playlist_type", "vod",
]


class ErrorAgente(Exception):
    pass


class EntornoNoAutorizado(Exception):
    """No se reintenta: el programa se detiene sin enviar nada."""


def es_produccion(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    candidatos = {host, host.split(".")[0]}
    return any(hashlib.sha256(c.encode()).hexdigest() in HUELLAS_PRODUCCION for c in candidatos)


def es_red_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def validar_config(config: dict, produccion: bool) -> None:
    """Antes de leer ninguna grabación. Sin --produccion solo se habla con un servidor de la red local."""
    url, entorno = config.get("url", ""), config.get("entorno", "")
    if produccion:
        if not entorno.startswith("produccion-") or urlparse(url).scheme != "https":
            raise EntornoNoAutorizado("Con --produccion hacen falta un entorno produccion-... y una URL https.")
        return
    if es_produccion(url):
        raise EntornoNoAutorizado("Esa URL es producción y no se indicó --produccion: no se envía nada.")
    if not entorno.startswith("pruebas-"):
        raise EntornoNoAutorizado("Falta \"entorno\" en config.json (pruebas-...).")
    if not es_red_local(url):
        raise EntornoNoAutorizado("En pruebas el servidor tiene que estar en la red local.")


# ---------- lista HLS ----------

def dividir_lista(lista: str, archivo: str, tamano_maximo: int = TAMANO_MAXIMO_PARTE) -> tuple[list[dict], str]:
    """Reparte los segmentos en partes sin cortar ninguno. Devuelve las partes
    ({nombre, inicio, bytes}) y la lista con cada segmento apuntando a su parte."""
    partes: list[dict] = []
    salida: list[str] = []
    rango = None
    for linea in lista.splitlines():
        m = re.fullmatch(r"#EXT-X-BYTERANGE:(\d+)@(\d+)", linea.strip())
        if m:
            rango = int(m[1]), int(m[2])
            continue
        if linea.strip() != archivo:
            salida.append(linea)
            continue
        if rango is None:
            raise ErrorAgente("La lista HLS no trae rangos de bytes")
        largo, inicio = rango
        if largo > tamano_maximo:
            raise ErrorAgente("Un segmento supera el tamaño máximo de una parte")
        if not partes or partes[-1]["bytes"] + largo > tamano_maximo:
            partes.append({"nombre": f"parte-{len(partes):02d}.ts", "inicio": inicio, "bytes": 0})
        parte = partes[-1]
        if inicio != parte["inicio"] + parte["bytes"]:
            raise ErrorAgente("Los segmentos de la lista HLS no son contiguos")
        salida.append(f"#EXT-X-BYTERANGE:{largo}@{parte['bytes']}")
        salida.append(parte["nombre"])
        parte["bytes"] += largo
        rango = None
    if not partes:
        raise ErrorAgente("La lista HLS no tiene segmentos")
    return partes, "\n".join(salida) + "\n"


def cortar_archivo(origen: Path, partes: list[dict], destino: Path) -> None:
    with origen.open("rb") as f:
        for parte in partes:
            f.seek(parte["inicio"])
            with (destino / parte["nombre"]).open("wb") as salida:
                restante = parte["bytes"]
                while restante:
                    bloque = f.read(min(1024 * 1024, restante))
                    if not bloque:
                        raise ErrorAgente("El archivo comprimido es más corto que su lista")
                    salida.write(bloque)
                    restante -= len(bloque)


# ---------- grabaciones de Zoom ----------

def hora_de_carpeta(nombre: str) -> datetime | None:
    """Zoom nombra la carpeta «2026-11-16 08.01.23 Tema de la reunión», con la hora local de la computadora."""
    m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2})", nombre)
    if not m:
        return None
    return datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}:{m[4]}").astimezone()


def buscar_grabaciones(carpeta: Path) -> list[tuple[Path, int]]:
    """Por cada carpeta de reunión: su .mp4 más grande y cuántos videos tiene. Los nombres cambian según la versión de
    Zoom; detener y volver a grabar deja más de un archivo."""
    encontrados = []
    for reunion in sorted(p for p in carpeta.iterdir() if p.is_dir()):
        videos = list(reunion.glob("*.mp4"))
        if videos:
            grandes = [v for v in videos if v.stat().st_size >= TAMANO_MINIMO_VIDEO]
            encontrados.append((max(videos, key=lambda p: p.stat().st_size), len(grandes)))
    return encontrados


def huella_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        while bloque := f.read(1024 * 1024):
            h.update(bloque)
    return h.hexdigest()


def duracion_segundos(ruta: Path) -> int:
    r = subprocess.run([PROGRAMAS["ffprobe"], "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(ruta)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise ErrorAgente(f"FFmpeg no pudo leer el archivo: {r.stderr.strip()[:200]}")
    return round(float(r.stdout.strip()))


def duraciones_por_flujo(ruta: Path) -> dict[str, float]:
    """Duración de cada tipo de flujo (video, audio) según FFmpeg; sin duración conocida queda en 0."""
    r = subprocess.run([PROGRAMAS["ffprobe"], "-v", "error", "-show_entries", "stream=codec_type,duration",
                        "-of", "json", str(ruta)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorAgente(f"FFmpeg no pudo leer el archivo: {r.stderr.strip()[:200]}")
    duraciones: dict[str, float] = {}
    for flujo in json.loads(r.stdout or "{}").get("streams", []):
        tipo = flujo.get("codec_type")
        if tipo in ("video", "audio"):
            try:
                duracion = float(flujo.get("duration") or 0)
            except ValueError:
                duracion = 0.0
            duraciones[tipo] = max(duraciones.get(tipo, 0.0), duracion)
    return duraciones


def verificar_comprimido(origen: Path, comprimido: Path) -> float:
    """Un MP4 cortado puede declarar los 40 minutos completos (si el índice está al principio) y FFmpeg comprime
    sin error solo lo que hay. El .ts comprimido no tiene índice: su duración sale de las marcas de tiempo del
    contenido. Se exige que video y audio duren lo que declara el original y que se decodifique entero sin errores."""
    r = subprocess.run([PROGRAMAS["ffprobe"], "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(origen)], capture_output=True, text=True)
    try:
        declarada = float(r.stdout.strip())
    except ValueError:
        raise ErrorAgente(f"FFmpeg no pudo leer la duración del original: {r.stderr.strip()[:200]}")
    esperados = duraciones_por_flujo(origen).keys() | {"video"}
    reales = duraciones_por_flujo(comprimido)
    for tipo in ("video", "audio"):
        if tipo not in esperados:
            continue
        if tipo not in reales:
            raise ErrorAgente(f"El video comprimido no tiene {tipo}")
        if abs(reales[tipo] - declarada) > TOLERANCIA_DURACION_SEG:
            raise ErrorAgente(f"Grabación incompleta: el {tipo} dura {reales[tipo]:.0f} s y el archivo declara "
                              f"{declarada:.0f} s")
    r = subprocess.run([PROGRAMAS["ffmpeg"], "-v", "error", "-i", str(comprimido), "-map", "0", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0 or r.stderr.strip():
        raise ErrorAgente(f"El video comprimido tiene errores: {r.stderr.strip()[:300]}")
    return reales["video"]


def miniatura_valida(ruta: Path) -> bool:
    try:
        datos = ruta.read_bytes()
    except OSError:
        return False
    return MINIATURA_MIN_BYTES <= len(datos) <= MINIATURA_MAX_BYTES and datos[:3] == b"\xff\xd8\xff"


def generar_miniatura(comprimido: Path, carpeta: Path, duracion: float) -> bool:
    """Un fotograma al 10 % de la clase (evita el negro del principio), en 480x270 con bandas si hiciera falta.
    Nunca corta el procesamiento: si algo falla, no hay miniatura."""
    destino = carpeta / MINIATURA
    try:
        r = subprocess.run([PROGRAMAS["ffmpeg"], "-y", "-v", "error", "-ss", f"{max(1.0, duracion * 0.10):.1f}",
                            "-i", str(comprimido), "-frames:v", "1", "-vf",
                            "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,"
                            "format=yuvj420p", "-q:v", "5", "-update", "1", str(destino)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and miniatura_valida(destino):
            return True
        logging.warning("No se pudo generar la miniatura: %s", r.stderr.strip()[:200])
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning("No se pudo generar la miniatura: %s", e)
    destino.unlink(missing_ok=True)
    return False


def limpiar_intermedios(carpeta: Path) -> None:
    """Borra solo lo que deja `comprimir`, para que un intento fallido no quede a medias."""
    for archivo in [carpeta / "salida.ts", carpeta / "salida.m3u8", carpeta / "manifiesto.json.tmp", carpeta / MINIATURA,
                    *carpeta.glob("parte-*.ts")]:
        archivo.unlink(missing_ok=True)


def comprimir(origen: Path, carpeta: Path) -> tuple[list[dict], str]:
    """Deja parte-NN.ts en `carpeta`. Si ya estaban de un intento anterior, no vuelve a comprimir: el manifiesto
    se escribe recién cuando todo está verificado."""
    manifiesto = carpeta / "manifiesto.json"
    if manifiesto.exists():
        datos = json.loads(manifiesto.read_text())
        return datos["partes"], datos["lista_hls"]
    carpeta.mkdir(parents=True, exist_ok=True)
    lista = carpeta / "salida.m3u8"
    try:
        r = subprocess.run([PROGRAMAS["ffmpeg"], "-y", "-v", "error", "-i", str(origen), *COMPRESION, str(lista)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ErrorAgente(f"Falló la compresión: {r.stderr.strip()[:300]}")
        duracion = verificar_comprimido(origen, carpeta / "salida.ts")
        generar_miniatura(carpeta / "salida.ts", carpeta, duracion)
        partes, lista_hls = dividir_lista(lista.read_text(), "salida.ts")
        cortar_archivo(carpeta / "salida.ts", partes, carpeta)
        (carpeta / "salida.ts").unlink()
        provisorio = carpeta / "manifiesto.json.tmp"
        provisorio.write_text(json.dumps({"partes": partes, "lista_hls": lista_hls}))
        provisorio.replace(manifiesto)
    except Exception:
        limpiar_intermedios(carpeta)
        raise
    return partes, lista_hls


# ---------- servidor ----------

class Servidor:
    def __init__(self, url: str, token: str, entorno: str = "", produccion: bool = False):
        self.url, self.token, self.entorno, self.produccion = url.rstrip("/"), token, entorno, produccion

    def post(self, ruta: str, datos: dict | None = None) -> dict:
        pedido = urllib.request.Request(
            f"{self.url}/api/videos/agente/{ruta}", data=json.dumps(datos or {}).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "X-Video-Entorno": self.entorno,
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(pedido, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise EntornoNoAutorizado(f"El servidor rechazó este programa ({e.code}).") from e
            raise

    def comprobar_entorno(self) -> None:
        if self.post("entorno").get("entorno") != self.entorno:
            raise EntornoNoAutorizado("El servidor está en otro entorno.")


def subir_parte(servidor: Servidor, url: str, ruta: Path) -> None:
    if es_produccion(url) and not servidor.produccion:
        raise EntornoNoAutorizado("El servidor pidió subir a producción: no se envía nada.")
    pedido = urllib.request.Request(url, data=ruta.read_bytes(), method="PUT", headers={"Content-Type": "video/mp2t"})
    with urllib.request.urlopen(pedido, timeout=900):
        pass


def avisar_error(servidor: Servidor, id_video: int, mensaje: str) -> None:
    """Sin conexión este aviso también falla: el error igual queda en el registro local."""
    try:
        servidor.post(f"{id_video}/error", {"mensaje": mensaje})
    except OSError:
        logging.info("No se pudo avisar el error del video %s", id_video)


def inicio_de(video: Path, duracion: int) -> datetime:
    inicio = hora_de_carpeta(video.parent.name)
    return inicio or datetime.fromtimestamp(video.stat().st_mtime).astimezone() - timedelta(seconds=duracion)


def procesar(servidor: Servidor, video: Path, trabajo: Path) -> tuple[str, int | None]:
    """Devuelve la decisión del servidor (o «publicado») y el id del video, si existe."""
    duracion = duracion_segundos(video)
    inicio = inicio_de(video, duracion).isoformat()
    consulta = servidor.post("consultar", {"inicio": inicio, "duracion_seg": duracion})
    if consulta["resultado"] != "autorizada":
        return consulta["resultado"], consulta.get("id_video")

    huella = huella_archivo(video)
    carpeta = trabajo / huella
    partes, lista_hls = comprimir(video, carpeta)
    datos = {
        "inicio": inicio, "huella": huella, "duracion_seg": duracion,
        "tamano_bytes": sum(p["bytes"] for p in partes),
        "partes": [{"nombre": p["nombre"], "bytes": p["bytes"]} for p in partes],
        "lista_hls": lista_hls,
    }
    if miniatura_valida(carpeta / MINIATURA):  # opcional: también se reutiliza en los reintentos
        datos["miniatura"] = base64.b64encode((carpeta / MINIATURA).read_bytes()).decode()
    registro = servidor.post("registrar", datos)
    if registro["resultado"] != "ok":
        return registro["resultado"], registro.get("id_video")

    id_video = registro["id_video"]
    try:
        subida = servidor.post(f"{id_video}/urls-subida")
        for nombre, url in subida["urls"].items():
            subir_parte(servidor, url, carpeta / nombre)
        publicado = servidor.post(f"{id_video}/publicar")
        if publicado["resultado"] != "ok":
            raise ErrorAgente(f"No se pudo publicar: {publicado['resultado']}")
    except (ErrorAgente, OSError) as e:
        avisar_error(servidor, id_video, str(e))
        raise
    shutil.rmtree(carpeta, ignore_errors=True)
    return "publicado", id_video


# ---------- cola local ----------

def abrir_cola(ruta: Path) -> sqlite3.Connection:
    cola = sqlite3.connect(ruta)
    cola.row_factory = sqlite3.Row
    cola.execute("""create table if not exists grabacion (
        ruta text primary key, tamano integer, visto_en real, detectado_en real, id_video integer,
        estado text not null default 'esperando', intentos integer not null default 0, proximo_intento real not null default 0,
        ultimo_error text)""")
    return cola


def tomar_candado(ruta: Path) -> sqlite3.Connection | None:
    """Evita dos ejecuciones a la vez. El sistema libera el candado aunque el programa se corte."""
    candado = sqlite3.connect(ruta, timeout=0, isolation_level=None)
    try:
        candado.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError:
        candado.close()
        return None
    return candado


def estable(cola: sqlite3.Connection, video: Path, ahora: float) -> bool:
    """True si el tamaño no cambió durante SEGUNDOS_ESTABLE: Zoom todavía puede estar convirtiendo."""
    tamano = video.stat().st_size
    fila = cola.execute("select tamano, visto_en from grabacion where ruta = ?", (str(video),)).fetchone()
    if fila is None:
        cola.execute("insert into grabacion (ruta, tamano, visto_en, detectado_en) values (?, ?, ?, ?)",
                     (str(video), tamano, ahora, ahora))
    elif fila["tamano"] != tamano:
        cola.execute("update grabacion set tamano = ?, visto_en = ? where ruta = ?", (tamano, ahora, str(video)))
    else:
        return tamano > 0 and ahora - fila["visto_en"] >= SEGUNDOS_ESTABLE
    return False


def espera_reintento(intentos: int) -> int:
    return ESPERAS_REINTENTO_SEG[min(intentos, len(ESPERAS_REINTENTO_SEG)) - 1]


def cerrar(cola: sqlite3.Connection, video: Path, estado: str, id_video: int | None = None) -> None:
    logging.info("%s: %s", video.parent.name, estado)
    with cola:
        cola.execute("update grabacion set estado = ?, id_video = ?, ultimo_error = null where ruta = ?",
                     (estado, id_video, str(video)))


def esperar(cola: sqlite3.Connection, video: Path, intentos: int, proximo: float, motivo: str) -> None:
    logging.warning("%s: intento %s: %s", video.parent.name, intentos, motivo)
    with cola:
        cola.execute("update grabacion set intentos = ?, proximo_intento = ?, ultimo_error = ? where ruta = ?",
                     (intentos, proximo, motivo[:500], str(video)))


def pasada(servidor: Servidor, carpeta_zoom: Path, trabajo: Path, cola: sqlite3.Connection, ahora: float) -> None:
    for video, cantidad in buscar_grabaciones(carpeta_zoom):
        with cola:
            listo = estable(cola, video, ahora)
        fila = cola.execute("select * from grabacion where ruta = ?", (str(video),)).fetchone()
        if not listo or fila["estado"] != "esperando" or ahora < fila["proximo_intento"]:
            continue
        if cantidad > 1:  # no se sabe cuál es la clase: no se adivina
            cerrar(cola, video, "varios_archivos")
            continue
        fecha = hora_de_carpeta(video.parent.name)
        if fecha and ahora - fecha.timestamp() > ANTIGUEDAD_MAXIMA_HORAS * 3600:
            cerrar(cola, video, "antigua")
            continue
        intentos = fila["intentos"] + 1
        try:
            resultado, id_video = procesar(servidor, video, trabajo)
        except (ErrorAgente, OSError, KeyError, ValueError) as e:
            esperar(cola, video, intentos, ahora + espera_reintento(intentos), str(e))
            continue
        vigente = ahora - fila["detectado_en"] < HORAS_REINTENTO_DECISION * 3600
        if resultado in RESULTADOS_REINTENTABLES and vigente:
            esperar(cola, video, intentos, ahora + ESPERA_DECISION_SEG, resultado)
        else:
            cerrar(cola, video, resultado, id_video)


def probar_archivo(video: Path, carpeta: Path) -> int:
    """Solo compresión y división, sin servidor: para validar FFmpeg con un video generado."""
    duracion = duracion_segundos(video)
    partes, lista = comprimir(video, carpeta)
    (carpeta / "lista.m3u8").write_text(lista)
    total = sum(p["bytes"] for p in partes)
    print(f"Duración: {duracion} s · Partes: {len(partes)} · Total: {total / 1e6:.1f} MB · "
          f"Mayor parte: {max(p['bytes'] for p in partes) / 1e6:.1f} MB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=AQUI / "config.json")
    parser.add_argument("--una-vez", action="store_true")
    parser.add_argument("--produccion", action="store_true", help="solo con autorización: permite un servidor de producción")
    parser.add_argument("--probar-archivo", type=Path, help="comprime y divide ese video, sin conectarse a nada")
    parser.add_argument("--salida", type=Path, default=AQUI / "trabajo" / "prueba", help="carpeta de --probar-archivo")
    args = parser.parse_args()

    if args.probar_archivo:
        return probar_archivo(args.probar_archivo, args.salida)

    config = json.loads(args.config.read_text())
    try:
        validar_config(config, args.produccion)
    except EntornoNoAutorizado as e:
        print(e, file=sys.stderr)
        return 2
    trabajo = Path(config.get("carpeta_trabajo", AQUI / "trabajo")).expanduser()
    trabajo.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=trabajo / "agente.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if os.name == "posix" and args.config.stat().st_mode & 0o077:
        logging.warning("config.json tiene el token y lo pueden leer otras cuentas: chmod 600 config.json")
    candado = tomar_candado(trabajo / "candado.sqlite")
    if candado is None:
        logging.info("Ya hay otra ejecución en curso")
        return 0
    for programa in PROGRAMAS:
        PROGRAMAS[programa] = config.get(programa, programa)
        if shutil.which(PROGRAMAS[programa]) is None:
            logging.error("No encontré %s", PROGRAMAS[programa])
            print(f"Falta instalar {programa} o indicar su ruta en config.json", file=sys.stderr)
            return 1

    servidor = Servidor(config["url"], config["token"], config["entorno"], args.produccion)
    carpeta_zoom = Path(config.get("carpeta_zoom", "~/Documents/Zoom")).expanduser()
    cola = abrir_cola(trabajo / "cola.sqlite")
    while True:
        try:
            servidor.comprobar_entorno()
            if carpeta_zoom.is_dir():
                pasada(servidor, carpeta_zoom, trabajo, cola, time.time())
            else:
                logging.warning("No existe la carpeta %s", carpeta_zoom)
        except EntornoNoAutorizado as e:
            logging.error("%s Se detiene sin enviar nada.", e)
            print(e, file=sys.stderr)
            return 2
        except PermissionError:
            logging.error("Sin permiso para leer %s (en Mac: Acceso total al disco)", carpeta_zoom)
        except OSError as e:
            logging.error("No se pudo revisar %s: %s", carpeta_zoom, e)
        if args.una_vez:
            return 0
        time.sleep(SEGUNDOS_ENTRE_PASADAS)


if __name__ == "__main__":
    sys.exit(main())
