"""W8 — Cabinet à trophées : moteur d'attribution + badge éphémère + connexions.

Le système est relatif/global (classements, distributions par match, paires) :
les trophées CONTINUS et RÉPÉTABLES (par participant) sont testés via le refresh
réel ; les trophées « fin de phase » (relatifs au peloton entier) sont gardés par
`all_done`/`group_done` et leur brique pure est testée à part — la base de test
est partagée entre fichiers, donc l'état global de complétion n'est pas fiable.
"""
import uuid

import pytest

from app.database import get_db
from app.routers.pages import _record_daily_visit
from app.timeutils import local_today, current_sporting_day
from app.trophies import (
    TROPHIES, TROPHY_BY_KEY, CATEGORIES, SNIPER_EXACT, refresh_trophy_awards,
    build_cabinet, latest_ephemeral_badges, all_ephemeral_badges,
    _top_department_member_ids,
)
from tests.conftest import run

_seq = iter(range(900000, 999999))


@pytest.fixture(autouse=True, scope="module")
def _init(client):
    """Déclenche le lifespan (init_db) avant tout test du module."""
    return client


def _new_participant(name="J", dept=None):
    async def _c():
        async with get_db() as db:
            tok = str(uuid.uuid4())
            cur = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed, department)
                   VALUES (?,?,?,1,?)""",
                (name, f"{tok}@t.local", tok, dept),
            )
            await db.commit()
            return cur.lastrowid
    return run(_c())


def _participant_token(participant_id):
    async def _c():
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT token FROM participants WHERE id=?",
                (participant_id,),
            )).fetchone()
            return row["token"]

    return run(_c())


def _set_nickname(pid, nickname):
    async def _c():
        async with get_db() as db:
            await db.execute(
                "UPDATE participants SET nickname=? WHERE id=?",
                (nickname, pid),
            )
            await db.commit()

    run(_c())


def _mk_match(result="team1", s1=2, s2=1, phase="group", date="2099-01-01", kickoff="12:00"):
    async def _c():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                   team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?,?,?,?,'A','B',1,?,?,?)""",
                (next(_seq), phase, date, kickoff, s1, s2, result),
            )
            await db.commit()
            return cur.lastrowid
    return run(_c())


