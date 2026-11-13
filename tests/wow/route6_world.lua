-- ROUTE6 (5.1) — the world of the route scenarios: level-29 Horde hunter, frFR, WoW: Forever, group of 4, Mille pointes /
-- Les Tarides / Orgrimmar with real client map bounds and a fake QuestieDB (fake ids, French titles, no player name).
-- Same world as tests/wow/quests41_scenario.lua; shared by tests/wow/route6_*_scenario.lua (tests/wow/test_route6.py).
-- Usage: local W = dofile("tests/wow/route6_world.lua")(M, ctx, { pre = function(M, Wd) … end, questie = "lib"|"loader"|"both"|"none" })
-- W.goTo(map, x, y) fires the zone event; W.move(map, x, y) only moves the player (walking inside a zone: no event).
-- NOTHING here forces a route rebuild: the scenarios read what the addon shows by itself.
return function(M, ctx, opts)
  opts = opts or {}
  local T = TreeAI

  local check = ctx.check
  local P, Z, X, R, Cm, QB = T.Planner, T.ZonePlan, T.Export, T.Route, T.Compass, T.QuestBrain
  check(P and Z and X and R and Cm, "quest chain loaded")
  if not (P and Z and X and R and Cm) then return end
  local SEE = os.getenv("R6_SEE") == "1"
  local function see(s) if SEE then io.write("SEE ", s, "\n") end end

  -- ------------------------------------------------------------------ world (real client bounds, yards)
  local MAPS = {
    [1440] = { "Orneval", 1, 4672.9, 1700.0, 829.2, -4066.7 },
    [1413] = { "Les Tarides", 1, 1612.5, 2622.9, -5143.8, -7510.4 },
    [1441] = { "Mille pointes", 1, -3966.7, -433.3, -6900.0, -4833.3 },
    [1442] = { "Les Serres-Rocheuses", 1, 2916.7, 3245.8, -339.6, -1637.5 },
    [1454] = { "Orgrimmar", 1, 2273.9, -3680.6, 1338.5, -5083.2 },
    [1456] = { "Les Pitons-du-Tonnerre", 1, -850.0, 516.7, -1545.8, -527.1 },
    [9301] = { "Kraal de Tranchebauge", 47, 1000.0, 1000.0, 0.0, 0.0 },      -- an instance (own continent)
  }
  for id, m in pairs(MAPS) do
    M.mapRects[id] = { top = m[3], left = m[4], h = m[3] - m[5], w = m[4] - m[6], continent = m[2] }
    M.mapNames[id] = m[1]
  end
  -- the instance map is a « Dungeon » (uiMapType 4) whose parent is the continent: ZoneOf cannot climb to a zone
  local CM = rawget(_G, "C_Map")
  CM.GetMapInfo = function(id)
    if id == 9301 then return { mapID = id, name = "Kraal de Tranchebauge", mapType = 4, parentMapID = 1414 } end
    if id == 1414 then return { mapID = id, name = "Kalimdor", mapType = 2, parentMapID = 947 } end
    return { mapID = id, name = M.mapNames[id] or M.player.zone, mapType = 3, parentMapID = 1414 }
  end
  T.Geo.rects = {}
  Z.ResetMaps()
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  rawset(_G, "UnitRace", function() return "Tauren", "Tauren", 6 end)
  M.player.level = 29
  local function party(n)
    M.group = {}
    local cls = { "WARRIOR", "PRIEST", "MAGE" }
    for i = 1, n - 1 do
      local logs = { { [6002] = true, [6070] = true }, { [6001] = true, [6070] = true }, {} }
      M.group[i] = { name = "Compagnon" .. i, class = cls[i], level = 28 + i % 3, online = true, quests = logs[i] }
    end
    M.FireEvent("GROUP_ROSTER_UPDATE")
  end
  party(4)
  local function goTo(map, x, y)
    M.pos = { mapID = map, x = x / 100, y = y / 100 }
    M.player.zone = MAPS[map][1]
    Z._lastAllowed = nil
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
  end
  goTo(1441, 46.0, 51.0)                               -- Poste de Librevent (Mille pointes)
  R.char.paused = false
  Cm.char.custom, Cm.pinned = nil, nil
  Z.char.ignore, Z.char.extra, Z.char.style, Z.char.mode, Z.char.zones = {}, {}, 1, "auto", {}
  Z.db.dungeons, Z.db.elites = false, false
  R.char.taxi = {}
  for _, id in ipairs({ 22, 23, 25, 29, 30, 77, 61, 58 }) do R.char.taxi[id] = true end
  rawset(_G, "GetBindLocation", function() return "Orgrimmar" end)
  if T.Speed and T.Speed.char then T.Speed.char.bind = { map = 1454, x = 54.1, y = 68.4, name = "Orgrimmar" } end

  -- ------------------------------------------------------------------ the quest log (level 29, group of 4)
  local function obj(text, have, need, typ) return { text = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    { id = 6001, title = "La revanche de Gann", level = 29, map = 1441, xp = 2450, objectives = {
      obj("Nitroglycérine : 0/6", 0, 6, "item"), obj("Pulpe de bois : 0/6", 0, 6, "item"), obj("Nitrate de sodium : 0/6", 0, 6, "item") } },
    { id = 6002, title = "Arikara", level = 28, map = 1441, xp = 2300, objectives = { obj("Peau de serpent d'Arikara : 0/1", 0, 1, "item") } },
    { id = 6003, title = "Les crocs de Galak", level = 27, map = 1441, xp = 2100, complete = true, objectives = { obj("Croc de Galak : 8/8", 8, 8, "item") } },
    { id = 6070, title = "Le chef Galak", level = 30, map = 1441, xp = 2900, elite = true, objectives = { obj("Chef Galak tué : 0/1", 0, 1) } },
    { id = 6010, title = "Le journal de Khazgorm", level = 27, map = 1413, xp = 2050, complete = true, objectives = { obj("Journal de Khazgorm : 1/1", 1, 1, "item") } },
    { id = 6011, title = "Les excavateurs de Bael'dun", level = 27, map = 1413, xp = 2050, complete = true, objectives = { obj("Excavateur de Bael'dun tué : 15/15", 15, 15) } },
    { id = 6012, title = "Le poignard de Tranchecrin", level = 26, map = 1413, xp = 1900, objectives = { obj("Poignard de Tranchecrin : 0/1", 0, 1, "item") } },
    { id = 6060, title = "Brouet de rat", level = 22, map = 1413, xp = 1200, objectives = { obj("Rat fouisseur : 0/8", 0, 8, "item") } },
    { id = 6061, title = "Vieux contrat", level = 21, map = 1441, xp = 300, objectives = { obj("Vieille caisse : 0/5", 0, 5, "item") } },
    { id = 6020, title = "Impasse aux Serres-Rocheuses", level = 27, map = 1442, xp = 2050, complete = true, objectives = { obj("Harpie tuée : 10/10", 10, 10) } },
    { id = 6030, title = "Les eaux du lac Mystral", level = 26, map = 1440, xp = 1950, complete = true, objectives = { obj("Fiole d'eau forte remplie : 1/1", 1, 1, "item") } },
    { id = 6040, title = "La flamme sacrée", level = 25, map = 1456, xp = 1800, complete = true, objectives = { obj("Fiole remplie : 1/1", 1, 1, "item") } },
    { id = 6050, title = "Les racines du Kraal", level = 30, map = 9301, xp = 2700, tag = 81, objectives = { obj("Racine du Kraal : 0/1", 0, 1, "item") } },
  }
  M.quests = {}
  for _, q in ipairs(LOG) do M.quests[#M.quests + 1] = q end
  M.questTags = {}
  M.SetCompleted({ [6107] = true, [6900] = true })
  P.char.turned = {}
  M.SetPlan(nil)
  M.FireEvent("QUEST_LOG_UPDATE")

  -- ------------------------------------------------------------------ fake QuestieDB
  -- areas: 331 Orneval, 17 Les Tarides, 400 Mille pointes, 406 Serres-Rocheuses, 1637 Orgrimmar, 1638 Thunder Bluff,
  -- 491 Kraal de Tranchebauge (instance)
  local AREAS = { [331] = 1440, [17] = 1413, [400] = 1441, [406] = 1442, [1637] = 1454, [1638] = 1456, [491] = 9301 }
  local function npc(name, lv, area, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [area] = { { x, y } } } } end
  local function camp(name, lv, area, cx, cy, rank)
    local s = {}
    for i = 0, 7 do s[#s + 1] = { cx + (i % 4) - 1.5, cy + math.floor(i / 4) * 2 - 1 } end
    return { name = name, minLevel = lv, maxLevel = lv + 1, rank = rank or 0, spawns = { [area] = s } }
  end
  local function quest(name, ql, rl, zone, giver, ender, kills, items, extra)
    local q = { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = zone, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and kills or nil, nil, items and items or nil } }
    for k, v in pairs(extra or {}) do q[k] = v end
    return q
  end
  local QUESTS = {
    -- log
    [6001] = quest("La revanche de Gann", 29, 25, 400, 7001, 7001, nil, { 8601, 8602, 8603 }),
    [6002] = quest("Arikara", 28, 24, 400, 7001, 7001, nil, { 8604 }),
    [6003] = quest("Les crocs de Galak", 27, 23, 400, 7002, 7002, nil, { 8605 }),
    [6070] = quest("Le chef Galak", 30, 26, 400, 7002, 7002, { 7605 }),
    [6010] = quest("Le journal de Khazgorm", 27, 22, 17, 7010, 7010, nil, { 8610 }),
    [6011] = quest("Les excavateurs de Bael'dun", 27, 22, 17, 7010, 7010, { 7611 }),
    [6012] = quest("Le poignard de Tranchecrin", 26, 21, 17, 7011, 7011, nil, { 8612 }),
    [6060] = quest("Brouet de rat", 22, 18, 17, 7012, 7012, nil, { 8660 }),
    [6061] = quest("Vieux contrat", 21, 16, 400, 7002, 7002, nil, { 8661 }),
    [6020] = quest("Impasse aux Serres-Rocheuses", 27, 22, 406, 7020, 7020, { 7620 }),
    [6030] = quest("Les eaux du lac Mystral", 26, 20, 331, 7030, 7030, nil, { 8630 }),
    [6040] = quest("La flamme sacrée", 25, 20, 331, 7040, 7040, nil, { 8640 }),
    [6050] = quest("Les racines du Kraal", 30, 25, 491, 7050, 7050, nil, { 8650 }),
    -- to take (Mille pointes, Poste de Librevent unless said)
    [6100] = quest("Sécurité de la route", 29, 25, 400, 7003, 7003, { 7610 }),
    [6101] = quest("Le secret des Galak", 30, 26, 400, 7003, 7003, { 7613 }, nil, { nextQuestInChain = 6108 }),
    [6103] = quest("Vieilles affaires", 20, 15, 400, 7003, 7003, { 7610 }),                       -- grey
    [6104] = quest("Le seigneur de guerre", 31, 27, 400, 7003, 7003, { 7612 }),                  -- elite (rank 1)
    [6105] = quest("Au cœur du Kraal", 31, 27, 491, 7003, 7003, { 7650 }),                       -- dungeon
    [6106] = quest("Pour l'Alliance", 29, 25, 400, 7003, 7003, { 7610 }, nil, { requiredRaces = 77 }),
    [6107] = quest("Déjà faite", 29, 25, 400, 7003, 7003, { 7610 }),                              -- done
    [6108] = quest("Le secret des Galak (suite)", 30, 26, 400, 7003, 7003, { 7613 }, nil, { preQuestSingle = { 6101 } }),
    [6109] = quest("Trop tôt", 33, 32, 400, 7003, 7003, { 7610 }),                                -- too high
    [6110] = quest("Épreuve du guerrier", 29, 25, 400, 7003, 7003, { 7610 }, nil, { requiredClasses = 1 }),
    [6111] = quest("Les ruines du sud", 29, 25, 400, 7004, 7004, { 7614 }),                       -- far away
    [6120] = quest("Les raiders kolkars", 28, 24, 17, 7010, 7010, { 7621 }),                      -- Les Tarides
  }
  local NPCS = {
    -- Gann is listed by Questie inside the instance only (data of the custom server): the game's waypoint knows better
    [7001] = { name = "Gann Sabot-de-Pierre", minLevel = 35, maxLevel = 35, rank = 0,
      spawns = { [491] = { { 30.0, 30.0 }, { 30.5, 30.2 }, { 30.2, 30.6 } } } }, [7002] = npc("Kanati Ventfroid", 35, 400, 46.4, 51.4),
    [7003] = npc("Éclaireur Tête-de-Faucon", 35, 400, 46.2, 50.8), [7004] = npc("Ermite des ruines", 30, 400, 80.0, 85.0),
    [7010] = npc("Mangeclous", 35, 17, 44.8, 59.1), [7011] = npc("Gardien Tranchecrin", 30, 17, 45.0, 59.6),
    [7012] = npc("Rocheyeux", 30, 17, 51.4, 30.6),
    [7020] = npc("Courant-noir", 30, 406, 47.4, 61.0), [7030] = npc("Mastok", 30, 331, 73.1, 61.5),
    [7040] = npc("Flamme-sacrée", 30, 1638, 78.0, 28.0),
    [7050] = npc("Prisonnier du Kraal", 30, 491, 50.0, 50.0),             -- the NPC of the dungeon quest: inside
    -- Gann's three drops: three camps of the same Tranchecrin site (≈ 150 yd apart)
    [7601] = camp("Contrebandier Tranchecrin", 29, 400, 52.0, 40.0), [7602] = camp("Bûcheron Tranchecrin", 29, 400, 55.0, 42.0),
    [7603] = camp("Mineur Tranchecrin", 29, 400, 51.0, 45.0),
    [7604] = npc("Arikara", 28, 400, 38.0, 27.0), [7605] = npc("Chef Galak", 30, 400, 60.0, 35.0, 1),
    [7606] = camp("Galak", 27, 400, 62.0, 30.0),
    [7610] = camp("Pillard des collines", 28, 400, 40.0, 40.0), [7612] = npc("Seigneur de guerre", 31, 400, 64.0, 36.0, 1),
    [7613] = camp("Éclaireur galak", 29, 400, 58.0, 48.0), [7614] = camp("Spectre des ruines", 29, 400, 82.0, 88.0),
    [7650] = npc("Gardien du Kraal", 31, 491, 40.0, 40.0),
    [7611] = camp("Excavateur de Bael'dun", 27, 17, 51.0, 88.0), [7612.5] = nil,
    [7615] = camp("Tranchecrin", 26, 17, 44.0, 62.0), [7616] = camp("Rat fouisseur", 22, 17, 46.0, 40.0),
    [7617] = npc("Coffre de Khazgorm", 1, 17, 48.0, 86.0),
    [7620] = camp("Harpie", 27, 406, 52.0, 66.0), [7621] = camp("Raider kolkar", 28, 17, 47.0, 63.0),
    [7630] = npc("Puits de lune", 1, 331, 49.0, 69.0), [7640] = npc("Puits sacré", 1, 331, 40.0, 70.0),
    [7651] = npc("Racine", 1, 491, 60.0, 60.0),
  }
  local ITEMS = {
    [8601] = { name = "Nitroglycérine", npcDrops = { 7601 } }, [8602] = { name = "Pulpe de bois", npcDrops = { 7602 } },
    [8603] = { name = "Nitrate de sodium", npcDrops = { 7603 } }, [8604] = { name = "Peau de serpent d'Arikara", npcDrops = { 7604 } },
    [8605] = { name = "Croc de Galak", npcDrops = { 7606 } }, [8610] = { name = "Journal de Khazgorm", npcDrops = { 7617 } },
    [8612] = { name = "Poignard de Tranchecrin", npcDrops = { 7615 } }, [8660] = { name = "Rat fouisseur", npcDrops = { 7616 } },
    [8630] = { name = "Fiole d'eau forte remplie", npcDrops = { 7630 } }, [8640] = { name = "Fiole remplie", npcDrops = { 7640 } },
    [8650] = { name = "Racine du Kraal", npcDrops = { 7651 } },
    [8661] = { name = "Vieille caisse", npcDrops = { 7606 } },
  }
  M.waypoints = { [6001] = { 1441, 0.456, 0.506 } }
  local XP = {}
  for id, q in pairs(QUESTS) do XP[id] = 55 * q.questLevel + q.questLevel * q.questLevel end
  if opts.pre then opts.pre(M, { QUESTS = QUESTS, NPCS = NPCS, ITEMS = ITEMS, XP = XP, npc = npc, camp = camp, quest = quest, obj = obj, AREAS = AREAS }) M.FireEvent("QUEST_LOG_UPDATE") end
  -- Questie flavours: "lib" = the QuestieDB library alone (LibQuestieDB, areas in its private ZoneDB table) ; "loader" = the
  -- old Questie (QuestieLoader._modules.QuestieDB / ZoneDB:GetUiMapIdByAreaId) ; "both" ; "none"
  local VARIANT = opts.questie or "lib"
  if VARIANT == "lib" or VARIANT == "both" then
    local lib = M.InstallQuestieDB({ areaToMap = AREAS, xp = XP, quests = QUESTS, npcs = NPCS, items = ITEMS })
    lib.Support.Get("ZoneDB").private.dungeons = { [491] = { "Kraal de Tranchebauge" } }
    if VARIANT == "both" then
      rawset(_G, "QuestieLoader", { _modules = { ZoneDB = { GetUiMapIdByAreaId = function(_, a) return AREAS[a] end } } })
    end
  elseif VARIANT == "loader" then
    local function q(rows) return function(id, key) local r = rows[id] if r then return r[key] end end end
    rawset(_G, "QuestieLoader", { _modules = {
      QuestieDB = { QueryQuestSingle = q(QUESTS), QueryNPCSingle = q(NPCS), QueryObjectSingle = q({}), QueryItemSingle = q(ITEMS),
        QuestPointers = QUESTS },
      ZoneDB = { GetUiMapIdByAreaId = function(_, a) return AREAS[a] end, IsDungeonZone = function(a) return a == 491 end } } })
  else
    -- (« réduis la dépendance aux autres addons ») no Questie at all: the game's own points only (C_QuestLog.GetNextWaypoint:
    -- the objective area of a quest in progress, the NPC of a finished quest), the dungeon quest tagged by the client
    for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "QuestieLoader" }) do rawset(_G, n, nil) end
    M.waypoints = { [6001] = { 1441, 0.52, 0.40 }, [6002] = { 1441, 0.38, 0.27 }, [6003] = { 1441, 0.464, 0.514 },
      [6070] = { 1441, 0.60, 0.35 }, [6010] = { 1413, 0.448, 0.591 }, [6011] = { 1413, 0.448, 0.591 },
      [6012] = { 1413, 0.44, 0.62 }, [6060] = { 1413, 0.46, 0.40 }, [6020] = { 1442, 0.474, 0.61 },
      [6030] = { 1440, 0.731, 0.615 }, [6040] = { 1456, 0.78, 0.28 } }
  end

  -- ------------------------------------------------------------------ helpers
  local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  local function stops() return R:Stops(64) end
  -- numbered stops as the tab / card / map show them (Route.Groups) : « 1. Ramasse … + Ramasse … »
  local function fmtMin(e) return " [" .. math.floor((e.at or 0) / 60 + 0.5) .. " min]" end
  local function stopsText(n)
    local st, out = stops(), {}
    local num = R.Groups(st)
    local cur, curK = nil, 0
    for i, e in ipairs(st) do
      local lbl = strip(e.step and P:StepLabel(e.step) or e.text)
      if num[i] ~= curK then
        if cur then out[#out + 1] = cur end
        if #out >= (n or 12) then cur = nil break end
        curK = num[i]
        cur = curK .. ". " .. lbl .. fmtMin(e)
      else cur = cur .. " + " .. lbl end
    end
    if cur and #out < (n or 12) then out[#out + 1] = cur end
    return table.concat(out, " · ")
  end
  local function out(s) if SEE then io.write("SEE ", tostring(s), "\n") end end
  local function plain(s) return (T.PlainText and T.PlainText(tostring(s or "")) or tostring(s or "")):gsub("|T.-|t", ""):gsub("%s+", " ") end
  local noteAt = #M.output
  local function texts(f, acc, depth)
    acc, depth = acc or {}, depth or 0
    if depth > 6 or type(f) ~= "table" then return acc end
    for _, c in ipairs(rawget(f, "__children") or {}) do
      if type(c) == "table" and (c.IsShown == nil or c:IsShown()) then
        if c.__kind == "FontString" then
          local t = c:GetText()
          if type(t) == "string" and t ~= "" then acc[#acc + 1] = t end
        else texts(c, acc, depth + 1) end
      end
    end
    return acc
  end
  -- what is on screen: every shown TreeAI frame parented to UIParent + the « Maintenant » lines + the route → lines
  local function screen(title)
    local L = {}
    local function add(s) L[#L + 1] = s out(s) end
    out("")
    out("=== " .. tostring(title))
    for i = 1, #M.frames do
      local f = M.frames[i]
      local n = f.GetName and f:GetName()
      if type(n) == "string" and n:find("^TreeAI") and rawget(f, "__parent") == UIParent and f:IsShown() and n ~= "TreeAIMinimapButton" and n ~= "TreeAIMainWindow" then
        if n == "TreeAIStrip" then add("  [bande] " .. plain(T.Shortcuts and T.Shortcuts.DumpLine and T.Shortcuts.DumpLine() or "?"))
        else
          local tx = texts(f)
          if #tx > 0 then add("  [" .. n .. "] " .. plain(table.concat(tx, " | ")):sub(1, 300)) end
        end
      end
    end
    local okL, now = pcall(T.GetTabLines, T, "now")
    for i, l in ipairs(okL and type(now) == "table" and now or {}) do if i <= 8 then add("  [Maintenant] " .. plain(l.text)) end end
    add("  [trajet] " .. stopsText(8))
    for i = noteAt + 1, #M.output do add("  [msg] " .. plain(M.output[i])) end
    noteAt = #M.output
    return L
  end
  if T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  -- walking inside a zone: the position changes, NO zone event (the audit's valid case)
  local function move(map, x, y) M.pos = { mapID = map, x = x / 100, y = y / 100 } M.player.zone = MAPS[map][1] end
  -- perf.on = true : real frames (25 images / s), Lua instructions counted per image (1 kinstr ≈ 0.02 ms : the session
  -- sim's rate, 200 kinstr = the 4 ms rule) — the WHOLE addon's work of each image, not only the route's
  local perf = { on = false, worst = 0, total = 0, frames = 0 }
  local count = 0
  -- perf.where = {} : which file the instructions of the current image ran in (one sample per 1000 instructions)
  -- as tests/wow/session_sim.lua counts a frame: the forever layer of the mock (functions defined after
  -- WowMock.FOREVER_LAYER_LINE: secret values, documented flags, wrappers) is C code in the game, not addon CPU — it moves
  -- the clock (count) but not the addon's own cost (own); the mock's API bodies stay counted, as in the session sim
  local FL = M.FOREVER_LAYER_LINE or math.huge
  local own = 0
  local function hook()
    count = count + 1
    local i = debug.getinfo(2, "S")
    local c = i and i.linedefined >= FL and i.source:find("wowmock.lua", 1, true)
    if not c then own = own + 1 end
    local wh = perf.where
    if wh then
      local src = c and "C" or (i and i.short_src and i.short_src:match("([^/]+)$") or "?")
      wh[src] = (wh[src] or 0) + 1
    end
  end
  perf.count = function() return count end
  do   -- Lua 5.1: a count hook is per coroutine — the route jobs run in coroutines, so every new one gets the hook too
    local create = coroutine.create
    coroutine.create = function(f)
      local co = create(f)
      debug.sethook(co, hook, "", 1000)
      return co
    end
    perf.restore = function() coroutine.create = create end
  end
  local function quiet(sec, dt)
    if not perf.on then return M.Advance(sec, dt or 0.5) end
    if not perf.clock0 then
      -- the client's clock moves while Lua runs: 50 kinstr = 1 ms (session sim's rate), so the 2 ms slices of the route
      -- jobs are cut as in the game (the mock's clock stands still inside an image)
      perf.clock0 = rawget(_G, "debugprofilestop")
      rawset(_G, "debugprofilestop", function() return M.now * 1000 + count / 50 end)
    end
    for _ = 1, math.floor(sec / 0.04 + 0.5) do
      count, own = 0, 0
      debug.sethook(hook, "", 1000)
      M.Advance(0.04, 0.04)
      debug.sethook()
      perf.frames, perf.total = perf.frames + 1, perf.total + own
      if own > perf.worst then perf.worst = own end
      if perf.onFrame then perf.onFrame(own) end
      if perf.where then for k in pairs(perf.where) do perf.where[k] = nil end end
    end
  end
  -- a REAL walk: one position per second at `speed` yd/s (7 = on foot at 29), PLAYER_STARTED_MOVING / STOPPED_MOVING
  -- around it, no zone event — so the measured speed (NavTime) stays true and nothing looks like a teleport
  local function walk(map, x0, y0, x1, y1, speed)
    speed = speed or 7
    local r = M.mapRects[map]
    local yd = math.sqrt(((x1 - x0) / 100 * r.w) ^ 2 + ((y1 - y0) / 100 * r.h) ^ 2)
    local n = math.max(1, math.floor(yd / speed + 0.5))
    M.FireEvent("PLAYER_STARTED_MOVING")
    for i = 1, n do move(map, x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) quiet(1) end
    M.FireEvent("PLAYER_STOPPED_MOVING")
    return n
  end
  local function cleanup()
    perf.restore()
    if perf.clock0 then rawset(_G, "debugprofilestop", perf.clock0) end
    for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "QuestieLoader", "ShortestPathForever" }) do rawset(_G, n, nil) end
  end
  return { out = out, screen = screen, plain = plain, goTo = goTo, move = move, walk = walk, quiet = quiet, party = party,
    stopsText = stopsText, stops = stops, MAPS = MAPS, QUESTS = QUESTS, NPCS = NPCS, ITEMS = ITEMS, LOG = LOG, obj = obj,
    quest = quest, npc = npc, camp = camp, texts = texts, XP = XP, cleanup = cleanup, strip = strip, AREAS = AREAS, perf = perf }
end
