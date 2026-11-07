# TreeAI route planner: data contracts (v1)

There are two channels. Both carry `v = 1`. A reader ignores fields it does not know, and treats a missing field as
"unknown", never as an error.

* **Addon → app**: `TreeAIDB.export` and `TreeAIDB.learn` (account level), plus `TreeAIDB.chars[key].learn` (per
  character). The game writes these SavedVariables on `/reload`, logout and quit. The app only reads them.
* **App → addon**: the data addon `Interface/AddOns/TreeAI_Plan/` (`TreeAI_Plan.toc` + `Plan.lua`, which defines
  the global `TreeAIPlan`). The app writes it atomically. WoW loads it at login and on `/reload`. The game
  never writes it, and the app never writes the game's SavedVariables.

Conventions:

* A position is `map` (a uiMapID) plus `x`, `y` in map **percent** (0–100, 1 decimal).
* Times are in seconds, except the plan's `eta` and `minutes`, which are in minutes.
* `key` is a character key `"Name-Realm"`, the same as `TreeAI:CharKey()`.
* Numeric-keyed tables (`[id] = …`) may also use string keys (`["123"] = …`). The Python parser turns a table
  keyed 1..n into a list, so the app accepts both forms.
* Records in the export are **arrays of tables**, each carrying its own `id`. They are not keyed by id.
* The export is capped at about 300 KB. When it is too big, drop in this order: `avail` quests of neighbour
  zones, then objective `alt` clusters, then squad quests that are not in my log.

## 1. `TreeAIDB.export`: what the planner reads

It is rewritten on every PLAYER_LOGOUT / reload, and on `/tai plan export`.

```lua
TreeAIDB.export = {
  v = 1, made = 1762500000,            -- time()
  char = "Name-Realm", src = "LibQuestieDB" | "QuestieLoader" | "log",  -- where quest data came from
  player = {
    class = "HUNTER", level = 26, xp = 1234, xpMax = 38900, rested = 0,  -- rested = GetXPExhaustion() or 0
    faction = "Alliance" | "Horde",
    map = 1424, x = 45.1, y = 62.0,    -- C_Map.GetBestMapForUnit + GetPlayerMapPosition
    speed = 7.0,                       -- run speed in yd/s (2nd return of GetUnitSpeed)
    mount = 0,                         -- mounted speed in yd/s, 0 = no mount
    dps = 55,                          -- optional: own DPS estimate (Boost/Hunter); 0/nil = unknown
    logMax = 20, logCount = 12,        -- C_QuestLog.GetMaxNumQuestsCanAccept() or 20
    hearth = { cd = 0, map = 1424, x = 51.2, y = 58.7, name = "Moulin-de-Tarren" },  -- cd = seconds left
    taxi = { 2, 4, 12 },               -- known TaxiNodes IDs (GetTaxiNodesForMap(...).isUndiscovered == false)
    session = 120,                     -- optional: minutes the player plans to play (Squad `lm`); nil = unknown
    group = 1,                         -- party size (1 = solo)
    raid = false,                      -- true = in a raid group (quests do not progress: the planner warns)
  },
  maps = {                             -- one entry per uiMapID used below (C_Map.GetWorldPosFromMapPos)
    { id = 1424, c = 0,                -- c = continent / instance map ID returned by GetWorldPosFromMapPos
      x0 = 1066.7, y0 = 400.0,         -- world pos (vector.x, vector.y) at map (0,0)
      x1 = -1800.0, y1 = -3900.0,      -- world pos at map (1,1)
      name = "Contreforts de Hautebrande", lo = 20, hi = 30 },  -- lo/hi = level band of the zone, if known
  },
  quests = {                           -- my log (st="log"), plus quests available (st="avail") in the current
    {                                  -- zone and 2 neighbour zones; plus the squad's log quests
      id = 1234, t = "Titre FR", lv = 25, rl = 21,  -- questLevel, requiredLevel
      xp = 2100,                       -- base XP (QuestXP support / GetQuestLogRewardXP); 0 = unknown
      st = "log" | "avail",
      done = 0 | 1,                    -- 1 = objectives complete (ready to turn in)
      zone = 1424,                     -- uiMapID the quest belongs to (zoneOrSort → uiMap)
      pre = { 1200 },                  -- preQuestGroup: all must be turned in (list only the ones NOT done yet)
      preAny = { 1201, 1202 },         -- preQuestSingle: one of them (only when none is done yet)
      excl = { 1300 },                 -- exclusiveTo
      flags = 8,                       -- questFlags (0x8 sharable, 0x2 party accept / escort)
      elite = 0 | 1, grp = 0,          -- elite tag, suggestedGroup (0/1 = solo)
      dg = 0 | 1, timed = 0 | 1, esc = 0 | 1,  -- dungeon, timed, escort / event
      s = { map = 1424, x = 50.1, y = 57.3, k = "npc" | "obj" | "item", id = 2263 },  -- starter (nil when in my log)
      f = { map = 1424, x = 50.1, y = 57.3, k = "npc" | "obj", id = 2263 },           -- finisher
      o = {                            -- objectives in quest order (position = objective index used in plan steps)
        { k = "kill" | "item" | "obj" | "explore" | "talk" | "spell" | "other",
          id = 2350,                   -- creature / item / object id (0 = unknown)
          n = 10, have = 3,            -- needed / already have (GetQuestObjectives when in the log)
          npc = { 2350, 2351 },        -- for "item": the creatures that drop it (first = most common)
          p = 0.35,                    -- drop chance 0–1 for "item" (Questie DropDB prior); nil = unknown
          plf = 0 | 1,                 -- the item has the party-loot flag (a copy for every member)
          map = 1424, x = 40.0, y = 50.0, r = 60,  -- centroid of the main spawn cluster, radius in yd
          sp = 8,                      -- spawn points in the cluster (density); 0 = unknown
          ml = 24, mx = 26, rank = 0,  -- mob min/max level; rank 0 normal, 1 elite, 2 rare-elite, 3 boss, 4 rare
          alt = { { map = 1424, x = 30.0, y = 55.0, sp = 5 } },  -- other clusters (optional, at most 2)
        },
      },
      who = { "Ami-Royaume" },         -- squad members who have this quest in their log (not me)
    },
  },
  squad = {                            -- group members who run TreeAI (Squad module), not me
    { key = "Ami-Royaume", class = "PRIEST", level = 25, xp = 0, map = 1424, x = 48.0, y = 60.0,
      speed = 7.0, dps = 0, q = { 1234, 1250 }, done = { 1199 } },  -- q = quests in log; done = recently turned in
  },
}
```

