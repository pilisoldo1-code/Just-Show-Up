import logging
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import clases
from app.clases import ALUMNAS_PROVISORIAS, DIAS_ORDEN
from app.db import get_supabase

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Just Show Up - Horarios")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

TABLE = "horario"
MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "setiembre", "octubre", "noviembre", "diciembre",
]
SEMANAS_HACIA_ADELANTE = 2


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


def _get_horarios() -> list[dict]:
    supabase = get_supabase()
    result = supabase.table(TABLE).select("*").eq("activo", True).execute()
    return sorted(result.data, key=_orden_horario)


def _alumna_actual(request: Request) -> str:
    alumna_id = request.cookies.get("alumna_id")
    if alumna_id in ALUMNAS_PROVISORIAS:
        return alumna_id
    return next(iter(ALUMNAS_PROVISORIAS))


def _volver(semana: int, ok: bool, aviso: str) -> RedirectResponse:
    query = urlencode({"semana": semana, "ok": int(ok), "aviso": aviso})
    return RedirectResponse(url=f"/?{query}", status_code=303)


def _ejecutar(accion, alumna_id: str, clase_id: int) -> tuple[bool, str]:
    try:
        return accion(alumna_id, clase_id)
    except Exception:
        logging.exception("Error en %s", accion.__name__)
        return False, "Ocurrió un error, probá de nuevo."


@app.get("/")
def home(request: Request, semana: int = 0, ok: int = 0, aviso: str = ""):
    semana = max(0, min(semana, SEMANAS_HACIA_ADELANTE))
    inicio, fin = _rango_semana(semana)
    alumna_id = _alumna_actual(request)
    clases.generar_clases(inicio)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "tarjetas": clases.tarjetas_semana(inicio, fin, alumna_id),
            "semana": semana,
            "rango_semana": _formato_rango(inicio, fin),
            "hay_semana_anterior": semana > 0,
            "hay_semana_siguiente": semana < SEMANAS_HACIA_ADELANTE,
            "alumnas": ALUMNAS_PROVISORIAS,
            "alumna_id": alumna_id,
            "aviso": aviso,
            "aviso_ok": bool(ok),
        },
    )


@app.post("/alumna/elegir")
def elegir_alumna(alumna_id: str = Form(...), semana: int = Form(0)):
    respuesta = RedirectResponse(url=f"/?semana={semana}", status_code=303)
    if alumna_id in ALUMNAS_PROVISORIAS:
        respuesta.set_cookie(
            "alumna_id", alumna_id, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax"
        )
    return respuesta


@app.post("/clases/{clase_id}/inscribirse")
def inscribirse(request: Request, clase_id: int, semana: int = Form(0)):
    ok, aviso = _ejecutar(clases.inscribirse, _alumna_actual(request), clase_id)
    return _volver(semana, ok, aviso)


@app.post("/clases/{clase_id}/cancelar")
def cancelar_inscripcion(request: Request, clase_id: int, semana: int = Form(0)):
    ok, aviso = _ejecutar(clases.cancelar, _alumna_actual(request), clase_id)
    return _volver(semana, ok, aviso)


@app.post("/clases/{clase_id}/lista-espera")
def anotarse_en_espera(request: Request, clase_id: int, semana: int = Form(0)):
    ok, aviso = _ejecutar(clases.anotar_en_espera, _alumna_actual(request), clase_id)
    return _volver(semana, ok, aviso)


@app.get("/api/horario")
def api_horario():
    return _get_horarios()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/profesora/horarios")
def profesora_horarios(request: Request):
    supabase = get_supabase()
    result = supabase.table(TABLE).select("*").execute()
    horarios = sorted(result.data, key=_orden_horario)
    return templates.TemplateResponse(
        "profesora_horarios.html", {"request": request, "horarios": horarios}
    )


@app.get("/profesora/horarios/nuevo")
def nuevo_horario_form(request: Request):
    return templates.TemplateResponse(
        "profesora_horario_form.html",
        {
            "request": request,
            "horario": None,
            "dias": DIAS_ORDEN,
            "accion": "/profesora/horarios/nuevo",
        },
    )


@app.post("/profesora/horarios/nuevo")
def crear_horario(
    dia_semana: str = Form(...),
    hora_inicio: str = Form(...),
    duracion: int = Form(...),
    cupo_max: int = Form(...),
    precio: float = Form(...),
    activo: bool = Form(False),
):
    supabase = get_supabase()
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
def editar_horario_form(request: Request, horario_id: int):
    supabase = get_supabase()
    result = supabase.table(TABLE).select("*").eq("id", horario_id).single().execute()
    return templates.TemplateResponse(
        "profesora_horario_form.html",
        {
            "request": request,
            "horario": result.data,
            "dias": DIAS_ORDEN,
            "accion": f"/profesora/horarios/{horario_id}/editar",
        },
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
):
    supabase = get_supabase()
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
def toggle_horario(horario_id: int):
    supabase = get_supabase()
    actual = supabase.table(TABLE).select("activo").eq("id", horario_id).single().execute()
    supabase.table(TABLE).update({"activo": not actual.data["activo"]}).eq(
        "id", horario_id
    ).execute()
    return RedirectResponse(url="/profesora/horarios", status_code=303)
