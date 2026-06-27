"""Cabinet à trophées — refonte W8 (source de vérité unique).

Philosophie (cf. charte de gamification) :
- Un trophée doit marquer un ÉVÉNEMENT RARE. Tous binaires (pas de paliers).
- Le classement est un FIL D'ACTUALITÉ : un seul badge par joueur, choisi parmi
  ceux gagnés pendant la dernière journée sportive finalisée (cf.
  `latest_ephemeral_badges`). Le cabinet est l'ARCHIVE permanente, avec le
  détail daté de chaque occurrence (cf. `build_cabinet`).
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

MATCH_ORIGIN_TROPHIES = {"extraterrestre", "sniper", "la_serie", "roi_du_nul"}
MATCH_MILESTONE_TROPHIES = {"sniper", "la_serie", "roi_du_nul"}
CAROUSEL_VISIBLE_LIMIT = 4
TROPHY_CAROUSEL_VISIBLE_WINNERS = 3
BILAN_ORIGIN_LABELS = {
    "oracle": "Prono pré-tournoi",
    "sang_froid": "Bilan phase finale",
    "tueur_de_favoris": f"{TUEUR_UPSETS} surprises trouvées",
    "eternel_deuxieme": "À un but près, encore.",
    "cuillere_en_bois": "Dernier, mais officiellement décoré.",
    "savant_fou": "Des scores venus du labo.",
    "champion_poules": "Département en tête fin des poules",
    "champion_tournoi": "Département en tête fin du tournoi",
}

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

    # Journées sportives : source de vérité de la date métier des trophées.
    # ``awarded_at`` n'est qu'une date d'écriture en base.
    all_matches_by_sday: dict[str, list[int]] = defaultdict(list)
    match_sdays: dict[int, str] = {}
    for mid, m in matches.items():
        sday = sporting_day(m)
        all_matches_by_sday[sday].append(mid)
        if m["result"] is not None:
            match_sdays[mid] = sday
    group_final_day = max(
        (match_sdays[mid] for mid in group_ids if mid in match_sdays),
        default=None,
    )
    tournament_final_day = max(match_sdays.values(), default=None)
    finalized_row = await db.execute(
        "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
    )
    latest_finalized_day = (await finalized_row.fetchone())["day"]

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

    inserts: list[tuple[int, str, str, str | None]] = []
    near_miss_by_pid: dict[int, int] = {}
    valid_perfect_days_by_pid: dict[int, set[str]] = defaultdict(set)

    # --- Trophées par participant (continus + métriques relatives) ---
    for pid, lst in played_by_pid.items():
        ordered = sorted(lst, key=lambda r: (r["match_date"], r["kickoff_time"]))
        exact_rows = [r for r in ordered if is_match_score_exact(r, r)]
        if len(exact_rows) >= SNIPER_EXACT:
            milestone = exact_rows[SNIPER_EXACT - 1]
            inserts.append((
                pid, "sniper", str(milestone["match_id"]),
                match_sdays.get(milestone["match_id"]),
            ))

        run = 0
        serie_day = None
        serie_match_id = None
        for r in ordered:
            if is_match_prediction_correct(r, r):
                run += 1
                if run == SERIE_STREAK and serie_day is None:
                    serie_day = match_sdays.get(r["match_id"])
                    serie_match_id = r["match_id"]
            else:
                run = 0
        if serie_day and serie_match_id:
            inserts.append((pid, "la_serie", str(serie_match_id), serie_day))

        draw_rows = [
            r for r in ordered
            if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
        ]
        if len(draw_rows) >= ROI_NUL_DRAWS:
            milestone = draw_rows[ROI_NUL_DRAWS - 1]
            inserts.append((
                pid, "roi_du_nul", str(milestone["match_id"]),
                match_sdays.get(milestone["match_id"]),
            ))

        near_miss_by_pid[pid] = sum(
            1 for r in lst
            if is_match_prediction_correct(r, r) and not is_match_score_exact(r, r)
            and r.get("exact_score_team1") is not None and r.get("score_team1") is not None
            and (abs(r["exact_score_team1"] - r["score_team1"])
                 + abs(r["exact_score_team2"] - r["score_team2"])) == 1
        )

        # Journée parfaite (répétable) : 100 % de bons résultats ET au moins
        # PERFECT_MIN_EXACT scores exacts sur une journée sportive finalisée.
        owned = {r["match_id"]: r for r in lst}
        for sday, mids in all_matches_by_sday.items():
            if len(mids) >= PERFECT_MIN_MATCHES \
                    and all(matches[mid]["result"] is not None for mid in mids) \
                    and all(mid in owned for mid in mids) \
                    and all(is_match_prediction_correct(owned[mid], owned[mid]) for mid in mids) \
                    and sum(1 for mid in mids if is_match_score_exact(owned[mid], owned[mid])) >= PERFECT_MIN_EXACT:
                inserts.append((pid, "journee_parfaite", sday, sday))
                valid_perfect_days_by_pid[pid].add(sday)

        # Extraterrestre (secret, répétable, une par match fou réussi)
        for r in lst:
            if is_match_score_exact(r, r):
                s1, s2 = r["score_team1"], r["score_team2"]
                if (s1 + s2) >= EXTRA_SUM or abs(s1 - s2) >= EXTRA_DIFF:
                    inserts.append((
                        pid, "extraterrestre", str(r["match_id"]),
                        match_sdays.get(r["match_id"]),
                    ))

        # Tueur de favoris — figé en fin de tournoi
        if all_done:
            upsets = 0
            for r in lst:
                if is_match_prediction_correct(r, r):
                    total, correct = outcome_stats[r["match_id"]]
                    if total > 0 and correct / total < UPSET_SHARE:
                        upsets += 1
            if upsets >= TUEUR_UPSETS:
                inserts.append((pid, "tueur_de_favoris", "", tournament_final_day))

            ko = [r for r in lst if r["phase"] != "group"]
            ko_ok = sum(1 for r in ko if is_match_prediction_correct(r, r))
            if len(ko) >= SANGFROID_MIN_KO and ko_ok / len(ko) >= SANGFROID_RATE:
                inserts.append((pid, "sang_froid", "", tournament_final_day))

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
                inserts.append((pid, "savant_fou", "", tournament_final_day))

    # --- L'Oracle : vainqueur du tournoi trouvé au prono pré-tournoi ---
    oracle_rows = await db.execute(
        "SELECT participant_id FROM pre_tournament_scores WHERE question_key='winner' AND points > 0"
    )
    for r in await oracle_rows.fetchall():
        if r["participant_id"] in participants:
            inserts.append((
                r["participant_id"], "oracle", "",
                tournament_final_day if all_done else latest_finalized_day,
            ))

    # --- Le Grimpeur (répétable, une par journée où l'on est meilleur grimpeur) ---
    cl_rows = await db.execute(
        "SELECT participant_id, sporting_day FROM sporting_day_rank_evolutions WHERE is_climber=1"
    )
    for r in await cl_rows.fetchall():
        if r["participant_id"] in participants:
            inserts.append((
                r["participant_id"], "grimpeur", r["sporting_day"], r["sporting_day"],
            ))

    # --- L'Éternel Deuxième : argmax de "presque" (figé en fin de tournoi) ---
    if all_done and near_miss_by_pid:
        best = max(near_miss_by_pid.values())
        if best > 0:
            for pid, n in near_miss_by_pid.items():
                if n == best:
                    inserts.append((pid, "eternel_deuxieme", "", tournament_final_day))

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
                    inserts.append((r["id"], "cuillere_en_bois", "", tournament_final_day))

    # --- Champions de département (fin de poules / fin de tournoi) ---
    if group_done:
        groups_rk = await get_rankings(db, scope="groups")
        for pid in _top_department_member_ids(groups_rk):
            if pid in participants:
                inserts.append((pid, "champion_poules", "", group_final_day))
    if all_done:
        general_rk = await get_rankings(db, scope="general")
        for pid in _top_department_member_ids(general_rk):
            if pid in participants:
                inserts.append((pid, "champion_tournoi", "", tournament_final_day))

    # --- Le Jumeau (secret, répétable) : 12 mêmes scores exacts consécutifs ---
    inserts.extend(await _compute_jumeaux(db, participants, match_sdays))

    await _sync_journee_parfaite_awards(
        db, participants.keys(), valid_perfect_days_by_pid
    )

    if inserts:
        await _upsert_trophy_awards(db, inserts)


async def _sync_journee_parfaite_awards(
    db,
    participant_ids,
    valid_days_by_pid: dict[int, set[str]],
) -> None:
    """Supprime les Journées parfaites invalidées une fois le jour complet."""
    for pid in participant_ids:
        valid_days = sorted(valid_days_by_pid.get(pid, set()))
        if not valid_days:
            await db.execute(
                "DELETE FROM trophy_awards WHERE participant_id=? AND trophy_key='journee_parfaite'",
                (pid,),
            )
            continue
        placeholders = ",".join("?" for _ in valid_days)
        await db.execute(
            f"""DELETE FROM trophy_awards
                WHERE participant_id=? AND trophy_key='journee_parfaite'
                  AND (detail IS NULL OR detail NOT IN ({placeholders}))""",
            (pid, *valid_days),
        )


async def _upsert_trophy_awards(
    db, inserts: list[tuple[int, str, str, str | None]]
) -> None:
    """Insère les trophées et enrichit les anciens détails vides sans doublons."""
    for participant_id, trophy_key, detail, sporting_day in inserts:
        if trophy_key not in MATCH_MILESTONE_TROPHIES or not detail:
            continue
        await db.execute(
            """DELETE FROM trophy_awards
               WHERE participant_id=? AND trophy_key=? AND detail<>?
                 AND EXISTS (
                   SELECT 1 FROM trophy_awards existing
                    WHERE existing.participant_id=?
                      AND existing.trophy_key=?
                      AND existing.detail=?
                 )""",
            (participant_id, trophy_key, detail, participant_id, trophy_key, detail),
        )
        await db.execute(
            """UPDATE trophy_awards
                  SET detail=?,
                      sporting_day=COALESCE(?, sporting_day)
                WHERE participant_id=? AND trophy_key=? AND detail<>?
                  AND NOT EXISTS (
                    SELECT 1 FROM trophy_awards existing
                     WHERE existing.participant_id=?
                       AND existing.trophy_key=?
                       AND existing.detail=?
                  )""",
            (
                detail, sporting_day, participant_id, trophy_key, detail,
                participant_id, trophy_key, detail,
            ),
        )
    await db.executemany(
        "INSERT INTO trophy_awards (participant_id, trophy_key, detail, sporting_day) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(participant_id, trophy_key, detail) DO UPDATE SET "
        "sporting_day=COALESCE(excluded.sporting_day, trophy_awards.sporting_day)",
        inserts,
    )


async def _compute_jumeaux(
    db, participants: dict, match_sdays: dict[int, str]
) -> list[tuple[int, str, str, str | None]]:
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
            twin_day = None
            for mid in order:
                va, vb = sa.get(mid), sb.get(mid)
                if va is not None and vb is not None and va == vb:
                    run += 1
                    if run >= JUMEAU_CONSEC:
                        twin_day = match_sdays.get(mid)
                        break
                else:
                    run = 0
            if twin_day:
                out.append((a, "le_jumeau", str(b), twin_day))
                out.append((b, "le_jumeau", str(a), twin_day))
    return out


# ---------------------------------------------------------------------------
# Lecture pour l'affichage (cabinet & classement)
# ---------------------------------------------------------------------------

async def build_cabinet(db, participant_id: int) -> dict:
    """Cabinet d'un participant, lu depuis `trophy_awards` (archive permanente).

    Renvoie les groupes par catégorie (chaque item : débloqué ou non, compteur
    ×N, secret et historique détaillé de toutes les occurrences), plus un résumé.
    """
    from app.timeutils import format_local_datetime, format_sporting_day_fr

    rows = await db.execute(
        "SELECT id, trophy_key, detail, sporting_day, awarded_at FROM trophy_awards "
        "WHERE participant_id=? "
        "ORDER BY COALESCE(sporting_day, substr(awarded_at, 1, 10)), awarded_at, id",
        (participant_id,),
    )
    award_rows = [dict(r) for r in await rows.fetchall()]
    awarded: dict[str, list[dict]] = defaultdict(list)
    for r in award_rows:
        awarded[r["trophy_key"]].append(r)

    occurrences = [occ for values in awarded.values() for occ in values]
    match_names, participant_names = await _load_detail_context(db, occurrences)

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
                "history": [
                    _occurrence_label(
                        t["key"], o, format_sporting_day_fr, format_local_datetime,
                        match_names, participant_names,
                    )
                    for o in occ
                ],
            }
            if t["key"] == "le_jumeau" and occ:
                item["twins"] = [twin_names.get(o["detail"], "?") for o in occ]
            items.append(item)
        groups.append({"key": rar_key, "label": rar_label, "items": items})

    latest = None
    latest_occurrence = next(
        (occ for occ in reversed(award_rows) if occ["trophy_key"] in TROPHY_BY_KEY),
        None,
    )
    if latest_occurrence:
        meta = TROPHY_BY_KEY[latest_occurrence["trophy_key"]]
        detail_label = _occurrence_label(
            meta["key"], latest_occurrence, format_sporting_day_fr,
            format_local_datetime, match_names, participant_names,
        )
        latest = {
            "key": meta["key"], "label": meta["label"],
            "icon": meta["icon"], "icon_key": meta["icon_key"],
            "rarity": meta["rarity"], "count": len(awarded[meta["key"]]),
            "detail_label": detail_label or "Dernier trophée obtenu",
        }

    return {
        "groups": groups,
        "unlocked_count": unlocked_count,
        "total": len(TROPHIES),
        "unlocked_keys": {k for k, occ in awarded.items() if occ},
        "latest": latest,
    }


async def latest_ephemeral_badges(db) -> dict[int, dict]:
    """Badge par joueur pour le classement : un trophée dont la date MÉTIER est
    exactement la dernière journée sportive finalisée.

    ``awarded_at`` est volontairement ignoré : un backfill effectué aujourd'hui
    ne doit jamais remettre en vitrine un ancien Grimpeur. Les trophées de la
    journée en cours ne sont pas encore visibles.

    Si plusieurs trophées ont été gagnés ce jour-là, le plus rare (moins de
    détenteurs) l'emporte.

    Renvoie {participant_id: {"key","icon","label","icon_key","rarity","caption","detail_label"}}.
    """
    from app.timeutils import format_local_datetime, format_sporting_day_fr

    day = (await (await db.execute(
        "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
    )).fetchone())["day"]
    if not day:
        return {}

    holders_rows = await db.execute(
        "SELECT trophy_key, COUNT(DISTINCT participant_id) AS c FROM trophy_awards GROUP BY trophy_key"
    )
    holders = {r["trophy_key"]: r["c"] for r in await holders_rows.fetchall()}

    rows = await db.execute(
        "SELECT participant_id, trophy_key, detail, sporting_day, awarded_at "
        "FROM trophy_awards WHERE sporting_day=?",
        (day,),
    )
    candidates = [dict(r) for r in await rows.fetchall()]
    if not candidates:
        return {}

    match_names, participant_names = await _load_detail_context(db, candidates)

    best: dict[int, tuple] = {}
    chosen: dict[int, dict] = {}
    for r in candidates:
        meta = TROPHY_BY_KEY.get(r["trophy_key"])
        if not meta:
            continue
        rank_key = (
            -holders.get(r["trophy_key"], 0),
            r["awarded_at"],
            r["trophy_key"],
        )
        if r["participant_id"] not in best or rank_key > best[r["participant_id"]]:
            best[r["participant_id"]] = rank_key
            detail_label = _occurrence_label(
                meta["key"], r, format_sporting_day_fr, format_local_datetime,
                match_names, participant_names,
            )
            chosen[r["participant_id"]] = {
                "key": meta["key"], "icon": meta["icon"], "label": meta["label"],
                "icon_key": meta["icon_key"], "rarity": meta["rarity"],
                "caption": meta["caption"], "detail_label": detail_label,
            }
    return chosen


RARITY_ORDER = {"legendary": 0, "rare": 1, "common": 2, "anti": 3}


async def all_ephemeral_badges(
    db,
    current_participant_id: int | None = None,
) -> tuple[list[dict], dict[int, list[dict]]]:
    """Tous les trophées de la dernière journée sportive finalisée.

    Renvoie (carousel_items, badges_by_participant) :
    - carousel_items : un élément par *type* de trophée, trié par rareté
      (legendary d'abord). Chaque élément contient une liste ``winners``
      regroupant les lauréats de ce trophée.
    - badges_by_participant : {participant_id: [badge, ...]} triés par rareté.
    """
    from app.timeutils import format_local_datetime, format_sporting_day_fr

    day = (await (await db.execute(
        "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
    )).fetchone())["day"]
    if not day:
        return [], {}

    rows = await db.execute(
        "SELECT ta.participant_id, ta.trophy_key, ta.detail, ta.sporting_day, "
        "ta.awarded_at, COALESCE(NULLIF(p.nickname, ''), p.name) AS participant_name, "
        "p.department "
        "FROM trophy_awards ta "
        "JOIN participants p ON p.id = ta.participant_id "
        "WHERE ta.sporting_day = ?",
        (day,),
    )
    candidates = [dict(r) for r in await rows.fetchall()]
    if not candidates:
        return [], {}

    match_names, participant_names = await _load_detail_context(db, candidates)

    climber_rows = await db.execute(
        "SELECT participant_id, delta FROM sporting_day_rank_evolutions "
        "WHERE sporting_day = ? AND is_climber = 1",
        (day,),
    )
    climber_deltas = {r["participant_id"]: r["delta"] for r in await climber_rows.fetchall()}

    grouped: dict[str, dict] = {}
    by_participant: dict[int, list[dict]] = {}

    for r in candidates:
        meta = TROPHY_BY_KEY.get(r["trophy_key"])
        if not meta:
            continue
        full_label = _occurrence_label(
            meta["key"], r, format_sporting_day_fr, format_local_datetime,
            match_names, participant_names,
        )
        event_label = _event_label(
            meta["key"], r, format_sporting_day_fr,
            match_names, participant_names,
        )
        participant_name = (r.get("participant_name") or "").strip()
        winner = {
            "participant_id": r["participant_id"],
            "participant_name": participant_name,
            "department": (r.get("department") or "").strip(),
            "event_label": event_label,
            "detail": r.get("detail") or "",
            "delta": climber_deltas.get(r["participant_id"]) if meta["key"] == "grimpeur" else None,
        }
        if meta["key"] not in grouped:
            grouped[meta["key"]] = {
                "key": meta["key"], "icon": meta["icon"], "label": meta["label"],
                "icon_key": meta["icon_key"], "rarity": meta["rarity"],
                "caption": meta["caption"],
                "winners": [],
            }
        grouped[meta["key"]]["winners"].append(winner)

        by_participant.setdefault(r["participant_id"], []).append({
            "key": meta["key"], "icon": meta["icon"], "label": meta["label"],
            "icon_key": meta["icon_key"], "rarity": meta["rarity"],
            "caption": meta["caption"], "detail_label": full_label,
        })

    day_label = format_sporting_day_fr(day)
    carousel_label = f"Trophées de la dernière journée finalisée · {day_label}"

    for slide in grouped.values():
        key = slide["key"]
        slide["day_label"] = day_label
        slide["carousel_label"] = carousel_label
        winners = slide["winners"]

        context_line = ""
        winner_lines: list[dict] = []

        deltas = {w["delta"] for w in winners if w["delta"] is not None}
        if len(deltas) == 1:
            slide["shared_delta"] = deltas.pop()
            for w in winners:
                w["delta"] = None
        else:
            slide["shared_delta"] = None

        if key in ("champion_poules", "champion_tournoi"):
            departments = _unique_nonempty(w["department"] for w in winners)
            for dept in departments:
                winner_lines.append({"name": dept, "detail": None, "is_me": False})
            member_names = _unique_nonempty(w["participant_name"] for w in winners)
            context_line = _limited_context("Membres", member_names)

        elif key == "le_jumeau":
            seen_pairs: set[frozenset] = set()
            for w in winners:
                pid = w["participant_id"]
                partner_id = int(w.get("detail") or 0)
                pair_key = frozenset([pid, partner_id])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                partner_name = participant_names.get(partner_id, "")
                name = w["participant_name"] or ""
                if name and partner_name:
                    label = f"{name} & {partner_name}"
                else:
                    label = name or partner_name
                is_me = (
                    current_participant_id is not None
                    and current_participant_id in (pid, partner_id)
                )
                winner_lines.append({"name": label, "detail": None, "is_me": is_me})

        else:
            event_labels = {w["event_label"] for w in winners if w["event_label"]}
            shared_context = len(event_labels) == 1

            if shared_context and event_labels:
                context_line = event_labels.pop()

            seen_names: set[str] = set()
            for w in winners:
                name = (w.get("participant_name") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                detail = None
                if not shared_context and w.get("event_label"):
                    detail = _detail_label(
                        key, w.get("detail") or "",
                        format_sporting_day_fr, match_names, participant_names,
                    )
                winner_lines.append({
                    "name": name,
                    "detail": detail or None,
                    "is_me": False,
                })

        current_user_is_winner = False
        if current_participant_id:
            for w in winners:
                if w["participant_id"] == current_participant_id:
                    current_user_is_winner = True
                    break

        if current_user_is_winner and key != "le_jumeau":
            cur_name = ""
            for w in winners:
                if w.get("participant_id") == current_participant_id:
                    cur_name = (w.get("participant_name") or "").strip()
                    break
            if cur_name and key not in ("champion_poules", "champion_tournoi"):
                for i, wl in enumerate(winner_lines):
                    if wl["name"] == cur_name:
                        winner_lines[i]["is_me"] = True
                        winner_lines.insert(0, winner_lines.pop(i))
                        break
            if key == "le_jumeau":
                for i, wl in enumerate(winner_lines):
                    if wl["is_me"]:
                        winner_lines.insert(0, winner_lines.pop(i))
                        break

        visible_limit = TROPHY_CAROUSEL_VISIBLE_WINNERS
        slide["winner_visible_limit"] = visible_limit
        slide["winner_lines"] = winner_lines
        slide["visible_winner_lines"] = winner_lines[:visible_limit]
        slide["overflow_winner_lines"] = winner_lines[visible_limit:]
        slide["context_line"] = context_line
        slide["current_user_is_winner"] = current_user_is_winner
        slide["tip_segments"] = [
            segment for segment in [slide["caption"]]
            if segment
        ]

    carousel = sorted(
        grouped.values(),
        key=lambda item: RARITY_ORDER.get(item["rarity"], 9),
    )
    for badges in by_participant.values():
        badges.sort(key=lambda b: RARITY_ORDER.get(b["rarity"], 9))

    return carousel, by_participant


async def _load_detail_context(db, occurrences: list[dict]) -> tuple[dict[int, str], dict[int, str]]:
    """Charge les libellés nécessaires aux détails de match et de jumeau."""
    from app.result_display import result_full_label

    detail_ids = {
        int(o["detail"])
        for o in occurrences
        if o.get("detail") and o["detail"].isdigit()
    }
    match_names: dict[int, str] = {}
    participant_names: dict[int, str] = {}
    if not detail_ids:
        return match_names, participant_names

    placeholders = ",".join("?" * len(detail_ids))
    id_list = list(detail_ids)
    mrows = await db.execute(
        f"""SELECT id, team1_name, team2_name, phase, score_team1, score_team2,
                   final_score_team1, final_score_team2, result, qualifier_winner
            FROM matches WHERE id IN ({placeholders})""",
        id_list,
    )
    for r in await mrows.fetchall():
        score_label = result_full_label(r).replace("–", "-")
        match_names[r["id"]] = f"{r['team1_name']} {score_label} {r['team2_name']}"
    prows = await db.execute(
        f"SELECT id, name, nickname FROM participants WHERE id IN ({placeholders})",
        id_list,
    )
    for r in await prows.fetchall():
        participant_names[r["id"]] = r["nickname"] or r["name"]
    return match_names, participant_names


def _occurrence_label(
    key: str,
    occurrence: dict,
    fmt_day,
    fmt_datetime,
    match_names: dict,
    participant_names: dict,
) -> str:
    """Libellé daté d'une occurrence, commun au classement et au cabinet."""
    detail = _detail_label(
        key, occurrence.get("detail") or "", fmt_day, match_names, participant_names
    )
    sporting_day = occurrence.get("sporting_day")
    if key in ("grimpeur", "journee_parfaite") and detail:
        return detail

    parts = [detail] if detail else []
    if sporting_day:
        parts.append(f"Journée du {fmt_day(sporting_day)}")
    elif occurrence.get("awarded_at"):
        parts.append(f"Obtenu le {fmt_datetime(occurrence['awarded_at'], '%d/%m/%Y')}")
    return " · ".join(parts)


def _unique_nonempty(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        clean = (value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _limited_items(items: list[str], limit: int = CAROUSEL_VISIBLE_LIMIT) -> tuple[list[str], int]:
    visible = items[:limit]
    return visible, max(0, len(items) - len(visible))


def _limited_context(label: str, items: list[str]) -> str:
    visible, hidden = _limited_items(items)
    if not visible:
        return ""
    parts = [*visible]
    if hidden:
        parts.append(f"+{hidden}")
    return f"{label} : {' · '.join(parts)}"


def _event_label(
    key: str,
    occurrence: dict,
    fmt_day,
    match_names: dict,
    participant_names: dict,
) -> str:
    if key in ("grimpeur", "journee_parfaite"):
        return ""
    detail = _detail_label(
        key, occurrence.get("detail") or "", fmt_day, match_names, participant_names
    )
    if detail:
        if key == "sniper":
            return f"{SNIPER_EXACT}e score exact · {detail}"
        if key == "la_serie":
            return f"{SERIE_STREAK}e bon résultat · {detail}"
        if key == "roi_du_nul":
            return f"{ROI_NUL_DRAWS}e nul trouvé · {detail}"
        return detail
    if key in BILAN_ORIGIN_LABELS:
        return BILAN_ORIGIN_LABELS[key]
    return ""


def _detail_label(key: str, detail: str, fmt_day, match_names: dict, participant_names: dict) -> str:
    if not detail:
        return ""
    if key in ("grimpeur", "journee_parfaite"):
        return f"Journée du {fmt_day(detail)}"
    if key in MATCH_ORIGIN_TROPHIES:
        if detail.isdigit():
            return match_names.get(int(detail), "")
        return ""
    if key == "le_jumeau":
        if detail.isdigit():
            name = participant_names.get(int(detail))
            return f"avec {name}" if name else ""
        return ""
    return ""
