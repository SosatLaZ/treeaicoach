-- Scenario 5.1 CLEAN / B5 + B9 + B14 — the DEFAULT profile (« sur »), a new hunter 40, no storm, faithful Forever mock.
--   1. B5: the default contains what he asked for and is cheap — Hunter (pet / ammo), Talents, PathLearn on; Stats
--      (measured, over the session budgets) left in « Complet » ; the 7 modules switched off since 4.1 are gone
--   2. Hunter at rest: only the events its switched-on helpers need (no movement, spell or group-aura stream), no
--      tick of the core's 3-s cycle ; dead pet → « Ressuscite » (also once the corpse is gone), never « Appelle »
--   3. B9: a failure swallowed by a wrapper is counted ONCE per place (file:line), with its first message, in
--      TreeAIDB.diag.swallowed ; the success path records nothing
--   4. B14: at logout the real per-module time (debugprofilestop) is kept in TreeAIDB.diag.perf
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.LeaveCombat()
  M.Advance(15, 0.5)
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  check(T:ModuleProfile() == "sur", "default profile (" .. tostring(T:ModuleProfile()) .. ")")

  -- ---------------------------------------------------------------- 1. what the default contains
  for _, n in ipairs({ "Hunter", "Talents", "PathLearn" }) do
    check(T.SAFE_MODULES[n] and T.modules[n] and T.modules[n]._active, n .. " is on in the default profile")
  end
  check(not (T.modules.Stats and T.modules.Stats._active), "Stats stays in « Complet » (measured: over the budgets)")
  for _, n in ipairs(T.GONE_MODULES or {}) do
    check(T.modules[n] == nil and not T.MODULE_INFO[n] and not T.SAFE_MODULES[n], n .. " is gone (code, info, profile)")
  end
  check(#(T.GONE_MODULES or {}) == 7, "7 modules removed")

  -- ---------------------------------------------------------------- 2. Hunter at rest
  local H = T.Hunter
  check(H and H.enabled and H.db, "Hunter enabled for a hunter")
  local f = H and H._evOn or {}
  for _, e in ipairs({ "PLAYER_STARTED_MOVING", "PLAYER_STOPPED_MOVING", "UNIT_SPELLCAST_SUCCEEDED", "UNIT_SPELLCAST_START",
      "UNIT_AURA", "PLAYER_TARGET_CHANGED", "CHAT_MSG_MONSTER_EMOTE", "START_AUTOREPEAT_SPELL", "PLAYER_SWING" }) do
    check(not f[e], "default helpers: Hunter does not listen to " .. e)
  end
  for _, e in ipairs({ "UNIT_PET", "UNIT_HAPPINESS", "PLAYER_REGEN_ENABLED", "PLAYER_REGEN_DISABLED" }) do
    check(f[e], "Hunter listens to " .. e)
  end
  check(H.AutoRefresh == nil and T.Talents and T.Talents.AutoRefresh == nil, "no work in the core's 3-s cycle")
  check(H.db.bar == false and H.db.pullTips == false and H.db.tab == false, "shot bar, pull reminders, old-panel tab: off")
  -- an option switched on: its events follow (and go away again)
  H.db.pullTips = true H:OnOptionChanged("pullTips", true)
  check(H._evOn.PLAYER_TARGET_CHANGED and H._evOn.UNIT_AURA, "pull reminders on: target / aura events registered")
  H.db.pullTips = false H:OnOptionChanged("pullTips", false)
  check(not H._evOn.PLAYER_TARGET_CHANGED and not H._evOn.UNIT_AURA, "pull reminders off: unregistered again")

  local function line(id)
    for k, e in pairs(T._suggestions) do if k:find(id, 1, true) and k:find("Hunter", 1, true) then return e.text end end
  end
  M.pet.exists, M.pet.dead, M.pet.health = true, true, 0
  M.FireEvent("UNIT_PET", "player")
  M.Advance(1, 0.5)
  check(line("petdead") and line("petdead"):find("Ressuscite", 1, true), "dead pet: « Ressuscite ton familier » — " .. tostring(line("petdead") or "nil"))
  M.pet.exists = false   -- the corpse goes away: « Appel du familier » would fail (« ton familier est mort »)
  M.FireEvent("UNIT_PET", "player")
  M.Advance(1, 0.5)
  check(line("petdead") and not line("nopet"), "pet gone after dying: still « Ressuscite », never « Appelle » — "
    .. tostring(line("nopet") or "nil"))
  M.pet.exists, M.pet.dead, M.pet.health = true, false, 500
  M.FireEvent("UNIT_PET", "player")
  M.Advance(1, 0.5)
  check(not line("petdead") and not line("nopet"), "pet back alive: both lines gone")

  -- ---------------------------------------------------------------- 3. B9 swallowed errors
  local db = TreeAIDB
  check(type(T.Swallow) == "function", "central counter T.Swallow")
  local before = db.diag and db.diag.swallowed and db.diag.swallowed.total or 0
  local boom = function() error("boum de test") end
  for _ = 1, 5 do T.Swallow("Interface\\AddOns\\TreeAI\\Modules\\Fake.lua:42: attempt to index a nil value", "Fake") end
  local sw = db.diag.swallowed
  local e = sw and sw.sites and sw.sites["Fake.lua:42"]
  check(e and e.n == 5 and e.msg:find("^Modules\\Fake.lua:42") and sw.total == before + 5,
    "one entry per place, counted, first message kept without the path prefix")
  -- Core's own wrapper (T.API calls go through it)
  local okCall = pcall(function() return T.API and T.API.Pub and T.API.Pub(1) end)
  check(okCall, "success path")
  local n0 = sw.total
  for _ = 1, 3 do pcall(function() return T.API.IsSecret(1) end) end
  check(sw.total == n0, "success path: nothing recorded")
  -- a real wrapper failure: Core's try on a function that raises
  local coreTry = T._TryForTests
  check(coreTry and coreTry(boom) == nil and sw.total == n0 + 1, "Core try: a failure returns nil and is counted")
  local site
  for k, v in pairs(sw.sites) do if v.msg and v.msg:find("boum de test", 1, true) then site = k end end
  check(site and site:find("default51_scenario.lua:", 1, true), "the place is the file:line that raised — " .. tostring(site))
  for k in pairs(sw.sites) do check(type(k) == "string" and #k < 120, "site key is short text: " .. tostring(k)) end

  -- ---------------------------------------------------------------- 4. B14 real time per module, kept at logout
  M.FireEvent("PLAYER_LOGOUT")
  local pf = db.diag.perf
  check(type(pf) == "table" and type(pf.mods) == "table" and #pf.mods > 0, "diag.perf written at logout")
  if pf and pf.mods and pf.mods[1] then
    check(type(pf.mods[1].m) == "string" and type(pf.mods[1].ms) == "number" and pf.mods[1].n >= 1 and pf.up >= 1,
      "each module: name, real ms, calls ; session length")
  end
end
