"""Pruebas controladas del módulo 6 (facturación) y regresión contra Supabase REAL, a través del
servidor local (http://localhost:8018) con el código de la rama. SOLO cuentas @prueba.invalid.

Resguardos:
- Antes de escribir verifica que septiembre de 2026 no tiene clases ni reservas reales: así lo que
  se factura es solo de prueba. Aborta si las tablas de facturación no están vacías.
- Todo lo que crea queda registrado (archivo de registro) y se marca: tipos "PRUEBA-M6-...",
  horarios inactivos de domingo, bloqueo con motivo "PRUEBA-M6", alumna "regresion.m6.*@prueba.invalid".
- No visita la grilla de la alumna ni el día (generan clases y procesan listas de espera reales).
- Al final borra solo lo registrado y compara las tablas con el respaldo (datos.json).

Uso: python tests/supabase_real/prueba_modulo6_real.py <carpeta del respaldo> <archivo de registro>
(el servidor local tiene que estar corriendo en el puerto 8018 con el .env real).
"""
import json
import os
import re
import secrets
import sys
import time
import zlib
from datetime import date

import httpx
from dotenv import load_dotenv
from supabase import create_client

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(REPO, ".env"), override=True)
URL = os.environ["SUPABASE_URL"]
assert "nspgafucytcjqkqnmkry" in URL, "no es el proyecto Just Show Up"
BASE = "http://localhost:8018"
RESPALDO, REGISTRO = sys.argv[1], sys.argv[2]
admin = create_client(URL, os.environ["SUPABASE_SERVICE_KEY"])
TABLAS = ["profiles", "tipo_clase", "horario", "clase", "reserva", "listadeespera", "asistencia", "bloqueo"]
FACT = ["pago_anulacion", "pago", "factura_historial", "resumen_mensual_detalle", "resumen_mensual"]

resultados, creado = [], {"tipos": [], "horarios": [], "clases": [], "reservas": [], "bloqueos": [],
                          "usuarias_nuevas": [], "cedulas_cargadas": [], "facturas": []}


def ok(nombre, cond, extra=""):
    resultados.append((nombre, bool(cond)))
    print(("OK   " if cond else "FALLA") + " " + nombre + (f"  [{extra}]" if extra and not cond else ""), flush=True)


def guardar_registro():
    with open(REGISTRO, "w") as f:
        json.dump(creado, f, indent=1, default=str)
    os.chmod(REGISTRO, 0o600)


def todas(t, **eq):
    q = admin.table(t).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data


def texto_pdf(datos: bytes) -> str:
    return "\n".join(zlib.decompress(m.group(1)).decode("latin-1")
                     for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", datos, re.S))


# ---------- cuentas de prueba ----------
cuentas = {}
for linea in open(os.path.join(REPO, ".env.cuentas-prueba")):
    partes = linea.split()
    if len(partes) == 2 and partes[0].endswith("@prueba.invalid"):
        cuentas[partes[0]] = partes[1]
perfiles = {p["mail"]: p for p in todas("profiles") if p["mail"].endswith("@prueba.invalid")}
PROF = perfiles["profesora@prueba.invalid"]["id"]
A0 = perfiles["alumna@prueba.invalid"]["id"]
A1 = perfiles["alumna.uno@prueba.invalid"]["id"]
A2 = perfiles["alumna.dos@prueba.invalid"]["id"]
PRUEBA_IDS = {p["id"] for p in perfiles.values()}


def sesion_web(mail=None, tokens=None):
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)
    if mail:
        r = c.post("/login", data={"mail": mail, "contrasena": cuentas[mail]})
        assert r.status_code == 303, f"login {mail}: {r.status_code}"
    if tokens:
        c.cookies.set("sb_acceso", tokens[0])
        c.cookies.set("sb_refresco", tokens[1])
    return c


def tokens_por_enlace(mail):
    """Sesión sin contraseña para las alumnas de prueba fijas (no manda mails)."""
    link = admin.auth.admin.generate_link({"type": "magiclink", "email": mail})
    cli = create_client(URL, os.environ["SUPABASE_KEY"])
    s = cli.auth.verify_otp({"type": "magiclink", "token_hash": link.properties.hashed_token}).session
    return s.access_token, s.refresh_token


def cliente_rls(tokens):
    cli = create_client(URL, os.environ["SUPABASE_KEY"])
    cli.postgrest.auth(tokens[0])
    return cli


