"""Cagnotte et répartition des prix.

La cagnotte vaut ENTRY_FEE_EUR × nombre d'inscrits confirmés. Chaque prix
reçoit un pourcentage de la cagnotte ; les montants sont arrondis à la
dizaine d'euros en garantissant que la somme vaut exactement la cagnotte
(méthode du plus fort reste, par pas de 10 €).

La répartition est volontairement centralisée ici : ajuster les `pct`
suffit pour faire évoluer les prix (le total doit rester 100).
"""

ENTRY_FEE_EUR = 10
ROUND_STEP_EUR = 10

PRIZES = [
    {
        "key": "general_1",
        "label": "1er du classement général",
        "description": "Le grand vainqueur, tous points confondus.",
        "icon": "🥇",
        "pct": 30,
    },
    {
        "key": "general_2",
        "label": "2e du classement général",
        "description": "Deuxième au total des points.",
        "icon": "🥈",
        "pct": 18,
    },
    {
        "key": "general_3",
        "label": "3e du classement général",
        "description": "Troisième au total des points.",
        "icon": "🥉",
        "pct": 12,
    },
    {
        "key": "groups",
        "label": "Meilleur en phase de groupes",
        "description": "Meilleur pronostiqueur sur les matchs de groupes.",
        "icon": "🏟️",
        "pct": 10,
    },
    {
        "key": "knockout",
        "label": "Meilleur en phase finale",
        "description": "Meilleur score sur les matchs à élimination uniquement.",
        "icon": "⚔️",
        "pct": 10,
    },
    {
        "key": "bonus",
        "label": "Meilleur aux questions bonus",
        "description": "Meilleur cumul pré-tournoi + questions bonus.",
        "icon": "⭐",
        "pct": 10,
    },
    {
        "key": "remontada",
        "label": "Remontada",
        "description": "Plus belle progression entre la fin des groupes et le classement final.",
        "icon": "📈",
        "pct": 10,
    },
]

assert sum(p["pct"] for p in PRIZES) == 100


def compute_prizes(participant_count: int) -> dict:
    """Montants par prix pour un nombre d'inscrits donné.

    Retourne {"count", "pot", "entry_fee", "prizes": [{..., "amount"}]} avec
    des montants multiples de 10 € dont la somme vaut exactement la cagnotte.
    """
    count = max(int(participant_count or 0), 0)
    pot = count * ENTRY_FEE_EUR
    raw = [pot * p["pct"] / 100 for p in PRIZES]
    amounts = [int(r // ROUND_STEP_EUR) * ROUND_STEP_EUR for r in raw]
    leftover = pot - sum(amounts)
    # Plus fort reste d'abord ; à égalité, priorité à l'ordre des prix.
    by_remainder = sorted(
        range(len(PRIZES)),
        key=lambda i: (-(raw[i] - amounts[i]), i),
    )
    i = 0
    while leftover >= ROUND_STEP_EUR:
        amounts[by_remainder[i % len(by_remainder)]] += ROUND_STEP_EUR
        leftover -= ROUND_STEP_EUR
        i += 1
    return {
        "count": count,
        "pot": pot,
        "entry_fee": ENTRY_FEE_EUR,
        "prizes": [{**p, "amount": amount} for p, amount in zip(PRIZES, amounts)],
    }


async def get_prize_info(db) -> dict:
    """Cagnotte calculée sur les inscrits confirmés (hors admin)."""
    row = await db.execute(
        "SELECT COUNT(*) AS cnt FROM participants WHERE is_confirmed=1 AND is_admin=0"
    )
    count = (await row.fetchone())["cnt"]
    return compute_prizes(count)
