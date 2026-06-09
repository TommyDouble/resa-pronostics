"""Pre-tournament question configuration helpers."""

DEFAULT_PRE_TOURNAMENT_DEADLINE = "2026-06-11T18:45:00"

DEFAULT_PRE_TOURNAMENT_QUESTIONS = [
    {
        "key": "winner",
        "label": "Vainqueur",
        "points_label": "+8 pts",
        "help_text": "Choisir parmi les 48 equipes.",
        "sort_order": 1,
        "is_enabled": 1,
    },
    {
        "key": "finalist",
        "label": "Finaliste",
        "points_label": "+5 pts",
        "help_text": "Selectionner une equipe differente du vainqueur.",
        "sort_order": 2,
        "is_enabled": 1,
    },
    {
        "key": "top_scorer",
        "label": "Meilleur buteur",
        "points_label": "+5 pts",
        "help_text": "Choisir un joueur dans la liste proposee.",
        "sort_order": 3,
        "is_enabled": 1,
    },
    {
        "key": "revelation",
        "label": "Revelation du tournoi",
        "points_label": "+5 pts",
        "help_text": "Choisir une equipe outsider.",
        "sort_order": 4,
        "is_enabled": 1,
    },
    {
        "key": "total_goals",
        "label": "Total buts en groupes",
        "points_label": "+8 pts exact / +4 pts a +/-3",
        "help_text": "Estimer le nombre total de buts pendant la phase de groupes.",
        "sort_order": 5,
        "is_enabled": 1,
    },
]


async def ensure_pre_tournament_defaults(db):
    """Seed settings and default question metadata if missing."""
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        ("pre_tournament_deadline", DEFAULT_PRE_TOURNAMENT_DEADLINE),
    )
    for q in DEFAULT_PRE_TOURNAMENT_QUESTIONS:
        await db.execute(
            """INSERT OR IGNORE INTO pre_tournament_questions
               (key, label, points_label, help_text, sort_order, is_enabled)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                q["key"],
                q["label"],
                q["points_label"],
                q["help_text"],
                q["sort_order"],
                q["is_enabled"],
            ),
        )


async def get_pre_tournament_deadline(db) -> str:
    row = await db.execute(
        "SELECT value FROM app_settings WHERE key='pre_tournament_deadline'"
    )
    setting = await row.fetchone()
    return setting["value"] if setting else DEFAULT_PRE_TOURNAMENT_DEADLINE


async def get_pre_tournament_questions(db, include_disabled: bool = False) -> list:
    where = "" if include_disabled else "WHERE is_enabled=1"
    rows = await db.execute(
        f"""SELECT key, label, points_label, help_text, sort_order, is_enabled
            FROM pre_tournament_questions
            {where}
            ORDER BY sort_order"""
    )
    return [dict(r) for r in await rows.fetchall()]


async def get_pre_tournament_question_map(db, include_disabled: bool = False) -> dict:
    questions = await get_pre_tournament_questions(db, include_disabled)
    return {q["key"]: q for q in questions}
