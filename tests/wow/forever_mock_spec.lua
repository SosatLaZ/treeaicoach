-- forever_mock_spec.lua — the « forever » flavour of wowmock.lua behaves like the real WoW: Forever client.
-- lua5.1 tests/wow/forever_mock_spec.lua   (prints « SPEC ok N » or the failed expectations; exit 1 on failure)
-- Facts checked here come from Blizzard's Forever interface code (forever_ref.lua) and warcraft.wiki.gg
-- « Secret Values »; see research/fidelity.md.
local here = (arg and arg[0] or "forever_mock_spec.lua"):match("^(.*)[/\\][^/\\]*$") or "."
local M = dofile(here .. "/wowmock.lua")
M.addonName = "TreeAI"
M.Install("forever")
local fails, n = {}, 0
local function expect(cond, what) n = n + 1 if not cond then fails[#fails + 1] = what end end
local function raises(fn, pat, what)
  local ok, err = pcall(fn)
  expect(not ok and (not pat or tostring(err):find(pat, 1, true)), what .. " (got: " .. tostring(err) .. ")")
end

-- existence: Era globals and deprecated shims are gone, the Forever replacements are there
for _, gone in ipairs({ "GetSpellInfo", "GetItemInfo", "GetContainerItemInfo", "GetQuestLogTitle", "UnitAura",
  "GetPetHappiness", "GetNumSkillLines", "IsSpellKnown", "GetNumPartyMembers", "SendChatMessage",
  "CombatLogGetCurrentEventInfo", "InterfaceOptions_AddCategory", "GetSpellCooldown", "GetNumQuestLogEntries" }) do
  expect(rawget(_G, gone) == nil, gone .. " must not exist on Forever")
end
for _, here in ipairs({ "UnitHealth", "GetUnitSpeed", "UnitPosition", "GetNumGroupMembers", "InCombatLockdown",
  "GetQuestLogLeaderBoard", "GetMoney", "issecretvalue", "canaccessvalue" }) do
  expect(type(rawget(_G, here)) == "function", here .. " must exist on Forever")
end
expect(type(C_Spell.GetSpellCooldown) == "function" and type(C_PetInfo.GetPetFoodTypes) == "function",
  "C_Spell.GetSpellCooldown / C_PetInfo exist")
expect(select(4, GetBuildInfo()) == 16001, "GetBuildInfo interface 16001")
expect(type(Enum.QuestClassification) == "table" and Enum.QuestClassification.Normal == 7, "documented enums loaded")

-- events: unknown events are refused like the client does, CLEU is forbidden
local f = CreateFrame("Frame")
raises(function() f:RegisterEvent("LEARNED_SPELL_IN_TAB") end, "Attempt to register unknown event", "unknown event")
local nv = #M.violations
f:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
expect(#M.violations == nv + 1 and M.violations[#M.violations].kind == "forbidden", "CLEU = ADDON_ACTION_FORBIDDEN")
f:RegisterEvent("PLAYER_REGEN_DISABLED")
expect(f:IsEventRegistered("PLAYER_REGEN_DISABLED"), "known events register")

-- secret values: out of combat everything is readable, in combat they are not
M.target = { exists = true, name = "Ours", level = 27, health = 800, healthMax = 800, hostile = true }
expect(type(UnitHealth("target")) == "number" and not issecretvalue(UnitHealth("target")), "health readable out of combat")
expect(not issecretvalue(GetUnitSpeed("player")), "speed readable out of combat")
M.EnterCombat()
local hp = UnitHealth("target")
expect(issecretvalue(hp) and type(hp) == "number", "UnitHealth secret in combat, type() = real type")
expect(issecretvalue(UnitHealth("player")), "SecretReturns: the player too")
expect(issecretvalue(GetUnitSpeed("player")), "GetUnitSpeed secret in combat (stats restriction, player too)")
expect(issecretvalue((UnitRangedDamage("player"))), "UnitRangedDamage secret in combat")
expect(issecretvalue(UnitName("target")) and not issecretvalue(UnitName("player")), "NPC name secret, mine not")
expect(not issecretvalue(UnitName("pet")), "pet name readable")
expect(issecretvalue(UnitGUID("target")), "NPC GUID secret")
expect(not issecretvalue(UnitHealthMax("player")) and issecretvalue(UnitHealthMax("target")), "max health: NPC only")
local cd = C_Spell.GetSpellCooldown(75)
expect(type(cd) == "table" and issecretvalue(cd.startTime), "cooldowns secret in combat (fields of the struct)")
raises(function() return hp < 30 end, "attempt to compare a secret value", "compare a secret")
raises(function() local x = hp + 1 return x end, "attempt to perform arithmetic on upvalue 'hp' (a secret number value, while execution tainted by 'TreeAI')", "arithmetic names the variable")
raises(function() return #UnitName("target") end, "get length of", "# on a secret")
raises(function() return UnitName("target"):lower() end, "attempt to index", "method call on a secret string")
raises(function() return math.floor(hp) end, "userdata", "C function argument")
raises(function() return UnitHealth(UnitName("target")) end, "secret value", "secret argument refused (AllowedWhenUntainted)")
expect(issecretvalue((UnitName(M.Secret("target")))), "secret argument accepted (AllowedWhenTainted): secret answer")
expect(issecretvalue("PV " .. hp) and issecretvalue(string.format("%d", hp)) and issecretvalue(tostring(hp))
  and issecretvalue(tonumber(hp)) and issecretvalue(strjoin(" ", "a", hp)), "concat / format / tostring / tonumber / strjoin give secrets")
-- a secret error swallowed by the addon's pcall still counts
nv = #M.violations
local ok = pcall(function() return hp > 10 end)
expect(not ok and #M.violations > nv and M.violations[#M.violations].kind == "secret", "swallowed secret error recorded")
M.LeaveCombat()
expect(not issecretvalue(UnitHealth("target")), "readable again after combat")
M.secretReturns = "always"
expect(issecretvalue(UnitHealth("player")), "secretReturns = always")
M.secretReturns = "restricted"

-- instances: map restriction (identity secret out of combat), no positions, no nameplates, no comms in encounters
M.instance = true
expect(issecretvalue(UnitName("target")) and UnitPosition("player") == nil, "instance: NPC identity secret, no position")
M.instance = false
local y, x = UnitPosition("player")
expect(type(y) == "number" and type(x) == "number", "UnitPosition(player) in the open world")
expect(UnitPosition("target") == nil, "UnitPosition is nil for NPCs")
M.encounter = true
expect(C_ChatInfo.SendAddonMessage("TREEAI", "x", "PARTY") == Enum.SendAddonMessageResult.AddOnMessageLockdown,
  "no addon messages during an encounter")
M.encounter = false

-- hardware events: macros / bindings / SAY only from a click or a typed command
nv = #M.violations
expect(SetBinding("1", "TOGGLEBACKPACK") == false and #M.violations == nv + 1, "SetBinding outside a click = forbidden")
expect(M.Hw(function() return SetBinding("1", "TOGGLEBACKPACK") end) == true, "SetBinding inside a click")
raises(function() CreateMacro("X", 1, "/say x", true) end, "Interface action failed", "CreateMacro outside a click")

-- quest log: a zone header first, every documented QuestInfo field
local q1 = C_QuestLog.GetInfo(1)
expect(q1 and q1.isHeader == true and C_QuestLog.GetQuestIDForLogIndex(1) == nil, "a header entry first")
local q2 = C_QuestLog.GetInfo(2)
expect(q2 and q2.isHeader == false and q2.difficultyLevel ~= nil and q2.isHidden == false and q2.questClassification == 7,
  "QuestInfo has the documented fields")

-- auction house: nothing works with the AH closed; results arrive later, throttled
M.ah.list = { { id = 2672, count = 10, buyout = 500, auctionID = 1 } }
local got = 0
local h = CreateFrame("Frame")
h:RegisterEvent("COMMODITY_SEARCH_RESULTS_UPDATED")
h:SetScript("OnEvent", function() got = got + 1 end)
C_AuctionHouse.SendSearchQuery(C_AuctionHouse.MakeItemKey(2672))
M.Advance(1)
expect(got == 0, "AH closed: no answer")
M.SetAuctionHouse(true)
C_AuctionHouse.SendSearchQuery(C_AuctionHouse.MakeItemKey(2672))
expect(got == 0 and not C_AuctionHouse.IsThrottledMessageSystemReady(), "answer is asynchronous, system busy")
M.Advance(1)
expect(got == 1 and C_AuctionHouse.IsThrottledMessageSystemReady(), "answer after the round trip")

if #fails > 0 then
  for _, w in ipairs(fails) do io.write("SPEC FAIL ", w, "\n") end
  io.write("SPEC fail ", #fails, "/", n, "\n")
  os.exit(1)
end
io.write("SPEC ok ", n, "\n")
