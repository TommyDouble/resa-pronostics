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

# Ordre des paliers : chocolat (entrée, optionnel) -> bronze -> argent -> or -> diamant.
# Les valeurs restent textuelles pour éviter les emojis système dans le cabinet.
_MEDALS = {
    "chocolat": "chocolat",
    "bronze": "bronze",
    "argent": "argent",
    "or": "or",
    "diamant": "diamant",
}


def _tiered(key, icon, label, category, value, thresholds, noun):
    """Trophée à paliers. `thresholds` = [(seuil, nom_palier), ...] croissant.

    Toujours explicite sur la marche suivante : palier courant + combien il reste
    pour le prochain (médaille incluse). Débloqué dès le premier seuil atteint.
    """
    value = value or 0
    tier = None
    tier_index = 0          # nb de paliers atteints (pour les pips ordinaux)
    for seuil, name in thresholds:
        if value >= seuil:
            tier = name
            tier_index += 1
    tier_count = len(thresholds)
    unlocked = tier is not None
    nxt = next(((s, nm) for s, nm in thresholds if value < s), None)
    floor = max((s for s, _ in thresholds if value >= s), default=0)
    if nxt is not None:
        target, next_tier = nxt
        progress = (value - floor) / (target - floor) if target > floor else 1.0
        remaining = target - value
        if unlocked:
            desc = (f"Niveau {tier} ({value} {noun}). Plus que {remaining} "
                    f"pour passer {next_tier} ({target}).")
        else:
            desc = (f"Plus que {remaining} pour le niveau {next_tier} "
                    f": {value}/{target} {noun}.")
    else:
        target, next_tier, remaining = None, None, 0
        progress = 1.0
        desc = f"Palier maximum atteint — niveau {tier} ({value} {noun})."
    return {
        "key": key, "icon": icon, "label": label, "category": category,
        "secret": False, "unlocked": unlocked,
        "tier": tier, "medal": _MEDALS.get(tier),
        "tier_index": tier_index, "tier_count": tier_count,
        "next_tier": next_tier, "next_medal": _MEDALS.get(next_tier),
        "current": value, "target": target, "remaining": remaining,
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
        "tier_index": 0, "tier_count": 0,
        "next_tier": None, "next_medal": None, "remaining": 0,
        "current": current, "target": target,
        "progress": round(max(0.0, min(1.0, progress)), 3),
        "desc": desc,
    }


def evaluate(m: dict) -> list[dict]:
    """Construit la liste des trophées v1 (~11) à partir des métriques.

    Métriques attendues : match_count, present_streak, total_played, total_results,
    exact, bonus_points, near_miss, longest_streak, draw_correct, last_minute_count,
    perfect_day.
    """
    g = lambda k, d=0: m.get(k, d)
    # Marathonien : le palier OR se clôture sur le dernier match de la compétition
    # (= tous les matchs pronostiqués). Repli à 100 si le total n'est pas connu.
    total_matches = g("total_matches", 0) or 0
    # Diamant de Marathonien = dernier match de la compétition (tous pronostiqués).
    marathon_top = total_matches if total_matches >= 100 else 100
    trophies = [
        # — Régularité (accessible à tous) —
        _simple("first_step", "👣", "Premier pas", "regularite",
                g("match_count") >= 1,
                "Poser son tout premier pronostic"),
        _tiered("present", "📅", "Présent", "regularite",
                g("present_streak"),
                [(3, "bronze"), (7, "argent"), (15, "or"), (30, "diamant")],
                "jours de connexion d'affilée"),
        _tiered("marathon", "🏃", "Marathonien", "regularite",
                g("match_count"),
                [(5, "chocolat"), (20, "bronze"), (50, "argent"), (80, "or"), (marathon_top, "diamant")],
                "pronostics posés"),
        _simple("loyal", "🛡️", "Fidèle au poste", "regularite",
                g("total_results") >= 5 and g("total_played") >= g("total_results"),
                "Tous les matchs joués pronostiqués (min. 5)"),
        # — Adresse (performance) —
        _tiered("sniper", "🎯", "Sniper", "adresse",
                g("exact"),
                [(2, "chocolat"), (5, "bronze"), (10, "argent"), (20, "or"), (35, "diamant")],
                "scores exacts trouvés"),
        _tiered("bonus_king", "⭐", "Roi des bonus", "adresse",
                g("bonus_points"),
                [(5, "chocolat"), (15, "bronze"), (30, "argent"), (50, "or"), (80, "diamant")],
                "points bonus accumulés"),
        _simple("so_close", "😬", "Si près !", "adresse",
                g("near_miss") >= 3,
                "3 scores exacts ratés à un seul but près",
                current=min(g("near_miss"), 3), target=3),
        # — Caractère (style) —
        _tiered("streak", "🔥", "En série", "caractere",
                g("longest_streak"), [(3, "bronze"), (5, "argent"), (8, "or"), (12, "diamant")],
                "bons pronos d'affilée"),
        _tiered("draw_king", "🤝", "Roi du nul", "caractere",
                g("draw_correct"),
                [(3, "bronze"), (6, "argent"), (10, "or"), (15, "diamant")],
                "matchs nuls trouvés"),
        _tiered("last_minute", "⏱️", "Dernière minute", "caractere",
                g("last_minute_count"),
                [(5, "bronze"), (10, "argent"), (20, "or"), (35, "diamant")],
                "pronos dans l'heure avant coup d'envoi"),
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
