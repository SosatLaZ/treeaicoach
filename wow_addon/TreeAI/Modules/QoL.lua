-- TreeAI — module QoL « Confort » (agent REPLACE, 4.0.1) : ce que Leatrix Plus faisait pour le joueur, en légal,
-- chaque chose une option :
--  * vendre les objets gris + réparer chez le marchand (Maj enfoncée = pause) : la barre de quête (Quick) le fait déjà
--    quand elle est allumée avec ses options « vendre / réparer » → jamais deux fois ; sinon ici, même règle des gris
--    (Quick.GreyItems / Inventaire), par paquets de 10 toutes les 0,3 s tant que la fenêtre du marchand est ouverte,
--    hors combat (UseContainerItem marchand ouvert, RepairAllItems si l'or suffit) ;
--  * butin instantané : avec le butin automatique du jeu, chaque emplacement est pris tout de suite (LootSlot à
--    LOOT_READY, comme Leatrix « Faster auto loot ») ; jamais de confirmation de butin lié acceptée à ta place ;
--  * butin automatique du jeu (CVar autoLootDefault) et recul de caméra maximal (cameraDistanceMaxZoomFactor) : un clic
--    dans les options (réglages du jeu, gardés par le jeu), hors combat.
-- Pas repris (exprès) : masquer les messages d'erreur, déplacer ou masquer des cadres de Blizzard (blocages / taint),
-- accepter des invitations ou des résurrections (fenêtres protégées). Aucune sortie dans le chat : le résultat d'une
-- vente s'affiche brièvement en haut de l'écran (comme une réponse à un clic). Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local API = T.API or {}

local Q = T:RegisterModule("QoL", {
  title = "Confort",
  description = "Ce que Leatrix Plus faisait pour toi, en légal : vendre les gris et réparer, butin instantané, "
    .. "butin automatique du jeu, recul de caméra.",
  defaults = { enabled = true, sellRepair = true, fastLoot = true },
  charDefaults = {},
  options = {
    { key = "sellRepair", type = "toggle", label = "Vendre les objets gris et réparer chez le marchand (Maj = pause)", section = "Marchand" },
    { key = "fastLoot", type = "toggle", label = "Butin instantané (avec le butin automatique du jeu)", section = "Butin" },
    { key = "autoLootBtn", type = "button", label = "Butin automatique du jeu : activer / couper", section = "Butin",
      onClick = function() local m = T.QoL if m and m.ToggleAutoLoot then m.ToggleAutoLoot() end end },
    { key = "cameraBtn", type = "button", label = "Caméra : recul maximal / normal", section = "Caméra",
      onClick = function() local m = T.QoL if m and m.ToggleCamera then m.ToggleCamera() end end },
  },
})
T.QoL = Q
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[4]   -- « Sacs, or, métiers »
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "QoL" then has = true end end
    if not has then table.insert(fam.mods, 1, "QoL") end
  end
end

local floor, min = math.floor, math.min
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function opt(k) return Q.db and Q.db[k] ~= false end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function now() return try(g("GetTime")) or 0 end
local function after(s, fn) if T.After then T:After(s, fn) else fn() end end
local function shift() return try(g("IsShiftKeyDown")) and true or false end
local function money() return num(API.Money and API.Money() or try(g("GetMoney"))) or 0 end
local function Fmoney(c) return (T.Fmt and T.Fmt.Money and T.Fmt.Money(c)) or (floor((c or 0) / 100) .. " pa") end
local function note(msg) if T.ClickNote then T:ClickNote(msg) end end
local function cvar(k)
  local CV = g("C_CVar")
  local f = (type(CV) == "table" and CV.GetCVar) or g("GetCVar")
  local v = try(f, k)
  if secret(v) then return nil end
  return v ~= nil and tostring(v) or nil
end
local function setCVar(k, v)
  local f = g("SetCVar")
  if type(f) ~= "function" then local CV = g("C_CVar") f = type(CV) == "table" and CV.SetCVar or nil end
  return f and pcall(f, k, v) or false
end

-- ===================================================================== marchand : gris + réparation
Q.stats = { sold = 0, repaired = 0, looted = 0 }
local merchantOpen = false
--- la barre de quête s'en charge déjà ? (allumée + ses options vendre / réparer) → on ne fait rien (jamais deux fois)
function Q.QuickDoesIt()
  local K = T.modules and T.modules.Quick
  return (K and K._active and type(K.db) == "table" and (K.db.autoSell ~= false or K.db.autoRepair ~= false)) and true or false
end
local function greyItems()
  local K = T.Quick
  if K and type(K.GreyItems) == "function" then
    local list, value = try(K.GreyItems)
    if type(list) == "table" then return list, tonumber(value) or 0 end
  end
  local I = T.modules and T.modules.Inventory
  if I and type(I.GreyItems) == "function" then
    local list, value = try(I.GreyItems)
    if type(list) == "table" then return list, tonumber(value) or 0 end
  end
  local list, value = {}, 0
  if API.IterBags then
    for bag, slot, it in API.IterBags() do
      if it.quality == 0 and not it.noValue then
        local sell = num(select(11, try(API.ItemInfo, it.link or it.itemID))) or 0
        if sell > 0 then
          list[#list + 1] = { bag = bag, slot = slot, value = sell * (it.count or 1) }
          value = value + sell * (it.count or 1)
        end
      end
    end
  end
  return list, value
end
Q.GreyItems = greyItems
local function useSlot(bag, slot)
  local CC = g("C_Container")
  local f = (type(CC) == "table" and CC.UseContainerItem) or g("UseContainerItem")
  return f and pcall(f, bag, slot) or false
end
local function merchantShown()
  local mf = g("MerchantFrame")
  if type(mf) == "table" and type(mf.IsShown) == "function" then return try(mf.IsShown, mf) and true or false end
  return merchantOpen
end

--- vend les gris (paquets de 10 / 0,3 s) + répare si l'or suffit ; jamais en combat ni marchand fermé
function Q.SellAndRepair()
  if inCombat() or not merchantOpen then return nil end
  local parts = {}
  if try(g("CanMerchantRepair")) then
    local cost = num(try(g("GetRepairAllCost"))) or 0
    if cost > 0 and cost <= money() then
      try(g("RepairAllItems"))
      Q.stats.repaired = Q.stats.repaired + 1
      parts[#parts + 1] = "Réparé : " .. Fmoney(cost)
    elseif cost > 0 then
      parts[#parts + 1] = "Réparation : pas assez d'or (" .. Fmoney(cost) .. ")"
    end
  end
  local list, value = greyItems()
  if #list > 0 then
    local i = 0
    local function batch()
      if not merchantOpen or inCombat() then return end
      for _ = 1, 10 do
        i = i + 1
        local it = list[i]
        if not it then break end
        if useSlot(it.bag, it.slot) then Q.stats.sold = Q.stats.sold + 1 end
      end
      if list[i + 1] then after(0.3, batch) end
    end
    batch()
    parts[#parts + 1] = fmt("Vendu : %d objet%s gris (%s)", #list, #list > 1 and "s" or "", Fmoney(value))
  end
  if #parts > 0 then note(table.concat(parts, " · ")) end
  return parts
end
local function onMerchant()
  if merchantOpen then return end                  -- MERCHANT_SHOW et l'événement d'interaction : un seul passage
  merchantOpen = true
  if not opt("sellRepair") or shift() or Q.QuickDoesIt() then return end
  -- un instant plus tard : la fenêtre du marchand est vraiment ouverte (et ses données arrivées)
  after(0.15, function() if merchantShown() then Q.SellAndRepair() end end)
end

-- ===================================================================== butin instantané
local lastLoot = -1
local function autoLooting(autoloot)
  if autoloot ~= nil and not secret(autoloot) then return autoloot and true or false end
  local on = cvar("autoLootDefault") == "1"
  local mod = try(g("IsModifiedClick"), "AUTOLOOTTOGGLE") and true or false
  return on ~= mod
end
--- LOOT_READY (autoloot) : chaque emplacement pris tout de suite si le jeu allait de toute façon tout ramasser
function Q.FastLoot(autoloot)
  if not opt("fastLoot") or not autoLooting(autoloot) then return 0 end
  local t = now()
  if t - lastLoot < 0.3 then return 0 end          -- un seul passage par fenêtre de butin
  lastLoot = t
  local slot = g("LootSlot")
  local n = num(try(g("GetNumLootItems"))) or 0
  if type(slot) ~= "function" or n <= 0 then return 0 end
  local k = 0
  for i = min(n, 40), 1, -1 do
    if pcall(slot, i) then k = k + 1 end
  end
  Q.stats.looted = Q.stats.looted + k
  return k
end

-- ===================================================================== réglages du jeu (un clic, hors combat)
function Q.ToggleAutoLoot()
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return nil end
  local on = cvar("autoLootDefault") == "1"
  setCVar("autoLootDefault", on and "0" or "1")
  note(on and "Butin automatique du jeu : coupé" or "Butin automatique du jeu : activé")
  return not on
end
Q.CAMERA_MAX, Q.CAMERA_NORMAL = "2.6", "1.9"
function Q.ToggleCamera()
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return nil end
  local v = num(cvar("cameraDistanceMaxZoomFactor")) or 1.9
  local maxed = v >= 2.55
  setCVar("cameraDistanceMaxZoomFactor", maxed and Q.CAMERA_NORMAL or Q.CAMERA_MAX)
  note(maxed and "Caméra : recul normal" or "Caméra : recul maximal")
  return not maxed
end
function Q.State()
  return { autoLoot = cvar("autoLootDefault") == "1", camera = (num(cvar("cameraDistanceMaxZoomFactor")) or 1.9) >= 2.55,
    quick = Q.QuickDoesIt() }
end

-- ===================================================================== cycle de vie
function Q:OnEnable()
  self:RegisterEvent("MERCHANT_SHOW", function() onMerchant() end)
  self:RegisterEvent("MERCHANT_CLOSED", function() merchantOpen = false end)
  self:RegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", function(_, _, kind)
    local E = g("Enum") and G.Enum.PlayerInteractionType
    if type(E) == "table" and kind ~= nil and not secret(kind) and kind == E.Merchant then onMerchant() end
  end)
  self:RegisterEvent("LOOT_READY", function(_, _, autoloot) Q.FastLoot(autoloot) end)
end
function Q:OnDisable() merchantOpen = false end