def _predict(pid, mid, pred="team1", ps1=2, ps2=1):
    async def _c():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                   exact_score_team1, exact_score_team2) VALUES (?,?,?,?,?)""",
                (pid, mid, pred, ps1, ps2),
            )
            await db.commit()
    run(_c())


def _refresh():
    async def _c():
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_c())


def _awards(pid):
    async def _c():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT trophy_key, detail FROM trophy_awards WHERE participant_id=?",
                (pid,),
            )
            return {(r["trophy_key"], r["detail"]) for r in await rows.fetchall()}
    return run(_c())


def _keys(pid):
    return {k for k, _ in _awards(pid)}


def _cabinet(pid):
    async def _c():
        async with get_db() as db:
            return await build_cabinet(db, pid)
    return run(_c())


def _carousel_items():
    async def _c():
        async with get_db() as db:
            return await all_ephemeral_badges(db)
    return run(_c())[0]


def _seed_carousel_awards(day, awards, climbers=None):
    climbers = climbers or {}

    async def _c():
        async with get_db() as db:
            await db.execute("DELETE FROM trophy_awards WHERE sporting_day=?", (day,))
            await db.execute(
                "DELETE FROM sporting_day_rank_evolutions WHERE sporting_day=?", (day,)
            )
            participant_ids = {participant_id for participant_id, _, _ in awards}
            for participant_id in participant_ids | set(climbers):
                delta = climbers.get(participant_id, 0)
                await db.execute(
                    """INSERT INTO sporting_day_rank_evolutions
                       (sporting_day, participant_id, points_before, day_points,
                        points_after, rank_before, rank_after, delta, is_climber)
                       VALUES (?, ?, 0, 0, 0, 5, 5, ?, ?)""",
                    (day, participant_id, delta, 1 if participant_id in climbers else 0),
                )
            for participant_id, trophy_key, detail in awards:
                await db.execute(
                    """INSERT INTO trophy_awards
                       (participant_id, trophy_key, detail, sporting_day)
                       VALUES (?, ?, ?, ?)""",
                    (participant_id, trophy_key, detail, day),
                )
            await db.commit()

    run(_c())


# --- Catalogue -------------------------------------------------------------

def test_catalog_integrity():
    assert len(TROPHIES) == 15
    valid = {c[0] for c in CATEGORIES}
    keys = set()
    for t in TROPHIES:
        assert t["category"] in valid, t["key"]
        assert t["timing"] in ("continu", "fin_poules", "fin_tournoi")
        assert t["key"] not in keys, "clé dupliquée"
        keys.add(t["key"])
    assert len(TROPHY_BY_KEY) == 15
    # 2 secrets : L'Extraterrestre et Le Jumeau
    assert sum(1 for t in TROPHIES if t["secret"]) == 2


def test_top_department_members_by_average_excludes_no_dept():
    rk = [
        {"id": 1, "department": "A", "total_points": 10},
        {"id": 2, "department": "A", "total_points": 30},   # A moyenne 20
        {"id": 3, "department": "B", "total_points": 26},    # B moyenne 26
        {"id": 4, "department": "", "total_points": 999},    # sans dept : ignoré
    ]
    assert set(_top_department_member_ids(rk)) == {3}


# --- Trophées continus / répétables (via refresh réel) ---------------------

def test_sniper_threshold():
    pid = _new_participant("Sniper")
    for _ in range(SNIPER_EXACT - 1):
        _predict(pid, _mk_match(result="team1", s1=2, s2=1), ps1=2, ps2=1)
    _refresh()
    assert ("sniper", "") not in _awards(pid)   # SNIPER_EXACT-1 exacts : pas encore
    trigger_match = _mk_match(result="team1", s1=2, s2=1)
    _predict(pid, trigger_match, ps1=2, ps2=1)
    _refresh()
    assert ("sniper", str(trigger_match)) in _awards(pid)  # SNIPER_EXACT exacts


def test_la_serie_consecutive():
    pid = _new_participant("Serie")
    # 8 bons résultats d'affilée (dates croissantes => ordre chronologique)
    trigger_match = None
    for i in range(8):
        trigger_match = _mk_match(result="team1", s1=1, s2=0, date=f"2099-02-{i + 1:02d}")
        _predict(pid, trigger_match, pred="team1", ps1=3, ps2=0)  # bon résultat, score non exact
    _refresh()
    assert ("la_serie", str(trigger_match)) in _awards(pid)


def test_refresh_backfills_empty_match_origin_without_duplicate():
    pid = _new_participant("Serie Migrée")
    trigger_match = None
    for i in range(8):
        trigger_match = _mk_match(result="team1", s1=1, s2=0, date=f"2099-09-{i + 1:02d}")
        _predict(pid, trigger_match, pred="team1", ps1=3, ps2=0)

    async def _seed_legacy_award():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day)
                   VALUES (?, 'la_serie', '', '2099-09-08')""",
                (pid,),
            )
            await db.commit()

    run(_seed_legacy_award())
    _refresh()
    awards = _awards(pid)

    assert ("la_serie", "") not in awards
    assert ("la_serie", str(trigger_match)) in awards
    assert sum(1 for key, _ in awards if key == "la_serie") == 1


def test_roi_du_nul():
    pid = _new_participant("Nul")
    trigger_match = None
    for i in range(5):
        trigger_match = _mk_match(result="draw", s1=1, s2=1, date=f"2099-03-{i + 1:02d}")
        _predict(pid, trigger_match, pred="draw", ps1=0, ps2=0)
    _refresh()
    assert ("roi_du_nul", str(trigger_match)) in _awards(pid)


