#!/usr/bin/env python3
"""Harnais de calibration des trophées (charte W8).

Deux sorties, sur les données RÉELLES de la base (via DATABASE_URL) :

1. DÉTENTEURS — pour chaque trophée, nombre de détenteurs au seuil COURANT,
   pourcentage du peloton et répartition par département. Sert à vérifier la
   règle de rareté « < 30 % » (sauf lauréats uniques / répétables).

2. DISTRIBUTIONS — pour les trophées À SEUIL, le nombre (et %) de joueurs qui
   atteindraient chaque palier candidat. On lit directement le seuil à choisir
   pour viser une rareté (ex. ~5 % = légendaire, ~15 % = rare).

Usage :
    DATABASE_URL=/chemin/vers/copie_prod.db .venv/bin/python -m scripts.simulate_trophies

IMPORTANT — travailler sur une COPIE : la prod tourne peut-être encore l'ancien
code, donc l'outil applique d'abord les migrations du nouveau système (colonne
`detail`, recalcul des trophées) sur la base fournie. Il MODIFIE donc la copie
(jamais la prod). Ne sort que des AGRÉGATS (compteurs, %, départements) — aucun
nom ni pronostic.
"""
import asyncio
from collections import defaultdict

from app.database import get_db, init_db
from app.scoring import is_match_prediction_correct, is_match_score_exact
from app.timeutils import sporting_day
from app.trophies import (
    TROPHIES, refresh_trophy_awards,
    SNIPER_EXACT, SERIE_STREAK, ROI_NUL_DRAWS, TUEUR_UPSETS, UPSET_SHARE,
    SAVANT_EXOTIC, EXOTIC_SHARE, SANGFROID_MIN_KO, SANGFROID_RATE, JUMEAU_CONSEC,
)

# Paliers candidats à explorer par trophée à seuil (autour du seuil courant).
CANDIDATES = {
    "sniper":           ("scores exacts",              [4, 6, 8, 10, 12, 15, 18]),
    "la_serie":         ("bons résultats d'affilée",   [4, 5, 6, 7, 8, 10, 12]),
    "roi_du_nul":       ("nuls de poule trouvés",      [2, 3, 4, 5, 6, 8]),
    "tueur_de_favoris": ("surprises (issue <25%)",     [2, 3, 4, 5, 6]),
    "savant_fou":       ("scores exotiques ratés",     [6, 8, 10, 12, 15, 20]),
    "le_jumeau":        ("mêmes scores consécutifs",   [8, 10, 12, 14, 16]),
}


async def _compute_metrics(db, participants):
    """Métriques par participant pour les trophées à seuil (lecture seule)."""
    mrows = await db.execute(
        "SELECT id, phase, match_date, kickoff_time, score_team1, score_team2, "
        "result, qualifier_winner FROM matches WHERE result IS NOT NULL"
    )
    matches = {r["id"]: dict(r) for r in await mrows.fetchall()}

    prows = await db.execute(
        """SELECT pr.participant_id, pr.match_id,
                  m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  m.match_date, m.kickoff_time,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction
           FROM predictions pr JOIN matches m ON m.id = pr.match_id
           WHERE m.result IS NOT NULL"""
    )
    played = defaultdict(list)
    by_match = defaultdict(list)
    for r in await prows.fetchall():
        d = dict(r)
        played[d["participant_id"]].append(d)
        by_match[d["match_id"]].append(d)

    # Distributions par match (issue correcte ; scoreline exotique).
    outcome_stats, score_freq = {}, {}
    for mid, lst in by_match.items():
        total = len(lst)
        correct = sum(1 for r in lst if is_match_prediction_correct(r, r))
        outcome_stats[mid] = (total, correct)
        counts, scored = defaultdict(int), 0
        for r in lst:
            if r["exact_score_team1"] is not None and r["exact_score_team2"] is not None:
                counts[(r["exact_score_team1"], r["exact_score_team2"])] += 1
                scored += 1
        score_freq[mid] = (counts, scored)

    m = {pid: {} for pid in participants}
    for pid in participants:
        lst = played.get(pid, [])
        m[pid]["sniper"] = sum(1 for r in lst if is_match_score_exact(r, r))
        m[pid]["roi_du_nul"] = sum(
            1 for r in lst
            if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
        )
        run = longest = 0
        for r in sorted(lst, key=lambda r: (r["match_date"], r["kickoff_time"])):
            run = run + 1 if is_match_prediction_correct(r, r) else 0
            longest = max(longest, run)
        m[pid]["la_serie"] = longest
        upsets = 0
        for r in lst:
            if is_match_prediction_correct(r, r):
                tot, cor = outcome_stats[r["match_id"]]
                if tot > 0 and cor / tot < UPSET_SHARE:
                    upsets += 1
        m[pid]["tueur_de_favoris"] = upsets
        exo = 0
        for r in lst:
            if r["exact_score_team1"] is None or r["exact_score_team2"] is None:
                continue
            counts, scored = score_freq[r["match_id"]]
            if scored > 0 and counts[(r["exact_score_team1"], r["exact_score_team2"])] / scored < EXOTIC_SHARE \
                    and not is_match_score_exact(r, r):
                exo += 1
        m[pid]["savant_fou"] = exo
        ko = [r for r in lst if r["phase"] != "group"]
        m[pid]["_ko_played"] = len(ko)
        m[pid]["_ko_correct"] = sum(1 for r in ko if is_match_prediction_correct(r, r))

    # Jumeau : plus longue série de scores identiques avec un autre joueur.
    allp = await db.execute(
        """SELECT pr.participant_id AS pid, pr.match_id AS mid,
                  pr.exact_score_team1 AS s1, pr.exact_score_team2 AS s2
           FROM predictions pr JOIN matches mm ON mm.id = pr.match_id
           WHERE pr.exact_score_team1 IS NOT NULL AND pr.exact_score_team2 IS NOT NULL
           ORDER BY mm.match_date, mm.kickoff_time, pr.match_id"""
    )
    order, seen, scores = [], set(), defaultdict(dict)
    for r in await allp.fetchall():
        if r["mid"] not in seen:
            seen.add(r["mid"]); order.append(r["mid"])
        if r["pid"] in participants:
            scores[r["pid"]][r["mid"]] = (r["s1"], r["s2"])
    best_twin = {pid: 0 for pid in participants}
    pids = sorted(scores)
    for i in range(len(pids)):
        a = pids[i]
        for j in range(i + 1, len(pids)):
            b = pids[j]; run = 0
            for mid in order:
                va, vb = scores[a].get(mid), scores[b].get(mid)
                run = run + 1 if (va is not None and va == vb) else 0
                if run > best_twin[a]: best_twin[a] = run
                if run > best_twin[b]: best_twin[b] = run
    for pid in participants:
        m[pid]["le_jumeau"] = best_twin[pid]
    return m


