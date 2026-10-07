"""Crea la cuenta de la profesora (administradora) con rol 'profesora'.

Se corre una sola vez al entregar el sistema. No hay registro público de profesoras.

Uso:
    python scripts/crear_profesora.py
"""

import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app import auth  # noqa: E402


def main() -> None:
    if not os.environ.get("SUPABASE_SERVICE_KEY"):
        raise SystemExit("Falta SUPABASE_SERVICE_KEY en el archivo .env.")

    mail = input("Mail de la profesora: ").strip().lower()
    problema = auth.validar_mail(mail)
    if problema:
        raise SystemExit(problema)
    nombre = input("Nombre: ").strip()
    apellido = input("Apellido: ").strip()
    if not nombre or not apellido:
        raise SystemExit("El nombre y el apellido son obligatorios.")

    contrasena = getpass.getpass("Contraseña (mínimo 8 caracteres, no se ve al escribir): ")
    problema = auth.validar_contrasena(contrasena)
    if problema:
        raise SystemExit(problema)
    if getpass.getpass("Repetí la contraseña: ") != contrasena:
        raise SystemExit("Las contraseñas no coinciden.")

    try:
        auth.crear_cuenta(mail, nombre, apellido, rol="profesora", contrasena=contrasena)
    except auth.ErrorAuth as e:
        raise SystemExit(str(e))

    print(f"\nListo: {nombre} {apellido} ({mail}) ya puede iniciar sesión como profesora.")


if __name__ == "__main__":
    main()
