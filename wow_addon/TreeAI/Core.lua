-- TreeAI Coach - core: events, modules, SavedVariables, suggestions, comms.
-- Conseils uniquement: aucune automatisation d'action (ToS Blizzard).
-- Lua 5.1. Globales: TreeAI, TreeAIDB, SLASH_TREEAI1/2.

local ADDON_NAME = ...
if type(ADDON_NAME) ~= "string" then ADDON_NAME = "TreeAI" end

local _G = _G
local type, pairs, ipairs, tostring, tonumber, select = type, pairs, ipairs, tostring, tonumber, select
local pcall, setmetatable, next = pcall, setmetatable, next
local tinsert, tremove, tsort, tconcat = table.insert, table.remove, table.sort, table.concat
local strsub, strfind, strgsub, strformat, strbyte, strchar = string.sub, string.find, string.gsub, string.format, string.byte, string.char
local mmax = math.max

local TreeAI = _G.TreeAI or {}
_G.TreeAI = TreeAI

TreeAI.name = ADDON_NAME
TreeAI.version = "0.1.0"
TreeAI.SCHEMA = 1
TreeAI.PREFIX = "TREEAI"
TreeAI.PRIO_INFO, TreeAI.PRIO_TIP, TreeAI.PRIO_URGENT = 1, 2, 3
TreeAI.Data = TreeAI.Data or {}
TreeAI.modules = TreeAI.modules or {}
TreeAI.moduleOrder = TreeAI.moduleOrder or {}
TreeAI.API = TreeAI.API or {}

-- Locale: L[key] returns the key itself when missing.
TreeAI.L = TreeAI.L or setmetatable({}, { __index = function(_, k) return k end })
local L = TreeAI.L

local function now()
  if _G.GetTime then return _G.GetTime() end
  return 0
end
local function wallclock()
  if _G.time then return _G.time() end
  return 0
end

---------------------------------------------------------------------------
-- Printing
---------------------------------------------------------------------------
local function joinArgs(...)
  local n = select("#", ...)
  local t = {}
  for i = 1, n do t[i] = tostring((select(i, ...))) end
  return tconcat(t, " ")
end

function TreeAI:Print(...)
  local msg = "|cff33cc66TreeAI|r: " .. joinArgs(...)
  local cf = _G.DEFAULT_CHAT_FRAME
  if cf and cf.AddMessage then cf:AddMessage(msg) elseif _G.print then _G.print(msg) end
end

function TreeAI:Debug(...)
  local db = _G.TreeAIDB
  if db and db.settings and db.settings.debug then
    self:Print("|cff999999[debug]|r", ...)
  end
end

