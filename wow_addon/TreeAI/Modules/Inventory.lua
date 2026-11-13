-- TreeAI — module Inventaire (agent F, CLEAN 4.0) : le propriétaire des sacs et des consommables.
--  • Sacs presque pleins / pleins (+ dans l'infobulle : ce qui vaut le moins par case, à jeter d'abord).
--  • Réparation : équipement abîmé (vraie durabilité) ; chez un marchand qui répare, le VRAI prix du jeu.
--  • Vente des objets gris : bouton chez le marchand, UNIQUEMENT sur ton clic, hors combat (jamais automatique).
--  • Nourriture : plus que 4 en sac → une ligne ; chez un marchand qui en vend, combien en prendre pour 1 h de jeu
--    à ton rythme MESURÉ (Stats). Boissons aussi pour les lanceurs de sorts — pas pour le chasseur.
-- Lua 5.1, aucune globale créée. Toutes les API WoW sont testées avant usage (Classic / Forever).
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = ns or {}

local M = {
  name = "Inventory", title = "Inventaire",
  defaults = { enabled = true, bagsMinFree = 2, durabilityWarn = 30, food = true },
  options = {
    { key = "bagsMinFree", type = "range", label = "Alerte sacs : places libres", min = 0, max = 10, step = 1 },
    { key = "durabilityWarn", type = "range", label = "Alerte réparation (%)", min = 10, max = 60, step = 5 },
    { key = "food", type = "toggle", label = "Nourriture : presque plus en sac, combien en acheter chez le marchand" },
  },
}
ns.Inventory = M

-- ---------------------------------------------------------------- utilitaires (copie locale, sans globale)
local function core() return _G.TreeAI end

local lastSent = {}
local function suggest(id, text, prio, every, tip)
  local now = (GetTime and GetTime()) or 0
  local prev = lastSent[id]
  if prev and prev.text == text and now - prev.t < (every or 120) then return end
  lastSent[id] = { text = text, t = now }
  local T = core()
  if M.core and M.Suggest then
    pcall(M.Suggest, M, id, text, prio or 2, { cooldown = every or 120, ttl = math.min(every or 120, 300), tooltip = tip })
  elseif T and T.Suggest then
    pcall(T.Suggest, T, id, text, prio or 2)
  elseif DEFAULT_CHAT_FRAME then
    DEFAULT_CHAT_FRAME:AddMessage("|cff44dd88TreeAI|r " .. text)
  end
end

local function clear(id)
  if not lastSent[id] then return end
  lastSent[id] = nil
  if M.core and M.Clear then pcall(M.Clear, M, id) return end
  local T = core()
  if not T then return end
  local f = T.ClearSuggestion or T.Unsuggest or T.RemoveSuggestion
  if f then pcall(f, T, id) end
end

local function inCombat()
  return (InCombatLockdown and InCombatLockdown()) or (UnitAffectingCombat and UnitAffectingCombat("player")) or false
end

local function charKey()
  local n = UnitName and UnitName("player") or "?"
  local r = GetRealmName and GetRealmName() or "?"
  local T = core()
  if T and T.CharKey then
    local ok, k = pcall(T.CharKey, T)
    if ok and k and k ~= "?" then return k end
  end
  return ((tostring(n) .. "-" .. tostring(r)):gsub("%s", ""))
end

