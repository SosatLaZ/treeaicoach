-- Scenario for Modules/SelfTest.lua + the AUDIT fixes (Forever 1.60.1 API). Fake names only.
-- Run on every flavour; the "forever" flavour adds secret values, removed globals, CLEU = forbidden.
return function(M, ctx)
  local T = TreeAI
  local ST = T.modules.SelfTest
  ctx.check(ST and T.RecordError and T._errorHook, "SelfTest registered with its error hook")
  if not ST then return end
  local forever = M.flavor == "forever"
  M.LeaveCombat()
  local diag = TreeAIDB.diag or {}

  -- 1. /tai test : report in TreeAIDB.diag.selftest
  M.RunSlash("/tai test")
  local r = TreeAIDB.diag and TreeAIDB.diag.selftest
  ctx.check(type(r) == "table" and type(r.items) == "table" and #r.items >= 20, "selftest report written")
  if r then
    local ids = {}
    for _, it in ipairs(r.items) do ids[it.id] = it.status end
    for _, id in ipairs({ "client", "questlog", "bags", "items", "spells", "auras", "skills", "map", "auction", "macros",
                          "comms", "questie", "spf", "tomtom", "nameplates", "secrets", "combatlog", "settings" }) do
      ctx.check(ids[id] ~= nil, "selftest item " .. id)
    end
    local okish = function(v) return v == "ok" or (M.flavor == "legacy" and v == "warn") end   -- legacy: Era fallbacks
    ctx.check(okish(ids.questlog) and okish(ids.bags) and okish(ids.items), "core API families ok")
    ctx.check(ids.macros == "ok", "macros writable during the typed command: " .. tostring(ids.macros))
    ctx.check(okish(ids.comms), "addon message self-test ok: " .. tostring(ids.comms))
    ctx.check(r.forever == forever, "forever flag")
    ctx.check(type(r.text) == "string" and r.text:find("✓", 1, true), "compact text report")
    ctx.check(type(r.modules) == "table" and #r.modules >= 20, "per-module health")
    if forever then
      ctx.check(ids.pet == "ok" or T:PlayerClass() ~= "HUNTER", "C_PetInfo read on Forever")
      ctx.check(ids.secrets == "ok" and ids.combatlog == "ok", "secrets + combat log items")
    end
  end
  -- reachable by clicks: a line in « Infos » + buttons on the module's options page
  local found
  for _, l in ipairs(T.GetTabLines and T:GetTabLines("info") or {}) do
    if tostring(l.text):find("Autotest TreeAI", 1, true) and l.onClick then found = l end
  end
  ctx.check(found, "autotest line (clickable) in the Infos tab")
  if found then T:UserAction(found.onClick, found) end
  ctx.check(ST.options and ST.options[1].type == "button", "autotest button on the options page")
  -- outside a command, the macro item says « only during a click / command »
  local r2 = ST.Run(true)
  for _, it in ipairs(r2.items) do
    if it.id == "macros" then ctx.check(it.status == "warn", "macros not writable outside a player action") end
  end

  -- 2. Lua errors of our modules -> TreeAIDB.diag.errors (module + message, deduped, 30 max)
  local Boom = T:RegisterModule("Boom", {})
  T:SetModuleEnabled("Boom", true)   -- (5.1) default profile « sur » : a new module is off until switched on
  Boom:RegisterEvent("PLAYER_FLAGS_CHANGED", function() error("boom-test") end)
  M.FireEvent("PLAYER_FLAGS_CHANGED")
  M.FireEvent("PLAYER_FLAGS_CHANGED")
  local errs = TreeAIDB.diag.errors
  local last = errs and errs[#errs]
  ctx.check(last and last.module == "Boom" and tostring(last.msg):find("boom-test", 1, true), "error captured with module")
  ctx.check(last and last.n == 2, "same error deduped (n=2)")
  for i = 1, 40 do T:RecordError("err " .. i, "Boom:x") end
  ctx.check(#TreeAIDB.diag.errors == 30, "error list capped at 30")
  T:RecordError(M.Secret and M.Secret() or "x", "Boom:secret")   -- never crash on a secret message
  Boom:UnregisterAllEvents()
  if T.SetModuleEnabled then T:SetModuleEnabled("Boom", false) end
  TreeAIDB.diag.errors = {}
  T._modErrors = {}
  M.RunSlash("/tai erreurs")

  -- 3. combat log: never registered on Forever (ADDON_ACTION_FORBIDDEN even inside pcall)
  local okReg = T:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED", Boom, function() end)
  if forever then
    ctx.check(okReg == false and not T:CombatLogAllowed(), "CLEU refused on Forever")
    ctx.check(not M.IsEventRegistered("COMBAT_LOG_EVENT_UNFILTERED"), "no frame registered CLEU")
  end

  -- 4. compat helpers
  local A = T.API
  ctx.check(type(A.Pub) == "function" and A.Pub(5) == 5, "API.Pub passthrough")
  if forever then ctx.check(A.Pub(M.Secret()) == nil and A.IsSecret(M.Secret()), "API.Pub hides a secret") end
  M.skills = { { name = "Arcs", rank = 120, max = 135 }, { name = "Secourisme", rank = 40, max = 75 } }
  local sl = A.SkillLines()
  ctx.check(#sl == 2 and sl[2].name == "Secourisme" and sl[2].rank == 40, "API.SkillLines (C_SkillInfo / Era)")
  local food = A.PetFoodTypes()
  ctx.check(type(food) == "table" and food[1] == "Viande", "API.PetFoodTypes -> table")
  ctx.check(type(A.QuestLogEntries()) == "table" and type(A.SpellBookSpells()) == "table", "quest log / spellbook helpers")
  ctx.check(A.HasAction(1) == false, "API.HasAction")

  -- 5. combat with secret values: hostile target + nameplates + pet; nothing may raise
  M.secretPlayer = true
  M.target = { exists = true, name = "Ours-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-7-1",
    classification = "rare", health = 50, healthMax = 100 }
  M.units.nameplate1 = { name = "Loup-a", guid = "Creature-0-0-0-0-8-1", hostile = true, level = 25, classification = "rare" }
  M.EnterCombat()
  M.FireEvent("PLAYER_TARGET_CHANGED")
  M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate1")
  M.Advance(0.4, 0.01)   -- (5.0 STABLE) nameplates reach the modules grouped, 0.1 s later
  M.FireEvent("UNIT_AURA", "player")
  M.FireEvent("UNIT_AURA", "target")
  M.FireEvent("UNIT_HEALTH", "pet")
  M.FireEvent("UNIT_THREAT_LIST_UPDATE", "target")
  local H, B, F, S = T.modules.Hunter, T.modules.Boost, T.modules.Fight, T.modules.Squad
  if H and H.CheckPet then T.safecall(H.CheckPet, H) end
  if H and H.CheckMark then T.safecall(H.CheckMark, H) end
  if B and B.CheckThreat then T.safecall(B.CheckThreat, B) end
  if F and F.CheckPull then T.safecall(F.CheckPull, F) end
  if S and S.CollectState then T.safecall(S.CollectState, S) end
  M.Advance(6, 0.5)
  M.RunSlash("/tai test")
  M.LeaveCombat()
  M.secretPlayer = false
  M.units.nameplate1 = nil
  local list = TreeAIDB.diag.errors or {}
  local msgs = {}
  for _, e in ipairs(list) do msgs[#msgs + 1] = tostring(e.module) .. ": " .. tostring(e.msg) end
  ctx.check(#list == 0, "no Lua error in combat with secret values: " .. table.concat(msgs, " | "))

  -- 6. macros: deleting outside a player action is refused (no forbidden call)
  local Mc = T.modules.Macros
  if Mc and Mc.Remove then Mc:Remove() end
  ctx.check(true, "Macros:Remove outside a command did not call DeleteMacro (violations checked by the harness)")
end
