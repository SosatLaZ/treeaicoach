# TreeAI — addon WoW (World of Warcraft: Forever)

Addon in-game du compagnon TreeAI Coach. Il **conseille**, il n'automatise rien
(aucune touche envoyée, aucune action à la place du joueur). Les macros ne sont
créées/modifiées qu'**hors combat** (`InCombatLockdown()`).

Install: copier `wow_addon/TreeAI/` dans
`World of Warcraft/_forever_/Interface/AddOns/TreeAI/` (TreeAI Coach le fait
automatiquement). Commandes: `/treeai` ou `/tai` (`/tai aide`).

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
Core.lua  Locale_frFR.lua  UI.lua  Options.lua
Data/Macros.lua
Modules/Macros.lua  Modules/Hunter.lua  Modules/Classes.lua
Data/PetFood.lua  Data/Consumables.lua
Modules/Leveling.lua  Modules/Group.lua  Modules/Squad.lua  Modules/Quests.lua  Modules/Compass.lua  Modules/Tracker.lua  Modules/Predict.lua  Modules/Inventory.lua  Modules/Boost.lua  Modules/Auction.lua
Modules/Leveling.lua  Modules/Group.lua  Modules/Squad.lua  Modules/Missions.lua  Modules/Quests.lua  Modules/Inventory.lua  Modules/Boost.lua  Modules/Auction.lua
Modules/Leveling.lua  Modules/Group.lua  Modules/Squad.lua  Modules/Quests.lua  Modules/Inventory.lua  Modules/Boost.lua  Modules/Pro.lua  Modules/Economy.lua  Modules/Auction.lua
Modules/Leveling.lua  Modules/Group.lua  Modules/Squad.lua  Modules/Quests.lua  Modules/Inventory.lua  Modules/Boost.lua  Modules/Fight.lua  Modules/HunterPro.lua  Modules/Comfort.lua  Modules/Auction.lua
Modules/Leveling.lua  Modules/Group.lua  Modules/Squad.lua  Modules/Quests.lua  Modules/Export.lua  Modules/Planner.lua  Modules/Inventory.lua  Modules/Boost.lua  Modules/Auction.lua
```

`Modules/Quests.lua` (agent Q): aide aux quêtes PvE — à rendre par zone, « finis ici avant de partir »,
toasts de progression, quêtes grises (clic = ouvrir, l'abandon reste manuel), « à faire à 4 » (+ Group),
bouton sécurisé d'objet de quête (créé/modifié hors combat), ligne « Étape : … », `/tai quetes`,
XP restante, `summary.quests`. Questie détecté: lecture seule de `QuestieLoader._modules.QuestieDB`
(jamais `ImportModule`, aucun hook), pas d'infobulles en double.

`Modules/Boost.lua` (informatif / un clic, chaque fonction désactivable dans les options) : rares/élites
(barres de nom, son 1×/GUID, clic = crâne hors combat), meilleure récompense de quête surlignée (jamais
choisie), infobulles « Amélioration +X % » / « Pas mieux » (poids de stats par classe/spé documentés dans le
fichier) + prix vendeur vs HV (Auctionator, lecture seule), menace ≥ seuil en groupe (non-tank), moteur d'XP
(monstres/quêtes avant le niveau, XP/h), chasseur : aspect de voyage (id partagé `Hunter:aspect`) et conseiller
d'apprivoisement, rappels foyer/munitions/nourriture, récolte (nœud / dépeçage). `/tai boost`.
Résumé IA : `summary.boost = {raresSeen, lastRare, bestReward, petAdvice, avgKillXP, avgQuestXP,
killsToLevel, questsToLevel, xpPerHour, secondsToLevel, threatWarnings, tank}`.
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
disparues, heures, … } }` (≤ 14 jours, plafond `maxItems`). Or (`M.Gold()`): total, réserve (`keepGold`,
entraînement/réparations = niveau² × 1,5 pa si `autoReserve`, `mountGold`), budget = total − réserve
(∩ plafond/jour `budget`), immobilisé ; `F.allocate` = glouton profit ajusté / prix, plafond `capShare` %
par objet (reventes marchand hors plafond) → `M.plan` → file `M.queue` (achats, annulations, poses).
Chaque affaire porte `deposit` et `relist` (= dépôt × (1 − p) / p, dépôt perdu attendu en reposant jusqu'à
la vente). Journal par perso `chars[k].modules.Auction.ledger` (courrier HV FR/EN), `spent[date]`,
`deposits[date]`. Résumé `summary.auction = { deals, sell, thin, realized = {today, week, total, top},
budget, budgetLeft, spentToday, scanAge, neutral, flavor, gold = {total, reserve, invested, plan,
planGain}, lastScan }`, lu par `treeaicoach/wow/auction.py` (mêmes formules: commission 5 % / 15 %
neutre, dépôt = floor(prix marchand × qté × 5 % (25 %) × h / 2), profit = vente × (1 − commission) −
achat − dépôt × P(invendu)). Prix marchand: `GetItemInfo` (11e valeur, mis en cache), sinon Auctionator,
sinon historique. `/tai hv [vendre|bilan|journal|fenetre|cible|debug|neutre|faction|auto|reserve N|
monture N|budget N|api auto|moderne|classique]` — `debug` affiche l'API active, le limiteur, le compte à
rebours, taille/vitesse du dernier scan, Auctionator et les dernières erreurs.

