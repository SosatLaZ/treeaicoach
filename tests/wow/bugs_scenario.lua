-- Scenario for Modules/Bugs.lua (/tai erreurs -> TreeAIDB.diag.export). Fake names only.
return function(M, ctx)
  local T = TreeAI
  local B = T.Bugs
  ctx.check(B and T.modules.Bugs, "Bugs module registered")
  if not B then return end
  M.LeaveCombat()
  -- fake other addons (Forever: C_AddOns only)
  local fake = { "TreeAI", "FakeMeter", "FakeBags" }
  local CA = rawget(_G, "C_AddOns")
  local host = CA or _G           -- legacy flavour: Era globals
  rawset(host, "GetNumAddOns", function() return #fake end)
  rawset(host, "GetAddOnInfo", function(i) return fake[i] end)
  local oldLoaded = host.IsAddOnLoaded
  host.IsAddOnLoaded = function(n) return n == "FakeMeter" or n == "FakeBags" or oldLoaded(n) end
  local oldMeta = host.GetAddOnMetadata
  host.GetAddOnMetadata = function(n, f) if n == "FakeMeter" and f == "Version" then return "9.9" end return oldMeta(n, f) end

  -- an error in a module (safecall) + one through the chained error handler + a blocked action
  local Boom = T:RegisterModule("BoomX", {})
  T:SetModuleEnabled("BoomX", true)   -- (5.1) default profile « sur » : a new module is off until switched on
  Boom:RegisterEvent("PLAYER_FLAGS_CHANGED", function() error("boomx-test") end)
  M.FireEvent("PLAYER_FLAGS_CHANGED")
  B.OnLuaError("Interface\\AddOns\\TreeAI\\Modules\\Quests.lua:12: handler-test")
  B.OnLuaError("Interface\\AddOns\\OtherAddon\\x.lua:1: not ours")
  M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", "FakeProtected()")
  M.FireEvent("ADDON_ACTION_FORBIDDEN", "TreeAI", "FakeProtected()")
  local errs = TreeAIDB.diag.errors or {}
  local seenHandler, seenOther = false, false
  for _, e in ipairs(errs) do
    if tostring(e.msg):find("handler-test", 1, true) then seenHandler = e.module == "Quests" end
    if tostring(e.msg):find("not ours", 1, true) then seenOther = true end
  end
  ctx.check(seenHandler, "error from the chained handler kept (module Quests)")
  ctx.check(not seenOther, "other addons' errors ignored")
  ctx.check(B._hooked, "error handler chained")

  local printed = {}
  local oldPrint = T.Print
  T.Print = function(self, ...) printed[#printed + 1] = table.concat({ tostringall and tostringall(...) or tostring((...)) }, " ") return oldPrint(self, ...) end
  M.RunSlash("/tai erreurs ça plante au PNJ")
  T.Print = oldPrint
  local ex = TreeAIDB.diag.export
  ctx.check(type(ex) == "table" and type(ex.lines) == "table" and #ex.lines > 5, "diag.export written")
  if not ex then return end
  local text = table.concat(ex.lines, "\n")
  ctx.check(#ex.lines <= 200 and #text <= 40000, "export capped")
  ctx.check(text:find("boomx-test", 1, true), "Lua error in export")
  ctx.check(text:find("FakeProtected()", 1, true) and text:find("×2", 1, true), "blocked action with count")
  ctx.check(text:find("== Autotest du", 1, true), "selftest summary in export")
  ctx.check(text:find("Modules actifs", 1, true) and text:find("Réglages", 1, true) and text:find("Highlight", 1, true), "modules (not cut) + settings")
  ctx.check(text:find("FakeMeter 9.9", 1, true), "other addons listed with version")
  ctx.check(text:find("ça plante au PNJ", 1, true), "note kept")
  ctx.check(ex.at and ex.at > 0 and ex.version, "timestamp + version")
  local said = false
  for _, s in ipairs(printed) do if s:find("TreeAI-erreurs.txt", 1, true) then said = true end end
  ctx.check(said, "one chat line about TreeAI-erreurs.txt")

  host.IsAddOnLoaded, host.GetAddOnMetadata = oldLoaded, oldMeta
  rawset(host, "GetNumAddOns", nil)
  rawset(host, "GetAddOnInfo", nil)

  -- alias /tai export + button in « Infos »
  TreeAIDB.diag.export = nil
  M.RunSlash("/tai export")
  ctx.check(TreeAIDB.diag.export, "/tai export alias")
  local found
  for _, l in ipairs(T.GetTabLines and T:GetTabLines("info") or {}) do
    if tostring(l.text):find("Erreurs pour TreeAI", 1, true) and l.onClick then found = l end
  end
  ctx.check(found, "Erreurs button in the Infos tab")
  if found then TreeAIDB.diag.export = nil; T:UserAction(found.onClick, found); ctx.check(TreeAIDB.diag.export, "button builds export") end
end
