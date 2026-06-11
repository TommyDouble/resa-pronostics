"""Participant name normalization."""
import uuid

import pytest

from app.database import _normalize_existing_participant_names, get_db
from app.nameutils import build_full_name, normalize_name_part
from tests.conftest import run


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" jean   pierre ", "Jean Pierre"),
        ("anne-sophie", "Anne-Sophie"),
        ("DUPONT", "Dupont"),
        ("d'angelo", "D'Angelo"),
        ("o’neil", "O’Neil"),
    ],
)
def test_normalize_name_part_handles_compound_names(raw, expected):
    assert normalize_name_part(raw) == expected


def test_build_full_name_normalizes_first_and_last_name():
    assert build_full_name(" anne-sophie ", "DUPONT") == (
        "Anne-Sophie",
        "Dupont",
        "Anne-Sophie Dupont",
    )


def test_existing_participant_name_cleanup_is_idempotent(client):
    token = str(uuid.uuid4())
    email = f"{token}@test.local"

    async def _create_dirty_participant():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                ("jean-pierre D'ANGELO", "", "", email, token),
            )
            await db.commit()
            return cursor.lastrowid

    participant_id = run(_create_dirty_participant())

    async def _cleanup_and_get():
        async with get_db() as db:
            await _normalize_existing_participant_names(db)
            await _normalize_existing_participant_names(db)
            await db.commit()
            row = await db.execute(
                "SELECT name, first_name, last_name FROM participants WHERE id=?",
                (participant_id,),
            )
            return dict(await row.fetchone())

    assert run(_cleanup_and_get()) == {
        "name": "Jean-Pierre D'Angelo",
        "first_name": "Jean-Pierre",
        "last_name": "D'Angelo",
    }
