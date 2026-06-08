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

GROUPS = {
    "A": ["Mexique", "Afrique du Sud", "Corée du Sud", "Tchéquie"],
    "B": ["Canada", "Bosnie-Herzégovine", "Qatar", "Suisse"],
    "C": ["Brésil", "Maroc", "Haïti", "Écosse"],
    "D": ["États-Unis", "Paraguay", "Australie", "Turquie"],
    "E": ["Allemagne", "Curaçao", "Côte d'Ivoire", "Équateur"],
    "F": ["Pays-Bas", "Japon", "Suède", "Tunisie"],
    "G": ["Belgique", "Égypte", "Iran", "Nouvelle-Zélande"],
    "H": ["Espagne", "Cap-Vert", "Arabie Saoudite", "Uruguay"],
    "I": ["France", "Sénégal", "Irak", "Norvège"],
    "J": ["Argentine", "Algérie", "Autriche", "Jordanie"],
    "K": ["Portugal", "RD Congo", "Ouzbékistan", "Colombie"],
    "L": ["Angleterre", "Croatie", "Ghana", "Panama"],
}

# Ordre : T0vsT1, T2vsT3 (J1) | T0vsT2, T1vsT3 (J2) | T0vsT3, T1vsT2 (J3 Top Match)
MATCHUPS = [(0,1),(2,3),(0,2),(1,3),(0,3),(1,2)]

# 6 slots (date, heure UTC) par groupe : J1m1, J1m2, J2m1, J2m2, J3m1, J3m2
GROUP_SCHEDULE = {
    "A": [("2026-06-11","19:00"),("2026-06-11","22:00"),("2026-06-18","16:00"),("2026-06-19","01:00"),("2026-06-25","18:00"),("2026-06-25","18:00")],
    "B": [("2026-06-12","19:00"),("2026-06-13","19:00"),("2026-06-19","19:00"),("2026-06-19","23:00"),("2026-06-25","22:00"),("2026-06-25","22:00")],
    "C": [("2026-06-13","22:00"),("2026-06-14","01:00"),("2026-06-20","18:00"),("2026-06-20","22:00"),("2026-06-25","22:00"),("2026-06-25","22:00")],
    "D": [("2026-06-13","01:00"),("2026-06-14","04:00"),("2026-06-21","00:00"),("2026-06-21","04:00"),("2026-06-26","18:00"),("2026-06-26","18:00")],
    "E": [("2026-06-14","17:00"),("2026-06-14","23:00"),("2026-06-20","17:00"),("2026-06-21","01:00"),("2026-06-26","22:00"),("2026-06-26","22:00")],
    "F": [("2026-06-14","20:00"),("2026-06-15","02:00"),("2026-06-21","17:00"),("2026-06-21","20:00"),("2026-06-26","22:00"),("2026-06-26","22:00")],
    "G": [("2026-06-15","22:00"),("2026-06-16","04:00"),("2026-06-21","19:00"),("2026-06-22","01:00"),("2026-06-27","18:00"),("2026-06-27","18:00")],
    "H": [("2026-06-15","17:00"),("2026-06-15","22:00"),("2026-06-21","16:00"),("2026-06-21","22:00"),("2026-06-27","18:00"),("2026-06-27","18:00")],
    "I": [("2026-06-16","19:00"),("2026-06-16","22:00"),("2026-06-22","21:00"),("2026-06-23","00:00"),("2026-06-26","22:00"),("2026-06-26","22:00")],
    "J": [("2026-06-17","01:00"),("2026-06-17","04:00"),("2026-06-22","17:00"),("2026-06-23","03:00"),("2026-06-27","22:00"),("2026-06-27","22:00")],
    "K": [("2026-06-17","17:00"),("2026-06-18","02:00"),("2026-06-23","17:00"),("2026-06-24","01:00"),("2026-06-27","22:00"),("2026-06-27","22:00")],
    "L": [("2026-06-17","20:00"),("2026-06-17","23:00"),("2026-06-23","01:00"),("2026-06-24","01:00"),("2026-06-28","18:00"),("2026-06-28","18:00")],
}

R32_DATES = [
    ("2026-06-28","22:00"),("2026-06-29","18:00"),("2026-06-29","22:00"),("2026-06-30","18:00"),
    ("2026-06-30","22:00"),("2026-07-01","18:00"),("2026-07-01","22:00"),("2026-07-02","18:00"),
    ("2026-07-02","22:00"),("2026-07-03","18:00"),("2026-07-03","22:00"),("2026-07-04","18:00"),
    ("2026-07-04","22:00"),("2026-07-05","18:00"),("2026-07-05","22:00"),("2026-07-06","18:00"),
]
R16_DATES = [
    ("2026-07-06","22:00"),("2026-07-07","18:00"),("2026-07-07","22:00"),("2026-07-08","18:00"),
    ("2026-07-08","22:00"),("2026-07-09","18:00"),("2026-07-09","22:00"),("2026-07-10","18:00"),
]
QF_DATES = [("2026-07-11","18:00"),("2026-07-11","22:00"),("2026-07-12","18:00"),("2026-07-12","22:00")]
SF_DATES = [("2026-07-14","21:00"),("2026-07-15","21:00")]


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

        n = 1
        for grp, teams in GROUPS.items():
            sched = GROUP_SCHEDULE[grp]
            for i, (t1i, t2i) in enumerate(MATCHUPS):
                date, time = sched[i]
                is_j3 = (i >= 4)
                await db.execute(
                    "INSERT INTO matches (match_number,phase,group_name,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?,?)",
                    (n,"group",f"Groupe {grp}",date,time,teams[t1i],teams[t2i],1 if is_j3 else 0,2 if is_j3 else 1)
                )
                n += 1

        for i,(date,time) in enumerate(R32_DATES):
            await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
                (n,"round_of_32",date,time,f"1/32 {i+1}A",f"1/32 {i+1}B",0,2)); n+=1

        for i,(date,time) in enumerate(R16_DATES):
            await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
                (n,"round_of_16",date,time,f"1/8 {i+1}A",f"1/8 {i+1}B",0,2)); n+=1

        for i,(date,time) in enumerate(QF_DATES):
            await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
                (n,"quarter",date,time,f"QF {i+1}A",f"QF {i+1}B",0,2)); n+=1

        for i,(date,time) in enumerate(SF_DATES):
            await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
                (n,"semi",date,time,f"SF {i+1}A",f"SF {i+1}B",0,2)); n+=1

        await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
            (n,"third_place","2026-07-17","21:00","3e place A","3e place B",0,2)); n+=1

        await db.execute("INSERT INTO matches (match_number,phase,match_date,kickoff_time,team1_name,team2_name,is_top_match,weight) VALUES (?,?,?,?,?,?,?,?)",
            (n,"final","2026-07-19","19:00","Finaliste A","Finaliste B",1,2))

        await db.commit()
        row = await db.execute("SELECT COUNT(*) as cnt FROM matches")
        print(f"✓ {(await row.fetchone())['cnt']} matchs insérés.")

if __name__ == "__main__":
    asyncio.run(seed())
