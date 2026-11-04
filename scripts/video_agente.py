"""Autoriza una computadora para subir videos (programa local de Videos On Demand) y prepara el bucket.

El token se muestra una sola vez: en la base queda solo su huella. Por defecto se niega a correr fuera de localhost:
contra un proyecto remoto exige --remoto (y autorización).

Uso:
    python scripts/video_agente.py crear <mail de la profesora> "<nombre de la computadora>" [--remoto]
    python scripts/video_agente.py desactivar <mail de la profesora> [--remoto]
    python scripts/video_agente.py bucket [--remoto]    # solo si los archivos van en un proyecto de Supabase aparte
"""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from supabase import create_client  # noqa: E402

from app import auth, videos  # noqa: E402


def id_de(mail: str) -> str:
    filas = auth.cliente_admin().table("profiles").select("id").eq("mail", mail.strip().lower()).execute().data
    if not filas:
        sys.exit("No encontré esa cuenta.")
    return filas[0]["id"]


def crear(mail: str, nombre: str) -> None:
    token = secrets.token_urlsafe(32)
    auth.cliente_admin().table("video_agente").insert(
        {"id_profesora": id_de(mail), "nombre": nombre, "token_hash": videos.huella_token(token)}
    ).execute()
    print(f"Token de esta computadora (se muestra una sola vez):\n{token}")


def desactivar(mail: str) -> None:
    auth.cliente_admin().table("video_agente").update({"activo": False}).eq("id_profesora", id_de(mail)).execute()
    print("Listo: las computadoras de esa cuenta ya no pueden subir videos.")


def bucket() -> None:
    url, clave = os.environ.get("VIDEOS_SUPABASE_URL"), os.environ.get("VIDEOS_SUPABASE_SERVICE_KEY")
    if not url or not clave:
        sys.exit("Faltan VIDEOS_SUPABASE_URL y VIDEOS_SUPABASE_SERVICE_KEY.")
    create_client(url, clave).storage.create_bucket(videos.BUCKET, options={
        "public": False, "file_size_limit": 52428800, "allowed_mime_types": ["video/mp2t"],
    })
    print(f"Bucket privado {videos.BUCKET} creado.")


def es_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--remoto"]
    if not es_local(os.environ.get("SUPABASE_URL", "")) and "--remoto" not in sys.argv:
        sys.exit("SUPABASE_URL no es local: para usarlo igual, agregá --remoto (y tené autorización).")
    accion = args[0] if args else ""
    if accion == "crear" and len(args) == 3:
        crear(args[1], args[2])
    elif accion == "desactivar" and len(args) == 2:
        desactivar(args[1])
    elif accion == "bucket":
        bucket()
    else:
        sys.exit(__doc__)
