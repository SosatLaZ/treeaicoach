-- TreeAI — « Montée de métier » + mode focus « Métier » (agent PROFLEVEL, 4.0.2). Extension du module Prof (même
-- module : mêmes réglages, même coût mesuré, coupé avec lui ; rien ne tourne tant que la page « Métiers » est fermée
-- et qu'aucune session n'est lancée).
--  * PLAN (Prof.Engine.Session = treeaicoach/wow/professions.py) : de ta compétence à la cible, recette × nombre,
--    couleur, chance (orange 100 %, jaune / vert (gris − compétence) / (gris − jaune)), coût, durée ; deux modes :
--    « Complet » (atteindre la cible : ce qui manque s'achète au marchand au prix exact, à l'HV au prix TreeAI /
--    Auctionator, sinon « à farmer / demander ») et « Pile-poil » (seulement tes compos + celles d'un marchand : le
--    plus de points pour vider tes sacs). Stock = sacs + BANQUE (relevée à chaque passage, « banque vue il y a 2 h »)
--    + courrier (relevé à la boîte aux lettres) − ce que tes quêtes demandent (« garde 10 Cuir moyen pour « X » »).
--    Recettes connues lues dans la fenêtre du métier (couleurs du jeu reprises : gris exact, jaune corrigé) ; exigence
--    des recettes d'entraîneur lue chez lui (sinon le seuil jaune : jamais trop tôt) ; rang suivant au plafond.
--  * SESSION MÉTIER (mode focus, un clic) : le trajet de quêtes en pause (ZonePlan/Route : rendu à la sortie), la
--    carte « Prochaine étape » montre les étapes de la session (contrat T.focus ci-dessous), la boussole va au PNJ de
--    l'étape (marchands / maîtres vus + QuestieDB, banque / HV / boîte aux lettres de Detect). Chez le marchand, à la
--    banque, à l'HV et dans la fenêtre du métier : une petite carte sous la fenêtre du jeu, UN bouton = UNE action du
--    jeu par clic (BuyMerchantItem, UseContainerItem d'une case de banque, C_TradeSkillUI.CraftRecipe(recette, N),
--    recherche / achat / confirmation HV) — jamais en combat, jamais sans clic. Avance seule : chaque étape disparaît
--    quand ce qu'elle demande est dans tes sacs (ou ta compétence atteinte).
--  * CONTRAT « focus » (lu par Brain, ZonePlan, la carte, la boussole) : T.focus = nil | { kind = "prof", owner =
--    "Prof", label = "Session métier", line, since = GetTime(), gen (change à chaque nouvelle étape), pausedRoute,
--    card = function(n) → rows, titre, résumé } ; T:FocusMode() → kind | nil ; message interne « FOCUS_CHANGED »
--    (kind | nil). Pendant un focus : ne pas reprendre le trajet, ne pas insérer de courses, ne pas changer de zone.
-- Lua 5.1, aucune globale, rien dans le chat.
local T = _G.TreeAI
local M = T and T.Prof
if not (M and type(M.Engine) == "table" and T.RegisterModule) then return end
local E = M.Engine
local API = T.API or {}
local G = _G
local floor, max, min, ceil = math.floor, math.max, math.min, math.ceil
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function pub(v) if secret(v) then return nil end return v end
local function num(v) v = pub(v) return tonumber(v) end
local function str(v) v = pub(v) return type(v) == "string" and v ~= "" and v or nil end
local function now() return try(g("GetTime")) or 0 end
local function epoch() local f = g("time") return (f and try(f)) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function D() return T.Data and T.Data.Recipes end
local function cut(s, n) return (T.Cut and T.Cut(s, n)) or tostring(s or "") end
local function money(c) return M.Money(c) end
local function fold(s) return M.Fold(s) end
local function say(msg) if T.ClickNote then T:ClickNote(msg) end end
local function refuse(msg) if T.CombatRefused then T:CombatRefused(msg) end end

local PL = {}
M.Level = PL
T.ProfLevel = PL
PL.E = E

-- état du perso (M.char.pl) : bank = {t, items}, mail = {t, items, n}, focus = {line, mode, since, paused}, cal,
-- line (métier choisi sur la page), ah = {[id] = true|false} (achats HV acceptés / refusés)
local function S()
  local c = M.char
  if type(c) ~= "table" then return {} end
  if type(c.pl) ~= "table" then c.pl = {} end
  return c.pl
end
-- compte (M.db) : vend[mapID][nom] = {x, y, t, i = {[id] = prix}} ; treq[ligne][nom] = {r, c} ; trn[mapID][nom] =
-- {x, y, l, t}
local function A(k)
  local d = M.db
  if type(d) ~= "table" then return {} end
  if type(d[k]) ~= "table" then d[k] = {} end
  return d[k]
end

local function matName(id)
  local d = D()
  local v = d and d.mats[id]
  return (v and v[1]) or (API.ItemName and API.ItemName(id)) or ("objet " .. tostring(id))
end
PL.MatName = matName
local function lineName(line) local d = D() local v = d and d.lines[line] return v and v[2] or ("Métier " .. tostring(line)) end
local function plural(n, one, many) return n > 1 and (many or (one .. "s")) or one end
local function ago(t)
  local s = epoch() - (tonumber(t) or 0)
  if s < 90 then return "à l'instant" end
  if s < 3600 then return fmt("il y a %d min", floor(s / 60 + 0.5)) end
  if s < 86400 then return fmt("il y a %d h", floor(s / 3600 + 0.5)) end
  return fmt("il y a %d j", floor(s / 86400 + 0.5))
end
PL.Ago = ago
local function minutes(sec)
  sec = tonumber(sec) or 0
  if sec < 60 then return "< 1 min" end
  local m = floor(sec / 60 + 0.5)
  if m < 60 then return fmt("%d min", m) end
  return fmt("%d h %02d", floor(m / 60), m % 60)
end
PL.Minutes = minutes

-- versions : un changement invalide le plan en cache
local ver = { bags = 0, bank = 0, known = 0, prices = 0, focus = 0 }
PL.ver = ver
function PL.Dirty(what)
  if what then ver[what] = (ver[what] or 0) + 1 end
  if PL.focus or (PL.PageShown and PL.PageShown()) then PL.Soon() end
end

