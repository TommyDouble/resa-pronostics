# Audit UX/UI & Produit — RESA Pronostics 2026

> Audit réalisé le 10 juin 2026 (veille du coup d'envoi du tournoi), à partir du code complet de la branche `main`.
> Périmètre : expérience participant, expérience organisateur, UI, gamification, rétention.

---

## 0. Compréhension du produit à partir du code

**Type d'application** : jeu de pronostics Coupe du Monde 2026 pour une communauté unique (~50 collègues RESA). FastAPI + SQLite + Jinja2 (SSR), JS vanilla, CSS custom avec une identité "carnet de croquis" (police Caveat, fond papier). Déployé sur Railway. Mobile-first (colonne 480px, nav basse), desktop = même colonne élargie (760px) avec nav haute.

**Routes participant** (`app/routers/pages.py`) :
- `/` login email+mot de passe · `/rejoindre` inscription publique · `/p/{token}` accueil (lien personnel)
- `/p/{token}/pronos` saisie des 104 matchs (9 sections) · `/p/{token}/pre-tournoi` 5 questions · `/p/{token}/bonus` questions bonus
- `/p/{token}/classement` · `/p/{token}/match/{id}` détail post-coup d'envoi · `/p/{token}/profil[/edit|/{id}]` · `/p/{token}/reglement`
- API : `POST /api/predictions` (auto-save des scores)

**Routes admin** (`app/routers/admin.py`) : dashboard KPI, participants (ajout/invitation/CSV/paiement/suppression), matchs (top match), résultats (encodage + correction), pré-tournoi (réponses correctes, deadline, libellés), bonus (CRUD + réponse correcte), communications (rappels email manuels).

**Fonctionnalités présentes** : saisie score-first avec issue déduite et auto-save ; verrouillage au coup d'envoi côté serveur ; qualifié-si-nul en phase finale ; pré-tournoi brouillon/soumission ; questions bonus à deadline ; classement général avec podium ; profil public avec stats (taux de réussite, scores exacts, série, meilleur groupe), avatar, bio, pseudo, comparaison 1-vs-1 ; répartition communauté par match ; conversion fuseaux horaires côté client ; rappels email manuels.

**Fonctionnalités partielles ou ambiguës** :
- Le SPEC promet 4 classements (général / phase finale / bonus / remontada) → seul le général existe.
- Classement inter-départements annoncé dans le README et l'UI ("Servira au futur classement inter-départements") → inexistant.
- Radar tactique, archétypes, badges, stats fun, forme récente (CSS `.bars`, `.arche`, `.bdg`, `.funlist` déjà écrits dans `resa.css`) → jamais branchés.
- Formulaire SMTP de `/admin/communications` entièrement `disabled` → UI morte.
- `bonus_questions.phase` est contraint en base à `('pre_tournament','round_of_32','quarter','semi')` → impossible de créer une question bonus pour les huitièmes (`round_of_16`) ou la finale, alors que le tournoi 2026 a 6 tours à élimination.

**Angle mort principal** : le brief parle de "groupes d'amis" qui se créent et se rejoignent. Le code est mono-communauté : pas de notion de groupe, de ligue ni d'organisation. C'est la question stratégique n°1 (voir §11).

---

## 1. Résumé exécutif (brutalement honnête)

L'application est bien au-dessus de la moyenne des projets "pronos entre collègues" : le geste central (taper un score, issue déduite, sauvegarde automatique) est excellent, le règlement est expliqué en langage humain, le verrouillage serveur est propre, et l'identité visuelle "carnet" est distinctive. C'est un vrai produit, pas un Google Sheet déguisé.

Mais trois faiblesses risquent de coûter cher dès cette semaine :

1. **Le piège du brouillon pré-tournoi** : un participant qui clique "Enregistrer en brouillon" et oublie "Soumettre" marque **0 point** sur ~30 points possibles (`recalculate_pre_tournament_scores` ne lit que `submitted=1`). La deadline est demain soir. C'est le bug UX le plus dangereux de l'app.
2. **Aucune mécanique de retour** : pas de rappel automatique, pas de récap, pas d'état "tout est à jour", rien qui ramène l'utilisateur à J+3. La rétention repose entièrement sur le fait que l'admin pense à envoyer des emails à la main.
3. **La motivation est floutée** : cagnotte "à préciser" partout, un seul classement, pas de prix intermédiaires visibles. Le moteur compétitif tourne à vide pour tous ceux qui ne sont pas dans le top 5.

Le reste est du polish : home surchargée de bannières, statut "Terminé" affiché pendant les matchs en cours, typographie trop petite, pas de drapeaux, pas de favicon/PWA. Rien de structurel — l'architecture SSR simple est saine et permet d'itérer vite.

---

## 2. Ce que l'application fait bien

- **La saisie score-first** ([predictions.html](app/templates/predictions.html) + `initPredictionScores` dans [resa.js](app/static/js/resa.js)) : un seul geste (deux chiffres), issue calculée, auto-save débouncé 500 ms, badge "Enregistré ✓", clamp 0–30, flèches clavier. C'est le meilleur écran de l'app et il est meilleur que Kicktipp.
- **Le règlement** ([rules.html](app/templates/rules.html)) : lexique, exemples chiffrés par cas (finalistes, révélation, remontada), ton simple. Rare et précieux pour les non-footeux.
- **La rigueur temporelle** : stockage UTC, verrouillage exclusivement serveur (`is_match_locked`), affichage dans le fuseau du navigateur (`data-local-utc`). Zéro litige possible sur les deadlines.
- **Le qualifié-si-nul** en phase finale : le champ n'apparaît que si le score saisi est nul, avec tooltip pédagogique. Une règle complexe rendue digeste.
- **Le pré-tournoi guidé** : miroir du champion dans "Finaliste 1", anti-doublon champion/finaliste (client + serveur), combobox 1246 joueurs avec recherche accent-insensible et navigation par équipe — du travail soigné.
- **Les débuts de social** : profils publics, avatars photo, pseudo, bio, comparaison 1-vs-1 avec barre bipartite, répartition communauté par match, pronostics des autres révélés après coup d'envoi.
- **L'identité visuelle** : Caveat + texture papier + orange #D3450D = personnalité reconnaissable, loin du template Bootstrap générique.
- **L'hygiène technique** : tests (scoring, rankings, inscriptions, pages), recalcul idempotent, tolérance "10,0/10.0" sur les réponses numériques.

---

## 3. Ce qui limite aujourd'hui l'expérience

- **Pas de boucle de retour** : aucun rappel automatisé (les emails partent à la main depuis `/admin/communications`), pas de notification, pas de récap post-journée, pas de PWA installable. Pendant 5 semaines de tournoi, c'est la cause n°1 d'attrition.
- **La home nag sans jamais féliciter** : le CTA par défaut de [home.html](app/templates/home.html) reste "Compléter mes scores" même quand tout est fait ; jusqu'à 5 blocs d'alerte empilés (paiement, CTA, bonus, pré-tournoi, urgence, matchs à compléter) qui se répètent entre eux ; et les jours sans match, la home est quasi vide (aucune info sur le prochain match).
- **Un seul classement** : les joueurs hors top 10 n'ont aucune raison de revenir. Les classements phase finale / bonus / remontada / départements — tous déjà prévus dans le SPEC et le règlement — sont la réponse, et ils ne sont pas implémentés.
- **Une récupération de compte inexistante** : pas de "mot de passe oublié", pas de "renvoyer mon lien". Un participant qui perd son email d'invitation et son mot de passe est bloqué (`login.html` se contente de dire d'utiliser le lien).
- **Des statuts trompeurs** : `is_locked` = coup d'envoi passé, mais [predictions.html:86](app/templates/predictions.html) affiche "Terminé" pendant que le match se joue. Et les points n'apparaissent que quand l'admin encode le résultat, sans que l'attente soit expliquée.
- **Une UI qui repose sur des styles inline** : des dizaines de `style="font-size:12px;…"` dans les templates contournent le design system de `resa.css`. Tailles de 10–11px fréquentes, cibles tactiles de 17px (`.help-tip`), gris #9CA39C sur fond papier sous les seuils de contraste.

