-- Scenario for Modules/ZonePlan.lua (agent ZONEPLAN), run by tests/wow/test_zoneplan.py (modern + forever).
-- « Zone de travail » + always-prepared in-game route: zone restriction, no other continent unless chosen, replan on
-- turn-in, hysteresis, ignore list (right click), app-plan masking, works without the app plan and without QuestieDB.
-- Fake quest log + fake QuestieDB (contract 4), fake names only.
-- lua5.1 tests/wow/harness.lua --allow LibQuestieDB --allow QuestDB --allow NpcDB --allow ObjectDB --allow ItemDB
--   --allow TreeAIPlan --allow IsQuestFlaggedCompleted --scenario tests/wow/zoneplan_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Z, P, X, R = T.ZonePlan, T.Planner, T.Export, T.Route
  check(Z and P and X and R, "ZonePlan + Planner + Export + Route loaded")
  if not (Z and P and X and R) then return end
  local N = Z.Engine

  -- ------------------------------------------------------------ pure engine
  local function lg(a, b) if not (a and b and a.x and b.x) then return 0 end return math.abs(a.x - b.x) * 10 end
  local function dist(a, b) return math.abs(a.x - b.x) * 10 end
  local reqs = {
    { q = 1, steps = { { k = "pick", q = 1, x = 10, svc = 15, value = 100 }, { k = "obj", q = 1, o = 1, x = 50, svc = 60, value = 500 },
      { k = "turn", q = 1, x = 12, svc = 15, value = 1000 } } },
    { q = 2, steps = { { k = "obj", q = 2, o = 1, x = 55, svc = 60, value = 500 }, { k = "turn", q = 2, x = 90, svc = 15, value = 1000 } } },
    { q = 3, steps = { { k = "obj", q = 3, o = 1, x = 30, svc = 60, value = 500 }, { k = "turn", q = 3, x = 31, svc = 15, value = 1000 } } },
  }
  local nodes = N.Nodes(reqs, dist)
  local area
  for _, nd in ipairs(nodes) do if #nd.steps == 2 then area = nd end end
  check(#nodes == 6 and area and area.area and area.qs[1] == 2 and area.qs[2] == 2, "objectives 1 & 2 (5 yd apart) = one stop")
  local ord = N.Greedy(nodes, { x = 0 }, lg, {})
  check(#ord == 6 and N.Valid(nodes, ord), "greedy covers every stop, precedences kept")
  local D = N.Matrix(nodes, ord, { x = 0 }, lg)
  local c0 = N.Cost(D, ord)
  N.Improve(nodes, ord, D, nil, 3)
  check(N.Valid(nodes, ord) and N.Cost(D, ord) <= c0, "2-opt / Or-opt: never worse, still valid (" .. c0 .. " → " .. N.Cost(D, ord) .. ")")
  local turn1, pick1
  for i, nd in ipairs(nodes) do
    if nd.key == "turn:1:" then turn1 = i end
    if nd.key == "pick:1:" then pick1 = i end
  end
  check(not N.Valid(nodes, { turn1, pick1 }), "turn-in before pick-up = invalid")
  -- hysteresis: ≥ 10 % and ≥ 1 min of travel, else the order in place stays
  check(N.Choose(950, 1000) == "old" and N.Choose(890, 1000) == "new" and N.Choose(80, 100) == "old" and N.Choose(1, nil) == "new",
    "hysteresis 10 % / 60 s")
  -- live hysteresis on a line: old order 11 → 10 → 100 costs 20 s more than 10 → 11 → 100: kept
  local hn = N.Nodes({ { q = 7, steps = { { k = "obj", q = 7, o = 1, x = 10, svc = 0, value = 1 } } },
    { q = 8, steps = { { k = "obj", q = 8, o = 1, x = 11, svc = 0, value = 1 } } },
    { q = 9, steps = { { k = "obj", q = 9, o = 1, x = 100, svc = 0, value = 1 } } } }, function() return 999 end)
  local ho = N.Greedy(hn, { x = 0 }, lg, {})
  local HD = N.Matrix(hn, ho, { x = 0 }, lg)
  N.Improve(hn, ho, HD)
  local hold = N.Patch(hn, ho, { "obj:8:1", "obj:7:1", "obj:9:1" }, HD)
  check(N.Cost(HD, ho) == 1000 and hold and N.Cost(HD, hold) == 1020 and N.Choose(N.Cost(HD, ho), N.Cost(HD, hold)) == "old",
    "tiny gain (20 s) → the order in place stays")
  local hold2 = N.Patch(hn, ho, { "obj:9:1", "obj:7:1", "obj:8:1" }, HD)
  check(hold2 and N.Choose(N.Cost(HD, ho), N.Cost(HD, hold2)) == "new", "big gain → new order")
  local keys = {}
  for _, i in ipairs(ord) do for _, s in ipairs(nodes[i].steps) do keys[#keys + 1] = N.StepKey(s) end end
  local same = N.Patch(nodes, ord, keys, D)
  check(same and table.concat(same, ",") == table.concat(ord, ","), "patched old order = same order")

  -- ------------------------------------------------------------ world: zone A, neighbour B, far C (same continent), D other continent
  M.player.level = 28
  M.mapRects[1424] = { top = 1000, left = 2000, w = 3000, h = 2000, continent = 1 }   -- A
  M.mapRects[1425] = { top = 1000, left = -1000, w = 3000, h = 2000, continent = 1 }  -- B, east of A (touching)
  M.mapRects[1426] = { top = 1000, left = -20000, w = 3000, h = 2000, continent = 1 } -- C, far east
  M.mapRects[1427] = { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }   -- D, other continent
  M.mapNames[1424], M.mapNames[1425], M.mapNames[1426], M.mapNames[1427] = "Zone A", "Zone B", "Zone C", "Zone D"
  T.Geo.rects = {}
  Z.ResetMaps()
  M.group = {}                                   -- solo first (in a group of 3+, elites are kept)
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.pos = { mapID = 1424, x = 0.50, y = 0.50 }
  M.quests = {
    { id = 1001, title = "Loups", level = 27, complete = false, objectives = { { text = "Loup tué : 2/6", done = false, have = 2, need = 6 } } },
    { id = 1002, title = "Crocs", level = 27, complete = false, objectives = { { text = "Croc : 0/5", done = false, have = 0, need = 5 } } },
    { id = 1003, title = "Lettre lointaine", level = 28, complete = true, objectives = {} },
    { id = 1004, title = "Ours", level = 28, complete = false, objectives = { { text = "Ours tué : 0/8", done = false, have = 0, need = 8 } } },
    { id = 1005, title = "Loin", level = 27, complete = false, objectives = { { text = "Raptor tué : 0/8", done = false, have = 0, need = 8 } } },
    { id = 1006, title = "Là-bas", level = 27, complete = false, objectives = { { text = "Crabe tué : 0/8", done = false, have = 0, need = 8 } } },
  }
  M.SetCompleted({})
  P.char.turned = {}
  M.SetPlan(nil)
  M.InstallQuestieDB({
    areaToMap = { [10] = 1424, [11] = 1425, [12] = 1426, [13] = 1427 },
    xp = { [1001] = 2500, [1002] = 2500, [1003] = 150000, [1004] = 2600, [1005] = 2500, [1006] = 2500, [2001] = 2400 },
    quests = {
      [1001] = { name = "Loups", questLevel = 27, requiredLevel = 22, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 501 } } } },
      [1002] = { name = "Crocs", questLevel = 27, requiredLevel = 22, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 502 } } } },
      [1003] = { name = "Lettre lointaine", questLevel = 28, requiredLevel = 22, zoneOrSort = 13, startedBy = { { 700 } }, finishedBy = { { 800 } }, objectives = {} },
      [1004] = { name = "Ours", questLevel = 28, requiredLevel = 22, zoneOrSort = 11, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 503 } } } },
      [1005] = { name = "Loin", questLevel = 27, requiredLevel = 22, zoneOrSort = 12, startedBy = { { 701 } }, finishedBy = { { 701 } }, objectives = { { { 504 } } } },
      [1006] = { name = "Là-bas", questLevel = 27, requiredLevel = 22, zoneOrSort = 13, startedBy = { { 800 } }, finishedBy = { { 800 } }, objectives = { { { 505 } } } },
      [2001] = { name = "Nouvelle", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 501 } } } },
      [2002] = { name = "Élite", questLevel = 28, requiredLevel = 24, zoneOrSort = 10, startedBy = { { 700 } }, finishedBy = { { 700 } }, objectives = { { { 506 } } } },
    },
    npcs = {
      [700] = { name = "Chef", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [10] = { { 52, 52 } } } },
      [701] = { name = "Ermite", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [12] = { { 50, 50 } } } },
      [800] = { name = "Maire", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [13] = { { 60, 60 } } } },
      [501] = { name = "Loup", minLevel = 26, maxLevel = 27, rank = 0, spawns = { [10] = { { 40, 60 }, { 41, 61 } } } },
      [502] = { name = "Croc", minLevel = 26, maxLevel = 27, rank = 0, spawns = { [10] = { { 41, 60 }, { 42, 60 } } } },
      [503] = { name = "Ours", minLevel = 27, maxLevel = 28, rank = 0, spawns = { [11] = { { 30, 50 } } } },
      [504] = { name = "Raptor", minLevel = 27, maxLevel = 28, rank = 0, spawns = { [12] = { { 40, 40 } } } },
      [505] = { name = "Crabe", minLevel = 27, maxLevel = 28, rank = 0, spawns = { [13] = { { 30, 30 } } } },
      [506] = { name = "Golem", minLevel = 28, maxLevel = 28, rank = 1, spawns = { [10] = { { 70, 30 } } } },
    },
  })
  local function exportNow() X:Rebuild(); M.Advance(6, 0.1) end
  local function replan() P:Refresh(true); M.Advance(4, 0.05) end
  local function maps()
    local set, qs = {}, {}
    for _, s in ipairs(P.steps or {}) do
      if s.map then set[s.map] = true end
      if s.q then qs[s.q] = true end
    end
    return set, qs
  end
  local function line(pat) for _, l in ipairs(P:Lines() or {}) do if tostring(l.text):find(pat) then return l end end end
  exportNow()
  check(TreeAIDB.export and TreeAIDB.export.work and TreeAIDB.export.work.mode == "auto", "export carries the work zone for the app")
  Z.lastKeys = nil
  replan()
  local ms, qs = maps()
  check(P.src == "auto" and P.route and P.route.id == "auto" and #P.steps >= 4, "in-game route without the app (" .. tostring(P.steps and #P.steps) .. " steps)")
  check(ms[1424] and ms[1425] and not ms[1426] and not ms[1427], "auto = my zone + neighbour, nothing far, no other continent")
  check(qs[1004] and not qs[1005] and not qs[1006] and not qs[1003], "neighbour quest in, far / other-continent quests out")
  check(not qs[2002], "elite quest left out solo")
  local a1, a2
  for _, s in ipairs(P.steps) do
    if s.k == "obj" and s.q == 1001 then a1 = s.area end
    if s.k == "obj" and s.q == 1002 then a2 = s.area end
  end
  check(a1 and a1 == a2, "wolves + fangs in the same spot = one stop")
  local zl = line("^Zone : Zone A %+ 1 voisine ▾")
  check(zl and zl.onClick, "zone picker line in the route view")
  local merged = line("^Arrêt %d+/%d+ : .* %+ ")
  check(merged, "merged stop shown once, numbered")
  local det = line("^Détour rentable : rendre « Lettre lointaine »")
  check(det and Z.detour and Z.detour.q == 1003 and Z.detour.cross, "far turn-in only PROPOSED, said explicitly (" .. tostring(det and det.text) .. ")")
  -- compass target = current stop
  local ct = Z.CompassTarget({ mapID = 1424, x = 0.5, y = 0.5 })
  local cur = P.steps[P.cursor]
  check(ct and cur and ct.key == "plan:" .. N.StepKey(cur) and ct.plan, "compass aims at the current stop")
  if T.Compass and T.Compass.BuildTargets then
    local tg = T.Compass.BuildTargets({}, { mapID = 1424, x = 0.5, y = 0.5 })
    local first
    for _, t in ipairs(tg) do if t.kind ~= "custom" then first = t break end end
    check(first and first.plan, "compass: the route stop comes first (" .. tostring(first and first.key) .. ")")
  end

  -- ------------------------------------------------------------ zone menu + modes
  check(Z:ShowZoneMenu() ~= false and Z.menuOpen, "zone menu opens (own frame)")
  local _, ents = Z:GetMenu()
  local zoneA
  for _, e in ipairs(ents or {}) do if tostring(e.text):find("Zone A") then zoneA = e end end
  check(zoneA and zoneA.fn, "zone list offered")
  zoneA.fn()
  check(Z.char.mode == "zones" and Z.char.zones[1424], "pick « Zone A » only")
  replan()
  ms, qs = maps()
  check(ms[1424] and not ms[1425] and not qs[1004], "zone list respected (neighbour B out)")
  Z:SetMode("continent")
  exportNow(); replan()
  ms, qs = maps()
  check(ms[1426] and qs[1005] and not ms[1427] and not qs[1006], "whole region: far zone C in, other continent still out")
  Z:SetMode("zone", 1424); Z:SetMode("zone", 1427)
  exportNow(); replan()
  ms, qs = maps()
  check(Z.char.mode == "zones" and ms[1427] and qs[1006], "other continent only when picked")
  check(line("^Changement de continent compris"), "…and said explicitly")
  Z:SetMode("auto")
  replan()
  ms = maps()
  check(not ms[1427] and not ms[1426], "back to auto")

  -- ------------------------------------------------------------ detour on a click
  if Z.detour then
    check(Z:AddDetour() and Z.char.extra[1003], "detour added on click")
    replan()
    local _, q2 = maps()
    local out3
    for _, s in ipairs(P.steps) do if s.q == 1003 and s.out and not s._other then out3 = s end end
    check(q2[1003] and out3, "added detour kept in the route, flagged « hors zone »")
    Z.char.extra = {}
  end

  -- ------------------------------------------------------------ ignore list (right click on a stop)
  replan()
  local stop
  for _, l in ipairs(P:Lines()) do if l.onRightClick and tostring(l.text):find("Ours") then stop = l end end
  if not stop then for _, l in ipairs(P:Lines()) do if l.onRightClick and tostring(l.text):find("^Arrêt") then stop = l end end end
  check(stop, "stops have a right-click menu")
  local s4
  for _, s in ipairs(P.steps) do if s.q == 1004 then s4 = s end end
  Z:StopMenu(s4)
  local _, sents = Z:GetMenu()
  local ign
  for _, e in ipairs(sents or {}) do if e.text == "Ignorer cette quête" then ign = e end end
  check(ign, "« Ignorer cette quête » offered")
  if ign then ign.fn() end
  replan()
  local _, q3 = maps()
  check(Z.char.ignore[1004] and not q3[1004], "ignored quest leaves the route")
  Z:Unignore(1004)
  replan()
  _, q3 = maps()
  check(q3[1004], "un-ignored → back")

  -- ------------------------------------------------------------ hysteresis in the live loop: same input → same order
  local before = {}
  for i, s in ipairs(P.steps) do before[i] = N.StepKey(s) end
  P:Refresh(true); M.Advance(4, 0.05)
  local after = {}
  for i, s in ipairs(P.steps) do after[i] = N.StepKey(s) end
  check(table.concat(before, ";") == table.concat(after, ";"), "replan with nothing new: order unchanged")
  -- tiny move of the player: order kept (gain < 10 %)
  M.pos = { mapID = 1424, x = 0.51, y = 0.50 }
  Z.rev = (Z.rev or 0) + 1
  P:Refresh(true); M.Advance(4, 0.05)
  check(Z.kept == "old" or Z.kept == "new", "hysteresis decided (" .. tostring(Z.kept) .. ")")
  local after2 = {}
  for i, s in ipairs(P.steps) do after2[i] = N.StepKey(s) end
  check(table.concat(before, ";") == table.concat(after2, ";"), "a few steps away: no reshuffle")

  -- ------------------------------------------------------------ turn-in → instant replan, never a turn-in of a quest not in the log
  table.remove(M.quests, 1)                 -- 1001 turned in
  M.SetCompleted({ [1001] = true })
  M.FireEvent("QUEST_TURNED_IN", 1001, 2500, 0)
  M.FireEvent("QUEST_REMOVED", 1001)
  M.Advance(6, 0.05)
  local _, q4 = maps()
  check(not q4[1001], "turned-in quest gone after the event")
  for _, s in ipairs(P.steps or {}) do
    if s.k == "turn" and not s._done then
      local inLog = false
      for _, q in ipairs(M.quests) do if q.id == s.q then inLog = true end end
      local picked = false
      for _, s2 in ipairs(P.steps) do if s2.k == "pick" and s2.q == s.q then picked = true end end
      check(inLog or picked, "turn-in " .. tostring(s.q) .. " = in the log or picked up before")
    end
  end

  -- ------------------------------------------------------------ app plan: out-of-zone and ignored steps masked
  local key = T:CharKey()
  M.SetPlan({ v = 1, made = os.time(), char = key, zone = 1424, routes = {
    { id = "xph", title = "XP/h max", xph = 20000, minutes = 40, verdict = "ok", reasons = {},
      steps = {
        { k = "turn", q = 1003, map = 1427, x = 60, y = 60, label = "Rendre « Lettre lointaine »", eta = 30 },
        { k = "obj", q = 1002, o = 1, map = 1424, x = 41, y = 60, label = "Ramasse « Croc »", eta = 8 },
      } } } })
  P:Refresh(true); M.Advance(3, 0.1)
  check(P.src == "file" and P.steps[1]._zone and P.steps[1]._other and P.cursor == 2, "app plan: other-continent turn-in masked")
  M.SetPlan(nil)

  -- ------------------------------------------------------------ without QuestieDB: log only, waypoints
  for _, n in ipairs({ "LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB" }) do rawset(_G, n, nil) end
  M.waypoints[1002] = { 1424, 0.41, 0.60 }
  M.waypoints[1004] = { 1425, 0.30, 0.50 }
  M.waypoints[1006] = { 1427, 0.30, 0.30 }
  exportNow()
  Z.lastKeys = nil
  replan()
  local ms5, q5 = maps()
  check(P.src == "auto" and #(P.steps or {}) >= 1 and q5[1002] and not ms5[1427], "no QuestieDB: route from the log, zone kept")

  -- ------------------------------------------------------------ options page (TIDY engine) builds with our block
  if T.InitOptions then
    T:InitOptions()
    local _, pages = T:GetOptionPages()
    local pg
    for _, p in ipairs(pages or {}) do if p.module == "ZonePlan" then pg = p.frame end end
    check(pg and pg.name == "Trajet", "options page « Trajet »")
    if pg then
      pg.build()
      local lay = T:GetOptionLayout(pg)
      local found = {}
      for _, r in ipairs(lay and lay.rects or {}) do found[r.label] = true end
      check(found["Zone de travail"] and found["Quêtes ignorées"] and found["Recalculer"], "zone, ignore list and actions on the page")
    end
  end
  -- combat: no build
  M.inCombat = true
  local n0 = Z.rev
  P:Refresh(true)
  check(P._dirty, "no planning in combat")
  M.inCombat = false
  Z.rev = n0
end