local function safecall(fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then TreeAI:Debug("erreur:", err) end
  return ok, err
end
TreeAI.safecall = safecall

---------------------------------------------------------------------------
-- Defensive API wrappers (Forever = Mainline family: C_* APIs; Era: globals)
---------------------------------------------------------------------------
local API = TreeAI.API
local C_Container, C_Spell, C_Item, C_UnitAuras, C_AddOns, C_Timer =
  _G.C_Container, _G.C_Spell, _G.C_Item, _G.C_UnitAuras, _G.C_AddOns, _G.C_Timer

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h, i, j, k, l, m, n = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h, i, j, k, l, m, n end
  return nil
end

function API.SpellName(id)
  if id == nil then return nil end
  if C_Spell and C_Spell.GetSpellInfo then
    local info = try(C_Spell.GetSpellInfo, id)
    if type(info) == "table" then return info.name end
    if C_Spell.GetSpellName then
      local n = try(C_Spell.GetSpellName, id)
      if n then return n end
    end
  end
  return (try(_G.GetSpellInfo, id))
end

function API.SpellIcon(id)
  if id == nil then return nil end
  if C_Spell and C_Spell.GetSpellInfo then
    local info = try(C_Spell.GetSpellInfo, id)
    if type(info) == "table" then return info.iconID end
  end
  local _, _, icon = try(_G.GetSpellInfo, id)
  return icon
end

function API.IsSpellKnown(id)
  if type(id) ~= "number" then return false end
  if _G.IsPlayerSpell and try(_G.IsPlayerSpell, id) then return true end
  if _G.IsSpellKnown and try(_G.IsSpellKnown, id) then return true end
  if C_Spell and C_Spell.IsSpellKnown and try(C_Spell.IsSpellKnown, id) then return true end
  return false
end

function API.SpellCooldown(id)
  if C_Spell and C_Spell.GetSpellCooldown then
    local info = try(C_Spell.GetSpellCooldown, id)
    if type(info) == "table" then return info.startTime or 0, info.duration or 0 end
  end
  local s, d = try(_G.GetSpellCooldown, id)
  return s or 0, d or 0
end

function API.ItemInfo(item)
  if item == nil then return nil end
  local fn = (C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
  return try(fn, item)
end

function API.ItemName(item)
  if C_Item and C_Item.GetItemNameByID and type(item) == "number" then
    local n = try(C_Item.GetItemNameByID, item)
    if n then return n end
  end
  return (API.ItemInfo(item))
end

function API.ItemCount(itemID, includeBank)
  local fn = (C_Item and C_Item.GetItemCount) or _G.GetItemCount
  return try(fn, itemID, includeBank) or 0
end

function API.NumBags()
  local n = _G.NUM_BAG_SLOTS or 4
  return n
end

function API.BagSlots(bag)
  local fn = (C_Container and C_Container.GetContainerNumSlots) or _G.GetContainerNumSlots
  return try(fn, bag) or 0
end

function API.BagFree(bag)
  local fn = (C_Container and C_Container.GetContainerNumFreeSlots) or _G.GetContainerNumFreeSlots
  local free, bagType = try(fn, bag)
  return free or 0, bagType or 0
end

function API.BagItem(bag, slot)
  if C_Container and C_Container.GetContainerItemInfo then
    local info = try(C_Container.GetContainerItemInfo, bag, slot)
    if type(info) ~= "table" then return nil end
    return {
      itemID = info.itemID, count = info.stackCount or 1, link = info.hyperlink,
      quality = info.quality, icon = info.iconFileID, locked = info.isLocked,
      noValue = info.hasNoValue,
    }
  end
  local icon, count, locked, quality, _, _, link, _, noValue, itemID = try(_G.GetContainerItemInfo, bag, slot)
  if not icon and not link then return nil end
  if not itemID and link then itemID = tonumber(link:match("item:(%d+)")) end
  return { itemID = itemID, count = count or 1, link = link, quality = quality,
           icon = icon, locked = locked, noValue = noValue }
end

function API.IterBags()
  local bag, slot, maxBag = 0, 0, API.NumBags()
  local slots = API.BagSlots(0)
  return function()
    while bag <= maxBag do
      slot = slot + 1
      if slot > slots then
        bag = bag + 1
        slot = 0
        if bag <= maxBag then slots = API.BagSlots(bag) end
      else
        local item = API.BagItem(bag, slot)
        if item then return bag, slot, item end
      end
    end
    return nil
  end
end

function API.FreeSlots()
  local free, total = 0, 0
  for bag = 0, API.NumBags() do
    local f, bagType = API.BagFree(bag)
    if bagType == 0 then -- general bags only (not quiver/ammo pouch/soul bag)
      free = free + f
      total = total + API.BagSlots(bag)
    end
  end
  return free, total
end

-- Only call from a player click (merchant sell button), out of combat.
function API.UseContainerItem(bag, slot)
  if TreeAI:InCombat() then return false end
  local fn = (C_Container and C_Container.UseContainerItem) or _G.UseContainerItem
  local ok = pcall(fn, bag, slot)
  return ok
end

local function auraFromData(d)
  if type(d) ~= "table" then return nil end
  return { name = d.name, icon = d.icon, count = d.applications or 0, duration = d.duration or 0,
           expires = d.expirationTime or 0, spellId = d.spellId, source = d.sourceUnit }
end

function API.Auras(unit, filter)
  filter = filter or "HELPFUL"
  local out = {}
  for i = 1, 40 do
    local a
    if C_UnitAuras and C_UnitAuras.GetAuraDataByIndex then
      a = auraFromData(try(C_UnitAuras.GetAuraDataByIndex, unit, i, filter))
    elseif _G.UnitAura then
      local name, icon, count, _, duration, expires, source, _, _, spellId = try(_G.UnitAura, unit, i, filter)
      if name then
        a = { name = name, icon = icon, count = count or 0, duration = duration or 0,
              expires = expires or 0, spellId = spellId, source = source }
      end
    end
    if not a then break end
    out[#out + 1] = a
  end
  return out
end

function API.Aura(unit, spell, filter)
  local wantName = type(spell) == "string" and spell or nil
  local wantId = type(spell) == "number" and spell or nil
  local idName = wantId and API.SpellName(wantId)
  for _, a in ipairs(API.Auras(unit, filter)) do
    if (wantId and a.spellId == wantId) or (wantName and a.name == wantName) or (idName and a.name == idName) then
      return a
    end
  end
  return nil
end

function API.AddOnMeta(addon, field)
  local fn = (C_AddOns and C_AddOns.GetAddOnMetadata) or _G.GetAddOnMetadata
  return try(fn, addon, field)
end

function API.GetMacroCount()
  local g, c = try(_G.GetNumMacros)
  return g or 0, c or 0
end

function API.Money()
  return try(_G.GetMoney) or 0
end

function API.Distance(unit)
  if _G.UnitPosition then
    local y1, x1, _, m1 = try(_G.UnitPosition, "player")
    local y2, x2, _, m2 = try(_G.UnitPosition, unit)
    if y1 and y2 and m1 == m2 then
      local dx, dy = x2 - x1, y2 - y1
      return (dx * dx + dy * dy) ^ 0.5
    end
  end
  if _G.CheckInteractDistance then
    if try(_G.CheckInteractDistance, unit, 3) then return 10 end
    if try(_G.CheckInteractDistance, unit, 4) then return 28 end
  end
  return nil
end

---------------------------------------------------------------------------
-- Timers
---------------------------------------------------------------------------
local timerFrame, timers
local function ensureTimerFrame()
  if timerFrame or not _G.CreateFrame then return end
  timers = {}
  timerFrame = _G.CreateFrame("Frame")
  timerFrame:SetScript("OnUpdate", function()
    local t = now()
    for i = #timers, 1, -1 do
      local tm = timers[i]
      if tm.at <= t then
        tremove(timers, i)
        safecall(tm.fn)
      end
    end
  end)
end

function TreeAI:After(sec, fn)
  if type(fn) ~= "function" then return end
  if C_Timer and C_Timer.After then return C_Timer.After(sec or 0, function() safecall(fn) end) end
  ensureTimerFrame()
  if timers then timers[#timers + 1] = { at = now() + (sec or 0), fn = fn } end
end

function TreeAI:NewTicker(sec, fn)
  if C_Timer and C_Timer.NewTicker then
    return C_Timer.NewTicker(sec, function() safecall(fn) end)
  end
  local t = { cancelled = false }
  function t:Cancel() self.cancelled = true end
  local function loop()
    if t.cancelled then return end
    safecall(fn)
    TreeAI:After(sec, loop)
  end
  TreeAI:After(sec, loop)
  return t
end

---------------------------------------------------------------------------
-- Event dispatcher (many owners per event) + internal messages
---------------------------------------------------------------------------
local frame = _G.CreateFrame and _G.CreateFrame("Frame", "TreeAICoreFrame") or nil
TreeAI.frame = frame
local handlers = {}   -- event -> { [owner] = fn }
local messages = {}   -- message -> { [owner] = fn }

local function ownerActive(owner)
  if type(owner) == "table" and owner.IsEnabled and owner ~= TreeAI then
    return owner:IsEnabled()
  end
  return true
end

local function dispatch(event, ...)
  local list = handlers[event]
  if not list then return end
  for owner, fn in pairs(list) do
    if ownerActive(owner) then
      TreeAI._ctx = tostring(owner.name or "core") .. ":" .. tostring(event)
      safecall(fn, owner, event, ...)
    end
  end
  TreeAI._ctx = nil
end

function TreeAI:RegisterEvent(event, owner, fn)
  owner = owner or self
  if type(fn) ~= "function" then
    local m = owner[fn or event]
    if type(m) ~= "function" then return false end
    fn = m
  end
  if not handlers[event] then
    handlers[event] = {}
    if frame then
      local ok = pcall(frame.RegisterEvent, frame, event)
      if not ok then self:Debug("event inconnu:", event) end
    end
  end
  handlers[event][owner] = fn
  return true
end

function TreeAI:UnregisterEvent(event, owner)
  owner = owner or self
  local list = handlers[event]
  if not list then return end
  list[owner] = nil
  if next(list) == nil and event ~= "ADDON_LOADED" and event ~= "PLAYER_LOGIN"
     and event ~= "PLAYER_LOGOUT" and event ~= "CHAT_MSG_ADDON"
     and event ~= "PLAYER_REGEN_ENABLED" and event ~= "PLAYER_REGEN_DISABLED" then
    handlers[event] = nil
    if frame then pcall(frame.UnregisterEvent, frame, event) end
  end
end

function TreeAI:On(message, owner, fn)
  owner = owner or self
  if type(fn) ~= "function" then return false end
  messages[message] = messages[message] or {}
  messages[message][owner] = fn
  return true
end

function TreeAI:Off(message, owner)
  if messages[message] then messages[message][owner or self] = nil end
end

--- Actualisation automatique : toutes les `settings.refresh` s, chaque module actif qui a une méthode
--- :AutoRefresh() la reçoit (conseils recalculés), les suggestions expirées partent, le panneau se redessine.
--- Actions faites par le joueur (clic / commande) : certaines fonctions du jeu (créer / modifier une macro…)
--- ne sont permises que pendant une action du joueur sur WoW Forever. TreeAI:InUserAction() le dit.
TreeAI._userAction = 0
function TreeAI:UserAction(fn, ...)
  self._userAction = (self._userAction or 0) + 1
  local ok, err = pcall(fn, ...)
  self._userAction = math.max(0, (self._userAction or 1) - 1)
  if not ok then self:Debug("action:", err) end
  return ok
end
function TreeAI:InUserAction() return (self._userAction or 0) > 0 end

function TreeAI:StartAutoRefresh()
  if self._refreshTicker or not self.NewTicker then return end
  local n = 0
  self._refreshTicker = self:NewTicker(1, function()
    n = n + 1
    local every = tonumber(self.settings and self.settings.refresh) or 3
    if every <= 0 then return end
    if InCombatLockdown and InCombatLockdown() then every = every * 2 end
    if n < every then return end
    n = 0
    for _, name in ipairs(self.moduleOrder or {}) do
      local m = self.modules and self.modules[name]
      if m and type(m.AutoRefresh) == "function" and (not m.IsEnabled or m:IsEnabled()) then
        safecall(m.AutoRefresh, m)
      end
    end
    self:Fire("REFRESH")
    if self.RefreshPanel then safecall(self.RefreshPanel, self) end
  end)
end

function TreeAI:Fire(message, ...)
  local list = messages[message]
  if not list then return end
  for owner, fn in pairs(list) do
    if ownerActive(owner) then safecall(fn, owner, message, ...) end
  end
end

if frame then
  frame:SetScript("OnEvent", function(_, event, ...) dispatch(event, ...) end)
end
TreeAI._dispatch = dispatch -- for tests

---------------------------------------------------------------------------
-- SavedVariables
---------------------------------------------------------------------------
local DEFAULT_SETTINGS = {
  enabled = true, sound = true, chat = false, comms = true, debug = false,
  soundTips = false,   -- sons aussi pour certains conseils (rares…) ; sinon son = important (prio 3) seulement
  minPrio = 1,
  refresh = 3,   -- actualisation automatique des conseils (s, hors combat ; 2x plus lent en combat)
  pvp = false,   -- aides PvP (OFF : l'addon aide à quêter, il se tait contre les joueurs ennemis)
  panel = { shown = true, locked = false, scale = 1, alpha = 0.9,
            point = "CENTER", x = 300, y = 120, maxLines = 8, nowMax = 5, width = 280 },
  alert = { enabled = true, corner = "TOPRIGHT", duration = 8, scale = 1, alpha = 0.95 },
}

local function mergeDefaults(dst, src)
  if type(dst) ~= "table" then dst = {} end
  for k, v in pairs(src) do
    if type(v) == "table" then
      dst[k] = mergeDefaults(dst[k], v)
    elseif dst[k] == nil then
      dst[k] = v
    end
  end
  return dst
end
TreeAI.MergeDefaults = mergeDefaults

function TreeAI:CharKey()
  if self._charKey then return self._charKey end
  local name = _G.UnitName and _G.UnitName("player")
  local realm = _G.GetRealmName and _G.GetRealmName()
  if not name or name == "" or name == _G.UNKNOWNOBJECT then return "?" end
  self._charKey = name .. "-" .. tostring(realm or "?")
  return self._charKey
end

function TreeAI:PlayerClass()
  if not _G.UnitClass then return nil end
  local _, classFile = _G.UnitClass("player")
  return classFile
end

local function initDB()
  local db = _G.TreeAIDB
  if type(db) ~= "table" then db = {} end
  -- future migrations: if (db.schema or 0) < N then ... end
  db.schema = TreeAI.SCHEMA
  db.version = TreeAI.version
  db.settings = mergeDefaults(db.settings, DEFAULT_SETTINGS)
  db.modules = type(db.modules) == "table" and db.modules or {}
  db.chars = type(db.chars) == "table" and db.chars or {}
  db.group = type(db.group) == "table" and db.group or {}
  db.group.peers = type(db.group.peers) == "table" and db.group.peers or {}
  _G.TreeAIDB = db
  TreeAI.db = db
  TreeAI.settings = db.settings
  return db
end

function TreeAI:CharDB()
  local db = self.db or initDB()
  local key = self:CharKey()
  local c = db.chars[key]
  if type(c) ~= "table" then c = {}; db.chars[key] = c end
  c.summary = type(c.summary) == "table" and c.summary or {}
  c.suggestions = type(c.suggestions) == "table" and c.suggestions or {}
  c.modules = type(c.modules) == "table" and c.modules or {}
  return c
end

---------------------------------------------------------------------------
-- Modules
---------------------------------------------------------------------------
local ModuleMixin = {}

function ModuleMixin:IsEnabled()
  if self._enabled == false then return false end
  local s = TreeAI.settings
  if s and s.enabled == false then return false end
  return not (self.db and self.db.enabled == false) and self._classOK ~= false
end
function ModuleMixin:RegisterEvent(event, fn) return TreeAI:RegisterEvent(event, self, fn) end
function ModuleMixin:UnregisterEvent(event) return TreeAI:UnregisterEvent(event, self) end
function ModuleMixin:UnregisterAllEvents()
  for event in pairs(handlers) do TreeAI:UnregisterEvent(event, self) end
end
function ModuleMixin:RegisterUnitEvent(event, unit1, unit2, fn)
  if type(unit2) == "function" then fn, unit2 = unit2, nil end
  local handler = fn or self[event]
  if type(handler) ~= "function" then return false end
  return TreeAI:RegisterEvent(event, self, function(mod, ev, unit, ...)
    if unit == unit1 or (unit2 and unit == unit2) then return handler(mod, ev, unit, ...) end
  end)
end
function ModuleMixin:On(message, fn)
  if type(fn) ~= "function" then fn = self[message] end
  return TreeAI:On(message, self, fn)
end
function ModuleMixin:Suggest(id, text, prio, opts)
  opts = opts or {}
  opts.module = opts.module or self.name
  return TreeAI:Suggest(self.name .. ":" .. tostring(id), text, prio, opts)
end
function ModuleMixin:Clear(id) return TreeAI:ClearSuggestion(self.name .. ":" .. tostring(id)) end
function ModuleMixin:Print(...) return TreeAI:Print("[" .. (self.title or self.name) .. "]", ...) end
function ModuleMixin:Debug(...) return TreeAI:Debug("[" .. self.name .. "]", ...) end

local function moduleBindDB(mod)
  local db = TreeAI.db
  if not db then return end
  local defaults = mergeDefaults({ enabled = true }, mod.defaults or {})
  db.modules[mod.name] = mergeDefaults(db.modules[mod.name], defaults)
  mod.db = db.modules[mod.name]
  if TreeAI:CharKey() ~= "?" then
    local c = TreeAI:CharDB()
    c.modules[mod.name] = mergeDefaults(c.modules[mod.name], mod.charDefaults or {})
    mod.char = c.modules[mod.name]
  else
    mod.char = mod.char or mergeDefaults({}, mod.charDefaults or {})
  end
end

local function moduleClassCheck(mod)
  if type(mod.classes) == "table" then
    local cls = TreeAI:PlayerClass()
    if cls then mod._classOK = mod.classes[cls] and true or false end
  end
end

local function moduleInit(mod)
  if mod._inited then return end
  moduleBindDB(mod)
  mod._inited = true
  if type(mod.OnInit) == "function" then safecall(mod.OnInit, mod) end
end

local function moduleEnable(mod)
  if mod._active then return end
  moduleClassCheck(mod)
  if not mod:IsEnabled() then return end
  mod._active = true
  if type(mod.OnEnable) == "function" then safecall(mod.OnEnable, mod) end
end

local function moduleDisable(mod)
  if not mod._active then return end
  mod._active = false
  if type(mod.OnDisable) == "function" then safecall(mod.OnDisable, mod) end
  TreeAI:ClearSuggestions(mod.name .. ":")
end

function TreeAI:RegisterModule(name, tbl)
  if type(name) ~= "string" or name == "" then return nil end
  tbl = tbl or {}
  if self.modules[name] then
    -- re-registration: merge into existing table
    for k, v in pairs(tbl) do self.modules[name][k] = v end
    return self.modules[name]
  end
  tbl.name = name
  tbl.core = self
  for k, v in pairs(ModuleMixin) do
    if tbl[k] == nil then tbl[k] = v end
  end
  self.modules[name] = tbl
  self.moduleOrder[#self.moduleOrder + 1] = name
  -- late registration (file loaded after core init)
  if self._inited then moduleInit(tbl) end
  if self._loggedIn then moduleEnable(tbl) end
  return tbl
end

function TreeAI:GetModule(name) return self.modules[name] end

function TreeAI:IterModules()
  local i = 0
  return function()
    i = i + 1
    local n = self.moduleOrder[i]
    if n then return n, self.modules[n] end
  end
end

function TreeAI:SetModuleEnabled(name, enabled)
  local mod = self.modules[name]
  if not mod or not mod.db then return false end
  mod.db.enabled = enabled and true or false
  if enabled then moduleEnable(mod) else moduleDisable(mod) end
  return true
end

---------------------------------------------------------------------------
-- Combat helpers
---------------------------------------------------------------------------
function TreeAI:InCombat()
  if _G.InCombatLockdown then
    local ok, v = pcall(_G.InCombatLockdown)
    return ok and v and true or false
  end
  return false
end

local oocQueue, oocOrder = {}, {}
function TreeAI:RunOutOfCombat(key, fn)
  if type(fn) ~= "function" then return end
  if not self:InCombat() then return safecall(fn) end
  key = key or tostring(fn)
  if not oocQueue[key] then oocOrder[#oocOrder + 1] = key end
  oocQueue[key] = fn
end

---------------------------------------------------------------------------
-- Suggestions
---------------------------------------------------------------------------
local suggestions = {}   -- id -> entry
local announced = {}     -- id -> { text, at }
TreeAI._suggestions = suggestions

local function playUrgentSound()
  if not (TreeAI.settings and TreeAI.settings.sound) then return end
  if _G.PlaySound then
    local kit = _G.SOUNDKIT and (_G.SOUNDKIT.RAID_WARNING or _G.SOUNDKIT.ALARM_CLOCK_WARNING_3)
    if kit then pcall(_G.PlaySound, kit, "Master") else pcall(_G.PlaySound, 8959) end
  end
end

local function logSuggestion(e)
  if not TreeAI.db or TreeAI:CharKey() == "?" then return end
  local log = TreeAI:CharDB().suggestions
  log[#log + 1] = { id = e.id, text = e.text, prio = e.prio, time = wallclock(), module = e.module }
  while #log > 40 do tremove(log, 1) end
end

--- PvP context: an enemy player targeted / attacking, or a battleground / arena. Never raises.
function TreeAI:InPvP()
  local ok, res = pcall(function()
    if IsInInstance then
      local _, kind = IsInInstance()
      if kind == "pvp" or kind == "arena" then return true end
    end
    if UnitExists and UnitExists("target") and UnitIsPlayer and UnitIsPlayer("target")
        and UnitCanAttack and UnitCanAttack("player", "target") then
      return true
    end
    return false
  end)
  return ok and res or false
end

function TreeAI:Suggest(id, text, prio, opts)
  if type(id) ~= "string" then id = tostring(id) end
  if text == nil or text == "" then self:ClearSuggestion(id); return nil end
  -- aides PvP désactivées (défaut) : rien contre un joueur ennemi, rien en champ de bataille / arène,
  -- et jamais une suggestion marquée opts.pvp
  if not (self.settings and self.settings.pvp) and ((opts and opts.pvp) or self:InPvP()) then
    return nil
  end
  text = tostring(text)
  prio = tonumber(prio) or 1
  if prio < 1 then prio = 1 elseif prio > 3 then prio = 3 end
  opts = opts or {}
  local t = now()
  local ttl = opts.ttl
  if ttl == nil then ttl = 60 end
  local e = suggestions[id] or {}
  local changed = (e.text ~= text) or (e.prio ~= prio)
  e.id, e.text, e.prio, e.time = id, text, prio, t
  e.expires = (ttl and ttl > 0) and (t + ttl) or nil
  e.module, e.icon, e.onClick, e.macro = opts.module, opts.icon, opts.onClick, opts.macro
  e.tab, e.tooltip = opts.tab, opts.tooltip
  suggestions[id] = e

  -- announce (sound/chat) at most once per cooldown for same id+text
  local cd = opts.cooldown or 30
  local a = announced[id]
  if not a or a.text ~= text or (t - a.at) >= cd then
    announced[id] = { text = text, at = t }
    if changed or not a then logSuggestion(e) end
    local minPrio = (self.settings and self.settings.minPrio) or 1
    if prio >= minPrio then
      local doSound = opts.sound
      -- sound only for important (prio 3) items, never forced on lower ones
      doSound = ((prio >= 3) and doSound ~= false) or (doSound == true and self.settings and self.settings.soundTips)
      if doSound then playUrgentSound() end
      if prio >= 3 and self.ShowAlert then safecall(self.ShowAlert, self, e) end
      -- important items go to the corner alert box; the centre error text only if that box is off
      local al = self.settings and self.settings.alert
      if prio >= 3 and al and al.enabled == false and _G.UIErrorsFrame and _G.UIErrorsFrame.AddMessage then
        pcall(_G.UIErrorsFrame.AddMessage, _G.UIErrorsFrame, text, 1, 0.3, 0.2)
      end
      -- advice never goes to the chat unless the player opted in (settings.chat, off by default)
      local doChat = self.settings and self.settings.chat and opts.chat ~= false
      if doChat then self:Print(text) end
    end
  end
  self:Fire("SUGGESTION", e)
  self:RequestRefresh()
  return e
end

function TreeAI:ClearSuggestion(id)
  if suggestions[id] then
    suggestions[id] = nil
    self:Fire("SUGGESTION_CLEARED", id)
    self:RequestRefresh()
  end
end

function TreeAI:ClearSuggestions(prefix)
  local any = false
  for id in pairs(suggestions) do
    if not prefix or strsub(id, 1, #prefix) == prefix then
      suggestions[id] = nil
      any = true
      self:Fire("SUGGESTION_CLEARED", id)
    end
  end
  if any then self:RequestRefresh() end
end

function TreeAI:GetSuggestions()
  local t = now()
  local list = {}
  local minPrio = (self.settings and self.settings.minPrio) or 1
  for id, e in pairs(suggestions) do
    if e.expires and e.expires <= t then
      suggestions[id] = nil
    elseif e.prio >= minPrio then
      list[#list + 1] = e
    end
  end
  tsort(list, function(a, b)
    if a.prio ~= b.prio then return a.prio > b.prio end
    if a.time ~= b.time then return a.time > b.time end
    return a.id < b.id
  end)
  return list
end

---------------------------------------------------------------------------
-- Panel tabs (shared contract: RegisterTab / SetTabLines; Suggest -> « Maintenant »)
---------------------------------------------------------------------------
TreeAI.tabs = TreeAI.tabs or {}
local tabSources = {}   -- tabId -> { order = {source,...}, lines = { [source] = {entries} } }
local NOW_TAB, DEFAULT_TAB = "now", "info"
-- where a module's suggestions are listed (besides « Maintenant »); others -> « Infos »
TreeAI.MODULE_TAB = TreeAI.MODULE_TAB or {
  Quests = "quests", Leveling = "quests", Tracker = "quests", Compass = "quests", Predict = "quests",
  Squad = "group", Group = "group", Missions = "group",
  Auction = "hv",
}

function TreeAI:RegisterTab(id, title, order)
  if type(id) ~= "string" or id == "" then return nil end
  local t = self.tabs[id] or { id = id }
  if t.auto and title ~= nil then t.order, t.title, t.auto = nil, nil, nil end   -- auto-created by SetTabLines
  t.title = title and tostring(title) or t.title or id
  t.order = t.order or tonumber(order) or 100   -- a tab keeps its first order (core tabs stay in place)
  self.tabs[id] = t
  if self.RequestRefresh then self:RequestRefresh() end
  return t
end

function TreeAI:GetTabs()
  local list = {}
  for _, t in pairs(self.tabs) do list[#list + 1] = t end
  tsort(list, function(a, b)
    if a.order ~= b.order then return a.order < b.order end
    return a.id < b.id
  end)
  return list
end

local function normLine(l, source)
  if type(l) == "string" then l = { text = l } end
  if type(l) ~= "table" or l.text == nil or l.text == "" then return nil end
  local prio = tonumber(l.prio) or 1
  if prio < 1 then prio = 1 elseif prio > 3 then prio = 3 end
  return { text = tostring(l.text), prio = prio, icon = l.icon,
    onClick = type(l.onClick) == "function" and l.onClick or nil,
    tooltip = l.tooltip, pin = l.pin and true or nil, id = l.id, source = source, data = l.data }
end

-- lines = { {text=, prio=1..3, icon=?, onClick=?, tooltip=?, pin=?}, ... } replaces the content of
-- `source` (default "main"): several modules can fill one tab, each with its own block, in call order.
-- pin = true also offers the line to « Maintenant ».
function TreeAI:SetTabLines(id, lines, source)
  if type(id) ~= "string" then return false end
  if not self.tabs[id] then self:RegisterTab(id, nil, 100); self.tabs[id].auto = true end
  source = source and tostring(source) or "main"
  local ts = tabSources[id]
  if not ts then ts = { order = {}, lines = {} }; tabSources[id] = ts end
  local out = {}
  if type(lines) == "table" then
    for i = 1, #lines do
      local e = normLine(lines[i], source)
      if e then out[#out + 1] = e end
      if #out >= 100 then break end
    end
  end
  if ts.lines[source] == nil then ts.order[#ts.order + 1] = source end
  ts.lines[source] = out
  self:RequestRefresh()
  return true
end

local function plain(text)
  text = strgsub(tostring(text or ""), "|c%x%x%x%x%x%x%x%x", "")
  text = strgsub(text, "|r", "")
  return text
end
TreeAI.PlainText = plain

function TreeAI:TabOf(e)
  if e.tab and self.tabs[e.tab] then return e.tab end
  return (e.module and self.MODULE_TAB[e.module]) or DEFAULT_TAB
end

-- Entries shown in a tab. « Maintenant » = top N by prio of suggestions + pinned tab lines, deduped
-- by visible text. Other tabs = their SetTabLines blocks, then the suggestions routed to them.
function TreeAI:GetTabLines(id)
  local out, seen = {}, {}
  local function push(e)
    local k = plain(e.text)
    if seen[k] then return false end
    seen[k] = true
    out[#out + 1] = e
    return true
  end
  local sugg = self:GetSuggestions()
  if id == NOW_TAB then
    local cand = {}
    for _, e in ipairs(sugg) do cand[#cand + 1] = e end
    for _, t in ipairs(self:GetTabs()) do
      local ts = tabSources[t.id]
      if ts then
        for _, src in ipairs(ts.order) do
          for _, l in ipairs(ts.lines[src] or {}) do if l.pin then cand[#cand + 1] = l end end
        end
      end
    end
    for i, e in ipairs(cand) do e._rank = i end
    tsort(cand, function(a, b)
      if a.prio ~= b.prio then return a.prio > b.prio end
      return a._rank < b._rank
    end)
    local max = tonumber(self.settings and self.settings.panel and self.settings.panel.nowMax) or 5
    for _, e in ipairs(cand) do
      if #out >= max then break end
      push(e)
    end
    return out
  end
  local ts = tabSources[id]
  if ts then
    for _, src in ipairs(ts.order) do
      for _, l in ipairs(ts.lines[src] or {}) do push(l) end
    end
  end
  for _, e in ipairs(sugg) do
    if self:TabOf(e) == id then push(e) end
  end
  return out
end

-- coalesced panel refresh: at most 2 per second
local lastRefresh, refreshPending = -10, false
function TreeAI:RequestRefresh()
  if not self.RefreshPanel or refreshPending then return end
  local wait = 0.5 - (now() - lastRefresh)
  if wait <= 0 then
    lastRefresh = now()
    safecall(self.RefreshPanel, self)
    return
  end
  refreshPending = true
  self:After(wait, function()
    refreshPending = false
    lastRefresh = now()
    if TreeAI.RefreshPanel then safecall(TreeAI.RefreshPanel, TreeAI) end
  end)
end

TreeAI:RegisterTab("now", L["Maintenant"], 1)
TreeAI:RegisterTab("quests", L["Quêtes"], 2)
TreeAI:RegisterTab("group", L["Groupe"], 3)
TreeAI:RegisterTab("hv", L["HV"], 4)
TreeAI:RegisterTab("info", L["Infos"], 5)

-- per-character panel state (tab, position, mini); lock/scale/alpha stay account-wide
function TreeAI:PanelChar()
  local acc = self.settings and self.settings.panel
  if not acc then return nil end
  if not self.db or self:CharKey() == "?" then return acc end
  local c = self:CharDB()
  if type(c.panel) ~= "table" then
    c.panel = { point = acc.point or "CENTER", x = acc.x or 300, y = acc.y or 120 }
  end
  local p = c.panel
  if type(p.tab) ~= "string" then p.tab = "now" end
  if p.mini == nil then p.mini = true end   -- compact by default: one line, expand on demand
  return p
end

---------------------------------------------------------------------------
-- Character summary (read by TreeAI Coach from SavedVariables)
---------------------------------------------------------------------------
function TreeAI:Summary()
  if not self.db or self:CharKey() == "?" then
    self._tmpSummary = self._tmpSummary or {}
    return self._tmpSummary
  end
  return self:CharDB().summary
end

function TreeAI:UpdateSummary()
  local s = self:Summary()
  local function g(fn, ...) return try(_G[fn], ...) end
  local className, classFile = g("UnitClass", "player")
  s.class, s.className = classFile, className
  s.race = g("UnitRace", "player")
  s.level = g("UnitLevel", "player")
  s.xp, s.xpMax = g("UnitXP", "player"), g("UnitXPMax", "player")
  s.rested = g("GetXPExhaustion")
  s.zone = g("GetRealZoneText") or g("GetZoneText")
  s.subzone = g("GetSubZoneText")
  s.money = API.Money()
  s.bagsFree, s.bagsTotal = API.FreeSlots()
  s.guild = g("GetGuildInfo", "player")
  s.inGroup = (g("IsInGroup") or (g("GetNumPartyMembers") or 0) > 0) and true or false
  s.groupSize = g("GetNumGroupMembers") or ((g("GetNumPartyMembers") or 0) + 1)
  if _G.GetSpecialization then
    local idx = g("GetSpecialization")
    if idx and _G.GetSpecializationInfo then
      local _, specName = g("GetSpecializationInfo", idx)
      s.spec = specName
    end
  elseif _G.GetActiveTalentGroup then
    s.specGroup = g("GetActiveTalentGroup")
  end
  s.addonVersion = self.version
  s.updated = wallclock()
  self:Fire("SUMMARY", s)
  return s
end

---------------------------------------------------------------------------
-- Serializer (string/number/boolean/tables; no functions)
---------------------------------------------------------------------------
local function esc(s)
  return (strgsub(s, "[%%|^~=;{}\n\r]", function(c) return strformat("%%%02X", strbyte(c)) end))
end
local function unesc(s)
  return (strgsub(s, "%%(%x%x)", function(h) return strchar(tonumber(h, 16)) end))
end

local function ser(v, depth)
  local tv = type(v)
  if tv == "string" then return "s" .. esc(v)
  elseif tv == "number" then return "n" .. tostring(v)
  elseif tv == "boolean" then return v and "T" or "F"
  elseif tv == "table" and depth < 6 then
    local parts = {}
    for k, val in pairs(v) do
      local sk, sv = ser(k, depth + 1), ser(val, depth + 1)
      if sk and sv then parts[#parts + 1] = sk .. "=" .. sv end
    end
    return "{" .. tconcat(parts, ";") .. "}"
  end
  return nil
end

function TreeAI:Serialize(v) return ser(v, 0) or "" end

local function deser(s, i)
  local c = strsub(s, i, i)
  if c == "{" then
    local t = {}
    i = i + 1
    while i <= #s do
      if strsub(s, i, i) == "}" then return t, i + 1 end
      local k, v
      k, i = deser(s, i)
      if strsub(s, i, i) ~= "=" then return nil, #s + 1 end
      v, i = deser(s, i + 1)
      if k ~= nil then t[k] = v end
      if strsub(s, i, i) == ";" then i = i + 1 end
    end
    return t, i
  elseif c == "T" then return true, i + 1
  elseif c == "F" then return false, i + 1
  elseif c == "s" or c == "n" then
    local j = i + 1
    while j <= #s do
      local d = strsub(s, j, j)
      if d == "=" or d == ";" or d == "}" then break end
      j = j + 1
    end
    local raw = strsub(s, i + 1, j - 1)
    if c == "n" then return tonumber(raw), j end
    return unesc(raw), j
  end
  return nil, #s + 1
end

function TreeAI:Deserialize(s)
  if type(s) ~= "string" or s == "" then return nil end
  local ok, v = pcall(deser, s, 1)
  if ok then return v end
  return nil
end

---------------------------------------------------------------------------
-- Comms (C_ChatInfo.SendAddonMessage, prefix TREEAI)
---------------------------------------------------------------------------
local commHandlers = {}  -- msgType -> { [owner] = fn }
local sendQueue = {}
local sending = false
local seq = 0
local partial = {}       -- sender..seq -> { n, parts = {} }
local CHUNK = 220

local function sendRaw(msg, channel, target)
  local C_ChatInfo = _G.C_ChatInfo
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then
    return pcall(C_ChatInfo.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  elseif _G.SendAddonMessage then
    return pcall(_G.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  end
  return false
end

local function pump()
  local item = tremove(sendQueue, 1)
  if not item then sending = false; return end
  sendRaw(item[1], item[2], item[3])
  TreeAI:After(0.2, pump)
end

function TreeAI:DefaultChannel()
  local function g(fn, ...) return try(_G[fn], ...) end
  if g("IsInRaid") or (g("GetNumRaidMembers") or 0) > 0 then return "RAID" end
  if g("IsInGroup") or (g("GetNumPartyMembers") or 0) > 0 then return "PARTY" end
  return nil
end

function TreeAI:Send(msgType, payload, channel, target)
  if not (self.settings and self.settings.comms) then return false end
  if type(msgType) ~= "string" or not msgType:match("^[A-Z0-9_]+$") then return false end
  channel = channel or self:DefaultChannel()
  if not channel then return false end
  local data
  if type(payload) == "table" then data = "t" .. self:Serialize(payload)
  else data = "r" .. tostring(payload or "") end
  seq = (seq % 9999) + 1
  local n = mmax(1, math.ceil(#data / CHUNK))
  for i = 1, n do
    local part = strsub(data, (i - 1) * CHUNK + 1, i * CHUNK)
    sendQueue[#sendQueue + 1] = { strformat("1~%s~%d~%d/%d~%s", msgType, seq, i, n, part), channel, target }
  end
  if not sending then sending = true; pump() end
  return true
end

function TreeAI:RegisterComm(msgType, owner, fn)
  if type(fn) ~= "function" then return false end
  commHandlers[msgType] = commHandlers[msgType] or {}
  commHandlers[msgType][owner or self] = fn
  return true
end

local function deliver(msgType, data, sender, channel)
  local payload
  local kind = strsub(data, 1, 1)
  if kind == "t" then payload = TreeAI:Deserialize(strsub(data, 2)) else payload = strsub(data, 2) end
  local list = commHandlers[msgType]
  if list then
    for owner, fn in pairs(list) do
      if ownerActive(owner) then safecall(fn, owner, payload, sender, channel, msgType) end
    end
  end
  TreeAI:Fire("COMM", msgType, payload, sender, channel)
end

local function isSelf(sender)
  if not sender then return true end
  local me = _G.UnitName and _G.UnitName("player")
  if not me then return false end
  local short = sender:match("^([^%-]+)") or sender
  local key = TreeAI:CharKey()
  return sender == me or sender == key or (short == me and not strfind(sender, "-", 1, true))
      or (key ~= "?" and strgsub(sender, "%s", "") == strgsub(key, "%s", ""))
end

function TreeAI:_OnAddonMessage(prefix, msg, channel, sender)
  if prefix ~= self.PREFIX or type(msg) ~= "string" then return end
  if isSelf(sender) then return end
  local ver, msgType, sq, i, n, data = msg:match("^(%d+)~([A-Z0-9_]+)~(%d+)~(%d+)/(%d+)~(.*)$")
  if ver ~= "1" then return end
  i, n = tonumber(i), tonumber(n)
  if not i or not n or n < 1 or n > 64 then return end
  if n == 1 then return deliver(msgType, data, sender, channel) end
  local key = tostring(sender) .. ":" .. sq
  local p = partial[key]
  if not p then p = { n = n, got = 0, parts = {}, at = now() }; partial[key] = p end
  if not p.parts[i] then p.parts[i] = data; p.got = p.got + 1 end
  if p.got >= p.n then
    partial[key] = nil
    deliver(msgType, tconcat(p.parts, "", 1, p.n), sender, channel)
  end
  -- garbage collect stale partials
  for k, v in pairs(partial) do
    if now() - v.at > 60 then partial[k] = nil end
  end
end

function TreeAI:SendHello(channel)
  local s = self:Summary()
  return self:Send("HELLO", { v = self.version, c = s.class, l = s.level, z = s.zone }, channel)
end

TreeAI:RegisterComm("HELLO", TreeAI, function(self, p, sender)
  if type(p) ~= "table" or not self.db then return end
  local peers = self.db.group.peers
  local isNew = peers[sender] == nil
  peers[sender] = { version = p.v, class = p.c, level = p.l, zone = p.z, seen = wallclock() }
  if isNew and p.r ~= true then
    -- reply once so both sides know each other
    self:After(1 + (math.random and math.random() or 0.5), function()
      local s = self:Summary()
      self:Send("HELLO", { v = self.version, c = s.class, l = s.level, z = s.zone, r = true })
    end)
  end
end)

---------------------------------------------------------------------------
-- Slash commands
---------------------------------------------------------------------------
local slash = {}
local slashOrder = {}

function TreeAI:RegisterSlash(cmd, fn, help)
  if type(cmd) ~= "string" or type(fn) ~= "function" then return false end
  cmd = cmd:lower()
  if not slash[cmd] then slashOrder[#slashOrder + 1] = cmd end
  slash[cmd] = { fn = fn, help = help }
  return true
end

function TreeAI:HandleSlash(input)
  input = tostring(input or "")
  local cmd, rest = input:match("^%s*(%S*)%s*(.-)%s*$")
  cmd = (cmd or ""):lower()
  if cmd == "" or cmd == "aide" or cmd == "help" or cmd == "?" then
    self:Print(L["Commandes"] .. " (/tai, /treeai):")
    for _, c in ipairs(slashOrder) do
      local h = slash[c].help
      if h then self:Print("  |cffffff00" .. c .. "|r - " .. L[h]) end
    end
    return
  end
  local entry = slash[cmd]
  if entry then
    safecall(entry.fn, rest)
  else
    self:Print(L["Commande inconnue. Tape /tai aide"])
  end
end

_G.SLASH_TREEAI1 = "/treeai"
_G.SLASH_TREEAI2 = "/tai"
if type(_G.SlashCmdList) == "table" then
  _G.SlashCmdList["TREEAI"] = function(msg) TreeAI:UserAction(TreeAI.HandleSlash, TreeAI, msg) end
end

local function onoff(v) return v and L["activé"] or L["désactivé"] end

TreeAI:RegisterSlash("pvp", function()
  TreeAI.settings.pvp = not TreeAI.settings.pvp
  TreeAI:Print("Aides PvP : " .. (TreeAI.settings.pvp and "activées" or "désactivées (aide à quêter seulement)"))
end, "aides PvP oui/non")
TreeAI:RegisterSlash("afficher", function() TreeAI:ShowPanel() end, L["afficher le panneau"])
TreeAI:RegisterSlash("show", function() TreeAI:ShowPanel() end)
TreeAI:RegisterSlash("cacher", function() TreeAI:HidePanel() end, L["cacher le panneau"])
TreeAI:RegisterSlash("hide", function() TreeAI:HidePanel() end)
TreeAI:RegisterSlash("basculer", function() TreeAI:TogglePanel() end, L["afficher/cacher"])
TreeAI:RegisterSlash("verrou", function()
  local p = TreeAI.settings.panel
  p.locked = not p.locked
  TreeAI:Print(L["Panneau verrouillé"] .. ": " .. onoff(p.locked))
end, L["verrouiller/déverrouiller le panneau"])
TreeAI:RegisterSlash("reset", function()
  if TreeAI.ResetPanel then TreeAI:ResetPanel() end
  TreeAI:Print(L["Position du panneau réinitialisée."])
end, L["remettre le panneau au centre"])
TreeAI:RegisterSlash("mini", function() if TreeAI.ToggleMini then TreeAI:ToggleMini() end end,
  L["panneau réduit à une ligne (oui/non)"])
TreeAI:RegisterSlash("onglet", function(rest)
  rest = tostring(rest or ""):lower()
  local ids = {}
  for _, t in ipairs(TreeAI:GetTabs()) do
    if t.id == rest or TreeAI.PlainText(t.title):lower() == rest then
      if TreeAI.SelectTab then TreeAI:SelectTab(t.id) end
      return
    end
    ids[#ids + 1] = t.id
  end
  TreeAI:Print("/tai onglet " .. tconcat(ids, " | "))
end, L["choisir l'onglet du panneau"])
TreeAI:RegisterSlash("alerte", function()
  if TreeAI.ShowAlert then
    TreeAI:ShowAlert({ text = L["Exemple : encadré d'alerte (clic : ouvre le panneau)."], prio = 3, tab = "now" }, true)
  end
end, L["tester l'encadré d'alerte"])
TreeAI:RegisterSlash("options", function() if TreeAI.OpenOptions then TreeAI:OpenOptions() end end, L["ouvrir les options"])
TreeAI:RegisterSlash("modules", function()
  for name, mod in TreeAI:IterModules() do
    TreeAI:Print("  " .. (mod.title or name) .. " (" .. name .. "): " .. onoff(mod:IsEnabled()))
  end
end, L["liste des modules"])
TreeAI:RegisterSlash("module", function(rest)
  local name, state = rest:match("^(%S+)%s*(%S*)")
  local mod
  if name then
    for n, m in TreeAI:IterModules() do
      if n:lower() == name:lower() then mod = m; name = n end
    end
  end
  if not mod then TreeAI:Print(L["Module introuvable."]); return end
  local enable = not mod:IsEnabled()
  if state == "on" then enable = true elseif state == "off" then enable = false end
  TreeAI:SetModuleEnabled(name, enable)
  TreeAI:Print((mod.title or name) .. ": " .. onoff(enable))
end, L["<Nom> on|off"])
TreeAI:RegisterSlash("sync", function()
  if TreeAI:SendHello() then TreeAI:Print(L["Synchro envoyée au groupe."])
  else TreeAI:Print(L["Pas de groupe (ou comms désactivées)."]) end
end, L["synchroniser avec le groupe"])
TreeAI:RegisterSlash("debug", function()
  TreeAI.settings.debug = not TreeAI.settings.debug
  TreeAI:Print("Debug: " .. onoff(TreeAI.settings.debug))
end, L["mode debug"])
TreeAI:RegisterSlash("statut", function()
  local s = TreeAI:UpdateSummary()
  TreeAI:Print(strformat("v%s | %s %s | %s | %s: %d/%d", TreeAI.version, tostring(s.className or "?"),
    tostring(s.level or "?"), tostring(s.zone or "?"), L["Sacs libres"], s.bagsFree or 0, s.bagsTotal or 0))
  local n = 0
  for _ in pairs(TreeAI.db.group.peers) do n = n + 1 end
  TreeAI:Print(L["Amis TreeAI connus"] .. ": " .. n)
end, L["état de l'addon"])
TreeAI:RegisterSlash("test", function()
  TreeAI:Suggest("core:test1", L["Exemple: conseil d'information."], 1, { ttl = 20 })
  TreeAI:Suggest("core:test2", L["Exemple: conseil utile."], 2, { ttl = 20 })
  TreeAI:Suggest("core:test3", L["Exemple: alerte urgente !"], 3, { ttl = 20, sound = false })
end, L["afficher des suggestions de test"])

---------------------------------------------------------------------------
-- Lifecycle
---------------------------------------------------------------------------
function TreeAI:ADDON_LOADED(_, name)
  if name ~= ADDON_NAME then return end
  initDB()
  self._inited = true
  for _, n in ipairs(self.moduleOrder) do moduleInit(self.modules[n]) end
  local C_ChatInfo = _G.C_ChatInfo
  if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
    pcall(C_ChatInfo.RegisterAddonMessagePrefix, self.PREFIX)
  elseif _G.RegisterAddonMessagePrefix then
    pcall(_G.RegisterAddonMessagePrefix, self.PREFIX)
  end
  if self.InitOptions then safecall(self.InitOptions, self) end
end

function TreeAI:PLAYER_LOGIN()
  if not self._inited then initDB(); self._inited = true end
  self._charKey = nil
  self._loggedIn = true
  -- rebind char data now that the name is known
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if not mod._inited then moduleInit(mod) else moduleBindDB(mod) end
  end
  if self._tmpSummary then
    local s = self:Summary()
    for k, v in pairs(self._tmpSummary) do if s[k] == nil then s[k] = v end end
    self._tmpSummary = nil
  end
  self:UpdateSummary()
  if self.CreatePanel then safecall(self.CreatePanel, self) end
  for _, n in ipairs(self.moduleOrder) do moduleEnable(self.modules[n]) end
  self:Fire("READY")
  self:StartAutoRefresh()
  self:After(5, function() self:SendHello() end)
end

function TreeAI:PLAYER_LOGOUT()
  if self.db then safecall(self.UpdateSummary, self) end
end

function TreeAI:PLAYER_REGEN_DISABLED()
  self:Fire("COMBAT_START")
end

function TreeAI:PLAYER_REGEN_ENABLED()
  local order = oocOrder
  oocOrder = {}
  for _, key in ipairs(order) do
    local fn = oocQueue[key]
    oocQueue[key] = nil
    if fn then safecall(fn) end
  end
  self:Fire("COMBAT_END")
end

function TreeAI:CHAT_MSG_ADDON(_, prefix, msg, channel, sender)
  self:_OnAddonMessage(prefix, msg, channel, sender)
end

local summaryPending = false
function TreeAI:_QueueSummary()
  if summaryPending or not self._loggedIn then return end
  summaryPending = true
  self:After(2, function() summaryPending = false; self:UpdateSummary() end)
end

function TreeAI:GROUP_ROSTER_UPDATE()
  self:_QueueSummary()
  self:Fire("GROUP_CHANGED")
end

-- Diagnostic : la fonction exacte que le jeu a bloquée (« TreeAI a été bloqué… »), dite dans le chat et gardée
-- dans TreeAIDB.diag.blocked (20 dernières) pour la corriger vite.
local function onBlocked(kind, addon, func)
  if addon ~= ADDON_NAME and addon ~= "TreeAI" then return end
  func = tostring(func or "?")
  local db = TreeAI.db
  if type(db) == "table" then
    db.diag = type(db.diag) == "table" and db.diag or {}
    local list = type(db.diag.blocked) == "table" and db.diag.blocked or {}
    list[#list + 1] = { kind = kind, func = func, t = (time and time()) or 0,
                        combat = (InCombatLockdown and InCombatLockdown()) and true or false }
    while #list > 20 do table.remove(list, 1) end
    db.diag.blocked = list
  end
  local ctx = TreeAI._ctx or TreeAI._lastCtx or "?"
  if type(db) == "table" and db.diag and db.diag.blocked then db.diag.blocked[#db.diag.blocked].ctx = ctx end
  TreeAI:Print(("Action bloquée par le jeu : %s (%s) pendant %s. Envoie ce message à TreeAI pour la correction.")
    :format(func, kind, ctx))
end
function TreeAI:ADDON_ACTION_FORBIDDEN(_, addon, func) onBlocked("interdite", addon, func) end
function TreeAI:ADDON_ACTION_BLOCKED(_, addon, func) onBlocked("en combat", addon, func) end

TreeAI:RegisterEvent("ADDON_ACTION_FORBIDDEN")
TreeAI:RegisterEvent("ADDON_ACTION_BLOCKED")
TreeAI:RegisterEvent("ADDON_LOADED")
TreeAI:RegisterEvent("PLAYER_LOGIN")
TreeAI:RegisterEvent("PLAYER_LOGOUT")
TreeAI:RegisterEvent("PLAYER_REGEN_DISABLED")
TreeAI:RegisterEvent("PLAYER_REGEN_ENABLED")
TreeAI:RegisterEvent("CHAT_MSG_ADDON")
TreeAI:RegisterEvent("GROUP_ROSTER_UPDATE")
TreeAI:RegisterEvent("PARTY_MEMBERS_CHANGED", TreeAI, "GROUP_ROSTER_UPDATE")
for _, ev in ipairs({ "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA", "PLAYER_MONEY", "PLAYER_GUILD_UPDATE" }) do
  TreeAI:RegisterEvent(ev, TreeAI, function(self) self:_QueueSummary() end)
end
