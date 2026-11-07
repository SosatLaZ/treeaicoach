-- Macro manager: create/update a macro ONLY out of combat; queue the request otherwise.
local A = SampleAddon
local M = A:RegisterModule("Macros", {})
local NAME = "SA_Manger"
local pending = false

local function bestFood()
  local bags = A.modules.Bags
  return bags and bags.food and GetItemInfo(bags.food)
end

function M:Install()
  if InCombatLockdown() then
    pending = true
    print("SampleAddon: macros mises à jour après le combat.")
    return false
  end
  local food = bestFood() or "Miche de pain dur"
  local body = "#showtooltip\n/use [nocombat] " .. food
  local idx = GetMacroIndexByName(NAME)
  if idx and idx > 0 then EditMacro(idx, NAME, nil, body)
  else CreateMacro(NAME, "INV_MISC_QUESTIONMARK", body, nil) end
  pending = false
  return true
end

function M:OnSlash(msg)
  if msg == "macros" or msg == "install" then self:Install() return true end
  if msg == "status" then print("SampleAddon: " .. tostring(A.modules.Bags.free) .. " places libres") return true end
end
A:On("PLAYER_REGEN_ENABLED", function() if pending then M:Install() end end)