local function store(section)
  -- avec le cœur : TreeAIDB.chars[perso].summary[section] (schéma v1 de l'agent B)
  local T = core()
  if T and T.db and T.Summary then
    local ok, s = pcall(T.Summary, T)
    if ok and type(s) == "table" then
      s[section] = type(s[section]) == "table" and s[section] or {}
      return s[section]
    end
  end
  if type(TreeAIDB) ~= "table" then return nil end
  -- sans le cœur : même chemin, créé à la main
  TreeAIDB.chars = type(TreeAIDB.chars) == "table" and TreeAIDB.chars or {}
  local k = charKey()
  local c = type(TreeAIDB.chars[k]) == "table" and TreeAIDB.chars[k] or {}
  TreeAIDB.chars[k] = c
  c.summary = type(c.summary) == "table" and c.summary or {}
  c.summary[section] = type(c.summary[section]) == "table" and c.summary[section] or {}
  return c.summary[section]
end

local function later(sec, fn)   -- répartiteur du cœur (jamais C_Timer directement)
  local T = core()
  if T and T.After then T:After(sec, fn) else fn() end
end

local function money(c)   -- format commun (T.Fmt) : « 1 po 20 pa », « 35 pa », « 8 pc »
  local T = core()
  if T and T.Fmt and T.Fmt.Money then return T.Fmt.Money(c) end
  c = math.floor(tonumber(c) or 0)
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  if g > 0 then return s > 0 and (g .. " po " .. s .. " pa") or (g .. " po") end
  if s > 0 then return s .. " pa" end
  return k .. " pc"
end
M.FormatMoney = money

-- ---------------------------------------------------------------- API sacs (C_Container ou ancienne API)
local CC = _G.C_Container
local NBAGS = tonumber(_G.NUM_BAG_SLOTS) or 4

local function API()
  local T = core()
  return T and type(T.API) == "table" and T.API or nil
end

local function numSlots(b)
  local A = API()
  if A and A.BagSlots then return A.BagSlots(b) or 0 end
  if CC and CC.GetContainerNumSlots then return CC.GetContainerNumSlots(b) or 0 end
  if GetContainerNumSlots then return GetContainerNumSlots(b) or 0 end
  return 0
end

local function numFree(b)
  local A = API()
  if A and A.BagFree then return A.BagFree(b) end
  if CC and CC.GetContainerNumFreeSlots then return CC.GetContainerNumFreeSlots(b) end
  if GetContainerNumFreeSlots then return GetContainerNumFreeSlots(b) end
  return 0, 0
end

local function getItemInfo(link)
  local A = API()
  if A and A.ItemInfo then return A.ItemInfo(link) end
  if C_Item and C_Item.GetItemInfo then return C_Item.GetItemInfo(link) end
  if GetItemInfo then return GetItemInfo(link) end
end

-- quality, link, count, locked, noValue, itemID
local function slotInfo(b, s)
  local q, link, count, locked, noValue, id
  local A = API()
  if A and A.BagItem then
    local i = A.BagItem(b, s)
    if not i then return nil end
    q, link, count, locked, noValue, id = i.quality, i.link, i.count, i.locked, i.noValue, i.itemID
  elseif CC and CC.GetContainerItemInfo then
    local i = CC.GetContainerItemInfo(b, s)
    if not i then return nil end
    q, link, count, locked, noValue, id = i.quality, i.hyperlink, i.stackCount, i.isLocked, i.hasNoValue, i.itemID
  elseif GetContainerItemInfo then
    local _tex, c, l, qq, _r, _lo, lk, _f, nv, iid = GetContainerItemInfo(b, s)
    if not lk then return nil end
    q, link, count, locked, noValue, id = qq, lk, c, l, nv, iid
  else
    return nil
  end
  if (q == nil or q < 0) and link then q = select(3, getItemInfo(link)) end
  return q, link, count or 1, locked, noValue, id
end

local function useSlot(b, s)
  local A = API()
  if A and A.UseContainerItem then A.UseContainerItem(b, s) return true end
  if CC and CC.UseContainerItem then CC.UseContainerItem(b, s) return true end
  if UseContainerItem then UseContainerItem(b, s) return true end
  return false
end

-- ---------------------------------------------------------------- analyses
-- Places libres dans les sacs « génériques » (bagType 0 ; carquois / gibernes ignorés).
function M.ScanBags()
  local free, total = 0, 0
  for b = 0, NBAGS do
    local n = numSlots(b)
    if n > 0 then
      local f, bagType = numFree(b)
      if (bagType or 0) == 0 then
        free, total = free + (f or 0), total + n
      end
    end
  end
  return free, total
end

--- (5.0 BAGS) l'assistant d'inventaire (Sacs) quand il est allumé : UN seul avis « vendre / garder » pour tout l'addon
local function brain()
  local T = core()
  local B = T and T.Bags
  if type(B) == "table" and B._active and type(B.Verdict) == "function" then return B end
  return nil
end
M.Brain = brain

-- Liste des objets gris vendables : { {bag, slot, value, link}, ... }, valeur totale.
-- (5.0 BAGS) par T.Bags.Verdict : un gris qu'un gardien réclame (objet de quête, marqué « garder ») n'y est pas.
function M.GreyItems()
  local B = brain()
  if B and type(B.GreyItems) == "function" then
    local ok, l, v = pcall(B.GreyItems)
    if ok and type(l) == "table" then return l, tonumber(v) or 0 end
  end
  local list, value = {}, 0
  for b = 0, NBAGS do
    for s = 1, numSlots(b) do
      local q, link, count, locked, noValue = slotInfo(b, s)
      if q == 0 and link and not locked and not noValue then
        local price = select(11, getItemInfo(link)) or 0
        if price > 0 then
          list[#list + 1] = { bag = b, slot = s, value = price * (count or 1), link = link }
          value = value + price * (count or 1)
        end
      end
    end
  end
  return list, value
end

-- Matériaux d'artisanat (classe d'objet 7 = Artisanat) : { [nom] = quantité }
function M.Materials()
  local mats = {}
  for b = 0, NBAGS do
    for s = 1, numSlots(b) do
      local _q, link, count = slotInfo(b, s)
      if link then
        local name, _, _, _, _, _, _, _, _, _, _, classID = getItemInfo(link)
        if name and classID == 7 then mats[name] = (mats[name] or 0) + (count or 1) end
      end
    end
  end
  return mats
end

-- Durabilité minimale de l'équipement en % (nil si aucune pièce n'a de durabilité).
function M.Durability()
  if not GetInventoryItemDurability then return nil, 0 end
  local minPct, broken
  broken = 0
  for slot = 1, 18 do
    local cur, max = GetInventoryItemDurability(slot)
    if cur and max and max > 0 then
      local p = math.floor(cur * 100 / max)
      if not minPct or p < minPct then minPct = p end
      if cur == 0 then broken = broken + 1 end
    end
  end
  return minPct, broken
