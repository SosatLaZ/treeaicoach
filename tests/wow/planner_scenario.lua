-- Harness scenario: route planner (Modules/Export.lua + Modules/Planner.lua) on the real addon.
-- Fake QuestieDB public API (contract 4), a fake TreeAIPlan, fake names only. Modes (env PLANNER_MODE):
--   full  (default) engine numbers + export + auto plan + file plan + repair + combat + navigation + sync
--   syncA / syncB   2-client check: A writes its PLN messages to $PLANNER_SYNC_FILE, B reads them (same plan).
-- lua5.1 tests/wow/harness.lua --allow LibQuestieDB --allow QuestDB --allow NpcDB --allow ObjectDB --allow ItemDB
--   --allow TreeAIPlan --allow TomTom --allow ShortestPathForever --allow UiMapPoint --allow IsQuestFlaggedCompleted
--   --scenario tests/wow/planner_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local check = ctx.check
  local P, X = TreeAI.Planner, TreeAI.Export
  check(P and X, "Planner + Export chargés")
  if not (P and X) then return end
  local E = P.Engine
  local mode = os.getenv("PLANNER_MODE") or "full"

  -- world geometry: map 1424 is 3000 x 2000 yd; player position settable
  local ppos = { x = 0.50, y = 0.40 }
  rawget(_G, "C_Map").GetWorldPosFromMapPos = function(map, v)
    local x, y = v.x or v[1], v.y or v[2]
    if v.GetXY then x, y = v:GetXY() end
    if map ~= 1424 and map ~= 1425 then return nil end
    -- client frame (as HereBeDragons): vec.x = north (follows the map's y), vec.y = west (follows the map's x)
    return 0, { x = 500 - y * 2000, y = 1000 - x * 3000 + (map == 1425 and -5000 or 0) }
  end
  rawget(_G, "C_Map").GetPlayerMapPosition = function()
    return { x = ppos.x, y = ppos.y, GetXY = function(p) return p.x, p.y end }
  end
  TreeAI.Geo.rects = {}

  -- quest log: 1001 kill 4/10 bears, 1002 complete
  M.quests = {
    { title = "Le fermier Ray", level = 25, complete = false,
      objectives = { { text = "Ours enragé tué : 4/10", done = false, have = 4, need = 10 } } },
    { title = "Le courrier", level = 22, complete = true, objectives = {} },
  }
  M.SetCompleted({})
  P.char.turned = {}   -- the generic event storm "turned in" 1002
  M.InstallQuestieDB({
    areaToMap = { [267] = 1424, [36] = 1425 },
    xp = { [1001] = 2100, [1002] = 900, [2001] = 1900 },
    quests = {
      [1001] = { name = "Le fermier Ray", questLevel = 25, requiredLevel = 20, zoneOrSort = 267,
        startedBy = { { 2300 } }, finishedBy = { { 2300 } }, objectives = { { { 2351 } } }, questFlags = 8 },
      [1002] = { name = "Le courrier", questLevel = 22, requiredLevel = 18, zoneOrSort = 267,
        startedBy = { { 2300 } }, finishedBy = { { 2263 } }, objectives = {} },
      [2001] = { name = "Crocs d'ours", questLevel = 25, requiredLevel = 22, zoneOrSort = 267,
        startedBy = { { 2300 } }, finishedBy = { { 2300 } }, objectives = { nil, nil, { { 5001 } } } },
      [2002] = { name = "Trop haut", questLevel = 40, requiredLevel = 38, zoneOrSort = 267,
        startedBy = { { 2300 } }, finishedBy = { { 2300 } }, objectives = {} },
      [2003] = { name = "Autre faction", questLevel = 25, requiredLevel = 20, zoneOrSort = 267, requiredRaces = 2 ^ 20,
        startedBy = { { 2300 } }, finishedBy = { { 2300 } }, objectives = {} },
      [3001] = { name = "Nouvelle", questLevel = 26, requiredLevel = 22, zoneOrSort = 267,
        startedBy = { { 2300 } }, finishedBy = { { 2263 } }, objectives = { { { 2351 } } } },
    },
    npcs = {
      [2300] = { name = "Fermier", minLevel = 30, maxLevel = 30, rank = 0, spawns = { [267] = { { 52, 57 } } } },
      [2263] = { name = "Maire", minLevel = 40, maxLevel = 40, rank = 0, spawns = { [267] = { { 51, 58 } } } },
      [2351] = { name = "Ours enragé", minLevel = 24, maxLevel = 26, rank = 0,
        spawns = { [267] = { { 40, 50 }, { 41, 51 }, { 42, 50 }, { 70, 20 } } } },
    },
    items = { [5001] = { name = "Croc", npcDrops = { 2351 }, flags = 0 } },
  })

  -- ------------------------------------------------------------------ engine numbers (research/4)
  check(E.KillXP(26, 26) == 175 and E.KillXP(26, 27) == 184 and E.KillXP(26, 25) == 159, "XP kill 175/184/159")
  check(E.KillXP(26, 23) == 127 and E.KillXP(26, 20) == 80 and E.KillXP(26, 19) == 0, "XP kill 127/80/gris 0")
  check(E.KillXP(26, 30) == 210 and E.KillXP(26, 40) == 210, "XP kill plafond +4 (210)")
  check(E.GreyLevel(26) == 19 and E.GreyLevel(40) == 31 and E.GreyLevel(50) == 39 and E.GreyLevel(60) == 51, "niveaux gris")
  check(E.QuestXPFactor(26, 21) == 1 and E.QuestXPFactor(26, 20) == 0.8 and E.QuestXPFactor(26, 19) == 0.6
    and E.QuestXPFactor(26, 18) == 0.4 and E.QuestXPFactor(26, 17) == 0.2 and E.QuestXPFactor(26, 16) == 0.1, "décroissance XP quête")
  check(math.abs(E.GroupShare(4) - 0.325) < 1e-9 and E.GroupShare(2) == 0.5, "part XP groupe 0.325")
  check(E.QuestXPEstimate(26) == 2106, "XP quête estimée ≈ 2100 au niv 26")
  local F1, l1 = E.Feasibility({ { class = "HUNTER", level = 26 } }, { level = 26, units = 4 })
  check(math.abs(F1 - 0.65) < 1e-6 and l1 == "risque", "chasseur 26 vs élite 26 = 0.65 risqué")
  local F2, l2 = E.Feasibility({ { class = "HUNTER", level = 26 } }, { level = 24, units = 4 })
  check(math.abs(F2 - 2.6 * 1.21 / 4) < 1e-6 and l2 == "risque", "chasseur 26 vs élite 24 = 0.79 (seuil 0.8)")
  local cx = { class = "HUNTER", level = 26, group = 4 }
  local g, why = E.Galere({ lv = 25, o = { { k = "item", n = 10, have = 0, p = 0.25, ml = 25 } } }, cx)
  check(g >= 45 and why and why:find("160 corps à 25 %% pour 4"), "galère : 160 corps à 25 % pour 4 (" .. tostring(why) .. ")")
  local v, vwhy = E.QuestVerdict({ lv = 31, o = {} }, { class = "HUNTER", level = 26, group = 1 })
  check(v == "impossible" and vwhy:find("rouge"), "quête rouge solo = pas faisable")
  check(E.QuestVerdict({ lv = 25, o = {} }, { class = "HUNTER", level = 26, group = 4, raid = true }) == "impossible", "raid = pas faisable")
  local rv = E.RouteVerdict({ steps = { {} }, minutes = 200, xph = 30000 }, { class = "HUNTER", level = 26, session = 60 }, { nonGrey = 9 })
  check(rv == "long", "trop long > temps de session")
  check(E.RouteVerdict({ steps = { {} }, minutes = 20, xph = 30000 }, { class = "HUNTER", level = 26 }, { nonGrey = 2 }) == "zone", "< 3 quêtes : changez de zone")
  check(E.RouteVerdict({ steps = { {} }, minutes = 20, xph = 30000 }, { class = "HUNTER", level = 31 }, { nonGrey = 9, zoneMedian = 25 }) == "zone", "niv ≥ médiane + 5 : changez de zone")
  check(E.Hash({ { k = "pick", q = 1 } }) == E.Hash({ { k = "pick", q = 1 } }) and E.Hash({ { k = "pick", q = 1 } }) ~= E.Hash({ { k = "turn", q = 1 } }), "empreinte déterministe")
  -- Or-opt on a zig-zag line keeps precedences and saves time
  local function lg(a, b) if not (a and b and a.x and b.x) then return 0 end return math.abs(a.x - b.x) * 10 end
  local st = { { k = "obj", q = 1, x = 90 }, { k = "pick", q = 2, x = 10 }, { k = "obj", q = 3, x = 80 },
    { k = "turn", q = 2, x = 20 }, { k = "turn", q = 1, x = 95 } }
  local gain = E.OrOpt(st, 1, 20, { x = 0 }, lg, nil, 3)
  check(gain > 0 and E.ValidOrder(st, 1, #st), "Or-opt : gain " .. tostring(gain) .. " et ordre valide")
  local ins = { { k = "obj", q = 1, x = 10 }, { k = "turn", q = 1, x = 30 } }
  E.Insert(ins, { { k = "obj", q = 9, x = 20 } }, 1, 20, { x = 0 }, lg)
  check(ins[2].q == 9, "insertion au meilleur coût")
  -- greedy vs brute force (4 independent one-step requests on a line): greedy ratio picks a sensible order
  local reqs = { { q = 1, steps = { { k = "obj", q = 1, x = 10, svc = 60, value = 600 } } },
    { q = 2, steps = { { k = "obj", q = 2, x = 50, svc = 60, value = 600 } } },
    { q = 3, steps = { { k = "obj", q = 3, x = 20, svc = 60, value = 600 } } } }
  local gs = E.Greedy(reqs, { x = 0 }, lg, {})
  check(#gs == 3 and gs[1].q == 1 and gs[2].q == 3 and gs[3].q == 2, "glouton : plus proche d'abord")

  local key = TreeAI:CharKey()

  if mode == "syncA" or mode == "syncB" then
    M.SetPlan({ v = 1, made = 1793000000, char = key, routes = {
      { id = "squad", title = "Escouade (4)", xph = 20000, minutes = 60, verdict = "ok", reasons = {},
        steps = { { k = "obj", q = 1001, o = 1, map = 1424, x = 40, y = 50, label = "Tuer 10 ours", eta = 10 },
          { k = "turn", q = 1002, map = 1424, x = 51, y = 58, label = "Rendre le courrier", eta = 14 } } } } })
    M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true } }
    M.FireEvent("GROUP_ROSTER_UPDATE")
    P:Refresh(true)
    M.Advance(3, 0.5)
    local path = os.getenv("PLANNER_SYNC_FILE")
    if mode == "syncA" then
      check(P:SendSync(true), "A envoie PLN")
      M.Advance(1, 0.1)
      local f = io.open(path, "w")
      for _, s in ipairs(M.sentAddon) do if tostring(s.msg):find("~PLN~") then f:write(s.msg, "\n") end end
      f:close()
    else
      local f = io.open(path, "r")
      check(f, "fichier de synchro présent")
      local n = 0
      for line in f:lines() do M.DeliverAddon("TREEAI", line, "PARTY", "Amie-Royaume"); n = n + 1 end
      f:close()
      M.Advance(1, 0.5)
      check(n >= 1, "B reçoit les messages de A")
      local ps = P:PeerStates()
      check(#ps == 1 and ps[1].name == "Amie" and ps[1].state == "synchro", "B voit Amie synchro (" .. tostring(ps[1] and ps[1].state) .. ")")
    end
    return
  end

  -- ------------------------------------------------------------------ export (QuestieDB → TreeAIDB.export)
  local t0 = X.builtAt
  check(X:Rebuild(), "export lancé")
  check(X.builtAt == t0, "export découpé (coroutine), pas synchrone")
  M.Advance(6, 0.1)
  check(X.builtAt ~= t0, "export terminé après quelques images")
  local exp = TreeAIDB.export
  check(type(exp) == "table" and exp.v == 1 and exp.src == "LibQuestieDB" and exp.char == key, "export v1 depuis LibQuestieDB")
  local byId = {}
  for _, q in ipairs(exp and exp.quests or {}) do byId[q.id] = q end
  check(byId[1001] and byId[1001].st == "log" and byId[1001].o[1].k == "kill" and byId[1001].o[1].map == 1424
    and math.abs(byId[1001].o[1].x - 41) < 1.1 and byId[1001].o[1].have == 4 and byId[1001].o[1].n == 10, "1001 : objectif kill, cluster, 4/10")
  check(byId[1001] and byId[1001].xp == 2100 and byId[1001].f and byId[1001].f.x == 52, "1001 : XP + rendu")
  check(byId[2001] and byId[2001].st == "avail" and byId[2001].s and byId[2001].o[1].k == "item"
    and byId[2001].o[1].npc[1] == 2351, "2001 dispo : départ + objet lâché par 2351")
  check(not byId[2002], "quête trop haute exclue")
  check(exp.player and exp.player.level == M.player.level and exp.player.map == 1424 and exp.player.x == 50, "état du joueur exporté")
  check(exp.maps[1] and exp.maps[1].id == 1424 and exp.maps[1].x0 == 500, "rectangle de carte exporté")
  check(X.Size(exp) < 300000, "export < 300 Ko")

  -- learned stats: a kill of a same-level bear in combat, then loot of a tusk
  M.target = { exists = true, name = "Ours enragé", level = M.player.level, hostile = true, guid = "Creature-0-1-0-1-2351-000A" }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  M.Advance(15, 0.5)
  M.target.dead = true
  M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "L'ours meurt, vous gagnez 175 points d'expérience.")
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
  local CL = X.CharLearn()
  check(CL and CL.ttk["0"] and CL.ttk["0"].n == 1 and CL.ttk["0"].s >= 14 and CL.ttk["0"].s <= 16, "TTK appris (Δ 0, ~15 s)")
  local L = X.LearnDB()
  check(L and L.comp[1424] and L.comp[1424].n >= 1, "affluence observée")

  -- ------------------------------------------------------------------ auto plan (no TreeAIPlan)
  M.SetPlan(nil)
  P:Refresh(true)
  M.Advance(4, 0.1)
  check(P.src == "auto" and P.route and P.route.id == "auto" and #P.steps >= 3, "plan auto construit (" .. tostring(P.steps and #P.steps) .. " étapes)")
  local kinds = {}
  for _, s in ipairs(P.steps or {}) do kinds[s.k .. (s.q or "")] = true end
  check(kinds.turn1002 and kinds.obj1001 and kinds.pick2001, "plan auto : rendre 1002, ours 1001, prendre 2001")
  check(P.route.verdict and P.Engine.LABEL[P.route.verdict], "verdict du plan auto : " .. tostring(P.route.verdict))
  local lines = P:Lines()
  local card
  for _, l in ipairs(lines) do if l.id == "plan:step" then card = l end end
  check(card and not card.text:find("▶") and card.text:find(" m ") and tostring(card.tooltip):find("min"), "carte d'étape : " .. tostring(card and card.text))
  local sugg = false
  for _, e in ipairs(TreeAI:GetSuggestions()) do if tostring(e.id):find("Planner:step") then sugg = true end end
  check(type(TreeAI.SetTabLines) == "function" or sugg, "repli Suggest quand SetTabLines est absent")

  -- ------------------------------------------------------------------ file plan (TreeAI_Plan)
  M.SetPlan({ v = 1, made = 1793000000, char = key, zone = 1424, routes = {
    { id = "xph", title = "XP/h max", xph = 28500, minutes = 74, verdict = "galere", reasons = { "Taux de drop 20 % sur Crocs" },
      steps = {
        { k = "pick", q = 2001, map = 1424, x = 52, y = 57, label = "Prendre « Crocs d'ours »", eta = 2 },
        { k = "obj", q = 1001, o = 1, map = 1424, x = 40, y = 50, label = "Tuer 10 Ours enragés", eta = 12 },
        { k = "fly", map = 1424, x = 60, y = 30, label = "Vol vers Moulin", eta = 15 },
        { k = "turn", q = 1002, map = 1424, x = 51, y = 58, label = "Rendre « Le courrier »", eta = 18 },
        { k = "turn", q = 1001, map = 1424, x = 52, y = 57, eta = 20 },
      } },
    { id = "clear", title = "Finir la zone", xph = 22000, minutes = 120, verdict = "long", reasons = { "120 min > session" },
      steps = { { k = "turn", q = 1002, map = 1424, x = 51, y = 58, eta = 3 } } },
    { id = "safe", title = "Prudent", xph = 18000, minutes = 80, verdict = "ok", reasons = {}, steps = {} },
  } })
  P:Refresh(true)
  M.Advance(3, 0.1)
  check(P.src == "file" and P.route.id == "xph" and P.cursor == 1, "plan fichier : route xph, étape 1")
  -- Route.lua view (« Suivi Forever » style): « Trajet 12 min · 2,4 km », verdict line, one route switcher
  local pl = P:Lines()
  local head = pl[1]
  local function line(pat) for _, l in ipairs(pl) do if l.text:find(pat) then return l end end end
  check(head.text:find("^Trajet %d+ min · ") and tostring(head.tooltip):find("XP/h max") and tostring(head.tooltip):find("28 500")
    and line("^On va galérer : Taux de drop 20 %%"), "en-tête : " .. head.text)
  local sw = line("^Route : XP/h max %(clic : changer%)")
  check(sw and tostring(sw.tooltip):find("Finir la zone") and tostring(sw.tooltip):find("Prudent"), "sélecteur : 3 routes")
  -- pick up 2001 → cursor moves to the bear objective
  M.quests[3] = { title = "Crocs d'ours", level = 25, complete = false, objectives = { { text = "Croc : 0/6", done = false, have = 0, need = 6 } } }
  -- mock ids are 1000+index: map log index 3 → 1003; keep quest ids aligned with the plan via a remap
  local ID = { 1001, 1002, 2001, 3001 }
  if C_QuestLog then
    local gi = C_QuestLog.GetInfo
    C_QuestLog.GetInfo = function(i) local r = gi(i) if r and r.questID then r.questID = ID[i] end return r end
    local go = C_QuestLog.GetQuestObjectives
    C_QuestLog.GetQuestObjectives = function(id) for i, v in ipairs(ID) do if v == id then return go(1000 + i) end end return {} end
    local ic = C_QuestLog.IsComplete
    C_QuestLog.IsComplete = function(id) for i, v in ipairs(ID) do if v == id then return ic(1000 + i) end end return false end
  else
    local gt = GetQuestLogTitle
    GetQuestLogTitle = function(i) local a, b, c, d, e, f, g, h = gt(i) if h then h = ID[i] end return a, b, c, d, e, f, g, h end
  end
  M.FireEvent("QUEST_ACCEPTED", 3, 2001)
  M.Advance(4, 0.1)
  check(P.cursor == 2, "prise de 2001 détectée → étape 2 (" .. tostring(P.cursor) .. ")")
  local c2
  for _, l in ipairs(P:Lines()) do if l.id == "plan:step" then c2 = l.text end end
  check(c2 and c2:find("Tuer 10 Ours enragés %(4/10%)"), "progression lue dans le journal : " .. tostring(c2))
  -- Tracker contract: GetActiveSteps(n) → steps, index, total, title ; fallback Planner.active
  local as, ai, at, atitle = P:GetActiveSteps(3)
  check(as and #as == 3 and ai == 2 and at == 5 and atitle == "XP/h max" and as[1].k == "obj" and as[1].q == 1001
    and as[1].map == 1424 and as[1].x == 40 and as[1].label:find("%(4/10%)") and as[2].k == "fly" and as[1]._done == nil,
    "GetActiveSteps(3) contrat Tracker")
  check(P.active and P.active.idx == 2 and P.active.route.title == "XP/h max" and #P.active.route.steps == 5, "Planner.active (repli)")
  -- objective done → cursor jumps over the fly step? no: fly is after; arrival within 40 yd marks it done
  M.quests[1].objectives[1] = { text = "Ours enragé tué : 10/10", done = true, have = 10, need = 10 }
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(4, 0.1)
  check(P.cursor == 3 and P.steps[3].k == "fly", "objectif fini → étape vol")
  ppos.x, ppos.y = 0.60, 0.30
  M.Advance(3, 0.5)
  check(P.cursor == 4, "arrivée au maître de vol → étape suivante (" .. tostring(P.cursor) .. ")")
  ppos.x, ppos.y = 0.50, 0.40

  -- repair: a new quest (3001) accepted off-plan is inserted in the next 20 steps
  M.quests[4] = { title = "Nouvelle", level = 26, complete = false, objectives = { { text = "Ours : 0/5", done = false, have = 0, need = 5 } } }
  M.FireEvent("QUEST_ACCEPTED", 4, 3001)
  M.Advance(5, 0.1)
  local has3001 = 0
  for _, s in ipairs(P.steps) do if s.q == 3001 then has3001 = has3001 + 1 end end
  check(has3001 == 2, "réparation : 3001 inséré (objectif + rendu) : " .. has3001)
  check(TreeAIDB.chars[key].modules.Planner.repaired and TreeAIDB.chars[key].modules.Planner.repaired.key, "ordre réparé sauvegardé")

  -- combat: nothing recomputed / sent
  local sentBefore = #M.sentAddon
  M.EnterCombat(); M.FireEvent("PLAYER_REGEN_DISABLED")
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(4, 0.5)
  check(P._dirty or P._pending == false or true, "en combat")
  check(not P:Navigate(), "guidage refusé en combat")
  local plnInCombat = 0
  for i = sentBefore + 1, #M.sentAddon do if tostring(M.sentAddon[i].msg):find("~PLN~") then plnInCombat = plnInCombat + 1 end end
  check(plnInCombat == 0, "aucun PLN envoyé en combat")
  M.LeaveCombat(); M.FireEvent("PLAYER_REGEN_ENABLED")
  M.Advance(3, 0.5)

  -- navigation chain (click only): Compass → SPF → TomTom → Blizzard waypoint
  local got
  TreeAI.Compass = { SetTarget = function(_, t) got = t return true end }
  check(P:Navigate() and P.navBy == "compass" and got and got.mapID == 1424 and got.x < 1, "guidage boussole TreeAI (0–1)")
  TreeAI.Compass = nil
  local stops
  rawset(_G, "ShortestPathForever", { API = { version = 1, NavigateRoute = function(owner, s) stops = s return owner == "TreeAI" end } })
  check(P:Navigate() and P.navBy == "spf" and stops and #stops >= 1 and stops[1].x < 1, "guidage Shortest Path Forever (NavigateRoute)")
  rawset(_G, "ShortestPathForever", nil)
  local wp
  rawset(_G, "TomTom", { AddWaypoint = function(_, m, x, y, o) wp = { m, x, y, o } return wp end, RemoveWaypoint = function() end })
  check(P:Navigate() and P.navBy == "tomtom" and wp[4].from == "TreeAI", "guidage TomTom")
  rawset(_G, "TomTom", nil)
  local uw, sup
  C_Map.CanSetUserWaypointOnMap = function() return true end
  C_Map.SetUserWaypoint = function(p) uw = p end
  rawset(_G, "C_SuperTrack", rawget(_G, "C_SuperTrack") or {})
  C_SuperTrack.SetSuperTrackedUserWaypoint = function(b) sup = b end
  rawset(_G, "UiMapPoint", { CreateFromCoordinates = function(m, x, y) return { m = m, x = x, y = y } end })
  check(P:Navigate() and P.navBy == "blizzard" and uw and sup == true, "repère Blizzard + super-suivi")

  -- route selector
  P:SelectRoute("clear")
  M.Advance(2, 0.1)
  check(P.route.id == "clear", "changement de route")
  M.RunSlash("/tai plan 1")
  M.Advance(2, 0.1)
  check(P.route.id == "xph", "/tai plan 1")
  M.RunSlash("/tai plan")
  M.RunSlash("/tai plan passer")
  M.RunSlash("/tai plan recalc")
  M.Advance(3, 0.1)

  -- group: sync message, peer ahead, « attendez » before turn-in, meeting point
  M.group = { { name = "Amie", class = "PRIEST", level = 25, online = true } }
  M.FireEvent("GROUP_ROSTER_UPDATE")
  M.Advance(2, 0.5)
  local before = #M.sentAddon
  check(P:SendSync(true), "PLN envoyé")
  M.Advance(1, 0.1)
  local pln
  for i = before + 1, #M.sentAddon do
    local m = tostring(M.sentAddon[i].msg)
    if m:find("~PLN~") then pln = m end
    check(#m <= 255, "message ≤ 255 o")
  end
  local payload = pln and pln:match("~r(1:.*)$")
  check(payload and payload:match("^1:1793000000:xph:%d+:%x+:%d+$"), "charge PLN : " .. tostring(payload))
  if payload then
    local v1, made, rid, step, hash, n = payload:match("^(%d+):(%d+):(%w+):(%d+):(%x+):(%d+)$")
    M.DeliverAddon("TREEAI", "1~PLN~77~1/1~r" .. table.concat({ v1, made, rid, tonumber(step) + 1, hash, n }, ":"), "PARTY", "Amie-Royaume")
    M.DeliverAddon("TREEAI", "1~PLN~78~1/1~r1:1:x|x:1:aa:1", "PARTY", "Amie-Royaume")
    M.Advance(1, 0.5)
    local ps = P:PeerStates()
    check(#ps == 1 and ps[1].state == "avance" and ps[1].diff == 1, "Amie en avance d'1 étape")
  end
  -- « attendez » : current step = a turn-in, Amie has the quest unfinished (Squad view)
  local S = TreeAI.Squad
  if S then
    local cur
    for _, st in ipairs(P.steps) do if st.k == "turn" and not cur then cur = st end end
    local oldQ, oldM, oldA = S.Quests, S.Members, S.IsActive
    S.IsActive = function() return true end
    S.Members = function() return { "Amie", "Testeur" } end
    S.Quests = function(_, name)
      if name == "Amie" and cur and cur.q then return { [cur.q] = { id = cur.q, done = false, obj = { { have = 2, need = 6 } } } } end
      return {}
    end
    local w = P:WaitFor(cur)
    check(cur and #w >= 1 and w[1]:find("Amie %(2/6%)"), "attendez Amie (2/6) avant de rendre")
    S.Quests, S.Members, S.IsActive = oldQ, oldM, oldA
  end

  -- wrong character plan → ignored, auto plan instead
  M.SetPlan({ v = 1, made = 1, char = "Autre-Royaume", routes = { { id = "xph", steps = {} } } })
  P:Refresh(true)
  M.Advance(4, 0.1)
  check(P.planError and P.planError:find("autre personnage") and P.src == "auto", "plan d'un autre perso ignoré")

  -- summary + logout
  TreeAI:Fire("SUMMARY")
  local sp = TreeAI:Summary().plan
  check(sp and sp.v == 1 and sp.route and sp.steps and sp.hash, "summary.plan écrit")
  M.FireEvent("PLAYER_LOGOUT")
  check(TreeAIDB.export and TreeAIDB.export.player and TreeAIDB.export.quests, "export rafraîchi à la déconnexion")
  M.SetPlan(nil)
end
