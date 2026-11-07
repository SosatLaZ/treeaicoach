-- TreeAI — module Chasseur (wow-D).
-- INFORMATIF UNIQUEMENT : aucune action automatique, aucun envoi de touche.
-- Seule écriture : la macro « TAI Nourrir » (CreateMacro/EditMacro), hors combat
-- uniquement (InCombatLockdown), désactivable (option feedMacro).
--
-- Contrat cœur : wow_addon/README.md (RegisterModule, M:Suggest prio 1-3, M.db, TreeAI.API, SUMMARY).
-- Réglages (compte) : M.db = TreeAIDB.modules.Hunter = { enabled, bar, feedMacro, ammoWarn, barPos = {point,x,y} }.
-- Instantané pour TreeAI Coach : TreeAIDB.chars[clé].summary.hunter (voir treeaicoach/wow/classes/hunter.py).
-- Sans cœur (tests) : repli autonome, API brutes protégées.

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
  AUTO = 75, RAPTOR = 2973, WINGCLIP = 2974, FEED = 6991, FEED_EFFECT = 1539,
  MEND = 136, REVIVE = 982, CALL = 883, MARK = 1130, FEIGN = 5384,
  FREEZING = 1499, AIMED = 19434, MULTI = 2643,
  HAWK = 13165, MONKEY = 13163, CHEETAH = 5118, PACK = 13159, BEAST = 13161, WILD = 20043,
}
local MARK_IDS = { [1130] = true, [14323] = true, [14324] = true, [14325] = true }
local COMBAT_ASPECTS = { S.HAWK, S.MONKEY, S.WILD, S.BEAST }
local TRAVEL_ASPECTS = { S.CHEETAH, S.PACK }

---------------------------------------------------------------------------
-- Utilitaires défensifs
---------------------------------------------------------------------------
local function now() return (GetTime and GetTime()) or 0 end
-- Wrappers défensifs du cœur (Forever = famille Mainline : globales classiques possiblement absentes).
local function API() return type(TreeAI.API) == "table" and TreeAI.API or nil end

local function spellName(id)
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

local function knows(id)
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
    return d.name, d.spellId, d.sourceUnit, d.expirationTime
  end
  if UnitAura then
    local name, _, _, _, _, exp, source, _, _, sid = UnitAura(unit, i, filter)
    return name, sid, source, exp
  end
  return nil
end

-- names : set de noms localisés ; ids : set d'IDs. mine : ne garder que nos auras.
local function hasAura(unit, filter, names, ids, mine)
  local A = API()
  if A and A.Auras then
    local ok, list = pcall(A.Auras, unit, filter)
    if ok and type(list) == "table" then
      for _, a in ipairs(list) do
        if (names and a.name and names[a.name]) or (ids and a.spellId and ids[a.spellId]) then
          if not mine or a.source == "player" or a.source == "pet" then return true, a.name end
        end
      end
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

local function itemInstant(id)
  if not id then return nil end
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

local function bagItemIDs()
  local seen, out = {}, {}
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

local function inCombat()
  if type(TreeAI.InCombat) == "function" then
    local ok, r = pcall(TreeAI.InCombat, TreeAI)
    if ok and r ~= nil then return r and true or false end
  end
  if InCombatLockdown and InCombatLockdown() then return true end
  return (UnitAffectingCombat and UnitAffectingCombat("player")) and true or false
end

-- Réglages (compte) : M.db fourni par le cœur (defaults fusionnés), sinon repli TreeAIDB.modules.Hunter.
local DEFAULTS = { enabled = true, bar = true, feedMacro = true, ammoWarn = 200 }
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
local function suggest(id, text, prio, every)
  if not opt("enabled") then return end
  local t = now()
  if lastSaid[id] and t - lastSaid[id] < (every or 30) then return end
  lastSaid[id] = t
  local o = { cooldown = every or 30, ttl = math.max(every or 30, 20), module = "Hunter" }
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

---------------------------------------------------------------------------
-- Familier : régime, meilleure nourriture, bonheur, loyauté, points
---------------------------------------------------------------------------
function M:GetDiet()
  local diet = {}
  if not GetPetFoodTypes then return diet end
  local types = { GetPetFoodTypes() }
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

function M:BestFood()
  if not (UnitExists and UnitExists("pet")) then return nil end
  local lvl = (UnitLevel and UnitLevel("pet")) or 1
  return M.PickFood(bagItemIDs(), self:GetDiet(), lvl)
end

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

