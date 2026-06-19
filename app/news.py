"""Registre central des stories de nouveautés (source de vérité unique).

Chaque fonctionnalité qui veut une story enrichie déclare ICI sa clé de
template (mappée vers ``app/templates/partials/news/{key}.html``) et son
libellé admin. La whitelist de rendu (``pages.py``), la validation admin
(``admin.py``) et le menu déroulant admin (``admin/news.html``) en DÉRIVENT —
aucune liste à synchroniser à la main.

Ajouter une story = (1) une entrée dans STORY_TEMPLATES, (2) le partial des
écrans, (3) éventuellement une entrée NEWS_DEFAULTS (ou création via l'admin).
"""

# clé de template -> libellé affiché dans l'admin
STORY_TEMPLATES = {
    "reveal_promo": "Promo « Reveal du jour »",
    "sporting_day_update": "Journées sportives et évolutions",
}


def is_valid_template_key(value: str | None) -> bool:
    return bool(value) and value in STORY_TEMPLATES


# Nouveautés livrées avec leur feature (idempotent : insert si le slug manque).
NEWS_DEFAULTS = [
    {
        "slug": "reveal-du-jour",
        "title": "Le Reveal du jour",
        "body": "Quand les résultats sont prêts, tes points depuis ta dernière visite "
                "se dévoilent carte par carte. 🎉",
        "icon": "🎬",
        "template_key": "reveal_promo",
        "sort_order": 10,
        "is_published": 1,
    },
    {
        "slug": "journees-sportives-apres-minuit",
        "title": "Tes journées, même après minuit",
        "body": "Les matchs de la nuit restent ensemble, ton classement évolue en direct "
                "et le dernier grimpeur garde son titre jusqu'à la journée suivante.",
        "icon": "🌙",
        "template_key": "sporting_day_update",
        "sort_order": 20,
        # Disponible dans l'écran Communications, mais laissée au choix du PO.
        "is_published": 0,
    },
]
