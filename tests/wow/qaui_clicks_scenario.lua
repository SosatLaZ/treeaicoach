-- qaui_clicks_scenario.lua (QA-UI round 9 → FIX-UI regression): every hub entry / Outils row / page action /
-- card click / minimap click in the current profile: what it opens, NOTHING in the chat (UI-10, UI-11, UI-19),
-- no error, no blocked action. QAUI_COMBAT=1 : the same in combat (a refusal = the game's red message, not the
-- chat). Then edit mode (UI-03 : the card gets its overlay, the old panel stays closed), « Réinitialiser
-- l'interface » (UI-04), edit mode refused in combat (UI-35), « Recharger » in combat (UI-36). QAUI_VERBOSE=1 : dump.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W, H = T.Window, T.Hub
  local Lb = dofile("tests/wow/qaui_lib.lua")
  local verbose = os.getenv("QAUI_VERBOSE") == "1"
  local function p(...)
    if not verbose then return end
    local t = { ... } for i = 1, #t do t[i] = tostring(t[i]) end io.stderr:write(table.concat(t, " "), "\n")
  end
  Lb.world(M, T)
  Lb.goTo(1441, 46, 50)
  M.Advance(6, 0.5)
  local wiz = T.GetWizard and T:GetWizard()
  if wiz and wiz:IsShown() then wiz.skip:Click("LeftButton") end
  if T.Tour and T.Tour.active then T.Tour.Finish("skip") end
  M.Advance(2, 0.5)
  local combat = os.getenv("QAUI_COMBAT") == "1"
  p("== profile", T:ModuleProfile(), combat and "IN COMBAT" or "out of combat")
  -- frames we watch: which became visible after a click
  local function shownSet()
    local s = {}
    for _, f in ipairs(M.frames) do
      local n = f.__name
      if n and n:find("^TreeAI") and f:IsVisible() then s[n] = true end
    end
    return s
  end
  local function diff(a, b)
    local t = {}
    for n in pairs(b) do if not a[n] then t[#t + 1] = "+" .. n end end
    for n in pairs(a) do if not b[n] then t[#t + 1] = "-" .. n end end
    table.sort(t)
    return table.concat(t, " ")
  end
  local function run(label, fn)
    local s0, c0, e0, b0, v0 = shownSet(), #M.output, #M.errors, #M.blocked, #M.violations
    local page0 = W.IsOpen() and W.CurrentPage() or "-"
    if combat then M.EnterCombat() end
    M.hardware = true
    local ok, err = pcall(fn)
    M.hardware = false
    M.Advance(0.5, 0.25)
    local s1 = shownSet()
    local page1 = W.IsOpen() and W.CurrentPage() or "-"
    local chat = {}
    for i = c0 + 1, #M.output do
      if not M.output[i]:find("^%[UIError%]") then chat[#chat + 1] = M.output[i] end   -- red game message ≠ chat
    end
    p(("  %-34s → %s%s%s%s%s%s"):format(label, diff(s0, s1),
      (page1 ~= page0) and (" [page " .. page1 .. "]") or "",
      #chat > 0 and ("  CHAT: " .. table.concat(chat, " / ")) or "",
      (#M.errors > e0) and ("  ERR: " .. tostring(M.errors[#M.errors].msg)) or "",
      (#M.blocked > b0) and ("  BLOCKED x" .. (#M.blocked - b0)) or "",
      ok and "" or ("  PCALL: " .. tostring(err))))
    check(ok, label .. ": no Lua error (" .. tostring(err) .. ")")
    check(#chat == 0, label .. ": nothing in the chat (" .. table.concat(chat, " / ") .. ")")
    check(#M.blocked == b0 and #M.violations == v0, label .. ": no blocked action / violation")
    if combat then M.LeaveCombat(); M.Advance(1, 0.5) end
    -- close what opened (back to a clean screen)
    if W.IsOpen() then W.GetWindow():Hide() end
    for n in pairs(s1) do
      if not s0[n] then local f = rawget(_G, n) if f and f.Hide and n ~= "TreeAIMinimapButton" and f:GetParent() == UIParent then f:Hide() end end
    end
    if T.SetEditMode and T:IsEditMode() then T:SetEditMode(false) end
  end
  -- 1. hub entries (main menu)
  p("== HUB entries")
  H.mode = "main"
  for _, e in ipairs(H.Entries()) do
    if e.id ~= "profil" then run("hub:" .. e.title, function() e.fn() end) end
  end
  -- 2. window Outils rows
  p("== window Outils rows")
  W.Open("tools")
  W.char.collapsed = {}
  W.Render()
  local labels = {}
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "row" then labels[#labels + 1] = r.label end end
  W.GetWindow():Hide()
  for i, lab in ipairs(labels) do
    run("outils:" .. lab, function()
      W.Open("tools")
      W.char.collapsed = {}
      W.Render()
      local k = 0
      for _, c in ipairs(W.GetWindow().content.__children) do
        if c.__kind == "Button" and c:IsVisible() and c.entry and c.entry.onClick and c.t then
          k = k + 1
          if k == i then W.Run(c.entry, "LeftButton") break end
        end
      end
    end)
  end
  -- 3. page action buttons (the card is hidden by « Cacher la carte » : shown again after)
  p("== page actions")
  for _, pg in ipairs(W.PAGES) do
    if not pg.hidden and pg.id ~= "options" and pg.id ~= "modules" then
      run("action:" .. pg.id, function()
        W.Open(pg.id)
        local a = W.GetWindow().action
        if a:IsShown() then a:GetScript("OnClick")(a) end
      end)
    end
  end
  -- 3b. (4.0 final) every clickable row of every page (journal quests, steps, lines, dungeon cards): nothing in the
  -- chat either (a quest of the journal printed « Ouvre ton journal (L) → … → Abandonner » on a plain click)
  p("== page rows")
  local function clickables()
    local t = {}
    for _, cb in ipairs(W.GetWindow().content.__children) do
      if cb.__kind == "Button" and cb:IsVisible() and cb:GetScript("OnClick") then t[#t + 1] = cb end
    end
    return t
  end
  for _, pg in ipairs(W.PAGES) do
    if not pg.hidden and pg.id ~= "options" and pg.id ~= "modules" and pg.id ~= "tools" then
      W.Open(pg.id)
      W.char.collapsed = {}
      W.Render()
      local n = #clickables()
      W.GetWindow():Hide()
      for i = 1, math.min(n, 30) do
        run("row:" .. pg.id .. "#" .. i, function()
          W.char.dungeon = nil
          W.Open(pg.id)
          W.char.collapsed = {}
          W.Render()
          local cb = clickables()[i]
          if cb then cb:GetScript("OnClick")(cb, "LeftButton") end
        end)
      end
    end
  end
  -- (5.1 LOOK) les clics ci-dessus ont pu couper le guidage des quêtes (entrée du menu, case de la page) : rallumé,
  -- la carte a ses étapes (avant, un nombre pair de bascules le rallumait par hasard)
  if T.SetQuestGuide then T:SetQuestGuide(true) M.Advance(3, 0.25) end
  W.db.card = true
  W:OnOptionChanged("card")
  -- 4. card: hero click, Ouvrir, step 1 click
  p("== card")
  W.ShowCard()
  local c = W.GetCard()
  if not c.steps[1] then
    -- (5.1 BAGS) the module states left by the clicks above depend on how many rows each page has (new options
    -- shift them): with nothing to do the card has no step — give it one so its click is still exercised
    T:Suggest("qaui:step", "Étape de test", 2, { tab = "now", ttl = 0 })
    W.ShowCard()
    c = W.GetCard()
  end
  run("card:hero", function() c.hero:GetScript("OnClick")(c.hero, "LeftButton") end)
  run("card:hero right", function() c.hero:GetScript("OnClick")(c.hero, "RightButton") end)
  run("card:Ouvrir", function() c.menuBtn:GetScript("OnClick")(c.menuBtn) end)
  run("card:step1", function() c.steps[1]:GetScript("OnClick")(c.steps[1], "LeftButton") end)
  run("card:step1 right", function() c.steps[1]:GetScript("OnClick")(c.steps[1], "RightButton") end)
  -- 5. minimap button
  p("== minimap button")
  local b = H.Button()
  run("minimap:left", function() b:GetScript("OnClick")(b, "LeftButton") end)
  run("minimap:right", function() b:GetScript("OnClick")(b, "RightButton") end)
  p("== total blocked", #M.blocked, "violations", #M.violations, "errors", #M.errors)
  if combat then return end

  -- 6. edit mode : the card « Prochaine étape » is the HUD → its overlay ; the old panel stays closed (UI-03)
  W.db.card = true
  W:OnOptionChanged("card")
  T:HidePanel()
  W.ShowCard()
  local pn = T:GetPanel()
  check(W.CardIsHUD() and c:IsShown() and not (pn and pn:IsShown()), "before edit mode: card shown, panel hidden")
  check(T:SetEditMode(true), "edit mode on")
  local ov = T:GetOverlays()
  check(ov.card and ov.card:IsShown() and c:IsShown(), "edit mode: the card has its overlay (handle)")
  check(not (pn and pn:IsShown()), "edit mode: the old panel does not pop up")
  T:SetEditMode(false)
  check(c:IsShown() and not (pn and pn:IsShown()) and not ov.card:IsShown(), "edit mode off: card back, panel closed")
  -- the player opened the old panel himself (Outils) : both can be placed, then each keeps its state
  T:ShowPanel()
  check(pn:IsShown() and not c:IsShown(), "panel opened by hand: the card steps aside")
  T:SetEditMode(true)
  check(ov.panel and ov.panel:IsShown() and ov.card and ov.card:IsShown(), "edit mode with the panel open: both overlays")
  T:SetEditMode(false)
  check(pn:IsShown() and not c:IsShown(), "edit mode off: the panel the player opened stays, the card hides")
  T:HidePanel()
  check(c:IsShown() and not pn:IsShown(), "panel closed by hand: the card is back")
  -- 7. « Réinitialiser l'interface » : the card comes back at the automatic place, never the old panel, no chat (UI-04)
  W.char.cardPos = { point = "CENTER", x = 10, y = 10 }
  local n0 = #M.output
  check(T:ResetInterface(), "reset")
  check(c:IsShown() and not pn:IsShown() and W.char.cardPos == nil, "reset: card at its automatic place, panel hidden")
  check(#M.output == n0, "reset: nothing in the chat")
  -- 8. combat : edit mode refused → nothing moves, the card does not pop back over the panel (UI-35)
  T:ShowPanel()
  M.EnterCombat()
  check(T:SetEditMode(true) == false and not T:IsEditMode(), "combat: edit mode refused")
  check(pn:IsShown() and not c:IsShown(), "combat: refused → the card does not pop back")
  -- UI-36 : « Recharger l'interface » in combat says why in the footer
  W.Open("options")
  local a = W.GetWindow().action
  a:GetScript("OnClick")(a)
  check(tostring(W.GetWindow().status:GetText()):find("combat", 1, true), "combat: « Recharger » explains in the footer")
  W.Close()
  M.LeaveCombat()
  M.Advance(1, 0.5)
  T:HidePanel()
  -- 9. « Cacher la carte » is kept (UI-23)
  -- (4.1 LAYOUT) la carte se coupe / s'allume dans Réglages → Affichage (une ligne, un clic)
  W.Open("display")
  local cardRow
  for _, b in ipairs(W.GetWindow().content.__children or {}) do
    if b.entry and b.entry.text and tostring(b.entry.text):find("^Carte « Prochaine étape »") and b:IsShown() then cardRow = b end
  end
  check(cardRow and cardRow.entry.onClick, "Affichage : ligne « Carte « Prochaine étape » »")
  if cardRow then T:UserAction(cardRow.entry.onClick) end
  check(W.db.card == false and not c:IsShown(), "carte coupée (Affichage) : hidden and remembered")
  T:HidePanel()
  check(not c:IsShown(), "still hidden after closing the panel")
  W.Render()
  local nowOff
  for _, b in ipairs(W.GetWindow().content.__children or {}) do
    if b.entry and tostring(b.entry.text):find("^Carte « Prochaine étape »") and b:IsShown() then nowOff = b.entry.right end
  end
  check(nowOff == "coupé", "then « coupé » (" .. tostring(nowOff) .. ")")
  for _, bb in ipairs(W.GetWindow().content.__children or {}) do
    if bb.entry and tostring(bb.entry.text):find("^Carte « Prochaine étape »") and bb:IsShown() then T:UserAction(bb.entry.onClick) break end
  end
  check(W.db.card == true and c:IsShown(), "carte rallumée (Affichage) : back")
  W.Close()
end
