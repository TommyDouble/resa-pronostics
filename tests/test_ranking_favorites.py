"""Favoris : départage des ex æquo dans les classements (étoile admin)."""
import uuid

from app.database import get_db
from app.scoring import get_rankings
from tests.conftest import run

# Valeur distinctive (assertions relatives uniquement) : assez haute pour ne
# pas croiser les petits scores des autres tests, assez basse pour ne pas
# perturber ceux qui vérifient des rangs absolus en tête de classement.
BIG = 4242


def _make_participant(name, points, match_id):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, f"{token}@test.local", token),
            )
            pid = cursor.lastrowid
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, match_id, points),
            )
            await db.commit()
            return pid

    return run(_create())


def _make_match(number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', '2000-01-01', '12:00', 'France', 'Brésil', 1)""",
                (number,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _rank_entries(ids):
    async def _q():
        async with get_db() as db:
            rankings = await get_rankings(db)
        return {r["id"]: (rankings.index(r), r["rank"]) for r in rankings if r["id"] in ids}

    return run(_q())


def test_favorite_wins_ties_without_changing_rank(client, admin_client):
    mid = _make_match(940001)
    aaa = _make_participant("Aaa Égalité", BIG, mid)
    zzz = _make_participant("Zzz Égalité", BIG, mid)

    entries = _rank_entries({aaa, zzz})
    # Sans favori : ordre alphabétique, même rang.
    assert entries[aaa][0] < entries[zzz][0]
    assert entries[aaa][1] == entries[zzz][1]

    admin_client.post(f"/admin/participants/{zzz}/toggle-favorite")
    entries = _rank_entries({aaa, zzz})
    # Le favori passe devant, le numéro de rang ne bouge pas.
    assert entries[zzz][0] < entries[aaa][0]
    assert entries[aaa][1] == entries[zzz][1]


def test_favorite_does_not_beat_more_points(client, admin_client):
    mid = _make_match(940002)
    leader = _make_participant("Leader Points", BIG + 10, mid)
    starred = _make_participant("Starred Points", BIG + 5, mid)
    admin_client.post(f"/admin/participants/{starred}/toggle-favorite")

    entries = _rank_entries({leader, starred})
    assert entries[leader][0] < entries[starred][0]
    assert entries[leader][1] < entries[starred][1]


def test_toggle_favorite_returns_json(admin_client):
    mid = _make_match(940003)
    pid = _make_participant("Toggle Favori", 1, mid)
    r = admin_client.post(f"/admin/participants/{pid}/toggle-favorite")
    assert r.status_code == 200
    assert r.json() == {"is_favorite": 1}
    r = admin_client.post(f"/admin/participants/{pid}/toggle-favorite")
    assert r.json() == {"is_favorite": 0}
    assert admin_client.post("/admin/participants/999999/toggle-favorite").status_code == 404
