"""Factura de DEMOSTRACIÓN de Mercado Pago (solo dinero ficticio) para la defensa.

Crea, para alumna@prueba.invalid, una factura de septiembre de 2026 por $800 ficticios con una
sola clase «Gimnasia (demostración)». El tipo de clase y el horario quedan INACTIVOS: no aparecen
en la grilla, nadie los puede reservar y no generan clases nuevas. La factura queda confirmada,
publicada y pendiente de pago. Es independiente de las facturas normales de las alumnas.

Todo lo que crea se anota en un manifiesto (archivo JSON local, fuera de git). 'borrar' elimina
SOLO lo anotado y se detiene si algo no tiene la marca de la demostración.

Uso:
    python scripts/factura_demo_mp.py crear  <manifiesto.json>
    python scripts/factura_demo_mp.py borrar <manifiesto.json>

Necesita .env (SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY) y .env.cuentas-prueba (contraseña
de profesora@prueba.invalid, que confirma la factura con sus permisos normales). No imprime
credenciales. No toca el interruptor de Mercado Pago ni hace pagos.

Numeración: la factura toma el número siguiente de factura_numero_seq. Si no hay otras facturas,
antes y después de 'crear' reiniciar el contador en el SQL Editor de Supabase con
    select setval('public.factura_numero_seq', 1, false);
así la demostración es JSU-2026-09-0001 y la primera factura real de octubre sigue siendo
JSU-2026-10-0001 (el número incluye año y mes, no chocan).
"""

import json
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from supabase import create_client

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")
logging.disable(logging.CRITICAL)

ALUMNA = "alumna@prueba.invalid"
PROFESORA = "profesora@prueba.invalid"
MARCA = "DEMO-MP"
TIPO = {"nombre": "Gimnasia (demostración)", "duracion": 60, "precio_presencial": 800, "cupo_max": 7,
        "descripcion": f"{MARCA}: solo para la demostración de Mercado Pago (dinero ficticio). No activar.",
        "activo": False}
DIA, HORA, FECHA, MES, ANIO = "lunes", "10:00", "2026-09-07", 9, 2026


def _admin():
    if "nspgafucytcjqkqnmkry" not in os.environ.get("SUPABASE_URL", ""):
        sys.exit("DETENIDO: el .env no apunta al proyecto Just Show Up.")
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _contrasena(mail):
    for linea in open(RAIZ / ".env.cuentas-prueba", encoding="utf-8"):
        partes = linea.split()
        if len(partes) == 2 and partes[0] == mail:
            return partes[1]
    sys.exit(f"DETENIDO: falta la contraseña de {mail} en .env.cuentas-prueba.")


def _guardar(ruta, reg):
    with open(ruta, "w") as f:
        json.dump(reg, f, indent=1)
    os.chmod(ruta, 0o600)


def crear(admin, ruta):
    if os.path.exists(ruta):
        sys.exit("DETENIDO: ese manifiesto ya existe (¿ya hay una demostración creada?).")
    sept = admin.table("clase").select("id").gte("fecha", f"{ANIO}-09-01").lt("fecha", f"{ANIO}-10-01").execute().data
    facturas_sept = admin.table("resumen_mensual").select("id").eq("mes", MES).eq("anio", ANIO).execute().data
    if sept or facturas_sept:
        sys.exit(f"DETENIDO: septiembre ya tiene {len(sept)} clases o {len(facturas_sept)} facturas.")
    alumna = admin.table("profiles").select("id").eq("mail", ALUMNA).execute().data[0]["id"]
    reg = {"alumna": alumna}
    _guardar(ruta, reg)
    try:
        tipo = admin.table("tipo_clase").insert(TIPO).execute().data[0]
        reg["tipo"] = tipo["id"]; _guardar(ruta, reg)
        horario = admin.table("horario").insert({"dia_semana": DIA, "hora_inicio": HORA, "id_tipo_clase": tipo["id"],
                                                 "activo": False}).execute().data[0]
        reg["horario"] = horario["id"]; _guardar(ruta, reg)
        clase = admin.table("clase").insert({"id_horario": horario["id"], "fecha": FECHA, "hora_inicio": HORA,
                                             "cantidad_inscriptas": 1, "estado": "programada", "duracion": 60,
                                             "precio": 800, "cupo_max": 7, "id_tipo_clase": tipo["id"]}).execute().data[0]
        reg["clase"] = clase["id"]; _guardar(ruta, reg)
        reserva = admin.table("reserva").insert({"id_alumna": alumna, "id_clase": clase["id"],
                                                 "estado": "confirmada"}).execute().data[0]
        reg["reserva"] = reserva["id"]; _guardar(ruta, reg)

        r = httpx.post(os.environ["SUPABASE_URL"] + "/auth/v1/token?grant_type=password",
                       headers={"apikey": os.environ["SUPABASE_KEY"]},
                       json={"email": PROFESORA, "password": _contrasena(PROFESORA)}, timeout=20)
        r.raise_for_status()
        jwt = r.json()["access_token"]
        prof = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        prof.postgrest.auth(jwt)
        prof.rpc("sincronizar_borradores", {"p_mes": MES, "p_anio": ANIO}).execute()
        fs = admin.table("resumen_mensual").select("*").eq("mes", MES).eq("anio", ANIO).execute().data
        reg["facturas"] = [f["id"] for f in fs]; _guardar(ruta, reg)
        if len(fs) != 1 or fs[0]["id_alumna"] != alumna or float(fs[0]["total"]) != 800:
            sys.exit(f"DETENIDO: se esperaba 1 factura de $800 de la alumna de prueba y hay {len(fs)}. Correr 'borrar'.")
        c = prof.rpc("confirmar_factura", {"p_id": fs[0]["id"], "p_version": fs[0]["version"]}).execute().data
        httpx.post(os.environ["SUPABASE_URL"] + "/auth/v1/logout",
                   headers={"apikey": os.environ["SUPABASE_KEY"], "Authorization": f"Bearer {jwt}"}, timeout=20)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"ERROR al crear ({type(e).__name__}): correr 'borrar' con el mismo manifiesto.")
    f = admin.table("resumen_mensual").select("*").eq("id", fs[0]["id"]).execute().data[0]
    print("confirmar:", c.get("resultado"))
    print("factura de demostración:", f["id"], f["numero"], f["estado_factura"], f["estado_pago"], "total", f["total"])


