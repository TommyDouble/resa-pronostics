"""Cabinet à trophées — refonte W8 (source de vérité unique).

Philosophie (cf. charte de gamification) :
- Un trophée doit marquer un ÉVÉNEMENT RARE. Tous binaires (pas de paliers).
- Le classement est un FIL D'ACTUALITÉ : un seul badge éphémère par joueur, le
  plus récent, jusqu'à la clôture du prochain sporting day (cf.
  `latest_ephemeral_badges`). Le cabinet est l'ARCHIVE permanente
  (cf. `build_cabinet`).
- Certains trophées sont RÉPÉTABLES : chaque occurrence est une ligne distincte
  dans `trophy_awards`, discriminée par `detail` ; le cabinet affiche un compteur
  « ×N » au-delà de la première fois.

Le moteur d'attribution vit dans `refresh_trophy_awards(db)` (contexte global :
classements, distributions par match, paires), appelé après chaque scoring. Il
écrit des lignes idempotentes (INSERT OR IGNORE) ; l'affichage ne recalcule rien.
"""

from collections import defaultdict

# Catégories d'étagères du cabinet (clé, libellé), dans l'ordre d'affichage.
CATEGORIES = [
    ("exploit", "Exploits"),
    ("adresse", "Adresse"),
    ("mouvement", "Régularité"),
    ("honte", "Anti-trophées"),
    ("departement", "Département"),
]

# Seuils — calibrés via scripts/simulate_trophies.py (1er passage : ~37 % du
# tournoi joué, à ré-affiner en fin de poules / phase finale).
SNIPER_EXACT = 14          # scores exacts trouvés (légendaire ~5 %, projeté plein tournoi)
SERIE_STREAK = 8           # bons résultats d'affilée
ROI_NUL_DRAWS = 5          # nuls de poule trouvés
SANGFROID_MIN_KO = 8       # matchs joués en phase finale (min.)
SANGFROID_RATE = 0.70      # taux de bons résultats en phase finale
TUEUR_UPSETS = 3           # surprises pronostiquées
UPSET_SHARE = 0.25         # une issue est une "surprise" si < 25 % du groupe l'a vue
SAVANT_EXOTIC = 12         # scores exotiques ratés
EXOTIC_SHARE = 0.05        # un score est "exotique" si < 5 % du groupe l'a tapé
JUMEAU_CONSEC = 12         # mêmes scores exacts sur N matchs consécutifs
CUILLERE_MIN_SHARE = 0.5   # avoir pronostiqué au moins la moitié des matchs
EXTRA_SUM = 6              # buts cumulés mini pour "match fou"
EXTRA_DIFF = 4             # écart de buts mini pour "match fou"
PERFECT_MIN_MATCHES = 3    # matchs mini sur une journée parfaite
PERFECT_MIN_EXACT = 2      # ... dont au moins N scores exacts (sinon trop commun : 71 %)

