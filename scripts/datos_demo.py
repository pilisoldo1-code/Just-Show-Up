"""Carga (y borra) datos de demostración para mostrar el sistema.

Todo lo que crea pertenece a alumnas de demo con mail @demo.invalid: borrarlas
elimina sus reservas, listas de espera y asistencias sin tocar nada más (ni la
profesora, ni las cuentas de prueba, ni horarios o clases).

Una de ellas, sofia@demo.invalid, puede iniciar sesión: su contraseña se guarda
en .env.cuentas-prueba (ignorado por git).

Uso:
    python scripts/datos_demo.py crear
    python scripts/datos_demo.py borrar
"""

import os
import random
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from app import auth, clases  # noqa: E402

DOMINIO = "@demo.invalid"
ARCHIVO_CUENTAS = RAIZ / ".env.cuentas-prueba"
MAIL_LOGIN = "sofia" + DOMINIO
ALUMNAS = [
    ("sofia", "Sofía", "Pereira", "099111222", "1995-03-14", "2027-05-01"),
    ("valentina", "Valentina", "Rodríguez", "098234567", "1990-07-22", "2027-02-10"),
    ("camila", "Camila", "Fernández", "091345678", "1998-11-03", "2026-12-20"),
    ("lucia", "Lucía", "Martínez", "094456789", "1987-01-30", "2027-08-15"),
    ("florencia", "Florencia", "Silva", "099567890", "1993-05-18", "2027-03-01"),
    ("agustina", "Agustina", "López", "092678901", "2000-09-09", "2027-06-30"),
    ("martina", "Martina", "Sosa", "095789012", "1996-12-12", "2027-01-25"),
    ("paula", "Paula", "Castro", "098890123", "1985-04-27", "2027-10-05"),
]


def _admin():
    if not os.environ.get("SUPABASE_SERVICE_KEY"):
        raise SystemExit("Falta SUPABASE_SERVICE_KEY en el archivo .env.")
    return auth.cliente_admin()


def _ids_demo(admin) -> list[str]:
    return [p["id"] for p in admin.table("profiles").select("id").ilike("mail", f"%{DOMINIO}").execute().data]


def _guardar_contrasena(contrasena: str | None) -> None:
    lineas = ARCHIVO_CUENTAS.read_text().splitlines() if ARCHIVO_CUENTAS.exists() else []
    lineas = [l for l in lineas if not l.startswith(MAIL_LOGIN)]
    if contrasena:
        lineas.append(f"{MAIL_LOGIN}  {contrasena}")
    ARCHIVO_CUENTAS.write_text("\n".join(lineas) + "\n")


def _sincronizar(admin, ids_clase: set[int]) -> None:
    for cid in ids_clase:
        n = admin.table("reserva").select("id", count="exact").eq("id_clase", cid).eq("estado", "confirmada").execute().count
        admin.table("clase").update({"cantidad_inscriptas": n}).eq("id", cid).execute()


