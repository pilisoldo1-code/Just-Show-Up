import logging
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, clases, estadisticas, validaciones
from app.auth import ErrorAuth, Redirigir, Usuario, requiere_alumna, requiere_login, requiere_profesora
from app.clases import DIAS_ORDEN

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Just Show Up - Horarios")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["nombre_dia"] = clases.nombre_dia

TABLE = "horario"
MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]
SEMANAS_HACIA_ADELANTE = 2
DIAS_ADELANTE_PROFESORA = 7  # la profesora ve inscriptas de hoy hasta el mismo día de la semana siguiente


@app.exception_handler(Redirigir)
async def _redirigir(request: Request, exc: Redirigir):
    respuesta = RedirectResponse(url=exc.url, status_code=303)
    if exc.borrar_cookies:
        auth.borrar_cookies(respuesta)
    return respuesta


@app.middleware("http")
async def _sesion_y_cache(request: Request, call_next):
    respuesta = await call_next(request)
    tokens = getattr(request.state, "tokens_nuevos", None)
    if tokens:
        auth.poner_cookies(respuesta, request, *tokens)
    if not request.url.path.startswith("/static"):
        respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


def _render(request: Request, plantilla: str, usuario: Usuario | None = None, status_code: int = 200, **contexto):
    contexto.update({"request": request, "usuario": usuario})
    return templates.TemplateResponse(plantilla, contexto, status_code=status_code)


def _destino(usuario: Usuario) -> str:
    return "/profesora/horarios" if usuario.es_profesora else "/"


def _rango_semana(offset: int) -> tuple[date, date]:
    hoy = clases.ahora().date()
    lunes_actual = hoy - timedelta(days=hoy.weekday())
    inicio = lunes_actual + timedelta(weeks=offset)
    fin = inicio + timedelta(days=6)
    return inicio, fin


def _formato_rango(inicio: date, fin: date) -> str:
    return f"{inicio.day} de {MESES[inicio.month]} al {fin.day} de {MESES[fin.month]}"


def _orden_horario(row: dict) -> tuple:
    return (DIAS_ORDEN.index(row["dia_semana"]), row["hora_inicio"])


def _sb(usuario: Usuario):
    """Cliente de Supabase con la sesión de la usuaria: las políticas RLS se aplican a ella."""
    return auth.cliente_con_token(usuario.token)


def _get_horarios(sb) -> list[dict]:
    result = sb.table(TABLE).select("*").eq("activo", True).execute()
    return sorted(result.data, key=_orden_horario)


def _parse_fecha(texto: str):
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _volver(fecha: str, ok: bool, aviso: str) -> RedirectResponse:
    query = urlencode({"ok": int(ok), "aviso": aviso})
    f = _parse_fecha(fecha)
    destino = f"/dia/{f.isoformat()}" if f else "/"
    return RedirectResponse(url=f"{destino}?{query}", status_code=303)


def _ejecutar(accion, usuario: Usuario, clase_id: int) -> tuple[bool, str]:
    try:
        return accion(_sb(usuario), clase_id)
    except Exception:
        logging.exception("Error en %s", accion.__name__)
        return False, "Ocurrió un error, probá de nuevo."


# ---------- sesion ----------

@app.get("/login")
def login_form(request: Request, ok: int = 0, aviso: str = ""):
    usuario = auth.usuario_opcional(request)
    if usuario is not None:
        return RedirectResponse(url=_destino(usuario), status_code=303)
    return _render(request, "login.html", aviso=aviso, aviso_ok=bool(ok), mail="")


@app.post("/login")
def login(request: Request, mail: str = Form(""), contrasena: str = Form("")):
    mail = mail.strip().lower()
    if not mail or not contrasena:
        return _render(request, "login.html", status_code=400, mail=mail, aviso_ok=False,
                       aviso="Ingresá tu mail y tu contraseña.")
    try:
        acceso, refresco = auth.iniciar_sesion(mail, contrasena)
        usuario = auth.usuario_de_acceso(acceso)
    except ErrorAuth as e:
        return _render(request, "login.html", status_code=401, mail=mail, aviso_ok=False, aviso=str(e))
    except Exception:
        logging.exception("Error inesperado al iniciar sesión")
        return _render(request, "login.html", status_code=503, mail=mail, aviso_ok=False, aviso=auth.MSG_CONEXION)
    if usuario is None:
        return _render(request, "login.html", status_code=403, mail=mail, aviso_ok=False,
                       aviso="Tu cuenta no está habilitada todavía. Avisale a tu profesora.")
    respuesta = RedirectResponse(url=_destino(usuario), status_code=303)
    auth.poner_cookies(respuesta, request, acceso, refresco)
    return respuesta