-- ===================================================================== inventaire : sacs, banque, courrier
local function bagIndex() local En = g("Enum") return type(En) == "table" and type(En.BagIndex) == "table" and En.BagIndex or nil end
--- conteneurs de la banque : Forever = onglets du perso (Enum.BagIndex.CharacterBankTab_1..9, achetés d'abord) ;
--- Era = BANK_CONTAINER (−1) + sacs de banque NUM_BAG_SLOTS + 1 … + NUM_BANKBAGSLOTS
function PL.BankBags()
  local out, BI = {}, bagIndex()
  if BI and BI.CharacterBankTab_1 then
    local CB, En = g("C_Bank"), g("Enum")
    local bt = En and En.BankType and En.BankType.Character or 0
    local ids = CB and CB.FetchPurchasedBankTabIDs and try(CB.FetchPurchasedBankTabIDs, bt)
    if type(ids) == "table" and #ids > 0 then
      for _, id in ipairs(ids) do if num(id) then out[#out + 1] = num(id) end end
      return out
    end
    for i = 1, 9 do local id = num(BI["CharacterBankTab_" .. i]) if id then out[#out + 1] = id end end
    return out
  end
  out[1] = num(g("BANK_CONTAINER")) or -1
  local nb = num(g("NUM_BAG_SLOTS")) or 4
  for i = 1, num(g("NUM_BANKBAGSLOTS")) or 6 do out[#out + 1] = nb + i end
  return out
end
local function numSlots(bag)
  local CC = g("C_Container")
  local fn = (CC and CC.GetContainerNumSlots) or g("GetContainerNumSlots")
  return num((try(fn, bag))) or 0
end
local function slotItem(bag, slot)
  local CC = g("C_Container")
  if CC and CC.GetContainerItemInfo then
    local it = try(CC.GetContainerItemInfo, bag, slot)
    if type(it) == "table" then return num(it.itemID), num(it.stackCount) or 1 end
    return nil
  end
  local _, count, _, _, _, _, link, _, _, id = try(g("GetContainerItemInfo"), bag, slot)
  id = num(id) or (str(link) and tonumber(str(link):match("item:(%d+)")))
  return id, num(count) or 1
end
--- relevé de la banque (ouverte) → char.pl.bank = { t, items } + cases par objet (surbrillance, retrait)
function PL.ScanBank()
  local items, slots, total = {}, {}, 0
  for _, bag in ipairs(PL.BankBags()) do
    local n = numSlots(bag)
    total = total + n
    for slot = 1, min(n, 240) do
      local id, c = slotItem(bag, slot)
      if id then
        items[id] = (items[id] or 0) + c
        local l = slots[id] or {}
        l[#l + 1] = { bag = bag, slot = slot, n = c }
        slots[id] = l
      end
    end
  end
  if total == 0 then return nil end           -- pas (encore) de données de banque : l'ancien relevé reste
  S().bank = { t = epoch(), items = items }
  PL.bankSlots = slots
  PL.Dirty("bank")
  return items
end
--- relevé du courrier (boîte ouverte) : pièces jointes par objet → char.pl.mail
function PL.ScanMail()
  local n = num((try(g("GetInboxNumItems")))) or 0
  local items = {}
  local maxA = num(g("ATTACHMENTS_MAX_RECEIVE")) or 16
  for i = 1, min(n, 50) do
    local hdr = { try(g("GetInboxHeaderInfo"), i) }
    local has = num(hdr[8])
    if has == nil or has > 0 then
      for a = 1, maxA do
        local _, id, _, count = try(g("GetInboxItem"), i, a)
        id = num(id)
        if id then items[id] = (items[id] or 0) + (num(count) or 1) end
      end
    end
  end
  S().mail = { t = epoch(), items = items, n = n }
  PL.Dirty("bank")
  return items
end
local function bagCount(id) return num(API.ItemCount and API.ItemCount(id, false)) or 0 end
PL.BagCount = bagCount
--- { bags, bank, mail } pour ces objets (ids = {[id] = true}) ; banque = dernier relevé, sinon le compte du jeu
function PL.Have(ids)
  local st = S()
  local bank = type(st.bank) == "table" and st.bank.items or nil
  local mail = type(st.mail) == "table" and st.mail.items or nil
  local h = { bags = {}, bank = {}, mail = {} }
  for id in pairs(ids) do
    local b = bagCount(id)
    if b > 0 then h.bags[id] = b end
    local k
    if bank then k = bank[id] or 0
    else k = max(0, (num(API.ItemCount and API.ItemCount(id, true)) or 0) - b) end
    if k > 0 then h.bank[id] = k end
    local m = mail and mail[id] or 0
    if m > 0 then h.mail[id] = m end
  end
  return h
end

-- ===================================================================== réserve de quête
local byNameCache
local function matsByName()
  if byNameCache then return byNameCache end
  byNameCache = {}
  local d = D()
  for id, v in pairs(d and d.mats or {}) do byNameCache[fold(v[1])] = id end
  return byNameCache
end
--- objets demandés par les quêtes du journal (objectifs « objet », finies comprises : il faut les rendre)
--- → { [id] = n }, { [id] = titre de la quête }
function PL.Reserve()
  local Q = T.modules and T.modules.Quests
  local list = Q and Q.lastList
  local out, why = {}, {}
  if type(list) ~= "table" then return out, why end
  local byName = matsByName()
  for _, q in ipairs(list) do
    for _, o in ipairs(type(q) == "table" and type(q.obj) == "table" and q.obj or {}) do
      if type(o) == "table" and o.t == "i" and type(o.name) == "string" then
        local id = byName[fold(o.name)]
        local need = tonumber(o.need) or 0
        if id and need > 0 then
          out[id] = (out[id] or 0) + need
          why[id] = why[id] or q.title
        end
      end
    end
  end
  return out, why
end

-- ===================================================================== prix
local priceCache, priceAt = {}, -100
--- prix unitaire d'une compo : marchand (exact), sinon HV (TreeAI, confiance ≥ 30 %) ou Auctionator ; jamais deviné
--- → prix, source ("vendeur" | "HV" | "Auctionator"), confiance
function PL.PriceOf(id)
  if now() - priceAt > 60 then priceCache, priceAt = {}, now() end
  local c = priceCache[id]
  if c then return c[1], c[2], c[3] end
  local d = D()
  local v = d and d.mats[id]
  local p, src, conf
  if v and v[3] == 1 and (v[2] or 0) > 0 then p, src, conf = v[2], "vendeur", 1 end
  if not p then
    local Au = T.modules and T.modules.Auction
    if type(Au) == "table" and type(Au.Stats) == "function" then
      local ok, s = pcall(Au.Stats, id)
      if ok and type(s) == "table" and tonumber(s.market) and (tonumber(s.conf) or 0) >= 0.3 then
        p, src, conf = floor(s.market), s.src == "Auctionator" and "Auctionator" or "HV", tonumber(s.conf)
      end
    end
  end
  if not p then
    local atr = g("Auctionator")
    local api = type(atr) == "table" and type(atr.API) == "table" and atr.API.v1
    local a = type(api) == "table" and try(api.GetAuctionPriceByItemID, "TreeAI", id)
    if type(a) == "number" and a > 0 then p, src, conf = floor(a), "Auctionator", 0.3 end
  end
  priceCache[id] = { p, src, conf }
  return p, src, conf
end
--- « se vend bien » / « ≈ 12 vendus par jour » / « peu vu à l'HV » (historique HV de TreeAI) ou nil
function PL.LiqText(id)
  local Au = T.modules and T.modules.Auction
  if not (type(Au) == "table" and type(Au.Liq) == "function") then return nil end
  local ok, l = pcall(Au.Liq, id)
  if not ok or type(l) ~= "table" then return nil end
  if l.ok then return "se vend bien" end
  local sold = tonumber(l.sold)
  if sold and sold >= 1 then return fmt("≈ %d vendus par jour", floor(sold + 0.5)) end
  if (tonumber(l.days) or 0) > 0 then return "peu vu à l'HV" end
  return nil
end

-- ===================================================================== recettes : connues, exigences, couleurs du jeu
local function profOf(line)
  for _, p in ipairs(M.profs or {}) do if p.line == line then return p end end
  return nil
end
PL.ProfOf = profOf
--- recettes connues (fenêtre du métier lue) ou nil (jamais lue : on ne sait pas)
function PL.Known(line)
  local k = M.char and M.char.known and M.char.known[line]
  if type(k) == "table" and next(k) then return k end
  return nil
end
--- exigences lues chez l'entraîneur : { [sort] = compétence } (noms → sorts de la ligne)
local learnCache = {}
function PL.LearnAt(line)
  local req = A("treq")[line]
  if type(req) ~= "table" or not next(req) then return nil end
  local key = ver.known
  local c = learnCache[line]
  if c and c.k == key then return c.v end
  local d = D()
  local out = {}
  for _, r in ipairs(d and d.r[line] or {}) do
    local e = req[fold(r[9])]
    if type(e) == "table" and tonumber(e.r) then out[r[1]] = tonumber(e.r) end
  end
  learnCache[line] = { k = key, v = out }
  return out
end
--- couleurs vues dans la fenêtre du métier (Prof.ScanKnown : maxTrivialLevel, relativeDifficulty à ta compétence)
--- → lignes corrigées : gris exact, seuil jaune ramené à ce que le jeu montre
local rowsCache = {}
function PL.Rows(line)
  local d = D()
  local rows = d and d.r[line]
  if not rows then return {} end
  local cal = M.char and type(M.char.cal) == "table" and M.char.cal[line]
  if type(cal) ~= "table" or not next(cal) then return rows end
  local c = rowsCache[line]
  if c and c.k == ver.known then return c.v end
  local out = {}
  for i, r in ipairs(rows) do
    local o = cal[r[1]]
    if type(o) == "table" then
      local y, gr = r[5], r[6]
      local gx = tonumber(o.g)
      if gx and gx > 0 and gx <= 450 then gr = gx end
      local s, dif = tonumber(o.s), tonumber(o.d)
      if s and dif then
        if dif == 0 and y <= s then y = s + 1                  -- encore orange à s : le jaune est plus haut
        elseif (dif == 1 or dif == 2) and y > s then y = s     -- déjà jaune / vert à s
        elseif dif == 3 and gr > s then gr = s end            -- déjà grise à s
      end
      if y ~= r[5] or gr ~= r[6] then
        local n = {}
        for k = 1, 11 do n[k] = r[k] end
        n[5], n[6] = y, gr
        r = n
      end
    end
    out[i] = r
  end
  rowsCache[line] = { k = ver.known, v = out }
  return out
end

-- ===================================================================== plan (mis en cache : 6 derniers, clé = tout ce qui compte)
PL.cache, PL.cacheOrder = {}, {}
local function castSec(spell)
  local CS = g("C_Spell")
  local info = CS and CS.GetSpellInfo and try(CS.GetSpellInfo, spell)
  local ms = type(info) == "table" and num(info.castTime) or nil
  if ms and ms > 0 then return ms / 1000 + 0.4 end
  return E.CRAFT_SEC
end
--- cible par défaut : Complet = la cible du métier (palier Legacy / fin du rang, ou celle choisie) ; Pile-poil = 300
function PL.Target(line, mode)
  local pr = profOf(line)
  if not pr then return 0 end
  if mode == "exact" then return 300 end
  return M.TargetFor(pr)
end
function PL.Mode() local m = M.db and M.db.plMode return m == "exact" and "exact" or "full" end
--- le plan d'un métier : { line, name, mode, target, skill, cap, res (E.Session), split (E.Split), have, reserve,
--- why, unit = {[id] = {prix, source, conf}}, known (liste lue ?), time, cost, visits }
function PL.Compute(line, mode, target)
  local d = D()
  local pr = profOf(line)
  if not (d and pr and d.r[line]) then return nil end
  mode = mode == "exact" and "exact" or "full"
  target = tonumber(target) or PL.Target(line, mode)
  local known = PL.Known(line)
  local learnAt = PL.LearnAt(line)
  local level = num((try(g("UnitLevel"), "player"))) or 1
  -- recettes utiles d'ici à la cible (le reste ne peut pas servir : moins de travail par fabrication)
  local rows, ids = {}, {}
  local hi = max(target, pr.max)
  for _, r in ipairs(PL.Rows(line)) do
    local okKnown = (known and known[r[1]]) or ((r[11] or 0) % 2 == 1)
    local need = (learnAt and learnAt[r[1]]) or r[4] or 1
    if r[6] > pr.rank and (okKnown or ((r[8] or 0) == 0 and need <= hi)) then
      rows[#rows + 1] = r
      local m = r[10] or {}
      for k = 1, #m - 1, 2 do ids[m[k]] = true end
    end
  end
  -- intermédiaires : une compo que ce métier sait faire maintenant (Peau moyenne traitée, Gants élégants…) ; ses compos
  -- comptent aussi (stock, prix)
  local makers = {}
  for _, r in ipairs(PL.Rows(line)) do
    local out = r[2]
    if out and out > 0 and ids[out] and not makers[out] and E.Usable(r, pr.rank, known, learnAt) then
      makers[out] = r
    end
  end
  for _, r in pairs(makers) do
    local m = r[10] or {}
    for k = 1, #m - 1, 2 do ids[m[k]] = true end
  end
  local have = PL.Have(ids)
  local reserve, why = PL.Reserve()
  local stock, vendor, vinfo, price, value, unit = {}, {}, {}, {}, {}, {}
  local sig = { line, mode, target, pr.rank, pr.max, level, ver.known, ver.prices, M.db and M.db.overtime or 0 }
  local idl = {}
  for id in pairs(ids) do idl[#idl + 1] = id end
  tsort(idl)
  for _, id in ipairs(idl) do
    local n = (have.bags[id] or 0) + (have.bank[id] or 0) + (have.mail[id] or 0) - (reserve[id] or 0)
    if n > 0 then stock[id] = n end
    sig[#sig + 1] = id .. ":" .. (have.bags[id] or 0) .. ":" .. (have.bank[id] or 0) .. ":" .. (have.mail[id] or 0)
      .. ":" .. (reserve[id] or 0)
    local p, src, conf = PL.PriceOf(id)
    unit[id] = { p, src, conf }
    local v = d.mats[id]
    if src == "vendeur" then vendor[id] = p vinfo[id] = { p, (v and v[5]) or 1 }
    elseif p then price[id] = p end
    value[id] = p or (v and v[4] and v[4] > 0 and v[4]) or 1
  end
  local key = tconcat(sig, "|")
  local hit = PL.cache[key]
  if hit then return hit end
  -- prix d'un intermédiaire sans prix du marchand / de l'HV : ce que coûtent ses compos (marchand, HV ; une compo que
  -- tu as et que rien ne prix : sa valeur de revente) ; une compo sans aucun prix = pas de prix (« à farmer »). Calculé
  -- sur les prix de base seulement (jamais en chaîne : l'ordre des recettes ne change rien)
  local made = {}
  for out, r in pairs(makers) do
    if not vendor[out] and not price[out] then
      local c, m = 0, r[10] or {}
      for k = 1, #m - 1, 2 do
        local mid = m[k]
        local u = vendor[mid] or price[mid] or ((stock[mid] or 0) > 0 and value[mid]) or nil
        if not u then c = nil break end
        c = c + m[k + 1] * u
      end
      if c then made[out] = ceil(c / max(1, r[3] or 1)) end
    end
  end
  for out, p in pairs(made) do price[out] = p unit[out] = { p, "fabriqué", 1 } end
  local res = E.Session({ rows = rows, skill = pr.rank, target = target, cap = pr.max, level = level, mode = mode,
    stock = stock, known = known, learnAt = learnAt, vendor = vendor, price = price, value = value,
    overtime = M.db and M.db.overtime or 0 })
  -- intermédiaires à faire soi-même : « Crée d'abord N× X » (ses compos : tes stocks d'abord, puis achats)
  local prep, use2, buy2, unk2 = {}, {}, {}, {}
  for id, n in pairs(res.use) do use2[id] = n end
  for id, n in pairs(res.buy) do buy2[id] = n end
  for id, n in pairs(res.unknown) do unk2[id] = n end
  local outs = {}
  for id in pairs(res.buy) do if makers[id] and unit[id] and unit[id][2] == "fabriqué" then outs[#outs + 1] = id end end
  tsort(outs)
  local function bought(mid) return vendor[mid] or (price[mid] and not made[mid]) end   -- s'achète vraiment
  for _, id in ipairs(outs) do
    local r = makers[id]
    local mk = ceil(res.buy[id] / max(1, r[3] or 1))
    local learn = (known and not known[r[1]] and (r[11] or 0) % 2 ~= 1) and "trainer" or nil
    prep[#prep + 1] = { kind = "craft", spell = r[1], name = r[9], from = pr.rank, to = pr.rank, crafts = mk,
      color = E.Color2(r, pr.rank), colorTo = E.Color2(r, pr.rank), row = r, prep = true, makes = id, learn = learn }
    buy2[id], unk2[id] = nil, nil
    local m = r[10] or {}
    for k = 1, #m - 1, 2 do
      local mid, need = m[k], m[k + 1] * mk
      local left = max(0, (stock[mid] or 0) - (use2[mid] or 0))
      local u = min(left, need)
      if u > 0 then use2[mid] = (use2[mid] or 0) + u end
      local miss = need - u
      if miss > 0 then
        buy2[mid] = (buy2[mid] or 0) + miss
        if not bought(mid) then unk2[mid] = (unk2[mid] or 0) + miss end
      end
    end
  end
  -- ce qui reste sans vrai prix (un intermédiaire de 2e niveau…) : à farmer, jamais « à l'HV »
  for id, n in pairs(buy2) do if not bought(id) then unk2[id] = n end end
  local split = E.Split({ use = use2, buy = buy2, unknown = unk2, var = res.var }, have, reserve, vinfo, price)
  local t = 0
  for _, x in ipairs(res.steps) do t = t + x.crafts * castSec(x.spell) end
  for _, x in ipairs(prep) do t = t + x.crafts * castSec(x.spell) end
  local visits = 0
  for _, k in ipairs({ "bank", "mail" }) do if next(split[k]) then visits = visits + 1 end end
  if #split.vendor > 0 then visits = visits + 1 end
  if #split.ah > 0 then visits = visits + 1 end
  if #res.learn > 0 or #res.train > 0 then visits = visits + 1 end
  local plan = { line = line, name = lineName(line), mode = mode, target = target, skill = pr.rank, cap = pr.max,
    res = res, split = split, prep = prep, use = use2, have = have, reserve = reserve, why = why, unit = unit, known = known ~= nil,
    time = t + visits * 40, visits = visits, cost = floor(split.vendorCost + split.ahCost), at = now(), level = level }
  PL.cache[key] = plan
  local order = PL.cacheOrder
  order[#order + 1] = key
  while #order > 6 do PL.cache[table.remove(order, 1)] = nil end
  return plan
end

-- ===================================================================== où aller : marchands, maîtres, services
local function me() local m, x, y = T:Here() if m and x then return { mapID = m, x = x, y = y } end return nil end
local function geo() local C = T.Compass return type(C) == "table" and type(C.Geo) == "table" and C.Geo or nil end
local function vec(p, q) local Gm = geo() if Gm and Gm.Vector and p and q then return Gm.Vector(p, q) end return nil end
local function distText(d) local Gm = geo() if d and Gm and Gm.Dist then return Gm.Dist(d) end return nil end
local function faction() return ({ Alliance = "A", Horde = "H" })[str((try(g("UnitFactionGroup"), "player"))) or ""] end
local function friendly(db, npc, fac)
  if not fac or type(db.QueryNPCSingle) ~= "function" then return true end
  local f = try(db.QueryNPCSingle, npc, "friendlyToFaction")
  return not (type(f) == "string" and f ~= "" and not f:find(fac, 1, true))
end
local MAX_MAPS, MAX_PER_MAP = 40, 10
local function remember(tbl, p, name, e)
  local key = tostring(p.mapID)
  local list = tbl[key]
  if type(list) ~= "table" then
    local n = 0
    for _ in pairs(tbl) do n = n + 1 end
    if n >= MAX_MAPS then return nil end
    list = {}
    tbl[key] = list
  end
  if not list[name] then
    local n, oldK, oldT = 0, nil, nil
    for k, v in pairs(list) do
      n = n + 1
      if not oldT or (tonumber(v.t) or 0) < oldT then oldK, oldT = k, tonumber(v.t) or 0 end
    end
    if n >= MAX_PER_MAP and oldK then list[oldK] = nil end
  end
  e.x, e.y, e.t = floor(p.x * 1e4) / 1e4, floor(p.y * 1e4) / 1e4, epoch()
  list[name] = e
  return e
end
local function merchantID(i)
  local id = num((try(g("GetMerchantItemID"), i)))
  if id then return id end
  local link = str((try(g("GetMerchantItemLink"), i)))
  return link and tonumber(link:match("item:(%d+)")) or nil
end
--- (prix d'un lot, taille du lot, lots disponibles ou −1) d'une case du marchand
local function merchantInfo(i)
  local MF = g("C_MerchantFrame")
  local info = MF and MF.GetItemInfo and try(MF.GetItemInfo, i)
  if type(info) == "table" then return num(info.price) or 0, max(1, num(info.stackCount) or 1), num(info.numAvailable) or -1 end
  local _, _, price, lot, avail = try(g("GetMerchantItemInfo"), i)
  return num(price) or 0, max(1, num(lot) or 1), num(avail) or -1
end
--- marchand ouvert : s'il vend des compos de métier, on le retient (carte + objets) pour la boussole
function PL.LearnVendor()
  local p = me()
  local name = str((try(g("UnitName"), "npc")))
  local d = D()
  if not (p and name and d) then return nil end
  local ids = {}
  for i = 1, min(num((try(g("GetMerchantNumItems")))) or 0, 120) do
    local id = merchantID(i)
    local v = id and d.mats[id]
    if v and v[3] == 1 then ids[#ids + 1] = id end
  end
  if #ids == 0 then return nil end
  tsort(ids)
  return remember(A("vend"), p, name, { i = ids })
end
--- maître de métier ouvert : exigences et prix réels des recettes, recettes déjà apprises, sa place
function PL.LearnTrainer()
  if not try(g("IsTradeskillTrainer")) then return nil end
  local d = D()
  local n = num((try(g("GetNumTrainerServices")))) or 0
  if not d or n <= 0 then return nil end
  local line = M.LineOf(str((try(g("GetTrainerServiceSkillLine"), 1))) or "")
  if not line then
    local sk = str((try(g("GetTrainerServiceSkillReq"), 1)))
    line = sk and M.LineOf(sk)
  end
  if not (line and d.lines[line]) then return nil end
  local byName = {}
  for _, r in ipairs(d.r[line] or {}) do byName[fold(r[9])] = r[1] end
  local req = A("treq")
  req[line] = type(req[line]) == "table" and req[line] or {}
  local known = M.char and M.char.known
  local learned = 0
  for i = 1, min(n, 300) do
    local name, _, cat = try(g("GetTrainerServiceInfo"), i)
    name = str(name)
    local spell = name and byName[fold(name)]
    if spell then
      local _, rank = try(g("GetTrainerServiceSkillReq"), i)
      local e = { r = num(rank) or 0, c = num((try(g("GetTrainerServiceCost"), i))) or 0 }
      req[line][fold(name)] = e
      if cat == "used" and type(known) == "table" then
        known[line] = type(known[line]) == "table" and known[line] or {}
        if not known[line][spell] then known[line][spell], learned = 1, learned + 1 end
      end
    end
  end
  local p = me()
  local who = str((try(g("UnitName"), "npc")))
  if p and who then remember(A("trn"), p, who, { l = line }) end
  PL.Dirty("known")
  return line, learned
end
-- QuestieDB (lecture seule) : qui vend ces objets, sur ta carte, de ta faction → candidats {name, mapID, x, y, covers}
local qvCache = {}
function PL.QuestieVendors(ids, p)
  local Cp = T.Compass
  if not (p and #ids > 0 and type(Cp) == "table" and type(Cp.Questie) == "function" and type(Cp.SpawnsOf) == "function") then return {} end
  local key = p.mapID .. ":" .. tconcat(ids, ",")
  local c = qvCache[key]
  if c and now() - c.t < 120 then return c.v end
  local db, zdb = Cp.Questie()
  local out = {}
  if type(db) == "table" and type(db.QueryItemSingle) == "function" then
    local fac = faction()
    local cover, order = {}, {}
    for _, id in ipairs(ids) do
      local vend = try(db.QueryItemSingle, id, "vendors")
      for _, npc in ipairs(type(vend) == "table" and vend or {}) do
        if type(npc) == "number" then
          if not cover[npc] then cover[npc] = 0 order[#order + 1] = npc end
          cover[npc] = cover[npc] + 1
        end
      end
    end
    tsort(order, function(a, b) if cover[a] ~= cover[b] then return cover[a] > cover[b] end return a < b end)
    for i, npc in ipairs(order) do
      if i > 30 then break end
      if friendly(db, npc, fac) then
        local pts = {}
        pcall(Cp.SpawnsOf, "npc", npc, db, zdb, p.mapID, pts)
        for _, pt in ipairs(pts) do
          if pt.mapID == p.mapID then
            out[#out + 1] = { name = pt.name or "Marchand", mapID = pt.mapID, x = pt.x, y = pt.y, covers = cover[npc], src = "Questie" }
          end
        end
      end
    end
  end
  qvCache[key] = { t = now(), v = out }
  return out
end
--- le marchand qui vend le plus de ces objets, puis le plus proche : vu (retenu) d'abord, sinon QuestieDB
function PL.WhereVendor(ids)
  local p = me()
  if not p then return nil end
  local best
  local function consider(c)
    c.dist = vec(p, c)
    if not best or c.covers > best.covers or (c.covers == best.covers and (c.dist or 1e12) < (best.dist or 1e12)) then best = c end
  end
  local list = A("vend")[tostring(p.mapID)]
  for name, e in pairs(type(list) == "table" and list or {}) do
    if type(e) == "table" and tonumber(e.x) and type(e.i) == "table" then
      local set, n = {}, 0
      for _, id in ipairs(e.i) do set[id] = true end
      for _, id in ipairs(ids) do if set[id] then n = n + 1 end end
      if n > 0 then consider({ name = name, mapID = p.mapID, x = e.x, y = e.y, covers = n, src = "vu" }) end
    end
  end
  if best and best.covers >= #ids then return best end
  for _, c in ipairs(PL.QuestieVendors(ids, p)) do consider(c) end
  return best
end
--- le maître de ce métier : vu (retenu) d'abord, sinon Questie (professionTrainers, si ta version le tient)
function PL.WhereTrainer(line)
  local p = me()
  if not p then return nil end
  local best
  local function consider(c)
    c.dist = vec(p, c)
    if not best or (c.dist or 1e12) < (best.dist or 1e12) then best = c end
  end
  local list = A("trn")[tostring(p.mapID)]
  for name, e in pairs(type(list) == "table" and list or {}) do
    if type(e) == "table" and e.l == line and tonumber(e.x) then consider({ name = name, mapID = p.mapID, x = e.x, y = e.y, src = "vu" }) end
  end
  if best then return best end
  local Qe = g("Questie")
  local qdb = type(Qe) == "table" and type(Qe.db) == "table" and Qe.db or nil
  local npcs
  for _, scope in ipairs({ "global", "char", "profile" }) do
    local t = qdb and type(qdb[scope]) == "table" and qdb[scope].professionTrainers
    if type(t) == "table" and type(t[line]) == "table" then npcs = t[line] break end
  end
  local Cp = T.Compass
  if npcs and type(Cp) == "table" and type(Cp.Questie) == "function" and type(Cp.SpawnsOf) == "function" then
    local db, zdb = Cp.Questie()
    if type(db) == "table" then
      local fac = faction()
      for i, npc in ipairs(npcs) do
        if i > 30 then break end
        if type(npc) == "number" and friendly(db, npc, fac) then
          local pts = {}
          pcall(Cp.SpawnsOf, "npc", npc, db, zdb, p.mapID, pts)
          for _, pt in ipairs(pts) do
            if pt.mapID == p.mapID then consider({ name = pt.name or "Maître", mapID = pt.mapID, x = pt.x, y = pt.y, src = "Questie" }) end
          end
        end
      end
    end
  end
  return best
end
--- banque / hôtel des ventes / boîte aux lettres les plus proches (module Detect : PNJ vus + Questie)
function PL.WhereService(kind)
  local Dt = T.Detect
  if not (type(Dt) == "table" and type(Dt.Nearest) == "function") then return nil end
  local e = try(Dt.Nearest, kind)
  if type(e) == "table" and tonumber(e.x) and e.mapID then
    return { name = e.name, mapID = e.mapID, x = e.x, y = e.y, dist = e.dist, src = e.src }
  end
  return nil
end

-- ===================================================================== étapes de la session
local ICON = "Interface\\Icons\\"
local LINE_ICON = { [165] = "Trade_LeatherWorking", [197] = "Trade_Tailoring", [164] = "Trade_BlackSmithing",
  [171] = "Trade_Alchemy", [202] = "Trade_Engineering", [333] = "Trade_Engraving", [185] = "INV_Misc_Food_15",
  [129] = "Spell_Holy_SealOfSacrifice", [186] = "Trade_Mining", [182] = "Trade_Herbalism", [393] = "INV_Misc_Pelt_Wolf_01",
  [356] = "Trade_Fishing" }
function PL.LineIcon(line) return ICON .. (LINE_ICON[line] or "INV_Misc_Book_09") end
local KIND_ICON = { mail = "INV_Letter_15", bank = "INV_Box_02", trainer = "INV_Misc_Book_11", vendor = "INV_Misc_Coin_02",
  ah = "INV_Misc_Coin_17", ask = "INV_Misc_QuestionMark", farm = "INV_Misc_Pelt_Wolf_01" }
-- où crafter (Classic) : la plupart des métiers partout ; Forge = une enclume, Cuisine = un feu
local WHERE_CRAFT = { [164] = "près d'une enclume (forge de ville)", [185] = "près d'un feu (Feu de camp)",
  [186] = "à une forge (fonte)" }
PL.WHERE_CRAFT = WHERE_CRAFT
local function itemsText(list, maxN)
  local parts = {}
  for k, it in ipairs(list) do
    if k > (maxN or 3) then parts[#parts + 1] = "…" break end
    parts[#parts + 1] = fmt("%d %s", it.buy or it.n, matName(it.id))
  end
  return tconcat(parts, ", ")
end
PL.ItemsText = itemsText
local function whereText(w, fallback)
  if not w then return fallback end
  local d = distText(w.dist)
  return cut(w.name or fallback or "", 28) .. (d and (" · " .. d) or "")
end
local function listOf(map)
  local l = {}
  for id, n in pairs(map) do l[#l + 1] = { id = id, n = n } end
  tsort(l, function(a, b) return a.id < b.id end)
  return l
end
--- les zones où dépecer un cuir (Data.Recipes.farm) : « bêtes niv. 30–40 : Mille pointes, Désolace » + « tu y es »
function PL.FarmText(id)
  local d = D()
  local f = d and d.farm and d.farm[id]
  if not f then return nil, nil end
  local here = fold(try(g("GetRealZoneText")) or "")
  local inZone = false
  for _, z in ipairs(f[4]) do if fold(z) == here then inZone = true end end
  local zs = {}
  for i = 1, min(3, #f[4]) do zs[#zs + 1] = f[4][i] end
  local txt = fmt("bêtes niv. %d–%d : %s", f[1], f[2], tconcat(zs, ", "))
  if inZone then txt = fmt("bêtes niv. %d–%d : tu y es, dépèce ici", f[1], f[2]) end
  return txt, f
end
--- les amis (Groupe) qui ont ce métier : « Bravo (Dépeçage 150) »
function PL.FriendsWith(line)
  local SQ = T.Squad
  if not (SQ and SQ.MemberList) then return nil end
  local ok, list = pcall(SQ.MemberList, SQ)
  if not ok or type(list) ~= "table" then return nil end
  local myName = try(g("UnitName"), "player")
  for _, m in ipairs(list) do
    if m.name ~= myName and type(m.st) == "table" and m.st.pr then
      for _, p in ipairs(M.ParsePr(m.st.pr)) do
        if p.line == line then return fmt("%s (%s %d)", m.name, lineName(line), p.rank) end
      end
    end
  end
  return nil
end
--- accord pour les achats à l'HV de ce métier (option « me demander ») : true / false / nil (pas encore demandé)
function PL.AHAccepted(line)
  local a = S().ah
  if type(a) ~= "table" then return nil end
  return a[line]
end
function PL.SetAH(line, ok)
  local st = S()
  st.ah = type(st.ah) == "table" and st.ah or {}
  st.ah[line] = ok and true or false
  PL.Dirty("focus")
end
local function stepText(st, plan)
  local k = st.kind
  if k == "mail" then
    st.title = "Courrier : prends " .. itemsText(st.items)
    st.sub = whereText(st.where, "boîte aux lettres")
    st.guideLabel = "Boîte aux lettres"
  elseif k == "bank" then
    st.title = "Banque : retire " .. itemsText(st.items)
    st.sub = whereText(st.where, "banque")
    st.guideLabel = "Banque"
  elseif k == "trainer" then
    local parts = {}
    if st.train then parts[#parts + 1] = fmt("passe %s", st.train.rank) end
    for i, l in ipairs(st.learn or {}) do
      if i > 2 then parts[#parts + 1] = fmt("+%d", #st.learn - 2) break end
      parts[#parts + 1] = cut(l.name, 28)
    end
    -- (4.1 LAYOUT, SCREEN405 5b) « (dès 67) » seulement quand tu ne peux pas encore l'apprendre ; sinon « chez ton maître »
    local later = st.at and st.at > plan.skill
    local what = {}
    if st.train then what[#what + 1] = fmt("passe %s", st.train.rank) end
    local names = {}
    for i, l in ipairs(st.learn or {}) do
      if i > 2 then names[#names + 1] = fmt("+%d", #st.learn - 2) break end
      names[#names + 1] = cut(l.name, 28)
    end
    if #names > 0 then what[#what + 1] = (later and "apprendre " or "apprends ") .. tconcat(names, ", ") end
    if #what == 0 then what[1] = tconcat(parts, ", ") end
    local txt = tconcat(what, ", ")
    if later then st.title = "Maître : " .. txt .. fmt(" (dès %d)", st.at)
    else st.title = (txt:gsub("^%l", string.upper)) .. " chez ton maître" end
    st.sub = whereText(st.where, "maître du " .. lineName(plan.line):lower() .. " (pas encore vu)")
    st.guideLabel = "Maître du métier"
  elseif k == "vendor" then
    local cost = 0
    for _, v in ipairs(st.items) do cost = cost + v.cost end
    st.title = "Marchand : achète " .. itemsText(st.items)
    st.sub = whereText(st.where, "marchand de fournitures") .. " · " .. money(cost)
    st.guideLabel = st.where and st.where.name or "Marchand"
  elseif k == "ask" then
    local cost = 0
    for _, v in ipairs(st.items) do cost = cost + v.cost end
    st.title = "HV : " .. itemsText(st.items) .. " ≈ " .. money(cost) .. ", d'accord ?"
    st.sub = "clic : oui, les acheter · clic droit : non, je les farme"
  elseif k == "ah" then
    local cost = 0
    for _, v in ipairs(st.items) do cost = cost + v.cost end
    local first = st.items[1]
    st.title = "HV : achète " .. itemsText(st.items)
    st.sub = whereText(st.where, "hôtel des ventes") .. " · ≈ " .. money(cost)
      .. ((first and #st.items == 1) and (" (≤ " .. money(PL.MaxUnit(first.unit)) .. "/u)") or "")
    st.guideLabel = "Hôtel des ventes"
  elseif k == "farm" then
    local it = st.items[1]
    local txt = it and PL.FarmText(it.id)
    local friend = PL.FriendsWith(393)
    st.title = "À farmer : " .. itemsText(st.items)
    st.sub = txt or (friend and ("demande à " .. friend)) or "pas en vente connue : farme-les ou demande à tes amis"
  elseif k == "craft" then
    local x = st.step
    local col = E.COLOR_FR[x.color] or x.color
    if x.colorTo and x.colorTo ~= x.color then col = col .. " → " .. (E.COLOR_FR[x.colorTo] or x.colorTo) end
    if x.prep then
      st.title = fmt("Crée d'abord %d× %s", x.crafts, cut(x.name, 30))
      st.sub = "compo des recettes suivantes" .. (WHERE_CRAFT[plan.line] and (" · " .. WHERE_CRAFT[plan.line]) or "")
    else
      st.title = fmt("Crée %d× %s (%s)", x.crafts, cut(x.name, 30), col)
      st.sub = (x.to > x.from and fmt("%d → %d", x.from, x.to) or fmt("à %d (chance d'un point)", x.from))
        .. (WHERE_CRAFT[plan.line] and (" · " .. WHERE_CRAFT[plan.line]) or "")
    end
  end
  st.icon = (k == "craft") and PL.LineIcon(plan.line) or (ICON .. (KIND_ICON[k] or "INV_Misc_Book_09"))
  return st
end
--- prix maximal conseillé à l'HV (marché + 20 %)
function PL.MaxUnit(unit) return ceil((tonumber(unit) or 0) * 1.2) end
--- la session d'un plan, dans l'ordre : courrier / banque / maître / marchand / HV (du plus proche au plus loin),
--- à farmer, puis les fabrications (avec le maître au bon moment : rang au plafond, nouvelle recette)
function PL.Steps(plan)
  local steps = {}
  if not plan then return steps end
  local res, sp = plan.res, plan.split
  local pre, decide, farm = {}, {}, {}
  for _, f in ipairs(sp.farm) do farm[#farm + 1] = f end
  if next(sp.mail) then pre[#pre + 1] = { kind = "mail", items = listOf(sp.mail), where = PL.WhereService("mailbox") } end
  if next(sp.bank) then pre[#pre + 1] = { kind = "bank", items = listOf(sp.bank), where = PL.WhereService("bank") } end
  local learnNow, later = {}, {}
  for _, l in ipairs(res.learn) do if l.at <= plan.skill then learnNow[#learnNow + 1] = l else later[l.spell] = l end end
  for _, x in ipairs(plan.prep or {}) do
    if x.learn then learnNow[#learnNow + 1] = { spell = x.spell, name = x.name, at = plan.skill, kind = "trainer" } end
  end
  local trainNow = res.train[1] and res.train[1].at <= plan.skill and res.train[1] or nil
  if #learnNow > 0 or trainNow then
    pre[#pre + 1] = { kind = "trainer", learn = learnNow, train = trainNow, where = PL.WhereTrainer(plan.line) }
  end
  if #sp.vendor > 0 then
    local ids = {}
    for _, v in ipairs(sp.vendor) do ids[#ids + 1] = v.id end
    pre[#pre + 1] = { kind = "vendor", items = sp.vendor, where = PL.WhereVendor(ids) }
  end
  if #sp.ah > 0 then
    local ok = PL.AHAccepted(plan.line)
    if ok == nil and M.db and M.db.askAH ~= false then decide[#decide + 1] = { kind = "ask", items = sp.ah }
    elseif ok == false then for _, it in ipairs(sp.ah) do farm[#farm + 1] = { id = it.id, n = it.n } end
    else pre[#pre + 1] = { kind = "ah", items = sp.ah, where = PL.WhereService("auction") } end
  end
  -- plus proche voisin depuis toi (les étapes sans lieu connu à la fin, dans l'ordre)
  local here, placed, rest = me(), {}, {}
  for _, st in ipairs(pre) do if st.where then placed[#placed + 1] = st else rest[#rest + 1] = st end end
  local cur = here
  while #placed > 0 do
    local bi, bd = 1, nil
    for i, st in ipairs(placed) do
      local d = cur and vec(cur, st.where) or nil
      if d and (not bd or d < bd) then bi, bd = i, d end
    end
    local st = table.remove(placed, bi)
    st.where.dist = here and vec(here, st.where) or st.where.dist
    steps[#steps + 1] = st
    cur = st.where
  end
  for _, st in ipairs(decide) do table.insert(steps, 1, st) end
  for _, st in ipairs(rest) do steps[#steps + 1] = st end
  if #farm > 0 then steps[#steps + 1] = { kind = "farm", items = farm } end
  for _, x in ipairs(plan.prep or {}) do steps[#steps + 1] = { kind = "craft", step = x } end
  -- une seule visite au maître par palier (rang + recettes du même moment)
  local function trainerAt(at)
    local lastSt = steps[#steps]
    if lastSt and lastSt.kind == "trainer" and lastSt.at == at then return lastSt end
    local st = { kind = "trainer", learn = {}, at = at, where = PL.WhereTrainer(plan.line) }
    steps[#steps + 1] = st
    return st
  end
  for _, x in ipairs(res.seq) do
    if x.kind == "train" then
      if x ~= trainNow then trainerAt(x.at).train = x end
    else
      local l = later[x.spell]
      if l then
        local st = trainerAt(l.at)
        st.learn[#st.learn + 1] = l
        later[x.spell] = nil
      end
      steps[#steps + 1] = { kind = "craft", step = x }
    end
  end
  for _, st in ipairs(steps) do stepText(st, plan) end
  return steps
end

-- ===================================================================== mode focus « Métier »
local function routeChar() local R = T.Route return R and type(R.char) == "table" and R.char or nil end
--- met le trajet de quêtes en pause (ZonePlan, sinon Route) ; vrai si C'EST NOUS qui l'avons mis en pause
local function pauseRoute()
  local rc = routeChar()
  if not rc or rc.paused then return false end
  local Z, R = T.ZonePlan, T.Route
  if Z and type(Z.TogglePause) == "function" then pcall(Z.TogglePause, Z)
  elseif R and type(R.TogglePause) == "function" then pcall(R.TogglePause, R) end
  return rc.paused == true
end
local function resumeRoute()
  local rc = routeChar()
  if not (rc and rc.paused) then return end
  local Z, R = T.ZonePlan, T.Route
  if Z and type(Z.TogglePause) == "function" then pcall(Z.TogglePause, Z)
  elseif R and type(R.TogglePause) == "function" then pcall(R.TogglePause, R) end
end
PL.PauseRoute, PL.ResumeRoute = pauseRoute, resumeRoute
if type(T.FocusMode) ~= "function" then
  --- (PROFLEVEL, contrat « focus ») le mode focus en cours : kind ("prof") | nil, puis la table T.focus
  function T:FocusMode() local f = self.focus return f and f.kind or nil, f end
end
function PL.FocusOn() return PL.focus ~= nil end
--- le métier montré par la page (le dernier choisi, sinon le 1er métier de fabrication qui a un plan)
function PL.Line()
  local st = S()
  local l = tonumber(st.line)
  if l and profOf(l) then return l end
  local d = D()
  for _, p in ipairs(M.profs or {}) do
    local k = d and d.lines[p.line]
    if k and k[1] ~= "g" and d.r[p.line] and #d.r[p.line] > 0 then return p.line end
  end
  return M.profs and M.profs[1] and M.profs[1].line or nil
end
function PL.SetLine(line) S().line = line PL.Dirty("focus") end
function PL.FocusPlan()
  local f = PL.focus
  if not f then return nil end
  return PL.Compute(f.line, f.mode, f.mode == "exact" and 300 or PL.Target(f.line, f.mode))
end
local function publish()
  local f = PL.focus
  if not f then
    if T.focus and T.focus.owner == "Prof" then T.focus = nil end
    return
  end
  T.focus = { kind = "prof", owner = "Prof", label = "Session métier", line = f.line, mode = f.mode,
    since = (T.focus and T.focus.owner == "Prof" and T.focus.since) or now(), gen = ver.focus,
    pausedRoute = f.paused and true or false, card = function(n) return PL.CardModel(n) end }
end
local function cardSoon()
  local Wn = T.Window
  if Wn and Wn.RenderCardSoon then pcall(Wn.RenderCardSoon) end
  if T.RequestRefresh then T:RequestRefresh() end
end
--- lance la session métier (un clic) : trajet en pause, carte + boussole sur les étapes
function PL.StartFocus(line, mode)
  if inCombat() then refuse("Session métier : après le combat.") return false end
  line = tonumber(line) or PL.Line()
  if not (line and profOf(line)) then say("Aucun métier à monter.") return false end
  mode = (mode == "exact" or mode == "full") and mode or PL.Mode()
  local st = S()
  local was = type(st.focus) == "table" and st.focus.paused or false
  local paused = pauseRoute()
  st.focus = { line = line, mode = mode, since = epoch(), paused = paused or was }
  PL.focus = st.focus
  st.line = line
  PL.stepSig, PL.lastGuide = nil, nil
  ver.focus = ver.focus + 1
  publish()
  T:Fire("FOCUS_CHANGED", "prof")
  PL.Update()
  cardSoon()
  say(fmt("Session métier : %s (%s). Le trajet de quêtes est en pause.", lineName(line), mode == "exact" and "pile-poil" or "complet"))
  return true
end
--- quitte la session : tout revient comme avant (trajet repris s'il était en route, boussole rendue au trajet)
function PL.StopFocus(why)
  local st = S()
  local f = st.focus or PL.focus
  st.focus, PL.focus = nil, nil
  if f and f.paused then resumeRoute() end
  publish()
  PL.HideCard()
  PL.ClearGlows()
  local C = T.Compass
  local c = C and type(C.char) == "table" and C.char.custom or nil
  if type(c) == "table" and c.source == "prof" and type(C.FollowRoute) == "function" then pcall(C.FollowRoute) end
  PL.lastGuide, PL.steps, PL.stepSig = nil, nil, nil
  ver.focus = ver.focus + 1
  if f then T:Fire("FOCUS_CHANGED", nil) end
  cardSoon()
  if f and why ~= "silent" then say("Session métier terminée : le trajet de quêtes reprend.") end
  return f ~= nil
end
function PL.ToggleFocus(line, mode) if PL.focus then return PL.StopFocus() end return PL.StartFocus(line, mode) end
--- reprise après /reload (session de moins de 12 h), sinon fin propre
function PL.RestoreFocus()
  local f = S().focus
  if type(f) ~= "table" then return end
  if epoch() - (tonumber(f.since) or 0) > 12 * 3600 or not profOf(tonumber(f.line) or 0) then PL.StopFocus("silent") return end
  PL.focus = f
  publish()
end
--- la boussole vers le lieu d'une étape (une fois par lieu, sauf clic)
function PL.Guide(st, force)
  if not (st and st.where) or (M.db and M.db.plCompass == false) then return false end
  local w = st.where
  local key = st.kind .. ":" .. tostring(w.mapID) .. ":" .. floor((w.x or 0) * 1000) .. ":" .. floor((w.y or 0) * 1000)
  if key == PL.lastGuide and not force then return false end
  PL.lastGuide = key
  local C = T.Compass
  if C and type(C.SetTarget) == "function" then
    pcall(C.SetTarget, C, { mapID = w.mapID, x = w.x, y = w.y, label = cut(st.guideLabel or st.title, 40), source = "prof" })
    return true
  end
  return false
end

-- ===================================================================== carte « Prochaine étape » (contrat T.focus.card)
local function pageShown()
  local Wn = T.Window
  return Wn and Wn.IsOpen and Wn.IsOpen() and Wn.CurrentPage and Wn.CurrentPage() == "prof" or false
end
PL.PageShown = pageShown
function PL.ActionText(st)
  local k = st.kind
  if k == "ask" then return "acheter ces compos à l'HV (sinon : page « Métiers »)" end
  if k == "vendor" then return PL.merchantOpen and "acheter la suite chez ce marchand" or "guider vers le marchand" end
  if k == "bank" then return PL.bankOpen and "retirer la pile suivante" or "guider vers la banque" end
  if k == "craft" then return PL.tradeOpen and "créer (un clic = une fabrication de N)" or "ouvrir ton métier, puis « Créer »" end
  if k == "ah" then return PL.ahOpen and "chercher / acheter à l'HV" or "guider vers l'hôtel des ventes" end
  if k == "mail" then return "guider vers la boîte aux lettres" end
  if k == "trainer" then return "guider vers le maître" end
  return nil
end
--- lignes de la carte pendant la session : étapes numérotées (titre, sous-titre, clic = l'action / la boussole)
function PL.CardModel(n)
  n = tonumber(n) or 3
  local quit = function() PL.StopFocus() end
  local plan = PL.FocusPlan()
  if not plan then
    return { { n = "!", title = "Session métier : métier introuvable", sub = "clic : quitter la session", onClick = quit,
      actionText = "quitter la session métier" } }, "Session métier", ""
  end
  local steps = PL.steps or PL.Steps(plan)
  local rows = {}
  for i, st in ipairs(steps) do
    if i > n then break end
    rows[#rows + 1] = { n = i, title = st.title, sub = st.sub, icon = st.icon, actionText = PL.ActionText(st),
      onClick = function() PL.RunStep(st) end }
  end
  if #rows == 0 then
    rows[1] = { n = "!", title = plan.res.stop == "target" and "Cible atteinte : session finie" or "Plus rien à faire avec ce que tu as",
      sub = "clic : quitter la session (le trajet reprend)", onClick = quit, actionText = "quitter la session métier" }
  end
  local title = fmt("%s %d → %d", plan.name, plan.skill, plan.res.reached)
  local sum = (plan.mode == "exact" and "Pile-poil" or "Complet") .. " · ≈ " .. minutes(plan.time)
    .. (plan.cost > 0 and (" · " .. money(plan.cost)) or "")
  return rows, title, sum
end
--- clic sur une étape (carte, page) : l'action du jeu si sa fenêtre est ouverte, sinon la boussole
function PL.RunStep(st)
  if type(st) ~= "table" then return false end
  if inCombat() then refuse("Session métier : après le combat.") return false end
  local k = st.kind
  local line = (PL.focus and PL.focus.line) or PL.Line()
  if k == "ask" then PL.SetAH(line, true) say("Achats à l'HV acceptés pour cette session.") return true end
  if k == "vendor" and PL.merchantOpen then return PL.BuyNext() end
  if k == "bank" and PL.bankOpen then return PL.WithdrawNext() end
  if k == "craft" and PL.TradeLine() == line then return PL.CraftNow() end
  if k == "ah" and PL.ahOpen then return PL.AHNext() end
  if st.where then
    PL.Guide(st, true)
    say("Boussole : " .. cut(st.where.name or st.guideLabel or "", 40))
    return true
  end
  if k == "craft" then say("Ouvre ton " .. lineName(line):lower() .. " (livre des métiers) : le bouton « Créer » apparaît sous sa fenêtre.") end
  return false
end

-- ===================================================================== carte d'action sous les fenêtres du jeu
local acard
local function uiScale() local U = g("UIParent") return U and num(try(U.GetEffectiveScale, U)) or 1 end
local function shown(f) return type(f) == "table" and try(f.IsVisible, f) and true or false end
local function centerOf(f)
  local x = num((try(f.GetCenter, f)))
  if not x then return nil end
  return x * (num(try(f.GetEffectiveScale, f)) or 1) / max(0.01, uiScale())
end
local function bottomOf(f)
  local b = num((try(f.GetBottom, f)))
  if not b then return nil end
  return b * (num(try(f.GetEffectiveScale, f)) or 1) / max(0.01, uiScale())
end
local OWNERS = { merchant = { "MerchantFrame" }, bank = { "BankFrame", "BankPanel" }, trade = { "ProfessionsFrame", "TradeSkillFrame" },
  ah = { "AuctionHouseFrame", "AuctionFrame" }, mail = { "MailFrame", "InboxFrame" } }
local function ownerOf(kind)
  for _, n in ipairs(OWNERS[kind] or {}) do local f = g(n) if shown(f) then return f end end
  return nil
end
local function ensureCard()
  if acard then return acard end
  if inCombat() then return nil end
  local Th, cf = T.Theme, g("CreateFrame")
  if type(cf) ~= "function" then return nil end
  local f = (Th and Th.Frame and Th:Frame(g("UIParent"), "panel", 280, 96)) or try(cf, "Frame", nil, g("UIParent"))
  if not f then return nil end
  try(f.SetSize, f, 280, 96)
  try(f.SetFrameStrata, f, "DIALOG")
  try(f.SetClampedToScreen, f, true)
  local function fs(size, tok)
    return (Th and Th.Text and Th:Text(f, size, tok)) or try(f.CreateFontString, f, nil, "OVERLAY", "GameFontHighlightSmall")
  end
  f.title = fs("S", "gold")
  f.body = fs("S", "text")
  for _, t in ipairs({ f.title, f.body }) do
    if t then try(t.SetJustifyH, t, "LEFT") try(t.SetWordWrap, t, true) try(t.SetWidth, t, 264) end
  end
  if f.title then try(f.title.SetPoint, f.title, "TOPLEFT", f, "TOPLEFT", 8, -7) end
  if f.body and f.title then try(f.body.SetPoint, f.body, "TOPLEFT", f.title, "BOTTOMLEFT", 0, -4) end
  local function click()
    if not f.fn then return end
    if inCombat() then refuse("Session métier : après le combat.") return end
    T:UserAction(f.fn)
    PL.UpdateCard()
  end
  f.btn = (Th and Th.Button and Th:Button(f, "", 220, 22, click)) or try(cf, "Button", nil, f)
  if f.btn then
    try(f.btn.SetSize, f.btn, 220, 22)
    try(f.btn.SetPoint, f.btn, "BOTTOM", f, "BOTTOM", 0, 8)
    if not (Th and Th.Button) then try(f.btn.SetScript, f.btn, "OnClick", click) end
    try(f.btn.SetScript, f.btn, "OnEnter", function(b)
      local tt = g("GameTooltip")
      if not (tt and f.tip) then return end
      try(tt.SetOwner, tt, b, "ANCHOR_RIGHT")
      for i, l in ipairs(f.tip) do
        if i == 1 then try(tt.SetText, tt, l) else try(tt.AddLine, tt, l, 0.9, 0.9, 0.9, true) end
      end
      try(tt.Show, tt)
    end)
    try(f.btn.SetScript, f.btn, "OnLeave", function() local tt = g("GameTooltip") if tt then try(tt.Hide, tt) end end)
  end
  try(f.Hide, f)
  acard = f
  return f
end
PL.GetCard = function() return acard end
--- carte d'action sous une fenêtre du jeu visible (lue, jamais modifiée ni ancrée) : titre, liste, UN bouton
function PL.ShowCard(owner, title, body, label, fn, tip)
  if inCombat() then return nil end
  local f = ensureCard()
  if not f then return nil end
  local safe = T.SafeText or function(s) return s end
  if f.title then try(f.title.SetText, f.title, safe(title or "")) end
  if f.body then try(f.body.SetText, f.body, safe(body or "")) end
  local lines = 1
  for _ in tostring(body or ""):gmatch("\n") do lines = lines + 1 end
  try(f.SetHeight, f, 30 + 14 * lines + (label and 30 or 0))
  if f.btn then
    if label then
      if f.btn.SetLabel then f.btn:SetLabel(safe(label)) else try(f.btn.SetText, f.btn, safe(label)) end
      try(f.btn.Show, f.btn)
    else try(f.btn.Hide, f.btn) end
  end
  f.fn, f.tip, f.label = fn, tip, label
  local x, y = owner and centerOf(owner), owner and bottomOf(owner)
  try(f.ClearAllPoints, f)
  if x and y then try(f.SetPoint, f, "TOP", g("UIParent"), "BOTTOMLEFT", x, y - 6)
  else try(f.SetPoint, f, "TOP", g("UIParent"), "TOP", 0, -140) end
  try(f.Show, f)
  return f
end
function PL.HideCard()
  if acard then acard.fn = nil try(acard.Hide, acard) end
end

-- surbrillance des cases de banque à retirer (cadres à nous, posés sur les boutons du jeu, hors combat)
local glows, glowN = {}, 0
local function glowFor(i)
  if glows[i] then return glows[i] end
  local cf = g("CreateFrame")
  if type(cf) ~= "function" then return nil end
  local f = try(cf, "Frame", nil, g("UIParent"))
  if not f then return nil end
  local tex = try(f.CreateTexture, f, nil, "OVERLAY")
  if tex then
    try(tex.SetAllPoints, tex, f)
    try(tex.SetTexture, tex, "Interface\\Buttons\\UI-ActionButton-Border")
    try(tex.SetBlendMode, tex, "ADD")
    try(tex.SetVertexColor, tex, 1, 0.82, 0.2, 1)
  end
  try(f.EnableMouse, f, false)
  glows[i] = f
  return f
end
function PL.ClearGlows()
  for i = 1, glowN do if glows[i] then try(glows[i].Hide, glows[i]) end end
  glowN = 0
end
--- boutons de la banque visibles : { {bouton, sac, case} } (Forever : EnumerateValidItems du panneau ; Era : BankFrameItemN
--- + sacs de banque ouverts)
function PL.BankButtons()
  local out = {}
  for _, name in ipairs({ "BankPanel", "BankFrame" }) do
    local f = g(name)
    local p = type(f) == "table" and ((type(f.EnumerateValidItems) == "function" and f) or (type(f.BankPanel) == "table" and f.BankPanel)) or nil
    if p and type(p.EnumerateValidItems) == "function" and shown(p) then
      local ok, iter, st, ctl = pcall(p.EnumerateValidItems, p)
      if ok and type(iter) == "function" then
        for _, btn in iter, st, ctl do
          local b, s = num((try(btn.GetBagID, btn))), num((try(btn.GetID, btn)))
          if b and s then out[#out + 1] = { btn, b, s } end
        end
      end
      if #out > 0 then return out end
    end
  end
  for i = 1, 28 do
    local btn = g("BankFrameItem" .. i)
    if shown(btn) then out[#out + 1] = { btn, num(g("BANK_CONTAINER")) or -1, i } end
  end
  local HL = T.modules and T.modules.Highlight
  if HL and type(HL.BlizzardBagButtons) == "function" then
    local bank = {}
    for _, b in ipairs(PL.BankBags()) do bank[b] = true end
    for _, r in ipairs(try(HL.BlizzardBagButtons) or {}) do if bank[r[2]] then out[#out + 1] = r end end
  end
  return out
end
function PL.GlowBank(list)
  PL.ClearGlows()
  if inCombat() or not list or #list == 0 then return 0 end
  local want = {}
  for _, e in ipairs(list) do for _, s in ipairs(e.slots or {}) do want[s.bag .. ":" .. s.slot] = true end end
  for _, r in ipairs(PL.BankButtons()) do
    if want[r[2] .. ":" .. r[3]] and glowN < 16 then
      local gl = glowFor(glowN + 1)
      if gl then
        glowN = glowN + 1
        try(gl.ClearAllPoints, gl)
        try(gl.SetPoint, gl, "TOPLEFT", r[1], "TOPLEFT", -8, 8)
        try(gl.SetPoint, gl, "BOTTOMRIGHT", r[1], "BOTTOMRIGHT", 8, -8)
        try(gl.SetFrameStrata, gl, try(r[1].GetFrameStrata, r[1]) or "HIGH")
        try(gl.SetFrameLevel, gl, (num(try(r[1].GetFrameLevel, r[1])) or 1) + 5)
        try(gl.Show, gl)
      end
    end
  end
  return glowN
end

-- ------------------------------------------------------------------ marchand : achats exacts, un par clic
PL.pendingBuy, PL.pendingBank, PL.pendingAH = {}, {}, {}
--- ce qu'il reste à acheter CHEZ CE marchand : { {index, id, n, lot, maxStack, price, avail} }, puis ce qu'il ne vend pas
function PL.MerchantPlan()
  local plan = PL.FocusPlan()
  if not (plan and PL.merchantOpen) then return {}, {} end
  local need = {}
  for _, v in ipairs(plan.split.vendor) do
    local left = v.buy - (PL.pendingBuy[v.id] or 0)
    if left > 0 then need[v.id] = left end
  end
  local out = {}
  for i = 1, min(num((try(g("GetMerchantNumItems")))) or 0, 120) do
    local id = merchantID(i)
    local want = id and need[id]
    if want then
      local price, lot, avail = merchantInfo(i)
      out[#out + 1] = { index = i, id = id, n = want, lot = lot, price = price, avail = avail,
        maxStack = max(1, num((try(g("GetMerchantItemMaxStack"), i))) or 20) }
      need[id] = nil
    end
  end
  local missing = {}
  for id, n in pairs(need) do missing[#missing + 1] = { id = id, n = n } end
  tsort(missing, function(a, b) return a.id < b.id end)
  return out, missing
end
--- quantité du prochain achat d'une ligne : le reste, arrondi au lot, au plus une pile (stock du marchand compris)
function PL.BuyQty(e)
  local lot = max(1, e.lot or 1)
  local qty = ceil(e.n / lot) * lot
  local cap = max(lot, floor((e.maxStack or lot) / lot) * lot)
  if qty > cap then qty = cap end
  if e.avail and e.avail >= 0 and e.avail * lot < qty then qty = e.avail * lot end
  return qty
end
local function clicksLeft(list)
  local n = 0
  for _, e in ipairs(list) do
    local q = PL.BuyQty(e)
    if q > 0 then n = n + ceil(e.n / q) end
  end
  return n
end
PL.ClicksLeft = clicksLeft
--- UN achat (dans le clic du joueur) : BuyMerchantItem(case, quantité exacte)
function PL.BuyNext()
  if inCombat() then refuse("Achat : hors combat.") return false end
  local list = PL.MerchantPlan()
  local e = list[1]
  if not e then say("Rien à acheter chez ce marchand.") return false end
  local qty = PL.BuyQty(e)
  if qty <= 0 then say(matName(e.id) .. " : épuisé chez ce marchand.") return false end
  local cost = floor((e.price or 0) * qty / max(1, e.lot))
  local gold = num(API.Money and API.Money()) or 0
  if cost > gold then say(fmt("Pas assez d'or pour %d %s (%s).", qty, matName(e.id), money(cost))) return false end
  local fn = g("BuyMerchantItem")
  if type(fn) ~= "function" then return false end
  local ok = pcall(fn, e.index, qty)
  if ok then
    PL.pendingBuy[e.id] = (PL.pendingBuy[e.id] or 0) + qty
    PL.bought = (PL.bought or 0) + 1
    say(fmt("Acheté : %d %s (%s)", qty, matName(e.id), money(cost)))
  end
  return ok
end

-- ------------------------------------------------------------------ banque : retirer, une pile par clic
function PL.BankPlan()
  local plan = PL.FocusPlan()
  if not (plan and PL.bankOpen) then return {} end
  local out = {}
  for id, n in pairs(plan.split.bank) do
    local left = n - (PL.pendingBank[id] or 0)
    if left > 0 then out[#out + 1] = { id = id, n = left, slots = (PL.bankSlots or {})[id] or {} } end
  end
  tsort(out, function(a, b) return a.id < b.id end)
  return out
end
local function merchantShown() return PL.merchantOpen or shown(g("MerchantFrame")) end
--- UN retrait (dans le clic) : la plus petite pile qui suffit, sinon la plus grosse (UseContainerItem, banque ouverte)
function PL.WithdrawNext()
  if inCombat() then refuse("Banque : hors combat.") return false end
  if merchantShown() then say("Ferme le marchand d'abord (sinon le jeu vendrait l'objet).") return false end
  if not PL.bankOpen then say("Ouvre ta banque d'abord.") return false end
  local e = PL.BankPlan()[1]
  if not e then say("Rien à retirer.") return false end
  local pick
  for _, s in ipairs(e.slots) do if s.n >= e.n and (not pick or s.n < pick.n) then pick = s end end
  if not pick then for _, s in ipairs(e.slots) do if not pick or s.n > pick.n then pick = s end end end
  if not pick then say(matName(e.id) .. " : introuvable dans la banque (relevé à refaire).") return false end
  local CC = g("C_Container")
  local fn = (CC and CC.UseContainerItem) or g("UseContainerItem")
  if type(fn) ~= "function" then return false end
  local ok = pcall(fn, pick.bag, pick.slot)
  if ok then
    PL.pendingBank[e.id] = (PL.pendingBank[e.id] or 0) + pick.n
    say(fmt("Retiré : %d %s%s", pick.n, matName(e.id), pick.n > e.n and fmt(" (il en fallait %d)", e.n) or ""))
  end
  return ok
end

-- ------------------------------------------------------------------ fenêtre du métier : créer N, un appel par clic
function PL.TradeLine()
  if not PL.tradeOpen then return nil end
  local C = g("C_TradeSkillUI")
  local d = D()
  local info = C and C.GetBaseProfessionInfo and try(C.GetBaseProfessionInfo)
  local line = type(info) == "table" and num(info.professionID) or nil
  if line and d and d.lines[line] then return line end
  if type(info) == "table" then
    line = M.LineOf(info.professionName or info.parentProfessionName or "")
    if line then return line end
  end
  local n = str((try(g("GetTradeSkillLine"))))
  return (n and M.LineOf(n)) or PL.tradeLine
end
function PL.NextCraft()
  for _, st in ipairs(PL.steps or {}) do if st.kind == "craft" then return st end end
  return nil
end
local function bagsFor(row)
  local st = {}
  local m = row[10] or {}
  for k = 1, #m - 1, 2 do st[m[k]] = bagCount(m[k]) end
  return st
end
function PL.TradeIndex(name)
  local f = fold(name)
  for i = 1, min(num((try(g("GetNumTradeSkills")))) or 0, 500) do
    local nm, kind = try(g("GetTradeSkillInfo"), i)
    if type(nm) == "string" and kind ~= "header" and fold(nm) == f then return i end
  end
  return nil
end
--- UNE fabrication de N (dans le clic) : C_TradeSkillUI.CraftRecipe(recette, N) (Forever), DoTradeSkill (Era)
function PL.CraftNow()
  if inCombat() then refuse("Fabrication : hors combat.") return false end
  local st = PL.NextCraft()
  if not st then say("Rien à créer pour l'instant.") return false end
  local x = st.step
  local line = PL.focus and PL.focus.line
  local known = line and PL.Known(line)
  if x.learn and not (known and known[x.spell]) then say("Apprends d'abord « " .. cut(x.name, 30) .. " » chez le maître.") return false end
  local n = min(x.crafts, E.Makeable(x.row, bagsFor(x.row)))
  if n <= 0 then say("Il te manque des compos dans tes sacs pour " .. cut(x.name, 30) .. " (étapes d'avant).") return false end
  local C = g("C_TradeSkillUI")
  if C and type(C.CraftRecipe) == "function" then
    local ok = pcall(C.CraftRecipe, x.spell, n)
    if ok then PL.crafting = { spell = x.spell, n = n, t = now() } say(fmt("Création : %d× %s", n, x.name)) end
    return ok
  end
  local idx, fn = PL.TradeIndex(x.name), g("DoTradeSkill")
  if idx and type(fn) == "function" then
    local ok = pcall(fn, idx, n)
    if ok then PL.crafting = { spell = x.spell, n = n, t = now() } say(fmt("Création : %d× %s", n, x.name)) end
    return ok
  end
  say("Clique « Créer » dans la fenêtre du métier : " .. cut(x.name, 30) .. fmt(" × %d.", n))
  return false
end

-- ------------------------------------------------------------------ hôtel des ventes : chercher, acheter, confirmer
function PL.AHPlan()
  local plan = PL.FocusPlan()
  if not plan then return {} end
  local out = {}
  for _, it in ipairs(plan.split.ah) do
    local left = it.n - (PL.pendingAH[it.id] or 0)
    if left > 0 then out[#out + 1] = { id = it.id, n = left, unit = it.unit } end
  end
  return out
end
--- UN appel HV par clic : recherche → achat (marchandise, quantité exacte) → confirmation ; prix revérifié
function PL.AHNext()
  if inCombat() then refuse("HV : hors combat.") return false end
  local AH = g("C_AuctionHouse")
  if not (PL.ahOpen and type(AH) == "table") then say("Ouvre l'hôtel des ventes d'abord.") return false end
  local e = PL.AHPlan()[1]
  if not e then return false end
  local s = PL.ahState
  if not s or s.id ~= e.id or s.state == "done" or s.state == "gone" or s.state == "high" then
    if type(AH.IsThrottledMessageSystemReady) == "function" and not try(AH.IsThrottledMessageSystemReady) then
      say("L'HV limite les recherches : reclique dans un instant.")
      return false
    end
    PL.ahState = { id = e.id, qty = e.n, maxUnit = PL.MaxUnit(e.unit), state = "search" }
    local key = (type(AH.MakeItemKey) == "function" and try(AH.MakeItemKey, e.id)) or { itemID = e.id }
    try(AH.SendSearchQuery, key, { { sortOrder = 0, reverseSort = false } }, true)
    return true
  end
  if s.state == "ready" then
    s.state = "starting"
    try(AH.StartCommoditiesPurchase, s.id, s.qty)
    return true
  end
  if s.state == "confirm" then
    s.state = "buying"
    try(AH.ConfirmCommoditiesPurchase, s.id, s.qty)
    PL.pendingAH[s.id] = (PL.pendingAH[s.id] or 0) + s.qty
    return true
  end
  return false
end
function PL.OnCommodityResults(id)
  local s = PL.ahState
  id = num(id)
  if not (s and s.state == "search" and (id == nil or id == s.id)) then return end
  local AH = g("C_AuctionHouse")
  local total, got = 0, 0
  for i = 1, min(200, num((try(AH.GetNumCommoditySearchResults, s.id))) or 0) do
    local r = try(AH.GetCommoditySearchResultInfo, s.id, i)
    local u, q = type(r) == "table" and num(r.unitPrice), type(r) == "table" and num(r.quantity)
    if u and q and not r.containsOwnerItem then
      local take = min(s.qty - got, q)
      if take > 0 then total, got = total + take * u, got + take end
      if got >= s.qty then break end
    end
  end
  if got == 0 then s.state = "gone" say(matName(s.id) .. " : rien en vente à l'HV.")
  elseif total > s.maxUnit * got then s.state = "high" say(fmt("%s : trop cher maintenant (%s/u).", matName(s.id), money(floor(total / got))))
  else
    if got < s.qty then say(fmt("%s : seulement %d en vente.", matName(s.id), got)) end
    s.qty, s.total, s.state = got, total, "ready"
  end
  PL.UpdateCard()
end
function PL.OnCommodityPrice(_, total)
  local s = PL.ahState
  if not (s and s.state == "starting") then return end
  total = num(total)
  if total and total <= s.maxUnit * s.qty then s.state, s.total = "confirm", total
  else
    local AH = g("C_AuctionHouse")
    if AH and AH.CancelCommoditiesPurchase then try(AH.CancelCommoditiesPurchase) end
    s.state = "high"
    say(fmt("%s : le prix a monté (%s), achat annulé.", matName(s.id), money(total or 0)))
  end
  PL.UpdateCard()
end
local function ahLabel(e)
  local s = PL.ahState
  if s and s.id == e.id then
    if s.state == "search" then return "Recherche…" end
    if s.state == "ready" then return fmt("Acheter %d (%s)", s.qty, money(s.total or 0)) end
    if s.state == "starting" then return "Vérification du prix…" end
    if s.state == "confirm" then return fmt("Confirmer %s", money(s.total or 0)) end
    if s.state == "buying" then return "Achat en cours…" end
    if s.state == "high" then return "Trop cher : rechercher" end
  end
  return "Chercher " .. cut(matName(e.id), 22)
end

-- ------------------------------------------------------------------ quelle carte montrer (fenêtre du jeu ouverte)
function PL.UpdateCard()
  if inCombat() or not PL.focus then PL.HideCard() PL.ClearGlows() return end
  local plan = PL.FocusPlan()
  if not plan then PL.HideCard() return end
  local head = "Session métier · " .. plan.name
  if PL.merchantOpen then
    local list, missing = PL.MerchantPlan()
    if #list > 0 then
      local lines = {}
      for i, e in ipairs(list) do if i <= 4 then lines[#lines + 1] = fmt("%d %s", e.n, matName(e.id)) end end
      if #missing > 0 then lines[#lines + 1] = "Pas ici : " .. itemsText(missing, 2) end
      local n = clicksLeft(list)
      local label = (PL.bought or 0) == 0 and fmt("Acheter tout (%d)", n) or fmt("Acheter la suite (%d)", n)
      local e = list[1]
      PL.ShowCard(ownerOf("merchant"), head .. " : à acheter ici", tconcat(lines, "\n"), label, PL.BuyNext,
        { "Acheter tout", fmt("Clic : %d %s maintenant (quantité exacte, une pile par clic).", PL.BuyQty(e), matName(e.id)),
          "Rien d'autre n'est acheté sans un clic." })
      return
    end
  end
  if PL.bankOpen then
    local list = PL.BankPlan()
    if #list > 0 then
      local lines = {}
      for i, e in ipairs(list) do if i <= 4 then lines[#lines + 1] = fmt("%d %s", e.n, matName(e.id)) end end
      PL.GlowBank(list)
      PL.ShowCard(ownerOf("bank"), head .. " : à retirer", tconcat(lines, "\n"), "Retirer " .. cut(matName(list[1].id), 24),
        PL.WithdrawNext, { "Retirer", "Clic : la pile de " .. matName(list[1].id) .. " passe dans tes sacs (cases en surbrillance)." })
      return
    end
    PL.ClearGlows()
  end
  if PL.tradeOpen and PL.TradeLine() == plan.line then
    local st = PL.NextCraft()
    if st then
      local x = st.step
      local n = min(x.crafts, E.Makeable(x.row, bagsFor(x.row)))
      local col = E.COLOR_FR[x.color] or x.color
      local kn = PL.Known(plan.line)
      if x.learn and not (kn and kn[x.spell]) then
        PL.ShowCard(ownerOf("trade"), head, fmt("Prochaine : %s — à apprendre d'abord chez le maître.", x.name), nil, nil, nil)
      elseif n > 0 then
        PL.ShowCard(ownerOf("trade"), head .. fmt(" : %d → %d", x.from, x.to), fmt("%s (%s)", x.name, col), fmt("Créer %d", n),
          PL.CraftNow, { "Créer", fmt("Clic : %d× %s (une seule commande au jeu, comme « Créer »).", n, x.name) })
      else
        PL.ShowCard(ownerOf("trade"), head, fmt("Prochaine : %d× %s — compos à aller chercher d'abord (étapes de la carte).",
          x.crafts, x.name), nil, nil, nil)
      end
      return
    end
  end
  if PL.ahOpen then
    local list = PL.AHPlan()
    if #list > 0 then
      local e = list[1]
      local lines = {}
      for i, it in ipairs(list) do if i <= 3 then lines[#lines + 1] = fmt("%d %s (≤ %s/u)", it.n, matName(it.id), money(PL.MaxUnit(it.unit))) end end
      PL.ShowCard(ownerOf("ah"), head .. " : à l'HV", tconcat(lines, "\n"), ahLabel(e), PL.AHNext,
        { "Hôtel des ventes", "Clic 1 : recherche du prix. Clic 2 : achat (quantité exacte). Clic 3 : confirmer.",
          "Le prix est revérifié : au-dessus du plafond, rien n'est acheté." })
      return
    end
  end
  if PL.mailOpen and next(plan.split.mail) then
    PL.ShowCard(ownerOf("mail"), head .. " : dans ton courrier", "Prends : " .. itemsText(listOf(plan.split.mail), 4), nil, nil, nil)
    return
  end
  PL.HideCard()
end

-- ===================================================================== boucle (regroupée, seulement si utile)
function PL.Soon(delay)
  if PL.pending then return end
  PL.pending = true
  T:After(delay or 0.3, function() PL.pending = false pcall(PL.Update) end)
end
function PL.Update()
  if not (M.IsEnabled and M:IsEnabled()) then return end
  if PL.focus and not inCombat() then
    if not M.profs or #M.profs == 0 then M.profs = M.ScanProfessions() end
    local plan = PL.FocusPlan()
    local steps = PL.Steps(plan)
    local sigT = {}
    for _, st in ipairs(steps) do sigT[#sigT + 1] = (st.title or "") .. "|" .. (st.sub or "") end
    local sig = tconcat(sigT, "\n")
    PL.steps = steps
    if sig ~= PL.stepSig then
      PL.stepSig = sig
      ver.focus = ver.focus + 1
      publish()
      for _, st in ipairs(steps) do if st.where then PL.Guide(st) break end end
      cardSoon()
      if M.Refresh then M.Refresh(0.2) end
    end
    PL.UpdateCard()
  end
  local Wn = T.Window
  if pageShown() and Wn.Dirty then pcall(Wn.Dirty) end
end
--- une recette vient d'être apprise (NEW_RECIPE_LEARNED / LEARNED_SPELL_IN_SKILL_LINE) : elle est connue
local spellLine
function PL.LearnedRecipe(spell)
  spell = num(spell)
  local d = D()
  if not (spell and d and M.char) then return end
  if not spellLine then
    spellLine = {}
    for line, rows in pairs(d.r) do for _, r in ipairs(rows) do spellLine[r[1]] = line end end
  end
  local line = spellLine[spell]
  if not line then return end
  M.char.known = type(M.char.known) == "table" and M.char.known or {}
  M.char.known[line] = type(M.char.known[line]) == "table" and M.char.known[line] or {}
  M.char.known[line][spell] = 1
  PL.Dirty("known")
end
local function busy() return PL.focus or pageShown() or PL.bankOpen or PL.merchantOpen end
-- événements (enregistrés par Prof:OnEnable sur le module Prof : coupés avec lui) ; handler(self, event, ...)
PL.On = {
  MERCHANT_SHOW = function()
    PL.merchantOpen, PL.bought, PL.pendingBuy = true, 0, {}
    pcall(PL.LearnVendor)
    if PL.focus then PL.Soon(0.1) end
  end,
  MERCHANT_UPDATE = function() if PL.focus and PL.merchantOpen then PL.Soon(0.2) end end,
  MERCHANT_CLOSED = function()
    PL.merchantOpen, PL.pendingBuy = false, {}
    if PL.focus then PL.Soon(0.1) end
  end,
  BANKFRAME_OPENED = function()
    PL.bankOpen, PL.pendingBank = true, {}
    pcall(PL.ScanBank)
    PL.Soon(0.1)
  end,
  BANKFRAME_CLOSED = function()
    PL.bankOpen, PL.pendingBank = false, {}
    PL.ClearGlows()
    if PL.focus then PL.Soon(0.1) end
  end,
  PLAYERBANKSLOTS_CHANGED = function() if PL.bankOpen then pcall(PL.ScanBank) end end,
  BAG_UPDATE_DELAYED = function()
    PL.pendingBuy, PL.pendingBank, PL.pendingAH = {}, {}, {}
    if PL.bankOpen then pcall(PL.ScanBank) end
    if busy() then PL.Dirty("bags") end
  end,
  MAIL_SHOW = function() PL.mailOpen = true end,
  MAIL_INBOX_UPDATE = function() if PL.mailOpen then pcall(PL.ScanMail) end end,
  MAIL_CLOSED = function() PL.mailOpen = false if PL.focus then PL.Soon(0.1) end end,
  TRAINER_SHOW = function() T:After(0.2, function() pcall(PL.LearnTrainer) end) end,
  TRAINER_UPDATE = function() pcall(PL.LearnTrainer) end,
  AUCTION_HOUSE_SHOW = function() PL.ahOpen, PL.ahState = true, nil if PL.focus then PL.Soon(0.2) end end,
  AUCTION_HOUSE_CLOSED = function()
    PL.ahOpen, PL.ahState = false, nil
    ver.prices = ver.prices + 1
    if PL.focus then PL.Soon(0.1) end
  end,
  COMMODITY_SEARCH_RESULTS_UPDATED = function(_, _, id) if PL.ahState then PL.OnCommodityResults(id) end end,
  COMMODITY_PRICE_UPDATED = function(_, _, unit, total) if PL.ahState then PL.OnCommodityPrice(unit, total) end end,
  COMMODITY_PURCHASE_SUCCEEDED = function() if PL.ahState then PL.ahState.state = "done" PL.Soon(0.2) end end,
  COMMODITY_PURCHASE_FAILED = function() if PL.ahState then PL.ahState.state = "gone" PL.Soon(0.2) end end,
  NEW_RECIPE_LEARNED = function(_, _, id) PL.LearnedRecipe(id) end,
  LEARNED_SPELL_IN_SKILL_LINE = function(_, _, id) PL.LearnedRecipe(id) end,
  PLAYER_REGEN_DISABLED = function() PL.HideCard() PL.ClearGlows() end,
  PLAYER_REGEN_ENABLED = function() if PL.focus then PL.Soon(0.5) end end,
  QUEST_LOG_UPDATE = function() if PL.focus or pageShown() then PL.Dirty("bags") end end,
}
-- complément des handlers de Prof (même événement, même module)
PL.After = {
  TRADE_SKILL_SHOW = function() PL.tradeOpen = true if PL.focus then PL.Soon(0.3) end end,
  TRADE_SKILL_CLOSE = function() PL.tradeOpen = false if PL.focus then PL.Soon(0.1) end end,
  TRADE_SKILL_LIST_UPDATE = function() PL.Dirty("known") end,
  SKILL_LINES_CHANGED = function() if busy() then M.profs = M.ScanProfessions() PL.Dirty() end end,
  CHAT_MSG_SKILL = function() if busy() then M.profs = M.ScanProfessions() PL.Dirty() end end,
}
--- appelé par Prof:OnEnable (après ses propres événements)
function PL.OnEnable()
  if type(g("C_AuctionHouse")) ~= "table" then
    for _, ev in ipairs({ "COMMODITY_SEARCH_RESULTS_UPDATED", "COMMODITY_PRICE_UPDATED", "COMMODITY_PURCHASE_SUCCEEDED",
      "COMMODITY_PURCHASE_FAILED" }) do M:UnregisterEvent(ev) end
  end
  PL.RestoreFocus()
  if PL.focus then PL.Soon(2) end
end
function PL.OnDisable()
  if PL.focus then PL.StopFocus("silent") end   -- module coupé = session finie, le trajet reprend
  PL.HideCard()
  PL.ClearGlows()
end

-- ===================================================================== en plus : ce qui aide vraiment
--- compos de métier dans tes sacs qui ne servent à AUCUNE recette non grise de tes métiers (ni à une quête) :
--- { {id, n, sell, ah} } triés par valeur (au plus 4)
function PL.Useless()
  local d = D()
  if not d then return {} end
  local useful = {}
  for _, pr in ipairs(M.profs or {}) do
    for _, r in ipairs(d.r[pr.line] or {}) do
      if r[6] > pr.rank then
        local m = r[10] or {}
        for k = 1, #m - 1, 2 do useful[m[k]] = true end
      end
    end
  end
  local reserve = PL.Reserve()
  local seen, out = {}, {}
  local iter = API.IterBagsShared or API.IterBags
  if not iter then return out end
  local ok, f, st, v = pcall(iter)
  if not ok or type(f) ~= "function" then return out end
  for _, _, it in f, st, v do
    local id = it and num(it.itemID)
    local m = id and d.mats[id]
    if m and not useful[id] and not reserve[id] and not seen[id] and m[3] ~= 1 then
      seen[id] = true
      local n = bagCount(id)
      local ah = PL.PriceOf(id)
      local sell = m[4] or 0
      if n > 0 and (sell > 0 or ah) then out[#out + 1] = { id = id, n = n, sell = sell, ah = ah ~= sell and ah or nil } end
    end
  end
  tsort(out, function(a, b)
    local va, vb = a.n * max(a.sell, a.ah or 0), b.n * max(b.sell, b.ah or 0)
    if va ~= vb then return va > vb end
    return a.id < b.id
  end)
  while #out > 4 do out[#out] = nil end
  return out
end
--- ce que rapportent les objets fabriqués par le plan, revendus (HV TreeAI si l'objet se vend, sinon marchand)
function PL.Resale(plan)
  if not plan then return nil end
  local best
  for _, x in ipairs(plan.res.steps) do
    local item = x.row[2]
    if item and item > 0 then
      local n = x.crafts * max(1, x.row[3] or 1)
      local ah, src = PL.PriceOf(item)
      local liq = (src == "HV" or src == "Auctionator") and PL.LiqText(item) or nil
      local sell = select(11, try(API.ItemInfo, item)) or 0
      sell = num(sell) or 0
      local val = (liq == "se vend bien" and ah) and ah or sell
      if val > 0 and (not best or n * val > best.n * best.val) then
        best = { id = item, n = n, val = val, ah = (liq == "se vend bien") and ah or nil, sell = sell, name = x.name }
      end
    end
  end
  return best
end
--- Secourisme : le meilleur bandage que tu sais faire et le prochain palier utile (Data.Consumables.bandage)
function PL.BandageTip()
  local pr = profOf(129)
  local C = T.Data and T.Data.Consumables
  if not (pr and C and type(C.bandage) == "table") then return nil end
  local cur, nxt
  for _, b in ipairs(C.bandage) do
    local need, heal = tonumber(b[2]) or 0, tonumber(b[3]) or 0
    if need <= pr.rank and (not cur or heal > cur.heal) then cur = { id = b[1], need = need, heal = heal } end
    if need > pr.rank and (not nxt or need < nxt.need) then nxt = { id = b[1], need = need, heal = heal } end
  end
  if not cur and not nxt then return nil end
  local parts = {}
  if cur then parts[#parts + 1] = fmt("ton meilleur bandage : %s (%d PV)", matName(cur.id), cur.heal) end
  if nxt then parts[#parts + 1] = fmt("à %d : %s (%d PV)", nxt.need, matName(nxt.id), nxt.heal) end
  return "Secourisme : " .. tconcat(parts, " · ")
end

-- ===================================================================== onglet « Métiers » (panneau) pendant une session
function PL.TabLines()
  local lines = {}
  if not PL.focus then return lines end
  local first = PL.steps and PL.steps[1]
  local plan = PL.FocusPlan()
  lines[#lines + 1] = { text = first and ("Session métier : " .. first.title) or "Session métier : cible atteinte", prio = 2, pin = true,
    icon = first and first.icon or (plan and PL.LineIcon(plan.line)), onClick = function() if first then PL.RunStep(first) else PL.StopFocus() end end,
    tooltip = { first and first.sub or "La session est finie.", "Clic : " .. ((first and PL.ActionText(first)) or "quitter la session"),
      "Le trajet de quêtes est en pause pendant la session." }, id = "prof:focus" }
  lines[#lines + 1] = { control = true, label = "Quitter la session", text = "Quitter la session métier", onClick = function() PL.StopFocus() end,
    tooltip = "Clic : fin de la session métier, le trajet de quêtes reprend." }
  return lines
end

-- ===================================================================== page « Métiers » de la fenêtre TreeAI
local MODE_FR = { full = "Complet", exact = "Pile-poil" }
local function rankName(pr) local r = E.RankOf(pr.max) return r and r[1] or "" end
local function freshness()
  local st = S()
  local b = type(st.bank) == "table" and st.bank.t
  local m = type(st.mail) == "table" and st.mail.t
  local parts = {}
  parts[#parts + 1] = b and ("banque vue " .. ago(b)) or "banque pas encore vue (passe à la banque pour compter tes compos)"
  if m then parts[#parts + 1] = "courrier vu " .. ago(m) end
  return tconcat(parts, " · ")
end
PL.Freshness = freshness
local function planSub(plan)
  if not plan then return "" end
  local res = plan.res
  local gain = res.reached - plan.skill
  local s = fmt("%s : %d → %d", MODE_FR[plan.mode], plan.skill, res.reached)
  if gain <= 0 then s = fmt("%s : rien à gagner avec ce que tu as", MODE_FR[plan.mode]) end
  s = s .. " · ≈ " .. minutes(plan.time) .. (plan.cost > 0 and (" · " .. money(plan.cost)) or " · rien à acheter")
  return s
end
local function stopText(plan)
  local res = plan.res
  if res.stop == "level" and res.nextRank then
    return fmt("Arrêt à %d : %s se prend au niveau %d.", res.reached, res.nextRank[1], res.nextRank[2])
  elseif res.stop == "mats" then
    return fmt("Arrêt à %d : plus de compos (mode pile-poil : rien d'autre n'est acheté qu'au marchand).", res.reached)
  elseif res.stop == "none" then
    return fmt("Arrêt à %d : aucune recette connue ou apprenable ne monte plus (ouvre ta fenêtre de métier pour que TreeAI lise tes recettes).", res.reached)
  elseif res.stop == "cap" then
    return fmt("Arrêt à %d : compétence maximale.", res.reached)
  end
  return nil
end
local function renderPage(Ly)
  local Wn = T.Window
  if not (M.IsEnabled and M:IsEnabled()) then
    Ly.empty("Le module « Métiers » est coupé (fonction bêta) : « Allumer Métiers », en bas à droite.")
    return
  end
  if not M.profs or #M.profs == 0 then M.profs = M.ScanProfessions() end
  if #M.profs == 0 then Ly.empty("Pas encore de métier : apprends-en un chez un maître de métier (en ville).") return end
  local line = PL.Line()
  local pr = profOf(line)
  if not pr then Ly.empty("Métier introuvable.") return end
  local mode = (PL.focus and PL.focus.line == line and PL.focus.mode) or PL.Mode()
  local plan = PL.Compute(line, mode, mode == "exact" and 300 or PL.Target(line, mode))
  Ly.hero(PL.LineIcon(line), fmt("%s %d/%d (%s)", lineName(line), pr.rank, pr.max, rankName(pr)), planSub(plan), freshness(), nil,
    { "Plan calculé avec tes sacs, ta banque et ton courrier (moins ce que tes quêtes demandent).",
      "Orange = 1 point par fabrication ; jaune / vert : (gris − compétence) / (gris − jaune).",
      "Prix : marchand exact, HV d'après l'historique TreeAI (ou Auctionator), sinon « à farmer »." })
  -- métiers, mode, cible
  local profBtns = {}
  local d = D()
  for _, p in ipairs(M.profs) do
    local k = d and d.lines[p.line]
    if k and k[1] ~= "g" and d.r[p.line] and #d.r[p.line] > 0 and #profBtns < 4 then
      local l = p.line
      profBtns[#profBtns + 1] = { (l == line and "• " or "") .. fmt("%s %d", lineName(l), p.rank), function() PL.SetLine(l) end,
        "Clic : planifier " .. lineName(l) }
    end
  end
  if #profBtns > 1 then Ly.buttons(profBtns, min(#profBtns, 3)) end
  local ask = not (M.db and M.db.askAH == false)
  Ly.buttons({
    { (mode == "full" and "• " or "") .. "Complet", function() M.db.plMode = "full" if PL.focus then PL.focus.mode = "full" end PL.Dirty("focus") end,
      "Atteindre la cible : ce qui manque s'achète (marchand au prix exact, HV, sinon à farmer)." },
    { (mode == "exact" and "• " or "") .. "Pile-poil", function() M.db.plMode = "exact" if PL.focus then PL.focus.mode = "exact" end PL.Dirty("focus") end,
      "Seulement ce que tu as (+ les compos d'un marchand) : le plus de points pour vider tes sacs." },
    { "Demander avant l'HV : " .. (ask and "oui" or "non"), function() M.db.askAH = not ask PL.Dirty("focus") end,
      "Oui : la session te demande avant de prévoir un achat à l'hôtel des ventes." },
  }, 3)
  if mode == "full" then
    local tgt = PL.Target(line, mode)
    Ly.buttons({
      { fmt("Cible : %d", tgt), function() M.CycleTarget(line) PL.Dirty("focus") end, "Clic : palier suivant (75, 150, 225, 300), puis automatique." },
      { "Cible − 5", function() M.char.target[line] = max(pr.rank + 1, tgt - 5) PL.Dirty("focus") end, "Clic : cible 5 points plus bas" },
      { "Cible + 5", function() M.char.target[line] = min(300, tgt + 5) PL.Dirty("focus") end, "Clic : cible 5 points plus haut" },
    }, 3)
  end
  if not plan then Ly.text("Pas de recettes pour ce métier dans les données de TreeAI.", "S", "muted") return end
  if not plan.known then
    Ly.text("Ouvre une fois la fenêtre de ce métier : TreeAI lira tes recettes connues (sinon il suppose celles du maître).", "S", "muted")
  end
  if not next(A("treq")[line] or {}) then
    Ly.text("Passe voir ton maître de métier : TreeAI lira ce qu'il enseigne et à quelle compétence (plan plus juste, recettes orange comprises).", "S", "muted")
  end
  -- étapes
  local steps = (PL.focus and PL.focus.line == line and PL.steps) or PL.Steps(plan)
  if Ly.header(fmt("Étapes (%d)", #steps), "prof:steps", nil, PL.LineIcon(line)) then
    if #steps == 0 then Ly.text(plan.res.reached >= plan.target and "Cible déjà atteinte." or "Rien à faire.", "S", "muted") end
    for i, st in ipairs(steps) do
      if i > 12 then Ly.text(fmt("… et %d étapes de plus.", #steps - 12), "S", "muted") break end
      if Wn.Match(st.title, st.sub) then
        Ly.step({ n = i, title = st.title, sub = st.sub, onClick = function() PL.RunStep(st) end, actionText = PL.ActionText(st) }, i == 1)
      end
    end
    local why = stopText(plan)
    if why then Ly.text(why, "S", "muted") end
  end
  -- achats
  local sp = plan.split
  local nBuy = #sp.vendor + #sp.ah + #sp.farm
  if nBuy > 0 and Ly.header(fmt("À acheter (%d)", nBuy), "prof:buy") then
    for _, v in ipairs(sp.vendor) do
      Ly.row({ text = fmt("%d %s — %s%s", v.buy, matName(v.id), money(v.cost), v.margin > 0 and fmt(" (dont %d de marge)", v.margin) or ""),
        right = "marchand", kind = "info", tooltip = { fmt("%s/u au marchand (prix exact du jeu).", money(v.unit)),
          v.margin > 0 and "Marge : les fabrications jaunes / vertes ne montent pas à chaque fois." or nil } })
    end
    for _, a in ipairs(sp.ah) do
      local liq = PL.LiqText(a.id)
      Ly.row({ text = fmt("%d %s ≈ %s (≤ %s/u)", a.n, matName(a.id), money(a.cost), money(PL.MaxUnit(a.unit))), right = "HV", kind = "info",
        tooltip = { fmt("Prix moyen %s/u (%s).", money(a.unit), (plan.unit[a.id] and plan.unit[a.id][2]) or "HV"), liq and ("Liquidité : " .. liq) or nil,
          "Au-delà du plafond, la session n'achète pas." } })
    end
    for _, f in ipairs(sp.farm) do
      local txt = PL.FarmText(f.id)
      Ly.row({ text = fmt("%d %s — %s", f.n, matName(f.id), txt or "à farmer ou à demander à tes amis"), right = "à farmer", kind = "info" })
    end
    if plan.cost > 0 then
      Ly.text(fmt("Total : %s (marchand %s · HV %s)", money(plan.cost), money(sp.vendorCost), money(floor(sp.ahCost))), "S", "gold")
      local gold = num(API.Money and API.Money()) or 0
      if plan.cost > gold then Ly.text(fmt("Il te faut %s : tu as %s. Essaie « Pile-poil » ou une cible plus basse.", money(plan.cost), money(gold)), "S", "prio3") end
    end
  end
  -- tes compos
  local used = {}
  for id, n in pairs(plan.use or plan.res.use) do used[#used + 1] = { id = id, n = n } end
  tsort(used, function(a, b) if a.n ~= b.n then return a.n > b.n end return a.id < b.id end)
  local rsv = {}
  for id, n in pairs(plan.reserve) do rsv[#rsv + 1] = { id = id, n = n } end
  tsort(rsv, function(a, b) return a.id < b.id end)
  if (#used > 0 or #rsv > 0) and Ly.header("Tes compos", "prof:have", nil, nil, true) then
    for i, u in ipairs(used) do
      if i > 6 then break end
      local h = plan.have
      Ly.row({ text = fmt("%d %s (sacs %d · banque %d · courrier %d)", u.n, matName(u.id), h.bags[u.id] or 0, h.bank[u.id] or 0, h.mail[u.id] or 0),
        kind = "info" })
    end
    for _, r in ipairs(rsv) do
      Ly.row({ text = fmt("Gardé pour « %s » : %d %s", cut(plan.why[r.id] or "une quête", 30), r.n, matName(r.id)), kind = "info",
        tooltip = "Ta quête en a besoin : TreeAI ne le compte pas dans tes compos." })
    end
  end
  -- en plus
  local extra = {}
  local junk = PL.Useless()
  if #junk > 0 then
    local parts, total = {}, 0
    for i, j in ipairs(junk) do
      if i <= 3 then parts[#parts + 1] = fmt("%d %s", j.n, matName(j.id)) end
      total = total + j.n * j.sell
    end
    extra[#extra + 1] = { text = "Vends (inutile pour tes métiers) : " .. tconcat(parts, ", ") .. (total > 0 and (" — " .. money(total) .. " au marchand") or ""),
      kind = "info", tooltip = "Aucune recette non grise de tes métiers ne s'en sert, aucune quête du journal non plus." }
  end
  local rs = PL.Resale(plan)
  if rs then
    extra[#extra + 1] = { text = rs.ah and fmt("Tes %d %s se revendent ≈ %s l'unité à l'HV (marchand : %s)", rs.n, cut(rs.name, 26), money(rs.ah), money(rs.sell))
      or fmt("Tes %d %s : %s l'unité au marchand", rs.n, cut(rs.name, 26), money(rs.sell)), kind = "info" }
  end
  if line == 129 or profOf(129) then
    local bt = PL.BandageTip()
    if bt then extra[#extra + 1] = { text = bt, kind = "info" } end
  end
  for _, f in ipairs(sp.farm) do
    if not profOf(393) then
      local friend = PL.FriendsWith(393)
      if friend then extra[#extra + 1] = { text = fmt("Ton ami %s peut dépecer pour toi.", friend), kind = "info" } break end
    end
  end
  if #extra > 0 and Ly.header("En plus", "prof:extra") then
    for _, e in ipairs(extra) do Ly.row(e) end
  end
end
local Wn = T.Window
if Wn and type(Wn.AddPage) == "function" then
  Wn.AddPage({ id = "prof", label = "Métiers", icon = ICON .. "Trade_LeatherWorking", title = "Métiers",
    desc = "Monter tes métiers vite : quoi créer, combien acheter, où aller",
    sub = function()
      if PL.focus then return "Session métier en cours : le trajet de quêtes est en pause" end
      return "Quoi créer, combien acheter (sacs + banque + courrier), où aller"
    end,
    badge = function() return PL.focus and "!" or nil end,
    -- (4.1 LAYOUT) en tête : les 1 à 3 actions utiles (agent CITYRUN : « Courses en ville », s'il est là)
    top = function()
      local out = {}
      local CR = T.CityRun
      if type(CR) == "table" and type(CR.Start) == "function" then
        out[#out + 1] = { "Courses en ville", function() pcall(CR.Start) end,
          "Clic : banque, marchands, maître, HV dans le meilleur ordre (la boussole te guide)" }
      end
      if M.IsEnabled and M:IsEnabled() and not PL.focus then
        out[#out + 1] = { "Me concentrer sur les métiers", function() PL.StartFocus(PL.Line()) end,
          "Clic : session métier (trajet de quêtes en pause, la boussole te guide : marchand, banque, maître, créer)" }
      end
      return out
    end,
    render = renderPage,
    action = function()
      if not (M.IsEnabled and M:IsEnabled()) then
        return "Allumer Métiers", function() T:SetModuleEnabled("Prof", true) end, "Clic : allumer le module Métiers (sans /reload)"
      end
      if PL.focus then return "Quitter la session", function() PL.StopFocus() end, "Clic : fin de la session métier, le trajet de quêtes reprend" end
      return "Lancer la session métier", function() PL.StartFocus(PL.Line()) end,
        "Clic : met le trajet de quêtes en pause, la carte et la boussole te guident (marchand, banque, maître, créer)"
    end,
    status = function() return PL.focus and "Session métier en cours" or freshness() end,
  })
  -- après « HV » dans la navigation (et donc dans la pastille)
  local pages, at, from = Wn.PAGES, nil, nil
  for i, p in ipairs(pages) do
    if p.id == "hv" then at = i end
    if p.id == "prof" then from = i end
  end
  if at and from and from ~= at + 1 then
    local pg = table.remove(pages, from)
    if from < at then at = at - 1 end
    table.insert(pages, at + 1, pg)
  end
end

-- ===================================================================== résumé (TreeAI Coach) + commande
function PL.Summary()
  local plan = PL.focus and PL.FocusPlan()
  return { focus = PL.focus and { line = PL.focus.line, mode = PL.focus.mode, since = PL.focus.since } or nil,
    plan = plan and { line = plan.line, mode = plan.mode, skill = plan.skill, target = plan.target, reached = plan.res.reached,
      cost = plan.cost, minutes = floor(plan.time / 60 + 0.5), crafts = plan.res.crafts } or nil,
    bank = type(S().bank) == "table" and S().bank.t or nil }
end
--- /tai metiers focus | pilepoil | complet | page
function PL.Slash(a)
  if a == "focus" or a == "session" then PL.ToggleFocus() return true end
  if a == "pilepoil" or a == "pile-poil" then M.db.plMode = "exact" PL.Dirty("focus") return true end
  if a == "complet" then M.db.plMode = "full" PL.Dirty("focus") return true end
  if a == "page" and T.Window and T.Window.Open then T.Window.Open("prof") return true end
  return false
end
