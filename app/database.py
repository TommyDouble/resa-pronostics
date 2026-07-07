import aiosqlite
import json
import os
from contextlib import asynccontextmanager
from app.config import settings
from app.nameutils import build_full_name, split_full_name
from app.pre_tournament import ensure_pre_tournament_defaults
from app.settings_store import ensure_default_settings

DB_PATH = settings.DATABASE_URL.replace("./", "")

ROUND32_TIEBREAK_COUNT_TEXT = (
    "Encore des tirs au but ? — Combien de nouvelles séances de tirs au but "
    "auront lieu sur les matchs restants des seizièmes ?"
)
ROUND32_FAVORITE_CONCEDE_TEXT = (
    "Le Favori Qui Tremble — Parmi France, Angleterre, Belgique, Espagne, "
    "Portugal et Argentine, combien encaisseront le premier but de leur match ?"
)
ROUND32_TIEBREAK_EXACT_CONFIG = {"min_value": 0, "max_value": 13}
ROUND32_FAVORITE_EXACT_CONFIG = {"min_value": 0, "max_value": 6}
ROUND32_EXACT_POINTS = 5
ROUND32_TIEBREAK_HELP = (
    "Une séance compte uniquement si la FIFA publie un score de tirs au but. "
    "Les séances déjà jouées (Allemagne-Paraguay, Pays-Bas-Maroc) ne comptent pas. "
    "Barème : les points de la question si le nombre exact est trouvé, 0 sinon."
)
ROUND32_FAVORITE_HELP = (
    "Un favori compte si le 1er but officiel du match est inscrit en faveur de "
    "l'adversaire — but contre son camp inclus (on regarde le score, pas le "
    "buteur). 0-0 jusqu'aux tirs au but = personne. But en prolongation compté "
    "s'il est le premier. But annulé par la VAR non compté. Penalty en cours de "
    "jeu compté, tirs au but non. Match abandonné/rejoué sans 1er but officiel : "
    "le favori ne compte pas. Barème : les points de la question si le nombre exact "
    "est trouvé, 0 sinon."
)

ROUND16_HOST_COUNTRIES_TEXT = (
    "À domicile, ça pousse — Combien de pays hôtes seront en quarts de finale, "
    "et lesquels ?"
)
ROUND16_RESCAPED_TIEBREAK_TEXT = (
    "Les rescapés ont-ils encore du jus ? — Combien d'équipes qualifiées aux "
    "tirs au but en seizièmes iront en quarts, et lesquelles ?"
)
ROUND16_FASTEST_GOAL_TEXT = (
    "Départ canon — À quelle minute sera inscrit le but le plus rapide des "
    "huitièmes de finale ?"
)
ROUND16_NUMBER_MULTI_CONFIG = {
    "locked_teams": [],
    "min_count": 0,
    "max_count": 3,
    "part1_points": 4,
    "team_step": 2,
    "max_points": 10,
}
ROUND16_FASTEST_GOAL_CONFIG = {
    "preset_key": "custom",
    "award_mode": "podium_custom",
    "tie_policy": "full_dense",
    "rank_points": [5, 3, 1],
    "min_value": 1,
    "max_value": 120,
    "integer_only": True,
    "max_points": 5,
}
ROUND16_HOST_COUNTRIES_HELP = (
    "Les trois pays hôtes encore en lice sont Canada, Mexique et États-Unis. "
    "Coche ceux que tu vois en quarts, puis indique leur nombre total. "
    "On compte les pays hôtes qualifiés pour les quarts après leur huitième, "
    "prolongation et tirs au but inclus. Jusqu'à 10 points : 4 points si le "
    "total est exact, puis +2 / -2 par pays sélectionné. Le bonus détail ne "
    "peut jamais descendre sous 0."
)
ROUND16_RESCAPED_TIEBREAK_HELP = (
    "Paraguay, Maroc et Égypte sont passés aux tirs au but en seizièmes. "
    "Combien d'entre eux referont l'exploit en huitièmes ? On compte uniquement "
    "ces équipes si elles gagnent ensuite leur huitième, prolongation et tirs "
    "au but inclus pour déterminer l'équipe qualifiée. Jusqu'à 10 points : "
    "4 points si le total est exact, puis +2 / -2 par équipe sélectionnée. "
    "Le bonus détail ne peut jamais descendre sous 0."
)
ROUND16_FASTEST_GOAL_HELP = (
    "Indique la minute officielle du but le plus rapide. Un but noté 90+5 "
    "compte comme 90. Un but noté 120+3 compte comme 120. Le temps réglementaire "
    "et la prolongation sont inclus, les tirs au but sont exclus. On utilise la "
    "minute officielle affichée par la source de référence, sans recalcul maison. "
    "Si aucun but n'est inscrit sur l'ensemble des huitièmes, la question est "
    "annulée. Barème : 5 points pour le ou les plus proches, 3 points pour le "
    "deuxième écart distinct, 1 point pour le troisième écart distinct."
)


async def _normalize_existing_participant_names(db):
    rows = await db.execute("SELECT id, name, first_name, last_name FROM participants")
    participants = await rows.fetchall()
    for p in participants:
        if p["first_name"] and p["last_name"]:
            first_name, last_name, name = build_full_name(
                p["first_name"], p["last_name"], p["name"] or ""
            )
        else:
            first_name, last_name = split_full_name(p["name"] or "")
            name = f"{first_name} {last_name}".strip()
        if not name:
            continue
        if name != p["name"] or first_name != p["first_name"] or last_name != p["last_name"]:
            await db.execute(
                "UPDATE participants SET name=?, first_name=?, last_name=? WHERE id=?",
                (name, first_name, last_name, p["id"]),
            )


