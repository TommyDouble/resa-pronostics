import json
import uuid

import aiosqlite

from app.database import (
    FINAL_WEEKEND_ARGENTINA_GOAL_HELP,
    FINAL_WEEKEND_ARGENTINA_GOAL_OPTIONS,
    FINAL_WEEKEND_ARGENTINA_GOAL_TEXT,
    FINAL_WEEKEND_CARDED_PLAYERS_HELP,
    FINAL_WEEKEND_CARDED_PLAYERS_TEXT,
    FINAL_WEEKEND_CARDS_CONFIG,
    FINAL_WEEKEND_FINAL_SCORERS_CONFIG,
    FINAL_WEEKEND_FINAL_SCORERS_HELP,
    FINAL_WEEKEND_FINAL_SCORERS_TEXT,
    FINAL_WEEKEND_FIRST_SCORER_HELP,
    FINAL_WEEKEND_FIRST_SCORER_OPTIONS,
    FINAL_WEEKEND_FIRST_SCORER_TEXT,
    FINAL_WEEKEND_HALFTIME_HELP,
    FINAL_WEEKEND_HALFTIME_OPTIONS,
    FINAL_WEEKEND_HALFTIME_TEXT,
    ensure_final_weekend_bonus_questions,
    get_db,
)
from app.scoring import closest_podium_bonus_points
from app.timeutils import format_local_datetime
from conftest import run


FINAL_TEXTS = (
    FINAL_WEEKEND_FIRST_SCORER_TEXT,
    FINAL_WEEKEND_HALFTIME_TEXT,
    FINAL_WEEKEND_CARDED_PLAYERS_TEXT,
    FINAL_WEEKEND_ARGENTINA_GOAL_TEXT,
    FINAL_WEEKEND_FINAL_SCORERS_TEXT,
)


