"""RESA Pronostics 2026 — FastAPI application entry point."""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db
from app.routers import admin, api, pages

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    logger.info("Database initialized.")
    yield


app = FastAPI(
    title="RESA Pronostics 2026",
    description="World Cup 2026 prediction game",
    version="1.0.0",
    lifespan=lifespan,
)

# Session middleware (required for admin auth + flash messages)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    max_age=86400 * 7,  # 7 days
)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates (also used by routers)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads

# Routers
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")
app.include_router(admin.router, prefix="/admin")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin/login")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 404, "message": "Page introuvable."},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 403, "message": "Accès refusé."},
        status_code=403,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.exception("Unhandled exception")
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 500, "message": "Erreur serveur interne."},
        status_code=500,
    )
