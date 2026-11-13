-- pickfix_scenario.lua (4.3.5 PICKFIX) — the real-game report on 4.3.4 (frFR): « ça vérifie mal si la quête est prise :
-- quand j'arrive, pour lui c'est bon, mais pas du tout » ; « des quêtes qu'affiche Questie n'existent pas » ; and the
-- new « Monter vite » mode (QuestBrain fastlvl, /tai vite). Fake ids / names only. Run by tests/wow/test_pickfix.py.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local P, QB, R, Z = T.Planner, T.QuestBrain, T.Route, T.ZonePlan
  check(P and QB and R and Z, "Planner, QuestBrain, Route, ZonePlan loaded")
  if not (P and QB and R and Z) then return end
  if T.modules.Brain and T.SetModuleEnabled then T:SetModuleEnabled("Brain", false) end
  M.LeaveCombat()
  M.RunSlash("/tai module Planner off")   -- the route is set by hand (Planner:Evaluate still runs)
  M.mapRects[1440] = { top = 4672.9, left = 1700.0, h = 4672.9 - 829.2, w = 1700 + 4066.7, continent = 1 }
  M.mapNames[1440] = "Orneval"
  T.Geo.rects = {}
  M.player.level = 24
  M.group = {}
  local function goTo(x, y) M.pos = { mapID = 1440, x = x / 100, y = y / 100 } end
  goTo(30, 30)
  M.quests = {}
  M.FireEvent("QUEST_LOG_UPDATE")
  QB.char.phantom, QB.char.visits, QB._visit = {}, {}, {}
  local function route(steps)
    P.auto = { id = "auto", title = "t", steps = steps, minutes = 10 }
    P.steps, P.key = nil, nil
    P:Evaluate()
  end
  local function find(k, q)
    for _, s in ipairs(P.steps or {}) do if s.k == k and s.q == q then return s end end
  end

  -- ================================================================== A. a « Prendre » is done ONLY by the quest log
  check(P.Skippable and not P.Skippable({ k = "pick" }) and not P.Skippable({ k = "turn" }) and P.Skippable({ k = "fly" }),
    "A: quest kinds are never « passed » by arrival (even without an id)")
  route({ { k = "fly", map = 1440, x = 30, y = 30 }, { k = "pick", q = 9001, map = 1440, x = 50, y = 50 },
    { k = "obj", q = 9001, o = 1, map = 1440, x = 60, y = 60 }, { k = "turn", q = 9001, map = 1440, x = 50, y = 50 } })
  -- the old index key (« auto:auto:2 ») set by an arrival on another stop: never makes the pick done
  P.char.skip["auto:auto:2"] = true
  P.char.skip[P.SkipKey(P.key, P.steps[1])] = true      -- arrived at the flight master (identity key)
  P:Evaluate()
  check(P.steps[1]._done and not find("pick", 9001)._done and P.cursor == 2, "A: flight stop passed, « Prendre » still stop 1 (cursor " .. tostring(P.cursor) .. ")")
  -- the route is rebuilt with another order: the pick takes the flight's old rank, still not done
  route({ { k = "pick", q = 9001, map = 1440, x = 50, y = 50 }, { k = "fly", map = 1440, x = 30, y = 30 },
    { k = "obj", q = 9001, o = 1, map = 1440, x = 60, y = 60 }, { k = "turn", q = 9001, map = 1440, x = 50, y = 50 } })
  check(not find("pick", 9001)._done and P.steps[2]._done, "A: rebuilt route: the pass follows the flight stop, not its rank")
  -- at the giver (≤ 10 m) for a while: still to do; the compass target is a « pickup », never « Dans la zone » ✓
  goTo(50, 50)
  M.Advance(10, 1)
  P:Evaluate()
  check(not find("pick", 9001)._done and P.steps[P.cursor] == find("pick", 9001), "A: standing at the giver does not take the quest")
  if R.CompassTarget and R.IsEnabled and R:IsEnabled() and R.char and not R.char.paused then
    local okT, t = pcall(R.CompassTarget, R, { mapID = 1440, x = 0.5, y = 0.5 })
    check(okT and (t == nil or t.kind == "pickup"), "A: compass target of a « Prendre » = pickup (" .. tostring(okT and t and t.kind) .. ")")
  end
  -- the quest really accepted (log + QUEST_ACCEPTED): done
  M.quests = { { id = 9001, title = "Les crocs", level = 24, map = 1440, objectives = { { text = "Croc : 0/5", have = 0, need = 5, type = "item" } } } }
  M.FireEvent("QUEST_ACCEPTED", 1, 9001)
  M.FireEvent("QUEST_LOG_UPDATE")
  P:Evaluate()
  check(find("pick", 9001)._done, "A: in the quest log → « Prendre » done")

  -- ================================================================== B. phantom quests (offered list at the giver)
  M.quests = {}
  M.FireEvent("QUEST_LOG_UPDATE")
  route({ { k = "pick", q = 9101, map = 1440, x = 50, y = 50 }, { k = "pick", q = 9102, map = 1440, x = 50.1, y = 50 },
    { k = "pick", q = 9103, map = 1440, x = 70, y = 70 }, { k = "obj", q = 9101, o = 1, map = 1440, x = 60, y = 60 } })
  goTo(50, 50)
  M.units = M.units or {}
  M.units.npc = { name = "Garde Rubis" }
  local GI0 = rawget(_G, "C_GossipInfo")
  local GI = type(GI0) == "table" and GI0 or {}
  rawset(_G, "C_GossipInfo", GI)
  local oldAvail = GI.GetAvailableQuests
  GI.GetAvailableQuests = function() return { { questID = 9101, title = "Les crocs", questLevel = 24 } } end
  M.FireEvent("GOSSIP_SHOW")
  GI.GetAvailableQuests = oldAvail
  rawset(_G, "C_GossipInfo", GI0)
  local ph = QB.Phantom(9102)
  check(ph and ph.why:find("Garde Rubis", 1, true) and ph.lv == 24 and ph.at, "B: 9102 not offered by its giver → « pas proposée » (reason, date, level)")
  check(not QB.Phantom(9101) and not QB.Phantom(9103), "B: the offered quest and another NPC's quest are kept")
  check(QB.char.phantom[9102] ~= nil, "B: kept per character (saved)")
  P:Evaluate()
  local p2 = find("pick", 9102)
  check(p2 and p2._other, "B: the phantom leaves the route")
  local okA, why = Z:Accept(9102, nil, nil, 24, 1)
  check(not okA and why == "pas proposée", "B: ZonePlan refuses it (« pas proposée »)")
  check(not QB.Eligible(9102, { st = "avail", s = { map = 1440, x = 50, y = 50 } }, { log = {}, level = 24, group = 1 }), "B: not in « Quêtes à prendre »")
  -- a quest opened from the list (QUEST_DETAIL just after): the others are not judged again
  M.FireEvent("QUEST_DETAIL")
  check(not QB.Phantom(9101), "B: QUEST_DETAIL after the list judges nothing")
  -- level up: tried again
  M.player.level = 25
  check(not QB.Phantom(9102), "B: next level → tried again")
  M.player.level = 24
  -- (5.1 ROUTE6, audit B4) reached (NPC targeted) and left without it, as often as he likes: NEVER a phantom from a
  -- walk-past (5.0.0 removed the quest at the 2nd one) ; the absent-giver question is tests/wow/route6_phantom_scenario.lua
  QB.char.phantom, QB.char.visits, QB._visit = {}, {}, {}
  route({ { k = "pick", q = 9201, map = 1440, x = 40, y = 40 }, { k = "obj", q = 9201, o = 1, map = 1440, x = 60, y = 60 } })
  for _ = 1, 3 do
    goTo(40, 40) QB.VisitTick()
    goTo(46, 46) QB.VisitTick()
    P:Evaluate()
    check(not QB.Phantom(9201) and not find("pick", 9201)._done and not find("pick", 9201)._other, "B: left without it → the stop stays")
  end
  check(not QB.Phantom(9201), "B: giver reached three times and left → nothing is removed by a walk-past")
  check(QB.MarkPhantom(9201, "PNJ absent : test", "t", "player") and QB.Phantom(9201), "B: dismissed by the player (« Pas là »)")
  M.FireEvent("QUEST_ACCEPTED", 1, 9201)
  check(not QB.Phantom(9201), "B: QUEST_ACCEPTED clears it")
  M.units.npc = nil

  -- ================================================================== C. « Monter vite » (fastlvl)
  local ids = {}
  for _, m in ipairs(QB.Modes()) do ids[#ids + 1] = m.id end
  check(table.concat(ids, ","):find("fastlvl", 1, true) and QB.ModeInfo("fastlvl").label == "Monter vite", "C: mode fastlvl « Monter vite » listed")
  M.RunSlash("/tai vite")
  check(QB.Mode() == "fastlvl", "C: /tai vite → fastlvl (" .. tostring(QB.Mode()) .. ")")
  -- a finished quest close by + a learned grinding spot, quests run dry
  M.quests = { { id = 9301, title = "Rapport au camp", level = 24, map = 1440, complete = true,
    objectives = { { text = "Rapport : 1/1", have = 1, need = 1, done = true } } } }
  M.waypoints = M.waypoints or {}
  M.waypoints[9301] = { 1440, 0.51, 0.5 }
  M.FireEvent("QUEST_LOG_UPDATE")
  QB.Bump()
  route({ { k = "turn", q = 9301, map = 1440, x = 51, y = 50 } })
  goTo(50, 50)
  QB.db.grind = {}
  QB._lastKill = nil
  for _ = 1, 8 do
    local xp, name = QB.ParseXP("Loup des bois meurt, vous gagnez 150 points d'expérience.")
    QB.LearnKill(xp, name)
    M.Advance(20, 5)
  end
  goTo(50, 50)
  local g = QB.BestGrind()
  check(g and g.rate > 0 and g.name == "Loup des bois", "C: grinding spot learned from kills (" .. tostring(g and g.rate) .. " XP/s)")
  local plan = QB.FastPlan()
  check(plan.now and plan.now.kind == "turn" and plan.now.q == 9301, "C: « Fais ça maintenant » = the finished quest nearby (" .. tostring(plan.now and plan.now.kind) .. ")")
  local hasGrind = false
  for _, c in ipairs(plan.nexts) do if c and c.kind == "grind" then hasGrind = true end end
  check(hasGrind, "C: quests run dry → the learned grinding spot is next")
  local lines = QB.FastLines()
  check(#lines >= 2 and #lines <= 4 and lines[1].text:find("^Fais ça maintenant : ") and lines[1].text:find("XP", 1, true)
    and lines[1].text:find("min", 1, true), "C: one line « Fais ça maintenant » (XP, time, why) + up to 3 next (" .. tostring(lines[1] and lines[1].text) .. ")")
  local pl = P:Lines()
  check(pl[1] and pl[1].text == lines[1].text, "C: the route view starts with « Fais ça maintenant »")
  -- rested XP doubles the grinding rate
  M.player.rested = 5000
  local plan2 = QB.FastPlan()
  local gr2
  for _, c in ipairs(plan2.nexts) do if c and c.kind == "grind" then gr2 = c end end
  check(gr2 and gr2.why:find("reposée", 1, true), "C: rested XP used (grinding × 2)")
  M.player.rested = 0
  QB.SetMode("zone")

  -- ================================================================== D. a zone chosen by the player stays
  local FW = Z.FallbackWhy
  local exp1 = { quests = { { id = 1 } } }
  check(FW({ zoneBy = "player", choice = 1 }, {}, { loin = 3, zone = 0 }, exp1), "D: far away (quests too far for now) → zone kept")
  check(FW({ zoneBy = "player", choice = 1 }, {}, { ["élite"] = 2 }, exp1), "D: quests filtered by a setting → zone kept")
  check(FW({ zoneBy = "player", choice = 1 }, {}, {}, { quests = {} }), "D: quest data not read yet → zone kept")
  check(FW({ zoneBy = "player", choice = 1 }, { { q = 1, steps = {} } }, {}, exp1), "D: quests without a known place → zone kept")
  check(FW({ zoneBy = "player", choice = 2, fallbackFor = 2 }, {}, {}, exp1), "D: never twice for one choice")
  check(FW({ zoneBy = "player", choice = 1 }, {}, { zone = 4, loin = 0 }, exp1) == nil, "D: truly nothing doable → back to auto (once)")
  check(FW({ zoneBy = "brain" }, {}, { loin = 3 }, exp1) == nil, "D: a zone chosen by the pilot: as before")
  local c0 = tonumber(Z.char.choice) or 0
  Z:PickZone(1440, "player")
  check(Z.char.zoneBy == "player" and Z.char.choice == c0 + 1 and Z.char.mode == "zones", "D: PickZone(player) = a new choice")
  Z:SetMode("auto")

  -- ================================================================== E. green quests: real XP, XP/min, low ROI dropped
  local Eg = P.Engine
  local v = QB.Value(9501, { lv = 18, xp = 900, o = {}, s = { map = 1440, x = 50, y = 50 } },
    { level = 24, group = 1, here = { map = 1440, x = 50, y = 50 }, cost = {}, log = {} })
  check(v.green and v.xp == math.floor(900 * Eg.QuestXPFactor(24, 18) + 0.5), "E: green quest XP scaled by the level gap (" .. tostring(v.xp) .. ")")
  local wj = table.concat(v.why, " · ")
  check(wj:find("XP/min", 1, true) and wj:find("verte", 1, true), "E: why shows XP, XP/min and « verte » (" .. wj .. ")")
  local kept = QB.DropLowGreens({ { questID = 1, score = 100 }, { questID = 2, score = 30, green = true, eta = 400 },
    { questID = 3, score = 30, green = true, eta = 40 }, { questID = 4, score = 30, green = true, eta = 400, chain = true },
    { questID = 5, score = 30, eta = 400 }, { questID = 6, score = 70, green = true, eta = 400 } })
  local ks = {}
  for _, e in ipairs(kept) do ks[#ks + 1] = e.questID end
  check(table.concat(ks, ",") == "1,3,4,5,6", "E: low-ROI green dropped unless on the way / chain (" .. table.concat(ks, ",") .. ")")
end
