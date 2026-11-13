-- Scenario for Modules/ProfLevel.lua (agent PROFLEVEL, 4.0.2): levelling planner (Prof.Engine.Session / Split),
-- bank / mail caches, quest reserve, « Session métier » focus (route paused / restored, T.focus contract, card rows),
-- action card flows ONE game action per click (bank withdraw, merchant purchase, craft), combat refusal, trainer and
-- vendor memory, the « Métiers » page. Fake names only. The user's case: level-28 hunter, Leatherworking 143/150.
return function(M, ctx)
  local T = TreeAI
  local P = T.modules.Prof
  local check = ctx.check
  check(P and P.Engine and P.Level, "Prof + ProfLevel loaded")
  if not (P and P.Engine and P.Level) then return end
  local E, PL = P.Engine, P.Level
  local function near(a, b) return math.abs(a - b) < 1e-9 end
  local function clearViolations() M.violations = {} end
  local function violations() return #M.violations end

  -- ------------------------------------------------------------------ 1. engine (pure)
  -- rows: {spell, item, count, learn, yellow, grey, ups, recipeItem, name, mats, flags}
  local rows = {
    { 10, 0, 1, 1, 5, 10, 1, 0, "A", { 900, 1 }, 1 },          -- learned with the profession
    { 11, 0, 1, 8, 20, 40, 1, 0, "B", { 901, 2 }, 0 },         -- trainer recipe, learnable from 8 (orange until 20)
    { 12, 0, 1, 15, 30, 40, 1, 777, "C", { 902, 1 }, 0 },      -- pattern (recipe item 777)
  }
  check(E.Usable(rows[1], 1, nil) and E.Usable(rows[1], 1, {}), "auto-learned recipe usable even before the list is read")
  local ok, why = E.Usable(rows[2], 25, {})
  check(ok and why == "trainer", "trainer recipe not known: usable once learned (" .. tostring(why) .. ")")
  check(not E.Usable(rows[2], 7, {}), "trainer recipe: not before its requirement")
  check(not E.Usable(rows[3], 30, {}) and E.Usable(rows[3], 30, { [12] = true }), "pattern: only when known")
  check(E.Usable(rows[2], 7, {}, { [11] = 5 }), "requirement read at the trainer wins over the estimate")
  check(E.Chance2(rows[1], 3) == 1 and near(E.Chance2(rows[1], 7), 0.6) and E.Chance2(rows[1], 10) == 0, "chance orange / yellow / grey")
  check(near(E.Chance2(rows[1], 7, 5), 0.8), "Working Overtime +4 %/rank")
  local res = E.Session({ rows = rows, skill = 1, target = 12, cap = 300, stock = { [900] = 3 }, vendor = { [901] = 50 },
    price = { [900] = 10 }, known = { [10] = true } })
  check(res.reached == 12 and res.stop == "target", "full mode reaches the target: " .. tostring(res.reached) .. " " .. tostring(res.stop))
  check(res.use[900] == 3 and res.buy[900] and res.buy[901] == 6, "stock used first, the rest bought (5.0: A under 25 % at 9 → B one point earlier)")
  check(#E.Audit(res, { skill = 1, target = 12, cap = 300, stock = { [900] = 3 }, vendor = { [901] = 50 }, price = { [900] = 10 }, known = { [10] = true } }) == 0,
    "the plan passes its own audit")
  check(res.learn[1] and res.learn[1].spell == 11 and res.learn[1].kind == "trainer", "trainer recipe to learn listed")
  check(res.steps[1].name == "A" and res.steps[#res.steps].name == "B", "cheapest first, then the next recipe")
  local sumC = 0
  for _, x in ipairs(res.steps) do sumC = sumC + x.crafts end
  check(sumC == res.crafts, "whole crafts")
  -- rank cap: Compagnon (150) → Expert needs level 20
  local capped = E.Session({ rows = { { 20, 0, 1, 1, 160, 200, 1, 0, "D", { 903, 1 }, 1 } }, skill = 148, target = 155,
    cap = 150, level = 15, vendor = { [903] = 5 } })
  check(capped.reached == 150 and capped.stop == "level" and capped.nextRank and capped.nextRank[1] == "Expert",
    "rank cap: stops at 150 until level 20 (" .. tostring(capped.stop) .. ")")
  local trained = E.Session({ rows = { { 20, 0, 1, 1, 160, 200, 1, 0, "D", { 903, 1 }, 1 } }, skill = 148, target = 155,
    cap = 150, level = 28, vendor = { [903] = 5 } })
  check(trained.reached == 155 and trained.train[1] and trained.train[1].rank == "Expert" and trained.train[1].at == 150,
    "rank cap: train Expert at 150 then go on")
  -- exact mode: only owned + vendor reagents
  local ex = E.Session({ rows = rows, skill = 1, target = 300, cap = 300, mode = "exact", stock = { [900] = 4 },
    vendor = { [901] = 50 } })
  check(ex.stop == "mats" and ex.use[900] == 4 and not ex.buy[900], "pile-poil: owned leather used up, nothing bought at the AH")
  -- split: bags, then bank, then mail, quest reserve first from the bags, vendor margin + lot
  local sp = E.Split({ use = { [900] = 10 }, buy = { [901] = 7 }, unknown = {}, var = { [901] = true } },
    { bags = { [900] = 4 }, bank = { [900] = 5 }, mail = { [900] = 5 } }, { [900] = 2 }, { [901] = { 4, 5 } }, {})
  check(sp.bank[900] == 5 and sp.mail[900] == 3, "split: 2 usable in bags (reserve 2), 5 from the bank, 3 from the mail")
  check(sp.vendor[1] and sp.vendor[1].buy == 10 and sp.vendor[1].margin == 3, "vendor: 7 + 1 margin rounded to the lot of 5 = 10")

  -- ------------------------------------------------------------------ 2. the character
  M.LeaveCombat()
  M.player.level = 28
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 92, max = 150 }, { name = "Secourisme", rank = 42, max = 75 } }
  for id, t in pairs({ [2319] = "Cuir moyen", [2318] = "Cuir léger", [4234] = "Cuir lourd", [2321] = "Bobine de fil raffiné",
    [2320] = "Bobine de fil grossier", [4289] = "Sel", [4340] = "Teinture grise", [4265] = "Renfort d'armure lourd",
    [5961] = "Pantalon noir en cuir", [4235] = "Peau lourde", [4236] = "Peau lourde traitée", [2589] = "Étoffe de lin" }) do
    M.AddItem(id, { name = t, stack = 20, sellPrice = 50, classID = 7 })
  end
  -- AH prices (Auctionator API, read only): heavy leather 80 c, medium 35 c
  local AH_PRICES = { [4234] = 80, [2319] = 35 }
  rawset(_G, "Auctionator", { API = { v1 = { GetAuctionPriceByItemID = function(caller, id)
    assert(caller == "TreeAI") return AH_PRICES[id] end } } })
  M.SetBagItem(0, 8, 2319, 6)       -- 6 Cuir moyen in the bags
  M.SetBagItem(0, 9, 4234, 4)       -- 4 Cuir lourd
  local BANK = M.BankBagIDs()[1]
  M.SetBankItem(BANK, 1, 4234, 20)  -- 20 + 6 Cuir lourd in the bank
  M.SetBankItem(BANK, 2, 4234, 6)
  M.SetBankItem(BANK, 3, 2319, 12)
  P.Update(true)
  check(#P.profs == 4, "4 professions read")
  -- trade window: learned recipes + the game's colours (Forever: GetFilteredRecipeIDs + GetRecipeInfo.learned)
  M.trade = { open = false, line = 165, name = "Travail du cuir", rank = 143, max = 150, recipes = {
    [3780] = { name = "Renfort d'armure lourd", learned = true, diff = 1, maxTrivial = 165, mats = { 4234, 5, 2321, 1 }, item = 4265 },
    [7135] = { name = "Pantalon noir en cuir", learned = true, diff = 3, maxTrivial = 140, mats = { 2319, 12, 4340, 1, 2321, 1 }, item = 5961 },
    [3818] = { name = "Peau lourde traitée", learned = true, diff = 2, maxTrivial = 145, mats = { 4235, 1, 4289, 3 }, item = 4236 },
  } }
  M.SetTradeSkill(true)
  M.Advance(1)
  local known = P.char.known[165]
  check(known and known[3780] == 1 and known[7135] == 1, "known recipes read in the profession window")
  local cal = P.char.cal[165] and P.char.cal[165][3780]
  check(cal and cal.d == 1 and cal.s == 143 and (cal.g == 165 or (M.flavor == "legacy" and cal.g == nil)),
    "game colours kept (colour at 143, grey threshold where the client gives it)")
  M.SetTradeSkill(false)

  -- ------------------------------------------------------------------ 3. bank: read & cached when opened
  check(not (P.char.pl and P.char.pl.bank), "no bank reading before the first visit")
  M.SetBank(true)
  M.Advance(0.5)
  local bank = P.char.pl.bank
  check(bank and bank.items[4234] == 26 and bank.items[2319] == 12, "bank read at the visit: " .. tostring(bank and bank.items[4234]))
  M.SetBank(false)
  M.Advance(0.5)
  check(PL.Freshness():find("banque vue à l'instant", 1, true), "« banque vue » shown: " .. PL.Freshness())
  local have = PL.Have({ [4234] = true })
  check(have.bags[4234] == 4 and have.bank[4234] == 26, "bank counted from the cache once closed")

  -- ------------------------------------------------------------------ 4. the plan (complet → 165)
  P.char.target[165] = 165
  local plan = PL.Compute(165, "full", 165)
  check(plan and plan.res.reached == 165, "plan reaches 165: " .. tostring(plan and plan.res.reached))
  if not plan then return end
  local usesKit = false
  for _, x in ipairs(plan.res.steps) do if x.spell == 3780 then usesKit = true end end
  check(usesKit, "Heavy Armor Kit (known, yellow) in the plan")
  check((plan.split.bank[4234] or 0) > 0, "Cuir lourd taken from the bank: " .. tostring(plan.split.bank[4234]))
  local thread
  for _, v in ipairs(plan.split.vendor) do if v.id == 2321 then thread = v end end
  check(thread and thread.buy >= thread.n and thread.unit == 100, "Fine Thread bought at the vendor at its exact price")
  local steps = PL.Steps(plan)
  local kinds = {}
  for _, st in ipairs(steps) do kinds[st.kind] = (kinds[st.kind] or 0) + 1 end
  check(kinds.bank == 1 and kinds.vendor == 1 and (kinds.craft or 0) >= 1, "session: bank, vendor, crafts")
  -- (5.0 PROF) the trainer of this profession was never read: « va d'abord chez ton maître » comes before the AH question
  local askAt
  for i, st in ipairs(steps) do if st.kind == "ask" then askAt = i end end
  check(kinds.ask == 1 and askAt and askAt <= 2 and (askAt == 1 or (steps[1].kind == "trainer" and steps[1].read)),
    "« me demander avant l'HV » : the AH purchase is a question first (after « va d'abord chez ton maître » if he was never read)")
  local ahRow
  for _, a in ipairs(plan.split.ah) do if a.id == 4234 then ahRow = a end end
  check(ahRow and ahRow.unit == 80 and ahRow.cost == ahRow.n * 80, "AH purchase priced from the AH data")
  PL.SetAH(165, true)
  local steps2 = PL.Steps(PL.Compute(165, "full", 165))
  local hasAH = false
  for _, st in ipairs(steps2) do if st.kind == "ah" then hasAH = true end check(st.kind ~= "ask", "accepted: no question left") end
  check(hasAH, "accepted: « HV : achète … » step")
  for _, st in ipairs(steps) do check(type(st.title) == "string" and st.title ~= "" and not st.title:find("nil", 1, true), "step text: " .. tostring(st.title)) end
  -- pile-poil: only what you own (+ vendor)
  local exact = PL.Compute(165, "exact", 300)
  check(exact and #exact.split.ah == 0 and #exact.split.farm == 0, "pile-poil: nothing to buy at the AH, nothing to farm")
  -- quest reserve: « garde X pour la quête »
  M.quests = { { title = "Le cuir du chef", level = 27, complete = false,
    objectives = { { text = "Cuir lourd : 0/10", done = false, have = 0, need = 10, type = "item" } } } }
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(2)
  local rsv = PL.Reserve()
  check(rsv[4234] == 10, "quest reserve: 10 Cuir lourd kept for the quest (" .. tostring(rsv[4234]) .. ")")
  local planQ = PL.Compute(165, "full", 165)
  check(planQ and planQ.reserve[4234] == 10 and planQ.why[4234] == "Le cuir du chef", "plan keeps the quest's leather")
  M.quests = {}
  M.FireEvent("QUEST_LOG_UPDATE")
  M.Advance(2)

  -- ------------------------------------------------------------------ 5. focus: route paused, T.focus contract, card
  local R = T.Route
  check(R and R.char and R.char.paused == false, "route running before the session")
  local fired = {}
  T:On("FOCUS_CHANGED", fired, function(_, _, kind) fired[#fired + 1] = kind or "nil" end)
  check(PL.StartFocus(165, "full"), "session started")
  M.Advance(1)
  check(R.char.paused == true, "quest route paused by the session")
  check(T.focus and T.focus.kind == "prof" and T:FocusMode() == "prof" and T.focus.pausedRoute == true, "T.focus contract")
  check(fired[1] == "prof", "FOCUS_CHANGED fired")
  local W = T.Window
  if W and W.CardModel then
    local crow, cmode, ctitle = W.CardModel(3)
    check(cmode == "focus" and tostring(ctitle):find("Travail du cuir 143", 1, true) and #crow >= 1,
      "card shows the session: " .. tostring(cmode) .. " / " .. tostring(ctitle))
  end
  if os.getenv("PROFLEVEL_DEBUG") then
    for i, st in ipairs(PL.steps or {}) do io.stderr:write("STEP ", i, " ", st.kind, " | ", st.title, " | ", tostring(st.sub), "\n") end
  end
  local tabTxt = {}
  P.Update(true)
  for _, l in ipairs(T:GetTabLines("prof")) do tabTxt[#tabTxt + 1] = T.PlainText(l.text) end
  check(table.concat(tabTxt, "\n"):find("Session métier : ", 1, true), "tab line « Session métier : … » pinned")

  -- ------------------------------------------------------------------ 6. bank flow: one stack per click
  clearViolations()
  M.SetBank(true)
  M.Advance(0.5)
  local card = PL.GetCard()
  check(card and card:IsShown() and card.label and card.label:find("^Retirer"), "bank: action card « Retirer … » under the bank")
  local before = #(M.withdrawn or {})
  local n = 0
  while card and card:IsShown() and card.label and card.label:find("^Retirer") and n < 6 do
    card.btn:Click()
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(0.5)
    n = n + 1
  end
  check(#(M.withdrawn or {}) > before and violations() == 0, "bank withdraw: " .. (#(M.withdrawn or {}) - before) .. " stack(s), no violation")
  M.Advance(1, 0.1)   -- (5.0 STABLE: bag changes are delivered ≥ 0.3 s after the last event, never in its frame)
  check((plan.split.bank[4234] or 0) == 0 or PL.Compute(165, "full", 165).split.bank[4234] == nil, "nothing left to withdraw")
  M.SetBank(false)
  M.Advance(0.5)

  -- ------------------------------------------------------------------ 7. merchant flow: exact quantities, one purchase per click
  M.units.npc = { name = "Fournisseur-Test" }
  M.merchantItems = { { id = 2321, price = 100, stack = 1, maxStack = 20 }, { id = 4340, price = 350, stack = 1, maxStack = 20 },
    { id = 4289, price = 50, stack = 1, maxStack = 20 } }
  clearViolations()
  M.bought = {}
  M.SetMerchant(true)
  M.Advance(0.5)
  card = PL.GetCard()
  check(card and card:IsShown() and card.label and card.label:find("^Acheter tout %(%d+%)"), "merchant: « Acheter tout (n) »: " .. tostring(card and card.label))
  local vend = P.db.vend and P.db.vend[tostring(M.pos.mapID)]
  check(vend and vend["Fournisseur-Test"], "reagent vendor remembered for the compass")
  -- a click in combat is refused (no purchase, no violation)
  M.EnterCombat()
  check(not PL.BuyNext(), "purchase refused in combat")
  M.LeaveCombat()
  M.Advance(0.6)
  n = 0
  card = PL.GetCard()
  while card and card:IsShown() and card.label and card.label:find("^Acheter") and n < 8 do
    card.btn:Click()
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(0.5)
    n = n + 1
  end
  check(#M.bought >= 1 and #M.bought == n and violations() == 0, ("merchant: %d click(s) = %d purchase(s), violations %d"):format(n, #M.bought, violations()))
  check(T.API.ItemCount(2321) >= (thread and thread.n or 1), "fine thread in the bags: " .. tostring(T.API.ItemCount(2321)))
  M.SetMerchant(false)
  M.Advance(0.5)

  -- ------------------------------------------------------------------ 8. craft flow: one command per click
  clearViolations()
  M.crafted = {}
  M.trade.open = false
  M.SetTradeSkill(true)
  M.Advance(0.8)
  card = PL.GetCard()
  check(card and card:IsShown() and card.label and card.label:find("^Créer %d+"), "trade window: « Créer N »: " .. tostring(card and card.label))
  if card and card.label and card.label:find("^Créer") then
    card.btn:Click()
    check(#M.crafted == 1 and M.crafted[1].n >= 1 and violations() == 0,
      "one craft command (CraftRecipe / DoTradeSkill) for N crafts: " .. tostring(M.crafted[1] and M.crafted[1].n))
  end
  -- skill up → the plan moves on (auto-advance)
  M.skills[1].rank = 150
  M.trade.rank = 150
  M.FireEvent("SKILL_LINES_CHANGED")
  M.Advance(1)
  local p2 = PL.FocusPlan()
  check(p2 and p2.skill == 150, "auto-advance: plan follows the new skill (" .. tostring(p2 and p2.skill) .. ")")
  M.SetTradeSkill(false)
  M.Advance(0.5)
  -- combat: card hidden, step clicks refused
  M.EnterCombat()
  M.Advance(0.2)
  check(not (PL.GetCard() and PL.GetCard():IsShown()), "no action card in combat")
  check(not PL.RunStep((PL.steps or {})[1] or { kind = "craft" }), "step click refused in combat")
  M.LeaveCombat()
  M.Advance(1)

  -- ------------------------------------------------------------------ 9. trainer + mail memory
  M.units.npc = { name = "Maître-Test" }
  M.trainer = { open = false, tradeskill = true, line = "Travail du cuir", rank = 150, services = {
    { name = "Renfort d'armure lourd", cat = "used", req = 145, cost = 1000 },
    { name = "Gants en cuir raffermi", cat = "available", req = 130, cost = 1500 } } }
  M.SetTrainer(true)
  M.Advance(0.5)
  local treq = P.db.treq and P.db.treq[165]
  check(treq and treq[P.Fold("Gants en cuir raffermi")] and treq[P.Fold("Gants en cuir raffermi")].r == 130, "trainer requirement read")
  check(P.db.trn and P.db.trn[tostring(M.pos.mapID)] and P.db.trn[tostring(M.pos.mapID)]["Maître-Test"], "trainer remembered")
  M.SetTrainer(false)
  M.AddMail("Colis", 0, nil, { id = 2319, count = 7 })
  M.FireEvent("MAIL_SHOW")
  M.FireEvent("MAIL_INBOX_UPDATE")
  M.Advance(0.2)
  check(P.char.pl.mail and P.char.pl.mail.items[2319] == 7, "mail attachments read")
  M.FireEvent("MAIL_CLOSED")

  -- ------------------------------------------------------------------ 10. the page (window)
  if W and W.Open then
    W.Open("prof")
    M.Advance(1.2)
    check(W.CurrentPage() == "prof", "page « Métiers » opened")
    local labels = {}
    local Ly = W._newLay and W._newLay(600)
    if Ly and W.pageIndex.prof then
      local okR, err = pcall(W.pageIndex.prof.render, Ly)
      check(okR, "page renders: " .. tostring(err))
      for _, r in ipairs(Ly.rects) do labels[#labels + 1] = r.label end
      local all = table.concat(labels, "\n")
      check(all:find("Travail du cuir", 1, true) and all:find("Étapes", 1, true), "page: hero + steps")
      check(all:find("Réglages du plan", 1, true), "page: the two modes live under « Réglages du plan » (folded)")
    end
    W.Close()
  end

  -- ------------------------------------------------------------------ 11. leave: everything restored
  check(PL.StopFocus(), "session stopped")
  M.Advance(1)
  check(R.char.paused == false, "route resumed after the session")
  check(T.focus == nil and T:FocusMode() == nil, "T.focus cleared")
  check(fired[#fired] == "nil", "FOCUS_CHANGED(nil) fired")
  local C = T.Compass
  local cc = C and C.char and C.char.custom
  check(not (type(cc) == "table" and cc.source == "prof"), "compass point of the session removed")
  -- a route the PLAYER had paused stays paused
  R.char.paused = true
  PL.StartFocus(165, "exact")
  PL.StopFocus()
  check(R.char.paused == true, "route paused by the player stays paused after the session")
  R.char.paused = false

  -- ------------------------------------------------------------------ 12. nothing runs when the page / focus are off
  local calls = 0
  local oldU = PL.Update
  PL.Update = function(...) calls = calls + 1 return oldU(...) end
  for _ = 1, 5 do M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(0.5) end
  PL.Update = oldU
  check(calls == 0, "page and session off: no ProfLevel update on bag changes (" .. calls .. ")")
  check(#M.violations == 0, "no violation")
  rawset(_G, "Auctionator", nil)
end
