-- TreeAI — « Courses en ville » (agent CITYRUN, 4.1) : tout ce qu'il y a à acheter / faire en ville MAINTENANT, dans
-- le bon ordre, en un clic.
--  * LISTE : plan de métier (ProfLevel : compos à acheter chez le marchand, achats HV acceptés avec prix plafond,
--    recettes / rang à apprendre chez le maître, retraits de banque, courrier), maître de classe (nouveaux rangs, prix
--    lu chez lui), munitions du chasseur (flèches / balles selon SON arme), nourriture du familier selon SON régime,
--    nourriture / eau, composants de classe, réparation, vente des gris et des compos « en trop », sortie (maître de
--    vol / zeppelin / prochain arrêt du trajet), pierre de foyer si utile. Chaque ligne dit son PNJ et pourquoi ; les
--    quantités sont exactes (lots du marchand lus à ta visite) ; l'or est vérifié (« il te manque 3 pa » : la course la
--    moins urgente est retirée, et c'est dit).
--  * POSITIONS : PNJ appris à TA visite (MERCHANT_SHOW, TRAINER_SHOW, BANKFRAME_OPENED, MAIL_SHOW, AUCTION_HOUSE_SHOW,
--    TAXIMAP_OPENED, aubergiste) : nom + carte + x, y du joueur à ce moment (+ ce que le marchand vend, prix et lot) ;
--    puis la mémoire de ProfLevel et de Detect, les tables Data (vols, quais), et QuestieDB seulement en plus. Aucune
--    coordonnée inventée : un PNJ inconnu = « demande à un garde », appris à la visite.
--  * TRAJET : ordre optimal (exact jusqu'à 9 arrêts, sinon plus proche voisin + 2-opt) depuis ta position jusqu'à la
--    sortie, en temps de marche (T.PathLearn.LegSec si présent, sinon NavTime.Model) ; PNJ d'un même bâtiment regroupés.
--  * MODE FOCUS (contrat T.focus de ProfLevel : kind = "city", owner = "CityRun") : trajet de quêtes en pause, boussole
--    sur l'arrêt choisi, chaque arrêt se coche seul (fenêtre du jeu ouverte puis achats faits / fenêtre fermée), petite
--    carte sous la fenêtre du jeu (UN clic = UNE action : vendre une pile, réparer, acheter un lot, retirer une pile,
--    recherche / achat HV via ProfLevel), « Fini : X dépensées, 4 min », puis le trajet reprend (ou la session métier
--    enchaîne). Clic sur un arrêt = c'est lui maintenant ; clic droit = le passer ; « Étape suivante / précédente » +
--    touches. Ton choix n'est jamais écrasé par un recalcul.
--  * MÉTIERS : « Me concentrer sur les métiers » (courses d'abord si besoin, puis la session métier) ; options
--    comparées honnêtement (acheter / apprendre chez le maître / tes compos), coût par point, « Mon choix » gardé ;
--    verdict par compo (« Besoin : 12 (il t'en reste 30 → 18 en trop) », « Pas besoin », « Garde : quête X ») pour
--    l'infobulle (ItemInfo), la pastille « trop » des sacs (Highlight) et la liste « À vendre / à jeter ».
-- Lua 5.1, aucune globale (sauf les noms de touches BINDING_NAME_*), rien dans le chat, aucune action protégée hors clic.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local CR = T:RegisterModule("CityRun", {
  title = "Courses en ville",
  defaults = { enabled = true, propose = true, pins = true, townToast = true, npcs = {} },
  charDefaults = { run = false, choice = {}, pick = {} },
  options = {
    { key = "propose", type = "toggle", label = "Proposer « Courses en ville » en entrant dans une ville (2 courses ou plus)", section = "Courses en ville" },
    { key = "pins", type = "toggle", label = "Points métiers sur la carte (maîtres, marchands utiles, banque, HV…)", section = "Courses en ville" },
    { key = "townToast", type = "toggle", label = "En arrivant en ville : une petite alerte « Ici : réparer, flèches… » (une fois par visite)", section = "Courses en ville" },
  },
})
T.CityRun = CR

local G = _G
local API = T.API or {}
local floor, max, min, ceil, huge = math.floor, math.max, math.min, math.ceil, math.huge
local fmt, tconcat, tsort, tinsert, tremove = string.format, table.concat, table.sort, table.insert, table.remove
local unpack = unpack or table.unpack
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function pub(v) if secret(v) then return nil end return v end
local function num(v) return tonumber(pub(v)) end
local function str(v) v = pub(v) return type(v) == "string" and v ~= "" and v or nil end
local function now() return num(try(g("GetTime"))) or 0 end
local function epoch() return num(try(g("time"))) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function cut(s, n) return (T.Cut and T.Cut(s, n)) or tostring(s or "") end
local function money(c) local F = T.Fmt return (F and F.Money and F.Money(c)) or (floor((c or 0) / 100) .. " pa") end
local function say(msg) if T.ClickNote then T:ClickNote(msg) end end
local function refuse(msg) if T.CombatRefused then T:CombatRefused(msg) end end
local function PL() local P = T.ProfLevel return type(P) == "table" and P or nil end
local function Prof() local P = T.Prof return (type(P) == "table" and P.IsEnabled and P:IsEnabled()) and P or nil end
local function mod(n) local m = T.modules and T.modules[n] if type(m) == "table" and (not m.IsEnabled or m:IsEnabled()) then return m end return nil end
local function recipes() local d = T.Data and T.Data.Recipes return type(d) == "table" and type(d.r) == "table" and d or nil end
local function bagCount(id) return num(API.ItemCount and API.ItemCount(id, false)) or 0 end
local function playerMoney() return num(API.Money and API.Money()) or num(try(g("GetMoney"))) or 0 end
local function itemName(id)
  local n = API.ItemName and API.ItemName(id)
  if type(n) == "string" and n ~= "" then return n end
  local P = PL()
  if P and P.MatName then return P.MatName(id) end
  return "objet " .. tostring(id)
end
local function minutes(sec)
  sec = tonumber(sec) or 0
  if sec < 60 then return "< 1 min" end
  local m = floor(sec / 60 + 0.5)
  if m < 60 then return fmt("%d min", m) end
  return fmt("%d h %02d", floor(m / 60), m % 60)
end
CR.Minutes = minutes
local function myClass() local _, c = try(g("UnitClass"), "player") return str(c) end
local function level() return num((try(g("UnitLevel"), "player"))) or 1 end
local function here() local m, x, y = T:Here() if m and x then return { mapID = m, x = x, y = y } end return nil end
local function geo() local C = T.Compass return type(C) == "table" and type(C.Geo) == "table" and C.Geo or nil end
local function dist(a, b)
  if not (a and b) then return nil end
  local Gm = geo()
  if Gm and Gm.Vector then return (Gm.Vector(a, b)) end
  return nil
end
local function distText(yd) local Gm = geo() if yd and Gm and Gm.Dist then return Gm.Dist(yd) end return nil end
local function lineName(line) local d = recipes() local v = d and d.lines and d.lines[line] return v and v[2] or ("métier " .. tostring(line)) end

local ICON = "Interface\\Icons\\"
CR.ICON = ICON .. "INV_Misc_Bag_10"
local KIND_FR = { vendor = "Marchand", repair = "Réparation", ctrainer = "Maître de classe", ptrainer = "Maître de métier",
  bank = "Banque", mail = "Boîte aux lettres", ah = "Hôtel des ventes", flight = "Maître de vol", inn = "Aubergiste",
  sell = "Marchand", dock = "Zeppelin" }
CR.KIND_FR = KIND_FR
local KIND_ICON = { vendor = "INV_Misc_Coin_02", repair = "Trade_BlackSmithing", ctrainer = "INV_Misc_Book_11",
  ptrainer = "INV_Misc_Book_09", bank = "INV_Box_02", mail = "INV_Letter_15", ah = "INV_Misc_Coin_17", inn = "INV_Misc_Rune_01",
  sell = "INV_Misc_Coin_01", flight = "Ability_Mount_Wyvern_01" }
-- capitales (cartes de ville) : tout le plan de la ville compte ; ailleurs (village, la Croisée…) : PNJ à moins de 300 m
local CAPITALS = { [1454] = true, [1456] = true, [1458] = true, [1453] = true, [1455] = true, [1457] = true }
CR.CAPITALS = CAPITALS
local TOWN_YD, NEAR_YD, AT_YD, SERVICE_SEC = 330, 20, 40, 20
CR.NEAR_YD, CR.SERVICE_SEC = NEAR_YD, SERVICE_SEC
-- munitions vendues par les marchands (objets du jeu ; niveau requis lu dans Data.Consumables.ammo)
local VENDOR_AMMO = { arrow = { 2512, 2515, 3030, 11285 }, bullet = { 2516, 2519, 3033, 11284 } }
-- composants de classe vendus en ville : { objet, niveau dès lequel il sert, quantité voulue }
local REAGENTS = { SHAMAN = { { 17030, 30, 5 } }, ROGUE = { { 5140, 22, 10 } }, PALADIN = { { 17033, 30, 5 } },
  DRUID = { { 17034, 20, 5 } }, PRIEST = { { 17028, 48, 10 } },
  MAGE = { { 17031, 20, 5 }, { 17032, 40, 5 }, { 17020, 56, 20 } } }
-- priorité (la plus basse sort la première quand l'or manque) ; la vente rapporte, jamais retirée
local PRIO = { sell = 100, repair = 90, ammo = 85, ctrainer = 80, petfood = 70, food = 60, water = 60, reagent = 55,
  ptrainer = 50, mats = 40, ah = 30, bank = 20, mail = 20, inn = 10 }
CR.PRIO = PRIO

-- ===================================================================== mémoire des PNJ (compte)
-- npcs[mapID][nom] = { x, y, t, k = {vendor, repair, ctrainer, ptrainer, bank, mail, ah, flight, inn}, i = {[objet] =
-- {prix d'un lot, lot}}, l = métier (maître de métier), c = classe (maître de classe) }
local MAX_MAPS, MAX_NPCS, MAX_ITEMS = 25, 40, 60
-- ce que vend un marchand : une chaîne compacte « objet:prix:lot,… » (SavedVariables légères), lue une fois
local parsed = setmetatable({}, { __mode = "k" })
function CR.ItemsOf(e)
  if type(e) ~= "table" then return {} end
  if type(e.i) == "table" then return e.i end
  local s = e.s
  if type(s) ~= "string" or s == "" then return {} end
  local c = parsed[e]
  if c and c.s == s then return c.t end
  local t = {}
  for id, pr, lot in s:gmatch("(%d+):(%d+):(%d+)") do t[tonumber(id)] = { tonumber(pr), tonumber(lot) } end
  parsed[e] = { s = s, t = t }
  return t
end
local function encodeItems(items)
  local ids = {}
  for id in pairs(items) do ids[#ids + 1] = id end
  tsort(ids)
  local parts = {}
  for _, id in ipairs(ids) do local v = items[id] parts[#parts + 1] = fmt("%d:%d:%d", id, floor(v[1] or 0), floor(v[2] or 1)) end
  return tconcat(parts, ",")
end
local function mem()
  local d = CR.db
  if type(d) ~= "table" then return {} end
  if type(d.npcs) ~= "table" then d.npcs = {} end
  return d.npcs
end
CR.Mem = mem
--- retient le PNJ (ou l'objet : boîte aux lettres) devant lequel tu es : nom, carte, x, y (position du joueur à
--- l'ouverture de sa fenêtre, à portée d'interaction) ; extra = { items, line, cls }
function CR.Remember(kind, name, extra)
  local p = here()
  if not (p and KIND_FR[kind]) then return nil end
  local all = mem()
  local key = tostring(p.mapID)
  local list = all[key]
  if type(list) ~= "table" then
    local n, oldK, oldT = 0, nil, nil
    for k, l in pairs(all) do
      n = n + 1
      local t = 0
      for _, e in pairs(type(l) == "table" and l or {}) do if type(e) == "table" then t = max(t, tonumber(e.t) or 0) end end
      if not oldT or t < oldT then oldK, oldT = k, t end
    end
    if n >= MAX_MAPS and oldK then all[oldK] = nil end
    list = {}
    all[key] = list
  end
  name = str(name)
  if name then name = cut(name, 40) end
  if not name then name = fmt("%s@%d,%d", KIND_FR[kind], floor(p.x * 1000), floor(p.y * 1000)) end
  local e = list[name]
  if type(e) ~= "table" then
    local n, oldK, oldT = 0, nil, nil
    for k, v in pairs(list) do
      n = n + 1
      local t = type(v) == "table" and tonumber(v.t) or 0
      if not oldT or t < oldT then oldK, oldT = k, t end
    end
    if n >= MAX_NPCS and oldK then list[oldK] = nil end
    e = { k = {} }
    list[name] = e
  end
  e.x, e.y, e.t = floor(p.x * 1e4) / 1e4, floor(p.y * 1e4) / 1e4, epoch()
  e.k = type(e.k) == "table" and e.k or {}
  e.k[kind] = true
  extra = extra or {}
  if extra.items then e.s, e.i = encodeItems(extra.items), nil end
  if extra.line then e.l = extra.line end
  if extra.cls then e.c = extra.cls end
  CR.memGen = (CR.memGen or 0) + 1
  if CR.PinsDirty then CR.PinsDirty() end
  return e, name, p.mapID
end
local function shownName(name) return (tostring(name or ""):gsub("@[%d,]+$", "")) end
CR.ShownName = shownName

-- objets qui comptent chez un marchand (munitions, nourriture, eau, familier, compos de métier vendues, composants)
local relevant
local function relevantSet()
  if relevant then return relevant end
  relevant = {}
  local C = T.Data and T.Data.Consumables
  for _, k in ipairs({ "ammo", "food", "water" }) do
    for _, r in ipairs(C and C[k] or {}) do if type(r) == "table" and r[1] then relevant[r[1]] = k end end
  end
  for _, r in ipairs(T.Data and T.Data.PetFood or {}) do if type(r) == "table" and r[1] then relevant[r[1]] = relevant[r[1]] or "petfood" end end
  local d = recipes()
  for id, v in pairs(d and d.mats or {}) do if type(v) == "table" and v[3] == 1 then relevant[id] = relevant[id] or "mats" end end
  for _, list in pairs(REAGENTS) do for _, r in ipairs(list) do relevant[r[1]] = "reagent" end end
  for _, list in pairs(VENDOR_AMMO) do for _, id in ipairs(list) do relevant[id] = "ammo" end end
  return relevant
end
CR.Relevant = relevantSet
local function merchantID(i)
  local id = num((try(g("GetMerchantItemID"), i)))
  if id then return id end
  local link = str((try(g("GetMerchantItemLink"), i)))
  return link and tonumber(link:match("item:(%d+)")) or nil
end
local function merchantInfo(i)
  local MF = g("C_MerchantFrame")
  local info = MF and MF.GetItemInfo and try(MF.GetItemInfo, i)
  if type(info) == "table" then return num(info.price) or 0, max(1, num(info.stackCount) or 1), num(info.numAvailable) or -1 end
  local _, _, price, lot, avail = try(g("GetMerchantItemInfo"), i)
  return num(price) or 0, max(1, num(lot) or 1), num(avail) or -1
end
--- marchand ouvert : son nom, sa place, ce qu'il vend d'utile (prix d'un lot, taille du lot), s'il répare
function CR.LearnMerchant()
  local rel = relevantSet()
  local items, n = {}, 0
  for i = 1, min(num((try(g("GetMerchantNumItems")))) or 0, 150) do
    local id = merchantID(i)
    if id and rel[id] and n < MAX_ITEMS then
      local price, lot = merchantInfo(i)
      items[id] = { price, lot }
      n = n + 1
    end
  end
  local e = CR.Remember("vendor", (try(g("UnitName"), "npc")), { items = items })
  if e and pub(try(g("CanMerchantRepair"))) then e.k.repair = true end
  return e
end
--- maître ouvert : de métier (la ligne) ou de TA classe
function CR.LearnTrainer()
  local name = (try(g("UnitName"), "npc"))
  if try(g("IsTradeskillTrainer")) then
    local P = T.Prof
    local sk = str((try(g("GetTrainerServiceSkillLine"), 1))) or str((try(g("GetTrainerServiceSkillReq"), 1)))
    local line = sk and P and P.LineOf and P.LineOf(sk)
    if line then return CR.Remember("ptrainer", name, { line = line }) end
    return nil
  end
  local _, cls = try(g("UnitClass"), "npc")
  cls = str(cls)
  if cls and cls == myClass() and (num((try(g("GetNumTrainerServices")))) or 0) > 0 then
    return CR.Remember("ctrainer", name, { cls = cls })
  end
  return nil
end
--- dialogue ouvert : un aubergiste (option « lier ta pierre ») est retenu
function CR.LearnGossip()
  local GI = g("C_GossipInfo")
  local binder = false
  local opts = GI and GI.GetOptions and try(GI.GetOptions)
  for _, o in ipairs(type(opts) == "table" and opts or {}) do
    if type(o) == "table" and (o.type == "binder" or o.icon == 132052) then binder = true end
  end
  if not binder then
    local list = { try(g("GetGossipOptions")) }
    for k = 2, #list, 2 do if list[k] == "binder" then binder = true end end
  end
  if binder then return CR.Remember("inn", (try(g("UnitName"), "npc"))) end
  return nil
end

-- ===================================================================== où sont les PNJ (carte actuelle)
local function inTownRange(p, c)
  if CAPITALS[p.mapID] then return c.mapID == p.mapID end
  local d = dist(p, c)
  return d ~= nil and d <= TOWN_YD
end
local DETECT_KIND = { repair = "repair", bank = "bank", mail = "mailbox", ah = "auction", flight = "flight", inn = "innkeeper" }
--- nœud de vol / quai (Data.Travel : "H", "A", "AH" ; quai : nil) ouvert à ta faction ?
function CR.FactionOK(code, fac)
  if code == nil or code == false then return true end
  local letter = ({ Horde = "H", Alliance = "A" })[fac or ""]
  return type(code) == "string" and letter ~= nil and code:find(letter, 1, true) ~= nil
end
--- candidats pour un genre de PNJ : { {name, mapID, x, y, src, covers = {[objet] = {prix, lot}}, repair} } ; mémoire de
--- CityRun d'abord, puis ProfLevel / Detect (vus + Questie), jamais une position inventée. opt = { ids, line }
function CR.Candidates(kind, opt)
  opt = opt or {}
  local p = here()
  if not p then return {} end
  local out, byName = {}, {}
  local function add(c)
    if not (c and c.name and tonumber(c.x) and tonumber(c.y) and c.mapID) then return end
    if not inTownRange(p, c) then return end
    local prev = byName[c.name]
    if prev then
      for id, v in pairs(c.covers or {}) do prev.covers[id] = prev.covers[id] or v end
      prev.repair = prev.repair or c.repair
      return
    end
    c.covers = c.covers or {}
    c.dist = dist(p, c)
    byName[c.name] = c
    out[#out + 1] = c
  end
  local list = mem()[tostring(p.mapID)]
  for name, e in pairs(type(list) == "table" and list or {}) do
    if type(e) == "table" and type(e.k) == "table" and tonumber(e.x) then
      local ok = false
      if kind == "vendor" then ok = e.k.vendor
      elseif kind == "repair" then ok = e.k.repair
      elseif kind == "ctrainer" then ok = e.k.ctrainer and (e.c == nil or e.c == myClass())
      elseif kind == "ptrainer" then ok = e.k.ptrainer and e.l == opt.line
      else ok = e.k[kind] end
      if ok then
        local covers = {}
        for id, v in pairs(CR.ItemsOf(e)) do covers[tonumber(id) or id] = v end
        add({ name = name, mapID = p.mapID, x = e.x, y = e.y, src = "vu", covers = covers, repair = e.k.repair and true or nil })
      end
    end
  end
  local P = PL()
  local pdb = T.Prof and T.Prof.db
  if kind == "vendor" and type(pdb) == "table" and type(pdb.vend) == "table" then
    for name, e in pairs(type(pdb.vend[tostring(p.mapID)]) == "table" and pdb.vend[tostring(p.mapID)] or {}) do
      if type(e) == "table" and tonumber(e.x) and type(e.i) == "table" then
        local covers = {}
        for _, id in ipairs(e.i) do covers[id] = true end
        add({ name = name, mapID = p.mapID, x = e.x, y = e.y, src = "vu", covers = covers })
      end
    end
  end
  if kind == "vendor" and P and P.QuestieVendors and opt.ids then
    for _, id in ipairs(opt.ids) do
      local found = false
      for _, c in ipairs(out) do if c.covers[id] then found = true break end end
      if not found then
        for _, c in ipairs(P.QuestieVendors({ id }, p) or {}) do
          add({ name = c.name, mapID = c.mapID, x = c.x, y = c.y, src = "Questie", covers = { [id] = true } })
        end
      end
    end
  end
  if kind == "ptrainer" and P and P.WhereTrainer and #out == 0 then
    local w = P.WhereTrainer(opt.line)
    if w then add({ name = w.name, mapID = w.mapID, x = w.x, y = w.y, src = w.src }) end
  end
  local Dk = DETECT_KIND[kind]
  local Dt = T.Detect
  if Dk and type(Dt) == "table" and type(Dt.Services) == "function" then
    for _, e in ipairs(try(Dt.Services, Dk) or {}) do
      if type(e) == "table" and (Dk ~= "flight" or e.known ~= false) and (Dk ~= "repair" or e.kind == "repair") then
        add({ name = e.name or KIND_FR[kind], mapID = e.mapID, x = e.x, y = e.y, src = e.src, repair = (Dk == "repair") or nil })
      end
    end
  end
  if kind == "flight" or kind == "dock" then
    local Tr = T.Data and T.Data.Travel
    local fac = str((try(g("UnitFactionGroup"), "player")))
    for _, r in pairs(Tr and (kind == "flight" and Tr.taxi or Tr.docks) or {}) do
      if type(r) == "table" and tonumber(r[6]) and CR.FactionOK(r[8], fac) then
        add({ name = r[1], mapID = r[5], x = r[6] / 100, y = r[7] / 100, src = "data" })
      end
    end
  end
  tsort(out, function(a, b) return (a.dist or 1e12) < (b.dist or 1e12) end)
  return out
end
--- es-tu en ville ? capitale, ou au repos (auberge) avec au moins 2 services connus tout près
function CR.InTown()
  local p = here()
  if not p then return false end
  if CAPITALS[p.mapID] then return true end
  if not try(g("IsResting")) then return false end
  local n = 0
  local list = mem()[tostring(p.mapID)]
  for _, e in pairs(type(list) == "table" and list or {}) do
    if type(e) == "table" and tonumber(e.x) then
      local d = dist(p, { mapID = p.mapID, x = e.x, y = e.y })
      if d and d <= TOWN_YD then n = n + 1 end
    end
  end
  return n >= 2
end

-- ===================================================================== temps de marche et ordre (purs + modèle)
--- secondes de marche de a à b : T.PathLearn.LegSec (appris) si présent, sinon NavTime.Model (vitesse et détour
--- mesurés), sinon 7 m/s × 1,15
function CR.LegSec(a, b)
  if not (a and b) then return nil end
  local PLn = T.PathLearn
  if type(PLn) == "table" and type(PLn.LegSec) == "function" then
    local s = num(try(PLn.LegSec, a, b))
    if s then return s end
  end
  local yd = dist(a, b)
  if not yd then return nil end
  local N = T.NavTime
  if type(N) == "table" and type(N.Model) == "function" then
    local s = num(try(N.Model, yd, b.mapID or a.mapID))
    if s then return s end
  end
  return yd * 1.15 / 7
end
local function bit(mask, i) return floor(mask / 2 ^ (i - 1)) % 2 == 1 end
--- ordre des n arrêts (1..n) depuis le départ (0) jusqu'à l'arrivée (n + 1, si hasEnd) : cost(i, j) en secondes ;
--- exact (programmation dynamique) jusqu'à 9 arrêts, sinon plus proche voisin + 2-opt → ordre, coût
function CR.Order(n, cost, hasEnd)
  if n == 0 then return {}, hasEnd and (cost(0, 1) or 0) or 0 end
  local function c(i, j) return cost(i, j) or 1e6 end
  local E = n + 1
  if n <= 9 then
    local full = 2 ^ n - 1
    local dp, par = {}, {}
    for j = 1, n do
      local m = 2 ^ (j - 1)
      dp[m] = dp[m] or {}
      par[m] = par[m] or {}
      dp[m][j] = c(0, j)
    end
    for mask = 1, full do
      local row = dp[mask]
      if row then
        for j = 1, n do
          local v = row[j]
          if v and bit(mask, j) then
            for k = 1, n do
              if not bit(mask, k) then
                local m2 = mask + 2 ^ (k - 1)
                local nv = v + c(j, k)
                dp[m2] = dp[m2] or {}
                par[m2] = par[m2] or {}
                if not dp[m2][k] or nv < dp[m2][k] - 1e-9 then dp[m2][k], par[m2][k] = nv, j end
              end
            end
          end
        end
      end
    end
    local best, bj = nil, nil
    for j = 1, n do
      local v = dp[full][j]
      if v then
        v = v + (hasEnd and c(j, E) or 0)
        if not best or v < best - 1e-9 then best, bj = v, j end
      end
    end
    local order, mask, j = {}, full, bj
    while j do
      tinsert(order, 1, j)
      local pj = par[mask] and par[mask][j]
      mask = mask - 2 ^ (j - 1)
      j = pj
    end
    return order, best
  end
  -- plus proche voisin
  local order, used, cur = {}, {}, 0
  for _ = 1, n do
    local bk, bv = nil, nil
    for k = 1, n do if not used[k] and (not bv or c(cur, k) < bv) then bk, bv = k, c(cur, k) end end
    used[bk] = true
    order[#order + 1] = bk
    cur = bk
  end
  local function total(o)
    local s, prev = 0, 0
    for _, k in ipairs(o) do s = s + c(prev, k) prev = k end
    if hasEnd then s = s + c(prev, E) end
    return s
  end
  -- 2-opt (chemin : départ et arrivée fixes)
  local best = total(order)
  local improved, guard = true, 0
  while improved and guard < 50 do
    improved, guard = false, guard + 1
    for i = 1, n - 1 do
      for j = i + 1, n do
        local o = {}
        for k = 1, i - 1 do o[#o + 1] = order[k] end
        for k = j, i, -1 do o[#o + 1] = order[k] end
        for k = j + 1, n do o[#o + 1] = order[k] end
        local t = total(o)
        if t < best - 1e-9 then order, best, improved = o, t, true end
      end
    end
  end
  return order, best
end

-- ===================================================================== verdict des compos (« en trop » ou pas)
-- [objet] = { {line, row}, ... } : recettes qui s'en servent, cherchées à la demande pour les seuls objets vus (pas
-- d'index de toutes les compos en mémoire) ; false = pas une compo
local matUse = {}
local matUseN = 0
local function uses(id)
  id = tonumber(id)
  if not id then return nil end
  local c = matUse[id]
  if c ~= nil then return c or nil end
  local d = recipes()
  local l = {}
  for line, rows in pairs(d and d.r or {}) do
    for _, r in ipairs(rows) do
      local m = r[10] or {}
      for k = 1, #m - 1, 2 do if m[k] == id then l[#l + 1] = { line = line, row = r } break end end
    end
  end
  if matUseN > 300 then matUse, matUseN = {}, 0 end
  matUse[id], matUseN = (#l > 0) and l or false, matUseN + 1
  return matUse[id] or nil
end
CR.Uses = uses
--- calcule fn() sans garder les plans qu'il a demandés à ProfLevel (sauf si la page Métiers / une session s'en sert)
local function tempPlans(fn, ...)
  local P = PL()
  if not (P and type(P.cache) == "table" and type(P.cacheOrder) == "table") then return fn(...) end
  if P.focus or (P.PageShown and P.PageShown()) then return fn(...) end
  local before = {}
  for _, k in ipairs(P.cacheOrder) do before[k] = true end
  local r = { pcall(fn, ...) }
  local keep = {}
  for _, k in ipairs(P.cacheOrder) do
    if before[k] then keep[#keep + 1] = k else P.cache[k] = nil end
  end
  for i = #P.cacheOrder, 1, -1 do P.cacheOrder[i] = nil end
  for i, k in ipairs(keep) do P.cacheOrder[i] = k end
  if not r[1] then return nil end
  return unpack(r, 2)
end
CR.TempPlans = tempPlans
local verdictCache = { sig = nil, at = -100, map = {} }
local function profRanks()
  local P = Prof()
  local out = {}
  for _, p in ipairs(P and (P.profs or (P.ScanProfessions and P.ScanProfessions())) or {}) do
    if p.line then out[p.line] = tonumber(p.rank) or 0 end
  end
  return out
end
local function planLines()
  local d, P = recipes(), Prof()
  local out = {}
  if not (d and P) then return out end
  for _, p in ipairs(P.profs or P.ScanProfessions() or {}) do
    local k = d.lines[p.line]
    if k and k[1] ~= "g" and d.r[p.line] and #d.r[p.line] > 0 then out[#out + 1] = p.line end
  end
  return out
end
CR.PlanLines = planLines
--- verdict (pur) d'une compo : have (sacs + banque + courrier), need (ce que tes plans consomment, le plus grand des
--- deux modes), reserve (quêtes), soon (recette utile d'ici 50 points, au-delà de la cible du plan), used (tes
--- métiers s'en servent encore), other (métiers que tu n'as pas) → { text, tone, extra, keep, details }
function CR.VerdictOf(v)
  local have, need, rsv = v.have or 0, v.need or 0, v.reserve or 0
  local out = { details = {}, have = have, need = need }
  local pre = rsv > 0 and fmt("Garde : quête « %s » (%d)", cut(v.quest or "?", 28), rsv) or nil
  if need > 0 or rsv > 0 then
    local spare = have - need - rsv
    if need > 0 then
      if spare > 0 and not v.soon then
        out.text = fmt("Besoin : %d (il t'en reste %d → %d en trop)", need, have, spare)
        out.tone, out.extra = "warn", spare
      else
        out.text = fmt("Besoin : %d (tu en as %d)", need, have)
        out.tone, out.keep = "good", true
        if spare > 0 and v.soon then out.details[#out.details + 1] = fmt("Le reste (%d) servira : « %s » dès %d.", spare, cut(v.soon.name, 30), v.soon.at) end
      end
      if pre then out.text = pre .. " · " .. out.text end
    else
      out.text, out.tone, out.keep = pre, "good", true
      if spare > 0 and not v.soon and not v.usedSoon then out.text = pre .. fmt(" · %d en trop", spare) out.extra = spare out.tone = "warn" end
    end
    return out
  end
  if v.usedSoon then
    out.text = fmt("Garde : sert à « %s » (dès %d)", cut(v.usedSoon.name, 30), v.usedSoon.at)
    out.tone, out.keep = "good", true
    return out
  end
  if have <= 0 then return nil end
  if v.mine then
    out.text, out.tone, out.extra = "Pas besoin : aucune recette utile à ton niveau", "warn", have
    if v.later then out.details[#out.details + 1] = fmt("Servira plus tard : « %s » dès %d.", cut(v.later.name, 30), v.later.at)
    else out.details[#out.details + 1] = "Tes recettes qui s'en servent sont grises (plus aucun point)." end
    return out
  end
  if v.other then
    out.text, out.tone, out.extra = fmt("Pas besoin : sert en %s (pas ton métier)", v.other), "warn", have
    return out
  end
  return nil
end
--- verdicts de toutes les compos de métier de tes sacs + banque (mémorisés jusqu'au prochain changement)
local verdictsRaw
function CR.Verdicts()
  local P, Pr = PL(), Prof()
  if not (P and Pr and P.Compute) then return {} end
  return tempPlans(verdictsRaw) or {}
end
verdictsRaw = function()
  local P, Pr = PL(), Prof()
  if not (P and Pr and P.Compute) then return {} end
  local ranks = profRanks()
  if not next(ranks) then return {} end
  local st = Pr.char and type(Pr.char.pl) == "table" and Pr.char.pl or {}
  local bank = type(st.bank) == "table" and type(st.bank.items) == "table" and st.bank.items or {}
  local mail = type(st.mail) == "table" and type(st.mail.items) == "table" and st.mail.items or {}
  local ver = P.ver or {}
  local sig = tconcat({ tostring(API.BagGen and API.BagGen() or 0), tostring(ver.bank), tostring(ver.known), tostring(ver.prices),
    tostring(ver.bags), tostring(T.QuestGen and T:QuestGen() or 0), tostring(CR.choiceGen or 0) }, ":")
  for l, r in pairs(ranks) do sig = sig .. ":" .. l .. "=" .. r end
  local t = now()
  if verdictCache.sig == sig and t - verdictCache.at < 30 then return verdictCache.map end
  if verdictCache.sig ~= nil and t - verdictCache.at < 1 then return verdictCache.map end
  local ids = {}
  local iter = API.IterBagsShared or API.IterBags
  if iter then
    local ok, f, s0, v0 = pcall(iter)
    if ok and type(f) == "function" then
      for _, _, it in f, s0, v0 do local id = it and num(it.itemID) if id and uses(id) then ids[id] = true end end
    end
  end
  for id in pairs(bank) do if uses(id) then ids[id] = true end end
  -- besoin des plans (les deux modes), cible de chaque plan
  local need, target = {}, {}
  for _, line in ipairs(planLines()) do
    local best = {}
    for _, mode in ipairs({ "full", "exact" }) do
      local ok, plan = pcall(P.Compute, line, mode, mode == "exact" and 300 or P.Target(line, mode))
      if ok and type(plan) == "table" then
        local tot = {}
        for id, n in pairs(plan.use or {}) do tot[id] = (tot[id] or 0) + n end
        for id, n in pairs(plan.res and plan.res.buy or {}) do if mode == "full" then tot[id] = (tot[id] or 0) + n end end
        for id, n in pairs(tot) do best[id] = max(best[id] or 0, n) end
        if mode == "full" then target[line] = plan.res and plan.res.reached or plan.target end
      end
    end
    for id, n in pairs(best) do need[id] = (need[id] or 0) + n end
  end
  local reserve, why = {}, {}
  if P.Reserve then local ok, r, w = pcall(P.Reserve) if ok then reserve, why = r or {}, w or {} end end
  local map = {}
  for id in pairs(ids) do
    local v = { have = bagCount(id) + (bank[id] or 0) + (mail[id] or 0), need = need[id] or 0, reserve = reserve[id] or 0, quest = why[id] }
    local otherNames = {}
    for _, u in ipairs(uses(id) or {}) do
      local r, rank = u.row, ranks[u.line]
      if rank then
        local learn = tonumber(r[4]) or 1
        if r[6] > rank then
          v.mine = true
          if learn <= rank + 50 then
            local tgt = target[u.line] or rank
            if r[6] > tgt and (not v.soon or learn < v.soon.at) then v.soon = { name = r[9], at = max(learn, rank) } end
            if (not v.usedSoon or learn < v.usedSoon.at) then v.usedSoon = { name = r[9], at = max(learn, rank) } end
          elseif not v.later or learn < v.later.at then v.later = { name = r[9], at = learn } end
        else v.mine = true end
      else
        otherNames[lineName(u.line)] = true
      end
    end
    local on = {}
    for n in pairs(otherNames) do on[#on + 1] = n end
    tsort(on)
    v.other = on[1] and tconcat(on, ", ", 1, min(2, #on)) or nil
    -- « utile d'ici 50 points » sans besoin chiffré par le plan : jamais « en trop »
    if v.need > 0 and not v.soon then v.usedSoon = nil end
    map[id] = CR.VerdictOf(v)
  end
  verdictCache.sig, verdictCache.at, verdictCache.map = sig, t, map
  local II = T.modules and T.modules.ItemInfo
  if II and type(II.Invalidate) == "function" then pcall(II.Invalidate) end
  return map
end
--- verdict d'un objet pour l'infobulle (ItemInfo) ou nil (pas une compo de métier / pas de métier)
function CR.Verdict(id)
  id = tonumber(id)
  if not id or not uses(id) or not CR:IsEnabled() then return nil end
  return CR.Verdicts()[id]
end
--- pastille « trop » d'une case de sac (Highlight) : le verdict s'il dit « en trop », sinon nil
function CR.BagBadge(id)
  -- la pastille ne sert qu'en ville / chez un marchand / pendant les courses / page Métiers ouverte : rien à calculer ailleurs
  local P = PL()
  if not (CR.run or CR.merchantOpen or (P and P.PageShown and P.PageShown()) or CR.InTown()) then return nil end
  local v = CR.Verdict(id)
  if v and (v.extra or 0) > 0 then return v end
  return nil
end
--- « À vendre / à jeter » : { {id, n (en trop), bags, sell (marchand, l'unité), ah (HV si connu), value, why} }
function CR.ExtraList()
  local out = {}
  local P = PL()
  for id, v in pairs(CR.Verdicts()) do
    if v and (v.extra or 0) > 0 then
      local sell = num(select(11, try(API.ItemInfo, id))) or 0
      local d = recipes()
      if sell <= 0 and d and d.mats[id] then sell = tonumber(d.mats[id][4]) or 0 end
      local ah, src = nil, nil
      if P and P.PriceOf then ah, src = P.PriceOf(id) end
      if src == "vendeur" then ah = nil end
      out[#out + 1] = { id = id, n = v.extra, bags = min(v.extra, bagCount(id)), sell = sell, ah = ah,
        value = v.extra * max(sell, ah or 0), why = v.text }
    end
  end
  tsort(out, function(a, b) if a.value ~= b.value then return a.value > b.value end return a.id < b.id end)
  return out
end
--- piles à vendre (sacs) : gris + compos en trop, jamais une pile qui contient du « besoin » (pas de découpe)
function CR.SellStacks()
  local out, left = {}, {}
  for _, e in ipairs(CR.ExtraList()) do left[e.id] = e.bags end
  local iter = API.IterBags
  if not iter then return out end
  local stacks = {}
  for bag, slot, it in iter() do
    local id = num(it.itemID)
    local locked = pub(it.locked)
    if id and not locked then
      local q = num(it.quality)
      local sell = num(select(11, try(API.ItemInfo, id))) or 0
      if q == 0 and not pub(it.noValue) and sell > 0 then
        out[#out + 1] = { bag = bag, slot = slot, id = id, n = num(it.count) or 1, value = sell * (num(it.count) or 1), grey = true }
      elseif left[id] and left[id] > 0 then
        stacks[#stacks + 1] = { bag = bag, slot = slot, id = id, n = num(it.count) or 1, sell = sell }
      end
    end
  end
  tsort(stacks, function(a, b) if a.id ~= b.id then return a.id < b.id end return a.n > b.n end)
  for _, s in ipairs(stacks) do
    if s.n <= (left[s.id] or 0) then
      left[s.id] = left[s.id] - s.n
      out[#out + 1] = { bag = s.bag, slot = s.slot, id = s.id, n = s.n, value = s.sell * s.n }
    end
  end
  return out
end

-- ===================================================================== options de métier (comparées honnêtement)
--- recettes du maître dont l'exigence n'est qu'ESTIMÉE (Forever) et sans doute à ta portée : { [sort] = compétence }
function CR.TrainerCandidates(line)
  local P = PL()
  local pr = P and P.ProfOf and P.ProfOf(line)
  if not pr then return {} end
  local learnAt = P.LearnAt and P.LearnAt(line) or nil
  local known = P.Known and P.Known(line) or nil
  local out = {}
  for _, r in ipairs(P.Rows and P.Rows(line) or {}) do
    local flags = tonumber(r[11]) or 0
    local est = floor(flags / 2) % 2 == 1
    if est and flags % 2 == 0 and (r[8] or 0) == 0 and not (known and known[r[1]]) and not (learnAt and learnAt[r[1]])
        and r[5] > pr.rank and r[5] <= pr.rank + 10 and r[6] > pr.rank then
      out[r[1]] = pr.rank
    end
  end
  return out
end
local optCache = {}
--- les options d'un métier : buy (HV + marchand), trainer (recettes du maître à confirmer), farm (tes compos) →
--- { list = { {id, label, cost, points, perPt, time, farm, confirm, names} }, best, cand }
function CR.ProfOptions(line)
  local P = PL()
  if not (P and P.Compute and Prof()) then return nil end
  local pr = P.ProfOf and P.ProfOf(line)
  if not pr then return nil end
  local ver = P.ver or {}
  local key = tconcat({ line, pr.rank, tostring(ver.bags), tostring(ver.bank), tostring(ver.known), tostring(ver.prices),
    tostring(API.BagGen and API.BagGen() or 0), tostring(P.Mode and P.Mode()) }, ":")
  local c = optCache[line]
  if c and c.key == key and now() - c.t < 20 then return c.v end
  local target = P.Target(line, "full")
  local cand = CR.TrainerCandidates(line)
  local list = {}
  local function metric(id, plan, label)
    if type(plan) ~= "table" then return nil end
    local pts = max(0, (plan.res and plan.res.reached or plan.skill) - plan.skill)
    local o = { id = id, label = label, cost = plan.cost or 0, points = pts, perPt = pts > 0 and (plan.cost or 0) / pts or nil,
      time = plan.time or 0, farm = plan.split and #plan.split.farm or 0, plan = plan }
    list[#list + 1] = o
    return o
  end
  local okA, A = pcall(P.Compute, line, "full", target, false)
  metric("buy", okA and A, "Acheter (marchand + HV)")
  if next(cand) then
    local okB, B = pcall(P.Compute, line, "full", target, cand)
    local o = metric("trainer", okB and B, "Apprendre chez le maître")
    if o then
      o.confirm = true
      local names = {}
      local d = recipes()
      local treq = T.Prof and T.Prof.db and type(T.Prof.db.treq) == "table" and T.Prof.db.treq[line] or nil
      local fold = T.Prof and T.Prof.Fold
      local learnCost, costKnown = 0, true
      for _, r in ipairs(d and d.r[line] or {}) do
        if cand[r[1]] then
          names[#names + 1] = r[9]
          local e = treq and fold and treq[fold(r[9])]
          if type(e) == "table" and tonumber(e.c) then learnCost = learnCost + e.c else costKnown = false end
        end
      end
      tsort(names)
      o.names, o.learnCost, o.costKnown = names, learnCost, costKnown
      o.cost = o.cost + learnCost
      o.perPt = o.points > 0 and o.cost / o.points or nil
    end
  end
  local okC, Cc = pcall(P.Compute, line, "exact", 300, false)
  local oc = metric("farm", okC and Cc, "Avec tes compos (farmer le reste)")
  -- le meilleur : le moins cher par point parmi ce qui monte autant (à 2 points près) ; à égalité, sans HV
  local best, ref = nil, list[1]
  for _, o in ipairs(list) do
    if o.id ~= "farm" and o.points > 0 then
      if not best then best = o
      elseif o.points >= best.points - 2 then
        local a, b = o.perPt or 0, best.perPt or 0
        if a < b * 0.98 or (a <= b * 1.02 and o.farm < best.farm) then best = o end   -- moins cher, ou autant sans farmer
      end
    end
  end
  if oc and oc.points > 0 and ref and oc.points >= ref.points - 2 then best = oc end
  local v = { list = list, best = best and best.id or "buy", cand = cand, line = line }
  optCache[line] = { key = key, t = now(), v = v }
  return v
end
--- (contrat ProfLevel.Compute) recettes à supposer apprenables maintenant : ton choix, sinon l'option la moins chère
function CR.Assume(line)
  local ch = CR.char and type(CR.char.choice) == "table" and CR.char.choice[line] or nil
  if ch == "buy" or ch == "farm" or not CR:IsEnabled() then return false end
  if CR.assuming then return false end
  CR.assuming = true
  local ok, cand = pcall(CR.TrainerCandidates, line)
  if not ok or type(cand) ~= "table" or not next(cand) then CR.assuming = false return false end
  if ch == "trainer" then CR.assuming = false return cand end
  local ok2, o = pcall(CR.ProfOptions, line)
  CR.assuming = false
  if ok2 and type(o) == "table" and o.best == "trainer" then return cand end
  return false
end
--- « Mon choix » (gardé) : "buy" | "trainer" | "farm" | nil (automatique : le moins cher par point)
function CR.SetChoice(line, choice)
  CR.char.choice = type(CR.char.choice) == "table" and CR.char.choice or {}
  CR.char.choice[line] = choice
  CR.choiceGen = (CR.choiceGen or 0) + 1
  optCache[line] = nil
  local Pr = T.Prof
  if Pr and Pr.db then
    if choice == "farm" then Pr.db.plMode = "exact" elseif choice then Pr.db.plMode = "full" end
  end
  local P = PL()
  if P and P.focus and choice then P.focus.mode = (choice == "farm") and "exact" or "full" end
  if P and P.Dirty then P.Dirty("focus") end
  if choice == "trainer" then
    local st = CR.TrainerStep(line)
    CR.Pick("prof", st)
    if P and P.Guide and st.where then P.Guide(st, true) end
  end
end

-- ===================================================================== étapes : choisir, passer, suivante / précédente
local function picks(kind)
  CR.char.pick = type(CR.char.pick) == "table" and CR.char.pick or {}
  local p = CR.char.pick[kind]
  if type(p) ~= "table" then p = { skip = {}, hist = {} } CR.char.pick[kind] = p end
  p.skip = type(p.skip) == "table" and p.skip or {}
  p.hist = type(p.hist) == "table" and p.hist or {}
  return p
end
CR.Picks = picks
--- clé stable d'une étape (survit au recalcul : mêmes recettes, mêmes objets, même palier)
function CR.StepKey(st)
  if type(st) ~= "table" then return "?" end
  if st.key then return st.key end
  local k = st.kind or "?"
  if st.step then return k .. ":" .. tostring(st.step.spell) .. ":" .. tostring(st.step.from) .. (st.step.prep and "p" or "") end
  if k == "trainer" then return "trainer:" .. tostring(st.at or "now") end
  if type(st.items) == "table" then
    local ids = {}
    for _, it in ipairs(st.items) do ids[#ids + 1] = tostring(it.id) end
    tsort(ids)
    return k .. ":" .. tconcat(ids, ",")
  end
  return k .. ":" .. tostring(st.title or "")
end
--- l'étape « Aller au maître de <métier> » (toujours proposée, même sans recette à apprendre)
function CR.TrainerStep(line)
  local P = PL()
  local w = P and P.WhereTrainer and P.WhereTrainer(line) or nil
  if not w then
    local c = CR.Candidates("ptrainer", { line = line })[1]
    if c then w = c end
  end
  local d = w and distText(w.dist)
  return { kind = "trainer", key = "trainer:goto", learn = {}, where = w, optional = true, line = line,
    title = fmt("Aller au maître de %s", lineName(line):lower()),
    sub = w and (cut(shownName(w.name), 28) .. (d and (" · " .. d) or "") .. " · lire ses recettes et leurs vrais niveaux")
      or "pas encore vu : demande à un garde · TreeAI retiendra sa place",
    guideLabel = "Maître de " .. lineName(line):lower(), icon = ICON .. KIND_ICON.ptrainer }
end
--- (contrat ProfLevel.Steps) l'étape choisie d'abord, les passées à la fin ; « aller au maître » toujours là
function CR.ApplyPick(kind, steps, line)
  if type(steps) ~= "table" or not CR:IsEnabled() then return steps end
  local p = picks(kind)
  if kind == "prof" and line then
    -- recettes du maître supposées apprenables (option choisie) : une étape « apprends … (à confirmer) » avant la
    -- première fabrication qui s'en sert
    local okA, cand = pcall(CR.Assume, line)
    if okA and type(cand) == "table" then
      local at, names, learn = nil, {}, {}
      for i, st in ipairs(steps) do
        if st.kind == "craft" and st.step and cand[st.step.spell] then
          if not at then at = i end
          if not learn[st.step.spell] then learn[st.step.spell] = true names[#names + 1] = st.step.name end
        end
      end
      if at then
        local P = PL()
        local w = P and P.WhereTrainer and P.WhereTrainer(line) or nil
        local dt = w and distText(w.dist)
        local ls = {}
        for sp in pairs(learn) do ls[#ls + 1] = { spell = sp, kind = "trainer", confirm = true } end
        tinsert(steps, at, { kind = "trainer", key = "trainer:confirm", learn = ls, where = w, line = line, confirm = true,
          title = "Maître : apprends « " .. cut(tconcat(names, " », « "), 50) .. " » (à confirmer chez le maître)",
          sub = (w and (cut(shownName(w.name), 28) .. (dt and (" · " .. dt) or "")) or "pas encore vu : demande à un garde")
            .. " · moins cher par point que d'acheter",
          guideLabel = "Maître de " .. lineName(line):lower(), icon = ICON .. KIND_ICON.ptrainer })
      end
    end
    local has = false
    for _, st in ipairs(steps) do if st.kind == "trainer" and (not st.at or st.at <= (st.skill or 1e9)) then has = true end end
    for _, st in ipairs(steps) do if st.kind == "trainer" then has = true end end
    if not has then steps[#steps + 1] = CR.TrainerStep(line) end
  end
  local front, mid, back = nil, {}, {}
  for _, st in ipairs(steps) do
    st.key = CR.StepKey(st)
    if p.skip[st.key] then
      st.skipped = true
      if st.sub and not st.sub:find("^passée") then st.sub = "passée · " .. st.sub elseif not st.sub then st.sub = "passée" end
      back[#back + 1] = st
    elseif p.key and st.key == p.key and not front then front = st
    else mid[#mid + 1] = st end
  end
  if p.key and not front then p.key = nil end   -- étape faite (disparue) : le plan reprend son ordre
  local out = {}
  if front then out[1] = front end
  for _, st in ipairs(mid) do out[#out + 1] = st end
  for _, st in ipairs(back) do out[#out + 1] = st end
  return out
end
local function prevKey(p)
  if p.key then
    local h = p.hist
    if h[#h] ~= p.key then h[#h + 1] = p.key end
    while #h > 12 do tremove(h, 1) end
  end
end
--- clic sur une étape : c'est elle maintenant (la boussole y va), même après un recalcul
function CR.Pick(kind, st)
  if type(st) ~= "table" then return false end
  local p = picks(kind)
  local key = CR.StepKey(st)
  if key ~= p.key then prevKey(p) end
  p.key = key
  p.skip[key] = nil
  CR.Changed(kind)
  return true
end
--- clic droit : passer l'étape (à la fin, « passée ») ; reclic droit : la reprendre
function CR.Skip(kind, st)
  if type(st) ~= "table" then return false end
  local p = picks(kind)
  local key = CR.StepKey(st)
  if p.skip[key] then p.skip[key] = nil say("Étape reprise.")
  else
    p.skip[key] = true
    if p.key == key then p.key = nil end
    say("Étape passée : " .. cut(st.title or "", 40))
  end
  CR.Changed(kind)
  return true
end
function CR.Changed(kind)
  if kind == "prof" then
    local P = PL()
    if P then
      P.stepSig, P.lastGuide = nil, nil
      if P.Dirty then P.Dirty("focus") end
    end
  else
    if CR.run then CR.run.lastGuide = nil end
    CR.Refresh(true)
  end
  local Wn = T.Window
  if Wn and Wn.Dirty then pcall(Wn.Dirty) end
  if Wn and Wn.RenderCardSoon then pcall(Wn.RenderCardSoon) end
end
local function profSteps()
  local P = PL()
  if not P then return {} end
  if P.steps and P.focus then return P.steps end
  local line = (P.focus and P.focus.line) or (P.Line and P.Line())
  if not line or not P.Compute then return {} end
  local mode = (P.focus and P.focus.mode) or P.Mode()
  local ok, plan = pcall(P.Compute, line, mode, mode == "exact" and 300 or P.Target(line, mode))
  if not ok or not plan then return {} end
  return P.Steps(plan) or {}
end
--- « Étape suivante / précédente » (boutons, touches) : du focus en cours (courses en ville, sinon session métier)
function CR.StepMove(delta)
  delta = tonumber(delta) or 1
  if CR.run then
    local list = CR.Remaining()
    local cur = CR.CurrentStop()
    local idx = 1
    for i, s in ipairs(list) do if cur and s.key == cur.key then idx = i end end
    local p = picks("city")
    if delta < 0 then
      local h = p.hist
      while #h > 0 do
        local k = tremove(h)
        for _, s in ipairs(CR.run.stops) do if s.key == k and k ~= p.key then p.key = k CR.Changed("city") return true end end
      end
      local s = list[idx - 1]
      if s then p.key = s.key CR.Changed("city") return true end
      return false
    end
    local s = list[idx + 1]
    if not s then say("C'est le dernier arrêt.") return false end
    if p.key then prevKey(p) elseif cur then p.hist[#p.hist + 1] = cur.key end
    p.key = s.key
    CR.Changed("city")
    return true
  end
  local steps = profSteps()
  if #steps == 0 then say("Pas d'étape à choisir.") return false end
  local p = picks("prof")
  if delta < 0 then
    local h = p.hist
    while #h > 0 do
      local k = tremove(h)
      for _, st in ipairs(steps) do if st.key == k and k ~= p.key then p.key = k CR.Changed("prof") return true end end
    end
    say("Pas d'étape précédente.")
    return false
  end
  local nxt
  for i = 2, #steps do if not steps[i].skipped then nxt = steps[i] break end end
  if not nxt then say("C'est la dernière étape.") return false end
  if p.key then prevKey(p) else p.hist[#p.hist + 1] = CR.StepKey(steps[1]) end
  p.key = CR.StepKey(nxt)
  CR.Changed("prof")
  return true
end
--- boutons « Étape précédente / suivante » (page Métiers, page Courses)
function CR.StepButtons()
  return { { "◀ Étape précédente", function() CR.StepMove(-1) end, "Clic : revenir à l'étape choisie avant (touche : Raccourcis → TreeAI)" },
    { "Étape suivante ▶", function() CR.StepMove(1) end, "Clic : l'étape d'après devient l'étape en cours (la boussole y va)" } }
end
T.FocusStep = function(d) return CR.StepMove(d) end   -- appelé par Bindings.xml
_G.BINDING_NAME_TREEAI_STEP_NEXT = "TreeAI : étape suivante (courses / session métier)"
_G.BINDING_NAME_TREEAI_STEP_PREV = "TreeAI : étape précédente (courses / session métier)"

-- ===================================================================== la liste des courses
local function lotOf(cands, id)
  for _, c in ipairs(cands or {}) do
    local v = c.covers and c.covers[id]
    if type(v) == "table" then return tonumber(v[2]) or 1, tonumber(v[1]) end
  end
  return nil, nil
end
local function roundLot(n, lot) lot = max(1, lot or 1) return ceil(n / lot) * lot end
CR.RoundLot = roundLot
-- index (refait seulement quand la mémoire change) : prix le moins cher connu par objet ; objets vendus sur chaque carte
local idx = { gen = nil, price = {}, sold = {} }
local function memIndex()
  if idx.gen == (CR.memGen or 0) and idx.built then return idx end
  idx.gen, idx.built, idx.price, idx.sold = CR.memGen or 0, true, {}, {}
  for mk, list in pairs(mem()) do
    local sold = {}
    idx.sold[mk] = sold
    for _, e in pairs(type(list) == "table" and list or {}) do
      if type(e) == "table" then
        for id, v in pairs(CR.ItemsOf(e)) do
          sold[id] = true
          if type(v) == "table" and tonumber(v[1]) then
            local lot = tonumber(v[2]) or 1
            local cur = idx.price[id]
            if not cur or v[1] / max(1, lot) < cur[1] / max(1, cur[2]) then idx.price[id] = { tonumber(v[1]), lot } end
          end
        end
      end
    end
  end
  return idx
end
--- prix connu d'un lot chez un marchand vu (le moins cher) → prix d'un lot, lot
local function knownPrice(id)
  local v = memIndex().price[id]
  if v then return v[1], v[2] end
  return nil, nil
end
CR.KnownPrice = knownPrice
local function soldBy(id)
  local p = here()
  local sold = p and memIndex().sold[tostring(p.mapID)]
  return sold and sold[id] and true or false
end
-- sacs relus UNE fois par calcul de la liste (pas un appel au jeu par objet des tables)
local snap
local function cnt(id) if snap then return snap[id] or 0 end return bagCount(id) end
--- un achat : { id, n (à acheter, en lots), base (dans tes sacs maintenant), cost (cuivre ou nil), lot, exact }
local function buyItem(id, want, have)
  local n = want - have
  if n <= 0 then return nil end
  local price, lot = knownPrice(id)
  local q = roundLot(n, lot or 1)
  return { id = id, n = q, base = cnt(id), want = want, lot = lot or 1, exact = lot ~= nil,
    cost = price and floor(price * q / max(1, lot or 1)) or nil }
end
local function sumCost(items)
  local c, unknown = 0, false
  for _, it in ipairs(items) do if it.cost then c = c + it.cost else unknown = true end end
  return c, unknown
end
local function itemsText(items, maxN)
  local parts = {}
  for k, it in ipairs(items or {}) do
    if k > (maxN or 3) then parts[#parts + 1] = "…" break end
    parts[#parts + 1] = fmt("%s%d %s", it.exact == false and "≈ " or "", it.n, itemName(it.id))
  end
  return tconcat(parts, ", ")
end
CR.ItemsText = itemsText

local function hunterErrands(out)
  local H = T.Hunter
  if myClass() ~= "HUNTER" or type(H) ~= "table" then return end
  local lvl = level()
  if type(H.AmmoInfo) == "function" then
    local ok, a = pcall(H.AmmoInfo, H)
    if ok and type(a) == "table" and a.kind then
      local rate = H.AmmoRate and num((try(H.AmmoRate)))
      local want = rate and max(1000, ceil(rate * 2 / 200) * 200) or 1000
      local have = tonumber(a.total) or 0
      if want - have >= 100 then
        local req = {}
        for _, r in ipairs(T.Data and T.Data.Consumables and T.Data.Consumables.ammo or {}) do req[r[1]] = r[2] end
        local pick, pickKnown
        for _, id in ipairs(VENDOR_AMMO[a.kind] or {}) do
          if (req[id] or 1) <= lvl then
            local known = soldBy(id)
            if known and (not pickKnown or (req[id] or 1) >= (req[pickKnown] or 1)) then pickKnown = id end
            if not pick or (req[id] or 1) >= (req[pick] or 1) then pick = id end
          end
        end
        pick = pickKnown or pick
        local it = pick and buyItem(pick, want, have)
        if it then
          local word = a.kind == "arrow" and "flèches" or "balles"
          out[#out + 1] = { id = "ammo", kind = "vendor", cat = "ammo", prio = PRIO.ammo, items = { it },
            label = "Munitions : " .. itemsText({ it }), why = fmt("tu as %d %s (ton arme tire des %s) ; %d pour 2 h de jeu", have, word, word, want),
            any = word }
        end
      end
    end
  end
  -- familier : nourriture de SON régime, qu'il mange pleinement (niveau d'objet ≥ niveau du familier − 10)
  if try(g("UnitExists"), "pet") and type(H.GetDiet) == "function" then
    local ok, diet = pcall(H.GetDiet, H)
    if ok and type(diet) == "table" and next(diet) then
      local plvl = num((try(g("UnitLevel"), "pet"))) or lvl
      local have, list = 0, {}
      for _, r in ipairs(T.Data and T.Data.PetFood or {}) do
        if diet[r[2]] then
          if r[3] >= plvl - 30 then have = have + cnt(r[1]) end
          list[#list + 1] = r
        end
      end
      if have < 10 then
        local pick
        for _, r in ipairs(list) do
          if r[3] >= plvl - 10 and soldBy(r[1]) and (not pick or r[3] < pick[3]) then pick = r end
        end
        if not pick then for _, r in ipairs(list) do if r[3] >= plvl - 30 and soldBy(r[1]) and (not pick or r[3] > pick[3]) then pick = r end end end
        local names = {}
        local DFR = { meat = "viande", fish = "poisson", bread = "pain", cheese = "fromage", fruit = "fruit", fungus = "champignon" }
        for k in pairs(diet) do names[#names + 1] = DFR[k] or k end
        tsort(names)
        local it = pick and buyItem(pick[1], 20, cnt(pick[1]) + max(0, have - cnt(pick[1])))
        out[#out + 1] = { id = "petfood", kind = "vendor", cat = "petfood", prio = PRIO.petfood, items = it and { it } or {},
          label = it and ("Familier : " .. itemsText({ it })) or ("Nourriture du familier (" .. tconcat(names, ", ") .. ")"),
          why = fmt("il lui en reste %d ; il mange : %s", have, tconcat(names, ", ")), any = "nourriture pour familier (" .. tconcat(names, ", ") .. ")",
          needItems = not it }
      end
    end
  end
end
local function consumableErrands(out)
  local C = T.Data and T.Data.Consumables
  if not C then return end
  local lvl = level()
  local cls = myClass()
  local _, ptype = try(g("UnitPowerType"), "player")
  ptype = num((try(g("UnitPowerType"), "player")))
  local wantDrink = ptype == 0 and cls ~= "HUNTER"
  for _, k in ipairs({ "food", wantDrink and "water" or nil }) do
    local have, best = 0, nil
    for _, r in ipairs(C[k] or {}) do
      have = have + cnt(r[1])
      if (r[2] or 1) <= lvl and soldBy(r[1]) and (not best or (r[3] or 0) > (best[3] or 0)) then best = r end
    end
    if have < 5 then
      local it = best and buyItem(best[1], 20, have)
      out[#out + 1] = { id = k, kind = "vendor", cat = k, prio = PRIO[k], items = it and { it } or {},
        label = it and ((k == "food" and "Nourriture : " or "Boisson : ") .. itemsText({ it })) or (k == "food" and "Nourriture" or "Boisson"),
        why = fmt("il t'en reste %d", have), any = k == "food" and "nourriture" or "boisson", needItems = not it }
    end
  end
  for _, r in ipairs(REAGENTS[cls or ""] or {}) do
    if lvl >= r[2] then
      local have = cnt(r[1])
      local it = buyItem(r[1], r[3], have)
      if it and have < ceil(r[3] / 2) then
        out[#out + 1] = { id = "reagent:" .. r[1], kind = "vendor", cat = "reagent", prio = PRIO.reagent, items = { it },
          label = "Composant : " .. itemsText({ it }), why = fmt("il t'en reste %d", have), any = "composants" }
      end
    end
  end
end
local function profErrands(out, opts)
  local P, Pr, d = PL(), Prof(), recipes()
  if not (P and Pr and d and P.Compute) then return end
  local only
  if opts.lines then only = {} for _, l in ipairs(opts.lines) do only[l] = true end end
  local bank, mail, ahItems = {}, {}, {}
  local fold = Pr.Fold or function(s) return s end
  for _, line in ipairs(planLines()) do
    if not only or only[line] then
      local mode = (opts.chain and opts.chain.line == line and opts.chain.mode) or P.Mode()
      local ok, plan = pcall(P.Compute, line, mode, mode == "exact" and 300 or P.Target(line, mode))
      if ok and type(plan) == "table" then
        local sp = plan.split
        local name = plan.name or lineName(line)
        if #sp.vendor > 0 then
          local items = {}
          for _, v in ipairs(sp.vendor) do
            local price, lot = knownPrice(v.id)
            items[#items + 1] = { id = v.id, n = v.buy, base = bagCount(v.id), lot = lot or (d.mats[v.id] and d.mats[v.id][5]) or 1,
              exact = true, cost = v.cost or (price and floor(price * v.buy / max(1, lot or 1))) }
          end
          out[#out + 1] = { id = "mats:" .. line, kind = "vendor", cat = "mats", prio = PRIO.mats, items = items, line = line,
            label = "Fournitures de " .. name:lower() .. " : " .. itemsText(items, 2),
            why = fmt("plan %s %d → %d (%s)", name, plan.skill, plan.res.reached, mode == "exact" and "pile-poil" or "complet"),
            any = "fournitures de " .. name:lower() }
        end
        for id, n in pairs(sp.bank) do bank[id] = (bank[id] or 0) + n end
        for id, n in pairs(sp.mail) do mail[id] = (mail[id] or 0) + n end
        if #sp.ah > 0 then
          local acc = P.AHAccepted and P.AHAccepted(line)
          if acc == nil and Pr.db and Pr.db.askAH == false then acc = true end
          local items, cost, worst = {}, 0, 0
          for _, a in ipairs(sp.ah) do
            local cap = P.MaxUnit and P.MaxUnit(a.unit) or ceil(a.unit * 1.2)
            items[#items + 1] = { id = a.id, n = a.n, base = bagCount(a.id), unit = a.unit, cap = cap, exact = true, cost = a.cost }
            cost, worst = cost + (a.cost or 0), worst + cap * a.n
          end
          ahItems[#ahItems + 1] = line
          out[#out + 1] = { id = "ah:" .. line, kind = "ah", cat = "ah", prio = PRIO.ah, items = items, line = line, mode = mode,
            cost = floor(cost), worst = worst, optional = acc ~= true, accept = acc == nil,
            label = "HV : " .. itemsText(items, 2) .. " ≈ " .. money(cost) .. (#items == 1 and (" (≤ " .. money(items[1].cap) .. "/u)") or ""),
            why = acc == true and fmt("plan %s : prix plafond = marché + 20 %%", name)
              or "achat à l'HV pas encore accepté : clic pour l'accepter" }
        end
        -- maître du métier : recettes / rang à prendre maintenant
        local learn, train = {}, nil
        for _, l in ipairs(plan.res.learn or {}) do if l.at <= plan.skill then learn[#learn + 1] = l end end
        for _, x in ipairs(plan.prep or {}) do if x.learn then learn[#learn + 1] = { spell = x.spell, name = x.name } end end
        local okA, cand = pcall(CR.Assume, line)
        if okA and type(cand) == "table" then
          local seenL = {}
          for _, l in ipairs(learn) do seenL[l.spell] = true end
          for _, x in ipairs(plan.res.steps or {}) do
            if cand[x.spell] and not seenL[x.spell] then seenL[x.spell] = true learn[#learn + 1] = { spell = x.spell, name = x.name, confirm = true } end
          end
        end
        local tr = plan.res.train and plan.res.train[1]
        if tr and tr.at <= plan.skill then train = tr end
        local treq = Pr.db and type(Pr.db.treq) == "table" and Pr.db.treq[line] or nil
        if #learn > 0 or train then
          local names, cost, unknown = {}, 0, false
          if train then names[#names + 1] = "rang " .. tostring(train.rank) unknown = true end
          for _, l in ipairs(learn) do
            names[#names + 1] = cut(l.name or "?", 26)
            local e = treq and treq[fold(l.name or "")]
            if type(e) == "table" and tonumber(e.c) then cost = cost + e.c else unknown = true end
          end
          out[#out + 1] = { id = "ptrainer:" .. line, kind = "ptrainer", cat = "ptrainer", prio = PRIO.ptrainer, line = line,
            cost = cost, unknownCost = unknown, label = fmt("Maître de %s : %s", name:lower(), tconcat(names, ", ", 1, min(3, #names))),
            why = unknown and "prix exact chez le maître" or ("prix lu chez lui : " .. money(cost)) }
        else
          out[#out + 1] = { id = "ptrainer:" .. line, kind = "ptrainer", cat = "ptrainer", prio = PRIO.ptrainer, line = line,
            optional = true, cost = 0, label = fmt("Aller au maître de %s", name:lower()),
            why = "lire ses recettes et leurs vrais niveaux (plan plus juste)" }
        end
      end
    end
  end
  local function listOf(map)
    local l = {}
    for id, n in pairs(map) do l[#l + 1] = { id = id, n = n, base = bagCount(id), exact = true } end
    tsort(l, function(a, b) return a.id < b.id end)
    return l
  end
  if next(bank) then
    local items = listOf(bank)
    out[#out + 1] = { id = "bank", kind = "bank", cat = "bank", prio = PRIO.bank, items = items, cost = 0,
      label = "Banque : retire " .. itemsText(items, 3), why = "compos de tes plans de métier, rangées en banque" }
  end
  if next(mail) then
    local items = listOf(mail)
    out[#out + 1] = { id = "mail", kind = "mail", cat = "mail", prio = PRIO.mail, items = items, cost = 0,
      label = "Courrier : prends " .. itemsText(items, 3), why = "compos de tes plans de métier, dans ton courrier" }
  end
end
local function trainerErrands(out)
  local E = T.modules and T.modules.Errands
  local ch = type(E) == "table" and E.char or nil
  local p = type(ch) == "table" and tonumber(ch.trainPending) or nil
  local lvl = level()
  if p then
    local nt = ch.nextTrain
    local known = type(nt) == "table" and tonumber(nt.level) and nt.level <= lvl and tonumber(nt.cost) and nt.cost > 0
    if known or p % 2 == 0 then
      out[#out + 1] = { id = "ctrainer", kind = "ctrainer", cat = "ctrainer", prio = PRIO.ctrainer,
        cost = known and nt.cost or 0, unknownCost = not known,
        label = fmt("Maître de classe : nouveaux rangs (niveau %d)", p),
        why = known and ("prix lu chez lui : " .. money(nt.cost)) or "prix exact dans sa fenêtre" }
      return
    end
  end
  out[#out + 1] = { id = "ctrainer", kind = "ctrainer", cat = "ctrainer", prio = PRIO.ctrainer, optional = true, cost = 0,
    label = "Aller au maître de classe", why = "rien de neuf connu : seulement si tu veux vérifier" }
end
local function serviceErrands(out)
  local Inv = mod("Inventory")
  if Inv and type(Inv.Durability) == "function" then
    local ok, pct, broken = pcall(Inv.Durability)
    if ok and pct and (pct < 70 or (broken or 0) > 0) then
      out[#out + 1] = { id = "repair", kind = "repair", cat = "repair", prio = PRIO.repair, cost = 0, unknownCost = true,
        label = fmt("Réparer (équipement à %d %%)", pct), why = "prix exact chez un marchand qui répare", minPct = pct }
    end
  end
  local stacks = CR.SellStacks()
  if #stacks > 0 then
    local gain, grey, extra = 0, 0, 0
    for _, s in ipairs(stacks) do gain = gain + (s.value or 0) if s.grey then grey = grey + 1 else extra = extra + 1 end end
    local parts = {}
    if grey > 0 then parts[#parts + 1] = fmt("%d gris", grey) end
    if extra > 0 then parts[#parts + 1] = fmt("%d pile%s de compos en trop", extra, extra > 1 and "s" or "") end
    out[#out + 1] = { id = "sell", kind = "sell", cat = "sell", prio = PRIO.sell, gain = gain, cost = 0,
      label = "Vendre : " .. tconcat(parts, " + ") .. (gain > 0 and (" (" .. money(gain) .. ")") or ""),
      why = "chez n'importe quel marchand ; jamais rien de détruit, tout se rachète chez lui" }
  end
  if pub(try(g("HasNewMail"))) then
    local found = false
    for _, e in ipairs(out) do if e.kind == "mail" then found = true end end
    if not found then
      out[#out + 1] = { id = "mail", kind = "mail", cat = "mail", prio = PRIO.mail, cost = 0, label = "Courrier qui t'attend",
        why = "le jeu signale du courrier non lu" }
    end
  end
end
--- la prochaine destination après la ville (trajet de quêtes / boussole) : {mapID, x, y, label} ou nil
function CR.NextStop()
  if type(T.Next) == "function" then
    local n = try(T.Next, T)
    if type(n) ~= "table" or not n.mapID then n = try(T.Next) end
    if type(n) == "table" and n.mapID and n.x then return { mapID = n.map or n.mapID, x = n.x, y = n.y, label = n.title or n.line } end
  end
  local C = T.Compass
  local cur = C and C.Current and try(C.Current)
  if type(cur) == "table" and cur.plan and cur.mapID and cur.x then
    local x, y = cur.x, cur.y
    if x > 1 or y > 1 then x, y = x / 100, y / 100 end
    return { mapID = cur.mapID, x = x, y = y, label = cur.name or cur.label }
  end
  return nil
end
--- la sortie : maître de vol (loin), quai du zeppelin (autre continent), sinon le prochain arrêt du trajet à pied
function CR.Exit(nextStop)
  local p = here()
  if not p then return nil end
  local fly = CR.Candidates("flight")[1]
  if nextStop then
    local d = dist(p, nextStop)
    if not d then
      local dock = CR.Candidates("dock")[1]
      if dock then return { kind = "dock", name = dock.name, mapID = dock.mapID, x = dock.x, y = dock.y, why = "zeppelin vers ton prochain arrêt" } end
      if fly then return { kind = "flight", name = fly.name, mapID = fly.mapID, x = fly.x, y = fly.y, why = "ton prochain arrêt est loin" } end
      return nil
    end
    if d > 1500 and fly then return { kind = "flight", name = fly.name, mapID = fly.mapID, x = fly.x, y = fly.y, why = "ton prochain arrêt est loin : vol" } end
    return { kind = "next", name = nextStop.label or "prochain arrêt", mapID = nextStop.mapID, x = nextStop.x, y = nextStop.y, why = "ton prochain arrêt du trajet" }
  end
  if fly then return { kind = "flight", name = fly.name, mapID = fly.mapID, x = fly.x, y = fly.y, why = "pour repartir" } end
  return nil
end
--- toutes les courses (avant l'or et les PNJ) ; opts = { lines, chain, picked = {[id] = true} }
function CR.Errands(opts)
  opts = opts or {}
  local out = {}
  snap = {}
  local iter = API.IterBagsShared or API.IterBags
  if iter then
    local ok, f, s0, v0 = pcall(iter)
    if ok and type(f) == "function" then
      for _, _, it in f, s0, v0 do
        local id = it and num(it.itemID)
        if id then snap[id] = (snap[id] or 0) + (num(it.count) or 1) end
      end
    else snap = nil end
  else snap = nil end
  pcall(profErrands, out, opts)
  if not opts.profOnly then
    pcall(hunterErrands, out)
    pcall(consumableErrands, out)
    pcall(trainerErrands, out)
  end
  pcall(serviceErrands, out)
  for _, e in ipairs(out) do
    if e.items and not e.cost then local c, unk = sumCost(e.items) e.cost, e.unknownCost = c, e.unknownCost or unk end
    if e.optional and opts.picked and opts.picked[e.id] then e.optional, e.picked = false, true end
  end
  snap = nil
  return out
end
--- l'or : si tout ne passe pas, la course la moins urgente sort (pure) → gardées, retirées, manque
function CR.Budget(errands, have)
  local gain, cost = 0, 0
  for _, e in ipairs(errands) do
    if not e.optional then gain = gain + (e.gain or 0) cost = cost + (e.worst or e.cost or 0) end
  end
  have = tonumber(have) or 0
  local missing = cost - have - gain
  if missing <= 0 then return errands, {}, 0 end
  local order = {}
  for i, e in ipairs(errands) do if not e.optional and (e.worst or e.cost or 0) > 0 then order[#order + 1] = { i = i, e = e } end end
  tsort(order, function(a, b) if (a.e.prio or 0) ~= (b.e.prio or 0) then return (a.e.prio or 0) < (b.e.prio or 0) end return a.i > b.i end)
  local drop, left = {}, missing
  for _, o in ipairs(order) do
    if left <= 0 then break end
    drop[o.e] = true
    left = left - (o.e.worst or o.e.cost or 0)
  end
  local keep, dropped = {}, {}
  for _, e in ipairs(errands) do if drop[e] then dropped[#dropped + 1] = e else keep[#keep + 1] = e end end
  return keep, dropped, missing
end

-- ===================================================================== PNJ de chaque course, arrêts, trajet
--- PNJ pour chaque course (marchands : le moins d'arrêts qui couvrent tout, puis le plus proche) → arrêts, inconnus
function CR.Assign(errands)
  local p = here()
  local byNpc, order, unknown = {}, {}, {}
  local function put(c, kind, e, items)
    local key = tostring(c.mapID) .. ":" .. tostring(c.name)
    local s = byNpc[key]
    if not s then
      s = { key = key, name = c.name, mapID = c.mapID, x = c.x, y = c.y, src = c.src, kinds = {}, errands = {}, items = {}, npcs = { c.name } }
      byNpc[key] = s
      order[#order + 1] = s
    end
    s.kinds[kind] = true
    local found = false
    for _, x in ipairs(s.errands) do if x == e then found = true end end
    if not found then s.errands[#s.errands + 1] = e end
    for _, it in ipairs(items or {}) do s.items[#s.items + 1] = { errand = e, item = it } end
    return s
  end
  -- marchands : couverture gloutonne des objets
  local need, vendorErrands = {}, {}
  for _, e in ipairs(errands) do
    if e.kind == "vendor" and not e.optional then
      vendorErrands[#vendorErrands + 1] = e
      for _, it in ipairs(e.items or {}) do need[#need + 1] = { e = e, it = it } end
      if e.needItems then unknown[#unknown + 1] = { errand = e, what = e.any or KIND_FR.vendor } end
    end
  end
  local ids = {}
  for _, u in ipairs(need) do ids[#ids + 1] = u.it.id end
  local cands = #ids > 0 and CR.Candidates("vendor", { ids = ids }) or {}
  local left = need
  while #left > 0 do
    local best, bestN
    for _, c in ipairs(cands) do
      local n = 0
      for _, u in ipairs(left) do if c.covers[u.it.id] then n = n + 1 end end
      if n > 0 and (not bestN or n > bestN or (n == bestN and (c.dist or 1e12) < (best.dist or 1e12))) then best, bestN = c, n end
    end
    if not best then break end
    local rest = {}
    for _, u in ipairs(left) do
      if best.covers[u.it.id] then
        local v = best.covers[u.it.id]
        if type(v) == "table" and tonumber(v[2]) then   -- lot exact de CE marchand
          u.it.lot = tonumber(v[2])
          u.it.n = roundLot(u.it.n, u.it.lot)
          u.it.exact = true
          if tonumber(v[1]) then u.it.cost = floor(v[1] * u.it.n / u.it.lot) end
        end
        put(best, "vendor", u.e, { u.it })
      else rest[#rest + 1] = u end
    end
    left = rest
  end
  local missingBy = {}
  for _, u in ipairs(left) do
    local k = u.e.id
    if not missingBy[k] then missingBy[k] = true unknown[#unknown + 1] = { errand = u.e, what = u.e.any or KIND_FR.vendor } end
  end
  for _, e in ipairs(vendorErrands) do if e.items then local c, unk = sumCost(e.items) e.cost, e.unknownCost = c, unk end end
  -- autres services : le plus proche (réparation et vente : un marchand déjà sur le trajet d'abord)
  for _, e in ipairs(errands) do
    if not e.optional and e.kind ~= "vendor" then
      local c
      if e.kind == "repair" then
        for _, s in ipairs(order) do
          if s.kinds.vendor then for _, rc in ipairs(CR.Candidates("repair")) do if rc.name == s.name then c = rc end end end
          if c then break end
        end
        c = c or CR.Candidates("repair")[1]
      elseif e.kind == "sell" then
        for _, s in ipairs(order) do if s.kinds.vendor then c = s break end end
        c = c or CR.Candidates("vendor")[1] or CR.Candidates("repair")[1]
      elseif e.kind == "ptrainer" then c = CR.Candidates("ptrainer", { line = e.line })[1]
      else c = CR.Candidates(e.kind)[1] end
      if c then put(c, e.kind, e) else unknown[#unknown + 1] = { errand = e, what = KIND_FR[e.kind] or e.kind } end
    end
  end
  -- même bâtiment (≤ 20 m) : un seul arrêt
  local merged = {}
  for _, s in ipairs(order) do
    local into
    for _, m in ipairs(merged) do
      local d = dist(m, s)
      if d and d <= NEAR_YD then into = m break end
    end
    if into then
      for k in pairs(s.kinds) do into.kinds[k] = true end
      for _, e in ipairs(s.errands) do into.errands[#into.errands + 1] = e end
      for _, it in ipairs(s.items) do into.items[#into.items + 1] = it end
      into.npcs[#into.npcs + 1] = s.name
      into.key = into.key .. "+" .. s.key
    else merged[#merged + 1] = s end
  end
  for _, s in ipairs(merged) do s.dist = p and dist(p, s) or nil end
  return merged, unknown
end
local function stopTitle(s)
  local parts = {}
  for _, e in ipairs(s.errands) do
    if e.kind == "vendor" then
      local items = {}
      for _, x in ipairs(s.items) do if x.errand == e then items[#items + 1] = x.item end end
      parts[#parts + 1] = (#items > 0 and ("Achète " .. itemsText(items, 2))) or e.label
    else parts[#parts + 1] = e.label end
  end
  local t = parts[1] or KIND_FR.vendor
  if #parts > 1 then t = t .. fmt(" + %d", #parts - 1) end
  return t
end
local function stopSub(s, from)
  local d = from and dist(from, s) or s.dist
  local names = {}
  for _, n in ipairs(s.npcs) do names[#names + 1] = cut(shownName(n), 22) end
  local cost, unk = 0, false
  for _, e in ipairs(s.errands) do
    if e.kind == "vendor" then
      for _, x in ipairs(s.items) do if x.errand == e then if x.item.cost then cost = cost + x.item.cost else unk = true end end end
    else cost = cost + (e.worst or e.cost or 0) unk = unk or e.unknownCost end
  end
  local dt = distText(d)
  return tconcat(names, " + ") .. (dt and (" · " .. dt) or "") .. (cost > 0 and (" · " .. money(cost)) or "") .. (unk and " · prix sur place" or "")
end
--- le plan complet : courses, or, PNJ, arrêts dans l'ordre, sortie, temps total
function CR.Plan(opts)
  opts = opts or {}
  local p = here()
  local all = CR.Errands(opts)
  local active, optional = {}, {}
  for _, e in ipairs(all) do if e.optional then optional[#optional + 1] = e else active[#active + 1] = e end end
  local keep, dropped, missing = CR.Budget(active, playerMoney())
  local stops, unknown = CR.Assign(keep)
  local exit = CR.Exit(opts.nextStop or CR.NextStop())
  local nodes = { [0] = p }
  for i, s in ipairs(stops) do nodes[i] = s end
  local n = #stops
  if exit then nodes[n + 1] = exit end
  local costM = {}
  local function cost(i, j)
    local k = i * 100 + j
    if costM[k] == nil then costM[k] = CR.LegSec(nodes[i], nodes[j]) or false end
    return costM[k] or nil
  end
  local order, total = CR.Order(n, cost, exit ~= nil)
  local ordered = {}
  for _, i in ipairs(order) do ordered[#ordered + 1] = stops[i] end
  local prev = p
  local cost0, gain = 0, 0
  for _, s in ipairs(ordered) do
    s.title, s.sub = stopTitle(s), stopSub(s, p)
    s.leg = prev and CR.LegSec(prev, s) or nil
    prev = s
    local ic
    for _, k in ipairs({ "vendor", "ctrainer", "ptrainer", "bank", "ah", "mail", "repair", "sell", "inn" }) do if s.kinds[k] and not ic then ic = k end end
    s.icon = ICON .. (KIND_ICON[ic or "vendor"] or KIND_ICON.vendor)
  end
  for _, e in ipairs(keep) do cost0 = cost0 + (e.worst or e.cost or 0) gain = gain + (e.gain or 0) end
  local sec = (total or 0) + SERVICE_SEC * n
  return { errands = keep, dropped = dropped, missing = missing, optional = optional, stops = ordered, unknown = unknown,
    exit = exit, sec = sec, walk = total or 0, cost = cost0, gain = gain, map = p and p.mapID, money = playerMoney() }
end
--- « Il te manque 3 pa : je laisse X (moins urgent) » ou nil
function CR.MoneyText(plan)
  if not plan or #plan.dropped == 0 then return nil end
  local names = {}
  for i, e in ipairs(plan.dropped) do if i <= 2 then names[#names + 1] = "« " .. cut(e.label, 34) .. " »" end end
  return fmt("Il te manque %s : je laisse %s (moins urgent)", money(plan.missing), tconcat(names, ", "))
end

-- ===================================================================== le mode focus « Courses en ville »
local function publish()
  local run = CR.run
  if run then
    T.focus = { kind = "city", owner = "CityRun", label = "Courses en ville", map = run.map,
      since = (T.focus and T.focus.owner == "CityRun" and T.focus.since) or now(), gen = CR.gen or 0,
      pausedRoute = run.paused and true or false, card = function(n) return CR.CardModel(n) end }
  elseif T.focus and T.focus.owner == "CityRun" then
    T.focus = nil
  end
end
local function cardSoon()
  local Wn = T.Window
  if Wn and Wn.RenderCardSoon then pcall(Wn.RenderCardSoon) end
  if Wn and Wn.Dirty then pcall(Wn.Dirty) end
  if T.RequestRefresh then T:RequestRefresh() end
end
local function durability()
  local Inv = mod("Inventory")
  if Inv and type(Inv.Durability) == "function" then return (try(Inv.Durability)) end
  return nil
end
local function pendingTrain()
  local E = T.modules and T.modules.Errands
  return type(E) == "table" and type(E.char) == "table" and tonumber(E.char.trainPending) ~= nil
end
--- une course est faite ? (sacs, fenêtres visitées, durabilité, passée)
function CR.ErrandDone(e, run)
  run = run or CR.run
  if not run then return false end
  if run.skipE and run.skipE[e.id] then return true end
  local v = run.visited
  local function itemsDone()
    if not e.items or #e.items == 0 then return false end
    for _, it in ipairs(e.items) do if bagCount(it.id) < (it.base or 0) + it.n then return false end end
    return true
  end
  local k = e.kind
  if k == "vendor" then return itemsDone()
  elseif k == "bank" then return itemsDone() or v.bank == true
  elseif k == "ah" then return itemsDone() or v.ah == true
  elseif k == "mail" then return v.mail == true
  elseif k == "repair" then
    if run.repaired then return true end
    local d = durability()
    return (v.repair == true) and (d == nil or d >= 90) or false
  elseif k == "sell" then return v.sell == true or #CR.SellStacks() == 0
  elseif k == "ctrainer" then return v.ctrainer == true or (not e.optional and not e.picked and not pendingTrain())
  elseif k == "ptrainer" then return v["ptrainer:" .. tostring(e.line)] == true
  elseif k == "inn" then return v.inn == true end
  return false
end
function CR.StopDone(s, run)
  run = run or CR.run
  if not run then return false end
  if picks("city").skip[s.key] then return true end
  for _, e in ipairs(s.errands) do if not CR.ErrandDone(e, run) then return false end end
  return true
end
--- arrêts qui restent, dans l'ordre (le choisi d'abord)
function CR.Remaining()
  local run = CR.run
  if not run then return {} end
  local p = picks("city")
  local out, front = {}, nil
  for _, s in ipairs(run.stops) do
    if not CR.StopDone(s, run) then
      if p.key == s.key then front = s else out[#out + 1] = s end
    end
  end
  if front then tinsert(out, 1, front) elseif p.key then p.key = nil end
  return out
end
function CR.CurrentStop() return CR.Remaining()[1] end
--- aller à un arrêt (boussole), une fois par arrêt sauf clic
function CR.Guide(s, force)
  local run = CR.run
  if not (run and s) then return false end
  if s.key == run.lastGuide and not force then return false end
  run.lastGuide = s.key
  local C = T.Compass
  if C and type(C.SetTarget) == "function" then
    pcall(C.SetTarget, C, { mapID = s.mapID, x = s.x, y = s.y, label = cut(shownName(s.name), 40), source = "city" })
    return true
  end
  return false
end
--- lance les courses (un clic) ; opts = { lines, chain = {line, mode}, profOnly, picked }
function CR.Start(opts)
  if inCombat() then refuse("Courses en ville : après le combat.") return false end
  opts = opts or {}
  local P = PL()
  if P and P.focus then
    opts.chain = opts.chain or { line = P.focus.line, mode = P.focus.mode }
    P.StopFocus("silent")
  end
  if CR.run then CR.Stop("silent") end
  CR.char.picked = type(CR.char.picked) == "table" and CR.char.picked or {}
  opts.picked = opts.picked or CR.char.picked
  local nextStop = CR.NextStop()
  opts.nextStop = nextStop
  local plan = CR.Plan(opts)
  if #plan.stops == 0 and #plan.unknown == 0 then
    say("Courses en ville : rien à faire ici.")
    if opts.chain and P then P.StartFocus(opts.chain.line, opts.chain.mode) end
    return false
  end
  local paused = (P and P.PauseRoute and P.PauseRoute()) or false
  CR.char.pick.city = { skip = {}, hist = {} }
  CR.run = { plan = plan, stops = plan.stops, unknown = plan.unknown, since = now(), money0 = playerMoney(), visited = {},
    skipE = {}, chain = opts.chain, paused = paused, map = plan.map, opts = { lines = opts.lines, chain = opts.chain, profOnly = opts.profOnly },
    nextStop = nextStop }
  CR.char.run = { since = epoch(), map = plan.map, chain = opts.chain, paused = paused, lines = opts.lines, profOnly = opts.profOnly }
  CR.gen = (CR.gen or 0) + 1
  publish()
  T:Fire("FOCUS_CHANGED", "city")
  CR:Clear("propose")
  CR.Refresh(true)
  cardSoon()
  say(fmt("Courses en ville : %d arrêt%s · %s. Le trajet de quêtes est en pause.", #plan.stops, #plan.stops > 1 and "s" or "", minutes(plan.sec)))
  return true
end
--- recalcule (bouton « Recalculer ») : ton arrêt choisi et tes arrêts passés restent
function CR.Replan()
  local run = CR.run
  if not run then return false end
  local plan = CR.Plan({ lines = run.opts.lines, chain = run.opts.chain, profOnly = run.opts.profOnly, picked = CR.char.picked, nextStop = run.nextStop })
  run.plan, run.stops, run.unknown = plan, plan.stops, plan.unknown
  CR.gen = (CR.gen or 0) + 1
  publish()
  CR.Refresh(true)
  return true
end
--- fin des courses : trajet rendu, boussole rendue, « Fini : X dépensées, 4 min », session métier enchaînée si prévue
function CR.Stop(why)
  local run = CR.run
  if not run then return false end
  CR.run = nil
  CR.char.run = false
  local P = PL()
  if run.paused and P and P.ResumeRoute then P.ResumeRoute() end
  publish()
  if P and P.HideCard then P.HideCard() end
  if P and P.ClearGlows then P.ClearGlows() end
  local C = T.Compass
  local c = C and type(C.char) == "table" and C.char.custom or nil
  if type(c) == "table" and c.source == "city" and type(C.FollowRoute) == "function" then pcall(C.FollowRoute) end
  CR.gen = (CR.gen or 0) + 1
  T:Fire("FOCUS_CHANGED", nil)
  cardSoon()
  if CR.PinsDirty then pcall(CR.PinsDirty) end
  local spent = (run.money0 or 0) - playerMoney()
  local sec = now() - (run.since or now())
  CR.last = { spent = spent, sec = sec, why = why }
  if why == "done" then
    local ask = #(run.unknown or {}) > 0 and fmt(" (%d à demander à un garde)", #run.unknown) or ""
    say((spent >= 0 and fmt("Fini : %s dépensées, %s", money(spent), minutes(sec)) or fmt("Fini : %s gagnées, %s", money(-spent), minutes(sec))) .. ask)
  elseif why ~= "silent" then
    say("Courses en ville arrêtées : le trajet de quêtes reprend.")
  end
  if run.chain and why ~= "silent" and P and P.StartFocus then P.StartFocus(run.chain.line, run.chain.mode) end
  return true
end
--- reprise après /reload (moins d'une heure, même ville), sinon fin propre
function CR.Restore()
  local r = CR.char and CR.char.run
  if type(r) ~= "table" then return end
  local p = here()
  if epoch() - (tonumber(r.since) or 0) > 3600 or not p or p.mapID ~= r.map then
    CR.char.run = false
    local P = PL()
    if r.paused and P and P.ResumeRoute then P.ResumeRoute() end
    return
  end
  local P = PL()
  local wasPaused = r.paused
  local ok = CR.Start({ lines = r.lines, chain = r.chain, profOnly = r.profOnly })
  if ok and CR.run then CR.run.paused = CR.run.paused or wasPaused CR.char.run.paused = CR.run.paused
  elseif wasPaused and P and P.ResumeRoute then P.ResumeRoute() end
end
--- l'état avance : arrêts cochés, boussole sur l'arrêt en cours, carte, fin
function CR.Refresh(force)
  local run = CR.run
  if not run then return end
  if inCombat() then return end
  local rem = CR.Remaining()
  local cur = rem[1]
  if not cur then CR.Stop("done") return end
  local sig = {}
  for _, s in ipairs(rem) do sig[#sig + 1] = s.key end
  local sg = tconcat(sig, "|") .. "#" .. tostring(picks("city").key)
  if sg ~= run.sig or force then
    run.sig = sg
    CR.gen = (CR.gen or 0) + 1
    publish()
    if cur then CR.Guide(cur, force) end
    cardSoon()
    if CR.PinsDirty then pcall(CR.PinsDirty) end
  end
  CR.UpdateCard()
end
function CR.Soon(delay)
  if CR.pending then return end
  CR.pending = true
  T:After(delay or 0.2, function() CR.pending = false pcall(CR.Refresh) end)
end

-- ===================================================================== actions (UN clic = UNE action du jeu)
local function merchantShown() local f = g("MerchantFrame") return CR.merchantOpen or (f and try(f.IsVisible, f)) and true or false end
--- ce qu'il faut acheter CHEZ CE marchand : { {index, id, n, lot, price, avail, maxStack} }
function CR.MerchantNeeds()
  local run = CR.run
  if not (run and CR.merchantOpen) then return {} end
  -- un objet demandé par plusieurs courses (Épices douces : secourisme + cuisine) : les quantités s'ajoutent
  local want = {}
  for _, s in ipairs(run.stops) do
    for _, x in ipairs(s.items) do
      local it = x.item
      if not (run.skipE[x.errand.id]) and not picks("city").skip[s.key] then
        local w = want[it.id] or { base = 0, n = 0 }
        w.base, w.n = max(w.base, it.base or 0), w.n + it.n
        want[it.id] = w
      end
    end
  end
  local need = {}
  for id, w in pairs(want) do
    local left = w.base + w.n - bagCount(id) - (CR.pendingBuy[id] or 0)
    if left > 0 then need[id] = left end
  end
  local out = {}
  for i = 1, min(num((try(g("GetMerchantNumItems")))) or 0, 150) do
    local id = merchantID(i)
    if id and need[id] then
      local price, lot, avail = merchantInfo(i)
      out[#out + 1] = { index = i, id = id, n = need[id], lot = lot, price = price, avail = avail,
        maxStack = max(1, num((try(g("GetMerchantItemMaxStack"), i))) or 20) }
      need[id] = nil
    end
  end
  return out
end
function CR.BuyQty(e)
  local lot = max(1, e.lot or 1)
  local qty = ceil(e.n / lot) * lot
  local cap = max(lot, floor((e.maxStack or lot) / lot) * lot)
  if qty > cap then qty = cap end
  if e.avail and e.avail >= 0 and e.avail * lot < qty then qty = e.avail * lot end
  return qty
end
CR.pendingBuy = {}
-- (4.2 FIX42) le chemin unique des actions du jeu (Core.ShopCall : clic du joueur, hors combat, mode sûr)
local function shop(api, fn, ...)
  if T.ShopCall then return T:ShopCall(api, fn, ...) end
  if inCombat() then return false, "combat" end
  return pcall(fn, ...)
end
--- mode sûr : l'objet montré dans la fenêtre du marchand (ProfLevel), rien n'est acheté
local function showMerchant(index, txt)
  local P = PL()
  if P and type(P.ShowMerchantItem) == "function" then return P.ShowMerchantItem(index, txt) end
  say(txt .. " : clic droit dessus dans la fenêtre du marchand.")
  return false
end
CR.Shop = shop
--- UN achat (dans le clic) : BuyMerchantItem(case, quantité exacte, lot du marchand)
function CR.BuyNext()
  if inCombat() then refuse("Achat : hors combat.") return false end
  local e = CR.MerchantNeeds()[1]
  if not e then say("Rien à acheter chez ce marchand.") return false end
  local qty = CR.BuyQty(e)
  if qty <= 0 then say(itemName(e.id) .. " : épuisé chez ce marchand.") return false end
  local cost = floor((e.price or 0) * qty / max(1, e.lot))
  if cost > playerMoney() then say(fmt("Pas assez d'or pour %d %s (%s).", qty, itemName(e.id), money(cost))) return false end
  local fn = g("BuyMerchantItem")
  if type(fn) ~= "function" then return false end
  local ok, why = shop("BuyMerchantItem", fn, e.index, qty)
  if why == "safe" then return showMerchant(e.index, fmt("%d %s", qty, itemName(e.id))) end
  if ok then
    CR.pendingBuy[e.id] = (CR.pendingBuy[e.id] or 0) + qty
    CR.bought = (CR.bought or 0) + 1
    say(fmt("Acheté : %d %s (%s)", qty, itemName(e.id), money(cost)))
  end
  return ok
end
CR.pendingSell = {}
--- UNE pile vendue (dans le clic, marchand ouvert) : gris d'abord, puis compos en trop ; jamais une pile « besoin »
function CR.SellNext()
  if inCombat() then refuse("Vente : hors combat.") return false end
  if not merchantShown() then say("Ouvre un marchand d'abord.") return false end
  local s
  for _, x in ipairs(CR.SellStacks()) do if not CR.pendingSell[x.bag .. ":" .. x.slot] then s = x break end end
  if not s then say("Rien à vendre.") return false end
  local CC = g("C_Container")
  local fn = (CC and CC.UseContainerItem) or g("UseContainerItem")
  if type(fn) ~= "function" then return false end
  local ok, why = shop("UseContainerItem", fn, s.bag, s.slot)
  if why == "safe" then say(fmt("Mode sûr : clic droit sur %s dans tes sacs pour le vendre.", itemName(s.id))) return false end
  if ok then
    CR.pendingSell[s.bag .. ":" .. s.slot] = true
    CR.sold = (CR.sold or 0) + 1
    say(fmt("Vendu : %d %s (%s) — rachetable chez ce marchand", s.n, itemName(s.id), money(s.value or 0)))
  end
  return ok
end
local function sellLeft()
  local n = 0
  for _, x in ipairs(CR.SellStacks()) do if not CR.pendingSell[x.bag .. ":" .. x.slot] then n = n + 1 end end
  return n
end
--- réparer tout (un clic = un appel RepairAllItems), si tu as l'or
function CR.RepairNow()
  if inCombat() then refuse("Réparation : hors combat.") return false end
  if not (merchantShown() and pub(try(g("CanMerchantRepair")))) then say("Ce marchand ne répare pas.") return false end
  local cost = num((try(g("GetRepairAllCost")))) or 0
  if cost <= 0 then say("Rien à réparer.") return false end
  if cost > playerMoney() then say(fmt("Pas assez d'or pour réparer (%s).", money(cost))) return false end
  local fn = g("RepairAllItems")
  if type(fn) ~= "function" then return false end
  local ok, why = shop("RepairAllItems", fn)
  if why == "safe" then say("Mode sûr : clique l'enclume « Tout réparer » de la fenêtre du marchand.") return false end
  if ok then
    if CR.run then CR.run.repaired = true end
    say("Réparé : " .. money(cost))
  end
  return ok
end
local function repairWanted()
  local run = CR.run
  if not run or run.repaired then return false end
  for _, s in ipairs(run.stops) do for _, e in ipairs(s.errands) do if e.kind == "repair" and not CR.ErrandDone(e, run) then return true end end end
  return false
end
CR.pendingBank = {}
--- ce qu'il reste à retirer de la banque : { {id, n, slots} }
function CR.BankNeeds()
  local run = CR.run
  local P = PL()
  if not (run and CR.bankOpen) then return {} end
  local out = {}
  for _, s in ipairs(run.stops) do
    for _, e in ipairs(s.errands) do
      if e.kind == "bank" then
        for _, it in ipairs(e.items or {}) do
          local left = (it.base or 0) + it.n - bagCount(it.id) - (CR.pendingBank[it.id] or 0)
          if left > 0 then out[#out + 1] = { id = it.id, n = left, slots = (P and P.bankSlots or {})[it.id] or {} } end
        end
      end
    end
  end
  return out
end
--- UN retrait (dans le clic) : la plus petite pile qui suffit, sinon la plus grosse
function CR.WithdrawNext()
  if inCombat() then refuse("Banque : hors combat.") return false end
  if merchantShown() then say("Ferme le marchand d'abord (sinon le jeu vendrait l'objet).") return false end
  if not CR.bankOpen then say("Ouvre ta banque d'abord.") return false end
  local e = CR.BankNeeds()[1]
  if not e then say("Rien à retirer.") return false end
  local pick
  for _, s in ipairs(e.slots) do if s.n >= e.n and (not pick or s.n < pick.n) then pick = s end end
  if not pick then for _, s in ipairs(e.slots) do if not pick or s.n > pick.n then pick = s end end end
  if not pick then say(itemName(e.id) .. " : introuvable dans la banque.") return false end
  local CC = g("C_Container")
  local fn = (CC and CC.UseContainerItem) or g("UseContainerItem")
  if type(fn) ~= "function" then return false end
  local ok, why = shop("UseContainerItem", fn, pick.bag, pick.slot)
  if why == "safe" then
    local P = PL()
    if P and P.GlowBank then pcall(P.GlowBank, { e }) end
    say(fmt("Mode sûr : clic droit sur la case en surbrillance (%s) de ta banque.", itemName(e.id)))
    return false
  end
  if ok then
    CR.pendingBank[e.id] = (CR.pendingBank[e.id] or 0) + pick.n
    say(fmt("Retiré : %d %s", pick.n, itemName(e.id)))
  end
  return ok
end
-- (4.2 FIX42) « Sortir à vendre » : à la banque, le SURPLUS des compos (verdict « en trop » : jamais ce que le plan
-- ou les quêtes demandent) passe dans tes sacs, une pile par clic ; le marchand le vend ensuite (carte du marchand)
CR.pendingOut = {}
function CR.BankSellList()
  local P = PL()
  if not (CR.bankOpen or (P and P.bankOpen)) then return {} end
  local slots = P and P.bankSlots or {}
  local out = {}
  for _, e in ipairs(CR.ExtraList()) do
    local inBank = e.n - (e.bags or 0) - (CR.pendingOut[e.id] or 0)
    if inBank > 0 and slots[e.id] and #slots[e.id] > 0 then out[#out + 1] = { id = e.id, n = inBank, slots = slots[e.id] } end
  end
  return out
end
function CR.BankSellNext()
  if inCombat() then refuse("Banque : hors combat.") return false end
  if merchantShown() then say("Ferme le marchand d'abord (sinon le jeu vendrait l'objet).") return false end
  local e = CR.BankSellList()[1]
  if not e then say("Rien en trop à la banque.") return false end
  -- la plus grosse pile qui ne dépasse pas le surplus (jamais une pile qui contient du « besoin »)
  local pick
  for _, sl in ipairs(e.slots) do if sl.n <= e.n and (not pick or sl.n > pick.n) then pick = sl end end
  if not pick then say(fmt("%s : la plus petite pile de la banque dépasse le surplus (%d) — rien sorti.", itemName(e.id), e.n)) return false end
  local CC = g("C_Container")
  local fn = (CC and CC.UseContainerItem) or g("UseContainerItem")
  if type(fn) ~= "function" then return false end
  local ok, why = shop("UseContainerItem", fn, pick.bag, pick.slot)
  if why == "safe" then
    local P = PL()
    if P and P.GlowBank then pcall(P.GlowBank, { { id = e.id, slots = { pick } } }) end
    say(fmt("Mode sûr : clic droit sur la case en surbrillance (%s) de ta banque.", itemName(e.id)))
    return false
  end
  if ok then
    CR.pendingOut[e.id] = (CR.pendingOut[e.id] or 0) + pick.n
    say(fmt("Sorti de la banque : %d %s (à vendre)", pick.n, itemName(e.id)))
  end
  return ok
end

--- (contrat ProfLevel.FocusPlan) pendant les courses, à l'HV : le métier dont on achète les compos
function CR.Borrow()
  local run = CR.run
  if not run then return nil end
  for _, s in ipairs(run.stops) do
    for _, e in ipairs(s.errands) do
      if e.kind == "ah" and e.line and not CR.ErrandDone(e, run) then return { line = e.line, mode = e.mode or "full" } end
    end
  end
  return nil
end
--- (contrat ProfLevel.UpdateCard) la carte sous les fenêtres du jeu est à nous pendant les courses
function CR.OwnsCard() return CR.run ~= nil end
local function frameOf(names) for _, n in ipairs(names) do local f = g(n) if f and try(f.IsVisible, f) then return f end end return nil end
local function ahLabel(e, P)
  local s = P.ahState
  if s and s.id == e.id then
    if s.state == "search" then return "Recherche…" end
    if s.state == "ready" then return fmt("Acheter %d (%s)", s.qty, money(s.total or 0)) end
    if s.state == "starting" then return "Vérification du prix…" end
    if s.state == "confirm" then return fmt("Confirmer %s", money(s.total or 0)) end
    if s.state == "buying" then return "Achat en cours…" end
    if s.state == "high" then return "Trop cher : rechercher" end
  end
  return "Chercher " .. cut(itemName(e.id), 22)
end
--- la carte sous la fenêtre du jeu ouverte (marchand, banque, HV, courrier, maître)
function CR.UpdateCard()
  local P = PL()
  if not (P and P.ShowCard) then return end
  if inCombat() or not CR.run then P.HideCard() if P.ClearGlows then P.ClearGlows() end return end
  local run = CR.run
  local rem = CR.Remaining()
  local head = fmt("Courses en ville · %d arrêt%s restant%s", #rem, #rem > 1 and "s" or "", #rem > 1 and "s" or "")
  if CR.merchantOpen then
    local owner = frameOf({ "MerchantFrame" })
    local lines = {}
    local nSell = sellLeft()
    local buys = CR.MerchantNeeds()
    local rep = repairWanted() and pub(try(g("CanMerchantRepair"))) and (num((try(g("GetRepairAllCost")))) or 0) or 0
    if nSell > 0 then lines[#lines + 1] = fmt("À vendre : %d pile%s (gris, compos en trop)", nSell, nSell > 1 and "s" or "") end
    if rep > 0 then lines[#lines + 1] = "Réparation : " .. money(rep) end
    for i, e in ipairs(buys) do if i <= 4 then lines[#lines + 1] = fmt("À acheter : %d %s", e.n, itemName(e.id)) end end
    if nSell > 0 then
      P.ShowCard(owner, head, tconcat(lines, "\n"), fmt("Vendre (%d)", nSell), CR.SellNext,
        { "Vendre au marchand", "Clic : UNE pile vendue (gris d'abord, puis les compos en trop).", "Rien n'est détruit : tout se rachète chez lui." })
      return
    end
    if rep > 0 then
      P.ShowCard(owner, head, tconcat(lines, "\n"), "Réparer (" .. money(rep) .. ")", CR.RepairNow,
        { "Réparer", "Clic : tout réparer chez ce marchand (" .. money(rep) .. ")." })
      return
    end
    if #buys > 0 then
      local n = 0
      for _, e in ipairs(buys) do local q = CR.BuyQty(e) if q > 0 then n = n + ceil(e.n / q) end end
      local label = (CR.bought or 0) == 0 and fmt("Acheter tout (%d)", n) or fmt("Acheter la suite (%d)", n)
      P.ShowCard(owner, head, tconcat(lines, "\n"), label, CR.BuyNext,
        { "Acheter tout", fmt("Clic : %d %s maintenant (lot du marchand, quantité exacte).", CR.BuyQty(buys[1]), itemName(buys[1].id)),
          "UN achat par clic ; rien n'est acheté sans clic." })
      return
    end
    P.HideCard()
    return
  end
  if CR.bankOpen then
    local list = CR.BankNeeds()
    if #list > 0 then
      local lines = {}
      for i, e in ipairs(list) do if i <= 4 then lines[#lines + 1] = fmt("%d %s", e.n, itemName(e.id)) end end
      if P.GlowBank then P.GlowBank(list) end
      P.ShowCard(frameOf({ "BankFrame", "BankPanel" }), head .. " : à retirer", tconcat(lines, "\n"), "Retirer " .. cut(itemName(list[1].id), 24),
        CR.WithdrawNext, { "Retirer", "Clic : une pile de " .. itemName(list[1].id) .. " passe dans tes sacs (cases en surbrillance)." })
      return
    end
    if P.ClearGlows then P.ClearGlows() end
  end
  if CR.ahOpen and P.AHPlan and P.AHNext then
    local list = P.AHPlan()
    if #list > 0 then
      local lines = {}
      for i, it in ipairs(list) do if i <= 3 then lines[#lines + 1] = fmt("%d %s (≤ %s/u)", it.n, itemName(it.id), money(P.MaxUnit(it.unit))) end end
      P.ShowCard(frameOf({ "AuctionHouseFrame", "AuctionFrame" }), head .. " : à l'HV", tconcat(lines, "\n"), ahLabel(list[1], P), P.AHNext,
        { "Hôtel des ventes", "Clic 1 : recherche du prix. Clic 2 : achat (quantité exacte). Clic 3 : confirmer.",
          "Le prix est revérifié : au-dessus du plafond, rien n'est acheté." })
      return
    end
  end
  if CR.mailOpen then
    for _, s in ipairs(run.stops) do
      for _, e in ipairs(s.errands) do
        if e.kind == "mail" and e.items and #e.items > 0 then
          P.ShowCard(frameOf({ "MailFrame", "InboxFrame" }), head .. " : courrier", "Prends : " .. itemsText(e.items, 4), nil, nil, nil)
          return
        end
      end
    end
  end
  if CR.trainerOpen then
    for _, s in ipairs(run.stops) do
      for _, e in ipairs(s.errands) do
        if (e.kind == "ptrainer" or e.kind == "ctrainer") and not CR.ErrandDone(e, run) then
          P.ShowCard(frameOf({ "ClassTrainerFrame", "TrainerFrame" }), head .. " : maître", e.label .. "\n" .. (e.why or ""), nil, nil, nil)
          return
        end
      end
    end
  end
  P.HideCard()
end

-- ===================================================================== carte « Prochaine étape » (contrat T.focus.card)
function CR.CardModel(n)
  n = tonumber(n) or 3
  local run = CR.run
  if not run then return {}, "Courses en ville", "" end
  local rows = {}
  local here0 = here()
  for i, s in ipairs(CR.Remaining()) do
    if #rows >= n then break end
    local st = s
    rows[#rows + 1] = { n = i, title = s.title, sub = stopSub(s, here0), icon = s.icon,
      actionText = "viser ce PNJ (clic droit : passer cet arrêt)",
      onClick = function() CR.Pick("city", st) CR.Guide(st, true) end,
      onRightClick = function() CR.Skip("city", st) end }
  end
  for _, u in ipairs(run.unknown) do
    if #rows >= n then break end
    rows[#rows + 1] = { n = "?", title = "Demande à un garde : " .. cut(u.what, 36), sub = (u.errand.label or "") .. " · TreeAI retiendra sa place",
      icon = ICON .. "INV_Misc_QuestionMark", actionText = "ranger cette ligne", onClick = function() run.doneUnknown = true CR.Refresh(true) end }
  end
  if #rows == 0 then
    rows[1] = { n = "!", title = "Courses finies", sub = "clic : reprendre le trajet", onClick = function() CR.Stop("done") end, actionText = "terminer" }
  end
  local rem = CR.Remaining()
  local sec = 0
  local prev = here0
  for _, s in ipairs(rem) do sec = sec + (CR.LegSec(prev, s) or 0) + SERVICE_SEC prev = s end
  if run.plan.exit and prev then sec = sec + (CR.LegSec(prev, run.plan.exit) or 0) end
  local sum = fmt("%d arrêt%s · %s", #rem, #rem > 1 and "s" or "", minutes(sec))
  if run.plan.cost > 0 then sum = sum .. " · " .. money(run.plan.cost) end
  return rows, "Courses en ville", sum
end

-- ===================================================================== proposition en entrant en ville
function CR.CheckPropose()
  if not (CR:IsEnabled() and CR.db and CR.db.propose ~= false) then return end
  if T.focus or inCombat() then CR:Clear("propose") return end
  if not CR.InTown() then CR:Clear("propose") CR.proposedFor = nil return end
  local p = here()
  if not p or CR.proposedFor == p.mapID then return end
  local plan = tempPlans(CR.Plan, {})
  if type(plan) ~= "table" then return end
  local n = 0
  for _, e in ipairs(plan.errands) do if e.kind ~= "sell" then n = n + 1 end end
  if n < 2 then return end
  CR.proposedFor = p.mapID
  CR.lastPlan = plan
  CR:Suggest("propose", fmt("Courses en ville : %d courses, %d arrêt%s · %s", n, #plan.stops, #plan.stops > 1 and "s" or "", minutes(plan.sec)), 2,
    { ttl = 0, tab = "now", sound = false, icon = CR.ICON, module = "CityRun",
      onClick = function() CR.Start() end,
      tooltip = { "Courses en ville", "Clic : lancer (trajet optimal, la boussole te guide, chaque arrêt se coche seul).",
        "Clic droit : ne plus proposer." } })
  -- (4.2 FIX42) en arrivant en ville : UNE petite alerte (une fois par visite) qui dit ce qu'il y a à faire ici
  -- (« Ici : réparer, flèches, maître de classe »), au même endroit que les autres alertes ; rien dans le chat
  if T.ShowAlert and CR.db.townToast ~= false then
    local names, seen = {}, {}
    for _, e in ipairs(plan.errands) do
      local l = e.kind ~= "sell" and CR.ShortLabel(e) or nil
      if l and not seen[l] and #names < 3 then seen[l] = true names[#names + 1] = l end
    end
    if #names > 0 then
      pcall(T.ShowAlert, T, { id = "CityRun:town", text = "Ici : " .. tconcat(names, ", ") .. (n > #names and fmt(" (+%d)", n - #names) or "")
        .. " — « Courses en ville » dans TreeAI", prio = 3, tab = "now" }, true)
      CR.toastFor = p.mapID
    end
  end
end
--- (4.2 FIX42) libellé court d'une course pour l'alerte d'arrivée en ville
local SHORT = { repair = "réparer", bank = "banque", mail = "courrier", ah = "HV", ctrainer = "maître de classe",
  ptrainer = "maître de métier", inn = "auberge", flight = "vol" }
function CR.ShortLabel(e)
  if type(e) ~= "table" then return nil end
  if SHORT[e.kind] then return SHORT[e.kind] end
  if e.kind == "vendor" then
    local it = e.items and e.items[1]
    if it then return fmt("%d %s", tonumber(it.n) or 0, itemName(it.id)) end
    return type(e.label) == "string" and cut(e.label, 24) or "marchand"
  end
  return type(e.label) == "string" and cut(e.label, 24) or nil
end

-- ===================================================================== « Me concentrer sur les métiers »
--- un clic : courses en ville d'abord si le plan demande des achats / retraits / le maître et que tu es en ville,
--- puis la session métier (enchaînée) ; sinon la session métier tout de suite
function CR.ProfFocus(line, mode)
  local P = PL()
  if not (P and P.StartFocus) then say("Module Métiers coupé.") return false end
  if inCombat() then refuse("Après le combat.") return false end
  line = tonumber(line) or (P.Line and P.Line())
  mode = mode or (P.Mode and P.Mode())
  if not line then say("Aucun métier à monter.") return false end
  if CR.InTown() then
    local errs = CR.Errands({ lines = { line }, profOnly = true, chain = { line = line, mode = mode } })
    local n = 0
    for _, e in ipairs(errs) do if not e.optional and e.kind ~= "sell" then n = n + 1 end end
    if n > 0 then return CR.Start({ lines = { line }, profOnly = true, chain = { line = line, mode = mode } }) end
  end
  return P.StartFocus(line, mode)
end
--- boutons de l'en-tête de la page Métiers
function CR.PageButtons()
  local P = PL()
  local list = {}
  if P and P.focus then
    list[#list + 1] = { "Quitter la session métier", function() P.StopFocus() end, "Clic : fin de la session, le trajet de quêtes reprend" }
  elseif CR.run then
    list[#list + 1] = { "Arrêter les courses", function() CR.Stop() end, "Clic : fin des courses en ville" }
  else
    list[#list + 1] = { "Me concentrer sur les métiers", function() CR.ProfFocus() end,
      "Clic : courses en ville d'abord si ton plan demande des achats (marchand, banque, maître, HV), puis la session métier (trajet de quêtes en pause)" }
  end
  list[#list + 1] = { "Courses en ville", function() local Wn = T.Window if Wn and Wn.Open then Wn.Open("city") end end,
    "Clic : la liste de tout ce qu'il y a à faire en ville, dans l'ordre le plus court" }
  if CR.PinsButton then list[#list + 1] = CR.PinsButton() end
  return list
end
local function pct(x) return x and (fmt("%.1f", x / 100):gsub("%.", ",") .. " pa") or "?" end
--- bloc « Options » de la page Métiers : acheter / apprendre chez le maître / tes compos, coût par point, ton choix
function CR.RenderProfOptions(Ly, line)
  local o = CR.ProfOptions(line)
  if not o or #o.list == 0 then return end
  local ch = CR.char.choice and CR.char.choice[line]
  local cur = ch or o.best
  if not Ly.header("Options : le moins cher par point", "city:opts:" .. line, nil, ICON .. "INV_Misc_Coin_02") then return end
  for _, x in ipairs(o.list) do
    local txt
    if x.id == "trainer" then
      txt = fmt("Apprendre %s chez le maître (%s) : %d points%s",
        x.names and #x.names > 0 and ("« " .. cut(tconcat(x.names, " », « "), 60) .. " »") or "ses recettes",
        x.costKnown and money(x.learnCost) or "prix chez lui", x.points,
        x.perPt and (" à " .. pct(x.perPt) .. "/point") or "")
    elseif x.id == "farm" then
      txt = fmt("%s : %d points sans rien acheter à l'HV%s", x.label, x.points, x.cost > 0 and (" (marchand " .. money(x.cost) .. ")") or "")
    else
      txt = fmt("%s : %s pour %d points%s%s", x.label, money(x.cost), x.points, x.perPt and (" (" .. pct(x.perPt) .. "/point)") or "",
        x.farm > 0 and fmt(" + %d à farmer", x.farm) or "")
    end
    txt = txt .. " · ≈ " .. minutes(x.time)
    local id = x.id
    Ly.row({ text = txt, right = (cur == id) and (ch and "mon choix" or "conseillé") or "choisir", kind = "info",
      onClick = function() CR.SetChoice(line, id) end,
      tooltip = { x.label, x.confirm and "Exigence estimée (WoW Forever ne la donne pas) : à confirmer chez le maître ; TreeAI la lit à ta visite et recalcule." or nil,
        "Clic : c'est mon choix (gardé)." } })
  end
  local P = PL()
  Ly.buttons({
    { "Aller au maître", function()
      local st = CR.TrainerStep(line)
      CR.Pick("prof", st)
      if P and P.Guide and st.where then P.Guide(st, true) say("Boussole : " .. cut(shownName(st.where.name), 40))
      else say("Maître pas encore vu : demande à un garde, TreeAI retiendra sa place.") end
    end, "Clic : l'étape « Aller au maître » devient l'étape en cours (la boussole y va)" },
    { ch and "Choix automatique" or "Auto (le moins cher)", function() CR.SetChoice(line, nil) end, "Clic : TreeAI choisit l'option la moins chère par point" },
  }, 2)
end

-- ===================================================================== page « Courses en ville » (fenêtre, cachée de la navigation)
local function renderPage(Ly)
  local run = CR.run
  local plan = run and run.plan
  if not plan then
    local ok, p2 = pcall(CR.Plan, {})
    plan = ok and p2 or nil
  end
  if not plan then Ly.empty("Position inconnue : ouvre la carte ou bouge un peu.") return end
  local stops = run and CR.Remaining() or plan.stops
  local sub = fmt("%d arrêt%s · %s", #stops, #stops > 1 and "s" or "", minutes(plan.sec))
    .. (plan.cost > 0 and (" · " .. money(plan.cost)) or "") .. (plan.gain > 0 and (" · vente ≈ " .. money(plan.gain)) or "")
  Ly.hero(CR.ICON, run and "Courses en ville (en cours)" or "Courses en ville", sub,
    plan.exit and ("Sortie : " .. cut(shownName(plan.exit.name), 30) .. " (" .. (plan.exit.why or "") .. ")") or nil, nil,
    { "Tout ce qu'il y a à acheter / faire ici, dans l'ordre le plus court (temps de marche).",
      "Clic sur un arrêt : c'est lui maintenant ; clic droit : le passer." })
  local btns = {}
  if run then
    btns[#btns + 1] = { "Arrêter", function() CR.Stop() end, "Clic : fin des courses, le trajet de quêtes reprend" }
    btns[#btns + 1] = { "Recalculer", function() CR.Replan() end, "Clic : recalculer (ton arrêt choisi et tes arrêts passés restent)" }
  else
    btns[#btns + 1] = { "Lancer les courses", function() CR.Start() end, "Clic : trajet de quêtes en pause, la boussole te guide d'arrêt en arrêt" }
    btns[#btns + 1] = { "Me concentrer sur les métiers", function() CR.ProfFocus() end, "Clic : les courses du métier puis la session métier" }
  end
  Ly.buttons(btns, 2)
  if run then Ly.buttons(CR.StepButtons(), 2) end
  local mt = CR.MoneyText(plan)
  if mt then Ly.text(mt, "S", "prio3") end
  if Ly.header(fmt("Trajet (%d)", #stops), "city:stops", nil, CR.ICON) then
    if #stops == 0 then Ly.text("Rien à faire ici pour l'instant.", "S", "muted") end
    for i, s in ipairs(stops) do
      local st = s
      Ly.step({ n = i, title = s.title, sub = s.sub, actionText = "c'est lui maintenant (la boussole y va)",
        onClick = function() if CR.run then CR.Pick("city", st) CR.Guide(st, true) else CR.Start() end end,
        onRightClick = function() if CR.run then CR.Skip("city", st) end end }, i == 1)
      for _, e in ipairs(s.errands) do Ly.text("· " .. e.label .. (e.why and (" — " .. e.why) or ""), "S", "muted", 24) end
    end
  end
  if #plan.unknown > 0 and Ly.header(fmt("À demander à un garde (%d)", #plan.unknown), "city:unknown") then
    for _, u in ipairs(plan.unknown) do
      Ly.row({ text = fmt("%s — %s", cut(u.what, 40), u.errand.label or ""), kind = "info",
        tooltip = { "Position pas encore connue : demande à un garde de la ville.", "TreeAI retient le PNJ dès que tu ouvres sa fenêtre." } })
    end
  end
  if #plan.optional > 0 and Ly.header(fmt("Au choix (%d)", #plan.optional), "city:opt", nil, nil, true) then
    for _, e in ipairs(plan.optional) do
      local ee = e
      Ly.row({ text = e.label .. (e.why and (" — " .. e.why) or ""), right = "ajouter", kind = "info",
        onClick = function()
          CR.char.picked = type(CR.char.picked) == "table" and CR.char.picked or {}
          if ee.accept and ee.line then local P = PL() if P and P.SetAH then P.SetAH(ee.line, true) end end
          CR.char.picked[ee.id] = true
          if CR.run then CR.Replan() end
          local Wn = T.Window if Wn and Wn.Dirty then pcall(Wn.Dirty) end
        end, tooltip = "Clic : l'ajouter au trajet" })
    end
  end
  local extra = CR.ExtraList()
  if #extra > 0 and Ly.header(fmt("À vendre / à jeter (%d)", #extra), "city:extra") then
    for i, x in ipairs(extra) do
      if i > 10 then break end
      Ly.row({ text = fmt("%d %s — %s au marchand%s", x.n, itemName(x.id), money(x.sell * x.n),
        x.ah and (" · HV ≈ " .. money(x.ah * x.n)) or ""), kind = "info",
        tooltip = { x.why or "", x.bags < x.n and fmt("Dont %d en banque.", x.n - x.bags) or nil,
          "Chez un marchand, la carte « Vendre » vend une pile par clic (jamais rien de détruit)." } })
    end
  end
  if plan.exit then Ly.text("Sortie : " .. cut(shownName(plan.exit.name), 40) .. " — " .. (plan.exit.why or ""), "S", "muted") end
end
CR.RenderPage = renderPage
local Wn = T.Window
if Wn and type(Wn.AddPage) == "function" then
  Wn.AddPage({ id = "city", hidden = true, before = "options", label = "Courses", icon = CR.ICON, title = "Courses en ville",
    desc = "Tout ce qu'il y a à acheter / faire en ville, dans l'ordre le plus court",
    render = renderPage,
    action = function()
      if CR.run then return "Arrêter les courses", function() CR.Stop() end, "Clic : fin des courses en ville" end
      return "Lancer les courses", function() CR.Start() end, "Clic : la boussole te guide d'arrêt en arrêt"
    end,
  })
end

-- ===================================================================== points « Services » (carte du monde + minimap)
-- Tes maîtres de métier (tes métiers seulement), les marchands qui vendent ce dont ton plan a besoin (ce qu'ils vendent,
-- prix, lot), HV, banque, boîte aux lettres, maître de classe, aubergiste, maître de vol, réparation ; les arrêts des
-- courses numérotés 1..n. Sources : PNJ appris à ta visite, puis Data (vols), puis QuestieDB s'il est chargé (lecture
-- seule). Carte : NOTRE calque (enfant d'UIParent posé sur le canevas, prévenu par un fournisseur de données), dessiné
-- seulement quand la carte change ; minimap : HereBeDragons s'il est chargé, sinon projection maison, seulement en
-- ville et en marchant (aucun minuteur à l'arrêt). Seuls les boutons des points prennent la souris.
local PIN_TEX = { vendor = "INV_Misc_Coin_02", repair = "Trade_BlackSmithing", ctrainer = "INV_Misc_Book_11",
  bank = "INV_Box_02", mail = "INV_Letter_15", ah = "INV_Misc_Coin_17", inn = "INV_Misc_Rune_01",
  flight = "Ability_Mount_Wyvern_01", ammo = "INV_Ammo_Arrow_02", petfood = "INV_Misc_Food_14", food = "INV_Misc_Food_11",
  stop = "INV_Misc_Bag_10" }
CR.MAX_PINS = 40
function CR.PinsOn() return CR:IsEnabled() and CR.db ~= nil and CR.db.pins ~= false end
local pinCache = { key = nil, list = nil }
local needCache = { at = -100, sig = nil, idx = {} }
--- objets à acheter (courses en cours, sinon la liste du moment) : [objet] = { n, label, cat }
function CR.NeedIndex()
  local errands
  if CR.run then errands = CR.run.plan.errands
  else
    local sig = tostring(API.BagGen and API.BagGen() or 0) .. ":" .. tostring(CR.memGen or 0) .. ":" .. tostring(CR.choiceGen or 0)
    if needCache.sig == sig and now() - needCache.at < 30 then return needCache.idx end
    local plan = tempPlans(CR.Plan, {})
    errands = type(plan) == "table" and plan.errands or {}
    needCache.sig, needCache.at = sig, now()
  end
  local idx = {}
  for _, e in ipairs(errands or {}) do
    if e.kind == "vendor" then
      for _, it in ipairs(e.items or {}) do idx[it.id] = { n = it.n, label = e.label, cat = e.cat } end
    end
  end
  if not CR.run then needCache.idx = idx end
  return idx
end
local function questieTrainers(line, mapID)
  local out = {}
  local Qe = g("Questie")
  local qdb = type(Qe) == "table" and type(Qe.db) == "table" and Qe.db or nil
  local npcs
  for _, scope in ipairs({ "global", "char", "profile" }) do
    local t = qdb and type(qdb[scope]) == "table" and qdb[scope].professionTrainers
    if type(t) == "table" and type(t[line]) == "table" then npcs = t[line] break end
  end
  local Cp = T.Compass
  if not (npcs and type(Cp) == "table" and type(Cp.Questie) == "function" and type(Cp.SpawnsOf) == "function") then return out end
  local db, zdb = Cp.Questie()
  if type(db) ~= "table" then return out end
  for i, npc in ipairs(npcs) do
    if i > 30 then break end
    if type(npc) == "number" then
      local pts = {}
      pcall(Cp.SpawnsOf, "npc", npc, db, zdb, mapID, pts)
      for _, pt in ipairs(pts) do if pt.mapID == mapID then out[#out + 1] = { name = pt.name or ("Maître de " .. lineName(line):lower()), x = pt.x, y = pt.y } end end
    end
  end
  return out
end
--- les points d'une carte : { key, kind, name, mapID, x, y, role, why = {lignes}, icon, n (arrêt), stop }
function CR.PinList(mapID)
  mapID = tonumber(mapID)
  if not mapID or not CR.PinsOn() then return {} end
  local p = here()
  local mine = p and p.mapID == mapID
  local key = tconcat({ mapID, tostring(CR.memGen or 0), tostring(CR.gen or 0), tostring(API.BagGen and API.BagGen() or 0),
    tostring(CR.pinGen or 0), mine and "m" or "o" }, ":")
  if pinCache.key == key and pinCache.list then return pinCache.list end
  local ranks = profRanks()
  local need = mine and CR.NeedIndex() or {}
  local list, byName = {}, {}
  local function add(pin)
    if #list >= CR.MAX_PINS or not (pin.name and tonumber(pin.x) and tonumber(pin.y)) then return end
    if byName[pin.name .. ":" .. pin.kind] then return end
    for _, q in ipairs(list) do
      if q.kind == pin.kind and math.abs(q.x - pin.x) < 0.004 and math.abs(q.y - pin.y) < 0.004 then return end
    end
    byName[pin.name .. ":" .. pin.kind] = true
    pin.mapID = mapID
    pin.key = pin.kind .. ":" .. pin.name
    pin.icon = pin.icon or (ICON .. (PIN_TEX[pin.kind] or PIN_TEX.vendor))
    list[#list + 1] = pin
  end
  -- arrêts des courses (numérotés)
  if CR.run then
    for i, s in ipairs(CR.Remaining()) do
      if s.mapID == mapID then
        add({ kind = "stop", name = shownName(s.name), x = s.x, y = s.y, n = i, stop = s, role = fmt("Arrêt %d des courses", i),
          why = { s.title } })
      end
    end
  end
  local function vendorWhy(e)
    local why, cat = {}, nil
    for id, v in pairs(CR.ItemsOf(e)) do
      local nd = need[tonumber(id) or id]
      if nd then
        cat = cat or nd.cat
        local price = type(v) == "table" and tonumber(v[1])
        local lot = type(v) == "table" and tonumber(v[2]) or 1
        why[#why + 1] = fmt("vend %s : %d pour %s%s", itemName(tonumber(id) or id), nd.n,
          nd.cat == "mats" and "ton plan" or "toi", price and fmt(" (%s le lot de %d)", money(price), lot) or "")
      end
    end
    tsort(why)
    return why, cat
  end
  local P = PL()
  local mlist = mem()[tostring(mapID)]
  for name, e in pairs(type(mlist) == "table" and mlist or {}) do
    if type(e) == "table" and type(e.k) == "table" and tonumber(e.x) then
      local nm = shownName(name)
      if e.k.ptrainer and e.l and ranks[e.l] then
        add({ kind = "ptrainer", name = nm, x = e.x, y = e.y, role = "Maître de " .. lineName(e.l):lower(),
          why = { fmt("ton métier : %s %d", lineName(e.l), ranks[e.l]) }, icon = P and P.LineIcon and P.LineIcon(e.l) or nil })
      end
      if e.k.ctrainer and (e.c == nil or e.c == myClass()) then
        add({ kind = "ctrainer", name = nm, x = e.x, y = e.y, role = "Maître de classe", why = pendingTrain() and { "nouveaux rangs à apprendre" } or {} })
      end
      if e.k.vendor then
        local why, cat = vendorWhy(e)
        if #why > 0 then add({ kind = (cat == "ammo" or cat == "petfood" or cat == "food") and cat or "vendor", name = nm, x = e.x, y = e.y,
          role = KIND_FR.vendor, why = why })
        elseif e.k.repair then add({ kind = "repair", name = nm, x = e.x, y = e.y, role = "Marchand (répare)", why = {} }) end
      end
      for _, k in ipairs({ "bank", "mail", "ah", "inn", "flight" }) do
        if e.k[k] then add({ kind = k, name = k == "mail" and KIND_FR.mail or nm, x = e.x, y = e.y, role = KIND_FR[k], why = {} }) end
      end
    end
  end
  -- ProfLevel : marchands de compos vus (objets de ton plan)
  local pdb = T.Prof and T.Prof.db
  local vend = type(pdb) == "table" and type(pdb.vend) == "table" and pdb.vend[tostring(mapID)] or nil
  for name, e in pairs(type(vend) == "table" and vend or {}) do
    if type(e) == "table" and tonumber(e.x) and type(e.i) == "table" then
      local why = {}
      for _, id in ipairs(e.i) do local nd = need[id] if nd then why[#why + 1] = fmt("vend %s : %d pour ton plan", itemName(id), nd.n) end end
      if #why > 0 then add({ kind = "vendor", name = name, x = e.x, y = e.y, role = KIND_FR.vendor, why = why }) end
    end
  end
  local trn = type(pdb) == "table" and type(pdb.trn) == "table" and pdb.trn[tostring(mapID)] or nil
  for name, e in pairs(type(trn) == "table" and trn or {}) do
    if type(e) == "table" and tonumber(e.x) and e.l and ranks[e.l] then
      add({ kind = "ptrainer", name = name, x = e.x, y = e.y, role = "Maître de " .. lineName(e.l):lower(),
        why = { fmt("ton métier : %s %d", lineName(e.l), ranks[e.l]) }, icon = P and P.LineIcon and P.LineIcon(e.l) or nil })
    end
  end
  if mine then
    -- Detect (PNJ vus + habitants Questie + maîtres de vol du jeu) pour les services de la ville
    local Dt = T.Detect
    if type(Dt) == "table" and type(Dt.Services) == "function" then
      for _, pair in ipairs({ { "bank", "bank" }, { "mailbox", "mail" }, { "auction", "ah" }, { "innkeeper", "inn" }, { "flight", "flight" }, { "repair", "repair" } }) do
        for _, e in ipairs(try(Dt.Services, pair[1]) or {}) do
          if type(e) == "table" and e.mapID == mapID and (pair[1] ~= "repair" or e.kind == "repair") and (pair[1] ~= "flight" or e.known ~= false) then
            add({ kind = pair[2], name = pair[2] == "mail" and KIND_FR.mail or (e.name or KIND_FR[pair[2]]), x = e.x, y = e.y, role = KIND_FR[pair[2]], why = {}, src = e.src })
          end
        end
      end
    end
    -- QuestieDB (si chargé) : tes maîtres de métier, les marchands des objets dont tu as besoin
    for line in pairs(ranks) do
      for _, c in ipairs(questieTrainers(line, mapID)) do
        add({ kind = "ptrainer", name = c.name, x = c.x, y = c.y, role = "Maître de " .. lineName(line):lower(), src = "Questie",
          why = { fmt("ton métier : %s %d", lineName(line), ranks[line]) }, icon = P and P.LineIcon and P.LineIcon(line) or nil })
      end
    end
    if P and P.QuestieVendors and p then
      for id, nd in pairs(need) do
        for _, c in ipairs(P.QuestieVendors({ id }, p) or {}) do
          add({ kind = (nd.cat == "ammo" or nd.cat == "petfood" or nd.cat == "food") and nd.cat or "vendor", name = c.name, x = c.x, y = c.y,
            role = KIND_FR.vendor, src = "Questie", why = { fmt("vend %s : %d pour %s", itemName(id), nd.n, nd.cat == "mats" and "ton plan" or "toi") } })
        end
      end
    end
  end
  -- Data : maîtres de vol de cette carte
  local Tr = T.Data and T.Data.Travel
  local fac = str((try(g("UnitFactionGroup"), "player")))
  for _, r in pairs(Tr and Tr.taxi or {}) do
    if type(r) == "table" and r[5] == mapID and tonumber(r[6]) and CR.FactionOK(r[8], fac) then
      add({ kind = "flight", name = r[1], x = r[6] / 100, y = r[7] / 100, role = KIND_FR.flight, why = { "pour repartir" }, src = "data" })
    end
  end
  -- un point d'arrêt couvre le PNJ dessous
  local out = {}
  for _, q in ipairs(list) do
    local hidden = false
    if q.kind ~= "stop" then
      for _, s in ipairs(list) do if s.kind == "stop" and math.abs(s.x - q.x) < 0.003 and math.abs(s.y - q.y) < 0.003 then hidden = true end end
    end
    if not hidden then out[#out + 1] = q end
  end
  pinCache.key, pinCache.list = key, out
  return out
end
function CR.PinTip(owner, pin)
  local tt = g("GameTooltip")
  if not (tt and pin) then return end
  try(tt.SetOwner, tt, owner, "ANCHOR_RIGHT")
  try(tt.AddLine, tt, cut(pin.name, 40), 1, 0.82, 0.2, true)
  try(tt.AddLine, tt, pin.role or "", 0.9, 0.9, 0.9, true)
  for i, l in ipairs(pin.why or {}) do if i <= 5 then try(tt.AddLine, tt, l, 0.75, 0.9, 0.75, true) end end
  local me = here()
  local d = me and dist(me, pin)
  if d then try(tt.AddLine, tt, (distText(d) or "") .. " · " .. minutes(CR.LegSec(me, pin) or 0), 0.62, 0.62, 0.66, true) end
  try(tt.AddLine, tt, CR.run and "Clic : c'est l'arrêt en cours (la boussole y va)" or "Clic : la boussole y va", 0.4, 0.8, 1, true)
  try(tt.Show, tt)
end
local function pinTipHide() local tt = g("GameTooltip") if tt then try(tt.Hide, tt) end end
--- clic sur un point : l'arrêt des courses correspondant devient l'arrêt en cours, sinon la boussole y va
function CR.PinClick(pin)
  if type(pin) ~= "table" then return false end
  local run = CR.run
  if run then
    local s = pin.stop
    if not s then for _, st in ipairs(run.stops) do if shownName(st.name) == pin.name then s = st end end end
    if s then CR.Pick("city", s) CR.Guide(s, true) return true end
  end
  local C = T.Compass
  if C and type(C.SetTarget) == "function" then
    pcall(C.SetTarget, C, { mapID = pin.mapID, x = pin.x, y = pin.y, label = cut(pin.name, 40), source = "city" })
    return true
  end
  return false
end
local function pinClick(self) local p = self and self.pin if p then if T.UserAction then T:UserAction(CR.PinClick, p) else CR.PinClick(p) end end end
local function stylePin(b, pin, size)
  b:SetSize(size, size)
  b.icon:SetTexture(pin.icon)
  if pin.kind == "stop" then
    b.num:SetText(tostring(pin.n or ""))
    b.num:Show()
    b.ring:SetVertexColor(1, 0.82, 0.2, 1)
    b.ring:Show()
  else
    b.num:Hide()
    b.ring:Hide()
  end
end
local function newPinButton(parent)
  local CF = g("CreateFrame")
  local b = CF("Button", nil, parent)
  b.ring = b:CreateTexture(nil, "BACKGROUND")
  b.ring:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
  b.ring:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.ring:SetSize(30, 30)
  b.ring:Hide()
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  b.num = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  b.num:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", 4, -2)
  b.num:Hide()
  try(b.RegisterForClicks, b, "LeftButtonUp")
  b:SetScript("OnEnter", function(self) CR.PinTip(self, self.pin) end)
  b:SetScript("OnLeave", pinTipHide)
  b:SetScript("OnClick", pinClick)
  b:Hide()
  return b
end
-- ------------------------------------------------------------------ carte du monde
local mview, mcanvas
local mapPins = {}
CR.mapPins = mapPins
CR.mapShown, CR.mapRedraws = 0, 0
local function wmParts()
  local wm = g("WorldMapFrame")
  if type(wm) ~= "table" then return nil end
  local sc = wm.ScrollContainer
  local child = type(sc) == "table" and sc.Child or nil
  if type(child) ~= "table" then return nil end
  return wm, sc, child
end
local function rectOf(f)
  local l, b = num(try(f.GetLeft, f)), num(try(f.GetBottom, f))
  local w, h = num(try(f.GetWidth, f)), num(try(f.GetHeight, f))
  if not (l and b and w and h) or w <= 0 or h <= 0 then return nil end
  local es = num(try(f.GetEffectiveScale, f)) or 1
  local up = g("UIParent")
  local us = up and num(try(up.GetEffectiveScale, up)) or 1
  local s = es / (us > 0 and us or 1)
  return l * s, b * s, w * s, h * s
end
local function ensureMapCanvas()
  if mview then return mview end
  local CF, up = g("CreateFrame"), g("UIParent")
  if not (CF and up) or inCombat() then return nil end
  mview = CF("Frame", nil, up)                 -- NOTRE cadre, enfant d'UIParent, jamais ancré sur la carte
  try(mview.EnableMouse, mview, false)
  try(mview.SetClipsChildren, mview, true)
  mview:Hide()
  mcanvas = CF("Frame", nil, mview)
  try(mcanvas.EnableMouse, mcanvas, false)
  CR.mview, CR.mcanvas = mview, mcanvas
  return mview
end
function CR.ClearMap()
  for _, b in ipairs(mapPins) do if b:IsShown() then b:Hide() end b.pin = nil end
  CR.mapShown = 0
end
function CR.DrawMap()
  local wm, sc, child = wmParts()
  if not (wm and ensureMapCanvas()) then return end
  if not (CR.PinsOn() and try(wm.IsShown, wm)) then CR.ClearMap() if mview:IsShown() then mview:Hide() end return end
  local mapID = num(try(wm.GetMapID, wm))
  local vl, vb, vw, vh = rectOf(sc)
  local cl, cb, cw, ch = rectOf(child)
  if not (mapID and vl and cl) then CR.ClearMap() return end
  CR.mapRedraws = CR.mapRedraws + 1
  mview:ClearAllPoints()
  mview:SetPoint("BOTTOMLEFT", g("UIParent"), "BOTTOMLEFT", vl, vb)
  mview:SetSize(vw, vh)
  mcanvas:ClearAllPoints()
  mcanvas:SetPoint("BOTTOMLEFT", g("UIParent"), "BOTTOMLEFT", cl, cb)
  mcanvas:SetSize(cw, ch)
  local list = CR.PinList(mapID)
  local used = 0
  for _, pin in ipairs(list) do
    used = used + 1
    local b = mapPins[used]
    if not b then b = newPinButton(mcanvas) mapPins[used] = b end
    stylePin(b, pin, pin.kind == "stop" and 20 or 16)
    b:ClearAllPoints()
    b:SetPoint("CENTER", mcanvas, "TOPLEFT", pin.x * cw, -pin.y * ch)
    b.pin = pin
    b:Show()
  end
  for i = used + 1, #mapPins do local b = mapPins[i] if b:IsShown() then b:Hide() end b.pin = nil end
  CR.mapShown = used
  try(mview.SetFrameStrata, mview, try(wm.GetFrameStrata, wm) or "HIGH")
  try(mview.SetFrameLevel, mview, min(9000, (num(try(wm.GetFrameLevel, wm)) or 1) + 160))
  if used > 0 then if not mview:IsShown() then mview:Show() end elseif mview:IsShown() then mview:Hide() end
end
-- les événements de la carte arrivent par rafales : un drapeau, une passe à l'image suivante, puis plus rien
local mapDirty = false
local function mapOnUpdate()
  if not mapDirty then mview:SetScript("OnUpdate", nil) return end
  mapDirty = false
  pcall(CR.DrawMap)
end
function CR.MapDirty()
  mapDirty = true
  if mview then mview:SetScript("OnUpdate", mapOnUpdate) end
end
function CR.MapHidden()
  mapDirty = false
  if mview then mview:SetScript("OnUpdate", nil) if mview:IsShown() then mview:Hide() end end
end
function CR.AttachMap()
  if CR._mapHooked then return true end
  local wm = wmParts()
  if not wm then return false end
  ensureMapCanvas()
  local mk, mixin = g("CreateFromMixins"), g("MapCanvasDataProviderMixin")
  if type(wm.AddDataProvider) == "function" and type(mk) == "function" and type(mixin) == "table" then
    local pr = try(mk, mixin)
    if type(pr) == "table" then
      local function dirty() CR.MapDirty() end
      pr.RefreshAllData, pr.OnMapChanged, pr.OnCanvasScaleChanged, pr.OnCanvasSizeChanged, pr.OnCanvasPanChanged = dirty, dirty, dirty, dirty, dirty
      pr.RemoveAllData = function() CR.ClearMap() end
      pr.OnShow = function() pcall(CR.DrawMap) end
      pr.OnHide = function() CR.MapHidden() end
      if pcall(wm.AddDataProvider, wm, pr) then CR._mapHooked, CR.mapEngine, CR.provider = true, "provider", pr return true end
    end
  end
  if type(wm.HookScript) == "function" then
    pcall(wm.HookScript, wm, "OnShow", function() if CR._active then pcall(CR.DrawMap) end end)
    pcall(wm.HookScript, wm, "OnHide", function() CR.MapHidden() end)
    CR._mapHooked, CR.mapEngine = true, "hook"
    return true
  end
  return false
end
-- ------------------------------------------------------------------ minimap
local miniPins = {}
CR.miniPins = miniPins
CR.miniShown, CR.hbdShown = 0, {}
local function MPins() local m = T.MinimapPins return type(m) == "table" and m or nil end
local function hbd() local m = MPins() return m and type(m.HBD) == "function" and m.HBD() or nil end
local function miniPin(i)
  local b = miniPins[i]
  if b then return b end
  local mm = g("Minimap")
  if not (mm and g("CreateFrame")) then return nil end
  b = newPinButton(mm)                         -- enfant de Minimap (comme HereBeDragons) ; Minimap intacte
  try(b.SetFrameLevel, b, (num(try(mm.GetFrameLevel, mm)) or 1) + 6)
  miniPins[i] = b
  return b
end
function CR.ClearMini()
  local H = hbd()
  for b in pairs(CR.hbdShown) do if H then pcall(H.RemoveMinimapIcon, H, CR, b) end end
  CR.hbdShown = {}
  for _, b in ipairs(miniPins) do if b:IsShown() then b:Hide() end b.pin = nil end
  CR.miniShown = 0
end
function CR.DrawMini()
  local mm = g("Minimap")
  local me = here()
  if not (mm and me and CR.PinsOn() and CR.InTown()) then if CR.miniShown > 0 or next(CR.hbdShown) then CR.ClearMini() end return false end
  local list = CR.PinList(me.mapID)
  local H = hbd()
  if H then
    local want = {}
    for i, pin in ipairs(list) do
      local b = miniPin(i)
      if not b then break end
      stylePin(b, pin, pin.kind == "stop" and 14 or 12)
      b.pin = pin
      if pcall(H.AddMinimapIconMap, H, CR, b, pin.mapID, pin.x, pin.y, false, false) then want[b] = true end
    end
    for b in pairs(CR.hbdShown) do if not want[b] then pcall(H.RemoveMinimapIcon, H, CR, b) b:Hide() b.pin = nil end end
    CR.hbdShown, CR.miniEngine = want, "hbd"
    CR.miniShown = #list
    return false
  end
  CR.miniEngine = "own"
  local Mp = MPins()
  local view = Mp and Mp.View and Mp.View()
  if not (view and Mp.Offset) then return false end
  local used, shown = 0, 0
  local Gm = geo()
  for _, pin in ipairs(list) do
    local d, bear = nil, nil
    if Gm and Gm.Vector then d, bear = Gm.Vector(me, pin) end
    if d and bear then
      local px, py, _, edge = Mp.Offset(d * math.cos(bear), d * math.sin(bear), view.facing, view.rotate, view.yr, view.pr, view.shape, 6)
      used = used + 1
      local b = miniPin(used)
      if not b then break end
      if b.pin ~= pin then stylePin(b, pin, pin.kind == "stop" and 14 or 12) end
      b.pin = pin
      b:ClearAllPoints()
      b:SetPoint("CENTER", mm, "CENTER", px, py)
      if edge then b:Hide() else b:Show() shown = shown + 1 end
    end
  end
  for i = used + 1, #miniPins do local b = miniPins[i] if b:IsShown() then b:Hide() end b.pin = nil end
  CR.miniShown = shown
  return used > 0
end
-- en marchant seulement : une passe toutes les 0,4 s, arrêtée dès que tu ne bouges plus
local miniTicker
function CR.MiniSleep() if miniTicker then miniTicker:Cancel() miniTicker = nil end end
function CR.MiniWake()
  if miniTicker or not CR._active or not CR.PinsOn() then return end
  local live = CR.DrawMini()
  if live and CR.miniEngine == "own" and T.NewTicker then
    miniTicker = T:NewTicker(0.4, function()
      local more = CR.DrawMini()
      if not more or not (T.Moving and T:Moving(1)) then CR.MiniSleep() end
    end, CR)
  end
end
function CR.PinsDirty()
  CR.pinGen = (CR.pinGen or 0) + 1
  pinCache.key = nil
  if not CR._active then return end
  local wm = wmParts()
  if wm and try(wm.IsShown, wm) then CR.MapDirty() end
  pcall(CR.DrawMini)
end
--- bouton « Points métiers sur la carte : oui / non » (page Métiers)
function CR.PinsButton()
  local on = CR.PinsOn()
  return { "Points métiers sur la carte : " .. (on and "oui" or "non"), function()
    CR.db.pins = not on
    if CR.db.pins then CR.PinsDirty() else CR.ClearMap() CR.ClearMini() CR.MapHidden() end
  end, "Maîtres de tes métiers, marchands utiles à ton plan, banque, HV, boîte aux lettres… (carte + minimap, en ville)" }
end
function CR:OnOptionChanged(key)
  if key == "pins" then if CR.PinsOn() then CR.PinsDirty() else CR.ClearMap() CR.ClearMini() CR.MapHidden() end end
end

-- ===================================================================== événements
local function visit(kind)
  local run = CR.run
  if not run then return end
  run.visited[kind] = true
  CR.Soon(0.1)
end
CR.handlers = {
  MERCHANT_SHOW = function()
    CR.merchantOpen, CR.bought, CR.pendingBuy, CR.pendingSell = true, 0, {}, {}
    pcall(CR.LearnMerchant)
    if CR.run then CR.Soon(0.1) end
  end,
  MERCHANT_UPDATE = function() if CR.run and CR.merchantOpen then CR.Soon(0.2) end end,
  MERCHANT_CLOSED = function()
    local was = CR.merchantOpen
    CR.merchantOpen, CR.pendingBuy, CR.pendingSell = false, {}, {}
    if CR.run and was then
      CR.run.visited.sell = true
      local d = durability()
      if d and d >= 90 then CR.run.visited.repair = true end
      CR.Soon(0.1)
    end
  end,
  TRAINER_SHOW = function()
    CR.trainerOpen = true
    local e = CR.LearnTrainer()
    if CR.run and e then
      if e.k.ctrainer then CR.trainerKind = "ctrainer" elseif e.l then CR.trainerKind = "ptrainer:" .. tostring(e.l) end
      CR.Soon(0.2)
    end
  end,
  TRAINER_CLOSED = function()
    CR.trainerOpen = false
    if CR.run and CR.trainerKind then visit(CR.trainerKind) end
    CR.trainerKind = nil
  end,
  BANKFRAME_OPENED = function()
    CR.bankOpen, CR.pendingBank, CR.pendingOut = true, {}, {}
    pcall(CR.Remember, "bank", (try(g("UnitName"), "npc")))
    if CR.run then CR.Soon(0.2) end
  end,
  BANKFRAME_CLOSED = function()
    CR.bankOpen, CR.pendingBank, CR.pendingOut = false, {}, {}
    if CR.run then visit("bank") end
  end,
  MAIL_SHOW = function()
    CR.mailOpen = true
    pcall(CR.Remember, "mail", nil)
    if CR.run then CR.Soon(0.2) end
  end,
  MAIL_CLOSED = function()
    CR.mailOpen = false
    if CR.run then visit("mail") end
  end,
  AUCTION_HOUSE_SHOW = function()
    CR.ahOpen = true
    pcall(CR.Remember, "ah", (try(g("UnitName"), "npc")))
    if CR.run then CR.Soon(0.3) end
  end,
  AUCTION_HOUSE_CLOSED = function()
    CR.ahOpen = false
    if CR.run then visit("ah") end
  end,
  TAXIMAP_OPENED = function() pcall(CR.Remember, "flight", (try(g("UnitName"), "npc"))) end,
  GOSSIP_SHOW = function() pcall(CR.LearnGossip) end,
  CONFIRM_BINDER = function() pcall(CR.Remember, "inn", (try(g("UnitName"), "npc"))) if CR.run then visit("inn") end end,
  BAG_UPDATE_DELAYED = function()
    CR.pendingBuy, CR.pendingBank, CR.pendingSell, CR.pendingOut = {}, {}, {}, {}
    if CR.run then CR.Soon(0.2) end
  end,
  PLAYER_MONEY = function() if CR.run then CR.Soon(0.3) end end,
  PLAYER_REGEN_DISABLED = function() if CR.run then local P = PL() if P and P.HideCard then P.HideCard() end end end,
  PLAYER_REGEN_ENABLED = function() pcall(CR.AttachMap) if CR.run then CR.Soon(0.5) end end,
  ZONE_CHANGED_NEW_AREA = function()
    if CR.run then
      local p = here()
      if p and CR.run.map and p.mapID ~= CR.run.map and not CAPITALS[p.mapID] then CR.Stop("left") return end
    end
    T:After(3, function() pcall(CR.CheckPropose) pcall(CR.PinsDirty) end)
  end,
  PLAYER_UPDATE_RESTING = function() T:After(2, function() pcall(CR.CheckPropose) end) end,
  PLAYER_ENTERING_WORLD = function() T:After(4, function() pcall(CR.CheckPropose) end) end,
}
function CR:OnEnable()
  CR._active = true
  for ev, fn in pairs(CR.handlers) do self:RegisterEvent(ev, fn) end
  pcall(CR.AttachMap)
  if T.OnMotion then T:OnMotion(CR, CR.MiniWake) end
  self:On("FOCUS_CHANGED", function(_, _, kind) if kind == nil and not CR.run then T:After(1, function() pcall(CR.CheckPropose) end) end end)
  if T.RegisterSlash then
    T:RegisterSlash("courses", function(rest)
      rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
      if rest == "stop" or rest == "fin" then CR.Stop() return end
      if rest == "suivante" or rest == "+" then CR.StepMove(1) return end
      if rest == "precedente" or rest == "précédente" or rest == "-" then CR.StepMove(-1) return end
      if rest == "metiers" or rest == "métiers" then CR.ProfFocus() return end
      if rest == "page" then local W = T.Window if W and W.Open then W.Open("city") end return end
      if CR.run then CR.Stop() else CR.Start() end
    end, "courses [stop|suivante|precedente|metiers|page] : courses en ville (trajet optimal entre marchands, maîtres, banque…)")
  end
  T:After(2, function() pcall(CR.Restore) end)
end
function CR:OnDisable()
  CR._active = false
  if CR.run then CR.Stop("silent") end
  CR:Clear("propose")
  CR.MiniSleep()
  CR.ClearMini()
  CR.ClearMap()
  CR.MapHidden()
  if T.OnMotion then T:OnMotion(CR, nil) end
end
