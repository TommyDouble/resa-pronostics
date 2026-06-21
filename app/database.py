import aiosqlite
import os
from contextlib import asynccontextmanager
from app.config import settings
from app.nameutils import build_full_name, split_full_name
from app.pre_tournament import ensure_pre_tournament_defaults
from app.settings_store import ensure_default_settings

DB_PATH = settings.DATABASE_URL.replace("./", "")


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
  phase          TEXT    NOT NULL CHECK(phase IN ('pre_tournament','round_of_32','round_of_16','quarter','semi','third_place','final')),
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
CREATE INDEX IF NOT EXISTS idx_sporting_evo_climber
  ON sporting_day_rank_evolutions(sporting_day, is_climber);

CREATE TABLE IF NOT EXISTS trophy_awards (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  trophy_key     TEXT    NOT NULL,
  tier           TEXT    NOT NULL DEFAULT '_',
  detail         TEXT    NOT NULL DEFAULT '',
  awarded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, trophy_key, detail)
);
CREATE INDEX IF NOT EXISTS idx_trophy_awards_participant
  ON trophy_awards(participant_id);
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

        # Migration: étendre les phases autorisées des questions bonus
        # (huitièmes, 3e place, finale). SQLite ne modifie pas un CHECK:
        # on reconstruit la table si l'ancien schéma est détecté.
        schema_row = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='bonus_questions'"
        )
        schema = await schema_row.fetchone()
        if schema and "round_of_16" not in schema["sql"]:
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.executescript("""
CREATE TABLE bonus_questions_new (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT    NOT NULL,
  phase          TEXT    NOT NULL CHECK(phase IN ('pre_tournament','round_of_32','round_of_16','quarter','semi','third_place','final')),
  answer_type    TEXT    NOT NULL CHECK(answer_type IN ('choice','number','text')),
  options        TEXT,
  points_value   INTEGER NOT NULL DEFAULT 5,
  correct_answer TEXT,
  deadline       TEXT    NOT NULL,
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO bonus_questions_new
  SELECT id, question_text, phase, answer_type, options, points_value, correct_answer, deadline, created_at
  FROM bonus_questions;
DROP TABLE bonus_questions;
ALTER TABLE bonus_questions_new RENAME TO bonus_questions;
            """)
            await db.execute("PRAGMA foreign_keys = ON")

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
        await _backfill_trophy_awards(db)
        await db.commit()


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
          awarded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
          UNIQUE(participant_id, trophy_key, detail)
        );
        CREATE INDEX IF NOT EXISTS idx_trophy_awards_participant
          ON trophy_awards(participant_id);
        """
    )
    await db.execute("PRAGMA foreign_keys=ON")
    # Recalcul immédiat (le garde-fou _backfill peut déjà être posé sur une base
    # existante, il ne relancerait donc pas le refresh par lui-même).
    from app.trophies import refresh_trophy_awards
    await refresh_trophy_awards(db)


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
