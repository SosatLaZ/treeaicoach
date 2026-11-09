-- Mock run of TreeAI Lite's WeakAuras custom code (lua5.1 tests/walite/mock_run.lua <repo root>).
-- Emulates the bits of WeakAuras used (aura_env, TSU allstates, WeakAuras.ScanEvents, C_Timer) and a tiny
-- Forever-like API (C_QuestLog / C_Map world positions, secret values as a marker table).
local root = arg[1] or "."
local function read(p) local f = assert(io.open(root .. "/walite/" .. p)); local s = f:read("*a"); f:close(); return s end
local SECRET = setmetatable({}, { __tostring = function() return "<secret>" end })
issecretvalue = function(v) return v == SECRET end
local ticks = 0
C_Timer = { NewTicker = function(_, fn) ticks = ticks + 1; return { fn = fn } end }
local scanned = {}
WeakAuras = { ScanEvents = function(e) scanned[#scanned + 1] = e end }
CreateVector2D = function(x, y) return { x = x, y = y, GetXY = function(v) return v.x, v.y end } end
-- map 1429 (Elwynn-like) is 3000 x 2000 yd, continent 0; world n = -y*2000, w = -x*3000
local P = { map = 1429, x = 0.50, y = 0.50, facing = 0, combat = false, instance = false }
C_Map = {
  GetBestMapForUnit = function() return P.map end,
  GetPlayerMapPosition = function(m) return CreateVector2D(P.x, P.y) end,
  GetWorldPosFromMapPos = function(m, v)
    if m == 9999 then return SECRET, CreateVector2D(0, 0) end
    return 0, CreateVector2D(-v.y * 2000, -v.x * 3000)
  end,
}
GetPlayerFacing = function() return P.facing end
IsInInstance = function() return P.instance end
InCombatLockdown = function() return P.combat end
local LOG = {
  { questID = 1, title = "Loups affamés", done = true, wp = { 1429, 0.60, 0.50 } },
  { isHeader = true, title = "Elwynn" },
  { questID = 2, title = "Runes", done = false, wp = { 1429, 0.50, 0.40 }, obj = { { text = "Rune gravée : 5/10", finished = false } } },
  { questID = 3, title = "Courrier", done = true, wp = { 1429, 0.52, 0.50 } },
  { questID = 4, title = "Sans lieu", done = true },
  { questID = 5, title = "Loin", done = false, wp = { 1429, 0.90, 0.90 }, obj = { { text = "Ours : 0/8", finished = false } } },
  { questID = 6, title = "Secret", done = false, wp = { 9999, 0.5, 0.5 }, obj = { { text = "x", finished = false } } },
}
local byId = {}
for _, q in ipairs(LOG) do if q.questID then byId[q.questID] = q end end
C_QuestLog = {
  GetNumQuestLogEntries = function() return #LOG end,
  GetInfo = function(i) local q = LOG[i]; return q and { questID = q.questID, title = q.title, isHeader = q.isHeader } end,
  ReadyForTurnIn = function(id) return byId[id].done end,
  IsComplete = function(id) return byId[id].done end,
  GetQuestObjectives = function(id) return byId[id].obj or {} end,
  GetNextWaypoint = function(id) local w = byId[id].wp; if w then return w[1], w[2], w[3] end end,
  GetQuestsOnMap = function() return {} end,
}
-- hunter
local H = { ranged = 2504, ammo = 2515, count = 120, pet = true, happy = 1 }
GetInventoryItemID = function(_, slot) if slot == 18 then return H.ranged elseif slot == 0 then return H.ammo end end
GetInventoryItemCount = function() return H.count end
C_Item = { GetItemInfoInstant = function(id)
  if id == 2504 then return id, "Arme", "Arc", "", 0, 2, 2 end
  return id, "Projectile", "Flèche", "", 0, 6, 2 end }
UnitExists = function(u) return u == "pet" and H.pet end
UnitIsDead = function() return false end
C_PetInfo = { GetPetHappiness = function() return H.happy, 75, -1 end,
  GetPetFoodTypes = function() return { "Viande", "Poisson" } end }

local function aura(initFile, trigFile)
  local env = {}
  aura_env = env
  if initFile then assert(loadstring(read(initFile), initFile))() end
  local fn = assert(loadstring("return " .. read(trigFile), trigFile))()
  local states = {}
  return function(event)
    aura_env = env
    local changed = fn(states, event)
    return changed, states[""] and states[""].show and states[""].name or nil, env
  end
end

local fails = 0
local function check(cond, msg) if not cond then fails = fails + 1; print("FAIL: " .. msg) end end

local quests = aura("quest_init.lua", "quest_trigger.lua")
check(ticks == 1, "one 1 Hz ticker")
local ch, txt = quests("PLAYER_ENTERING_WORLD")
print(txt)
check(ch and txt, "quest text shown")
check(txt:find("devant · 183 m · Rune gravée : 5/10", 1, true), "nearest objective, direction + metres")
check(txt:find("À rendre (3)", 1, true), "3 to turn in")
local i3, i1 = txt:find("1. Courrier", 1, true), txt:find("2. Loups affamés", 1, true)
check(i3 and i1, "turn-in order: nearest first")
check(txt:find("Sans lieu (lieu inconnu)", 1, true), "unknown place listed")
check(not txt:find("Secret", 1, true), "secret position ignored")
ch = quests("TREEAI_LITE_TICK")
check(ch == false, "unchanged text: no update")
P.facing = math.pi / 2  -- facing west: north objective is on the right
ch, txt = quests("TREEAI_LITE_TICK")
check(ch and txt:find("à droite", 1, true), "relative direction follows facing")
P.combat = true
check(quests("TREEAI_LITE_TICK") == false, "no work in combat")
P.combat = false
P.instance = true
ch, txt = quests("QUEST_LOG_UPDATE")
check(txt and not txt:find("Rune", 1, true) and txt:find("lieu inconnu", 1, true), "instance: no positions, list kept")
P.instance = false

local hunter = aura(nil, "hunter_trigger.lua")
ch, txt = hunter("UNIT_HAPPINESS")
print(txt)
check(txt and txt:find("Munitions basses : 120 flèches", 1, true), "ammo low warning")
check(txt:find("Familier mécontent", 1, true) and txt:find("Viande, Poisson", 1, true), "pet food reminder")
H.count, H.happy = 1000, 3
ch, txt = hunter("BAG_UPDATE_DELAYED")
check(ch and txt == nil, "all good: hidden")
H.count, H.happy = SECRET, SECRET
ch, txt = hunter("BAG_UPDATE_DELAYED")
check(txt == nil, "secret values: nothing claimed, no crash")
H.ammo = nil
ch, txt = hunter("UNIT_INVENTORY_CHANGED")
check(txt and txt:find("emplacement vide", 1, true), "empty ammo slot")

if fails > 0 then os.exit(1) end
print("MOCK OK")
