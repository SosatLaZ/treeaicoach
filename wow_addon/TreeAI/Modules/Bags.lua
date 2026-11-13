-- TreeAI — module « Sacs » (agent BAGS, 5.0) : LE cerveau de chaque décision d'objet. Une seule question, une seule
-- réponse pour tout l'addon : T.Bags.Verdict(itemID, sac, case) → { action, why, keep, surplus, value, … }.
--   action : "keep" (garde) · "sell" (marchand) · "ah" (hôtel des ventes) · "bank" (banque) · "use" (à utiliser /
--            apprendre) · "destroy?" (ne vaut rien : à jeter SEULEMENT si tu manques de place — jamais détruit par TreeAI).
-- Règles (dans cet ordre) :
--   1. objet pas encore chargé par le jeu → « inconnu : je garde » ;
--   2. les GARDIENS disent ce qu'il faut garder (T.Bags.RegisterKeeper(nom, fn(itemID) → combien, pourquoi)). Ceux
--      d'ici : objets de quête (journal + suite connue), tes marques « garder », tes consommables (potions de soins /
--      mana, nourriture, boissons, bandages : la MEILLEURE que tu possèdes n'est jamais vendue ; une moins bonne n'est
--      « en trop » que si les meilleures suffisent déjà), munitions de ton arme, nourriture du familier (son régime),
--      composants de ta classe, clés, outils (métiers, canne à pêche, pierre de foyer), sacs, monture, équipement que
--      tu portes en alternance / d'un set, recettes à apprendre. Équipement (GEAR) et Métiers (PROF) ajoutent les leurs ;
--   3. ce qu'un gardien réclame n'est JAMAIS proposé à la vente ; sans avis « en trop » de quelqu'un → on garde ;
--   4. le reste : marchand ou HV selon l'espérance honnête (Auction.PostPrice : commission, dépôt, risque d'invendu) ;
--      sans prix scanné, un objet échangeable de valeur (vert+, compo, recette) est montré « HV » et jamais bradé d'un clic.
-- Autonomie dans les règles du jeu : carte sous la fenêtre du marchand (« Vendre 14 objets · 1 po 20 pa », UN clic,
-- par petits paquets revérifiés, jamais en combat, rachat possible), carte sous la banque (« Ranger »), alerte avant
-- que les sacs soient pleins (le moins cher est seulement MONTRÉ : détruire est protégé et irréversible), pastilles
-- sur les boutons de sac du jeu (pièce = vendre, HV, banque, cadenas = garder), une ligne d'infobulle, « Il te manque »
-- (T.Bags.Shopping() : Courses en ville et liste d'achats de l'HV), page « Sacs » de la fenêtre.
-- Perf : rien n'est calculé sur un événement de sac — le relevé des sacs et les avis sont refaits À LA DEMANDE (clé :
-- API.BagGen), un avis par objet gardé tant que son compte ne change pas, pastilles retouchées case par case, gros
-- recalcul découpé (T:Spread). Rien au repos. Lua 5.1, aucune globale, rien dans le chat.
local tonumber = function(v) return _G.tonumber(v) end   -- tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local API = T.API or {}

local M = T:RegisterModule("Bags", {
  title = "Sacs",
  description = "Assistant d'inventaire : quoi garder, vendre, mettre à l'HV ou à la banque, et ce qu'il te manque.",
  defaults = { enabled = true, card = true, repair = true, overlays = true, keepIcon = false, legend = true, tooltip = true,
    warn = true, warnFree = 2, stock = true, ahUnknown = false },
  charDefaults = { marks = {}, worn = {}, vend = {}, diet = false },
  options = {
    { key = "card", type = "toggle", label = "Marchand : carte « Vendre N objets » (un clic, rachat possible)", section = "Marchand" },
    { key = "repair", type = "toggle", label = "Marchand : réparer dans le même clic", section = "Marchand" },
    { key = "ahUnknown", type = "toggle", label = "Sans prix d'HV scanné : vendre quand même au marchand (coupé d'office)", section = "Marchand" },
    { key = "overlays", type = "toggle", label = "Pastilles sur les sacs : pièce = vendre, HV, banque", section = "Sacs" },
    { key = "keepIcon", type = "toggle", label = "Pastille cadenas sur ce qu'il faut garder", section = "Sacs" },
    { key = "legend", type = "toggle", label = "Légende des pastilles au-dessus des sacs", section = "Sacs" },
    { key = "tooltip", type = "toggle", label = "Infobulle : « Garde / Vends / HV » et pourquoi", section = "Sacs" },
    { key = "warn", type = "toggle", label = "Prévenir avant que les sacs soient pleins", section = "Sacs" },
    { key = "warnFree", type = "range", label = "… quand il reste (places)", min = 1, max = 8, step = 1 },
    { key = "stock", type = "toggle", label = "« Il te manque » : potions, nourriture, munitions (en ville / chez un marchand)", section = "Stock" },
  },
})
T.Bags = M
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Bags = true end   -- sûr : lit les sacs, n'agit que sur ton clic
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[4]   -- « Sacs, or, métiers »
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Bags" then has = true end end
    if not has then table.insert(fam.mods, 1, "Bags") end
  end
end

local floor, max, min, huge = math.floor, math.max, math.min, math.huge
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local ICON = "Interface\\Icons\\"

-- ===================================================================== utilitaires
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function meth(o, k, ...) if type(o) == "table" and type(o[k]) == "function" then return try(o[k], o, ...) end end
-- valeurs secrètes (combat) : jamais comparées ni converties ; le test du jeu est lu une fois (appel direct, sans pcall)
local ISV = type(g("issecretvalue")) == "function" and g("issecretvalue") or nil
local function num(v) if v == nil or (ISV and ISV(v)) then return nil end return tonumber(v) end
local function pub(v) if v ~= nil and ISV and ISV(v) then return nil end return v end
local GT = type(g("GetTime")) == "function" and g("GetTime") or nil
local function now() return GT and num(GT()) or 0 end
local function epoch() return num(try(g("time"))) or 0 end
local function opt(k) return not (M.db and M.db[k] == false) end          -- options allumées d'office
local function optOn(k) return M.db ~= nil and M.db[k] == true end        -- options coupées d'office
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function class()
  local c = T.PlayerClass and T:PlayerClass()
  if type(c) == "string" then return c end
  local _, f = try(g("UnitClass"), "player")
  return pub(f)
end
local function money(c)
  if T.Fmt and T.Fmt.Money then return T.Fmt.Money(c) end
  c = floor(tonumber(c) or 0)
  local po, pa, pc = floor(c / 10000), floor(c / 100) % 100, c % 100
  if po > 0 then return pa > 0 and (po .. " po " .. pa .. " pa") or (po .. " po") end
  if pa > 0 then return pa .. " pa" end
  return pc .. " pc"
end
M.Money = money
local function cut(s, n) s = tostring(s or "") if T.Cut then return T.Cut(s, n or 30, "…") end return s end
local function note(msg) if T.ClickNote then T:ClickNote(msg) end end
local function mod(name) local m = T.modules and T.modules[name] if type(m) == "table" and m._active then return m end return nil end
local function after(sec, fn) if T.After then T:After(sec, fn) else fn() end end
local function plural(n, one, many) return n .. " " .. ((n > 1) and many or one) end
local anyBagShown                                           -- (défini avec les pastilles)

-- ===================================================================== objets (données fixes, gardées)
local INFO = {}
--- { id, name, q, req, stack, loc, sell, cls, sub, bind } ou nil si le jeu n'a pas (encore) l'objet
local function itemInfo(id)
  local e = INFO[id]
  if e then return e end
  local name, link, q, _, req, _, _, stack, loc, _, sell, cls, sub, bind = try(API.ItemInfo or g("GetItemInfo"), id)
  if type(pub(name)) ~= "string" then return nil end
  e = { id = id, name = name, link = pub(link), q = num(q) or 1, req = num(req) or 0, stack = num(stack) or 1,
    loc = (type(loc) == "string" and loc ~= "") and loc or nil, sell = num(sell) or 0, cls = num(cls), sub = num(sub), bind = num(bind) }
  INFO[id] = e
  return e
end
M.ItemInfo = itemInfo
local function itemName(id)
  local it = itemInfo(id)
  return (it and it.name) or (API.ItemName and API.ItemName(id)) or ("objet " .. tostring(id))
end

-- ===================================================================== relevé des sacs (à la demande)
local CC = g("C_Container")
local function bagSlots(bag)
  if API.BagSlots then return API.BagSlots(bag) or 0 end
  return num(try((CC and CC.GetContainerNumSlots) or g("GetContainerNumSlots"), bag)) or 0
end
--- lecture directe d'une case : id, nombre, qualité, verrouillée, sans valeur, liée, lien
local function readSlot(bag, slot)
  if CC and CC.GetContainerItemInfo then
    local i = try(CC.GetContainerItemInfo, bag, slot)
    if type(i) ~= "table" then return nil end
    local id = num(i.itemID)
    if not id and type(i.hyperlink) == "string" then id = tonumber(i.hyperlink:match("item:(%d+)")) end
    return id, num(i.stackCount) or 1, num(i.quality), pub(i.isLocked) and true or false, pub(i.hasNoValue) and true or false,
      pub(i.isBound) and true or false, pub(i.hyperlink)
  end
  local tex, count, locked, quality, _, _, link, _, noValue, id, bound = try(g("GetContainerItemInfo"), bag, slot)
  if not tex and not link then return nil end
  id = num(id) or (type(link) == "string" and tonumber(link:match("item:(%d+)"))) or nil
  return id, num(count) or 1, num(quality), locked and true or false, noValue and true or false, bound and true or false, link
end
M.ReadSlot = readSlot
--- lecture minimale pour le relevé : id, nombre, liée (un seul appel protégé par case)
local function slotFast(bag, slot)
  if CC and CC.GetContainerItemInfo then
    local ok, i = pcall(CC.GetContainerItemInfo, bag, slot)
    if not ok or type(i) ~= "table" then return nil end
    local id, c, b = i.itemID, i.stackCount, i.isBound
    if ISV and (ISV(id) or ISV(c) or ISV(b)) then return nil end
    if not id then
      local link = pub(i.hyperlink)
      id = type(link) == "string" and tonumber(link:match("item:(%d+)")) or nil
    end
    return id, c or 1, b and true or false
  end
  local id, c, _, _, _, bound = readSlot(bag, slot)
  return id, c, bound
end

local S = { seen = -1, id = {}, n = {}, bound = {}, count = {}, list = {}, first = {}, dirty = {}, touch = {}, free = 0, total = 0, all = true }
local V = {}          -- itemID → avis
local PLAN = {}       -- catégorie → plan des consommables
local C = { at = -1, gen = 0 }   -- contexte (niveau, journal, HV, gardiens, marques) : change → tous les avis sont refaits
local CAT, PET        -- tables des consommables / de la nourriture de familier (construites une fois)
local function bagGen() return API.BagGen and API.BagGen() or 0 end

local function dropItem(id)
  V[id] = nil
  S.touch[id] = true
  local c = CAT and CAT[id]
  for _, cat in ipairs({ c and c.cat or false, (PET and PET[id]) and "pet" or false }) do
    local p = cat and PLAN[cat]
    if p then
      -- le compte d'une potion change l'avis des autres potions de sa catégorie
      for other in pairs(p.ids or {}) do V[other] = nil S.touch[other] = true end
      PLAN[cat] = nil
    end
  end
  if c and c.cat == "ammo" then PLAN.ammo = nil end
  M._shop = nil
end
--- change de contexte : tous les avis (ou celui d'un objet) sont à refaire ; les pastilles suivront
function M.Invalidate(id)
  id = tonumber(id)
  if id then
    dropItem(id)
    for key, sid in pairs(S.id) do if S.touch[sid] then S.dirty[key] = true end end
    S.touch = {}
  else
    V, PLAN = {}, {}
    C.gen = C.gen + 1
    C.targets, C.profs, C.usable, M._shop = nil, nil, nil, nil
    S.all = true
  end
  M._lists = nil
end

local function snapshot()
  local gen = bagGen()
  if S.seen == gen then return S end
  local oid, on = S.id, S.n
  local nid, nn, nb, count, list, first, k = {}, {}, {}, {}, {}, {}, 0
  for bag = 0, (API.NumBags and API.NumBags() or 4) do
    for slot = 1, bagSlots(bag) do
      local id, c, bound = slotFast(bag, slot)
      if id then
        local key = bag * 1000 + slot
        nid[key], nn[key] = id, c
        if bound then nb[key] = true end
        count[id] = (count[id] or 0) + c
        if not first[id] then first[id] = key end
        k = k + 1
        list[k] = key
      end
    end
  end
  for id, c in pairs(count) do if S.count[id] ~= c then dropItem(id) end end
  for id in pairs(S.count) do if not count[id] then dropItem(id) end end
  -- cases à redessiner : celles qui ont changé + toutes les piles des objets dont l'avis change
  for key, id in pairs(nid) do if oid[key] ~= id or on[key] ~= nn[key] or S.touch[id] then S.dirty[key] = true end end
  for key in pairs(oid) do if not nid[key] then S.dirty[key] = true end end
  S.touch = {}
  S.id, S.n, S.bound, S.count, S.list, S.first = nid, nn, nb, count, list, first
  if API.FreeSlots then S.free, S.total = API.FreeSlots() end
  S.seen = gen
  M._lists = nil
  return S
end
M.Snapshot = snapshot
--- relire les sacs à la prochaine demande, même sans événement (ouverture du marchand / de la banque, ton clic)
function M.Rescan() S.seen = -1 M._lists, M._shop = nil, nil end
--- combien tu en as dans tes sacs
function M.Count(id) return snapshot().count[id] or 0 end

--- contexte : niveau et dernier scan de l'HV (un changement refait tous les avis) ; journal de quêtes (chaque monstre
--- tué le fait bouger) : seuls les avis dont la réponse « quête » a changé sont refaits. Au plus 1 test par image.
local questSig
local function context()
  local t = now()
  if C.at == t then return C end
  C.at = t
  local A = mod("Auction")
  local ag = A and ((A.lastScan and A.lastScan.t) or (A.db and A.db.lastScanT)) or 0
  local lv = level()
  if ag ~= C.ag or lv ~= C.level then
    local first = C.level == nil
    C.ag, C.level = ag, lv
    if not first then M.Invalidate() end
  end
  local H = mod("Highlight")
  local D = H and type(H.Data) == "function" and H.Data() or nil
  local qg = (type(D) == "table" and D.ver) or (T.QuestGen and T:QuestGen()) or 0
  if qg ~= C.qg then
    local first = C.qg == nil
    C.qg = qg
    if not first and questSig then
      for id, v in pairs(V) do
        if questSig(id) ~= v.qsig then
          V[id] = nil
          for key, sid in pairs(S.id) do if sid == id then S.dirty[key] = true end end
          M._lists = nil
        end
      end
    end
  end
  return C
end

-- ===================================================================== consommables : catégories, stocks cibles
local CATS = { "hpot", "mpot", "food", "water", "bandage" }
local NAME = {   -- { un, plusieurs, « ta meilleure … » }
  hpot = { "potion de soins", "potions de soins", "ta meilleure potion de soins" },
  mpot = { "potion de mana", "potions de mana", "ta meilleure potion de mana" },
  food = { "repas", "repas", "ton meilleur repas" },
  water = { "boisson", "boissons", "ta meilleure boisson" },
  bandage = { "bandage", "bandages", "ton meilleur bandage" },
  arrow = { "flèche", "flèches" }, bullet = { "balle", "balles" },
  pet = { "portion pour ton familier", "portions pour ton familier" },
}
M.NAME = NAME
-- repli quand rien n'est connu (ni sacs, ni marchand vu, ni HV) : les objets classiques, du moins bon au meilleur
local CLASSIC = { hpot = { 118, 858, 929, 1710, 3928, 13446 }, mpot = { 2455, 3385, 3827, 6149, 13443, 13444 } }
local function buildTables()
  if CAT then return end
  CAT, PET = {}, {}
  local D = T.Data and T.Data.Consumables
  for _, cat in ipairs({ "hpot", "mpot", "food", "water", "bandage", "ammo" }) do
    for _, r in ipairs(type(D) == "table" and type(D[cat]) == "table" and D[cat] or {}) do
      if type(r) == "table" and r[1] and not CAT[r[1]] then
        CAT[r[1]] = { cat = cat, req = tonumber(r[2]) or 1, val = tonumber(r[3]) or 0, label = r[4] or "" }
      end
    end
  end
  for _, r in ipairs(T.Data and type(T.Data.PetFood) == "table" and T.Data.PetFood or {}) do
    if type(r) == "table" and r[1] then PET[r[1]] = r end
  end
