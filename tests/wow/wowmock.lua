-- wowmock.lua — a small World of Warcraft (Classic-family / WoW Forever) API mock for Lua 5.1.
-- Loaded by tests/wow/harness.lua BEFORE the addon files. Everything lives in the global table
-- `WowMock` (test-side only; the harness whitelists it). Two API flavours:
--   WowMock.Install("modern")  C_Container, C_ChatInfo, C_AddOns, C_Spell, C_UnitAuras…
--   WowMock.Install("legacy")  GetContainerItemInfo & co, global SendAddonMessage, no C_Container
--   WowMock.Install("forever") "modern" + what WoW Forever 1.60.1 really changes (checked against
--     Blizzard_APIDocumentationGenerated, Gethe/wow-ui-source branch « forever », build 70862):
--     Classic-era globals removed (GetPetHappiness → C_PetInfo, GetSkillLineInfo → C_SkillInfo, spellbook
--     → C_SpellBook, GetQuestLogTitle, UnitAura, GetSpellInfo…), secret values in combat (issecretvalue;
--     arithmetic / comparison / table key on them raise), COMBAT_LOG_EVENT_UNFILTERED registration =
--     ADDON_ACTION_FORBIDDEN, CreateMacro/EditMacro/DeleteMacro only inside a hardware event (click / slash).
-- so the addon's feature tests (`if C_Container then … else … end`) are exercised both ways.
-- Unknown methods on mocked widgets are no-ops (recorded in WowMock.unmockedMethods).
-- ToS guards recorded as violations: CreateMacro/EditMacro/DeleteMacro/PickupMacro/SetBinding*
-- in combat, macro bodies > 255 chars, addon messages > 255 bytes / prefix > 16 chars.

local M = {}
WowMock = M

local unpack = unpack or table.unpack

----------------------------------------------------------------------------------------------
-- state
----------------------------------------------------------------------------------------------
function M.Reset()
  M.now = 1000.0
  M.serverTime = 1793000000
  M.inCombat = false
  M.errors = {}          -- Lua errors raised by addon handlers
  M.violations = {}      -- ToS / API-contract violations
  M.output = {}          -- print() / chat frame lines
  M.sentAddon = {}       -- { prefix, msg, channel, target }
  M.sentChat = {}
  M.prefixes = {}
  M.frames = {}
  M.timers = {}
  M.unmockedMethods = {}
  M.namedFrames = {}
  M.sounds = {}
  M.macros = { global = {}, char = {} }
  M.bindings = {}
  M.actions = {}         -- [slot] = { type = "spell"|"macro"|"item", id =, text = } (action bars)
  M.cursor = nil         -- { type, id } after PickupMacro / PickupSpell
  M.forbidBindings = false -- true: SetBinding/SaveBindings raise ADDON_ACTION_FORBIDDEN (WoW Forever test)
  M.combatEvent = nil
  M.player = {
    name = "Testeur", realm = "Royaume-Test", class = "HUNTER", classLocal = "Chasseur",
    race = "Nain", raceFile = "Dwarf", level = 26, health = 900, healthMax = 1000,
    power = 400, powerMax = 600, powerType = 0, xp = 12000, xpMax = 26000, rested = 5000,
    money = 123456, zone = "Contreforts de Hautebrande", subzone = "Moulin-de-Tarren",
    guid = "Player-0000-00000001", resting = false, dead = false,
  }
  M.pet = {
    exists = true, name = "Crocdur", family = "Loup", health = 500, healthMax = 600,
    happiness = 2, loyalty = "Loyauté (3)", tp = 5, level = 25, dead = false,
  }
  M.target = { exists = false }
  M.units = {}           -- other unit tokens: nameplateN, mouseover… = { name=, guid=, classification=, hostile= }
  M.threat = nil         -- { tanking=, status=, pct= } for UnitDetailedThreatSituation
  M.questChoices = {}    -- item ids offered as quest reward choices (QUEST_COMPLETE)
  M.group = {}           -- list of { name=, class=, level=, online= }
  M.auras = { player = {}, pet = {}, target = {} }
  M.spells = {}          -- [id] = { name=, rank=, icon=, castTime=, known= }
  M.spellCooldowns = {}
  M.inventory = {}       -- [slot] = { id=, count=, dur=, durMax= }
  M.quests = {}          -- { title=, level=, complete=, objectives={ {text=, done=} } }
  M.items = {}
  M.bags = {}
  M.merchantOpen = false
  M.ah = { open = false, list = {}, owned = {}, bids = {}, cancels = {}, queries = {}, mail = {} }
  M.hardware, M.hwCalls = false, 0
  M.talentGroup, M.numTalentGroups = 1, 2
  M.locale = "frFR"
  M.ammoSlot = 0
  M.cvars = {}
  M.pos = { mapID = 1424, x = 0.5, y = 0.4 }  -- player map position (C_Map), nil = unknown
  M.facing = 0                                 -- GetPlayerFacing radians (0 = north, counter-clockwise)
  M.instance = false                           -- IsInInstance(): positions hidden when true
  M.speed = 0                                  -- GetUnitSpeed yards/s
  M.mapRects = { [1424] = { top = 1000, left = 2000, w = 3000, h = 2000, continent = 0 },
                 [1416] = { top = 3000, left = 2500, w = 2000, h = 1500, continent = 0 } }
  M.mapNames = { [1424] = "Contreforts de Hautebrande", [1416] = "Montagnes d'Alterac" }
  M.waypoints = {}                             -- [questID] = { mapID, x, y } (C_QuestLog.GetNextWaypoint, modern)
  M.superTrack = nil
  M.loggingCombat, M.loggingChat = false, false
  M.SeedDefaults()
end

