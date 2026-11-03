"""Empresa 2 de demostración para la defensa: «Pilates Centro» (PIL), con un escenario FIJO y reproducible (mismas
personas, horarios y reglas; las fechas se calculan desde el día en que se ejecuta).

Personas (todas ficticias, @demo.invalid salvo la alumna compartida):
- Lucía Bentancor, administradora (da Pilates mat los lunes y reformer los miércoles).
- Carolina Méndez, profesora común (mat los martes y reformer los jueves): no ve Facturación.
- Rocío Acosta (cumplidora), Inés Duarte (dos inasistencias: una el mes anterior, al 100 %, y otra este mes, al
  25 %), Julia Ferreira (canceló a tiempo una clase: no se cobra), Noelia Gómez (en lista de espera de una clase
  completa de la próxima semana), Sabrina Viera (su última clase de este mes quedó «sin registrar»).
- alumna@prueba.invalid: la misma cuenta que ya es alumna de Just Show Up (alumna compartida).

Crea tipos de clase, horarios, clases de las 4 semanas anteriores, esta y la próxima, reservas, asistencias, la lista
de espera y las facturas del mes anterior (confirmadas con numeración PIL; la de Rocío, pagada). El cobro del 25 %
rige desde el 1° del mes en curso (nunca hacia atrás). Mercado Pago queda apagado en esta empresa.

`crear` no hace nada si la empresa ya existe (no duplica). `borrar` elimina SOLO lo de esta empresa y las cuentas que
pertenecen únicamente a ella (siempre ficticias): la alumna compartida y Just Show Up no se tocan.

SEGURIDAD: por defecto solo corre contra un Supabase LOCAL (localhost). Contra producción hace falta `--produccion`,
que solo se usa con la autorización de Julieta. Las contraseñas se generan al azar y nunca se muestran: en local van a
`.env.cuentas-local` y en producción a `.env.cuentas-prueba` (los dos ignorados por git).

Pruebas: tests/base_local/test_empresa_demo_base.py y tests/supabase_local/prueba_empresa2_reales.py

Uso:
    python scripts/empresa_demo.py crear [--con-superadmin] [--produccion]
    python scripts/empresa_demo.py borrar [--produccion]
"""

import re
import secrets
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

NOMBRE, CODIGO = "Pilates Centro", "PIL"
ALUMNA_COMPARTIDA = "alumna@prueba.invalid"
SUPERADMIN = ("superadmin@prueba.invalid", "Administración", "General")
ADMIN = ("admin.pilates@demo.invalid", "Lucía", "Bentancor", "admin")
PROFESORA = ("profe.pilates@demo.invalid", "Carolina", "Méndez", "profesora")
ROCIO, INES, JULIA, NOELIA, SABRINA = (f"{n}.pilates@demo.invalid" for n in ("rocio", "ines", "julia", "noelia", "sabrina"))
ALUMNAS = [
    (ROCIO, "Rocío", "Acosta", "099222333"),
    (INES, "Inés", "Duarte", "098333444"),
    (JULIA, "Julia", "Ferreira", "091444555"),
    (NOELIA, "Noelia", "Gómez", "094555666"),
    (SABRINA, "Sabrina", "Viera", "092666777"),
]
CUENTAS = [ADMIN, PROFESORA] + [(m, n, a, "alumna") for m, n, a, _ in ALUMNAS]
CON_CONTRASENA = {ADMIN[0], PROFESORA[0], ROCIO, INES}
TIPOS = [("Pilates mat", "Pilates en colchoneta, todos los niveles.", 50, 900, 6),
         ("Pilates reformer", "Clase con máquina reformer, grupos reducidos.", 50, 1200, 4)]
# (día, hora, tipo, docente, alumnas que reservan cada semana)
HORARIOS = [
    ("lunes", "19:00", 0, "admin", [ROCIO, INES, JULIA, SABRINA]),
    ("martes", "18:00", 0, "profe", [ROCIO, NOELIA, SABRINA]),
    ("miercoles", "19:00", 1, "admin", [INES, JULIA, NOELIA]),
    ("jueves", "18:00", 1, "profe", [ROCIO, SABRINA]),
]
LLENA_PROXIMA = [ROCIO, INES, JULIA, SABRINA]  # el jueves de la próxima semana (reformer, cupo 4): completa
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
ARCHIVOS = {"local": RAIZ / ".env.cuentas-local", "produccion": RAIZ / ".env.cuentas-prueba"}


