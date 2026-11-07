-- Extra steps + assertions for the SampleAddon fixture (run by tests/wow/test_harness.py).
return function(M, ctx)
  local A = SampleAddon
  ctx.check(A and A.modules.Bags and A.modules.Macros, "modules registered")
  ctx.check(SampleAddonDB and SampleAddonDB.version == 1, "SavedVariables initialised")
  ctx.check(SampleAddonDB.char and SampleAddonDB.char.class == "HUNTER", "char summary saved")
  -- macro created out of combat (slash storm), never in combat
  ctx.check(GetMacroIndexByName("SA_Manger") > 0, "macro exists")
  local _, _, body = GetMacroInfo("SA_Manger")
  ctx.check(body and body:find("/use %[nocombat%]"), "macro body: " .. tostring(body))
  M.EnterCombat()
  ctx.check(A.modules.Macros:Install() == false, "install refused in combat")
  M.LeaveCombat()
  ctx.check(ctx.out("après le combat"), "combat message printed")
  ctx.check((M.sold or 0) >= 1, "grey item sold")
  ctx.check(#M.sentAddon >= 1 and M.sentAddon[1].prefix == "SAMPLEADD", "HELLO sent to the group")
  ctx.check(A.lastPeer == "Amie", "peer seen: " .. tostring(A.lastPeer))
  -- bags full -> suggestion shown on the panel
  for b = 0, 4 do for s = 1, M.bags[b].size do if not M.bags[b].slots[s] then M.SetBagItem(b, s, 4865, 1) end end end
  ctx.fire("BAG_UPDATE_DELAYED")
  ctx.check(A.suggestions.bags, "bags-full suggestion")
  M.Advance(1)
  ctx.check(SampleAddonPanel.text:GetText() == "Sacs presque pleins", "panel shows the top suggestion")
end