@app.post("/logout")
def logout(request: Request):
    auth.cerrar_sesion(request.cookies.get(auth.COOKIE_ACCESO))
    respuesta = RedirectResponse(
        url="/login?" + urlencode({"ok": 1, "aviso": "Cerraste sesión."}), status_code=303
    )
    auth.borrar_cookies(respuesta)
    return respuesta


@app.get("/olvide-contrasena")
def olvide_form(request: Request):
    return _render(request, "olvide_contrasena.html", mail="", enviado=False, aviso="")


@app.post("/olvide-contrasena")
def olvide(request: Request, mail: str = Form("")):
    mail = mail.strip().lower()
    problema = auth.validar_mail(mail) if mail else "Ingresá tu mail."
    if problema:
        return _render(request, "olvide_contrasena.html", status_code=400, mail=mail, enviado=False, aviso=problema)
    try:
        auth.pedir_recuperacion(mail, auth.url_restablecer(request))
    except ErrorAuth as e:
        return _render(request, "olvide_contrasena.html", status_code=429, mail=mail, enviado=False, aviso=str(e))
    return _render(request, "olvide_contrasena.html", mail=mail, enviado=True, aviso="")


@app.get("/restablecer-contrasena")
def restablecer_form(request: Request):
    return _render(request, "restablecer_contrasena.html", aviso="")


@app.post("/restablecer-contrasena")
def restablecer(
    request: Request,
    acceso: str = Form(""),
    refresco: str = Form(""),
    nueva: str = Form(""),
    repetir: str = Form(""),
):
    if nueva != repetir:
        aviso = "Las contraseñas no coinciden."
    else:
        try:
            auth.restablecer_contrasena(acceso, refresco, nueva)
            aviso = ""
        except ErrorAuth as e:
            aviso = str(e)
    if aviso:
        return _render(request, "restablecer_contrasena.html", status_code=400, aviso=aviso,
                       vencido=aviso == auth.MSG_LINK_INVALIDO, acceso=acceso, refresco=refresco)
    respuesta = RedirectResponse(
        url="/login?" + urlencode({"ok": 1, "aviso": "Listo, tu contraseña se cambió. Iniciá sesión con la nueva."}),
        status_code=303,
    )
    auth.borrar_cookies(respuesta)
    return respuesta


_MESES_CORTOS = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fecha_legible(iso: str | None) -> str:
    f = _parse_fecha(iso) if iso else None
    return f"{f.day} de {MESES[f.month]} de {f.year}" if f else ""


def _datos_perfil(usuario: Usuario) -> dict:
    datos = {"nombre": usuario.nombre, "apellido": usuario.apellido, "mail": usuario.mail,
             "telefono": "", "fecha_nacimiento": "", "fecha_vencimiento_carne_salud": ""}
    try:
        filas = (
            auth.cliente_con_token(usuario.token).table("profiles")
            .select("telefono, fecha_nacimiento, fecha_vencimiento_carne_salud")
            .eq("id", usuario.id).execute().data
        )
    except Exception:
        logging.exception("No se pudo leer el perfil")
        return datos
    if filas:
        datos["telefono"] = filas[0]["telefono"] or ""
        datos["fecha_nacimiento"] = _fecha_legible(filas[0]["fecha_nacimiento"])
        datos["fecha_vencimiento_carne_salud"] = _fecha_legible(filas[0]["fecha_vencimiento_carne_salud"])
    return datos


@app.get("/perfil")
def perfil(request: Request, ok: int = 0, aviso: str = "", usuario: Usuario = Depends(requiere_login)):
    return _render(request, "perfil.html", usuario, datos=_datos_perfil(usuario), aviso=aviso, aviso_ok=bool(ok))


