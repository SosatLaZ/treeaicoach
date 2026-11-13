# LOOK 5.0 — « change tous les affichages pour faire le truc parfait » (agent LOOK, 13 nov 2026)

Fichiers : `Theme.lua` (système de design), `Shortcuts.lua` (bande d'actions rapides), `Window.lua` / `WindowPages.lua`
(fenêtre), `Options.lua` (réglage en 3 étapes), `Hub.lua` (menu de la pastille), `Tour.lua`, `Bindings.xml`.
Tests : `tests/wow/test_look5.py` + `look5_scenario.lua` (nouveaux), `test_strip.py` + `strip_scenario.lua` (réécrits).

## 1. Pourquoi le joueur ne voyait pas « les petites barres rapides » (preuve avant de corriger)

Rejoué sur la géométrie du vrai client (`tests/wow/geometry.lua`), 3 résolutions × 3 échelles d'interface × 2
dispositions par défaut (cadres d'unité en haut à gauche « classic » ; places du mode Édition + conteneur `PartyFrame`
« modern »), groupe de 4, colonne « Suivi Forever ». La bande 4.3.6, mesurée :

| écran | échelle | bande 4.3.6 (px réels) | bande 5.0 (px réels) |
|---|---|---|---|
| 1920×1080 | 0,64 | **521×27**, texte 9 px, fond à 42 % | 507×68, mots 11 px, boutons pleins |
| 1920×1080 | 0,90 | 732×37 | 642×86 |
| 1920×1080 | 1,15 | 936×48, **sous la boussole** (solo) | 548×58 (icônes), jamais sous la boussole |
| 1366×768 | 0,64 | 371×19 | 454×61 (taille plancher ×1,4) |

Causes trouvées (toutes corrigées, chacune a son test) :
1. **Trop petite, trop transparente** : 27 px réels de haut en 1080p à l'échelle 0,64, texte de 9 px, fond à 42 % sur
   un décor clair (Tarides). → boutons de 42 unités, mots de 11, fond de bouton plein (0,88), liseré or quand choisi,
   `S.AutoScale()` : taille plancher en pixels réels (jamais moins de ~36 px de bouton, 26 en compact).
2. **Elle changeait de place** : replacée à chaque `GROUP_ROSTER_UPDATE` / cible / `LAYOUT_DONE` — sa place sautait
   dans la même session (à 0,64 : 144,156 seul → 488,6 en groupe ; à 0,90 : 144,165 → 272,165). → place **stable** : elle ne bouge que si un cadre vient vraiment dessus
   (`S.PlaceStrip` : `S.Blockers` vide = on ne touche à rien).
3. **La boussole TreeAI venait se poser dessus** (le placement du cœur ne connaît pas la bande) : à l'échelle 1,15 la
   bande finissait sous la boussole. → place d'origine de la boussole toujours réservée + `S.WatchCompass` (crochets
   `OnShow` / `SetPoint` sur NOTRE cadre, regroupés 0,5 s) : la bande s'écarte une fois.
4. **Client récent** : pas de `PartyMemberFrame1-4`, un conteneur `PartyFrame` minuscule tant qu'on est seul → la bande se
   posait là où le groupe arrive. → la place de 4 membres est réservée sous son coin (`S.RectOf`).
5. **Suivis qui grandissent** (Suivi Forever, suivi du jeu) : leur colonne est réservée via `T._LightRects` (la bande ne se
   pose plus juste dessous).
6. **Écran bondé** (1,15 + groupe + cible + Suivi Forever) : « la place qui cache le moins » tombait sur la boussole. →
   ordre des essais : mots à gauche → icônes à gauche → mots partout → icônes partout → icônes dans les 40 % hauts.
7. Restes d'un ancien client (place enregistrée, taille, verrou, « cacher en combat ») : `/tai interface reset` remet
   tout (bande, fenêtre, puis boussole / alerte par le cœur) ; une fois par personnage la bande 5.0 reprend sa place auto.
8. Plus aucune attente de fin de combat : la bande est faite de cadres simples (créée, posée, repeinte en combat —
   `/reload` en combat, module rallumé en combat : testés).

## 2. Système de design (Theme.lua, `Th.V = 5`)

