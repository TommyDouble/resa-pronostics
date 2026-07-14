import json

import aiosqlite

from app.database import (
    SEMIFINAL_FRANCE_SPAIN_HELP,
    SEMIFINAL_FRANCE_SPAIN_LEGACY_OPTIONS,
    SEMIFINAL_FRANCE_SPAIN_LEGACY_TEXT,
    SEMIFINAL_FRANCE_SPAIN_OPTIONS,
    SEMIFINAL_FRANCE_SPAIN_TEXT,
    SEMIFINAL_HALFTIME_HELP,
    SEMIFINAL_HALFTIME_OPTIONS,
    SEMIFINAL_HALFTIME_TEXT,
    SEMIFINAL_STARS_CONFIG,
    SEMIFINAL_STARS_HELP,
    SEMIFINAL_STARS_NONE_OPTION,
    SEMIFINAL_STARS_OPTIONS,
    SEMIFINAL_STARS_TEXT,
    ensure_semifinal_bonus_questions,
    get_db,
)
from app.scoring import multi_select_bonus_points
from conftest import run


def _semifinal_questions():
    async def _get():
        async with get_db() as db:
            rows = await db.execute(
                """SELECT * FROM bonus_questions
                   WHERE question_text IN (?, ?, ?)
                   ORDER BY id""",
                (
                    SEMIFINAL_STARS_TEXT,
                    SEMIFINAL_HALFTIME_TEXT,
                    SEMIFINAL_FRANCE_SPAIN_TEXT,
                ),
            )
            return [dict(row) for row in await rows.fetchall()]

    return run(_get())


def _ensure_semifinal_questions():
    async def _ensure():
        async with get_db() as db:
            await db.execute(
                """DELETE FROM app_settings
                   WHERE key IN (
                     'bonus_questions_semi_2026_v1',
                     'bonus_questions_semi_2026_v2',
                     'bonus_questions_semi_2026_v3',
                     'bonus_questions_semi_2026_stars_all_or_nothing_v1'
                   )"""
            )
            await ensure_semifinal_bonus_questions(db)
            await db.commit()

    run(_ensure())


