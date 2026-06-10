"""Réglages applicatifs persistés dans la table app_settings."""

KNOCKOUT_OPEN_KEY = "knockout_predictions_open"

DEFAULT_SETTINGS = {
    # Les pronos de phase finale restent verrouillés tant que l'organisateur
    # ne les ouvre pas (affiches connues).
    KNOCKOUT_OPEN_KEY: "0",
}


async def ensure_default_settings(db):
    for key, value in DEFAULT_SETTINGS.items():
        await db.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )


async def get_setting(db, key: str, default: str = "") -> str:
    row = await db.execute("SELECT value FROM app_settings WHERE key=?", (key,))
    setting = await row.fetchone()
    return setting["value"] if setting else default


async def set_setting(db, key: str, value: str):
    await db.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


async def knockout_predictions_open(db) -> bool:
    return await get_setting(db, KNOCKOUT_OPEN_KEY, "0") == "1"