local FEED_MACRO = "TAI Nourrir"
local lastMacroBody
function M:UpdateFeedMacro(foodID)
  if not opt("feedMacro") or not foodID then return end
  if inCombat() or not (CreateMacro and EditMacro and GetMacroIndexByName) then return end
  local feed = spellName(S.FEED)
  if not feed then return end
  local body = "#showtooltip\n/cast " .. feed .. "\n/use item:" .. foodID
  if body == lastMacroBody then return end
  local idx = GetMacroIndexByName(FEED_MACRO)
  local ok
  if idx and idx > 0 then
    ok = pcall(EditMacro, idx, FEED_MACRO, nil, body)
  elseif GetNumMacros or (API() and API().GetMacroCount) then
    local A, perChar = API(), nil
    if A and A.GetMacroCount then perChar = select(2, A.GetMacroCount()) else perChar = select(2, GetNumMacros()) end
    local max = _G.MAX_CHARACTER_MACROS or 18
    if (perChar or max) < max then
      ok = pcall(CreateMacro, FEED_MACRO, "INV_MISC_QUESTIONMARK", body, true)
      if ok then
        suggest("macro", "Macro « " .. FEED_MACRO .. " » créée : mets-la dans ta barre (1 clic = nourrir).", 1, 3600)
      end
    end
  end
  if ok then lastMacroBody = body end
end

function M:CheckPet()
  if not (UnitExists and UnitExists("pet")) then
    clear("pethp")
    if knows(S.CALL) and not inCombat() and not (IsMounted and IsMounted())
        and not (UnitOnTaxi and UnitOnTaxi("player")) and not (UnitIsDeadOrGhost and UnitIsDeadOrGhost("player")) then
      suggest("nopet", "Pas de familier : « " .. (spellName(S.CALL) or "Appel du familier") .. " ».", 1, 300)
    end
    return
  end
  if UnitIsDead and UnitIsDead("pet") then
    suggest("petdead", "Familier mort : « " .. (spellName(S.REVIVE) or "Ressusciter le familier") .. " » hors combat.", 3, 60)
    return
  end
  -- Santé
  local hp, mx = UnitHealth("pet") or 0, UnitHealthMax("pet") or 1
  local pct = mx > 0 and hp / mx or 1
  if inCombat() and pct < 0.35 then
    suggest("pethp", string.format("Familier à %d %% : « %s » ou rappelle-le.", pct * 100,
      spellName(S.MEND) or "Guérison du familier"), 3, 8)
  end
  -- Bonheur / loyauté
  if not GetPetHappiness then return end
  local happy, _, loyaltyRate = GetPetHappiness()
  if not happy then return end
  local feeding = hasAura("pet", "HELPFUL", nameSet({ S.FEED_EFFECT }), { [S.FEED_EFFECT] = true })
  if feeding then clear("hungry"); return end
  local food, quality = self:BestFood()
  if food then self:UpdateFeedMacro(food) end
  if happy >= 3 then clear("hungry"); return end
  local foodTxt
  if food then
    foodTxt = "Donne-lui : " .. itemName(food) .. (quality == "partial" and " (un peu faible)" or "")
  else
    foodTxt = "Aucune nourriture adaptée dans les sacs (régime : " .. dietText(self:GetDiet()) .. ")"
  end
  if happy == 1 or (loyaltyRate and loyaltyRate < 0) then
    suggest("hungry", "Familier MÉCONTENT : 75 % des dégâts et perte de loyauté (risque de fuite). " .. foodTxt .. ".", 3, 45)
  elseif not inCombat() then
    suggest("hungry", "Familier juste content : nourris-le pour +25 % de dégâts. " .. foodTxt .. ".", 1, 240)
  end
end

function M:CheckTraining()
  if inCombat() or not GetPetTrainingPoints or not (UnitExists and UnitExists("pet")) then return end
  local total, spent = GetPetTrainingPoints()
  local free = (total or 0) - (spent or 0)
  if free > 0 then
    suggest("tp", string.format("%d point(s) d'entraînement libres : Dressage des bêtes (nouvelles techniques du familier).", free), 1, 900)
  end
end

---------------------------------------------------------------------------
-- Munitions, carquois
---------------------------------------------------------------------------
-- Renvoie infos munitions : equipped (nombre), kind ("arrow"/"bullet"/nil), total compatible, mismatch, quiver
function M:AmmoInfo()
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

