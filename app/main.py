from pathlib import Path

from fastapi import FastAPI, Request
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