# Catalogue. timing : "continu" (évalué en continu, éligible au badge éphémère),
# "fin_poules" (à la clôture de la phase de groupes), "fin_tournoi" (à la clôture
# du tournoi). repeatable : occurrences multiples avec compteur ×N.
TROPHIES = [
    # — Exploits & légendes —
    {"key": "sniper", "icon": "🎯", "label": "Le Sniper", "category": "exploit",
     "secret": False, "repeatable": False, "timing": "continu",
     "icon_key": "target", "rarity": "legendary",
     "caption": "Il triche ou il a un don ?",
     "desc": f"Trouver {SNIPER_EXACT} scores exacts sur le tournoi."},
    {"key": "la_serie", "icon": "🔥", "label": "La Série", "category": "exploit",
     "secret": False, "repeatable": False, "timing": "continu",
     "icon_key": "flame", "rarity": "rare",
     "caption": "Intouchable, en feu.",
     "desc": f"Enchaîner {SERIE_STREAK} bons résultats d'affilée."},
    {"key": "oracle", "icon": "🔮", "label": "L'Oracle", "category": "exploit",
     "secret": False, "repeatable": False, "timing": "continu",
     "icon_key": "crystal-ball", "rarity": "legendary",
     "caption": "Il avait écrit le nom du champion avant tout le monde.",
     "desc": "Avoir donné le vainqueur de la Coupe du Monde dans le prono pré-tournoi."},
    {"key": "journee_parfaite", "icon": "🌋", "label": "La Journée Parfaite",
     "category": "exploit", "secret": False, "repeatable": True, "timing": "continu",
     "icon_key": "calendar-check", "rarity": "rare",
     "caption": "Ce jour-là, branché sur la bonne fréquence.",
     "desc": f"100 % de bons résultats (dont {PERFECT_MIN_EXACT} scores exacts) sur une "
             f"journée d'au moins {PERFECT_MIN_MATCHES} matchs."},
    # — Sélectifs & malins —
    {"key": "sang_froid", "icon": "🧊", "label": "Sang-Froid", "category": "adresse",
     "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "snowflake", "rarity": "rare",
     "caption": "Il ne tremble pas dans les grands matchs.",
     "desc": f"≥ {int(SANGFROID_RATE * 100)} % de bons résultats en phase finale "
             f"(min. {SANGFROID_MIN_KO} matchs joués)."},
    {"key": "tueur_de_favoris", "icon": "🐉", "label": "Le Tueur de Favoris",
     "category": "adresse", "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "sword", "rarity": "rare",
     "caption": "Les favoris le détestent.",
     "desc": f"Trouver {TUEUR_UPSETS} surprises (issue vue par < {int(UPSET_SHARE * 100)} % du groupe)."},
    {"key": "roi_du_nul", "icon": "⚖️", "label": "Le Roi du Nul", "category": "adresse",
     "secret": False, "repeatable": False, "timing": "continu",
     "icon_key": "scales", "rarity": "rare",
     "caption": "Personne ne mise sur le nul. Lui, si.",
     "desc": f"Pronostiquer correctement {ROI_NUL_DRAWS} matchs nuls de poule."},
    # — Anti-trophées (vestiaire) —
    {"key": "eternel_deuxieme", "icon": "💔", "label": "L'Éternel Deuxième",
     "category": "honte", "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "heart-broken", "rarity": "anti",
     "caption": "À un but près à chaque fois. Rageant.",
     "desc": "Le plus de scores exacts ratés à un seul but près de tout le tournoi."},
    {"key": "cuillere_en_bois", "icon": "🥄", "label": "La Cuillère en Bois",
     "category": "honte", "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "spoon", "rarity": "anti",
     "caption": "Dernier mais pas oublié. Enfin si, un peu.",
     "desc": "Terminer dernier du classement général (en ayant joué au moins la moitié des matchs)."},
    {"key": "savant_fou", "icon": "🤡", "label": "Le Savant Fou", "category": "honte",
     "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "flask", "rarity": "anti",
     "caption": "Des théories brillantes. Zéro résultat.",
     "desc": f"{SAVANT_EXOTIC} scores exotiques tapés (vus par < {int(EXOTIC_SHARE * 100)} % "
             f"du groupe), aucun tombé juste."},
    # — Régularité / mouvement —
    {"key": "grimpeur", "icon": "🚀", "label": "Le Grimpeur", "category": "mouvement",
     "secret": False, "repeatable": True, "timing": "continu",
     "icon_key": "rocket", "rarity": "common",
     "caption": "Du sous-sol au penthouse en une soirée.",
     "desc": "Meilleur grimpeur au classement sur une journée sportive."},
    # — Département —
    {"key": "champion_poules", "icon": "🏰", "label": "Champion des Poules",
     "category": "departement", "secret": False, "repeatable": False, "timing": "fin_poules",
     "icon_key": "castle", "rarity": "rare",
     "caption": "Bombez le torse, les collègues.",
     "desc": "Membre du département en tête (à la moyenne) à la fin de la phase de groupes."},
    {"key": "champion_tournoi", "icon": "🏆", "label": "Champion du Tournoi",
     "category": "departement", "secret": False, "repeatable": False, "timing": "fin_tournoi",
     "icon_key": "trophy", "rarity": "legendary",
     "caption": "Le département de l'année, c'est le nôtre.",
     "desc": "Membre du département en tête (à la moyenne) à la fin du tournoi."},
    # — Secrets —
    {"key": "extraterrestre", "icon": "👽", "label": "L'Extraterrestre",
     "category": "exploit", "secret": True, "repeatable": True, "timing": "continu",
     "icon_key": "alien", "rarity": "legendary",
     "caption": "Comment. Est-ce. Possible.",
     "desc": f"Score exact sur un match à {EXTRA_SUM}+ buts cumulés ou {EXTRA_DIFF}+ buts d'écart."},
    {"key": "le_jumeau", "icon": "🪞", "label": "Le Jumeau", "category": "adresse",
     "secret": True, "repeatable": True, "timing": "continu",
     "icon_key": "mirror", "rarity": "rare",
     "caption": "Vous avez le même cerveau ou quoi ?",
     "desc": f"Mêmes scores exacts qu'un autre joueur sur {JUMEAU_CONSEC} matchs consécutifs."},
]

