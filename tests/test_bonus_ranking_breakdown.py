from html import unescape
import re
import uuid

from app.database import get_db
from app.routers.pages import _load_bonus_ranking_breakdowns
from app.scoring import get_rankings
from tests.conftest import run


_PAST = "2020-01-01T12:00:00"
_FUTURE = "2035-01-01T12:00:00"


def _make_participant(name):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed)
                   VALUES (?, ?, ?, 1)""",
                (name, f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


def _seed_question(text, phase, deadline=_PAST):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, points_value,
                    correct_answer, scoring_mode, is_published, deadline)
                   VALUES (?, ?, 'choice', 5, 'Oui', 'exact', 1, ?)""",
                (text, phase, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_score(participant_id, question_id, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO scores (participant_id, bonus_question_id, points)
                   VALUES (?, ?, ?)""",
                (participant_id, question_id, points),
            )
            await db.commit()

    run(_create())


def _set_pt_state(participant_points):
    async def _set():
        async with get_db() as db:
            deadline_row = await db.execute(
                "SELECT value FROM app_settings WHERE key='pre_tournament_deadline'"
            )
            deadline = await deadline_row.fetchone()
            answer_row = await db.execute(
                "SELECT correct_answer FROM pre_tournament_questions WHERE key='winner'"
            )
            answer = await answer_row.fetchone()
            await db.execute(
                """INSERT INTO app_settings (key, value)
                   VALUES ('pre_tournament_deadline', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (_PAST,),
            )
            await db.execute(
                "UPDATE pre_tournament_questions SET correct_answer='France' WHERE key='winner'"
            )
            for participant_id, points in participant_points.items():
                await db.execute(
                    """INSERT INTO pre_tournament_scores
                       (participant_id, question_key, points)
                       VALUES (?, 'winner', ?)""",
                    (participant_id, points),
                )
            await db.commit()
            return (
                deadline["value"] if deadline else None,
                answer["correct_answer"] if answer else None,
            )

    return run(_set())


def _cleanup(question_ids, participant_ids, pt_participant_ids, old_pt_state):
    async def _clean():
        async with get_db() as db:
            marks = ",".join("?" for _ in question_ids)
            await db.execute(
                f"DELETE FROM bonus_questions WHERE id IN ({marks})", question_ids
            )
            pmarks = ",".join("?" for _ in pt_participant_ids)
            await db.execute(
                f"DELETE FROM pre_tournament_scores WHERE participant_id IN ({pmarks})",
                pt_participant_ids,
            )
            old_deadline, old_answer = old_pt_state
            if old_deadline is None:
                await db.execute(
                    "DELETE FROM app_settings WHERE key='pre_tournament_deadline'"
                )
            else:
                await db.execute(
                    "UPDATE app_settings SET value=? WHERE key='pre_tournament_deadline'",
                    (old_deadline,),
                )
            await db.execute(
                "UPDATE pre_tournament_questions SET correct_answer=? WHERE key='winner'",
                (old_answer,),
            )
            if participant_ids:
                delete_marks = ",".join("?" for _ in participant_ids)
                await db.execute(
                    f"DELETE FROM participants WHERE id IN ({delete_marks})",
                    participant_ids,
                )
            await db.commit()

    run(_clean())


def _detail_html(html, participant_id):
    marker = f'data-participant-id="{participant_id}"'
    start = html.rindex("<details", 0, html.index(marker) + len(marker))
    end = html.index("</details>", start) + len("</details>")
    return html[start:end]


def test_bonus_ranking_explains_every_total_by_phase_and_question(client, participant):
    colleague = _make_participant("Alice Détail Bonus")
    group_question = _seed_question(
        "Feu d'artifice — Combien de buts ?", "group"
    )
    quarter_question = _seed_question(
        "Suspense — Minute du dernier but ?", "quarter"
    )
    future_question = _seed_question(
        "Question encore secrète", "semi", deadline=_FUTURE
    )
    _seed_score(participant["id"], group_question, 4)
    # Une ancienne ligne dupliquée peut subsister avec NULL dans l'index SQLite :
    # le classement et son détail doivent tous deux retenir la correction récente.
    _seed_score(participant["id"], group_question, 2)
    _seed_score(participant["id"], quarter_question, 0)
    _seed_score(participant["id"], future_question, 9)
    _seed_score(colleague["id"], quarter_question, 3)
    old_pt_state = _set_pt_state({participant["id"]: 2, colleague["id"]: 0})

    try:
        async def _data():
            async with get_db() as db:
                rankings = await get_rankings(db, scope="bonus")
                breakdowns = await _load_bonus_ranking_breakdowns(
                    db,
                    [participant["id"], colleague["id"]],
                    "2026-07-12T12:00:00",
                )
                return rankings, breakdowns

        rankings, breakdowns = run(_data())
        totals = {
            row["id"]: row["total_points"]
            for row in rankings
            if row["id"] in {participant["id"], colleague["id"]}
        }
        assert totals == {participant["id"]: 4, colleague["id"]: 3}
        for participant_id, breakdown in breakdowns.items():
            assert breakdown["total_points"] == totals[participant_id]
            assert sum(phase["points"] for phase in breakdown["phases"]) == totals[participant_id]
            assert sum(
                question["points"]
                for phase in breakdown["phases"]
                for question in phase["questions"]
            ) == totals[participant_id]

        response = client.get(f"/p/{participant['token']}/classement?view=bonus")
        html = unescape(response.text)
        mine = _detail_html(html, participant["id"])
        colleague_detail = _detail_html(html, colleague["id"])

        assert response.status_code == 200
        assert 'name="bonus-ranking"' in mine
        assert " open" not in mine[:mine.index(">")]
        assert "Pré-tournoi" in mine and "+2 pts" in mine
        assert "Phase de groupes" in mine and "+2 pts" in mine
        assert "Quarts de finale" in mine
        assert "Champion du Monde" in mine
        assert "Feu d'artifice" in mine and "Combien de buts ?" in mine
        assert "Suspense" in mine and "Minute du dernier but ?" in mine
        assert 'data-bonus-rank-question="bonus:' in mine
        assert "Question encore secrète" not in html
        # Alice n'a aucune ligne sur la question de groupes : elle apparaît
        # quand même avec 0 pt afin d'expliquer complètement son total.
        assert "Feu d'artifice" in colleague_detail
        group_row = colleague_detail[colleague_detail.index("Feu d'artifice"):]
        assert re.search(r"0 pt", group_row)
        assert f'href="/p/{participant["token"]}/profil/{colleague["id"]}?return_view=bonus"' in colleague_detail
        # Le podium est masqué dans ce mode : une seule liste, entièrement dépliable.
        assert 'class="podium"' not in html

        bonus_html = client.get(f"/p/{participant['token']}/bonus").text
        assert "Total bonus : 4 pts" in bonus_html

        general_html = client.get(
            f"/p/{participant['token']}/classement?view=general"
        ).text
        assert "data-bonus-rank-detail" not in general_html
    finally:
        _cleanup(
            [group_question, quarter_question, future_question],
            [colleague["id"]],
            [participant["id"], colleague["id"]],
            old_pt_state,
        )