def test_journee_parfaite_repeatable_counts():
    pid = _new_participant("Parfait")
    for day in ("2099-04-01", "2099-04-02"):
        for _ in range(3):
            _predict(pid, _mk_match(result="team1", s1=1, s2=0, date=day),
                     pred="team1", ps1=1, ps2=0)
    _refresh()
    awards = _awards(pid)
    assert ("journee_parfaite", "2099-04-01") in awards
    assert ("journee_parfaite", "2099-04-02") in awards
    cab = _cabinet(pid)
    jp = next(i for g in cab["groups"] for i in g["items"] if i["key"] == "journee_parfaite")
    assert jp["count"] == 2 and jp["unlocked"]
    assert len(jp["history"]) == 2
    assert any("1 avril" in event for event in jp["history"])
    assert any("2 avril" in event for event in jp["history"])


def test_grimpeur_from_evolutions():
    pid = _new_participant("Grimpeur")

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO sporting_day_rank_evolutions
                   (sporting_day, participant_id, points_before, day_points, points_after,
                    rank_before, rank_after, delta, is_climber)
                   VALUES ('2099-05-09', ?, 0, 5, 5, 10, 3, 7, 1)""",
                (pid,),
            )
            await db.commit()
    run(_seed())
    _refresh()
    assert ("grimpeur", "2099-05-09") in _awards(pid)


def test_extraterrestre_secret_hidden_then_unlocked():
    # Participant sans rien : le secret est masqué dans le cabinet
    other = _new_participant("Lambda")
    cab = _cabinet(other)
    extra = next(i for g in cab["groups"] for i in g["items"] if i["key"] == "extraterrestre")
    assert extra["hidden"] is True and extra["unlocked"] is False

    pid = _new_participant("Alien")
    mid = _mk_match(result="team1", s1=5, s2=1, date="2099-06-01")  # 6 buts cumulés
    _predict(pid, mid, pred="team1", ps1=5, ps2=1)
    _refresh()
    assert ("extraterrestre", str(mid)) in _awards(pid)
    cab = _cabinet(pid)
    extra = next(i for g in cab["groups"] for i in g["items"] if i["key"] == "extraterrestre")
    assert extra["unlocked"] is True and extra["hidden"] is False


def test_jumeau_pair_and_twin_name():
    a = _new_participant("Castor")
    b = _new_participant("Pollux")
    # 12 matchs consécutifs (derniers chronologiquement) avec scores identiques
    for i in range(12):
        mid = _mk_match(result="team1", s1=2, s2=1, date=f"2099-12-{i + 1:02d}")
        _predict(a, mid, pred="team1", ps1=3, ps2=2)
        _predict(b, mid, pred="team1", ps1=3, ps2=2)
    _refresh()
    assert ("le_jumeau", str(b)) in _awards(a)
    assert ("le_jumeau", str(a)) in _awards(b)
    cab = _cabinet(a)
    jum = next(i for g in cab["groups"] for i in g["items"] if i["key"] == "le_jumeau")
    assert jum["twins"] == ["Pollux"]


def test_refresh_idempotent():
    pid = _new_participant("Idem")
    _predict(pid, _mk_match(result="team1", s1=5, s2=1, date="2098-01-01"), ps1=5, ps2=1)
    _refresh()
    before = _awards(pid)
    _refresh()
    assert _awards(pid) == before


def test_legacy_history_migration_dates_existing_occurrences():
    from app.database import _migrate_trophy_sporting_day

    pid = _new_participant("Historique migré")

    async def _migrate():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day)
                   VALUES (?, 'journee_parfaite', '2099-08-12', NULL)""",
                (pid,),
            )
            await db.execute(
                "DELETE FROM app_settings WHERE key='migr_trophy_sporting_day_v1'"
            )
            await _migrate_trophy_sporting_day(db)
            row = await (await db.execute(
                """SELECT sporting_day FROM trophy_awards
                   WHERE participant_id=? AND trophy_key='journee_parfaite'""",
                (pid,),
            )).fetchone()
            await db.commit()
            return row["sporting_day"]

    assert run(_migrate()) == "2099-08-12"


# --- Badge éphémère du classement -----------------------------------------

