-- Scenario for agent NAV: Compass redesign (smooth arrow, alignment colour, « derrière toi », arrival ring,
-- one-guide rule with Shortest Path), NavTime (measured speed, learned detour, recorded fake trip: predicted vs
-- actual ≤ 10 % once calibrated), Horizon strip, Glow (marching ants on quest items / tooltip, bag-addon roots).
-- Fake names only. Run on modern / legacy / forever by tests/wow/test_nav.py.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local C, N, H = T.Compass, T.NavTime, T.modules and T.modules.Highlight   -- (5.1 CLEAN) Horizon removed
  check(C and N and T.Glow and H and not T.Horizon, "Compass, NavTime, Glow, Highlight present ; Horizon gone")
  if not (C and N and T.Glow and H) then return end
  local pi = math.pi
  local G = C.Geo
  M.LeaveCombat()
  rawset(_G, "ShortestPathForever", nil)
  if C.ResetSPF then C.ResetSPF() end

  -- ------------------------------------------------------------ pure helpers
  check(G.ETA(61 * 7, 7) == "1 min 01", "ETA keeps the seconds on a short trip: " .. tostring(G.ETA(61 * 7, 7)))
  check(G.ETA(140, 7) == "20 s" and G.ETA(700, 7) == "1 min 40" and G.ETA(7 * 1500, 7) == "25 min", "ETA formats")
  local r, g = G.AlignColor(0)
  check(r < 0.3 and g == 1, "facing the target = green")
  r, g = G.AlignColor(pi)
  check(r == 1 and g < 0.05, "target behind = red")
  check(math.abs(G.Shortest(0.1, 2 * pi - 0.1) + 0.2) < 1e-9, "shortest arc wraps through north")

  -- ------------------------------------------------------------ compass: arrow, colour, behind, arrival
  M.instance = false
  M.pos = { mapID = 1424, x = 0.40, y = 0.40 }
  M.facing = 0
  C.db.hideCombat, C.db.hideDup = true, true
  T.settings.guide = nil                     -- default: TreeAI is the only guide
  C.Show(true)
  C.SetWaypoint(50, 40, "Moulin")             -- 300 yd east (map 1424 = 3000 × 2000 yd)
  local st = C.Refresh()
  local fr = rawget(_G, "TreeAICompassFrame")
  check(fr and fr:IsShown() and st.dist and math.abs(st.dist - 300) < 1, "compass shown, 300 yd: " .. tostring(st and st.dist))
  check(st.eta and st.sec and math.abs(st.sec - 300 * 1.15 / 7) < 0.5, "ETA = distance × prior detour ÷ run speed: " .. tostring(st.sec))
  check(fr:GetWidth() == C.W and C.W >= 180, "bigger frame (" .. tostring(C.W) .. ")")
  -- smooth rotation: one 33 ms step moves part of the way, ~1 s converges
  M.facing = pi / 2                          -- turn to face west: target (east) now behind-right
  C.Step(0.033)
  check(C.state.rel and math.abs(C.state.rel - (-pi / 2 - pi / 2) % (2 * pi)) < 1e-6, "relative angle follows facing at once")
  for _ = 1, 30 do C.Step(0.033) end
  check(C.state.behind == true, "« derrière toi » when the target is behind")
  M.facing = -pi / 2                         -- face east
  for _ = 1, 30 do C.Step(0.033) end
  check(not C.state.behind and C.state.rel and C.state.rel < 1e-3, "facing it: not behind")
  -- arrival ring
  M.pos = { mapID = 1424, x = 0.497, y = 0.40 }
  st = C.Refresh()
  check(st.arrived, "arrival (≤ 12 yd for a custom point)")

  -- ------------------------------------------------------------ one guide: TreeAI by default, SPF only on opt-in
  local active, est = true, 61
  rawset(_G, "ShortestPathForever", { API = {
    Estimate = function() return est end,
    Active = function() return active end,
    NavigateRoute = function() return true end,
    Navigate = function() return true end,
  } })
  if C.ResetSPF then C.ResetSPF() end
  M.pos = { mapID = 1424, x = 0.40, y = 0.40 }
  C.travelT = nil
  st = C.Refresh()
  check(not st.dup and fr:IsShown(), "SPF installed and guiding, not chosen → TreeAI compass stays (only guide)")
  check(not st.spf and math.abs(st.sec - 300 * 1.15 / 7) < 0.5, "SPF estimate ignored without opt-in: " .. tostring(st.sec))
  M.hardware = true                          -- « Aller là » is a click
  local how = C.Pin(C.Current())
  M.hardware = false
  check(how ~= "spf", "« Aller là » does not hand over to SPF without opt-in: " .. tostring(how))
  T.settings.guide, T.settings.guideV = "auto", 1                   -- player picked « Guidage : Suivi Forever »
  C.travelT = nil
  st = C.Refresh()
  -- (ZONEFIX) one arrow: the compass steps aside, reduced to one short line that says why (no arrow, no distance)
  check(st.dup == "spf" and C.hideWhy == "dup" and (not fr:IsShown() or fr:GetHeight() < 40),
    "opt-in + SPF guiding → compass reduced to one line (one arrow)")
  if how == "blizzard" then   -- (FIDELITY F11) TreeAI's own Blizzard waypoint is removed: SPF's arrow only
    local CMw = rawget(_G, "C_Map")
    check(not (CMw and CMw.HasUserWaypoint and CMw.HasUserWaypoint()), "our « Aller là » waypoint removed when SPF guides")
  end
  active = false
  C.travelT = nil
  st = C.Refresh()
  check(st.spf and st.sec == 61 and st.eta == "1 min 01", "opt-in: SPF estimate used: " .. tostring(st.eta))
  T.settings.guide = nil
  rawset(_G, "ShortestPathForever", nil)
  if C.ResetSPF then C.ResetSPF() end
  C.travelT = nil

  -- ------------------------------------------------------------ recorded fake trips (no SPF)
  -- the road bends: start → (0.45, 0.50) → (0.50, 0.40): 250 + 250 = 500 yd for 300 yd as the crow flies (×1.67)
  local db = N.Store()
  db.log, db.detour, db.speed = {}, {}, {}
  N.trip = nil
  local SPEED = 7
  local function walk(path)
    M.pos = { mapID = 1424, x = path[1][1], y = path[1][2] }
    C.Refresh()
    local t0 = M.now
    for k = 2, #path do
      local ax, ay, bx, by = path[k - 1][1], path[k - 1][2], path[k][1], path[k][2]
      local dx, dy = (bx - ax) * 3000, (by - ay) * 2000
      local len = math.sqrt(dx * dx + dy * dy)
      local n = math.floor(len / SPEED / 0.1 + 0.5)
      for i = 1, n do
        M.pos = { mapID = 1424, x = ax + (bx - ax) * i / n, y = ay + (by - ay) * i / n }
        M.Advance(0.1)
      end
    end
    for _ = 1, 15 do M.Advance(0.1) end        -- the next 1 Hz refresh sees the arrival
    return M.now - t0
  end
  local out = { { 0.40, 0.40 }, { 0.45, 0.50 }, { 0.50, 0.40 } }
  local back = { { 0.50, 0.40 }, { 0.45, 0.50 }, { 0.40, 0.40 } }
  C.SetWaypoint(50, 40, "Moulin")
  walk(out)
  local e1 = N.last
  check(e1 and e1.ok and e1.e < -20, "trip 1: prior detour too optimistic (" .. tostring(e1 and e1.e) .. " %)")
  C.SetWaypoint(40, 40, "Camp")
  walk(back)
  local d, n = N.Detour(1424)
  check(n == 2 and d > 1.5 and d < 1.8, "detour learned for the zone from 2 arrivals: " .. tostring(d))
  local sp = N.Speed("w")
  check(sp > 6.3 and sp <= 7.05, "walking speed measured from position deltas: " .. tostring(sp))
  C.SetWaypoint(50, 40, "Moulin")
  walk(out)
  local e3 = N.last
  check(e3 and e3.ok and math.abs(e3.e) <= 10, "trip 3: predicted vs actual within 10 % (" .. tostring(e3 and e3.e) .. " %)")
  local acc, cnt = N.Accuracy()
  check(cnt == 3 and acc, "accuracy log (" .. tostring(cnt) .. " trips)")
  check(N.AccuracyText() and N.AccuracyText():find("Temps de trajet : ±"), "accuracy text: " .. tostring(N.AccuracyText()))
  -- a fight on the way: logged but not used for calibration
  C.SetWaypoint(40, 40, "Camp")
  M.pos = { mapID = 1424, x = 0.50, y = 0.40 }
  C.Refresh()
  M.EnterCombat()
  M.Advance(1.2)
  M.LeaveCombat()
  walk(back)
  check(N.last and not N.last.ok, "trip with a fight is not counted as clean")
  check(select(2, N.Detour(1424)) == 3, "detour not learned from the dirty trip")

  -- ------------------------------------------------------------ glow: quest item overlay + tooltip + bag roots
  M.AddItem(9201, { name = "Croc de test", type = "Quête", classID = 12, stack = 20 })
  M.bags = M.bags or {}
  for b = 0, 4 do M.bags[b] = M.bags[b] or { size = 16, slots = {} } end
  M.SetBagItem(0, 9, 9201, 1)
  local btn = CreateFrame("Button", nil, UIParent)
  btn:SetSize(37, 37)
  local ov = H.Apply(btn, "need", { need = 10, have = 2, quest = "Test" })
  check(ov and T.Glow.IsOn(ov), "quest item: marching ants on")
  check(ov and ov._pulse and ov._pulse:IsShown(), "quest item: pulsing halo")
  H.HideOverlay(btn)
  check(not T.Glow.IsOn(ov), "hidden overlay stops the ants")
  local tip = H.TooltipGlow(GameTooltip or CreateFrame("GameTooltip", "GameTooltip", UIParent), "starter", 1, 0.8, 0)
  check(tip and T.Glow.IsOn(tip), "tooltip of a quest-starting item: ants")
  local root = CreateFrame("Frame", "BetterBagsBagBackpack", UIParent)
  local bagf = CreateFrame("Frame", nil, root)
  bagf:SetID(0)
  local ib = CreateFrame("Button", nil, bagf)
  ib:SetID(9)
  H.db.thirdParty = true
  H.ScanBagRoots()
  check(H.External()[ib] and H.External()[ib][1] == 0 and H.External()[ib][2] == 9, "BetterBags-like root: item button found")
  -- in combat with protected owners: no blocked call from the glow
  M.EnterCombat()
  H.HideOverlay(btn)
  M.LeaveCombat()
  C.ClearWaypoint()
end
