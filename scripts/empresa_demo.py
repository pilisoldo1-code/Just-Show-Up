"""Empresa 2 de demostración para multiempresa: «Pilates Centro» (PIL), con su administradora (que también
da clases), otra profesora, 5 alumnas @demo.invalid y alumna@prueba.invalid (que ya es alumna de la Empresa 1,
con la misma cuenta). Crea sus tipos de clase, horarios, clases de las últimas 3 semanas, esta y la próxima,
reservas y asistencias. No crea facturas ni pagos, y Mercado Pago queda apagado para esta empresa.

`borrar` elimina SOLO lo de esta empresa (y las cuentas que creó este script); la Empresa 1 no se toca.
Las contraseñas de las dos profesoras y de una alumna se guardan en .env.cuentas-prueba (ignorado por git).

ESCRIBE EN LA BASE DEL .env: correrlo contra Supabase de producción necesita la autorización de Julieta
(nombres definitivos confirmados). Se prueba completo en la base local:
    python tests/base_local/test_empresa_demo_base.py "<carpeta del respaldo antes de multiempresa>"

Uso:
    python scripts/empresa_demo.py crear
    python scripts/empresa_demo.py borrar
"""

import random
import secrets
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

NOMBRE, CODIGO = "Pilates Centro", "PIL"
ALUMNA_COMPARTIDA = "alumna@prueba.invalid"
ADMIN = ("admin.pilates@demo.invalid", "Lucía", "Bentancor", "admin_profesora")
PROFESORA = ("profe.pilates@demo.invalid", "Carolina", "Méndez", "profesora")
ALUMNAS = [
    ("rocio.pilates@demo.invalid", "Rocío", "Acosta", "099222333"),
    ("ines.pilates@demo.invalid", "Inés", "Duarte", "098333444"),
    ("julia.pilates@demo.invalid", "Julia", "Ferreira", "091444555"),
    ("noelia.pilates@demo.invalid", "Noelia", "Gómez", "094555666"),
    ("sabrina.pilates@demo.invalid", "Sabrina", "Viera", "092666777"),
]
CUENTAS = [ADMIN, PROFESORA] + [(m, n, a, "alumna") for m, n, a, _ in ALUMNAS]
CON_CONTRASENA = {ADMIN[0], PROFESORA[0], ALUMNAS[0][0]}
TIPOS = [("Pilates mat", "Pilates en colchoneta, todos los niveles.", 50, 900, 6),
         ("Pilates reformer", "Clase con máquina reformer, grupos reducidos.", 50, 1200, 4)]
# (día, hora, tipo, profesora): la administradora da los lunes y miércoles; la profesora, martes y jueves.
HORARIOS = [("lunes", "19:00", 0, "admin"), ("miercoles", "19:00", 1, "admin"),
            ("martes", "18:00", 0, "profe"), ("jueves", "18:00", 1, "profe")]
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
ARCHIVO_CUENTAS = RAIZ / ".env.cuentas-prueba"


class BaseSupabase:
    """Acceso con la clave de administrador del .env (ignora RLS: solo para este script)."""

    def __init__(self):
        import os
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
        if not os.environ.get("SUPABASE_SERVICE_KEY"):
            raise SystemExit("Falta SUPABASE_SERVICE_KEY en el archivo .env.")
        from app import auth
        self.cli = auth.cliente_admin()

    def crear_cuenta(self, mail, nombre, apellido, contrasena, meta):
        datos = {"email": mail, "password": contrasena, "email_confirm": True,
                 "user_metadata": {"nombre": nombre, "apellido": apellido, **meta}}
        return self.cli.auth.admin.create_user(datos).user.id

    def borrar_cuenta(self, uid):
        self.cli.auth.admin.delete_user(uid)

    def insertar(self, tabla, filas):
        return self.cli.table(tabla).insert(filas).execute().data

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

    def borrar(self, tabla, **eq):
        consulta = self.cli.table(tabla).delete()
        for k, v in eq.items():
            consulta = consulta.eq(k, v)
        consulta.execute()


def _empresa(db):
    filas = db.leer("empresa", "id, nombre, mp_habilitado", codigo=CODIGO)
    return filas[0] if filas else None