def borrar(admin, ruta):
    reg = json.load(open(ruta))
    tipo = admin.table("tipo_clase").select("descripcion").eq("id", reg.get("tipo", -1)).execute().data
    if reg.get("tipo") and not (tipo and (tipo[0]["descripcion"] or "").startswith(MARCA)):
        sys.exit("DETENIDO: el tipo de clase del manifiesto no tiene la marca de la demostración. No se borró nada.")
    facturas = reg.get("facturas", [])
    if facturas:
        ajenas = [f for f in admin.table("resumen_mensual").select("id, id_alumna").in_("id", facturas).execute().data
                  if f["id_alumna"] != reg["alumna"]]
        if ajenas:
            sys.exit("DETENIDO: el manifiesto incluye facturas de otra alumna. No se borró nada.")
        pagos = [p["id"] for p in admin.table("pago").select("id").in_("id_resumen_mensual", facturas).execute().data]
        intentos = admin.table("mp_intento").select("id_pago_mp").in_("id_resumen_mensual", facturas).execute().data
        admin.table("mp_intento").update({"id_pago": None}).in_("id_resumen_mensual", facturas).execute()
        if pagos:
            admin.table("pago_anulacion").delete().in_("id_pago", pagos).execute()
            admin.table("pago").delete().in_("id", pagos).execute()
        ops = [i["id_pago_mp"] for i in intentos if i.get("id_pago_mp")]
        if ops:
            admin.table("mp_evento").delete().in_("id_recurso", ops).execute()
        admin.table("mp_intento").delete().in_("id_resumen_mensual", facturas).execute()
        admin.table("factura_historial").delete().in_("id_resumen_mensual", facturas).execute()
        admin.table("resumen_mensual_detalle").delete().in_("id_resumen_mensual", facturas).execute()
        admin.table("resumen_mensual").update({"reemplaza_a": None}).in_("id", facturas).execute()
        admin.table("resumen_mensual").delete().in_("id", facturas).execute()
    if reg.get("reserva"):
        admin.table("asistencia").delete().eq("id_reserva", reg["reserva"]).execute()
        admin.table("reserva").delete().eq("id", reg["reserva"]).eq("id_alumna", reg["alumna"]).execute()
    if reg.get("clase"):
        admin.table("listadeespera").delete().eq("id_clase", reg["clase"]).execute()
        admin.table("clase").delete().eq("id", reg["clase"]).execute()
    if reg.get("horario"):
        admin.table("horario").delete().eq("id", reg["horario"]).eq("activo", False).execute()
    if reg.get("tipo"):
        admin.table("tipo_clase").delete().eq("id", reg["tipo"]).like("descripcion", f"{MARCA}%").execute()
    os.rename(ruta, ruta + ".borrado")
    print("borrado lo del manifiesto:", {k: v for k, v in reg.items() if k != "alumna"})


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("crear", "borrar"):
        sys.exit(__doc__)
    (crear if sys.argv[1] == "crear" else borrar)(_admin(), sys.argv[2])
