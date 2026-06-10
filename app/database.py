import aiosqlite
import os
from contextlib import asynccontextmanager
from app.config import settings
from app.pre_tournament import ensure_pre_tournament_defaults
from app.settings_store import ensure_default_settings

DB_PATH = settings.DATABASE_URL.replace("./", "")


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
  result       TEXT    CHECK(result IN ('team1','draw','team2') OR result IS NULL),
  qualifier_winner TEXT CHECK(qualifier_winner IN ('team1','team2') OR qualifier_winner IS NULL),
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
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bonus_questions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT    NOT NULL,
  phase          TEXT    NOT NULL CHECK(phase IN ('pre_tournament','round_of_32','quarter','semi')),
  answer_type    TEXT    NOT NULL CHECK(answer_type IN ('choice','number','text')),
  options        TEXT,
  points_value   INTEGER NOT NULL DEFAULT 5,
  correct_answer TEXT,
  deadline       TEXT    NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bonus_answers (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_id    INTEGER NOT NULL REFERENCES bonus_questions(id) ON DELETE CASCADE,
  answer         TEXT    NOT NULL,
  submitted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
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
        ]
        for column in participant_columns:
            try:
                await db.execute(f"ALTER TABLE participants ADD COLUMN {column}")
            except Exception:
                pass

        prediction_columns = [
            "qualifier_prediction TEXT CHECK(qualifier_prediction IN ('team1','team2') OR qualifier_prediction IS NULL)",
        ]
        for column in prediction_columns:
            try:
                await db.execute(f"ALTER TABLE predictions ADD COLUMN {column}")
            except Exception:
                pass

        match_columns = [
            "qualifier_winner TEXT CHECK(qualifier_winner IN ('team1','team2') OR qualifier_winner IS NULL)",
        ]
        for column in match_columns:
            try:
                await db.execute(f"ALTER TABLE matches ADD COLUMN {column}")
            except Exception:
                pass

        pt_question_columns = [
            "points_value INTEGER",
            "correct_answer TEXT",
        ]
        for column in pt_question_columns:
            try:
                await db.execute(f"ALTER TABLE pre_tournament_questions ADD COLUMN {column}")
            except Exception:
                pass

        await ensure_pre_tournament_defaults(db)
        await ensure_default_settings(db)
        await db.commit()