@asynccontextmanager
async def get_db():
    """Async context manager yielding a configured db connection."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def init_db():
    """Initialize database schema."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")

        await db.executescript("""
CREATE TABLE IF NOT EXISTS participants (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  first_name TEXT,
  last_name  TEXT,
  nickname   TEXT,
  email      TEXT    NOT NULL UNIQUE,
  token      TEXT    NOT NULL UNIQUE,
  is_admin   INTEGER NOT NULL DEFAULT 0,
  is_confirmed INTEGER NOT NULL DEFAULT 0,
  has_paid   INTEGER NOT NULL DEFAULT 0,
  favorite_team TEXT,
  bio        TEXT,
  profile_visibility TEXT NOT NULL DEFAULT 'public' CHECK(profile_visibility IN ('public','limited')),
  email_opt_in INTEGER NOT NULL DEFAULT 1,
  password_hash TEXT,
  department TEXT,
  is_favorite INTEGER NOT NULL DEFAULT 0,
  created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  match_number INTEGER NOT NULL UNIQUE,
  phase        TEXT    NOT NULL CHECK(phase IN ('group','round_of_32','round_of_16','quarter','semi','third_place','final')),
  group_name   TEXT,
  match_date   TEXT    NOT NULL,
  kickoff_time TEXT    NOT NULL,
  team1_name   TEXT    NOT NULL,
  team2_name   TEXT    NOT NULL,
  is_top_match INTEGER NOT NULL DEFAULT 0,
  weight       INTEGER NOT NULL DEFAULT 1 CHECK(weight IN (1,2)),
  score_team1  INTEGER,
  score_team2  INTEGER,
  final_score_team1 INTEGER,
  final_score_team2 INTEGER,
  result       TEXT    CHECK(result IN ('team1','draw','team2') OR result IS NULL),
  qualifier_winner TEXT CHECK(qualifier_winner IN ('team1','team2') OR qualifier_winner IS NULL),
  predictions_open INTEGER NOT NULL DEFAULT 0 CHECK(predictions_open IN (0,1)),
  created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS predictions (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id      INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  prediction          TEXT    NOT NULL CHECK(prediction IN ('team1','draw','team2')),
  exact_score_team1   INTEGER,
  exact_score_team2   INTEGER,
  qualifier_prediction TEXT   CHECK(qualifier_prediction IN ('team1','team2') OR qualifier_prediction IS NULL),
  submitted_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  is_locked           INTEGER NOT NULL DEFAULT 0,
  admin_entered       INTEGER NOT NULL DEFAULT 0,
  UNIQUE(participant_id, match_id)
);

CREATE TABLE IF NOT EXISTS pre_tournament_predictions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL UNIQUE REFERENCES participants(id) ON DELETE CASCADE,
  winner         TEXT,
  finalist       TEXT,
  top_scorer     TEXT,
  revelation     TEXT,
  total_goals    INTEGER,
  submitted      INTEGER NOT NULL DEFAULT 0,
  submitted_at   TEXT,
  admin_entered  INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bonus_questions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT    NOT NULL,
  phase          TEXT    NOT NULL CHECK(phase IN ('pre_tournament','group','round_of_32','round_of_16','quarter','semi','third_place','final')),
  answer_type    TEXT    NOT NULL CHECK(answer_type IN ('choice','number','text','multi_choice','number_multi')),
  options        TEXT,
  points_value   INTEGER NOT NULL DEFAULT 5,
  correct_answer TEXT,
  scoring_mode   TEXT    NOT NULL DEFAULT 'exact' CHECK(scoring_mode IN ('exact','closest_podium','multi_select','number_multi')),
  scoring_config TEXT,
  help_text      TEXT,
  is_published   INTEGER NOT NULL DEFAULT 1,
  deadline       TEXT    NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bonus_answers (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_id    INTEGER NOT NULL REFERENCES bonus_questions(id) ON DELETE CASCADE,
  answer         TEXT    NOT NULL,
  submitted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  admin_entered  INTEGER NOT NULL DEFAULT 0,
  UNIQUE(participant_id, question_id)
);

CREATE TABLE IF NOT EXISTS scores (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id    INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  match_id          INTEGER REFERENCES matches(id) ON DELETE CASCADE,
  bonus_question_id INTEGER REFERENCES bonus_questions(id) ON DELETE CASCADE,
  points            INTEGER NOT NULL DEFAULT 0,
  calculated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  CHECK(
    (match_id IS NOT NULL AND bonus_question_id IS NULL) OR
    (match_id IS NULL     AND bonus_question_id IS NOT NULL)
  ),
  UNIQUE(participant_id, match_id, bonus_question_id)
);

CREATE TABLE IF NOT EXISTS admin_users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE,
  password_hash TEXT    NOT NULL,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knockout_slots (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  side                TEXT    NOT NULL CHECK(side IN ('team1','team2')),
  source_kind         TEXT    NOT NULL DEFAULT 'manual' CHECK(source_kind IN ('manual','match')),
  source_label        TEXT    NOT NULL DEFAULT '',
  source_match_number INTEGER,
  source_outcome      TEXT    CHECK(source_outcome IN ('winner','loser') OR source_outcome IS NULL),
  is_confirmed        INTEGER NOT NULL DEFAULT 0 CHECK(is_confirmed IN (0,1)),
  updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(match_id, side)
);

CREATE TABLE IF NOT EXISTS pre_tournament_questions (
  key          TEXT PRIMARY KEY CHECK(key IN ('winner','finalist','top_scorer','revelation','total_goals')),
  label        TEXT NOT NULL,
  points_label TEXT NOT NULL,
  help_text    TEXT NOT NULL DEFAULT '',
  sort_order   INTEGER NOT NULL,
  is_enabled   INTEGER NOT NULL DEFAULT 1,
  points_value INTEGER,
  correct_answer TEXT
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  endpoint       TEXT    NOT NULL UNIQUE,
  p256dh         TEXT    NOT NULL,
  auth           TEXT    NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notification_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  kind           TEXT    NOT NULL,
  ref            TEXT    NOT NULL,
  sent_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, kind, ref)
);

CREATE TABLE IF NOT EXISTS ranking_snapshots (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date  TEXT    NOT NULL,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  rank           INTEGER NOT NULL,
  total_points   INTEGER NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(snapshot_date, participant_id)
);

CREATE TABLE IF NOT EXISTS sporting_day_rank_evolutions (
  sporting_day  TEXT    NOT NULL,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  points_before INTEGER NOT NULL,
  day_points    INTEGER NOT NULL,
  points_after  INTEGER NOT NULL,
  rank_before   INTEGER NOT NULL,
  rank_after    INTEGER NOT NULL,
  delta         INTEGER NOT NULL,
  is_climber    INTEGER NOT NULL DEFAULT 0,
  finalized_at  TEXT    NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (sporting_day, participant_id)
);

CREATE TABLE IF NOT EXISTS news_items (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT    NOT NULL UNIQUE,
  title        TEXT    NOT NULL,
  body         TEXT    NOT NULL DEFAULT '',
  icon         TEXT,
  media_path   TEXT,
  template_key TEXT,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  is_published INTEGER NOT NULL DEFAULT 0,
  published_at TEXT    NOT NULL DEFAULT (datetime('now')),
  created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pre_tournament_scores (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_key   TEXT    NOT NULL REFERENCES pre_tournament_questions(key) ON DELETE CASCADE,
  points         INTEGER NOT NULL DEFAULT 0,
  calculated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, question_key)
);

CREATE INDEX IF NOT EXISTS idx_participants_token ON participants(token);
CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id);
CREATE INDEX IF NOT EXISTS idx_predictions_participant ON predictions(participant_id);
CREATE INDEX IF NOT EXISTS idx_scores_participant ON scores(participant_id);
CREATE INDEX IF NOT EXISTS idx_knockout_slots_source
  ON knockout_slots(source_match_number, source_outcome);
CREATE INDEX IF NOT EXISTS idx_sporting_evo_climber
  ON sporting_day_rank_evolutions(sporting_day, is_climber);

CREATE TABLE IF NOT EXISTS scoring_point_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  source         TEXT    NOT NULL CHECK(source IN ('bonus','pre_tournament')),
  source_key     TEXT    NOT NULL,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  delta          INTEGER NOT NULL,
  occurred_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scoring_point_events_participant
  ON scoring_point_events(participant_id);

CREATE TABLE IF NOT EXISTS trophy_awards (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  trophy_key     TEXT    NOT NULL,
  tier           TEXT    NOT NULL DEFAULT '_',
  detail         TEXT    NOT NULL DEFAULT '',
  sporting_day   TEXT,
  awarded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, trophy_key, detail)
);
CREATE INDEX IF NOT EXISTS idx_trophy_awards_participant
  ON trophy_awards(participant_id);

CREATE TABLE IF NOT EXISTS bonus_result_views (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  source         TEXT    NOT NULL CHECK(source IN ('bonus_question','pre_tournament')),
  source_key     TEXT    NOT NULL,
  result_version TEXT    NOT NULL,
  seen_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_bonus_result_views_participant
  ON bonus_result_views(participant_id);
        """)
        await db.commit()

        participant_columns = [
            "has_paid INTEGER NOT NULL DEFAULT 0",
            "first_name TEXT",
            "last_name TEXT",
            "nickname TEXT",
            "favorite_team TEXT",
            "bio TEXT",
            "profile_visibility TEXT NOT NULL DEFAULT 'public' CHECK(profile_visibility IN ('public','limited'))",
            "email_opt_in INTEGER NOT NULL DEFAULT 1",
            "password_hash TEXT",
            "department TEXT",
            "avatar_path TEXT",
            "is_favorite INTEGER NOT NULL DEFAULT 0",
            "last_seen_news_id INTEGER NOT NULL DEFAULT 0",
            "last_revealed_date TEXT",
            "seen_trophies TEXT",
            "last_visit_date TEXT",
            "visit_streak INTEGER NOT NULL DEFAULT 0",
            "best_visit_streak INTEGER NOT NULL DEFAULT 0",
            "last_connected_sporting_day TEXT",
            "reveal_connection_baseline_day TEXT",
        ]
        for column in participant_columns:
            try:
                await db.execute(f"ALTER TABLE participants ADD COLUMN {column}")
            except Exception:
                pass

        for column in ["template_key TEXT"]:
            try:
                await db.execute(f"ALTER TABLE news_items ADD COLUMN {column}")
            except Exception:
                pass

        prediction_columns = [
            "qualifier_prediction TEXT CHECK(qualifier_prediction IN ('team1','team2') OR qualifier_prediction IS NULL)",
            "admin_entered INTEGER NOT NULL DEFAULT 0",
        ]
        for column in prediction_columns:
            try:
                await db.execute(f"ALTER TABLE predictions ADD COLUMN {column}")
            except Exception:
                pass

        for table in ("pre_tournament_predictions", "bonus_answers"):
            try:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN admin_entered INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass

        match_columns = [
            "qualifier_winner TEXT CHECK(qualifier_winner IN ('team1','team2') OR qualifier_winner IS NULL)",
            "predictions_open INTEGER NOT NULL DEFAULT 0 CHECK(predictions_open IN (0,1))",
            "final_score_team1 INTEGER",
            "final_score_team2 INTEGER",
        ]
        for column in match_columns:
            try:
                await db.execute(f"ALTER TABLE matches ADD COLUMN {column}")
            except Exception:
                pass

        await db.execute(
            """UPDATE matches
               SET final_score_team1=score_team1
               WHERE final_score_team1 IS NULL AND score_team1 IS NOT NULL"""
        )
        await db.execute(
            """UPDATE matches
               SET final_score_team2=score_team2
               WHERE final_score_team2 IS NULL AND score_team2 IS NOT NULL"""
        )

        await db.executescript("""
CREATE TABLE IF NOT EXISTS knockout_slots (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  side                TEXT    NOT NULL CHECK(side IN ('team1','team2')),
  source_kind         TEXT    NOT NULL DEFAULT 'manual' CHECK(source_kind IN ('manual','match')),
  source_label        TEXT    NOT NULL DEFAULT '',
  source_match_number INTEGER,
  source_outcome      TEXT    CHECK(source_outcome IN ('winner','loser') OR source_outcome IS NULL),
  is_confirmed        INTEGER NOT NULL DEFAULT 0 CHECK(is_confirmed IN (0,1)),
  updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(match_id, side)
);
CREATE INDEX IF NOT EXISTS idx_knockout_slots_source
  ON knockout_slots(source_match_number, source_outcome);
        """)
        open_migration_key = "migr_match_predictions_open_v1"
        open_migration_done = await (await db.execute(
            "SELECT 1 FROM app_settings WHERE key=?", (open_migration_key,)
        )).fetchone()
        if not open_migration_done:
            await db.execute("UPDATE matches SET predictions_open=1 WHERE phase='group'")
            await db.execute("UPDATE matches SET predictions_open=0 WHERE phase!='group'")
            await db.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
                (open_migration_key,),
            )

        from app.knockout import ensure_knockout_slots
        await ensure_knockout_slots(db)

        pt_question_columns = [
            "points_value INTEGER",
            "correct_answer TEXT",
        ]
        for column in pt_question_columns:
            try:
                await db.execute(f"ALTER TABLE pre_tournament_questions ADD COLUMN {column}")
            except Exception:
                pass

        # Migration: étendre les phases / modes des questions bonus.
        # SQLite ne modifie pas un CHECK: on reconstruit la table si l'ancien
        # schéma est détecté, en conservant les anciennes questions publiées.
        bonus_cols_rows = await (await db.execute("PRAGMA table_info(bonus_questions)")).fetchall()
        bonus_col_names = {c["name"] for c in bonus_cols_rows}
        schema_row = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='bonus_questions'"
        )
        schema = await schema_row.fetchone()
        bonus_schema = schema["sql"] if schema else ""
        if (
            schema
            and (
                "'group'" not in bonus_schema
                or "scoring_mode" not in bonus_col_names
                or "scoring_config" not in bonus_col_names
                or "is_published" not in bonus_col_names
                or "multi_choice" not in bonus_schema
                or "number_multi" not in bonus_schema
                or "help_text" not in bonus_col_names
            )
        ):
            is_published_expr = (
                "COALESCE(is_published, 1)" if "is_published" in bonus_col_names else "1"
            )
            scoring_mode_expr = (
                "COALESCE(scoring_mode, 'exact')" if "scoring_mode" in bonus_col_names else "'exact'"
            )
            scoring_config_expr = (
                "scoring_config" if "scoring_config" in bonus_col_names else "NULL"
            )
            help_text_expr = (
                "help_text" if "help_text" in bonus_col_names else "NULL"
            )
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.executescript("""
CREATE TABLE bonus_questions_new (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT    NOT NULL,
  phase          TEXT    NOT NULL CHECK(phase IN ('pre_tournament','group','round_of_32','round_of_16','quarter','semi','third_place','final')),
  answer_type    TEXT    NOT NULL CHECK(answer_type IN ('choice','number','text','multi_choice','number_multi')),
  options        TEXT,
  points_value   INTEGER NOT NULL DEFAULT 5,
  correct_answer TEXT,
  scoring_mode   TEXT    NOT NULL DEFAULT 'exact' CHECK(scoring_mode IN ('exact','closest_podium','multi_select','number_multi')),
  scoring_config TEXT,
  help_text      TEXT,
  is_published   INTEGER NOT NULL DEFAULT 1,
  deadline       TEXT    NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
            """)
            await db.execute(
                f"""INSERT INTO bonus_questions_new
                      (id, question_text, phase, answer_type, options, points_value,
                       correct_answer, scoring_mode, scoring_config, help_text,
                       is_published, deadline, created_at)
                    SELECT id, question_text, phase, answer_type, options, points_value,
                           correct_answer, {scoring_mode_expr}, {scoring_config_expr},
                           {help_text_expr}, {is_published_expr}, deadline, created_at
                    FROM bonus_questions"""
            )
            await db.executescript("""
DROP TABLE bonus_questions;
ALTER TABLE bonus_questions_new RENAME TO bonus_questions;
            """)
            await db.execute("PRAGMA foreign_keys = ON")

        await ensure_bonus_question_drafts(db)
        await ensure_round_of_32_bonus_drafts(db)
        await ensure_round_of_16_bonus_drafts(db)

        # Les anciens brouillons comptent désormais comme des réponses valides.
        await db.execute(
            """UPDATE pre_tournament_predictions
               SET submitted=1, submitted_at=COALESCE(submitted_at, created_at)
               WHERE submitted=0"""
        )

        await _normalize_existing_participant_names(db)
        await ensure_pre_tournament_defaults(db)
        await ensure_default_settings(db)
        await ensure_news_defaults(db)
        await _migrate_reveal_sporting_day(db)
        await _migrate_trophy_detail(db)
        await _migrate_trophy_sporting_day(db)
        await _backfill_trophy_awards(db)
        await _cleanup_journee_parfaite_awards(db)
        await _migrate_backfill_scoring_point_events(db)
        await db.commit()


