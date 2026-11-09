-- QUESTS (4.1) — the quest brain, replayed on a realistic log (SCREEN405: level-29 Horde hunter, group of 4, frFR,
-- WoW: Forever). Les Tarides / Mille pointes / Serres-Rocheuses / Orneval / Thunder Bluff, quests finished in several
-- zones, the 3 items of « La revanche de Gann » at one place, a dungeon quest (Kraal de Tranchebauge, its NPC inside
-- the instance), a grey quest, an elite quest, two turn-ins at the same NPC, quests to take (grey, elite, dungeon,
-- other faction, other class, done, prerequisite missing, too high, far away, a chain).
-- Every audit item of research/quests41.md is a check here; « SEE » lines dump what the player would see per mode.
-- Run by tests/wow/test_quests41.py (forever + modern). Fake quest / NPC ids, French titles, real client map bounds.
return function(M, ctx)
  local T = TreeAI
  if T.modules.Brain and T.SetModuleEnabled then T:SetModuleEnabled("Brain", false) end   -- the pilot is tested apart
  local check = ctx.check
  local P, Z, X, R, Cm, QB = T.Planner, T.ZonePlan, T.Export, T.Route, T.Compass, T.QuestBrain
  check(P and Z and X and R and Cm, "quest chain loaded")
  if not (P and Z and X and R and Cm) then return end
  local SEE = os.getenv("Q41_SEE") == "1"
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
  local VARIANT = os.getenv("Q41_VARIANT") or "full"
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
  local function stopNumbersOf(q)
    local st, out, seen = stops(), {}, {}
    local num = R.Groups(st)
    for i, e in ipairs(st) do
      if e.step and e.step.q == q and e.step.k == "obj" and not seen[num[i]] then seen[num[i]] = true; out[#out + 1] = num[i] end
    end
    return out
  end
  local function head(id) for _, l in ipairs(Z:HeadLines()) do if l.id == id then return l end end end
  local function zoneKind(m)
    local i = CM.GetMapInfo(m)
    return i and i.mapType
  end
  local DUNGEON_NAMES = { ["Kraal de Tranchebauge"] = true }
  local ENGLISH = { "Thunder Bluff", "Razorfen", "Stonespire", "Turn in", "Pick up", "Kill " }
  local function noEnglish(s, what)
    for _, w in ipairs(ENGLISH) do check(not tostring(s):find(w, 1, true), what .. ": no English « " .. w .. " » in « " .. tostring(s) .. " »") end
  end

  local out0 = #M.output
  if VARIANT == "noquestie" then
    -- ================================================================ N. every mode without Questie
    check(QB and QB.HasQuestie and not QB.HasQuestie(), "N: Questie is absent")
    local L0 = QB.Learned and QB.Learned()
    if L0 then L0.give, L0.fin = {}, {} end
    exportNow(); replan(); M.Advance(4, 0.5)
    see("N zone route: " .. stopsText(10))
    local rqN = routeQuests()
    check(rqN[6001] and rqN[6002] and rqN[6070] and rqN[6003], "N: the zone route has the quests of the log (game points only): " .. stopsText(8))
    check(#stopNumbersOf(6001) == 1, "N: « La revanche de Gann »: one stop without Questie too (" .. table.concat(stopNumbersOf(6001), ",") .. ")")
    check(not rqN[6050], "N: the dungeon quest (client tag) stays out")
    -- « Rendre mes quêtes »: the six finished quests, at the game's turn-in points, flights / hearth across zones
    local tlN = QB.TurnInList()
    local tqN = {}
    for _, e in ipairs(tlN) do
      tqN[e.questID] = e
      see(("N turn-in: « %s » · %s %s %s · %s"):format(e.title, tostring(e.npc), tostring(e.map), tostring(e.x), tostring(e.eta and math.floor(e.eta / 60 + 0.5))))
    end
    for _, q in ipairs({ 6003, 6010, 6011, 6020, 6030, 6040 }) do check(tqN[q] and tqN[q].map and tqN[q].x, "N: finished " .. q .. " has a turn-in place") end
    QB.SetMode("turnin")
    exportNow(); replan(); M.Advance(4, 0.5)
    see("N turn-in route: " .. stopsText(14))
    rqN = routeQuests()
    for _, q in ipairs({ 6003, 6010, 6011, 6020, 6030, 6040 }) do check(rqN[q], "N: « Rendre mes quêtes » has " .. q) end
    local fastN = false
    for _, e in ipairs(stops()) do if e.kind == "fly" or e.kind == "hs" then fastN = true end end
    check(fastN, "N: flights / hearth used without Questie")
    -- « Quêtes à prendre »: nothing known yet → said plainly (what needs Questie)
    check(#QB.PickupList(5) == 0, "N: nothing to take is invented without Questie")
    local note = QB.PickupNote()
    check(note and note:find("Questie", 1, true) and note:find("parlé", 1, true), "N: « " .. tostring(note) .. " »")
    local tipN
    for _, m in ipairs(QB.Modes()) do if m.id == "pickup" then tipN = m.tip end end
    check(tipN and tipN:find("Questie", 1, true), "N: the « Quêtes à prendre » tip says it")
    -- the player talks to the scout of Poste de Librevent (gossip: 3 quests offered, 1 to turn in)
    local npcName
    local un = UnitName
    rawset(_G, "UnitName", function(u) if u == "npc" then return npcName end return un(u) end)
    local hadGI = rawget(_G, "C_GossipInfo")
    local GI = hadGI or {}
    rawset(_G, "C_GossipInfo", GI)
    local avail, active = {}, {}
    GI.GetAvailableQuests = function() return avail end
    GI.GetActiveQuests = function() return active end
    goTo(1441, 46.2, 50.9)
    npcName = "Éclaireur Tête-de-Faucon"
    avail = { { questID = 6100, title = "Sécurité de la route", questLevel = 29 },
      { questID = 6101, title = "Le secret des Galak", questLevel = 30 },
      { questID = 6103, title = "Vieilles affaires", questLevel = 20, isTrivial = true } }
    M.FireEvent("GOSSIP_SHOW")
    -- …and Gann takes his quest back here (QUEST_PROGRESS while it is not finished: the turn-in NPC is learned)
    npcName = "Gann Sabot-de-Pierre"
    goTo(1441, 45.6, 50.6)
    rawset(_G, "GetQuestID", function() return 6001 end)
    rawset(_G, "GetTitleText", function() return "La revanche de Gann" end)
    M.FireEvent("QUEST_PROGRESS")
    -- a quest offered at Camp Taurajo (QUEST_DETAIL)
    goTo(1413, 44.8, 59.1)
    npcName = "Mangeclous"
    rawset(_G, "GetQuestID", function() return 6120 end)
    rawset(_G, "GetTitleText", function() return "Les raiders kolkars" end)
    M.FireEvent("QUEST_DETAIL")
    goTo(1441, 46.0, 51.0)
    rawset(_G, "GetQuestID", nil); rawset(_G, "GetTitleText", nil)   -- (not in the mock: no leak)
    local plN = QB.PickupList(8)
    local pqN = {}
    for i, e in ipairs(plN) do
      pqN[e.questID] = e
      see(("N pickup %d: « %s » · %s · %s"):format(i, e.title, tostring(e.npc), tostring(e.why)))
    end
    check(pqN[6100] and pqN[6101], "N: the quests the scout offered are proposed")
    check(pqN[6120], "N: the quest seen at Camp Taurajo is proposed (further)")
    check(not pqN[6103], "N: the grey (trivial) quest is not")
    check(pqN[6100] and pqN[6100].npc == "Éclaireur Tête-de-Faucon" and tostring(pqN[6100].why):find("rencontré", 1, true),
      "N: the NPC's name (from the game) and why: « " .. tostring(pqN[6100] and pqN[6100].why) .. " »")
    check(pqN[6100] and pqN[6120] and (pqN[6100].score or 0) >= (pqN[6120].score or 0), "N: the near quest ranks before the far one")
    check(QB.LearnedFinisher(6001) and QB.LearnedFinisher(6001).map == 1441, "N: Gann's turn-in place is learned")
    QB.SetMode("pickup")
    exportNow(); replan(); M.Advance(4, 0.5)
    see("N pickup route: " .. stopsText(6))
    rqN = routeQuests()
    check(rqN[6100] and rqN[6101], "N: « Quêtes à prendre » route goes to the learned givers")
    check(not stopsText(6):find("quête 61", 1, true), "N: the stops are named (title seen at the NPC): " .. stopsText(4))
    QB.SetMode("group")
    exportNow(); replan(); M.Advance(4, 0.5)
    see("N group route: " .. stopsText(8))
    rqN = routeQuests()
    check(rqN[6001] and rqN[6070], "N: « Avec le groupe » works without Questie")
    local gturn
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if s.q == 6001 and s.k == "turn" then gturn = s end
    end
    check(gturn and gturn.map == 1441 and math.abs((gturn.x or 0) - 45.6) < 0.5, "N: Gann's turn-in at the learned place")
    QB.SetMode("zone")
    rawset(_G, "UnitName", un)
    if not hadGI then rawset(_G, "C_GossipInfo", nil) end
    check(#M.output == out0, "N: nothing written in the chat")
    return
  end
  -- ================================================================== A. « Tout faire ici » (the zone route), group of 4
  exportNow(); replan(); M.Advance(4, 0.5)
  see("A zone route: " .. stopsText(12))
  local rq = routeQuests()
  -- A1 (SCREEN405 item 1): the 3 items of « La revanche de Gann » at one site = ONE stop, not 3 stops 13 / 25 min apart
  local gn = stopNumbersOf(6001)
  check(#gn == 1, "A1: « La revanche de Gann »: its 3 items (camps ≈ 150 yd apart) are ONE stop (stops " .. table.concat(gn, ",") .. "): " .. stopsText(6))
  -- A1b: one card row for that stop (not 3 rows « dans ~13 min »), its title names the 3 items
  local W0 = T.Window
  if W0 and W0.Guide then
    local g = W0.Guide(12)
    local rows = 0
    local rowT
    for _, r in ipairs(g.steps or {}) do if tostring(r.title):find("Nitroglycérine", 1, true) or tostring(r.title):find("Pulpe de bois", 1, true) then rows = rows + 1; rowT = r.title end end
    see("A Gann row: " .. tostring(rowT))
    check(rows == 1 and rowT:find("Pulpe de bois", 1, true) and rowT:find("Nitrate de sodium", 1, true),
      "A1: one card row for Gann's stop with its 3 items (" .. rows .. "): « " .. tostring(rowT) .. " »")
  end
  -- A1c: at the Tranchecrin site, Gann's stop is stop 1: compass = card = tab, same text
  goTo(1441, 52.5, 41.5)
  replan(); M.Advance(3, 0.5)
  Cm.Rebuild("force")
  local ctg = Cm.Current()
  local stA = stops()
  local l1 = R.StopLabel(P, stA, 1, R.Groups(stA))
  see("A at the site: compass « " .. tostring(ctg and ctg.label) .. " » · stop 1 « " .. tostring(l1) .. " »")
  check(ctg and l1 and ctg.label == l1 and l1:find("Nitroglycérine", 1, true) and l1:find("Pulpe de bois", 1, true),
    "A1: compass label = the whole stop 1: « " .. tostring(ctg and ctg.label) .. " » vs « " .. tostring(l1) .. " »")
  if W0 and W0.Guide then
    local g1 = W0.Guide(3).steps[1]
    check(g1 and l1 and g1.title == l1:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""), "A1: card row 1 = the same text: « " .. tostring(g1 and g1.title) .. " »")
  end
  goTo(1441, 46.0, 51.0)
  replan(); M.Advance(3, 0.5)
  -- A2 (SCREEN405 item 2): the card title / route zone label is never a dungeon name; it is the zone of the stops
  local th = Z:ThemeParts()
  local W = T.Window
  local title = W and W.CardTitle and th and W.CardTitle(th) or (th and th.zone)
  see("A card title: " .. tostring(title) .. " · subtitle: " .. tostring(Z:StyleName()))
  check(th and not DUNGEON_NAMES[tostring(th.zone)], "A2: the theme zone is not the dungeon: « " .. tostring(th and th.zone) .. " »")
  check(not DUNGEON_NAMES[tostring(title)] and not tostring(title):find("Kraal", 1, true), "A2: card title « " .. tostring(title) .. " » is not a dungeon")
  local thG = Z:ThemeParts(6001)
  see("A theme of Gann's quest: " .. tostring(thG and (thG.full or thG.zone)))
  check(thG and not tostring(thG.zone):find("Kraal", 1, true) and not tostring(thG.full):find("Kraal", 1, true),
    "A2: the theme of « La revanche de Gann » (its NPC listed inside the instance) is not « Kraal de Tranchebauge »: « "
    .. tostring(thG and (thG.full or thG.zone)) .. " »")
  local f1 = stops()[1]
  local s1map = f1 and (f1.step and f1.step.map or (f1.place and f1.place.map))
  check(th and s1map and th.zone == Z.MapName(Z.ZoneOf(s1map)), "A2: the card zone = the zone of stop 1 (« " .. tostring(th and th.zone)
    .. " » vs « " .. tostring(s1map and Z.MapName(Z.ZoneOf(s1map))) .. " »)")
  for i = P.cursor or 1, #(P.steps or {}) do
    local s = P.steps[i]
    if s.q == 6001 and s.k == "turn" then
      check(s.map == nil or s.map == 1441, "A2: « Rendre « La revanche de Gann » » is never placed inside the instance (map " .. tostring(s.map) .. ")")
    end
  end
  check(rq[6001] and rq[6001] >= 4, "A2: …so Gann's quest keeps its turn-in in the route")
  local zl = head("zone:pick")
  check(zl and not strip(zl.text):find("Kraal", 1, true), "A2: route zone line « " .. strip(zl and zl.text) .. " »")
  -- A3: no dungeon quest in a route of 4 when dungeons are not wanted; its NPC (inside the instance) never a stop
  check(not rq[6050], "A3: the dungeon quest « Les racines du Kraal » is not in the route (dungeons not wanted)")
  for _, e in ipairs(stops()) do
    local m = e.step and e.step.map
    check(not (m and zoneKind(m) == 4), "A3: no stop inside an instance map: " .. strip(e.step and P:StepLabel(e.step) or e.text))
  end
  -- A4: the elite quest is in the route of 4 (« Le chef Galak », the group has it)
  check(rq[6070], "A4: the elite quest « Le chef Galak » is in the route of a group of 4")
  -- A5: a grey quest is never worked in the route (« Brouet de rat », level 22 at 29)
  check(not rq[6060], "A5: the grey quest « Brouet de rat » is not in the zone route")
  check(not rq[6061], "A5: the grey quest « Vieux contrat » (here, in Mille pointes) is not in the zone route")
  check(tostring(Z:MaskedText()):find("2 grises", 1, true), "A5: …and said: « " .. tostring(Z:MaskedText()) .. " »")
  -- A6: the turn-ins / quests of other zones are not mixed into « Tout faire ici » (Les Tarides, Orneval…)
  for _, q in ipairs({ 6010, 6011, 6012, 6020, 6030, 6040 }) do check(not rq[q], "A6: quest " .. q .. " of another zone is not in the zone route") end
  -- A7: stable order: replanning with nothing changed, or after a few steps, keeps the same stops
  local before = stopsText(8)
  replan(); M.Advance(3, 0.5)
  check(stopsText(8) == before, "A7: same order after a replan: " .. stopsText(8) .. " ≠ " .. before)
  goTo(1441, 46.3, 51.2)                                -- a few steps
  replan(); M.Advance(3, 0.5)
  local after = stopsText(8):gsub("%[%d+ min%]", "")
  check(after == before:gsub("%[%d+ min%]", ""), "A7: same order after a few steps: " .. after)
  goTo(1441, 46.0, 51.0)
  -- A7b: « Suivant » (the strip): Skip() passes stop 1 at once (its quests, for the session); Prev() brings it back
  local q1 = QB and QB.FirstStopQuests and QB.FirstStopQuests() or {}
  local first1 = stopsText(1)
  check(QB and QB.Skip and QB.Skip() == true, "A7b: Skip() → true")
  local after1 = stopsText(1)
  check(after1 ~= first1, "A7b: stop 1 changed at once: « " .. first1 .. " » → « " .. after1 .. " »")
  replan(); M.Advance(3, 0.5)
  local rqS = routeQuests()
  for _, q in ipairs(q1) do check(not rqS[q], "A7b: skipped quest " .. q .. " stays out after the rebuild") end
  check(tostring(Z:MaskedText()):find("Suivant", 1, true), "A7b: said: « " .. tostring(Z:MaskedText()) .. " »")
  check(QB.Prev() == true and QB.Prev() == false, "A7b: Prev() brings it back once")
  replan(); M.Advance(3, 0.5)
  check(stopsText(8) == before, "A7b: same route as before the skip: " .. stopsText(8))
  -- A8: no English name anywhere in the route view
  for _, l in ipairs(R:PlanLines(P) or {}) do noEnglish(strip(l.text), "A8 route line") end

  -- A9 (SCREEN405 item 1, SPF part): Shortest Path Forever gets the new order after every rebuild
  local sent = {}
  local fakeSPF = { NavigateRoute = function(owner, list) sent[#sent + 1] = list return true end,
    CurrentStop = function() return 1 end, Ended = function() return nil end, Cancel = function() return true end }
  local oldSPF = R.SPF
  local saveCmSPF = Cm.SPF
  Cm.SPF = function() return fakeSPF end
  local guide0 = T.settings.guide
  T.settings.guide, T.settings.guideV = "auto", 1           -- « Guidage : Suivi Forever » (sinon TreeAI ne lui envoie rien)
  R.following, R._sentAt = true, nil
  local s0 = #sent
  -- a rebuild that changes the order: the player accepts « Sécurité de la route »
  table.insert(M.quests, { id = 6100, title = "Sécurité de la route", level = 29, map = 1441, xp = 2400,
    objectives = { obj("Pillard des collines tué : 0/8", 0, 8) } })
  M.FireEvent("QUEST_ACCEPTED", 6100); M.FireEvent("QUEST_LOG_UPDATE")
  exportNow(); replan(); M.Advance(4, 0.5)
  local last = sent[#sent]
  local f1b = stops()[1]
  check(#sent > s0 and last and last[1] and f1b and f1b.step and last[1].questID == f1b.step.q,
    "A9: SPF got the new route after the rebuild (stop 1 there = TreeAI stop 1: " .. tostring(last and last[1] and last[1].title)
    .. " / " .. tostring(f1b and f1b.step and P:StepLabel(f1b.step)) .. ")")
  local n1 = #sent
  replan(); M.Advance(4, 0.5)
  check(#sent == n1, "A9: …and not again when nothing changed (" .. (#sent - n1) .. " resend)")
  Cm.SPF, R.following = saveCmSPF, nil
  T.settings.guide = guide0
  R.SPF = oldSPF
  -- remove 6100 again (abandon)
  for i = #M.quests, 1, -1 do if M.quests[i].id == 6100 then table.remove(M.quests, i) end end
  M.FireEvent("QUEST_REMOVED", 6100); M.FireEvent("QUEST_LOG_UPDATE")
  exportNow(); replan()

  -- ================================================================== B. the quest brain API
  check(QB and QB.Modes and QB.SetMode and QB.Mode and QB.PickupList and QB.TurnInList, "B: T.QuestBrain API (Modes, SetMode, Mode, PickupList, TurnInList)")
  if not (QB and QB.Modes) then return end
  local modes = QB.Modes()
  local ids = {}
  for _, m in ipairs(modes) do
    ids[#ids + 1] = m.id
    check(type(m.label) == "string" and type(m.short) == "string" and type(m.tip) == "string" and m.label ~= "", "B: mode " .. tostring(m.id) .. " has label / short / tip")
    noEnglish(m.label .. " " .. m.tip, "B mode " .. tostring(m.id))
    see(("B mode %s · %s · « %s » · count %s · key %s%s"):format(m.id, m.label, m.short, tostring(m.count), tostring(m.key), m.active and " · ACTIF" or ""))
  end
  check(table.concat(ids, ",") == "turnin,pickup,zone,group,fastlvl", "B: modes in order (4.3.5: + fastlvl): " .. table.concat(ids, ","))
  local act = 0
  for _, m in ipairs(modes) do if m.active then act = act + 1 end end
  check(act == 1 and QB.Mode() == "zone", "B: one active mode, « zone » by default (" .. tostring(QB.Mode()) .. ")")
  local fired = {}
  T:On("QUEST_MODE_CHANGED", "q41test", function(_, _, id) fired[#fired + 1] = id end)

  -- ================================================================== C. « Rendre mes quêtes »
  local tl = QB.TurnInList()
  local tq = {}
  for _, e in ipairs(tl) do
    tq[e.questID] = true
    see(("C turn-in: « %s » · %s · %s %.1f %.1f · %s"):format(e.title, tostring(e.npc), tostring(e.map), e.x or -1, e.y or -1, tostring(e.eta and math.floor(e.eta / 60 + 0.5) .. " min")))
    noEnglish(tostring(e.title) .. " " .. tostring(e.npc), "C turn-in")
    check(e.npc == nil or not tostring(e.npc):find("Kraal"), "C: npc name French / known")
  end
  for _, q in ipairs({ 6003, 6010, 6011, 6020, 6030, 6040 }) do check(tq[q], "C: finished quest " .. q .. " is in TurnInList") end
  for _, q in ipairs({ 6001, 6002, 6070, 6012, 6060, 6050 }) do check(not tq[q], "C: unfinished quest " .. q .. " is not in TurnInList") end
  check(QB.SetMode("turnin") and QB.Mode() == "turnin" and fired[#fired] == "turnin", "C: SetMode(turnin) + QUEST_MODE_CHANGED")
  exportNow(); replan(); M.Advance(4, 0.5)
  see("C route: " .. stopsText(12))
  rq = routeQuests()
  for _, q in ipairs({ 6003, 6010, 6011, 6020, 6030, 6040 }) do check(rq[q], "C: turn-in of " .. q .. " is in the route (other zones too)") end
  for _, q in ipairs({ 6001, 6002, 6070, 6012, 6060 }) do check(not rq[q], "C: nothing else in « Rendre mes quêtes » (" .. q .. ")") end
  local okKind = true
  for i = P.cursor or 1, #(P.steps or {}) do
    local s = P.steps[i]
    if s.q and not s._done and not s._other and s.k ~= "turn" and not (s.k == "pick" and s.onWay) then okKind = false end
  end
  check(okKind, "C: only « Rendre » stops (+ 4.2: quests picked up in passing at an NPC on the way)")
  -- the nearest first: Freewind (Les crocs de Galak, 30 yd) before the far zones
  local c1 = stops()[1]
  check(c1 and c1.step and c1.step.q == 6003, "C: stop 1 = the turn-in a few steps away (then the pick-ups at the same NPC): " .. stopsText(2))
  -- the two turn-ins at the same NPC (Mangeclous, Camp Taurajo) = one stop
  local nums = {}
  for i, e in ipairs(stops()) do if e.step and (e.step.q == 6010 or e.step.q == 6011) then nums[#nums + 1] = R.Groups(stops())[i] end end
  check(#nums == 2 and nums[1] == nums[2], "C: two turn-ins at the same NPC are one stop (" .. table.concat(nums, ",") .. ")")
  -- hearth / flights considered: the route uses at least one fast leg (flight or hearth) for 5 zones
  local fast = false
  for _, e in ipairs(stops()) do if e.kind == "fly" or e.kind == "hs" or e.kind == "zeppelin" or e.kind == "boat" then fast = true end end
  check(fast, "C: flights / hearthstone are part of « Rendre mes quêtes » across zones: " .. stopsText(14))
  -- the card / route label says the mode, not a dungeon / a zone
  check(Z:StyleName() == "Rendre mes quêtes", "C: subtitle = the mode: « " .. tostring(Z:StyleName()) .. " »")
  local ql = head("qb:mode")
  check(ql and strip(ql.text) == "Que faire : Rendre mes quêtes ▾", "C: the route view says the mode: « " .. strip(ql and ql.text) .. " »")
  local ents = Z:ModeEntries()
  local found
  for _, e in ipairs(ents) do if strip(e.text):find("Rendre mes quêtes (6)", 1, true) then found = e end end
  check(found and found.fn, "C: the « Que faire » menu lists « Rendre mes quêtes (6) »")
  local cnt
  for _, m in ipairs(QB.Modes()) do if m.id == "turnin" then cnt = m.count end end
  check(cnt == 6, "C: « Rendre mes quêtes » count = 6 finished quests (" .. tostring(cnt) .. ")")
  -- stable: no reorder on a replan
  local cBefore = stopsText(12)
  replan(); M.Advance(3, 0.5)
  check(stopsText(12) == cBefore, "C: stable after a replan")

  -- ================================================================== D. « Quêtes à prendre »
  local pl = QB.PickupList(10)
  local pq, prev = {}, nil
  for i, e in ipairs(pl) do
    pq[e.questID] = i
    see(("D pickup %d: « %s » (niv %d) · %s · %.0f yd · %s · %s XP · %s"):format(i, e.title, e.level or 0, tostring(e.npc),
      e.dist or -1, tostring(e.eta and math.floor(e.eta / 60 + 0.5) .. " min"), tostring(e.xp), tostring(e.why)))
    check(type(e.why) == "string" and e.why ~= "", "D: each pick-up says why")
    check(e.title and e.map and e.x and e.y, "D: each pick-up has a place")
    noEnglish(e.title .. " " .. tostring(e.why) .. " " .. tostring(e.npc), "D pickup")
    if prev then check((prev.score or 0) >= (e.score or 0), "D: ranked by value") end
    prev = e
  end
  for _, q in ipairs({ 6100, 6101, 6120 }) do check(pq[q], "D: quest to take " .. q .. " is proposed") end
  for q, why in pairs({ [6103] = "grey", [6105] = "dungeon (not wanted)", [6106] = "other faction", [6107] = "already done",
      [6108] = "prerequisite missing", [6109] = "too high", [6110] = "other class" }) do
    check(not pq[q], "D: quest " .. q .. " (" .. why .. ") never proposed")
  end
  check(pq[6104], "D: the elite quest to take is proposed to a group of 4")
  check(pq[6111] == nil or pq[6111] > pq[6100], "D: the far quest ranks after the near one")
  local w101
  for _, e in ipairs(pl) do if e.questID == 6101 then w101 = e.why end end
  check(w101 and w101:find("suite", 1, true), "D: a chain says it opens the next quest: « " .. tostring(w101) .. " »")
  -- solo: no elite, no dungeon even if wanted
  party(1)
  local plSolo = QB.PickupList(10)
  for _, e in ipairs(plSolo) do
    check(e.questID ~= 6104, "D: solo: no elite quest to take")
    check(e.questID ~= 6105, "D: solo: no dungeon quest")
  end
  party(4)
  Z.db.dungeons = true
  local plDg = QB.PickupList(12)
  local dg = false
  for _, e in ipairs(plDg) do if e.questID == 6105 then dg = true end end
  check(dg, "D: group of 4 and dungeons wanted: the dungeon quest is proposed")
  Z.db.dungeons = false
  -- stable ranking across ticks / small moves
  local function order(l) local t = {} for _, e in ipairs(l) do t[#t + 1] = e.questID end return table.concat(t, ",") end
  local o1 = order(QB.PickupList(10))
  M.Advance(10, 1)
  goTo(1441, 46.1, 51.1)
  check(order(QB.PickupList(10)) == o1, "D: same ranking after a few steps / ticks: " .. order(QB.PickupList(10)) .. " vs " .. o1)
  goTo(1441, 46.0, 51.0)
  -- the route of the mode = the pick-ups
  QB.SetMode("pickup")
  exportNow(); replan(); M.Advance(4, 0.5)
  see("D route: " .. stopsText(10))
  local onlyPick = true
  local any = false
  for i = P.cursor or 1, #(P.steps or {}) do
    local s = P.steps[i]
    if s.q and not s._done and not s._other then any = true if s.k ~= "pick" then onlyPick = false end end
  end
  check(any and onlyPick, "D: « Quêtes à prendre » route = pick-ups only")
  check(Z:StyleName() == "Quêtes à prendre", "D: subtitle = the mode")

  -- ================================================================== E. « Avec le groupe »
  QB.SetMode("group")
  exportNow(); replan(); M.Advance(4, 0.5)
  see("E route: " .. stopsText(10))
  rq = routeQuests()
  check(rq[6001] and rq[6070] and rq[6002], "E: the quests the group shares are in the route")
  -- the shared quests' objectives come before any other quest's objectives (what is at hand may come first)
  local SHARED = { [6001] = true, [6002] = true, [6070] = true }
  local seenOther, late = false, nil
  for _, e in ipairs(stops()) do
    local s = e.step
    if s and s.k == "obj" then
      if SHARED[s.q] then if seenOther then late = late or s.q end else seenOther = true end
    end
  end
  check(not late, "E: the group's common quests are done first (" .. tostring(late) .. " comes after another quest): " .. stopsText(8))
  check(Z:StyleName() == "Avec le groupe", "E: subtitle = the mode")

  -- ================================================================== F. solo: elite quest out, said
  QB.SetMode("zone")
  party(1)
  exportNow(); replan(); M.Advance(4, 0.5)
  see("F solo zone route: " .. stopsText(10))
  rq = routeQuests()
  check(not rq[6070], "F: solo: the elite quest is out of the route")
  check(tostring(Z:MaskedText()):find("élite", 1, true), "F: …and said: « " .. tostring(Z:MaskedText()) .. " »")
  party(4)

  -- ================================================================== G. hygiene: nothing in chat, no OnUpdate added
  check(#M.output == out0, "G: nothing written in the chat (" .. (#M.output - out0) .. " line(s): " .. tostring(M.output[#M.output]) .. ")")
  check(QB.SetMode("nope") == false and QB.Mode() == "zone", "G: unknown mode refused")
  -- G2: NPC names: Questie's translation table (frFR client) has Mangeclous only → the others are omitted, never English
  rawset(_G, "QuestieLoader", { _modules = { l10n = { npcNameLookup = { frFR = { [7010] = { "Mangeclous" } } } } } })
  exportNow()
  local names = {}
  for _, e in ipairs(QB.TurnInList()) do names[e.questID] = e.npc or false end
  check(names[6010] == "Mangeclous" and names[6003] == false, "G: NPC name only when translated (" .. tostring(names[6010]) .. ", " .. tostring(names[6003]) .. ")")
  rawset(_G, "QuestieLoader", nil)
  exportNow()
  T:On("QUEST_MODE_CHANGED", "q41test", function() end)
end
