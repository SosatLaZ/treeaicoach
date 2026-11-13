-- 5.1 AH — the buy-and-resell desk (Modules/AHMath.lua + AHDesk.lua), run by tests/wow/test_ah5.py on the
-- C_AuctionHouse mock flavours (modern / forever). The player: « refais entièrement tout sur l'HV … que je puisse faire
-- de l'achat-revente en masse … sans te gourer ». Evidence first:
--   §1 property tests over generated markets: profit maths exact to the copper, no « sûr » deal ever loses money,
--      thin evidence = no deal, never more capital than the chosen share, floor never undercut.
--   §2 the engine on a fake market: classes, own auctions never bought, kept items never posted (T.Bags.Verdict).
--   §3 the mass workflow: one key press = at most one protected call (strict mock), live re-check before each buy,
--      a listing that changed is skipped, nothing in combat / AH closed, nothing in chat.
--   §4 the ledger: purchases, sale mails (invoice), expired mails, vendor resale → today's profit vs goal.
--   « CE QUE LE JOUEUR VOIT » : the docked panel's lines are printed (VOIT: …).
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X, D, A = T.AHMath, T.AHDesk, T:GetModule("Auction")
  check(X and D and A, "AHMath + AHDesk + Auction loaded")
  if not (X and D and A) then return end
  local floor, ceil, max, min = math.floor, math.ceil, math.max, math.min

  -- ================================================================ §1 properties (pure maths)
  local seed = 5120261
  local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648 return (floor(seed / 65536) % n) + 1 end
  check(X.cut(10000) == 500 and X.cut(10000, true) == 1500 and X.cut(19) == 0 and X.net(10000) == 9500, "cut 5 % / 15 %, floored")
  check(X.deposit(100, 5, 24) == 300 and X.deposit(100, 5, 24, true) == 1500 and X.deposit(100, 5, 24, false, 300) == 150,
    "deposit: ceiling formula, or the share learned from the client")
  check(X.depLoss(300, 1000) == 0 and X.depLoss(300, 500) == 300 and X.depLoss(300, 950) == 16 and X.depLoss(7, 333) == 15,
    "expected deposit losses rounded up")
  check(X.sellPm(nil) == nil and X.sellPm(0) == 50 and X.sellPm(48, 0, 20, 24) == 950 and X.sellPm(2, 4, 2, 24) == 333,
    "sale chance per posting (‰)")
  check(X.undercut(1000) == 999 and X.undercut(1000, 5) == 950 and X.undercut(1) == 1, "undercut")
  local bad = 0
  for _ = 1, 4000 do
    local qty, buyU, sellU = rnd(40), rnd(5000), rnd(9000)
    local buy = buyU * qty
    local dep, pm, neutral = rnd(400) - 1, 50 + rnd(900), rnd(10) == 1
    local net, gross, cut, loss = X.flipNet(buy, sellU, qty, dep, pm, neutral)
    if gross ~= sellU * qty or cut ~= floor(gross * (neutral and 15 or 5) / 100) or loss ~= ceil(dep * (1000 - pm) / pm)
      or net ~= gross - cut - loss - buy then bad = bad + 1 end
    local need = X.need(buy, 100, 15)
    local fl = X.floorUnit(buy, qty, dep, pm, neutral, need)
    if X.flipNet(buy, fl, qty, dep, pm, neutral) < need or (fl > 1 and X.flipNet(buy, fl - 1, qty, dep, pm, neutral) >= need) then bad = bad + 1 end
    -- the post price is never under the floor, whatever the competition
    local low = rnd(12000)
    local u = X.postUnit({ floor = fl, low = low, normal = rnd(2) == 1 and rnd(12000) or nil, mine = rnd(5) == 1 and rnd(12000) or nil })
    if u and (u < fl or X.flipNet(buy, u, qty, dep, pm, neutral) < need) then bad = bad + 1 end
  end
  check(bad == 0, "4000 random trades: net = gross − cut − deposit losses − buy, floor minimal, post price ≥ floor: " .. bad)

  -- generated markets: « sûr » never loses, the queue never exceeds the capital
  local function market(n, base, spread, commodity)
    local rows = {}
    for i = 1, n do
      local c = commodity and rnd(20) or 1
      local u = max(1, base + rnd(spread) - floor(spread / 2))
      rows[i] = { unit = u, count = c, buy = u * c + (commodity and 0 or rnd(c) - 1), auctionID = 1000 + i }
    end
    table.sort(rows, function(a, b) return a.unit < b.unit end)
    return rows
  end
  local EV = { ok = true, days = 5, scans = 10, hours = 60, sold = 48, sellers = 6, volPct = 5, ageH = 1 }
  local badSure, badFlip, badCap, nSure, nFlip, thin = 0, 0, 0, 0, 0, 0
  for round = 1, 600 do
    local com = round % 2 == 0
    local vendor = 20 + rnd(400)
    local rows = market(2 + rnd(10), vendor, vendor * 2, com)
    local all = {}
    for _, d in ipairs(X.vendorDeals(round, rows, vendor, com)) do
      nSure = nSure + 1
      all[#all + 1] = d
      -- executed exactly as planned: pay d.buy, sell d.qty units to the vendor
      local got = vendor * d.qty - d.buy
      if d.class ~= "sur" or got ~= d.net or got < X.RULES.minNet or d.fees ~= 0 or d.buy <= 0 then badSure = badSure + 1 end
      if com then
        local cost, left = 0, d.qty
        for _, r in ipairs(rows) do local k = min(left, r.count) cost, left = cost + r.unit * k, left - k if left <= 0 then break end end
        if cost ~= d.buy then badSure = badSure + 1 end
      end
    end
    local mk = vendor * 3 + rnd(500)
    local o = { id = 100000 + round, rows = market(2 + rnd(10), floor(mk * 0.8), floor(mk * 0.6), com), market = mk, sold = EV.sold,
      ahead = rnd(10) - 1, maxQty = rnd(200), vendorUnit = vendor, hours = 24, neutral = false, commodity = com, ev = EV }
    for _, d in ipairs((X.flipDeals(o))) do
      nFlip = nFlip + 1
      all[#all + 1] = d
      local net = X.flipNet(d.buy, d.sellUnit, d.qty, d.dep, d.pm, false)
      if d.class ~= "probable" or net ~= d.net or d.net < X.need(d.buy, X.RULES.minNet, X.RULES.marginPct) or d.sellUnit > mk
        or d.fees ~= d.cut + d.depLoss or d.days > X.RULES.maxDays or d.capital ~= d.buy then badFlip = badFlip + 1 end
    end
    -- thin evidence (1 day, or stale prices, or unmeasured sales): never a deal
    for _, e in ipairs({ { ok = true, days = 1, scans = 10, hours = 60, sold = 48, sellers = 6, volPct = 5, ageH = 1 },
      { ok = true, days = 5, scans = 10, hours = 60, sold = 48, sellers = 6, volPct = 5, ageH = 30 },
      { ok = true, days = 5, scans = 10, hours = 2, sold = nil, sellers = 6, volPct = 5, ageH = 1 },
      { ok = true, days = 5, scans = 10, hours = 60, sold = 48, sellers = 1, volPct = 5, ageH = 1 },
      { ok = false, days = 5, scans = 10, hours = 60, sold = 48, sellers = 6, volPct = 5, ageH = 1 } }) do
      o.ev = e
      thin = thin + #(X.flipDeals(o))
    end
    local gold, tied = rnd(300000), rnd(100000)
    local share = 10 * rnd(9)
    local budget = X.budget(gold, tied, share, rnd(50000))
    local q, spent = X.allocate(all, budget, floor((gold + tied) * 20 / 100))
    local sum, last = 0, nil
    for _, d in ipairs(q) do
      sum = sum + d.buy
      if last and d.net > last then badCap = badCap + 1 end
      last = d.net
    end
    if sum ~= spent or spent > budget or spent + tied > floor((gold + tied) * share / 100) + 0 and spent > 0 and spent > budget then badCap = badCap + 1 end
    if budget > gold then badCap = badCap + 1 end
  end
  check(nSure > 100 and badSure == 0, ("%d « sûr » deals over 600 markets: none loses money, profit exact: %d wrong"):format(nSure, badSure))
  check(nFlip > 100 and badFlip == 0, ("%d « probable » deals: net exact to the copper, margin kept, never above the usual price: %d wrong"):format(nFlip, badFlip))
  check(thin == 0, "thin / stale evidence: no deal at all (" .. thin .. ")")
  check(badCap == 0, "queue: best net first, never more than the budget (share of gold − reserve): " .. badCap)
  check(X.budget(100000, 0, 50, 0) == 50000 and X.budget(100000, 40000, 50, 0) == 30000 and X.budget(100000, 0, 50, 80000) == 20000
    and X.budget(100000, 90000, 50, 0) == 5000 and X.budget(1000, 5000, 50, 0) == 0, "budget keeps the reserve")
  local led = X.realized({ { k = "buy", id = 1, n = 10, c = 1000, day = "d1" }, { k = "sale", id = 1, n = 4, c = 600, cut = 30, day = "d1" },
    { k = "exp", id = 1, n = 6, dep = 12, day = "d2" }, { k = "vend", id = 1, n = 6, c = 480, cut = 0, day = "d2" },
    { k = "sale", name = "butin", n = 1, c = 200, cut = 10, day = "d2" } })
  check(led.days.d1 == 170 and led.days.d2 == -12 + (480 - 600) + 190 and led.tied == 0 and led.total == 228
    and led.items[1].profit == 38, "ledger: FIFO cost, fees, expired deposit: " .. tostring(led.total))
  local g = X.goal(120000, 500000, 100000)
  check(g.pct == 24 and g.left == 380000 and g.reachable == false and X.goal(0, 500000, nil).reachable == nil, "goal: honest (unknown stays unknown)")

  -- ================================================================ §2 the engine on a fake market
  local modern = rawget(_G, "C_AuctionHouse") ~= nil
  if not modern then return end
  local me = M.player.name
  M.AddItem(91001, { name = "Cuir-test", stack = 20, sellPrice = 50, type = "Artisanat", classID = 7 })
  M.AddItem(91002, { name = "Caillou-test", stack = 20, sellPrice = 40, type = "Artisanat", classID = 7 })
  M.AddItem(91003, { name = "Dague-test", stack = 1, sellPrice = 800, type = "Arme", classID = 2 })
  M.AddItem(91004, { name = "Soie-test", stack = 20, sellPrice = 10, type = "Artisanat", classID = 7 })
  M.AddItem(91006, { name = "Plume-test", stack = 20, sellPrice = 40, type = "Artisanat", classID = 7 })
  M.AddItem(91007, { name = "Anneau-test", stack = 1, sellPrice = 900, type = "Armure", classID = 4 })
  A.db.autoReserve, A.db.keepGold, A.db.mountGold, A.db.hours = false, 0, 0, 24
  D.db.goal, D.db.sharePct, D.db.itemPct, D.db.minNetPa, D.db.marginPct = 50, 50, 20, 1, 15
  M.player.money = 500000
  M.ah.real = true
  local aid = 7000
  local function L(id, count, unit, owner)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = owner or ("Vendeur" .. (aid % 6)), auctionID = aid }
  end
  local day = floor(GetServerTime() / 86400)
  local function history(id, med, vendor, days, gone)
    local h, sl = {}, {}
    for k = days, 1, -1 do
      for _, v in ipairs({ day - k + 1, med - 2, med, 200, 2, gone or 40, 12 }) do h[#h + 1] = v end
      sl[#sl + 1] = day - k + 1 sl[#sl + 1] = 6
    end
    A.RealmData().items[id] = { h = h, sl = sl, v = vendor, last = GetServerTime() }
  end
  history(91001, 1000, 50, 5)            -- sells ~48 / day for 5 days at ~10 pa
  history(91004, 500, 10, 1)             -- seen one day only: thin
  M.ah.list = {
    L(91001, 20, 600), L(91001, 20, 620), L(91001, 20, 990), L(91001, 20, 1005),
    L(91002, 30, 25), L(91002, 10, 30), L(91002, 20, 60),
    L(91003, 1, 500), L(91003, 1, 790),
    L(91004, 20, 100), L(91004, 20, 480),
    L(91006, 20, 10, me), L(91006, 20, 20),
    L(91007, 1, 100, me), L(91007, 1, 200),
  }
  M.ah.owned = { { id = 91006, count = 20, buyout = 200, auctionID = M.ah.list[12].auctionID, owner = me },
    { id = 91007, count = 1, buyout = 100, auctionID = M.ah.list[14].auctionID, owner = me } }
  -- bags: a potion the inventory assistant keeps, a stack of loot nobody claims
  for b = 0, 4 do for s = 1, 16 do M.SetBagItem(b, s, nil) end end
  M.SetBagItem(0, 1, 929, 5)
  M.SetBagItem(0, 2, 91001, 20)
  M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED")
  local chat0, bids0 = #M.output, #M.ah.bids

  M.SetAuctionHouse(true)
  M.Advance(0.5, 0.05)
  check(D.frame and D.frame:IsShown() and not (A.win and A.win:IsShown()), "the desk is docked to the AH; the old window stays closed")
  local L0 = D.Lines()
  io.write("VOIT (avant scan): " .. L0.goal .. " | " .. L0.capital .. " | " .. L0.buy .. " | " .. L0.post .. " | " .. L0.deal, "\n")
  -- no full scan yet: only the items of my bags were looked up (targeted searches, not protected), nothing was bought
  check(#M.ah.bids == bids0 and A.lastScan == nil and A.lastMini ~= nil, "AH just opened: targeted price check of my bag items only, no action")

  D.frame.scan:Click()
  M.Advance(3, 0.05)
  check(A.lastScan and A.lastScan.full, "full scan read")
  check(D.built and #D.buyQ > 0, "queue built after the scan: " .. #D.buyQ)
  local by = {}
  for _, d in ipairs(D.all) do by[d.id] = by[d.id] or {} table.insert(by[d.id], d) end
  local c2, c3 = by[91002] and by[91002][1], by[91003] and by[91003][1]
  check(c2 and c2.class == "sur" and c2.qty == 40 and c2.buy == 1050 and c2.net == 550 and c2.fees == 0, "commodity under the vendor price: 40 for 10 pa 50 → +5 pa 50 sure")
  check(c3 and #by[91003] == 1 and c3.class == "sur" and c3.buy == 500 and c3.net == 300, "item under the vendor price: +3 pa sure; the 7 pa 90 one (+10 pc) is not worth a click")
  local f1 = by[91001]
  check(f1 and #f1 == 1 and f1[1].class == "probable" and f1[1].qty == 40 and f1[1].buy == 24400 and f1[1].sellUnit == 989,
    "flip with 5 days of evidence: 40 leather under the wall, resale 1 pc under the wall: " .. tostring(f1 and f1[1].sellUnit))
  if f1 and f1[1] then
    local d = f1[1]
    local net = 989 * 40 - floor(989 * 40 * 5 / 100) - d.depLoss - 24400
    check(d.net == net and d.cut == 1978 and d.fees == d.cut + d.depLoss and d.capital == 24400 and d.days and d.ev.days == 5,
      ("all fees counted: cut %d, deposit losses %d, net %d"):format(d.cut, d.depLoss, d.net))
  end
  check(by[91004] == nil, "one day of history: no deal (thin evidence is absent, not « sûr »)")
  check(by[91006] == nil, "commodity whose cheapest listing is mine: nothing proposed")
  check(by[91007] and #by[91007] == 1 and by[91007][1].buy == 200 and by[91007][1].net == 700, "ring: only the OTHER seller's auction (2 pa → vendor 9 pa)")
  local sum = 0
  for i, d in ipairs(D.buyQ) do
    sum = sum + d.buy
    check(i == 1 or D.buyQ[i - 1].net >= d.net, "queue sorted by net profit")
  end
  check(sum <= 250000, "queue ≤ 50 % of the gold: " .. sum)
  local L1 = D.Lines()
  io.write("VOIT (après scan): " .. L1.goal .. " | " .. L1.goal2 .. " | " .. L1.capital .. " | " .. L1.buy .. " | " .. L1.post .. " | " .. L1.next, "\n")
  io.write("VOIT (affaire): " .. L1.deal, "\n")
  check(L1.goal2:find("Objectif non atteignable", 1, true), "honest goal line: " .. L1.goal2)

  -- ================================================================ §3 mass workflow: one press = one action
  -- another player buys the cheap dagger before us: the live re-check must skip it
  for i, a in ipairs(M.ah.list) do if a.id == 91003 and a.buyout == 500 then table.remove(M.ah.list, i) break end end
  local presses, money0 = 0, M.player.money
  local viol0 = #M.violations
  while D.BuyHead() and presses < 40 do
    presses = presses + 1
    local before = #M.ah.bids
    M.Hw(T.AuctionNext)
    check(#M.ah.bids - before <= 1, "at most one purchase per press")
    M.Advance(0.5, 0.05)
  end
  local bought = {}
  for i = bids0 + 1, #M.ah.bids do
    local b = M.ah.bids[i]
    bought[b.id] = (bought[b.id] or 0) + b.count
    check(b.owner ~= me, "never one of my own auctions")
  end
  check(bought[91002] == 40 and bought[91001] == 40 and bought[91007] == 1 and not bought[91003] and not bought[91006] and not bought[91004],
    ("bought: stone ×%s, leather ×%s, ring ×%s, dagger %s"):format(tostring(bought[91002]), tostring(bought[91001]), tostring(bought[91007]), tostring(bought[91003])))
  check(#M.violations == viol0, "no protected call outside a click, none while the AH is busy: " .. (#M.violations - viol0))
  check(D.Said("l'annonce a changé") ~= nil, "the listing that changed was skipped, said in the panel")
  local r = D.Realized()
  check(money0 - M.player.money == 1050 + 24400 + 200 and r.tied == 25650, "gold spent = the three planned purchases, capital tied = 2 po 56 pa 50: " .. r.tied)
  check(D.sess.buys == 3 and presses <= 12, "3 buys in " .. presses .. " presses (commodity = quote + confirm)")
  local L2 = D.Lines()
  io.write("VOIT (après achats): " .. L2.capital .. " | " .. L2.buy .. " | " .. L2.post .. " | " .. L2.deal .. " | " .. L2.status, "\n")

  -- the bags: the potion is kept (T.Bags.Verdict), the loot leather goes to the AH above its floor
  M.Advance(1.5, 0.05)
  local potion, leather
  for _, e in ipairs(D.postQ) do if e.id == 929 then potion = e elseif e.id == 91001 then leather = e end end
  check(not potion, "a kept potion is never in the post queue")
  local B = T.Bags
  if B and B._active then
    check(leather and leather.unit >= leather.floor and leather.unit == 989 and leather.depExact,
      "loot leather: 1 pc under the cheapest, above the floor: " .. tostring(leather and leather.unit) .. " / " .. tostring(leather and leather.floor))
    io.write("VOIT (poster): " .. tostring(D.PostText(leather)), "\n")
    local posts0 = #M.ah.posts
    while D.PostHead() and presses < 60 do
      presses = presses + 1
      M.Hw(T.AuctionNext)
      M.Advance(0.6, 0.05)
    end
    local p = M.ah.posts[#M.ah.posts]
    check(#M.ah.posts == posts0 + 1 and p.id == 91001 and p.unit == 989 and p.count == 20, "posted once, at the computed price")
    check(D.sess.posts == 1 and D.char.posts[91001] and D.char.posts[91001][1].dep == 300, "post recorded with its real deposit (3 pa)")
  end
  -- combat / AH closed: nothing
  local n0 = #M.ah.bids + #M.ah.posts
  M.EnterCombat()
  M.Hw(T.AuctionNext)
  M.LeaveCombat()
  M.SetAuctionHouse(false)
  M.Hw(T.AuctionNext)
  M.Advance(2, 0.1)
  check(#M.ah.bids + #M.ah.posts == n0 and #M.violations == viol0, "combat / AH closed: no action")
  check(not D.frame:IsShown() and D.build == nil and D.pb == nil and D.pend == nil, "AH closed: panel hidden, nothing running")
  local chat = 0
  for i = chat0 + 1, #M.output do if tostring(M.output[i]):find("HV", 1, true) or tostring(M.output[i]):find("Achat", 1, true) then chat = chat + 1 end end
  check(chat == 0, "nothing in chat")

  -- ================================================================ §4 ledger: mail + vendor
  -- the 40 stones arrive by mail, go to the bags, are sold to a vendor (40 × 40 pc)
  M.SetBagItem(1, 1, 91002, 20) M.SetBagItem(1, 2, 91002, 20)
  M.FireEvent("BAG_UPDATE", 1) M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(1, 0.1)
  check(T.Bags.Verdict(91002, 1, 1).action == "sell", "the vendor lot is « à vendre au marchand » for the inventory assistant (one-click vendor card): "
    .. tostring(T.Bags.Verdict(91002, 1, 1).why))
  M.SetMerchant(true)
  M.SetBagItem(1, 1, nil) M.SetBagItem(1, 2, nil)
  M.FireEvent("BAG_UPDATE", 1) M.FireEvent("BAG_UPDATE_DELAYED")
  M.SetMerchant(false)
  r = D.Realized()
  check(r.today == 550 and r.items[91002].profit == 550, "vendor resale realised exactly as promised: +5 pa 50 → " .. r.today)
  -- 20 bought leather sold at the AH (invoice: 989 × 20, cut 989), 20 expired
  M.AddMail("Vente aux enchères réussie : Cuir-test (20)", 989 * 20 - 989 + 300, { type = "seller", item = "Cuir-test", bid = 989 * 20, deposit = 300, cut = 989 })
  M.AddMail("Vente aux enchères expirée : Cuir-test (20)", 0, nil, { id = 91001, count = 20 })
  M.FireEvent("MAIL_INBOX_UPDATE") M.FireEvent("MAIL_INBOX_UPDATE")
  M.Advance(1.5, 0.05)
  M.FireEvent("MAIL_INBOX_UPDATE")
  M.Advance(1.5, 0.05)
  r = D.Realized()
  local sale = 989 * 20 - 989 - 12200
  check(r.today == 550 + sale, ("sale mail counted once, FIFO cost 1 po 22: +%d → today %d"):format(sale, r.today))
  check(r.items[91001].held == 20 and r.tied == 12200 + 200, "20 leather (cost 1 po 22 pa) and the ring still held")
  local l1 = D.GoalLines()
  io.write("VOIT (bilan): " .. l1 .. " | " .. D.CapitalLine(), "\n")
  check(l1:find("Aujourd'hui : +", 1, true) and l1:find("sur 50 po", 1, true), "today's profit vs the goal: " .. l1)
  -- the window's HV page (AH closed): the goal first, then the desk's queue (recording layout)
  local rec = {}
  local Ly = {}
  function Ly.tiles(list) for _, it in ipairs(list) do rec[#rec + 1] = "tile:" .. it[1] .. " / " .. tostring(it[2]) end end
  function Ly.header(t) rec[#rec + 1] = "header:" .. t return true end
  function Ly.row(e) rec[#rec + 1] = "row:" .. e.text end
  function Ly.text(t) rec[#rec + 1] = "text:" .. t end
  function Ly.empty(t) rec[#rec + 1] = "empty:" .. t end
  function Ly.buttons(l) for _, b in ipairs(l) do rec[#rec + 1] = "button:" .. b[1] end end
  function Ly.gap() end
  A.view = "flips"
  A.RenderPage(Ly, T.Window)
  io.write("VOIT (page HV): " .. table.concat(rec, " | "):sub(1, 700), "\n")
  check(rec[1] == "header:Achat-revente : ton objectif du jour" and tostring(rec[2]):find("text:Aujourd'hui : +", 1, true),
    "HV page: today's profit vs goal first")
  -- what the real client answered is kept for the error report (counters only), and the API probe has its AH section
  local dg = TreeAIDB and TreeAIDB.diag and TreeAIDB.diag.ah or {}
  check((dg.purchaseCompleted or 0) >= 1 and (dg.quote or 0) >= 2 and (dg.commoditySucceeded or 0) >= 2 and (dg.depPm24 or 0) == 300,
    "diag.ah: answers of the game counted (purchase / quote / deposit share)")
  local Bg = T:GetModule("Bugs")
  local probe = Bg and Bg.ProbeAPI and table.concat(Bg.ProbeAPI(), "\n") or ""
  check(probe:find("-- Hôtel des ventes", 1, true) and probe:find("C_AuctionHouse.PlaceBid : présente", 1, true)
    and probe:find("C_AuctionHouse.CalculateCommodityDeposit : 1 retour", 1, true), "API probe: auction-house section")
  local s = D.Summary()
  check(s.today == r.today and s.goal == 500000 and s.tied == 12400, "summary for the app / the window")
end
