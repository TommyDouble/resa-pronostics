import os
from dotenv import load_dotenv

load_dotenv()


def _resolve_avatars_dir() -> str:
    """Volume persistant Railway si accessible, sinon dossier local (dev/tests)."""
    preferred = os.getenv("AVATARS_DIR", "/data/avatars")
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except OSError:
        fallback = os.path.join(os.getcwd(), "data", "avatars")
        os.makedirs(fallback, exist_ok=True)
        return fallback


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "./resa.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-32-bytes-hex-default-key")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "pronos@resa.be")
    EMAIL_WEBHOOK_URL: str = os.getenv("EMAIL_WEBHOOK_URL", "")
    EMAIL_WEBHOOK_SECRET: str = os.getenv("EMAIL_WEBHOOK_SECRET", "")
    EMAIL_SENDER_NAME: str = os.getenv("EMAIL_SENDER_NAME", "RESA Pronostics 2026")
    ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    AVATARS_DIR: str = _resolve_avatars_dir()

    # Rappels automatiques (emails / notifications)
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "1") == "1"
    SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "600"))

    # Notifications push web (volet B) — désactivées si les clés sont absentes.
    # Générer: python -c "from py_vapid import Vapid01; v=Vapid01(); v.generate_keys();
    #          print(v.private_pem().decode()); print(v.public_pem().decode())"
    # ou via scripts/generate_vapid_keys.py
    VAPID_PUBLIC_KEY: str = os.getenv("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY: str = os.getenv("VAPID_PRIVATE_KEY", "")
    VAPID_CLAIMS_EMAIL: str = os.getenv("VAPID_CLAIMS_EMAIL", "pronos@resa.be")

    # Timezone
    TZ_DISPLAY: str = "Europe/Brussels"

    @property
    def is_railway(self) -> bool:
        return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))

    def validate(self):
        if self.is_railway and self.SECRET_KEY == "change-me-32-bytes-hex-default-key":
            raise RuntimeError("SECRET_KEY must be configured before deploying to Railway.")


settings = Settings()
settings.validate()
