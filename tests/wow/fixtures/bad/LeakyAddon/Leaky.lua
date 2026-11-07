-- Intentionally bad: global leaks, macro edit in combat, runtime error in a handler.
local f = CreateFrame("Frame")
leakedCounter = 0                            -- global leak
function helperWithoutLocal() return 1 end   -- global leak
f:RegisterEvent("PLAYER_REGEN_DISABLED")
f:RegisterEvent("PLAYER_LOGIN")
f:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_REGEN_DISABLED" then
    pcall(CreateMacro, "Bad", "INV_MISC_QUESTIONMARK", "/cast Attaque", nil)  -- in combat: blocked
  elseif event == "PLAYER_LOGIN" then
    local t = nil
    return t.boom                             -- runtime error
  end
end)