TROPHY_BY_KEY = {t["key"]: t for t in TROPHIES}
CATEGORY_LABELS = dict(CATEGORIES)


# ---------------------------------------------------------------------------
# Moteur d'attribution
# ---------------------------------------------------------------------------

def _top_department_member_ids(rankings: list) -> list[int]:
    """IDs des membres du département de tête (à la moyenne de points par inscrit).

    « Sans département » ne concourt pas. Départage : moyenne, puis effectif.
    """
    depts: dict[str, dict] = {}
    for r in rankings:
        dept = (r.get("department") or "").strip()
        if not dept:
            continue
        bucket = depts.setdefault(dept, {"total": 0, "members": 0, "ids": []})
        bucket["total"] += r["total_points"]
        bucket["members"] += 1
        bucket["ids"].append(r["id"])
    if not depts:
        return []
    best = max(
        depts.values(),
        key=lambda b: (b["total"] / b["members"] if b["members"] else 0, b["members"]),
    )
    return best["ids"]


async def refresh_trophy_awards(db) -> None:
    """Persiste les trophées débloqués de tous les participants (batch).

    Idempotent (INSERT OR IGNORE) : les awarded_at existants ne sont pas écrasés.
    Les trophées relatifs (dernier, plus de « presque », champions de département)
    ne sont calculés qu'à la clôture de leur phase, donc une fois figés.
    """
    from app.scoring import (
        is_match_prediction_correct, is_match_score_exact, get_rankings,
    )
    from app.timeutils import sporting_day

    prows = await db.execute(
        "SELECT id, department FROM participants WHERE is_confirmed=1 AND is_admin=0"
    )
    participants = {r["id"]: {"department": r["department"]} for r in await prows.fetchall()}
    if not participants:
        return

    # --- Matchs & état des phases ---
    mrows = await db.execute(
        "SELECT id, phase, match_date, kickoff_time, score_team1, score_team2, "
        "result, qualifier_winner FROM matches"
    )
    matches = {r["id"]: dict(r) for r in await mrows.fetchall()}
    total_matches = len(matches)
    group_ids = [mid for mid, m in matches.items() if m["phase"] == "group"]
    group_done = bool(group_ids) and all(matches[mid]["result"] is not None for mid in group_ids)
    all_done = total_matches > 0 and all(m["result"] is not None for m in matches.values())

    # Journées sportives (matchs avec résultat) pour la journée parfaite.
    matches_by_sday: dict[str, list[int]] = defaultdict(list)
    for mid, m in matches.items():
        if m["result"] is not None:
            matches_by_sday[sporting_day(m)].append(mid)

    # --- Pronostics joués (match avec résultat) ---
    played_rows = await db.execute(
        """SELECT pr.participant_id, pr.match_id,
                  m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  m.match_date, m.kickoff_time,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE m.result IS NOT NULL"""
    )
    played_by_pid: dict[int, list[dict]] = defaultdict(list)
    preds_by_match: dict[int, list[dict]] = defaultdict(list)
    for r in await played_rows.fetchall():
        d = dict(r)
        played_by_pid[d["participant_id"]].append(d)
        preds_by_match[d["match_id"]].append(d)

    # Distributions par match : part des pronostiqueurs ayant trouvé l'issue,
    # et fréquence de chaque score exact (pour "surprise" et "score exotique").
    outcome_stats: dict[int, tuple[int, int]] = {}
    score_freq: dict[int, tuple[dict, int]] = {}
    for mid, lst in preds_by_match.items():
        total = len(lst)
        correct = sum(1 for r in lst if is_match_prediction_correct(r, r))
        outcome_stats[mid] = (total, correct)
        counts: dict[tuple, int] = defaultdict(int)
        scored = 0
        for r in lst:
            if r["exact_score_team1"] is not None and r["exact_score_team2"] is not None:
                counts[(r["exact_score_team1"], r["exact_score_team2"])] += 1
                scored += 1
        score_freq[mid] = (counts, scored)

    # Nombre de pronostics (tous matchs) par participant — éligibilité Cuillère.
    pc_rows = await db.execute(
        "SELECT participant_id, COUNT(*) AS cnt FROM predictions GROUP BY participant_id"
    )
    pred_count = {r["participant_id"]: r["cnt"] for r in await pc_rows.fetchall()}

    inserts: list[tuple] = []
    near_miss_by_pid: dict[int, int] = {}

    # --- Trophées par participant (continus + métriques relatives) ---
    for pid, lst in played_by_pid.items():
        exact = sum(1 for r in lst if is_match_score_exact(r, r))
        if exact >= SNIPER_EXACT:
            inserts.append((pid, "sniper", ""))

        ordered = sorted(lst, key=lambda r: (r["match_date"], r["kickoff_time"]))
        run = longest = 0
        for r in ordered:
            if is_match_prediction_correct(r, r):
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        if longest >= SERIE_STREAK:
            inserts.append((pid, "la_serie", ""))

        draw_ok = sum(
            1 for r in lst
            if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
        )
        if draw_ok >= ROI_NUL_DRAWS:
            inserts.append((pid, "roi_du_nul", ""))

        near_miss_by_pid[pid] = sum(
            1 for r in lst
            if is_match_prediction_correct(r, r) and not is_match_score_exact(r, r)
            and r.get("exact_score_team1") is not None and r.get("score_team1") is not None
            and (abs(r["exact_score_team1"] - r["score_team1"])
                 + abs(r["exact_score_team2"] - r["score_team2"])) == 1
        )

        # Journée parfaite (répétable) : 100 % de bons résultats ET au moins
        # PERFECT_MIN_EXACT scores exacts sur la journée (sinon trop commun).
        owned = {r["match_id"]: r for r in lst}
        for sday, mids in matches_by_sday.items():
            if len(mids) >= PERFECT_MIN_MATCHES and all(mid in owned for mid in mids) \
                    and all(is_match_prediction_correct(owned[mid], owned[mid]) for mid in mids) \
                    and sum(1 for mid in mids if is_match_score_exact(owned[mid], owned[mid])) >= PERFECT_MIN_EXACT:
                inserts.append((pid, "journee_parfaite", sday))

        # Extraterrestre (secret, répétable, une par match fou réussi)
        for r in lst:
            if is_match_score_exact(r, r):
                s1, s2 = r["score_team1"], r["score_team2"]
                if (s1 + s2) >= EXTRA_SUM or abs(s1 - s2) >= EXTRA_DIFF:
                    inserts.append((pid, "extraterrestre", str(r["match_id"])))

        # Tueur de favoris — figé en fin de tournoi
        if all_done:
            upsets = 0
            for r in lst:
                if is_match_prediction_correct(r, r):
                    total, correct = outcome_stats[r["match_id"]]
                    if total > 0 and correct / total < UPSET_SHARE:
                        upsets += 1
            if upsets >= TUEUR_UPSETS:
                inserts.append((pid, "tueur_de_favoris", ""))

            ko = [r for r in lst if r["phase"] != "group"]
            ko_ok = sum(1 for r in ko if is_match_prediction_correct(r, r))
            if len(ko) >= SANGFROID_MIN_KO and ko_ok / len(ko) >= SANGFROID_RATE:
                inserts.append((pid, "sang_froid", ""))

            exotic_miss = 0
            for r in lst:
                if r["exact_score_team1"] is None or r["exact_score_team2"] is None:
                    continue
                counts, scored = score_freq[r["match_id"]]
                if scored > 0:
                    share = counts[(r["exact_score_team1"], r["exact_score_team2"])] / scored
                    if share < EXOTIC_SHARE and not is_match_score_exact(r, r):
                        exotic_miss += 1
            if exotic_miss >= SAVANT_EXOTIC:
                inserts.append((pid, "savant_fou", ""))

    # --- L'Oracle : vainqueur du tournoi trouvé au prono pré-tournoi ---
    oracle_rows = await db.execute(
        "SELECT participant_id FROM pre_tournament_scores WHERE question_key='winner' AND points > 0"
    )
    for r in await oracle_rows.fetchall():
        if r["participant_id"] in participants:
            inserts.append((r["participant_id"], "oracle", ""))

    # --- Le Grimpeur (répétable, une par journée où l'on est meilleur grimpeur) ---
    cl_rows = await db.execute(
        "SELECT participant_id, sporting_day FROM sporting_day_rank_evolutions WHERE is_climber=1"
    )
    for r in await cl_rows.fetchall():
        if r["participant_id"] in participants:
            inserts.append((r["participant_id"], "grimpeur", r["sporting_day"]))

    # --- L'Éternel Deuxième : argmax de "presque" (figé en fin de tournoi) ---
    if all_done and near_miss_by_pid:
        best = max(near_miss_by_pid.values())
        if best > 0:
            for pid, n in near_miss_by_pid.items():
                if n == best:
                    inserts.append((pid, "eternel_deuxieme", ""))

    # --- La Cuillère en Bois : dernier du général parmi les assidus (fin) ---
    if all_done:
        rankings = await get_rankings(db, scope="general")
        eligible = [
            r for r in rankings
            if pred_count.get(r["id"], 0) >= CUILLERE_MIN_SHARE * total_matches
        ]
        if eligible:
            worst = min(r["total_points"] for r in eligible)
            for r in eligible:
                if r["total_points"] == worst:
                    inserts.append((r["id"], "cuillere_en_bois", ""))

    # --- Champions de département (fin de poules / fin de tournoi) ---
    if group_done:
        groups_rk = await get_rankings(db, scope="groups")
        for pid in _top_department_member_ids(groups_rk):
            if pid in participants:
                inserts.append((pid, "champion_poules", ""))
    if all_done:
        general_rk = await get_rankings(db, scope="general")
        for pid in _top_department_member_ids(general_rk):
            if pid in participants:
                inserts.append((pid, "champion_tournoi", ""))

    # --- Le Jumeau (secret, répétable) : 12 mêmes scores exacts consécutifs ---
    inserts.extend(await _compute_jumeaux(db, participants))

    if inserts:
        await db.executemany(
            "INSERT OR IGNORE INTO trophy_awards (participant_id, trophy_key, detail) "
            "VALUES (?,?,?)",
            inserts,
        )


