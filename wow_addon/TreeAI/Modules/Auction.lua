-- TreeAI — module Hôtel des ventes (achat-revente). Conseille, calcule, n'automatise RIEN :
--  * chaque achat / annulation = UN clic du joueur sur un bouton « Acheter » / « Annuler » (événement
--    matériel), l'appel protégé est fait DANS ce OnClick, une seule action par clic, jamais de boucle ni
--    de file de clics ; le prix est revérifié juste avant. Les scans sont lancés par le bouton « Scan
--    complet » (getAll, 1 fois / 15 min) ou lus passivement quand le joueur parcourt l'HV lui-même.
--  * historique des prix par objet (≤ 14 jours, compact, plafonné) → valeur de marché (médiane robuste),
--    volatilité, volume/jour (annonces disparues entre deux scans complets), confiance.
--  * frais exacts (règle Classic, voir treeaicoach/wow/auction.py — mêmes formules, mêmes arrondis) :
--    commission 5 % (HV de faction) / 15 % (HV neutre) ; dépôt = floor(prix marchand × qté × 5 % (25 %
--    neutre) × heures / 2) ; profit = vente × (1 − commission) − achat − dépôt × P(invendu).
--  * affaires : sous le marché (marge ≥ X %, profit ≥ Y après frais et risque), reventes au marchand
--    (prix < prix marchand = gain garanti), marchés minces (« reset »).
--  * vente : prix conseillé (1 pc sous le moins cher, jamais sous marché − σ ni sous le marchand), dépôt,
--    net, « mieux au marchand », découpe des piles ; mes enchères sapées → annuler / reposter.
--  * budget, journal achats/ventes (courrier de l'HV, FR/EN), profit réalisé par objet / jour.
--  * Auctionator : lecture seule de Auctionator.API.v1 (prix + âge) en secours, jamais modifié.
-- API HV : classique (QueryAuctionItems / GetAuctionItemInfo / PlaceAuctionBid) OU C_AuctionHouse
-- (ReplicateItems / SendSearchQuery / PlaceBid / Start+ConfirmCommoditiesPurchase), testées à l'exécution.
-- Résumé : TreeAIDB.chars[perso].summary.auction (meilleures affaires, conseils de vente, profit réalisé).
-- Lua 5.1 ; aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Auction", {
  title = "Hôtel des ventes",
  defaults = { enabled = true, minMargin = 20, minProfit = 20, budget = 0, hours = 24, maxItems = 1500,
               house = "auto", window = true, tips = true },
  charDefaults = { ledger = {}, seen = {}, spent = {} },
  options = {
    { key = "window", type = "toggle", label = "Fenêtre d'aide à l'ouverture de l'HV" },
    { key = "tips", type = "toggle", label = "Annoncer les affaires dans le panneau" },
    { key = "minMargin", type = "range", label = "Marge minimale sous le marché (%)", min = 5, max = 60, step = 5 },
    { key = "minProfit", type = "range", label = "Profit minimal par achat (pa)", min = 0, max = 1000, step = 10 },
    { key = "budget", type = "range", label = "Budget d'achat par jour (po, 0 = illimité)", min = 0, max = 5000, step = 10 },
    { key = "hours", type = "range", label = "Durée de mise en vente (h)", min = 2, max = 48, step = 2 },
    { key = "maxItems", type = "range", label = "Objets suivis au maximum", min = 300, max = 5000, step = 100 },
  },
})
ns.Auction = M

local API = T.API or {}
local floor, ceil, max, min, abs = math.floor, math.ceil, math.max, math.min, math.abs
local unpack = unpack or table.unpack

-- ------------------------------------------------------------------ utilitaires
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local r = { pcall(fn, ...) }
  if not r[1] then return nil end
  return unpack(r, 2, table.maxn(r))
end
local function now() return try(_G.GetServerTime) or try(_G.time) or 0 end
local function today(t) return floor((t or now()) / 86400) end
local function dateKey(t) return try(_G.date, "%Y-%m-%d", t or now()) or tostring(today(t)) end
local function n0(v) return tonumber(v) or 0 end
local function r4(x) return floor(x * 10000 + 0.5) / 10000 end
local function clamp(x, a, b) if x < a then return a elseif x > b then return b end return x end
local function cfg(k, d) local v = M.db and M.db[k] if v == nil then return d end return v end

