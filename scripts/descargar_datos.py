"""Respaldo de producción, SOLO LECTURA: todas las filas de las tablas de la app, las cuentas de Supabase Auth (id, correo y
metadatos: Supabase no exporta contraseñas) y las imágenes del bucket tipos-clase. Se restaura con
scripts/restaurar_datos.py (primero en un Supabase local para verificarlo).

El contador de facturas (`factura_numero_seq`) no se puede leer por la API: se lee aparte con
«select last_value, is_called from public.factura_numero_seq» (solo lectura) y se pasa con --secuencia.

No incluye, a propósito: video_agente (huellas de tokens del programa local: se vuelven a crear), sesion_empresa
(sesiones abiertas) ni los secretos de Vault (token de Mercado Pago de prueba y los de notificaciones: se vuelven a
guardar con sus scripts).

Uso:  python scripts/descargar_datos.py "<carpeta nueva del respaldo>" --secuencia <last_value>,<true|false>
Lee SUPABASE_URL y SUPABASE_SERVICE_KEY del .env de la carpeta principal del proyecto (o de RESPALDO_ENV).
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

PRODUCCION = "nspgafucytcjqkqnmkry"
TABLAS = ["empresa", "membresia", "profiles", "tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia",
          "bloqueo", "resumen_mensual", "resumen_mensual_detalle", "factura_historial", "pago", "pago_anulacion",
          "mp_intento", "mp_evento", "mp_configuracion", "asistencia_historial", "baja_docente"]
# Solo si existen en la base (los respaldos anteriores a cada módulo no las tienen)
OPCIONALES = ["grabacion_independiente", "video", "video_reproduccion", "video_programacion", "video_parametros",
              "notificacion_parametros", "notificacion_envio"]
ORDEN = {"pago_anulacion": "id_pago"}


def leer_tabla(a, t):
    filas, desde = [], 0
    while True:
        lote = a.table(t).select("*").order(ORDEN.get(t, "id")).range(desde, desde + 999).execute().data
        filas += lote
        if len(lote) < 1000:
            return filas
        desde += 1000


def main() -> int:
    if len(sys.argv) < 2 or "--secuencia" not in sys.argv:
        print(__doc__)
        return 1
    carpeta = os.path.abspath(sys.argv[1])
    valor, llamado = sys.argv[sys.argv.index("--secuencia") + 1].split(",")
    secuencia = {"last_value": int(valor), "is_called": llamado.strip().lower() == "true"}
    if os.path.exists(os.path.join(carpeta, "datos.json")):
        print("Esa carpeta ya tiene un respaldo: usar una nueva. No se hizo nada.")
        return 1
    load_dotenv(os.environ.get("RESPALDO_ENV", "/Users/julietaalvez/Desktop/OBLIGATORIO IET/.env"), override=True)
    if PRODUCCION not in os.environ.get("SUPABASE_URL", ""):
        print("El .env no apunta al proyecto de producción. No se hizo nada.")
        return 1
    a = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    os.makedirs(os.path.join(carpeta, "imagenes_tipos_clase"), mode=0o700, exist_ok=True)
    os.chmod(carpeta, 0o700)

    datos, faltantes = {}, []
    for t in TABLAS:
        datos[t] = leer_tabla(a, t)
    for t in OPCIONALES:
        try:
            datos[t] = leer_tabla(a, t)
        except Exception:
            faltantes.append(t)  # todavía no existe en la base
    usuarios = a.auth.admin.list_users(per_page=1000)
    datos["auth_users"] = [{"id": u.id, "email": u.email, "user_metadata": u.user_metadata,
                            "created_at": str(u.created_at)} for u in usuarios]
    archivos = [o for o in a.storage.from_("tipos-clase").list() if o.get("name") and o.get("id")]
    datos["storage_tipos_clase"] = [o["name"] for o in archivos]
    imagenes = {}
    for o in archivos:
        contenido = a.storage.from_("tipos-clase").download(o["name"])
        ruta = os.path.join(carpeta, "imagenes_tipos_clase", os.path.basename(o["name"]))
        with open(ruta, "wb") as f:
            f.write(contenido)
        os.chmod(ruta, 0o600)
        imagenes[o["name"]] = hashlib.sha256(contenido).hexdigest()

    destino = os.path.join(carpeta, "datos.json")
    with open(destino, "w") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1, default=str)
    os.chmod(destino, 0o600)
    conteos = {"factura_numero_seq": secuencia,
               "nota": f"secuencia leída de producción (solo lectura) el {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"}
    with open(os.path.join(carpeta, "conteos_produccion.json"), "w") as f:
        json.dump(conteos, f, indent=1)
    with open(destino, "rb") as f:
        huella = hashlib.sha256(f.read()).hexdigest()
    manifiesto = {"fecha_utc": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}", "datos_sha256": huella,
                  "filas": {k: len(v) for k, v in datos.items()}, "tablas_que_no_existen": faltantes,
                  "imagenes_sha256": imagenes}
    with open(os.path.join(carpeta, "manifiesto.json"), "w") as f:
        json.dump(manifiesto, f, ensure_ascii=False, indent=1)
    print(json.dumps(manifiesto["filas"], ensure_ascii=False))
    print("no existen (todavía):", faltantes, "| imágenes:", len(imagenes), "| datos.json sha256:", huella[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
