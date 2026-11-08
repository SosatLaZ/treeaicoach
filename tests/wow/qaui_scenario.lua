-- qaui_scenario.lua (QA-UI round 9 → FIX-UI regression): what the player SEES — card, window pages, options
-- pages, hub menu, shortcuts bar, chat — for a level-28 Horde hunter in Mille pointes, with the checks of the
-- verified findings (UI-02, 08, 12, 13, 16, 17, 18, 20, 21, 25, 26, 27, 29, 41, W-*). Dump: QAUI_OUT=<file> or
-- QAUI_VERBOSE=1. Run with --no-storm --profile sur|tout --flavor forever --class HUNTER --level 28 (+ --allow …).
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Lb = dofile("tests/wow/qaui_lib.lua")
  Lb.instrument(M)
  local out
  local path = os.getenv("QAUI_OUT")
  if path then out = io.open(path, "w") elseif os.getenv("QAUI_VERBOSE") == "1" then out = io.stderr end
  local function p(...)
    if not out then return end
    local t = { ... } for i = 1, #t do t[i] = tostring(t[i]) end out:write(table.concat(t, " "), "\n")
  end
  local W, H = T.Window, T.Hub
  local chat0 = #M.output
  local prof = T:ModuleProfile()

  -- world
  Lb.world(M, T)
  Lb.goTo(1441, 46, 50)                        -- Mille pointes, Librevent
  local X, P = T.Export, T.Planner
  if X and X.Rebuild then X:Rebuild() end
  M.Advance(8, 0.1)
  if P and P.Refresh then P:Refresh(true) end
  M.Advance(5, 0.05)
  M.Advance(3, 0.5)

  p("== profile", prof, "flavor", M.flavor)
  -- ------------------------------------------------------------------ card
  W.ShowCard()
  W.RenderCard(true)
  local c = W.GetCard()
  check(c and c:IsShown(), "card shown")
  p("== CARD shown=" .. tostring(c and c:IsShown()))
  p("  hero.title:", c.hero.title:GetText(), "| sub:", c.hero.sub:GetText())
  local rows = {}
  for i, r in ipairs(c.steps) do
    if r:IsShown() then
      rows[#rows + 1] = r
      p(("  step %d [%s] %s || %s"):format(i, tostring(r.badge.num:GetText()), Lb.strip(r.title:GetText()), Lb.strip(r.sub:GetText())))
    end
  end
  -- UI-16 / F30 : hero = the zone name only (no « Histoire : »)
  -- (ZONEFIX) the zone first, then the theme when there is one (« Mille pointes · Librevent »), never « Histoire : »
  local ht = tostring(c.hero.title:GetText())
  check(ht:find("^Mille pointes") and not ht:find("Histoire", 1, true), "card hero = zone name (+ theme) (" .. ht .. ")")
  -- W-05 : « n arrêts proches », no « en cours » on the card
  local hs = tostring(c.hero.sub:GetText())
  check(not hs:find("en cours", 1, true) and (hs:find("arrêt", 1, true) or hs:find("min", 1, true)), "card summary wording (" .. hs .. ")")
  -- UI-16 : no « nouvelle quête » / « quête finie » (the ! / ? badge says it)
  for _, r in ipairs(rows) do
    local s = Lb.strip(r.sub:GetText())
    check(not s:find("nouvelle quête", 1, true) and not s:find("quête finie", 1, true), "card sub without redundant words: " .. s)
    check(not Lb.strip(r.title:GetText()):find("→", 1, true), "card title: no missing glyph « → »")
  end
  -- UI-41 : mode sûr notice once at the top (badge « i »), with a short text that fits
  if prof == "sur" then
    check(rows[1] and rows[1].badge.num:GetText() == "i" and Lb.strip(rows[1].title:GetText()):find("Mode sûr", 1, true),
      "mode sûr notice on top of the card (badge i)")
    check(rows[2] and rows[2].badge.num:GetText() == "1", "route steps numbered from 1 after the notice")
  else
    check(rows[1] and rows[1].badge.num:GetText() == "1", "tout: route step 1 first")
  end
  for i, r in ipairs(rows) do
    for _, fs in ipairs({ r.title, r.sub }) do
      local need = Lb.ulen(fs:GetText()) * 5.4
      check(need <= 228, ("card row %d fits its 224 px (%d px: « %s »)"):format(i, need, Lb.strip(fs:GetText())))
    end
  end

  -- ------------------------------------------------------------------ window pages
  W.Open("now")
  local win = W.GetWindow()
  local function rectsOf(kind)
    local t = {}
    for _, r in ipairs(W.lastLay.rects) do if not kind or r.kind == kind then t[#t + 1] = r end end
    return t
  end
  local function has(label, kind)
    for _, r in ipairs(W.lastLay.rects) do if (not kind or r.kind == kind) and r.label:find(label, 1, true) then return r end end
  end
  local function dumpPage(id)
    W.Select(id)
    p(("== PAGE %s · title « %s » · sub « %s » · action « %s » · status « %s »"):format(id,
      tostring(win.pageTitle:GetText()), tostring(win.pageSub:GetText()),
      win.action:IsShown() and tostring(win.action.labelText) or "-", tostring(win.status:GetText())))
    for _, r in ipairs(W.lastLay.rects) do p(("  %-7s y=%4d h=%3d w=%3d  %s"):format(r.kind, r.y, r.h, r.w, r.label)) end
  end
  for _, pg in ipairs(W.PAGES) do if not pg.hidden then dumpPage(pg.id) end end
  -- UI-25 / W-08 : the release version, not « v1.0.9 »
  check(win.ver:GetText() == "Coach · v4.0", "window version « Coach · v4.0 » (" .. tostring(win.ver:GetText()) .. ")")
  -- UI-08 / W-01 : « Tout va bien (…) » only for what really runs
  local ag = W.AllGoodText()
  if prof == "sur" then
    check(ag == "Rien d'urgent : continue ton étape.", "mode sûr: no false « Tout va bien » (" .. ag .. ")")
  else
    check(ag:find("Tout va bien (sacs, réparation", 1, true) and ag:find("familier", 1, true), "tout: checks named (" .. ag .. ")")
  end
  -- Quêtes : hero = zone, « Pause » (F30), status cap
  W.Select("quests")
  check(W.lastLay.rects[1] and W.lastLay.rects[1].label == "Mille pointes", "Quêtes hero = zone")
  check(win.action:IsShown() and win.action.labelText == "Pause", "Quêtes: « Pause » (not « Arrêter »)")
  W.Select("route")
  check(win.action:IsShown() and (win.action.labelText == "Pause" or win.action.labelText:find("Suivre", 1, true)), "Trajet: « Pause »")
  -- UI-27 : HV page off → one « Allumer l'HV (bêta) » button (the footer), not two
  W.Select("hv")
  local A = T.modules.Auction
  if A and not A._active then
    check(not has("Allumer l'HV", "button") and win.action.labelText == "Allumer l'HV (bêta)", "HV: one « Allumer l'HV (bêta) » (footer)")
  end
  -- UI-29 / UI-18 : Outils page: both groups, the panel toggle once
  W.Select("tools")
  local nPanel = 0
  for _, r in ipairs(rectsOf("row")) do if r.label:find("panneau", 1, true) then nPanel = nPanel + 1 end end
  check(has("Outils", "header") and has("Réglages et diagnostic", "header"), "Outils: « Outils » + « Réglages et diagnostic »")
  check(nPanel <= 1, "Outils: the panel toggle listed once (" .. nPanel .. ")")
  check(not has("Rien ne correspond", "text"), "Outils: no « Rien ne correspond à «  » » with an empty search")
  -- UI-30 : no raw tab id in « Autres pages »
  for _, r in ipairs(rectsOf("row")) do check(r.label ~= "finder" and r.label ~= "modules", "Outils: no raw tab id (" .. r.label .. ")") end
  -- UI-26 / W-07 : Fonctions intro follows the profile
  W.Select("modules")
  local intro = W.lastLay.rects[1] and W.lastLay.rects[1].label or ""
  if prof == "tout" then check(intro:find("Tout est allumé", 1, true), "Fonctions intro in « tout »: " .. intro)
  else check(intro:find("Mode sûr", 1, true), "Fonctions intro in mode sûr: " .. intro) end
  -- W-36 : module lines = state + title (+ bêta), the description is the tooltip
  for _, r in ipairs(rectsOf("row")) do
    if r.label:find("^ON ") or r.label:find("^OFF ") then check(not r.label:find(" — ", 1, true), "Fonctions line without « — desc »: " .. r.label) end
  end
  -- UI-15 : « Tout allumer » says the quest automations stay opt-in
  if prof == "sur" then
    check(win.action.labelText == "Tout allumer" and tostring(win.action.tip):find("automatismes de quête", 1, true), "« Tout allumer » tip")
  end
  -- UI-13 : dungeon names in their card (the « x/y quêtes » counter moved to the bottom line)
  W.char.dungeon, W.char.dgAll = nil, true
  W.Select("dungeon")
  local cut = 0
  for _, f in ipairs(win.content.__children) do
    if f.__kind == "Button" and f.name and f.lv and f:IsVisible() then
      local need = Lb.ulen(f.name:GetText()) * 8.2
      if need > (f.name.__w or 0) + 4 then cut = cut + 1 end
    end
  end
  check(cut <= 2, "Donjons: names not cut (" .. cut .. " cut)")
  W.char.dgAll = nil

  -- ------------------------------------------------------------------ options pages
  -- UI-02 : one section title per section (no « Barre » ×6) ; every page builds, nothing overlaps
  local entries = T:OptionEntries()
  local dupPages = {}
  for _, e in ipairs(entries) do
    if e.key and e.builder then
      local f = CreateFrame("Frame", nil, UIParent)
      f:SetSize(414, 100)
      local ok, err = pcall(e.builder, f, 414)
      check(ok, "options page " .. tostring(e.key) .. " builds: " .. tostring(err))
      local seen, lastH = {}, nil
      for _, r in ipairs(f.__layout and f.__layout.rects or {}) do
        if r.kind == "header" then
          if seen[r.label] then dupPages[#dupPages + 1] = e.key .. ":" .. r.label end
          seen[r.label] = true
          lastH = r.label
        end
      end
      local _ = lastH
      f:Hide()
    end
  end
  check(#dupPages == 0, "options: no repeated section title (" .. table.concat(dupPages, ", ") .. ")")
  -- W-20 / W-21 / UI-38 : names
  check(T.modules.Quick.title == "Barre de quête" and T.modules.QuickTravel.title == "Voyage et groupe", "renamed bars")
  check(T.tabs.modules and T.tabs.modules.title == "Fonctions", "tab « Fonctions »")
  -- UI-20 : no missing glyph in option labels / key hints
  for _, n in ipairs({ "Region", "Highlight" }) do
    for _, o in ipairs(T.modules[n] and T.modules[n].options or {}) do
      check(not tostring(o.label):find("→", 1, true) and not tostring(o.label):find("✓", 1, true), n .. " option label glyphs: " .. tostring(o.label))
    end
  end
  check(not T.Theme:KeyHint({}):find("→", 1, true), "key hint without « → »")

  -- ------------------------------------------------------------------ hub menu
  local function hub(tag)
    H.mode = "main"
    H.Toggle()
    local menu = rawget(_G, "TreeAIHubMenu")
    p(("== HUB %s : %d rows, size %dx%d"):format(tag, #H.layout, menu:GetWidth(), menu:GetHeight()))
    for _, e in ipairs(H.layout) do
      if e.kind == "header" then p("  # " .. e.group) elseif e.kind == "item" then p(("  - %-28s %s"):format(e.text, e.desc or "")) end
    end
    local h = menu:GetHeight()
    H.Close()
    return h
  end
  local h1 = hub(prof)
  check(h1 <= 560 + 10, "hub menu fits (" .. h1 .. " ≤ 570)")
  -- UI-12 : at UI scale 1.15 (UIParent 668 units high) the menu still fits
  local uw, uh = UIParent:GetWidth(), UIParent:GetHeight()
  UIParent:SetSize(1336, 668)
  local h2 = hub(prof .. " @1.15")
  check(h2 <= 668 - 110, "hub menu fits at UI scale 1.15 (" .. h2 .. " ≤ 558)")
  UIParent:SetSize(uw, uh)
  -- « Jeu » = the window pages, one line each (no description)
  H.mode = "main"
  local jeu = {}
  for _, e in ipairs(H.Entries()) do if e.group == "jeu" then jeu[#jeu + 1] = e end end
  check(#jeu >= 6 and #jeu <= 9, "Jeu: the window pages (" .. #jeu .. ")")
  for _, e in ipairs(jeu) do check(not e.desc and e.id:find("^tab:"), "Jeu entry compact: " .. tostring(e.id)) end
  -- UI-18 : Échap on the bêta list → the next menu opens on the main list
  H.Toggle()
  H.mode = "beta"
  H.Fill()
  rawget(_G, "TreeAIHubMenu"):Hide()          -- (UISpecialFrames : Échap)
  check(H.mode == "main", "menu closed by Échap: back to the main list")
  -- UI-10 / UI-11 : Boussole = a toggle (no chat), Radar only when its module runs
  local ent = {}
  for _, e in ipairs(H.Entries()) do ent[e.id] = e end
  local Rd = T.modules.Radar
  check((ent.radar ~= nil) == (Rd ~= nil and Rd._active == true), "Radar entry only when Radar runs")
  if ent.boussole then
    local cf = rawget(_G, "TreeAICompassFrame")
    local was = cf and cf:IsShown()
    local n0 = #M.output
    ent.boussole.fn()
    check(#M.output == n0 and cf and (cf:IsShown() ~= was), "Boussole entry toggles the arrow, nothing in the chat")
    ent.boussole.fn()
  end
  -- UI-31 : pastille tooltip = the card's first real line (not the mode sûr notice, no « Arrêt n/m : »)
  local sum = H.Summary()[1][1]
  check(not sum:find("Mode sûr", 1, true) and not sum:find("Arrêt %d"), "pastille tooltip: " .. sum)

  -- ------------------------------------------------------------------ shortcuts
  local S = T.Shortcuts
  M.Advance(2, 0.5)
  local dock = S and S.Frame and S.Frame()
  p("== SHORTCUTS dock shown=" .. tostring(dock and dock:IsShown()) .. " segments=" .. tostring(S and S.shown))
  if dock and dock:IsShown() then
    for _, f in ipairs(dock.__children) do
      if f.__kind == "FontString" and f.__shown and f.__text and f.__text ~= "" then
        local need = Lb.ulen(f.__text) * 5.4
        p(("  label « %s » need≈%d w=%s"):format(f.__text, need, tostring(f.__w)))
        check(need <= (f.__w or 0) + 4, ("shortcuts label fits its group (« %s » %d > %s)"):format(f.__text, need, tostring(f.__w)))
      end
    end
  end
  -- UI-17 : narrow group → short title or none, never spilling
  check(S.FitLabel("Quêtes · voyage", 60) == "Quêtes" and S.FitLabel("Quêtes · voyage", 20) == nil
    and S.FitLabel("Quêtes · voyage", 200) == "Quêtes · voyage", "shortcuts label fitting")

  p("== CHAT lines since start:", #M.output - chat0)
  for i = chat0 + 1, #M.output do p("  chat: " .. M.output[i]) end
  check(#M.output - chat0 == 0, "nothing in the chat (" .. (#M.output - chat0) .. " lines)")
  W.Close()
  if path and out then out:close() end
end