```
JETON thème = Sombre (v5)
JETON grille = 4 px, liseré = 1 px
JETON polices = S 10 · N 11 · M 12 · L 16 · H 20 px
JETON espaces = xs 2 · s 4 · m 8 · l 12 · xl 16
JETON couleur bg = 0.05 0.06 0.08, alpha 0.97
JETON couleur panel = 0.09 0.10 0.13, alpha 0.97
JETON couleur small = 0.04 0.05 0.07, alpha 0.50
JETON couleur tile = 0.09 0.10 0.13, alpha 0.88
JETON couleur tileOn = 0.36 0.27 0.06, alpha 0.94
JETON couleur border = 0.78 0.62 0.28, alpha 0.90
JETON couleur smallBorder = 0.78 0.62 0.28, alpha 0.55
JETON couleur gold = 1.00 0.82 0.20, alpha 1.00
JETON couleur on = 0.30 0.85 0.40, alpha 1.00
JETON couleur off = 0.58 0.58 0.60, alpha 1.00
JETON couleur count = 0.78 0.13 0.10, alpha 1.00
JETON couleur key = 0.55 0.80 1.00, alpha 1.00
```
- **Fonds** : menus / fenêtre opaques (0,97) ; petits cadres posés sur l'écran à 50 % (jeton `small`), leurs boutons pleins.
- **Un bouton** : `Th:ActionButton(parent, { layout = "tile" | "bar" | "icon", icon, label, onClick, onEnter })` →
  `b:Set{ label, icon, count, key, active, disabled }`, `b:Fit()`. États : choisi (fond or sombre + liseré or), coupé
  (gris, icône désaturée), survol (+10 % blanc), enfoncé (voile sombre, scripts de souris — aucun OnUpdate).
- **Une pastille ON / OFF** : `Th:Pill(parent, { label, onClick })` → `p:SetOn(bool)`.
- **Un nombre** : `Th:CountBadge(parent)` → `badge:Set(n)` (rouge, « 99+ »).
- **Une carte** : `Th:Card(parent, { kind = "card" | "small", title, w, h })` (`small` = sous un cadre du jeu ; les cartes
  marchand / HV / banque qui font `Th:Skin(f, "small")` héritent déjà des nouveaux jetons).
- **Une ligne / une bascule / un choix** : dans la fenêtre, `Ly.row`, `Ly.toggle`, `Ly.choice` (ci-dessous).
- **Un jeu d'icônes** : `Th.ICONS[rôle]` / `Th:Icon(rôle)` — uniquement des textures du jeu.
- **Légende des sacs** : `Th.LEGEND.bags` (garder / utiliser / vendre / HV / banque / jeter — jetons `keep`, `use`,
  `sell`, `ah`, `bank`, `destroy`), `Th:Legend(parent, items)`, `Th:LegendLines(items)`, `Ly.legend(items)`.
- **Alertes** : une à la fois, 8 à 15 s (`Th:ToastSeconds`), posée sous la boussole quand elle est à l'écran (sinon la
  place du cœur ; jamais sur la bande ; place du joueur respectée).

### Contrats pour les autres agents (tout est gardé : `if T.X and T.X.f then`)
- **Page de la fenêtre** : `T.Window.AddPage({ id, label, icon, title, desc, sub, top, render, action, status, badge, guard })`
  depuis VOTRE fichier. `id = "bags"` (Sacs) entre dans la navigation dès qu'elle est inscrite (sinon « Sacs » redirige
  vers Équipement). `top()` → ≤ 3 tuiles `{ titre, fn, infobulle, icon = , sub = , count = , active = , disabled = }`.
- **Mise en page `Ly`** (inchangée + ajouts) : `gap, text, header(titre, clé, n, icône, repliéParDéfaut), row, hero, step,
  quest, buttons, empty, tabs, tiles` + V5 : `Ly.actions(list, cols)`, `Ly.toggle{ label, desc, on, set }`,
  `Ly.choice{ {titre, phrase, fn, active=} }`, `Ly.card(titre, lignes)`, `Ly.note(texte, "info"|"warn"|"good")`,
  `Ly.kv(nom, valeur)`, `Ly.legend(items)`. `Ly.buttons` réduit seul son nombre de colonnes si un mot ne tient pas.
- **Bande** : `T.Shortcuts.SetAction("sacs" | "equip" | "metiers" | …, { fn = function(bouton) … return true end,
  count = function() return n end, active = function() … end, tip = function() return { "ligne" } end })` : l'action
  directe, la pastille et l'infobulle du bouton viennent du module qui sait. Sans cela : « Sacs » ouvre les sacs du jeu
  (`OpenAllBags`), « Équipement » ouvre sa page, « Métiers » lance / quitte la session métier.
