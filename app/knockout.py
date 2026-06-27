"""Helpers for the knockout bracket and per-match prediction opening."""
import re


SIDES = ("team1", "team2")
SIDE_COLUMNS = {"team1": "team1_name", "team2": "team2_name"}
_SOURCE_RE = re.compile(r"^(Vainqueur|Perdant)\s+M(\d+)$", re.IGNORECASE)


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def is_knockout(match: dict) -> bool:
    return _row_get(match, "phase") != "group"


def match_predictions_open(match: dict) -> bool:
    """Whether participants may submit a prediction for this match."""
    if not is_knockout(match):
        return True
    return int(_row_get(match, "predictions_open", 0) or 0) == 1


def is_placeholder_team(label: str | None) -> bool:
    raw = (label or "").strip()
    if not raw:
        return True
    if _SOURCE_RE.match(raw):
        return True
    return any(token in raw for token in ("Groupe", "Équipe", "Equipe", "TBD"))


def _slot_from_label(label: str | None) -> dict:
    raw = (label or "").strip()
    match = _SOURCE_RE.match(raw)
    if match:
        return {
            "source_kind": "match",
            "source_label": raw,
            "source_match_number": int(match.group(2)),
            "source_outcome": "winner" if match.group(1).lower().startswith("vain") else "loser",
            "is_confirmed": 0,
        }
    return {
        "source_kind": "manual",
        "source_label": raw,
        "source_match_number": None,
        "source_outcome": None,
        "is_confirmed": 0 if is_placeholder_team(raw) else 1,
    }


