"""Drapeaux emoji des 48 équipes qualifiées (noms français du seed)."""

TEAM_FLAGS = {
    "Afrique du Sud": "🇿🇦",
    "Algérie": "🇩🇿",
    "Allemagne": "🇩🇪",
    "Angleterre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Arabie Saoudite": "🇸🇦",
    "Argentine": "🇦🇷",
    "Australie": "🇦🇺",
    "Autriche": "🇦🇹",
    "Belgique": "🇧🇪",
    "Bosnie-Herzégovine": "🇧🇦",
    "Brésil": "🇧🇷",
    "Canada": "🇨🇦",
    "Cap-Vert": "🇨🇻",
    "Colombie": "🇨🇴",
    "Corée du Sud": "🇰🇷",
    "Croatie": "🇭🇷",
    "Curaçao": "🇨🇼",
    "Côte d'Ivoire": "🇨🇮",
    "Espagne": "🇪🇸",
    "France": "🇫🇷",
    "Ghana": "🇬🇭",
    "Haïti": "🇭🇹",
    "Irak": "🇮🇶",
    "Iran": "🇮🇷",
    "Japon": "🇯🇵",
    "Jordanie": "🇯🇴",
    "Maroc": "🇲🇦",
    "Mexique": "🇲🇽",
    "Norvège": "🇳🇴",
    "Nouvelle-Zélande": "🇳🇿",
    "Ouzbékistan": "🇺🇿",
    "Panama": "🇵🇦",
    "Paraguay": "🇵🇾",
    "Pays-Bas": "🇳🇱",
    "Portugal": "🇵🇹",
    "Qatar": "🇶🇦",
    "RD Congo": "🇨🇩",
    "Suisse": "🇨🇭",
    "Suède": "🇸🇪",
    "Sénégal": "🇸🇳",
    "Tchéquie": "🇨🇿",
    "Tunisie": "🇹🇳",
    "Turquie": "🇹🇷",
    "Uruguay": "🇺🇾",
    "Écosse": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "Égypte": "🇪🇬",
    "Équateur": "🇪🇨",
    "États-Unis": "🇺🇸",
}


def team_flag(team_name: str) -> str:
    return TEAM_FLAGS.get((team_name or "").strip(), "")


def team_label(team_name: str) -> str:
    """« 🇧🇪 Belgique » — ou le nom inchangé pour les affiches non résolues."""
    name = (team_name or "").strip()
    flag = TEAM_FLAGS.get(name, "")
    return f"{flag} {name}" if flag else name
