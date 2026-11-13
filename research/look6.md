# LOOK 5.1 — « une vraie maj graphique » (agent LOOK, 13 nov 2026)

Mots du joueur après la 5.0 : « fais une vraie maj graphique, là c'est dégueulasse et trop compliqué, faut penser à
tous les connecteurs logiques et pense à tous les addons que j'ai pour t'y connecter correctement, surtout Shortest
Path Forever, rends les menus moins agressifs ».

Fichiers : `Theme.lua` (V6), `Shortcuts.lua` (bande d'une rangée), `Window.lua` / `WindowPages.lua` (« Maintenant »,
9 catégories, connecteurs), `Now.lua` (nouveau : le modèle « Maintenant » + le petit bloc contre la colonne Suivi
Forever), `Addons.lua` (réécrit : lecture seule), `Tour.lua` (le calme, la ligne du 1er lancement).
Petits crochets gardés hors de mes fichiers : `Core.lua` (ligne MODULE_INFO « Now », `_MaybeWizard` → `T:Hello`),
`UI.lua` (`OthersHold` suit les liens), `Bags.lua` / `QoL.lua` (Leatrix Plus : rien en double chez le marchand),
`Bugs.lua` (la sonde des autres addons dans le rapport d'erreurs), `ProfLevel.lua` (lignes d'achat cliquables),
`Watch.lua` (plus d'écriture dans les options de Questie).
Outil : `tools/wow_render.lua` + `tools/wow_render.js` (+ `wow_render_metrics.lua`). Tests : `test_look6.py` +
`look6_scenario.lua` (nouveaux), tests de la bande / de la mise en page / de la visite mis à jour.

## 1. Voir ce qu'on dessine (méthode)

`tools/wow_render.lua` fait tourner le vrai code de l'addon dans le simulateur, géométrie du client du joueur
(2000×1125, échelle d'interface 0,64, `realui_scenario`), instrumente le mock AVANT le chargement (couleurs, alpha,
polices, couches, justification ; largeur des textes mesurée avec la police du rendu) et vide l'arbre des cadres
VISIBLES de TreeAI dans l'ordre de dessin. `tools/wow_render.js` (Playwright, Chromium) le dessine en PNG par-dessus la
capture du joueur (gardée hors du dépôt : elle montre d'autres joueurs). Scènes : idle_town, questing_on,
questing_off, merchant, bags, ah, toast, first_login, hub, window_<page>.

```
lua5.1 tools/wow_render.lua --scene questing_on --out /tmp/d.json
node tools/wow_render.js --scenes idle_town,questing_on,window_home --tag after --out DIR --bg capture.webp
```

Ce que les rendus « avant » (5.0) montraient, en designer : une bande de deux rangées à boutons pleins (68 px réels),
titres de groupe dorés, poignée dorée ; des cadres à bord arrondi doré, des tuiles colorées, une alerte rouge à liseré
épais ; la fenêtre ouvrait sur une page dense (11 catégories, 3 cartes empilées) ; au 1er lancement un assistant
plein écran. Rien ne ressemblait aux panneaux fins et sombres de son écran (Suivi Forever, suivi du jeu, compteurs).

## 2. Langage V6 (Theme.lua)

Plat, fin, sombre translucide : fond noir 0,88 pour la fenêtre, 0,45 pour les petits cadres posés sur l'écran,
liseré blanc à 10 % d'1 px, texte 10–12 px, titres 14 / 16. UN accent (bleu 0.45 0.74 1.00) pour ce qui se clique ;
l'or seulement pour les titres de section (comme les titres de la colonne Suivi Forever). Plus de bord arrondi doré, de
dégradé, de tuile colorée ni de pulsation d'office. Survol = voile blanc à 7 %. Une action principale par page (la
seule teinte pleine).

## 3. Moins agressif

* Bande : UNE rangée de 24–28 px réels (icône 14 + mot court + nombre en accent), fond noir translucide, trois points
  gris pour la poignée. Estompée à 60 % au repos, 30 % en combat, pleine au survol (scripts de souris, pas
  d'OnUpdate). « Suivant » / « Vite » hors de la bande d'office (option « Boutons en plus », touches et /tai
  inchangés). Les positions 5.0 sauvegardées sont gardées ; `/tai interface reset` marche. Vu sur le rendu du
  marchand : la bande tombait sur le titre de la fenêtre du jeu → une fenêtre du jeu ouverte par-dessus (marchand,
  PNJ, banque, HV, maître, courrier…) l'efface comme en combat (30 %), sur événement, lecture seule (`S.UnderPanel`).
* Connexion : plus aucune fenêtre seule. Une ligne fine « TreeAI est prêt. · Régler (1 min) · Plus tard » (20 s, pas
  en combat ; au 3e lancement sans réponse elle vaut « plus tard »). `T.Calm` (Tour.lua) : rien ne se propose pendant
  la 1re minute ni en combat, un seul à la fois (2 min d'écart), « plus tard » retenu (TreeAIDB.quiet). La visite ne
  démarre plus jamais seule. La question de spé et le réglage sont des lignes de « Maintenant » (clic droit = plus
  tard), pas des fenêtres.
* Alertes : une seule, petite, sous la boussole (ou en haut au centre quand Suivi Forever guide), jamais empilées ;
  « discret » d'office (5 à 8 s, texte blanc cassé, trait d'accent à gauche, plus de cadre rouge).

## 4. Moins compliqué

* La fenêtre s'ouvre TOUJOURS sur « Maintenant » : 5 lignes au plus, chacune « quoi » + UN bouton (Now.Model : urgent,
  quêtes, sacs, équipement, réparations, groupe, métiers, une question à la fois). Rien d'urgent = « Rien d'urgent :
  continue. »
* 9 catégories en mots simples : Maintenant · Quêtes · Groupe · Équipement · Sacs · Métiers · HV · Chasseur ·
  Réglages. « Trajet » = onglet de Quêtes, « Donjons » = onglet de Groupe, « Conseils » = ligne des Réglages. Les
  anciens ids (route, dungeon, coach, perso, journal…) mènent toujours au bon endroit ; la recherche reste.
* Une action principale par page en haut, le reste replié. Le bouton du pied de page qui redit une action déjà en
  tête n'est plus montré (`W.DupOfTop` : Métiers montrait « Me concentrer » en haut ET « Me concentrer sur les
  métiers » en bas).