function M:CheckAmmo()
  local a = self:AmmoInfo()
  M.lastAmmo = a
  if not a.needs then return end
  local word = a.kind == "arrow" and "flèches" or "balles"
  if a.mismatch then
    suggest("ammotype", "Mauvaises munitions équipées : ton arme utilise des " .. word .. ".", 3, 30)
  end
  if a.equipped == 0 then
    if a.total > 0 then
      suggest("ammo", "Aucune munition équipée : place tes " .. word .. " dans l'emplacement de munitions.", 3, 20)
    else
      suggest("ammo", "Plus de " .. word .. " ! Achète-en chez un marchand d'armes/aubergiste.", 3, 30)
    end
  elseif a.total < 50 then
    suggest("ammo", string.format("Plus que %d %s : réapprovisionne-toi vite.", a.total, word), 3, 60)
  elseif a.total < (opt("ammoWarn") or 200) then
    suggest("ammo", string.format("Munitions basses : %d %s.", a.total, word), 2, 300)
  else
    clear("ammo")
  end
  local lvl = UnitLevel and UnitLevel("player") or 1
  if not a.quiver and lvl >= 10 and not inCombat() then
    local q = a.kind == "arrow" and "carquois" or "giberne"
    suggest("quiver", "Pas de " .. q .. " équipé : il accélère ton tir et range tes " .. word .. ".", 1, 1800)
  end
end

---------------------------------------------------------------------------
-- Aspects, Marque du chasseur
---------------------------------------------------------------------------
function M:CheckAspect()
  local anyKnown = false
  for _, id in ipairs(COMBAT_ASPECTS) do if knows(id) then anyKnown = true end end
  if not anyKnown then return end
  local combatOn = hasAura("player", "HELPFUL", nameSet(COMBAT_ASPECTS))
  local travelOn, travelName = hasAura("player", "HELPFUL", nameSet(TRAVEL_ASPECTS))
  if inCombat() then
    if travelOn then
      suggest("aspect", "« " .. travelName .. " » en combat : tu seras hébété à chaque coup ! Change d'aspect.", 3, 10)
    elseif not combatOn then
      local best = knows(S.HAWK) and spellName(S.HAWK) or spellName(S.MONKEY)
      suggest("aspect", "Aucun aspect actif : « " .. (best or "Aspect du faucon") .. " » (singe si ça tape au corps à corps).", 2, 25)
    else
      clear("aspect")
    end
  elseif not combatOn and not travelOn then
    suggest("aspect", "Pense à un aspect avant le prochain pull.", 1, 600)
  end
end

local markedGUID
function M:CheckMark()
  if not knows(S.MARK) or not (UnitExists and UnitExists("target")) then return end
  if not (UnitCanAttack and UnitCanAttack("player", "target")) or (UnitIsDead and UnitIsDead("target")) then return end
  local guid = UnitGUID and UnitGUID("target")
  if guid and guid == markedGUID then return end
  local cls = UnitClassification and UnitClassification("target") or "normal"
  local big = cls == "elite" or cls == "rareelite" or cls == "worldboss" or cls == "rare"
  local grp = (IsInGroup and IsInGroup()) or ((GetNumPartyMembers and GetNumPartyMembers() or 0) > 0)
  if not big and not grp then return end
  if hasAura("target", "HARMFUL", nameSet({ S.MARK }), MARK_IDS) then markedGUID = guid; return end
  markedGUID = guid
  suggest("mark", "Cible " .. (big and "élite" or "de groupe") .. " : « " .. (spellName(S.MARK) or "Marque du chasseur") .. " » d'abord.", 1, 15)
end

---------------------------------------------------------------------------
-- Zone morte (entre ~5 et 8 m : ni tir ni mêlée)
---------------------------------------------------------------------------
local function inRange(name, unit)
  if not name then return nil end
  if C_Spell and C_Spell.IsSpellInRange then
    local ok, r = pcall(C_Spell.IsSpellInRange, name, unit)
    if ok and r ~= nil then return r == true or r == 1 end
  end
  if IsSpellInRange then
    local r = IsSpellInRange(name, unit)
    if r ~= nil then return r == 1 end
  end
  return nil
end

function M:CheckDeadZone()
  if not inCombat() or not (UnitExists and UnitExists("target")) then return end
  if not (UnitCanAttack and UnitCanAttack("player", "target")) or (UnitIsDead and UnitIsDead("target")) then return end
  local shoot = inRange(spellName(S.AUTO), "target")
  local melee = inRange(spellName(S.RAPTOR) or spellName(S.WINGCLIP), "target")
  if shoot ~= false or melee ~= false then return end
  local close = false
  if CheckInteractDistance then
    local ok, r = pcall(CheckInteractDistance, "target", 3) -- ~10 m
    close = ok and r and true or false
  end
  if close then
    suggest("deadzone", "Zone morte : trop près pour tirer, trop loin pour frapper. Recule (ou colle-toi).", 2, 6)
  end
