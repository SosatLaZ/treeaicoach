-- Scenario for Modules/Route.lua (agent ROUTE), run by tests/wow/test_route.py on two API flavours.
-- Exact travel numbers on the shipped Data/Travel.lua, quest selection, Shortest Path Forever hand-off (fake API),
-- pause / resume, the « Trajet … · Arrêt 1/N » view. Fake quests, fake names.
-- lua5.1 tests/wow/harness.lua --allow ShortestPathForever --scenario tests/wow/route_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local R, P = T.Route, T.Planner
  check(R and P and T.Data and T.Data.Travel, "Route + Planner + Data/Travel loaded")
  if not (R and P and T.Data and T.Data.Travel) then return end
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-4) end
  local function modes(legs) local t = {} for _, l in ipairs(legs or {}) do t[#t + 1] = l.mode end return table.concat(t, ",") end
  local function has(lines, pat)
    for _, l in ipairs(lines or {}) do if tostring(l.text):find(pat) then return l end end
  end

  -- ------------------------------------------------------------ fastest path (pure, real tables)
  local g = R.BuildHubs("Horde", { 23, 25 })
  check(g and #g.pl == 27, "hubs = 2 known flight masters + 25 docks")
  local s, legs = R.FastestWorld(g, { c = 1, n = 1707.6, w = -4315.7 }, { c = 1, n = -481.8, w = -2596.1 })
  check(near(s, 141.25) and modes(legs) == "walk,fly,walk" and legs[2].to.name == "La Croisée" and near(legs[2].sec, 128.25),
    "flight Orgrimmar → La Croisée = 141.25 s: " .. tostring(s) .. " " .. modes(legs))
  local s0 = R.FastestWorld(R.BuildHubs("Horde", {}), { c = 1, n = 1707.6, w = -4315.7 }, { c = 1, n = -481.8, w = -2596.1 })
  check(near(s0, 517.0233, 1e-3), "flight path not known → walk 517 s: " .. tostring(s0))
  local gz = R.BuildHubs("Horde", {})
  local sz, lz = R.FastestWorld(gz, { c = 1, n = 1368.1, w = -4658.0 }, { c = 0, n = 1992.4, w = 293.0 })
  check(near(sz, 50 * 1.3 / 7 + 356 / 2 + 131 + 70 * 1.3 / 7) and modes(lz) == "walk,zeppelin,walk"
    and lz[2].to.name == "Clairières de Tirisfal", "zeppelin Orgrimmar → Tirisfal = 331.29 s: " .. tostring(sz) .. " " .. modes(lz))
  local sa, la = R.FastestWorld(R.BuildHubs("Alliance", {}), { c = 1, n = 1368.1, w = -4658.0 }, { c = 0, n = 1992.4, w = 293.0 })
  check(not modes(la):find("zeppelin") and modes(la):find("boat") and sa > 1500, "Alliance: no Horde zeppelin (" .. modes(la) .. ")")
  local sh, lh = R.FastestWorld(gz, { c = 1, n = -441.8, w = -2596.1 }, { c = 1, n = 1577.6, w = -4315.7 },
    { hearth = true, bind = { c = 1, n = 1677.6, w = -4315.7, name = "Orgrimmar" } })
  check(near(sh, 15 + 100 * 1.3 / 7) and modes(lh) == "hs,walk", "hearthstone ready, bind closer: 33.57 s " .. tostring(sh))
  local sw = R.FastestWorld(gz, { c = 1, n = -441.8, w = -2596.1 }, { c = 1, n = 1577.6, w = -4315.7 })
  check(sw > 400, "no hearthstone → long walk " .. tostring(sw))
  check(R.Minutes(309) == "5 min" and R.Minutes(42) == "42 s" and R.Km(2625) == "2,4 km" and R.Km(100) == "90 m", "formats")

  -- ------------------------------------------------------------ quest selection (pure)
  local cands = {}
  for i, d in ipairs({ 120, 180, 200, 240, 300, 360, 1500, 2000 }) do
    cands[#cands + 1] = { q = i, title = "Q" .. i, lv = 26, dist = d }
  end
  cands[#cands + 1] = { q = 9, title = "Rouge", lv = 31, dist = 100 }
  local keep, drop = R.Select(cands, { level = 26, group = 1, farMin = 10 })
  check(#keep == 6 and #drop == 3 and drop[1].why == "trop dure" and drop[2].q == 7 and drop[3].why == "trop loin",
    "keep 6, drop 2 far + 1 too hard")
  check(R.ProposalText(keep, drop) == "Garde 6 quêtes, mets-en 3 de côté (1 trop dure, 2 trop loin)", tostring(R.ProposalText(keep, drop)))
  cands[9] = nil
  cands[#cands + 1] = { q = 10, title = "Prête", lv = 26, dist = 800, done = true }      -- 800 < 600 × 1.5
  keep, drop = R.Select(cands, { level = 26, group = 1, farMin = 10 })
  check(#keep == 7 and #drop == 2 and R.ProposalText(keep, drop) == "Garde 7 quêtes, mets-en 2 de côté (trop loin)",
    tostring(R.ProposalText(keep, drop)))
  local k2, d2 = R.Select({ { q = 1, lv = 26, dist = 60, elite = true }, { q = 2, lv = 26, dist = 60 } }, { level = 26, group = 1 })
  check(#d2 == 1 and d2[1].why == "élite" and #k2 == 1, "elite solo → left")
  k2, d2 = R.Select({ { q = 1, lv = 26, dist = 60, elite = true }, { q = 2, lv = 26, dist = 60 } }, { level = 26, group = 2 })
  check(#d2 == 0, "elite in a group → kept")
  -- (QA-QUEST F9) position unknown (no Questie) = kept, never « trop loin » ; a running timer is never proposed
  local cu = {}
  for i, d in ipairs({ 120, 180, 200, 240 }) do cu[#cu + 1] = { q = i, title = "Q" .. i, lv = 26, dist = d } end
  cu[#cu + 1] = { q = 50, title = "Sans position", lv = 26 }
  cu[#cu + 1] = { q = 51, title = "Chrono loin", lv = 26, dist = 5000, timer = true }
  cu[#cu + 1] = { q = 52, title = "Loin", lv = 26, dist = 5000 }
  local ku, du = R.Select(cu, { level = 26, group = 1, farMin = 10 })
  local kept = {}
  for _, c in ipairs(ku) do kept[c.q] = true end
  check(kept[50] and kept[51] and #du == 1 and du[1].q == 52, "unknown position + running timer kept, only the far one proposed")

  -- ------------------------------------------------------------ geometry for the live parts (real zone rects)
  M.mapRects[1411] = { top = 1808.3, left = -1962.5, h = 3525.0, w = 5287.5, continent = 1 }   -- Durotar
  M.mapRects[1420] = { top = 3837.5, left = 3033.3, h = 3012.5, w = 4518.7, continent = 0 }    -- Tirisfal
  T.Geo.rects = {}
  local oldFaction = rawget(_G, "UnitFactionGroup")
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  R.db.hearth = false
  M.pos = { mapID = 1411, x = 0.510, y = 0.139 }                -- at the Orgrimmar zeppelin tower
  local steps = {
    { k = "turn", q = 9001, map = 1420, x = 60.6, y = 58.0, label = "Rendre « Lettre »" },
  }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.steps, P.cursor, P.log = steps, 1, {}
  local st = R:Stops(64)
  check(#st == 2 and st[1].kind == "zeppelin" and st[1].text == "Zeppelin pour Clairières de Tirisfal" and near(st[1].sec, 309)
    and st[2].step == steps[1], "zeppelin leg shown as a stop (" .. tostring(st[1] and st[1].text) .. ")")
  local lines = R:PlanLines(P)
  check(lines and lines[1].text:match("^Trajet %d+ min · ") and has(lines, "^Arrêt 1/2 : Zeppelin pour Clairières de Tirisfal · 5 min")
    and has(lines, "^Arrêt 2/2 : Rendre « Lettre »"), "view: " .. tostring(lines and lines[1] and lines[1].text))

  -- ------------------------------------------------------------ Shortest Path Forever hand-off (fake API)
  local sent, cancels, cur = {}, 0, nil
  local spf = { API = {
    version = 1, minor = 1,
    Estimate = function() return 60 end,
    NavigateRoute = function(owner, stops) if owner ~= "TreeAI" then return false end sent[#sent + 1] = stops cur = 1 return true end,
    CurrentStop = function(owner) return owner == "TreeAI" and cur or nil end,
    Ended = function() return cur == nil and "cancelled" or nil end,
    Cancel = function(owner) if owner == "TreeAI" and cur then cur = nil cancels = cancels + 1 return true end return false end,
    Active = function() return cur ~= nil end,
    Subscribe = function(fn) return function() end end,
  } }
  -- (3.9.8) Suivi Forever = choix du joueur (« Guidage : Suivi Forever ») : on l'active pour cette partie
  local oldGuide, oldGuideV = T.settings.guide, T.settings.guideV
  T.settings.guideV, T.settings.guide = 1, "auto"
  rawset(_G, "ShortestPathForever", spf)
  if TreeAI.SetGuideMode then TreeAI:SetGuideMode("auto") end   -- (3.9.8) SPF is opt-in: this part tests the opt-in
  if T.Compass and T.Compass.ResetSPF then T.Compass.ResetSPF() end
  M.mapRects[1424] = M.mapRects[1424] or { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }
  T.Geo.rects = {}
  M.pos = { mapID = 1424, x = 0.45, y = 0.40 }
  steps = {
    { k = "pick", q = 501, map = 1424, x = 50, y = 40, label = "Prendre « Loups »" },
    { k = "obj", q = 501, o = 1, map = 1424, x = 60, y = 40, label = "Tue « Loup »" },
    { k = "fly", map = 1424, x = 70, y = 40, label = "Vol pour Ailleurs" },
    { k = "turn", q = 501, map = 1424, x = 55, y = 45, label = "Rendre « Loups »" },
  }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.steps, P.cursor, P.log = steps, 1, {}
  R.following, R.char.paused = nil, false
  lines = R:PlanLines(P)
  check(has(lines, "^▶ Suivre ce trajet %(Suivi Forever%)"), "« Suivre ce trajet » offered when SPF is installed")
  check(R:Follow() == "spf" and #sent == 1, "one click → NavigateRoute")
  local s1 = sent[1] or {}
  check(#s1 == 3 and s1[1].questID == 501 and s1[1].kind == "pickup" and near(s1[1].x, 0.5) and s1[2].kind == "objective"
    and s1[2].hold == true and s1[2].radius == 60 and s1[3].kind == "turnin" and s1[3].title == "Rendre « Loups »",
    "our order, transport steps left to SPF, objective area held")
  check(has(R:PlanLines(P), "^Suivi Forever te guide · arrêt 1"), "view says SPF guides")
  steps[1]._done, P.cursor, R._sentAt = true, 2, -100
  R:OnPlanStep()
  check(#sent == 2 and #sent[2] == 2 and sent[2][1].kind == "objective", "step done → remaining stops re-sent")
  R:TogglePause()
  lines = R:PlanLines(P)
  check(cancels == 1 and not R.following and lines[1].text == "Trajet en pause"
    and lines[2].text == "Itinéraire en pause. Clique sur le titre pour reprendre.", "pause cancels our SPF journey")
  lines[1].onClick()
  check(not R.char.paused and has(R:PlanLines(P), "^Trajet "), "click on the title resumes")
  -- the Tracker / Todo « itinéraire » button goes through Route when a plan is active
  local Tr = T.Tracker
  if Tr and Tr.SendRoute then
    local n0 = #sent
    check(Tr.SendRoute() == "spf" and #sent == n0 + 1, "Tracker.SendRoute → Route:Follow")
  end
  M.inCombat = true
  check(R:Follow() == false, "never guides in combat")
  M.inCombat = false

  -- ------------------------------------------------------------ FilterRequests + adopt
  R.db.farMin = 5
  M.pos = { mapID = 1424, x = 0.05, y = 0.50 }
  local reqs, recs = {}, {}
  for i = 1, 6 do
    reqs[#reqs + 1] = { q = 700 + i, steps = { { k = "obj", q = 700 + i, map = 1424, x = 5 + i, y = 50 } } }
    recs[700 + i] = { t = "Proche " .. i, lv = 26, o = {} }
  end
  for i = 1, 2 do
    reqs[#reqs + 1] = { q = 800 + i, steps = { { k = "obj", q = 800 + i, map = 1424, x = 95, y = 50 + i } } }
    recs[800 + i] = { t = "Loin " .. i, lv = 26, o = {} }
  end
  reqs[#reqs + 1] = { q = 900, steps = { { k = "obj", q = 900, map = 1424, x = 8, y = 50 } } }
  recs[900] = { t = "Élite", lv = 26, o = { { rank = 1 } } }
  local out = R:FilterRequests(reqs, recs, {}, { level = 26, group = 1 })
  local sel = R.selection
  check(#out == 9 and sel and #sel.keep == 6 and #sel.drop == 3, "proposal computed, nothing removed yet")
  check(R.ProposalText(sel.keep, sel.drop) == "Garde 6 quêtes, mets-en 3 de côté (1 élite, 2 trop loin)", tostring(R.ProposalText(sel.keep, sel.drop)))
  local Pref = P.Refresh
  P.Refresh = function() end
  check(R:Adopt() == 3 and R.char.drop[801] and R.char.drop[900], "one click adopts")
  out = R:FilterRequests(reqs, recs, {}, { level = 26, group = 1 })
  check(#out == 6 and R.selection.dropped == 3 and #R.selection.drop == 0, "adopted quests leave the route")
  out = R:FilterRequests(reqs, recs, {}, { level = 27, group = 1 })
  check(#out == 9, "level up → everything reconsidered")
  P.Refresh = Pref

  -- ------------------------------------------------------------ Planner leg uses the fastest path
  local gw = R.Graph()                 -- (NAV) walking = measured speed × learned detour (NavTime), prior 1.15 → 1.2
  check(gw and gw.speed == 7 and gw.detour == 1.2, "walk model from NavTime: " .. tostring(gw and gw.speed) .. " " .. tostring(gw and gw.detour))
  check(P.Leg and near(P.Leg({ map = 1424, x = 10, y = 50 }, { map = 1424, x = 20, y = 50 }), 300 * gw.detour / gw.speed, 0.05),
    "Planner leg = Route.LegSeconds (walk) " .. tostring(P.Leg and P.Leg({ map = 1424, x = 10, y = 50 }, { map = 1424, x = 20, y = 50 })))

  rawset(_G, "ShortestPathForever", nil)
  if TreeAI.SetGuideMode then TreeAI:SetGuideMode("treeai") end
  if T.Compass and T.Compass.ResetSPF then T.Compass.ResetSPF() end
  rawset(_G, "UnitFactionGroup", oldFaction)
  T.settings.guide, T.settings.guideV = oldGuide, oldGuideV
  R.db.hearth, R.db.farMin, R.following = true, 10, nil
  P.route, P.steps, P.cursor = nil, nil, nil
end
