-- Liquidity model of the Auction module (run by tests/wow/test_auction.py on both API flavours).
-- Fake histories prove: illiquid items (rare green seen once, unstable price, too few sales, Auctionator
-- disagreeing, stack bigger than what sells) are REJECTED; a liquid trade good is KEPT with its stop-loss
-- and quantity cap; the empty state says « Aucun achat-revente sûr pour l'instant » with why; hard rule
-- « max X % of gold in one item »; quick buttons (buy best / repost / cancel beaten) = one action per click;
-- « ah » sound only for a new safe deal. Same numbers as tests/wow/test_auction.py. Fake names only.
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  ctx.check(A, "Auction module registered")
  if not A then return end
  local F = A.F
  local modern = M.flavor == "modern"

  -- pure model: same numbers as the python mirror (test_auction.py::test_liquidity_mirror_numbers)
  local function hist(meds, gone, hrs, n, sel)
    local ds = {}
    for i, m in ipairs(meds) do
      ds[i] = { day = 100 + i, min = m - 2, med = m, qty = 60, n = n or 2, gone = gone or 0, hrs = hrs or 0, sel = sel or 6 }
    end
    return ds
  end
  local liquid = F.liquidity(hist({ 100, 102, 98, 101, 100 }, 40, 12), 20, 1, 0.10, 104, 0)
  ctx.check(liquid.ok and liquid.conf == 1 and liquid.sold == 48 and liquid.market == 100, ("liquid kept: %s %s %s"):format(
    tostring(liquid.ok), liquid.conf, tostring(liquid.sold)))
  local rare = F.liquidity(hist({ 5000 }, 0, 0, 1, 1), 1, 2, 0, nil, 0)
  ctx.check(not rare.ok and rare.reasons[1].code == "rare", "rare green seen once: excluded")
  local wild = F.liquidity(hist({ 100, 180, 60, 150, 90 }, 40, 12), 20, 1, 0, nil, 0)
  ctx.check(not wild.ok and wild.reasons[1].code == "unstable", "unstable price rejected: " .. tostring(wild.reasons[1] and wild.reasons[1].code))
  local slow = F.liquidity(hist({ 100, 100, 100, 100, 100 }, 0, 12), 20, 1, 0, nil, 0)
  ctx.check(not slow.ok and slow.reasons[1].code == "slow" and slow.reasons[1].text:find("0,0 par jour"), "slow seller rejected: " .. tostring(slow.reasons[1] and slow.reasons[1].text))
  local atr = F.liquidity(hist({ 100, 102, 98, 101, 100 }, 40, 12), 20, 1, 0, 300, 0)
  ctx.check(not atr.ok and atr.reasons[1].code == "atr", "Auctionator disagreeing: rejected")
  local young = F.liquidity(hist({ 100, 100 }, 40, 12), 20, 1, 0, nil, 0)
  ctx.check(not young.ok and young.reasons[1].code == "short", "2 days of history: too short")
  local youngDemand = F.liquidity(hist({ 100, 100 }, 40, 12), 20, 1, 0.10, nil, 0)
  ctx.check(youngDemand.ok, "a demand class (trade good) needs only 2 days: " .. tostring(youngDemand.reasons[1] and youngDemand.reasons[1].text))
  ctx.check(F.maxQty(48, 3, 20) == 124 and F.maxQty(nil) == 0, "max quantity = sold in K days − held")
  local st = F.stopLoss(1000, 0)
  ctx.check(st.unit == 850 and st.loss == 192, "stop-loss −15 %: " .. st.unit .. " " .. st.loss)
  ctx.check(F.itemCap(1000000, 20, 150000) == 150000 and F.itemCap(1000000, 20) == 200000, "item cap")
  ctx.check(F.soldBetween({ ["100:20"] = 60, ["90:5"] = 5 }, { ["100:20"] = 20, ["95:20"] = 20 }) == 25, "reposts at a lower price are not sales")
  local d, why = F.safeFlip(1200, 20, liquid, 3, 0, 0, false, 24, 0.2, 100)
  ctx.check(d and d.safe and d.stop.unit == 51 and d.stop.loss == 11 and d.profit == 679 and d.days == 0.42 and d.maxQty == 144, "safe flip with stop + max qty: " .. tostring(why and why.text))
  local _, w2 = F.safeFlip(1200 * 10, 200, liquid, 3, 0, 0, false, 24, 0.2, 100)
  ctx.check(w2 and w2.code == "stack", "stack bigger than 3 days of sales: rejected")
  ctx.check(F.whyNone({ slow = 2, rare = 1 }, 3)[2] == "se vend trop peu (2)", "why lines")

  -- items: a liquid trade good, an illiquid rare green, an unstable reagent
  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat", classID = 7 })
  M.AddItem(7001, { name = "Lame du test vert", stack = 1, quality = 2, sellPrice = 900, type = "Arme", classID = 2, minLevel = 50 })
  M.AddItem(7002, { name = "Poudre instable", stack = 20, sellPrice = 5, type = "Composant", classID = 5 })
  local aid = 7000
  local function L(id, count, unit, owner)
    aid = aid + 1
    return { id = id, count = count, buyout = unit * count, owner = owner or ("Vendeur" .. (aid % 6)), auctionID = aid }
  end
  local r = A.RealmData()
  local day = math.floor(M.serverTime / 86400)
  local function seed(id, meds, gone, hrs, sel)
    local h, sl = {}, {}
    for i, m in ipairs(meds) do
      local dd = day - #meds + i - 1
      for _, v in ipairs({ dd, m - 2, m, 100, 3, gone, hrs }) do h[#h + 1] = v end
      sl[#sl + 1] = dd sl[#sl + 1] = sel
    end
    r.items[id] = { h = h, sl = sl, last = M.serverTime, v = 0 }
  end
  seed(2589, { 100, 102, 98, 101, 100 }, 40, 12, 6)         -- ~48 sold / day, stable
  seed(7002, { 100, 190, 55, 160, 80 }, 40, 12, 6)          -- sells, but the price jumps around

  -- scan: one cheap listing of each (+ the rare green, seen once, at a « bargain »)
  M.SetAuctionHouse(true)
  local snd0 = #M.sounds
  local function market(extra)
    local l = {}
    for k = 1, 6 do l[#l + 1] = L(2589, 20, 100 + k) end
    for k = 1, 6 do l[#l + 1] = L(7002, 20, 100 + k) end
    for _, x in ipairs(extra or {}) do l[#l + 1] = x end
    return l
  end
  A.db.minProfit = 1
  M.ah.list = market({ L(7001, 1, 2000), L(7002, 20, 40) })
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)
  ctx.check(#A.deals == 0, "illiquid bargains rejected: " .. #A.deals)
  ctx.check(A.none and A.none.text == "Aucun achat-revente sûr pour l'instant", "empty state text")
  ctx.check(A.rejects.unstable == 1, "unstable reagent rejected (reason counted)")
  local rareLiq = A.Liq(7001)
  ctx.check(not rareLiq.ok and rareLiq.reasons[1].code == "rare", "rare green with 1 observation excluded: " .. tostring(rareLiq.reasons[1] and rareLiq.reasons[1].code))
  A.tab, A.page = "deals", 1
  A.Refresh()
  ctx.check(A.win.rows[1]:IsShown() and A.win.rows[1].cells[1]:GetText():find("Aucun achat%-revente sûr"), "window says « Aucun achat-revente sûr »")
  ctx.check(not A.win.rows[1].btn:IsShown(), "no buy button on the explanation")
  ctx.check(A.win.rows[2].cells[1]:GetText():find("écartée"), "with why: " .. tostring(A.win.rows[2].cells[1]:GetText()))
  local lines = A.TabLines()
  local found = false
  for _, l in ipairs(lines) do if l.text:find("Aucun achat%-revente sûr") then found = true end end
  ctx.check(found, "panel HV tab says it too")
  ctx.check(#M.sounds == snd0, "no « good deal » sound without a safe deal")

  -- 200 cheap lin at once: more than 3 days of sales → refused (max quantity rule)
  M.Advance(30, 1)
  M.ah.list = market({ L(2589, 200, 60) })
  M.serverTime = M.serverTime + 16 * 60
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)
  ctx.check(#A.deals == 0 and A.rejects.stack == 1, "200 lin (more than 3 days of sales) refused: " .. tostring(A.lastReject and A.lastReject.why.text))
  ctx.check(#M.sounds == snd0, "still no sound")

  -- now a cheap stack of 20 lin: safe deal, with stop-loss and quantity cap; sound plays once
  M.Advance(30, 1)
  M.ah.list = market({ L(2589, 20, 60) })
  M.serverTime = M.serverTime + 16 * 60
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)
  local lin
  for _, x in ipairs(A.deals) do if x.id == 2589 and x.count == 20 then lin = x end end
  ctx.check(lin and lin.safe and lin.stop and lin.stop.unit == 51 and lin.sold and lin.maxQty >= 20,
    "liquid lin kept with stop-loss + max qty: " .. tostring(A.lastReject and A.lastReject.why.text))
  if not lin then return end
  ctx.check(#M.sounds > snd0, "« good deal » sound for the new safe deal")
  local s1 = #M.sounds
  A.FindDeals()
  for _, x in ipairs(A.deals) do if x.id == 2589 and x.count == 20 then lin = x end end
  ctx.check(#M.sounds == s1, "no repeat sound for the same deal")

  -- hard rule: never more than capGold % of all gold in one item
  A.db.autoReserve, A.db.keepGold, A.db.mountGold = false, 0, 0
  M.player.money = 100000                                   -- 10 po : 1 % = 10 pa < 12 pa (the lin stack)
  A.db.capGold = 1
  A.Plan()
  ctx.check(#A.plan == 0, "cap 1 % of gold: the 12 pa stack is not planned")
  A.db.capGold = 20
  A.Plan()
  do local g = A.Gold() ctx.check(#A.plan == 1 and A.plan[1] == lin, ("cap 20 %%: planned %d total %s budget %s adj %s buy %s state %s"):format(#A.plan, g.total, g.budget, tostring(lin.adj), lin.buy, tostring(lin.state))) end
  A.BuildQueue()
  A.Refresh()
  ctx.check(A.win.buyBtn:GetText():find("Acheter la meilleure offre %(1%)"), "buy button label: " .. tostring(A.win.buyBtn:GetText()))
  ctx.check(A.win.hint:GetText():find("Prochain achat : Étoffe de lin x20"), "hint shows the next purchase")

  -- « Acheter la meilleure offre »: one protected call per click (modern: verify → buy/start → confirm)
  local nb, clicks = #M.ah.bids, 0
  while lin.state ~= "bought" and clicks < 4 do M.Advance(0.2, 0.05) A.win.buyBtn:Click() clicks = clicks + 1 end
  ctx.check(#M.ah.bids == nb + 1 and lin.state == "bought", "bought with the quick button in " .. clicks .. " click(s)")
  ctx.check(modern or clicks == 1, "legacy: one click")
  ctx.check(A.char.flips[2589] and A.char.flips[2589].stop == 51, "stop-loss remembered for the resale")
  A.win.buyBtn:Click()
  ctx.check(#M.ah.bids == nb + 1, "nothing more to buy")

  -- « Annuler les battues »: my lin auction beaten → one cancel per click
  do   -- a really beaten lin: 300 cheaper units ahead (the 50/60 pc bargain is gone), cancelling pays
    local rows = {}
    for k = 1, 15 do rows[k] = { count = 20, buy = (100 + k) * 20, owner = "Vendeur" .. k } end
    A.ApplyItemListings(2589, rows)
  end
  M.ah.owned = { { id = 2589, count = 20, buyout = 20 * 150, owner = M.player.name, auctionID = 990 } }
  A.CheckOwned()
  A.Refresh()
  ctx.check(A.win.cancelBtn:GetText():find("%(1%)"), "cancel button counts beaten auctions: " .. tostring(A.win.cancelBtn:GetText()))
  local nc = #M.ah.cancels
  A.win.cancelBtn:Click()
  A.win.cancelBtn:Click()
  ctx.check(#M.ah.cancels == nc + 1, "one cancel per click, nothing when none left")

  -- « Remettre en vente »: the bag stack, 1 pc under the cheapest (never under the floor)
  M.SetBagItem(2, 1, 2589, 20)
  A.postRows = nil
  A.Refresh()
  local np, pc = #M.ah.posts, 0
  while #M.ah.posts == np and pc < 3 do M.Advance(3.5, 0.5) A.win.postBtn:Click() pc = pc + 1 end
  ctx.check(#M.ah.posts == np + 1 and M.ah.posts[#M.ah.posts].id == 2589, "reposted with one click (+ a price check on Forever): " .. pc)
  local unit = M.ah.posts[#M.ah.posts].unit or math.floor(M.ah.posts[#M.ah.posts].buyout / 20)
  ctx.check(unit >= 51, "post price above the stop-loss: " .. tostring(unit))

  -- summary for TreeAI's app
  T:Fire("SUMMARY")
  local sum = T:Summary().auction
  ctx.check(sum and sum.rules and sum.rules.capGold == 20, "rules in the summary")
  M.SetAuctionHouse(false)
  -- AH closed: quick buttons do nothing protected
  A.win.buyBtn:Click() A.win.postBtn:Click() A.win.cancelBtn:Click()
end
