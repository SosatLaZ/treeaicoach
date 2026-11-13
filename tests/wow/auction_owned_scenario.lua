-- « Mes ventes » sanity (bug report on 3.9.8, run by tests/wow/test_auction.py on both API flavours).
-- Reproduces the user's screen: a « Commission d'artisan : pantalon raffiné en cuir » listed at 95 pa, an
-- other listing at 3 pa 25 pc, full scan still reading (84 %) → the old code said « sapée », « annuler,
-- reposter 4 pa 93 pc » (above the lowest!) and « Suivant : annuler … », footer « budget 0 pc · scan jamais ».
-- Now: no advice while the scan is partial, no price advice on special items, repost always ≤ lowest − 1 pc,
-- cancel only when it pays after the lost deposit; footer shows the gold and « scan en cours (84 %) ».
return function(M, ctx)
  local T = TreeAI
  local A = T and T:GetModule("Auction")
  -- (5.1 AH) this scenario covers the market-data layer and the 4.1 panel; the buy-and-resell desk that replaces that
  -- panel at the AH has its own scenario (ah5_scenario.lua) and is switched off here.
  if T and T.AHDesk and T.SetModuleEnabled then T:SetModuleEnabled("AHDesk", false) end
  ctx.check(A, "Auction module registered")
  if not A then return end
  local F = A.F

  -- pure cancel advice: same numbers as test_auction.py::test_cancel_advice
  local hist = {}
  for i = 1, 5 do hist[i] = { day = 100 + i, min = 98, med = 100, qty = 60, n = 2, gone = 40, hrs = 12, sel = 6 } end
  local liq = F.liquidity(hist, 20, 1, 0, nil, 0)
  local comp = {}
  for k = 1, 15 do comp[k] = { 100 + k, 20 } end
  local c = F.cancelAdvice(150, 20, comp, liq, 13, false, 24)
  ctx.check(c.action == "cancel" and c.repost == 100 and c.gain == 1346, ("cancel pays: %s %s %s"):format(c.action, tostring(c.repost), tostring(c.gain)))
  ctx.check(c.repost <= comp[1][1] - 1, "repost ≤ lowest − 1 pc")
  local o = F.cancelAdvice(9500, 1, { { 325, 1 } }, liq, 0, false, 24)
  ctx.check(o.action == "keep" and o.text:find("plus de 10 fois"), "95 pa vs 3 pa 25 pc: keep, check the item")
  local dump = { { 50, 20 } }
  for k = 1, 15 do dump[#dump + 1] = { 100 + k, 20 } end
  ctx.check(F.cancelAdvice(150, 20, dump, liq, 13, false, 24).text:find("anormalement bas"), "dumped lowest: keep")
  local small = F.cancelAdvice(150, 20, { { 101, 20 } }, liq, 13, false, 24)
  ctx.check(small.action == "keep" and small.gain == -1058, "cancel would lose the deposit: keep " .. tostring(small.gain))
  ctx.check(F.cancelAdvice(150, 20, comp, { ok = false }, 13, false, 24).action == "none", "no data: no advice")

  -- the user's screen
  local ODD = 9100
  M.AddItem(ODD, { name = "Commission d'artisan : pantalon raffiné en cuir", stack = 1, quality = 1, sellPrice = 0, type = "Divers" })
  M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13, type = "Artisanat", classID = 7 })
  local me = M.player.name
  M.ah.list = { { id = ODD, count = 1, buyout = 325, owner = "Vendeur1", auctionID = 9001 } }
  M.ah.owned = { { id = ODD, count = 1, buyout = 9500, owner = me, auctionID = 9002 } }
  M.SetAuctionHouse(true)
  A.job = { rows = 84, total = 100, t0 = GetTime(), kind = "replicate" }   -- full scan still being read
  A.CheckOwned()
  A.BuildQueue()
  A.tab, A.page = "owned", 1
  A.Refresh()
  local row = A.win.rows[1]
  ctx.check(A.owned[1] and A.owned[1].advice:find("scan en cours %(84 %%%)"), "partial scan: « scan en cours » advice: " .. tostring(A.owned[1] and A.owned[1].advice))
  ctx.check(row.cells[4]:GetText() == "scan…" and not row.btn:IsShown(), "no « sapée », no cancel button during the scan")
  ctx.check(A.win.status:GetText():find("scan en cours %(84 %%%)") and A.win.status:GetText():find("or "), "footer: gold + scan en cours: " .. A.win.status:GetText())
  ctx.check(not A.NextLabel():find("annuler"), "Suivant does not cancel: " .. A.NextLabel())
  local nc = #M.ah.cancels
  A.win.nextBtn:Click() A.win.cancelBtn:Click() row.btn:Click()
  ctx.check(#M.ah.cancels == nc, "nothing cancelled during the scan")

  -- scan finished: special item → no price advice at all
  A.job = nil
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)
  A.CheckOwned()
  A.BuildQueue()
  A.Refresh()
  ctx.check(A.owned[1].advice:find("objet spécial %(commission d'artisan%)"), "special item: " .. tostring(A.owned[1].advice))
  ctx.check(not A.owned[1].worth and not A.owned[1].undercut and A.win.rows[1].cells[3]:GetText() == "-", "no « sapée », no lowest shown")
  ctx.check(not A.win.status:GetText():find("jamais"), "footer: scan age, never « jamais » after a scan")
  A.win.nextBtn:Click() A.win.cancelBtn:Click() A.win.rows[1].btn:Click()
  ctx.check(#M.ah.cancels == nc, "special item never cancelled")
  for _, d in ipairs(A.deals) do ctx.check(d.id ~= ODD, "special item never a deal") end

  -- a liquid trade good really beaten behind 300 cheaper units: cancel advised, repost ≤ lowest − 1
  local r = A.RealmData()
  local day = math.floor(M.serverTime / 86400)
  local h, sl = {}, {}
  for i = 1, 5 do
    local dd = day - 6 + i
    for _, v in ipairs({ dd, 98, ({ 100, 102, 98, 101, 100 })[i], 100, 3, 40, 12 }) do h[#h + 1] = v end
    sl[#sl + 1] = dd sl[#sl + 1] = 6
  end
  r.items[2589] = { h = h, sl = sl, last = M.serverTime, v = 13 }
  local list = {}
  for k = 1, 15 do list[k] = { id = 2589, count = 20, buyout = (100 + k) * 20, owner = "Vendeur" .. (k % 6), auctionID = 9100 + k } end
  M.ah.list = list
  M.ah.owned = { { id = 2589, count = 20, buyout = 150 * 20, owner = me, auctionID = 9200 } }
  M.serverTime = M.serverTime + 16 * 60
  A.win.scanBtn:Click()
  M.Advance(1.5, 0.02)
  A.CheckOwned()
  A.BuildQueue()
  A.Refresh()
  local a = A.owned[1]
  ctx.check(a and a.worth and a.repost and a.repost <= 100 and a.advice:find("^annuler, reposter"), "beaten liquid lin: cancel + repost ≤ 1 pa: " .. tostring(a and a.advice))
  ctx.check(A.NextLabel():find("annuler Étoffe de lin x20 %(reposter 1 pa/u%)"), "Suivant names the repost price: " .. A.NextLabel())
  A.win.cancelBtn:Click()
  ctx.check(#M.ah.cancels == nc + 1, "one cancel on click")

  -- same lin but someone dumps at 50 pc: keep (abnormal lowest), nothing to cancel
  list[#list + 1] = { id = 2589, count = 20, buyout = 50 * 20, owner = "Vendeur9", auctionID = 9300 }
  M.ah.owned = { { id = 2589, count = 20, buyout = 150 * 20, owner = me, auctionID = 9201 } }
  A.ApplyItemListings(2589, (function()
    local rows = {}
    for _, x in ipairs(list) do rows[#rows + 1] = { count = x.count, buy = x.buyout, owner = x.owner } end
    return rows
  end)())
  A.CheckOwned()
  ctx.check(not A.owned[1].worth and A.owned[1].advice:find("anormalement bas"), "dumped lowest: keep: " .. tostring(A.owned[1].advice))
  M.SetAuctionHouse(false)
end
