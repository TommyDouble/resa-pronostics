"""Easter egg temporaire "Folarin Balogun" — écran classement uniquement.

Actif tant que le prochain match du tournoi n'a pas débuté ; se désactive
tout seul à son coup d'envoi. Aucune donnée en base n'est modifiée : seuls
les dictionnaires déjà construits pour le rendu de `ranking.html` sont
réécrits (voir `apply_balogun_swap`).

À supprimer en bloc après l'événement : ce fichier, l'appel dans
`ranking_page` (app/routers/pages.py), et les conditions
`balogun_easter_egg_active` / `real_name` dans `app/templates/ranking.html`.
"""
from datetime import datetime

from app.timeutils import match_kickoff_utc, now_utc_iso

BALOGUN_NAME = "Folarin Balogun"

# Ces deux trophées listent des noms de départements dans `winner_lines`
# (pas des participants) : on ne les touche pas, seul leur `context_line`
# ("Membres : ...") énumère de vrais noms à masquer.
_CHAMPION_TROPHY_KEYS = ("champion_poules", "champion_tournoi")


async def get_next_match(db) -> dict | None:
    """Prochain match du tournoi (toutes équipes confondues), ou None."""
    row = await db.execute(
        """SELECT * FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) > datetime(?)
           ORDER BY match_date, kickoff_time LIMIT 1""",
        (now_utc_iso(),),
    )
    match = await row.fetchone()
    return dict(match) if match else None


def is_balogun_easter_egg_active(now: datetime, next_match: dict | None) -> bool:
    """Actif tant que `now` précède le coup d'envoi de `next_match`."""
    if next_match is None:
        return False
    try:
        return now < match_kickoff_utc(next_match)
    except Exception:
        return False


def apply_balogun_swap(ctx: dict) -> None:
    """Remplace les noms affichés sur l'écran classement par BALOGUN_NAME.

    Ne modifie que le contexte de rendu déjà en mémoire (`ctx`), jamais la
    base de données. Le vrai nom reste disponible sous `real_name`.
    """
    for r in ctx.get("rankings", []):
        r["real_name"] = r.get("name")
        r["name"] = BALOGUN_NAME
        r["full_name"] = None
        for b in r.get("badges") or []:
            if b.get("key") == "le_jumeau" and b.get("detail_label"):
                b["detail_label"] = f"avec {BALOGUN_NAME}"
    for d in ctx.get("departments", []):
        for m in d.get("participants", []):
            m["real_name"] = m.get("name")
            m["name"] = BALOGUN_NAME
    for item in ctx.get("carousel_items", []):
        if item.get("key") in _CHAMPION_TROPHY_KEYS:
            item["context_line"] = f"Membres : {BALOGUN_NAME}"
            continue
        for w in item.get("winner_lines", []):
            w["real_name"] = w.get("name")
            w["name"] = BALOGUN_NAME