@app.post("/perfil/contrasena")
def perfil_contrasena(
    request: Request,
    actual: str = Form(""),
    nueva: str = Form(""),
    repetir: str = Form(""),
    usuario: Usuario = Depends(requiere_login),
):
    aviso = ""
    if nueva != repetir:
        aviso = "Las contraseñas nuevas no coinciden."
    else:
        try:
            auth.cambiar_contrasena(usuario, actual, nueva)
        except ErrorAuth as e:
            aviso = str(e)
    if aviso:
        return _render(request, "perfil.html", usuario, status_code=400,
                       datos=_datos_perfil(usuario), aviso=aviso, aviso_ok=False)
    # Supabase cierra las sesiones al cambiar la contraseña: se abre una nueva para
    # que la usuaria no quede afuera apenas la cambia.
    try:
        request.state.tokens_nuevos = auth.iniciar_sesion(usuario.mail, nueva)
    except ErrorAuth:
        return RedirectResponse(
            url=auth.url_login("Tu contraseña se cambió. Iniciá sesión con la nueva."), status_code=303
        )
    return RedirectResponse(
        url="/perfil?" + urlencode({"ok": 1, "aviso": "Tu contraseña se cambió correctamente."}),
        status_code=303,
    )


# ---------- alumna ----------

@app.get("/")
def home(request: Request, semana: int = 0, ok: int = 0, aviso: str = "", usuario: Usuario = Depends(requiere_alumna)):
    semana = max(0, min(semana, SEMANAS_HACIA_ADELANTE))
    inicio, fin = _rango_semana(semana)
    sb = _sb(usuario)
    clases.generar_clases(sb, inicio)
    dias = clases.agrupar_por_dia(clases.clases_entre(sb, inicio, fin, usuario.id))
    return _render(
        request, "index.html", usuario,
        dias=dias,
        semana=semana,
        rango_semana=_formato_rango(inicio, fin),
        hay_semana_anterior=semana > 0,
        hay_semana_siguiente=semana < SEMANAS_HACIA_ADELANTE,
        fecha="",
        aviso=aviso,
        aviso_ok=bool(ok),
    )


@app.get("/dia/{fecha}")
def dia(request: Request, fecha: str, ok: int = 0, aviso: str = "", usuario: Usuario = Depends(requiere_alumna)):
    f = _parse_fecha(fecha)
    if f is None:
        return RedirectResponse(url="/", status_code=303)
    semana = clases.offset_semana(f)
    if not 0 <= semana <= SEMANAS_HACIA_ADELANTE:
        return RedirectResponse(url="/", status_code=303)
    sb = _sb(usuario)
    clases.generar_clases(sb, f - timedelta(days=f.weekday()))
    return _render(
        request, "dia.html", usuario,
        clases=clases.clases_entre(sb, f, f, usuario.id),
        titulo_dia=f"{clases.nombre_dia(f)} {f.day}/{f.month}",
        mensaje_plazo=clases.MENSAJE_PLAZO,
        semana=semana,
        fecha=f.isoformat(),
        aviso=aviso,
        aviso_ok=bool(ok),
    )


@app.get("/mis-inscripciones")
def mis_inscripciones(request: Request, usuario: Usuario = Depends(requiere_alumna)):
    try:
        datos = clases.mis_inscripciones(_sb(usuario), usuario.id)
        aviso = ""
    except Exception:
        logging.exception("No se pudieron cargar las inscripciones")
        datos, aviso = {"proximas": [], "en_espera": [], "historial": []}, "No pudimos cargar tus inscripciones. Probá de nuevo."
    return _render(request, "mis_inscripciones.html", usuario, aviso=aviso, aviso_ok=False, **datos)


@app.get("/mi-historial")
def mi_historial(request: Request, usuario: Usuario = Depends(requiere_alumna)):
    try:
        historial = clases.mis_inscripciones(_sb(usuario), usuario.id)["historial"]
        aviso = ""
    except Exception:
        logging.exception("No se pudo cargar el historial")
        historial, aviso = [], "No pudimos cargar tu historial. Probá de nuevo."
    return _render(
        request, "mi_historial.html", usuario, historial=historial, aviso=aviso, aviso_ok=False,
        presentes=sum(c["estado_txt"] == "Presente" for c in historial),
        ausentes=sum(c["estado_txt"] == "Ausente" for c in historial),
    )


