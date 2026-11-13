-- TreeAI — module Hôtel des ventes (achat-revente). Conseille, calcule, n'automatise RIEN :
--  * chaque achat / mise en vente / annulation = UN clic du joueur (« Acheter », « Annuler » ou le gros
--    bouton « Suivant ») : l'appel protégé est fait DANS ce OnClick, au plus UN par clic, jamais de boucle
--    ni de minuterie qui agit ; le prix est revérifié juste avant (sinon l'étape est sautée).
--  * API HV détectée à l'exécution (comme les addons d'HV le font) : WoW Forever (16001) expose
--    C_AuctionHouse (ReplicateItems, SendSearchQuery, PlaceBid, PostItem/PostCommodity, événements
--    REPLICATE_ITEM_LIST_UPDATE / *_SEARCH_RESULTS_UPDATED / AUCTION_HOUSE_THROTTLED_SYSTEM_READY) ;
--    l'ancienne API (QueryAuctionItems / GetAuctionItemInfo / PlaceAuctionBid) reste gérée (Classic Era).
--  * scan : complet = ReplicateItems (1 fois / 15 min, compte entier, partagé avec Auctionator : on relit
--    aussi le scan complet qu'Auctionator vient de faire) ou getAll ; traitement par tranches ≤ 4 ms par
--    image (coroutine) ; infos d'objet manquantes = résolues plus tard (jamais d'attente) ; mini-scan ciblé
--    (liste d'achats / de ventes) au rythme du limiteur Blizzard ; lecture passive de ce que le joueur
--    parcourt (parcours, recherche, pages).
--  * liquidité (F.liquidity) : un achat-revente n'est proposé que s'il y a la PREUVE que l'objet se vend
--    (ventes mesurées entre scans, plusieurs jours, prix stable, plusieurs vendeurs, Auctionator d'accord) ;
--    sinon « Aucun achat-revente sûr pour l'instant » + pourquoi. Règles dures : ≤ capGold % de l'or par
--    objet, quantité ≤ ventes de qtyDays jours, stop-loss −15 %. Seuils et audit 4.1 : research/ahprofit.md.
--  * prix : historique par objet (≤ 14 jours) → marché (médiane robuste), volatilité, volume/jour,
--    confiance ; Auctionator.API.v1 (callerID « TreeAI », lecture seule) en secours / quand plus frais.
--  * frais exacts (règle Classic, miroir de treeaicoach/wow/auction.py) : commission 5 % / 15 % neutre ;
--    dépôt = floor(prix marchand × qté × 5 % (25 %) × h / 2) ; profit = vente × (1 − commission) − achat
--    − dépôt × P(invendu) ; risque de remise en vente : dépôt perdu à chaque cycle invendu.
--  * gestion de l'or : réserve (entraînement / réparations / monture), budget de revente, répartition
--    sur les meilleures affaires (glouton profit ajusté / risque, plafond par objet), file « Suivant ».
-- Résumé : TreeAIDB.chars[perso].summary.auction. Lua 5.1 ; aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Auction", {
  title = "Hôtel des ventes",
  defaults = { enabled = true, minMargin = 15, minProfit = 1, budget = 0, hours = 24, maxItems = 1500,
               house = "auto", window = true, tips = true, keepGold = 0, mountGold = 0, autoReserve = true,
               capGold = 20, qtyDays = 3, api = "auto", undercutPct = 0 },
  charDefaults = { ledger = {}, seen = {}, spent = {}, deposits = {}, flips = {} },
  options = {
    { key = "window", type = "toggle", label = "Fenêtre d'aide à l'ouverture de l'HV" },
    { key = "tips", type = "toggle", label = "Annoncer les affaires dans « Maintenant »" },
    { key = "minMargin", type = "range", label = "Acheter seulement sous le marché de (%)", min = 5, max = 60, step = 5 },
    { key = "minProfit", type = "range", label = "Bénéfice net minimal par achat (pa ; et toujours ≥ 15 % de l'achat)", min = 1, max = 100, step = 1 },
    { key = "undercutPct", type = "range", label = "Sous-coter le moins cher de (% ; 0 = 1 pc dessous)", min = 0, max = 10, step = 1 },
    { key = "budget", type = "range", label = "Budget d'achat par jour (po, 0 = illimité)", min = 0, max = 5000, step = 10 },
    { key = "hours", type = "range", label = "Durée de mise en vente (12 / 24 / 48 h)", min = 12, max = 48, step = 12 },
    { key = "keepGold", type = "range", label = "Or à garder quoi qu'il arrive (po)", min = 0, max = 2000, step = 5 },
    { key = "autoReserve", type = "toggle", label = "Réserver l'entraînement et les réparations (selon le niveau)" },
    { key = "mountGold", type = "range", label = "Réserve monture (po)", min = 0, max = 1000, step = 10 },
    { key = "capGold", type = "range", label = "Part max de ton or dans un même objet (%)", min = 5, max = 50, step = 5 },
    { key = "qtyDays", type = "range", label = "Acheter au plus ce qui s'est vendu en (jours)", min = 1, max = 7, step = 1 },
    { key = "maxItems", type = "range", label = "Objets suivis au maximum", min = 300, max = 5000, step = 100 },
  },
})
ns.Auction = M

local API = T.API or {}
_G.BINDING_HEADER_TREEAI = _G.BINDING_HEADER_TREEAI or "TreeAI"
_G.BINDING_NAME_TREEAI_AH_NEXT = "TreeAI : action HV suivante"
local floor, ceil, max, min, abs = math.floor, math.ceil, math.max, math.min, math.abs
local unpack = unpack or table.unpack

-- ------------------------------------------------------------------ utilitaires
-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
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
-- rec.sl = { jour, vendeurs, jour, vendeurs, ... } (vendeurs distincts, ou niveaux de prix distincts si inconnus)
local function points(rec)
  local out, h = {}, rec and rec.h or {}
  local sel, sl = {}, rec and rec.sl
  if sl then for i = 1, #sl - 1, 2 do sel[sl[i]] = sl[i + 1] end end
  for i = 1, #h - STRIDE + 1, STRIDE do
    if n0(h[i + 2]) > 0 then
      out[#out + 1] = { day = h[i], min = h[i + 1], med = h[i + 2], qty = h[i + 3], n = h[i + 4], gone = h[i + 5], hrs = h[i + 6],
        sel = sel[h[i]] or 0 }
    end
  end
  return out
end
M.Points = points
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
  -- (4.1) volume inconnu tant que les scans ne couvrent pas ≥ 12 h (« 0 vendu en 20 min » ne prouve rien)
  local volume = hrs >= 12 and floor(gone / hrs * 24 * SOLD_SHARE * 100 + 0.5) / 100 or nil
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
-- (4.1 AHPROFIT) seuil d'un achat-revente (HV ou marchand) : bénéfice net ≥ max(1 pa, 15 % de l'achat)
F.MIN_NET, F.MIN_ROI = 100, 0.15
function F.minNet(buy, minProfit)
  local m = n0(minProfit)
  return max(m > 0 and m or F.MIN_NET, ceil(max(0, n0(buy)) * F.MIN_ROI - 1e-9))
