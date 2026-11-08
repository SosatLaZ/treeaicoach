-- Harness scenario (TIDY): the options window can never overlap. Walks EVERY options page (Accueil, Apparence,
-- Sons, one per module) and the category tree on the left; asserts no two widget rectangles overlap, nothing
-- leaves the column horizontally, every widget really sits where the layout engine recorded it, labels have a
-- fixed width (they wrap instead of running into the next row). Also: the compass hides while dead / ghost.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/tidy_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  M.inCombat = false

  T:InitOptions()
  local canvas, pages, app, sounds, home = T:GetOptionPages()
  check(canvas and home and app and sounds, "options : racine + Accueil + Apparence + Sons")
  canvas:build()
  local all = { home, app, sounds }
  for _, p in ipairs(pages) do all[#all + 1] = p.frame end

  local function overlap(a, b)
    return a.x < b.x + b.w - 0.01 and b.x < a.x + a.w - 0.01 and a.y < b.y + b.h - 0.01 and b.y < a.y + a.h - 0.01
  end
  local function walk(name, lay)
    if not lay then check(false, name .. " : mise en page enregistrée") return end
    local bad = 0
    for i, a in ipairs(lay.rects) do
      if a.x < 0 or a.x + a.w > lay.W + 0.01 or a.h <= 0 then
        bad = bad + 1
        check(false, ("%s : « %s » sort de la colonne (x=%d w=%d / %d)"):format(name, a.label or "?", a.x, a.w, lay.W))
      end
      for j = i + 1, #lay.rects do
        local b = lay.rects[j]
        if overlap(a, b) then
          bad = bad + 1
          check(false, ("%s : « %s » chevauche « %s »"):format(name, a.label or "?", b.label or "?"))
        end
      end
      -- le widget est vraiment là où le moteur l'a noté (un seul point TOPLEFT, x / -y)
      local w = a.widget
      if w and w.__points and #w.__points == 1 then
        local p = w.__points[1]
        local dy = (a.kind == "button") and 2 or 0
        if a.kind == "button" and math.abs(-p[5] - a.y) <= 2 then dy = -p[5] - a.y end
        if not (p[1] == "TOPLEFT" and p[4] == a.x and -p[5] == a.y + dy) then
          bad = bad + 1
          check(false, ("%s : « %s » posé ailleurs que prévu"):format(name, a.label or "?"))
        end
      end
      -- textes : largeur fixe (retour à la ligne) dans la colonne
      if w and (a.kind == "text" or a.kind == "header") and not (w:GetWidth() and w:GetWidth() <= lay.W) then
        bad = bad + 1
        check(false, name .. " : texte sans largeur fixe : " .. tostring(a.label))
      end
      if a.kind == "slider" then
        local c = a.ctl
        local cp = c and c.__points and c.__points[1]
        if not (cp and cp[4] == a.x + 6 and -cp[5] > a.y and -cp[5] + 17 + 16 <= a.y + a.h
            and cp[4] + c:GetWidth() + 10 + 56 <= lay.W and w:GetWidth() <= lay.W - a.x) then
          bad = bad + 1
          check(false, name .. " : curseur hors de sa ligne : " .. tostring(a.label))
        end
      end
      if w and a.kind == "check" and not (w.label and w.label:GetWidth() and a.x + 28 + w.label:GetWidth() <= lay.W) then
        bad = bad + 1
        check(false, name .. " : libellé de case trop large : " .. tostring(a.label))
      end
    end
    return bad
  end

  local total, nRects = 0, 0
  for _, page in ipairs(all) do
    page.build()
    local lay = T:GetOptionLayout(page)
    total = total + (walk(page.name or "?", lay) or 1)
    nRects = nRects + (lay and #lay.rects or 0)
    check(lay and page:GetHeight() >= (lay.rects[#lay.rects].y + lay.rects[#lay.rects].h), (page.name or "?") ..
      " : le contenu défilant couvre toute la page")
  end
  check(total == 0, ("%d pages d'options, %d widgets, aucun chevauchement"):format(#all, nRects))

  -- rythme : un curseur = titre + barre + min/max (≥ 50 px) ; un bouton = 28 ; une case = 26
  local R = T.OPTIONS_RHYTHM
  local lay = T:GetOptionLayout(home)
  local kinds = {}
  for _, r in ipairs(lay.rects) do
    kinds[r.kind] = true
    if r.kind == "slider" then check(r.h >= R.slider, "curseur assez haut : " .. r.label) end
    if r.kind == "check" then check(r.h >= R.check, "case assez haute : " .. r.label) end
  end
  check(kinds.slider and kinds.check and kinds.button and kinds.header, "Accueil : sections, cases, curseurs, boutons")
  -- le titre long du curseur ne déborde pas à gauche : il est sur sa propre ligne, à gauche de la colonne
  for _, r in ipairs(lay.rects) do
    if r.kind == "slider" and r.label:find("Priorité minimale") then
      check(r.x == R.pad and r.widget:GetWidth() == lay.W - 2 * R.pad,
        "« Priorité minimale … » : titre entier, aligné à gauche")
    end
  end
  -- boutons : même largeur partout (largeur commune ou grille), jamais au hasard
  local widths = {}
  for _, page in ipairs(all) do
    local l = T:GetOptionLayout(page)
    for _, r in ipairs(l.rects) do
      if r.kind == "button" then widths[math.floor(r.w)] = true end
    end
  end
  local nW = 0
  for _ in pairs(widths) do nW = nW + 1 end
  check(nW <= 6, "largeurs de boutons cohérentes (" .. nW .. " largeurs)")

  -- arbre des catégories : tout tient, rien ne se chevauche, une entrée par page
  local nav = T:GetOptionLayout("nav")
  check(nav and walk("arbre", { W = nav.W, rects = nav.rects }) == 0, "arbre des catégories : rien ne se chevauche")
  local entries = 0
  for _, r in ipairs(nav.rects) do if r.h == R.navRow then entries = entries + 1 end end
  check(entries == #all, ("arbre : une entrée par page (%d/%d)"):format(entries, #all))

  -- ouvrir sur une page précise (Sons) puis sur un module
  T:OpenOptions("sounds")
  check(sounds:IsShown() and not home:IsShown(), "OpenOptions(« sounds ») montre la page Sons")
  if pages[1] then
    T:OpenOptions(pages[1].module)
    check(pages[1].frame:IsShown() and not sounds:IsShown(), "OpenOptions(module) montre sa page")
  end

  -- panneau « rangé » : Maintenant = Urgent → Quête en cours → Ensuite, avec un titre par section
  if T.ApplyPreset then T:ApplyPreset("tout") end
  if T.SetMini then T:SetMini(false) end
  T:ClearSuggestions()
  T:Suggest("tidy:low", "Petit conseil", 2, { sound = false, onClick = function() end })
  T:Suggest("tidy:danger", "Danger : élite en approche", 3, { sound = false, alert = false })
  T:SetTabLines("quests", { { text = "Étape : parler au garde", prio = 1, pin = true } }, "Quests")
  T:SelectTab("now")
  T:RefreshPanel()
  local vis = T:GetVisibleLines()
  local iD, iQ, iL
  for i, e in ipairs(vis) do
    if e.text:find("Danger") then iD = i elseif e.pin and not iQ then iQ = i elseif e.text:find("Petit conseil") then iL = i end
  end
  check(iD and iQ and iL and iD < iQ and iQ < iL, "Maintenant : danger → étape de quête → le reste")
  check((T:GetPanel().sections or 0) >= 3, "Maintenant : titres de section (Urgent / Quête en cours / Ensuite)")
  local rl = T:GetRowLayout()
  local okGap = true
  for i, r in ipairs(rl) do if rl[i + 1] and rl[i + 1].top > r.top - r.h then okGap = false end end
  check(okGap, "lignes et titres de section ne se chevauchent pas")
  T:SetTabLines("quests", {}, "Quests")
  T:ClearSuggestions()
  if T.ApplyPreset then T:ApplyPreset("normal") end

  -- boussole : cachée quand on est mort / fantôme (la fenêtre de mort de Blizzard reste lisible)
  local C = T.modules and T.modules.Compass
  if C and C.Refresh and C.Show then
    C.Show(true)
    local was = M.player and M.player.dead
    if M.player then M.player.dead = true end
    if T.InvalidatePlayerState then T:InvalidatePlayerState() end
    T._stateCache = nil
    local st = C.Refresh()
    local f = _G.TreeAICompassFrame
    check(not (f and f:IsShown()), "boussole cachée pendant la mort")
    check(st and st.hiddenDead, "boussole : raison = mort / fantôme")
    -- itinéraire TreeAI confié à Shortest Path : retiré à la mort (le nôtre seulement), plus de ligne flottante
    local stopped
    rawset(_G, "ShortestPathForever", { API = { Estimate = function() return nil end,
      StopRoute = function(owner) stopped = owner return true end } })
    if TreeAI.SetGuideMode then TreeAI:SetGuideMode("auto") end   -- (3.9.8) SPF is opt-in: this part tests the opt-in
    if C.ResetSPF then C.ResetSPF() end
    C.MarkRoute("spf")
    M.FireEvent("PLAYER_DEAD")
    check(stopped == "TreeAI", "mort : l'itinéraire TreeAI de Shortest Path est retiré")
    stopped = nil
    M.FireEvent("PLAYER_DEAD")
    check(stopped == nil, "rien d'autre n'est retiré (pas d'itinéraire TreeAI en cours)")
    rawset(_G, "ShortestPathForever", nil)
  if TreeAI.SetGuideMode then TreeAI:SetGuideMode("treeai") end
    if C.ResetSPF then C.ResetSPF() end
    if M.player then M.player.dead = was end
    if T.InvalidatePlayerState then T:InvalidatePlayerState() end
    C.Refresh()
  end
end
