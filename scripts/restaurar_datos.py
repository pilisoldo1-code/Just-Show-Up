"""Restauración de un respaldo (datos.json de descargar_datos.py), en dos partes:

1. Cuentas (por la API, con la clave de administrador): mismo id, mail y metadatos; contraseña nueva al azar
   (Supabase no exporta contraseñas: cada persona usa «Olvidé mi contraseña»). Las que ya existen no se tocan.
2. Datos: genera `restaurar_datos.sql` en la carpeta del respaldo (con datos personales: queda solo ahí). Carga
   perfiles y las tablas de la app conservando sus ids (OVERRIDING SYSTEM VALUE, que la API no permite): agrega lo que falta y
   vuelve las filas del respaldo a sus valores (no borra nada), y deja los contadores como estaban. Contra un Supabase local se ejecuta
   solo; para producción lo ejecuta Julieta en el editor SQL del panel de Supabase (gratis), así ninguna
   credencial pasa por el chat.
Al final compara fila por fila con el respaldo.

Por seguridad, contra cualquier Supabase que no sea local exige `--confirmo-produccion` (y la autorización de
Julieta), y aun así solo hace la parte 1 y genera el SQL: nunca lo ejecuta en producción.

Uso:  python scripts/restaurar_datos.py "<carpeta del respaldo>" [--confirmo-produccion]
"""
import json
import os
import re
import secrets
import sys

from supabase import create_client

ORDEN = ["tipo_clase", "horario", "clase", "bloqueo", "reserva", "listadeespera", "asistencia", "resumen_mensual",
         "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion", "mp_configuracion", "mp_intento", "mp_evento"]
CLAVE = {"pago_anulacion": "id_pago"}


def es_local(url: str) -> bool:
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", url))


def restaurar_cuentas(sb, datos: dict) -> int:
    existentes = {u.id for u in sb.auth.admin.list_users(per_page=1000)}
    nuevas = 0
    for u in datos["auth_users"]:
        if u["id"] not in existentes:
            sb.auth.admin.create_user({"id": u["id"], "email": u["email"], "password": secrets.token_urlsafe(24),
                                       "email_confirm": True, "user_metadata": u.get("user_metadata") or {}})
            nuevas += 1
    return nuevas


def _literal(filas: list) -> str:
    texto = json.dumps(filas, ensure_ascii=False, default=str)
    etiqueta = "respaldo_" + secrets.token_hex(4)
    assert f"${etiqueta}$" not in texto
    return f"${etiqueta}${texto}${etiqueta}$::jsonb"


def generar_sql(datos: dict, contador: dict) -> str:
    partes = ["-- Restauración del respaldo: agrega lo que falta y deja las filas del respaldo con sus valores (no borra nada). Todo o nada.", "begin;"]
    perfiles = datos["profiles"]
    if perfiles:
        cols = list(perfiles[0])
        asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
        partes.append(f"insert into public.profiles ({', '.join(cols)}) select {', '.join(cols)} "
                      f"from jsonb_populate_recordset(null::public.profiles, {_literal(perfiles)}) "
                      f"on conflict (id) do update set {asignaciones};")
    for t in ORDEN:
        filas = datos.get(t) or []
        if filas:
            cols = list(filas[0])
            asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("id", CLAVE.get(t, "id")))
            partes.append(f"insert into public.{t} ({', '.join(cols)}) overriding system value select {', '.join(cols)} "
                          f"from jsonb_populate_recordset(null::public.{t}, {_literal(filas)}) "
                          f"on conflict on constraint {t}_pkey do update set {asignaciones};")
    for t in ["profiles"] + ORDEN:
        partes.append(f"""do $$ declare c text; begin
  for c in select a.attname from pg_attribute a where a.attrelid = 'public.{t}'::regclass and a.attidentity <> '' loop
    execute format('select setval(pg_get_serial_sequence(%L, %L), coalesce((select max(%I) from public.{t}), 0) + 1, false)',
                   'public.{t}', c, c);
  end loop; end $$;""")
    partes.append(f"select setval('public.factura_numero_seq', {int(contador['last_value'])}, {str(bool(contador['is_called'])).lower()});")
    partes.append("commit;")
    return "\n".join(partes) + "\n"


def comparar(sb, datos: dict) -> dict:
    diferencias = {}
    for t in ["profiles"] + ORDEN:
        clave = CLAVE.get(t, "id")
        actuales = {str(f[clave]): f for f in sb.table(t).select("*").execute().data}
        for f in datos.get(t) or []:
            a = actuales.get(str(f[clave]))
            distintas = [c for c, v in f.items() if a is None or json.dumps(a.get(c), default=str) != json.dumps(v, default=str)]
            if distintas:
                diferencias.setdefault(t, []).append((f[clave], distintas[:3]))
    return diferencias


if __name__ == "__main__":
    carpeta = sys.argv[1]
    url, clave = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    local = es_local(url)
    if not local and "--confirmo-produccion" not in sys.argv:
        raise SystemExit("La URL no es local: para producción hace falta --confirmo-produccion (y la autorización de Julieta).")
    datos = json.load(open(os.path.join(carpeta, "datos.json")))
    conteos = json.load(open(os.path.join(carpeta, "conteos_produccion.json")))
    sb = create_client(url, clave)
    print(f"cuentas creadas: {restaurar_cuentas(sb, datos)}")
    ruta = os.path.join(carpeta, "restaurar_datos.sql")
    with open(ruta, "w") as f:
        f.write(generar_sql(datos, conteos["factura_numero_seq"]))
    os.chmod(ruta, 0o600)
    if not local:
        print(f"SQL generado en {ruta}: ejecutarlo en el editor SQL del panel de Supabase (no se ejecuta desde acá).")
        sys.exit(0)
    import psycopg2
    with psycopg2.connect(os.environ["DB_URL"]) as c, c.cursor() as cur:
        cur.execute(open(ruta).read())
    dif = comparar(sb, datos)
    print("Restauración completa: todas las filas iguales al respaldo." if not dif else {t: v[:2] for t, v in dif.items()})