`Modules/Compass.lua` · `Modules/Tracker.lua` · `Modules/Predict.lua` (agent QUEST-PRO) — en complément de
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
* **Route sûre** (`T.SafePath`, `Modules/SafePath.lua`, agent SAFEPATH) — `/tai sur [x y]` (bascule, `/tai sur off`) :
  **aucun auto-déplacement**, le joueur marche. A* sur une grille 100 × 100 de l'uiMap (hors combat, coroutine
  ≤ 2 ms/image) ; coût = yards × (1 ou 0,8 près des maîtres de vol / PNJ amicaux) + danger : apparitions des PNJ
  hostiles (QuestieDB lecture seule, `friendlyToFaction`, `maxLevel`, `rank`) pondérées élite 8 · rouge 6 ·
  orange 3 · jaune 1 · vert 0,3 (gris ignoré), rayon 20 yd + écart de niveau (+5 élite) ; barres de nom hostiles
  non grises → secteur ±50° / 40 yd devant le joueur pendant 20 s ; lieux de mort (1 h). Lissage ≤ +1 % de coût,
  points donnés un à un à la boussole (`Compass:SetTarget` sinon `SetWaypoint`), suivant à 15 yd, point perso
  rendu à l'arrivée. Message « Détour sûr : +40 m, évite 3 élites ». SPF : `API.GetRoute/Route/GetPath`
  (supposé, même carte) gardé s'il n'est pas plus exposé (+10 %). Une option par niveau de danger. Pur/testable :
  `SafePath.Plan{W,H,N,from,to,dangers,roads,live,deaths}`.