async def _compute_jumeaux(db, participants: dict) -> list[tuple]:
    """Paires de joueurs ayant tapé les mêmes scores exacts sur >= JUMEAU_CONSEC
    matchs consécutifs (ordre chronologique). Chaque membre reçoit une occurrence
    dont le `detail` est l'id de son jumeau."""
    rows = await db.execute(
        """SELECT pr.participant_id AS pid, pr.match_id AS mid,
                  pr.exact_score_team1 AS s1, pr.exact_score_team2 AS s2
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.exact_score_team1 IS NOT NULL AND pr.exact_score_team2 IS NOT NULL
           ORDER BY m.match_date, m.kickoff_time, pr.match_id"""
    )
    order: list[int] = []
    seen_mid: set[int] = set()
    scores: dict[int, dict[int, tuple]] = defaultdict(dict)
    for r in await rows.fetchall():
        if r["mid"] not in seen_mid:
            seen_mid.add(r["mid"])
            order.append(r["mid"])
        if r["pid"] in participants:
            scores[r["pid"]][r["mid"]] = (r["s1"], r["s2"])

    pids = sorted(scores)
    out: list[tuple] = []
    for i in range(len(pids)):
        a = pids[i]
        sa = scores[a]
        for j in range(i + 1, len(pids)):
            b = pids[j]
            sb = scores[b]
            run = 0
            twins = False
            for mid in order:
                va, vb = sa.get(mid), sb.get(mid)
                if va is not None and vb is not None and va == vb:
                    run += 1
                    if run >= JUMEAU_CONSEC:
                        twins = True
                        break
                else:
                    run = 0
            if twins:
                out.append((a, "le_jumeau", str(b)))
                out.append((b, "le_jumeau", str(a)))
    return out


