-- Scenario for Modules/SafePath.lua (agent SAFEPATH), run on both API flavours by tests/wow/test_safepath.py.
-- Fake QuestieDB NPC spawn map (fake data, fake names), fake Shortest Path. The addon never moves the player:
-- the scenario moves M.pos itself ("the player walks") and checks the compass waypoint chain.
-- lua5.1 tests/wow/harness.lua --allow LibQuestieDB --allow ShortestPathForever --scenario tests/wow/safepath_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local SP, C = T.SafePath, T.Compass
  check(SP and C, "SafePath + Compass loaded")
  if not (SP and C) then return end
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end
  local function fmt(w) local t = {} for _, p in ipairs(w) do t[#t + 1] = ("%.3f,%.3f"):format(p.x, p.y) end return table.concat(t, " ") end
  local function said(pat) for _, l in ipairs(M.output) do if tostring(l):find(pat) then return l end end end
  if os.getenv("SAFEPATH_DEBUG") then ctx.debug = true end

  -- ------------------------------------------------------------ levels (pure)
  check(SP.GreyLevel(26) == 19 and SP.GreyLevel(5) == 0 and SP.GreyLevel(50) == 39 and SP.GreyLevel(60) == 51, "grey levels")
  check(SP.Category(31, 26) == "red" and SP.Category(29, 26) == "orange" and SP.Category(27, 26) == "yellow"
    and SP.Category(22, 26) == "green" and SP.Category(19, 26) == "grey" and SP.Category(26, 26, true) == "elite"
    and SP.Category(-1, 26) == "red" and SP.Category(18, 26, true) == "grey", "difficulty categories")
  check(SP.Radius(26, 26) == 20 and SP.Radius(31, 26) == 25 and SP.Radius(26, 26, true) == 25
    and SP.Radius(10, 40) == 5 and SP.Radius(-1, 26) == 30, "aggro radius = 20 yd + level diff")
  check(SP.Danger(0.5, 0.5, 15, 26) == nil, "grey mob ignored")
  check(SP.Danger(0.5, 0.5, 28, 26, true, "x", { elite = false }) == nil, "option: elites off")

  -- ------------------------------------------------------------ exact paths on a small grid (pure)
  local A, B = { x = 0.1, y = 0.5 }, { x = 0.9, y = 0.5 }
  local base = { W = 1000, H = 1000, N = 20, from = A, to = B }
  local function plan(extra)
    local c = {}
    for k, v in pairs(base) do c[k] = v end
    for k, v in pairs(extra or {}) do c[k] = v end
    return SP.Plan(c)
  end
  local r0 = plan({ dangers = {} })
  check(#r0.waypoints == 1 and near(r0.waypoints[1].x, 0.9) and near(r0.direct, 800) and r0.text == "Route directe sûre (732 m)",
    "no danger → direct: " .. tostring(r0.text))
  local grey = SP.Danger(0.5, 0.5, 15, 26)
  local r1 = plan({ dangers = { grey } })
  check(#r1.waypoints == 1, "only grey mobs on the line → direct")

  local elite = SP.Danger(0.5, 0.48, 28, 26, true, "Ogre")
  local r2 = plan({ dangers = { elite } })
  if ctx.debug then print("r2", fmt(r2.waypoints), r2.text) end
  check(fmt(r2.waypoints) == "0.625,0.575 0.900,0.500", "elite → exact detour south: " .. fmt(r2.waypoints))
  check(r2.avoided.elite == 1 and r2.left == 0 and r2.exposure == 0 and r2.directExposure > 0, "1 elite avoided, path clean")
  check(r2.text == ("Détour sûr : +%d m, évite 1 élite"):format(math.floor((r2.length - 800) * 0.9144 + 0.5)), r2.text)
  check(r2.length > 800 and r2.length < 900, "short detour: " .. r2.length)

  -- wall of red mobs with a gap at y 0.65..0.8: the route must cross x = 0.5 inside the gap
  local wall = {}
  for _, y in ipairs({ 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.85, 0.9, 0.95 }) do
    wall[#wall + 1] = SP.Danger(0.5, y, 33, 26, false, "Brute")
  end
  local r3 = plan({ dangers = wall })
  if ctx.debug then print("r3", fmt(r3.waypoints), r3.text) end
  local cross
  local prev = A
  for _, p in ipairs(r3.waypoints) do
    if (prev.x - 0.5) * (p.x - 0.5) <= 0 and p.x ~= prev.x then
      local t = (0.5 - prev.x) / (p.x - prev.x)
      cross = prev.y + t * (p.y - prev.y)
    end
    prev = p
  end
  check(cross and cross > 0.66 and cross < 0.79, "route goes through the gap: y=" .. tostring(cross))
  check(fmt(r3.waypoints) == "0.525,0.675 0.900,0.500", "exact gap path: " .. fmt(r3.waypoints))
  check(r3.avoided.red == 1 and r3.left == 0, "1 red avoided (the one on the direct line): " .. r3.text)

  -- roads: a hub of friendly NPCs makes a slightly longer path cheaper (no danger)
  local g = SP.BuildGrid({ W = 1000, H = 1000, N = 20, roads = { { x = 0.5, y = 0.5 } } })
  check(g.mult[SP.CellOf(g, 0.5, 0.5)] == SP.ROAD_MULT and g.mult[SP.CellOf(g, 0.1, 0.1)] == 1 and g.minMult == SP.ROAD_MULT, "roads lower the cost")
  -- live danger: sector ahead of the player (facing east = -pi/2)
  local gl = SP.BuildGrid({ W = 1000, H = 1000, N = 50, live = { { x = 0.5, y = 0.5, facing = -math.pi / 2, w = 4 } } })
  check(gl.pen[SP.CellOf(gl, 0.53, 0.5)] > 0 and gl.pen[SP.CellOf(gl, 0.47, 0.5)] == 0 and gl.pen[SP.CellOf(gl, 0.5, 0.47)] == 0,
    "live danger only in the facing sector")
  -- death spot
  local gd = SP.BuildGrid({ W = 1000, H = 1000, N = 50, deaths = { { x = 0.3, y = 0.3 } } })
  check(gd.pen[SP.CellOf(gd, 0.3, 0.3)] > 0 and gd.pen[SP.CellOf(gd, 0.4, 0.3)] == 0, "death spot cost")

  -- ------------------------------------------------------------ in game: fake LibQuestieDB (contract 4)
  local npcs = {
    [101] = { name = "Ogre des collines", maxLevel = 28, rank = 1, spawns = { [267] = { { 50, 49 }, { 50, 51 } } } },
    [102] = { name = "Ogre chaman", maxLevel = 28, rank = 1, spawns = { [267] = { { 52, 50 } } } },
    [103] = { name = "Lapin", maxLevel = 5, spawns = { [267] = { { 35, 50 } } } },                       -- grey
    [104] = { name = "Garde amical", maxLevel = 50, rank = 1, friendlyToFaction = "A", spawns = { [267] = { { 65, 50 } } } },
    [105] = { name = "Loup lointain", maxLevel = 30, spawns = { [999] = { { 50, 50 } } } },             -- other zone
  }
  local ids = { 101, 102, 103, 104, 105 }
  local Npc = { Get = function(id, key) return npcs[id] and npcs[id][key] end, GetAllIds = function() return ids end }
  rawset(_G, "LibQuestieDB", { RequireContract = function(v) return v <= 4 end, Npc = Npc, Quest = { Get = function() end },
    Support = { Get = function(n) if n == "ZoneDB" then return { private = { areaIdToUiMapId = { [267] = 1424 } } } end end } })
  local db = SP.DB()
  check(db and db.src == "LibQuestieDB", "LibQuestieDB contract 4 adapter")
  local idx = SP.IndexMap(1424)
  check(#idx.hostile == 4 and #idx.friends == 1, "index: 4 hostile spawns (incl. grey), 1 friendly: " .. #idx.hostile)

  M.player.level = 26
  M.pos = { mapID = 1424, x = 0.2, y = 0.5 }
  M.facing = 0
  C.SetWaypoint(0.8, 0.5, "Camp")
  check(C.Current() and C.Current().kind == "custom", "compass target = custom point")
  M.output = {}

  -- in combat: refused, never computed
  M.inCombat = true
  SP.Toggle("")
  check(said("hors combat seulement") and not SP.job, "no computation in combat")
  M.inCombat = false

  -- coroutine budget: a clock that advances 0.5 ms per read → several frames needed
  local tick = 0
  local realClock = SP.Clock
  SP.Clock = function() tick = tick + 0.5 return tick end
  M.output = {}
  M.RunSlash("/tai sur")
  check(SP.job ~= nil, "/tai sur starts a background job")
  local frames = 0
  while SP.job and frames < 5000 do
    if frames == 3 then M.inCombat = true end
    local before = tick
    SP.Step()
    if frames == 3 then check(tick == before, "paused in combat (no work)") M.inCombat = false end
    frames = frames + 1
  end
  SP.Clock = realClock
  check(frames > 5 and not SP.job, "computed over several frames (≤ 2 ms each): " .. frames)
  local r = SP.route
  check(r and #r.waypoints >= 2, "route with waypoints")
  if not r then return end
  if ctx.debug then print("game", fmt(r.waypoints), r.text) end
  check(SP.last.avoided.elite == 3 and SP.last.left == 0, "avoids 3 elite spawns: " .. tostring(SP.last.text))
  check(said("Détour sûr : %+%d+ m, évite 3 élites"), "message « Détour sûr : +N m, évite 3 élites »")
  local cur = C.Current()
  check(cur and cur.label:find("^Route sûre 1/") and near(cur.x, r.waypoints[1].x), "compass fed with waypoint 1: " .. tostring(cur and cur.label))

  -- the player walks: within 15 yd of waypoint 1 → waypoint 2
  M.pos = { mapID = 1424, x = r.waypoints[1].x + 0.002, y = r.waypoints[1].y }
  M.Advance(0.6)
  check(r.idx == 2 and near(C.Current().x, r.waypoints[2].x), "auto-advance at 15 yd")
  -- jumping straight to the last waypoint → arrived, the custom point is back
  local last = r.waypoints[#r.waypoints]
  M.pos = { mapID = 1424, x = last.x, y = last.y }
  M.Advance(0.6)
  check(SP.route == nil and said("arrivé") and C.Current().label == "Camp", "arrived → compass back to « Camp »")

  -- options per danger level: elites off → direct
  SP.db.elite = false
  M.pos = { mapID = 1424, x = 0.2, y = 0.5 }
  SP.Toggle("80 50")
  SP.RunToEnd()
  check(SP.route and #SP.route.waypoints == 1 and SP.last.text:find("^Route directe sûre"), "elites off → direct route")
  SP.Toggle("off")
  check(SP.route == nil, "/tai sur off stops")
  SP.db.elite = true

  -- Shortest Path: its straight route crosses the elites → ours; a route that avoids → kept
  local spfRoute = { { x = 0.8, y = 0.5 } }
  rawset(_G, "ShortestPathForever", { API = { Estimate = function() return 60 end, GetRoute = function() return spfRoute end } })
  if TreeAI.SetGuideMode then TreeAI:SetGuideMode("auto") end   -- (3.9.8) SPF is opt-in: this part tests the opt-in
  if C.ResetSPF then C.ResetSPF() end
  SP.Toggle("80 50") SP.RunToEnd()
  check(SP.last and not SP.last.via and SP.last.avoided.elite == 3, "SPF straight route ignored (not safe)")
  SP.Stop(true)
  spfRoute = { { x = 0.4, y = 0.7 }, { x = 0.6, y = 0.7 }, { x = 0.8, y = 0.5 } }
  SP.Toggle("80 50") SP.RunToEnd()
  check(SP.last and SP.last.via and #SP.route.waypoints == 3 and near(SP.route.waypoints[1].y, 0.7), "SPF route kept when it already avoids: " .. tostring(SP.last and SP.last.text))
  SP.Stop(true)
  rawset(_G, "ShortestPathForever", nil)
  if TreeAI.SetGuideMode then TreeAI:SetGuideMode("treeai") end

  -- live danger from nameplates (hostile, non grey) + death spots
  M.units.nameplate1 = { name = "Ogre furieux", hostile = true, level = 33 }
  local e = SP.NoteLive()
  check(e and e.cat == "red" and e.mapID == 1424, "hostile red nameplate → live danger entry")
  M.units.nameplate1 = { name = "Lapin", hostile = true, level = 5 }
  SP.live = {}
  check(SP.NoteLive() == nil, "grey nameplate ignored")
  M.units.nameplate1 = nil
  local nd = #SP.char.deaths
  M.pos = { mapID = 1424, x = 0.33, y = 0.44 }
  M.FireEvent("PLAYER_DEAD")
  local dl = SP.char.deaths[#SP.char.deaths]
  check(#SP.char.deaths == math.min(10, nd + 1) and dl.mapID == 1424 and near(dl.x, 0.33), "death spot recorded")
  rawset(_G, "LibQuestieDB", nil)
  -- fallback: QuestieLoader._modules.QuestieDB (read-only)
  rawset(_G, "QuestieLoader", { _modules = {
    QuestieDB = { NPCPointers = { [101] = 1, [104] = 1 }, QueryNPCSingle = function(id, key) return npcs[id] and npcs[id][key] end },
    ZoneDB = { GetUiMapIdByAreaId = function(_, a) return a == 267 and 1424 or nil end } } })
  SP.cache = {}
  local i2 = SP.IndexMap(1424)
  check(SP.DB().src == "QuestieLoader" and #i2.hostile == 2 and #i2.friends == 1, "QuestieLoader fallback index")
  rawset(_G, "QuestieLoader", nil)
  SP.cache = {}
end
