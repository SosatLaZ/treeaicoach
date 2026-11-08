-- TreeAI — module Chasseur (wow-D, CLEAN 4.0) : LE propriétaire de tout ce qui touche au chasseur.
--  • Familier : humeur (et ses vrais % de dégâts), nourriture du sac adaptée à son régime, loyauté (texte du jeu),
--    points de dressage LIBRES (total − utilisés), techniques à jour pour son niveau (tables du client Forever) et
--    où chercher une bête qui les apprend (« Chercher → Familier »). Mort / absent : une ligne.
--  • Munitions : nombre, type (flèches / balles), carquois, combien de temps elles durent à TON rythme (mesuré par
--    Stats sur au moins 10 min), alerte quand il en manque ; l'achat se fait d'un clic sur la barre de combat.
--  • Aspects : en combat, mauvais aspect (Guépard / Meute = hébété) ou aucun ; avant un pull (cible hostile hors
--    combat) sans aspect de combat. Marque du chasseur avant un élite ou un rare.
--  • Pièges : petite barre « posé · recharge » (le déclenchement n'est pas lisible en combat sur Forever : on ne le
--    devine pas) ; Feindre la mort réussi (tu sors vraiment du combat) → « pose ton piège maintenant ».
--  • Barre de tir automatique (Maj+glisser) et avertissement quand Visée / Flèches multiples retardent le tir.
--  • Onglet « Chasseur » (sous « Plus ») : la fiche du familier et des munitions, en lignes courtes.
-- INFORMATIF UNIQUEMENT : aucune action automatique, aucun sort, aucune macro écrite (la nourriture du familier
-- passe par la macro « TAI Familier » du panneau Macros : Ctrl+clic = nourrir).
-- Contrat cœur : wow_addon/README.md. Instantané pour TreeAI Coach : summary.hunter (treeaicoach/wow/classes/hunter.py).
-- Retiré (ne marchait pas dans le vrai jeu) : zone morte (CheckInteractDistance interdit en combat), PV du familier
-- en combat (valeur secrète), « 2 ennemis sur toi » (journal de combat interdit sur Forever).

local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local TreeAI = _G.TreeAI
if type(TreeAI) ~= "table" then return end
local M = { name = "Hunter" }

