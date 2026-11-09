"""Verificación de SOLO LECTURA de producción durante un despliegue. Funciona con una o varias empresas.
Compara cada fila del respaldo con la actual (columnas originales), revisa que Just Show Up no tenga filas nuevas
inesperadas, que cada fila esté en la misma empresa que su fila madre, que Mercado Pago siga solo en Just Show Up y que
la API resuelva las consultas anidadas que usa theta (sin PGRST201). Solo hace SELECT con la clave de administrador; no
escribe nada. Imprime solo cantidades y resultados (sin datos personales).

Uso:  python scripts/verificar_despliegue.py "<carpeta del respaldo>"
"""
import json
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(RAIZ, ".env"))
respaldo = json.load(open(os.path.join(sys.argv[1], "datos.json")))
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
TABLAS = ["profiles", "tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo",
          "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion", "mp_intento",
          "mp_evento", "mp_configuracion"]
CLAVE = {"pago_anulacion": "id_pago"}
# Videos On Demand: pueden no existir todavía, y sus filas cambian con el uso normal: no se comparan con el respaldo
VIDEOS = ["video_programacion", "video", "video_reproduccion"]
# columnas que cambian con el uso normal (no son datos de negocio): el lugar elegido al iniciar sesión
VARIABLES = {"profiles": {"empresa_activa"}}
# (tabla hija, columna, tabla madre): la fila hija tiene que estar en la misma empresa que su madre
MADRES = [("horario", "id_tipo_clase", "tipo_clase"), ("clase", "id_horario", "horario"), ("reserva", "id_clase", "clase"),
          ("listadeespera", "id_clase", "clase"), ("asistencia", "id_reserva", "reserva"),
          ("resumen_mensual_detalle", "id_resumen_mensual", "resumen_mensual"), ("pago", "id_resumen_mensual", "resumen_mensual")]
fallas = 0


def ok(nombre, cond, extra=""):
    global fallas
    fallas += not cond
    print(("OK   " if cond else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not cond else ""))


def todas(t, cols="*"):
    filas, desde = [], 0
    while True:
        lote = sb.table(t).select(cols).range(desde, desde + 999).execute().data
        filas += lote
        if len(lote) < 1000:
            return filas
        desde += 1000


empresas = todas("empresa")
jsu = next((e for e in empresas if e["codigo"] == "JSU"), None)
ok("Just Show Up (JSU) existe, activa y con Mercado Pago de prueba habilitado",
   jsu is not None and jsu["activa"] and jsu["mp_habilitado"])
otras = [e for e in empresas if e is not jsu]
ok(f"otras empresas: {len(otras)}, todas con Mercado Pago apagado", all(not e["mp_habilitado"] for e in otras),
   [e["codigo"] for e in otras if e["mp_habilitado"]])
if "empresa" in respaldo:
    antes = next((e for e in respaldo["empresa"] if e["codigo"] == "JSU"), {})
    distintas = [c for c, v in antes.items() if json.dumps(jsu.get(c), default=str) != json.dumps(v, default=str)]
    ok("Just Show Up: sus datos iguales al respaldo (nombre, código, contador, vigencia…)", not distintas, distintas)

ids_empresa = {e["id"] for e in empresas}
actual = {t: todas(t) for t in TABLAS}
for t in TABLAS:
    clave = CLAVE.get(t, "id")
    ahora = {str(f[clave]): f for f in actual[t]}
    ignorar = VARIABLES.get(t, set())
    distintas = [f[clave] for f in respaldo[t]
                 if str(f[clave]) not in ahora
                 or any(json.dumps(ahora[str(f[clave])].get(c), default=str) != json.dumps(v, default=str)
                        for c, v in f.items() if c not in ignorar)]
    previas = {str(f[clave]) for f in respaldo[t]}
    nuevas = [f for k, f in ahora.items() if k not in previas]
    nuevas_jsu = [f for f in nuevas if jsu and f.get("id_empresa") == jsu["id"]]
    ok(f"{t}: {len(respaldo[t])} filas del respaldo intactas; nuevas: {len(nuevas)} (Just Show Up: {len(nuevas_jsu)})",
       not distintas and (t == "profiles" or not nuevas_jsu), f"distintas o faltantes: {len(distintas)}, nuevas en JSU: {len(nuevas_jsu)}")
    if t not in ("profiles", "mp_evento", "mp_configuracion"):
        ok(f"{t}: toda fila pertenece a una empresa existente", all(f.get("id_empresa") in ids_empresa for f in actual[t]))

