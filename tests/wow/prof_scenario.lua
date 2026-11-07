-- Scenario for Modules/Prof.lua (agent PROF): Classic skill-up formula, cheapest path, shopping list, « fais N× X
-- maintenant », gathering helper, Legacy points, squad professions, tab « Métiers » + /tai metiers. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local P = T.modules.Prof
  ctx.check(P and P.db and P.Engine, "Prof registered")
  ctx.check(T.Data and T.Data.Recipes and T.Data.Recipes.r[129], "Data/Recipes.lua loaded")
  if not (P and T.Data and T.Data.Recipes) then return end
  local E = P.Engine
  local function near(a, b) return math.abs(a - b) < 1e-9 end

  -- formula: orange below yellow, linear (grey - skill) / (grey - yellow), grey = 0, overtime +4 %/rank
  ctx.check(E.Chance(20, 30, 60) == 1, "orange = 100 %")
  ctx.check(near(E.Chance(45, 30, 60), 0.5), "yellow/green interpolation")
  ctx.check(E.Chance(60, 30, 60) == 0 and E.Chance(10, 30, 60, 0, 20) == 0, "grey / not learnable = 0")
  ctx.check(near(E.Chance(45, 30, 60, 5), 0.7) and E.Chance(31, 30, 60, 5) == 1, "Working Overtime capped")
  local row = { 1, 0, 1, 1, 30, 60, 1, 0, "x", {} }
  ctx.check(E.Color(10, row) == "orange" and E.Color(40, row) == "yellow" and E.Color(50, row) == "green"
    and E.Color(60, row) == "grey", "colours")
  ctx.check(E.SkinLevel(50) == 15 and E.SkinLevel(150) == 30, "skinning level")
  ctx.check(E.NodeColor(80, 70) == "orange" and E.NodeColor(130, 70) == "green", "node colour")
  local earned, nl, ns = E.Legacy({ [197] = 230, [182] = 300, [171] = 140 }, { [197] = "c", [182] = "g", [171] = "c" })
  ctx.check(earned == 2 and nl == 171 and ns == 150, "legacy points " .. tostring(earned))

  -- tiny synthetic plan: cheap orange recipe first, then the other once grey
  local rows = {
    { 10, 0, 1, 1, 5, 10, 1, 0, "A", { 900, 1 } },
    { 11, 0, 1, 1, 20, 40, 1, 0, "B", { 901, 2 } },
  }
  local res = E.Plan(rows, 1, 12, function(id) return id == 900 and 10 or 50 end, { [900] = 3 })
  ctx.check(res.reached == 12 and res.steps[1].name == "A" and res.steps[#res.steps].name == "B", "plan order")
  ctx.check(res.used[900] == 3 and res.shopping[901] and res.shopping[901] >= 4, "stock used before buying")

  -- the module on the mock: First Aid 40, Tailoring 148, Herbalism 85
  M.LeaveCombat()
  M.skills = { { name = "Secourisme", rank = 40, max = 75 }, { name = "Couture", rank = 148, max = 150 },
    { name = "Herboristerie", rank = 85, max = 150 } }
  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13 })
  M.SetBagItem(1, 1, 2589, 12)
  rawset(_G, "GetRealZoneText", function() return "Les Tarides" end)
  local SQ = T.Squad
  local oldML = SQ and SQ.MemberList
  if SQ then
    SQ.MemberList = function() return { { name = "Bravo", st = { pr = "Alchimie 150 Minage 120" } } } end
  end
  P.Update(true)
  ctx.check(#P.profs == 3 and P.profs[1].line == 197, "professions read (crafting first)")
  ctx.check(T.tabs.prof and T.tabs.prof.title == "Métiers", "tab « Métiers » registered")
  local texts = {}
  for _, l in ipairs(T:GetTabLines("prof")) do texts[#texts + 1] = T.PlainText(l.text) end
  local all = table.concat(texts, "\n")
  ctx.check(all:find("Legacy (métiers) : 0 point", 1, true) and all:find("Couture 150", 1, true), "legacy line: " .. all:sub(1, 200))
  ctx.check(all:find("Fais %d+× Bandage épais en lin maintenant %(orange%)"), "now tip: " .. all)
  ctx.check(all:find("Secourisme 40/75", 1, true) and all:find("40>", 1, true), "first aid route")
  ctx.check(all:find("Achats > ", 1, true) and all:find("total", 1, true), "shopping list")
  ctx.check(all:find("Meilleures zones (70–124)", 1, true) and all:find("tu y es", 1, true), "gathering zones")
  ctx.check(all:find("Bravo : Alchimie 150", 1, true) and all:find("potions", 1, true), "squad professions")
  ctx.check(all:find("Camp : ", 1, true), "camp tip")
  local nowPinned = false
  for _, l in ipairs(T:GetTabLines("prof")) do if l.pin and T.PlainText(l.text):find("maintenant", 1, true) then nowPinned = true end end
  ctx.check(nowPinned, "now tip pinned in « Maintenant »")

  -- target via slash, recomputed; in combat nothing is recomputed
  M.RunSlash("/tai metiers cible 60 secourisme")
  ctx.check(P.char.target[129] == 60, "slash target")
  -- clicks: header cycles the target, toolbar toggles « Heures sup. » rank
  local clicked = 0
  for _, l in ipairs(T:GetTabLines("prof")) do
    local t = T.PlainText(l.text)
    if l.onClick and t:find("^Heures sup") then l.onClick() clicked = clicked + 1 break end
  end
  ctx.check(clicked == 1 and P.db.overtime == 1, "overtime button")
  P.db.overtime = 0
  for _, l in ipairs(T:GetTabLines("prof")) do
    if l.onClick and T.PlainText(l.text):find("^Secourisme 40/75") then l.onClick() break end
  end
  ctx.check(P.char.target[129] == 75, "header click = next target " .. tostring(P.char.target[129]))
  M.RunSlash("/tai metiers cible 60 secourisme")
  local plan
  for _, p in ipairs(P.out.plans) do if p.line == 129 then plan = p end end
  ctx.check(plan and plan.target == 60 and plan.reached == 60, "plan to 60")
  M.EnterCombat()
  local before = P.lines
  P.Update(true)
  ctx.check(P.lines == before, "no recompute in combat")
  M.LeaveCombat()
  T:Fire("SUMMARY")
  local s = T:Summary()
  ctx.check(s.prof and s.prof.legacy and #s.prof.profs == 3, "summary.prof")
  if SQ then SQ.MemberList = oldML end
end
