from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import get_supabase

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Just Show Up - Horarios")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

TABLE = "horario"
DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _orden_horario(row: dict) -> tuple:
    return (DIAS_ORDEN.index(row["dia_semana"]), row["hora_inicio"])


def _get_horarios() -> list[dict]:
    supabase = get_supabase()
    result = supabase.table(TABLE).select("*").eq("activo", True).execute()
    return sorted(result.data, key=_orden_horario)


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "horarios": _get_horarios()}
    )


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
