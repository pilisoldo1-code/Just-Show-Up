"""Crea la cuenta de la profesora: administradora y profesora de Just Show Up (Empresa 1).

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

from app import auth, validaciones  # noqa: E402


def main() -> None:
    if not os.environ.get("SUPABASE_SERVICE_KEY"):
        raise SystemExit("Falta SUPABASE_SERVICE_KEY en el archivo .env.")

    mail = input("Mail de la profesora: ").strip().lower()
    problema = auth.validar_mail(mail)
    if problema:
        raise SystemExit(problema)
    nombre = input("Nombre: ").strip()
    apellido = input("Apellido: ").strip()
    problema = validaciones.nombre(nombre, "El nombre") or validaciones.nombre(apellido, "El apellido")
    if problema:
        raise SystemExit(problema)

    contrasena = getpass.getpass("Contraseña (mínimo 8 caracteres, no se ve al escribir): ")
    problema = auth.validar_contrasena(contrasena)
    if problema:
        raise SystemExit(problema)
    if getpass.getpass("Repetí la contraseña: ") != contrasena:
        raise SystemExit("Las contraseñas no coinciden.")

    # Multiempresa: la cuenta queda como administradora y profesora de la Empresa 1 (Just Show Up) por su
    # membresía; los roles nunca salen de profiles.rol.
    empresa = auth.cliente_admin().table("empresa").select("id").eq("codigo", "JSU").execute().data
    if not empresa:
        raise SystemExit("No existe la empresa Just Show Up (JSU).")
    try:
        auth.crear_cuenta(mail, nombre, apellido, rol="profesora", contrasena=contrasena,
                          id_empresa=empresa[0]["id"], rol_empresa="admin_profesora")
    except auth.ErrorAuth as e:
        raise SystemExit(str(e))

    print(f"\nListo: {nombre} {apellido} ({mail}) ya puede iniciar sesión en «Just Show Up» como administradora y profesora.")


if __name__ == "__main__":
    main()
