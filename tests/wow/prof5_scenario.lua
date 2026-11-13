-- Scenario for the 5.0 PROF round (Modules/Prof.lua engine, ProfLevel.lua, ProfGuide.lua, CityRun.lua): a level-30
-- Horde hunter with Cuisine 92/150, Secourisme 42/75, Travail du cuir 92/150 (+ Dépeçage). The four real complaints as
-- regressions (AH advised when the trainer was better ; no jump between steps ; « Apprendre X à 67 » at 92 ;
-- « 28/271 Cuir moyen »), property checks over every profession and skill 1..299, the trainer window read with BOTH
-- client signatures, « tout ce que tu peux apprendre d'utile » (one click = one recipe), the passive hint (once), the
-- BAGS keeper, the page. PROF5_SEE=1 dumps what the player sees. Fake names only; nothing in chat.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local P, CR = T.modules.Prof, T.CityRun
  local PL = P and P.Level
  check(PL and PL.Offers and PL.Steps5 and PL.Check and P.Engine.Audit, "Prof + ProfLevel + ProfGuide loaded")
  if not (PL and PL.Offers) then return end
  local E = P.Engine
  local D = T.Data.Recipes
  local SEE = os.getenv("PROF5_SEE") == "1"
  local function see(...) if SEE then io.stderr:write(table.concat({ ... }, " "), "\n") end end
  local function chat()
    local n = 0
    for _, l in ipairs(M.output) do if not l:find("^%[UIError%]") and not l:find("^%[RaidNotice%]") then n = n + 1 end end
    return n
  end
  local chat0 = chat()

  -- ------------------------------------------------------------------ 1. engine: property checks (all lines, 1..299)
  local vendor, price = {}, {}
  for id, v in pairs(D.mats) do if v[3] == 1 then vendor[id] = v[2] elseif id % 5 ~= 0 then price[id] = 300 + (id % 7) * 100 end end
  local plans, badN, firstBad, deep, ranged = 0, 0, nil, 0, 0
  local MINE = { [129] = true, [185] = true, [165] = true }
  for line, rows in pairs(D.r) do
    local stepSk = MINE[line] and 1 or 7
    for skill = 1, 299, stepSk do
      for _, cap in ipairs({ 75, 150, 225, 300 }) do
        if skill < cap and skill >= cap - 75 then
          local target = math.min(cap, skill + 25)
          local rel, stock, knownNow = {}, {}, {}
          for _, r in ipairs(rows) do
            if r[6] > skill and r[4] <= target then
              rel[#rel + 1] = r
              if r[5] <= skill then knownNow[r[1]] = true end
              if skill % 3 == 0 then for k = 1, #r[10] - 1, 2 do stock[r[10][k]] = (skill * 7 + r[10][k]) % 40 end end
            end
          end
          for _, known in ipairs({ false, knownNow }) do
            for _, mode in ipairs({ "full", "exact" }) do
              local inp = { rows = rel, skill = skill, target = target, cap = cap, level = 60, mode = mode, stock = stock,
                known = known or nil, vendor = vendor, price = price }
              local res = E.Session(inp)
              local bad = E.Audit(res, inp)
              plans = plans + 1
              if #bad > 0 then badN = badN + 1 firstBad = firstBad or (line .. "@" .. skill .. " " .. mode .. ": " .. bad[1]) end
              for _, x in ipairs(res.steps) do
                if x.minC < E.MIN_CHANCE then deep = deep + 1 end
                if x.hi > x.lo then ranged = ranged + 1 end
                -- orange only = exact count, never a range
                if x.minC >= 1 and x.lo ~= x.hi then badN = badN + 1 firstBad = firstBad or "orange step with a range" end
              end
            end
          end
        end
      end
    end
  end
  check(plans > 5000 and badN == 0, ("%d plans over every profession and skill: %d incoherent (%s)"):format(plans, badN, tostring(firstBad)))
  check(ranged > 0, "yellow / green steps carry a range (" .. ranged .. ")")
  see("== engine:", plans, "plans,", deep, "deep-green steps (only when nothing else gives a point)")
  -- the audit does catch a silly plan (a hand-made one: 271 crafts for 2 points, a step below the skill, a recipe not learnable)
  local A = { 10, 0, 1, 1, 5, 10, 1, 0, "A", { 900, 1 }, 1 }
  local B = { 11, 0, 1, 80, 100, 140, 1, 0, "B", { 901, 4 }, 2 }
  local silly = { seq = { { kind = "craft", spell = 11, name = "B", from = 60, to = 62, crafts = 271, row = B, lo = 271, hi = 271 } },
    steps = {}, learn = { { spell = 11, name = "B", at = 60, req = 80, kind = "trainer" } }, use = {}, buy = { [901] = 1084 },
    unknown = { [901] = 1084 }, crafts = 271, reached = 62, cost = 0 }
  local sb = table.concat(E.Audit(silly, { skill = 92, target = 150, cap = 150, known = {} }), " | ")
  check(sb:find("sous ta compétence", 1, true) and sb:find("pas encore apprenable", 1, true) and sb:find("271 fabrications", 1, true),
    "audit refuses a silly plan: " .. sb)
  -- deep green: taken only when nothing else gives a point
  local C2 = { 12, 0, 1, 1, 8, 12, 1, 0, "C", { 902, 1 }, 1 }
  local r1 = E.Session({ rows = { A, C2 }, skill = 9, target = 12, cap = 75, vendor = { [900] = 1, [902] = 500 }, known = { [10] = true, [12] = true } })
  check(r1.steps[1].name == "C", "at 9 the 20 % recipe A is left for C (75 %), even if A is cheaper: " .. r1.steps[1].name)
  local r2 = E.Session({ rows = { A }, skill = 9, target = 10, cap = 75, vendor = { [900] = 1 }, known = { [10] = true } })
  check(r2.steps[1] and r2.steps[1].name == "A" and r2.steps[1].minC < E.MIN_CHANCE and r2.steps[1].hi > r2.steps[1].crafts,
    "nothing else: the deep-green recipe is planned, flagged, with a range")

  -- ------------------------------------------------------------------ 2. the character (nothing read yet)
  M.LeaveCombat()
  local oldFaction = rawget(_G, "UnitFactionGroup")
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 30
  M.mapRects[1454] = { top = 1900, left = -4100, w = 1400, h = 930, continent = 1 }
  M.mapNames[1454] = "Orgrimmar"
  M.player.zone, M.player.subzone = "Orgrimmar", "La Herse"
  M.pos = { mapID = 1454, x = 0.50, y = 0.50 }
  M.skills = { { name = "Travail du cuir", rank = 92, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 92, max = 150 }, { name = "Secourisme", rank = 42, max = 75 } }
  for id, v in pairs(D.mats) do if v[3] == 1 or id == 2319 or id == 2589 or id == 2592 or id == 4232 or id == 2318 then
    M.AddItem(id, { name = v[1], stack = 20, sellPrice = math.max(1, v[4] or 1), classID = 7 }) end end
  M.SetBagItem(0, 8, 2319, 20)
  M.SetBagItem(0, 9, 2319, 8)                 -- 28 Cuir moyen
  M.SetBagItem(0, 10, 2589, 12)               -- 12 Étoffe de lin
  P.char.known, P.char.cal, P.char.target = {}, {}, {}
  P.db.treq, P.db.trank, P.db.tseen, P.db.trn = {}, {}, {}, {}
  P.db.askAH = true
  P.Update(true)
  M.Advance(1)
  local function planOf(line, mode) return PL.Compute(line, mode or "full", mode == "exact" and 300 or PL.Target(line, mode or "full")) end
  local function texts(steps)
    local t = {}
    for _, st in ipairs(steps) do t[#t + 1] = (st.title or "") .. " — " .. (st.sub or "") end
    return table.concat(t, "\n")
  end
  -- an AH price for linen / peacebloom so that the plan WOULD buy at the AH
  local Au = T.modules.Auction
  local oldStats = Au and Au.Stats
  if Au then Au.Stats = function(id) return { market = (id == 2589 or id == 2447 or id == 2319 or id == 4232) and 400 or nil, conf = 0.9, src = "HV" } end end
  PL.ver.prices = PL.ver.prices + 1
  M.Advance(61)                               -- the price memo (60 s) forgets « no price »

  -- ------------------------------------------------------------------ 3. REAL PROBLEM 1: « achète à l'HV » while the trainer was better
  local fa = planOf(129)
  check(fa and not fa.bad, "First Aid plan computed and coherent")
  local st1 = PL.Steps(fa)
  see("== Secourisme 42/75, maître jamais lu :\n" .. texts(st1))
  check(st1[1] and st1[1].kind == "trainer" and st1[1].read and st1[1].title:find("Va d'abord chez ton maître", 1, true),
    "trainer never read: « Va d'abord chez ton maître » is the FIRST step: " .. tostring(st1[1] and st1[1].title))
  local hvBefore = false
  for _, st in ipairs(st1) do
    if st.kind == "ah" or st.kind == "ask" then
      hvBefore = true
      check(st.provisional and st.sub:find("à confirmer", 1, true), "the AH purchase is marked « à confirmer : le maître d'abord »")
    end
  end
  check(#PL.Unread(129) > 0, "unread trainer recipes within 25 points: " .. #PL.Unread(129))
  -- the trainer window, MAINLINE signature (name, type, texture, reqLevel) as on Forever
  local oldInfo = rawget(_G, "GetTrainerServiceInfo")
  local function mainline()
    rawset(_G, "GetTrainerServiceInfo", function(i)
      local sv = M.trainer.open and M.trainer.services[i]
      if sv then return sv.name, sv.cat or "available", 134400, sv.lvl or 0 end
    end)
  end
  local bought = {}
  rawset(_G, "BuyTrainerService", function(i)
    if not M.hardware then M.Violation("automation", "BuyTrainerService outside a click") return end
    local sv = M.trainer.open and M.trainer.services[i]
    if sv and sv.cat == "available" then
      sv.cat = "used"
      bought[#bought + 1] = sv.name
      M.money = (M.money or 0) - (sv.cost or 0)
      M.FireEvent("TRAINER_UPDATE")
    end
  end)
  mainline()
  M.pos = { mapID = 1454, x = 0.40, y = 0.60 }
  M.units.npc = { name = "Maître-Soins-Test" }
  M.trainer = { open = false, tradeskill = true, line = "Secourisme", rank = 42, services = {
    { name = "Bandage en lin", cat = "used", req = 1, cost = 0 },
    { name = "Bandage épais en lin", cat = "available", req = 40, cost = 250 },
    { name = "Anti-venin", cat = "unavailable", req = 80, cost = 250 },
    { name = "Bandage en laine", cat = "unavailable", req = 80, cost = 500 },
    { name = "Compagnon", cat = "unavailable", req = 50, cost = 500, lvl = 10 },
  } }
  M.money = 100000
  M.SetTrainer(true) M.Advance(0.6)
  local treq = P.db.treq[129] or {}
  local hl = treq[P.Fold("Bandage épais en lin")]
  check(hl and hl.r == 40 and hl.c == 250 and hl.a == 1, "Mainline signature: requirement 40, price, « apprenable » read at the trainer")
  check(treq[P.Fold("Bandage en laine")] and treq[P.Fold("Bandage en laine")].r == 80 and not treq[P.Fold("Bandage en laine")].a, "an unavailable recipe: its real requirement (80)")
  check(P.char.known[129] and P.char.known[129][3275] == 1, "« used » = known (Mainline: the type is the 2nd return)")
  local tk = P.db.trank[129]
  check(tk and tk.cap == 75 and tk.r == 50 and tk.lv == 10 and tk.name == "Compagnon", "next rank read: skill 50, level 10")
  local ranks, est = PL.Ranks(129)
  check(est == false and ranks[2][2] == 10, "rank level comes from the trainer, no longer an estimate")
  check(PL.TrainerSeen(129) and #PL.Unread(129) == 0, "trainer read: nothing left to confirm")
  -- the card under the trainer window: everything useful, with the price, one click = one recipe
  local card = PL._card and PL._card() or nil
  local o = PL.Offers(129)
  check(#o.list == 1 and o.list[1].name == "Bandage épais en lin" and o.cost == 250 and o.later[1] and o.later[1].rank,
    "offers: Bandage épais en lin (useful now), then the rank at 50")
  check(PL.TrainerCard() == true, "trainer card shown under the trainer window")
  local okNoClick = PL.LearnNext()
  check(okNoClick == false and #bought == 0, "outside a click nothing is bought (ShopCall refuses)")
  local tc = PL.GetCard()
  check(tc and tc:IsShown() and tc.label and tc.label:find("^Apprendre Bandage épais"), "the card button: « " .. tostring(tc and tc.label) .. " »")
  tc.btn:Click()                                      -- the real path: the card's button (a hardware click)
  M.Advance(0.6)
  check(#bought == 1 and bought[1] == "Bandage épais en lin", "ONE click = ONE recipe learned: " .. table.concat(bought, ","))
  tc = PL.GetCard()
  check(tc and tc:IsShown() and not (tc.label and tc.label:find("^Apprendre")), "after the game confirmed: no « Apprendre » button left (nothing useful)")
  check(P.char.known[129][3276] == 1 and #PL.Offers(129).list == 0, "the game confirms (used) → known, the card moves on")
  M.Hw(function() T:UserAction(function() PL.LearnNext() end) end)
  check(#bought == 1, "nothing else is bought: nothing useful left")
  M.SetTrainer(false) M.Advance(0.6)
  local fa2 = planOf(129)
  local st2 = PL.Steps(fa2)
  see("== Secourisme 42/75, maître lu, recette apprise :\n" .. texts(st2))
  local first
  for _, st in ipairs(st2) do if st.kind == "craft" then first = st break end end
  local heavy, untrue = false, nil
  for _, x in ipairs(fa2.res.steps) do
    if x.spell == 3276 then heavy = true end
    if not (P.char.known[129][x.spell] or (PL.LearnAt(129) or {})[x.spell]) then untrue = x.name end
  end
  check(first and heavy, "after the visit the plan uses the recipe just learned (Bandage épais en lin): " .. tostring(first and first.title))
  check(not untrue, "after the visit every planned recipe is KNOWN or was READ at the trainer (not: " .. tostring(untrue) .. ")")
  check(not (st2[1].kind == "trainer" and st2[1].read), "no more « va d'abord chez ton maître »")
  for _, st in ipairs(st2) do check(not st.provisional, "after the visit no purchase is « à confirmer »: " .. st.title) end
  check(hvBefore, "before the visit the plan did want an AH purchase (the complaint reproduced)")
  -- ERA signature (name, subText, type, expanded) + level by GetTrainerServiceLevelReq: same result
  rawset(_G, "GetTrainerServiceInfo", oldInfo)
  rawset(_G, "GetTrainerServiceLevelReq", function(i) local sv = M.trainer.open and M.trainer.services[i] return sv and sv.lvl or 0 end)
  P.db.treq[185], P.db.trank[185] = nil, nil
  M.pos = { mapID = 1454, x = 0.45, y = 0.62 }
  M.units.npc = { name = "Maître-Cuisine-Test" }
  M.trainer = { open = false, tradeskill = true, line = "Cuisine", rank = 92, services = {
    { name = "Steak de coyote", cat = "available", req = 50, cost = 100 },
    { name = "Beignet de crabe", cat = "unavailable", req = 100, cost = 400 },
    { name = "Expert", cat = "unavailable", req = 125, cost = 5000, lvl = 20 },
  } }
  M.SetTrainer(true) M.Advance(0.6)
  local cq = P.db.treq[185] or {}
  check(cq[P.Fold("Steak de coyote")] and cq[P.Fold("Steak de coyote")].r == 50 and cq[P.Fold("Beignet de crabe")].r == 100,
    "Era signature: same requirements read")
  check(P.db.trank[185] and P.db.trank[185].lv == 20 and P.db.trank[185].r == 125, "Era signature: next rank read (level 20, skill 125)")
  M.SetTrainer(false) M.Advance(0.6)
  rawset(_G, "GetTrainerServiceLevelReq", nil)
  -- a requirement the client does not say: « available » = at most your skill ; « unavailable » without a number = unknown, never 0
  mainline()
  rawset(_G, "GetTrainerServiceSkillReq", function() return nil end)
  M.trainer = { open = false, tradeskill = true, line = "Cuisine", rank = 92, services = {
    { name = "Côtes de porc séchées", cat = "available", cost = 300 }, { name = "Palourdes bouillies", cat = "unavailable", cost = 300 } } }
  local oldLine = rawget(_G, "GetTrainerServiceSkillLine")
  rawset(_G, "GetTrainerServiceSkillLine", function() return nil end)
  M.SetTrainer(true) M.Advance(0.6)
  cq = P.db.treq[185] or {}
  local dp = cq[P.Fold("Côtes de porc séchées")]
  check(PL.trainerLine == 185, "line found from the recipe names when the client says nothing")
  check(dp and dp.r == 92 and dp.le == 1, "no number but « available »: learnable now (at most 92), flagged")
  local rg = cq[P.Fold("Palourdes bouillies")]
  check(rg and rg.r == nil and not (PL.LearnAt(185) or {})[0], "no number and unavailable: unknown, never « 0 »")
  M.SetTrainer(false) M.Advance(0.6)
  rawset(_G, "GetTrainerServiceSkillLine", oldLine)
  rawset(_G, "GetTrainerServiceInfo", oldInfo)
  local oldSkillReq = nil
  -- restore the mock's own trainer functions for the rest (they were replaced above)
  rawset(_G, "GetTrainerServiceSkillReq", function(i)
    local sv = M.trainer.open and M.trainer.services[i]
    if sv then return M.trainer.line, sv.req or 0, (sv.req or 0) <= (M.trainer.rank or 999) end
  end)

  -- ------------------------------------------------------------------ 4. REAL PROBLEM 3: « Apprendre X à 67 » while the skill is 92
  -- every text of every plan of his three professions, at every skill: never a requirement at or below the skill
  local function sweepTexts(line, name, max0)
    local bad, n = nil, 0
    for sk = 1, max0 - 1, 3 do
      for _, s in ipairs(M.skills) do if s.name == name then s.rank = sk s.max = (sk < 75 and 75) or (sk < 150 and 150) or (sk < 225 and 225) or 300 end end
      P.profs = P.ScanProfessions()
      local plan = planOf(line)
      if plan then
        n = n + 1
        if plan.bad then bad = bad or (sk .. ": plan.bad " .. plan.bad[1]) end
        local all = texts(PL.Steps(plan)) .. "\n" .. table.concat(PL.ComponentLines(true), "\n")
        for at in all:gmatch("dès (%d+)") do if tonumber(at) <= sk then bad = bad or (sk .. ": « dès " .. at .. " »") end end
        if all:find("Apprendre [^\n]- à %d+") then bad = bad or (sk .. ": « Apprendre X à N »") end
        if all:find("%d+/%d+ %a") then bad = bad or (sk .. ": « n/N objet »") end
        for _, l in ipairs(plan.res.learn) do if l.at < sk then bad = bad or (sk .. ": learn below skill") end end
      end
    end
    return bad, n
  end
  for _, c in ipairs({ { 165, "Travail du cuir", 300 }, { 185, "Cuisine", 300 }, { 129, "Secourisme", 300 } }) do
    local bad, n = sweepTexts(c[1], c[2], c[3])
    check(n > 50 and not bad, ("%s: %d plans shown, never « apprendre à N ≤ compétence », never « n/N » (%s)"):format(c[2], n, tostring(bad)))
  end
  M.skills = { { name = "Travail du cuir", rank = 92, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 92, max = 150 }, { name = "Secourisme", rank = 42, max = 75 } }
  P.profs = P.ScanProfessions()
  -- the exact case: Leatherworking 92, a recipe whose requirement (67, read at the trainer) is below the skill
  P.db.treq[165] = { [P.Fold("Gilet en cuir estampé")] = { r = 67, c = 300, a = 1 }, [P.Fold("Bottes en cuir gris")] = { r = 100, c = 500 },
    [P.Fold("Renfort d'armure moyen")] = { r = 90, c = 400, a = 1 }, [P.Fold("Peau moyenne traitée")] = { r = 90, c = 400, a = 1 },
    [P.Fold("Cape noire en cuir")] = { r = 110, c = 600 }, [P.Fold("Pantalon noir en cuir")] = { r = 115, c = 600 } }
  P.db.tseen[165] = { t = 1, n = 6, hi = 115 }
  P.db.trn["1454"] = P.db.trn["1454"] or {}
  P.db.trn["1454"]["Maître-Cuir-Test"] = { x = 0.42, y = 0.61, l = 165, t = 1 }
  P.char.known[165] = { [2165] = 1 }           -- his trade window was read: he knows « Renfort d'armure moyen »
  PL.Dirty("known")
  local lw = planOf(165)
  local lwSteps = PL.Steps(lw)
  local lwText = texts(lwSteps)
  see("== Travail du cuir 92/150, 28 Cuir moyen, maître lu :\n" .. lwText)
  check(lw and not lw.bad, "Leatherworking plan coherent")
  check(not lwText:find("67", 1, true), "skill 92: the number 67 is never shown")
  local nowLearn = false
  for _, st in ipairs(lwSteps) do if st.kind == "trainer" and not st.at and st.title:find("chez ton maître", 1, true) then nowLearn = true end end
  check(nowLearn, "a recipe learnable now reads « Apprends … chez ton maître » (no number)")

  -- ------------------------------------------------------------------ 5. REAL PROBLEM 4: « 28/271 Cuir moyen »
  local need = (lw.use[2319] or 0) + (lw.res.buy[2319] or 0)
  local byCrafts = 0
  for _, x in ipairs(lw.res.steps) do for k = 1, #x.row[10] - 1, 2 do if x.row[10][k] == 2319 then byCrafts = byCrafts + x.crafts * x.row[10][k + 1] end end end
  for _, x in ipairs(lw.prep or {}) do for k = 1, #x.row[10] - 1, 2 do if x.row[10][k] == 2319 then byCrafts = byCrafts + x.crafts * x.row[10][k + 1] end end end
  check(need == byCrafts, ("Cuir moyen: %d planned = %d consumed by the crafts shown, not one more"):format(need, byCrafts))
  check((lw.use[2319] or 0) <= 28, "never more Cuir moyen « used » than the 28 he owns: " .. tostring(lw.use[2319]))
  for _, x in ipairs(lw.res.steps) do
    check(x.crafts <= (x.to - x.from + 1) * math.ceil(1 / E.MIN_CHANCE) and x.lo <= x.crafts and x.crafts <= x.hi,
      ("step « %s » %d → %d: %d crafts (%d–%d), plausible for its range"):format(x.name, x.from, x.to, x.crafts, x.lo, x.hi))
  end
  check(not lwText:find("%d+/%d+"), "no « have/need » fraction anywhere in the steps")
  -- surplus / keep: the plan's leather is kept (exact count + margin), what no plan uses is declared
  local keepN, keepWhy = PL.KeepFor(2319)
  check(keepN == 28 and keepWhy:find("en cours", 1, true), "keeper before the first computation: all 28 Cuir moyen are kept (when in doubt, never sold)")
  M.Advance(2)
  PL.KeepRebuild() M.Advance(3)
  keepN, keepWhy = PL.KeepFor(2319)
  check(keepN >= 28 and keepWhy:find("ton plan de travail du cuir en utilise", 1, true), ("keeper: Cuir moyen kept, exact count + margin (%s — %s)"):format(tostring(keepN), tostring(keepWhy)))
  check(PL.KeepFor(6948) == 0, "keeper: an item no plan uses → 0")
  -- contract BAGS: registered when T.Bags exists (guarded), never an error without it
  local hadBags, reg = T.Bags, nil
  PL.keeperOn = nil
  T.Bags = nil
  check(PL.RegisterKeeper() == false, "no T.Bags: nothing registered, no error")
  T.Bags = { RegisterKeeper = function(name, fn) reg = { name = name, fn = fn } end }
  check(PL.RegisterKeeper() == true and reg and reg.name == "Prof" and select(1, reg.fn(2319)) == keepN, "keeper « Prof » registered with T.Bags.RegisterKeeper")
  -- contract BAGS: « Courses en ville » takes the consumables to buy from T.Bags.Shopping() (defensive read)
  local outE = {}
  T.Bags = nil
  check(CR.BagsShopping(outE) == false and #outE == 0, "no T.Bags.Shopping: the module's own consumable errands stay")
  T.Bags = { Shopping = function() return { { itemID = 2581, need = 12, why = "stock cible : 20 bandages" }, { id = "x", n = 3 }, "junk",
    { itemID = 1251, need = -4 }, { itemID = 2581, need = 5 } } end }
  check(CR.BagsShopping(outE) == true and #outE == 1 and outE[1].items[1].id == 2581 and outE[1].items[1].n == 12
    and outE[1].why == "stock cible : 20 bandages", "T.Bags.Shopping: one errand per valid entry (bad / duplicate entries ignored)")
  T.Bags = { Shopping = function() error("boom") end, Verdict = function() return { action = "keep" } end }
  check(CR.BagsShopping({}) == false, "a failing T.Bags.Shopping never breaks the list")
  check(#CR.ExtraList() == 0, "« en trop » goes through T.Bags.Verdict: « keep » there = not surplus here")
  T.Bags = hadBags
  PL.keeperOn = nil

  -- ------------------------------------------------------------------ 6. REAL PROBLEM 2: jump between the steps
  check(PL.StartFocus(165, "full") == true and T.focus and T.focus.kind == "prof", "« Me concentrer » : the profession session starts")
  M.Advance(0.6)
  local s0 = PL.steps
  check(#s0 >= 3, "session steps: " .. #s0)
  local rows = T.focus.card(8)
  local target = s0[3]
  local key3 = CR.StepKey(target)
  rows[3].onClick()                                   -- click on step 3 = go to that step
  M.Advance(0.6)
  check(PL.steps[1].key == key3, "click on step 3: it is the current step now (" .. tostring(PL.steps[1].title) .. ")")
  M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1)
  M.FireEvent("QUEST_LOG_UPDATE") M.Advance(1)
  check(PL.steps[1].key == key3, "his choice sticks after the bags / the quest log changed")
  local skipKey = PL.steps[2].key
  T.focus.card(8)[2].onRightClick()                   -- right-click = skip
  M.Advance(0.6)
  local last = PL.steps[#PL.steps]
  check(last.key == skipKey and last.skipped and PL.steps[1].key == key3, "right-click: the step is skipped (last, « passée »), the chosen one stays first")
  M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1)
  check(PL.steps[#PL.steps].key == skipKey, "the skip sticks too")
  check(T.FocusStep(1) == true, "« étape suivante » (key binding) works")
  M.Advance(0.6)
  check(PL.steps[1].key ~= key3, "next step: another step is current")
  check(T.FocusStep(-1) == true, "« étape précédente » works")
  M.Advance(0.6)
  check(PL.steps[1].key == key3, "previous: back to the chosen step")
  PL.StopFocus("silent")
  CR.char.pick = {}
  M.Advance(0.6)

  -- ------------------------------------------------------------------ 6b. guided session: craft card, the game confirms, auto-advance
  M.AddItem(1251, { name = "Bandage en lin", stack = 20, sellPrice = 10, classID = 0 })
  M.AddItem(2581, { name = "Bandage épais en lin", stack = 20, sellPrice = 20, classID = 0 })
  M.trade = { open = false, line = 129, name = "Secourisme", rank = 42, max = 75, recipes = {
    [3275] = { name = "Bandage en lin", learned = true, diff = 1, maxTrivial = 60, mats = { 2589, 1 }, item = 1251 },
    [3276] = { name = "Bandage épais en lin", learned = true, diff = 0, maxTrivial = 100, mats = { 2589, 2 }, item = 2581 } } }
  check(PL.StartFocus(129, "exact") == true, "session « pile-poil » on First Aid (only his 12 linen)")
  M.Advance(0.6)
  local anyCraft = false
  for _, st in ipairs(PL.steps) do
    if st.kind == "craft" then anyCraft = true check(st.step.from >= 42, "no craft step below his skill: " .. st.title) end
    check(st.kind ~= "ah" and st.kind ~= "ask", "pile-poil: nothing bought at the AH (" .. st.title .. ")")
  end
  check(anyCraft, "the session has craft steps")
  M.crafted = {}
  M.SetTradeSkill(true) M.Advance(0.8)
  local cardF = PL.GetCard()
  check(cardF and cardF:IsShown() and cardF.label and cardF.label:find("^Créer %d+"), "trade window: one button « Créer N »: " .. tostring(cardF and cardF.label))
  if cardF and cardF.label and cardF.label:find("^Créer") then
    local nWant = tonumber(cardF.label:match("%d+"))
    local linen0 = PL.BagCount(2589)
    cardF.btn:Click()
    check(#M.crafted == 1 and M.crafted[1].n == nWant, "ONE click = ONE craft command of exactly N: " .. tostring(M.crafted[1] and M.crafted[1].n))
    check(PL.BagCount(2589) < linen0, "the game confirms: linen left the bags")
    for _, s in ipairs(M.skills) do if s.name == "Secourisme" then s.rank = 47 end end
    M.trade.rank = 47
    M.FireEvent("SKILL_LINES_CHANGED") M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1.2)
    local p2 = PL.FocusPlan()
    check(p2 and p2.skill == 47 and not p2.bad, "auto-advance: the plan restarts from the new skill 47")
    for _, st in ipairs(PL.steps) do if st.kind == "craft" then check(st.step.from >= 47, "after the skill-up no step below 47: " .. st.title) end end
  end
  M.SetTradeSkill(false) M.Advance(0.5)
  PL.StopFocus("silent")
  -- 6c. real reagents read in the trade-skill window (C_TradeSkillUI.GetRecipeSchematic) win over the table
  local CT = rawget(_G, "C_TradeSkillUI")
  if type(CT) == "table" and CT.GetRecipeInfo then
    local oldSch = CT.GetRecipeSchematic
    local reads = 0
    CT.GetRecipeSchematic = function(id)
      reads = reads + 1
      if id == 3276 then return { reagentSlotSchematics = { { reagentType = 1, required = true, quantityRequired = 3, reagents = { { itemID = 2589 } } } } } end
      if id == 3275 then return { reagentSlotSchematics = { { reagentType = 1, required = true, quantityRequired = 1, reagents = { { itemID = 2589 } } } } } end
      return { reagentSlotSchematics = { { reagentType = 1, required = true, reagents = {} } } }
    end
    M.SetTradeSkill(true) M.Advance(0.8)
    local fx = P.char.fix and P.char.fix[129]
    check(fx and fx[3276] and fx[3276][1] == 2589 and fx[3276][2] == 3, "live reagents: the game says 3 linen for « Bandage épais en lin », the table said 2 → kept")
    check(fx and fx[3275] == nil, "live reagents equal to the table: nothing stored")
    local live
    for _, r in ipairs(PL.Rows(129)) do if r[1] == 3276 then live = r end end
    check(live and live[10][2] == 3 and live.live, "the plan rows use the reagents read in the game")
    local r0 = reads
    M.FireEvent("TRADE_SKILL_LIST_UPDATE") M.Advance(0.6)
    check(reads == r0, "each recipe is read once per session (no re-read on every list update)")
    M.SetTradeSkill(false) M.Advance(0.5)
    CT.GetRecipeSchematic = oldSch
    P.char.fix[129] = nil
    P.fixGen = (P.fixGen or 0) + 1
  end
  for _, s in ipairs(M.skills) do if s.name == "Secourisme" then s.rank = 42 end end
  P.profs = P.ScanProfessions()
  M.Advance(0.6)

  -- ------------------------------------------------------------------ 7. passing in front of the trainer: one alert, once
  local alerts = {}
  local oldAlert = T.ShowAlert
  T.ShowAlert = function(_, e) if tostring(e.id):find("^Prof:pass") then alerts[#alerts + 1] = e.text end end
  M.pos = { mapID = 1454, x = 0.80, y = 0.20 }
  M.FireEvent("ZONE_CHANGED") M.Advance(6)
  check(#alerts == 0, "far from the trainer: nothing")
  M.pos = { mapID = 1454, x = 0.421, y = 0.611 }
  M.Advance(4)
  check(#alerts == 1 and alerts[1]:find("Tu passes devant tes maîtres", 1, true) and alerts[1]:find("travail du cuir (", 1, true)
    and alerts[1]:find("cuisine (", 1, true), "in front of the trainers: ONE alert « " .. tostring(alerts[1]) .. " » (" .. #alerts .. ")")
  see("== en passant : " .. tostring(alerts[1]))
  M.FireEvent("ZONE_CHANGED") M.Advance(6)
  M.FireEvent("ZONE_CHANGED_INDOORS") M.Advance(6)
  check(#alerts == 1, "once: no second alert for the same recipes")
  PL.hintCands = nil
  M.pos = { mapID = 1424, x = 0.5, y = 0.5 }
  M.FireEvent("ZONE_CHANGED_NEW_AREA") M.Advance(3)
  check(PL.hintCands == nil and not PL.hintTimer, "a map without a known trainer: nothing is computed, no timer")
  T.ShowAlert = oldAlert
  M.pos = { mapID = 1454, x = 0.50, y = 0.50 }

  -- ------------------------------------------------------------------ 8. the page: 3 actions, one card per profession, honest unknowns
  local W = T.Window
  if W and W._newLay and W.pageIndex and W.pageIndex.prof then
    local top = W.pageIndex.prof.top()
    local names = {}
    for _, b in ipairs(top) do names[#names + 1] = b[1] end
    local tl = table.concat(names, " · ")
    see("== page Métiers, en haut : " .. tl)
    check(#top == 3 and tl:find("Me concentrer", 1, true) and tl:find("Courses en ville", 1, true) and tl:find("Ce que je peux apprendre", 1, true),
      "page top: Me concentrer · Courses en ville · Ce que je peux apprendre (" .. tl .. ")")
    PL.showLearn = true
    local Ly = W._newLay(600)
    local okR, err = pcall(W.pageIndex.prof.render, Ly)
    check(okR, "page renders: " .. tostring(err))
    local labels = {}
    for _, r in ipairs(Ly.rects) do labels[#labels + 1] = r.label end
    local all = table.concat(labels, "\n")
    see("== page Métiers :\n" .. all)
    for _, nm in ipairs({ "Travail du cuir 92/150", "Cuisine 92/150", "Secourisme 42/75", "Dépeçage 140/150" }) do
      check(all:find(nm, 1, true), "one card per profession: " .. nm)
    end
    check(all:find("Inconnu : ", 1, true), "what TreeAI does not know is said (« Inconnu : … »)")
    check(all:find("Ce que je peux apprendre", 1, true) and all:find("Total : ", 1, true), "« Ce que je peux apprendre » list with its total")
    PL.showLearn = false
  end
  local cards = PL.Cards()
  check(#cards == 4, "4 cards")
  for _, c in ipairs(cards) do
    see("== carte : " .. c.title .. " | " .. PL.CardSub(c) .. " | " .. tostring(c.next) .. " | inconnu : " .. table.concat(c.unknown, " ; "))
    check(c.goal and c.goal ~= "", "card « " .. c.title .. " » has a goal")
    if c.plan then check(c.next and c.cost and c.cost <= math.max(M.money or 0, 0) + 1, ("card « %s »: next step, cost %s within his gold %s"):format(c.title, tostring(c.cost), tostring(M.money))) end
  end

  -- ------------------------------------------------------------------ 9. nothing in chat, mocks restored
  check(chat() == chat0, "nothing in chat (" .. (chat() - chat0) .. ")")
  if Au then Au.Stats = oldStats end
  rawset(_G, "UnitFactionGroup", oldFaction)
  rawset(_G, "BuyTrainerService", nil)
end
