"""Classements par périmètre, remontada, départements, évolution."""
from html import unescape
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from app.constants import DEPARTMENTS
from app.database import get_db
from app.scoring import (
    get_department_rankings,
    get_rankings,
    get_remontada,
)
from tests.conftest import run


def make_participant(name, department=None):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed, department)
                   VALUES (?,?,?,1,?)""",
                (name, f"{token}@test.local", token, department),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


def make_match(phase, number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?,?, '2000-01-01', '12:00', 'A', 'B', ?, 1, 0, 'team1')""",
                (number, phase, 2 if phase != "group" else 1),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def add_score(participant_id, match_id, points):
    async def _add():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (participant_id, match_id, points),
            )
            await db.commit()

    run(_add())


def test_scoped_rankings_and_remontada_and_departments(client):
    alice = make_participant("Alice Scope", "Finances")
    bob = make_participant("Bob Scope", "Expérience Client")
    group_match = make_match("group", 800001)
    ko_match = make_match("quarter", 800002)
    # Alice forte en groupes, Bob fort en phase finale.
    add_score(alice["id"], group_match, 4)
    add_score(bob["id"], group_match, 0)
    add_score(alice["id"], ko_match, 0)
    add_score(bob["id"], ko_match, 6)

    async def _all():
        async with get_db() as db:
            return (
                await get_rankings(db, scope="groups"),
                await get_rankings(db, scope="knockout"),
                await get_remontada(db),
                await get_department_rankings(db),
            )

    groups, knockout, remontada, departments = run(_all())
    g = {r["id"]: r["total_points"] for r in groups}
    k = {r["id"]: r["total_points"] for r in knockout}
    assert g[alice["id"]] == 4 and g[bob["id"]] == 0
    assert k[alice["id"]] == 0 and k[bob["id"]] == 6

    # Remontada: Bob progresse grâce à la phase finale.
    rem = {r["id"]: r for r in remontada}
    assert rem[bob["id"]]["delta"] > 0
    assert rem[bob["id"]]["remontada_rank"] == 1

    dep = {d["department"]: d for d in departments}
    assert dep["Finances"]["members"] >= 1
    assert dep["Finances"]["average"] >= 0


def test_department_rankings_expose_all_members_in_score_order(client):
    department = f"Département détail {uuid.uuid4()}"
    leader = make_participant("Alice Détail", department)
    zero = make_participant("Bob Zéro", department)
    match_id = make_match("group", 800003)
    add_score(leader["id"], match_id, 8)

    async def _departments():
        async with get_db() as db:
            return await get_department_rankings(db)

    row = next(
        d for d in run(_departments())
        if d["department"] == department
    )

    assert row["members"] == 2
    assert row["total"] == 8
    assert row["average"] == 4.0
    assert row["rank"] is not None
    assert row["is_provisional"] is False
    assert row["participants"] == [
        {
            "id": leader["id"],
            "name": "Alice Détail",
            "total_points": 8,
        },
        {
            "id": zero["id"],
            "name": "Bob Zéro",
            "total_points": 0,
        },
    ]


def test_department_ranking_renders_collapsed_member_details(client):
    department = f"Département rendu {uuid.uuid4()}"
    viewer = make_participant("Moi Département", department)
    colleague = make_participant("Collègue Département", department)
    match_id = make_match("group", 800004)
    add_score(colleague["id"], match_id, 7)

    response = client.get(f"/p/{viewer['token']}/classement?view=departments")
    html = response.text

    assert response.status_code == 200
    detail_tags = re.findall(r"<details\b[^>]*data-department-detail[^>]*>", html)
    assert detail_tags
    assert all(" open" not in tag for tag in detail_tags)
    assert 'name="department-ranking"' in html
    assert department in html
    assert "Provisoire" not in html
    assert "2 inscrits" in html
    assert "3,5" in html
    assert "Collègue Département" in html
    assert "Moi Département" in html
    assert f'href="/p/{viewer["token"]}/profil/{colleague["id"]}?return_view=departments' in html
    assert f'href="/p/{viewer["token"]}/profil?return_view=departments' in html
    assert 'class="rrow department-member me"' in html
    assert "c’est toi" in html
    assert 'class="av' not in html
    assert "department-formula" not in html
    assert 'class="ranking-mode-tabs"' in html
    assert 'data-ranking-filters' not in html
    assert 'aria-current="page">Départements' in html


