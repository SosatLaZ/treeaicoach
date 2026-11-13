-- SEE41 (4.1 INTEG) — « what the player sees », replayed on the SCREEN405 situation: WoW: Forever, frFR, level-29 Horde
-- hunter in a group of 4 (party frames top-left like the default UI), standing in Les Tarides, quests finished in several
-- zones (Les Tarides, Mille pointes, Serres-Rocheuses, Orneval, Pitons-du-Tonnerre), « La revanche de Gann » (3 items at
-- one place). Real-client geometry. Dumps (SEE41=1) and checks against research/LAYOUT41 (scratch) / research/see41.md:
--   * on screen by default: the button strip, the compass, the minimap button + pins only; nothing over the party /
--     player / target frames, the quest tracker or the minimap;
--   * the strip: Rendre (n) · À prendre (n) · Suivant · Zone · Groupe · Journal · TreeAI, counts = T.QuestBrain;
--   * the compass line = the Quêtes page « Prochaine étape » (same text, same distance) = stop 1 of the route;
--   * Quêtes page top: the 4 modes, « Prochaine étape », the route; Groupe page: « Équipement du groupe ».
-- World / log / QuestieDB fixture: the one of quests41_scenario.lua (fake ids, French names, real map bounds).
-- lua5.1 tests/wow/harness.lua -q --no-storm --geometry ready=1 --profile sur --flavor forever --class HUNTER --level 29 \
--   --allow LibQuestieDB … --scenario tests/wow/see41_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  if T.modules.Brain and T.SetModuleEnabled then T:SetModuleEnabled("Brain", false) end
  local check = ctx.check
  local P, Z, X, R, Cm = T.Planner, T.ZonePlan, T.Export, T.Route, T.Compass
  if not check(P and Z and X and R and Cm and M.geo, "quest chain + geometry") then return end
  local SEE = os.getenv("SEE41") == "1"
  local function see(s) if SEE then io.write("SEE41 ", s, "\n") end end
  local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  -- a returning player: no wizard / tour
  T.db.onboarded = T.version or true
  T.db.tour = { done = 999 }
  local wiz = T.GetWizard and T:GetWizard()
  if wiz then wiz:Hide() end

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


  -- ------------------------------------------------------------------ the SCREEN405 moment: Les Tarides, group of 4
  M.AddItem(99201, { name = "Hache rouillée", quality = 0, ilvl = 5, subType = "Haches à une main", equipLoc = "INVTYPE_WEAPON" })
  M.AddItem(99202, { name = "Casque de cuir clouté", quality = 2, ilvl = 26, equipLoc = "INVTYPE_HEAD" })
  for i, m in ipairs(M.group) do
    local gear = {}
    for _, s in ipairs({ 1, 3, 5, 6, 7, 8, 9, 10, 15, 16 }) do gear[s] = 99202 end
    if i == 1 then gear[16] = 99201 end
    m.gear, m.inRange, m.guid = gear, true, "Player-0-S" .. i
  end
  local UIP = UIParent
  local party = {}
  for i = 1, 4 do
    local f = rawget(_G, "PartyMemberFrame" .. i)
    if type(f) ~= "table" then f = CreateFrame("Frame", "PartyMemberFrame" .. i, UIP) end
    f.__parent, f.__w, f.__h = UIP, 128, 53
    f.__points = { { "TOPLEFT", UIP, "TOPLEFT", 10, -160 - (i - 1) * 60 } }
    f.__shown = i <= 3
    party[#party + 1] = f
  end
  M.FireEvent("GROUP_ROSTER_UPDATE")
  goTo(1413, 46.0, 57.5)                               -- Les Tarides, near the Carrefour
  X:Rebuild()
  M.Advance(8, 0.1)
  P:Refresh(true)
  M.Advance(40, 0.5)
  if wiz then wiz:Hide() end
  local chat0 = #M.output

  -- ------------------------------------------------------------------ 1. frames on screen by default
  local geo = M.geo
  local W, S = T.Window, T.Shortcuts
  local ALLOWED = { "^TreeAIStrip", "^TreeAICompass", "^TreeAIMinimap", "^TreeAIQuestPin", "^TreeAIPath", "^TreeAIPin", "^TreeAIHub" }
  local function allowed(n) for _, p in ipairs(ALLOWED) do if n:find(p) then return true end end return false end
  local mm = rawget(_G, "Minimap")
  local shown, bad = {}, {}
  for _, f in ipairs(M.frames) do
    local n = f.GetName and f:GetName()
    local par = f.GetParent and f:GetParent()
    if n and n:find("^TreeAI") and n ~= "TreeAICoreFrame" and (par == UIP or par == nil or par == mm) and f:IsVisible() then
      local r = geo.Rect(f)
      if r and r.w > 1 then
        shown[#shown + 1] = n
        see(("ÉCRAN %s %d,%d %dx%d%s"):format(n, r.l, r.b, r.w, r.h, par == mm and " (minimap)" or ""))
        if not allowed(n) then bad[#bad + 1] = n end
      end
    end
  end
  check(#bad == 0, "à l'écran par défaut : bande, boussole, pastille et repères seulement (en trop : " .. table.concat(bad, ", ") .. ")")
  local function overlap(a, b)
    return a and b and a.l < b.l + b.w - 1 and a.l + a.w > b.l + 1 and a.b < b.b + b.h - 1 and a.b + a.h > b.b + 1
  end
  local guards = { "PlayerFrame", "TargetFrame", "ObjectiveTrackerFrame", "MinimapCluster" }
  for _, f in ipairs(party) do if f:IsShown() then guards[#guards + 1] = f:GetName() end end
  for _, n in ipairs(shown) do
    local f = rawget(_G, n)
    if f and f:GetParent() ~= mm then
      for _, gname in ipairs(guards) do
        local gf = rawget(_G, gname)
        check(not overlap(geo.Rect(f), gf and gf:IsShown() and geo.Rect(gf)), ("%s pas sur %s"):format(n, gname))
      end
    end
  end

  -- ------------------------------------------------------------------ 2. the strip
  S.Refresh(true)
  local dump = S.Dump()
  see("BANDE " .. table.concat(dump, " · "))
  -- (5.0 LOOK) la bande : la pastille du guidage, puis Quêtes · Perso · Groupe · TreeAI
  local want = { "Guidage quêtes : ON", "Rendre", "Prendre", "Suivant", "Zone", "Vite", "Sacs", "Équipement" }
  for i, l in ipairs(want) do check(dump[i] and dump[i]:sub(1, #l) == l, ("bouton %d = « %s » (%s)"):format(i, l, tostring(dump[i]))) end
  check(dump[#dump] == "TreeAI" or tostring(dump[#dump]):find("^TreeAI"), "… TreeAI à la fin (" .. tostring(dump[#dump]) .. ")")
  local QB = T.QuestBrain
  local nTurn = #QB.TurnInList()
  check(dump[2]:find("(" .. nTurn .. ")", 1, true), ("Rendre (%d) = les quêtes finies du journal (%s)"):format(nTurn, dump[2]))
  check(nTurn == 6, "6 quêtes finies dans 5 zones (" .. nTurn .. ")")
  for _, st in ipairs(S.lastState or {}) do
    see(("BOUTON %s : %s · touche %s%s"):format(st.label, strip(st.title), tostring(st.key or "aucune"), st.active and " · choisi" or ""))
  end

  -- ------------------------------------------------------------------ 3. the compass line
  local wd = Cm.Widgets()
  local cLabel, cDist = strip(wd.label and wd.label:GetText()), strip(wd.dist and wd.dist:GetText())
  see("BOUSSOLE « " .. cLabel .. " » · « " .. cDist .. " »" .. (wd.tag and wd.tag:GetText() and wd.tag:GetText() ~= "" and (" · " .. strip(wd.tag:GetText())) or ""))
  check(wd.frame and wd.frame:IsShown() and cLabel ~= "" and cDist ~= "", "la boussole montre une cible et sa distance")

  -- ------------------------------------------------------------------ 4. the Quêtes page
  W.Open("quests")
  M.Advance(1, 0.1)
  local function stepsShown()
    local list = {}
    for _, f in ipairs(M.frames) do
      if type(f.step) == "table" and f:IsVisible() then list[#list + 1] = { f = f, y = select(5, f:GetPoint(1)) or 0 } end
    end
    table.sort(list, function(a, b) return (tonumber(a.y) or 0) > (tonumber(b.y) or 0) end)
    return list
  end
  for i, r in ipairs(W.lastLay.rects) do if i <= 16 then see(("QUÊTES %02d %-6s %s"):format(i, r.kind, r.label)) end end
  local st = stepsShown()
  for i, e in ipairs(st) do if i <= 6 then see(("QUÊTES étape %d : « %s » · « %s »"):format(i, strip(e.f.step.title), strip(e.f.step.sub))) end end
  -- ONE truth: the compass box, « Prochaine étape » and stop 1 of the route say the same thing at the same distance
  -- (the compass cuts a long text to 40 letters with « … » — the window has room for all of it)
  local function norm(x) return (strip(x):gsub("%s+", " ")) end
  local tgt = Cm.Current and Cm.Current()
  local full = tgt and strip(tgt.label or tgt.title) or "?"
  local nx, s1 = st[1] and st[1].f.step, st[2] and st[2].f.step
  check(tgt and tgt.plan, "la boussole vise l'arrêt 1 du trajet (" .. tostring(tgt and tgt.key) .. ")")
  check(cLabel == strip(Cm.ShortLabel(full)) and not cLabel:find("+ Ren", 1, true),
    ("boussole = sa cible en 40 lettres (« %s » / « %s »)"):format(cLabel, full))
  check(nx and strip(nx.title) == full and norm(nx.sub) == norm(cDist), ("Prochaine étape = la boussole (« %s » · « %s » / « %s » · « %s »)")
    :format(nx and strip(nx.title) or "?", nx and strip(nx.sub) or "?", full, cDist))
  -- (4.3 V43) les arrêts sont sur la page Trajet (la page Quêtes garde la carte du trajet)
  W.Select("route")
  M.Advance(1, 0.1)
  local rt = stepsShown()
  s1 = rt[1] and rt[1].f.step
  check(s1 and strip(s1.title) == full and norm(s1.sub) == norm(cDist), ("arrêt 1 du trajet = la boussole (« %s » · « %s »)")
    :format(s1 and strip(s1.title) or "?", s1 and strip(s1.sub) or "?"))
  if rt[2] then
    check(strip(rt[2].f.step.title) ~= strip(s1 and s1.title or ""), "un arrêt = une ligne (pas deux fois le même PNJ)")
  end
  W.Select("quests")
  M.Advance(1, 0.1)
  -- (5.0 LOOK) en tête : 3 tuiles d'action (Rendre · Prendre · Tout faire ici) + la case « Guidage des quêtes » ; la
  -- zone (« Changer ») dans « Ma zone » ; les 2 autres modes dans « Autres façons de jouer »
  local nTiles, nAct, zoneRow = 0, 0, nil
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "tile" then nTiles = nTiles + 1 end
    if r.kind == "action" then nAct = nAct + 1 end
    if r.kind == "row" and r.label:find("^Zone : ") then zoneRow = r end
  end
  local r1 = W.lastLay.rects[1]
  check(nAct == 3 and nTiles == 2 and r1 and r1.kind == "action" and r1.label == "Rendre mes quêtes" and zoneRow,
    "Quêtes : 3 tuiles d'action en tête, 2 autres modes, la ligne de zone (" .. tostring(r1 and r1.label) .. ", " .. nAct .. " + " .. nTiles .. ")")

  -- ------------------------------------------------------------------ 5. the Groupe page
  W.Select("group")
  M.Advance(1, 0.1)
  local gg = false
  for i, r in ipairs(W.lastLay.rects) do
    if i <= 24 then see(("GROUPE %02d %-6s %s"):format(i, r.kind, r.label)) end
    if r.label == "Équipement du groupe" then gg = true end
  end
  check(gg, "Groupe : section « Équipement du groupe »")
  W.Close()
  local nChat = 0
  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then nChat = nChat + 1 see("CHAT " .. M.output[i]) end end
  check(nChat == 0, "rien dans le chat (" .. nChat .. ")")
end
