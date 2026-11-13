local tonumber = function(v) return _G.tonumber(v) end   -- tonumber(f()) : un 2e retour de f n'est jamais une base
-- TreeAI — Hôtel des ventes, partie « Calculs » (5.1 AH). Fait partie du bureau d'achat-revente (AHDesk) : pas un
-- module de plus, aucune API du jeu ici, que des entiers (cuivre) → les mêmes nombres en Lua et en Python.
-- Idées et formules expliquées dans research/ah5.md. Règle d'or : un chiffre est exact ou absent.
--   commission  = floor(prix total × 5 % (15 % HV neutre))            — retenue par le jeu à la vente
--   dépôt       = valeur du jeu (CalculateCommodityDeposit / CalculateItemDeposit) quand elle est lisible, sinon un
--                 PLAFOND : floor(prix marchand × quantité × 5 % (25 %) × heures / 2) ; perdu si l'enchère expire
--   dépôts perdus attendus = ceil(dépôt × (1000 − p) / p), p = chance de vendre pendant UNE mise en vente (‰)
--   net d'un achat-revente = prix de revente × n − commission − dépôts perdus attendus − prix d'achat
--   net d'une revente au marchand = prix marchand × n − prix d'achat   (aucun frais : c'est le gain « sûr »)
--   plancher de mise en vente = plus petit prix unitaire qui laisse (coût + frais + marge voulue)
-- Trois classes : « sur » (prouvable : revente au marchand), « probable » (preuves de ventes sur plusieurs jours),
-- « speculatif » (preuves minces : jamais mis dans une file).
local T = _G.TreeAI
if not T then return end

local X = {}
T.AHMath = X
X.V = 1

local floor, ceil, max, min = math.floor, math.ceil, math.max, math.min
local function n0(v) return tonumber(v) or 0 end
local function int(v) return floor(n0(v)) end

X.CUT_PCT = { [false] = 5, [true] = 15 }
X.DEP_PCT = { [false] = 5, [true] = 25 }
-- seuils par défaut (réglables par le joueur dans AHDesk) ; « preuves » = ce qu'il faut pour dire « probable »
X.RULES = {
  minNet = 100,        -- gain net minimal par clic (cuivre) : 1 pa
  marginPct = 15,      -- et au moins 15 % de la mise pour un achat-revente à l'HV (pas pour le marchand : il est sûr)
  sharePct = 50,       -- part de ton or qu'on peut immobiliser (le reste est ta réserve)
  itemPct = 20,        -- part de ton or dans UN même objet
  maxDays = 3,         -- revendu en 3 jours au plus (selon les ventes mesurées)
  minDays = 3, minScans = 4, minHours = 12, minSold = 1, minSellers = 3, maxVolPct = 30,   -- preuves
  maxAgeH = 12,        -- prix vus il y a plus de 12 h : pas d'achat-revente proposé (le marchand reste vrai)
  rows = 12,           -- annonces lues par objet (les moins chères)
}

function X.cut(total, neutral) return floor(max(0, int(total)) * X.CUT_PCT[neutral and true or false] / 100) end
function X.net(total, neutral) return max(0, int(total)) - X.cut(total, neutral) end

--- dépôt : `pm` = part (‰ du prix marchand × quantité) apprise du jeu pour cette durée ; sinon le plafond
function X.deposit(vendorUnit, qty, hours, neutral, pm)
  local base = max(0, int(vendorUnit)) * max(1, int(qty))
  if pm and n0(pm) >= 0 then return floor(base * int(pm) / 1000) end
  return floor(base * X.DEP_PCT[neutral and true or false] * max(0, int(hours or 24)) / 200)
end

--- chance (‰, 50..950) de vendre `qty` pendant une mise en vente de `hours` h ; nil = ventes non mesurées
function X.sellPm(soldPerDay, ahead, qty, hours)
  local v = tonumber(soldPerDay)
  if not v then return nil end
  if v <= 0 then return 50 end
  local need = max(0, n0(ahead)) + max(1, n0(qty))
  local p = floor(v * max(1, n0(hours or 24)) / 24 / need * 1000)
  return max(50, min(950, p))
end
--- jours avant d'avoir tout vendu (2 décimales) ; nil = inconnu
function X.days(soldPerDay, ahead, qty)
  local v = tonumber(soldPerDay)
  if not v or v <= 0 then return nil end
  return floor((max(0, n0(ahead)) + max(1, n0(qty))) / v * 100 + 0.5) / 100
end
--- dépôts perdus avant la vente (espérance, arrondie vers le haut : un frais n'est jamais sous-estimé)
function X.depLoss(dep, pm)
  dep, pm = max(0, int(dep)), max(1, min(1000, int(pm or 1000)))
  return ceil(dep * (1000 - pm) / pm)
end

function X.vendorNet(buyTotal, vendorUnit, qty) return max(0, int(vendorUnit)) * max(1, int(qty)) - max(0, int(buyTotal)) end
--- net d'un achat-revente : renvoie net, brut, commission, dépôts perdus attendus
function X.flipNet(buyTotal, sellUnit, qty, dep, pm, neutral)
  local gross = max(0, int(sellUnit)) * max(1, int(qty))
  local cut = X.cut(gross, neutral)
  local loss = X.depLoss(dep, pm)
  return gross - cut - loss - max(0, int(buyTotal)), gross, cut, loss
end
--- gain minimal voulu pour une mise de `buy` : max(minimum par clic, marge % de la mise arrondie vers le haut)
function X.need(buy, minNet, marginPct)
  return max(int(minNet or X.RULES.minNet), ceil(max(0, int(buy)) * n0(marginPct or X.RULES.marginPct) / 100))
end
--- plancher : plus petit prix unitaire dont le net (après commission et dépôts perdus) est ≥ `need` au-dessus du coût
function X.floorUnit(costTotal, qty, dep, pm, neutral, need)
  qty = max(1, int(qty))
  local want = max(0, int(costTotal)) + X.depLoss(dep, pm) + max(0, int(need))
  local u = max(1, ceil(want * 100 / (100 - X.CUT_PCT[neutral and true or false]) / qty))
  while u > 1 and X.flipNet(costTotal, u - 1, qty, dep, pm, neutral) >= int(need) do u = u - 1 end
  while X.flipNet(costTotal, u, qty, dep, pm, neutral) < int(need) do u = u + 1 end
  return u
end
--- sous-coter : 1 pc dessous (pct = 0) ou pct % dessous ; jamais sous 1 pc
function X.undercut(low, pct)
  low = max(1, int(low))
  if n0(pct) <= 0 then return max(1, low - 1) end
  return max(1, min(low - 1, floor(low * (100 - n0(pct)) / 100)))
end

--- prix de mise en vente. o = { floor, low (moins cher des AUTRES, nil = personne), mine (mon moins cher déjà en vente),
---   normal (prix habituel prouvé, nil = inconnu), undercutPct } → unité, "undercut"|"match"|"normal"|nil, pourquoi
--- Jamais sous le plancher : si le concurrent est dessous, on ne poste pas (« wait »).
function X.postUnit(o)
  local fl = max(1, int(o.floor))
  local low, mine, normal = tonumber(o.low), tonumber(o.mine), tonumber(o.normal)
  if low and low > 0 then
    local u
    if mine and mine > 0 and mine <= low then u = int(mine) else u = X.undercut(low, o.undercutPct) end
    if normal and normal > 0 and u > floor(normal * 3 / 2) then u = floor(normal * 3 / 2) end   -- concurrent hors de prix : pas plus de 1,5 × l'habituel
    if u < fl then return nil, "wait", "le moins cher est sous ton plancher" end
    return u, (mine and mine > 0 and mine <= low) and "match" or "undercut"
  end
  if normal and normal > 0 then
    if normal < fl then return nil, "wait", "le prix habituel est sous ton plancher" end
    return int(normal), "normal"
  end
  return nil, "unknown", "aucun prix connu : relis les prix"
end

--- preuves → classe. ev = { ok (modèle de liquidité), days, scans, hours, sold, sellers, volPct, ageH }
function X.evidence(ev, R)
  R = R or X.RULES
  if type(ev) ~= "table" then return "speculatif", "jamais vu en scan" end
  if n0(ev.days) < R.minDays then return "speculatif", ("historique trop court (%d j sur %d)"):format(n0(ev.days), R.minDays) end
  if n0(ev.scans) < R.minScans then return "speculatif", ("trop peu de scans (%d sur %d)"):format(n0(ev.scans), R.minScans) end
  if n0(ev.hours) < R.minHours then return "speculatif", "ventes pas encore mesurées" end
  if ev.sold == nil or n0(ev.sold) < R.minSold then return "speculatif", "se vend trop peu" end
  if n0(ev.sellers) > 0 and n0(ev.sellers) < R.minSellers then return "speculatif", "trop peu de vendeurs" end
  if n0(ev.volPct) > R.maxVolPct then return "speculatif", "prix instable" end
  if n0(ev.ageH) > R.maxAgeH then return "speculatif", "prix trop vieux" end
  if ev.ok == false then return "speculatif", ev.why or "preuves insuffisantes" end
  return "probable"
end

--- Revente au marchand (classe « sur »). rows = annonces triées (prix unitaire croissant) { unit, count, buy, auctionID }.
--- commodity : UN lot (toutes les unités sous le prix marchand) ; sinon une affaire par annonce.
--- → liste d'affaires (peut être vide). Rien si le prix marchand n'est pas certain (vendorUnit lu dans le jeu).
function X.vendorDeals(id, rows, vendorUnit, commodity, R)
  R = R or X.RULES
  local out = {}
  vendorUnit = int(vendorUnit)
  if vendorUnit <= 0 then return out end
  local function mk(qty, buy, row, maxUnit)
    local net = X.vendorNet(buy, vendorUnit, qty)
    if net < R.minNet then return end
    out[#out + 1] = { id = id, kind = "vendor", class = "sur", qty = qty, buy = buy, buyUnit = ceil(buy / qty), maxUnit = maxUnit,
      sellUnit = vendorUnit, gross = vendorUnit * qty, cut = 0, dep = 0, depLoss = 0, fees = 0, net = net, capital = buy,
      pm = 1000, days = 0, commodity = commodity or nil, auctionID = row and row.auctionID or nil, count = row and row.count or nil }
  end
  if commodity then
    local qty, buy, top = 0, 0, 0
    for _, r in ipairs(rows) do
      if r.unit < vendorUnit then qty, buy, top = qty + r.count, buy + r.unit * r.count, r.unit else break end
    end
    if qty > 0 then mk(qty, buy, nil, top) end
  else
    for _, r in ipairs(rows) do
      if r.buy < vendorUnit * r.count then mk(r.count, r.buy, r, floor(r.buy / r.count)) else break end
    end
  end
  return out
end

--- Achat-revente à l'HV (classe « probable » si les preuves tiennent). o = { id, rows, market (prix habituel prudent),
---   sold, ahead (mes unités déjà en vente / en sacs), maxQty, vendorUnit, hours, neutral, depPm, commodity, ev }
--- On achète les k annonces les moins chères ; on revend 1 pc sous la suivante (le « mur »), jamais au-dessus du prix
--- habituel. k = celui qui rapporte le plus, chaque annonce achetée devant être rentable seule.
function X.flipDeals(o, R)
  R = R or X.RULES
  local out = {}
  local class, why = X.evidence(o.ev, R)
  if class ~= "probable" then return out, why end
  local rows, market = o.rows, int(o.market)
  if market <= 0 or #rows == 0 then return out, "prix habituel inconnu" end
  local maxQty = int(o.maxQty)
  if maxQty <= 0 then return out, "tu en as déjà autant qu'il s'en vend" end
  local best, bestNet
  local nrows = min(#rows, R.rows)
  for k = 1, nrows do
    local wall = rows[k + 1]
    local sell = market
    if wall and wall.unit - 1 < sell then sell = wall.unit - 1 end
    local set, qty, buy, total, okAll = {}, 0, 0, 0, sell > 0
    for i = 1, k do
      if not okAll then break end
      local r = rows[i]
      local take = r.count
      if qty + take > maxQty then
        if o.commodity then take = maxQty - qty else take = 0 end
      end
      if take <= 0 then break end
      local cost = o.commodity and r.unit * take or r.buy
      local pm = X.sellPm(o.sold, n0(o.ahead) + qty, take, o.hours)
      local dep = X.deposit(o.vendorUnit, take, o.hours, o.neutral, o.depPm)
      local net, gross, cut, loss = X.flipNet(cost, sell, take, dep, pm, o.neutral)
      if net < X.need(cost, R.minNet, R.marginPct) then okAll = false break end
      local days = X.days(o.sold, n0(o.ahead) + qty, take)
      if not days or days > R.maxDays then okAll = false break end
      qty, buy, total = qty + take, buy + cost, total + net
      set[#set + 1] = { id = o.id, kind = "flip", class = "probable", qty = take, buy = cost, buyUnit = ceil(cost / take),
        maxUnit = r.unit, sellUnit = sell, gross = gross, cut = cut, dep = dep, depLoss = loss, fees = cut + loss, net = net,
        capital = cost, pm = pm, days = days, auctionID = r.auctionID, count = r.count }
    end
    if okAll and #set == k and (not bestNet or total > bestNet) then best, bestNet = { set = set, qty = qty, buy = buy, sell = sell }, total end
  end
  if not best then return out, "pas assez sous le prix habituel" end
  if o.commodity then
    -- un seul achat (le jeu vend les unités les moins chères d'abord) : frais recalculés sur le lot entier
    local qty, buy, sell = best.qty, best.buy, best.sell
    local pm = X.sellPm(o.sold, n0(o.ahead), qty, o.hours)
    local dep = X.deposit(o.vendorUnit, qty, o.hours, o.neutral, o.depPm)
    local net, gross, cut, loss = X.flipNet(buy, sell, qty, dep, pm, o.neutral)
    local days = X.days(o.sold, n0(o.ahead), qty)
    if net < X.need(buy, R.minNet, R.marginPct) or not days or days > R.maxDays then return out, "marge trop faible sur le lot" end
    out[1] = { id = o.id, kind = "flip", class = "probable", qty = qty, buy = buy, buyUnit = ceil(buy / qty),
      maxUnit = best.set[#best.set].maxUnit, sellUnit = sell, gross = gross, cut = cut, dep = dep, depLoss = loss,
      fees = cut + loss, net = net, capital = buy, pm = pm, days = days, commodity = true }
  else
    for i, d in ipairs(best.set) do out[i] = d end
  end
  return out
end

--- File d'achat : meilleur gain net d'abord (à gain égal : le sûr, puis la plus petite mise) ; jamais plus que
--- `budget` au total, ni plus que `itemCap` dans un même objet (les reventes au marchand ne comptent pas dans itemCap).
function X.allocate(deals, budget, itemCap)
  local list = {}
  for i, d in ipairs(deals) do list[i] = d end
  table.sort(list, function(a, b)
    if a.net ~= b.net then return a.net > b.net end
    if a.class ~= b.class then return a.class == "sur" end
    if a.buy ~= b.buy then return a.buy < b.buy end
    if a.id ~= b.id then return a.id < b.id end
    return n0(a.auctionID) < n0(b.auctionID)
  end)
  local out, left, per, spent, gain = {}, max(0, int(budget)), {}, 0, 0
  for _, d in ipairs(list) do
    local capOk = d.class == "sur" or not itemCap or n0(per[d.id]) + d.buy <= itemCap
    if d.net > 0 and d.buy <= left and capOk then
      out[#out + 1] = d
      left, spent, gain = left - d.buy, spent + d.buy, gain + d.net
      per[d.id] = n0(per[d.id]) + d.buy
    end
  end
  return out, spent, gain
end

--- capital : `gold` = or en poche, `tied` = coût des lots achetés pas encore revendus. On ne dépasse jamais
--- sharePct % de (or + immobilisé), et on ne touche pas à `reserve` (or à garder quoi qu'il arrive).
function X.budget(gold, tied, sharePct, reserve)
  gold, tied = max(0, int(gold)), max(0, int(tied))
  local cap = floor((gold + tied) * max(0, min(100, n0(sharePct))) / 100)
  return max(0, min(cap - tied, gold - max(0, int(reserve))))
end

--- Journal → bilan. ev = { { k = "buy"|"sale"|"vend"|"exp"|"cancel", day, id, n, c, cut, dep } … } dans l'ordre du temps.
--- Coût d'achat : premier entré, premier sorti, par objet. → { days = {[jour] = net}, items = {[id] = {…}}, tied, total }
function X.realized(ev)
  local lots, days, items, total = {}, {}, {}, 0
  local function item(e)
    local key = e.id or e.name or "?"
    local it = items[key]
    if not it then it = { id = e.id, name = e.name, bought = 0, sold = 0, spent = 0, income = 0, fees = 0, profit = 0 } items[key] = it end
    return it, key
  end
  local function take(key, n)
    local q, cost = lots[key], 0
    while q and n > 0 and q[1] do
      local l = q[1]
      local k = min(n, l.n)
      local part = (k == l.n) and l.c or floor(l.c * k / l.n)
      cost, n = cost + part, n - k
      l.n, l.c = l.n - k, l.c - part
      if l.n <= 0 then table.remove(q, 1) end
    end
    return cost
  end
  for _, e in ipairs(ev) do
    local it, key = item(e)
    local n = max(1, int(e.n))
    local gain
    if e.k == "buy" then
      lots[key] = lots[key] or {}
      lots[key][#lots[key] + 1] = { n = n, c = int(e.c) }
      it.bought, it.spent = it.bought + n, it.spent + int(e.c)
    elseif e.k == "sale" or e.k == "vend" then
      local cost = take(key, n)
      gain = int(e.c) - int(e.cut) - cost
      it.sold, it.income, it.fees = it.sold + n, it.income + int(e.c), it.fees + int(e.cut)
    elseif e.k == "exp" or e.k == "cancel" then
      gain = -int(e.dep)
      it.fees = it.fees + int(e.dep)
    end
    if gain then
      it.profit, total = it.profit + gain, total + gain
      if e.day then days[e.day] = n0(days[e.day]) + gain end
    end
  end
  local tied = 0
  for key, q in pairs(lots) do
    local c, n = 0, 0
    for _, l in ipairs(q) do c, n = c + l.c, n + l.n end
    tied = tied + c
    if items[key] then items[key].held, items[key].heldCost = n, c end
  end
  return { days = days, items = items, tied = tied, total = total }
end

--- objectif du jour : honnête. → { pct, left, reachable (bool ou nil = inconnu), text }
function X.goal(today, goal, visibleNet)
  today, goal = int(today), max(0, int(goal))
  local left = max(0, goal - today)
  local r = { pct = goal > 0 and min(100, max(0, floor(today * 100 / goal))) or 100, left = left }
  if left == 0 then r.reachable = true
  elseif visibleNet ~= nil then r.reachable = int(visibleNet) >= left end
  return r
end
