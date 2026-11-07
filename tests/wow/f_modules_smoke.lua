-- Smoke test of the Leveling / Group / Inventory addon modules (agent F) against a tiny WoW mock.
-- Usage: lua5.1 f_modules_smoke.lua <Modules dir> [path to Core.lua]
local dir = arg[1]
local frames, sugg, chat, sent, used = {}, {}, {}, {}, {}
local timers = {}
local inCombat = false
function GetTime() return os.clock()*1000 end
function time() return os.time() end
C_Timer = { After = function(s, fn) timers[#timers+1] = fn end, NewTicker = function() return {} end }
local function runTimers() for i=1,5 do local t = timers; timers = {}; for _,f in ipairs(t) do f() end end end
local function mkframe()
  local f = { ev = {}, scripts = {}, shown = true }
  function f:RegisterEvent(e) self.ev[e] = true end
  function f:SetScript(k, fn) self.scripts[k] = fn end
  function f:SetSize() end function f:SetPoint() end
  function f:SetText(t) self.text = t end function f:Enable() self.enabled = true end function f:Disable() self.enabled = false end
  function f:Show() self.shown = true end function f:Hide() self.shown = false end function f:IsShown() return self.shown end
  frames[#frames+1] = f
  return f
end
function CreateFrame() return mkframe() end
MerchantFrame = mkframe()
DEFAULT_CHAT_FRAME = { AddMessage = function(_, m) chat[#chat+1] = m end }
function InCombatLockdown() return inCombat end
function UnitAffectingCombat() return inCombat end
function UnitName(u) if u == "player" then return "Testeur" end if u=="party1" then return "Amie" end end
function GetUnitName(u) return UnitName(u) end
function UnitExists(u) return u == "party1" end
function GetRealmName() return "Royaume" end
function UnitLevel() return 26 end
function UnitClass() return "Chasseur", "HUNTER" end
function UnitRace() return "Nain", "Dwarf" end
function UnitFactionGroup() return "Alliance" end
function UnitXP() return 1000 end function UnitXPMax() return 20000 end
function GetXPExhaustion() return 8000 end
function IsResting() return false end
function UnitCharacterPoints() return 1 end
function GetMoney() return 123456 end
function GetRealZoneText() return "Contreforts de Hautebrande" end
function GetSubZoneText() return "Champs de Hautebrande" end
function IsInGroup() return true end function IsInRaid() return false end function IsInInstance() return false end
function Ambiguate(n) return (n:gsub("%-.*","")) end
function UnitPosition() return 100, 100, 0, 0 end
C_Map = { GetBestMapForUnit = function() return 1424 end }
C_ChatInfo = { RegisterAddonMessagePrefix = function() end, SendAddonMessage = function(p, m, c) sent[#sent+1] = m end }
function GetNumQuestLogEntries() return 2 end
function GetQuestLogTitle(i) if i==1 then return "Sangliers", 25, 0, false, false, nil, 0, 500 end return "Ogres", 31, 0, false, false, nil, 0, 501 end
function GetNumQuestLeaderBoards() return 1 end
function GetQuestLogLeaderBoard(j, i) return "Sanglier tué : 3/8", "monster", false end
function GetNumSkillLines() return 2 end
function GetSkillLineInfo(i) if i==1 then return "Arbalètes", false, nil, 100, 0, 0, 130 end return "Dépeçage", false, nil, 80, 0,0,150 end
local bags = { [0] = { {q=0, link="|Hitem:1|h[Os]|h", c=2}, {q=2, link="|Hitem:2|h[Vert]|h", c=1}, false, false } }
C_Container = {
  GetContainerNumSlots = function(b) return bags[b] and #bags[b] or 0 end,
  GetContainerNumFreeSlots = function(b) local n=0; for _,v in ipairs(bags[b] or {}) do if not v then n=n+1 end end; return n, 0 end,
  GetContainerItemInfo = function(b, s) local v = bags[b] and bags[b][s]; if not v then return nil end return {quality=v.q, hyperlink=v.link, stackCount=v.c, itemID=1} end,
  UseContainerItem = function(b, s) used[#used+1] = b..":"..s end,
}
function GetItemInfo(l) return "Objet", l, 0, 1, 1, "x", "y", 1, "", 0, 25, 7 end
function GetInventoryItemDurability(s) if s==1 then return 10, 100 end end
function CanMerchantRepair() return true end function GetRepairAllCost() return 5000 end
LOOT_ITEM_SELF = "Vous recevez le butin : %s."
LOOT_ITEM_SELF_MULTIPLE = "Vous recevez le butin : %sx%d."
SlashCmdList = {}
TreeAI = { mods = {}, RegisterModule = function(self, n, t) self.mods[n] = t end,
  Suggest = function(self, id, text, prio) sugg[#sugg+1] = id .. " [" .. prio .. "] " .. text end }
TreeAIDB = {}
local ns = {}
if arg[2] then
  TreeAI = nil
  setmetatable(_G, {__index = function(t, k) return nil end})
  assert(loadfile(arg[2]))("TreeAI", ns)
  local S = TreeAI.Suggest
  TreeAI.Suggest = function(self, id, text, prio, o) sugg[#sugg+1] = id .. " [" .. tostring(prio) .. "] " .. tostring(text) return S(self, id, text, prio, o) end
  TreeAI._sendRawHook = true
end
for _, f in ipairs({"Leveling", "Group", "Inventory"}) do
  local chunk = assert(loadfile(dir .. "/" .. f .. ".lua"))
  chunk("TreeAI", ns)
end
local function fire(ev, ...) for _, f in ipairs(frames) do if f.ev[ev] and f.scripts.OnEvent then f.scripts.OnEvent(f, ev, ...) end end runTimers() end
if arg[2] then fire("ADDON_LOADED", "TreeAI") end
fire("PLAYER_LOGIN"); fire("PLAYER_ENTERING_WORLD", true, false)
fire("PLAYER_LEVEL_UP", 26); fire("SKILL_LINES_CHANGED"); fire("BAG_UPDATE", 0); fire("UPDATE_INVENTORY_DURABILITY")
fire("CHAT_MSG_LOOT", "Vous recevez le butin : |Hitem:1|h[Os]|hx3.")
fire("QUEST_LOG_UPDATE")
if arg[2] then
fire("CHAT_MSG_ADDON", "TREEAI", "1~FSTAT~1~1/1~t" .. TreeAI:Serialize({l=20,c="MAGE",m=1424,y=800,x=100,i=0,z="Contreforts"}), "PARTY", "Amie-Royaume")
fire("CHAT_MSG_ADDON", "TREEAI", "1~FQUEST~2~1/1~r500=1/8;999=C", "PARTY", "Amie-Royaume")
else
fire("CHAT_MSG_ADDON", "TREEAI", "S1|20|MAGE|1424|800|100|0|Contreforts", "PARTY", "Amie-Royaume")
fire("CHAT_MSG_ADDON", "TREEAI", "Q1|1|1|1|500=1/8;999=C", "PARTY", "Amie-Royaume")
end
fire("CHAT_MSG_ADDON", "TREEAI", "S1|bad", "PARTY", "Inconnu-Royaume")
fire("MERCHANT_SHOW")
assert(#used == 0, "sold without click!")
for _, f in ipairs(frames) do if f.scripts.OnClick then f.scripts.OnClick(f) end end
assert(#used == 1 and used[1] == "0:1", "sell grey failed")
inCombat = true; used = {}
for _, f in ipairs(frames) do if f.scripts.OnClick then f.scripts.OnClick(f) end end
assert(#used == 0, "sold in combat"); inCombat = false
fire("PLAYER_LOGOUT")
if SlashCmdList.TREEAIGROUP then SlashCmdList.TREEAIGROUP() else TreeAI:HandleSlash("groupe") end
for _, s in ipairs(sugg) do print("SUGG", s) end
for _, s in ipairs(sent) do print("SENT", s) end
for _, s in ipairs(chat) do print("CHAT", s) end
local function dump(t, ind) ind = ind or "" for k, v in pairs(t) do if type(v)=="table" then print(ind..tostring(k)..":") dump(v, ind.."  ") else print(ind..tostring(k).."="..tostring(v)) end end end
dump(TreeAIDB)
if TreeAI.mods then print("mods", TreeAI.mods.Leveling ~= nil, TreeAI.mods.Group ~= nil, TreeAI.mods.Inventory ~= nil) end
