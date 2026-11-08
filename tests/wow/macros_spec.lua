-- Lua 5.1 spec for Modules/Macros.lua with a tiny self-contained WoW mock (agent C).
-- Usage: lua5.1 tests/wow/macros_spec.lua <addon_dir>   (prints "OK" or errors)
local dir = arg[1] or "wow_addon/TreeAI"

-- ---------------------------------------------------------------- mock game
local known = { [75]=true, [3044]=true, [1978]=true, [1130]=true, [5116]=true, [2973]=true,
  [2974]=true, [1495]=true, [13163]=true, [13165]=true, [883]=true, [982]=true, [136]=true,
  [6991]=true, [13795]=true, [1499]=true, [3045]=true, [5118]=true, [3043]=true }
local spellNames = { [75]="Tir automatique", [3044]="Tir des arcanes", [3045]="Tir rapide" }
local bags = { [1]={117, 20}, [2]={2287, 5}, [3]={159, 10}, [4]={858, 3}, [5]={2512, 200},
  [6]={1251, 4}, [7]={4592, 3} }
local macros, inCombat, level = {}, false, 26

GetSpellInfo = function(id)
  local D = TreeAI and TreeAI.Data and TreeAI.Data.Macros
  local function nm(sid) return spellNames[sid] or (D and D.spells[sid]) end
  if type(id) == "number" then return nm(id) end
  for sid in pairs(known) do if nm(sid) == id then return id end end
  return nil
end
IsPlayerSpell = function(id) return known[id] == true end
UnitLevel = function(u) return u == "pet" and 25 or level end
UnitClass = function() return "Chasseur", "HUNTER" end
InCombatLockdown = function() return inCombat end
GetContainerNumSlots = function(bag) return bag == 0 and #bags or 0 end
GetContainerItemID = function(bag, slot) return bag == 0 and bags[slot] and bags[slot][1] or nil end
GetContainerItemInfo = function(bag, slot)
  local b = bag == 0 and bags[slot]
  if not b then return nil end
  return "icon", b[2], false, 1, nil, nil, "|Hitem:" .. b[1] .. "|h", nil, false, b[1]
end
GetItemInfo = function(id)
  if id == 4444 then return "Arc", nil, 1, 20, 15, nil, nil, 1, nil, nil, 1, 2, 2 end
  return nil
end
GetItemSpell = function() return nil end
GetInventoryItemID = function(_, slot) return slot == 18 and 4444 or nil end
GetPetFoodTypes = function() return "Viande", "Poisson" end
GetNumMacros = function() local n = 0 for _ in pairs(macros) do n = n + 1 end return 0, n end
GetMacroIndexByName = function(name) return macros[name] and macros[name].idx or 0 end
GetMacroInfo = function(idx)
  for name, m in pairs(macros) do if m.idx == idx then return name, m.icon, m.body end end