end
function F.rank(deals, minMargin, minProfit, budget)
  local out = {}
  for _, d in ipairs(deals) do
    if not budget or d.buy <= budget then
      if d.kind == "vendor" then
        if d.profit >= F.minNet(d.buy, minProfit) then out[#out + 1] = d end
      elseif d.discount >= minMargin and d.adj >= minProfit and d.profit >= F.minNet(d.buy, minProfit) then
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
-- comp = { {unit, count}, ... } des AUTRES vendeurs ; undercutPct : 0 = 1 pc sous le moins cher, sinon N %
-- (4.1 AHPROFIT) ev = espérance honnête : vendu (proba p) → net ; invendu → l'objet revient, dépôt perdu, puis marchand
function F.undercut(low, pct)
  low = n0(low)
  return low - max(1, floor(low * max(0, n0(pct)) / 100))
end
function F.suggestPost(count, vendor, st, comp, neutral, hours, undercutPct)
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
    unit = F.undercut(comp[1][1], undercutPct)
    res.reason = n0(undercutPct) > 0 and (n0(undercutPct) .. " % sous le moins cher") or "1 pc sous le moins cher"
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
  res.ev = floor(p * F.net(unit * count, neutral) + (1 - p) * (vt - res.deposit) + 0.5)
  res.better = vt >= res.ev and "vendor" or "ah"
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

-- répartition de l'or : glouton par profit ajusté (confiance × P(vente)) / prix, plafond par objet
-- (diversification), reventes au marchand (sans risque) hors plafond. Renvoie plan, dépense, gain attendu.
function F.allocate(deals, budget, capShare, maxPer, capAbs)
  budget = max(0, n0(budget))
  local list = {}
  for _, d in ipairs(deals or {}) do
    if n0(d.adj) > 0 and n0(d.buy) > 0 and d.state ~= "bought" and d.state ~= "gone" then list[#list + 1] = d end
  end
  table.sort(list, function(a, b)
    local ra, rb = a.adj / a.buy, b.adj / b.buy
    if ra ~= rb then return ra > rb end
    return a.adj > b.adj
  end)
  local left, per, perN, perU, out, gain = budget, {}, {}, {}, {}, 0
  local cap = capAbs or max(1, floor(budget * (capShare or 0.35)))
  for _, d in ipairs(list) do
    local id = d.id or 0
    local lim = d.kind == "vendor" and budget or cap
    -- (4.1 AHPROFIT) quantité totale par objet ≤ la demande observée (d.maxQty = ventes de qtyDays jours − stock)
    local demandOk = d.kind == "vendor" or d.maxQty == nil or n0(perU[id]) + max(1, n0(d.count)) <= n0(d.maxQty)
    if d.buy <= left and n0(per[id]) + d.buy <= lim and n0(perN[id]) < (maxPer or 4) and demandOk then
      out[#out + 1] = d
      left, gain = left - d.buy, gain + d.adj
      per[id], perN[id], perU[id] = n0(per[id]) + d.buy, n0(perN[id]) + 1, n0(perU[id]) + max(1, n0(d.count))
    end
  end
  return out, budget - left, gain
end
-- dépôt perdu attendu si l'on remet en vente jusqu'à la vente (cycles géométriques, P(vente) = p par cycle)
function F.relistLoss(dep, p)
  p = clamp(n0(p), 0.05, 1)
  return floor(n0(dep) * (1 - p) / p + 0.5)
end


-- ------------------------------------------------------------------ liquidité : « est-ce que ça se vend ? »
-- Miroir exact de auction.py (liquidity / safe_flip / sold_between…) ; seuils expliqués dans research/ahprofit.md.
local LIQ = { minDays = 3, minDaysDemand = 2, minScans = 4, minHours = 12, minSold = 1.0, minSoldSingle = 0.5,
  minSellers = 3, maxVol = 0.30, atrGap = 0.35, staleDays = 3, minConf = 0.70, maxTts = 3.0, minRoi = 0.15,
  qtyDays = 3, stopPct = 15, capPct = 20 }
F.LIQ = LIQ
F.NONE_TEXT = "Aucun achat-revente sûr pour l'instant"
local REASON_LABEL = {
  nohist = "jamais vu en scan", rare = "objet rare, presque jamais vendu", short = "historique trop court",
  scans = "trop peu de scans", unmeasured = "ventes pas encore mesurées", slow = "se vend trop peu",
  unstable = "prix instable", falling = "prix en chute", sellers = "trop peu de vendeurs",
  atr = "Auctionator pas d'accord", stale = "données trop vieilles", conf = "confiance trop basse",
  notcheap = "pas assez sous le marché", stack = "pile plus grosse que ce qui se vend",
  tts = "trop long à revendre", margin = "marge trop faible après commission et dépôt",
}
F.REASON_LABEL = REASON_LABEL
local function dec(x) if x == nil then return "0" end return (("%.1f"):format(x):gsub("%.", ",")) end

function F.demand(classID, quality, stack, ilvl, minLevel, level)
  local c, q, lvl = tonumber(classID) or -1, tonumber(quality) or 1, n0(level)
  if c == 0 then return "consommable", 0.10 end
  if c == 1 then return "sac", 0.10 end
  if c == 6 then return "munitions", 0.10 end
  if c == 7 then return "artisanat", (lvl <= 0 or n0(ilvl) <= lvl + 20) and 0.10 or 0.03 end
  if c == 5 then return "composant", 0.05 end
  if c == 2 or c == 4 then
    if q == 2 and lvl > 0 and n0(minLevel) >= lvl - 5 and n0(minLevel) <= lvl + 10 then return "équipement de leveling", 0.05 end
    return "équipement", 0
  end
  return "autre", 0
end

local function sigKey(k)
  local a, b = tostring(k):match("^(%-?%d+):(%-?%d+)$")
  return n0(a), n0(b)
end
-- unités VENDUES entre deux scans : disparues − remises en vente moins cher (même taille de pile)
function F.soldBetween(prev, cur)
  prev, cur = prev or {}, cur or {}
  local gone, dis, new = 0, {}, {}
  for k, q in pairs(prev) do
    local still = n0(cur[k])
    if still < n0(q) then
      local u, c = sigKey(k)
      dis[#dis + 1] = { u, c, n0(q) - still }
      gone = gone + n0(q) - still
    end
  end
  for k, q in pairs(cur) do
    local inc = n0(q) - n0(prev[k])
    if inc > 0 then local u, c = sigKey(k) new[#new + 1] = { u, c, inc } end
  end
  table.sort(dis, function(a, b) if a[2] ~= b[2] then return a[2] < b[2] end return a[1] > b[1] end)
  table.sort(new, function(a, b) if a[1] ~= b[1] then return a[1] < b[1] end return a[2] < b[2] end)
  local matched = 0
  for _, nw in ipairs(new) do
    for _, e in ipairs(dis) do
      if nw[3] <= 0 then break end
      if e[2] == nw[2] and e[1] > nw[1] and e[3] > 0 then
        local m = min(e[3], nw[3])
        e[3], nw[3], matched = e[3] - m, nw[3] - m, matched + m
      end
    end
  end
  return gone - matched
end

function F.marketOf(ds)
  local meds = {}
  for i, d in ipairs(ds) do meds[i] = d.med end
  return F.median(meds)
end
function F.volOf(ds)
  if #ds == 0 then return 0.5 end
  if #ds < 3 then
    local l = ds[#ds]
    return r4(max(0.10, (l.med - l.min) / l.med))
  end
  local mv = F.marketOf(ds)
  local dev = {}
  for i, d in ipairs(ds) do dev[i] = abs(d.med - mv) end
  return r4(1.4826 * (F.median(dev) or 0) / max(1, mv))
end
function F.volumeOf(ds)
  local gone, hrs = 0, 0
  for _, d in ipairs(ds) do gone, hrs = gone + n0(d.gone), hrs + n0(d.hrs) end
  if hrs <= 0 then return nil end
  return floor(gone / hrs * 24 * SOLD_SHARE * 100 + 0.5) / 100
end

function F.liquidity(ds, stack, quality, prior, atrPrice, ageDays)
  ds = ds or {}
  local res = { ok = false, conf = 0, reasons = {}, market = 0, vol = 0.5, sold = nil, days = #ds, scans = 0, hours = 0, sellers = 0, gone = 0 }
  local R = res.reasons
  local function why(code, text) R[#R + 1] = { code = code, text = text } end
  if #ds == 0 then why("nohist", "jamais vu dans un scan TreeAI") return res end
  local nd, scans, hrs, gone, sellers = #ds, 0, 0, 0, 0
  for _, d in ipairs(ds) do
    scans, hrs, gone = scans + max(1, n0(d.n)), hrs + n0(d.hrs), gone + n0(d.gone)
    sellers = max(sellers, n0(d.sel))
  end
  local sold, mv, vol = F.volumeOf(ds), F.marketOf(ds) or 0, F.volOf(ds)
  local single = n0(stack or 1) <= 1
  local p = n0(prior)
  res.market, res.vol, res.sold, res.scans, res.hours, res.sellers, res.gone = mv, vol, sold, scans, hrs, sellers, gone
  if single and (tonumber(quality) or 1) >= 2 and (gone < 2 or nd <= 1) then why("rare", "objet rare : moins de 2 ventes vues") end
  local need = p >= 0.10 and LIQ.minDaysDemand or LIQ.minDays
  if nd < need then why("short", ("historique trop court (%d j sur %d)"):format(nd, need)) end
  if scans < LIQ.minScans then why("scans", ("trop peu de scans (%d sur %d)"):format(scans, LIQ.minScans)) end
  local minSold = single and LIQ.minSoldSingle or LIQ.minSold
  if hrs < LIQ.minHours then why("unmeasured", "ventes pas encore mesurées (il faut des scans espacés de quelques heures)")
  elseif sold == nil or sold < minSold then why("slow", ("se vend peu (%s par jour)"):format(dec(sold))) end
  if vol > LIQ.maxVol then why("unstable", ("prix instable (±%d %%)"):format(floor(vol * 100 + 0.5))) end
  if mv > 0 and ds[#ds].med < mv * (1 - LIQ.maxVol) then why("falling", "prix en chute") end
  if sellers > 0 and sellers < LIQ.minSellers then why("sellers", ("trop peu de vendeurs (%d)"):format(sellers)) end
  local av, atrOk = n0(atrPrice), false
  if av > 0 and mv > 0 then
    atrOk = abs(av - mv) / mv <= LIQ.atrGap
    if not atrOk then why("atr", ("Auctionator pas d'accord (%s contre %s)"):format(money(av), money(mv))) end
  end
  local c = 0.25 * min(1, nd / 5) + 0.15 * min(1, scans / 8) + 0.25 * min(1, n0(sold) / (3 * minSold))
    + 0.20 * (1 - min(1, vol / 0.4)) + (sellers > 0 and 0.15 * min(1, sellers / 5) or 0.075) + p + (atrOk and 0.05 or 0)
  local age = n0(ageDays)
  if age > LIQ.staleDays then c = c * 0.5 why("stale", ("données vieilles de %d j"):format(floor(age))) end
  c = floor(clamp(c, 0, 1) * 100 + 0.5) / 100
  res.conf = c
  if #R == 0 and c < LIQ.minConf then why("conf", ("confiance trop basse (%d %%)"):format(floor(c * 100 + 0.5))) end
  res.ok = #R == 0
  return res
end

function F.maxQty(sold, days, held)
  local v = tonumber(sold)
  if not v or v <= 0 then return 0 end
  return max(0, floor(v * (days or LIQ.qtyDays)) - max(0, n0(held)))
end
function F.stopLoss(buyUnit, vendor, neutral)
  local b = max(0, floor(n0(buyUnit)))
  local stop = max(max(0, n0(vendor)), floor(b * (100 - LIQ.stopPct) / 100))
  return { unit = stop, loss = max(0, b - F.net(stop, neutral)) }
end
function F.itemCap(total, pct, budget)
  local cap = floor(max(0, n0(total)) * n0(pct or LIQ.capPct) / 100)
  if budget == nil then return cap end
  return min(cap, max(0, n0(budget)))
end
-- renvoie deal, nil si l'achat est SÛR ; sinon nil, {code, text}
function F.safeFlip(buyout, count, liq, vendor, ahead, held, neutral, hrs, minMargin, minProfit)
  if not (liq and liq.ok) then
    local r = liq and liq.reasons and liq.reasons[1]
    return nil, r or { code = "nohist", text = REASON_LABEL.nohist }
  end
  count, buyout = max(1, n0(count)), n0(buyout)
  local mv = n0(liq.market)
  local unit = floor(buyout / count)
  if mv <= 0 or r4(1 - unit / mv) < n0(minMargin or 0.2) then
    return nil, { code = "notcheap", text = ("pas assez sous le marché (%s/u)"):format(money(mv)) }
  end
  local maxq = F.maxQty(liq.sold, nil, held)
  if count > maxq then
    return nil, { code = "stack", text = ("pile de %d : il s'en vend ~%d en %d j"):format(count, maxq, LIQ.qtyDays) }
  end
  local d = F.flip(buyout, count, { market = mv, vol = liq.vol, volume = liq.sold, conf = liq.conf }, vendor, ahead, neutral, hrs)
  if d.days == nil or d.days > LIQ.maxTts then
    return nil, { code = "tts", text = ("trop long à revendre (~%s j)"):format(dec(d.days)) }
  end
  if d.profit < F.minNet(buyout, minProfit or F.MIN_NET) or n0(d.roi) < LIQ.minRoi then
    return nil, { code = "margin", text = ("marge trop faible (%s net, il faut %s)"):format(money(d.profit), money(F.minNet(buyout, minProfit or F.MIN_NET))) }
  end
  d.safe, d.maxQty, d.sold, d.stop = true, maxq, liq.sold, F.stopLoss(unit, vendor, neutral)
  return d
end
function F.whyNone(rejects, candidates)
  if n0(candidates) <= 0 then return { "Aucune annonce assez sous le prix du marché parmi les objets suivis." } end
  local top = {}
  for k, v in pairs(rejects or {}) do if n0(v) > 0 then top[#top + 1] = { k, n0(v) } end end
  table.sort(top, function(a, b) if a[2] ~= b[2] then return a[2] > b[2] end return a[1] < b[1] end)
  local out = { ("%d affaire(s) apparente(s) écartée(s) :"):format(n0(candidates)) }
  for i = 1, min(3, #top) do out[#out + 1] = ("%s (%d)"):format(REASON_LABEL[top[i][1]] or top[i][1], top[i][2]) end
  return out
end

-- Annuler une de MES enchères battues ? (miroir de auction.cancel_advice) — comp = { {unit, count}, ... } des autres.
-- Jamais sans données sûres ; reposte toujours à ≤ (plus bas − 1 pc) ; plus bas anormal (bradage, autre variante,
-- < marché − 30 % ou < 1/10 de mon prix) = laisser ; annuler seulement si le gain espéré après dépôt perdu
-- + nouveau dépôt à risque + commission dépasse max(1 pa, 5 % du net).
function F.cancelAdvice(myUnit, count, comp, liq, vendor, neutral, hrs)
  count, myUnit = max(1, n0(count)), n0(myUnit)
  if not (liq and liq.ok) then
    return { action = "none", state = "?", text = "pas assez de données de prix : laisser" }
  end
  local c = {}
  for _, x in ipairs(comp or {}) do if n0(x[1]) > 0 then c[#c + 1] = { n0(x[1]), max(1, n0(x[2])) } end end
  table.sort(c, function(a, b) return a[1] < b[1] end)
  local low = c[1] and c[1][1]
  if not low or low >= myUnit then
    return { action = "keep", state = "ok", text = low and "le moins cher : garder" or "aucun concurrent : garder" }
  end
  local mv, vol = n0(liq.market), n0(liq.vol)
  if low * 10 < myUnit then
    return { action = "keep", state = "battue", text = ("laisser : ton prix est plus de 10 fois le plus bas (%s/u), vérifie l'objet"):format(money(low)) }
  end
  if low < mv * (1 - LIQ.maxVol) then
    return { action = "keep", state = "battue", text = ("laisser : %s/u est anormalement bas (marché %s/u)"):format(money(low), money(mv)) }
  end
  local dep = F.deposit(vendor, count, hrs, neutral)
  local floorU = max(1, mv - floor(mv * vol))
  local vt = n0(vendor) * count
  if vt > 0 then floorU = max(floorU, ceil(F.breakEven(vt, dep, F.pSell(liq.sold, 0, count, hrs), neutral) / count)) end
  local repost = low - 1
  if repost < floorU then
    return { action = "keep", state = "battue", text = ("laisser : passer sous %s/u ne rapporte rien (plancher %s/u)"):format(money(low), money(floorU)) }
  end
  local ahead = 0
  for _, x in ipairs(c) do if x[1] < myUnit then ahead = ahead + x[2] end end
  local pKeep, pNew = F.pSell(liq.sold, ahead, count, hrs), F.pSell(liq.sold, 0, count, hrs)
  local keepV = pKeep * F.net(myUnit * count, neutral) - dep * (1 - pKeep)
  local cancelV = pNew * F.net(repost * count, neutral) - dep * (1 - pNew) - dep
  local gain = floor(cancelV - keepV + 0.5)
  local need = max(100, floor(F.net(repost * count, neutral) * 0.05))
  if gain < need then
    return { action = "keep", state = "battue", gain = gain, text = gain > 0 and ("laisser : annuler et reposter ne rapporte que %s"):format(money(gain))
      or "laisser : annuler (dépôt perdu) coûterait plus qu'attendre" }
  end
  return { action = "cancel", state = "battue", repost = repost, gain = gain,
    text = ("annuler, reposter %s/u (+%s espéré, dépôt %s perdu compté)"):format(money(repost), money(gain), money(dep)) }
end

-- ------------------------------------------------------------------ état / API HV
M.cur = {}        -- [itemID] = { units = {..}, listings = { {index, unit, count, buy, owner, auctionID} }, qty, t }
M.deals, M.thinList, M.owned, M.plan, M.queue, M.qi = {}, {}, {}, {}, {}, 1
M.browse = {}     -- [itemID] = { min, qty, t } (parcours passif, API moderne)
M.sig = nil       -- signatures du dernier scan complet { t, items = {[id] = {[clé] = n}} } (session)
M.ahOpen = false
M.dbg = { errors = {}, browse = 0, searches = 0 }
M.vcache = {}     -- prix marchand connus (GetItemInfo peut répondre nil tant que l'objet n'est pas en cache)

local function clock() return try(_G.GetTime) or 0 end
local function ms() return try(_G.debugprofilestop) or clock() * 1000 end
-- (4.1 AHPROFIT) RIEN dans le chat : les messages des clics / scans vont dans la ligne d'état du panneau HV
-- (M.msg, gardés dans M.msgLog) ; seules les commandes tapées (/tai hv …) répondent dans le chat (out).
M.msgLog = {}
local function say(msg)
  msg = tostring(msg or "")
  M.msg, M.msgT = msg, now()
  local l = M.msgLog
  l[#l + 1] = msg
  while #l > 30 do table.remove(l, 1) end
  local f = M.win
  if f and f.msg and f.msg.SetText then f.msg:SetText(msg) end
end
M.Say = say
function M.Said(pattern)
  for i = #M.msgLog, 1, -1 do if M.msgLog[i]:find(pattern) then return M.msgLog[i] end end
end
local function out(msg) if M.Print then M:Print(msg) end end   -- commande tapée seulement
local function logErr(msg)
  local e = M.dbg.errors
  e[#e + 1] = (try(_G.date, "%H:%M:%S") or "") .. " " .. tostring(msg)
  while #e > 8 do table.remove(e, 1) end
  if M.Debug then M:Debug(msg) end
end
local function mmss(sec)
  sec = max(0, floor(n0(sec)))
  return ("%d:%02d"):format(floor(sec / 60), sec % 60)
end

local function modernAH()
  local A = rawget(_G, "C_AuctionHouse")
  if type(A) == "table" and type(A.ReplicateItems) == "function" and type(A.GetReplicateItemInfo) == "function" then return A end
  return nil
end
local function legacyAH()
  return type(rawget(_G, "QueryAuctionItems")) == "function" and type(rawget(_G, "GetAuctionItemInfo")) == "function"
end
-- "modern" = C_AuctionHouse (WoW Forever, Retail, Mists), "classic" = ancienne API (Classic Era).
-- Si les deux existent (couche de compatibilité d'un autre addon), l'interface Blizzard chargée tranche.
function M.Flavor()
  local m, l = modernAH() ~= nil, legacyAH()
  local force = cfg("api", "auto")
  if force == "modern" and m then return "modern" elseif force == "classic" and l then return "classic" end
  if m and l then
    if rawget(_G, "AuctionFrame") and not rawget(_G, "AuctionHouseFrame") then return "classic" end
    return "modern"
  end
  if m then return "modern" elseif l then return "classic" end
  return nil
end

-- prix marchand : cache → GetItemInfo → Auctionator → historique ; demande le chargement sinon
local function atr()
  local A = rawget(_G, "Auctionator")
  local v = type(A) == "table" and type(A.API) == "table" and A.API.v1
  return type(v) == "table" and v or nil
end
vendorPrice = function(id)
  if not id then return 0 end
  local c = M.vcache[id]
  if c then return c end
  local v = select(11, M.ItemInfo(id))
  if v == nil then
    local api = atr()
    v = api and try(api.GetVendorPriceByItemID, "TreeAI", id)
    if v == nil then
      local CI = rawget(_G, "C_Item")
      if type(CI) == "table" then try(CI.RequestLoadItemDataByID, id) end
      local r = M.db and M.db.realms
      for _, rd in pairs(r or {}) do local rec = rd.items and rd.items[id] if rec and n0(rec.v) > 0 then return n0(rec.v) end end
      return 0
    end
  end
  v = n0(v)
  M.vcache[id] = v
  return v
end

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
local function hours()
  local h = max(2, n0(cfg("hours", 24)))
  if h <= 12 then return 12 elseif h <= 24 then return 24 end
  return 48
end
local function durIndex() local h = hours() return h == 12 and 1 or h == 24 and 2 or 3 end

-- (3.9.9) clé royaume / faction / HV relue une fois par image : Economy évalue tout le sac (M.Stats par objet)
local rdT, rdDB, rdHouse, rdKey
function M.RealmData()
  local db = M.db
  if not db then return nil end
  db.realms = db.realms or {}
  local t, h = try(_G.GetTime), cfg("house", "auto")
  local key
  if t and t == rdT and db == rdDB and h == rdHouse then key = rdKey
  else
    key = (try(_G.GetRealmName) or "?") .. "-" .. (try(_G.UnitFactionGroup, "player") or "?") .. (M.IsNeutral() and "-N" or "")
    rdT, rdDB, rdHouse, rdKey = t, db, h, key
  end
  local r = db.realms[key]
  if not r then r = { items = {}, n = 0 } db.realms[key] = r end
  return r, key
end

-- ------------------------------------------------------------------ Auctionator (lecture seule)
function M.AtrPrice(id)
  local api = atr()
  if not (api and id) then return nil end
  local p = try(api.GetAuctionPriceByItemID, "TreeAI", id)
  if type(p) ~= "number" or p <= 0 then return nil end
  return floor(p), tonumber((try(api.GetAuctionAgeByItemID, "TreeAI", id)))
end
function M.HookAuctionator()
  if M.atrHooked then return true end
  local api = atr()
  if not api then return false end
  M.atrHooked = true
  if type(api.RegisterForDBUpdate) == "function" then
    local ok, err = pcall(api.RegisterForDBUpdate, "TreeAI", function()
      M.atrUpdated = now()
      if M.ahOpen and not M.job then T:After(0.5, function() if not M.job then M.FindDeals() M.Refresh() end end) end
    end)
    M.atrRegistered = ok
    if not ok then logErr("Auctionator RegisterForDBUpdate : " .. tostring(err)) end
  end
  return true
end

function M.Stats(id)
  local r = M.RealmData()
  local rec = r and r.items[id]
  local st, ageDays
  if rec then
    ageDays = (now() - n0(rec.last)) / 86400
    st = F.statsFromPoints(points(rec), ageDays)
  end
  if st then st.src = "TreeAI" end
  -- secours / priorité à Auctionator quand nos données manquent, sont peu sûres ou vieilles (> 2 j)
  if not st or st.conf < 0.3 or (ageDays and ageDays > 2) then
    local p, age = M.AtrPrice(id)
    if p and (not st or not age or age < ageDays or st.conf < 0.3) then
      local conf = (age and age <= 0 and 0.45) or (age and age <= 2 and 0.35) or 0.25
      st = { market = p, vol = 0.2, volume = st and st.volume, conf = conf, ndays = 1, src = "Auctionator", age = age }
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

local function addSel(rec, day, n)
  if n0(n) <= 0 then return end
  rec.sl = rec.sl or {}
  local sl = rec.sl
  if sl[#sl - 1] == day then sl[#sl] = max(sl[#sl], n) else sl[#sl + 1] = day sl[#sl + 1] = n end
  while #sl >= 2 and (sl[1] <= day - MAX_DAYS or #sl > MAX_DAYS * 2) do table.remove(sl, 1) table.remove(sl, 1) end
end
-- vendeurs distincts vus (noms connus : ancienne API / recherche) sinon niveaux de prix distincts (scan complet)
local function sellersOf(it)
  local n, l = 0, 0
  for _ in pairs(it.owners or {}) do n = n + 1 end
  if n > 0 then return n end
  for _ in pairs(it.levels or {}) do l = l + 1 end
  return l
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

-- lecture d'une annonce : id, count, buyout, owner, minBid (id présent même si les infos ne sont pas chargées)
local function readListing(i, src)
  local r
  if src == "replicate" then
    local A = modernAH()
    r = A and { try(A.GetReplicateItemInfo, i - 1) }
  else
    r = { try(_G.GetAuctionItemInfo, "list", i) }
  end
  if not r or (r[1] == nil and r[17] == nil) then return nil end
  local id = tonumber(r[17])
  if not id and src == "replicate" then
    local A = modernAH()
    id = idFromLink(A and try(A.GetReplicateItemLink, i - 1))
  end
  return id, n0(r[3]), n0(r[10]), r[14], n0(r[8]), r[18]
end

-- ------------------------------------------------------------------ moteur de tâches (coroutine, ≤ 4 ms / image)
local SLICE_MS, SLICE_ROWS = 4, 2500
local function runJob()
  local j = M.job
  if not j then return end
  j.deadline = ms() + SLICE_MS
  j.slice = 0
  local ok, err = coroutine.resume(j.co)
  if not ok then
    logErr("scan " .. tostring(j.kind) .. " : " .. tostring(err))
    M.job, M.scan = nil, nil
    M.Refresh()
    return
  end
  if coroutine.status(j.co) == "dead" then M.job = nil return end
  T:After(0, runJob)
end
local function startJob(kind, fn)
  if M.job then return false end
  local j = { kind = kind, t0 = clock(), rows = 0, total = 0 }
  j.co = coroutine.create(function() fn(j) end)
  M.job = j
  runJob()
  return true
end
local function slice(j)
  if not j then return end
  j.slice = j.slice + 1
  if j.slice >= SLICE_ROWS or (j.slice % 64 == 0 and ms() >= j.deadline) then coroutine.yield() end
end

-- ------------------------------------------------------------------ scans
function M.BeginScan(src, full, total)
  M.scan = { src = src, full = full, total = total, i = 0, items = {}, t = now(), missing = {}, t0 = clock() }
end

local function addRow(s, i, id, count, buy, owner, auctionID)
  if not (id and count > 0 and buy > 0) then return end
  local it = s.items[id]
  if not it then it = { units = {}, listings = {}, qty = 0, sig = {}, owners = {}, levels = {} } s.items[id] = it end
  local unit = floor(buy / count)
  local isMine = owner ~= nil and (owner == M.meName or owner == "player")
  if not isMine then
    if type(owner) == "string" and owner ~= "" then it.owners[owner] = true end
    it.levels[unit] = true
    it.units[#it.units + 1] = unit
    it.listings[#it.listings + 1] = { index = i, unit = unit, count = count, buy = buy, owner = owner, auctionID = auctionID }
    it.qty = it.qty + count
  end
  local k = unit .. ":" .. count
  it.sig[k] = (it.sig[k] or 0) + count
end

local function scanRow(s, i)
  local id, count, buy, owner = readListing(i, s.src)
  if id then addRow(s, i, id, count, buy, owner)
  else s.missing[#s.missing + 1] = i end
end

function M.ScanChunk(nmax)
  local s = M.scan
  if not s then return true end
  M.meName = me()
  local last = min(s.total, s.i + (nmax or 500))
  for i = s.i + 1, last do scanRow(s, i) end
  s.i = last
  return last >= s.total
end

function M.FinishScan(j)
  local s = M.scan
  M.scan = nil
  if not s then return end
  -- annonces illisibles au 1er passage (données pas encore reçues) : un 2e essai, puis on n'attend pas
  local miss = s.missing
  s.missing = {}
  for _, i in ipairs(miss) do scanRow(s, i) slice(j) end
  local unresolved = #s.missing
  local r = M.RealmData()
  local day, t = today(s.t), s.t
  local prev = s.full and M.sig or nil
  local hrs = prev and (t - prev.t) / 3600 or 0
  if hrs < 0.2 or hrs > 48 then prev, hrs = nil, 0 end
  local cur, nItems = {}, 0
  for id, it in pairs(s.items) do
    nItems = nItems + 1
    table.sort(it.listings, function(a, b) return a.unit < b.unit end)
    table.sort(it.units)
    it.t = t
    cur[id] = it
    if r and #it.units > 0 then
      local rec = r.items[id]
      if not rec then rec = { h = {} } r.items[id] = rec end
      if n0(rec.v) <= 0 then rec.v = vendorPrice(id) end
      local gone = (prev and prev.items[id]) and max(0, F.soldBetween(prev.items[id], it.sig)) or 0
      addPoint(rec, day, it.units[1], F.lowMedian(it.units), it.qty, gone, prev and floor(hrs * 100 + 0.5) / 100 or 0)
      addSel(rec, day, sellersOf(it))
      rec.last = t
    end
    slice(j)
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
  local dt = max(0.001, clock() - s.t0)
  if M.db then M.db.lastScanT = t end
  M.lastScan = { src = s.src, full = s.full, rows = s.total, items = nItems, unresolved = unresolved, t = t,
    secs = floor(dt * 100 + 0.5) / 100, speed = floor(s.total / dt + 0.5), by = s.by }
  M.Prune()
  M.FindDeals(cur, j)
  M.CheckOwned()
  M.BuildQueue()
  M.Refresh()
  if T.Bags and T.Bags.Invalidate then pcall(T.Bags.Invalidate) end   -- (5.0 BAGS) nouveaux prix : avis des sacs refaits
end

local function processScan(src, full, total, by)
  M.lastSrc = src
  M.meName = me()
  return startJob(src, function(j)
    M.BeginScan(src, full, total)
    local s = M.scan
    s.by = by
    j.total = total
    for i = 1, total do
      if M.scan ~= s then return end
      scanRow(s, i)
      s.i, j.rows = i, i
      slice(j)
    end
    M.FinishScan(j)
    if src == "replicate" then M.repDone, M.repN = now(), total end
    if full and total > 0 then
      say(("Scan complet lu : %d annonces, %d objets en %s s (%d annonces/s)%s."):format(total, M.lastScan.items,
        tostring(M.lastScan.secs), M.lastScan.speed, #M.deals > 0 and (", " .. #M.deals .. " affaire(s)") or ""))
    end
  end)
end
M.ProcessScan = processScan

-- une recherche d'objet (lecture passive ou ciblée) : remplace les annonces connues de cet objet
function M.ApplyItemListings(id, rows)
  if not id then return end
  local s = { items = {} }
  M.meName = me()
  for i, l in ipairs(rows) do addRow(s, i, id, l.count, l.buy, l.owner, l.auctionID) end
  local it = s.items[id] or { units = {}, listings = {}, qty = 0, sig = {} }
  table.sort(it.listings, function(a, b) return a.unit < b.unit end)
  table.sort(it.units)
  it.t = now()
  M.cur[id] = it
  local r = M.RealmData()
  if r and #it.units > 0 then
    local rec = r.items[id]
    if not rec then rec = { h = {} } r.items[id] = rec end
    if n0(rec.v) <= 0 then rec.v = vendorPrice(id) end
    addPoint(rec, today(), it.units[1], F.lowMedian(it.units), it.qty, 0, 0)
    addSel(rec, today(), sellersOf(it))
    rec.last = now()
  end
  if T.Bags and T.Bags.Invalidate then pcall(T.Bags.Invalidate, id) end   -- (5.0 BAGS) prix de cet objet relu
  return it
end

function M.Budget()
  local g = M.Gold()
  return g.budget, g.spent, g.cap
end

local function riskOf(d)
  if d.kind == "vendor" then return 0 end
  return r4(clamp(1 - n0(d.conf) * n0(d.p), 0, 1))
end
function M.RiskText(d)
  local r = riskOf(d)
  return (r < 0.35 and "faible") or (r < 0.65 and "moyen") or "élevé"
end

-- liquidité d'un objet à partir de NOTRE historique (Auctionator seul ne prouve pas que ça se vend)
function M.Liq(id)
  local r = M.RealmData()
  local rec = r and r.items[id]
  local _, _, q, ilvl, minL, _, _, stack, _, _, _, classID = M.ItemInfo(id)
  local label, prior = F.demand(classID, q, stack, ilvl, minL, try(_G.UnitLevel, "player"))
  local ageDays = rec and (now() - n0(rec.last)) / 86400 or 0
  local liq = F.liquidity(rec and points(rec) or {}, stack or 20, q or 1, prior, (M.AtrPrice(id)), ageDays)
  liq.class = label
  M.liqCache[id] = liq
  return liq
end
M.liqCache = {}

local function held(id)
  local n = n0(try(_G.GetItemCount, id))
  for _, a in ipairs(M.owned or {}) do if a.id == id and a.state ~= "cancelled" then n = n + n0(a.count) end end
  return n
end

-- seulement les achats-reventes SÛRS (preuves de ventes) + les reventes au marchand (sans risque)
--- (4.0 QA) « 1 achat-revente sûr » / « 3 achats-reventes sûrs » (pas « achat(s)-revente(s) sûr(s) »)
function M.DealsCount(n)
  n = tonumber(n) or 0
  return n == 1 and "1 achat-revente sûr" or (n .. " achats-reventes sûrs")
end
function M.FindDeals(items, j)
  local neutral, hrs = M.IsNeutral(), hours()
  local minMargin, minProfit = n0(cfg("minMargin", 15)) / 100, max(1, n0(cfg("minProfit", 1))) * 100
  local all, thin, rejects, cand = {}, {}, {}, 0
  LIQ.qtyDays = max(1, n0(cfg("qtyDays", 3)))
  for id, it in pairs(items or M.cur) do
    if #it.listings > 0 and not (it.odd or M.OddCached(id)) then
      local vendor = vendorPrice(id)
      local st = M.Stats(id)
      local liq
      for li = 1, min(3, #it.listings) do
        local l = it.listings[li]
        local d = F.vendorFlip(l.buy, l.count, vendor)
        if d and d.profit < F.minNet(d.buy, minProfit) then d = nil end   -- (4.1) 1 pc de gain ne vaut pas un clic
        if d then
          d.safe = true
        elseif st and st.market and st.market > 0 and floor(l.buy / max(1, l.count)) <= st.market * (1 - minMargin) then
          cand = cand + 1                          -- affaire apparente : a-t-on la preuve que ça se revend ?
          liq = liq or M.Liq(id)
          local target = floor(n0(liq.market) * (1 - 0.5 * n0(liq.vol)))
          local ahead = 0
          for _, o in ipairs(it.listings) do if o ~= l and o.unit < target then ahead = ahead + o.count end end
          local why
          d, why = F.safeFlip(l.buy, l.count, liq, vendor, ahead, held(id), neutral, hrs, minMargin, minProfit)
          if d then
            d.thin = F.thin(it.units)
            d.relist = F.relistLoss(d.deposit, d.p)
            d.market, d.src, d.class = liq.market, "TreeAI", liq.class
          else
            local code = why and why.code or "nohist"
            rejects[code] = (rejects[code] or 0) + 1
            M.lastReject = { id = id, why = why }
          end
        end
        if d then
          d.id, d.index, d.owner, d.auctionID, d.src = id, l.index, l.owner, l.auctionID, d.src or M.lastSrc
          d.risk = riskOf(d)
          all[#all + 1] = d
        end
      end
      if F.thin(it.units) and st and st.market and st.market >= 10000 then thin[#thin + 1] = id end
    end
    slice(j)
  end
  local budget = M.Budget()
  M.deals = F.rank(all, minMargin, minProfit, budget)
  for i = #M.deals, 41, -1 do M.deals[i] = nil end
  M.thinList, M.rejects, M.candidates = thin, rejects, cand
  M.none = #M.deals == 0 and { text = F.NONE_TEXT, why = F.whyNone(rejects, cand) } or nil
  M.Plan()
  local d = M.deals[1]
  if d and cfg("tips", true) and M.Suggest and M.ahOpen then   -- (4.1) seulement à l'HV (pas de pub ailleurs)
    local fresh = d.id ~= M._dealSound   -- son « bonne affaire » seulement pour une nouvelle meilleure affaire sûre
    M._dealSound = d.id
    M:Suggest("deals", ("HV : %s, meilleur %s +%s net"):format(M.DealsCount(#M.deals), itemName(d.id), money(d.profit)), 2,
      { ttl = 300, sound = fresh or nil, soundKind = "ah" })
  elseif not d and M.Clear then
    M._dealSound = nil
    M:Clear("deals")
  end
  M.tabDirty = true
  return M.deals
end

-- ------------------------------------------------------------------ gestion de l'or
function M.Reserve()
  local lvl = n0(try(_G.UnitLevel, "player"))
  local keep = n0(cfg("keepGold", 0)) * 10000
  local train = 0
  if cfg("autoReserve", true) and lvl > 0 and lvl < 60 then train = floor(lvl * lvl * 150) end  -- entraînement + réparations
  local mount = n0(cfg("mountGold", 0)) * 10000
  return keep + train + mount, { keep = keep, train = train, mount = mount }
end
function M.Gold()
  local total = n0(API.Money and API.Money())
  if total <= 0 then total = n0((try(_G.GetMoney))) end   -- l'or réel (hors combat à l'HV)
  local reserve, parts = M.Reserve()
  local free = max(0, total - reserve)
  local cap = n0(cfg("budget", 0)) * 10000
  local spent = n0(M.char and M.char.spent and M.char.spent[dateKey()])
  local budget = cap > 0 and min(free, max(0, cap - spent)) or free
  local listed = 0
  for _, a in ipairs(M.owned or {}) do if a.state ~= "cancelled" then listed = listed + n0(a.buy) end end
  local held = 0
  local ev = M.char and M.char.ledger
  if ev then for _, it in pairs(F.realized(ev).items) do held = held + max(0, n0(it.cost)) end end
  return { total = total, reserve = reserve, parts = parts, free = free, budget = budget, cap = cap, spent = spent,
    listed = listed, held = held, invested = listed + held }
end
-- (5.0 BAGS) l'assistant d'inventaire quand il est allumé : UN avis « vendre / garder » pour tout l'addon
local function brain()
  local B = T.Bags
  if type(B) == "table" and B._active and type(B.Verdict) == "function" then return B end
  return nil
end
M.Brain = brain
-- ce qu'il faut garder de cet objet : (5.0 BAGS) T.Bags.Verdict (gardiens : quêtes, potions, munitions, métiers,
-- équipement…) — seul un avis « HV » ou « marchand » laisse des piles à vendre ; sinon l'ancien chemin
-- (4.2 FIX42 : ProfLevel.KeepOf, compos de tes plans de métier, objets de tes quêtes)
local function keepOf(id)
  local B = brain()
  if B then
    local ok, v = pcall(B.Verdict, id)
    -- (un objet absent des sacs — achat-revente, banque — garde l'ancien chemin : le besoin des métiers)
    if ok and type(v) == "table" and not v.off and n0(v.have) > 0 then
      -- un surplus reconnu (avis « HV » / « marchand », ou pile entamée) : on garde ce que les gardiens réclament ;
      -- sinon tout est gardé (rien à mettre en vente)
      if n0(v.surplus) > 0 then return n0(v.keep), v.rest and v.rest.why or (n0(v.keep) > 0 and v.why or nil), v end
      return n0(v.have), v.why, v
    end
  end
  local P = T.ProfLevel
  if not (P and type(P.KeepOf) == "function") then return 0, nil end
  local ok, n, why = pcall(P.KeepOf, id)
  if ok then return n0(n), why end
  return 0, nil
end
M.KeepOf = keepOf
function M.Plan()
  local g = M.Gold()
  -- (4.2 FIX42) jamais « acheter pour revendre » un objet que tes métiers / quêtes demandent : tu le garderais
  local deals = {}
  for _, d in ipairs(M.deals or {}) do if keepOf(d.id) <= 0 then deals[#deals + 1] = d end end
  -- règle dure : jamais plus de capGold % de TOUT ton or dans un même objet (reventes au marchand exclues)
  local plan, spend, gain = F.allocate(deals, g.budget, nil, 4, max(1, F.itemCap(g.total, cfg("capGold", 20), g.budget)))
  M.plan, M.planSpend, M.planGain = plan, spend, gain
  return plan, spend, gain
end

-- ------------------------------------------------------------------ actions (UN appel protégé par clic)
-- (4.1 AHPROFIT) jamais en combat (règle TreeAI) ; bilan de la session (depuis la connexion) dans le panneau
local function inCombat() return try(_G.InCombatLockdown) and true or false end
M.InCombat = inCombat
local function combatRefused()
  if not inCombat() then return false end
  say("En combat : rien à l'HV avant la fin du combat.")
  return true
end
M.sess = { buys = 0, spent = 0, expect = 0, posts = 0, postNet = 0, cancels = 0, posted = {} }
function M.SessionText()
  local s = M.sess
  if s.buys + s.posts + s.cancels == 0 then return "Session : rien encore (1 clic = 1 action)." end
  local t = {}
  if s.buys > 0 then t[#t + 1] = ("%d achat%s (%s)"):format(s.buys, s.buys > 1 and "s" or "", money(-s.spent)) end
  if s.posts > 0 then t[#t + 1] = ("%d mise%s en vente (≈ %s net)"):format(s.posts, s.posts > 1 and "s" or "", money(s.postNet)) end
  if s.cancels > 0 then t[#t + 1] = ("%d annulée%s"):format(s.cancels, s.cancels > 1 and "s" or "") end
  return ("Session : %s · bénéfice prévu +%s"):format(table.concat(t, " · "), money(s.expect))
end
-- Classique : retrouve l'annonce (même objet, même pile, même prix, pas à moi) puis UNE enchère au rachat.
function M.BuyClassic(d)
  if combatRefused() then return false end
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

local function priceSorts()
  local E = rawget(_G, "Enum")
  local so = E and E.AuctionHouseSortOrder
  return { { sortOrder = (so and so.Price) or 0, reverseSort = false }, { sortOrder = (so and so.Name) or 1, reverseSort = false } }
end
function M.SearchItem(id)
  local A = modernAH()
  if not (A and id) then return false end
  if type(A.IsThrottledMessageSystemReady) == "function" and not try(A.IsThrottledMessageSystemReady) then
    M.dbg.throttled = (M.dbg.throttled or 0) + 1
    return false                                           -- limiteur Blizzard : on ne force jamais
  end
  local key = try(A.MakeItemKey, id) or { itemID = id }
  M.dbg.searches = M.dbg.searches + 1
  M.lastSearch = { id = id, t = clock() }
  try(A.SendSearchQuery, key, priceSorts(), true)
  return true
end

-- Moderne : clic 1 = recherche (vérif du prix), clic 2 = achat (objet) ou début (marchandise), clic 3 = confirmer.
function M.BuyModern(d)
  local A = modernAH()
  if not A then return false end
  if not d.state or d.state == "gone" then
    local prev = d.state
    d.state, M.pendingSearch = "search", d                 -- avant l'envoi : la réponse peut être immédiate
    if not M.SearchItem(d.id) then
      d.state, M.pendingSearch = prev, nil
      say("Blizzard limite les recherches : reclique dans un instant.")
    end
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

-- résultats d'une recherche moderne → lignes {count, buy, unit, owner, auctionID}
function M.ReadSearch(id, commodity)
  local A = modernAH()
  local rows = {}
  if not (A and id) then return rows end
  local mine = me()
  local function owned(r)
    if r.containsOwnerItem then return true end
    local o = type(r.owners) == "table" and r.owners[1]
    return o ~= nil and (o == mine or o == "player")
  end
  if commodity then
    for i = 1, min(200, n0(try(A.GetNumCommoditySearchResults, id))) do
      local r = try(A.GetCommoditySearchResultInfo, id, i)
      if type(r) == "table" and n0(r.unitPrice) > 0 and n0(r.quantity) > 0 then
        rows[#rows + 1] = { count = n0(r.quantity), buy = n0(r.unitPrice) * n0(r.quantity), unit = n0(r.unitPrice),
          owner = owned(r) and "player" or nil, auctionID = r.auctionID }
      end
    end
  else
    local key = try(A.MakeItemKey, id) or { itemID = id }
    for i = 1, min(200, n0(try(A.GetNumItemSearchResults, key))) do
      local r = try(A.GetItemSearchResultInfo, key, i)
      if type(r) == "table" and n0(r.buyoutAmount) > 0 then
        local q = max(1, n0(r.quantity))
        rows[#rows + 1] = { count = q, buy = n0(r.buyoutAmount), unit = floor(n0(r.buyoutAmount) / q),
          owner = owned(r) and "player" or nil, auctionID = r.auctionID }
      end
    end
  end
  return rows
end

function M.OnSearchResults(_, event, arg)
  local A = modernAH()
  if not A then return end
  local commodity = event == "COMMODITY_SEARCH_RESULTS_UPDATED" or event == "COMMODITY_SEARCH_RESULTS_ADDED"
  local id = (type(arg) == "number" and arg) or (type(arg) == "table" and tonumber(arg.itemID)) or nil
  local d = M.pendingSearch
  if not id then id = (d and d.id) or (M.mini and M.mini.waiting) or (M.lastSearch and M.lastSearch.id) end
  if not id then return end
  local rows = M.ReadSearch(id, commodity)
  M.dbg.lastSearchRows = #rows
  M.ApplyItemListings(id, rows)            -- lecture passive : le joueur (ou nous) a cherché cet objet
  if d and d.id == id then
    M.pendingSearch = nil
    d.state = "gone"
    if commodity then
      local r = rows[1]
      if r and r.owner == nil and r.unit * d.count <= d.buy and r.count >= d.count then
        d.state, d.commodity, d.buyNow = "ready", true, r.unit * d.count
      end
    else
      for _, r in ipairs(rows) do
        if r.owner == nil and r.count == d.count and r.buy <= d.buy then
          d.state, d.auctionID, d.buyNow = "ready", r.auctionID, r.buy
          break
        end
      end
    end
    if d.state == "gone" then say(("%s : prix changé, plus d'affaire."):format(itemName(d.id))) end
  end
  for _, q in ipairs(M.queue or {}) do
    if q.kind == "post" and q.p.id == id and q.p.state == "check" then q.p.state = nil end
  end
  for _, p in ipairs(M.postRows or {}) do if p.id == id and p.state == "check" then p.state = nil end end
  if M.mini and M.mini.waiting == id then
    M.mini.waiting, M.mini.done = nil, M.mini.done + 1
    T:After(0, M.MiniNext)
  end
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
  if d.state == "bought" or combatRefused() then return false end
  local ok, paid
  if M.Flavor() == "classic" then ok, paid = M.BuyClassic(d) else ok, paid = M.BuyModern(d) end
  if ok then
    local ss = M.sess
    ss.buys, ss.spent, ss.expect = ss.buys + 1, ss.spent + n0(paid), ss.expect + max(0, n0(d.profit))
  end
  if ok and M.char then
    local k = dateKey()
    M.char.spent = M.char.spent or {}
    M.char.spent[k] = n0(M.char.spent[k]) + n0(paid)
    if d.kind == "flip" and d.stop then   -- pour le stop-loss des ventes suivantes
      M.char.flips = M.char.flips or {}
      M.char.flips[d.id] = { unit = floor(n0(paid) / max(1, d.count)), qty = d.count, t = now(), stop = d.stop.unit }
    end
    local stopT = d.stop and (" Stop-loss : sous %s/u, revends (perte max %s/u)."):format(money(d.stop.unit), money(d.stop.loss)) or ""
    say(("Acheté : %s x%d pour %s (profit attendu %s).%s"):format(itemName(d.id), d.count, money(paid), money(d.profit), stopT))
  end
  M.Refresh()
  return ok
end

-- ------------------------------------------------------------------ scan complet / ciblé
function M.FullCooldown()
  return max(0, 900 - (now() - n0(M.db and M.db.lastFullCall)))
end

function M.ScanAll()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if M.job then say(("Lecture en cours : %d / %d annonces."):format(M.job.rows, M.job.total)) return false end
  local fl = M.Flavor()
  local cd = M.FullCooldown()
  if fl == "modern" then
    local A = modernAH()
    local n = n0(try(A.GetNumReplicateItems))
    if cd > 0 and n > 0 and n ~= M.repN then   -- un scan complet récent (Auctionator ?) est en mémoire : on le lit
      say(("Lecture du dernier scan complet en mémoire (%d annonces)…"):format(n))
      return processScan("replicate", true, n, "mémoire")
    end
    if cd > 0 then
      say(("Scan complet dans %s (Blizzard : 1 fois / 15 min pour tout le compte). En attendant : « Prix de mes objets »."):format(mmss(cd)))
      M.dbg.refused = now()
      return false
    end
    M.pendingAll, M.db.lastFullCall, M.dbg.lastCall = true, now(), now()
    try(A.ReplicateItems)
    T:After(25, function()
      if M.pendingAll and not M.job and not M.repWait then
        M.pendingAll = false
        logErr("ReplicateItems sans réponse en 25 s (limite de 15 min déjà prise par un autre addon ?)")
        say("Pas de réponse au scan complet : la limite Blizzard (15 min, tout le compte) est sans doute déjà prise (Auctionator ?). « Prix de mes objets » en attendant.")
      end
    end)
  elseif fl == "classic" then
    local _, canAll = try(_G.CanSendAuctionQuery)
    if not canAll then
      say(("Scan complet pas encore disponible (Blizzard : 1 fois / 15 min%s)."):format(cd > 0 and (", encore " .. mmss(cd)) or ""))
      M.dbg.refused = now()
      return false
    end
    M.pendingAll, M.db.lastFullCall, M.dbg.lastCall = true, now(), now()
    try(_G.QueryAuctionItems, "", nil, nil, 0, false, -1, true, false, nil)
  else
    say("API de l'hôtel des ventes introuvable (/tai hv debug).")
    logErr("aucune API HV détectée")
    return false
  end
  say("Scan complet demandé…")
  M.Refresh()
  return true
end

-- REPLICATE_ITEM_LIST_UPDATE arrive en rafale (et aussi quand un AUTRE addon fait le scan complet) :
-- on attend que le nombre d'annonces soit stable, puis on lit tout par tranches.
function M.REPLICATE_ITEM_LIST_UPDATE()
  if not modernAH() or M.job or M.repWait then return end
  if not M.pendingAll and M.repDone and now() - M.repDone < 120 then return end  -- infos d'objets reçues après notre lecture
  M.repWait = { n = -1, tries = 0 }
  T:After(0.25, M.CheckReplicate)
end
function M.CheckReplicate()
  local w, A = M.repWait, modernAH()
  if not (w and A) or M.job then M.repWait = nil return end
  local n = n0(try(A.GetNumReplicateItems))
  w.tries = w.tries + 1
  if n > 0 and (n == w.n or w.tries >= 12) then
    M.repWait = nil
    local ours = M.pendingAll
    M.pendingAll = false
    if not ours and M.db then M.db.lastFullCall = now() end   -- la limite du compte vient d'être consommée
    processScan("replicate", true, n, ours and "TreeAI" or "autre addon")
  elseif n == 0 and w.tries >= 12 then
    M.repWait = nil
  else
    w.n = n
    T:After(0.25, M.CheckReplicate)
  end
end

-- liste ciblée : plan d'achat, objets à vendre, mes enchères (≤ 40 objets)
function M.TargetIDs()
  local ids, seen = {}, {}
  local function add(id) if id and not seen[id] and #ids < 40 then seen[id] = true ids[#ids + 1] = id end end
  for _, d in ipairs(M.plan or {}) do add(d.id) end
  for _, a in ipairs(M.owned or {}) do add(a.id) end
  if API.IterBags then
    for _, _, item in API.IterBags() do
      if item and item.itemID and not item.noValue and (item.quality or 1) >= 1 and (M.Stats(item.itemID) or M.cur[item.itemID]) then add(item.itemID) end
    end
  end
  -- (5.0 BAGS) objets des sacs « à l'HV, prix inconnu » et ce qu'il te manque : leur prix d'abord
  local B = brain()
  if B then
    local okL, L = pcall(B.Lists)
    for _, e in ipairs(okL and type(L) == "table" and L.ah or {}) do add(e.id) end
    local okS, sh = pcall(B.Shopping)
    for _, e in ipairs(okS and type(sh) == "table" and sh or {}) do add(e.id) end
  end
  for _, d in ipairs(M.deals or {}) do add(d.id) end
  return ids
end

function M.ScanTargets(ids)
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if M.mini then say(("Scan ciblé en cours : %d / %d."):format(M.mini.done, #M.mini.list)) return false end
  ids = ids or M.TargetIDs()
  if #ids == 0 then say("Rien à cibler : fais un scan complet, ou mets des objets dans tes sacs.") return false end
  M.mini = { list = ids, i = 0, done = 0, t0 = clock() }
  M.MiniNext()
  return true
end

function M.MiniNext()
  local m = M.mini
  if not m then return end
  if not M.ahOpen then M.mini = nil return end
  if m.waiting then
    if clock() - n0(m.sentAt) < 5 then return end
    m.waiting = nil                                       -- pas de réponse en 5 s : objet suivant
  end
  local fl = M.Flavor()
  local A = modernAH()
  if fl == "modern" and A and type(A.IsThrottledMessageSystemReady) == "function" and not try(A.IsThrottledMessageSystemReady) then
    m.throttled = (m.throttled or 0) + 1                  -- AUCTION_HOUSE_THROTTLED_SYSTEM_READY relancera
    T:After(0.5, M.MiniNext)                              -- filet de sécurité si l'événement n'arrive pas
    return
  end
  if fl == "classic" and not try(_G.CanSendAuctionQuery) then
    T:After(0, M.MiniNext)                                -- dès que la requête est permise (image suivante)
    return
  end
  m.i = m.i + 1
  local id = m.list[m.i]
  if not id then
    M.mini = nil
    local dt = max(0.001, clock() - m.t0)
    M.lastMini = { n = m.done, secs = floor(dt * 100 + 0.5) / 100, t = now() }
    M.FindDeals()
    M.CheckOwned()
    M.BuildQueue()
    say(("Scan ciblé fini : %d objet(s) en %s s."):format(m.done, tostring(M.lastMini.secs)))
    M.Refresh()
    return
  end
  m.waiting, m.sentAt = id, clock()
  if fl == "modern" then
    if not M.SearchItem(id) then m.waiting, m.i = nil, m.i - 1 T:After(0.2, M.MiniNext) return end
  else
    local name = API.ItemName and API.ItemName(id)
    if not name then m.waiting = nil T:After(0, M.MiniNext) return end
    try(_G.QueryAuctionItems, name, nil, nil, 0, false, -1, false, true, nil)
  end
  T:After(5.1, M.MiniNext)                                -- délai de garde (aucune action, juste la lecture)
end

function M.AUCTION_HOUSE_THROTTLED_SYSTEM_READY()
  if M.mini and not M.mini.waiting then M.MiniNext() end
end

-- parcours passif (API moderne) : prix minimum par objet de la page parcourue par le joueur
function M.OnBrowse()
  local A = modernAH()
  local res = A and try(A.GetBrowseResults)
  if type(res) ~= "table" then return end
  local t = now()
  for _, r in ipairs(res) do
    local id = type(r) == "table" and type(r.itemKey) == "table" and r.itemKey.itemID
    if id and n0(r.minPrice) > 0 then M.browse[id] = { min = n0(r.minPrice), qty = n0(r.totalQuantity), t = t } end
  end
  M.dbg.browse = M.dbg.browse + #res
end

function M.AUCTION_ITEM_LIST_UPDATE()
  if M.job then return end
  local batch, total = try(_G.GetNumAuctionItems, "list")
  batch, total = n0(batch), n0(total)
  if M.pendingAll then
    M.pendingAll = false
    processScan("list", true, batch, "TreeAI")
    return
  end
  local m = M.mini
  if m and m.waiting then                                 -- mini-scan classique : 1re page de l'objet visé
    local id = m.waiting
    local rows = {}
    for i = 1, min(batch, 50) do
      local lid, count, buy, owner = readListing(i, "list")
      if lid == id and buy > 0 then rows[#rows + 1] = { count = count, buy = buy, owner = owner } end
    end
    M.ApplyItemListings(id, rows)
    m.waiting, m.done = nil, m.done + 1
    T:After(0, M.MiniNext)
    return
  end
  if batch > 0 and batch <= 50 then
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
        it.t = now()
        M.cur[id] = it
      end
      M.FindDeals(items)
      M.Refresh()
    else
      M.FinishScan()
    end
  end
end

-- ------------------------------------------------------------------ mes enchères
-- scan complet en lecture / attendu → texte « 84 % » (sinon nil)
function M.Scanning()
  if M.job and n0(M.job.total) > 0 then return floor(100 * M.job.rows / max(1, M.job.total)) .. " %" end
  if M.pendingAll or M.repWait then return "attente du serveur" end
  return nil
end
-- objets « spéciaux » sans vrai marché : lié quand ramassé, objet de quête, commission / commande d'artisan
M.oddCache = {}
function M.OddCached(id)
  local v = M.oddCache[id]
  if v == nil then v = M.Odd(id) or false if M.ItemInfo(id) then M.oddCache[id] = v end end
  return v
end
local ODD_NAMES = { "^commission", "^crafting order", "^work order", "^commande d'artisan", "^bon de commande" }
function M.Odd(id)
  local name, _, _, _, _, _, _, _, _, _, _, classID, _, bindType = M.ItemInfo(id)
  if tonumber(classID) == 12 or tonumber(bindType) == 4 then return "objet de quête" end
  if tonumber(bindType) == 1 then return "lié quand ramassé" end
  local nm = tostring(name or itemName(id)):lower()
  for _, p in ipairs(ODD_NAMES) do if nm:find(p) then return "commission d'artisan" end end
  return nil
end

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
  local scanning = M.Scanning()
  for _, a in ipairs(mine) do
    a.unit = floor(a.buy / a.count)
    local it = a.id and M.cur[a.id]
    a.depositLost = F.deposit(vendorPrice(a.id), a.count, hrs, neutral)
    local odd = a.id and M.Odd(a.id)
    if scanning then
      a.state2, a.advice = "scan…", ("scan en cours (%s) : conseil après la lecture"):format(scanning)
    elseif odd or not a.id then
      a.state2, a.advice = "-", "objet spécial (" .. (odd or "inconnu") .. ") : pas de conseil de prix"
    else
      local comp = {}
      if it then for _, l in ipairs(it.listings) do comp[#comp + 1] = { l.unit, l.count } end end
      a.lowest = it and it.units[1]
      local adv = F.cancelAdvice(a.unit, a.count, comp, M.Liq(a.id), vendorPrice(a.id), neutral, hrs)
      a.state2, a.advice = adv.state, adv.text
      a.undercut = adv.state == "battue"
      a.worth = adv.action == "cancel"
      a.repost, a.gain = adv.repost, adv.gain
    end
    local fl = a.id and M.char and M.char.flips and M.char.flips[a.id]
    local mk = a.id and M.liqCache[a.id] and M.liqCache[a.id].market
    if fl and mk and mk > 0 and mk < n0(fl.stop) then
      a.stopHit = true
      a.advice = ("stop-loss : marché %s < %s, brade ou vends au marchand"):format(money(mk), money(fl.stop))
    end
    out[#out + 1] = a
  end
  M.owned = out
  local n = 0
  for _, a in ipairs(out) do if a.worth then n = n + 1 end end
  if n > 0 and M.Suggest and M.ahOpen then
    M:Suggest("undercut", ("HV : %d de tes ventes ont un concurrent moins cher (bouton « Annuler les dépassés » à l'HV)"):format(n),
      2, { ttl = 300 })
  end
  return out
end

function M.Cancel(a)
  if not (a and M.ahOpen) then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if a.state == "cancelled" or combatRefused() then return false end
  if not a.worth or M.Scanning() then say("Annulation non conseillée : " .. tostring(a.advice or "pas de données")) return false end
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
  M.sess.cancels, M.sess.expect = M.sess.cancels + 1, M.sess.expect + max(0, n0(a.gain))
  say(("Annulée : %s. Récupère-la au courrier, puis « Poster » à %s/u."):format(itemName(a.id), money(a.repost)))
  M.Refresh()
  return true
end

-- ------------------------------------------------------------------ vente (sacs)
local function comps(id)
  local comp = {}
  local it = M.cur[id]
  if it then for _, l in ipairs(it.listings) do comp[#comp + 1] = { l.unit, l.count } end
  elseif M.browse[id] then comp[1] = { M.browse[id].min, max(1, M.browse[id].qty) } end
  return comp
end
function M.PostPrice(id, count)
  local s = F.suggestPost(count, vendorPrice(id), M.Stats(id), comps(id), M.IsNeutral(), hours(), cfg("undercutPct", 0))
  s.id = id
  local liq = M.liqCache[id] or M.Liq(id)
  s.sold, s.proof = liq and liq.sold, liq and liq.ok or false
  if s.unit and s.sold and s.sold > 0 then s.days = F.days(s.sold, 0, count) end
  if s.unit and s.p then s.relist = F.relistLoss(s.deposit, s.p) end
  local fl = M.char and M.char.flips and M.char.flips[id]
  if fl then s.boughtUnit, s.stop = fl.unit, fl.stop end
  return s
end

function M.SellAdvice()
  local bag = {}
  if API.IterBags then
    for _, _, item in API.IterBags() do
      if item and item.itemID and not item.noValue and (item.quality or 1) >= 1 then
        bag[item.itemID] = (bag[item.itemID] or 0) + (item.count or 1)
      end
    end
  end
  local out = {}
  for id, count in pairs(bag) do
    -- (4.2 FIX42) seulement le surplus : « garde 12, vends 18 » (compos de tes plans, objets de quête)
    local keep, why, v = keepOf(id)
    local spare = count - keep
    if spare > 0 and not M.Odd(id) and (M.Stats(id) or M.cur[id] or M.browse[id]) then
      local stack = select(8, M.ItemInfo(id))
      local s = M.PostPrice(id, min(spare, max(1, n0(stack))))
      s.have, s.keep, s.keepWhy = spare, keep > 0 and keep or nil, why
      -- (5.0 BAGS) même seuil que l'avis des sacs : « marchand » là-bas = « marchand » ici (jamais deux avis)
      if v and v.action == "sell" and s.better == "ah" then s.better = "vendor" end
      if s.unit or s.vendor > 0 then out[#out + 1] = s end
    end
  end
  table.sort(out, function(a, b) return max(n0(a.net), a.vendor) > max(n0(b.net), b.vendor) end)
  M.sell = out
  return out
end

-- (4.1 AHPROFIT) quantité à poster = ce qui se vend pendant UNE mise en vente (ventes/jour × durée), moins ce
-- qui est déjà en vente (mes enchères + postées cette session) ; sans mesure des ventes : la pile entière
local function listedOf(id)
  local n = n0(M.sess.posted[id])
  for _, a in ipairs(M.owned or {}) do if a.id == id and a.state ~= "cancelled" then n = n + n0(a.count) end end
  return n
end
function F.postQty(have, sold, hrs, listed, split)
  have = max(1, n0(have))
  local v = tonumber(sold)
  local q = have
  if v and v > 0 then q = min(have, max(0, ceil(v * max(1, n0(hrs or 24)) / 24) - max(0, n0(listed)))) end
  if split and n0(split) > 0 and q > n0(split) then q = n0(split) end
  return q
end
-- file de mise en vente : une entrée par pile des sacs qui vaut mieux à l'HV qu'au marchand
function M.PostList()
  local out = {}
  if not API.IterBags then return out end
  local left = {}
  for bag, slot, item in API.IterBags() do
    local id = item and item.itemID
    if id and not item.noValue and not item.locked and (item.quality or 1) >= 1 and not M.Odd(id) and (M.Stats(id) or M.cur[id] or M.browse[id]) then
      local have = max(1, n0(item.count))
      local liq = M.Liq(id)
      if left[id] == nil then
        -- (4.2 FIX42) ce que tes métiers / quêtes demandent reste dans les sacs : seul le surplus part en vente
        local total = 0
        for _, _, it2 in API.IterBags() do if it2 and it2.itemID == id then total = total + max(1, n0(it2.count)) end end
        left[id] = { listed = listedOf(id), spare = total - keepOf(id) }
      end
      have = min(have, max(0, left[id].spare))
      local qty = have > 0 and F.postQty(have, liq.sold, hours(), left[id].listed) or 0
      if qty > 0 then
        local s = M.PostPrice(id, qty)
        if s.split and s.split < qty then qty = s.split s = M.PostPrice(id, qty) end
        local okPost = s.unit and s.better == "ah"
        if okPost and brain() then   -- (5.0 BAGS) l'avis des sacs dit « marchand » : pas à l'HV (même seuil partout)
          local okV, v = pcall(brain().Verdict, id)
          okPost = okV and type(v) == "table" and v.action ~= "sell"
        end
        if okPost then
          s.bag, s.slot, s.have = bag, slot, have
          left[id].listed = left[id].listed + qty
          left[id].spare = left[id].spare - qty
          out[#out + 1] = s
        end
      end
    end
    if #out >= 24 then break end
  end
  table.sort(out, function(a, b) return n0(a.net) > n0(b.net) end)
  return out
end

function M.Post(p)
  if not (p and M.ahOpen) then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if p.state == "posted" or p.state == "skip" or combatRefused() then return false end
  local sc = M.Scanning()
  if sc then say(("Scan en cours (%s) : mise en vente après la lecture des prix."):format(sc)) return false end
  local item = API.BagItem and API.BagItem(p.bag, p.slot)
  if not (item and item.itemID == p.id and n0(item.count) == n0(p.have or p.count)) then
    p.state = "skip"
    say(("%s : la pile a bougé dans les sacs, ignorée."):format(itemName(p.id)))
    return false
  end
  local A, fl = modernAH(), M.Flavor()
  -- prix plus vieux que 5 min : clic = une recherche (non protégée) ; le clic suivant poste
  local it = M.cur[p.id]
  if fl == "modern" and (not it or now() - n0(it.t) > 300) and p.state ~= "checked" then
    p.state = "check"
    if not M.SearchItem(p.id) then p.state = nil say("Blizzard limite les recherches : reclique dans un instant.") return false end
    T:After(3, function() if p.state == "check" then p.state = "checked" M.Refresh() end end)
    return false
  end
  local s = M.PostPrice(p.id, p.count)
  if not s.unit or s.better ~= "ah" or s.unit < floor(p.unit * 0.95) then
    p.state = "skip"
    say(("%s : le prix a bougé (%s → %s), mise en vente ignorée."):format(itemName(p.id), money(p.unit), money(s.unit)))
    return false
  end
  local unit = s.unit
  if fl == "modern" and A then
    if type(A.SupportsCopperValues) == "function" and not try(A.SupportsCopperValues) then unit = max(100, floor(unit / 100) * 100) end
    local IL = rawget(_G, "ItemLocation")
    local loc = type(IL) == "table" and type(IL.CreateFromBagAndSlot) == "function" and try(IL.CreateFromBagAndSlot, IL, p.bag, p.slot)
    if not loc then say("Mise en vente impossible : ItemLocation absent.") logErr("ItemLocation absent") return false end
    local E = rawget(_G, "Enum")
    local COMMOD = (E and E.ItemCommodityStatus and E.ItemCommodityStatus.Commodity) or 2
    if try(A.GetItemCommodityStatus, loc) == COMMOD then
      try(A.PostCommodity, loc, durIndex(), p.count, unit)                      -- action protégée (clic)
    else
      try(A.PostItem, loc, durIndex(), p.count, nil, unit * p.count)            -- action protégée (clic)
    end
  elseif fl == "classic" then
    local pick = (rawget(_G, "C_Container") and _G.C_Container.PickupContainerItem) or rawget(_G, "PickupContainerItem")
    try(pick, p.bag, p.slot)
    try(_G.ClickAuctionSellItemButton)
    local name = try(_G.GetAuctionSellItemInfo)
    if not name then try(_G.ClearCursor) say("Ouvre l'onglet « Vendre » de l'HV pour poster.") return false end
    local buy = unit * p.count
    try(_G.PostAuction, max(1, floor(buy * 0.95)), buy, durIndex(), p.count, 1)  -- action protégée (clic)
  else
    return false
  end
  p.state, p.posted = "posted", unit
  local ss = M.sess
  ss.posts, ss.postNet = ss.posts + 1, ss.postNet + max(0, n0(s.net))
  ss.posted[p.id] = n0(ss.posted[p.id]) + p.count
  if T.Stats and T.Stats.NotePost then pcall(T.Stats.NotePost, itemName(p.id)) end
  if M.char then
    M.char.deposits = M.char.deposits or {}
    local k = dateKey()
    M.char.deposits[k] = n0(M.char.deposits[k]) + n0(s.deposit)
  end
  say(("Mis en vente : %s x%d à %s/u (dépôt %s, net attendu %s)."):format(itemName(p.id), p.count, money(unit), money(s.deposit), money(s.net)))
  return true
end

-- ------------------------------------------------------------------ file « Suivant » (1 clic = 1 action)
function M.BuildQueue()
  local q = {}
  for _, d in ipairs(M.plan or {}) do
    if d.state ~= "bought" and d.state ~= "gone" then q[#q + 1] = { kind = "buy", d = d } end
  end
  if M.ahOpen then
    for _, p in ipairs(M.PostList()) do q[#q + 1] = { kind = "post", p = p } end
  end
  for _, a in ipairs(M.owned or {}) do
    if a.undercut and a.worth and a.state ~= "cancelled" then q[#q + 1] = { kind = "cancel", a = a } end
  end
  M.queue, M.qi = q, 1
  return q
end

local function qDone(e)
  if e.kind == "buy" then return e.d.state == "bought" or e.d.state == "gone" end
  if e.kind == "cancel" then return e.a.state == "cancelled" end
  return e.p.state == "posted" or e.p.state == "skip"
end
function M.QueueHead()
  while M.queue[M.qi] and qDone(M.queue[M.qi]) do M.qi = M.qi + 1 end
  return M.queue[M.qi]
end
function M.NextLabel()
  local e = M.QueueHead()
  if not e then return "Suivant : rien à faire", false end
  if e.kind == "buy" then
    local d = e.d
    local verb = (M.Flavor() == "modern" and (d.state == nil and "vérifier" or d.state == "confirm" and "confirmer")) or "acheter"
    return ("Suivant : %s %s x%d (%s)"):format(verb, itemName(d.id), d.count, money(d.buyNow or d.buy)), true
  elseif e.kind == "cancel" then
    if M.Scanning() or not e.a.worth then return "Suivant : scan en cours…", false end
    return ("Suivant : annuler %s x%d (reposter %s/u)"):format(itemName(e.a.id), e.a.count, money(e.a.repost)), true
  end
  local p = e.p
  if M.Scanning() then return "Suivant : scan en cours…", false end
  return ("Suivant : %s %s x%d à %s/u"):format(p.state == "check" and "(vérif…)" or "poster", itemName(p.id), p.count, money(p.unit)), p.state ~= "check"
end
-- appelé UNIQUEMENT par le OnClick du bouton « Suivant » : au plus UNE action protégée, puis on avance
function M.Next()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if not M.QueueHead() then M.BuildQueue() end
  local e = M.QueueHead()
  if not e then say("File vide : scanne, ajuste le budget ou la réserve (/tai hv debug).") M.Refresh() return false end
  local ok
  if e.kind == "buy" then ok = M.Buy(e.d)
  elseif e.kind == "cancel" then ok = M.Cancel(e.a)
  else ok = M.Post(e.p) end
  M.QueueHead()
  M.Refresh()
  return ok
end

-- ------------------------------------------------------------------ mode rafale (« une touche = une action », style TSM)
-- Un vrai mode automatique est IMPOSSIBLE et interdit (achat / mise en vente / annulation = fonctions protégées,
-- une action matérielle chacune ; les enchaîner seul = un bot). La rafale fait le plus rapide permis : chaque
-- appui (gros bouton ou touche « TreeAI : action HV suivante ») exécute LA prochaine action de la file sûre
-- (achats sûrs dans les plafonds → mises en vente au prix calculé → annulations rentables), au plus UN appel
-- protégé, dans le gestionnaire du clic / de la touche. Entre deux appuis (sans action protégée) : la file est
-- recalculée et le prix de la prochaine action revérifié (recherche limitée par Blizzard), pour que l'appui
-- suivant agisse directement. Arrêt : file vide, scan partiel, réserve d'or atteinte.
M.raf = { n = 0, gain = 0 }
-- petit son après chaque action réussie : système de sons du Core (genre « ah »), réglages du joueur respectés ;
-- sans le délai anti-spam (c'est le joueur qui appuie, un son par appui)
local function tick()
  local s = T.settings
  if not (s and s.sound ~= false) or not (T.SoundKindOn and T:SoundKindOn("ah")) or not T.Sound then return end
  pcall(T.Sound, T, "ah", { preview = true })
end
local function qActive(e)
  if e.kind == "buy" and M.raf.noBuy then return false end
  if e.kind == "buy" then return not (e.d.state == "bought" or e.d.state == "gone") end
  if e.kind == "cancel" then return e.a.state ~= "cancelled" and e.a.worth end
  return not (e.p.state == "posted" or e.p.state == "skip")
end
function M.RafaleHead()
  local q = M.queue or {}
  for i = M.qi or 1, #q do if qActive(q[i]) then return q[i], i, #q end end
  return nil, 0, #q
end
function M.RafaleText()
  local sc = M.Scanning()
  if sc then return ("En pause : scan en cours (%s)."):format(sc), false end
  local e, i, n = M.RafaleHead()
  if not e then return "Rien à faire pour l'instant : appuie pour scanner.", M.ahOpen end
  local what
  if e.kind == "buy" then
    local d = e.d
    local unit = money(floor(n0(d.buyNow or d.buy) / max(1, d.count)))
    if d.state == "confirm" then what = ("confirmer l'achat de %d × %s (%s)"):format(d.count, itemName(d.id), money(d.buyNow or d.buy))
    elseif d.state == "search" or d.state == "starting" then what = ("vérification du prix de %s…"):format(itemName(d.id))
    elseif M.Flavor() == "modern" and d.state ~= "ready" then what = ("vérifier puis acheter %d × %s à %s"):format(d.count, itemName(d.id), unit)
    else what = ("acheter %d × %s à %s"):format(d.count, itemName(d.id), unit) end
  elseif e.kind == "post" then
    local p = e.p
    what = p.state == "check" and ("vérification du prix de %s…"):format(itemName(p.id))
      or ("mettre en vente %d × %s à %s/u"):format(p.count, itemName(p.id), money(p.unit))
  else
    what = ("annuler %d × %s (reposter %s/u)"):format(e.a.count, itemName(e.a.id), money(e.a.repost))
  end
  return ("Prochaine action (%d/%d) : %s"):format(i, n, what), M.ahOpen
end
function M.RafaleSummary()
  local r = M.raf
  return ("Série : %d action%s, +%s prévus"):format(r.n, r.n > 1 and "s" or "", money(r.gain))
end
local function rafaleStop(why)
  local r = M.raf
  if r.n > 0 or r.on then
    say(("%s%s."):format(M.RafaleSummary(), why and (" — " .. why) or ""))
    if M.char then M.char.rafale = { n = r.n, gain = r.gain, t = now() } end
  end
  r.on = false
end
-- entre deux appuis (aucune action protégée) : file recalculée + prix de la prochaine action revérifié
function M.RafalePrefetch()
  M.prefetchQ = nil
  if not (M.ahOpen and M.raf.on) or M.Scanning() or M.pendingSearch then return end
  local e = M.RafaleHead()
  if not e or M.Flavor() ~= "modern" then return end
  if e.kind == "buy" and (e.d.state == nil or e.d.state == "gone") and e.d.state ~= "bought" then
    local d = e.d
    if d.state == "gone" then return end
    d.state, M.pendingSearch = "search", d
    if not M.SearchItem(d.id) then d.state, M.pendingSearch = nil, nil T:After(0.5, M.RafalePrefetch) end
  elseif e.kind == "post" and e.p.state == nil then
    local it = M.cur[e.p.id]
    if not it or now() - n0(it.t) > 300 then
      e.p.state = "check"
      if not M.SearchItem(e.p.id) then e.p.state = nil T:After(0.5, M.RafalePrefetch) end
    end
  end
  M.Refresh()
end
local function schedulePrefetch()
  if M.prefetchQ then return end
  M.prefetchQ = true
  T:After(0.3, M.RafalePrefetch)
end
-- UN appui = au plus UNE action protégée (appelé seulement par le OnClick du bouton ou la touche liée)
function M.RafaleNext()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if combatRefused() then M.Refresh() return false end
  local r = M.raf
  if not r.on then r.on, r.n, r.gain, r.noBuy, r.t0 = true, 0, 0, false, now() M.Plan() M.BuildQueue() end
  local sc = M.Scanning()
  if sc then say(("En pause : scan en cours (%s)."):format(sc)) M.Refresh() return false end
  local e = M.RafaleHead()
  if not e then
    M.Plan() M.BuildQueue() e = M.RafaleHead()
  end
  if not e then
    if r.n > 0 then rafaleStop("file vide") M.Refresh() return false end
    -- file vide dès le départ : cet appui lance le scan (complet si permis, sinon ciblé) — aucune action protégée d'achat
    if M.FullCooldown() <= 0 then M.ScanAll() else M.ScanTargets() end
    r.on = false
    M.Refresh()
    return false
  end
  if e.kind == "buy" and e.d.state ~= "confirm" and e.d.state ~= "ready" and n0(e.d.buy) > M.Gold().budget then
    r.noBuy = true                                         -- réserve d'or atteinte : on n'achète plus
    say("Réserve d'or atteinte : plus d'achats (mises en vente et annulations continuent).")
    M.Refresh()
    return false
  end
  local ok
  if e.kind == "buy" then ok = M.Buy(e.d)
  elseif e.kind == "cancel" then ok = M.Cancel(e.a)
  else ok = M.Post(e.p) end
  if ok then
    r.n = r.n + 1
    if e.kind == "buy" then r.gain = r.gain + max(0, n0(e.d.profit))
    elseif e.kind == "cancel" then r.gain = r.gain + max(0, n0(e.a.gain)) end
    tick()
    if e.kind ~= "buy" then M.postRows = nil end
    M.Plan()
    M.BuildQueue()                                         -- file recalculée après chaque action
  end
  if not M.RafaleHead() and r.n > 0 then rafaleStop("file vide") end
  schedulePrefetch()
  M.Refresh()
  return ok
end
T.AuctionNext = function() return M.RafaleNext() end      -- appelé par Bindings.xml (touche = action matérielle)

-- « Lier à une touche » : même chemin que le module Touches (confirmation, hors combat, SetBinding + SaveBindings)
local RAF_CMD = "TREEAI_AH_NEXT"
local RAF_KEYS = { "CTRL-SPACE", "ALT-SPACE", "SHIFT-SPACE", "CTRL-B", "ALT-B" }
M.RAF_CMD = RAF_CMD
function M.RafaleKey()
  local k = try(_G.GetBindingKey, RAF_CMD)
  return type(k) == "string" and k ~= "" and k or nil
end
function M.BindRafale()
  if M.RafaleKey() then say(("Action suivante déjà liée à %s."):format(M.RafaleKey())) return false end
  local B = T.GetModule and T:GetModule("Binds")
  local key
  for _, k in ipairs(RAF_KEYS) do
    local a = try(_G.GetBindingAction, k, true)
    if a == nil then a = try(_G.GetBindingAction, k) end
    if a == nil or a == "" then key = k break end
  end
  if not key then say("Aucune touche libre proposée : Échap > Raccourcis > AddOns > TreeAI > « action HV suivante ».") return false end
  local tg = { id = "ahnext", how = "bind", key = key, item = { kind = "command", name = "TreeAI : action HV suivante", cmd = RAF_CMD } }
  if not (B and B.Bind) then say("Module Touches absent : Échap > Raccourcis > AddOns > TreeAI.") return false end
  local confirmed = M.bindAsk and M.bindAsk.key == key and now() - M.bindAsk.t < 15
  M.bindAsk = not confirmed and { key = key, t = now() } or nil
  local ok = B:Bind(tg, confirmed)
  M.Refresh()
  return ok
end

-- ------------------------------------------------------------------ boutons rapides (1 clic = 1 action, dans le OnClick)
function M.BuyHead()
  for _, d in ipairs(M.plan or {}) do if d.state ~= "bought" and d.state ~= "gone" then return d end end
end
function M.PostHead()
  if M.ahOpen then M.postRows = M.postRows or M.PostList() end
  for _, p in ipairs(M.postRows or {}) do if p.state ~= "posted" and p.state ~= "skip" then return p end end
end
function M.CancelHead()
  for _, a in ipairs(M.owned or {}) do if a.undercut and a.worth and a.state ~= "cancelled" then return a end end
end
local function left(list, done)
  local n = 0
  for _, e in ipairs(list or {}) do if not done(e) then n = n + 1 end end
  return n
end
function M.Counts()
  return left(M.plan, function(d) return d.state == "bought" or d.state == "gone" end),
    left(M.postRows, function(p) return p.state == "posted" or p.state == "skip" end),
    left(M.owned, function(a) return not (a.undercut and a.worth) or a.state == "cancelled" end)
end
-- « Acheter la meilleure offre » : la meilleure affaire sûre du plan (moderne : vérifier → acheter → confirmer)
function M.BuyBest()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  local d = M.BuyHead()
  if not d then say(F.NONE_TEXT .. ".") M.Refresh() return false end
  local ok = M.Buy(d)
  M.Refresh()
  return ok
end
-- « Remettre en vente » : la pile suivante des sacs, au prix « 1 pc sous le moins cher » (jamais sous le plancher)
function M.RepostNext()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  local p = M.PostHead()
  if not p then M.postRows = nil p = M.PostHead() end
  if not p then say("Rien à poster (objets annulés : récupère-les d'abord au courrier).") M.Refresh() return false end
  local ok = M.Post(p)
  M.Refresh()
  return ok
end
-- « Annuler les dépassés » (un concurrent vend moins cher) : une annulation par clic, si le dépôt perdu vaut le coup
function M.CancelNext()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return false end
  local a = M.CancelHead()
  if not a then say("Aucune vente dépassée à annuler.") M.Refresh() return false end
  local ok = M.Cancel(a)
  M.Refresh()
  return ok
end
-- (4.1 AHPROFIT) libellés du panneau : « Acheter (n) » · « Poster (n) » · « Annuler les dépassés (n) » + la ligne
-- « ce que fera le prochain clic » sous chacun (objet, prix, prix max revérifié)
function M.ActionLabels()
  local nb, np, nc = M.Counts()
  local d = M.BuyHead()
  local buy, buyOn = ("Acheter (%d)"):format(nb), d ~= nil
  if d and d.state == "confirm" then buy = "Confirmer l'achat"
  elseif d and (d.state == "search" or d.state == "starting") then buy, buyOn = "Vérification du prix…", false end
  local p = M.PostHead()
  local post = ("Poster (%d)"):format(np)
  if p and p.state == "check" then post = "Vérification du prix…" end
  local c = M.CancelHead()
  local buySub = d and ("%s ×%d à %s/u (max %s) · +%s"):format(itemName(d.id), d.count, money(floor(n0(d.buyNow or d.buy) / max(1, d.count))),
    money(floor(n0(d.buy) / max(1, d.count))), money(d.profit)) or "rien de sûr à acheter"
  local postSub = p and ("%s ×%d à %s/u · net ≈ %s"):format(itemName(p.id), p.count, money(p.unit), money(p.net)) or "rien à poster"
  local cancelSub = c and ("%s ×%d : reposter à %s/u"):format(itemName(c.id), c.count, money(c.repost)) or "aucune vente dépassée"
  local hint
  if d then
    hint = ("Prochain achat : %s x%d pour %s, +%s net%s"):format(itemName(d.id), d.count, money(d.buyNow or d.buy), money(d.profit),
      d.kind == "vendor" and " · revente au marchand" or "")
  elseif p then
    hint = ("Prochaine vente : %s x%d à %s/u (dépôt %s)"):format(itemName(p.id), p.count, money(p.unit), money(p.deposit))
  else
    hint = F.NONE_TEXT .. "."
  end
  local sc = M.Scanning()
  if sc then hint = ("Scan en cours (%s) : conseils de vente et d'annulation après la lecture."):format(sc) end
  return { buy = buy, buyOn = buyOn and M.ahOpen, post = post, postOn = p ~= nil and p.state ~= "check" and M.ahOpen and not sc,
    cancel = ("Annuler les dépassés (%d)"):format(nc), cancelOn = nc > 0 and M.ahOpen and not sc, hint = hint,
    buySub = buySub, postSub = postSub, cancelSub = cancelSub }
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
        if T.Stats and T.Stats.NoteAuction then pcall(T.Stats.NoteAuction, kind, e.item, arrived * 7200) end
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
    return ("%s : achat %s, revente au marchand %s, +%s garanti"):format(lbl, money(d.buy), money(d.sell), money(d.profit))
  end
  return ("%s : achat %s (−%d %%), revente %s, +%s net (commission + dépôt %s inclus) · se vend ~%s/jour · conf. %d %% · stop-loss %s/u"):format(lbl,
    money(d.buy), floor(d.discount * 100 + 0.5), money(d.sell), money(d.profit), money(d.deposit), dec(d.sold),
    floor(d.conf * 100 + 0.5), money(d.stop and d.stop.unit or 0))
end

function M.SellText(s)
  local name = itemName(s.id)
  if not s.unit then return ("%s : pas de prix de marché (marchand %s)"):format(name, money(s.vendor)) end
  if s.better == "vendor" then
    return ("%s : mieux au marchand (%s ; HV net %s après dépôt)"):format(name, money(s.vendor), money(s.net))
  end
  local t = ("%s x%d : %s/u (dépôt %s, net %s, marchand %s)"):format(name, s.count, money(s.unit), money(s.deposit), money(s.net), money(s.vendor))
  if s.relist and s.relist > 0 then t = t .. (" — si invendu : −%s par remise en vente"):format(money(s.deposit)) end
  if s.split then t = t .. (" — piles de %d"):format(s.split) end
  if s.boughtUnit then t = t .. (" — acheté %s/u, stop-loss %s/u"):format(money(s.boughtUnit), money(s.stop)) end
  return t
end

-- objet posé dans l'emplacement de vente (HV classique) → prix conseillé immédiat
function M.NEW_AUCTION_UPDATE()
  local name, _, count, _, _, vendor = try(_G.GetAuctionSellItemInfo)
  if not name then if M.Clear then M:Clear("post") end return end
  local link = select(2, M.ItemInfo(name))
  local id = idFromLink(link)
  if not id then return end
  local unitVendor = n0(vendor) > 0 and floor(n0(vendor) / max(1, n0(count))) or vendorPrice(id)
  local s = F.suggestPost(max(1, n0(count)), unitVendor, M.Stats(id), comps(id), M.IsNeutral(), hours())
  local dep = try(_G.CalculateAuctionDeposit, durIndex(), max(1, n0(count)))
  if n0(dep) > 0 then s.deposit = n0(dep) end
  s.id = id
  local St = T.Stats   -- tes ventes passées de cet objet (taux réel, délai)
  local rate, hrs = St and St.SaleRate and St:SaleRate(itemName(id))
  local mine = rate and (" — tes ventes : %d %% vendues%s"):format(floor(rate * 100 + 0.5), hrs and (" en ~" .. floor(hrs + 0.5) .. " h") or "") or ""
  if M.Suggest then M:Suggest("post", "Vente : " .. M.SellText(s) .. mine, 2, { ttl = 120 }) end
  return s
end

function M.BilanLines()
  local g = M.Gold()
  local r = M.Realized()
  local L = {}
  L[#L + 1] = ("Or total %s · réserve %s (garder %s, entraînement/réparations %s, monture %s)"):format(money(g.total), money(g.reserve),
    money(g.parts.keep), money(g.parts.train), money(g.parts.mount))
  L[#L + 1] = ("Budget de revente %s%s · dépensé aujourd'hui %s"):format(money(g.budget), g.cap > 0 and (" (plafond/jour " .. money(g.cap) .. ")") or "", money(g.spent))
  L[#L + 1] = ("Argent immobilisé %s (en vente %s, acheté non revendu %s)"):format(money(g.invested), money(g.listed), money(g.held))
  local np = #(M.plan or {})   -- (4.0) pluriels écrits, pas de « → » (absent des polices du jeu : la fenêtre HV ne le filtre pas)
  L[#L + 1] = ("Plan : %d achat%s pour %s, profit attendu %s (max %d %% de ton or par objet, quantité ≤ ventes de %d j)"):format(np,
    np > 1 and "s" or "", money(M.planSpend), money(M.planGain), n0(cfg("capGold", 20)), n0(cfg("qtyDays", 3)))
  local lr = M.char and M.char.rafale
  if lr and n0(lr.n) > 0 then L[#L + 1] = ("Dernière série : %d action%s, +%s prévus"):format(n0(lr.n), n0(lr.n) > 1 and "s" or "", money(lr.gain)) end
  local deps = M.char and M.char.deposits and M.char.deposits[dateKey()]
  L[#L + 1] = ("Profit réalisé : aujourd'hui %s, 7 j %s, total %s · dépôts payés aujourd'hui %s"):format(money(r.today), money(r.week), money(r.total), money(deps))
  for i, d in ipairs(M.plan or {}) do
    if i > 6 then break end
    L[#L + 1] = ("  achat %d : %s x%d %s, gain +%s (risque %s)"):format(i, itemName(d.id), d.count, money(d.buy), money(d.adj), M.RiskText(d))
  end
  local ev = (M.char and M.char.ledger) or {}
  local KIND = { buy = "acheté", sell = "vendu", expired = "expiré", cancelled = "annulé" }
  for i = #ev, max(1, #ev - 20), -1 do
    local e = ev[i]
    L[#L + 1] = ("  %s %s %s x%d %s"):format(e.day or "", KIND[e.type] or e.type or "", tostring(e.item or "?"), n0(e.qty), money(n0(e.money) - n0(e.cut)))
  end
  return L
end

function M.Report(what)
  what = tostring(what or "")
  local P = function(s) out(s) end
  if what:find("^vend") then
    local s = M.SellAdvice()
    if #s == 0 then P("Rien à conseiller : ouvre l'HV et clique Scanner.") end
    for i = 1, min(8, #s) do P(M.SellText(s[i])) end
    return
  end
  if what:find("^jour") or what:find("^ledger") or what:find("^bilan") then
    local r = M.Realized()
    P(("Profit réalisé : aujourd'hui %s, 7 j %s, total %s."):format(money(r.today), money(r.week), money(r.total)))
    for _, l in ipairs(M.BilanLines()) do P(l) end
    return
  end
  local g = M.Gold()
  local age = M.lastFull and ceil((now() - M.lastFull) / 60)
  P(("HV %s · dernier scan complet : %s · budget de revente %s (réserve %s)"):format(M.IsNeutral() and "neutre (15 %)" or "de faction (5 %)",
    age and ("il y a " .. age .. " min") or "jamais", money(g.budget), money(g.reserve)))
  if #M.deals == 0 then
    P(F.NONE_TEXT .. ".")
    for _, w in ipairs((M.none and M.none.why) or { "Fais un scan complet à l'HV (plusieurs scans espacés sur 3 jours)." }) do P("  " .. w) end
  end
  for i = 1, min(6, #M.deals) do P(i .. ". " .. M.DealText(M.deals[i])) end
  local nb = 0
  for _, a in ipairs(M.owned) do if a.worth then nb = nb + 1 end end
  if nb > 0 then P(nb .. " vente(s) dépassée(s) à annuler : bouton « Annuler les dépassés » à l'HV.") end
end

function M.DebugLines()
  local L = {}
  local A = modernAH()
  local build, _, _, iface = try(_G.GetBuildInfo)
  L[#L + 1] = ("Client %s (interface %s, projet %s) · API HV : %s (C_AuctionHouse %s, ReplicateItems %s, QueryAuctionItems %s, forcée : %s)"):format(
    tostring(build), tostring(iface), tostring(rawget(_G, "WOW_PROJECT_ID")), tostring(M.Flavor() or "AUCUNE"),
    rawget(_G, "C_AuctionHouse") and "oui" or "non", A and "oui" or "non", legacyAH() and "oui" or "non", tostring(cfg("api", "auto")))
  L[#L + 1] = ("Interface Blizzard : AuctionHouseFrame %s, AuctionFrame %s · HV ouvert : %s"):format(
    rawget(_G, "AuctionHouseFrame") and "oui" or "non", rawget(_G, "AuctionFrame") and "oui" or "non", M.ahOpen and "oui" or "non")
  local thr
  if A and type(A.IsThrottledMessageSystemReady) == "function" then thr = try(A.IsThrottledMessageSystemReady) and "prêt" or "limité (attente)"
  elseif type(rawget(_G, "CanSendAuctionQuery")) == "function" then
    local c, all = try(_G.CanSendAuctionQuery)
    thr = ("requête %s, getAll %s"):format(c and "ok" or "non", all and "ok" or "non")
  end
  L[#L + 1] = ("Limiteur : %s · scan complet possible dans %s · demandé : %s · annonces complètes en mémoire : %s"):format(tostring(thr or "?"),
    mmss(M.FullCooldown()), M.pendingAll and "oui (attente réponse)" or "non", tostring(A and try(A.GetNumReplicateItems) or "-"))
  local j = M.job
  if j then
    local dt = max(0.001, clock() - j.t0)
    L[#L + 1] = ("Lecture en cours : %d / %d annonces (%d /s)"):format(j.rows, j.total, floor(j.rows / dt))
  end
  local s = M.lastScan
  if s then
    L[#L + 1] = ("Dernier scan : %s%s par %s, %d annonces, %d objets, %d illisibles, %s s, %d annonces/s, il y a %s"):format(tostring(s.src),
      s.full and " (complet)" or "", tostring(s.by or "?"), s.rows, s.items, s.unresolved, tostring(s.secs), s.speed, mmss(now() - s.t))
  else
    L[#L + 1] = "Dernier scan : aucun dans cette session"
  end
  if M.lastMini then L[#L + 1] = ("Dernier scan ciblé : %d objets en %s s"):format(M.lastMini.n, tostring(M.lastMini.secs)) end
  if M.mini then L[#L + 1] = ("Scan ciblé en cours : %d / %d (attentes limiteur %d)"):format(M.mini.done, #M.mini.list, n0(M.mini.throttled)) end
  local n = 0
  for _ in pairs(M.cur) do n = n + 1 end
  local rd = M.RealmData()
  L[#L + 1] = ("Objets en mémoire %d · suivis (historique) %d · parcours passif vus %d · recherches %d · affaires %d · plan %d · file %d (pos %d)"):format(
    n, rd and rd.n or 0, M.dbg.browse, M.dbg.searches, #M.deals, #(M.plan or {}), #(M.queue or {}), M.qi or 1)
  local api = atr()
  if api then
    local sample = M.deals[1] and M.deals[1].id
    local p, age = M.AtrPrice(sample)
    L[#L + 1] = ("Auctionator : API v1 oui · callback %s · dernière mise à jour %s%s"):format(M.atrRegistered and "enregistré (TreeAI)" or "non",
      M.atrUpdated and ("il y a " .. mmss(now() - M.atrUpdated)) or "pas vue", sample and (" · ex. " .. itemName(sample) .. " " .. money(p) .. " (âge " .. tostring(age) .. " j)") or "")
  else
    L[#L + 1] = "Auctionator : absent (ou pas d'API v1)"
  end
  for _, e in ipairs(M.dbg.errors) do L[#L + 1] = "Erreur : " .. e end
  return L
end

-- ------------------------------------------------------------------ (4.1 AHPROFIT) le bénéfice au-delà des annonces
-- Mêmes règles honnêtes partout (miroir de auction.py : vendor_to_ah / craft_profit) : prix de revente = marché de NOS
-- scans (médiane des plus bas) moins la moitié de sa volatilité ; il faut la preuve que ça se vend (liquidité ok) ;
-- commission + dépôt × P(invendu) déduits ; bénéfice net ≥ max(1 pa, 15 % de la mise) ; quantité ≤ la demande.
local function resale(liq) return max(1, floor(n0(liq.market) * (1 - 0.5 * n0(liq.vol)))) end
-- acheter chez un marchand (prix connu, à l'unité) pour revendre à l'HV
function F.vendorToAH(buyUnit, liq, vendorSell, neutral, hrs, held, minProfit)
  if not (liq and liq.ok) or n0(buyUnit) <= 0 then return nil end
  local qty = F.maxQty(liq.sold, nil, held)
  if qty <= 0 then return nil end
  local sellUnit = resale(liq)
  local dep = F.deposit(vendorSell, qty, hrs, neutral)
  local p = F.pSell(liq.sold, 0, qty, hrs)
  local cost = floor(n0(buyUnit)) * qty
  local profit = F.profit(cost, sellUnit * qty, dep, p, neutral)
  if profit < F.minNet(cost, minProfit) then return nil end
  return { kind = "vendorAH", buyUnit = floor(n0(buyUnit)), sellUnit = sellUnit, count = qty, buy = cost, sell = sellUnit * qty,
    deposit = dep, p = p, profit = profit, days = F.days(liq.sold, 0, qty), sold = liq.sold }
end
-- fabriquer (coût des composants pour UNE fabrication) pour vendre à l'HV
function F.craft(cost, made, liq, vendorSell, neutral, hrs, minProfit)
  if not (liq and liq.ok) or n0(cost) <= 0 then return nil end
  made = max(1, n0(made))
  local sellUnit = resale(liq)
  local dep = F.deposit(vendorSell, made, hrs, neutral)
  local p = F.pSell(liq.sold, 0, made, hrs)
  local profit = F.profit(n0(cost), sellUnit * made, dep, p, neutral)
  if profit < F.minNet(cost, minProfit) then return nil end
  return { kind = "craft", cost = n0(cost), count = made, sellUnit = sellUnit, sell = sellUnit * made, deposit = dep, p = p,
    profit = profit, sold = liq.sold, days = F.days(liq.sold, 0, made) }
end

local function recipes() return T.Data and T.Data.Recipes end
local function minProfitC() return max(1, n0(cfg("minProfit", 1))) * 100 end
local function tracked(id) local r = M.RealmData() return r and r.items[id] ~= nil end

-- marchand ouvert : on retient ce qu'il vend (prix à l'unité, quantité limitée) pour les achats marchand → HV
local function merchantRow(i)
  local id = tonumber((try(_G.GetMerchantItemID, i))) or idFromLink(try(_G.GetMerchantItemLink, i))
  local MF = rawget(_G, "C_MerchantFrame")
  local info = MF and type(MF.GetItemInfo) == "function" and try(MF.GetItemInfo, i)
  local price, lot, avail, ext
  if type(info) == "table" then price, lot, avail, ext = n0(info.price), max(1, n0(info.stackCount)), tonumber(info.numAvailable) or -1, info.hasExtendedCost
  else
    local _, _, p, q, a, _, _, e = try(_G.GetMerchantItemInfo, i)
    price, lot, avail, ext = n0(p), max(1, n0(q)), tonumber(a) or -1, e
  end
  return id, price, lot, avail, ext
end
function M.MERCHANT_SHOW()
  local r = M.RealmData()
  if not r then return end
  r.vend = r.vend or {}
  local npc = try(_G.UnitName, "npc")
  local n = 0
  for _ in pairs(r.vend) do n = n + 1 end
  for i = 1, min(120, n0(try(_G.GetMerchantNumItems))) do
    local id, price, lot, avail, ext = merchantRow(i)
    if id and price > 0 and not ext and (r.vend[id] or n < 400) then
      if not r.vend[id] then n = n + 1 end
      r.vend[id] = { p = floor(price / lot), lim = avail ~= -1 or nil, npc = type(npc) == "string" and npc or nil, t = now() }
    end
  end
end

function M.VendorFlips(limit)
  local out, seen = {}, {}
  local neutral, hrs, mp = M.IsNeutral(), hours(), minProfitC()
  local function try1(id, unit, src)
    if seen[id] or not tracked(id) or M.OddCached(id) then return end
    seen[id] = true
    local liq = M.Liq(id)
    local d = F.vendorToAH(unit, liq, vendorPrice(id), neutral, hrs, held(id), mp)
    if d then d.id, d.npc, d.lim = id, src and src.npc, src and src.lim out[#out + 1] = d end
  end
  local r = M.RealmData()
  for id, v in pairs((r and r.vend) or {}) do try1(id, n0(v.p), v) end
  local D = recipes()
  for id, v in pairs((D and D.mats) or {}) do
    if v[3] == 1 and n0(v[2]) > 0 then try1(id, n0(v[2])) end
  end
  table.sort(out, function(a, b) if a.profit ~= b.profit then return a.profit > b.profit end return a.id < b.id end)
  for i = #out, (limit or 8) + 1, -1 do out[i] = nil end
  M.vflips = out
  return out
end

-- métiers du joueur : ce qu'il sait fabriquer (recettes connues, sinon celles de son rang) qui se vend avec bénéfice
function M.Professions()
  local P = T.modules and T.modules.Prof
  local profs = P and P.profs
  if (type(profs) ~= "table" or #profs == 0) and P and type(P.ScanProfessions) == "function" then profs = try(P.ScanProfessions) end
  return type(profs) == "table" and profs or {}
end
function M.CraftProfits(limit)
  local D, PL = recipes(), T.ProfLevel
  local out, profs = {}, M.Professions()
  if not (D and PL and type(PL.PriceOf) == "function") then return out, profs end
  local neutral, hrs, mp = M.IsNeutral(), hours(), minProfitC()
  for _, pr in ipairs(profs) do
    local line, rank = tonumber(pr.line), n0(pr.rank)
    local kind = line and D.lines[line] and D.lines[line][1]
    local rows = (kind == "c" or kind == "s") and D.r[line]
    local known = rows and type(PL.Known) == "function" and PL.Known(line)
    for _, rc in ipairs(rows or {}) do
      local spell, item, made, learn = rc[1], rc[2], rc[3], n0(rc[4])
      local can = (known and known[spell]) or (not known and learn <= rank)
      if can and item and tracked(item) then
        local cost, ok, mats = 0, true, rc[10] or {}
        for k = 1, #mats - 1, 2 do
          local price = PL.PriceOf(mats[k])
          if not price then ok = false break end
          cost = cost + price * n0(mats[k + 1])
        end
        local d = ok and F.craft(cost, made, M.Liq(item), vendorPrice(item), neutral, hrs, mp)
        if d then
          d.id, d.name, d.line, d.prof, d.mats = item, rc[9], line, pr.name or (D.lines[line] and D.lines[line][2]), mats
          out[#out + 1] = d
        end
      end
    end
  end
  table.sort(out, function(a, b) if a.profit ~= b.profit then return a.profit > b.profit end return a.id < b.id end)
  for i = #out, (limit or 8) + 1, -1 do out[i] = nil end
  M.crafts = out
  return out, profs
end

-- sacs : ce qui rapporte plus à l'HV (espérance après commission, dépôt, risque d'invendu) qu'au marchand
function M.BagAdvice()
  local list = M.SellAdvice()
  local ah, vendorN, gain = {}, 0, 0
  for _, s in ipairs(list) do
    if s.unit and s.better == "ah" then
      s.gain = n0(s.ev) - n0(s.vendor)
      gain = gain + max(0, s.gain)
      ah[#ah + 1] = s
    else vendorN = vendorN + 1 end
  end
  table.sort(ah, function(a, b) if a.gain ~= b.gain then return a.gain > b.gain end return n0(a.id) < n0(b.id) end)
  return ah, vendorN, gain
end

-- ventes en cours (gardées : l'HV fermé, on sait encore ce qui est en vente)
local function ownedSummary()
  local n, v = 0, 0
  for _, a in ipairs(M.owned or {}) do if a.state ~= "cancelled" then n, v = n + 1, v + n0(a.buy) end end
  if M.ahOpen and M.char then M.char.ownedSum = { n = n, v = v, t = now() } end
  local s = M.char and M.char.ownedSum
  if not M.ahOpen and s then return n0(s.n), n0(s.v), n0(s.t) end
  return n, v, now()
end
M.OwnedSummary = ownedSummary

local function soldShort(x)
  x = tonumber(x)
  if not x then return "?" end
  if x >= 1 then return tostring(floor(x + 0.5)) end
  return dec(x)
end
M.SoldShort = soldShort
local function daysShort(d)
  d = tonumber(d)
  if not d then return nil end
  if d < 1 then return "en moins d'1 j" end
  return ("en ~%d j"):format(floor(d + 0.5))
end

-- regroupe le plan d'achat par objet : « Étoffe de lin : 2 annonces (40) à ≤ 62 pc/u »
function M.BuyGroups()
  local g, order = {}, {}
  for _, d in ipairs(M.plan or {}) do
    if d.state ~= "bought" and d.state ~= "gone" then
      local e = g[d.id]
      if not e then e = { id = d.id, kind = d.kind, n = 0, qty = 0, buy = 0, profit = 0, maxUnit = 0, d = d } g[d.id] = e order[#order + 1] = e end
      e.n, e.qty, e.buy, e.profit = e.n + 1, e.qty + n0(d.count), e.buy + n0(d.buy), e.profit + n0(d.profit)
      e.maxUnit = max(e.maxUnit, floor(n0(d.buy) / max(1, n0(d.count))))
      e.sellUnit, e.sold, e.days, e.risk = d.sellUnit or floor(n0(d.sell) / max(1, n0(d.count))), d.sold, d.days, M.RiskText(d)
    end
  end
  table.sort(order, function(a, b) if a.profit ~= b.profit then return a.profit > b.profit end return a.id < b.id end)
  return order
end

function M.PageModel()
  local g = M.Gold()
  local r = M.Realized()
  local bags, vendorN, bagGain = M.BagAdvice()
  local groups = M.BuyGroups()
  local n, v, t = ownedSummary()
  local rd = M.RealmData()
  local last = (M.lastScan and M.lastScan.t) or (M.db and n0(M.db.lastScanT) > 0 and M.db.lastScanT) or nil
  -- (5.0 BAGS) ce qu'il te manque (stock conseillé) : la liste d'achats de l'HV
  local need = {}
  local B = brain()
  if B and type(B.Shopping) == "function" then
    local okS, sh = pcall(B.Shopping)
    if okS and type(sh) == "table" then need = sh end
  end
  return { gold = g, realized = r, bags = bags, vendorN = vendorN, bagGain = bagGain, groups = groups, need = need,
    planGain = n0(M.planGain), possible = n0(M.planGain) + bagGain, ownedN = n, ownedV = v, ownedT = t,
    tracked = rd and n0(rd.n) or 0, lastScan = last, scanned = last ~= nil or (rd and n0(rd.n) > 0) }
end

local function ago(t)
  local s = max(0, now() - n0(t))
  if s < 3600 then return ("il y a %d min"):format(floor(s / 60)) end
  if s < 172800 then return ("il y a %d h"):format(floor(s / 3600)) end
  return ("il y a %d j"):format(floor(s / 86400))
end
M.Ago = ago

-- page « HV » de la fenêtre TreeAI : 3 chiffres, 4 gros boutons, puis la liste choisie (état vide = quoi faire)
M.view = "flips"
local function scanHere(W)
  if not M.ahOpen then
    say("Ouvre l'HV et clique Scanner.")
    if W and W.Flash then pcall(W.Flash, "Ouvre l'hôtel des ventes (va voir un commissaire-priseur), puis clique Scanner.", 4) end
    return
  end
  if not M.ScanAll() and M.ahOpen and not M.job and not M.pendingAll and M.FullCooldown() > 0 then
    M.ScanTargets()
    say(("Scan complet dans %s (Blizzard : 1 fois / 15 min) : en attendant, les prix de tes objets."):format(mmss(M.FullCooldown())))
  end
end
M.ScanSmart = scanHere
function M.RenderPage(Ly, W)
  local m = M.PageModel()
  local week = n0(m.realized.week)
  -- (4.2 FIX42) deux gains JAMAIS additionnés : vendre ce que tu as déjà (sacs) ≠ acheter pour revendre (de l'or engagé)
  local nBags = #m.bags
  local view = M.view or "flips"
  -- (5.0 BAGS) une seule rangée de chiffres : les deux premiers SONT les listes (plus de tuile en double dessous)
  Ly.tiles({
    { "Vendre tes sacs à l'HV : +" .. money(m.bagGain), nBags > 0 and (nBags .. " objet" .. (nBags > 1 and "s" or "") .. " mieux payé" .. (nBags > 1 and "s" or "") .. " qu'au marchand")
        or "rien de mieux qu'au marchand", function() M.view = "bags" end,
      { "Ce que tu as déjà : vendu à l'HV plutôt qu'au marchand (net, risque d'invendu compris).",
        "Net : commission 5 % (15 % en HV neutre) et dépôt perdu si invendu déjà déduits.",
        "Jamais ce que tu dois garder (potions, munitions, quêtes, compos de tes métiers) : l'avis de la page « Sacs »." },
      view == "bags", "Clic : la liste" },
    { "Acheter-revendre : +" .. money(m.planGain), #m.groups > 0 and (#m.groups .. " achat" .. (#m.groups > 1 and "s" or "") .. " sûr" .. (#m.groups > 1 and "s" or ""))
        or "rien de sûr pour l'instant", function() M.view = "flips" end,
      { "De l'or engagé : acheté à l'HV, revendu plus cher (ventes mesurées entre tes scans).",
        "Net : commission et dépôt déjà déduits. Jamais additionné à la vente de tes sacs." },
      view == "flips", "Clic : la liste" },
    { ("%d vente%s · %s"):format(m.ownedN, m.ownedN > 1 and "s" or "", money(m.ownedV)), "en vente à l'HV", nil,
      { m.ahOpen and "Lu à l'instant" or ("Vu à l'HV " .. ago(m.ownedT)) } },
    { (week >= 0 and "+" or "") .. money(week), "gagné cette semaine", nil,
      { ("Aujourd'hui %s · total %s"):format(money(m.realized.today), money(m.realized.total)), "Lu dans le courrier de l'HV (ventes − achats)." } },
  }, 2)
  local function pick(v) return function() M.view = v end end
  local need = m.need or {}
  local needSub = "rien : ton stock est bon"
  if #need > 0 then
    local parts = {}
    for i, e in ipairs(need) do if i <= 2 then parts[#parts + 1] = ("%d %s"):format(n0(e.n), tostring(e.label)) end end
    needSub = table.concat(parts, ", ") .. (#need > 2 and "…" or "")
  end
  local scanSub = M.Scanning() and ("lecture " .. M.Scanning())
    or (not M.ahOpen and "ouvre l'HV d'abord")
    or (M.FullCooldown() > 0 and ("complet dans " .. mmss(M.FullCooldown()) .. " · ciblé maintenant"))
    or (m.lastScan and ("dernier : " .. ago(m.lastScan))) or "jamais fait"
  Ly.tiles({
    { "Scanner", scanSub, function() scanHere(W) end, { "Lit les prix de l'HV (scan complet 1 fois / 15 min, sinon tes objets).",
      "Plusieurs scans espacés sur 2-3 jours mesurent ce qui se vend vraiment." }, false, M.ahOpen and "Clic : scanner" or "Ouvre l'HV d'abord" },
    { "Ce qu'il te manque", needSub, pick("need"), { "Potions, nourriture, munitions : le stock conseillé pour ton niveau (page « Sacs »).",
      "Ici : leur prix à l'HV quand il est connu." }, view == "need", "Clic : la liste" },
    { "Mes métiers", "ce que tu fabriques et vends", pick("craft"), { "Coût des composants contre prix de vente mesuré à l'HV." },
      view == "craft", "Clic : la liste" },
  }, 3)
  if view == "bags" then M.RenderBags(Ly, W, m)
  elseif view == "craft" then M.RenderCraft(Ly, W, m)
  elseif view == "need" then M.RenderNeed(Ly, W, m)
  else M.RenderFlips(Ly, W, m) end
  -- toujours : mes ventes en cours (si on en a vu) et le bilan court
  local owned = {}
  for _, a in ipairs(M.owned or {}) do if a.state ~= "cancelled" then owned[#owned + 1] = a end end
  if #owned > 0 and Ly.header("Mes ventes en cours", "hv:owned", #owned) then
    for i, a in ipairs(owned) do
      if i > 8 then break end
      Ly.row({ text = ("%s ×%d à %s/u — %s"):format(itemName(a.id), a.count, money(a.unit), a.advice or "ok"),
        prio = a.worth and 2 or 1, tooltip = a.lowest and ("Plus bas des autres : " .. money(a.lowest) .. "/u") or nil })
    end
  end
end
local function emptyScan(Ly, m)
  if not m.scanned then
    Ly.empty("Ouvre l'HV et clique Scanner. Refais-le 2-3 fois sur 2 jours : TreeAI mesure ce qui se vend vraiment avant de te conseiller un achat.")
    return true
  end
  return false
end
function M.RenderFlips(Ly, W, m)
  if Ly.header("Acheter pour revendre", "hv:flips", #m.groups) then
    if #m.groups == 0 then
      if not emptyScan(Ly, m) then
        local why = (M.none and M.none.why) or { "Aucune annonce assez sous le prix du marché parmi les objets suivis." }
        Ly.text(F.NONE_TEXT .. ". " .. table.concat(why, " "), "S", "muted")
        Ly.text(("Règle : bénéfice net ≥ max(1 pa, 15 %%) après commission et dépôt, et la preuve que l'objet se vend (≥ %s vente/jour mesurée entre tes scans)."):format(dec(LIQ.minSold)), "S", "muted")
      end
    end
    for _, e in ipairs(m.groups) do
      local name = itemName(e.id)
      local txt
      if e.kind == "vendor" then
        txt = ("%s ×%d — achète ≤ %s/u, revends au marchand → +%s sûr"):format(name, e.qty, money(e.maxUnit), money(e.profit))
      else
        txt = ("%s ×%d — achète ≤ %s/u, revends ~%s/u → +%s net · risque %s · se vend ~%s/j%s"):format(name, e.qty, money(e.maxUnit),
          money(e.sellUnit), money(e.profit), e.risk, soldShort(e.sold), daysShort(e.days) and (", " .. daysShort(e.days)) or "")
      end
      Ly.row({ text = txt, prio = 2, tooltip = { ("%d annonce%s, %s en tout."):format(e.n, e.n > 1 and "s" or "", money(e.buy)),
        "À l'HV : bouton « Acheter » du panneau TreeAI (1 clic = 1 achat, prix revérifié avant)." } })
    end
  end
  local vf = M.VendorFlips(6)
  if #vf > 0 and Ly.header("Acheter au marchand, revendre à l'HV", "hv:vflips", #vf) then
    for _, d in ipairs(vf) do
      Ly.row({ text = ("%s ×%d — marchand %s/u%s, HV ~%s/u → +%s net · se vend ~%s/j"):format(itemName(d.id), d.count, money(d.buyUnit),
        d.lim and " (quantité limitée)" or "", money(d.sellUnit), money(d.profit), soldShort(d.sold)), prio = 1,
        tooltip = d.npc and ("Vu chez " .. d.npc) or "Vendu par les marchands de composants" })
    end
  end
end
function M.RenderBags(Ly, W, m)
  if Ly.header("Vendre à l'HV plutôt qu'au marchand", "hv:bags", #m.bags) then
    if #m.bags == 0 and not emptyScan(Ly, m) then
      Ly.text(m.vendorN > 0 and ("Rien dans tes sacs ne rapporte plus à l'HV : vends tout au marchand (%d objet%s)."):format(m.vendorN, m.vendorN > 1 and "s" or "")
        or "Rien à vendre dans tes sacs (ou pas encore de prix : scanne à l'HV).", "S", "muted")
    end
    for i, s in ipairs(m.bags) do
      if i > 12 then break end
      Ly.row({ text = ("%s ×%d — HV ≈ %s/u (vendu ~%s/j) · net ≈ %s · marchand %s → +%s à l'HV%s"):format(itemName(s.id), s.count, money(s.unit),
        soldShort(s.sold), money(s.ev), money(s.vendor), money(s.gain), s.keep and (" · garde " .. s.keep) or ""), prio = 2,
        tooltip = { s.keep and ("Garde %d : %s. Seul le surplus est compté."):format(s.keep, tostring(s.keepWhy or "tu en as besoin")) or nil,
          ("Dépôt %s (perdu si invendu), commission déduite."):format(money(s.deposit)),
          s.proof and "Ventes mesurées entre tes scans." or "Peu de ventes mesurées : prix d'après les annonces.",
          "À l'HV : bouton « Poster » du panneau TreeAI (1 clic = 1 mise en vente)." } })
    end
  end
end
--- (5.0 BAGS) « Ce qu'il te manque » : le stock conseillé par l'assistant d'inventaire, avec le prix d'HV s'il est connu
function M.RenderNeed(Ly, W, m)
  local need = m.need or {}
  if Ly.header("Ce qu'il te manque (à acheter)", "hv:need", #need) then
    if #need == 0 then
      Ly.text(brain() and "Rien : tes potions, ta nourriture et tes munitions suffisent pour ton niveau."
        or "L'assistant d'inventaire (« Sacs ») est coupé : allume-le pour voir ce qu'il te manque.", "S", "muted")
    end
    for _, e in ipairs(need) do
      local price = e.ah and ("HV ≈ %s/u (%s) → ~%s"):format(money(e.ah), tostring(e.ahSrc or "?"), money(n0(e.ah) * n0(e.n)))
        or (e.src == "marchand" and (e.npc and ("chez un marchand : " .. e.npc) or "chez un marchand") or "pas de prix à l'HV : scanne")
      Ly.row({ text = ("%s%s — %s"):format(e.name or tostring(e.label), " ×" .. n0(e.n), price), prio = 2,
        onClick = (e.id and M.ahOpen) and function() M.SearchItem(e.id) end or nil, actionText = "chercher à l'HV",
        tooltip = { ("Tu en as %d, stock conseillé : %d."):format(n0(e.have), n0(e.want)),
          e.src == "marchand" and "Un marchand en vend : souvent moins cher qu'à l'HV." or nil,
          "L'achat reste ton clic dans la fenêtre de l'HV." } })
    end
  end
end
function M.RenderCraft(Ly, W, m)
  local list, profs = M.CraftProfits(8)
  if Ly.header("Ce que tu fabriques et vends avec bénéfice", "hv:craft", #list) then
    if #profs == 0 then
      Ly.empty("Pas de métier de fabrication : apprends-en un chez un maître de métier, en ville.")
    elseif #list == 0 and not emptyScan(Ly, m) then
      Ly.text("Rien de rentable à fabriquer pour l'instant avec tes recettes (ou les objets fabriqués ne se vendent pas assez).", "S", "muted")
    end
    for _, d in ipairs(list) do
      local parts = {}
      for k = 1, #(d.mats or {}) - 1, 2 do parts[#parts + 1] = ("%s ×%d"):format(itemName(d.mats[k]), n0(d.mats[k + 1])) end
      Ly.row({ text = ("%s%s — composants %s, se vend ~%s → +%s net · se vend ~%s/j"):format(d.name or itemName(d.id),
        d.count > 1 and (" ×" .. d.count) or "", money(d.cost), money(d.sell), money(d.profit), soldShort(d.sold)), prio = 2,
        tooltip = { (d.prof and (d.prof .. " : ") or "") .. table.concat(parts, ", ") } })
    end
    if W and W.Select and #profs > 0 then
      Ly.buttons({ { "Mon plan de métier", function() W.Select("prof") end, "Clic : le plan de montée de ton métier" } }, 2)
    end
  end
end

-- ------------------------------------------------------------------ fenêtre (ancrée à l'HV, jamais parentée)
local ROWS = 9
M.tab, M.page = "deals", 1
-- (4.1 AHPROFIT) un seul panneau compact collé à l'HV : 3 files (Acheter · Poster · Annuler les dépassés), la touche
-- « action suivante », le bilan de la session, une ligne d'état (au lieu du chat) ; dessous, la liste de la file choisie.
local COLS = {
  deals = { { "Objet", 124 }, { "Prix", 58 }, { "Revente/u", 58 }, { "Net", 58 }, { "Ventes/j", 44 }, { "Risque", 46 } },
  sell = { { "Objet", 132 }, { "Qté", 28 }, { "Prix/u", 58 }, { "Net", 60 }, { "Marchand", 58 }, { "Vendu", 52 } },
  owned = { { "Objet", 132 }, { "Mon prix/u", 62 }, { "Plus bas", 58 }, { "État", 140 } },
}

local function rowsFor(tab)
  if tab == "deals" then
    if #M.deals > 0 then return M.deals end
    local out = { { text = F.NONE_TEXT .. "." } }
    for _, w in ipairs((M.none and M.none.why) or { "Pas encore de scan : clique « Scanner » (2-3 fois sur 2 jours pour mesurer les ventes)." }) do out[#out + 1] = { text = "  " .. w } end
    out[#out + 1] = { text = ("Règle : net ≥ max(1 pa, 15 %%) après commission et dépôt, et ≥ %s vente/jour mesurée entre tes scans."):format(dec(LIQ.minSold)) }
    return out
  elseif tab == "sell" then M.postRows = M.postRows or M.PostList() return M.postRows
  elseif tab == "owned" then return M.owned
  end
  return {}
end

local function cells(tab, e)
  if e.text then return { e.text } end
  if tab == "deals" then
    local name = itemName(e.id) .. (e.count > 1 and (" x" .. e.count) or "")
    if e.kind == "vendor" then return { name, money(e.buy), "marchand", "+" .. money(e.profit), "-", "aucun" } end
    return { name, money(e.buy), money(e.sellUnit or e.market), "+" .. money(e.profit), soldShort(e.sold), M.RiskText(e) }
  elseif tab == "sell" then
    return { itemName(e.id), tostring(e.count), money(e.unit), money(e.net), money(e.vendor), daysShort(e.days) or "?" }
  elseif tab == "owned" then
    local st = (e.state == "cancelled" and "annulée") or (e.state2 == "scan…" and "scan…") or e.advice or "ok"
    return { itemName(e.id) .. " x" .. e.count, money(e.unit), e.lowest and money(e.lowest) or "-", st }
  end
  return { e.text }
end

local function buttonLabel(tab, e)
  if e.text then return nil end
  if tab == "deals" then
    if e.state == "bought" then return "Acheté", false end
    if e.state == "gone" then return "Disparu", M.Flavor() == "modern" end
    if e.state == "search" or e.state == "starting" then return "…", false end
    if e.state == "confirm" then return "Confirmer", true end
    if M.Flavor() == "modern" and e.state ~= "ready" then return "Vérifier", M.ahOpen end
    return "Acheter", M.ahOpen
  elseif tab == "owned" and e.worth then
    if e.state == "cancelled" then return "Annulée", false end
    return "Annuler", M.ahOpen
  elseif tab == "sell" then
    if e.state == "posted" then return "Postée", false end
    if e.state == "skip" then return "Ignorée", false end
    if e.state == "check" then return "…", false end
    return "Poster", M.ahOpen
  end
  return nil
end

local function mkButton(parent, w, h, text)
  local b = _G.CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  b:SetSize(w, h)
  b:SetText(text)
  return b
end
local function smallText(parent, tmpl, w)
  local fs = parent:CreateFontString(nil, "OVERLAY", tmpl or "GameFontHighlightSmall")
  if fs.SetJustifyH then fs:SetJustifyH("LEFT") end
  if fs.SetWordWrap then fs:SetWordWrap(false) end
  if w and fs.SetWidth then fs:SetWidth(w) end
  return fs
end

local WIN_W, WIN_H = 500, 520
function M.CreateWindow()
  if M.win or not _G.CreateFrame then return M.win end
  local parent = _G.UIParent
  local f = _G.CreateFrame("Frame", nil, parent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  M.win = f
  f:SetSize(WIN_W, WIN_H)
  f:SetFrameStrata("HIGH")
  f:EnableMouse(true)
  f:SetMovable(true)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving)
  f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.SetBackdrop then
    f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 12, insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    if f.SetBackdropColor then f:SetBackdropColor(0, 0, 0, 0.88) end
  end
  local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", 10, -8)
  title:SetText("TreeAI — Hôtel des ventes")
  f.title = title
  if T.Theme then T.Theme:Skin(f, "window"); T.Theme:Font(title, "M", "gold") end  -- thème commun
  local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 0, 0)
  if T.Theme and T.Theme.Window then T.Theme:Window(f, { id = "auction", header = false }) end  -- Échap, fondu, verre
  local W2 = WIN_W - 20
  local function tipOn(b, tip)
    if b.SetScript and _G.GameTooltip then
      b:SetScript("OnEnter", function(self)
        local GT = _G.GameTooltip
        GT:SetOwner(self, "ANCHOR_TOP")
        GT:SetText(type(tip) == "function" and tip() or tip, 1, 1, 1, 1, true)
        GT:Show()
      end)
      b:SetScript("OnLeave", function() _G.GameTooltip:Hide() end)
    end
  end
  local function quick(w, text, fn, tip)
    local b = mkButton(f, w, 24, text)
    b:SetScript("OnClick", fn)
    tipOn(b, tip)
    return b
  end
  -- ligne d'état : or, budget, âge du scan
  f.status = smallText(f, "GameFontDisableSmall", W2)
  f.status:SetPoint("TOPLEFT", 10, -28)
  -- scanner (complet si permis, sinon les prix de tes objets) + scan ciblé explicite
  local scan = quick(150, "Scanner", function() M.ScanAll() M.Refresh() end,
    "Lit les prix : scan complet (Blizzard : 1 fois / 15 min pour tout le compte), sinon les prix de tes objets. Plusieurs scans espacés mesurent ce qui se vend.")
  scan:SetPoint("TOPLEFT", 10, -44)
  f.scanBtn = scan
  local mini = quick(150, "Prix de mes objets", function() M.ScanTargets() M.Refresh() end,
    "Scan ciblé : tes objets à vendre, tes ventes et le plan d'achat (au rythme permis par Blizzard).")
  mini:SetPoint("LEFT", scan, "RIGHT", 6, 0)
  f.miniBtn = mini
  -- les 3 files : un clic = UNE action protégée (prix revérifié juste avant)
  f.buyBtn = quick(170, "Acheter (0)", function() M.BuyBest() end,
    "Achète l'annonce suivante du plan, un clic à la fois, seulement si son prix est toujours ≤ au prix max (revérifié juste avant).")
  f.buyBtn:SetPoint("TOPLEFT", 10, -76)
  f.buySub = smallText(f, nil, W2 - 178)
  f.buySub:SetPoint("LEFT", f.buyBtn, "RIGHT", 8, 0)
  f.postBtn = quick(170, "Poster (0)", function() M.RepostNext() end, function()
    local pct = n0(cfg("undercutPct", 0))
    return ("Met en vente la pile suivante de tes sacs, %s sous le moins cher (jamais sous le plancher), en quantité vendable pendant la durée de la vente."):format(pct > 0 and (pct .. " %") or "1 pc")
  end)
  f.postBtn:SetPoint("TOPLEFT", 10, -104)
  f.postSub = smallText(f, nil, W2 - 178)
  f.postSub:SetPoint("LEFT", f.postBtn, "RIGHT", 8, 0)
  f.cancelBtn = quick(170, "Annuler les dépassés (0)", function() M.CancelNext() end,
    "Annule une de tes ventes qu'un concurrent propose moins cher, seulement si le dépôt perdu vaut le coup ; récupère-la au courrier puis « Poster ».")
  f.cancelBtn:SetPoint("TOPLEFT", 10, -132)
  f.cancelSub = smallText(f, nil, W2 - 178)
  f.cancelSub:SetPoint("LEFT", f.cancelBtn, "RIGHT", 8, 0)
  -- la touche : chaque appui = la prochaine action de la file (acheter → poster → annuler), une seule
  local raf = quick(W2 - 140, "Action suivante", function() M.RafaleNext() end,
    "Un appui = une action (acheter, poster, annuler) dans l'ordre. Lie-la à une touche et appuie autant de fois que tu veux : chaque appui est un clic à toi.")
  raf:SetHeight(30)
  raf:SetPoint("TOPLEFT", 10, -162)
  f.rafBtn = raf
  f.nextBtn = raf                                          -- (4.1) l'ancien « Suivant » = la même action
  local bind = quick(132, "Lier à une touche", function() M.BindRafale() end,
    "Lie « TreeAI : action HV suivante » à une touche libre (2 clics : proposer puis confirmer). Hors combat.")
  bind:SetPoint("TOPRIGHT", -10, -165)
  f.bindBtn = bind
  f.hint = smallText(f, nil, W2)
  f.hint:SetPoint("TOPLEFT", 10, -198)
  f.sess = smallText(f, "GameFontNormalSmall", W2)
  f.sess:SetPoint("TOPLEFT", 10, -214)
  f.msg = smallText(f, "GameFontHighlightSmall", W2)
  f.msg:SetPoint("TOPLEFT", 10, -230)
  if f.msg.SetTextColor then f.msg:SetTextColor(0.4, 0.8, 1) end
  -- la liste : 3 onglets (navigation seulement)
  local tabs = { { "deals", "À acheter" }, { "sell", "À poster" }, { "owned", "Mes ventes" } }
  f.tabBtns = {}
  for i, tb in ipairs(tabs) do
    local b = mkButton(f, 100, 20, tb[2])
    b:SetPoint("TOPLEFT", 10 + (i - 1) * 104, -250)
    b:SetScript("OnClick", function()
      M.tab, M.page = tb[1], 1
      if tb[1] == "sell" then M.SellAdvice() M.postRows = M.PostList() elseif tb[1] == "owned" then M.CheckOwned() end
      M.Refresh()
    end)
    f.tabBtns[i] = b
  end
  local prev = mkButton(f, 24, 20, "<")
  prev:SetPoint("TOPRIGHT", -38, -250)
  prev:SetScript("OnClick", function() M.page = max(1, M.page - 1) M.Refresh() end)
  local nxt = mkButton(f, 24, 20, ">")
  nxt:SetPoint("LEFT", prev, "RIGHT", 4, 0)
  nxt:SetScript("OnClick", function() M.page = M.page + 1 M.Refresh() end)
  f.head = {}
  for c = 1, 6 do f.head[c] = smallText(f, "GameFontNormalSmall") end
  f.rows = {}
  for i = 1, ROWS do
    local row = _G.CreateFrame("Frame", nil, f)
    row:SetSize(W2, 22)
    row:SetPoint("TOPLEFT", 10, -290 - (i - 1) * 24)
    row.cells = {}
    for c = 1, 6 do row.cells[c] = smallText(row) end
    row.text = row.cells[1]
    row.btn = mkButton(row, 66, 20, "")
    row.btn:SetPoint("RIGHT", 0, 0)
    row.btn:SetScript("OnClick", function()   -- clic du joueur = UNE action
      local e = row.entry
      if not e then return end
      if M.tab == "deals" then M.Buy(e) elseif M.tab == "owned" then M.Cancel(e) elseif M.tab == "sell" then M.Post(e) M.Refresh() end
    end)
    f.rows[i] = row
  end
  local acc = 0
  f:SetScript("OnUpdate", function(_, el)   -- affichage seulement (compte à rebours, progression) ; fenêtre ouverte = HV ouvert
    acc = acc + (el or 0)
    if acc < 1 then return end
    acc = 0
    M.RefreshStatus()
  end)
  f:Hide()
  return f
end

function M.Anchor()
  local f = M.win
  if not f then return end
  f:ClearAllPoints()
  local af = rawget(_G, "AuctionHouseFrame") or rawget(_G, "AuctionFrame")
  if af then f:SetPoint("TOPLEFT", af, "TOPRIGHT", 4, 0)   -- ancrage seulement (jamais SetParent)
  else f:SetPoint("CENTER", _G.UIParent, "CENTER", 0, 0) end
end

function M.RefreshStatus()
  local f = M.win
  if not (f and f.status) then return end
  local cd = M.FullCooldown()
  if M.job then f.scanBtn:SetText(("Lecture %d %%"):format(floor(100 * M.job.rows / max(1, M.job.total))))
  elseif cd > 0 then f.scanBtn:SetText("Scan dans " .. mmss(cd))
  else f.scanBtn:SetText("Scanner") end
  if f.miniBtn then f.miniBtn:SetText(M.mini and ("Prix… " .. M.mini.done .. "/" .. #M.mini.list) or "Prix de mes objets") end
  local g = M.Gold()
  local sc = M.Scanning()
  local last = M.lastScan and M.lastScan.t or (M.db and n0(M.db.lastScanT) > 0 and M.db.lastScanT)
  local age = sc and ("scan en cours (" .. sc .. ")") or (last and ("scan il y a " .. mmss(now() - last))) or "aucun scan"
  f.status:SetText(("Ton or %s · budget %s · %s%s"):format(money(g.total), money(g.budget), age,
    cd > 0 and not sc and (" · complet dans " .. mmss(cd)) or ""))
  local q = M.ActionLabels()
  local function set(b, text, on) if b then b:SetText(text) if b.SetEnabled then b:SetEnabled(on and true or false) end end end
  set(f.buyBtn, q.buy, q.buyOn)
  set(f.postBtn, q.post, q.postOn)
  set(f.cancelBtn, q.cancel, q.cancelOn)
  if f.buySub then f.buySub:SetText(q.buySub) f.postSub:SetText(q.postSub) f.cancelSub:SetText(q.cancelSub) end
  local rt, ron = M.RafaleText()
  if f.hint then f.hint:SetText(rt) end
  if f.sess then f.sess:SetText(M.SessionText()) end
  if f.msg and M.msg then f.msg:SetText(M.msg) end
  if f.rafBtn then
    local key = M.RafaleKey()
    f.rafBtn:SetText(("Action suivante%s"):format(key and (" [" .. key .. "]") or ""))
    if f.rafBtn.SetEnabled then f.rafBtn:SetEnabled(ron and true or false) end
  end
  if f.bindBtn then
    f.bindBtn:SetText(M.bindAsk and "Confirmer la touche" or (M.RafaleKey() and "Touche liée" or "Lier à une touche"))
    if f.bindBtn.SetEnabled then f.bindBtn:SetEnabled(not M.RafaleKey()) end
  end
end

local lastTab = 0
function M.TabLines()
  local g = M.Gold()
  local open = function() M.ShowWindow() end
  local tip = "Clic : panneau de l'HV (achats, mises en vente et annulations : un clic par action)."
  local lines = { { text = ("Budget HV %s · %s%s"):format(money(g.budget), M.DealsCount(#M.deals),
      (tonumber(M.planGain) or 0) > 0 and (" · plan +" .. money(M.planGain)) or ""),
    prio = 2, onClick = open, tooltip = tip } }
  for i = 1, min(5, #M.deals) do
    local d = M.deals[i]
    local lbl = itemName(d.id) .. (d.count > 1 and (" x" .. d.count) or "")
    local t = d.kind == "vendor" and ("%s : achat %s, revente au marchand %s, +%s sûr"):format(lbl, money(d.buy), money(d.sell), money(d.profit))
      or ("%s : achat %s, revente %s, +%s net · se vend ~%s/jour"):format(lbl, money(d.buy), money(d.sell), money(d.profit), dec(d.sold))
    lines[#lines + 1] = { text = t, prio = i == 1 and 2 or 1, onClick = open, tooltip = tip }
  end
  if #M.deals == 0 then
    lines[#lines + 1] = { text = F.NONE_TEXT .. ".", prio = 1, onClick = open, tooltip = tip }
    for _, w in ipairs((M.none and M.none.why) or { "Ouvre l'HV et clique Scanner (2-3 scans espacés sur 2 jours)." }) do
      lines[#lines + 1] = { text = w, prio = 1 }
    end
  end
  local nc = 0
  for _, a in ipairs(M.owned or {}) do if a.undercut and a.worth and a.state ~= "cancelled" then nc = nc + 1 end end
  if nc > 0 then
    lines[#lines + 1] = { text = ("%d vente(s) dépassée(s) à annuler (bouton « Annuler les dépassés » à l'HV)."):format(nc), prio = 1 }
  end
  return lines
end
function M.Refresh()
  if T.SetTabLines and (clock() - lastTab > 0.5 or M.tabDirty) then   -- onglet « HV » du panneau principal (≤ 2/s)
    lastTab, M.tabDirty = clock(), nil
    pcall(T.SetTabLines, T, "hv", M.TabLines(), "Auction")
  end
  local f = M.win
  if not (f and f.rows) then return end
  local cols = COLS[M.tab] or COLS.deals
  local x = 10
  for c = 1, 6 do
    local h, col = f.head[c], cols[c]
    if col then
      h:ClearAllPoints() h:SetPoint("TOPLEFT", f, "TOPLEFT", x, -274) h:SetWidth(col[2]) h:SetText(col[1]) h:Show()
      x = x + col[2] + 4
    else h:Hide() end
  end
  local list = rowsFor(M.tab)
  local pages = max(1, ceil(#list / ROWS))
  M.page, M.pages = min(M.page, pages), pages
  for i = 1, ROWS do
    local row, e = f.rows[i], list[(M.page - 1) * ROWS + i]
    row.entry = e
    if e then
      local cv = cells(M.tab, e)
      local cx = 0
      for c = 1, 6 do
        local fs, col = row.cells[c], cols[c]
        if e.text then col = c == 1 and { "", WIN_W - 24 } or nil end
        if col then
          fs:ClearAllPoints() fs:SetPoint("LEFT", row, "LEFT", cx, 0) fs:SetWidth(col[2]) fs:SetText(cv[c] or "") fs:Show()
          cx = cx + col[2] + 4
        else fs:Hide() end
      end
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
  M.RefreshStatus()
end

function M.ShowWindow()
  local f = M.CreateWindow()
  if f then M.Anchor() f:Show() M.Refresh() T:After(0, M.Anchor) end   -- l'UI Blizzard peut se charger après
end

-- ------------------------------------------------------------------ événements / cycle de vie
function M.AUCTION_HOUSE_SHOW()
  M.ahOpen = true
  M.HookAuctionator()
  local A = modernAH()
  if A and A.QueryOwnedAuctions then try(A.QueryOwnedAuctions, {}) end
  M.CheckOwned()
  M.postRows = nil
  M.Plan()
  M.BuildQueue()
  if cfg("window", true) then M.ShowWindow() end
end
function M.AUCTION_HOUSE_CLOSED()
  if M.raf.on then rafaleStop("HV fermé") end
  M.ahOpen, M.pendingAll, M.pendingSearch, M.mini, M.postRows = false, false, nil, nil, nil
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
      profit = d.profit, roi = d.roi and floor(d.roi * 100 + 0.5) / 100, conf = d.conf, days = d.days,
      deposit = d.deposit, relist = d.relist, risk = d.risk, sold = d.sold, maxQty = d.maxQty,
      stop = d.stop and d.stop.unit or nil, class = d.class }
  end
  local sell = {}
  for i, a in ipairs(M.sell or {}) do
    if i > 5 then break end
    sell[i] = { name = itemName(a.id), id = a.id, count = a.count, unit = a.unit, net = a.net, vendor = a.vendor,
      deposit = a.deposit, better = a.better, split = a.split, ev = a.ev, sold = a.sold }
  end
  local thin = {}
  for i, id in ipairs(M.thinList) do if i > 5 then break end thin[i] = itemName(id) end
  local owned = {}   -- mes ventes en cours (page HV de TreeAI Coach)
  for i, o in ipairs(M.owned or {}) do
    if i > 10 then break end
    owned[i] = { name = itemName(o.id), id = o.id, count = o.count, unit = o.unit, lowest = o.lowest,
      undercut = o.undercut or nil, advice = o.advice }
  end
  -- (4.1 AHPROFIT) métiers rentables et achats marchand → HV (mêmes règles que la page HV)
  local crafts, vflips = {}, {}
  local okc, cl = pcall(M.CraftProfits, 5)
  for i, d in ipairs(okc and cl or {}) do
    crafts[i] = { name = d.name or itemName(d.id), id = d.id, cost = d.cost, sell = d.sell, profit = d.profit, sold = d.sold, count = d.count }
  end
  local okv, vl = pcall(M.VendorFlips, 5)
  for i, d in ipairs(okv and vl or {}) do
    vflips[i] = { name = itemName(d.id), id = d.id, buyUnit = d.buyUnit, sellUnit = d.sellUnit, count = d.count, profit = d.profit, lim = d.lim }
  end
  local r = M.Realized()
  local top = {}
  for k, v in pairs(r.items) do top[#top + 1] = { item = k, profit = v.profit } end
  table.sort(top, function(a, b) return a.profit > b.profit end)
  for i = #top, 6, -1 do top[i] = nil end
  local g = M.Gold()
  local rd = M.RealmData()
  s.auction = { updated = now(), neutral = M.IsNeutral(), flavor = M.Flavor(),
    scanAge = M.lastFull and (now() - M.lastFull) or nil, tracked = rd and rd.n or 0,
    deals = deals, sell = sell, thin = thin, owned = owned, none = M.none and { why = M.none.why } or nil,
    crafts = crafts, vendorFlips = vflips,
    rules = { capGold = n0(cfg("capGold", 20)), qtyDays = n0(cfg("qtyDays", 3)), minDays = LIQ.minDays, minConf = LIQ.minConf }, budget = g.cap > 0 and g.cap or nil, budgetLeft = g.budget, spentToday = g.spent,
    gold = { total = g.total, reserve = g.reserve, invested = g.invested, plan = #(M.plan or {}), planGain = M.planGain },
    lastScan = M.lastScan and { src = M.lastScan.src, rows = M.lastScan.rows, speed = M.lastScan.speed } or nil,
    realized = { today = r.today, week = r.week, total = r.total, top = top } }
end

function M:OnInit()
  -- (4.1 AHPROFIT) anciens seuils (20 pa, −20 %) : rien ne passait sur un serveur calme → 1 pa / 15 %, une fois
  local db = M.db
  if db and not db.profitMig then
    if n0(db.minProfit) == 20 then db.minProfit = 1 end
    if n0(db.minMargin) == 20 then db.minMargin = 15 end
    db.profitMig = 1
  end
  if M.char then
    M.char.ledger = M.char.ledger or {}
    M.char.seen = M.char.seen or {}
    M.char.spent = M.char.spent or {}
    M.char.deposits = M.char.deposits or {}
    local old = dateKey(now() - 14 * 86400)
    for k in pairs(M.char.spent) do if k < old then M.char.spent[k] = nil end end
    for k in pairs(M.char.deposits) do if k < old then M.char.deposits[k] = nil end end
    M.char.flips = M.char.flips or {}
    for k, v in pairs(M.char.flips) do if type(v) ~= "table" or now() - n0(v.t) > 14 * 86400 then M.char.flips[k] = nil end end
  end
end

function M:OnEnable()
  for _, ev in ipairs({ "AUCTION_HOUSE_SHOW", "AUCTION_HOUSE_CLOSED", "AUCTION_ITEM_LIST_UPDATE", "REPLICATE_ITEM_LIST_UPDATE",
    "NEW_AUCTION_UPDATE", "MAIL_INBOX_UPDATE", "COMMODITY_PRICE_UPDATED", "AUCTION_HOUSE_THROTTLED_SYSTEM_READY", "MERCHANT_SHOW" }) do
    self:RegisterEvent(ev)
  end
  for _, ev in ipairs({ "ITEM_SEARCH_RESULTS_UPDATED", "COMMODITY_SEARCH_RESULTS_UPDATED" }) do
    self:RegisterEvent(ev, M.OnSearchResults)
  end
  for _, ev in ipairs({ "AUCTION_HOUSE_BROWSE_RESULTS_UPDATED", "AUCTION_HOUSE_BROWSE_RESULTS_ADDED" }) do
    self:RegisterEvent(ev, function() M.OnBrowse() end)
  end
  self:RegisterEvent("AUCTION_OWNED_LIST_UPDATE", function() M.CheckOwned() M.Refresh() end)
  self:RegisterEvent("OWNED_AUCTIONS_UPDATED", function() M.CheckOwned() M.Refresh() end)
  self:RegisterEvent("PLAYER_LOGIN", function() M.HookAuctionator() end)
  M.HookAuctionator()
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  if T.RegisterTab then pcall(T.RegisterTab, T, "hv", "HV", 40) end
end

local function slash(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if rest == "neutre" or rest == "faction" or rest == "auto" then
    if M.db then M.db.house = (rest == "neutre" and "neutral") or rest end
    out("HV : " .. (M.IsNeutral() and "neutre (commission 15 %)" or "de faction (commission 5 %)"))
    return
  end
  if rest == "debug" then
    out("--- /tai hv debug (copie-colle ces lignes) ---")
    for _, l in ipairs(M.DebugLines()) do out(l) end
    return
  end
  local api = rest:match("^api%s+(%S+)")
  if api then
    if M.db then M.db.api = (api:find("^mod") and "modern") or (api:find("^cla") and "classic") or "auto" end
    out("API HV : " .. tostring(M.db and M.db.api) .. " → " .. tostring(M.Flavor()))
    return
  end
  local k, v = rest:match("^(%a+)%s+(%d+)$")
  if k == "reserve" or k == "garder" then if M.db then M.db.keepGold = tonumber(v) end out("Réserve : garder " .. v .. " po.") M.Plan() return end
  if k == "monture" then if M.db then M.db.mountGold = tonumber(v) end out("Réserve monture : " .. v .. " po.") M.Plan() return end
  if k == "budget" then if M.db then M.db.budget = tonumber(v) end out("Plafond d'achat / jour : " .. v .. " po (0 = illimité).") M.Plan() return end
  if rest == "fenetre" or rest == "fenêtre" or rest == "window" then M.ShowWindow() return end
  if rest == "cible" or rest == "ciblé" then M.ScanTargets() return end
  if rest == "aide" or rest == "help" then
    out("/tai hv [vendre | bilan | journal | fenetre | cible | debug | neutre | faction | auto | reserve N | monture N | budget N | api auto|moderne|classique]")
    out("Achats / ventes / annulations : panneau TreeAI de l'HV, ou la touche « action suivante » (1 appui = 1 action).")
    return
  end
  M.Report(rest)
  if M.ahOpen then M.ShowWindow() end
end
T:RegisterSlash("hv", slash, "hôtel des ventes : affaires, vendre, bilan, debug")
T:RegisterSlash("ah", slash)