* **Suivi** (`T.Tracker`, refait par TRACK-PRO — comparatif AGF / Questie / RestedXP dans
  `research/agf_vs_treeai.md`) : onglet « Quêtes » (`SetTabLines`) **et** cadre compact optionnel à nous
  (`standalone` : déplaçable, verrou, réduction par quête au clic droit / tout réduire sur l'en-tête, police,
  largeur, échelle, opacité du fond ; sans onglets : cadre de secours `frame`).
  - **Tri par coût** (défaut) : XP/min = XP ÷ (trajet + travail × difficulté + 15 s). Trajet = SPF `Estimate`
    (8 plus proches, hors combat) sinon distance ÷ vitesse ; travail = objectifs restants × s/monstre mesurés
    (`Predict.Rates().killsPerHour`, sinon 25 s), objets : ⌈restants ÷ taux⌉ monstres (taux mesuré
    `Predict.BestSource/DropRate`, sinon 35 %) → « encore ~16 × Lion des collines (50 %) · ≈ 5 min », objets à
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
* **À faire** (`T.Todo`, `Modules/Todo.lua`, agent TODO) — la page de suivi refaite : une fenêtre (`/tai todo`, ou la
  dernière ligne de l'onglet « Quêtes ») déplaçable / redimensionnable (`TreeAI:RegisterMovable` si présent) et l'onglet
  « Quêtes » en version compacte (le Tracker lui passe la main dans `Push`, retour `"todo"` ; Todo désactivé = affichage
  historique). Sections : **Maintenant** (courses urgentes > carte épinglée > étape du plan > meilleure carte ; pourquoi,
  temps, boutons « Boussole » / « Y aller »), **Étapes du trajet** (`Tracker.PlanSteps` → `Planner:GetActiveSteps`),
  **Quêtes** par zone (cartes repliables : niveau en couleur, barres par objectif + « encore ~N × monstre », temps,
  distance, « À rendre », avatars de classe + progression des amis, récompense + % (Abandon/Boost), verdict Abandon au
  survol), **Courses** (`Predict.list` : réparation, munitions, sacs, nourriture, formation, foyer), **Groupe**
  (`Missions.board` / `next`, repli `Squad:Common()`), barre de **session** (`Boost:XPStats`, sinon Predict). Tout à la
  souris : puces de filtre (Tout / Zone / Prêtes / Groupe / Faciles / Épinglées), bouton de tri, « Itinéraire »,
  « Ordre auto », case épingler, masquer (réafficher en bas), glisser-déposer ou ▲▼ (ordre sauvé par perso
  `chars[k].modules.Todo.order`), recherche facultative. Rendu ≤ 2/s, mise en page refaite seulement si le contenu change.
  Résumé `summary.todo = {now, pins, hidden, ordered, filter, errands, shown}`. `/tai todo [montrer|cacher|ordre|tout|reset]`.
* **Prévisions** (`T.Predict`) : taux mesurés (munitions/min, nourriture/boisson par repos, monstres/h via
  `PARTY_KILL` ou message d'XP, butin par monstre et objet de quête — `TreeAIDB.modules.Predict.mobs` —,
  XP/h, or/h, durabilité/h, places de sac par monstre) → onglet « Prévisions » ; urgentes dans « Maintenant ».
  Coûts de formation : table **approximative** `Predict.TRAINING`. `/tai prevoir`.
* Résumés IA : `summary.compass = {target={kind,questID,title,label,zone,src,dist,distText,eta}, targets,
  mapID, custom, hidden}`, `summary.tracker = {route={{id,title,ready,dist,xp,zone,done,total,xpm,sec,mates}}, sort, filter, totalMin, plan, easyNearby,
  pickup, available, dropMobs}`, `summary.predict = {sessionMin, rates={ammoPerMin, killsPerHour, xpPerHour,
  goldPerHour, durPerHour, foodPerRest, waterPerRest, restsPerHour, slotsPerKill, kills, loots},
  forecasts={{id,text,prio}}}`. Message interne `QUESTS_UPDATED` (émis par Quests.Update).

`Modules/Pro.lua` (agent X1, quêtes, onglets « Quêtes » + « Infos », bloc `Pro`) : journal classé par XP/min
(XP réelle en API moderne, sinon estimée ≈), quêtes à abandonner (abandon manuel), camp saturé (barres de nom
`UnitIsTapDenied`), PNJ nommé en vue, escortes (se préparer), chaînes / relais (Questie, lecture seule), donjons
du niveau + quêtes à prendre avant, checklist au niveau (entraîneur, talents, améliorations marchand), repos
(auberges mémorisées), lieu de foyer conseillé, meilleure récompense connue (capturée chez le donneur), temps
restant par objectif, respawn observé, vrai taux de drop (`TreeAI.Predict` si présent, sinon mesure légère).
Calculs hors combat (valeurs secrètes de Forever). `TreeAI.ProUtil` : `Money`, `Dur`, `Upgrade(link)`, `Out`.
`summary.pro = {top, abandon, checklist}`. `/tai pro`.
`Modules/Economy.lua` (agent X1, onglets « HV » + « Infos », bloc `Economy`) : améliorations chez les marchands
(mémorisées par PNJ), quoi vendre/jeter d'abord (valeur/case, prix HV via `Auction.Stats`), or/h par activité,
bouton « Joindre compos » à la boîte aux lettres (1 clic = 1 pièce jointe hors combat, destinataire et Envoyer
restent au joueur ; `/tai compos <Nom>`), montée de métier la moins chère (fenêtre de métier), mémoire des nœuds
(clic = boussole), moment pour aller vendre, réparation prévue + coût estimé, durabilité avant donjon, valeur du
butin de la session. `summary.economy`. `/tai eco`.

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
`Modules/Fight.lua` (agent X2, PvE, informatif) : compteur de pull (barres de nom), élite en patrouille vers toi,
fuyards (émotes, mémorisés), récap de mort + conseil (`/tai mort`), PV/mana avant pull, sort à interrompre sur la
cible (avec ton interruption prête), minuteur des contrôles + rupture (petit cadre), familier qui perd l'aggro,
DPS perdu par l'humeur du familier, tirs entre deux tirs auto. Forever ne donne plus le journal de combat :
UNIT_COMBAT / UNIT_AURA / UNIT_SPELLCAST_* / barres de nom, valeurs testées avec `issecretvalue` (le journal de
combat n'est qu'un bonus en Era). Lignes dans « Infos » (source "Fight"), `summary.fight = {deaths, lastDeath, fleeKnown}`.
`Modules/HunterPro.lua` (chasseur) : familier conseillé par zone/niveau + rangs (`/tai familier`), munitions pour
finir le niveau, barre de piège (armé 60 s / recharge / déclenché, Maj+glisser), % de réussite de Feindre la mort,
temps passé par aspect. `summary.hunterPro = {fdTries, fdOk, aspects, ammoPerKill, ammoNeed}`.
`Modules/Comfort.lua` : changements depuis la dernière connexion (`/tai nouveau`), nouveaux sorts + case de barre
libre conseillée (jamais posés par l'addon), `/tai perf` (chronométrage de nos fonctions de module, mémoire
`GetAddOnMemoryUsage`, CPU `GetAddOnCPUUsage` si profilage). Ralentissement auto : FPS < 30 ou > 2 ms/image
(ou en ville, option) → `TreeAI.perfSlow = true`, `settings.refresh` ×2 (rétabli ensuite et à la déconnexion),
message `PERF_SLOW`. Les modules avec leur propre minuterie peuvent lire `TreeAI.perfSlow`.
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
TreeAI:After(sec, fn)                 -- C_Timer.After ou fallback OnUpdate
TreeAI:NewTicker(sec, fn)             -- retourne objet avec :Cancel()
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
API.FreeSlots()               -> free, total  (sacs généraux)
API.UseContainerItem(bag, slot)  -- HORS COMBAT, uniquement sur clic du joueur (marchand)
API.Aura(unit, spellIDOrName [, filter]) -> {name, icon, count, duration, expires, spellId, source} | nil   (filter "HELPFUL" défaut)
API.Auras(unit, filter)       -> liste d'auras
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

* Panneau compact déplaçable (sans icône minimap), **à onglets** : « Maintenant », « Quêtes »,
  « Groupe », « HV », « Infos ». Chaque onglet défile à la molette (barre fine à droite, « 3-10/14 »),
  lignes colorées par prio, cliquables (`onClick`), infobulles, clic droit = retirer un conseil.
  Barre d'outils : boussole (si `TreeAI.Compass`), macros, groupe, options, cadenas, mini, fermer.
  Mode **mini** (une seule ligne : le plus important, sinon l'étape de quête ; clic droit sur le titre
  ou `/tai mini`) — c'est le mode par défaut d'un nouveau personnage.
  Onglet, position et mini mémorisés **par personnage** (`TreeAIDB.chars[key].panel`) ;
  verrou / échelle / opacité / largeur / lignes au niveau du compte (`settings.panel`).
* **Encadré d'alerte** (`settings.alert`) : petit cadre dans un coin (haut-droite sous la minimap par
  défaut, 4 coins au choix), seulement les conseils **prio 3**, 1–2 lignes, s'efface après `duration` s,
  clic = ouvre le panneau sur l'onglet concerné. `/tai alerte` pour tester.