def test_department_official_ties_and_without_department_stays_unranked(client):
    suffix = uuid.uuid4()
    department_a = f"Département officiel A {suffix}"
    department_b = f"Département officiel B {suffix}"
    for index in range(3):
        make_participant(f"A{index} {suffix}", department_a)
        make_participant(f"B{index} {suffix}", department_b)
    make_participant(f"Sans équipe {suffix}")

    async def _departments():
        async with get_db() as db:
            return await get_department_rankings(db)

    departments = run(_departments())
    by_name = {d["department"]: d for d in departments}

    assert by_name[department_a]["is_provisional"] is False
    assert by_name[department_b]["is_provisional"] is False
    assert by_name[department_a]["rank"] == by_name[department_b]["rank"]
    assert by_name[department_a]["rank"] is not None
    assert by_name["Sans département"]["is_provisional"] is True
    assert by_name["Sans département"]["rank"] is None
    assert departments[-1]["department"] == "Sans département"


def test_department_query_opens_valid_detail_and_profile_restores_it(client):
    department = DEPARTMENTS[0]
    viewer = make_participant("Moi Retour", department)
    colleague = make_participant("Collègue Retour", department)

    response = client.get(
        f"/p/{viewer['token']}/classement",
        params={"view": "departments", "department": department},
    )
    html = response.text
    detail_start = html.index(f'data-department-name="{department}"')
    detail_tag = html[html.rfind("<details", 0, detail_start):html.index(">", detail_start) + 1]

    assert " open" in detail_tag
    expected_profile_query = (
        "return_view=departments&return_department=" + quote(department)
    )
    assert f"/profil/{colleague['id']}?{expected_profile_query}" in unescape(html)

    profile_html = client.get(
        f"/p/{viewer['token']}/profil/{colleague['id']}",
        params={"return_view": "departments", "return_department": department},
    ).text
    expected_back = f"/p/{viewer['token']}/classement?" + urlencode({
        "view": "departments",
        "department": department,
    })
    assert expected_back in unescape(profile_html)

    invalid = client.get(
        f"/p/{viewer['token']}/classement",
        params={"view": "departments", "department": "Département inconnu"},
    ).text
    assert all(
        " open" not in tag
        for tag in re.findall(r"<details\b[^>]*data-department-detail[^>]*>", invalid)
    )


def test_large_department_limits_members_but_keeps_current_user_visible(client):
    department = f"Département large {uuid.uuid4()}"
    for index in range(21):
        make_participant(f"Membre {index:02d}", department)
    viewer = make_participant("ZZ Moi", department)

    response = client.get(
        f"/p/{viewer['token']}/classement",
        params={"view": "departments", "department": department},
    )
    html = response.text
    start = html.index(f'data-department-name="{department}"')
    fragment = html[start:html.index("</details>", start)]

    assert fragment.count("data-department-member-hidden") == 16
    assert "Voir les 16 autres" in fragment
    assert "ZZ Moi" in fragment and "c’est toi" in fragment

    expanded = client.get(
        f"/p/{viewer['token']}/classement",
        params={"view": "departments", "department": department, "members": "all"},
    ).text
    start = expanded.index(f'data-department-name="{department}"')
    expanded_fragment = expanded[start:expanded.index("</details>", start)]

    assert "data-members-expanded=\"1\"" in expanded_fragment
    assert "data-department-member-hidden" not in expanded_fragment
    assert "data-department-show-all" not in expanded_fragment
    assert "return_members=all" in unescape(expanded_fragment)


def test_individual_ranking_uses_secondary_filters_and_keeps_legacy_views(client, participant):
    for view in ("general", "groups", "knockout", "bonus", "remontada"):
        response = client.get(f"/p/{participant['token']}/classement?view={view}")
        assert response.status_code == 200
        assert 'class="ranking-mode-tabs"' in response.text
        assert 'data-ranking-filters' in response.text
        assert "Bonus uniquement" in response.text
        assert f'href="/p/{participant["token"]}/classement?view={view}"' in response.text


def test_department_accordion_has_exclusive_js_fallback():
    source = Path("app/static/js/resa.js").read_text()

    assert "function initDepartmentRanking()" in source
    assert "other.open = false" in source
    assert "url.searchParams.set('department'" in source
    assert "data-department-member-hidden" in source
    assert "detail.open = !detail.open" in source
    assert "initRankingFilters();" in source
    assert "initDepartmentRanking();" in source


# L'évolution du classement (piste B, déterministe par journée de kickoff) est
# couverte de façon isolée dans tests/test_rank_evolution_baseline.py.
