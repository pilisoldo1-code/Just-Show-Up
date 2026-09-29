from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import get_supabase

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="POC Supabase + FastAPI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

TABLE = "items"


@app.get("/")
def home(request: Request):
    supabase = get_supabase()
    result = supabase.table(TABLE).select("*").order("id", desc=True).execute()
    return templates.TemplateResponse(
        "index.html", {"request": request, "items": result.data}
    )


@app.post("/items")
def create_item(title: str = Form(...)):
    supabase = get_supabase()
    supabase.table(TABLE).insert({"title": title}).execute()
    return RedirectResponse(url="/", status_code=303)


@app.post("/items/{item_id}/delete")
def delete_item(item_id: int):
    supabase = get_supabase()
    supabase.table(TABLE).delete().eq("id", item_id).execute()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/health")
def health():
    return {"status": "ok"}
