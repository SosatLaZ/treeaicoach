-- ROUTE6 (5.1) — AUDIT5 B1 + B2 : the route is re-solved from where the player IS, and new quest data reaches it.
-- « j'ai la quête à côté, ça me dit de partir », back-and-forth, « plus court chemin » that is not.
-- The audit's scenarios d_walk / a2_zone, turned into checks. NO forced rebuild anywhere (no Recalc, no Refresh(true), no
-- Rebuild): the scenario only moves the player, waits, and reads what the addon shows by itself.
-- Run by tests/wow/test_route6.py in the DEFAULT profile (« sur ») and in « tout », Forever flavour.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W = dofile("tests/wow/route6_world.lua")(M, ctx, {})
  if not W then return end
  local P, Z, R, QB = T.Planner, T.ZonePlan, T.Route, T.QuestBrain
  local Geo = T.Geo
  local quiet, out = W.quiet, W.out
  local function me() local m, x, y = Geo.Player() return { map = m, x = x, y = y } end
  local function dist(a, b) return Geo.Dist(a.map, a.x, a.y, b.map, b.x, b.y) end
  local function visible()
    local t = {}
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if not s._done and not s._other then t[#t + 1] = s end
    end
    return t
  end
  local function label(s) return W.strip(P:StepLabel(s)) end
  local function indexOf(pat)
    for i, s in ipairs(visible()) do if label(s):find(pat, 1, true) then return i, s end end
  end
  -- INVARIANT 1 (« même PNJ = un arrêt ») : prises / remises prêtes au même PNJ (≤ 25 yd) se suivent, jamais séparées
  local function sameNpcTogether(tag)
    local v = visible()
    for i, a in ipairs(v) do
      if (a.k == "pick" or a.k == "turn") and a.x then
        local gap = false
        for j = i + 1, #v do
          local b = v[j]
          local same = (b.k == "pick" or b.k == "turn") and b.x and b.map == a.map and (dist(a, b) or 1e9) <= 25
          if same and gap then
            -- allowed only when a precedence forces it (a turn-in after its own objectives)
            local forced = false
            for k = i + 1, j - 1 do if v[k].q == b.q then forced = true end end
            check(forced, tag .. ": two stops for one NPC — « " .. label(a) .. " » (stop " .. i .. ") and « " .. label(b) .. " » (stop " .. j .. ")")
          elseif not same then gap = true end
        end
      end
    end
  end
  -- INVARIANT 2 (« j'ai la quête à côté, ça me dit de partir ») : une prise / remise prête à ≤ 100 m passe avant tout arrêt
  -- plus loin — sauf raison dite (Z.firstWhy)
  local function nothingNearSkipped(tag)
    local v, here = visible(), me()
    local first = v[1]
    if not (first and first.x) then return end
    local d1 = dist(here, first) or 1e9
    if d1 <= 110 then return end
    local log = P.log or {}
    for i = 2, #v do
      local s = v[i]
      local ready = (s.k == "turn" and log[s.q] and log[s.q].done == 1) or (s.k == "pick" and not log[s.q])
      local d = ready and s.x and dist(here, s)
      if d and d <= 100 then
        check(type(Z.firstWhy) == "string" and Z.firstWhy ~= "", tag .. ": « " .. label(s) .. " » is " .. math.floor(d) .. " yd away but stop 1 is « "
          .. label(first) .. " » at " .. math.floor(d1) .. " yd, with no stated reason")
      end
    end
  end
  -- INVARIANT 3 (« [2 min] pour un arrêt à 2,6 km ») : l'heure d'arrivée affichée n'est jamais plus courte que la ligne
  -- droite à 14 yd/s (monture rapide) pour un arrêt atteint à pied dans la même zone
  local function etaHonest(tag)
    local here = me()
    local st = R:Stops(64)
    for i = 1, math.min(4, #st) do
      local e = st[i]
      local s = e.step
      if s and s.x and s.map == here.map and not e.hs then
        local d = dist(here, s)
        local arrive = (e.at or 0) - (e.sec or 0)
        if d and i == 1 then check(arrive + 1 >= d / 14, ("%s: stop 1 « %s » at %d yd shown in %d s"):format(tag, label(s), d, arrive)) end
      end
    end
  end
  local function invariants(tag) sameNpcTogether(tag) nothingNearSkipped(tag) etaHonest(tag) end

  -- ================================================================ D0 (B2) : la route de connexion, groupe de 4
  -- Les données de quêtes (Questie) arrivent après le premier calcul ; 60 s plus tard, sans clic, le trajet les a.
  quiet(60)
  out("D0 " .. W.stopsText(6))
  local v = visible()
  check(v[1] and label(v[1]):find("Les crocs de Galak", 1, true) and v[1].k == "turn",
    "D0: 60 s after login, stop 1 = the turn-in 19 m away (« Rendre « Les crocs de Galak » »), got « " .. tostring(v[1] and label(v[1])) .. " »")
  check(indexOf("Prendre « Sécurité de la route »") and indexOf("Prendre « Sécurité de la route »") <= 3,
    "D0: the giver next to him is in the first stop (« Prendre « Sécurité de la route » »): " .. W.stopsText(3))
  invariants("D0")
  local builds0 = P.buildCount or 0

  -- ================================================================ D1 (B1) : 2,6 km à pied dans la zone, aucun évènement
  W.party(2) quiet(30)
  out("D1 start " .. W.stopsText(6))
  W.perf.on = true                                     -- real frames while he walks: the 4 ms rule, measured
  local secs = W.walk(1441, 46, 51, 80, 85) quiet(20)
  W.perf.on = false
  out(("D1 walk: %d s, %d frames, worst frame %d kinstr, %d kinstr/min, %d re-orders (last %.1f ms), %d builds"):format(secs, W.perf.frames,
    W.perf.worst, W.perf.total / math.max(1, secs + 20) * 60, Z.reorders or 0, Z.reorderMs or 0, (P.buildCount or 0) - builds0))
  check(W.perf.worst <= 200, "D1: no frame over 4 ms (200 kinstr) while walking 1.6 km with the route re-solved: worst " .. W.perf.worst .. " kinstr")
  out("D1 " .. W.stopsText(8))
  v = visible()
  check(v[1] and label(v[1]) == "Prendre « Les ruines du sud »", "D1: at the ruins, stop 1 = the giver he stands on, got « " .. tostring(v[1] and label(v[1])) .. " »")
  local iKill, iBack = indexOf("Spectre des ruines"), indexOf("Rendre « Les crocs de Galak »")
  check(iKill and iBack and iKill < iBack, "D1: the mobs where he stands (« Tue « Spectre des ruines » », stop " .. tostring(iKill)
    .. ") come before the turn-in 2.6 km behind him (stop " .. tostring(iBack) .. ")")
  check(iKill and iKill <= 3, "D1: « Tue « Spectre des ruines » » right after its pick-up, not at stop " .. tostring(iKill))
  do   -- the ETA of the far turn-in is a real one (≥ straight line at mount speed)
    local st = R:Stops(64)
    for _, e in ipairs(st) do
      if e.step and e.step.q == 6003 and e.step.k == "turn" then
        local d = dist(me(), e.step) or 0
        check(d > 1500 and (e.at or 0) >= d / 14, ("D1: « Rendre « Les crocs de Galak » » is %d yd away, shown at %d s"):format(d, e.at or 0))
      end
    end
  end
  invariants("D1")
  check(Z.reorders and Z.reorders >= 1, "D1: the order was re-solved from the position by the cheap job (Z.reorders = " .. tostring(Z.reorders) .. ")")

  -- ================================================================ D2 (B1) : zone CHOISIE, 3 km jusqu'à Arikara
  M.Hw(function() T:UserAction(function() QB.PickZone(1441) end) end) quiet(30)
  W.walk(1441, 80, 85, 38, 27) quiet(20)
  out("D2 " .. W.stopsText(8))
  v = visible()
  check(v[1] and label(v[1]):find("Arikara", 1, true) and v[1].k == "obj",
    "D2: chosen zone, standing on Arikara's spot → stop 1 = « Ramasse « Peau de serpent d'Arikara » », got « " .. tostring(v[1] and label(v[1])) .. " »")
  invariants("D2")

  -- ================================================================ immobile : rien n'est recalculé
  local r0, b0 = Z.reorders or 0, P.buildCount or 0
  quiet(120)
  check((Z.reorders or 0) == r0 and (P.buildCount or 0) == b0, "at rest for 2 min: no re-order, no rebuild (" .. tostring((Z.reorders or 0) - r0)
    .. " re-orders, " .. tostring((P.buildCount or 0) - b0) .. " builds)")
  -- petit pas (30 m) : pas de recalcul non plus
  W.move(1441, 38.3, 27.2) quiet(30)
  check((Z.reorders or 0) == r0, "a 30 m step does not re-solve the route")

  -- ================================================================ A2 (B1 + B2) : zone choisie = Les Tarides, arrivée à Taurajo
  W.goTo(1441, 46.0, 51.0) quiet(30)
  M.Hw(function() T:UserAction(function() QB.PickZone(1413) end) end)
  quiet(20)
  out("A2 pick " .. W.stopsText(8))
  check(indexOf("Prendre « Les raiders kolkars »"), "A2 (B2): 20 s after choosing Les Tarides its pick-up is in the route, without « Recalculer »: "
    .. W.stopsText(8))
  W.goTo(1413, 45.0, 60.0) quiet(60)
  out("A2 arrived " .. W.stopsText(8))
  v = visible()
  local i1, i2 = indexOf("Rendre « Le journal de Khazgorm »"), indexOf("Rendre « Les excavateurs de Bael'dun »")
  check(i1 and i2 and math.abs(i1 - i2) == 1 and math.max(i1, i2) <= 3, "A2: both turn-ins at Mangeclous in the first stop (got stops "
    .. tostring(i1) .. " and " .. tostring(i2) .. "): " .. W.stopsText(8))
  local st = R:Stops(64)
  local flights = 0
  for i = 1, math.min(6, #st) do if st[i].kind == "fly" then flights = flights + 1 end end
  check(flights == 0, "A2: no flight out of the chosen zone and back among the first stops: " .. W.stopsText(8))
  invariants("A2")
  -- mort, résurrection plus loin : re-résolu depuis là où il se relève (pas d'évènement de zone)
  M.player.dead = true ctx.fire("PLAYER_DEAD") quiet(5)
  W.move(1413, 46.5, 63.0)
  M.player.dead = false ctx.fire("PLAYER_ALIVE") ctx.fire("PLAYER_UNGHOST") quiet(20)
  out("A2 after death " .. W.stopsText(8))
  invariants("A2 after death")

  -- ================================================================ coût : un ré-ordonnancement n'est pas un recalcul complet
  check((P.buildCount or 0) > builds0, "full rebuilds happened only for real changes (group, zone, data)")
  check(type(Z.reorderMs) == "number", "the re-order job reports its cost (Z.reorderMs)")
  W.cleanup()
end
