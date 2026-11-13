-- ROUTE6 (5.1) — AUDIT5 B4 : phantom quests done right. The audit's b_pick / b2_pick as checks.
--  * a giver that is NOT there when he stands on its spot (nobody of that name seen: target, mouse-over, nameplates, NPC
--    window) → after 20 s, ONE question « Ce PNJ n'est pas là ? » with [Pas là] [Je reviens plus tard] ;
--  * never a quest removed by walking past its giver (5.0.0: all the giver's quests after two walk-pasts) ;
--  * « Quêtes écartées » : a visible list with undo ; « Pas là » is remembered for the character (level-up included).
-- No forced rebuild. Run by tests/wow/test_route6.py, profiles « sur » and « tout ».
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W = dofile("tests/wow/route6_world.lua")(M, ctx, {})
  if not W then return end
  local P, Z, QB = T.Planner, T.ZonePlan, T.QuestBrain
  local quiet, out = W.quiet, W.out
  local GIVER = "Éclaireur Tête-de-Faucon"            -- gives 6100, 6101 (+ elite 6104 at 4)
  local function hasPick(q)
    for i = P.cursor or 1, #(P.steps or {}) do
      local s = P.steps[i]
      if s.k == "pick" and s.q == q and not s._done and not s._other then return true end
    end
    return false
  end
  local function nowLines()
    local okL, now = pcall(T.GetTabLines, T, "now")
    local t = {}
    for _, l in ipairs(okL and type(now) == "table" and now or {}) do t[#t + 1] = W.plain(l.text) end
    return t
  end
  local function nowHas(pat) for _, l in ipairs(nowLines()) do if l:find(pat, 1, true) then return l end end end
  local function headLine(pat)
    for _, l in ipairs(Z:HeadLines()) do if W.plain(l.text):find(pat, 1, true) then return l end end
  end
  quiet(60)
  check(hasPick(6100) and hasPick(6101), "start: both pick-ups of the giver are in the route")

  -- ================================================================ 1. walking past the giver, NPC present (targeted)
  -- 5.0.0: « PNJ atteint 2 fois sans la proposer » removed ALL his quests. Now: nothing, ever.
  M.target = { exists = true, name = GIVER, level = 35, health = 100, healthMax = 100, hostile = false }
  ctx.fire("PLAYER_TARGET_CHANGED")
  for k = 1, 3 do
    W.walk(1441, 50, 45, 46.2, 50.8) quiet(4)
    W.walk(1441, 46.2, 50.8, 50, 45) quiet(4)
  end
  check(not QB.Phantom(6100) and not QB.Phantom(6101), "walk-past ×3 with the NPC in sight: no quest removed (5.0.0 removed them after 2)")
  check(hasPick(6100) and hasPick(6101), "walk-past ×3: the pick-ups are still in the route")
  check(not nowHas("n'est pas là"), "NPC seen: no « pas là » question")
  -- standing next to him 40 s, still targeted: he IS there
  W.walk(1441, 50, 45, 46.2, 50.8) quiet(40)
  check(not nowHas("n'est pas là") and not QB._absent, "40 s next to a giver he can see (target): no question")
  W.walk(1441, 46.2, 50.8, 50, 45) quiet(10)
  M.target = { exists = false } ctx.fire("PLAYER_TARGET_CHANGED")
  -- seen only by its nameplate: he is there too
  M.units.nameplate3 = { name = GIVER, hostile = false }
  W.walk(1441, 50, 45, 46.2, 50.8) quiet(40)
  check(not QB._absent, "40 s next to a giver seen by its nameplate: no question")
  M.units.nameplate3 = nil
  W.walk(1441, 46.2, 50.8, 50, 45) quiet(10)

  -- ================================================================ 2. the giver is NOT there (Questie phantom)
  W.walk(1441, 50, 45, 46.2, 50.8)
  quiet(8)
  check(not QB._absent, "8 s on the spot: too early to ask")
  quiet(20)
  out(table.concat(nowLines(), " | "))
  local q1 = nowHas("Ce PNJ n'est pas là ?")
  check(q1 and q1:find(GIVER, 1, true), "28 s on the spot, nobody of that name seen: ONE question « Ce PNJ n'est pas là ? " .. GIVER .. " », got: "
    .. table.concat(nowLines(), " | "))
  check(not QB.Phantom(6100) and hasPick(6100), "the question removes nothing by itself")
  local n = 0
  for _, l in ipairs(nowLines()) do if l:find("n'est pas là", 1, true) then n = n + 1 end end
  check(n == 1, "one question for the giver's quests, not one per quest (" .. n .. ")")
  -- the two answers are exactly the two buttons of the small menu
  M.Hw(function() T:UserAction(function() QB.AbsentMenu() end) end)
  local _, entries = Z:GetMenu()
  local labels = {}
  for _, e in ipairs(entries or {}) do labels[#labels + 1] = e.text end
  check(table.concat(labels, "|"):find("Pas là|Je reviens plus tard", 1, true), "the question's menu: [Pas là] [Je reviens plus tard], got " .. table.concat(labels, "|"))
  -- « Je reviens plus tard »
  M.Hw(function() T:UserAction(function() QB.AbsentLater() end) end) quiet(10)
  check(not QB.Phantom(6100) and not QB.Phantom(6101), "« Je reviens plus tard » : nothing is dismissed for good")
  check(not hasPick(6100) and not hasPick(6101), "« Je reviens plus tard » : the route moves on (stop skipped for the session)")
  check(not nowHas("n'est pas là"), "answered: the question is gone")
  local dl = headLine("Quêtes écartées")
  check(dl and W.plain(dl.text):find("Quêtes écartées (", 1, true), "route head: « Quêtes écartées (n) » line, got " .. tostring(dl and dl.text))
  local list = QB.DismissedList()
  local kinds = {}
  for _, e in ipairs(list) do kinds[e.q] = e.kind end
  check(kinds[6100] == "later" and kinds[6101] == "later", "list: both quests « plus tard (cette session) »")
  -- undo from the list
  M.Hw(function() T:UserAction(function() QB.Restore(nil) end) end) quiet(15)
  check(hasPick(6100) and hasPick(6101), "« Tout remettre » : back in the route")
  quiet(60)
  check(not QB._absent, "no second question for the same giver within 10 min")

  -- ================================================================ 3. « Pas là » : remembered for the character
  W.walk(1441, 46.2, 50.8, 50, 45) quiet(620)
  W.walk(1441, 50, 45, 46.2, 50.8) quiet(30)
  check(QB._absent and nowHas("Ce PNJ n'est pas là ?"), "a later visit (10 min after): the question comes again")
  M.Hw(function() T:UserAction(function() QB.AbsentYes() end) end) quiet(15)
  local ph = QB.Phantom(6100)
  check(ph and ph.by == "player" and tostring(ph.why):find("PNJ absent", 1, true), "« Pas là » : 6100 dismissed, reason « PNJ absent », by the player")
  check(QB.Phantom(6101) ~= nil, "« Pas là » : the giver's other quest too")
  check(not hasPick(6100) and not hasPick(6101), "« Pas là » : out of the route")
  local inPick = false
  for _, e in ipairs(QB.PickupList(12)) do if e.questID == 6100 or e.questID == 6101 then inPick = true end end
  check(not inPick, "« Pas là » : out of « Quêtes à prendre »")
  check(type(QB.char.phantom) == "table" and QB.char.phantom[6100] ~= nil, "kept in the character's saved data (QuestBrain char.phantom)")
  -- level-up: a « Pas là » of the player stays (an « NPC did not offer it » is retried)
  M.player.level = 30 ctx.fire("PLAYER_LEVEL_UP", 30) quiet(20)
  check(QB.Phantom(6100) ~= nil, "level 30: the player's « Pas là » is still remembered")
  kinds = {}
  for _, e in ipairs(QB.DismissedList()) do kinds[e.q] = e.kind end
  check(kinds[6100] == "absent", "list: « PNJ absent » entry for 6100")
  -- the visible list and its undo
  M.Hw(function() T:UserAction(function() QB.ShowDismissed() end) end)
  _, entries = Z:GetMenu()
  local found
  for _, e in ipairs(entries or {}) do if tostring(e.text):find("Sécurité de la route", 1, true) and tostring(e.text):find("PNJ absent", 1, true) then found = e end end
  check(found and type(found.fn) == "function", "menu « Quêtes écartées » : a row « Sécurité de la route · PNJ absent » with a click to restore")
  if found then M.Hw(function() T:UserAction(found.fn) end) end
  quiet(15)
  check(not QB.Phantom(6100) and hasPick(6100), "undo: 6100 is back in the route")
  check(QB.Phantom(6101) ~= nil, "undo of one quest leaves the other dismissed")

  -- ================================================================ 4. an NPC window that does not list it (real evidence)
  M.Hw(function() T:UserAction(function() QB.Restore(nil) end) end) quiet(15)
  W.cleanup()
end
