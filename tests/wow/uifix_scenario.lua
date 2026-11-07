-- Harness scenario (UIFIX, round 5): the first in-game screenshot of 3.9.0 (2000×1125, FR client) rebuilt with
-- mock frame rectangles — Details top-left, party frames left, Questie tracker left, minimap + objective tracker
-- right, chat bottom-left, action bars bottom. Asserts: nothing TreeAI overlaps them (radar next to the
-- minimap, never on the tracker; panel away from the party frames), 4 tabs that never overlap (+ « Plus »
-- sub-menu), lines on 2 lines instead of « … », min width 300, one waypoint arrow, one-shot re-placement for
-- existing users (manual places kept), « Réinitialiser l'interface », course auto, radar legend/zoom, Apparence.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/uifix_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.inCombat = false
  if T.ApplyPreset then T:ApplyPreset("normal") end
  if T.SetProfile then T:SetProfile("account") end

  -- 1. screenshot layout (rects in UIParent units: left, bottom, width, height)
  UIParent:SetSize(2000, 1125)
  local fakes = {}
  local function fake(name, l, b, w, h, hidden)
    local f = CreateFrame("Frame", name, UIParent)
    fakes[#fakes + 1] = f
    f.GetRect = function() return l, b, w, h end
    if hidden then f:Hide() end
    _G[name] = f
    return f
  end
  local real = {
    { "MinimapCluster", 1750, 863, 245, 262 },
    { "ObjectiveTrackerFrame", 1745, 335, 255, 525 },
    { "Questie_BaseFrame", 25, 325, 255, 510 },
    { "PartyMemberFrame1", 20, 935, 115, 55 },
    { "PartyMemberFrame2", 20, 875, 115, 55 },
    { "PartyMemberFrame3", 20, 815, 115, 55 },
    { "DetailsBaseFrame1", 0, 995, 375, 130 },
    { "ChatFrame1", 30, 75, 420, 170 },
    { "MainMenuBar", 435, 0, 1120, 50 },
    { "MultiBarBottomLeft", 730, 250, 525, 45 },
    { "PlayerFrame", 545, 245, 180, 65 },
    { "BuffFrame", 1560, 1060, 180, 60 },
  }
  for _, r in ipairs(real) do fake(r[1], r[2], r[3], r[4], r[5]) end
  fake("PartyMemberFrame4", 20, 755, 115, 55, true)      -- 4th member not there yet: reserved anyway
  fake("LibDBIcon10_Questie", 1745, 1000, 30, 30)        -- minimap button (child of the minimap)

  local placed = T:AutoLayout(true)
  check(placed and placed.panel and placed.alert and placed.radar, "placement : panneau, encadré, radar placés")
  local function overlaps(a, b)
    return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h
  end
  -- against the REAL rects of the screenshot (not only the scan) and against each other
  local obstacles = {}
  for _, r in ipairs(real) do obstacles[#obstacles + 1] = { name = r[1], l = r[2], b = r[3], w = r[4], h = r[5] } end
  obstacles[#obstacles + 1] = { name = "PartyMemberFrame4", l = 20, b = 755, w = 115, h = 55 }
  for role, r in pairs(placed) do
    for _, o in ipairs(obstacles) do
      check(not overlaps(r, o), ("%s ne chevauche pas %s (%d,%d %dx%d)"):format(role, o.name, r.l, r.b, r.w, r.h))
    end
    for role2, r2 in pairs(placed) do
      if role2 > role then check(not overlaps(r, r2), role .. " ne chevauche pas " .. role2) end
    end
  end
  for _, o in ipairs(T:ScanScreen().rects) do
    for role, r in pairs(placed) do check(not overlaps(r, o), ("%s libre de %s (scan)"):format(role, tostring(o.name))) end
  end
  local rd = placed.radar
  check(rd.l + rd.w <= 1750 and rd.b + rd.h > 863, ("radar à gauche de la minimap (%d,%d)"):format(rd.l, rd.b))
  local p = placed.panel
  local pcx = p.l + p.w / 2
  check(p.width >= 300, "panneau ≥ 300 de large (" .. tostring(p.width) .. ")")
  check(pcx < 2000 * 0.3 or pcx > 2000 * 0.7, "panneau sur un bord, le centre reste au jeu")
  check(p.l > 135 + 160 or p.b + p.h < 755 - 160, ("panneau loin des cadres de groupe (%d,%d)"):format(p.l, p.b))
  if placed.compass then
    local c = placed.compass
    check(math.abs(c.l + c.w / 2 - 1000) < 300 and c.b > 900, "boussole en haut au centre")
  end
  local a = placed.alert
  check(a.b + a.h <= 863 and a.l + a.w > 1300, ("encadré sous la minimap / côté droit (%d,%d)"):format(a.l, a.b))
  local R = T.modules.Radar
  check(R and R.char and R.char.point and R.char.point:find("TOP"), "radar : position mémorisée (" .. tostring(R.char.point) .. ")")

  -- 2. tabs: 4 visible, never overlapping, text fits its box
  local panel = T:GetPanel() or T:CreatePanel()
  T:ShowPanel()
  T:SetMini(false)
  T.settings.panel.width = 200   -- too narrow: min 300
  T:SelectTab("now")
  check(panel:GetWidth() >= 300, "largeur min 300 (" .. tostring(panel:GetWidth()) .. ")")
  local function noOverlap(rects, what)
    local byRow = {}
    for _, r in ipairs(rects) do byRow[r.row] = byRow[r.row] or {}; table.insert(byRow[r.row], r) end
    local ok = true
    for _, list in pairs(byRow) do
      table.sort(list, function(x, y) return x.x < y.x end)
      for i = 2, #list do if list[i - 1].x + list[i - 1].w > list[i].x then ok = false end end
      for _, r in ipairs(list) do
        if r.textW > r.textMax then ok = false; print("TEXT TOO WIDE", r.id, r.text, r.textW, r.textMax) end
        if r.x + r.w > panel:GetWidth() then ok = false end
      end
    end
    check(ok, what .. " : aucun chevauchement, texte dans sa case")
    return byRow
  end
  local rows = noOverlap(T:GetTabLayout(), "onglets")
  check(rows[0] and #rows[0] == 4 and not rows[1], "4 onglets visibles (« Maintenant », « Quêtes », « Groupe », « Plus »)")
  local ids = {}
  for _, r in ipairs(rows[0]) do ids[#ids + 1] = r.id end
  check(table.concat(ids, ",") == "now,quests,group,__more", "ordre : " .. table.concat(ids, ","))
  T:ToggleMoreMenu(true)
  local all = T:GetTabLayout()
  rows = noOverlap(all, "sous-menu « Plus »")
  local seen = {}
  for _, r in ipairs(all) do seen[r.id] = r end
  check(seen.hv and seen.info and seen["act:reset"] and rows[1], "« Plus » : HV, Infos… + « Réinitialiser l'interface »")
  check(not seen.now or seen.now.row == 0, "onglets principaux pas répétés dans « Plus »")
  T:SelectTab("hv")
  check(T:GetActiveTab() == "hv" and #T:GetTabLayout() > 4, "onglet secondaire actif : sous-menu visible")
  T:SelectTab("now")
  check(not T:IsMoreMenuOpen() and #T:GetTabLayout() == 4, "retour à « Maintenant » : 4 onglets")
  -- the FitLabel helper never lets a label exceed its width
  local fs = panel:CreateFontString()
  local out = T._FitLabel(fs, "Équipementissime", "Équip.", 30)
  check(fs:GetStringWidth() <= 30 and out ~= "", "libellé raccourci / « … » : " .. out)

  -- 3. lines wrap on 2 lines instead of « … »
  T.settings.panel.width = 320
  T:ClearSuggestions()
  T:Suggest("ui:long", "Atrexis le Chevalier de la tombe : il faut aller au nord du camp puis tuer la cible", 2, { sound = false })
  T:Suggest("ui:short", "Court", 1, { sound = false })
  T:RefreshPanel()
  local rl = T:GetRowLayout()
  local two, okGap = false, true
  for i, r in ipairs(rl) do
    if r.lines == 2 then two = true end
    if rl[i + 1] and rl[i + 1].top > r.top - r.h then okGap = false end
  end
  check(two, "texte long sur 2 lignes")
  check(okGap, "les lignes ne se chevauchent pas")

  -- 4. one-shot re-placement for existing users (version flag), manual places kept
  local st = TreeAIDB.chars[T:CharKey()].layout
  st.v = 1
  st.manual.panel = true
  local cp = T:PanelChar()
  cp.point, cp.x, cp.y = "TOPLEFT", 400, -400
  check(T:CheckAutoLayout(), "ancienne version : placement refait une fois")
  check(st.v == T.LAYOUT_VERSION and cp.point == "TOPLEFT" and cp.x == 400, "cadre déplacé à la main : gardé")
  check(not T:CheckAutoLayout(), "ensuite : plus de replacement")

  -- 5. one waypoint arrow only
  local C = T.modules.Compass
  M.superTrack = 4242
  if C_SuperTrack then   -- (legacy clients: no supertracking API)
    check(C.OtherGuide({ questID = 4242, key = "obj:4242" }) == "blizzard", "quête suivie par Blizzard : flèche en double détectée")
  end
  M.superTrack = 0
  check(C.OtherGuide({ questID = 4242, key = "obj:4242" }) == nil, "pas d'autre guide : notre flèche reste")
  C.guide = { key = "obj:7", how = "tomtom" }
  check(C.OtherGuide({ key = "obj:7" }) == "tomtom" and C.OtherGuide({ key = "obj:8" }) == nil, "TomTom posé par nous : même cible seulement")
  C.guide = nil
  check(C.db.hideDup ~= false, "option « une seule flèche » activée par défaut")

  -- 6. « Réinitialiser l'interface »
  T:SetEditMode(true)
  panel.GetRect = function() return 0, 0, 320, 120 end
  T:CommitMove("panel", 703, 404)
  T:SetEditMode(false)
  T:SelectTab("hv")
  check(TreeAIDB.chars[T:CharKey()].movers.panel, "panneau déplacé")
  check(T:ResetInterface(), "réinitialiser l'interface")
  check(not TreeAIDB.chars[T:CharKey()].movers.panel and T:GetActiveTab() == "now" and panel:IsShown(),
    "réinitialisé : place auto, onglet « Maintenant », panneau affiché")
  check(T._lastLayout and T._lastLayout.panel, "réinitialisé : placement auto refait")

  -- 7. course auto (binding du jeu, jamais ToggleAutoRun) : la touche du joueur est lue et respectée
  local B = T.modules.Binds
  if B and B.AutoRunHint then
    local loc = M.locale
    M.locale = "frFR"
    M.bindings.TOGGLEAUTORUN = "`"
    check(B.AutoRunHint() == "Course auto : ²", "touche ² (AZERTY) : " .. B.AutoRunHint())
    check(B.AutoRunStep() == nil, "touche déjà liée : aucune proposition de changer")
    M.bindings.TOGGLEAUTORUN = "NUMLOCK"
    check(B.AutoRunHint() == "Course auto : Verr Num" and B.AutoRunStep() == nil, "Verr Num : gardée")
    M.bindings.TOGGLEAUTORUN = nil
    local step = B.AutoRunStep()
    check(step and step.item.cmd == "TOGGLEAUTORUN", "aucune touche : propose de lier « Course automatique »")
    M.bindings.TOGGLEAUTORUN = "`"
    M.locale = loc
  end

  -- 8. radar : légende, zoom fluide, compact
  if R and R.ShowLegend and R.frame then
    check(type(R.ShowLegend(R.frame)) == "number", "radar : légende au survol")
    R.zoomGoal = 300
    for _ = 1, 40 do R.StepZoom() end
    check(math.abs((R.db.zoom or 0) - 300) < 1, "radar : zoom fluide jusqu'au but")
    R.db.compact = true
    check(R.Size() == 120, "radar compact : 120 px")
    R.db.compact = false
  end

  -- 9. robustesse : 3 résolutions × 4 échelles d'interface, disposition Blizzard par défaut (unités d'UI)
  for _, f in ipairs(fakes) do f:Hide() end
  local function inDB(tbl)
    local seen = {}
    local function walk(t)
      if t == tbl then return true end
      if type(t) ~= "table" or seen[t] then return false end
      seen[t] = true
      for _, v in pairs(t) do if walk(v) then return true end end
      return false
    end
    return walk(TreeAIDB)
  end
  local combos = 0
  for _, res in ipairs({ { 1920, 1080 }, { 2560, 1440 }, { 1366, 768 } }) do
    for _, sc in ipairs({ 0.64, 0.8, 1.0, 1.15 }) do
      local W, H = math.floor(res[1] / sc), math.floor(res[2] / sc)
      UIParent:SetSize(W, H)
      local cx = math.floor(W / 2)
      local layout = {
        { "MinimapCluster", W - 200, H - 220, 200, 220 },
        { "ObjectiveTrackerFrame", W - 270, H - 740, 250, 500 },
        { "BuffFrame", W - 500, H - 80, 290, 70 },
        { "PlayerFrame", 20, H - 110, 232, 100 },
        { "TargetFrame", 260, H - 110, 232, 100 },
        { "PartyMemberFrame1", 10, H - 230, 120, 55 },
        { "PartyMemberFrame2", 10, H - 290, 120, 55 },
        { "PartyMemberFrame3", 10, H - 350, 120, 55 },
        { "PartyMemberFrame4", 10, H - 410, 120, 55 },
        { "ChatFrame1", 30, 95, 430, 180 },
        { "MainMenuBar", cx - 512, 0, 1024, 53 },
        { "MultiBarBottomLeft", cx - 512, 55, 500, 40 },
        { "MultiBarRight", W - 45, math.floor(H / 2) - 250, 40, 500 },
        { "Recount_MainWindow", W - 520, 60, 220, 160 },
      }
      local current = {}
      for _, r in ipairs(layout) do current[#current + 1] = fake(r[1], r[2], r[3], r[4], r[5]) end
      local tag = ("%dx%d@%.2f"):format(res[1], res[2], sc)
      local pl = T:AutoLayout(true)
      check(pl and pl.panel and pl.radar and pl.alert, tag .. " : panneau, radar, encadré placés")
      for role, r in pairs(pl or {}) do
        check(r.l >= 0 and r.b >= 0 and r.l + r.w <= W + 0.5 and r.b + r.h <= H + 0.5,
          ("%s : %s dans l'écran (%d,%d %dx%d)"):format(tag, role, r.l, r.b, r.w, r.h))
        for _, o in ipairs(layout) do
          local ob = { l = o[2], b = o[3], w = o[4], h = o[5] }
          check(not overlaps(r, ob), ("%s : %s ne chevauche pas %s"):format(tag, role, o[1]))
        end
        for role2, r2 in pairs(pl) do
          if role2 > role then check(not overlaps(r, r2), ("%s : %s / %s"):format(tag, role, role2)) end
        end
      end
      if pl and pl.panel then
        check(T:PanelChar().point == pl.panel.point and inDB(T:PanelChar()), tag .. " : place du panneau sauvée (survit à /reload)")
      end
      if pl and pl.radar then check(inDB(R.char) and R.char.point == pl.radar.point, tag .. " : place du radar sauvée") end
      for _, f in ipairs(current) do f:Hide() end
      combos = combos + 1
    end
  end
  check(combos == 12, "12 écrans testés (" .. combos .. ")")
  UIParent:SetSize(2000, 1125)

  -- 10. cadres jamais hors écran, boutons avec infobulle, textes prêts (pas de « nil » / « ? » seul)
  T:AutoLayout(true)
  for _, n in ipairs({ "TreeAIPanel", "TreeAIAlert", "TreeAIRadarFrame", "TreeAICompassFrame", "TreeAITrackerFrame",
    "TreeAITodoFrame", "TreeAIEditBar" }) do
    local f = _G[n]
    if f then check(f:IsClampedToScreen(), n .. " : SetClampedToScreen") end
  end
  T:ShowPanel()
  T:SetMini(false)
  T:ToggleMoreMenu(true)
  local noTip = 0
  for _, ch in ipairs({ panel:GetChildren() }) do
    if ch.__scripts and ch.__scripts.OnClick and not ch.entry and not ch.__scripts.OnEnter then noTip = noTip + 1 end
  end
  check(#{ panel:GetChildren() } > 10, "boutons du panneau trouvés")
  check(noTip == 0, "chaque bouton du panneau a une infobulle (" .. noTip .. " sans)")
  for _, tab in ipairs({ "now", "quests", "group", "info", "hv" }) do
    T:SelectTab(tab)
    for _, e in ipairs(T:GetVisibleLines()) do
      local t = T.PlainText(e.text)
      check(t ~= "?" and t ~= "" and not t:find("%f[%a]nil%f[%A]"), ("onglet %s : texte prêt (%s)"):format(tab, t))
    end
  end
  for _, r in ipairs(T:GetTabLayout()) do
    check(r.textW <= r.textMax and r.x + r.w <= panel:GetWidth(), "onglet " .. tostring(r.id) .. " : texte dans sa case")
  end
  T:SelectTab("now")

  -- 11. page « Apparence »
  local _, _, app = T:GetOptionPages()
  check(app and pcall(app.build), "page d'options « Apparence » construite")
  T:ClearSuggestions()
end