- **Guidage des quêtes** : la pastille appelle `T:SetQuestGuide(on)` / lit `T:QuestGuideOn()` (STABLE) et écoute
  `QUEST_GUIDE_CHANGED`. Sans eux : elle ne fait que griser les boutons de quêtes de la bande.

## 3. Ce que le joueur voit — à l'écran (1920×1080, chasseur 30, groupe de 4, cible, colonne Suivi Forever)

```
  TreeAIStrip : gauche 406, haut 203, 507×68 unités = 507×68 px réels
  TreeAICompassFrame : gauche 864, haut 73, 170×95 unités = 170×95 px réels
  TreeAIMinimapButton : gauche 1750, haut 122, 31×31 unités = 31×31 px réels
  bande : Quêtes [Guidage quêtes ON] : Rendre (2) · Prendre · Suivant · Zone (3) * · Vite (2)  |  Perso : Sacs · Équipement  |  Groupe  |  TreeAI
  infobulle « zone » : Tout faire ici · choisir ma zone / Un clic : tout faire dans ta zone, et la liste des zones pour toi, classées (quêtes, trajet, XP/h, groupe). / Mode choisi. / Touche : aucune (« Touches conseillées » dans …
  infobulle « guide » : Guidage des quêtes : allumé / Un clic : coupe les boutons de quêtes de la bande. / Touche : aucune (« Touches conseillées » dans les réglages)
  infobulle « prendre » : Quêtes à prendre / Un clic : le trajet va chercher les meilleures quêtes près de toi (XP par minute trajet compris, suites, groupe, niveau). / Touche : aucune (« Touches conseillées » dans les réglages) …
  infobulle « groupe » : Avec le groupe / Un clic : les quêtes que tout le groupe peut faire ensemble ; seul : la page pour rejoindre un groupe. / Touche : aucune (« Touches conseillées » dans les réglages) / Clic droit : la page…
  infobulle « vite » : Monter vite / Un clic : une seule consigne « fais ça maintenant », pour le plus d'XP par heure. / Touche : aucune (« Touches conseillées » dans les réglages) / Clic droit : la page de la fenêtre
  infobulle « sacs » : Sacs / Un clic : tes sacs s'ouvrent avec ce qui est à vendre, à garder, à mettre à l'HV ou à la banque. / Touche : aucune (« Touches conseillées » dans les réglages) / Clic droit : la page de la fenêtre
  infobulle « equip » : Équipement / Un clic : ce que tu peux équiper de mieux tout de suite, et les prochaines pièces à viser. / Touche : aucune (« Touches conseillées » dans les réglages)
  infobulle « fenetre » : Fenêtre TreeAI / Un clic : ouvre ou ferme la fenêtre TreeAI (toutes les pages, la recherche, les réglages). / Touche : aucune (« Touches conseillées » dans les réglages)
  infobulle « rendre » : Rendre mes quêtes / Un clic : le trajet passe par les PNJ qui attendent tes quêtes finies, au plus court (pierre de foyer et vols compris). / • La revanche de test / • Crocs de test / Conseillé maintenant…
  infobulle « suivant » : Étape suivante / Un clic : passe à l'étape suivante (celle-ci est faite, ou tu veux la sauter). / Touche : aucune (« Touches conseillées » dans les réglages) / Clic droit : la page de la fenêtre
  alerte : « Quête finie : La revanche de test — va la rendre » — TreeAIAlert : gauche 799, haut 174, 300×23 unités = 300×23 px réels
```
Lecture critique (corrigé après ce relevé) : la pastille « Vite (2) » comptait rendre + prendre, déjà montrés à côté →
plus de pastille sur « Vite » ; chaque infobulle dit maintenant ce que compte sa pastille (« Pastille : 2 quêtes finies à
rendre », « … 3 quêtes en cours dans ta zone »). « Sacs » / « Équipement » n'ont pas de pastille tant que leurs modules
n'ont pas branché `S.SetAction`. Touche trop longue pour le coin du bouton : abrégée (« C-M-3 »), entière dans l'infobulle.

### Matrice 3 résolutions × 3 échelles × 2 dispositions (toutes vertes : rien sur un cadre du jeu, rien hors écran)