def test_latest_ephemeral_badge_recent_then_rarest():
    from app.timeutils import sporting_day_bounds
    pid = _new_participant("Vitrine")
    jp_ids = [_new_participant(f"JP{k}") for k in range(3)]
    # Journée volontairement maximale (postérieure à toute autre du jeu de test).
    day = "2099-12-31"
    start, end = sporting_day_bounds(day)

    async def _seed_and_award():
        async with get_db() as db:
            # Dernière journée finalisée = celle-ci (MAX sporting_day).
            await db.execute(
                """INSERT INTO sporting_day_rank_evolutions
                   (sporting_day, participant_id, points_before, day_points, points_after,
                    rank_before, rank_after, delta, is_climber)
                   VALUES (?, ?, 0, 0, 0, 5, 5, 0, 0)""",
                (day, pid),
            )
            # Deux trophées du même instant (dans la fenêtre) : un rare, un commun.
            await db.execute(
                "INSERT INTO trophy_awards "
                "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "extraterrestre", "z1", day, start),
            )
            await db.execute(
                "INSERT INTO trophy_awards "
                "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "journee_parfaite", day, day, start),
            )
            # Rend journee_parfaite plus commun (3 autres détenteurs).
            for k, jp in enumerate(jp_ids):
                await db.execute(
                    "INSERT INTO trophy_awards "
                    "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                    "VALUES (?,?,?,?,?)",
                    (jp, "journee_parfaite", f"x{k}", day, start),
                )
            await db.commit()
    run(_seed_and_award())

    async def _badges():
        async with get_db() as db:
            return await latest_ephemeral_badges(db)
    badges = run(_badges())
    # À égalité de date, le plus rare (extraterrestre) l'emporte.
    assert badges[pid]["key"] == "extraterrestre"
    assert "31 décembre" in badges[pid]["detail_label"]


