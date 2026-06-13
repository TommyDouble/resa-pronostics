# RESA Pronostics — guide projet

Concours de pronostics foot **interne d'entreprise** (RESA, 7 départements, collègues).
Stack : FastAPI + Jinja (server-rendered), vanilla JS (aucune lib), SQLite/aiosqlite, PWA web-push.
Maintenu par un seul dev. Mobile-first. Philosophie d'accueil : **une seule action prioritaire**.

## Tests
Pas de venv ni pytest préinstallés sur la machine. Créer/utiliser un venv local :
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx`
puis `.venv/bin/python -m pytest -q`. (Le scheduler est désactivé en test via `SCHEDULER_ENABLED=0`, cf. `tests/conftest.py`.)

## Convention : story des nouveautés (IMPORTANT)

> **À chaque nouvelle fonctionnalité UX/UI, DEMANDER au PO si on crée une story** pour la
> présenter. Toutes les features visuelles n'en ont pas forcément une — c'est décidé au cas
> par cas pendant le dev.

### Philosophie (non négociable, ne plus en rediscuter)

Une story n'est PAS un changelog ni un écran d'accroche unique : c'est un **mini-tutoriel
qui ACCOMPAGNE l'utilisateur pas à pas dans son parcours réel**. Règle absolue :

> **Une vue par écran.** Chaque écran réel de l'app que la fonctionnalité fait apparaître
> ou modifie a **son propre écran de story**, présenté **dans l'ordre où l'utilisateur le
> rencontrerait**. On lui montre *où* est la nouveauté et *ce qu'elle fait*, écran après
> écran, pour qu'il puisse refaire le chemin tout seul.

Concrètement : si la feature touche 2 écrans (ex. un nouveau bouton sur l'accueil **puis**
un nouvel écran), la story a **au moins ces 2 écrans** (souvent précédés d'un écran
d'accroche). On ne **résume jamais** plusieurs étapes en une seule vue, et on **ne se
contente jamais de décrire** : on **montre** chaque étape avec sa maquette.

Si oui, la story DOIT respecter le **standard uniforme** (ne jamais réinventer un look par feature) :

- **Parcours guidé, une vue par écran** : la story rejoue le **trajet utilisateur complet**,
  un écran de story = une étape réelle (ex. Reveal : accroche → le nouveau CTA sur l'accueil
  → le nouvel écran Reveal). Une story par fonctionnalité, avec **titre fixé en haut** et
  **compteur « i/N »** (réinitialisés par feature) pour situer l'utilisateur dans le parcours.
- **Thème uniforme = clair + manuscrit « brouillon »** : fond clair (`--paper`), police
  manuscrite **Caveat** (`var(--sketch)`) sur titres et légendes, maquettes légèrement
  inclinées (esprit note épinglée). **Toujours des données d'exemple** dans les maquettes
  (ex. `🇫🇷 2–0 🇧🇷`, `🎯 +5 pts`).
- **Maquettes HTML/CSS uniquement** (jamais de capture/GIF, jamais de JS couplé à une
  feature vivante). Chaque écran doit être **compréhensible figé** (son coupé) ; l'animation
  n'est qu'un bonus, `prefers-reduced-motion` respecté.
- **Réutiliser les classes partagées** : `.story-screen`, `.story-lead`, `.story-cap`,
  maquettes `.sm-frame` (mini-accueil/CTA) et `.rp-*` (cartes avant/après). Ne pas créer
  de nouveau style ad hoc → c'est ce qui garantit l'uniformité.

### Comment ajouter une story (registre central `app/news.py`)
Source de vérité unique. Ajouter une story = :
1. Une entrée dans `STORY_TEMPLATES` (clé + libellé admin) — la whitelist de rendu
   (`pages.py`), la validation et le menu admin (`admin/news.html`) en dérivent.
2. Le partial des écrans : `app/templates/partials/news/{clé}.html` qui émet N
   `<div class="story-screen" data-story-screen>` (chacun = une maquette + une `.story-cap`).
3. (Optionnel) une entrée `NEWS_DEFAULTS` dans `app/news.py` pour livrer la news avec la
   feature, sinon création via l'écran admin `/admin/nouveautes`.

Player générique : `initStoryPlayer()` dans `app/static/js/resa.js` (ne connaît aucune
feature). Suivi « vu » : `participants.last_seen_news_id` + `POST /api/news/seen`.
