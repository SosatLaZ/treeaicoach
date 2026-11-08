-- qaui_first_scenario.lua (QA-UI round 9 → FIX-UI regression): what a NEW player sees in the first minute (run
-- with --profile sur --no-storm): mode sûr notice on the card, start-up wizard (every step, every button), the
-- guided tour, chat. Checks UI-05 (wizard step 2 keeps the card, never the old panel), W-12..W-15, UI-19 (no
-- chat at the end of the wizard), UI-32 (Échap closes the tour bubble). QAUI_VERBOSE=1 : dump.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W, Tr, H = T.Window, T.Tour, T.Hub
  local Lb = dofile("tests/wow/qaui_lib.lua")
  Lb.instrument(M)
  local verbose = os.getenv("QAUI_VERBOSE") == "1"
  local function p(...)
    if not verbose then return end
    local t = { ... } for i = 1, #t do t[i] = tostring(t[i]) end io.stderr:write(table.concat(t, " "), "\n")
  end
  local chat0 = #M.output
  p("== new player: profile", T:ModuleProfile(), "tour done", tostring(Tr and Tr.Done()))
  Lb.world(M, T)
  Lb.goTo(1441, 46, 50)
  local function cardShown() return W.GetCard() and W.GetCard():IsShown() or false end
  -- (4.1 STRIP) the « boutons rapides » strip replaced the card at the top left (card off by default)
  local function stripShown() local f = rawget(_G, "TreeAIStrip") return f and f:IsShown() or false end
  local function card(tag)
    local c = W.GetCard()
    if not (c and c:IsShown()) then p("  card: hidden", tag or "") return end
    p("  card (" .. (tag or "") .. "): " .. tostring(c.hero.title:GetText()) .. " | " .. tostring(c.hero.sub:GetText()))
    for i, r in ipairs(c.steps) do
      if r:IsShown() then p(("    %d [%s] %s || %s"):format(i, tostring(r.badge.num:GetText()), Lb.strip(r.title:GetText()), Lb.strip(r.sub:GetText()))) end
    end
    if c.empty:IsShown() then p("    empty: " .. tostring(c.empty:GetText())) end
  end
  local function panel()
    local pn = T:GetPanel()
    return pn and pn:IsShown() or false
  end
  for t = 1, 4 do
    M.Advance(1, 0.25)
    p(("t=%ds panel=%s card=%s"):format(t, tostring(panel()), tostring(cardShown())))
  end
  check(stripShown() and not cardShown() and not panel(), "first login: the quick buttons, not the card nor the old panel (4.1)")
  card("t=4")
  M.Advance(2, 0.25)
  local wiz = T.GetWizard and T:GetWizard()
  p("== wizard shown:", tostring(wiz and wiz:IsShown()))
  check(wiz and wiz:IsShown(), "start-up wizard shown")
  if wiz and wiz:IsShown() then
    for step = 1, 3 do
      local body = Lb.strip(wiz.body:GetText())
      p(("  -- wizard step %d title « %s »"):format(step, Lb.strip(wiz.title:GetText())))
      p("     body: " .. body:gsub("\n", " / "))
      if step == 1 then
        check(body:find("petite carte", 1, true) and not body:find("petit panneau", 1, true), "wizard 1 describes the card (W-12)")
        check(body:find("Aucun automatisme sans ton accord", 1, true), "wizard 1: W-13")
      end
      if step == 2 then
        check(body:find("Où mettre la carte « Prochaine étape »", 1, true), "wizard 2: where to put the CARD (W-14)")
        -- every place button: the card stays the HUD, the old panel never comes back
        for _, b in ipairs(wiz.groups[2]) do
          local lbl = Lb.strip(b:GetText())
          if lbl == "Auto (scan)" or lbl == "Haut droite" or lbl == "Bas gauche" then
            b:Click("LeftButton")
            p(("     after « %s »: panel=%s card=%s"):format(lbl, tostring(panel()), tostring(cardShown())))
            check(stripShown() and not panel(), "wizard « " .. lbl .. " » : quick buttons shown, old panel hidden (UI-05)")
          end
        end
      end
      wiz.next:Click("LeftButton")
    end
    p("  wizard after Terminer: shown=" .. tostring(wiz:IsShown()) .. " onboarded=" .. tostring(T.db.onboarded))
    check(not wiz:IsShown(), "wizard closed")
  end
  check(stripShown() and not panel(), "after the wizard: still the quick buttons")
  -- the tour
  for _ = 1, 30 do
    M.Advance(1, 0.25)
    if Tr.active and Tr.mode == "tour" then break end
  end
  p("== tour active:", tostring(Tr.active ~= nil), "mode", tostring(Tr.mode))
  check(Tr.active and Tr.mode == "tour", "the guided tour starts")
  local guard = 0
  local steps = 0
  while Tr.active and guard < 2 do
    guard = guard + 1
    local b = Tr.Box()
    p(("  [%s] « %s » %s"):format(Lb.strip(b.count:GetText()), Lb.strip(b.head:GetText()), Tr.active.step and Tr.active.step.id or "?"))
    p("     body: " .. Lb.strip(b.body:GetText()))
    steps = steps + 1
    b.primary:Click("LeftButton")
  end
  -- UI-32 : Échap (UISpecialFrames hides the bubble) = « Passer », nothing left open
  local sf = rawget(_G, "UISpecialFrames") or {}
  local inList = false
  for _, n in ipairs(sf) do if n == "TreeAITourCallout" then inList = true end end
  check(inList, "tour bubble closes with Échap (UISpecialFrames)")
  if Tr.active then
    Tr.Box():Hide()                        -- Échap
    check(not Tr.active and not Tr.mode and not Tr.Box():IsShown(), "Échap: the tour is skipped, nothing left open")
  end
  -- a programmatic hide (combat pause) keeps the tour
  if Tr.Start then
    Tr.Start("hub")
    if Tr.active then
      Tr.Pause("combat")
      check(Tr.active ~= nil and Tr.paused, "combat pause keeps the tour (not finished by its own hide)")
      Tr.Finish("skip")
    end
  end
  p(("after tour: panel=%s card=%s hubmenu=%s"):format(tostring(panel()), tostring(cardShown()), tostring(H.IsOpen())))
  card("after tour")
  local nChat = 0
  for i = chat0 + 1, #M.output do
    p("  chat: " .. M.output[i])
    if not M.output[i]:find("^%[UIError%]") then nChat = nChat + 1 end
  end
  check(nChat == 0, "first minute: nothing in the chat (" .. nChat .. ")")
end
