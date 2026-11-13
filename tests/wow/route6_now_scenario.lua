-- ROUTE6 (5.1) — AUDIT5 B6, B7, B11, B12 + D4 / D5 : ONE answer to « que faire maintenant », numbers that are real or
-- absent, the game's watch list left alone, city help on screen, flight-point advice, « mauvais conseil ».
-- The audit's g_group / b_pick / a_zone (A7), as checks. No forced rebuild. tests/wow/test_route6.py, « sur » and « tout ».
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W = dofile("tests/wow/route6_world.lua")(M, ctx, {})
  if not W then return end
  local P, Z, R, QB, Cm, NTr = T.Planner, T.ZonePlan, T.Route, T.QuestBrain, T.Compass, T.NavTrace
  local quiet, out = W.quiet, W.out
  local function lines(tab)
    local okL, now = pcall(T.GetTabLines, T, tab or "now")
    local t = {}
    for _, l in ipairs(okL and type(now) == "table" and now or {}) do t[#t + 1] = W.plain(l.text) end
    return t
  end
  local function has(t, pat) for _, l in ipairs(t) do if l:find(pat, 1, true) then return l end end end

  -- ================================================================ B11 : le suivi du jeu n'est pas touché par défaut
  for _, q in ipairs({ 6001, 6002, 6003 }) do M.WatchQuest(q, true) end
  M.watchLog = {}
  quiet(60)
  check(Z.db.autoWatch == false and not Z:AutoWatchOn(), "B11: « Suivi auto par zone » is off by default")
  check(#M.watchLog == 0, "B11: 60 s after login TreeAI changed nothing in the game's watch list (" .. #M.watchLog .. " changes)")

  -- ================================================================ B6 : une seule réponse (groupe de 3, « Vite »)
  W.party(3) quiet(30)
  M.Hw(function() T:UserAction(function() QB.SetMode("fastlvl") end) end) quiet(30)
  local scr = W.screen("G2 « Vite », groupe de 3")
  local fl = QB.FastLines()
  for _, l in ipairs(fl) do out("   [vite] " .. W.plain(l.text)) end
  local st = R:Stops(64)
  local grp = R.Groups(st)
  local first
  for i, e in ipairs(st) do if e.step then first = i break end end
  local stop1 = first and W.strip((R.StopLabel(P, st, first, grp))) or "?"
  local nowLine = W.plain(fl[1] and fl[1].text or "")
  check(nowLine:find("Fais ça maintenant : ", 1, true) == 1 and nowLine:find(stop1, 1, true),
    "B6: « Fais ça maintenant » names stop 1 of the route (« " .. stop1 .. " »), got « " .. nowLine .. " »")
  local ns = QB.NextStep()
  check(ns and W.strip(ns.text) == stop1, "B6: QuestBrain.NextStep() = stop 1 of the route")
  -- the compass aims at the same stop
  local me = Cm.Player and Cm.Player()
  local tg = R:CompassTarget(me)
  local s1 = st[first].step
  check(tg and s1 and math.abs((tg.x or 0) * 100 - s1.x) < 0.2 and math.abs((tg.y or 0) * 100 - s1.y) < 0.2, "B6: the compass target is the place of stop 1")
  -- the « Maintenant » line « Arrêt 1/N » names it too
  local arret = has(lines("now"), "Arrêt 1/")
  check(arret and arret:find(stop1:sub(1, 30), 1, true), "B6: « Maintenant » line « Arrêt 1/N » names the same stop: " .. tostring(arret))
  -- the next lines are the next stops of the route, in order
  local k = 1
  local i = first
  while i <= #st and k <= 3 do
    local _, last = R.StopLabel(P, st, i, grp)
    i = (last or i) + 1
    while st[i] and not st[i].step do i = i + 1 k = k + 1 end   -- a transport entry is a line of its own
    if st[i] and fl[k + 1] then
      local lbl = W.strip((R.StopLabel(P, st, i, grp)))
      check(W.plain(fl[k + 1].text):find(lbl:sub(1, 28), 1, true), "B6: line " .. k .. " of « Vite » = stop " .. (k + 1) .. " of the route (« " .. lbl .. " »): "
        .. W.plain(fl[k + 1].text))
    end
    k = k + 1
  end
  -- « tout près » only within ~100 m ; never for another zone (5.0.0: « Rends « Le journal de Khazgorm » … finie, tout près »)
  for _, l in ipairs(fl) do
    local t = W.plain(l.text)
    check(not (t:find("Khazgorm", 1, true) and t:find("tout près", 1, true)), "B6: a turn-in in another zone is not « tout près »: " .. t)
    check(not t:find("XP/min", 1, true) and not t:find("≈ %d+ min"), "B7: no invented rate / duration in « Vite »: " .. t)
  end
  for _, c in ipairs(QB.RouteSteps(4)) do
    if c.dist and c.dist > 110 then
      check(not tostring(QB.FastStepText(c) or ""):find("tout près", 1, true), "B6: « tout près » only within 100 m (" .. math.floor(c.dist) .. " yd)")
    end
  end

  -- ================================================================ B7 : pas de chiffre inventé
  -- quêtes à prendre : nombres d'objectifs inconnus (QuestieDB ne les donne pas), temps de combat jamais mesuré → la
  -- récompense (réelle) et la distance ; jamais « 2 930 XP en ~5 min (545 XP/min) »
  local picks = QB.PickupList(12)
  check(#picks >= 2, "B7: quests to take are listed (" .. #picks .. ")")
  for _, e in ipairs(picks) do
    local why = W.plain(tostring(e.why))
    out("   [à prendre] " .. e.title .. " : " .. why)
    check(not why:find("XP/min", 1, true) and not why:find("XP en ~", 1, true), "B7: « " .. e.title .. " » shows no rate built on supposed counts: " .. why)
    check(why:find("XP à la remise", 1, true), "B7: « " .. e.title .. " » shows its real reward: " .. why)
    check(e.est == true and e.xpm == nil, "B7: flagged as an estimate, no XP/min exported")
  end
  local p6100
  for _, e in ipairs(picks) do if e.questID == 6100 then p6100 = e end end
  check(p6100 and p6100.xp == W.XP[6100], "B7: the XP shown is the quest's reward from the database (" .. tostring(p6100 and p6100.xp) .. " = " .. W.XP[6100] .. ")")
  -- le nombre réel, appris le jour où la quête est dans un journal, puis un temps de combat mesuré → le rythme est dit
  QB.db.counts = QB.db.counts or { n = 0 }
  QB.db.counts[6100] = { 12 }
  check(QB.LearnedCount(6100, 1) == 12, "B7: a count seen in a log is kept per quest")
  local v0 = QB.Value(6100, T.ZonePlan.Recs(T.Export:Data())[6100], QB.Ctx())
  check(v0.est == true, "B7: real count but kill time never measured → still no rate")
  local X = T.Export
  local oldTTK = X.TTK
  -- (not a stub of the function under test: the MEASURE source of this character, as the game would have filled it)
  for _ = 1, 6 do if X.LearnTTK then X:LearnTTK(0, 14) end end
  if not (X.TTK and X:TTK(0)) then X.TTK = function() return 14 end end
  local v1 = QB.Value(6100, T.ZonePlan.Recs(T.Export:Data())[6100], QB.Ctx())
  out("   [mesuré] " .. table.concat(v1.why, " · "))
  check(v1.est == false and v1.why[1]:find("XP/min", 1, true) and v1.why[1]:find("≈", 1, true), "B7: real count + measured kill time → « … (≈ N XP/min) », got " .. v1.why[1])
  local rate = tonumber((v1.why[1]:match("≈ ([%d ]+) XP/min") or ""):gsub(" ", ""), 10)
  check(rate and rate == QB.Round2(rate), "B7: the rate is rounded to 2 significant digits (" .. tostring(rate) .. ")")
  X.TTK = oldTTK
  -- the kills really asked by the log are learned
  QB.LearnCounts()
  check(QB.LearnedCount(6001, 1) == 6 and QB.LearnedCount(6060, 1) == 8, "B7: counts of the quests in the log are learned (6001: 6, 6060: 8)")
  -- route header: the XP/h, when shown, says « estimation »
  for _, l in ipairs(P:Lines()) do
    local tip = type(l.tooltip) == "table" and table.concat(l.tooltip, " ") or tostring(l.tooltip or "")
    if tip:find("XP/h", 1, true) then check(tip:find("estimation", 1, true), "B7: route XP/h labelled « estimation »: " .. tip) end
  end

  -- ================================================================ D5 : « Mauvais conseil »
  M.Hw(function() T:UserAction(function() QB.SetMode("zone") end) end) quiet(30)
  local stopMenu = Z:StopEntries(P.steps[P.cursor])
  local bad
  for _, e in ipairs(stopMenu) do if tostring(e.text):find("Mauvais conseil", 1, true) then bad = e end end
  check(bad and type(bad.fn) == "function", "D5: right-click on the current stop offers « Mauvais conseil »")
  if bad then M.Hw(function() T:UserAction(bad.fn) end) end
  local store = TreeAIDB.diag and TreeAIDB.diag.bad
  local e = store and store[#store]
  check(e and e.step and e.pos and e.pos:find("^1441:") and type(e.why) == "table" and #e.why >= 3 and type(e.alts) == "table" and #e.alts >= 1,
    "D5: the entry keeps the step, the position, the alternatives and the reason codes")
  local rep = table.concat(NTr.ExportLines(10), "\n")
  check(rep:find("== Mauvais conseils notés : 1", 1, true) and rep:find("raisons : ", 1, true) and rep:find("autres arrêts proches", 1, true),
    "D5: it is in the error report (/tai erreurs)")
  check(rep:find("== Coût des trajets", 1, true) and rep:find("Questie : ", 1, true), "B3 / B8: the report says where travel times and Questie areas come from")
  M.RunSlash("/tai mauvais trop loin")
  check(#TreeAIDB.diag.bad == 2 and TreeAIDB.diag.bad[2].note == "trop loin", "D5: /tai mauvais <note> records too")
  for _, l in ipairs(M.output) do check(not tostring(l):find("Mauvais conseil", 1, true), "D5: nothing in the chat") end

  -- ================================================================ D4 : point de vol « non découvert » (dit par le jeu) près du trajet
  local D = T.Data.Travel
  local near
  for id, r in pairs(D.taxi) do
    if W.MAPS[r[5]] and r[5] ~= 1454 and not R.char.taxi[id] and R.FactionOK(r[8], "Horde") and (not near or id < near) then near = id end
  end
  check(near, "D4: the travel table has a Horde flight point he does not know in this world")
  if near then
    local r = D.taxi[near]
    check(R.UnknownTaxiNear() == nil, "D4: no advice while the game never showed the taxi map")
    R.char.taxiNo = { [near] = true }                 -- what TAXIMAP_OPENED stores: the game listed it « isUndiscovered »
    W.goTo(r[5], r[6] + 0.5, r[7] + 0.5) quiet(10)
    local fp = R.UnknownTaxiNear()
    check(fp and fp.id == near and fp.dist < 330, "D4: an undiscovered flight point near him is found")
    local hl
    for _, l in ipairs(Z:HeadLines()) do if W.plain(l.text):find("Point de vol à découvrir", 1, true) then hl = l end end
    check(hl and type(hl.onClick) == "function", "D4: one line « Point de vol à découvrir : … » with its click, got " .. tostring(hl and hl.text))
    R.char.taxi[near] = true
    check(R.UnknownTaxiNear() == nil, "D4: learned → the line goes away")
    R.char.taxi[near], R.char.taxiNo = nil, nil
  end

  -- ================================================================ B12 : entrer en ville avec des courses → une ligne À L'ÉCRAN
  local B = T.Brain
  if B and B._active then
    M.SetAlerts = nil
    local alerts = {}
    local oldShow = T.ShowAlert
    T.ShowAlert = function(self, e2, ...) alerts[#alerts + 1] = W.plain(e2 and e2.text or "") if oldShow then return oldShow(self, e2, ...) end end
    -- a real need: broken gear (durability read by the pilot)
    M.durability = M.durability or {}
    for slot = 1, 18 do M.durability[slot] = { 2, 100 } end
    ctx.fire("UPDATE_INVENTORY_DURABILITY")
    if T.ResetAlertGate then T:ResetAlertGate() end
    W.goTo(1454, 54.0, 68.0)
    M.player.resting = true ctx.fire("PLAYER_UPDATE_RESTING")
    quiet(20)
    local scr2 = W.screen("A7 entrée à Orgrimmar, équipement cassé")
    local cityLine = has(lines("now"), "Ici : ")
    if B.Needs and #B.Needs() > 0 then
      check(cityLine and cityLine:find("clic : le PNJ le plus proche", 1, true), "B12: entering the city with an errand → « Ici : … — clic : le PNJ le plus proche » : "
        .. table.concat(lines("now"), " | "))
      local shown = false
      for _, a in ipairs(alerts) do if a:find("Ici : ", 1, true) then shown = true end end
      check(shown, "B12: the line went through the on-screen alert (priority 3), not only the closed page: " .. table.concat(alerts, " | "))
      local n = 0
      for _, a in ipairs(alerts) do if a:find("Ici : ", 1, true) then n = n + 1 end end
      quiet(120)
      for _, a in ipairs(alerts) do if a:find("Ici : ", 1, true) then n = n + 0 end end
      local n2 = 0
      for _, a in ipairs(alerts) do if a:find("Ici : ", 1, true) then n2 = n2 + 1 end end
      check(n2 == 1, "B12: once per visit (" .. n2 .. " alerts in 2 min)")
    else
      out("   (no errand need detected on this mock: B12 not exercised)")
      check(false, "B12: the pilot should see the broken gear as a need")
    end
    T.ShowAlert = oldShow
  end
  W.cleanup()
end