* Page « Autres addons » : une case ne s'affiche que si elle coupe vraiment quelque chose ; les lignes tenues par
  d'autres modules (base de Questie, icônes de Questie, prix d'Auctionator) sont des lignes d'information.

## 5. Connecteurs : audit « une ligne = un clic évident »

| ligne de conseil | avant (5.0) | maintenant |
|---|---|---|
| « Répare au prochain marchand… » (Coach, Inventory) | texte seul | clic = le réparateur connu le plus proche (`W.GoRepair`, confié à Suivi Forever) |
| « Refais le plein… / munitions / flèches » | texte seul | clic = le marchand le plus proche (`W.GoService("vendor")`) |
| composant vendu par un marchand (Métiers → À acheter) | texte seul | clic = le marchand (`service = "vendor"`) |
| composant à l'HV / à farmer (Métiers) | texte seul | clic = le chercher à l'HV ouvert, sinon y aller (`W.FindItem`) |
| « Vérifie tes talents… » | texte seul | clic = la fenêtre de talents du jeu (hors combat) |
| « Prochaine étape » (page Quêtes) sans clic | texte seul | clic = y aller (cible de la boussole → Suivi Forever / repère du jeu) |
| « Maintenant » : rendre / prendre / zone | — | bouton Rendre / Prendre / Ma zone (`Shortcuts.Do`) |
| « Maintenant » : étape en cours | — | « Y aller » (A.GoTo) |
| « Maintenant » : marchand ouvert, objets à vendre | — | « Vendre » (`Bags.SellClick`, annulable au rachat) |
| « Maintenant » : sacs presque pleins | — | « Sacs » (sacs ouverts, chaque objet marqué) |
| « Maintenant » : meilleure pièce dans les sacs / à aller chercher | — | « Équiper » / « Y aller » (Gear.NowRows) |
| « Maintenant » : usure forte | — | « Réparateur » |
| « Maintenant » : quêtes à partager | — | « Partager » |
| « Maintenant » : achats de métier en attente | — | « Courses » (session métier) ; absent si SkillUp tient la liste |
| « Maintenant » : spé illisible | fenêtre de question | « Choisir » (page Équipement), clic droit = plus tard |
| Suivi TreeAI : ligne « Suivi de Questie » | écrivait dans les options de Questie | ouvre « Autres addons » (lecture seule) |
| tout « va là » d'un module (maîtres, marchands, équipement…) | flèche TreeAI | même clic, destination confiée à Suivi Forever / repère du jeu quand SPF guide |