@app.get("/mis-facturas")
def mis_facturas(request: Request, usuario: Usuario = Depends(requiere_alumna)):
    return _render(request, "mis_facturas.html", usuario)


@app.post("/clases/{clase_id}/inscribirse")
def inscribirse(clase_id: int, fecha: str = Form(""), usuario: Usuario = Depends(requiere_alumna)):
    ok, aviso = _ejecutar(clases.inscribirse, usuario, clase_id)
    return _volver(fecha, ok, aviso)


@app.post("/clases/{clase_id}/cancelar")
def cancelar_inscripcion(clase_id: int, fecha: str = Form(""), usuario: Usuario = Depends(requiere_alumna)):
    ok, aviso = _ejecutar(clases.cancelar, usuario, clase_id)
    return _volver(fecha, ok, aviso)


@app.post("/clases/{clase_id}/lista-espera")
def anotarse_en_espera(clase_id: int, fecha: str = Form(""), usuario: Usuario = Depends(requiere_alumna)):
    ok, aviso = _ejecutar(clases.anotar_en_espera, usuario, clase_id)
    return _volver(fecha, ok, aviso)


@app.get("/api/horario")
def api_horario(usuario: Usuario = Depends(requiere_login)):
    return _get_horarios(_sb(usuario))


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------- profesora: horarios ----------

@app.get("/profesora/horarios")
def profesora_horarios(request: Request, ok: int = 0, aviso: str = "", usuario: Usuario = Depends(requiere_profesora)):
    supabase = _sb(usuario)
    result = supabase.table(TABLE).select("*").execute()
    horarios = sorted(result.data, key=_orden_horario)
    return _render(request, "profesora_horarios.html", usuario, horarios=horarios,
                   aviso=aviso, aviso_ok=bool(ok))


@app.get("/profesora/horarios/nuevo")
def nuevo_horario_form(request: Request, usuario: Usuario = Depends(requiere_profesora)):
    return _render(
        request, "profesora_horario_form.html", usuario,
        horario=None, dias=DIAS_ORDEN, accion="/profesora/horarios/nuevo",
    )


@app.post("/profesora/horarios/nuevo")
def crear_horario(
    dia_semana: str = Form(...),
    hora_inicio: str = Form(...),
    duracion: int = Form(...),
    cupo_max: int = Form(...),
    precio: float = Form(...),
    activo: bool = Form(False),
    usuario: Usuario = Depends(requiere_profesora),
):
    supabase = _sb(usuario)
    supabase.table(TABLE).insert(
        {
            "dia_semana": dia_semana,
            "hora_inicio": hora_inicio,
            "duracion": duracion,
            "cupo_max": cupo_max,
            "precio": precio,
            "activo": activo,
        }
    ).execute()
    return RedirectResponse(url="/profesora/horarios", status_code=303)


@app.get("/profesora/horarios/{horario_id}/editar")
def editar_horario_form(request: Request, horario_id: int, usuario: Usuario = Depends(requiere_profesora)):
    supabase = _sb(usuario)
    result = supabase.table(TABLE).select("*").eq("id", horario_id).single().execute()
    return _render(
        request, "profesora_horario_form.html", usuario,
        horario=result.data, dias=DIAS_ORDEN, accion=f"/profesora/horarios/{horario_id}/editar",
    )


@app.post("/profesora/horarios/{horario_id}/editar")
def actualizar_horario(
    horario_id: int,
    dia_semana: str = Form(...),
    hora_inicio: str = Form(...),
    duracion: int = Form(...),
    cupo_max: int = Form(...),
    precio: float = Form(...),
    activo: bool = Form(False),
    usuario: Usuario = Depends(requiere_profesora),
):
    supabase = _sb(usuario)
    supabase.table(TABLE).update(
        {
            "dia_semana": dia_semana,
            "hora_inicio": hora_inicio,
            "duracion": duracion,
            "cupo_max": cupo_max,
            "precio": precio,
            "activo": activo,
        }
    ).eq("id", horario_id).execute()
    return RedirectResponse(url="/profesora/horarios", status_code=303)


