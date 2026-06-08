#!/usr/bin/env python3
"""
Seed 78 FIFA 2026 matches into the database.
FIFA 2026 format: 16 groups of 3 teams → 48 group matches
+ Round of 32 (16) + QF (8) + SF (4) + 3rd place (1) + Final (1) = 78
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
import aiosqlite
from app.config import settings

DB_PATH = settings.DATABASE_URL.replace("./", "")

GROUPS = {
    "A": ["Équateur", "Panama", "Jamaïque"],
    "B": ["États-Unis", "Canada", "Cuba"],
    "C": ["Mexique", "Guatemala", "Honduras"],
    "D": ["Maroc", "Portugal", "Uruguay"],
    "E": ["France", "Allemagne", "Belgique"],
    "F": ["Espagne", "Croatie", "Albanie"],
    "G": ["Argentine", "Chili", "Pérou"],
    "H": ["Brésil", "Colombie", "Paraguay"],
    "I": ["Angleterre", "Pays-Bas", "Sénégal"],
    "J": ["Japon", "Corée du Sud", "Arabie Saoudite"],
    "K": ["Australie", "Iran", "Nouvelle-Zélande"],
    "L": ["Cameroun", "Nigeria", "Côte d'Ivoire"],
    "M": ["Égypte", "Afrique du Sud", "Algérie"],
    "N": ["Tunisie", "Ghana", "RD Congo"],
    "O": ["Qatar", "Koweït", "Bahreïn"],
    "P": ["Inde", "Indonésie", "Viêt Nam"],
}

# Group stage: 3 matches per group (A vs B, A vs C, B vs C)
# Dates start June 11, rotating through host cities
# Group stage: June 11 – July 2, 2026
GROUP_DATES = {
    "A": [("2026-06-11", "18:00"), ("2026-06-14", "18:00"), ("2026-06-23", "18:00")],
    "B": [("2026-06-11", "21:00"), ("2026-06-14", "21:00"), ("2026-06-23", "21:00")],
    "C": [("2026-06-12", "18:00"), ("2026-06-15", "18:00"), ("2026-06-24", "18:00")],
    "D": [("2026-06-12", "21:00"), ("2026-06-15", "21:00"), ("2026-06-24", "21:00")],
    "E": [("2026-06-13", "18:00"), ("2026-06-16", "18:00"), ("2026-06-25", "18:00")],
    "F": [("2026-06-13", "21:00"), ("2026-06-16", "21:00"), ("2026-06-25", "21:00")],
    "G": [("2026-06-17", "18:00"), ("2026-06-20", "18:00"), ("2026-06-26", "18:00")],
    "H": [("2026-06-17", "21:00"), ("2026-06-20", "21:00"), ("2026-06-26", "21:00")],
    "I": [("2026-06-18", "18:00"), ("2026-06-21", "18:00"), ("2026-06-27", "18:00")],
    "J": [("2026-06-18", "21:00"), ("2026-06-21", "21:00"), ("2026-06-27", "21:00")],
    "K": [("2026-06-19", "18:00"), ("2026-06-22", "18:00"), ("2026-06-28", "18:00")],
    "L": [("2026-06-19", "21:00"), ("2026-06-22", "21:00"), ("2026-06-28", "21:00")],
    "M": [("2026-06-29", "18:00"), ("2026-07-01", "18:00"), ("2026-07-02", "18:00")],
    "N": [("2026-06-29", "21:00"), ("2026-07-01", "21:00"), ("2026-07-02", "21:00")],
    "O": [("2026-06-30", "18:00"), ("2026-07-01", "18:00"), ("2026-07-03", "18:00")],
    "P": [("2026-06-30", "21:00"), ("2026-07-01", "21:00"), ("2026-07-03", "21:00")],
}

# Round of 32: July 5-8
R32_DATES = [
    ("2026-07-05", "18:00"), ("2026-07-05", "21:00"),
    ("2026-07-06", "18:00"), ("2026-07-06", "21:00"),
    ("2026-07-07", "18:00"), ("2026-07-07", "21:00"),
    ("2026-07-08", "18:00"), ("2026-07-08", "21:00"),
    ("2026-07-05", "15:00"), ("2026-07-05", "00:00"),
    ("2026-07-06", "15:00"), ("2026-07-06", "00:00"),
    ("2026-07-07", "15:00"), ("2026-07-07", "00:00"),
    ("2026-07-08", "15:00"), ("2026-07-08", "00:00"),
]

QF_DATES = [
    ("2026-07-11", "18:00"), ("2026-07-11", "21:00"),
    ("2026-07-12", "18:00"), ("2026-07-12", "21:00"),
    ("2026-07-11", "15:00"), ("2026-07-11", "00:00"),
    ("2026-07-12", "15:00"), ("2026-07-12", "00:00"),
]

SF_DATES = [
    ("2026-07-15", "21:00"),
    ("2026-07-16", "21:00"),
    ("2026-07-15", "18:00"),
    ("2026-07-16", "18:00"),
]


async def seed():
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Check if already seeded
        row = await db.execute("SELECT COUNT(*) as cnt FROM matches")
        count = (await row.fetchone())["cnt"]
        if count > 0:
            print(f"Already {count} matches in DB. Use --force to reset.")
            if "--force" not in sys.argv:
                return

        await db.execute("DELETE FROM matches")
        await db.commit()

        match_num = 1

        # Group stage
        for grp, teams in GROUPS.items():
            dates = GROUP_DATES.get(grp, [("2026-06-11", "18:00")] * 3)
            matchups = [(0, 1), (0, 2), (1, 2)]  # indices into teams
            for i, (t1i, t2i) in enumerate(matchups):
                date, time = dates[i]
                is_j3 = (i == 2)  # Third match of group = Top Match
                weight = 2 if is_j3 else 1
                await db.execute(
                    """INSERT INTO matches
                       (match_number, phase, group_name, match_date, kickoff_time,
                        team1_name, team2_name, is_top_match, weight)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (match_num, "group", f"Groupe {grp}", date, time,
                     teams[t1i], teams[t2i], 1 if is_j3 else 0, weight)
                )
                match_num += 1

        # Round of 32 (16 matches)
        for i in range(16):
            date, time = R32_DATES[i % len(R32_DATES)]
            await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, is_top_match, weight)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (match_num, "round_of_32", date, time,
                 f"Équipe R32-{i+1}A", f"Équipe R32-{i+1}B", 0, 2)
            )
            match_num += 1

        # Quarterfinals (8 matches)
        for i in range(8):
            date, time = QF_DATES[i % len(QF_DATES)]
            await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, is_top_match, weight)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (match_num, "quarter", date, time,
                 f"Équipe QF-{i+1}A", f"Équipe QF-{i+1}B", 0, 2)
            )
            match_num += 1

        # Semifinals (4 matches)
        for i in range(4):
            date, time = SF_DATES[i]
            await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, is_top_match, weight)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (match_num, "semi", date, time,
                 f"Équipe SF-{i+1}A", f"Équipe SF-{i+1}B", 0, 2)
            )
            match_num += 1

        # 3rd place
        await db.execute(
            """INSERT INTO matches
               (match_number, phase, match_date, kickoff_time,
                team1_name, team2_name, is_top_match, weight)
               VALUES (?,?,?,?,?,?,?,?)""",
            (match_num, "third_place", "2026-07-19", "17:00",
             "Équipe 3ème A", "Équipe 3ème B", 0, 2)
        )
        match_num += 1

        # Final
        await db.execute(
            """INSERT INTO matches
               (match_number, phase, match_date, kickoff_time,
                team1_name, team2_name, is_top_match, weight)
               VALUES (?,?,?,?,?,?,?,?)""",
            (match_num, "final", "2026-07-19", "21:00",
             "Équipe Finale A", "Équipe Finale B", 1, 2)
        )

        await db.commit()
        row = await db.execute("SELECT COUNT(*) as cnt FROM matches")
        total = (await row.fetchone())["cnt"]
        print(f"✓ {total} matches seeded successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
