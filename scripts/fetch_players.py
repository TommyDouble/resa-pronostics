"""Fetch the 2026 FIFA World Cup squads from Wikipedia and write app/data/players.json.

Source: https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads (final squads,
submitted to FIFA on June 1, 2026). The MediaWiki API returns the page wikitext,
in which every player is a `{{nat fs g player|...}}` template — far more reliable
to parse than the rendered HTML.

Usage:
    python scripts/fetch_players.py            # fetch + write app/data/players.json
    python scripts/fetch_players.py --check    # fetch + print stats without writing
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = (
    "https://en.wikipedia.org/w/api.php?action=parse"
    "&page=2026_FIFA_World_Cup_squads&prop=wikitext&format=json&formatversion=2"
)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "players.json"

# Wikipedia (English) section title -> team name used in the app (French).
TEAM_NAMES_EN_FR = {
    "Mexico": "Mexique",
    "South Africa": "Afrique du Sud",
    "South Korea": "Corée du Sud",
    "Czech Republic": "Tchéquie",
    "Canada": "Canada",
    "Bosnia and Herzegovina": "Bosnie-Herzégovine",
    "Qatar": "Qatar",
    "Switzerland": "Suisse",
    "Brazil": "Brésil",
    "Morocco": "Maroc",
    "Haiti": "Haïti",
    "Scotland": "Écosse",
    "United States": "États-Unis",
    "Paraguay": "Paraguay",
    "Australia": "Australie",
    "Turkey": "Turquie",
    "Germany": "Allemagne",
    "Curaçao": "Curaçao",
    "Ivory Coast": "Côte d'Ivoire",
    "Ecuador": "Équateur",
    "Netherlands": "Pays-Bas",
    "Japan": "Japon",
    "Sweden": "Suède",
    "Tunisia": "Tunisie",
    "Belgium": "Belgique",
    "Egypt": "Égypte",
    "Iran": "Iran",
    "New Zealand": "Nouvelle-Zélande",
    "Spain": "Espagne",
    "Cape Verde": "Cap-Vert",
    "Saudi Arabia": "Arabie Saoudite",
    "Uruguay": "Uruguay",
    "France": "France",
    "Senegal": "Sénégal",
    "Iraq": "Irak",
    "Norway": "Norvège",
    "Argentina": "Argentine",
    "Algeria": "Algérie",
    "Austria": "Autriche",
    "Jordan": "Jordanie",
    "Portugal": "Portugal",
    "DR Congo": "RD Congo",
    "Uzbekistan": "Ouzbékistan",
    "Colombia": "Colombie",
    "England": "Angleterre",
    "Croatia": "Croatie",
    "Ghana": "Ghana",
    "Panama": "Panama",
}

SECTION_RE = re.compile(r"^===([^=].*?)===\s*$", re.M)
PLAYER_START_RE = re.compile(r"\{\{nat fs g player\s*\|", re.I)


def iter_player_template_bodies(text: str):
    """Yield the body of each player template, respecting nested {{...}} templates."""
    for match in PLAYER_START_RE.finditer(text):
        start = match.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i : i + 2] == "{{":
                depth += 1
                i += 2
            elif text[i : i + 2] == "}}":
                depth -= 1
                i += 2
            else:
                i += 1
        if depth == 0:
            yield text[start : i - 2]


def fetch_wikitext() -> str:
    request = urllib.request.Request(
        API_URL, headers={"User-Agent": "resa-pronostics/1.0 (squad importer)"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    return data["parse"]["wikitext"]


def clean_link(value: str) -> str:
    """Resolve [[Target|Label]] / [[Target]] wikilinks to plain text."""
    value = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", value)
    return value.strip()


def parse_template_fields(body: str) -> dict:
    """Split a template body on top-level pipes (nested {{...}} and [[...]] kept intact)."""
    fields = {}
    depth_braces = depth_brackets = 0
    current = []
    parts = []
    for i, char in enumerate(body):
        if body[i : i + 2] == "{{":
            depth_braces += 1
        elif body[i : i + 2] == "}}" and depth_braces:
            depth_braces -= 1
        elif body[i : i + 2] == "[[":
            depth_brackets += 1
        elif body[i : i + 2] == "]]" and depth_brackets:
            depth_brackets -= 1
        if char == "|" and depth_braces == 0 and depth_brackets == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    for part in parts:
        if "=" in part:
            key, _, val = part.partition("=")
            fields[key.strip()] = val.strip()
    return fields


def parse_players(wikitext: str) -> list[dict]:
    sections = SECTION_RE.split(wikitext)
    players = []
    # sections = [preamble, title1, body1, title2, body2, ...]
    for title, body in zip(sections[1::2], sections[2::2]):
        team_fr = TEAM_NAMES_EN_FR.get(title.strip())
        if not team_fr:
            continue
        for template_body in iter_player_template_bodies(body):
            fields = parse_template_fields(template_body)
            name = clean_link(fields.get("name", ""))
            if not name:
                continue
            players.append({
                "name": name,
                "team": team_fr,
                "position": fields.get("pos", "").strip(),
                "club": clean_link(fields.get("club", "")),
            })
    return players


def main():
    check_only = "--check" in sys.argv
    wikitext = fetch_wikitext()
    players = parse_players(wikitext)

    teams = {}
    for player in players:
        teams.setdefault(player["team"], []).append(player)
    missing = set(TEAM_NAMES_EN_FR.values()) - set(teams)
    print(f"{len(players)} joueurs, {len(teams)} équipes")
    for team in sorted(teams):
        count = len(teams[team])
        flag = "" if 23 <= count <= 26 else "  ⚠️ effectif inhabituel"
        print(f"  {team}: {count}{flag}")
    if missing:
        print(f"⚠️ équipes manquantes: {sorted(missing)}")
    if len(players) < 48 * 23:
        print("⚠️ total inférieur au minimum attendu (48 × 23)")

    if not check_only:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(
            json.dumps(players, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"Écrit: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
