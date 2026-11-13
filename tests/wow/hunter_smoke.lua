-- Test de fumée autonome (wow-D) : Hunter.lua + Classes.lua sur un mini-mock de l'API WoW.
-- Usage : lua5.1 tests/wow/hunter_smoke.lua <racine du dépôt>
local root = arg[1] or "."
local frames, said = {}, {}
local function noop() end
local function obj()
  local o = { scripts = {}, events = {} }
  return setmetatable(o, { __index = function(t, k)
    if k == "SetScript" then return function(s, n, f) s.scripts[n] = f end end
    if k == "RegisterEvent" then return function(s, e) s.events[e] = true end end
    if k == "CreateTexture" or k == "CreateFontString" then return function() return obj() end end
    if k == "GetPoint" then return function() return "CENTER", nil, "CENTER", 0, 0 end end
    return noop
  end })
end
CreateFrame = function() local f = obj(); frames[#frames + 1] = f; return f end
UIParent = obj()
local T = 100
GetTime = function() return T end
time = os.time
bit = { band = function(a, b) return (math.floor(a / b) % 2 == 1) and b or 0 end }

-- État simulé
local S = { class = "HUNTER", combat = false, pet = true, happy = 2, loyalty = 1, petLevel = 26,
  bag = { [0] = { 3770, 3771, 2515 } }, ammoID = 2515, ammoCount = 40, ranged = 2504, buffs = {},
  known = { [75] = true, [883] = true, [6991] = true, [13165] = true, [1130] = true, [1499] = true, [5118] = true } }
UnitClass = function() return "Chasseur", S.class end
UnitExists = function(u) if u == "pet" then return S.pet end return u == "player" or u == "target" end
UnitLevel = function(u) return u == "pet" and S.petLevel or 26 end
UnitHealth = function(u) return u == "pet" and 20 or 100 end
UnitHealthMax = function() return 100 end
UnitIsDead = function() return false end
UnitIsDeadOrGhost = function() return false end
UnitAffectingCombat = function() return S.combat end
InCombatLockdown = function() return S.combat end
UnitGUID = function(u) return "GUID-" .. u end
UnitName = function() return "Fakehunt" end
GetRealmName = function() return "Fakerealm" end
UnitCanAttack = function() return true end
UnitClassification = function() return "elite" end
UnitCreatureFamily = function() return "Loup" end
IsLoggedIn = function() return false end
GetPetHappiness = function() return S.happy, 100, S.loyalty end
GetPetFoodTypes = function() return "Viande", "Poisson" end
GetPetTrainingPoints = function() return 120, 100 end
IsPlayerSpell = function(id) return S.known[id] or false end
local NAMES = { [75] = "Tir automatique", [6991] = "Nourrir le familier", [13165] = "Aspect du faucon",
  [5118] = "Aspect du guépard", [1130] = "Marque du chasseur", [1499] = "Piège givrant", [883] = "Appel du familier",
  [136] = "Guérison du familier", [1539] = "Effet Nourrir le familier", [6673] = "Cri de guerre" }
GetSpellInfo = function(id) return NAMES[id] or ("Sort" .. tostring(id)) end
UnitAura = function(u, i)
  local list = S.buffs[u] or {}
  local b = list[i]; if not b then return nil end
  return NAMES[b], nil, 0, nil, 0, 0, "player", nil, nil, b
end
GetContainerNumSlots = function(b) return S.bag[b] and #S.bag[b] or 0 end
GetContainerItemID = function(b, s) return S.bag[b] and S.bag[b][s] end
GetItemCount = function(id) return id == S.ammoID and S.ammoCount or 1 end
GetItemInfo = function(id) return "Objet" .. id end
GetItemInfoInstant = function(id)
  if id == 2504 then return id, "Arme", "Arc", "", 0, 2, 2 end
  if id == 2515 then return id, "Projectile", "Flèche", "", 0, 6, 2 end
  return id, "Conso", "", "", 0, 0, 0
end
GetInventoryItemID = function(_, slot) if slot == 18 then return S.ranged elseif slot == 0 then return S.ammoID end end
GetInventoryItemCount = function(_, slot) return slot == 0 and S.ammoCount or 0 end
ContainerIDToInventoryID = function(b) return 19 + b end
GetSpellCooldown = function() return 0, 0 end
local macros = {}
GetMacroIndexByName = function(n) return macros[n] and 1 or 0 end
GetNumMacros = function() return 0, 0 end
CreateMacro = function(n, _, body) macros[n] = body; return 1 end
EditMacro = function(_, n, _, body) macros[n] = body end
UnitRangedDamage = function() return 2.5 end
GetUnitSpeed = function() return 0 end
CombatLogGetCurrentEventInfo = nil

TreeAI = { mods = {} }
local tabs = {}
function TreeAI:Suggest(id, text, prio) said[#said + 1] = { id = id, text = text, prio = prio } end
function TreeAI:SetTabLines(id, lines) tabs[id] = lines end
local function tabHas(id, pat)
  for _, l in ipairs(tabs[id] or {}) do if tostring(l.text):find(pat) then return true end end
  return false
end
-- Mini-cœur conforme au contrat de wow_addon/README.md (M.db, M:Suggest préfixé, classes, OnEnable).
local summary = {}
function TreeAI:Summary() return summary end
function TreeAI:RegisterModule(name, tbl)
  TreeAIDB.modules = TreeAIDB.modules or {}
  TreeAIDB.modules[name] = TreeAIDB.modules[name] or {}
  tbl.db = TreeAIDB.modules[name]
  for k, v in pairs(tbl.defaults or {}) do if tbl.db[k] == nil then tbl.db[k] = v end end
  function tbl:Suggest(id, text, prio, o) return TreeAI:Suggest(name .. ":" .. id, text, prio, o) end
  function tbl:Clear() end
  function tbl:On(msg, fn) TreeAI.msgs[msg] = fn end
  self.mods[name] = tbl
  return tbl
end
TreeAI.msgs = {}
local login = CreateFrame("Frame")
login:RegisterEvent("PLAYER_LOGIN")
login:SetScript("OnEvent", function()
  for _, m in pairs(TreeAI.mods) do
    if not m.classes or m.classes[S.class] then m:OnEnable() end
  end
end)
TreeAIDB = { modules = { Hunter = { tab = true, pullTips = true, bar = true, trapBar = true } } }   -- (5.1 CLEAN) options coupées d'office, testées ici
function TreeAI:Debug(m) print("DEBUG", m) end

local function fire(ev, ...)
  for _, f in ipairs(frames) do
    if f.events[ev] and f.scripts.OnEvent then f.scripts.OnEvent(f, ev, ...) end
  end
end
local function find(id)
  for i = #said, 1, -1 do if said[i].id == id then return said[i] end end
end
local function check(c, msg) if not c then print("ECHEC: " .. msg); for _, s in ipairs(said) do print(s.id, s.prio, s.text) end; os.exit(1) end end

dofile(root .. "/wow_addon/TreeAI/Modules/Hunter.lua")
dofile(root .. "/wow_addon/TreeAI/Modules/Classes.lua")
check(TreeAI.mods.Hunter and TreeAI.mods.Classes, "modules enregistrés")
fire("PLAYER_LOGIN")
fire("PLAYER_ENTERING_WORLD")
check(find("Hunter:hungry") and find("Hunter:hungry").text:find("Objet3771"), "nourriture 25 choisie")
check(next(macros) == nil, "aucune macro écrite par le module (TAI Familier = panneau Macros)")
check(find("Hunter:ammo") and find("Hunter:ammo").prio == 3, "munitions critiques")
check(tabHas("hunter", "carquois"), "carquois dans l'onglet Chasseur")
check(tabHas("hunter", "Nourriture : Objet3771"), "fiche : nourriture")
check(find("Hunter:tp") and find("Hunter:tp").text:find("20 points"), "points de dressage libres = total - utilisés")
-- avant le pull : élite ciblé hors combat -> marque
fire("PLAYER_TARGET_CHANGED")
check(find("Hunter:mark"), "marque avant un élite (hors combat)")
-- Combat avec aspect du guépard
S.combat, S.buffs.player = true, { 5118 }
T = T + 100
fire("PLAYER_REGEN_DISABLED")
check(find("Hunter:aspect") and find("Hunter:aspect").prio == 3, "guépard en combat")
S.bag[0] = { 4599, 2515 }
fire("BAG_UPDATE_DELAYED")
check(not find("Hunter:pethp"), "pas de PV du familier en combat (valeur secrète sur Forever)")
-- Tir auto + barre
fire("START_AUTOREPEAT_SPELL")
fire("UNIT_SPELLCAST_SUCCEEDED", "player", "x", 75)
for _, f in ipairs(frames) do if f.scripts.OnUpdate then f.scripts.OnUpdate(f, 0.3) end end
-- Instantané
fire("PLAYER_LOGOUT")
check(TreeAI.msgs.SUMMARY, "accroche SUMMARY")
local snap = summary.hunter
check(snap and snap.pet and snap.pet.food == 4599 and snap.ammo.kind == "arrow", "instantané")
-- Classes : guerrier sans cri en combat
said = {}
local W = TreeAI.Classes
W.enabled, W.class = nil, nil
S.class, S.known, S.buffs.player = "WARRIOR", { [6673] = true }, {}
W:Enable()
W:Check()
check(find("Classes:WARRIOR.shout"), "cri de guerre manquant")
print("OK")
