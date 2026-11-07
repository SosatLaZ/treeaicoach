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
  son + message écran). Défaut 1. Constantes `TreeAI.PRIO_INFO/TIP/URGENT`.
* `opts` (facultatif):
  * `ttl` secondes avant disparition auto (défaut 60; `0` = permanent jusqu'à `ClearSuggestion`).
  * `cooldown` secondes avant de ré-annoncer (son/chat) le même id+texte (défaut 30).
  * `icon` texture (fileID ou chemin), `module` nom, `onClick` fonction (clic gauche sur la ligne).
  * `sound` (bool) forcer/empêcher le son; `chat` (bool) l'écrire aussi dans le chat.
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
  version = "0.1.0",
  settings = {
    enabled = true, sound = true, chat = false, comms = true, debug = false,
    minPrio = 1,                -- prio minimale affichée
    panel = { shown = true, locked = false, scale = 1, alpha = 0.9,
              point = "CENTER", x = 300, y = 120, maxLines = 6 },
  },
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
    },
  },
  group = { peers = { ["Ami-Royaume"] = { version=, seen=, class=, level=, zone= } } },
}
```

## UI / Options

* Panneau compact déplaçable (sans icône minimap): titre, lignes de
  suggestions colorées par prio, clic droit = retirer la ligne, cadenas.
  `TreeAI:ShowPanel() / HidePanel() / TogglePanel() / RefreshPanel()`.
* Options: `Settings` (moderne) si présent, sinon `InterfaceOptions_AddCategory`.
  `/tai options`. Chaque module avec `options` obtient sa section auto
  (+ case « Activé »). `TreeAI:OpenOptions()`.

## Slash

`/tai` (aide) · `afficher` / `cacher` / `basculer` · `verrou` · `reset`
· `options` · `modules` · `module <Nom> on|off` · `sync` (HELLO au groupe)
· `debug` · `statut` · `test` · + sous-commandes des modules (`RegisterSlash`).

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