async def _create_seed_schema(db):
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE bonus_questions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          question_text TEXT NOT NULL,
          phase TEXT NOT NULL,
          answer_type TEXT NOT NULL,
          options TEXT,
          points_value INTEGER NOT NULL,
          correct_answer TEXT,
          scoring_mode TEXT NOT NULL,
          scoring_config TEXT,
          help_text TEXT,
          is_published INTEGER NOT NULL,
          deadline TEXT NOT NULL
        );
        CREATE TABLE bonus_answers (
          question_id INTEGER NOT NULL,
          answer TEXT NOT NULL
        );
        """
    )


def test_final_weekend_seed_creates_five_drafts_for_fifteen_points():
    async def _seed():
        async with aiosqlite.connect(":memory:") as db:
            await _create_seed_schema(db)
            await ensure_final_weekend_bonus_questions(db)
            # L'early return doit lui aussi être idempotent.
            await ensure_final_weekend_bonus_questions(db)
            rows = await db.execute(
                "SELECT * FROM bonus_questions ORDER BY id"
            )
            questions = [dict(row) for row in await rows.fetchall()]
            marker = await (await db.execute(
                """SELECT value FROM app_settings
                   WHERE key='bonus_questions_final_weekend_2026_v1'"""
            )).fetchone()
            return questions, marker

    questions, marker = run(_seed())
    assert marker is not None
    assert [question["question_text"] for question in questions] == list(FINAL_TEXTS)
    assert len(questions) == 5
    assert sum(question["points_value"] for question in questions) == 15
    assert {question["is_published"] for question in questions} == {0}
    assert {question["correct_answer"] for question in questions} == {None}

    by_text = {question["question_text"]: question for question in questions}
    first = by_text[FINAL_WEEKEND_FIRST_SCORER_TEXT]
    assert first["phase"] == "third_place"
    assert first["answer_type"] == "choice"
    assert first["scoring_mode"] == "exact"
    assert first["deadline"] == "2026-07-18T20:59:00"
    assert json.loads(first["options"]) == FINAL_WEEKEND_FIRST_SCORER_OPTIONS
    assert first["help_text"] == FINAL_WEEKEND_FIRST_SCORER_HELP

    halftime = by_text[FINAL_WEEKEND_HALFTIME_TEXT]
    assert halftime["phase"] == "third_place"
    assert halftime["answer_type"] == "choice"
    assert halftime["scoring_mode"] == "exact"
    assert halftime["deadline"] == "2026-07-18T20:59:00"
    assert json.loads(halftime["options"]) == FINAL_WEEKEND_HALFTIME_OPTIONS
    assert halftime["help_text"] == FINAL_WEEKEND_HALFTIME_HELP

    cards = by_text[FINAL_WEEKEND_CARDED_PLAYERS_TEXT]
    assert cards["phase"] == "final"
    assert cards["answer_type"] == "number"
    assert cards["scoring_mode"] == "closest_podium"
    assert cards["deadline"] == "2026-07-18T20:59:00"
    assert json.loads(cards["scoring_config"]) == FINAL_WEEKEND_CARDS_CONFIG
    assert cards["help_text"] == FINAL_WEEKEND_CARDED_PLAYERS_HELP

    argentina = by_text[FINAL_WEEKEND_ARGENTINA_GOAL_TEXT]
    assert argentina["phase"] == "final"
    assert argentina["answer_type"] == "choice"
    assert argentina["scoring_mode"] == "exact"
    assert argentina["deadline"] == "2026-07-19T18:59:00"
    assert json.loads(argentina["options"]) == FINAL_WEEKEND_ARGENTINA_GOAL_OPTIONS
    assert argentina["help_text"] == FINAL_WEEKEND_ARGENTINA_GOAL_HELP

    scorers = by_text[FINAL_WEEKEND_FINAL_SCORERS_TEXT]
    assert scorers["phase"] == "final"
    assert scorers["answer_type"] == "number"
    assert scorers["scoring_mode"] == "exact"
    assert scorers["deadline"] == "2026-07-19T18:59:00"
    assert json.loads(scorers["scoring_config"]) == FINAL_WEEKEND_FINAL_SCORERS_CONFIG
    assert scorers["help_text"] == FINAL_WEEKEND_FINAL_SCORERS_HELP


def test_final_weekend_seed_refreshes_unplayed_only_and_never_duplicates():
    async def _seed_again():
        async with aiosqlite.connect(":memory:") as db:
            await _create_seed_schema(db)
            await ensure_final_weekend_bonus_questions(db)
            rows = await db.execute(
                "SELECT id, question_text FROM bonus_questions ORDER BY id"
            )
            ids = {row["question_text"]: row["id"] for row in await rows.fetchall()}
            protected_id = ids[FINAL_WEEKEND_FIRST_SCORER_TEXT]
            refreshed_id = ids[FINAL_WEEKEND_HALFTIME_TEXT]
            await db.execute(
                """UPDATE bonus_questions
                   SET phase='group', answer_type='number', options=NULL,
                       points_value=49, correct_answer='sentinelle',
                       scoring_mode='closest_podium', scoring_config='{}',
                       help_text='Ne pas toucher', is_published=1,
                       deadline='2030-01-01T00:00:00'
                   WHERE id=?""",
                (protected_id,),
            )
            await db.execute(
                "INSERT INTO bonus_answers (question_id, answer) VALUES (?, 'joué')",
                (protected_id,),
            )
            await db.execute(
                """UPDATE bonus_questions
                   SET points_value=48, correct_answer='à effacer',
                       help_text='À rafraîchir', is_published=1
                   WHERE id=?""",
                (refreshed_id,),
            )
            await db.execute(
                """DELETE FROM app_settings
                   WHERE key='bonus_questions_final_weekend_2026_v1'"""
            )

            await ensure_final_weekend_bonus_questions(db)
            rows = await db.execute(
                "SELECT * FROM bonus_questions ORDER BY id"
            )
            return [dict(row) for row in await rows.fetchall()]

    questions = run(_seed_again())
    assert len(questions) == 5
    by_text = {question["question_text"]: question for question in questions}

    protected = by_text[FINAL_WEEKEND_FIRST_SCORER_TEXT]
    assert protected["phase"] == "group"
    assert protected["answer_type"] == "number"
    assert protected["points_value"] == 49
    assert protected["correct_answer"] == "sentinelle"
    assert protected["help_text"] == "Ne pas toucher"
    assert protected["is_published"] == 1
    assert protected["deadline"] == "2030-01-01T00:00:00"

    refreshed = by_text[FINAL_WEEKEND_HALFTIME_TEXT]
    assert refreshed["phase"] == "third_place"
    assert refreshed["answer_type"] == "choice"
    assert refreshed["points_value"] == 3
    assert refreshed["correct_answer"] is None
    assert refreshed["help_text"] == FINAL_WEEKEND_HALFTIME_HELP
    assert refreshed["is_published"] == 0


def test_carded_players_use_dense_three_two_one_podium_with_ties():
    answers = [
        {"participant_id": 1, "answer": "8"},
        {"participant_id": 2, "answer": "8.0"},
        {"participant_id": 3, "answer": "7"},
        {"participant_id": 4, "answer": "9"},
        {"participant_id": 5, "answer": "10"},
        {"participant_id": 6, "answer": "12"},
    ]

    assert closest_podium_bonus_points(
        3,
        "8",
        answers,
        FINAL_WEEKEND_CARDS_CONFIG,
    ) == {1: 3, 2: 3, 3: 2, 4: 2, 5: 1, 6: 0}


def _question_by_text(question_text):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_questions WHERE question_text=?",
                (question_text,),
            )
            question = await row.fetchone()
            return dict(question) if question else None

    return run(_get())


def _delete_questions(question_ids):
    async def _delete():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in question_ids)
            await db.execute(
                f"DELETE FROM scores WHERE bonus_question_id IN ({placeholders})",
                tuple(question_ids),
            )
            await db.execute(
                f"DELETE FROM bonus_answers WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            )
            await db.execute(
                f"DELETE FROM bonus_questions WHERE id IN ({placeholders})",
                tuple(question_ids),
            )
            await db.commit()

    if question_ids:
        run(_delete())


def _create_participants(count):
    async def _create():
        participants = []
        async with get_db() as db:
            for index in range(count):
                token = str(uuid.uuid4())
                cursor = await db.execute(
                    """INSERT INTO participants
                       (name, first_name, last_name, email, token, is_confirmed)
                       VALUES (?, ?, 'Final', ?, ?, 1)""",
                    (
                        f"Final Test {index}",
                        f"Final{index}",
                        f"{token}@test.local",
                        token,
                    ),
                )
                participants.append({"id": cursor.lastrowid, "token": token})
            await db.commit()
        return participants

    return run(_create())


def _delete_participants(participant_ids):
    async def _delete():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in participant_ids)
            await db.execute(
                f"DELETE FROM participants WHERE id IN ({placeholders})",
                tuple(participant_ids),
            )
            await db.commit()

    if participant_ids:
        run(_delete())


def _create_number_question(admin_client, question_text, mode, **overrides):
    data = {
        "question_text": question_text,
        "phase": "final",
        "answer_type": "number",
        "points_value": "3",
        "deadline": "2030-07-19T20:59",
        "number_scoring_mode": mode,
        "number_config_present": "1",
        "number_min_value": "0",
        "number_max_value": "30",
        "number_integer_only": "1",
        "closest_preset_key": "custom",
        "closest_award_mode": "podium_custom",
        "closest_tie_policy": "full_dense",
        "closest_rank1_points": "3",
        "closest_rank2_points": "2",
        "closest_rank3_points": "1",
    }
    data.update(overrides)
    response = admin_client.post(
        "/admin/bonus/create",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _question_by_text(question_text)


def test_admin_creates_edits_and_locks_generic_number_modes(admin_client, participant):
    suffix = uuid.uuid4().hex
    exact_text = f"Nombre exact générique {suffix}"
    closest_text = f"Nombre au plus proche générique {suffix}"
    default_text = f"Nombre historique sans mode {suffix}"
    question_ids = []
    try:
        exact = _create_number_question(admin_client, exact_text, "exact")
        closest = _create_number_question(
            admin_client,
            closest_text,
            "closest_podium",
            number_max_value="40",
        )
        default = _create_number_question(admin_client, default_text, "")
        question_ids.extend([exact["id"], closest["id"], default["id"]])

        assert exact["scoring_mode"] == "exact"
        assert exact["points_value"] == 3
        assert json.loads(exact["scoring_config"]) == {
            "min_value": 0,
            "max_value": 30,
            "integer_only": True,
        }
        assert default["scoring_mode"] == "closest_podium"

        closest_config = json.loads(closest["scoring_config"])
        assert closest["scoring_mode"] == "closest_podium"
        assert closest["points_value"] == 3
        assert closest_config["rank_points"] == [3, 2, 1]
        assert closest_config["tie_policy"] == "full_dense"
        assert closest_config["min_value"] == 0
        assert closest_config["max_value"] == 40
        assert closest_config["integer_only"] is True

        # Édition explicite du mode exact et de ses bornes.
        response = admin_client.post(
            f"/admin/bonus/{exact['id']}/update",
            data={
                "question_text": exact_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "4",
                "deadline": "2030-07-19T20:59",
                "number_scoring_mode": "exact",
                "number_config_present": "1",
                "number_min_value": "1",
                "number_max_value": "31",
                "number_integer_only": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        exact = _question_by_text(exact_text)
        assert exact["scoring_mode"] == "exact"
        assert exact["points_value"] == 4
        assert json.loads(exact["scoring_config"]) == {
            "min_value": 1,
            "max_value": 31,
            "integer_only": True,
        }

        # Sans champ de mode, une édition conserve le comportement existant.
        response = admin_client.post(
            f"/admin/bonus/{exact['id']}/update",
            data={
                "question_text": exact_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "5",
                "deadline": "2030-07-19T20:59",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        exact = _question_by_text(exact_text)
        assert exact["scoring_mode"] == "exact"
        assert exact["points_value"] == 5

        # Édition du podium au plus proche.
        response = admin_client.post(
            f"/admin/bonus/{closest['id']}/update",
            data={
                "question_text": closest_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "3",
                "deadline": "2030-07-19T20:59",
                "number_scoring_mode": "closest_podium",
                "number_config_present": "1",
                "number_min_value": "0",
                "number_max_value": "40",
                "number_integer_only": "1",
                "closest_preset_key": "custom",
                "closest_award_mode": "podium_custom",
                "closest_tie_policy": "full_dense",
                "closest_rank1_points": "4",
                "closest_rank2_points": "2",
                "closest_rank3_points": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        closest = _question_by_text(closest_text)
        assert closest["points_value"] == 4
        assert json.loads(closest["scoring_config"])["rank_points"] == [4, 2, 1]

        # Publication : un POST forgé ne peut plus changer le mode ni les bornes.
        async def _publish_exact():
            async with get_db() as db:
                await db.execute(
                    "UPDATE bonus_questions SET is_published=1 WHERE id=?",
                    (exact["id"],),
                )
                await db.commit()

        run(_publish_exact())
        locked_config = exact["scoring_config"]
        response = admin_client.post(
            f"/admin/bonus/{exact['id']}/update",
            data={
                "question_text": exact_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "5",
                "deadline": "2030-07-19T20:59",
                "is_published": "1",
                "number_scoring_mode": "closest_podium",
                "number_config_present": "1",
                "number_min_value": "-100",
                "number_max_value": "100",
                "closest_preset_key": "custom",
                "closest_rank1_points": "9",
                "closest_rank2_points": "8",
                "closest_rank3_points": "7",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        exact = _question_by_text(exact_text)
        assert exact["scoring_mode"] == "exact"
        assert exact["scoring_config"] == locked_config

        # Une réponse verrouille le mode de la même manière, même en brouillon.
        async def _answer_exact():
            async with get_db() as db:
                await db.execute(
                    "UPDATE bonus_questions SET is_published=0 WHERE id=?",
                    (exact["id"],),
                )
                await db.execute(
                    """INSERT INTO bonus_answers
                       (participant_id, question_id, answer)
                       VALUES (?, ?, '3')""",
                    (participant["id"], exact["id"]),
                )
                await db.commit()

        run(_answer_exact())
        response = admin_client.post(
            f"/admin/bonus/{exact['id']}/update",
            data={
                "question_text": exact_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "5",
                "deadline": "2030-07-19T20:59",
                "number_scoring_mode": "closest_podium",
                "closest_preset_key": "custom",
                "closest_rank1_points": "9",
                "closest_rank2_points": "8",
                "closest_rank3_points": "7",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _question_by_text(exact_text)["scoring_mode"] == "exact"

        # La notation minute ne peut jamais basculer en exact.
        async def _enable_minute_notation():
            async with get_db() as db:
                config = json.loads(closest["scoring_config"])
                config["minute_notation"] = True
                await db.execute(
                    "UPDATE bonus_questions SET scoring_config=? WHERE id=?",
                    (json.dumps(config), closest["id"]),
                )
                await db.commit()

        run(_enable_minute_notation())
        response = admin_client.post(
            f"/admin/bonus/{closest['id']}/update",
            data={
                "question_text": closest_text,
                "phase": "final",
                "answer_type": "number",
                "points_value": "9",
                "deadline": "2030-07-19T20:59",
                "number_scoring_mode": "exact",
                "closest_preset_key": "custom",
                "closest_award_mode": "podium_custom",
                "closest_tie_policy": "full_dense",
                "closest_rank1_points": "4",
                "closest_rank2_points": "2",
                "closest_rank3_points": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _question_by_text(closest_text)["scoring_mode"] == "closest_podium"
    finally:
        _delete_questions(question_ids)


def test_exact_number_bounds_apply_to_participant_admin_and_scoring(client, admin_client):
    question_text = f"Buteurs exacts intégration {uuid.uuid4().hex}"
    participants = _create_participants(4)
    question = None
    try:
        question = _create_number_question(
            admin_client,
            question_text,
            "exact",
            is_published="1",
        )
        page = client.get(f"/p/{participants[0]['token']}/bonus")
        assert page.status_code == 200
        question_pos = page.text.index(question_text)
        question_fragment = page.text[question_pos:question_pos + 7000]
        assert 'name="answer"' in question_fragment
        assert 'min="0"' in question_fragment
        assert 'max="30"' in question_fragment
        assert 'step="1"' in question_fragment

        answer_url = f"/p/{participants[0]['token']}/bonus/{question['id']}"
        assert client.post(
            answer_url, data={"answer": "-1"}, follow_redirects=False
        ).status_code == 400
        assert client.post(
            answer_url, data={"answer": "30.5"}, follow_redirects=False
        ).status_code == 400
        assert client.post(
            answer_url, data={"answer": "31"}, follow_redirects=False
        ).status_code == 400

        answers = ("3", "3.0", "03", "4")
        for participant_data, answer in zip(participants, answers):
            response = client.post(
                f"/p/{participant_data['token']}/bonus/{question['id']}",
                data={"answer": answer},
                follow_redirects=False,
            )
            assert response.status_code == 303

        # L'endpoint de correction renvoie une redirection aussi en erreur :
        # on contrôle donc la valeur réellement persistée.
        for invalid in ("3.5", "31", "NaN", "Infinity"):
            response = admin_client.post(
                f"/admin/bonus/{question['id']}/answer",
                data={"correct_answer": invalid},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert _question_by_text(question_text)["correct_answer"] is None

        response = admin_client.post(
            f"/admin/bonus/{question['id']}/answer",
            data={"correct_answer": "3.0"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        async def _scores():
            async with get_db() as db:
                rows = await db.execute(
                    """SELECT participant_id, points FROM scores
                       WHERE bonus_question_id=?""",
                    (question["id"],),
                )
                return {row["participant_id"]: row["points"] for row in await rows.fetchall()}

        scores = run(_scores())
        assert [scores[participant["id"]] for participant in participants] == [3, 3, 3, 0]
    finally:
        if question:
            _delete_questions([question["id"]])
        _delete_participants([participant["id"] for participant in participants])


def test_final_weekend_deadlines_display_in_brussels_time(admin_client):
    assert format_local_datetime("2026-07-18T20:59:00") == "18/07/2026 22:59"
    assert format_local_datetime("2026-07-19T18:59:00") == "19/07/2026 20:59"

    # La base de tests est partagée par toute la session et certains tests CRUD
    # suppriment leurs questions : réappliquer explicitement le seed avant de
    # contrôler son rendu admin.
    async def _restore_seed():
        async with get_db() as db:
            await db.execute(
                """DELETE FROM app_settings
                   WHERE key='bonus_questions_final_weekend_2026_v1'"""
            )
            await ensure_final_weekend_bonus_questions(db)
            await db.commit()

    run(_restore_seed())
    html = admin_client.get("/admin/bonus").text
    assert FINAL_WEEKEND_FIRST_SCORER_TEXT in html
    assert FINAL_WEEKEND_FINAL_SCORERS_TEXT in html
    assert "18/07/2026 22:59" in html
    assert "19/07/2026 20:59" in html