def opcional(t):
    """Filas de una tabla que puede no existir antes de la migración: None en ese caso; otro error corta."""
    try:
        return todas(t)
    except Exception as e:
        if any(c in str(e) for c in ("PGRST205", "42P01", "does not exist")):
            return None
        raise


videos = {t: opcional(t) for t in VIDEOS}
if videos["video"] is None:
    print("OK    Videos On Demand: tablas todavía no creadas, no se verifican")
else:
    for t in VIDEOS:
        ok(f"{t}: {len(videos[t])} filas, todas de una empresa existente", all(f["id_empresa"] in ids_empresa for f in videos[t]))
    actual.update(videos)
    MADRES = MADRES + [("video", "id_clase", "clase"), ("video_programacion", "id_horario", "horario"),
                       ("video_reproduccion", "id_video", "video")]
    grabaciones = opcional("grabacion_independiente")  # grabaciones independientes: solo si ya se aplicó su migración
    if grabaciones is not None:
        ok(f"grabacion_independiente: {len(grabaciones)} filas, todas de una empresa existente",
           all(f["id_empresa"] in ids_empresa for f in grabaciones))
        actual["grabacion_independiente"] = grabaciones
        MADRES = MADRES + [("video", "id_grabacion", "grabacion_independiente")]

por_id = {t: {f["id"]: f for f in actual[t]} for t in {m for _, _, m in MADRES}}
for hija, col, madre in MADRES:
    mal = [f["id"] for f in actual[hija] if f.get(col) in por_id[madre] and por_id[madre][f[col]]["id_empresa"] != f["id_empresa"]]
    ok(f"{hija} y su {madre}: siempre en la misma empresa", not mal, len(mal))

membresias = todas("membresia")
if "membresia" in respaldo:
    ahora = {(m["id_usuario"], m["id_empresa"]): m for m in membresias}
    cols = ("es_admin", "es_profesora", "es_alumna", "activa")
    distintas = [k for m in respaldo["membresia"] for k in [(m["id_usuario"], m["id_empresa"])]
                 if k not in ahora or any(ahora[k][c] != m[c] for c in cols)]
    ok(f"membresías del respaldo intactas ({len(respaldo['membresia'])})", not distintas, len(distintas))
docentes = {(m["id_usuario"], m["id_empresa"]) for m in membresias if m["es_admin"] or m["es_profesora"]}
ok("cada clase y cada horario tienen una docente de SU empresa",
   all((c.get("id_profesora"), c["id_empresa"]) in docentes for c in actual["clase"])
   and all((h.get("id_profesora"), h["id_empresa"]) in docentes for h in actual["horario"]))
ok("las líneas de factura tienen la profesora", all(d.get("profesora_nombre") for d in actual["resumen_mensual_detalle"]))
supers = {p["id"] for p in actual["profiles"] if p.get("es_superadmin")}
ok(f"superadministradoras: {len(supers)}, ninguna pertenece a una empresa",
   not [m for m in membresias if m["id_usuario"] in supers])

# las consultas anidadas que usa el código de theta (PGRST201 si hubiera relaciones duplicadas)
consultas = {
    "reserva → clase, profiles, asistencia": ("reserva", "id, clase(fecha, hora_inicio), profiles(nombre), asistencia(estado)"),
    "clase → horario, tipo_clase": ("clase", "id, horario(activo, dia_semana), tipo_clase(nombre, imagen_url)"),
    "asistencia → reserva → clase": ("asistencia", "estado, reserva(id_alumna, estado, profiles(nombre, apellido), clase(fecha, hora_inicio))"),
    "listadeespera → profiles": ("listadeespera", "posicion, estado, profiles(nombre, apellido)"),
    "horario → tipo_clase": ("horario", "*, tipo_clase(nombre, activo)"),
}
for nombre, (t, cols) in consultas.items():
    try:
        sb.table(t).select(cols).limit(5).execute()
        ok(f"API: consulta anidada {nombre}", True)
    except Exception as e:  # noqa: BLE001
        ok(f"API: consulta anidada {nombre}", False, str(e)[:160])
print(f"\n{'TODO OK' if not fallas else f'{fallas} FALLAS'}")
sys.exit(1 if fallas else 0)