end
M.CatOf = function(id) buildTables() return CAT[id] end

-- 2 = classe à mana (boit, potions de mana) ; 1 = a du mana mais ne s'arrête pas pour boire (chasseur) ; rien = pas de mana
local MANA = { MAGE = 2, PRIEST = 2, WARLOCK = 2, DRUID = 2, SHAMAN = 2, PALADIN = 2, HUNTER = 1 }
--- stocks cibles par classe / niveau : want = ce qu'il te faut (« Il te manque »), hold = au-delà, une moins bonne est en trop
local function targets()
  if C.targets then return C.targets end
  local cls, lvl = class(), level()
  local mana = MANA[cls] or 0
  local t = {
    hpot = { want = lvl >= 10 and 5 or 0, hold = 5 },
    mpot = { want = (mana == 2 and lvl >= 15) and 5 or 0, hold = mana > 0 and 5 or 0 },
    food = { want = lvl >= 5 and 20 or 10, hold = 20 },
    water = { want = (mana == 2 and lvl >= 5) and 20 or 0, hold = mana > 0 and 20 or 0 },
    bandage = { want = 0, hold = 20 },
    pet = { want = 10, hold = 40 },
  }
  C.targets = t
  return t
end
M.Targets = targets

--- métiers connus : { [ligne] = rang } ou nil (inconnu : jamais lu)
local function profRanks()
  if C.profs ~= nil and now() - (C.profsAt or 0) < 60 and now() >= (C.profsAt or 0) then return C.profs or nil end
  C.profsAt = now()
  local P = T.modules and T.modules.Prof
  local list = type(P) == "table" and P.profs or nil
  if (type(list) ~= "table" or #list == 0) and type(P) == "table" and type(P.ScanProfessions) == "function" then
    list = try(P.ScanProfessions)                           -- module Métiers coupé : la lecture du jeu (au plus 1 / min)
  end
  if type(list) ~= "table" then C.profs = false return nil end   -- pas lu : on ne sait pas (une liste vide = aucun métier)
  local map = {}
  for _, p in ipairs(list) do if type(p) == "table" and p.line then map[p.line] = tonumber(p.rank) or 0 end end
  C.profs = map
  return map
end
local function lineName(line)
  local D = T.Data and T.Data.Recipes
  local l = D and D.lines and D.lines[line]
  return (type(l) == "table" and l[2]) or "ce métier"
end

--- plan d'une catégorie « pour toi » : quoi garder de chaque objet possédé, et pourquoi
local function planCat(cat)
  local p = PLAN[cat]
  if p then return p end
  buildTables()
  local s, tg, lvl = snapshot(), targets()[cat], level()
  p = { ids = {}, keep = {}, why = {}, opin = {}, have = 0, want = tg.want, hold = tg.hold }
  PLAN[cat] = p
  local usable = {}
  local fa
  if cat == "bandage" then
    -- rang de Secourisme s'il a été lu ; pas lu = on ne sait pas → un bandage n'est JAMAIS déclaré inutile pour ça
    local ranks = profRanks()
    fa = ranks and ranks[129] or nil
  end
  for id, n in pairs(s.count) do
    local c = CAT[id]
    if c and c.cat == cat then
      p.ids[id] = true
      if c.label == "conj" then
        p.keep[id], p.why[id] = huge, "invoqué : disparaît à la déconnexion"
        p.have = p.have + n
      elseif cat == "bandage" and fa and c.req > fa then
        p.keep[id], p.why[id] = huge, fmt("utilisable à %d en Secourisme (tu es à %d)", c.req, fa)
      elseif cat ~= "bandage" and c.req > lvl then
        if c.req <= lvl + 5 then p.keep[id], p.why[id] = huge, fmt("utilisable au niveau %d", c.req)
        else p.keep[id], p.opin[id] = 0, fmt("pas utilisable avant le niveau %d", c.req) end
      else
        usable[#usable + 1] = id
      end
    end
  end
  tsort(usable, function(a, b)
    if CAT[a].val ~= CAT[b].val then return CAT[a].val > CAT[b].val end
    return a < b
  end)
  local nm = NAME[cat]
  local better = 0
  for i, id in ipairs(usable) do
    local n = s.count[id]
    p.have = p.have + n
    if tg.hold <= 0 then
      p.keep[id], p.opin[id] = 0, "tu n'as pas de mana : inutile pour toi"
    elseif i == 1 then
      -- la meilleure que tu possèdes : JAMAIS en trop (l'incident des potions de soins vendues)
      p.best, p.keep[id] = id, huge
      p.why[id] = n >= tg.hold and fmt("%s (tu en as %d)", nm[3], n) or fmt("%d %s pour toi (tu en as %d)", tg.hold, nm[2], n)
    elseif better >= tg.hold then
      -- une moins bonne n'est en trop que si les meilleures suffisent déjà
      p.keep[id] = 0
      p.opin[id] = fmt("moins bien que %s : tu en as déjà %d (%d suffisent)", itemName(p.best), better, tg.hold)
    else
      p.keep[id] = huge
      p.why[id] = fmt("%d %s pour toi (tu n'en as que %d de mieux)", tg.hold, nm[2], better)
    end
    better = better + n
  end
  return p
end
M.PlanCat = planCat

--- munitions : le type de ton arme à distance ("arrow" / "bullet", sous-classe de munition) ou nil
local function ammoKind()
  local id = num(try(g("GetInventoryItemID"), "player", 18))
  local it = id and itemInfo(id)
  if it and it.cls == 2 then
    if it.sub == 2 or it.sub == 18 then return "arrow", 2 end
    if it.sub == 3 then return "bullet", 3 end
  end
  return nil
end
local function ammoSub(id, it)
  if it and it.cls == 6 then return it.sub end
  local c = CAT[id]
  if c and c.cat == "ammo" then return (c.label == "arrow" and 2) or (c.label == "bullet" and 3) or nil end
  return nil
end
local function planAmmo()
  local p = PLAN.ammo
  if p then return p end
  buildTables()
  local s = snapshot()
  local kind, sub = ammoKind()
  p = { kind = kind, sub = sub, alt = {}, have = 0, want = 0 }
  PLAN.ammo = p
  for id, n in pairs(s.count) do
    local it = itemInfo(id)
    if it and it.cls == 2 then
      if it.sub == 2 or it.sub == 18 then p.alt[2] = it.name elseif it.sub == 3 then p.alt[3] = it.name end
    end
    local a = ammoSub(id, it)
    if a and a == sub and (not it or it.req <= level()) then p.have = p.have + n end
  end
  if kind and class() == "HUNTER" and level() >= 10 then
    local want = 400
    local H = T.modules and T.modules.Hunter
    local rate = H and type(H.AmmoRate) == "function" and num(try(H.AmmoRate)) or nil
    if rate then want = min(2000, max(400, math.ceil(rate * 1.5 / 200) * 200)) p.rate = rate end
    p.want = want
  end
  return p
end
M.PlanAmmo = planAmmo

--- familier du chasseur : son régime (lu, sinon le dernier connu) et son niveau
local function petDiet()
  if class() ~= "HUNTER" then return nil end
  local H = T.modules and T.modules.Hunter
  if pub(try(g("UnitExists"), "pet")) and type(H) == "table" and type(H.GetDiet) == "function" then
    local d = try(H.GetDiet, H)
    if type(d) == "table" and next(d) then
      if M.char then
        local keep = {}
        for k in pairs(d) do keep[k] = true end
        M.char.diet = keep
      end
      return d, num(try(g("UnitLevel"), "pet"))
    end
  end
  local saved = M.char and M.char.diet
  if type(saved) == "table" and next(saved) then return saved, nil end
  return nil
end
local DIET_FR = { meat = "viande", fish = "poisson", bread = "pain", cheese = "fromage", fruit = "fruits", fungus = "champignons" }
local function planPet()
  local p = PLAN.pet
  if p then return p end
  buildTables()
  local s = snapshot()
  local diet, plvl = petDiet()
  local tg = targets().pet
  p = { ids = {}, keep = {}, why = {}, have = 0, want = diet and tg.want or 0, diet = diet, level = plvl or level() }
  PLAN.pet = p
  if not diet then return p end
  local eat = {}
  for id in pairs(s.count) do
    local f = PET[id]
    if f then
      p.ids[id] = true
      if diet[f[2]] and (tonumber(f[3]) or 0) >= p.level - 30 then eat[#eat + 1] = id end
    end
  end
  tsort(eat, function(a, b) if PET[a][3] ~= PET[b][3] then return PET[a][3] > PET[b][3] end return a < b end)
  local better = 0
  for _, id in ipairs(eat) do
    local n = s.count[id]
    p.have = p.have + n
    if better < tg.hold then
      p.keep[id], p.why[id] = huge, fmt("ton familier en mange (%s)", DIET_FR[PET[id][2]] or PET[id][2])
    end
    better = better + n
  end
  return p
end
M.PlanPet = planPet

-- ===================================================================== gardiens
local KEEPERS, ORDER, EXT = {}, {}, {}
--- T.Bags.RegisterKeeper(nom, fn) : fn(itemID, infos, combien_en_sac, sac, case) →
---   n > 0, "pourquoi"            : garde n (math.huge = tout) ; « où » en 3e retour : "bank" (à ranger) / "use" (à utiliser)
---   0, "pourquoi"                : je connais cet objet, il est EN TROP pour moi (avis explicite → vente possible)
---   nil (ou 0 sans raison)       : pas d'avis (sans avis « en trop » de quelqu'un, l'objet est gardé)
--- 4e retour vrai = « je n'ai pas d'avis sur le reste » (un gardien qui réclame 6 sur 20 sans dire que 14 sont en trop).
--- Une erreur du gardien = il réclame tout (dans le doute, on ne vend pas).
function M.RegisterKeeper(name, fn)
  if type(name) ~= "string" or name == "" or type(fn) ~= "function" then return false end
  if not KEEPERS[name] then ORDER[#ORDER + 1] = name end
  KEEPERS[name] = fn
  local low = name:lower()
  if low:find("gear", 1, true) or low:find("quip", 1, true) then EXT.gear = true end
  if low:find("prof", 1, true) or low:find("tier", 1, true) or low:find("cityrun", 1, true) then EXT.prof = true end
  M.Invalidate()
  return true
end
function M.UnregisterKeeper(name)
  if not KEEPERS[name] then return false end
  KEEPERS[name] = nil
  for i, n in ipairs(ORDER) do if n == name then table.remove(ORDER, i) break end end
  EXT.gear, EXT.prof = nil, nil
  for _, n in ipairs(ORDER) do
    local low = n:lower()
    if low:find("gear", 1, true) or low:find("quip", 1, true) then EXT.gear = true end
    if low:find("prof", 1, true) or low:find("tier", 1, true) or low:find("cityrun", 1, true) then EXT.prof = true end
  end
  M.Invalidate()
  return true
end
function M.Keepers() local out = {} for i, n in ipairs(ORDER) do out[i] = n end return out end

local BUILTIN, BORDER, PRE = {}, {}, {}
local function builtin(name, fn) BUILTIN[name] = fn BORDER[#BORDER + 1] = name end
-- un gardien d'ici n'est appelé que s'il peut avoir un avis sur cet objet (test sans appel protégé)
PRE["marque"] = function(id) return M.char and type(M.char.marks) == "table" and M.char.marks[id] ~= nil end
PRE["consommable"] = function(id) local c = CAT[id] return c ~= nil and c.cat ~= "ammo" end
PRE["munitions"] = function(id, it) return it.cls == 6 or (CAT[id] ~= nil and CAT[id].cat == "ammo") end
PRE["familier"] = function(id) return PET[id] ~= nil end
PRE["équipement"] = function(_, it) return (it.cls == 2 or it.cls == 4) and it.loc ~= nil end
PRE["recette"] = function(_, it) return it.cls == 9 end
PRE["métiers"] = function(_, it) return it.cls == 7 or it.cls == 5 or it.cls == 0 end

-- tes marques : « garder », « banque », « vendre »
function M.MarkOf(id) local m = M.char and type(M.char.marks) == "table" and M.char.marks[id] return m or nil end
--- marque un objet : "keep" (jamais proposé à la vente), "bank", "sell" (toujours proposé), nil = retire la marque
function M.Mark(id, kind)
  id = tonumber(id)
  if not (id and M.char) then return false end
  M.char.marks = type(M.char.marks) == "table" and M.char.marks or {}
  if kind ~= "keep" and kind ~= "bank" and kind ~= "sell" then kind = nil end
  M.char.marks[id] = kind
  M.Invalidate(id)
  M.Refresh()
  return true
end
builtin("marque", function(id)
  local m = M.MarkOf(id)
  if m == "keep" then return huge, "tu l'as marqué « garder »" end
  if m == "bank" then return huge, "tu l'as marqué « banque »", "bank" end
  if m == "sell" then return 0, "tu l'as marqué « vendre »" end
end)

-- quêtes : journal (Highlight : besoin, à utiliser, prêt, suite connue), objets de classe « Quête »
local function questSlot(bag, slot)
  if not (bag and slot) then return false end
  local f = (CC and CC.GetContainerItemQuestInfo) or g("GetContainerItemQuestInfo")
  local a, b = try(f, bag, slot)
  if type(a) == "table" then return (pub(a.isQuestItem) or pub(a.questID)) and true or false end
  return (pub(a) or pub(b)) and true or false
end
builtin("quête", function(id, it, _, bag, slot)
  local H = mod("Highlight")
  if H and type(H.Classify) == "function" then
    local ok, kind, e = pcall(H.Classify, id, nil, bag, slot)
    if ok and kind and kind ~= "useless" then
      local q = cut(type(e) == "table" and e.quest or "?", 30)
      if kind == "starter" then return huge, fmt("lance la quête « %s » (clic droit dessus)", q), "use" end
      if kind == "upcoming" then return huge, fmt("pour une quête à venir : « %s »", q) end
      if kind == "need" and it.cls ~= 12 and type(e) == "table" and tonumber(e.need) then
        return tonumber(e.need), fmt("quête « %s » : il en faut %d", q, e.need), nil, true
      end
      if kind == "done" then return huge, fmt("quête « %s » : prêt, à rendre", q) end
      return huge, fmt("quête « %s »", q)
    end
  end
  if it.cls == 12 or it.bind == 4 or questSlot(bag, slot) then return huge, "objet de quête" end
end)

--- la réponse « quête » d'un objet, en une chaîne : sert à savoir si un changement du journal touche cet objet
questSig = function(id)
  local it = itemInfo(id)
  if not it then return "?" end
  local key = S.first[id]
  local ok, n, why = pcall(BUILTIN["quête"], id, it, S.count[id] or 0, key and floor(key / 1000) or nil, key and key % 1000 or nil)
  if not ok then return "!" end
  return tostring(n) .. "|" .. tostring(why)
end

-- consommables pour toi / munitions / familier
builtin("consommable", function(id)
  buildTables()
  local c = CAT[id]
  if not c or c.cat == "ammo" then return nil end
  local p = planCat(c.cat)
  local k = p.keep[id]
  if k == nil then return nil end
  if k > 0 then return k, p.why[id] end
  return 0, p.opin[id]
end)
builtin("munitions", function(id, it)
  buildTables()
  local a = ammoSub(id, it)
  if not a then return nil end
  local p = planAmmo()
  if not p.kind then return huge, "munitions" end         -- pas d'arme à distance lue : dans le doute, des munitions se gardent
  local word = NAME[p.kind][2]
  if a == p.sub then
    if it.req > level() then return huge, fmt("%s pour le niveau %d", word, it.req) end
    return huge, "munitions de ton arme"
  end
  if p.alt[a] then return huge, fmt("munitions de %s (dans tes sacs)", p.alt[a]) end
  return 0, fmt("ton arme tire des %s, pas des %s", word, NAME[a == 2 and "arrow" or "bullet"][2])
end)
builtin("familier", function(id)
  buildTables()
  if not PET[id] then return nil end
  local p = planPet()
  if p.keep[id] then return p.keep[id], p.why[id], nil, true end
end)

-- composants de classe (objets du jeu, par classe) : les tiens se gardent, ceux d'une autre classe sont en trop
local REAGENT = {
  [17031] = "MAGE", [17032] = "MAGE", [17020] = "MAGE", [17056] = "MAGE PRIEST", [17028] = "PRIEST", [17029] = "PRIEST",
  [21177] = "PALADIN", [17033] = "PALADIN", [17030] = "SHAMAN", [17057] = "SHAMAN", [17058] = "SHAMAN",
  [17034] = "DRUID", [17035] = "DRUID", [17036] = "DRUID", [17037] = "DRUID", [17038] = "DRUID", [17021] = "DRUID", [17026] = "DRUID",
  [6265] = "WARLOCK", [5565] = "WARLOCK", [16583] = "WARLOCK", [5140] = "ROGUE", [5530] = "ROGUE",
}
local CLASS_FR = { MAGE = "mage", PRIEST = "prêtre", PALADIN = "paladin", SHAMAN = "chaman", DRUID = "druide", WARLOCK = "démoniste",
  ROGUE = "voleur" }
PRE["composant"] = function(id) return REAGENT[id] ~= nil end
builtin("composant", function(id)
  local who = REAGENT[id]
  if not who then return nil end
  local cls = class()
  if not cls then return nil end
  if who:find(cls, 1, true) then return huge, "composant de tes sorts" end
  return 0, fmt("composant de %s : inutile pour toi", CLASS_FR[who:match("^%a+")] or "une autre classe")
end)

-- clés, outils, sacs, monture, mascottes
local HEARTH = 6948
local TOOLS = {   -- outils de métier (objets du jeu) : un seul suffit
  [2901] = true, [7005] = true, [5956] = true, [6219] = true, [10498] = true, [4471] = true, [5060] = true, [9149] = true,
  [6218] = true, [6339] = true, [11130] = true, [11145] = true, [16207] = true, [6256] = true, [6365] = true, [6366] = true,
  [6367] = true, [12225] = true, [19022] = true, [19970] = true, [15846] = true,
}
builtin("outil", function(id, it)
  if id == HEARTH then return huge, "ta pierre de foyer" end
  if it.cls == 13 then return huge, "une clé" end
  if TOOLS[id] or (it.cls == 2 and it.sub == 20) then return 1, (it.cls == 2 and it.sub == 20) and "ta canne à pêche" or "outil de métier" end
  if it.cls == 1 or it.cls == 11 then return huge, it.cls == 11 and "carquois / giberne" or "un sac" end
  if it.cls == 15 and it.sub == 5 then return huge, "ta monture" end
  if it.cls == 15 and it.sub == 2 then return huge, "une mascotte" end
end)

-- équipement : porté en alternance, d'un set ; sinon l'avis de GEAR (son gardien) ou, à défaut, son moteur public
local WORN_DAYS = 30
local function setItems()
  if C.sets then return C.sets end
  local out = {}
  local ES = g("C_EquipmentSet")
  if type(ES) == "table" and ES.GetEquipmentSetIDs and ES.GetItemIDs then
    for _, sid in ipairs(try(ES.GetEquipmentSetIDs) or {}) do
      for _, iid in pairs(try(ES.GetItemIDs, sid) or {}) do if num(iid) and iid > 0 then out[iid] = true end end
    end
  end
  C.sets = out
  return out
end
local function slotLink(id, bag, slot)
  if bag and slot then
    local sid, _, _, _, _, _, link = readSlot(bag, slot)
    if sid == id and type(link) == "string" then return link end
  end
  local s = snapshot()
  for _, key in ipairs(s.list) do
    if s.id[key] == id then
      local _, _, _, _, _, _, link = readSlot(floor(key / 1000), key % 1000)
      if type(link) == "string" then return link end
    end
  end
  return nil
end
builtin("équipement", function(id, it, _, bag, slot)
  if not ((it.cls == 2 or it.cls == 4) and it.loc) then return nil end
  local w = M.char and type(M.char.worn) == "table" and tonumber(M.char.worn[id])
  if w and epoch() - w < WORN_DAYS * 86400 then return huge, "tu le portes en alternance" end
  if setItems()[id] then return huge, "dans un de tes sets d'équipement" end
  if EXT.gear then return nil end                         -- GEAR a inscrit son gardien : son avis, pas le nôtre
  local Gm = T.Gear
  if not (type(Gm) == "table" and type(Gm.CanUse) == "function") then return nil end
  local link = slotLink(id, bag, slot) or it.link or id
  local lv = level()
  -- en groupe, une pièce échangeable peut servir à un ami : seul le gardien de GEAR le sait → ce pont ne dit pas « en trop »
  local key = bag and slot and (bag * 1000 + slot) or S.first[id]
  local bound = it.bind == 1 or (key and S.bound[key]) or false
  local friends = (num(try(g("GetNumGroupMembers"))) or num(try(g("GetNumPartyMembers"))) or 0) > 0 and not bound
  local ok, why = try(Gm.CanUse, link)
  if ok == nil then return nil end
  if ok == false then
    if why == "non équipable" then return nil end
    local at = type(why) == "string" and tonumber(why:match("niveau (%d+)")) or nil
    if at and at <= lv + 10 then return huge, why, (at > lv + 3) and "bank" or nil end
    if friends then return nil end
    return 0, at and fmt("pas portable avant le niveau %d", at) or (why or "tu ne peux pas le porter")
  end
  if it.req > lv then
    if it.req <= lv + 10 then return huge, fmt("équipable au niveau %d", it.req), (it.req > lv + 3) and "bank" or nil end
    if friends then return nil end
    return 0, fmt("pas équipable avant le niveau %d", it.req)
  end
  local pct = num(try(Gm.UpgradePct, link))
  if pct == nil then return nil end                       -- stats inconnues : pas d'avis → gardé
  if pct >= 3 then return huge, pct >= 999 and "emplacement vide : équipe-le" or "mieux que ce que tu portes : équipe-le", "use" end
  if pct > -3 then return huge, "équivalent à ce que tu portes" end
  if friends then return nil end
  return 0, "moins bien que ce que tu portes"
end)

-- recettes : déjà connue / métier que tu n'as pas → en trop ; à apprendre → garde
local RIDX
local function recipeIndex()
  if RIDX then return RIDX end
  RIDX = {}
  local D = T.Data and T.Data.Recipes
  for line, rows in pairs(type(D) == "table" and type(D.r) == "table" and D.r or {}) do
    for _, r in ipairs(rows) do
      local item = tonumber(r[8]) or 0
      if item > 0 then RIDX[item] = { line = line, spell = r[1], learn = tonumber(r[4]) or 1 } end
    end
  end
  return RIDX
end
local KNOWN_TIP = {}
local function knownByTooltip(id, bag, slot)
  if KNOWN_TIP[id] ~= nil then return KNOWN_TIP[id] end
  local TI, word = g("C_TooltipInfo"), g("ITEM_SPELL_KNOWN")
  if not (bag and slot and type(TI) == "table" and TI.GetBagItem and type(word) == "string") then return nil end
  local data = try(TI.GetBagItem, bag, slot)
  if type(data) ~= "table" or type(data.lines) ~= "table" then return nil end
  local known = false
  for _, l in ipairs(data.lines) do if type(l) == "table" and pub(l.leftText) == word then known = true end end
  KNOWN_TIP[id] = known
  return known
end
builtin("recette", function(id, it, _, bag, slot)
  if it.cls ~= 9 then return nil end
  local e = recipeIndex()[id]
  if e then
    local ranks = profRanks()
    if not ranks then return nil end
    local rank, ln = ranks[e.line], lineName(e.line)
    if not rank then return 0, fmt("recette de %s : tu n'as pas ce métier", ln) end
    local PL = T.ProfLevel
    local known = PL and type(PL.Known) == "function" and try(PL.Known, e.line) or nil
    if type(known) == "table" and known[e.spell] then return 0, "recette que tu connais déjà" end
    if knownByTooltip(id, bag, slot) then return 0, "recette que tu connais déjà" end
    if rank >= e.learn then return huge, fmt("à apprendre maintenant (%s %d)", ln, e.learn), "use" end
    -- bientôt (≤ 25 points) : reste dans les sacs ; plus loin : à la banque en attendant
    return huge, fmt("à apprendre à %d en %s (tu es à %d)", e.learn, ln, rank), (e.learn - rank > 25) and "bank" or nil
  end
  if knownByTooltip(id, bag, slot) then return 0, "recette que tu connais déjà" end
end)

-- pont Métiers (tant que PROF n'a pas inscrit son gardien) : une marchandise qui sert à une recette d'UN DE TES
-- métiers est gardée (« sert à ton Travail du cuir ») ; le « en trop » précis (plan, quantités) viendra du gardien
-- de PROF. Index des compos de TES métiers seulement, construit un métier par appel (jamais un gros calcul d'un coup).
local MAT = { lines = {}, by = {}, todo = nil, gen = -1, wait = {} }
local function matIndex()
  if MAT.gen ~= C.gen then
    MAT.gen, MAT.todo = C.gen, nil
    local ranks = profRanks()
    if not ranks then MAT.known = false return nil end
    MAT.known, MAT.todo = true, {}
    for line in pairs(ranks) do if not MAT.lines[line] then MAT.todo[#MAT.todo + 1] = line end end
    table.sort(MAT.todo)
  end
  if not MAT.known then return nil end
  local line = MAT.todo and table.remove(MAT.todo)
  if line then
    MAT.lines[line] = true
    local D = T.Data and T.Data.Recipes
    for _, r in ipairs(type(D) == "table" and type(D.r) == "table" and D.r[line] or {}) do
      local mats = r[10]
      for i = 1, type(mats) == "table" and #mats or 0, 2 do
        local mid = mats[i]
        if mid and not (MAT.by[mid] and MAT.by[mid][line]) then
          MAT.by[mid] = MAT.by[mid] or {}
          MAT.by[mid][line] = true
        end
      end
    end
  end
  if MAT.todo and #MAT.todo > 0 then return nil end           -- pas encore fini : pas d'avis (donc gardé)
  if next(MAT.wait) then
    -- les avis rendus pendant la construction (« je ne sais pas ») sont refaits à la prochaine demande
    for wid in pairs(MAT.wait) do V[wid] = nil S.touch[wid] = true end
    MAT.wait = {}
    M._lists = nil
  end
  return MAT.by
end
builtin("métiers", function(id)
  if EXT.prof then return nil end
  local by = matIndex()
  if not by then
    EXT.profSilent = true
    if MAT.known then MAT.wait[id] = true end
    return nil
  end
  local ranks, lines = profRanks(), by[id]
  if not (lines and ranks) then return nil end
  local names = {}
  for line in pairs(lines) do if ranks[line] then names[#names + 1] = lineName(line) end end
  if #names == 0 then return nil end
  table.sort(names)
  return huge, "sert à ton métier : " .. tconcat(names, ", ")
end)

-- ===================================================================== marchand ou HV : l'espérance honnête
local AH_MIN_GAIN = 100          -- l'HV doit rapporter au moins 1 pa de plus que le marchand (et 20 %) pour valoir le détour
local function auction()
  local A = T.modules and T.modules.Auction
  if type(A) == "table" and A._enabled ~= false and type(A.db) == "table" and A.db.enabled ~= false
      and type(A.PostPrice) == "function" then return A end
  return nil
end
local function atrPrice(id)
  local A = g("Auctionator")
  local v1 = type(A) == "table" and type(A.API) == "table" and A.API.v1
  if type(v1) ~= "table" or type(v1.GetAuctionPriceByItemID) ~= "function" then return nil end
  local p = num(try(v1.GetAuctionPriceByItemID, "TreeAI", id))
  if p and p > 0 then return floor(p) end
  return nil
end
--- prix d'HV connu d'un objet : unité, source ("TreeAI" = nos scans, "Auctionator"), ventes/jour mesurées ; nil = inconnu
function M.AHPrice(id)
  local A = auction()
  if A and type(A.Stats) == "function" then
    local st = try(A.Stats, id)
    if type(st) == "table" and (tonumber(st.market) or 0) > 0 then
      local liq = type(A.Liq) == "function" and try(A.Liq, id) or nil
      return floor(st.market), "TreeAI", type(liq) == "table" and tonumber(liq.sold) or nil
    end
  end
  local p = atrPrice(id)
  if p then return p, "Auctionator" end
  return nil
end
--- où vendre n exemplaires : action, valeur attendue (cuivre), détail { vendor, ah, src, sold, unknown }
local function venue(id, it, n, bound)
  local vendor = it.sell
  local r = { vendor = vendor }
  if it.q == 0 or bound or it.cls == 12 or it.bind == 1 or it.bind == 4 then
    if vendor > 0 then return "sell", vendor * n, r end
    return "destroy?", 0, r
  end
  local A = auction()
  local known = false
  if A then
    local seen = (type(A.Stats) == "function" and try(A.Stats, id)) or (type(A.cur) == "table" and A.cur[id])
      or (type(A.browse) == "table" and A.browse[id])
    if seen and not (type(A.Odd) == "function" and try(A.Odd, id)) then
      local pp = try(A.PostPrice, id, min(n, max(1, it.stack)))
      if type(pp) == "table" and tonumber(pp.unit) then
        known = true
        r.ah, r.src, r.sold, r.proof = pp.unit, "TreeAI", pp.sold, pp.proof
        local evU = (tonumber(pp.ev) or 0) / max(1, tonumber(pp.count) or 1)
        if pp.better == "ah" and (evU - vendor) * n >= max(AH_MIN_GAIN, 0.2 * vendor * n) then
          return "ah", floor(evU * n + 0.5), r
        end
      end
    end
  end
  if not known then
    local p = atrPrice(id)
    if p then
      known = true
      r.ah, r.src = p, "Auctionator"
      -- sans mesure des ventes : commission 5 % et 20 % de marge pour l'invendu / la sous-cote
      local evU = p * 0.95 * 0.8
      if (evU - vendor) * n >= max(AH_MIN_GAIN, 0.2 * vendor * n) then return "ah", floor(evU * n + 0.5), r end
    end
  end
  if vendor > 0 then
    if not known and not optOn("ahUnknown") and (it.q >= 2 or it.cls == 7 or it.cls == 9) then
      r.unknown = true                                     -- jamais bradé d'un clic sans connaître son prix à l'HV
      return "ah", vendor * n, r
    end
    return "sell", vendor * n, r
  end
  if r.ah then return "ah", floor(r.ah * 0.95 * 0.8 * n + 0.5), r end
  return "destroy?", 0, r
end

-- ===================================================================== l'avis
local LABEL = { keep = "Garde", sell = "Vends au marchand", ah = "Vends à l'HV", bank = "À la banque", use = "À utiliser",
  ["destroy?"] = "À jeter si tu manques de place" }
M.LABEL = LABEL
local function claimText(v) return v.why end

-- un gardien répond : ses réclamations s'additionnent ; un « 0 + raison » est un avis « en trop »
M._ask = {}
function M._Ask(name, fn, id, it, have, bag, slot)
  local Q = M._ask
  local ok, n, w, wh, sf = pcall(fn, id, it, have, bag, slot)
  if not ok then
    -- un gardien en erreur réclame tout : dans le doute on ne vend pas
    Q.all, Q.why, Q.keeper = true, Q.why or ("gardien « " .. name .. " » en erreur : je garde"), Q.keeper or name
    Q.reasons[#Q.reasons + 1] = "gardien « " .. name .. " » en erreur"
    if T._errorHook then pcall(T._errorHook, n, "Bags:keeper:" .. name) end
    return
  end
  n = tonumber(n)
  if not n then return end
  w = type(w) == "string" and w ~= "" and w or nil
  if n > 0 then
    Q.keep = Q.keep + n
    if n >= have then Q.all = true
    elseif not sf and not Q.opinion then Q.opinion = fmt("en trop : il t'en faut %d (%s)", n, w or name) end
    if w then Q.reasons[#Q.reasons + 1] = w end
    if not Q.why or (wh == "use" and Q.where ~= "use") then Q.why, Q.keeper = w or name, name end
    if wh == "use" or (wh == "bank" and Q.where == nil) then Q.where = wh end
  elseif w then
    Q.opinion = Q.opinion or w
    Q.reasons[#Q.reasons + 1] = w
  end
end
local function compute(id, bag, slot)
  local s = snapshot()
  local have = s.count[id] or 0
  if not (bag and slot) and s.first[id] then bag, slot = floor(s.first[id] / 1000), s.first[id] % 1000 end
  local it = itemInfo(id)
  local v = { id = id, have = have, t = now(), gen = C.gen, keep = have, surplus = 0, value = 0 }
  if not it then
    v.action, v.why, v.unknown, v.qsig = "keep", "objet inconnu (pas encore chargé par le jeu) : je garde", true, "?"
    return v
  end
  v.name, v.vendor, v.qsig = it.name, it.sell, questSig(id)
  local Q = M._ask
  Q.keep, Q.all, Q.opinion, Q.where, Q.why, Q.keeper, Q.reasons = 0, false, nil, nil, nil, nil, {}
  EXT.profSilent = nil
  buildTables()
  for _, name in ipairs(BORDER) do
    local pre = PRE[name]
    if not pre or pre(id, it) then M._Ask(name, BUILTIN[name], id, it, have, bag, slot) end
  end
  for _, name in ipairs(ORDER) do M._Ask(name, KEEPERS[name], id, it, have, bag, slot) end
  local keep, all, opinion, where, why, keeper, reasons = Q.keep, Q.all, Q.opinion, Q.where, Q.why, Q.keeper, Q.reasons
  if it.q == 0 and not opinion then opinion = "objet gris : ne sert à rien" end
  -- marchandise que personne ne réclame (et Métiers a pu répondre) : proposée à l'HV SEULEMENT si son prix y est connu
  -- et nettement meilleur qu'au marchand — jamais vendue d'un clic au marchand sans un avis « en trop »
  local market = false
  if not opinion and keep == 0 and not all and it.cls == 7 and it.q >= 1 and not EXT.profSilent then
    opinion, market = "personne ne le réclame", true
  end
  v.reasons, v.keeper, v.where = reasons, keeper, where
  local kept = (all or keep >= have) and have or keep
  local surplus = have - kept
  if surplus <= 0 or not opinion then
    -- gardé : réclamé par un gardien, ou personne ne dit qu'il est en trop
    v.keep, v.surplus = have, 0
    v.action = (where == "use" and "use") or (where == "bank" and "bank") or "keep"
    v.why = why or ((it.cls == 2 or it.cls == 4) and it.loc and "équipement : personne ne dit qu'il est en trop (toi, un ami ?), je garde")
      or "je ne sais pas s'il te sert : je garde"
    v.claimed = why ~= nil
    return v
  end
  -- en trop (avis explicite) : des piles ENTIÈRES dans le surplus (jamais de découpe), les plus petites d'abord
  local stacks, boundAny = {}, false
  for _, key in ipairs(s.list) do
    if s.id[key] == id then
      stacks[#stacks + 1] = key
      if s.bound[key] then boundAny = true end
    end
  end
  tsort(stacks, function(a, b) if s.n[a] ~= s.n[b] then return s.n[a] < s.n[b] end return a < b end)
  local sel, left, picked = {}, surplus, 0
  for _, key in ipairs(stacks) do
    if s.n[key] <= left then sel[key] = true left = left - s.n[key] picked = picked + s.n[key] end
  end
  v.keep, v.surplus = kept, surplus
  if picked == 0 then
    v.action = "keep"
    v.why = (why or "à garder") .. fmt(" (%d en trop dans une pile entamée)", surplus)
    v.claimed = true
    return v
  end
  local action, value, d = venue(id, it, picked, boundAny)
  if market and (action ~= "ah" or d.unknown) then
    v.keep, v.surplus, v.action, v.why = have, 0, "keep", "je ne sais pas s'il te sert : je garde"
    return v
  end
  v.action, v.value, v.sellN, v.sel, v.opinion = action, value, picked, sel, opinion
  v.ah, v.ahSrc, v.ahSold, v.ahUnknown = d.ah, d.src, d.sold, d.unknown
  local tail
  if action == "sell" then tail = fmt("marchand %s", money(value))
  elseif action == "ah" then
    tail = d.unknown and fmt("prix d'HV inconnu (scanne l'HV) · marchand %s", money(it.sell * picked))
      or fmt("HV ≈ %s l'unité%s contre %s au marchand", money(d.ah or 0),
        d.sold and fmt(" (se vend ~%s/j)", (d.sold >= 1) and tostring(floor(d.sold + 0.5)) or "<1") or (d.src == "Auctionator" and " (Auctionator)" or ""),
        money(it.sell))
  else tail = "0 pc" end
  v.why = opinion .. " · " .. tail
  if kept > 0 then
    -- le reste est gardé : l'avis d'une case qui n'est pas dans le lot à vendre
    v.rest = { id = id, have = have, t = v.t, gen = v.gen, action = (where == "bank" and "bank") or "keep", keep = kept, surplus = surplus,
      value = 0, name = it.name, vendor = it.sell, why = fmt("%s (garde %d, %d en trop)", why or "à garder", kept, surplus),
      reasons = reasons, keeper = keeper, claimed = true }
  end
  return v
end

local OFF = { action = "keep", why = "assistant d'inventaire coupé : je garde", keep = 0, surplus = 0, value = 0, off = true }
local TTL = 45        -- les gardiens des autres modules changent d'avis sans prévenir : un avis ne vit pas plus longtemps
--- L'AVIS. itemID seul = l'objet ; avec sac + case = cette pile (une pile hors du lot à vendre répond « garde »).
--- → { action, why, keep, surplus, value, have, name, vendor, ah, reasons, keeper, claimed }. Table partagée : ne pas modifier.
function M.Verdict(itemID, bag, slot)
  local id = tonumber(itemID)
  if not id and type(itemID) == "string" then id = tonumber(itemID:match("item:(%d+)")) end
  if not id then return OFF end
  if not M._active then return OFF end
  context()
  snapshot()
  local v = V[id]
  local t = now()
  if not v or v.gen ~= C.gen or t - v.t > TTL or t < v.t then
    v = compute(id, bag, slot)
    V[id] = v
    M.stats.computed = M.stats.computed + 1
  end
  if bag and slot and v.sel and not v.sel[bag * 1000 + slot] then return v.rest or v end
  return v
end
M.stats = { computed = 0, overlays = 0, sold = 0 }
--- l'avis déjà prêt (ou nil) : pour les rappels appelés en rafale par un addon de sacs — jamais de calcul dans leur image
function M.Peek(id, bag, slot)
  if not M._active or S.seen ~= bagGen() then return nil end
  local v = V[id]
  if not v or v.gen ~= C.gen or now() - v.t > TTL then return nil end
  if bag and slot and v.sel and not v.sel[bag * 1000 + slot] then return v.rest or v end
  return v
end
--- ligne courte d'un avis : « Garde : 5 potions de soins pour toi (tu en as 3) »
function M.Text(v)
  if type(v) ~= "table" or not v.action then return nil end
  return (LABEL[v.action] or v.action) .. " : " .. tostring(claimText(v) or "")
end
--- vrai quand ce module répond : les autres infobulles ne redisent pas « vends / garde » (un seul avis)
function M.Owns() return M._active == true and opt("tooltip") end

-- ===================================================================== listes (vendre, HV, banque, garder)
local function sortValue(a, b)
  if a.grey ~= b.grey then return a.grey end                 -- les gris d'abord : ce qui a de la valeur part en dernier (rachat)
  if a.value ~= b.value then return a.value < b.value end
  if a.bag ~= b.bag then return a.bag < b.bag end
  return a.slot < b.slot
end
--- toutes les piles classées : { sell = {…}, ah = {…}, bank = {…}, use = {…}, destroy = {…}, keep = {…} } ; une entrée =
--- { bag, slot, id, n, value, grey, why, v }
function M.Lists()
  local s = snapshot()
  context()
  local L = M._lists
  if L and L.gen == C.gen and L.seen == s.seen and now() - L.t < TTL then return L end
  L = { sell = {}, ah = {}, bank = {}, use = {}, destroy = {}, keep = {}, gen = C.gen, seen = s.seen, t = now() }
  for _, key in ipairs(s.list) do
    local id = s.id[key]
    local bag, slot = floor(key / 1000), key % 1000
    local v = M.Verdict(id, bag, slot)
    local n = s.n[key]
    local it = INFO[id]
    local list = (v.action == "destroy?" and L.destroy) or L[v.action] or L.keep
    local unit = (v.action == "sell" and (it and it.sell or 0)) or (v.action == "ah" and (v.sellN or 0) > 0 and v.value / v.sellN) or 0
    list[#list + 1] = { bag = bag, slot = slot, id = id, n = n, value = floor(unit * n + 0.5), grey = (it and it.q == 0) or false,
      why = v.why, v = v, name = (it and it.name) or itemName(id) }
  end
  for _, k in ipairs({ "sell", "ah", "bank", "use", "destroy", "keep" }) do tsort(L[k], sortValue) end
  M._lists = L
  return L
end
--- ce qu'UN clic vend chez le marchand : tous les gris + au plus 12 autres (le rachat du marchand garde 12 objets)
M.SAFE_SELL = 12
function M.SellList(greyOnly)
  local L = M.Lists()
  local out = { list = {}, value = 0, greys = 0, rest = 0 }
  local others = 0
  for _, e in ipairs(L.sell) do
    if e.grey or (not greyOnly and others < M.SAFE_SELL) then
      if not e.grey then others = others + 1 else out.greys = out.greys + 1 end
      out.list[#out.list + 1] = e
      out.value = out.value + e.value
    elseif not greyOnly then
      out.rest = out.rest + 1
    end
  end
  return out
end
--- une ligne par objet (les piles regroupées) : { id, name, n, value, why, v }
function M.Grouped(list)
  local by, out = {}, {}
  for _, e in ipairs(list) do
    local r = by[e.id]
    if not r then
      r = { id = e.id, name = e.name, n = 0, value = 0, why = e.why, v = e.v, grey = e.grey, stacks = 0 }
      by[e.id] = r
      out[#out + 1] = r
    end
    r.n, r.value, r.stacks = r.n + e.n, r.value + e.value, r.stacks + 1
  end
  return out
end
--- objets gris vendables, au format des anciens appelants (Inventaire / Confort / barre de quête) : liste, valeur
--- (5.0 INTEG) SEULS les avis des objets gris sont calculés ici (quelques-uns) : ces appelants tournent hors d'un clic
--- (résumé du trajet rapide, ligne « vends tes gris »…) et ne doivent jamais déclencher l'avis de tous les sacs d'un coup
--- (c'était la pire image « ville » du profil complet : 3,5 ms). Les listes déjà prêtes sont réutilisées.
function M.GreyItems()
  local list, value = {}, 0
  if not M._active then return list, value end
  local s = snapshot()
  context()
  local L = M._lists
  if L and L.gen == C.gen and L.seen == s.seen and now() - L.t < TTL then
    for _, e in ipairs(L.sell) do
      if e.grey then
        list[#list + 1] = { bag = e.bag, slot = e.slot, value = e.value, link = INFO[e.id] and INFO[e.id].link, id = e.id }
        value = value + e.value
      end
    end
    return list, value
  end
  for _, key in ipairs(s.list) do
    local id = s.id[key]
    local it = itemInfo(id)
    if it and it.q == 0 then
      local bag, slot = floor(key / 1000), key % 1000
      local v = M.Verdict(id, bag, slot)
      if v.action == "sell" then
        local val = floor((it.sell or 0) * s.n[key] + 0.5)
        list[#list + 1] = { bag = bag, slot = slot, value = val, link = it.link, id = id }
        value = value + val
      end
    end
  end
  tsort(list, function(a, b2)
    if a.value ~= b2.value then return a.value < b2.value end
    if a.bag ~= b2.bag then return a.bag < b2.bag end
    return a.slot < b2.slot
  end)
  return list, value
end

--- prépare les avis de tous les objets des sacs par petits paquets (une image chacun), puis appelle cb
local WARM_CHUNK = 6
local function stale()
  local s = snapshot()
  context()
  local todo, t = {}, now()
  for id in pairs(s.count) do
    local v = V[id]
    if not v or v.gen ~= C.gen or t - v.t > TTL - 5 then todo[#todo + 1] = id end
  end
  return todo
end
--- (5.0 INTEG) nombre d'objets à vendre pour la pastille du bouton « Sacs » : seulement si les listes sont FRAÎCHES
--- (mêmes sacs, mêmes avis) — sinon nil (pas de pastille plutôt qu'un nombre périmé). Ne calcule rien.
function M.SellCount()
  local L = M._lists
  if not (M._active and L and L.gen == C.gen and L.seen == bagGen() and now() - L.t < TTL) then return nil end
  return #L.sell, L
end
local function stripSoon()
  local Sc = T.Shortcuts
  if type(Sc) == "table" and Sc._active and type(Sc.Refresh) == "function" then pcall(Sc.Refresh) end
end
--- vrai quand tous les avis des sacs sont prêts (une page peut se dessiner sans rien calculer)
function M.Ready() return #stale() == 0 end
function M.Warm(cb)
  local todo = stale()
  if #todo == 0 then if cb then cb() end return 0 end
  tsort(todo)
  local i = 0
  -- (5.0 INTEG) le joueur a une fenêtre ouverte devant lui (marchand, banque, sacs) : ces paquets passent en tête de la
  -- file des travaux répartis (T:SpreadFirst) — sa carte n'attend pas derrière un plan d'équipement ou de métier
  local function spread(fn, ctx)
    local urgent = M.merchantOpen or M.bankOpen or anyBagShown()
    if urgent and T.SpreadFirst then T:SpreadFirst(M, fn, nil, nil, nil, nil, ctx)
    elseif T.Spread then T:Spread(M, fn, nil, nil, nil, nil, ctx)
    else fn() end
  end
  local function step()
    if not M._active then return end
    local n = 0
    while i < #todo and n < WARM_CHUNK do
      i, n = i + 1, n + 1
      M.Verdict(todo[i])
    end
    if i < #todo then
      spread(step, "Bags:avis")
    elseif cb then
      -- la suite (pastilles, carte, liste) dans sa propre image : jamais avec le dernier paquet d'avis
      spread(function() if M._active then cb() end end, "Bags:suite")
    end
  end
  spread(step, "Bags:avis")
  return #todo
end

-- ===================================================================== « Il te manque » (stock)
local VENDOR_CAT = { food = true, water = true, arrow = true, bullet = true, pet = true }
--- quel objet acheter pour une catégorie : le meilleur de ton niveau vu chez un marchand, sinon l'objet classique de
--- ton niveau (potions), sinon le tien s'il est encore de ton niveau ; sinon aucun (on ne nomme pas un pain de niveau 1)
local function buyID(cat, p)
  local lvl, best, bv = level(), nil, nil
  for id in pairs(M.char and type(M.char.vend) == "table" and M.char.vend or {}) do
    local c = CAT[id]
    if c and c.cat == cat and c.req <= lvl and (not bv or c.val > bv) then best, bv = id, c.val end
  end
  if best then return best end
  for _, id in ipairs(CLASSIC[cat] or {}) do
    local c = CAT[id]
    if c and c.req <= lvl then best = id end
  end
  if best then return best end
  local own = p.best and CAT[p.best]
  if own and own.req >= lvl - 15 then return p.best end
  return nil
end
local function shopEntry(cat, label, need, have, want, id)
  local e = { cat = cat, id = id, n = need, need = need, have = have, want = want, label = label }
  local parts = {}
  if id then
    local p, src = M.AHPrice(id)
    if p then e.ah, e.ahSrc = p, src parts[#parts + 1] = "HV ≤ " .. money(p) end
    local npc = M.char and type(M.char.vend) == "table" and M.char.vend[id]
    if type(npc) == "string" then e.npc = npc parts[#parts + 1] = "marchand " .. cut(npc, 20) end
    local it = itemInfo(id)
    e.name = it and it.name or nil                          -- pas encore chargé par le jeu : pas de « objet 1710 »
  end
  if not e.npc and VENDOR_CAT[cat] then parts[#parts + 1] = "marchand" e.vendor = true end
  e.src = (e.npc or e.vendor) and "marchand" or (e.ah and "hv") or nil
  e.text = fmt("%d %s%s", need, label, #parts > 0 and (" (" .. tconcat(parts, " / ") .. ")") or "")
  return e
end
--- ce qu'il te manque pour jouer : { {cat, id, n, have, want, label, name, ah, npc, src, text} } (Courses en ville, HV)
function M.Shopping()
  if not M._active then return {} end
  local s = snapshot()
  context()
  local sh = M._shop
  if sh and sh.seen == s.seen and sh.gen == C.gen then return sh.list end
  buildTables()
  local list = {}
  for _, cat in ipairs({ "hpot", "mpot", "food", "water" }) do
    local p = planCat(cat)
    -- potions : tout manque compte ; repas / boissons : seulement sous la moitié du stock (pas de rappel pour 2 pains)
    local low = (cat == "hpot" or cat == "mpot") and p.have < p.want or p.have * 2 < p.want
    if p.want > 0 and low then
      local need = p.want - p.have
      list[#list + 1] = shopEntry(cat, NAME[cat][need > 1 and 2 or 1], need, p.have, p.want, buyID(cat, p))
    end
  end
  local a = planAmmo()
  if a.kind and a.want > 0 and a.have < a.want then
    local need = math.ceil((a.want - a.have) / 200) * 200
    local id = num(try(g("GetInventoryItemID"), "player", 0))
    if not (id and ammoSub(id, itemInfo(id)) == a.sub) then
      id = nil
      local lvl, bv = level(), nil
      for cid, c in pairs(CAT) do
        if c.cat == "ammo" and c.label == a.kind and c.req <= lvl and cid < 24000 and (not bv or c.val > bv) then id, bv = cid, c.val end
      end
    end
    list[#list + 1] = shopEntry(a.kind, NAME[a.kind][2], need, a.have, a.want, id)
  end
  local pt = planPet()
  if pt.want > 0 and pt.have * 2 < pt.want then
    local diets = {}
    for k in pairs(pt.diet or {}) do diets[#diets + 1] = DIET_FR[k] or k end
    tsort(diets)
    list[#list + 1] = shopEntry("pet", fmt("portions pour ton familier (%s)", tconcat(diets, ", ")), pt.want - pt.have, pt.have, pt.want, nil)
  end
  M._shop = { seen = s.seen, gen = C.gen, list = list }
  return list
end
--- « Il te manque : 2 potions de soins (HV ≤ 25 pa), 400 balles (marchand) » ou nil
function M.MissingText()
  local list = M.Shopping()
  if #list == 0 then return nil end
  local parts = {}
  for i, e in ipairs(list) do if i <= 3 then parts[#parts + 1] = e.text end end
  return "Il te manque : " .. tconcat(parts, ", ") .. (#list > 3 and fmt(" (+%d)", #list - 3) or "")
end
local function resting() return pub(try(g("IsResting"))) and true or false end
function M.UpdateStock()
  -- (5.0 INTEG) la ligne n'est reposée / retirée que si elle change (avant : à chaque changement de sac en ville, et
  -- chaque fois le panneau se redessinait pour rien)
  if not (M._active and opt("stock")) or inCombat() or not (resting() or M.merchantOpen) then
    if M._stockShown ~= false then M._stockShown = false M:Clear("stock") end
    if not M.merchantOpen and not resting() then M._stockHidden = nil end   -- reparti : la ligne reviendra à la prochaine ville
    return nil
  end
  local text = M.MissingText()
  if not text or M._stockHidden then
    if M._stockShown ~= false then M._stockShown = false M:Clear("stock") end
    return nil
  end
  if M._stockShown == text and now() - (M._stockAt or -1e9) < 600 then return text end
  M._stockShown, M._stockAt = text, now()
  M:Suggest("stock", text, 1, { ttl = 900, cooldown = 300, sound = false,
    tooltip = { "Stock conseillé pour ton niveau (compté dans tes sacs).",
      "Le détail et pourquoi : /tai sacs (fenêtre TreeAI, « HV » puis « Sacs »).", "Clic : ranger jusqu'à la prochaine ville" },
    onClick = function() M._stockHidden = true M._stockShown = false M:Clear("stock") end })
  return text
end

-- ===================================================================== sacs presque pleins
--- la pile qui vaut le moins parmi ce qui peut partir (jamais un objet gardé) : entrée, "destroy" | "sell"
function M.Cheapest()
  local L = M.Lists()
  if L.destroy[1] then return L.destroy[1], "destroy" end
  local best
  for _, e in ipairs(L.sell) do if not best or e.value < best.value then best = e end end
  if best then return best, "sell" end
  return nil
end
--- « 2 places : vends / jette Griffe cassée ×2 (14 pc) » ; rien d'inventé : sans candidat, on le dit
function M.FullText(free)
  free = tonumber(free) or 0
  local head = free <= 0 and "Sacs pleins" or plural(free, "place", "places")
  local e = M.Cheapest()
  if not e then return head .. " : tout te sert, vide tes sacs à la banque ou chez un marchand", nil end
  local what = e.name .. (e.n > 1 and (" ×" .. e.n) or "")
  return fmt("%s : vends / jette %s (%s)", head, what, money(e.value)), e
end
--- ouvre les sacs du jeu (ton clic) : c'est là que l'objet le moins cher est marqué ; sans sacs du jeu, la page Sacs
function M.ShowBags()
  if anyBagShown() then return true end
  local open = g("OpenAllBags")
  if type(open) == "function" and pcall(open) then return true end
  note("Ouvre tes sacs : l'objet le moins cher porte une croix rouge.")
  return false
end
function M.CheckFull()
  if not (M._active and opt("warn")) or inCombat() then return end
  local free, total = 0, 0
  if API.FreeSlots then free, total = API.FreeSlots() end
  local limit = tonumber(M.db and M.db.warnFree) or 2
  if (total or 0) <= 0 or free > limit then
    if M.full then
      M.full = nil
      M:Clear("full")
      M.QueueOverlays()
    end
    return
  end
  if M.full and M.full.free == free and M.full.seen == bagGen() then return end
  M.Warm(function()
    local text, e = M.FullText(free)
    local sl = M.SellList()
    M.full = { free = free, seen = bagGen(), key = e and (e.bag * 1000 + e.slot) or nil, id = e and e.id or nil }
    M:Suggest("full", text, free <= 0 and 3 or 2, { ttl = 600, cooldown = 120, sound = free <= 0,
      tooltip = { #sl.list > 0 and fmt("Chez un marchand : %s à vendre d'un clic (%s).", plural(#sl.list, "objet", "objets"), money(sl.value))
          or "Rien à vendre d'un clic pour l'instant.",
        e and "Dans tes sacs, l'objet le moins cher porte une croix rouge." or nil,
        "TreeAI ne détruit jamais rien : jeter reste ton geste." },
      actionText = "ouvrir tes sacs", onClick = function() M.ShowBags() end })
    M.QueueOverlays()
  end)
end

-- ===================================================================== marchand : vendre d'un clic, par paquets revérifiés
local BATCH, BATCH_EVERY = 6, 0.25
local function merchantShown()
  local mf = g("MerchantFrame")
  if type(mf) == "table" and type(mf.IsShown) == "function" then return try(mf.IsShown, mf) and true or false end
  return M.merchantOpen and true or false
end
local function useSlot(bag, slot, click)
  local fn = (CC and CC.UseContainerItem) or g("UseContainerItem")
  if type(fn) ~= "function" then return false end
  if click and T.ShopCall then return (T:ShopCall("UseContainerItem", fn, bag, slot)) and true or false end
  if T.ShopSafe and T:ShopSafe() then return false end
  return pcall(fn, bag, slot) and true or false
end
function M.RepairCost()
  if not (M.merchantOpen and pub(try(g("CanMerchantRepair")))) then return 0 end
  return num(try(g("GetRepairAllCost"))) or 0
end
--- répare tout si l'or suffit ; → coût payé (0 = rien), "or" si l'or manque
function M.RepairNow(click)
  local cost = M.RepairCost()
  if cost <= 0 or inCombat() then return 0 end
  if cost > (num(try(g("GetMoney"))) or 0) then return 0, "or" end
  local fn = g("RepairAllItems")
  if type(fn) ~= "function" then return 0 end
  local ok
  if click and T.ShopCall then ok = T:ShopCall("RepairAllItems", fn) else ok = pcall(fn) end
  if not ok then return 0 end
  M.lastRepair = { cost = cost, at = now() }
  return cost
end
local function saleDone()
  local sale = M.sale
  M.sale = nil
  if not sale then return end
  M.lastSale = { n = sale.n, money = sale.money, at = now(), skipped = sale.skipped }
  M.stats.sold = M.stats.sold + sale.n
  M:Clear("full")
  after(0.4, function() M.Refresh() end)
end
--- un paquet : chaque pile est RELUE juste avant (même objet, même nombre, pas verrouillée, avis toujours « vendre »)
function M.SellBatch(click)
  local sale = M.sale
  if not sale then return 0 end
  if inCombat() or not merchantShown() then saleDone() return 0 end
  local k = 0
  while k < BATCH and sale.i < #sale.q do
    sale.i = sale.i + 1
    k = k + 1
    local e = sale.q[sale.i]
    local id, c, _, locked = readSlot(e.bag, e.slot)
    local ok = id == e.id and c == e.n and not locked
    if ok then
      local v = M.Verdict(id, e.bag, e.slot)
      ok = v.action == "sell" and not (sale.greyOnly and not e.grey)
    end
    if ok and useSlot(e.bag, e.slot, click) then
      sale.n, sale.money = sale.n + 1, sale.money + e.value
    else
      sale.skipped = (sale.skipped or 0) + 1
    end
  end
  if sale.i < #sale.q then after(BATCH_EVERY, function() M.SellBatch(false) end) else saleDone() end
  return k
end
--- lance la vente d'une liste d'entrées { bag, slot, id, n, value, grey } ; → nombre de piles en file
function M.StartSale(list, o)
  o = o or {}
  if M.sale or inCombat() or not merchantShown() or type(list) ~= "table" or #list == 0 then return 0 end
  local q = {}
  for i, e in ipairs(list) do q[i] = e end
  M.sale = { q = q, i = 0, n = 0, money = 0, greyOnly = o.greyOnly and true or false }
  M.SellBatch(o.click and true or false)
  return #q
end
--- LE clic de la carte du marchand (jamais appelé hors d'un clic) : réparer si coché, puis vendre
function M.SellClick()
  if inCombat() then note("Pas de vente en combat.") return 0 end
  if not merchantShown() then return 0 end
  if T.ShopSafe and T:ShopSafe() then note("Mode sûr : vends à la main, les objets à vendre portent une pièce.") return 0 end
  if opt("repair") then
    local _, why = M.RepairNow(true)
    if why == "or" then note("Réparation : pas assez d'or.") end
  end
  M.Rescan()
  local sl = M.SellList()
  local n = M.StartSale(sl.list, { click = true })
  M.UpdateCard()
  return n
end
--- vente automatique des gris à l'ouverture du marchand (option « Confort ») : mêmes paquets, mêmes relectures
function M.SellGreys()
  if not M._active then return 0 end
  local sl = M.SellList(true)
  return M.StartSale(sl.list, { greyOnly = true }), sl.value
end

-- ===================================================================== cartes sous les fenêtres du jeu (marchand, banque)
local CARD = {}
local CARD_W, ROW_H, ROWS = 300, 16, 8
local function frameOf(names)
  for _, n in ipairs(names) do
    local f = g(n)
    if type(f) == "table" and (meth(f, "IsVisible") or meth(f, "IsShown")) then return f end
  end
  return nil
end
local function tipShow(owner, title, lines, hint)
  local tt = g("GameTooltip")
  if not tt then return end
  meth(tt, "SetOwner", owner, "ANCHOR_RIGHT")
  meth(tt, "AddLine", title or "", 1, 0.82, 0.2, true)
  for _, l in ipairs(lines or {}) do meth(tt, "AddLine", l, 0.88, 0.88, 0.88, true) end
  if hint then meth(tt, "AddLine", hint, 0.4, 0.8, 1, true) end
  meth(tt, "Show")
end
local function tipHide() local tt = g("GameTooltip") if tt then meth(tt, "Hide") end end
local function fontString(parent, size, token)
  local Th = T.Theme
  local fs = (Th and Th.Text and Th:Text(parent, size or "S", token or "text")) or meth(parent, "CreateFontString", nil, "OVERLAY", "GameFontHighlightSmall")
  if fs then meth(fs, "SetJustifyH", "LEFT") end
  return fs
end
--- une carte : un gros bouton, une ligne d'aide, des lignes (dépliées au survol). Jamais créée en combat.
local function newCard(kind, onClick, onTip)
  if CARD[kind] then return CARD[kind] end
  if inCombat() then return nil end
  local Th, cf, UIP = T.Theme, g("CreateFrame"), g("UIParent")
  if type(cf) ~= "function" then return nil end
  local f = (Th and Th.Frame and Th:Frame(UIP, (Th.KINDS and Th.KINDS.small) and "small" or "panel", CARD_W, 56)) or try(cf, "Frame", nil, UIP)
  if not f then return nil end
  meth(f, "SetSize", CARD_W, 56)
  meth(f, "SetFrameStrata", "DIALOG")
  meth(f, "SetClampedToScreen", true)
  meth(f, "EnableMouse", true)
  local function click(_, button)
    if button == "RightButton" then return end
    if inCombat() then note("Après le combat.") return end
    T:UserAction(onClick)
  end
  f.btn = (Th and Th.Button and Th:Button(f, "", CARD_W - 16, 24, click)) or try(cf, "Button", nil, f)
  if f.btn then
    meth(f.btn, "SetSize", CARD_W - 16, 24)
    meth(f.btn, "SetPoint", "TOPLEFT", f, "TOPLEFT", 8, -8)
    if not (Th and Th.Button) then meth(f.btn, "SetScript", "OnClick", click) end
    meth(f.btn, "SetScript", "OnEnter", function(b)
      M.CardExpand(kind, true)
      local title, lines, hint = onTip()
      tipShow(b, title, lines, hint)
    end)
    meth(f.btn, "SetScript", "OnLeave", function() tipHide() M.CardLeave(kind) end)
  end
  f.info = fontString(f, "S", "muted")
  if f.info then
    meth(f.info, "SetWordWrap", true)
    meth(f.info, "SetWidth", CARD_W - 16)
    meth(f.info, "SetPoint", "TOPLEFT", f, "TOPLEFT", 8, -36)
  end
  f.rows = {}
  meth(f, "SetScript", "OnEnter", function() M.CardExpand(kind, true) end)
  meth(f, "SetScript", "OnLeave", function() M.CardLeave(kind) end)
  meth(f, "Hide")
  CARD[kind] = f
  return f
end
local function cardRow(f, i, kind)
  if f.rows[i] then return f.rows[i] end
  local cf = g("CreateFrame")
  local r = try(cf, "Button", nil, f)
  if not r then return nil end
  meth(r, "SetSize", CARD_W - 16, ROW_H)
  meth(r, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  r.t = fontString(r, "S", "text")
  r.v = fontString(r, "S", "muted")
  if r.v then meth(r.v, "SetJustifyH", "RIGHT") meth(r.v, "SetPoint", "RIGHT", r, "RIGHT", 0, 0) meth(r.v, "SetWidth", 80) end
  if r.t then meth(r.t, "SetPoint", "LEFT", r, "LEFT", 0, 0) meth(r.t, "SetWidth", CARD_W - 16 - 84) meth(r.t, "SetWordWrap", false) end
  meth(r, "SetScript", "OnClick", function(self, button)
    if button == "RightButton" and self.e then T:UserAction(function() M.Mark(self.e.id, "keep") end) end
  end)
  meth(r, "SetScript", "OnEnter", function(self)
    M.CardExpand(kind, true)
    local e = self.e
    if not e then return end
    local lines = { M.Text(e.v) }
    for _, w in ipairs(e.v.reasons or {}) do if w ~= e.v.why then lines[#lines + 1] = w end end
    tipShow(self, e.name .. (e.n > 1 and (" ×" .. e.n) or ""), lines, "Clic droit : garder (ne plus le proposer)")
  end)
  meth(r, "SetScript", "OnLeave", function() tipHide() M.CardLeave(kind) end)
  f.rows[i] = r
  return r
end
--- dessine une carte : libellé, aide, lignes ; accrochée SOUS la fenêtre du jeu (sous la carte Métiers si elle y est)
local function drawCard(kind, owner, label, info, rows, enabled)
  local f = CARD[kind]
  if not f then return nil end
  if f.btn then
    if f.btn.SetLabel then f.btn:SetLabel(label or "") else meth(f.btn, "SetText", label or "") end
    if enabled == false then meth(f.btn, "Disable") else meth(f.btn, "Enable") end
  end
  if f.info then meth(f.info, "SetText", info or "") end
  local infoH = (info and info ~= "") and ((#info > 52) and 26 or 14) or 0
  local y = 36 + infoH + ((infoH > 0) and 4 or 0)
  local shown = 0
  if f.open and rows then
    for i = 1, min(#rows, ROWS) do
      local r = cardRow(f, i, kind)
      if r then
        local e = rows[i]
        r.e = e
        meth(r.t, "SetText", e.name .. (e.n > 1 and (" ×" .. e.n) or ""))
        meth(r.v, "SetText", money(e.value))
        meth(r, "ClearAllPoints")
        meth(r, "SetPoint", "TOPLEFT", f, "TOPLEFT", 8, -y)
        meth(r, "Show")
        y = y + ROW_H + 1
        shown = shown + 1
      end
    end
  end
  for i = shown + 1, #f.rows do f.rows[i].e = nil meth(f.rows[i], "Hide") end
  f.shownRows = shown
  meth(f, "SetHeight", y + 6)
  meth(f, "ClearAllPoints")
  local PL = T.ProfLevel
  local pc = PL and type(PL.GetCard) == "function" and try(PL.GetCard) or nil
  if type(pc) == "table" and pc ~= f and meth(pc, "IsShown") then
    if not pcall(f.SetPoint, f, "TOP", pc, "BOTTOM", 0, -4) then owner = nil end
  elseif owner then
    if not pcall(f.SetPoint, f, "TOPLEFT", owner, "BOTTOMLEFT", 0, -2) then owner = nil end
  end
  if not owner and not (type(pc) == "table" and meth(pc, "IsShown")) then meth(f, "SetPoint", "TOP", g("UIParent"), "TOP", 0, -140) end
  if not meth(f, "IsShown") then meth(f, "Show") end
  return f
end
function M.CardExpand(kind, open)
  local f = CARD[kind]
  if not f or f.open == open then return end
  f.open = open and true or false
  if kind == "merchant" then M.UpdateCard() else M.UpdateBankCard() end
end
--- la souris quitte la carte : repliée un instant plus tard si elle n'est plus dessus (pas d'OnUpdate)
function M.CardLeave(kind)
  local f = CARD[kind]
  if not (f and f.open) or f.leaving then return end
  f.leaving = true
  after(0.6, function()
    f.leaving = nil
    if meth(f, "IsMouseOver") then return end
    for _, r in ipairs(f.rows) do if meth(r, "IsShown") and meth(r, "IsMouseOver") then return end end
    M.CardExpand(kind, false)
  end)
end
M.GetCard = function(kind) return CARD[kind or "merchant"] end

--- ce marchand vend-il quelque chose qu'il te manque ? (lu une fois à l'ouverture)
local function merchantStock()
  if not M.merchantSells then return nil end
  local parts = {}
  for _, e in ipairs(M.Shopping()) do
    local ok = e.id and M.merchantSells[e.id]
    if not ok and M.merchantCats then ok = M.merchantCats[e.cat] end
    if ok then parts[#parts + 1] = fmt("%d %s", e.n, e.label) end
  end
  if #parts == 0 then return nil end
  return "Il te manque (vendu ici) : " .. tconcat(parts, ", ")
end
function M.ReadMerchant()
  buildTables()
  local n = min(num(try(g("GetMerchantNumItems"))) or 0, 100)
  local sells, cats = {}, {}
  local npc = pub(try(g("UnitName"), "npc"))
  for i = 1, n do
    local id = num(try(g("GetMerchantItemID"), i))
    if not id then
      local link = pub(try(g("GetMerchantItemLink"), i))
      id = type(link) == "string" and tonumber(link:match("item:(%d+)")) or nil
    end
    if id then
      local c = CAT[id]
      local cat = c and ((c.cat == "ammo") and c.label or c.cat) or nil
      if cat or PET[id] then
        sells[id] = i
        if cat then cats[cat] = true end
        if M.char and type(npc) == "string" then
          M.char.vend = type(M.char.vend) == "table" and M.char.vend or {}
          M.char.vend[id] = npc
        end
      end
    end
  end
  M.merchantSells, M.merchantCats = sells, cats
  M._shop = nil
end
--- le nom de l'onglet de rachat du marchand, dans la langue du jeu
local function buyback()
  local w = g("MERCHANT_BUYBACK")
  return (type(w) == "string" and w ~= "") and w or "Rachat"
end
function M.CardModel()
  local sl = M.SellList()
  local rep = opt("repair") and M.RepairCost() or 0
  local n = #sl.list
  local m = { n = n, value = sl.value, rows = M.Grouped(sl.list), rest = sl.rest, greys = sl.greys, repair = rep, enabled = true }
  local ls = M.lastSale
  if M.sale then
    m.label, m.enabled = fmt("Vente en cours… %d / %d", M.sale.i, #M.sale.q), false
  elseif n > 0 and T.ShopSafe and T:ShopSafe() then
    -- mode sûr (un blocage du jeu a été vu) : rien n'est vendu par TreeAI, la liste et les pièces restent
    m.label, m.enabled = fmt("À vendre : %s · %s", plural(n, "objet", "objets"), money(sl.value)), false
    m.info = "Mode sûr allumé : vends à la main (clic droit sur les objets marqués d'une pièce). /tai bloque achats l'explique."
  elseif n > 0 then
    m.label = fmt("Vendre %s · %s", plural(n, "objet", "objets"), money(sl.value)) .. (rep > 0 and (" + réparer " .. money(rep)) or "")
    m.info = (sl.rest > 0 and fmt("Puis %d autres au clic suivant. ", sl.rest) or "")
      .. fmt("Survole : la liste (clic droit = garder). Annulable : onglet « %s ».", buyback())
  elseif rep > 0 then
    m.label = "Réparer · " .. money(rep)
  elseif ls and now() - ls.at < 90 then
    m.label, m.enabled = fmt("Vendu : %s · +%s", plural(ls.n, "objet", "objets"), money(ls.money)), false
  end
  if ls and now() - ls.at < 90 and not M.sale and n == 0 then
    m.info = fmt("Vendu : %s (+%s). Une erreur ? Onglet « %s » du marchand.", plural(ls.n, "objet", "objets"), money(ls.money), buyback())
  end
  local miss = merchantStock()
  if miss then m.info = (m.info and (m.info .. "\n") or "") .. miss m.label = m.label or "Rien à vendre" m.enabled = m.enabled and n + rep > 0 end
  return m
end
local function merchantTip()
  local m = M.cardModel or M.CardModel()
  local lines = {}
  for i, e in ipairs(m.rows) do
    if i > 14 then lines[#lines + 1] = fmt("… et %d autres", #m.rows - 14) break end
    lines[#lines + 1] = fmt("%s%s — %s — %s", e.name, e.n > 1 and (" ×" .. e.n) or "", money(e.value), tostring(e.v.opinion or e.why or ""))
  end
  if m.repair > 0 then lines[#lines + 1] = "Réparation : " .. money(m.repair) .. " (dans le même clic)" end
  lines[#lines + 1] = "Les gris d'abord, puis 12 objets au plus : le marchand en garde 12 au rachat."
  lines[#lines + 1] = "Jamais ce qu'un gardien réclame (quêtes, potions, munitions, métiers, équipement)."
  return "Vendre au marchand", lines, m.n > 0 and "Clic : vendre · clic droit sur une ligne : garder" or nil
end
--- (5.0 INTEG) vrai quand la carte « Vendre N objets + réparer » du marchand est la nôtre : les autres modules
--- (Métiers, Courses en ville) n'affichent alors NI bouton de vente NI bouton de réparation
function M.OwnsMerchant() return M._active == true and opt("card") and true or false end
--- vrai quand les pièces « vendre / HV / banque » des cases de sac sont les nôtres (pas de 2e pastille « trop » d'un autre module)
function M.OwnsOverlays() return M._active == true and opt("overlays") and true or false end
function M.UpdateCard()
  local f = CARD.merchant
  if not (M._active and opt("card") and M.merchantOpen) or inCombat() then
    if f then meth(f, "Hide") end
    M.cardModel = nil
    return nil
  end
  local m = M.CardModel()
  M.cardModel = m
  if not m.label then
    if f then meth(f, "Hide") end
    return m
  end
  f = newCard("merchant", function() M.SellClick() end, merchantTip)
  if not f then return m end
  drawCard("merchant", frameOf({ "MerchantFrame" }), m.label, m.info, m.rows, m.enabled)
  return m
end

-- ===================================================================== banque : « Ranger » (déposer / sortir)
local function bankBags()
  local PL = T.ProfLevel
  if PL and type(PL.BankBags) == "function" then
    local b = try(PL.BankBags)
    if type(b) == "table" and #b > 0 then return b end
  end
  local out = {}
  local En = g("Enum")
  local BI = type(En) == "table" and En.BagIndex
  if type(BI) == "table" and BI.CharacterBankTab_1 then
    for i = 1, 9 do local id = num(BI["CharacterBankTab_" .. i]) if id then out[#out + 1] = id end end
    return out
  end
  out[1] = num(g("BANK_CONTAINER")) or -1
  local nb = num(g("NUM_BAG_SLOTS")) or 4
  for i = 1, num(g("NUM_BANKBAGSLOTS")) or 6 do out[#out + 1] = nb + i end
  return out
end
--- contenu de la banque ouverte : { [id] = { {bag, slot, n}, … } }, cases libres
function M.ScanBank()
  local items, free = {}, 0
  for _, bag in ipairs(bankBags()) do
    local slots = bagSlots(bag)
    for slot = 1, min(slots, 240) do
      local id, c = readSlot(bag, slot)
      if id then
        local l = items[id] or {}
        l[#l + 1] = { bag = bag, slot = slot, n = c, id = id }
        items[id] = l
      else free = free + 1 end
    end
  end
  return items, free
end
--- le plan de rangement : dep = piles des sacs à déposer (avis « banque »), wd = piles à sortir (stock, quêtes)
function M.BankPlan()
  local plan = { dep = {}, wd = {}, free = 0 }
  if not (M._active and M.bankOpen) then return plan end
  buildTables()
  local L = M.Lists()
  for _, e in ipairs(L.bank) do plan.dep[#plan.dep + 1] = e end
  local items, free = M.ScanBank()
  plan.free = free
  local lvl = level()
  local function take(list, need, why)
    for _, st in ipairs(list) do
      if need <= 0 then break end
      plan.wd[#plan.wd + 1] = { bag = st.bag, slot = st.slot, id = st.id, n = st.n, why = why, name = itemName(st.id) }
      need = need - st.n
    end
  end
  for _, cat in ipairs({ "hpot", "mpot", "food", "water" }) do
    local p = planCat(cat)
    if p.want > 0 and p.have < p.want then
      local cand = {}
      for id, list in pairs(items) do
        local c = CAT[id]
        if c and c.cat == cat and c.req <= lvl then for _, st in ipairs(list) do cand[#cand + 1] = st end end
      end
      tsort(cand, function(a, b)
        if CAT[a.id].val ~= CAT[b.id].val then return CAT[a.id].val > CAT[b.id].val end
        if a.bag ~= b.bag then return a.bag < b.bag end
        return a.slot < b.slot
      end)
      take(cand, p.want - p.have, fmt("il te manque %d %s", p.want - p.have, NAME[cat][2]))
    end
  end
  local a = planAmmo()
  if a.kind and a.want > a.have then
    local cand = {}
    for id, list in pairs(items) do
      local it = itemInfo(id)
      if ammoSub(id, it) == a.sub and it and it.req <= lvl then for _, st in ipairs(list) do cand[#cand + 1] = st end end
    end
    take(cand, a.want - a.have, fmt("il te manque %d %s", a.want - a.have, NAME[a.kind][2]))
  end
  local H = mod("Highlight")
  local D = H and type(H.Data) == "function" and try(H.Data) or nil
  for id, e in pairs(type(D) == "table" and type(D.byID) == "table" and D.byID or {}) do
    local need = type(e) == "table" and ((tonumber(e.need) or 0) - (tonumber(e.have) or 0)) or 0
    if need > 0 and items[id] then take(items[id], need, fmt("quête « %s »", cut(e.quest or "?", 26))) end
  end
  return plan
end
local DEPOSIT_PER_CLICK = 8
--- LE clic « Ranger » : d'abord déposer (un paquet par clic), puis sortir UNE pile par clic ; chaque pile relue avant
function M.BankClick()
  if inCombat() then note("Après le combat.") return 0 end
  if not M.bankOpen then return 0 end
  if T.ShopSafe and T:ShopSafe() then note("Mode sûr : range à la main.") return 0 end
  M.Rescan()
  local plan = M.BankPlan()
  local done = 0
  if #plan.dep > 0 then
    if plan.free <= 0 then note("Banque pleine.") return 0 end
    for i = 1, min(#plan.dep, DEPOSIT_PER_CLICK, plan.free) do
      local e = plan.dep[i]
      local id, c, _, locked = readSlot(e.bag, e.slot)
      if id == e.id and c == e.n and not locked and M.Verdict(id, e.bag, e.slot).action == "bank"
          and useSlot(e.bag, e.slot, true) then done = done + 1 end
    end
    M.lastBank = { dep = done, at = now() }
  elseif #plan.wd > 0 then
    local e = plan.wd[1]
    local id, c = readSlot(e.bag, e.slot)
    if id == e.id and c == e.n and (snapshot().free or 0) > 0 and useSlot(e.bag, e.slot, true) then done = 1 end
    M.lastBank = { wd = done, at = now() }
  end
  after(0.4, function() M.UpdateBankCard() end)
  return done
end
local function bankTip()
  local plan = M.bankPlan or M.BankPlan()
  local lines = {}
  for i, e in ipairs(plan.dep) do
    if i > 8 then lines[#lines + 1] = fmt("… et %d autres", #plan.dep - 8) break end
    lines[#lines + 1] = fmt("Déposer : %s%s — %s", e.name, e.n > 1 and (" ×" .. e.n) or "", tostring(e.why or ""))
  end
  for i, e in ipairs(plan.wd) do
    if i > 8 then break end
    lines[#lines + 1] = fmt("Sortir : %s%s — %s", e.name, e.n > 1 and (" ×" .. e.n) or "", tostring(e.why or ""))
  end
  lines[#lines + 1] = "Dépôt : un paquet par clic. Retrait : une pile par clic."
  return "Ranger", lines, "Clic : ranger"
end
function M.UpdateBankCard()
  local f = CARD.bank
  if not (M._active and opt("card") and M.bankOpen) or inCombat() then
    if f then meth(f, "Hide") end
    M.bankPlan = nil
    return nil
  end
  local plan = M.BankPlan()
  M.bankPlan = plan
  local label, info
  if #plan.dep > 0 then
    label = fmt("Ranger : déposer %s", plural(min(#plan.dep, DEPOSIT_PER_CLICK), "pile", "piles"))
    info = (#plan.wd > 0 and fmt("Puis sortir %s (stock, quêtes). ", plural(#plan.wd, "pile", "piles")) or "") .. "Survole : le détail."
  elseif #plan.wd > 0 then
    local e = plan.wd[1]
    label = fmt("Sortir : %s%s", cut(e.name, 24), e.n > 1 and (" ×" .. e.n) or "")
    info = cut(tostring(e.why or ""), 60) .. (#plan.wd > 1 and fmt(" · encore %d ensuite", #plan.wd - 1) or "")
  end
  if not label then
    if f then meth(f, "Hide") end
    return plan
  end
  f = newCard("bank", function() M.BankClick() end, bankTip)
  if not f then return plan end
  drawCard("bank", frameOf({ "BankFrame", "BankPanel" }), label, info, nil, true)
  return plan
end

-- ===================================================================== pastilles sur les boutons de sac du jeu
local OV = { tex = setmetatable({}, { __mode = "k" }), kind = setmetatable({}, { __mode = "k" }), btn = {}, n = 0 }
local TEX = {
  sell = "Interface\\Buttons\\UI-GroupLoot-Coin-Up",          -- pièce : à vendre au marchand
  ah = "Interface\\Minimap\\Tracking\\Auctioneer",            -- marteau : à l'hôtel des ventes
  bank = "Interface\\Minimap\\Tracking\\Banker",              -- coffre : à la banque
  keep = "Interface\\Buttons\\LockButton-Locked-Up",          -- cadenas : à garder (option)
  destroy = "Interface\\Buttons\\UI-GroupLoot-Pass-Up",       -- croix : le moins cher, à jeter si tu manques de place
}
M.TEX = TEX
local function bagFrames()
  local out = {}
  local cb = g("ContainerFrameCombinedBags")
  if type(cb) == "table" then out[#out + 1] = cb end
  for i = 1, num(g("NUM_CONTAINER_FRAMES")) or 13 do
    local f = g("ContainerFrame" .. i)
    if type(f) == "table" then out[#out + 1] = f end
  end
  return out
end
--- boutons de sac du jeu visibles : { {bouton, sac, case} } (sac combiné : EnumerateValidItems ; sinon ContainerFrameNItemM)
function M.BagButtons()
  local out = {}
  local function add(btn, bag)
    if not (type(btn) == "table" and meth(btn, "IsShown")) then return end
    local b = num(meth(btn, "GetBagID")) or bag
    local sl = num(meth(btn, "GetID"))
    if b and sl and sl > 0 then out[#out + 1] = { btn, b, sl } end
  end
  for _, f in ipairs(bagFrames()) do
    if meth(f, "IsShown") then
      local bag = num(meth(f, "GetID"))
      local ok, iter, st, ctl = false, nil, nil, nil
      if type(f.EnumerateValidItems) == "function" then ok, iter, st, ctl = pcall(f.EnumerateValidItems, f) end
      if ok and type(iter) == "function" then
        for _, btn in iter, st, ctl do add(btn, bag) end
      else
        local name = meth(f, "GetName")
        if type(name) == "string" then
          for j = 1, 40 do
            local btn = g(name .. "Item" .. j)
            if not btn then break end
            add(btn, bag)
          end
        end
      end
    end
  end
  return out
end
local function kindOf(bag, slot)
  local key = bag * 1000 + slot
  local id = S.id[key]
  if not id then return nil end
  if M.full and M.full.key == key then return "destroy" end
  local v = M.Verdict(id, bag, slot)
  local a = v.action
  if a == "sell" or a == "ah" or a == "bank" then return a end
  if a == "destroy?" then return nil end
  if v.claimed and optOn("keepIcon") then return "keep" end
  return nil
end
--- pose / change / retire la pastille d'un bouton ; ne touche le bouton que si elle change
local function apply(btn, kind)
  if OV.kind[btn] == kind then return false end
  local tx = OV.tex[btn]
  if not kind then
    if tx then meth(tx, "Hide") end
    OV.kind[btn] = nil
    OV.n = max(0, OV.n - 1)
    return true
  end
  if not tx then
    tx = meth(btn, "CreateTexture", nil, "OVERLAY")
    if not tx then return false end
    meth(tx, "SetSize", 14, 14)
    meth(tx, "SetPoint", "BOTTOMLEFT", btn, "BOTTOMLEFT", 1, 1)
    OV.tex[btn] = tx
  end
  meth(tx, "SetTexture", TEX[kind])
  meth(tx, "Show")
  if OV.kind[btn] == nil then OV.n = OV.n + 1 end
  OV.kind[btn] = kind
  M.stats.overlays = M.stats.overlays + 1
  return true
end
M.OverlayKind = function(btn) return OV.kind[btn] end
M.OverlayCount = function() return OV.n end
M.GetLegend = function() return OV.legend end
local function clearOverlays()
  for btn in pairs(OV.kind) do apply(btn, nil) end
  OV.btn, OV.n = {}, 0
end
anyBagShown = function()
  for _, f in ipairs(bagFrames()) do if meth(f, "IsShown") then return f end end
  return nil
end
--- la légende (une ligne au-dessus des sacs) + « Ranger » si le jeu n'a pas déjà son bouton de tri
function M.UpdateLegend()
  local top = anyBagShown()
  local f = OV.legend
  if not (M._active and opt("overlays") and opt("legend") and top and OV.n > 0) or inCombat() then
    if f then meth(f, "Hide") end
    return nil
  end
  if not f then
    local cf, UIP = g("CreateFrame"), g("UIParent")
    f = try(cf, "Frame", nil, UIP)
    if not f then return nil end
    meth(f, "SetSize", 260, 16)
    meth(f, "EnableMouse", false)
    f.t = fontString(f, "S", "muted")
    if f.t then meth(f.t, "SetPoint", "LEFT", f, "LEFT", 0, 0) meth(f.t, "SetWordWrap", false) end
    local Th = T.Theme
    local function sortClick() if inCombat() then note("Après le combat.") return end T:UserAction(M.Sort) end
    f.sort = (Th and Th.Button and Th:Button(f, "Ranger", 56, 16, sortClick)) or nil
    if f.sort then
      meth(f.sort, "SetPoint", "RIGHT", f, "RIGHT", 0, 0)
      meth(f.sort, "SetScript", "OnEnter", function(b) tipShow(b, "Ranger mes sacs", { "Le tri des sacs du jeu." }, "Clic : ranger") end)
      meth(f.sort, "SetScript", "OnLeave", tipHide)
    end
    OV.legend = f
  end
  if f.sort then
    -- le jeu a déjà son bouton de tri (sac combiné) : pas de doublon
    local native = g("BagItemAutoSortButton")
    if M.CanSort() and not (type(native) == "table" and meth(native, "IsVisible")) then meth(f.sort, "Show") else meth(f.sort, "Hide") end
  end
  for _, bf in ipairs(bagFrames()) do
    if meth(bf, "IsShown") and (num(meth(bf, "GetTop")) or 0) > (num(meth(top, "GetTop")) or 0) then top = bf end
  end
  local seen = {}
  for _, k in pairs(OV.kind) do seen[k] = true end
  local parts = {}
  for _, k in ipairs({ "sell", "ah", "bank", "keep", "destroy" }) do
    if seen[k] then
      parts[#parts + 1] = "|T" .. TEX[k] .. ":12|t " .. ((k == "sell" and "vendre") or (k == "ah" and "HV") or (k == "bank" and "banque")
        or (k == "keep" and "garder") or "à jeter d'abord")
    end
  end
  local text = "TreeAI : " .. tconcat(parts, " · ")
  if f.t then meth(f.t, "SetText", text) end
  M.legendText = text
  meth(f, "ClearAllPoints")
  if not pcall(f.SetPoint, f, "BOTTOMLEFT", top, "TOPLEFT", 4, 2) then meth(f, "Hide") return nil end
  meth(f, "Show")
  return text
end
--- refait les pastilles : seulement les cases qui ont changé (toutes après un changement de contexte / d'affichage)
function M.RefreshOverlays(full)
  OV.queued = nil
  if not (M._active and opt("overlays")) then
    if OV.n > 0 then clearOverlays() end
    M.UpdateLegend()
    return 0
  end
  if inCombat() then OV.pending = true return 0 end
  if not anyBagShown() then
    OV.stale = true
    if OV.legend then meth(OV.legend, "Hide") end           -- sacs fermés : la légende part avec eux
    return 0
  end
  full = full or OV.stale or S.all
  M.Warm(function()
    if inCombat() then OV.pending = true return end
    local s = snapshot()
    local touched = 0
    if full or s.all then
      OV.btn = {}
      local seen = {}
      for _, rec in ipairs(M.BagButtons()) do
        local btn, bag, slot = rec[1], rec[2], rec[3]
        OV.btn[bag * 1000 + slot] = btn
        seen[btn] = true
        if apply(btn, kindOf(bag, slot)) then touched = touched + 1 end
      end
      for btn in pairs(OV.kind) do if not seen[btn] then apply(btn, nil) end end
      OV.stale, s.all = nil, false
    else
      for key in pairs(s.dirty) do
        local btn = OV.btn[key]
        if btn then
          local bag, slot = floor(key / 1000), key % 1000
          -- le jeu a redistribué ses boutons (nouveau sac, autre disposition) : tout sera refait au prochain passage
          if (num(meth(btn, "GetBagID")) or bag) ~= bag or num(meth(btn, "GetID")) ~= slot then OV.stale = true
          elseif apply(btn, kindOf(bag, slot)) then touched = touched + 1 end
        end
      end
      if OV.stale then M.QueueOverlays(true) end
    end
    s.dirty = {}
    M.lastTouched = touched
    M.UpdateLegend()
  end)
  return 1
end
function M.QueueOverlays(full)
  if full then OV.stale = true end
  if OV.queued or not M._active then return end
  OV.queued = true
  after(0.05, function() M.RefreshOverlays() end)
end
--- « Ranger mes sacs » : le tri du jeu (C_Container.SortBags), seulement sur ton clic, hors combat
function M.CanSort() return type((CC and CC.SortBags) or g("SortBags")) == "function" end
function M.Sort()
  if inCombat() then note("Rangement des sacs : après le combat.") return false end
  local fn = (CC and CC.SortBags) or g("SortBags")
  if type(fn) ~= "function" then note("Cette version du jeu n'a pas de tri des sacs.") return false end
  if not (T.InUserAction and T:InUserAction()) then return false end
  local ok = pcall(fn)
  if ok then note("Sacs rangés (tri du jeu).") end
  return ok
end

--- un addon de sacs demande des avis pas encore prêts : préparés par petits paquets, puis son affichage est relancé
function M.ThirdWarm()
  if OV.thirdWarm or not M._active then return end
  OV.thirdWarm = true
  after(0.1, function()
    M.Warm(function()
      OV.thirdWarm = nil
      if inCombat() then return end
      local BG = g("Baganator")
      local refresh = type(BG) == "table" and type(BG.API) == "table" and BG.API.RequestItemButtonsRefresh
      if type(refresh) == "function" then pcall(refresh) end
      for btn, key in pairs(OV.ext or {}) do
        if meth(btn, "IsVisible") then apply(btn, kindOf(floor(key / 1000), key % 1000)) end
      end
      OV.ext = nil
    end)
  end)
end
-- addons de sacs : Baganator (API publique : camelote + widget de coin), Bagnon (hooksecurefunc) — lecture seule
local function hookThird()
  local hsf = g("hooksecurefunc")
  local BG = g("Baganator")
  if not OV.baganator and type(BG) == "table" and type(BG.API) == "table" then
    OV.baganator = true
    if type(BG.API.RegisterJunkPlugin) == "function" then
      pcall(BG.API.RegisterJunkPlugin, "TreeAI", "treeai", function(bagID, slotID, itemID)
        if not (M._active and opt("overlays")) or inCombat() then return false end
        local v = M.Peek(itemID, bagID, slotID)
        if not v then M.ThirdWarm() return false end
        return v.action == "sell"
      end)
    end
    if type(BG.API.RegisterCornerWidget) == "function" then
      pcall(BG.API.RegisterCornerWidget, "TreeAI : HV / banque", "treeai_bags",
        function(widget, details)
          if not (M._active and opt("overlays")) or type(details) ~= "table" or inCombat() then return false end
          local id = num(details.itemID)
          local v = id and M.Peek(id)
          if id and not v then M.ThirdWarm() end
          local a = v and v.action
          if a ~= "ah" and a ~= "bank" then return false end
          if widget.tex then widget.tex:SetTexture(TEX[a]) end
          return true
        end,
        function(itemButton)
          local f = G.CreateFrame("Frame", nil, itemButton)
          f:SetSize(14, 14)
          f.tex = f:CreateTexture(nil, "OVERLAY")
          f.tex:SetAllPoints(f)
          return f
        end, { corner = "bottom_left", priority = 2 })
    end
  end
  local BN = g("Bagnon")
  if not OV.bagnon and type(BN) == "table" and type(hsf) == "function" then
    local cls = (type(BN.Item) == "table" and BN.Item) or (type(BN.ItemSlot) == "table" and BN.ItemSlot)
    if cls and type(cls.Update) == "function" then
      OV.bagnon = true
      pcall(hsf, cls, "Update", function(btn)
        if not (M._active and opt("overlays")) or inCombat() then return end
        local bag = num(meth(btn, "GetBag")) or num(type(btn) == "table" and btn.bag)
        local slot = num(meth(btn, "GetID"))
        if bag and slot and bag >= 0 and bag <= 5 then
          local id = S.id[bag * 1000 + slot]
          if id and not M.Peek(id, bag, slot) then OV.ext = OV.ext or setmetatable({}, { __mode = "k" }) OV.ext[btn] = bag * 1000 + slot M.ThirdWarm() return end
          apply(btn, id and kindOf(bag, slot) or nil)
        end
      end)
    end
  end
end
local function hookBagFrames()
  for _, f in ipairs(bagFrames()) do
    if not OV[f] and type(f.HookScript) == "function" then
      OV[f] = true
      pcall(f.HookScript, f, "OnShow", function() M.QueueOverlays(true) end)
      pcall(f.HookScript, f, "OnHide", function() M.QueueOverlays(true) end)
    end
  end
  local hsf = g("hooksecurefunc")
  if not OV.fnHooked and type(hsf) == "function" then
    OV.fnHooked = true
    for _, fn in ipairs({ "ToggleBag", "OpenBag", "OpenAllBags", "ToggleAllBags", "OpenBackpack", "ToggleBackpack" }) do
      if type(g(fn)) == "function" then pcall(hsf, fn, function() M.QueueOverlays(true) end) end
    end
  end
end

-- ===================================================================== infobulle : une ligne, le même avis
local TIP_COL = { keep = { 0.55, 0.85, 0.55 }, use = { 0.4, 0.8, 1 }, bank = { 0.75, 0.75, 1 }, sell = { 1, 0.82, 0.2 },
  ah = { 1, 0.82, 0.2 }, ["destroy?"] = { 1, 0.5, 0.4 } }
--- la ligne d'infobulle d'un objet de tes sacs : texte, r, g, b (nil si tu ne l'as pas / rien à dire)
function M.TipLine(id, bag, slot)
  if not (M._active and opt("tooltip")) or inCombat() then return nil end
  id = tonumber(id)
  if not id or (snapshot().count[id] or 0) <= 0 then return nil end
  local v = M.Verdict(id, bag, slot)
  if not v or v.off or v.unknown then return nil end
  if v.action == "keep" and not v.claimed then return nil end   -- « je ne sais pas » : rien à dire, rien d'affiché
  local c = TIP_COL[v.action] or TIP_COL.keep
  return "TreeAI · " .. M.Text(v), c[1], c[2], c[3]
end
--- vrai quand l'infobulle de cet objet porte notre avis (les autres lignes ne redisent alors pas « vends / garde »)
function M.HasTip(id) return (M.TipLine(id)) ~= nil end
local function onTip(tt, data)
  if tt ~= g("GameTooltip") or not M._active then return end
  if meth(tt, "IsForbidden") then return end
  local id = type(data) == "table" and num(data.id) or nil
  if not id then
    local _, link = meth(tt, "GetItem")
    link = pub(link)
    id = type(link) == "string" and tonumber(link:match("item:(%d+)")) or nil
  end
  if not id then return end
  local owner = meth(tt, "GetOwner")
  local bag = type(owner) == "table" and (num(meth(owner, "GetBagID")) or nil) or nil
  local slot = bag and num(meth(owner, "GetID")) or nil
  if bag and slot and S.id[bag * 1000 + slot] ~= id then bag, slot = nil, nil end
  local text, r, gg, b = M.TipLine(id, bag, slot)
  if not text then return end
  meth(tt, "AddLine", text, r, gg, b, true)
  meth(tt, "Show")
end
M.OnTooltip = function(tt, data) local ok, err = pcall(onTip, tt, data) if not ok and T._errorHook then pcall(T._errorHook, err, "Bags:tooltip") end end
local tipHooked = setmetatable({}, { __mode = "k" })
function M.HookTooltip(TDP, E)
  if type(TDP) == "table" and type(TDP.AddTooltipPostCall) == "function" and type(E) == "table" and E.Item ~= nil then
    if not tipHooked[TDP] then
      tipHooked[TDP] = true
      pcall(TDP.AddTooltipPostCall, E.Item, M.OnTooltip)
    end
    return true
  end
  local tt = g("GameTooltip")
  if type(tt) == "table" and tt.HookScript and not tipHooked[tt] then
    tipHooked[tt] = true
    pcall(tt.HookScript, tt, "OnTooltipSetItem", function(self) M.OnTooltip(self, nil) end)
  end
  return false
end

-- ===================================================================== page « Sacs » de la fenêtre
M.ICON = ICON .. "INV_Misc_Bag_08"
function M.OpenPage()
  local W = T.Window
  if W and type(W.Open) == "function" then W.Open("bags") return true end
  return false
end
local function itemIcon(id)
  local CI = g("C_Item")
  return pub(try((type(CI) == "table" and CI.GetItemIconByID) or g("GetItemIcon"), id))
end
local grouped = M.Grouped
--- résumé bon marché pour un bouton / une barre (LOOK) : ne calcule RIEN si les avis ne sont pas prêts (ready = false)
--- → { ready, sellN, sellValue, missing, free, total, merchant, bank, canSort }
function M.Summary()
  local out = { ready = false, sellN = 0, sellValue = 0, missing = 0, merchant = M.merchantOpen and true or false,
    bank = M.bankOpen and true or false, canSort = M.CanSort() }
  if not M._active then return out end
  if API.FreeSlots then out.free, out.total = API.FreeSlots() end
  if not M.Ready() then return out end
  local sl = M.SellList()
  out.ready, out.sellN, out.sellValue, out.missing = true, #sl.list + sl.rest, sl.value, #M.Shopping()
  return out
end
--- ce que la page montre (aussi pour les tests et le résumé) : tuiles + sections
function M.PageModel()
  local L = M.Lists()
  local sl = M.SellList()
  local shop = M.Shopping()
  local m = { sell = grouped(L.sell), ah = grouped(L.ah), bank = grouped(L.bank), use = grouped(L.use), destroy = grouped(L.destroy),
    keep = grouped(L.keep), shop = shop, sellN = #sl.list, sellValue = sl.value, free = snapshot().free, total = snapshot().total }
  local all = 0
  for _, e in ipairs(L.sell) do all = all + e.value end
  m.sellAll, m.sellStacks = all, #L.sell
  m.sellSub = (#L.sell == 0 and "rien à vendre") or (fmt("%s · %s", plural(#L.sell, "objet", "objets"), money(all))
    .. (M.merchantOpen and "" or " · chez un marchand"))
  local plan = M.bankOpen and M.BankPlan() or nil
  m.sortSub = (plan and (#plan.dep + #plan.wd) > 0 and fmt("banque : %d à déposer, %d à sortir", #plan.dep, #plan.wd))
    or (M.CanSort() and "trie tes sacs (tri du jeu)") or "tri du jeu indisponible ici"
  if #shop == 0 then m.missSub = "rien : ton stock est bon"
  else
    local parts = {}
    for i, e in ipairs(shop) do if i <= 3 then parts[#parts + 1] = fmt("%d %s", e.n, e.label) end end
    m.missSub = tconcat(parts, ", ")
  end
  return m
end
local function rowOf(r, kind)
  local v = r.v
  local tip = {}
  for _, w in ipairs(v.reasons or {}) do tip[#tip + 1] = w end
  if v.keep and v.keep > 0 and v.surplus and v.surplus > 0 then tip[#tip + 1] = fmt("Garde %d, %d en trop.", v.keep, v.surplus) end
  if v.ah then tip[#tip + 1] = fmt("HV ≈ %s l'unité (%s).", money(v.ah), v.ahSrc or "?") end
  if (v.vendor or 0) > 0 then tip[#tip + 1] = fmt("Marchand : %s l'unité.", money(v.vendor)) end
  local mark = M.MarkOf(r.id)
  local e = { text = fmt("%s%s — %s", r.name, r.n > 1 and (" ×" .. r.n) or "", tostring(v.opinion or v.why or "")),
    right = (kind == "sell" or kind == "ah") and ((v.ahUnknown and kind == "ah") and "prix ?" or money(r.value)) or nil,
    icon = itemIcon(r.id), prio = (kind == "sell" or kind == "ah") and 2 or 1, tooltip = tip }
  if kind == "keep" or kind == "bank" or kind == "use" then
    e.text = fmt("%s%s — %s", r.name, r.n > 1 and (" ×" .. r.n) or "", tostring(v.why or ""))
    if mark then e.onClick, e.actionText = function() M.Mark(r.id, nil) end, "retirer ta marque" end
  else
    e.onClick, e.actionText = function() M.Mark(r.id, "keep") end, "garder (ne plus le proposer)"
  end
  return e
end
--- onglets « Sacs | Hôtel des ventes » en tête des deux pages (la navigation garde ses catégories : Sacs vit sous « HV »)
function M.PageTabs(Ly, cur)
  if type(Ly) ~= "table" or type(Ly.tabs) ~= "function" then return end
  local sl = (M._active and M.Ready()) and M.SellList() or nil   -- la pastille seulement si les avis sont déjà prêts
  Ly.tabs({ { "bags", "Sacs", sl and #sl.list > 0 and #sl.list or nil, "Vendre, ranger, ce qu'il te manque" },
    { "hv", "Hôtel des ventes", nil, "Bénéfice : vendre tes sacs à l'HV, acheter-revendre, métiers" } }, cur)
end
function M.RenderPage(Ly, W)
  M.PageTabs(Ly, "bags")
  if not M.Ready() then
    -- les avis se préparent par petits paquets (jamais tout dans l'image du clic) : la page se redessine quand c'est prêt
    Ly.text("Je regarde tes sacs…", "M", "muted")
    M.Warm(function()
      if not (W and W.IsOpen and W.IsOpen() and W.char and W.char.page == "bags") then return end
      if W.Render then W.Render() elseif W.Dirty then W.Dirty() end
    end)
    return
  end
  local m = M.PageModel()
  Ly.tiles({
    { "Vendre", m.sellSub, function()
        if M.merchantOpen then M.SellClick()
        elseif W and W.Flash then W.Flash("Va voir un marchand : la carte « Vendre » s'affiche sous sa fenêtre.", 4) end
      end, { "Vend d'un clic ce que personne ne réclame : les gris, puis le reste (12 au plus par clic).",
        "Jamais tes potions, munitions, objets de quête, compos de métier, équipement utile." },
      false, M.merchantOpen and "Clic : vendre" or "Chez un marchand" },
    { "Ranger", m.sortSub, function()
        if M.bankOpen and M.BankClick() > 0 then return end
        M.Sort()
      end, { "Banque ouverte : dépose ce qui va à la banque, sort ce qu'il te manque.", "Sinon : le tri des sacs du jeu." },
      false, "Clic : ranger" },
    { "Ce qu'il te manque", m.missSub, function()
        local Wn = T.Window
        if #m.shop > 0 and Wn and Wn.pageIndex and Wn.pageIndex.city and Wn.Open then Wn.Open("city") end
      end, { "Potions, nourriture, munitions : le stock conseillé pour ton niveau, compté dans tes sacs.",
        "Repris par les Courses en ville et la liste d'achats de l'HV." }, false, #m.shop > 0 and "Clic : Courses en ville" or nil },
  }, 3)
  if #m.shop > 0 and Ly.header("Il te manque", "bags:miss", #m.shop) then
    for _, e in ipairs(m.shop) do
      Ly.row({ text = e.text .. (e.name and (" — " .. e.name) or ""), prio = 2, icon = e.id and itemIcon(e.id) or nil,
        tooltip = { fmt("Tu en as %d, stock conseillé : %d.", e.have, e.want) } })
    end
  end
  local function section(title, key, list, kind, emptyText, closed)
    if #list == 0 and not emptyText then return end
    if Ly.header(title, key, #list, nil, closed) then
      if #list == 0 then Ly.text(emptyText, "S", "muted") end
      for i, r in ipairs(list) do
        if i > 40 then Ly.text(fmt("… et %d autres", #list - 40), "S", "muted") break end
        Ly.row(rowOf(r, kind))
      end
    end
  end
  section("Vendre au marchand", "bags:sell", m.sell, "sell", "Rien à vendre : tout ce que tu portes te sert, ou personne ne sait encore.")
  section("Hôtel des ventes", "bags:ah", m.ah, "ah", nil)
  section("À utiliser", "bags:use", m.use, "use", nil)
  section("Banque", "bags:bank", m.bank, "bank", nil)
  section("À jeter si tu manques de place", "bags:destroy", m.destroy, "destroy", nil)
  section("Garder", "bags:keep", m.keep, "keep", nil, true)
end

-- ===================================================================== événements (rien de lourd ici)
local function pageShown()
  local W = T.Window
  return W and W._active and type(W.IsOpen) == "function" and W.IsOpen() and W.char and W.char.page == "bags"
end
--- tout ce qui suit un changement de sacs, une fois les sacs posés (jamais dans l'image de l'événement, jamais en combat)
function M.Refresh()
  if not M._active then return end
  if inCombat() then M._afterCombat = true return end
  M.CheckFull()
  if anyBagShown() then M.QueueOverlays() else OV.stale = true end
  if M.merchantOpen then M.Warm(function() M.UpdateCard() stripSoon() end) end
  if M.bankOpen then M.Warm(function() M.UpdateBankCard() end) end
  if M.merchantOpen or resting() then M.UpdateStock() end
  if pageShown() and T.Window.Dirty then T.Window.Dirty() end
  -- (5.0 INTEG) en ville / sacs ouverts : les listes sont tenues à jour (par petits paquets) → pastille « Sacs » vraie
  -- (hors des sacs ouverts / du marchand : au plus une fois par 20 s — un butin en ville ne refait pas tout)
  local open = anyBagShown()
  if not (M.merchantOpen or M.bankOpen) and (open or (resting() and now() - (M._listsAt or -100) >= 20)) and M.SellCount() == nil then
    M._listsAt = now()
    M.Warm(function() M.Lists() stripSoon() end)
  end
end
local function settle()
  if not M._pending then return end
  if now() - (M._lastBag or 0) < 0.3 then after(0.3, settle) return end   -- 0,3 s après le DERNIER événement
  M._pending = nil
  M.Refresh()
end
local function onBags()
  if not M._active then return end
  if inCombat() then M._afterCombat = true return end       -- en combat (chaque tir consomme une munition) : après
  M._lastBag = now()
  if M._pending then return end
  M._pending = true
  after(0.4, settle)
end
M.OnBagsChanged = onBags

local function readWorn()
  M._worn = {}
  for slot = 1, 19 do M._worn[slot] = num(try(g("GetInventoryItemID"), "player", slot)) end
end
local function onEquip(slot)
  slot = num(slot)
  if not (slot and M._worn) then return end
  local new = num(try(g("GetInventoryItemID"), "player", slot))
  local old = M._worn[slot]
  M._worn[slot] = new
  if old and old ~= new and M.char then
    M.char.worn = type(M.char.worn) == "table" and M.char.worn or {}
    M.char.worn[old] = epoch()                              -- tu viens de l'enlever : équipement « en alternance »
  end
  M.Invalidate()                                            -- arme à distance, comparaisons d'équipement
  onBags()
end

function M:OnEnable()
  buildTables()
  if self.char then
    self.char.marks = type(self.char.marks) == "table" and self.char.marks or {}
    self.char.worn = type(self.char.worn) == "table" and self.char.worn or {}
    self.char.vend = type(self.char.vend) == "table" and self.char.vend or {}
    local t = epoch()
    for id, at in pairs(self.char.worn) do if t - (tonumber(at) or 0) > WORN_DAYS * 86400 then self.char.worn[id] = nil end end
  end
  readWorn()
  M.Invalidate()
  -- sacs : le rappel regroupé du cœur (T:OnBags) s'il existe, sinon le nôtre (même règle : ≥ 0,3 s après le dernier)
  if type(T.OnBags) == "function" then T:OnBags(self, function() if M._active then M._pending = nil M.Refresh() end end)
  else self:RegisterEvent("BAG_UPDATE_DELAYED", function() onBags() end) end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    if M._afterCombat then M._afterCombat = nil onBags() end
    if OV.pending then OV.pending = nil M.QueueOverlays(true) end
  end)
  self:RegisterEvent("MERCHANT_SHOW", function()
    if M.merchantOpen then return end
    M.merchantOpen, M.lastSale = true, nil
    M.Rescan()
    -- les avis se préparent dès l'image suivante (par paquets) : la vente auto des gris (0,15 s) et la carte les trouvent prêts
    after(0, function() if M.merchantOpen then M.Warm() end end)
    after(0.3, function()
      if not M.merchantOpen then return end
      M.ReadMerchant()
      M.Refresh()
    end)
    after(1.2, function() if M.merchantOpen and not M.sale then M.UpdateCard() end end)   -- la carte Métiers est arrivée : dessous
  end)
  self:RegisterEvent("MERCHANT_CLOSED", function()
    M.merchantOpen, M.sale, M.merchantSells, M.merchantCats = false, nil, nil, nil
    M.UpdateCard()
    M.UpdateStock()
  end)
  self:RegisterEvent("BANKFRAME_OPENED", function()
    M.bankOpen = true
    M.Rescan()
    after(0.3, function() if M.bankOpen then M.Refresh() end end)
  end)
  self:RegisterEvent("BANKFRAME_CLOSED", function() M.bankOpen = false M.UpdateBankCard() end)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", function(_, _, slot) onEquip(slot) end)
  self:RegisterEvent("PLAYER_UPDATE_RESTING", function() after(1, function() M.UpdateStock() end) end)
  self:RegisterEvent("BAG_OPEN", function() M.QueueOverlays(true) end)
  self:RegisterEvent("BAG_CLOSED", function() M.QueueOverlays(true) end)
  if class() == "HUNTER" and self.RegisterUnitEvent then
    self:RegisterUnitEvent("UNIT_PET", "player", nil, function() PLAN.pet = nil M._shop = nil end)
  end
  local En = g("Enum")
  M.HookTooltip(g("TooltipDataProcessor"), type(En) == "table" and En.TooltipDataType or nil)
  hookBagFrames()
  hookThird()
  -- (5.0 INTEG, contrat LOOK) le bouton « Sacs » de la bande : un clic = tes sacs ouverts avec les pièces (vendre / HV /
  -- banque) ; pastille = objets à vendre (seulement quand le compte est frais) ; infobulle = ce qu'il y a à faire
  local Sc = T.Shortcuts
  if type(Sc) == "table" and type(Sc.SetAction) == "function" then
    pcall(Sc.SetAction, "sacs", {
      fn = function()
        if not M._active then return false end
        local ok = M.ShowBags()
        if ok then M.Warm(function() M.Lists() M.QueueOverlays(true) stripSoon() end) end
        return ok
      end,
      count = function() local n = M.SellCount() if n and n > 0 then return n end return nil end,
      tip = function()
        if not M._active then return nil end
        local n, L = M.SellCount()
        local out = {}
        if n == nil then out[1] = "Ouvre tes sacs : TreeAI marque ce qui est à vendre, à l'HV, à la banque."
        elseif n == 0 then out[1] = "Rien à vendre pour l'instant."
        else
          local v = 0
          for _, e in ipairs(L.sell) do v = v + (e.value or 0) end
          out[1] = fmt("Pastille : %s à vendre au marchand (%s)", plural(n, "objet", "objets"), money(v))
          if #L.ah > 0 then out[#out + 1] = fmt("%s pour l'hôtel des ventes", plural(#L.ah, "objet", "objets")) end
        end
        local miss = M._shop and #M._shop > 0 and M.MissingText and M.MissingText() or nil
        if type(miss) == "string" and miss ~= "" then out[#out + 1] = miss end
        return out
      end })
  end
  local W = T.Window
  if W and type(W.AddPage) == "function" and not M._page then
    M._page = true
    -- rangée sous « HV » (hors navigation : elle garde ses 11 catégories) ; les deux pages portent les mêmes onglets
    W.AddPage({ id = "bags", hidden = true, parent = "hv", label = "Sacs", icon = M.ICON, title = "Sacs",
      desc = "Vendre, ranger, ce qu'il te manque",
      render = function(Ly) if M._active then M.RenderPage(Ly, W) else Ly.empty("L'assistant d'inventaire est coupé (Réglages → Fonctions → Sacs).") end end })
    local hv = W.pageIndex and W.pageIndex.hv
    if type(hv) == "table" and type(hv.render) == "function" then
      local orig = hv.render
      W.AddPage({ id = "hv", render = function(Ly, ...)
        if M._active then pcall(M.PageTabs, Ly, "hv") end
        return orig(Ly, ...)
      end })
    end
  end
  after(4, function() hookBagFrames() hookThird() M.Refresh() end)
end
function M:OnDisable()
  M.merchantOpen, M.bankOpen, M.sale, M._pending = false, false, nil, nil
  for _, f in pairs(CARD) do meth(f, "Hide") end
  clearOverlays()
  if OV.legend then meth(OV.legend, "Hide") end
end
function M:OnOptionChanged()
  M.Invalidate()
  M.RefreshOverlays(true)
  M.Refresh()
end

-- /tai sacs · /tai garde <lien> · /tai vends <lien> · /tai oublie <lien>
if T.RegisterSlash then
  local function idOf(rest) return tonumber(tostring(rest or ""):match("item:(%d+)")) or tonumber((tostring(rest or ""):match("^%s*(%d+)%s*$"))) end
  local function marker(kind, done)
    return function(rest)
      local id = idOf(rest)
      if not id then note("Maj-clic sur l'objet après la commande (ex. /tai garde [objet]).") return end
      if not M._active then note("L'assistant d'inventaire est coupé.") return end
      M.Mark(id, kind)
      note(fmt("%s : %s", itemName(id), done))
    end
  end
  if not (T.HasSlash and T:HasSlash("sacs")) then T:RegisterSlash("sacs", function() M.OpenPage() end, "sacs : la page Sacs (vendre, ranger, ce qu'il te manque)") end
  if not (T.HasSlash and T:HasSlash("garde")) then T:RegisterSlash("garde", marker("keep", "marqué « garder » (plus jamais proposé à la vente)"), "garde [objet] : ne plus jamais le proposer à la vente") end
  if not (T.HasSlash and T:HasSlash("vends")) then T:RegisterSlash("vends", marker("sell", "marqué « vendre »"), "vends [objet] : le proposer à la vente chez le marchand") end
  if not (T.HasSlash and T:HasSlash("oublie")) then T:RegisterSlash("oublie", marker(nil, "marque retirée"), "oublie [objet] : retire ta marque garder / vendre") end
end
