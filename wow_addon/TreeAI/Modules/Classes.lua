-- TreeAI — rappels de buffs pour les 8 autres classes (wow-D).
-- INFORMATIF UNIQUEMENT : on signale un buff manquant, on ne lance rien.
-- Contrat cœur : TreeAI:RegisterModule(name, tbl), TreeAI:Suggest(id, text, prio). Options :
-- Réglages : M.db (= TreeAIDB.modules.Classes) = { enabled = true, disabled = { ["ROGUE.poison"] = true } }

local TreeAI = _G.TreeAI
if type(TreeAI) ~= "table" then return end

local M = { name = "Classes" }

-- Chaque règle : key, spells = IDs de tous les rangs (le 1er sert au nom localisé),
-- kind = "buff" (aura sur soi) | "weapon" (enchantement d'arme) | "shards" | "aura" (aura de paladin),
-- when = "ooc" (hors combat) | "combat" | "any", minLevel, text (%s = nom du sort).
local RULES = {
  WARRIOR = {
    { key = "shout", kind = "buff", when = "combat", text = "Pas de « %s » : lance ton cri (PA pour tout le groupe).",
      spells = { 6673, 5242, 6192, 11549, 11550, 11551, 25289 } },
  },
  ROGUE = {
    { key = "poison", kind = "weapon", hand = "main", when = "ooc", minLevel = 20, always = true,
      text = "Arme principale sans poison : applique un poison (Instantané en solo).", spells = { 8679 } },
    { key = "poisonoh", kind = "weapon", hand = "off", when = "ooc", minLevel = 20, always = true,
      text = "Main gauche sans poison (Mortel ou Affaiblissant).", spells = { 8679 } },
  },
  MAGE = {
    { key = "armor", kind = "buff", when = "any",
      text = "Pas d'armure : « %s » (Armure de givre/de glace en solo, du mage en groupe).",
      spells = { 168, 7300, 7301, 7302, 7320, 10219, 10220, 6117, 22782, 22783 } },
    { key = "int", kind = "buff", when = "ooc", text = "« %s » manquante (plus de mana).",
      spells = { 1459, 1460, 1461, 10156, 10157, 23028 } },
  },
  PRIEST = {
    { key = "fort", kind = "buff", when = "ooc", text = "« %s » manquant : endurance pour toi et le groupe.",
      spells = { 1243, 1244, 1245, 2791, 10937, 10938, 21562, 21564 } },
    { key = "innerfire", kind = "buff", when = "ooc", minLevel = 12, text = "« %s » inactif.",
      spells = { 588, 7128, 602, 1006, 10951, 10952 } },
  },
  DRUID = {
    { key = "motw", kind = "buff", when = "ooc", text = "« %s » manquante.",
      spells = { 1126, 5232, 6756, 5234, 8907, 9884, 9885, 21849, 21850 } },
    { key = "thorns", kind = "buff", when = "ooc", minLevel = 6, text = "« %s » manquantes (bonus en farm de mêlée).",
      spells = { 467, 782, 1075, 8914, 9756, 9910 } },
  },
  PALADIN = {
    { key = "blessing", kind = "buff", when = "ooc", text = "Aucune bénédiction : « %s » (Sagesse si tu manques de mana).",
      spells = { 19740, 19834, 19835, 19836, 19837, 19838, 25291, 19742, 19850, 19852, 19853, 19854, 25290,
        20217, 1038, 20911, 19977, 25782, 25894, 25898, 25895, 25899, 25890 } },
    { key = "aura", kind = "buff", when = "any", text = "Aucune aura active : « %s » (Dévotion par défaut).",
      spells = { 465, 10290, 643, 10291, 1032, 10292, 10293, 7294, 10298, 10299, 10300, 10301, 19746,
        19876, 19895, 19896, 19888, 19897, 19898, 19891, 19899, 19900, 20218 } },
  },
  WARLOCK = {
    { key = "armor", kind = "buff", when = "any", text = "Pas d'armure démoniaque : « %s ».",
      spells = { 687, 696, 706, 1086, 11733, 11734, 11735 } },
    { key = "shards", kind = "shards", when = "ooc", minLevel = 10, min = 2,
      text = "Moins de %d fragments d'âme : achève une cible avec Siphon d'âme.", spells = { 1120 } },
  },
  SHAMAN = {
    { key = "imbue", kind = "weapon", hand = "main", when = "any", text = "Arme sans imprégnation : « %s ».",
      spells = { 8017, 8024, 8033, 8232 } },
    { key = "shield", kind = "buff", when = "ooc", minLevel = 8, text = "« %s » inactif.",
      spells = { 324, 325, 905, 945, 8134, 10431, 10432 } },
  },
}
M.RULES = RULES

local SOUL_SHARD = 6265

local function now() return (GetTime and GetTime()) or 0 end
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
  return GetSpellInfo and GetSpellInfo(id) or nil
end

local function knowsAny(ids)
  local A = API()
  for _, id in ipairs(ids) do
    if A and A.IsSpellKnown then
      local ok, r = pcall(A.IsSpellKnown, id); if ok and r then return id end
    end
    if IsPlayerSpell then
      local ok, r = pcall(IsPlayerSpell, id); if ok and r then return id end
    end
    if IsSpellKnown then
      local ok, r = pcall(IsSpellKnown, id); if ok and r then return id end
    end
  end
  if not IsPlayerSpell and not IsSpellKnown and GetSpellInfo then
    local n = spellName(ids[1])
    if n and GetSpellInfo(n) then return ids[1] end
  end
  return nil
end

local function auraAt(unit, i, filter)
  if C_UnitAuras and C_UnitAuras.GetAuraDataByIndex then
    local ok, d = pcall(C_UnitAuras.GetAuraDataByIndex, unit, i, filter)
    if not ok or not d then return nil end
    local A = API()
    if A and A.Pub then return A.Pub(d.name) or "?", A.Pub(d.spellId) end   -- auras secrètes en combat
    return d.name, d.spellId
  end
  if UnitAura then
    local name, _, _, _, _, _, _, _, _, sid = UnitAura(unit, i, filter)
    return name, sid
  end
  return nil
end

-- Pure-ish : vrai si une aura du joueur correspond à un ID ou au nom localisé d'un ID.
local function hasBuff(ids)
  local idset, names = {}, {}
  for _, id in ipairs(ids) do
    idset[id] = true
    local n = spellName(id); if n then names[n] = true end
  end
  local A = API()
  if A and A.Auras then
    local ok, list = pcall(A.Auras, "player", "HELPFUL")
    if ok and type(list) == "table" then
      for _, a in ipairs(list) do
        if (a.name and names[a.name]) or (a.spellId and idset[a.spellId]) then return true end
      end
      return false
    end
  end
  for i = 1, 40 do
    local name, sid = auraAt("player", i, "HELPFUL")
    if not name then return false end
    if names[name] or (sid and idset[sid]) then return true end
  end
  return false
end

local function weaponEnchanted(hand)
  if not GetWeaponEnchantInfo then return true end
  local mh, _, _, _, oh = GetWeaponEnchantInfo()
  if hand == "off" then
    local ohItem = GetInventoryItemID and GetInventoryItemID("player", 17)
    if not ohItem then return true end
    local cls
    local A = API()
    if A and A.ItemInfo then cls = select(12, A.ItemInfo(ohItem)) end
    if not cls and GetItemInfoInstant then cls = select(6, GetItemInfoInstant(ohItem)) end
    if cls and cls ~= 2 then return true end -- bouclier / objet tenu en main gauche
    return oh and true or false
  end
  return mh and true or false
end

local function inCombat()
  if type(TreeAI.InCombat) == "function" then
    local ok, r = pcall(TreeAI.InCombat, TreeAI)
    if ok and r ~= nil then return r and true or false end
  end
  if InCombatLockdown and InCombatLockdown() then return true end
  return (UnitAffectingCombat and UnitAffectingCombat("player")) and true or false
end

local function opts()
  local d = type(M.db) == "table" and M.db or nil
  if not d then
    local root = _G.TreeAIDB
    if type(root) ~= "table" then return { enabled = true, disabled = {} } end
    root.modules = type(root.modules) == "table" and root.modules or {}
    d = type(root.modules.Classes) == "table" and root.modules.Classes or {}
    root.modules.Classes = d
  end
  if d.enabled == nil then d.enabled = true end
  d.disabled = type(d.disabled) == "table" and d.disabled or {}
  return d
end

local lastSaid = {}
local function suggest(id, text, prio, every)
  local t = now()
  if lastSaid[id] and t - lastSaid[id] < (every or 60) then return end
  lastSaid[id] = t
  local o = { cooldown = every or 60, ttl = math.max(every or 60, 30), module = "Classes" }
  if type(M.Suggest) == "function" then pcall(M.Suggest, M, id, text, prio or 1, o)
  elseif type(TreeAI.Suggest) == "function" then pcall(TreeAI.Suggest, TreeAI, "Classes:" .. id, text, prio or 1, o) end
end
local function clear(id)
  lastSaid[id] = nil
  if type(M.Clear) == "function" then pcall(M.Clear, M, id)
  elseif type(TreeAI.ClearSuggestion) == "function" then pcall(TreeAI.ClearSuggestion, TreeAI, "Classes:" .. id) end
end

-- Évalue une règle ; renvoie le texte à afficher ou nil.
function M:Evaluate(rule, ctx)
  if rule.minLevel and ctx.level < rule.minLevel then return nil end
  if rule.when == "ooc" and ctx.combat then return nil end
  if rule.when == "combat" and not ctx.combat then return nil end
  local known = rule.always and rule.spells[1] or knowsAny(rule.spells)
  if rule.kind == "shards" then
    if not known then return nil end
    local n = (C_Item and C_Item.GetItemCount and C_Item.GetItemCount(SOUL_SHARD))
      or (GetItemCount and GetItemCount(SOUL_SHARD)) or 0
    if n < (rule.min or 2) then return string.format(rule.text, rule.min or 2) end
    return nil
  end
  if not known then return nil end
  if rule.kind == "weapon" then
    if weaponEnchanted(rule.hand) then return nil end
    return string.format(rule.text, spellName(known) or "?")
  end
  if hasBuff(rule.spells) then return nil end
  return string.format(rule.text, spellName(known) or spellName(rule.spells[1]) or "?")
end

function M:Check()
  local o = opts()
  if not o.enabled or not self.class then return end
  if UnitIsDeadOrGhost and UnitIsDeadOrGhost("player") then return end
  if (UnitOnTaxi and UnitOnTaxi("player")) then return end
  local ctx = { combat = inCombat(), level = (UnitLevel and UnitLevel("player")) or 1 }
  for _, rule in ipairs(RULES[self.class] or {}) do
    local id = self.class .. "." .. rule.key
    if not o.disabled[id] then
      local ok, txt = pcall(self.Evaluate, self, rule, ctx)
      if ok and txt then
        suggest(id, txt, rule.when == "combat" and 2 or 1, rule.when == "combat" and 20 or 180)
      elseif ok then
        clear(id)
      end
    end
  end
end

local frame, acc, pending = nil, 0, false
function M:Enable()
  if self.enabled or not CreateFrame then return end
  local cls
  if type(TreeAI.PlayerClass) == "function" then
    local ok, c = pcall(TreeAI.PlayerClass, TreeAI); if ok then cls = c end
  end
  if not cls and UnitClass then cls = select(2, UnitClass("player")) end
  if not cls or cls == "HUNTER" or not RULES[cls] then return end
  self.class, self.enabled = cls, true
  opts()
  frame = frame or CreateFrame("Frame")
  for _, e in ipairs({ "PLAYER_ENTERING_WORLD", "PLAYER_REGEN_ENABLED", "PLAYER_REGEN_DISABLED",
    "UNIT_AURA", "UNIT_INVENTORY_CHANGED", "BAG_UPDATE_DELAYED" }) do
    pcall(frame.RegisterEvent, frame, e)
  end
  frame:SetScript("OnEvent", function(_, ev, unit)
    if (ev == "UNIT_AURA" or ev == "UNIT_INVENTORY_CHANGED") and unit ~= "player" then return end
    pending = true -- regroupe les rafales d'événements
  end)
  frame:SetScript("OnUpdate", function(_, el)
    acc = acc + (el or 0)
    if (pending and acc > 1) or acc > 30 then
      acc, pending = 0, false
      pcall(M.Check, M)
    end
  end)
  pending = true
end
M.OnEnable = M.Enable

function M:OnDisable()
  self.enabled = false
  if frame then
    if frame.UnregisterAllEvents then frame:UnregisterAllEvents() end
    frame:SetScript("OnUpdate", nil)
  end
end

M.defaults = { enabled = true }
M.classes = { WARRIOR = true, ROGUE = true, MAGE = true, PRIEST = true, DRUID = true,
  PALADIN = true, WARLOCK = true, SHAMAN = true }
M.title = "Rappels de buffs"

TreeAI.Classes = M
if type(TreeAI.RegisterModule) == "function" then
  local ok, r = pcall(TreeAI.RegisterModule, TreeAI, "Classes", M)
  if ok and type(r) == "table" then M = r; TreeAI.Classes = r end
elseif CreateFrame then
  local boot = CreateFrame("Frame")
  boot:RegisterEvent("PLAYER_LOGIN")
  boot:SetScript("OnEvent", function() pcall(M.Enable, M) end)
end