def es_local(url: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", str(url)))


class BaseSupabase:
    """Acceso con la clave de administrador del .env (ignora RLS: solo para este script)."""

    def __init__(self, produccion: bool = False):
        import os
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
        if not os.environ.get("SUPABASE_SERVICE_KEY"):
            raise SystemExit("Falta SUPABASE_SERVICE_KEY en el archivo .env.")
        from app import auth
        self.auth = auth
        self.cli = auth.cliente_admin()
        self.local = es_local(self.cli.supabase_url)
        if not self.local and not produccion:
            raise SystemExit("Este script solo corre contra un Supabase local. Contra producción hace falta --produccion "
                             "(y la autorización de Julieta).")

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

    def como(self, mail, contrasena, lugar):
        """Ejecuta funciones de la base con la sesión de una persona en un lugar (para facturar como la administradora)."""
        acceso, _ = self.auth.iniciar_sesion(mail, contrasena)
        if self.auth.entrar_a_empresa(acceso, lugar) is None:
            raise SystemExit(f"{mail} no pudo entrar a {lugar}.")
        cli = self.auth.cliente_con_token(acceso)
        return lambda funcion, params: cli.rpc(funcion, params).execute().data


def _empresa(db):
    filas = db.leer("empresa", "id, nombre, mp_habilitado", codigo=CODIGO)
    return filas[0] if filas else None


def fechas(hoy: date) -> dict:
    """Las clases del escenario: 4 semanas anteriores, esta y la próxima (sin el día de hoy, que lo genera la app)."""
    lunes = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)
    clases = []
    for semana in range(-4, 2):
        for i, (dia, hora, t, quien, alumnas) in enumerate(HORARIOS):
            f = lunes + timedelta(weeks=semana, days=DIAS.index(dia))
            if f == hoy:
                continue
            reservas = list(alumnas)
            if dia == "lunes" and f >= inicio_mes:
                reservas.append(ALUMNA_COMPARTIDA)  # la compartida toma los lunes desde este mes
            if semana == 1 and dia == "jueves":
                reservas = list(LLENA_PROXIMA)
            clases.append({"horario": i, "fecha": f, "pasada": f < hoy, "reservas": reservas, "semana": semana})
    pasadas = [c for c in clases if c["pasada"]]
    del_mes_ant = [c for c in pasadas if c["fecha"] < inicio_mes]
    del_mes = [c for c in pasadas if c["fecha"] >= inicio_mes]
    de = lambda mail, lista: [c for c in lista if mail in c["reservas"]]
    reglas = {
        "ines_ausente_100": min((c["fecha"], c["horario"]) for c in de(INES, del_mes_ant)) if de(INES, del_mes_ant) else None,
        "ines_ausente_25": max((c["fecha"], c["horario"]) for c in de(INES, del_mes)) if de(INES, del_mes) else None,
        "sabrina_sin_registrar": max((c["fecha"], c["horario"]) for c in de(SABRINA, del_mes)) if de(SABRINA, del_mes) else None,
        "julia_cancelada": max((c["fecha"], c["horario"]) for c in de(JULIA, del_mes_ant) if HORARIOS[c["horario"]][0] == "miercoles")
        if [c for c in de(JULIA, del_mes_ant) if HORARIOS[c["horario"]][0] == "miercoles"] else None,
    }
    return {"clases": clases, "reglas": reglas, "mes_anterior": (inicio_mes - timedelta(days=1)), "inicio_mes": inicio_mes}


