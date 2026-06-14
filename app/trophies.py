"""Cabinet à trophées — source de vérité unique (W7).

`evaluate(metrics)` transforme un dict de métriques déjà calculées (cf.
`_build_profile`) en une liste de trophées prêts à afficher. Tout est dérivé sans
état stocké : la même fonction sert le profil (à l'affichage) et, plus tard (v2),
un éventuel job batch pour la comparaison entre joueurs.

Conventions de catégorie : "regularite" (assiduité, accessible à tous),
"adresse" (performance), "caractere" (style). Les trophées secrets restent
masqués (« ??? ») tant qu'ils ne sont pas débloqués.
"""

CATEGORIES = [
    ("regularite", "Régularité"),
    ("adresse", "Adresse"),
    ("caractere", "Caractère"),
]

_MEDALS = {"bronze": "🥉", "argent": "🥈", "or": "🥇", "diamant": "💎"}


def _tiered(key, icon, label, category, value, thresholds, noun):
    """Trophée à paliers. `thresholds` = [(seuil, nom_palier), ...] croissant.

    Renvoie le palier courant (médaille), la progression vers le prochain, et un
    libellé. Débloqué dès le premier seuil atteint.
    """
    value = value or 0
    tier = None
    for seuil, name in thresholds:
        if value >= seuil:
            tier = name
    unlocked = tier is not None
    nxt = next((s for s, _ in thresholds if value < s), None)
    floor = max((s for s, _ in thresholds if value >= s), default=0)
    if nxt is not None:
        target = nxt
        span_lo = floor
        progress = (value - span_lo) / (target - span_lo) if target > span_lo else 1.0
    else:
        target = None
        progress = 1.0
    if unlocked:
        desc = f"{noun} : {value} · niveau {tier}"
    else:
        desc = f"{thresholds[0][0]} {noun} pour le bronze (tu en es à {value})"
    return {
        "key": key, "icon": icon, "label": label, "category": category,
        "secret": False, "unlocked": unlocked,
        "tier": tier, "medal": _MEDALS.get(tier),
        "current": value, "target": target,
        "progress": round(max(0.0, min(1.0, progress)), 3),
        "desc": desc,
    }


def _simple(key, icon, label, category, unlocked, desc, *,
            current=None, target=None, secret=False):
    """Trophée binaire (avec, optionnellement, une progression current/target)."""
    if current is not None and target:
        progress = current / target if target else 1.0
    else:
        progress = 1.0 if unlocked else 0.0
    return {
        "key": key, "icon": icon, "label": label, "category": category,
        "secret": secret, "unlocked": bool(unlocked),
        "tier": None, "medal": None,
        "current": current, "target": target,
        "progress": round(max(0.0, min(1.0, progress)), 3),
        "desc": desc,
    }


def evaluate(m: dict) -> list[dict]:
    """Construit la liste des trophées v1 (~10) à partir des métriques.

    Métriques attendues : match_count, present_streak, total_played, total_results,
    exact, bonus_king, near_miss, longest_streak, draw_correct, perfect_day.
    """
    g = lambda k, d=0: m.get(k, d)
    trophies = [
        # — Régularité (accessible à tous) —
        _simple("first_step", "👣", "Premier pas", "regularite",
                g("match_count") >= 1,
                "Poser son tout premier pronostic"),
        _tiered("present", "📅", "Présent", "regularite",
                g("present_streak"), [(3, "bronze"), (7, "argent"), (15, "or")],
                "jours d'affilée pronostiqués"),
        _tiered("marathon", "🏃", "Marathonien", "regularite",
                g("match_count"), [(20, "bronze"), (50, "argent"), (100, "or")],
                "pronostics posés"),
        _simple("loyal", "🛡️", "Fidèle au poste", "regularite",
                g("total_results") >= 5 and g("total_played") >= g("total_results"),
                "Tous les matchs joués pronostiqués (min. 5)"),
        # — Adresse (performance) —
        _tiered("sniper", "🎯", "Sniper", "adresse",
                g("exact"), [(5, "bronze"), (10, "argent"), (20, "or"), (35, "diamant")],
                "scores exacts trouvés"),
        _simple("bonus_king", "⭐", "Roi des bonus", "adresse",
                g("bonus_king"), "1er au classement bonus"),
        _simple("so_close", "😬", "Si près !", "adresse",
                g("near_miss") >= 3,
                "3 scores exacts ratés à un seul but près",
                current=min(g("near_miss"), 3), target=3),
        # — Caractère (style) —
        _tiered("streak", "🔥", "En série", "caractere",
                g("longest_streak"), [(3, "bronze"), (5, "argent"), (8, "or"), (12, "diamant")],
                "bons pronos d'affilée"),
        _tiered("draw_king", "🤝", "Roi du nul", "caractere",
                g("draw_correct"), [(3, "bronze"), (6, "argent"), (10, "or")],
                "matchs nuls trouvés"),
        # — Secret —
        _simple("perfect_day", "✨", "Journée parfaite", "caractere",
                g("perfect_day"),
                "100 % de bons résultats sur une journée d'au moins 3 matchs",
                secret=True),
    ]
    return trophies


def summarize(trophies: list[dict]) -> dict:
    """Compteurs + trophée le plus proche d'être débloqué (pour le teaser accueil)."""
    unlocked = [t for t in trophies if t["unlocked"]]
    locked = [t for t in trophies if not t["unlocked"] and not t["secret"]]
    # Le plus proche : progression la plus élevée parmi les verrouillés non secrets.
    nearest = max(locked, key=lambda t: t["progress"], default=None)
    last = unlocked[-1] if unlocked else None
    return {
        "unlocked_count": len(unlocked),
        "total": len(trophies),
        "nearest": nearest,
        "last_unlocked": last,
    }
