-- wowmock.lua — a small World of Warcraft (Classic-family / WoW Forever) API mock for Lua 5.1.
-- Loaded by tests/wow/harness.lua BEFORE the addon files. Everything lives in the global table
-- `WowMock` (test-side only; the harness whitelists it). Two API flavours:
--   WowMock.Install("modern")  C_Container, C_ChatInfo, C_AddOns, C_Spell, C_UnitAuras…
--   WowMock.Install("legacy")  GetContainerItemInfo & co, global SendAddonMessage, no C_Container
--   WowMock.Install("forever") the REAL WoW: Forever client (1.60.1 build 70862, game type camelot), driven by
--     tests/wow/forever_ref.lua (generated from Blizzard's own Forever interface code by forever_api_check.py):
--     APIs the client lacks are absent, secret values (combat / instance) raise the client's errors, secret
--     arguments refused, hardware-event gating, unknown events refused, async + throttled AH, quest headers…
--     (see the « forever » section at the end of this file and research/fidelity.md).
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
  M.blocked = {}         -- TAINT: ADDON_ACTION_BLOCKED the real client would count ({fn})
  M.strictTaint = true   -- protected frames / functions: blocked in combat (or outside a click)
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
  M.macroFail, M.macroBlockedCalls = nil, 0   -- "forbidden" | "nilret" (see CreateMacro)
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
  -- forever: restrictions and secret values (see the « forever » section)
  M.encounter, M.chatLockdown, M.pvpMatch = false, false, false
  -- "always": SecretReturns APIs (UnitHealth, UnitInRange, GetRaidTargetIndex…) secret even out of combat (the
  -- documentation read literally); env TREEAI_WOW_SECRET_RETURNS=always runs a whole suite that way
  M.secretReturns = (os and os.getenv and os.getenv("TREEAI_WOW_SECRET_RETURNS")) or "restricted"
  M.secretBooleans = {}            -- secret booleans handed to addon code { fn, where } (a boolean test on one errors)
  M.secretBoolSeen = nil           -- « fn@where » already in M.secretBooleans
  M.questHeadersSeeded = nil
  -- (JOURNAL) quest log selection / abandon target (C_QuestLog: quest ids; Era: log indexes), abandoned ids,
  -- saved instance locks { {name=, mapID=, reset=, locked=, total=, done=, difficultyName=} }
  M.questSel, M.selQuest, M.abandonTarget, M.abandonIdx, M.abandoned = nil, 0, 0, nil, {}
  M.savedInstances = {}
  M.SeedDefaults()
end

local function record(list, kind, msg)
  local where, lvl, chain = "?", 3, nil
  while true do
    local info = debug.getinfo(lvl, "Sl")
    if not info then break end
    local src = tostring(info.short_src)
    if info.what ~= "C" and not src:find("wowmock%.lua$") and src ~= "(tail call)" then
      local here = src .. ":" .. tostring(info.currentline)
      if where == "?" then
        where = here
        if kind ~= "secret" then break end
        chain = {}
      elseif #chain < 4 then   -- secret: who called (where the secret value came from)
        chain[#chain + 1] = (here:gsub("^.-([%w_]+%.lua:%d+)$", "%1"))
      else break end
    end
    lvl = lvl + 1
  end
  if chain and #chain > 0 then where = where .. " < " .. table.concat(chain, " < ") end
  list[#list + 1] = { kind = kind, msg = msg, where = where }
end
function M.Violation(kind, msg) record(M.violations, kind, msg) end

function M.SafeCall(label, fn, ...)
  local args, n = { ... }, select("#", ...)
  local ok, err = xpcall(function() return fn(unpack(args, 1, n)) end, function(e)
    local named = M.SecretVmError and M.SecretVmError(e)   -- forever: « attempt to compare userdata… » = a secret
    return tostring(named or e) .. "\n" .. debug.traceback("", 2)
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

M.Widget = Widget   -- PLACEBUG: tests/wow/geometry.lua (opt-in real-client frame geometry) extends the widgets
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
function Widget:RegisterEvent(ev, level)
  if type(ev) ~= "string" then error("RegisterEvent: event must be a string", 2) end
  if M.flavor == "forever" then
    local flags = M.ref and M.ref.events[ev]
    if (flags and flags:find("H", 1, true)) or ev == "COMBAT_LOG_EVENT_UNFILTERED" or ev == "COMBAT_LOG_EVENT" then
      M.Violation("forbidden", "RegisterEvent(" .. ev .. ") on Forever (ADDON_ACTION_FORBIDDEN)")
      return
    end
    if M.ref and flags == nil then   -- the real client: an immediate Lua error
      error("Frame:RegisterEvent(): Attempt to register unknown event \"" .. ev .. "\"", level or 2)
    end
  end
  self.__events[ev] = true
end
Widget.RegisterUnitEvent = function(self, ev) Widget.RegisterEvent(self, ev, 3) end
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
function Widget:GetStringWidth()
  local t = self.__text
  if M.IsSecret and M.IsSecret(t) then return M.Secret(#tostring(M.Unsecret(t)) * 6, "number") end   -- aspect Text
  return #(tostring(t or "")) * 6
end
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
    M.blocked[#M.blocked + 1] = { fn = "SetAttribute()" }
  end
  self.__attrs[k] = v
end
function Widget:GetAttribute(k) return self.__attrs[k] end
-- TAINT: explicit protection (secure template / Blizzard protected frame) or implicit: anchored (SetPoint) to a
-- protected frame, like the real client. Regions (textures, font strings) are never protected themselves.
local REGION = { Texture = true, FontString = true, Line = true, MaskTexture = true, AnimationGroup = true, Animation = true }
local function protectedOf(w, depth)
  if type(w) ~= "table" or depth > 6 then return false end
  if rawget(w, "__protected") then return true end
  for _, p in ipairs(rawget(w, "__points") or {}) do
    local rel = p[2]
    if type(rel) == "string" then rel = rawget(_G, rel) end
    if type(rel) == "table" and rel ~= w and rawget(rel, "__kind") and protectedOf(rel, depth + 1) then return true end
  end
  return false
end
M.ProtectedOf = function(w) return protectedOf(w, 0) end
function Widget:IsProtected()
  if REGION[self.__kind] then return false, false end
  return protectedOf(self, 0), self.__protected or false
end
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
function Widget:EnableMouse(v) self.__mouse = v and true or false end
function Widget:IsMouseEnabled() return self.__mouse or false end
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

-- TAINT: what the real client blocks in combat on a protected frame (counted in the AddOns list as
-- « Échec des actions d'interface »). The call is dropped, ADDON_ACTION_BLOCKED fires, a violation is recorded.
function M.Blocked(fn)
  M.blocked[#M.blocked + 1] = { fn = fn }
  M.Violation("blocked", fn .. " blocked in combat (ADDON_ACTION_BLOCKED)")
  if M.FireEvent then M.FireEvent("ADDON_ACTION_BLOCKED", "TreeAI", fn) end
end
for _, name in ipairs({ "Show", "Hide", "SetPoint", "ClearAllPoints", "SetAllPoints", "SetSize", "SetWidth",
  "SetHeight", "SetScale", "SetParent", "SetFrameStrata", "SetFrameLevel", "EnableMouse" }) do
  local orig = Widget[name]
  Widget[name] = function(self, ...)
    if M.inCombat and M.strictTaint and not REGION[self.__kind] then
      local rel
      if name == "SetPoint" then rel = select(2, ...) elseif name == "SetAllPoints" or name == "SetParent" then rel = ... end
      if type(rel) == "string" then rel = rawget(_G, rel) end
      if protectedOf(self, 0) or (type(rel) == "table" and rel ~= self and rawget(rel, "__kind") and protectedOf(rel, 0)) then
        M.Blocked((self.__name or self.__kind or "Frame") .. ":" .. name .. "()")
        return
      end
    end
    return orig(self, ...)
  end
end

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
local function eventArgs(event, ...)   -- forever: payloads secret per the event documentation (forever_ref.lua)
  if M.flavor ~= "forever" or not M.ref or not M.Secret then return ... end
  if event == "ENCOUNTER_START" then M.encounter = true elseif event == "ENCOUNTER_END" then M.encounter = false end
  local flags = M.ref.events[event]
  local codes = flags and flags:match("s:(%S+)")
  if not codes then return ... end
  local a1 = ...
  local u = type(a1) == "string" and a1:lower() or ""
  local secret, keepFirst = false, false
  for c in codes:gmatch("[^,]+") do
    if c == "chat" and M.ChatLockdown() then secret = true
    elseif c == "R" then secret = true
    elseif c == "cast" and M.Restricted() and u ~= "player" and u ~= "pet" then secret, keepFirst = true, true
    elseif c == "id" and M.Restricted() then secret = true
    elseif c == "aura" and M.Restricted() and u ~= "player" then secret, keepFirst = true, true
    elseif c == "power" and M.Restricted() and u ~= "player" then secret, keepFirst = true, true
    end
  end
  if not secret then return ... end
  local n, a = select("#", ...), { ... }
  for i = keepFirst and 2 or 1, n do if a[i] ~= nil and type(a[i]) ~= "table" then a[i] = M.Secret(a[i]) end end
  return unpack(a, 1, n)
end
function M.FireEvent(event, ...)
  if M.flavor == "forever" then return M.FireEventRaw(event, eventArgs(event, ...)) end
  return M.FireEventRaw(event, ...)
end
function M.FireEventRaw(event, ...)
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
    if M.FrameHook then M.FrameHook(true) end   -- SPIKE: per-frame cost (timers + OnUpdate) measured by session_sim
    if M.GeoTick then M.GeoTick(dt) end         -- PLACEBUG: geometry engine (screen laid out, UI scale applied…)
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
    if M.FrameHook then M.FrameHook(false) end
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
-- (JOURNAL) the server side of an abandon: the quest leaves the log (QUEST_REMOVED then QUEST_LOG_UPDATE, a moment
-- later, as the server answers); quest ids stay stable (real ids do not follow the log index); M.abandoned keeps them
local function abandonQuest(id)
  for j, e in ipairs(M.quests) do if not e.header then e.id = e.id or (1000 + j) end end
  local q, i = qAt(id)
  if not q then return false end
  table.remove(M.quests, i)
  M.abandoned[#M.abandoned + 1] = q.id
  local t = { at = M.now + 0.2, fn = function()
    M.FireEvent("QUEST_REMOVED", q.id, false)
    M.FireEvent("QUEST_LOG_UPDATE")
  end }
  M.timers[#M.timers + 1] = t
  return true
end
M.AbandonServer = abandonQuest

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
  G.UISpecialFrames = {}   -- noms des cadres fermés par Échap (FrameXML)
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
  G.Minimap.__w, G.Minimap.__h = 140, 140
  function G.Minimap:GetZoom() return M.minimapZoom or 0 end
  function G.Minimap:GetZoomLevels() return 6 end
  G.IsIndoors = function() return M.indoors and true or false end
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
  G.UnitIsUnit = function(a, b)
    a, b = tostring(a):lower(), tostring(b):lower()
    local np = a:match("^(nameplate%d+)target$")   -- PVPMARK: « nameplateN target » = le joueur si d.targetsPlayer
    if np and b == "player" then local d = M.units[np] return d and d.targetsPlayer and true or false end
    return a == b
  end
  -- units with isPlayer = true are enemy / other players (world PvP on nameplates)
  G.UnitIsPlayer = function(u) local d, k = U(u) return k == "player" or k == "group" or (k == "other" and d and d.isPlayer) or false end
  G.UnitIsPVP = function(u)
    local d, k = U(u)
    if k == "player" then return M.player.pvp ~= false end
    if not d then return false end
    if d.pvp ~= nil then return d.pvp and true or false end
    return true
  end
  G.UnitIsPVPFreeForAll = function(u) local d = U(u) return d and d.ffa or false end
  G.UnitPlayerControlled = G.UnitIsPlayer
  G.UnitIsFriend = function(_, u) local _, k = U(u) return k ~= "target" end
  G.UnitIsEnemy = function(_, u) local _, k = U(u) return k == "target" end
  G.UnitCanAttack = function(_, u) local d, k = U(u) return (k == "target" or k == "other") and d and d.hostile or false end
  G.UnitClassification = function(u) local d = U(u) return d and d.classification or "normal" end
  G.UnitCreatureFamily = function(u) local d, k = U(u) if k == "pet" then return d.family end return d and d.family end
  G.UnitCreatureType = function(u) local d = U(u) return d and d.creatureType or "Bête" end
  G.UnitAffectingCombat = function(u) local _, k = U(u) if k == "player" or k == "pet" then return M.inCombat end return false end
  G.UnitIsConnected = function(u) local d = U(u) return d ~= nil and d.online ~= false end
  G.UnitIsAFK = function(u) local d = U(u) return d and d.afk or false end
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
  G.UnitInRange = function(u) local d, k = U(u) if k == "group" then return d.inRange ~= false, true end return true, true end
  -- party members (Team.lua): role, visibility, inspect (d.gear = { [slot] = itemID }), quests (d.quests = { [questID] = true }, nil = unknown)
  G.UnitGroupRolesAssigned = function(u) local d = U(u) return d and d.role or "NONE" end
  G.UnitIsVisible = function(u) local d = U(u) return d ~= nil and d.visible ~= false end
  G.CanInspect = function(u) local _, k = U(u) return k == "group" end
  G.NotifyInspect = function(u)
    local d, k = U(u)
    M.inspects = (M.inspects or 0) + 1
    if k == "group" and d.gear then
      local guid = G.UnitGUID(u)
      G.C_Timer.After(0.3, function() M.FireEvent("INSPECT_READY", guid) end)
    end
  end
  G.ClearInspectPlayer = function() end
  local function onQuest(u, id)
    local d, k = U(u)
    if k ~= "group" then return false end
    if d.quests == nil then return nil end   -- nil: unknown (as before this mock existed)
    return d.quests[id] == true
  end
  M.UnitOnQuest = onQuest
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
  G.GetZonePVPInfo = function() return M.zonePvP or "contested" end
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
  -- total, utilisés (Forever : C_PetInfo) ; M.pet.tp = points LIBRES, M.pet.tpUsed (défaut 60) = déjà utilisés
  G.GetPetTrainingPoints = function() local u = M.pet.tpUsed or 60 return (M.pet.tp or 0) + u, u end
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
      local hit, hitId
      for id, sp in pairs(M.spells) do
        if sp.name == base and (not hit or (sp.known and not hit.known)) then hit, hitId = sp, id end
      end
      if hit then return hit, hitId end
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
  G.GetInventoryItemLink = function(u, slot)
    local d, k = U(u)
    if k == "group" then local id = d.gear and d.gear[slot] return id and M.ItemLink(id) or nil end
    local s = M.inventory[slot] return s and M.ItemLink(s.id)
  end
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
          sourceUnit = a.source or "player", isHelpful = (a.kind or "buff") == "buff", dispelName = a.debuffType } end
      end,
    }
    G.C_Item = {
      GetItemInfo = G.GetItemInfo, GetItemCount = G.GetItemCount, GetItemInfoInstant = G.GetItemInfoInstant,
      GetItemNameByID = function(id) local it = M.items[id] return it and it.name end,
      GetItemStats = function(link) return G.GetItemStats(link) end,
    }
    G.C_QuestLog = {
      GetNumQuestLogEntries = function()
        local q = 0
        for _, e in ipairs(M.quests) do if not e.header then q = q + 1 end end
        return #M.quests, q
      end,
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
          out[#out + 1] = { text = o.text, finished = o.done, numFulfilled = o.have, numRequired = o.need, type = o.type or "monster" }
        end
        return out
      end,
      GetNextWaypoint = function(id)
        local w = M.waypoints[id]
        if w then return w[1], w[2], w[3] end
      end,
      IsUnitOnQuest = function(u, id) return M.UnitOnQuest(u, id) end,
      GetQuestTagInfo = function(id)
        local q = qAt(id) or (M.questTags and M.questTags[id] and { tag = M.questTags[id] })
        -- (QUESTQA) q.tag = tag du jeu (41 JcJ, 62 raid, 81 donjon) ; M.questTags[id] pour une quête pas prise
        local TAGN = { [1] = "Élite", [41] = "JcJ", [62] = "Raid", [81] = "Donjon" }
        if q and q.tag then return { tagID = q.tag, tagName = TAGN[q.tag] or "?", isElite = q.tag == 1 or q.elite or false } end
        if q and q.elite then return { tagID = 1, tagName = "Élite", isElite = true } end
      end,
      IsFailed = function(id) local q = qAt(id) return q and q.failed or false end,
      GetTimeAllowed = function(id)
        local q = qAt(id)
        if q and q.timer then return q.timer[1], q.timer[2] end
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
    return q.title, q.level, q.group, false, false, (q.failed and -1) or (q.complete and 1) or nil, nil, qid(i)
  end
  G.GetQuestUiMapID = function(id) local q = qAt(id) return q and q.map or 0 end
  if not modern then   -- (QUESTQA) client classique : chronos des quêtes (secondes restantes, index du journal)
    G.GetQuestTimers = function()
      local t = {}
      for _, q in ipairs(M.quests) do if q.timer then t[#t + 1] = q.timer[1] - q.timer[2] end end
      return unpack(t)
    end
    G.GetQuestIndexForTimer = function(k)
      local n = 0
      for i, q in ipairs(M.quests) do if q.timer then n = n + 1 if n == k then return i end end end
    end
  end
  if not modern then   -- (QUESTQA) client classique : GetQuestTagInfo(questID) → tagID, tagName
    G.GetQuestTagInfo = function(id)
      local q = qAt(id) or (M.questTags and M.questTags[id] and { tag = M.questTags[id] })
      local TAGN = { [1] = "Élite", [41] = "JcJ", [62] = "Raid", [81] = "Donjon" }
      if q and q.tag then return q.tag, TAGN[q.tag] end
      if q and q.elite then return 1, "Élite" end
    end
  end
  G.IsUnitOnQuest = function(i, u)   -- Classic: 1 / nil ; unknown member log (d.quests nil) = on it (no noise)
    if M.quests[i] and not M.quests[i].header then return M.UnitOnQuest(u, qid(i)) ~= false and 1 or nil end
  end
  G.GetNumQuestLeaderBoards = function(i) local q = M.quests[i or M.questSel or 1] return q and #(q.objectives or {}) or 0 end
  G.GetQuestLogLeaderBoard = function(j, i)
    local q = M.quests[i or M.questSel or 1]
    local o = q and q.objectives and q.objectives[j]
    if o then return o.text, o.type or "monster", o.done end
  end
  G.SelectQuestLogEntry = function(i) M.questSel = i end
  G.GetQuestLogSelection = function() return M.questSel or 0 end
  -- (JOURNAL) Era abandon: SetAbandonQuest() takes the SELECTED log entry, GetAbandonQuestName() its title,
  -- AbandonQuest() abandons it (absent on Forever: the forever layer removes these globals)
  G.SetAbandonQuest = function() M.abandonIdx = M.questSel end
  G.GetAbandonQuestName = function()
    local q = M.quests[M.abandonIdx or 0]
    return (q and not q.header and q.title) or ""
  end
  G.AbandonQuest = function()
    local i = M.abandonIdx
    M.abandonIdx = nil
    local q = i and M.quests[i]
    if q and not q.header then abandonQuest(qid(i)) end
  end
  -- (JOURNAL) instance locks (C functions of every client, called by Blizzard's RaidFrame): RequestRaidInfo asks the
  -- server, UPDATE_INSTANCE_INFO comes back; GetSavedInstanceInfo = the Mainline 14 returns (mapID last)
  G.RequestRaidInfo = function()
    M.timers[#M.timers + 1] = { at = M.now + 0.3, fn = function() M.FireEvent("UPDATE_INSTANCE_INFO") end }
  end
  G.GetNumSavedInstances = function() return #M.savedInstances end
  G.GetSavedInstanceInfo = function(i)
    local e = M.savedInstances[i]
    if not e then return nil end
    return e.name, 1000 + i, e.reset or 86400, e.difficulty or 1, e.locked ~= false, false, 0, e.raid or false,
      e.maxPlayers or 5, e.difficultyName or "Normal", e.total or 0, e.done or 0, false, e.mapID
  end
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
  -- failure modes seen in the real client (WowMock.macroFail): "forbidden" = the call is ignored and
  -- ADDON_ACTION_FORBIDDEN fires (no Lua error, returns nothing); "nilret" = ignored silently.
  local function macroFail(fn)
    if M.macroFail == "forbidden" then
      M.macroBlockedCalls = (M.macroBlockedCalls or 0) + 1
      M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", fn .. "()")
      return true
    elseif M.macroFail == "nilret" then
      M.macroBlockedCalls = (M.macroBlockedCalls or 0) + 1
      return true
    end
  end
  G.CreateMacro = function(name, icon, body, perChar)
    if protected("CreateMacro") then error("Interface action failed because of an AddOn", 2) end
    if macroFail("CreateMacro") then return nil end
    checkBody("CreateMacro", name, body)
    local list = macroList(perChar)
    local cap = perChar and G.MAX_CHARACTER_MACROS or G.MAX_ACCOUNT_MACROS
    if #list >= cap then error("CreateMacro: macro limit reached", 2) end
    list[#list + 1] = { name = name, icon = icon, body = body or "" }
    return perChar and (G.MAX_ACCOUNT_MACROS + #list) or #list
  end
  G.EditMacro = function(idx, name, icon, body)
    if protected("EditMacro") then error("Interface action failed because of an AddOn", 2) end
    if macroFail("EditMacro") then return nil end
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
  M.InstallTaint(G, modern)
  if M.flavor == "forever" and M.PruneForever then M.PruneForever(G) end
end

-- TAINT (strict) ------------------------------------------------------------------------------------
-- Blizzard protected frames (secure unit / action buttons) and nameplates (protected in combat on the
-- Midnight-restricted client: worst case), plus protected functions: in combat (or outside a click for the
-- click-only ones) = a blocked action the AddOns list would blame on TreeAI.
function M.InstallTaint(G, modern)
  local function prot(name, kind)
    local f = newWidget(kind or "Button", name, G.UIParent)
    f.__protected = true
    G[name] = f
    return f
  end
  for _, n in ipairs({ "TargetFrame", "PlayerFrame", "FocusFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2",
    "PartyMemberFrame3", "PartyMemberFrame4" }) do prot(n) end
  G.TargetFrame.IsVisible = function() return M.target and M.target.exists and true or false end
  G.TargetFrame.IsShown = G.TargetFrame.IsVisible
  for i = 1, 12 do prot("ActionButton" .. i, "CheckButton") prot("MultiBarBottomLeftButton" .. i, "CheckButton") end
  M.plateFrames = {}
  if modern then
    local function plateFor(unit)
      if type(unit) ~= "string" or not unit:match("^nameplate%d+$") or not (M.units and M.units[unit]) then return nil end
      local f = M.plateFrames[unit]
      if not f then
        f = newWidget("Frame", nil, G.UIParent)
        f.__protected = M.protectedPlates ~= false
        f.namePlateUnitToken = unit
        f.UnitFrame = { unit = unit }
        M.plateFrames[unit] = f
      end
      return f
    end
    G.C_NamePlate = G.C_NamePlate or {}
    G.C_NamePlate.GetNamePlateForUnit = plateFor
    G.C_NamePlate.GetNamePlates = function()
      local out = {}
      for u in pairs(M.units or {}) do local f = plateFor(u) if f then out[#out + 1] = f end end
      return out
    end
  end
  local function wrapFn(tbl, fname, label, clickOnly)
    local f = type(tbl) == "table" and rawget(tbl, fname)
    if type(f) ~= "function" then return end
    tbl[fname] = function(...)
      if M.strictTaint and M.inCombat then
        M.Violation("combat", label .. " called during combat lockdown (ADDON_ACTION_BLOCKED)")
        M.blocked[#M.blocked + 1] = { fn = label }
        return nil
      end
      if M.strictTaint and clickOnly and not M.hardware then
        M.Violation("forbidden", label .. " outside a click (protected / taints Blizzard code)")
        M.blocked[#M.blocked + 1] = { fn = label }
        return nil
      end
      return f(...)
    end
  end
  wrapFn(G, "SetRaidTarget", "SetRaidTarget()", true)
  -- (JOURNAL) the quest log selection and the abandon: only inside a click (the 3.8 rule: Blizzard's quest log is
  -- never touched from non-click code), never in combat (TreeAI's rule), one abandon per hardware event
  wrapFn(G, "SelectQuestLogEntry", "SelectQuestLogEntry()", true)
  wrapFn(G, "SetAbandonQuest", "SetAbandonQuest()", true)
  wrapFn(G, "AbandonQuest", "AbandonQuest()", true)
  wrapFn(G.C_QuestLog, "SetSelectedQuest", "C_QuestLog.SetSelectedQuest()", true)
  wrapFn(G.C_QuestLog, "SetAbandonQuest", "C_QuestLog.SetAbandonQuest()", true)
  wrapFn(G.C_QuestLog, "AbandonQuest", "C_QuestLog.AbandonQuest()", true)
  for _, ref in ipairs({ { G, "AbandonQuest" }, { G.C_QuestLog, "AbandonQuest" } }) do
    local tbl, fname = ref[1], ref[2]
    local f = type(tbl) == "table" and rawget(tbl, fname)
    if type(f) == "function" then
      tbl[fname] = function(...)
        if M.hardware then
          M.hwCalls = M.hwCalls + 1
          if M.hwCalls > 1 then M.Violation("automation", fname .. ": more than one abandon in one click") return nil end
        end
        return f(...)
      end
    end
  end
  wrapFn(G, "SetCVar", "SetCVar()", false)
  wrapFn(G, "InterfaceOptionsFrame_OpenToCategory", "InterfaceOptionsFrame_OpenToCategory()", false)
  wrapFn(G.Settings, "OpenToCategory", "Settings.OpenToCategory()", false)
  wrapFn(G.C_Map, "SetUserWaypoint", "C_Map.SetUserWaypoint()", true)
  wrapFn(G.C_SuperTrack, "SetSuperTrackedUserWaypoint", "C_SuperTrack.SetSuperTrackedUserWaypoint()", true)
  for _, n in ipairs({ "ShowUIPanel", "HideUIPanel", "CloseAllWindows", "ToggleFrame" }) do
    if rawget(G, n) == nil then G[n] = function() end end
    wrapFn(G, n, n .. "()", false)
  end
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
function M.Hw(fn, ...)   -- run fn as one hardware event (a player click), like Widget:Click
  local hw, n = M.hardware, M.hwCalls
  M.hardware, M.hwCalls = true, 0
  local r = { pcall(fn, ...) }
  M.hardware, M.hwCalls = hw, n
  if not r[1] then error(r[2], 2) end
  return unpack(r, 2)
end
-- the macro index of a name, as GetMacroIndexByName did (absent on Forever): for scenario code
function M.MacroIndex(name)
  for i, m in ipairs(M.macros.global) do if m.name == name then return i end end
  for i, m in ipairs(M.macros.char) do if m.name == name then return (rawget(_G, "MAX_ACCOUNT_MACROS") or 120) + i end end
  return 0
end
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
    -- forever: the server answers (round trip ~0.15 s, system busy ~0.3 s: WowMock.ahThrottleSec) and only while
    -- the AH is open
    local function ahDelay() return M.flavor == "forever" and (M.ahThrottleSec or 0.3) or 0.05 end
    local function respond(fn) if M.flavor == "forever" then newTimer(math.min(0.15, ahDelay()), fn) else fn() end end
    local function throttled(fn)
      if M.flavor == "forever" and not M.ah.open then
        M.ah.droppedClosed = (M.ah.droppedClosed or 0) + 1   -- AH closed: the query goes nowhere, nothing comes back
        return true
      end
      if M.now < M.ah.throttleUntil then
        M.Violation("throttle", fn .. " while the AH message system is not ready")
        M.FireEvent("AUCTION_HOUSE_THROTTLED_MESSAGE_DROPPED")
        return true
      end
      M.ah.throttleUntil = M.now + ahDelay()
      newTimer(ahDelay(), function() M.FireEvent("AUCTION_HOUSE_THROTTLED_SYSTEM_READY") end)
      return false
    end
    AH.IsThrottledMessageSystemReady = function() return M.now >= M.ah.throttleUntil end
    AH.SupportsCopperValues = function() return true end
    local function byID(id) for _, a in ipairs(M.ah.list) do if a.auctionID == id then return a end end end
    AH.ReplicateItems = function()
      if not M.hardware then M.Violation("automation", "ReplicateItems outside a click") end
      if M.flavor == "forever" and not M.ah.open then return end
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
      respond(function() M.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED") end)
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
      respond(function()
        if AH.GetItemCommodityStatus(id) == 2 then M.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED", id)
        else M.FireEvent("ITEM_SEARCH_RESULTS_UPDATED", key) end
      end)
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
    AH.QueryOwnedAuctions = function()
      if M.flavor == "forever" and not M.ah.open then return end
      respond(function() M.FireEvent("OWNED_AUCTIONS_UPDATED") end)
    end
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
-- "forever" flavour = the REAL WoW: Forever client (1.60.1 build 70862, game type « camelot », Mainline family,
-- Interface 16001, frFR). Driven by tests/wow/forever_ref.lua, GENERATED by tests/wow/forever_api_check.py from
-- Blizzard's own interface code (Gethe/wow-ui-source, branch forever: API documentation + camelot FrameXML):
--   * existence: a global function / C_ namespace member the real client lacks is REMOVED (nil), as in the game
--     (Era globals, deprecated shims of Blizzard_Deprecated*, invented C_ members);
--   * secret values: functions documented SecretReturns / SecretWhen*Restricted return secrets (userdata proxies
--     carrying the real value) while their restriction is active (combat, instance map, encounter); arithmetic,
--     comparison, #, indexing, calling, or a C function argument on a secret raises the client's error
--     (« attempt to compare local 'hp' (a secret number value, while execution tainted by 'TreeAI') ») AND records
--     VIOLATION [secret], even when the addon swallows the error in a pcall; type() gives the real type;
--     concatenation, string.format, strjoin, strconcat, tostring and tonumber accept secrets and return secrets;
--   * SecretArguments: a secret passed to an API that refuses it from addon code = error + violation;
--   * hardware event: CreateMacro / EditMacro / DeleteMacro / SetBinding* / SaveBindings only inside a click or a
--     typed command (else ADDON_ACTION_FORBIDDEN); SAY / YELL / EMOTE / CHANNEL chat too;
--   * events: RegisterEvent of an event the client does not have raises « Attempt to register unknown event »;
--     COMBAT_LOG_EVENT_UNFILTERED = ADDON_ACTION_FORBIDDEN; payloads secret per the event documentation;
--   * UnitPosition: player / party / raid only (nil for NPCs, enemy players, and everyone in an instance);
--     nameplates are forbidden (GetNamePlateForUnit = nil) in instances;
--   * auction house: queries need the AH open, are throttled (~0.6 s) and answered ASYNCHRONOUSLY;
--   * quest log: zone header entries (isHeader) before the quests, QuestInfo with every documented field.
-- WowMock.secretReturns = "restricted" (default: SecretReturns APIs such as UnitHealth are secret while any
-- restriction is active, the player included) | "always" (documentation read literally: always secret).
-- Blind spots (Lua 5.1 cannot hook them): `secret == x` with a non-secret x is silently false, a boolean test on a
-- secret boolean passes (noted in WowMock.secretBooleans), a secret table KEY is only found by WowMock.ScanSecretKeys.
----------------------------------------------------------------------------------------------
-- session_sim: a sample whose innermost function is defined below this line is the forever layer (secrets, flags,
-- wrappers) = C code in the real client, not counted as addon CPU; the API bodies above are counted as before
M.FOREVER_LAYER_LINE = debug.getinfo(1, "l").currentline
local rawtype, rawtostring, rawtonumber, rawpcall, rawxpcall = type, tostring, tonumber, pcall, xpcall
-- one registry per Lua state (session_sim runs 4 clients side by side and they share the string / table libraries):
-- proxy -> real value / kind, and the untouched library functions the forever flavour wraps
local SHARED = rawget(string, "__wowmock_secrets")
if not SHARED then
  SHARED = { val = setmetatable({}, { __mode = "k" }), kind = setmetatable({}, { __mode = "k" }),
    format = string.format, concat = table.concat }
  rawset(string, "__wowmock_secrets", SHARED)
end
local rawformat, rawconcat = SHARED.format, SHARED.concat
local secretVal, secretKind = SHARED.val, SHARED.kind
local secretBase = newproxy(true)          -- this mock instance's metatable: its errors go to this M
local SecretMT = getmetatable(secretBase)
M.SecretMT = SecretMT

local function isSecret(v) return rawtype(v) == "userdata" and secretKind[v] ~= nil end
M.IsSecret = isSecret
-- WowMock.Secret(value [, kind]) wraps a real value; WowMock.Secret() = an opaque secret number
-- one proxy per (kind, value) while it lives (weak cache): reading the same secret health / name every frame does not
-- allocate a new proxy each time (the session_sim GC budget measures the addon, not this mock)
local proxyCache = {}
function M.Secret(v, kind)
  if v == nil and kind == nil then v = 0 end
  if v == nil or isSecret(v) then return v end
  local k = kind or rawtype(v)
  local cacheable = (k == "number" or k == "string" or k == "boolean") and v == v
  local byKind = cacheable and proxyCache[k]
  if cacheable and not byKind then byKind = setmetatable({}, { __mode = "v" }) proxyCache[k] = byKind end
  local p = byKind and byKind[v]
  if p then return p end
  p = newproxy(secretBase)
  secretVal[p] = v
  secretKind[p] = k
  if byKind then byKind[v] = p end
  return p
end
function M.Unsecret(v) if isSecret(v) then return secretVal[v] end return v end
local wrapSecret, unwrap = M.Secret, M.Unsecret

-- the addon name in « execution tainted by '…' »
local function taintName() return M.addonName or "TreeAI" end
-- like Lua's own errors: name the local / upvalue that holds the secret in the function doing the operation
local function varinfo(level, value)
  local info = debug.getinfo(level + 1, "f")
  if not info then return nil end
  local i = 1
  while true do
    local n, v = debug.getlocal(level + 1, i)
    if not n then break end
    if rawequal(v, value) and n:sub(1, 1) ~= "(" then return "local '" .. n .. "'" end
    i = i + 1
  end
  if info and info.func then
    i = 1
    while true do
      local n, v = debug.getupvalue(info.func, i)
      if not n then break end
      if rawequal(v, value) then return "upvalue '" .. n .. "'" end
      i = i + 1
    end
  end
end
local function secretError(what, value, level)
  level = (level or 1) + 1
  local kind = secretKind[value] or "number"
  local vi = varinfo(level, value)
  local msg
  if vi then
    msg = rawformat("attempt to %s %s (a secret %s value, while execution tainted by '%s')", what, vi, kind, taintName())
  else
    msg = rawformat("attempt to %s a secret %s value (execution tainted by '%s')", what, kind, taintName())
  end
  M.Violation("secret", msg)
  M.secretErrorSeen = (M.secretErrorSeen or 0) + 1
  error(msg, level)
end
M.SecretError = secretError
local function secretOf(a, b) if isSecret(a) then return a end return b end
local function arith(a, b) secretError("perform arithmetic on", secretOf(a, b), 2) end
SecretMT.__add, SecretMT.__sub, SecretMT.__mul, SecretMT.__div, SecretMT.__mod, SecretMT.__pow = arith, arith, arith, arith, arith, arith
SecretMT.__unm = function(a) secretError("perform arithmetic on", a, 2) end
SecretMT.__lt = function(a, b) secretError("compare", secretOf(a, b), 2) end
SecretMT.__le = SecretMT.__lt
SecretMT.__eq = SecretMT.__lt
SecretMT.__len = function(a) secretError("get length of", a, 2) end
SecretMT.__index = function(a) secretError("index", a, 2) end
SecretMT.__newindex = function(a) secretError("index", a, 2) end
SecretMT.__call = function(a) secretError("call", a, 2) end
-- allowed: concatenating string / number secrets gives a secret string
SecretMT.__concat = function(a, b) return wrapSecret(rawtostring(unwrap(a)) .. rawtostring(unwrap(b)), "string") end
SecretMT.__tostring = function() return "<secret>" end
SecretMT.__metatable = "secret"

-- errors Lua 5.1 raises by itself on a secret (mixed-type compare, C function argument): recognisable by
-- « userdata » (the addon never uses userdata: every userdata in the mock is a secret)
local function secretVmError(msg)
  if rawtype(msg) ~= "string" or not msg:find("userdata", 1, true) then return nil end
  if msg:find("tainted by", 1, true) then return nil end
  local where, rest = msg:match("^(.-:%d+): (.*)$")
  rest = rest or msg
  local what = rest:match("^attempt to (compare) ") or rest:match("^attempt to (perform arithmetic on) ")
    or rest:match("^attempt to (concatenate) ")
  local txt
  if what then
    txt = rawformat("attempt to %s a secret value (execution tainted by '%s')  [Lua 5.1: %s]", what, taintName(), rest)
  else
    local fn = rest:match("^bad argument #%d+ to '([^']+)'")
    txt = rawformat("%s  [secret value passed to the C function '%s': refused from addon code]", rest, fn or "?")
  end
  return (where and (where .. ": ") or "") .. txt, where
end
M.SecretVmError = secretVmError
local function noteSwallowed(msg)
  local txt, where = secretVmError(msg)
  if txt then
    M.violations[#M.violations + 1] = { kind = "secret", msg = txt .. " (swallowed by pcall)", where = where or "?" }
  end
end
M.NoteSwallowed = noteSwallowed

-- restrictions ----------------------------------------------------------------------------------
local function compound(u)  -- « targettarget », « nameplate3target », « party1target »…
  return rawtype(u) == "string" and u:lower():match("^%a+%d*target") ~= nil and u:lower() ~= "target"
end
local function unitInfo(u)
  if rawtype(u) ~= "string" then return nil, "none" end
  local l = u:lower()
  if l == "player" then return M.player, "player" end
  if l == "pet" then return M.pet, "pet" end
  if l:match("^party%d$") or l:match("^raid%d+$") then return M.group[tonumber(l:match("%d+"))], "group" end
  if l:match("^partypet%d$") or l:match("^raidpet%d+$") then return {}, "grouppet" end
  local d = (l == "target" and M.target and M.target.exists and M.target) or (M.units and M.units[l])
  if d and d.isPlayer then
    for _, g in ipairs(M.group) do if g.name == d.name then return d, "group" end end
    return d, "enemyplayer"
  end
  if d then return d, "npc" end
  return nil, "none"
end
M.UnitInfo = unitInfo
local function playerControlled(u)
  local _, k = unitInfo(u)
  return k == "player" or k == "pet" or k == "group" or k == "grouppet" or k == "enemyplayer"
end
local function friendlyGroup(u)
  local _, k = unitInfo(u)
  return k == "player" or k == "pet" or k == "group" or k == "grouppet"
end
local function restricted() return M.inCombat or M.instance or M.encounter end
function M.ChatLockdown() return M.chatLockdown or M.encounter end
M.Restricted = restricted
-- which arguments make a call secret, per documentation predicate (code from forever_ref.lua)
local RULES = {   -- (a1, a2) = the call's first two arguments (unit, mob / second unit): no table per call
  R = function() if M.secretReturns == "always" then return true end return restricted() end,
  id = function(a1) return restricted() and not friendlyGroup(a1) end,
  name = function(a1) return restricted() and not playerControlled(a1) end,
  hpmax = function(a1) return restricted() and not playerControlled(a1) end,
  power = function(a1) return restricted() and not playerControlled(a1) end,
  powermax = function(a1) return restricted() and not playerControlled(a1) end,
  stats = function() return M.inCombat or M.encounter end,   -- ShouldUnitStatsBeSecret(): no unit = the player too
  aura = function(a1) return restricted() and (rawtostring(a1):lower() ~= "player" or M.secretPlayer) end,
  cast = function(a1) local l = rawtostring(a1):lower() return restricted() and l ~= "player" and l ~= "pet" end,
  threat = function(_, a2) return restricted() and a2 ~= nil and not friendlyGroup(a2) end,
  threatv = function(_, a2) return restricted() and (a2 == nil or not friendlyGroup(a2)) end,
  cmp = function(a1, a2) return (M.instance or M.encounter) and (compound(a1) or compound(a2)) end,
  cd = function() return M.inCombat end,
  combat = function() return M.inCombat end,
  chat = function() return M.ChatLockdown() end,
  loc = function(a1) return M.inCombat and rawtostring(a1):lower() ~= "player" end,
  encounter = function() return M.encounter end,
  pvp = function() return M.pvpMatch end,
}
M.SECRET_RULES = RULES

local codeCache = {}
local function secretNow(codes, a1, a2)
  local list = codeCache[codes]
  if not list then
    list = {}
    for c in codes:gmatch("[^,]+") do if RULES[c] then list[#list + 1] = RULES[c] end end
    codeCache[codes] = list
  end
  for i = 1, #list do if list[i](a1, a2) then return true end end
  return false
end
-- a secret boolean handed to addon code: where (3 addon frames), once per (function, place); harness notes them
local function noteSecretBoolean(name)
  if M.traceSecretBooleans == false then return end   -- session_sim: nobody reads them
  local chain = {}
  for lvl = 3, 40 do
    local info = debug.getinfo(lvl, "Sl")
    if not info then break end
    if info.what ~= "C" and not rawtostring(info.short_src):find("wowmock%.lua$") then
      chain[#chain + 1] = (rawtostring(info.short_src) .. ":" .. rawtostring(info.currentline)):gsub("^.-([%w_]+%.lua:%d+)$", "%1")
      if #chain >= 3 then break end
    end
  end
  local where = table.concat(chain, " < ")
  local key = name .. "@" .. where
  local seen = M.secretBoolSeen
  if not seen then seen = {} M.secretBoolSeen = seen end
  if seen[key] then return end
  seen[key] = true
  M.secretBooleans[#M.secretBooleans + 1] = { fn = name, where = where }
end
-- wrap every returned value (fields of a returned struct table) as secret — no table per call (varargs)
local function secretValue(name, v)
  local t = rawtype(v)
  if t == "table" and not rawget(v, "__kind") then
    local c, bools = {}, false
    for k, x in pairs(v) do c[k] = wrapSecret(x) if rawtype(x) == "boolean" then bools = true end end
    if bools then noteSecretBoolean(name) end
    return c
  elseif v ~= nil then
    if t == "boolean" then noteSecretBoolean(name) end
    return wrapSecret(v)
  end
  return nil
end
local function secretMap(name, n, v, ...)
  if n <= 1 then
    if n == 1 then return secretValue(name, v) end
    return
  end
  return secretValue(name, v), secretMap(name, n - 1, ...)
end
local function secretReturns(name, ...) return secretMap(name, select("#", ...), ...) end

-- reference -------------------------------------------------------------------------------------
local MOCK_DIR = (debug.getinfo(1, "S").source or ""):match("^@(.*)[/\\][^/\\]*$") or "."
function M.LoadForeverRef()
  local cache = rawget(_G, "package") and package.loaded or {}
  if cache.treeai_forever_ref then return cache.treeai_forever_ref end
  local chunk, err = loadfile(MOCK_DIR .. "/forever_ref.lua")
  if not chunk then error("wowmock: cannot load forever_ref.lua (" .. tostring(err) .. ")") end
  local raw = chunk()
  local ref = { build = raw.build, api = {}, globals = {}, members = {}, shim = {}, events = {}, enums = {}, ns = {} }
  for line in raw.api:gmatch("[^\n]+") do
    local n, f = line:match("^(%S+)%s*(%S*)")
    ref.api[n] = (f or ""):gsub("|", " ")
    local ns = n:match("^(C_%w+)%.")
    if ns then ref.ns[ns] = true end
  end
  for w in raw.globals:gmatch("%S+") do ref.globals[w] = true end
  for w in raw.members:gmatch("%S+") do ref.members[w] = true local ns = w:match("^(C_%w+)%.") if ns then ref.ns[ns] = true end end
  for w in raw.shim:gmatch("%S+") do ref.shim[w] = true end
  for line in raw.events:gmatch("[^\n]+") do
    local n, f = line:match("^(%S+)%s*(%S*)")
    ref.events[n] = (f or ""):gsub("|", " ")
  end
  for line in raw.enums:gmatch("[^\n]+") do
    local n, vals = line:match("^(%S+)%s+(%S+)")
    if n then
      local t = {}
      for k, v in vals:gmatch("([%w_]+)=(%-?%d+)") do t[k] = tonumber(v) end
      ref.enums[n] = t
    end
  end
  cache.treeai_forever_ref = ref
  return ref
end

-- C functions that exist in every Mainline-family client but that Blizzard's camelot FrameXML never calls
-- (so forever_ref.lua cannot see them): kept. The protected movement / cast functions stay to catch automation.
local KEEP = {}
for _, n in ipairs({ "issecurevariable", "strconcat", "PlaySoundFile", "SetBindingClick", "SetBindingMacro",
  "SetBindingSpell", "SetBindingItem", "GetMacroBody", "CastSpellByName", "CastSpellByID", "UseAction", "RunMacroText",
  "TargetUnit", "AttackTarget", "CastPetAction", "PetAttack", "MoveForwardStart", "JumpOrAscendStart", "TurnLeftStart",
  "InteractUnit", "UseInventoryItem", "SpellStopCasting", "TargetNearestEnemy" }) do KEEP[n] = true end
local LUA_BUILTIN = {}
for _, n in ipairs({ "assert", "collectgarbage", "dofile", "error", "getfenv", "getmetatable", "ipairs", "load",
  "loadfile", "loadstring", "module", "next", "pairs", "pcall", "print", "rawequal", "rawget", "rawset", "require",
  "select", "setfenv", "setmetatable", "tonumber", "tostring", "type", "unpack", "xpcall", "newproxy", "gcinfo",
  "CreateFrame" }) do LUA_BUILTIN[n] = true end

-- install -----------------------------------------------------------------------------------------
-- part 1, before InstallTaint: Forever-only APIs and behaviours
function M.ApplyForever(G)
  local ref = M.LoadForeverRef()
  M.ref = ref
  local old = {}
  for _, n in ipairs({ "GetPetHappiness", "GetPetLoyalty", "GetPetTrainingPoints", "GetPetFoodTypes",
    "GetNumSkillLines", "GetSkillLineInfo", "GetItemStats", "GetItemSpell", "GetItemCooldown", "GetSpellCooldown",
    "IsUsableItem", "GetItemIcon", "GetSpellTexture", "GetSpellLink", "IsSpellInRange", "IsAutoRepeatSpell",
    "IsCurrentSpell", "IsUsableSpell", "GetZonePVPInfo", "GetTrackingTexture", "GetQuestsCompleted",
    "EquipItemByName", "PickupItem", "PickupSpell", "UnitAura", "UnitBuff", "UnitDebuff", "GetBagName",
    "ContainerIDToInventoryID", "GetCoinTextureString", "SendChatMessage", "GuildRoster" }) do old[n] = rawget(G, n) end
  local function oldf(n) return old[n] or function() end end
  G.issecretvalue = isSecret
  G.canaccessvalue = function(v) return not isSecret(v) end
  G.canaccesssecrets = function() return false end
  G.issecrettable = function(t)
    if rawtype(t) ~= "table" then return false end
    for k, v in pairs(t) do if isSecret(k) or isSecret(v) then return true end end
    return false
  end
  G.canaccesstable = function(t) return not G.issecrettable(t) end
  G.secretwrap = function(...) local n = select("#", ...) local o = { ... } for i = 1, n do o[i] = wrapSecret(o[i]) end return unpack(o, 1, n) end
  G.scrubsecretvalues = function(...) local n = select("#", ...) local o = { ... } for i = 1, n do if isSecret(o[i]) then o[i] = nil end end return unpack(o, 1, n) end
  G.WOW_PROJECT_CAMELOT = 99
  G.WOW_PROJECT_ID = 99   -- unknown real number: the addon must compare with WOW_PROJECT_CAMELOT, not a literal
  G.GetBuildInfo = function() return "1.60.1", "70862", "Nov 4 2026", 16001, "1.60.1", "Release" end
  -- Enum: every documented enumeration (the mock's own entries kept where the docs have none)
  G.Enum = G.Enum or {}
  for name, t in pairs(ref.enums) do
    local cur = rawget(G.Enum, name)
    if rawtype(cur) == "table" then for k, v in pairs(t) do if cur[k] == nil then cur[k] = v end end
    else G.Enum[name] = t end
  end
  -- moved APIs (Forever = C_ namespaces)
  G.C_PetInfo = {
    GetPetHappiness = oldf("GetPetHappiness"), GetPetLoyalty = oldf("GetPetLoyalty"),
    GetPetTrainingPoints = oldf("GetPetTrainingPoints"),
    GetPetFoodTypes = function() local a, b = oldf("GetPetFoodTypes")() return { a, b } end,   -- a table on Forever
  }
  G.C_SkillInfo = {
    GetNumSkillLines = oldf("GetNumSkillLines"),
    GetSkillLineInfo = function(i)
      local name, isHeader, _, rank, _, _, maxRank = oldf("GetSkillLineInfo")(i)
      if name then return { skillID = i, name = name, isHeader = isHeader and true or false, isCollapsed = false,
        rank = rank or 0, tempPoints = 0, modifier = 0, maxRank = maxRank or 0, isAbandonable = false, stepCost = 0,
        rankCost = 0, minLevel = 0, costType = 0, parentSkillLineID = 0, skillLineCategoryID = 0, description = "" } end   -- SkillLineAttributes
    end,
  }
  local SB = G.C_SpellBook or {}
  G.C_SpellBook = SB
  SB.IsSpellKnown = SB.IsSpellKnown or function(id) local sp = M.spells[id] return sp and sp.known or false end
  -- the spellbook as the client builds it from the known spells: line 1 « Général », line 2 = the class
  local function book()
    local ids = {}
    for id, sp in pairs(M.spells) do if sp.known then ids[#ids + 1] = id end end
    table.sort(ids)
    return ids
  end
  SB.GetNumSpellBookSkillLines = SB.GetNumSpellBookSkillLines or function() return 2 end
  SB.GetSpellBookSkillLineInfo = SB.GetSpellBookSkillLineInfo or function(line)
    local n = #book()
    if line == 1 then return { name = "Général", iconID = 136243, itemIndexOffset = 0, numSpellBookItems = 0,
      isGuild = false, shouldHide = false, specID = nil, offSpecID = nil } end
    if line == 2 then return { name = M.player.classLocal or "Classe", iconID = 132162, itemIndexOffset = 0,
      numSpellBookItems = n, isGuild = false, shouldHide = false, specID = nil, offSpecID = nil } end
  end
  local function slotSpell(slot, bank)
    if bank == 1 then return nil end
    local id = book()[slot]
    return id, id and M.spells[id]
  end
  SB.GetSpellBookItemName = SB.GetSpellBookItemName or function(slot, bank)
    local _, sp = slotSpell(slot, bank)
    if sp then return sp.name, sp.rank or "" end
  end
  SB.GetSpellBookItemInfo = SB.GetSpellBookItemInfo or function(slot, bank)
    local id, sp = slotSpell(slot, bank)
    if not sp then return nil end
    return { actionID = id, spellID = id, itemType = 1, name = sp.name, subName = sp.rank or "", iconID = sp.icon,
      isPassive = sp.passive and true or false, isOffSpec = false, skillLineIndex = 2 }
  end
  SB.GetSpellBookItemType = SB.GetSpellBookItemType or function(slot, bank)
    local id = slotSpell(slot, bank)
    if id then return 1, id, id end
    return 0
  end
  SB.HasPetSpells = SB.HasPetSpells or function() if M.pet and M.pet.exists then return 0, "PET" end return nil end
  -- spellIdentifier = id or name (rank-agnostic): a slot only for a LEARNED spell
  SB.FindSpellBookSlotForSpell = SB.FindSpellBookSlotForSpell or function(s)
    if rawtype(s) == "number" then local sp = M.spells[s] if sp and sp.known then return s, 0 end return nil end
    if rawtype(s) == "string" then
      for id, sp in pairs(M.spells) do if sp.known and sp.name == s then return id, 0 end end
    end
  end
  G.C_Spell = G.C_Spell or {}
  local CS = G.C_Spell
  CS.GetSpellName = CS.GetSpellName or function(s) local i = CS.GetSpellInfo(s) return i and i.name end
  CS.GetSpellTexture = CS.GetSpellTexture or function(s) local i = CS.GetSpellInfo(s) if i then return i.iconID, i.originalIconID end end
  CS.GetSpellSubtext = CS.GetSpellSubtext or function(s)
    local sp = rawtype(s) == "number" and M.spells[s]
    if not sp then for _, x in pairs(M.spells) do if x.name == s then sp = x break end end end
    return sp and (sp.rank or "") or nil
  end
  CS.GetSpellLink = CS.GetSpellLink or function(s) return oldf("GetSpellLink")(s) end
  CS.GetSpellCooldown = CS.GetSpellCooldown or function(s)
    local start, duration = oldf("GetSpellCooldown")(s)
    if start == nil then return nil end
    return { startTime = start, duration = duration, isEnabled = true, isActive = (duration or 0) > 0, modRate = 1,
      activeCategory = nil, timeUntilEndOfStartRecovery = nil, isOnGCD = false }
  end
  CS.IsSpellInRange = CS.IsSpellInRange or function(s, u) local r = oldf("IsSpellInRange")(s, u) if r == nil then return nil end return r == 1 end
  CS.IsAutoRepeatSpell = CS.IsAutoRepeatSpell or function(s) return oldf("IsAutoRepeatSpell")(s) and true or false end
  CS.IsCurrentSpell = CS.IsCurrentSpell or function(s) return oldf("IsCurrentSpell")(s) and true or false end
  CS.IsSpellUsable = CS.IsSpellUsable or function(s) return oldf("IsUsableSpell")(s) end
  CS.PickupSpell = CS.PickupSpell or function(s) return oldf("PickupSpell")(s) end
  G.C_Item = G.C_Item or {}
  local CI = G.C_Item
  CI.GetItemStats = function(link) return oldf("GetItemStats")(link) end
  CI.GetItemSpell = CI.GetItemSpell or function(i) return oldf("GetItemSpell")(i) end
  CI.GetItemCooldown = CI.GetItemCooldown or function(i) local s, d, e = oldf("GetItemCooldown")(i) return s, d, e == 1 or e == true end
  CI.IsUsableItem = CI.IsUsableItem or function(i) return oldf("IsUsableItem")(i) end
  CI.GetItemIconByID = CI.GetItemIconByID or function(i) return oldf("GetItemIcon")(i) end
  CI.EquipItemByName = CI.EquipItemByName or function(...) return oldf("EquipItemByName")(...) end
  CI.PickupItem = CI.PickupItem or function(...) return oldf("PickupItem")(...) end
  if G.C_Container then
    G.C_Container.GetItemCooldown = G.C_Container.GetItemCooldown or function(id) return oldf("GetItemCooldown")(id) end
    G.C_Container.ContainerIDToInventoryID = G.C_Container.ContainerIDToInventoryID or oldf("ContainerIDToInventoryID")
  end
  G.C_PvP = G.C_PvP or {}
  G.C_PvP.GetZonePVPInfo = G.C_PvP.GetZonePVPInfo or function() return oldf("GetZonePVPInfo")() end
  G.C_Minimap = G.C_Minimap or {}
  G.C_Minimap.GetTrackingInfo = G.C_Minimap.GetTrackingInfo or function() return nil end
  G.C_Minimap.GetNumTrackingTypes = G.C_Minimap.GetNumTrackingTypes or function() return 0 end
  G.C_CurrencyInfo = G.C_CurrencyInfo or {}
  G.C_CurrencyInfo.GetCoinTextureString = G.C_CurrencyInfo.GetCoinTextureString or oldf("GetCoinTextureString")
  G.C_GuildInfo = G.C_GuildInfo or {}
  G.C_GuildInfo.GuildRoster = G.C_GuildInfo.GuildRoster or function() end
  G.C_ChatInfo = G.C_ChatInfo or {}
  -- SAY / YELL / EMOTE / CHANNEL need a hardware event (Patch 1.60.1 API changes); a party message does not
  G.C_ChatInfo.SendChatMessage = function(msg, chatType, lang, target)
    local ct = tostring(chatType or "SAY"):upper()
    if (ct == "SAY" or ct == "YELL" or ct == "EMOTE" or ct == "CHANNEL") and not M.hardware then
      M.Violation("forbidden", "C_ChatInfo.SendChatMessage(" .. ct .. ") outside a hardware event (ADDON_ACTION_FORBIDDEN)")
      return
    end
    return oldf("SendChatMessage")(msg, chatType, lang, target)
  end
  -- addon messages: refused during encounters (C_ChatInfo.AreOutgoingAddonChatMessagesRestricted)
  local send = G.C_ChatInfo.SendAddonMessage
  if send then
    G.C_ChatInfo.SendAddonMessage = function(...)
      if M.encounter then return 11 end   -- Enum.SendAddonMessageResult.AddOnMessageLockdown (nothing sent)
      local ok = send(...)
      if ok == false then return 9 end    -- GeneralError
      return 0                            -- Success (a number: always truthy, like the real client)
    end
  end
  G.C_ChatInfo.AreOutgoingAddonChatMessagesRestricted = function() return M.encounter and true or false end
  G.C_ChatInfo.InChatMessagingLockdown = function() return M.ChatLockdown() and true or false end
  G.C_Secrets = {
    HasSecretRestrictions = function() return restricted() and true or false end,
    ShouldUnitIdentityBeSecret = function(u) return RULES.id(u) and true or false end,
    ShouldUnitStatsBeSecret = function() return RULES.stats() and true or false end,
    ShouldUnitSpellCastingBeSecret = function(u) return RULES.cast(u) and true or false end,
    ShouldAurasBeSecret = function() return restricted() and true or false end,
    ShouldCooldownsBeSecret = function() return M.inCombat and true or false end,
    ShouldUnitHealthMaxBeSecret = function(u) return RULES.hpmax(u) and true or false end,
    ShouldUnitPowerBeSecret = function(u) return RULES.power(u) and true or false end,
    ShouldUnitComparisonBeSecret = function(a, b) return RULES.cmp(a, b) and true or false end,
  }
  G.C_RestrictedActions = G.C_RestrictedActions or {}
  G.C_RestrictedActions.IsAddOnRestrictionActive = function(kind)
    if kind == 0 then return M.inCombat and true or false end
    return restricted() and true or false
  end
  G.C_RestrictedActions.InCombatLockdown = function() return M.inCombat and true or false end
  G.Constants = G.Constants or {}
  G.Constants.MacroConsts = { MAX_ACCOUNT_MACROS = 120, MAX_CHARACTER_MACROS = 30 }
  G.Constants.QuestLogConsts = { MAXIMUM_NUM_QUESTS_LOG_CAN_ACCEPT = 40 }
  G.MAX_QUESTLOG_QUESTS, G.MAX_QUESTS = 40, 40   -- Blizzard_FrameXMLBase/Constants.lua on camelot
  G.MAX_CHARACTER_MACROS = 30   -- the mock's own cap follows Forever
  G.Enum.SpellBookSpellBank = G.Enum.SpellBookSpellBank or { Player = 0, Pet = 1 }
  G.C_ActionBar = G.C_ActionBar or {}
  G.C_ActionBar.HasAction = G.C_ActionBar.HasAction or function(slot) return M.actions[slot] ~= nil end
  G.C_ActionBar.GetActionText = G.C_ActionBar.GetActionText or function(slot) local a = M.actions[slot] return a and a.text end
  G.C_ActionBar.GetActionTexture = G.C_ActionBar.GetActionTexture or function(slot) return M.actions[slot] and 134400 or nil end
  G.C_ActionBar.GetBonusBarOffset = G.C_ActionBar.GetBonusBarOffset or function() return M.bonusBar or 0 end
  G.C_ClassTalents = G.C_ClassTalents or { HasUnspentTalentPoints = function() return false, 0, 0 end }
  G.C_CombatLog = G.C_CombatLog or {}
  G.C_CombatLog.IsCombatLogRestricted = function() return true end
  G.C_UnitAuras = G.C_UnitAuras or {}
  G.C_UnitAuras.GetBuffDataByIndex = G.C_UnitAuras.GetBuffDataByIndex or function(u, i, f)
    return G.C_UnitAuras.GetAuraDataByIndex(u, i, "HELPFUL" .. (f and ("|" .. f) or ""))
  end
  G.C_UnitAuras.GetDebuffDataByIndex = G.C_UnitAuras.GetDebuffDataByIndex or function(u, i, f)
    return G.C_UnitAuras.GetAuraDataByIndex(u, i, "HARMFUL" .. (f and ("|" .. f) or ""))
  end
  -- real Forever APIs the addon calls that the old mock lacked (so those paths were never run): minimal, doc-shaped.
  -- (World data APIs — C_TaxiMap nodes, C_AreaPoiInfo, C_QuestLog.GetQuestsOnMap — stay absent: an empty answer
  -- would claim « no flight master here » and poison the addon's caches; scenarios give them data.)
  local function stub(tbl, name, fn) if rawget(tbl, name) == nil then tbl[name] = fn end end
  stub(G, "IsLoggedIn", function() return true end)
  stub(G, "IsSwimming", function() return M.swimming and true or false end)
  stub(G, "SetCursor", function() return true end)
  stub(G, "ResetCursor", function() end)
  stub(G, "UnitIsGroupAssistant", function() return false end)
  stub(G, "UnitQuestTrivialLevelRange", function() return M.trivialRange or 5 end)
  stub(G, "GetRaidTargetIndex", function(u) local d = unitInfo(u) return d and d.mark or nil end)
  stub(G, "GetAddOnMemoryUsage", function() return 1024 end)
  stub(G, "UpdateAddOnMemoryUsage", function() end)
  stub(G, "GetPVPSessionStats", function() return 0, 0 end)
  -- character stats (documented SecretWhenUnitStatsRestricted: secret in combat, the player included)
  stub(G, "UnitAttackPower", function() return 120, 0, 0 end)
  stub(G, "UnitRangedAttackPower", function() return 150, 0, 0 end)
  stub(G, "UnitDamage", function() return 20, 30, 0, 0, 0, 0, 1 end)
  stub(G, "UnitStat", function(_, i) return 40 + (i or 1), 40 + (i or 1), 0, 0 end)
  stub(G, "UnitArmor", function() return 900, 900, 900, 0 end)
  stub(G, "UnitResistance", function() return 0, 0, 0, 0 end)
  for _, n in ipairs({ "GetCritChance", "GetRangedCritChance", "GetSpellCritChance", "GetDodgeChance", "GetParryChance",
    "GetBlockChance", "GetHitModifier", "GetSpellHitModifier", "GetHaste", "GetMeleeHaste" }) do stub(G, n, function() return 5 end) end
  stub(G, "GetCombatRating", function() return 0 end)
  stub(G, "GetManaRegen", function() return 2, 1 end)
  stub(G, "GetPowerRegen", function() return 2, 1 end)
  local QLx = G.C_QuestLog or {}
  stub(QLx, "ReadyForTurnIn", function(id) local q = qAt(id) return q and q.complete or false end)
  stub(QLx, "IsFailed", function(id) local q = qAt(id) return q and q.failed or false end)
  stub(QLx, "GetTimeAllowed", function(id) local q = qAt(id) if q and q.timer then return q.timer, 0 end end)
  stub(QLx, "GetDistanceSqToQuest", function() return nil end)
  stub(QLx, "IsPushableQuest", function(id) return qAt(id) ~= nil end)
  stub(QLx, "GetMaxNumQuestsCanAccept", function() return 40 end)   -- QuestLogConstsMainlineCamelot: 40 (not 20)
  -- (JOURNAL) quest log selection + abandon (QuestMapQuestOptions_AbandonQuest: GetSelectedQuest, SetSelectedQuest,
  -- SetAbandonQuest = the selected quest becomes the abandon target, GetAbandonQuest, AbandonQuest = abandons THE
  -- TARGET, then cleared); quest ids, no restriction in the documentation
  stub(QLx, "GetSelectedQuest", function() return M.selQuest or 0 end)
  stub(QLx, "SetSelectedQuest", function(id) M.selQuest = tonumber(id) or 0 end)
  stub(QLx, "SetAbandonQuest", function() M.abandonTarget = (qAt(M.selQuest) and M.selQuest) or 0 end)
  stub(QLx, "GetAbandonQuest", function() return M.abandonTarget or 0 end)
  stub(QLx, "GetAbandonQuestItems", function() return {} end)
  stub(QLx, "CanAbandonQuest", function(id) local q = qAt(id) return (q ~= nil and not q.noAbandon) and true or false end)
  stub(QLx, "AbandonQuest", function()
    local id = M.abandonTarget or 0
    M.abandonTarget = 0
    if id ~= 0 then abandonQuest(id) end
  end)
  stub(QLx, "IsOnQuest", function(id) return qAt(id) ~= nil end)
  stub(QLx, "AddQuestWatch", function() return true end)
  stub(QLx, "RemoveQuestWatch", function() return true end)
  local CM = G.C_Map
  stub(CM, "GetAreaInfo", function(id) return M.areaNames and M.areaNames[id] or nil end)
  stub(CM, "GetUserWaypoint", function() return M.userWaypoint end)
  stub(CM, "HasUserWaypoint", function() return M.userWaypoint ~= nil end)
  G.C_SuperTrack = G.C_SuperTrack or {}
  stub(G.C_SuperTrack, "IsSuperTrackingUserWaypoint", function() return M.superTrackUser and true or false end)
  stub(CI, "GetDetailedItemLevelInfo", function(i) local it = itemFromArg(i) if it then return it.ilvl, false, it.ilvl end end)
  stub(CI, "GetItemLinkByGUID", function(guid)
    local l = (M.itemGUIDs or {})[guid]
    return l and M.ItemLink(l.id) or nil
  end)
  stub(CI, "RequestLoadItemDataByID", function() end)
  -- (ITEMINFO) the GUID of a bag / equipped item (tooltip data) → ItemLocation mixin, its stack count and bound state.
  -- M.itemGUIDs[guid] = { id=, bag=, slot= } or { id=, equip=slot }, bound = true for a soulbound item
  stub(CI, "GetItemLocation", function(guid)
    local l = (M.itemGUIDs or {})[guid]
    if not l then return nil end
    return { _guid = guid,
      IsBagAndSlot = function() return l.bag ~= nil end, GetBagAndSlot = function() return l.bag, l.slot end,
      IsEquipmentSlot = function() return l.equip ~= nil end, GetEquipmentSlot = function() return l.equip end,
      IsValid = function() return true end }
  end)
  local function locOf(loc) return type(loc) == "table" and (M.itemGUIDs or {})[loc._guid] or nil end
  stub(CI, "GetStackCount", function(loc)
    local l = locOf(loc)
    if not l then return 0 end
    if l.bag then local s = bagSlot(l.bag, l.slot) return s and s.count or 0 end
    return 1
  end)
  stub(CI, "IsBound", function(loc) local l = locOf(loc) return (l and l.bound) and true or false end)
  stub(G, "GetLootSlotLink", function(i) local l = (M.loot or {})[i] return l and l.id and M.ItemLink(l.id) or nil end)
  G.C_AddOns = G.C_AddOns or {}
  stub(G.C_AddOns, "GetNumAddOns", function() return 1 end)
  stub(G.C_AddOns, "GetAddOnInfo", function(i)
    local name = M.addonName or "TreeAI"
    if i == 1 or i == name then return name, name, "", true, nil, "INSECURE" end
  end)
  G.C_MerchantFrame = G.C_MerchantFrame or {}
  stub(G.C_MerchantFrame, "GetItemInfo", function(i)
    local it = M.merchantOpen and (M.merchantItems or {})[i]
    if not it then return nil end
    local d = M.items[it.id] or {}
    return { name = d.name, texture = 134400 + it.id, price = it.price or 0, stackCount = it.stack or 1,
      numAvailable = -1, isPurchasable = true, isUsable = true, hasExtendedCost = false, currencyID = nil,
      spellID = nil, isQuestStartItem = false }
  end)
  stub(G.C_Minimap, "SetTracking", function(i, on) M.tracking = M.tracking or {} M.tracking[i] = on and true or false end)
  G.C_GossipInfo = G.C_GossipInfo or {}
  stub(G.C_GossipInfo, "GetOptions", function() return {} end)
  stub(G.C_GossipInfo, "GetActiveQuests", function() return {} end)
  stub(G.C_GossipInfo, "GetAvailableQuests", function() return {} end)
  stub(G.C_GossipInfo, "SelectOption", function() end)
  stub(G.C_GossipInfo, "SelectActiveQuest", function() end)
  stub(G.C_GossipInfo, "SelectAvailableQuest", function() end)
  G.C_MountJournal = G.C_MountJournal or {}
  stub(G.C_MountJournal, "GetMountIDs", function() return {} end)
  stub(G.C_MountJournal, "GetMountInfoByID", function() return nil end)
  G.C_UIWidgetManager = G.C_UIWidgetManager or {}
  stub(G.C_UIWidgetManager, "GetTopCenterWidgetSetID", function() return nil end)
  stub(G.C_UIWidgetManager, "GetAllWidgetsBySetID", function() return {} end)
  stub(G.C_UIWidgetManager, "GetIconAndTextWidgetVisualizationInfo", function() return nil end)
  stub(G.C_PvP, "GetBattlefieldFlagPosition", function() return nil end)
  stub(G.C_PvP, "GetScoreInfo", function() return nil end)
  G.C_TradeSkillUI = G.C_TradeSkillUI or {}
  stub(G.C_TradeSkillUI, "GetBaseProfessionInfo", function() return nil end)
  stub(G.C_TradeSkillUI, "GetRecipeInfo", function() return nil end)
  stub(G.C_TradeSkillUI, "GetRecipeSchematic", function() return nil end)
  stub(SB, "IsSpellBookItemPassive", function(slot, bank) local _, sp = slotSpell(slot, bank) return sp and sp.passive and true or false end)
  -- professions: GetProfessions / GetProfessionInfo (C, used by Blizzard_ProfessionsBook/Camelot: prof1, prof2,
  -- first aid, fishing, cooking — no archaeology on Forever), built from WowMock.skills; the Era skill lines are gone
  local LINES = { ["Travail du cuir"] = 165, Couture = 197, Forge = 164, Alchimie = 171, ["Ingénierie"] = 202,
    Enchantement = 333, Herboristerie = 182, Minage = 186, ["Dépeçage"] = 393, ["Premiers soins"] = 129,
    Secourisme = 129, ["Pêche"] = 356, Cuisine = 185 }
  local function profIndex(kind)
    local primary = 0
    for i, sk in ipairs(M.skills or {}) do
      local line = LINES[sk.name]
      if line == 129 or line == 356 or line == 185 then
        if (kind == "faid" and line == 129) or (kind == "fish" and line == 356) or (kind == "cook" and line == 185) then return i end
      elseif line then
        primary = primary + 1
        if (kind == "p1" and primary == 1) or (kind == "p2" and primary == 2) then return i end
      end
    end
  end
  stub(G, "GetProfessions", function() return profIndex("p1"), profIndex("p2"), profIndex("faid"), profIndex("fish"), profIndex("cook") end)
  stub(G, "GetProfessionInfo", function(i)
    local sk = (M.skills or {})[i]
    if not sk then return nil end
    return sk.name, 136243, sk.rank or 0, sk.max or 300, 1, 0, LINES[sk.name], 0, 0, 0, sk.name
  end)
  -- dual spec (Forever: level 40): GetNumSpecGroups (C, used by the camelot FrameXML) + C_SpecializationInfo
  stub(G, "GetNumSpecGroups", function() return M.numTalentGroups or 1 end)
  G.C_SpecializationInfo = G.C_SpecializationInfo or {}
  stub(G.C_SpecializationInfo, "GetActiveSpecGroup", function() return M.talentGroup or 1 end)
  -- quest log as on Forever: every documented QuestInfo field; a zone header entry before the seeded quests
  local QL = G.C_QuestLog
  if QL and QL.GetInfo then
    local getInfo = QL.GetInfo
    QL.GetInfo = function(i)
      local q = getInfo(i)
      if rawtype(q) ~= "table" then return q end
      local src = M.quests[i] or {}
      local full = { title = q.title or "", questLogIndex = i, questID = q.questID or 0, campaignID = nil,
        level = q.level or 0, difficultyLevel = q.level or 0, suggestedGroup = q.suggestedGroup or 0,
        frequency = src.frequency, isHeader = q.isHeader and true or false, useMinimalHeader = false,
        sortAsNormalQuest = false, isCollapsed = src.collapsed and true or false, startEvent = false,
        isTask = false, isBounty = false, isStory = false, isScaling = false, isOnMap = (not q.isHeader) and true or false,
        hasLocalPOI = false, isHidden = false, isAutoComplete = false, overridesSortOrder = false,
        readyForTranslation = true, isInternalOnly = false, isAbandonOnDisable = false, headerSortKey = nil,
        questClassification = 7 }   -- Enum.QuestClassification.Normal
      return full
    end
    QL.GetLogIndexForQuestID = QL.GetLogIndexForQuestID or function(id)
      for i, q in ipairs(M.quests) do if not q.header and (q.id or 1000 + i) == id then return i end end
    end
    QL.GetAllCompletedQuestIDs = QL.GetAllCompletedQuestIDs or function()
      local out = {}
      for id, v in pairs(M.completed or {}) do if v then out[#out + 1] = id end end
      return out
    end
    QL.IsQuestFlaggedCompleted = QL.IsQuestFlaggedCompleted or function(id) return (M.completed or {})[id] and true or false end
    QL.GetTitleForQuestID = QL.GetTitleForQuestID or function(id)
      for i, q in ipairs(M.quests) do if not q.header and (q.id or 1000 + i) == id then return q.title end end
    end
    QL.GetQuestWatchType = QL.GetQuestWatchType or function() return nil end
  end
  if not M.questHeadersSeeded and M.quests[1] and not M.quests[1].header then
    for i, q in ipairs(M.quests) do q.id = q.id or (1000 + i) end   -- real quest IDs do not move with the header
    table.insert(M.quests, 1, { header = M.player.zone })
    M.questHeadersSeeded = true
  end
  -- UnitPosition: the player and the group only (world yards: y, x, z, instanceID); never in an instance
  G.UnitPosition = function(u)
    if M.instance then return nil end
    local d, k = unitInfo(u)
    if k == "player" then
      local p = M.pos
      local r = p and M.mapRects[p.mapID]
      if not r then return nil end
      return r.top - p.y * r.h, r.left - p.x * r.w, 0, r.continent or 0
    end
    if (k == "group" or k == "pet") and d and d.pos then
      local r = M.mapRects[d.pos.mapID]
      if r then return r.top - d.pos.y * r.h, r.left - d.pos.x * r.w, 0, r.continent or 0 end
    end
    return nil
  end
  -- macro writes and key bindings: only inside a hardware event (a click / a typed command) — else
  -- ADDON_ACTION_FORBIDDEN (« UNKNOWN() »), the call does nothing
  for _, n in ipairs({ "CreateMacro", "EditMacro", "DeleteMacro" }) do
    local f = rawget(G, n)
    if rawtype(f) == "function" then
      G[n] = function(...)
        if not M.hardware then
          M.Violation("forbidden", n .. "() outside a hardware event (ADDON_ACTION_FORBIDDEN « UNKNOWN() »)")
          error("Interface action failed because of an AddOn", 2)
        end
        return f(...)
      end
    end
  end
  for _, n in ipairs({ "SetBinding", "SetBindingClick", "SetBindingMacro", "SetBindingSpell", "SetBindingItem", "SaveBindings" }) do
    local f = rawget(G, n)
    if rawtype(f) == "function" then
      G[n] = function(...)
        if not M.hardware then
          M.Violation("forbidden", n .. "() outside a hardware event (ADDON_ACTION_FORBIDDEN)")
          if M.FireEvent then M.FireEvent("ADDON_ACTION_FORBIDDEN", M.addonName or "TreeAI", n .. "()") end
          return false
        end
        return f(...)
      end
    end
  end
  -- nameplates: forbidden frames in instances
  local np = G.C_NamePlate
  if np and np.GetNamePlateForUnit then
    local gp = np.GetNamePlateForUnit
    np.GetNamePlateForUnit = function(u, ...) if M.instance then return nil end return gp(u, ...) end
  end
  -- Lua with secrets: type() = real type; tostring / tonumber / string.format / strjoin / strconcat accept them
  G.type = function(v) local t = rawtype(v) if t == "userdata" and secretKind[v] then return secretKind[v] end return t end
  G.tostring = function(v) if isSecret(v) then return wrapSecret(rawtostring(secretVal[v]), "string") end return rawtostring(v) end
  G.tonumber = function(v, base)
    if isSecret(v) then local n = rawtonumber(secretVal[v], base) if n == nil then return nil end return wrapSecret(n, "number") end
    return rawtonumber(v, base)
  end
  local function liftFormat(fmt, ...)
    local n = select("#", ...)
    local args, any = { ... }, isSecret(fmt)
    for i = 1, n do if isSecret(args[i]) then any = true args[i] = secretVal[args[i]] end end
    if not any then return nil end
    return wrapSecret(rawformat(unwrap(fmt), unpack(args, 1, n)), "string")
  end
  string.format = function(fmt, ...)
    local ok, r = rawpcall(rawformat, fmt, ...)
    if ok then return r end
    local s = liftFormat(fmt, ...)
    if s ~= nil then return s end
    error(r, 2)
  end
  G.format = string.format
  local function joinSecrets(sep, ...)
    local n, any = select("#", ...), false
    local parts = { ... }
    for i = 1, n do if isSecret(parts[i]) then any = true parts[i] = rawtostring(secretVal[parts[i]]) else parts[i] = rawtostring(parts[i]) end end
    local s = rawconcat(parts, sep or "", 1, n)
    if any then return wrapSecret(s, "string") end
    return s
  end
  G.strjoin = function(sep, ...) return joinSecrets(sep, ...) end
  string.join = G.strjoin
  G.strconcat = function(...) return joinSecrets("", ...) end
  -- table.concat is not documented as accepting secrets: a secret element is the client's error
  table.concat = function(t, sep, i, j)
    local ok, r = rawpcall(rawconcat, t, sep, i, j)
    if ok then return r end
    if rawtype(t) == "table" then
      for k = i or 1, j or #t do if isSecret(t[k]) then secretError("concatenate", t[k], 2) end end
    end
    error(r, 2)
  end
  -- pcall / xpcall: errors on secrets swallowed by the addon still count (the feature is dead in game)
  local function after(ok, ...)
    if not ok then
      local e = ...
      noteSwallowed(e)
      local txt = secretVmError(e)   -- the addon sees the client's wording, not Lua 5.1's « userdata »
      if txt then return false, txt end
    end
    return ok, ...
  end
  G.pcall = function(f, ...) return after(rawpcall(f, ...)) end
  G.xpcall = function(f, h, ...)
    local args, n = { ... }, select("#", ...)
    return rawxpcall(function() return f(unpack(args, 1, n)) end, function(e) noteSwallowed(e) return h and h(e) or e end)
  end
  -- print / chat frames take secrets (shown as « <secret> » here)
  local pr = G.print
  G.print = function(...)
    local n, o = select("#", ...), { ... }
    for i = 1, n do if isSecret(o[i]) then o[i] = "<secret>" end end
    return pr(unpack(o, 1, n))
  end
  M.secretBooleans = M.secretBooleans or {}
end

-- part 2, after InstallTaint: remove what Forever does not have; wrap what it restricts
function M.PruneForever(G)
  local ref = M.ref or M.LoadForeverRef()
  M.pruned = {}
  for k, v in pairs(G) do
    if rawtype(k) == "string" and rawtype(v) == "function" and not LUA_BUILTIN[k] and not KEEP[k]
        and not ref.api[k] and not ref.globals[k] then
      M.pruned[#M.pruned + 1] = k
    end
  end
  for _, k in ipairs(M.pruned) do rawset(G, k, nil) end
  for k, v in pairs(G) do
    if rawtype(k) == "string" and k:match("^C_") and rawtype(v) == "table" then
      if not ref.ns[k] then
        M.pruned[#M.pruned + 1] = k
      else
        local gone = {}
        for m, f in pairs(v) do
          if rawtype(f) == "function" and not ref.api[k .. "." .. m] and not ref.members[k .. "." .. m] then gone[#gone + 1] = m end
        end
        for _, m in ipairs(gone) do v[m] = nil M.pruned[#M.pruned + 1] = k .. "." .. m end
      end
    end
  end
  for _, k in ipairs(M.pruned) do if not k:find("%.") and k:match("^C_") then rawset(G, k, nil) end end
  table.sort(M.pruned)
  -- wrap documented flags: secret arguments refused, secret returns
  -- the secret-inspection builtins are documented « AllowedWhenUntainted » but take secrets from addons (that is
  -- their purpose: AutoTurnIn & co call issecretvalue on map positions in combat)
  local INSPECT = { issecretvalue = true, canaccessvalue = true, canaccesstable = true, issecrettable = true,
    canaccessallvalues = true, hasanysecretvalues = true, scrubsecretvalues = true, scrub = true, secretwrap = true }
  local function wrap(name, f, flags)
    local refuse = flags:find("A", 1, true) ~= nil and not INSPECT[name]
    local codes = flags:match("s:(%S+)")
    if INSPECT[name] then return f end
    return function(...)
      local n = select("#", ...)
      for i = 1, n do
        local a = select(i, ...)
        if isSecret(a) then
          if refuse then
            local msg = rawformat("bad argument #%d to '%s' (secret value: refused from addon code)", i, name)
            M.Violation("secret", msg)
            error(msg, 2)
          end
          -- accepted (« AllowedWhenTainted »): the client answers, with secrets
          local args = { ... }
          for j = 1, n do args[j] = unwrap(args[j]) end
          return secretReturns(name, f(unpack(args, 1, n)))
        end
      end
      if codes and secretNow(codes, ...) then
        return secretReturns(name, f(...))
      end
      return f(...)
    end
  end
  for k, v in pairs(G) do
    if rawtype(v) == "function" and ref.api[k] then G[k] = wrap(k, v, ref.api[k]) end
  end
  for k, v in pairs(G) do
    if rawtype(k) == "string" and k:match("^C_") and rawtype(v) == "table" then
      for m, f in pairs(v) do
        local fl = ref.api[k .. "." .. m]
        if rawtype(f) == "function" and fl then v[m] = wrap(k .. "." .. m, f, fl) end
      end
    end
  end
end

-- a secret stored as a table KEY (an immediate error in the real client): scan what the addon can reach
function M.ScanSecretKeys(roots)
  local seen, found = {}, {}
  local stack = {}
  for _, r in ipairs(roots) do stack[#stack + 1] = { r, "root" } end
  local n = 0
  while #stack > 0 and n < 200000 do
    local item = table.remove(stack)
    local t, path = item[1], item[2]
    if rawtype(t) == "table" and not seen[t] and not rawget(t, "__kind") then
      seen[t] = true
      n = n + 1
      for k, v in next, t do
        if isSecret(k) then found[#found + 1] = path .. "[<secret>]" end
        if rawtype(v) == "table" then stack[#stack + 1] = { v, path .. "." .. rawtostring(k) } end
      end
    elseif rawtype(t) == "function" and not seen[t] then
      seen[t] = true
      local i = 1
      while true do
        local nm, v = debug.getupvalue(t, i)
        if not nm then break end
        if rawtype(v) == "table" or rawtype(v) == "function" then stack[#stack + 1] = { v, path .. "^" .. nm } end
        i = i + 1
      end
    end
  end
  return found
end

return M
