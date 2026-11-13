-- Scenario for Modules/CityRun.lua (agent CITYRUN, 4.1): « Courses en ville » in Orgrimmar for a level-29 hunter with a
-- Leatherworking + Cooking plan, arrows for his bow, food for his pet's diet, a class trainer visit, bank mats, repair
-- and greys; NPC memory learned on visit (name + map + x, y), money cap, optimal route (exact on a known layout), the
-- focus contract, stops checked off by the game frames, ONE game action per click, nothing in chat; the profession
-- session steps (pick / skip / next / previous, kept across re-plans), honest options (First Aid: learn at the trainer
-- vs buy), the « en trop » verdict, the « Services » map pins with and without Questie. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local CR, P = T.CityRun, T.modules.Prof
  check(CR and CR.Plan and P and P.Level, "CityRun + Prof + ProfLevel loaded")
  if not (CR and P and P.Level) then return end
  local PL = P.Level
  local function chat()
    local n = 0
    for _, l in ipairs(M.output) do if not l:find("^%[UIError%]") and not l:find("^%[RaidNotice%]") then n = n + 1 end end
    return n
  end
  local function near(a, b, eps) return math.abs(a - b) < (eps or 1e-6) end

  -- ------------------------------------------------------------------ 1. pure: route order (exact) vs brute force
  local pts = { [0] = { 0, 0 }, { 5, 1 }, { 1, 4 }, { 6, 6 }, { 2, 9 }, { 9, 2 }, { 7, 9 }, [7] = { 10, 10 } }
  local function d(i, j) local a, b = pts[i], pts[j] return math.sqrt((a[1] - b[1]) ^ 2 + (a[2] - b[2]) ^ 2) end
  local order, cost = CR.Order(6, d, true)
  local best = math.huge
  local function perm(left, cur, acc)
    if #left == 0 then best = math.min(best, acc + d(cur, 7)) return end
    for i = 1, #left do
      local rest = {}
      for k = 1, #left do if k ~= i then rest[#rest + 1] = left[k] end end
      perm(rest, left[i], acc + d(cur, left[i]))
    end
  end
  perm({ 1, 2, 3, 4, 5, 6 }, 0, 0)
  check(#order == 6 and near(cost, best), ("exact order = brute force optimum (%.3f vs %.3f)"):format(cost or -1, best))
  local seen = {}
  for _, k in ipairs(order) do seen[k] = true end
  check(#order == 6 and seen[1] and seen[6], "order is a permutation of the stops")
  -- > 9 stops: nearest neighbour + 2-opt, a valid permutation no worse than nearest neighbour alone
  local big = { [0] = { 0, 0 } }
  for i = 1, 12 do big[i] = { (i * 37) % 23, (i * 17) % 19 } end
  local function d2(i, j) local a, b = big[i], big[j] return math.sqrt((a[1] - b[1]) ^ 2 + (a[2] - b[2]) ^ 2) end
  local o2, c2 = CR.Order(12, d2, false)
  local s2 = {}
  for _, k in ipairs(o2) do s2[k] = true end
  local nn, used, cur = 0, {}, 0
  for _ = 1, 12 do
    local bk, bv
    for k = 1, 12 do if not used[k] and (not bv or d2(cur, k) < bv) then bk, bv = k, d2(cur, k) end end
    used[bk], nn, cur = true, nn + bv, bk
  end
  local cnt = 0
  for _ in pairs(s2) do cnt = cnt + 1 end
  check(#o2 == 12 and cnt == 12 and c2 <= nn + 1e-9, ("12 stops: 2-opt %.2f ≤ nearest neighbour %.2f"):format(c2, nn))
  -- budget: lowest priority dropped first, sales count
  local keep, dropped, missing = CR.Budget({ { id = "a", prio = 85, cost = 500 }, { id = "b", prio = 40, cost = 700 },
    { id = "c", prio = 30, worst = 300 }, { id = "s", prio = 100, gain = 100, cost = 0 } }, 800)
  check(#dropped == 2 and dropped[1].id == "b" and dropped[2].id == "c" and missing == 600 and #keep == 2,
    "money: 1500 needed, 900 available → AH (30) then mats (40) dropped, ammo kept")
  check(CR.RoundLot(660, 200) == 800 and CR.RoundLot(16, 5) == 20, "quantities rounded to the vendor lot")

  -- ------------------------------------------------------------------ 2. the character in Orgrimmar (no Questie)
  M.LeaveCombat()
  local oldFaction = rawget(_G, "UnitFactionGroup")
  rawset(_G, "UnitFactionGroup", function() return "Horde", "Horde" end)
  M.player.level = 29
  M.mapRects[1454] = { top = 1900, left = -4100, w = 1400, h = 930, continent = 1 }
  M.mapNames[1454] = "Orgrimmar"
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de l'Honneur"
  M.AddItem(3026, { name = "Arc-Test", classID = 2, subclassID = 2, stack = 1 })
  M.inventory[18] = { id = 3026, dur = 50, durMax = 60 }
  M.skills = { { name = "Travail du cuir", rank = 143, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 92, max = 150 }, { name = "Secourisme", rank = 42, max = 75 } }
  for id, t in pairs({ [2319] = "Cuir moyen", [2318] = "Cuir léger", [4234] = "Cuir lourd", [2321] = "Bobine de fil raffiné",
    [2320] = "Bobine de fil grossier", [4289] = "Sel", [4340] = "Teinture grise", [4265] = "Renfort d'armure lourd",
    [5961] = "Pantalon noir en cuir", [4235] = "Peau lourde", [4236] = "Peau lourde traitée", [2589] = "Étoffe de lin",
    [2592] = "Étoffe de laine", [2604] = "Teinture rouge", [2605] = "Teinture verte" }) do
    M.AddItem(id, { name = t, stack = 20, sellPrice = 50, classID = 7 })
  end
  M.AddItem(4593, { name = "Poisson-Test", stack = 20, sellPrice = 1, classID = 0, subclassID = 5 })
  M.AddItem(2678, { name = "Epices douces", stack = 20, sellPrice = 0, classID = 7 })
  M.AddItem(3371, { name = "Fiole vide", stack = 20, sellPrice = 1, classID = 7 })
  M.SetBagItem(0, 8, 2319, 6)
  M.SetBagItem(0, 9, 4234, 4)
  M.SetBagItem(0, 10, 2604, 5)          -- a Tailoring dye: no use for this player
  local BANK = M.BankBagIDs()[1]
  M.SetBankItem(BANK, 1, 4234, 20)
  M.SetBankItem(BANK, 2, 4234, 6)
  M.SetBankItem(BANK, 3, 2319, 12)
  P.Update(true)
  M.trade = { open = false, line = 165, name = "Travail du cuir", rank = 143, max = 150, recipes = {
    [3780] = { name = "Renfort d'armure lourd", learned = true, diff = 1, maxTrivial = 165, mats = { 4234, 5, 2321, 1 }, item = 4265 },
    [7135] = { name = "Pantalon noir en cuir", learned = true, diff = 3, maxTrivial = 140, mats = { 2319, 12, 4340, 1, 2321, 1 }, item = 5961 },
  } }
  M.SetTradeSkill(true) M.Advance(1) M.SetTradeSkill(false)
  M.SetBank(true) M.Advance(0.5) M.SetBank(false) M.Advance(0.5)
  P.char.target[165] = 165

  -- visits: every NPC learned at the player's position when its window opens (name + map + x, y)
  local function at(x, y) M.pos = { mapID = 1454, x = x, y = y } M.Advance(0.05) end
  local function visitMerchant(name, x, y, items)
    at(x, y)
    M.units.npc = { name = name }
    M.merchantItems = items
    M.SetMerchant(true) M.Advance(0.3) M.SetMerchant(false) M.Advance(0.3)
  end
  visitMerchant("Fournisseur-Test", 0.60, 0.50, { { id = 2321, price = 100, stack = 1, maxStack = 20 },
    { id = 2515, price = 10, stack = 200, maxStack = 1000 }, { id = 4593, price = 25, stack = 5, maxStack = 20 },
    { id = 4340, price = 350, stack = 1, maxStack = 20 }, { id = 4289, price = 50, stack = 1, maxStack = 20 },
    { id = 2678, price = 2, stack = 1, maxStack = 20 }, { id = 3371, price = 20, stack = 5, maxStack = 20 },
    { id = 2605, price = 100, stack = 1, maxStack = 20 } })   -- (5.0) green dye: the plan may pick « Ceinture verte en cuir »
  local list = CR.db.npcs["1454"]
  local f = list and list["Fournisseur-Test"]
  local fi = f and CR.ItemsOf(f) or {}
  check(f and near(f.x, 0.60) and near(f.y, 0.50) and f.k.vendor and fi[2515] and fi[2515][2] == 200 and type(f.s) == "string",
    "merchant learned on visit: name, map, x, y, items with price and lot")
  at(0.55, 0.65) M.units.npc = { name = "Banquier-Test" } M.SetBank(true) M.Advance(0.3) M.SetBank(false) M.Advance(0.3)
  at(0.52, 0.52) M.units.npc = nil M.FireEvent("MAIL_SHOW") M.FireEvent("MAIL_CLOSED") M.Advance(0.2)
  at(0.30, 0.30) M.units.npc = { name = "Maître-Chasseur-Test", class = "HUNTER" }
  M.trainer = { open = false, tradeskill = false, services = { { name = "Morsure de serpent", cat = "available", cost = 1200 } } }
  M.SetTrainer(true) M.Advance(0.3) M.SetTrainer(false) M.Advance(0.3)
  at(0.65, 0.35) M.units.npc = { name = "Maître-Cuir-Test" }
  M.trainer = { open = false, tradeskill = true, line = "Travail du cuir", rank = 143, services = {
    { name = "Renfort d'armure lourd", cat = "used", req = 145, cost = 1000 } } }
  M.SetTrainer(true) M.Advance(0.3) M.SetTrainer(false) M.Advance(0.3)
  check(list["Banquier-Test"] and list["Banquier-Test"].k.bank, "banker learned")
  local mailKey
  for k, e in pairs(list) do if e.k.mail then mailKey = k end end
  check(mailKey and near(list[mailKey].x, 0.52), "mailbox learned (object: position as its key)")
  check(list["Maître-Chasseur-Test"] and list["Maître-Chasseur-Test"].k.ctrainer and list["Maître-Chasseur-Test"].c == "HUNTER", "class trainer learned")
  check(list["Maître-Cuir-Test"] and list["Maître-Cuir-Test"].k.ptrainer and list["Maître-Cuir-Test"].l == 165, "leatherworking trainer learned (line 165)")
  -- after the trainer visit Errands knows nothing is pending; a level-up later makes the class trainer due
  local Er = T.modules.Errands
  Er.char.trainPending = 30
  Er.char.nextTrain = { level = 30, cost = 5000 }
  M.player.level = 30
  at(0.50, 0.50)

  -- ------------------------------------------------------------------ 3. the shopping list
  -- (5.0 INTEG) ONE « à vendre » list for the whole addon: the inventory assistant's (verdicts prepared by small
  -- batches, the profession keeper computed off the event) — let both settle as they do in play
  local BG = T.Bags
  -- (no AH price known in this scenario: the option « vendre au marchand quand le prix d'HV est inconnu » is on, so
  -- that a trade good declared surplus by the profession keeper is a vendor sale as in 4.x)
  if BG and BG.db then BG.db.ahUnknown = true end
  -- (5.1 BAGS) …and no Questie here: « sans Questie : vendre quand même d'un clic » is on too (else the surplus is
  -- listed « à confirmer », one click each — tests/wow/bags_scenario.lua §11)
  if BG and BG.db then BG.db.noQuestie = true end
  if BG and BG.Warm then
    for _ = 1, 3 do BG.Warm() M.Advance(2, 0.1) end
    check(BG.Ready(), "inventory verdicts ready (keepers settled)")
  end
  local plan = CR.Plan({})
  local by = {}
  for _, e in ipairs(plan.errands) do by[e.id] = e end
  for _, e in ipairs(plan.optional) do by[e.id] = by[e.id] or e end
  if os.getenv("CITYRUN_DEBUG") then
    for _, e in ipairs(plan.errands) do io.stderr:write("ERR ", e.id, " ", e.kind, " | ", e.label, " | ", tostring(e.cost), "\n") end
    for _, e in ipairs(plan.optional) do io.stderr:write("OPT ", e.id, " | ", e.label, "\n") end
    for i, s0 in ipairs(plan.stops) do io.stderr:write("STOP ", i, " ", s0.title, " | ", s0.sub, "\n") end
    for _, u in ipairs(plan.unknown) do io.stderr:write("UNK ", u.what, " | ", u.errand.label, "\n") end
    local Gm = T.Compass.Geo
    io.stderr:write("DIST ", tostring(Gm.Vector({ mapID = 1454, x = 0.65, y = 0.35 }, { mapID = 1454, x = 0.30, y = 0.30 })), "\n")
    for _, c in ipairs(CR.Candidates("ctrainer")) do io.stderr:write("CT ", c.name, " ", c.x, " ", c.y, " ", tostring(c.src), "\n") end
    for _, c in ipairs(CR.Candidates("ptrainer", { line = 165 })) do io.stderr:write("PT ", c.name, " ", c.x, " ", c.y, " ", tostring(c.src), "\n") end
  end
  check(by["mats:165"] and by["mats:165"].items[1], "leatherworking vendor mats listed")
  local thread
  for _, it in ipairs(by["mats:165"] and by["mats:165"].items or {}) do if it.id == 2321 then thread = it end end
  check(thread and thread.n >= 1 and thread.cost == thread.n * 100, "Fine Thread: exact quantity, exact price")
  local heavy
  for _, it in ipairs(by.bank and by.bank.items or {}) do if it.id == 4234 then heavy = it end end
  check(heavy and heavy.n == 26, "bank withdrawal: 26 Cuir lourd")
  local ammo = by.ammo
  check(ammo and ammo.items[1].id == 2515 and ammo.items[1].n % 200 == 0 and ammo.items[1].n >= 660,
    "arrows for the bow, in lots of 200: " .. tostring(ammo and ammo.items[1].n))
  local pf = by.petfood
  check(pf and pf.items[1] and pf.items[1].id == 4593 and pf.items[1].n % 5 == 0, "pet food of its diet (fish), lots of 5")
  check(by.ctrainer and not by.ctrainer.optional and by.ctrainer.cost == 5000, "class trainer: new ranks, price read at the last visit")
  check(by.repair and by.sell and by.sell.gain > 0, "repair (12 %) and sell the greys")
  check(by["ptrainer:165"] and (by["ptrainer:129"] or {}).optional, "leatherworking trainer listed; « Aller au maître de secourisme » offered with nothing to learn")
  -- NPCs: everything placed, the same building grouped, route ends at the flight master (Data)
  local names = {}
  for _, s in ipairs(plan.stops) do for _, n in ipairs(s.npcs) do names[CR.ShownName(n)] = true end end
  check(names["Fournisseur-Test"] and names["Banquier-Test"] and names["Maître-Chasseur-Test"], "stops at the learned NPCs")
  check(#plan.unknown == 0, "no « demande à un garde » line: every NPC is known (" .. #plan.unknown .. ")")
  -- an NPC never seen (no memory, no Questie): « demande à un garde », never a made-up position
  local saved = CR.db.npcs["1454"]["Fournisseur-Test"]
  CR.db.npcs["1454"]["Fournisseur-Test"] = nil
  local blind = CR.Plan({})
  local askAmmo = false
  for _, u in ipairs(blind.unknown) do if u.errand.id == "ammo" then askAmmo = true end end
  check(askAmmo, "unknown vendor → « demande à un garde » line")
  CR.db.npcs["1454"]["Fournisseur-Test"] = saved
  check(plan.exit and plan.exit.kind == "flight" and near(plan.exit.x, 0.453, 1e-3), "exit: Orgrimmar flight master from Data")
  local vendStop
  for _, s in ipairs(plan.stops) do if s.kinds.vendor then vendStop = s end end
  check(vendStop and vendStop.kinds.repair and vendStop.kinds.sell, "repair + sale grouped at the vendor already on the route")
  check(plan.sec > 0 and plan.cost > 0, ("total time %d s, cost %d"):format(plan.sec, plan.cost))
  -- the route is the best order (exact, ≤ 9 stops)
  local nodes = { [0] = { mapID = 1454, x = 0.5, y = 0.5 } }
  for i, s in ipairs(plan.stops) do nodes[i] = s end
  nodes[#plan.stops + 1] = plan.exit
  local function legs(o)
    local s, prev = 0, 0
    for _, k in ipairs(o) do s = s + CR.LegSec(nodes[prev], nodes[k]) prev = k end
    return s + CR.LegSec(nodes[prev], nodes[#plan.stops + 1])
  end
  local idn = {}
  for i = 1, #plan.stops do idn[i] = i end
  local bestL = math.huge
  local function perm2(left, acc)
    if #left == 0 then bestL = math.min(bestL, legs(acc)) return end
    for i = 1, #left do
      local rest, a2 = {}, {}
      for k = 1, #left do if k ~= i then rest[#rest + 1] = left[k] end end
      for k = 1, #acc do a2[k] = acc[k] end
      a2[#a2 + 1] = left[i]
      perm2(rest, a2)
    end
  end
  perm2(idn, {})
  check(near(legs(idn), bestL, 1e-6), ("route order optimal: %.1f s vs best %.1f s"):format(legs(idn), bestL))
  -- money cap
  local money0 = M.player.money
  M.player.money = 2000
  local poor = CR.Plan({})
  local mt = CR.MoneyText(poor)
  check(#poor.dropped >= 1 and mt and mt:find("Il te manque", 1, true), "money cap: « " .. tostring(mt) .. " »")
  local keptAmmo = false
  for _, e in ipairs(poor.errands) do if e.id == "ammo" then keptAmmo = true end end
  for _, e in ipairs(poor.dropped) do check(e.prio <= 85, "dropped by priority: " .. e.id) end
  check(keptAmmo or poor.dropped[#poor.dropped].id == "ammo", "ammo dropped last")
  M.player.money = money0

  -- ------------------------------------------------------------------ 4. verdicts « en trop »
  local v = CR.Verdict(2604)
  check(v and v.extra == 5 and v.text:find("Pas besoin : sert en Couture", 1, true), "Tailoring dye: « " .. tostring(v and v.text) .. " »")
  check(CR.BagBadge(2604), "bag badge « trop » on the dye")
  local II = T.modules.ItemInfo
  if II and II.Build then
    local lines = II.Build({ id = 2604, name = "Teinture rouge", classID = 7, quality = 1, sellPrice = 50 }, { bag = true, count = 5 }, false)
    local txt = {}
    for _, l in ipairs(lines or {}) do txt[#txt + 1] = T.PlainText and T.PlainText(l[1]) or l[1] end
    -- (5.0 INTEG) the precise verdict is said ONCE on the tooltip: by the inventory assistant's line when it has one
    -- (« TreeAI · Vends … : Pas besoin : sert en Couture … », fed by the profession keeper), else by ItemInfo
    local inInfo = table.concat(txt, "\n"):find("Pas besoin : sert en Couture", 1, true) ~= nil
    local tip = T.Bags and T.Bags.TipLine and T.Bags.TipLine(2604) or nil
    local inBags = type(tip) == "string" and tip:find("Pas besoin : sert en Couture", 1, true) ~= nil
    check(inInfo ~= inBags, "item tooltip: the precise verdict, exactly once (ItemInfo " .. tostring(inInfo) .. " / Sacs " .. tostring(inBags) .. " : " .. tostring(tip) .. ")")
  end
  local HL = T.modules.Highlight
  if HL and HL.Apply then
    local btn = CreateFrame("Button", nil, UIParent)
    btn:SetSize(36, 36)
    local ov = HL.Apply(btn, "extra", CR.BagBadge(2604))
    check(ov and ov.text:GetText() == "trop", "bag slot badge « trop » (Highlight overlay)")
    if HL.HideOverlay then HL.HideOverlay(btn) end
  end
  local vl = CR.Verdict(4234)
  check(vl and vl.text:find("Besoin", 1, true) and not (vl.extra and vl.extra > 0 and vl.text:find("Garde")), "Cuir lourd: « " .. tostring(vl and vl.text) .. " »")
  local xl = CR.ExtraList()
  check(xl[1] and xl[1].id == 2604 and xl[1].sell == 50, "« À vendre / à jeter » lists the dye with its vendor value")
  M.quests = { { title = "Le cuir du chef", level = 27, complete = false,
    objectives = { { text = "Cuir lourd : 0/40", done = false, have = 0, need = 40, type = "item" } } } }
  M.FireEvent("QUEST_LOG_UPDATE") M.Advance(2)
  local vq = CR.Verdict(4234)
  check(vq and vq.text:find("Garde : quête « Le cuir du chef »", 1, true) and not CR.BagBadge(4234), "quest item kept: « " .. tostring(vq and vq.text) .. " »")
  M.quests = {} M.FireEvent("QUEST_LOG_UPDATE") M.Advance(2)

  -- ------------------------------------------------------------------ 4b. one dismissible suggestion in a capital
  -- (5.0 PROF) the proposal is computed in two frames (errands, then the route) and « nothing to propose » is kept 2 min
  CR.proposedFor, CR.proposeTried = nil, nil
  CR.CheckPropose() M.Advance(0.6)
  local function proposals()
    local n, e0 = 0, nil
    for _, e in ipairs(T:GetSuggestions()) do if tostring(e.id):find("^CityRun:") then n, e0 = n + 1, e end end
    return n, e0
  end
  local np, pe = proposals()
  check(np == 1 and pe.text:find("Courses en ville", 1, true) and type(pe.onClick) == "function", "one suggestion « Courses en ville : … » in Orgrimmar")
  CR.proposeTried = nil
  CR.CheckPropose() M.Advance(0.6)
  check(select(1, proposals()) == 1, "proposed once per visit")
  T:DismissSuggestion(pe.id)
  CR.proposedFor, CR.proposeTried = nil, nil
  CR.CheckPropose() M.Advance(0.6)
  check(select(1, proposals()) == 0, "dismissed: not proposed again")
  -- the window pages
  local Wp = T.Window
  if Wp and Wp._newLay and Wp.pageIndex.city then
    local Ly = Wp._newLay(600)
    local okR, err = pcall(Wp.pageIndex.city.render, Ly)
    check(okR, "page « Courses en ville » renders: " .. tostring(err))
    local labels = {}
    for _, r in ipairs(Ly.rects) do labels[#labels + 1] = r.label end
    local all = table.concat(labels, "\n")
    check(all:find("Lancer les courses", 1, true) and all:find("Trajet", 1, true) and all:find("À vendre / à jeter", 1, true), "page: start, route, « À vendre / à jeter »")
    local Ly2 = Wp._newLay(600)
    PL.SetLine(165)
    local okP, errP = pcall(Wp.pageIndex.prof.render, Ly2)
    check(okP, "page Métiers renders: " .. tostring(errP))
    local l2 = {}
    for _, r in ipairs(Ly2.rects) do l2[#l2 + 1] = r.label end
    local all2 = table.concat(l2, "\n")
    -- (5.0 PROF) the 3 big actions are the page's « top » ; the map pins toggle lives under « Réglages du plan » (folded)
    local tops = {}
    for _, b in ipairs(Wp.pageIndex.prof.top()) do tops[#tops + 1] = b[1] end
    local topAll = table.concat(tops, "\n")
    check(topAll:find("Me concentrer", 1, true) and topAll:find("Courses en ville", 1, true) and topAll:find("Ce que je peux apprendre", 1, true)
      and all2:find("Réglages du plan", 1, true), "Métiers page: 3 top actions (« Me concentrer », « Courses en ville », « Ce que je peux apprendre »), settings folded")
    check(all2:find("Options : le moins cher par point", 1, true) and all2:find("Étape suivante", 1, true), "Métiers page: options + next / previous step")
  end

  -- ------------------------------------------------------------------ 5. focus: start, compass, stops checked off
  M.violations = {}
  local out0 = chat()
  local R = T.Route
  local fired = {}
  T:On("FOCUS_CHANGED", fired, function(_, _, kind) fired[#fired + 1] = kind or "nil" end)
  check(CR.Start(), "courses started")
  M.Advance(0.5)
  check(T.focus and T.focus.kind == "city" and T.focus.owner == "CityRun" and T:FocusMode() == "city", "T.focus contract (city)")
  check(fired[1] == "city", "FOCUS_CHANGED(city)")
  check(R and R.char and R.char.paused == true, "quest route paused")
  local C = T.Compass
  local cs = CR.CurrentStop()
  local cc = C and C.char and C.char.custom
  check(cs and type(cc) == "table" and cc.source == "city" and near(cc.x > 1 and cc.x / 100 or cc.x, cs.x, 1e-3), "compass aims at stop 1")
  local W = T.Window
  if W and W.CardModel then
    local rows, mode, title = W.CardModel(3)
    check(mode == "focus" and title == "Courses en ville" and #rows >= 1, "card shows the courses: " .. tostring(title))
  end
  -- pick another stop (click) / skip it (right-click) / next / previous
  local stops = CR.run.stops
  local last = stops[#stops]
  CR.Pick("city", last)
  M.Advance(0.3)
  check(CR.CurrentStop().key == last.key, "click: that stop becomes the current one")
  CR.Replan()
  check(CR.CurrentStop().key == last.key, "re-plan keeps the player's choice")
  CR.Skip("city", last)
  check(CR.CurrentStop().key ~= last.key, "right-click: stop skipped")
  CR.Skip("city", last)
  CR.StepMove(1)
  local afterNext = CR.CurrentStop()
  CR.StepMove(-1)
  check(afterNext and CR.CurrentStop().key ~= afterNext.key, "next / previous step")
  CR.char.pick.city.key = nil
  M.Advance(0.3)

  -- go through the stops, opening each window: ONE game action per click, never in combat
  local function card() return PL.GetCard() end
  local clicks = 0
  local guard = 0
  while CR.run and guard < 12 do
    guard = guard + 1
    local s = CR.CurrentStop()
    if not s then break end
    at(s.x, s.y)
    if s.kinds.vendor or s.kinds.sell or s.kinds.repair then
      M.units.npc = { name = CR.ShownName(s.npcs[1]) }
      M.merchantItems = { { id = 2321, price = 100, stack = 1, maxStack = 20 }, { id = 2515, price = 10, stack = 200, maxStack = 1000 },
        { id = 4593, price = 25, stack = 5, maxStack = 20 }, { id = 4340, price = 350, stack = 1, maxStack = 20 }, { id = 4289, price = 50, stack = 1, maxStack = 20 },
        { id = 2678, price = 2, stack = 1, maxStack = 20 }, { id = 3371, price = 20, stack = 5, maxStack = 20 },
        { id = 2605, price = 100, stack = 1, maxStack = 20 } }
      M.bought = {}
      M.SetMerchant(true) M.Advance(1.6, 0.1)
      local c = card()
      -- (5.0 INTEG) ONE sell / repair card: the inventory assistant's « Vendre N objets · X + réparer », under the
      -- courses card, which keeps the purchases only (no second « Vendre » / « Réparer » button)
      local bc = BG and BG.GetCard and BG.GetCard("merchant")
      local bm = BG and BG.cardModel
      check(bc and bc:IsShown() and bm and bm.label and bm.label:find("^Vendre %d+ objet") and bm.label:find("réparer", 1, true),
        "merchant: ONE sell + repair card (Bags) « " .. tostring(bm and bm.label) .. " »")
      check(c and c:IsShown() and c.label and not c.label:find("^Vendre") and not c.label:find("^Réparer"),
        "merchant: the courses card has no sell / repair button « " .. tostring(c and c.label) .. " »")
      M.EnterCombat()
      check(not CR.SellNext() and not CR.BuyNext(), "sell / buy refused in combat")
      M.LeaveCombat() M.Advance(0.6)
      -- one click on the Bags card: repair + sale by re-checked batches (its own scenario proves the details)
      local sold0 = M.sold or 0
      if bc and bc.btn then
        bc.btn:Click() clicks = clicks + 1
        M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(2, 0.1)
        M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1, 0.1)
      end
      check((M.sold or 0) > sold0, "the Bags card sold the surplus (" .. ((M.sold or 0) - sold0) .. " stacks)")
      CR.run.repaired = true                            -- (the mock's repair does not restore durability)
      check(CR.ErrandDone({ kind = "sell", id = "sell" }, CR.run), "sale errand checked off once the Bags card sold")
      local n = 0
      c = card()
      while c and c:IsShown() and c.label and n < 20 do
        local before = #M.bought + (M.sold or 0)
        local lab = c.label
        if os.getenv("CITYRUN_DEBUG") then io.stderr:write("CLICK ", lab, " money ", M.player.money, "\n") end
        M.hwCalls = 0
        c.btn:Click()
        clicks = clicks + 1
        M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(0.4)
        n = n + 1
        if lab:find("^Réparer") then CR.run.repaired = true end
        local after = #M.bought + (M.sold or 0)
        check(after - before <= 1, "one action per click (" .. lab .. ")")
        c = card()
      end
      check(#M.bought >= 1, "purchases made at the vendor: " .. #M.bought)
      M.SetMerchant(false) M.Advance(0.4)
    elseif s.kinds.bank then
      M.units.npc = { name = "Banquier-Test" }
      M.SetBank(true) M.Advance(0.5)
      local c = card()
      check(c and c:IsShown() and c.label and c.label:find("^Retirer"), "bank: « Retirer … »")
      local n = 0
      while c and c:IsShown() and c.label and c.label:find("^Retirer") and n < 6 do
        c.btn:Click() clicks = clicks + 1
        M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(0.5)
        n = n + 1
        c = card()
      end
      M.SetBank(false) M.Advance(0.4)
    elseif s.kinds.ctrainer then
      M.units.npc = { name = "Maître-Chasseur-Test", class = "HUNTER" }
      M.trainer = { open = false, tradeskill = false, services = { { name = "Morsure de serpent", cat = "available", cost = 1200 } } }
      M.SetTrainer(true) M.Advance(0.4) M.SetTrainer(false) M.Advance(0.4)
    elseif s.kinds.mail then
      M.FireEvent("MAIL_SHOW") M.Advance(0.2) M.FireEvent("MAIL_CLOSED") M.Advance(0.4)
    else
      CR.Skip("city", s) M.Advance(0.3)
    end
    if CR.run and CR.CurrentStop() and CR.CurrentStop().key == s.key then
      check(false, "stop not checked off by its window: " .. tostring(s.title))
      for _, e in ipairs(s.errands) do if not CR.ErrandDone(e) then
        local t = {}
        for _, it in ipairs(e.items or {}) do t[#t + 1] = it.id .. ":" .. T.API.ItemCount(it.id) .. "/" .. ((it.base or 0) + it.n) end
        io.stderr:write("UNDONE ", e.id, " ", table.concat(t, " "), "\n") end end
      CR.Skip("city", s)
    end
  end
  M.Advance(1)
  check(CR.run == nil, "all stops done → courses finished")
  check(T.focus == nil and fired[#fired] == "nil", "T.focus cleared, FOCUS_CHANGED(nil)")
  check(R.char.paused == false, "quest route resumed")
  local fin = false
  for _, l in ipairs(M.output) do if l:find("Fini : ", 1, true) then fin = l:find("^%[UIError%]") ~= nil end end
  check(fin and CR.last and CR.last.spent > 0, "« Fini : X dépensées, N min » on screen (never in chat)")
  check(chat() == out0, "nothing in the chat during the courses (" .. (chat() - out0) .. ")")
  check(#M.violations == 0 and #M.blocked == 0, "zero violation, zero blocked action (" .. #M.violations .. ")")
  check(clicks >= 3, "cards clicked: " .. clicks)

  -- « Me concentrer sur les métiers » : courses du métier d'abord (en ville), puis la session métier enchaînée
  M.Advance(1)
  CR.ProfFocus(165, "full")
  M.Advance(0.5)
  if CR.run then
    check(T.focus and T.focus.kind == "city" and CR.run.chain and CR.run.chain.line == 165, "prof focus in town: city errands first (chained)")
    CR.Stop("done")
    M.Advance(0.5)
  end
  check(PL.focus and T.focus and T.focus.kind == "prof", "then the profession session starts")
  PL.StopFocus("silent")
  M.Advance(0.5)

  -- ------------------------------------------------------------------ 6. profession session: pick / skip / next, kept
  check(PL.StartFocus(165, "full"), "session métier started")
  M.Advance(1)
  local st = PL.steps
  check(st and #st >= 3, "session steps: " .. tostring(st and #st))
  local hasGoto = false
  for _, x in ipairs(st or {}) do if x.key == "trainer:goto" or (x.kind == "trainer") then hasGoto = true end end
  check(hasGoto, "a « maître » step is always offered")
  local third = st[3]
  PL.RunStep(third)
  M.Advance(1)
  check(PL.steps[1].key == third.key, "click on step 3: it is the current step now")
  PL.Dirty("bags") M.Advance(1)
  check(PL.steps[1].key == third.key, "re-plan keeps the player's step")
  local rows = select(1, PL.CardModel(3))
  check(rows[1] and type(rows[1].onRightClick) == "function", "card rows: right-click = skip")
  rows[1].onRightClick()
  M.Advance(1)
  check(PL.steps[1].key ~= third.key and PL.steps[#PL.steps].key == third.key and PL.steps[#PL.steps].skipped, "skipped step moved to the end")
  local first = PL.steps[1].key
  CR.StepMove(1) M.Advance(1)
  check(PL.steps[1].key ~= first, "« Étape suivante »")
  CR.StepMove(-1) M.Advance(1)
  check(PL.steps[1].key == first, "« Étape précédente »")
  PL.StopFocus("silent")
  CR.char.pick.prof = nil

  -- ------------------------------------------------------------------ 7. honest options: First Aid 42, linen + wool
  M.skills[4].rank, M.skills[4].max = 72, 150
  M.SetBagItem(2, 1, 2589, 20)
  M.SetBagItem(2, 2, 2592, 20)
  M.SetBagItem(2, 3, 2592, 20)
  M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(1)
  P.Update(true)
  local cand = CR.TrainerCandidates(129)
  check(cand[3277] == 72, "Wool Bandage (requirement only estimated) is a trainer candidate at 72")
  local o = CR.ProfOptions(129)
  local byO = {}
  for _, x in ipairs(o and o.list or {}) do byO[x.id] = x end
  if os.getenv("CITYRUN_DEBUG") then
    for _, x in ipairs(o and o.list or {}) do io.stderr:write("OPTN ", x.id, " cost ", x.cost, " pts ", x.points, " perPt ", tostring(x.perPt), " farm ", x.farm, " ", table.concat(x.names or {}, ","), "\n") end
    for sp in pairs(cand) do io.stderr:write("CAND ", sp, "\n") end
  end
  check(byO.buy and byO.trainer and byO.farm, "three options compared")
  local wool = false
  for _, nm in ipairs(byO.trainer and byO.trainer.names or {}) do if nm == "Bandage en laine" then wool = true end end
  check(byO.trainer and byO.trainer.confirm and wool, "trainer option names the recipe (Bandage en laine), « à confirmer »")
  check(byO.trainer and byO.buy and (byO.trainer.perPt or 0) <= (byO.buy.perPt or 1e9),
    ("learning is cheaper per point (%.1f vs %.1f)"):format(byO.trainer and byO.trainer.perPt or -1, byO.buy and byO.buy.perPt or -1))
  check(o.best == "trainer" or o.best == "farm", "best option is not « acheter » here: " .. tostring(o.best))
  local fa = PL.Compute(129, "full", PL.Target(129, "full"))
  local learnStep
  for _, x in ipairs(PL.Steps(fa)) do if x.kind == "trainer" and x.learn then for _, l in ipairs(x.learn) do if l.spell == 3277 then learnStep = x end end end end
  if os.getenv("CITYRUN_DEBUG") then
    local a = CR.Assume(129)
    io.stderr:write("ASSUME ", type(a), " best ", tostring(o.best), "\n")
    for _, x in ipairs(PL.Steps(fa)) do io.stderr:write("FA ", x.kind, " | ", x.title, "\n") end
  end
  check(learnStep ~= nil, "the plan proposes learning Wool Bandage at the trainer")
  CR.SetChoice(129, "buy")
  local fb = PL.Compute(129, "full", PL.Target(129, "full"))
  local still = false
  for _, x in ipairs(PL.Steps(fb)) do if x.kind == "trainer" and x.learn then for _, l in ipairs(x.learn) do if l.spell == 3277 and l.at <= 72 then still = true end end end end
  check(not still, "« Mon choix : acheter » sticks: no early trainer recipe")
  CR.SetChoice(129, "trainer")
  check(CR.char.pick.prof and CR.char.pick.prof.key == "trainer:goto" or (CR.char.pick.prof and CR.char.pick.prof.key and CR.char.pick.prof.key:find("^trainer")),
    "choosing the trainer jumps to the trainer step")
  CR.SetChoice(129, nil)
  CR.char.pick.prof = nil

  -- ------------------------------------------------------------------ 8. map pins (no Questie), then Questie mocked
  local wm = CreateFrame("Frame", "WorldMapFrame", UIParent)
  local sc = CreateFrame("Frame", nil, wm)
  local child = CreateFrame("Frame", nil, sc)
  wm.ScrollContainer, sc.Child = sc, child
  function wm:GetMapID() return 1454 end
  function sc:GetLeft() return 100 end
  function sc:GetBottom() return 50 end
  function sc:GetWidth() return 1000 end
  function sc:GetHeight() return 667 end
  function child:GetLeft() return 100 end
  function child:GetBottom() return 50 end
  function child:GetWidth() return 1000 end
  function child:GetHeight() return 667 end
  local providers = {}
  function wm:AddDataProvider(p2) providers[#providers + 1] = p2 end
  rawset(_G, "MapCanvasDataProviderMixin", { OnAdded = function() end })
  wm.__shown = false
  CR._mapHooked = nil
  check(CR.AttachMap() and CR.mapEngine == "canvas" and #providers == 0, "(4.2) world map: pins inside the map canvas, no data provider")
  local wmPoints = #wm.__points
  wm:Show()
  local kinds, vendorWhy = {}, nil
  for _, b in ipairs(CR.mapPins) do
    if b:IsShown() and b.pin then
      kinds[b.pin.kind] = (kinds[b.pin.kind] or 0) + 1
      if b.pin.name == "Fournisseur-Test" then vendorWhy = table.concat(b.pin.why or {}, " | ") end
    end
  end
  check(kinds.ptrainer and kinds.bank and kinds.mail and kinds.flight and kinds.ctrainer, "pins: my trainers, bank, mailbox, flight, class trainer")
  check(CR.mapShown >= 5 and CR.mview:IsVisible() and CR.mview:GetParent() == child and #wm.__points == wmPoints,
    "pins on OUR layer inside the map canvas; WorldMapFrame never anchored (" .. CR.mapShown .. ")")
  local tb
  for _, b in ipairs(CR.mapPins) do if b:IsShown() and b.pin and b.pin.name == "Maître-Cuir-Test" then tb = b end end
  local pt = tb and tb.__points[1]
  if os.getenv("CITYRUN_DEBUG") and pt then io.stderr:write("PIN ", tostring(pt[1]), " ", tostring(pt[3]), " ", tostring(pt[4]), " ", tostring(pt[5]), "\n") end
  check(pt and pt[2] == CR.mcanvas and near(pt[4], 650) and near(pt[5], -0.35 * 667), "leatherworking trainer at (65 %, 35 %) of the canvas")
  check(tb and tb.icon:GetTexture():find("Trade_LeatherWorking", 1, true), "profession icon for the trainer")
  M.Advance(0.5)
  check(CR.mview:GetScript("OnUpdate") == nil, "map at rest: no OnUpdate (the game tells us when it zooms / changes)")
  local mouse = 0
  for _, b in ipairs(CR.mapPins) do if b:IsShown() then mouse = mouse + 1 end end
  check(mouse >= 5 and not (CR.mview.IsMouseEnabled and CR.mview:IsMouseEnabled()), "only the pin buttons take the mouse")
  -- pins during the courses: stops numbered, click = current step
  M.SetBagItem(1, 1, 2515, 100) M.SetBagItem(1, 2, nil)
  M.FireEvent("BAG_UPDATE_DELAYED") M.Advance(0.5)
  if CR.Start() then
    M.Advance(0.5)
    T.MapCanvas.Fire() M.Advance(0.1)
    local stopPins = {}
    for _, b in ipairs(CR.mapPins) do if b:IsShown() and b.pin and b.pin.kind == "stop" then stopPins[#stopPins + 1] = b end end
    check(#stopPins >= 1 and stopPins[1].num:GetText() ~= nil, "stops of the courses numbered on the map (" .. #stopPins .. ")")
    local target = stopPins[#stopPins]
    if target then
      local key = target.pin.stop.key                   -- (4.2) the click redraws at once (pins reused): read it first
      target:Click()
      check(CR.CurrentStop() and CR.CurrentStop().key == key, "pin click: that stop becomes the current step")
    end
    CR.Stop("silent")
  end
  -- minimap (own engine, in town)
  CR.DrawMini()
  check(CR.miniEngine == "own" and CR.miniShown >= 1, "minimap pins in town (" .. CR.miniShown .. ")")
  at(0.5, 0.5)
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }
  M.Advance(0.1)
  CR.DrawMini()
  check(CR.miniShown == 0, "no minimap pin outside the town")
  at(0.5, 0.5)
  -- Questie mocked: trainers / vendors / townsfolk read at run time (never copied)
  local Cp = T.Compass
  local oldQ, oldS = Cp.Questie, Cp.SpawnsOf
  local fakeDB = {
    QueryItemSingle = function(id, key) if key == "vendors" and id == 2321 then return { 7002 } end return nil end,
    QueryNPCSingle = function(npc, key) if key == "friendlyToFaction" then return "H" end return nil end,
  }
  local spawns = { [7001] = { 0.70, 0.20, "Maîtresse-Cuir-Questie" }, [7002] = { 0.62, 0.70, "Fournisseuse-Questie" }, [7003] = { 0.45, 0.45, "Banquière-Questie" } }
  Cp.Questie = function() return fakeDB, {} end
  Cp.SpawnsOf = function(_, id, _, _, mapID, out) local s = spawns[id] if s then out[#out + 1] = { mapID = 1454, x = s[1], y = s[2], name = s[3] } end end
  rawset(_G, "Questie", { db = { global = { professionTrainers = { [165] = { 7001 } } }, char = { townsfolk = { Banker = { 7003 } } } } })
  CR.PinsDirty()
  local qn = {}
  for _, pin in ipairs(CR.PinList(1454)) do qn[pin.name] = pin end
  check(qn["Maîtresse-Cuir-Questie"] and qn["Maîtresse-Cuir-Questie"].kind == "ptrainer", "Questie: profession trainer of MY profession")
  check(qn["Banquière-Questie"] and qn["Banquière-Questie"].kind == "bank", "Questie: townsfolk banker")
  check(qn["Maître-Cuir-Test"] ~= nil, "learned positions first, Questie only adds")
  Cp.Questie, Cp.SpawnsOf = oldQ, oldS
  rawset(_G, "Questie", nil)
  CR.PinsDirty()
  wm:Hide()
  check(not CR.mview:IsVisible(), "map closed: layer hidden with the map")
  check(#M.violations == 0, "no violation (" .. #M.violations .. ")")
  rawset(_G, "UnitFactionGroup", oldFaction)
  rawset(_G, "MapCanvasDataProviderMixin", nil)
end
