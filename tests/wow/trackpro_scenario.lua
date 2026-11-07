-- Scenario for the quest tracker (agent TRACK-PRO), run on both API flavours by tests/wow/test_trackpro.py.
-- Cost model, sorting with hysteresis, filters, drop-rate aware hints, chain preview, story, group overlay
-- (Squad + IsUnitOnQuest), plan steps first (TreeAI.Planner contract), standalone frame, completion sound.
-- Fake names only. Fake Questie DB (read-only use).
return function(M, ctx)
  local T = TreeAI
  local TR, Q, C, P = T:GetModule("Tracker"), T:GetModule("Quests"), T:GetModule("Compass"), T:GetModule("Predict")
  local check = ctx.check
  check(TR and Q and C and P, "Tracker, Quests, Compass, Predict registered")
  if not (TR and Q and C and P) then return end
  local modern = rawget(_G, "CreateVector2D") ~= nil
  local function near(a, b, eps) return a and b and math.abs(a - b) <= (eps or 1e-6) end

  -- ---------------------------------------------------------------- pure: formats, estimates, difficulty
  check(TR.Time(40) == "40 s" and TR.Time(420) == "7 min" and TR.Time(3900) == "1 h 05", "time format: " .. TR.Time(3900))
  check(TR.Rate(5120) == "5,1k" and TR.Rate(830.4) == "830", "XP/min format: " .. TR.Rate(5120))
  check(TR.MobName("Ours enragé tué") == "Ours enragé" and TR.MobName("Gnolls tués") == "Gnolls" and TR.MobName("Ogre slain") == "Ogre", "mob names")
  local e = TR.ObjEstimate({ name = "Ours enragé tué", have = 2, need = 8, t = "m" }, { secPerKill = 25 })
  check(e and e.mobs == 6 and e.sec == 150 and e.mob == "Ours enragé", "kill objective: 6 left, 150 s")
  e = TR.ObjEstimate({ name = "Sang de lion", have = 2, need = 10, t = "m" },
    { secPerKill = 20, drops = { ["sang de lion"] = { mob = "Lion des collines", rate = 0.5, measured = true } } })
  check(e and e.mobs == 16 and e.sec == 320 and e.measured and e.mob == "Lion des collines", "drop objective: 8 items at 50 % = 16 lions")
  check(TR.ObjHint(e) == "encore ~16 × Lion des collines (50 %) · ≈ 5 min", "drop hint: " .. tostring(TR.ObjHint(e)))
  e = TR.ObjEstimate({ name = "Défense de kobold", have = 0, need = 7, t = "i" }, { secPerKill = 20 })
  check(e and e.mobs == 20 and not e.measured, "unknown drop rate → 35 % assumed (20 mobs for 7)")
  check(TR.ObjEstimate({ name = "x", have = 3, need = 3, finished = true }) == nil, "finished objective → nil")
  check(TR.ObjEstimate({ name = "Caisse", have = 1, need = 4, t = "o" }).sec == 60, "object objective 20 s each")
  local d = TR.Difficulty({ level = 28 }, { pl = 26, group = 1 })
  check(near(d, 3), "+2 levels → ×3 (AGF): " .. d)
  d = TR.Difficulty({ level = 26, elite = true }, { pl = 26, group = 1 })
  check(near(d, 3), "elite solo ×3")
  d = TR.Difficulty({ level = 26, elite = true }, { pl = 26, group = 3 })
  check(near(d, 1.2), "elite at 3 ×1.2")
  local why
  d, why = TR.Difficulty({ level = 25, suggestedGroup = 3 }, { pl = 26, group = 1 })
  check(near(d, 1.8) and why[1] == "groupe de 3", "suggested group 3 solo ×1.8")
  d = TR.Difficulty({ level = 26, dungeon = true }, { pl = 26, group = 2 })
  check(near(d, 4), "dungeon quest at 2 ×4")
  -- score: ready turn-in close by beats a far kill quest
  local a = { key = 1, q = { id = 1, complete = true, xp = 1500, obj = {} }, dist = 70 }
  local b = { key = 2, q = { id = 2, level = 26, xp = 2400, obj = { { name = "Loup tué", have = 2, need = 10, t = "m" } } }, dist = 400 }
  local sctx = { pl = 26, group = 1, speed = 7, secPerKill = 25 }
  TR.Score(a, sctx)
  TR.Score(b, sctx)
  check(near(a.sec, 25) and near(a.xpm, 3600), "ready: 10 s walk + 15 s → 3600 XP/min: " .. tostring(a.xpm))
  check(b.xpm < a.xpm and near(b.work, 200), "kill quest: 8 × 25 s of work")
  local b2 = { key = 3, q = b.q, dist = 400, shared = 1 }
  TR.Score(b2, sctx)
  check(near(b2.work, 140) and b2.xpm > b.xpm, "shared with a friend → work ×0.7")
  -- sort + hysteresis (10 %)
  local x1 = { key = "x1", q = { level = 1, zone = "Z" }, xpm = 100 }
  local x2 = { key = "x2", q = { level = 2, zone = "Z" }, xpm = 105 }
  local s = TR.Sort({ x1, x2 }, "cout", nil)
  check(s[1].key == "x2" and s[1].rank == 1, "cost sort: best XP/min first")
  s = TR.Sort({ x1, x2 }, "cout", "x1")
  check(s[1].key == "x1", "hysteresis: previous top kept within 10 %")
  x2.xpm = 120
  s = TR.Sort({ x1, x2 }, "cout", "x1")
  check(s[1].key == "x2", "hysteresis: switch when ≥ 10 % better")
  s = TR.Sort({ x2, x1 }, "niveau")
  check(s[1].key == "x1", "level sort")
  check(TR.Pass({ q = { complete = true } }, "pretes", {}) and not TR.Pass({ q = {} }, "pretes", {}), "filter: ready")
  check(TR.Pass({ q = {}, shared = 1 }, "groupe", {}) and not TR.Pass({ q = {}, shared = 0 }, "groupe", {}), "filter: group")
  check(TR.Pass({ q = { zone = "Z" } }, "zone", { zone = "Z" }) and not TR.Pass({ q = { zone = "Y" } }, "zone", { zone = "Z" }), "filter: zone")
  check(not TR.Pass({ q = { hard = true } }, "faciles", {}), "filter: easy")
  check(Q.Summarize("Les lions attaquent nos fermes. Ramène-moi leur sang, $N ! Vite. Encore une phrase.")
    == "Les lions attaquent nos fermes. Ramène-moi leur sang, !", "story summary: 2 sentences")

  -- ---------------------------------------------------------------- quest log + fake Questie
  local ZONE = "Contreforts de Hautebrande"
  M.player.zone = ZONE
  M.group = {}
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  M.quests = {
    { header = ZONE },
    { title = "Sang de lion", level = 26, complete = false, xp = 2400,
      objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10 } } },
    { title = "Les ours de Hautebrande", level = 25, complete = false, xp = 2000,
      objectives = { { text = "Ours enragé tué : 3/8", done = false, have = 3, need = 8 } } },
    { title = "Le courrier", level = 24, complete = true, xp = 1500, objectives = {} },
    { title = "Le chef ogre", level = UnitLevel("player") + 3, complete = false, xp = 3000, elite = true, group = 3,
      objectives = { { text = "Chef ogre tué : 0/1", done = false, have = 0, need = 1 } } },
  }
  local npcs = {
    [7] = { name = "Lion des collines", spawns = { [267] = { { 55, 40 } } } },
    [8] = { name = "Ours enragé", spawns = { [267] = { { 50, 30 } } } },
    [9] = { name = "Fermier", spawns = { [267] = { { 48, 41 } } } },
    [11] = { name = "Chef ogre", spawns = { [267] = { { 90, 90 } } } },
  }
  local quests = {
    [1002] = { name = "Sang de lion", objectives = { nil, nil, { { 9001 } } },
      objectivesText = { "Les lions attaquent nos fermes.", "Ramène-moi 10 fioles de leur sang. Merci d'avance." } },
    [1003] = { name = "Les ours de Hautebrande", objectives = { { { 8, "Ours" } } } },
    [1004] = { name = "Le courrier", finishedBy = { { 9 } }, nextQuestInChain = 501 },
    [1005] = { name = "Le chef ogre", objectives = { { { 11, "Chef" } } } },
    [501] = { name = "Aide au fermier", nextQuestInChain = 502, startedBy = { { 9 } } },
    [502] = { name = "La grange brûlée" },
  }
  local fakeDB = {
    QuestPointers = {},
    QueryQuestSingle = function(id, key) return quests[id] and quests[id][key] end,
    QueryNPCSingle = function(id, key) return npcs[id] and npcs[id][key] end,
    QueryItemSingle = function(id, key)
      if id == 9001 and key == "npcDrops" then return { 7 } end
      if id == 9001 and key == "name" then return "Sang de lion" end
    end,
    IsDoable = function() return true end,
  }
  rawset(_G, "QuestieLoader", { _modules = { QuestieDB = fakeDB,
    ZoneDB = { GetUiMapIdByAreaId = function(_, ar) return ar == 267 and 1424 or nil end } } })
  local mapAPI = C_Map
  local oldArea = mapAPI.GetAreaInfo
  mapAPI.GetAreaInfo = function(id) return id == 267 and ZONE or "Ailleurs" end

  -- measured drop rate: 4 lions looted, 2 blood vials → 50 %
  for k = 1, 4 do P.RecordLoot("Lion des collines", (k % 2 == 0) and { { name = "Sang de lion", questItem = true } } or {}) end

  local tabs = {}
  local oldSet = T.SetTabLines
  T.SetTabLines = function(_, id, lines, src) if not src then tabs[id] = lines end end
  M.sounds = {}
  Q.Update()
  TR.Update()
  check(#TR.items == 4, "4 quests ranked")
  local byId = {}
  for _, it in ipairs(TR.items) do byId[it.q.id] = it end
  check(TR.items[1].q.id == 1004, "cost sort: nearby turn-in first: " .. tostring(TR.items[1].q.title))
  check(TR.items[#TR.items].q.id == 1005, "elite +3 solo ranked last")
  local soloDiff = byId[1005].diff
  check(soloDiff > 10 and byId[1005].why[1] == "niv +3" and byId[1005].why[2] == "trop haut seul", "elite +3: difficulty and reasons")
  check(byId[1002].objs["Sang de lion"] and byId[1002].objs["Sang de lion"].mobs == 16, "drop-aware: 16 lions for 8 vials (measured 50 %)")
  check(TR.totalSec and TR.totalSec > 0, "total time for the whole log")

  TR.Push()
  local L = tabs.quests
  check(L and L[1].text:find("tri XP/min") and L[1].text:find("1 prête"), "header: sort, filter, ready count: " .. tostring(L and L[1].text))
  local lion, ready
  for _, l in ipairs(L) do
    if l.questID == 1002 then lion = l end
    if l.questID == 1004 then ready = l end
  end
  check(lion and lion.text:find("encore ~16 × Lion des collines") and lion.text:find("/min"), "line: « encore ~16 lions », XP/min: " .. tostring(lion and lion.text))
  check(ready and ready.text:find("Prête à rendre") and ready.text:find("suite « Aide au fermier » → « La grange"), "chain preview on the ready line: " .. tostring(ready and ready.text))
  local tip = table.concat(lion.tooltip, "\n")
  check(tip:find("Pourquoi ce rang") and tip:find("XP/min"), "tooltip: why this rank")
  check(tip:find("« Les lions attaquent nos fermes%. Ramène%-moi 10 fioles de leur sang%. »"), "tooltip: story summary")
  check(tip:find("encore ~16 × Lion des collines %(50 %%%)"), "tooltip: drop hint with measured rate")
  local rtip = table.concat(ready.tooltip, "\n")
  check(rtip:find("Chaîne 1/3 : → « Aide au fermier » %(Fermier%) → « La grange brûlée »"), "tooltip: chain 1/3: " .. rtip)

  -- filters (header click cycles; slash sets)
  L[1].onClick()
  check(TR.db.filter == "zone", "header click → next filter")
  M.RunSlash("/tai suivi filtre pretes")
  check(#TR.shown == 1 and TR.shown[1].q.id == 1004, "filter « prêtes »")
  M.RunSlash("/tai suivi filtre faciles")
  local hasOgre = false
  for _, it in ipairs(TR.shown) do if it.q.id == 1005 then hasOgre = true end end
  check(not hasOgre and #TR.shown >= 2, "filter « faciles » drops the elite +3")
  M.RunSlash("/tai suivi filtre groupe")
  check(#TR.shown == 1 and TR.shown[1].q.id == 1005, "filter « groupe »: elite only (no friends yet)")
  TR.Push()
  check(tabs.quests[2].text:find("Le chef ogre") or tabs.quests[2].text:find("chef"), "group filter shows the elite")
  M.RunSlash("/tai suivi filtre tous")
  M.RunSlash("/tai suivi tri distance")
  check(TR.db.sort == "distance" and TR.items[1].dist <= TR.items[2].dist, "sort by distance")
  M.RunSlash("/tai suivi tri cout")
  M.RunSlash("/tai suivi masquer 1003")
  local hidden = true
  for _, it in ipairs(TR.shown) do if it.q.id == 1003 then hidden = false end end
  check(hidden, "/tai suivi masquer <id>")
  M.RunSlash("/tai suivi montrer")
  check(#TR.shown == 4, "/tai suivi montrer")

  -- group overlay: a TreeAI friend (Squad) and a party member without the addon (IsUnitOnQuest)
  local S = T:GetModule("Squad")
  local oldActive, oldList = S.IsActive, S.MemberList
  S.IsActive = function() return true end
  S.MemberList = function()
    return { { name = M.player.name, quests = {} },
      { name = "Lea-Royaume", quests = { [1003] = { done = false, obj = { { have = 5, need = 8 } } },
        [1004] = { done = true, obj = {} } } } }
  end
  M.group = { { name = "Bob", class = "WARRIOR", level = 26, online = true } }
  local QL = rawget(_G, "C_QuestLog")
  local hadQL = QL ~= nil
  if not QL then QL = {} rawset(_G, "C_QuestLog", QL) end
  QL.IsUnitOnQuest = function(unit, qid) return unit == "party1" and qid == 1005 end
  TR.Update()
  byId = {}
  for _, it in ipairs(TR.items) do byId[it.q.id] = it end
  check(byId[1003].shared == 1 and byId[1003].mates[1].name == "Lea" and byId[1003].mates[1].have == 5, "Squad friend on the bears: 5/8")
  check(byId[1004].mates[1].done and byId[1004].shared == 0, "friend already finished the letter")
  check(byId[1005].mates[1] and byId[1005].mates[1].name == "Bob" and byId[1005].mates[1].noAddon, "party member without TreeAI via IsUnitOnQuest")
  check(byId[1005].diff < soloDiff * 0.6, "elite +3 much cheaper in a group of 2: " .. byId[1005].diff)
  TR.Push()
  local bears
  for _, l in ipairs(tabs.quests) do if l.questID == 1003 then bears = l end end
  check(bears and bears.text:find("Lea 5/8"), "group overlay on the line: " .. tostring(bears and bears.text))
  M.RunSlash("/tai suivi filtre groupe")
  check(#TR.shown == 2, "filter « groupe »: shared + elite")
  M.RunSlash("/tai suivi filtre tous")
  S.IsActive, S.MemberList = oldActive, oldList
  M.group = {}
  QL.IsUnitOnQuest = nil
  if not hadQL then rawset(_G, "C_QuestLog", nil) end

  -- plan first (TreeAI.Planner contract: GetActiveSteps(n) → steps, index, total, title)
  T.Planner = { GetActiveSteps = function(self, n)
    return { { k = "obj", q = 1003, map = 1424, x = 0.5, y = 0.3, label = "Tuer 5 Ours enragés", eta = 6 },
      { k = "turn", q = 1004, map = 1424, x = 48, y = 41 }, { k = "pick", q = 501 } }, 3, 18, "XP/h max"
  end }
  TR.Push()
  L = tabs.quests
  check(L[2].text:find("Étape 3/18") and L[2].text:find("Tuer 5 Ours enragés") and L[2].text:find("~6 min") and L[2].plan, "plan step first: " .. tostring(L[2].text))
  check(L[3].text:find("^ensuite : Rendre « Le courrier »") and L[4].text:find("^puis : Prendre « Aide au fermier »"), "next 2 plan steps: " .. tostring(L[3].text))
  L[2].onClick()
  local cur = C.Current()
  check(cur and cur.kind == "custom" and near(cur.x, 0.5) and near(cur.y, 0.3), "plan step click → compass")
  M.RunSlash("/tai way effacer")
  T.Planner = { active = { route = { title = "Prudent", steps = { { k = "hs", label = "Foyer" }, { k = "train" } } }, idx = 2 } }
  TR.Push()
  check(tabs.quests[2].text:find("Étape 2/2") and tabs.quests[2].text:find("Formation"), "plan from tables (fallback)")
  T.Planner = nil
  TR.Push()
  check(not tabs.quests[2].plan, "no planner → no plan lines")

  -- standalone compact frame (in addition to the tab): collapsible per quest, font / scale options
  TR.db.standalone = true
  TR.db.scale = 120
  TR.db.fontSize = 13
  check(TR.Push() == "tab+frame", "tab + standalone frame")
  local fr = rawget(_G, "TreeAITrackerFrame")
  check(fr and fr:IsShown() and near(fr:GetScale(), 1.2), "frame shown, scale 120 %")
  local rows = TR.frameRows
  local objRows, barRow = 0, nil
  for _, r in ipairs(rows) do
    if r.kind == "obj" and r.qid == 1002 then objRows = objRows + 1 if r.bar then barRow = r end end
  end
  check(objRows >= 2 and barRow and barRow.bar[1] == 2 and barRow.bar[2] == 10, "objective rows with a real progress bar")
  local btn
  for _, w in ipairs({ fr:GetChildren() }) do if w.row and w.row.kind == "quest" and w.row.qid == 1002 then btn = w end end
  check(btn, "quest row button")
  btn:Click("RightButton")
  local after = 0
  for _, r in ipairs(TR.frameRows) do if r.kind == "obj" and r.qid == 1002 then after = after + 1 end end
  check(after == 0 and TR.char.collapsed["1002"], "right-click collapses one quest")
  TR.ToggleCollapse(1002)
  check(not TR.char.collapsed["1002"], "expand again")
  TR.ToggleCollapse(nil)
  local any = false
  for _, r in ipairs(TR.frameRows) do if r.kind == "obj" then any = true end end
  check(not any and TR.char.allCollapsed, "collapse all")
  TR.ToggleCollapse(nil)
  -- left click on a quest row → compass target
  for _, w in ipairs({ fr:GetChildren() }) do if w.row and w.row.kind == "quest" and w.row.qid == 1003 then btn = w end end
  btn:Click("LeftButton")
  check(C.Current().questID == 1003, "frame click → compass target")
  M.RunSlash("/tai suivi verrou")
  check(TR.db.lock == true, "lock toggle")
  M.RunSlash("/tai suivi verrou")
  M.RunSlash("/tai suivi cadre")
  check(not TR.db.standalone and not fr:IsShown(), "/tai suivi cadre toggles the frame off")

  -- completion sound: only on a transition to complete, once
  local n0 = #M.sounds
  M.quests[3].objectives[1] = { text = "Ours enragé tué : 8/8", done = true, have = 8, need = 8 }
  M.quests[3].complete = true
  Q.Update()
  check(#M.sounds == n0 + 1, "sound when a quest becomes complete")
  Q.Update()
  check(#M.sounds == n0 + 1, "no repeat")
  TR.db.sound = false
  M.quests[2].objectives[1] = { text = "Sang de lion : 10/10", done = true, have = 10, need = 10 }
  M.quests[2].complete = true
  Q.Update()
  check(#M.sounds == n0 + 1, "sound option off")
  TR.db.sound = true

  -- combat: no recompute, but progress copied into the lines
  M.quests[2].objectives[1] = { text = "Sang de lion : 4/10", done = false, have = 4, need = 10 }
  M.quests[2].complete = false
  TR.Update()
  M.EnterCombat()
  M.quests[2].objectives[1] = { text = "Sang de lion : 5/10", done = false, have = 5, need = 10 }
  local before = TR.items
  Q.Update()
  M.Advance(1)
  check(TR.items == before and TR.dirty, "no re-plan in combat")
  TR.Push()
  local l5
  for _, l in ipairs(tabs.quests) do if l.questID == 1002 then l5 = l end end
  check(l5 and l5.text:find("5/10"), "progress copied in combat: " .. tostring(l5 and l5.text))
  TR:AutoRefresh()
  check(TR.items == before, "AutoRefresh does nothing in combat")
  M.LeaveCombat()
  M.FireEvent("PLAYER_REGEN_ENABLED")
  M.Advance(3)
  check(not TR.dirty, "re-planned after combat")
  TR.WriteSummary()
  local st = T:Summary().tracker
  check(st and st.route[1].xpm and st.sort == "cout" and st.filter == "tous" and st.totalMin, "summary.tracker: xpm, sort, filter, total")
  check(M.questSel == nil, "never selected a quest log entry")

  T.SetTabLines = oldSet
  mapAPI.GetAreaInfo = oldArea
  rawset(_G, "QuestieLoader", nil)
end
