"""Restauración de un respaldo (datos.json de descargar_datos.py), en dos partes:

0. Empresas que falten (SQL `restaurar_empresas.sql`): primero, porque el alta de cada cuenta crea su membresía en
   la empresa de sus metadatos. Local: se ejecuta solo; producción: se pide ejecutarlo en el editor SQL y volver a correr.
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

ANTES = ["empresa"]  # multiempresa: antes de los perfiles (profiles.empresa_activa la referencia)
# membresia: al recrear una cuenta, la base ya le crea una membresía (trigger de alta); se restaura por persona y
# empresa, sin su id (que nadie referencia)
POR_PERSONA = {"membresia": ("id_usuario", "id_empresa")}
ORDEN = ["membresia", "tipo_clase", "horario", "clase", "bloqueo", "baja_docente", "reserva", "listadeespera", "asistencia",
         "asistencia_historial", "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion",
         "mp_configuracion", "mp_intento", "mp_evento"]
# Lo que el respaldo trae y NO se carga, a propósito (cualquier otra tabla del respaldo sin restaurar corta el script):
# - auth_users se recrean por la API; storage_tipos_clase son nombres de archivos (las imágenes van aparte).
# - _tres_roles_respaldo (en datos_complementarios.json) guarda ids de membresías, que la restauración no conserva
#   (se recrean por persona y empresa): solo servía para la vuelta atrás de «tres roles».
# - sesion_empresa no se respalda: son las sesiones abiertas, que no sobreviven a recrear las cuentas.
NO_SE_CARGAN = {"auth_users", "storage_tipos_clase", "_tres_roles_respaldo"}
# Videos On Demand: solo si el respaldo las trae (los anteriores a la migración no). video_agente queda afuera a propósito:
# guarda huellas de tokens, que se vuelven a crear. Las filas no recuperan los archivos de Storage.
VIDEOS = ["video_programacion", "grabacion_independiente", "video", "video_reproduccion"]
CLAVE = {"pago_anulacion": "id_pago"}


def es_local(url: str) -> bool:
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", url))


def tablas_de(datos: dict) -> list:
    return ORDEN + [t for t in VIDEOS if t in datos]


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


def generar_sql_empresas(datos: dict) -> str:
    """Las empresas, ANTES de recrear las cuentas: el alta de cada cuenta crea su membresía en la empresa de sus
    metadatos, y con más de una empresa esa empresa tiene que existir (si no, Supabase responde «Database error
    creating new user»). Mismo upsert que en generar_sql, en su propia transacción."""
    filas = datos.get("empresa") or []
    if not filas:
        return ""
    cols = list(filas[0])
    asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    return ("-- Restauración del respaldo, parte 0: empresas (antes de las cuentas). No borra nada.\nbegin;\n"
            f"insert into public.empresa ({', '.join(cols)}) overriding system value select {', '.join(cols)} "
            f"from jsonb_populate_recordset(null::public.empresa, {_literal(filas)}) "
            f"on conflict on constraint empresa_pkey do update set {asignaciones};\n"
            "select setval(pg_get_serial_sequence('public.empresa', 'id'), coalesce((select max(id) from public.empresa), 0) + 1, false);\n"
            "commit;\n")


def empresas_faltantes(sb, datos: dict) -> list:
    existentes = {e["id"] for e in sb.table("empresa").select("id").execute().data}
    return [e["id"] for e in datos.get("empresa") or [] if e["id"] not in existentes]


def generar_sql(datos: dict, contador: dict) -> str:
    partes = ["-- Restauración del respaldo: agrega lo que falta y deja las filas del respaldo con sus valores (no borra nada). Todo o nada.", "begin;"]
    for t in ANTES:
        filas = datos.get(t) or []
        if filas:
            cols = list(filas[0])
            asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
            partes.append(f"insert into public.{t} ({', '.join(cols)}) overriding system value select {', '.join(cols)} "
                          f"from jsonb_populate_recordset(null::public.{t}, {_literal(filas)}) "
                          f"on conflict on constraint {t}_pkey do update set {asignaciones};")
    perfiles = datos["profiles"]
    if perfiles:
        cols = list(perfiles[0])
        asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
        partes.append(f"insert into public.profiles ({', '.join(cols)}) select {', '.join(cols)} "
                      f"from jsonb_populate_recordset(null::public.profiles, {_literal(perfiles)}) "
                      f"on conflict (id) do update set {asignaciones};")
    for t in tablas_de(datos):
        filas = datos.get(t) or []
        if filas and t in POR_PERSONA:
            cols = [c for c in filas[0] if c != "id"]
            asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in POR_PERSONA[t])
            partes.append(f"insert into public.{t} ({', '.join(cols)}) select {', '.join(cols)} "
                          f"from jsonb_populate_recordset(null::public.{t}, {_literal(filas)}) "
                          f"on conflict ({', '.join(POR_PERSONA[t])}) do update set {asignaciones};")
        elif filas:
            cols = list(filas[0])
            asignaciones = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("id", CLAVE.get(t, "id")))
            partes.append(f"insert into public.{t} ({', '.join(cols)}) overriding system value select {', '.join(cols)} "
                          f"from jsonb_populate_recordset(null::public.{t}, {_literal(filas)}) "
                          f"on conflict on constraint {t}_pkey do update set {asignaciones};")
    for t in ANTES + ["profiles"] + tablas_de(datos):
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
    for t in ANTES + ["profiles"] + tablas_de(datos):
        if t not in datos:
            continue  # respaldo anterior a multiempresa
        def k(f):
            return "|".join(str(f[c]) for c in POR_PERSONA[t]) if t in POR_PERSONA else str(f[CLAVE.get(t, "id")])
        actuales = {k(f): f for f in sb.table(t).select("*").execute().data}
        for f in datos.get(t) or []:
            a = actuales.get(k(f))
            distintas = [c for c, v in f.items() if (c != "id" or t not in POR_PERSONA)
                         and (a is None or json.dumps(a.get(c), default=str) != json.dumps(v, default=str))]
            if distintas:
                diferencias.setdefault(t, []).append((k(f), distintas[:3]))
    return diferencias


if __name__ == "__main__":
    carpeta = sys.argv[1]
    url, clave = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    local = es_local(url)
    if not local and "--confirmo-produccion" not in sys.argv:
        raise SystemExit("La URL no es local: para producción hace falta --confirmo-produccion (y la autorización de Julieta).")
    datos = json.load(open(os.path.join(carpeta, "datos.json")))
    sin_restaurar = set(datos) - set(ANTES) - {"profiles"} - set(tablas_de(datos)) - NO_SE_CARGAN
    if sin_restaurar:
        raise SystemExit(f"El respaldo trae tablas que este script no restaura: {sorted(sin_restaurar)}. No se hizo nada.")
    conteos = json.load(open(os.path.join(carpeta, "conteos_produccion.json")))
    sb = create_client(url, clave)
    faltan = empresas_faltantes(sb, datos)
    if faltan:
        ruta_empresas = os.path.join(carpeta, "restaurar_empresas.sql")
        with open(ruta_empresas, "w") as f:
            f.write(generar_sql_empresas(datos))
        os.chmod(ruta_empresas, 0o600)
        if not local:
            raise SystemExit(f"Faltan las empresas {faltan}: ejecutar primero {ruta_empresas} en el editor SQL del panel "
                             "de Supabase y volver a correr este script. No se creó ninguna cuenta.")
        import psycopg2
        with psycopg2.connect(os.environ["DB_URL"]) as c, c.cursor() as cur:
            cur.execute(open(ruta_empresas).read())
        print(f"empresas restauradas antes de las cuentas: {faltan}")
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