| résolution | échelle | disposition | interface (unités) | bande (gauche,haut) | bande (px réels) | forme | fenêtre (px réels) |
|---|---|---|---|---|---|---|---|
| 1920x1080 | 0.64 | classic | 2133×1200 | 406,197 | 507×68 | mots | 738×486 |
| 1920x1080 | 0.64 | modern | 2133×1200 | 8,6 | 507×68 | mots | 738×486 |
| 1920x1080 | 0.90 | classic | 1517×853 | 8,436 | 642×86 | mots | 944×683 |
| 1920x1080 | 0.90 | modern | 1517×853 | 8,6 | 642×86 | mots | 944×683 |
| 1920x1080 | 1.15 | classic | 1187×668 | 406,203 | 548×58 | icônes | 1035×820 |
| 1920x1080 | 1.15 | modern | 1187×668 | 8,6 | 548×58 | icônes | 1035×820 |
| 2560x1440 | 0.64 | classic | 2133×1200 | 406,156 | 608×82 | mots | 984×648 |
| 2560x1440 | 0.64 | modern | 2133×1200 | 8,6 | 608×82 | mots | 984×648 |
| 2560x1440 | 0.90 | classic | 1517×853 | 8,436 | 856×115 | mots | 1259×911 |
| 2560x1440 | 0.90 | modern | 1517×853 | 8,6 | 856×115 | mots | 1259×911 |
| 2560x1440 | 1.15 | classic | 1187×668 | 406,203 | 731×78 | icônes | 1380×1093 |
| 2560x1440 | 1.15 | modern | 1187×668 | 8,6 | 731×78 | icônes | 1380×1093 |
| 1366x768 | 0.64 | classic | 2134×1200 | 406,203 | 454×61 | mots | 525×346 |
| 1366x768 | 0.64 | modern | 2134×1200 | 8,6 | 454×61 | mots | 525×346 |
| 1366x768 | 0.90 | classic | 1518×853 | 488,6 | 339×36 | icônes | 671×486 |
| 1366x768 | 0.90 | modern | 1518×853 | 8,6 | 507×68 | mots | 671×486 |
| 1366x768 | 1.15 | classic | 1188×668 | 406,203 | 390×41 | icônes | 736×583 |
| 1366x768 | 1.15 | modern | 1188×668 | 8,6 | 390×41 | icônes | 736×583 |

## 4. Ce que le joueur voit — la fenêtre (une ligne par bloc, dans l'ordre)