@app.post("/profesora/horarios/{horario_id}/toggle")
def toggle_horario(horario_id: int, usuario: Usuario = Depends(requiere_profesora)):
    supabase = _sb(usuario)
    actual = supabase.table(TABLE).select("activo").eq("id", horario_id).single().execute()
    supabase.table(TABLE).update({"activo": not actual.data["activo"]}).eq(
        "id", horario_id
    ).execute()
    return RedirectResponse(url="/profesora/horarios", status_code=303)


# ---------- profesora: clases y asistencia ----------

def _dias_profesora() -> list[date]:
    hoy = clases.ahora().date()
    return [hoy + timedelta(days=i) for i in range(DIAS_ADELANTE_PROFESORA + 1)]


@app.get("/profesora/clases")
def profesora_clases(request: Request, fecha: str = "", usuario: Usuario = Depends(requiere_profesora)):
    rango = _dias_profesora()
    hoy = rango[0]
    sb = _sb(usuario)
    for lunes in {d - timedelta(days=d.weekday()) for d in (rango[0], rango[-1])}:
        clases.generar_clases(sb, lunes)
    # Solo los días que tienen clases: si mañana no hay (ej. sábado), se pasa al siguiente.
    dias = clases.dias_con_clases(sb, rango[0], rango[-1])
    if fecha:
        f = _parse_fecha(fecha)
        if f not in dias:
            return RedirectResponse(url="/profesora/clases", status_code=303)
    else:
        f = dias[0] if dias else None
    if f is None:
        return _render(request, "profesora_clases.html", usuario, clases=[], dias=[], titulo_dia="", es_hoy=True)
    etiquetas = {hoy: "Hoy", hoy + timedelta(days=1): "Mañana"}
    return _render(
        request, "profesora_clases.html", usuario,
        clases=clases.clases_del_dia(sb, f),
        titulo_dia=f"{clases.nombre_dia(f)} {f.day} de {MESES[f.month]}",
        es_hoy=f == hoy,
        dias=[
            {"fecha": d.isoformat(), "activo": d == f,
             "texto": etiquetas.get(d, f"{clases.nombre_dia(d)[:3]} {d.day}")}
            for d in dias
        ],
    )


@app.get("/profesora/clases/{clase_id}")
def profesora_clase(request: Request, clase_id: int, ok: int = 0, aviso: str = "",
                    usuario: Usuario = Depends(requiere_profesora)):
    clase = clases.clase_con_asistencia(_sb(usuario), clase_id)
    if clase is None or clase["fecha"] > _dias_profesora()[-1]:
        return RedirectResponse(url="/profesora/clases", status_code=303)
    return _render(request, "profesora_clase.html", usuario, clase=clase, aviso=aviso, aviso_ok=bool(ok))


@app.post("/profesora/clases/{clase_id}/asistencia")
def profesora_marcar(clase_id: int, id_reserva: int = Form(...), estado: str = Form(""),
                     usuario: Usuario = Depends(requiere_profesora)):
    try:
        ok, aviso = clases.marcar_asistencia(_sb(usuario), id_reserva, estado)
    except Exception:
        logging.exception("Error al marcar asistencia")
        ok, aviso = False, "Ocurrió un error, probá de nuevo."
    return RedirectResponse(
        url=f"/profesora/clases/{clase_id}?" + urlencode({"ok": int(ok), "aviso": aviso}), status_code=303
    )


# ---------- profesora: planificación (bloqueos) ----------

def _datos_bloqueo(desde: str, hasta: str, id_horario: str, motivo: str):
    try:
        horario = int(id_horario) if id_horario else None
    except ValueError:
        horario = -1  # no existe: lo rechaza la validación
    return _parse_fecha(desde), _parse_fecha(hasta), horario, motivo.strip()


def _render_planificacion(request: Request, usuario: Usuario, status_code: int = 200, **contexto):
    sb = _sb(usuario)
    hoy = clases.ahora().date()
    contexto.setdefault("datos", {})
    contexto.setdefault("errores", [])
    return _render(
        request, "profesora_planificacion.html", usuario, status_code=status_code,
        bloqueos=clases.bloqueos(sb), horarios=clases.horarios_para_elegir(sb),
        hoy_iso=hoy.isoformat(), largo_motivo=clases.LARGO_MAX_MOTIVO, **contexto,
    )