def aviso(r):
    loc = r.headers.get("location", "")
    q = httpx.URL(BASE + loc).params if loc else {}
    return q.get("ok"), q.get("aviso", "")


def version_de(web, factura_id):
    m = re.search(r'name="version" value="(\d+)"', web.get(f"/profesora/resumenes/{factura_id}").text)
    return int(m.group(1)) if m else None


def lineas(factura_id):
    return {l["id_clase"]: l for l in admin.table("resumen_mensual_detalle").select("*")
            .eq("id_resumen_mensual", factura_id).execute().data}


def factura_de(alumna, estado=None):
    q = admin.table("resumen_mensual").select("*").eq("id_alumna", alumna).eq("mes", 9).eq("anio", 2026)
    filas = q.execute().data
    return [f for f in filas if estado is None or f["estado_factura"] == estado]


# ---------- resguardos antes de escribir ----------
ok("servidor local responde", httpx.get(BASE + "/api/health").status_code == 200)
ok("cuentas de prueba disponibles (profesora + 4 alumnas)", len(perfiles) == 5 and "profesora@prueba.invalid" in cuentas
   and "alumna@prueba.invalid" in cuentas)
sept = admin.table("clase").select("id").gte("fecha", "2026-09-01").lt("fecha", "2026-10-01").execute().data
ok("septiembre 2026 sin clases reales (lo que se facture es solo de prueba)", sept == [])
vacias = all(admin.table(t).select("*", count="exact").limit(1).execute().count == 0 for t in FACT)
ok("tablas de facturación vacías antes de empezar", vacias)
ok("ninguna alumna tiene cédula cargada", not [p for p in todas("profiles") if p.get("cedula")])
ok("no hay clases reales el domingo 22/11 (fecha de la clase de prueba futura)",
   admin.table("clase").select("id").eq("fecha", "2026-11-22").execute().data == [])
if not all(c for _, c in resultados):
    print("\nABORTADO: no se cumplen los resguardos, no se escribió nada")
    sys.exit(2)

T0 = tokens_por_enlace("alumna.uno@prueba.invalid")
T2 = tokens_por_enlace("alumna.dos@prueba.invalid")
prof = sesion_web("profesora@prueba.invalid")
alu = sesion_web("alumna@prueba.invalid")
alu1 = sesion_web(tokens=T0)
alu2 = sesion_web(tokens=T2)
sufijo = secrets.token_hex(3)

