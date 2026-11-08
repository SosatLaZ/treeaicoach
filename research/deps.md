# TreeAI et les autres addons — ce qui casse sans eux, et par quoi le jeu les remplace (4.1 BUGHUNT)

Inventaire : `grep -rn "QuestieLoader|QuestieDB|Questie|ShortestPath|SPF|TomTom|Auctionator|Details|Leatrix|Bagnon|Baganator|AdiBags|ElvUI|HereBeDragons|TreeAI_Plan"`
sur `wow_addon/TreeAI` (Data/ exclu). Toutes ces lectures sont déjà en `pcall` / lecture seule : **aucun addon n'est
requis pour charger TreeAI** (TOC : `OptionalDeps` seulement). La question est donc « qu'est-ce qui se dégrade ».

## Tableau de synthèse

| Addon | Modules qui le lisent | Sans lui | Remplacement par le jeu / nos données | État 4.1 |
|---|---|---|---|---|
| **Questie** (QuestieLoader._modules.QuestieDB / LibQuestieDB) | Compass (points d'objectifs et de remise), Quests (quêtes à prendre ici, chaînes, qui lâche l'objet), ZonePlan / Planner / Export (arrêts « prendre », points des quêtes pas encore prises), Detect (objectifs du jeu ↔ Questie), Highlight (ID des objets d'objectif, monstres qui les lâchent, objets « à venir »), ItemInfo (« lance la quête », « butin de », « vendu par »), Gear, Pro (chaînes, quêtes relais), Edge2 (relais devenus inutiles), Abandon, Brain (meilleure zone voisine), Dungeon / DungeonPrep (quêtes de donjon), Finder (apparitions des bêtes), ProfLevel / QuickTravel (marchands qui vendent X), Stats (a priori des taux), Tracker (« faciles à côté »), Radar, Watch, QuestPins, MinimapPins (muets quand Questie dessine) | **Le plus gros manque : « Quelles quêtes prendre »** (aucune quête hors journal), plus de points pour les quêtes pas encore prises, plus de chaînes / quêtes relais, pas de quêtes de donjon, pas de « qui lâche / qui vend » tant que rien n'est appris. Les quêtes DU JOURNAL restent guidées (point du jeu) | `C_QuestLine.GetAvailableQuestLines(carte)` (+ `RequestQuestLinesForMap`, `QUESTLINE_UPDATE`) = les « ! » de la carte, présents dans Forever 1.60.1 ; `C_QuestLog.GetNextWaypoint(q)` / `GetNextWaypointForMap(q, carte)` ; `C_QuestLog.GetQuestsOnMap(carte)` ; `C_GossipInfo.GetAvailableQuests()` / `GetNumAvailableQuests()` chez le PNJ ; PNJ de quête appris quand on leur parle (QUEST_DETAIL / QUEST_PROGRESS / QUEST_COMPLETE / GOSSIP_SHOW + position du joueur + `GetQuestID`) ; monstres qui lâchent l'objet appris au butin (Stats : `GetLootSourceInfo`) ; Data/Pets (bêtes), Data/Recipes (vendu par les marchands), Data/Dungeons | **Fait (BUGHUNT)** : `T.NpcBook` (Finder.lua), `T.GameQuests` (Finder.lua), Tracker (remise apprise), Highlight (monstres appris au butin). **À brancher** par QUESTS / LAYOUT (liste plus bas) |
| **Shortest Path Forever** (ShortestPathForever.API) | Compass (`Estimate` = temps réel navmesh / vols, `Navigate`), Route / Planner / Tracker (`NavigateRoute("TreeAI", arrêts)`), PathDraw (ne pas dessiner deux fois), Horizon, Finder | Temps de trajet = distance × détour par zone (la plainte « pas de dénivelé ni de virages ») ; pas de chemin pas-à-pas dans le suivi de SPF ; la boussole TreeAI guide seule (c'est déjà le défaut : `guide = "treeai"`) | Pas d'API de navigation dans le jeu. Nos données : Data/Travel.lua (vols, bateaux, foyer), NavTime (vitesses MESURÉES par zone), PathDraw (chemin dessiné). Améliorer le détour : apprendre le rapport « distance marchée / distance à vol d'oiseau » par zone (Speed/NavTime) | Rien à faire côté BUGHUNT ; PATHS / QUESTS : estimation sans SPF |
| **TomTom** | Compass / Planner / Tracker / Todo (flèche de secours, « Aller là ») | Rien : repli sur notre boussole puis `C_Map.SetUserWaypoint` + `C_SuperTrack.SetSuperTrackedUserWaypoint` (déjà codé) | `C_Map.SetUserWaypoint` / `C_SuperTrack` | OK |
| **Auctionator** | Auction, Prof, ProfLevel, Gear (prix HV) | Prix HV = notre historique (scans TreeAI) ; sans scan : « à farmer » / prix marchand | `C_AuctionHouse` (scans TreeAI), Data/Recipes (prix marchand) | OK |
| **Details / Recount / Skada** | Meter (nom), UI (placement : les éviter), ItemInfo | Rien : fenêtre « Dégâts » TreeAI (off par défaut) + compteur du jeu « Dégâts infligés » (C_DamageMeter) | `C_DamageMeter` | OK |
| **Leatrix Plus** | QoL / Quick (ne pas vendre deux fois), Window (page « Mes addons ») | Rien : QoL fait vente des gris, réparation, butin rapide | — | OK |
| **Bagnon / Baganator / AdiBags / ElvUI / BetterBags / ArkInventory** | Highlight (surbrillance dans leurs sacs : API publiques / hooksecurefunc), Binds (ElvUI), garde des sacs (racines connues) | Surbrillance seulement dans les sacs Blizzard (cas du joueur) | Sacs Blizzard : `ContainerFrame1-13`, `ContainerFrameCombinedBags`, `C_Container.GetContainerItemQuestInfo` | OK |
| **HereBeDragons** | MinimapPins (moteur de points s'il est là) | Notre propre projection minimap (déjà le cas par défaut) | `C_Map`, `Minimap:GetZoom` | OK |
| **TreeAI_Plan** (fichier écrit par l'appli PC) | Planner, ZonePlan | Plan calculé en jeu (automatique) | — | OK |

## Détail Questie — ce que chaque fonction perd, et le remplaçant

1. **Quêtes à prendre** (Quests `lastQuestie.here`, ZonePlan / Planner arrêts « prendre », Brain « meilleure zone »,
   Highlight « objets à venir ») → `T.GameQuests.Available(carte)` : `{ {questID, name, line, mapID, x, y, src = "jeu"} }`
   (C_QuestLine, quêtes cachées exclues, demande une fois par carte / 5 min). Le niveau de la quête n'y est pas :
   classer par distance (+ XP / niveau appris quand le PNJ la propose : QUEST_DETAIL, `GetQuestID`), jamais inventé.
2. **Point d'une quête du journal** (Compass, Watch, Radar, QuestPins, Tracker) → déjà `GetNextWaypoint` ; ajouter
   `T.GameQuests.Point(q, carte)` (GetNextWaypointForMap = le point sur la carte affichée, utile pour les quêtes
   multi-zones) et `T.GameQuests.InLog(carte)` (`GetQuestsOnMap`) pour les POI de la carte courante.
3. **Remise d'une quête finie quand le jeu ne donne pas de point** → `T.NpcBook.Points(q, "end")` (le PNJ à qui on a
   rendu / qui a dit « reviens me voir » : QUEST_PROGRESS / QUEST_COMPLETE). Fait dans Tracker ; à faire dans
   `Compass.QuestPoints` (après `waypointAPI`, avant `QuestieFinisherPoints`).
4. **Donneur d'une quête** (chaînes, « reprendre une quête abandonnée ») → `T.NpcBook.Starter(q)` / `Points(q, "start")`.
5. **Qui lâche l'objet** (Highlight barres de nom, ItemInfo « butin de », MinimapPins « butin de quête ») →
   Stats `TreeAIDB.stats.drops[npc].i[item]` (corps fouillés par toi) + `stats.names[nom] = item`. Fait dans Highlight
   (monstres marqués `learned = true`) ; ItemInfo le lit déjà (`measuredDrop`).
6. **Qui vend X / maîtres** (ProfLevel, QuickTravel, Brain) → Detect.Learn (services appris : MERCHANT_SHOW,
   TRAINER_SHOW, TAXIMAP_OPENED…) + ProfLevel `vend` (marchands de compos appris) + Data/Recipes (`mats[id][3] == 1` :
   vendu par les marchands).
7. **Objectifs ↔ créatures / objets** (Detect.MapObjectives) → `C_QuestLog.GetQuestObjectives` (texte, type, fini) +
   `C_QuestLog.UnitIsRelatedToActiveQuest` sur les barres de nom (Detect le fait déjà) ; noms seuls, pas d'ID.
8. **Quêtes de donjon** (Dungeon / DungeonPrep) → Data/Dungeons.lua (à compléter avec les questID par donjon) ;
   `C_QuestLine.GetAvailableQuestLines` sur la carte de l'entrée du donjon.
9. **Chaînes / quêtes relais** (Pro, Edge2) → `C_QuestLine.GetQuestLineQuests(ligne)` (ordre de la ligne) +
   `C_QuestLog.IsQuestFlaggedCompleted` ; sans ligne : rien (on se tait plutôt qu'inventer).
10. **Apparitions des bêtes** (Finder) → Data/Pets + bêtes apprises (`Finder.db.seen`, déjà).

## Fait dans cette passe (fichiers BUGHUNT)

- `Modules/Finder.lua` : **`T.NpcBook`** — PNJ / objets de quête appris en leur parlant (donneur, destinataire,
  dialogue), compte entier `TreeAIDB.npcBook` (≤ 800 fiches), hors combat, valeurs secrètes ignorées ; API `Get`,
  `ByName`, `Starter`, `Finisher`, `Points` (forme des points de la boussole), `Near`.
- `Modules/Finder.lua` : **`T.GameQuests`** — `Available(carte)` (C_QuestLine), `InLog(carte)` (GetQuestsOnMap),
  `Point(q, carte)` (GetNextWaypointForMap → GetNextWaypoint).
- `Modules/Tracker.lua` : quête finie sans point du jeu ni Questie → destinataire appris.
- `Modules/Highlight.lua` : monstres qui ont vraiment lâché l'objet d'un objectif (Stats) marqués sans Questie.
- `tools/wow_build_travel.py` + `Data/Travel.lua` : noms laissés en anglais par les tables frFR du client traduits
  (« Thunder Bluff » → « Les Pitons-du-Tonnerre », Darkshire → Sombre-Comté, Feathermoon → Bastion de Pennelune,
  Ratchet → Cabestan).
- Tests : `tests/wow/deps_scenario.lua` / `test_deps.py` (sans Questie ni SPF, deux saveurs d'API).

## À brancher par les autres agents

- **QUESTS** (Quests.lua, Planner.lua, ZonePlan.lua, Export.lua) : « Quelles quêtes prendre » / `T.QuestBrain.PickupList`
  sans Questie = `T.GameQuests.Available(carte du joueur + voisines)` ; arrêts « prendre » = ces points ; « Rendre mes
  quêtes » = `GetNextWaypoint` puis `T.NpcBook.Points(q, "end")`.
- **Compass.QuestPoints** (propriétaire QUESTS / PATHS) : insérer `T.NpcBook.Points(q.id, "end")` pour une quête finie
  et `T.GameQuests.Point(q.id, pmap)` avant les points Questie.
- **PATHS** (Route / NavTime / Speed) : sans SPF, détour appris par zone (marché / vol d'oiseau) au lieu d'un facteur fixe.
- **QuickTravel** : la correspondance par nom de `D.taxi` (vieille API sans nodeID) compare au nom du jeu ; les 4 noms
  traduits ci-dessus ne correspondront plus si le jeu renvoie l'anglais — comparer aussi la partie avant la virgule
  du nom du jeu, ou garder l'ID (C_TaxiMap le donne sur Forever).