async def ensure_knockout_slots(db) -> None:
    """Create missing slot metadata from the seeded knockout labels."""
    existing_rows = await db.execute("SELECT match_id, side FROM knockout_slots")
    existing = {(r["match_id"], r["side"]) for r in await existing_rows.fetchall()}
    rows = await db.execute(
        "SELECT id, phase, team1_name, team2_name FROM matches WHERE phase != 'group'"
    )
    for match in await rows.fetchall():
        for side in SIDES:
            if (match["id"], side) in existing:
                continue
            meta = _slot_from_label(match[SIDE_COLUMNS[side]])
            await db.execute(
                """INSERT INTO knockout_slots
                   (match_id, side, source_kind, source_label, source_match_number,
                    source_outcome, is_confirmed)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    match["id"],
                    side,
                    meta["source_kind"],
                    meta["source_label"],
                    meta["source_match_number"],
                    meta["source_outcome"],
                    meta["is_confirmed"],
                ),
            )


async def knockout_match_ready(db, match_id: int) -> bool:
    rows = await db.execute(
        "SELECT is_confirmed FROM knockout_slots WHERE match_id=?", (match_id,)
    )
    slots = await rows.fetchall()
    return len(slots) == 2 and all(int(s["is_confirmed"] or 0) == 1 for s in slots)


async def set_match_predictions_open(db, match_id: int, open_value: bool) -> tuple[bool, str]:
    row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    match = await row.fetchone()
    if not match:
        return False, "Match introuvable."
    if match["phase"] == "group":
        await db.execute("UPDATE matches SET predictions_open=1 WHERE id=?", (match_id,))
        return True, "Les matchs de groupes restent ouverts jusqu'au coup d'envoi."
    if open_value and not await knockout_match_ready(db, match_id):
        return False, "Confirme les deux équipes avant d'ouvrir les pronostics."
    await db.execute(
        "UPDATE matches SET predictions_open=? WHERE id=?",
        (1 if open_value else 0, match_id),
    )
    return True, "Pronostics ouverts." if open_value else "Pronostics fermés."


async def confirm_match_teams(db, match_id: int, team1_name: str, team2_name: str) -> tuple[bool, str]:
    team1_name = (team1_name or "").strip()
    team2_name = (team2_name or "").strip()
    if not team1_name or not team2_name:
        return False, "Les deux équipes sont requises."
    if team1_name == team2_name:
        return False, "Les deux équipes doivent être différentes."
    if is_placeholder_team(team1_name) or is_placeholder_team(team2_name):
        return False, "Choisis deux équipes qualifiées réelles."

    row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    match = await row.fetchone()
    if not match:
        return False, "Match introuvable."
    if match["phase"] == "group":
        return False, "Les équipes de groupes ne se confirment pas ici."
    if match["result"] is not None:
        return False, "Impossible de modifier une affiche déjà jouée."

    await db.execute(
        "UPDATE matches SET team1_name=?, team2_name=?, predictions_open=1 WHERE id=?",
        (team1_name, team2_name, match_id),
    )
    for side, team in (("team1", team1_name), ("team2", team2_name)):
        await db.execute(
            """INSERT INTO knockout_slots
               (match_id, side, source_kind, source_label, is_confirmed)
               VALUES (?, ?, 'manual', 'Saisie admin', 1)
               ON CONFLICT(match_id, side) DO UPDATE SET
                 source_kind='manual',
                 source_label='Saisie admin',
                 source_match_number=NULL,
                 source_outcome=NULL,
                 is_confirmed=1,
                 updated_at=datetime('now')""",
            (match_id, side),
        )
    return True, "Affiche confirmée et pronostics ouverts."


def _qualified_side(match: dict) -> str:
    if _row_get(match, "phase") == "group":
        return ""
    result = _row_get(match, "result")
    if result in ("team1", "team2"):
        return result
    return _row_get(match, "qualifier_winner") or ""


def _opposite_side(side: str) -> str:
    return "team2" if side == "team1" else "team1"


async def _prediction_count(db, match_id: int) -> int:
    row = await db.execute(
        "SELECT COUNT(*) AS cnt FROM predictions WHERE match_id=?", (match_id,)
    )
    return (await row.fetchone())["cnt"]


async def _open_if_ready(db, match_id: int) -> None:
    row = await db.execute("SELECT result FROM matches WHERE id=?", (match_id,))
    match = await row.fetchone()
    if match and match["result"] is None and await knockout_match_ready(db, match_id):
        await db.execute("UPDATE matches SET predictions_open=1 WHERE id=?", (match_id,))


async def propagate_from_match(db, match_id: int) -> dict:
    """Propagate a knockout result to downstream bracket slots.

    If a downstream fixture already has predictions and the propagated team would
    change the visible fixture, the fixture is closed and left untouched.
    """
    await ensure_knockout_slots(db)
    row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
    source = await row.fetchone()
    if not source or source["phase"] == "group" or source["result"] is None:
        return {"updates": [], "conflicts": []}

    winner_side = _qualified_side(dict(source))
    if winner_side not in SIDES:
        return {"updates": [], "conflicts": []}
    names = {
        "winner": source[SIDE_COLUMNS[winner_side]],
        "loser": source[SIDE_COLUMNS[_opposite_side(winner_side)]],
    }
    refs = await db.execute(
        """SELECT
             ks.id AS slot_id, ks.match_id AS target_id, ks.side, ks.source_outcome,
             ks.is_confirmed,
             target.match_number AS target_match_number,
             target.team1_name AS target_team1_name,
             target.team2_name AS target_team2_name,
             target.result AS target_result
           FROM knockout_slots ks
           JOIN matches target ON target.id=ks.match_id
           WHERE ks.source_kind='match' AND ks.source_match_number=?
           ORDER BY target.match_number, ks.side""",
        (source["match_number"],),
    )
    updates = []
    conflicts = []
    for slot in await refs.fetchall():
        desired = names.get(slot["source_outcome"])
        if not desired:
            continue
        current = slot[f"target_{SIDE_COLUMNS[slot['side']]}"]
        predictions = await _prediction_count(db, slot["target_id"])
        would_change_confirmed_fixture = current != desired and predictions > 0
        if slot["target_result"] is not None or would_change_confirmed_fixture:
            await db.execute(
                "UPDATE matches SET predictions_open=0 WHERE id=?",
                (slot["target_id"],),
            )
            await db.execute(
                """UPDATE knockout_slots
                   SET is_confirmed=0, updated_at=datetime('now')
                   WHERE id=?""",
                (slot["slot_id"],),
            )
            conflicts.append({
                "match_number": slot["target_match_number"],
                "side": slot["side"],
                "current": current,
                "expected": desired,
                "predictions": predictions,
            })
            continue
        if current != desired:
            await db.execute(
                f"UPDATE matches SET {SIDE_COLUMNS[slot['side']]}=? WHERE id=?",
                (desired, slot["target_id"]),
            )
        await db.execute(
            """UPDATE knockout_slots
               SET is_confirmed=1, updated_at=datetime('now')
               WHERE id=?""",
            (slot["slot_id"],),
        )
        await _open_if_ready(db, slot["target_id"])
        updates.append({
            "match_number": slot["target_match_number"],
            "side": slot["side"],
            "team": desired,
        })
    return {"updates": updates, "conflicts": conflicts}


async def enrich_knockout_matches(db, matches: list[dict]) -> None:
    """Add bracket/admin status fields to match dicts in-place."""
    match_ids = [m["id"] for m in matches if m.get("phase") != "group"]
    slots_by_match = {mid: [] for mid in match_ids}
    prediction_counts = {mid: 0 for mid in match_ids}
    if match_ids:
        placeholders = ",".join("?" for _ in match_ids)
        rows = await db.execute(
            f"SELECT * FROM knockout_slots WHERE match_id IN ({placeholders})",
            match_ids,
        )
        for slot in await rows.fetchall():
            slots_by_match.setdefault(slot["match_id"], []).append(dict(slot))
        pred_rows = await db.execute(
            f"""SELECT match_id, COUNT(*) AS cnt FROM predictions
                WHERE match_id IN ({placeholders}) GROUP BY match_id""",
            match_ids,
        )
        prediction_counts.update({
            r["match_id"]: r["cnt"] for r in await pred_rows.fetchall()
        })

    for match in matches:
        if match.get("phase") == "group":
            match["bracket_ready"] = True
            match["predictions_open_effective"] = True
            match["bracket_status"] = "group"
            match["bracket_status_label"] = "Groupes"
            match["prediction_count"] = 0
            continue
        slots = slots_by_match.get(match["id"], [])
        ready = len(slots) == 2 and all(int(s.get("is_confirmed") or 0) == 1 for s in slots)
        open_effective = match_predictions_open(match)
        match["knockout_slots"] = slots
        match["bracket_ready"] = ready
        match["predictions_open_effective"] = open_effective
        match["prediction_count"] = prediction_counts.get(match["id"], 0)
        if match.get("result") is not None:
            key, label = "played", "joué"
        elif open_effective:
            key, label = "open", "ouvert"
        elif ready:
            key, label = "ready", "prêt fermé"
        else:
            key, label = "pending", "à confirmer"
        match["bracket_status"] = key
        match["bracket_status_label"] = label
