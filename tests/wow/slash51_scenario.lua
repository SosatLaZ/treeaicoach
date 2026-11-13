-- Scenario 5.1 CLEAN / B15 + B13 — commands. Default profile (« sur »), hunter 40, no storm.
--   1. a bare switch command SHOWS the state and how to change it, and changes nothing ; on / off changes it
--   2. a command whose module is off says what to enable — never « ACTIVÉ », never an empty window
--   3. generic: after running EVERY documented command bare, no switch has moved (modules, automation, guide, profile…)
--   4. one name per switch: « Profil : Simple / Complet » (old words accepted, « Mode sûr » never printed),
--      « Conseils » for the advice level (« /tai mode » = silent alias), duplicates out of the help
--   5. one shell: « /tai afficher » opens the TreeAI window ; the old panel and the window are never open together
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.LeaveCombat()
  M.Advance(15, 0.5)
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  local Q = T.modules.QoL
  check(Q and Q.db and T.SlashSwitch and T.SLASH_OWNER and T.SLASH_HIDDEN, "5.1 command API")
  check(T:ModuleProfile() == "sur", "default profile (" .. tostring(T:ModuleProfile()) .. ")")
  local function plain(s) return (T.PlainText and T.PlainText(tostring(s)) or tostring(s)) end
  local function run(c)
    local o0 = #M.output
    M.Hw(function() M.RunSlash("/tai " .. c) end)
    M.Advance(0.6, 0.3)
    local out = {}
    for i = o0 + 1, #M.output do out[#out + 1] = plain(M.output[i]) end
    return table.concat(out, "\n"), #out
  end
  local function has(s, sub) return s:find(sub, 1, true) ~= nil end

  -- ---------------------------------------------------------------- 2. module off: say what to enable
  check(Q.db.enabled == false and not Q._active, "« Confort » is off in the default profile")
  for _, c in ipairs({ "quetesauto", "recompense", "quetesauto on", "recompense on" }) do
    local s, n = run(c)
    check(n == 1 and has(s, "« Confort » est coupée") and has(s, "/tai module QoL on"), c .. " (module off): says what to enable — " .. s)
    check(not has(s:upper(), "ACTIV") and not has(s, "ALLUMÉ"), c .. " (module off): never « ACTIVÉ » — " .. s)
  end
  check(not Q.db.questAuto and not Q.db.questTake, "module off: nothing was switched on")
  for _, c in ipairs({ "kos", "jcj", "cherche", "hv", "degats" }) do
    local own = T.SLASH_OWNER[c]
    local mod = own and T.modules[own]
    if mod and mod.db and mod.db.enabled == false then
      local s, n = run(c)
      check(n == 1 and has(s, "est coupée") and has(s, "/tai module " .. own .. " on"), c .. " (module off): " .. s)
    end
  end

  -- ---------------------------------------------------------------- 1. bare = state, on / off = change
  T:SetModuleEnabled("QoL", true)
  M.Advance(1, 0.5)
  for _, e in ipairs({ { "quetesauto", "questAuto" }, { "recompense", "questTake" } }) do
    local c, key = e[1], e[2]
    Q.db[key] = false
    local s = run(c)
    check(Q.db[key] == false, c .. " bare: nothing changed")
    check(has(s, "coupé") and has(s, "/tai " .. c .. " on"), c .. " bare: the state and how to change it — " .. s)
    s = run(c .. " on")
    check(Q.db[key] == true and has(s, "ALLUMÉ"), c .. " on: switched on — " .. s)
    s = run(c)
    check(Q.db[key] == true and has(s, "ALLUMÉ") and has(s, "/tai " .. c .. " off"), c .. " bare again: still on (not a toggle) — " .. s)
    s = run(c .. " off")
    check(Q.db[key] == false and has(s, "coupé"), c .. " off — " .. s)
  end
  T:SetModuleEnabled("QoL", false)
  if type(T.db.modChoice) == "table" then T.db.modChoice.QoL = nil end
  do
    local was = T.settings.pvp
    local s = run("pvp")
    check(T.settings.pvp == was and has(s, "Aides JcJ") and has(s, "/tai pvp o"), "pvp bare: state only — " .. s)
    run("pvp on") check(T.settings.pvp == true, "pvp on")
    run("pvp") check(T.settings.pvp == true, "pvp bare: still on")
    run("pvp off") check(T.settings.pvp == false, "pvp off")
    T.settings.pvp = was
    local g = T:QuestGuideOn()
    s = run("guide")
    check(T:QuestGuideOn() == g and has(s, "Guidage des quêtes") and has(s, "/tai guide o"), "guide bare: state only — " .. s)
    run("guide off") check(T:QuestGuideOn() == false, "guide off")
    run("guide") check(T:QuestGuideOn() == false, "guide bare: still off")
    run("guide on") check(T:QuestGuideOn() == true, "guide on")
    local d = T.settings.debug
    run("debug") check(T.settings.debug == d, "debug bare: state only")
    local st = T.modules.Stats and T.modules.Stats.db and T.modules.Stats.db.enabled
    s = run("module Stats")
    check((T.modules.Stats and T.modules.Stats.db.enabled) == st and has(s, "/tai module Stats o"), "module <Nom> bare: state only — " .. s)
  end

  -- ---------------------------------------------------------------- 4. one name per switch
  do
    local s = run("profil")
    check(s == "TreeAI: Profil : Simple. Pour changer : /tai profil simple | complet", "profil bare: " .. s)
    check(T:ModuleProfile() == "sur", "profil bare: nothing changed")
    for _, w in ipairs({ "complet", "tout" }) do
      T:SetModuleProfile("sur")
      s = run("profil " .. w)
      check(T:ModuleProfile() == "tout" and has(s, "Profil : Complet"), "profil " .. w .. " — " .. s)
    end
    for _, w in ipairs({ "simple", "sur", "sûr", "safe" }) do
      T:SetModuleProfile("tout")
      s = run("profil " .. w)
      check(T:ModuleProfile() == "sur" and has(s, "Profil : Simple") and not has(s, "Mode sûr"), "profil " .. w .. " — " .. s)
    end
    local a, b = run("mode"), run("conseils")
    check(a == b and has(a, "Conseils : ") and not has(a, "Mode :"), "« /tai mode » = « /tai conseils » : " .. a)
    s = run("reglages")
    check(has(s, "Réglages : ") and has(s, "/tai reglages perso | compte"), "reglages: " .. s)
    check(has(run("profil compte"), "Réglages : "), "old « profil compte » still answers as « reglages »")
  end
  local help, seen, nDoc = T:GetSlashHelp(), {}, 0
  for _, g in ipairs(help) do
    for _, it in ipairs(g.items) do
      nDoc = nDoc + 1
      check(not seen[it.cmd], "listed once: " .. it.cmd)
      seen[it.cmd] = true
      check(T:HasSlash(it.cmd), "documented command exists: " .. it.cmd)
      check(type(it.help) == "string" and it.help ~= "", "documented command has a text: " .. it.cmd)
    end
  end
  for _, c in ipairs({ "mode", "bug", "export", "itineraire", "basculer", "reinit", "reset", "verrou", "mini", "onglet" }) do
    check(not seen[c], "duplicate / old name out of the help: " .. c)
    check(T:HasSlash(c), "…but still accepted: " .. c)
  end
  for _, c in ipairs({ "profil", "conseils", "guide", "erreurs", "fenetre", "plan", "macros", "sacs" }) do check(seen[c], "in the help: " .. c) end

  -- ---------------------------------------------------------------- 3. generic: no bare command flips a switch
  local function signature()
    local t = {}
    for _, n in ipairs(T.moduleOrder) do
      local m = T.modules[n]
      t[#t + 1] = n .. "=" .. tostring(m.db and m.db.enabled)
    end
    t[#t + 1] = "pvp=" .. tostring(T.settings.pvp) .. " debug=" .. tostring(T.settings.debug) .. " guide=" .. tostring(T:QuestGuideOn())
      .. " profil=" .. tostring(T:ModuleProfile()) .. " conseils=" .. tostring(T.settings.preset) .. " regl=" .. tostring(T:ProfileMode())
      .. " auto=" .. tostring(Q.db.questAuto) .. "/" .. tostring(Q.db.questTake) .. " enabled=" .. tostring(T.settings.enabled)
      .. " verrou=" .. tostring(T.settings.panel and T.settings.panel.locked)
    return table.concat(t, " ")
  end
  local SKIP = { reinit = true, demo = true, bienvenue = true, visite = true }   -- reset / tours: not switches
  local moved = {}
  for _, g in ipairs(help) do
    for _, it in ipairs(g.items) do
      if not SKIP[it.cmd] then
        local before = signature()
        local s = run(it.cmd)
        local after = signature()
        if after ~= before then moved[#moved + 1] = it.cmd end
        check(not has(s, "ACTIVÉ") and not has(s, "Mode sûr : seules"), "bare /tai " .. it.cmd .. " announces nothing switched on: " .. s:sub(1, 120))
        if T.GetWizard and T:GetWizard() and T:GetWizard():IsShown() then T:GetWizard():Hide() end
      end
    end
  end
  check(#moved == 0, "bare commands that flipped a switch: " .. table.concat(moved, ", "))
  check(nDoc >= 30, "help read (" .. nDoc .. " commands)")

  -- ---------------------------------------------------------------- 5. one shell
  do
    local W = T.Window
    check(W and W._active and W.IsOpen, "the TreeAI window is on in the default profile")
    local panel = T.GetPanel and T:GetPanel()
    local function both() return (panel and panel:IsShown() and W.IsOpen()) and true or false end
    if W.IsOpen() then W.Close() end
    if panel and panel:IsShown() then T:HidePanel() end
    M.Advance(1, 0.2)   -- (the window's closing fade keeps the hand inside its own frame)
    run("afficher")
    check(W.IsOpen() and not (panel and panel:IsShown()), "« /tai afficher » opens the TreeAI window, not the old panel (window "
      .. tostring(W.IsOpen()) .. ", panel " .. tostring(panel and panel:IsShown()) .. ")")
    M.Hw(function() T:ShowPanel() end)   -- the old panel, opened by hand (menu entry)
    M.Advance(1, 0.2)                    -- (closing fade of the window)
    panel = T.GetPanel and T:GetPanel()
    check(panel and panel:IsShown() and not W.IsOpen(), "old panel opened by hand: the window closes")
    M.Advance(1, 0.2)
    run("fenetre")
    check(W.IsOpen() and not panel:IsShown(), "window opened: the old panel hides")
    check(not both(), "never both")
    run("cacher")
    check(not W.IsOpen() and not panel:IsShown(), "« /tai cacher » hides everything")
  end
end