---

## 4. Les 10 problèmes UX/UI prioritaires

### P1 — Le brouillon pré-tournoi vaut 0 point
- **Écran** : [pre_tournament.html](app/templates/pre_tournament.html) (boutons "Enregistrer en brouillon" / "Soumettre") ; [scoring.py:304](app/scoring.py) (`WHERE submitted=1`).
- **Problème** : deux boutons côte à côte, le moins engageant à gauche ; rien n'explique qu'un brouillon non soumis ne rapporte **aucun** point.
- **Conséquence** : des participants perdront ~30 pts sans comprendre pourquoi ; contestation garantie, confiance entamée.
- **Recommandation** : supprimer la distinction — toute réponse enregistrée compte (un seul bouton "Enregistrer mes pronos", modifiable jusqu'à la deadline, comme les matchs). Alternative minimale : auto-soumission des brouillons à la deadline + bannière rouge "Brouillon non soumis = 0 point".
- **Priorité : critique · Effort : faible · Impact : élevé**

### P2 — Aucun rappel automatique pendant 5 semaines
- **Écran** : aucune tâche planifiée ; [mail.py](app/mail.py) n'est appelé que par les boutons de `/admin/communications`.
- **Problème** : la rétention dépend de la discipline manuelle de l'organisateur ; `email_opt_in` existe mais ne sert à rien automatiquement.
- **Conséquence** : pronos oubliés → frustration ("j'aurais gagné des points") → abandon progressif après la phase de groupes.
- **Recommandation** : un scheduler (boucle asyncio au démarrage ou cron Railway) : J-1 matin "X matchs demain, il t'en manque Y" aux non-pronostiqueurs opt-in ; rappel deadline bonus ; récap après chaque journée encodée ("+6 pts hier, tu passes 8e").
- **Priorité : critique · Effort : moyen · Impact : élevé**

### P3 — Cagnotte et prix "à préciser"
- **Écran** : [ranking.html:65-74](app/templates/ranking.html) ("montants à préciser") ; [rules.html](app/templates/rules.html) section gains (6 × "à préciser").
- **Problème** : le motivateur n°1 d'un pool entre collègues est volontairement flou.
- **Conséquence** : tension compétitive molle ; personne ne se projette sur "le prix bonus" ou "la remontada".
- **Recommandation** : fixer les montants (même provisoires : "basé sur 50 participants × 10 €") et les afficher sur le classement et le règlement ; à terme un encart admin pour les éditer.
- **Priorité : haute · Effort : faible · Impact : élevé**

### P4 — Pas de récupération d'accès
- **Écran** : [login.html](app/templates/login.html) ; aucune route de reset dans `pages.py`.
- **Problème** : pas de "mot de passe oublié", pas de "renvoyer mon lien personnel".
- **Conséquence** : tout accès perdu = ticket à l'organisateur = friction et abandon silencieux.
- **Recommandation** : lien "Lien personnel perdu ?" sur la page de connexion → formulaire email → renvoi du lien token par mail (réutilise `send_invitation`). Réponse neutre ("Si cet email est inscrit, le lien a été renvoyé").
- **Priorité : haute · Effort : faible/moyen · Impact : élevé**

### P5 — La home ne sait pas dire "c'est bon" ni "à venir"
- **Écran** : [home.html:31-47](app/templates/home.html) (namespace `cta`) ; [pages.py:390-416](app/routers/pages.py) (requête limitée aux matchs du jour).
- **Problème** : CTA par défaut = injonction permanente ; les jours sans match, ni prochain match ni compte à rebours ; aucun feedback sur les points récents.
- **Conséquence** : l'app semble "ne rien avoir à dire" hors jours de match — exactement les jours où il faut donner une raison de revenir.
- **Recommandation** : état "✅ Tout est à jour — prochain match : Brésil–Japon, vendredi 18h" (requête sur le prochain match futur, pas seulement aujourd'hui) + bloc "Depuis ta dernière visite : +X pts, rang Y→Z".
- **Priorité : haute · Effort : faible · Impact : élevé**

### P6 — "Terminé" affiché pendant les matchs en cours
- **Écran** : [predictions.html:85-86](app/templates/predictions.html) (`is_locked` → "Terminé") ; statut équivalent ambigu sur la home.
- **Problème** : un match verrouillé n'est pas un match terminé ; et un match terminé sans résultat encodé n'explique pas l'absence de points.
- **Conséquence** : confusion les jours de match ("l'app déconne, le match vient de commencer") ; attente de points incomprise.
- **Recommandation** : trois états — "🔴 En cours" (verrouillé, < ~2h30 après coup d'envoi, sans résultat), "Attente du résultat officiel" (au-delà), "Terminé 2-1" (résultat encodé). Pure logique template + helper `minutes_until_match` déjà existant.
- **Priorité : haute · Effort : faible · Impact : moyen**

### P7 — Un seul classement pour 50 joueurs
- **Écran** : [ranking.html](app/templates/ranking.html) ; `get_rankings` dans [scoring.py](app/scoring.py).
- **Problème** : les classements phase finale / bonus / remontada / départements (promis par le règlement et le README) n'existent pas ; pas d'indicateur d'évolution (↑↓) ; le 35e n'a rien à jouer.
- **Conséquence** : désengagement de la moitié du peloton dès la 2e semaine — le moment exact où la remontada devrait les retenir.
- **Recommandation** : onglets sur la page classement (les données `scores`/`pre_tournament_scores` suffisent : filtrer par phase ; remontada = rang fin de groupes vs rang courant à snapshotter) + colonne évolution depuis la veille.
- **Priorité : haute · Effort : moyen/élevé · Impact : élevé**

### P8 — Empilement de bannières sur la home
- **Écran** : [home.html:21-102](app/templates/home.html) — bannière paiement (toujours visible, même payée), carte CTA, bannière bonus, encart pré-tournoi, carte urgence, bannière rouge "matchs à compléter", carte règlement.
- **Problème** : jusqu'à 6 blocs avant le contenu, dont 3 qui disent la même chose ("complète tes pronos") ; la bannière verte "Paiement reçu" est du bruit permanent.
- **Conséquence** : cécité aux bannières — le jour où une alerte compte vraiment, elle est ignorée.
- **Recommandation** : une seule "prochaine action" prioritaire (urgence > pré-tournoi > bonus > pronos) ; paiement visible uniquement si impayé, avec instructions concrètes (montant, à qui) ; carte règlement reléguée en bas après la 1re semaine.
- **Priorité : moyenne · Effort : faible · Impact : moyen**

### P9 — Naviguer 104 matchs sans recherche ni vue "à faire"
- **Écran** : [predictions.html](app/templates/predictions.html) — 9 onglets ("Match 1/2/3", Seizièmes…), lien "Voir le prochain match à compléter" limité à la section courante.
- **Problème** : pas de vue transversale "tous mes matchs à pronostiquer", pas de recherche par équipe, sections de 24+ matchs à scroller ; le découpage "Match 1 de chaque équipe" demande un effort de compréhension (l'aide existe mais est en bannière grise).
- **Conséquence** : friction sur le geste le plus fréquent de l'app ; pronos oubliés en fin de section.
- **Recommandation** : onglet/filtre "À compléter (n)" en première position qui agrège toutes les sections ; champ de filtre par équipe ; le compteur global manquant dans le header.
- **Priorité : moyenne · Effort : moyen · Impact : moyen/élevé**

### P10 — Typographie trop petite, contrastes et cibles tactiles
- **Écran** : global — `.lbl` 10px uppercase ([resa.css:140](app/static/css/resa.css)), `.sub1` 11px, `.help-tip` 17×17px porteur d'explications clés, `--n400` #9CA39C sur papier (~2.6:1), dizaines de `style="font-size:11px"` inline dans les templates.
- **Problème** : le corps utile descend sous 12px sur mobile ; les explications importantes (barème, séries, qualifié) sont derrière des cibles de 17px ; le gris des labels est sous le seuil WCAG.
- **Conséquence** : lisibilité pénible pour les +45 ans d'une population d'entreprise ; aide invisible sur mobile.
- **Recommandation** : plancher 13px pour tout texte porteur de sens, 12px pour le métadata ; `--n400` réservé au décoratif, labels en `--n600` ; zone tactile 44px sur les help-tips (padding invisible) ; migrer les styles inline récurrents vers des classes.
- **Priorité : moyenne · Effort : moyen · Impact : moyen**

**Bugs techniques à corriger au passage** (impact UX direct) :
- Contrainte `CHECK` de `bonus_questions.phase` sans `round_of_16` ni `final` ([database.py:93](app/database.py)) → bloquera l'admin aux huitièmes.
- Comparaison des réponses texte sensible aux accents ([scoring.py:184-197](app/scoring.py) : "Mbappe" ≠ "Mbappé") → privilégier les questions à choix, ou folder les accents comme le fait déjà `resa.js`.
- Valeur par défaut du stepper buts = 140 alors que l'aide affiche le repère 144 ([pre_tournament.html:161](app/templates/pre_tournament.html)).
- Mini-classement de la home : en cas d'égalité au rang 3, le participant à égalité hors `rankings[:3]` est dupliqué/absent selon le cas ([pages.py:418-422](app/routers/pages.py)).
- Pas de protection CSRF sur les POST admin (session cookie) — risque faible en interne, à noter.

---

## 5. Analyse des parcours clés

**Première visite (lien token)** — ✅ Onboarding minimal (prénom/nom/département), friction quasi nulle. ❌ Après confirmation, on est lâché sur la home sans cadrage : aucun "3 étapes : ① pré-tournoi avant demain ② tes pronos ③ le classement". **Idéal** : écran ou carte de bienvenue à 3 étapes avec états cochés, qui disparaît une fois les 3 faits.

**Inscription publique (`/rejoindre`)** — ✅ Formulaire court, erreurs claires, départements triés. ❌ Le mot de passe est obligatoire ici alors que le parcours token n'en demande pas — incohérence de modèle ; pas de vérification d'email (typo = compte perdu, cf. P4). **Idéal** : inscription = email seul → lien magique envoyé, mot de passe optionnel ensuite (le modèle token existe déjà, autant s'aligner dessus).

**Création de groupe / invitation d'amis** — N'existe pas en tant que telle : l'admin invite (`/admin/participants`, email + lien) ou les gens s'auto-inscrivent. Pour le cas RESA c'est suffisant ; ❌ il manque juste un moyen pour un participant de partager `/rejoindre` ("Invite un collègue" avec lien copiable). Pour l'ambition "groupes d'amis", voir §11.

**Faire un pronostic** — ✅ Le meilleur parcours de l'app (cf. §2). ❌ "Score requis" comme libellé d'état vide est sec ; après sauvegarde, aucun renforcement (pas de micro-célébration de complétion de section) ; le risque silencieux : score nul saisi en phase finale sans qualifié choisi → rien n'est sauvegardé si l'utilisateur quitte malgré l'erreur inline. **Idéal** : barre de progression globale persistante + toast de complétion de section ("Match 1 : 24/24 🎉") + auto-focus du choix qualifié.

**Modifier un pronostic** — ✅ Identique à la saisie, modifiable jusqu'au verrou — c'est le bon modèle. ❌ Rien n'indique la date de son dernier prono ni que la modification est possible/normale (certains n'oseront pas toucher).

**Consulter les résultats** — ✅ Détail match riche (scoreboard, mon prono, répartition, table complète triée par points). ❌ Dépend de l'encodage manuel de l'admin sans gestion d'attente (cf. P6) ; pas accessible depuis la home (les items "Matchs du jour" ne sont pas cliquables — seuls les cards pronos verrouillées ont un lien "Détail") ; couleurs de la barre communautaire ambiguës (team2 en gris proche du gris "nul").

**Consulter le classement** — ✅ Podium, avatars, ligne "moi" surlignée, liens vers profils. ❌ Un seul classement (P7), pas d'évolution, pas de raccourci vers sa propre ligne (50 rangs à scroller), cagnotte vide de contenu (P3).

**Revenir après plusieurs jours** — ❌ Le parcours le plus faible : aucun email automatique, pas de "ce que tu as raté", home muette hors jours de match. **Idéal** : email récap post-journée + bloc home "Depuis ta dernière visite" + badge de points frais sur l'icône Classement.

---

## 6. Analyse UI détaillée

**Cohérence** : le design system de [resa.css](app/static/css/resa.css) est bon (tokens, chips, cards, boutons) mais contourné en permanence par des styles inline dans les templates ([home.html:22-29](app/templates/home.html), [bonus.html](app/templates/bonus.html), [profile.html](app/templates/profile.html)…). Trois "langues" de bannières coexistent : `.banner acc/red/soft`, des divs stylées à la main (encart pré-tournoi bleu #EFF6FF — seule intrusion de bleu dans toute la palette), et `.home-cta`. → Standardiser : un composant bannière, un composant CTA-card, et bannir le bleu hors liens.

**Hiérarchie** : la home met le paiement (information froide) au-dessus de l'action prioritaire. Les chiffres clés (rang, points) sont en 12px dans `.sub1` alors qu'ils sont la première chose qu'un joueur cherche. → Hero compact : rang + points + évolution en gros, actions ensuite.

**Couleurs** : palette saine, orange réservé à l'interactif — respecté. Points faibles : `--n400` en texte (contraste), team2 gris dans `.combar` (lisibilité), `chip.warn` #9A6A00 sur #FDF0D8 correct mais petit.

**Typographie** : trio Nunito/Inter/Caveat réussi ; Caveat utilisé à bon escient (titres émotionnels, états vides). Problème : l'échelle descend trop bas (9px dans `.tbl th`, 10px partout) — cf. P10.

**Boutons & formulaires** : `.btn`/`.field` solides (44px, focus ring). Les `confirm()`/`alert()` natifs (soumission pré-tournoi, validations admin) cassent le ton soigné du reste → modale maison légère.

**Cartes & densité** : bonne discipline d'espacement (`gap:14px`). La carte de match prono est dense mais lisible. La table "Tous les pronostics" du détail match passera mal sur 360px avec 4 colonnes → 2 lignes par joueur ou masquer la colonne "Résultat prédit" (déductible du score).

**États** : verrouillé/enregistré/à faire bien distingués (dots + chips). Manque : état "en cours" (P6), état succès global (P5), skeletons (non nécessaires en SSR).

**Mobile** : structure exemplaire (nav basse 5 items, colonne unique, inputs numériques `inputmode`). Trois défauts : tooltips porteuses de sens sur cibles 17px ; nav haute affichée par JS au resize ([participant_base.html:44-52](app/templates/participant_base.html)) au lieu d'une media query CSS (flash sans JS) ; "Règlement" absent de la nav mobile (acceptable, accessible via la home).

**Qualité perçue** : pas de favicon, pas de `theme-color`, pas de manifest PWA, pas d'`apple-touch-icon` ([base.html](app/templates/base.html)) — sur un produit utilisé tous les jours depuis l'écran d'accueil d'un téléphone, c'est le premier signal "artisanal". Pas de drapeaux ni d'identité visuelle des équipes : 104 matchs en texte brut, scannabilité faible (un mapping emoji 🇧🇷🇫🇷 suffit).

---

## 7. Opportunités de gamification

**Existant** : classement + podium, série en cours (🔥 ≥ 4), taux de réussite, scores exacts, meilleur groupe, comparaison 1-vs-1, répartition communauté. Base saine.

**Priorisé, du plus rentable au plus accessoire** :
1. **Récap post-journée** (email + carte home) : "+6 pts hier · 2 scores exacts dans le groupe · tu passes 8e (+3)". Le moment de dopamine n°1, déclenché par l'encodage des résultats — toutes les données existent déjà. *Effort moyen, impact maximal.*
2. **Classements multiples + remontada** (P7) : redonne un enjeu aux rangs 10–50. La remontada est l'anti-churn parfait de mi-tournoi — la marketer dès la fin des groupes ("le classement remontada démarre : tout le monde repart de zéro").
3. **"Qui peut me rattraper"** : sur la home, écart avec le joueur devant/derrière ("Il te manque 3 pts pour doubler Marie"). Trivial à calculer depuis `get_rankings`.
4. **Classement inter-départements** : déjà promis dans l'UI, données déjà collectées (`department`) — moyenne des points par département. Rivalité d'open-space garantie. *Effort faible.*
5. **Badges** : le CSS `.bdg` existe, le SPEC liste 7 badges (Sniper, En série, Fidèle au poste…). Commencer par 4 calculables en SQL simple, affichés sur le profil avec versions grisées + condition visible.
6. **Suspense pré-match** : la répartition communauté n'est visible qu'après coup d'envoi (anti-triche, bien) — mais après le verrou de SON prono, montrer "32% ont parié comme toi" crée le frisson sans biais.
7. **Stats fun de fin de phase** : "l'équipe qui t'a le plus trahi", "le prono le plus solitaire gagnant" — contenu de conversation pour la machine à café, à pousser par email entre les phases.

---

## 8. Quick wins

| # | Quoi | Où | Pourquoi | Effort | Impact |
|---|------|-----|----------|--------|--------|
| 1 | Fusionner brouillon/soumission pré-tournoi (P1) | `pre_tournament.html`, `pages.py`, `scoring.py:304` | Évite des pertes de points injustes dès demain | Faible | Élevé |
| 2 | Afficher les montants de la cagnotte (P3) | `ranking.html`, `rules.html` | Réactive le motivateur n°1 | Faible | Élevé |
| 3 | État "Tout est à jour" + prochain match à venir (P5) | `home.html`, `pages.py` (requête prochain match futur) | Home utile tous les jours | Faible | Élevé |
| 4 | Statut "En cours" vs "Terminé" (P6) | `predictions.html`, `home.html` | Supprime une confusion quotidienne | Faible | Moyen |
| 5 | "Lien perdu ?" → renvoi du lien par email (P4) | `login.html` + route dans `pages.py` (réutilise `send_invitation`) | Débloque les accès perdus sans ticket | Faible/Moyen | Élevé |
| 6 | Drapeaux emoji des équipes | dict dans `players.py`/`constants.py` + templates | Scannabilité ×2 sur 104 matchs, coût quasi nul | Faible | Moyen |
| 7 | Favicon + theme-color + manifest PWA | `base.html` + `app/static/` | Qualité perçue, installation écran d'accueil | Faible | Moyen |
| 8 | Items "Matchs du jour" cliquables vers le détail | `home.html` | Raccourci vers l'écran le plus social | Faible | Moyen |
| 9 | Une seule bannière prioritaire sur la home (P8) | `home.html` | Fin de la cécité aux bannières | Faible | Moyen |
| 10 | "Il te manque X pts pour doubler Y" | `home.html` (données de `get_rankings`) | Tension compétitive personnalisée | Faible | Moyen/Élevé |
| 11 | Corriger le CHECK `bonus_questions.phase` (+`round_of_16`,`final`) | `database.py` (migration) | Débloquera l'admin aux huitièmes | Faible | Moyen (différé) |
| 12 | Nav desktop en media query CSS (suppr. JS resize) | `participant_base.html`, `resa.css` | Supprime le flash, robuste sans JS | Faible | Faible |

---

## 9. Proposition de refonte idéale

**Navigation** (structure actuelle conservée, contenu revu) : Accueil · Pronos · Classement · Profil · Bonus en nav basse — c'est la bonne structure, ne pas la toucher. Ajouter un badge numérique (pas juste un point) sur Pronos (n à compléter aujourd'hui/demain) et Bonus.

**Accueil = cockpit en 4 zones fixes** :
1. *Hero* : avatar, "8e · 42 pts · ▲2 depuis hier" en gros, mini-sparkline de forme.
2. *Prochaine action* (une seule) : urgence match > pré-tournoi > bonus > "✅ À jour — prochain match vendredi 18h" avec compte à rebours.
3. *Le fil* : matchs du jour (cliquables, états en cours/attente/terminé avec points), sinon "depuis ta dernière visite" (points gagnés, mouvements du top 3, ta variation).
4. *Course* : top 3 + toi + qui te précède/suit avec écarts ("3 pts pour doubler Marie").

**Pronos** : header sticky avec progression globale ("87/104") ; premier onglet "À faire (n)" transversal ; filtre par équipe ; reste identique (le geste de saisie ne doit pas changer).

**Classement** : onglets Général / Groupes / Phase finale / Bonus / Remontada / Départements ; colonne évolution ↑↓ ; bouton flottant "Ma position" ; cagnotte chiffrée par catégorie en tête d'onglet.

**Résultats / détail match** : timeline du jour (hier-aujourd'hui-demain) ; détail match enrichi d'un encart "ce que ça change au classement" (qui a pris/perdu des places sur ce match).

**Profil** : ajouter la forme récente (`.bars` déjà en CSS), 4 badges, et compléter la comparaison ("il te faudrait X pts de plus par match pour le rattraper d'ici la finale").

**Mobile** : PWA installable (manifest + icônes + theme-color), tailles plancher 13px, tooltips remplacées par des lignes d'aide visibles là où l'info est critique (barème sur la carte match).

**Composants à standardiser** : `Banner(kind)`, `CtaCard`, `MatchRow`, `RankRow`, `StatCard`, `Modal` (remplace `confirm()`/`alert()`), macros Jinja pour le podium (actuellement triplé à la main dans `ranking.html`) et les avatars (bloc avatar/initiales dupliqué 6 fois).

**Rétention** : récap email post-journée (opt-in déjà en base) ; rappel J-1 ; emails d'événements ("tu viens de prendre la tête", "la remontada commence").

---

## 10. Roadmap recommandée

### Phase 1 — Stabilisation UX (cette semaine — avant/pendant les premiers matchs)
**Objectif** : ne perdre personne pendant la première semaine, éliminer les pièges.
- Quick wins 1–9 du §8 (brouillon pré-tournoi en tout premier, **avant la deadline de demain soir**).
- Bannière paiement : uniquement si impayé, avec instructions.
- Plancher typographique 13px sur les textes porteurs de sens.
- **Zones de code** : `app/templates/*.html` (home, pre_tournament, predictions, ranking, login), `app/routers/pages.py`, `app/scoring.py` (1 ligne), `app/static/css/resa.css`, `app/templates/base.html`.
- **Impact** : pré-tournoi sans litige, home utile au quotidien, accès jamais bloqué.

### Phase 2 — Refonte UI + classements (pendant la phase de groupes, ~2 semaines)
**Objectif** : qualité perçue et tension compétitive pour tout le peloton.
- Classements multiples + évolution quotidienne (snapshot des rangs par jour : petite table `ranking_snapshots`) + départements.
- Récap post-journée (carte home + email déclenché à l'encodage des résultats).
- Migration des styles inline vers des classes/macros ; modale maison ; refactor podium/avatars.
- Détail match : couleurs de la barre communautaire, table responsive, lien depuis la home.
- PWA complète ; layout desktop 2 colonnes sur la page pronos.
- **Zones de code** : `app/scoring.py` (rankings par périmètre), nouvelle table + petit module snapshot, `app/templates/ranking.html`, `match_detail.html`, `resa.css`, macros Jinja partagées, `app/mail.py`.
- **Impact** : les rangs 10–50 ont une raison de revenir ; l'app "fait pro".

### Phase 3 — Engagement produit (avant/pendant la phase finale)
**Objectif** : faire de la phase finale un événement.
- Rappels automatiques (scheduler) + emails d'événements.
- Remontada mise en scène ("nouveau jeu dans le jeu") + classement phase finale avec sa cagnotte.
- Badges (4 → 7), stats fun de fin de phase, "qui peut me rattraper" sur la home.
- Questions bonus pour huitièmes/finale (fix du CHECK) + privilégier le type "choix".
- Optionnel à fort levier : encodage semi-automatique des résultats via une API football — supprime le dernier gros point artisanal (l'attente de l'admin).
- **Zones de code** : nouveau module `app/scheduler.py`, `app/mail.py`, `app/database.py` (migration CHECK), `app/scoring.py` (badges), `app/templates/profile.html`, `home.html`.
- **Impact** : pic d'engagement sur la phase finale au lieu de l'essoufflement habituel des pools.

---

## 11. Questions stratégiques à trancher

1. **Produit one-shot ou plateforme ?** Le brief décrit "créer/rejoindre un groupe" ; le code est mono-communauté RESA. Si l'ambition est multi-groupes (familles, amis, autres entreprises), il faut le décider *avant* la Phase 2 : cela impacte l'auth (comptes au lieu de tokens), le schéma (table `groups`, scoping de toutes les requêtes) et l'admin (par groupe). Mon conseil : finir RESA 2026 tel quel, capitaliser l'apprentissage, et décider en juillet si on généralise pour l'Euro 2028 / la CdM 2030.
2. **Cagnotte** : qui décide les montants et quand ? Chaque jour de flou coûte de l'engagement.
3. **Notifications** : email seulement, ou PWA + push ? L'opt-in actuel (`email_opt_in`, défaut activé) suffit-il côté consentement interne ?
4. **Pronos de phase finale ouverts dès maintenant** sur des affiches inconnues ("Vainqueur Groupe A – 3e Groupe B") : assumé (stratégie long terme) ou à verrouiller jusqu'à la connaissance des équipes ? Aujourd'hui c'est ouvert avec une simple note — clarifier la règle dans le règlement.
5. **Résultats** : l'encodage restera-t-il manuel (avec l'engagement de réactivité que ça implique en soirée de matchs) ou investit-on dans une API ?
6. **Confidentialité interne** : photos, bios et comparaisons sont visibles de tous les inscrits ; le lien token circule par email. Acceptable en interne RESA, mais à valider (RGPD/IT) avant toute ouverture au-delà.
7. **Questions bonus texte libre** : les garder (avec le risque d'arbitrage accents/orthographe) ou imposer le type "choix" ?

---

*Audit basé sur la lecture exhaustive de `app/` (routers, templates, CSS, JS, scoring, mail), du SPEC (`project/uploads/SPEC.md`) et du README. Non testé en navigateur : les points de rendu (contrastes réels, débordements sur petits écrans, comportement des tooltips tactiles) sont à confirmer manuellement sur un téléphone — notamment la table du détail match sur 360px et les onglets pronos sur iPhone SE.*