```
navigation : Quêtes · Trajet · Groupe · Donjons · Équipement · Métiers · HV · Chasseur · Conseils · Réglages
PAGE quests — « Quêtes » · Mode : Tout faire ici · Zone : Contreforts de Hautebrande · trajet prévu ≈ 2 min
    [action] Rendre mes quêtes
    [action] Quêtes à prendre
    [action] Tout faire ici
    [toggle] Guidage des quêtes
    [header] Prochaine étape
    [step] Tue « Plume » 2/6
    [header] Ma zone
    [row] Zone : Contreforts de Hautebrande · automatique (ta zone)
    [button] Choisir ma zone
PAGE route — « Trajet » · Zone de travail : Contreforts de Hautebrande
    [action] Recalculer
    [action] Choisir ma zone
    [action] Conseille-moi
    [hero] Trajet prévu : ≈ 2 min · 0 m
    [row] Ordre des arrêts : Groupe
    [header] Arrêts
    [step] Tue « Plume »
    [step] Rendre « Plumes de test »
    [step] Rendre « Crocs de test »
PAGE group — « Groupe » · Groupe de 4
    [action] Partager mes quêtes (3)
    [action] Mode « Avec le groupe »
    [header] Partager
    [text] « La revanche de test », « Crocs de test », « Plumes de test »
    [button] Partager (3)
    [header] Équipement du groupe
    [row] Toi · Chasseur 30 · faible : main droite (vide), distance (vide) et torse (vide) (+8)
    [row] Amie · Prêtre 28 · équipement inconnu (pas de réponse du jeu)
    [row] Compagnon · Guerrier 29 · équipement inconnu (pas de réponse du jeu)
PAGE dungeon — « Donjons » · Tes donjons à ton niveau (clic sur une carte : le détail)
    [row] Suivi en donjon coupé (prochain boss, butin du groupe en direct, bilan) : fonction bêta.
    [button] Tous les donjons
    [dcard] Profondeurs de Brassenoire
    [dcard] Prison de Hurlevent
    [dcard] Kraal de Tranchebauge
    [dcard] Gnomeregan
    [dcard] Excavations : les Paluns
    [dcard] Cité de Dalaran
    [dcard] Monastère écarlate
PAGE gear — « Équipement » · 
    [row] Réparations : équipement à 12 %
    [row] Potions de soins : 3 sur 5 conseillées
    [text] Le module Équipement est coupé (fonction bêta) : « Allumer Équipement », en bas à droite.
PAGE bags — « Sacs » · 
    [action] Vendre (sûr)
    [action] Ranger
    [action] Réparer
    [note] Note d'essai.
    [card] Carte
    [row] Places libres
    [legend] À garder
    [legend] À utiliser
    [legend] À vendre au marchand
PAGE prof — « Métiers » · Quoi créer, combien acheter (sacs + banque + courrier), où aller
    [action] Courses en ville
    [action] Me concentrer sur les métiers
    [text] Pas encore de métier : apprends-en un chez un maître de métier (en ville).
PAGE hv — « Hôtel des ventes » · 
    [text] Le module Hôtel des ventes est coupé (fonction bêta) : « Allumer l'HV (bêta) », en bas à droite.
PAGE hunter — « Familier » · 
    [tab] Familier
    [tab] Talents
    [row] Familier : Crocdur · vie 83 %
    [header] Chasseur : aides
    [row] Ton familier a 5 niveaux de retard (niv 25) : fais-le tuer des monstres de ton niveau
    [header] Familier
PAGE pet — « Familier » · 
    [tab] Familier
    [tab] Talents
    [row] Familier : Crocdur · vie 83 %
    [header] Chasseur : aides
    [row] Ton familier a 5 niveaux de retard (niv 25) : fais-le tuer des monstres de ton niveau
    [header] Familier
PAGE talents — « Talents » · 
    [tab] Familier
    [tab] Talents
    [text] Le conseiller de talents est coupé (fonction bêta) : « Allumer Talents », en bas à droite.
PAGE coach — « Conseils pro » · 1 conseil vrai en ce moment · 20 conseils, 42 techniques
    [text] Le geste d'un joueur expert au bon moment, chiffres à l'appui. Un seul conseil à la fois dans « Maintenant » ; ici, tout ce qui est vrai en ce moment, chaque conseil (clic = ON / OFF) et les techniques à appliquer.
    [header] En ce moment
    [row] Ton familier a 5 niveaux de retard (niv 25) : fais-le tuer des monstres de ton niveau
    [row] Aides de chasseur (familier, pièges, Feindre la mort) : page Chasseur
    [header] Pull et combat
    [header] Repos et ressources
    [header] Familier
    [header] Quêtes et trajet
    [header] Argent et sacs
PAGE settings — « Outils » · 
    [header] Outils
    [row] Chercher (bêta, coupé)
    [row] Macros
    [row] Fenêtre « À faire »
    [row] Boussole
    [row] Journal de la boussole
    [row] Prêt pour le donjon ?
    [row] Pilote auto : pourquoi ?
    [header] Réglages et diagnostic
PAGE display — « Affichage » · Une case par élément. D'office : guidage, bande d'actions, boussole, pastille et ses repères.
    [action] Déplacer les cadres
    [action] Tout remettre en place
    [header] Toujours à l'écran
    [toggle] Guidage des quêtes
    [toggle] Bande d'actions rapides
    [toggle] Bande en mode compact
    [toggle] Cacher la bande en combat
    [toggle] Alertes (une à la fois, 8 à 15 s)
    [toggle] Boussole (en haut, au centre)
PAGE profile — « Profil » · Deux profils. Tu peux changer quand tu veux, sans recharger.
    [choice] Simple
    [choice] Complet
    [text] Une fonction précise ? « Fonctions », à gauche : une ligne par fonction, un clic pour l'allumer ou la couper.
    [row] Fonctions allumées
PAGE keys — « Touches » · Échap → Options → Raccourcis → AddOns → TreeAI pour changer une touche
    [action] Touches conseillées
    [text] 26 raccourcis TreeAI, 26 sans touche. « Touches conseillées » pose une touche libre sur les plus utiles (jamais une touche déjà prise).
    [header] Toutes les touches
    [row] Raccourci combat : bandage
    [row] Raccourci combat : familier (appeler / ressusciter)
    [row] Raccourci combat : manger / boire
    [row] Raccourci combat : soigner le familier
    [row] Raccourci combat : tirer (marque + familier)
    [row] Raccourci quête : cibler l'objectif
PAGE macros — « Macros » · Préréglage Complet · Maîtrise des bêtes · rien n'est écrit sans ton clic
    [row] Nouvelles macros prêtes : clique pour installer
    [hero] Tes macros TreeAI
    [button] Simple
    [button] « Complet »
    [button] Pro
    [button] Mes 5 macros essentielles
    [header] Comment ça marche
    [text] 1. Installer : un clic sur « Tout mettre à jour » (ou sur une ligne orange) crée les macros dans la liste du jeu (Échap > Macros, onglet de ton personnage). Rien n'est écrit sans ton clic, jamais en combat.
    [text] 2. [Auto] : TreeAI la réécrit tout seul quand ton sac, tes sorts ou tes rangs changent (nouvelle nourriture, sort appris). [Fixe] : son texte ne change jamais. Chaque ligne dit à quoi elle sert et où la mettre (ex. « b…
PAGE spellkeys — « Sorts & barres » · 
    [text] Le conseiller de touches est coupé : « Allumer Touches », en bas à droite.
PAGE modules — « Fonctions » · 
    [row] Mode sûr : les fonctions bêta sont à activer dans le menu
    [text] Profil Simple (mode sûr) : seules les fonctions fiables tournent. Les fonctions « bêta » sont testées hors du jeu : allume-les une par une.
    [row] Mode sûr : seules les fonctions vérifiées tournent — clic : tout allumer (bêta compris)
    [row] Profil léger (essentiel, moins de lag) — clic
    [row] Coût des fonctions — clic : afficher
    [row] ON Thème
    [row] ON Autotest
    [row] OFF Chasseur (bêta)
    [row] OFF Rappels de buffs (bêta)
PAGE addons — « Mes addons » · Questie, Suivi Forever, Leatrix Plus, Details : ce que TreeAI fait à leur place
    [text] TreeAI fait le travail de tes autres addons quand il le peut ; ce qui manque est dit ici, sans détour.
    [text] Aucun des addons qu'il remplace n'est installé : rien à régler.
PAGE options — « Options » · Choisis une page à gauche, ou cherche un réglage en haut à droite.
    [button] ← Réglages
    [navhead] TreeAI
    [navrow] Accueil
    [navrow] Apparence
    [navrow] Sons
    [navhead] Quêtes et trajet
    [navrow] Barre de quête
    [navrow] Suivi TreeAI
    [navrow] Quêtes
PAGE tools — « Outils » · 
    [header] Outils
    [row] Chercher (bêta, coupé)
    [row] Macros
    [row] Fenêtre « À faire »
    [row] Boussole
    [row] Journal de la boussole
    [row] Prêt pour le donjon ?
    [row] Pilote auto : pourquoi ?
    [header] Réglages et diagnostic
Réglages, sous-liste : Affichage · Profil : Simple / Complet · Touches · Macros · Sorts & barres · Fonctions · Mes addons · Options · Outils
AFFICHAGE : Guidage des quêtes = à l'écran | Bande d'actions rapides = à l'écran | Bande en mode compact = coupé | Cacher la bande en combat = coupé | Alertes (une à la fois, 8 à 15 s) = à l'écran | Boussole (en haut, au centre) …
PROFIL Simple : Seulement ce qui est fiable et utile tout le temps : quêtes, trajet, boussole, bande d'actions, métiers, groupe. Rien de bêta, écran léger.
PROFIL Complet : Tout TreeAI, fonctions bêta comprises (HV, combat, radar, conseils avancés…) : plus d'aides, plus de choses à lire et à l'écran.
RECHERCHE « rendre » : Action : Rendre mes quêtes au plus vite | Action : Quêtes à prendre | Affichage : Icônes de quêtes (« ! » à prendre, « ? » à rendre)  (à l'écran) | Affichage : Bande d'actions rapides  (à l'écran) | Réglage…
RECHERCHE « sacs » : Action : Sacs | Page : Sacs | Page : HV | Affichage : Surbrillance dans les sacs  (à l'écran) | Affichage : Alertes (une à la fois, 8 à 15 s)  (à l'écran) | Affichage : Bande d'actions rapides  (à l'écran) | …
RECHERCHE « boussole » : Page : Réglages → Journal de la boussole | Page : Réglages → Outils | Affichage : Boussole (en haut, au centre)  (à l'écran) | Profil : Simple  (choisi) | Affichage : Guidage des quêtes  (à l'écran) | Rég…
RECHERCHE « compact » : Affichage : Bande en mode compact  (coupé) | Réglage : Actions rapides → Un mot sous chaque icône (décoché : mode compact, icônes seules) | Réglage : Radar → Radar compact (petit, 120 px) | Réglage : Suivi…
RECHERCHE « guidage » : Action : guidage des quêtes — le couper | Affichage : Guidage des quêtes  (à l'écran)
RECHERCHE « simple » : Page : Réglages → Macros | Page : Réglages → Profil | Profil : Simple  (choisi)
RECHERCHE « touches » : Page : Réglages → Touches | Page : Réglages → Sorts & barres | Réglage : Actions rapides → Touches conseillées : Alt-1 à Alt-0 (seulement les touches libres) | Réglage : Touches → Signaler les sorts import…
RECHERCHE « verrouiller » : Réglage : Actions rapides → Verrouiller la bande (elle ne bouge plus) | Réglage : Actions rapides → Verrouiller la barre d'actions | Réglage : Boussole → Verrouiller la boussole | Réglage : Radar → Ver…
RECHERCHE « combat » : Affichage : Barre de combat (manger, familier, pull…)  (coupé) | Affichage : Cacher la bande en combat  (coupé) | Profil : Complet | Affichage : Fenêtre « Dégâts »  (coupé) | Réglage : Actions rapides → Cac…
RECHERCHE « equipement » : Action : Équipement | Page : Équipement | Affichage : Bande d'actions rapides  (à l'écran) | Réglage : Équipement du groupe → Partager mon équipement et mes objets échangeables avec mes amis TreeAI | Ré…
RECHERCHE « erreurs » : Page : Réglages → Outils | Réglage : Autotest → Voir les dernières erreurs | Réglage : Erreurs (export) → Préparer le fichier d'erreurs | Outil : Erreurs | Outil : Journal de la boussole
```

