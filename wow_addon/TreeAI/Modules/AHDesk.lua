local tonumber = function(v) return _G.tonumber(v) end   -- tonumber(f()) : un 2e retour de f n'est jamais une base
-- TreeAI — « Bureau d'achat-revente » de l'hôtel des ventes (5.1 AH). Le moteur qui ne peut pas se tromper avec aplomb :
--  * une affaire = prix d'achat, prix de revente, TOUS les frais (commission, dépôts perdus attendus), gain net, capital
--    immobilisé, délai de vente, âge des prix, preuves, et une classe : « sûr » (revente au marchand : prouvable),
--    « probable » (ventes mesurées sur plusieurs jours). Preuves minces ou prix vieux → l'affaire n'existe pas.
--    Les calculs sont dans AHMath.lua (entiers, au cuivre près) ; les prix et les preuves viennent du module Auction
--    (nos scans sur plusieurs jours, Auctionator en lecture seule).
--  * AUCUNE automatisation : chaque achat / mise en vente est UN clic (ou UN appui de touche) du joueur, un seul appel
--    protégé par clic, jamais en combat, jamais HV fermé. Avant chaque achat l'annonce est relue en direct (recherche,
--    non protégée, au rythme du limiteur du jeu) : si elle a changé, l'affaire est recalculée ou sautée.
--  * « Acheter » : meilleur gain net d'abord, jamais mes propres enchères, jamais plus que la part d'or choisie.
--    « Poster » : sous-coter sans jamais passer sous le plancher (coût + frais + marge) ; seulement ce que
--    T.Bags.Verdict dit « à l'HV » (un objet « à garder » n'est jamais proposé).
--  * journal vrai (SavedVariables, rien dans le dépôt) : achats confirmés par le jeu, ventes / expirations lues dans le
--    courrier, reventes au marchand → gain réalisé du jour / 7 jours, or par heure passée à l'HV, objectif du jour.
--  * rien ne tourne HV fermé (hors lecture du courrier quand la boîte est ouverte et un relevé chez le marchand) ;
--    gros calculs étalés par T:Spread (25 objets par image). Rien dans le chat.
-- Lua 5.1, aucune globale, toutes les API du jeu sous pcall, valeurs secrètes écartées.
local T = _G.TreeAI
if not (T and T.RegisterModule and T.AHMath) then return end
local X = T.AHMath
local API = T.API or {}
local G = _G

