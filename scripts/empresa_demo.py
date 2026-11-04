"""Empresa 2 de demostración para la defensa: «Pilates Centro» (PIL), con un escenario FIJO y reproducible (mismas
personas, horarios y reglas; las fechas se calculan desde el día en que se ejecuta).

Personas (todas ficticias, @demo.invalid salvo la alumna compartida):
- Lucía Bentancor, administradora (da Pilates mat los lunes y reformer los miércoles).
- Carolina Méndez, profesora común (mat los martes y reformer los jueves): no ve Facturación.
- Rocío Acosta (cumplidora), Inés Duarte (dos inasistencias, una el mes anterior y otra este mes: las dos se cobran al
  25 %), Julia Ferreira (canceló a tiempo una clase: no se cobra), Noelia Gómez (en lista de espera de una clase
  completa de la próxima semana), Sabrina Viera (asiste a todas sus clases).
- alumna@prueba.invalid: la misma cuenta que ya es alumna de Just Show Up (alumna compartida).

Crea tipos de clase, horarios, clases de las 4 semanas anteriores, esta y la próxima, reservas, asistencias, la lista
de espera y las facturas del mes anterior (confirmadas con numeración PIL; la de Rocío, pagada). El cobro del 25 % por
inasistencia rige desde el 1° del mes ANTERIOR (todo el escenario), igual que en producción desde el 04/11/2026. Ninguna
clase dictada queda «sin registrar». Mercado Pago queda apagado en esta empresa.

`crear` no hace nada si la empresa ya existe (no duplica). `borrar` elimina SOLO lo de esta empresa y las cuentas que
pertenecen únicamente a ella (siempre ficticias): la alumna compartida y Just Show Up no se tocan.

SEGURIDAD: por defecto solo corre contra un Supabase LOCAL (localhost). Contra producción hace falta `--produccion`,
que solo se usa con la autorización de Julieta. Las contraseñas se generan al azar y nunca se muestran: en local van a
`.env.cuentas-local` y en producción a `.env.cuentas-prueba` (los dos ignorados por git).

Pruebas: tests/base_local/test_empresa_demo_base.py y tests/supabase_local/prueba_empresa2_reales.py

`actualizar` pone al día una Pilates Centro ya creada sin borrar nada (reservas de las próximas clases, clase completa con
lista de espera); conserva lo hecho en los ensayos y no toca facturas confirmadas, pagos ni Just Show Up. NUNCA asigna
asistencias por su cuenta: `--propuesta` escribe un CSV con las asistencias pendientes (columna `estado` vacía), Julieta
completa presente/ausente en las filas que aprueba y `--asignaciones` las aplica (las filas vacías quedan pendientes). Sin
`--aplicar` solo muestra el plan; con asignaciones, `--aplicar` exige `--huella` con el sha256 que mostró la vista previa.

Uso:
    python scripts/empresa_demo.py crear [--con-superadmin] [--produccion]
    python scripts/empresa_demo.py actualizar --propuesta <archivo.csv> [--produccion]
    python scripts/empresa_demo.py actualizar [--asignaciones <archivo.csv>] [--aplicar [--huella <sha256>]] [--produccion]
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
        "ines_ausente_mes_anterior": min((c["fecha"], c["horario"]) for c in de(INES, del_mes_ant)) if de(INES, del_mes_ant) else None,
        "ines_ausente_mes": max((c["fecha"], c["horario"]) for c in de(INES, del_mes)) if de(INES, del_mes) else None,
        "julia_cancelada": max((c["fecha"], c["horario"]) for c in de(JULIA, del_mes_ant) if HORARIOS[c["horario"]][0] == "miercoles")
        if [c for c in de(JULIA, del_mes_ant) if HORARIOS[c["horario"]][0] == "miercoles"] else None,
    }
    mes_anterior = inicio_mes - timedelta(days=1)
    return {"clases": clases, "reglas": reglas, "mes_anterior": mes_anterior, "inicio_mes": inicio_mes,
            "vigencia_25": mes_anterior.replace(day=1)}


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
            if not c["pasada"]:
                continue
            ausente = mail == INES and clave in (reglas["ines_ausente_mes_anterior"], reglas["ines_ausente_mes"])
            db.insertar("asistencia", [{"id_reserva": r["id"], "id_empresa": e2, "estado": "ausente" if ausente else "presente"}])
            resumen["asistencias"] += 1
        db.actualizar("clase", {"cantidad_inscriptas": confirmadas}, id=clase["id"])
        if c["semana"] == 1 and HORARIOS[c["horario"]][0] == "jueves":
            db.insertar("listadeespera", [{"id_alumna": ids[NOELIA], "id_clase": clase["id"], "posicion": 1,
                                           "estado": "en_espera", "id_empresa": e2}])
            resumen["espera"] += 1

    # el 25 % por inasistencia rige para todo el escenario (desde el 1° del mes anterior), como en producción
    db.actualizar("empresa", {"cobro_inasistencia_desde": plan["vigencia_25"].isoformat()}, id=e2)

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


MOTIVO_ASIGNADA = ("Asignación explícita de datos ficticios de demostración, aprobada por Julieta (no verificada; "
                   "la registró un script, no una persona)")
ESTADOS = ("presente", "ausente")
COLUMNAS = ["id_reserva", "fecha", "hora", "clase", "alumna", "precio", "cobro_si_presente", "cobro_si_ausente", "estado"]
# Autora técnica de esas asistencias en el historial: así nunca aparecen como hechas por Lucía. No pertenece a ninguna
# empresa ni tiene rol (su contraseña se descarta: no puede iniciar sesión en ningún lugar).
SISTEMA = ("escenario.sistema@demo.invalid", "Carga automática", "(script de demostración)")


def leer_asignaciones(ruta) -> tuple[dict, str]:
    """Lee el CSV de asignaciones (el de `--propuesta` con la columna estado completada): {id_reserva: estado} y el
    sha256 del archivo. Las filas con estado vacío no se asignan (quedan pendientes). Cualquier error detiene todo."""
    import csv
    import hashlib
    datos = Path(ruta).read_bytes()
    asignaciones, errores = {}, []
    for n, fila in enumerate(csv.DictReader(datos.decode("utf-8").splitlines(), delimiter=";"), start=2):
        estado = (fila.get("estado") or "").strip().lower()
        if not estado:
            continue
        try:
            rid = int((fila.get("id_reserva") or "").strip())
        except ValueError:
            errores.append(f"fila {n}: id_reserva inválido"); continue
        if estado not in ESTADOS:
            errores.append(f"fila {n}: estado «{estado}» (solo presente o ausente)")
        elif rid in asignaciones:
            errores.append(f"fila {n}: la reserva {rid} está repetida")
        else:
            asignaciones[rid] = estado
    if errores:
        raise SystemExit("El archivo de asignaciones tiene errores; no se aplicó nada:\n  " + "\n  ".join(errores))
    return asignaciones, hashlib.sha256(datos).hexdigest()


def escribir_propuesta(pendientes: list, ruta) -> int:
    """CSV para revisar: una fila por asistencia pendiente, con la columna estado VACÍA (no se sugiere nada)."""
    import csv
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        for p in pendientes:
            w.writerow({**{k: p[k] for k in COLUMNAS if k != "estado"}, "estado": ""})
    return len(pendientes)


def actualizar(db, ahora: datetime, aplicar: bool = False, asignaciones: dict | None = None) -> dict:
    """Pone al día el escenario de una Pilates Centro YA creada, sin borrar nada (para mantener la demostración hasta
    la defensa). Solo agrega; nunca cambia una asistencia, reserva, factura ni pago existentes:
    - asistencias pendientes de clases ya dictadas de las alumnas del escenario (no la compartida ni otras cuentas), en
      meses sin factura confirmada: se LISTAN; solo se registran las que vengan en `asignaciones` ({id_reserva: estado},
      aprobadas por Julieta), a nombre de la cuenta técnica SISTEMA. Las que no se asignan quedan pendientes. Si una
      asignación no corresponde a una pendiente del escenario, no se aplica nada.
    - reservas del patrón en las clases que todavía no empezaron hasta el fin de la próxima semana (sin pasar el cupo, sin
      repetir ni revivir cancelaciones); crea las clases que falten de los horarios del escenario.
    - el jueves de la próxima semana completo y Noelia en la lista de espera (si se puede llegar al cupo).
    Por defecto no escribe nada: devuelve el plan. Con aplicar=True lo ejecuta."""
    e = _empresa(db)
    if not e:
        raise SystemExit(f"No existe la empresa {CODIGO}: primero hay que crearla.")
    e2 = e["id"]
    hoy = ahora.date()
    lunes = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)
    # personas del escenario: solo las que pertenecen a Pilates Centro
    en_e2 = {m["id_usuario"] for m in db.leer("membresia", "id_usuario", id_empresa=e2)}
    mails = [ADMIN[0]] + [m for m, *_ in ALUMNAS] + [ALUMNA_COMPARTIDA]
    ids = {p["mail"]: p["id"] for p in db.leer("profiles", "id, mail") if p["mail"] in mails and p["id"] in en_e2}
    faltan = [m for m in mails if m not in ids]
    if faltan:
        raise SystemExit(f"Faltan personas del escenario en {CODIGO} ({', '.join(faltan)}): no se cambió nada.")
    del_escenario = {ids[m] for m, *_ in ALUMNAS}  # solo las alumnas creadas por el escenario (no la compartida)

    tipos = {t["id"]: t for t in db.leer("tipo_clase", "id, nombre, duracion, precio_presencial, cupo_max, activo", id_empresa=e2)}
    horarios = {}
    for h in db.leer("horario", "id, dia_semana, hora_inicio, id_tipo_clase, id_profesora, activo", id_empresa=e2):
        for i, (dia, hora, t, _, _) in enumerate(HORARIOS):
            if (h["dia_semana"], str(h["hora_inicio"])[:5], tipos.get(h["id_tipo_clase"], {}).get("nombre")) == (dia, hora, TIPOS[t][0]):
                horarios[i] = h
    clases_e2 = db.leer("clase", "id, id_horario, fecha, hora_inicio, estado, cupo_max, precio", id_empresa=e2)
    por_horario_fecha = {(c["id_horario"], str(c["fecha"])[:10]): c for c in clases_e2}
    reservas = db.leer("reserva", "id, id_alumna, id_clase, estado", id_empresa=e2)
    asistencias = {a["id_reserva"]: a["estado"] for a in db.leer("asistencia", "id_reserva, estado", id_empresa=e2)}
    espera = db.leer("listadeespera", "id, id_alumna, id_clase, estado, posicion", id_empresa=e2)
    confirmadas_fact = {(f["id_alumna"], f["mes"], f["anio"]) for f in db.leer("resumen_mensual", "id_alumna, mes, anio, estado_factura",
                                                                             id_empresa=e2) if f["estado_factura"] == "confirmada"}
    clase_por_id = {c["id"]: c for c in clases_e2}
    inicio = lambda c: datetime.combine(_fecha(c["fecha"]), _hora(c["hora_inicio"]), tzinfo=ahora.tzinfo)
    del_horario = {h["id"]: i for i, h in horarios.items()}
    plan = {"asistencias": [], "clases": [], "reservas": [], "espera": [], "avisos": [], "cupos": {}}

    # ---------- asistencias pendientes de clases ya dictadas ----------
    pendientes = []
    for r in reservas:
        c = clase_por_id.get(r["id_clase"])
        if (not c or c["id_horario"] not in del_horario or r["estado"] != "confirmada" or c["estado"] != "programada"
                or r["id_alumna"] not in del_escenario or r["id"] in asistencias or inicio(c) >= ahora):
            continue
        f = _fecha(c["fecha"])
        if (r["id_alumna"], f.month, f.year) in confirmadas_fact:
            continue  # su factura de ese mes está confirmada: no se toca (la base tampoco lo permitiría)
        pendientes.append((inicio(c), r))
    pendientes.sort(key=lambda x: x[0])
    vigencia = db.leer("empresa", "cobro_inasistencia_desde", id=e2)[0]["cobro_inasistencia_desde"]
    nombre_tipo = {h["id"]: tipos[h["id_tipo_clase"]]["nombre"] for h in horarios.values()}
    mail_de = {v: k for k, v in ids.items()}
    plan["pendientes"] = []
    for t, r in pendientes:
        c = clase_por_id[r["id_clase"]]
        precio = float(c["precio"])
        al_25 = vigencia is not None and _fecha(c["fecha"]) >= _fecha(vigencia)
        plan["pendientes"].append({"id_reserva": r["id"], "fecha": t.date().isoformat(), "hora": t.strftime("%H:%M"),
                                   "clase": nombre_tipo.get(c["id_horario"], ""), "alumna": mail_de[r["id_alumna"]],
                                   "precio": f"{precio:.2f}", "cobro_si_presente": f"{precio:.2f}",
                                   "cobro_si_ausente": f"{round(precio * (0.25 if al_25 else 1), 2):.2f}"})
    asignaciones = asignaciones or {}
    por_id = {r["id"]: r for _, r in pendientes}
    ajenas = sorted(set(asignaciones) - set(por_id))
    if ajenas:
        raise SystemExit("Hay asignaciones que no son asistencias pendientes del escenario (ya registradas, de otra cuenta "
                         f"o de un mes facturado): {ajenas}. No se aplicó nada.")
    if any(e not in ESTADOS for e in asignaciones.values()):
        raise SystemExit("Solo se aceptan los estados presente o ausente. No se aplicó nada.")
    plan["asistencias"] = [(por_id[rid], estado) for rid, estado in sorted(asignaciones.items())]

    # ---------- reservas de las clases que todavía no empezaron (hasta el fin de la próxima semana) ----------
    jueves_proximo = lunes + timedelta(days=7 + DIAS.index("jueves"))
    ocupadas = {}  # id de clase -> reservas confirmadas (contando las que se agregan)
    for i, (dia, hora, t, _, alumnas) in enumerate(HORARIOS):
        h = horarios.get(i)
        if not h or not h["activo"]:
            if i not in horarios:
                plan["avisos"].append(f"No está el horario del escenario {dia} {hora}: no se agregan reservas ahí.")
            continue
        for semana in (0, 1):
            fecha = lunes + timedelta(weeks=semana, days=DIAS.index(dia))
            if datetime.combine(fecha, _hora(hora), tzinfo=ahora.tzinfo) <= ahora:
                continue
            c = por_horario_fecha.get((h["id"], fecha.isoformat()))
            if c and c["estado"] != "programada":
                continue  # cancelada (por ejemplo, en un ensayo de Planificación): se respeta
            patron = list(LLENA_PROXIMA) if fecha == jueves_proximo else list(alumnas) + ([ALUMNA_COMPARTIDA] if dia == "lunes" else [])
            clave = c["id"] if c else ("nueva", i, fecha)
            if not c:
                plan["clases"].append({"horario": i, "fecha": fecha, "clave": clave})
            de_la_clase = [r for r in reservas if c and r["id_clase"] == c["id"]]
            en_espera = {x["id_alumna"] for x in espera if c and x["id_clase"] == c["id"] and x["estado"] in ("en_espera", "ofrecido")}
            cupo = c["cupo_max"] if c else tipos[h["id_tipo_clase"]]["cupo_max"]
            ocupadas[clave] = sum(r["estado"] == "confirmada" for r in de_la_clase)
            for mail in patron:
                if any(r["id_alumna"] == ids[mail] for r in de_la_clase) or ids[mail] in en_espera:
                    continue  # ya reservó, canceló o espera: no se repite ni se revive
                if ocupadas[clave] >= cupo:
                    break
                plan["reservas"].append({"clave": clave, "mail": mail, "fecha": fecha, "horario": i})
                ocupadas[clave] += 1
            plan["cupos"][clave] = (ocupadas[clave], cupo)
            if fecha == jueves_proximo:
                ya = any(x["id_alumna"] == ids[NOELIA] for x in espera if c and x["id_clase"] == c["id"]) \
                    or any(r["id_alumna"] == ids[NOELIA] for r in de_la_clase)
                if ya:
                    pass
                elif ocupadas[clave] >= cupo:
                    pos = max([x["posicion"] for x in espera if c and x["id_clase"] == c["id"]] or [0]) + 1
                    plan["espera"].append({"clave": clave, "fecha": fecha, "posicion": pos})
                else:
                    plan["avisos"].append(f"El jueves {fecha.day}/{fecha.month} no llega al cupo ({ocupadas[clave]}/{cupo}): no se crea la lista de espera.")

    if aplicar:
        autor = None
        if plan["asistencias"]:
            existente = db.leer("profiles", "id", mail=SISTEMA[0])
            autor = existente[0]["id"] if existente else db.crear_cuenta(*SISTEMA, secrets.token_urlsafe(24), {"sin_empresa": True})
        for r, estado in plan["asistencias"]:
            # se vuelve a comprobar justo antes: si alguien la registró mientras tanto, no se pisa
            if db.leer("asistencia", "id", id_reserva=r["id"]):
                plan["avisos"].append(f"La reserva {r['id']} ya tenía asistencia: no se cambió.")
                continue
            db.insertar("asistencia", [{"id_reserva": r["id"], "id_empresa": e2, "estado": estado}])
            db.insertar("asistencia_historial", [{"id_empresa": e2, "id_reserva": r["id"], "estado_anterior": None, "estado_nuevo": estado,
                                                  "origen": "correccion", "motivo": MOTIVO_ASIGNADA, "por": autor}])
        nuevas = {}
        for n in plan["clases"]:
            h = horarios[n["horario"]]
            tipo = tipos[h["id_tipo_clase"]]
            nuevas[n["clave"]] = db.insertar("clase", [{"id_horario": h["id"], "fecha": n["fecha"].isoformat(), "hora_inicio": h["hora_inicio"],
                                                        "duracion": tipo["duracion"], "precio": tipo["precio_presencial"],
                                                        "cupo_max": tipo["cupo_max"], "id_tipo_clase": tipo["id"], "estado": "programada",
                                                        "id_profesora": h["id_profesora"], "id_empresa": e2}])[0]["id"]
        id_de = lambda clave: nuevas.get(clave, clave)
        confirmadas = lambda cid: len(db.leer("reserva", "id", id_clase=cid, estado="confirmada"))
        for k, x in enumerate(plan["reservas"]):
            cid = id_de(x["clave"])
            # se vuelve a contar justo antes: si alguien reservó mientras tanto, no se pasa el cupo
            if confirmadas(cid) >= plan["cupos"][x["clave"]][1]:
                plan["avisos"].append(f"La clase del {x['fecha'].day}/{x['fecha'].month} se llenó mientras tanto: no se agregó a {x['mail']}.")
                continue
            reservada = datetime.combine(hoy, datetime.min.time()) + timedelta(minutes=k)
            db.insertar("reserva", [{"id_alumna": ids[x["mail"]], "id_clase": cid, "estado": "confirmada",
                                     "fecha_reserva": reservada.isoformat(), "id_empresa": e2}])
        for clave, (_, cupo) in plan["cupos"].items():
            db.actualizar("clase", {"cantidad_inscriptas": confirmadas(id_de(clave))}, id=id_de(clave))
            plan["cupos"][clave] = (confirmadas(id_de(clave)), cupo)
        plan["espera"] = [x for x in plan["espera"] if plan["cupos"][x["clave"]][0] >= plan["cupos"][x["clave"]][1]]
        for x in plan["espera"]:
            db.insertar("listadeespera", [{"id_alumna": ids[NOELIA], "id_clase": id_de(x["clave"]), "posicion": x["posicion"],
                                           "estado": "en_espera", "id_empresa": e2}])

    nombre = {v: k for k, v in ids.items()}
    asignadas = {r["id"] for r, _ in plan["asistencias"]}
    return {
        "aplicado": aplicar,
        "asistencias": len(plan["asistencias"]),
        "ausentes": [(_fecha(clase_por_id[r["id_clase"]]["fecha"]), nombre[r["id_alumna"]]) for r, e in plan["asistencias"] if e == "ausente"],
        "pendientes": plan["pendientes"],
        "quedan_pendientes": [p for p in plan["pendientes"] if p["id_reserva"] not in asignadas],
        "clases_nuevas": len(plan["clases"]),
        "reservas": len(plan["reservas"]),
        "espera": [x["fecha"] for x in plan["espera"]],
        "avisos": plan["avisos"],
    }


def _fecha(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _hora(v):
    from datetime import time as _time
    return v if isinstance(v, _time) else _time.fromisoformat(str(v)[:8] if len(str(v)) >= 8 else str(v))


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
    for p in db.leer("profiles", "id", mail=SISTEMA[0]):  # la autora técnica de «actualizar» (sin empresa)
        db.borrar_cuenta(p["id"])
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


def _opcion(args, nombre):
    """Valor de una opción `--nombre valor` (None si no está)."""
    if nombre not in args:
        return None
    i = args.index(nombre)
    if i + 1 >= len(args) or args[i + 1].startswith("--"):
        raise SystemExit(f"Falta el valor de {nombre}.")
    return args[i + 1]


if __name__ == "__main__":
    args = sys.argv[1:]
    con_valor = {"--propuesta", "--asignaciones", "--huella"}
    banderas = [a for i, a in enumerate(args[1:], 1) if a.startswith("--") and not (i > 1 and args[i - 1] in con_valor)]
    if not args or args[0] not in ("crear", "borrar", "actualizar") or set(banderas) - {"--produccion", "--con-superadmin", "--aplicar"} - con_valor:
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
    elif args[0] == "actualizar":
        propuesta, archivo, huella = _opcion(args, "--propuesta"), _opcion(args, "--asignaciones"), _opcion(args, "--huella")
        if propuesta and ("--aplicar" in args or archivo):
            raise SystemExit("--propuesta solo escribe el archivo para revisar: no se combina con --aplicar ni --asignaciones.")
        asignaciones, sha = leer_asignaciones(archivo) if archivo else ({}, None)
        aplicar = "--aplicar" in args
        if aplicar and archivo and huella != sha:
            raise SystemExit(f"Para aplicar las asignaciones hace falta --huella {sha} (la del archivo revisado en la vista "
                             "previa). Si el archivo cambió, revisalo de nuevo. No se aplicó nada.")
        r = actualizar(base, clases.ahora(), aplicar=aplicar, asignaciones=asignaciones)
        if propuesta:
            n = escribir_propuesta(r["pendientes"], propuesta)
            print(f"Propuesta escrita en {propuesta}: {n} asistencias pendientes, con la columna «estado» vacía. "
                  "Completá presente o ausente en las filas que apruebes; las vacías quedan pendientes. No se escribió nada en la base.")
            sys.exit(0)
        print(("APLICADO" if r["aplicado"] else "VISTA PREVIA (no se escribió nada)") + f" — {NOMBRE}:")
        if archivo:
            print(f"  archivo de asignaciones: {archivo} · sha256 {sha}")
        print(f"  asistencias asignadas: {r['asistencias']} (ausentes: "
              + (", ".join(f"{m.split('.')[0]} {f.day}/{f.month}" for f, m in r["ausentes"]) or "ninguna") + ")")
        print(f"  quedan pendientes: {len(r['quedan_pendientes'])}"
              + "".join(f"\n    {p['fecha']} {p['hora']} {p['clase']} · {p['alumna']} (reserva {p['id_reserva']})" for p in r["quedan_pendientes"]))
        print(f"  clases nuevas: {r['clases_nuevas']} · reservas nuevas: {r['reservas']} · lista de espera: "
              + (", ".join(f"jueves {f.day}/{f.month}" for f in r["espera"]) or "sin cambios"))
        for a in r["avisos"]:
            print("  aviso:", a)
        if not r["aplicado"] and archivo:
            print(f"  para aplicar: --asignaciones {archivo} --aplicar --huella {sha}")
    else:
        borrar(base)
        print(f"Empresa {NOMBRE} borrada (Just Show Up y la alumna compartida no se tocaron).")