def crear(db, hoy: date, guardar_contrasenas=True) -> dict:
    if _empresa(db):
        return {"ya_existia": True}
    for mail, *_ in CUENTAS:
        if db.leer("profiles", "id", mail=mail):
            raise SystemExit(f"Ya existe la cuenta {mail} sin la empresa {CODIGO}: revisá antes de crear (no se creó nada).")
    compartida = db.leer("profiles", "id", mail=ALUMNA_COMPARTIDA)
    if not compartida:
        raise SystemExit(f"No existe {ALUMNA_COMPARTIDA} (alumna de la Empresa 1): no se creó nada.")
    plan = fechas(hoy)

    e2 = db.insertar("empresa", [{"nombre": NOMBRE, "codigo": CODIGO, "mp_habilitado": False}])[0]["id"]
    ids, contrasenas = {}, {}
    for mail, nombre, apellido, rol in CUENTAS:
        contrasena = "Demo-" + secrets.token_urlsafe(9)
        if mail in CON_CONTRASENA:
            contrasenas[mail] = contrasena
        ids[mail] = db.crear_cuenta(mail, nombre, apellido, contrasena, {"id_empresa": e2, "rol_empresa": rol})
    for mail, _, _, tel in ALUMNAS:
        db.actualizar("profiles", {"telefono": tel}, id=ids[mail])
    ids[ALUMNA_COMPARTIDA] = compartida[0]["id"]
    db.insertar("membresia", [{"id_usuario": ids[ALUMNA_COMPARTIDA], "id_empresa": e2, "es_alumna": True}])

    tipos = [db.insertar("tipo_clase", [{"nombre": n, "descripcion": d, "duracion": dur, "precio_presencial": p,
                                          "cupo_max": cupo, "activo": True, "id_empresa": e2}])[0]
             for n, d, dur, p, cupo in TIPOS]
    docentes = {"admin": ids[ADMIN[0]], "profe": ids[PROFESORA[0]]}
    horarios = [db.insertar("horario", [{"dia_semana": dia, "hora_inicio": hora, "id_tipo_clase": tipos[t]["id"], "activo": True,
                                         "id_profesora": docentes[quien], "id_empresa": e2}])[0]
                for dia, hora, t, quien, _ in HORARIOS]

    reglas = plan["reglas"]
    resumen = {"clases": 0, "reservas": 0, "asistencias": 0, "canceladas": 0, "espera": 0}
    for c in plan["clases"]:
        h, tipo = horarios[c["horario"]], tipos[HORARIOS[c["horario"]][2]]
        clave = (c["fecha"], c["horario"])
        clase = db.insertar("clase", [{"id_horario": h["id"], "fecha": c["fecha"].isoformat(), "hora_inicio": h["hora_inicio"],
                                       "duracion": tipo["duracion"], "precio": tipo["precio_presencial"],
                                       "cupo_max": tipo["cupo_max"], "id_tipo_clase": tipo["id"], "estado": "programada",
                                       "id_profesora": h["id_profesora"], "id_empresa": e2}])[0]
        resumen["clases"] += 1
        confirmadas = 0
        for n, mail in enumerate(c["reservas"]):
            reservada = datetime.combine(c["fecha"], datetime.min.time()) - timedelta(days=2, hours=n)
            cancelada = mail == JULIA and clave == reglas["julia_cancelada"]
            fila = {"id_alumna": ids[mail], "id_clase": clase["id"], "id_empresa": e2, "fecha_reserva": reservada.isoformat(),
                    "estado": "cancelada" if cancelada else "confirmada"}
            if cancelada:
                fila["fecha_cancelacion"] = (reservada + timedelta(days=1)).isoformat()
            r = db.insertar("reserva", [fila])[0]
            resumen["reservas"] += 1
            if cancelada:
                resumen["canceladas"] += 1
                continue
            confirmadas += 1
            if not c["pasada"] or (mail == SABRINA and clave == reglas["sabrina_sin_registrar"]):
                continue
            ausente = mail == INES and clave in (reglas["ines_ausente_100"], reglas["ines_ausente_25"])
            db.insertar("asistencia", [{"id_reserva": r["id"], "id_empresa": e2, "estado": "ausente" if ausente else "presente"}])
            resumen["asistencias"] += 1
        db.actualizar("clase", {"cantidad_inscriptas": confirmadas}, id=clase["id"])
        if c["semana"] == 1 and HORARIOS[c["horario"]][0] == "jueves":
            db.insertar("listadeespera", [{"id_alumna": ids[NOELIA], "id_clase": clase["id"], "posicion": 1,
                                           "estado": "en_espera", "id_empresa": e2}])
            resumen["espera"] += 1

    # el 25 % rige desde el 1° de este mes (nunca hacia atrás: el mes anterior se factura con la regla anterior)
    db.actualizar("empresa", {"cobro_inasistencia_desde": plan["inicio_mes"].isoformat()}, id=e2)

    # facturas del mes anterior: borradores, confirmación (numeración PIL) y el pago de Rocío, como la administradora
    mes_ant = plan["mes_anterior"]
    rpc = db.como(ADMIN[0], contrasenas[ADMIN[0]], NOMBRE)
    rpc("sincronizar_borradores", {"p_mes": mes_ant.month, "p_anio": mes_ant.year})
    facturas = db.leer("resumen_mensual", "id, id_alumna, version, estado_factura", id_empresa=e2)
    for f in sorted(facturas, key=lambda f: f["id"]):
        rpc("confirmar_factura", {"p_id": f["id"], "p_version": f["version"]})
    rocio = next((f for f in db.leer("resumen_mensual", "id, id_alumna, total", id_empresa=e2) if f["id_alumna"] == ids[ROCIO]), None)
    if rocio:
        rpc("registrar_pago", {"p_id_resumen": rocio["id"], "p_monto": str(rocio["total"]), "p_medio": "transferencia",
                               "p_fecha": hoy.isoformat()})
    resumen["facturas"] = len(facturas)

    if guardar_contrasenas:
        _guardar_contrasenas(contrasenas, db.local)
    return {"empresa": e2, **resumen, "cuentas": len(CUENTAS), "contrasenas": contrasenas, "reglas": reglas, "ids": ids}