def crear() -> None:
    admin = _admin()
    if _ids_demo(admin):
        raise SystemExit("Ya hay datos de demo cargados. Corré primero: python scripts/datos_demo.py borrar")

    rnd = random.Random(7)
    ids = {}
    contrasena = "Demo-" + secrets.token_urlsafe(8)
    for usuario, nombre, apellido, tel, nac, carne in ALUMNAS:
        datos = {"email": usuario + DOMINIO, "email_confirm": True,
                 "password": contrasena if usuario == "sofia" else secrets.token_urlsafe(16),
                 "user_metadata": {"nombre": nombre, "apellido": apellido}}
        uid = admin.auth.admin.create_user(datos).user.id
        admin.table("profiles").update({"telefono": tel, "fecha_nacimiento": nac,
                                        "fecha_vencimiento_carne_salud": carne}).eq("id", uid).execute()
        ids[usuario] = uid
    _guardar_contrasena(contrasena)
    sofia, otras = ids["sofia"], [ids[a[0]] for a in ALUMNAS[1:]]

    ahora = clases.ahora()
    hoy = ahora.date()
    filas = (
        admin.table("clase").select("*, horario(cupo_max, activo)").eq("estado", "programada")
        .gte("fecha", (hoy - timedelta(days=9)).isoformat())
        .lte("fecha", (hoy + timedelta(days=16)).isoformat())
        .order("fecha").order("hora_inicio").execute().data
    )
    filas = [c for c in filas if c["horario"] and c["horario"]["activo"]]
    ocupadas = {}
    for r in admin.table("reserva").select("id_clase").eq("estado", "confirmada").in_("id_clase", [c["id"] for c in filas]).execute().data:
        ocupadas[r["id_clase"]] = ocupadas.get(r["id_clase"], 0) + 1

    def inicio(c):
        return datetime.combine(datetime.fromisoformat(c["fecha"]).date(),
                                datetime.strptime(c["hora_inicio"][:5], "%H:%M").time(), tzinfo=clases.TZ)

    pasadas = [c for c in filas if inicio(c) <= ahora]
    futuras = [c for c in filas if inicio(c) > ahora]
    reservas, asistencias, espera = [], [], []

    def reservar(c, alumna, estado="confirmada", asistencia=None):
        cuando = inicio(c) - timedelta(days=rnd.randint(1, 4), hours=rnd.randint(0, 10))
        fila = {"id_alumna": alumna, "id_clase": c["id"], "estado": estado, "fecha_reserva": cuando.isoformat()}
        if estado == "cancelada":
            fila["fecha_cancelacion"] = (cuando + timedelta(hours=5)).isoformat()
        reservas.append((fila, asistencia))

    # Clases pasadas: varias alumnas, casi todas presentes. Sofía con los 4 casos del historial.
    casos_sofia = ["presente", "presente", "ausente", "presente", None, "cancelada", "presente"]
    for i, c in enumerate(pasadas):
        libres = c["horario"]["cupo_max"] - ocupadas.get(c["id"], 0) - 1
        for a in rnd.sample(otras, min(libres, rnd.randint(3, 6))):
            reservar(c, a, asistencia="presente" if rnd.random() < 0.85 else "ausente")
        if i % 2 == 0 and casos_sofia:
            caso = casos_sofia.pop(0)
            if caso == "cancelada":
                reservar(c, sofia, estado="cancelada")
            else:
                reservar(c, sofia, asistencia=caso)

    # Clases futuras: algunas reservas; una clase completa con Sofía en lista de espera.
    vacias = [c for c in futuras if not ocupadas.get(c["id"])]
    completa = vacias[min(3, len(vacias) - 1)] if vacias else None
    sofia_en = {c["id"] for c in futuras[1:8:3]}
    for c in futuras:
        cupo = c["horario"]["cupo_max"] - ocupadas.get(c["id"], 0)
        if completa and c["id"] == completa["id"]:
            for a in otras[:cupo]:
                reservar(c, a)
            espera.append({"id_alumna": sofia, "id_clase": c["id"], "posicion": 1})
            continue
        n = rnd.randint(1, min(4, cupo - 2))
        for a in rnd.sample(otras, n):
            reservar(c, a)
        if c["id"] in sofia_en:
            reservar(c, sofia)

    creadas = admin.table("reserva").insert([f for f, _ in reservas]).execute().data
    for fila, (_, estado) in zip(creadas, reservas):
        if estado:
            asistencias.append({"id_reserva": fila["id"], "estado": estado})
    if asistencias:
        admin.table("asistencia").insert(asistencias).execute()
    if espera:
        admin.table("listadeespera").insert(espera).execute()
    _sincronizar(admin, {f["id_clase"] for f, _ in reservas})

    print(f"Listo: {len(ids)} alumnas de demo, {len(creadas)} reservas, {len(asistencias)} asistencias, "
          f"{len(espera)} en lista de espera.")
    if completa:
        print(f"Clase completa (con Sofía en lista de espera): {completa['fecha']} {completa['hora_inicio'][:5]}")
    print(f"Sofía puede iniciar sesión: {MAIL_LOGIN} (contraseña en .env.cuentas-prueba)")


def borrar() -> None:
    admin = _admin()
    ids = _ids_demo(admin)
    if not ids:
        print("No hay datos de demo cargados.")
        _guardar_contrasena(None)
        return
    reservas = admin.table("reserva").select("id, id_clase").in_("id_alumna", ids).execute().data
    rids = [r["id"] for r in reservas]
    for i in range(0, len(rids), 200):
        admin.table("asistencia").delete().in_("id_reserva", rids[i:i + 200]).execute()
    admin.table("listadeespera").delete().in_("id_alumna", ids).execute()
    admin.table("reserva").delete().in_("id_alumna", ids).execute()
    for uid in ids:
        admin.auth.admin.delete_user(uid)
    _sincronizar(admin, {r["id_clase"] for r in reservas})
    _guardar_contrasena(None)
    print(f"Borrado: {len(ids)} alumnas de demo y {len(rids)} reservas (con sus asistencias y listas de espera).")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else ""
    if accion == "crear":
        crear()
    elif accion == "borrar":
        borrar()
    else:
        raise SystemExit("Uso: python scripts/datos_demo.py crear | borrar")