end

local function opts()
  local o = type(M.db) == "table" and M.db or {}
  return {
    minFree = tonumber(o.bagsMinFree) or 2,
    durWarn = tonumber(o.durabilityWarn) or 30,
  }
end

-- quoi jeter d'abord (infobulle de « Sacs pleins ») : ce qui vaut le moins par case chez le marchand, le gris
-- d'abord ; jamais les objets de quête, consommables, sacs, munitions, carquois (pur, testé)
local KEEP_CLASS = { [0] = true, [1] = true, [6] = true, [11] = true, [12] = true }
function M.Cheapest(items, n)
  local list = {}
  for _, it in ipairs(items or {}) do
    if it.name and not KEEP_CLASS[it.classID or -1] and (it.quality or 1) <= 2 then list[#list + 1] = it end
  end
  table.sort(list, function(a, b)
    local ga, gb = (a.quality or 1) == 0, (b.quality or 1) == 0
    if ga ~= gb then return ga end
    if a.value ~= b.value then return a.value < b.value end
    return a.name < b.name
  end)
  local out = {}
  for i = 1, math.min(n or 3, #list) do out[i] = list[i] end
  return out
end
local dropTipRaw
local dropMemo = { at = -100 }
--- infobulle « à jeter d'abord » : relue quand le nombre de cases libres change, sinon au plus toutes les 60 s
--- (le butin change les sacs sans cesse ; les 3 objets les moins chers, eux, changent rarement)
function M.DropTip(free)
  local t = (GetTime and GetTime()) or 0
  if dropMemo.free == free and t - dropMemo.at < 60 then return dropMemo.r end
  dropMemo.free, dropMemo.at, dropMemo.r = free, t, dropTipRaw()
  return dropMemo.r
end
dropTipRaw = function()
  local items = {}
  -- jamais la nourriture du familier (le chasseur la garde exprès)
  local TA = core()
  local H = TA and TA.Hunter
  local petFood = H and type(H.BestFood) == "function" and select(2, pcall(H.BestFood, H)) or nil
  for b = 0, NBAGS do
    for s = 1, numSlots(b) do
      local q, link, count, _, _, id = slotInfo(b, s)
      if link and not (petFood and id == petFood) then
        local name, _, _, _, _, _, _, _, _, _, price, classID = getItemInfo(link)
        -- sans prix de vente (pierre de foyer, clés, objets liés au joueur) : jamais « à jeter »
        if name and (tonumber(price) or 0) > 0 then
          items[#items + 1] = { name = name, value = (tonumber(price) or 0) * (count or 1), unit = tonumber(price) or 0,
            count = count or 1, quality = q, classID = classID }
        end
      end
    end
  end
  local list = M.Cheapest(M.GroupByName(items), 3)
  if #list == 0 then return nil end
  local parts = {}
  for _, it in ipairs(list) do
    parts[#parts + 1] = (it.count or 1) > 1 and ("%s ×%d (%s pièce)"):format(it.name, it.count, money(it.unit or 0))
      or ("%s (%s)"):format(it.name, money(it.value))
  end
  return { "À jeter d'abord (vaut le moins par case) :", table.concat(parts, ", ") }
end
--- une entrée par objet, pas par case (pur) : { name, value = la case la moins chère, unit, count, quality, classID }
function M.GroupByName(items)
  local by, out = {}, {}
  for _, it in ipairs(items or {}) do
    local e = it.name and by[it.name]
    if e then
      e.count = e.count + (it.count or 1)
      if (it.value or 0) < (e.value or 0) then e.value = it.value end
    elseif it.name then
      e = { name = it.name, value = it.value or 0, unit = it.unit, count = it.count or 1, quality = it.quality, classID = it.classID }
      by[it.name] = e
      out[#out + 1] = e
    end
  end
  return out
end

function M.CheckBags()
  local free, total = M.ScanBags()
  local o = opts()
  local B = brain()
  if B and not (type(B.db) == "table" and B.db.warn == false) then
    clear("inv_bags")   -- (5.0 BAGS) « 2 places : vends / jette X » : dit une fois, par Sacs (le moins cher surligné)
  elseif total > 0 and free == 0 then
    suggest("inv_bags", "Sacs pleins : fais de la place pour ramasser le butin", 3, nil, M.DropTip(free))
  elseif total > 0 and free <= o.minFree then
    suggest("inv_bags", ("Fais de la place : plus que %d case%s libre%s"):format(free, free > 1 and "s" or "", free > 1 and "s" or ""),
      2, 300, M.DropTip(free))
  elseif free > o.minFree + 1 then
    clear("inv_bags")
  end
  local d = store("inventory")
  if d then d.free, d.total = free, total end
  return free, total
end

function M.CheckDurability()
  local p, broken = M.Durability()
  local d = store("inventory")
  if d then d.durMin, d.broken = p, broken end
  if not p then return end
  -- le prix réel s'affiche chez un marchand qui répare (« Répare ici : … ») ; ailleurs, pas d'estimation inventée
  if broken > 0 then
    suggest("inv_repair", "Répare au plus vite : équipement cassé (rouge)", 3, 180)
  elseif p <= opts().durWarn then
    suggest("inv_repair", ("Répare au prochain marchand : équipement à %d %%"):format(p), 2, 600)
  elseif p > 60 then
    clear("inv_repair")
  end
end

-- ---------------------------------------------------------------- nourriture (et boissons des lanceurs de sorts)
-- listes du client (Data/Consumables) : pas de devinette sur le nom
local foodSet, waterSet
local function consumableSets()
  if foodSet then return foodSet, waterSet end
  foodSet, waterSet = {}, {}
  local T = core()
  local C = T and T.Data and T.Data.Consumables
  for _, r in ipairs(C and C.food or {}) do if type(r) == "table" and r[1] then foodSet[r[1]] = r end end
  for _, r in ipairs(C and C.water or {}) do if type(r) == "table" and r[1] then waterSet[r[1]] = r end end
  return foodSet, waterSet
end
local function linkID(link) return tonumber(type(link) == "string" and link:match("item:(%d+)") or nil) end
--- nourriture et boissons dans les sacs -> repas, boissons (mémorisé jusqu'au prochain changement de sacs)
local foodCountRaw
function M.FoodCount()
  local A = API()
  if A and A.BagMemo then return A.BagMemo("Inventory.FoodCount", foodCountRaw) end
  return foodCountRaw()
end
foodCountRaw = function()
  local fs, ws = consumableSets()
  local food, drink = 0, 0
  for b = 0, NBAGS do
    for s = 1, numSlots(b) do
      local _, link, count, _, _, id = slotInfo(b, s)
      id = id or linkID(link)
      if id and fs[id] then food = food + (count or 1) elseif id and ws[id] then drink = drink + (count or 1) end
    end
  end
  return food, drink
end
--- lanceurs de sorts seulement : le chasseur ne s'arrête pas pour boire (mana des chasseurs = pas de rappel)
local function needsDrink()
  local _, cls = UnitClass and UnitClass("player")
  local pt = UnitPowerType and UnitPowerType("player")
  return pt == 0 and cls ~= "HUNTER"
end
--- texte (pur) : repas / boissons qui restent -> « Refais le plein : plus que 3 repas » ou nil
function M.FoodText(food, drink, wantDrink)
  local low = {}
  if (food or 0) < 5 then low[#low + 1] = (food or 0) == 0 and "plus de repas" or ("plus que " .. food .. " repas") end
  if wantDrink and (drink or 0) < 5 then
    low[#low + 1] = (drink or 0) == 0 and "plus de boisson" or ("plus que " .. drink .. " boisson" .. (drink > 1 and "s" or ""))
  end
  if #low == 0 then return nil end
  return "Refais le plein chez un marchand : " .. table.concat(low, " et ")
end
local lastFood = -100
function M.CheckFood(force)
  if inCombat() or (type(M.db) == "table" and M.db.food == false) then return end
  local t = (GetTime and GetTime()) or 0
  if not force and t - lastFood < 60 then return end   -- un repas de moins ne presse pas : relu au plus chaque minute
  lastFood = t
  local food, drink = M.FoodCount()
  local text = M.FoodText(food, drink, needsDrink())
  if text then suggest("inv_food", text, 1, 1500) else clear("inv_food") end
end
--- chez le marchand : combien en prendre pour 1 h de jeu, au rythme MESURÉ par Stats (sinon rien)
function M.CheckFoodBuy()
  if type(M.db) == "table" and M.db.food == false then return end
  local T = core()
  local St = T and T.Stats
  if not (St and St.PerHour) then return end
  local fs, ws = consumableSets()
  local lvl = (UnitLevel and UnitLevel("player")) or 1
  local food, drink = M.FoodCount()
  local n = (GetMerchantNumItems and GetMerchantNumItems()) or 0
  for _, k in ipairs({ { "food", fs, food, "repas" }, needsDrink() and { "water", ws, drink, "boissons" } or nil }) do
    local okR, perH = pcall(St.PerHour, St, k[1])
    perH = okR and tonumber(perH) or nil
    local need = perH and (math.ceil(perH) - k[3]) or 0
    if need >= 3 then
      local best, bestV
      for i = 1, math.min(n, 60) do
        local id = (GetMerchantItemID and GetMerchantItemID(i)) or linkID(GetMerchantItemLink and GetMerchantItemLink(i))
        local r = id and k[2][id]
        if r and (r[2] or 1) <= lvl and (not bestV or (r[3] or 0) > bestV) then best, bestV = id, r[3] or 0 end
      end
      if best then
        local name = getItemInfo(best) or ("objet " .. best)
        local amount = math.ceil(need / 5) * 5
        suggest("inv_buy_" .. k[1], ("Achète %d × %s : 1 h de jeu à ton rythme (tu en as %d)"):format(amount, name, k[3]), 2, 300,
          ("Mesuré par Stats : environ %d %s par heure de jeu."):format(math.floor(perH + 0.5), k[4]))
      end
    end
  end
end

-- ---------------------------------------------------------------- vente des gris (clic explicite)
local SELL_PER_CLICK = 12 -- = taille du rachat : tout reste récupérable chez le marchand
local button

local function merchantOpen()
  return MerchantFrame and MerchantFrame.IsShown and MerchantFrame:IsShown()
end

local function refreshButton()
  if not button then return end
  local list, value = M.GreyItems()
  if #list == 0 then
    button:SetText("Aucun gris")
    button:Disable()
  else
    button:SetText(("Vendre gris (%s)"):format(money(value)))
    button:Enable()
  end
end

-- Appelé UNIQUEMENT depuis OnClick (événement matériel). Ne vend rien sinon.
function M.SellGreys()
  if inCombat() then suggest("inv_sell", "Pas de vente en combat.", 1, 10) return 0 end
  if not merchantOpen() then return 0 end
  local list, value = M.GreyItems()
  local sold, gained = 0, 0
  for i = 1, math.min(#list, SELL_PER_CLICK) do
    local it = list[i]
    if useSlot(it.bag, it.slot) then sold, gained = sold + 1, gained + it.value end
  end
  if sold > 0 then   -- jamais dans le chat : le bouton dit ce qui reste (« Vendre gris (…) » / « Aucun gris »)
    M.lastSold = { n = sold, money = gained, rest = #list - sold }
  end
  clear("inv_bags")
  later(0.5, refreshButton)
  return sold, value
end

local function ensureButton()
  if button or not CreateFrame or not MerchantFrame then return button end
  local ok, b = pcall(CreateFrame, "Button", nil, MerchantFrame, "UIPanelButtonTemplate")
  if not ok or not b then return nil end
  button = b
  b:SetSize(150, 22)
  b:SetPoint("TOPRIGHT", MerchantFrame, "TOPRIGHT", -40, -40)
  b:SetScript("OnClick", function() M.SellGreys() end)
  b:SetScript("OnEnter", function(self)
    if GameTooltip then
      GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
      GameTooltip:SetText("Vend jusqu'à 12 objets gris par clic.\nRachetables chez le marchand.")
      GameTooltip:Show()
    end
  end)
  b:SetScript("OnLeave", function() if GameTooltip then GameTooltip:Hide() end end)
  return button
end

local function onMerchantShow()
  -- (5.0 BAGS) la carte « Vendre N objets » sous la fenêtre du marchand remplace ce bouton (jamais deux boutons de vente)
  local B = brain()
  if B and not (type(B.db) == "table" and B.db.card == false) then
    if button then button:Hide() end
  elseif ensureButton() then button:Show() refreshButton() end
  pcall(M.CheckFoodBuy)
  local p = M.Durability()
  if CanMerchantRepair and CanMerchantRepair() and GetRepairAllCost and p and p < 80 then
    local cost = GetRepairAllCost() or 0
    if cost > 0 then
      local d = store("inventory")
      if d then d.repairCost = cost end
      suggest("inv_repair", ("Répare ici : %s."):format(money(cost)), 2, 30)
    end
  end
end

-- ---------------------------------------------------------------- butin de session
local lootPatterns
local function buildLootPatterns()
  lootPatterns = {}
  for _, gs in ipairs({ _G.LOOT_ITEM_SELF_MULTIPLE, _G.LOOT_ITEM_PUSHED_SELF_MULTIPLE, _G.LOOT_ITEM_SELF, _G.LOOT_ITEM_PUSHED_SELF }) do
    if type(gs) == "string" then
      local p = gs:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1")
      p = p:gsub("%%%%s", "(.+)"):gsub("%%%%d", "(%%d+)")
      lootPatterns[#lootPatterns + 1] = "^" .. p .. "$"
    end
  end
end

local session = { loot = 0, started = nil }
function M.OnLootMessage(msg)
  if type(msg) ~= "string" then return end
  if not lootPatterns then buildLootPatterns() end
  for _, p in ipairs(lootPatterns) do
    local link, n = msg:match(p)
    if link then
      local price = select(11, getItemInfo(link)) or 0
      session.loot = session.loot + price * (tonumber(n) or 1)
      local d = store("inventory")
      if d then d.sessionLoot = session.loot end
      return
    end
  end
end

-- ---------------------------------------------------------------- résumé pour TreeAI (SavedVariables)
function M.SaveSummary()
  local d = store("inventory")
  if not d then return end
  d.free, d.total = M.ScanBags()
  local greys, gv = M.GreyItems()
  d.greyCount, d.greyValue = #greys, gv
  d.durMin, d.broken = M.Durability()
  d.money = GetMoney and GetMoney() or nil
  d.sessionLoot = session.loot
  d.mats = M.Materials()
  d.updated = time and time() or nil
end

-- ---------------------------------------------------------------- événements
local pending = false
local function bagsChanged()
  if pending then return end
  pending = true
  later(0.7, function()
    pending = false
    M.CheckBags()
    M.CheckFood()
    if merchantOpen() then refreshButton() end
  end)
end

local handlers = {
  PLAYER_ENTERING_WORLD = function() later(3, function() M.CheckBags() M.CheckDurability() end) end,
  BAG_UPDATE = bagsChanged,
  BAG_UPDATE_DELAYED = bagsChanged,
  UPDATE_INVENTORY_DURABILITY = function() M.CheckDurability() end,
  MERCHANT_SHOW = onMerchantShow,
  MERCHANT_CLOSED = function() if button then button:Hide() end clear("inv_buy_food") clear("inv_buy_water") end,
  CHAT_MSG_LOOT = function(msg) M.OnLootMessage(msg) end,
  PLAYER_LOGOUT = function() pcall(M.SaveSummary) end,
}
M.handlers = handlers

if CreateFrame then
  local f = CreateFrame("Frame")
  for ev in pairs(handlers) do pcall(f.RegisterEvent, f, ev) end
  f:SetScript("OnEvent", function(_, ev, ...)
    local h = handlers[ev]
    if M.core and M.IsEnabled and not M:IsEnabled() then return end
    if h then
      local ok, err = pcall(h, ...)
      if not ok and core() and core().Debug then pcall(core().Debug, core(), "Inventory: " .. tostring(err)) end
    end
  end)
end

do
  local T = core()
  if T and T.RegisterModule then pcall(T.RegisterModule, T, "Inventory", M) end
end

function M:AutoRefresh() pcall(M.CheckBags); pcall(M.CheckDurability); pcall(M.CheckFood) end
