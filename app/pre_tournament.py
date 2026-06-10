"""Pre-tournament question configuration helpers."""

DEFAULT_PRE_TOURNAMENT_DEADLINE = "2026-06-11T18:45:00"

DEFAULT_PRE_TOURNAMENT_QUESTIONS = [
    {
        "key": "winner",
        "points_value": 8,
        "label": "Champion du Monde",
        "points_label": "+8 pts",
        "help_text": "Choisis l'équipe qui gagnera la finale. Elle compte aussi comme l'un de tes deux finalistes.",
        "sort_order": 1,
        "is_enabled": 1,
    },
    {
        "key": "finalist",
        "points_value": 7,
        "label": "Autre finaliste",
        "points_label": "+7 pts par finaliste correct",
        "help_text": "Avec ton champion, cette équipe forme tes deux finalistes. Chaque équipe réellement finaliste rapporte 7 points.",
        "sort_order": 2,
        "is_enabled": 1,
    },
    {
        "key": "top_scorer",
        "points_value": 8,
        "label": "Meilleur buteur",
        "points_label": "+8 pts",
        "help_text": "Choisis le joueur qui terminera meilleur buteur officiel du tournoi.",
        "sort_order": 3,
        "is_enabled": 1,
    },
    {
        "key": "revelation",
        "points_value": 5,
        "label": "Révélation du tournoi",
        "points_label": "+5 pts",
        "help_text": "Choisis l'outsider de la liste qui réalisera le meilleur parcours surprise.",
        "sort_order": 4,
        "is_enabled": 1,
    },
    {
        "key": "total_goals",
        "points_value": 8,
        "label": "Total buts phase de groupes",
        "points_label": "+8 pts exact / +4 pts à +/-3",
        "help_text": "Estime le nombre total de buts marqués pendant la phase de groupes.",
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
               (key, label, points_label, help_text, sort_order, is_enabled, points_value)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                q["key"],
                q["label"],
                q["points_label"],
                q["help_text"],
                q["sort_order"],
                q["is_enabled"],
                q["points_value"],
            ),
        )
        await db.execute(
            "UPDATE pre_tournament_questions SET points_value=? WHERE key=? AND points_value IS NULL",
            (q["points_value"], q["key"]),
        )
    legacy_text_updates = [
        ("winner", "label", "Vainqueur", "Champion du Monde"),
        ("winner", "help_text", "Choisir parmi les 48 équipes.", "Choisis l'équipe qui gagnera la finale. Elle compte aussi comme l'un de tes deux finalistes."),
        ("winner", "help_text", "Choisir parmi les 48 equipes.", "Choisis l'équipe qui gagnera la finale. Elle compte aussi comme l'un de tes deux finalistes."),
        ("finalist", "points_label", "+5 pts", "+7 pts par finaliste correct"),
        ("finalist", "label", "Finaliste", "Autre finaliste"),
        ("finalist", "help_text", "Sélectionner une équipe différente du vainqueur.", "Avec ton champion, cette équipe forme tes deux finalistes. Chaque équipe réellement finaliste rapporte 7 points."),
        ("finalist", "help_text", "Selectionner une equipe differente du vainqueur.", "Avec ton champion, cette équipe forme tes deux finalistes. Chaque équipe réellement finaliste rapporte 7 points."),
        ("top_scorer", "points_label", "+5 pts", "+8 pts"),
        ("top_scorer", "help_text", "Choisir un joueur dans la liste proposee.", "Choisis le joueur qui terminera meilleur buteur officiel du tournoi."),
        ("revelation", "label", "Revelation du tournoi", "Révélation du tournoi"),
        ("revelation", "help_text", "Choisir une equipe outsider.", "Choisis l'outsider de la liste qui réalisera le meilleur parcours surprise."),
        ("revelation", "help_text", "Choisir une équipe outsider.", "Choisis l'outsider de la liste qui réalisera le meilleur parcours surprise."),
        ("total_goals", "label", "Total buts en groupes", "Total buts phase de groupes"),
        ("total_goals", "label", "Total buts du tournoi", "Total buts phase de groupes"),
        ("total_goals", "points_label", "+8 pts exact / +4 pts a +/-3", "+8 pts exact / +4 pts à +/-3"),
        ("total_goals", "help_text", "Estimer le nombre total de buts pendant la phase de groupes.", "Estime le nombre total de buts marqués pendant la phase de groupes."),
        ("total_goals", "help_text", "Estimer le nombre total de buts pendant tout le tournoi.", "Estime le nombre total de buts marqués pendant la phase de groupes."),
    ]
    for key, column, old_value, new_value in legacy_text_updates:
        await db.execute(
            f"UPDATE pre_tournament_questions SET {column}=? WHERE key=? AND {column}=?",
            (new_value, key, old_value),
        )
    point_updates = [
        ("finalist", 5, 7),
        ("top_scorer", 5, 8),
    ]
    for key, old_value, new_value in point_updates:
        await db.execute(
            "UPDATE pre_tournament_questions SET points_value=? WHERE key=? AND points_value=?",
            (new_value, key, old_value),
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
        f"""SELECT key, label, points_label, help_text, sort_order, is_enabled,
                   points_value, correct_answer
            FROM pre_tournament_questions
            {where}
            ORDER BY sort_order"""
    )
    return [dict(r) for r in await rows.fetchall()]


async def get_pre_tournament_question_map(db, include_disabled: bool = False) -> dict:
    questions = await get_pre_tournament_questions(db, include_disabled)
    return {q["key"]: q for q in questions}