end
local nextIdx = 121
CreateMacro = function(name, icon, body, perChar)
  assert(not inCombat, "CreateMacro in combat")
  assert(#body <= 255, "body too long: " .. name)
  assert(#name <= 16, "name too long")
  macros[name] = { idx = nextIdx, icon = icon, body = body, perChar = perChar }
  nextIdx = nextIdx + 1
  return nextIdx - 1
end
EditMacro = function(idx, name, icon, body)
  assert(not inCombat, "EditMacro in combat")
  for n, m in pairs(macros) do if m.idx == idx then m.body = body end end
end
DeleteMacro = function(idx) for n, m in pairs(macros) do if m.idx == idx then macros[n] = nil end end end
NUM_BAG_SLOTS = 0

-- ---------------------------------------------------------------- mock core (contract of agent B)
local events, ooc, suggestions = {}, {}, {}
TreeAI = { Data = {}, modules = {} }
function TreeAI:RegisterModule(name, tbl)
  tbl.name = name
  tbl.db = {}; for k, v in pairs(tbl.defaults or {}) do tbl.db[k] = v end
  tbl.char = {}; for k, v in pairs(tbl.charDefaults or {}) do tbl.char[k] = v end
  function tbl:RegisterEvent(e, fn) events[e] = fn end
  function tbl:Suggest(id, text) suggestions[id] = text end
  function tbl:Clear(id) suggestions[id] = nil end
  function tbl:Print() end
  self.modules[name] = tbl
  return tbl
end
function TreeAI:GetModule(n) return self.modules[n] end
function TreeAI:InCombat() return inCombat end
function TreeAI:RunOutOfCombat(key, fn) if inCombat then ooc[key] = fn else fn() end end
function TreeAI:After(_, fn) fn() end
function TreeAI:PlayerClass() return "HUNTER" end
function TreeAI:RegisterSlash() end

local before = {}
for k in pairs(_G) do before[k] = true end

dofile(dir .. "/Data/Macros.lua")
dofile(dir .. "/Modules/Macros.lua")

for k in pairs(_G) do
  assert(before[k], "global leak: " .. tostring(k))
end

local M = TreeAI.modules.Macros
assert(M and TreeAI.Data.Macros, "module/data missing")

-- context: best items
local ctx = M.NewContext()
assert(ctx.item("food") == 2287, "best food (level 26 has 117 and 2287) -> " .. tostring(ctx.item("food")))
assert(ctx.item("water") == 159, "water")
assert(ctx.item("hpot") == 858, "hpot")
assert(ctx.item("ammo") == 2512, "ammo arrows for bow")
assert(ctx.item("bandage") == 1251, "bandage")
assert(ctx.item("hs") == nil, "no healthstone")
-- pet level 25 -> lowest liked food with diet meat/fish: ilvl >= 15 -> 2287 (req 5, ilvl 15) or 4592
local pf = ctx.item("petfood")
assert(pf == 2287 or pf == 4592, "petfood " .. tostring(pf))

M:OnEnable()
assert(suggestions.ready == "Nouvelles macros prêtes : clique pour installer", "one macro line")

-- install in combat is refused (nothing queued: a later run would be outside the player's click)
inCombat = true
M:Install(false)
assert(next(macros) == nil, "no macro in combat")
assert(ooc["Macros:install"] == nil, "nothing queued for after combat")
inCombat = false
M:Install(false)

local tir = macros["TAI Tir"]
assert(tir, "TAI Tir created")
assert(tir.body:find("/cast !Tir automatique", 1, true), tir.body)
assert(tir.body:find("Tir des arcanes", 1, true), tir.body)
assert(not tir.body:find("[mod:shift]", 1, true), "multi-shot unknown -> clause dropped: " .. tir.body)
local fam = macros["TAI Familier"].body
assert(fam:find("item:", 1, true), "pet food in Familier: " .. fam)
assert(macros["TAI Repas"].body:find("item:2287", 1, true), macros["TAI Repas"].body)
assert(macros["TAI Survie"].body:find("item:858", 1, true), macros["TAI Survie"].body)
assert(macros["TAI Munitions"].body:find("/equip item:2512", 1, true))
local pieges = macros["TAI Pieges"].body
assert(pieges:find("Piège givrant", 1, true) and not pieges:find("Piège de givre", 1, true), pieges)
assert(macros["TAI FD Piege"] == nil, "FD not known yet")
assert(macros["TAI Marqueur"] == nil, "non default not installed")
for name, m in pairs(macros) do
  assert(not m.body:find("[{}]"), "unresolved token in " .. name .. ": " .. m.body)
  assert(not m.body:find("?", 1, true), "marker left in " .. name)
end

-- bag change: better food appears -> consumable macro updated
bags[8] = { 3770, 2 }
events.BAG_UPDATE_DELAYED(M, "BAG_UPDATE_DELAYED")
assert(macros["TAI Repas"].body:find("item:3770", 1, true), macros["TAI Repas"].body)

-- user-edited macro is not overwritten
macros["TAI Survie"].body = "/say perso"
bags[4] = { 929, 1 }
events.BAG_UPDATE_DELAYED(M, "BAG_UPDATE_DELAYED")
assert(macros["TAI Survie"].body == "/say perso", "user edit kept")

-- learning Multi-Shot upgrades TAI Tir
known[2643] = true
events.SPELLS_CHANGED(M, "SPELLS_CHANGED")
assert(macros["TAI Tir"].body:find("[mod:shift] Flèches multiples", 1, true), macros["TAI Tir"].body)

-- empty bags: consumable macro gets a placeholder, never an empty /use
bags = {}
events.BAG_UPDATE_DELAYED(M, "BAG_UPDATE_DELAYED")
assert(macros["TAI Repas"].body:find("^/run print"), macros["TAI Repas"].body)

-- spec: hunter without talents -> leveling spec "bete"; Tir uses the BM variant (hawk unknown -> arcane)
assert(M:Spec() == "bete", "leveling spec " .. tostring(M:Spec()))
-- talents: 31 points in Marksmanship (classic return shape: name, icon, points)
GetTalentTabInfo = function(i) return "Tab" .. i, "icon", (i == 2) and 31 or 0 end
assert(M:Spec() == "precision", "talent spec " .. tostring(M:Spec()))
-- learning Aimed Shot (MM talent) -> Ctrl clause appears after a spell refresh
known[19434] = true
events.SPELLS_CHANGED(M, "SPELLS_CHANGED")
assert(macros["TAI Tir"].body:find("[mod:ctrl] Visée", 1, true), macros["TAI Tir"].body)
assert(macros["TAI Tir"].body:find("^#showtooltip\n/cast "), "main shot first for #showtooltip")
-- player's own macro with a TAI name is never overwritten
macros["TAI Pet"] = { idx = 999, icon = "x", body = "/petattack" }
local function def(name)
  for _, d in ipairs(M:Defs()) do if d.n == name then return d end end
end
local defPet = def("TAI Pet")
assert(defPet and defPet.w and defPet.w ~= "", "why text exported")
assert(M:InstallOne(defPet) == "user" and macros["TAI Pet"].body == "/petattack", "foreign macro kept")
assert(M:InstallOne(defPet, true) == "updated", "forced")
-- trinket slot lines appear only with an on-use trinket
GetInventoryItemID = function(_, slot) if slot == 18 then return 4444 end if slot == 13 then return 7777 end end
GetItemSpell = function(id) if id == 7777 then return "Utiliser" end end
local burst = M:Render(def("TAI Burst"))
assert(burst and burst:find("/use 13", 1, true) and not burst:find("/use 14", 1, true), tostring(burst))
-- Forever: writes outside a player action are deferred to a click
TreeAI.InUserAction = function() return false end
assert(M:Write(def("TAI Marque"), "/cast X") == "pending" and suggestions.ready, "pending outside action")
TreeAI.InUserAction = nil

-- panel: list + preview + « pourquoi » + buttons (permissive frame mock: methods are no-ops)
local function anyFrame()
  local o = { scripts = {} }
  return setmetatable(o, { __index = function(_, k)
    if type(k) ~= "string" or not k:find("^%u") then return nil end
    if k == "SetScript" then return function(self, n, fn) self.scripts[n] = fn end end
    if k == "SetText" then return function(self, x) self.txt = x end end
    if k == "IsShown" then return function(self) return self.shown ~= false end end
    if k == "Hide" then return function(self) self.shown = false end end
    if k == "Show" then return function(self) self.shown = true end end
    if k:find("^Create") then return function() return anyFrame() end end
    return function() end
  end })
end
CreateFrame = function() return anyFrame() end
UIParent = anyFrame()
M:TogglePanel()
local P = M.panel
assert(P and not P.rows[1].def and P.rows[1].text.txt:find("Installées", 1, true), "group header first")
assert(P.rows[2].def, "panel rows")
P.rows[3].scripts.OnClick(P.rows[3])
assert(M.selected == P.rows[3].def.n, "row selects")
assert(P.where.txt and P.where.txt:find("Où la mettre", 1, true), "where shown")
assert(P.why.txt and #P.why.txt > 10, "why shown")
assert(P.body.txt and P.body.txt ~= "", "preview shown")
assert(P.specBtn.txt:find("Spé", 1, true), P.specBtn.txt)
P.specBtn.scripts.OnClick(P.specBtn)
assert(P.specBtn.txt:find("Maîtrise des bêtes", 1, true), P.specBtn.txt)
P.ctxBtn.scripts.OnClick(P.ctxBtn)
assert(P.ctxBtn.txt == "Solo", P.ctxBtn.txt)
local userCalls = 0
TreeAI.UserAction = function(_, fn, ...) userCalls = userCalls + 1; return pcall(fn, ...) end
P.one.scripts.OnClick(P.one)
P.all.scripts.OnClick(P.all)
assert(userCalls == 2, "panel buttons go through TreeAI:UserAction")
M:TogglePanel()
assert(P.shown == false, "toggle hides")

M:Remove()
for name in pairs(macros) do
  assert(name == "TAI Survie", "only the user-edited macro survives: " .. name)
end
print("OK")
