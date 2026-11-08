-- TreeAI — module Inventaire (agent F)
-- Sacs presque pleins, réparation, vente des objets gris (UNIQUEMENT sur clic du bouton, marchand
-- ouvert, hors combat : jamais automatique), butin de la session, matériaux de métier.
-- Lua 5.1, aucune globale créée. Toutes les API WoW sont testées avant usage (Classic / Forever).
local _, ns = ...
ns = ns or {}

local M = {
  name = "Inventory", title = "Inventaire",
  defaults = { enabled = true, bagsMinFree = 2, durabilityWarn = 30 },
  options = {
    { key = "bagsMinFree", type = "range", label = "Alerte sacs : places libres", min = 0, max = 10, step = 1 },
    { key = "durabilityWarn", type = "range", label = "Alerte réparation (%)", min = 10, max = 60, step = 5 },
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

local function later(sec, fn)
  if C_Timer and C_Timer.After then C_Timer.After(sec, fn) else fn() end
end

local function money(c)
  c = math.floor(tonumber(c) or 0)
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  local t = {}
  if g > 0 then t[#t + 1] = g .. " po" end
  if s > 0 then t[#t + 1] = s .. " pa" end
  if k > 0 or #t == 0 then t[#t + 1] = k .. " pc" end
  return table.concat(t, " ")
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

-- Liste des objets gris vendables : { {bag, slot, value, link}, ... }, valeur totale.
function M.GreyItems()
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

function M.CheckBags()
  local free, total = M.ScanBags()
  local o = opts()
  if total > 0 and free == 0 then
    suggest("inv_bags", "Sacs pleins ! Le butin est perdu : vends ou détruis du gris.", 3)
  elseif total > 0 and free <= o.minFree then
    suggest("inv_bags", ("Sacs presque pleins (%d place%s)."):format(free, free > 1 and "s" or ""), 2, 300)
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
  -- coût estimé (module Économie) dans l'infobulle : une seule ligne « répare » dans tout l'addon
  local T = core()
  local E = T and T.modules and T.modules.Economy
  local est = E and E.RepairEstimate and select(2, pcall(E.RepairEstimate))
  local tip = (type(est) == "number" and est > 0) and ("Coût estimé : " .. money(est) .. ".") or nil
  if broken > 0 then
    suggest("inv_repair", "Répare au plus vite : équipement cassé (rouge)", 3, 180, tip)
  elseif p <= opts().durWarn then
    suggest("inv_repair", ("Répare au prochain marchand : équipement à %d %%"):format(p), 2, 600, tip)
  elseif p > 60 then
    clear("inv_repair")
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
  if sold > 0 and DEFAULT_CHAT_FRAME then
    local rest = #list - sold
    DEFAULT_CHAT_FRAME:AddMessage(("|cff44dd88TreeAI|r %d gris vendu%s (%s).%s"):format(
      sold, sold > 1 and "s" or "", money(gained), rest > 0 and " Reclique pour le reste." or ""))
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
  if ensureButton() then button:Show() refreshButton() end
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
    if merchantOpen() then refreshButton() end
  end)
end

local handlers = {
  PLAYER_ENTERING_WORLD = function() later(3, function() M.CheckBags() M.CheckDurability() end) end,
  BAG_UPDATE = bagsChanged,
  BAG_UPDATE_DELAYED = bagsChanged,
  UPDATE_INVENTORY_DURABILITY = function() M.CheckDurability() end,
  MERCHANT_SHOW = onMerchantShow,
  MERCHANT_CLOSED = function() if button then button:Hide() end end,
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

function M:AutoRefresh() pcall(M.CheckBags); pcall(M.CheckDurability) end