async def _create_semifinal_memory_schema(db):
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE matches (
          match_number INTEGER PRIMARY KEY,
          phase TEXT NOT NULL,
          match_date TEXT NOT NULL,
          kickoff_time TEXT NOT NULL
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


def test_semifinal_questions_are_seeded_as_drafts_for_ten_points(client):
    _ensure_semifinal_questions()
    questions = _semifinal_questions()
    assert len(questions) == 3
    assert sum(question["points_value"] for question in questions) == 10
    assert {question["phase"] for question in questions} == {"semi"}
    assert {question["is_published"] for question in questions} == {0}
    assert {question["deadline"] for question in questions} == {
        "2026-07-14T18:59:00"
    }

    by_text = {question["question_text"]: question for question in questions}

    stars = by_text[SEMIFINAL_STARS_TEXT]
    assert stars["answer_type"] == "multi_choice"
    assert stars["scoring_mode"] == "multi_select"
    assert stars["points_value"] == 4
    assert json.loads(stars["options"]) == SEMIFINAL_STARS_OPTIONS
    assert json.loads(stars["scoring_config"]) == SEMIFINAL_STARS_CONFIG
    assert stars["help_text"] == SEMIFINAL_STARS_HELP

    halftime = by_text[SEMIFINAL_HALFTIME_TEXT]
    assert halftime["answer_type"] == "choice"
    assert halftime["scoring_mode"] == "exact"
    assert halftime["points_value"] == 3
    assert json.loads(halftime["options"]) == SEMIFINAL_HALFTIME_OPTIONS
    assert halftime["scoring_config"] is None
    assert halftime["help_text"] == SEMIFINAL_HALFTIME_HELP

    france_spain = by_text[SEMIFINAL_FRANCE_SPAIN_TEXT]
    assert france_spain["answer_type"] == "choice"
    assert france_spain["scoring_mode"] == "exact"
    assert france_spain["points_value"] == 3
    assert json.loads(france_spain["options"]) == SEMIFINAL_FRANCE_SPAIN_OPTIONS
    assert france_spain["scoring_config"] is None
    assert france_spain["help_text"] == SEMIFINAL_FRANCE_SPAIN_HELP


def test_semifinal_star_scoring_awards_partial_credit_and_handles_none():
    answers = [
        {
            "participant_id": 1,
            "answer": json.dumps(["Kylian Mbappé", "Lionel Messi"]),
        },
        {
            "participant_id": 2,
            "answer": json.dumps(["Kylian Mbappé"]),
        },
        {
            "participant_id": 3,
            "answer": json.dumps(["Kylian Mbappé", "Harry Kane"]),
        },
        {
            "participant_id": 4,
            "answer": json.dumps([SEMIFINAL_STARS_NONE_OPTION]),
        },
        {
            "participant_id": 5,
            "answer": json.dumps(
                [SEMIFINAL_STARS_NONE_OPTION, "Kylian Mbappé"]
            ),
        },
    ]

    scores = multi_select_bonus_points(
        4,
        json.dumps(["Kylian Mbappé", "Lionel Messi"]),
        answers,
        SEMIFINAL_STARS_CONFIG,
    )
    assert scores == {1: 4, 2: 3, 3: 2, 4: 0, 5: 0}

    no_scorer_scores = multi_select_bonus_points(
        4,
        json.dumps([SEMIFINAL_STARS_NONE_OPTION]),
        [answers[1], answers[3]],
        SEMIFINAL_STARS_CONFIG,
    )
    assert no_scorer_scores == {2: 3, 4: 4}


def test_semifinal_star_rule_is_migrated_after_the_original_seed():
    async def _migrate_in_memory():
        async with aiosqlite.connect(":memory:") as db:
            await _create_semifinal_memory_schema(db)
            await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    correct_answer, scoring_mode, scoring_config, help_text,
                    is_published, deadline)
                   VALUES (?, 'semi', 'multi_choice', ?, 4, NULL,
                           'multi_select', ?, 'Ancien tooltip', 1,
                           '2026-07-14T18:59:00')""",
                (
                    SEMIFINAL_STARS_TEXT,
                    json.dumps(SEMIFINAL_STARS_OPTIONS, ensure_ascii=False),
                    json.dumps({"error_step": 1}),
                ),
            )
            await db.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, 'done')",
                ("bonus_questions_semi_2026_v3",),
            )

            await ensure_semifinal_bonus_questions(db)

            question = await (await db.execute(
                """SELECT scoring_config, help_text, is_published
                   FROM bonus_questions
                   WHERE question_text=?""",
                (SEMIFINAL_STARS_TEXT,),
            )).fetchone()
            help_marker = await (await db.execute(
                """SELECT value FROM app_settings
                   WHERE key='bonus_questions_semi_2026_stars_help_v2'"""
            )).fetchone()
            scoring_marker = await (await db.execute(
                """SELECT value FROM app_settings
                   WHERE key=
                     'bonus_questions_semi_2026_stars_all_or_nothing_v1'"""
            )).fetchone()
            return dict(question), help_marker, scoring_marker

    question, help_marker, scoring_marker = run(_migrate_in_memory())
    assert json.loads(question["scoring_config"]) == SEMIFINAL_STARS_CONFIG
    assert question["help_text"] == SEMIFINAL_STARS_HELP
    assert question["is_published"] == 1
    assert help_marker is not None
    assert scoring_marker is not None


def test_semifinal_none_option_is_exclusive_in_form_and_server(
    client,
    admin_client,
    participant,
):
    _ensure_semifinal_questions()
    stars = next(
        question
        for question in _semifinal_questions()
        if question["question_text"] == SEMIFINAL_STARS_TEXT
    )

    response = admin_client.post(
        f"/admin/bonus/{stars['id']}/update",
        data={
            "question_text": SEMIFINAL_STARS_TEXT,
            "phase": "semi",
            "answer_type": "multi_choice",
            "points_value": "4",
            "deadline": "2030-07-14T19:00",
            "options_text": "\n".join(SEMIFINAL_STARS_OPTIONS),
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    stars = next(
        question
        for question in _semifinal_questions()
        if question["question_text"] == SEMIFINAL_STARS_TEXT
    )
    assert json.loads(stars["scoring_config"]) == SEMIFINAL_STARS_CONFIG

    page = client.get(f"/p/{participant['token']}/bonus")
    assert page.status_code == 200
    option_position = page.text.index(
        f'value="{SEMIFINAL_STARS_NONE_OPTION}"'
    )
    option_input = page.text[option_position:page.text.index(">", option_position)]
    assert 'data-all-or-nothing-answer="1"' in option_input

    response = client.post(
        f"/p/{participant['token']}/bonus/{stars['id']}",
        data={
            "answer": [SEMIFINAL_STARS_NONE_OPTION, "Kylian Mbappé"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "doit être cochée seule" in response.text

    response = client.post(
        f"/p/{participant['token']}/bonus/{stars['id']}",
        data={"answer": SEMIFINAL_STARS_NONE_OPTION},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_semifinal_wording_migration_preserves_existing_answers():
    async def _migrate_in_memory():
        async with aiosqlite.connect(":memory:") as db:
            await _create_semifinal_memory_schema(db)
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    correct_answer, scoring_mode, scoring_config, help_text,
                    is_published, deadline)
                   VALUES (?, 'semi', 'choice', ?, 6, ?, 'exact', NULL,
                           'Ancienne aide', 1, '2026-07-14T18:59:00')""",
                (
                    SEMIFINAL_FRANCE_SPAIN_LEGACY_TEXT,
                    json.dumps(
                        SEMIFINAL_FRANCE_SPAIN_LEGACY_OPTIONS,
                        ensure_ascii=False,
                    ),
                    SEMIFINAL_FRANCE_SPAIN_LEGACY_OPTIONS[1],
                ),
            )
            question_id = cursor.lastrowid
            await db.executemany(
                "INSERT INTO bonus_answers (question_id, answer) VALUES (?, ?)",
                [
                    (question_id, SEMIFINAL_FRANCE_SPAIN_LEGACY_OPTIONS[0]),
                    (question_id, SEMIFINAL_FRANCE_SPAIN_LEGACY_OPTIONS[3]),
                ],
            )
            await db.execute(
                """INSERT INTO app_settings (key, value)
                   VALUES ('bonus_questions_semi_2026_v1', datetime('now'))"""
            )

            await ensure_semifinal_bonus_questions(db)

            question = await (await db.execute(
                "SELECT * FROM bonus_questions WHERE id=?", (question_id,)
            )).fetchone()
            answers = await (await db.execute(
                "SELECT answer FROM bonus_answers WHERE question_id=? ORDER BY rowid",
                (question_id,),
            )).fetchall()
            count = await (await db.execute(
                "SELECT COUNT(*) AS total FROM bonus_questions"
            )).fetchone()
            return dict(question), [row["answer"] for row in answers], count["total"]

    question, answers, question_count = run(_migrate_in_memory())
    assert question_count == 3
    assert question["question_text"] == SEMIFINAL_FRANCE_SPAIN_TEXT
    assert json.loads(question["options"]) == SEMIFINAL_FRANCE_SPAIN_OPTIONS
    assert question["help_text"] == SEMIFINAL_FRANCE_SPAIN_HELP
    assert question["points_value"] == 3
    assert question["is_published"] == 0
    assert question["correct_answer"] == SEMIFINAL_FRANCE_SPAIN_OPTIONS[1]
    assert answers == [
        SEMIFINAL_FRANCE_SPAIN_OPTIONS[0],
        SEMIFINAL_FRANCE_SPAIN_OPTIONS[3],
    ]