end

---------------------------------------------------------------------------
-- Pièges : 2+ ennemis sur toi, ou après Feindre la mort
---------------------------------------------------------------------------
local attackers = {}
local function cooldownReady(id)
  local n = spellName(id)
  if not n then return false end
  local start, dur
  if C_Spell and C_Spell.GetSpellCooldown then
    local ok, cd = pcall(C_Spell.GetSpellCooldown, id)
    if ok and type(cd) == "table" then start, dur = cd.startTime, cd.duration end
  end
  local A = API()
  if start == nil and A and A.SpellCooldown then
    local ok, st, du = pcall(A.SpellCooldown, id)
    if ok then start, dur = st, du end
  end
  if start == nil and GetSpellCooldown then start, dur = GetSpellCooldown(n) end
  if not start then return false end
  return start == 0 or (start + (dur or 0) - now()) < 1.5
end

function M:OnCombatLog(...)
  local _, sub, _, srcGUID, _, srcFlags, _, dstGUID = ...
  if CombatLogGetCurrentEventInfo then
    _, sub, _, srcGUID, _, srcFlags, _, dstGUID = CombatLogGetCurrentEventInfo()
  end
  if not sub or not UnitGUID then return end
  local me = UnitGUID("player")
  if dstGUID == me and srcFlags and bit and bit.band(srcFlags, 0x40) ~= 0
      and (sub == "SWING_DAMAGE" or sub == "SPELL_DAMAGE" or sub == "RANGE_DAMAGE" or sub == "SWING_MISSED") then
    attackers[srcGUID] = now()
    local n, t = 0, now()
    for g, ts in pairs(attackers) do
      if t - ts < 6 then n = n + 1 else attackers[g] = nil end
    end
    if n >= 2 and knows(S.FREEZING) and cooldownReady(S.FREEZING) then
      suggest("trap", n .. " ennemis sur toi : « " .. (spellName(S.FREEZING) or "Piège givrant") .. " » sur l'add (Feindre la mort si besoin).", 2, 20)
    end
  end
end

---------------------------------------------------------------------------
-- Barre de tir automatique
---------------------------------------------------------------------------
local bar, autoOn, lastShot, shotSpeed = nil, false, nil, 2.0
M.clips = 0

local function rangedSpeed()
  if UnitRangedDamage then
    local s = UnitRangedDamage("player")
    if s and s > 0 then return s end
  end
  return 2.0
end

local function buildBar()
  if bar or not CreateFrame or not opt("bar") then return end
  local f = CreateFrame("StatusBar", nil, UIParent)
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
  f:SetMovable(true)
  f:EnableMouse(true)
  f:RegisterForDrag("LeftButton")
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
    local moving = GetUnitSpeed and (GetUnitSpeed("player") or 0) > 0
    if left <= 0.5 then
      if moving then s:SetStatusBarColor(1, 0.2, 0.2); s.text:SetText("Ne bouge pas !")
      else s:SetStatusBarColor(1, 0.85, 0.2); s.text:SetText(left > 0 and string.format("%.1f", left) or "Tir") end
    else
      s:SetStatusBarColor(0.3, 0.8, 0.3); s.text:SetText(string.format("%.1f", left))
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
    local n, _, _, s, e = UnitCastingInfo("player"); name, startMS, endMS = n, s, e
  elseif CastingInfo then
    local n, _, _, s, e = CastingInfo(); name, startMS, endMS = n, s, e
  end
  if not (CLIPPERS[spellID] or (name and (name == nameAimed or name == nameMulti))) then return end
  if not (startMS and endMS) then return end
  local cast = (endMS - startMS) / 1000
  local left = shotSpeed - (now() - lastShot)
  if cast > left + 0.15 then
    M.clips = M.clips + 1
    suggest("clip", "« " .. (name or "Sort") .. " » retarde ton tir auto : lance-le juste après un tir.", 1, 20)
  end
end

