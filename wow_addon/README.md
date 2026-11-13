# TreeAI — addon WoW (World of Warcraft: Forever)

Addon in-game du compagnon TreeAI Coach. Il **conseille**, il n'automatise rien
(aucune touche envoyée, aucune action à la place du joueur). Les macros ne sont
créées/modifiées qu'**hors combat** (`InCombatLockdown()`).

Install: copier `wow_addon/TreeAI/` dans
`World of Warcraft/_forever_/Interface/AddOns/TreeAI/` (TreeAI Coach le fait
automatiquement). Tout se fait à la souris depuis la **pastille de la minimap** (module `Hub`) ; une
**visite guidée** de 60 s la montre au premier lancement (module `Tour`). Commandes : `/treeai` ou `/tai`
(`/tai aide` montre la pastille, `/tai aide tout` liste les commandes, `/tai visite` rejoue la visite).
Démarrage rapide côté joueur (installer, 3 choses à savoir, menu, bêta, bug, FAQ) : `TUTO_FR.md`.

## Interface (TOC)

`## Interface: 16001, 11509, 11508, 11507`

* **16001** = WoW Forever (patch 1.60.01). Source: warcraft.wiki.gg « TOC format »
  (flavor `forever`, suffixe TOC `_Forever`, alias `_Camelot`, famille
  `_Mainline`) + guide FR playnews.gg (Questie Forever = 16001).
  Forever est dans la famille **Mainline**: il faut s'attendre aux API
  modernes (`C_Container`, `C_Spell`, `C_Item`, `C_UnitAuras`, `C_AddOns`,
  `Settings`) et à l'absence possible des globales classiques
  (`GetSpellInfo`, `GetContainerItemInfo`, `UnitAura`…). D'où `TreeAI.API`
  ci-dessous: **utilisez-le au lieu des API brutes**.
* 11507–11509 = Classic Era (pour tester sur Era).

## Ordre de chargement (TOC)

```
Core.lua  Locale_frFR.lua  UI.lua  Modules/Theme.lua  Options.lua  Modules/SelfTest.lua  Data/Macros.lua
Data/PetFood.lua  Data/Consumables.lua  Data/Dungeons.lua  Data/Gear.lua  Data/Talents.lua  Data/TalentSpells.lua
Data/Recipes.lua  Data/Travel.lua  Data/Pets.lua  Modules/Hunter.lua  Modules/Classes.lua  Modules/Leveling.lua
Modules/Group.lua  Modules/GroupJoin.lua  Modules/Plain.lua  Modules/Quests.lua  Modules/Compass.lua
Modules/NavTime.lua  Modules/PathLearn.lua  Modules/Detect.lua  Modules/Stats.lua  Modules/Macros.lua
Modules/Binds.lua  Modules/Tracker.lua  Modules/Todo.lua  Modules/Hub.lua  Modules/Export.lua  Modules/Planner.lua
Modules/Speed.lua  Modules/Route.lua  Modules/PathDraw.lua  Modules/ZonePlan.lua  Modules/ZoneWatch.lua
Modules/QuestBrain.lua  Modules/NavTrace.lua  Modules/Bags.lua  Modules/BagsPredict.lua  Modules/Inventory.lua
Modules/Boost.lua  Modules/Fight.lua  Modules/Pro.lua  Modules/Abandon.lua  Modules/Auction.lua  Modules/AHMath.lua
Modules/AHDesk.lua  Modules/Prof.lua  Modules/Glow.lua  Modules/Highlight.lua  Modules/PvP.lua  Modules/PvPMark.lua
Modules/Dungeon.lua  Modules/DungeonPrep.lua  Modules/MinimapGeo.lua  Modules/QuestPins.lua  Modules/Gear.lua
Modules/GearSpec.lua  Modules/GearValue.lua  Modules/GearRent.lua  Modules/ItemInfo.lua  Modules/Talents.lua
Modules/Session.lua  Modules/Errands.lua  Modules/Edge2.lua  Modules/Bugs.lua  Modules/Finder.lua
Modules/QuickTravel.lua  Modules/Shortcuts.lua  Modules/Window.lua  Modules/WindowPages.lua  Modules/JournalPage.lua
Modules/Coach.lua  Modules/QoL.lua  Modules/Meter.lua  Modules/Addons.lua  Modules/Now.lua  Modules/Brain.lua
Modules/BrainPage.lua  Modules/ProfLevel.lua  Modules/CityRun.lua  Modules/ProfGuide.lua  Modules/GroupGear.lua
Modules/Tour.lua
```

