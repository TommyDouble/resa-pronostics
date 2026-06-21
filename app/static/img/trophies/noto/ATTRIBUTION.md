# Icônes de trophées — Noto Emoji (vendorisées)

Les icônes de ce dossier sont des assets **Noto Emoji** de Google, servis
**localement** (aucun CDN au runtime). Seuls les 15 glyphes utilisés par le
cabinet à trophées sont vendorisés — pas l'intégralité de Noto.

- Source : https://github.com/googlefonts/noto-emoji (dossier `svg/`)
- Licence : **Apache License 2.0** (cf. `LICENSE-APACHE-2.0.txt` dans ce dossier)
- Récupérés depuis la branche `main` du dépôt.

## Correspondance `icon_key` → fichier source Noto (codepoint)

| Fichier local        | icon_key         | Emoji Noto source         | Codepoint |
|----------------------|------------------|---------------------------|-----------|
| `target.svg`         | target           | 🎯 direct hit             | U+1F3AF   |
| `crystal-ball.svg`   | crystal-ball     | 🔮 crystal ball           | U+1F52E   |
| `trophy.svg`         | trophy           | 🏆 trophy                 | U+1F3C6   |
| `alien.svg`          | alien            | 👽 alien                  | U+1F47D   |
| `castle.svg`         | castle           | 🏰 castle                 | U+1F3F0   |
| `calendar-check.svg` | calendar-check   | 📅 calendar (+ overlay)   | U+1F4C5   |
| `flame.svg`          | flame            | 🔥 fire                   | U+1F525   |
| `snowflake.svg`      | snowflake        | ❄️ snowflake              | U+2744    |
| `sword.svg`          | sword            | 🗡️ dagger                 | U+1F5E1   |
| `scales.svg`         | scales           | ⚖️ balance scale          | U+2696    |
| `mirror.svg`         | mirror           | 🪞 mirror                 | U+1FA9E   |
| `rocket.svg`         | rocket           | 🚀 rocket                 | U+1F680   |
| `heart-broken.svg`   | heart-broken     | 💔 broken heart           | U+1F494   |
| `spoon.svg`          | spoon            | 🥄 spoon                  | U+1F944   |
| `flask.svg`          | flask            | 🧪 test tube              | U+1F9EA   |

Notes :
- `calendar-check` réutilise l'asset **calendrier** Noto ; le petit check vert est
  ajouté en overlay propre côté template (macro `trophy_icon`), pas dans l'asset.
- `sword` utilise la **dague** (🗡️) — la plus lisible dans un badge.
- `flask` utilise le **tube à essai** (🧪) — le plus fun pour « Le Savant Fou ».
