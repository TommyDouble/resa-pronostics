"""Seed trophy_awards with all trophy types (5+ winners each) for visual testing.

Usage:
    python scripts/seed_test_trophies.py          # insert test data
    python scripts/seed_test_trophies.py --revert  # remove test data

Inserts trophies on sporting_day = '2026-06-20' (the current max).
Thomas Wehenkel (id=9) is included in most trophies for user-first testing.
"""

import sqlite3
import sys

DB = "resa.db"
SPORTING_DAY = "2026-06-20"
SEED_TAG = "seed-test"  # used in 'detail' to identify seeded rows

# Thomas Wehenkel = 9
# Technique et Opérationnel members for champion_poules/champion_tournoi
TECH_DEPT = [9, 12, 14, 26, 32, 33, 34, 36, 55, 62, 69, 78]
# Direction générale
DG_DEPT = [16, 17, 22, 23, 25, 44]

# Diverse participants for regular trophies
P = {
    "thomas": 9,
    "dimitri": 12,
    "maxime": 14,
    "laurent_d": 7,
    "nathan": 10,
    "bernard": 11,
    "raph": 18,
    "ben": 19,
    "thierry": 8,
    "seb": 26,
    "luc_martin": 32,
    "benjamin": 33,
    "thomas_l": 34,
    "thomas_d": 38,
    "julie": 17,
    "jd": 16,
    "oli": 59,
    "maxence": 30,
    "dorothee": 63,
    "nathali": 20,
    "yoan": 21,
    "laurence": 53,
    "justine": 82,
    "seba": 51,
}

# Recent played match IDs for detail references
MATCH_IDS = [86, 87, 88, 89, 90]

AWARDS = []

# --- journee_parfaite: 6 winners (repeatable, detail = match-origin or sporting_day) ---
for pid in [P["thomas"], P["dimitri"], P["raph"], P["ben"], P["julie"], P["maxence"]]:
    AWARDS.append((pid, "journee_parfaite", SPORTING_DAY, SPORTING_DAY))

# --- grimpeur: 7 winners (same delta — grimpeur is always the max climb) ---
GRIMPEUR_WINNERS = [
    (P["thomas"], 12),
    (P["laurent_d"], 12),
    (P["nathan"], 12),
    (P["seb"], 12),
    (P["benjamin"], 12),
    (P["thomas_l"], 12),
    (P["nathali"], 12),
]
for pid, _delta in GRIMPEUR_WINNERS:
    AWARDS.append((pid, "grimpeur", SPORTING_DAY, SPORTING_DAY))

# --- extraterrestre: 5 winners (legendary, detail = match_id) ---
for pid, mid in [
    (P["thomas"], MATCH_IDS[0]),
    (P["maxime"], MATCH_IDS[1]),
    (P["thierry"], MATCH_IDS[2]),
    (P["bernard"], MATCH_IDS[0]),
    (P["dorothee"], MATCH_IDS[3]),
]:
    AWARDS.append((pid, "extraterrestre", str(mid), SPORTING_DAY))

# --- sniper: 5 winners (legendary) ---
for pid, mid in [
    (P["thomas"], MATCH_IDS[0]),
    (P["oli"], MATCH_IDS[1]),
    (P["julie"], MATCH_IDS[2]),
    (P["jd"], MATCH_IDS[3]),
    (P["yoan"], MATCH_IDS[4]),
]:
    AWARDS.append((pid, "sniper", str(mid), SPORTING_DAY))

# --- la_serie: 6 winners (rare, detail = match_id) ---
for pid, mid in [
    (P["thomas"], MATCH_IDS[0]),
    (P["raph"], MATCH_IDS[1]),
    (P["laurent_d"], MATCH_IDS[2]),
    (P["maxence"], MATCH_IDS[3]),
    (P["ben"], MATCH_IDS[4]),
    (P["seba"], MATCH_IDS[0]),
]:
    AWARDS.append((pid, "la_serie", str(mid), SPORTING_DAY))

# --- le_jumeau: 6 pairs (rare, detail = partner participant_id) ---
TWIN_PAIRS = [
    (P["thomas"], P["dimitri"]),
    (P["raph"], P["ben"]),
    (P["julie"], P["jd"]),
    (P["laurent_d"], P["nathan"]),
    (P["maxime"], P["seb"]),
    (P["thierry"], P["nathali"]),
]
for a, b in TWIN_PAIRS:
    AWARDS.append((a, "le_jumeau", str(b), SPORTING_DAY))
    AWARDS.append((b, "le_jumeau", str(a), SPORTING_DAY))

# --- champion_poules: 2 departments (rare, all members get it) ---
# Technique et Opérationnel (12 members) + Direction générale (6 members)
for pid in TECH_DEPT:
    AWARDS.append((pid, "champion_poules", SEED_TAG, SPORTING_DAY))
for pid in DG_DEPT:
    AWARDS.append((pid, "champion_poules", SEED_TAG, SPORTING_DAY))

# --- roi_du_nul: 5 winners ---
for pid in [P["thomas"], P["bernard"], P["thierry"], P["seb"], P["dorothee"]]:
    AWARDS.append((pid, "roi_du_nul", str(MATCH_IDS[0]), SPORTING_DAY))


def insert(conn: sqlite3.Connection):
    cur = conn.cursor()

    # Clear existing awards for this sporting day (to avoid duplicates on re-run)
    cur.execute(
        "DELETE FROM trophy_awards WHERE sporting_day = ? AND detail LIKE ?",
        (SPORTING_DAY, f"%{SEED_TAG}%"),
    )
    # Also clear seeded rows by checking participant+key+day combos
    for pid, key, detail, day in AWARDS:
        cur.execute(
            "DELETE FROM trophy_awards WHERE participant_id = ? AND trophy_key = ? AND sporting_day = ?",
            (pid, key, day),
        )

    # Insert climber deltas
    for pid, delta in GRIMPEUR_WINNERS:
        cur.execute(
            "UPDATE sporting_day_rank_evolutions SET is_climber = 1, delta = ? "
            "WHERE participant_id = ? AND sporting_day = ?",
            (delta, pid, SPORTING_DAY),
        )

    # Insert awards
    for pid, key, detail, day in AWARDS:
        cur.execute(
            "INSERT OR IGNORE INTO trophy_awards (participant_id, trophy_key, detail, sporting_day, awarded_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (pid, key, detail, day),
        )

    conn.commit()
    print(f"Inserted {len(AWARDS)} trophy awards on {SPORTING_DAY}")

    # Summary
    cur.execute(
        "SELECT trophy_key, COUNT(*) FROM trophy_awards WHERE sporting_day = ? GROUP BY trophy_key ORDER BY trophy_key",
        (SPORTING_DAY,),
    )
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} winners")


def revert(conn: sqlite3.Connection):
    cur = conn.cursor()
    for pid, key, detail, day in AWARDS:
        cur.execute(
            "DELETE FROM trophy_awards WHERE participant_id = ? AND trophy_key = ? AND sporting_day = ?",
            (pid, key, day),
        )
    # Reset climber flags
    for pid, delta in GRIMPEUR_WINNERS:
        cur.execute(
            "UPDATE sporting_day_rank_evolutions SET is_climber = 0, delta = 0 "
            "WHERE participant_id = ? AND sporting_day = ?",
            (pid, SPORTING_DAY),
        )
    conn.commit()
    print("Reverted all seeded trophy awards.")


if __name__ == "__main__":
    conn = sqlite3.connect(DB)
    if "--revert" in sys.argv:
        revert(conn)
    else:
        insert(conn)
    conn.close()
