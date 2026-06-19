"""Cabinet à trophées — source de vérité unique (W7).

`evaluate(metrics)` transforme un dict de métriques déjà calculées (cf.
`_build_profile`) en une liste de trophées prêts à afficher.

`refresh_trophy_awards(db)` persiste les trophées débloqués dans la table
`trophy_awards` (appelé après chaque scoring).

Conventions de catégorie : "regularite" (assiduité, accessible à tous),
"adresse" (performance), "caractere" (style). Les trophées secrets restent
masqués (« ??? ») tant qu'ils ne sont pas débloqués.
"""

from collections import defaultdict

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

TIERED_THRESHOLDS = {
    "present":     [(3, "bronze"), (7, "argent"), (15, "or"), (30, "diamant")],
    "marathon":    [(5, "chocolat"), (20, "bronze"), (50, "argent"), (80, "or")],
    "sniper":      [(2, "chocolat"), (5, "bronze"), (10, "argent"), (20, "or"), (35, "diamant")],
    "bonus_king":  [(5, "chocolat"), (15, "bronze"), (30, "argent"), (50, "or"), (80, "diamant")],
    "streak":      [(3, "bronze"), (5, "argent"), (8, "or"), (12, "diamant")],
    "draw_king":   [(3, "bronze"), (6, "argent"), (10, "or"), (15, "diamant")],
    "last_minute": [(5, "bronze"), (10, "argent"), (20, "or"), (35, "diamant")],
    "climber":     [(1, "chocolat"), (3, "bronze"), (5, "argent"), (8, "or"), (12, "diamant")],
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
    perfect_day, climber_count.
    """
    g = lambda k, d=0: m.get(k, d)
    # Marathonien : le palier OR se clôture sur le dernier match de la compétition
    # (= tous les matchs pronostiqués). Repli à 100 si le total n'est pas connu.
    total_matches = g("total_matches", 0) or 0
    # Diamant de Marathonien = dernier match de la compétition (tous pronostiqués).
    marathon_top = total_matches if total_matches >= 100 else 100
    marathon_thresholds = TIERED_THRESHOLDS["marathon"] + [(marathon_top, "diamant")]
    trophies = [
        # — Régularité (accessible à tous) —
        _simple("first_step", "👣", "Premier pas", "regularite",
                g("match_count") >= 1,
                "Poser son tout premier pronostic"),
        _tiered("present", "📅", "Présent", "regularite",
                g("present_streak"),
                TIERED_THRESHOLDS["present"],
                "jours de connexion d'affilée"),
        _tiered("marathon", "🏃", "Marathonien", "regularite",
                g("match_count"),
                marathon_thresholds,
                "pronostics posés"),
        _simple("loyal", "🛡️", "Fidèle au poste", "regularite",
                g("total_results") >= 5 and g("total_played") >= g("total_results"),
                "Tous les matchs joués pronostiqués (min. 5)"),
        # — Adresse (performance) —
        _tiered("sniper", "🎯", "Sniper", "adresse",
                g("exact"),
                TIERED_THRESHOLDS["sniper"],
                "scores exacts trouvés"),
        _tiered("bonus_king", "⭐", "Roi des bonus", "adresse",
                g("bonus_points"),
                TIERED_THRESHOLDS["bonus_king"],
                "points bonus accumulés"),
        _simple("so_close", "😬", "Si près !", "adresse",
                g("near_miss") >= 3,
                "3 scores exacts ratés à un seul but près",
                current=min(g("near_miss"), 3), target=3),
        # — Caractère (style) —
        _tiered("streak", "🔥", "En série", "caractere",
                g("longest_streak"),
                TIERED_THRESHOLDS["streak"],
                "bons pronos d'affilée"),
        _tiered("draw_king", "🤝", "Roi du nul", "caractere",
                g("draw_correct"),
                TIERED_THRESHOLDS["draw_king"],
                "matchs nuls trouvés"),
        _tiered("last_minute", "⏱️", "Dernière minute", "caractere",
                g("last_minute_count"),
                TIERED_THRESHOLDS["last_minute"],
                "pronos dans l'heure avant coup d'envoi"),
        _tiered("climber", "🏔️", "Alpiniste", "adresse",
                g("climber_count"),
                TIERED_THRESHOLDS["climber"],
                "fois meilleur grimpeur de la journée"),
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
    nearest = max(locked, key=lambda t: t["progress"], default=None)
    last = unlocked[-1] if unlocked else None
    return {
        "unlocked_count": len(unlocked),
        "total": len(trophies),
        "nearest": nearest,
        "last_unlocked": last,
    }


def _tiers_earned(trophy: dict) -> list[str]:
    """Renvoie la liste des paliers atteints pour un trophée débloqué."""
    key = trophy["key"]
    if trophy["tier_count"] == 0:
        return ["_"] if trophy["unlocked"] else []
    thresholds = TIERED_THRESHOLDS.get(key, [])
    return [name for _, name in thresholds[:trophy["tier_index"]]]


async def refresh_trophy_awards(db) -> None:
    """Persiste les trophées débloqués de tous les participants (batch).

    Appelé après chaque scoring. Utilise INSERT OR IGNORE pour ne pas
    écraser les awarded_at existants.
    """
    from app.scoring import is_match_prediction_correct, is_match_score_exact
    from app.timeutils import sporting_day

    pids_rows = await db.execute(
        "SELECT id, best_visit_streak FROM participants WHERE is_confirmed=1"
    )
    participants = {r["id"]: {"best_visit_streak": r["best_visit_streak"] or 0}
                    for r in await pids_rows.fetchall()}
    if not participants:
        return

    # --- Requêtes batch pour tous les participants ---

    tm_row = await db.execute("SELECT COUNT(*) AS cnt FROM matches")
    total_matches = (await tm_row.fetchone())["cnt"]

    tr_row = await db.execute(
        "SELECT COUNT(*) AS cnt FROM matches WHERE result IS NOT NULL"
    )
    total_results = (await tr_row.fetchone())["cnt"]

    mc_rows = await db.execute(
        "SELECT participant_id, COUNT(*) AS cnt FROM predictions GROUP BY participant_id"
    )
    match_counts = {r["participant_id"]: r["cnt"] for r in await mc_rows.fetchall()}

    played_rows = await db.execute(
        """SELECT pr.participant_id,
                  m.phase, m.score_team1, m.score_team2, m.result,
                  m.qualifier_winner, m.match_date, m.kickoff_time,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction, pr.match_id
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE m.result IS NOT NULL"""
    )
    played_by_pid: dict[int, list[dict]] = defaultdict(list)
    for r in await played_rows.fetchall():
        played_by_pid[r["participant_id"]].append(dict(r))

    lm_rows = await db.execute(
        """SELECT pr.participant_id, COUNT(*) AS cnt
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.submitted_at >= datetime(m.match_date || 'T' || m.kickoff_time, '-60 minutes')
             AND pr.submitted_at <= datetime(m.match_date || 'T' || m.kickoff_time)
           GROUP BY pr.participant_id"""
    )
    last_minute_counts = {r["participant_id"]: r["cnt"] for r in await lm_rows.fetchall()}

    bp_rows = await db.execute(
        """SELECT participant_id, SUM(points) AS total FROM (
             SELECT participant_id, points FROM scores WHERE bonus_question_id IS NOT NULL
             UNION ALL
             SELECT participant_id, points FROM pre_tournament_scores
           ) GROUP BY participant_id"""
    )
    bonus_points_map = {r["participant_id"]: r["total"] for r in await bp_rows.fetchall()}

    cl_rows = await db.execute(
        """SELECT participant_id, COUNT(*) AS cnt
           FROM sporting_day_rank_evolutions WHERE is_climber=1
           GROUP BY participant_id"""
    )
    climber_counts = {r["participant_id"]: r["cnt"] for r in await cl_rows.fetchall()}

    # Journées sportives pour perfect_day (une seule requête, partagée)
    result_rows = await db.execute(
        "SELECT id AS match_id, match_date, kickoff_time FROM matches WHERE result IS NOT NULL"
    )
    matches_by_sday: dict[str, list[int]] = defaultdict(list)
    for r in await result_rows.fetchall():
        matches_by_sday[sporting_day(dict(r))].append(r["match_id"])

    # --- Évaluation par participant ---

    inserts = []
    for pid in participants:
        preds = played_by_pid.get(pid, [])
        total_played = len(preds)
        exact = sum(1 for r in preds if is_match_score_exact(r, r))
        near_miss = sum(
            1 for r in preds
            if is_match_prediction_correct(r, r) and not is_match_score_exact(r, r)
            and r.get("exact_score_team1") is not None and r.get("score_team1") is not None
            and (abs(r["exact_score_team1"] - r["score_team1"])
                 + abs(r["exact_score_team2"] - r["score_team2"])) == 1
        )
        draw_correct = sum(
            1 for r in preds
            if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
        )

        # Longest streak (ordre chronologique)
        sorted_preds = sorted(preds, key=lambda r: (r["match_date"], r["kickoff_time"]))
        longest_streak = 0
        run = 0
        for sp in sorted_preds:
            if is_match_prediction_correct(sp, sp):
                run += 1
                longest_streak = max(longest_streak, run)
            else:
                run = 0

        # Perfect day
        preds_by_match = {r["match_id"]: r for r in preds}
        perfect_day = any(
            len(mids) >= 3
            and all(mid in preds_by_match for mid in mids)
            and all(is_match_prediction_correct(preds_by_match[mid], preds_by_match[mid])
                    for mid in mids)
            for mids in matches_by_sday.values()
        )

        metrics = {
            "match_count": match_counts.get(pid, 0),
            "total_matches": total_matches,
            "present_streak": participants[pid]["best_visit_streak"],
            "total_played": total_played,
            "total_results": total_results,
            "exact": exact,
            "bonus_points": bonus_points_map.get(pid, 0),
            "near_miss": near_miss,
            "longest_streak": longest_streak,
            "draw_correct": draw_correct,
            "last_minute_count": last_minute_counts.get(pid, 0),
            "perfect_day": perfect_day,
            "climber_count": climber_counts.get(pid, 0),
        }
        trophies = evaluate(metrics)
        for t in trophies:
            if not t["unlocked"]:
                continue
            for tier_name in _tiers_earned(t):
                inserts.append((pid, t["key"], tier_name))

    if inserts:
        await db.executemany(
            "INSERT OR IGNORE INTO trophy_awards (participant_id, trophy_key, tier) VALUES (?,?,?)",
            inserts,
        )