# ---------------------------------------------------------------------------
# Lecture pour l'affichage (cabinet & classement)
# ---------------------------------------------------------------------------

async def build_cabinet(db, participant_id: int) -> dict:
    """Cabinet d'un participant, lu depuis `trophy_awards` (archive permanente).

    Renvoie les groupes par catégorie (chaque item : débloqué ou non, compteur
    ×N, secret, et pour Le Jumeau la liste des noms de jumeaux), plus un résumé.
    """
    rows = await db.execute(
        "SELECT trophy_key, detail, awarded_at FROM trophy_awards "
        "WHERE participant_id=? ORDER BY awarded_at",
        (participant_id,),
    )
    awarded: dict[str, list[dict]] = defaultdict(list)
    for r in await rows.fetchall():
        awarded[r["trophy_key"]].append({"detail": r["detail"], "awarded_at": r["awarded_at"]})

    # Noms des jumeaux (detail = id du jumeau)
    twin_names: dict[str, str] = {}
    twin_ids = [int(o["detail"]) for o in awarded.get("le_jumeau", []) if o["detail"].isdigit()]
    if twin_ids:
        placeholders = ",".join("?" * len(twin_ids))
        nrows = await db.execute(
            f"SELECT id, name, nickname FROM participants WHERE id IN ({placeholders})",
            twin_ids,
        )
        for r in await nrows.fetchall():
            twin_names[str(r["id"])] = r["nickname"] or r["name"]

    # Étagères PAR RARETÉ (plus de catégories) : du plus prestigieux au moins.
    rarity_shelves = [
        ("legendary", "Légendaires"),
        ("rare", "Rares"),
        ("common", "Communs"),
        ("anti", "Anti-trophées"),
    ]
    groups = []
    unlocked_count = 0
    for rar_key, rar_label in rarity_shelves:
        items = []
        for t in TROPHIES:
            if t["rarity"] != rar_key:
                continue
            occ = awarded.get(t["key"], [])
            unlocked = bool(occ)
            if unlocked:
                unlocked_count += 1
            hidden = t["secret"] and not unlocked
            item = {
                "key": t["key"], "icon": t["icon"], "label": t["label"],
                "icon_key": t["icon_key"], "rarity": t["rarity"],
                "category": t["category"], "secret": t["secret"], "repeatable": t["repeatable"],
                "caption": t["caption"], "desc": t["desc"],
                "unlocked": unlocked, "hidden": hidden, "count": len(occ),
                "just_unlocked": False,
            }
            if t["key"] == "le_jumeau" and occ:
                item["twins"] = [twin_names.get(o["detail"], "?") for o in occ]
            items.append(item)
        groups.append({"key": rar_key, "label": rar_label, "items": items})

    return {
        "groups": groups,
        "unlocked_count": unlocked_count,
        "total": len(TROPHIES),
        "unlocked_keys": {k for k, occ in awarded.items() if occ},
    }


