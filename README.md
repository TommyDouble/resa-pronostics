# RESA Pronostics 2026

Application de pronostics pour la Coupe du Monde 2026 (FastAPI + SQLite + Jinja).
Les participants accèdent à l'app via un lien personnel à token (pas de mot de passe) ;
l'organisateur gère tout depuis un backoffice `/admin`.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_matches.py      # les 104 matchs du tournoi
python scripts/create_admin.py      # compte admin du backoffice
uvicorn app.main:app --reload
```

- Participant : `http://localhost:8000/p/<token>` (token généré à l'ajout d'un participant)
- Admin : `http://localhost:8000/admin`
- Inscription publique : `http://localhost:8000/rejoindre`

## Fonctionnement

### Pronostics de matchs
Score exact par match ; l'issue (1/X/2) est déduite. Bonne issue = +2 × poids
(top match et phase finale ×2), score exact = +2. Verrouillage au coup d'envoi.

### Pré-tournoi (5 questions fixes)
Vainqueur (+8), finaliste (+5), meilleur buteur (+5), révélation (+5),
total de buts en phase de groupes (+8 exact, +4 à ±3). Vainqueur et finaliste
doivent être différents (validation client + serveur). L'admin encode les
réponses correctes dans `/admin/pre-tournoi` au fil du tournoi ; chaque
enregistrement recalcule les points de tous les participants.

### Questions bonus
Questions libres créées par l'admin (choix / nombre / texte) avec deadline et
points. La réponse correcte (select pour les questions à choix) déclenche le
recalcul. Comparaison tolérante : casse/espaces ignorés pour le texte,
`10`, `10.0` et `10,0` équivalents pour le numérique.

## Données joueurs

`app/data/players.json` contient les 1246 joueurs des 48 sélections finales
(listes FIFA du 1er juin 2026), utilisés pour le choix du meilleur buteur.
Source : page Wikipedia « 2026 FIFA World Cup squads ». Pour regénérer :

```bash
python scripts/fetch_players.py            # met à jour app/data/players.json
python scripts/fetch_players.py --check    # stats sans écrire
```

Les valeurs stockées sont au format canonique `Nom (Équipe)` pour lever toute
ambiguïté entre homonymes.

## Tests

```bash
pip install pytest httpx
python -m pytest tests/
```

## Déploiement

Conçu pour Railway (`railway.toml`, `Procfile`, Python 3.12). Variables :
`SECRET_KEY` (obligatoire en prod), `DATABASE_URL`, `BASE_URL`, et la config
email (`SMTP_*` ou `EMAIL_WEBHOOK_*`). Voir `.env.example`.

## Design d'origine

Le dossier `project/` et `chats/` contiennent le bundle de design Claude Design
(wireframes + hi-fi) qui a servi de référence pour l'implémentation.
