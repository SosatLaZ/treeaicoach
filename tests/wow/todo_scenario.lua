-- Scenario for « À faire » (agent TODO), run on both API flavours by tests/wow/test_todo.py.
-- Data from mocks: quest log + Tracker cost model, Squad mates, Abandon verdicts, Predict errands, Planner steps,
-- Missions board, Boost XP stats. Layout (rows, heights, no overlap), filters, search, pins, hide, order (drag /
-- nudge), compact « Quêtes » tab, buttons (filters, sort, route, compass). Fake names only.
return function(M, ctx)
  M.RunSlash("/tai module Planner off")
  local T = TreeAI
  local check = ctx.check
  local TD, TR, Q, C = T:GetModule("Todo"), T:GetModule("Tracker"), T:GetModule("Quests"), T:GetModule("Compass")
  check(TD and TR and Q and C and T.Todo == TD, "Todo, Tracker, Quests, Compass registered")
  if not (TD and TR and Q and C) then return end

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
    { header = "Les Paluns" },
    { title = "Crocs de raptor", level = 29, complete = false, xp = 2600,
      objectives = { { text = "Croc de raptor : 0/6", done = false, have = 0, need = 6 } } },
  }
  -- friends (Squad) + verdicts (Abandon) + errands (owners via T.Predict.list) + board (Missions) + XP (Boost)
  local S = T:GetModule("Squad")
  S.IsActive = function() return true end
  S.MemberList = function()
    return { { name = M.player.name, quests = {} },
      { name = "Lea-Royaume", st = { c = "MAGE" }, quests = { [1003] = { done = false, obj = { { have = 5, need = 8 } } } } } }
  end
  local tabs = {}
  T.SetTabLines = function(_, id, lines, src) if not src then tabs[id] = lines end end
  Q.Update()
  TR.Update()
  check(#TR.items == 4, "tracker model: 4 quests")
  local ids = {}
  for _, it in ipairs(TR.items) do ids[it.q.title] = it.q.id end
  local RAPTOR, BEARS, LETTER, LION = ids["Crocs de raptor"], ids["Les ours de Hautebrande"], ids["Le courrier"], ids["Sang de lion"]
  check(RAPTOR and BEARS and LETTER and LION, "quest ids")
  local A = T:GetModule("Abandon")
  A.evals = {
    { q = { id = RAPTOR, reward = { item = "Bottes du marais", pct = 12 } }, verdict = "later", short = "loin d'ici",
      text = "Plus tard « Crocs de raptor » : loin d'ici.", score = 42 },
    { q = { id = BEARS }, verdict = "keep", short = "ok", text = "Garde « Les ours » : Lea l'a aussi.", score = 80 },
  }
  -- (CLEAN 4.0) Predict retiré : les courses = les lignes réelles des propriétaires, lues par la façade T.Predict.list
  local P = T.Predict
  rawset(P, "list", { { id = "repair", text = "Réparation bientôt : durabilité 30 %", prio = 2 }, { id = "ammo", text = "Munitions : ~40 min de tir", prio = 1 },
    { id = "drop:x", text = "ignored", prio = 1 } })
  local Mi = T:GetModule("Missions")
  Mi.board = { { id = BEARS, title = "Les ours de Hautebrande", pct = 50, holders = { "A", "B" }, done = { "B" }, need = { "A" },
    missing = {}, multi = true, per = {} } }
  Mi.next = { text = "Rendre ensemble « Le courrier »", mission = Mi.board[1] }
  local B = T:GetModule("Boost")
  B.XPStats = function() return { perHour = 12300, eta = 2280, remaining = 7000 } end

  -- model
  local m = TD.Collect()
  check(#m.cards == 4 and m.total == 4, "4 cards")
  check(#m.zones == 2 and m.zones[1].zone == ZONE, "grouped by zone, current zone first: " .. tostring(m.zones[1] and m.zones[1].zone))
  check(#m.errands == 2 and m.errands[1].id == "repair" and m.errands[1].icon:find("Trade_BlackSmithing"), "errands from Predict (quest drops ignored)")
  check(m.now and m.now.kind == "quest" and m.now.questID == TR.items[1].q.id, "Maintenant = best card of the cost model")
  check(m.now.why ~= "" and m.now.title ~= "", "now: title + why: " .. tostring(m.now.why))
  check(m.board and m.board[1].pct == 50 and m.board.next:find("courrier"), "group mini-board (Missions)")
  check(m.session and m.session.xph == 12300 and TD.SessionText(m.session):find("niveau 28 dans ≈ ", 1, true) and not TD.SessionText(m.session):find("XP/h"), "session: " .. TD.SessionText(m.session))
  local byId = {}
  for _, c in ipairs(m.cards) do byId[c.id] = c end
  local lea = byId[BEARS].mates[1]
  check(lea and lea.name == "Lea" and lea.class == "MAGE" and lea.have == 5, "friend avatar class + progress")
  check(byId[RAPTOR].verdict.v == "later" and byId[RAPTOR].reward.pct == 12, "verdict + reward upgrade")
  check(byId[LETTER].complete and byId[LION].objs[1].need == 10, "ready badge + objectives")

  -- compact quests tab (through Tracker.Push)
  check(TR.Push() == "todo", "Tracker.Push hands the tab to Todo")
  local L = tabs.quests
  local planActive = T.Planner and T.Planner.GetActiveSteps and T.Planner:GetActiveSteps(1) ~= nil
  check(L and L[1].now and (L[1].pin or planActive) and L[1].text:find("À faire maintenant", 1, true) and T.Plain.IsPlain(L[1].text),
    "tab: Maintenant first, pinned to the Now tab (unless the planner already pins its step)")
  local raptor, courses, open
  for _, l in ipairs(L) do
    if l.questID == RAPTOR then raptor = l end
    if l.text:find("Courses") then courses = l end
    if l.text:find("fenêtre « À faire »") then open = l end
  end
  check(raptor and raptor.text:find("À plusieurs", 1, true) and not raptor.text:find("%", 1, true)
    and table.concat(raptor.tooltip, "\n"):find("mieux que ton équipement", 1, true), "tab line: name + one status word: " .. tostring(raptor and raptor.text))
  for _, l in ipairs(L) do
    local okp, w = T.Plain.IsPlain(l.text)
    check(okp, "plain todo line (no " .. tostring(w) .. "): " .. tostring(l.text))
  end
  check(courses and courses.prio == 2, "tab: errands line")
  check(open and open.onClick, "tab: open-window button")

  -- window
  open.onClick()
  local f = rawget(_G, "TreeAITodoFrame")
  check(f and f:IsShown() and TD.db.shown, "window shown from the tab button")
  M.Advance(1)
  local n = TD.Render(true)
  check(n and n > 10, "rows rendered: " .. tostring(n))
  local kinds, prevEnd, ok = {}, -1, true
  for _, r in ipairs(TD.rows) do
    kinds[r.kind] = (kinds[r.kind] or 0) + 1
    if r.y < prevEnd then ok = false end
    prevEnd = r.y + r.h
  end
  check(ok and math.abs(prevEnd - TD.contentH) < 1e-6, "layout: rows stacked, no overlap, content height = sum")
  check(kinds.section >= 4 and kinds.now == 1 and kinds.zone == 2 and kinds.card == 4 and kinds.errand == 2 and (kinds.board or 0) >= 1
    and kinds.mates == 1, ("layout: sections %s now %s zone %s card %s errand %s board %s mates %s"):format(tostring(kinds.section), tostring(kinds.now), tostring(kinds.zone), tostring(kinds.card), tostring(kinds.errand), tostring(kinds.board), tostring(kinds.mates)))
  check((kinds.obj or 0) >= 3, "layout: objective bars for unfolded cards")
  check(f.session:IsShown() and f.session.fs:GetText():find("Niv 27", 1, true) and not f.session.fs:GetText():find("XP/h"), "session bar")

  -- plan steps
  T.Planner = { GetActiveSteps = function() return { { k = "obj", q = BEARS, map = 1424, x = 0.5, y = 0.3, label = "Tuer 5 Ours", eta = 6 },
    { k = "turn", q = LETTER, map = 1424, x = 48, y = 41, label = "Rendre « Le courrier »" } }, 3, 12, "XP/h max" end }
  m = TD.Collect()
  check(m.steps and #m.steps == 2 and m.steps[2].point and math.abs(m.steps[2].point.x - 0.48) < 1e-6, "plan steps (Planner contract)")
  check(m.now.kind == "step" and m.now.title:find("Tuer 5 Ours"), "Maintenant = plan step when a plan is active")
  local rows = TD.Rows(m)
  local nsteps = 0
  for _, r in ipairs(rows) do if r.kind == "step" and not r.group then nsteps = nsteps + 1 end end
  check(nsteps == 2, "Étapes du trajet section")
  check(TD.Go(m.now) == "compass" and C.Current() and C.Current().kind == "custom", "Boussole button → compass waypoint")
  T.Planner = nil
  M.RunSlash("/tai way effacer")

  -- filter chips (buttons)
  local function chip(id) for _, c in ipairs(f.chips) do if c.id == id then c:Click() end end end
  chip("pretes")
  m = TD.Collect()
  check(TD.char.filter == "pretes" and #m.cards == 1 and m.cards[1].id == LETTER, "chip « Prêtes »")
  chip("zone")
  m = TD.Collect()
  local other = false
  for _, c in ipairs(m.cards) do if c.zone ~= ZONE then other = true end end
  check(not other and #m.cards >= 3, "chip « Zone »")
  chip("tous")
  -- search
  f.search:SetText("raptor")
  M.Script(f.search, "OnTextChanged")
  m = TD.Collect()
  check(#m.cards == 1 and m.cards[1].id == RAPTOR, "search box")
  f.search:SetText("")
  M.Script(f.search, "OnTextChanged")
  check(#TD.Collect().cards == 4, "search cleared")

  -- pin (checkbox on the card row) → first + Maintenant
  TD.Render(true)
  local btn
  for _, child in ipairs(f.content.__children) do if child.row and child.row.kind == "card" and child.row.c.id == RAPTOR then btn = child end end
  check(btn, "card widget for the raptor quest")
  btn.pin:Click()
  m = TD.Collect()
  check(TD.char.pins["q:" .. RAPTOR] and m.cards[1].id == RAPTOR and m.now.questID == RAPTOR and m.now.why:find("épinglée"), "pin → first + Maintenant")
  chip("epingles")
  check(#TD.Collect().cards == 1, "chip « Épinglées »")
  chip("tous")
  TD.TogglePin("q:" .. RAPTOR)
  check(not TD.char.pins["q:" .. RAPTOR], "unpin")

  -- hide + show all
  TD.ToggleHidden("q:" .. LION)
  m = TD.Collect()
  check(#m.cards == 3 and #m.hidden == 1, "hide a card")
  TD.ToggleHidden("c:ammo")
  m = TD.Collect()
  check(#m.errands == 1 and #m.hiddenErrands == 1, "hide an errand")
  local foot
  for _, r in ipairs(TD.Rows(m)) do if r.kind == "foot" then foot = r end end
  check(foot and foot.text:find("2 masqués"), "hidden footer")
  TD.ShowAll()
  check(#TD.Collect().cards == 4, "show all")

  -- order: pure move, drop, nudge, target under cursor, reset
  check(table.concat(TD.MoveKey({ "a", "b", "c" }, "c", "a"), ",") == "c,a,b", "MoveKey before")
  check(table.concat(TD.MoveKey({ "a", "b", "c" }, "a", "c", true), ",") == "b,c,a", "MoveKey after")
  m = TD.Collect()
  local last = m.all[#m.all].key
  TD.DropOn(last, m.all[1].key)
  m = TD.Collect()
  check(m.all[1].key == last and #TD.char.order == 4, "drag & drop order saved")
  local key2, zone2 = m.all[2].key, m.all[2].zone
  local hasNext = false
  for i = 3, #m.all do if m.all[i].zone == zone2 then hasNext = true end end
  if hasNext then
    TD.Nudge(key2, 1)
    m = TD.Collect()
    local pos
    for i, c in ipairs(m.all) do if c.key == key2 then pos = i end end
    check(pos and pos > 2, "▼ moves within the zone")
  end
  rows = TD.Rows(m)
  local firstCard
  for _, r in ipairs(rows) do if r.kind == "card" then firstCard = firstCard or r end end
  local tk, after = TD.TargetAt(rows, firstCard.y + 1)
  check(tk == firstCard.key and not after, "drop target under the cursor (top half)")
  tk, after = TD.TargetAt(rows, firstCard.y + firstCard.h - 1)
  check(tk == firstCard.key and after, "drop target (bottom half → after)")
  TD.BeginDrag(firstCard.key)
  TD.EndDrag(true)
  check(TD.dragKey == nil, "drag cancelled cleanly")
  f.head.reset:Click()
  check(#TD.char.order == 0, "« Ordre auto » button")

  -- header buttons: sort + route
  local s0 = TR.db.sort
  f.head.sort:Click()
  check(TR.db.sort ~= s0, "sort button cycles the cost-model sort")
  TR.db.sort = "cout"
  f.head.route:Click()
  -- folds: sections, zones
  TD.ToggleSection("quests")
  local cards = 0
  for _, r in ipairs(TD.Rows(TD.Collect())) do if r.kind == "card" then cards = cards + 1 end end
  check(cards == 0, "fold the Quêtes section")
  TD.ToggleSection("quests")
  TD.ToggleFold("z:Les Paluns", false)
  cards = 0
  for _, r in ipairs(TD.Rows(TD.Collect())) do if r.kind == "card" then cards = cards + 1 end end
  check(cards == 3, "fold a zone")
  -- cheap refresh: unchanged content → no relayout
  TD.Render(true)
  local r0 = TD.renders
  TD.Render()
  check(TD.renders == r0, "unchanged content → no relayout")
  -- close button, summary
  f.head.close:Click()
  check(not f:IsShown() and not TD.db.shown, "close button")
  TD.WriteSummary()
  local st = T:Summary().todo
  check(st and st.filter == "tous" and st.now, "summary.todo")
  -- disabled Todo → Tracker's own tab again
  M.RunSlash("/tai module Todo off")
  check(TR.Push() == "tab", "Todo off → historic tab")
end
