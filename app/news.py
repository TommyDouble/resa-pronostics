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
    "cabinet_promo": "Promo « Cabinet à trophées »",
}


def is_valid_template_key(value: str | None) -> bool:
    return bool(value) and value in STORY_TEMPLATES


# Nouveautés livrées avec leur feature (idempotent : insert si le slug manque).
NEWS_DEFAULTS = [
    {
        "slug": "reveal-du-jour",
        "title": "Le Reveal du jour",
        "body": "Chaque matin, tes points de la veille se dévoilent carte par carte — "
                "et c'est la fête sur tes scores exacts. 🎉",
        "icon": "🎬",
        "template_key": "reveal_promo",
        "sort_order": 10,
    },
    {
        "slug": "cabinet-trophees",
        "title": "Le Cabinet à trophées",
        "body": "Tes exploits deviennent des trophées : assiduité, scores exacts, "
                "séries… Débloque-les et fais grimper ta collection. 🏆",
        "icon": "🏆",
        "template_key": "cabinet_promo",
        "sort_order": 20,
    },
]