async def latest_ephemeral_badges(db) -> dict[int, dict]:
    """Badge éphémère par joueur pour le classement : le trophée décroché PENDANT
    la dernière journée sportive finalisée uniquement (fenêtre [start, end[).
    Les trophées de la journée en cours ne sont pas encore visibles.

    En cas d'égalité de date, le plus rare (moins de détenteurs) l'emporte.

    Renvoie {participant_id: {"key","icon","label","icon_key","rarity","caption","detail_label"}}.
    """
    from app.timeutils import sporting_day_bounds, format_sporting_day_fr

    day = (await (await db.execute(
        "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
    )).fetchone())["day"]
    if not day:
        return {}
    start, end = sporting_day_bounds(day)

    holders_rows = await db.execute(
        "SELECT trophy_key, COUNT(DISTINCT participant_id) AS c FROM trophy_awards GROUP BY trophy_key"
    )
    holders = {r["trophy_key"]: r["c"] for r in await holders_rows.fetchall()}

    rows = await db.execute(
        "SELECT participant_id, trophy_key, detail, awarded_at FROM trophy_awards "
        "WHERE datetime(awarded_at) >= datetime(?) AND datetime(awarded_at) < datetime(?)",
        (start, end),
    )
    candidates = [dict(r) for r in await rows.fetchall()]
    if not candidates:
        return {}

    detail_ids = set()
    for c in candidates:
        if c["detail"] and c["detail"].isdigit():
            detail_ids.add(int(c["detail"]))
    match_names: dict[int, str] = {}
    participant_names: dict[int, str] = {}
    if detail_ids:
        ph = ",".join("?" * len(detail_ids))
        id_list = list(detail_ids)
        mrows = await db.execute(
            f"SELECT id, team1_name, team2_name, score_team1, score_team2 FROM matches WHERE id IN ({ph})",
            id_list,
        )
        for r in await mrows.fetchall():
            match_names[r["id"]] = f"{r['team1_name']} {r['score_team1']}-{r['score_team2']} {r['team2_name']}"
        prows = await db.execute(
            f"SELECT id, name, nickname FROM participants WHERE id IN ({ph})",
            id_list,
        )
        for r in await prows.fetchall():
            participant_names[r["id"]] = r["nickname"] or r["name"]

    best: dict[int, tuple] = {}
    chosen: dict[int, dict] = {}
    for r in candidates:
        meta = TROPHY_BY_KEY.get(r["trophy_key"])
        if not meta:
            continue
        rank_key = (r["awarded_at"], -holders.get(r["trophy_key"], 0))
        if r["participant_id"] not in best or rank_key > best[r["participant_id"]]:
            best[r["participant_id"]] = rank_key
            detail_label = _detail_label(meta["key"], r["detail"], format_sporting_day_fr,
                                         match_names, participant_names)
            chosen[r["participant_id"]] = {
                "key": meta["key"], "icon": meta["icon"], "label": meta["label"],
                "icon_key": meta["icon_key"], "rarity": meta["rarity"],
                "caption": meta["caption"], "detail_label": detail_label,
            }
    return chosen


def _detail_label(key: str, detail: str, fmt_day, match_names: dict, participant_names: dict) -> str:
    if not detail:
        return ""
    if key in ("grimpeur", "journee_parfaite"):
        return f"Journée du {fmt_day(detail)}"
    if key == "extraterrestre":
        if detail.isdigit():
            return match_names.get(int(detail), "")
        return ""
    if key == "le_jumeau":
        if detail.isdigit():
            name = participant_names.get(int(detail))
            return f"avec {name}" if name else ""
        return ""
    return ""
