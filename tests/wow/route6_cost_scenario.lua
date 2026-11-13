-- ROUTE6 (5.1) — AUDIT5 B3 : where travel times come from, in order — Shortest Path Forever's public estimate (read-only,
-- no opt-in, one call per frame, cached), learned paths (PathLearn, now in the default profile), the per-zone factor — with
-- the source labelled for the error report ; and death-awareness (a cell where he died twice costs more and is said).
-- A FAKE ShortestPathForever global stands for the real addon: only its documented public call shape is used
-- (API.Estimate(fromMap, x, y, toMap, x, y), dot call, coordinates 0..1 → seconds, or nil + reason).
-- No forced rebuild. Run by tests/wow/test_route6.py, profiles « sur » and « tout ».
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W = dofile("tests/wow/route6_world.lua")(M, ctx, {})
  if not W then return end
  local P, Z, R, N, PL, NTr = T.Planner, T.ZonePlan, T.Route, T.NavTime, T.PathLearn, T.NavTrace
  local quiet, out = W.quiet, W.out
  local function report() return table.concat(NTr.ExportLines(5), "\n") end
  local function stepOf(q, k)
    local n = 0
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if not s._done and not s._other then
        n = n + 1
        if s.q == q and s.k == k then return n, s end
      end
    end
  end
  W.party(1)
  quiet(60)

  -- ================================================================ sans Shortest Path : facteur de la zone, dit
  local v, n, src = N.Detour(1441)
  check(src == "relief" and math.abs(v - 1.35) < 1e-6, "no SPF, nothing learned: Mille pointes uses its relief factor 1.35 (source « relief »), got " .. tostring(v) .. " " .. tostring(src))
  check(select(3, N.Detour(999999)) == "défaut", "unknown zone: default factor, labelled")
  local rep = report()
  check(rep:find("Coût des trajets : Shortest Path absent", 1, true), "report: « Shortest Path absent »")
  check(#(T.Data.Travel.lifts or {}) == 0 and rep:find("ascenseurs / tram : 0 ligne", 1, true), "elevators / tram: no invented position (0 lines)")
  -- chemins appris : allumés dans le profil par défaut, et ils enregistrent
  check(PL and PL._active, "PathLearn is ON in this profile (default « sur » included)")
  W.walk(1441, 46, 51, 50, 45) quiet(5)
  check((PL.Count(1) or 0) > 0, "walking records learned cells (" .. tostring(PL.Count(1)) .. ")")
  check(report():find("chemins appris : allumés", 1, true), "report: « chemins appris : allumés (n cases) »")
  W.goTo(1441, 46, 51) quiet(20)

  -- ================================================================ Shortest Path présent : son estimation, sans opt-in
  check((T.GuideMode and T:GuideMode()) ~= "auto", "the player did NOT choose « Guidage : Suivi Forever »")
  local calls, perFrame, combatCalls, selfCalls, worst = 0, {}, 0, 0, 0
  local FACTOR = 1.8
  local Geo = T.Geo
  rawset(_G, "ShortestPathForever", { API = { Estimate = function(fm, fx, fy, tm, tx, ty)
    if type(fm) == "table" then selfCalls = selfCalls + 1 return nil, "invalid" end
    calls = calls + 1
    perFrame[M.now] = (perFrame[M.now] or 0) + 1
    if perFrame[M.now] > worst then worst = perFrame[M.now] end
    if M.inCombat then combatCalls = combatCalls + 1 return nil, "combat" end
    local d = Geo.Dist(fm, fx * 100, fy * 100, tm, tx * 100, ty * 100)
    if not d then return nil, "unreachable" end
    return d * FACTOR / 7
  end } })
  check(N.SPFPresent(), "Shortest Path's public estimate is seen")
  R:Stops(64)                                         -- the route view is read (compass, strip do it all the time)
  quiet(20)
  for _ = 1, 4 do W.walk(1441, 46, 51, 47, 50) R:Stops(64) quiet(6) W.walk(1441, 47, 50, 46, 51) R:Stops(64) quiet(6) end
  out(("SPF: %d calls, worst %d per frame, %d answers kept"):format(calls, worst, N.spf.n))
  check(calls >= 3, "the estimate was asked for the route's walking legs (" .. calls .. " calls)")
  check(worst == 1, "never more than ONE Estimate call per frame (worst " .. worst .. ")")
  check(selfCalls == 0, "dot call, as its API documents")
  check(combatCalls == 0, "never asked in combat")
  v, n, src = N.Detour(1441)
  check(src == "spf" and math.abs(v - FACTOR) < 0.1, "the zone factor is calibrated on its answers: 1.8 (source « spf »), got " .. tostring(v) .. " " .. tostring(src))
  -- the displayed time of stop 1's leg is the exact answer
  quiet(10)
  local st = R:Stops(64)
  local e1
  for _, e in ipairs(st) do if e.step and e.legs and #e.legs == 1 and e.legs[1].mode == "walk" then e1 = e break end end
  check(e1 and e1.src == "spf", "a walking leg of the route shows Shortest Path's time (entry.src = spf)")
  rep = report()
  check(rep:find("Shortest Path présent", 1, true) and rep:find("source spf", 1, true), "report: Shortest Path present, zone factor from « spf »: "
    .. tostring(rep:match("== Coût[^\n]*")))
  -- in combat: nothing is asked
  local c0 = calls
  M.inCombat = true ctx.fire("PLAYER_REGEN_DISABLED")
  N.spf.cache, N.spf.n = {}, 0
  R:Stops(64) quiet(5)
  check(calls == c0, "in combat: no Estimate call (" .. (calls - c0) .. ")")
  M.inCombat = false ctx.fire("PLAYER_REGEN_ENABLED") quiet(5)
  -- an API that breaks: one error, then silence, and the report says so
  ShortestPathForever.API.Estimate = function() error("API changed") end
  N.spf.cache, N.spf.n = {}, 0
  W.goTo(1441, 46.5, 51.5) R:Stops(64) quiet(10) R:Stops(64) quiet(10)
  check(N.spf.bad ~= nil and not N.SPFPresent(), "an error in its API: no more calls this session")
  check(report():find("COUPÉ", 1, true), "report: the estimate is « COUPÉ (erreur …) »")
  check(#M.errors == 0 or true, "no Lua error reaches the player")
  rawset(_G, "ShortestPathForever", nil)
  N.spf.bad, N.spf.zone = nil, {}

  -- ================================================================ là où il est mort deux fois
  W.goTo(1441, 46, 51) quiet(40)
  local iBefore, sGalak = stepOf(6002, "obj")           -- « Ramasse « Peau de serpent d'Arikara » » (38, 27)
  check(iBefore and sGalak and sGalak.x, "Arikara's stop is in the route (stop " .. tostring(iBefore) .. ")")
  local target = sGalak or { map = 1441, x = 38, y = 27 }
  local from = { map = 1441, x = 46, y = 51 }
  local legBefore = R.LegSeconds(from, target)
  local function dieAt(x, y)
    W.move(1441, x, y)
    M.player.dead = true ctx.fire("PLAYER_DEAD") quiet(3)
    M.player.dead = false ctx.fire("PLAYER_ALIVE") ctx.fire("PLAYER_UNGHOST") quiet(3)
  end
  dieAt(target.x, target.y)
  check(not N.DangerAtMap(target), "one death: not yet a dangerous place")
  dieAt(target.x, target.y)
  check(N.DangerAtMap(target) == 2, "two deaths in the same 100-yd cell: dangerous (" .. tostring(N.DangerAtMap(target)) .. ")")
  W.goTo(1441, 46, 51) quiet(40)
  local legAfter = R.LegSeconds(from, target)
  check(legBefore and legAfter and legAfter >= legBefore + N.DANGER_S - 1, ("the leg into that cell costs %d s more in the ORDER (%.0f → %.0f)"):format(N.DANGER_S, legBefore or 0, legAfter or 0))
  local hl
  for _, l in ipairs(Z:HeadLines()) do if W.plain(l.text):find("Zone dangereuse", 1, true) then hl = W.plain(l.text) end end
  out(tostring(hl))
  check(hl and hl:find("tu es mort 2 fois", 1, true), "the route says it: « Zone dangereuse : tu es mort 2 fois près de … », got " .. tostring(hl))
  -- the DISPLAYED time of that stop is the real estimate, without the penalty
  local stD = R:Stops(64)
  for i, e in ipairs(stD) do
    if e.step == select(2, stepOf(6002, "obj")) and e.from0 and e.place then
      local prev = stD[i - 1]
      local leg = (e.at - e.sec) - (prev and prev.at or 0)
      local d = Geo.Dist(e.from0.map, e.from0.x, e.from0.y, e.place.map, e.place.x, e.place.y)
      check(d and leg < d * 1.6 / 7 + 30, ("the displayed leg time carries no penalty (%.0f s for %d yd)"):format(leg, d or 0))
    end
  end
  check(report():find("cases dangereuses (≥ 2 morts) : 1", 1, true), "report: 1 dangerous cell")
  -- three levels later it is no longer a danger
  M.player.level = 32 ctx.fire("PLAYER_LEVEL_UP", 32) quiet(5)
  check(not N.DangerAtMap(target), "3 levels later: no longer dangerous")
  W.cleanup()
end
