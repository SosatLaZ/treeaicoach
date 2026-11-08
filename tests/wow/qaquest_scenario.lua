-- QA-QUEST (round 9) regression replay: the whole quest-guidance chain as the player lives it, with CHECKS for every
-- P0 / P1 finding of research/qa_quest.md (F1 … F14). Level 28 → 30 Horde hunter in a duo, hearth bound in Orgrimmar:
-- Freewind Post (4 turn-ins) → Shimmering Flats (2 quests + 2 to take + the elite) → a timed quest (5 min left) →
-- death (ghost at the graveyard) → zone picker « Orneval » from the flats → Splintertree → Hillsbrad (other continent)
-- → quest log full (40/40). Fake quest / NPC ids, French titles, real client map bounds and taxi nodes.
-- Variants (QAQ_VARIANT): full (default) · noquestie (only the game's waypoints) · org (Orgrimmar flight master).
-- Run by tests/wow/test_qaquest.py (forever + modern):
-- lua5.1 tests/wow/harness.lua --flavor forever --class HUNTER --allow LibQuestieDB --allow QuestDB --allow NpcDB
--   --allow ObjectDB --allow ItemDB --allow TreeAIPlan --allow IsQuestFlaggedCompleted
--   --scenario tests/wow/qaquest_scenario.lua wow_addon/TreeAI      (QAQ_DUMP=1 prints what the player sees)
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local P, Z, X, R, Q = T.Planner, T.ZonePlan, T.Export, T.Route, T.modules and T.modules.Quests
  local Cm, Sp = T.Compass, T.Speed
  check(P and Z and X and R and Q and Cm and Sp, "quest chain loaded")
  if not (P and Z and X and R and Q and Cm and Sp) then return end
  local VARIANT = os.getenv("QAQ_VARIANT") or "full"
  local E = P.Engine

  -- ------------------------------------------------------------------ world (real client bounds, yards)
  local MAPS = {
    [1441] = { "Mille pointes", 1, -3966.7, -433.3, -6900.0, -4833.3 },
    [1440] = { "Orneval", 1, 4672.9, 1700.0, 829.2, -4066.7 },
    [1413] = { "Les Tarides", 1, 1612.5, 2622.9, -5143.8, -7510.4 },
    [1456] = { "Thunder Bluff", 1, -850.0, 516.7, -1545.8, -527.1 },
    [1412] = { "Mulgore", 1, 266.7, 2479.2, -3835.4, -3675.0 },
    [1454] = { "Orgrimmar", 1, 2273.9, -3680.6, 1338.5, -5083.2 },
    [1411] = { "Durotar", 1, 1808.3, -1962.5, -1716.7, -7250.0 },
    [1442] = { "Les Serres-Rocheuses", 1, 2916.7, 3245.8, -339.6, -1637.5 },
    [1443] = { "Désolace", 1, 452.1, 4233.3, -2545.8, -262.5 },
    [1444] = { "Féralas", 1, -2366.7, 5441.7, -7000.0, -1508.3 },
    [1445] = { "Marécage d'Âprefange", 1, -2033.3, -975.0, -5533.3, -6225.0 },
    [1446] = { "Tanaris", 1, -5875.0, -218.7, -10475.0, -7118.7 },
    [1424] = { "Contreforts de Hautebrande", 0, 400.0, 1066.7, -1733.3, -2133.3 },
    [1421] = { "Forêt des Pins argentés", 0, 1666.7, 3450.0, -1133.3, -750.0 },
    [1420] = { "Clairières de Tirisfal", 0, 3837.5, 3033.3, 825.0, -1485.4 },
    [1458] = { "Fossoyeuse", 0, 1877.9, 873.2, 1237.8, -86.2 },
    [1417] = { "Hautes-terres d'Arathi", 0, -133.3, -866.7, -2533.3, -4466.7 },
  }
  for id, m in pairs(MAPS) do
    M.mapRects[id] = { top = m[3], left = m[4], h = m[3] - m[5], w = m[4] - m[6], continent = m[2] }
    M.mapNames[id] = m[1]
  end
  T.Geo.rects = {}
  Z.ResetMaps()
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 28
  M.group = { { name = "Ami", class = "WARRIOR", level = 28, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  local function goTo(map, x, y)
    M.pos = { mapID = map, x = x / 100, y = y / 100 }
    M.player.zone = MAPS[map][1]
    Z._lastAllowed = nil
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
  end
  goTo(1441, 45.8, 50.6)                                 -- Poste de Librevent (Freewind Post)
  -- known flight paths (Horde): TB 22, Org 23, Crossroads 25, Sun Rock 29, Freewind 30, Taurajo 77, Splintertree 61,
  -- Zoram'gar 58, Tarren Mill 13, Undercity 11, Sepulcher 10
  local KNOWN = { 22, 23, 25, 29, 30, 77, 61, 58, 13, 11, 10 }
  if VARIANT == "nofp" then KNOWN = { 22, 23, 25, 29, 30, 77, 13, 11, 10 } end   -- Splintertree never discovered
  R.char.taxi = {}
  for _, id in ipairs(KNOWN) do R.char.taxi[id] = true end
  -- hearthstone bound in Orgrimmar (Valley of Strength inn)
  rawset(_G, "GetBindLocation", function() return "Orgrimmar" end)
  local Sp = T.Speed
  if Sp and Sp.char then Sp.char.bind = { map = 1454, x = 54.1, y = 68.4, name = "Orgrimmar" } end
  local hsCD = (VARIANT == "hscd") and 1800 or 0           -- hearth on cooldown (30 min left)
  local CC = rawget(_G, "C_Container")
  if type(CC) == "table" then
    local old = CC.GetItemCooldown
    CC.GetItemCooldown = function(id)
      if id == 6948 then if hsCD > 0 then return (M.now or 0) - 10, hsCD + 10, 1 end return 0, 0, 1 end
      return old and old(id)
    end
  end

  -- ------------------------------------------------------------------ the quest log (level 28, duo)
  local DUNGEON, PVP, ELITE = 81, 41, 1
  local function obj(text, have, need, typ) return { text = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    -- Thousand Needles: finished, turn in at Freewind Post
    { id = 5104, title = "L'œuf étrange", level = 26, complete = true, map = 1441, objectives = {} },
    { id = 5105, title = "ON RECHERCHE : Arnak Sinistre", level = 29, complete = true, map = 1441, objectives = {} },
    { id = 5106, title = "Pacifier les centaures", level = 25, complete = true, map = 1441, objectives = {} },
    { id = 5108, title = "Interruption des hurlements", level = 30, complete = true, map = 1441, objectives = {} },
    -- Shimmering Flats (east of Thousand Needles): 2 quests + the elite one
    { id = 5130, title = "Chargement de foudre", level = 30, map = 1441, objectives = { obj("Huile de basilic : 0/5", 0, 5, "item") } },
    { id = 5131, title = "Un cahot sur la route", level = 30, map = 1441, objectives = { obj("Basilic cristallin tué : 0/10", 0, 10) } },
    { id = 5182, title = "Hypercapaciteur bidulotron", level = 30, tag = ELITE, elite = true, map = 1441,
      objectives = { obj("Hypercapaciteur bidulotron : 0/1", 0, 1, "item") } },
    -- Ashenvale leftovers (Splintertree Post)
    { id = 5201, title = "La patte d'Ursangous", level = 25, map = 1440, objectives = { obj("Patte d'Ursangous : 0/1", 0, 1, "item") } },
    { id = 5205, title = "Les estafettes d'Ashenvale", level = 25, map = 1440, objectives = { obj("Estafette tuée : 2/6", 2, 6) } },
    { id = 5110, title = "La flamme sacrée", level = 25, map = 1440, objectives = { obj("Fiole d'eau forte remplie : 0/1", 0, 1, "item") } },
    -- Hillsbrad: done, turn in at Tarren Mill (other continent)
    { id = 5140, title = "Bataille de Hautebrande", level = 29, complete = true, map = 1424, objectives = {} },
    { id = 5141, title = "Les fermes de Hautebrande", level = 27, complete = true, map = 1424, objectives = {} },
    { id = 5142, title = "Élixir de douleur", level = 27, complete = true, map = 1424, objectives = {} },
    -- the Barrens leftover
    { id = 5107, title = "Brouet de rat", level = 23, map = 1413, objectives = { obj("Rat fouisseur : 3/8", 3, 8, "item") } },
    -- dungeon quests
    { id = 5112, title = "Source de pouvoir", level = 33, tag = DUNGEON, map = 1440, objectives = { obj("Noyau de sentinelle fissuré : 0/6", 0, 6, "item") } },
    { id = 5117, title = "Racines de Feuillebleue", level = 26, tag = DUNGEON, map = 1413, objectives = { obj("Racines de Feuillebleue : 0/6", 0, 6, "item") } },
    { id = 5150, title = "Fléau de Tranchebauge", level = 37, tag = DUNGEON, map = 1413, objectives = { obj("Amnennar le Porte-froid tué : 0/1", 0, 1) } },
    -- elite done, turn in at Undercity
    { id = 5111, title = "Cœurs tonnants", level = 33, tag = ELITE, elite = true, complete = true, map = 1458, objectives = {} },
  }
  local function setLog(list) M.quests = {} for _, q in ipairs(list) do M.quests[#M.quests + 1] = q end end
  setLog(LOG)
  M.questTags = { [5301] = DUNGEON }
  M.SetCompleted({})
  P.char.turned = {}
  M.SetPlan(nil)

  -- ------------------------------------------------------------------ fake QuestieDB
  -- areas: 400 Mille pointes, 331 Orneval, 17 Tarides, 1638 TB, 1637 Org, 267 Hautebrande, 1497 Fossoyeuse,
  -- 406 Serres-Rocheuses, 405 Désolace, dungeons 719 BFD, 491 RFK, 722 RFD
  local AREAS = { [400] = 1441, [331] = 1440, [17] = 1413, [1638] = 1456, [1637] = 1454, [267] = 1424, [1497] = 1458,
    [406] = 1442, [405] = 1443, [215] = 1412 }
  local function npc(name, lv, area, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [area] = { { x, y } } } } end
  local function camp(name, lv, area, cx, cy, rank)                     -- a mob camp: 8 spawns around (cx, cy)
    local s = {}
    for i = 0, 7 do s[#s + 1] = { cx + (i % 4) - 1.5, cy + math.floor(i / 4) * 2 - 1 } end
    return { name = name, minLevel = lv, maxLevel = lv + 1, rank = rank or 0, spawns = { [area] = s } }
  end
  local function quest(name, ql, rl, zone, giver, ender, kills, items)
    return { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = zone, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and { kills } or nil, nil, items and { items } or nil } }
  end
  local QUESTS = {
    [5104] = quest("L'œuf étrange", 26, 22, 400, 612, 612),
    [5105] = quest("ON RECHERCHE : Arnak Sinistre", 29, 25, 400, 612, 612),
    [5106] = quest("Pacifier les centaures", 25, 21, 400, 612, 612),
    [5108] = quest("Interruption des hurlements", 30, 25, 400, 613, 613),
    [5130] = quest("Chargement de foudre", 30, 25, 400, 611, 611, nil, { 8130 }),
    [5131] = quest("Un cahot sur la route", 30, 25, 400, 614, 614, { 7131 }),
    [5182] = quest("Hypercapaciteur bidulotron", 30, 25, 400, 611, 611, nil, { 8102 }),
    [5132] = quest("Pièces de voiture-fusée", 30, 25, 400, 614, 614, nil, { 8132 }),          -- to take on the flats
    [5133] = quest("Le contremaître Mécazod", 30, 26, 400, 611, 611, { 7133 }),               -- to take on the flats
    [5240] = quest("Course contre la montre", 29, 25, 400, 614, 614, nil, { 8160 }),         -- timed (15 min)
    [5201] = quest("La patte d'Ursangous", 25, 20, 331, 700, 700, nil, { 8201 }),
    [5205] = quest("Les estafettes d'Ashenvale", 25, 20, 331, 700, 702, { 7205 }),
    [5110] = quest("La flamme sacrée", 25, 20, 1638, 620, 620, nil, { 8110 }),
    [5140] = quest("Bataille de Hautebrande", 29, 24, 267, 640, 640),
    [5141] = quest("Les fermes de Hautebrande", 27, 22, 267, 641, 641),
    [5142] = quest("Élixir de douleur", 27, 22, 267, 642, 642),
    [5107] = quest("Brouet de rat", 23, 18, 17, 622, 622, nil, { 8107 }),
    [5112] = quest("Source de pouvoir", 33, 25, 719, 703, 703, nil, { 8112 }),
    [5117] = quest("Racines de Feuillebleue", 26, 20, 491, 621, 621, nil, { 8117 }),
    [5150] = quest("Fléau de Tranchebauge", 37, 32, 722, 650, 650, { 7150 }),
    [5111] = quest("Cœurs tonnants", 33, 28, 1497, 630, 630),
    -- to take in Ashenvale (Splintertree)
    [5202] = quest("Entre un roc et un Crin-de-Chardon", 26, 20, 331, 700, 700, { 7202 }),
    [5204] = quest("Les traqueurs des ombres", 27, 22, 331, 701, 701, { 7204 }),
    [5206] = quest("Destruction des machines", 27, 22, 331, 701, 701, { 7206 }),
    [5301] = quest("Les profondeurs de Brassenoire", 27, 21, 331, 703, 703, { 7301 }),       -- dungeon (client tag)
    -- to take in Hillsbrad (Tarren Mill): the best zone at 30, on the other continent
    [5401] = quest("Les avant-postes de Hautebrande", 31, 26, 267, 640, 640, { 7401 }),
    [5402] = quest("Sang de la terre", 30, 26, 267, 641, 641, nil, { 8402 }),
    [5403] = quest("Le marais des Rives Brisées", 31, 27, 267, 642, 642, { 7403 }),
    [5404] = quest("Mineurs de Ruissemont", 30, 25, 267, 640, 640, { 7404 }),
    -- Desolace (Ghost Walker Post) and Stonetalon
    [5501] = quest("Kodos perdus", 31, 26, 405, 660, 660, { 7501 }),
    [5502] = quest("Centaures de Gelkis", 33, 28, 405, 660, 660, { 7502 }),
    [5503] = quest("Le Pic de Chantepierre", 29, 24, 406, 670, 670, { 7503 }),
  }
  local NPCS = {
    [612] = npc("Cerf-de-vent", 30, 400, 46.0, 51.4), [613] = npc("Nyrill", 30, 400, 45.6, 50.8),
    [611] = npc("Pozzik", 30, 400, 80.2, 75.9), [614] = npc("Maître de piste Zherin", 30, 400, 81.6, 77.9),
    [700] = npc("Kuray'bin", 30, 331, 73.6, 60.0), [701] = npc("Mastok", 30, 331, 73.1, 61.5), [702] = npc("Pyrotechnicien", 30, 331, 71.2, 68.0),
    [703] = npc("Je'neu Sancrea", 30, 331, 11.6, 34.3), [620] = npc("Sage de Thunder Bluff", 50, 1638, 30.0, 30.0),
    [640] = npc("Haut exécuteur Darthalia", 50, 267, 62.4, 20.4), [641] = npc("Fermier Kalaba", 30, 267, 62.0, 19.4),
    [642] = npc("Apothicaire Lydon", 30, 267, 61.5, 19.2), [622] = npc("Rocheyeux", 30, 17, 51.4, 30.6),
    [621] = npc("Mangletooth", 30, 17, 44.9, 59.1), [650] = npc("Sage de l'Enclos", 40, 17, 49.0, 94.0),
    [630] = npc("Apothicaire", 50, 1497, 50.0, 60.0), [660] = npc("Taureau-noir", 35, 405, 56.0, 59.6),
    [670] = npc("Courant-noir", 30, 406, 47.4, 61.0),
    [7131] = camp("Basilic cristallin", 29, 400, 74.0, 82.0), [7133] = npc("Mécazod", 31, 400, 86.0, 82.0),
    [7101] = camp("Basilic de Sel", 29, 400, 72.0, 78.0), [7102] = npc("Gardien de Bidulotron", 32, 400, 69.0, 89.0, 1),
    [7120] = camp("Gobelin de la piste", 30, 400, 77.0, 74.0),
    [7201] = npc("Ursangous", 26, 331, 42.0, 66.0), [7202] = npc("Crin-de-Chardon", 26, 331, 79.0, 54.0),
    [7204] = camp("Traqueur des ombres", 27, 331, 58.0, 46.0), [7205] = camp("Estafette", 25, 331, 62.0, 52.0),
    [7206] = camp("Déchiqueteur", 27, 331, 68.0, 56.0), [7301] = npc("Garde de Brassenoire", 24, 331, 14.0, 14.0, 1),
    [7110] = npc("Puits de lune", 1, 331, 49.0, 69.0), [7107] = camp("Rat fouisseur", 22, 17, 46.0, 40.0),
    [7117] = npc("Fouisseur", 26, 491, -1, -1), [7150] = npc("Amnennar le Porte-froid", 39, 722, -1, -1, 3),
    [7112] = npc("Sentinelle", 24, 719, -1, -1),
    [7401] = camp("Garde de Ruissemont", 31, 267, 45.0, 30.0), [7402] = camp("Élémentaire de terre", 30, 267, 38.0, 40.0),
    [7403] = camp("Naga des Rives", 31, 267, 30.0, 62.0), [7404] = camp("Mineur de Ruissemont", 30, 267, 33.0, 46.0),
    [7501] = camp("Kodo égaré", 31, 405, 62.0, 40.0), [7502] = camp("Centaure Gelkis", 33, 405, 39.0, 81.0),
    [7503] = camp("Harpie", 29, 406, 52.0, 66.0),
    [7160] = npc("Caisse de pièces", 1, 400, 70.0, 88.0),
  }
  local ITEMS = {
    [8130] = { name = "Huile de basilic", npcDrops = { 7101 } }, [8102] = { name = "Hypercapaciteur", npcDrops = { 7102 } },
    [8132] = { name = "Pièce de voiture-fusée", npcDrops = { 7120 } }, [8201] = { name = "Patte d'Ursangous", npcDrops = { 7201 } },
    [8110] = { name = "Fiole d'eau forte remplie", npcDrops = { 7110 } }, [8107] = { name = "Rat fouisseur", npcDrops = { 7107 } },
    [8112] = { name = "Noyau de sentinelle fissuré", npcDrops = { 7112 } }, [8117] = { name = "Racines", npcDrops = { 7117 } },
    [8402] = { name = "Sang de la terre", npcDrops = { 7402 } },
    [8160] = { name = "Caisse de pièces", npcDrops = { 7160 } },
  }
  local XP = {}
  for id, q in pairs(QUESTS) do XP[id] = 55 * q.questLevel + q.questLevel * q.questLevel end
  local lib
  if VARIANT ~= "noquestie" then
    lib = M.InstallQuestieDB({ areaToMap = AREAS, xp = XP, quests = QUESTS, npcs = NPCS, items = ITEMS })
    lib.Support.Get("ZoneDB").private.dungeons = { [719] = { "Brassenoire" }, [491] = { "Tranchebauge" }, [722] = { "Souilles" } }
  else
    rawset(_G, "LibQuestieDB", nil)
    -- no Questie: the game's own waypoints only (C_QuestLog.GetNextWaypoint on Forever)
    M.waypoints = { [5104] = { 1441, 0.460, 0.514 }, [5105] = { 1441, 0.460, 0.514 }, [5106] = { 1441, 0.460, 0.514 },
      [5108] = { 1441, 0.456, 0.508 }, [5130] = { 1441, 0.72, 0.78 }, [5131] = { 1441, 0.74, 0.82 } }
  end

  -- ------------------------------------------------------------------ helpers
  local function exportNow() X:Rebuild(); M.Advance(8, 0.1) end
  local function replan() P:Refresh(true); M.Advance(5, 0.05) end
  local function tick(n) for _ = 1, n or 1 do M.Advance(3, 0.5) end end
  local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  local function lines(tab) local ok, l = pcall(T.GetTabLines, T, tab) return ok and l or {} end
  local function find(pat, tab)
    for _, tb in ipairs(tab and { tab } or { "now", "quests" }) do
      for _, l in ipairs(lines(tb)) do if strip(l.text):find(pat) then return l end end
    end
  end
  local function sugg(id) for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == id then return e end end end
  local function anySugg(pat) for _, e in ipairs(T:GetSuggestions() or {}) do if strip(e.text):find(pat) then return e end end end
  local function first() return R:Stops(1)[1] end
  local function stopsText(n)
    local t = {}
    for i, e in ipairs(R:Stops(64)) do
      if i > (n or 6) then break end
      t[#t + 1] = i .. ". " .. strip(e.step and P:StepLabel(e.step) or e.text)
    end
    return table.concat(t, " · ")
  end
  local function me() return { mapID = M.pos.mapID, x = M.pos.x, y = M.pos.y } end
  local function dump(tag)
    if not os.getenv("QAQ_DUMP") then return end
    io.stderr:write("\n=== " .. tag .. "\n  STOPS " .. stopsText(8) .. "\n")
    for _, tab in ipairs({ "now", "quests" }) do
      for _, l in ipairs(lines(tab)) do io.stderr:write(("  %-6s [%s] %s\n"):format(tab, tostring(l.source), strip(l.text))) end
    end
    local t = Cm.Current and Cm.Current()
    io.stderr:write("  COMPASS " .. tostring(t and (t.label or t.title)) .. "\n")
  end
  local function addQuest(id, title, lvl, map, objs, extra)
    local q = { id = id, title = title, level = lvl, map = map, objectives = objs or {} }
    for k, v in pairs(extra or {}) do q[k] = v end
    M.quests[#M.quests + 1] = q
    M.FireEvent("QUEST_ACCEPTED", #M.quests, id)
    return q
  end
  local function findQ(id) for _, q in ipairs(M.quests) do if q.id == id then return q end end end
  local function complete(id)
    local q = findQ(id)
    if not q then return end
    q.complete = true
    for _, o in ipairs(q.objectives or {}) do o.have = o.need; o.done = true; o.text = o.text:gsub("%d+/(%d+)", "%1/%1") end
    M.FireEvent("QUEST_LOG_UPDATE")
  end
  local function turnIn(id, xp)
    for i, q in ipairs(M.quests) do if q.id == id then table.remove(M.quests, i) break end end
    M.completed[id] = true
    M.FireEvent("QUEST_TURNED_IN", id, xp or 2000, 0)
    M.FireEvent("QUEST_REMOVED", id)
    M.FireEvent("QUEST_LOG_UPDATE")
  end
  local function progress(id, j, have)
    local q = findQ(id)
    local o = q and q.objectives[j]
    if not o then return end
    o.have = have; o.done = have >= o.need
    o.text = o.text:gsub("%d+/(%d+)", have .. "/%1")
    local all = true
    for _, x in ipairs(q.objectives) do if not x.done then all = false end end
    q.complete = all
    M.FireEvent("QUEST_LOG_UPDATE")
  end
  local function routeQuests()
    local set = {}
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if s.q and not s._done and not s._other then set[s.q] = true end
    end
    return set
  end
  Cm.char.custom, Cm.pinned = nil, nil            -- the generic event storm may have clicked a minimap pin

  local function hearthSlot()
    for b, bag in pairs(M.bags or {}) do
      for s, it in pairs(bag.slots or {}) do if it.id == 6948 then return b, s, it.count end end
    end
  end
  local oldDeath = rawget(_G, "C_DeathInfo")

  -- ================================================================== F8: the hearthstone is counted on Forever
  if M.flavor == "forever" then
    local CI = rawget(_G, "C_Item")
    check(rawget(_G, "GetItemCount") == nil and CI and CI.GetItemCount, "Forever: only C_Item.GetItemCount (no global)")
  end
  check(R.HearthReady() and Sp.HearthReady(), "F8: a hearthstone in the bags, ready → Route and Speed both see it")
  local hb, hsl, hc = hearthSlot()
  if hb then
    M.SetBagItem(hb, hsl, nil)
    check(not R.HearthReady() and not Sp.HearthReady(), "F8: hearthstone left in the bank → never planned / advised")
    M.SetBagItem(hb, hsl, 6948, hc)
  end

  -- ================================================================== O. Orgrimmar (variant org): the flight first
  if VARIANT == "org" then
    goTo(1454, 54.0, 68.0)                                -- auction house / bank
    exportNow(); replan(); tick(2)
    dump("O1")
    local f = first()
    check(f and f.kind == "fly" and f.from and f.from.map == 1454, "F4: from Orgrimmar the way starts with the flight: " .. stopsText())
    local ct = Z.CompassTarget(me())
    check(ct and ct.leg == "fly" and ct.mapID == 1454 and ct.dist and ct.dist < 400,
      "F4: the compass aims at the Orgrimmar flight master (" .. tostring(ct and ct.label) .. ", " .. tostring(ct and ct.dist) .. " yd)")
    check(not (ct and ct.questID), "F4: …not at the far quest in a straight line")
    goTo(1454, 45.3, 63.7)                                -- at the flight master
    M.Advance(4, 0.05); exportNow(); replan(); tick(2)
    dump("O2")
    ct = Z.CompassTarget(me())
    check(ct and ct.leg == "fly" and ct.dist and ct.dist < 30, "F4: at the flight master: arrived (" .. tostring(ct and ct.dist) .. " yd)")
    local line = find("^Arrêt 1/%d+ : Vol pour ")
    check(line and not strip(line.text):find("assez loin"), "F4: « Vol pour … » measured to the flight master, not to the landing: " .. strip(line and line.text))
    rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
    return
  end

  -- ================================================================== A. Freewind Post: 4 turn-ins, the flats 1,3 km away
  exportNow(); replan(); tick(2)
  dump("A1")
  local f = first()
  check(f and f.step and f.step.k == "turn" and f.step.map == 1441, "A1: the turn-ins at hand first: " .. stopsText())
  for _, id in ipairs({ 5104, 5105, 5106, 5108 }) do turnIn(id, 2500) end
  M.Advance(6, 0.05); exportNow(); replan(); tick(2)
  dump("A2")
  f = first()
  check(R.HearthReady(), "A2: the hearthstone is ready (the hearth option is open)")
  check(f and f.kind ~= "hs" and f.step and f.step.map == 1441 and f.place,
    "F1: flats 1,3 km away → walk there, never « hearth → Orgrimmar → fly » first: " .. stopsText())
  local hs3 = false
  for i, e in ipairs(R:Stops(64)) do if i <= 4 and e.kind == "hs" then hs3 = true end end
  check(not hs3 and Z.noHearth == true, "F1: built and shown without the hearth (a stop ≤ 5 min away)")
  if VARIANT == "noquestie" then
    -- F10: positionless stops judged on their quest's zone (Ashenvale / Thunder Bluff quests stay out), and after
    -- the stops with a place; the game's waypoint of an unfinished quest is its objective, not its turn-in
    local rq = routeQuests()
    check(not rq[5201] and not rq[5205] and not rq[5110], "F10: no Ashenvale / Thunder Bluff quest in the Thousand Needles route")
    check(f.place, "F10: the first stop has a place (positionless stops come last): " .. stopsText())
    local rec
    for _, r in ipairs((X:Data() or {}).quests or {}) do if r.id == 5130 then rec = r end end
    check(rec and not rec.f and rec.o and rec.o[1] and rec.o[1].x and math.abs(rec.o[1].x - 72) < 0.5 and rec.zone == 1441,
      "F10: no Questie: the waypoint of « Chargement de foudre » (unfinished) is its objective, the zone is known")
  end
  -- F6: the route XP/h is real XP and believable
  local base = E.GrindXPH({ class = "HUNTER", level = 28 })
  check(P.route and (P.route.xph or 0) > 0 and P.route.xph <= 3 * base,
    ("F6: route XP/h from real XP: %s (≤ 3 × grind %d)"):format(tostring(P.route and P.route.xph), base))
  local okXP = true
  for _, s in ipairs(P.steps or {}) do
    if s.q and type(s.xp) ~= "number" then okXP = false end
    if s.k == "pick" and s.xp ~= 0 then okXP = false end
  end
  check(okXP, "F6: every quest stop carries its real XP (prendre = 0)")
  local head = find("^Trajet %d+ min")
  local tip = strip(head and head.tooltip or "")
  local shownXph = tonumber(((tip:match("(%d[%d ]*) XP/h")) or ""):gsub(" ", ""), 10)
  check(not shownXph or shownXph <= 3 * base, "F6: XP/h shown only when believable: " .. tip)
  -- F7: one route total (horizon): the tab head = R.Horizon, travel inside it
  local all = R:Stops(64)
  local keepN, total, _, _, trav = R.Horizon(all)
  check(head and strip(head.text):find("^Trajet " .. R.Minutes(total) .. " ·", 1, false) and trav and trav <= total,
    "F7: tab head = the horizon total (" .. strip(head and head.text) .. " / " .. R.Minutes(total) .. ")")
  -- F18: one numbering (R.Groups) for the tab, the page and the world map
  local g = R.Groups(all, keepN)
  local a1 = find("^Arrêt 1/%d+")
  local nShown = a1 and tonumber(strip(a1.text):match("^Arrêt 1/(%d+)"))
  check(g and g.n >= 1 and nShown == g.n, "F18: « Arrêt 1/N » counts the same stops as R.Groups (" .. tostring(nShown) .. " / " .. tostring(g and g.n) .. ")")
  -- (4.0 final) the app numbers its Trajet tab with the same stop numbers (summary.plan.next[i].n = R.Groups)
  local nx = P:NextStops(12)
  local okN = #nx > 0
  for i, e in ipairs(nx) do if e.n ~= g[i] then okN = false end end
  check(okN, "F18: summary.plan.next carries the in-game stop numbers (R.Groups)")
  -- F20: a quest the route plans is never « plus tard » beside it; dungeon quests: « à faire en groupe »
  if VARIANT == "full" then
    local Ab = T.modules.Abandon
    if Ab and Ab:IsEnabled() and Ab.Update then pcall(Ab.Update, true) end
    local later
    for _, l in ipairs(lines("quests")) do if l.source == "Abandon" and strip(l.text):find("Chargement de foudre", 1, true) then later = l end end
    check(not later, "F20: « Chargement de foudre » (planned) is not « plus tard » beside its stop")
    local dg = find("« Source de pouvoir » : ", "quests")
    check(not dg or strip(dg.text):find("à faire en groupe", 1, true), "F20: dungeon quest: « à faire en groupe », no XP/min guess: " .. strip(dg and dg.text))
  end
  -- F22: the duo does the normal +2 quest the route gives it; an elite stays « à plusieurs » under 3 players
  local Tr = T.Tracker
  if Tr and Tr.IsHard then
    local oc = Tr.ctx
    Tr.ctx = { group = 2 }
    check(not Tr.IsHard({ q = { complete = false }, diff = 3 }) and Tr.IsHard({ q = { complete = false, elite = true }, diff = 1 }),
      "F22: duo: normal quest +2 not « à plusieurs », elite still « à plusieurs »")
    Tr.ctx = oc
  end
  -- F25: the resurrection sickness counts when one of the next 3 stops is a fight
  local oldAS = P.GetActiveSteps
  P.GetActiveSteps = function() return { { k = "turn" }, { k = "pick" }, { k = "obj" } } end
  check(Sp.FightSoon({ k = "turn" }) == true, "F25: a fight within the next 3 stops → the sickness matters")
  P.GetActiveSteps = function() return { { k = "turn" }, { k = "pick" } } end
  check(Sp.FightSoon({ k = "turn" }) == false, "F25: no fight soon → the sickness does not matter")
  P.GetActiveSteps = oldAS
  -- F27: a click never writes in the chat
  local n0 = #M.output
  P:Navigate({ k = "obj", q = 5130 })
  check(#M.output == n0 and sugg("Planner:nav"), "F27: « pas de position » said in « Maintenant », not in the chat")
  -- F29: the export knows the flight paths Route learned
  local ps = X:PlayerState()
  local kt = {}
  for _, id in ipairs(ps.taxi or {}) do kt[id] = true end
  check(kt[30] and kt[61], "F29: known flight paths in the export (Freewind, Splintertree)")

  -- ================================================================== B. the flats
  goTo(1441, 78.0, 77.0)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  dump("B1")
  f = first()
  check(f and f.step and f.step.map == 1441 and f.kind ~= "hs", "F1: standing on the flats, the flats quests first: " .. stopsText())
  local tq = addQuest(5240, "Course contre la montre", 29, 1441, { obj("Caisse de pièces : 0/1", 0, 1, "item") })
  tq.timer = { 900, 600 }                                -- 5 min left
  M.Advance(4, 0.05); exportNow(); replan(); tick(1)
  dump("B2")
  local st = R:Stops(64)
  local s1, s2 = st[1] and st[1].step, st[2] and st[2].step
  check(s1 and s1.q == 5240 and s1.k == "obj" and s2 and s2.q == 5240 and s2.k == "turn",
    "F2: the timed quest first, its objective then its turn-in: " .. stopsText())
  local dropT = false
  for _, d in ipairs(R.selection and R.selection.drop or {}) do if d.q == 5240 then dropT = true end end
  check(not dropT, "F9: a running timer is never proposed « trop loin »")
  check(not sugg("Edge2:questTimer"), "F28: the timer is said once (Quests), not again by Avantages")
  -- F3: manual compass points (minimap pin, « Y aller », /tai way)
  local mx, my = M.pos.x, M.pos.y
  Cm.SetTarget(Cm, { mapID = 1441, x = mx + 0.003, y = my, label = "Réparation — Test", source = "detect" })
  Cm.Rebuild("force")
  check(Cm.Current() and Cm.Current().kind == "custom", "F3: a « Y aller » point goes first")
  for _ = 1, 5 do Cm.Compute() M.Advance(1, 0.5) end
  check(not Cm.char.custom and Cm.Current() and Cm.Current().kind ~= "custom",
    "F3: reached (≤ 20 m for 3 s) → cleared, the compass is back on the route (" .. tostring(Cm.Current() and Cm.Current().label) .. ")")
  Cm.SetTarget(Cm, { mapID = 1424, x = 0.62, y = 0.20, label = "Réparation — Moulin-de-Tarren", source = "detect" })
  Cm.Rebuild("force")
  local cur = Cm.Current()
  check(cur and cur.kind ~= "custom", "F3: a point on another continent (no arrow possible) never goes first (" .. tostring(cur and cur.label) .. ")")
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  check(not Cm.char.custom, "F3: zone change → the unreachable point is cleared")
  Cm.SetWaypoint(0.20, 0.30, "Repère de la minicarte", 1441)       -- a pin: no source
  Cm.char.custom.at = (GetTime() or 0) - 700
  Cm.Rebuild("force")
  if Z.CompassTarget(me()) then
    check(Cm.Current() and Cm.Current().plan, "F3: a pin set 10+ min ago goes after the route's stop")
  end
  M.RunSlash("/tai way 20 30 Mon camp")
  Cm.char.custom.at = (GetTime() or 0) - 700
  Cm.Rebuild("force")
  check(Cm.Current() and Cm.Current().kind == "custom", "F3: your own /tai way point keeps its place")
  -- /reload: the saved point is not restored unless re-confirmed (one click)
  local savedPt = Cm.char.custom
  Cm._sessionChecked = nil
  Cm.ForgetSaved()
  local rs = sugg("Compass:resume")
  check(not Cm.char.custom and rs and strip(rs.text):find("Mon camp", 1, true) and rs.onClick,
    "F3: after a /reload the point waits for a click (« " .. strip(rs and rs.text) .. " »)")
  if rs and rs.onClick then rs.onClick() end
  check(Cm.char.custom and Cm.char.custom.name == savedPt.name and not sugg("Compass:resume"), "F3: one click → aimed again")
  Cm.ClearWaypoint()
  M.output = {}
  -- death mid-route
  if VARIANT == "full" then
    local p = M.units and M.units.player or M.player
    p.ghost, M.player.ghost = true, true
    M.FireEvent("PLAYER_DEAD")
    goTo(1441, 67.0, 63.0)                               -- graveyard
    rawset(_G, "C_DeathInfo", { GetCorpseMapPosition = function(m) if m == 1441 then return { x = 0.74, y = 0.82, GetXY = function() return 0.74, 0.82 end } end end })
    T:InvalidatePlayerState()
    M.Advance(4, 0.5); tick(1)
    dump("B3")
    local c = sugg("Speed:corpse")
    check(c and c.prio == 3, "F12: ghost → the corpse run is an obligation (prio 3, red « ! » row first on the card): " .. strip(c and c.text))
    check(Cm.char.custom and Cm.char.custom.source == "corpse", "the compass aims at the corpse")
    p.ghost, M.player.ghost = false, false
    T:InvalidatePlayerState()
    M.FireEvent("PLAYER_ALIVE"); M.FireEvent("PLAYER_UNGHOST")
    goTo(1441, 74.0, 82.0)
    M.Advance(4, 0.5)
    check(not (Cm.char.custom and Cm.char.custom.source == "corpse"), "alive: the corpse point is gone")
  end
  progress(5131, 1, 10); progress(5130, 1, 5); complete(5240)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  for _, id in ipairs({ 5130, 5131, 5240 }) do turnIn(id, 2800) end
  M.player.level = 29
  M.FireEvent("PLAYER_LEVEL_UP", 29)
  M.Advance(6, 0.05); exportNow(); replan(); tick(2)
  dump("B5")

  -- ================================================================== C. zone picker « Orneval » from the flats
  T:ClearSuggestions()
  Z:SetMode("zone", 1440)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  dump("C1")
  f = first()
  if VARIANT == "full" then
    check(f and f.kind == "hs", "C1: nothing within 5 min in Orneval → the hearth is still the first leg: " .. stopsText())
    local ct = Z.CompassTarget(me())
    check(ct and ct.leg == "hs" and not ct.mapID and tostring(ct.label):find("^Pierre de foyer vers Orgrimmar"),
      "F4: the compass says « Pierre de foyer vers Orgrimmar » (no arrow toward the far quest): " .. tostring(ct and ct.label))
    Cm.Rebuild("force")
    check(Cm.Current() and Cm.Current().leg == "hs", "F4: …and it is the compass's current target")
    local l1 = find("^Arrêt 1/%d+ : Pierre de foyer")
    check(l1 and strip(l1.text):find("^Arrêt 1/%d+ : Pierre de foyer vers Orgrimmar %(15 s%)$"),
      "F16: « Arrêt 1/N : Pierre de foyer vers Orgrimmar (15 s) »: " .. strip(l1 and l1.text))
    Cm.char.custom = nil
    check(R:Follow() == "hs" and not Cm.char.custom and sugg("Route:say"),
      "F4: « suivre le trajet » with the hearth first: said, never a compass point toward the inn")
    T:Fire("SUMMARY")
    local nx = T:Summary().plan and T:Summary().plan.next or {}
    local okN = #nx >= 3 and #nx <= 12 and nx[1].k == "hs" and nx[1].label == "Pierre de foyer vers Orgrimmar"
    for i, x in ipairs(nx) do
      local e = R:Stops(64)[i]
      if not (e and x.label and (not e.step or x.label:find(P:StepLabel(e.step), 1, true) == 1)) then okN = false end
    end
    check(okN, "F5: summary.plan.next = the stops the card shows, same labels (app « Trajet » tab): "
      .. tostring(nx[1] and nx[1].label) .. " · " .. tostring(nx[2] and nx[2].label))
    local PD = T.PathDraw
    if PD and PD.dotsShown ~= nil then
      M.Advance(1, 0.2)
      check(PD.dotsShown == 0, "F19: hearth first → no minimap trail toward the inn (" .. tostring(PD.dotsShown) .. ")")
    end
  end
  check(not anySugg("^Objectif fini"), "F13: a route rebuilt for a new zone is not « Objectif fini »")
  -- F24 / F14: the zone menu (no « · 0 » while counting; then the best zones of the other continent)
  Z.counts = nil
  Z:ShowZoneMenu()
  local _, ents = Z:GetMenu()
  local zero = false
  for _, e in ipairs(ents or {}) do if tostring(e.text):find(" · 0$") then zero = true end end
  check(not zero, "F24: no « · 0 » while the quests are being counted")
  for _ = 1, 40 do if not Z._counting then break end M.Advance(0.1, 0.05) end
  Z:ShowZoneMenu(); Z:ShowZoneMenu()
  _, ents = Z:GetMenu()
  local far
  for _, e in ipairs(ents or {}) do if tostring(e.text):find("Contreforts de Hautebrande", 1, true) and tostring(e.text):find("autre continent", 1, true) then far = e end end
  check(Z.counts and far and far.fn, "F14: « Contreforts de Hautebrande (autre continent) » can be picked with the mouse")
  local menu = Z:GetMenu()
  if menu and menu.Hide then menu:Hide() end
  -- Splintertree: never « Pas finie … » / « avant de quitter » beside the route
  T:ClearSuggestions()
  goTo(1440, 73.3, 61.7)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  dump("C2")
  check(not anySugg("^Pas finie") and not anySugg("avant de quitter"), "F11: landing in Orneval: no « finis avant de quitter » beside the route")
  complete(5201); progress(5205, 1, 6); complete(5110)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  turnIn(5201, 1800); turnIn(5205, 1800)
  M.player.level = 30
  M.FireEvent("PLAYER_LEVEL_UP", 30)
  M.Advance(6, 0.05); exportNow(); replan(); tick(2)

  -- ================================================================== D. Hillsbrad (other continent), log full
  Z:SetMode("zone", 1440)
  Z:SetMode("zone", 1424)
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  dump("D1")
  if VARIANT == "full" then
    -- (ZONEFIX) a turn-in just outside the zone ON the way (≤ 45 s of detour) is in the route itself (« hors zone »);
    -- a bigger detour is only proposed
    local onWay = false
    for _, s in ipairs(P.steps or {}) do if s.q == 5111 and s.k == "turn" and s.onWay and not s._other then onWay = true end end
    check(onWay or find("^Détour rentable : rendre « Cœurs tonnants »"),
      "F21: the Undercity turn-in on the way: in the route (insertion cost ≤ 45 s) or proposed")
    check(find("^Arrêt %d+/%d+ : Rendre « Cœurs tonnants » %(hors zone%)") or not onWay, "F21: …said « hors zone » on its stop")
  end
  local n = 0
  for _, q in ipairs(M.quests) do if q.id then n = n + 1 end end
  for i = n + 1, 40 do addQuest(5900 + i, "Quête de remplissage " .. i, 27, 1413, { obj("Truc : 0/5", 0, 5, "item") }) end
  M.Advance(4, 0.05); exportNow(); replan(); tick(2)
  dump("D2")
  if VARIANT == "full" then
    local cap = tonumber(C_QuestLog and C_QuestLog.GetMaxNumQuestsCanAccept and C_QuestLog.GetMaxNumQuestsCanAccept()) or 20
    local lf = find("^Journal plein %(%d+/" .. cap .. "%)")
    check(lf, "log full: « Journal plein (n/" .. cap .. ") »")
  end
  all = R:Stops(64)
  keepN, total, _, _, trav = R.Horizon(all)
  head = find("^Trajet %d+ m?i?n? ?s?")  -- (ROUTEFIX) « Trajet 45 s » : 3 remises au même PNJ, 15 s chacune
  check(head and strip(head.text):find("^Trajet " .. R.Minutes(total) .. " ·", 1, false) and total <= R.HORIZON_S + 600,
    "F7: log full: one total within the horizon (" .. strip(head and head.text) .. ")")
  -- F15: the plan's picks will not all fit, but the log is NOT full: say it as such
  local lfWas = Z.logFull
  Z.logFull = { n = 20, max = 40, waiting = 15 }
  local found
  for _, l in ipairs(Z:HeadLines()) do if l.id == "zone:logfull" then found = l.text end end
  check(found == "Journal 20/40 : 15 quêtes à prendre attendront une place", "F15: « " .. tostring(found) .. " »")
  Z.logFull = lfWas
  -- F23: a long « mets-en de côté » list: 8 names, then « … et N autres »
  local selWas = R.selection
  local drop = {}
  for i = 1, 12 do drop[i] = { q = 7000 + i, title = "Quête lointaine " .. i, why = "trop loin" } end
  R.selection = { keep = { {}, {}, {} }, drop = drop, dropped = 0 }
  local prop
  for _, l in ipairs(R:PlanLines(P) or {}) do if strip(l.text):find("^Garde 3 quêtes, mets%-en 12 de côté") then prop = l end end
  local _, nl = tostring(prop and prop.tooltip or ""):gsub("\n", "\n")
  check(prop and nl <= 10 and tostring(prop.tooltip):find("… et 4 autres", 1, true), "F23: tooltip of 8 names + « … et 4 autres »")
  R.selection = selWas
  -- combat: nothing recomputed, no error
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  M.FireEvent("QUEST_LOG_UPDATE"); tick(2)
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED"); tick(2)
  check(#M.output == 0, "chat stays empty during play (" .. tostring(M.output[1]) .. ")")
  rawset(_G, "C_DeathInfo", oldDeath)
  M.SetPlan(nil)
  rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
end
