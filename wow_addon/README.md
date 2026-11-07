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
Modules/Leveling.lua  Modules/Group.lua  Modules/Quests.lua  Modules/Inventory.lua
```

`Modules/Quests.lua` (agent Q): aide aux quêtes PvE — à rendre par zone, « finis ici avant de partir »,
toasts de progression, quêtes grises (clic = ouvrir, l'abandon reste manuel), « à faire à 4 » (+ Group),
bouton sécurisé d'objet de quête (créé/modifié hors combat), ligne « Étape : … », `/tai quetes`,
XP restante, `summary.quests`. Questie détecté: lecture seule de `QuestieLoader._modules.QuestieDB`
(jamais `ImportModule`, aucun hook), pas d'infobulles en double.

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
