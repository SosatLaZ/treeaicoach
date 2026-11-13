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
  -- (tranche 2) « réguler » un petit marché : racheter TOUT le mur bas et reposter au prix habituel, seulement si…
  resetMaxSellers = 3, -- … le mur bas appartient à 3 vendeurs au plus (noms lus dans les annonces)
  resetMaxUnit = 10000,-- … l'objet est bon marché (prix habituel ≤ 1 po l'unité)
  resetWallPct = 75,   -- … le « mur bas » = toutes les annonces sous 75 % du prix habituel
  resetCap = 30000,    -- … le mur coûte au plus 3 po (réglable)
  resetMinSold = 2,    -- … il se vend ≥ 2 unités / jour (mesuré entre tes scans)
  resetMaxDays = 4,    -- … le mur entier se revend en ≤ 4 j à ce rythme
  resetMarginPct = 30, -- … et rapporte ≥ 30 % de la mise (le risque se paie)
  -- annuler / reposter une vente dépassée : seulement si le gain espéré ≥ max(gain minimal, 5 % du net reposté)
  cancelMinPct = 5,
  -- moments de vente : un créneau (jour × 4 h) compte s'il a ≥ 6 h mesurées ; il en faut ≥ 8, sur ≥ 4 jours différents
  patMinHours = 600, patMinBuckets = 8, patMinDays = 4, patMinPm = 1200,
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
--- mode "sellers" : le nombre de vendeurs n'est pas un critère (annuler une de MES ventes) ; mode "reset" : non plus,
--- et un prix du jour tiré vers le bas par le mur lui-même (« prix en chute ») est accepté — la régulation veut
--- justement un mur bas tenu par PEU de vendeurs ; ses propres seuils (resetDeals) prennent le relais.
function X.evidence(ev, R, mode)
  local noSellers = mode == "sellers" or mode == "reset"
  R = R or X.RULES
  if type(ev) ~= "table" then return "speculatif", "jamais vu en scan" end
  if n0(ev.days) < R.minDays then return "speculatif", ("historique trop court (%d j sur %d)"):format(n0(ev.days), R.minDays) end
  if n0(ev.scans) < R.minScans then return "speculatif", ("trop peu de scans (%d sur %d)"):format(n0(ev.scans), R.minScans) end
  if n0(ev.hours) < R.minHours then return "speculatif", "ventes pas encore mesurées" end
  if ev.sold == nil or n0(ev.sold) < R.minSold then return "speculatif", "se vend trop peu" end
  if not noSellers and n0(ev.sellers) > 0 and n0(ev.sellers) < R.minSellers then return "speculatif", "trop peu de vendeurs" end
  if n0(ev.volPct) > R.maxVolPct then return "speculatif", "prix instable" end
  if n0(ev.ageH) > R.maxAgeH then return "speculatif", "prix trop vieux" end
  if ev.ok == false and not ((mode == "sellers" and ev.onlySellers) or (mode == "reset" and (ev.onlySellers or ev.resetOnly))) then
    return "speculatif", ev.why or "preuves insuffisantes"
  end
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
--- Une régulation (d.group) se prend en entier ou pas du tout : ses annonces se suivent dans la file.
function X.allocate(deals, budget, itemCap)
  local units, byGroup = {}, {}
  for _, d in ipairs(deals) do
    if d.group then
      local u = byGroup[d.group]
      if not u then
        u = { group = d.group, members = {}, net = 0, buy = 0, class = d.class, id = d.id, aid = n0(d.auctionID) }
        byGroup[d.group] = u
        units[#units + 1] = u
      end
      u.members[#u.members + 1] = d
      u.net, u.buy = u.net + d.net, u.buy + d.buy
      if n0(d.auctionID) < u.aid then u.aid = n0(d.auctionID) end
    else
      units[#units + 1] = { members = { d }, net = d.net, buy = d.buy, class = d.class, id = d.id, aid = n0(d.auctionID) }
    end
  end
  -- ordre total (sinon table.sort peut échouer) : gain, le « sûr » d'abord, mise, objet, annonce
  local function rank(u) return u.class == "sur" and 0 or 1 end
  table.sort(units, function(a, b)
    if a.net ~= b.net then return a.net > b.net end
    if rank(a) ~= rank(b) then return rank(a) < rank(b) end
    if a.buy ~= b.buy then return a.buy < b.buy end
    if a.id ~= b.id then return a.id < b.id end
    return a.aid < b.aid
  end)
  local out, left, per, spent, gain = {}, max(0, int(budget)), {}, 0, 0
  for _, u in ipairs(units) do
    local capOk = u.class == "sur" or not itemCap or n0(per[u.id]) + u.buy <= itemCap
    if u.net > 0 and u.buy <= left and capOk then
      table.sort(u.members, function(a, b) return n0(a.auctionID) < n0(b.auctionID) end)
      for _, d in ipairs(u.members) do out[#out + 1] = d end
      left, spent, gain = left - u.buy, spent + u.buy, gain + u.net
      per[u.id] = n0(per[u.id]) + u.buy
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

--- coût d'achat de `qty` unités, les moins chères d'abord (marchandise) ; nil s'il n'y en a pas assez
function X.buyCost(rows, qty)
  local left, cost = max(0, int(qty)), 0
  for _, r in ipairs(rows or {}) do
    if left <= 0 then break end
    local k = min(left, int(r.count))
    cost, left = cost + int(r.unit) * k, left - k
  end
  if left > 0 then return nil end
  return cost
end

--- « Réguler » un petit marché bon marché (classe « regul ») : racheter TOUT le mur bas (annonces sous resetWallPct %
--- du prix habituel) et le reposter au prix habituel (1 pc sous l'annonce suivante, jamais au-dessus de l'habituel).
--- o = { id, rows (annonces des AUTRES, triées, avec owner), market, sold, ahead, vendorUnit, hours, neutral, depPm,
---   commodity, ev, cap (plafond du mur, cuivre), mineLow (ma propre vente la moins chère) }
--- Seuils explicites (R.reset*) ; tout le mur ou rien. → liste d'affaires (une par annonce pour un objet, un lot pour une
--- marchandise ; toutes du même groupe), pourquoi
function X.resetDeals(o, R)
  R = R or X.RULES
  local out = {}
  local class, why = X.evidence(o.ev, R, "reset")
  if class ~= "probable" then return out, why end
  local rows, market = o.rows or {}, int(o.market)
  if market <= 0 then return out, "prix habituel inconnu" end
  if market > R.resetMaxUnit then return out, "pas un petit objet (prix habituel > plafond)" end
  local sold = tonumber(o.sold)
  if not sold or sold < R.resetMinSold then return out, "se vend trop peu pour réguler" end
  if n0((o.ev or {}).sellers) > R.resetMaxSellers then return out, "trop de vendeurs sur ce marché" end
  local lim = floor(market * R.resetWallPct / 100)
  local wall, owners, nOwn = {}, {}, 0
  for _, r in ipairs(rows) do
    if int(r.unit) >= lim then break end
    local who = r.seller or r.owner          -- « Nom » ou « Nom1,Nom2 » (une ligne de marchandise peut avoir plusieurs vendeurs)
    if type(who) ~= "string" or who == "" then return out, "vendeurs du mur inconnus" end
    for nm in who:gmatch("[^,]+") do if not owners[nm] then owners[nm], nOwn = true, nOwn + 1 end end
    wall[#wall + 1] = r
  end
  if #wall == 0 then return out, "pas de mur bas" end
  if #wall == #rows and o.more then return out, "mur trop long (annonces non lues au-delà)" end
  if nOwn > R.resetMaxSellers then return out, "trop de vendeurs dans le mur" end
  local nextRow = rows[#wall + 1]
  local sell = market
  if nextRow and int(nextRow.unit) - 1 < sell then sell = int(nextRow.unit) - 1 end
  if sell <= int(wall[#wall].unit) then return out, "pas d'écart entre le mur et le prix habituel" end
  if o.mineLow and int(o.mineLow) < sell then return out, "ta propre vente est sous le prix de revente" end
  local qty, buy = 0, 0
  for _, r in ipairs(wall) do
    qty = qty + int(r.count)
    buy = buy + (o.commodity and int(r.unit) * int(r.count) or int(r.buy))
  end
  local cap = R.resetCap
  if o.cap and int(o.cap) < cap then cap = int(o.cap) end
  if buy > cap then return out, "mur trop cher (plafond dépassé)" end
  local days = X.days(sold, o.ahead, qty)
  if not days or days > R.resetMaxDays then return out, "mur trop gros pour ce qui se vend" end
  local pm = X.sellPm(sold, o.ahead, qty, o.hours)
  local base = { id = o.id, kind = "reset", class = "regul", sellUnit = sell, pm = pm, days = days, group = "reset:" .. tostring(o.id),
    wallQty = qty, wallBuy = buy, wallRows = #wall, sellers = nOwn, worst = max(0, int(o.vendorUnit)) * qty - buy }
  local function mk(take, cost, r)
    local dep = X.deposit(o.vendorUnit, take, o.hours, o.neutral, o.depPm)
    local net, gross, cut, loss = X.flipNet(cost, sell, take, dep, pm, o.neutral)
    local d = { qty = take, buy = cost, buyUnit = ceil(cost / take), maxUnit = r and int(r.unit) or int(wall[#wall].unit),
      gross = gross, cut = cut, dep = dep, depLoss = loss, fees = cut + loss, net = net, capital = cost,
      auctionID = r and r.auctionID or nil, count = r and r.count or nil, commodity = o.commodity or nil }
    for k, v in pairs(base) do d[k] = v end
    return d
  end
  if o.commodity then out[1] = mk(qty, buy, nil)
  else for i, r in ipairs(wall) do out[i] = mk(int(r.count), int(r.buy), r) end end
  local net = 0
  for _, d in ipairs(out) do net = net + d.net end
  if net < X.need(buy, R.minNet, R.resetMarginPct) then return {}, "marge trop faible pour le risque" end
  for _, d in ipairs(out) do d.groupNet, d.groupBuy = net, buy end
  return out
end

--- Annuler puis reposter une vente dépassée vaut-il le dépôt perdu ? (une mise en vente, espérance en cuivre)
--- o = { my (mon prix unitaire), n, dep (mon dépôt, rendu si ça se vend), aheadKeep (unités des autres sous mon prix),
---   new (prix reposté), aheadNew (unités des autres sous ce prix), dep2 (dépôt de la nouvelle mise), cost (frais
---   d'annulation lus dans le jeu, 0 sinon), sold, hours, neutral }
--- garder  = p(garder) × (net de ma vente + dépôt rendu)
--- annuler = p(reposté) × (net reposté + dépôt rendu) − nouveau dépôt − frais d'annulation   (l'ancien dépôt est perdu)
--- → gain (annuler − garder), garder, annuler ; nil si les ventes ne sont pas mesurées
function X.cancelGain(o)
  local n = max(1, int(o.n))
  local pK = X.sellPm(o.sold, o.aheadKeep, n, o.hours)
  local pN = X.sellPm(o.sold, o.aheadNew, n, o.hours)
  if not (pK and pN) then return nil end
  local keepV = floor(pK * (X.net(int(o.my) * n, o.neutral) + max(0, int(o.dep))) / 1000)
  local cxlV = floor(pN * (X.net(int(o.new) * n, o.neutral) + max(0, int(o.dep2))) / 1000) - max(0, int(o.dep2)) - max(0, int(o.cost))
  return cxlV - keepV, keepV, cxlV, pK, pN
end
--- gain minimal pour annuler : max(gain minimal par clic, cancelMinPct % du net reposté)
function X.cancelNeed(new, n, neutral, R)
  R = R or X.RULES
  return max(R.minNet, ceil(X.net(int(new) * max(1, int(n)), neutral) * R.cancelMinPct / 100))
end

--- Marge d'artisanat : fabriquer `made` objets avec des composants payés `cost` (cuivre, prix lus), revendre au prix
--- habituel prudent. o = { cost, made, market, sold, ahead, vendorUnit, hours, neutral, depPm, ev } → table ou nil, pourquoi
function X.craftSpread(o, R)
  R = R or X.RULES
  local class, why = X.evidence(o.ev, R)
  if class ~= "probable" then return nil, why end
  local cost, made, market = int(o.cost), max(1, int(o.made)), int(o.market)
  if cost <= 0 or market <= 0 then return nil, "prix inconnu" end
  local pm = X.sellPm(o.sold, o.ahead, made, o.hours)
  local days = X.days(o.sold, o.ahead, made)
  if not (pm and days) then return nil, "ventes non mesurées" end
  local dep = X.deposit(o.vendorUnit, made, o.hours, o.neutral, o.depPm)
  local net, gross, cut, loss = X.flipNet(cost, market, made, dep, pm, o.neutral)
  if net < X.need(cost, R.minNet, R.marginPct) then return nil, "marge trop faible" end
  return { class = "probable", cost = cost, made = made, sellUnit = market, gross = gross, cut = cut, dep = dep, depLoss = loss,
    fees = cut + loss, net = net, pm = pm, days = days }
end

--- Moments de vente mesurés : b[clé] = { g = unités disparues, h = centièmes d'heure mesurés }, clé = jour × 6 + tranche
--- de 4 h (jour 0 = dimanche). → { key, wd, blk, pm (vitesse du meilleur créneau / moyenne, ‰), n (créneaux comptés) }
--- ou nil, pourquoi — seulement quand les données le permettent.
function X.pattern(b, R)
  R = R or X.RULES
  local G, H, n, days, seen, best = 0, 0, 0, 0, {}, nil
  local keys = {}
  for k in pairs(b or {}) do keys[#keys + 1] = k end
  table.sort(keys)
  for _, k in ipairs(keys) do
    local e = b[k]
    local g, h = max(0, int(e.g)), max(0, int(e.h))
    if h >= R.patMinHours then
      n, G, H = n + 1, G + g, H + h
      local wd = floor(int(k) / 6)
      if not seen[wd] then seen[wd], days = true, days + 1 end
      if not best or g * best.h > best.g * h then best = { key = int(k), g = g, h = h } end
    end
  end
  if n < R.patMinBuckets or days < R.patMinDays then return nil, ("créneaux mesurés : %d sur %d"):format(n, R.patMinBuckets) end
  if G <= 0 then return nil, "aucune vente mesurée" end
  local pm = floor(best.g * H * 1000 / (best.h * G))
  return { key = best.key, wd = floor(best.key / 6), blk = best.key % 6, pm = pm, n = n, clear = pm >= R.patMinPm }
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
