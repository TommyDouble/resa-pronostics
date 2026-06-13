"""W1 — Le Reveal du jour : page, point d'entrée accueil, promo story."""
from app.database import get_db
from tests.conftest import run


def _seed_settled_match(number, s1, s2, result="team1", match_date="2099-12-31"):
    # Date volontairement très lointaine : la base de test est partagée en
    # session et le Reveal cible la DERNIÈRE journée jouée. Une date max
    # déterministe garantit que c'est bien ce match qui est révélé.
    async def _c():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'Groupe R', ?, '12:00:00', 'France', 'Brésil', 1, ?, ?, ?)""",
                (number, match_date, s1, s2, result),
            )
            await db.commit()
            return cur.lastrowid

    return run(_c())


def _seed_pred_score(pid, mid, es1, es2, points):
    async def _c():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?, 'team1', ?, ?)""",
                (pid, mid, es1, es2),
            )
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()

    run(_c())


def _last_revealed(pid):
    async def _g():
        async with get_db() as db:
            row = await db.execute(
                "SELECT last_revealed_date FROM participants WHERE id=?", (pid,)
            )
            return (await row.fetchone())["last_revealed_date"]

    return run(_g())


def test_reveal_page_shows_settled_day_and_marks_seen(client, participant):
    mid = _seed_settled_match(960001, 2, 0, "team1", match_date="2099-12-31")
    _seed_pred_score(participant["id"], mid, 2, 0, 5)  # score exact

    html = client.get(f"/p/{participant['token']}/reveal").text
    assert "Le Reveal du jour" in html
    assert "2–0" in html
    assert "+5 pts" in html
    assert 'data-has-exact="1"' in html

    # La journée (la plus récente jouée) est marquée comme vue.
    assert _last_revealed(participant["id"]) == "2099-12-31"


def test_home_shows_reveal_entry_and_promo(client, participant):
    mid = _seed_settled_match(960002, 1, 1, "draw", match_date="2098-12-31")
    _seed_pred_score(participant["id"], mid, 1, 1, 5)

    html = client.get(f"/p/{participant['token']}").text
    # Point d'entrée Reveal présent tant que la journée n'a pas été vue.
    assert "reveal-entry" in html
    assert "Le Reveal du jour est prêt" in html
    # La story promo (template_key reveal_promo, seedée par défaut) est rendue
    # en parcours multi-écrans : une "feature" titrée + au moins 3 écrans.
    assert 'data-story-feature' in html
    assert 'data-title="Le Reveal du jour"' in html
    assert html.count("data-story-screen") >= 3
    assert "rp-demo" in html


def test_reveal_empty_state_when_no_results(client):
    # Un participant tout neuf, base sans résultat garanti pour CE token :
    # on vérifie juste que la page répond (200) même si une autre journée existe.
    import uuid
    token = str(uuid.uuid4())

    async def _mk():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed)
                   VALUES ('Reveal Empty', ?, ?, 1)""",
                (f"{token}@test.local", token),
            )
            await db.commit()

    run(_mk())
    res = client.get(f"/p/{token}/reveal")
    assert res.status_code == 200