---------------------------------------------------------------------------
-- Instantané pour TreeAI (lu après /reload ou déconnexion)
---------------------------------------------------------------------------
-- Écrit TreeAIDB.chars[clé].summary.hunter (lu par TreeAI Coach : treeaicoach/wow/classes/hunter.py).
function M:Snapshot()
  local s = { t = time and time() or 0, level = UnitLevel and UnitLevel("player") or 0, clips = M.clips }
  if UnitExists and UnitExists("pet") then
    s.pet = { level = UnitLevel("pet"), family = UnitCreatureFamily and UnitCreatureFamily("pet") or nil,
      dead = UnitIsDead and UnitIsDead("pet") or false }
    if GetPetHappiness then
      local h, dmg, loy = GetPetHappiness(); s.pet.happiness, s.pet.damagePct, s.pet.loyaltyRate = h, dmg, loy
    end
    if GetPetLoyalty then s.pet.loyalty = GetPetLoyalty() end
    if GetPetTrainingPoints then
      local tot, sp = GetPetTrainingPoints(); s.pet.tpFree = (tot or 0) - (sp or 0)
    end
    local diet = {}
    for k in pairs(self:GetDiet()) do diet[#diet + 1] = k end
    table.sort(diet)
    s.pet.diet = diet
    s.pet.food = self:BestFood()
  end
  local a = self:AmmoInfo()
  s.ammo = { kind = a.kind, total = a.total, equipped = a.equipped, quiver = a.quiver, mismatch = a.mismatch }
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
  "COMBAT_LOG_EVENT_UNFILTERED", "PLAYER_LOGOUT", "PLAYER_LEAVING_WORLD",
}

function M:OnEvent(ev, ...)
  if ev == "COMBAT_LOG_EVENT_UNFILTERED" then return self:OnCombatLog(...) end
  if ev == "START_AUTOREPEAT_SPELL" then autoOn = true; buildBar(); return end
  if ev == "STOP_AUTOREPEAT_SPELL" then autoOn = false; return end
  if ev == "UNIT_SPELLCAST_SUCCEEDED" then
    local unit, _, spellID = ...
    if unit ~= "player" then return end
    if spellID == S.AUTO then return self:OnAutoShot() end
    if spellID == S.FEIGN then
      suggest("fd", "Feindre la mort réussi : pose un piège maintenant.", 2, 10)
    end
    return
  end
  if ev == "UNIT_SPELLCAST_START" then
    local unit, _, spellID = ...
    if unit == "player" then self:OnCastStart(spellID) end
    return
  end
  if ev == "PLAYER_LOGOUT" or ev == "PLAYER_LEAVING_WORLD" then return pcall(self.Snapshot, self) end
  if ev == "PLAYER_TARGET_CHANGED" then return self:CheckMark() end
  if ev == "PLAYER_REGEN_DISABLED" then self:CheckAspect(); self:CheckMark(); return end
  if ev == "PLAYER_REGEN_ENABLED" then
    for g in pairs(attackers) do attackers[g] = nil end
  end
  if ev == "BAG_UPDATE_DELAYED" or ev == "UNIT_INVENTORY_CHANGED" or ev == "PLAYER_ENTERING_WORLD"
      or ev == "PLAYER_REGEN_ENABLED" then
    self:CheckAmmo()
  end
  self:CheckPet()
  if ev == "PLAYER_REGEN_ENABLED" or ev == "PLAYER_ENTERING_WORLD" then self:CheckTraining(); self:CheckAspect() end
end

local tick, slow = 0, 0
local function onUpdate(_, elapsed)
  tick, slow = tick + (elapsed or 0), slow + (elapsed or 0)
  if tick >= 0.25 then
    tick = 0
    if inCombat() then pcall(M.CheckDeadZone, M) end
  end
  if slow >= 5 then
    slow = 0
    pcall(M.CheckPet, M)
    if inCombat() then pcall(M.CheckAspect, M) end
  end
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
  frame = frame or CreateFrame("Frame")
  for _, e in ipairs(EVENTS) do pcall(frame.RegisterEvent, frame, e) end
  frame:SetScript("OnEvent", function(_, ev, ...)
    local ok, err = pcall(M.OnEvent, M, ev, ...)
    if not ok and TreeAI.Debug then pcall(TreeAI.Debug, TreeAI, "Hunter: " .. tostring(err)) end
  end)
  frame:SetScript("OnUpdate", onUpdate)
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
  if bar then bar:Hide() end
end

function M:OnOptionChanged(key, value)
  if key == "bar" and not value and bar then bar:Hide() end
  if key == "feedMacro" then lastMacroBody = nil end
end

-- Déclaration auprès du cœur (agent B) : défauts, classe, options auto-rendues.
M.defaults = { enabled = true, bar = true, feedMacro = true, ammoWarn = 200 }
M.classes = { HUNTER = true }
M.title = "Chasseur"
M.options = {
  { key = "bar", type = "toggle", label = "Barre de tir automatique (Maj+glisser pour déplacer)" },
  { key = "feedMacro", type = "toggle", label = "Tenir à jour la macro « TAI Nourrir » (hors combat)" },
  { key = "ammoWarn", type = "range", label = "Seuil d'alerte munitions", min = 50, max = 2000, step = 50 },
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
