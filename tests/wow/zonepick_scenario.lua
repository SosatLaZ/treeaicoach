-- 4.1 ZONEPICK — « Choisir ma zone » + « Conseille-moi », replayed on the QUESTS world (level-30 Horde hunter, group of 4,
-- frFR, WoW: Forever): Mille pointes (Poste de Librevent) / Les Tarides / Les Serres-Rocheuses / Orneval / Thunder Bluff,
-- six finished quests in five zones, quests to take that must NOT be counted (grey, other faction, done, prerequisite
-- missing, too early, other class, dungeon). Checks: one entry (strip « Zone », the top line of the Quêtes page,
-- /tai zone), honest counts per zone, French names, real travel (flights), XP/h, group fit, the why, ranking + star,
-- the four chips, click = the route goes there and stays (player's choice), « Automatique », the advice block (3 lines
-- max, one click each, errands first), the Quêtes page labels say what they are, nothing in the chat — with QuestieDB and
-- WITHOUT it (NPCs the player met, « (sans Questie : estimation) » once). Run by tests/wow/test_zonepick.py.
-- Fake quest / NPC ids, French titles, real client map bounds. ZP_SEE=1 prints what the player sees.
return function(M, ctx)
  local T = TreeAI
  if T.modules.Brain and T.SetModuleEnabled then T:SetModuleEnabled("Brain", false) end   -- the pilot is tested apart
  local check = ctx.check
  local P, Z, X, R, Cm, QB = T.Planner, T.ZonePlan, T.Export, T.Route, T.Compass, T.QuestBrain
  check(P and Z and X and R and Cm, "quest chain loaded")
  if not (P and Z and X and R and Cm) then return end
  local SEE = os.getenv("ZP_SEE") == "1"
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
  M.player.level = 30
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
  local VARIANT = os.getenv("ZP_VARIANT") or "full"
  if VARIANT ~= "noquestie" then
    local lib = M.InstallQuestieDB({ areaToMap = AREAS, xp = XP, quests = QUESTS, npcs = NPCS, items = ITEMS })
    lib.Support.Get("ZoneDB").private.dungeons = { [491] = { "Kraal de Tranchebauge" } }
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
  local function exportNow() X:Rebuild(); M.Advance(8, 0.1) end
  local function replan() P:Refresh(true); M.Advance(5, 0.05) end
  local function stops() return R:Stops(64) end
  local function routeQuests()
    local set = {}
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if s.q and not s._done and not s._other then set[s.q] = (set[s.q] or 0) + 1 end
    end
    return set
  end
  local W, S = T.Window, T.Shortcuts
  check(W and S and QB and QB.ZoneRows and W.OpenZonePicker, "ZONEPICK: window, strip and the zone picker are loaded")
  if not (W and S and QB and QB.ZoneRows and W.OpenZonePicker) then return end
  local ENGLISH = { "Thunder Bluff", "Thousand Needles", "Barrens", "Stonetalon", "Ashenvale", "Turn in", "Pick up", "quests for you" }
  local function noEnglish(s, what)
    for _, w in ipairs(ENGLISH) do check(not tostring(s):find(w, 1, true), what .. ": no English « " .. w .. " » in « " .. tostring(s) .. " »") end
  end
  -- the row button of the layout with this entry id (what the player clicks)
  local function rowButton(id)
    for _, f in ipairs(M.frames) do
      if type(f.entry) == "table" and f.entry.id == id and f:IsVisible() then return f end
    end
  end
  local function chipButton(label)
    for _, f in ipairs(M.frames) do
      if f.labelText and strip(f.labelText):gsub("^● ", "") == label and f:IsVisible() then return f end
    end
  end
  local function layout(prefix)
    local out = {}
    for i, r in ipairs(W.lastLay and W.lastLay.rects or {}) do
      out[#out + 1] = r
      see(("%s %02d %-6s %s"):format(prefix, i, r.kind, r.label))
    end
    return out
  end
  local function waitScan() for _ = 1, 200 do if not QB.ScanBusy() then break end M.Advance(0.1, 0.05) end end
  local function rowOf(rows, map) for _, r in ipairs(rows) do if r.map == map then return r end end end
  local function rowsText(rows)
    local t = {}
    for i, r in ipairs(rows) do
      t[#t + 1] = ("%d. %s%s · %s · %s · %s · ~%d XP/h · %s · %s"):format(i, r.star and "★ " or "", r.name, tostring(r.lo) .. "-" .. tostring(r.hi),
        QB.ZoneCountText(r), tostring(r.travel), r.xph or 0, tostring(r.group), tostring(r.why))
    end
    return table.concat(t, "\n   ")
  end
  local out0 = #M.output
  local nq = VARIANT == "noquestie"
  QB.char.zpSort, QB.char.zpNoElite, W.zonePick, W.zoneAdvice = "moi", false, false, false   -- (the harness clicked everything)
  M.player.level = 30
  M.inventory[5] = { id = 0, dur = 70, durMax = 80 }    -- nothing to repair (errands are tested below)
  M.inventory[0] = { id = 2515, count = 1200 }
  if nq then
    -- the player talked to the scout of Poste de Librevent and to Mangeclous at Camp Taurajo (no Questie: learned)
    check(not QB.HasQuestie(), "N: Questie is absent")
    local L0 = QB.Learned and QB.Learned()
    if L0 then L0.give, L0.fin = {}, {} end
    local npcName
    local un = UnitName
    rawset(_G, "UnitName", function(u) if u == "npc" then return npcName end return un(u) end)
    local hadGI = rawget(_G, "C_GossipInfo")
    local GI = hadGI or {}
    rawset(_G, "C_GossipInfo", GI)
    GI.GetAvailableQuests = function() return { { questID = 6100, title = "Sécurité de la route", questLevel = 29 },
      { questID = 6101, title = "Le secret des Galak", questLevel = 30 },
      { questID = 6103, title = "Vieilles affaires", questLevel = 20, isTrivial = true },
      { questID = 6107, title = "Déjà faite", questLevel = 29 } } end
    GI.GetActiveQuests = function() return {} end
    goTo(1441, 46.2, 50.9)
    npcName = "Éclaireur Tête-de-Faucon"
    M.FireEvent("GOSSIP_SHOW")
    goTo(1413, 44.8, 59.1)
    npcName = "Mangeclous"
    rawset(_G, "GetQuestID", function() return 6120 end)
    rawset(_G, "GetTitleText", function() return "Les raiders kolkars" end)
    M.FireEvent("QUEST_DETAIL")
    rawset(_G, "GetQuestID", nil); rawset(_G, "GetTitleText", nil)
    rawset(_G, "UnitName", un)
    if not hadGI then rawset(_G, "C_GossipInfo", nil) end
    goTo(1441, 46.0, 51.0)
  end
  exportNow(); replan(); M.Advance(4, 0.5)
  M.Advance(31, 1)                                     -- known flight masters are re-read every 30 s (Route.KnownTaxi)

  -- ================================================================== 1. one entry: the strip « Zone » button opens the picker
  S.Refresh(true)
  local zb = S.Buttons and S.Buttons().zone
  check(zb and zb:IsShown(), "the strip has its « Zone » button")
  QB.SetMode("pickup")
  if zb then zb:Click("LeftButton") end
  M.Advance(0.5, 0.1)
  check(W.IsOpen() and W.CurrentPage() == "quests" and W.zonePick, "strip « Zone » (left click) opens the picker on the Quêtes page")
  check(QB.Mode() == "zone", "…and the mode is « Tout faire ici » (" .. tostring(QB.Mode()) .. ")")
  waitScan()
  W.Render()
  local L1 = layout("PICKER")
  local head, auto, star, nEst = false, false, nil, 0
  for _, r in ipairs(L1) do
    if r.kind == "header" and r.label == "Choisir ma zone" then head = true end
    if r.label:find("Automatique", 1, true) then auto = true end
    if r.label:find("Conseillé", 1, true) and not star then star = r end
    if r.label:find("sans Questie : estimation", 1, true) then nEst = nEst + 1 end
    noEnglish(r.label, "picker")
  end
  check(head and auto and star, "picker: header, « Automatique », a « Conseillé » row (" .. tostring(star and star.label) .. ")")
  if nq then check(nEst == 1, "N: « (sans Questie : estimation) » said once (" .. nEst .. ")")
  else check(nEst == 0, "with Questie: no « estimation » note") end
  for _, label in ipairs({ "Pour moi", "Pour le groupe", "Rendre d'abord", "Pas d'élites" }) do
    check(chipButton(label), "chip « " .. label .. " »")
  end

  -- ================================================================== 2. rows: honest counts, French names, travel, XP/h, group, why
  local rows = QB.ZoneRows()
  see("ROWS moi:\n   " .. rowsText(rows))
  local mp, tar, ser, orn = rowOf(rows, 1441), rowOf(rows, 1413), rowOf(rows, 1442), rowOf(rows, 1440)
  check(mp and tar and ser and orn, "rows for Mille pointes, Les Tarides, Les Serres-Rocheuses, Orneval")
  check(not rowOf(rows, 9301), "never the dungeon as a zone row")
  if mp then
    -- Mille pointes at 30: to take = 6100, 6101, 6111 + the elite 6104 (group of 4) — never the grey 6103, the
    -- Alliance 6106, the done 6107, the chain 6108 (6101 not done), the too-early 6109, the warrior's 6110, the dungeon
    -- 6105; in the log: 6001, 6002, 6070 (6061 grey) ; finished: 6003
    local wantAvail = nq and 2 or 4
    check(mp.avail == wantAvail and mp.open == 3 and mp.fin == 1, ("Mille pointes: %d to take (want %d), %d open, %d finished"):format(mp.avail, wantAvail, mp.open, mp.fin))
    check(QB.ZoneCountText(mp) == ("%d quêtes pour toi (1 finie à rendre)"):format(wantAvail + 4), "« " .. QB.ZoneCountText(mp) .. " »")
    check(mp.here and mp.travel == "tu y es", "Mille pointes: « tu y es » (" .. tostring(mp.travel) .. ")")
    if not nq then check(mp.lo == 29 and mp.hi == 31, ("Mille pointes: levels %s-%s"):format(tostring(mp.lo), tostring(mp.hi))) end
    check(tostring(mp.group):find("^à 4 : ") and (nq or tostring(mp.group):find("élite possible", 1, true)), "group fit « " .. tostring(mp.group) .. " »")
  end
  if tar then
    check(tar.avail == 1 and tar.open == 1 and tar.fin == 2, ("Les Tarides: %d to take, %d open (6060 grey out), %d finished"):format(tar.avail, tar.open, tar.fin))
    check(QB.ZoneCountText(tar) == "4 quêtes pour toi (2 finies à rendre)", "« " .. QB.ZoneCountText(tar) .. " »")
    check(tostring(tar.travel):find("^%d+ min"), "Les Tarides: a real travel time « " .. tostring(tar.travel) .. " »")
    check(tostring(tar.travel):find("vol", 1, true), "Les Tarides: by flight (Poste de Librevent → Camp Taurajo known): « " .. tostring(tar.travel) .. " »")
    check(tostring(tar.why):find("2 quêtes finies à rendre", 1, true), "why « " .. tostring(tar.why) .. " »")
  end
  if ser then check(ser.avail == 0 and ser.fin == 1 and QB.ZoneCountText(ser) == "1 quête pour toi (1 finie à rendre)", "Serres-Rocheuses « " .. QB.ZoneCountText(ser) .. " »") end
  local okOrder = true
  for i = 2, #rows do if QB.Bucket(rows[i].score) > QB.Bucket(rows[i - 1].score) then okOrder = false end end
  check(okOrder and rows[1].star and not (rows[2] and rows[2].star), "ranked by score, the top row (only) is starred")
  check(tostring(rows[1].why):find("^la plus rentable : "), "top row why « " .. tostring(rows[1].why) .. " »")
  for _, r in ipairs(rows) do
    noEnglish(r.name .. " " .. r.why .. " " .. tostring(r.group), "row")
    check(r.xph >= 0 and (r.total == 0 or r.xph > 0), r.name .. ": an XP/h estimate (" .. tostring(r.xph) .. ")")
  end

  -- ================================================================== 3. chips
  local c = chipButton("Rendre d'abord")
  if c then c:Click("LeftButton") end
  M.Advance(0.2, 0.1)
  local rr = QB.ZoneRows()
  see("ROWS rendre:\n   " .. rowsText(rr))
  check(QB.char.zpSort == "rendre" and rr[1] and rr[1].map == 1413 and tostring(rr[1].why):find("^le plus à rendre : "),
    "chip « Rendre d'abord »: Les Tarides (2 finished) first (" .. tostring(rr[1] and rr[1].name) .. ")")
  c = chipButton("Pour le groupe")
  if c then c:Click("LeftButton") end
  M.Advance(0.2, 0.1)
  local rg = QB.ZoneRows()
  see("ROWS groupe:\n   " .. rowsText(rg))
  local mpg = rowOf(rg, 1441)
  check(QB.char.zpSort == "groupe" and mpg and mpg.common == 3 and tostring(mpg.group):find("3 quêtes communes", 1, true),
    "chip « Pour le groupe »: Mille pointes has 3 common quests (6001, 6002, 6070): « " .. tostring(mpg and mpg.group) .. " »")
  check(rg[1] and rg[1].map == 1441, "for the group, Mille pointes (common quests, elites at 4) comes first (" .. tostring(rg[1] and rg[1].name) .. ")")
  if not nq then
    c = chipButton("Pas d'élites")
    if c then c:Click("LeftButton") end
    M.Advance(0.2, 0.1)
    local mpn = rowOf(QB.ZoneRows(), 1441)
    check(QB.char.zpNoElite and mpn and mpn.avail == 3 and not tostring(mpn.group):find("élite", 1, true),
      "chip « Pas d'élites »: the elite quest is not counted (" .. tostring(mpn and mpn.avail) .. ", « " .. tostring(mpn and mpn.group) .. " »)")
    c = chipButton("Pas d'élites")
    if c then c:Click("LeftButton") end
  end
  c = chipButton("Pour moi")
  if c then c:Click("LeftButton") end
  M.Advance(0.2, 0.1)
  check(QB.char.zpSort == "moi" and not QB.char.zpNoElite, "chips back to « Pour moi »")

  -- ================================================================== 4. click a zone: the route goes there and stays
  W.Render()
  local tb = rowButton("zp:1413")
  check(tb, "the Les Tarides row is clickable")
  if tb then tb:Click("LeftButton") end
  M.Advance(0.5, 0.1)
  check(Z.char.mode == "zones" and Z.char.zones[1413] and Z.char.zoneBy == "player" and QB.Mode() == "zone" and not W.zonePick,
    "click « Les Tarides »: work zone = Les Tarides, chosen by the player, mode « Tout faire ici », picker closed")
  exportNow(); replan(); M.Advance(4, 0.5)
  local rq = routeQuests()
  check((rq[6010] or rq[6011] or rq[6012]) and not rq[6001] and not rq[6002], "the route works in Les Tarides now (not Mille pointes)")
  for i = P.cursor or 1, #(P.steps or {}) do
    local s = P.steps[i]
    if s.q and s.map and not s._done and not s._other and s.k ~= "turn" then   -- (a turn-in right on the way may stay)
      check(Z.ZoneOf(s.map) == 1413, "route work in Les Tarides only: quest " .. tostring(s.q) .. " map " .. tostring(s.map))
    end
  end
  Cm.Rebuild("force")
  local tg = Cm.Current and Cm.Current()
  local st1 = stops()[1]
  check(tg and tg.plan, "the compass follows the new route (" .. tostring(tg and tg.label) .. ")")
  check(st1 and (st1.kind ~= "quest" or (st1.step and (st1.step.k == "turn" or Z.ZoneOf(st1.step.map) == 1413))),
    "stop 1 heads to Les Tarides (or a turn-in on the way) (" .. tostring(st1 and (st1.text or st1.kind)) .. ")")
  S.Refresh(true)
  local dump = S.Dump()
  check(tostring(dump[5]):find("^Zone") and tostring(dump[5]):find("%*"), "the strip shows « Zone » as the active mode (" .. tostring(dump[5]) .. ")")
  W.Render()
  local L2 = layout("QUÊTES")
  -- (5.0 LOOK) the 3 action tiles and the « Guidage des quêtes » switch come first ; the zone line is in « Ma zone »
  local zl
  for _, r in ipairs(L2) do if not zl and r.kind == "row" and r.label:find("^Zone : ") then zl = r end end
  check(zl and zl.label == "Zone : Les Tarides · choisie par toi", "the zone line of the Quêtes page: « " .. tostring(zl and zl.label) .. " »")
  check(L2[1] and L2[1].kind == "action" and L2[1].label == "Rendre mes quêtes", "top of the Quêtes page: the action tiles (" .. tostring(L2[1] and L2[1].label) .. ")")
  local hero, dupMode, order
  for _, r in ipairs(L2) do
    if r.kind == "hero" and not hero then hero = r.label end
    if r.label:find("^Que faire") then dupMode = r.label end
    if r.label:find("^Ordre des arrêts : ") then order = r.label end
    check(not r.label:find("^Mode : "), "no bare « Mode : … » row (it is the order of the stops): " .. r.label)
  end
  check(hero and hero:find("^Trajet prévu : [^·]*%d+ min"), "the route card says what the time is: « " .. tostring(hero) .. " »")
  check(not dupMode, "no « Que faire » row under the 4 mode tiles (" .. tostring(dupMode) .. ")")
  check(order, "« Ordre des arrêts : Le plus rapide » says what the mode is")
  local sub = W.pageIndex.quests.sub()
  see("SUB " .. tostring(sub))
  check(sub:find("^Mode : Tout faire ici · Zone : Les Tarides") and (sub:find("trajet prévu", 1, true) or not sub:find("min", 1, true)),
    "window subtitle « " .. sub .. " »")
  -- sticky: walking into another zone, the pilot, a replan — the player's choice stays
  goTo(1441, 47.0, 52.0)
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  replan(); M.Advance(2, 0.5)
  local Bm = T.Brain or (T.modules and T.modules.Brain)
  if Bm and Bm.ZonePass then pcall(Bm.ZonePass) end
  check(Z.char.mode == "zones" and Z.char.zones[1413] and not Z.char.zones[1441], "the choice sticks (zone change, replan, pilot)")

  -- ================================================================== 5. « Automatique » and /tai zone
  M.RunSlash("/tai zone")
  M.Advance(0.3, 0.1)
  check(W.IsOpen() and W.zonePick, "/tai zone opens the picker")
  local ab = rowButton("zp:auto")
  if ab then ab:Click("LeftButton") end
  M.Advance(0.3, 0.1)
  check(Z.char.mode == "auto" and not W.zonePick, "« Automatique »: TreeAI picks (zone mode auto)")
  W.Render()
  local zl2
  for _, r in ipairs(W.lastLay.rects) do if not zl2 and r.kind == "row" and r.label:find("^Zone : ") then zl2 = r end end
  check(zl2 and zl2.label == "Zone : Mille pointes · automatique (ta zone)", "zone line « " .. tostring(zl2 and zl2.label) .. " »")
  -- the top line itself opens the picker (« Changer »)
  local zr = rowButton("zone:pick")
  if zr then zr:Click("LeftButton") end
  M.Advance(0.2, 0.1)
  check(W.zonePick, "the « Zone : … · Changer » line opens the picker")
  zb:Click("LeftButton")
  M.Advance(0.2, 0.1)
  check(not W.zonePick, "strip « Zone » again: the picker closes")

  -- ================================================================== 6. « Conseille-moi »: 3 lines max, each applies in one click
  M.player.rested = 4500
  M.RunSlash("/tai zone conseil")
  M.Advance(0.3, 0.1)
  local adv = QB.Advice()
  local L3 = layout("CONSEIL")
  local ah = false
  for _, r in ipairs(L3) do if r.kind == "header" and r.label:find("^Conseil") then ah = true end end
  for i, a in ipairs(adv) do see(("ADVICE %d « %s » · %s · [%s]"):format(i, a.text, tostring(a.why), tostring(a.label))) noEnglish(a.text .. " " .. tostring(a.why), "advice") end
  check(ah and #adv >= 2 and #adv <= 3 and W.zoneAdvice, "« Conseille-moi »: a block of 2–3 lines (" .. #adv .. ")")
  check(adv[1] and adv[1].id == "turnin" and adv[1].text:find("Rends tes 6 quêtes finies", 1, true), "advice 1 = turn in the 6 finished quests (" .. tostring(adv[1] and adv[1].text) .. ")")
  local za
  for _, a in ipairs(adv) do if a.id == "zone" then za = a end end
  check(za and za.why:find("repos : 4 500 XP", 1, true), "the zone advice says the rested XP (" .. tostring(za and za.why) .. ")")
  local tbtn = rowButton("zp:advice:turnin")
  if tbtn then tbtn:Click("LeftButton") end
  M.Advance(0.3, 0.1)
  check(QB.Mode() == "turnin" and not W.zoneAdvice, "one click on the advice: « Rendre mes quêtes »")
  W.ShowAdvice(true)
  M.Advance(0.2, 0.1)
  local zbtn = za and rowButton("zp:advice:zone")
  if zbtn then zbtn:Click("LeftButton") end
  M.Advance(0.3, 0.1)
  check(za and Z.char.mode == "zones" and Z.char.zones[za.map] and QB.Mode() == "zone", "one click on the zone advice: that zone, « Tout faire ici »")
  -- errands first: ammo low
  M.inventory[0] = { id = 2515, count = 80 }
  local adv2 = QB.Advice()
  check(adv2[1] and adv2[1].id == "errands" and adv2[1].text:find("munitions : 80", 1, true) and adv2[1].apply, "ammo low: the errands come first (" .. tostring(adv2[1] and adv2[1].text) .. ")")
  M.inventory[0] = { id = 2515, count = 1200 }
  W.Close()
  local nChat = 0
  for i = out0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then nChat = nChat + 1 see("CHAT " .. M.output[i]) end end
  check(nChat == 0, "nothing in the chat (" .. nChat .. ")")
end
