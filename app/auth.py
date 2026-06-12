import bcrypt
from fastapi import HTTPException, Request
from app.database import get_db

# Cookie de reconnexion participant : permet à la PWA (start_url "/") et au
# navigateur de retrouver le compte sans repasser par le login ou le lien email.
PARTICIPANT_COOKIE = "resa_token"
PARTICIPANT_COOKIE_MAX_AGE = 180 * 24 * 60 * 60  # 180 jours, rafraîchi à chaque visite


def set_participant_cookie(response, token: str, secure: bool = False) -> None:
    response.set_cookie(
        PARTICIPANT_COOKIE,
        token,
        max_age=PARTICIPANT_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_participant_cookie(response) -> None:
    response.delete_cookie(PARTICIPANT_COOKIE, path="/")


async def get_participant_by_token(token: str):
    """Look up participant by UUID token."""
    async with get_db() as db:
        row = await db.execute(
            "SELECT * FROM participants WHERE token = ?", (token,)
        )
        participant = await row.fetchone()
    return participant


async def require_participant(token: str):
    """Get participant or raise 404."""
    participant = await get_participant_by_token(token)
    if not participant:
        raise HTTPException(status_code=404, detail="Token invalide ou introuvable.")
    return participant


async def require_admin(request: Request):
    """Check admin session or raise 401."""
    admin_id = request.session.get("admin_id")
    if not admin_id:
        from fastapi.responses import RedirectResponse
        raise HTTPException(status_code=401, detail="Non autorisé")
    async with get_db() as db:
        row = await db.execute("SELECT * FROM admin_users WHERE id = ?", (admin_id,))
        admin = await row.fetchone()
    if not admin:
        raise HTTPException(status_code=401, detail="Non autorisé")
    return admin


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
