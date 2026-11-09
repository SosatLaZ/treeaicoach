-- Scenario ROUTEFIX (4.1) : « le chemin le plus court qui marche réellement », run by tests/wow/test_routefix.py.
--  1. ordre exact (Held-Karp) contre la force brute, même modèle de coût (précédences, journal, foyer une seule fois) ;
--  2. cas réalistes sur les VRAIES tables de voyage (Kalimdor Horde : Orgrimmar, La Croisée, Pitons-du-Tonnerre,
--     Serres-Rocheuses, Mille pointes, Orneval ; vols connus / inconnus, foyer prêt / en recharge, zeppelin) : ancien
--     moteur (glouton + 2-opt) contre le nouveau, contre l'optimum ; bon sens (pas de vol quand marcher est plus court,
--     vols seulement entre maîtres connus, foyer une fois) — chiffres imprimés « AUDIT … » pour research/route41.md ;
--  3. chemin appris (PathLearn) : trajet en L / rampe de mesa, temps ±15 % du réel, point de passage, boussole « par le
--     chemin », traînée de la minimap ;
--  4. stabilité d'un calcul à l'autre, Stops(1) = début de Stops(64), foyer au meilleur trajet ;
--  5. Suivi Forever : choix « ordre de TreeAI » → notre ordre renvoyé après chaque trajet refait, sans clic.
-- Faux lieux de quêtes, faux noms ; aucune coordonnée inventée dans l'addon (seulement dans ce test).
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local R, Zp, P = T.Route, T.ZonePlan, T.Planner
  check(R and R.Solve and R.BruteForce and Zp and Zp.Engine and T.PathLearn, "Route solver + ZonePlan engine + PathLearn loaded")
  if not (R and R.Solve and Zp and T.PathLearn) then return end
  local N = Zp.Engine
  check(not N._routefix and not R.InstallEngine, "(4.2) no bridge: ZonePlan calls the Route solver directly (exact ≤ 10 stops)")
  local function say(...) local t = {} for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end io.stdout:write("AUDIT " .. table.concat(t, " ") .. "\n") end
  local abs, sqrt, floor = math.abs, math.sqrt, math.floor

  -- ------------------------------------------------------------ 1. exact vs brute force (random, deterministic)
  local seed = 12345
  local function rnd() seed = (seed * 1103515245 + 12345) % 2147483648 return seed / 2147483648 end
  local worst, cases = 0, 0
  for trial = 1, 40 do
    local n = 4 + trial % 6                                  -- 4..9 stops
    local nodes, pts = {}, {}
    local q = 0
    while #nodes < n do
      q = q + 1
      local chain = (rnd() < 0.5 and #nodes + 3 <= n) and 3 or 1
      for r = 1, chain do
        local rank = chain == 1 and (rnd() < 0.5 and 3 or 2) or r
        nodes[#nodes + 1] = { qs = { [q] = rank }, x = rnd() * 1000, y = rnd() * 1000, steps = { { k = rank == 1 and "pick" or (rank == 3 and "turn" or "obj") } } }
      end
    end
    for i, nd in ipairs(nodes) do pts[i] = nd end
    local ord = {}
    for i = 1, n do ord[i] = i end
    local D = { [0] = {} }
    for i = 1, n do
      D[0][i] = sqrt((pts[i].x - 500) ^ 2 + (pts[i].y - 500) ^ 2) / 7
      D[i] = {}
      for j = 1, n do if i ~= j then D[i][j] = sqrt((pts[i].x - pts[j].x) ^ 2 + (pts[i].y - pts[j].y) ^ 2) / 7 end end
    end
    if trial % 3 == 0 then                                   -- hearth: inn at (0, 0), 15 s
      D.hs = {}
      for i = 1, n do D.hs[i] = 15 + sqrt(pts[i].x ^ 2 + pts[i].y ^ 2) / 7 end
    end
    local o = (trial % 4 == 0) and { cap = 2, logCount = 1 } or {}
    local bs, bc = R.BruteForce(nodes, ord, D, o)
    local s, c = R.Solve(nodes, ord, D, o)
    if bs then
      cases = cases + 1
      check(s and N.Valid(nodes, s), "trial " .. trial .. ": solver order valid")
      local ratio = s and (c / math.max(1e-9, bc)) or 99
      if ratio > worst then worst = ratio end
      check(s and c <= bc * 1.02 + 1e-6 and abs(R.OrderCost(D, s) - c) < 1e-6, "trial " .. trial .. " (n=" .. n .. "): " .. tostring(c) .. " vs optimum " .. tostring(bc))
      if n == 9 then
        local hs2, hc = R.Solve(nodes, ord, D, { cap = o.cap, logCount = o.logCount, exactN = 0 })
        check(hs2 and N.Valid(nodes, hs2) and hc <= bc * 1.15, "heuristic (n=9) within 15 %: " .. tostring(hc) .. " / " .. tostring(bc))
      end
    end
  end
  say("exact-vs-brute", cases, "cases, worst ratio", string.format("%.4f", worst))
  check(cases >= 30 and worst <= 1.02, "≥ 30 brute-force cases, worst ratio " .. worst)
  -- stability rule (4.2 ROUTE42): gain ≥ min(10 %, 30 s)
  check(R.Choose(975, 1000) == "old" and R.Choose(970, 1000) == "new" and R.Choose(92, 100) == "old" and R.Choose(1, nil) == "new",
    "stability: new order only for a gain ≥ min(10 %, 30 s)")

  -- ------------------------------------------------------------ 2. realistic cases on the shipped travel tables
  local TX = T.Data.Travel.taxi
  local function fm(id, dn, dw) local r = TX[id] return { c = r[2], n = r[3] + (dn or 0), w = r[4] + (dw or 0) } end
  local ORG, XR, TB, SUN, FREE, SPLIN, TAU = 23, 25, 22, 29, 30, 61, 77
  local function buildD(g, stops, start, bind)
    local D = { [0] = {} }
    for i, a in ipairs(stops) do
      D[0][i] = (R.FastestWorld(g, start, a))
      D[i] = {}
      for j, b in ipairs(stops) do if i ~= j then D[i][j] = (R.FastestWorld(g, a, b)) end end
    end
    if bind then
      D.hs = {}
      for i, a in ipairs(stops) do D.hs[i] = g.hs + (R.FastestWorld(g, bind, a)) end
    end
    return D
  end
  -- the old engine (4.1.0): greedy + 2-opt/Or-opt over a matrix where the hearth was folded into EVERY leg (« Rendre
  -- mes quêtes ») or into the first leg only (other modes), ordered then evaluated with the hearth once (real cost)
  local function oldOrder(nodes, D, hsMode)
    local leg = function(a, b) return D[a.i][b.i] end
    local first
    if D.hs then
      if hsMode == "any" then leg = function(a, b) return math.min(D[a.i][b.i], D.hs[b.i]) end end
      first = function(nd) return math.min(D[0][nd.i], D.hs[nd.i]) end
    end
    local start = { i = 0 }
    for i, nd in ipairs(nodes) do nd.i = i end
    D[0][0] = 0
    local ord = N.Greedy(nodes, start, function(a, b) if a.i == 0 then return D[0][b.i] end return leg(a, b) end, { first = first })
    local Dm = N.Matrix(nodes, ord, start, function(a, b) if a.i == 0 then return D[0][b.i] end return leg(a, b) end, first)
    N.Improve(nodes, ord, Dm, nil, 3)
    return ord
  end
  local function node(q, rank, p) return { qs = { [q] = rank }, x = 1, steps = { { k = rank == 1 and "pick" or (rank == 3 and "turn" or "obj") } }, value = 1000, svc = 15, pos = p } end
  local function run(name, g, stops, nodes, start, bind, hsMode)
    local D = buildD(g, stops, start, bind)
    local ord = {}
    for i = 1, #nodes do ord[i] = i end
    local bs, bc = R.BruteForce(nodes, ord, D, {})
    local s, c, at = R.Solve(nodes, ord, D, {})
    local old = oldOrder(nodes, D, hsMode)
    local oc = R.OrderCost(D, old)
    check(s and c <= bc * 1.02 + 1e-6, name .. ": TreeAI " .. tostring(c) .. " ≤ optimum × 1.02 (" .. tostring(bc) .. ")")
    say(name, "| optimum", floor(bc + 0.5), "s | 4.1.1", floor(c + 0.5), "s | 4.1.0", floor(oc + 0.5), "s (+" .. floor((oc - bc) / bc * 100 + 0.5) .. " %)",
      "| order", table.concat(s, ","), "| old", table.concat(old, ","), at and ("| foyer avant arrêt " .. s[at]) or "")
    return s, c, oc, at
  end
  local known = { ORG, XR, TB, SUN, FREE, SPLIN, TAU }
  local g = R.BuildHubs("Horde", known, { speed = 11.2 })
  local gNoFly = R.BuildHubs("Horde", { XR }, { speed = 11.2 })
  -- case A: 8 finished quests in 5 zones, start at La Croisée, inn Orgrimmar, hearth ready (« Rendre mes quêtes »)
  local A = { fm(XR, 60, 40), fm(XR, -90, 30), fm(TB, 80, -40), fm(SUN, 120, 60), fm(FREE, 150, -100), fm(SPLIN, -80, 90), fm(ORG, 50, 20), fm(ORG, -60, -30) }
  local nA = {}
  for i = 1, #A do nA[i] = node(100 + i, 3) end
  local sA, cA, oA = run("A rendre 8 quêtes, 5 zones, foyer prêt", g, A, nA, fm(XR, 10, 10), fm(ORG, 40, 0), "any")
  check(cA <= oA + 1e-6, "A: never worse than the 4.1.0 order")
  -- case B: same, hearth on cooldown
  run("B rendre 8 quêtes, foyer en recharge", g, A, nA, fm(XR, 10, 10), nil)
  -- case C: same, flight paths unknown (only La Croisée)
  run("C rendre 8 quêtes, vols inconnus", gNoFly, A, nA, fm(XR, 10, 10), fm(ORG, 40, 0), "any")
  -- case D: a zone run with chains (pick → kill → turn-in) in Mille pointes + a turn-in at Thunder Bluff, hearth ready
  local Dp = { fm(FREE, 40, 0), fm(FREE, 600, 300), fm(FREE, 40, 0), fm(FREE, -500, 400), fm(FREE, 45, 5), fm(FREE, 700, -200), fm(TB, 60, 0), fm(FREE, 300, 300), fm(FREE, 42, 2) }
  local nD = { node(1, 1), node(1, 2), node(1, 3), node(2, 2), node(2, 3), node(3, 2), node(4, 3), node(5, 2), node(5, 3) }
  local sD = run("D Mille pointes, chaînes + remise aux Pitons", g, Dp, nD, fm(FREE, 0, 0), fm(ORG, 40, 0), "first")
  check(N.Valid(nD, sD), "D: chains in order (pick before kill before turn-in)")
  -- case E: the zeppelin — a turn-in in Tirisfal (other continent) among Durotar stops
  local E = { fm(ORG, 100, 50), { c = 0, n = 2062.4 + 300, w = 293.0 + 200 }, fm(XR, 50, 50), fm(ORG, -200, 100) }
  local nE = { node(11, 3), node(12, 3), node(13, 3), node(14, 3) }
  local sE = run("E zeppelin pour Tirisfal", g, E, nE, fm(ORG, 0, 0), nil)
  local s12, l12 = R.FastestWorld(g, E[1], E[2])
  local modes = {}
  for _, lg in ipairs(l12) do modes[#modes + 1] = lg.mode end
  check(table.concat(modes, ","):find("zeppelin") and s12 > 178, "Orgrimmar → Tirisfal by zeppelin, wait included: " .. table.concat(modes, ",") .. " " .. tostring(s12))
  check(sE[#sE] == 2 or sE[1] == 2, "E: the other continent is visited once, at an end of the route (" .. table.concat(sE, ",") .. ")")
  -- case F: one turn-in 150 yd away + 4 at Orgrimmar (inn), from La Croisée — 4.1.0 dropped the hearth entirely
  -- (« un arrêt à moins de 5 min ») and flew; now: hand in here, THEN hearth
  local F = { fm(XR, 80, 60), fm(ORG, 60, 30), fm(ORG, -40, 80), fm(ORG, 90, -60), fm(ORG, -100, -20) }
  local nF = { node(21, 3), node(22, 3), node(23, 3), node(24, 3), node(25, 3) }
  local DF = buildD(g, F, fm(XR, 10, 10), fm(ORG, 40, 0))
  local ordF = { 1, 2, 3, 4, 5 }
  local sF, cF, atF = R.Solve(nF, ordF, DF, {})
  local hsF = DF.hs
  DF.hs = nil
  local oldF = oldOrder(nF, DF)
  local ocF = R.OrderCost(DF, oldF)
  DF.hs = hsF
  check(sF[1] == 1 and atF == 2 and cF < ocF, "F: nearby turn-in first, then the hearthstone (" .. tostring(cF) .. " < " .. tostring(ocF) .. ")")
  say("F 1 remise ici + 4 à Orgrimmar (auberge) | 4.1.1", floor(cF + 0.5), "s (foyer après l'arrêt 1) | 4.1.0", floor(ocF + 0.5), "s (foyer retiré, vol) (+"
    .. floor((ocF - cF) / cF * 100 + 0.5) .. " %)")
  -- time model: level 30 with a mount = mounted speed (11.2 yd/s, not the old 14 default nor walking 7)
  local NTm = T.NavTime
  NTm.char.mount = true
  say("speed: travel", NTm.TravelSpeed(), "mode", NTm.TravelMode(), "mount-up", tostring(NTm.MountUp()))
  check(abs(NTm.TravelSpeed() - 11.2) < 0.01 and NTm.MountUp() == 3, "level 30 + mount known: 11.2 yd/s + 3 s to mount up")
  NTm.char.mount = false
  check(NTm.TravelSpeed() < 8, "no mount: running speed")
  -- common sense: a 300 yd hop is walked, a flight leg only joins two KNOWN flight masters
  local _, lw = R.FastestWorld(g, fm(XR, 0, 0), fm(XR, 300, 0))
  check(#lw == 1 and lw[1].mode == "walk", "short hop stays on foot")
  local _, lf = R.FastestWorld(g, fm(XR, 30, 0), fm(ORG, 30, 0))
  local okFly = false
  for _, lg in ipairs(lf) do if lg.mode == "fly" then okFly = true end end
  check(okFly, "La Croisée → Orgrimmar flies (both known)")
  local gOne = R.BuildHubs("Horde", { XR }, { speed = 11.2 })
  local _, lu = R.FastestWorld(gOne, fm(XR, 30, 0), fm(ORG, 30, 0))
  for _, lg in ipairs(lu) do check(lg.mode ~= "fly", "unknown Orgrimmar flight master: no flight leg") end
  -- relief prior: Serres-Rocheuses walks slower than Les Tarides (until learned)
  check(T.NavTime.Detour(1442) > T.NavTime.Detour(1413) and T.NavTime.Detour(1441) >= 1.3, "relief priors: Serres-Rocheuses / Mille pointes")

  -- pick-ups on the way: a quest giver 60 yd off the road between two stops is taken in passing (before its own
  -- objective, which is further on); one 900 yd away is not worth the detour
  do
    local pn = { { qs = { [1] = 3 }, x = 1, steps = { { k = "turn" } } }, { qs = { [2] = 3 }, x = 1, steps = { { k = "turn" } } },
      { qs = { [3] = 1 }, x = 1, steps = { { k = "pick" } }, value = 400, svc = 15 },
      { qs = { [4] = 1 }, x = 1, steps = { { k = "pick" } }, value = 400, svc = 15 },
      { qs = { [3] = 2 }, x = 1, steps = { { k = "obj" } } } }
    local P2 = { { 0, 0 }, { 500, 0 }, { 1000, 0 }, { 250, 60 }, { 250, 900 }, { 1300, 0 } }   -- start, 1, 2, giver near, giver far, obj
    local D2 = { [0] = {} }
    local function dd(a, b) return sqrt((P2[a][1] - P2[b][1]) ^ 2 + (P2[a][2] - P2[b][2]) ^ 2) / 7 end
    for i = 1, 5 do D2[0][i] = dd(1, i + 1); D2[i] = {} for j = 1, 5 do if i ~= j then D2[i][j] = dd(i + 1, j + 1) end end end
    local ordP = { 1, 2, 5 }
    local _, added = R.InsertOptional(pn, ordP, D2, { 3, 4 }, 15000 / 3600)
    check(#added == 1 and added[1].i == 3 and ordP[1] == 3 and ordP[#ordP] == 5 and N.Valid(pn, ordP),
      "pick-up on the way taken in passing, before its objective; the far one skipped (" .. table.concat(ordP, ",") .. ")")
  end
  -- « acheter des épices » : the vendor (seen while playing) becomes the stop, the nearest one
  do
    local CRm = T.CityRun
    local mem = CRm and CRm.Mem and CRm.Mem()
    if type(mem) == "table" then
      mem["1424"] = mem["1424"] or {}
      mem["1424"]["Marchande d'épices"] = { k = { vendor = true }, x = 0.52, y = 0.50, s = "2678:5:1" }
      local v = R.VendorFor(2678, { map = 1424, x = 50, y = 50 })
      check(v and v.name == "Marchande d'épices" and v.src == "vu" and math.abs(v.x - 52) < 0.01, "vendor item → the seller is the stop (" .. tostring(v and v.name) .. ")")
      mem["1424"]["Marchande d'épices"] = nil
    end
  end

  -- ------------------------------------------------------------ 3. learned path: L-shaped / ramp, compass, minimap
  local PL, C, NT = T.PathLearn, T.Compass, T.NavTime
  PL.Reset()
  M.mapRects[1424] = { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 }
  T.Geo.rects = {}
  local SPEED = 7
  local function walk(path)
    M.pos = { mapID = 1424, x = path[1][1], y = path[1][2] }
    C.Refresh()
    local t0 = M.now
    for k = 2, #path do
      local ax, ay, bx, by = path[k - 1][1], path[k - 1][2], path[k][1], path[k][2]
      local len = sqrt(((bx - ax) * 3000) ^ 2 + ((by - ay) * 2000) ^ 2)
      local n = floor(len / SPEED / 0.1 + 0.5)
      for i = 1, n do
        M.pos = { mapID = 1424, x = ax + (bx - ax) * i / n, y = ay + (by - ay) * i / n }
        M.Advance(0.1)
      end
    end
    for _ = 1, 15 do M.Advance(0.1) end
    return M.now - t0
  end
  -- the mesa: base (0.30, 0.60) → east along the cliff 600 yd → up the ramp north 400 yd → back west on the top 300 yd
  -- → quest spot (0.40, 0.40), 500 yd from the base as the crow flies (1 300 yd walked)
  local up = { { 0.30, 0.60 }, { 0.50, 0.60 }, { 0.50, 0.40 }, { 0.40, 0.40 } }
  C.SetWaypoint(40, 40, "Sommet")
  local observed = walk(up) - 1.5
  C.SetWaypoint(30, 60, "Base")
  walk({ { 0.40, 0.40 }, { 0.50, 0.40 }, { 0.50, 0.60 }, { 0.30, 0.60 } })
  check(PL.Count(0) > 20, "cells recorded while moving: " .. PL.Count(0))
  local base, top = PL.WorldOf({ mapID = 1424, x = 0.30, y = 0.60 }), PL.WorldOf({ mapID = 1424, x = 0.40, y = 0.40 })
  check(not PL.LineClear(0, base.n, base.w, top.n, top.w), "straight line base → top crosses unwalked cells (the cliff)")
  local ls = PL.LegSec(base, top, SPEED)
  local straight = sqrt((base.n - top.n) ^ 2 + (base.w - top.w) ^ 2)
  say("ramp: observed", floor(observed + 0.5), "s | learned", ls and floor(ls + 0.5), "s | straight model (prior 1.15)", floor(straight * 1.15 / SPEED + 0.5), "s")
  check(ls and abs(ls - observed) <= 0.15 * observed, "learned time within ±15 % of observed: " .. tostring(ls) .. " vs " .. observed)
  check(abs(straight * 1.15 / SPEED - observed) > 0.15 * observed, "the straight-line model alone was off by > 15 %")
  local wp, ang = PL.Waypoint(base, top)
  local corner = PL.WorldOf({ mapID = 1424, x = 0.50, y = 0.60 })
  check(wp and sqrt((wp.n - corner.n) ^ 2 + (wp.w - corner.w) ^ 2) <= 30 and ang > 35, "waypoint = foot of the ramp (" .. tostring(wp and wp.n) .. ", " .. tostring(wp and wp.w) .. ")")
  -- Route uses it: Planner leg between base and top = the learned time (the route sees the ramp)
  local legS = P.Leg({ map = 1424, x = 30, y = 60 }, { map = 1424, x = 40, y = 40 })
  check(legS and abs(legS - observed) <= 0.15 * observed + 3.5, "Route leg base → top follows the ramp: " .. tostring(legS))
  -- compass: arrow toward the ramp, distance / time of the stop itself
  M.pos = { mapID = 1424, x = 0.30, y = 0.60 }
  M.facing = 0
  C.SetWaypoint(40, 40, "Sommet")
  local st = C.Refresh()
  check(st and st.via and st.an and abs(st.an - corner.n) <= 30, "compass aims « par le chemin » at the foot of the ramp")
  check(st and st.dist and abs(st.dist - straight) < 2 and st.sec and abs(st.sec - observed) <= 0.15 * observed + 3.5,
    "compass shows the stop's distance and the learned time: " .. tostring(st and st.dist) .. " / " .. tostring(st and st.sec))
  local pts = T.PathDraw and T.PathDraw.LearnedPath and T.PathDraw.LearnedPath(0, base.n, base.w, top)
  check(pts and #pts >= 2, "minimap trail follows the learned path (" .. tostring(pts and #pts) .. " points)")
  -- an open straight line stays straight (no waypoint, no learned override)
  local mid = PL.WorldOf({ mapID = 1424, x = 0.38, y = 0.60 })
  check(PL.Waypoint(base, mid) == nil and PL.LegSec(base, mid, SPEED) == nil, "walked straight road: no waypoint, model kept")
  -- group members feed the graph at a lower weight
  local m0 = PL.Store(0)
  local before = m0.n
  PL.AddSegment(0, -1500, 900, -1500, 960, 1 / 3)
  local k = PL.Key(-1500, 930)
  check(m0.n > before and (m0.w[k] or 0) < 1, "a friend's trace counts at 1/3 weight (not 'walked' on one pass)")
  C.ClearWaypoint()

  -- ------------------------------------------------------------ 4. one list: Stops(1) = start of Stops(64); hearth once
  M.pos = { mapID = 1424, x = 0.50, y = 0.50 }
  local steps = {
    { k = "turn", q = 9101, map = 1424, x = 52, y = 50, svc = 15, label = "Rendre « Un »" },
    { k = "turn", q = 9102, map = 1424, x = 60, y = 52, svc = 15, label = "Rendre « Deux »" },
    { k = "turn", q = 9103, map = 1424, x = 70, y = 55, svc = 15, label = "Rendre « Trois »" },
  }
  P.route = { id = "auto", title = "Plan auto (en jeu)", steps = steps, verdict = "ok" }
  P.steps, P.cursor, P.log = steps, 1, {}
  P.stepsRev = (P.stepsRev or 0) + 1
  local s1, s64 = R:Stops(1), R:Stops(64)
  check(s1[1] == s64[1] and s1[1].step == steps[1], "compass list (Stops(1)) = window list (Stops(64)), same stop 1 object")
  check(s64[3] and s64[3].sec == 15, "time on site = the stop's own 15 s (not 60 % of the travel)")
  local hsN = 0
  for _, e in ipairs(s64) do if e.kind == "hs" then hsN = hsN + 1 end end
  check(hsN <= 1, "the hearthstone is used at most once")

  -- ------------------------------------------------------------ 5. SPF « ordre de TreeAI » re-sent after each rebuild
  local sent, cur = {}, nil
  local spfT = { API = { version = 1, minor = 1, Estimate = function() return nil end,
    NavigateRoute = function(owner, list) if owner ~= "TreeAI" then return false end sent[#sent + 1] = list cur = 1 return true end,
    CurrentStop = function(owner) return owner == "TreeAI" and cur or nil end, Ended = function() return nil end,
    Cancel = function() cur = nil return true end, Active = function() return cur ~= nil end } }
  rawset(_G, "ShortestPathForever", spfT)
  if T.SetGuideMode then T:SetGuideMode("auto") end
  if C.ResetSPF then C.ResetSPF() end
  local Ad = T.Addons
  if Ad and Ad.db then Ad.db.spfChoice = "order" end
  R.following, R._sentSig, R._sentAt = nil, nil, nil
  R.char.paused = false
  R:OnPlanStep()                                   -- what Planner calls after every rebuild
  check(#sent == 1 and sent[1][1] and sent[1][1].questID == 9101, "choice « ordre de TreeAI »: our order sent without a click")
  steps[1]._done = true
  P.cursor = 2
  M.Advance(3)
  R:OnPlanStep()
  check(#sent == 2 and sent[2][1].questID == 9102, "rebuild / step done: re-sent (" .. #sent .. ")")
  R:OnPlanStep()
  check(#sent == 2, "nothing changed: nothing re-sent (no stacked routes)")
  if Ad and Ad.db then Ad.db.spfChoice = nil end
  rawset(_G, "ShortestPathForever", nil)
  if T.SetGuideMode then T:SetGuideMode("treeai") end
  if C.ResetSPF then C.ResetSPF() end
  R.following = nil
end
