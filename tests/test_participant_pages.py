import pytest


@pytest.mark.parametrize(
    "route",
    [
        "",
        "/pronos",
        "/bonus",
        "/pre-tournoi",
        "/classement",
        "/profil",
        "/reglement",
    ],
)
def test_participant_pages_render(client, participant, route):
    response = client.get(f"/p/{participant['token']}{route}")

    assert response.status_code == 200
    assert "Internal Server Error" not in response.text
