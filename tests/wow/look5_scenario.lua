-- Harness scenario (5.0, agent LOOK): the V5 look — design tokens, the « actions rapides » strip on a real screen, the
-- redone window (11 guarded categories, ≤ 3 action tiles per page, collapsible sections, search across everything,
-- Réglages façon Leatrix : sous-liste, Affichage, Profil Simple / Complet), the 3-step first-run setup, toasts, the
-- minimap menu. Run by tests/wow/test_look5.py on the real-client geometry, level-30 hunter in a group of 4.
--   LOOK5_MODE=screen : one resolution (--geometry w=,h=) × one UI scale (LOOK5_SCALE) × one default layout
--     (LOOK5_LAYOUT = classic : unit frames top left, PartyMemberFrame1-4 | modern : Edit-Mode places, PartyFrame
--     container). Dumps every TreeAI frame on screen (units and real pixels) and proves: on screen, big enough, over
--     none of the game's frames (player, target + auras, party, tracker, minimap, buffs, Suivi Forever column), nothing
--     clipped inside the strip, window on screen with every page laid out without overlap, still there in combat.
--   LOOK5_MODE=full (default) : everything else at 1920×1080, with the « ce que le joueur voit » dumps.
-- Fake names and quests only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "look5_scenario needs --geometry") then return end
  local S, W, Th, H = T.Shortcuts, T.Window, T.Theme, T.Hub
  if not check(S and W and Th and Th.TOKENS and S.Audit, "Shortcuts + Window + Theme V5 loaded") then return end
  local UIP = UIParent
  local MODE = os.getenv("LOOK5_MODE") or "full"
  local LAYOUT = os.getenv("LOOK5_LAYOUT") or "classic"
  local SCALE = tonumber(os.getenv("LOOK5_SCALE") or "")
  local function out(s) io.write("LOOK5 ", s, "\n") end
  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end
  local function ulen(s) local _, n = tostring(s or ""):gsub("[^\128-\191]", "") return n end
  local function plain(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
  local chat0, b0, v0 = #M.output, #(M.blocked or {}), #M.violations
  rawset(_G, "GetPhysicalScreenSize", function() return geo.opts.w, geo.opts.h end)

  -- ==================================================================== UPGRADE : a player who finished the 4.x wizard
  -- (SavedVariables with onboarded) logs in on 5.0 : the 3-step setup is offered ONCE, at a calm moment, never reset
  -- while he reads it, « Passer » = never again ; the strip is there
  if MODE == "upgrade" then
    local function wz() local w = T:GetWizard() return w and w:IsShown() and w or nil end
    check(T.db.setupV == 5, "5.0 : réglage en 3 étapes proposé une fois (setupV posé)")
    M.Advance(12, 0.2)
    local w = wz()
    if check(w and w.step == 1, "joueur de la 4.x : le réglage en 3 étapes s'ouvre une fois, au calme") then
      out("MISE À JOUR étape 1 : " .. plain(w.title:GetText()))
      w.next:Click("LeftButton")
      M.Advance(8, 0.2)
      check(w.step == 2 and w:IsShown(), "… jamais remis à l'étape 1 pendant qu'il le lit")
      w.skip:Click("LeftButton")
    end
    M.Advance(40, 0.5)
    check(not wz() and T.db.onboarded, "« Passer » : mémorisé, plus jamais rouvert")
    check(S.Strip() and S.Strip():IsShown() and geo.OnScreen(S.Strip(), 2), "la bande est à l'écran")
    for i = chat0 + 1, #M.output do out("chat: " .. M.output[i]) end
    check(#M.output == chat0 and #(M.blocked or {}) == b0 and #M.violations == v0, "rien dans le chat, rien de bloqué")
    return
  end

  -- ------------------------------------------------------------------ world: level-30 hunter in a group of 4
  M.LeaveCombat()
  M.player.level = 30
  M.quests = {
    { header = "Les Tarides" },
    { id = 801, title = "La revanche de test", level = 28, complete = true, objectives = {} },
    { id = 802, title = "Crocs de test", level = 27, complete = true, objectives = {} },
    { id = 803, title = "Plumes de test", level = 28, objectives = { { text = "Plume : 2/6", done = false, have = 2, need = 6 } } },
  }
  M.group = {
    { name = "Amie", class = "PRIEST", level = 28, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 29, online = true },
    { name = "Camarade", class = "MAGE", level = 30, online = true },
  }
  if LAYOUT == "modern" then
    -- recent client: no PartyMemberFrameN, one PartyFrame container, unit frames where Edit Mode puts them
    for i = 1, 4 do rawset(_G, "PartyMemberFrame" .. i, nil) end
    local pf = rawget(_G, "PartyFrame") or CreateFrame("Frame", "PartyFrame", UIP)
    pf.__parent, pf.__w, pf.__h, pf.__shown = UIP, 120, 3 * 61, true
    pf.__points = { { "TOPLEFT", UIP, "TOPLEFT", 22, -160 } }
    PlayerFrame.__points = { { "BOTTOMRIGHT", UIP, "BOTTOM", -300, 250 } }
    TargetFrame.__points = { { "BOTTOMLEFT", UIP, "BOTTOM", 300, 250 } }
  else
    for i = 1, 4 do
      local f = rawget(_G, "PartyMemberFrame" .. i)
      if f then
        f.__parent, f.__w, f.__h, f.__shown = UIP, 128, 53, i <= 3
        f.__points = { { "TOPLEFT", UIP, "TOPLEFT", 10, -160 - (i - 1) * 64 } }
      end
    end
    local pet = rawget(_G, "PetFrame")
    if pet then pet.__parent, pet.__w, pet.__h, pet.__shown = UIP, 128, 53, true pet.__points = { { "TOPLEFT", PlayerFrame, "TOPLEFT", 80, -60 } } end
  end
  -- Shortest Path Forever's « Suivi Forever » column (left side) : another addon's frame, read only
  local spf = CreateFrame("Frame", "ShortestPathForeverTrackerFrame", UIP)
  spf.__parent, spf.__w, spf.__h, spf.__shown = UIP, 250, 260, true
  spf.__points = { { "TOPLEFT", UIP, "TOPLEFT", 150, -170 } }
  M.target = { exists = true, name = "Hyène de test", level = 27, health = 500, healthMax = 500, hostile = true }
  if SCALE then geo.SetUIScale(SCALE) end
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("PLAYER_TARGET_CHANGED")
  ctx.fire("QUEST_LOG_UPDATE")
  -- (profil d'un nouveau joueur) la visite guidée du 1er lancement a ses propres tests : ici elle se tait
  if T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
  M.Advance(8, 0.1)
  -- the first-run setup is tested below (full mode) ; here it must not sit over the measurements
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end

  local GAME = { "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "PartyFrame", "ObjectiveTrackerFrame", "MinimapCluster", "BuffFrame", "MainMenuBar",
    "MultiBarBottomLeft", "CastingBarFrame", "ShortestPathForeverTrackerFrame" }
  local function overlap(a, b) return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h end
  local function px() return geo.scale * geo.opts.h / 768 end   -- real pixels per UI unit
  local function describe(name)
    local f = rawget(_G, name)
    if not (f and f:IsShown()) then return nil end
    local r = geo.Rect(f)
    if not r then return nil end
    local _, SH = geo.Screen()
    return ("%s : gauche %d, haut %d, %d×%d unités = %d×%d px réels"):format(name, r0(r.l), r0(SH - r.b - r.h), r0(r.w), r0(r.h),
      r0(r.w * px()), r0(r.h * px())), r
  end
  local function clearOfGame(name, tag)
    local f = rawget(_G, name)
    local r = f and f:IsShown() and geo.Rect(f)
    if not check(r, tag .. " : " .. name .. " affiché et mesurable") then return end
    check(geo.OnScreen(f, 2), tag .. " : " .. name .. " entièrement à l'écran")
    for _, n in ipairs(GAME) do
      local g = rawget(_G, n)
      local gr = g and g:IsShown() and geo.Rect(g)
      if gr then check(not overlap(r, gr), ("%s : %s pas sur %s (%d,%d %d×%d)"):format(tag, name, n, r0(gr.l), r0(gr.b), r0(gr.w), r0(gr.h))) end
    end
    local tf = rawget(_G, "TargetFrame")
    local tr = tf and geo.Rect(tf)
    if tr then check(not overlap(r, { l = tr.l, b = tr.b - 40, w = tr.w, h = 40 }), tag .. " : " .. name .. " pas sur les auras de la cible") end
    return r
  end
  local function stripInside(tag)
    local strip = S.Strip()
    local r = geo.Rect(strip)
    local list = {}
    for id, b in pairs(S.Buttons()) do
      if b:IsShown() then
        local br = geo.Rect(b)
        if check(br, tag .. " : bouton " .. id .. " mesurable") then
          check(br.l >= r.l - 0.5 and br.b >= r.b - 0.5 and br.l + br.w <= r.l + r.w + 0.5 and br.b + br.h <= r.b + r.h + 0.5,
            tag .. " : « " .. id .. " » entier dans la bande")
          list[#list + 1] = { id = id, r = br }
          -- the word under the icon fits its button (never cut)
          local lbl = b.label and b.label:IsShown() and b.label:GetText()
          if lbl and b.GetWidth then
            check(ulen(lbl) * 5.6 <= b:GetWidth() + 0.5, ("%s : le mot « %s » tient dans son bouton (%d)"):format(tag, lbl, r0(b:GetWidth())))
          end
        end
      end
    end
    for i = 1, #list do
      for j = i + 1, #list do
        local a = list[i].r
        check(not overlap({ l = a.l + 0.5, b = a.b + 0.5, w = a.w - 1, h = a.h - 1 }, list[j].r),
          tag .. " : « " .. list[i].id .. " » et « " .. list[j].id .. " » séparés")
      end
    end
    return #list
  end
  local function layOK(Ly, what)
    local r = Ly and Ly.rects or {}
    for i = 1, #r do
      for j = i + 1, #r do
        local a, b = r[i], r[j]
        if a.x < b.x + b.w - 0.5 and b.x < a.x + a.w - 0.5 and a.y < b.y + b.h - 0.5 and b.y < a.y + a.h - 0.5 then
          return false, ("%s : « %s » (%s) chevauche « %s » (%s)"):format(what, a.label, a.kind, b.label, b.kind)
        end
      end
    end
    for _, a in ipairs(r) do
      if a.x + a.w > Ly.W + 0.5 or a.x < -0.5 then return false, what .. " : « " .. a.label .. " » sort de la colonne" end
    end
    return true
  end
  local function allPages()
    local ids, seen = {}, {}
    for _, p in ipairs(W.NavPages()) do
      if not seen[p.id] then seen[p.id] = true ids[#ids + 1] = p.id end
      for _, c in ipairs(W.SUBS[p.id] or {}) do if W.pageIndex[c] and not seen[c] then seen[c] = true ids[#ids + 1] = c end end
    end
    return ids
  end
  local function hygiene()
    for i = chat0 + 1, #M.output do out("chat: " .. M.output[i]) end
    check(#M.output == chat0, "rien dans le chat (" .. (#M.output - chat0) .. " lignes)")
    check(#(M.blocked or {}) == b0, "aucune action bloquée")
    for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].kind) .. " " .. tostring(M.violations[i].msg)) end
    check(#M.violations == v0, "aucune violation")
  end

  -- ==================================================================== SCREEN : one resolution × scale × layout
  if MODE == "screen" then
    local SW, SH = geo.Screen()
    local tag = ("%dx%d éch. %.2f %s"):format(geo.opts.w, geo.opts.h, geo.scale, LAYOUT)
    out(("ÉCRAN %s : interface %d×%d unités, %.2f px réel par unité"):format(tag, r0(SW), r0(SH), px()))
    local strip = S.Strip()
    check(strip and strip:IsShown(), tag .. " : bande affichée")
    local r = clearOfGame("TreeAIStrip", tag)
    if r then
      out("  " .. describe("TreeAIStrip") .. (S.compact and " (compact)" or ""))
      -- impossible to miss: a button is at least 26 real px (compact) / 36 (words), and never the size of 4.3.6 (27 px strip)
      local bh = (S.compact and Th.TOKENS.tile.compact or Th.TOKENS.tile.h) * strip:GetScale() * px()
      check(bh >= (S.compact and 26 or 36), ("%s : bouton de %d px réels de haut (≥ %d)"):format(tag, r0(bh), S.compact and 26 or 36))
      check(r.l + r.w / 2 < SW * 0.62 and r.b + r.h / 2 > SH * 0.4, tag .. " : à gauche, jamais dans le bas de l'écran")
      -- never in the middle of the screen (the character, the fight)
      check(not overlap(r, { l = SW * 0.38, b = SH * 0.34, w = SW * 0.24, h = SH * 0.30 }), tag .. " : jamais au milieu de l'écran")
    end
    check(stripInside(tag) >= 9, tag .. " : au moins 9 boutons")
    local bad = S.Audit()
    check(#bad == 0, tag .. " : chaque bouton a une action et une infobulle (" .. table.concat(bad, " ; ") .. ")")
    out("  bande : " .. S.DumpLine())
    for _, n in ipairs({ "TreeAICompassFrame", "TreeAIMinimapButton" }) do
      local d = describe(n)
      if d then out("  " .. d) end
    end
    local cf = rawget(_G, "TreeAICompassFrame")
    if cf and cf:IsShown() and r then
      local cr = geo.Rect(cf)
      check(cr and not overlap(r, cr), tag .. " : la bande n'est pas sur la boussole")
    end
    -- the group changes, the target goes: nothing under the strip → it stays where it is
    local p1 = { strip:GetPoint(1) }
    M.target = { exists = false }
    ctx.fire("PLAYER_TARGET_CHANGED")
    ctx.fire("GROUP_ROSTER_UPDATE")
    M.Advance(3, 0.1)
    local p2 = { strip:GetPoint(1) }
    check(p1[1] == p2[1] and r0(p1[4]) == r0(p2[4]) and r0(p1[5]) == r0(p2[5]), tag .. " : place stable (cible / groupe qui changent)")
    -- combat: still there (nothing protected), clicks work
    M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
    M.Advance(2, 0.1)
    check(strip:IsShown() and geo.OnScreen(strip, 2), tag .. " : bande à l'écran en combat")
    M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
    M.Advance(1, 0.1)
    -- one alert: near the top, one line, 8 to 15 s
    T:ShowAlert({ id = "look5", text = "Sacs pleins : vends ou range avant de repartir", prio = 3 })
    M.Advance(0.5, 0.1)
    local al = T:GetAlert()
    check(al and al:IsShown() and geo.OnScreen(al, 2), tag .. " : alerte à l'écran")
    if al and al:IsShown() then out("  " .. (describe("TreeAIAlert") or "TreeAIAlert ?")) al:Hide() end
    -- the window: first time on the right, fully on screen, every page laid out
    W.Open("quests")
    M.Advance(0.5, 0.1)
    local win = W.GetWindow()
    local wr = win and geo.Rect(win)
    if check(wr and geo.OnScreen(win, 2), tag .. " : fenêtre entièrement à l'écran") then
      out("  " .. describe("TreeAIMainWindow"))
      check(wr.l + wr.w / 2 > SW / 2 - 1, tag .. " : fenêtre sur la moitié droite (1re ouverture)")
      check(wr.w >= 640 and wr.h >= 420, tag .. " : fenêtre d'au moins 640×420")
    end
    local nPages = 0
    for _, id in ipairs(allPages()) do
      W.Select(id)
      local ok, why = layOK(W.lastLay, tag .. " page " .. id)
      check(ok, why)
      check(W.lastLay.col >= 240, ("%s page %s : colonne de %d unités (≥ 240)"):format(tag, id, r0(W.lastLay.col)))
      nPages = nPages + 1
    end
    out(("  fenêtre : %d pages dessinées sans chevauchement, navigation de %d catégories"):format(nPages, W.navCount or 0))
    check((W.navCount or 0) >= 8 and (W.navCount or 99) <= 11, tag .. " : 8 à 11 catégories (" .. tostring(W.navCount) .. ")")
    W.Close()
    M.Advance(0.5, 0.1)
    hygiene()
    return
  end

  -- ==================================================================== FULL (1920×1080)
  -- ---------------------------------------------------------------- 1. design tokens
  for _, l in ipairs(Th:Describe()) do out("JETON " .. l) end
  local K = Th.TOKENS
  check(K.grid == 4 and K.border == 1, "grille de 4 px, liseré 1 px")
  for _, v in pairs(K.space) do check(v == 2 or v % 4 == 0, "espace sur la grille (" .. v .. ")") end
  local _, _, _, aBg = Th:Color("bg")
  local _, _, _, aSmall = Th:Color("small")
  check(aBg >= 0.95, "menus et fenêtres opaques (alpha " .. aBg .. ")")
  check(aSmall >= 0.35 and aSmall <= 0.65, "petits cadres à moitié transparents (alpha " .. aSmall .. ")")
  for _, role in ipairs({ "guide", "turnin", "pickup", "next", "zone", "fast", "bags", "gear", "prof", "group", "window", "settings" }) do
    check(type(Th.ICONS[role]) == "string" and Th.ICONS[role]:find("^Interface\\"), "icône du jeu pour « " .. role .. " »")
  end
  for _, fn in ipairs({ "ActionButton", "Pill", "CountBadge", "Card", "Legend", "LegendLines", "PaintAction", "ToastSeconds", "Describe" }) do
    check(type(Th[fn]) == "function", "Theme:" .. fn)
  end
  do   -- one button, its states
    local b = Th:ActionButton(UIP, { name = "TreeAILook5TestButton", icon = Th.ICONS.bags, label = "Essai" })
    check(b and b.icon and b.label and b.keyText and b.badge, "bouton d'action : icône + mot + touche + pastille")
    b:Set({ label = "Essai", count = 3, key = "Alt-1", active = true })
    check(b.badge.value == 3 and b.badge:IsShown() and b.keyText:GetText() == "Alt-1" and b.__st.active, "états : nombre, touche, choisi")
    b:Set({ label = "Essai", disabled = true })
    check(b.__st.disabled and not b.badge:IsShown() and b.keyText:GetText() == "", "état coupé ; nombre et touche retirés")
    b:Hide()
    local p = Th:Pill(UIP, { name = "TreeAILook5TestPill", label = "Guidage quêtes" })
    check(p and p.on == true and p.state:GetText() == "ON", "pastille ON")
    p:SetOn(false)
    check(p.on == false and p.state:GetText() == "OFF", "pastille OFF")
    p:Hide()
    local c = Th:Card(UIP, { name = "TreeAILook5TestCard", kind = "small", title = "Carte d'essai", w = 200, h = 60 })
    check(c and c.title and c.title:GetText() == "Carte d'essai" and Th:SkinOf(c) == "small", "carte « small » (sous un cadre du jeu)")
    c:Hide()
    local lines = Th:LegendLines(Th.LEGEND.bags)
    check(#lines == #Th.LEGEND.bags and #lines >= 5, "légende des sacs : " .. #lines .. " couleurs")
    out("LÉGENDE SACS : " .. plain(table.concat(lines, " | ")))
  end

  -- ---------------------------------------------------------------- 2. on screen by default
  out("=== À L'ÉCRAN (1920×1080, groupe de 4, cible, colonne Suivi Forever) ===")
  clearOfGame("TreeAIStrip", "défaut")
  stripInside("défaut")
  for _, n in ipairs({ "TreeAIStrip", "TreeAICompassFrame", "TreeAIMinimapButton" }) do
    local d = describe(n)
    check(d, n .. " à l'écran par défaut")
    if d then out("  " .. d) end
  end
  out("  bande : " .. S.DumpLine())
  for _, n in ipairs({ "TreeAICard", "TreeAIWatchFrame", "TreeAIHorizon", "TreeAIMeter", "TreeAIShortcuts", "TreeAIRadar" }) do
    local f = rawget(_G, n)
    check(not (f and f:IsShown()) or T:ModuleProfile() ~= "sur", n .. " : pas à l'écran par défaut (profil d'un nouveau joueur)")
  end
  for id, b in pairs(S.Buttons()) do
    local t = S.TipLines(id)
    local parts = {}
    for _, l in ipairs(t) do parts[#parts + 1] = l[1] end
    out(("  infobulle « %s » : %s"):format(id, table.concat(parts, " / ")))
    check(#t >= 2 and b:GetScript("OnClick") and b:GetScript("OnEnter"), "bouton « " .. id .. " » : action + infobulle")
  end

  -- (re)created and shown DURING a fight (a /reload in combat, the module switched back on): nothing waits for the
  -- end of the fight — plain frames, nothing protected
  T:SetModuleEnabled("Shortcuts", false)
  M.Advance(0.5, 0.1)
  check(not S.Strip():IsShown(), "module coupé : bande cachée")
  M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
  T:SetModuleEnabled("Shortcuts", true)
  M.Advance(3, 0.1)
  check(S.Strip():IsShown() and geo.OnScreen(S.Strip(), 2), "rallumée en plein combat : la bande est là tout de suite")
  clearOfGame("TreeAIStrip", "en combat")
  M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(1, 0.1)

  -- ---------------------------------------------------------------- 3. toasts: one at a time, 8-15 s
  T:ShowAlert({ id = "look5:a", text = "Sacs pleins : vends ou range avant de repartir", prio = 3 })
  T:ShowAlert({ id = "look5:b", text = "Quête finie : La revanche de test — va la rendre", prio = 3 })
  M.Advance(0.3, 0.1)
  local al = T:GetAlert()
  if check(al and al:IsShown(), "alerte affichée") then
    check(al.lines[1]:IsShown() and not al.lines[2]:IsShown(), "une alerte à la fois (la plus récente)")
    check(al.lines[1]:GetText():find("Quête finie", 1, true), "… la plus récente : " .. tostring(al.lines[1]:GetText()))
    check(al.left >= 7.5 and al.left <= 15, "durée de 8 à 15 s (" .. tostring(al.left) .. ")")
    local _, SH = geo.Screen()
    local ar = geo.Rect(al)
    check(ar and ar.b > SH * 0.5, "alerte dans la moitié haute de l'écran (jamais au centre)")
    local cf = rawget(_G, "TreeAICompassFrame")
    local cr = cf and cf:IsShown() and geo.Rect(cf)
    if cr and ar then
      check(math.abs((ar.l + ar.w / 2) - (cr.l + cr.w / 2)) < 3 and ar.b + ar.h <= cr.b and cr.b - (ar.b + ar.h) < 12,
        ("alerte juste sous la boussole (alerte %d,%d %dx%d ; boussole %d,%d %dx%d)"):format(r0(ar.l), r0(ar.b), r0(ar.w), r0(ar.h), r0(cr.l), r0(cr.b), r0(cr.w), r0(cr.h)))
      local sr = geo.Rect(S.Strip())
      check(not overlap(ar, sr), "alerte : pas sur la bande")
    end
    out("  alerte : « " .. al.lines[1]:GetText() .. " » — " .. tostring(describe("TreeAIAlert")))
    check(Th:SkinOf(al) == "alert", "alerte au style du thème")
    M.Advance(16, 0.5)
    check(not al:IsShown(), "alerte effacée toute seule")
  end
  check(Th:ToastSeconds("court") == 8 and Th:ToastSeconds(("x"):rep(400)) == 15, "durée bornée 8-15 s")

  -- ---------------------------------------------------------------- 4. the window
  out("=== FENÊTRE ===")
  W.Open()
  M.Advance(0.5, 0.1)
  local win = W.GetWindow()
  local SW, SH = geo.Screen()
  local wr = geo.Rect(win)
  check(win:IsShown() and geo.OnScreen(win, 2) and wr.l + wr.w / 2 > SW / 2, "fenêtre : 1re ouverture à droite, à l'écran")
  check(W.CurrentPage() == "quests", "elle ouvre sur Quêtes")
  W.Open("settings")
  check(W.CurrentPage() == "display" and W.Resolve("reglages") == "display", "Réglages ouvre « Affichage » (1re fois)")
  W.Open("quests")
  local nav = W.NavPages()
  local names = {}
  for _, p in ipairs(nav) do
    names[#names + 1] = p.label
    check(type(p.icon) == "string" and p.icon ~= "", "catégorie « " .. p.label .. " » : une icône")
    check(ulen(p.label) * 6.6 <= W.NAV_W - 16 - 32 - 22 + 1, "catégorie « " .. p.label .. " » : le nom tient dans la colonne")
  end
  out("navigation : " .. table.concat(names, " · "))
  check(#nav <= 11 and #nav >= 9, "11 catégories au plus (" .. #nav .. ")")
  check(names[1] == "Quêtes" and names[#names] == "Réglages", "Quêtes d'abord, Réglages à la fin")
  -- guarded: « Sacs » only once its module registered a page
  local hasBags = W.pageIndex.bags ~= nil
  if not hasBags then
    check(W.Resolve("bags") == "gear" and W.Resolve("sacs") == "gear", "page Sacs pas encore inscrite : redirigée vers Équipement")
    -- (inscrite comme avant la 5.0 : sous-page cachée de l'HV — la navigation lui donne quand même sa place)
    W.AddPage({ id = "bags", hidden = true, parent = "hv", label = "Sacs", icon = Th.ICONS.bags, title = "Sacs", desc = "Page d'essai",
      top = function() return { { "Vendre (sûr)", function() end, "essai", icon = Th.ICONS.bags, sub = "3 objets", count = 3 },
        { "Ranger", function() end, "essai" }, { "Réparer", function() end, "essai" }, { "Quatrième", function() end, "essai" } } end,
      render = function(Ly) Ly.note("Note d'essai.", "info") Ly.card("Carte", { "Ligne 1", "Ligne 2" }) Ly.kv("Places libres", "12")
        Ly.legend(Th.LEGEND.bags) Ly.toggle({ id = "t", label = "Bascule d'essai", desc = "Une ligne d'explication", on = true, set = function() end }) end })
    W.Render()
    local n2 = W.NavPages()
    local found = false
    for _, p in ipairs(n2) do if p.id == "bags" then found = true end end
    check(found and W.navCount == #nav + 1 and W.navCount <= 11, "page Sacs inscrite par son module : elle entre dans la navigation (" .. tostring(W.navCount) .. ")")
    W.Select("bags")
    local n = 0
    for _, r in ipairs(W.lastLay.rects) do if r.kind == "action" then n = n + 1 end end
    check(n == 3, "au plus 3 tuiles d'action en tête de page (" .. n .. ")")
    local ok, why = layOK(W.lastLay, "page d'essai (widgets V5)")
    check(ok, why)
    check(W.Resolve("sacs") == "bags" and W.Resolve("inventaire") == "bags", "alias français : sacs, inventaire")
  end
  -- redirects for old page ids
  local RED = { now = "quests", perso = "gear", binds = "keys", journal = "journal", tools = "tools", macros = "macros", keys = "keys",
    options = "options", display = "display", affichage = "display",
    touches = "keys", profil = "profile", metiers = "prof", trajet = "route", groupe = "group" }
  for old, new in pairs(RED) do
    if W.pageIndex[new] then
      check(W.Resolve(old) == new, ("ancien id « %s » → « %s » (%s)"):format(old, new, tostring(W.Resolve(old))))
      W.Open(old)
      check(W.IsOpen() and W.CurrentPage() == new, "W.Open(« " .. old .. " ») ouvre « " .. new .. " »")
    end
  end
  for _, id in ipairs({ "journal", "brain", "pet", "talents", "macros", "keys", "spellkeys", "display", "profile", "modules", "addons", "options", "tools" }) do
    if W.pageIndex[id] then check(W.NavOf(id) ~= id and W.pageIndex[W.NavOf(id)], "sous-page « " .. id .. " » rangée sous « " .. tostring(W.NavOf(id)) .. " »") end
  end
  -- every page: no overlap, ≤ 3 action tiles, a dump of what the player reads
  for _, id in ipairs(allPages()) do
    W.Select(id)
    local Ly = W.lastLay
    local ok, why = layOK(Ly, "page " .. id)
    check(ok, why)
    local n, first = 0, {}
    for _, r in ipairs(Ly.rects) do
      if r.kind == "action" then n = n + 1 end
      if #first < 9 and r.kind ~= "side" and r.label ~= "" then first[#first + 1] = "[" .. r.kind .. "] " .. plain(r.label):gsub("\n", " / ") end
    end
    check(n <= 3, "page " .. id .. " : au plus 3 tuiles d'action (" .. n .. ")")
    local p = W.pageIndex[id]
    out(("PAGE %s — « %s » · %s"):format(id, tostring(win.pageTitle:GetText()), plain(tostring(win.pageSub:GetText() or ""))))
    for _, l in ipairs(first) do out("    " .. l) end
    check(win.pageIcon:GetTexture() ~= nil, "page " .. id .. " : icône d'en-tête")
    check(p and plain(win.pageTitle:GetText() or "") ~= "", "page " .. id .. " : titre")
  end
  -- Réglages façon Leatrix : sous-liste à gauche, bascules avec une ligne d'explication
  W.Open("display")
  check(W.NavOf(W.Resolve("settings")) == "settings", "Réglages rouvre sa dernière sous-page")
  local side, toggles = {}, 0
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "side" then side[#side + 1] = r.label check(r.x + r.w <= W.lastLay.pad, "sous-liste à gauche de la page") end
    if r.kind == "toggle" then toggles = toggles + 1 check(r.x >= W.SIDE_W + 8, "bascules à droite de la sous-liste") end
  end
  out("Réglages, sous-liste : " .. table.concat(side, " · "))
  check(#side >= 6 and side[1] == "Affichage", "sous-liste de 6 entrées ou plus, Affichage d'abord (" .. #side .. ")")
  check(toggles >= 8, "Affichage : une bascule par élément d'écran (" .. toggles .. " visibles)")
  local st = W.DisplayState()
  local dl = {}
  for _, d in ipairs(st) do dl[#dl + 1] = d.label .. " = " .. (d.on and "à l'écran" or "coupé") end
  out("AFFICHAGE : " .. table.concat(dl, " | "))
  check(#st >= 15, "Affichage liste chaque élément d'écran (" .. #st .. ")")
  for _, d in ipairs(W.DISPLAY) do
    check(type(d.preview) == "string" and ulen(d.preview) <= 90 and not d.preview:find("\n"), "« " .. d.label .. " » : une ligne d'explication")
  end
  -- a toggle really toggles (strip off / on, compact, guide)
  local function disp(id) for _, d in ipairs(W.DISPLAY) do if d.id == id then return d end end end
  disp("strip").set(false)
  M.Advance(0.3, 0.1)
  check(not S.Strip():IsShown() and not disp("strip").on(), "Affichage : la bande se coupe")
  disp("strip").set(true)
  M.Advance(0.5, 0.1)
  check(S.Strip():IsShown() and disp("strip").on(), "Affichage : la bande revient")
  disp("stripCompact").set(true)
  M.Advance(0.3, 0.1)
  check(S.compact == true, "Affichage : mode compact")
  disp("stripCompact").set(false)
  M.Advance(0.3, 0.1)
  disp("guide").set(false)
  check(not S.GuideOn() and not disp("guide").on(), "Affichage : le guidage des quêtes se coupe d'une case")
  disp("guide").set(true)
  check(S.GuideOn(), "… et se rallume")
  -- Profil : Simple / Complet, one sentence each
  W.Select("profile")
  local ch = {}
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "choice" then ch[#ch + 1] = r.label end end
  check(#ch == 2 and ch[1] == "Simple" and ch[2] == "Complet", "Profil : Simple / Complet (" .. table.concat(ch, ", ") .. ")")
  for _, pr in ipairs(W.PROFILES) do
    local _, dots = pr.text:gsub("%.", "")
    check(ulen(pr.text) <= 170 and dots <= 2, "profil « " .. pr.label .. " » expliqué en une phrase")
    out("PROFIL " .. pr.label .. " : " .. pr.text)
  end
  local before = W.Profile()
  check(W.SetProfile("sur") and W.Profile() == "sur", "profil Simple posé")
  check(W.SetProfile("tout") and W.Profile() == "tout", "profil Complet posé")
  W.SetProfile(before)
  -- search: any action, any setting
  local function hits(q)
    win.search:SetText(q)
    if win.search.__scripts.OnTextChanged then win.search.__scripts.OnTextChanged(win.search) end
    local h = W.SearchHits(W.CurrentPage())
    local t = {}
    for _, e in ipairs(h) do t[#t + 1] = plain(e.text) end
    return h, table.concat(t, " | ")
  end
  W.Select("quests")
  local SEARCH = {
    { "rendre", "Action : Rendre mes quêtes" }, { "sacs", "Action : Sacs" }, { "boussole", "Affichage : Boussole" },
    { "compact", "Affichage : Bande en mode compact" }, { "guidage", "Action : guidage des quêtes" }, { "simple", "Profil : Simple" },
    { "touches", "Page : Réglages → Touches" }, { "verrouiller", "Réglage : " }, { "combat", "Affichage : Cacher la bande en combat" },
    { "equipement", "Action : Équipement" }, { "erreurs", "Outil : Erreurs" },
  }
  for _, q in ipairs(SEARCH) do
    local _, txt = hits(q[1])
    out(("RECHERCHE « %s » : %s"):format(q[1], txt))
    check(txt:find(q[2], 1, true), ("recherche « %s » trouve « %s »"):format(q[1], q[2]))
    local ok, why = layOK(W.lastLay, "recherche " .. q[1])
    check(ok, why)
  end
  local h = hits("rendre")
  S.last = nil
  for _, e in ipairs(h) do if plain(e.text):find("Action : Rendre", 1, true) then e.onClick() end end
  check(S.last == "rendre", "un clic sur le résultat FAIT l'action")
  h = hits("zzzz")
  check(#h == 0, "recherche sans résultat : rien")
  win.search:SetText("")
  if win.search.__scripts.OnTextChanged then win.search.__scripts.OnTextChanged(win.search) end
  -- remembers place and size ; /tai interface reset forgets them
  geo.Drag(win.nav, -400, -60)
  local a1 = { win:GetPoint(1) }
  W.Close()
  M.Advance(0.5, 0.1)
  W.Open()
  local a2 = { win:GetPoint(1) }
  check(a1[1] == a2[1] and r0(a1[4]) == r0(a2[4]) and r0(a1[5]) == r0(a2[5]), "fenêtre : rouvre où le joueur l'a laissée")
  W.ResetPlace()
  wr = geo.Rect(win)
  check(wr.l + wr.w / 2 > SW / 2, "W.ResetPlace : revenue à droite")
  -- reopened during its closing fade: stays open
  W.Close()
  M.Advance(0.05, 0.05)
  check(Th:IsAnimating(win), "fermeture : fondu en cours")
  W.Open("quests")
  M.Advance(0.5, 0.05)
  check(W.IsOpen() and (win:GetAlpha() or 1) > 0.9, "rouverte pendant le fondu de fermeture : elle reste ouverte")
  W.Close()
  M.Advance(0.5, 0.1)

  -- ---------------------------------------------------------------- 5. first-run setup: 3 steps, skippable
  out("=== RÉGLAGE EN 3 ÉTAPES ===")
  T.db.onboarded = nil
  check(T:ShowWizard(1), "réglage ouvert")
  local wz = T:GetWizard()
  local zr = geo.Rect(wz)
  check(wz:IsShown() and geo.OnScreen(wz, 2) and zr.b + zr.h / 2 > SH / 2, "réglage : à l'écran, au-dessus du centre")
  -- never where the bags open (bottom right) nor over the party frames (left)
  check(zr.l > SW * 0.2 and zr.l + zr.w < SW * 0.8 and zr.b > SH * 0.3, "réglage : ni sur les sacs (bas droite) ni sur le groupe (gauche)")
  for step = 1, 3 do
    out(("étape %d — %s"):format(step, plain(wz.title:GetText())))
    out("   " .. plain(wz.body:GetText()):gsub("\n+", " / "))
    local bl = {}
    for _, b in ipairs(wz.groups[step]) do if b:IsShown() then bl[#bl + 1] = "[" .. plain(b:GetText()) .. "]" end end
    out("   boutons : " .. table.concat(bl, " "))
    if step == 1 then
      check(plain(wz.body:GetText()):find("niveau 30", 1, true), "étape 1 : classe et niveau lus dans le jeu")
      check(plain(wz.body:GetText()):find("Spécialisation", 1, true), "étape 1 : spécialisation (lue, ou « inconnue »)")
    elseif step == 2 then
      check(plain(wz.presetHelp:GetText()) ~= "", "étape 2 : une phrase pour le profil choisi")
      check(#bl >= 6, "étape 2 : Simple, Complet + 4 éléments d'écran")
      for _, b in ipairs(wz.groups[2]) do
        if plain(b:GetText()):find("Bande d'actions", 1, true) then
          b:Click("LeftButton")
          M.Advance(0.3, 0.1)
          check(not S.Strip():IsShown() and plain(b:GetText()):find("OFF", 1, true), "étape 2 : la bande se coupe d'un clic")
          b:Click("LeftButton")
          M.Advance(0.5, 0.1)
          check(S.Strip():IsShown() and plain(b:GetText()):find("ON", 1, true), "… et revient")
        end
      end
    else
      check(plain(wz.body:GetText()):find("Alt-1", 1, true), "étape 3 : les touches conseillées")
      for _, cmd in ipairs(S.KEY_ORDER) do M.bindings[cmd] = nil end
      wz.groups[3][1]:Click("LeftButton")
      M.Advance(0.3, 0.1)
      check(M.bindings.TREEAI_QB_TURNIN == "ALT-1", "étape 3 : « Poser les touches conseillées » (touches libres seulement)")
    end
    wz.next:Click("LeftButton")
  end
  check(not wz:IsShown() and T.db.onboarded, "terminé : mémorisé, plus jamais rouvert tout seul")
  T.db.onboarded = nil
  T:ShowWizard(1)
  wz.skip:Click("LeftButton")
  check(not wz:IsShown() and T.db.onboarded, "« Passer » à tout moment : mémorisé")
  -- with the gear module's spec (contract T.Gear.Spec) : shown with its source
  local oldGear = T.Gear
  T.Gear = setmetatable({ Spec = function() return { class = "HUNTER", tree = 2, name = "Précision", source = "api", conf = 1 } end },
    { __index = oldGear })
  local _, _, spec, src = T._WizardCharacter()
  check(spec == "Précision" and src == "lue dans le jeu", "étape 1 : spécialisation du module Équipement, avec sa source")
  T.Gear = oldGear

  -- ---------------------------------------------------------------- 6. minimap menu: quick actions first
  if H and H.Entries then
    out("=== MENU DE LA PASTILLE ===")
    H.mode = "main"
    local list = H.Entries()
    local groups, cur, names2 = {}, nil, {}
    for _, e in ipairs(list) do
      if e.group ~= cur then cur = e.group groups[#groups + 1] = cur end
      if e.group == "rapide" then names2[#names2 + 1] = plain(e.title) .. (e.state and (" [" .. plain(e.state) .. "]") or "") end
      check(type(e.fn) == "function" and type(e.tip) == "string" and e.tip ~= "", "menu : « " .. tostring(e.title) .. " » a une action et une infobulle")
    end
    out("groupes : " .. table.concat(groups, " · "))
    out("actions rapides : " .. table.concat(names2, " · "))
    check(groups[1] == "rapide" and names2[1] and names2[1]:find("Guidage des quêtes [ON]", 1, true), "menu : les actions rapides et le guidage d'abord")
    for _, e in ipairs(list) do if e.id == "guide" then e.fn() end end
    check(not S.GuideOn(), "menu : un clic coupe le guidage")
    S.SetGuide(true)
  end

  -- ---------------------------------------------------------------- 7. keys
  for _, cmd in ipairs(S.KEY_ORDER) do
    check(type(rawget(_G, "BINDING_NAME_" .. cmd)) == "string", "raccourci nommé : " .. cmd)
  end
  local keys = T._WizardKeys()
  check(#keys == 10, "10 touches conseillées montrées")
  hygiene()
end