def test_existing_published_semifinal_questions_become_drafts_without_losing_answers():
    async def _migrate_in_memory():
        async with aiosqlite.connect(":memory:") as db:
            await _create_semifinal_memory_schema(db)
            await ensure_semifinal_bonus_questions(db)
            await db.execute(
                """UPDATE bonus_questions
                   SET is_published=1,
                       points_value=CASE
                         WHEN question_text=? THEN 8
                         ELSE 6
                       END,
                       scoring_config=CASE
                         WHEN question_text=? THEN '{"error_step":2}'
                         ELSE NULL
                       END""",
                (SEMIFINAL_STARS_TEXT, SEMIFINAL_STARS_TEXT),
            )
            rows = await db.execute(
                "SELECT id, question_text FROM bonus_questions ORDER BY id"
            )
            questions = await rows.fetchall()
            for question in questions:
                answer = (
                    json.dumps(["Kylian Mbappé"])
                    if question["question_text"] == SEMIFINAL_STARS_TEXT
                    else "Réponse conservée"
                )
                await db.execute(
                    "INSERT INTO bonus_answers (question_id, answer) VALUES (?, ?)",
                    (question["id"], answer),
                )
            await db.execute(
                "DELETE FROM app_settings WHERE key='bonus_questions_semi_2026_v3'"
            )
            await db.execute(
                """INSERT OR IGNORE INTO app_settings (key, value)
                   VALUES ('bonus_questions_semi_2026_v2', datetime('now'))"""
            )

            await ensure_semifinal_bonus_questions(db)

            rows = await db.execute(
                """SELECT question_text, points_value, scoring_config, is_published
                   FROM bonus_questions ORDER BY id"""
            )
            migrated = [dict(row) for row in await rows.fetchall()]
            answer_count = await (await db.execute(
                "SELECT COUNT(*) AS total FROM bonus_answers"
            )).fetchone()
            return migrated, answer_count["total"]

    questions, answer_count = run(_migrate_in_memory())
    assert answer_count == 3
    assert {question["is_published"] for question in questions} == {0}
    by_text = {question["question_text"]: question for question in questions}
    assert by_text[SEMIFINAL_STARS_TEXT]["points_value"] == 4
    assert (
        json.loads(by_text[SEMIFINAL_STARS_TEXT]["scoring_config"])
        == SEMIFINAL_STARS_CONFIG
    )
    assert by_text[SEMIFINAL_HALFTIME_TEXT]["points_value"] == 3
    assert by_text[SEMIFINAL_FRANCE_SPAIN_TEXT]["points_value"] == 3


def test_semifinal_seed_uses_first_match_deadline_and_is_idempotent():
    async def _seed_in_memory():
        async with aiosqlite.connect(":memory:") as db:
            await _create_semifinal_memory_schema(db)
            await db.executescript(
                """
                INSERT INTO matches
                  (match_number, phase, match_date, kickoff_time)
                VALUES
                  (102, 'semi', '2030-07-15', '19:00'),
                  (101, 'semi', '2030-07-14', '20:30');
                """
            )
            await ensure_semifinal_bonus_questions(db)
            await ensure_semifinal_bonus_questions(db)
            rows = await db.execute(
                "SELECT deadline, is_published FROM bonus_questions ORDER BY id"
            )
            questions = [dict(row) for row in await rows.fetchall()]
            marker = await (await db.execute(
                "SELECT value FROM app_settings WHERE key='bonus_questions_semi_2026_v3'"
            )).fetchone()
            return questions, marker

    questions, marker = run(_seed_in_memory())
    assert len(questions) == 3
    assert {question["deadline"] for question in questions} == {
        "2030-07-14T20:29:00"
    }
    assert {question["is_published"] for question in questions} == {0}
    assert marker is not None