* Aucun conseil n'est écrit dans le chat (`settings.chat` = false, et `opts.chat` ne peut pas forcer) ;
  `Print` = réponses aux commandes et erreurs. Son : prio 3 seulement (`settings.soundTips` pour plus).
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
if TreeAI.SetTabLines then TreeAI:SetTabLines("group", lines, "Squad") else --[[ ancien chemin ]] end
```
* « Maintenant » = top 5 (`settings.panel.nowMax`) des suggestions + lignes `pin`, par prio, sans doublon
  (texte ET sens : voir « Anti-bruit » plus bas).
* Les suggestions sont aussi listées dans l'onglet de leur module (`TreeAI.MODULE_TAB`, ou `opts.tab`),
  sinon « Infos ». Rafraîchissement regroupé (≤ 2/s, `TreeAI:RequestRefresh()`).
* Aussi : `TreeAI:SelectTab(id)`, `GetActiveTab()`, `SetMini(bool)`, `ToggleMini()`, `ShowAlert(e)`.

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
Depuis 3.9.1 (`TreeAI.LAYOUT_VERSION` = 2, `layout.v`) : préfixes `TreeAI.LAYOUT_SCAN_PREFIX` (LibDBIcon10_,
Questie_, Details, Recount, CompactRaid/Party, PartyMemberFrame), suivis de quêtes réservés sur toute leur colonne
(ils grandissent), cadres de groupe réservés même cachés ; ordre : **radar** sous la minimap si libre sinon à sa
gauche → encadré sous la minimap → boussole en haut au centre → panneau sur un bord (jamais le centre, loin des
cadres de groupe, largeur ≥ 300). Replacement unique pour les joueurs existants (places manuelles gardées).
`TreeAI:ResetInterface()` (bouton « Réinitialiser l'interface » des options, page **Apparence** et sous-menu
« Plus », `/tai reinit`) remet tout par défaut. Panneau SIMPLE : 4 onglets (`now`, `quests`, `group`, « Plus »
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

## Slash

`/tai` ou `/tai aide` (page « Tout » ; `aide chat` = dans le chat) · `bienvenue` · `mode calme|normal|tout`
· `profil perso|compte` · `edit` · `placer [doux]` · `afficher` / `cacher` / `basculer` · `mini`
· `onglet <id>` · `alerte` · `verrou` · `reset` (= place auto) · `options` · `modules` · `module <Nom> on|off`
· `sync` · `debug` · `statut` · `test` · + sous-commandes des modules (`RegisterSlash`).

## Groupe à plusieurs : module `Squad` (`Modules/Squad.lua`)

Quand les amis du groupe ont tous TreeAI, chaque client partage un état compact et **tous calculent
les mêmes conseils** (moteur déterministe ; égalités départagées par le nom le plus petit = « chef »).
Conseils écrits seulement (section « Groupe » sous le panneau + `/tai groupe`), rien d'automatique ;
le bouton « Partager » (QuestLogPushQuest) n'agit que sur clic du joueur, jamais en combat. PvE.

* Comms (via `TreeAI:Send`, PARTY/RAID) : `SQS` état `{v=2, c classe, l niveau, x xp%, r repos%, z zone,
  m mapID, py/px position grossière (UnitPosition, arrondie à 5 m), pi instance, h PV%, p mana% (-1),
  d 0 vivant/1 mort/2 fantôme, pt familier 1 ok/2 absent, ph bonheur, b/bt sacs libres/total,
  du durabilité%, hs pierre de foyer (s restantes, -1 sans), hb bind, fo/wa nourriture/eau, am munitions,
  pr métiers, lm départ dans N min (`/tai partir N`), rd prêt (`/tai pret on|off`), af AFK, cb combat,
  qn/qm quêtes/max, xh XP/h}` ; `SQQ` `"2:" .. "id,niv,drapeaux(C fini, G groupe, Dn donjon),titre,m3/8.i0/5;…"` ;
  `SQR` `"2"` (renvoyez vos infos). Envoi au changement + toutes les 15 s (état) / 60 s (quêtes),
  ≤ 1 message/s ; en réception : membres actuels du groupe seulement, seau de jetons par membre,
  version ≠ 2 ignorée, tout revalidé (bornes, longueur, pas de `|`).
* Catégories (options, chacune désactivable) : `quests` (communes, « X n'a pas Q : Y, partage-la »,
  attendre avant de rendre, quêtes de groupe, ordre conseillé, à faire à part), `xp` (écarts de niveau,
  repos, XP/h), `regroup` (autre zone, loin, mort/fantôme), `pull` (cible hostile hors combat : mana/PV
  bas, familier, AFK/pas prêt), `loot` (objets de quête à laisser, conseil Besoin/Cupidité au
  START_LOOT_ROLL — texte seulement, jamais de jet automatique), `logistics` (marchand, réparation,
  munitions, nourriture, pierres de foyer, départs), `dungeons` (tranche de niveau + qui a les quêtes).
* Instantané pour TreeAI Coach : `TreeAIDB.chars[perso].summary.group = {v=2, updated, size, leader,
  members = {{name, class, level, xp, rested, zone, hp, mana, dead, bags, durability, ammo, leaveMin,
  ready, afk, professions, me}}, advices = {{cat, prio, text}}, common = {{id, title, holders, done, group}},
  xph}` (lu par `treeaicoach/wow/ai_context.py`, noms remplacés par « Classe niveau » avant l'IA).
* Compat : `Group.lua` se tait quand Squad est actif (plus d'envoi FSTAT/FQUEST ni de suggestions),
  mais `Group.members` reste rempli (même forme) pour les modules qui le lisent (Quests.lua).

API pour les autres modules (Quests, Boost…) :

```lua
local SQ = TreeAI.Squad            -- nil si le module n'est pas chargé
SQ:IsActive()                      -- module activé
SQ:Members()                       -- { "Nom", ... } triés (moi compris), seulement ceux qui ont TreeAI
SQ:State(name)                     -- état décodé (champs ci-dessus) ; nil = moi
SQ:Quests(name)                    -- [questID] = {id, level, title, done, group, dungeon, obj={{t,have,need}}}
SQ:Common()                        -- {{id, title, holders={}, done={}, notDone={}, missing={}, group, dungeon}} trié
SQ:Advices()                       -- conseils courants {{id, cat, prio, score, text, action}}
SQ:Leader()  SQ:Size()
SQ.Engine.Advise(members, ctx)     -- moteur pur (testable) ; SQ.Engine.LootHint(item, members)
-- message interne : TreeAI:On("SQUAD_UPDATE", owner, fn(owner, msg, advices))
```
Boost : si `TreeAI.Boost:XPPerHour()` existe, Squad l'utilise pour `xh`.

## Missions du groupe : module `Missions` (`Modules/Missions.lua`, après Squad)

Suivi des missions (quêtes) des amis, à partir des données de Squad (+ son propre protocole v1). Conseils
et clics du joueur seulement ; aucune sélection dans le journal de quêtes, aucun panneau Blizzard ouvert.

* **Tableau** : par quête, qui l'a, progression de chacun par objectif (barres), prêts à rendre, % du
  groupe, « Mission terminée » ; trié « finissable ensemble maintenant » (tous prêts > reste = monstres,
  crédit partagé > nb de porteurs > reste > distance). Ligne « Charlie : 8 Défense restants, Bravo : terminé ».
* **Répartition** : tuer ensemble (crédit partagé), qui loote d'abord (le plus en retard, réparti), chacun
  clique, membres éloignés (> 70 m) « de ton côté ». `/tai prends <quête|n°> [objectif]` ou clic sur la ligne
  = je m'en charge (bascule, partagé, expire après 30 min).
* **Route** : prochaine mission (rendre ensemble, sinon le plus de porteurs puis la plus proche,
  `C_QuestLog.GetDistanceSqToQuest`) ; `/tai mission cible` ou clic : le **chef** pose la cible du groupe
  (`TreeAI.Compass:SetTarget({x, y, mapID, label})` chez tous, si la boussole existe).
* **Annonces** (`Suggest`, `opts.tab = "group"`) : fini, tous prêts à rendre, rendu, abandon, mission
  terminée, arrivée/départ, membre **sans TreeAI** (clic = message privé pour l'installer, 1 fois),
  niveau, mort, AFK, hors ligne, loin ; « Attendez X » à `QUEST_COMPLETE` (urgent si suite connue, Questie).
* **41 Prêt ?** vérification maison (pas `DoReadyCheck`) : états prêt / pas prêt (raison) / ? / AFK /
  hors ligne / sans TreeAI, auto-évaluation PV/mana/familier ; `/tai mission pret [oui|non raison]`.
* **42 Butin** (`CHAT_MSG_LOOT`, formats `LOOT_ITEM*` du client) : objets par membre, verts/bleus/épiques.
* **43 Où est chacun** : distance + direction relative (`GetPlayerFacing`) ou cardinale (`/tai mission ou`).
* **44 Marquage** : crâne (élite/rare/+2 niv.), lune (lanceur), croix ; `SetRaidTarget` sur clic, hors combat,
  une marque par clic.
* **45 Classement XP** de la session · **46 Plan de voyage** (zone de la prochaine mission ou zone
  majoritaire, pierre de foyer prête/bind) · **47 Artisans** (métiers partagés `pr` → sacs, bandages,
  potions, balles, pierres… pour qui en a besoin).
* **Session** : objectif `/tai mission objectif niveau 30 | quetes 15 | <quête> | off` (partagé, ETA d'après
  l'XP/h, table XP Classic), `/tai mission recap`, `start`, `fin`. Gardée 3 h sans activité.
* Comms v1 : `MSM` "1:id,dist/10,mapID,x‰,y‰,obj1/obj2/obj3;…", `MSC` "1:id.j,…" (liste complète de mes
  prises), `MST` {v,q,m,x,y,l} (chef seulement), `MSG` {v,k,n,l}, `MSR` {v,k=ask|ans,id,r,w}. Membres du
  groupe seulement, seau de jetons, version ≠ 1 ignorée, tout revalidé. Envoi ≤ 1 message / 2 s.
* Onglet : `TreeAI:SetTabLines("group", lines, "missions")` (repli : `Suggest` de la prochaine mission).
* Résumé `summary.missions = {v=1, updated, size, members={{name,class,level}}, board={{id,title,holders,
  done,pct,ready,status,missing}}, next, assign, claims={{quest,by}}, travel, crafts, noAddon,
  session={start,duration,turned,missions,abandoned,deaths,titles,levels,events,goal={kind,text,pct,eta,done},
  leaderboard,loot}}` → `ai_context.py` (`missions_groupe`, noms remplacés par « Classe niveau »).

## Comms : limites de WoW Forever (Core)

~10 messages d'addon par préfixe en rafale puis 1/s, sinon le message est **perdu** (code de retour) ;
aucun message vers les autres en donjon/raid. Le cœur : seau de jetons (8, +1/s), code de retour vérifié
(`true`/0 ok, 3/8/`false` → nouvel essai 1-2-4-8 s, 11 → pause), deux files (état > journaux `SQQ/SQD/MSM`),
un message d'un type pas encore parti est remplacé par le plus récent, pause « Synchro du groupe en pause
(donjon) » (`TreeAI.commPaused`, messages `COMM_PAUSED`/`COMM_RESUMED`, reprise à `PLAYER_ENTERING_WORLD` /
`ZONE_CHANGED_NEW_AREA`), morceaux incomplets oubliés après 30 s. `TreeAI:CommStatus()`.
Squad envoie des **différences** de journal (`SQD "2:base:nouveau:+entrée;-id"`, empreintes des entrées
triées) et un dump complet toutes les 180 s ou sur demande (`SQR`, base inconnue). « Loin » = > 70 m du
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
## Radar : module `Radar` (`Modules/Radar.lua`, après Compass/Squad)

Petit radar **rond** à nous (jamais la minimap de Blizzard), `TreeAIRadarFrame` enfant d'`UIParent` : verre
sombre (masque circulaire si dispo, sinon texture ronde teintée), fin anneau doré, cercles de distance,
flèche du joueur (nord en haut, ou carte qui tourne : option), zoom 50–400 yd (molette ou boutons −/+).
Tout à la souris : boutons sur l'anneau (−, +, cadenas, menu ≡) et **clic droit = menu** (couches, rotation,
cercles, taille, verrou, oublier, cacher). Points : objectifs (amas d'apparitions via `Compass.QuestPoints`,
hors combat, cache monde 6 s), remises, cible boussole + étapes `Planner`, chemin sûr (`TreeAI.SafePath` :
`GetWaypoints()`/`.waypoints` si présent), amis (`party/raid` en direct puis état `Squad`), rares / élites vus
(relevés à notre position, `char.rares` / `char.dangers`), corps (`PLAYER_DEAD`), maîtres de vol
(`C_TaxiMap.GetTaxiNodesForMap`). Loin = collé au bord (flèche pour amis / cible), survol = infobulle,
clic = cible de la boussole. 20 i/s seulement visible, caché en instance. API pure : `R.Project(dn, dw,
facing, rotate, zoom, radius)`, `R.WorldToMap`, `R.Cluster`. `RegisterMovable(frame, "radar", "Radar",
{resizable, square, minSize, maxSize, onResize, onMove})` appelé si présent. `/tai radar [zoom N|tourne|statut|reset|oublier]`.
`Modules/Gear.lua` + `Data/Gear.lua` (agent GEAR) : planificateur d'équipement, onglet « Équipement » (tout au clic :
« ↻ Recalculer », horizon +2, clic sur un emplacement = toutes les sources et alternatives ; `/tai equipement` en
secours). Score par emplacement avec les poids du module Boost (exportés : `Boost.Weights`, `ScoreStats`,
`ScoreLink`, `EQUIP_SLOTS`, `ARMOR_LEVEL`, `WEAPON_TYPES` — aucune duplication) ; candidats = ~5 100 objets du
client (`tools/wow_build_gear.py`, build 1.60.1.70862 : pas de tables JournalEncounter*) + sacs + récompenses du
journal (`Pro.char.rewards`, `GetQuestLogItemLink`). Sources : sacs, quête (journal / Questie `questRewards`),
boss de donjon (Questie `npcDrops` dans une zone de donjon) ou monstre / coffre, marchand (Questie `vendors` +
prix d'achat client), artisanat (SkillLineAbility/SpellEffect, compétence ≈ seuil jaune − 15), HV (`Auction.Stats`,
jamais pour un objet lié quand ramassé) ; contrats optionnels lus si présents : `Dungeon.ItemSource(id)`,
`Prof.CraftInfo(id)` → `{text, min, cost}`. Valeur = gain ÷ √(effort en minutes, or converti via or/h).
Chasseur : DPS / vitesse de l'arme à distance (lente favorisée ±15 %), munitions assorties, mailles au niv 40.
Calcul par coroutine hors combat. Résumé IA : `summary.gear = {priority, slots={{slot,item,score,next={id,name,
gain,need,kind,where,cost,min}}}, shopping, total, hunter}` → `treeaicoach/wow/gear.py` (`gear_context`).