local function money(c)
  c = floor(n0(c))
  local sign = c < 0 and "-" or ""
  c = abs(c)
  local g, s, cp = floor(c / 10000), floor(c / 100) % 100, c % 100
  local t = {}
  if g > 0 then t[#t + 1] = g .. " po" end
  if s > 0 then t[#t + 1] = s .. " pa" end
  if cp > 0 or #t == 0 then t[#t + 1] = cp .. " pc" end
  return sign .. table.concat(t, " ")
end
M.Money = money

local function itemName(id) return (API.ItemName and API.ItemName(id)) or ("objet " .. tostring(id)) end
local function vendorPrice(id)
  if not id then return 0 end
  return n0(select(11, M.ItemInfo(id)))
end
function M.ItemInfo(x)
  if x == nil or type(API.ItemInfo) ~= "function" then return nil end
  return API.ItemInfo(x)
end
local function idFromLink(link)
  return link and tonumber(tostring(link):match("item:(%-?%d+)")) or nil
end
local function me() return try(_G.UnitName, "player") or "" end

-- ------------------------------------------------------------------ calculs (miroir de auction.py)
local CUT = { [false] = 5, [true] = 15 }
local DEP = { [false] = 5, [true] = 25 }
local SOLD_SHARE, LOW_N, MAX_DAYS, STRIDE = 0.6, 5, 14, 7
local F = {}
M.F = F

function F.cut(price, neutral) return floor(max(0, n0(price)) * CUT[neutral and true or false] / 100) end
function F.deposit(vendor, count, hours, neutral)
  return floor(max(0, n0(vendor)) * max(1, n0(count)) * DEP[neutral and true or false] * max(0, n0(hours or 24)) / 200)
end
function F.net(price, neutral) return max(0, n0(price)) - F.cut(price, neutral) end
function F.profit(buy, sell, dep, p, neutral)
  p = clamp(p or 1, 0, 1)
  return floor(F.net(sell, neutral) - n0(buy) - n0(dep) * (1 - p) + 0.5)
end
function F.breakEven(buy, dep, p, neutral)
  p = clamp(p or 1, 0, 1)
  local need = n0(buy) + n0(dep) * (1 - p)
  local s = max(0, ceil(need * 100 / (100 - CUT[neutral and true or false])))
  while s > 0 and F.profit(buy, s - 1, dep, p, neutral) >= 0 do s = s - 1 end
  while F.profit(buy, s, dep, p, neutral) < 0 do s = s + 1 end
  return s
end
function F.median(xs)
  local t = {}
  for _, v in ipairs(xs) do t[#t + 1] = floor(n0(v)) end
  if #t == 0 then return nil end
  table.sort(t)
  local n = #t
  if n % 2 == 1 then return t[(n + 1) / 2] end
  return floor((t[n / 2] + t[n / 2 + 1]) / 2)
end
function F.lowMedian(units, n)
  local t = {}
  for _, v in ipairs(units) do if n0(v) > 0 then t[#t + 1] = n0(v) end end
  table.sort(t)
  local k = {}
  for i = 1, min(#t, n or LOW_N) do k[i] = t[i] end
  return F.median(k)
end
function F.pSell(volume, ahead, count, hours)
  if volume == nil then return 0.5 end
  if volume <= 0 then return 0.05 end
  local need = (max(0, n0(ahead)) + max(1, n0(count))) / volume
  return r4(clamp((max(1, n0(hours or 24)) / 24) / need, 0.05, 0.95))
end
function F.days(volume, ahead, count)
  if not volume or volume <= 0 then return nil end
  return floor(((max(0, n0(ahead)) + max(1, n0(count))) / volume) * 100 + 0.5) / 100
end
function F.thin(units)
  local t = {}
  for _, v in ipairs(units) do if n0(v) > 0 then t[#t + 1] = n0(v) end end
  if #t == 0 then return false end
  table.sort(t)
  if #t <= 3 then return true end
  return #t <= 6 and t[2] * 2 >= t[1] * 3
end

-- historique : rec.h = { jour, min, med, qté, nScans, disparus, heures, jour, ... } (pas de 7)
local function points(rec)
  local out, h = {}, rec and rec.h or {}
  for i = 1, #h - STRIDE + 1, STRIDE do
    if n0(h[i + 2]) > 0 then
      out[#out + 1] = { day = h[i], min = h[i + 1], med = h[i + 2], qty = h[i + 3], n = h[i + 4], gone = h[i + 5], hrs = h[i + 6] }
    end
  end
  return out
end
function F.statsFromPoints(ds, ageDays)
  if #ds == 0 then return nil end
  local meds = {}
  for i, d in ipairs(ds) do meds[i] = d.med end
  local mv = F.median(meds)
  local vol
  if #ds < 3 then
    local l = ds[#ds]
    vol = r4(max(0.10, (l.med - l.min) / l.med))
  else
    local dev = {}
    for i, d in ipairs(ds) do dev[i] = abs(d.med - mv) end
    vol = r4(1.4826 * (F.median(dev) or 0) / max(1, mv))
  end
  local gone, hrs, scans, qty = 0, 0, 0, 0
  for _, d in ipairs(ds) do
    gone, hrs = gone + n0(d.gone), hrs + n0(d.hrs)
    scans, qty = scans + max(1, n0(d.n)), qty + n0(d.qty)
  end
  local volume = hrs > 0 and floor(gone / hrs * 24 * SOLD_SHARE * 100 + 0.5) / 100 or nil
  local nd = #ds
  local c = 0.5 * min(1, nd / 4) + 0.25 * min(1, scans / 4) + 0.25 * min(1, (qty / nd) / 10)
  if (ageDays or 0) > 3 then c = c * 0.5 end
  c = floor(c * (1 - min(0.5, vol)) * 100 + 0.5) / 100
  return { market = mv, vol = vol, volume = volume, conf = c, ndays = nd }
end

function F.flip(buy, count, st, vendor, ahead, neutral, hours)
  count = max(1, n0(count))
  local target = max(1, floor(st.market * (1 - 0.5 * (st.vol or 0))))
  local sell = target * count
  local dep = F.deposit(vendor, count, hours, neutral)
  local p = F.pSell(st.volume, ahead, count, hours)
  local profit = F.profit(buy, sell, dep, p, neutral)
  local c = clamp(st.conf or 0, 0, 1)
  local adj = profit > 0 and floor(profit * c) or profit
  local unit = floor(buy / count)
  return { kind = "flip", buy = buy, count = count, unit = unit, sell = sell, sellUnit = target, deposit = dep,
    p = p, profit = profit, adj = adj, roi = buy > 0 and profit / buy or nil,
    breakEven = F.breakEven(buy, dep, p, neutral),
    discount = st.market > 0 and r4(1 - unit / st.market) or 0, days = F.days(st.volume, ahead, count),
    score = buy > 0 and floor(adj / buy * 10000 * 100 + 0.5) / 100 or 0, conf = c }
end
function F.vendorFlip(buy, count, vendor)
  count = max(1, n0(count))
  local gain = n0(vendor) * count - n0(buy)
  if n0(buy) <= 0 or gain <= 0 then return nil end
  return { kind = "vendor", buy = buy, count = count, unit = floor(buy / count), sell = vendor * count, deposit = 0,
    p = 1, profit = gain, adj = gain, roi = gain / buy, breakEven = buy, discount = 0, days = 0,
    score = floor(gain / buy * 10000 * 100 + 0.5) / 100, conf = 1 }
end
function F.rank(deals, minMargin, minProfit, budget)
  local out = {}
  for _, d in ipairs(deals) do
    if not budget or d.buy <= budget then
      if d.kind == "vendor" then
        if d.profit > 0 then out[#out + 1] = d end
      elseif d.discount >= minMargin and d.adj >= minProfit then
        out[#out + 1] = d
      end
    end
  end
  table.sort(out, function(a, b)
    if a.score ~= b.score then return a.score > b.score end
    return (a.days or 999) < (b.days or 999)
  end)
  return out
end
-- comp = { {unit, count}, ... } des AUTRES vendeurs
function F.suggestPost(count, vendor, st, comp, neutral, hours)
  count = max(1, n0(count))
  local vt = n0(vendor) * count
  local mv = st and st.market or 0
  local res = { count = count, deposit = F.deposit(vendor, count, hours, neutral), vendor = vt, market = mv > 0 and mv or nil,
    better = vt > 0 and "vendor" or nil, reason = "pas de prix de marché" }
  table.sort(comp, function(a, b) return a[1] < b[1] end)
  res.lowest = comp[1] and comp[1][1] or nil
  if mv <= 0 and #comp == 0 then return res end
  if #comp > 0 then
    local sizes, best, bestN = {}, nil, 0
    for _, c in ipairs(comp) do sizes[c[2]] = (sizes[c[2]] or 0) + 1 end
    for size, nb in pairs(sizes) do
      if nb > bestN or (nb == bestN and size < best) then best, bestN = size, nb end
    end
    if best and best < count then res.split = best end
  end
  local sigma = floor(mv * ((st and st.vol) or 0))
  local floorU = mv > 0 and max(1, mv - sigma) or 1
  local volume = st and st.volume
  if vt > 0 then
    local p0 = F.pSell(volume, 0, count, hours)
    floorU = max(floorU, ceil(F.breakEven(vt, res.deposit, p0, neutral) / count))
  end
  local unit
  if #comp > 0 then
    unit, res.reason = comp[1][1] - 1, "1 pc sous le moins cher"
    if unit < floorU then unit, res.reason = floorU, "concurrent trop bas : ne pas sous-coter sous le plancher" end
  else
    unit, res.reason = mv, "aucun concurrent : prix du marché"
  end
  unit = max(unit, floorU)
  local ahead = 0
  for _, c in ipairs(comp) do if c[1] < unit then ahead = ahead + c[2] end end
  local p = F.pSell(volume, ahead, count, hours)
  res.unit, res.p = unit, p
  res.net = floor(F.net(unit * count, neutral) - res.deposit * (1 - p) + 0.5)
  res.better = vt >= res.net and "vendor" or "ah"
  return res
end
function F.realized(events)
  local items, days, total = {}, {}, 0
  for _, e in ipairs(events) do
    local k = tostring(e.item or "?")
    local it = items[k]
    if not it then it = { held = 0, cost = 0, profit = 0, sold = 0, bought = 0, noCost = 0 } items[k] = it end
    local q, m = max(1, n0(e.qty)), n0(e.money)
    if e.type == "buy" then
      it.held, it.cost, it.bought = it.held + q, it.cost + m, it.bought + q
    elseif e.type == "sell" then
      local net = m - n0(e.cut)
      local covered = min(q, it.held)
      local basis = it.held > 0 and floor(it.cost * covered / it.held) or 0
      it.cost, it.held = it.cost - basis, it.held - covered
      it.noCost, it.sold = it.noCost + q - covered, it.sold + q
      local gain = net - basis
      it.profit, total = it.profit + gain, total + gain
      local d = tostring(e.day or "")
      days[d] = (days[d] or 0) + gain
    end
  end
  return { items = items, days = days, total = total }
end

-- ------------------------------------------------------------------ état / API HV
M.cur = {}        -- [itemID] = { units = {..}, listings = { {index, unit, count, buy, owner} }, qty } (dernier scan, session)
M.deals = {}
M.thinList = {}
M.owned = {}
M.sig = nil       -- signatures du dernier scan complet { t, items = {[id] = {[clé] = n}} } (session)
M.ahOpen = false

local function modernAH() local A = _G.C_AuctionHouse return type(A) == "table" and type(A.ReplicateItems) == "function" and A or nil end
local function classicAH() return type(_G.GetAuctionItemInfo) == "function" and type(_G.QueryAuctionItems) == "function" end
function M.Flavor() if classicAH() then return "classic" elseif modernAH() then return "modern" end return nil end

local NEUTRAL_ZONES = { ["baie-du-butin"] = true, ["booty bay"] = true, ["gadgetzan"] = true, ["long-guet"] = true, ["everlook"] = true }
function M.IsNeutral()
  local h = cfg("house", "auto")
  if h == "neutral" then return true elseif h == "faction" then return false end
  for _, f in ipairs({ _G.GetZoneText, _G.GetSubZoneText, _G.GetMinimapZoneText }) do
    local z = try(f)
    if z and NEUTRAL_ZONES[tostring(z):lower()] then return true end
  end
  return false
end
local function hours() return max(2, n0(cfg("hours", 24))) end

function M.RealmData()
  local db = M.db
  if not db then return nil end
  db.realms = db.realms or {}
  local key = (try(_G.GetRealmName) or "?") .. "-" .. (try(_G.UnitFactionGroup, "player") or "?") .. (M.IsNeutral() and "-N" or "")
  local r = db.realms[key]
  if not r then r = { items = {}, n = 0 } db.realms[key] = r end
  return r, key
end

function M.Stats(id)
  local r = M.RealmData()
  local rec = r and r.items[id]
  local st
  if rec then st = F.statsFromPoints(points(rec), (now() - n0(rec.last)) / 86400) end
  if st then st.src = "TreeAI" end
  if not st or st.conf < 0.3 then  -- secours : Auctionator (lecture seule)
    local A = _G.Auctionator
    local api = type(A) == "table" and type(A.API) == "table" and A.API.v1
    if type(api) == "table" then
      local p = try(api.GetAuctionPriceByItemID, "TreeAI", id)
      local age = try(api.GetAuctionAgeByItemID, "TreeAI", id)
      if type(p) == "number" and p > 0 then
        st = { market = floor(p), vol = 0.2, volume = st and st.volume, conf = (age and age <= 1) and 0.4 or 0.25, ndays = 1, src = "Auctionator" }
      end
    end
  end
  return st
end

local function addPoint(rec, day, mn, med, qty, gone, hrs)
  local h = rec.h
  local i = #h - STRIDE + 1
  if i >= 1 and h[i] == day then
    h[i + 1] = min(h[i + 1], mn)
    h[i + 2] = floor((h[i + 2] * h[i + 4] + med) / (h[i + 4] + 1))
    h[i + 3], h[i + 4] = qty, h[i + 4] + 1
    h[i + 5], h[i + 6] = h[i + 5] + gone, h[i + 6] + hrs
  else
    local t = { day, mn, med, qty, 1, gone, hrs }
    for k = 1, STRIDE do h[#h + 1] = t[k] end
  end
  while #h > MAX_DAYS * STRIDE or (#h >= STRIDE and h[1] <= day - MAX_DAYS) do
    for _ = 1, STRIDE do table.remove(h, 1) end
  end
end

function M.Prune()
  local r = M.RealmData()
  if not r then return end
  local cap = max(100, n0(cfg("maxItems", 1500)))
  local list = {}
  for id, rec in pairs(r.items) do list[#list + 1] = { id = id, last = n0(rec.last) } end
  r.n = #list
  if #list <= cap then return end
  table.sort(list, function(a, b) return a.last > b.last end)
  for i = cap + 1, #list do r.items[list[i].id] = nil end
  r.n = cap
end

-- lecture d'une annonce de la liste courante : id, count, buyout, owner, minBid
local function readListing(i, src)
  local r
  if src == "replicate" then
    local A = modernAH()
    r = A and { try(A.GetReplicateItemInfo, i - 1) }
  else
    r = { try(_G.GetAuctionItemInfo, "list", i) }
  end
  if not r or r[1] == nil and r[17] == nil then return nil end
  return tonumber(r[17]), n0(r[3]), n0(r[10]), r[14], n0(r[8])
end

-- ------------------------------------------------------------------ scans
function M.BeginScan(src, full, total)
  M.scan = { src = src, full = full, total = total, i = 0, items = {}, t = now() }
end

function M.ScanChunk(nmax)
  local s = M.scan
  if not s then return true end
  local mine = me()
  local last = min(s.total, s.i + (nmax or 500))
  for i = s.i + 1, last do
    local id, count, buy, owner = readListing(i, s.src)
    if id and count > 0 and buy > 0 then
      local it = s.items[id]
      if not it then it = { units = {}, listings = {}, qty = 0, sig = {} } s.items[id] = it end
      local unit = floor(buy / count)
      local isMine = owner ~= nil and owner == mine
      if not isMine then
        it.units[#it.units + 1] = unit
        it.listings[#it.listings + 1] = { index = i, unit = unit, count = count, buy = buy, owner = owner }
        it.qty = it.qty + count
      end
      local k = unit .. ":" .. count
      it.sig[k] = (it.sig[k] or 0) + count
    end
  end
  s.i = last
  return last >= s.total
end

function M.FinishScan()
  local s = M.scan
  M.scan = nil
  if not s then return end
  local r = M.RealmData()
  local day, t = today(s.t), s.t
  local prev = s.full and M.sig or nil
  local hrs = prev and (t - prev.t) / 3600 or 0
  if hrs < 0.2 or hrs > 48 then prev, hrs = nil, 0 end
  local cur = {}
  for id, it in pairs(s.items) do
    table.sort(it.listings, function(a, b) return a.unit < b.unit end)
    table.sort(it.units)
    cur[id] = it
    if r and #it.units > 0 then
      local rec = r.items[id]
      if not rec then rec = { h = {}, v = vendorPrice(id) } r.items[id] = rec end
      local gone = 0
      if prev and prev.items[id] then
        for k, q in pairs(prev.items[id]) do
          local still = it.sig[k] or 0
          if still < q then gone = gone + (q - still) end
        end
      end
      addPoint(rec, day, it.units[1], F.lowMedian(it.units), it.qty, gone, prev and floor(hrs * 100 + 0.5) / 100 or 0)
      rec.last = t
    end
  end
  if s.full then
    local sig = { t = t, items = {} }
    for id, it in pairs(s.items) do sig.items[id] = it.sig end
    M.sig = sig
    M.lastFull = t
    M.cur = cur
  else
    for id, it in pairs(cur) do M.cur[id] = it end
  end
  M.Prune()
  M.FindDeals(cur)
  M.CheckOwned()
  M.Refresh()
end

function M.Budget()
  local b = n0(cfg("budget", 0))
  local spent = n0(M.char and M.char.spent and M.char.spent[dateKey()])
  local left = API.Money and API.Money() or 0
  if b > 0 then left = min(left, b * 10000 - spent) end
  return max(0, left), spent, b * 10000
end

function M.FindDeals(items)
  local neutral, hrs = M.IsNeutral(), hours()
  local all, thin = {}, {}
  for id, it in pairs(items or M.cur) do
    if #it.listings > 0 then
      local vendor = vendorPrice(id)
      local st = M.Stats(id)
      for li = 1, min(3, #it.listings) do
        local l = it.listings[li]
        local d = F.vendorFlip(l.buy, l.count, vendor)
        if not d and st and st.market and st.market > 0 then
          local target = floor(st.market * (1 - 0.5 * (st.vol or 0)))
          local ahead = 0
          for _, o in ipairs(it.listings) do if o ~= l and o.unit < target then ahead = ahead + o.count end end
          d = F.flip(l.buy, l.count, st, vendor, ahead, neutral, hrs)
          d.thin = F.thin(it.units)
        end
        if d then
          d.id, d.index, d.owner, d.src = id, l.index, l.owner, M.lastSrc
          all[#all + 1] = d
        end
      end
      if F.thin(it.units) and st and st.market and st.market >= 10000 then thin[#thin + 1] = id end
    end
  end
  local budget = M.Budget()
  M.deals = F.rank(all, n0(cfg("minMargin", 20)) / 100, n0(cfg("minProfit", 20)) * 100, budget)
  for i = #M.deals, 31, -1 do M.deals[i] = nil end
  M.thinList = thin
  local d = M.deals[1]
  if d and cfg("tips", true) and M.Suggest then
    M:Suggest("deals", ("HV : %d affaire(s), meilleure %s +%s — /tai hv"):format(#M.deals, itemName(d.id), money(d.profit)), 2, { ttl = 300 })
  end
  if T and T.SetTabLines then  -- « HV » tab of the panel: best deals, click = deal window (buy there, 1 click = 1 action)
    local lines = {}
    for i = 1, math.min(#M.deals, 8) do
      local x = M.deals[i]
      lines[#lines + 1] = { text = ("%s x%d : +%s"):format(itemName(x.id), x.count or 1, money(x.profit)), prio = i == 1 and 2 or 1,
        tooltip = "Clic : fenêtre des affaires (l'achat se fait là, un clic par achat).", onClick = function() M.ShowWindow() end }
    end
    pcall(T.SetTabLines, T, "hv", lines, "Auction")
  end
  return M.deals
end

-- ------------------------------------------------------------------ boutons (UN appel protégé par clic)
local function say(msg) if M.Print then M:Print(msg) end end

-- Classique : retrouve l'annonce (même objet, même pile, même prix, pas à moi) puis UNE enchère au rachat.
function M.BuyClassic(d)
  local idx
  local id, count, buy, owner = readListing(d.index, "list")
  if id == d.id and count == d.count and buy == d.buy and owner ~= me() then
    idx = d.index
  else
    local n = n0(try(_G.GetNumAuctionItems, "list"))
    for i = 1, min(n, 1000) do
      id, count, buy, owner = readListing(i, "list")
      if id == d.id and count == d.count and buy > 0 and buy <= d.buy and owner ~= me() then idx = i break end
    end
  end
  if not idx then
    d.state = "gone"
    say(("%s : annonce introuvable ou prix changé, relance la recherche."):format(itemName(d.id)))
    return false
  end
  local _, _, nbuy = readListing(idx, "list")
  try(_G.PlaceAuctionBid, "list", idx, nbuy)   -- action protégée : dans le OnClick du joueur
  d.state = "bought"
  return true, nbuy
end

-- Moderne : clic 1 = recherche (vérif du prix), clic 2 = achat (objet) ou début (marchandise), clic 3 = confirmer.
function M.BuyModern(d)
  local A = modernAH()
  if not A then return false end
  if not d.state or d.state == "gone" then
    local key = try(A.MakeItemKey, d.id) or { itemID = d.id }
    d.state, M.pendingSearch = "search", d
    try(A.SendSearchQuery, key, {}, true)
    return false
  elseif d.state == "ready" and d.commodity then
    d.state = "starting"
    try(A.StartCommoditiesPurchase, d.id, d.count)
    return false
  elseif d.state == "ready" and d.auctionID then
    try(A.PlaceBid, d.auctionID, d.buyNow or d.buy)
    d.state = "bought"
    return true, d.buyNow or d.buy
  elseif d.state == "confirm" then
    try(A.ConfirmCommoditiesPurchase, d.id, d.count)
    d.state = "bought"
    return true, d.buyNow or d.buy
  end
  return false
end

function M.OnSearchResults(_, event, arg)
  local d = M.pendingSearch
  local A = modernAH()
  if not (d and A) then return end
  M.pendingSearch = nil
  local mine = me()
  if event == "COMMODITY_SEARCH_RESULTS_UPDATED" then
    local r = try(A.GetCommoditySearchResultInfo, d.id, 1)
    if type(r) == "table" and n0(r.unitPrice) > 0 and n0(r.unitPrice) * d.count <= d.buy and n0(r.quantity) >= d.count then
      d.state, d.commodity, d.buyNow = "ready", true, n0(r.unitPrice) * d.count
    else d.state = "gone" end
  else
    local key = try(A.MakeItemKey, d.id) or { itemID = d.id }
    d.state = "gone"
    for i = 1, min(50, n0(try(A.GetNumItemSearchResults, key))) do
      local r = try(A.GetItemSearchResultInfo, key, i)
      local owned = type(r) == "table" and type(r.owners) == "table" and r.owners[1] == mine
      if type(r) == "table" and not owned and n0(r.quantity) == d.count and n0(r.buyoutAmount) > 0 and n0(r.buyoutAmount) <= d.buy then
        d.state, d.auctionID, d.buyNow = "ready", r.auctionID, n0(r.buyoutAmount)
        break
      end
    end
  end
  if d.state == "gone" then say(("%s : prix changé, plus d'affaire."):format(itemName(d.id))) end
  M.Refresh()
end

function M.COMMODITY_PRICE_UPDATED(_, _, unitPrice, total)
  for _, d in ipairs(M.deals) do
    if d.state == "starting" then
      if n0(total) > 0 and n0(total) <= d.buy then
        d.state, d.buyNow = "confirm", n0(total)
      else
        local A = modernAH()
        if A then try(A.CancelCommoditiesPurchase) end
        d.state = "gone"
        say(("%s : prix monté à %s, achat annulé."):format(itemName(d.id), money(total)))
      end
    end
  end
  M.Refresh()
end

function M.Buy(d)
  if not (d and M.ahOpen) then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if d.state == "bought" then return false end
  local ok, paid
  if M.Flavor() == "classic" then ok, paid = M.BuyClassic(d) else ok, paid = M.BuyModern(d) end
  if ok and M.char then
    local k = dateKey()
    M.char.spent = M.char.spent or {}
    M.char.spent[k] = n0(M.char.spent[k]) + n0(paid)
    say(("Acheté : %s x%d pour %s (profit attendu %s)."):format(itemName(d.id), d.count, money(paid), money(d.profit)))
  end
  M.Refresh()
  return ok
end

function M.ScanAll()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  local fl = M.Flavor()
  if fl == "classic" then
    local _, canAll = try(_G.CanSendAuctionQuery)
    if not canAll then
      local wait = M.lastFull and max(0, 900 - (now() - M.lastFull)) or 0
      say(("Scan complet pas encore disponible (Blizzard : 1 fois / 15 min%s)."):format(wait > 0 and (", encore " .. ceil(wait / 60) .. " min") or ""))
      return false
    end
    M.pendingAll = true
    try(_G.QueryAuctionItems, "", nil, nil, 0, false, -1, true, false, nil)
  elseif fl == "modern" then
    M.pendingAll = true
    try(modernAH().ReplicateItems)
  else
    say("API de l'hôtel des ventes introuvable.")
    return false
  end
  say("Scan complet demandé…")
  return true
end

local function processAsync(src, full, total)
  M.lastSrc = src
  M.BeginScan(src, full, total)
  local function step()
    if not M.scan then return end
    if M.ScanChunk(500) then M.FinishScan() else T:After(0.01, step) end
  end
  step()
end

function M.AUCTION_ITEM_LIST_UPDATE()
  if M.scan then return end
  local batch, total = try(_G.GetNumAuctionItems, "list")
  batch, total = n0(batch), n0(total)
  if M.pendingAll then
    M.pendingAll = false
    processAsync("list", true, batch)
  elseif batch > 0 and batch <= 50 then
    -- page parcourue par le joueur : affaires sur la page ; historique seulement si résultat complet
    M.lastSrc = "list"
    M.BeginScan("list", false, batch)
    M.ScanChunk(batch)
    if total > batch then
      local items = M.scan.items
      M.scan = nil
      for id, it in pairs(items) do
        table.sort(it.listings, function(a, b) return a.unit < b.unit end)
        table.sort(it.units)
        M.cur[id] = it
      end
      M.FindDeals(items)
      M.Refresh()
    else
      M.FinishScan()
    end
  end
end

function M.REPLICATE_ITEM_LIST_UPDATE()
  if M.scan or not M.pendingAll then return end
  M.pendingAll = false
  local A = modernAH()
  processAsync("replicate", true, n0(A and try(A.GetNumReplicateItems)))
end

-- ------------------------------------------------------------------ mes enchères
function M.CheckOwned()
  local out, mine = {}, {}
  local A = modernAH()
  if M.Flavor() == "classic" then
    for i = 1, min(200, n0(try(_G.GetNumAuctionItems, "owner"))) do
      local r = { try(_G.GetAuctionItemInfo, "owner", i) }
      if r[17] and n0(r[16]) == 0 and n0(r[10]) > 0 then
        mine[#mine + 1] = { index = i, id = tonumber(r[17]), count = max(1, n0(r[3])), buy = n0(r[10]) }
      end
    end
  elseif A and A.GetNumOwnedAuctions then
    for i = 1, min(200, n0(try(A.GetNumOwnedAuctions))) do
      local r = try(A.GetOwnedAuctionInfo, i)
      if type(r) == "table" and n0(r.status) == 0 and n0(r.buyoutAmount) > 0 then
        local id = type(r.itemKey) == "table" and r.itemKey.itemID or idFromLink(r.itemLink)
        mine[#mine + 1] = { auctionID = r.auctionID, id = id, count = max(1, n0(r.quantity)), buy = n0(r.buyoutAmount) }
      end
    end
  end
  local neutral, hrs = M.IsNeutral(), hours()
  for _, a in ipairs(mine) do
    a.unit = floor(a.buy / a.count)
    local it = a.id and M.cur[a.id]
    local low = it and it.units[1]
    if low and low < a.unit then
      local comp = {}
      for _, l in ipairs(it.listings) do comp[#comp + 1] = { l.unit, l.count } end
      local s = F.suggestPost(a.count, vendorPrice(a.id), M.Stats(a.id), comp, neutral, hrs)
      a.undercut, a.lowest, a.repost = true, low, s.unit
      a.depositLost = F.deposit(vendorPrice(a.id), a.count, hrs, neutral)
      -- annuler vaut le coup si le gain de vendre (au lieu de rester derrière) dépasse le dépôt perdu
      a.worth = s.unit and s.better == "ah" and F.net(s.unit * a.count, neutral) * 0.5 > a.depositLost or false
    end
    out[#out + 1] = a
  end
  M.owned = out
  local n = 0
  for _, a in ipairs(out) do if a.undercut then n = n + 1 end end
  if n > 0 and M.Suggest then M:Suggest("undercut", ("HV : %d de tes enchères sont sapées — /tai hv"):format(n), 2, { ttl = 300 }) end
  return out
end

function M.Cancel(a)
  if not (a and M.ahOpen) then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if a.state == "cancelled" then return false end
  if M.Flavor() == "classic" then
    local r = { try(_G.GetAuctionItemInfo, "owner", a.index) }
    if tonumber(r[17]) ~= a.id or n0(r[3]) ~= a.count or n0(r[10]) ~= a.buy then
      say("Liste de tes enchères changée : rouvre l'onglet Enchères.")
      return false
    end
    try(_G.CancelAuction, a.index)   -- action protégée : dans le OnClick du joueur
  else
    local A = modernAH()
    if not (A and a.auctionID) then return false end
    try(A.CancelAuction, a.auctionID)
  end
  a.state = "cancelled"
  say(("Annulée : %s. Reposte à %s/u."):format(itemName(a.id), money(a.repost)))
  M.Refresh()
  return true
end

-- ------------------------------------------------------------------ vente (sacs)
function M.SellAdvice()
  local bag = {}
  if API.IterBags then
    for _, _, item in API.IterBags() do
      if item and item.itemID and not item.noValue and (item.quality or 1) >= 1 then
        bag[item.itemID] = (bag[item.itemID] or 0) + (item.count or 1)
      end
    end
  end
  local out, neutral, hrs = {}, M.IsNeutral(), hours()
  for id, count in pairs(bag) do
    local st = M.Stats(id)
    local it = M.cur[id]
    if st or it then
      local comp = {}
      if it then for _, l in ipairs(it.listings) do comp[#comp + 1] = { l.unit, l.count } end end
      local stack = select(8, M.ItemInfo(id))
      local c = min(count, max(1, n0(stack)))
      local s = F.suggestPost(c, vendorPrice(id), st, comp, neutral, hrs)
      s.id, s.have = id, count
      if s.unit or s.vendor > 0 then out[#out + 1] = s end
    end
  end
  table.sort(out, function(a, b) return max(n0(a.net), a.vendor) > max(n0(b.net), b.vendor) end)
  M.sell = out
  return out
end

function M.SellText(s)
  local name = itemName(s.id)
  if not s.unit then return ("%s : pas de prix de marché (marchand %s)"):format(name, money(s.vendor)) end
  if s.better == "vendor" then
    return ("%s : mieux au marchand (%s ; HV net %s)"):format(name, money(s.vendor), money(s.net))
  end
  local t = ("%s x%d : %s/u (dépôt %s, net %s, marchand %s)"):format(name, s.count, money(s.unit), money(s.deposit), money(s.net), money(s.vendor))
  if s.split then t = t .. (" — piles de %d"):format(s.split) end
  return t
end

-- objet posé dans l'emplacement de vente (HV classique) → prix conseillé immédiat
function M.NEW_AUCTION_UPDATE()
  local name, _, count, _, _, vendor = try(_G.GetAuctionSellItemInfo)
  if not name then if M.Clear then M:Clear("post") end return end
  local link = select(2, M.ItemInfo(name))
  local id = idFromLink(link)
  if not id then return end
  local it = M.cur[id]
  local comp = {}
  if it then for _, l in ipairs(it.listings) do comp[#comp + 1] = { l.unit, l.count } end end
  local unitVendor = n0(vendor) > 0 and floor(n0(vendor) / max(1, n0(count))) or vendorPrice(id)
  local s = F.suggestPost(max(1, n0(count)), unitVendor, M.Stats(id), comp, M.IsNeutral(), hours())
  local dep = try(_G.CalculateAuctionDeposit, hours() * 60, max(1, n0(count)))
  if n0(dep) > 0 then s.deposit = n0(dep) end
  s.id = id
  if M.Suggest then M:Suggest("post", "Vente : " .. M.SellText(s), 2, { ttl = 120 }) end
  return s
end

-- ------------------------------------------------------------------ journal (courrier de l'HV)
local function toPattern(fmt)
  if type(fmt) ~= "string" or not fmt:find("%%s") then return nil end
  local p = fmt:gsub("([%(%)%.%+%-%*%?%[%]%^%$])", "%%%1"):gsub("%%s", "(.+)")
  return "^" .. p .. "$"
end
local SUBJECTS = {
  sell = { "AUCTION_SOLD_MAIL_SUBJECT", "Auction successful: %s", "Vente aux enchères réussie : %s", "Vente réussie : %s" },
  buy = { "AUCTION_WON_MAIL_SUBJECT", "Auction won: %s", "Objet remporté : %s", "Enchère remportée : %s" },
  expired = { "AUCTION_EXPIRED_MAIL_SUBJECT", "Auction expired: %s", "Vente aux enchères expirée : %s", "Enchère expirée : %s" },
  cancelled = { "AUCTION_REMOVED_MAIL_SUBJECT", "Auction cancelled: %s", "Vente aux enchères annulée : %s" },
}
function M.MailKind(subject)
  if type(subject) ~= "string" then return nil end
  for kind, list in pairs(SUBJECTS) do
    for i, f in ipairs(list) do
      local pat = toPattern(i == 1 and rawget(_G, f) or f)
      local item = pat and subject:match(pat)
      if item then
        local name, q = item:match("^(.-)%s*%((%d+)%)$")
        return kind, name or item, tonumber(q)
      end
    end
  end
  return nil
end

function M.MAIL_INBOX_UPDATE()
  local ch = M.char
  if not ch then return end
  ch.ledger, ch.seen = ch.ledger or {}, ch.seen or {}
  local t = now()
  local n = min(100, n0(try(_G.GetInboxNumItems)))
  for i = 1, n do
    local _, _, _, subject, mmoney, _, daysLeft = try(_G.GetInboxHeaderInfo, i)
    local kind, name, qty = M.MailKind(subject)
    if kind then
      local arrived = floor((t - (30 - n0(daysLeft)) * 86400) / 7200)  -- tranche de 2 h, stable
      local key = subject .. "|" .. n0(mmoney) .. "|" .. arrived
      local dup = ch.seen[key] or ch.seen[subject .. "|" .. n0(mmoney) .. "|" .. (arrived - 1)] or ch.seen[subject .. "|" .. n0(mmoney) .. "|" .. (arrived + 1)]
      if not dup then
        ch.seen[key] = t
        local itype, iname, _, bid, _, deposit, cut = try(_G.GetInboxInvoiceInfo, i)
        local _, itemID, _, icount = try(_G.GetInboxItem, i, 1)
        local e = { t = t, day = dateKey(t), item = iname or name, type = kind, qty = qty or (n0(icount) > 0 and icount) or 1 }
        if kind == "sell" then
          if itype == "seller" and n0(bid) > 0 then e.money, e.cut = n0(bid), n0(cut)
          else e.money, e.cut = n0(mmoney), 0 end
        elseif kind == "buy" then
          e.money = n0(bid) > 0 and n0(bid) or 0
          e.id = itemID
        end
        ch.ledger[#ch.ledger + 1] = e
      end
    end
  end
  while #ch.ledger > 500 do table.remove(ch.ledger, 1) end
  for k, v in pairs(ch.seen) do if t - n0(v) > 32 * 86400 then ch.seen[k] = nil end end
end

function M.Realized()
  local ev = (M.char and M.char.ledger) or {}
  local r = F.realized(ev)
  local t = now()
  local week = 0
  for i = 0, 6 do week = week + n0(r.days[dateKey(t - i * 86400)]) end
  r.today, r.week = n0(r.days[dateKey(t)]), week
  return r
end

-- ------------------------------------------------------------------ texte / rapport
function M.DealText(d)
  local name = itemName(d.id)
  local lbl = d.count > 1 and (name .. " x" .. d.count) or name
  if d.kind == "vendor" then
    return ("%s : %s → marchand %s = +%s garanti"):format(lbl, money(d.buy), money(d.sell), money(d.profit))
  end
  return ("%s : %s (−%d %%) → revendre %s = +%s, ROI %d %%, conf. %d %%%s"):format(lbl, money(d.buy), floor(d.discount * 100 + 0.5),
    money(d.sell), money(d.profit), floor((d.roi or 0) * 100 + 0.5), floor(d.conf * 100 + 0.5), d.thin and " ⚠ marché mince" or "")
end

function M.Report(what)
  what = tostring(what or "")
  local P = function(s) say(s) end
  if what:find("^vend") then
    local s = M.SellAdvice()
    if #s == 0 then P("Rien à conseiller : scanne l'HV d'abord (bouton « Scan complet »).") end
    for i = 1, min(8, #s) do P(M.SellText(s[i])) end
    return
  end
  if what:find("^jour") or what:find("^ledger") then
    local r = M.Realized()
    P(("Profit réalisé : aujourd'hui %s, 7 j %s, total %s."):format(money(r.today), money(r.week), money(r.total)))
    local list = {}
    for k, v in pairs(r.items) do list[#list + 1] = { k, v.profit } end
    table.sort(list, function(a, b) return a[2] > b[2] end)
    for i = 1, min(5, #list) do P(("  %s : %s"):format(list[i][1], money(list[i][2]))) end
    return
  end
  local left, spent, b = M.Budget()
  local age = M.lastFull and ceil((now() - M.lastFull) / 60)
  P(("HV %s · dernier scan complet : %s · budget restant %s%s"):format(M.IsNeutral() and "neutre (15 %)" or "de faction (5 %)",
    age and ("il y a " .. age .. " min") or "jamais", money(left), b > 0 and (" (dépensé " .. money(spent) .. ")") or ""))
  if #M.deals == 0 then P("Aucune affaire pour l'instant : ouvre l'HV, « Scan complet », ou parcours des pages.") end
  for i = 1, min(6, #M.deals) do P(i .. ". " .. M.DealText(M.deals[i])) end
  local nb = 0
  for _, a in ipairs(M.owned) do if a.undercut then nb = nb + 1 end end
  if nb > 0 then P(nb .. " enchère(s) sapée(s) : onglet « Mes enchères ».") end
end

-- ------------------------------------------------------------------ fenêtre
local ROWS = 10
M.tab, M.page = "deals", 1

local function rowsFor(tab)
  if tab == "deals" then return M.deals
  elseif tab == "sell" then return M.sell or {}
  elseif tab == "owned" then return M.owned
  end
  local out = {}
  local ev = (M.char and M.char.ledger) or {}
  for i = #ev, max(1, #ev - 40), -1 do out[#out + 1] = ev[i] end
  return out
end

local KIND = { buy = "acheté", sell = "vendu", expired = "expiré", cancelled = "annulé" }
local function rowText(tab, e)
  if tab == "deals" then return M.DealText(e)
  elseif tab == "sell" then return M.SellText(e)
  elseif tab == "owned" then
    local t = ("%s x%d : %s/u"):format(itemName(e.id), e.count, money(e.unit))
    if e.undercut then t = t .. (" — sapé (%s) → reposter %s, dépôt perdu %s"):format(money(e.lowest), money(e.repost), money(e.depositLost)) end
    return t
  end
  return ("%s %s x%d %s"):format(e.day or "", KIND[e.type] or e.type or "", n0(e.qty), money(n0(e.money) - n0(e.cut)))
end

local function buttonLabel(tab, e)
  if tab == "deals" then
    if e.state == "bought" then return "Acheté", false end
    if e.state == "gone" then return "Disparu", M.Flavor() == "modern" end
    if e.state == "search" or e.state == "starting" then return "…", false end
    if e.state == "confirm" then return "Confirmer", true end
    if M.Flavor() == "modern" and e.state ~= "ready" then return "Vérifier", M.ahOpen end
    return "Acheter", M.ahOpen
  elseif tab == "owned" and e.undercut then
    if e.state == "cancelled" then return "Annulée", false end
    return "Annuler", M.ahOpen
  end
  return nil
end

function M.CreateWindow()
  if M.win or not _G.CreateFrame then return M.win end
  local parent = _G.UIParent
  local f = _G.CreateFrame("Frame", nil, parent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  M.win = f
  f:SetSize(520, 340)
  local af = rawget(_G, "AuctionFrame") or rawget(_G, "AuctionHouseFrame")
  if af then f:SetPoint("TOPLEFT", af, "TOPRIGHT", 4, 0) else f:SetPoint("CENTER", parent, "CENTER", 0, 0) end
  f:SetFrameStrata("HIGH")
  f:EnableMouse(true)
  f:SetMovable(true)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving)
  f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.SetBackdrop then
    f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 12, insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    if f.SetBackdropColor then f:SetBackdropColor(0, 0, 0, 0.85) end
  end
  local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", 10, -8)
  title:SetText("TreeAI — Hôtel des ventes")
  local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 0, 0)
  local tabs = { { "deals", "Affaires" }, { "sell", "Vendre" }, { "owned", "Mes enchères" }, { "ledger", "Journal" } }
  for i, tb in ipairs(tabs) do
    local b = _G.CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
    b:SetSize(90, 20)
    b:SetPoint("TOPLEFT", 10 + (i - 1) * 94, -28)
    b:SetText(tb[2])
    b:SetScript("OnClick", function()
      M.tab, M.page = tb[1], 1
      if tb[1] == "sell" then M.SellAdvice() elseif tb[1] == "owned" then M.CheckOwned() end
      M.Refresh()
    end)
  end
  local scan = _G.CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  scan:SetSize(110, 20)
  scan:SetPoint("TOPRIGHT", -28, -28)
  scan:SetText("Scan complet")
  scan:SetScript("OnClick", function() M.ScanAll() end)
  f.scanBtn = scan
  f.rows = {}
  for i = 1, ROWS do
    local row = _G.CreateFrame("Frame", nil, f)
    row:SetSize(500, 24)
    row:SetPoint("TOPLEFT", 10, -54 - (i - 1) * 25)
    row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.text:SetPoint("LEFT", 0, 0)
    row.text:SetWidth(410)
    if row.text.SetJustifyH then row.text:SetJustifyH("LEFT") end
    row.btn = _G.CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.btn:SetSize(80, 20)
    row.btn:SetPoint("RIGHT", 0, 0)
    row.btn:SetScript("OnClick", function()   -- clic du joueur = UNE action
      local e = row.entry
      if not e then return end
      if M.tab == "deals" then M.Buy(e) elseif M.tab == "owned" then M.Cancel(e) end
    end)
    f.rows[i] = row
  end
  local prev = _G.CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  prev:SetSize(24, 20) prev:SetPoint("BOTTOMLEFT", 10, 8) prev:SetText("<")
  prev:SetScript("OnClick", function() M.page = max(1, M.page - 1) M.Refresh() end)
  local nxt = _G.CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  nxt:SetSize(24, 20) nxt:SetPoint("LEFT", prev, "RIGHT", 4, 0) nxt:SetText(">")
  nxt:SetScript("OnClick", function() M.page = M.page + 1 M.Refresh() end)
  f.status = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  f.status:SetPoint("LEFT", nxt, "RIGHT", 8, 0)
  f:Hide()
  return f
end

function M.Refresh()
  local f = M.win
  if not (f and f.rows) then return end
  local list = rowsFor(M.tab)
  local pages = max(1, ceil(#list / ROWS))
  M.page = min(M.page, pages)
  for i = 1, ROWS do
    local row, e = f.rows[i], list[(M.page - 1) * ROWS + i]
    row.entry = e
    if e then
      row.text:SetText(rowText(M.tab, e))
      local lbl, enabled = buttonLabel(M.tab, e)
      if lbl then
        row.btn:SetText(lbl)
        if row.btn.SetEnabled then row.btn:SetEnabled(enabled and true or false) end
        row.btn:Show()
      else row.btn:Hide() end
      row:Show()
    else
      row:Hide()
    end
  end
  local left = M.Budget()
  local r = M.Realized()
  f.status:SetText(("Page %d/%d · budget %s · profit aujourd'hui %s"):format(M.page, pages, money(left), money(r.today)))
end

function M.ShowWindow()
  local f = M.CreateWindow()
  if f then f:Show() M.Refresh() end
end

-- ------------------------------------------------------------------ événements / cycle de vie
function M.AUCTION_HOUSE_SHOW()
  M.ahOpen = true
  if cfg("window", true) then M.ShowWindow() end
  local A = modernAH()
  if A and A.QueryOwnedAuctions then try(A.QueryOwnedAuctions, {}) end
  M.CheckOwned()
end
function M.AUCTION_HOUSE_CLOSED()
  M.ahOpen, M.pendingAll, M.pendingSearch = false, false, nil
  for _, d in ipairs(M.deals) do if d.state ~= "bought" then d.state = nil end end
  if M.win then M.win:Hide() end
end

function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local deals = {}
  for i = 1, min(5, #M.deals) do
    local d = M.deals[i]
    deals[i] = { name = itemName(d.id), id = d.id, kind = d.kind, count = d.count, buy = d.buy, sell = d.sell,
      profit = d.profit, roi = d.roi and floor(d.roi * 100 + 0.5) / 100, conf = d.conf, days = d.days }
  end
  local sell = {}
  for i, a in ipairs(M.sell or {}) do
    if i > 5 then break end
    sell[i] = { name = itemName(a.id), id = a.id, count = a.count, unit = a.unit, net = a.net, vendor = a.vendor,
      deposit = a.deposit, better = a.better, split = a.split }
  end
  local thin = {}
  for i, id in ipairs(M.thinList) do if i > 5 then break end thin[i] = itemName(id) end
  local r = M.Realized()
  local top = {}
  for k, v in pairs(r.items) do top[#top + 1] = { item = k, profit = v.profit } end
  table.sort(top, function(a, b) return a.profit > b.profit end)
  for i = #top, 6, -1 do top[i] = nil end
  local left, spent, b = M.Budget()
  local rd = M.RealmData()
  s.auction = { updated = now(), neutral = M.IsNeutral(), flavor = M.Flavor(),
    scanAge = M.lastFull and (now() - M.lastFull) or nil, tracked = rd and rd.n or 0,
    deals = deals, sell = sell, thin = thin, budget = b > 0 and b or nil, budgetLeft = left, spentToday = spent,
    realized = { today = r.today, week = r.week, total = r.total, top = top } }
end

function M:OnInit()
  if M.char then
    M.char.ledger = M.char.ledger or {}
    M.char.seen = M.char.seen or {}
    M.char.spent = M.char.spent or {}
    for k in pairs(M.char.spent) do if k < dateKey(now() - 14 * 86400) then M.char.spent[k] = nil end end
  end
end

function M:OnEnable()
  for _, ev in ipairs({ "AUCTION_HOUSE_SHOW", "AUCTION_HOUSE_CLOSED", "AUCTION_ITEM_LIST_UPDATE", "REPLICATE_ITEM_LIST_UPDATE",
    "NEW_AUCTION_UPDATE", "MAIL_INBOX_UPDATE", "COMMODITY_PRICE_UPDATED" }) do
    self:RegisterEvent(ev)
  end
  self:RegisterEvent("ITEM_SEARCH_RESULTS_UPDATED", M.OnSearchResults)
  self:RegisterEvent("COMMODITY_SEARCH_RESULTS_UPDATED", M.OnSearchResults)
  self:RegisterEvent("AUCTION_OWNED_LIST_UPDATE", function() M.CheckOwned() M.Refresh() end)
  self:RegisterEvent("OWNED_AUCTIONS_UPDATED", function() M.CheckOwned() M.Refresh() end)
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
end

local function slash(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if rest == "neutre" or rest == "faction" or rest == "auto" then
    if M.db then M.db.house = (rest == "neutre" and "neutral") or rest end
    say("HV : " .. (M.IsNeutral() and "neutre (commission 15 %)" or "de faction (commission 5 %)"))
    return
  end
  if rest == "fenetre" or rest == "fenêtre" or rest == "window" then M.ShowWindow() return end
  if rest == "aide" or rest == "help" then
    say("/tai hv [vendre | journal | fenetre | neutre | faction | auto] — achats/ventes : boutons de la fenêtre (1 clic = 1 action).")
    return
  end
  M.Report(rest)
  if M.ahOpen then M.ShowWindow() end
end
T:RegisterSlash("hv", slash, "hôtel des ventes : affaires, vendre, journal, profit")
T:RegisterSlash("ah", slash)
