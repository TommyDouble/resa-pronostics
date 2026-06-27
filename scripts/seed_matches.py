#!/usr/bin/env python3
"""
Seed 104 FIFA 2026 matches.
Format réel : 12 groupes de 4 équipes → 72 matchs de groupes
+ Seizièmes (16) + Huitièmes (8) + Quarts (4) + Demies (2) + 3e place (1) + Finale (1) = 104
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
import aiosqlite
from app.config import settings

DB_PATH = settings.DATABASE_URL.replace("./", "")

MATCHES = [
    (1, "group", "Groupe A", "2026-06-11", "19:00", "Mexique", "Afrique du Sud", False, 1),
    (2, "group", "Groupe A", "2026-06-12", "02:00", "Corée du Sud", "Tchéquie", False, 1),
    (3, "group", "Groupe B", "2026-06-12", "19:00", "Canada", "Bosnie-Herzégovine", False, 1),
    (4, "group", "Groupe D", "2026-06-13", "01:00", "États-Unis", "Paraguay", False, 1),
    (5, "group", "Groupe C", "2026-06-14", "01:00", "Haïti", "Écosse", False, 1),
    (6, "group", "Groupe D", "2026-06-14", "04:00", "Australie", "Turquie", False, 1),
    (7, "group", "Groupe C", "2026-06-13", "22:00", "Brésil", "Maroc", False, 1),
    (8, "group", "Groupe B", "2026-06-13", "19:00", "Qatar", "Suisse", False, 1),
    (9, "group", "Groupe E", "2026-06-14", "23:00", "Côte d'Ivoire", "Équateur", False, 1),
    (10, "group", "Groupe E", "2026-06-14", "17:00", "Allemagne", "Curaçao", False, 1),
    (11, "group", "Groupe F", "2026-06-14", "20:00", "Pays-Bas", "Japon", False, 1),
    (12, "group", "Groupe F", "2026-06-15", "02:00", "Suède", "Tunisie", False, 1),
    (13, "group", "Groupe H", "2026-06-15", "22:00", "Arabie Saoudite", "Uruguay", False, 1),
    (14, "group", "Groupe H", "2026-06-15", "16:00", "Espagne", "Cap-Vert", False, 1),
    (15, "group", "Groupe G", "2026-06-16", "01:00", "Iran", "Nouvelle-Zélande", False, 1),
    (16, "group", "Groupe G", "2026-06-15", "19:00", "Belgique", "Égypte", False, 1),
    (17, "group", "Groupe I", "2026-06-16", "19:00", "France", "Sénégal", False, 1),
    (18, "group", "Groupe I", "2026-06-16", "22:00", "Irak", "Norvège", False, 1),
    (19, "group", "Groupe J", "2026-06-17", "01:00", "Argentine", "Algérie", False, 1),
    (20, "group", "Groupe J", "2026-06-17", "04:00", "Autriche", "Jordanie", False, 1),
    (21, "group", "Groupe L", "2026-06-17", "23:00", "Ghana", "Panama", False, 1),
    (22, "group", "Groupe L", "2026-06-17", "20:00", "Angleterre", "Croatie", False, 1),
    (23, "group", "Groupe K", "2026-06-17", "17:00", "Portugal", "RD Congo", False, 1),
    (24, "group", "Groupe K", "2026-06-18", "02:00", "Ouzbékistan", "Colombie", False, 1),
    (25, "group", "Groupe A", "2026-06-18", "16:00", "Tchéquie", "Afrique du Sud", False, 1),
    (26, "group", "Groupe B", "2026-06-18", "19:00", "Suisse", "Bosnie-Herzégovine", False, 1),
    (27, "group", "Groupe B", "2026-06-18", "22:00", "Canada", "Qatar", False, 1),
    (28, "group", "Groupe A", "2026-06-19", "01:00", "Mexique", "Corée du Sud", False, 1),
    (29, "group", "Groupe C", "2026-06-20", "00:30", "Brésil", "Haïti", False, 1),
    (30, "group", "Groupe C", "2026-06-19", "22:00", "Écosse", "Maroc", False, 1),
    (31, "group", "Groupe D", "2026-06-20", "03:00", "Turquie", "Paraguay", False, 1),
    (32, "group", "Groupe D", "2026-06-19", "19:00", "États-Unis", "Australie", False, 1),
    (33, "group", "Groupe E", "2026-06-20", "20:00", "Allemagne", "Côte d'Ivoire", False, 1),
    (34, "group", "Groupe E", "2026-06-21", "00:00", "Équateur", "Curaçao", False, 1),
    (35, "group", "Groupe F", "2026-06-20", "17:00", "Pays-Bas", "Suède", False, 1),
    (36, "group", "Groupe F", "2026-06-21", "04:00", "Tunisie", "Japon", False, 1),
    (37, "group", "Groupe H", "2026-06-21", "22:00", "Uruguay", "Cap-Vert", False, 1),
    (38, "group", "Groupe H", "2026-06-21", "16:00", "Espagne", "Arabie Saoudite", False, 1),
    (39, "group", "Groupe G", "2026-06-21", "19:00", "Belgique", "Iran", False, 1),
    (40, "group", "Groupe G", "2026-06-22", "01:00", "Nouvelle-Zélande", "Égypte", False, 1),
    (41, "group", "Groupe I", "2026-06-23", "00:00", "Norvège", "Sénégal", False, 1),
    (42, "group", "Groupe I", "2026-06-22", "21:00", "France", "Irak", False, 1),
    (43, "group", "Groupe J", "2026-06-22", "17:00", "Argentine", "Autriche", False, 1),
    (44, "group", "Groupe J", "2026-06-23", "03:00", "Jordanie", "Algérie", False, 1),
    (45, "group", "Groupe L", "2026-06-23", "20:00", "Angleterre", "Ghana", False, 1),
    (46, "group", "Groupe L", "2026-06-23", "23:00", "Panama", "Croatie", False, 1),
    (47, "group", "Groupe K", "2026-06-23", "17:00", "Portugal", "Ouzbékistan", False, 1),
    (48, "group", "Groupe K", "2026-06-24", "02:00", "Colombie", "RD Congo", False, 1),
    (49, "group", "Groupe C", "2026-06-24", "22:00", "Écosse", "Brésil", True, 2),
    (50, "group", "Groupe C", "2026-06-24", "22:00", "Maroc", "Haïti", True, 2),
    (51, "group", "Groupe B", "2026-06-24", "19:00", "Suisse", "Canada", True, 2),
    (52, "group", "Groupe B", "2026-06-24", "19:00", "Bosnie-Herzégovine", "Qatar", True, 2),
    (53, "group", "Groupe A", "2026-06-25", "01:00", "Tchéquie", "Mexique", True, 2),
    (54, "group", "Groupe A", "2026-06-25", "01:00", "Afrique du Sud", "Corée du Sud", True, 2),
    (55, "group", "Groupe E", "2026-06-25", "20:00", "Curaçao", "Côte d'Ivoire", True, 2),
    (56, "group", "Groupe E", "2026-06-25", "20:00", "Équateur", "Allemagne", True, 2),
    (57, "group", "Groupe F", "2026-06-25", "23:00", "Japon", "Suède", True, 2),
    (58, "group", "Groupe F", "2026-06-25", "23:00", "Tunisie", "Pays-Bas", True, 2),
    (59, "group", "Groupe D", "2026-06-26", "02:00", "Turquie", "États-Unis", True, 2),
    (60, "group", "Groupe D", "2026-06-26", "02:00", "Paraguay", "Australie", True, 2),
    (61, "group", "Groupe I", "2026-06-26", "19:00", "Norvège", "France", True, 2),
    (62, "group", "Groupe I", "2026-06-26", "19:00", "Sénégal", "Irak", True, 2),
    (63, "group", "Groupe G", "2026-06-27", "03:00", "Égypte", "Iran", True, 2),
    (64, "group", "Groupe G", "2026-06-27", "03:00", "Nouvelle-Zélande", "Belgique", True, 2),
    (65, "group", "Groupe H", "2026-06-27", "00:00", "Cap-Vert", "Arabie Saoudite", True, 2),
    (66, "group", "Groupe H", "2026-06-27", "00:00", "Uruguay", "Espagne", True, 2),
    (67, "group", "Groupe L", "2026-06-27", "21:00", "Panama", "Angleterre", True, 2),
    (68, "group", "Groupe L", "2026-06-27", "21:00", "Croatie", "Ghana", True, 2),
    (69, "group", "Groupe J", "2026-06-28", "02:00", "Algérie", "Autriche", True, 2),
    (70, "group", "Groupe J", "2026-06-28", "02:00", "Jordanie", "Argentine", True, 2),
    (71, "group", "Groupe K", "2026-06-27", "23:30", "Colombie", "Portugal", True, 2),
    (72, "group", "Groupe K", "2026-06-27", "23:30", "RD Congo", "Ouzbékistan", True, 2),
    (73, "round_of_32", None, "2026-06-28", "19:00", "2e Groupe A", "2e Groupe B", False, 2),
    (74, "round_of_32", None, "2026-06-29", "20:30", "1er Groupe E", "3e Groupe A/B/C/D/F", False, 2),
    (75, "round_of_32", None, "2026-06-30", "01:00", "1er Groupe F", "2e Groupe C", False, 2),
    (76, "round_of_32", None, "2026-06-29", "17:00", "1er Groupe C", "2e Groupe F", False, 2),
    (77, "round_of_32", None, "2026-06-30", "21:00", "1er Groupe I", "3e Groupe C/D/F/G/H", False, 2),
    (78, "round_of_32", None, "2026-06-30", "17:00", "2e Groupe E", "2e Groupe I", False, 2),
    (79, "round_of_32", None, "2026-07-01", "01:00", "1er Groupe A", "3e Groupe C/E/F/H/I", False, 2),
    (80, "round_of_32", None, "2026-07-01", "16:00", "1er Groupe L", "3e Groupe E/H/I/J/K", False, 2),
    (81, "round_of_32", None, "2026-07-02", "00:00", "1er Groupe D", "3e Groupe B/E/F/I/J", False, 2),
    (82, "round_of_32", None, "2026-07-01", "20:00", "1er Groupe G", "3e Groupe A/E/H/I/J", False, 2),
    (83, "round_of_32", None, "2026-07-02", "23:00", "2e Groupe K", "2e Groupe L", False, 2),
    (84, "round_of_32", None, "2026-07-02", "19:00", "1er Groupe H", "2e Groupe J", False, 2),
    (85, "round_of_32", None, "2026-07-03", "03:00", "1er Groupe B", "3e Groupe E/F/G/I/J", False, 2),
    (86, "round_of_32", None, "2026-07-03", "22:00", "1er Groupe J", "2e Groupe H", False, 2),
    (87, "round_of_32", None, "2026-07-04", "01:30", "1er Groupe K", "3e Groupe D/E/I/J/L", False, 2),
    (88, "round_of_32", None, "2026-07-03", "18:00", "2e Groupe D", "2e Groupe G", False, 2),
    (89, "round_of_16", None, "2026-07-04", "21:00", "Vainqueur M74", "Vainqueur M77", False, 2),
    (90, "round_of_16", None, "2026-07-04", "17:00", "Vainqueur M73", "Vainqueur M75", False, 2),
    (91, "round_of_16", None, "2026-07-05", "20:00", "Vainqueur M76", "Vainqueur M78", False, 2),
    (92, "round_of_16", None, "2026-07-06", "00:00", "Vainqueur M79", "Vainqueur M80", False, 2),
    (93, "round_of_16", None, "2026-07-06", "19:00", "Vainqueur M83", "Vainqueur M84", False, 2),
    (94, "round_of_16", None, "2026-07-07", "00:00", "Vainqueur M81", "Vainqueur M82", False, 2),
    (95, "round_of_16", None, "2026-07-07", "16:00", "Vainqueur M86", "Vainqueur M88", False, 2),
    (96, "round_of_16", None, "2026-07-07", "20:00", "Vainqueur M85", "Vainqueur M87", False, 2),
    (97, "quarter", None, "2026-07-09", "20:00", "Vainqueur M89", "Vainqueur M90", False, 2),
    (98, "quarter", None, "2026-07-10", "19:00", "Vainqueur M93", "Vainqueur M94", False, 2),
    (99, "quarter", None, "2026-07-11", "21:00", "Vainqueur M91", "Vainqueur M92", False, 2),
    (100, "quarter", None, "2026-07-12", "01:00", "Vainqueur M95", "Vainqueur M96", False, 2),
    (101, "semi", None, "2026-07-14", "19:00", "Vainqueur M97", "Vainqueur M98", False, 2),
    (102, "semi", None, "2026-07-15", "19:00", "Vainqueur M99", "Vainqueur M100", False, 2),
    (103, "third_place", None, "2026-07-18", "21:00", "Perdant M101", "Perdant M102", False, 2),
    (104, "final", None, "2026-07-19", "19:00", "Vainqueur M101", "Vainqueur M102", True, 2),
]


async def seed():
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute("SELECT COUNT(*) as cnt FROM matches")
        count = (await row.fetchone())["cnt"]
        if count > 0:
            print(f"Déjà {count} matchs en base. Utilisez --force pour réinitialiser.")
            if "--force" not in sys.argv:
                return
        await db.execute("DELETE FROM matches")
        await db.commit()

        for match in MATCHES:
            predictions_open = 1 if match[1] == "group" else 0
            await db.execute(
                """INSERT INTO matches
                   (match_number, phase, group_name, match_date, kickoff_time,
                    team1_name, team2_name, is_top_match, weight, predictions_open)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*match, predictions_open),
            )

        from app.knockout import ensure_knockout_slots
        await ensure_knockout_slots(db)
        await db.commit()
        row = await db.execute("SELECT COUNT(*) as cnt FROM matches")
        print(f"✓ {(await row.fetchone())['cnt']} matchs insérés.")

if __name__ == "__main__":
    asyncio.run(seed())