async def ensure_bonus_question_drafts(db):
    """Prépare les deux bonus J3 en brouillon, une seule fois par base."""
    key = "bonus_drafts_group_j3_2026_v1"
    renamed_drafts = [
        (
            "Combien de buts seront marqués sur les 24 matchs de la troisième journée des groupes ?",
            "Feu d'artifice J3 - Combien de buts seront marqués sur les 24 matchs de la troisième journée des groupes ?",
        ),
        (
            "Y aura-t-il au moins un match avec 5 buts ou plus pendant la troisième journée des groupes ?",
            "Match popcorn J3 - Y aura-t-il au moins un match avec 5 buts ou plus pendant la troisième journée des groupes ?",
        ),
    ]
    for old_text, new_text in renamed_drafts:
        new_exists = await (await db.execute(
            "SELECT 1 FROM bonus_questions WHERE question_text=?", (new_text,)
        )).fetchone()
        if new_exists:
            continue
        await db.execute(
            """UPDATE bonus_questions
               SET question_text=?
               WHERE question_text=? AND is_published=0""",
            (new_text, old_text),
        )
    await db.execute(
        """UPDATE bonus_questions
           SET scoring_config='{"preset_key":"fun_balanced","award_mode":"podium_custom","tie_policy":"full_skip","rank_points":[6,4,2]}'
           WHERE scoring_mode='closest_podium'
             AND (scoring_config IS NULL OR scoring_config='')"""
    )

    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    drafts = [
        {
            "question_text": "Feu d'artifice J3 - Combien de buts seront marqués sur les 24 matchs de la troisième journée des groupes ?",
            "phase": "group",
            "answer_type": "number",
            "options": None,
            "points_value": 6,
            "correct_answer": None,
            "scoring_mode": "closest_podium",
            "scoring_config": '{"preset_key":"fun_balanced","award_mode":"podium_custom","tie_policy":"full_skip","rank_points":[6,4,2]}',
            "is_published": 0,
            "deadline": "2026-06-24T16:59:00",
        },
        {
            "question_text": "Match popcorn J3 - Y aura-t-il au moins un match avec 5 buts ou plus pendant la troisième journée des groupes ?",
            "phase": "group",
            "answer_type": "choice",
            "options": '["Oui","Non"]',
            "points_value": 6,
            "correct_answer": None,
            "scoring_mode": "exact",
            "scoring_config": None,
            "is_published": 0,
            "deadline": "2026-06-24T16:59:00",
        },
    ]
    for draft in drafts:
        exists = await (await db.execute(
            """SELECT 1 FROM bonus_questions
               WHERE question_text=? AND deadline=?""",
            (draft["question_text"], draft["deadline"]),
        )).fetchone()
        if exists:
            continue
        await db.execute(
            """INSERT INTO bonus_questions
               (question_text, phase, answer_type, options, points_value,
                correct_answer, scoring_mode, scoring_config, is_published,
                deadline)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                draft["question_text"],
                draft["phase"],
                draft["answer_type"],
                draft["options"],
                draft["points_value"],
                draft["correct_answer"],
                draft["scoring_mode"],
                draft["scoring_config"],
                draft["is_published"],
                draft["deadline"],
            ),
        )
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


def _numeric_answer_matches(given, correct) -> bool:
    try:
        return float(str(given or "").strip().replace(",", ".")) == float(
            str(correct or "").strip().replace(",", ".")
        )
    except ValueError:
        return str(given or "").strip().casefold() == str(correct or "").strip().casefold()


async def _rescore_exact_round32_bonus(db, question_id: int, correct_answer, points_value: int):
    await db.execute("DELETE FROM scores WHERE bonus_question_id=?", (question_id,))
    if correct_answer is None or str(correct_answer).strip() == "":
        return
    rows = await db.execute(
        "SELECT participant_id, answer FROM bonus_answers WHERE question_id=?",
        (question_id,),
    )
    for answer in await rows.fetchall():
        points = points_value if _numeric_answer_matches(answer["answer"], correct_answer) else 0
        await db.execute(
            """INSERT INTO scores (participant_id, bonus_question_id, points)
               VALUES (?,?,?)""",
            (answer["participant_id"], question_id, points),
        )


async def _migrate_round32_exact_numeric_bonus(db):
    """Convertit deux questions seizièmes de « plus proche » vers « exact ».

    Ces questions restent numériques côté participant, mais seules les réponses
    strictement correctes marquent 5 points. La migration est séparée du seed,
    car les bases existantes ont déjà posé le garde-fou app_settings du seed.
    """
    key = "migr_round32_exact_numeric_bonus_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    questions = [
        (
            ROUND32_TIEBREAK_COUNT_TEXT,
            json.dumps(ROUND32_TIEBREAK_EXACT_CONFIG, separators=(",", ":")),
            ROUND32_TIEBREAK_HELP,
        ),
        (
            ROUND32_FAVORITE_CONCEDE_TEXT,
            json.dumps(ROUND32_FAVORITE_EXACT_CONFIG, separators=(",", ":")),
            ROUND32_FAVORITE_HELP,
        ),
    ]
    changed_score_rows = False
    for question_text, scoring_config, help_text in questions:
        await db.execute(
            """UPDATE bonus_questions
               SET points_value=?,
                   scoring_mode='exact',
                   scoring_config=?,
                   help_text=?
               WHERE question_text=?""",
            (ROUND32_EXACT_POINTS, scoring_config, help_text, question_text),
        )
        rows = await db.execute(
            """SELECT id, correct_answer
               FROM bonus_questions
               WHERE question_text=? AND correct_answer IS NOT NULL""",
            (question_text,),
        )
        for question in await rows.fetchall():
            await _rescore_exact_round32_bonus(
                db,
                question["id"],
                question["correct_answer"],
                ROUND32_EXACT_POINTS,
            )
            changed_score_rows = True

    if changed_score_rows:
        from app.trophies import refresh_trophy_awards
        await refresh_trophy_awards(db)
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _migrate_round32_exact_help_text(db):
    """Retire le nombre de points codé en dur des tooltips de ces questions."""
    key = "migr_round32_exact_help_text_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    old_fragment = "Barème : 5 pts si le nombre exact est trouvé, 0 sinon."
    for question_text, help_text in (
        (ROUND32_TIEBREAK_COUNT_TEXT, ROUND32_TIEBREAK_HELP),
        (ROUND32_FAVORITE_CONCEDE_TEXT, ROUND32_FAVORITE_HELP),
    ):
        await db.execute(
            """UPDATE bonus_questions
               SET help_text=?
               WHERE question_text=?
                 AND (help_text IS NULL OR help_text='' OR help_text LIKE ?)""",
            (help_text, question_text, f"%{old_fragment}%"),
        )
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def ensure_round_of_32_bonus_drafts(db):
    """Prépare en brouillon les questions « seizièmes » (Afrique, tirs au but,
    favori qui tremble). Idempotent via app_settings. L'admin règle les deadlines
    et les réponses correctes, puis publie."""
    key = "bonus_drafts_round_of_32_2026_v4"
    await _migrate_round32_exact_numeric_bonus(db)
    await _migrate_round32_exact_help_text(db)
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    AFRICA_OPTIONS = [
        "Maroc", "Côte d'Ivoire", "RD Congo", "Sénégal", "Algérie",
        "Égypte", "Cap-Vert", "Ghana",
    ]
    africa_config = json.dumps({
        "locked_teams": ["Maroc"],
        "min_count": 1,
        "max_count": 8,
        "part1_points": 3,
        "team_step": 1,
        "max_points": 10,
    }, ensure_ascii=False, separators=(",", ":"))
    tab_count_config = json.dumps(ROUND32_TIEBREAK_EXACT_CONFIG, separators=(",", ":"))
    fun_config = json.dumps(ROUND32_FAVORITE_EXACT_CONFIG, separators=(",", ":"))
    placeholder_deadline = "2026-06-30T16:59:00"

    # Nettoyage des deux anciennes questions Afrique (v1) désormais fusionnées en
    # une seule question number_multi — uniquement si non répondues.
    obsolete_v1 = [
        "Afrique Mode Patron — Maroc déjà qualifié : combien d'équipes africaines (Maroc inclus) seront en huitièmes au total ?",
        "Afrique Mode Patron (expert) — Quelles équipes africaines encore en course rejoindront le Maroc en huitièmes ?",
        "Afrique Mode Patron — Combien d'équipes africaines (Maroc inclus) seront en huitièmes, et lesquelles parmi celles encore en course ?",
        "Encore des tirs au but ? — Après Paraguay et Maroc, y aura-t-il au moins une nouvelle séance de tirs au but sur les matchs restants des seizièmes ?",
        ROUND32_TIEBREAK_COUNT_TEXT,
    ]
    for text in obsolete_v1:
        await db.execute(
            """DELETE FROM bonus_questions
               WHERE question_text=?
                 AND id NOT IN (SELECT question_id FROM bonus_answers)""",
            (text,),
        )
        await db.execute(
            """UPDATE bonus_questions
               SET is_published=0
               WHERE question_text=?
                 AND id IN (SELECT question_id FROM bonus_answers)""",
            (text,),
        )

    drafts = [
        {
            "question_text": "Afrique Mode Patron — Combien d'équipes africaines (Maroc inclus) seront en huitièmes, et lesquelles parmi celles encore en course ?",
            "answer_type": "number_multi",
            "options": json.dumps(AFRICA_OPTIONS, ensure_ascii=False),
            "points_value": 10,
            "scoring_mode": "number_multi",
            "scoring_config": africa_config,
            "help_text": (
                "Maroc est déjà qualifié : sa tuile est validée et compte dans le "
                "total. Entre le total d'équipes africaines en huitièmes (de 1 à 8), "
                "puis coche les autres équipes qui se qualifient : le nombre de tuiles "
                "cochées, Maroc inclus, doit correspondre au total. Barème : 3 pts si "
                "le total est exact, +1 par autre équipe juste, −1 par erreur (jamais "
                "en dessous de 0 sur cette partie), 10 pts max. Résultat officiel FIFA, "
                "prolongation incluse, tirs au but exclus."
            ),
        },
        {
            "question_text": ROUND32_TIEBREAK_COUNT_TEXT,
            "answer_type": "number",
            "options": None,
            "points_value": ROUND32_EXACT_POINTS,
            "scoring_mode": "exact",
            "scoring_config": tab_count_config,
            "help_text": ROUND32_TIEBREAK_HELP,
        },
        {
            "question_text": ROUND32_FAVORITE_CONCEDE_TEXT,
            "answer_type": "number",
            "options": None,
            "points_value": ROUND32_EXACT_POINTS,
            "scoring_mode": "exact",
            "scoring_config": fun_config,
            "help_text": ROUND32_FAVORITE_HELP,
        },
    ]
    for draft in drafts:
        exists = await (await db.execute(
            "SELECT 1 FROM bonus_questions WHERE question_text=?",
            (draft["question_text"],),
        )).fetchone()
        if exists:
            continue
        await db.execute(
            """INSERT INTO bonus_questions
               (question_text, phase, answer_type, options, points_value,
                correct_answer, scoring_mode, scoring_config, help_text,
                is_published, deadline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                draft["question_text"],
                "round_of_32",
                draft["answer_type"],
                draft["options"],
                draft["points_value"],
                None,
                draft["scoring_mode"],
                draft["scoring_config"],
                draft["help_text"],
                0,
                placeholder_deadline,
            ),
        )
    await db.execute(
        "UPDATE bonus_questions SET scoring_config=? WHERE question_text=?",
        (
            tab_count_config,
            ROUND32_TIEBREAK_COUNT_TEXT,
        ),
    )
    await db.execute(
        "UPDATE bonus_questions SET scoring_config=? WHERE question_text=?",
        (
            fun_config,
            ROUND32_FAVORITE_CONCEDE_TEXT,
        ),
    )
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def ensure_round_of_16_bonus_drafts(db):
    """Prépare les bonus huitièmes en brouillon.

    L'admin règle ensuite les deadlines, les réponses correctes, puis publie.
    """
    key = "bonus_drafts_round_of_16_2026_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    number_multi_config = json.dumps(
        ROUND16_NUMBER_MULTI_CONFIG,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fastest_goal_config = json.dumps(
        ROUND16_FASTEST_GOAL_CONFIG,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    placeholder_deadline = "2026-07-04T16:59:00"
    drafts = [
        {
            "question_text": ROUND16_HOST_COUNTRIES_TEXT,
            "answer_type": "number_multi",
            "options": json.dumps(["Canada", "Mexique", "États-Unis"], ensure_ascii=False),
            "points_value": 10,
            "scoring_mode": "number_multi",
            "scoring_config": number_multi_config,
            "help_text": ROUND16_HOST_COUNTRIES_HELP,
        },
        {
            "question_text": ROUND16_RESCAPED_TIEBREAK_TEXT,
            "answer_type": "number_multi",
            "options": json.dumps(["Paraguay", "Maroc", "Égypte"], ensure_ascii=False),
            "points_value": 10,
            "scoring_mode": "number_multi",
            "scoring_config": number_multi_config,
            "help_text": ROUND16_RESCAPED_TIEBREAK_HELP,
        },
        {
            "question_text": ROUND16_FASTEST_GOAL_TEXT,
            "answer_type": "number",
            "options": None,
            "points_value": 5,
            "scoring_mode": "closest_podium",
            "scoring_config": fastest_goal_config,
            "help_text": ROUND16_FASTEST_GOAL_HELP,
        },
    ]
    for draft in drafts:
        exists = await (await db.execute(
            "SELECT 1 FROM bonus_questions WHERE question_text=?",
            (draft["question_text"],),
        )).fetchone()
        if exists:
            continue
        await db.execute(
            """INSERT INTO bonus_questions
               (question_text, phase, answer_type, options, points_value,
                correct_answer, scoring_mode, scoring_config, help_text,
                is_published, deadline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                draft["question_text"],
                "round_of_16",
                draft["answer_type"],
                draft["options"],
                draft["points_value"],
                None,
                draft["scoring_mode"],
                draft["scoring_config"],
                draft["help_text"],
                0,
                placeholder_deadline,
            ),
        )

    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _migrate_reveal_sporting_day(db):
    """One-shot : aligne `last_revealed_date` sur la « journée sportive » (cutoff 9h).

    Avant Reveal v2, ce champ stockait la date CALENDRIER du dernier match vu ;
    la nouvelle logique la compare à la journée sportive (kickoff − 9h). Les matchs
    joués entre 0h et 9h retombent sur la veille sportive : une valeur héritée peut
    donc marquer toute une nuit comme « vue » à tort, masquant le reveal de la nuit.
    On recule la borne d'un jour — l'écart maximal entre date calendrier et journée
    sportive — une seule fois (garde via app_settings, donc pas de double recul).
    """
    key = "migr_reveal_sporting_day_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return
    await db.execute(
        """UPDATE participants
           SET last_revealed_date = date(last_revealed_date, '-1 day')
           WHERE last_revealed_date IS NOT NULL AND last_revealed_date <> ''"""
    )
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _migrate_trophy_detail(db):
    """Refonte des trophées (W8) : ajoute la colonne `detail` (discriminant
    d'occurrence pour les trophées répétables) et passe la contrainte d'unicité à
    (participant_id, trophy_key, detail). L'ancien contenu (système v1) est purgé
    puis recalculé par le nouveau moteur — il ne s'agit pas d'une révocation de
    trophées du système courant mais du remplacement d'un système entier.
    """
    cols = await (await db.execute("PRAGMA table_info(trophy_awards)")).fetchall()
    if any(c["name"] == "detail" for c in cols):
        return
    await db.execute("PRAGMA foreign_keys=OFF")
    await db.executescript(
        """
        DROP TABLE IF EXISTS trophy_awards;
        CREATE TABLE trophy_awards (
          id             INTEGER PRIMARY KEY AUTOINCREMENT,
          participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
          trophy_key     TEXT    NOT NULL,
          tier           TEXT    NOT NULL DEFAULT '_',
          detail         TEXT    NOT NULL DEFAULT '',
          sporting_day   TEXT,
          awarded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
          UNIQUE(participant_id, trophy_key, detail)
        );
        CREATE INDEX IF NOT EXISTS idx_trophy_awards_participant
          ON trophy_awards(participant_id);
        CREATE INDEX IF NOT EXISTS idx_trophy_awards_sporting_day
          ON trophy_awards(sporting_day);
        """
    )
    await db.execute("PRAGMA foreign_keys=ON")
    # Recalcul immédiat (le garde-fou _backfill peut déjà être posé sur une base
    # existante, il ne relancerait donc pas le refresh par lui-même).
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)


async def _migrate_trophy_sporting_day(db):
    """Rattache chaque trophée à sa journée sportive réelle.

    ``awarded_at`` reste la date technique d'écriture et ne permet pas de savoir
    pendant quelle journée le trophée a été gagné (notamment après un backfill).
    Le moteur de trophées reconstruit ici la date métier des lignes existantes.
    """
    cols = await (await db.execute("PRAGMA table_info(trophy_awards)")).fetchall()
    has_sporting_day = any(c["name"] == "sporting_day" for c in cols)
    if not has_sporting_day:
        await db.execute("ALTER TABLE trophy_awards ADD COLUMN sporting_day TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_trophy_awards_sporting_day "
        "ON trophy_awards(sporting_day)"
    )
    key = "migr_trophy_sporting_day_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return

    # Ces trophées portent historiquement leur journée dans ``detail``. On la
    # copie directement afin de conserver aussi les occurrences qui ne seraient
    # plus recalculables après une correction de résultats.
    await db.execute(
        """UPDATE trophy_awards
           SET sporting_day=detail
           WHERE trophy_key IN ('grimpeur', 'journee_parfaite')
             AND length(detail)=10
             AND detail GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"""
    )
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _backfill_trophy_awards(db):
    """One-shot : peuple trophy_awards depuis les données existantes."""
    key = "migr_backfill_trophy_awards_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _migrate_backfill_scoring_point_events(db):
    """One-shot : reconstruit les événements bonus/pré-tournoi déjà attribués
    avant l'existence de `scoring_point_events`, puis resynchronise l'historique
    de rang et les trophées avec ces mouvements retrouvés.

    Sans cette étape, les points bonus/pré-tournoi déjà encodés en production
    au moment du déploiement de cette table resteraient invisibles : flèches
    de `/classement`, Reveal, Grimpeur et carrousel de badges ne bougeraient
    qu'à partir du prochain recalcul, pas pour l'historique déjà en base.
    """
    key = "migr_backfill_scoring_point_events_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return
    from app.scoring import backfill_scoring_point_events, sync_finalized_evolution_history
    await backfill_scoring_point_events(db)
    await sync_finalized_evolution_history(db)
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def _cleanup_journee_parfaite_awards(db):
    """One-shot : retire les Journées parfaites attribuées avant la fin du jour."""
    key = "migr_cleanup_journee_parfaite_complete_day_v1"
    done = await (await db.execute(
        "SELECT 1 FROM app_settings WHERE key=?", (key,)
    )).fetchone()
    if done:
        return
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)
    await db.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now'))",
        (key,),
    )


async def ensure_news_defaults(db):
    """Seed des nouveautés livrées avec leur feature (registre app.news)."""
    from app.news import NEWS_DEFAULTS
    for item in NEWS_DEFAULTS:
        await db.execute(
            """INSERT INTO news_items (slug, title, body, icon, template_key, sort_order, is_published)
               VALUES (:slug, :title, :body, :icon, :template_key, :sort_order, :is_published)
               ON CONFLICT(slug) DO NOTHING""",
            item,
        )
