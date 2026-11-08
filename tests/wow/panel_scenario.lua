-- Harness scenario: the panel contract (RegisterTab / SetTabLines / Suggest -> « Maintenant »), tabs,
-- scrolling, clickable lines, mini mode, per-character state, corner alert box, no advice in chat,
-- one options page per module. Fake names only.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/panel_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  -- (the in-game planner fills « Quêtes » / « Maintenant » too: off for these UI checks)
  M.RunSlash("/tai module Planner off")
  local T = TreeAI
  ctx.check(type(T.SetTabLines) == "function" and type(T.RegisterTab) == "function", "contrat onglets présent")
  local ids = {}
  for _, t in ipairs(T:GetTabs()) do ids[#ids + 1] = t.id end
  local order = table.concat(ids, ","):gsub(",gear", ""):gsub(",stats", ""):gsub(",binds", ""):gsub(",modules", "")  -- module tabs (Équipement, Stats, Touches…) may follow
  ctx.check(order == "now,quests,group,hv,info" or order == "now,quests,predict,group,hv,info",
    "onglets dans l'ordre : " .. order)
  local panel = T:GetPanel() or T:CreatePanel()
  ctx.check(panel ~= nil, "panneau créé")
  ctx.check(panel:GetParent() == UIParent and not panel:IsProtected(), "panneau : enfant d'UIParent, non protégé")
  T:ShowPanel()
  -- la tempête du harnais clique tous les boutons (préréglages, verrou…) : on repart du mode normal
  if T.ApplyPreset then T:ApplyPreset("normal") end
  if T.SetProfile then T:SetProfile("account") end

  -- « Maintenant » : top 5 par priorité, dédoublonné
  T:ClearSuggestions()
  T:Suggest("a:1", "Info un", 1)
  T:Suggest("a:2", "Conseil deux", 2)
  T:Suggest("b:2", "Conseil deux", 2)            -- même texte, autre id
  T:Suggest("a:3", "Urgent trois", 3, { sound = false })
  T:Suggest("a:4", "Info quatre", 1)
  T:Suggest("a:5", "Info cinq", 1)
  T:Suggest("a:6", "Info six", 1)
  local now = T:GetTabLines("now")
  ctx.check(#now == 5, "Maintenant : 5 lignes max (" .. #now .. ")")
  local sorted = now[1].text == "Urgent trois"
  -- (COHERE) l'en-tête d'étape de quête épinglé passe juste après le danger : hors du tri par priorité
  local function stepPin(e) return e.pin and T.STEP_SOURCES and T.STEP_SOURCES[e.source or ""] end
  local prevP = now[1] and now[1].prio
  for i = 2, #now do
    if not stepPin(now[i]) then
      if prevP and (now[i].prio or 0) > prevP then sorted = false end
      prevP = now[i].prio or 0
    end
  end
  ctx.check(sorted, "Maintenant trié par priorité")
  local seen = {}
  for _, e in ipairs(now) do ctx.check(not seen[e.text], "doublon : " .. e.text); seen[e.text] = true end

  -- conseils jamais dans le chat (settings.chat = false par défaut), même si un module le demande
  local before = #M.output
  T:Suggest("a:chat", "Conseil bavard", 2, { chat = true })
  ctx.check(#M.output == before, "aucun conseil écrit dans le chat")

  -- SetTabLines : lignes cliquables, routage, sources multiples
  local clicked
  T:SetTabLines("hv", { { text = "Affaire A", prio = 2, onClick = function(e) clicked = e.text end, tooltip = "aide" },
    { text = "Affaire B" } }, "Auction")
  T:SetTabLines("hv", { { text = "Autre source" } }, "Autre")
  T:Suggest("Auction:deals", "HV : 2 affaires", 2, { module = "Auction" })
  ctx.check(T:SelectTab("hv"), "SelectTab hv")
  M.Advance(1, 0.25)
  ctx.check(T:GetActiveTab() == "hv" and not T:IsMini(), "onglet HV actif, panneau agrandi")
  local vis = T:GetVisibleLines()
  ctx.check(#vis == 4 and vis[1].text == "Affaire A" and vis[3].text == "Autre source" and vis[4].text == "HV : 2 affaires",
    "HV : lignes du module puis conseils routés (" .. #vis .. ")")
  local row
  for _, c in ipairs({ panel:GetChildren() }) do
    if c.entry and c.entry.text == "Affaire A" and c:IsShown() then row = c end
  end
  ctx.check(row ~= nil, "ligne affichée")
  if row then
    row:Click("LeftButton")
    ctx.check(clicked == "Affaire A", "clic -> onClick")
    M.Script(row, "OnEnter"); M.Script(row, "OnLeave")
  end
  ctx.check(#T:GetTabLines("now") == 5, "Maintenant toujours limité à 5")

  -- défilement (molette)
  -- (Pro / Economy also fill « Infos » with their own blocks: switched off so the tab holds only "Test")
  M.RunSlash("/tai module Pro off"); M.RunSlash("/tai module Economy off")
  local many = {}
  for i = 1, 30 do many[i] = { text = "Ligne " .. i } end
  T:RegisterTab("zztest", "Test", 99) -- onglet dédié : « Infos » reçoit aussi les lignes des modules
  T:SetTabLines("zztest", many, "Test")
  T.settings.panel.maxLines = 8
  T:SelectTab("zztest")
  M.Advance(1, 0.25)
  local shown = 0
  for _, c in ipairs({ panel:GetChildren() }) do if c.entry and c:IsShown() then shown = shown + 1 end end
  ctx.check(shown == 8, "8 lignes visibles (" .. shown .. ")")
  ctx.check(panel:IsMouseWheelEnabled(), "molette activée")
  M.Wheel(panel, -1)
  local first
  for _, c in ipairs({ panel:GetChildren() }) do if c.entry and c:IsShown() and c.index == 1 then first = c.entry.text end end
  ctx.check(first == "Ligne 2", "molette : défile d'une ligne (" .. tostring(first) .. ")")
  for _ = 1, 50 do M.Wheel(panel, -1) end
  for _, c in ipairs({ panel:GetChildren() }) do if c.entry and c:IsShown() and c.index == 1 then first = c.entry.text end end
  local total = #T:GetTabLines("zztest")
  local want = T:GetTabLines("zztest")[total - 8 + 1].text
  ctx.check(first == want, "molette : butée en bas (" .. tostring(first) .. " / " .. want .. ")")

  -- état par personnage
  local key = T:CharKey()
  local cp = TreeAIDB.chars[key] and TreeAIDB.chars[key].panel
  ctx.check(cp and cp.tab == "zztest", "onglet mémorisé par personnage")
  local onStop = panel:GetScript("OnDragStop")
  if onStop then onStop(panel) end
  ctx.check(cp and cp.point ~= nil, "position mémorisée par personnage")

  -- mode mini
  T:SetMini(true)
  ctx.check(T:IsMini() and cp.mini == true, "mode mini")
  ctx.check(panel:GetHeight() <= 30, "mini : une ligne (" .. tostring(panel:GetHeight()) .. ")")
  ctx.check(panel.miniText:GetText() == "Urgent trois", "mini : la ligne la plus importante")
  T:HandleSlash("mini")
  ctx.check(not T:IsMini(), "/tai mini bascule")

  -- encadré d'alerte : seulement prio 3, clic = ouvre le panneau sur l'onglet
  local alert, items = T:GetAlert()
  if alert then alert:Hide() end
  if T.ResetAlertGate then T:ResetAlertGate() end   -- anti-bruit : 1 alerte / 20 s (déjà une plus haut)
  T:Suggest("Squad:far", "Ami loin", 2, { module = "Squad" })
  alert, items = T:GetAlert()
  ctx.check(not (alert and alert:IsShown()), "prio 2 : pas d'encadré")
  T:Suggest("Squad:dead", "Amie morte !", 3, { sound = false, module = "Squad" })
  alert, items = T:GetAlert()
  ctx.check(alert and alert:IsShown() and items[1] and items[1].text == "Amie morte !" and items[1].tab == "group",
    "prio 3 : encadré dans le coin")
  T:HidePanel()
  if alert then alert:Click("LeftButton") end
  ctx.check(T:GetPanel():IsShown() and T:GetActiveTab() == "group", "clic encadré : panneau ouvert sur Groupe")
  T:Suggest("x:urg", "Autre urgence", 3, { sound = false })
  M.Advance((T.settings.alert.duration or 8) + 2, 0.5)
  ctx.check(not alert:IsShown(), "encadré disparaît après quelques secondes")
  for _, corner in ipairs(T.ALERT_CORNERS) do
    T.settings.alert.corner = corner
    T:ApplyAlertSettings()
    ctx.check(alert:GetPoint(1) == corner, "coin " .. corner)
  end
  T.settings.alert.corner = "TOPRIGHT"

  -- modules migrés
  if T.Squad and T.Squad.PushGroupTab then ctx.check(pcall(T.Squad.PushGroupTab, T.Squad), "Squad -> onglet Groupe") end

  -- options : une page par module
  T:InitOptions()
  local main, pages = T:GetOptionPages()
  local n = 0
  for _ in T:IterModules() do n = n + 1 end
  ctx.check(main and #pages == n, "une page d'options par module (" .. #pages .. "/" .. n .. ")")
  main:build()
  for _, p in ipairs(pages) do p.frame:build() end
  T:ClearSuggestions()
end
