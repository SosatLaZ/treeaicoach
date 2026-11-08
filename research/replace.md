# REPLACE — « qu'on n'ait plus besoin des autres addons » (4.0.1)

Branche `replace` (worktree /home/user/replace), base m401 `c663669`. Joueur : chasseur Horde niveau 28, WoW: Forever
(1.60.1, famille Mainline, frFR), groupe de 4. Addons installés (captures) : Questie, QuestieDB / QuestieTrace, Shortest
Path Forever « Suivi Forever », Details, Leatrix Plus, Auctionator, DungeonJournal, Forever Loot Sparkles, Tweaks
Forever: AH compatibility. Demande : « vois tout ce qui peut être amélioré pour tout, qu'on n'ait plus besoin des autres
addons » + « que cet addon prenne le dessus sur les suivis de quêtes, et tout faire un truc parfait avec la distance ».

Règles tenues : Questie / Shortest Path sont sous GPL → leurs données sont **lues en jeu**, jamais copiées dans TreeAI ;
on n'écrit chez eux que les **réglages de leurs propres pages d'options**, sur un clic, avec retour arrière ; aucune
action protégée sans clic, rien en combat qui puisse être bloqué ; mesures dans `session_sim` et un scénario dédié.

## 1. Addon par addon

| Addon | Ce que le joueur en utilise | TreeAI avant (module, qualité) | Ce qui manquait | TreeAI peut-il le remplacer sur Forever ? | Fait ici / plan |
|---|---|---|---|---|---|
| **Questie** | base de quêtes (donneurs, objectifs, apparitions), « ! » / « ? » / zones sur la carte et la minimap, suivi de quêtes, infobulles de quête | Quests, Detect, Compass, Tracker, Todo, ZonePlan, Planner, Export lisent QuestieDB en jeu (bon : objectif ↔ monstre, trajet, boussole) ; MinimapPins se taisait sur ce que Questie montre | **les icônes** (TreeAI n'en dessinait aucune), **le suivi à l'écran** (celui de Blizzard ou de Questie restait), un moyen simple de faire taire Questie | **Oui pour l'affichage, non pour la base** : la base (PNJ, objets, apparitions, prérequis) n'existe pas dans le client et ne peut pas être recopiée (GPL) → cible réaliste : **« Questie installé mais silencieux »** (QuestieDB lue par TreeAI, aucune icône ni suivi de Questie) | **Fait** : `QuestPins` (icônes carte + minimap), `Watch` « Suivi TreeAI », `Addons.SilenceQuestie` (un clic, ses options, recharge) ; plus tard : lignes de quête dans les infobulles de PNJ (aujourd'hui laissées à Questie, silencieux ou pas) |
| **QuestieDB / QuestieTrace** | la base (dépendance de Questie) | lue par TreeAI (`LibQuestieDB` contrat 4, sinon `QuestieLoader._modules`) | — | à **garder** (c'est la base) ; QuestieTrace : outil de Questie, sans rôle pour TreeAI, à laisser tel quel | si QuestieDB se charge seule (addon séparé non « à la demande »), Questie lui-même pourrait être désactivé : **à vérifier en jeu** avant de le proposer |
| **Shortest Path Forever** (« Suivi Forever ») | trajet guidé (étapes, flèche, ligne sur la carte), horaires des bateaux, chemins qui contournent les falaises | Route (plus court chemin marche / vol / bateau / foyer), PathDraw (carte + minimap), Compass (flèche), NavTime (durées calibrées), carte « Prochaine étape », ZonePlan (ordre des arrêts) ; SPF piloté si le joueur le choisit (`NavigateRoute`) | son **suivi à l'écran** faisait doublon ; les chemins réels (navmesh) et les horaires exacts des bateaux | **En grande partie** : trajet, vols, bateaux, flèche, dessin, suivi = TreeAI ; **le navmesh** (contourner une falaise à Mille pointes) est une donnée de SPF (GPL) → TreeAI garde des lignes droites + facteur de détour mesuré | **Fait** : `Watch` remplace son suivi pour les quêtes ; page « Mes addons » : couper / remettre **son** suivi (`ShortestPathForeverDB.tracker` / `journeyTracker`, sans recharge). Garder SPF en « moteur » (Options > Trajet > Guidage) seulement si les détours comptent |
| **Details** | dégâts du groupe par combat / session | rien (le journal de combat est **interdit** aux addons sur Forever : `COMBAT_LOG_EVENT_UNFILTERED` = ADDON_ACTION_FORBIDDEN, voir `research/fidelity.md`) | une fenêtre de dégâts | **Oui, autrement** : Forever expose son **propre compteur** `C_DamageMeter` (documenté 1.60.1 : `GetCombatSessionFromType`, `GetCombatSessionSourceFromType`, sessions « Current / Overall / Expired », types DamageDone / Dps / Healing… ; événements `DAMAGE_METER_*` ; cadre Blizzard « DamageMeter » du mode édition). Ses valeurs sont **secrètes en combat** (`SecretWhenInCombat`) → un addon ne lit qu'après le combat | **Fait** : `Meter` « Dégâts » (dernier combat / session, total, par seconde, part, remise à zéro du jeu), mis à jour à la fin de chaque combat. Pour des chiffres **en direct** pendant le combat : le compteur du jeu (mode édition) — aucun addon ne peut faire mieux sur Forever |
| **Leatrix Plus** | confort : vendre les gris, réparer, butin plus rapide, recul de caméra, accepter / rendre les quêtes… | Quick : vendre / réparer / quêtes auto (options, mais module bêta coupé en mode sûr), rappel butin auto | le butin instantané, le recul de caméra, un endroit unique « confort » | **Oui pour ce qu'il utilise** (automatismes permis : marchand, butin, réglages du jeu) ; **non, exprès**, pour ce qui masque / déplace des cadres de Blizzard (taint, « Échec des actions d'interface ») et les fenêtres protégées (résurrection, invocation) | **Fait** : `QoL` « Confort » (vendre les gris + réparer — jamais en double avec la barre de quête —, butin instantané à `LOOT_READY`, butin automatique et recul de caméra en un clic). Plus tard si demandé : refuser les duels (hors liste ROUND7 aujourd'hui) |
| **Auctionator** | scans, prix, mise en vente rapide, listes d'achat | Auction (achats-reventes avec preuve de liquidité, ventes, bilan, relit les scans d'Auctionator) : solide | mise en vente en masse, recherche rapide, prix dans les infobulles | **Pas encore** : TreeAI conseille et vérifie, Auctionator reste l'outil de vente de masse | page « Mes addons » le dit ; plan : prix d'HV (Auction.Stats) dans les infobulles d'objet |
| **Tweaks Forever: AH compatibility** | compatibilité des addons d'HV avec l'HV de Forever | TreeAI parle `C_AuctionHouse` directement | — | inutile pour TreeAI ; à garder tant qu'Auctionator est là | dit sur la page |
| **DungeonJournal** | fiches de boss, butin des donjons (le client Forever n'a pas les tables du journal de donjon) | Dungeon (quêtes du donjon, qui n'a pas quoi, prêts, donjons déjà faits), Gear (butin de boss via QuestieDB `npcDrops`) | fiches de boss, tableau de butin complet | **Non** (données de l'addon) | à garder ; page « Mes addons » le dit |
| **Forever Loot Sparkles** | effet visuel dans le monde | Highlight surligne sacs et fenêtre de butin seulement | — | **Non** : un addon ne dessine pas dans le monde 3D | à garder s'il plaît (sans conflit) |

## 2. Ce qui a été construit

* **`Modules/QuestPins.lua` « Icônes de quêtes »** (mode sûr). « ! » couleur de niveau du jeu (un par donneur, les autres
  quêtes du PNJ dans l'infobulle), « ? » des quêtes prêtes, zones d'objectifs (disque au rayon réel de l'amas, icône
  tuer / ramasser / objet). Filtres : faction / classe (export), niveau requis, grises (option), prérequis, exclusives,
  déjà faites, JcJ / raid. Ordre / plafonds : arrêts du trajet d'abord (même numéro que l'onglet et la carte ; l'icône
  se pose à côté de la pastille du Chemin dessiné), puis à rendre, objectifs, à prendre ; carte 40 (20 « ! »,
  24 objectifs), minimap 10. Infobulle : quête, donneur, XP (base de Questie), distance + durée à pied (NavTime),
  pourquoi (« Sur ton trajet : arrêt 3 », « L'XP tombe au rendu », « Élite : à 3 ou plus »…), clic = boussole. Questie
  dessine encore ses icônes → TreeAI n'en dessine aucune (lecture de SES options carte / minimap).
* **`Modules/Watch.lua` « Suivi TreeAI »** (mode sûr). Quêtes du trajet par zone dans l'ordre des arrêts, puis « Hors
  trajet » ; bascule « Par distance ». Par objectif : barre, texte du jeu, **distance au bord de la zone + direction +
  durée à pied, en direct** (1 lecture / s en marchant, seuls les textes dont la valeur arrondie change sont réécrits,
  rien à l'arrêt). « Rendre à X · 330 m NE · 1 min ». Objet de quête : bouton sécurisé (hors combat). Clic = boussole ;
  clic droit : guider, ignorer dans le trajet, partager, retirer du suivi, abandonner (page Journal). **Remplace le suivi
  du jeu** : caché hors combat, recaché à chaque `OnShow`, en combat seulement `SetAlpha(0)` (jamais Hide : ses boutons
  d'objet sont sécurisés), rendu en mode édition du jeu et quand l'option est coupée ; Questie : une ligne propose de
  couper son suivi (ses options).
* **`Modules/Addons.lua` « Mes addons »** (mode sûr, aucun automatisme) : la page qui dit, addon par addon, ce que TreeAI
  fait à sa place et ce qui manque, avec le clic (Questie silencieux / réaffiché, suivi de SPF coupé / remis, Confort et
  Dégâts allumés) ; options de Questie illisibles → les 3 clics exacts (« Échap > Options > AddOns > Questie : onglet
  « Icônes », décoche « Activer les icônes » ; onglet « Suivi », décoche « Activer le suivi » »). Une ligne, une fois.
* **`Modules/QoL.lua` « Confort »** (bêta, un clic) et **`Modules/Meter.lua` « Dégâts »** (bêta, un clic) : voir § 1.
* Petits crochets : Core.lua (QuestPins, Watch, Addons dans le mode sûr ; les 5 modules décrits dans « Fonctions ») ;
  UI.lua mesure le Suivi TreeAI comme un suivi de quêtes (rien ne se pose dessus, il n'est jamais écarté) ;
  MinimapPins : « ≈ ≈ » doublé dans son infobulle corrigé. Le Suivi TreeAI **lit** le trajet (`Route:Stops`, ordre
  et numéros de ZonePlan) et le journal ; il ne calcule aucun trajet et ne touche pas à la liste de suivi du jeu.

## 3. Mesures (scénario `tests/wow/replace_scenario.lua`, harnais modern + forever ; `session_sim`)

| | QuestPins (kinstr/min) |
|---|---|
| immobile, carte fermée | **0** |
| en marchant 60 s, minimap sans HereBeDragons (pire cas) | 22–30 |
| carte ouverte 60 s | 6–9 |
| carte glissée sans arrêt 30 s | 26–38 |
| avec HereBeDragons (Questie installé) | ≈ 0 par image (la lib place les icônes) |

`session_sim` forever « mode sûr » : Watch ≈ 5, QuestPins ≈ 4, Addons 0 ; plafonds ajoutés : QuestPins 40, Watch 40,
Addons 5, QoL 10, Meter 10 ; à l'arrêt (`--idle`) 1 chacun. Zéro action bloquée (mock strict, suivi du jeu marqué
protégé dans le scénario).

## 4. À vérifier en jeu

1. Questie silencieux après le rechargement : plus aucune icône ni suivi de Questie, et les icônes TreeAI apparaissent
   (carte : alignées au zoom et au glisser ; minimap : par HereBeDragons de Questie).
2. Le suivi de Blizzard reste caché (connexion, quête acceptée, combat, mode édition fermé), aucune « Échec des actions
   d'interface » (`/tai bloque`) ; le Suivi TreeAI prend sa colonne au premier lancement.
3. `C_DamageMeter.IsDamageMeterAvailable()` sur Forever (sinon la fenêtre affiche la raison du jeu) et la forme des
   sessions (noms, totaux) après un combat à 4.
4. Butin instantané avec un objet lié quand ramassé (la confirmation du jeu doit rester au joueur).
5. QuestieDB seule chargée sans Questie ? (si oui, proposer « désactiver Questie » au lieu de « silencieux »).
