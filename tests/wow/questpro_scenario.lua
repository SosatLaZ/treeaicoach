-- Scenario for Compass / Tracker / Predict (agent QUEST-PRO), run on both API flavours by
-- tests/wow/test_questpro.py. Fake names only. Fake Questie DB (read-only use).
return function(M, ctx)
  local T = TreeAI
  M.RunSlash("/tai module Todo off")   -- « À faire » owns the quests tab otherwise (tested in todo_scenario)
  local C, TR, P, Q = T:GetModule("Compass"), T:GetModule("Tracker"), T:GetModule("Predict"), T:GetModule("Quests")
  ctx.check(C and TR and P and Q, "Compass, Tracker, Predict, Quests registered")
  if not (C and TR and P and Q) then return end
  local check = ctx.check
  local pi = math.pi
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function sugg(pat)
    for _, s in ipairs(T:GetSuggestions()) do if tostring(s.text):find(pat) then return s end end
  end
  local modern = rawget(_G, "CreateVector2D") ~= nil

  -- ---------------------------------------------------------------- geometry (pure)
  local G = C.Geo
  local me = { mapID = 1424, x = 0.5, y = 0.4 }
  local d, b = G.Vector(me, { mapID = 1424, x = 0.5, y = 0.3 })
  check(near(d, 200) and near(b, 0), "north: 200 yd, bearing 0: " .. tostring(d) .. " " .. tostring(b))
  d, b = G.Vector(me, { mapID = 1424, x = 0.6, y = 0.4 })
  check(near(d, 300) and near(b, -pi / 2), "east: 300 yd, bearing -pi/2")
  check(G.Cardinal(b) == "E" and G.Cardinal(0) == "N" and G.Cardinal(pi / 2) == "O", "cardinals")
  check(near(G.Relative(-pi / 2, 0), 3 * pi / 2), "relative angle wraps")
  check(near(G.Relative(pi / 2, pi / 2), 0), "facing the target = 0")
  local d2 = G.Vector(me, { mapID = 1416, x = 0.5, y = 0.5 })
  check(d2 and d2 > 0, "cross-map distance on the same continent via world coords")
  check(G.Dist(100, "m") == "91 m" and G.Dist(100, "yd") == "100 yd" and G.Dist(2000, "m") == "1,8 km", "distance format: " .. G.Dist(2000, "m"))
  check(G.ETA(140, 7) == "20 s" and G.ETA(700, 7) == "1 min 40", "ETA: " .. tostring(G.ETA(700, 7)))
  check(near(TR.DetourDist({ mapID = 1424, x = 0.4, y = 0.4 }, { mapID = 1424, x = 0.6, y = 0.4 },
    { mapID = 1424, x = 0.5, y = 0.45 }, G.Vector), 100, 1e-3), "detour distance to the path")
  local order = TR.Route({ { key = "a", p = { mapID = 1424, x = 0.9, y = 0.4 } }, { key = "b", p = { mapID = 1424, x = 0.55, y = 0.4 } },
    { key = "c", p = { mapID = 1424, x = 0.7, y = 0.4 } } }, me, function(x, y) return (G.Vector(x, y)) end)
  check(order[1].key == "b" and order[2].key == "c" and order[3].key == "a", "nearest-neighbour route")
  check(TR.Bar(3, 6, 6) == "[===---]", "progress bar")

  -- ---------------------------------------------------------------- quest log + fake Questie
  local ZONE = "Contreforts de Hautebrande"
  M.player.zone = ZONE
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  M.facing = 0
  M.quests = {
    { header = ZONE },
    { title = "Sang de lion", level = 26, complete = false, xp = 2400,
      objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10, type = "item" } } },
    { title = "Les ours de Hautebrande", level = 25, complete = false, xp = 2000,
      objectives = { { text = "Ours enragé tué : 7/8", done = false, have = 7, need = 8 } } },
    { title = "Le courrier", level = 24, complete = true, xp = 1500, objectives = {} },
  }
  local spawns = {
    [7] = { name = "Lion des collines", spawns = { [267] = { { 55, 40 }, { 80, 80 } } } },
    [8] = { name = "Ours enragé", spawns = { [267] = { { 50, 30 } } } },
    [9] = { name = "Fermier", spawns = { [267] = { { 48, 41 } } } },
    [10] = { name = "Garde", spawns = { [267] = { { 51, 40 } } } },
  }
  local quests = {
    [1002] = { name = "Sang de lion", objectives = { nil, nil, { { 9001 } } }, nextQuestInChain = 501 },
    [1003] = { name = "Les ours de Hautebrande", objectives = { { { 8, "Ours" } } } },
    [1004] = { name = "Le courrier", finishedBy = { { 9 } }, nextQuestInChain = 501 },
    [501] = { name = "Aide au fermier", zoneOrSort = 267, requiredLevel = 24, questLevel = 26, startedBy = { { 10 } },
      requiredSourceItems = { 9100 } },
  }
  local fakeDB = {
    QuestPointers = { [501] = true },
    QueryQuestSingle = function(id, key) return quests[id] and quests[id][key] end,
    QueryNPCSingle = function(id, key) return spawns[id] and spawns[id][key] end,
    QueryItemSingle = function(id, key)
      if id == 9001 and key == "npcDrops" then return { 7 } end
      if id == 9001 and key == "name" then return "Sang de lion" end
    end,
    IsDoable = function() return true end,
  }
  local fakeZone = { GetUiMapIdByAreaId = function(_, a) return a == 267 and 1424 or nil end }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = fakeDB, ZoneDB = fakeZone } })
  local mapAPI = C_Map
  local oldArea = mapAPI.GetAreaInfo
  mapAPI.GetAreaInfo = function(id) return id == 267 and ZONE or "Ailleurs" end
  if modern then M.waypoints[1003] = { 1424, 0.5, 0.35 } end
  M.AddItem(9100, { name = "Peau tannée", stack = 20 })
  M.SetBagItem(3, 1, 9100, 2)

  Q.Update()
  check(Q.lastList and #Q.lastList == 3, "3 quests scanned")
  check(Q.lastQuestie and Q.lastQuestie.hereCount == 1, "Questie: 1 available quest here")

  -- ---------------------------------------------------------------- compass
  if C.ClearWaypoint then C.ClearWaypoint() end   -- the event storm may have clicked a minimap pin (manual target)
  C.Rebuild("init")
  local t1 = C.Current()
  check(t1 and t1.kind == "turnin" and t1.questID == 1004 and t1.npc == "Fermier", "nearest target = turn-in at the farmer: " .. tostring(t1 and t1.label))
  check(t1 and t1.label:find("Rendre « Le courrier » à Fermier"), "turn-in label")
  local ours
  for _, t in ipairs(C.targets) do if t.questID == 1003 then ours = t end end
  check(ours and ours.kind == "objective" and ours.src == (modern and "jeu" or "questie"), "objective source: " .. tostring(ours and ours.src))
  local lion
  for _, t in ipairs(C.targets) do if t.questID == 1002 then lion = t end end
  check(lion and near(lion.x, 0.55) and lion.label == "Sang de lion 2/10", "item objective → nearest drop spawn (Questie npcDrops)")
  C.Show(true)   -- the harness clicks every toolbar button, the compass one included (it toggles)
  local st = C.Refresh()
  check(st and st.dist and st.dist > 50 and st.dist < 80 and st.eta, "distance + ETA to the target: " .. tostring(st and st.dist))
  check(st and st.rel and st.rel > 0 and st.rel < pi, "farmer is to the west-ish (left): " .. tostring(st and st.rel))
  local fr = rawget(_G, "TreeAICompassFrame")
  check(fr and fr:IsShown(), "compass frame shown")
  C.WriteSummary()
  local sc = T:Summary().compass
  check(sc and sc.target and sc.target.questID == 1004 and sc.target.distText and sc.targets == 3, "summary.compass")

  -- cycle + quest target from a tracker click
  local t2 = C.Cycle(1)
  check(t2 and t2.key ~= t1.key, "/tai cible cycles")
  M.output = {}
  M.RunSlash("/tai cible")
  check(ctx.out("Cible : "), "slash /tai cible")
  C.SetQuestTarget(1002)
  check(C.Current().questID == 1002, "SetQuestTarget")

  -- objective completes → auto-switch to the next target
  M.quests[2].objectives[1] = { text = "Sang de lion : 10/10", done = true, have = 10, need = 10, type = "item" }
  M.quests[2].complete = true
  quests[1002].finishedBy = { { 9 } }
  Q.Update()
  C.Rebuild()
  check(C.Current().key ~= "obj:1002", "target left the finished objective")
  check(sugg("Cible suivante") or sugg("Objectif fini"), "announced the next target")

  -- custom waypoint
  M.output = {}
  M.RunSlash("/tai way 45,5 62 Mine")
  local cw = C.Current()
  check(cw and cw.kind == "custom" and near(cw.x, 0.455) and near(cw.y, 0.62) and cw.label == "Mine", "custom waypoint first")
  check(ctx.out("Point perso : 45.5, 62.0"), "waypoint echo")
  M.RunSlash("/tai way 200 5")
  check(ctx.out("Usage : /tai way"), "bad coordinates → usage")
  M.RunSlash("/tai way effacer")
  check(C.Current() and C.Current().kind ~= "custom", "waypoint cleared")

  -- instance: positions hidden → compass hidden
  M.instance = true
  C.Refresh()
  check(fr and not fr:IsShown(), "hidden in instances")
  M.instance = false
  C.Refresh()
  check(fr and fr:IsShown(), "back after the instance")

  -- ---------------------------------------------------------------- tracker
  local tabs = {}
  T.SetTabLines = function(_, id, lines) tabs[id] = lines end
  TR.Update()
  check(TR.items and #TR.items == 3 and TR.items[1].rank == 1, "tracker route built")
  check(TR.Push() == "tab" and tabs.quests and #tabs.quests >= 3, "tracker → quests tab via SetTabLines")
  local ready, clickLine
  for _, l in ipairs(tabs.quests) do
    if l.text:find("Prête à rendre") then ready = l end
    if l.questID == 1003 then clickLine = l end
  end
  check(ready and ready.prio == 2, "« prête à rendre » highlighted")
  check(ready and table.concat(ready.tooltip, "\n"):find("Aide au fermier"), "chain (tooltip): rendre ici → suite: " .. tostring(ready and ready.text))
  check(clickLine and table.concat(clickLine.tooltip, "\n"):find("%[=+%-*%]") and table.concat(clickLine.tooltip, "\n"):find("2 000 XP"),
    "objective bar + XP in the tooltip: " .. tostring(clickLine and clickLine.text))
  clickLine.onClick()
  check(C.Current().questID == 1003, "click a line = compass target")
  -- easy nearby + pick up on the way
  check(TR.easy and TR.easy[1] and TR.easy[1].title == "Aide au fermier" and TR.easy[1].giver == "Garde", "easy quest nearby")
  check(sugg("Fais aussi « Aide au fermier » : facile"), "easy quest suggestion")
  -- player far west, target east: the guard (0.51, 0.40) is on the way
  M.pos = { mapID = 1424, x = 0.40, y = 0.40 }
  M.RunSlash("/tai way 60 40 Est")
  TR.Update()
  check(TR.pickup and TR.pickup.title == "Aide au fermier", "« passe prendre … en chemin »")
  check(sugg("Prends « Aide au fermier » en chemin"), "pick-up suggestion")
  M.RunSlash("/tai way effacer")
  -- leaving the hub without the quest
  M.pos = { mapID = 1424, x = 0.51, y = 0.405 }
  TR.Update()
  M.pos = { mapID = 1424, x = 0.62, y = 0.40 }
  TR.Update()
  check(sugg("Tu pars sans prendre : « Aide au fermier »"), "hub warning")
  -- tooltip text for a mob dropping a needed item (quest 1002 done → reopen it)
  M.quests[2].objectives[1] = { text = "Sang de lion : 2/10", done = false, have = 2, need = 10, type = "item" }
  M.quests[2].complete = false
  Q.Update()
  TR.Update()
  check(TR.DropTip("Lion des collines") == "TreeAI : lâche Sang de lion 2/10", "drop tooltip: " .. tostring(TR.DropTip("Lion des collines")))
  TR.WriteSummary()
  local stS = T:Summary().tracker
  check(stS and #stS.route == 3 and stS.easyNearby[1] and stS.dropMobs == 1, "summary.tracker")
  -- fallback frame without the panel contract
  T.SetTabLines = nil
  check(TR.Push() == "frame" and rawget(_G, "TreeAITrackerFrame"):IsShown(), "fallback tracker frame")

  -- ---------------------------------------------------------------- predict
  P.ResetSession()
  -- kills from the combat log (player = source), loots from those mobs
  for k = 1, 4 do
    M.combatEvent = { M.now, "PARTY_KILL", false, M.player.guid, M.player.name, 0x511, 0,
      "Creature-0-0-0-0-77-" .. k, "Lion des collines", 0xa48, 0 }
    M.FireEvent("COMBAT_LOG_EVENT_UNFILTERED")
    M.combatEvent = nil
    M.loot = (k % 2 == 0) and { { name = "Sang de lion", count = 1, questItem = true }, { name = "Griffe" } } or { { name = "Griffe" } }
    M.FireEvent("LOOT_OPENED")
    M.FireEvent("LOOT_CLOSED")
    M.Advance(2)
  end
  check(P.State().killCount == 4, "4 kills counted from PARTY_KILL")
  local rate, n = P.DropRate("Lion des collines", "Sang de lion")
  check(near(rate, 0.5) and n == 4, "drop rate per mob per quest item: " .. tostring(rate))
  check(TR.DropTip("Lion des collines"):find("~50 %%"), "tooltip shows the measured rate")
  local fc = P.Update()
  local dropF
  for _, f in ipairs(fc) do if f.id == "drop:sang de lion" then dropF = f end end
  check(dropF and dropF.text == "Il te faut ~16 Lion des collines de plus pour 8 Sang de lion (taux 50 %)", "drop forecast: " .. tostring(dropF and dropF.text))
  local keepF
  for _, f in ipairs(fc) do if f.id:find("^keep:") then keepF = f end end
  check(keepF and keepF.text == "Garde Peau tannée : sert pour « Aide au fermier »", "keep item for an upcoming quest: " .. tostring(keepF and keepF.text))
  -- pure forecasts
  local list = P.Forecasts({ ammoPerMin = 15, slotsPerKill = 0.4, durPerHour = 10 },
    { ammo = 180, free = 10, level = 27, money = 18000, xp = 1, xpMax = 10, hearth = 360, durAvg = 30, durWorst = 25 })
  local byId = {}
  for _, f in ipairs(list) do byId[f.id] = f end
  check(byId.ammo and byId.ammo.text == "Munitions : vides dans ~12 min, achète 1000 au prochain vendeur" and byId.ammo.prio == 3, "ammo forecast: " .. tostring(byId.ammo and byId.ammo.text))
  check(byId.bags and byId.bags.text:find("^Sacs pleins dans ~25 monstres"), "bags forecast")
  check(byId.train and byId.train.text == "Formation niv 28 : ≈ 3 po 20 pa — il te manque 1 po 40 pa", "training forecast: " .. tostring(byId.train and byId.train.text))
  check(byId.hearth and byId.hearth.text == "Pierre de foyer prête dans 6 min", "hearth forecast")
  check(byId.repair and byId.repair.text:find("^Réparation bientôt") and byId.repair.prio == 2, "repair forecast")
  check(list[1].prio == 3, "urgent first")
  check(P.Money(32000) == "3 po 20 pa" and P.Money(14000) == "1 po 40 pa" and P.Money(250) == "2 pa 50", "money format")
  local L, c = P.NextTraining(26)
  check(L == 28 and c == 32000, "next training level")
  -- urgent forecast goes to « Maintenant »
  local oldF = P.Forecasts
  P.Forecasts = function() return { { id = "ammo", text = "Munitions : vides dans ~5 min, achète 1000 au prochain vendeur", prio = 3 } } end
  P.Update()
  check(sugg("Munitions : vides dans ~5 min"), "urgent forecast in « Maintenant »")
  P.Forecasts = oldF
  P.WriteSummary()
  local sp = T:Summary().predict
  check(sp and sp.rates and sp.rates.kills == 4 and sp.forecasts, "summary.predict")
  M.output = {}
  M.RunSlash("/tai prevoir")
  check(ctx.out("Prévisions"), "slash /tai prevoir")
  M.RunSlash("/tai suivi")
  M.RunSlash("/tai boussole yd")
  check(C.db.units == "yd", "/tai boussole yd")
  M.RunSlash("/tai boussole m")

  -- kills without the combat log (Forever removed CLEU for addons): XP message fallback
  P.ResetSession()
  check(P.ParseKillXP("Lion des collines meurt, vous gagnez 120 points d'expérience.") == "Lion des collines", "FR XP kill message")
  check(P.ParseKillXP("Bear dies, you gain 80 experience.") == "Bear", "EN XP kill message")
  M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Lion des collines meurt, vous gagnez 120 points d'expérience.")
  check(P.State().killCount == 1, "kill counted from the XP message")

  -- secret values (Forever, in combat): never compared / concatenated
  rawset(_G, "issecretvalue", function(v) return v == "SECRET" end)
  P.OnKill("SECRET", "SECRET")
  check(P.State().killCount == 1, "secret kill ignored")
  check(TR.DropTip("SECRET") == nil, "secret unit name ignored in tooltips")
  rawset(_G, "issecretvalue", nil)

  -- heavy planning only out of combat; compass hidden in combat (option)
  M.EnterCombat()
  local before = TR.items
  check(TR.Update() == before and TR.dirty, "tracker deferred in combat")
  C.Rebuild()
  check(C.dirty, "compass rebuild deferred in combat")
  C.Refresh()
  check(not fr:IsShown(), "compass hidden in combat")
  check(TR.DropTip("Lion des collines") == nil, "no tooltip work in combat")
  M.LeaveCombat()
  M.FireEvent("PLAYER_REGEN_ENABLED")
  M.Advance(3)
  check(not C.dirty and not TR.dirty, "re-planned after combat")

  -- LibQuestieDB (QuestieDB addon, contract 4) preferred over QuestieLoader
  local libCalls = 0
  local function getter(tbl) return { Get = function(id, key) libCalls = libCalls + 1 return tbl(id, key) end } end
  rawset(_G, "LibQuestieDB", { RequireContract = function(n) return n <= 4 end,
    Quest = getter(fakeDB.QueryQuestSingle), Npc = getter(fakeDB.QueryNPCSingle), Item = getter(fakeDB.QueryItemSingle),
    Object = getter(function() end) })
  local db = C.Questie()
  check(db and db.lib and db.QueryQuestSingle(1004, "name") == "Le courrier" and libCalls > 0, "LibQuestieDB adapter")
  C.Rebuild("force")
  check(#C.targets >= 3 and libCalls > 3, "targets built from LibQuestieDB")
  rawset(_G, "LibQuestieDB", nil)

  -- Shortest Path Forever: real travel time + « Aller là » / itinerary on the player's click (colon form)
  local spf = { est = 0, nav = {}, route = nil }
  local API = {}
  function API:Estimate(m1, x1, y1, m2, x2, y2)
    if self ~= API then error("colon expected") end
    spf.est = spf.est + 1
    return 95
  end
  function API:Navigate(owner, m, x, y, title) spf.nav = { owner, m, x, y, title } return true end
  function API:NavigateRoute(owner, stops) spf.route = stops return true end
  rawset(_G, "ShortestPathForever", { API = API })
  C.ResetSPF()
  C.SetQuestTarget(1003)
  local st2 = C.Compute()
  check(st2.spf and st2.eta == "1 min 35" and spf.est > 0, "ETA from Shortest Path Estimate: " .. tostring(st2.eta))
  M.output = {}
  M.RunSlash("/tai aller")
  check(spf.nav[1] == "TreeAI" and spf.nav[2] == 1424 and ctx.out("Shortest Path te guide"), "« Aller là » via SPF Navigate")
  TR.Update()
  check(TR.items[1].travel == 95, "tracker uses SPF travel time")
  M.RunSlash("/tai itineraire")
  local Rt = TreeAI.Route   -- a TreeAI plan is active → its order (Route:SPFStops), else the tracker's stops
  local want = (Rt and Rt:IsEnabled() and Rt:HasRoute() and #Rt:SPFStops() > 0) and #Rt:SPFStops() or #TR.RouteStops()
  check(spf.route and #spf.route == want and spf.route[1].map == 1424 and spf.route[1].title, "itinerary → NavigateRoute " .. tostring(spf.route and #spf.route) .. "/" .. tostring(want) .. " " .. tostring(spf.route and spf.route[1] and spf.route[1].map))
  rawset(_G, "ShortestPathForever", nil)
  -- TomTom when SPF is absent
  local tt = { added = {} }
  function tt:AddWaypoint(m, x, y, o) self.added[#self.added + 1] = { m, x, y, o } return #self.added end
  function tt:RemoveWaypoint() end
  rawset(_G, "TomTom", tt)
  check(C.Pin(C.Current()) == "tomtom" and tt.added[1][4].from == "TreeAI", "TomTom waypoint")
  rawset(_G, "TomTom", nil)
  -- Blizzard user waypoint (modern only), only through a click: shift-click a tracker line
  tabs = {}
  T.SetTabLines = function(_, id, lines) tabs[id] = lines end
  TR.Push()
  M.shift = true
  for _, l in ipairs(tabs.quests) do if l.questID == 1002 then M.Hw(l.onClick) end end
  M.shift = false
  T.SetTabLines = nil
  if modern then
    check(M.userWaypoint and M.userWaypoint.uiMapID == 1424 and M.superTrackUser == true, "shift-click → user waypoint + supertrack")
  end
  check(C.Current().questID == 1002, "shift-click also sets the compass target")

  -- no Blizzard quest-log selection / panel calls (3.8.2 taint rule)
  check(M.questSel == nil, "never selected a quest log entry")

  mapAPI.GetAreaInfo = oldArea
  rawset(_G, "QuestieLoader", nil)
end