## 5. Réglage en 3 étapes (1er lancement, « Passer » à tout moment, `/tai bienvenue`) et menu de la pastille

```
étape 1 — Bienvenue dans TreeAI  1/3 · Ton personnage
   Tu joues : Chasseur niveau 30. / Spécialisation : inconnue pour l'instant (TreeAI la lira sur tes talents, jamais au hasard). / TreeAI règle là-dessus ses conseils d'équipement, de talents et de macros. / Ta prochaine étape : …
   boutons : [C'est bien ça] [Ce n'est pas ça…]
étape 2 — Bienvenue dans TreeAI  2/3 · Ce que TreeAI affiche
   Choisis un profil : / Et ce que tu veux voir à l'écran (tout se change dans Réglages, page Affichage) :
   boutons : [> Simple <] [Complet] [Guidage quêtes : ON] [Bande d'actions : ON] [Boussole : ON] [Icônes de quêtes : ON]
étape 3 — Bienvenue dans TreeAI  3/3 · Tes touches
   Une touche par action rapide (en vert : déjà posée). / Alt-1 Rendre  ·  Alt-2 Prendre  ·  Alt-3 Suivant  ·  Alt-4 Zone  ·  Alt-5 Groupe  ·  Alt-6 Vite  ·  Alt-7 TreeAI  ·  Alt-8 Action suivante  ·  Alt-9 Sacs  ·  Alt-0 Équipem…
   boutons : [Poser les touches conseillées] [Voir toutes les touches]
groupes : rapide · jeu · outils · reglages
actions rapides : Guidage des quêtes [ON] · Rendre mes quêtes [2] · Quêtes à prendre · Tout faire ici [3] · Monter vite [2] · Sacs · Équipement · Avec le groupe
```

