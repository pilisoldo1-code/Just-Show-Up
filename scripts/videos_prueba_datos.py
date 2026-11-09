"""Datos ficticios para probar Videos On Demand: dos empresas, administradora, profesora y alumnas de cada una, un tipo
de clase de 40 minutos, horarios, clases, la grabación activada, el token del programa local y el entorno de prueba.

SEGURIDAD: solo corre contra un Supabase local o contra el proyecto de prueba confirmado con --proyecto, y rechaza
producción. Lee únicamente el archivo de --env (nunca el .env). Las contraseñas y el token quedan en archivos ignorados
por git: no se muestran.

Uso:
    python scripts/videos_prueba_datos.py crear --fecha 2026-11-09 --hora 18:00 --proyecto <ref>
    python scripts/videos_prueba_datos.py clase --fecha 2026-11-10 --hora 18:00 --proyecto <ref>
    python scripts/videos_prueba_datos.py margen --menos-seg 2100 --proyecto <ref>   # solo para probar videos cortos
    python scripts/videos_prueba_datos.py margen --restaurar --proyecto <ref>
Opciones: --env <archivo> (por defecto .env.videos-prueba)
"""
import argparse
import json
import os
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import entorno  # noqa: E402

ARCHIVO_ENV = ".env.videos-prueba"
ARCHIVO_CUENTAS = ".env.cuentas-videos-prueba"
EMPRESAS = [("Prueba Videos Uno", "PRUA", "a"), ("Prueba Videos Dos", "PRUB", "b")]
ROLES = [("admin", "admin"), ("profesora", "profesora"), ("alumna1", "alumna"), ("alumna2", "alumna")]
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MARGEN_MENOS_SEG = 360


def cargar_env(archivo: str) -> None:
    """Solo ese archivo. Nunca el .env: es el que puede tener credenciales de producción."""
    from dotenv import dotenv_values
    ruta = Path(archivo)
    if ruta.name == ".env":
        raise SystemExit("Este script no usa el .env: indicá el archivo del entorno de prueba.")
    if not ruta.exists():
        raise SystemExit(f"No existe {archivo}.")
    for clave in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "VIDEOS_SUPABASE_URL", "VIDEOS_SUPABASE_SERVICE_KEY"):
        os.environ.pop(clave, None)
    os.environ.update({k: v for k, v in dotenv_values(ruta).items() if v})


class BaseSupabase:
    def __init__(self, proyecto):
        from app import auth
        entorno.exigir_proyecto_prueba(os.environ.get("SUPABASE_URL"), proyecto)
        self.cli = auth.cliente_admin()

    def crear_cuenta(self, mail, contrasena, meta):
        datos = {"email": mail, "password": contrasena, "email_confirm": True, "user_metadata": meta}
        return self.cli.auth.admin.create_user(datos).user.id

    def insertar(self, tabla, fila):
        return self.cli.table(tabla).insert(fila).execute().data[0]

    def leer(self, tabla, columnas, **eq):
        consulta = self.cli.table(tabla).select(columnas)
        for k, v in eq.items():
            consulta = consulta.eq(k, v)
        return consulta.execute().data

    def actualizar(self, tabla, cambios, **eq):
        consulta = self.cli.table(tabla).update(cambios)
        for k, v in eq.items():
            consulta = consulta.eq(k, v)
        consulta.execute()


def agregar_clase(db, id_empresa, id_profesora, id_tipo, fecha: date, hora: str) -> dict:
    horario = db.insertar("horario", {
        "id_empresa": id_empresa, "id_profesora": id_profesora, "id_tipo_clase": id_tipo, "dia_semana": DIAS[fecha.weekday()],
        "hora_inicio": hora, "duracion": 40, "cupo_max": 7, "precio": 1, "activo": True,
    })
    clase = db.insertar("clase", {
        "id_empresa": id_empresa, "id_profesora": id_profesora, "id_horario": horario["id"], "id_tipo_clase": id_tipo,
        "fecha": fecha.isoformat(), "hora_inicio": hora, "duracion": 40, "precio": 1, "cupo_max": 7,
    })
    db.insertar("video_programacion", {"id_empresa": id_empresa, "id_horario": horario["id"],
                                       "activa_desde": (fecha - timedelta(days=1)).isoformat()})
    return clase


