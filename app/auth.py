import bcrypt
from fastapi import HTTPException, Request
from app.database import get_db


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
