"""Aplica las migraciones del repo a un proyecto de Supabase de PRUEBA, vacío y sin datos reales.

SEGURIDAD: rechaza producción, exige confirmar el proyecto con --proyecto y volver a escribirlo, comprueba que la URL de
la API y la conexión sean del mismo proyecto y se niega si la base ya tiene datos que no puso este script. Sin --aplicar
solo revisa los archivos y no se conecta a nada. Lee únicamente el archivo de --env, nunca el .env.

Uso:
    python scripts/videos_prueba_migrar.py                                    # revisa orden y dependencias
    python scripts/videos_prueba_migrar.py --aplicar --proyecto <ref>
Opciones: --env <archivo> (por defecto .env.videos-prueba). Necesita VIDEOS_PRUEBA_DB_URL y requirements-dev.txt.
"""
import argparse
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from app import entorno  # noqa: E402

CARPETA = RAIZ / "supabase" / "migrations"
# Rama de Josefina, reemplazada por 20261021150000 (ver CLAUDE.md): aplicar las dos crea conflicto.
PROHIBIDAS = {"20261021000000_resumen_mensual_pagos.sql"}
ESQUEMAS_REQUERIDOS = ["auth", "storage", "vault"]


def revisar(carpeta: Path = CARPETA) -> tuple[list[Path], list[str]]:
    """Orden y dependencias, solo leyendo los archivos: cada clave foránea apunta a una tabla creada antes."""
    archivos = sorted(carpeta.glob("*.sql"))
    problemas = []
    versiones = set()
    creadas = set()
    for a in archivos:
        if a.name in PROHIBIDAS:
            problemas.append(f"{a.name}: no debe aplicarse")
        m = re.fullmatch(r"(\d{14})_[a-z0-9_]+\.sql", a.name)
        if not m:
            problemas.append(f"{a.name}: nombre inválido")
            continue
        if m[1] in versiones:
            problemas.append(f"{a.name}: versión repetida")
        versiones.add(m[1])
        sql = a.read_text()
        creadas |= set(re.findall(r"create table (?:if not exists )?public\.(\w+)", sql))
        for tabla in re.findall(r"references public\.(\w+)", sql):
            if tabla not in creadas:
                problemas.append(f"{a.name}: referencia a {tabla}, que todavía no existe")
    return archivos, sorted(set(problemas))


def requisitos(archivos: list[Path]) -> list[str]:
    """Lo que las migraciones necesitan de Supabase, además de las tablas propias."""
    texto = "\n".join(a.read_text() for a in archivos)
    return [e for e in ESQUEMAS_REQUERIDOS if re.search(rf"\b{e}\.", texto)]


def exigir_destino(dsn: str, url: str | None, proyecto: str | None) -> None:
    entorno.exigir_conexion_prueba(dsn, proyecto)
    if not entorno.es_local(dsn):
        entorno.exigir_proyecto_prueba(url, proyecto)
        if entorno.ref_de_url(url) != entorno.ref_de_conexion(dsn):
            raise entorno.DestinoNoPermitido("La URL de la API y la conexión son de proyectos distintos.")


def _uno(cur, sql, *args):
    cur.execute(sql, args or None)
    return cur.fetchone()[0]


def aplicadas(cur) -> set:
    if not _uno(cur, "select to_regclass('supabase_migrations.schema_migrations') is not null"):
        return set()
    cur.execute("select version from supabase_migrations.schema_migrations")
    return {r[0] for r in cur.fetchall()}


def comprobar_base(cur, archivos: list[Path], ya: set) -> None:
    """Corta si la base no es el proyecto vacío esperado: última barrera si todo lo anterior fallara."""
    for esquema in requisitos(archivos):
        if not _uno(cur, "select exists (select 1 from pg_namespace where nspname = %s)", esquema):
            raise entorno.DestinoNoPermitido(f"Falta el esquema {esquema}: no parece un proyecto de Supabase.")
    if ya - {a.name[:14] for a in archivos}:
        raise entorno.DestinoNoPermitido("La base tiene migraciones que no están en este repo: no se toca.")
    tablas = _uno(cur, "select count(*) from information_schema.tables where table_schema = 'public'")
    if tablas and not ya:
        raise entorno.DestinoNoPermitido("La base ya tiene tablas: tiene que ser un proyecto vacío.")
    if ya and _uno(cur, "select to_regclass('public.profiles') is not null"):
        if _uno(cur, "select count(*) from public.profiles where mail not like '%@prueba.invalid'"):
            raise entorno.DestinoNoPermitido("La base tiene cuentas que no son de prueba: no se toca.")


def aplicar(conn, archivos: list[Path]) -> list[str]:
    """Cada archivo en su propia transacción; si uno falla se detiene ahí y se puede retomar."""
    hechas = []
    conn.autocommit = True
    with conn.cursor() as cur:
        ya = aplicadas(cur)
        comprobar_base(cur, archivos, ya)  # antes de escribir nada
        cur.execute("create schema if not exists supabase_migrations")
        cur.execute("create table if not exists supabase_migrations.schema_migrations (version text primary key, name text)")
    conn.autocommit = False
    for a in archivos:
        version, nombre = a.name[:14], a.stem[15:]
        if version in ya:
            continue
        try:
            with conn.cursor() as cur:
                cur.execute(a.read_text())
                cur.execute("insert into supabase_migrations.schema_migrations (version, name) values (%s, %s)", (version, nombre))
            conn.commit()
        except Exception:
            conn.rollback()
            print(f"Falló {a.name}: se detiene acá.", file=sys.stderr)
            raise
        hechas.append(a.name)
    return hechas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--proyecto")
    parser.add_argument("--env", default=str(RAIZ / ".env.videos-prueba"))
    args = parser.parse_args()

    archivos, problemas = revisar()
    print(f"{len(archivos)} migraciones, de {archivos[0].name[:14]} a {archivos[-1].name[:14]}")
    print("Necesitan de Supabase: " + ", ".join(requisitos(archivos)))
    if problemas:
        raise SystemExit("Problemas:\n- " + "\n- ".join(problemas))
    print("Orden y dependencias: OK")
    if not args.aplicar:
        print("No se conectó a nada. Para aplicar: --aplicar --proyecto <ref>")
        return

    from videos_prueba_datos import cargar_env
    cargar_env(args.env)
    dsn = os.environ.get("VIDEOS_PRUEBA_DB_URL")
    if not dsn:
        raise SystemExit("Falta VIDEOS_PRUEBA_DB_URL.")
    try:
        exigir_destino(dsn, os.environ.get("SUPABASE_URL"), args.proyecto)
    except entorno.DestinoNoPermitido as e:
        raise SystemExit(str(e))
    if not entorno.es_local(dsn) and input(f"Escribí de nuevo el proyecto de PRUEBA ({args.proyecto}): ").strip() != args.proyecto:
        raise SystemExit("No coincide: no se hizo nada.")
    import psycopg2
    with psycopg2.connect(dsn, sslmode="disable" if entorno.es_local(dsn) else "require") as conn:
        try:
            hechas = aplicar(conn, archivos)
        except entorno.DestinoNoPermitido as e:
            raise SystemExit(str(e))
    print(f"Aplicadas: {len(hechas)}")


if __name__ == "__main__":
    main()