def test_ephemeral_badge_uses_sporting_day_not_insert_time():
    """Un vieux trophée inséré aujourd'hui ne redevient jamais un badge."""
    from app.timeutils import sporting_day_bounds
    pid_old = _new_participant("Ancien")
    pid_future = _new_participant("Futur")
    day = "2100-06-15"
    start, end = sporting_day_bounds(day)

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO sporting_day_rank_evolutions
                   (sporting_day, participant_id, points_before, day_points, points_after,
                    rank_before, rank_after, delta, is_climber)
                   VALUES (?, ?, 0, 0, 0, 5, 5, 0, 0)""",
                (day, pid_old),
            )
            # Les deux écritures ont volontairement un awarded_at situé dans la
            # dernière journée : seule leur vraie date métier doit compter.
            await db.execute(
                "INSERT INTO trophy_awards "
                "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                "VALUES (?,?,?,?,?)",
                (pid_old, "grimpeur", "2100-06-14", "2100-06-14", start),
            )
            await db.execute(
                "INSERT INTO trophy_awards "
                "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                "VALUES (?,?,?,?,?)",
                (pid_future, "grimpeur", "2100-06-16", "2100-06-16", start),
            )
            await db.commit()
    run(_seed())

    async def _badges():
        async with get_db() as db:
            return await latest_ephemeral_badges(db)
    badges = run(_badges())
    assert pid_old not in badges, "trophée de la veille ne doit pas apparaître"
    assert pid_future not in badges, "trophée de la journée suivante ne doit pas apparaître"


def test_ephemeral_badge_detail_label_sporting_day():
    """Le detail_label d'un grimpeur contient la date formatée."""
    from app.timeutils import sporting_day_bounds
    pid = _new_participant("Grimpeur Label")
    day = "2100-07-20"
    start, _ = sporting_day_bounds(day)

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO sporting_day_rank_evolutions
                   (sporting_day, participant_id, points_before, day_points, points_after,
                    rank_before, rank_after, delta, is_climber)
                   VALUES (?, ?, 0, 0, 0, 5, 5, 0, 0)""",
                (day, pid),
            )
            await db.execute(
                "INSERT INTO trophy_awards "
                "(participant_id, trophy_key, detail, sporting_day, awarded_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "grimpeur", day, day, start),
            )
            await db.commit()
    run(_seed())

    async def _badges():
        async with get_db() as db:
            return await latest_ephemeral_badges(db)
    badges = run(_badges())
    assert pid in badges
    assert "20 juillet" in badges[pid]["detail_label"]


def test_trophy_carousel_filters_empty_names_and_shared_match_origin():
    day = "2100-08-01"
    alpha = _new_participant("À moi la cagnotte !")
    beta = _new_participant("Apaches Power")
    _set_nickname(alpha, "")
    match_id = _mk_match(result="team1", s1=5, s2=1, date=day)
    _seed_carousel_awards(
        day,
        [
            (alpha, "extraterrestre", str(match_id)),
            (beta, "extraterrestre", str(match_id)),
        ],
    )

    slide = next(item for item in _carousel_items() if item["key"] == "extraterrestre")

    assert slide["carousel_label"].endswith("1 août")
    assert slide["visible_names"] == ["À moi la cagnotte !", "Apaches Power"]
    assert slide["hidden_count"] == 0
    assert " · · " not in " · ".join(slide["visible_names"])
    assert slide["card_context"] == "A 5-1 B"
    assert slide["detail_hint"] == ""
    assert slide["tip_segments"] == ["Comment. Est-ce. Possible."]


def test_trophy_carousel_keeps_different_match_origins_in_help():
    day = "2100-08-02"
    alpha = _new_participant("Origine Alpha")
    beta = _new_participant("Origine Beta")
    first_match = _mk_match(result="team1", s1=5, s2=1, date=day)
    second_match = _mk_match(result="draw", s1=4, s2=4, date=day)
    _seed_carousel_awards(
        day,
        [
            (alpha, "extraterrestre", str(first_match)),
            (beta, "extraterrestre", str(second_match)),
        ],
    )

    slide = next(item for item in _carousel_items() if item["key"] == "extraterrestre")

    assert slide["card_context"] == ""
    assert slide["detail_hint"] == "Détails par lauréat dans ?"
    assert "Origine Alpha — A 5-1 B" in slide["tip_lines"]
    assert "Origine Beta — A 4-4 B" in slide["tip_lines"]


def test_trophy_carousel_origin_rules_for_day_pair_and_summary():
    day = "2100-08-03"
    climber = _new_participant("Règle Grimpeur")
    twin = _new_participant("Règle Jumeau")
    other_twin = _new_participant("Double Miroir")
    champion = _new_participant("Règle Champion", "Technique et Opérationnel")
    _seed_carousel_awards(
        day,
        [
            (climber, "grimpeur", day),
            (twin, "le_jumeau", str(other_twin)),
            (champion, "champion_tournoi", ""),
        ],
        climbers={climber: 4},
    )

    slides = {item["key"]: item for item in _carousel_items()}

    assert slides["grimpeur"]["card_context"] == ""
    assert slides["grimpeur"]["shared_delta"] == 4
    assert slides["le_jumeau"]["visible_names"] == ["Règle Jumeau avec Double Miroir"]
    assert slides["champion_tournoi"]["visible_names"] == ["Technique et Opérationnel"]
    assert slides["champion_tournoi"]["card_context"] == "Membres : Règle Champion"


def test_trophy_carousel_global_label_and_no_repeated_day(client):
    day = "2100-08-04"
    viewer = _new_participant("Vue Carrousel")
    _seed_carousel_awards(day, [(viewer, "journee_parfaite", day)])

    html = client.get(f"/p/{_participant_token(viewer)}/classement").text
    carousel = html[html.index('data-trophy-carousel'):html.index('class="podium"', html.index('data-trophy-carousel'))]

    assert "Trophées de la dernière journée finalisée" in carousel
    assert "derni&#232;re journ&#233;e finalis&#233;e" not in carousel
    assert "Origine" not in carousel
    assert "Journée du" not in carousel


def test_trophy_carousel_limits_visible_winners_and_keeps_full_list_in_help():
    day = "2100-08-05"
    winners = [_new_participant(f"Lauréat {idx}") for idx in range(6)]
    match_id = _mk_match(result="team1", s1=5, s2=1, date=day)
    _seed_carousel_awards(
        day,
        [(participant_id, "extraterrestre", str(match_id)) for participant_id in winners],
    )

    slide = next(item for item in _carousel_items() if item["key"] == "extraterrestre")

    assert slide["visible_names"] == ["Lauréat 0", "Lauréat 1", "Lauréat 2", "Lauréat 3"]
    assert slide["hidden_count"] == 2
    assert any("Lauréat 5" in segment for segment in slide["tip_segments"])


def test_trophy_carousel_threshold_contexts_are_compact():
    day = "2100-08-06"
    sniper = _new_participant("Seuil Sniper")
    serie = _new_participant("Seuil Série")
    nul = _new_participant("Seuil Nul")
    match_id = _mk_match(result="draw", s1=1, s2=1, date=day)
    _seed_carousel_awards(
        day,
        [
            (sniper, "sniper", str(match_id)),
            (serie, "la_serie", str(match_id)),
            (nul, "roi_du_nul", str(match_id)),
        ],
    )

    slides = {item["key"]: item for item in _carousel_items()}

    assert slides["sniper"]["card_context"] == "14e score exact · A 1-1 B"
    assert slides["la_serie"]["card_context"] == "8e bon résultat · A 1-1 B"
    assert slides["roi_du_nul"]["card_context"] == "5e nul trouvé · A 1-1 B"


def test_trophy_carousel_taquin_and_summary_labels_are_visible():
    day = "2100-08-07"
    second = _new_participant("Anti Deuxième")
    spoon = _new_participant("Anti Cuillère")
    mad = _new_participant("Anti Savant")
    killer = _new_participant("Favoris Killer")
    cold = _new_participant("Sang Froid")
    oracle = _new_participant("Oracle Court")
    _seed_carousel_awards(
        day,
        [
            (second, "eternel_deuxieme", ""),
            (spoon, "cuillere_en_bois", ""),
            (mad, "savant_fou", ""),
            (killer, "tueur_de_favoris", ""),
            (cold, "sang_froid", ""),
            (oracle, "oracle", ""),
        ],
    )

    slides = {item["key"]: item for item in _carousel_items()}

    assert slides["eternel_deuxieme"]["card_context"] == "À un but près, encore."
    assert slides["cuillere_en_bois"]["card_context"] == "Dernier, mais officiellement décoré."
    assert slides["savant_fou"]["card_context"] == "Des scores venus du labo."
    assert slides["tueur_de_favoris"]["card_context"] == "3 surprises trouvées"
    assert slides["sang_froid"]["card_context"] == "Bilan phase finale"
    assert slides["oracle"]["card_context"] == "Prono pré-tournoi"


# --- Série de connexions (inchangé) ---------------------------------------

def test_visit_streak_consecutive_idempotent_and_reset(participant):
    pid = participant["id"]
    today = local_today().strftime("%Y-%m-%d")
    yest = local_today(-1).strftime("%Y-%m-%d")
    older = local_today(-3).strftime("%Y-%m-%d")

    def state():
        async def _c():
            async with get_db() as db:
                r = await (await db.execute(
                    "SELECT last_visit_date, visit_streak, best_visit_streak FROM participants WHERE id=?",
                    (pid,))).fetchone()
                return dict(r)
        return run(_c())

    def visit(last, cur, best):
        async def _c():
            async with get_db() as db:
                await db.execute(
                    "UPDATE participants SET last_visit_date=?, visit_streak=?, best_visit_streak=? WHERE id=?",
                    (last, cur, best, pid))
                await db.commit()
                p = dict(await (await db.execute("SELECT * FROM participants WHERE id=?", (pid,))).fetchone())
                await _record_daily_visit(db, p)
        run(_c())

    visit(yest, 2, 2)
    assert state()["visit_streak"] == 3 and state()["best_visit_streak"] == 3
    visit(today, 3, 3)
    assert state()["visit_streak"] == 3
    visit(older, 9, 9)
    s = state()
    assert s["visit_streak"] == 1 and s["best_visit_streak"] == 9
