-- Harness scenario (UI2, round 5): sounds per kind (distinct, cooldown, global gap, only danger in combat,
-- preview), windows (common header, ESC via UISpecialFrames for our windows only, collapse, size bounds, fade),
-- live placement (screenshot rects, a growing Questie tracker → our panel slides aside then comes back home,
-- drop onto another frame → nearest free place, accepted overlaps), 21:9 / 4:3 profiles, dispositions.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/ui2_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.LeaveCombat()
  check(type(_G.UISpecialFrames) == "table", "UISpecialFrames (mock)")
  if T.SetProfile then T:SetProfile("account") end

  ---------------------------------------------------------------- 1. sons
  check(type(T.Sound) == "function" and T.SOUND_KINDS, "TreeAI:Sound exposé")
  local ids, n = {}, 0
  for kind, def in pairs(T.SOUND_KINDS) do
    n = n + 1
    check(not ids[def.id], "son distinct pour " .. kind)
    ids[def.id] = true
  end
  check(n >= 8, "au moins 8 genres de sons (" .. n .. ")")
  T.settings.sound = true
  T:ResetSoundGate()
  local s0 = #M.sounds
  check(T:Sound("quest") == true and #M.sounds == s0 + 1, "son « quête terminée » joué")
  local ok, why = T:Sound("rare")
  check(not ok and why == "gap", "écart global : pas deux sons à la suite (" .. tostring(why) .. ")")
  M.Advance(3, 1)
  ok, why = T:Sound("quest")
  check(not ok and why == "cooldown", "même genre : cooldown (" .. tostring(why) .. ")")
  check(T:Sound("rare") == true, "autre genre après l'écart : joué")
  M.Advance(40, 5)
  T:SetSoundKind("rare", false)
  ok, why = T:Sound("rare")
  check(not ok and why == "kind", "genre coupé dans les options")
  local s1 = #M.sounds
  check(T:PreviewSound("rare") and #M.sounds == s1 + 1, "bouton « Écouter » : joue même coupé")
  T:SetSoundKind("rare", true)
  -- rafale : 20 événements de groupe en 1 s = un seul son
  M.Advance(40, 5)
  s1 = #M.sounds
  for _ = 1, 20 do T:Sound("group"); M.Advance(0.05, 0.05) end
  check(#M.sounds == s1 + 1, "rafale de 20 → 1 son (" .. (#M.sounds - s1) .. ")")
  -- canal
  M.Advance(40, 5)
  T:SoundSettings().channel = "SFX"
  T:Sound("ah")
  local log = T:SoundLog()
  check(log[#log].kind == "ah" and log[#log].channel == "SFX", "canal choisi (Effets)")
  T:SoundSettings().channel = "Master"
  -- combat : seul le danger
  M.Advance(40, 5)
  M.EnterCombat()
  ok, why = T:Sound("quest")
  check(not ok and why == "combat", "en combat : pas de son de quête")
  check(T:Sound("danger") == true, "en combat : le danger sonne")
  ok, why = T:Sound("danger")
  check(not ok and why == "cooldown", "danger : son propre cooldown")
  -- via Suggest : prio 3 danger en combat = son danger, prio 3 normal en combat = rien
  M.Advance(30, 5)
  if T.ResetAlertGate then T:ResetAlertGate() end
  T:ResetSoundGate()
  s1 = #M.sounds
  T:Suggest("ui2:danger", "Danger : test", 3, { danger = true, ttl = 5 })
  log = T:SoundLog()
  check(#M.sounds == s1 + 1 and log[#log].kind == "danger", "Suggest prio 3 danger en combat → son danger")
  M.Advance(30, 5)
  s1 = #M.sounds
  T:Suggest("ui2:calme", "Pas un danger", 3, { ttl = 5 })
  check(#M.sounds == s1, "Suggest prio 3 non-danger en combat → aucun son")
  M.LeaveCombat()
  M.Advance(30, 5)
  -- genre choisi par un module (rare) sur un conseil prio 2
  T:ResetSoundGate()
  s1 = #M.sounds
  T:Suggest("ui2:rare", "Rare : test à proximité", 2, { ttl = 5, sound = true, soundKind = "rare" })
  log = T:SoundLog()
  check(#M.sounds == s1 + 1 and log[#log].kind == "rare", "conseil prio 2 avec soundKind = rare → son rare")
  local Mi = T:GetModule("Missions")
  check(Mi and Mi.SOUND_EVENTS and Mi.SOUND_EVENTS.join, "Missions : événements de groupe sonores")
  T.settings.sound = false
  ok, why = T:Sound("danger")
  check(not ok and why == "off", "sons coupés : rien, même le danger")
  T.settings.sound = true

  ---------------------------------------------------------------- 2. fenêtres
  local Th = T.Theme
  check(Th and Th.Window, "Theme:Window exposé")
  local function inEsc(name) for _, v in ipairs(_G.UISpecialFrames) do if v == name then return true end end return false end
  local function pressEsc()
    for _, name in ipairs(_G.UISpecialFrames) do
      local f = _G[name]
      if f and f:IsShown() then f:Hide() end
    end
  end
  do local w0 = T:GetWizard(); if w0 and w0:IsShown() then w0:Hide() end end  -- 1st-launch wizard may already be open
  T:ShowWizard(1)
  local wiz = T:GetWizard()
  local wd = Th:GetWindow(wiz)
  check(wd and wd.header and wd.header.title and wd.header.close, "assistant : en-tête commun (titre + fermer)")
  check(Th:SkinOf(wiz) == "window", "assistant : verre sombre")
  check(inEsc("TreeAIWizard"), "assistant : Échap ferme (UISpecialFrames)")
  check(Th:IsAnimating(wiz), "assistant : fondu à l'ouverture")
  M.Advance(0.5, 0.05)
  check(not Th:IsAnimating(wiz) and math.abs((wiz:GetAlpha() or 0) - 1) < 0.01, "fondu terminé (alpha 1)")
  pressEsc()
  check(not wiz:IsShown(), "Échap : assistant fermé")
  T:ShowHelp()
  local help = T:GetHelpFrame()
  local h0 = help:GetHeight()
  Th:SetCollapsed(help, true)
  check(help:GetHeight() == Th.HEADER_H + 6 and T.settings.windows.TreeAIHelp.collapsed, "réduire : en-tête seul, mémorisé")
  Th:SetCollapsed(help, false)
  check(help:GetHeight() == h0 and not T.settings.windows.TreeAIHelp.collapsed, "déplier : taille d'avant")
  help:Hide()
  -- fenêtres des modules
  local Todo = T:GetModule("Todo")
  if Todo and Todo.CreateFrame then
    local tf = Todo.CreateFrame()
    check(tf and Th:IsWindow(tf) and inEsc("TreeAITodoFrame"), "À faire : Échap + fondu")
  end
  local A = T:GetModule("Auction")
  if A and A.CreateWindow then
    local w = A.CreateWindow()
    w:Show()
    check(Th:IsWindow(w) and inEsc("TreeAIEsc_auction") and _G.TreeAIEsc_auction:IsShown(), "HV : relais Échap (cadre sans nom)")
    pressEsc()
    check(not w:IsShown(), "Échap : fenêtre HV fermée")
  end
  local Mc = T:GetModule("Macros")
  if Mc and Mc.TogglePanel then
    if Mc.ToggleList then Mc:ToggleList() else Mc:TogglePanel() end   -- (MACRO3) the list = the macro panel
    check(Mc.panel and Th:IsWindow(Mc.panel) and Th:SkinOf(Mc.panel) == "window", "macros : fenêtre habillée")
    if Mc.panel then Mc.panel:Hide() end
  end
  -- le panneau est un HUD : jamais fermé par Échap ; poignée de largeur bornée
  T:ShowPanel()
  local panel = T:GetPanel()
  check(not inEsc("TreeAIPanel") and Th:IsWindow(panel), "panneau : pas d'Échap (HUD), décoré")
  local fw = Th:ClampSize(panel, 5000, 300)
  check(fw <= 560 and fw >= 300, "panneau : largeur bornée (" .. fw .. ")")
  fw = Th:ClampSize(panel, 10, 300)
  check(fw >= 300, "panneau : largeur mini 300")
  T.settings.panel.width = 320
  T:ApplyPanelSettings()
  -- barre du mode édition : Échap = Fini
  T:SetEditMode(true)
  check(inEsc("TreeAIEditBar"), "barre d'édition dans UISpecialFrames")
  pressEsc()
  check(not T:IsEditMode(), "Échap quitte le mode édition")

  -- commandes d'onglet = vrais boutons dans une barre, pas des lignes de texte
  local clicked = 0
  T:SetTabLines("info", {
    { text = "Recalculer (objets jusqu'au niv 30)", label = "Recalculer", control = true, onClick = function() clicked = clicked + 1 end },
    { text = "Horizon : niveau +3", label = "Horizon +3", control = true, onClick = function() end },
    { text = "Une vraie ligne d'information" },
  }, "ui2")
  T:SelectTab("info")
  -- (« Infos » a aussi ses propres boutons : Autotest, Parcours complet… on regarde les deux posés ici)
  local all, ctl = T:GetTabControls(), {}
  for _, r in ipairs(all) do if r.text == "Recalculer" or r.text == "Horizon +3" then ctl[#ctl + 1] = r end end
  check(#ctl == 2 and ctl[1].text == "Recalculer" and (ctl[2].x > ctl[1].x + ctl[1].w - 1 or ctl[2].row > ctl[1].row), "2 boutons dans la barre de l'onglet")
  for _, r in ipairs(ctl) do check(r.textW <= r.textMax, "bouton " .. r.text .. " : texte dans sa case") end
  local asLine = false
  for _, e in ipairs(T:GetVisibleLines()) do if e.control then asLine = true end end
  check(not asLine, "les commandes ne sont plus des lignes")
  T:SetTabLines("info", {}, "ui2")
  T:SelectTab("now")
  check(#T:GetTabControls() == 0, "onglet sans commande : pas de barre")

  ---------------------------------------------------------------- 3. placement
  local function overlaps(a, b)
    return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h
  end
  local fakes = {}
  local function fake(name, l, b, w, h)
    local f = _G[name] or CreateFrame("Frame", name, UIParent)
    f.GetRect = function() return l, b, w, h end
    f:Show()
    _G[name] = f
    fakes[name] = f
    return f
  end
  local function clearFakes() for _, f in pairs(fakes) do f:Hide() end end
  local function freeOfReserved(pl, W, H, tag)
    for role, r in pairs(pl) do
      for _, z in ipairs(T.ReservedRects(W, H)) do
        if role == "panel" or role == "radar" or role == "alert" or role == "tracker" then
          check(not overlaps(r, z), ("%s : %s hors de la zone « %s »"):format(tag, role, z.name))
        end
      end
    end
  end

  -- 3a. capture d'écran 2000×1125
  UIParent:SetSize(2000, 1125)
  local shot = {
    { "MinimapCluster", 1750, 863, 245, 262 }, { "ObjectiveTrackerFrame", 1745, 335, 255, 525 },
    { "Questie_BaseFrame", 25, 325, 255, 510 }, { "PartyMemberFrame1", 20, 935, 115, 55 },
    { "PartyMemberFrame2", 20, 875, 115, 55 }, { "PartyMemberFrame3", 20, 815, 115, 55 },
    { "DetailsBaseFrame1", 0, 995, 375, 130 }, { "ChatFrame1", 30, 75, 420, 170 },
    { "MainMenuBar", 435, 0, 1120, 50 }, { "MultiBarBottomLeft", 730, 250, 525, 45 },
    { "PlayerFrame", 545, 245, 180, 65 }, { "BuffFrame", 1560, 1060, 180, 60 },
  }
  for _, r in ipairs(shot) do fake(r[1], r[2], r[3], r[4], r[5]) end
  T:ApplyDisposition("confort")
  local pl = T:AutoLayout(true)
  check(pl and pl.panel, "capture : panneau placé")
  check(T.ScreenProfile(2000, 1125) == "16:9", "profil 16:9")
  freeOfReserved(pl, 2000, 1125, "16:9")

  -- 3b. le suivi Questie grandit sur le panneau → le panneau glisse, puis revient
  check(not (T:Reflow(true) or {}).panel, "placement auto propre : rien à écarter")
  local p = pl.panel
  local FR = T._FrameRect
  local l0, b0, w0, h0 = FR(panel, 2000, 1125)
  check(l0 and math.abs(l0 - p.l) < 3 and math.abs((b0 + h0) - (p.b + p.h)) < 20,
    ("rectangle du panneau lu (%s,%s)"):format(tostring(l0), tostring(b0)))
  local home = { l = l0, b = b0, w = w0, h = h0 }
  -- le suivi du côté du panneau s'allonge vers le bas jusque sur lui
  local right = home.l + home.w / 2 > 1000
  local grow = right and { l = 1745, b = home.b + 10, w = 255, h = 860 - home.b - 10 }
    or { l = 25, b = home.b + 10, w = math.max(255, home.l + 40 - 25), h = 835 - home.b - 10 }
  local growName = right and "ObjectiveTrackerFrame" or "Questie_BaseFrame"
  check(overlaps(grow, home), "le suivi agrandi recouvre le panneau")
  fake(growName, grow.l, grow.b, grow.w, grow.h)
  local moved = T:Reflow()
  check(moved and moved.panel and not moved.panel.home, "suivi qui grandit : le panneau s'écarte")
  local l1, b1, w1, h1 = FR(panel, 2000, 1125)
  check(l1 and not overlaps({ l = l1, b = b1, w = w1, h = h1 }, grow), ("panneau libre du suivi (%d,%d)"):format(l1 or -1, b1 or -1))
  check(T:IsDisplaced("panel"), "panneau marqué « déplacé » (sa place est gardée)")
  check(not T:Reflow(), "rien de neuf : aucun mouvement")
  if right then fake("ObjectiveTrackerFrame", 1745, 335, 255, 525) else fake("Questie_BaseFrame", 25, 325, 255, 510) end
  moved = T:Reflow()
  check(moved and moved.panel and moved.panel.home, "suivi revenu : le panneau rentre chez lui")
  local l2, b2 = FR(panel, 2000, 1125)
  check(l2 and math.abs(l2 - home.l) < 3 and math.abs(b2 - home.b) < 3 and not T:IsDisplaced("panel"), "panneau à sa place d'origine")

  -- 3c. déposé sur un autre cadre → place libre la plus proche ; chevauchement voulu = gardé
  T:SetEditMode(true)
  local rec = T:CommitMove("panel", 1600, 600)        -- à moitié sur le suivi des objectifs
  local pr = { l = rec.l, b = rec.b, w = panel:GetWidth(), h = panel:GetHeight() }
  check(not overlaps(pr, { l = 1745, b = 335, w = 255, h = 525 }) and rec.l < 1600,
    ("déposé sur le suivi : glisse vers la place libre la plus proche (%d,%d)"):format(rec.l, rec.b))
  check(rec.zone ~= nil, "zone apprise (" .. tostring(rec.zone) .. ")")
  T.settings.snapFree = false
  rec = T:CommitMove("panel", 40, 100)
  check(rec.l == 40 and rec.b == 100, "aimant libre coupé : la place du joueur est gardée")
  T:SetEditMode(false)
  check(not (T:Reflow(true) or {}).panel, "chevauchement voulu par le joueur : pas de reflow")
  fake("ChatFrame1", 30, 75, 420, 260)                -- le chat change de taille → obstacle nouveau
  moved = T:Reflow()
  check(moved and moved.panel, "chat agrandi : le panneau s'écarte quand même")
  T.settings.snapFree = true
  T:ResetMovable("panel")
  T:ResetReflow()

  -- 3d. 21:9
  clearFakes()
  local W, H = 2560, 1080
  UIParent:SetSize(W, H)
  local wide = {
    { "MinimapCluster", W - 200, H - 220, 200, 220 }, { "ObjectiveTrackerFrame", W - 270, H - 740, 250, 500 },
    { "PlayerFrame", 20, H - 110, 232, 100 }, { "ChatFrame1", 30, 95, 430, 180 },
    { "MainMenuBar", W / 2 - 512, 0, 1024, 53 },
  }
  for _, r in ipairs(wide) do fake(r[1], r[2], r[3], r[4], r[5]) end
  check(T.ScreenProfile(W, H) == "21:9", "profil 21:9")
  local inset = T.ScreenInset(W, H)
  check(inset > 100, "21:9 : colonnes rapprochées de la bande 16:9 (" .. inset .. ")")
  pl = T:AutoLayout(true)
  check(pl and pl.panel, "21:9 : panneau placé")
  local pp = pl.panel
  local dEdge = math.min(math.abs(pp.l - inset), math.abs(W - inset - (pp.l + pp.w)))
  check(dEdge < 200, ("21:9 : panneau près du bord de la bande centrale (%d,%d)"):format(pp.l, pp.b))
  check(pp.l > 60 or pp.l + pp.w < W - 60, "21:9 : pas collé à l'extrême bord")
  freeOfReserved(pl, W, H, "21:9")

  -- 3e. 4:3
  clearFakes()
  W, H = 1440, 1080
  UIParent:SetSize(W, H)
  for _, r in ipairs(wide) do
    local x = r[2]
    if r[1] == "MinimapCluster" then x = W - 200 elseif r[1] == "ObjectiveTrackerFrame" then x = W - 270
    elseif r[1] == "MainMenuBar" then x = W / 2 - 512 end
    fake(r[1], x, r[3], r[4], r[5])
  end
  check(T.ScreenProfile(W, H) == "4:3", "profil 4:3")
  pl = T:AutoLayout(true)
  check(pl and pl.panel and pl.panel.width >= 300, "4:3 : panneau placé (≥ 300)")
  freeOfReserved(pl, W, H, "4:3")

  -- 3f. dispositions en un clic
  clearFakes()
  UIParent:SetSize(2000, 1125)
  for _, r in ipairs(shot) do fake(r[1], r[2], r[3], r[4], r[5]) end
  local okc, plc = T:ApplyDisposition("compacte")
  check(okc and T.settings.layoutStyle == "compacte" and T.settings.panel.width == 300 and T.settings.panel.maxLines <= 4,
    "disposition Compacte : panneau 300, 4 lignes")
  check(plc.panel and plc.panel.l + plc.panel.w / 2 > 1000, "Compacte : groupé côté minimap")
  local okk, plk = T:ApplyDisposition("coins")
  local q = plk.panel
  local dc = math.min(q.l, 2000 - q.l - q.w) + math.min(q.b, 1125 - q.b - q.h)
  check(okk and q and dc < 260, ("disposition Coins : panneau dans un coin (%d,%d, d=%d)"):format(q.l, q.b, dc))
  T:HandleSlash("disposition confort")
  check(T.settings.layoutStyle == "confort" and T.settings.panel.width >= 320, "/tai disposition confort")
  for role, r in pairs(T._lastLayout or {}) do
    for _, o in ipairs(shot) do
      check(not overlaps(r, { l = o[2], b = o[3], w = o[4], h = o[5] }), ("confort : %s libre de %s"):format(role, o[1]))
    end
  end
  clearFakes()
  T:ResetInterface()
end