## 2. Learned stats (the addon writes them, the app reads them)

Every entry is a running sum `{ s = sum, n = count }`, so the mean is s/n. Drops are `{ k = drops, n = kills }`.

```lua
TreeAIDB.learn = {                     -- account level, pooled over characters and the squad
  v = 1,
  drops   = { [itemID] = { k = 3, n = 9 } },            -- quest-item drops / kills of the creatures that drop it
  respawn = { [npcID]  = { s = 1500, n = 5 } },         -- observed respawn time, seconds
  flights = { ["2-4"]  = { s = 154, n = 2 } },          -- "fromNode-toNode" (taxi node IDs) → seconds
  detour  = { [uiMapID] = { s = 6.6, n = 5 } },         -- walked time / straight-line time
  comp    = { [uiMapID] = { s = 7.5, n = 5 } },         -- competition factor κ (1 = quiet)
}
TreeAIDB.chars[key].learn = {          -- per character
  v = 1,
  ttk      = { ["0"] = { s = 150, n = 10 }, ["-2"] = {...}, ["3"] = {...} },  -- kill time in s, keyed by mobLevel − myLevel (string keys)
  search   = { s = 100, n = 10 },      -- seconds between kills while questing
  downtime = { s = 1.5, n = 10 },      -- share of time spent eating / drinking (0–1)
  eff      = { s = 12, n = 10 },       -- observed / predicted quest time (E_player)
  quests   = { [questID] = { s = 600, n = 1 } },        -- seconds from accept to turn-in
  xph      = { s = 250000, n = 10 },   -- XP/h per session
}
```

## 3. `TreeAIPlan`: the plan (app → addon) in `Interface/AddOns/TreeAI_Plan/Plan.lua`

`TreeAI_Plan.toc`:
```
## Interface: 11507, 16001
## Title: TreeAI Plan
## Notes: Trajet calculé par TreeAI Coach (fichier généré, ne pas modifier)
## Author: TreeAI Coach
## Version: <made>
Plan.lua
```

`Plan.lua` is pure data: a single global assignment, with no function calls.
```lua
TreeAIPlan = {
  v = 1, made = 1762500000, char = "Name-Realm", zone = 1424,
  routes = {
    { id = "xph" | "clear" | "squad" | "safe",
      title = "XP/h max", xph = 28500, minutes = 74,
      verdict = "ok" | "galere" | "long" | "impossible" | "zone",
      reasons = { "Taux de drop 20 % sur Crocs (galère)", ... },   -- French; the first one is the main reason
      steps = {
        { k = "pick" | "obj" | "turn" | "fly" | "hs" | "train" | "vendor" | "meet" | "grind",
          q = 1234,                    -- questID (pick / obj / turn); nil otherwise
          o = 1,                       -- objective index into the quest's objectives (obj only)
          map = 1424, x = 40.0, y = 50.0,
          label = "Tuer 10 Loups",
          eta = 12,                    -- minutes after the start of the plan by which the step should be done
          who = "Ami-Royaume" },       -- optional: the member this step is for (squad route)
      } },
  },
}
```

Rules for the addon:

* Reject the plan when `v ~= 1` or when `char ~= TreeAI:CharKey()`. Show a plan made more than 24 h ago as « plan ancien ».
* For a `k` it does not know, show the step's `label`.
* `meet` is a regroup point for the squad. `grind` means "kill mobs here until the next step is unlocked".
* Verdict labels: `ok` → « OK », `galere` → « On va galérer », `long` → « Trop long »,
  `impossible` → « Pas faisable », `zone` → « Changez de zone ».
* Routes come in this order: `xph`, `clear`, `squad` (only when in a group), `safe`.