## 6. Touches

Une liaison par action (aucune d'office) : Rendre, Prendre, Suivant, Zone, Groupe, Vite, Fenêtre, **Action suivante**,
Sacs, Équipement, Métiers, Guidage ON / OFF, Mode suivant, Journal. « Touches conseillées » (bande, Réglages → Touches,
étape 3 du réglage) : Alt-1 … Alt-0 puis Alt-F1 … — seulement sur des touches libres, jamais Ctrl-chiffre (barre du
familier) ni Maj-1…6. « Action suivante » = la chose la plus utile d'après le jeu : session en cours → son étape ;
quêtes finies → les rendre ; plus rien à rendre → retour à la zone ; rien en cours et des quêtes à prendre → les prendre ;
sinon rien (« continue ton étape »). Jamais « Suivant » tout seul (ça saute un arrêt : choix du joueur).

## 7. Coût (session simulée, `tests/wow/session_sim.lua`)

Bande : aucun OnUpdate, aucune minuterie au repos ; événements regroupés (≥ 0,3 s ; `T:OnQuestLog` du cœur s'il existe) ;
peinture différentielle (`b:Set` ne touche que ce qui change), disposition seulement si un mot change ; UN bouton par
action qui change de forme (mots ↔ icônes : pas de 2e jeu de boutons) ; à la connexion / au `/reload` les boutons sont
créés 3 par image (1re version : 10 boutons d'un coup = pire image 121 kinstr > budget 115 ; maintenant 107 = la base) ;
le placement mesure chaque forme une fois (tailles gardées) au lieu de redisposer à chaque essai.
Mesuré (WARRIOR / modern / essentiel, 60 min) : pire image 107 kinstr (base 107), connexion 97 (base 96). Au repos
(HUNTER / forever / sur, 10 min) : Shortcuts < 1 kinstr/min, Window 1 (comme la base). Les dépassements restants de
`test_session_sim[HUNTER-forever-sur*]` (Core, Export, PathDraw…) existaient sur la base et ne sont pas dans mes fichiers.
Fenêtre : rien quand elle est fermée ; ouverte : la page visible seulement, ≤ 1 rendu / s, cadres recyclés.

### Hors de mes fichiers (petits morceaux gardés)
- `Core.lua` : la ligne `MODULE_INFO.Shortcuts` (description de la fonction).
- Scénarios d'autres zones mis à jour pour la nouvelle bande / navigation / page Quêtes / assistant : `uiredo`, `layout`,
  `menu`, `qaui`, `qaui_first`, `fix42`, `polish`, `see41`, `zonepick`, `coach` (une ligne chacun ou presque) + `test_layout.py`,
  `test_fix42.py`.
- Pages à onglets des autres modules (HV, Conseils, Talents…) : leurs 3 premières lignes « commande » deviennent
  automatiquement les grosses tuiles du haut (`W.RenderTab`) ; `icon` / `sub` sur la ligne = icône / sous-titre de la tuile.
- Une page inscrite `hidden = true, parent = "hv"` avec un id de la navigation (`bags`) y prend quand même sa place.

## 8. Pas fait / à vérifier dans le vrai jeu

- Jamais vu dans le vrai client : la place réelle des cadres de WoW: Forever (groupe, colonne Suivi Forever, « Sac à dos
  combiné »). Si la bande gêne : la glisser par sa poignée (place gardée), ou `/tai interface reset`.
- `OpenAllBags` est appelé au clic « Sacs » (fonction du jeu, non protégée) : à confirmer en combat.
- `GetPhysicalScreenSize` sert à la taille plancher ; absent → taille normale.
- L'alerte sous la boussole : ancrée à la position lue ; si la boussole est déplacée pendant qu'une alerte est affichée,
  l'alerte ne suit qu'à la suivante.
- Les pages Équipement / Sacs / Métiers / Groupe gardent leur contenu actuel : leurs agents les remplissent (tuiles `top`).
- Le libellé « Mode sûr » reste dans les lignes du cœur (page Fonctions) ; la fenêtre dit « Profil Simple (mode sûr) ».
- Le réglage en 3 étapes est reproposé UNE fois aux joueurs qui avaient fini l'ancien assistant (`db.setupV = 5`).
- Fenêtre rouverte pendant son fondu de fermeture (0,12 s) : elle reste ouverte (avant : elle se refermait seule).
- Pas fait : alerte déplacée par le placement du cœur (elle est seulement reposée sous la boussole à chaque alerte) ;
  rendu « différentiel » des lignes de la fenêtre (elle redessine la page visible au plus 1 fois / s, comme avant) ;
  pastilles de « Sacs » / « Équipement » (aux modules, par `S.SetAction`) ; captures dans le vrai client.