try:
    # ---------- datos de prueba ----------
    def tipo(nombre, precio, cupo):
        t = admin.table("tipo_clase").insert({"nombre": f"PRUEBA-M6-{nombre}-{sufijo}", "descripcion": "prueba módulo 6",
                                               "duracion": 60, "precio_presencial": precio, "cupo_max": cupo,
                                               "activo": True}).execute().data[0]
        creado["tipos"].append(t["id"])
        guardar_registro()
        return t["id"]

    def horario(id_tipo, hora):
        h = admin.table("horario").insert({"dia_semana": "domingo", "hora_inicio": hora, "id_tipo_clase": id_tipo,
                                           "activo": False}).execute().data[0]
        creado["horarios"].append(h["id"])
        guardar_registro()
        return h["id"]

    def clase(id_horario, fecha, hora):
        h = admin.table("horario").select("duracion, precio").eq("id", id_horario).execute().data[0]
        c = admin.table("clase").insert({"id_horario": id_horario, "fecha": fecha, "hora_inicio": hora,
                                         "duracion": h["duracion"], "precio": h["precio"],
                                         "estado": "programada"}).execute().data[0]
        creado["clases"].append(c["id"])
        guardar_registro()
        return c

    def reserva(alumna, id_clase, estado="confirmada"):
        assert alumna in PRUEBA_IDS
        r = admin.table("reserva").insert({"id_alumna": alumna, "id_clase": id_clase, "estado": estado}).execute().data[0]
        creado["reservas"].append(r["id"])
        guardar_registro()
        return r["id"]

    tA, tB, tC = tipo("A", 800, 7), tipo("B", 950, 7), tipo("C", 700, 1)
    hA, hA2, hB, hC = horario(tA, "06:00"), horario(tA, "07:00"), horario(tB, "06:00"), horario(tC, "10:00")
    cA1 = clase(hA, "2026-09-06", "06:00"); cA2 = clase(hA, "2026-09-13", "06:00"); cA3 = clase(hA, "2026-09-20", "06:00")
    cA4 = clase(hA2, "2026-09-06", "07:00")
    cB1 = clase(hB, "2026-09-06", "06:00"); cB2 = clase(hB, "2026-09-13", "06:00")
    cC = clase(hC, "2026-11-22", "10:00")
    ok("las clases de prueba toman precio y cupo de su tipo (foto de la clase)",
       (float(cA1["precio"]), float(cB1["precio"]), cC["cupo_max"]) == (800, 950, 1), (cA1["precio"], cB1["precio"], cC["cupo_max"]))
    for c in (cA1, cA2, cB1):
        reserva(A0, c["id"])
    reserva(A0, cA3["id"], "cancelada")
    reserva(A1, cA1["id"]); reserva(A1, cB2["id"]); reserva(A1, cA4["id"])
    reserva(A2, cA2["id"])

    # ---------- facturación: profesora ----------
    r = prof.get("/profesora/resumenes")
    ok("profesora: el mes en curso (octubre) se muestra provisorio", r.status_code == 200 and "Provisorio" in r.text
       and "/profesora/resumenes/generar" not in r.text)
    ok("octubre todavía no admite borradores (fuera_de_revision hasta el 31)",
       cliente_rls(tokens_por_enlace("profesora@prueba.invalid")).rpc("sincronizar_borradores", {"p_mes": 10, "p_anio": 2026})
       .execute().data["resultado"] == "fuera_de_revision")
    ok("…y no se creó nada para octubre", admin.table("resumen_mensual").select("id").eq("mes", 10).execute().data == [])
    r = prof.get("/profesora/resumenes?mes=9&anio=2026")
    filas = admin.table("resumen_mensual").select("*").execute().data
    creado["facturas"] = [f["id"] for f in filas]
    guardar_registro()
    ok("septiembre: se generan borradores solo para las 3 alumnas de prueba con clases",
       r.status_code == 200 and {f["id_alumna"] for f in filas} == {A0, A1, A2} and all(f["estado_factura"] == "borrador" for f in filas),
       [(f["id_alumna"], f["estado_factura"]) for f in filas])
    F0, F1, F2 = (factura_de(a)[0] for a in (A0, A1, A2))
    ok("A0: 3 clases (la cancelada no se cobra) = 800 + 800 + 950", (F0["cantidad_clases"], float(F0["total"])) == (3, 2550.0))
    ced = "12345672"
    rr = prof.post(f"/profesora/alumnas/{A0}/cedula", data={"cedula": "1.234.567-2"})
    creado["cedulas_cargadas"].append(A0)
    guardar_registro()
    ok("cargar la cédula de la alumna de prueba desde Alumnas", aviso(rr)[0] == "1"
       and admin.table("profiles").select("cedula").eq("id", A0).execute().data[0]["cedula"] == ced)
    t = prof.get(f"/profesora/resumenes/{F0['id']}").text
    nombre0 = f"{perfiles['alumna@prueba.invalid']['nombre']} {perfiles['alumna@prueba.invalid']['apellido']}"
    ok("detalle: nombre completo, cédula y período", f"<dt>Alumna</dt><dd>{nombre0}</dd>" in t
       and "<dt>Cédula</dt><dd>1.234.567-2</dd>" in t and "<dt>Período</dt><dd>Setiembre 2026</dd>" in t)
    t1 = prof.get(f"/profesora/resumenes/{F1['id']}").text
    ok("detalle sin cédula: 'no registrada' y aviso", "<dd>no registrada</dd>" in t1 and "no tiene cédula registrada" in t1)

    L0 = lineas(F0["id"])
    r = prof.post(f"/profesora/resumenes/{F0['id']}/lineas/{L0[cB1['id']]['id']}/ajustar",
                  data={"version": version_de(prof, F0["id"]), "tipo": "bonificada", "motivo": "PRUEBA bonificación"})
    ok("bonificar una clase", aviso(r)[0] == "1", aviso(r))
    r = prof.post(f"/profesora/resumenes/{F0['id']}/lineas/{L0[cA2['id']]['id']}/ajustar",
                  data={"version": version_de(prof, F0["id"]), "tipo": "importe", "importe": "500", "motivo": "PRUEBA importe"})
    ok("modificar el importe de una clase", aviso(r)[0] == "1", aviso(r))
    r = prof.post(f"/profesora/resumenes/{F0['id']}/descuento",
                  data={"version": version_de(prof, F0["id"]), "tipo": "porcentaje", "valor": "10", "motivo": "PRUEBA porcentaje"})
    f0 = admin.table("resumen_mensual").select("*").eq("id", F0["id"]).execute().data[0]
    ok("descuento por porcentaje: (800 + 500 + 0) - 10% = 1170, original 2550",
       aviso(r)[0] == "1" and (float(f0["total"]), float(f0["subtotal_original"])) == (1170, 2550), (f0["total"], f0["subtotal_original"]))
    r = prof.post(f"/profesora/resumenes/{F1['id']}/descuento",
                  data={"version": version_de(prof, F1["id"]), "tipo": "monto", "valor": "100", "motivo": "PRUEBA monto"})
    f1 = admin.table("resumen_mensual").select("*").eq("id", F1["id"]).execute().data[0]
    ok("descuento por monto: 800 + 950 + 800 - 100 = 2450", aviso(r)[0] == "1" and float(f1["total"]) == 2450, f1["total"])
    r = prof.post(f"/profesora/resumenes/{F0['id']}/lineas/{L0[cA1['id']]['id']}/ajustar",
                  data={"version": 1, "tipo": "bonificada", "motivo": "versión vieja"})
    ok("una modificación con versión vieja se rechaza", aviso(r)[0] == "0" and "cambió" in aviso(r)[1])
    L2 = lineas(F2["id"])
    prof.post(f"/profesora/resumenes/{F2['id']}/lineas/{L2[cA2['id']]['id']}/ajustar",
              data={"version": version_de(prof, F2["id"]), "tipo": "bonificada", "motivo": "PRUEBA sin cargo"})

    r = prof.post(f"/profesora/resumenes/{F1['id']}/confirmar", data={"version": version_de(prof, F1["id"])})
    f1 = admin.table("resumen_mensual").select("*").eq("id", F1["id"]).execute().data[0]
    ok("confirmación individual: número y foto (sin cédula)", aviso(r)[0] == "1" and f1["estado_factura"] == "confirmada"
       and f1["numero"].startswith("JSU-2026-09-") and f1["alumna_cedula"] is None and f1["alumna_nombre"], aviso(r))
    t = prof.get(f"/profesora/resumenes/{F1['id']}").text
    ok("confirmada: ya no se puede ajustar ni cambiar el descuento", "/ajustar" not in t and "/descuento" not in t)
    t = prof.get("/profesora/resumenes/confirmar-mes?mes=9&anio=2026").text
    firma = re.search(r'name="firma" value="([^"]+)"', t)
    ok("vista previa masiva: 2 facturas, total y aviso de 1 sin cédula",
       "Vas a confirmar <strong>2 facturas</strong>" in t and "$1170.00" in t and "1 factura es de una alumna sin cédula registrada" in t and firma)
    r = prof.post("/profesora/resumenes/confirmar-mes", data={"mes": 9, "anio": 2026, "firma": "vieja"})
    ok("confirmación masiva con firma vieja: no confirma ninguna", aviso(r)[0] == "0"
       and factura_de(A0)[0]["estado_factura"] == "borrador")
    r = prof.post("/profesora/resumenes/confirmar-mes", data={"mes": 9, "anio": 2026, "firma": firma.group(1)})
    f0, f2 = factura_de(A0)[0], factura_de(A2)[0]
    ok("confirmación masiva: 2 confirmadas; la de $0 queda 'sin cargo'", aviso(r)[0] == "1" and f0["estado_factura"] == "confirmada"
       and f2["estado_pago"] == "sin_cargo" and f0["alumna_cedula"] == ced, aviso(r))

    t = prof.get(f"/profesora/resumenes/{F0['id']}").text
    total0 = re.search(r'name="monto" value="([^"]+)"', t).group(1)
    r = prof.post(f"/profesora/resumenes/{F0['id']}/pagar", data={"monto": "1", "medio_pago": "efectivo", "fecha": date.today().isoformat()})
    ok("pago parcial rechazado", aviso(r)[0] == "0")
    r = prof.post(f"/profesora/resumenes/{F0['id']}/pagar", data={"monto": total0, "medio_pago": "efectivo", "fecha": "2099-01-01"})
    ok("pago con fecha futura rechazado", aviso(r)[0] == "0")
    r = prof.post(f"/profesora/resumenes/{F0['id']}/pagar", data={"monto": total0, "medio_pago": "efectivo", "fecha": date.today().isoformat()})
    r2 = prof.post(f"/profesora/resumenes/{F0['id']}/pagar", data={"monto": total0, "medio_pago": "efectivo", "fecha": date.today().isoformat()})
    pagos = admin.table("pago").select("*").eq("id_resumen_mensual", F0["id"]).execute().data
    ok("registrar pago (total exacto) y un segundo envío no duplica", aviso(r)[0] == "1" and aviso(r2)[0] == "0" and len(pagos) == 1)
    r = prof.post(f"/profesora/resumenes/{F0['id']}/pagos/{pagos[0]['id']}/anular", data={"motivo": "PRUEBA anulación de pago"})
    an = admin.table("pago_anulacion").select("*").eq("id_pago", pagos[0]["id"]).execute().data
    ok("anular el pago con motivo: queda en el historial", aviso(r)[0] == "1" and an and an[0]["motivo"] == "PRUEBA anulación de pago"
       and factura_de(A0)[0]["estado_pago"] == "pendiente")
    r = prof.post(f"/profesora/resumenes/{F0['id']}/pagar", data={"monto": total0, "medio_pago": "transferencia", "fecha": date.today().isoformat()})
    ok("nuevo pago después de anular: dos pagos identificados", aviso(r)[0] == "1"
       and sorted(p["estado"] for p in admin.table("pago").select("estado").eq("id_resumen_mensual", F0["id"]).execute().data) == ["anulado", "vigente"])

    r = prof.post(f"/profesora/resumenes/{F1['id']}/anular", data={"motivo": "PRUEBA anular y reemitir"})
    nueva = factura_de(A1, "borrador")
    ok("anular y reemitir: la anterior anulada y un borrador nuevo con las mismas clases",
       aviso(r)[0] == "1" and factura_de(A1, "anulada") and nueva and nueva[0]["reemplaza_a"] == F1["id"]
       and float(nueva[0]["total"]) == 2450, aviso(r))
    N1 = nueva[0]["id"]
    creado["facturas"].append(N1)
    guardar_registro()
    LN = lineas(N1)
    prof.post(f"/profesora/resumenes/{N1}/lineas/{LN[cA4['id']]['id']}/ajustar",
              data={"version": version_de(prof, N1), "tipo": "bonificada", "motivo": "PRUEBA corrección"})
    r = prof.post(f"/profesora/resumenes/{N1}/confirmar", data={"version": version_de(prof, N1)})
    n1 = admin.table("resumen_mensual").select("*").eq("id", N1).execute().data[0]
    ok("confirmar el reemplazo: número nuevo y total corregido (1650)", aviso(r)[0] == "1" and n1["numero"] != f1["numero"]
       and float(n1["total"]) == 1650, (aviso(r), n1["total"]))

    antes = {f["id"]: (f["total"], f["subtotal_original"]) for f in admin.table("resumen_mensual").select("*").execute().data}
    lin_antes = sorted((l["id"], l["precio_original"], l["importe_final"]) for l in todas("resumen_mensual_detalle"))
    rr = cliente_rls(tokens_por_enlace("profesora@prueba.invalid")).rpc("aplicar_cambio_tipo", {
        "p_id": tA, "p_nombre": f"PRUEBA-M6-A-{sufijo}", "p_descripcion": "prueba módulo 6", "p_duracion": 60,
        "p_precio": 1200, "p_cupo": 7}).execute().data
    ok("cambiar el precio del tipo de clase después de facturar no cambia las facturas",
       rr["resultado"] == "ok" and antes == {f["id"]: (f["total"], f["subtotal_original"]) for f in admin.table("resumen_mensual").select("*").execute().data}
       and lin_antes == sorted((l["id"], l["precio_original"], l["importe_final"]) for l in todas("resumen_mensual_detalle")))

    # ---------- facturación: alumna ----------
    t = alu.get("/mis-facturas").text
    ok("alumna: Mis facturas carga y muestra solo su factura de setiembre", "Setiembre 2026" in t and f0["numero"] in t
       and f1["numero"] not in t and n1["numero"] not in t)
    ok("alumna: nombre, cédula, clases, descuentos, total y estado de pago",
       all(x in t for x in ("<dt>Cédula</dt><dd>1.234.567-2</dd>", "Bonificada", "Descuento", "Descuento general (10%)",
                            "$1170.00", "Pagada")))
    ok("alumna: no ve motivos internos", "PRUEBA bonificación" not in t and "PRUEBA porcentaje" not in t and "PRUEBA anulación" not in t)
    r = alu.get(f"/mis-facturas/{F0['id']}/pdf")
    tp = texto_pdf(r.content) if r.headers.get("content-type") == "application/pdf" else ""
    ok("alumna: descarga el PDF con número, cédula y total", r.status_code == 200 and f0["numero"] in tp and "1.234.567-2" in tp
       and "$ 1.170,00" in tp)
    r = alu.get(f"/mis-facturas/{F1['id']}/pdf")
    ok("alumna: no puede bajar la factura de otra alumna", r.status_code == 303 and r.headers["location"].startswith("/mis-facturas?"))
    ok("alumna: no entra a la facturación de la profesora", alu.get("/profesora/resumenes").status_code == 303)
    rls0 = cliente_rls(tokens_por_enlace("alumna@prueba.invalid"))
    ok("alumna (directo a Supabase): solo ve su factura, sus líneas y ningún historial",
       {f["id"] for f in rls0.table("resumen_mensual").select("id").execute().data} == {F0["id"]}
       and {l["id_resumen_mensual"] for l in rls0.table("resumen_mensual_detalle").select("id_resumen_mensual").execute().data} == {F0["id"]}
       and rls0.table("factura_historial").select("id").execute().data == []
       and rls0.table("pago_anulacion").select("id_pago").execute().data == [])
    t1 = alu1.get("/mis-facturas").text
    ok("alumna sin cédula: ve la anulada y la nueva, con 'no registrada'", f1["numero"] in t1 and n1["numero"] in t1
       and "Anulada" in t1 and "no registrada" in t1)
    t2 = alu2.get("/mis-facturas").text
    ok("factura en $0: la alumna la ve 'Sin cargo'", "Sin cargo" in t2)

    # pausa opcional para revisar las pantallas en el navegador con los datos de prueba
    if os.environ.get("PAUSA_NAVEGADOR"):
        marca = os.environ["PAUSA_NAVEGADOR"]
        print(f"PAUSA: datos listos; para seguir crear {marca}", flush=True)
        for _ in range(1800):
            if os.path.exists(marca):
                break
            time.sleep(1)

    # ---------- regresión ----------
    c = httpx.Client(base_url=BASE, follow_redirects=False)
    r = c.post("/login", data={"mail": "profesora@prueba.invalid", "contrasena": "incorrecta-" + sufijo})
    ok("login: contraseña incorrecta rechazada", r.status_code == 401)
    ok("login: sin sesión se va al login", c.get("/mis-facturas").headers.get("location", "").startswith("/login"))
    ok("login: profesora y alumna entran (sesión válida)", prof.get("/profesora/resumenes").status_code == 200
       and alu.get("/mis-facturas").status_code == 200)
    t = prof.get("/profesora/alumnas").text
    ok("alumnas: la lista carga con la columna Cédula", "<th>Cédula</th>" in t and "1.234.567-2" in t)
    mail_nueva = f"regresion.m6.{sufijo}@prueba.invalid"
    r = prof.post("/profesora/alumnas", data={"nombre": "Regresion", "apellido": "Prueba", "mail": mail_nueva, "cedula": "1.234.567-2"})
    ok("alumnas: alta con cédula repetida rechazada (no crea la cuenta)", r.status_code == 400
       and not [u for u in admin.auth.admin.list_users(per_page=1000) if u.email == mail_nueva])
    r = prof.post("/profesora/alumnas", data={"nombre": "Regresion", "apellido": "Prueba", "mail": mail_nueva, "cedula": "9.876.543-8"})
    nuevas = [u for u in admin.auth.admin.list_users(per_page=1000) if u.email == mail_nueva]
    creado["usuarias_nuevas"] += [u.id for u in nuevas]
    guardar_registro()
    ok("alumnas: alta con cédula válida", r.status_code == 200 and "Alumna creada" in r.text and nuevas
       and admin.table("profiles").select("cedula").eq("id", nuevas[0].id).execute().data[0]["cedula"] == "98765438")
    r = prof.post(f"/profesora/alumnas/{nuevas[0].id}/reiniciar-contrasena")
    ok("alumnas: reiniciar contraseña", r.status_code == 200 and "Contraseña reiniciada" in r.text)

    r = alu.post(f"/clases/{cC['id']}/inscribirse", data={"fecha": "2026-11-22"})
    rs = admin.table("reserva").select("*").eq("id_clase", cC["id"]).execute().data
    creado["reservas"] += [x["id"] for x in rs if x["id"] not in creado["reservas"]]
    guardar_registro()
    ok("reservas: reservar una clase (cupo 1)", aviso(r)[0] == "1" and [x["estado"] for x in rs] == ["confirmada"], aviso(r))
    r = alu1.post(f"/clases/{cC['id']}/inscribirse", data={"fecha": "2026-11-22"})
    ok("reservas: con el cupo lleno no se puede reservar", aviso(r)[0] == "0")
    r = alu1.post(f"/clases/{cC['id']}/lista-espera", data={"fecha": "2026-11-22"})
    ok("lista de espera: anotarse", aviso(r)[0] == "1", aviso(r))
    r = alu.post(f"/clases/{cC['id']}/cancelar", data={"fecha": "2026-11-22"})
    le = admin.table("listadeespera").select("*").eq("id_clase", cC["id"]).execute().data
    ok("reservas: cancelar libera el lugar y se le ofrece a la primera de la lista", aviso(r)[0] == "1"
       and [x["estado"] for x in le] == ["ofrecido"], (aviso(r), le))
    r = alu1.post(f"/clases/{cC['id']}/confirmar-oferta", data={"fecha": "2026-11-22"})
    rs = admin.table("reserva").select("*").eq("id_clase", cC["id"]).eq("estado", "confirmada").execute().data
    creado["reservas"] += [x["id"] for x in rs if x["id"] not in creado["reservas"]]
    guardar_registro()
    ok("lista de espera: confirmar la oferta deja la reserva confirmada", aviso(r)[0] == "1" and [x["id_alumna"] for x in rs] == [A1])

    ok("tipos de clase: la pantalla carga", prof.get("/profesora/tipos-clase").status_code == 200)
    ok("asistencia: la pantalla Clases de la profesora carga (semanas ya generadas)", prof.get("/profesora/clases").status_code == 200)
    ok("planificación: la pantalla carga", prof.get("/profesora/planificacion").status_code == 200)
    datos_b = {"desde": "2026-11-22", "hasta": "2026-11-22", "id_horario": "", "motivo": f"PRUEBA-M6 {sufijo}"}
    r = prof.post("/profesora/planificacion/revisar", data=datos_b)
    ok("planificación: la vista previa muestra solo la clase de prueba", r.status_code == 200 and "10:00" in r.text)
    r = prof.post("/profesora/planificacion/confirmar", data={**datos_b, "reservas_previstas": "1"})
    bl = [b for b in todas("bloqueo") if b["motivo"].startswith("PRUEBA-M6")]
    creado["bloqueos"] += [b["id"] for b in bl]
    guardar_registro()
    cc = admin.table("clase").select("estado").eq("id", cC["id"]).execute().data[0]
    ok("planificación: el bloqueo cancela la clase de prueba y su reserva", aviso(r)[0] == "1" and cc["estado"] == "cancelada"
       and not admin.table("reserva").select("id").eq("id_clase", cC["id"]).eq("estado", "confirmada").execute().data, aviso(r))
    ok("planificación: ninguna clase real afectada", not [x for x in admin.table("clase").select("id, estado").eq("fecha", "2026-11-22").execute().data
                                                       if x["id"] not in creado["clases"]])
    r = prof.post(f"/profesora/planificacion/{bl[0]['id']}/eliminar")
    ok("planificación: quitar el bloqueo recupera la clase y la reserva", aviso(r)[0] == "1"
       and admin.table("clase").select("estado").eq("id", cC["id"]).execute().data[0]["estado"] == "programada"
       and admin.table("reserva").select("id").eq("id_clase", cC["id"]).eq("estado", "confirmada").execute().data)
    pantalla = prof.get(f"/profesora/clases/{cC['id']}")
    rid = admin.table("reserva").select("id").eq("id_clase", cC["id"]).eq("estado", "confirmada").execute().data[0]["id"]
    r = prof.post(f"/profesora/clases/{cC['id']}/asistencia", data={"id_reserva": rid, "estado": "presente"})
    ok("asistencia: una clase a más de 7 días no abre el detalle y antes de la clase no se puede marcar",
       pantalla.status_code == 303 and aviso(r)[0] == "0"
       and not admin.table("asistencia").select("id").eq("id_reserva", rid).execute().data)
