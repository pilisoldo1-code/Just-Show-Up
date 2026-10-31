"""Verificación de SOLO LECTURA de producción durante el despliegue de multiempresa (etapas B y D).
Compara cada fila con el respaldo (columnas originales), revisa las columnas y tablas nuevas y prueba que la API
resuelva las consultas anidadas que usa theta (sin PGRST201). Solo hace SELECT con la clave de administrador; no
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


actual = {t: todas(t) for t in TABLAS}
for t in TABLAS:
    clave = CLAVE.get(t, "id")
    ahora = {str(f[clave]): f for f in actual[t]}
    distintas = [f[clave] for f in respaldo[t]
                 if str(f[clave]) not in ahora
                 or any(json.dumps(ahora[str(f[clave])].get(c), default=str) != json.dumps(v, default=str) for c, v in f.items())]
    ok(f"{t}: {len(respaldo[t])} filas del respaldo intactas (columnas originales)", not distintas
       and len(ahora) == len(respaldo[t]), f"distintas o faltantes: {len(distintas)}, ahora {len(ahora)}")

empresas = sb.table("empresa").select("id, nombre, codigo, activa, mp_habilitado, ultimo_numero_factura").execute().data
ok("una sola empresa: Just Show Up / JSU, activa, Mercado Pago habilitado, contador 0",
   len(empresas) == 1 and (empresas[0]["nombre"], empresas[0]["codigo"], empresas[0]["activa"], empresas[0]["mp_habilitado"],
                           empresas[0]["ultimo_numero_factura"]) == ("Just Show Up", "JSU", True, True, 0), empresas)
jsu = empresas[0]["id"] if empresas else None
for t in TABLAS[1:-2] + ["mp_intento"]:
    sin = [f for f in actual[t] if f.get("id_empresa") != jsu]
    ok(f"{t}: todas las filas en la Empresa 1", not sin, len(sin))
membresias = sb.table("membresia").select("id_usuario, id_empresa, es_admin, es_profesora, es_alumna, activa").execute().data
por_usuario = {m["id_usuario"]: m for m in membresias}
roles_ok = all(
    p["id"] in por_usuario and por_usuario[p["id"]]["id_empresa"] == jsu and por_usuario[p["id"]]["activa"]
    and (por_usuario[p["id"]]["es_admin"], por_usuario[p["id"]]["es_profesora"], por_usuario[p["id"]]["es_alumna"])
    == ((True, True, False) if p["rol"] == "profesora" else (False, False, True))
    for p in respaldo["profiles"])
ok(f"una membresía por persona según su rol ({len(membresias)} de {len(respaldo['profiles'])})",
   roles_ok and len(membresias) == len(respaldo["profiles"]))
profesoras = [p["id"] for p in respaldo["profiles"] if p["rol"] == "profesora"]
ok("cada clase y cada horario tienen su profesora", all(c.get("id_profesora") in profesoras for c in actual["clase"])
   and all(h.get("id_profesora") in profesoras for h in actual["horario"]))
ok("las líneas de factura tienen la profesora", all(d.get("profesora_nombre") for d in actual["resumen_mensual_detalle"]))
ok("ninguna persona quedó como superadministradora", not [p for p in actual["profiles"] if p.get("es_superadmin")])

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