def crear(db, hoy: date, guardar_contrasenas=True) -> dict:
    if _empresa(db):
        raise SystemExit(f"Ya existe la empresa {CODIGO}. Corré primero: python scripts/empresa_demo.py borrar")
    for mail, *_ in CUENTAS:
        if db.leer("profiles", "id", mail=mail):
            raise SystemExit(f"Ya existe la cuenta {mail}: borrala antes de crear la empresa de demostración.")
    compartida = db.leer("profiles", "id", mail=ALUMNA_COMPARTIDA)
    if not compartida:
        raise SystemExit(f"No existe {ALUMNA_COMPARTIDA} (alumna de la Empresa 1).")

    e2 = db.insertar("empresa", [{"nombre": NOMBRE, "codigo": CODIGO, "mp_habilitado": False}])[0]["id"]
    ids, contrasenas = {}, {}
    for mail, nombre, apellido, rol in CUENTAS:
        contrasena = "Demo-" + secrets.token_urlsafe(8)
        if mail in CON_CONTRASENA:
            contrasenas[mail] = contrasena
        ids[mail] = db.crear_cuenta(mail, nombre, apellido, contrasena if mail in CON_CONTRASENA else secrets.token_urlsafe(16),
                                    {"id_empresa": e2, "rol_empresa": rol})
    for mail, _, _, tel in ALUMNAS:
        db.actualizar("profiles", {"telefono": tel}, id=ids[mail])
    ids[ALUMNA_COMPARTIDA] = compartida[0]["id"]
    db.insertar("membresia", [{"id_usuario": ids[ALUMNA_COMPARTIDA], "id_empresa": e2, "es_alumna": True}])

    tipos = [db.insertar("tipo_clase", [{"nombre": n, "descripcion": d, "duracion": dur, "precio_presencial": p,
                                          "cupo_max": cupo, "activo": True, "id_empresa": e2}])[0]
             for n, d, dur, p, cupo in TIPOS]
    profesoras = {"admin": ids[ADMIN[0]], "profe": ids[PROFESORA[0]]}
    horarios = []
    for dia, hora, t, quien in HORARIOS:
        h = db.insertar("horario", [{"dia_semana": dia, "hora_inicio": hora, "id_tipo_clase": tipos[t]["id"], "activo": True,
                                     "id_profesora": profesoras[quien], "id_empresa": e2}])[0]
        horarios.append((h, tipos[t]))

    # clases de las últimas 3 semanas (ya dictadas, con asistencia) y de las próximas 2 (con algunas reservas)
    rnd = random.Random(11)
    alumnas = [ids[m] for m, *_ in ALUMNAS] + [ids[ALUMNA_COMPARTIDA]]
    lunes = hoy - timedelta(days=hoy.weekday())
    resumen = {"clases": 0, "reservas": 0, "asistencias": 0}
    for semana in range(-3, 2):
        for h, tipo in horarios:
            fecha = lunes + timedelta(weeks=semana, days=DIAS.index(h["dia_semana"]))
            pasada = fecha < hoy
            if fecha == hoy:
                continue  # el día de hoy lo genera la app sola, sin reservas inventadas
            c = db.insertar("clase", [{"id_horario": h["id"], "fecha": fecha.isoformat(), "hora_inicio": h["hora_inicio"],
                                       "duracion": tipo["duracion"], "precio": tipo["precio_presencial"],
                                       "cupo_max": tipo["cupo_max"], "id_tipo_clase": tipo["id"], "estado": "programada",
                                       "id_profesora": h["id_profesora"], "id_empresa": e2}])[0]
            resumen["clases"] += 1
            elegidas = rnd.sample(alumnas, rnd.randint(2, tipo["cupo_max"]) if pasada else rnd.randint(1, tipo["cupo_max"] - 1))
            for a in elegidas:
                cuando = datetime.combine(fecha, datetime.min.time()) - timedelta(days=rnd.randint(1, 3))
                r = db.insertar("reserva", [{"id_alumna": a, "id_clase": c["id"], "estado": "confirmada",
                                             "fecha_reserva": cuando.isoformat(), "id_empresa": e2}])[0]
                resumen["reservas"] += 1
                if pasada:
                    db.insertar("asistencia", [{"id_reserva": r["id"], "id_empresa": e2,
                                                "estado": "presente" if rnd.random() < 0.8 else "ausente"}])
                    resumen["asistencias"] += 1
            db.actualizar("clase", {"cantidad_inscriptas": len(elegidas)}, id=c["id"])
    if guardar_contrasenas:
        _guardar_contrasenas(contrasenas)
    return {"empresa": e2, **resumen, "cuentas": len(CUENTAS), "contrasenas": contrasenas}


def borrar(db, guardar_contrasenas=True) -> dict:
    e = _empresa(db)
    if not e:
        raise SystemExit(f"No existe la empresa {CODIGO}: no hay nada para borrar.")
    e2 = e["id"]
    if db.leer("resumen_mensual", "id", id_empresa=e2) and any(f.get("numero") for f in db.leer("resumen_mensual", "numero", id_empresa=e2)):
        print("Aviso: la empresa tenía facturas confirmadas de demostración; se borran también.")
    for f in db.leer("resumen_mensual", "id", id_empresa=e2):
        db.actualizar("resumen_mensual", {"reemplaza_a": None}, id=f["id"])
    for tabla in ("mp_intento", "pago_anulacion", "pago", "factura_historial", "resumen_mensual_detalle", "resumen_mensual",
                  "asistencia", "listadeespera", "reserva", "bloqueo", "clase", "horario", "tipo_clase"):
        db.borrar(tabla, id_empresa=e2)
    mails = {m for m, *_ in CUENTAS}
    for p in db.leer("profiles", "id, mail, empresa_activa"):
        if p["mail"] in mails:
            db.borrar_cuenta(p["id"])
        elif p["empresa_activa"] == e2:
            db.actualizar("profiles", {"empresa_activa": None}, id=p["id"])
    db.borrar("membresia", id_empresa=e2)
    db.borrar("empresa", id=e2)
    if guardar_contrasenas:
        _guardar_contrasenas({})
    return {"empresa": e2}


def _guardar_contrasenas(contrasenas: dict) -> None:
    mails = {m for m, *_ in CUENTAS}
    lineas = ARCHIVO_CUENTAS.read_text().splitlines() if ARCHIVO_CUENTAS.exists() else []
    lineas = [l for l in lineas if l.split("  ")[0] not in mails]
    lineas += [f"{m}  {c}" for m, c in contrasenas.items()]
    ARCHIVO_CUENTAS.write_text("\n".join(lineas) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("crear", "borrar"):
        raise SystemExit(__doc__)
    from app import clases
    base = BaseSupabase()
    if sys.argv[1] == "crear":
        r = crear(base, clases.ahora().date())
        print(f"Empresa {NOMBRE} creada: {r['cuentas']} cuentas, {r['clases']} clases, {r['reservas']} reservas, "
              f"{r['asistencias']} asistencias. Contraseñas en .env.cuentas-prueba.")
    else:
        borrar(base)
        print(f"Empresa {NOMBRE} borrada (la Empresa 1 no se tocó).")
