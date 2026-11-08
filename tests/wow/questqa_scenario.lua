-- QUESTQA — replay of real play sessions on the whole quest-tracking chain (Export → ZonePlan / Planner → Route →
-- Quests / Todo / Tracker / Abandon → panel tabs + compass), with INVARIANTS checked after every tick.
-- Level-28 Horde hunter (fake character, fake quest / NPC ids; quest titles as the French client shows them, real
-- map bounds of the client): Mille pointes (Thousand Needles, Shimmering Flats) → Orneval (Ashenvale), a log
-- with dungeon / elite / PvP quests, completed quests to turn in on another continent, a timed quest, a failed
-- escort; the stale « Arrêt 1/75 : Tue « Atrexis le Chevalier de la tombe » » app plan of the user's screenshot.
-- Run by tests/wow/test_questqa.py (modern / legacy / forever):
-- lua5.1 tests/wow/harness.lua --allow LibQuestieDB --allow QuestDB --allow NpcDB --allow ObjectDB --allow ItemDB
--   --allow TreeAIPlan --allow IsQuestFlaggedCompleted --scenario tests/wow/questqa_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local P, Z, X, R, Q = T.Planner, T.ZonePlan, T.Export, T.Route, T.modules and T.modules.Quests
  check(P and Z and X and R and Q, "Planner + ZonePlan + Export + Route + Quests loaded")
  if not (P and Z and X and R and Q) then return end
  local E = P.Engine

  -- ------------------------------------------------------------------ world: real client map bounds (yards)
  -- { name, continent, x0, y0, x1, y1 } = world X/Y at map (0,0) and (1,1), as treeaicoach/wow/data/taxi.json.gz
  local MAPS = {
    [1440] = { "Orneval", 1, 4672.9, 1700.0, 829.2, -4066.7 },
    [1441] = { "Mille pointes", 1, -3966.7, -433.3, -6900.0, -4833.3 },
    [1413] = { "Les Tarides", 1, 1612.5, 2622.9, -5143.8, -7510.4 },
    [1456] = { "Thunder Bluff", 1, -850.0, 516.7, -1545.8, -527.1 },
    [1412] = { "Mulgore", 1, 266.7, 2479.2, -3835.4, -3675.0 },
    [1442] = { "Les Serres-Rocheuses", 1, 2916.7, 3245.8, -339.6, -1637.5 },
    [1454] = { "Orgrimmar", 1, 2273.9, -3680.6, 1338.5, -5083.2 },
    [1458] = { "Fossoyeuse", 0, 1877.9, 873.2, 1237.8, -86.2 },
    [1420] = { "Clairières de Tirisfal", 0, 3837.5, 3033.3, 825.0, -1485.4 },
  }
  for id, m in pairs(MAPS) do
    M.mapRects[id] = { top = m[3], left = m[4], h = m[3] - m[5], w = m[4] - m[6], continent = m[2] }
    M.mapNames[id] = m[1]
  end
  T.Geo.rects = {}
  Z.ResetMaps()
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 28
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  local function goTo(map, x, y)
    M.pos = { mapID = map, x = x / 100, y = y / 100 }
    M.player.zone = MAPS[map][1]
    Z._lastAllowed = nil
    M.FireEvent("ZONE_CHANGED_NEW_AREA")
  end
  goTo(1440, 73, 61)                                      -- Orneval, Poste de Bois-Brisé

  -- ------------------------------------------------------------------ the quest log (20/20, like the screenshots)
  local DUNGEON, PVP, ELITE = 81, 41, 1
  local function obj(text, have, need, typ) return { text = text, have = have, need = need, done = have >= need, type = typ or "monster" } end
  local LOG = {
    { header = "Mille pointes" },
    { id = 5101, title = "Le cavalier céleste", level = 29, map = 1441, objectives = { obj("Œuf de wyverne de Haut-perchoir : 0/10", 0, 10, "item") } },
    { id = 5102, title = "Hypercapaciteur bidulotron", level = 30, tag = ELITE, elite = true, map = 1441,
      objectives = { obj("Hypercapaciteur bidulotron : 0/1", 0, 1, "item") } },
    { id = 5104, title = "L'œuf étrange", level = 26, complete = true, map = 1441, objectives = {} },
    { id = 5105, title = "ON RECHERCHE : Arnak Sinistre", level = 29, complete = true, map = 1441, objectives = {} },
    { id = 5106, title = "Pacifier les centaures", level = 25, complete = true, map = 1441, objectives = {} },
    { id = 5108, title = "Interruption des hurlements", level = 30, complete = true, map = 1441, objectives = {} },
    { id = 5109, title = "Les fournitures de Librevent", level = 30, complete = true, map = 1441, objectives = {} },
    { header = "Thunder Bluff" },
    { id = 5103, title = "Broie-l'acier", level = 30, complete = true, map = 1456, objectives = {} },
    { id = 5110, title = "La flamme sacrée", level = 25, map = 1440, objectives = { obj("Fiole d'eau forte remplie : 0/1", 0, 1, "item") } },
    { header = "Fossoyeuse" },
    { id = 5111, title = "Cœurs tonnants", level = 33, tag = ELITE, elite = true, complete = true, map = 1458, objectives = {} },
    { header = "Profondeurs de Brassenoire" },
    { id = 5112, title = "Source de pouvoir", level = 33, tag = DUNGEON, map = 1440, objectives = { obj("Noyau de sentinelle fissuré : 0/6", 0, 6, "item") } },
    { header = "Monastère écarlate" },
    { id = 5116, title = "Le Chevalier de la tombe", level = 33, tag = DUNGEON, map = 1420,
      objectives = { obj("Atrexis le Chevalier de la tombe : 0/1", 0, 1) } },
    { header = "Kraal de Tranchebauge" },
    { id = 5117, title = "Racines de Feuillebleue", level = 26, tag = DUNGEON, map = 1413, objectives = { obj("Racines de Feuillebleue : 0/6", 0, 6, "item") } },
    { header = "Bassin d'Arathi" },
    { id = 5118, title = "La conquête du bassin d'Arathi", level = 29, tag = PVP, objectives = { obj("Marque d'honneur : 0/3", 0, 3, "item") } },
    { header = "Les Tarides" },
    { id = 5107, title = "Brouet de rat", level = 23, map = 1413, objectives = { obj("Rat fouisseur : 0/8", 0, 8, "item") } },
    { id = 5119, title = "Accusé à tort, corrigé à raison", level = 23, tag = ELITE, elite = true, map = 1413,
      objectives = { obj("Soldat envahissant tué : 1/2", 1, 2) } },
    { id = 5120, title = "Des armes de choix", level = 24, map = 1413, objectives = { obj("Poignard de Tranchecrin : 0/1", 0, 1, "item") } },
    { id = 5113, title = "L'élixir d'agonie", level = 30, map = 1413, objectives = { obj("Sang de troll inférieur : 0/1", 0, 1, "item") } },
    { id = 5121, title = "Une trahison de l'intérieur", level = 25, map = 1413, objectives = { obj("Crâne de Kuz : 0/1", 0, 1, "item") } },
    { id = 5122, title = "La réclamation de Gann", level = 23, map = 1413, objectives = { obj("Excavateur de Bael'dun tué : 0/15", 0, 15) } },
  }
  local function setLog(list)
    M.quests = {}
    for _, q in ipairs(list) do M.quests[#M.quests + 1] = q end
  end
  setLog(LOG)
  local TAG = {}                                          -- truth table: what the game says about every quest
  for _, q in ipairs(LOG) do if q.id then TAG[q.id] = q.tag end end
  M.questTags = { [5301] = DUNGEON, [5302] = ELITE }      -- not-yet-taken quests (client tag cache)
  for id, t in pairs(M.questTags) do TAG[id] = t end
  M.SetCompleted({})
  P.char.turned = {}
  M.SetPlan(nil)

  -- ------------------------------------------------------------------ fake QuestieDB (contract 4), fake ids
  -- areas: 331 Orneval, 400 Mille pointes, 17 Tarides, 1638 Thunder Bluff, 1497 Fossoyeuse, 85 Tirisfal,
  -- dungeons 719 Brassenoire, 796 Monastère, 491 Tranchebauge
  local AREAS = { [331] = 1440, [400] = 1441, [17] = 1413, [1638] = 1456, [1497] = 1458, [85] = 1420, [215] = 1412 }
  local function npc(name, lv, area, x, y, rank) return { name = name, minLevel = lv, maxLevel = lv, rank = rank or 0, spawns = { [area] = { { x, y } } } } end
  local function quest(name, ql, rl, zone, giver, ender, kills, items)
    return { name = name, questLevel = ql, requiredLevel = rl, zoneOrSort = zone, startedBy = { { giver } }, finishedBy = { { ender } },
      objectives = { kills and { kills } or nil, nil, items and { items } or nil } }
  end
  local lib = M.InstallQuestieDB({
    areaToMap = AREAS,
    xp = { [5110] = 1850, [5201] = 1750, [5202] = 1750, [5203] = 1800, [5204] = 1750, [5205] = 1650, [5101] = 2650,
      [5104] = 1300, [5105] = 2650, [5106] = 1450, [5108] = 2800, [5109] = 2800, [5103] = 2800, [5111] = 3950 },
    quests = {
      -- in the log
      [5101] = quest("Le cavalier céleste", 29, 24, 400, 610, 610, nil, { 8101 }),
      [5102] = quest("Hypercapaciteur bidulotron", 30, 25, 400, 611, 611, nil, { 8102 }),
      [5103] = quest("Broie-l'acier", 30, 25, 1638, 620, 620),
      [5104] = quest("L'œuf étrange", 26, 22, 400, 612, 612),
      [5105] = quest("ON RECHERCHE : Arnak Sinistre", 29, 25, 400, 612, 612),
      [5106] = quest("Pacifier les centaures", 25, 21, 400, 612, 612),
      [5108] = quest("Interruption des hurlements", 30, 25, 400, 613, 613),
      [5109] = quest("Les fournitures de Librevent", 30, 25, 400, 613, 613),
      [5110] = quest("La flamme sacrée", 25, 20, 1638, 620, 620, nil, { 8110 }),
      [5111] = quest("Cœurs tonnants", 33, 28, 1497, 630, 630),
      [5112] = quest("Source de pouvoir", 33, 25, 719, 640, 640, nil, { 8112 }),
      [5116] = quest("Le Chevalier de la tombe", 33, 28, 796, 631, 631, { 7116 }),
      [5117] = quest("Racines de Feuillebleue", 26, 20, 491, 621, 621, nil, { 8117 }),
      [5118] = quest("La conquête du bassin d'Arathi", 29, 25, 0, 650, 650, nil, { 8118 }),
      [5107] = quest("Brouet de rat", 23, 18, 17, 622, 622, nil, { 8107 }),
      [5119] = quest("Accusé à tort, corrigé à raison", 23, 18, 17, 622, 622, { 7119 }),
      [5120] = quest("Des armes de choix", 24, 20, 17, 622, 622, nil, { 8120 }),
      [5113] = quest("L'élixir d'agonie", 30, 25, 17, 623, 623, nil, { 8113 }),
      [5121] = quest("Une trahison de l'intérieur", 25, 20, 17, 622, 622, nil, { 8121 }),
      [5122] = quest("La réclamation de Gann", 23, 18, 17, 622, 622, { 7122 }),
      -- to take in Orneval (Bois-Brisé)
      [5201] = quest("La patte d'Ursangous", 24, 20, 331, 700, 700, { 7201 }),
      [5202] = quest("Entre un roc et un Crin-de-Chardon", 24, 20, 331, 700, 700, { 7202 }),
      [5203] = quest("Charme troll", 24, 20, 331, 701, 701, nil, { 8203 }),
      [5204] = quest("Liberté pour Ruul", 24, 20, 331, 701, 701, { 7204 }),
      [5205] = quest("Les estafettes d'Ashenvale", 24, 20, 331, 700, 702, { 7205 }),
      [5301] = quest("Les profondeurs de Brassenoire", 27, 21, 331, 703, 703, { 7301 }),       -- dungeon (tag)
      [5302] = quest("Le seigneur des Furbolgs", 27, 22, 331, 701, 701, { 7302 }),            -- elite (tag)
    },
    npcs = {
      [610] = npc("Cavalier céleste", 30, 400, 46, 50), [611] = npc("Pozzik", 30, 400, 80, 76),
      [612] = npc("Cerf-de-vent", 30, 400, 46, 51), [613] = npc("Nyrill", 30, 400, 45, 50),
      [620] = npc("Sage de Thunder Bluff", 50, 1638, 30, 30), [630] = npc("Apothicaire", 50, 1497, 50, 60),
      [631] = npc("Chasseur écarlate", 40, 85, 80, 30), [640] = npc("Argent", 30, 331, 15, 15),
      [621] = npc("Mangletooth", 30, 17, 44, 59), [622] = npc("Rocheyeux", 30, 17, 45, 58), [623] = npc("Apothicaire Helbrim", 30, 17, 51, 30),
      [650] = npc("Recruteur", 50, 1638, 60, 55),
      [700] = npc("Kuray'bin", 30, 331, 73, 61), [701] = npc("Mastok", 30, 331, 73, 60), [702] = npc("Pyrotechnicien", 30, 331, 70, 70),
      [703] = npc("Je'neu Sancrea", 30, 331, 18, 21),
      [7116] = npc("Atrexis le Chevalier de la tombe", 33, 85, 85, 33, 3),
      [7119] = npc("Soldat envahissant", 25, 17, 49, 84, 1), [7122] = npc("Excavateur de Bael'dun", 24, 17, 48, 85),
      [7201] = npc("Ursangous", 25, 331, 42, 66), [7202] = npc("Crin-de-Chardon", 24, 331, 79, 54), [7204] = npc("Ruul", 25, 331, 76, 66),
      [7205] = npc("Estafette", 25, 331, 62, 52), [7301] = npc("Garde de Brassenoire", 24, 331, 14, 14, 1),
      [7302] = npc("Seigneur Furbolg", 27, 331, 56, 33, 1),
      [7107] = npc("Rat fouisseur", 22, 17, 46, 40), [7113] = npc("Troll inférieur", 24, 17, 60, 15),
      [7120] = npc("Tranchecrin", 23, 17, 43, 82), [7121] = npc("Kuz", 25, 17, 42, 86),
      [7101] = npc("Wyverne", 29, 400, 47, 61), [7110] = npc("Puits de lune", 1, 331, 49, 69),
      [7203] = npc("Troll Sombrebois", 25, 331, 66, 77), [7117] = npc("Fouisseur", 26, 491, -1, -1),
    },
    items = {
      [8101] = { name = "Œuf de wyverne", npcDrops = { 7101 } }, [8110] = { name = "Fiole d'eau forte remplie", npcDrops = { 7110 } },
      [8107] = { name = "Rat fouisseur", npcDrops = { 7107 } }, [8113] = { name = "Sang de troll", npcDrops = { 7113 } },
      [8120] = { name = "Poignard", npcDrops = { 7120 } }, [8121] = { name = "Crâne de Kuz", npcDrops = { 7121 } },
      [8203] = { name = "Charme", npcDrops = { 7203 } }, [8117] = { name = "Racines", npcDrops = { 7117 } },
    },
  })
  -- Questie's ZoneDB: the instances (the dungeon quests are known as such without the game tag, legacy too)
  lib.Support.Get("ZoneDB").private.dungeons = { [719] = { "Brassenoire" }, [796] = { "Monastère" }, [491] = { "Tranchebauge" } }

  -- ------------------------------------------------------------------ helpers
  local function exportNow() X:Rebuild(); M.Advance(8, 0.1) end
  local function replan() P:Refresh(true); M.Advance(5, 0.05) end
  local function tick(n) for _ = 1, n or 1 do M.Advance(3, 0.5) end end
  local function inLog()
    local set = {}
    for _, q in ipairs(M.quests) do if q.id then set[q.id] = q end end
    return set
  end
  local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  local function lines(tab)
    local ok, l = pcall(T.GetTabLines, T, tab)
    return ok and l or {}
  end
  local function all()
    local out = {}
    for _, tab in ipairs({ "quests", "now" }) do for _, l in ipairs(lines(tab)) do out[#out + 1] = l end end
    if P.lines then for _, l in ipairs(P.lines) do out[#out + 1] = l end end
    return out
  end
  local function find(pat)
    for _, l in ipairs(all()) do if strip(l.text):find(pat) then return l end end
  end
  local function dump()
    local t = {}
    for _, l in ipairs(lines("quests")) do t[#t + 1] = "[" .. tostring(l.source) .. "] " .. strip(l.text) end
    return table.concat(t, " || ")
  end
  local function order()
    local t = {}
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if not s._done and not s._other then t[#t + 1] = Z.Engine.StepKey(s) end
    end
    return table.concat(t, ";")
  end
  local LABELS = { "OK", "On va galérer", "Trop long", "Pas faisable", "Changez de zone", "Étape", "Arrêt" }
  local function groupN() return #M.group + 1 end

  -- ------------------------------------------------------------------ THE INVARIANTS (every tick)
  local nInv = 0
  local function inv(tag)
    nInv = nInv + 1
    local log = inLog()
    local bad = {}
    local function no(cond, what) if cond then bad[#bad + 1] = what end end
    for _, l in ipairs(all()) do
      local t = strip(l.text)
      -- 1. no repeated phrase (« Changez de zone : Changez de zone : … », « Étape : Étape : »)
      for _, lab in ipairs(LABELS) do no(t:find(lab .. " : " .. lab, 1, true), "repeated label: " .. t) end
      local head = t:match("^(.-) : ")
      no(head and #head >= 6 and t:find(head .. " : " .. head, 1, true), "repeated phrase: " .. t)
      -- 2. no absurd number: percentages ≤ 100, XP/h only plausible
      for n in t:gmatch("(%d+) %%") do no(tonumber(n) > 100, "absurd percentage: " .. t) end
      for n in t:gmatch("(%d[%d ]*) XP/h") do no((tonumber((n:gsub(" ", ""))) or 0) > 150000, "absurd XP/h: " .. t) end
      -- 3. route length: one zone plan, never 75 stops / 300 min
      local mins = t:match("^Trajet (%d+) min")
      no(mins and tonumber(mins) > 95, "route longer than the horizon: " .. t)
      no(t:match("^Trajet %d+ h"), "route in hours: " .. t)
      local k, nn = t:match("^Arrêt (%d+)/(%d+)")
      no(nn and tonumber(nn) > R.HORIZON_STOPS, "too many stops: " .. t)
      no(k and tonumber(k) > tonumber(nn), "stop number > count: " .. t)
      local eta = t:match("%(≈ (%d+) min%)")
      no(eta and tonumber(eta) > 90, "ETA beyond the horizon: " .. t)
      -- 4. the log's « Étape : rendre … » only for a complete quest of the log (route turn-ins: see 5.)
      local rt = t:match("Étape : rendre « (.-) »")
      if rt then
        local okT = false
        for _, q in pairs(log) do if q.title:sub(1, #rt:gsub("…$", "")) == rt:gsub("…$", "") and q.complete then okT = true end end
        no(not okT, "turn-in line for a quest not complete / not in the log: " .. t)
      end
    end
    -- 5. route steps: only quests of the log (or picked earlier in the route), solo rules
    local picked, seenObj = {}, {}
    local steps = P.steps or {}
    for i = P.cursor or 1, #steps do
      local s = steps[i]
      if not s._done and not s._other and s.q then
        local q = log[s.q]
        if s.k == "pick" then no(q ~= nil, "pick of a quest already in the log: " .. s.q) picked[s.q] = true end
        if s.k == "obj" then no(not q and not picked[s.q], "objective of a quest not in the log: " .. s.q); seenObj[s.q] = true end
        if s.k == "turn" then
          no(not (q and q.complete) and not picked[s.q] and not seenObj[s.q], "turn-in of a quest not complete: " .. s.q)
        end
        no(q and q.failed, "failed quest in the route: " .. s.q)
        local tg = TAG[s.q]
        local ready = q and q.complete and s.k == "turn"
        if not ready then
          no(tg == PVP, "PvP quest in the route: " .. s.q)
          no(tg == DUNGEON and not (Z.db.dungeons and groupN() >= 3), "dungeon quest in the solo route: " .. s.q)
          no(tg == ELITE and not (Z.db.elites or groupN() >= 3), "elite quest in the solo route: " .. s.q)
        end
      end
    end
    -- 5b. a full log never gets a « Prendre » before a turn-in frees a slot
    local nlog = 0
    for _ in pairs(log) do nlog = nlog + 1 end
    -- (FIDELITY) the game's own cap (Forever / Camelot: 40, Classic Era: 20), as the addon reads it
    local QLc = rawget(_G, "C_QuestLog")
    local cap = tonumber(QLc and QLc.GetMaxNumQuestsCanAccept and QLc.GetMaxNumQuestsCanAccept()) or 20
    if nlog >= cap then
      for i = P.cursor or 1, #steps do
        local s = steps[i]
        if not s._done and not s._other then
          if s.k == "turn" then break end
          no(s.k == "pick", "pick-up with a full log (" .. nlog .. "/" .. cap .. "): " .. tostring(s.q))
        end
      end
    end
    -- 6. the current stop is in the work area (or a detour the player added), the compass aims at it
    local cur = steps[P.cursor or 0]
    if cur and not cur._done and not cur._other and cur.map then
      no(not cur.out and not Z:InZone(cur.map), "current stop outside the work area: " .. tostring(cur.map))
      local ct = Z.CompassTarget({ mapID = M.pos.mapID, x = M.pos.x, y = M.pos.y })
      no(not (R.char and R.char.paused) and ct and ct.questID ~= cur.q, "compass not on the current stop")
    end
    -- 7. one « step » on screen: the route's current stop; the log's « Étape : … » stays silent then
    local active = P.GetActiveSteps and P:GetActiveSteps(1)
    if active then
      for _, l in ipairs(lines("quests")) do
        no(l.source == "Quests" and strip(l.text):find("^Étape :"), "« Étape » line beside the route: " .. strip(l.text))
        no(strip(l.text):find("^Ensuite :"), "second step list (« Ensuite »): " .. strip(l.text))
      end
      local card
      for _, l in ipairs(P.lines or {}) do if l.id == "plan:step" then card = strip(l.text) end end
      local first = R:Stops(1)[1]
      if card and first and first.step then
        no(not card:find(P:StepLabel(first.step):sub(1, 20), 1, true), "card ≠ current stop: " .. card)
      end
    end
    -- 8. « Changez de zone » never while a quest of the log can be done right here
    local zl = find("^Changez de zone")
    if zl then
      local here = false
      for _, q in pairs(log) do
        if q.map == M.pos.mapID and not q.complete and not q.failed and not TAG[q.id] then here = true end
      end
      no(here, "« Changez de zone » with a quest to do here: " .. strip(zl.text))
    end
    check(#bad == 0, "[" .. tag .. "] invariants" .. (#bad > 0 and (" — " .. table.concat(bad, " | ") .. " — " .. dump()) or ""))
    if os.getenv("QUESTQA_DUMP") then        -- review aid: what the player sees at this tick
      io.stderr:write("\n=== " .. tag .. "\n")
      for _, tab in ipairs({ "now", "quests" }) do
        for _, l in ipairs(lines(tab)) do io.stderr:write(("  %-6s [%s] %s\n"):format(tab, tostring(l.source), strip(l.text))) end
      end
    end
  end

  -- ================================================================== A. the screenshot: stale app plan (Mille pointes)
  exportNow()
  local key = T:CharKey()
  local big = {}
  big[1] = { k = "obj", q = 5116, o = 1, map = 1420, x = 85, y = 33, label = "Tue « Atrexis le Chevalier de la tombe »", eta = 1 }
  big[2] = { k = "fly", map = 1441, x = 45, y = 49, label = "Vol pour Orgrimmar", eta = 3 }
  big[3] = { k = "zep", map = 1454, x = 50, y = 12, label = "Zeppelin pour Clairières de Tirisfal", eta = 8 }
  for i = 4, 75 do
    local q = ({ 5101, 5104, 5105, 5106, 5108, 5109, 5107, 5113, 5120 })[(i % 9) + 1]
    big[i] = { k = (i % 2 == 0) and "turn" or "obj", q = q, o = 1, map = (i % 3 == 0) and 1441 or 1413, x = 20 + i % 60, y = 30 + i % 40, eta = 4 * i }
  end
  M.SetPlan({ v = 1, made = os.time() - 3600, char = key, zone = 1441, routes = {
    { id = "xph", title = "Le plus rapide", xph = 102414, minutes = 307, verdict = "zone",
      reasons = { "Changez de zone : ailleurs : +575 % d'XP/h après 5 min de trajet → Fossoyeuse" }, steps = big } } })
  replan()
  check(P.src == "auto" and P.planError and P.planError:find("Mille pointes", 1, true),
    "app plan made in Mille pointes is ignored in Orneval (" .. tostring(P.planError) .. ")")
  check(not find("Atrexis") and not find("^Arrêt %d+/75") and not find("575"), "no « Arrêt 1/75 : Tue Atrexis », no +575 %")
  check(find("^Plan de TreeAI Coach ignoré : fait pour Mille pointes"), "said once, in plain words: " .. dump())
  local qs = {}
  for _, s in ipairs(P.steps or {}) do if s.q and not s._other then qs[s.q] = true end end
  check(qs[5110], "« La flamme sacrée » (Lac Mystral, Orneval) is in the in-game route")
  check(not qs[5112] and not qs[5116] and not qs[5117] and not qs[5118] and not qs[5119] and not qs[5102],
    "no dungeon / PvP / elite quest in the solo route")
  inv("A stale plan")
  -- summary for the app: the plan is marked stale with the reason
  T:Fire("SUMMARY")
  local sp = T:Summary().plan
  check(sp and sp.stale and tostring(sp.staleWhy):find("Mille pointes", 1, true), "summary.plan: stale + why (the app recomputes)")

  -- ================================================================== B. a FRESH app plan for Orneval, but too long and with dungeon / PvP / elite steps
  local logIds = {}
  for _, q in ipairs(M.quests) do if q.id then logIds[#logIds + 1] = q.id end end
  local long = {
    { k = "obj", q = 5112, o = 1, map = 1440, x = 15, y = 15, label = "Ramasse « Noyau de sentinelle fissuré »", eta = 5 },
    { k = "obj", q = 5118, o = 1, map = 1440, x = 60, y = 60, label = "Ramasse « Marque d'honneur »", eta = 6 },
    { k = "obj", q = 5119, o = 1, map = 1413, x = 49, y = 84, label = "Tue « Soldat envahissant »", eta = 7 },
    { k = "obj", q = 5110, o = 1, map = 1440, x = 49, y = 69, label = "Fiole d'eau forte remplie", eta = 10 },
  }
  for i = 1, 40 do
    long[#long + 1] = { k = "pick", q = 6000 + i, map = 1440, x = (i * 37) % 90 + 5, y = (i * 53) % 90 + 5, label = "Prendre « Quête " .. i .. " »", eta = 10 + 6 * i }
  end
  M.SetPlan({ v = 1, made = os.time() - 600, char = key, zone = 1440, level = 28, log = logIds, routes = {
    { id = "xph", title = "Le plus rapide", xph = 21000, minutes = 250, verdict = "long",
      reasons = { "Trop long : Trop long : ~250 min pour 90 min de jeu prévues" }, steps = long },
    { id = "safe", title = "Sans galère", xph = 15000, minutes = 40, verdict = "ok", reasons = {}, steps = { long[4] } } } })
  replan()
  check(P.src == "file" and not P.planError, "fresh app plan (same log, same zone) is used (" .. tostring(P.planError) .. ")")
  local masked = {}
  for _, s in ipairs(P.steps) do if s._zone then masked[s.q or -1] = s._why end end
  check(masked[5112] == "donjon" and masked[5118] == "JcJ" and masked[5119] == "élite", "app plan: dungeon / PvP / elite steps masked (same rules)")
  check(find("^Trop long : ~250 min"), "verdict said once: « Trop long : ~250 min … »")
  local head = find("^Trajet %d+ min")
  check(head and tostring(head.tooltip):find("arrêts? de plus", 1, false), "head: the rest is announced in the tooltip (" .. tostring(head and head.tooltip) .. ")")
  local zone = find("^Zone : ")
  check(zone and tostring(zone.tooltip):find("Hors trajet : 1 de donjon, 1 élite, 1 JcJ", 1, true), "zone tooltip says what is left out: " .. tostring(zone and zone.tooltip))
  inv("B long app plan")
  -- the player takes 3 quests not planned: still fine; 10 new quests → the plan no longer matches the log
  local function addQuest(id, title, lvl, map, objs)
    local q = { id = id, title = title, level = lvl, map = map, objectives = objs or { obj("Furbolg tué : 0/8", 0, 8) } }
    M.quests[#M.quests + 1] = q
    M.FireEvent("QUEST_ACCEPTED", #M.quests, id)
    return q
  end
  check(E.PlanFresh({ char = key, made = os.time(), log = { 1, 2, 3 }, zone = 1440 },
    { key = key, now = os.time(), level = 28, log = { [1] = true, [2] = true, [3] = true, [4] = true, [5] = true, [6] = true, [7] = true } }) == false,
    "PlanFresh: 4 quests taken since the plan (> half the log) → stale")
  check(E.PlanFresh({ char = key, made = os.time() - 13 * 3600, zone = 1440 }, { key = key, now = os.time(), level = 28 }) == false,
    "PlanFresh: no fingerprint and > 12 h → stale")
  check(E.PlanFresh({ char = key, made = os.time() - 3600, level = 26 }, { key = key, now = os.time(), level = 28 }) == false,
    "PlanFresh: made 2 levels ago → stale")
  check(E.PlanFresh({ char = key, made = os.time() - 3600, level = 28, log = { 5110 } }, { key = key, now = os.time(), level = 28, log = { [5110] = true } }),
    "PlanFresh: same log, same level → fresh")

  -- ================================================================== C. play in Orneval on the in-game route
  M.SetPlan(nil)
  replan()
  check(P.src == "auto", "back to the in-game route")
  inv("C start")
  local o0 = order()
  for i = 1, 6 do tick(1) inv("C idle " .. i) end
  P:Refresh(true); M.Advance(5, 0.05)
  check(order() == o0, "stability: nothing changed → same order after a replan (no flapping)")
  -- small moves: hysteresis keeps the order
  M.pos = { mapID = 1440, x = 0.72, y = 0.62 }
  Z.rev = (Z.rev or 0) + 1
  P:Refresh(true); M.Advance(5, 0.05)
  check(order() == o0, "stability: a few steps away → same order")
  inv("C moved")
  -- make room the way a player does (abandon what will not be done soon), then take the Bois-Brisé quests
  local function drop(id)
    for i, q in ipairs(M.quests) do if q.id == id then table.remove(M.quests, i) break end end
    M.FireEvent("QUEST_REMOVED", id)
  end
  drop(5118); drop(5116); drop(5117)
  M.Advance(4, 0.05)
  inv("C room made")
  local u = addQuest(5201, "La patte d'Ursangous", 24, 1440, { obj("Ursangous tué : 0/1", 0, 1) })
  addQuest(5202, "Entre un roc et un Crin-de-Chardon", 24, 1440, { obj("Crin-de-Chardon tué : 0/1", 0, 1) })
  M.Advance(6, 0.05)
  exportNow(); replan()
  qs = {}
  for _, s in ipairs(P.steps or {}) do if s.q and not s._other then qs[s.q] = true end end
  check(qs[5201] and qs[5202], "new quests are in the route at once")
  inv("C accepted")
  -- the vial is filled: objective done → the turn-in is in Thunder Bluff (other zone): only a proposal
  for _, q in ipairs(M.quests) do if q.id == 5110 then q.complete = true; q.objectives[1] = obj("Fiole d'eau forte remplie : 1/1", 1, 1, "item") end end
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(6, 0.05)
  replan()
  inv("C vial filled")
  local cur = P.steps and P.steps[P.cursor]
  check(not (cur and cur.q == 5110 and cur.k == "turn" and not cur.out), "the far turn-in (Thunder Bluff) is not the current stop of the Orneval route")
  -- Ursangous killed, quest done, turned in
  u.objectives[1] = obj("Ursangous tué : 1/1", 1, 1)
  u.complete = true
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(6, 0.05)
  inv("C ursangous done")
  for i, q in ipairs(M.quests) do if q.id == 5201 then table.remove(M.quests, i) break end end
  M.SetCompleted({ [5201] = true })
  M.FireEvent("QUEST_TURNED_IN", 5201, 1750, 0)
  M.FireEvent("QUEST_REMOVED", 5201)
  M.Advance(6, 0.05)
  for _, s in ipairs(P.steps or {}) do check(s.q ~= 5201 or s._done or s._other, "turned-in quest leaves the route") end
  inv("C turned in")
  -- level up
  M.player.level = 29
  M.FireEvent("PLAYER_LEVEL_UP", 29)
  M.Advance(6, 0.05)
  inv("C level 29")
  M.player.level = 28

  -- ================================================================== D. the log alone (no route): « Étape » makes sense here
  local a = Q.Analyse(Q.Scan(), "Orneval")
  local step = Q.NextStep(a)
  check(step and not step:find("Fossoyeuse", 1, true) and not step:find("aller rendre", 1, true),
    "« Étape » in Orneval is not « aller rendre 1 quête en Fossoyeuse »: " .. tostring(step))
  local inLater = {}
  for _, q in ipairs(a.later) do inLater[q.id] = true end
  check(inLater[5112] and inLater[5119], "dungeon / elite quests are « later », never the step")
  goTo(1441, 46, 50)                                      -- Mille pointes, Poste de Librevent
  a = Q.Analyse(Q.Scan(), "Mille pointes")
  step = Q.NextStep(a)
  check(step and step:find("^Étape : rendre «") and step:find(" ici"), "in Mille pointes with turn-ins here: « Étape : rendre … ici »: " .. tostring(step))
  exportNow(); replan()
  local first = R:Stops(1)[1]
  check(first and first.step and first.step.k == "turn" and first.step.map == 1441, "route in Mille pointes starts with the turn-ins at hand")
  check(not (P.route and P.route.verdict == "zone"), "no « Changez de zone » with 5 turn-ins here")
  inv("D Mille pointes")

  -- ================================================================== E. group of 4: elites come back, dungeons only when chosen
  M.group = { { name = "Amie", class = "PRIEST", level = 28, online = true }, { name = "Ami", class = "WARRIOR", level = 29, online = true },
    { name = "Copain", class = "MAGE", level = 28, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  exportNow(); replan()
  qs = {}
  for _, s in ipairs(P.steps or {}) do if s.q and not s._other then qs[s.q] = true end end
  check(qs[5102], "in a group of 4 the elite « Hypercapaciteur » is planned")
  inv("E group")
  M.group = {}
  M.FireEvent("GROUP_ROSTER_UPDATE")
  exportNow(); replan()
  inv("E solo again")

  -- ================================================================== F. timed quest, failed escort, log full
  goTo(1440, 73, 61)
  drop(5112); drop(5119)
  local timed = addQuest(5203, "Charme troll", 24, 1440, { obj("Charme : 0/1", 0, 1, "item") })
  timed.timer = { 900, 700 }                                -- 200 s left
  local esc = addQuest(5204, "Liberté pour Ruul", 24, 1440, { obj("Ruul escorté : 0/1", 0, 1, "event") })
  esc.failed = true
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(6, 0.05)
  exportNow(); replan()
  local ch = find("^Chrono : « Charme troll » à finir avant %d%d:%d%d")
  check(ch and ch.prio == 3, "timed quest: one line with the end time, urgent under 5 min: " .. dump())
  check(find("^« Liberté pour Ruul » a échoué : abandonne%-la puis reprends%-la"), "failed escort: said once, what to do")
  for _, s in ipairs(P.steps or {}) do check(s.q ~= 5204 or s._other or s._done, "failed quest never planned") end
  inv("F timer + failed")
  -- Abandon (bêta) : « Retire » only on facts
  local Ab = T.modules.Abandon
  if Ab and Ab.Update then
    pcall(Ab.Update, true)
    for _, l in ipairs(Ab.lines or {}) do
      local t = strip(l.text)
      if t:find("^Retire «") then
        local title = t:match("^Retire « (.-) »")
        local ok = false
        for _, q in ipairs(M.quests) do
          if q.title and title and q.title:sub(1, #title:gsub("…$", "")) == title:gsub("…$", "") then
            ok = (q.level or 99) <= 21 or #M.quests >= 20 or q.failed or false
          end
        end
        check(ok or #M.quests >= 20, "« Retire » only on a fact (grey / log full / failed): " .. t)
      end
    end
  end

  -- ================================================================== G. combat: nothing recomputed, lines stay coherent
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  M.FireEvent("QUEST_LOG_UPDATE")
  tick(2)
  inv("G combat")
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
  tick(2)
  inv("G after combat")
  check(nInv >= 20, "invariants checked on every tick (" .. nInv .. ")")
  M.SetPlan(nil)
  rawset(_G, "UnitFactionGroup", function() return "Alliance", "Alliance" end)
end
