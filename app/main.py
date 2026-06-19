"""RESA Pronostics 2026 — FastAPI application entry point."""
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import set_participant_cookie
from app.config import settings
from app.database import get_db, init_db
from app.routers import admin, api, pages
from app.templating import create_templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and background reminders on startup."""
    await init_db()
    from app.scoring import sync_finalized_evolution_history
    async with get_db() as db:
        await sync_finalized_evolution_history(db)
        await db.commit()
    logger.info("Database initialized.")
    reminder_task = None
    if settings.SCHEDULER_ENABLED:
        from app.scheduler import scheduler_loop
        reminder_task = asyncio.create_task(scheduler_loop())
    yield
    if reminder_task:
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task


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
    max_age=8 * 60 * 60,
)

_PARTICIPANT_PATH = re.compile(r"^/p/([^/]+)")


@app.middleware("http")
async def refresh_participant_cookie(request: Request, call_next):
    """Pose/rafraîchit le cookie de reconnexion sur toute page participant servie
    avec succès. Les routes /p/{token} valident le token (404 sinon), donc un
    statut < 400 garantit un token légitime."""
    response = await call_next(request)
    match = _PARTICIPANT_PATH.match(request.url.path)
    if match and response.status_code < 400:
        set_participant_cookie(
            response, match.group(1), secure=request.url.scheme == "https"
        )
    return response


# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Avatars servis depuis le volume persistant (Railway) ou un dossier local
app.mount("/avatars", StaticFiles(directory=settings.AVATARS_DIR), name="avatars")

# Templates (also used by routers)
templates = create_templates()

# Routers
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")
app.include_router(admin.router, prefix="/admin")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    # Servi à la racine pour donner au service worker un scope "/" complet.
    return FileResponse("app/static/js/sw.js", media_type="application/javascript")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(request, "error.html",
        {"request": request, "code": 404, "message": "Page introuvable."},
        status_code=404,
    )


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    # Admin session expired or missing: send back to the login form.
    if request.url.path.startswith("/admin"):
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(request, "error.html",
        {"request": request, "code": 401, "message": "Authentification requise."},
        status_code=401,
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    # Les routes API sont appelées en fetch: le JS affiche `detail` à l'utilisateur.
    if request.url.path.startswith("/api"):
        return JSONResponse(
            {"detail": getattr(exc, "detail", None) or "Accès refusé."},
            status_code=403,
        )
    return templates.TemplateResponse(request, "error.html",
        {"request": request, "code": 403, "message": "Accès refusé."},
        status_code=403,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.exception("Unhandled exception")
    return templates.TemplateResponse(request, "error.html",
        {"request": request, "code": 500, "message": "Erreur serveur interne."},
        status_code=500,
    )
