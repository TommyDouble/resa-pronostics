#!/usr/bin/env python3
"""Harnais de simulation des trophées (charte W8).

Pour chaque trophée du catalogue, recalcule l'attribution sur les données
RÉELLES de la base puis affiche le **nombre de détenteurs**, le **pourcentage**
du peloton et la **répartition par département**. C'est l'outil qui rend
vérifiable la règle de rareté « < 30 % de détenteurs » et le calibrage des
seuils (cf. app/trophies.py) avant de figer quoi que ce soit.

Usage :
    .venv/bin/python -m scripts.simulate_trophies            # base courante
    DATABASE_URL=/chemin/vers/copie.db .venv/bin/python -m scripts.simulate_trophies

N'ÉCRIT JAMAIS : la simulation se fait dans une transaction annulée (ROLLBACK),
la base n'est pas modifiée.
"""
import asyncio
from collections import defaultdict

from app.database import get_db
from app.trophies import TROPHIES, refresh_trophy_awards


async def _simulate() -> None:
    async with get_db() as db:
        # Population de référence (mêmes critères que le classement).
        prows = await db.execute(
            "SELECT id, department FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        dept_of = {
            r["id"]: (r["department"] or "").strip() or "Sans département"
            for r in await prows.fetchall()
        }
        field = len(dept_of)
        if not field:
            print("Aucun participant confirmé : rien à simuler.")
            return

        # Recalcule dans une transaction qu'on annulera (on lit l'état résultant
        # avant ROLLBACK pour ne rien persister).
        await refresh_trophy_awards(db)

        rows = await db.execute(
            "SELECT trophy_key, participant_id, detail FROM trophy_awards"
        )
        holders: dict[str, set] = defaultdict(set)
        occurrences: dict[str, int] = defaultdict(int)
        for r in await rows.fetchall():
            holders[r["trophy_key"]].add(r["participant_id"])
            occurrences[r["trophy_key"]] += 1

        await db.rollback()

    print(f"\nPeloton : {field} participants\n")
    header = f"{'Trophée':<22}{'Détenteurs':>11}{'%':>7}{'Occur.':>8}  Répartition par département"
    print(header)
    print("-" * len(header))
    for t in TROPHIES:
        hs = holders.get(t["key"], set())
        n = len(hs)
        pct = round(n / field * 100, 1) if field else 0.0
        by_dept = defaultdict(int)
        for pid in hs:
            by_dept[dept_of.get(pid, "?")] += 1
        spread = ", ".join(
            f"{d}:{c}" for d, c in sorted(by_dept.items(), key=lambda x: -x[1])
        ) or "—"
        flag = " ⚠️>30%" if pct > 30 and not t["repeatable"] else ""
        occ = occurrences.get(t["key"], 0)
        print(f"{t['label']:<22}{n:>11}{pct:>6}%{occ:>8}  {spread}{flag}")
    print()


if __name__ == "__main__":
    asyncio.run(_simulate())