def crear_superadmin_local(db) -> str | None:
    """Solo en local: la superadministradora de prueba (en producción la crea Julieta con scripts/crear_superadmin.py)."""
    if not db.local:
        raise SystemExit("--con-superadmin solo se usa en local; en producción: python scripts/crear_superadmin.py")
    mail, nombre, apellido = SUPERADMIN
    if db.leer("profiles", "id", mail=mail):
        return None
    contrasena = "Demo-" + secrets.token_urlsafe(9)
    uid = db.crear_cuenta(mail, nombre, apellido, contrasena, {"sin_empresa": True})
    db.actualizar("profiles", {"es_superadmin": True}, id=uid)
    return contrasena


def borrar(db, guardar_contrasenas=True) -> dict:
    e = _empresa(db)
    if not e:
        raise SystemExit(f"No existe la empresa {CODIGO}: no hay nada para borrar.")
    e2 = e["id"]
    # cuentas que solo pertenecen a esta empresa (las del script y las que se crearon después desde la app): se borran,
    # pero solo si son ficticias (.invalid); si hubiera otra, no se borra nada
    membresias = db.leer("membresia", "id_usuario, id_empresa")
    perfiles = {p["id"]: p for p in db.leer("profiles", "id, mail, empresa_activa, es_superadmin")}
    de_e2 = {m["id_usuario"] for m in membresias if m["id_empresa"] == e2}
    exclusivas = {u for u in de_e2 if not any(m["id_usuario"] == u and m["id_empresa"] != e2 for m in membresias)
                  and not perfiles.get(u, {}).get("es_superadmin")}
    reales = sorted(perfiles[u]["mail"] for u in exclusivas if u in perfiles and not perfiles[u]["mail"].endswith(".invalid"))
    if reales:
        raise SystemExit(f"La empresa {CODIGO} tiene cuentas que no son ficticias ({', '.join(reales)}): no se borra nada.")
    if db.leer("resumen_mensual", "id", id_empresa=e2) and any(f.get("numero") for f in db.leer("resumen_mensual", "numero", id_empresa=e2)):
        print("Aviso: la empresa tenía facturas confirmadas de demostración; se borran también.")
    for f in db.leer("resumen_mensual", "id", id_empresa=e2):
        db.actualizar("resumen_mensual", {"reemplaza_a": None}, id=f["id"])
    for tabla in ("mp_intento", "pago_anulacion", "pago", "factura_historial", "resumen_mensual_detalle", "resumen_mensual",
                  "asistencia_historial", "asistencia", "listadeespera", "reserva", "baja_docente", "bloqueo", "clase", "horario",
                  "tipo_clase", "sesion_empresa"):
        db.borrar(tabla, id_empresa=e2)
    mails = {m for m, *_ in CUENTAS}
    for p in perfiles.values():
        if p["mail"] in mails or p["id"] in exclusivas:
            db.borrar_cuenta(p["id"])
        elif p["empresa_activa"] == e2:
            db.actualizar("profiles", {"empresa_activa": None}, id=p["id"])
    db.borrar("membresia", id_empresa=e2)
    db.borrar("empresa", id=e2)
    if guardar_contrasenas:
        _guardar_contrasenas({}, db.local)
    return {"empresa": e2}


def _guardar_contrasenas(contrasenas: dict, local: bool) -> None:
    archivo = ARCHIVOS["local" if local else "produccion"]
    quitar = {m for m, *_ in CUENTAS} | set(contrasenas)  # la superadministradora se conserva salvo que se reemplace
    lineas = archivo.read_text().splitlines() if archivo.exists() else []
    lineas = [l for l in lineas if l.split("  ")[0] not in quitar]
    lineas += [f"{m}  {c}" for m, c in contrasenas.items()]
    archivo.write_text("\n".join(lineas) + "\n")
    archivo.chmod(0o600)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("crear", "borrar") or set(args[1:]) - {"--produccion", "--con-superadmin"}:
        raise SystemExit(__doc__)
    from app import clases
    base = BaseSupabase(produccion="--produccion" in args)
    if args[0] == "crear":
        extra = {}
        if "--con-superadmin" in args:
            clave = crear_superadmin_local(base)
            if clave:
                extra[SUPERADMIN[0]] = clave
        r = crear(base, clases.ahora().date(), guardar_contrasenas=False)
        if r.get("ya_existia"):
            print(f"La empresa {NOMBRE} ya existe: no se creó nada (para empezar de nuevo: borrar y crear).")
        else:
            _guardar_contrasenas({**r["contrasenas"], **extra}, base.local)
            print(f"Empresa {NOMBRE} creada: {r['cuentas']} cuentas, {r['clases']} clases, {r['reservas']} reservas, "
                  f"{r['asistencias']} asistencias, {r['facturas']} facturas del mes anterior. "
                  f"Contraseñas en {ARCHIVOS['local' if base.local else 'produccion'].name}.")
    else:
        borrar(base)
        print(f"Empresa {NOMBRE} borrada (Just Show Up y la alumna compartida no se tocaron).")