finally:
    # ---------- limpieza: solo lo creado ----------
    print("\n--- limpieza ---", flush=True)
    guardar_registro()
    facturas = {f["id"] for f in admin.table("resumen_mensual").select("id, id_alumna").execute().data if f["id_alumna"] in PRUEBA_IDS}
    ajenas = [f for f in admin.table("resumen_mensual").select("id, id_alumna").execute().data if f["id_alumna"] not in PRUEBA_IDS]
    if ajenas:
        print("ATENCIÓN: hay facturas de alumnas que no son de prueba; NO se borran:", ajenas)
    pagos = [p["id"] for p in admin.table("pago").select("id, id_resumen_mensual").execute().data if p["id_resumen_mensual"] in facturas]
    if pagos:
        admin.table("pago_anulacion").delete().in_("id_pago", pagos).execute()
        admin.table("pago").delete().in_("id", pagos).execute()
    if facturas:
        admin.table("factura_historial").delete().in_("id_resumen_mensual", list(facturas)).execute()
        admin.table("resumen_mensual_detalle").delete().in_("id_resumen_mensual", list(facturas)).execute()
        # primero las que reemplazan a otras (referencian a la anterior)
        for f in sorted(admin.table("resumen_mensual").select("id, reemplaza_a").in_("id", list(facturas)).execute().data,
                        key=lambda f: f["reemplaza_a"] is None):
            admin.table("resumen_mensual").delete().eq("id", f["id"]).execute()
    if creado["clases"]:
        rs = [x["id"] for x in admin.table("reserva").select("id").in_("id_clase", creado["clases"]).execute().data]
        if rs:
            admin.table("asistencia").delete().in_("id_reserva", rs).execute()
        admin.table("listadeespera").delete().in_("id_clase", creado["clases"]).execute()
        admin.table("reserva").delete().in_("id_clase", creado["clases"]).execute()
    for b in creado["bloqueos"]:
        admin.table("bloqueo").delete().eq("id", b).like("motivo", "PRUEBA-M6%").execute()
    if creado["clases"]:
        admin.table("clase").delete().in_("id", creado["clases"]).execute()
    if creado["horarios"]:
        admin.table("horario").delete().in_("id", creado["horarios"]).execute()
    if creado["tipos"]:
        admin.table("tipo_clase").delete().in_("id", creado["tipos"]).like("nombre", "PRUEBA-M6-%").execute()
    for uid in creado["usuarias_nuevas"]:
        admin.auth.admin.delete_user(uid)
    for uid in creado["cedulas_cargadas"]:
        admin.table("profiles").update({"cedula": None}).eq("id", uid).execute()
    for c in (prof, alu):
        try:
            c.post("/logout")
        except Exception:
            pass

    # ---------- verificación contra el respaldo ----------
    print("\n--- verificación final ---", flush=True)
    respaldo = json.load(open(os.path.join(RESPALDO, "datos.json")))
    norm = lambda filas, t: sorted((json.dumps({k: v for k, v in f.items() if not (t == "profiles" and k == "cedula")},
                                               sort_keys=True, default=str) for f in filas))
    for t in TABLAS:
        actual = todas(t)
        ok(f"{t}: idéntica al respaldo ({len(actual)} filas)", norm(actual, t) == norm(respaldo[t], t),
           f"{len(actual)} vs {len(respaldo[t])}")
    ok("tablas de facturación vacías (sin residuos)", all(admin.table(t).select("*", count="exact").limit(1).execute().count == 0 for t in FACT))
    ok("ninguna cédula cargada", not [p for p in todas("profiles") if p.get("cedula")])
    usuarios = admin.auth.admin.list_users(per_page=1000)
    ok("usuarias de auth: las 16 del respaldo", sorted(u.id for u in usuarios) == sorted(u["id"] for u in respaldo["auth_users"]))
    print(f"\n{sum(c for _, c in resultados)} OK · {sum(not c for _, c in resultados)} fallas")
