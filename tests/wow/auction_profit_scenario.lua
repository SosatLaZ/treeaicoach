-- 4.1 AHPROFIT (run by tests/wow/test_auction.py on modern / legacy / forever). The player: « revois tout pour l'HV
-- pour faire vla le bénef ». Honest profit engine + one-click-one-action panel + the window's « HV » page.
-- Checks: formulas = auction.py; AH closed → the page says what to do; flips only after scans over days prove sales;
-- thresholds (net ≥ max(1 pa, 15 %)); buys only at ≤ the planned price after a re-check; one protected call per
-- click (strict mock); nothing in combat; nothing in chat; vendor → AH, crafts, bags. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  local W = T and T.Window
  ctx.check(A and A.F and A.RenderPage, "Auction module with the 4.1 page")
  if not A then return end
  local F, check = A.F, ctx.check
  local modern = M.flavor ~= "legacy"

  -- ---------------------------------------------------------------- formulas = treeaicoach/wow/auction.py
  check(F.minNet(0) == 100 and F.minNet(1000) == 150 and F.minNet(1001) == 151 and F.minNet(1000, 500) == 500, "minNet = py")
  check(F.undercut(1000) == 999 and F.undercut(1000, 5) == 950 and F.undercut(10, 5) == 9, "undercut = py")
  check(F.postQty(20, 3, 24) == 3 and F.postQty(20, 3, 48) == 6 and F.postQty(20, 3, 24, 2) == 1 and F.postQty(20, 3, 24, 5) == 0
    and F.postQty(20, nil) == 20 and F.postQty(20, 48, 24, 0, 5) == 5, "postQty = py")
  local ds = {}
  for i, m in ipairs({ 100, 102, 98, 101, 100 }) do ds[i] = { day = 100 + i, min = m - 2, med = m, qty = 60, n = 2, gone = 40, hrs = 12, sel = 6 } end
  local liq = F.liquidity(ds, 20, 1, 0.10)
  local v = F.vendorToAH(40, liq, 10, false, 24, 0)
  check(v and v.count == 144 and v.sellUnit == 99 and v.deposit == 864 and v.profit == 7208, "vendorToAH = py: " .. tostring(v and v.profit))
  check(F.vendorToAH(95, liq, 10, false, 24, 0) == nil and F.vendorToAH(40, { ok = false }) == nil, "vendorToAH refusals = py")
  local c3 = F.craft(100, 3, liq, 10, false, 24)
  check(c3 and c3.profit == 182 and c3.deposit == 18 and F.craft(50, 1, liq, 10, false, 24) == nil, "craft = py")
  local dead = F.suggestPost(20, 33, { market = 306, vol = 0.05, volume = 0 }, { { 306, 20 } }, false, 24)
  check(dead.ev == 541 and dead.better == "vendor" and dead.net == 5419, "never-selling wool: vendor (ev = py): " .. tostring(dead.ev))
  check(F.statsFromPoints({ { day = 1, min = 90, med = 100, qty = 5, n = 2, gone = 0, hrs = 0.5 } }, 0).volume == nil,
    "30 min of scans: volume unknown, not « 0 / day »")

  -- ---------------------------------------------------------------- the world (fake items)
  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat", classID = 7 })
  M.AddItem(93020, { name = "Pierre-test", stack = 20, sellPrice = 5, type = "Artisanat", classID = 7 })
  M.AddItem(93030, { name = "Fil-test", stack = 20, sellPrice = 2, type = "Artisanat", classID = 7 })
  M.AddItem(1251, { name = "Bandage-test", stack = 20, sellPrice = 10, type = "Consommable", classID = 0 })
  A.db.autoReserve, A.db.keepGold, A.db.mountGold, A.db.minProfit, A.db.minMargin = false, 0, 0, 1, 15
  local aid = 9000
  local function L(id, count, unit)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = "Vendeur" .. (aid % 6), auctionID = aid }
  end
  local function market(extra)
    local l = {}
    for k = 1, 8 do l[#l + 1] = L(2589, 20, 100 + k) end
    for k = 1, 4 do l[#l + 1] = L(93020, 20, 500 + k) end            -- stone: listed every day, never sells
    for _, x in ipairs(extra or {}) do l[#l + 1] = x end
    return l
  end
  local out0 = #M.output
  local function texts()
    local t = {}
    for _, r in ipairs((W and W.lastLay and W.lastLay.rects) or {}) do t[#t + 1] = r.kind .. ":" .. tostring(r.label) end
    return table.concat(t, " | ")
  end

  -- ---------------------------------------------------------------- AH closed: the window page says what to do
  if W and W.Open then
    A.view = "flips"                                                  -- (the harness clicks every button at load)
    W.Open("hv")
    -- every word the page draws (a recording layout: tiles keep their sub-line too)
    local rec = {}
    local Ly = { y = 0 }
    function Ly.tiles(list) for _, it in ipairs(list) do rec[#rec + 1] = "tile:" .. it[1] .. " / " .. tostring(it[2]) end end
    function Ly.header(t) rec[#rec + 1] = "header:" .. t return true end
    function Ly.row(e) rec[#rec + 1] = "row:" .. e.text end
    function Ly.text(t) rec[#rec + 1] = "text:" .. t end
    function Ly.empty(t) rec[#rec + 1] = "empty:" .. t end
    function Ly.buttons(l) for _, b in ipairs(l) do rec[#rec + 1] = "button:" .. b[1] end end
    function Ly.gap() end
    A.RenderPage(Ly, W)
    local tx = table.concat(rec, " | ")
    check(tx:find("bénéfice possible maintenant", 1, true) and tx:find("en vente à l'HV", 1, true) and tx:find("gagné cette semaine", 1, true),
      "page: the 3 numbers on top: " .. tx)
    check(tx:find("tile:Scanner", 1, true) and tx:find("tile:Acheter-revendre", 1, true) and tx:find("tile:Vendre mes sacs", 1, true)
      and tx:find("tile:Mes métiers", 1, true), "page: the 4 big buttons: " .. texts())
    check(tx:find("Ouvre l'HV et clique Scanner", 1, true), "empty state says what to do: " .. tx)
    local q0 = #M.ah.queries
    M.Hw(A.ScanSmart, W)
    check(#M.ah.queries == q0 and A.Said("Ouvre l'HV et clique Scanner"), "Scanner with the AH closed: no query, a hint")
    W.Close()
  end
  M.Hw(T.AuctionNext)
  check(#M.ah.bids == 0, "AH closed: the key does nothing protected")

  -- ---------------------------------------------------------------- scans over days: evidence before any flip
  M.SetAuctionHouse(true)
  local function fullScan()
    M.serverTime = M.serverTime + 16 * 60                            -- past Blizzard's 15-min account throttle
    M.Hw(function() A.win.scanBtn:Click() end)
    M.Advance(1.5, 0.02)
  end
  local function hasDeal(id, unit)
    for _, d in ipairs(A.deals) do if d.id == id and math.floor(d.buy / d.count) == unit then return d end end
  end
  M.ah.list = market()
  fullScan()
  M.serverTime = M.serverTime + 6 * 3600
  for _ = 1, 4 do table.remove(M.ah.list, 1) end                     -- 80 lin sold in 6 h
  M.ah.list[#M.ah.list + 1] = L(2589, 20, 60)                         -- a bargain… on day 1
  fullScan()
  check(not hasDeal(2589, 60), "day 1: a cheap lin is NOT a flip yet (no proof it sells)")
  check(A.none and A.none.text == F.NONE_TEXT, "day 1: « Aucun achat-revente sûr pour l'instant »")
  for _ = 2, 3 do
    M.serverTime = M.serverTime + 12 * 3600
    M.ah.list = market()
    fullScan()
    M.serverTime = M.serverTime + 6 * 3600
    for _ = 1, 4 do table.remove(M.ah.list, 1) end
    fullScan()
  end
  -- day 4: bargains (lin 60/62/64 = real flips, lin 88 = too thin a margin, stone −40 % but it never sells)
  M.serverTime = M.serverTime + 12 * 3600
  M.ah.list = market({ L(2589, 20, 60), L(2589, 20, 62), L(2589, 20, 64), L(2589, 20, 88), L(93020, 20, 300) })
  fullScan()
  local d60 = hasDeal(2589, 60)
  check(d60 and d60.profit >= F.minNet(d60.buy) and d60.safe, "day 4: lin at 60 pc/u is a flip (net ≥ max(1 pa, 15 %))")
  check(not hasDeal(2589, 88), "lin at 88 pc/u rejected (margin)")
  check(not hasDeal(93020, 300), "stone at −40 % rejected: never sells")
  check((A.rejects.slow or 0) + (A.rejects.unmeasured or 0) >= 1, "why: « se vend trop peu » counted")
  -- demand cap: units planned per item ≤ observed demand
  local planned = 0
  for _, d in ipairs(A.plan) do if d.id == 2589 then planned = planned + d.count end end
  check(planned > 0 and planned <= (d60.maxQty or 0), ("plan ≤ demand: %d ≤ %s"):format(planned, tostring(d60.maxQty)))
  local groups = A.BuyGroups()
  check(groups[1] and groups[1].id == 2589 and groups[1].maxUnit >= 60, "« Acheter pour revendre » grouped by item")

  -- ---------------------------------------------------------------- nothing in combat
  M.EnterCombat()
  local nb = #M.ah.bids
  M.Hw(T.AuctionNext)
  A.win.buyBtn:Click()
  check(#M.ah.bids == nb and A.Said("En combat"), "in combat: nothing bought")
  M.LeaveCombat()

  -- ---------------------------------------------------------------- re-check: the price went up → no purchase
  A.Plan() A.BuildQueue()
  local head = A.BuyHead()
  check(head and head.id == 2589 and math.floor(head.buy / head.count) == 60, "first planned buy: lin at 60")
  for _, x in ipairs(M.ah.list) do                                   -- raised since the scan (95 pc/u)
    if x.id == 2589 and x.count == head.count and x.buyout == head.buy then x.buyout = 95 * 20 break end
  end
  nb = #M.ah.bids
  for _ = 1, 3 do
    if head.state == "gone" or head.state == "bought" then break end
    A.win.buyBtn:Click()
    M.Advance(0.4, 0.05)
  end
  check(#M.ah.bids == nb and head.state == "gone", "price above the max after the re-check: not bought")
  -- next planned listing (62): bought, at ≤ its planned price, one protected call per click
  local nxt = A.BuyHead()
  local clicks = 0
  while nxt and nxt.state ~= "bought" and nxt.state ~= "gone" and clicks < 5 do
    A.win.buyBtn:Click()
    clicks = clicks + 1
    M.Advance(0.4, 0.05)
  end
  local bid = M.ah.bids[#M.ah.bids]
  check(#M.ah.bids == nb + 1 and bid.id == 2589 and (bid.commodity or bid.amount <= nxt.buy), "next listing bought at ≤ its max price")
  check(clicks >= 1 and (not modern or clicks <= 3), "clicks: " .. clicks)
  check(A.SessionText():find("^Session : 1 achat"), "session line: " .. A.SessionText())
  check(A.win.sess:GetText():find("Session", 1, true) and A.win.buySub:GetText() ~= "", "panel shows the session and the next buy")

  -- ---------------------------------------------------------------- posting: stack sized for demand, undercut rule
  A.db.undercutPct = 2
  M.SetBagItem(2, 1, 2589, 20)
  A.postRows = nil
  local p = A.PostHead()
  local low = 9999
  for _, l in ipairs(A.cur[2589].listings) do low = math.min(low, l.unit) end
  check(p and p.id == 2589 and p.count <= 20 and p.better == "ah", "a lin post queued: " .. tostring(p and p.count))
  check(p and (p.unit == F.undercut(low, 2) or p.reason:find("plancher")), "undercut 2 % (or the floor): " .. tostring(p and p.unit))
  A.db.undercutPct = 0
  local bags = A.BagAdvice()
  check(bags[1] and bags[1].id == 2589 and bags[1].gain > 0, "« Vendre mes sacs »: lin better at the AH than at the vendor")

  -- ---------------------------------------------------------------- the key: one press = one action, never a loop
  local np = #M.ah.posts
  A.raf.on = false
  local presses = 0
  while presses < 12 and A.RafaleHead() do
    M.Hw(T.AuctionNext)
    presses = presses + 1
    M.Advance(0.6, 0.05)
  end
  local acts = (#M.ah.bids - nb - 1) + (#M.ah.posts - np)
  check(acts >= 1 and acts <= presses, ("key presses: %d actions for %d presses"):format(acts, presses))

  -- ---------------------------------------------------------------- vendor → AH, crafts (his professions)
  local r = A.RealmData()
  local day = math.floor(M.serverTime / 86400)
  local function hist(id, meds)
    local h, sl = {}, {}
    for i, m in ipairs(meds) do
      for _, x in ipairs({ day - 6 + i, m - 2, m, 100, 3, 40, 12 }) do h[#h + 1] = x end
      sl[#sl + 1] = day - 6 + i sl[#sl + 1] = 6
    end
    r.items[id] = { h = h, sl = sl, last = M.serverTime }
  end
  hist(93030, { 60, 61, 59, 60, 60 })
  hist(1251, { 400, 405, 398, 402, 400 })
  M.merchantItems = { { id = 93030, price = 10, stack = 1, avail = 5 } }
  M.SetMerchant(true)
  M.SetMerchant(false)
  local vf = A.VendorFlips(5)
  local fil
  for _, d in ipairs(vf) do if d.id == 93030 then fil = d end end
  check(fil and fil.buyUnit == 10 and fil.lim and fil.profit >= F.minNet(fil.buy), "vendor → AH: Fil-test (limited supply)")
  local P = T.modules and T.modules.Prof
  if P and T.ProfLevel then
    local old = P.profs
    P.profs = { { line = 129, name = "Secourisme", rank = 80, max = 150 } }
    local list = A.CraftProfits(5)
    local band
    for _, d in ipairs(list) do if d.id == 1251 then band = d end end
    check(band and band.cost > 0 and band.profit >= F.minNet(band.cost), "craft: Bandage-test made with lin sells with profit")
    if W and W.Open then
      W.Open("hv")
      A.view = "craft"
      W.Render()
      check(texts():find("Ce que tu fabriques et vends avec bénéfice", 1, true) and texts():find("Bandage", 1, true), "page: « Mes métiers » list")
      A.view = "bags"
      W.Render()
      check(texts():find("Vendre à l'HV plutôt qu'au marchand", 1, true), "page: « Vendre mes sacs » list")
      A.view = "flips"
      W.Render()
      check(texts():find("Acheter au marchand, revendre à l'HV", 1, true), "page: vendor → AH section: " .. texts())
      W.Close()
    end
    P.profs = old
  end

  -- ---------------------------------------------------------------- nothing in chat, zero blocked actions
  M.SetAuctionHouse(false)
  for i = out0 + 1, #M.output do
    check(not M.output[i]:find("Hôtel des ventes", 1, true), "nothing in chat from the AH: " .. M.output[i])
  end
  for _, e in ipairs(M.output) do check(not e:find("ADDON_ACTION_BLOCKED", 1, true), "no blocked action") end
end