`Modules/Route.lua` + `Data/Travel.lua` (agent ROUTE) : le chemin le plus rapide entre les étapes du plan =
min(marche, vol par maîtres de vol connus, bateau / zeppelin — attente période/2 + traversée —, pierre de foyer
prête vers l'auberge apprise). `Data/Travel.lua` est généré (`python tools/wow_build_travel.py`) depuis la même
table que le solveur Python (`treeaicoach/wow/planner/transport.py`). Vue de l'onglet Quêtes (remplace celle du
Planner) : « Trajet 12 min · 2,4 km », « Arrêt 1/8 : … », vols / bateaux / zeppelins / foyer comme arrêts ; clic
sur le titre = pause / reprise. Shortest Path Forever présent : « Suivre ce trajet » →
`ShortestPathForever.API.NavigateRoute("TreeAI", arrêts dans l'ordre TreeAI)` (objectifs tenus `hold`+`radius`,
renvoyé à chaque étape faite, `Cancel` à la pause). Tri : « Garde ces 6 quêtes, laisse ces 3 (trop loin) » (trop
loin / trop dure / élite en solo), un clic adopte (rien n'est abandonné ; tout revu au niveau suivant).
`/tai route` (suivre) · `pause` · `garde` · `tout`. Planner : `leg()` → `Route.LegSeconds`, `BuildAuto` →
`Route:FilterRequests`. `T.Geo.World(map, x, y)` renvoie le repère de `UnitPosition` (1er = nord, suit le y de la carte).

`Modules/PathDraw.lua` (agent PATH, juste après Route.lua) : le trajet de `Route:Stops(64)` **dessiné**. Carte du
monde : tirets entre arrêts consécutifs (objets `Line` via `CreateLine` + `SetStartPoint/SetEndPoint`, sinon points),
pastilles numérotées (n° = n° d'arrêt du panneau) + icône vol / bateau / zeppelin / foyer, sur NOTRE calque enfant
d'UIParent posé en coordonnées absolues sur `WorldMapFrame.ScrollContainer(.Child)` (lecture seule, coupe ce qui
dépasse) ; prévenu par `WorldMapFrame:AddDataProvider(CreateFromMixins(MapCanvasDataProviderMixin))`, sinon
`HookScript` OnShow/OnHide ; arrêts d'une autre carte convertis par `T.Geo.FromWorld` (même continent). Minimap :
1–2 prochaines étapes en points fixes dans le monde, projection `MinimapPins` (yards/zoom, rotation, forme), ≤ 5 Hz
en mouvement, 2 Hz immobile, réserve de 48 textures, aucune table par passe. Rien sans trajet actif (pause = rien) ;
option « Guidage » d'un module `Guide` respectée si présent ; SPF qui guide → carte gardée, traînée minimap
seulement avec « Garder la traînée minimap quand Shortest Path guide ». Options : carte, minimap, couleur,
épaisseur. Budget `session_sim` : ≤ 60 kinstr/min. `/tai chemin` (statut).
`Modules/ZonePlan.lua` (agent ZONEPLAN, `T.ZonePlan`, page d'options « Trajet ») : zone de travail + trajet toujours
prêt. `char.mode` = `auto` (zone + voisines, rectangles `T.Geo.Rect` qui se touchent) | `zones` (`char.zones[uiMapID]`)
| `continent` ; `Z:Allowed()` → `{ mode, maps|nil, cont, label, cross }`, `Z:InZone(map)`. `Planner:BuildAuto` →
`Z:BuildRoute(P, yield)` (même tâche découpée ≤ 2 ms, cadencée par `T:NewTicker`) : journal + export QuestieDB (sinon
points du client), filtres (`Z:Accept` : ignorées, donjons, élites — gardées à 3 joueurs et plus —, groupe, écart de
niveau, distance max), objectifs à < 120 m = un arrêt (`step.area`, affiché « A + B »), glouton valeur/temps puis 2-opt
+ Or-opt (précédences prendre → objectifs → rendre), hystérésis `N.Choose` (≥ 10 % et ≥ 60 s de trajet gagnés, sinon
l'ordre en place reste). Hors zone : jamais planifié ; un rendu très rentable est PROPOSÉ (« Détour rentable … ·
clic ») → `char.extra`. Plan de l'appli : `Z:MaskSteps` (hors zone / ignorées → `_other`, `_zone`) ; export
`TreeAIDB.export.work = { mode, maps|cont, ignore, extra }` → `load_problem(work=True)` côté solveur. Lignes : « Zone :
… ▾ » (menu à nous), clic droit sur un arrêt (`onRightClick`, cœur + UI) → « Ignorer cette quête » / guider /
recalculer ; boussole : `Z.CompassTarget` = arrêt en cours (rang juste après un point perso). `Route:Stops(n)` inchangé.
`/tai zone [auto|region|recalc]`.
**QUESTQA** (suivi de quête, règles uniques) : `Z.Kind(q, rec, lq)` → élite, groupe, donjon, `{ pvp, raid, boss, failed }`
et `Z:Accept` = les MÊMES règles que l'appli (`treeaicoach/wow/planner/rules.py`) : prête à rendre gardée ; échouée, JcJ,
raid jamais ; donjon si coché ET groupe ≥ 3 ; élite si coché ou ≥ 3 ; écart de niveau. Zone de travail arrêt par arrêt
(remise dans une capitale = seule la remise sort), `N.NearFirst` (rendre / prendre à < 60 m d'abord), capacité du journal
(`N.CapOK`) après 2-opt / patch, ligne « Journal plein (20/20) : … », `Z:MaskSteps(steps, log)` (plan de l'appli : mêmes
règles, arrêt par arrêt, `s._why`), `Z:MaskedText()` (« Hors trajet : 2 de donjon, 1 élite », infobulle de « Zone »).
Planner : `Planner.Engine.PlanFresh(plan, st)` (perso, zone ∉ zone de travail, +2 niveaux, journal changé, âge 3 j / 12 h
sans empreinte `plan.log`) → « Plan de TreeAI Coach ignoré : … » + `summary.plan.stale/staleWhy` ; plan sans arrêt
visible → trajet en jeu ; `E.CleanReason` (jamais « X : X », % > 100 en mots), `E.XPHTrusted` (≥ 10 min). Route :
horizon `R.Horizon` (≤ 1 h 30, ≤ 30 arrêts, la suite en infobulle), « (≈ N min) » = fait dans N min (trajet + sur place),
`R.firstKind`. Quests : une seule « étape » (celle du trajet), `Quests.Timers` / « Chrono : … avant HH:MM »,
« … a échoué : abandonne-la puis reprends-la », `Quests.SoloOK(id)`. Rejeu : `tests/wow/questqa_scenario.lua`.
**QA-QUEST** (4.0, `research/qa_quest.md`) : foyer en 1er trajet seulement si aucun arrêt à ≤ 5 min (`Z.noHearth`,
Route l'affiche pareil) ; quête chronométrée en tête (`N.TimersFirst`), arrêts sans position au bout
(`N.PositionlessLast`), zone de travail aussi sans position (zone de la quête / `GetQuestUiMapID`) ; `s.xp` = XP
réelle d'un arrêt (`s.value` = score d'ordre), `route.xph` = XP réelle, `E.XPHTrusted` ≤ 3 × tuer des monstres.
Entrées de `Route:Stops(n)` : + `from` (départ d'un vol / bateau / zeppelin : maître de vol, quai ; nil pour le foyer),
`trAt` (déplacement cumulé) ; `R.Horizon(st)` → `keep, total, yd, beyond, travel` ; `R.Groups(st, n)` → `{ [i] =
numéro d'arrêt de st[i], n = nombre d'arrêts }` (mêmes numéros onglet / page / carte). Boussole : `Z.CompassTarget` vise
le départ du 1er transport (`leg` = hs | fly | boat | zeppelin ; foyer = sans flèche, `note`) ; point posé à la main
(`custom = { …, source, at }`) effacé à l'arrivée (≤ 20 m, 3 s), au changement de zone s'il est sur un autre continent,
jamais repris après un /reload sans un clic (`Compass:ForgetSaved` / ligne « Boussole : reprendre … ? »), après le
trajet s'il a > 10 min (sauf `/tai way`) ou est hors d'atteinte. `summary.plan.next = { {k, q, map, label, min} }`
(arrêts de la carte, horizon) → onglet « Trajet » de l'appli ; message `PLAN_BUILT` après chaque trajet en jeu.
Rejeu : `tests/wow/qaquest_scenario.lua` (variantes `QAQ_VARIANT=full|noquestie|org`).
**ZONEFIX** (4.0.1, `tests/wow/zonefix_scenario.lua`, `ZF_VARIANT=full|noquestie`) : ZonePlan dans le mode sûr
(`T.SAFE_MODULES.ZonePlan`, rallumé une fois : `db.safeV`, jamais contre `T.db.modChoice.ZonePlan == false`). Zone
choisie = trajet DANS la zone (le plus proche d'abord, ≤ 1 h `R.HORIZON_S`, remise voisine seulement en chemin
`s.onWay`, ≤ 45 s), voisines seulement pour compléter une zone trop maigre (auto) ; `Z.others` → « Autres zones : N
quêtes (…) ». Donjons : tag Questie d'abord, puis `T.Dungeon.QuestDungeon(id)` / `T.Dungeon.IsDone(d)` s'ils existent
(`Z.DungeonOf(q)`, `Z.DungeonDone(q)` ; raison « donjon fait » : jamais, sauf prête à rendre). Modes du trajet
`Z.STYLES` (`Z:StyleIndex()`, `Z:SetStyle(i)`, par perso), thèmes `Z:Themes()` / `Z:ThemeParts()` (titre de la carte
`W.CardTitle`), « Zones à faire » `Z:ZonesToDo()` / `Z:PickZone(map)`. Boussole = arrêt 1 de la carte :
`Route:CompassTarget(me)` (même objet que `Route:Stops(1)`, `kind = "zone"` sans position), `Compass.FollowRoute()`,
`Planner:Navigate(s)` (arrêt 1 → le trajet, autre arrêt → point « plan » 5 min, `Planner:ReleaseNav`), arrêt à ≤ 110 yd
pris en passant (`Planner:QuickWin`), `Planner.stepsRev` / `StepsChanged()` (étapes réordonnées sur place),
`Planner.evalRev`. Une autre flèche ne l'efface que sur le MÊME endroit (20 m, `Compass.SameSpot`) → ligne courte
`Compass.DUP_TXT` ; combat : flèche seule (`hideCombat` = option). Cadence : animation seulement en mouvement
(`Compass.SetMoving`, évènements PLAYER_STARTED_MOVING / TURNING / LOOKING, sinon `Compass.Glance` 1 ×/s), flèche
20-30 Hz, texte 5 Hz ; à l'arrêt aucun OnUpdate. Ticker du Planner : rendu si `Planner:TickChanged()` (position, plan,
étape, journal lu, zone, pause) ou toutes les 5 min ; `Route:Stops` gardé à l'arrêt (≤ 5 min). « Suivi auto par zone »
(`Modules/ZoneWatch.lua`, options `autoWatch` / `watchMax`) : `Z:WatchWanted(log)`, `Z:WatchPass()` (hors combat, 1 / 2 s),
`char.watchManual` (choix à la main gardés). Journal de la boussole (`Modules/NavTrace.lua`, `T.NavTrace`) :
`NavTrace.Log(kind, texte)` sur un CHANGEMENT d'état seulement (anneau 300 lignes / 30 Ko, `TreeAIDB.diag.nav`, hors
combat — en combat gardé en mémoire), `NavTrace.Lines(n)`, `ExportLines(n)` (rapport `/tai erreurs`) ; section
« Trajet / boussole » de TreeAI-erreurs.txt ; page `navlog` (Outils → « Journal de la boussole »).

`Modules/Quests.lua` (agent Q): aide aux quêtes PvE — à rendre par zone, « finis ici avant de partir »,
toasts de progression, quêtes grises (clic = ouvrir, l'abandon reste manuel), « à faire à 4 » (+ Group),
bouton sécurisé d'objet de quête (créé/modifié hors combat), ligne « Étape : … », `/tai quetes`,
XP restante, `summary.quests`. Questie détecté: lecture seule de `QuestieLoader._modules.QuestieDB`
(jamais `ImportModule`, aucun hook), pas d'infobulles en double.

`Modules/Boost.lua` — **Rares** (4.0) : un rare ou un trésor tout près, une ligne, clic = boussole (+ crâne sur ton
clic, hors combat). Le reste est chez son propriétaire : récompense de quête et infobulles → Gear, XP/h → Stats,
récolte → Prof, munitions → Hunter, nourriture / foyer → Inventory. Résumé IA : `summary.boost = {raresSeen, lastRare}`.
`Modules/Auction.lua` (agents AH, AH2): achat-revente à l'hôtel des ventes. **Aucune automatisation**:
chaque achat / mise en vente / annulation = un clic du joueur (« Acheter », « Poster », « Annuler » ou le
bouton « Suivant » qui exécute l'étape en tête de file) ; au plus UN appel protégé dans le OnClick, prix
revérifié juste avant (sinon étape sautée) ; jamais de boucle ni de minuterie qui agit. API détectée à
l'exécution (`M.Flavor()`): `C_AuctionHouse` = « modern » (WoW Forever 16001, Retail, Mists — prioritaire
si présente ; warcraft.wiki.gg liste ReplicateItems / SendSearchQuery / SendBrowseQuery / PlaceBid pour le
type de jeu `forever`), sinon API classique `QueryAuctionItems` (Classic Era) ; forçable `/tai hv api …`.
Scan complet: `ReplicateItems` (limite 15 min pour tout le compte, silencieuse : compte à rebours stocké
dans `modules.Auction.lastFullCall`, chien de garde 25 s) ou getAll ; `REPLICATE_ITEM_LIST_UPDATE` est
traité même si un autre addon (Auctionator) a lancé le scan ; lecture par coroutine ≤ 4 ms / image ;
lignes sans infos d'objet (hasAllInfo = false) lues quand même (itemID/prix présents), 2e passage, jamais
d'attente. Scan ciblé (`M.ScanTargets`): `SendSearchQuery` objet par objet dès
`IsThrottledMessageSystemReady` / `AUCTION_HOUSE_THROTTLED_SYSTEM_READY` (classique: dès
`CanSendAuctionQuery`). Lecture passive: `AUCTION_HOUSE_BROWSE_RESULTS_*` (`GetBrowseResults` → prix min),
`*_SEARCH_RESULTS_UPDATED`, pages classiques. Auctionator: `Auctionator.API.v1.*("TreeAI", …)` lecture
seule + `RegisterForDBUpdate("TreeAI", cb)` ; utilisé quand nos données manquent/sont peu sûres/vieilles.
Historique par royaume-faction(-neutre) dans `TreeAIDB.modules.Auction.realms[clé].items[itemID] =
{ v = prix marchand, last = time, h = { jour, min, médiane des 5 moins chères, qté, nScans, unités
disparues (remises en vente moins cher déduites, `F.soldBetween`), heures, … }, sl = { jour, vendeurs
distincts (ou niveaux de prix si inconnus), … } }` (≤ 14 jours, plafond `maxItems`). **Liquidité**
(`F.liquidity`, miroir de `auction.liquidity`, seuils dans `research/ah2.md`) : un achat-revente n'est
proposé que s'il y a la preuve que l'objet se vend (≥ 3 j de scans — 2 pour consommables/artisanat/sacs/
munitions —, ≥ 4 scans, ≥ 12 h mesurées, ≥ 1 vente/j (0,5 pour un objet unique), prix stable ±30 %, pas en
chute, ≥ 3 vendeurs, Auctionator d'accord à ±35 %, confiance ≥ 70 %) ; objets verts rares vus 0–1 fois exclus ;
quantité ≤ ventes de `qtyDays` jours ; revente en ≤ 3 j ; ROI ≥ 15 % ; stop-loss −15 % (`chars[k].modules.
Auction.flips[id]`). Sinon `M.none = { text = « Aucun achat-revente sûr pour l'instant », why = {…} }`. Or (`M.Gold()`): total, réserve (`keepGold`,
entraînement/réparations = niveau² × 1,5 pa si `autoReserve`, `mountGold`), budget = total − réserve
(∩ plafond/jour `budget`), immobilisé ; `F.allocate` = glouton profit ajusté / prix, plafond `capGold` %
de TOUT l'or par objet (reventes marchand hors plafond) → `M.plan` → file `M.queue` (achats, annulations,
poses) + boutons rapides de la fenêtre HV « Acheter la meilleure offre » / « Remettre en vente » /
« Annuler les battues » (`M.BuyBest`, `M.RepostNext`, `M.CancelNext` : une action protégée par clic).
**Mode rafale** (`M.RafaleNext`, gros bouton + touche `Bindings.xml` « TreeAI : action HV suivante »
= `TreeAI.AuctionNext()`, sans touche par défaut, « Lier à une touche » via le module Touches) : chaque appui =
la prochaine action de la file sûre (achats → mises en vente → annulations rentables), au plus UN appel protégé ;
entre deux appuis la file est recalculée et le prix suivant revérifié ; pause si scan partiel, plus d'achats si
réserve atteinte ; bilan « Rafale : N actions, +X attendus » (`chars[k].modules.Auction.rafale`). Pas de mode
100 % automatique : ce serait un bot (fonctions protégées).
Chaque affaire porte `deposit` et `relist` (= dépôt × (1 − p) / p, dépôt perdu attendu en reposant jusqu'à
la vente). Journal par perso `chars[k].modules.Auction.ledger` (courrier HV FR/EN), `spent[date]`,
`deposits[date]`. Résumé `summary.auction = { deals, sell, thin, realized = {today, week, total, top},
budget, budgetLeft, spentToday, scanAge, neutral, flavor, gold = {total, reserve, invested, plan,
planGain}, lastScan, none = {why}, rules = {capGold, qtyDays, minDays, minConf} }` (deals : + sold, maxQty,
stop, class), lu par `treeaicoach/wow/auction.py` (mêmes formules: commission 5 % / 15 %
neutre, dépôt = floor(prix marchand × qté × 5 % (25 %) × h / 2), profit = vente × (1 − commission) −
achat − dépôt × P(invendu)). Prix marchand: `GetItemInfo` (11e valeur, mis en cache), sinon Auctionator,
sinon historique. `/tai hv [vendre|bilan|journal|fenetre|cible|debug|neutre|faction|auto|reserve N|
monture N|budget N|api auto|moderne|classique]` — `debug` affiche l'API active, le limiteur, le compte à
rebours, taille/vitesse du dernier scan, Auctionator et les dernières erreurs.

`Modules/Compass.lua` · `Modules/Tracker.lua` (agent QUEST-PRO ; 4.0 : `Predict.lua` retiré, voir `T.Predict`) — en complément de
Questie / Shortest Path Forever / TomTom, jamais en doublon, aucun cadre ni fonction de Blizzard touché :
* **Boussole** (`T.Compass`) : flèche (cadre à nous, déplaçable, molette = taille) vers la cible courante —
  point perso (`/tai way x y [nom]`, `/tai way effacer`) → `C_QuestLog.GetNextWaypoint` / `C_SuperTrack`
  (lecture) → base Questie (apparition la plus proche des PNJ/objets d'objectif ou de remise) → zone seule.
  Distance m/yd, temps : `ShortestPathForever.API.Estimate` (temps réel, hors combat) sinon vitesse
  (`GetUnitSpeed`). Passe à la cible suivante quand l'objectif est fini. Cachée en instance (et en combat,
  option). `/tai cible` (suivante), `/tai aller` ou Maj-clic = « Aller là » : SPF `Navigate` → TomTom
  `AddWaypoint` → `C_Map.SetUserWaypoint` + `C_SuperTrack.SetSuperTrackedUserWaypoint` (non protégés),
  **uniquement sur clic / commande du joueur**. Géométrie pure : `Compass.Geo` (World, Vector, Relative,
  Dist, ETA). Base Questie : `LibQuestieDB` (addon QuestieDB, contrat 4) sinon `QuestieLoader._modules`.
  (NAV) Flèche 64 px lissée (≤ 30 Hz en mouvement, 4 Hz à l'arrêt), couleur selon l'alignement
  (`Geo.AlignColor`), « Derrière toi », anneau d'arrivée (`Compass.ARRIVE` : perso 12 yd, PNJ 15, zone 30),
  cachée quand Shortest Path guide (`API.Active()`, ou `T:GuideOwner()` s'il existe) sauf option `preferTreeAI`.
* **Temps de trajet** (`T.NavTime`, `Modules/NavTime.lua`, agent NAV) : `NavTime.Estimate(a, b)` → secondes,
  "spf" | "modèle" ; `Speed(mode)` (mesurée hors combat, w/m/s), `Detour(mapID)` (appris aux arrivées),
  `Model(yd, mapID)`, journal prévu/réel (`Accuracy()`, `AccuracyText()`), `summary.navTime`.
* **Lueurs** (`T.Glow`, `Modules/Glow.lua`) : `Glow.Ants(host, r, g, b, a, w, h)`, `Glow.Pulse(...)`,
  `Glow.Stop(host)` — animations jouées par le client, sur nos cadres seulement (Highlight).
* (4.0) **Route sûre** (`SafePath`) : retirée — ses dangers étaient devinés (positions des monstres illisibles sur
  Forever). La boussole, le trajet (Route) et la zone de travail (ZonePlan) guident.
* **Suivi** (`T.Tracker`, refait par TRACK-PRO — comparatif AGF / Questie / RestedXP dans
  `research/agf_vs_treeai.md`) : onglet « Quêtes » (`SetTabLines`) **et** cadre compact optionnel à nous
  (`standalone` : déplaçable, verrou, réduction par quête au clic droit / tout réduire sur l'en-tête, police,
  largeur, échelle, opacité du fond ; sans onglets : cadre de secours `frame`).
  - **Tri par coût** (défaut) : XP/min = XP ÷ (trajet + travail × difficulté + 15 s). Trajet = SPF `Estimate`
    (8 plus proches, hors combat) sinon distance ÷ vitesse ; travail = objectifs restants × s/monstre mesurés
    (`T.Predict.Rates().killsPerHour`, mesuré par Stats, sinon 25 s), objets : ⌈restants ÷ taux⌉ monstres (taux mesuré
    `T.Predict.BestSource/DropRate`, sinon 35 %) → « encore ~16 × Lion des collines (50 %) · ≈ 5 min », objets à
    cliquer 20 s, évènements 90 s ; difficulté = (1 + 0,5·Δniv²) × 2 si seul à +3 × élite max(1,2 ; 3/groupe) ×
    1,8 si groupe suggéré > groupe × donjon (4 à < 3, sinon 1,5) ; partagée avec un ami ×0,7. Hystérésis : la
    tête ne change que pour ≥ 10 % de mieux. Autres tris : `route` (plus proche voisin), `distance`, `niveau`,
    `zone`. Filtres : `tous`, `zone`, `pretes`, `groupe`, `faciles` (clic sur l'en-tête / Maj-clic = tri).
  - Ligne : barres, ETA, distance (+ temps SPF), XP, XP/min, calque groupe (amis TreeAI via `Squad:MemberList()`
    avec progression ; membres sans addon via `C_QuestLog.IsUnitOnQuest`), « Prête à rendre » + PNJ + suite
    de chaîne (« → suite « A » → « B » »). Infobulle : objectifs + estimation, « pourquoi ce rang », chaîne n/N
    (`Quests.Chain`), groupe, histoire (`Quests.Story` : Questie `objectivesText`, sinon
    `GetQuestLogQuestText(index)` en API moderne seulement — jamais de sélection du journal).
  - Clic = boussole ; Maj-clic = « Aller là » (SPF / TomTom / repère) ; Ctrl-clic = itinéraire complet SPF ;
    Alt-clic = masquer jusqu'au /reload. Son (option `sound`) **uniquement** quand une quête devient finie.
  - **Plan TreeAI en tête** si `TreeAI.Planner` a un plan actif. Contrat lu (gardé) :
    `Planner:GetActiveSteps(n) → steps, index, total, titre` (étapes à partir de la courante, format
    `TreeAI_Plan` : `{k, q, o, map, x, y, label, eta, who}`), repli `Planner.active = {route={title,steps}, idx}`.
    Affiche « Étape i/N : … — distance · ~eta » + « ensuite » + « puis » ; clic = boussole (point perso).
  - Astuces conservées : faciles à côté, « passe prendre X chez Y en chemin », « tu pars sans prendre… »,
    infobulle « lâche <objet> (taux) ». Recalcul hors combat seulement (`:AutoRefresh()`, ≥ 1,5 s) ; en combat
    la progression est recopiée sans calcul. `/tai suivi [cadre|filtre X|tri X|police N|verrou|masquer id|
    montrer|reduire|cacher]`, `/tai itineraire` (→ SPF `NavigateRoute`, ≤ 64 étapes).
* **En clair** (`T.Plain`, `Modules/Plain.lua`, agent SIMPLE) — toutes les consignes de quête en français de tous les
  jours : `Task` (« Va tuer 6 Loups au nord (≈ 3 min) », « Rends « X » à Dughan »), `Tag` (UN mot : Rapide / Long /
  À plusieurs / Prête à rendre / À retirer + une raison), `Why` (« C'est la quête la plus rapide autour de toi »),
  `RouteName` (« Le plus rapide », « Tout finir ici », « Avec mes potes »), `IsPlain` (garde-fou : jamais d'XP/min,
  de score, de λ ni de % hors progression dans l'interface principale). Les calculs (Tracker, Abandon, Planner) ne
  changent pas ; `/tai abandon detail` garde les chiffres.
* **À faire** (`T.Todo`, `Modules/Todo.lua`, agent TODO) — la page de suivi refaite : une fenêtre (`/tai todo`, ou la
  dernière ligne de l'onglet « Quêtes ») déplaçable / redimensionnable (`TreeAI:RegisterMovable` si présent) et l'onglet
  « Quêtes » en version compacte (le Tracker lui passe la main dans `Push`, retour `"todo"` ; Todo désactivé = affichage
  historique). Sections : **Maintenant** (courses urgentes > carte épinglée > étape du plan > meilleure carte ; pourquoi,
  temps, boutons « Boussole » / « Y aller »), **Étapes du trajet** (`Tracker.PlanSteps` → `Planner:GetActiveSteps`),
  **Quêtes** par zone (cartes repliables : niveau en couleur, barres par objectif + « encore ~N × monstre », temps,
  distance, « À rendre », avatars de classe + progression des amis, récompense + % (Abandon/Boost), verdict Abandon au
  survol), **Courses** (`T.Predict.list` : les lignes que Inventaire / Chasseur / Guichets affichent vraiment), **Groupe**
  (`Missions.board` / `next`, repli `Squad:Common()` — façades du module Group), barre de **session** (`Boost:XPStats` = XP/h
  mesurée par Stats). Tout à la
  souris : puces de filtre (Tout / Zone / Prêtes / Groupe / Faciles / Épinglées), bouton de tri, « Itinéraire »,
  « Ordre auto », case épingler, masquer (réafficher en bas), glisser-déposer ou ▲▼ (ordre sauvé par perso
  `chars[k].modules.Todo.order`), recherche facultative. Rendu ≤ 2/s, mise en page refaite seulement si le contenu change.
  Résumé `summary.todo = {now, pins, hidden, ordered, filter, errands, shown}`. `/tai todo [montrer|cacher|ordre|tout|reset]`.
* (4.0) **Prévisions** : module retiré (tables approximatives). `T.Predict` (défini dans `Modules/Stats.lua`) garde
  `DropRate(mob, objet)`, `BestSource(objet)`, `Rates()` et `list` (lignes réelles d'Inventaire, Chasseur, Guichets)
  pour Tracker, Todo, Highlight, Abandon, Pro, MinimapPins : rien sous 10 min de mesure.
* Résumés IA : `summary.compass = {target={kind,questID,title,label,zone,src,dist,distText,eta}, targets,
  mapID, custom, hidden}`, `summary.tracker = {route={{id,title,ready,dist,xp,zone,done,total,xpm,sec,mates}}, sort, filter, totalMin, plan, easyNearby,
  pickup, available, dropMobs}` ; XP/h, or/h, kills, drops : `summary.stats` (module Stats, avec leur durée de mesure). Message interne `QUESTS_UPDATED` (émis par Quests.Update).

`Modules/Pro.lua` (agent X1, quêtes, onglets « Quêtes » + « Infos », bloc `Pro`) : journal classé par XP/min
(XP réelle en API moderne, sinon estimée ≈), quêtes à abandonner (abandon manuel), camp saturé (barres de nom
`UnitIsTapDenied`), PNJ nommé en vue, escortes (se préparer), chaînes / relais (Questie, lecture seule), donjons
du niveau + quêtes à prendre avant, checklist au niveau (entraîneur au prix lu chez lui, talents), repos
(auberges mémorisées), lieu de foyer conseillé, meilleure récompense connue (capturée chez le donneur), temps
restant par objectif, respawn observé, vrai taux de drop (mesuré par Stats via `T.Predict`, sinon mesure légère) ;
morts lues sur l'événement `DETECT` (« kill ») de Detect, seul lecteur du message d'XP.
Calculs hors combat (valeurs secrètes de Forever). `TreeAI.ProUtil` : `Money`, `Dur`, `Upgrade(link)`, `Out`.
`summary.pro = {top, abandon, checklist}`. `/tai pro`.
(4.0) `Modules/Economy.lua` est retiré : « Joindre compos » → Guichets (Errands), or/h → Stats, nœuds et montée de
métier → Métiers (Prof) ; plus d'améliorations marchand ni de réparation « prévue ».

`Modules/Dungeon.lua` + `Data/Dungeons.lua` (agent PVE, informatif / un clic) : onglet « Donjon » présent
seulement en instance ou quand un donjon est prévu (`/tai donjon <nom>|liste|stop|marque`, ou auto : groupe +
quête de donjon). Données : id de rencontre, noms FR et carte d'instance tirés des tables client Forever
(DungeonEncounter, Map, LFGDungeons/ContentTuning pour le niveau min ; 9 donjons propres à Forever inclus) ;
conseils / minuteurs / butin écrits d'après Classic = **indicatifs**. Avant : niveaux vs tranche, tank /
soigneur (rôle ou classe), nourriture/eau/munitions/durabilité (états Squad), quêtes à prendre (QuestieDB
lecture seule, `zoneOrSort` = zone du donjon), butin « amélioration pour toi » (`Boost.UpgradePct`) / « pour
<pote> ». Pendant : `ENCOUNTER_START/END`, `BOSS_KILL`, cris/émotes lisibles (ignorés s'ils sont secrets),
ou clic sur un boss → minuteurs au début du combat ; encadré du coin seulement pour les minuteurs prio 3, et
rétrogradé quand la frise Blizzard (`C_EncounterTimeline`) est active. Après : boss vaincus, liste de reprise
après wipe, run (pulls, morts, wipes, XP, or), mini-compteur lu dans `C_DamageMeter` hors combat. Pull /
patrouille = ligne de Fight reprise. Marque conseillée : un clic = un `SetRaidTarget` hors combat.
`summary.dungeon = {key, name, why, boss, run, meter, runs}`.
**DUNGEON2** (même module + `Modules/DungeonPrep.lua`) : onglet rangé en blocs (`SetTabLines(tab, lines, <bloc>)`,
ordre `Dungeon.BLOCKS` : en-tête + boutons « Prêt ? » / « Marquer » / « Partager », Prochain boss, Boss, Butin du groupe,
Quêtes du donjon, Préparation, Chasseur, Bilan). Fenêtre « Prêt pour <donjon> ? » (◀ ▶ = choisir le donjon prévu ;
Groupe / Quêtes du donjon / Préparation, ✓ ! ✗ par point, « Partager » par quête) : `Dungeon:ToggleChecklist(on)`,
`Dungeon.PrepRows(d)`, `Dungeon.Checklist(d)` (niveaux, rôles, réparation, sacs, nourriture, munitions, familier ;
Squad pour les potes), aussi par la pastille de la minimap (`TreeAI.HUB_EXTRA`, lu par Hub) et `/tai donjon pret`.
Quêtes du donjon : index Questie `zoneOrSort → ids` construit une fois par tranches de 1 500 hors combat, mémorisé
(`TreeAIDB…Dungeon.qidx`, refait si le nombre de quêtes Questie change) ; donneur (`startedBy` → PNJ + zone), qui ne
l'a pas (`C_QuestLog.IsUnitOnQuest` / `IsUnitOnQuest`), qui l'a (Squad), objet d'objectif → boss (`npcDrops`) ;
partage = `QuestLogPushQuest(index)` au clic, hors combat. Qui fait quoi : codes `jobs` par boss (`Data/Dungeons.lua`
`extra` + `jobs` : int, cc, magic, curse, poison, disease, fear, adds) attribués aux classes du groupe (`Dungeon.Jobs`,
`JobsText`). Pulls connus (`extra[key].pulls`, avant le boss `b`), boss optionnels (`opt`), cibles à contrôler (`cc`).
Butin : `Dungeon.LootPlan(d, members)` → par objet `need` (gain : `Boost.UpgradePct` pour toi, `Boost.ScoreLink` × poids
de classe contre l'équipement inspecté par Team — `Team.GearOf(guid).links`), `later` (mailles au 40…), `verdict`
Besoin / Cupidité par membre ; `Dungeon.ItemSource(id)` (contrat Gear). Bilan : `char.last` (durée, boss, morts toi +
groupe, wipes, butin reçu ≥ vert, quêtes finies, XP, or) à la sortie, suggestion + bloc 30 min ; `summary.dungeon.last`.
Coût : minuterie 1 s seulement en instance / boss / fenêtre ouverte, reconstruction sur drapeau sale (15 s onglet
visible, 60 s caché, 2 s pendant un boss à minuteurs) ; `session_sim` plafonne Dungeon à 80 kinstr/min. Tables de
butin, pulls, qui-fait-quoi = Classic (≈) ; donjons de Forever : boss et ordre du client seulement.

`Modules/Prof.lua` (agent PROF, onglet « Métiers » créé seulement si tu as un métier, bloc `Prof`, `/tai metiers
[cible N [métier]|debug]`) : montée de métier la moins chère (glouton, coût attendu par point ; formule Classic :
orange 100 %, sinon (gris − compétence) / (gris − jaune), + « Heures sup. » Legacy 4 %/rang), étapes « 112→125 : 14× X
(orange) », compos en sac/banque utilisées d'abord, liste d'achat + total (prix : vendeur → `Auction.Stats` →
Auctionator → fabriqué soi-même → revente × 4 ≈), « Fais N× X maintenant » (pin), prochain rang (niveau/compétence),
points Legacy (6 métiers de fabrication : 150/225/300), récolte (nœuds par couleur, meilleures zones, nœuds vus
d'Économie, niveau de dépeçage), astuces Cuisine/Secourisme/Pêche + camp, métiers des amis (Squad `pr`). Tout au clic :
ligne « Heures sup. » (clic = rang +1, Maj-clic = recettes connues seulement), clic sur un métier = cible suivante.
Données `Data/Recipes.lua` (tables client, `tools/wow_build_data.py --only recipes`, même calcul en Python :
`treeaicoach/wow/professions.py`). `summary.prof = {profs, legacy, plans, updated}`.
`Modules/Fight.lua` — **Combat** (CLEAN 4.0, informatif) : « pourquoi je suis mort » sur des faits (contre quoi — nom,
niveau, élite lus sur ta cible avant le pull —, durée du combat, vie au départ, noyade / épuisement) + un conseil seulement
s'il découle de ces faits ; « récupère avant le pull » (vie / mana / familier bas en ciblant un hostile hors combat,
mêmes seuils que la barre de combat). `summary.fight = {deaths, lastDeath = {killer, level, elite, advice, zone, time,
duration, startHP}}`. Retiré : compteur de pull, patrouilles, sort à interrompre, contrôles, menace (secrets sur Forever).
`Modules/Boost.lua` — **Rares** : un rare ou un trésor tout près = une ligne, clic = boussole (+ crâne sur ton clic).
`summary.boost = {raresSeen, lastRare}` ; garde l'ancien contrat (`XPStats` → Stats, `UpgradePct` → Gear).
(4.0) `HunterPro.lua` (fusionné dans Hunter : barre de piège, Feindre la mort) et `Comfort.lua` (ralenti auto → cœur,
`TreeAI.perfSlow` / `PERF_SLOW` ; nouveaux sorts → Touches ; `/tai perf` → cœur) sont retirés.
`Modules/Export.lua` + `Modules/Planner.lua` (agent PLAN-LUA, contrat `treeaicoach/wow/planner/SCHEMA.md`):
planificateur de trajet de quêtes. Export : `TreeAIDB.export` (journal + quêtes dispo zone courante + 2 voisines +
escouade, lu à l'exécution via l'API publique QuestieDB `LibQuestieDB` contrat 4, repli
`QuestieLoader._modules.QuestieDB`, jamais copié dans le dépôt ; coroutine ≤ 2 ms/image hors combat, ≤ ~300 Ko) +
statistiques apprises `TreeAIDB.learn` (drops, réapparition, vols, détours, affluence) et `chars[k].learn` (TTK par
écart de niveau, recherche, pause repas, efficacité, durée des quêtes, XP/h). `T.Geo` = géométrie partagée (uiMapID +
x, y en %). Planner : lit le global `TreeAIPlan` de l'addon de données séparé `wow_addon/TreeAI_Plan/` (écrit par
l'appli, `## OptionalDeps: TreeAI_Plan`), sinon plan glouton en jeu ; routes + verdict (OK / On va galérer / Trop long /
Pas faisable / Changez de zone), carte d'étape + 2 suivantes (onglet « quests », source `plan`, sinon Suggest),
progression via `C_QuestLog`, réparation insertion + Or-opt sur 20 étapes (hystérésis 10 % et 2 min), synchro groupe
`PLN` = `"1:<made>:<route>:<étape>:<empreinte>:<n>"` (≤ 1/15 s, jamais en combat ni en raid), « attendez X », rendez-vous,
guidage sur clic (`TreeAI.Compass:SetTarget{mapID, x, y (0–1), label, source="plan", questID}` → Shortest Path Forever
`NavigateRoute` → TomTom → `C_Map.SetUserWaypoint` + `C_SuperTrack`). `/tai plan [n°|passer|go|recalc]`,
`/tai export`. Contrat Tracker : `Planner:GetActiveSteps(n) → steps, index, total, titre` (étapes restantes à partir
de la courante, format `{k, q, o, map, x, y, label, eta, who}`, `label` toujours rempli) et repli
`Planner.active = {route={title, steps}, idx}`. Résumé `summary.plan = {v, src, route, title, step, steps, label, kind, quest, verdict, reason, xph,
minutes, left, hash, sync={n, synchro, avance, retard, autre}}`.

`Modules/Auction.lua` (agent AH): achat-revente à l'hôtel des ventes. **Aucune automatisation**: chaque
achat / annulation = un clic du joueur sur « Acheter » / « Annuler » (appel protégé dans le OnClick, une
seule action par clic, prix revérifié juste avant ; en API moderne: clic 1 vérifie, clic 2 achète ou
démarre l'achat de marchandise, clic 3 confirme). Scan complet = bouton (getAll / `ReplicateItems`,
1×/15 min) + lecture passive des pages que le joueur parcourt (≤ 50 annonces). API classique
(`QueryAuctionItems`…) ou `C_AuctionHouse`, testées à l'exécution. Historique par royaume-faction(-neutre)
dans `TreeAIDB.modules.Auction.realms[clé].items[itemID] = { v = prix marchand, last = time,
h = { jour, min, médiane des 5 moins chères, qté, nScans, unités disparues, heures, … } }` (≤ 14 jours,
plafond `maxItems`). Journal par perso `chars[k].modules.Auction.ledger` (courrier HV FR/EN),
`spent[date]` (budget). Résumé `summary.auction = { deals, sell, thin, realized = {today, week, total,
top}, budget, budgetLeft, spentToday, scanAge, neutral, flavor }`, lu par `treeaicoach/wow/auction.py`
(mêmes formules: commission 5 % / 15 % neutre, dépôt = floor(prix marchand × qté × 5 % (25 %) × h / 2),
profit = vente × (1 − commission) − achat − dépôt × P(invendu)). Prix marchand: `GetItemInfo` (11e
valeur), pas de table exportée. Auctionator: lecture seule de `Auctionator.API.v1` en secours.
`/tai hv [vendre|journal|fenetre|neutre|faction|auto]`.

`Modules/PvP.lua` (agent PVP): aides JcJ **informatives**, actives seulement si `settings.pvp` (`/tai pvp`,
OFF par défaut) ; onglet `pvp` « JcJ » enregistré / retiré selon ce réglage. Champs de bataille : minuteurs
d'objectifs (POI `C_AreaPoiInfo` textureIndex « contesté », comme Capping ; messages `CHAT_MSG_BG_SYSTEM_*`
FR/EN en secours ; Arathi 60 s, Alterac 300 s), porteurs de drapeau (vie % si l'unité est visible : cible,
survol, barres de nom, raid ; position `C_PvP.GetBattlefieldFlagPosition` / `GetBattlefieldFlagPosition`),
prédiction de score + chance de victoire (widgets 1893/1894 d'Arathi ou set du haut, taux classiques ou
mesurés), équipe ennemie (`C_PvP.GetScoreInfo` / `GetBattlefieldScore` : classes, soigneurs estimés),
file d'attente (`GetBattlefieldStatus`, `GetBattlefieldPortExpiration`). JcJ sauvage : ennemis vus
(classe, niveau, guilde, danger 0–10 vs ta classe/niveau), liste KOS `TreeAIDB.modules.PvP.kos[nom]`
(`/tai kos [Nom|liste]`), alerte prio 3 « Danger : 2 ennemis niv 60 près de toi ». Honneur de session
(`CHAT_MSG_COMBAT_HONOR_GAIN`, `GetPVPSessionStats`), historique par perso
`chars[k].modules.PvP.history = { {bg, dur, honor, won, at} }` → meilleur BG en honneur/h (`/tai jcj`).
Cible / duel : conseils par classe, contres du chasseur, auras défensives visibles. Clic sur un ennemi =
macro « TAI Cible » (`/targetexact Nom`, hors combat, pendant le clic) que le joueur appuie lui-même.
Jamais de journal de combat, de suivi de recharges ennemies, ni de ciblage automatique.

`TreeAI` (global) existe dès `Core.lua`. Un module se déclare au chargement
de son fichier (top-level) avec `TreeAI:RegisterModule`; il ne doit **rien**
faire d'autre au top-level que préparer des tables (pas d'appel d'API de jeu
« profond »: la DB n'est prête qu'à `OnInit`).
`Data/*.lua` remplit des tables sous `TreeAI.Data` (ex. `TreeAI.Data.Macros = {...}`).

Globales autorisées: `TreeAI`, `TreeAIDB`, `SLASH_TREEAI1/2`. Tout le reste en
`local`. Lua 5.1.

## API cœur (`Core.lua`)

### Modules

```lua
local M = TreeAI:RegisterModule("Hunter", {
  defaults     = { enabled = true, feedWarn = true },  -- réglages (compte)
  charDefaults = { lastFeed = 0 },                     -- données par perso
  classes      = { HUNTER = true },  -- optionnel: module actif seulement pour ces classes
  title        = "Chasseur",          -- optionnel: nom affiché (options)
  options      = {                    -- optionnel: auto-rendu dans les options
    { key = "feedWarn", type = "toggle", label = "Rappel nourrir le familier" },
    { key = "ammoMin",  type = "range",  label = "Seuil munitions", min = 50, max = 2000, step = 50 },
  },
})
function M:OnInit()   end  -- ADDON_LOADED: DB prête (self.db / self.char)
function M:OnEnable() end  -- PLAYER_LOGIN (ou tout de suite si déjà connecté), si activé
function M:OnDisable() end -- désactivé via options / `/tai module Hunter off`
function M:OnOptionChanged(key, value) end -- optionnel, après un changement dans les options
```

`RegisterModule(name, tbl)` retourne `tbl` enrichi. Champs/méthodes ajoutés:

| Membre | Rôle |
|---|---|
| `M.name`, `M.core` | nom, `TreeAI` |
| `M.db` | `TreeAIDB.modules[name]` (defaults fusionnés, `enabled` inclus) |
| `M.char` | `TreeAIDB.chars[charKey].modules[name]` (charDefaults fusionnés) |
| `M:RegisterEvent(event [, fn])` | `fn(self, event, ...)`; défaut: méthode `M[event]`. Plusieurs modules peuvent écouter le même event. |
| `M:UnregisterEvent(event)` / `M:UnregisterAllEvents()` | |
| `M:RegisterUnitEvent(event, unit1 [, unit2] [, fn])` | filtre sur l'unité (1er arg) |
| `M:On(message, fn)` | messages internes (voir plus bas), `fn(self, message, ...)` |
| `M:Suggest(id, text, prio, opts)` | = `TreeAI:Suggest` avec `opts.module = name` et id préfixé `name..":"` |
| `M:Clear(id)` | retire sa suggestion |
| `M:Print(...)` / `M:Debug(...)` | chat (debug seulement si `/tai debug`) |
| `M:IsEnabled()` | |

Les events/messages/comms d'un module désactivé (ou d'une autre classe que `classes`) ne sont pas appelés; ses suggestions sont effacées à la désactivation. Les erreurs dans un
handler sont attrapées (`pcall`) et affichées en debug, elles ne cassent pas
les autres modules.

**Profils de modules (LITE, « je lag »)** : `TreeAI.MODULE_INFO[name] = { lite, gate, combat, liteParts, desc }`.
Profil `essentiel` (défaut pour tout le monde, posé une fois via `TreeAIDB.profileV` + avis « Tout réactiver ») :
seuls les modules `lite = true` tournent ; `gate` = `"group"` (Group « Groupe »), `"ah"` (Auction, HV ouvert),
`"instance"` (Dungeon). `TreeAI:SetModuleProfile("essentiel"|"tout")`, `TreeAI:ModuleProfile()`, `TreeAI:Lite()`
(les modules `liteParts` en font moins : Highlight = sacs + butin, Macros = panneau seul, Abandon = à la demande).
Module coupé = ses événements sont **désinscrits** (rendus au réveil) et ses tickers **annulés** (repris) ; ses
`TreeAI:After` en attente ne partent plus. En combat, seul `AutoRefresh` des modules `combat = true` tourne.
Onglet « Plus → Modules » : une ligne par module (clic = on / off), `/tai perf` (coût par module mesuré par le
cœur), `/tai profil essentiel|tout`. Budget (tests/wow/session_sim.lua) : ≤ 1,5 M instructions Lua / min en
essentiel, ≤ 4 M tout allumé ; `--fine` donne le coût par fonction.

### Suggestions (le point central)

```lua
TreeAI:Suggest(id, text, prio, opts)
```
* `id` (string) clé unique: un nouvel appel avec le même id **remplace** (pas de doublon).
* `text` (string, FR court) — `nil`/`""` = retirer.
* `prio` : `1` info (gris/blanc), `2` conseil (jaune), `3` urgent (rouge,
  son + encadré d'alerte). Défaut 1. Constantes `TreeAI.PRIO_INFO/TIP/URGENT`.
* `opts` (facultatif):
  * `ttl` secondes avant disparition auto (défaut 60; `0` = permanent jusqu'à `ClearSuggestion`).
  * `cooldown` secondes avant de ré-annoncer (son/chat) le même id+texte (défaut 30).
  * `icon` texture (fileID ou chemin), `module` nom, `onClick` fonction (clic gauche sur la ligne).
  * `sound` (false = muet ; le son reste réservé à la prio 3), `chat` ignoré sauf si le joueur active « conseils dans le chat », `tab` onglet où lister la ligne, `tooltip`.
  * `macro` texte de macro proposé (affiché en infobulle).
* Retourne l'entrée `{id,text,prio,time,expires,...}`.

```lua
TreeAI:ClearSuggestion(id)        TreeAI:ClearSuggestions(modulePrefix?)
TreeAI:GetSuggestions()           -- liste triée prio desc puis récence
```
Message déclenché: `"SUGGESTION"` (entry) et `"SUGGESTION_CLEARED"` (id).
Les 40 dernières suggestions sont journalisées dans
`TreeAIDB.chars[key].suggestions` (lu par TreeAI Coach après /reload).

### Événements & messages internes

```lua
TreeAI:RegisterEvent(event, owner, fn)    TreeAI:UnregisterEvent(event, owner)
TreeAI:On(message, owner, fn)             TreeAI:Fire(message, ...)
```
Messages fournis par le cœur: `READY` (après OnEnable des modules),
`SUGGESTION`, `SUGGESTION_CLEARED`, `COMBAT_START`, `COMBAT_END`,
`SUMMARY` (avant écriture du résumé), `GROUP_CHANGED`, `COMM` (type, payload, sender, channel).

### Utilitaires

```lua
TreeAI:Print(...)  TreeAI:Debug(...)
TreeAI:InCombat()                     -- InCombatLockdown() protégé
TreeAI:RunOutOfCombat(key, fn)        -- tout de suite si hors combat, sinon file (1 par key) exécutée à PLAYER_REGEN_ENABLED
TreeAI:FrameLocked(f, ...)            -- vrai en combat si un cadre est protégé (sécurisé, ou NOTRE cadre ancré sur un
                                      -- cadre protégé de Blizzard : TargetFrame, boutons d'action…) → n'y toucher qu'à l'alpha
TreeAI:BlockedTop(n)                  -- actions bloquées par le jeu : { {func, ctx, kind, n, combat, last} }, total, session
                                      -- (TreeAIDB.diag.blockedTop / blockedTotal ; ligne « Blocages » dans Infos ; /tai bloque [reset])
TreeAI:After(sec, fn)                 -- C_Timer.After ou fallback OnUpdate
TreeAI:NewTicker(sec, fn[, owner])    -- retourne objet avec :Cancel() ; owner = module (défaut : celui dont le code tourne)
                                      -- (3.9.7) un seul ordonnanceur (OnUpdate du cœur) : phase propre par ticker
                                      -- (nombre d'or), ≤ 3 rappels « lourds » (période ≥ 1 s) par image, ~1,5 ms ;
                                      -- les :AutoRefresh() du cycle passent en tranches sur plusieurs images
                                      -- (4.0.1) ticker:IdleStretch(k) : k fois moins souvent quand le joueur est immobile
                                      -- (3 s sans bouger) ; cadence normale dès qu'il bouge. M.autoIdleSafe = true :
                                      -- le cycle :AutoRefresh() saute ce module tant que rien n'a changé (cycles calmes)
TreeAI:Here()                         -- (4.0.1) mapID, x, y du joueur, lus une fois par instant (valeurs secrètes écartées)
TreeAI:Stamp(st) / TreeAI:Moved(st[, d])  -- retient la position dans st / vrai si carte changée ou écart > d (0,002)
TreeAI:Moving([sec])                  -- a bougé ces 3 dernières s (coup d'œil 1/s de l'ordonnanceur)
TreeAI:OnMotion(owner, fn)            -- fn() quand le joueur se remet à bouger (dormir à l'arrêt, se réveiller ici)
TreeAI:QuestGen()                     -- compteur qui change à chaque événement du journal de quêtes (mémos par journal)
TreeAI:CharKey()                      -- "Nom-Royaume" (en jeu uniquement)
TreeAI:PlayerClass()                  -- "HUNTER", ...
TreeAI:Summary()                      -- table résumé du perso (voir schéma), modifiable
TreeAI:UpdateSummary()                -- recalcule les champs de base
TreeAI:RegisterSlash(cmd, fn(rest), help)  -- sous-commande /tai <cmd>
TreeAI.L                              -- chaînes FR; L["clé"] renvoie la clé si absente
TreeAI.Data                           -- tables de données (Data/*.lua)
```

### `TreeAI.API` (wrappers défensifs, ne lèvent jamais)

```lua
API.SpellName(spellIDOrName)  -> name|nil          API.SpellIcon(id) -> icon|nil
API.IsSpellKnown(spellID)     -> bool               API.SpellCooldown(id) -> start, duration
API.ItemName(itemID)          -> name|nil           API.ItemInfo(itemIDOrLink) -> name, link, quality, level, minLevel, type, subType, stack, equipLoc, icon, sellPrice, classID, subClassID
API.ItemCount(itemID [, includeBank]) -> n
API.NumBags()                 -> NUM_BAG_SLOTS (4)  -- sacs 0..NumBags()
API.BagSlots(bag)             -> n                 API.BagFree(bag) -> free, bagType
API.BagItem(bag, slot)        -> {itemID, count, link, quality, icon, locked, noValue} | nil
API.IterBags()                -> for bag, slot, item in API.IterBags() do ... end
API.IterBagsShared()          -> idem, relevé partagé dans une même image tant qu'aucun événement de sac (LECTURE seule)
API.FreeSlots()               -> free, total  (sacs généraux)
API.UseContainerItem(bag, slot)  -- HORS COMBAT, uniquement sur clic du joueur (marchand)
API.Aura(unit, spellIDOrName [, filter]) -> {name, icon, count, duration, expires, spellId, source} | nil   (filter "HELPFUL" défaut)
API.Auras(unit, filter)       -> liste d'auras lisibles, nombre d'auras masquées (valeurs secrètes : « illisible » ≠ « absente »)
API.AddOnMeta(addon, field)
API.GetMacroCount()           -> global, perChar
API.Money()                   -> cuivre
API.Distance(unit)            -> yards approx | nil (UnitPosition si dispo, sinon CheckInteractDistance)
```

### Comms de groupe (préfixe `TREEAI`)

```lua
TreeAI:Send(msgType, payload [, channel [, target]])
TreeAI:RegisterComm(msgType, owner, fn)   -- fn(owner, payload, sender, channel, msgType)
TreeAI:Serialize(tbl) / TreeAI:Deserialize(str)
```
* `msgType` court (`[A-Z0-9_]`, ex. `"QUEST"`, `"HELLO"`, `"POS"`).
* `payload`: string ou table **plate ou imbriquée** de string/number/boolean
  (sérialisée par le cœur, pas de fonctions).
* Canal par défaut: `RAID` si en raid, `PARTY` si en groupe, sinon rien
  (`"GUILD"`/`"WHISPER"` explicites possibles). Découpage automatique
  > 240 octets, file anti-flood (~5 msg/s), pas d'envoi si `settings.comms=false`.
* Ses propres messages sont ignorés. Format filaire: `1~TYPE~seq~i/n~data` (pas de `|`, évite les codes d'échappement).
* Le cœur gère `HELLO` (version) → `TreeAIDB.group.peers[sender] = {version, seen, class, level}`.

## SavedVariables `TreeAIDB` (schéma v1)

`## SavedVariables: TreeAIDB` (compte). Écrit par WoW au /reload, à la
déconnexion et en quittant. Lu par TreeAI Coach (`treeaicoach/wow/savedvars.py`).

```lua
TreeAIDB = {
  schema  = 1,
  version = "0.4.0",
  settings = {
    enabled = true, sound = true, chat = false, comms = true, debug = false,
    minPrio = 1,                -- prio minimale affichée
    panel = { shown = true, locked = false, scale = 1, alpha = 0.9,
              point = "CENTER", x = 300, y = 120, maxLines = 6, nowMax = 5, width = 280 },
    alert = { enabled = true, corner = "TOPRIGHT", duration = 8, scale = 1, alpha = 0.95, pos = nil },
    preset = "normal",          -- calme | normal | tout | perso
    noise = { nowMinPrio = 1, perModule = 2, alertGap = 20, topicGap = 120 },
  },
  onboarded = "0.4.0",          -- assistant de démarrage déjà vu
  flags = { commPause_donjon = true },  -- explications déjà dites une fois (pause synchro en donjon)
  modules = { ["Hunter"] = { enabled = true, ... }, ... },  -- réglages (compte)
  chars = {
    ["Nom-Royaume"] = {
      summary = {                     -- mis à jour au login, changement de zone/niveau, PLAYER_LOGOUT
        class = "HUNTER", className = "Chasseur", race = "Nain", level = 26,
        xp = 1234, xpMax = 25000, rested = 5000, -- rested peut être nil
        zone = "Contreforts de Hautebrande", subzone = "...",
        money = 123456,              -- cuivre
        bagsFree = 10, bagsTotal = 64,
        guild = "…" | nil, inGroup = true, groupSize = 4,
        spec = nil | "...",          -- si une API de spé existe
        updated = 1762500000,        -- time()
        -- les modules ajoutent leurs propres sous-tables: summary.hunter = {...}, summary.quests = {...}
      },
      suggestions = { {id=, text=, prio=, time=, module=}, ... },  -- 40 dernières
      dismissed = { [id] = {sk=, ts=} },   -- clic droit « masquer » : même sens caché 2 h (≤ 40, ANNOY)
      recentAlerts = { [id] = {sk=, ts=} }, -- encadrés des 5 dernières min (pas rejoués au /reload)
      modules = { ["Hunter"] = { ... } },   -- données par perso des modules
      panel = { tab=, mini=, point=, x=, y=, manual= },   -- état du panneau pour ce perso
      profile = "char" | nil,               -- profil perso : settings + moduleSettings ci-dessous
      settings = { ... }, moduleSettings = { ["Hunter"] = { enabled = true, ... } },
      layout = { sig = "1920x1080@1.00", manual = { panel = true, ... }, last = { role = {...} } },
      movers = { ["panel"] = { point=, x=, y=, scale= }, ... },   -- mode édition
    },
  },
  group = { peers = { ["Ami-Royaume"] = { version=, seen=, class=, level=, zone= } } },
}
```

## UI / Options

* Panneau compact déplaçable (la pastille de la minimap est le module `Hub`), **à onglets** : « Maintenant », « Quêtes »,
  « Groupe », « HV », « Infos ». Chaque onglet défile à la molette (barre fine à droite, « 3-10/14 »),
  lignes colorées par prio, cliquables (`onClick`), infobulles, clic droit = masquer un conseil
  (`TreeAI:DismissSuggestion(id)` : même sens — chiffres ignorés — et même sujet caché 2 h, gardé au /reload ;
  un danger passe toujours ; `TreeAI:IsDismissed(id, text, danger)`).
  Barre d'outils : boussole (si `TreeAI.Compass`), macros, groupe, options, cadenas, mini, fermer.
  Mode **mini** (une seule ligne : le plus important, sinon l'étape de quête ; clic droit sur le titre
  ou `/tai mini`) — c'est le mode par défaut d'un nouveau personnage.
  Onglet, position et mini mémorisés **par personnage** (`TreeAIDB.chars[key].panel`) ;
  verrou / échelle / opacité / largeur / lignes au niveau du compte (`settings.panel`).
* **Encadré d'alerte** (`settings.alert`) : petit cadre dans un coin (haut-droite sous la minimap par
  défaut, 4 coins au choix), seulement les conseils **prio 3**, 1–2 lignes, s'efface après `duration` s,
  clic = ouvre le panneau sur l'onglet concerné. `/tai alerte` pour tester.
* Aucun conseil n'est écrit dans le chat (`settings.chat` = false, et `opts.chat` ne peut pas forcer) ;
  `Print` = réponses aux commandes et erreurs. Sons : un par genre, voir « Sons, fenêtres, placement vivant ».
* Taint : cadres simples enfants d'`UIParent`, aucun modèle sécurisé, aucun cadre Blizzard modifié.
* Options : page principale (Aides PvP, Panneau, Encadré d'alerte, Sons et messages, Général) +
  **une page par module** (sous-catégories `Settings`, ou `.parent` en InterfaceOptions), case
  « Module activé » + ses `options` (`toggle`/`range`, `section=` facultatif pour un sous-titre).

### Contrat d'onglets (pour les modules, toujours garder un repli)

```lua
TreeAI:RegisterTab(id, title, order)              -- ids de base : now, quests, group, hv, info
TreeAI:SetTabLines(id, lines, source?)            -- remplace le bloc `source` (défaut "main") de l'onglet
--   lines = { {text=, prio=1..3, icon=?, onClick=?, tooltip=? (texte ou liste), pin=?}, ... }
--   pin = true : la ligne concourt aussi pour « Maintenant ».
--   control = true (+ label court, onClick obligatoire) : un vrai petit bouton dans la barre de l'onglet,
--   pas une ligne (« Recalculer », « Horizon +3 »…) ; TreeAI:GetTabControls() = géométrie (tests).
if TreeAI.SetTabLines then TreeAI:SetTabLines("group", lines, "Group") else --[[ ancien chemin ]] end
```
* « Maintenant » = top 5 (`settings.panel.nowMax`) des suggestions + lignes `pin`, par prio, sans doublon
  (texte ET sens : voir « Anti-bruit » plus bas).
* Les suggestions sont aussi listées dans l'onglet de leur module (`TreeAI.MODULE_TAB`, ou `opts.tab`),
  sinon « Infos ». Rafraîchissement regroupé (≤ 2/s, `TreeAI:RequestRefresh()`).
* Aussi : `TreeAI:SelectTab(id)`, `GetActiveTab()`, `SetMini(bool)`, `ToggleMini()`, `ShowAlert(e)`.

## Où va quelle info — guide d'affichage unique (agent CHECKUP)

Une seule règle pour tous les modules (le bilan module par module est dans le check-up de la ronde 5).

| Endroit | Ce qui y va | Ce qui n'y va jamais |
|---|---|---|
| **Maintenant** | au plus **3 lignes actionnables** (préréglage simple) : la chose à faire tout de suite, une par sujet | un constat sans action, un chiffre seul, un doublon d'une autre ligne |
| **Onglet** (Quêtes, Groupe, Plus › …) | le **détail** du module : lignes cliquables, ≤ 3 étapes d'une liste, 1 ligne par genre de problème ; les actions sans contexte = **boutons** (`control = true` + `label`) dans la barre de l'onglet | une liste brute de 10+ lignes, un bouton écrit comme une ligne (« [Clic] … »), un compteur seul (« Missions : 0 en commun ») |
| **Infobulle** | les **chiffres et le pourquoi** (coût, XP/min, DPS, liste complète, sources, alternatives) | une action (elle va sur la ligne) |
| **Encadré du coin** | **danger** (mort imminente, contrôle qui casse) ou **rare** (rare / élite / bonne affaire) — prio 3 | un rappel, une info |
| **Minimap** (MinimapPins) | les **lieux** (cible, rare, PNJ utile, ami loin) | un texte |
| **Fenêtre** | seulement **HV**, **macros**, **À faire** (Todo) — et le courrier « Joindre compos » collé à la fenêtre de courrier visible (jamais flottant) | un conseil |

**Chaque ligne** : verbe d'abord (« Répare… », « Lie… », « Achète… », « Nourris… »), ≤ 2 lignes courtes
(`TreeAI.LINE_MAX`), un clic fait l'évidence (sinon la ligne n'a pas de clic et doit être utile en elle-même),
jamais un chiffre qu'on n'a pas (« 0 dégâts », « Attaquants : 0 », « ? »). Une même info = **un seul
propriétaire** : les autres modules lui passent leurs chiffres pour l'infobulle au lieu d'écrire leur ligne.

Propriétaires (CHECKUP, 4.0) : réparation = `Inventory:inv_repair` · nourrir le familier = `Hunter:hungry` (vrais % de
dégâts de l'humeur en infobulle, clic = met à jour « TAI Nourrir ») · aspect = `Hunter:aspect` · apprivoiser =
`Finder:now` / onglet « Chercher » pour un chasseur · touches = onglet **« Touches »** (`binds`, module Binds : bouton « Lier le suivant »,
3 étapes, conflits groupés, listes en infobulle ; plus de ligne « macros pas créées » si `Macros:ready` est là ;
jamais de ligne « course automatique ») · classement XP/min = 1 ligne « Meilleure quête » de Pro (top 5 en
infobulle ; les temps par objectif se taisent quand Todo est actif, `Pro.Display`) · autotest = bouton « Autotest »
d'« Infos » (ligne seulement en cas d'échec) · « Prêt ? » = bouton de l'onglet Groupe (compteurs des missions en
infobulle) · talents : bouton « Parcours complet », rangs consécutifs fusionnés (« 1 → 3/5 »), rien quand l'arbre
n'est pas encore lu · équipement : une ligne par emplacement avec une vraie piste, pistes vagues regroupées
(« Surveille l'HV et le butin »), verbe selon la source dans « Maintenant » (« Équipe » seulement pour un objet des
sacs, sinon « Achète », « Fais fabriquer », « Choisis en récompense »…).

**Guidage : une seule source de vérité (agent COHERE).** `settings.guide` = `"auto"` (défaut, « Suivi Forever
(recommandé si installé) ») ou `"treeai"` (bouton « Guidage » de la page d'options principale, `/tai guidage`,
`TreeAI:SetGuideMode`). `TreeAI:ExternalGuide()` → `"spf"` / `"questie"` / nil (détection par `ShortestPathForever.API`,
`QuestieLoader` / `Questie`). Quand un guide externe est là, « Maintenant » saute les lignes de `TreeAI.GUIDE_IDS` /
`GUIDE_PREFIX` (objectifs, « près de la remise », « Suite », « à rendre ici », « Cible suivante »…) : SPF / Questie
les montrent déjà ; l'onglet Quêtes reste complet. Toujours : **une seule** ligne d'étape épinglée (`STEP_SOURCES` :
le trajet `plan` d'abord = l'arrêt courant de SPF via `Route:CurrentStep()`, sinon « Étape » de Quests), jamais coupée
par le budget ; ordre : danger → étape → actions (prio ≥ 2) → infos. Boussole : cachée tant que SPF guide
(`Compass.SPFGuiding()` : `Active` / `IsNavigating` / `CurrentStop`) → une seule flèche. En mode `treeai`, Route
n'envoie plus rien à SPF. Quêtes : `QUEST_TURNED_IN` / `QUEST_REMOVED` effacent tout de suite les lignes de la quête
(`Quests.Forget`, `Quests.gone` 15 s pour le journal qui traîne), « Suite » jamais vers une quête déjà prise / rendue.
Points de dressage : `API.PetFreeTrainingPoints()` (total − utilisés ; nil si 0 utilisé = total brut). Placement :
colonne « Suivi Forever » de SPF lue (noms + préfixes `ShortestPath` / `SPF_`, union des enfants d'une ancre,
réservée comme un suivi de quêtes, vue aussi par le reflow) ; `LAYOUT_V` = 3 (replacement unique).
**« Rendre » seulement pour MES quêtes finies** : `Planner:Evaluate` saute (transitoire, `s._absent`) toute étape
« rendre » dont la quête n'est pas dans le journal ET finie, et tout objectif d'une quête absente, sauf si une
prise / un objectif de cette quête les précède encore ; `Planner.stale` / `summary.plan.stale` + message `PLAN_STALE`
→ l'appli recalcule. Missions : une quête finie par les potes mais absente de mon journal = « Bravo, Charlie peuvent
rendre … », jamais « Rendez ensemble ».

## Interface d'abord, anti-bruit, placement, mode édition (agent POLISH)

**Tout est cliquable** ; les commandes sont secondaires. Barre d'outils du panneau : *Tout* (page de toutes
les actions, groupées par rubrique via `TreeAI.SLASH_GROUPS`, chaque ligne = bouton qui lance la même
fonction que `/tai <cmd>` dans `UserAction`), *mode édition*, boussole, macros, groupe, options, cadenas,
mini, fermer. Une commande enregistrée par `RegisterSlash(cmd, fn, help)` **avec un `help`** apparaît
automatiquement dans « Tout » (rubrique connue, sinon « Autres ») : c'est le moyen pour un module d'offrir
ses actions à la souris. Options : préréglage, profil, assistant, « Tout », mode édition, placement auto.

**Anti-bruit (Core)**
* `TreeAI.TOPIC_IDS` (id complet → sujet) + `TreeAI.TOPIC_PATTERNS` (repli par texte) ; `opts.topic` force
  un sujet. « Maintenant » garde **une ligne par sujet** (la plus prioritaire ; infobulle « Aussi signalé
  par : … » via `e._also`), au plus `noise.perModule` lignes par module, prio ≥ `noise.nowMinPrio` (ligne
  `pin` d'étape de quête exemptée), `panel.nowMax` lignes. `TreeAI:TopicOf(e)`.
* Encadré + son prio 3 : `TreeAI:AlertAllowed(e, opts)` — au plus 1 / `noise.alertGap` s (20), même sujet
  pas avant `noise.topicGap` s, **en combat seulement le danger** (`opts.danger = true` ou
  `TreeAI.DANGER_IDS`) ; le reste est rejoué 2 s après le combat s'il est encore vrai. `ResetAlertGate()`.
* Préréglages `TreeAI:ApplyPreset("simple"|"calme"|"normal"|"tout")` (`TreeAI.PRESETS` : 3/3/5/8 lignes,
  `settings.preset`, `settings.noise`) ; un réglage touché à la main → `preset = "perso"`. **simple** (défaut)
  = `noise.actionable` : dans « Maintenant », seulement les lignes cliquables ou prio ≥ 2, 1 par module ;
  proposé une fois aux anciens utilisateurs (`settings.simpleV` / `TreeAI.SIMPLE_VERSION`, une ligne
  « Mode simple activé : … clique pour revenir »).
* État du joueur : `TreeAI:PlayerState()` → `{ dead, ghost, taxi, mounted, resting, inCombat, inInstance, afk }`
  (cache 0,25 s, appels gardés) ; `TreeAI:Quiet(kind)` selon `TreeAI.QUIET_RULES` : `"buff"` (aspect, buff,
  familier, munitions : `QUIET_TOPICS` / `QUIET_MODULES`) muet mort / fantôme / vol / monture / repos et
  **jeté** par `Suggest` ; `"advice"` (défaut) muet mort / fantôme / vol, **mis en attente** (caché, sans son)
  puis réaffiché ; `"death"` (`opts.death = true` : récap, course au corps) jamais muet. `opts.kind` force le
  genre ; les lignes `pin` des onglets suivent la même règle.
* Textes : `TreeAI.SafeText` retire les glyphes absents des polices WoW (▶ ✔ → …), appliqué par `Suggest` et
  `SetTabLines` ; `TreeAI.ObjectiveName("Atrexis : 0/1")` = « Atrexis » (insécables FR, compteur devant /
  derrière, « (Terminé) »). Règle des textes : verbe d'abord, ≤ 2 lignes courtes, jamais un chiffre qu'on n'a
  pas (« 0 dégâts », « ? »), une ligne sans action = cliquable ou supprimée.
* Module coupé en direct : ses `Suggest` (avec `opts.module`) sont ignorés, ses blocs `SetTabLines` masqués
  (`TreeAI.SOURCE_OWNER`, `MAIN_OWNER` pour le bloc "main", `TAB_OWNER` cache l'onglet). `SetModuleEnabled`,
  `TreeAI:SetEnabled(bool)` (tout l'addon) : sans /reload.
* Profil : `TreeAI:SetProfile("account"|"char")`, `ProfileMode()` ; profil perso = `chars[key].settings` +
  `chars[key].moduleSettings` (copiés du compte au 1er passage). `TreeAI.settings` / `mod.db` pointent
  toujours vers le profil actif ; message `PROFILE_CHANGED`.
* Formats : `TreeAI.Fmt.Num(1234) = "1 234"`, `Fmt.Money(c) = "12 po 3 pa"`, `Fmt.Time(s) = "45 s" / "2 min 30"
  / "1 h 05"`, `Fmt.Pct`. Style : tutoiement, phrases courtes, couleurs par prio (gris / or / rouge),
  icône par défaut d'une ligne selon son sujet ou son module.

**Assistant** (`Options.lua`) : `TreeAI:ShowWizard(step)` au 1er lancement (`TreeAIDB.onboarded`), 3 étapes
(présentation · préréglage + position du panneau dont « Auto » · Macros / Touches / Boussole / Aide).
`TreeAI:ShowHelp(groupIdx)` = page « Tout », `TreeAI:GetSlashHelp()` la liste groupée, `PrintHelp()` le chat.

**Placement automatique** (`UI.lua`) : `TreeAI:ScanScreen()` lit (jamais n'écrit) les cadres connus
(`TreeAI.LAYOUT_SCAN_NAMES` : minimap, barres, chat, suivi des quêtes/Questie, cadres d'unité, buffs,
Details/Recount/Skada/Bartender/ElvUI…) + `EnumerateFrames` (enfants visibles d'UIParent), `IsVisible` +
`GetRect` ramenés à l'échelle d'UIParent, sans plein écran / caché / minuscule / les nôtres → grille 16 u.
`TreeAI:AutoLayout(force)` place par rôle : encadré près de la minimap (`settings.alert.pos`), boussole en
haut au centre hors cadre de cible, panneau du côté le plus libre (largeur / lignes adaptées), suivi sous le
suivi de quêtes (ou côté opposé), barres tir auto / pièges au-dessus des barres d'action (positions écrites
dans leurs SV). Hors combat (sinon après), au 1er lancement et sur `UI_SCALE_CHANGED` /
`DISPLAY_SIZE_CHANGED` (`CheckAutoLayout`, signature `LayoutSignature()` dans `chars[key].layout.sig`).
Cadre déplacé à la main (`layout.manual[role]`) = gardé ; `force` / « réinitialiser » rendent l'auto.
**4.0.1 (PLACEBUG)** : UN seul pilote (`TreeAI:RequestLayout(kind)`) — jamais de placement sur un écran pas posé :
taille d'UIParent valide (`screenSize()` = nil sinon, plus de repli 1920×1080) et identique 1,5 s, 3 s après
l'entrée dans le monde, hors combat ; puis `CheckAutoLayout(strict)` : même écran = rien ne bouge, 1er lancement /
résolution vraiment changée (> 1 case et > 2 %, `TreeAI._ScreenChanged`) = placement des seuls cadres automatiques
(`AutoLayout(force, strict)` : `strict` = jamais sur un scan vide). `TreeAI:IsPlayerPlaced(id)` (glissé par n'importe
quelle poignée — crochet `StartMoving` / `StopMovingOrSizing` sur nos cadres —, mode édition, `opts.isPlaced`) :
jamais repris par l'auto ni écarté par le reflow. Après un glisser, la place est relue du rectangle réel et reposée
sur UIParent avec un point explicite (`TreeAI:AnchorFromRect(f)`, `TreeAI:SetAnchor(f, point, x, y)` : point valide,
nombres finis, ancrage d'avant remis si SetPoint échoue ; `TreeAI.ValidAnchor`) ; jamais de coin d'écran pour une
place auto (sauf disposition « Coins ») ; place sauvegardée hors écran = remise (`_SanitizePlacements`).
`TreeAI:ScreenStable()` = écran posé et contrôlé (le reflow attend). Tests : `tests/wow/geometry.lua` (harness
`--geometry`, géométrie et connexion du vrai client) + `tests/wow/test_placement.py`.
Depuis 3.9.1 (`TreeAI.LAYOUT_VERSION` = 2, `layout.v`) : préfixes `TreeAI.LAYOUT_SCAN_PREFIX` (LibDBIcon10_,
Questie_, Details, Recount, CompactRaid/Party, PartyMemberFrame), suivis de quêtes réservés sur toute leur colonne
(ils grandissent), cadres de groupe réservés même cachés ; ordre : **radar** sous la minimap si libre sinon à sa
gauche → encadré sous la minimap → boussole en haut au centre → panneau sur un bord (jamais le centre, loin des
cadres de groupe, largeur ≥ 300). Replacement unique pour les joueurs existants (places manuelles gardées).
`TreeAI:ResetInterface()` (bouton « Réinitialiser l'interface » des options, page **Apparence** et sous-menu
« Plus », `/tai reinit`) remet tout par défaut. Depuis UI2, « Plus » = **menu déroulant** (`TreeAIMoreMenu` sous l'onglet : onglets secondaires avec icône, séparateur, actions Macros / Déplacer / Tout / Réinitialiser ; fermé au clic ou Échap, `TreeAI:GetMoreMenu()`), l'onglet « Plus » affiche l'onglet secondaire actif. Panneau SIMPLE : 4 onglets (`now`, `quests`, `group`, « Plus »
qui ouvre une 2e rangée : onglets secondaires + actions), libellés mesurés (`GetStringWidth`, court puis « … »),
géométrie dans `TreeAI:GetTabLayout()` / `TreeAI:GetRowLayout()` (lignes sur 2 lignes). Onglet virtuel
`talents` = bloc Talents d'« Infos ». Boussole : `hideDup` (une seule flèche si Blizzard / SPF / TomTom guide
déjà la même cible, `Compass.OtherGuide(t)`). Course auto : `Binds.AutoRunHint()` / `AutoRunStep()` (lie la
commande du jeu TOGGLEAUTORUN, jamais `ToggleAutoRun`).

**Cadres déplaçables + mode édition** :
```lua
-- chaque module inscrit ses cadres (gardé : if TreeAI.RegisterMovable then … end)
TreeAI:RegisterMovable(frame, "radar", "Radar", {
  onMoved = function(point, x, y, scale) M.db.pos = { point, x, y }; M.db.scale = scale end, -- sinon TreeAI garde
  onReset = function() M.db.pos = nil end,       -- « remettre auto »
  minScale = 0.5, maxScale = 2, show = fn, hide = fn })   -- show/hide : afficher en mode édition
```
`TreeAI:SetEditMode(bool)` (`/tai edit`, bouton clé) : calque coloré + nom par cadre, glisser (aimant :
`TreeAI.SnapRect` — grille 10 px, bords d'écran, bords des autres cadres), molette = échelle, clic droit =
`ResetMovable(id)` ; barre *Verrouiller tout* (`SetFramesLocked`) / *Tout remettre auto* / *Fini*.
`TreeAI:CommitMove(id, l, b, scale)` enregistre par personnage (`chars[key].movers[id]`) et marque le
manuel prioritaire. Déjà inscrits : panneau, encadré d'alerte, boussole, suivi.

## Panneau, lignes, régions, pastille (agent MENU)

* **Une ligne = un genre** (`TreeAI.Theme.LINE`, `Theme:LineStyle(kind, prio)`) : `danger`, `warn`, `turnin`, `quest`,
  `kill`, `collect`, `use`, `travel`, `flight`, `boat`, `gear`, `pet`, `group`, `money`, `consumable`, `info` → une
  icône + une couleur partout. Rouge = urgent (prio 3) seulement, or = argent, bleu clair = trajet, orange = mise en
  garde ; les titres de quête gardent la couleur de difficulté du jeu (`Theme:QuestDiffColor(ql, pl)`). Genre
  deviné par le verbe (« Tue », « Ramasse », « Rends », « Va », « Prends le vol »…) ou passé par le module
  (`kind = "money"`). L'icône du module est gardée pour équipement / consommables / argent / familier / équipe / info.
* **Champs de ligne en plus** (`SetTabLines`) : `questID` (regroupement par région, boussole), `right` (chiffre de
  la colonne de droite ; sinon le dernier « · 396 m » / « (≈ 3 min) » du texte y est déplacé), `clickHint` (1re
  ligne de l'infobulle = ce que fait le clic ; sinon la ligne « Clic … » du module remonte en tête), `header`.
* **Rendu** : colonne de droite alignée, largeur commune à l'onglet ; texte sur 1–3 lignes mesurées (jamais
  « … ») ; chevron + curseur « main » sur ce qui se clique ; titres de section avec icône ; pastilles de compte
  sur les onglets (quêtes, urgences) ; « Plus » rangé en Jeu / Outils / Réglages ; panneau vide = « Rien d'urgent —
  continue « <quête> » ». Géométrie exposée pour les tests : `TreeAI:GetRowLayout()` (kind, icon, couleur, textPx,
  avail, colW, chevW, right…).
* **Meilleur guide** : la 1re ligne de « Quête en cours » affiche distance · durée (cible de `Compass.state`, même
  contrat que Route / Compass ; sinon l'entrée du suivi), direction + pourquoi en infobulle ; mise à jour par le
  ticker 1 s existant, sans redessin complet (`TreeAI:_UpdateGuide()`).
* **Pastille de la minimap** (`Modules/Hub.lua`, `TreeAI.Hub`) : bouton rond enfant de `Minimap` (glisser autour
  du bord, angle sauvé, minimaps carrées via `GetMinimapShape`), aussi dans `AddonCompartmentFrame` s'il existe.
  Clic = fenêtre TreeAI (UIREDO ; sans le module Window : menu hub) ; clic droit = menu hub (Jeu : tous les onglets ;
  Outils : Recherche, Meilleur familier, Macros, À faire, Boussole, Radar ; Réglages : Options, Profil, Erreurs,
  Blocages, panneau, Déplacer, Réinitialiser) ;
  infobulle = prochaine étape, XP/h, niveau dans… `/tai menu` ouvre le même menu. `TreeAI:HasSlash(cmd)` (Core)
  sert à n'afficher que ce qui existe.
* **Donjons** : fiche « joueur confirmé » (`T.Data.Dungeons.cards[key]` : `danger`, `tank`, `heal`, `dps`) →
  « Ce qui tue le groupe : … » + « <rôle> (toi) : … » (les 3 rôles en infobulle) ; textes de boss réécrits.

## Fenêtre TreeAI et carte « Prochaine étape » (agent UIREDO, 4.0)

`Modules/Window.lua` + `Modules/WindowPages.lua` (`TreeAI.Window`, module `Window`, mode sûr). Rendu seulement : les
données viennent des contrats existants (`GetTabLines`, `Route:Stops(64)`, `Planner:StepLabel/Navigate`,
`Quests.last`, `ZonePlan:HeadLines/StopMenu`, `Dungeon.Index/ForLevel/Quests/LootPlan/SetPlanned`, `Hub.Entries`,
`TreeAI:OptionEntries()` (Options.lua : mêmes pages, même moteur TIDY)).
* **Fenêtre** `TreeAIMainWindow` (façon Leatrix Plus) : « TreeAI » + version en haut à gauche, navigation à gauche
  (Maintenant · Quêtes · Trajet · Groupe · Donjons · HV · Outils · Fonctions · Options ; ligne choisie surlignée),
  grand titre de page + sous-titre, recherche en haut à droite (filtre la page : lignes, arrêts, donjons, pages
  d'options), UN bouton d'action en bas à droite (Arrêter / Reprendre, Prévoir ce donjon, Ouvrir la fenêtre HV, Recharger…).
  Redimensionnable (taille + position gardées, `settings.windows.main`), Échap ferme. Ouverte par la pastille
  (clic gauche ; clic droit = menu rapide), la touche `TREEAI_MAIN` (« TreeAI : ouvrir / fermer la fenêtre », sans
  touche par défaut, `TreeAI.ToggleMain()`), `/tai fenetre [page]`. Un clic qui ouvrait un onglet du panneau
  (encadré d'alerte, pastille, « Prêt ? ») ouvre la page de la fenêtre (`Window.OpenTab(id)`).
* **Quêtes** (façon Adventure Guide) : carte d'en-tête « Histoire : <zone> · n en cours · m proches · durée »,
  étapes numérotées (pastille ronde + ! / ?, sous-titre gris : à prendre / objectif 3/8 · « quête » / à rendre ·
  distance, direction, temps), quêtes à venir aux couleurs de niveau, sections repliables (À rendre, En cours ici,
  Nettoyer mon journal, Direction <zone>, Autres conseils), état gardé par perso (`chars[k].modules.Window`).
* **Trajet** : tous les arrêts numérotés, zone de travail / suivi / tri du producteur ; **Donjons** (façon Journal des
  donjons) : cartes en dégradé de couleur par tranche de niveau + icône du jeu (aucune illustration copiée), badge
  « Niv. a-b », « x/y quêtes » + coche, « prévu / tu y es » ; clic = détail (quêtes du donjon, boss numérotés,
  butin pour le groupe, conseils, « Partager les quêtes ») ; **Options** : liste des pages + la page construite
  par le moteur TIDY (`TreeAI:OptionEntries()`, `TreeAI:RefreshOptionWidgets()`), recherche dans les titres,
  descriptions et libellés.
* **Carte « Prochaine étape »** `TreeAICard` : remplace le panneau texte (option ; le panneau reste dans Outils et
  s'ouvre à la main) ; posée à la place que le placement automatique a trouvée pour le panneau (`PanelChar()`),
  descend sous un suivi de quêtes qu'elle toucherait ; déplaçable (mode édition, id `card`). En-tête (icône,
  « Histoire : zone », résumé) + danger en rouge + 1–5 étapes numérotées. Aucune minuterie : suit les rendus du
  panneau (≤ 1 / 2 s, clé `TreeAI:DataGen()` + étapes + position), signature comparée.
* Jetons communs (`Theme.TYPE`, `Theme.BLOCK`, `Theme.STEP_ICON`, `Theme.GRID` = 4 px, `Theme.SHORTCUT`) ; genre de
  skin `card` (fond plat, liseré 1 px) + `Theme:Shadow(frame)` pour la fenêtre et la carte ; survol = liseré 1 px
  (couche HIGHLIGHT) ; titres sur une ligne (« … » + texte entier en infobulle) ; états vides = une phrase + le
  bouton qui agit (« Calculer le trajet », « Reprendre le trajet », « Voir tous les donjons », « Effacer la
  recherche »…). La couleur d'accent des options = l'or du thème.
* **Barre « Raccourcis »** (`Modules/Shortcuts.lua`, module `Shortcuts`, mode sûr, rendu seulement) : UNE barre
  `TreeAIShortcuts` au lieu des barres séparées. Un module y amarre sa barre :
  `T:RegisterShortcutBar(frame, { id, label, order, inset, chrome = {régions cachées une fois amarrée}, movable = id
  du déplaçable retiré, onUndock = fn })` ; `T:ShortcutDocked(frame)` (le module ne la place plus) ;
  `T.Shortcuts.Changed()` après un changement de boutons. Quick = « Quêtes · voyage », QuickFight = « Combat ·
  familier » (logique inchangée dans les modules). Un fond, une poignée (glisser, clic droit = réglages), un titre
  par groupe, un filet entre groupes ; groupe vide retiré, barre cachée quand rien ne sert ; un seul cadre en mode
  édition (`TreeAI:UnregisterMovable(id)` dans UI.lua) ; tout changement hors combat. Bouton commun
  `Theme:ShortcutStyle(b)` (fond, liseré, enfoncé, touche liée `Theme:HotKey`, `Theme:KeyHint` en infobulle) ;
  Bindings.xml : « Raccourci quête / voyage / combat : … » (`CLICK TreeAIQuick…:LeftButton`, sans touche par défaut).
  Options : une seule barre, verrou, titres, taille.
* Infobulles des points de la minimap (`MinimapPins.TipLines`) et de la boussole : ce que c'est, distance + temps,
  pourquoi maintenant, ce que fait le clic.

## Nettoyer mon journal + donjons déjà faits (agent JOURNAL, 4.0.1)

`Modules/Abandon.lua` (module « Nettoyer mon journal », **mode sûr** : faits du jeu seulement, deux clics pour agir ;
allumé une fois pour qui était en mode sûr) + `Modules/JournalPage.lua` (`T.JournalPage`, page « Journal » de la
fenêtre, juste après « Quêtes », donc aussi dans la pastille ; rendu seulement) + `Modules/Dungeon.lua` /
`Data/Dungeons.lua` (`quests`).
* **Raisons = faits du jeu**, jamais une durée estimée (`Abandon.Core.Facts(q, ctx)`, `Core.Cleanup(qs, ctx)` purs,
  `tests/wow/abandon_spec.lua`) : échouée / chrono écoulé (`C_QuestLog.IsFailed`, `GetTimeAllowed`) · chaîne bloquée
  (relais dont la suite est dans le journal ou finie selon `IsQuestFlaggedCompleted` ; quête de livraison sans objectif
  dont l'objet `sourceItemId` n'est plus dans les sacs, `C_Item.GetItemCount(id, true)`) · doublon (QuestieDB
  `exclusiveTo` toutes deux dans le journal, deux relais vers la même suite) · grise (couleur du jeu,
  `UnitQuestTrivialLevelRange` ; XP réelle `GetQuestLogRewardXP`) · donjon déjà fait (`Dungeon.IsDone`, historique ou
  verrou) · trop dure pour le groupe ACTUEL (étiquette donjon à moins de 4, groupe conseillé du jeu > groupe, élite < 3,
  rouge < 3) · autre continent (`GetQuestUiMapID` → continent `Geo.Rect`) ou trajet ≥ 15 min (`Route.LegSeconds`, vitesse
  NavTime). Jamais pour une quête prête à rendre, un arrêt du trajet, une quête qu'un ami a, une grise qui ouvre une
  suite non grise ou dont la récompense est une amélioration. `A.STRONG` (échouée, bloquée, doublon, grise, donjon) =
  « Retire » ; trop dure / loin = « à voir ». Le suivi (Tracker / Todo / Plain : `Abandon.evals[i].verdict`) dit
  « À retirer » sur les mêmes faits seulement. Sorties : `Abandon.cleanup = {groups = {{key, title, advice, items =
  {{q, facts, key, strong}}}}, count, strong}`, `Abandon.CleanupNow()` (la page la calcule même module coupé),
  bloc « Nettoyer mon journal » de l'onglet Quêtes (clic = la page), rappel `Abandon:journal` dans « Maintenant »
  montré en avis (badge « i ») sur la carte quand le journal a ≥ 20 quêtes (option `hintMin`) ou ≤ 3 places,
  `summary.abandon = {list = {{title, verdict = "abandon"|"later", why, detail, reason, group}}, count, strong}`.
* **Abandon dans le clic** : `Abandon.Request(questID, title)` (1er clic → « Confirmer l'abandon de « X » ? », 2e clic
  sur la même quête ≤ 8 s → `Abandon.AbandonNow`), `Abandon.BatchRequest("grey")` (« Tout ce qui est gris » : une
  confirmation puis UN abandon par clic), `Abandon.ButtonState(id)` (`go` / `confirm` / `combat` / `guided`).
  `AbandonNow` refuse hors d'un clic (`T:InUserAction()`) et en combat ; puis seulement des fonctions C :
  `C_QuestLog.GetSelectedQuest` → `SetSelectedQuest(id)` → `SetAbandonQuest()` → relit `GetAbandonQuest() == id` →
  `AbandonQuest()` → remet la quête « à abandonner » de Blizzard (une fenêtre « Abandonner ? » ouverte garde la
  sienne) puis la sélection (clients classiques : `GetQuestLogSelection` / `SelectQuestLogEntry(index)` /
  `SetAbandonQuest` / `GetAbandonQuestName() == titre` / `AbandonQuest` / sélection remise). **Analyse de
  contamination** : documentées sans restriction ni protection (Forever 1.60.1) ; une fonction C ne contamine rien ;
  aucune fonction Lua ni variable de Blizzard (ni `QuestMapFrame_*`, ni `StaticPopup_Show("ABANDON_QUEST")`, ni
  `ShowUIPanel`) ; ce qui avait bloqué « Abandonner » en 3.8 (sélection changée HORS CLIC, journal ouvert par l'addon)
  n'arrive jamais. Repli « où cliquer » sans aucun appel (« Ouvre ton journal (L) > « X » > « Abandonner ». ») si
  l'API manque, si l'option `direct` est coupée, ou si le jeu a bloqué une fois (`ADDON_ACTION_*` pendant l'appel →
  `db.refused`, plus jamais tenté). Mock strict : ces appels hors clic / en combat = action bloquée ; deux
  `AbandonQuest` dans un même clic = violation.
* **Donjons déjà faits** (`Dungeon.DoneInfo(d)` → `{done, src = "run"|"lock"|"quests", at, kills, total, lock,
  qDone, qTotal, qInLog, label}`, cache 30 s) : 1) historique TreeAI `char.kills[clé][boss] = date` (ENCOUNTER_END
  réussi / BOSS_KILL, écouté même module Donjon coupé ; fait = le dernier boss obligatoire ou tous ; runs archivés
  avant 4.0.1 comptés s'ils ont tous les boss) ; 2) verrou du jeu (`GetNumSavedInstances` / `GetSavedInstanceInfo`,
  14 retours, carte en dernier ; `RequestRaidInfo` au plus une fois / 2 min hors combat, `UPDATE_INSTANCE_INFO`) ;
  3) quêtes du donjon finies selon le jeu (`Data/Dungeons.lua quests` = id QuestieDB Forever par `zoneOrSort`, vérifiés
  dans la table client QuestV2 ; `h` / `a` / `n` = faction, `r` / `c` = masques race / classe ; `qsort` = zones de
  quêtes en plus de `area` pour l'index « à prendre »). Libellés : « Fait le 3 nov. », « Fait (verrou en cours) »,
  « Quêtes toutes faites », « Fait en partie : 2/5 boss », « Verrou : 2/5 boss ».
* **Contrats pour les autres modules (ZonePlan / Quests / Route)** : `T.Dungeon.IsDone(id)` (clé, nom, carte ou
  table) → `fait, infos` ; `T.Dungeon.QuestDungeon(questID)` → donjon `{key, n, min, max, map, …}` ou nil (liste du
  donjon, puis index QuestieDB, puis en-tête de zone du journal EXACTEMENT égal au nom du donjon — jamais « Les
  Paluns » → « Excavations ») ; `T.Dungeon.QuestIDs(d)` (quêtes du donjon pour TON perso, `.set`) ; `T.Dungeon.QuestForMe(id)`.
  Ne jamais proposer une quête d'un donjon fait : `local d = T.Dungeon.QuestDungeon(id) if d and T.Dungeon.IsDone(d)
  then … end`. Le module Donjon ne prévoit plus tout seul un donjon fait ; « à prendre » ne liste que les quêtes de ta
  faction / race / classe. Cartes « Donjons » : « Fait le … » (vert, coche), « Quêtes faites x/y », « Aucune quête pour
  toi », pas encore faits + à ton niveau d'abord ; fiche : « Faites : x/y (selon le jeu) », boss « (déjà tué) ».

## Infos objets : module `ItemInfo` (agent ITEMINFO, 4.0.2, `Modules/ItemInfo.lua`, **mode sûr**)

Quelques lignes VRAIES sous chaque infobulle d'objet (sacs, butin, récompenses de quête, marchand, HV, liens du chat,
objets portés) : une ligne par sujet, **≤ 6**, label coloré par sens (vert mieux, rouge non / moins bien, orange plus
tard, or info), texte clair ; **Maj** = détails gris (au prochain rafraîchissement de l'infobulle par le jeu, jamais
forcé) ; « (Maj : détails) » quand il y en a. Une case par sujet (options « Infos objets », famille « Sacs, or,
métiers »). **Seul propriétaire des lignes d'objet** : `T.ItemInfo.Owns()` vrai → les crochets d'infobulle d'objet de
Gear (« mieux que ce que tu portes », prix), Highlight (« Quête : X — encore 4 » + contour, repris par ItemInfo via
`Highlight.TooltipGlow`) et Quests se taisent ; module coupé → leurs lignes reviennent.
* **Amélioration** (`Gear.Engine` : `CanUse`, `UpgradePct`, `Verdict`, `ScoreLink`, `ItemStats`, mêmes poids que
  l'onglet « Équipement ») : « Mieux / Nettement mieux / Pareil / Moins bien : +6 Agi, +3 End » (3 stats les plus
  pesées, arme : « DPS 9,8 → 14,5 »), anneau / bijou / arme 1M comparé au plus faible des deux portés, 2M = main droite
  + main gauche, « emplacement vide », « niv 30 requis », « Pas encore : mailles portables au niv 40 », « Pas pour toi :
  … » (dont « réservé à d'autres classes » : masque de classes des données du client, `Gear.Rec` / `Gear.Usable`),
  munitions (« Pas pour ton arme : balles (ton arc tire des flèches) », écart de DPS si le jeu le donne). Chasseur :
  « (≈ +4 % de dégâts) » = DPS à distance de la fiche (`UnitRangedDamage`, hors combat ; en combat le dernier relevé)
  + écart (DPS de l'arc, 1 Agi = 2 PA à distance, PA / 14) ; rien si l'écart touche une stat hors modèle (toucher,
  critique) ou < 0,5 % ; l'estimation est écrite dans les détails. Rien sur l'objet porté lui-même.
* **Groupe** (en groupe, objet équipable) : pour qui l'objet est fait (armure de la classe à SON niveau — mailles au
  40 —, arme maniée, arc / fusil → chasseur, bijoux : la classe qui valorise le plus ces stats) → `Dungeon.LootItem`
  (niveau requis, gain pour toi et pour les amis inspectés `Team.GearOf`) : « Groupe : Besoin pour toi (mieux) et Furtif
  · Cupidité pour les autres » / « personne n'en a besoin → Cupidité pour tous » ; détails : qui pourra s'en servir à
  quel niveau, gain inconnu (pas inspecté). Déjà dans tes sacs : « utile à X » (seulement un ami). Chez le marchand
  (objet à acheter) et sur une récompense de quête (un choix) : ni ligne de groupe ni verdict de valeur, les faits
  seulement ; le contexte (porté / sac / butin / marchand / récompense) fait partie de la clé du cache.
* **Valeur** : marchand (la pile : nombre lu par le GUID de l'infobulle `C_Item.GetItemLocation` / `GetStackCount`,
  sinon la case du sac ou du butin du propriétaire, vérifiée) ; HV seulement si `Auction.Liq(id).ok` (preuves de
  ventes) : « HV ~45 pa, se vend ~6/jour », sinon « HV : pas de marché fiable » (raison et dernier prix dans les
  détails) ; jamais d'HV pour un objet lié (`bindType` 1 / 4, `C_Item.IsBound`) ou de quête. Verdict : « → Garder »
  (amélioration, quête, compo de tes recettes, familier, munitions — ligne seule cachée : les autres lignes disent
  pourquoi), « → Donne-le à X » (un ami en a besoin, objet échangeable), « → Mettre à l'HV » (marché fiable et
  `Auction.PostPrice(...).better == "ah"`), « → Vendre au marchand » (gris, objet de quête plus utile, équipement lié
  inutile). Prix unitaire seul = déjà écrit par le jeu → détails seulement.
* **Quête** (`Highlight.Data()` : journal + QuestieDB, règles DATA ; Highlight coupé → `Quests.ObjectiveFor`) :
  « Objet de quête : « X » (3/8) », « (8/8) — à rendre, garde-les », « — à utiliser », « Pour une quête à venir », « ! Commence
  une quête : « X » (niv 27) » (case du sac / du butin, sinon QuestieDB `startQuest` ; ni dans le journal ni faite),
  « Objet de quête : plus utile » seulement si la case / le butin / QuestieDB permettent de le savoir (jamais deviné).
  Questie chargé : sa propre ligne de progression, la nôtre se tait (départ et « plus utile » restent).
* **Métiers** (`Data/Recipes.lua`, `Prof.ScanProfessions`, recettes connues `Prof.char.known` lues dans la fenêtre du
  métier) : « Métiers : compo de « Bandage en laine » (×6 possibles) et 1 autre recette » (fabrications possibles avec
  tes sacs ; détails : chaque recette + couleur de montée), sinon « sert en Couture (3 recettes à ta portée) », sinon
  (marchandise) « sert en Cuisine ».
* **Familier** (chasseur, familier présent : `Hunter:GetDiet()` × `Hunter.FOODS` + `Data/PetFood.lua`) : « il aime ça
  (viande) », « il en mange (viande), effet réduit » (détails : niveau conseillé), « trop faible pour lui », « il n'en
  mange pas (il mange : poisson, viande) ».
* **Sources** : taux MESURÉ = les comptes de Stats (`Stats.Account().drops` : corps fouillés / objet trouvé, ≥ 10 corps,
  sans a priori) « Source : Lion des collines (8 sur 25 corps, 32 %) », sinon QuestieDB sans taux (« butin de Loup gris », « vendu par X (zone) »), marchands
  (`Recipes.mats`), fabriqué (`Gear.crafted`), récolte (`Recipes.gather`). Ligne courte seulement si l'objet te sert
  (quête, compo, familier) ou si un taux est mesuré ; sinon détails.
* **Accroche, taint** : `TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item)` (une fois par processeur,
  `ItemInfo.HookProcessor`), repli `HookScript("OnTooltipSetItem")` (GameTooltip, ItemRefTooltip) ; seulement
  `AddLine` + `Show` sur GameTooltip / ItemRefTooltip (jamais les infobulles de comparaison, protégées sur Forever :
  `C_TooltipComparison.CompareItem`) ; aucune méthode remplacée, aucun `RefreshData` / `SetX` ; infobulle interdite
  (`IsForbidden`) ignorée ; valeurs secrètes écartées (`issecretvalue`), rien de la fiche lu en combat.
* **Coût** : rien tant qu'aucune infobulle d'objet ne s'affiche (événements = un compteur) ; cache par objet (lien,
  pile, Maj, lié, porté) invalidé par `API.BagGen` (sacs + équipement), `T:QuestGen`, la version des données de
  Highlight, le groupe / niveau / familier / métiers / talents / fin de combat, ≤ 60 s ; index des compos (un masque
  par compo) et des nourritures construits à la 1re infobulle (≈ 160 kinstr une fois), recettes indexées seulement pour
  tes métiers. Objet pas encore en cache : RequestLoadItemDataByID, « TreeAI : chargement des infos de l'objet… » une
  seule fois, `ITEM_DATA_LOAD_RESULT` / `GET_ITEM_INFO_RECEIVED` écoutés seulement pendant l'attente (le jeu rafraîchit
  lui-même l'infobulle). `ItemInfo.Decorate(tt, data)` (rend les lignes), `ItemInfo.Build(it, ctx, shift)`,
  `ItemInfo.stats = {built, hits, loading}`. Tests : `tests/wow/iteminfo_scenario.lua` (3 saveurs + mode sûr),
  `test_iteminfo.py`, plafond session_sim `ItemInfo` (survol des sacs chez le marchand, sacs pleins).
## Pilote auto : module `Brain` (agent BRAIN, 4.0.2, `Modules/Brain.lua` + page `Modules/BrainPage.lua`)
« Fais une super intelligence qui fait plein de trucs logiques » : TreeAI décide SEUL ce qui est **sûr, logique et
réversible**, et dit pourquoi. **Mode sûr** (allumé par défaut), interrupteur général = le module « Pilote auto »
(page « Fonctions », bouton de sa page, `/tai pilote on|off`) ; chaque règle se coupe d'un clic (page « Pilote auto » →
« Règles », ou la page d'options). Rien de protégé (seulement `C_QuestLog.AddQuestWatch / RemoveQuestWatch`, nos réglages
de ZonePlan, la boussole), rien d'abandonné, rien dans le chat, aucune minuterie à lui (événements + un rappel `T:After`
regroupé, une seule tâche lourde par image), tout hors combat. Couper le pilote rend tout (quêtes reprises, zone et mode
d'avant, courses retirées du trajet).
* **Contexte** (`T.Brain.Context()` → `{ main, label, flags, map, raw, at }`, même table, lecture seule) : `main` =
  `dead` / `ghost` / `dungeon` / `taxi` / `afk` / `city` (capitale) / `travel` (on bouge ≥ 20 s vers un arrêt à > 600 yd,
  ou un transport en tête du trajet) / `grind` (≥ 3 combats en 5 min sans quête avancée ni arrêt à < 400 yd) / `quest` /
  `idle` ; `flags` = `pvp` (JcJ en zone contestée / hostile), `gather` (un membre du groupe dans une autre zone), `group`,
  `levelUp` (5 min), `bags` (≤ 1 place), `ammo` (< 400 ou < 30 min au rythme mesuré), `repair` (≤ 25 % ou pièce cassée),
  `rested` / `restedXP`, `resting`, `logFull` / `log` / `logMax`, `combat`, `mounted`. `T.Brain.Is(kind)`. Message
  `BRAIN_CONTEXT` (main) quand le contexte principal change. Carte « Prochaine étape » : UNE ligne d'avis (badge « i »,
  `CARD_NOTICE["Brain:card"]`) — urgent (réparer, sacs pleins, plus de munitions : clic = la boussole vise le PNJ connu
  le plus proche) > décision du pilote (90 s, clic = sa page) > en vol (« En vol : à l'arrivée, <étape> », genre
  `always`) > en ville (« Courses ici : répare · vends · achète des flèches ») > pierre de foyer.
* **Quêtes en pause** (`T.Brain.Parked(q)` → `{ k = raison, t = fait du jeu, at, n = titre, lv }` ou nil ;
  `ParkedList()` ; `Resume(q)` ; message `BRAIN_PARKED`) : retirées du suivi du jeu (par le « Suivi auto par zone »
  quand il est là — il suit le trajet —, sinon par le pilote, qui remet le suivi en les reprenant) et du trajet
  (`Z:Accept` → `false, "pause"` APRÈS les règles de ZonePlan, pour les raisons qu'elles ne jugent pas : grise, échouée,
  bloquée, doublon, donjon fait, détour ; « trop dure » et « loin » restent à ZonePlan — une quête qu'il prend n'est
  jamais en pause), rangées « En pause » avec la raison. Raisons = faits du module « Nettoyer mon journal »
  (`Abandon.cleanup` : échouée, chaîne bloquée, doublon, grise, donjon déjà fait, trop dure pour le groupe actuel, autre
  continent / ≥ 15 min) ; une quête grise du trajet est jugée par le pilote (faits d'Abandon, `planned = false`) ;
  « presque rien pour le détour » = XP réelle (`GetQuestLogRewardXP`) × 3600 / (détour du trajet + 30 s) < 25 % de l'XP/h
  mesurée par Stats (≥ 10 min), détour ≥ 2 min 30 (sinon la règle attend). **Jamais** : finie, chrono en cours, prise il
  y a < 5 min, avancée il y a < 15 min, visée à la main par la boussole, suivie à la main (`ZonePlan.char.watchManual`).
  **Reprise seule** quand la raison disparaît : le groupe grandit (« vous êtes 3 maintenant », ou « ton groupe l'a
  aussi » ; à 4 et plus pour une quête de donjon : « Groupe de 4 : <donjon> possible », clic = « Prêt pour le donjon ? »),
  un niveau, le trajet passe à < 150 yd, tu y avances, elle est finie, tu la suis à la main. Abandon : jamais ici — page
  « Journal » (2 clics).
* **Zone** : seulement si tu n'as pas choisi de zone (`ZonePlan.char.mode == "auto"`) ET que le trajet n'a plus RIEN (une
  remise compte) : la meilleure zone du même continent (`ZonePlan.counts.n` = quêtes à ton niveau, QuestieDB), ≥ 6 quêtes,
  score = n / (1 + trajet / 5 min) → `Z:PickZone(map)` ; annoncée une fois, annulable (retour « auto »).
* **Mode du trajet** (ZonePlan `Z.STYLES`, `Z:StyleIndex()`, `Z:SetStyle(i)`) : groupe ≥ 2 et mode « Le plus rapide »
  → « Groupe » ; groupe dissous → le mode d'avant ; un mode choisi par le joueur n'est jamais remplacé.
* **Courses** (`T.Brain.Needs()` → `{ key, text, urgent, kind, short }`, `ErrandStops()`, `GoService(kind)`) : réparer
  (≤ 25 %, urgent ≤ 10 % ou pièce cassée), vendre (sacs ≤ 3 places, urgent plein), munitions (chasseur : < 400, urgent
  < 100), maître de classe (niveau pris, prix lu chez lui par Guichets, sinon niveaux pairs). Un **arrêt** est inséré au
  plus court dans le trajet (contrat du Planner, comme Finder : `Brain.Inject(steps, cursor)` / `Brain.StopDone(s)`,
  étape `{ k = "vendor"|"train", label, map, x, y, brain = clé }`) seulement chez un PNJ CONNU (QuestieDB / déjà vu, module
  Détection) : urgent = le plus proche à ≤ 900 yd, en tête ; sinon seulement quand le trajet passe par une capitale (PNJ
  connu là-bas) — ailleurs, une note (page « Pilote auto », avis « Courses ici » en ville). Fait à l'arrivée (≤ 40 yd), retiré quand le besoin est réglé, jamais reproposé 10 min après.
* **Pierre de foyer** : prête, ≥ 2 quêtes finies se rendent à ≤ 400 yd de l'auberge (`C_QuestLog.GetNextWaypoint`), plus
  rien d'autre au trajet, ≥ 5 min gagnées (Route) → avis ; jamais si le trajet la prend déjà (`Route.firstKind == "hs"`).
* **Suivre le groupe** (coupé par défaut) : lit `Group.Intent()` (contrat GROUPJOIN ci-dessous) ; zone → `Z:PickZone`,
  donjon → `Dungeon.SetPlanned`, point de rencontre → `Compass:SetTarget`, quête → `Compass.SetQuestTarget` ; une fois
  par intention, journalisé avec qui l'a dit, annulable.
* **Le choix du joueur gagne pour la session** (≤ 2 h sans jeu) : « Reprendre », « Annuler », une zone / un mode changés
  à la main, une course annulée → `T.Brain.Overrides()` (`keep[q]`, `zone`, `style`, `errand[genre]`).
* **Page « Pilote auto : pourquoi ? »** (`T.BrainPage`, page cachée de la navigation comme « Journal de la boussole » :
  pastille > Outils > « Pilote auto : pourquoi ? », clic sur l'avis de la carte, section « En pause » de la page Quêtes,
  `/tai pilote`) : en-tête
  (contexte, quêtes en pause, décisions) ; « En pause » (bouton « Reprendre », lien « Abandonner : page Journal » pour
  les raisons sûres) ; « Courses » (clic = boussole) ; « Décisions » (heure, quoi, pourquoi, bouton « Annuler » :
  `T.Brain.Undo(id)`, `T.Brain.Decisions(n)`, ≤ 30 gardées par perso) ; « Ce que TreeAI voit » et « Règles » repliés ;
  bouton d'action « Couper / Allumer le pilote ». Page « Quêtes » : section « En pause (pilote auto) » repliée
  (`T.BrainPage.QuestsSection(Ly)`, crochet gardé de WindowPages.lua).
* **Crochets** (gardés, sans Brain rien ne change) : `Z.Accept` enveloppé une fois par le pilote (ZonePlan peut le faire
  lui-même et poser `Z.BRAIN_AWARE = true`) ; Planner `Evaluate` → `T.Brain.Inject`, `stepDone` → `T.Brain.StopDone` ;
  WindowPages → `T.BrainPage.QuestsSection`. Résumé `summary.brain = { v, ctx, parked = { {title, reason, why} },
  decisions = { {at, kind, text, undone} }, updated }`. `/tai pilote` ouvre la page.
* **Coût** : `session_sim` plafonne Brain à 40 kinstr/min (mesuré ≈ 7 en mode sûr) et à 1 au repos (`--idle`).
  Tests : `tests/wow/brain_spec.lua` (cœur pur), `tests/wow/brain_scenario.lua` (contextes, chaque règle : déclenche /
  ne déclenche pas / annuler / le joueur gagne ; suivi auto par zone allumé et coupé), `tests/wow/test_brain.py`.
**Contrats demandés aux agents en parallèle** (le pilote les détecte ; sans eux il fait sans) :
* **ZONEFIX** (ZonePlan / Route / Planner / Compass) : (1) à la fin de `Z:Accept(q, …)`, quand ses règles prennent la
  quête : `local p = T.Brain and T.Brain.Parked and T.Brain.Parked(q) if p and T.Brain.Core.ROUTE_KEYS[p.k] then return
  false, "pause" end` + `Z.BRAIN_AWARE = true`, et `WHY_TXT.pause = "en pause (pilote auto)"` dans `MaskedText` ; (2) `Z:SetStyle(i, by)` / `Z:PickZone(map, by)` avec `by = "brain"` ou nil (joueur)
  → `Z.char.styleBy` / `Z.char.zoneBy` = `"brain"` | `"player"` : un choix explicite du joueur reconnu même quand il
  vaut le défaut (aujourd'hui le pilote ne remplace que « Le plus rapide ») ; (3) garder stables `Z.counts.n[map]`,
  `Z:Allowed().maps`, `Z.char.watchManual[q]` (true / false = la main du joueur) et `Z:AutoWatchOn()` ; (4) facultatif :
  `Z:SetBias({ kill = 1.5 })` (repos + monstres sur le chemin) — le pilote le posera quand il existe ; (5) garder les deux
  crochets du Planner (`Brain.Inject` juste après `Finder.Inject`, `s.brain` dans `stepDone`).
* **GROUPJOIN** (Group) : `T.modules.Group.Intent()` → nil | `{ kind = "zone" | "dungeon" | "quest" | "meet" | "grind",
  map = uiMapID, dungeon = clé de Data/Dungeons, questID, point = { map, x, y (0–100) }, label = texte court en français
  (« on va à Mille pointes »), by = nom court de qui l'a dit, src = "chat" | "goal" | "leader" | "positions" | "log",
  conf = 0..1, at = GetTime() de l'inférence (stable tant que l'intention ne change pas) }` + `T:Fire("GROUP_INTENT",
  intent)` à chaque changement. Le pilote n'agit que si « Suivre le groupe » est coché et `conf ≥ 0,6`.
  (4.0.3, branché) `Group.Intent()` est dans `GroupJoin.lua` (`J.BrainIntent` : `z` → `zone`, `dg` → `dungeon` +
  entrée, `fm` / `rdv` → `meet` ; pause, HV, rendre → nil) ; intention relue ≤ 1 / 10 s par `Group:Tick` seulement quand
  « Suivre le groupe » est coché (sinon à l'ouverture de la page Groupe) ; `GROUP_INTENT` quand la clé change (nil au
  départ du groupe). ZONEFIX (2) aussi : `Z:SetStyle(i, by)` / `Z:PickZone(map, by)` posent `styleBy` / `zoneBy`.
* **REPLACE** (icônes de quête + « Suivi TreeAI ») : (1) le suivi TreeAI ne montre pas les quêtes en pause dans sa liste
  (ou les range repliées dans « En pause » avec `T.Brain.Parked(q).t` comme raison), rafraîchi sur `BRAIN_PARKED` ;
  (2) pas d'icône (ou une icône éteinte) pour une quête en pause ; (3) suivre `T.Brain.Context().main` : `taxi` →
  compact « à l'arrivée », `dungeon` → quêtes du donjon seulement, `dead` / `ghost` → caché ; (4) si le suivi TreeAI
  remplace celui du jeu, garder la même sémantique du suivi `C_QuestLog` (le pilote et le suivi auto par zone y écrivent).
  (4.0.3, branché) (1) + (2) : le suivi TreeAI range les quêtes en pause dans une ligne « En pause (n) : pilote auto »
  (infobulle : titres et raisons, clic = page « Pilote auto ») ; QuestPins ne dessine plus leurs zones d'objectifs ; les
  deux se rafraîchissent sur `BRAIN_PARKED` ; une quête prête à rendre n'est jamais cachée. (3) pas encore.

## Cerveau des quêtes : module `QuestBrain` (agent QUESTS, 4.1, `Modules/QuestBrain.lua`, **mode sûr**)

Quatre modes, UN actif (par personnage), le trajet (ZonePlan → Planner.steps → Route) suit le mode actif :
`turnin` « Rendre mes quêtes » (quêtes finies, toutes zones, ordre le plus rapide, vols / bateaux / foyer à n'importe quelle
étape), `pickup` « Quêtes à prendre » (les meilleures prises, XP / min), `zone` « Tout faire ici » (défaut : le trajet de
la zone de travail), `group` « Avec le groupe » (quêtes communes d'abord, d'un seul tenant).

* `T.QuestBrain.Modes()` → `{ { id, label, short, key, binding, tip, count, active } }` dans l'ordre turnin, pickup, zone,
  group ; `binding` = action de raccourci (`TREEAI_QB_TURNIN`, `TREEAI_QB_PICKUP`, `TREEAI_QB_ZONE`, `TREEAI_QB_GROUP`, à
  déclarer par Binds.lua / Bindings.xml), `key` = la touche liée (`GetBindingKey`) ou nil, `count` = quêtes concernées.
* `T.QuestBrain.SetMode(id)` → true / false (mode inconnu) ; `T.QuestBrain.Mode()` → id ; `T.QuestBrain.Cycle()` ;
  message `QUEST_MODE_CHANGED` (id, ancien id) ; `T.QuestBrain.Label(id)`.
* `T.QuestBrain.PickupList(n)` → `{ { questID, title, level, npc, map, x, y, dist (yd), eta (s jusqu'au donneur), xp,
  why, score (XP / min), minutes } }` classées (2 chiffres significatifs, ordre précédent puis questID : stable).
  Règles : jamais grise, jamais élite seul (à 2 seulement si « élites » coché), donjon seulement à 4 ET coché, jamais JcJ /
  raid / donjon fait, race / classe / prérequis / exclusivité / niveau requis / écart de niveau (réglage du trajet).
* `T.QuestBrain.TurnInList()` → `{ { questID, title, npc, map, x, y, eta, xp, zone } }` : dans l'ordre du trajet quand le
  mode turnin est actif (eta = arrivée), sinon les plus proches d'abord.
* « Suivant » : `T.QuestBrain.Skip()` (= `SkipStop()` = `NextStop()`) passe l'arrêt 1 (ses quêtes, pour la session, masquées
  tout de suite, trajet refait sans elles, « Hors trajet : 1 passée (Suivant) ») → true si quelque chose a changé ;
  `Unskip(q)` (nil = toutes), `Prev()` (reprend le dernier passé), `Skipped(q)`, `FirstStopQuests()` ; message `QUEST_SKIPPED`.
* `T.QuestBrain.GroupList()`, `T.QuestBrain.PickupNote()` (ce qu'il manque sans Questie, en clair, ou nil),
  `T.QuestBrain.HasQuestie()`.
* Sans Questie : quêtes du journal aux points du jeu (`C_QuestLog.GetNextWaypoint`, `C_QuestLog.GetQuestsOnMap`) ; PNJ appris
  quand tu leur parles (`GOSSIP_SHOW`, `QUEST_GREETING`, `QUEST_DETAIL`, `QUEST_PROGRESS`, `QUEST_COMPLETE` : position + nom
  du jeu, `TreeAIDB.modules.QuestBrain.learned`, 600 au plus) → `T.QuestBrain.LearnedFinisher(q)`, `LearnedTitle(q)`,
  `SyntheticRec(q)` (Planner, ZonePlan).
* Contrat ZonePlan : `Z.BrainMode()`, `Z:StyleName()` (sans argument) = le libellé du mode quand ce n'est pas « zone »,
  ligne `qb:mode` « Que faire : … ▾ » + `Z:ShowModeMenu()` dans la vue Trajet, `Z.StepsZoneLabel(steps)`,
  `Z.IsInstance(m)` / `Z.OutdoorZone(m)` (jamais un nom d'instance comme zone du trajet ou titre de carte).
* Rien dans le chat, aucune action protégée, aucun OnUpdate : calculs à la demande, mémorisés jusqu'au prochain évènement
  de quête / groupe / export ou 60 yd de déplacement. Slash : `/tai rendre`, `/tai prendre`, `/tai cerveau <mode>`.

## Sons, fenêtres, placement vivant (agent UI2)

**Sons** (`Core.lua`) : `TreeAI:Sound(kind[, {preview=true}])` → `true` ou `false, raison` (`off` sons coupés ·
`kind` genre coupé · `combat` en combat seul `danger` sonne · `gap` écart global `settings.sounds.gap` (2,5 s) ·
`cooldown` même genre trop tôt · `unknown`). Genres `TreeAI.SOUND_KINDS` (ordre `SOUND_ORDER`), sons du jeu
(clés `SOUNDKIT` essayées puis id de repli, tous distincts) : `danger` (alerte de raid), `alert` (important),
`quest` (quête terminée / à rendre), `rare` (rare / élite), `group` (groupe / amis), `ah` (bonne affaire HV),
`levelup` (carte de niveau, 4 s après la fanfare, hors combat), `reminder` (rappel discret, coupé par défaut).
Réglages `settings.sounds = { channel = "Master"|"SFX"|"Ambience"|"Dialog", gap, kinds = { [genre] = bool } }`,
`settings.sound` = interrupteur général. `TreeAI:PreviewSound(k)` (bouton « Écouter »), `SoundKindOn/SetSoundKind`,
`SoundLog()`, `ResetSoundGate()`. `Suggest` : prio 3 → `danger` si `IsDanger` sinon `alert` ; prio < 3 avec
`opts.sound = true` + `opts.soundKind` → ce genre (Boost rare = `rare`, Missions `SOUND_EVENTS` = `group`, Auction
nouvelle meilleure affaire = `ah`), sans `soundKind` → `reminder` si `settings.soundTips`. Tracker : fin de quête =
`T:Sound("quest")`. Page d'options **Sons** (genre on/off + Écouter, canal, écart) ; `/tai son [genre|options]`.

**Fenêtres** (`Modules/Theme.lua`) : `Th:Window(frame, opts)` — en-tête commun (icône, titre sans débordement,
réduire, fermer ; glisser = déplacer, double-clic = réduire), verre sombre arrondi (kind `window` = `glass`),
survol, fondu d'ouverture (un seul `OnUpdate` partagé, coupé au repos ; `Th:Animate/FadeIn/FadeOut`),
`resize = { minW, minH, maxW, maxH, width = "only", onResize }` (poignée, bornes, jamais > 90 % de l'écran,
`Th:ClampSize`), état `settings.windows[id] = { w, h, collapsed }` (`Th:SetCollapsed`), **Échap** : nom `TreeAI…`
ajouté à `UISpecialFrames` (cadre sans nom = relais `TreeAIEsc_<id>`) — jamais un cadre Blizzard. Appliqué à :
assistant + aide (en-tête complet), barre du mode édition (Échap = Fini), À faire, HV, macros (Échap, fondu,
verre ; leurs en-têtes gardés), panneau (verre, poignée de largeur 300–560, fondu ; **pas** d'Échap : c'est un HUD),
encadré d'alerte (fondu d'entrée).

**Placement** (`UI.lua`) : `TreeAI.ScreenProfile(W,H)` = `16:9` / `21:9` / `4:3` ; `ScreenInset` (21:9 : colonnes
du HUD au bord de la bande centrale 16:9) ; `ReservedRects` (centre + personnage) jamais pris par le placement auto
(repli sans réserve sur un très petit écran). Dispositions `TreeAI:ApplyDisposition("compacte"|"confort"|"coins")`
(`settings.layoutStyle`, `DISPOSITIONS`, boutons page Apparence, `/tai disposition …`). **Reflow** :
`TreeAI:Reflow(force)` toutes les 2 s hors combat (`settings.reflow`) — passage léger sur les cadres connus ; un
cadre TreeAI recouvert par un cadre nouveau / qui a changé (suivi Questie qui grandit, groupe…) glisse vers la
place libre la plus proche (`IsDisplaced(id)`), puis rentre chez lui quand c'est libre ; les chevauchements que le
joueur a choisis (cadre déposé là) restent acceptés tant que l'obstacle ne change pas. (4.0.1) Seulement les cadres
placés automatiquement, seulement quand `ScreenStable()` : un cadre posé par le joueur ne bouge jamais. `CommitMove` : déposé à plus
d'un quart sur un autre cadre → place libre la plus proche (≤ 300 px, `settings.snapFree`), zone 3×3 apprise
(`movers[id].zone`, `TreeAI.ZoneOf`).

## Slash

`/tai` ou `/tai aide` (une bulle montre la pastille de la minimap — module `Tour` ; `aide tout` = la page « Tout »,
`aide chat` = dans le chat) · `visite` (visite guidée) · `menu` · `bienvenue` · `mode calme|normal|tout`
· `profil perso|compte` · `edit` · `placer [doux]` · `afficher` / `cacher` / `basculer` · `mini`
· `onglet <id>` · `alerte` · `verrou` · `reset` (= place auto) · `options` · `modules` · `module <Nom> on|off`
· `sync` · `debug` · `statut` · `test` · + sous-commandes des modules (`RegisterSlash`).

## Visite guidée et astuces : module `Tour` (agent ONBOARD, `Modules/Tour.lua`, dernier du TOC)

Comprendre TreeAI en 60 secondes, à la souris. Module **sûr** (`Tour.lua` s'inscrit dans `SAFE_MODULES`), léger.
* **Visite guidée** (`T.Tour.Start(from)`) : 7 étapes au plus, une bulle (titre, 2 lignes, « n/N », **Suivant** /
  **Passer**) + une flèche et un halo pulsé sur le VRAI élément : pastille (`hub`), carte « Prochaine étape » ou
  panneau (`next`), zone de travail (`zone` : ligne `zone:pick`, sinon le menu de ZonePlan), boussole (`compass`),
  barres de raccourcis (`quick`), lignes « Fonctions bêta » (`beta`) et « Erreurs » (`bug`) du menu Hub (ouvert
  pour l'étape, refermé après). Étape sans cible visible (module coupé, cadre caché) = sautée, le compteur suit.
  Proposée **une fois** au premier moment calme (≥ 8 s après la connexion, assistant de démarrage fermé, 3 s sans
  combat / mort / vol / donjon / menu du jeu / options / mode édition) ; combat ou mort = bulle cachée, reprise au
  même endroit après. Rejouable : entrée « Visite guidée » du menu Hub (tag « nouveau » tant qu'elle n'est pas vue),
  bouton de la page d'accueil des options (attend la fermeture des options), page du module, `/tai visite`.
* **Cibles par contrat**, jamais par position : `T.Tour.Target(id, cadre | function() return cadre end)` passe
  avant les repères intégrés (ids : `hub`, `next`, `zone`, `compass`, `quick`, `beta`, `bug` ; 4.0 : la fenêtre TreeAI
  enregistre `next` = carte « Prochaine étape » et `zone` = ligne « Zone » de la page Trajet, la barre « Raccourcis »
  enregistre `quick` quand les barres y sont amarrées) ; sinon ids
  `RegisterMovable` (`card`, `panel`, `compass`, `quick`, `quickfight`, `quicktravel`), cadres `TreeAIMinimapButton`,
  `TreeAICard`, `TreeAICompassFrame`, lignes `H.layout` du Hub (`profil`, `erreurs`) et lignes à id dans le panneau,
  `TreeAIMainWindow`, `TreeAICard` (champ `entry` / `e` / `line` / `data` avec `.id`, `T.Tour.FindRow(id)`).
  Nos trois cadres (`TreeAITourCallout`, `TreeAITourGlow`, `TreeAITourArrow`) sont posés sur `UIParent` à la
  position LUE de la cible (`T.Tour.RectOf`), jamais ancrés sur elle (pas de protection implicite) ; le halo ne
  prend pas la souris (on clique le vrai élément à travers).
* **Astuces au bon moment** (`T.Tour.TIPS` : `ah` mode rafale à la 1re ouverture de l'HV, `questitem` bouton d'objet
  de quête, `dismiss` clic droit = masquer, `group` page Groupe / Fonctions bêta, `error` signaler un bug) : une fois
  chacune (vue = OK, ou restée ≥ 8 s), **≤ 1 toutes les 10 min** (horloge du serveur, survit au /reload), pas avant
  1 min de jeu ni 2 min après la visite, jamais en combat / mort / donjon / pendant la visite ; s'efface seule
  après 30 s. « Couper les astuces » = réglage `tips` du module (plus jamais) ; « Revoir les astuces » remet à zéro.
* **`/tai aide`** (et `/tai` seul) : bulle sur la pastille (« Visite guidée » / « OK ») ; en combat une ligne de chat ;
  `/tai aide tout` = l'ancienne page « Tout » (`T:ShowHelp`), `aide chat` = dans le chat. Fait en enveloppant
  `T.HandleSlash` (`Tour:OnInit`) ; module coupé = comportement d'origine.
* **Branchements** (gardés) : `Hub.lua` `H.Entries` → `T.Tour.HubEntry()` (dernière entrée de Réglages) ;
  `Options.lua` page d'accueil → `T.Tour.OptionsButton(P)` (moteur `P.button`).
* **État** `TreeAIDB.tour` (compte) = `{ done = TOUR_V, how = "done"|"skip"|"empty", at, step (1re visite coupée
  par un /reload), seen = { [astuce] = date }, last }`. `TOUR_V` + 1 = reproposée une fois à tout le monde.
* **Coût** : pas d'`AutoRefresh` ; un réveil ponctuel (`T.Tour.Kick`) seulement s'il y a de quoi attendre (assistant
  = attente de sa fermeture, pas de boucle ; astuces restantes = un regard / 30 s), ticker 1 s pendant qu'une bulle
  est affichée. Harnais : `TreeAIDB.tour` semé « vue » sauf `--profile sur` ; `tests/wow/tour_scenario.lua`.

## Groupe à plusieurs : module `Squad` (`Modules/Squad.lua`)
## Groupe : module `Group` « Groupe » (`Modules/Group.lua`, GROUPMERGE 4.0)

**Un seul module** pour le groupe de 2 à 4 amis, avec ou sans TreeAI : il remplace Squad (cerveau de groupe),
Missions (tableau des missions), Team (membres sans TreeAI) et l'ancien Group (S1/Q1). Un protocole, un onglet,
un propriétaire par information. Bêta (mode sûr : un clic dans « Fonctions bêta ») ; s'allume tout seul si l'un des
anciens modules l'était (migration une fois, `db.v4`, leurs réglages / données effacés). Rien dans le chat, rien
d'automatique (partager, buffer, dissiper = consignes ; seuls clics : boussole, « Prêt ? », objectif, rendez-vous).

* **Onglet « Groupe »** (`SetTabLines("group", lines, "Group")`), dans cet ordre : « Prêt ? » (si je l'ai demandé) ·
  **objectif** du groupe (« Objectif niveau 30 ensemble : 2/4 — Bravo 28 (45 %) », « Objectif : finir « <zone> » —
  7 quêtes restantes (toi 2, Bravo 3) · 2 à rendre » ; clic = choisir) · **prochain arrêt commun** (« Rendez
  ensemble « X » (3/3 prêts) » si la quête est dans MON journal et finie, sinon « Bravo, Charlie peuvent rendre … » ;
  « Ensemble : « X » — Bravo 5/8, toi 2/8 » ; clic = boussole) · **regroupement** (« Regroupez-vous : Bravo (Les
  Paluns) est dans une autre zone » / « Rejoins le groupe : ils sont dans … ») · **ce que je peux lancer** (« Lance
  Robustesse sur Bravo et Charlie », « Dissipe Bravo : poison (…) » — épinglé, concourt pour « Maintenant ») ·
  **écarts de niveau** (« Bravo est 2 niveaux derrière : partage-lui « A » et « B » », « Tu es 2 niveaux derrière :
  demande « X » à Charlie ») · **une ligne par quête** à partager / demander (« Partage « X » à Bravo et Echo »,
  « Demande « Y » à Charlie ») · **une carte par membre** : en-tête « Bravo · Chasseur 28 (45 %) · soins · loin ·
  PV 60 % · mana 30 % · sans familier · part dans 10 min · pas prêt » (PV / mana hors combat ; mort, fantôme, AFK,
  hors ligne, autre zone ; % du niveau pour les amis TreeAI ; icône de classe), son **étape en cours** (amis TreeAI :
  « Bravo : Tue « Ogre » (3/8) », clic = boussole si tu l'as aussi), ce qui lui manque et qui peut le donner
  (« Bravo : manque Marque du fauve (Charlie) · poison à retirer (Charlie) »), ses **signaux d'alerte** (« Delta :
  arme grise · rien en torse · durabilité 8 % · sacs pleins · 80 munitions ») · **course à l'XP** de la soirée
  (« XP de la soirée : Charlie +12 000 · toi +9 800 », amis TreeAI, XP exacte comptée par leur jeu). Boutons :
  « Objectif » (choix en lignes : niveau +1 / +2, les zones de ton journal, aucun), « Prêt ? » (chef : l'appel du
  jeu via `QuickTravel.ReadyCheck` ; sinon la vérification TreeAI : clic « oui » chez les amis, les autres vus par
  le jeu). « Avant de tirer, attends : Bravo 30 % mana et Delta loin » quand tu cibles un monstre hors combat
  (épinglée en tête, retirée en combat).
* **Annonces** (`Suggest`, onglet Groupe) : niveau d'un ami, objectif proposé / atteint, « Bravo demande : prêt ? —
  clic : oui », « Rejoins Bravo au point de rencontre (Moulin) · 230 m » (la boussole y va si `meetCompass`),
  « Attends Bravo (5/8) : « X » ouvre une suite, rendez ensemble » (QUEST_COMPLETE, seulement si une suite est
  connue), conseil de jet hors donjon (« Jet « Arc » : Besoin Bravo ; les autres Cupidité »).
* **Données** : tout ce que le jeu donne pour chaque membre est lu par le jeu (niveau, classe, rôle assigné, PV /
  mana hors combat, mort / fantôme / AFK / hors ligne, `UnitInRange`, `C_Map.GetBestMapForUnit`, familier,
  `C_QuestLog.IsUnitOnQuest` sur MES quêtes pour les membres sans TreeAI, auras relues toutes les 15 s hors combat —
  le cœur ne relaie pas UNIT_AURA du groupe —, équipement par `NotifyInspect` → INSPECT_READY : hors combat, à portée,
  un à la fois, ≤ 1 / 10 s, cache 10 min). Les amis TreeAI n'envoient que ce que le jeu ne donne pas : XP % du niveau,
  repos, XP de la soirée, durabilité, sacs, munitions, nourriture / eau, pierre de foyer, départ / prêt, métiers,
  zone, étape en cours, compte des quêtes de la zone objectif, et leur journal (quêtes + objectifs).
* **Protocole v4** (un seul type `GRP`, `TreeAI:Send(..., "PARTY")`, chaîne `"4;<genre>;…"`) :
  `D` différences seulement depuis le dernier envoi (`"4;D;<seq>;k=v;…;+<quête>;-<id>"`, valeurs exactes envoyées
  quand le changement compte : munitions ± 50, durabilité ± 5, XP ± 100…), `F` état complet (arrivée, demande, trou),
  `R` « renvoie ton état » (`*` ou un nom), `B` module coupé, `E` événements des clics (`goal`, `meet`, `ask`, `ans` ;
  4.0.1 : `meet` + genre + numéro = proposition de rendez-vous, `go`, `vote`, `ballot`, `plan` — voir GroupJoin).
  Quête = `"id,niv,drapeaux(C G),titre,m3/8.i0/5"`. Seau de jetons propre (3, +1 / 5 s) : **≤ 1 message / 5 s** en
  régime établi, rien si rien ne change, jamais par-dessus un envoi encore en file chez le cœur (il le remplacerait) ;
  numéros de suite → trou détecté = état complet redemandé (≤ 1 / 20 s par ami). Réception : membres du groupe
  seulement, seau par membre (8, +1 / 2 s), version ≠ 4 ignorée, tout revalidé (bornes, longueurs, `|`). En
  instance : pause du cœur (le jeu y coupe les messages). **Compat 3.9.x** (une version, à retirer en 4.1) :
  `SQS` / `SQQ` / `SQD` (Squad v2) et `QTM` (rendez-vous) décodés, jamais envoyés ; info-bulle de sa carte :
  « TreeAI 3.9 : demande-lui de mettre à jour ».
* **Façades** (anciens noms, mêmes tables dans `T.modules`, hors de « Plus → Modules ») : `TreeAI.Squad`
  (`IsActive`, `Members`, `MemberList` = moi + amis TreeAI `{name, st, quests, leaveMin}`, `State(nom)` champs
  d'avant + `h p d af pt py px pi` lus par le jeu, `Quests(nom)`, `Common`, `Leader`, `Size`, `IsMember`, `char.leaveAt`,
  `_members`, `Engine.Progress`, `SetLeave`, `SetReady`), `TreeAI.Missions` (`board`, `next`, `Engine.Next /
  StatusLine`, `SetGroupTarget`, `ReadyAsk`, `SOUND_EVENTS`), `TreeAI.Team` (`GearOf(guid)` → `{ilvl, links, …}`),
  `ns.Group.members` (Quests). `_active` / `_suspended` / `char` suivent le module « Groupe ».
* **Résumés** pour TreeAI Coach (formats d'avant gardés) : `summary.group` (v2), `summary.missions` (v1 : tableau,
  prochaine, session : durée, quêtes rendues, morts, niveaux, objectif, classement), `summary.team` (v1, sans noms).
* **Coût** : les gestionnaires posent un drapeau ; un seul `T:NewTicker(2)` fait le travail hors combat (journal
  ≤ 1 / 5 s, lignes seulement si quelque chose a changé : ≤ 1 / 3 s onglet visible, ≤ 1 / 30 s caché, sauf une ligne
  épinglée). `session_sim` (4 clients TreeAI) : `kinstrModuleMin.Group = 150` (mesuré ≈ 80 ; 3.9.9 : Squad + Missions
  + Team ≈ 430 sur la même simulation) et `groupMsgsPerMin = 12` (mesuré ≤ 3) hors minute d'arrivée / de /reload.
  4.0.1 : `kinstrModuleMin.GroupJoin = 60` (mesuré ≈ 25 : positions ≤ 1 / 15 s, indices de lieu refaits seulement
  si quelqu'un a bougé, trajets recalculés quand la page est ouverte) ; Group ≈ 92, GRP ≤ 6 par minute établie.
* **Rejoindre / ce que veut le groupe** (`Modules/GroupJoin.lua`, GROUPJOIN 4.0.1 ; partie du module « Groupe », pas
  de module à lui : `Grp.Join` = `T.GroupJoin`). Page Groupe, dans l'ordre : « Ce que veut le groupe » · « Rejoindre »
  · « Quêtes à partager » · « Ce que fait chacun » (cartes). **Rejoindre** : une ligne par membre loin (> 120 m) —
  « Rejoindre Bravo (Les Tarides) : au plus vite » (à droite « vol · 3 min » : `Route.FastestWorld` + `NavTime`,
  pierre de foyer seulement si ≥ 3 min gagnées) et « Rentable : +15 s · 1 rendu · +1 450 XP » (rendus / objectifs
  sur le chemin à pied si le détour coûte ≤ 20 % ou 30 s : `E.Detour`, insertion la moins chère) ; « Bravo arrive
  vers toi : ≈ 3 min — attends-le » / « Delta se rapproche » ; clic = boussole (`source = "join"`), carte « Prochaine
  étape » sur le vol (`QuickTravel.Destination` cède à `GroupJoin.Destination()`), « en vol », « Tu as rejoint
  Bravo ». Positions : jeu d'abord (`UnitPosition`, `C_Map.GetBestMapForUnit` / `GetPlayerMapPosition`), sinon
  `ps` des amis TreeAI, sinon le nom de zone (dit dans l'info-bulle). **Point de rencontre** (groupe éparpillé ≥ 600 m et
  sans destination claire : maître de vol des zones du groupe ou lieu de la mission commune ; `E.Meeting` = le plus
  loin a le moins de chemin, le lieu utile gagne s'il coûte ≤ 25 % de plus) : « Proposer au groupe »
  → les amis TreeAI ont « Bravo propose un rendez-vous … — clic : j'y vais » (`go` + temps restant), ligne
  « Rendez-vous X : Bravo (≈ 4 min) y va · 2 sans réponse ». **Intention du groupe** (une ligne, « Le groupe va
  vers : Kraal de Tranchebauge (3/4 à l'entrée) », « Le groupe finit : Bois-brisé (5 quêtes communes) », confiance
  + indices dans l'info-bulle) : indices pondérés avec déclin (`E.Intent`) — zones, direction des déplacements,
  regroupement à une entrée / un maître de vol / au rendez-vous, quêtes communes, étapes / zones choisies (`wz`) /
  trajets partagés des amis TreeAI, chef × 1,5, objectif, vote, et le **chat de groupe lu** (CHAT_MSG_PARTY /
  PARTY_LEADER, `E.ParseChat` : lieux, donjons, abréviations RFK / BFD / SM…, « on va », « go », « rdv »,
  « attendez », « je rends » ; jamais d'écriture, valeur secrète = ignorée, gardé en mémoire seulement, ≤ 30
  indices ; seul le texte de l'intention va dans `summary.group.intent`). Conflit : « Bravo et Charlie vont vers
  « Orneval », toi et Delta vers « Mille pointes » » + « Suivre le groupe » (`ZonePlan.SetMode("zone", carte)` /
  `Dungeon.SetPlanned`) et « Partager mon trajet » (`plan`) ; « Voter » (2 à 4 suites, un bulletin par ami, un
  vote à la fois, ≤ 1 / min). Entrées de donjon : table Classic approchée (≈ 150 m de marge), remplacée par l'entrée vue
  (`db.entrances`, carte + x / y seulement). Nouveaux champs d'état : `ps` (position ‰ "x.y" sur la carte `m`,
  ≤ 1 / 20 s et seulement si on a bougé, rien en instance), `sp` (point de l'étape "carte.x.y"), `wz` (zone choisie
  dans ZonePlan), `j` / `je` (qui je rejoins, temps restant). **« Maintenant »** : toutes les annonces du groupe
  passent par `Grp:Offer(clé, genre, texte, prio, opts)` / `Withdraw` / `Say` ; une seule ligne du groupe à la fois
  (rang : tirer > retour d'un clic > prêt ? > vote > rendez-vous > rejoindre > rendre > lancer > intention > trajet
  > nouvelles), le reste dans la page. Hors combat seulement (clics refusés en combat avec « Hors combat seulement »).
* Slash : `/tai groupe` (ouvre l'onglet), `/tai objectif [niveau 30 | zone | off]`, `/tai partir N`, `/tai pret
  [on|off]`. Options : cards, pull, quests, support, gear, goals, race, meetCompass, toasts.
* Tests : `tests/wow/group_spec.lua` (moteur + fil + 4 clients sur un faux canal PARTY + un membre sans TreeAI + un
  ami en 3.9 ; section GROUPJOIN : détour, rencontre, vote, chat, intention), `tests/wow/group_scenario.lua` (le vrai
  addon dans le harnais), `tests/wow/groupjoin_scenario.lua` (vol / à pied / foyer / attendre, rentable, rendez-vous,
  suivre, vote, chat + déplacements, vie privée, combat), `tests/wow/test_group.py`.

## Comms : limites de WoW Forever (Core)

~10 messages d'addon par préfixe en rafale puis 1/s, sinon le message est **perdu** (code de retour) ;
aucun message vers les autres en donjon/raid. Le cœur : seau de jetons (8, +1/s), code de retour vérifié
(`true`/0 ok, 3/8/`false` → nouvel essai 1-2-4-8 s, 11 → pause), deux files (état > journaux `SQQ/SQD/MSM`),
un message d'un type pas encore parti est remplacé par le plus récent, pause « Synchro du groupe en pause
(donjon) » (`TreeAI.commPaused`, messages `COMM_PAUSED`/`COMM_RESUMED`, reprise à `PLAYER_ENTERING_WORLD` /
`ZONE_CHANGED_NEW_AREA`), morceaux incomplets oubliés après 30 s. `TreeAI:CommStatus()`.
Le module « Groupe » envoie des **différences** seulement (`GRP "4;D;…"`, voir sa section) et un état complet sur
demande ou trou détecté. « Loin » = > 70 m du
coéquipier le plus proche (portée du crédit de kill partagé).

`Modules/Binds.lua` (agent BINDS, onglet « Infos », bloc `Binds`, `/tai touches [poser|oui|reset]`) : « sur quoi je
peux bind ». Lit les barres (`GetActionInfo` cases 1–72, barres de forme 73–120 pour guerrier/druide/voleur/prêtre,
145–180 en Mainline) et les raccourcis (`GetBindingKey` ACTIONBUTTON / MULTIACTIONBAR1..7BUTTON ; Bartender4,
ElvUI, Dominos : boutons `BT4Button*`, `ElvUI_Bar*Button*`, `DominosActionButton*` + `keyBoundTarget` /
`CLICK <bouton>:LeftButton`, **lecture seule**). Liste : cases vides avec touche, touches confortables libres
(1–6, Q E R F A, Z X C V W, Maj/Ctrl/Alt + celles-ci, souris 3–5 ; les touches de déplacement ZQSD sont exclues
d'office) ou peu utiles (`M.USELESS` : s'asseoir, rengainer, pages de barre…), conflits (même sort sur 2 touches,
sort clé sur une barre sans touche, sort clé absent, même touche sur 2 cases) et un plan par priorité : macros
TAI (liste lue via `Macros:ListNames()`/`MacroList()`/`Defs()`, touche conseillée `k`) puis sorts clés
(`M.KEY_SPELLS`, couverts par une macro TAI = ignorés). Actions **sur clic seulement**, hors combat, une par clic
(`TreeAI:UserAction`) : poser (`PickupMacro`/`PickupSpell` + `PlaceAction` dans une case vide) ou lier
(`SetBinding(touche, "MACRO nom")` + `SaveBindings(GetCurrentBindingSet())`, après une ligne « Confirmer »).
Si le jeu bloque (`ADDON_ACTION_FORBIDDEN` pendant l'appel ou nouvelle entrée `diag.blocked`), c'est retenu
(`db.bindBlocked` / `db.placeBlocked`, `/tai touches reset`) et les lignes donnent les instructions à la main.
`summary.binds = {ok, plan, conflicts, free, emptyKeyed, addons, bindBlocked, placeBlocked}`.

## Thème commun (`TreeAI.Theme`, Modules/Theme.lua, chargé juste après UI.lua)
Options (module « Thème », section Apparence) : thème Sombre / Clair / Blizzard, échelle du texte, on/off ; `/tai theme clair`.
- Couleurs : `Th:Color(token)` → r,g,b,a (`bg panel hud barBg border borderSoft text muted gold prio1 prio2 prio3 good bad warn hover`), `Th:Hex`, `Th:Wrap(token, txt)`, `Th:PrioColor(p)`, `Th:ClassColor(class)`.
- Polices : `Th:Font(fs, "S"|"M"|"L"[, token])`, `Th:Text(parent, size, token)` (suivent l'échelle).
- Habillage : `Th:Skin(frame, kind[, opts])`, kind = `window panel hud bar badge button row alert tooltip` ; opts `bg=` texture de fond existante, `noBg`, `border=false`. BackdropTemplate si présent, sinon textures. Refuse tout cadre nommé hors « TreeAI… », protégé ou interdit : jamais un cadre Blizzard. Toujours appeler gardé : `if T.Theme then T.Theme:Skin(f, "hud") end`.
- Widgets : `Th:Frame Button IconButton ProgressBar(:SetProgress(v,max,prio,txt)) Header Separator Checkbox Slider Tab(:SetSelected) Badge(:SetBadge)`, `Th:Tooltip(frame, title, lines)` (GameTooltip rempli, jamais habillé). Message `THEME_CHANGED` après un changement.
## Retirés en 5.1 (CLEAN)

`Watch` (Suivi TreeAI), `Horizon` (bande d'horizon), `Radar`, `QuickFight` (barre de combat), `Quick` (barre de quête),
`MinimapPins` (points en plus sur la minimap) et `Region` (quêtes par région) : coupés chez tout le monde depuis la 4.1,
supprimés en 5.1. Leurs réglages enregistrés sont effacés une fois (`TreeAI.GONE_MODULES`). La géométrie de la minimap
(projection yards / zoom, rotation, forme) reste dans `Modules/MinimapGeo.lua`, sous le même nom `T.MinimapPins` (lu par
QuestPins / PathDraw) ; la cible suivante de l'ancienne barre Quick est passée dans Macros.

## Sans autres addons (agent REPLACE, 4.0.1) : `QuestPins`, `Addons`, `QoL`, `Meter`

But : jouer avec TreeAI seul — Questie installé mais **silencieux** (sa base QuestieDB lue en jeu, rien de copié), plus
besoin de son suivi ni de ses icônes, ni de Leatrix Plus pour le confort, ni de Details pour les dégâts. Audit et plan :
`research/replace.md`.
* **`QuestPins` « Icônes de quêtes »** (mode sûr ; `T.QuestPins`) : « ! » des quêtes à prendre (couleur de niveau du jeu,
  un par donneur, filtres de l'export : faction / classe ; ici : niveau requis, grises sauf option, prérequis
  `pre` / `preAny`, exclusives, déjà faites, JcJ / raid), « ? » des quêtes prêtes, zones d'objectifs (amas de
  l'export, rayon `r` → %). Données : `T.Export:Data()` (construit hors combat depuis QuestieDB) + `Export.LogSnapshot()`
  (journal réel) + `Route:Stops(64)` / `Route.Groups` (n° d'arrêt = onglet / carte) ; sans QuestieDB :
  `C_QuestLog.GetNextWaypoint`. `QP.Build(exp, log, lvl, route, opts)` pur ; `QP.Pins()` refait la liste seulement si
  journal (`T:QuestGen`), export, niveau, trajet ou option ont changé, jamais en combat. Priorité : arrêts du trajet,
  à rendre, objectifs, à prendre (jaune / orange avant vert, XP) ; plafonds `MAX_MAP` 40 (20 « ! », 24 objectifs),
  `MAX_MINI` 10. Carte : NOTRE calque (enfant d'UIParent sur `ScrollContainer.Child`, comme PathDraw), fournisseur de
  données `AddDataProvider` : glisser = 1 point reposé (`QP.MapPan`), zoom / liste = icônes replacées, ≤ 10 passes / s,
  aucun OnUpdate carte posée. Minimap : HereBeDragons-Pins si chargé (Questie l'embarque : `AddMinimapIconMap`, rien par
  image), sinon support (`QP.holder`, enfant de Minimap) : minimap fixe = 1 point par passe (`SlideMini`), minimap qui
  tourne = icônes reposées au demi-pixel ; minuteur du cœur (`T:NewTicker`, ~1,5 px par passe : 0,2–0,75 s), arrêté à
  l'arrêt (`T:OnMotion`). **Questie dessine encore** (`Questie.db.profile.enabled` / `enableMapIcons` /
  `enableMiniMapIcons`) → rien (pas de doublon) ; option « Dessiner même quand Questie dessine ». Infobulle : quête,
  donneur, autres quêtes du même PNJ, XP, distance + durée (NavTime), pourquoi ; clic = `Compass.SetQuestTarget` /
  `SetTarget`. `/tai icones`. Coût mesuré (`replace_scenario.lua`) : immobile 0, en marchant ≈ 25, carte ouverte ≈ 7,
  carte glissée sans arrêt ≈ 35 kinstr/min ; session_sim : plafond 40, 1 à l'arrêt.
* **`Addons` « Mes addons »** (mode sûr, rien d'automatique) : page cachée de la fenêtre (hors navigation : la
  pastille garde sa hauteur), ouverte par Outils / pastille (« Mes addons », groupe réglages), la ligne d'avis et
  `/tai addons` (`A.Open()`) : par addon trouvé
  (Questie, QuestieDB, Shortest Path, Details, Leatrix, Auctionator, DungeonJournal, Loot Sparkles, Tweaks) ce que
  TreeAI fait à sa place, ce qui manque, le clic. `A.QuestieState()`, `A.SilenceQuestie(reload)` /
  `A.RestoreQuestie(reload)` : SES options seulement (`profile.enabled`, `trackerEnabled`, `minimap.hide`, état
  d'avant gardé), puis ReloadUI (dans le clic) ; options illisibles = les 3 clics exacts. `A.SetSPFTracker(on)` :
  `ShortestPathForeverDB.tracker` / `journeyTracker`. Une ligne « Maintenant », une fois (`noticeV`).
* **`QoL` « Confort »** (bêta, un clic sur la page) : vendre les gris + réparer (jamais si la barre de quête le fait déjà,
  `Q.QuickDoesIt`), butin instantané (`LootSlot` à `LOOT_READY` quand le jeu ramasse tout seul), butin automatique du
  jeu et recul de caméra (CVar, sur clic, hors combat). Pas repris : ce qui touche aux cadres de Blizzard.
* **`Meter` « Dégâts »** (bêta) : `C_DamageMeter` (compteur intégré de Forever, documenté 1.60.1) lu hors combat
  seulement (SecretWhenInCombat) : dernier combat / session, total, par seconde, part ; « Remise à zéro » = celle du jeu.
  `/tai degats`.

## Ma session + Guichets (agent NEXT10, `Modules/Session.lua`, `Modules/Errands.lua`, après Talents)

Deux petits modules, aucune nouvelle fenêtre : des lignes dans les onglets existants, toutes cliquables,
silencieux quand il n'y a rien à dire, soumis à `TreeAI:Quiet` (genre « advice ») et au mode simple.

* **Session** (onglet « Infos », bloc `Session`, `T.Session`) : ligne « Ma session : 1 h 05, +1 niveau, 9 quêtes,
  +2 po 40 pa » (après 5 min ; clic = détail : XP/h, niveaux atteints, meilleure quête, beau butin bleu+, or
  dépensé, où se déconnecter) · **fin de session** : `chars[k].modules.Session.cur` continue après un /reload
  ou une coupure < 30 min, sinon il devient `prev` et s'affiche une fois au retour (« Dernière session : … »,
  clic = ranger ; sujet `changes`) · **pourquoi je meurs** : `M.DeathTrend(deaths)`
  (écart de niveau ≥ 3, même tueur, ≥ 3 monstres, élites, même zone → texte + UN conseil + onglet utile), ligne
  « Maintenant » prio 2 au 3e décès en 30 min (sujet `death`, avec `Fight:death`) · **pause** après
  `pauseHours` h (option). Lit `Fight.lastRecap` (tueur, niveau) si présent. `/tai bilan`. Résumé IA :
  `summary.session = {minutes, xp, quests, levels, money, deaths, trend, tip, best}`.
* **Errands** (« Guichets », `T.Errands`) : une ligne pendant que la fenêtre du jeu est ouverte, effacée à sa
  fermeture. Boîte aux lettres (`M.MailDigest`/`MailLines` : lettres de l'HV via `Auction.MailKind`, argent,
  objets, contre-remboursement avec somme + expéditeur, lettres < 3 jours ; échéance gardée dans
  `char.mail.expireAt` → rappel au login) · maître de vol (`M.MatchBind` nœud de l'auberge de
  `GetBindLocation`, `M.HearthLeft` recharge de la pierre 6948 → « garde le prix du vol » / « vole pour X ») ·
  entraîneur (`M.TrainerPlan` : prix exact lu dans la fenêtre, sinon les sorts les moins chers d'abord, ou le
  niveau des prochains ; métiers ignorés) · appel « prêt » (`READY_CHECK` : PV / mana < 60 %, sacs pleins,
  munitions < 200, familier absent, équipement < 20 % ; rien si tout va bien) · limite de 5 instances / heure
  (`db.inst`, compte ; même instance non recomptée sauf réinitialisation `INSTANCE_RESET_SUCCESS`), bloc
  `Errands` de l'onglet « Groupe » dès 3 entrées. Résumé : `summary.errands`.
* Jamais appelés : `TakeTaxiNode`, `BuyTrainerService`, `TakeInbox*`, `AutoLootMailItem`, `OpenAllMail`,
  `ConfirmReadyCheck`, `ResetInstances` (vérifié par `tests/wow/next10_scenario.lua`).
(4.0) `Modules/Edge.lua` (« Coups d'avance ») est retiré : ses avantages vérifiables sont dans Edge2, Leveling, Session et
Errands ; le reste (repop / patrouilles appris sur trop peu de passages, chuchotements, prix devinés) a disparu.

`Modules/Edge2.lua` — **Avantages** (CLEAN 4.0) : 9 avantages légaux qui reposent sur un FAIT lisible dans le jeu,
chacun = UNE ligne qui commence par un verbe, coupable une par une. Farm : `contestCamp` (monstres de quête tapés par
d'autres), `dropLuck` (objet qui ne tombe pas : taux mesuré par Stats), `lootLeft` (corps pas fouillés, solo),
`gatherTrack` (récolteur sans suivi actif ; un clic l'active). Quêtes : `turninLevel` (quêtes finies = niveau suivant),
`questTimer`, `escortFail`, `breadcrumb` (quête de liaison devenue inutile). Ville : `worldBuff`. Sortie : `Suggest`
(`tab = "adv"`, onglet « Avantages »), jamais le chat. Coût : une vérification par `AutoRefresh`, rien en combat ;
`session_sim` : `kinstrModuleMin.Edge2 = 80`, `edgeNowPct` 25 %, `edgeDistinctMax` 12. Tests :
`tests/wow/edge2_scenario.lua`, `tests/wow/test_edge2.py`.
## Détection : module `Detect` (`Modules/Detect.lua`, après Compass)

Tout ce que l'addon voit dans le monde, au même endroit. Les API Forever 1.60.1 ont été vérifiées dans
Blizzard_APIDocumentationGenerated (branche `forever`). Les modules qui utilisent `TreeAI.Detect` gardent chaque appel.

| Appel | Rend |
|---|---|
| `:Rares([kind])` | rares / élites / trésors (C_VignetteInfo : position exacte ; repli barre de nom `approx`), triés par distance |
| `:QuestUnits()` / `:QuestUnit(unit)` | barres de nom liées à une quête (UnitIsRelatedToActiveQuest, UnitIsQuestBoss, nom ↔ objectif) ; `.left` = reste |
| `:Quests()` | relevé du journal (anti-rebond 0,3 s : QUEST_LOG_UPDATE, QUEST_WATCH_UPDATE, UNIT_QUEST_LOG_CHANGED, QUEST_ACCEPTED, QUEST_TURNED_IN, QUEST_REMOVED) |
| `:Kills(name)` | morts comptées par le message d'XP (pas de journal de combat sur Forever) |
| `:Services(kind)` / `:Nearest(kind)` / `:NearestText(kind)` | vendor, repair, flight, trainer, innkeeper, mailbox, bank, auction, stable, spirit, poi : PNJ appris en leur parlant (`db.learned`, compte), C_TaxiMap, C_AreaPoiInfo, habitants Questie |
| `:Group()` | membres : position (C_Map, UnitPosition, Squad), zone, hors ligne / ABS / mort / fantôme, portée |
| `:Combat()` | coups reçus en 5 s (UNIT_COMBAT), PV, menace, ennemis engagés ; une valeur secrète reste absente |
| `:Pins([withServices])` / `:Target(e)` | épingles génériques (Radar) / un clic = cible de la boussole |
| `:OnChange(cb)` | `cb(kind, data)` : `rares`, `questunits`, `quests`, `progress`, `kill`, `services`, `group`, `combat` (+ message `DETECT`) |

Utilisé par : Boost (ligne rare / trésor, clic = y aller), Highlight (repli UnitIsRelatedToActiveQuest), Compass
(`TargetNearest(kind)`), Radar (couche rares : vignettes), Pro / Stats / Edge2 (morts : message `DETECT` « kill »).
Commande : `/tai detect [rare|tresor|marchand|reparation|vol|auberge|courrier]`. Résumé IA : `summary.detect`.
**Vérité des données (agent DATA)** : un monstre / objet de quête ne vient que d'un objectif NON fini d'une quête du
journal, par questID. `Detect.Norm(s)`, `ObjectiveFor(nom)` (exact, objectifs « monstre »), `InLog(qid)`,
`QuestByTitle(t)` (infobulles seulement), `QuestieObjectives(qid)`, `MapObjectives(qid, objectifsDuJeu)` (nom exact
puis rang), `NpcObjective(npcID)` (tué / butin), `GameRelated(unit)` (vrai / faux / nil). Highlight, Tracker,
Compass, Quests s'en servent ; les lignes d'infobulle des membres du groupe sont étiquetées (« Pour <membre> »).
**Course au corps** : `Speed` (seul propriétaire) — ligne « Cadavre : 230 m au NE (≈ 40 s) », boussole via
`Compass:SetTarget{source = "corpse"}` (visible en fantôme, passe avant l'itinéraire), gardien des esprits ou corps,
endroit sûr, tout effacé au retour à la vie.
## Macros : installation robuste (agent MACROFIX, `Modules/Macros.lua`)
Écriture **sur clic / commande seulement**, hors combat (refusée en combat, rien mis en file d'attente).
- Index par nom : `GetMacroIndexByName` s'il existe, sinon parcours `GetMacroInfo` (compte 1..n, perso 121..) ;
  `CreateMacro` est vérifié par son retour (index) — jamais de 2e création (les noms en double sont permis).
- Limites : `Constants.MacroConsts` (Forever 120 compte / 30 perso), sinon globales (120 / 18). Plein → message
  « perso x/y, compte x/y » + fenêtre copier-coller.
- Sorts : nom depuis l'ID au chargement (`C_Spell.GetSpellInfo(id).name`), connu par l'ID **ou par le nom sans
  rang** (`C_SpellBook.FindSpellBookSlotForSpell(nom)`, `C_Spell.GetSpellInfo(nom).spellID`, scan du grimoire) :
  Forever a des rangs (`IsSpellBookItemLowRank`), l'ID du rang 1 peut ne pas être « connu ». Sort inconnu → la
  macro (ou la clause `?`) n'est jamais émise. `M.ValidBody(texte)` : ≤ 255 octets, aucun jeton, aucune clause vide.
- Bloqué par le jeu (`ADDON_ACTION_FORBIDDEN` sur `CreateMacro`/`EditMacro`, nouvelle entrée `diag.blocked`, ou
  appel accepté sans rien créer) → **une seule** tentative, `db.blocked = {t, func}`, mode copier-coller
  (`M:ShowCopy(list)` : nom + texte sélectionnables (Ctrl+C), icône, étapes, « Ouvrir les macros » =
  `ShowMacroFrame` sur clic, « Réessayer la création auto »).
- `M:Diagnose(show)` (bouton « Diagnostic macros », `/tai macros diag`) → `TreeAIDB.diag.macros = {t, ok, ko,
  mode="auto"|"copier-coller", blocked, slots={account,accountMax,char,charMax}, api={create,byName,spellbook},
  lastError, items={{n, ok, status=installed|user|later|full|blocked|ready|invalid, reason, detail, len}}}`.
- Succès : une ligne « N macro(s) TAI prête(s) — clique pour les glisser sur ta barre » ; « Installer cette
  macro » la met directement sur le curseur (`PickupMacro`). `/tai macros copier` ouvre la fenêtre à la main.

## Macros : état, réglages, préréglages, aspect intelligent (agent MACRO3, 4.0.1)
- Page **« Macros »** de la fenêtre TreeAI (`W.AddPage{ before = "modules" }`, après Outils, ouverte par `M:TogglePanel()` : minimap,
  Outils, Options, `/tai macros` ; l'ancien panneau = `M:ToggleList()` « Liste détaillée »). Une ligne par macro :
  pastille (vert active · orange à installer / à mettre à jour, clic · gris désactivée / plus tard / modifiée par toi /
  figée · rouge erreur + raison), « auto · mise à jour il y a 2 min », sur une barre ? touche ? (relevé des barres
  `M.Placement()` : nom de la case `GetActionText`, touche `ACTIONBUTTONn`… ou `MACRO nom`, barres d'addons via
  `Binds.ReadAddonBars`). `M:MacroState(def)` → `{code, badge, text, autoText, installed, current, expected, onBar,
  key, stamp…}` ; `M:States()` → liste + compteurs. Clic droit = activer / désactiver (jamais supprimée).
- **« Tout mettre à jour »** (`M:UpdateAll()`, bouton de la page et ligne « Macros à mettre à jour : clic ») : UN clic
  installe les macros activées qui manquent et réécrit celles qui ont changé ; jamais en combat ni hors d'un clic.
- **Réglages par macro** (`char.opts[nom]`, par personnage) : on, auto, mods (variantes Maj/Ctrl/Alt), mo (survol),
  pet (lignes /pet…), guard (« /cast !Aspect du faucon »), var (variante), tank (@party1-4), name, icon. Le corps est
  retouché avant le rendu par `M.Personalize` = `generator.personalize` (mêmes cas testés des deux côtés) ; une macro
  installée est réécrite dans le même clic (`M:ApplyNow`). `def.f` = réglages proposés (m o p g t).
- **Préréglages** `M:ApplyPreset("simple"|"complet"|"pro")` (2 clics : confirmation) : Simple = sans modificateurs ni
  survol (`def.sp`), Complet = jeu conseillé (`def.def`), Pro = Complet + macros « Pro » (`def.pr`, `def.pro`,
  astuce `def.t`). Les macros « clic » (TAI Pro Rare / Quete) font `/click TreeAIMacro_<clé>` : bouton sécurisé caché
  dont le texte est réécrit **hors combat** (`SetAttribute`, `useOnKeyDown = false`) — aucune écriture de macro.
- **Aspect** : TAI Aspects = `[combat][harm,nodead] !Faucon; !Guépard` (Maj/Ctrl/Alt en Complet). Les aspects
  prennent le temps de recharge global dans Forever : la garde d'aspect passe d'abord dans le pull (Marque, Pro Tir),
  après le tir dans TAI Tir. Raccourcis (QuickFight) : bouton d'aspect = même macro, il brille quand l'aspect est
  mauvais (+ un son, une fois, seulement en combat sous Guépard / Meute : le vrai danger, jamais « sans danger ») ; l'aspect est lu sur les auras hors combat, sur nos lancers en combat (auras secrètes).
  Chaque bouton des Raccourcis porte une pastille (vert à jour · orange après le combat · rouge plus d'objet).

## Stats mesurées : module `Stats` (agent STATS, `Modules/Stats.lua`, tôt dans le TOC, avant Macros)

UN centre de statistiques, `TreeAI.Stats`, que les autres modules lisent au lieu de leurs compteurs maison.
Uniquement des événements + une minuterie de 60 s (aucun OnUpdate, ≤ 60 kinstr/min vérifié par
`tests/wow/session_sim.lua`), jamais le journal de combat, valeurs « secrètes » de Forever ignorées (rien en combat).

* **Fiche** (`Stats:Sheet()`) : UnitStat 1-5, PA / PA distance, critique mêlée / distance / sorts, toucher, hâte
  (+ GetCombatRating si présent), armure, esquive / parade / blocage, résistances, vitesse, dégâts mêlée /
  distance, regen mana (GetManaRegen / GetPowerRegen), PV / mana max, niveau d'objet moyen. Relue sur
  PLAYER_EQUIPMENT_CHANGED / UNIT_STATS (1 s) et UNIT_AURA(player) (≤ 1 fois / 10 s), jamais en combat.
* **Mesures** (par perso, `chars[k].modules.Stats`, v = 1) :
  `kill[clé] = {s, n}` temps de kill, clé = écart de niveau (-5..5) .. type (`n`/`e` élite/`r` rare/`b` boss) ..
  `g` en groupe · `fight = {n, s, dm = {s, n}}` combats, secondes, % PV perdus (seulement si mesurable) ·
  `down = {s}` secondes à manger / boire · `death = {n, hi, el, t = {time()…≤20}, c = {[tueur] = n ≤15}}` ·
  `act = {q|g|d|t = {x = XP, s = secondes}}` quêtes / farm / donjon / trajet (classé chaque minute) ·
  `gold = {i, o}` · `played` secondes jouées (hors AFK) · `quest = {n, tot, a2c, c2t = {s, n}, open = {[id] = {a, c}}}` ·
  `move = {w|m = {d = mètres, s = secondes}}` (début → arrêt, 1 h glissante) · `trans = {["A>B"] = {s, n}}`
  bateaux / zeppelins · `rep` réparations payées (pc) · `use = {am, fd, dr}` munitions / nourriture / boisson ·
  `ah = {[objet] = {p, s, x, ts = {s, n}, o = {time()…}}}` (≤ 40) · `win = {{b, x, k, s, g, m0}…}` 12 tranches de 5 min ·
  `sheet` dernière fiche.
* **Taux de drop** (compte, `TreeAIDB.stats = {v = 1, drops = {[npc] = {n, nm, t, i = {[item] = {o, k, q}},
  p = {[nom] = corps en attente}}}, names = {[nom] = item}}`, ≤ 120 monstres × 10 objets) : une **occasion** = un
  corps que tu as fouillé (GUID source → id du monstre) d'un monstre qui peut lâcher l'objet ; un objet de quête
  ne compte que tant que l'objectif n'est pas fini ; jamais deux fois le même corps ; dépeçage / pêche ignorés ;
  corps fouillés par d'autres invisibles donc non comptés (en groupe, l'objet de quête est tiré pour chaque membre
  qui en a besoin). **Réussite** = l'objet est dans le butin de ce corps. Estimation = **Beta a posteriori** :
  a priori QuestieDB (`GetItemDroprate`, si sa base en donne un, poids 8 corps) sinon Beta(1, 3) ≈ 25 % ;
  moyenne + intervalle 80 % (quantiles 10 / 90 %). `Stats:Drop(item [, npc])` → moyenne, occasions, bas, haut ;
  `Stats:KillsNeeded(reste, item [, npc])` → attendu, pessimiste (« encore ~8, jusqu'à 12 si pas de chance ») ;
  `DropByName` / `BestSourceByName`. Exposé par la façade `T.Predict` (`DropRate`, `BestSource` → Tracker, Highlight, Abandon, Pro),
  Tracker (borne pessimiste, « taux mesuré sur N » dans l'infobulle), Planner.
* **API** : `Stats:Get("xph.q")` (chemin dans l'instantané), `:KillTime(écart [, type])` (solo, sinon groupe ×1,25,
  sinon écart voisin, sinon Export), `:XPPerHour("questing"|"grinding"|"dungeon"|nil)`, `:RecentXPPerHour()`,
  `:TravelSpeed(monté)`, `:Downtime()` → s / combat, part du temps, `:GoldPerHour()`, `:PerHour("ammo"|"food"|
  "water"|"repair")`, `:DeathsPerHour()`, `:QuestMinutes()`, `:SaleRate(objet)`, `:Respawn(npc)`,
  `:FlightTime(de, à)` (apprentissage Export), `:Snapshot()`. Tout renvoie nil sans mesure suffisante.
  Utilisé par Planner (temps de kill, repos, vitesse), `T.Predict` (XP/h, or/h), Chasseur (munitions / h),
  Abandon (temps de kill, vitesse, XP/h), Gear (or/h = valeur du temps), Auction (`NotePost` / `NoteAuction`,
  « tes ventes : 67 % vendues en ~2 h »).
* **Onglet « Stats »** (sous-menu « Plus », `/tai stats`) : 5-8 lignes (fiche, attaque, combat, repos / morts,
  XP/h + or/h, trajets, conso / h) + « Exporter pour TreeAI » → `summary.stats` (aussi au /reload, à la
  déconnexion et à `SUMMARY`), lu par `treeaicoach/wow/stats.py` → bloc « Mes stats mesurées » de l'onglet Session.

## Fichier d'erreurs : module `Bugs` (agent EXPORT, `Modules/Bugs.lua`, dernier du TOC)

- `/tai erreurs` (alias `/tai export`, `/tai bug`, bouton « Erreurs » de l'onglet Infos) → `TreeAIDB.diag.export =
  { at, version, toc, build, interface, errors, blocked, lines = {…} }` (≤ 200 lignes, ≤ 40 Ko) : erreurs Lua
  (`diag.errors`, récentes d'abord), actions bloquées regroupées par fonction avec nombre et contexte
  (`diag.blocked`), autotest (relancé en silence hors combat), `diag.macros`, `/tai perf`, modules actifs/éteints,
  réglages, autres addons chargés (nom + version), note libre (`/tai erreurs <texte>`). `/tai export` lance aussi
  l'export du planificateur (module Export).
- Chaîne `seterrorhandler` (le gestionnaire précédent est TOUJOURS rappelé) et ne garde que les erreurs dont le
  message ou la pile cite `AddOns/TreeAI/` (→ `T:RecordError`, module = fichier fautif).
- Côté appli : `treeaicoach/wow/errlog.py` écrit `TreeAI-erreurs.txt` (UTF-8, français) dans
  `%APPDATA%\TreeAICoach` (+ copie dans `exports\` s'il existe) dès que la sauvegarde contient `diag` et a changé.
## Chercher un truc qui change tout : module `Finder` (agent FINDER, `Modules/Finder.lua`, `Data/Pets.lua`)
* **Meilleur familier maintenant** (chasseur) : bêtes apprivoisables (niveau ≤ le tien, zone sans gardes ennemis),
  classées par un **gain** lisible : rangs de technique gagnés (Morsure / Griffe 8 pts par rang, Hurlement furieux 7
  (+3 à 3+ joueurs), Souffle de foudre 6…, comparés au grimoire de ton familier), famille (dégâts / armure / PV de
  l'époque, tables client, comparés à ton familier), vitesse d'attaque (`(2,0 − s) × 12`), niveau (−2 / niveau sous
  toi), rare (+3), trajet (−0,8 / min, modèle de Route : vols, bateaux, zeppelins, foyer). Top 3 dans l'onglet,
  « pourquoi » en infobulle (« Lion… : Griffe rang 4, attaque 1,5 s, 6 min de trajet », régime, rang probable à
  vérifier avec « Connaissance des bêtes »).
* `Data/Pets.lua` (généré par `tools/wow_build_pets.py`) : familles, techniques + niveaux des rangs, modificateurs =
  tables client Forever (fiables) ; bêtes = liste Classic écrite à la main (**approximative**), revérifiée en jeu
  dans QuestieDB (lecture seule : niveaux par id de PNJ — écart > 5 = écartée — et apparitions) ; les bêtes que tu
  cibles / survoles hors combat sont retenues (`db.seen`, 60 max) et passent avant la liste.
* **Recherche** : onglet « Chercher » (menu Plus, boutons « Chercher » / « Actualiser »), fenêtre
  `TreeAIFinderFrame` (mots + catégories), `/tai cherche [familier|rare|équipement|maître|vol|marchand|trésor|quête|donjon|texte]`.
  Sources : `Detect` (rares, trésors, marchand, maître), `Gear.plan` (meilleure amélioration par emplacement,
  pas les sacs), `Leveling:lvl_train`, maîtres d'armes (Classic ≈) pour une compétence d'arme non apprise,
  points de vol non appris (`Data/Travel` + `Route.KnownTaxi`), `Pro.ranked` (quêtes clé), `Data/Dungeons` (niveau
  du groupe, bouton « Prévoir »). Résultat = `{ key, cat, title, score, why = { {pts, texte} }, map, x, y (%), sec, yd }`.
* **Ajouter au trajet** (`Finder:AddToRoute(r)`, clic, hors combat) : arrêt `{ k, label, map, x, y, finder = key }`
  gardé dans `char.stops` (8 max) et inséré au plus court (`Planner.Engine.Insert`) par `Planner:Evaluate`
  (`Finder.Inject`) ; fait quand tu y arrives (≤ 40 m, `Finder.StopDone`) ; Route le montre et, s'il suit Shortest
  Path Forever, renvoie le trajet. Sans trajet : la boussole guide et l'arrêt rejoint le prochain trajet.
  **Y aller** = `Compass.SetTarget`.
* **Maintenant** : au plus une ligne `Finder:now`, seulement si un résultat ≥ 15 pts est à ≤ `nearMin` min
  (« Apprivoise X à 300 m : Morsure 3 »). Propriétaire unique d'« apprivoiser » (`Finder.OwnsPet()`).
* Perf : événements (cible, survol, niveau, zone) + relevé toutes les 30 s hors combat (calcul ≤ 1 / 25 s, à la
  demande sinon) ; QuestieDB lu une fois par PNJ et par session. Budget session_sim `Finder = 60` kinstr/min (mesuré
  ~11). Rien d'automatique, aucune fonction protégée. Test : `tests/wow/finder_scenario.lua`, `tests/wow/test_finder.py`.
## Raccourcis : module `QuickTravel` (agent QUICK3, `Modules/QuickTravel.lua`, en fin de TOC)
Tout part d'un clic du joueur, rien en combat, aucun cadre Blizzard modifié ni ancré (nos cadres sont sur UIParent,
placés à la position LUE des fenêtres du jeu). `TreeAI.QuickTravel` :
* **Maître de vol** (TAXIMAP_OPENED) : `Q.Destination()` (1re étape positionnée de `Route:Stops`, sinon cible de la
  boussole) → `Q.PlanFlight()` : nœuds `C_TaxiMap.GetAllTaxiNodes(GetTaxiMapID())` (repli NumTaxiNodes/TaxiNodeName),
  durées `Q.FlightTimes(src, Data.Travel.flights, nœuds connus)` (Dijkstra, vols enchaînés), `Q.BestFlight` (vol + 20 s
  d'embarquement + marche finale ; proposé si ≥ 45 s gagnées sur la marche). Le nœud brille (`Q.SlotSpot` : TaxiButton<n>
  ou épingle de FlightMapFrame, lecture seule) ; carte « Prendre ce vol » sous la fenêtre → `TakeTaxiNode(slot)`.
* **Aubergiste** (GOSSIP_SHOW avec l'option « foyer ») : moyenne de trajet vers les 6 prochaines quêtes depuis ici vs
  depuis l'auberge liée (`Route.LegSeconds`, `Route.BindPlace`) ; `Q.InnWorth(ici, lié)` = ici < 75 % et ≥ 60 s de
  gagné → carte « Lier ta pierre ici » → `C_GossipInfo.SelectOption(id)` ; la confirmation du jeu reste au joueur.
* **Courses groupées** (au repos, hors instance) : `Q.Needs()` (réparation ≤ 60 %, gris, munitions < 600, nourriture /
  boisson < 10, entraîneur en attente, courrier) → `Q.ErrandSteps(needs, find)` (une étape par PNJ, fusionnées,
  triées par distance) → une ligne « Courses avant de partir (n) : … » (clic = boussole vers l'étape 1). Lieux :
  marchands vus (`db.vendors[mapID][nom] = {x, y, a, f, w}` appris à MERCHANT_SHOW), puis Questie
  (`Q.QuestieVendor(tag)` : itemDB `vendors` des meilleurs objets de ton niveau, ta faction), puis `Detect.Nearest`.
* **Groupe** : bouton « Outils du groupe » de l'onglet Groupe → fenêtre « Tout le monde prêt ? » (`Q.MemberRows()` :
  façade Squad du module « Groupe » pour les amis TreeAI, API du jeu sinon ; `Q.Verdict` : PV ≥ 70, mana ≥ 70,
  munitions ≥ 200, familier) ; boutons Appel « prêt » (chef : `C_PartyInfo.DoReadyCheck`, sinon la vérification TreeAI
  `Missions:ReadyAsk` → Groupe), Rendez-vous ici, Inviter (amis du dernier groupe, `char.friends`), En raid / En groupe,
  Butin (cycle). Ouverte aussi à READY_CHECK et par « Prêt ? » de l'onglet Groupe. Slash : `/tai prets`, `/tai rdv`,
  `/tai courses`.
* **Point de rencontre** (GROUPMERGE 4.0) : `Q.SendMeet` / `Q.OnMeet` passent par le module « Groupe » (protocole
  unique `GRP`, événement `meet`, envoi ≤ 1 / 15 s, réception membres du groupe ≤ 1 / 10 s) → ligne « Rejoins X au
  point de rencontre » (`Group:meet`, 10 min) + boussole ; plus de message `QTM` (encore décodé pour les amis en 3.9).

## Métiers : montée + « Session métier » (agent PROFLEVEL, 4.0.2, `Modules/Prof.lua` + `Modules/ProfLevel.lua`)
`ProfLevel.lua` étend le module **Prof** (bêta, même interrupteur, même coût mesuré ; chargé après `Window.lua`) :
* **Planificateur** `Prof.Engine.Session(inp)` / `Prof.Engine.Split(res, have, reserve, vinfo, price)` = miroir ligne à
  ligne de `treeaicoach/wow/professions.py` (`session_plan` / `split`, parité testée par `tests/wow/test_proflevel.py`).
  Une fabrication à la fois (comptes entiers), la recette UTILISABLE au meilleur (coût ou compos rares) / point attendu ;
  chance Classic (orange 1, jaune / vert (gris − compétence) / (gris − jaune), « Heures sup. » + 4 %/rang) ; la recette en
  cours garde la main à 15 % près, une recette à apprendre doit faire 30 % mieux. Mode `full` (« Complet » : atteindre la
  cible, ce qui manque s'achète : marchand au prix exact, HV, sinon « à farmer ») / `exact` (« Pile-poil » : tes compos +
  celles d'un marchand). Au plafond du rang : le rang suivant (niveau requis, sinon arrêt `level`). Utilisable = connue
  (fenêtre du métier), apprise avec le métier (drapeau 1 des données), recette d'entraîneur dont l'exigence est atteinte
  (lue chez lui : `db.treq`, sinon = seuil jaune, drapeau 2 — les tables du client n'ont pas l'exigence), jamais un patron
  inconnu. `PL.Compute(line, mode, target)` (cache 6 plans) : stock = sacs + banque (relevé à BANKFRAME_OPENED,
  `char.pl.bank`) + courrier (`char.pl.mail`) − réserve de quête (objectifs « objet » du journal) ; prix `PL.PriceOf`
  (marchand, `Auction.Stats` conf ≥ 30 %, Auctionator ; jamais deviné) ; couleurs du jeu (`char.cal` : maxTrivialLevel,
  relativeDifficulty) ; intermédiaires du même métier (« Crée d'abord N× Peau moyenne traitée »).
* **Page « Métiers »** de la fenêtre (après « HV ») : métier, mode, cible (±5, palier), « Demander avant l'HV », étapes,
  achats (marchand / HV / à farmer + total, alerte si plus que ton or), tes compos (sacs · banque · courrier, « gardé pour
  « quête » »), en plus (compos inutiles à vendre, revente des objets créés, meilleur bandage, ami dépeceur). Bouton :
  « Lancer la session métier » / « Quitter la session ».
* **Session métier** (mode focus) : trajet de quêtes en pause (ZonePlan / Route, rendu à la sortie seulement si c'est la
  session qui l'a mis en pause), étapes dans l'ordre (courrier, banque, maître, marchand, HV du plus proche au plus loin,
  à farmer, puis les fabrications avec le maître au bon palier), boussole vers le PNJ de l'étape (marchands de compos et
  maîtres vus `db.vend` / `db.trn`, puis QuestieDB `vendors` / `professionTrainers`, banque / HV / boîte de Detect).
  Sous la fenêtre du jeu ouverte, une carte « Session métier » : UN bouton = UNE action par clic (`BuyMerchantItem`
  quantité exacte, arrondie au lot, une pile max ; `UseContainerItem` d'une case de banque, cases en surbrillance, jamais
  avec un marchand ouvert ; `C_TradeSkillUI.CraftRecipe(recette, N)` ou `DoTradeSkill` (Era) ; HV : recherche → achat →
  confirmation, prix plafonné à +20 %) ; rien en combat. Avance seule (étapes recalculées sur tes sacs / ta compétence).
  Reprise après /reload (< 12 h). `/tai metiers focus | pilepoil | complet | page`.
* **Contrat « focus »** (pour Brain, ZonePlan, la carte, la boussole) : `T.focus = nil | { kind = "prof", owner = "Prof",
  label = "Session métier", line, mode, since, gen, pausedRoute, card = function(n) → rows, titre, résumé }` ;
  `T:FocusMode()` → `kind | nil, T.focus` ; message `FOCUS_CHANGED` (`kind | nil`). Pendant un focus : ne pas reprendre
  le trajet, ne pas insérer de courses ni changer de zone. La carte « Prochaine étape » (`Window.CardModel`, mode `focus`)
  montre `T.focus.card` à la place du trajet (hors mort / donjon) ; clic sur son en-tête = la page « Métiers ».
* Tests : `tests/wow/proflevel_scenario.lua` (banque, marchand, fabrication un clic = une action, combat, pause / reprise,
  page), `tests/wow/test_proflevel.py` (moteur, données, parité Lua ↔ Python). Mock : `M.SetBank` / `M.SetBankItem`,
  `M.merchantItems`, `M.SetTradeSkill` (`M.trade`), `M.SetTrainer` (`M.trainer`).
