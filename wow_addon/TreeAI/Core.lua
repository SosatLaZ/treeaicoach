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
TreeAI.version = "0.4.0"
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
  if not ok then
    TreeAI:Debug("erreur:", err)
    local hook = TreeAI._errorHook   -- SelfTest : garde les 30 dernières erreurs (TreeAIDB.diag.errors)
    if hook then pcall(hook, err, TreeAI._ctx) end
  end
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

-- Valeurs secrètes (Forever = famille Midnight) : en combat, certaines valeurs (PV d'une autre unité, auras,
-- GUID / nom d'un monstre…) sont « secrètes » : les comparer, les additionner ou s'en servir de clé lève une
-- erreur Lua. API.Pub(v) rend nil pour une valeur secrète (et v sinon) : à mettre autour de ces lectures.
function API.IsSecret(v)
  local f = _G.issecretvalue
  if type(f) ~= "function" or v == nil then return false end
  local ok, r = pcall(f, v)
  return ok and r and true or false
end
function API.Pub(v)
  if v ~= nil and API.IsSecret(v) then return nil end
  return v
end
local pub = API.Pub

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
  local SB = _G.C_SpellBook   -- Forever : C_SpellBook (IsPlayerSpell / IsSpellKnown globaux = repli déprécié)
  if SB and SB.IsSpellKnown and try(SB.IsSpellKnown, id) then return true end
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
  local name = pub(d.name)
  if name == nil and pub(d.spellId) == nil then return { secret = true } end   -- aura masquée (combat)
  return { name = name, icon = pub(d.icon), count = pub(d.applications) or 0, duration = pub(d.duration) or 0,
           expires = pub(d.expirationTime) or 0, spellId = pub(d.spellId), source = pub(d.sourceUnit) }
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
    if not a.secret then out[#out + 1] = a end
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
    y1, x1, y2, x2, m1, m2 = pub(y1), pub(x1), pub(y2), pub(x2), pub(m1), pub(m2)
    if y1 and y2 and x1 and x2 and m1 == m2 then
      local dx, dy = x2 - x1, y2 - y1
      return (dx * dx + dy * dy) ^ 0.5
    end
  end
  if _G.CheckInteractDistance then
    if pub(try(_G.CheckInteractDistance, unit, 3)) then return 10 end
    if pub(try(_G.CheckInteractDistance, unit, 4)) then return 28 end
  end
  return nil
end

-- Compatibilité Forever 1.60.1 (vérifiée sur Blizzard_APIDocumentationGenerated, branche « forever ») :
-- les globales de Classic Era (GetPetHappiness, GetNumSkillLines, GetNumSpellTabs, GetQuestLogTitle…) n'y
-- existent plus ; on lit d'abord le C_* puis on se replie sur la globale (Era).
local function petInfo(name) local P = _G.C_PetInfo return (P and P[name]) or _G[name] end
function API.PetHappiness() return try(petInfo("GetPetHappiness")) end          -- happiness, damage%, loyaltyRate
function API.PetLoyalty() return try(petInfo("GetPetLoyalty")) end
function API.PetTrainingPoints() return try(petInfo("GetPetTrainingPoints")) end  -- total, used
function API.PetFoodTypes()                                                       -- { "Viande", ... }
  local r = { try(petInfo("GetPetFoodTypes")) }
  if type(r[1]) == "table" then return r[1] end
  return r
end

--- Lignes de compétence (armes, métiers…) : { {name=, isHeader=, rank=, maxRank=, id=}, ... }
function API.SkillLines()
  local out, SI = {}, _G.C_SkillInfo
  if SI and SI.GetNumSkillLines and SI.GetSkillLineInfo then
    for i = 1, (tonumber(try(SI.GetNumSkillLines)) or 0) do
      local d = try(SI.GetSkillLineInfo, i)
      if type(d) == "table" then
        out[#out + 1] = { name = d.name, isHeader = d.isHeader, rank = d.rank, maxRank = d.maxRank, id = d.skillID }
      end
    end
    return out
  end
  if _G.GetNumSkillLines and _G.GetSkillLineInfo then
    for i = 1, (tonumber(try(_G.GetNumSkillLines)) or 0) do
      local name, isHeader, _, rank, _, _, maxRank = try(_G.GetSkillLineInfo, i)
      if name then out[#out + 1] = { name = name, isHeader = isHeader, rank = rank, maxRank = maxRank } end
    end
  end
  return out
end

--- Sorts du grimoire : { {name=, sub=, passive=, slot=}, ... } ; pet = true pour le familier.
function API.SpellBookSpells(pet)
  local out, SB, E = {}, _G.C_SpellBook, _G.Enum
  local bankEnum = E and E.SpellBookSpellBank
  local slots = {}
  if SB and SB.GetSpellBookItemName and bankEnum then
    local bank = pet and bankEnum.Pet or bankEnum.Player
    if pet then
      for i = 1, (tonumber((try(SB.HasPetSpells))) or 0) do slots[#slots + 1] = i end
    elseif SB.GetNumSpellBookSkillLines and SB.GetSpellBookSkillLineInfo then
      for l = 1, (tonumber(try(SB.GetNumSpellBookSkillLines)) or 0) do
        local info = try(SB.GetSpellBookSkillLineInfo, l)
        if type(info) == "table" and not info.shouldHide then
          local off = tonumber(info.itemIndexOffset) or 0
          for i = off + 1, off + (tonumber(info.numSpellBookItems) or 0) do slots[#slots + 1] = i end
        end
      end
    end
    for _, i in ipairs(slots) do
      local name, sub = try(SB.GetSpellBookItemName, i, bank)
      if name then
        out[#out + 1] = { name = name, sub = sub, slot = i,
                          passive = SB.IsSpellBookItemPassive and try(SB.IsSpellBookItemPassive, i, bank) or false }
      end
    end
    return out
  end
  local bt = pet and (_G.BOOKTYPE_PET or "pet") or (_G.BOOKTYPE_SPELL or "spell")
  if pet then
    for i = 1, (tonumber((try(_G.HasPetSpells))) or 0) do slots[#slots + 1] = i end
  elseif _G.GetNumSpellTabs and _G.GetSpellTabInfo then
    for tab = 1, (tonumber(try(_G.GetNumSpellTabs)) or 0) do
      local _, _, off, num = try(_G.GetSpellTabInfo, tab)
      for i = (off or 0) + 1, (off or 0) + (num or 0) do slots[#slots + 1] = i end
    end
  end
  for _, i in ipairs(slots) do
    local name, sub = try(_G.GetSpellBookItemName, i, bt)
    if name then out[#out + 1] = { name = name, sub = sub, slot = i, passive = try(_G.IsPassiveSpell, i, bt) or false } end
  end
  return out
end

--- Entrées du journal de quêtes (lecture seule, sans toucher à la sélection Blizzard) :
--- { {title=, level=, isHeader=, isComplete=, questID=, index=}, ... }
function API.QuestLogEntries()
  local out, QL = {}, _G.C_QuestLog
  if QL and QL.GetNumQuestLogEntries and QL.GetInfo then
    for i = 1, (tonumber((try(QL.GetNumQuestLogEntries))) or 0) do
      local q = try(QL.GetInfo, i)
      if type(q) == "table" then
        local done = q.questID and QL.IsComplete and try(QL.IsComplete, q.questID)
        out[#out + 1] = { title = q.title, level = q.level, isHeader = q.isHeader, questID = q.questID, index = i,
                          isComplete = done and 1 or nil }
      end
    end
    return out
  end
  if _G.GetNumQuestLogEntries and _G.GetQuestLogTitle then
    for i = 1, (tonumber((try(_G.GetNumQuestLogEntries))) or 0) do
      local title, lvl, _, isHeader, _, isComplete, _, qid = try(_G.GetQuestLogTitle, i)
      if title then
        out[#out + 1] = { title = title, level = lvl, isHeader = isHeader, isComplete = isComplete, questID = qid, index = i }
      end
    end
  end
  return out
end

--- Sort en cours du joueur : name, startMS, endMS (UnitCastingInfo ; CastingInfo = vieux Classic).
function API.PlayerCasting()
  if _G.UnitCastingInfo then
    local n, _, _, s, e = try(_G.UnitCastingInfo, "player")
    return pub(n), pub(s), pub(e)
  end
  if _G.CastingInfo then
    local n, _, _, s, e = try(_G.CastingInfo)
    return n, s, e
  end
end

function API.HasAction(slot)
  local AB = _G.C_ActionBar
  if AB and AB.HasAction then return try(AB.HasAction, slot) and true or false end
  return try(_G.HasAction, slot) and true or false
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

local profile = _G.debugprofilestop
TreeAI._stats = TreeAI._stats or {}   -- [module] = { calls, ms } (SelfTest / « /tai test »)
local function dispatch(event, ...)
  local list = handlers[event]
  if not list then return end
  for owner, fn in pairs(list) do
    if ownerActive(owner) then
      local name = tostring(owner.name or "core")
      TreeAI._ctx = name .. ":" .. tostring(event)
      local t0 = profile and profile()
      safecall(fn, owner, event, ...)
      if t0 then
        local st = TreeAI._stats[name]
        if not st then st = { calls = 0, ms = 0 }; TreeAI._stats[name] = st end
        st.calls = st.calls + 1
        st.ms = st.ms + ((profile() or t0) - t0)
      end
    end
  end
  TreeAI._ctx = nil
end

--- Forever (restrictions Midnight) : s'abonner à COMBAT_LOG_EVENT(_UNFILTERED) déclenche ADDON_ACTION_FORBIDDEN
--- (« TreeAI a été bloqué »), même dans un pcall. On ne s'y abonne que sur un client sans valeurs secrètes.
local RESTRICTED_EVENTS = { COMBAT_LOG_EVENT_UNFILTERED = true, COMBAT_LOG_EVENT = true }
function TreeAI:CombatLogAllowed()
  if type(_G.issecretvalue) == "function" then return false end
  local CL = _G.C_CombatLog
  if CL and CL.IsCombatLogRestricted then
    local ok, r = pcall(CL.IsCombatLogRestricted)
    if ok and r then return false end
  end
  return true
end
function TreeAI:EventAllowed(event)
  if RESTRICTED_EVENTS[event] then return self:CombatLogAllowed() end
  return true
end

function TreeAI:RegisterEvent(event, owner, fn)
  owner = owner or self
  if not self:EventAllowed(event) then return false end
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
  if not ok then
    self:Debug("action:", err)
    if self._errorHook then pcall(self._errorHook, err, "action") end
  end
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
        self._ctx = name .. ":AutoRefresh"
        safecall(m.AutoRefresh, m)
        self._ctx = nil
      end
    end
    self:Fire("REFRESH")
    if self.RefreshPanel then safecall(self.RefreshPanel, self) end
  end)
end

function TreeAI:Fire(message, ...)
  local list = messages[message]
  if not list then return end
  local prev = TreeAI._ctx
  for owner, fn in pairs(list) do
    if ownerActive(owner) then
      TreeAI._ctx = tostring(type(owner) == "table" and owner.name or "core") .. ":" .. tostring(message)
      safecall(fn, owner, message, ...)
    end
  end
  TreeAI._ctx = prev
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
            point = "CENTER", x = 300, y = 120, maxLines = 8, nowMax = 5, width = 320,
            fontSize = 11, lineGap = 3, iconSize = 13 },
  alert = { enabled = true, corner = "TOPRIGHT", duration = 8, scale = 1, alpha = 0.95 },
  -- anti-bruit (réglé par le préréglage « calme / normal / tout », chaque valeur reste modifiable)
  preset = "normal",
  noise = { nowMinPrio = 1, perModule = 2, alertGap = 20, topicGap = 120 },
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
  TreeAI._freshDB = type(db.settings) ~= "table"
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

local function deepcopy(v)
  if type(v) ~= "table" then return v end
  local t = {}
  for k, x in pairs(v) do t[k] = deepcopy(x) end
  return t
end
TreeAI.DeepCopy = deepcopy

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

-- Profil : réglages du compte (défaut) ou propres à ce personnage (CharDB().profile == "char").
-- TreeAI.settings et mod.db pointent toujours vers le profil actif.
function TreeAI:ProfileMode()
  if not self.db or self:CharKey() == "?" then return "account" end
  local c = self.db.chars[self:CharKey()]
  return (type(c) == "table" and c.profile == "char") and "char" or "account"
end

local function moduleStore()
  local db = TreeAI.db
  if TreeAI:ProfileMode() == "char" then
    local c = TreeAI:CharDB()
    c.moduleSettings = type(c.moduleSettings) == "table" and c.moduleSettings or {}
    return c.moduleSettings, db.modules
  end
  return db.modules
end

local function bindSettings()
  local db = TreeAI.db
  if not db then return end
  if TreeAI:ProfileMode() == "char" then
    local c = TreeAI:CharDB()
    if type(c.settings) ~= "table" then c.settings = deepcopy(db.settings) end
    c.settings = mergeDefaults(c.settings, DEFAULT_SETTINGS)
    TreeAI.settings = c.settings
  else
    TreeAI.settings = db.settings
  end
end

local function moduleBindDB(mod)
  local db = TreeAI.db
  if not db then return end
  local defaults = mergeDefaults({ enabled = true }, mod.defaults or {})
  local store, seed = moduleStore()
  if store[mod.name] == nil and seed and type(seed[mod.name]) == "table" then
    store[mod.name] = deepcopy(seed[mod.name])   -- profil perso : part des réglages du compte
  end
  store[mod.name] = mergeDefaults(store[mod.name], defaults)
  mod.db = store[mod.name]
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
  if enabled then
    if self._loggedIn then moduleEnable(mod) end
  else
    moduleDisable(mod)
  end
  self:RequestRefresh()
  return true
end

--- Tout TreeAI on/off, en direct (sans /reload).
function TreeAI:SetEnabled(v)
  if not self.settings then return end
  self.settings.enabled = v and true or false
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if v then if self._loggedIn then moduleEnable(mod) end else moduleDisable(mod) end
  end
  if not v then
    self:ClearSuggestions()
    if self.HidePanel then safecall(self.HidePanel, self) end
  elseif self.ShowPanel and self.settings.panel and self.settings.panel.shown ~= false then
    safecall(self.ShowPanel, self)
  end
  self:RequestRefresh()
end

--- Profil "account" (réglages communs à tes persos) ou "char" (propres à ce perso), en direct.
function TreeAI:SetProfile(mode)
  mode = (mode == "char") and "char" or "account"
  if not self.db or self:CharKey() == "?" or mode == self:ProfileMode() then return false end
  local wasActive = {}
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if mod._active then wasActive[n] = true; moduleDisable(mod) end
  end
  self:CharDB().profile = (mode == "char") and "char" or nil
  bindSettings()
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    moduleBindDB(mod)
    if self._loggedIn then moduleEnable(mod) end
  end
  if self.ApplyPanelSettings then safecall(self.ApplyPanelSettings, self) end
  self:Fire("PROFILE_CHANGED", mode)
  self:RequestRefresh()
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
local alerted = {}       -- id -> { sk, at } (corner alert pops)
TreeAI._alerted = alerted
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


---------------------------------------------------------------------------
-- Anti-bruit : sujets (même sens = une seule ligne), budget « Maintenant », encadré ≤ 1 / 20 s,
-- rien dans l'encadré en combat sauf danger de mort, préréglages « calme / normal / tout ».
---------------------------------------------------------------------------
-- id complet de suggestion -> sujet. Plusieurs modules qui disent la même chose partagent un sujet :
-- « Maintenant » n'en garde qu'une ligne (la plus prioritaire), l'encadré ne la répète pas.
TreeAI.TOPIC_IDS = TreeAI.TOPIC_IDS or {
  ["Hunter:ammo"] = "ammo", ["Predict:ammo"] = "ammo", ["HunterPro:ammo"] = "ammo", ["Boost:ammo30"] = "ammo",
  ["Inventory:inv_bags"] = "bags", ["Predict:bags"] = "bags", ["Boost:hearth"] = "bags",
  ["Inventory:inv_repair"] = "repair", ["Predict:repair"] = "repair",
  ["Predict:food"] = "food", ["Predict:water"] = "food", ["Boost:food"] = "food",
  ["Leveling:lvl_train"] = "train", ["Predict:train"] = "train",
  ["Hunter:aspect"] = "aspect", ["HunterPro:aspect"] = "aspect",
  ["Hunter:fd"] = "fd", ["HunterPro:fd"] = "fd",
  ["Hunter:hungry"] = "petfood", ["Fight:mood"] = "petfood",
  ["Leveling:lvl_hub"] = "hub", ["Tracker:hub"] = "hub",
  ["Hunter:petdead"] = "pet", ["Hunter:pethp"] = "pet", ["Hunter:nopet"] = "pet",
  ["Quests:grey"] = "abandon",
  ["Fight:interrupt"] = "interrupt", ["Fight:ccend"] = "cc", ["Fight:ccbreak"] = "cc",
}
-- repli par le texte (lignes de groupe, nouveaux modules) : motif en minuscules -> sujet
TreeAI.TOPIC_PATTERNS = TreeAI.TOPIC_PATTERNS or {
  { "munitions", "ammo" }, { "plus de flèches", "ammo" }, { "plus de balles", "ammo" },
  { "sacs pleins", "bags" }, { "sacs presque pleins", "bags" },
  { "répar", "repair" }, { "durabilité", "repair" }, { "abandonne", "abandon" },
}
-- danger de mort : seules alertes permises dans l'encadré pendant un combat
TreeAI.DANGER_IDS = TreeAI.DANGER_IDS or {
  ["Fight:ccbreak"] = true, ["Fight:ccend"] = true, ["Fight:interrupt"] = true, ["HunterPro:fd"] = true,
  ["Boost:threat"] = true, ["Hunter:aspect"] = true, ["Hunter:fd"] = true,
}
TreeAI.PRESETS = {
  -- simple (défaut) : 3 lignes, seulement ce qu'on peut faire (clic ou conseil utile), pas de bavardage
  simple = { nowMax = 3, nowMinPrio = 1, perModule = 1, alertGap = 45, topicGap = 600, soundTips = false,
             actionable = true },
  calme = { nowMax = 3, nowMinPrio = 2, perModule = 1, alertGap = 45, topicGap = 600, soundTips = false },
  normal = { nowMax = 5, nowMinPrio = 1, perModule = 2, alertGap = 20, topicGap = 120 },
  tout   = { nowMax = 8, nowMinPrio = 1, perModule = 0, alertGap = 20, topicGap = 60 },
}
TreeAI.PRESET_ORDER = { "simple", "calme", "normal", "tout" }
TreeAI.PRESET_LABEL = { simple = "Simple", calme = "Mode calme", normal = "Normal", tout = "Tout", perso = "Perso" }
TreeAI.PRESET_HELP = {
  simple = "3 lignes, seulement ce que tu peux faire maintenant",
  calme = "3 lignes, seulement l'utile, alerte rare (45 s mini)",
  normal = "5 lignes, une alerte max toutes les 20 s",
  tout = "8 lignes, tous les conseils (même les petits)",
}

local function noiseCfg()
  local s = TreeAI.settings
  local n = s and s.noise
  if type(n) ~= "table" then return TreeAI.PRESETS.normal end
  return n
end

--- Sujet d'une ligne / suggestion (nil = sans sujet : dédoublonnée seulement au texte exact).
function TreeAI:TopicOf(e)
  if type(e) ~= "table" then return nil end
  if e.topic then return e.topic end
  local t = e.id and self.TOPIC_IDS[e.id]
  if t then return t end
  local txt = string.lower(self.PlainText and self.PlainText(e.text) or tostring(e.text or ""))
  for _, p in ipairs(self.TOPIC_PATTERNS) do
    if strfind(txt, p[1], 1, true) then return p[2] end
  end
  return nil
end

function TreeAI:IsDanger(e, opts)
  if not e or (e.prio or 0) < 3 then return false end
  return (opts and opts.danger) or e.danger or self.DANGER_IDS[e.id or ""] or false
end

--- Préréglage : "calme" (peu de lignes, seulement l'utile), "normal", "tout". Appliqué en direct.
function TreeAI:ApplyPreset(name)
  local p = self.PRESETS[name]
  local s = self.settings
  if not (p and s) then return false end
  s.preset = name
  s.noise = type(s.noise) == "table" and s.noise or {}
  s.noise.nowMinPrio, s.noise.perModule = p.nowMinPrio, p.perModule
  s.noise.alertGap, s.noise.topicGap = p.alertGap, p.topicGap
  s.noise.actionable = p.actionable and true or nil
  if s.panel then s.panel.nowMax = p.nowMax end
  if p.soundTips ~= nil then s.soundTips = p.soundTips end
  self:Fire("PRESET_CHANGED", name)
  self:RequestRefresh()
  return true
end

---------------------------------------------------------------------------
-- État du joueur (CONTENTFIX) : mort / fantôme / vol / monture / repos… -> qui a le droit de parler.
--   TreeAI:PlayerState() -> { dead, ghost, taxi, mounted, resting, inCombat, inInstance, afk } (cache 0,25 s)
--   TreeAI:Quiet(kind) -> true, raison quand une ligne de ce genre doit se taire :
--     "buff"   (aspect, buff, familier, munitions) : mort, fantôme, vol, monture, repos
--     "advice" (tout le reste, défaut)              : mort, fantôme, vol (gardé, réaffiché après)
--     "death"  (course au corps, gardien des esprits, récap de mort) : jamais
-- Suggest applique la règle : "buff" jeté, "advice" mis en attente (caché, sans son ni encadré).
---------------------------------------------------------------------------
local stateCache, stateAt = nil, -1
local function tryState(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, v = pcall(fn, ...)
  if not ok then return nil end
  if _G.issecretvalue then
    local ok2, s = pcall(_G.issecretvalue, v)
    if ok2 and s then return nil end
  end
  return v
end
function TreeAI:PlayerState(force)
  local t = now()
  if stateCache and not force and t - stateAt < 0.25 then return stateCache end
  local ghost = tryState(_G.UnitIsGhost, "player") and true or false
  local dog = tryState(_G.UnitIsDeadOrGhost, "player") and true or false
  local inst = tryState(_G.IsInInstance)
  stateCache = {
    dead = (dog and not ghost) or false, ghost = ghost,
    taxi = tryState(_G.UnitOnTaxi, "player") and true or false,
    mounted = tryState(_G.IsMounted) and true or false,
    resting = tryState(_G.IsResting) and true or false,
    inCombat = self:InCombat(),
    inInstance = inst and true or false,
    afk = tryState(_G.UnitIsAFK, "player") and true or false,
  }
  stateAt = t
  return stateCache
end
function TreeAI:InvalidatePlayerState() stateCache = nil end

TreeAI.QUIET_RULES = TreeAI.QUIET_RULES or {
  buff = { "dead", "ghost", "taxi", "mounted", "resting" },
  advice = { "dead", "ghost", "taxi" },
  death = {}, always = {},
}
-- sujet -> genre (aspects, familier, munitions = rappels « buff »)
TreeAI.QUIET_TOPICS = TreeAI.QUIET_TOPICS or { aspect = "buff", pet = "buff", petfood = "buff", ammo = "buff", buff = "buff" }
-- modules dont tous les conseils sont des rappels de buff
TreeAI.QUIET_MODULES = TreeAI.QUIET_MODULES or { Classes = "buff" }

function TreeAI:Quiet(kind)
  local rule = self.QUIET_RULES[kind or "advice"] or self.QUIET_RULES.advice
  if #rule == 0 then return false end
  local st = self:PlayerState()
  for _, k in ipairs(rule) do if st[k] then return true, k end end
  return false
end

--- Genre d'une suggestion / ligne : opts.kind ou e.kind, sinon module / sujet, sinon "advice".
function TreeAI:QuietKindOf(e)
  if type(e) ~= "table" then return "advice" end
  if e.kind then return e.kind end
  if e.death then return "death" end
  local m = e.module and self.QUIET_MODULES[e.module]
  if m then return m end
  local topic = e.topic or (e.id and self.TOPIC_IDS[e.id])
  return (topic and self.QUIET_TOPICS[topic]) or "advice"
end

TreeAI.STATE_EVENTS = { "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST", "PLAYER_UPDATE_RESTING" }
for _, ev in ipairs(TreeAI.STATE_EVENTS) do
  if not TreeAI[ev] then
    TreeAI[ev] = function(self) stateCache = nil; self:RequestRefresh() end
  end
end

-- Glyphes absents des polices WoW (carrés vides en jeu) -> équivalents lisibles. Appliqué aux textes
-- des conseils et des lignes d'onglet.
local GLYPHS = {
  ["▶"] = "", ["►"] = "", ["→"] = ">", ["←"] = "<", ["⇒"] = ">", ["✔"] = "OK", ["✓"] = "OK",
  ["✖"] = "x", ["✗"] = "x", ["●"] = "•", ["○"] = "o", ["↑"] = "+", ["↓"] = "-", ["≠"] = "<>",
  ["★"] = "*", ["☆"] = "*", ["⚠"] = "!", ["⏱"] = "", ["⌛"] = "",
}
local safeCache, safeN = {}, 0
local function safeText(s)
  if not strfind(s, "[\226\227]") then return s end
  local c = safeCache[s]
  if c then return c end
  local r = strgsub(s, "[\226\227][\128-\191][\128-\191]", GLYPHS)
  r = strgsub(strgsub(r, "^%s+", ""), "  +", " ")
  if safeN > 400 then safeCache, safeN = {}, 0 end
  safeCache[s], safeN = r, safeN + 1
  return r
end
TreeAI.SafeText = safeText

-- ≤ 2 lignes courtes : au-delà de LINE_MAX caractères visibles, la fin (après « — », « . », « ; »…)
-- passe dans l'infobulle ; sinon coupe au dernier espace + « … » (texte complet dans l'infobulle).
TreeAI.LINE_MAX = 96
local function ulen(s)
  s = strgsub(strgsub(s, "|c%x%x%x%x%x%x%x%x", ""), "|r", "")
  local _, n = strgsub(s, "[\128-\191]", "")
  return #s - n
end
TreeAI.TextLen = ulen
local SEPS = { " — ", ". ", " ; ", " : ", " (", ", " }
local function shortText(text, tooltip)
  local max = TreeAI.LINE_MAX
  if #text <= max or ulen(text) <= max then return text, tooltip end
  local head, tail
  for _, sep in ipairs(SEPS) do
    local best, init = nil, 1
    while true do
      local a, b = strfind(text, sep, init, true)
      if not a then break end
      if ulen(strsub(text, 1, a - 1)) <= max then best = a else break end
      init = b + 1
    end
    if best and ulen(strsub(text, 1, best - 1)) >= 24 then
      head = strsub(text, 1, best - 1)
      tail = strgsub(strsub(text, best), "^[%s—%.;:,]+", "")
      if sep == " (" then tail = strgsub(strgsub(tail, "^%(", ""), "%)%s*%.?$", "") end
      break
    end
  end
  if not head then
    local cutAt = 1
    local init = 1
    while true do
      local a = strfind(text, " ", init, true)
      if not a or ulen(strsub(text, 1, a - 1)) > max - 1 then break end
      cutAt, init = a, a + 1
    end
    head, tail = strsub(text, 1, cutAt - 1) .. "…", text
  end
  if (strfind(head, "|c", 1, true) or 0) > (strfind(head, "|r", 1, true) or 0) then head = head .. "|r" end
  tail = strgsub(tail, "^%s+", "")
  if tail == "" then return head, tooltip end
  local tip
  if type(tooltip) == "table" then
    tip = { tail }
    for i = 1, #tooltip do tip[#tip + 1] = tooltip[i] end
  elseif type(tooltip) == "string" and tooltip ~= "" then tip = { tail, tooltip }
  else tip = tail end
  return head, tip
end
TreeAI.ShortText = shortText

--- Nom propre d'un objectif de quête : « Atrexis : 0/1 », « 0/1 Atrexis », « Atrexis tué : 0/1 (Terminé) »
--- -> « Atrexis ». Espaces insécables du client FR compris. Jamais nil.
function TreeAI.ObjectiveName(text)
  local s = tostring(text or "")
  s = strgsub(s, "\194\160", " ")
  s = strgsub(s, "\226\128\175", " ")
  s = strgsub(s, "|c%x%x%x%x%x%x%x%x", "")
  s = strgsub(s, "|r", "")
  s = strgsub(s, "%s*%(%s*[TtCcFf][^%)]*%)%s*$", "")          -- (Terminé) (Complete) (Facultatif)
  s = strgsub(s, "%s*:?%s*%d+%s*/%s*%d+%s*$", "")             -- … : 0/1
  s = strgsub(s, "^%s*%d+%s*/%s*%d+%s*", "")                  -- 0/1 …
  s = strgsub(s, "%s*:%s*$", "")
  s = strgsub(s, "^%s+", "")
  s = strgsub(s, "%s+$", "")
  return s
end

-- Mode simple : préréglage par défaut (et proposé une fois aux anciens utilisateurs)
TreeAI.SIMPLE_VERSION = 1
function TreeAI:_OfferSimpleMode(fresh)
  local s = self.settings
  if not s or (tonumber(s.simpleV) or 0) >= self.SIMPLE_VERSION then return end
  s.simpleV = self.SIMPLE_VERSION
  local prev = s.preset
  if prev == "simple" then return end
  self:ApplyPreset("simple")
  if fresh then return end
  local back = (prev and self.PRESETS[prev]) and prev or "normal"
  self:Suggest("core:simple", "Mode simple activé : 3 conseils utiles. Clique pour revenir à « "
    .. (self.PRESET_LABEL[back] or back) .. " ».", 2, { ttl = 180, sound = false, kind = "always",
      onClick = function() self:ApplyPreset(back); self:ClearSuggestion("core:simple") end })
end

-- encadré : 1 alerte max / alertGap s (20 s par défaut), un même sujet pas avant topicGap s,
-- en combat seulement le danger de mort (le reste attend la fin du combat, s'il est encore vrai)
local alertState = { last = -1e9, topics = {}, pending = nil, shown = 0, blocked = 0 }
TreeAI._alertState = alertState
function TreeAI:ResetAlertGate()
  alertState.last, alertState.topics, alertState.pending = -1e9, {}, nil
end

function TreeAI:AlertAllowed(e, opts)
  local n, t = noiseCfg(), now()
  local danger = self:IsDanger(e, opts)
  if self:InCombat() and not danger then
    alertState.pending, alertState.blocked = e.id, alertState.blocked + 1
    return false, "combat"
  end
  if t - alertState.last < (tonumber(n.alertGap) or 20) then
    alertState.blocked = alertState.blocked + 1
    return false, "gap"
  end
  local topic = self:TopicOf(e) or e.id
  local tl = topic and alertState.topics[topic]
  if tl and t - tl < (tonumber(n.topicGap) or 120) and not danger then
    alertState.blocked = alertState.blocked + 1
    return false, "topic"
  end
  alertState.last = t
  if topic then alertState.topics[topic] = t end
  alertState.shown = alertState.shown + 1
  return true
end

-- fin de combat : l'alerte importante retenue pendant le combat, si elle est toujours d'actualité
function TreeAI:_FlushPendingAlert()
  local id = alertState.pending
  alertState.pending = nil
  local e = id and TreeAI._suggestions[id]
  if not e or (e.expires and e.expires <= now()) or e.prio < 3 then return end
  if self:AlertAllowed(e) then
    -- same « once per meaning » rule as Suggest (QA: « Familier à 30 % » popped after every fight)
    local t = now()
    local sk = strgsub(tostring(e.text or ""), "%d+", "#")
    local pa = TreeAI._alerted and TreeAI._alerted[id]
    if pa and pa.sk == sk and (t - pa.at) < 300 then return end
    if TreeAI._alerted then TreeAI._alerted[id] = { sk = sk, at = t } end
    playUrgentSound()
    if self.ShowAlert then safecall(self.ShowAlert, self, e) end
  end
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
  -- module coupé en direct (options, /tai module X off) : ses minuteurs ne doivent plus rien afficher
  local owner = opts.module and self.modules[opts.module]
  if owner and owner._active == false then return nil end
  -- état du joueur : rappels de buff jetés (mort / vol / monture / repos), autres conseils mis en attente
  local kind = self:QuietKindOf({ kind = opts.kind, death = opts.death, module = opts.module, topic = opts.topic, id = id })
  local quiet = self:Quiet(kind)
  if quiet and kind == "buff" then self:ClearSuggestion(id); return nil end
  local tip
  text, tip = shortText(safeText(text), opts.tooltip)
  local t = now()
  local ttl = opts.ttl
  if ttl == nil then ttl = 60 end
  local e = suggestions[id] or {}
  local changed = (e.text ~= text) or (e.prio ~= prio)
  -- first appearance (or new prio): the order inside a prio follows it, so a module re-suggesting the
  -- same advice every refresh does not reshuffle « Maintenant » (counters in the text do not count)
  if not e.since or e.prio ~= prio then e.since = t end
  e.id, e.text, e.prio, e.time = id, text, prio, t
  e.expires = (ttl and ttl > 0) and (t + ttl) or nil
  e.module, e.icon, e.onClick, e.macro = opts.module, opts.icon, opts.onClick, opts.macro
  e.tab, e.tooltip = opts.tab, tip
  e.topic, e.danger = opts.topic, opts.danger
  e.kind = kind
  suggestions[id] = e
  if quiet then   -- en attente : ni son, ni encadré, ni journal ; réaffiché quand l'état revient
    self:RequestRefresh()
    return e
  end

  -- announce (sound/chat) at most once per cooldown for same id+text
  local cd = opts.cooldown or 30
  local a = announced[id]
  if not a or a.text ~= text or (t - a.at) >= cd then
    announced[id] = { text = text, at = t }
    if changed or not a then logSuggestion(e) end
    local minPrio = (self.settings and self.settings.minPrio) or 1
    if prio >= minPrio then
      -- important (prio 3) : encadré + son, passés au filtre anti-bruit (≤ 1 / 20 s, sujet, combat)
      local alertOK = prio >= 3 and self:AlertAllowed(e, opts)
      local doSound = opts.sound
      doSound = (alertOK and doSound ~= false)
        or (prio < 3 and doSound == true and self.settings and self.settings.soundTips)
      if doSound then playUrgentSound() end
      -- corner alert: POLISH filter (≤ 1 / 20 s, topic, combat) AND once per id, again only if the meaning
      -- changed (digits ignored) or after 5 min (QA)
      if alertOK and self.ShowAlert then
        local sk = strgsub(text, "%d+", "#")
        local pa = alerted[id]
        if not pa or pa.sk ~= sk or (t - pa.at) >= 300 then
          alerted[id] = { sk = sk, at = t }
          safecall(self.ShowAlert, self, e)
        end
      end
      -- important items go to the corner alert box; the centre error text only if that box is off
      local al = self.settings and self.settings.alert
      if alertOK and al and al.enabled == false and _G.UIErrorsFrame and _G.UIErrorsFrame.AddMessage then
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
  local qAdv, qBuf = self:Quiet("advice"), self:Quiet("buff")
  for id, e in pairs(suggestions) do
    if e.expires and e.expires <= t then
      suggestions[id] = nil
    elseif e.prio >= minPrio and not ((e.kind == nil or e.kind == "advice") and qAdv or (e.kind == "buff" and qBuf)) then
      list[#list + 1] = e
    end
  end
  tsort(list, function(a, b)
    if a.prio ~= b.prio then return a.prio > b.prio end
    local sa, sb = a.since or a.time, b.since or b.time
    if sa ~= sb then return sa > sb end
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
  for id, t in pairs(self.tabs) do
    if not t.virtual and not (self.IsTabOff and self:IsTabOff(id)) then list[#list + 1] = t end
  end
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
  local text, tip = shortText(safeText(tostring(l.text)), l.tooltip)
  return { text = text, prio = prio, icon = l.icon, kind = l.kind, death = l.death,
    onClick = type(l.onClick) == "function" and l.onClick or nil,
    tooltip = tip, pin = l.pin and true or nil, id = l.id, source = source, data = l.data }
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

-- Formats communs (tous les modules peuvent s'en servir) : 1 234 · 12 po 3 pa · 45 s / 12 min / 1 h 05 · 85 %
TreeAI.Fmt = TreeAI.Fmt or {}
local Fmt = TreeAI.Fmt
function Fmt.Num(n)
  n = tonumber(n) or 0
  local neg = n < 0
  local s = tostring(math.floor(math.abs(n) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  s = s:gsub("^ ", "")
  return (neg and "-" or "") .. s
end
function Fmt.Money(c)
  c = math.floor(tonumber(c) or 0)
  local neg = c < 0
  c = math.abs(c)
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  local t = {}
  if g > 0 then t[#t + 1] = Fmt.Num(g) .. " po" end
  if s > 0 then t[#t + 1] = s .. " pa" end
  if (k > 0 and g == 0) or #t == 0 then t[#t + 1] = k .. " pc" end
  return (neg and "-" or "") .. tconcat(t, " ")
end
function Fmt.Time(sec)
  sec = tonumber(sec)
  if not sec then return "?" end
  sec = math.max(0, math.floor(sec + 0.5))
  if sec < 60 then return sec .. " s" end
  if sec < 3600 then
    local m, s = math.floor(sec / 60), sec % 60
    if m < 10 and s > 0 then return strformat("%d min %02d", m, s) end
    return math.floor(sec / 60 + 0.5) .. " min"
  end
  if sec < 86400 then return strformat("%d h %02d", math.floor(sec / 3600), math.floor(sec / 60) % 60) end
  return strformat("%d j %d h", math.floor(sec / 86400), math.floor(sec / 3600) % 24)
end
function Fmt.Pct(x) return math.floor((tonumber(x) or 0) + 0.5) .. " %" end

-- bloc de lignes -> module propriétaire (coupé = bloc masqué tout de suite, sans /reload)
TreeAI.SOURCE_OWNER = TreeAI.SOURCE_OWNER or { missions = "Missions" }
TreeAI.MAIN_OWNER = TreeAI.MAIN_OWNER or { predict = "Predict", quests = "Tracker", hv = "Auction" }
TreeAI.TAB_OWNER = TreeAI.TAB_OWNER or { predict = "Predict" }
local function moduleOff(name)
  local m = name and TreeAI.modules[name]
  return (m and m._active == false) and true or false
end
local function sourceActive(tabId, src)
  if src == "main" then return not moduleOff(TreeAI.MAIN_OWNER[tabId]) end
  return not moduleOff(TreeAI.SOURCE_OWNER[src] or src)
end
TreeAI._SourceActive = sourceActive

function TreeAI:TabOf(e)
  if e.tab and self.tabs[e.tab] then return e.tab end
  return (e.module and self.MODULE_TAB[e.module]) or DEFAULT_TAB
end

function TreeAI:IsTabOff(id)
  local owner = self.TAB_OWNER and self.TAB_OWNER[id]
  local m = owner and self.modules[owner]
  return (m and m._active == false) and true or false
end

-- Entries shown in a tab. « Maintenant » = top N by prio of suggestions + pinned tab lines, deduped
-- by visible text AND by meaning (topic: ammo / bags / repair… from several modules -> one line),
-- at most `perModule` lines per module, prio >= nowMinPrio (pinned quest step exempt).
-- Other tabs = their SetTabLines blocks, then the suggestions routed to them.
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
          if sourceActive(t.id, src) then
            for _, l in ipairs(ts.lines[src] or {}) do
              if l.pin and not self:Quiet(self:QuietKindOf(l)) then cand[#cand + 1] = l end
            end
          end
        end
      end
    end
    for i, e in ipairs(cand) do e._rank = i end
    tsort(cand, function(a, b)
      if a.prio ~= b.prio then return a.prio > b.prio end
      return a._rank < b._rank
    end)
    local max = tonumber(self.settings and self.settings.panel and self.settings.panel.nowMax) or 5
    local nz = noiseCfg()
    local minP, perMod = tonumber(nz.nowMinPrio) or 1, tonumber(nz.perModule) or 0
    local onlyAct = nz.actionable and true or false
    local topics, perCount = {}, {}
    for _, e in ipairs(cand) do
      if #out >= max then break end
      local topic = self:TopicOf(e)
      local modName = e.module or e.source
      if topic and topics[topic] then
        local kept = topics[topic]
        if modName and modName ~= (kept.module or kept.source) then
          kept._also = kept._also or {}
          kept._also[modName] = true
        end
      elseif (e.prio >= minP or e.pin)
          and not (onlyAct and e.prio < 2 and not (e.onClick or e.macro))
          and not (perMod > 0 and modName and (perCount[modName] or 0) >= perMod) then
        e._also = nil
        if push(e) then
          if topic then topics[topic] = e end
          if modName then perCount[modName] = (perCount[modName] or 0) + 1 end
        end
      end
    end
    return out
  end
  local vt = self.tabs[id]
  if vt and vt.virtual and type(vt.from) == "table" then   -- vue d'un bloc d'un autre onglet (Talents ⊂ Infos)
    local fts = tabSources[vt.from.tab]
    local src = vt.from.source
    if fts and fts.lines[src] and sourceActive(vt.from.tab, src) then
      for _, l in ipairs(fts.lines[src]) do push(l) end
    end
    return out
  end
  local ts = tabSources[id]
  if ts then
    for _, src in ipairs(ts.order) do
      if sourceActive(id, src) then
        for _, l in ipairs(ts.lines[src] or {}) do push(l) end
      end
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
-- « Talents » : vue du bloc Talents d'« Infos », proposée dans le sous-menu « Plus » (pas un onglet de plus)
TreeAI.tabs.talents = { id = "talents", title = L["Talents"], order = 4.8, virtual = true,
  from = { tab = "info", source = "Talents" } }

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
local seq = 0
local partial = {}       -- sender..seq -> { n, parts = {} }
local CHUNK = 220
-- Envoi : le jeu tolère ~10 messages d'addon par préfixe en rafale puis 1/s ; au-delà, le message est
-- PERDU sans erreur Lua (code de retour « throttle »). D'où : seau de jetons (8, +1/s), code de retour
-- vérifié, nouvel essai avec délai croissant, deux files (état > quêtes), message d'un type pas encore
-- parti remplacé par le plus récent, pause en donjon/raid (les addons n'y parlent plus aux autres).
local BURST, REFILL = 8, 1
local LOW_PRIO = { SQQ = true, SQD = true, MSM = true, FQUEST = true }
local queues = { {}, {} }  -- [1] état / petits messages, [2] journaux de quêtes
local sending = false
local tokens, tokenAt = BURST, nil
local retryAt, backoff = 0, 1
local msgId, started = 0, {}
TreeAI.commPaused = nil  -- nil | "donjon" | "bloquée" (verrou du jeu)
TreeAI.commStats = { sent = 0, throttled = 0, dropped = 0, paused = 0 }

-- résultat de SendAddonMessage : true/0/nil = ok ; 3/8 (ou false) = limité -> réessayer ; 11 = verrou ; autre = abandon
local RES_THROTTLE, RES_LOCK = { [3] = true, [8] = true }, 11
local function classify(ok, res)
  if not ok then return "error" end
  if res == nil or res == true or res == 0 then return "ok" end
  if res == false or RES_THROTTLE[res] then return "throttle" end
  if res == RES_LOCK then return "lock" end
  return "error"
end
TreeAI._ClassifySend = classify

local function sendRaw(msg, channel, target)
  local C_ChatInfo = _G.C_ChatInfo
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then
    return pcall(C_ChatInfo.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  elseif _G.SendAddonMessage then
    return pcall(_G.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  end
  return false
end

local function instancePaused()
  local inInst, kind = try(_G.IsInInstance)
  return (inInst and (kind == "party" or kind == "raid" or kind == "pvp" or kind == "arena")) and true or false
end

local function clearQueues()
  local n = #queues[1] + #queues[2]
  queues[1], queues[2] = {}, {}
  started = {}
  return n
end

function TreeAI:_SetCommPaused(reason)
  if self.commPaused == reason then return end
  local was = self.commPaused
  self.commPaused = reason
  if reason then
    self.commStats.dropped = self.commStats.dropped + clearQueues()
    self.commStats.paused = self.commStats.paused + 1
    self:Print(("Synchro du groupe en pause (%s) : elle reprend à la sortie."):format(
      reason == "donjon" and "donjon" or "bloquée par le jeu"))
    self:Fire("COMM_PAUSED", reason)
  elseif was then
    self:Print("Synchro du groupe reprise.")
    self:Fire("COMM_RESUMED")
  end
end

function TreeAI:_CheckCommPause()
  if instancePaused() then self:_SetCommPaused("donjon")
  elseif self.commPaused then self:_SetCommPaused(nil) end
end

function TreeAI:CommStatus()
  return { paused = self.commPaused, queued = #queues[1] + #queues[2], tokens = math.floor(tokens),
           stats = self.commStats }
end

local function refill(t)
  tokenAt = tokenAt or t
  tokens = math.min(BURST, tokens + (t - tokenAt) * REFILL)
  tokenAt = t
end

local pump
local function schedule(delay)
  if sending then return end
  sending = true
  TreeAI:After(math.max(0.05, delay), function() sending = false; pump() end)
end

pump = function()
  local q = (#queues[1] > 0 and queues[1]) or (#queues[2] > 0 and queues[2]) or nil
  if not q then return end
  if TreeAI.commPaused then TreeAI.commStats.dropped = TreeAI.commStats.dropped + clearQueues(); return end
  local t = now()
  refill(t)
  if t < retryAt then return schedule(retryAt - t) end
  if tokens < 1 then return schedule((1 - tokens) / REFILL) end
  local item = q[1]
  local status = classify(sendRaw(item[1], item[2], item[3]))
  if status == "ok" then
    tremove(q, 1)
    tokens = tokens - 1
    started[item.mid] = true
    backoff = 1
    TreeAI.commStats.sent = TreeAI.commStats.sent + 1
  elseif status == "throttle" then
    TreeAI.commStats.throttled = TreeAI.commStats.throttled + 1
    item.tries = (item.tries or 0) + 1
    tokens = 0
    if item.tries > 6 then tremove(q, 1); TreeAI.commStats.dropped = TreeAI.commStats.dropped + 1 end
    retryAt = t + backoff
    backoff = math.min(backoff * 2, 8)
  elseif status == "lock" then
    TreeAI:_SetCommPaused(instancePaused() and "donjon" or "bloquée")
    return
  else
    tremove(q, 1)
    TreeAI.commStats.dropped = TreeAI.commStats.dropped + 1
  end
  if #queues[1] + #queues[2] > 0 then schedule(0.05) end
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
  if self.commPaused == nil and instancePaused() then self:_SetCommPaused("donjon") end
  if self.commPaused then return false end
  seq = (seq % 9999) + 1
  local q = queues[LOW_PRIO[msgType] and 2 or 1]
  -- même type, même destinataire, pas encore commencé : remplacé par le plus récent
  for i = #q, 1, -1 do
    local it = q[i]
    if it.type == msgType and it[2] == channel and it[3] == target and not started[it.mid] then tremove(q, i) end
  end
  msgId = msgId + 1
  local n = mmax(1, math.ceil(#data / CHUNK))
  for i = 1, n do
    local part = strsub(data, (i - 1) * CHUNK + 1, i * CHUNK)
    q[#q + 1] = { strformat("1~%s~%d~%d/%d~%s", msgType, seq, i, n, part), channel, target, type = msgType, mid = msgId }
  end
  pump()
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
    if now() - v.at > 30 then partial[k] = nil end
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

-- Aide groupée : commande -> rubrique (les commandes des modules sans rubrique vont dans « Autres »)
TreeAI.SLASH_GROUPS = TreeAI.SLASH_GROUPS or {
  { "Démarrer", { "aide", "bienvenue", "mode", "profil", "options" } },
  { "Panneau", { "afficher", "cacher", "basculer", "mini", "onglet", "edit", "placer", "verrou", "reset", "alerte" } },
  { "Quêtes", { "quetes", "suivi", "itineraire", "prevoir", "pro", "abandon", "surbrillance", "highlight" } },
  { "Boussole", { "boussole", "aller", "cible", "way" } },
  { "Groupe", { "groupe", "mission", "prends", "partir", "pret", "sync" } },
  { "Chasseur et combat", { "familier", "mort", "boost", "pvp" } },
  { "Macros et touches", { "macros", "binds", "touches", "raccourcis" } },
  { "Économie et HV", { "hv", "compos", "eco" } },
  { "Outils", { "modules", "module", "statut", "perf", "nouveau", "selftest", "diag", "debug", "test" } },
}

--- { { title=, items = { {cmd=, help=}, ... } }, ... } : toutes les commandes documentées, groupées.
function TreeAI:GetSlashHelp()
  local out, used = {}, {}
  for _, g in ipairs(self.SLASH_GROUPS) do
    local items = {}
    for _, c in ipairs(g[2]) do
      local e = slash[c]
      if e and e.help and not used[c] then used[c] = true; items[#items + 1] = { cmd = c, help = L[e.help] } end
    end
    if #items > 0 then out[#out + 1] = { title = L[g[1]], items = items } end
  end
  local other = {}
  for _, c in ipairs(slashOrder) do
    local e = slash[c]
    if e.help and not used[c] then other[#other + 1] = { cmd = c, help = L[e.help] } end
  end
  if #other > 0 then out[#out + 1] = { title = L["Autres"], items = other } end
  return out
end

function TreeAI:PrintHelp()
  self:Print(L["Commandes"] .. " (/tai, /treeai) :")
  for _, g in ipairs(self:GetSlashHelp()) do
    self:Print("|cff33cc66" .. g.title .. "|r")
    for _, it in ipairs(g.items) do self:Print("  |cffffff00/tai " .. it.cmd .. "|r — " .. it.help) end
  end
end

function TreeAI:HandleSlash(input)
  input = tostring(input or "")
  local cmd, rest = input:match("^%s*(%S*)%s*(.-)%s*$")
  cmd = (cmd or ""):lower()
  if cmd == "" or cmd == "aide" or cmd == "help" or cmd == "?" then
    if rest == "chat" or not (self.ShowHelp and self:ShowHelp()) then self:PrintHelp() end
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

TreeAI:RegisterSlash("aide", function() end, "cette aide (/tai aide chat : dans le chat)")
TreeAI:RegisterSlash("mode", function(rest)
  rest = tostring(rest or ""):lower()
  if rest == "" then
    local cur = TreeAI.settings.preset or "normal"
    TreeAI:Print(L["Mode"] .. " : " .. L[TreeAI.PRESET_LABEL[cur] or cur] .. "  (/tai mode simple | calme | normal | tout)")
    return
  end
  if rest == "calm" then rest = "calme" elseif rest == "all" then rest = "tout" end
  if TreeAI:ApplyPreset(rest) then
    TreeAI:Print(L["Mode"] .. " : " .. L[TreeAI.PRESET_LABEL[rest]] .. " — " .. L[TreeAI.PRESET_HELP[rest]])
  else
    TreeAI:Print("/tai mode simple | calme | normal | tout")
  end
end, "simple | calme | normal | tout : combien de conseils tu veux voir")
TreeAI:RegisterSlash("profil", function(rest)
  rest = tostring(rest or ""):lower()
  local want = (rest == "perso" or rest == "char" or rest == "personnage") and "char"
    or ((rest == "compte" or rest == "account") and "account") or nil
  if want then TreeAI:SetProfile(want) end
  TreeAI:Print(L["Réglages"] .. " : " .. (TreeAI:ProfileMode() == "char" and L["propres à ce personnage"]
    or L["communs au compte"]) .. "  (/tai profil perso | compte)")
end, "perso | compte : réglages propres à ce perso ou communs")
TreeAI:RegisterSlash("bienvenue", function() if TreeAI.ShowWizard then TreeAI:ShowWizard(1) end end,
  "relancer l'assistant de démarrage")
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
  -- rebind char data now that the name is known (profil du perso éventuel compris)
  bindSettings()
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
  safecall(self._OfferSimpleMode, self, self._freshDB)
  if self.CreatePanel then safecall(self.CreatePanel, self) end
  for _, n in ipairs(self.moduleOrder) do moduleEnable(self.modules[n]) end
  self:Fire("READY")
  self:StartAutoRefresh()
  -- premier lancement : petit assistant en 3 étapes (hors combat)
  if self.db and not self.db.onboarded and self.ShowWizard then
    self:After(4, function() self:RunOutOfCombat("wizard", function() self:ShowWizard(1) end) end)
  end
  self:After(5, function() self:SendHello() end)
end

function TreeAI:PLAYER_LOGOUT()
  if self.db then safecall(self.UpdateSummary, self) end
end

function TreeAI:PLAYER_REGEN_DISABLED()
  -- (3.9.2) combat starts → leave the edit mode at once (overlays must never stay in combat)
  if self.IsEditMode and self:IsEditMode() and self.SetEditMode then safecall(self.SetEditMode, self, false) end
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
  self:After(2, function() if not self:InCombat() then self:_FlushPendingAlert() end end)
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
for _, ev in ipairs(TreeAI.STATE_EVENTS) do TreeAI:RegisterEvent(ev) end
TreeAI:RegisterEvent("PLAYER_LOGOUT")
TreeAI:RegisterEvent("PLAYER_REGEN_DISABLED")
TreeAI:RegisterEvent("PLAYER_REGEN_ENABLED")
TreeAI:RegisterEvent("CHAT_MSG_ADDON")
do  -- pause / reprise des comms selon l'instance (propriétaire distinct : n'écrase pas les autres handlers)
  local commOwner = {}
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA" }) do
    TreeAI:RegisterEvent(ev, commOwner, function() TreeAI:_CheckCommPause() end)
  end
end
TreeAI:RegisterEvent("GROUP_ROSTER_UPDATE")
TreeAI:RegisterEvent("PARTY_MEMBERS_CHANGED", TreeAI, "GROUP_ROSTER_UPDATE")
for _, ev in ipairs({ "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA", "PLAYER_MONEY", "PLAYER_GUILD_UPDATE" }) do
  TreeAI:RegisterEvent(ev, TreeAI, function(self) self:_QueueSummary() end)
end