Règle générale (`W.DefaultAction`) : une ligne sans clic reçoit le clic évident (service, objet, page) ; rien
d'évident = pas de faux clic (testé : « Ton repos double l'XP » reste du texte).

## 6. Autres addons (Addons.lua, lecture seule, pcall / rawget partout)

* **Shortest Path Forever** : détecté (dossier, globale `ShortestPathForever`, API publique sondée : `API.Navigate`,
  `NavigateRoute`, `Estimate`, `Active`, `CurrentStop`, `Subscribe`, `Ended`). Lien « flèche » (d'office) : la
  boussole TreeAI n'est plus affichée d'office, le guidage passe en « auto », la page Quêtes ne répète pas le trajet
  pas à pas. « Y aller » (dans un clic du joueur seulement) → `ShortestPathForever.API.Navigate` (propriétaire
  « TreeAI »), sinon le repère du jeu (`C_Map.SetUserWaypoint` + `C_SuperTrack.SetSuperTrackedUserWaypoint`) que SPF
  et la carte suivent. Lien « bloc » : le petit bloc « Maintenant »
  (3 lignes, même largeur que la colonne, titre or, fond à 20 %) se pose sous la colonne si c'est libre, sinon au-dessus,
  sinon nulle part — sur UIParent à la place LUE, jamais ancré à son cadre. Rien n'est écrit dans ses tables.
* **Questie** : base lue (LibQuestieDB / QuestieLoader) ; icônes et suivi laissés à Questie (les anciennes fonctions
  qui coupaient son suivi dans SES options sont supprimées). Ligne d'information, pas de case (le lien est tenu par
  Export / la boussole / Icônes de quêtes).
* **Adventure Guide Forever** : lien « guide » — la page Quêtes ne répète pas sa liste d'étapes.
* **SkillUp Forever** : lien « prof » — « Maintenant » ne dit pas les achats de métier, la liste « composants » de
  Suivi Forever n'est pas doublée ; TreeAI garde « où est le maître / quoi vendre / quoi garder ».
* **Auctionator** : prix lus par son API publique v1 (module HV, autre agent) ; ligne d'information.
* **Leatrix Plus** : `LeaPlusDB.AutoSellJunk` / `AutoRepairGear` lus ; s'ils sont « On », la carte du marchand le dit en
  une ligne, ne propose ni « vendre » ni « réparer » en double, et l'auto-vente de QoL ne passe pas une 2e fois.
* **OpenMap, Tweaks Forever, compteur de dégâts, DungeonJournal, Forever Loot Sparkles** : évités au placement, rien lu.
* Page **Réglages → Autres addons** : chaque addon trouvé (version), « TreeAI prend : … » / « TreeAI laisse : … »,
  chaque lien réel se coupe d'une case (coupé = TreeAI rend ce qu'il avait pris : boussole, guidage, listes).
* Sonde (`A.Probe`, ajoutée au rapport d'erreurs par Bugs.ProbeAPI) : chaque addon connu présent / absent, dossier,
  version, chargé, état des liens, chaque point d'entrée présent / absent, le cadre de la colonne trouvé (nom, taille),
  les réglages Leatrix lus, le dernier « Y aller » et les API de repère du jeu présentes.

## 7. Pas fait / à vérifier en jeu

* Le nom du cadre de la colonne Suivi Forever n'est pas connu avec certitude : `A.COLUMN_NAMES` en essaie 13 ; si aucun
  ne répond, pas de bloc (tout reste dans la fenêtre) et la sonde le dit. Lire TreeAI-erreurs.txt du joueur.
* « Y aller » passe par le chemin déjà en place de la boussole (`Compass.GoTo` → `Compass.Pin` : `API.Navigate` de
  SPF, sinon TomTom, sinon le repère du jeu) ; à confirmer par la sonde et un clic « Y aller » en jeu.
* Les questions de spé / réserve de monture du module Équipement restent des conseils de sa liste (une fois, clic
  droit = masquer), pas des fenêtres ; elles ne passent pas par `T.Calm`.
* Rendus : la capture du joueur sert de fond local seulement ; les icônes du jeu sont des tuiles schématiques.
* Carte du marchand (Bags.lua, agent BAGS) : fond « small » à 45 % ; accrochée sous la fenêtre du marchand elle tombe
  sur la colonne Suivi Forever et les deux textes se mélangent (rendu `after_merchant`). Le jeton `Th.KINDS.docked`
  (fond presque opaque) est prêt : un mot à changer dans `newCard` de Bags.lua.
