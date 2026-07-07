"""Easter egg temporaire "Folarin Balogun" — écran classement uniquement.

Clin d'œil au 1/8e de finale (match n°94) où la Belgique élimine les USA de
Folarin Balogun. Actif jusqu'au coup d'envoi du match qui suit
chronologiquement ce 1/8e ; se désactive alors définitivement (ancré sur un
match fixe, il ne se réactive jamais pour les matchs suivants). Aucune
donnée en base n'est modifiée : seuls les dictionnaires déjà construits pour
le rendu de `ranking.html` sont réécrits (voir `apply_balogun_swap`).

À supprimer en bloc après l'événement : ce fichier, l'appel dans
`ranking_page` (app/routers/pages.py), et les conditions
`balogun_easter_egg_active` / `real_name` dans `app/templates/ranking.html`.
"""
from datetime import datetime

from app.timeutils import match_kickoff_utc

BALOGUN_NAME = "Folarin Balogun"

# Match déclencheur : 1/8e de finale Belgique-USA (Folarin Balogun).
BALOGUN_TRIGGER_MATCH_NUMBER = 94

# Ces deux trophées listent des noms de départements dans `winner_lines`
# (pas des participants) : on ne les touche pas, seul leur `context_line`
# ("Membres : ...") énumère de vrais noms à masquer.
_CHAMPION_TROPHY_KEYS = ("champion_poules", "champion_tournoi")


async def get_deactivation_match(db) -> dict | None:
    """Premier match qui suit chronologiquement le match déclencheur
    (`BALOGUN_TRIGGER_MATCH_NUMBER`), ou None si ce match n'existe pas en
    base ou si aucun match ne le suit (fin du tournoi).

    Volontairement ancré sur un match fixe (pas "le prochain match à venir")
    pour que la désactivation soit définitive dès son coup d'envoi, sans se
    réactiver ensuite pour les matchs suivants.
    """
    trigger_row = await db.execute(
        "SELECT match_date, kickoff_time FROM matches WHERE match_number = ?",
        (BALOGUN_TRIGGER_MATCH_NUMBER,),
    )
    trigger = await trigger_row.fetchone()
    if trigger is None:
        return None
    row = await db.execute(
        """SELECT * FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) > datetime(?)
           ORDER BY match_date, kickoff_time LIMIT 1""",
        (f"{trigger['match_date']}T{trigger['kickoff_time']}",),
    )
    match = await row.fetchone()
    return dict(match) if match else None


def is_balogun_easter_egg_active(now: datetime, deactivation_match: dict | None) -> bool:
    """Actif tant que `now` précède le coup d'envoi de `deactivation_match`."""
    if deactivation_match is None:
        return False
    try:
        return now < match_kickoff_utc(deactivation_match)
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
