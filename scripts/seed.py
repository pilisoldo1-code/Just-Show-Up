"""Script standalone de Python que prueba la conexión a Supabase.

Uso:
    python scripts/seed.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app.db import get_supabase  # noqa: E402

SEED_TITLES = ["Primer item", "Segundo item", "Tercer item"]


def main() -> None:
    supabase = get_supabase()
    for title in SEED_TITLES:
        supabase.table("items").insert({"title": title}).execute()
        print(f"Insertado: {title}")

    result = supabase.table("items").select("*").execute()
    print(f"\nTotal de items en la tabla: {len(result.data)}")


if __name__ == "__main__":
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        raise SystemExit("Faltan SUPABASE_URL / SUPABASE_KEY. Copiá .env.example a .env y completalo.")
    main()
