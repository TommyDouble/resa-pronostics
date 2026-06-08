# RESA Pronostics 2026 — Spécifications

> Version 1.1 · 8 juin 2026 · Usage interne RESA

---

## Table des matières

1. [Règles du jeu & Scoring](#1-règles-du-jeu--scoring)
2. [Modèle de données](#2-modèle-de-données)
3. [Architecture technique](#3-architecture-technique)
4. [Expérience participant](#4-expérience-participant)
5. [Page profil joueur](#5-page-profil-joueur)
6. [Backoffice organisateur](#6-backoffice-organisateur)
7. [Design system](#7-design-system)
8. [Roadmap des phases](#8-roadmap-des-phases)

---

## 1. Règles du jeu & Scoring

### 1.1 Principe général

Chaque participant pronostique les matchs du tournoi et répond à des questions bonus. Les points accumulés déterminent sa position dans quatre classements distincts. **Aucun malus** : un pronostic incorrect rapporte toujours 0 point.

### 1.2 Pronostics de matchs

**Soumission obligatoire** : l'issue du match — `Équipe 1 gagne` / `Match nul` / `Équipe 2 gagne`.

**Soumission optionnelle** (fortement encouragée) : le score exact (ex. `2-1`).

> Le score exact est indépendant de l'issue. Un score exact correct sur une issue incorrecte ne rapporte rien.

#### Barème

| Situation | Points | Note |
|---|---|---|
| Bonne issue | `+2 × poids` | Jamais de malus |
| Mauvaise issue | `0` | |
| Score exact correct (en sus) | `+2 fixes` | Indépendant du poids |
| Score exact incorrect | `0` | |

#### Poids des matchs

| Type | Poids | Max avec score exact |
|---|---|---|
| Phase de groupes — match normal | ×1 | 4 pts |
| Phase de groupes — Top Match ⭐ | ×2 | 6 pts |
| Phase finale (1/8 → finale) | ×2 | 6 pts |

**Top Match** : désigné par l'organisateur avant le tournoi. Par défaut = tous les matchs de 3ème journée de chaque groupe (enjeu maximal, joués simultanément). L'organisateur peut en désigner d'autres lors des J1/J2. Liste figée avant le coup d'envoi du premier match.

#### Algorithme de calcul (pseudo-code)

```python
def calculate_match_score(prediction, match) -> int:
    if match.result is None:
        return 0
    base = 2 if prediction.prediction == match.result else 0
    exact = 0
    if (prediction.exact_score_team1 == match.score_team1 and
        prediction.exact_score_team2 == match.score_team2):
        exact = 2
    return base * match.weight + exact
```

> Le calcul est idempotent. Un recalcul complet est déclenché à chaque encodage ou correction de résultat.

#### Règle phase finale & prolongations

En phase éliminatoire, l'issue et le score retenus sont ceux **à 90 minutes** (hors prolongations et tirs au but).

#### Exemples

| Contexte | Pronostic | Résultat réel | Points |
|---|---|---|---|
| Match groupe J1 (×1) | Argentine gagne, 2-1 | Argentine gagne 2-1 | `2×1 + 2 = 4 pts` |
| Top Match J3 (×2) | France gagne, 1-0 | France gagne 2-0 | `2×2 + 0 = 4 pts` |
| Quart de finale (×2) | Brésil gagne | Angleterre gagne | `0 pt` |

---

### 1.3 Pronostics pré-tournoi

Soumis **une seule fois**, avant le coup de sifflet du 1er match (11 juin 2026). Non modifiables après.

| Question | Type | Points si exact |
|---|---|---|
| Vainqueur du tournoi | Liste 48 équipes | `+8 pts` |
| Finaliste (l'autre équipe en finale) | Liste 48 équipes | `+5 pts` |
| Meilleur buteur | Texte + autocomplétion | `+5 pts` |
| Révélation du tournoi | Choix parmi 8-10 outsiders | `+5 pts` |
| Nb total de buts en phase de groupes (valeur exacte) | Numérique | `+8 pts` |
| Nb total de buts en phase de groupes (± 3 de la réalité) | Numérique | `+4 pts` |

> Pour le total de buts : les deux bonus ne se cumulent pas. Si valeur exacte → +8 pts seulement. Si dans ±3 mais pas exacte → +4 pts seulement.

> La liste des outsiders pour la "Révélation" est publiée et figée à l'ouverture des inscriptions.

---

### 1.4 Questions bonus mid-tournoi

L'organisateur publie **1 question** avant chaque phase éliminatoire.

| Publication | Deadline |
|---|---|
| Avant les huitièmes | Coup de sifflet du 1er match des 1/8 |
| Avant les quarts | Coup de sifflet du 1er match des quarts |
| Avant les demi-finales | Coup de sifflet du 1er match des demies |

Format : texte libre + type de réponse (choix unique / numérique / texte) + barème entre **3 et 10 pts**. Un participant qui ne répond pas obtient 0.

---

### 1.5 Règles de gel

| Pronostic | Gelé à |
|---|---|
| Match | Heure officielle de coup de sifflet (serveur) |
| Pré-tournoi | Coup de sifflet du 1er match du tournoi |
| Bonus mid-tournoi | Coup de sifflet du 1er match de la phase concernée |

La vérification est **exclusivement côté serveur**. Un pronostic soumis avant le gel peut être modifié librement.

---

### 1.6 Classements & gains

| Classement | Périmètre | 1er | 2ème | 3ème |
|---|---|---|---|---|
| Général | Tous les points | 180 € | 100 € | 60 € |
| Phase finale | Points matchs éliminatoires uniquement | 60 € | — | — |
| Questions bonus | Points bonus uniquement (pré + mid) | 50 € | — | — |
| Remontada | Meilleure progression de rang | 50 € | — | — |

**Total cagnotte : 500 € (10 € × 50 participants)**. Un participant peut cumuler plusieurs gains.

**Remontada** : rang_final_général − rang_fin_phase_de_groupes. Plus la valeur est négative, meilleure est la remontée.

---

### 1.7 Départage (ex-aequo)

En cas d'égalité de points, dans l'ordre :

1. Nombre de scores exacts corrects (↓)
2. Nombre d'issues correctes (↓)
3. Ordre alphabétique du nom (↑) — tiebreaker final, non modifiable

---

## 2. Modèle de données

### Schema SQL

```sql
CREATE TABLE participants (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  email      TEXT    NOT NULL UNIQUE,
  token      TEXT    NOT NULL UNIQUE,        -- UUID v4
  is_admin   INTEGER NOT NULL DEFAULT 0,
  is_confirmed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE matches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  match_number INTEGER NOT NULL UNIQUE,
  phase        TEXT    NOT NULL CHECK(phase IN
                 ('group','round_of_32','quarter','semi','third_place','final')),
  group_name   TEXT,
  match_date   TEXT    NOT NULL,             -- YYYY-MM-DD UTC
  kickoff_time TEXT    NOT NULL,             -- HH:MM UTC
  team1_name   TEXT    NOT NULL,
  team2_name   TEXT    NOT NULL,
  is_top_match INTEGER NOT NULL DEFAULT 0,
  weight       INTEGER NOT NULL DEFAULT 1 CHECK(weight IN (1,2)),
  score_team1  INTEGER,
  score_team2  INTEGER,
  result       TEXT    CHECK(result IN ('team1','draw','team2') OR result IS NULL),
  created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE predictions (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id      INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  prediction          TEXT    NOT NULL CHECK(prediction IN ('team1','draw','team2')),
  exact_score_team1   INTEGER,
  exact_score_team2   INTEGER,
  submitted_at        TEXT    NOT NULL DEFAULT (datetime('now')),
  is_locked           INTEGER NOT NULL DEFAULT 0,
  UNIQUE(participant_id, match_id)
);

CREATE TABLE bonus_questions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question_text  TEXT    NOT NULL,
  phase          TEXT    NOT NULL CHECK(phase IN
                   ('pre_tournament','round_of_32','quarter','semi')),
  answer_type    TEXT    NOT NULL CHECK(answer_type IN ('choice','number','text')),
  options        TEXT,                       -- JSON array si choice
  points_value   INTEGER NOT NULL DEFAULT 5,
  correct_answer TEXT,
  deadline       TEXT    NOT NULL,           -- ISO 8601 UTC
  created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE bonus_answers (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_id    INTEGER NOT NULL REFERENCES bonus_questions(id) ON DELETE CASCADE,
  answer         TEXT    NOT NULL,
  submitted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE(participant_id, question_id)
);

CREATE TABLE scores (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id    INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  match_id          INTEGER REFERENCES matches(id) ON DELETE CASCADE,
  bonus_question_id INTEGER REFERENCES bonus_questions(id) ON DELETE CASCADE,
  points            INTEGER NOT NULL DEFAULT 0,
  calculated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  CHECK(
    (match_id IS NOT NULL AND bonus_question_id IS NULL) OR
    (match_id IS NULL     AND bonus_question_id IS NOT NULL)
  ),
  UNIQUE(participant_id, match_id, bonus_question_id)
);
```

**Index obligatoires** : `participants(token)`, `predictions(match_id)`, `predictions(participant_id)`, `scores(participant_id)`.

---

## 3. Architecture technique

### 3.1 Stack

| Composant | Choix |
|---|---|
| Backend | Python 3.11 + FastAPI (async, validation Pydantic) |
| Base de données | SQLite 3 |
| Templates | Jinja2 (SSR, pas de SPA) |
| Frontend | HTML5 + Bootstrap 5 + CSS custom properties + Vanilla JS |
| Auth participants | Token UUID dans l'URL (`/p/<TOKEN>`) |
| Auth admin | Session HTTP + cookie signé (SessionMiddleware) |
| Hébergement | Railway (volume persistant pour SQLite) |
| Emails | fastapi-mail + templates HTML |

### 3.2 Gestion du temps

- Toutes les datetimes stockées en **UTC** (TEXT ISO 8601).
- Affichage converti en **Europe/Brussels** (UTC+2 été).
- Vérification deadline : côté serveur à chaque `POST /api/predictions`. Si `kickoff_time UTC` dépassé → `403 {"error": "deadline_passed"}`.
- Pas de cron job nécessaire.

### 3.3 Endpoints API

#### Participants (`auth: ?token=<uuid>`)

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/p/{token}` | Page d'accueil (confirme `is_confirmed`) |
| `GET` | `/api/matches` | Liste des matchs + statut pronostic du participant |
| `POST` | `/api/predictions` | Soumettre / modifier un pronostic |
| `GET` | `/api/rankings` | Les 4 classements |
| `GET` | `/api/match/{id}/details` | Détail post-coup de sifflet (403 avant) |
| `GET` | `/api/profile` | Stats du participant connecté |
| `GET` | `/api/profile/{id}` | Profil public d'un autre participant |
| `GET\|POST` | `/api/bonus` | Questions bonus + soumission réponses |

#### Admin (`auth: session cookie après /admin/login`)

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/admin/login` | Authentification |
| `GET\|POST` | `/admin/participants` | Gestion participants |
| `POST` | `/admin/participants/import` | Import CSV |
| `POST` | `/admin/matches/{id}/result` | Encoder résultat → recalcul |
| `GET\|POST` | `/admin/bonus-questions` | Créer questions bonus |
| `POST` | `/admin/bonus-questions/{id}/answer` | Encoder réponse correcte |
| `GET` | `/admin/export/rankings` | Export CSV classement |

### 3.4 Variables d'environnement

| Variable | Description |
|---|---|
| `DATABASE_URL` | Chemin absolu vers le fichier SQLite |
| `ADMIN_PASSWORD_HASH` | Hash bcrypt du mot de passe admin |
| `SECRET_KEY` | 32 bytes hex pour sessions (`secrets.token_hex(32)`) |
| `BASE_URL` | URL publique de l'application |
| `SMTP_HOST / PORT / USER / PASSWORD / FROM` | Configuration email |

---

## 4. Expérience participant

### 4.1 Onboarding — 1er accès `[Phase 1]`

Page dédiée (distincte de l'interface principale) :

- Logo + titre de bienvenue
- Formulaire : Prénom + Nom pré-remplis
- Bouton "Je participe" (pleine largeur mobile)
- Token invalide → `"Ce lien n'est pas valide. Contactez l'organisateur."`
- Visites suivantes : onboarding sauté directement.

---

### 4.2 Pronostics pré-tournoi `[Phase 1]`

Formulaire scrollable en 5 questions numérotées (cards distinctes) :

| # | Question | Composant |
|---|---|---|
| 1 | Vainqueur | Liste déroulante 48 équipes |
| 2 | Finaliste | Même liste — validation anti-doublon avec Q1 |
| 3 | Meilleur buteur | Texte libre + autocomplétion (≥ 2 chars) |
| 4 | Révélation | Choix unique parmi 8-10 outsiders |
| 5 | Nombre de buts | Numérique, contraintes min/max (50–300) |

Actions : "Enregistrer en brouillon" (sauvegarde sans verrouillage) + "Soumettre" (modale de confirmation, irréversible).

Tant que non soumis → badge orange d'alerte sur la page principale.

---

### 4.3 Saisie des pronostics de matchs `[Phase 1]`

Interface intégrée dans la liste (pas de navigation séparée). Par match :

- 3 pills radio : `[Équipe 1]` — `[Match nul]` — `[Équipe 2]`
- Pill active : fond `#D3450D`, texte blanc
- Après sélection d'une issue : apparition (300ms ease-out) du champ score exact optionnel
- Sauvegarde automatique à chaque sélection (badge "Enregistré ✓" pendant 2s)

**Statuts par match** :

| Statut | Indicateur |
|---|---|
| À pronostiquer | Pastille grise |
| Pronostic enregistré | Pastille verte + coche |
| Verrouillé | Pastille rouge + cadenas |

Les matchs passés apparaissent grisés avec résultat réel, pronostic du participant et points gagnés.

---

### 4.4 Classement `[Phase 1 basique → Phase 2 enrichi]`

Page publique (accessible sans token). La propre ligne du participant connecté est surlignée (`#F5E8E3`, bordure gauche 3px `#D3450D`).

- **Phase 1** : classement général uniquement. Colonnes : Rang | Nom | Score total | Évolution.
- **Phase 2** : 4 onglets (Général / Phase finale / Questions bonus / Remontada). Top 3 avec médailles 🥇🥈🥉.

---

### 4.5 Détail d'un match `[Phase 2]`

Accessible uniquement après le coup de sifflet.

- En-tête : score réel, noms équipes, phase, date
- Card "Mon pronostic" : pronostic + score exact + points (vert si > 0)
- Répartition communauté : barre proportionnelle — `32 Éq.1 | 12 Nul | 6 Éq.2`
- Tableau tous les pronostics : Participant | Pronostic | Score exact | Points (trié par points)

> Les pronostics des autres participants sont strictement cachés côté serveur jusqu'au coup de sifflet.

---

### 4.6 Questions bonus mid-tournoi `[Phase 3]`

Bannière non-dismissible orange en haut de toutes les pages pour les participants n'ayant pas encore répondu, avec deadline affichée. Formulaire dans l'onglet "Bonus".

---

### 4.7 Navigation globale

| Support | Format | Entrées |
|---|---|---|
| Desktop | Barre en haut, 64px | Pronostics · Classement · Mon profil · Bonus |
| Mobile | Barre fixée en bas, 56px | Idem avec icônes |

Badge orange sur "Bonus" si question disponible et non répondue.

---

## 5. Page profil joueur

### 5.1 Principe

Chaque participant dispose d'une page profil **publique** consultable par tous les participants. Objectif : créer de l'engagement social — les collègues vont "fouiller" pour comprendre le style de jeu des autres et se comparer.

URL : `/profil/{participant_id}` (accessible à tous les participants connectés, pas seulement à l'intéressé).

---

### 5.2 Données affichées

#### En-tête

| Élément | Source |
|---|---|
| Avatar initiales | `prenom[0] + nom[0]`, fond couleur dérivée de l'ID |
| Nom complet | `participants.name` |
| Rang actuel (général) | Classement général calculé |
| Score total | `SUM(scores.points)` |
| Nb de matchs pronostiqués | `COUNT(predictions)` |
| Date d'inscription | `participants.created_at` |

---

#### Bloc métriques (4 cards)

| Métrique | Calcul |
|---|---|
| Taux de réussite | `nb_issues_correctes / nb_matchs_avec_résultat × 100` |
| Scores exacts | `COUNT(predictions WHERE exact_score_correct = true)` |
| Série actuelle | Nb de bons pronostics consécutifs depuis le dernier raté |
| Meilleure journée | Journée (groupe J1-J3) avec le plus de points |

---

#### Profil tactique — Radar 5 axes

| Axe | Calcul |
|---|---|
| **Précision** | `% d'issues correctes` (normalisé 0–100) |
| **Exactitude** | `% de scores exacts` × 3 (pondéré car rare) |
| **Audace** | `% de pronostics "outsider"` — équipe ayant < 30% de cotes de victoire |
| **Régularité** | `100 - (écart-type des points par match × 10)` |
| **Finales** | `points_phase_finale / points_total × 150` (normalisé) |

---

#### Archétype du parieur

Label algorithmique calculé à partir des 5 axes du radar. Logique de sélection :

| Archétype | Condition de déclenchement |
|---|---|
| Le Chasseur d'exactes | Exactitude > 70 |
| L'Outsider Hunter | Audace > 65 |
| Le Prudent | Précision > 75 et Audace < 35 |
| Le Joueur de nul | `nb_nuls_pronostiqués / total > 25%` |
| La Machine | Régularité > 80 et Précision > 70 |
| L'Homme des grands soirs | Finales > 75 |

Si aucune condition atteinte : "Le Pragmatique" (profil équilibré). Si plusieurs conditions déclenchées : priorité à la plus haute valeur d'axe.

Chaque archétype a une description courte (1-2 phrases).

---

#### Forces & faiblesses

Calculées automatiquement :

- **Force** : axe le plus haut dans le radar
- **Faiblesse** : axe le plus bas

Affichées en vert/rouge avec flèche directionnelle (↑ / ↓).

---

#### Forme récente — 10 derniers matchs

Visualisation en barres verticales :

| Couleur barre | Condition |
|---|---|
| Orange plein `#D3450D` | Points ≥ 4 (bonne issue + score ou match valorisé) |
| Orange pâle `rgba(211,69,13,.3)` | Points > 0 et < 4 |
| Gris | 0 point |

Hauteur proportionnelle aux points (max = 6).

---

#### Statistiques fun (6 items)

| Stat | Calcul |
|---|---|
| Équipe la plus soutenue | `team1_name` ou `team2_name` le plus souvent prédit gagnant |
| Matchs nuls tentés / réussis | `COUNT(nuls pronostiqués)` / `COUNT(nuls réussis)` |
| Pire journée | Journée avec le moins de points |
| Délai moyen de soumission | `AVG(kickoff_time - submitted_at)` en minutes |
| Outsider le plus prédit | Équipe outsider prédite gagnante le plus souvent |
| Rival naturel | Participant dont l'évolution de rang est la plus corrélée |

---

#### Comparaison directe (si ≠ profil propre)

Quand un participant consulte le profil d'un autre :

- Barre de progression bipartite : score A vs score B
- Écart en points + nombre de matchs restants
- "Il te faudrait X pts de plus que lui sur les prochains matchs pour le rattraper"

---

#### Badges débloqués

| Badge | Condition de déclenchement |
|---|---|
| Sniper | ≥ 5 scores exacts en une phase |
| Chasseur d'upset | ≥ 3 surprises outsider validées |
| Dernier de la minute | ≥ 6 soumissions dans les 5 dernières minutes avant KO |
| En série | Série en cours ≥ 4 bons pronostics |
| Fidèle au poste | 100% des matchs pronostiqués |
| Roi des bonus | 1er au classement questions bonus |
| Remontada | A gagné le classement Remontada |

Les badges non encore débloqués sont affichés grisés avec leur condition visible.

---

#### 5 derniers matchs

Tableau : Match | Statut (pill colorée) | Résultat réel | Points.

| Pill | Condition |
|---|---|
| "Score exact" (orange) | Issue correcte ET score exact |
| "Bonne issue" (vert) | Issue correcte, score exact raté ou non saisi |
| "Raté" (gris) | Issue incorrecte |

---

### 5.3 Visibilité & accès

- La page profil est **uniquement accessible aux participants** (token valide requis).
- Les pronostics des matchs **non encore joués** ne sont jamais affichés sur la page profil d'un autre participant.
- Le profil propre (`/profil/moi` ou bouton "Mon profil") affiche les mêmes données, mais avec en plus : l'historique complet de tous les matchs.

---

### 5.4 Phase de livraison

- **Phase 1** : en-tête + métriques + 5 derniers matchs + comparaison directe
- **Phase 2** : radar + archétype + forme récente + stats fun
- **Phase 3** : badges + profil propre enrichi

---

## 6. Backoffice organisateur

Accessible via `/admin`. Auth : identifiant + mot de passe. Session expirée après 8h d'inactivité.

### 6.1 Tableau de bord `[Phase 1]`

Rafraîchissement auto toutes les 60s.

- Participants confirmés / total invités
- Pronostics pré-tournoi soumis / confirmés
- Participants ayant pronostiqué ≥ 1 match
- Prochain match sans résultat encodé
- 5 derniers résultats encodés
- **Alerte** (bandeau rouge) : matchs joués depuis > 2h sans résultat

---

### 6.2 Gestion des participants `[Phase 1]`

Colonnes : Nom | Email | Statut | Pré-tournoi soumis | ≥ 1 pronostic | Actions.

Actions par ligne :
- Copier le lien unique
- Renvoyer l'email d'invitation
- Supprimer (soft delete — exclu des classements). Désactivé si tournoi démarré.

**Ajout manuel** : Nom + Email → génère token UUID + envoie email. Erreur si email existant.

**Import CSV** : colonnes `nom, email`. Validation complète avant tout import (0 erreur = import autorisé). Résumé affiché après.

---

### 6.3 Gestion des matchs `[Phase 1]`

78 matchs pré-chargés (script `seed_matches.py`). Filtrable par phase.

Actions :
- Toggle Top Match (avant coup de sifflet)
- Modifier date/heure (cas de report)
- Marquer "Annulé" (0 pt calculé)

Résolution automatique des équipes éliminatoires dès qu'un groupe est complet. En cas d'égalité non résolue automatiquement → champ de saisie manuelle.

---

### 6.4 Encodage des résultats `[Phase 1]`

Vue listant les matchs joués sans résultat (ordre chronologique).

Par match : Score Éq.1 `[ ]` — `[ ]` Score Éq.2 + bouton "Valider".

Après validation :
- Résultat persisté avec horodatage + ID admin
- Recalcul des points pour les 50 participants (**< 3 secondes**)
- Confirmation inline + match retiré de la liste

**Correction** : bouton "Corriger" sur tout match encodé. Recalcul complet. Historique conservé (les deux versions).

> Avertissement si score `0-0` sur un match éliminatoire (saisie autorisée après confirmation).

---

### 6.5 Questions bonus pré-tournoi `[Phase 1]`

L'organisateur définit :
- La liste des outsiders pour la "Révélation" (figée à l'ouverture des inscriptions)
- La réponse correcte à chaque question (déclenche calcul automatique)

---

### 6.6 Questions bonus mid-tournoi `[Phase 3]`

Création : texte + type + options + points (3–10) + phase. Deadline calculée automatiquement. Encodage de la réponse → calcul auto.

---

### 6.7 Communications `[Phase 1]`

- "Envoyer rappel pré-tournoi" → email groupé aux non-soumetteurs
- "Envoyer rappel match" → sélecteur de match (24h) + envoi aux non-pronostiqueurs
- Configuration SMTP dans les paramètres admin (avec bouton "Tester")

---

### 6.8 Exports `[Phase 2]`

- Classement CSV : Rang | Nom | Points | Détail par catégorie
- Pronostics par match : Nom | Pronostic | Score exact | Points

---

## 7. Design system

### 7.1 Couleurs

| Token | Hex | Usage |
|---|---|---|
| `--color-primary` | `#D3450D` | CTA, sélections actives, scores, rangs |
| `--color-primary-light` | `#F5E8E3` | Fond ligne propre participant |
| `--color-primary-dark` | `#A33308` | Hover bouton primaire |
| `--color-success` | `#2E7D32` | Points positifs, badge enregistré |
| `--color-warning` | `#F59E0B` | Badge Top Match |
| `--color-error` | `#DC2626` | Match verrouillé, deadline proche |
| `--color-neutral-50` | `#F9FAFB` | Fond de page |
| `--color-neutral-100` | `#F3F4F6` | Fond cards secondaires |
| `--color-neutral-300` | `#D1D5DB` | Bordures, séparateurs |
| `--color-neutral-600` | `#4B5563` | Texte secondaire |
| `--color-neutral-900` | `#111827` | Texte principal |

> `#D3450D` est réservé aux éléments interactifs et chiffres clés. Ne pas l'utiliser en fond décoratif pleine page.

---

### 7.2 Typographie

| Usage | Police | Poids | Taille |
|---|---|---|---|
| Titres H1–H3, grands chiffres | Nunito | 700 / 800 | 48 / 32 / 24 px |
| Corps, labels, descriptions | Inter | 400 / 500 | 16 / 14 / 12 px |

Échelle : 12 · 14 · 16 · 18 · 24 · 32 · 48 px.

---

### 7.3 Spacing, radius, shadows

```css
:root {
  --color-primary:      #D3450D;
  --color-primary-light:#F5E8E3;
  --color-primary-dark: #A33308;
  --color-success:      #2E7D32;
  --color-neutral-50:   #F9FAFB;
  --color-neutral-900:  #111827;
  --font-display:       'Nunito', sans-serif;
  --font-body:          'Inter', sans-serif;
  --radius-card:        8px;
  --radius-pill:        999px;
  --shadow-card:        0 1px 3px rgba(0,0,0,0.08);
  --shadow-focus:       0 0 0 3px rgba(211,69,13,0.15);
}
```

---

### 7.4 Composants

#### Bouton primaire

```
Fond #D3450D · texte blanc · border-radius 999px · padding 12×24px
Hover: #A33308 (150ms) · Disabled: opacité 40% · Loading: spinner blanc
```

#### Pills de pronostic

3 pills côte à côte (`flex:1`, `gap:8px`, `border-radius:999px`).

- Non sélectionné : `#F3F4F6` / bordure `#D1D5DB` / texte `#4B5563`
- Sélectionné : `#D3450D` / texte blanc
- Verrouillé : `#F3F4F6` / texte `#D1D5DB` / `pointer-events:none`

#### Ligne de classement

`height:56px` · Rang (Nunito 700 24px) · Nom · Score (`#D3450D`) · Évolution (↑ vert / ↓ rouge).
Propre ligne : `background:#F5E8E3` · `border-left:3px solid #D3450D`.

#### Navigation desktop / mobile

| | Desktop | Mobile |
|---|---|---|
| Position | Top, 64px | Bottom fixe, 56px |
| Fond | Blanc | Blanc |
| Lien actif | Border-bottom 2px `#D3450D` | Couleur `#D3450D` |

---

### 7.5 Responsive

| Breakpoint | Layout |
|---|---|
| < 768px (mobile) | Nav en bas, padding 12px, pills `flex:1`, inputs 44px min |
| 768–1024px (tablet) | Nav en haut, max-width 640px |
| > 1024px (desktop) | Nav en haut 64px, max-width 720px |

---

### 7.6 Animations

| Interaction | Spec |
|---|---|
| Sauvegarde silencieuse | Fade-in 150ms du badge "Enregistré ✓", disparition après 2s |
| Score exact expansion | `max-height: 0 → 80px` + `opacity: 0 → 1`, 300ms ease-out |
| Skeleton loader classement | Shimmer 1.4s sur 3 lignes grises pendant le fetch |
| Underline onglets | Transition `left + width` 200ms ease |

---

### 7.7 Accessibilité

- Ratio contraste ≥ 4.5:1 (blanc sur `#D3450D` = 4.7:1 ✓)
- Focus visible sur tous les éléments interactifs (shadow-focus)
- `aria-label` sur les pills de pronostic
- Taille tactile minimale : 44px (guidelines Apple/Google)

---

## 8. Roadmap des phases

| Phase | Échéance | Livrables |
|---|---|---|
| **Phase 1 — MVP** | Avant le 11 juin 2026 | Schéma BDD + migrations · Auth token + session admin · `seed_matches.py` (78 matchs) · Onboarding participant · Pronostics pré-tournoi · Saisie pronostics matchs + deadline enforcement · Backoffice : participants CRUD + CSV import + encodage résultats + recalcul < 3s · Classement général · Emails invitation · Page profil (en-tête + métriques + 5 derniers matchs + comparaison) |
| **Phase 2** | Phase de groupes (juin 2026) | Pronostics visibles post coup de sifflet · 4 classements + onglets · Détail match · Profil enrichi (radar + archétype + forme + stats fun) · Stats communauté · Export CSV |
| **Phase 3** | Phase finale (juillet 2026) | Questions bonus mid-tournoi · Badges profil · Notifications in-app · Gamification avancée ("qui peut me rattraper", stats humoristiques) |

> **Dépendance critique** : la table `scores` doit être peuplée (Phase 1) avant que les classements Phase finale et Remontada soient calculables (Phase 2). Le script `seed_matches.py` doit être exécuté en Phase 1 avec le calendrier FIFA 2026 complet.

---

*RESA Pronostics 2026 — Document interne — Ne pas diffuser*