local M = T:RegisterModule("AHDesk", {
  title = "Achat-revente (HV)",
  description = "À l'hôtel des ventes : files « Acheter » et « Poster » (un clic = une action), gain sûr ou probable, journal et objectif du jour.",
  defaults = { enabled = true, dock = true, goal = 50, sharePct = 50, itemPct = 20, minNetPa = 1, marginPct = 15 },
  charDefaults = { ev = {}, open = {}, posts = {}, names = {}, seen = {}, ahTime = {} },
  options = {
    { key = "dock", type = "toggle", label = "Panneau collé à la fenêtre de l'HV (Acheter / Poster)" },
    { key = "goal", type = "range", label = "Objectif de gain par jour (po)", min = 5, max = 500, step = 5 },
    { key = "sharePct", type = "range", label = "Part de ton or qu'on peut immobiliser (%) — le reste est ta réserve", min = 10, max = 90, step = 10 },
    { key = "itemPct", type = "range", label = "Part max de ton or dans un même objet (%)", min = 5, max = 50, step = 5 },
    { key = "minNetPa", type = "range", label = "Gain net minimal par clic (pa)", min = 1, max = 100, step = 1 },
    { key = "marginPct", type = "range", label = "Marge minimale d'un achat-revente à l'HV (% de la mise)", min = 5, max = 50, step = 5 },
  },
})
T.AHDesk = M
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[4]   -- « Sacs, or, métiers »
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "AHDesk" then has = true end end
    if not has then fam.mods[#fam.mods + 1] = "AHDesk" end
  end
end

local floor, ceil, max, min, abs = math.floor, math.ceil, math.max, math.min, math.abs
local fmt = string.format

-- ===================================================================== utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function num(v)
  local isv = rawget(G, "issecretvalue")
  if v == nil or (isv and isv(v)) then return nil end
  return tonumber(v)
end
local function n0(v) return num(v) or 0 end
local function now() return num(try(G.GetServerTime)) or num(try(G.time)) or 0 end
local function clock() return num(try(G.GetTime)) or 0 end
local function dayKey(t) return try(G.date, "%Y-%m-%d", t or now()) or tostring(floor((t or now()) / 86400)) end
local function gold() return n0(try(G.GetMoney)) end
local function inCombat() return try(G.InCombatLockdown) and true or false end
local function bagCount(id) return n0(API.ItemCount and API.ItemCount(id)) end   -- (GetItemCount n'existe pas sur Forever : C_Item)
local function cfg(k, d) local v = M.db and M.db[k] if v == nil then return d end return v end
local function auc()
  local a = T.modules and T.modules.Auction
  if type(a) == "table" and a._active and type(a.cur) == "table" then return a end
  return nil
end
local function AH()
  local a = rawget(G, "C_AuctionHouse")
  return type(a) == "table" and a or nil
end
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
local function dec(x) if x == nil then return "?" end return (fmt("%.1f", x):gsub("%.", ",")) end

M.status, M.log = "", {}
local function say(msg)
  msg = tostring(msg or "")
  M.status = msg
  local l = M.log
  l[#l + 1] = msg
  while #l > 40 do table.remove(l, 1) end
  local f = M.frame
  if f and f.status then f.status:SetText(msg) end
end
M.Say = say
function M.Said(pattern)
  for i = #M.log, 1, -1 do if M.log[i]:find(pattern) then return M.log[i] end end
end

local function rules()
  local R = M._rules
  if not R then R = {} for k, v in pairs(X.RULES) do R[k] = v end M._rules = R end
  R.minNet = max(1, n0(cfg("minNetPa", 1))) * 100
  R.marginPct = max(1, n0(cfg("marginPct", 15)))
  R.sharePct, R.itemPct = n0(cfg("sharePct", 50)), n0(cfg("itemPct", 20))
  return R
end
M.Rules = rules
local function hoursOf(A)
  local h = max(2, n0(A and A.db and A.db.hours or 24))
  if h <= 12 then return 12 elseif h <= 24 then return 24 end
  return 48
end
local function durIndex(h) return h == 12 and 1 or h == 24 and 2 or 3 end
local function ready()
  local a = AH()
  if not a then return false end
  if type(a.IsThrottledMessageSystemReady) ~= "function" then return true end
  return try(a.IsThrottledMessageSystemReady) and true or false
end
-- diagnostic « ce que le vrai client a répondu » (TreeAIDB.diag.ah → fichier d'erreurs) : compteurs, jamais de nom
local function diag(k, v)
  local db = T.db
  if type(db) ~= "table" then return end
  db.diag = type(db.diag) == "table" and db.diag or {}
  local d = type(db.diag.ah) == "table" and db.diag.ah or {}
  db.diag.ah = d
  if v == nil then d[k] = n0(d[k]) + 1 else d[k] = v end
end

-- ===================================================================== journal (vrai, par perso)
local function char() return M.char end
function M.Realized()
  local r = M._real
  local ch = char()
  if r and r.n == (ch and #ch.ev or 0) and r.day == dayKey() then return r end
  r = X.realized(ch and ch.ev or {})
  local t = now()
  local week = 0
  for i = 0, 6 do week = week + n0(r.days[dayKey(t - i * 86400)]) end
  r.today, r.week, r.n, r.day = n0(r.days[dayKey(t)]), week, ch and #ch.ev or 0, dayKey(t)
  M._real = r
  return r
end
--- secondes passées HV ouvert aujourd'hui (session en cours comprise)
function M.TimeToday()
  local ch = char()
  local s = n0(ch and ch.ahTime and ch.ahTime[dayKey()])
  if M.ahOpen and M.openT then s = s + max(0, now() - M.openT) end
  return s
end
--- or par heure passée à l'HV aujourd'hui ; nil tant qu'il n'y a pas 5 min de mesure
function M.PerHour()
  local s = M.TimeToday()
  if s < 300 then return nil end
  return floor(M.Realized().today * 3600 / s)
end
function M.Note(k, e)
  local ch = char()
  if not ch then return end
  e.k, e.t, e.day = k, now(), dayKey()
  ch.ev[#ch.ev + 1] = e
  while #ch.ev > 3000 do table.remove(ch.ev, 1) end
  local id = e.id
  if id then
    if type(e.name) == "string" and e.name ~= "" then ch.names[e.name] = id end   -- le courrier ne donne que le nom
    local o = ch.open[id]
    if k == "buy" then
      o = o or { n = 0 }
      o.n, o.kind = o.n + max(1, n0(e.n)), e.kind or o.kind
      ch.open[id] = o
    elseif (k == "sale" or k == "vend") and o then
      o.n = o.n - max(1, n0(e.n))
      if o.n <= 0 then ch.open[id] = nil end
    end
    if T.Bags and T.Bags.Invalidate then pcall(T.Bags.Invalidate, id) end
  end
  M._real = nil
end
local function takePost(id, n)
  local ch = char()
  local q, dep = ch and id and ch.posts[id], 0
  while q and n > 0 and q[1] do
    local l = q[1]
    local k = min(n, n0(l.n))
    local part = (k >= n0(l.n)) and n0(l.dep) or floor(n0(l.dep) * k / max(1, n0(l.n)))
    dep, n = dep + part, n - k
    l.n, l.dep = n0(l.n) - k, n0(l.dep) - part
    if l.n <= 0 then table.remove(q, 1) end
  end
  if q and #q == 0 then ch.posts[id] = nil end
  return dep
end

-- courrier de l'HV : ventes (facture : prix, commission), expirations / annulations (dépôt perdu). Lecture seule.
local function mailOne(i, A)
  local ch = char()
  local _, _, _, subject, mmoney, _, daysLeft = try(G.GetInboxHeaderInfo, i)
  if type(subject) ~= "string" then return end
  local kind, name, qty = A.MailKind(subject)
  if not kind or kind == "buy" then return end
  local t = now()
  local arrived = floor((t - (30 - n0(daysLeft)) * 86400) / 7200)
  local base = subject .. "|" .. n0(mmoney) .. "|"
  if ch.seen[base .. arrived] or ch.seen[base .. (arrived - 1)] or ch.seen[base .. (arrived + 1)] then return end
  ch.seen[base .. arrived] = t
  local itype, iname, _, bid, _, deposit, cutv = try(G.GetInboxInvoiceInfo, i)
  local _, itemID, _, icount = try(G.GetInboxItem, i, 1)
  local nm = (type(iname) == "string" and iname ~= "" and iname) or name
  local id = num(itemID) or (nm and ch.names[nm]) or nil
  local n = num(qty) or (n0(icount) > 0 and n0(icount)) or 1
  if kind == "sell" then
    local e = { id = id, name = nm, n = n, src = "courrier" }
    if itype == "seller" and n0(bid) > 0 then e.c, e.cut = n0(bid), n0(cutv)
    else e.c, e.cut, e.netOnly = n0(mmoney), 0, true end   -- pas de facture lisible : seul le net reçu est connu
    if e.c > 0 and e.cut > 0 then diag("cutPm", floor(e.cut * 1000 / e.c)) end
    if id then takePost(id, n) end
    M.Note("sale", e)
  else
    local dep = id and takePost(id, n) or 0
    M.Note(kind == "expired" and "exp" or "cancel", { id = id, name = nm, n = n, dep = dep, src = "courrier" })
  end
end
function M.MailStep()
  local A = T.modules and T.modules.Auction
  local ch = char()
  if not (ch and type(A) == "table" and type(A.MailKind) == "function") then M.mailI = nil return end
  local n = min(100, n0(try(G.GetInboxNumItems)))
  local i = M.mailI or 1
  local last = min(n, i + 7)
  for k = i, last do mailOne(k, A) end
  if last < n then
    M.mailI = last + 1
    T:Spread(M, M.MailStep)
    return
  end
  M.mailI = nil
  local t = now()
  for k, v in pairs(ch.seen) do if t - n0(v) > 32 * 86400 then ch.seen[k] = nil end end
  M.Refresh()
end
function M.MAIL_INBOX_UPDATE()
  if M.mailWait or M.mailI then return end
  M.mailWait = true
  T:After(0.4, function()
    M.mailWait = nil
    M.mailI = 1
    T:Spread(M, M.MailStep)
  end)
end

-- marchand : un lot acheté « à revendre au marchand » qui quitte les sacs pendant que le marchand est ouvert = vendu
function M.MERCHANT_SHOW()
  local ch = char()
  if not ch or next(ch.open) == nil then M.merch = nil return end
  local snap, k = {}, 0
  for id in pairs(ch.open) do
    snap[id] = bagCount(id)
    k = k + 1
    if k >= 60 then break end
  end
  M.merch = snap
end
function M.MERCHANT_CLOSED()
  local snap, ch = M.merch, char()
  M.merch = nil
  if not (snap and ch) then return end
  local A = T.modules and T.modules.Auction
  for id, before in pairs(snap) do
    local o = ch.open[id]
    local sold = before - bagCount(id)
    if o and sold > 0 then
      local vend = type(A) == "table" and A.ItemInfo and num((select(11, A.ItemInfo(id)))) or nil
      if vend and vend > 0 then
        local n = min(sold, o.n)
        M.Note("vend", { id = id, name = itemName(id), n = n, c = floor(vend) * n, cut = 0, src = "marchand" })
      end
    end
  end
  M.Refresh()
end

-- ===================================================================== affaires
M.all, M.buyQ, M.postQ, M.waitQ = {}, {}, {}, {}
M.sessPosted = {}   -- [id] = { n = unités postées cette visite, low = mon prix le plus bas }
M.touched = {}   -- [id] = heure de mon dernier achat : les annonces lues avant ne valent plus pour poster
M.sess = { buys = 0, spent = 0, expect = 0, posts = 0, skipped = 0 }

local function vendorSure(A, id)
  local v = num((select(11, A.ItemInfo(id))))
  if v and v > 0 then return floor(v) end
  return nil
end
local function commodityOf(id)
  local a = AH()
  if not (a and type(a.GetItemCommodityStatus) == "function") then return nil end
  local s = num(try(a.GetItemCommodityStatus, id))
  if s == 2 then return true elseif s == 1 then return false end
  return nil
end
--- mes enchères de cet objet : unités en vente, mon prix le plus bas, signatures « prix:quantité »
local function ownedOf(A, id)
  local units, low, sig = 0, nil, nil
  local sp = M.sessPosted[id]                    -- posté pendant cette visite (la liste du jeu n'est relue qu'à la fin)
  if sp then units, low = sp.n, sp.low end
  for _, a in ipairs(A.owned or {}) do
    if a.id == id and a.state ~= "cancelled" then
      units = units + n0(a.count)
      local u = n0(a.unit)
      if u > 0 and (not low or u < low) then low = u end
      sig = sig or {}
      sig[u .. ":" .. n0(a.count)] = true
    end
  end
  return units, low, sig
end
--- annonces des AUTRES, triées par prix ; une annonce qui ressemble à une des miennes (même prix, même quantité)
--- est écartée : on n'achète jamais ses propres enchères.
local function rowsOf(A, id, listings, R, forPost)
  local _, mineLow, sig = ownedOf(A, id)
  local rows = {}
  -- pour ACHETER une marchandise : le jeu vend du moins cher au plus cher → seulement ce qui est STRICTEMENT sous ma
  -- propre enchère (pour poster, on regarde tous les concurrents)
  local cap = (not forPost and mineLow and commodityOf(id) ~= false) and mineLow or nil
  for _, l in ipairs(listings or {}) do
    if cap and n0(l.unit) >= cap then break end
    if l.owner ~= "player" and not (sig and sig[n0(l.unit) .. ":" .. n0(l.count)]) and n0(l.unit) > 0 and n0(l.count) > 0 then
      rows[#rows + 1] = l
      if #rows > R.rows then break end
    end
  end
  return rows
end
local function evidenceOf(A, id, liq)
  local r = A.RealmData()
  local rec = r and r.items[id]
  local why = liq.reasons and liq.reasons[1] and liq.reasons[1].text
  return { ok = liq.ok and true or false, why = why, days = n0(liq.days), scans = n0(liq.scans), hours = n0(liq.hours),
    sold = liq.sold, sellers = n0(liq.sellers), volPct = floor(n0(liq.vol) * 100 + 0.5),
    ageH = rec and max(0, (now() - n0(rec.last)) / 3600) or 9999 }
end
-- « tu le garderais ? » (métiers, quêtes, T.Bags) : réponse gardée 2 min par objet (le plan de métier coûte cher à relire)
M.keepC, M.keepCalls = {}, 0
local function keepOf(A, id)
  local c = M.keepC[id]
  local t = clock()
  if c and t - c.t < 120 then return c.n end
  M.keepCalls = M.keepCalls + 1
  local n = A.KeepOf and n0(try(A.KeepOf, id)) or 0
  M.keepC[id] = { n = n, t = t }
  return n
end
--- affaires d'un objet à partir de ses annonces (du scan, ou relues en direct : live = true)
local function dealsFor(A, id, rows, t, live)
  local R = rules()
  if #rows == 0 then return nil end
  local vend = vendorSure(A, id)
  local out, com
  if vend and rows[1].unit < vend then
    com = commodityOf(id)
    if com ~= nil then out = X.vendorDeals(id, rows, vend, com, R) end
  end
  local evd
  if not out or #out == 0 then
    out = nil
    local r = A.RealmData()
    local rec = r and r.items[id]
    local h = rec and rec.h
    -- pré-filtre gratuit : assez de jours d'historique et l'annonce la moins chère sous la dernière médiane vue
    if h and #h >= 7 * R.minDays and rows[1].unit < n0(h[#h - 4]) then
      local liq = A.Liq(id)
      if type(liq) == "table" and liq.ok and n0(liq.market) > 0 then
        if com == nil then com = commodityOf(id) end
        if com ~= nil then
          evd = evidenceOf(A, id, liq)
          local listed = ownedOf(A, id)
          local held = bagCount(id) + listed
          out = X.flipDeals({ id = id, rows = rows, market = floor(liq.market * (1 - n0(liq.vol) / 2)), sold = liq.sold,
            ahead = listed, maxQty = floor(n0(liq.sold) * R.maxDays) - held, vendorUnit = vend or 0, hours = hoursOf(A),
            neutral = A.IsNeutral(), depPm = M.db and M.db.depPm and M.db.depPm[hoursOf(A)], commodity = com, ev = evd }, R)
        end
      end
    end
  end
  if not out or #out == 0 then return nil end
  if A.Odd and try(A.Odd, id) then return nil end
  if keepOf(A, id) > 0 then return nil end   -- un objet que tu garderais (métier, quête) : pas pour revendre
  local name = itemName(id)
  for _, d in ipairs(out) do
    d.name, d.t, d.ev = name, t, evd
    if live then d.liveT = clock() end
  end
  return out
end
M._DealsFor = dealsFor

function M.Budget()
  local A = auc()
  local g, tied = gold(), M.Realized().tied
  local reserve = A and A.Reserve and n0((try(A.Reserve))) or 0
  local R = rules()
  return X.budget(g, tied, R.sharePct, reserve), g, tied, reserve
end
function M.Allocate()
  local R = rules()
  local budget, g, tied = M.Budget()
  local cand, visible = {}, 0
  for _, d in ipairs(M.all) do
    if not d.state or d.state == "confirm" then cand[#cand + 1] = d visible = visible + d.net end
  end
  M.buyQ, M.planSpend, M.planNet = X.allocate(cand, budget, floor((g + tied) * R.itemPct / 100))
  M.budget, M.visibleNet = budget, visible
end
function M.Build()
  local A = auc()
  if not (A and M.ahOpen) then return false end
  local ids = {}
  for id in pairs(A.cur) do ids[#ids + 1] = id end
  table.sort(ids)
  local first = M.build == nil
  M.build = { ids = ids, i = 0, deals = {} }
  if first then T:Spread(M, M.BuildStep) end
  return true
end
local BUILD_N = 25
function M.BuildStep()
  local b, A = M.build, auc()
  if not (b and A and M.ahOpen) then M.build = nil return end
  local R = rules()
  local last = min(#b.ids, b.i + BUILD_N)
  local calls = M.keepCalls
  for i = b.i + 1, last do
    local id = b.ids[i]
    local it = A.cur[id]
    if it and it.listings and it.listings[1] then
      local ds = dealsFor(A, id, rowsOf(A, id, it.listings, R), it.t, false)
      if ds then for _, d in ipairs(ds) do b.deals[#b.deals + 1] = d end end
    end
    if M.keepCalls - calls >= 2 then last = i break end   -- deux questions « tu le garderais ? » par image au plus
  end
  b.i = last
  if last < #b.ids then T:Spread(M, M.BuildStep) return end
  M.build = nil
  local keep = {}
  for _, d in ipairs(M.all) do if d.state == "confirm" then keep[#keep + 1] = d end end   -- un prix déjà confirmé par le jeu reste
  for _, d in ipairs(b.deals) do keep[#keep + 1] = d end
  M.all, M.built = keep, now()
  M.Allocate()
  M.BuildPosts()
  M.Refresh()
  M.Prefetch()
end
--- appelé par le module Auction quand un scan (complet ou ciblé) vient d'être lu
function M.OnScan()
  if M._active and M.ahOpen then M.Build() end
end

--- l'annonce en tête de la file d'achat (un prix confirmé par le jeu passe devant)
function M.BuyHead()
  for _, d in ipairs(M.buyQ) do if d.state == "confirm" then return d end end
  for _, d in ipairs(M.buyQ) do if not d.state then return d end end
  return nil
end
local FRESH = 30
local function fresh(d) return d.liveT ~= nil and clock() - d.liveT <= FRESH and (d.commodity or d.auctionID ~= nil) end

--- relit en direct l'annonce en tête (recherche : non protégée, au rythme du limiteur du jeu)
function M.Prefetch()
  local A = auc()
  if not (A and M.ahOpen) or M.pend or A.job or A.mini or A.pendingAll then return false end
  local d = M.BuyHead()
  if not d or d.state == "confirm" or fresh(d) then return false end
  local s = M.search
  if s and clock() - s.t < 4 then return false end
  if not ready() then return false end
  local q = { id = d.id, t = clock() }
  M.search = q                                   -- avant l'envoi : la réponse peut arriver tout de suite
  if A.SearchItem(d.id) then
    T:After(4.2, function() if M.search == q then M.search = nil M.Prefetch() end end)   -- sans réponse : on réessaie
    return true
  end
  if M.search == q then M.search = nil end
  return false
end
--- annonces relues en direct pour `id` : ses affaires sont recalculées (ou disparaissent)
function M.Live(id, rows, commodity)
  local A = auc()
  if not A then return end
  local R = rules()
  table.sort(rows, function(a, b) if a.unit ~= b.unit then return a.unit < b.unit end return n0(a.auctionID) < n0(b.auctionID) end)
  local clean = {}
  for _, r in ipairs(rows) do
    if r.owner ~= nil then
      if commodity then break end   -- marchandise : on s'arrête avant ma propre enchère (le jeu achète du moins cher au plus cher)
    else
      clean[#clean + 1] = r
    end
  end
  local had = false
  local keep = {}
  for _, d in ipairs(M.all) do
    if d.id ~= id or d.state == "confirm" then keep[#keep + 1] = d elseif not d.state then had = true end
  end
  local ds = dealsFor(A, id, rowsOf(A, id, clean, R), now(), true)
  for _, d in ipairs(ds or {}) do keep[#keep + 1] = d end
  if had and not ds then
    M.sess.skipped = M.sess.skipped + 1
    say(fmt("%s : l'annonce a changé, affaire retirée.", itemName(id)))
  end
  M.all = keep
  M.Allocate()
end
--- appelé par le module Auction quand une recherche d'objet vient d'être lue (lui a déjà rangé ces annonces)
function M.OnSearchRows(id, rows, commodity)
  if not (M._active and M.ahOpen and id) then return end
  local A = auc()
  if not A then return end
  local mine = M.search and M.search.id == id
  if mine then M.search = nil end
  local wanted = mine
  if not wanted then for _, d in ipairs(M.all) do if d.id == id and not d.state then wanted = true break end end end
  if wanted then M.Live(id, rows, commodity and true or false) end
  -- une recherche envoyée APRÈS mon dernier achat de cet objet : ses annonces sont de nouveau vraies pour poster
  local ls, dirty = A.lastSearch, false
  if M.touched[id] and type(ls) == "table" and ls.id == id and n0(ls.t) >= M.touched[id] then M.touched[id] = nil dirty = true end
  if not dirty then for _, p in ipairs(M.postQ) do if p.id == id and not p.state then dirty = true break end end end
  if not dirty then for _, p in ipairs(M.waitQ) do if p.id == id then dirty = true break end end end
  if dirty and not A.mini then if M.pb then M.postsDirty = true else M.BuildPosts() end end   -- (relecture ciblée : refait à sa fin)
  M.Refresh()
  T:After(0, M.Prefetch)
end

-- ===================================================================== file « Poster »
local function lotCost(id, qty)
  local it = M.Realized().items[id]
  if it and n0(it.held) > 0 and n0(it.heldCost) > 0 then return ceil(it.heldCost * min(qty, it.held) / it.held), true end
  return nil, false
end
--- une pile des sacs → entrée de la file, ou nil, état ("check" = prix à relire, "wait" = pas maintenant), pourquoi
local function postEntry(A, bag, slot, id, count)
  local ch = char()
  local lot = ch and ch.open[id]
  local B = T.Bags
  if type(B) == "table" and B._active and type(B.Verdict) == "function" then
    local ok, v = pcall(B.Verdict, id, bag, slot)
    if not (ok and type(v) == "table" and not v.off and v.action == "ah") then return nil end   -- « garde » : jamais proposé
  elseif not (lot and n0(lot.n) > 0 and lot.kind ~= "vendor") then
    return nil   -- assistant des sacs coupé : seulement ce que TU as acheté pour le revendre
  end
  if A.Odd and try(A.Odd, id) then return nil end
  local it = A.cur[id]
  -- prix vieux de plus de 10 min, ou lus AVANT mon dernier achat de cet objet (les annonces ont changé) : à relire
  if not it or now() - n0(it.t) > 600 or M.touched[id] then return nil, "check", "prix à relire" end
  local R = rules()
  local com = commodityOf(id)
  if com == nil then return nil, "wait", "type d'enchère inconnu" end
  local listed, mineLow = ownedOf(A, id)
  local rows = rowsOf(A, id, it.listings, R, true)
  local liq = A.Liq(id)
  local hrs, neutral = hoursOf(A), A.IsNeutral()
  local qty = com and count or 1
  if type(liq) == "table" and A.F and A.F.postQty then qty = min(qty, A.F.postQty(qty, liq.sold, hrs, listed)) end
  if qty <= 0 then return nil, "wait", "déjà assez en vente pour ce qui se vend" end
  local vend = vendorSure(A, id) or 0
  local a = AH()
  local dep, exact
  if a then
    if com and type(a.CalculateCommodityDeposit) == "function" then dep = num(try(a.CalculateCommodityDeposit, id, durIndex(hrs), qty))
    elseif not com and type(a.CalculateItemDeposit) == "function" then
      local IL = rawget(G, "ItemLocation")
      local loc = type(IL) == "table" and try(IL.CreateFromBagAndSlot, IL, bag, slot)
      if loc then dep = num(try(a.CalculateItemDeposit, loc, durIndex(hrs), qty)) end
    end
  end
  if dep and dep >= 0 then
    exact, dep = true, floor(dep)
    if vend > 0 and M.db then   -- part apprise du jeu (‰ du prix marchand) : sert aux estimations d'achat
      M.db.depPm = type(M.db.depPm) == "table" and M.db.depPm or {}
      M.db.depPm[hrs] = max(n0(M.db.depPm[hrs]), ceil(dep * 1000 / (vend * qty)))
      diag("depPm" .. hrs, M.db.depPm[hrs])
    end
  else
    dep = X.deposit(vend, qty, hrs, neutral, M.db and M.db.depPm and M.db.depPm[hrs])
  end
  local pm = X.sellPm(type(liq) == "table" and liq.sold or nil, listed, qty, hrs) or 500
  local cost, mineLot = lotCost(id, qty)
  if not cost then cost = vend * qty end                    -- butin : il vaut au moins ce que le marchand en donne
  local fl = X.floorUnit(cost, qty, dep, pm, neutral, X.need(cost, R.minNet, R.marginPct))
  local normal = (type(liq) == "table" and liq.ok and n0(liq.market) > 0) and floor(liq.market) or nil
  local unit, how, why = X.postUnit({ floor = fl, low = rows[1] and rows[1].unit, mine = mineLow, normal = normal,
    undercutPct = A.db and A.db.undercutPct })
  if not unit then return nil, how == "unknown" and "check" or "wait", why end
  if a and type(a.SupportsCopperValues) == "function" and try(a.SupportsCopperValues) == false then unit = ceil(unit / 100) * 100 end
  local gain, gross, cut = X.flipNet(cost, unit, qty, dep, pm, neutral)
  return { id = id, name = itemName(id), bag = bag, slot = slot, count = count, qty = qty, unit = unit, floor = fl, dep = dep,
    depExact = exact or false, cut = cut, cash = gross - cut, gain = gain, how = how, lot = mineLot, commodity = com,
    low = rows[1] and rows[1].unit, hours = hrs, pm = pm, t = it.t }
end
M._PostEntry = postEntry
local POST_N, POST_SLOTS = 5, 24
--- file « Poster » refaite en fond : les sacs sont lus par tranches (24 cases ou 5 piles évaluées par image)
function M.BuildPosts()
  local A = auc()
  if not (A and M.ahOpen) then return false end
  local first = M.pb == nil
  M.pb = { bag = 0, slot = 0, out = {}, wait = {}, check = {} }
  if first then T:Spread(M, M.PostStep) end
  return true
end
function M.PostStep()
  local b, A = M.pb, auc()
  if not (b and A and M.ahOpen) then M.pb = nil return end
  local maxBag = API.NumBags and API.NumBags() or 4
  local seen, done = 0, 0
  while b.bag <= maxBag and seen < POST_SLOTS and done < POST_N do
    local slots = n0(API.BagSlots and API.BagSlots(b.bag))
    if b.slot >= slots then
      b.bag, b.slot = b.bag + 1, 0
    else
      b.slot, seen = b.slot + 1, seen + 1
      local item = API.BagItem and API.BagItem(b.bag, b.slot)
      local id = item and num(item.itemID)
      if id and not item.locked and not item.noValue then
        done = done + 1
        local e, state, why = postEntry(A, b.bag, b.slot, id, max(1, n0(item.count)))
        if e then b.out[#b.out + 1] = e
        elseif state == "check" then b.check[id] = true
        elseif state == "wait" then b.wait[#b.wait + 1] = { id = id, name = itemName(id), why = why } end
      end
    end
  end
  if b.bag <= maxBag then T:Spread(M, M.PostStep) return end
  M.pb = nil
  -- une pile postée (ou déplacée) pendant le calcul : son entrée ne vaut plus
  local out = {}
  for _, e in ipairs(b.out) do
    local item = API.BagItem and API.BagItem(e.bag, e.slot)
    if item and item.itemID == e.id and n0(item.count) == e.count then out[#out + 1] = e end
  end
  table.sort(out, function(x, y)
    if x.gain ~= y.gain then return x.gain > y.gain end
    if x.bag ~= y.bag then return x.bag < y.bag end
    return x.slot < y.slot
  end)
  M.postQ, M.waitQ = out, b.wait
  -- prix trop vieux pour poster : relecture ciblée de ces objets (recherches, non protégées), une fois par minute au plus
  local ids = {}
  M.checked = M.checked or {}
  for id in pairs(b.check) do
    if clock() - n0(M.checked[id] or -999) > 60 then ids[#ids + 1] = id M.checked[id] = clock() end
  end
  if #ids > 0 and not A.mini and not A.job and A.ScanTargets then table.sort(ids) A.ScanTargets(ids) end
  M.Refresh()
  if M.postsDirty then M.postsDirty = nil M.BuildPosts() end
end
function M.PostHead()
  for _, p in ipairs(M.postQ) do if not p.state then return p end end
  return nil
end

-- ===================================================================== actions (UN appel protégé par clic)
local function refuse()
  if not M.ahOpen then say("Ouvre l'hôtel des ventes d'abord.") return true end
  if inCombat() then say("En combat : rien à l'HV avant la fin du combat.") return true end
  if M.pend then say("Le jeu n'a pas encore répondu à l'action précédente : reclique dans un instant.") return true end
  if not AH() then say("Hôtel des ventes : API du jeu introuvable.") return true end
  return false
end
local function arm(p)
  M.pend = p
  p.t = clock()
  T:After(6, function()
    if M.pend ~= p then return end
    M.Resolve(nil, "délai")
  end)
end
--- fin d'une action : ok = true (le jeu confirme), false (refus), nil (pas de réponse : on regarde l'or / les sacs)
function M.Resolve(ok, why)
  local p = M.pend
  if not p then return end
  if p.kind == "quote" then
    if ok == nil then M.pend = nil p.d.state = nil p.d.liveT = nil say("Pas de prix reçu du jeu : reclique.") M.Refresh() end
    return
  end
  if ok == nil then
    if p.kind == "buy" then ok = (p.money - gold()) == p.paid
    else
      local item = API.BagItem and API.BagItem(p.e.bag, p.e.slot)
      ok = (not item or item.itemID ~= p.e.id or n0(item.count) < p.e.count) and gold() <= p.money
    end
    if not ok then diag("noAnswer") end
  end
  M.pend = nil
  if p.kind == "buy" then
    local d = p.d
    if ok then
      d.state = "done"
      M.sess.buys, M.sess.spent, M.sess.expect = M.sess.buys + 1, M.sess.spent + p.paid, M.sess.expect + d.net
      M.Note("buy", { id = d.id, name = d.name, n = d.qty, c = p.paid, kind = d.kind, cls = d.class })
      M.touched[d.id] = clock()
      if M.checked then M.checked[d.id] = nil end
      if bagCount(d.id) > 0 then if M.pb then M.postsDirty = true else M.BuildPosts() end end   -- ses prix de mise en vente ont changé
      say(fmt("Acheté : %s ×%d pour %s. Gain %s attendu : +%s.", d.name, d.qty, money(p.paid),
        d.class == "sur" and "sûr" or "probable", money(d.net)))
    else
      d.state, d.why = "skip", why or "refusé par le jeu"
      M.sess.skipped = M.sess.skipped + 1
      say(fmt("%s : achat non confirmé par le jeu (%s). Rien n'est compté.", d.name, why or "refus"))
    end
    M.Allocate()
  else
    local e = p.e
    if ok then
      e.state = "done"
      M.sess.posts = M.sess.posts + 1
      local ch = char()
      if ch then
        ch.posts[e.id] = ch.posts[e.id] or {}
        local q = ch.posts[e.id]
        q[#q + 1] = { n = e.qty, u = e.unit, dep = e.dep, t = now() }
        ch.names[e.name] = e.id
      end
      if T.Stats and T.Stats.NotePost then pcall(T.Stats.NotePost, e.name) end
      local sp = M.sessPosted[e.id] or { n = 0 }
      sp.n, sp.low = sp.n + e.qty, (sp.low and sp.low < e.unit) and sp.low or e.unit
      M.sessPosted[e.id] = sp
      for _, o in ipairs(M.postQ) do if o.bag == e.bag and o.slot == e.slot then o.state = "done" end end   -- file refaite entre-temps
      if M.pb then M.postsDirty = true end
      say(fmt("En vente : %s ×%d à %s l'unité (dépôt %s).", e.name, e.qty, money(e.unit), money(e.dep)))
    else
      e.state, e.why = "skip", why or "refusé par le jeu"
      say(fmt("%s : mise en vente non confirmée par le jeu (%s).", e.name, why or "refus"))
    end
  end
  M.Refresh()
  T:After(0, M.Prefetch)
end

function M.BuyClick()
  if refuse() then return false end
  local d = M.BuyHead()
  if not d then say(M.built and "Rien à acheter pour l'instant : aucune affaire sûre ou prouvée." or "Scanne d'abord : clique « Scanner ».") return false end
  if not ready() then
    say(M.search and fmt("%s : je relis l'annonce… reclique.", d.name) or "Le jeu digère l'action précédente : reclique.")
    return false
  end
  local a = AH()
  local budget, g, _, reserve = M.Budget()
  if d.state == "confirm" then
    if clock() - n0(d.quoteT) > 25 then
      d.state, d.liveT = nil, nil
      try(a.CancelCommoditiesPurchase)
      say(fmt("%s : prix expiré, je le relis.", d.name))
      M.Prefetch()
      return false
    end
    arm({ kind = "buy", d = d, money = g, paid = d.buy })
    try(a.ConfirmCommoditiesPurchase, d.id, d.qty)             -- action protégée : dans le clic du joueur
    return true
  end
  if not fresh(d) then
    M.Prefetch()
    say(fmt("%s : je relis l'annonce… reclique.", d.name))
    return false
  end
  if d.buy > budget or d.buy > g - reserve then
    d.state, d.why = "skip", "capital"
    M.Allocate()
    say(fmt("%s : plus assez d'or libre (%s), affaire sautée.", d.name, money(budget)))
    M.Refresh()
    return false
  end
  if d.commodity then
    M.pend = { kind = "quote", d = d, t = clock() }
    local p = M.pend
    T:After(6, function() if M.pend == p then M.Resolve(nil, "délai") end end)
    try(a.StartCommoditiesPurchase, d.id, d.qty)               -- action protégée (le jeu répond par son prix)
    return true
  end
  arm({ kind = "buy", d = d, money = g, paid = d.buy })
  try(a.PlaceBid, d.auctionID, d.buy)                          -- action protégée : rachat immédiat de CETTE annonce
  return true
end

function M.PostClick()
  if refuse() then return false end
  local A = auc()
  local e = M.PostHead()
  if not (A and e) then say("Rien à poster pour l'instant.") return false end
  if not ready() then say("Le jeu digère l'action précédente : reclique.") return false end
  if A.Scanning and A.Scanning() then say("Scan en cours : mise en vente après la lecture des prix.") return false end
  local item = API.BagItem and API.BagItem(e.bag, e.slot)
  if not (item and item.itemID == e.id and n0(item.count) == e.count and not item.locked) then
    e.state = "skip"
    say("Tes sacs ont changé : je les relis… reclique.")
    if M.pb then M.postsDirty = true else M.BuildPosts() end
    return false
  end
  -- tout est recalculé à l'instant du clic (avis des sacs, prix, plancher) : si ça ne tient plus, on ne poste pas
  local n, state, why = postEntry(A, e.bag, e.slot, e.id, e.count)
  if not n or n.unit < n.floor then
    e.state, e.why = "skip", why or state
    say(fmt("%s : %s, pas mis en vente.", e.name, why or "le prix a bougé"))
    if state == "check" then M.BuildPosts() end
    M.Refresh()
    return false
  end
  local g = gold()
  if n.dep > g then e.state = "skip" say(fmt("%s : pas assez d'or pour le dépôt (%s).", e.name, money(n.dep))) return false end
  e.qty, e.unit, e.floor, e.dep, e.gain, e.cash, e.cut, e.how = n.qty, n.unit, n.floor, n.dep, n.gain, n.cash, n.cut, n.how
  local a = AH()
  local IL = rawget(G, "ItemLocation")
  local loc = type(IL) == "table" and try(IL.CreateFromBagAndSlot, IL, e.bag, e.slot)
  if not loc then say("Mise en vente impossible : emplacement d'objet illisible.") return false end
  arm({ kind = "post", e = e, money = g })
  if e.commodity then try(a.PostCommodity, loc, durIndex(n.hours), e.qty, e.unit)          -- action protégée (clic)
  else try(a.PostItem, loc, durIndex(n.hours), e.qty, nil, e.unit) end                    -- action protégée (clic)
  return true
end

--- « Action suivante » (bouton ou touche) : au plus UNE action protégée par appui
function M.Next()
  if M.BuyHead() then return M.BuyClick() end
  if M.PostHead() then return M.PostClick() end
  if refuse() then return false end
  say(M.built and "Rien à faire : aucune affaire, rien à poster." or "Scanne d'abord : clique « Scanner ».")
  return false
end
function M.ScanClick()
  local A = auc()
  if not (A and M.ahOpen) then say("Ouvre l'hôtel des ventes d'abord.") return false end
  if inCombat() then say("En combat : rien à l'HV avant la fin du combat.") return false end
  local ok = A.ScanAll and A.ScanAll()
  if not ok and A.ScanTargets and not A.mini and not A.job then
    -- scan complet pas disponible (1 fois / 15 min) : relecture ciblée de ce que tu échanges (≤ 40 objets)
    local ids, seen = {}, {}
    local function add(id) if id and not seen[id] and #ids < 40 then seen[id] = true ids[#ids + 1] = id end end
    for _, d in ipairs(M.all) do add(d.id) end
    for id in pairs(char() and char().open or {}) do add(id) end
    for _, id in ipairs(A.TargetIDs and A.TargetIDs() or {}) do add(id) end
    ok = #ids > 0 and A.ScanTargets(ids)
  end
  say(A.msg or (ok and "Scan demandé…" or "Scan impossible pour l'instant."))
  return ok and true or false
end
function M.Counts()
  local b, p = 0, 0
  for _, d in ipairs(M.buyQ) do if not d.state or d.state == "confirm" then b = b + 1 end end
  for _, e in ipairs(M.postQ) do if not e.state then p = p + 1 end end
  return b, p, #M.waitQ
end

-- réponses du jeu
function M.COMMODITY_PRICE_UPDATED(_, _, _, total)
  local p = M.pend
  if not (p and p.kind == "quote") then return end
  local d, a = p.d, AH()
  M.pend = nil
  total = n0(total)
  diag("quote")
  if total > 0 and total <= d.buy then
    -- le prix du jeu fait foi : l'affaire est refaite au cuivre près avec CE prix
    d.net, d.buy, d.capital = d.net + (d.buy - total), total, total
    d.buyUnit = ceil(total / d.qty)
    d.state, d.quoteT = "confirm", clock()
    say(fmt("%s ×%d : prix confirmé par le jeu (%s). Reclique pour acheter.", d.name, d.qty, money(total)))
  else
    if a then try(a.CancelCommoditiesPurchase) end
    d.state, d.why = "skip", "prix monté"
    M.sess.skipped = M.sess.skipped + 1
    say(fmt("%s : le prix est monté à %s (prévu %s), achat annulé.", d.name, money(total), money(d.buy)))
    M.Allocate()
  end
  M.Refresh()
  T:After(0, M.Prefetch)
end
function M.COMMODITY_PRICE_UNAVAILABLE()
  local p = M.pend
  if not (p and p.kind == "quote") then return end
  M.pend = nil
  p.d.state, p.d.why = "skip", "plus en vente"
  say(fmt("%s : plus en vente à ce prix, affaire sautée.", p.d.name))
  M.Allocate()
  M.Refresh()
  T:After(0, M.Prefetch)
end
local function bought(ev) return function() diag(ev) local p = M.pend if p and p.kind == "buy" then M.Resolve(true) end end end
local function failed(ev, kind) return function(_, _, code)
  diag(ev)
  local p = M.pend
  if p and (not kind or p.kind == kind) and p.kind ~= "quote" then
    M.Resolve(false, type(code) == "number" and ("erreur " .. code) or "refus du jeu")
  end
end end
function M.PLAYER_MONEY()
  local p = M.pend
  if p and p.kind == "buy" and (p.money - gold()) == p.paid then diag("moneyOnly") M.Resolve(true) end
end

-- ===================================================================== textes (français simple)
function M.DealText(d)
  if not d then return nil end
  if d.state == "confirm" then
    return fmt("Confirmer : %s ×%d pour %s (prix donné par le jeu). Gain %s : +%s.", d.name, d.qty, money(d.buy),
      d.class == "sur" and "sûr" or "probable", money(d.net))
  end
  if d.kind == "vendor" then
    return fmt("Acheter %s ×%d pour %s → revendre au MARCHAND %s. Gain sûr : +%s (aucun frais).", d.name, d.qty, money(d.buy),
      money(d.gross), money(d.net))
  end
  local ev = d.ev or {}
  return fmt("Acheter %s ×%d pour %s → revendre à l'HV %s l'unité. Frais : commission %s, dépôts perdus prévus %s. Gain probable : +%s, vendu en ~%s j (%d j de preuves, ~%s ventes/j).",
    d.name, d.qty, money(d.buy), money(d.sellUnit), money(d.cut), money(d.depLoss), money(d.net), dec(d.days), n0(ev.days),
    dec(ev.sold))
end
local HOW = { undercut = "juste sous le moins cher", match = "au prix de ta vente en cours", normal = "au prix habituel (personne d'autre)" }
function M.PostText(e)
  if not e then return nil end
  return fmt("Poster %s ×%d à %s l'unité (%s). Plancher %s, dépôt %s%s. Si vendu : +%s%s.", e.name, e.qty, money(e.unit),
    HOW[e.how] or "", money(e.floor), money(e.dep), e.depExact and "" or " (estimé)", money(e.gain),
    e.lot and " sur ton achat" or " de plus qu'au marchand")
end
function M.GoalLines()
  local r = M.Realized()
  local goal = max(0, n0(cfg("goal", 50))) * 10000
  local g = X.goal(r.today, goal, (M.ahOpen and M.built) and M.planNet or nil)
  local l1 = fmt("Aujourd'hui : %s%s sur %s (%d %%)", r.today >= 0 and "+" or "", money(r.today), money(goal), g.pct)
  local ph = M.PerHour()
  if ph then l1 = l1 .. fmt(" · %s/h à l'HV", money(ph)) end
  local l2
  -- seulement devant le marché (HV ouvert, files calculées) : HV fermé, on ne dit rien d'un marché qu'on ne voit plus
  if g.left > 0 and M.ahOpen and M.built and not M.build and g.reachable == false then
    if n0(M.visibleNet) >= g.left then
      l2 = fmt("Objectif non atteignable avec %s de capital libre : au mieux +%s maintenant.", money(M.budget), money(M.planNet))
    else
      l2 = fmt("Objectif non atteignable sur ce marché pour l'instant : affaires visibles +%s.", money(M.visibleNet))
    end
  end
  return l1, l2, g
end
function M.CapitalLine()
  local budget, g, tied, reserve = M.Budget()
  return fmt("Or %s · libre pour l'HV %s · immobilisé %s%s", money(g), money(budget), money(tied),
    reserve > 0 and (" · réserve " .. money(reserve)) or "")
end
function M.Summary()
  local b, p, w = M.Counts()
  local r = M.Realized()
  local budget, g, tied = M.Budget()
  return { buy = b, post = p, wait = w, today = r.today, week = r.week, total = r.total, goal = max(0, n0(cfg("goal", 50))) * 10000,
    tied = tied, free = budget, gold = g, perHour = M.PerHour(), planNet = M.planNet, open = M.ahOpen and true or false }
end

-- page « HV » de la fenêtre TreeAI (rendue par Auction.RenderPage) : l'objectif en tête, puis la file d'achat
function M.PageHead(Ly)
  if type(Ly) ~= "table" or type(Ly.header) ~= "function" or type(Ly.text) ~= "function" then return end
  if not Ly.header("Achat-revente : ton objectif du jour", "hv:desk") then return end
  local l1, l2 = M.GoalLines()
  Ly.text(l1)
  if l2 then Ly.text(l2, "S", "warn") end
  local r = M.Realized()
  Ly.text(fmt("7 jours : %s%s · %s", r.week >= 0 and "+" or "", money(r.week), M.CapitalLine()), "S", "muted")
  if not M.ahOpen then
    Ly.text("À l'HV, le panneau « Achat-revente » se colle à la fenêtre : Acheter · Poster · Action suivante (un clic = une action).", "S", "muted")
  end
end
function M.PageDeals(Ly)
  if type(Ly) ~= "table" or type(Ly.header) ~= "function" then return end
  local b = M.Counts()
  if not Ly.header("Acheter pour revendre", "hv:flips", b) then return end
  if b == 0 and Ly.text then
    Ly.text(M.built and "Aucune affaire sûre ou prouvée pour l'instant : je ne propose rien sans preuve."
      or "Ouvre l'HV et clique Scanner : les affaires sûres (revente au marchand) arrivent tout de suite, les autres après 3 jours de scans.", "S", "muted")
  end
  local n = 0
  for _, d in ipairs(M.buyQ) do
    if (not d.state or d.state == "confirm") and Ly.row then
      n = n + 1
      if n > 8 then break end
      Ly.row({ text = M.DealText(d), prio = d.class == "sur" and 1 or 2,
        tooltip = { fmt("Capital immobilisé : %s.", money(d.capital)), "À l'HV : bouton « Acheter » du panneau (l'annonce est relue avant l'achat)." } })
    end
  end
end

-- ===================================================================== panneau collé à l'HV (ancré, jamais parenté)
local DOCK_W = 316
function M.Docked() return M._active == true and cfg("dock", true) and auc() ~= nil end
local function keyName()
  local A = T.modules and T.modules.Auction
  local k = type(A) == "table" and A.RafaleKey and try(A.RafaleKey)
  return type(k) == "string" and k or nil
end
function M.CreateDock()
  if M.frame or type(G.CreateFrame) ~= "function" then return M.frame end
  local Th = T.Theme
  if not (Th and Th.Frame and Th.Button and Th.Text) then return nil end
  local f = Th:Frame(G.UIParent, "card", DOCK_W, 320)
  if not f then return nil end
  M.frame = f
  f:SetFrameStrata("HIGH")
  f:EnableMouse(true)
  local pad, w = 8, DOCK_W - 16
  local function text(size, token, y, h)
    local fs = Th:Text(f, size, token)
    fs:SetPoint("TOPLEFT", f, "TOPLEFT", pad, y)
    fs:SetWidth(w)
    if fs.SetJustifyH then fs:SetJustifyH("LEFT") end
    if fs.SetJustifyV then fs:SetJustifyV("TOP") end
    if h and fs.SetHeight then fs:SetHeight(h) end
    if fs.SetWordWrap then fs:SetWordWrap(h and h > 14 or false) end
    return fs
  end
  f.title = text("M", "gold", -8)
  f.title:SetText("Achat-revente")
  f.goal = text("S", "text", -28)
  f.bar = Th.ProgressBar and Th:ProgressBar(f, w, 8) or nil
  if f.bar then f.bar:SetPoint("TOPLEFT", f, "TOPLEFT", pad, -44) end
  f.goal2 = text("S", "warn", -56, 24)
  f.capital = text("S", "muted", -82)
  local function act(fn) return function() T:UserAction(fn) M.Refresh() end end
  local bw = floor((w - 4) / 2)
  f.buy = Th:Button(f, "Acheter", bw, 40, act(M.BuyClick), { "Un clic = un achat (la meilleure affaire d'abord).", "L'annonce est relue en direct avant l'achat." })
  f.buy:SetPoint("TOPLEFT", f, "TOPLEFT", pad, -100)
  f.post = Th:Button(f, "Poster", bw, 40, act(M.PostClick), { "Un clic = une pile mise en vente.", "Jamais sous ton plancher (coût + frais + marge)." })
  f.post:SetPoint("TOPLEFT", f.buy, "TOPRIGHT", 4, 0)
  f.next = Th:Button(f, "Action suivante", w, 32, act(M.Next), { "Fait la prochaine action : acheter, puis poster.", "Une action par clic ou par appui de touche." })
  f.next:SetPoint("TOPLEFT", f, "TOPLEFT", pad, -144)
  f.deal = text("S", "text", -182, 84)
  f.status = text("S", "muted", -268, 24)
  f.scan = Th:Button(f, "Scanner", bw, 20, act(M.ScanClick), { "Relit les prix de l'hôtel des ventes.", "Scan complet : 1 fois / 15 min (règle du jeu) ; sinon tes objets seulement." })
  f.scan:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", pad, 6)
  f.bind = Th:Button(f, "Lier une touche", bw, 20, function()
    local A = T.modules and T.modules.Auction
    if type(A) == "table" and A.BindRafale then T:UserAction(A.BindRafale) say(A.msg or "") end
    M.Refresh()
  end, { "Choisit une touche libre pour « Action suivante ».", "Deux clics : proposer, puis confirmer." })
  f.bind:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -pad, 6)
  f:Hide()
  return f
end
function M.Anchor()
  local f = M.frame
  if not f then return end
  f:ClearAllPoints()
  local af = rawget(G, "AuctionHouseFrame") or rawget(G, "AuctionFrame")
  if af then f:SetPoint("TOPLEFT", af, "TOPRIGHT", 4, 0)      -- ancrage seulement : la fenêtre du jeu n'est ni bougée ni cachée
  else f:SetPoint("LEFT", G.UIParent, "CENTER", 200, 0) end
end
function M.Lines()
  local l1, l2 = M.GoalLines()
  local b, p, w = M.Counts()
  local d, e = M.BuyHead(), M.PostHead()
  local cur
  if M.pend then cur = "Le jeu répond…"
  elseif d then cur = M.DealText(d)
  elseif e then cur = M.PostText(e)
  elseif M.build or M.pb then cur = "Calcul des affaires…"
  elseif not M.built then cur = "Clique « Scanner » : je lis les prix, puis je te propose les affaires sûres."
  elseif w > 0 then cur = fmt("Rien à faire maintenant. En attente : %s (%s).", M.waitQ[1].name, M.waitQ[1].why or "")
  else cur = "Rien à faire maintenant : aucune affaire sûre ou prouvée, rien à poster." end
  local k = keyName()
  return { title = "Achat-revente", goal = l1, goal2 = l2 or "", capital = M.CapitalLine(),
    buy = fmt("Acheter (%d)", b), post = fmt("Poster (%d)", p),
    next = k and fmt("Action suivante [%s]", k) or "Action suivante", deal = cur, status = M.status or "" }
end
function M.Refresh()
  local f = M.frame
  if not (f and M.ahOpen and f:IsShown()) then return end
  local L = M.Lines()
  f.goal:SetText(L.goal)
  f.goal2:SetText(L.goal2)
  f.capital:SetText(L.capital)
  f.buy:SetLabel(L.buy)
  f.post:SetLabel(L.post)
  f.next:SetLabel(L.next)
  f.deal:SetText(L.deal)
  f.status:SetText(L.status)
  if f.bar then
    local _, _, g = M.GoalLines()
    f.bar:SetProgress(g.pct, 100, nil, "")
  end
  if f.bind then if keyName() then f.bind:Hide() else f.bind:Show() end end
end

-- ===================================================================== cycle de vie
function M.AUCTION_HOUSE_SHOW()
  M.ahOpen, M.openT = true, now()
  if not auc() then return end
  if cfg("dock", true) then
    local f = M.CreateDock()
    if f then M.Anchor() f:Show() T:After(0, M.Anchor) end
  end
  local A = auc()
  if next(A.cur) ~= nil then M.Build() else M.BuildPosts() end
  M.Refresh()
end
function M.AUCTION_HOUSE_CLOSED()
  local ch = char()
  if M.ahOpen and M.openT and ch then
    local k = dayKey()
    ch.ahTime[k] = n0(ch.ahTime[k]) + max(0, now() - M.openT)
  end
  M.ahOpen, M.openT, M.pend, M.search, M.build, M.pb = false, nil, nil, nil, nil, nil
  for _, d in ipairs(M.all) do if d.state == "confirm" then d.state = nil end d.liveT = nil end
  M.postQ, M.waitQ, M.sessPosted = {}, {}, {}
  if M.frame then M.frame:Hide() end
end
function M:OnInit()
  local ch = M.char
  if ch then
    for _, k in ipairs({ "ev", "open", "posts", "names", "seen", "ahTime" }) do if type(ch[k]) ~= "table" then ch[k] = {} end end
    local old = dayKey(now() - 30 * 86400)
    for k in pairs(ch.ahTime) do if k < old then ch.ahTime[k] = nil end end
  end
end
function M:OnEnable()
  for _, ev in ipairs({ "AUCTION_HOUSE_SHOW", "AUCTION_HOUSE_CLOSED", "MAIL_INBOX_UPDATE", "MERCHANT_SHOW", "MERCHANT_CLOSED",
    "COMMODITY_PRICE_UPDATED", "COMMODITY_PRICE_UNAVAILABLE", "PLAYER_MONEY" }) do
    self:RegisterEvent(ev)
  end
  self:RegisterEvent("AUCTION_HOUSE_THROTTLED_SYSTEM_READY", function() if M.ahOpen then M.Prefetch() end end)
  self:RegisterEvent("AUCTION_HOUSE_PURCHASE_COMPLETED", bought("purchaseCompleted"))
  self:RegisterEvent("COMMODITY_PURCHASE_SUCCEEDED", bought("commoditySucceeded"))
  self:RegisterEvent("COMMODITY_PURCHASE_FAILED", failed("commodityFailed", "buy"))
  self:RegisterEvent("AUCTION_HOUSE_SHOW_ERROR", failed("showError"))
  self:RegisterEvent("AUCTION_HOUSE_POST_ERROR", failed("postError", "post"))
  self:RegisterEvent("AUCTION_HOUSE_POST_WARNING", function()
    diag("postWarning")
    local p = M.pend
    if p and p.kind == "post" then M.Resolve(false, "le jeu demande une confirmation : poste cet objet à la main") end
  end)
  self:RegisterEvent("AUCTION_HOUSE_AUCTION_CREATED", function()
    diag("auctionCreated")
    local p = M.pend
    if p and p.kind == "post" then M.Resolve(true) end
  end)
  if T.OnBags then
    T:OnBags(M, function()
      if not M.ahOpen then return end
      local p = M.pend
      if p and p.kind == "post" then M.Resolve(nil) end
      if M.pb then M.postsDirty = true else M.BuildPosts() end
    end)
  end
  -- gardien des sacs : ce que TU as acheté pour revendre est « en trop » pour toi (les autres gardiens gardent la main)
  if T.Bags and T.Bags.RegisterKeeper then
    pcall(T.Bags.RegisterKeeper, "ahdesk", function(id)
      local ch = M.char
      local o = M._active and ch and ch.open[id]
      if o and n0(o.n) > 0 then return 0, "acheté pour revendre" end
      return nil
    end)
  end
  self:On("SUMMARY", function()
    local s = T.Summary and T:Summary()
    if type(s) == "table" then s.ahdesk = M.Summary() end
  end)
end
function M:OnDisable()
  if M.frame then M.frame:Hide() end
  M.ahOpen, M.pend, M.search, M.build, M.pb = false, nil, nil, nil, nil
  if T.Bags and T.Bags.UnregisterKeeper then pcall(T.Bags.UnregisterKeeper, "ahdesk") end
end

-- touche « TreeAI : action HV suivante » (Bindings.xml) : le bureau quand il est là, sinon l'ancien chemin
do
  local prev = T.AuctionNext
  T.AuctionNext = function()
    if M.Docked() and M.ahOpen then T:UserAction(M.Next) M.Refresh() return end
    if prev then return prev() end
  end
end
