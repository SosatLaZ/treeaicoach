-- « Mode rafale » (run by tests/wow/test_auction.py on modern / legacy / forever). The user asked for a fully
-- automatic flip mode: impossible (protected AH calls need one hardware event each). Rafale = one press
-- (big button or the « TreeAI : action HV suivante » key) = the NEXT safe action, at most ONE protected call;
-- the strict mock flags any purchase / post / cancel outside a hardware event or two in one. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  ctx.check(A and T.AuctionNext, "Auction module + TreeAI.AuctionNext (key binding entry)")
  if not A then return end
  if T.SetSoundKind then T:SetSoundKind("ah", true) end   -- (4.0.1) sons coupés par défaut sauf le danger
  ctx.check(BINDING_NAME_TREEAI_AH_NEXT == "TreeAI : action HV suivante" and BINDING_HEADER_TREEAI == "TreeAI", "binding names")
  local modern = M.flavor ~= "legacy"

  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat", classID = 7 })
  M.AddItem(9990, { name = "Griffe d'ours", stack = 1, sellPrice = 500, type = "Divers" })
  local r = A.RealmData()
  local day = math.floor(M.serverTime / 86400)
  local h, sl = {}, {}
  for i, m in ipairs({ 100, 102, 98, 101, 100 }) do
    local dd = day - 6 + i
    for _, v in ipairs({ dd, m - 2, m, 100, 3, 40, 12 }) do h[#h + 1] = v end
    sl[#sl + 1] = dd sl[#sl + 1] = 6
  end
  r.items[2589] = { h = h, sl = sl, last = M.serverTime, v = 13 }
  local aid = 8000
  local function L(id, count, unit)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = "Vendeur" .. (aid % 6), auctionID = aid }
  end
  local list = {}
  for k = 1, 6 do list[#list + 1] = L(2589, 20, 100 + k) end
  list[#list + 1] = L(2589, 20, 60)        -- safe flip
  list[#list + 1] = L(9990, 1, 300)        -- vendor flip (risk-free)
  M.ah.list = list
  A.db.minProfit, A.db.autoReserve, A.db.keepGold, A.db.mountGold = 1, false, 0, 0
  M.SetAuctionHouse(true)

  -- first press with nothing known: the press starts the (throttled) scan, nothing bought
  local nb, np = #M.ah.bids, #M.ah.posts
  M.Hw(T.AuctionNext)
  M.Advance(1.5, 0.02)
  ctx.check(A.lastScan and A.lastScan.full and #M.ah.bids == nb, "empty queue: the press scans (ReplicateItems in the key press)")
  ctx.check(#A.plan >= 2, "safe plan after the scan: " .. #A.plan)
  M.SetBagItem(2, 1, 2589, 20)             -- a stack to post after the buys
  A.BuildQueue()
  A.Refresh()
  local first = A.win.hint:GetText()
  ctx.check(first:find("^Prochaine action %(1/%d+%) : ") and first:find("acheter 1 × Griffe d'ours"), "label before the press: " .. first)

  -- partial scan: the press does nothing
  A.job = { rows = 84, total = 100, t0 = GetTime(), kind = "replicate" }
  M.Hw(T.AuctionNext)
  ctx.check(#M.ah.bids == nb and A.Said("En pause : scan en cours %(84 %%%)"), "partial scan: paused")
  A.job = nil

  -- reserve reached: no purchase
  A.db.keepGold = 1000000
  M.Hw(T.AuctionNext)
  ctx.check(#M.ah.bids == nb and A.raf.noBuy, "gold reserve reached: no purchase")
  A.db.keepGold = 0
  A.raf.on = false

  -- the rafale: press after press (key or big button), the queue refreshes itself in between
  local snd = #M.sounds
  local presses = 0
  while presses < 20 do
    local e = A.RafaleHead()
    if not e and presses > 0 then break end
    presses = presses + 1
    if presses % 2 == 0 then A.win.rafBtn:Click() else M.Hw(T.AuctionNext) end
    M.Advance(0.6, 0.05)                  -- between presses: re-check of the next price (search, no protected call)
  end
  local bought, posted = #M.ah.bids - nb, #M.ah.posts - np
  ctx.check(bought == 2, "bought the 2 safe deals: " .. bought)
  ctx.check(posted == 1 and M.ah.posts[#M.ah.posts].id == 2589, "posted the bag stack: " .. posted)
  ctx.check(A.raf.n == bought + posted and presses >= A.raf.n, ("one action per press: %d actions / %d presses"):format(A.raf.n, presses))
  ctx.check(not modern or presses <= A.raf.n + 2, "prefetch: Forever needs at most the game's own confirm press: " .. presses)
  ctx.check(#M.sounds > snd, "tick after successful actions")
  ctx.check(A.Said("Série : 3 actions, %+%d+ pa") and A.SessionText():find("^Session : 2 achats"), "session summary in the panel (not in chat): " .. A.SessionText())
  ctx.check(A.char.rafale and A.char.rafale.n == 3, "summary kept for the Bilan")

  -- « Lier à une touche »: 2 clicks (propose, confirm), through the Binds module path
  A.win.bindBtn:Click()
  ctx.check(not A.RafaleKey(), "first click only proposes")
  A.win.bindBtn:Click()
  ctx.check(A.RafaleKey() == "CTRL-SPACE", "bound to a free key: " .. tostring(A.RafaleKey()))
  M.SetAuctionHouse(false)
  M.Hw(T.AuctionNext)                       -- AH closed: nothing protected
end