---------------------------------------------------------------------------
-- Nourriture de familier : { itemID, régime, niveau d'objet (approx.) }
-- Régimes : meat, fish, bread, cheese, fruit, fungus. Tenu en phase avec hunter.py (test).
---------------------------------------------------------------------------
local FOODS = {
  -- Viande (vendeurs puis crue/cuisinée)
  {117, "meat", 1}, {2287, "meat", 5}, {3770, "meat", 15}, {3771, "meat", 25},
  {4599, "meat", 35}, {8952, "meat", 45},
  {769, "meat", 5}, {2672, "meat", 5}, {2673, "meat", 10}, {2677, "meat", 12},
  {729, "meat", 12}, {1015, "meat", 15}, {2886, "meat", 15}, {5465, "meat", 10},
  {1081, "meat", 18}, {2924, "meat", 20}, {3173, "meat", 20}, {3731, "meat", 25},
  {3712, "meat", 30}, {3730, "meat", 30}, {12037, "meat", 35}, {12184, "meat", 40},
  {12203, "meat", 35}, {12205, "meat", 42}, {12208, "meat", 42},
  {2681, "meat", 5}, {2680, "meat", 10}, {724, "meat", 12}, {2888, "meat", 15},
  {3220, "meat", 20}, {3662, "meat", 22}, {3726, "meat", 30}, {3727, "meat", 30},
  {12209, "meat", 37}, {12210, "meat", 42},
  -- Poisson
  {787, "fish", 1}, {4592, "fish", 5}, {4593, "fish", 15}, {4594, "fish", 25},
  {21552, "fish", 35}, {8957, "fish", 45},
  {6291, "fish", 5}, {6303, "fish", 5}, {6289, "fish", 10}, {6308, "fish", 20},
  {6290, "fish", 5}, {4603, "fish", 35}, {5095, "fish", 25}, {6316, "fish", 15},
  {13930, "fish", 45}, {13933, "fish", 55},
  -- Pain
  {4540, "bread", 1}, {4541, "bread", 5}, {4542, "bread", 15}, {4544, "bread", 25},
  {4601, "bread", 35}, {8950, "bread", 45},
  -- Fromage
  {2070, "cheese", 1}, {414, "cheese", 5}, {422, "cheese", 15}, {1707, "cheese", 25},
  {3927, "cheese", 35}, {8932, "cheese", 45},
  -- Fruit
  {4536, "fruit", 1}, {4537, "fruit", 5}, {4538, "fruit", 15}, {4539, "fruit", 25},
  {4602, "fruit", 35}, {8953, "fruit", 45},
  -- Champignon
  {4604, "fungus", 1}, {4605, "fungus", 5}, {4606, "fungus", 15}, {4607, "fungus", 25},
  {4608, "fungus", 35}, {8948, "fungus", 45},
}
local FOOD_BY_ID = {}
for _, f in ipairs(FOODS) do FOOD_BY_ID[f[1]] = f end
-- Complément : table générée depuis les données du client (Data/PetFood.lua), si chargée.
do
  local extra = type(TreeAI.Data) == "table" and TreeAI.Data.PetFood
  if type(extra) == "table" then
    for _, f in ipairs(extra) do
      if type(f) == "table" and type(f[1]) == "number" and not FOOD_BY_ID[f[1]] then FOOD_BY_ID[f[1]] = f end
    end
  end
end
M.FOODS = FOODS

-- Noms de régime renvoyés par GetPetFoodTypes() (enUS / frFR) -> clé.
local DIET_KEYS = {
  meat = "meat", viande = "meat", fish = "fish", poisson = "fish",
  bread = "bread", pain = "bread", cheese = "cheese", fromage = "cheese",
  fruit = "fruit", fruits = "fruit", fungus = "fungus", champignon = "fungus",
  champignons = "fungus",
}
local DIET_FR = { meat = "viande", fish = "poisson", bread = "pain", cheese = "fromage",
  fruit = "fruit", fungus = "champignon" }

-- Sorts (ID de rang 1 : le nom localisé couvre tous les rangs)
local S = {
  AUTO = 75, FEED = 6991, FEED_EFFECT = 1539,
  MEND = 136, REVIVE = 982, CALL = 883, MARK = 1130, FEIGN = 5384,
  FREEZING = 1499, AIMED = 19434, MULTI = 2643,
  HAWK = 13165, MONKEY = 13163, CHEETAH = 5118, PACK = 13159, BEAST = 13161, WILD = 20043,
}
-- pièges (rang 1 et suivants) : givrant, immolation, givre, explosif — tous partagent une recharge de 15 s
local TRAPS = { 1499, 14310, 14311, 13795, 14302, 14303, 14304, 14305, 13809, 13813, 14316, 14317 }
local TRAP_FB = { ["Piège givrant"] = true, ["Piège d'immolation"] = true, ["Piège d'Immolation"] = true,
  ["Piège de givre"] = true, ["Piège explosif"] = true }
local MARK_IDS = { [1130] = true, [14323] = true, [14324] = true, [14325] = true }
local COMBAT_ASPECTS = { S.HAWK, S.MONKEY, S.WILD, S.BEAST }
local TRAVEL_ASPECTS = { S.CHEETAH, S.PACK }

---------------------------------------------------------------------------
-- Utilitaires défensifs
---------------------------------------------------------------------------
local function now() return (GetTime and GetTime()) or 0 end
-- Wrappers défensifs du cœur (Forever = famille Mainline : globales classiques possiblement absentes).
local function API() return type(TreeAI.API) == "table" and TreeAI.API or nil end
-- Forever 1.60.1 : les API du familier sont dans C_PetInfo (globales retirées) ; valeurs secrètes en combat.
local PetInfo = _G.C_PetInfo or {}
local GetPetHappiness = PetInfo.GetPetHappiness or _G.GetPetHappiness
local GetPetLoyalty = PetInfo.GetPetLoyalty or _G.GetPetLoyalty
local GetPetTrainingPoints = PetInfo.GetPetTrainingPoints or _G.GetPetTrainingPoints
local GetPetFoodTypes = PetInfo.GetPetFoodTypes or _G.GetPetFoodTypes
local function pub(v) local A = API() if A and A.Pub then return A.Pub(v) end return v end

local spellNameMemo, spellNameMiss = {}, {}   -- (LITE) un nom de sort ne change pas : lu une fois (absent : relu après 60 s)
local spellNameRaw
local function spellName(id)
  local c = spellNameMemo[id]
  if c then return c end
  local t = (GetTime and GetTime()) or 0
  if spellNameMiss[id] and t - spellNameMiss[id] < 60 then return nil end
  c = spellNameRaw(id)
  if c then spellNameMemo[id] = c else spellNameMiss[id] = t end
  return c
end
spellNameRaw = function(id)
  local A = API()
  if A and A.SpellName then
    local ok, n = pcall(A.SpellName, id)
    if ok and n then return n end
  end
  if C_Spell and C_Spell.GetSpellInfo then
    local ok, info = pcall(C_Spell.GetSpellInfo, id)
    if ok and type(info) == "table" and info.name then return info.name end
  end
  if GetSpellInfo then
    local n = GetSpellInfo(id)
    if n then return n end
  end
  return nil
end

local knowsRaw
local knownMemo = {}   -- (CLEAN) id -> { v, at } : le grimoire change rarement ; relu après 30 s
local function knows(id)
  local t = now()
  local c = knownMemo[id]
  if c and t - c.at < 30 then return c.v end
  local v = knowsRaw(id) and true or false
  knownMemo[id] = { v = v, at = t }
  return v
end
knowsRaw = function(id)
  local A = API()
  if A and A.IsSpellKnown then
    local ok, r = pcall(A.IsSpellKnown, id)
    if ok and r then return true end
  end
  if IsPlayerSpell then
    local ok, r = pcall(IsPlayerSpell, id)
    if ok and r then return true end
  end
  if IsSpellKnown then
    local ok, r = pcall(IsSpellKnown, id)
    if ok and r then return true end
  end
  -- Repli Classic : GetSpellInfo(nom) ne renvoie rien si le sort n'est pas dans le grimoire.
  local n = spellName(id)
  if n and GetSpellInfo and not IsPlayerSpell and not IsSpellKnown then
    return GetSpellInfo(n) ~= nil
  end
  return false
end
M.knows = knows

-- Renvoie name, spellId, source, expiration de la i-ème aura.
local function auraAt(unit, i, filter)
  if C_UnitAuras and C_UnitAuras.GetAuraDataByIndex then
    local ok, d = pcall(C_UnitAuras.GetAuraDataByIndex, unit, i, filter)
    if not ok or not d then return nil end
    return pub(d.name) or "?", pub(d.spellId), pub(d.sourceUnit), pub(d.expirationTime)
  end
  if UnitAura then
    local name, _, _, _, _, exp, source, _, _, sid = UnitAura(unit, i, filter)
    return name, sid, source, exp
  end
  return nil
end

-- names : set de noms localisés ; ids : set d'IDs. mine : ne garder que nos auras.
-- -> true, nom | false (absente) | nil (inconnu : des auras sont masquées par le jeu, elle est peut-être là)
local function hasAura(unit, filter, names, ids, mine)
  local A = API()
  if A and A.Auras then
    local ok, list, hidden = pcall(A.Auras, unit, filter)
    if ok and type(list) == "table" then
      for _, a in ipairs(list) do
        if (names and a.name and names[a.name]) or (ids and a.spellId and ids[a.spellId]) then
          if not mine or a.source == "player" or a.source == "pet" then return true, a.name end
        end
      end
      if (tonumber(hidden) or 0) > 0 then return nil end
      return false
    end
  end
  for i = 1, 40 do
    local name, sid, source = auraAt(unit, i, filter)
    if not name then return false end
    if (names and names[name]) or (ids and sid and ids[sid]) then
      if not mine or source == "player" or source == "pet" then return true, name end
    end
  end
  return false
end

local function nameSet(list)
  local t = {}
  for _, id in ipairs(list) do
    local n = spellName(id)
    if n then t[n] = id end
  end
  return t
end

local itemInstantRaw
local instantMemo = {}   -- (CLEAN) classe / sous-classe d'un objet : ne changent jamais
local function itemInstant(id)
  if not id then return nil end
  local c = instantMemo[id]
  if c then return c[1], c[2] end
  local a, b = itemInstantRaw(id)
  if a then instantMemo[id] = { a, b } end
  return a, b
end
itemInstantRaw = function(id)
  local A = API()
  if A and A.ItemInfo then
    local ok, c, sub = pcall(function() return select(12, A.ItemInfo(id)) end)
    if ok and c then return c, sub end
  end
  if C_Item and C_Item.GetItemInfoInstant then
    local ok, _, _, _, _, _, classID, subID = pcall(C_Item.GetItemInfoInstant, id)
    if ok then return classID, subID end
  end
  if GetItemInfoInstant then
    local _, _, _, _, _, classID, subID = GetItemInfoInstant(id)
    return classID, subID
  end
  return nil
end

local function itemCount(id)
  local A = API()
  if A and A.ItemCount then
    local ok, n = pcall(A.ItemCount, id)
    if ok and n then return n end
  end
  if C_Item and C_Item.GetItemCount then
    local ok, n = pcall(C_Item.GetItemCount, id)
    if ok and n then return n end
  end
  return (GetItemCount and GetItemCount(id)) or 0
end

local function numSlots(bag)
  if C_Container and C_Container.GetContainerNumSlots then return C_Container.GetContainerNumSlots(bag) or 0 end
  return (GetContainerNumSlots and GetContainerNumSlots(bag)) or 0
end

local function slotItemID(bag, slot)
  if C_Container and C_Container.GetContainerItemID then return C_Container.GetContainerItemID(bag, slot) end
  if GetContainerItemID then return GetContainerItemID(bag, slot) end
  if GetContainerItemLink then
    local link = GetContainerItemLink(bag, slot)
    if link then return tonumber(link:match("item:(%d+)")) end
  end
  return nil
end

-- (LITE) liste gardée jusqu'au prochain changement de sacs (BAG_UPDATE_DELAYED…) ou 30 s
local bagCache, bagCacheAt = nil, -100
local function bagItemIDsRaw()
  local seen, out = {}, {}
  -- (LITE) l'id seul suffit : GetContainerItemID (pas de table par case) quand le jeu l'a
  if (C_Container and C_Container.GetContainerItemID) or GetContainerItemID then
    local ok = pcall(function()
      for bag = 0, 4 do
        for slot = 1, numSlots(bag) do
          local id = slotItemID(bag, slot)
          if id and not seen[id] then seen[id] = true; out[#out + 1] = id end
        end
      end
    end)
    if ok then return out end
    seen, out = {}, {}
  end
  local A = API()
  if A and A.IterBags then
    local ok = pcall(function()
      for _, _, item in A.IterBags() do
        local id = type(item) == "table" and item.itemID
        if id and not seen[id] then seen[id] = true; out[#out + 1] = id end
      end
    end)
    if ok then return out end
    seen, out = {}, {}
  end
  for bag = 0, 4 do
    for slot = 1, numSlots(bag) do
      local id = slotItemID(bag, slot)
      if id and not seen[id] then seen[id] = true; out[#out + 1] = id end
    end
  end
  return out
end
local function bagItemIDs()
  local t = (GetTime and GetTime()) or 0
  if not bagCache or t - bagCacheAt > 30 then bagCache, bagCacheAt = bagItemIDsRaw(), t end
  return bagCache
end

local function inCombat()
  if type(TreeAI.InCombat) == "function" then
    local ok, r = pcall(TreeAI.InCombat, TreeAI)
    if ok and r ~= nil then return r and true or false end
  end
  if InCombatLockdown and InCombatLockdown() then return true end
  return (UnitAffectingCombat and UnitAffectingCombat("player")) and true or false
end

-- Réglages (compte) : M.db fourni par le cœur (defaults fusionnés), sinon repli TreeAIDB.modules.Hunter.
local DEFAULTS = { enabled = true, bar = true, trapBar = true, ammoWarn = 200, tab = true }
local function settings()
  local d = type(M.db) == "table" and M.db or nil
  if not d then
    local root = _G.TreeAIDB
    if type(root) ~= "table" then return nil end
    root.modules = type(root.modules) == "table" and root.modules or {}
    d = type(root.modules.Hunter) == "table" and root.modules.Hunter or {}
    root.modules.Hunter = d
  end
  for k, v in pairs(DEFAULTS) do if d[k] == nil then d[k] = v end end
  return d
end

local function opt(k)
  local d = settings()
  if d then return d[k] end
  return DEFAULTS[k]
end

---------------------------------------------------------------------------
-- Suggestions (anti-spam par id)
---------------------------------------------------------------------------
local lastSaid = {}
local function suggest(id, text, prio, every, extra)
  if not opt("enabled") then return end
  local t = now()
  -- même texte redit trop tôt : rien (le cœur garde la ligne) ; texte changé (120 → 30 flèches) : mis à jour tout de suite
  local last = lastSaid[id]
  if last and last.text == text and t - last.at < (every or 30) then return end
  lastSaid[id] = { text = text, at = t }
  local o = { cooldown = every or 30, ttl = math.max(every or 30, 20), module = "Hunter" }
  if type(extra) == "table" then for k, v in pairs(extra) do o[k] = v end end
  if type(M.Suggest) == "function" then
    pcall(M.Suggest, M, id, text, prio or 1, o)          -- id préfixé "Hunter:" par le cœur
  elseif type(TreeAI.Suggest) == "function" then
    pcall(TreeAI.Suggest, TreeAI, "Hunter:" .. id, text, prio or 1, o)
  end
end
local function clear(id)
  lastSaid[id] = nil
  if type(M.Clear) == "function" then
    pcall(M.Clear, M, id)
  elseif type(TreeAI.ClearSuggestion) == "function" then
    pcall(TreeAI.ClearSuggestion, TreeAI, "Hunter:" .. id)
  end
end

local function fmtNum(n) local F = TreeAI.Fmt return (F and F.Num) and F.Num(n) or tostring(math.floor((tonumber(n) or 0) + 0.5)) end
local function fmtTime(sec) local F = TreeAI.Fmt return (F and F.Time) and F.Time(sec) or (math.floor((sec or 0) / 60) .. " min") end

---------------------------------------------------------------------------
-- Familier : régime, meilleure nourriture
---------------------------------------------------------------------------
function M:GetDiet()
  local diet = {}
  if not GetPetFoodTypes then return diet end
  local types = { GetPetFoodTypes() }
  if type(types[1]) == "table" then types = types[1] end   -- C_PetInfo.GetPetFoodTypes() rend une table
  for _, t in ipairs(types) do
    local key = DIET_KEYS[tostring(t):lower()]
    if key then diet[key] = true end
  end
  return diet
end

-- Pure : choisit la nourriture. bag = liste d'itemIDs, diet = set, petLevel = nombre.
-- Préfère la moins chère qui donne l'effet plein (>= niv-10), sinon la plus haute mangeable (>= niv-30).
function M.PickFood(bag, diet, petLevel)
  local full, ok
  for _, id in ipairs(bag) do
    local f = FOOD_BY_ID[id]
    if f and diet[f[2]] then
      local lvl = f[3]
      if lvl >= petLevel - 10 then
        if not full or lvl < full[3] then full = f end
      elseif lvl >= petLevel - 30 then
        if not ok or lvl > ok[3] then ok = f end
      end
    end
  end
  local best = full or ok
  return best and best[1] or nil, best and (full and "full" or "partial") or nil
end

local foodMemo = { at = -100 }
function M:BestFood()
  if not (UnitExists and UnitExists("pet")) then return nil end
  local lvl = (UnitLevel and UnitLevel("pet")) or 1
  -- (CLEAN) mémo 20 s : la ligne « Nourris ton familier » et la barre de combat la demandent souvent
  local t = now()
  if foodMemo.lvl == lvl and t - foodMemo.at < 20 then return foodMemo.a, foodMemo.b end
  local a, b = M.PickFood(bagItemIDs(), self:GetDiet(), lvl)
  foodMemo.lvl, foodMemo.at, foodMemo.a, foodMemo.b = lvl, t, a, b
  return a, b
end
--- même choix pour la macro « TAI Familier » (Macros) et la barre de combat : un seul propriétaire
function M:GetBestPetFood() return (self:BestFood()) end

local function dietText(diet)
  local t = {}
  for k in pairs(diet) do t[#t + 1] = DIET_FR[k] or k end
  table.sort(t)
  return #t > 0 and table.concat(t, ", ") or "?"
end

local function itemName(id)
  local n
  if C_Item and C_Item.GetItemNameByID then n = C_Item.GetItemNameByID(id) end
  if not n and GetItemInfo then n = GetItemInfo(id) end
  return n or ("objet " .. id)
end

local MOOD = { [1] = "mécontent", [2] = "content", [3] = "heureux" }
local MOOD_DMG = { [1] = 75, [2] = 100, [3] = 125 }
--- humeur lue au jeu : niveau (1-3), % de dégâts (2e valeur du jeu, sinon la règle 75/100/125), loyauté qui baisse
function M.Mood()
  if not GetPetHappiness then return nil end
  local h, dmg, rate = GetPetHappiness()
  h, dmg, rate = tonumber(_ns(h)), tonumber(_ns(dmg)), tonumber(_ns(rate))
  if not h or not MOOD[h] then return nil end
  if not dmg or dmg <= 0 then dmg = MOOD_DMG[h] end
  return h, dmg, rate
end

function M:CheckPet()
  if not (UnitExists and UnitExists("pet")) then
    if knows(S.CALL) and not inCombat() and not (IsMounted and IsMounted())
        and not (UnitOnTaxi and UnitOnTaxi("player")) and not (UnitIsDeadOrGhost and UnitIsDeadOrGhost("player")) then
      suggest("nopet", "Appelle ton familier : « " .. (spellName(S.CALL) or "Appel du familier") .. " »", 1, 300)
    end
    return
  end
  clear("nopet")
  if UnitIsDead and UnitIsDead("pet") then
    suggest("petdead", "Ressuscite ton familier : « " .. (spellName(S.REVIVE) or "Ressusciter le familier") .. " » hors combat", 3, 60)
    return
  end
  clear("petdead")
  local happy, dmg, loyaltyRate = M.Mood()
  if not happy then return end
  local feeding = hasAura("pet", "HELPFUL", nameSet({ S.FEED_EFFECT }), { [S.FEED_EFFECT] = true })
  if feeding or happy >= 3 then clear("hungry"); return end
  if inCombat() then return end   -- on ne nourrit pas en combat : la ligne attend la fin du combat
  -- une seule ligne « Nourris ton familier » : verbe d'abord, nourriture en clair ; le pourquoi dans l'infobulle
  local food, quality = self:BestFood()
  local what = food and (itemName(food) .. (quality == "partial" and " (un peu faible pour son niveau)" or ""))
  local tip = { ("Humeur : %s, %d %% de ses dégâts (heureux = 125 %%)."):format(MOOD[happy], dmg),
    "Macro « TAI Familier » (panneau Macros) : Ctrl+clic = nourrir avec la bonne nourriture du sac." }
  if not food then tip[#tip + 1] = "Rien de mangeable pour lui dans tes sacs (régime : " .. dietText(self:GetDiet()) .. ")." end
  if happy == 1 or (loyaltyRate and loyaltyRate < 0) then
    tip[#tip + 1] = "Mécontent, il perd de la loyauté : à la longue il peut s'enfuir."
    suggest("hungry", "Nourris ton familier mécontent : " .. (what or "achète de quoi le nourrir"), 3, 45, { tooltip = tip })
  else
    suggest("hungry", "Nourris ton familier (heureux = +25 % de dégâts) : " .. (what or "achète de quoi le nourrir"), 1, 240,
      { tooltip = tip })
  end
end

local function freeTrainingPoints()
  local A = API()
  local free = A and A.PetFreeTrainingPoints and A.PetFreeTrainingPoints()   -- nil = lecture pas fiable
  if free then return free end
  if not GetPetTrainingPoints then return nil end
  local total, used = GetPetTrainingPoints()
  total, used = tonumber(_ns(total)), tonumber(_ns(used))
  -- libres = total − utilisés ; « 0 utilisé » = le total brut (un familier a toujours des techniques) : on se tait
  if total and used and used > 0 and used <= total then return total - used end
  return nil
end
M.FreeTrainingPoints = freeTrainingPoints

function M:CheckTraining()
  if inCombat() or not (UnitExists and UnitExists("pet")) then return end
  local free = freeTrainingPoints()
  if free and free > 0 then
    suggest("tp", ("Dépense tes %d points de dressage : « Dressage des bêtes »"):format(free), 1, 900)
  else
    clear("tp")
  end
end

--- techniques de la famille du familier qui ont un rang de plus à son niveau (tables du client Forever,
--- Data/Pets.lua) -> { {name, rank, have, level} } ; nil si le grimoire du familier ne se lit pas (on se tait)
local abilityRaw
local abilityMemo = { at = -100 }
--- techniques du familier à apprendre (mémo 60 s : le grimoire du familier ne change qu'au dressage)
function M.AbilityUpgrades()
  local t = now()
  if t - abilityMemo.at < 60 then return abilityMemo.a, abilityMemo.b end
  local a, b = abilityRaw()
  abilityMemo.at, abilityMemo.a, abilityMemo.b = t, a, b
  return a, b
end
abilityRaw = function()
  local D = TreeAI.Data and TreeAI.Data.Pets
  local F = TreeAI.Finder
  if not (D and F and F.PetKnown and F.FamilyId and UnitExists and UnitExists("pet")) then return nil end
  local known = F.PetKnown()
  if not next(known) then return nil end                    -- grimoire illisible : jamais « rang 0 »
  local fam = D.families and D.families[F.FamilyId(_ns(UnitCreatureFamily and UnitCreatureFamily("pet")))]
  if not fam then return nil end
  local lvl = tonumber(UnitLevel and UnitLevel("pet")) or 1
  local out = {}
  for _, name in ipairs(fam.learn or {}) do
    local a = D.abilities and D.abilities[name]
    local best, at = 0, nil
    for r, l in ipairs(a and a.lv or {}) do if l > 0 and l <= lvl then best, at = r, l end end
    local have = known[name] or 0
    if best > have then out[#out + 1] = { name = name, rank = best, have = have, level = at } end
  end
  return out, fam.n
end

---------------------------------------------------------------------------
-- Munitions, carquois
---------------------------------------------------------------------------
-- Renvoie infos munitions : equipped (nombre), kind ("arrow"/"bullet"/nil), total compatible, mismatch, quiver
local ammoRaw
local ammoMemo = { at = -100 }
--- (CLEAN) une lecture partagée par passage (Chasseur + barre de combat la demandent dans la même seconde)
function M:AmmoInfo()
  local t = now()
  if ammoMemo.info and t - ammoMemo.at < 1.5 then return ammoMemo.info end
  local info = ammoRaw()
  ammoMemo.info, ammoMemo.at = info.kind and info or nil, t   -- sans arme à distance lue : pas gardé (arc équipé à l'instant)
  return info
end
ammoRaw = function()
  local info = { equipped = 0, total = 0, kind = nil, mismatch = false, quiver = false, needs = false }
  local ranged = GetInventoryItemID and GetInventoryItemID("player", 18)
  local cls, sub = itemInstant(ranged)
  if cls == 2 and (sub == 2 or sub == 18) then info.kind = "arrow"
  elseif cls == 2 and sub == 3 then info.kind = "bullet" end
  info.needs = info.kind ~= nil
  local ammoID = GetInventoryItemID and GetInventoryItemID("player", 0)
  if ammoID then
    info.equipped = (GetInventoryItemCount and GetInventoryItemCount("player", 0)) or itemCount(ammoID)
    local _, asub = itemInstant(ammoID)
    if info.kind and asub and ((info.kind == "arrow" and asub ~= 2) or (info.kind == "bullet" and asub ~= 3)) then
      info.mismatch = true
    end
  end
  local want = info.kind == "arrow" and 2 or (info.kind == "bullet" and 3) or nil
  if want then
    for _, id in ipairs(bagItemIDs()) do
      local c, s = itemInstant(id)
      if c == 6 and s == want then info.total = info.total + itemCount(id) end
    end
  end
  local toInv = (C_Container and C_Container.ContainerIDToInventoryID) or ContainerIDToInventoryID
  for bag = 1, 4 do
    local inv = toInv and toInv(bag)
    local bid = inv and GetInventoryItemID and GetInventoryItemID("player", inv)
    if bid and itemInstant(bid) == 11 then info.quiver = true end
  end
  return info
end

--- munitions tirées par heure de jeu, mesurées par Stats (au moins 10 min) -> par heure, minutes mesurées ; nil sinon
function M.AmmoRate()
  local St = TreeAI.Stats
  if not (St and St.PerHour) then return nil end
  local ok, ph = pcall(St.PerHour, St, "ammo")
  ph = ok and tonumber(ph) or nil
  if not ph or ph < 20 then return nil end
  local played = St.Get and tonumber((select(2, pcall(St.Get, St, "played")))) or nil
  return ph, played
end

function M:CheckAmmo()
  local a = self:AmmoInfo()
  M.lastAmmo = a
  if not a.needs then return end
  local word = a.kind == "arrow" and "flèches" or "balles"
  if a.mismatch then
    suggest("ammotype", "Change de munitions : ton arme tire des " .. word, 3, 30,
      { tooltip = "Macro « TAI Munitions » (panneau Macros) : un clic équipe les bonnes." })
  else
    clear("ammotype")
  end
  local rate = M.AmmoRate()
  local left = rate and a.total / rate * 3600 or nil
  local tip = rate and ("À ton rythme : %s %s par heure de jeu (mesuré par Stats)."):format(fmtNum(rate), word) or nil
  if a.equipped == 0 and a.total > 0 then
    suggest("ammo", "Équipe tes " .. word .. " : l'emplacement de munitions est vide", 3, 20,
      { tooltip = "Macro « TAI Munitions » (panneau Macros) : un clic les équipe." })
  elseif a.total == 0 and a.equipped == 0 then
    suggest("ammo", "Achète des " .. word .. " : tu n'en as plus", 3, 30,
      { tooltip = "Chez un marchand : la barre de combat propose l'achat d'un clic." })
  elseif a.total < 50 then
    suggest("ammo", ("Rachète des %s vite : plus que %d"):format(word, a.total), 3, 60, { tooltip = tip })
  elseif a.total < (tonumber(opt("ammoWarn")) or 200) or (left and left < 1800) then
    suggest("ammo", ("Munitions basses : %s %s%s"):format(fmtNum(a.total), word,
      left and (", ≈ " .. fmtTime(left) .. " de jeu") or ""), 2, 300, { tooltip = tip })
  else
    clear("ammo")
  end
end

---------------------------------------------------------------------------
-- Aspects, Marque du chasseur (en combat, et juste avant un pull)
---------------------------------------------------------------------------
local function hostileTarget()
  return UnitExists and UnitExists("target") and UnitCanAttack and UnitCanAttack("player", "target")
    and not (UnitIsDead and UnitIsDead("target")) and not (UnitIsPlayer and UnitIsPlayer("target"))
end

function M:CheckAspect()
  local anyKnown = false
  for _, id in ipairs(COMBAT_ASPECTS) do if knows(id) then anyKnown = true end end
  if not anyKnown then return end
  local combatOn = hasAura("player", "HELPFUL", nameSet(COMBAT_ASPECTS))
  local travelOn, travelName = hasAura("player", "HELPFUL", nameSet(TRAVEL_ASPECTS))
  local best = knows(S.HAWK) and spellName(S.HAWK) or spellName(S.MONKEY)
  if inCombat() then
    if travelOn then
      suggest("aspect", "Change d'aspect : « " .. travelName .. " » en combat = hébété à chaque coup", 3, 10)
    elseif combatOn == false and travelOn == false then   -- nil = auras masquées par le jeu : on ne sait pas, on se tait
      suggest("aspect", "Active « " .. (best or "Aspect du faucon") .. " » (Singe si ça tape au corps à corps)", 2, 25)
    else
      clear("aspect")
    end
  elseif combatOn == false and travelOn == false and hostileTarget() and not (UnitAffectingCombat and UnitAffectingCombat("target")) then
    suggest("aspect", "Active « " .. (best or "Aspect du faucon") .. " » avant le pull", 2, 60,
      { tooltip = "Macro « TAI Aspects » (panneau Macros) : clic = Faucon, Maj = Guépard pour voyager." })
  else
    clear("aspect")   -- hors combat en Guépard / Meute : normal, la ligne de combat s'efface
  end
end

local markedGUID
function M:CheckMark()
  if inCombat() or not knows(S.MARK) or not hostileTarget() then clear("mark") return end
  if UnitAffectingCombat and UnitAffectingCombat("target") then return end
  local guid = UnitGUID and pub(UnitGUID("target"))
  local cls = UnitClassification and pub(UnitClassification("target")) or "normal"
  local big = cls == "elite" or cls == "rareelite" or cls == "worldboss" or cls == "rare"
  if not big then clear("mark") return end
  if guid and guid == markedGUID then return end
  if hasAura("target", "HARMFUL", nameSet({ S.MARK }), MARK_IDS) then markedGUID = guid clear("mark") return end
  suggest("mark", "Pose « " .. (spellName(S.MARK) or "Marque du chasseur") .. " » avant le pull : " ..
    (cls == "rare" and "c'est un rare" or "c'est un élite"), 1, 15,
    { tooltip = "Macro « TAI Marque » (panneau Macros) : marque puis envoie le familier." })
end

---------------------------------------------------------------------------
-- Pièges : barre « posé · recharge » ; Feindre la mort réussi → piège
---------------------------------------------------------------------------
local trapNames
local function isTrap(id)
  if not trapNames then
    trapNames = {}
    for k in pairs(TRAP_FB) do trapNames[k] = true end
    for _, i in ipairs(TRAPS) do local n = spellName(i) if n then trapNames[n] = true end end
  end
  local n = id and spellName(id)
  return n and trapNames[n] and n or nil
end
M.IsTrap = isTrap

local trap        -- { name, placed, armedUntil, cdUntil, fight } (fight = un combat a commencé depuis)
local trapBar
local TRAP_LIFE, TRAP_CD = 60, 15   -- Classic : un piège reste 1 min ; recharge commune de 15 s

--- texte de la barre (pur) : jamais « déclenché » (illisible en combat sur Forever), seulement ce qu'on sait
function M.TrapText(tr, t)
  if not tr then return nil end
  local cd = tr.cdUntil and math.max(0, math.ceil(tr.cdUntil - t)) or 0
  local cdTxt = cd > 0 and ("recharge %d s"):format(cd) or "pièges prêts"
  if tr.fight then return "Pièges : " .. cdTxt end
  local left = math.max(0, math.ceil(tr.armedUntil - t))
  return ("%s posé : %d s au plus · %s"):format(tr.name, left, cdTxt)
end

local function buildTrapBar()
  if trapBar or not CreateFrame or not opt("trapBar") then return trapBar end
  local f = CreateFrame("StatusBar", nil, UIParent)
  if f.SetClampedToScreen then f:SetClampedToScreen(true) end
  f:SetSize(190, 12)
  f:SetPoint("CENTER", UIParent, "CENTER", 0, -178)
  if f.SetStatusBarTexture then f:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar") end
  if f.SetStatusBarColor then f:SetStatusBarColor(0.3, 0.7, 1) end
  f:SetMinMaxValues(0, TRAP_LIFE)
  f.text = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.text:SetPoint("CENTER", f, "CENTER")
  if TreeAI.Theme then TreeAI.Theme:Skin(f, "bar"); TreeAI.Theme:Font(f.text, "S") end
  if TreeAI.RegisterMovable then pcall(TreeAI.RegisterMovable, TreeAI, f, "huntertrap", "Barre de piège (chasseur)") end
  local acc = 0
  f:SetScript("OnUpdate", function(_, el) acc = acc + (el or 0) if acc >= 0.2 then acc = 0 M:TrapTick() end end)
  f:Hide()
  trapBar = f
  return f
end

function M:TrapTick()
  local t = now()
  if not trap then if trapBar then trapBar:Hide() end return end
  local armedOver = trap.fight or t > trap.armedUntil
  if armedOver and t > (trap.cdUntil or 0) then
    trap = nil
    if trapBar then trapBar:Hide() end
    return
  end
  if trapBar and opt("trapBar") then
    trapBar:SetValue(trap.fight and 0 or math.max(0, trap.armedUntil - t))
    trapBar.text:SetText(M.TrapText(trap, t))
    trapBar:Show()
  end
end

function M:OnTrapCast(name)
  local t = now()
  trap = { name = name, placed = t, armedUntil = t + TRAP_LIFE, cdUntil = t + TRAP_CD }
  if opt("trapBar") then buildTrapBar() end
  M:TrapTick()
end
function M.Trap() return trap end

local fdAt = nil
--- Feindre la mort a VRAIMENT marché quand le jeu te sort du combat juste après (résisté = tu restes en combat)
function M:OnLeaveCombat()
  if fdAt and now() - fdAt <= 2 and knows(S.FREEZING) then
    local cdLeft = trap and trap.cdUntil and (trap.cdUntil - now()) or 0
    if cdLeft <= 1 then
      suggest("fd", "Feindre la mort a marché : pose ton piège maintenant", 2, 10,
        { ttl = 8, tooltip = "Macro « TAI FD Piege » (panneau Macros) : 2e clic = Piège givrant à tes pieds." })
    end
  end
  fdAt = nil
end

---------------------------------------------------------------------------
-- Barre de tir automatique
---------------------------------------------------------------------------
local bar, autoOn, lastShot, shotSpeed = nil, false, nil, 2.0
M.clips = 0

local function rangedSpeed()
  if UnitRangedDamage then
    local s = _ns(UnitRangedDamage("player"))
    if type(s) == "number" and s > 0 then return s end
  end
  return 2.0
end

local function buildBar()
  if bar or not CreateFrame or not opt("bar") then return end
  local f = CreateFrame("StatusBar", nil, UIParent)
  if f.SetClampedToScreen then f:SetClampedToScreen(true) end
  f:SetSize(160, 10)
  local d = settings()
  local p = d and d.barPos
  f:ClearAllPoints()
  if type(p) == "table" and p.point then f:SetPoint(p.point, UIParent, p.point, p.x or 0, p.y or 0)
  else f:SetPoint("CENTER", UIParent, "CENTER", 0, -160) end
  f:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar")
  f:SetMinMaxValues(0, 1)
  f:SetValue(0)
  local bg = f:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(f)
  bg:SetColorTexture(0, 0, 0, 0.5)
  f.text = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.text:SetPoint("CENTER", f, "CENTER")
  if TreeAI and TreeAI.Theme then TreeAI.Theme:Skin(f, "bar", { bg = bg }); TreeAI.Theme:Font(f.text, "S") end
  f:SetMovable(true)
  f:EnableMouse(true)
  f:RegisterForDrag("LeftButton")
  -- (PLACEBUG 4.0.1) glissée = posée par le joueur (ancrage explicite sur UIParent, le placement auto la laisse)
  if TreeAI._HookMoves then TreeAI._HookMoves(f, "barPos") end
  f:SetScript("OnDragStart", function(s) if IsShiftKeyDown and IsShiftKeyDown() then s:StartMoving() end end)
  f:SetScript("OnDragStop", function(s)
    s:StopMovingOrSizing()
    local dd = settings()
    if dd then
      local point, _, _, x, y = s:GetPoint()
      dd.barPos = { point = point, x = x, y = y }
    end
  end)
  f:SetScript("OnUpdate", function(s)
    if not lastShot then s:SetValue(0); s.text:SetText(""); return end
    local el = now() - lastShot
    local frac = math.min(el / shotSpeed, 1)
    s:SetValue(frac)
    local left = shotSpeed - el
    -- (Forever) GetUnitSpeed est secret en combat : on suit PLAYER_STARTED/STOPPED_MOVING (jamais secrets)
    local moving = M.moving
    if left <= 0.5 then
      if moving then s:SetStatusBarColor(1, 0.2, 0.2); s.text:SetText("Ne bouge pas !")
      else s:SetStatusBarColor(1, 0.85, 0.2); s.text:SetText(left > 0 and (("%.1f"):format(left):gsub("%.", ",")) or "Tir") end
    else
      s:SetStatusBarColor(0.3, 0.8, 0.3); s.text:SetText((("%.1f"):format(left):gsub("%.", ",")))
    end
    if not autoOn and el > shotSpeed + 1 then lastShot = nil; s:Hide() end
  end)
  f:Hide()
  bar = f
end

function M:OnAutoShot()
  lastShot = now()
  shotSpeed = rangedSpeed()
  if bar and opt("bar") then bar:Show() end
end

local CLIPPERS = { [S.AIMED] = true, [S.MULTI] = true }
function M:OnCastStart(spellID)
  if not autoOn or not lastShot then return end
  local nameAimed, nameMulti = spellName(S.AIMED), spellName(S.MULTI)
  local name, startMS, endMS
  if UnitCastingInfo then
    local n, _, _, s, e = UnitCastingInfo("player"); name, startMS, endMS = pub(n), pub(s), pub(e)
  elseif CastingInfo then
    local n, _, _, s, e = CastingInfo(); name, startMS, endMS = n, s, e
  end
  if not (CLIPPERS[spellID] or (name and (name == nameAimed or name == nameMulti))) then return end
  if not (startMS and endMS) then return end
  local cast = (endMS - startMS) / 1000
  local left = shotSpeed - (now() - lastShot)
  if cast > left + 0.15 then
    M.clips = M.clips + 1
    suggest("clip", "Lance « " .. (name or "ce sort") .. " » juste après un tir auto : là, il retarde le suivant", 1, 20)
  end
end

---------------------------------------------------------------------------
-- Onglet « Chasseur » : la fiche (familier, munitions), lignes courtes, verbe d'abord quand il y a quelque chose à faire
---------------------------------------------------------------------------
local TAB = "hunter"
function M:TabLines()
  local out = {}
  local function add(text, prio, tip, click) out[#out + 1] = { text = text, prio = prio or 1, tooltip = tip, onClick = click } end
  if UnitExists and UnitExists("pet") then
    local fam = _ns(UnitCreatureFamily and UnitCreatureFamily("pet"))
    local name = _ns(UnitName and UnitName("pet"))
    local lvl = tonumber(UnitLevel and UnitLevel("pet"))
    local happy, dmg = M.Mood()
    local head = ("Familier : %s%s%s"):format(type(name) == "string" and name or "?",
      type(fam) == "string" and (" (" .. fam .. (lvl and (", niv " .. lvl) or "") .. ")") or "",
      happy and (" · " .. MOOD[happy] .. ", " .. dmg .. " % de ses dégâts") or "")
    add(head, (UnitIsDead and UnitIsDead("pet")) and 3 or ((happy or 3) == 1 and 2 or 1))
    local food, quality = self:BestFood()
    local diet = dietText(self:GetDiet())
    if food then
      add(("Nourriture : %s ×%d%s"):format(itemName(food), itemCount(food), quality == "partial" and " (un peu faible)" or ""), 1,
        { "Régime : " .. diet,
          "Choisie dans tes sacs pour son niveau (la moins chère qui le rend heureux).",
          "Macro « TAI Familier » : Ctrl+clic = nourrir avec elle." })
    else
      add("Achète de quoi le nourrir : rien dans tes sacs (régime : " .. diet .. ")", 2)
    end
    local loyalty = GetPetLoyalty and _ns(GetPetLoyalty())
    local free = freeTrainingPoints()
    local parts = {}
    if type(loyalty) == "string" and loyalty ~= "" then parts[#parts + 1] = loyalty end
    if free then parts[#parts + 1] = free > 0 and (free .. " points de dressage libres") or "aucun point de dressage libre" end
    if #parts > 0 then add(table.concat(parts, " · "), (free or 0) > 0 and 2 or 1,
      (free or 0) > 0 and "Dépense-les avec « Dressage des bêtes » (grimoire)." or nil) end
    local ups, famName = M.AbilityUpgrades()
    for i, u in ipairs(ups or {}) do
      if i > 3 then break end
      add(("Technique à monter : %s rang %d (niv %d), il a le rang %d"):format(u.name, u.rank, u.level or 0, u.have), 1,
        { ("Tables du client Forever pour la famille « %s »."):format(famName or "?"),
          "Si tu connais déjà ce rang : « Dressage des bêtes ». Sinon apprivoise une bête qui l'a.",
          "Clic : Chercher → Familier (les bêtes à ta portée)." },
        function()
          local F = TreeAI.Finder
          if F and F.Show then F.lastQuery = "familier" pcall(F.Show, true) end
        end)
    end
  elseif knows(S.CALL) then
    add("Pas de familier : « " .. (spellName(S.CALL) or "Appel du familier") .. " »", 1)
  end
  local a = M.lastAmmo or self:AmmoInfo()
  if a.needs then
    local word = a.kind == "arrow" and "flèches" or "balles"
    local rate, played = M.AmmoRate()
    local txt = ("Munitions : %s %s"):format(fmtNum(a.total), word)
    if rate and a.total > 0 then txt = txt .. (" · ≈ %s de jeu à ton rythme"):format(fmtTime(a.total / rate * 3600)) end
    local tip = { "Compte : toutes tes " .. word .. " (sacs + emplacement)." }
    if rate then tip[#tip + 1] = ("Rythme : %s par heure de jeu, mesuré sur %s."):format(fmtNum(rate),
      played and fmtTime(played * 60) or "au moins 10 min") end
    tip[#tip + 1] = "Chez un marchand : la barre de combat les achète d'un clic."
    add(txt, a.total < 50 and 3 or (a.total < (tonumber(opt("ammoWarn")) or 200) and 2 or 1), tip)
    if not a.quiver and (tonumber(UnitLevel and UnitLevel("player")) or 1) >= 10 then
      add("Équipe un " .. (a.kind == "arrow" and "carquois" or "giberne") .. " : tir plus rapide et place pour tes " .. word, 1)
    end
  end
  return out
end

local tabSig
local lastPush = -100
function M:PushTab(force)
  if not (TreeAI.SetTabLines and opt("tab")) then return end
  local t = now()
  if not force and t - lastPush < 10 then return end   -- la fiche suit sans courir : au plus toutes les 10 s
  lastPush = t
  local ok, lines = pcall(self.TabLines, self)
  if not ok or type(lines) ~= "table" then return end
  local parts = {}
  for _, l in ipairs(lines) do parts[#parts + 1] = l.text end
  local sig = table.concat(parts, "\n")
  if sig == tabSig and not force then return end
  tabSig = sig
  pcall(TreeAI.SetTabLines, TreeAI, TAB, lines, "Hunter")
end

---------------------------------------------------------------------------
-- Instantané pour TreeAI (lu après /reload ou déconnexion)
---------------------------------------------------------------------------
-- Écrit TreeAIDB.chars[clé].summary.hunter (lu par TreeAI Coach : treeaicoach/wow/classes/hunter.py).
function M:Snapshot()
  local s = { t = time and time() or 0, level = UnitLevel and UnitLevel("player") or 0, clips = M.clips }
  if UnitExists and UnitExists("pet") then
    s.pet = { level = UnitLevel("pet"), family = UnitCreatureFamily and _ns(UnitCreatureFamily("pet")) or nil,
      dead = UnitIsDead and UnitIsDead("pet") or false }
    local h, dmg, loy = M.Mood()
    s.pet.happiness, s.pet.damagePct, s.pet.loyaltyRate = h, dmg, loy
    if GetPetLoyalty then s.pet.loyalty = _ns(GetPetLoyalty()) end
    s.pet.tpFree = freeTrainingPoints()
    local diet = {}
    for k in pairs(self:GetDiet()) do diet[#diet + 1] = k end
    table.sort(diet)
    s.pet.diet = diet
    s.pet.food = self:BestFood()
  end
  local a = self:AmmoInfo()
  local rate = M.AmmoRate()
  s.ammo = { kind = a.kind, total = a.total, equipped = a.equipped, quiver = a.quiver, mismatch = a.mismatch,
    perHour = rate and math.floor(rate + 0.5) or nil }
  local summary
  if type(TreeAI.Summary) == "function" then
    local ok, t = pcall(TreeAI.Summary, TreeAI); if ok and type(t) == "table" then summary = t end
  end
  if not summary then
    local root = _G.TreeAIDB
    if type(root) ~= "table" then return end
    local key
    if type(TreeAI.CharKey) == "function" then
      local ok, k = pcall(TreeAI.CharKey, TreeAI); if ok then key = k end
    end
    if not key and UnitName then key = (UnitName("player") or "?") .. "-" .. ((GetRealmName and GetRealmName()) or "?") end
    if not key then return end
    root.chars = type(root.chars) == "table" and root.chars or {}
    root.chars[key] = type(root.chars[key]) == "table" and root.chars[key] or {}
    local c = root.chars[key]
    c.summary = type(c.summary) == "table" and c.summary or {}
    summary = c.summary
  end
  summary.hunter = s
end

---------------------------------------------------------------------------
-- Événements
---------------------------------------------------------------------------
local frame
local EVENTS = {
  "PLAYER_ENTERING_WORLD", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED", "PLAYER_TARGET_CHANGED",
  "UNIT_PET", "UNIT_HAPPINESS", "PET_BAR_UPDATE", "BAG_UPDATE_DELAYED", "UNIT_INVENTORY_CHANGED",
  "START_AUTOREPEAT_SPELL", "STOP_AUTOREPEAT_SPELL", "UNIT_SPELLCAST_SUCCEEDED", "UNIT_SPELLCAST_START",
  "UNIT_AURA", "PLAYER_LOGOUT", "PLAYER_LEAVING_WORLD", "PLAYER_STARTED_MOVING", "PLAYER_STOPPED_MOVING",
}

local UNIT_EVENTS = { UNIT_PET = true, UNIT_HAPPINESS = true, UNIT_INVENTORY_CHANGED = true,
  UNIT_SPELLCAST_SUCCEEDED = true, UNIT_SPELLCAST_START = true, UNIT_AURA = true }
function M:OnEvent(ev, ...)
  if ev == "PLAYER_STARTED_MOVING" then M.moving = true return end   -- barre de tir : « Ne bouge pas ! »
  if ev == "PLAYER_STOPPED_MOVING" then M.moving = false return end
  if ev == "UNIT_PET" or ev == "PET_BAR_UPDATE" then abilityMemo.at, foodMemo.at = -100, -100 end   -- autre familier
  if ev == "BAG_UPDATE_DELAYED" or ev == "UNIT_INVENTORY_CHANGED" then ammoMemo.at = -100 end   -- munitions relues
  -- (LITE) événements d'unité : seulement le joueur et son familier (pas tout le groupe à chaque sort)
  if UNIT_EVENTS[ev] then
    local unit = ...
    if unit ~= nil and unit ~= "player" and unit ~= "pet" then return end
    if ev == "UNIT_AURA" then
      -- seulement pour effacer la ligne « Active … avant le pull » dès que l'aspect est mis (pas un scan à chaque buff)
      if unit == "player" and lastSaid.aspect and not inCombat() then self:CheckAspect() end
      return
    end
  end
  if ev == "BAG_UPDATE_DELAYED" or ev == "UNIT_INVENTORY_CHANGED" or ev == "PLAYER_ENTERING_WORLD" then bagCache = nil end
  if ev == "START_AUTOREPEAT_SPELL" then autoOn = true; buildBar(); return end
  if ev == "STOP_AUTOREPEAT_SPELL" then autoOn = false; return end
  if ev == "UNIT_SPELLCAST_SUCCEEDED" then
    local unit, _, spellID = ...
    if unit ~= "player" then return end
    spellID = pub(spellID)
    if spellID == S.AUTO then return self:OnAutoShot() end
    if spellID == S.FEIGN or (spellID and spellName(spellID) == spellName(S.FEIGN)) then fdAt = now() return end
    local tn = isTrap(spellID)
    if tn then self:OnTrapCast(tn) end
    return
  end
  if ev == "UNIT_SPELLCAST_START" then
    local unit, _, spellID = ...
    if unit == "player" then self:OnCastStart(pub(spellID)) end
    return
  end
  if ev == "PLAYER_LOGOUT" or ev == "PLAYER_LEAVING_WORLD" then return pcall(self.Snapshot, self) end
  if ev == "PLAYER_TARGET_CHANGED" then self:CheckMark(); self:CheckAspect(); return end
  if ev == "PLAYER_REGEN_DISABLED" then
    if trap then trap.fight = true end
    clear("mark")
    self:CheckAspect()
    return
  end
  if ev == "PLAYER_REGEN_ENABLED" then self:OnLeaveCombat() end
  if ev == "BAG_UPDATE_DELAYED" or ev == "UNIT_INVENTORY_CHANGED" or ev == "PLAYER_ENTERING_WORLD"
      or ev == "PLAYER_REGEN_ENABLED" then
    self:CheckAmmo()
  end
  self:CheckPet()
  if ev == "PLAYER_REGEN_ENABLED" or ev == "PLAYER_ENTERING_WORLD" then self:CheckTraining(); self:CheckAspect() end
  self:PushTab()
end

function M:Enable()
  if self.enabled or not CreateFrame then return end
  local cls
  if type(TreeAI.PlayerClass) == "function" then
    local ok, c = pcall(TreeAI.PlayerClass, TreeAI); if ok then cls = c end
  end
  if not cls and UnitClass then cls = select(2, UnitClass("player")) end
  if cls ~= "HUNTER" then return end
  self.enabled = true
  settings()
  if TreeAI.RegisterTab and opt("tab") then pcall(TreeAI.RegisterTab, TreeAI, TAB, "Chasseur", 4.6) end
  if type(TreeAI.TAB_OWNER) == "table" then TreeAI.TAB_OWNER[TAB] = "Hunter" end
  if type(TreeAI.MODULE_TAB) == "table" then TreeAI.MODULE_TAB.Hunter = TAB end
  frame = frame or CreateFrame("Frame")
  for _, e in ipairs(EVENTS) do
    if not (TreeAI.EventAllowed and not TreeAI:EventAllowed(e)) then pcall(frame.RegisterEvent, frame, e) end
  end
  frame:SetScript("OnEvent", function(_, ev, ...)
    local ok, err = pcall(M.OnEvent, M, ev, ...)
    if not ok and TreeAI.Debug then pcall(TreeAI.Debug, TreeAI, "Hunter: " .. tostring(err)) end
    if not ok and TreeAI._errorHook then pcall(TreeAI._errorHook, err, "Hunter:" .. tostring(ev)) end
  end)
  if not self._slowTicker and TreeAI.NewTicker then
    self._slowTicker = TreeAI:NewTicker(5, function()
      if not M.enabled then return end
      pcall(M.CheckPet, M)
      if inCombat() then pcall(M.CheckAspect, M) end
    end, M)
  end
  if type(self.On) == "function" and not self.summaryHooked then
    self.summaryHooked = true
    pcall(self.On, self, "SUMMARY", function() if M.enabled then pcall(M.Snapshot, M) end end)
  end
  if IsLoggedIn and IsLoggedIn() then pcall(M.OnEvent, M, "PLAYER_ENTERING_WORLD") end
end
M.OnEnable = M.Enable

function M:OnDisable()
  self.enabled = false
  if frame then
    if frame.UnregisterAllEvents then frame:UnregisterAllEvents() end
    frame:SetScript("OnUpdate", nil)
  end
  if self._slowTicker then self._slowTicker:Cancel(); self._slowTicker = nil end
  if bar then bar:Hide() end
  if trapBar then trapBar:Hide() end
  trap = nil
  if TreeAI.SetTabLines and TreeAI.tabs and TreeAI.tabs[TAB] then pcall(TreeAI.SetTabLines, TreeAI, TAB, {}, "Hunter") end
  tabSig = nil
end

function M:OnOptionChanged(key, value)
  if key == "bar" and not value and bar then bar:Hide() end
  if key == "trapBar" and not value and trapBar then trapBar:Hide() end
  if key == "tab" then tabSig = nil M:PushTab(true) end
end

-- Déclaration auprès du cœur (agent B) : défauts, classe, options auto-rendues.
M.defaults = { enabled = true, bar = true, trapBar = true, ammoWarn = 200, tab = true }
M.classes = { HUNTER = true }
M.title = "Chasseur"
M.options = {
  { key = "tab", type = "toggle", label = "Onglet « Chasseur » : fiche du familier et des munitions" },
  { key = "bar", type = "toggle", label = "Barre de tir automatique (Maj+glisser pour déplacer)" },
  { key = "trapBar", type = "toggle", label = "Barre de piège : posé, recharge (mode édition pour déplacer)" },
  { key = "ammoWarn", type = "range", label = "Alerte munitions sous", min = 50, max = 2000, step = 50 },
}

TreeAI.Hunter = M
if type(TreeAI.RegisterModule) == "function" then
  local ok, r = pcall(TreeAI.RegisterModule, TreeAI, "Hunter", M)
  if ok and type(r) == "table" then M = r; TreeAI.Hunter = r end
elseif CreateFrame then
  -- Sans cœur : activation autonome à la connexion.
  local boot = CreateFrame("Frame")
  boot:RegisterEvent("PLAYER_LOGIN")
  boot:SetScript("OnEvent", function() pcall(M.Enable, M) end)
end

function M:AutoRefresh()
  if InCombatLockdown and InCombatLockdown() then return end
  pcall(M.CheckPet, M); pcall(M.CheckAmmo, M); pcall(M.CheckAspect, M); pcall(M.CheckTraining, M)
  pcall(M.PushTab, M)
end
