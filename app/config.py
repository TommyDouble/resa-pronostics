import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "./resa.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-32-bytes-hex-default-key")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "pronos@resa.be")
    ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "")

    # Timezone
    TZ_DISPLAY: str = "Europe/Brussels"


settings = Settings()
