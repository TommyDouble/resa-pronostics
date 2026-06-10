# RESA Pronostics 2026

Application de pronostics pour la Coupe du Monde 2026 (FastAPI + SQLite + Jinja).
Les participants se connectent par email + mot de passe depuis la page d'accueil,
ou via leur lien personnel à token (les deux fonctionnent) ; l'organisateur gère
tout depuis un backoffice `/admin`.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_matches.py      # les 104 matchs du tournoi
python scripts/create_admin.py      # compte admin du backoffice
uvicorn app.main:app --reload
```

- Connexion participant : `http://localhost:8000/` (email + mot de passe)
- Inscription publique : `http://localhost:8000/rejoindre` (prénom, nom, email,
  département RESA, mot de passe)
- Lien direct participant : `http://localhost:8000/p/<token>` (fonctionne toujours,
  avec ou sans mot de passe ; les invités peuvent créer leur mot de passe depuis
  leur profil)
- Lien perdu : `http://localhost:8000/lien-perdu` (renvoie le lien personnel par email)
- Admin : `http://localhost:8000/admin`

L'app est installable sur l'écran d'accueil (PWA : manifest, icônes,
service worker pour les notifications).

Le département RESA apparaît sur le profil et servira au futur classement
inter-départements.

## Fonctionnement

### Pronostics de matchs
Score exact par match ; l'issue (1/X/2) est déduite. Bon résultat = +2 × poids
(top match et phase finale ×2), score exact = +2. Verrouillage au coup d'envoi.
En phase finale, les points portent sur le vainqueur/qualifié du match. Si le
score pronostiqué est nul, le participant doit choisir l'équipe qualifiée. Le
bonus score exact n'est accordé que si le vainqueur/qualifié pronostiqué est
correct.

Les pronostics de **phase finale sont verrouillés** tant que l'organisateur ne
les ouvre pas (toggle dans `/admin/matches`, à activer quand les affiches se
précisent).

### Pré-tournoi (5 questions fixes)
Champion du Monde (+8), finalistes (+7 par finaliste correct, le champion
pronostiqué compte comme un des deux finalistes), meilleur buteur (+8),
révélation (+5), total de buts en phase de groupes (+8 exact, +4 à ±3).
Champion et autre finaliste doivent être différents (validation client +
serveur). **Toute réponse enregistrée compte** (plus de notion de brouillon),
modifiable jusqu'à la deadline. L'admin encode les réponses correctes dans
`/admin/pre-tournoi` au fil du tournoi ; chaque enregistrement recalcule les
points de tous les participants.

### Questions bonus
Questions **à choix unique** créées par l'admin (≥ 2 options) avec deadline et
points, pour toutes les phases (seizièmes → finale). La réponse correcte
déclenche le recalcul.

### Classements
Six vues : général, phase de groupes, phase finale, bonus, remontada
(progression depuis la fin des groupes) et départements (moyenne par inscrit).
Le rang dépend uniquement du total de points ; à égalité, les participants
restent ex æquo. Des snapshots quotidiens alimentent les flèches d'évolution.

### Cagnotte
10 € × inscrits confirmés, répartie en pourcentages par prix
(`app/prizes.py`), montants arrondis à la dizaine avec total exact garanti,
affichés sur le classement et le règlement.

### Rappels automatiques (plan hybride)
Une boucle de fond (`app/scheduler.py`, `SCHEDULER_ENABLED`) envoie : rappel
J-1 des matchs non pronostiqués, rappels deadline pré-tournoi/bonus, récap
quotidien (+points, rang, top 3). Canal : **notification push** si le
participant a activé les notifications (PWA installée), **email sinon**
(`app/notify.py`). Le push nécessite des clés VAPID
(`python scripts/generate_vapid_keys.py` → variables Railway) ; sans clés,
tout part par email. Opt-out par participant dans son profil.

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
