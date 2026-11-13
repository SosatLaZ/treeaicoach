-- 5.1 AH, slice 2 — « vois ce qui peut être régulé par moi-même sans te gourer, genre sur des items de merde »,
-- the cancel / repost queue, crafting spreads, sale-time patterns and the one-dashboard HV page. Run by
-- tests/wow/test_ah5.py on the C_AuctionHouse mock flavours (modern / forever).
--   §1 property tests (pure maths): a « réguler » deal only with every explicit threshold met (few sellers, measured
--      sales, wall cost cap, whole wall, resold in ≤ 4 days, 30 % margin), its net exact to the copper, a wall group
--      never split by the queue; cancel gain exact; craft spread exact; sale-time pattern only with enough data.
--   §2 the engine on a fake market: a cheap commodity wall held by 2 sellers → one lot, a wall of 3 single items →
--      3 purchases in a row; too dear / unmeasured walls → nothing; bought by key presses, never my own auction.
--   §3 cancel / repost: an undercut auction worth reposting → « Annuler » (one CancelAuction per press, throttle
--      respected, deposit lost counted once even when the « annulée » mail arrives); kept when not worth it.
--   §4 crafting spreads for the player's profession; §5 the HV page = one dashboard (no old tiles).
-- Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X, D, A = T.AHMath, T.AHDesk, T:GetModule("Auction")
  check(X and D and A, "AHMath + AHDesk + Auction loaded")
  if not (X and D and A) then return end
  local floor, ceil, max, min = math.floor, math.ceil, math.max, math.min
  local R0 = X.RULES

  -- ================================================================ §1 properties
  local seed = 5120262
  local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648 return (floor(seed / 65536) % n) + 1 end
  local EV = { ok = false, onlySellers = true, days = 5, scans = 10, hours = 60, sold = 12, sellers = 2, volPct = 5, ageH = 1 }
  local nReset, badReset, refused = 0, 0, 0
  local names = { "Vendeur1", "Vendeur2", "Vendeur3", "Vendeur4", "Vendeur5" }
  for round = 1, 1500 do
    local com = round % 2 == 0
    local market = 50 + rnd(R0.resetMaxUnit + 2000)
    local nOwn = rnd(5)
    local rows = {}
    for i = 1, 1 + rnd(8) do
      local c = com and rnd(30) or 1
      local u = max(1, floor(market * (20 + rnd(110)) / 100))
      rows[i] = { unit = u, count = c, buy = u * c, auctionID = 5000 + i, seller = names[rnd(nOwn)] }
    end
    table.sort(rows, function(a, b) return a.unit < b.unit end)
    local sold = rnd(4) == 1 and rnd(3) - 1 or rnd(40)
    local ev = {}
    for k, v in pairs(EV) do ev[k] = v end
    ev.sold = sold
    if rnd(6) == 1 then ev.days = 2 end
    local o = { id = 300 + round, rows = rows, market = market, sold = sold, ahead = rnd(6) - 1, vendorUnit = rnd(60),
      hours = 24, neutral = rnd(8) == 1, commodity = com, ev = ev, cap = rnd(3) == 1 and rnd(40000) or nil }
    local ds = X.resetDeals(o)
    if #ds == 0 then refused = refused + 1 end
    if #ds > 0 then
      nReset = nReset + 1
      local lim = floor(market * R0.resetWallPct / 100)
      local wallQ, wallB, own, nrow = 0, 0, {}, 0
      for _, r in ipairs(rows) do
        if r.unit < lim then wallQ, wallB, nrow = wallQ + r.count, wallB + r.buy, nrow + 1 own[r.seller] = true end
      end
      local nO = 0
      for _ in pairs(own) do nO = nO + 1 end
      local nxt = rows[nrow + 1]
      local sell = nxt and min(market, nxt.unit - 1) or market
      local sumNet, sumQ, sumB = 0, 0, 0
      for _, d in ipairs(ds) do
        local net = X.flipNet(d.buy, d.sellUnit, d.qty, d.dep, d.pm, o.neutral)
        if net ~= d.net or d.dep ~= X.deposit(o.vendorUnit, d.qty, 24, o.neutral) or d.class ~= "regul" or d.group ~= ds[1].group
          or d.sellUnit ~= sell then badReset = badReset + 1 end
        sumNet, sumQ, sumB = sumNet + d.net, sumQ + d.qty, sumB + d.buy
      end
      if sumQ ~= wallQ or sumB ~= wallB or ds[1].groupNet ~= sumNet or ds[1].groupBuy ~= sumB          -- the WHOLE wall
        or nO > R0.resetMaxSellers or sumB > min(R0.resetCap, o.cap or 1e12) or market > R0.resetMaxUnit
        or sold < R0.resetMinSold or ev.days < R0.minDays or X.days(sold, o.ahead, sumQ) > R0.resetMaxDays
        or sumNet < X.need(sumB, R0.minNet, R0.resetMarginPct) or ds[1].worst ~= o.vendorUnit * sumQ - sumB
        or (com and #ds ~= 1) or (not com and #ds ~= nrow) then badReset = badReset + 1 end
      -- the queue takes a wall whole or not at all
      local q = X.allocate(ds, sumB - 1, nil)
      local q2 = X.allocate(ds, sumB, nil)
      if #q ~= 0 or #q2 ~= #ds then badReset = badReset + 1 end
    end
  end
  check(nReset > 40 and badReset == 0 and refused > 400,
    ("%d « réguler » deals over 1500 markets: every threshold met, whole wall, net exact, group never split: %d wrong (%d refused)"):format(nReset, badReset, refused))
  -- each threshold alone refuses
  local base = { id = 1, market = 100, sold = 12, ahead = 0, vendorUnit = 5, hours = 24, commodity = true, ev = EV,
    rows = { { unit = 40, count = 10, buy = 400, seller = "Vendeur1" }, { unit = 45, count = 15, buy = 675, seller = "Vendeur2" },
      { unit = 98, count = 20, buy = 1960, seller = "Vendeur1" } } }
  local function with(k, v) local o = {} for a, b in pairs(base) do o[a] = b end o[k] = v return o end
  local ok1 = X.resetDeals(base)
  check(#ok1 == 1 and ok1[1].qty == 25 and ok1[1].buy == 1075 and ok1[1].sellUnit == 97, "base wall: 25 units for 10 pa 75, relisted at 97 pc")
  local fourSellers = { { unit = 40, count = 10, buy = 400, seller = "Vendeur1,Vendeur3" }, { unit = 45, count = 15, buy = 675, seller = "Vendeur2,Vendeur4" },
    { unit = 98, count = 20, buy = 1960, seller = "Vendeur1" } }
  local noOwner = { { unit = 40, count = 10, buy = 400 }, { unit = 98, count = 20, buy = 1960, seller = "Vendeur1" } }
  local thin = {} for k, v in pairs(EV) do thin[k] = v end thin.days = 1
  local cases = { { "4 sellers in the wall", with("rows", fourSellers) }, { "sellers unknown", with("rows", noOwner) },
    { "1 sale / day", with("sold", 1) }, { "sales unmeasured", with("sold", nil) }, { "wall over the cap", with("cap", 1000) },
    { "not a cheap item", with("market", R0.resetMaxUnit + 1) }, { "1 day of history", with("ev", thin) },
    { "my own auction under the resale", with("mineLow", 60) }, { "wall too long to read", with("more", true) } }
  for _, c in ipairs(cases) do
    local o = c[2]
    if c[1] == "wall too long to read" then o.rows = { base.rows[1], base.rows[2] } end
    local ds, why = X.resetDeals(o)
    check(#ds == 0 and type(why) == "string", "réguler refused: " .. c[1] .. " (" .. tostring(why) .. ")")
  end
  -- cancel gain, exact
  local badC = 0
  for _ = 1, 3000 do
    local o = { my = rnd(5000), n = rnd(20), dep = rnd(300) - 1, aheadKeep = rnd(60) - 1, new = rnd(5000), aheadNew = rnd(10) - 1,
      dep2 = rnd(300) - 1, cost = rnd(3) == 1 and rnd(100) or 0, sold = rnd(50), hours = 24, neutral = rnd(9) == 1 }
    local g, keepV, cxlV, pK, pN = X.cancelGain(o)
    local kv = floor(pK * (X.net(o.my * o.n, o.neutral) + o.dep) / 1000)
    local cv = floor(pN * (X.net(o.new * o.n, o.neutral) + o.dep2) / 1000) - o.dep2 - o.cost
    if keepV ~= kv or cxlV ~= cv or g ~= cv - kv or pK ~= X.sellPm(o.sold, o.aheadKeep, o.n, 24) then badC = badC + 1 end
  end
  check(badC == 0 and X.cancelGain({ my = 1, n = 1, sold = nil }) == nil, "cancel gain = repost value − keep value, exact; unknown without measured sales")
  -- craft spread exact; pattern only with data
  local sp = X.craftSpread({ cost = 1000, made = 2, market = 900, sold = 10, ahead = 0, vendorUnit = 50, hours = 24, ev = { ok = true, days = 5, scans = 9, hours = 40, sold = 10, sellers = 5, volPct = 5, ageH = 1 } })
  local spNet = X.flipNet(1000, 900, 2, X.deposit(50, 2, 24), X.sellPm(10, 0, 2, 24))
  check(sp and sp.net == spNet and sp.cost == 1000 and sp.fees == sp.cut + sp.depLoss, "craft spread: net exact to the copper: " .. tostring(sp and sp.net))
  check(X.craftSpread({ cost = 1000, made = 2, market = 900, sold = 10, ev = thin }) == nil, "craft spread: thin evidence → nothing")
  local pb = {}
  for wd = 0, 3 do for blk = 0, 1 do pb[wd * 6 + blk] = { g = 100, h = 800 } end end
  check(X.pattern(pb) and not X.pattern(pb).clear, "8 slots on 4 days, all equal: measured, nothing clearly better")
  pb[3 * 6 + 5] = { g = 400, h = 800 }
  local pt = X.pattern(pb)
  check(pt and pt.clear and pt.wd == 3 and pt.blk == 5 and pt.pm == floor(400 * 7200 * 1000 / (800 * 1200)), "the busy slot stands out: Wednesday 20–24 h, " .. tostring(pt and pt.pm))
  check(X.pattern({ [0] = { g = 9, h = 900 }, [7] = { g = 9, h = 900 } }) == nil, "two slots: no pattern claimed")

  -- ================================================================ §2 the engine on a fake market
  if rawget(_G, "C_AuctionHouse") == nil then return end
  local me = M.player.name
  local items = {
    { 92001, "Chiffon-test", 20, 5 }, { 92003, "Bobine-test", 20, 5 }, { 92004, "Toile-test", 20, 5 }, { 92005, "Perle-test", 1, 10 },
    { 92010, "Laine-test", 20, 10 }, { 92011, "Lin-test", 20, 10 }, { 92012, "Fil-test", 20, 10 },
  }
  for _, it in ipairs(items) do M.AddItem(it[1], { name = it[2], stack = it[3], sellPrice = it[4], type = "Artisanat", classID = 7 }) end
  A.db.autoReserve, A.db.keepGold, A.db.mountGold, A.db.hours = false, 0, 0, 24
  D.db.goal, D.db.sharePct, D.db.itemPct, D.db.minNetPa, D.db.marginPct, D.db.reset, D.db.resetCapPo = 50, 50, 20, 1, 15, true, 3
  M.player.money = 500000
  M.ah.real = true
  local aid = 8000
  local function L(id, count, unit, owner)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = owner or ("Vendeur" .. (aid % 6)), auctionID = aid }
  end
  local day = floor(GetServerTime() / 86400)
  local function history(id, med, vendor, days, gone, sellers, hrs)
    local h, sl = {}, {}
    for k = days, 1, -1 do
      for _, v in ipairs({ day - k + 1, med - 2, med, 200, 2, gone, hrs or 12 }) do h[#h + 1] = v end
      sl[#sl + 1] = day - k + 1 sl[#sl + 1] = sellers
    end
    A.RealmData().items[id] = { h = h, sl = sl, v = vendor, last = GetServerTime() }
  end
  history(92001, 100, 5, 5, 10, 2)         -- ~12 sold / day, 2 sellers: a thin cheap market
  history(92003, 100, 5, 5, 500, 2)        -- sells 600 / day but its wall costs 3 po 60 > cap 3 po
  history(92004, 100, 5, 5, 10, 2, 0)      -- never measured between two scans
  history(92005, 2000, 10, 5, 5, 2)        -- single items, ~6 sold / day
  history(92010, 500, 10, 5, 20, 6)        -- my wool: 24 / day, 6 sellers
  history(92011, 300, 10, 5, 200, 6)       -- my linen: sells 240 / day (being 2nd costs little)
  history(92012, 1000, 10, 5, 20, 6)       -- my thread: undercut by an absurdly low price
  M.ah.list = {
    L(92001, 10, 40, "VendeurA"), L(92001, 15, 45, "VendeurB"), L(92001, 20, 98, "VendeurA"), L(92001, 20, 99, me),
    L(92003, 300, 60, "VendeurA"), L(92003, 300, 61, "VendeurB"), L(92003, 50, 98, "VendeurA"),
    L(92004, 10, 40, "VendeurA"), L(92004, 20, 98, "VendeurB"),
    L(92005, 1, 800, "VendeurC"), L(92005, 1, 900, "VendeurC"), L(92005, 1, 1000, "VendeurC"), L(92005, 1, 1900, "VendeurD"),
    L(92010, 20, 450, "VendeurE"), L(92010, 20, 495, me), L(92010, 20, 520, "VendeurF"),
    L(92011, 20, 299, "VendeurE"), L(92011, 20, 300, me),
    L(92012, 20, 100, "VendeurE"), L(92012, 20, 990, me),
  }
  M.ah.owned = {}
  for _, a in ipairs(M.ah.list) do if a.owner == me then M.ah.owned[#M.ah.owned + 1] = a end end
  -- what TreeAI posted earlier (its real deposits, kept in the per-character ledger)
  D.char.posts[92010] = { { n = 20, u = 495, dep = 60, t = GetServerTime() - 3600 } }
  D.char.posts[92011] = { { n = 20, u = 300, dep = 60, t = GetServerTime() - 3600 } }
  for b = 0, 4 do for s = 1, 16 do M.SetBagItem(b, s, nil) end end
  M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED")
  local bids0, viol0, chat0 = #M.ah.bids, #M.violations, #M.output

  M.SetAuctionHouse(true)
  M.Advance(0.5, 0.05)
  M.serverTime = M.serverTime + 16 * 60
  D.frame.scan:Click()
  M.Advance(4, 0.05)
  check(A.lastScan and A.lastScan.full and D.built, "full scan read, queues built")
  local by = {}
  for _, d in ipairs(D.all) do by[d.id] = by[d.id] or {} table.insert(by[d.id], d) end
  local c1 = by[92001] and by[92001][1]
  check(c1 and #by[92001] == 1 and c1.class == "regul" and c1.qty == 25 and c1.buy == 1075 and c1.sellUnit == 97 and c1.sellers == 2,
    "thin cheap wall (2 sellers): buy all 25 units for 10 pa 75, relist at 97 pc: " .. tostring(c1 and c1.class))
  if c1 then
    -- my own 20 cloth on sale are sold first (ahead = 20)
    local net = X.flipNet(1075, 97, 25, X.deposit(5, 25, 24, false, D.db.depPm and D.db.depPm[24]), X.sellPm(c1.ev.sold, 20, 25, 24))
    check(c1.net == net and c1.groupNet == net and c1.worst == 5 * 25 - 1075 and c1.days == X.days(c1.ev.sold, 20, 25),
      "réguler: net exact, worst case (vendor) stated: " .. c1.net)
    io.write("VOIT (réguler): " .. D.DealText(c1), "\n")
    check(D.DealText(c1):find("Risque : ", 1, true) and D.DealText(c1):find("au pire, revendu au marchand : perte 9 pa 50 pc", 1, true), "the risk in one line")
  end
  check(by[92003] == nil, "wall dearer than the cap: nothing")
  check(by[92004] == nil, "sales never measured: nothing")
  local p5 = by[92005] or {}
  check(#p5 == 3 and p5[1].group == p5[3].group and p5[1].groupBuy == 2700, "wall of 3 single items: one group of 3 purchases (27 pa)")
  local pos = {}
  for i, d in ipairs(D.buyQ) do if d.id == 92005 then pos[#pos + 1] = i end end
  check(#pos == 3 and pos[3] - pos[1] == 2, "the group's purchases follow each other in the queue")

  -- press the key: buy everything, never my own auction
  local presses = 0
  while D.BuyHead() and presses < 30 do
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
  check(bought[92001] == 25 and bought[92005] == 3 and not bought[92003] and not bought[92004], ("bought: wall of 25 cloth, 3 pearls (%s / %s)"):format(tostring(bought[92001]), tostring(bought[92005])))
  check(#M.violations == viol0, "no protected call outside a click, none while the AH is busy: " .. (#M.violations - viol0))

  -- ================================================================ §3 cancel / repost
  M.Advance(2, 0.1)
  local cx = {}
  for _, e in ipairs(D.cxlQ) do cx[e.id] = e end
  local w = cx[92010]
  check(w and w.low == 450 and w.new == 449 and w.dep == 60 and w.depExact, "wool undercut at 4 pa 50: cancel, repost at 4 pa 49 (deposit 60 pc lost, known)")
  if w then
    local g = X.cancelGain({ my = 495, n = 20, dep = 60, aheadKeep = 20, new = 449, aheadNew = 0, dep2 = w.dep2, cost = 0, sold = w.gain and 24 or 24, hours = 24 })
    check(w.gain == g and w.gain >= X.cancelNeed(449, 20, false), "cancel gain exact: " .. tostring(w.gain))
    io.write("VOIT (annuler): " .. D.CancelText(w), "\n")
  end
  check(cx[92011] == nil, "linen 1 pc under, sells 240 / day: kept (cancelling costs more than waiting)")
  check(cx[92012] == nil, "thread undercut by an absurd price: kept (wait)")
  check(cx[92001] == nil, "my cloth above a wall I just bought: not in the cancel queue")
  local L3 = D.Lines()
  io.write("VOIT (panneau): " .. L3.buy .. " | " .. L3.post .. " | " .. L3.cancel .. " | " .. L3.deal, "\n")
  check(L3.cancel == "Annuler (1)" and L3.deal:find("^Annuler Laine%-test ×20"), "panel: « Annuler (1) », the next action in plain French")
  local today0 = D.Realized().today
  local c0 = #M.ah.cancels
  M.Hw(T.AuctionNext)
  check(#M.ah.cancels == c0 + 1 and M.ah.cancels[#M.ah.cancels].auctionID == w.auctionID, "one press = one CancelAuction")
  M.Hw(T.AuctionNext)                              -- hammered at once: the game is busy, nothing protected
  check(#M.violations == viol0, "a press while the AH message system is busy: no call")
  M.Advance(1, 0.05)
  check(D.sess.cancels == 1 and D.Realized().today == today0 - 60 and not D.char.posts[92010], "cancel confirmed: deposit 60 pc counted as lost, once")
  M.AddMail("Vente aux enchères annulée : Laine-test (20)", 0, nil, { id = 92010, count = 20 })
  M.FireEvent("MAIL_INBOX_UPDATE")
  M.Advance(1.5, 0.05)
  check(D.Realized().today == today0 - 60 and next(D.char.cxl) == nil, "the « annulée » mail is not counted a second time")
  -- the wool is back in the bags: the post queue relists it 1 pc under the cheapest
  M.SetBagItem(0, 1, 92010, 20)
  M.FireEvent("BAG_UPDATE", 0) M.FireEvent("BAG_UPDATE_DELAYED")
  M.Advance(2, 0.05)
  local wool
  for _, e in ipairs(D.postQ) do if e.id == 92010 then wool = e end end
  local B = T.Bags
  if B and B._active then
    check(wool and wool.unit == 449 and wool.unit >= wool.floor, "the cancelled wool is reposted at 4 pa 49: " .. tostring(wool and wool.unit))
  end

  -- ================================================================ §4 crafting spreads (his profession)
  local P = T.modules and T.modules.Prof
  if P and T.ProfLevel then
    M.AddItem(1251, { name = "Bandage-test", stack = 20, sellPrice = 10, type = "Consommable", classID = 0 })
    M.AddItem(2589, { name = "Lin-brut-test", stack = 20, sellPrice = 1, type = "Artisanat", classID = 7 })
    history(1251, 300, 10, 5, 10, 6)
    A.cur[2589] = { listings = { { unit = 12, count = 20, buy = 240, owner = "VendeurE" } }, units = { 12 }, qty = 20, t = GetServerTime() }
    local old = P.profs
    P.profs = { { line = 129, name = "Secourisme", rank = 80, max = 150 } }
    D._craft = nil
    local list = D.CraftSpreads(5)
    local band
    for _, s in ipairs(list) do if s.id == 1251 then band = s end end
    check(band and band.cost == 12 and band.mats[1].src == "HV", "craft: linen bandage, linen bought now at 12 pc")
    if band then
      check(band.net == X.flipNet(12, band.sellUnit, 1, band.dep, band.pm) and band.class == "probable", "craft spread exact: +" .. band.net)
      io.write("VOIT (métier): " .. (D.CraftText(band)), "\n")
    end
    P.profs = old
  end

  -- ================================================================ §5 the HV page: one dashboard
  local rec = {}
  local Ly = {}
  function Ly.tiles(list) for _, it in ipairs(list) do rec[#rec + 1] = "tile:" .. it[1] .. " / " .. tostring(it[2]) end end
  function Ly.header(t) rec[#rec + 1] = "header:" .. t return true end
  function Ly.row(e) rec[#rec + 1] = "row:" .. e.text end
  function Ly.text(t) rec[#rec + 1] = "text:" .. t end
  function Ly.empty(t) rec[#rec + 1] = "empty:" .. t end
  function Ly.buttons(l) for _, b in ipairs(l) do rec[#rec + 1] = "button:" .. b[1] end end
  function Ly.gap() end
  D.view = "buy"
  A.RenderPage(Ly, T.Window)
  local tx = table.concat(rec, " | ")
  io.write("VOIT (page HV): " .. tx:sub(1, 900), "\n")
  check(rec[1] == "header:Achat-revente : ton objectif du jour" and tx:find("tile:Acheter (", 1, true) and tx:find("tile:Poster (", 1, true)
    and tx:find("tile:Annuler (", 1, true), "HV page: goal first, then the three queues with their counts")
  check(not tx:find("Vendre tes sacs", 1, true) and not tx:find("Acheter-revendre", 1, true) and not tx:find("gagné cette semaine", 1, true),
    "the superseded old tiles are gone")
  check(tx:find("header:Résultat par objet", 1, true) and tx:find("Moments de vente", 1, true), "per-item result and sale-time line")

  M.SetAuctionHouse(false)
  M.Advance(2, 0.1)
  check(D.cb == nil and D.pb == nil and D.build == nil and D.pend == nil and #D.cxlQ == 0, "AH closed: nothing running")
  local chat = 0
  for i = chat0 + 1, #M.output do if tostring(M.output[i]):find("HV", 1, true) or tostring(M.output[i]):find("Annul", 1, true) then chat = chat + 1 end end
  check(chat == 0, "nothing in chat")
  local dg = TreeAIDB and TreeAIDB.diag and TreeAIDB.diag.ah or {}
  check((dg.auctionCanceled or 0) >= 1 and (dg.sellerNamesKnown or 0) >= 1, "diag.ah: the game's cancel answer and the sellers' names in searches are counted")
  -- sale-time pattern: two full scans 3 h apart → one slot (weekday × 4 h) of measured sales
  M.serverTime = M.serverTime + 3 * 3600
  M.SetAuctionHouse(true)
  M.Advance(0.5, 0.05)
  D.frame.scan:Click()
  M.Advance(4, 0.05)
  local pat, nk = A.RealmData().pat or {}, 0
  for _, e in pairs(pat) do nk = nk + 1 check(e.h >= 300 and e.h <= 302 and e.g >= 0, "slot: 3 h measured (" .. e.h .. ")") end
  check(nk == 1, "one weekday × 4 h slot recorded after two full scans 3 h apart")
  M.SetAuctionHouse(false)
  M.Advance(1, 0.1)
end
