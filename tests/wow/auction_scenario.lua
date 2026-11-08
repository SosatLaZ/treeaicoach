-- Scenario for the Auction module, run by tests/wow/test_auction.py on both API flavours
-- (legacy = classic AH API, modern = C_AuctionHouse). Fake names only. The mock records a violation
-- for any purchase / cancel outside a click, with the AH closed, or more than one per click.
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  ctx.check(A, "Auction module registered")
  if not A then return end
  local F = A.F
  local modern = M.flavor == "modern"

  -- exact math (same numbers as tests/wow/test_auction.py)
  ctx.check(F.cut(10000) == 500 and F.cut(10000, true) == 1500 and F.cut(199, true) == 29, "AH cut 5 % / 15 %")
  ctx.check(F.deposit(100, 5, 24) == 300 and F.deposit(100, 5, 24, true) == 1500 and F.deposit(100, 5, 2) == 25, "deposit")
  ctx.check(F.profit(10000, 20000, 300, 0.6) == 8880, "profit 8880: " .. tostring(F.profit(10000, 20000, 300, 0.6)))
  ctx.check(F.breakEven(10000, 300, 0.6) == 10652 and F.breakEven(10000) == 10526, "break-even")
  ctx.check(F.median({ 1, 2, 3, 10 }) == 2 and F.lowMedian({ 100, 90, 500, 95, 99, 101, 300 }) == 99, "medians")
  local f = F.flip(6000, 1, { market = 10000, vol = 0.1, volume = 6, conf = 0.8 }, 500, 4, false, 24)
  ctx.check(f.profit == 3010 and f.adj == 2408 and f.score == 4013.33 and f.days == 0.83, "flip numbers: " .. f.profit .. " " .. f.score)
  local sp = F.suggestPost(5, 20, { market = 1000, vol = 0.1, volume = 10 }, { { 950, 5 }, { 980, 5 }, { 1200, 10 } }, false, 24)
  ctx.check(sp.unit == 949 and sp.deposit == 60 and sp.net == 4505 and sp.better == "ah", "suggest post: " .. tostring(sp.unit) .. " " .. tostring(sp.net))
  local st = F.statsFromPoints({ { day = 1, min = 90, med = 100, qty = 5, n = 2, gone = 6, hrs = 12 }, { day = 2, min = 90, med = 100, qty = 5, n = 2, gone = 4, hrs = 12 } }, 0)
  ctx.check(st.volume == 6 and st.market == 100, "daily volume 6")
  local rz = F.realized({ { type = "buy", item = "Tissu", qty = 10, money = 1000 },
    { type = "sell", item = "Tissu", qty = 4, money = 800, cut = 40, day = "d1" },
    { type = "sell", item = "Tissu", qty = 10, money = 2000, cut = 100, day = "d2" } })
  ctx.check(rz.total == 1660 and rz.days.d1 == 360, "realized 1660")

  -- items
  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat" })
  M.AddItem(2592, { name = "Étoffe de laine", stack = 20, sellPrice = 33, type = "Artisanat" })
  M.AddItem(9990, { name = "Griffe d'ours", stack = 1, sellPrice = 500, type = "Divers" })
  local me = M.player.name
  local aid = 100
  local function L(id, count, unit, owner)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = owner or "Vendeur" .. (aid % 7), auctionID = aid }
  end
  local function market(extra)
    local l = {}
    for k = 1, 8 do l[#l + 1] = L(2589, 20, 100 + k) end
    for k = 1, 6 do l[#l + 1] = L(2592, 20, 300 + 2 * k) end
    for _, x in ipairs(extra or {}) do l[#l + 1] = x end
    return l
  end
  local function fullScan()
    ctx.check(A.win and A.win.scanBtn, "scan button")
    A.win.scanBtn:Click()
    M.Advance(1, 0.01)
  end

  M.SetAuctionHouse(true)
  ctx.check(A.ahOpen and A.win and A.win:IsShown(), "window shown at AH open")
  -- 4 days of history, two scans a day (some listings vanish = sales)
  for day = 1, 4 do
    M.ah.list = market()
    fullScan()
    M.serverTime = M.serverTime + 6 * 3600
    for _ = 1, 4 do table.remove(M.ah.list, 1) end  -- 80 lin sold in 6 h (a liquid market)
    fullScan()
    M.serverTime = M.serverTime + 18 * 3600
  end
  ctx.check(#M.ah.queries >= 8, "scans sent from clicks: " .. #M.ah.queries)
  local s = A.Stats(2589)
  ctx.check(s and s.market >= 101 and s.market <= 106, "market value of lin: " .. tostring(s and s.market))
  ctx.check(s and s.volume and s.volume > 0, "daily volume estimated: " .. tostring(s and s.volume))
  ctx.check(s and s.conf >= 0.5, "confidence: " .. tostring(s and s.conf))
  local rd = A.RealmData()
  ctx.check(#rd.items[2589].h <= 14 * 7 and #rd.items[2589].h % 7 == 0, "compact history")

  -- today: a cheap lin stack, a vendor flip, my own cheap listing (never a deal)
  A.db.minProfit = 1
  M.ah.list = market({ L(2589, 20, 50), L(9990, 1, 300), L(2592, 20, 100, me) })
  fullScan()
  local vend, lin, own
  for _, d in ipairs(A.deals) do
    if d.kind == "vendor" and d.id == 9990 then vend = d end
    if d.kind == "flip" and d.id == 2589 then lin = d end
    if d.id == 2592 and d.unit == 100 then own = d end
  end
  ctx.check(vend and vend.profit == 200, "vendor flip +2 pa: " .. tostring(vend and vend.profit))
  ctx.check(lin and lin.buy == 1000 and lin.discount >= 0.2 and lin.profit > 0, "lin deal below market")
  ctx.check(not own, "my own listing is not a deal")
  ctx.check(#A.thinList == 0, "no thin market flagged for cheap items")

  local function rowFor(entry)
    for _, r in ipairs(A.win.rows) do if r.entry == entry then return r end end
  end
  A.tab, A.page = "deals", 1
  A.Refresh()
  local r = rowFor(lin)
  ctx.check(r and r.btn:IsShown(), "deal row with a button")
  local nb = #M.ah.bids
  if modern then
    r.btn:Click()                    -- 1: search (price re-check)
    ctx.check(lin.state == "ready" and lin.commodity, "modern: verified commodity: " .. tostring(lin.state))
    r.btn:Click()                    -- 2: start purchase
    ctx.check(lin.state == "confirm", "modern: price confirmed: " .. tostring(lin.state))
    r.btn:Click()                    -- 3: confirm
  else
    r.btn:Click()
  end
  ctx.check(#M.ah.bids == nb + 1 and lin.state == "bought", "exactly one purchase")
  r.btn:Click()
  ctx.check(#M.ah.bids == nb + 1, "bought deal cannot be bought twice")
  ctx.check(not modern and M.ah.bids[#M.ah.bids].amount == 1000 or modern, "classic bid = buyout")
  -- price changed before the click: no purchase
  local rv = rowFor(vend)
  for _, a in ipairs(M.ah.list) do if a.id == 9990 then a.buyout = 900 end end
  nb = #M.ah.bids
  M.Advance(0.2, 0.05)             -- the AH search throttle frees up (real clicks are not instantaneous)
  rv.btn:Click()
  if modern then ctx.check(vend.state == "gone", "modern: price changed → gone") end
  ctx.check(#M.ah.bids == nb and vend.state == "gone", "changed listing not bought")

  -- budget
  A.db.budget = 1
  A.FindDeals()
  for _, d in ipairs(A.deals) do ctx.check(d.buy <= 10000, "deal within budget") end
  A.db.budget = 0

  -- my auctions undercut → cancel (one click)
  do   -- a really beaten lin: 300 cheaper units ahead (the 50/60 pc bargain is gone), cancelling pays
    local rows = {}
    for k = 1, 15 do rows[k] = { count = 20, buy = (106 + k) * 20, owner = "Vendeur" .. k } end
    A.ApplyItemListings(2589, rows)
  end
  M.ah.owned = { { id = 2589, count = 20, buyout = 3000, owner = me, auctionID = 900 } }
  A.CheckOwned()
  ctx.check(A.owned[1] and A.owned[1].undercut and A.owned[1].repost, "undercut detected: " .. tostring(A.owned[1] and A.owned[1].advice))
  A.tab = "owned" A.Refresh()
  local ro = rowFor(A.owned[1])
  ro.btn:Click()
  ctx.check(#M.ah.cancels == 1 and #M.ah.owned == 0, "one cancel")

  -- AH closed: buttons do nothing protected
  M.SetAuctionHouse(false)
  A.tab = "deals" A.Refresh()
  for _, row in ipairs(A.win.rows) do row.btn:Click() end
  A.Buy(A.deals[1])

  -- ledger from AH mails (FR subjects), deduplicated
  M.AddMail("Objet remporté : Étoffe de lin", 0, { type = "buyer", item = "Étoffe de lin", bid = 1000 }, { id = 2589, count = 20 })
  M.AddMail("Vente aux enchères réussie : Étoffe de lin (20)", 1900 + 60, { type = "seller", item = "Étoffe de lin", bid = 2000, cut = 100, deposit = 60 })
  M.AddMail("Auction expired: Griffe d'ours", 0)
  M.FireEvent("MAIL_INBOX_UPDATE")
  M.FireEvent("MAIL_INBOX_UPDATE")
  ctx.check(#A.char.ledger == 3, "3 ledger entries (dedup): " .. #A.char.ledger)
  local kind, name, q = A.MailKind("Auction successful: Copper Ore (5)")
  ctx.check(kind == "sell" and name == "Copper Ore" and q == 5, "EN subject parsed")
  local rr = A.Realized()
  ctx.check(rr.today == 900 and rr.total == 900, "realized profit 9 pa: " .. tostring(rr.today))

  -- selling assistant
  M.SetBagItem(2, 1, 2592, 20)
  local sell = A.SellAdvice()
  local wool
  for _, x in ipairs(sell) do if x.id == 2592 then wool = x end end
  ctx.check(wool and wool.unit and wool.unit >= 300 and wool.better == "ah", "post price for wool: " .. tostring(wool and wool.unit))
  ctx.check(wool and wool.deposit == 33 * 20 * 5 * 24 / 200, "wool deposit 396")
  ctx.check(A.SellText(wool):find("dépôt"), "sell text")

  -- « Suivant » posts the wool from the bags: one protected call per click, both API flavours
  M.SetAuctionHouse(true)
  A.BuildQueue()
  local np, clicks = #M.ah.posts, 0
  while A.QueueHead() and clicks < 10 do M.Advance(0.2, 0.05) A.win.nextBtn:Click() clicks = clicks + 1 end
  ctx.check(#M.ah.posts == np + 1 and M.ah.posts[#M.ah.posts].id == 2592, "wool posted via Suivant: " .. (#M.ah.posts - np))
  M.SetAuctionHouse(false)

  -- summary for TreeAI's AI
  T:Fire("SUMMARY")
  local sum = T:Summary().auction
  ctx.check(sum and sum.realized and sum.realized.today == 900, "summary.auction.realized")
  ctx.check(sum and sum.deals and sum.deals[1] and sum.deals[1].name, "summary.auction.deals")
  ctx.check(sum and sum.sell and sum.sell[1], "summary.auction.sell")

  -- slash
  for _, sub in ipairs({ "", "vendre", "journal", "neutre", "auto", "aide" }) do M.RunSlash("/tai hv " .. sub) end
  ctx.check(ctx.out("Profit réalisé"), "journal printed")
end
