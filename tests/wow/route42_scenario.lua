-- Scenario ROUTE42 (4.2) : run by tests/wow/test_route42.py on two API flavours × variants (R42_VARIANT) :
--   full (Questie db + SPF mocked) · plain (no Questie, no SPF) · spf (SPF mocked, no Questie) · questie (Questie, no SPF).
-- Each item of the study (research/addons_study.md §8) that ROUTE42 implements, proven on what the player sees :
--  2  numbered, timed transport sub-steps whose times sum to « Trajet m:ss » ;
--  3  elevators / tram as graph edges (mocked lift, data list empty until measured) + « sorti de la cage » rule ;
--  4  the « Suivi TreeAI » column : OFF by default, anchored under the visible tracker, never moves / hides it ;
--  5  native user waypoint at the end of the current sub-step : set on a click only, cleared when stale, off if SPF guides ;
--  6  SPF delegation : merged stops with questIDs / handins, Subscribe-driven state ;
--  7  « Histoire : zone · Chapitre n sur N » (N only for a proven chain), « Histoire terminée » once ;
-- 10  world-map pins / route INSIDE the map canvas (no AddDataProvider, nothing on UIParent, same canvas point after
--     pan and zoom, clipped by the map, hidden with it) ;
-- 11  hysteresis min(10 %, 30 s) ; 12 boats : departure SEEN at the quay → « part dans m:ss », « Prochains départs » ;
-- 13  hearth only if it saves ≥ 20 s (solver = display = brute force) ;
--  +  Route.InsertOptional / Route.VendorFor wired into the plan (ZonePlan / Planner), no hearth bridge ;
--  +  P0 from the player's log : a chosen city / empty zone falls back to « auto », said once ; compass target stable.
-- Nothing in chat, zero blocked action. Fake quests, fake names, fake lift coordinates (only in this test).
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local VARIANT = os.getenv("R42_VARIANT") or "full"
  local withSPF = VARIANT == "full" or VARIANT == "spf"
  local withQuestie = VARIANT == "full" or VARIANT == "questie"
  local R, P, Zp, QB, Cm = T.Route, T.Planner, T.ZonePlan, T.QuestBrain, T.Compass
  check(R and P and Zp and QB and Cm and T.MapCanvas, "Route, Planner, ZonePlan, QuestBrain, Compass, MapCanvas loaded")
  if not (R and P and Zp and QB and Cm and T.MapCanvas) then return end
  M.LeaveCombat()
  local chat0 = #M.output
  local blocked0 = #M.blocked
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function say(...) local t = {} for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end io.stdout:write("R42 " .. table.concat(t, " ") .. "\n") end
  if R.char then R.char.paused = false end
  if Zp.char then Zp.char.mode, Zp.char.zones = "auto", {} end
  local oldFaction = rawget(_G, "UnitFactionGroup")
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)

  -- ------------------------------------------------------------ 11. hysteresis : gain ≥ min(10 %, 30 s)
  check(R.Choose(975, 1000) == "old" and R.Choose(970, 1000) == "new" and R.Choose(110, 120) == "old"
    and R.Choose(105, 120) == "new" and R.Choose(98, 100) == "old" and R.Choose(1, nil) == "new",
    "hysteresis: a new order only for a gain ≥ min(10 %, 30 s) (and ≥ 3 s)")

  -- ------------------------------------------------------------ 13. hearth ≥ 20 s, solver = brute force = display rule
  do
    local nodes = { { qs = { [1] = 3 }, steps = { { k = "turn" } } }, { qs = { [2] = 3 }, steps = { { k = "turn" } } } }
    local D = { [0] = { 100, 200 }, { [2] = 100 }, { [1] = 100 }, hs = { 85, 300 } }   -- hearth saves 15 s on leg 0→1
    local s, c, at = R.Solve(nodes, { 1, 2 }, D, {})
    check(s and at == nil and near(c, 200), "hearth saving 15 s: not used (" .. tostring(c) .. ")")
    D.hs = { 70, 300 }                                                                  -- saves 30 s
    s, c, at = R.Solve(nodes, { 1, 2 }, D, {})
    check(at == 1 and near(c, 170), "hearth saving 30 s: used once (" .. tostring(c) .. ")")
    local seed, worst = 4242, 1
    local function rnd() seed = (seed * 1103515245 + 12345) % 2147483648 return seed / 2147483648 end
    for trial = 1, 24 do
      local n = 4 + trial % 5
      local nd, pts = {}, {}
      for i = 1, n do nd[i] = { qs = { [i] = 3 }, steps = { { k = "turn" } } } pts[i] = { rnd() * 900, rnd() * 900 } end
      local Dm = { [0] = {}, hs = {} }
      for i = 1, n do
        Dm[0][i] = math.sqrt((pts[i][1] - 450) ^ 2 + (pts[i][2] - 450) ^ 2) / 7
        Dm[i] = {}
        for j = 1, n do if i ~= j then Dm[i][j] = math.sqrt((pts[i][1] - pts[j][1]) ^ 2 + (pts[i][2] - pts[j][2]) ^ 2) / 7 end end
        Dm.hs[i] = 15 + math.sqrt(pts[i][1] ^ 2 + pts[i][2] ^ 2) / 7
      end
      local ord = {}
      for i = 1, n do ord[i] = i end
      local _, bc = R.BruteForce(nd, ord, Dm, {})
      local so, sc2 = R.Solve(nd, ord, Dm, {})
      if bc and sc2 then worst = math.max(worst, sc2 / bc) end
      check(so and sc2 <= bc + 1e-6 and near(R.OrderCost(Dm, so), sc2), "hearth rule: exact = brute force (trial " .. trial .. ")")
    end
    say("hearth-gain brute force worst ratio", string.format("%.4f", worst))
  end

  -- ------------------------------------------------------------ geometry (real zone rects) + a route Durotar → Tirisfal
  M.mapRects[1411] = { top = 1808.3, left = -1962.5, h = 3525.0, w = 5287.5, continent = 1 }   -- Durotar
  M.mapRects[1420] = { top = 3837.5, left = 3033.3, h = 3012.5, w = 4518.7, continent = 0 }    -- Tirisfal
  M.mapRects[1424] = M.mapRects[1424] or { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }
  T.Geo.rects = {}
  R.db.hearth = false
  local fakeT = 1700000000
  local oldTime = rawget(_G, "time")
  rawset(_G, "time", function(tb) if tb then return oldTime(tb) end return fakeT end)
  M.pos = { mapID = 1411, x = 0.510, y = 0.139 }                -- at the Orgrimmar zeppelin tower
  local steps = { { k = "turn", q = 9001, map = 1420, x = 60.6, y = 58.0, label = "Rendre « Lettre »" } }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.steps, P.cursor, P.log, P.stepsRev = steps, 1, {}, (P.stepsRev or 0) + 1
  R.char.boat = {}

  -- ------------------------------------------------------------ 2. sub-steps, header = sum of the displayed times
  local b = R.Block()
  check(b and #b.steps >= 2, "route block built: " .. tostring(b and #b.steps))
  if not b then return end
  local sum, txt = 0, {}
  for _, ss in ipairs(b.steps) do sum = sum + ss.sec txt[#txt + 1] = ss.n .. ". " .. ss.text .. " " .. R.MSS(ss.sec) .. (ss.est and " ~" or "") end
  say("SUBSTEPS", b.head, "|", table.concat(txt, " | "))
  check(sum == b.total and b.head:find("^Trajet " .. R.MSS(sum):gsub("%p", "%%%0") .. " · ") and b.head:find("Arrêt 1 sur 1 : Rendre « Lettre »", 1, true),
    "header « Trajet m:ss · km · Arrêt 1 sur 1 : X » = sum of the displayed sub-steps (" .. b.head .. ")")
  local zep
  for _, ss in ipairs(b.steps) do if ss.mode == "zeppelin" then zep = ss end end
  check(zep and zep.est and zep.text:find("^Zeppelin pour Clairières de Tirisfal %(attente 2:58 environ%)"),
    "zeppelin never seen: half the period, « environ » (" .. tostring(zep and zep.text) .. ")")
  check(b.steps[#b.steps].mode == "walk" and b.steps[#b.steps].text == "Marcher jusqu'au PNJ (rendre)", "last sub-step: walk to the NPC")

  -- ------------------------------------------------------------ 12. departure SEEN at the quay → « part dans »
  -- seen: near the Orgrimmar tower (dock 13), then 150 s later at the Tirisfal tower (dock 14) : departed at t+150-131-5
  local D13, D14 = T.Data.Travel.docks[13], T.Data.Travel.docks[14]
  R._dock = nil
  R.NoteSample(D13[2], D13[3] + 5, D13[4], 0)
  fakeT = fakeT + 150
  R.NoteSample(D14[2], D14[3], D14[4] + 5, 150)
  local ob = R.char.boat["13-14"]
  check(ob and ob.t == fakeT - 131 - 5, "departure Orgrimmar → Tirisfal seen and kept per character (" .. tostring(ob and ob.t) .. ")")
  fakeT = fakeT + 356 * 4 + 300                                 -- later the same session
  local w, cross, known = R.BoatWait(13, 14, fakeT)
  check(known and cross == 131 and near(w, ((ob.t - fakeT) % 356)), "wait from the seen phase: " .. tostring(w))
  local wb, _, kb = R.BoatWait(14, 13, fakeT)
  check(wb and not kb, "the way back is deduced, still « environ »")
  P.stepsRev = P.stepsRev + 1
  b = R.Block()
  zep = nil
  for _, ss in ipairs(b.steps) do if ss.mode == "zeppelin" then zep = ss end end
  check(zep and not zep.est and zep.text:find("(part dans " .. R.MSS(w) .. ")", 1, true) and zep.sec == math.floor(w + 131 + 0.5),
    "zeppelin seen: « part dans " .. R.MSS(w) .. " », time = wait + crossing (" .. tostring(zep and zep.text) .. ")")
  local deps = R.Departures(R.World({ map = 1411, x = 51.0, y = 13.9 }))
  check(deps and deps[1].text:find("^Zeppelin pour Clairières de Tirisfal : part dans "), "near the quay: « Prochains départs » (" .. tostring(deps and deps[1] and deps[1].text) .. ")")
  check(b.departures and #b.departures >= 1, "block carries the next departures at the quay")

  -- ------------------------------------------------------------ 3. elevators / tram : graph edge + cage rule
  check(type(T.Data.Travel.lifts) == "table" and #T.Data.Travel.lifts == 0, "shipped lift list empty until measured (no invented coordinates)")
  T.Data.Travel.lifts = { { "elevator", "Bas du test", "Haut du test", 1, 0, 0, 600, 0, 40, 20, 1411, 50, 50, 1411, 55, 50 } }
  local gl = R.BuildHubs("Horde", {}, { speed = 7 })
  local sl, ll = R.FastestWorld(gl, { c = 1, n = -5, w = 0 }, { c = 1, n = 605, w = 0 })
  local ml = {}
  for _, l in ipairs(ll) do ml[#ml + 1] = l.mode end
  check(table.concat(ml, ",") == "walk,elevator,walk" and near(sl, 5 * 1.3 / 7 + 40 + 5 * 1.3 / 7, 1e-6),
    "a lift is a transport edge (half period + ride): " .. table.concat(ml, ",") .. " " .. tostring(sl))
  check(ll[2].li == 1 and ll[2].from and ll[2].to.name == "Haut du test", "lift leg knows its line and its ends")
  local st0 = {}
  check(R.LiftStep(st0, true, 10, 20) == "riding" and R.LiftStep(st0, false, 25, 20) == "back"
    and R.LiftStep(st0, true, 30, 20) == "riding" and R.LiftStep(st0, false, 55, 20) == "done",
    "lift step: left too early = « reviens au palier », left after the ride = done")
  local subs = R.SubSteps({ step = { k = "turn" }, legs = ll })
  check(subs[2] and subs[2].text == "Ascenseur pour Haut du test", "sub-step « Ascenseur pour … »")
  T.Data.Travel.lifts = {}

  -- ------------------------------------------------------------ 7. stories : « Chapitre n sur N » only when proven
  local DB = { [1] = { nx = 2 }, [2] = { pre = { 1 }, nx = 3 }, [3] = { pre = { 2 } },                -- proven chain
    [10] = { nx = 11 }, [11] = { pre = { 10, 12 }, nx = 13 }, [12] = {}, [13] = { pre = { 11 } },    -- branching
    [20] = {}, [21] = { nx = 22 }, [22] = { pre = { 21 }, nx = 21 } }                                -- alone · loop
  local db = { Q = function(id, f)
    local e = DB[id] or {}
    if f == "nextQuestInChain" then return e.nx elseif f == "preQuestSingle" then return e.pre
    elseif f == "name" then return "Histoire " .. id end
  end }
  local c2 = QB.Chain(db, 2)
  check(c2 and c2.head == 1 and c2.n == 2 and c2.N == 3, "proven chain: Chapitre 2 sur 3")
  local c13 = QB.Chain(db, 13)
  check(c13 and c13.n == 3 and c13.N == nil, "branching chain: « Chapitre 3 » without « sur N »")
  check(QB.Chain(db, 20) == nil, "a lone quest has no chapter")
  local cl = QB.Chain(db, 22)
  check(cl and cl.N == nil, "a loop is never « proven »")
  check(R.StoryText("Mille pointes", c2) == "Histoire : Mille pointes · Chapitre 2 sur 3"
    and R.StoryText("Mille pointes", c13) == "Histoire : Mille pointes · Chapitre 3" and R.StoryText("Mille pointes", nil) == "Histoire : Mille pointes",
    "story header text")
  local X = T.Export
  local oldSrc = X and X.Source
  if X then X.Source = function() return withQuestie and db or nil end end
  steps[1].q = 3
  P.stepsRev = P.stepsRev + 1
  b = R.Block()
  if withQuestie then
    check(b.story and b.story:find("· Chapitre 3 sur 3$"), "block story with Questie: " .. tostring(b.story))
    QB.char.storyDone = {}
    local t1 = QB.StoryDone(3)
    check(t1 == "Histoire terminée : « Histoire 1 »" and QB.StoryDone(3) == nil, "« Histoire terminée » once per character")
  else
    check(b.story and not b.story:find("Chapitre"), "no Questie: no chapter guessed (" .. tostring(b.story) .. ")")
  end
  if X then X.Source = oldSrc end
  steps[1].q = 9001

  -- ------------------------------------------------------------ 6. SPF delegation (merged stops, handins, Subscribe)
  local sent, subFn, cur = {}, nil, nil
  if withSPF then
    rawset(_G, "ShortestPathForever", { API = { version = 1, minor = 1,
      Estimate = function() return 60 end,
      NavigateRoute = function(owner, stops) if owner ~= "TreeAI" then return false end sent[#sent + 1] = stops cur = 1 return true end,
      CurrentStop = function(owner) return owner == "TreeAI" and cur or nil end,
      Ended = function() return cur == nil and "done" or nil end,
      Cancel = function() cur = nil return true end, Active = function() return cur ~= nil end,
      Subscribe = function(fn) subFn = fn return function() end end } })
    if T.SetGuideMode then T:SetGuideMode("auto") end
    if Cm.ResetSPF then Cm.ResetSPF() end
    R._unsub, R._subOK = nil, nil
    R:OnEnable()
  else
    rawset(_G, "ShortestPathForever", nil)
  end
  M.pos = { mapID = 1424, x = 0.45, y = 0.40 }
  local st2 = {
    { k = "turn", q = 601, map = 1424, x = 50, y = 40, label = "Rendre « A »" },
    { k = "turn", q = 602, map = 1424, x = 50.2, y = 40.1, label = "Rendre « B »" },
    { k = "obj", q = 603, o = 1, map = 1424, x = 60, y = 45, label = "Tue « Loup »", area = "a1" },
    { k = "obj", q = 604, o = 1, map = 1424, x = 61, y = 45, label = "Ramasse « Herbe »", area = "a1" },
  }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = st2, verdict = "ok" }
  P.steps, P.cursor, P.stepsRev = st2, 1, P.stepsRev + 1
  R.following = nil
  local stops = R:SPFStops()
  check(#stops == 2 and #stops[1].questIDs == 2 and stops[1].handins and #stops[1].handins == 2 and stops[1].kind == "turnin"
    and stops[2].hold and stops[2].radius == 60 and #stops[2].questIDs == 2,
    "one tab stop = one SPF stop: questIDs + handins (the game's « ? » hidden by SPF), objective area held")
  if withSPF then
    local how = R:Follow()
    check(how == "spf" and #sent == 1 and #sent[1] == 2, "Follow (click) → NavigateRoute with merged stops: " .. tostring(how))
    check(subFn ~= nil and R._subOK, "subscribed to SPF")
    cur = 2
    subFn({ stop = 2 })
    check(R:Following() == 2 and R:CurrentStep() == st2[3], "state from Subscribe: stop 2 = the objectives (merged)")
    cur = nil
    subFn({ ended = true })
    check(R:Following() == nil and not R.following, "Ended (via Subscribe) → no longer following")
  else
    check(R.SPF() == nil and R:Follow() ~= "spf", "no SPF: our own guidance")
  end

  -- ------------------------------------------------------------ 5. native user waypoint (click only, off if SPF guides)
  rawset(_G, "ShortestPathForever", nil)
  if Cm.ResetSPF then Cm.ResetSPF() end
  if T.SetGuideMode then T:SetGuideMode("treeai") end
  R.following = nil
  check(R.db.waypoint == false, "native waypoint: off by default")
  R.db.waypoint = true
  M.userWaypoint = nil
  check(R.SyncWaypoint(false) == nil and M.userWaypoint == nil, "no click → never set by code")
  if rawget(_G, "C_Map") and C_Map.SetUserWaypoint and rawget(_G, "UiMapPoint") then
    M.hardware = true
    local r1 = R.SyncWaypoint(true)
    M.hardware = false
    local wp = M.userWaypoint
    check(r1 == "set" and wp and wp.uiMapID == 1424 and near(wp.position.x, 0.50, 0.003), "click → user waypoint at the end of the current sub-step")
    check(M.superTrackUser == true, "super-tracked (native centre-screen marker)")
    st2[1]._done, st2[2]._done, P.cursor, P.stepsRev = true, true, 3, P.stepsRev + 1
    local rs = R.SyncWaypoint(false)
    if os.getenv("R42_DEBUG") then say("STALE", rs, tostring(M.userWaypoint), tostring(R._wpKey)) end
    check(rs == "stale" and M.userWaypoint == nil, "stop done → our stale waypoint removed (next one on the next click)")
    st2[1]._done, st2[2]._done, P.cursor, P.stepsRev = nil, nil, 1, P.stepsRev + 1
  else
    check(R.SyncWaypoint(true) == nil, "no native waypoint API on this flavour: compass only")
  end
  R.db.waypoint = false

  -- ------------------------------------------------------------ 4. (5.1 CLEAN) « Suivi TreeAI » (Watch.lua) retiré : le suivi du jeu
  -- reste LE suivi, jamais déplacé ni caché
  local tf = rawget(_G, "ObjectiveTrackerFrame") or CreateFrame("Frame", "ObjectiveTrackerFrame", UIParent)
  tf:ClearAllPoints()
  tf:SetPoint("TOPRIGHT", UIParent, "TOPRIGHT", -60, -230)
  tf:Show()
  local tfPoints = #tf.__points
  M.Advance(1, 0.5)
  check(not T.Watch and not rawget(_G, "TreeAIWatchFrame"), "no TreeAI tracker column")
  check(tf:IsShown() and #tf.__points == tfPoints and (tf.__alpha or 1) == 1 and #M.blocked == blocked0,
    "the game's tracker never moved / hidden, nothing blocked")

  -- ------------------------------------------------------------ 10. world map : inside the canvas, no data provider
  local wm = CreateFrame("Frame", "WorldMapFrame", UIParent)
  local scr = CreateFrame("ScrollFrame", nil, wm)
  local child = CreateFrame("Frame", nil, scr)
  wm.ScrollContainer, scr.Child = scr, child
  local providers = 0
  wm.AddDataProvider = function() providers = providers + 1 end
  wm.GetMapID = function() return 1424 end
  child.__w, child.__h, child.__scale = 1000, 667, 1
  wm.__shown = false
  local PD, QP = T.PathDraw, T.QuestPins
  PD._mapHooked, QP._mapHooked = nil, nil
  PD.AttachMap()
  if PD.view then
    local mkLine = PD.view.CreateLine
    PD.view.CreateLine = function(self, ...)
      local l = mkLine(self, ...)
      function l:SetStartPoint(_, rel, x, y) self.sp = { rel, x, y } end
      function l:SetEndPoint(_, rel, x, y) self.ep = { rel, x, y } end
      function l:SetThickness(th) self.th = th end
      function l:SetColorTexture(r, g, bb, a) self.rgba = { r, g, bb, a } end
      return l
    end
  end
  if QP and QP.AttachMap then QP.AttachMap() end
  local CR = T.CityRun
  if CR and CR.AttachMap then CR._mapHooked = nil pcall(CR.AttachMap) end
  check(providers == 0, "no AddDataProvider (taints the gamepad map walk on Forever)")
  check(PD.view and T.MapCanvas.InMap(PD.view) and PD.view:GetParent() == child, "route layer = child of the map canvas, never on UIParent")
  check(not QP.view or T.MapCanvas.InMap(QP.view), "quest pin layer inside the map canvas")
  check(not (CR and CR.mview) or T.MapCanvas.InMap(CR.mview), "city pin layer inside the map canvas")
  check(PD.view.__mouse == false, "layer takes no mouse (only pins do)")
  wm:Show()                                                  -- the player opens the map
  M.Advance(0.2, 0.1)
  check(PD.badgesShown >= 2 and PD.segments >= 1, ("route drawn in the canvas: %d badges, %d segments"):format(PD.badgesShown, PD.segments))
  local b1 = PD.badges[1] and PD.badges[1].bg.__points[1]
  local x1 = b1 and b1[4]
  check(b1 and b1[2] == PD.view and near(x1 * PD.view:GetScale(), 0.50 * 1000, 1e-6), "badge 1 at 50 % of the canvas width (canvas units)")
  local redraws = PD.mapRedraws
  child.GetLeft = function() return 300 end                  -- the player drags the map
  local oldDM = PD.DrawMap
  PD.DrawMap = function(...) if os.getenv("R42_DEBUG") then say("DRAW", debug.traceback()) end return oldDM(...) end
  M.Advance(0.3, 0.1)
  PD.DrawMap = oldDM
  check(PD.badges[1].bg.__points[1][4] == x1, "pan: same canvas offsets (the pin moves with the canvas, nothing to recompute)")
  child:SetScale(2)                                          -- zoom in
  M.Advance(0.2, 0.1)
  local xz = PD.badges[1].bg.__points[1][4]
  check(near(PD.view:GetScale(), 0.5) and near(xz * PD.view:GetScale(), 0.50 * 1000, 1e-6) and PD.mapRedraws > redraws,
    "zoom: layer scale = 1 / canvas scale, the pin stays on the same canvas point, constant size on screen")
  check(PD.npcLeg == true and PD.dashes[1].obj.rgba and near(PD.dashes[1].obj.rgba[1], 1) and near(PD.dashes[1].obj.rgba[2], 0.82),
    "current stop is an NPC: its leg drawn in gold at once")
  check((PD.arrowsShown or 0) >= 1, "direction arrows along the legs")
  wm:Hide()
  local before = PD.mapRedraws
  M.Advance(0.5, 0.1)
  check(not PD.view:IsVisible() and PD.mapRedraws == before, "map closed: layer hidden with it, no OnUpdate work")
  child:SetScale(1)
  wm:Hide()

  -- ------------------------------------------------------------ Route.VendorFor wired : « Achète X » → vendor stop
  local oldVF = R.VendorFor
  R.VendorFor = function(id) if id == 777 then return { map = 1424, x = 33, y = 44, name = "Marchande", src = "vu" } end end
  local ctxc = P.CostCtx and P.CostCtx() or { level = 30, group = 1 }
  local qs = P:QuestSteps(950, { lv = 30, o = { { k = "item", id = 777, n = 3 } } }, { obj = { { n = 3, have = 0, type = "item", text = "Épices : 0/3" } } }, ctxc)
  local buy = qs[1]
  check(buy and buy.buy and buy.map == 1424 and buy.x == 33 and buy.svc == 20, "item sold by a vendor: the stop is the vendor (20 s on site)")
  P.log = { [950] = { t = "Épices", obj = { { n = 3, have = 0, type = "item", text = "Épices : 0/3" } } } }
  check(P:StepLabel(buy):find("^Achète « Épices »"), "label « Achète « Épices » »: " .. tostring(P:StepLabel(buy)))
  R.VendorFor = oldVF
  P.log = {}

  -- ------------------------------------------------------------ InsertOptional wired + no hearth bridge
  check(not R.InstallEngine and not Zp.Engine._routefix and Zp.noHearth == nil, "bridge removed: ZonePlan calls the Route solver directly")
  do
    local nodes = {
      { steps = { { k = "turn", q = 1 } }, map = 1424, x = 40, y = 40, qs = { [1] = 3 } },
      { steps = { { k = "turn", q = 2 } }, map = 1424, x = 70, y = 40, qs = { [2] = 3 } },
      { steps = { { k = "pick", q = 3 } }, map = 1424, x = 55, y = 41, qs = { [3] = 1 }, value = 500, svc = 15 },
    }
    local leg = function(a, c) return math.sqrt(((a.x - c.x) * 30) ^ 2 + ((a.y - c.y) * 20) ^ 2) / 7 end
    local start = { map = 1424, x = 30, y = 40 }
    local D = Zp.SolverMatrix(nodes, { 1, 2 }, start, leg)
    Zp.ExtendMatrix(D, nodes, { 1, 2 }, { 3 }, start, leg)
    local ord = { 1, 2 }
    local _, added = R.InsertOptional(nodes, ord, D, { 3 }, 10, { maxDetour = Zp.PICK_DETOUR_S })
    check(#added == 1 and ord[2] == 3, "quest giver on the way: picked up in passing (" .. table.concat(ord, ",") .. ")")
  end

  -- a hub with 3 quest givers + their objectives outside : all 3 picked up before leaving, no return trip
  do
    local N = Zp.Engine
    local function nd(q, rank, x, y) return { steps = { { k = rank == 1 and "pick" or (rank == 2 and "obj" or "turn"), q = q } }, map = 1424, x = x, y = y, qs = { [q] = rank } } end
    local nodes = { nd(1, 1, 50, 50), nd(2, 1, 51, 50.5), nd(3, 1, 50.5, 51), nd(1, 2, 80, 70), nd(2, 2, 20, 70), nd(3, 2, 75, 20),
      nd(9, 3, 52, 49) }
    local leg = function(a, c) return math.sqrt(((a.x - c.x) * 30) ^ 2 + ((a.y - c.y) * 20) ^ 2) / 7 end
    local start = { map = 1424, x = 50.2, y = 50.2 }
    local ord = { 1, 2, 3, 4, 5, 6, 7 }
    local D = Zp.SolverMatrix(nodes, ord, start, leg)
    local so = R.Solve(nodes, ord, D, {})
    so = N.NearFirst(nodes, so, start, Zp.Dist, N.HUB_PICK, 20, 0, "pick")
    local picksFirst = nodes[so[1]].steps[1].k == "pick" and nodes[so[2]].steps[1].k == "pick" and nodes[so[3]].steps[1].k == "pick"
    local lastPick, firstObj = 0, 99
    for i, k in ipairs(so) do
      local kk = nodes[k].steps[1].k
      if kk == "pick" then lastPick = i end
      if kk == "obj" and i < firstObj then firstObj = i end
    end
    check(picksFirst and lastPick < firstObj and N.Valid(nodes, so), "hub: the 3 quests picked up before leaving, no return (" .. table.concat(so, ",") .. ")")
  end

  -- ------------------------------------------------------------ P0 (player's log) : chosen city / empty zone → auto
  P.steps, P.cursor, P.route = nil, nil, nil
  Zp.char.mode, Zp.char.zones = "zones", { [1458] = true }   -- Fossoyeuse
  Zp._lastAllowed, Zp.fallbackNote = nil, nil
  M.pos = { mapID = 1424, x = 0.45, y = 0.40 }
  local okB = pcall(Zp.BuildRoute, Zp, P)
  check(okB and Zp.char.mode == "auto" and Zp.fallbackNote and Zp.fallbackNote.text:find("ville n'est pas une zone de quêtes", 1, true),
    "a city chosen as quest zone → back to « auto », said once: " .. tostring(Zp.fallbackNote and Zp.fallbackNote.text))
  check(Zp:PickZone(1458, "player") == false, "a city is never kept as a sticky quest zone")
  Zp.char.mode, Zp.char.zones = "zones", { [1442] = true }   -- a zone with nothing doable (Serres-Rocheuses)
  Zp._lastAllowed, Zp.fallbackNote = nil, nil
  local errB
  okB, errB = pcall(Zp.BuildRoute, Zp, P)
  if os.getenv("R42_DEBUG") then say("P0", okB, tostring(errB), Zp.char.mode) end
  check(okB and Zp.char.mode == "auto" and Zp.fallbackNote and Zp.fallbackNote.text:find("plus rien à faire → zone actuelle", 1, true),
    "chosen zone with 0 stops → « plus rien à faire → zone actuelle »: " .. tostring(Zp.fallbackNote and Zp.fallbackNote.text))
  local hl = Zp:HeadLines()
  local saidLine = false
  for _, l in ipairs(hl) do if l.id == "zone:fallback" then saidLine = true end end
  check(saidLine, "said on the Quêtes page (one line, 2 min)")

  -- compass : without a route, the nearest-quest target stays put (no flip per step), a click spam = one step
  do
    local oldBuild = Cm.BuildTargets
    local A1 = { key = "obj:1", kind = "objective", label = "A", mapID = 1424, x = 0.5, y = 0.5 }
    local B1 = { key = "obj:2", kind = "objective", label = "B", mapID = 1424, x = 0.6, y = 0.5 }
    local seq = { { A1, B1 }, { B1, A1 }, { B1, A1 } }
    local k = 0
    Cm.BuildTargets = function() k = math.min(k + 1, #seq) return seq[k] end
    Cm.pinned = nil
    Cm.targets, Cm.idx = {}, 1
    Cm.Rebuild("init")
    local t1 = Cm.Current()
    Cm.Rebuild("tick")
    local t2 = Cm.Current()
    check(t1 and t2 and t1.key == "obj:1" and t2.key == "obj:1", "no route: the nearest-quest target does not flip when the list re-sorts")
    Cm.Cycle()
    local t3 = Cm.Current()
    Cm.Cycle()
    check(Cm.Current() == t3, "click spam within 0.35 s = one step")
    Cm.BuildTargets = oldBuild
    Cm.pinned = nil
    Cm.Rebuild("force")
  end

  -- ------------------------------------------------------------ the whole run : nothing in chat, nothing blocked
  local nChat = 0
  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then nChat = nChat + 1 say("CHAT " .. M.output[i]) end end
  check(nChat == 0, "nothing in the chat (" .. nChat .. ")")
  check(#M.blocked == blocked0, "zero blocked action")
  rawset(_G, "time", oldTime)
  rawset(_G, "UnitFactionGroup", oldFaction)
  rawset(_G, "ShortestPathForever", nil)
  R.db.hearth = true
  P.route, P.steps, P.cursor = nil, nil, nil
end
