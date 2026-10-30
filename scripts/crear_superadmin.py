"""Crea la cuenta de la superadministradora (administración general de Just Show Up): no pertenece a ningún
lugar, entra por /admin/login y solo ve el panel de empresas.

ESCRIBE EN LA BASE DEL .env: correrlo contra Supabase de producción necesita la autorización de Julieta.
La contraseña se escribe por teclado (no se ve ni pasa por el chat).

Uso:
    python scripts/crear_superadmin.py
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
    mail = (input("Mail [superadmin@prueba.invalid]: ").strip() or "superadmin@prueba.invalid").lower()
    nombre = input("Nombre [Administración]: ").strip() or "Administración"
    apellido = input("Apellido [General]: ").strip() or "General"
    problema = auth.validar_mail(mail) or validaciones.nombre(nombre, "El nombre") or validaciones.nombre(apellido, "El apellido")
    if problema:
        raise SystemExit(problema)
    contrasena = getpass.getpass("Contraseña (mínimo 8 caracteres, no se ve al escribir): ")
    problema = auth.validar_contrasena(contrasena)
    if problema:
        raise SystemExit(problema)
    if getpass.getpass("Repetí la contraseña: ") != contrasena:
        raise SystemExit("Las contraseñas no coinciden.")

    try:
        uid, _ = auth.crear_cuenta(mail, nombre, apellido, contrasena=contrasena, sin_empresa=True)
    except auth.ErrorAuth as e:
        raise SystemExit(str(e))
    admin = auth.cliente_admin()
    admin.table("profiles").update({"es_superadmin": True}).eq("id", uid).execute()
    if admin.table("membresia").select("id").eq("id_usuario", uid).execute().data:
        admin.auth.admin.delete_user(uid)
        raise SystemExit("La cuenta quedó con una empresa asignada: se borró. No se creó la superadministradora.")
    print(f"\nListo: {mail} entra por /admin/login y solo ve el panel de empresas.")


if __name__ == "__main__":
    main()
