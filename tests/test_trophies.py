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
    build_cabinet, latest_ephemeral_badges, _top_department_member_ids,
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
    _predict(pid, _mk_match(result="team1", s1=2, s2=1), ps1=2, ps2=1)
    _refresh()
    assert ("sniper", "") in _awards(pid)        # SNIPER_EXACT exacts


def test_la_serie_consecutive():
    pid = _new_participant("Serie")
    # 8 bons résultats d'affilée (dates croissantes => ordre chronologique)
    for i in range(8):
        _predict(pid, _mk_match(result="team1", s1=1, s2=0, date=f"2099-02-{i + 1:02d}"),
                 pred="team1", ps1=3, ps2=0)  # bon résultat, score non exact
    _refresh()
    assert "la_serie" in _keys(pid)


def test_roi_du_nul():
    pid = _new_participant("Nul")
    for i in range(5):
        _predict(pid, _mk_match(result="draw", s1=1, s2=1, date=f"2099-03-{i + 1:02d}"),
                 pred="draw", ps1=0, ps2=0)
    _refresh()
    assert "roi_du_nul" in _keys(pid)


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


# --- Badge éphémère du classement -----------------------------------------

def test_latest_ephemeral_badge_recent_then_rarest():
    from app.timeutils import sporting_day_bounds
    pid = _new_participant("Vitrine")
    jp_ids = [_new_participant(f"JP{k}") for k in range(3)]
    # Journée volontairement maximale (postérieure à toute autre du jeu de test).
    day = "2099-12-31"
    start, _ = sporting_day_bounds(day)

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
                "INSERT INTO trophy_awards (participant_id, trophy_key, detail, awarded_at) VALUES (?,?,?,?)",
                (pid, "extraterrestre", "z1", start),
            )
            await db.execute(
                "INSERT INTO trophy_awards (participant_id, trophy_key, detail, awarded_at) VALUES (?,?,?,?)",
                (pid, "journee_parfaite", day, start),
            )
            # Rend journee_parfaite plus commun (3 autres détenteurs).
            for k, jp in enumerate(jp_ids):
                await db.execute(
                    "INSERT INTO trophy_awards (participant_id, trophy_key, detail, awarded_at) VALUES (?,?,?,?)",
                    (jp, "journee_parfaite", f"x{k}", start),
                )
            await db.commit()
    run(_seed_and_award())

    async def _badges():
        async with get_db() as db:
            return await latest_ephemeral_badges(db)
    badges = run(_badges())
    # À égalité de date, le plus rare (extraterrestre) l'emporte.
    assert badges[pid]["key"] == "extraterrestre"


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
