-- WoW Forever auction-house path (C_AuctionHouse, "modern" mock flavour), run by tests/wow/test_auction.py.
-- Covers: runtime API detection, async/throttled ReplicateItems, rows without item info, reading the full
-- scan another addon (Auctionator) just made, sliced processing of a big scan, targeted mini-scan under
-- the search throttle, passive browse, Auctionator API v1 fallback, gold manager + « Suivant » queue
-- (one protected action per click, posts skipped when the price moved), /tai hv debug. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  -- (5.1 AH) this scenario covers the market-data layer and the 4.1 panel; the buy-and-resell desk that replaces that
  -- panel at the AH has its own scenario (ah5_scenario.lua) and is switched off here.
  if T and T.AHDesk and T.SetModuleEnabled then T:SetModuleEnabled("AHDesk", false) end
  ctx.check(A and M.flavor ~= "legacy", "Auction module on the modern (Forever) mock")   -- FIDELITY: « forever » too
  if not A then return end
  ctx.check(A.Flavor() == "modern", "C_AuctionHouse detected: " .. tostring(A.Flavor()))

  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat", classID = 7 })
  M.AddItem(2592, { name = "Étoffe de laine", stack = 20, sellPrice = 33, type = "Artisanat", classID = 7 })
  M.AddItem(2770, { name = "Minerai de cuivre", stack = 20, sellPrice = 5, type = "Artisanat" })
  M.AddItem(9990, { name = "Griffe d'ours", stack = 1, sellPrice = 500, type = "Divers" })
  M.AddItem(7070, { name = "Épée de test", stack = 1, sellPrice = 100, type = "Arme" })
  local aid = 5000
  local function L(id, count, unit, owner, noInfo)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = owner or ("Vendeur" .. (aid % 5)), auctionID = aid, noInfo = noInfo }
  end
  local function market(extra)
    local l = {}
    for k = 1, 8 do l[#l + 1] = L(2589, 20, 100 + k, nil, k <= 3) end   -- 3 rows without item info yet
    for k = 1, 6 do l[#l + 1] = L(2592, 20, 300 + 2 * k) end
    for _, x in ipairs(extra or {}) do l[#l + 1] = x end
    return l
  end

  -- Auctionator (public API v1 only) present: price for the copper ore we never scanned
  local registered
  rawset(_G, "Auctionator", { API = { v1 = {
    GetAuctionPriceByItemID = function(caller, id) assert(caller == "TreeAI") if id == 2770 then return 80 end end,
    GetAuctionAgeByItemID = function(_, id) if id == 2770 then return 0 end end,
    GetVendorPriceByItemID = function() return nil end,
    RegisterForDBUpdate = function(caller, cb) registered = { caller, cb } end,
  } } })

  M.SetAuctionHouse(true)
  ctx.check(registered and registered[1] == "TreeAI", "Auctionator callback registered with callerID TreeAI")
  local s0 = A.Stats(2770)
  ctx.check(s0 and s0.src == "Auctionator" and s0.market == 80, "price from Auctionator when we have no scan")

  -- full scan: async, rows without item info are still read (id/price present)
  M.ah.list = market()
  A.win.scanBtn:Click()
  ctx.check(A.pendingAll, "ReplicateItems sent, waiting for the async result")
  M.Advance(1.5, 0.02)
  ctx.check(A.lastScan and A.lastScan.full and A.lastScan.rows == 14, "full scan read: " .. tostring(A.lastScan and A.lastScan.rows))
  ctx.check(A.lastScan and A.lastScan.unresolved == 0, "rows without item info resolved without waiting")
  ctx.check(A.cur[2589] and #A.cur[2589].listings == 8, "all 8 lin listings kept")
  local q0 = #M.ah.queries
  -- immediate second click: Blizzard's 15-min account throttle → countdown, nothing sent
  A.win.scanBtn:Click()
  ctx.check(#M.ah.queries == q0 and (M.ah.replicateRefused or 0) == 0, "no ReplicateItems inside the 15-min window")
  ctx.check(A.Said("Scan complet dans 1[45]:%d%d"), "countdown shown (panel status, not chat)")
  ctx.check(A.FullCooldown() > 800, "cooldown tracked")

  -- 20 min later Auctionator does the account's full scan: we read it too (no call of our own)
  M.Advance(1, 0.5)
  M.serverTime = M.serverTime + 20 * 60
  M.ah.list = market({ L(2589, 20, 50), L(9990, 1, 300) })
  M.OtherAddonReplicate()
  M.Advance(1.5, 0.02)
  ctx.check(A.lastScan and A.lastScan.by == "autre addon" and A.lastScan.rows == 16, "piggybacked on another addon's full scan")
  ctx.check(#M.ah.queries == q0, "piggyback costs no query")

  -- big scan processed in slices (several frames), not in one freeze
  M.serverTime = M.serverTime + 16 * 60
  local big = {}
  for k = 1, 6000 do big[k] = L(k % 2 == 0 and 2589 or 2592, 20, 100 + (k % 50)) end
  M.ah.list = big
  A.win.scanBtn:Click()
  local frames, ticks = 0, 0
  while ticks < 200 and not (A.lastScan.rows == 6000 and not A.job) do
    M.Advance(0.02, 0.02)
    ticks = ticks + 1
    if A.job then frames = frames + 1 end
  end
  ctx.check(frames >= 2 and not A.job and A.lastScan.rows == 6000, "6000 rows over several frames: " .. frames)
  ctx.check(A.lastScan.speed and A.lastScan.speed > 0, "scan speed measured")

  -- back to a normal market for the rest
  M.serverTime = M.serverTime + 16 * 60
  M.ah.list = market({ L(2589, 20, 50), L(9990, 1, 300) })
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)

  -- passive browse (the player browsing in Blizzard's UI)
  M.Advance(0.1, 0.05)
  C_AuctionHouse.SendBrowseQuery({ searchString = "" })
  M.Advance(0.5, 0.05)   -- (FIDELITY) the browse answer arrives a moment later, like in the game
  ctx.check(A.browse[2592] and A.browse[2592].min == 302, "browse results captured")

  -- targeted mini-scan under the search throttle
  M.SetBagItem(2, 1, 2592, 20)
  M.SetBagItem(2, 2, 7070, 1)
  M.Advance(0.1, 0.05)
  A.win.miniBtn:Click()
  M.Advance(3, 0.02)
  ctx.check(not A.mini and A.lastMini and A.lastMini.n >= 2, "mini-scan finished: " .. tostring(A.lastMini and A.lastMini.n))

  -- gold manager: reserve and per-item cap
  A.db.keepGold, A.db.autoReserve, A.db.minProfit = 0, true, 1
  local g = A.Gold()
  ctx.check(g.reserve == UnitLevel("player") ^ 2 * 150 and g.budget == M.player.money - g.reserve, ("reserve by level: %s %s %s"):format(g.reserve, g.budget, M.player.money))
  A.FindDeals()
  local plan = A.plan
  ctx.check(#plan >= 1, "a buying plan exists: " .. #plan)
  local per = {}
  for _, d in ipairs(plan) do per[d.id] = (per[d.id] or 0) + d.buy end
  for id, v in pairs(per) do
    local isVendor = false
    for _, d in ipairs(plan) do if d.id == id and d.kind == "vendor" then isVendor = true end end
    ctx.check(isVendor or v <= A.F.itemCap(g.total, A.db.capGold, g.budget), "per-item cap (% of all gold) respected")
  end
  for _, d in ipairs(A.deals) do
    if d.kind == "flip" then ctx.check(d.deposit and d.relist and d.relist >= 0, "deposit + relist risk on every flip") end
  end

  -- « Suivant »: each click = at most one protected action (the mock flags anything else)
  A.BuildQueue()
  local n0b, n0p = #M.ah.bids, #M.ah.posts
  local clicks = 0
  while A.QueueHead() and clicks < 20 do
    M.Advance(0.2, 0.05)
    A.win.nextBtn:Click()
    clicks = clicks + 1
  end
  ctx.check(#M.ah.bids > n0b, "Suivant bought something: " .. (#M.ah.bids - n0b))
  ctx.check(#M.ah.posts > n0p, "Suivant posted something: " .. (#M.ah.posts - n0p))
  ctx.check(clicks >= (#M.ah.bids - n0b) + (#M.ah.posts - n0p), "never more than one action per click")
  ctx.check(not A.QueueHead(), "queue drained")

  -- post skipped when the market dropped since the price was computed
  M.SetBagItem(2, 3, 2592, 20)
  A.BuildQueue()
  local e = A.QueueHead()
  ctx.check(e and e.kind == "post" and e.p.id == 2592, "a wool post queued")
  if e then
    for _, x in ipairs(M.ah.list) do if x.id == 2592 then x.buyout = 150 * 20 end end
    A.ApplyItemListings(2592, { { count = 20, buy = 150 * 20 } })
    local np = #M.ah.posts
    M.Hw(A.Next)                                           -- the queue's one-action step (the panel key rebuilds the queue)
    ctx.check(#M.ah.posts == np and e.p.state == "skip", "price moved → post skipped")
  end

  -- debug for the user to send
  M.RunSlash("/tai hv debug")
  ctx.check(ctx.out("API HV : modern"), "debug: API path")
  ctx.check(ctx.out("annonces/s"), "debug: scan speed")
  ctx.check(ctx.out("Auctionator : API v1 oui"), "debug: Auctionator")
  M.SetAuctionHouse(false)
  rawset(_G, "Auctionator", nil)
end