@app.get("/profesora/planificacion")
def profesora_planificacion(request: Request, ok: int = 0, aviso: str = "",
                            usuario: Usuario = Depends(requiere_profesora)):
    return _render_planificacion(request, usuario, aviso=aviso, aviso_ok=bool(ok))


@app.post("/profesora/planificacion/revisar")
def profesora_planificacion_revisar(
    request: Request,
    desde: str = Form(""),
    hasta: str = Form(""),
    id_horario: str = Form(""),
    motivo: str = Form(""),
    usuario: Usuario = Depends(requiere_profesora),
):
    sb = _sb(usuario)
    f_desde, f_hasta, horario, texto = _datos_bloqueo(desde, hasta, id_horario, motivo)
    errores = clases.validar_bloqueo(sb, f_desde, f_hasta, horario, texto)
    datos = {"desde": desde, "hasta": hasta, "id_horario": id_horario, "motivo": motivo.strip()}
    if errores:
        return _render_planificacion(request, usuario, status_code=400, datos=datos, errores=errores)
    horario_txt = next((h["texto"] for h in clases.horarios_para_elegir(sb) if h["id"] == horario), "")
    return _render(
        request, "profesora_planificacion_revisar.html", usuario,
        datos=datos, previa=clases.vista_previa_bloqueo(sb, f_desde, f_hasta, horario),
        rango_txt=clases._rango_texto(f_desde, f_hasta),
        alcance_txt=f"Solo {horario_txt}" if horario_txt else "Todas las clases",
    )


@app.post("/profesora/planificacion/confirmar")
def profesora_planificacion_confirmar(
    request: Request,
    desde: str = Form(""),
    hasta: str = Form(""),
    id_horario: str = Form(""),
    motivo: str = Form(""),
    reservas_previstas: str = Form(""),
    usuario: Usuario = Depends(requiere_profesora),
):
    sb = _sb(usuario)
    f_desde, f_hasta, horario, texto = _datos_bloqueo(desde, hasta, id_horario, motivo)
    errores = clases.validar_bloqueo(sb, f_desde, f_hasta, horario, texto)
    if errores:
        datos = {"desde": desde, "hasta": hasta, "id_horario": id_horario, "motivo": motivo.strip()}
        return _render_planificacion(request, usuario, status_code=400, datos=datos, errores=errores)
    try:
        ok, aviso = clases.confirmar_bloqueo(
            sb, f_desde, f_hasta, horario, texto,
            int(reservas_previstas) if reservas_previstas.isdigit() else None,
        )
    except Exception:
        logging.exception("Error al crear bloqueo")
        ok, aviso = False, "Ocurrió un error, probá de nuevo."
    return RedirectResponse(
        url="/profesora/planificacion?" + urlencode({"ok": int(ok), "aviso": aviso}), status_code=303
    )


@app.post("/profesora/planificacion/{bloqueo_id}/eliminar")
def profesora_planificacion_eliminar(bloqueo_id: int, usuario: Usuario = Depends(requiere_profesora)):
    try:
        ok, aviso = clases.eliminar_bloqueo(_sb(usuario), bloqueo_id)
    except Exception:
        logging.exception("Error al eliminar bloqueo")
        ok, aviso = False, "Ocurrió un error, probá de nuevo."
    return RedirectResponse(
        url="/profesora/planificacion?" + urlencode({"ok": int(ok), "aviso": aviso}), status_code=303
    )


# ---------- profesora: alumnas ----------

COLUMNAS_ALUMNA = "id, nombre, apellido, mail, telefono, fecha_nacimiento, fecha_vencimiento_carne_salud"


@app.get("/profesora/alumnas")
def alumnas_lista(request: Request, ok: int = 0, aviso: str = "", usuario: Usuario = Depends(requiere_profesora)):
    try:
        filas = (
            auth.cliente_con_token(usuario.token).table("profiles").select(COLUMNAS_ALUMNA)
            .eq("rol", "alumna").order("apellido").order("nombre").execute().data
        )
    except Exception:
        logging.exception("No se pudo listar alumnas")
        filas, aviso, ok = [], "No pudimos cargar la lista de alumnas. Probá de nuevo.", 0
    return _render(request, "profesora_alumnas.html", usuario, alumnas=filas, aviso=aviso, aviso_ok=bool(ok))