local function record(list, kind, msg)
  local where, lvl = "?", 3
  while true do
    local info = debug.getinfo(lvl, "Sl")
    if not info then break end
    local src = tostring(info.short_src)
    if info.what ~= "C" and not src:find("wowmock%.lua$") and src ~= "(tail call)" then
      where = src .. ":" .. tostring(info.currentline)
      break
    end
    lvl = lvl + 1
  end
  list[#list + 1] = { kind = kind, msg = msg, where = where }
end
function M.Violation(kind, msg) record(M.violations, kind, msg) end

function M.SafeCall(label, fn, ...)
  local args, n = { ... }, select("#", ...)
  local ok, err = xpcall(function() return fn(unpack(args, 1, n)) end, function(e)
    return tostring(e) .. "\n" .. debug.traceback("", 2)
  end)
  if not ok then M.errors[#M.errors + 1] = { label = label, msg = err } end
  return ok
end

----------------------------------------------------------------------------------------------
-- data seeds (fake but plausible; FR client names)
----------------------------------------------------------------------------------------------
function M.AddItem(id, t)
  t.id = id
  t.quality = t.quality or 1
  t.stack = t.stack or 1
  t.ilvl = t.ilvl or 1
  t.minLevel = t.minLevel or 0
  t.sellPrice = t.sellPrice or 0
  t.type = t.type or "Divers"
  t.subType = t.subType or "Divers"
  t.equipLoc = t.equipLoc or ""
  t.classID = t.classID or 15
  t.subclassID = t.subclassID or 0
  M.items[id] = t
  return t
end

function M.ItemLink(id)
  local it = M.items[id]
  if not it then return nil end
  local colors = { [0] = "ff9d9d9d", "ffffffff", "ff1eff00", "ff0070dd", "ffa335ee", "ffff8000" }
  return "|c" .. (colors[it.quality] or "ffffffff") .. "|Hitem:" .. id .. "::::::::" .. M.player.level
    .. ":::::|h[" .. it.name .. "]|h|r"
end

function M.SetBagItem(bag, slot, id, count)
  M.bags[bag] = M.bags[bag] or { size = 16, slots = {} }
  M.bags[bag].slots[slot] = id and { id = id, count = count or 1 } or nil
end

function M.SeedDefaults()
  M.AddItem(4540, { name = "Miche de pain dur", stack = 20, sellPrice = 1, type = "Consommable",
    subType = "Nourriture et boissons", classID = 0, subclassID = 5, minLevel = 1 })
  M.AddItem(1179, { name = "Thé glacé", stack = 20, sellPrice = 6, type = "Consommable",
    subType = "Nourriture et boissons", classID = 0, subclassID = 5, minLevel = 15 })
  M.AddItem(929, { name = "Potion de soins", stack = 5, sellPrice = 25, type = "Consommable",
    subType = "Potion", classID = 0, subclassID = 1, minLevel = 12 })
  M.AddItem(3530, { name = "Bandage en laine", stack = 20, sellPrice = 10, type = "Consommable",
    subType = "Bandage", classID = 0, subclassID = 7 })
  M.AddItem(2515, { name = "Flèche acérée", stack = 200, sellPrice = 0, type = "Projectile",
    subType = "Flèche", classID = 6, subclassID = 2, minLevel = 10 })
  M.AddItem(2519, { name = "Balle lourde", stack = 200, sellPrice = 0, type = "Projectile",
    subType = "Balle", classID = 6, subclassID = 3, minLevel = 10 })
  M.AddItem(2672, { name = "Viande de loup filandreuse", stack = 10, sellPrice = 1,
    type = "Artisanat", subType = "Viande", classID = 7, subclassID = 8 })
  M.AddItem(2287, { name = "Viande séchée de haut-orme", stack = 20, sellPrice = 4,
    type = "Consommable", subType = "Nourriture et boissons", classID = 0, subclassID = 5 })
  M.AddItem(4865, { name = "Griffe cassée", quality = 0, stack = 5, sellPrice = 7,
    type = "Camelote", subType = "Camelote", classID = 15 })
  M.AddItem(5512, { name = "Pierre de soins", stack = 1, type = "Consommable", classID = 0 })
  M.AddItem(6948, { name = "Pierre de foyer", stack = 1, type = "Divers", classID = 15 })

  M.bags = {}
  for b = 0, 4 do M.bags[b] = { size = (b == 0) and 16 or 10, slots = {} } end
  M.SetBagItem(0, 1, 6948, 1)
  M.SetBagItem(0, 2, 4540, 12)
  M.SetBagItem(0, 3, 1179, 8)
  M.SetBagItem(0, 4, 929, 3)
  M.SetBagItem(0, 5, 3530, 6)
  M.SetBagItem(0, 6, 2672, 4)
  M.SetBagItem(0, 7, 4865, 2)
  M.SetBagItem(1, 1, 2515, 200)
  M.SetBagItem(1, 2, 2515, 140)

  local spells = {
    [75] = "Tir automatique", [1978] = "Morsure de serpent", [3044] = "Tir des arcanes",
    [1130] = "Marque du chasseur", [13165] = "Aspect du faucon", [5118] = "Aspect du guépard",
    [13163] = "Aspect du singe", [2973] = "Attaque du raptor", [781] = "Désengagement",
    [1499] = "Piège givrant", [13795] = "Piège d'immolation", [136] = "Guérison du familier",
    [6991] = "Nourrir le familier", [883] = "Appel du familier", [2641] = "Renvoyer le familier",
    [982] = "Ressusciter le familier", [5384] = "Feindre la mort", [2643] = "Flèches multiples",
    [5116] = "Trait de choc", [6603] = "Attaque", [1515] = "Dressage des bêtes",
    [8613] = "Dépeçage", [2550] = "Cuisine", [3273] = "Premiers soins", [19883] = "Pistage des humanoïdes",
  }
  M.spells = {}
  for id, name in pairs(spells) do M.spells[id] = { name = name, rank = "", icon = 132000 + id,
    castTime = 0, known = true } end

  M.inventory = {
    [1] = { id = 0, dur = 40, durMax = 50 }, [5] = { id = 0, dur = 10, durMax = 80 },
    [16] = { id = 0, dur = 30, durMax = 55 }, [18] = { id = 0, dur = 50, durMax = 60 },
    [0] = { id = 2515, count = 340 },
  }
  M.quests = {
    { title = "Le fermier Ray", level = 25, complete = false,
      objectives = { { text = "Ours enragé tué : 4/10", done = false } } },
    { title = "Le courrier de Southshore", level = 22, complete = true, objectives = {} },
  }
end

----------------------------------------------------------------------------------------------
-- widgets
----------------------------------------------------------------------------------------------
local Widget = {}
local WidgetMT = {
  __index = function(self, k)
    local v = Widget[k]
    if v ~= nil then return v end
    if type(k) == "string" and k:match("^[A-Z]") then
      M.unmockedMethods[k] = true
      return function() return nil end
    end
    return nil
  end,
}

local function newWidget(kind, name, parent)
  local w = setmetatable({
    __kind = kind, __name = name, __parent = parent, __scripts = {}, __events = {},
    __shown = true, __w = 100, __h = 20, __text = nil, __value = 0, __min = 0, __max = 1,
    __alpha = 1, __attrs = {}, __points = {}, __checked = false, __children = {},
  }, WidgetMT)
  return w
end
M.NewWidget = newWidget

function Widget:GetObjectType() return self.__kind end
function Widget:IsObjectType(t) return self.__kind == t end
function Widget:GetName() return self.__name end
function Widget:GetParent() return self.__parent end
function Widget:SetParent(p) self.__parent = p end
function Widget:SetScript(what, fn) self.__scripts[what] = fn end
function Widget:GetScript(what) return self.__scripts[what] end
function Widget:HasScript(what) return true end
function Widget:HookScript(what, fn)
  local old = self.__scripts[what]
  self.__scripts[what] = function(...) if old then old(...) end return fn(...) end
end
function Widget:RegisterEvent(ev)
  if type(ev) ~= "string" then error("RegisterEvent: event must be a string", 2) end
  if M.flavor == "forever" and (ev == "COMBAT_LOG_EVENT_UNFILTERED" or ev == "COMBAT_LOG_EVENT") then
    M.Violation("forbidden", "RegisterEvent(" .. ev .. ") on Forever (ADDON_ACTION_FORBIDDEN)")
    return
  end
  self.__events[ev] = true
end
Widget.RegisterUnitEvent = function(self, ev) Widget.RegisterEvent(self, ev) end
function Widget:UnregisterEvent(ev) self.__events[ev] = nil end
function Widget:UnregisterAllEvents() self.__events = {} end
function Widget:RegisterAllEvents() self.__allEvents = true end
function Widget:IsEventRegistered(ev) return self.__events[ev] and true or false end
function Widget:Show()
  local was = self.__shown
  self.__shown = true
  if not was and self.__scripts.OnShow then M.SafeCall("OnShow", self.__scripts.OnShow, self) end
end
function Widget:Hide()
  local was = self.__shown
  self.__shown = false
  if was and self.__scripts.OnHide then M.SafeCall("OnHide", self.__scripts.OnHide, self) end
end
function Widget:SetShown(v) if v then self:Show() else self:Hide() end end
function Widget:IsShown() return self.__shown end
function Widget:IsVisible()
  local w = self
  while w do
    if not w.__shown then return false end
    w = w.__parent
  end
  return true
end
function Widget:SetSize(w, h) self.__w, self.__h = w or self.__w, h or self.__h end
function Widget:SetWidth(w) self.__w = w end
function Widget:SetHeight(h) self.__h = h end
function Widget:GetWidth() return self.__w end
function Widget:GetHeight() return self.__h end
function Widget:GetSize() return self.__w, self.__h end
function Widget:SetPoint(...) self.__points[#self.__points + 1] = { ... } end
function Widget:ClearAllPoints() self.__points = {} end
function Widget:GetPoint(i)
  local p = self.__points[i or 1]
  if p then return unpack(p) end
  return "CENTER", UIParent, "CENTER", 0, 0
end
function Widget:GetNumPoints() return #self.__points end
function Widget:SetClampedToScreen(v) self.__clamped = v and true or false end
function Widget:IsClampedToScreen() return self.__clamped or false end
function Widget:SetAllPoints() end
function Widget:GetCenter() return 500, 400 end
function Widget:GetLeft() return 450 end
function Widget:GetRight() return 550 end
function Widget:GetTop() return 410 end
function Widget:GetBottom() return 390 end
function Widget:GetEffectiveScale() return 1 end
function Widget:GetScale() return self.__scale or 1 end
function Widget:SetScale(s) self.__scale = s end
function Widget:SetAlpha(a) self.__alpha = a end
function Widget:GetAlpha() return self.__alpha end
function Widget:SetText(t) self.__text = t end
function Widget:GetText() return self.__text end
function Widget:SetFormattedText(fmt, ...) self.__text = string.format(fmt, ...) end
function Widget:GetStringWidth() return #(tostring(self.__text or "")) * 6 end
function Widget:GetStringHeight() return 12 end
function Widget:SetFont() return true end
function Widget:GetFont() return "Fonts\\FRIZQT__.TTF", 12, "" end
function Widget:SetMinMaxValues(a, b) self.__min, self.__max = a, b end
function Widget:GetMinMaxValues() return self.__min, self.__max end
function Widget:SetValue(v) self.__value = v end
function Widget:GetValue() return self.__value end
function Widget:SetChecked(v) self.__checked = v and true or false end
function Widget:GetChecked() return self.__checked end
function Widget:SetAttribute(k, v)
  if M.inCombat and self.__protected then
    M.Violation("protected", "SetAttribute on a secure frame in combat")
  end
  self.__attrs[k] = v
end
function Widget:GetAttribute(k) return self.__attrs[k] end
function Widget:IsProtected() return self.__protected or false end
function Widget:GetChildren() return unpack(self.__children) end
function Widget:GetNumChildren() return #self.__children end
function Widget:GetFrameLevel() return self.__level or 1 end
function Widget:SetFrameLevel(l) self.__level = l end
function Widget:SetFrameStrata(s) self.__strata = s end
function Widget:GetFrameStrata() return self.__strata or "MEDIUM" end
function Widget:GetID() return self.__id or 0 end
function Widget:SetID(i) self.__id = i end
function Widget:IsMouseOver() return false end
function Widget:IsEnabled() return self.__enabled ~= false end
function Widget:Enable() self.__enabled = true end
function Widget:Disable() self.__enabled = false end
function Widget:SetEnabled(v) self.__enabled = v and true or false end
function Widget:Click(button)
  local fn = self.__scripts.OnClick
  if fn then
    M.hardware, M.hwCalls = true, 0  -- a click = one hardware event
    M.SafeCall("OnClick", fn, self, button or "LeftButton", true)
    M.hardware = false
  end
end
-- mouse wheel / scroll frames (panel tabs)
function Widget:EnableMouseWheel(v) self.__wheel = v and true or false end
function Widget:IsMouseWheelEnabled() return self.__wheel or false end
function Widget:SetScrollChild(c) self.__scrollChild = c end
function Widget:GetScrollChild() return self.__scrollChild end
function Widget:SetVerticalScroll(v) self.__vscroll = v end
function Widget:GetVerticalScroll() return self.__vscroll or 0 end
function Widget:GetVerticalScrollRange() return 0 end
function M.Wheel(frame, delta)  -- a mouse-wheel turn over frame
  local fn = frame and frame.__scripts.OnMouseWheel
  if fn then M.SafeCall("OnMouseWheel", fn, frame, delta) end
end
function M.Script(frame, what, ...)  -- run any script handler (OnEnter, OnLeave, OnHide…)
  local fn = frame and frame.__scripts[what]
  if fn then return M.SafeCall(what, fn, frame, ...) end
end
function Widget:GetTexture() return self.__texture end
function Widget:SetTexture(t) self.__texture = t end
function Widget:GetNumLines() return 0 end
function Widget:GetCursorPosition() return 0 end
function Widget:GetNumber() return tonumber(self.__text) or 0 end

local function makeRegion(parent, kind)
  local r = newWidget(kind, nil, parent)
  parent.__children[#parent.__children + 1] = r
  return r
end
function Widget:CreateFontString() return makeRegion(self, "FontString") end
function Widget:CreateTexture() return makeRegion(self, "Texture") end
function Widget:CreateLine() return makeRegion(self, "Line") end
function Widget:CreateMaskTexture() return makeRegion(self, "MaskTexture") end
function Widget:CreateAnimationGroup()
  local g = makeRegion(self, "AnimationGroup")
  function g:CreateAnimation() return makeRegion(g, "Animation") end
  function g:IsPlaying() return false end
  return g
end

function CreateFrame(kind, name, parent, template, id)
  if type(kind) ~= "string" then error("CreateFrame: frame type expected", 2) end
  local f = newWidget(kind, name, parent or UIParent)
  f.__template = template
  f.__protected = template and tostring(template):find("Secure") and true or false
  if f.__protected and M.inCombat then M.Violation("protected", "CreateFrame(secure) in combat") end
  M.frames[#M.frames + 1] = f
  if parent and parent.__children then parent.__children[#parent.__children + 1] = f end
  if kind == "GameTooltip" then
    function f:SetOwner() end
    f.__lines = {}
    function f:AddLine(t) self.__lines[#self.__lines + 1] = t end
    function f:GetItem() if self.__item then return M.items[self.__item].name, M.ItemLink(self.__item) end end
    function f:GetUnit() return nil end
    function f:AddDoubleLine() end
    function f:SetHyperlink() end
    function f:ClearLines() end
  end
  if type(name) == "string" and name ~= "" then
    name = name:gsub("%$parent", parent and parent.__name or "")
    f.__name = name
    M.namedFrames[name] = true
    rawset(_G, name, f)
  end
  if f.__scripts.OnLoad then M.SafeCall("OnLoad", f.__scripts.OnLoad, f) end
  return f
end

----------------------------------------------------------------------------------------------
-- events, time, timers
----------------------------------------------------------------------------------------------
function M.FireEvent(event, ...)
  local n = 0
  for i = 1, #M.frames do
    local f = M.frames[i]
    if (f.__events[event] or f.__allEvents) and f.__scripts.OnEvent then
      n = n + 1
      M.SafeCall(event, f.__scripts.OnEvent, f, event, ...)
    end
  end
  return n
end

function M.IsEventRegistered(event)
  for i = 1, #M.frames do if M.frames[i].__events[event] then return true end end
  return false
end

function M.Advance(seconds, step)
  step = step or 0.1
  local t = 0
  while t < seconds - 1e-9 do
    local dt = math.min(step, seconds - t)
    t = t + dt
    M.now = M.now + dt
    M.serverTime = M.serverTime + dt
    -- timers
    local due = {}
    for i = #M.timers, 1, -1 do
      local tm = M.timers[i]
      if tm.cancelled then
        table.remove(M.timers, i)
      elseif tm.at <= M.now then
        due[#due + 1] = tm
        if tm.interval and (not tm.iterations or tm.iterations > 1) then
          tm.at = tm.at + tm.interval
          if tm.iterations then tm.iterations = tm.iterations - 1 end
        else
          table.remove(M.timers, i)
          tm.done = true
        end
      end
    end
    for i = #due, 1, -1 do M.SafeCall("C_Timer", due[i].fn, due[i].handle) end
    -- OnUpdate of visible frames
    for i = 1, #M.frames do
      local f = M.frames[i]
      if f.__scripts.OnUpdate and f:IsVisible() then M.SafeCall("OnUpdate", f.__scripts.OnUpdate, f, dt) end
    end
  end
end

function GetTime() return M.now end
function GetServerTime() return math.floor(M.serverTime) end
function GetTimePreciseSec() return M.now end
function debugprofilestop() return M.now * 1000 end

local function newTimer(delay, fn, interval, iterations)
  if type(fn) ~= "function" then error("C_Timer: callback must be a function", 3) end
  local tm = { at = M.now + (delay or 0), fn = fn, interval = interval, iterations = iterations }
  local handle = {}
  function handle:Cancel() tm.cancelled = true end
  function handle:IsCancelled() return tm.cancelled or false end
  tm.handle = handle
  M.timers[#M.timers + 1] = tm
  return handle
end

----------------------------------------------------------------------------------------------
-- install the global API
----------------------------------------------------------------------------------------------
local function auraTuple(a)
  return a.name, a.icon or 136000, a.count or 0, a.debuffType, a.duration or 0,
    a.expirationTime or 0, a.source or "player", false, false, a.spellId or 0
end

local function bagSlot(bag, slot)
  local b = M.bags[bag]
  return b and b.slots[slot]
end

local function itemFromArg(item)
  if type(item) == "number" then return M.items[item] end
  if type(item) == "string" then
    local id = tonumber(item:match("item:(%d+)"))
    if id then return M.items[id] end
    for _, it in pairs(M.items) do if it.name == item then return it end end
  end
  return nil
end

local function bit_op(a, b, f)
  local r, p = 0, 1
  a, b = math.floor(a or 0) % 4294967296, math.floor(b or 0) % 4294967296
  for _ = 1, 32 do
    local x, y = a % 2, b % 2
    if f(x, y) then r = r + p end
    a, b, p = (a - x) / 2, (b - y) / 2, p * 2
  end
  return r
end

-- quest ids: q.id when the test sets one (stable across removals), else 1000 + log index
local function qid(i) local q = M.quests[i] return q and (q.id or 1000 + i) end
local function qAt(id)
  id = tonumber(id)
  if not id then return nil end
  for i, q in ipairs(M.quests) do if not q.header and (q.id or 1000 + i) == id then return q, i end end
  return nil
end

function M.Install(flavor)
  M.flavor = flavor or "modern"
  local modern = (M.flavor == "modern" or M.flavor == "forever")
  local G = _G

  -- Lua helpers WoW adds -----------------------------------------------------------------------
  G.strsplit = function(sep, s, limit)
    s = tostring(s or "")
    local out, start, n = {}, 1, 0
    while true do
      if limit and n >= limit - 1 then break end
      local i, j = s:find(sep, start, true)
      if not i then break end
      out[#out + 1] = s:sub(start, i - 1)
      n = n + 1
      start = j + 1
    end
    out[#out + 1] = s:sub(start)
    return unpack(out)
  end
  G.strjoin = function(sep, ...) return table.concat({ ... }, sep) end
  G.strtrim = function(s, chars)
    chars = chars and ("[" .. chars:gsub("[%]%^%-]", "%%%0") .. "]") or "%s"
    return (tostring(s):gsub("^" .. chars .. "+", ""):gsub(chars .. "+$", ""))
  end
  G.strconcat = function(...) return table.concat({ ... }) end
  G.strmatch, G.strfind, G.strsub, G.strlen = string.match, string.find, string.sub, string.len
  G.strupper, G.strlower, G.strrep, G.strbyte, G.strchar = string.upper, string.lower, string.rep, string.byte, string.char
  G.strrev, G.gsub, G.format = string.reverse, string.gsub, string.format
  G.strlenutf8 = function(s) local _, n = tostring(s):gsub("[^\128-\191]", "") return n end
  string.trim, string.split, string.join = G.strtrim, function(s, sep, ...) return G.strsplit(sep, s, ...) end, G.strjoin
  G.tinsert, G.tremove, G.sort, G.getn = table.insert, table.remove, table.sort, table.getn
  G.wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
  table.wipe = G.wipe
  G.tContains = function(t, v) for _, x in pairs(t) do if x == v then return true end end return false end
  G.tInvert = function(t) local r = {} for k, v in pairs(t) do r[v] = k end return r end
  G.CopyTable = function(t, seen)
    if type(t) ~= "table" then return t end
    local r = {}
    for k, v in pairs(t) do r[k] = (type(v) == "table") and G.CopyTable(v) or v end
    return r
  end
  G.Mixin = function(o, ...) for i = 1, select("#", ...) do for k, v in pairs((select(i, ...))) do o[k] = v end end return o end
  G.CreateFromMixins = function(...) return G.Mixin({}, ...) end
  G.floor, G.ceil, G.abs, G.min, G.max, G.sqrt = math.floor, math.ceil, math.abs, math.min, math.max, math.sqrt
  G.mod = math.fmod
  G.random = math.random
  G.fastrandom = math.random
  G.Round = function(x) return math.floor(x + 0.5) end
  G.Clamp = function(v, a, b) return math.max(a, math.min(b, v)) end
  G.bit = {
    band = function(a, b) return bit_op(a, b, function(x, y) return x == 1 and y == 1 end) end,
    bor = function(a, b) return bit_op(a, b, function(x, y) return x == 1 or y == 1 end) end,
    bxor = function(a, b) return bit_op(a, b, function(x, y) return x ~= y end) end,
    lshift = function(a, n) return (math.floor(a) * 2 ^ n) % 4294967296 end,
    rshift = function(a, n) return math.floor((math.floor(a) % 4294967296) / 2 ^ n) end,
  }
  G.hooksecurefunc = function(tbl, name, fn)
    if type(tbl) == "string" then tbl, name, fn = G, tbl, name end
    local old = tbl[name]
    if type(old) ~= "function" then error("hooksecurefunc: " .. tostring(name) .. " is not a function", 2) end
    rawset(tbl, name, function(...) local r = { old(...) } fn(...) return unpack(r) end)
  end
  G.issecurevariable = function() return true, nil end
  G.securecall = function(fn, ...) if type(fn) == "string" then fn = G[fn] end return fn(...) end
  G.geterrorhandler = function() return function(e) M.errors[#M.errors + 1] = { label = "errorhandler", msg = tostring(e) } end end
  G.seterrorhandler = function() end
  G.debugstack = function() return debug.traceback() end
  G.print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
    M.output[#M.output + 1] = table.concat(parts, " ")
  end

  -- constants / tables -----------------------------------------------------------------------
  G.WOW_PROJECT_MAINLINE, G.WOW_PROJECT_CLASSIC = 1, 2
  G.WOW_PROJECT_ID = 2
  G.NUM_BAG_SLOTS, G.NUM_BAG_FRAMES, G.BACKPACK_CONTAINER = 4, 4, 0
  G.INVSLOT_AMMO, G.INVSLOT_RANGED, G.INVSLOT_MAINHAND = 0, 18, 16
  G.MAX_PLAYER_LEVEL = 60
  G.LE_PARTY_CATEGORY_HOME = 1
  G.Enum = {
    ItemQuality = { Poor = 0, Common = 1, Uncommon = 2, Rare = 3, Epic = 4, Legendary = 5 },
    ItemClass = { Consumable = 0, Container = 1, Weapon = 2, Armor = 4, Reagent = 5, Projectile = 6,
      Tradegoods = 7, Recipe = 9, Quiver = 11, Questitem = 12, Miscellaneous = 15 },
    PowerType = { Mana = 0, Rage = 1, Focus = 2, Energy = 3 },
  }
  G.RAID_CLASS_COLORS = {}
  for _, c in ipairs({ "WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID" }) do
    G.RAID_CLASS_COLORS[c] = { r = 1, g = 1, b = 1, colorStr = "ffffffff",
      GenerateHexColor = function() return "ffffffff" end, WrapTextInColorCode = function(_, s) return s end }
  end
  G.LOCALIZED_CLASS_NAMES_MALE = { HUNTER = "Chasseur", WARRIOR = "Guerrier", MAGE = "Mage", ROGUE = "Voleur",
    PRIEST = "Prêtre", DRUID = "Druide", PALADIN = "Paladin", SHAMAN = "Chaman", WARLOCK = "Démoniste" }
  G.LOCALIZED_CLASS_NAMES_FEMALE = G.LOCALIZED_CLASS_NAMES_MALE
  G.ITEM_QUALITY_COLORS = {}
  for q = 0, 5 do G.ITEM_QUALITY_COLORS[q] = { r = 1, g = 1, b = 1, hex = "|cffffffff" } end
  G.SOUNDKIT = setmetatable({}, { __index = function(_, k) return 1 end })
  G.STANDARD_TEXT_FONT = "Fonts\\FRIZQT__.TTF"
  G.CreateColor = function(r, g, b, a)
    return { r = r, g = g, b = b, a = a, GetRGB = function(c) return c.r, c.g, c.b end,
      WrapTextInColorCode = function(_, s) return s end, GenerateHexColor = function() return "ffffffff" end }
  end
  G.BackdropTemplateMixin = {}
  G.StaticPopupDialogs = {}
  G.StaticPopup_Show = function(which) if not G.StaticPopupDialogs[which] then M.Violation("api", "StaticPopup_Show unknown " .. tostring(which)) end end
  G.StaticPopup_Hide = function() end
  G.ERR_NOT_IN_COMBAT = "Impossible en combat."

  -- frames -------------------------------------------------------------------------------------
  G.UIParent = newWidget("Frame", "UIParent", nil)
  G.WorldFrame = newWidget("Frame", "WorldFrame", nil)
  G.Minimap = newWidget("Minimap", "Minimap", G.UIParent)
  local chat = newWidget("ScrollingMessageFrame", "ChatFrame1", G.UIParent)
  function chat:AddMessage(msg) M.output[#M.output + 1] = tostring(msg) end
  G.DEFAULT_CHAT_FRAME, G.ChatFrame1, G.SELECTED_CHAT_FRAME = chat, chat, chat
  G.UIErrorsFrame = newWidget("MessageFrame", "UIErrorsFrame", G.UIParent)
  function G.UIErrorsFrame:AddMessage(msg) M.output[#M.output + 1] = "[UIError] " .. tostring(msg) end
  G.RaidWarningFrame = newWidget("MessageFrame", "RaidWarningFrame", G.UIParent)
  G.RaidNotice_AddMessage = function(_, msg) M.output[#M.output + 1] = "[RaidNotice] " .. tostring(msg) end
  G.GameTooltip = CreateFrame("GameTooltip", "GameTooltip", G.UIParent)
  G.MerchantFrame = newWidget("Frame", "MerchantFrame", G.UIParent)
  G.MerchantFrame.__shown = false
  G.MerchantFrame.IsShown = function() return M.merchantOpen end
  G.MerchantFrame.IsVisible = G.MerchantFrame.IsShown
  G.InterfaceOptionsFramePanelContainer = newWidget("Frame", "InterfaceOptionsFramePanelContainer", G.UIParent)
  G.InterfaceOptions_AddCategory = function(panel) if type(panel) ~= "table" then error("bad panel", 2) end end
  G.InterfaceOptionsFrame_OpenToCategory = function() end
  G.Settings = {
    RegisterCanvasLayoutCategory = function(frame, name) return { ID = name, GetID = function() return name end } end,
    RegisterVerticalLayoutCategory = function(name) return { ID = name, GetID = function() return name end } end,
    RegisterAddOnCategory = function() end,
    OpenToCategory = function() end,
  }
  for _, fnt in ipairs({ "GameFontNormal", "GameFontNormalSmall", "GameFontNormalLarge", "GameFontHighlight",
    "GameFontHighlightSmall", "GameFontDisable", "GameFontRed", "GameFontGreen", "NumberFontNormal" }) do
    G[fnt] = newWidget("Font", fnt, nil)
  end

  -- time ---------------------------------------------------------------------------------------
  G.C_Timer = {
    After = function(d, fn) newTimer(d, fn) end,
    NewTimer = function(d, fn) return newTimer(d, fn) end,
    NewTicker = function(d, fn, it) return newTimer(d, fn, d, it) end,
  }

  -- slash --------------------------------------------------------------------------------------
  G.SlashCmdList = G.SlashCmdList or {}
  G.hash_SlashCmdList = {}

  -- client / addon info --------------------------------------------------------------------------
  G.GetLocale = function() return M.locale end
  G.GetBuildInfo = function() return "1.15.8", "60000", "Nov 4 2026", 11508 end
  G.GetRealmName = function() return M.player.realm end
  G.GetNormalizedRealmName = function() return (M.player.realm:gsub("[%s%-]", "")) end
  local function addonMeta(name, field)
    local meta = M.tocMeta and M.tocMeta[name]
    return meta and meta[field] or nil
  end
  G.GetAddOnMetadata = addonMeta
  G.IsAddOnLoaded = function(name) return M.tocMeta and M.tocMeta[name] ~= nil end
  G.GetCVar = function(k) return M.cvars[k] end
  G.SetCVar = function(k, v) M.cvars[k] = tostring(v) end
  G.GetCVarBool = function(k) return M.cvars[k] == "1" end
  G.LoggingCombat = function(v) if v ~= nil then M.loggingCombat = v and true or false end return M.loggingCombat end
  G.LoggingChat = function(v) if v ~= nil then M.loggingChat = v and true or false end return M.loggingChat end
  G.IsShiftKeyDown = function() return M.shift and true or false end
  G.IsControlKeyDown = G.IsShiftKeyDown
  G.IsAltKeyDown = G.IsShiftKeyDown
  G.IsModifierKeyDown = G.IsShiftKeyDown
  G.PlaySound = function(id) M.sounds[#M.sounds + 1] = id return true end
  G.PlaySoundFile = function(f) M.sounds[#M.sounds + 1] = f return true end
  G.ReloadUI = function() M.reloaded = true end
  G.GetFramerate = function() return 60 end
  G.GetCursorPosition = function() return 0, 0 end
  G.GetScreenWidth = function() return 1920 end
  G.GetScreenHeight = function() return 1080 end
  G.GetCoinTextureString = function(c) return tostring(c) .. "c" end
  G.GetMoneyString = G.GetCoinTextureString
  G.date, G.time = os.date, os.time

  -- combat --------------------------------------------------------------------------------------
  G.InCombatLockdown = function() return M.inCombat end
  G.CombatLogGetCurrentEventInfo = function() if M.combatEvent then return unpack(M.combatEvent) end end

  -- player / units --------------------------------------------------------------------------------
  local function U(unit)
    unit = unit and tostring(unit):lower() or ""
    if unit == "player" then return M.player, "player" end
    if unit == "pet" then return M.pet.exists and M.pet or nil, "pet" end
    if unit == "target" then return M.target.exists and M.target or nil, "target" end
    local p = unit:match("^party(%d)$") or unit:match("^raid(%d+)$")
    if p then return M.group[tonumber(p)], "group" end
    if M.units[unit] then return M.units[unit], "other" end
    return nil
  end
  G.UnitExists = function(u) return U(u) ~= nil end
  G.UnitName = function(u) local d = U(u) if d then return d.name, nil end end
  G.UnitFullName = function(u) local d = U(u) if d then return d.name, M.player.realm end end
  G.UnitGUID = function(u) local d = U(u) if d then return d.guid or ("Creature-0-0-0-0-1-" .. tostring(d.name)) end end
  G.UnitClass = function(u)
    local d, k = U(u)
    if not d then return nil end
    if k == "pet" then return "Chasseur", "HUNTER", 3 end
    local cls = d.class or "WARRIOR"
    return G.LOCALIZED_CLASS_NAMES_MALE[cls] or cls, cls, 3
  end
  G.UnitRace = function(u) local d = U(u) if d then return d.race or "Humain", d.raceFile or "Human" end end
  G.UnitLevel = function(u) local d = U(u) if d then return d.level or 1 end return 0 end
  G.UnitEffectiveLevel = G.UnitLevel
  G.UnitHealth = function(u) local d = U(u) if d then return d.health or 100 end return 0 end
  G.UnitHealthMax = function(u) local d = U(u) if d then return d.healthMax or 100 end return 0 end
  G.UnitPower = function(u) local d = U(u) if d then return d.power or 100 end return 0 end
  G.UnitPowerMax = function(u) local d = U(u) if d then return d.powerMax or 100 end return 0 end
  G.UnitPowerType = function(u) local d = U(u) if d then return d.powerType or 0, "MANA" end return 0, "MANA" end
  G.UnitIsDead = function(u) local d = U(u) return d and d.dead or false end
  G.UnitIsDeadOrGhost = function(u) local d = U(u) return d and (d.dead or d.ghost) or false end
  G.UnitIsGhost = function(u) local d = U(u) return d and d.ghost or false end
  G.UnitOnTaxi = function(u) return (u == "player" and M.onTaxi) and true or false end
  G.UnitIsUnit = function(a, b) return tostring(a):lower() == tostring(b):lower() end
  G.UnitIsPlayer = function(u) local _, k = U(u) return k == "player" or k == "group" end
  G.UnitPlayerControlled = G.UnitIsPlayer
  G.UnitIsFriend = function(_, u) local _, k = U(u) return k ~= "target" end
  G.UnitIsEnemy = function(_, u) local _, k = U(u) return k == "target" end
  G.UnitCanAttack = function(_, u) local d, k = U(u) return (k == "target" or k == "other") and d and d.hostile or false end
  G.UnitClassification = function(u) local d = U(u) return d and d.classification or "normal" end
  G.UnitCreatureFamily = function(u) local d, k = U(u) if k == "pet" then return d.family end return d and d.family end
  G.UnitCreatureType = function(u) local d = U(u) return d and d.creatureType or "Bête" end
  G.UnitAffectingCombat = function(u) local _, k = U(u) if k == "player" or k == "pet" then return M.inCombat end return false end
  G.UnitIsConnected = function(u) local d = U(u) return d ~= nil and d.online ~= false end
  G.UnitIsAFK = function() return false end
  G.UnitIsTapDenied = function() return false end
  G.UnitThreatSituation = function(u) local d = U(u) return d and d.threat or nil end
  G.UnitDetailedThreatSituation = function()
    local t = M.threat
    if t then return t.tanking or false, t.status or 0, t.pct, t.pct, 0 end
  end
  G.GetNumQuestChoices = function() return #M.questChoices end
  G.GetQuestItemLink = function(kind, i) return kind == "choice" and M.questChoices[i] and M.ItemLink(M.questChoices[i]) or nil end
  G.GetQuestItemInfo = function(kind, i)
    local it = kind == "choice" and M.items[M.questChoices[i] or 0]
    if it then return it.name, 134400, 1, it.quality, true, it.id end
  end
  G.GetItemStats = function(link)
    local it = M.items[tonumber(tostring(link):match("item:(%d+)")) or 0]
    return it and it.stats and G.CopyTable(it.stats) or nil
  end
  G.UnitInParty = function(u) local _, k = U(u) return k == "group" end
  G.UnitInRaid = function() return nil end
  G.UnitXP = function() return M.player.xp end
  G.UnitXPMax = function() return M.player.xpMax end
  G.GetXPExhaustion = function() return M.player.rested end
  G.GetRestState = function() return M.player.rested and 1 or 2, "Reposé" end
  G.IsResting = function() return M.player.resting end
  G.UnitRangedDamage = function() return 2.9, 20, 30, 0, 0, 1 end
  G.GetUnitSpeed = function() return M.speed or 0, 7, 7, 4.7 end
  G.IsMounted = function() return M.mounted and true or false end
  G.SetRaidTarget = function(u, i) M.raidTargets = M.raidTargets or {}; M.raidTargets[u] = i end
  G.GetBindLocation = function() return "Auberge-Test" end
  G.GetNumSkillLines = function() return #(M.skills or {}) end
  G.GetSkillLineInfo = function(i)
    local sk = (M.skills or {})[i]
    if sk then return sk.name, false, false, sk.rank, 0, 0, sk.max or 300 end
  end
  G.UnitAttackSpeed = function() return 2.4, nil end
  G.UnitCastingInfo = function() return nil end
  G.UnitChannelInfo = function() return nil end
  G.CastingInfo = G.UnitCastingInfo
  G.ChannelInfo = G.UnitChannelInfo
  G.CheckInteractDistance = function() return true end
  G.UnitInRange = function() return true, true end
  G.UnitPosition = function() return nil end
  G.IsInGroup = function() return #M.group > 0 end
  G.IsInRaid = function() return false end
  G.IsInGuild = function() return true end
  G.GetGuildInfo = function(u) if tostring(u) == "player" then return "Guilde-Test", "Membre", 3, M.player.realm end end
  G.GuildRoster = function() end
  G.C_GuildInfo = modern and { GuildRoster = function() end } or nil
  G.UNKNOWNOBJECT, G.UNKNOWN = "Inconnu", "Inconnu"
  G.GetNumGroupMembers = function() return #M.group > 0 and (#M.group + 1) or 0 end
  G.GetNumSubgroupMembers = function() return #M.group end
  G.GetNumPartyMembers = G.GetNumSubgroupMembers
  G.GetNumRaidMembers = function() return 0 end
  G.UnitIsGroupLeader = function(u) return u == "player" end
  G.GetMoney = function() return M.player.money end
  G.GetZoneText = function() return M.player.zone end
  G.GetRealZoneText = G.GetZoneText
  G.GetSubZoneText = function() return M.player.subzone end
  G.GetMinimapZoneText = G.GetSubZoneText
  G.GetZonePVPInfo = function() return "contested" end
  G.IsInInstance = function() if M.instance then return true, "party" end return false, "none" end
  G.GetInstanceInfo = function() return M.player.zone, "none", 0, "", 5, 0, false, 0 end
  -- map / position / facing (Compass, Tracker). World convention as WoW: world X grows north, world Y
  -- grows west; M.mapRects[uiMapID] = { top = max world X, left = max world Y, w = yards, h = yards }.
  local function vec(x, y) return { x = x, y = y, GetXY = function(p) return p.x, p.y end } end
  local function rect(id) return M.mapRects[id] end
  G.C_Map = {
    GetBestMapForUnit = function(u) if u == "player" and not M.instance and M.pos then return M.pos.mapID end end,
    GetPlayerMapPosition = function(id, u)
      if M.instance or not M.pos or (id and id ~= M.pos.mapID) then return nil end
      return vec(M.pos.x, M.pos.y)
    end,
    GetMapInfo = function(id) return { mapID = id, name = M.mapNames[id] or M.player.zone, mapType = 3, parentMapID = 1415 } end,
    GetWorldPosFromMapPos = function(id, pos)
      local r = rect(id)
      if not r or type(pos) ~= "table" then return nil end
      local x, y = pos.x, pos.y
      if pos.GetXY then x, y = pos:GetXY() end
      return r.continent or 0, vec(r.top - y * r.h, r.left - x * r.w)
    end,
  }
  if modern then
    G.C_Map.GetMapWorldSize = function(id) local r = rect(id) if r then return r.w, r.h end return 0, 0 end
    G.CreateVector2D = vec
    G.C_SuperTrack = { GetSuperTrackedQuestID = function() return M.superTrack or 0 end,
      SetSuperTrackedUserWaypoint = function(on) M.superTrackUser = on end }
    -- user waypoint (Forever 1.60.1, not protected): recorded in M.userWaypoint
    G.UiMapPoint = { CreateFromCoordinates = function(id, x, y) return { uiMapID = id, position = vec(x, y) } end }
    G.C_Map.CanSetUserWaypointOnMap = function(id) return M.mapRects[id] ~= nil end
    G.C_Map.SetUserWaypoint = function(pt) M.userWaypoint = pt end
    G.C_Map.ClearUserWaypoint = function() M.userWaypoint = nil end
  end
  G.GetPlayerFacing = function() if M.instance then return nil end return M.facing end

  -- auras --------------------------------------------------------------------------------------
  local function aura(unit, i, filter)
    local list = M.auras[tostring(unit):lower()] or {}
    local want = filter and tostring(filter):find("HARMFUL") and "debuff" or "buff"
    local k = 0
    for _, a in ipairs(list) do
      if (a.kind or "buff") == want then
        k = k + 1
        if k == i then return a end
      end
    end
  end
  G.UnitBuff = function(u, i) local a = aura(u, i, "HELPFUL") if a then return auraTuple(a) end end
  G.UnitDebuff = function(u, i) local a = aura(u, i, "HARMFUL") if a then return auraTuple(a) end end
  G.UnitAura = function(u, i, f) local a = aura(u, i, f) if a then return auraTuple(a) end end
  G.AuraUtil = {
    FindAuraByName = function(name, u, f)
      for i = 1, 40 do
        local a = aura(u, i, f)
        if not a then return nil end
        if a.name == name then return auraTuple(a) end
      end
    end,
    ForEachAura = function(u, f, _, fn, usePacked)
      for i = 1, 40 do
        local a = aura(u, i, f)
        if not a then return end
        if fn(a) then return end
      end
    end,
  }
  G.GetWeaponEnchantInfo = function() return M.weaponEnchant or false, 0, 0, 0, false, 0, 0, 0 end
  G.GetShapeshiftForm = function() return 0 end
  G.GetNumShapeshiftForms = function() return 0 end
  G.GetShapeshiftFormInfo = function() return nil end
  G.GetTrackingTexture = function() return nil end

  -- pet ------------------------------------------------------------------------------------------
  G.HasPetUI = function() return M.pet.exists, true end
  G.GetPetHappiness = function() if M.pet.exists then return M.pet.happiness, 0, 0 end end
  G.GetPetLoyalty = function() return M.pet.exists and M.pet.loyalty or nil end
  G.GetPetTrainingPoints = function() return M.pet.tp, 0 end
  G.GetPetFoodTypes = function() return "Viande", "Poisson" end
  G.GetPetExperience = function() return 100, 1000 end
  G.PetHasActionBar = function() return M.pet.exists end
  G.GetPetActionInfo = function() return nil end
  G.IsPetAttackActive = function() return false end

  -- spells -------------------------------------------------------------------------------------
  local function findSpell(s)
    if type(s) == "number" then return M.spells[s], s end
    if type(s) == "string" then
      local base = s:gsub("%(.-%)", ""):gsub("%s+$", "")
      for id, sp in pairs(M.spells) do if sp.name == base then return sp, id end end
    end
  end
  G.GetSpellInfo = function(s)
    local sp, id = findSpell(s)
    if not sp then return nil end
    return sp.name, sp.rank, sp.icon, sp.castTime or 0, 0, 35, id
  end
  G.GetSpellTexture = function(s) local sp = findSpell(s) return sp and sp.icon end
  G.GetSpellLink = function(s) local sp, id = findSpell(s) if sp then return "|cff71d5ff|Hspell:" .. id .. "|h[" .. sp.name .. "]|h|r" end end
  G.IsSpellKnown = function(id) local sp = M.spells[id] return sp and sp.known or false end
  G.IsPlayerSpell = G.IsSpellKnown
  G.IsUsableSpell = function(s) local sp = findSpell(s) return sp ~= nil, false end
  G.IsCurrentSpell = function() return false end
  G.IsAutoRepeatSpell = function(s) return M.autoRepeat or false end
  G.IsSpellInRange = function() return 1 end
  G.GetSpellCooldown = function(s)
    local _, id = findSpell(s)
    local cd = id and M.spellCooldowns[id]
    if cd then return cd.start, cd.duration, 1, 1 end
    return 0, 0, 1, 1
  end
  G.GetNumSpellTabs = function() return 2 end
  G.GetSpellTabInfo = function(i) return "Général", 0, 0, 0 end
  G.GetSpellBookItemName = function() return nil end
  G.BOOKTYPE_SPELL, G.BOOKTYPE_PET = "spell", "pet"
  G.GetItemSpell = function() return nil end

  -- talents / spec -------------------------------------------------------------------------------
  G.GetNumTalentTabs = function() return 3 end
  G.GetTalentTabInfo = function(i) return ({ "Maîtrise des bêtes", "Précision", "Survie" })[i], nil, 5, "x" end
  G.GetNumTalents = function() return 0 end
  G.GetTalentInfo = function() return nil end
  G.GetActiveTalentGroup = function() return M.talentGroup end
  G.GetNumTalentGroups = function() return M.numTalentGroups end
  G.UnitCharacterPoints = function() return 0 end

  -- items / inventory ---------------------------------------------------------------------------
  G.GetItemInfo = function(item)
    local it = itemFromArg(item)
    if not it then return nil end
    return it.name, M.ItemLink(it.id), it.quality, it.ilvl, it.minLevel, it.type, it.subType, it.stack,
      it.equipLoc, 134400 + it.id, it.sellPrice, it.classID, it.subclassID
  end
  G.GetItemInfoInstant = function(item)
    local it = itemFromArg(item)
    if not it then return nil end
    return it.id, it.type, it.subType, it.equipLoc, 134400 + it.id, it.classID, it.subclassID
  end
  G.GetItemCount = function(item)
    local it = itemFromArg(item)
    if not it then return 0 end
    local n = 0
    for _, b in pairs(M.bags) do for _, s in pairs(b.slots) do if s.id == it.id then n = n + s.count end end end
    return n
  end
  G.GetItemIcon = function(item) local it = itemFromArg(item) return it and 134400 + it.id end
  G.GetItemCooldown = function() return 0, 0, 1 end
  G.IsUsableItem = function(item) return itemFromArg(item) ~= nil, false end
  G.GetInventorySlotInfo = function(name)
    local slots = { HeadSlot = 1, ChestSlot = 5, MainHandSlot = 16, SecondaryHandSlot = 17, RangedSlot = 18,
      AmmoSlot = 0, Bag0Slot = 20, Bag1Slot = 21, Bag2Slot = 22, Bag3Slot = 23 }
    return slots[name] or 1, 0
  end
  G.GetInventoryItemID = function(_, slot) local s = M.inventory[slot] return s and s.id ~= 0 and s.id or nil end
  G.GetInventoryItemLink = function(_, slot) local s = M.inventory[slot] return s and M.ItemLink(s.id) end
  G.GetInventoryItemCount = function(_, slot) local s = M.inventory[slot] return s and s.count or 0 end
  G.GetInventoryItemDurability = function(slot) local s = M.inventory[slot] if s and s.durMax then return s.dur, s.durMax end end
  G.GetInventoryItemTexture = function() return nil end
  G.GetInventoryItemQuality = function() return 1 end
  G.CanMerchantRepair = function() return M.merchantOpen end
  G.GetRepairAllCost = function() return 1234, true end
  G.RepairAllItems = function()
    if not M.merchantOpen then M.Violation("api", "RepairAllItems with no merchant") end
  end
  G.GetMerchantNumItems = function() return 0 end
  G.CloseMerchant = function() M.merchantOpen = false end
  G.PickupItem = function() if M.inCombat then M.Violation("protected", "PickupItem in combat") end end
  G.EquipItemByName = function() end
  G.ClearCursor = function() end
  G.CursorHasItem = function() return false end

  -- bags ---------------------------------------------------------------------------------------
  local function numSlots(bag) local b = M.bags[bag] return b and b.size or 0 end
  local function numFree(bag)
    local b = M.bags[bag]
    if not b then return 0, 0 end
    local used = 0
    for _ in pairs(b.slots) do used = used + 1 end
    return b.size - used, b.family or 0
  end
  local function useItem(bag, slot)
    if not bagSlot(bag, slot) then return end
    if not M.merchantOpen and M.inCombat then
      M.Violation("protected", "UseContainerItem from insecure code in combat")
    end
    M.lastUsed = { bag = bag, slot = slot }
    if M.merchantOpen then
      local s = bagSlot(bag, slot)
      local it = M.items[s.id]
      M.player.money = M.player.money + (it and it.sellPrice or 0) * s.count
      M.bags[bag].slots[slot] = nil
      M.sold = (M.sold or 0) + 1
    end
  end
  if modern then
    G.C_Container = {
      GetContainerNumSlots = numSlots,
      GetContainerNumFreeSlots = numFree,
      GetContainerItemInfo = function(bag, slot)
        local s = bagSlot(bag, slot)
        if not s then return nil end
        local it = M.items[s.id] or {}
        return { iconFileID = 134400 + s.id, stackCount = s.count, isLocked = false, quality = it.quality,
          isReadable = false, hasLoot = false, hyperlink = M.ItemLink(s.id), isFiltered = false,
          hasNoValue = (it.sellPrice or 0) == 0, itemID = s.id, isBound = false, itemName = it.name }
      end,
      GetContainerItemLink = function(bag, slot) local s = bagSlot(bag, slot) return s and M.ItemLink(s.id) end,
      GetContainerItemID = function(bag, slot) local s = bagSlot(bag, slot) return s and s.id end,
      GetContainerItemCooldown = function() return 0, 0, 1 end,
      UseContainerItem = useItem,
      PickupContainerItem = function() if M.inCombat then M.Violation("protected", "PickupContainerItem in combat") end end,
      GetBagName = function(bag) return bag == 0 and "Sac à dos" or "Sac" end,
    }
    G.C_ChatInfo = {
      RegisterAddonMessagePrefix = function(p) return M.RegisterPrefix(p) end,
      IsAddonMessagePrefixRegistered = function(p) return M.prefixes[p] or false end,
      SendAddonMessage = function(...) return M.SendAddon(...) end,
    }
    G.C_AddOns = {
      GetAddOnMetadata = addonMeta,
      IsAddOnLoaded = G.IsAddOnLoaded,
      LoadAddOn = function() return false, "MISSING" end,
    }
    G.C_Spell = {
      GetSpellInfo = function(s)
        local sp, id = findSpell(s)
        if sp then return { name = sp.name, iconID = sp.icon, castTime = sp.castTime or 0, minRange = 0,
          maxRange = 35, spellID = id, originalIconID = sp.icon } end
      end,
    }
    G.C_UnitAuras = {
      GetAuraDataByIndex = function(u, i, f)
        local a = aura(u, i, f)
        if a then return { name = a.name, icon = a.icon or 136000, applications = a.count or 0,
          duration = a.duration or 0, expirationTime = a.expirationTime or 0, spellId = a.spellId or 0,
          sourceUnit = a.source or "player", isHelpful = (a.kind or "buff") == "buff" } end
      end,
    }
    G.C_Item = {
      GetItemInfo = G.GetItemInfo, GetItemCount = G.GetItemCount, GetItemInfoInstant = G.GetItemInfoInstant,
      GetItemNameByID = function(id) local it = M.items[id] return it and it.name end,
      GetItemStats = function(link) return G.GetItemStats(link) end,
    }
    G.C_QuestLog = {
      GetNumQuestLogEntries = function() return #M.quests, #M.quests end,
      GetQuestIDForLogIndex = function(i) return M.quests[i] and not M.quests[i].header and qid(i) or nil end,
      GetInfo = function(i)
        local q = M.quests[i]
        if not q then return nil end
        if q.header then return { title = q.header, isHeader = true, questLogIndex = i } end
        return { title = q.title, level = q.level, questID = qid(i), questLogIndex = i, isHeader = false,
          suggestedGroup = q.group or 0 }
      end,
      IsComplete = function(id) local q = qAt(id) return q and q.complete or false end,
      GetQuestObjectives = function(id)
        local q = qAt(id)
        local out = {}
        for _, o in ipairs(q and q.objectives or {}) do
          out[#out + 1] = { text = o.text, finished = o.done, numFulfilled = o.have, numRequired = o.need, type = "monster" }
        end
        return out
      end,
      GetNextWaypoint = function(id)
        local w = M.waypoints[id]
        if w then return w[1], w[2], w[3] end
      end,
      GetQuestTagInfo = function(id)
        local q = qAt(id)
        if q and q.elite then return { tagID = 1, tagName = "Élite", isElite = true } end
      end,
    }
    G.C_Container.GetContainerItemQuestInfo = function(bag, slot)
      local s = bagSlot(bag, slot)
      local it = s and M.items[s.id]
      return { isQuestItem = it and it.questItem or false, questID = it and it.startsQuest, isActive = false }
    end
  else
    G.GetContainerNumSlots = numSlots
    G.GetContainerNumFreeSlots = numFree
    G.GetContainerItemInfo = function(bag, slot)
      local s = bagSlot(bag, slot)
      if not s then return nil end
      local it = M.items[s.id] or {}
      return 134400 + s.id, s.count, false, it.quality, false, false, M.ItemLink(s.id), false,
        (it.sellPrice or 0) == 0, s.id
    end
    G.GetContainerItemLink = function(bag, slot) local s = bagSlot(bag, slot) return s and M.ItemLink(s.id) end
    G.GetContainerItemID = function(bag, slot) local s = bagSlot(bag, slot) return s and s.id end
    G.GetContainerItemCooldown = function() return 0, 0, 1 end
    G.UseContainerItem = useItem
    G.PickupContainerItem = function() if M.inCombat then M.Violation("protected", "PickupContainerItem in combat") end end
    G.RegisterAddonMessagePrefix = function(p) return M.RegisterPrefix(p) end
    G.SendAddonMessage = function(...) return M.SendAddon(...) end
  end
  G.GetBagName = function(bag) return bag == 0 and "Sac à dos" or "Sac" end
  G.ContainerIDToInventoryID = function(bag) return 19 + bag end

  -- quests -------------------------------------------------------------------------------------
  G.GetNumQuestLogEntries = function() return #M.quests, #M.quests end
  G.GetQuestLogTitle = function(i)
    local q = M.quests[i]
    if not q then return nil end
    if q.header then return q.header, nil, nil, true, false end
    return q.title, q.level, q.group, false, false, q.complete and 1 or nil, nil, qid(i)
  end
  G.GetNumQuestLeaderBoards = function(i) local q = M.quests[i or M.questSel or 1] return q and #(q.objectives or {}) or 0 end
  G.GetQuestLogLeaderBoard = function(j, i)
    local q = M.quests[i or M.questSel or 1]
    local o = q and q.objectives and q.objectives[j]
    if o then return o.text, "monster", o.done end
  end
  G.SelectQuestLogEntry = function(i) M.questSel = i end
  G.GetQuestLogSelection = function() return M.questSel or 0 end
  G.IsQuestComplete = function(id) local q = qAt(id) return q and q.complete or false end
  G.GetQuestLogIndexByID = function(id) local _, i = qAt(id) return i or 0 end
  G.GetQuestsCompleted = function() return {} end
  -- quest helper APIs (Quests module): special item, XP reward (modern: by questID; legacy: selection), loot
  G.GetQuestLogSpecialItemInfo = function(i)
    local q = M.quests[i]
    if q and q.item then return M.ItemLink(q.item), 134400 + q.item, q.charges or 0, false end
  end
  G.GetQuestLogRewardXP = function(id)
    local q = id and qAt(id) or M.quests[M.questSel or 0]
    return q and q.xp or 0
  end
  G.GetNumLootItems = function() return #(M.loot or {}) end
  G.GetLootSlotInfo = function(i)
    local l = (M.loot or {})[i]
    if l then return 134400, l.name, l.count or 1, nil, 1, false, l.questItem or false, l.questID, l.isActive or false end
  end
  G.GetContainerItemQuestInfo = function(bag, slot)
    local s = bagSlot(bag, slot)
    local it = s and M.items[s.id]
    return it and it.questItem or false, it and it.startsQuest, false
  end

  -- macros (ToS guard: never in combat) -------------------------------------------------------
  G.MAX_ACCOUNT_MACROS, G.MAX_CHARACTER_MACROS = 120, 18
  local function macroList(perChar) return perChar and M.macros.char or M.macros.global end
  local function macroAt(idx)
    if type(idx) == "string" then
      for i, m in ipairs(M.macros.global) do if m.name == idx then return m, i end end
      for i, m in ipairs(M.macros.char) do if m.name == idx then return m, G.MAX_ACCOUNT_MACROS + i end end
      return nil, 0
    end
    if type(idx) ~= "number" then return nil, 0 end
    if idx > G.MAX_ACCOUNT_MACROS then return M.macros.char[idx - G.MAX_ACCOUNT_MACROS], idx end
    return M.macros.global[idx], idx
  end
  local function checkBody(fn, name, body)
    if type(body) == "string" and #body > 255 then
      M.Violation("macro", fn .. ": body of '" .. tostring(name) .. "' is " .. #body .. " > 255 chars")
    end
    if type(name) == "string" and #name > 16 then
      M.Violation("macro", fn .. ": name '" .. name .. "' > 16 chars")
    end
  end
  local function protected(fn)
    if M.inCombat then M.Violation("combat", fn .. " called during combat lockdown (ADDON_ACTION_BLOCKED)") return true end
  end
  G.GetNumMacros = function() return #M.macros.global, #M.macros.char end
  G.GetMacroInfo = function(idx) local m = macroAt(idx) if m then return m.name, m.icon, m.body end end
  G.GetMacroBody = function(idx) local m = macroAt(idx) return m and m.body end
  G.GetMacroIndexByName = function(name) local _, i = macroAt(name) return i or 0 end
  G.GetMacroIcons = function(t) t = t or {} t[#t + 1] = 134400 return t end
  G.GetMacroItemIcons = G.GetMacroIcons
  G.CreateMacro = function(name, icon, body, perChar)
    if protected("CreateMacro") then error("Interface action failed because of an AddOn", 2) end
    checkBody("CreateMacro", name, body)
    local list = macroList(perChar)
    local cap = perChar and G.MAX_CHARACTER_MACROS or G.MAX_ACCOUNT_MACROS
    if #list >= cap then error("CreateMacro: macro limit reached", 2) end
    list[#list + 1] = { name = name, icon = icon, body = body or "" }
    return perChar and (G.MAX_ACCOUNT_MACROS + #list) or #list
  end
  G.EditMacro = function(idx, name, icon, body)
    if protected("EditMacro") then error("Interface action failed because of an AddOn", 2) end
    local m, i = macroAt(idx)
    if not m then return 0 end
    checkBody("EditMacro", name or m.name, body or m.body)
    if name then m.name = name end
    if icon then m.icon = icon end
    if body then m.body = body end
    return i
  end
  G.DeleteMacro = function(idx)
    if protected("DeleteMacro") then return end
    local m, i = macroAt(idx)
    if not m then return end
    if i > G.MAX_ACCOUNT_MACROS then table.remove(M.macros.char, i - G.MAX_ACCOUNT_MACROS)
    else table.remove(M.macros.global, i) end
  end
  -- action bars: M.actions[slot]; PickupMacro/PickupSpell fill M.cursor, PlaceAction swaps it into the slot
  local function forbidden(fn)
    if not M.forbidBindings then return false end
    M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", fn .. "()")
    return true
  end
  G.PickupMacro = function(idx)
    if protected("PickupMacro") then return end
    if macroAt(idx) then local _, i = macroAt(idx) M.cursor = { type = "macro", id = i } end
  end
  G.PickupSpell = function(id) if protected("PickupSpell") then return end M.cursor = { type = "spell", id = id } end
  G.PlaceAction = function(slot)
    if protected("PlaceAction") then return end
    local c = M.cursor
    if not c then return end
    local old = M.actions[slot]
    local m = c.type == "macro" and macroAt(c.id)
    M.actions[slot] = { type = c.type, id = c.id, text = m and m.name or nil }
    M.cursor = old and { type = old.type, id = old.id } or nil
  end
  G.GetCursorInfo = function() if M.cursor then return M.cursor.type, M.cursor.id end end
  G.ClearCursor = function() M.cursor = nil end
  G.GetActionInfo = function(slot) local a = M.actions[slot] if a then return a.type, a.id, a.subType end end
  G.HasAction = function(slot) return M.actions[slot] ~= nil end
  G.GetActionText = function(slot) local a = M.actions[slot] return a and a.text end
  G.GetActionTexture = function(slot) return M.actions[slot] and 134400 or nil end
  G.GetBonusBarOffset = function() return M.bonusBar or 0 end
  G.GetBindingKey = function(cmd) return M.bindings[cmd] end
  G.GetBindingAction = function(key) for c, k in pairs(M.bindings) do if k == key then return c end end return "" end
  G.SetBinding = function(key, cmd)
    if protected("SetBinding") or forbidden("SetBinding") then return false end
    for c, k in pairs(M.bindings) do if k == key then M.bindings[c] = nil end end
    if cmd then M.bindings[cmd] = key end
    return true
  end
  G.SetBindingMacro = function(key, m) if protected("SetBindingMacro") then return false end M.bindings["MACRO " .. tostring(m)] = key return true end
  G.SetBindingSpell = function(key, s) if protected("SetBindingSpell") then return false end M.bindings["SPELL " .. tostring(s)] = key return true end
  G.SetBindingClick = function(key, b) if protected("SetBindingClick") then return false end M.bindings["CLICK " .. tostring(b)] = key return true end
  G.SaveBindings = function(set) if protected("SaveBindings") or forbidden("SaveBindings") then return end M.savedBindings = set end
  G.GetCurrentBindingSet = function() return 1 end
  G.ACCOUNT_BINDINGS, G.CHARACTER_BINDINGS = 1, 2
  -- protected actions an addon must never call (automation): record if called at all
  for _, fn in ipairs({ "CastSpellByName", "CastSpellByID", "UseAction", "RunMacroText", "TargetUnit",
    "AttackTarget", "CastPetAction", "PetAttack", "MoveForwardStart", "JumpOrAscendStart", "TurnLeftStart",
    "InteractUnit", "UseInventoryItem", "SpellStopCasting", "TargetNearestEnemy" }) do
    G[fn] = function() M.Violation("automation", fn .. " called from addon code (protected / automation)") end
  end

  -- chat -----------------------------------------------------------------------------------------
  G.SendChatMessage = function(msg, chatType, lang, target)
    if type(msg) == "string" and #msg > 255 then M.Violation("chat", "SendChatMessage > 255 bytes") end
    M.sentChat[#M.sentChat + 1] = { msg = msg, chatType = chatType, target = target }
  end
  G.Ambiguate = function(name) return (tostring(name):gsub("%-.*$", "")) end
  G.ChatFrame_AddMessageEventFilter = function() end
  if M.InstallAH then M.InstallAH(G, modern) end
  if M.flavor == "forever" and M.ApplyForever then M.ApplyForever(G) end
end

function M.RegisterPrefix(p)
  if type(p) ~= "string" or #p == 0 or #p > 16 then
    M.Violation("comms", "addon prefix must be 1..16 chars: " .. tostring(p))
    return false
  end
  M.prefixes[p] = true
  return true
end

function M.SendAddon(prefix, msg, channel, target)
  if not M.prefixes[prefix] then M.Violation("comms", "SendAddonMessage with unregistered prefix " .. tostring(prefix)) end
  if type(msg) ~= "string" then M.Violation("comms", "SendAddonMessage message must be a string") msg = tostring(msg) end
  if #msg > 255 then M.Violation("comms", "SendAddonMessage message > 255 bytes (" .. #msg .. ")") end
  if channel == "WHISPER" and not target then M.Violation("comms", "WHISPER without target") end
  M.sentAddon[#M.sentAddon + 1] = { prefix = prefix, msg = msg, channel = channel, target = target }
  return true
end

-- scenario helpers ----------------------------------------------------------------------------
function M.EnterCombat()
  M.inCombat = true
  M.FireEvent("PLAYER_REGEN_DISABLED")
  M.FireEvent("PLAYER_ENTER_COMBAT")
end
function M.LeaveCombat()
  M.inCombat = false
  M.FireEvent("PLAYER_LEAVE_COMBAT")
  M.FireEvent("PLAYER_REGEN_ENABLED")
end
function M.DeliverAddon(prefix, msg, channel, sender)
  return M.FireEvent("CHAT_MSG_ADDON", prefix, msg, channel or "PARTY", sender or "Amie-Royaume", "", 0, 0, "", 0)
end
function M.CombatLog(subevent, ...)
  M.combatEvent = { M.now, subevent, false, M.player.guid, M.player.name, 0x511, 0, "Creature-0-1", "Ours", 0xa48, 0, ... }
  M.FireEvent("COMBAT_LOG_EVENT_UNFILTERED")
  M.combatEvent = nil
end
function M.SetMerchant(open)
  M.merchantOpen = open and true or false
  M.FireEvent(open and "MERCHANT_SHOW" or "MERCHANT_CLOSED")
end

-- run "/cmd args" through SlashCmdList like the chat box does; returns true if a handler ran
function M.RunSlash(line)
  local cmd, rest = line:match("^(/%S+)%s*(.-)$")
  if not cmd then return false end
  cmd = cmd:lower()
  for key, fn in pairs(SlashCmdList) do
    for i = 1, 9 do
      local s = rawget(_G, "SLASH_" .. key .. i)
      if s == nil then break end
      if tostring(s):lower() == cmd then
        local hw = M.hardware
        M.hardware = true   -- a typed command = one hardware event
        M.SafeCall("slash " .. cmd, fn, rest, DEFAULT_CHAT_FRAME)
        M.hardware = hw
        return true
      end
    end
  end
  return false
end

-- every registered slash command, as { key=, cmd= }
function M.SlashCommands()
  local out = {}
  for key in pairs(SlashCmdList) do
    local s = rawget(_G, "SLASH_" .. key .. "1")
    if s then out[#out + 1] = { key = key, cmd = s } end
  end
  table.sort(out, function(a, b) return a.cmd < b.cmd end)
  return out
end

----------------------------------------------------------------------------------------------
-- auction house (both flavours) + mail invoices. Classic API in "legacy" (QueryAuctionItems,
-- GetAuctionItemInfo, PlaceAuctionBid…), C_AuctionHouse in "modern" = the WoW Forever 1.60 path
-- (warcraft.wiki.gg lists ReplicateItems / GetReplicateItemInfo / SendSearchQuery / SendBrowseQuery /
-- PlaceBid for game type "forever"; QueryAuctionItems is not listed there). Modelled like the client:
-- ReplicateItems = 15-min account-wide throttle, silent when throttled, async snapshot, bursty
-- REPLICATE_ITEM_LIST_UPDATE, some rows without item info yet (name nil, hasAllInfo false, itemID ok);
-- searches throttled (IsThrottledMessageSystemReady / AUCTION_HOUSE_THROTTLED_SYSTEM_READY; sending
-- while not ready = violation "throttle"). Purchases/posts/cancels must come from a click (hardware
-- event), with the AH open, ONE per click: anything else is recorded as a violation.
-- M.ah.list = { {id=, count=, buyout=, minBid=, owner=, auctionID=} }  M.ah.owned = same (mine)
----------------------------------------------------------------------------------------------
local function ahProtected(fn)
  if not M.hardware then M.Violation("automation", fn .. " outside a hardware event (click)") return false end
  M.hwCalls = M.hwCalls + 1
  if M.hwCalls > 1 then M.Violation("automation", fn .. ": more than one AH action in one click") return false end
  if not M.ah.open then M.Violation("api", fn .. " with the auction house closed") return false end
  return true
end
local function ahInfo(a)
  local it = M.items[a.id] or { name = "?", quality = 1 }
  if a.noInfo and not a.infoLoaded then   -- item data not cached yet: the client still gives the id/price
    a.infoLoaded = true
    return nil, nil, a.count, 1, nil, 1, nil, a.minBid or a.buyout, 1, a.buyout, 0, false, nil, nil, nil, 0, a.id, false
  end
  return it.name, 134400 + a.id, a.count, it.quality or 1, true, 1, "", a.minBid or a.buyout, 1, a.buyout,
    0, false, nil, a.owner, a.owner, 0, a.id, true
end
function M.SetAuctionHouse(open)
  M.ah.open = open and true or false
  M.FireEvent(open and "AUCTION_HOUSE_SHOW" or "AUCTION_HOUSE_CLOSED")
end
function M.AddMail(subject, money, invoice, item)
  M.ah.mail[#M.ah.mail + 1] = { subject = subject, money = money or 0, invoice = invoice, item = item, daysLeft = 29.9 }
end
function M.InstallAH(G, modern)
  G.AUCTION_SOLD_MAIL_SUBJECT = "Vente aux enchères réussie : %s"
  G.AUCTION_WON_MAIL_SUBJECT = "Objet remporté : %s"
  G.AUCTION_EXPIRED_MAIL_SUBJECT = "Vente aux enchères expirée : %s"
  G.AUCTION_REMOVED_MAIL_SUBJECT = "Vente aux enchères annulée : %s"
  G.AUCTION_OUTBID_MAIL_SUBJECT = "Surenchère : %s"
  G.GetInboxNumItems = function() return #M.ah.mail, #M.ah.mail end
  G.GetInboxHeaderInfo = function(i)
    local m = M.ah.mail[i]
    if not m then return nil end
    return nil, nil, "Hôtel des ventes", m.subject, m.money, 0, m.daysLeft, m.item and 1 or 0, false
  end
  G.GetInboxInvoiceInfo = function(i)
    local m = M.ah.mail[i]
    local v = m and m.invoice
    if not v then return nil end
    return v.type, v.item, "Inconnu", v.bid, v.buyout or v.bid, v.deposit or 0, v.cut or 0
  end
  G.GetInboxItem = function(i, a)
    local m = M.ah.mail[i]
    if not (m and m.item) then return nil end
    local it = M.items[m.item.id] or {}
    return it.name, m.item.id, 134400, m.item.count, 1, true
  end
  G.UnitFactionGroup = function() return "Alliance", "Alliance" end
  M.ah.posts = M.ah.posts or {}
  local function bagLoc(loc) local b = loc and M.bags[loc.bag] return b and b.slots[loc.slot] end
  if not modern then
    G.CanSendAuctionQuery = function()
      return M.ah.open, M.ah.open and (M.serverTime - (M.ah.lastAll or -1e9) >= 900)
    end
    G.QueryAuctionItems = function(name, lmin, lmax, page, usable, rarity, getAll, exact)
      if getAll and not M.hardware then M.Violation("automation", "QueryAuctionItems getAll outside a click") end
      if getAll then
        if M.serverTime - (M.ah.lastAll or -1e9) < 900 then return end   -- throttled: silently ignored
        M.ah.lastAll = M.serverTime
      end
      M.ah.queries[#M.ah.queries + 1] = { name = name, page = page, getAll = getAll, exact = exact }
      M.FireEvent("AUCTION_ITEM_LIST_UPDATE")
    end
    local pickup = G.PickupContainerItem
    G.PickupContainerItem = function(bag, slot) if pickup then pickup(bag, slot) end M.cursor = { bag = bag, slot = slot } end
    G.ClickAuctionSellItemButton = function() M.ah.sellSlot, M.cursor = M.cursor, nil end
    G.PostAuction = function(bid, buyout, runTime, stackSize, numStacks)
      if not ahProtected("PostAuction") then return end
      local it = bagLoc(M.ah.sellSlot)
      if not it then M.Violation("api", "PostAuction with an empty sell slot") return end
      M.ah.posts[#M.ah.posts + 1] = { id = it.id, count = stackSize, buyout = buyout, duration = runTime }
      M.SetBagItem(M.ah.sellSlot.bag, M.ah.sellSlot.slot, nil)
      M.ah.sellSlot = nil
    end
    G.GetNumAuctionItems = function(kind)
      local l = kind == "owner" and M.ah.owned or kind == "list" and M.ah.list or {}
      return #l, #l
    end
    G.GetAuctionItemInfo = function(kind, i)
      local l = kind == "owner" and M.ah.owned or M.ah.list
      local a = l[i]
      if a then return ahInfo(a) end
    end
    G.GetAuctionItemLink = function(kind, i)
      local l = kind == "owner" and M.ah.owned or M.ah.list
      return l[i] and M.ItemLink(l[i].id)
    end
    G.PlaceAuctionBid = function(kind, i, amount)
      if not ahProtected("PlaceAuctionBid") then return end
      local a = M.ah.list[i]
      if not a then return end
      M.ah.bids[#M.ah.bids + 1] = { id = a.id, count = a.count, amount = amount, buyout = a.buyout }
      if amount >= a.buyout then table.remove(M.ah.list, i) end
      M.player.money = M.player.money - amount
    end
    G.CancelAuction = function(i)
      if not ahProtected("CancelAuction") then return end
      local a = table.remove(M.ah.owned, i)
      if a then M.ah.cancels[#M.ah.cancels + 1] = a end
    end
    G.GetAuctionSellItemInfo = function()
      local it = bagLoc(M.ah.sellSlot)
      if not it then return nil end
      local info = M.items[it.id] or {}
      return info.name, 134400, it.count, 1, true, (info.sellPrice or 0) * it.count, 0, 1, 1, it.id
    end
    G.CalculateAuctionDeposit = function() return 0 end
  else
    local AH = {}
    G.C_AuctionHouse = AH
    G.Enum = G.Enum or {}
    G.Enum.ItemCommodityStatus = { Unknown = 0, Item = 1, Commodity = 2 }
    G.Enum.AuctionHouseSortOrder = { Price = 0, Name = 1 }
    G.ItemLocation = { CreateFromBagAndSlot = function(_, bag, slot) return { bag = bag, slot = slot } end }
    M.ah.throttleUntil = 0
    local function throttled(fn)
      if M.now < M.ah.throttleUntil then M.Violation("throttle", fn .. " while the AH message system is not ready") return true end
      M.ah.throttleUntil = M.now + 0.05
      newTimer(0.05, function() M.FireEvent("AUCTION_HOUSE_THROTTLED_SYSTEM_READY") end)
      return false
    end
    AH.IsThrottledMessageSystemReady = function() return M.now >= M.ah.throttleUntil end
    AH.SupportsCopperValues = function() return true end
    local function byID(id) for _, a in ipairs(M.ah.list) do if a.auctionID == id then return a end end end
    AH.ReplicateItems = function()
      if not M.hardware then M.Violation("automation", "ReplicateItems outside a click") end
      if M.serverTime - (M.ah.lastReplicate or -1e9) < 900 then   -- 15 min account-wide: silently ignored
        M.ah.replicateRefused = (M.ah.replicateRefused or 0) + 1
        return
      end
      M.ah.lastReplicate = M.serverTime
      M.ah.queries[#M.ah.queries + 1] = { getAll = true }
      local snap = {}
      for i, a in ipairs(M.ah.list) do snap[i] = a end
      newTimer(0.2, function()   -- async snapshot + burst of events
        M.ah.replicate = snap
        for _ = 1, 3 do M.FireEvent("REPLICATE_ITEM_LIST_UPDATE") end
      end)
    end
    -- another addon (e.g. Auctionator) doing the account's full scan: its data is readable by everyone
    function M.OtherAddonReplicate()
      M.ah.lastReplicate = M.serverTime
      local snap = {}
      for i, a in ipairs(M.ah.list) do snap[i] = a end
      M.ah.replicate = snap
      M.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    end
    AH.GetNumReplicateItems = function() return #(M.ah.replicate or {}) end
    AH.GetReplicateItemInfo = function(i0) local a = (M.ah.replicate or {})[i0 + 1] if a then return ahInfo(a) end end
    AH.GetReplicateItemLink = function(i0) local a = (M.ah.replicate or {})[i0 + 1] return a and M.ItemLink(a.id) end
    AH.SendBrowseQuery = function(query)
      if throttled("SendBrowseQuery") then return end
      M.ah.queries[#M.ah.queries + 1] = { browse = query and query.searchString }
      M.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    end
    AH.GetBrowseResults = function()
      local agg, out = {}, {}
      for _, a in ipairs(M.ah.list) do
        local u = math.floor(a.buyout / a.count)
        local r = agg[a.id]
        if not r then r = { itemKey = AH.MakeItemKey(a.id), minPrice = u, totalQuantity = 0 } agg[a.id] = r out[#out + 1] = r end
        r.minPrice, r.totalQuantity = math.min(r.minPrice, u), r.totalQuantity + a.count
      end
      return out
    end
    AH.PostItem = function(loc, duration, qty, bid, buyout)
      if not ahProtected("PostItem") then return end
      local it = bagLoc(loc)
      if not it then M.Violation("api", "PostItem: empty bag slot") return end
      M.ah.posts[#M.ah.posts + 1] = { id = it.id, count = qty, buyout = buyout, duration = duration }
      M.SetBagItem(loc.bag, loc.slot, nil)
    end
    AH.PostCommodity = function(loc, duration, qty, unitPrice)
      if not ahProtected("PostCommodity") then return end
      local it = bagLoc(loc)
      if not it then M.Violation("api", "PostCommodity: empty bag slot") return end
      M.ah.posts[#M.ah.posts + 1] = { id = it.id, count = qty, unit = unitPrice, buyout = unitPrice * qty, duration = duration, commodity = true }
      M.SetBagItem(loc.bag, loc.slot, nil)
    end
    AH.MakeItemKey = function(id) return { itemID = id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0 } end
    AH.GetItemCommodityStatus = function(x)
      local id = x
      if type(x) == "table" then local b = bagLoc(x) id = b and b.id end
      local it = M.items[id]
      return (it and it.stack or 1) > 1 and 2 or 1
    end
    AH.SendSearchQuery = function(key)   -- not protected (searching is not an action), but throttled
      if throttled("SendSearchQuery") then return end
      M.ah.queries[#M.ah.queries + 1] = { search = key and key.itemID, hw = M.hardware }
      local id = key and key.itemID
      if AH.GetItemCommodityStatus(id) == 2 then M.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED", id)
      else M.FireEvent("ITEM_SEARCH_RESULTS_UPDATED", key) end
    end
    local function results(id)
      local r = {}
      for _, a in ipairs(M.ah.list) do if a.id == id then r[#r + 1] = a end end
      table.sort(r, function(x, y) return x.buyout / x.count < y.buyout / y.count end)
      return r
    end
    AH.GetNumItemSearchResults = function(key) return #results(key.itemID) end
    AH.GetItemSearchResultInfo = function(key, i)
      local a = results(key.itemID)[i]
      if a then return { auctionID = a.auctionID, quantity = a.count, buyoutAmount = a.buyout, owners = { a.owner }, itemKey = key } end
    end
    AH.GetNumCommoditySearchResults = function(id) return #results(id) end
    AH.GetCommoditySearchResultInfo = function(id, i)
      local a = results(id)[i]
      if a then return { auctionID = a.auctionID, quantity = a.count, unitPrice = math.floor(a.buyout / a.count), owners = { a.owner }, itemID = id } end
    end
    AH.PlaceBid = function(auctionID, amount)
      if not ahProtected("PlaceBid") then return end
      local a = byID(auctionID)
      if not a then return end
      M.ah.bids[#M.ah.bids + 1] = { id = a.id, count = a.count, amount = amount, buyout = a.buyout }
      for i, x in ipairs(M.ah.list) do if x == a then table.remove(M.ah.list, i) break end end
    end
    AH.StartCommoditiesPurchase = function(id, qty)
      if not ahProtected("StartCommoditiesPurchase") then return end
      M.ah.pendingCommodity = { id = id, qty = qty }
      local r, left, total = results(id), qty, 0
      for _, a in ipairs(r) do
        local take = math.min(left, a.count)
        total = total + math.floor(a.buyout / a.count) * take
        left = left - take
        if left <= 0 then break end
      end
      M.FireEvent("COMMODITY_PRICE_UPDATED", math.floor(total / qty), total)
    end
    AH.ConfirmCommoditiesPurchase = function(id, qty)
      if not ahProtected("ConfirmCommoditiesPurchase") then return end
      if not (M.ah.pendingCommodity and M.ah.pendingCommodity.id == id) then M.Violation("api", "Confirm without Start") return end
      M.ah.bids[#M.ah.bids + 1] = { id = id, count = qty, commodity = true }
      M.ah.pendingCommodity = nil
    end
    AH.CancelCommoditiesPurchase = function() M.ah.pendingCommodity = nil end
    AH.GetNumOwnedAuctions = function() return #M.ah.owned end
    AH.GetOwnedAuctionInfo = function(i)
      local a = M.ah.owned[i]
      if a then return { auctionID = a.auctionID, itemKey = AH.MakeItemKey(a.id), itemLink = M.ItemLink(a.id),
        quantity = a.count, buyoutAmount = a.buyout, status = 0, timeLeftSeconds = 3600 } end
    end
    AH.QueryOwnedAuctions = function() M.FireEvent("OWNED_AUCTIONS_UPDATED") end
    AH.CancelAuction = function(auctionID)
      if not ahProtected("CancelAuction") then return end
      for i, a in ipairs(M.ah.owned) do
        if a.auctionID == auctionID then M.ah.cancels[#M.ah.cancels + 1] = table.remove(M.ah.owned, i) return end
      end
    end
  end
end

-- Route planner (agent PLAN-LUA): fake QuestieDB public API (contract 4), the TreeAIPlan data global and
-- completed quests. These set globals after load: run the harness with --allow LibQuestieDB --allow QuestDB
-- --allow NpcDB --allow ObjectDB --allow ItemDB --allow TreeAIPlan.
-- data = { quests = {[id] = {name=, questLevel=, ...}}, npcs = {...}, objects = {...}, items = {...},
--          areaToMap = {[areaId] = uiMapID}, xp = {[id] = xp} } (field names = QuestieDB schema names)
function M.InstallQuestieDB(data)
  local function entity(rows)
    local E = {}
    E.Get = function(id, key) local r = rows[id] if r then return r[key] end end
    E.GetAllIds = function(hash)
      local out = {}
      for id in pairs(rows) do if hash then out[id] = true else out[#out + 1] = id end end
      if not hash then table.sort(out) end
      return out
    end
    E.Exists = function(id) return rows[id] ~= nil end
    return E
  end
  local lib = { contractVersion = 4, minSupportedContract = 1,
    Quest = entity(data.quests or {}), Npc = entity(data.npcs or {}), Object = entity(data.objects or {}),
    Item = entity(data.items or {}), Enum = { raceMaskById = {} } }
  lib.RequireContract = function(v) if v >= 1 and v <= 4 then return true end return false, "contract" end
  local s = "return {"
  for a, m in pairs(data.areaToMap or {}) do s = s .. "[" .. a .. "]=" .. m .. "," end
  local xpdb = {}
  for id, xp in pairs(data.xp or {}) do xpdb[id] = { 0, xp } end
  local support = { ZoneDB = { private = { areaIdToUiMapId = s .. "}" } }, QuestXP = { db = xpdb } }
  lib.Support = { Get = function(name) return support[name] end }
  rawset(_G, "LibQuestieDB", lib)
  rawset(_G, "QuestDB", lib.Quest); rawset(_G, "NpcDB", lib.Npc)
  rawset(_G, "ObjectDB", lib.Object); rawset(_G, "ItemDB", lib.Item)
  return lib
end
function M.SetPlan(plan) rawset(_G, "TreeAIPlan", plan) end
function M.SetCompleted(ids)
  M.completed = ids or {}
  local fn = function(id) return M.completed[id] and true or false end
  local CQ = rawget(_G, "C_QuestLog")
  if type(CQ) == "table" then CQ.IsQuestFlaggedCompleted = fn else rawset(_G, "IsQuestFlaggedCompleted", fn) end  -- legacy: --allow it
end

M.Reset()
----------------------------------------------------------------------------------------------
-- "forever" flavour: what the real WoW Forever 1.60.1 client changes on top of "modern"
----------------------------------------------------------------------------------------------
local SecretMT = {}
local function boom(what) return function() error("attempt to " .. what .. " a secret value", 2) end end
SecretMT.__add, SecretMT.__sub, SecretMT.__mul, SecretMT.__div = boom("perform arithmetic on"), boom("perform arithmetic on"),
  boom("perform arithmetic on"), boom("perform arithmetic on")
SecretMT.__mod, SecretMT.__pow, SecretMT.__unm = boom("perform arithmetic on"), boom("perform arithmetic on"), boom("negate")
SecretMT.__lt, SecretMT.__le, SecretMT.__eq = boom("compare"), boom("compare"), boom("compare")
SecretMT.__len, SecretMT.__index, SecretMT.__newindex = boom("get length of"), boom("index"), boom("index")
SecretMT.__call = boom("call")
SecretMT.__concat = function(a, b)   -- allowed: concatenation gives a (secret) string; we keep it readable
  local function s(v) return getmetatable(v) == SecretMT and "<secret>" or tostring(v) end
  return s(a) .. s(b)
end
SecretMT.__tostring = function() return "<secret>" end
M.SecretMT = SecretMT
function M.Secret() return setmetatable({}, SecretMT) end
function M.IsSecret(v) return getmetatable(v) == SecretMT end

-- Classic-era globals that do not exist on Forever (no C function, no deprecated shim for "camelot")
M.FOREVER_REMOVED = {
  "GetSpellInfo", "GetSpellCooldown", "GetItemInfo", "GetItemCount", "GetItemCooldown", "GetItemSpell", "GetItemStats",
  "GetItemInfoInstant", "GetContainerItemInfo", "GetContainerNumSlots", "GetContainerNumFreeSlots",
  "GetContainerItemID", "GetContainerItemLink", "UseContainerItem", "PickupContainerItem", "UnitAura",
  "IsSpellKnown", "IsPlayerSpell", "GetPetHappiness", "GetPetLoyalty", "GetPetTrainingPoints", "GetPetFoodTypes",
  "GetNumSkillLines", "GetSkillLineInfo", "GetQuestLogTitle", "GetNumPartyMembers", "GetNumRaidMembers",
  "InterfaceOptions_AddCategory", "InterfaceOptionsFrame_OpenToCategory", "GetNumSpellTabs", "GetSpellTabInfo",
  "GetSpellBookItemName", "IsPassiveSpell", "HasPetSpells", "HasAction", "CastingInfo", "UnitCharacterPoints",
  "GetTalentTabInfo", "GetNumTalentTabs", "GetActiveTalentGroup", "GetAddOnMetadata", "IsAddOnLoaded",
  "GetAddOnInfo", "GetNumAddOns", "SendAddonMessage", "RegisterAddonMessagePrefix", "GetQuestGreenRange",
  "GetSpellName", "QueryAuctionItems", "GetAuctionItemInfo", "GetNumAuctionItems", "PlaceAuctionBid",
  "PostAuction", "CancelAuction", "CanSendAuctionQuery", "ClickAuctionSellItemButton", "GetAuctionSellItemInfo",
  "CalculateAuctionDeposit", "GetNumTradeSkills", "GetTradeSkillInfo", "GetTradeSkillLine",
}

function M.ApplyForever(G)
  local old = {}
  for _, n in ipairs(M.FOREVER_REMOVED) do old[n] = rawget(G, n); rawset(G, n, nil) end
  local function oldf(n) return old[n] or function() end end
  -- C_* tables that forwarded to a removed global keep working (bound to the original function)
  if G.C_Item then
    G.C_Item.GetItemStats = function(link) return oldf("GetItemStats")(link) end
    G.C_Item.GetItemSpell = G.C_Item.GetItemSpell or function(i) return oldf("GetItemSpell")(i) end
    G.C_Item.GetItemCooldown = G.C_Item.GetItemCooldown or function(i) return oldf("GetItemCooldown")(i) end
  end
  G.issecretvalue = function(v) return getmetatable(v) == SecretMT end
  G.canaccessvalue = function(v) return getmetatable(v) ~= SecretMT end
  G.WOW_PROJECT_ID = 99   -- unknown project id: the addon must not depend on it
  -- moved APIs
  G.C_PetInfo = {
    GetPetHappiness = oldf("GetPetHappiness"), GetPetLoyalty = oldf("GetPetLoyalty"),
    GetPetTrainingPoints = oldf("GetPetTrainingPoints"),
    GetPetFoodTypes = function() return { oldf("GetPetFoodTypes")() } end,   -- a table on Forever
  }
  G.C_SkillInfo = {
    GetNumSkillLines = oldf("GetNumSkillLines"),
    GetSkillLineInfo = function(i)
      local name, isHeader, _, rank, _, _, maxRank = oldf("GetSkillLineInfo")(i)
      if name then return { name = name, isHeader = isHeader and true or false, rank = rank or 0, maxRank = maxRank or 0,
        skillID = i } end
    end,
  }
  local SB = G.C_SpellBook or {}
  G.C_SpellBook = SB
  SB.IsSpellKnown = SB.IsSpellKnown or function(id) local sp = M.spells[id] return sp and sp.known or false end
  SB.GetNumSpellBookSkillLines = SB.GetNumSpellBookSkillLines or function() return 0 end
  SB.GetSpellBookSkillLineInfo = SB.GetSpellBookSkillLineInfo or function() return nil end
  SB.GetSpellBookItemName = SB.GetSpellBookItemName or function() return nil end
  SB.HasPetSpells = SB.HasPetSpells or function() return nil end
  G.Enum = G.Enum or {}
  G.Enum.SpellBookSpellBank = G.Enum.SpellBookSpellBank or { Player = 0, Pet = 1 }
  G.C_ActionBar = G.C_ActionBar or {}
  G.C_ActionBar.HasAction = G.C_ActionBar.HasAction or function() return false end
  G.C_ClassTalents = G.C_ClassTalents or { HasUnspentTalentPoints = function() return false, 0, 0 end }
  G.C_CombatLog = G.C_CombatLog or { IsCombatLogRestricted = function() return true end }
  -- secret values: in combat, other units' health / power / identity / casts / auras are secret
  local function wrapUnit(name, secretFor)
    local f = rawget(G, name)
    if type(f) ~= "function" then return end
    G[name] = function(u, ...)
      if M.inCombat and secretFor(u) then
        local r = { f(u, ...) }
        if r[1] == nil then return nil end
        for i = 1, #r do if r[i] ~= nil then r[i] = M.Secret() end end
        return unpack(r)
      end
      return f(u, ...)
    end
  end
  local function notMe(u) return u ~= "player" or M.secretPlayer end   -- M.secretPlayer: worst case
  local function notGroup(u)
    return not (u == "player" or u == "pet" or tostring(u):match("^party%d$") or tostring(u):match("^raid%d+$"))
  end
  for _, n in ipairs({ "UnitHealth", "UnitHealthMax", "UnitPower", "UnitPowerMax", "UnitCastingInfo", "UnitChannelInfo" }) do
    wrapUnit(n, notMe)
  end
  for _, n in ipairs({ "UnitGUID", "UnitName", "UnitCreatureType", "UnitCreatureFamily" }) do wrapUnit(n, notGroup) end
  wrapUnit("UnitDetailedThreatSituation", function() return true end)
  if G.C_UnitAuras and G.C_UnitAuras.GetAuraDataByIndex then
    local f = G.C_UnitAuras.GetAuraDataByIndex
    G.C_UnitAuras.GetAuraDataByIndex = function(u, ...)
      local d = f(u, ...)
      if d and M.inCombat and (u ~= "player" or M.secretPlayer) then
        for k in pairs(d) do d[k] = M.Secret() end
      end
      return d
    end
  end
  -- macro writes: only inside a hardware event (a click / a typed command) — else ADDON_ACTION_FORBIDDEN
  for _, n in ipairs({ "CreateMacro", "EditMacro", "DeleteMacro" }) do
    local f = rawget(G, n)
    if type(f) == "function" then
      G[n] = function(...)
        if not M.hardware then
          M.Violation("forbidden", n .. "() outside a hardware event (ADDON_ACTION_FORBIDDEN « UNKNOWN() »)")
          error("Interface action failed because of an AddOn", 2)
        end
        return f(...)
      end
    end
  end
end

return M