async def _simulate():
    # Aligne le schéma de la copie sur le nouveau code (colonne `detail`, etc.)
    # et calcule les trophées du nouveau système sur les vrais pronos.
    await init_db()
    async with get_db() as db:
        prows = await db.execute(
            "SELECT id, department FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        dept_of = {
            r["id"]: (r["department"] or "").strip() or "Sans département"
            for r in await prows.fetchall()
        }
        field = len(dept_of)
        if not field:
            print("Aucun participant confirmé : rien à simuler.")
            return

        # Mesure « à blanc » : on repart d'une table vide (annulé au ROLLBACK)
        # pour refléter UNIQUEMENT le calcul du code courant, sans awards hérités.
        await db.execute("DELETE FROM trophy_awards")
        await refresh_trophy_awards(db)
        rows = await db.execute("SELECT trophy_key, participant_id FROM trophy_awards")
        holders = defaultdict(set)
        for r in await rows.fetchall():
            holders[r["trophy_key"]].add(r["participant_id"])
        metrics = await _compute_metrics(db, dept_of)
        await db.rollback()

    print(f"\nPeloton : {field} participants confirmés\n")
    print("=" * 64, "\n1) DÉTENTEURS au seuil courant\n", "=" * 64, sep="")
    hdr = f"{'Trophée':<22}{'Détenteurs':>11}{'%':>7}  Départements"
    print(hdr)
    for t in TROPHIES:
        hs = holders.get(t["key"], set())
        pct = round(len(hs) / field * 100, 1)
        by = defaultdict(int)
        for pid in hs:
            by[dept_of.get(pid, "?")] += 1
        spread = ", ".join(f"{d}:{c}" for d, c in sorted(by.items(), key=lambda x: -x[1])) or "—"
        flag = " ⚠️>30%" if pct > 30 and not t["repeatable"] else ""
        print(f"{t['label']:<22}{len(hs):>11}{pct:>6}%  {spread}{flag}")

    print("\n", "=" * 64, "\n2) DISTRIBUTIONS — joueurs ≥ palier (choisir le seuil cible)\n", "=" * 64, sep="")
    cur = {"sniper": SNIPER_EXACT, "la_serie": SERIE_STREAK, "roi_du_nul": ROI_NUL_DRAWS,
           "tueur_de_favoris": TUEUR_UPSETS, "savant_fou": SAVANT_EXOTIC, "le_jumeau": JUMEAU_CONSEC}
    label = {t["key"]: t["label"] for t in TROPHIES}
    for key, (unit, paliers) in CANDIDATES.items():
        vals = [metrics[pid][key] for pid in dept_of]
        print(f"\n{label[key]} — {unit} (seuil courant : {cur[key]})")
        for n in paliers:
            c = sum(1 for v in vals if v >= n)
            pct = round(c / field * 100, 1)
            mark = "  ← courant" if n == cur[key] else ""
            print(f"   ≥ {n:<3} : {c:>3} joueurs ({pct:>5}%){mark}")

    print("\nSang-Froid — taux de bons résultats en phase finale "
          f"(min. {SANGFROID_MIN_KO} matchs ; seuil courant : {int(SANGFROID_RATE*100)}%)")
    elig = [metrics[pid] for pid in dept_of if metrics[pid]["_ko_played"] >= SANGFROID_MIN_KO]
    if elig:
        for rate in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8):
            c = sum(1 for x in elig if x["_ko_correct"] / x["_ko_played"] >= rate)
            mark = "  ← courant" if abs(rate - SANGFROID_RATE) < 1e-6 else ""
            print(f"   ≥ {int(rate*100)}% : {c:>3} joueurs (sur {len(elig)} éligibles){mark}")
    else:
        print(f"   (aucun joueur n'a encore {SANGFROID_MIN_KO}+ matchs de phase finale joués)")
    print()


if __name__ == "__main__":
    asyncio.run(_simulate())