def _fechas_formulario() -> dict:
    hoy = clases.ahora().date()
    return {"ayer_iso": (hoy - timedelta(days=1)).isoformat(), "manana_iso": (hoy + timedelta(days=1)).isoformat()}


@app.get("/profesora/alumnas/nueva")
def alumna_nueva_form(request: Request, usuario: Usuario = Depends(requiere_profesora)):
    return _render(request, "profesora_alumna_nueva.html", usuario, datos={}, errores=[], **_fechas_formulario())


@app.post("/profesora/alumnas")
def alumna_crear(
    request: Request,
    nombre: str = Form(""),
    apellido: str = Form(""),
    mail: str = Form(""),
    telefono: str = Form(""),
    fecha_nacimiento: str = Form(""),
    fecha_vencimiento_carne_salud: str = Form(""),
    usuario: Usuario = Depends(requiere_profesora),
):
    ingresados = {
        "nombre": nombre, "apellido": apellido, "mail": mail, "telefono": telefono,
        "fecha_nacimiento": fecha_nacimiento, "fecha_vencimiento_carne_salud": fecha_vencimiento_carne_salud,
    }
    datos, errores = validaciones.validar_alumna(ingresados, clases.ahora().date())
    if not errores:
        try:
            _, contrasena = auth.crear_cuenta(
                datos["mail"], datos["nombre"], datos["apellido"],
                extras={k: datos[k] for k in ("telefono", "fecha_nacimiento", "fecha_vencimiento_carne_salud")},
            )
        except ErrorAuth as e:
            errores = [str(e)]
        except Exception:
            logging.exception("Error al crear alumna")
            errores = [auth.MSG_GENERICO]
    if errores:
        return _render(request, "profesora_alumna_nueva.html", usuario, status_code=400,
                       datos={k: v.strip() for k, v in ingresados.items()}, errores=errores, **_fechas_formulario())
    return _render(
        request, "alumna_credenciales.html", usuario,
        titulo="Alumna creada", nombre=f"{datos['nombre']} {datos['apellido']}",
        mail=datos["mail"], contrasena=contrasena,
    )


@app.post("/profesora/alumnas/{alumna_id}/reiniciar-contrasena")
def alumna_reiniciar(request: Request, alumna_id: str, usuario: Usuario = Depends(requiere_profesora)):
    try:
        uuid.UUID(alumna_id)
    except ValueError:
        return RedirectResponse(
            url="/profesora/alumnas?" + urlencode({"ok": 0, "aviso": "No encontramos a esa alumna."}),
            status_code=303,
        )
    filas = (
        auth.cliente_con_token(usuario.token).table("profiles").select("nombre, apellido, mail, rol")
        .eq("id", alumna_id).execute().data
    )
    if not filas or filas[0]["rol"] != "alumna":
        return RedirectResponse(
            url="/profesora/alumnas?" + urlencode({"ok": 0, "aviso": "No encontramos a esa alumna."}),
            status_code=303,
        )
    try:
        contrasena = auth.reiniciar_contrasena(alumna_id)
    except ErrorAuth as e:
        return RedirectResponse(
            url="/profesora/alumnas?" + urlencode({"ok": 0, "aviso": str(e)}), status_code=303
        )
    a = filas[0]
    return _render(
        request, "alumna_credenciales.html", usuario,
        titulo="Contraseña reiniciada", nombre=f"{a['nombre']} {a['apellido']}",
        mail=a["mail"], contrasena=contrasena,
    )


# ---------- profesora: estadísticas ----------

@app.get("/profesora/estadisticas")
def profesora_estadisticas(request: Request, periodo: str = estadisticas.PERIODO_DEFECTO,
                           usuario: Usuario = Depends(requiere_profesora)):
    if periodo not in estadisticas.PERIODOS:
        periodo = estadisticas.PERIODO_DEFECTO
    try:
        datos, aviso = estadisticas.estadisticas(_sb(usuario), periodo), ""
    except Exception:
        logging.exception("No se pudieron calcular las estadísticas")
        datos, aviso = None, "No pudimos cargar las estadísticas. Probá de nuevo."
    return _render(request, "profesora_estadisticas.html", usuario, datos=datos, periodo=periodo,
                   periodos=estadisticas.PERIODOS, aviso=aviso, aviso_ok=False)
