"""Guarda en Vault la dirección del despachador y el secreto de las notificaciones por correo.

Los valores se piden por teclado (no pasan por el chat ni quedan en el historial de la terminal) y se guardan con
`notificaciones_guardar_secreto` usando la clave de administrador del .env. Por defecto solo corre contra un Supabase
local; contra producción exige --produccion (y la autorización de Julieta).

Uso:  python scripts/notificaciones_secretos.py [--produccion]
El mismo secreto va en la variable NOTIFICACIONES_SECRETO del servidor que despacha.
"""
import getpass
import hashlib
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from dotenv import load_dotenv  # noqa: E402

from app import auth, entorno  # noqa: E402

PRODUCCION = "nspgafucytcjqkqnmkry"


def main() -> int:
    # sin variables ya definidas, toma el .env de la carpeta principal del proyecto (no pisa lo que esté definido)
    load_dotenv(os.environ.get("RESPALDO_ENV", "/Users/julietaalvez/Desktop/OBLIGATORIO IET/.env"), override=False)
    url_base = os.environ.get("SUPABASE_URL", "")
    if not entorno.es_local(url_base) and "--produccion" not in sys.argv:
        print("Esto no es un Supabase local: para producción hace falta --produccion (y autorización).")
        return 1
    if "--produccion" in sys.argv and PRODUCCION not in url_base:
        print("--produccion pero el .env no apunta al proyecto de producción. No se hizo nada.")
        return 1
    print(f"Destino: {'Supabase local' if entorno.es_local(url_base) else 'PRODUCCIÓN (' + PRODUCCION + ')'}")
    url = input("Dirección del despachador (https://<sitio>/tareas/notificaciones): ").strip()
    secreto = getpass.getpass("Secreto (32 caracteres o más; no se muestra): ").strip()
    admin = auth.cliente_admin()
    for nombre, valor in (("notificaciones_url", url), ("notificaciones_secreto", secreto)):
        r = admin.rpc("notificaciones_guardar_secreto", {"p_nombre": nombre, "p_valor": valor}).execute().data
        if r != "ok":
            print(f"{nombre}: {r}. No se guardó.")
            return 1
    print("Guardados. Huella del secreto (sha256, para comparar con el servidor):",
          hashlib.sha256(secreto.encode()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