def crear(db, fecha: date, hora: str) -> dict:
    """Devuelve lo que hay que guardar: contraseñas, token y entorno. No imprime ninguno."""
    if db.leer("empresa", "id", codigo=EMPRESAS[0][1]):
        raise SystemExit("Los datos de prueba ya existen. Para otra clase usá el comando «clase».")
    resultado = {"cuentas": {}, "entorno": f"{entorno.PREFIJO_PRUEBA}{secrets.token_hex(8)}", "token": secrets.token_urlsafe(32)}
    db.actualizar("video_parametros", {"entorno": resultado["entorno"]}, id=1)
    for nombre, codigo, letra in EMPRESAS:
        id_empresa = db.insertar("empresa", {"nombre": nombre, "codigo": codigo})["id"]
        ids = {}
        for cuenta, rol in ROLES:
            mail, contrasena = f"{cuenta}.{letra}@prueba.invalid", secrets.token_urlsafe(16)
            ids[cuenta] = db.crear_cuenta(mail, contrasena, {
                "nombre": cuenta.capitalize(), "apellido": f"Prueba {letra.upper()}", "id_empresa": id_empresa, "rol_empresa": rol,
            })
            resultado["cuentas"][mail] = contrasena
        tipo = db.insertar("tipo_clase", {"id_empresa": id_empresa, "nombre": "PRUEBA Clase de 40 minutos", "duracion": 40,
                                          "precio_presencial": 1, "cupo_max": 7})
        agregar_clase(db, id_empresa, ids["profesora"], tipo["id"], fecha, hora)
        if letra == "a":
            from app import videos
            db.insertar("video_agente", {"id_profesora": ids["profesora"], "nombre": "PRUEBA computadora de grabación",
                                         "token_hash": videos.huella_token(resultado["token"])})
    return resultado


def nueva_clase(db, fecha: date, hora: str) -> dict:
    empresa = db.leer("empresa", "id", codigo=EMPRESAS[0][1])
    if not empresa:
        raise SystemExit("Primero hay que correr «crear».")
    id_empresa = empresa[0]["id"]
    tipo = db.leer("tipo_clase", "id", id_empresa=id_empresa)[0]
    profesora = db.leer("horario", "id_profesora", id_empresa=id_empresa)[0]["id_profesora"]
    return agregar_clase(db, id_empresa, profesora, tipo["id"], fecha, hora)


def guardar(resultado: dict) -> None:
    cuentas = RAIZ / ARCHIVO_CUENTAS
    cuentas.write_text("".join(f"{m} {c}\n" for m, c in resultado["cuentas"].items()))
    cuentas.chmod(0o600)
    config = RAIZ / "agente" / "config.json"
    config.write_text(json.dumps({"url": "http://127.0.0.1:8000", "entorno": resultado["entorno"], "token": resultado["token"],
                                  "carpeta_zoom": "~/JSU-pruebas/zoom"}, indent=2) + "\n")
    config.chmod(0o600)
    print(f"Listo. Contraseñas en {ARCHIVO_CUENTAS}; token y entorno en agente/config.json.")
    print(f"Falta agregar a tu archivo de entorno: VIDEOS_ENTORNO={resultado['entorno']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accion", choices=["crear", "clase", "margen"])
    parser.add_argument("--env", default=str(RAIZ / ARCHIVO_ENV))
    parser.add_argument("--proyecto")
    parser.add_argument("--fecha", type=date.fromisoformat)
    parser.add_argument("--hora")
    parser.add_argument("--menos-seg", type=int)
    parser.add_argument("--restaurar", action="store_true")
    args = parser.parse_args()

    cargar_env(args.env)
    try:
        db = BaseSupabase(args.proyecto)
    except entorno.DestinoNoPermitido as e:
        raise SystemExit(str(e))
    if args.accion == "margen":
        db.actualizar("video_parametros", {"margen_menos_seg": MARGEN_MENOS_SEG if args.restaurar else args.menos_seg}, id=1)
        return
    if not args.fecha or not args.hora:
        raise SystemExit("Faltan --fecha y --hora.")
    if args.accion == "crear":
        guardar(crear(db, args.fecha, args.hora))
    else:
        nueva_clase(db, args.fecha, args.hora)
        print("Clase agregada y activada.")


if __name__ == "__main__":
    main()
