-- Scenario (UIREDO): the main « TreeAI » window (Leatrix-like chrome, left navigation, search, one action button),
-- the « Prochaine étape » card (header card + numbered steps, replaces the text panel, never on a quest tracker),
-- the Adventure-Guide « Quêtes » page, the « Trajet » page, the Dungeon-Journal « Donjons » cards + detail, the
-- « Options » page built by the TIDY layout engine (search), the key binding entry. Fake quests, fake names.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/uiredo_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local W = T.Window
  check(W and W.Open and W.Guide and W.RenderCard, "module Window chargé")
  if not W then return end
  M.inCombat = false
  check(T.SAFE_MODULES.Window, "Window fait partie du mode sûr")
  check(type(T.ToggleMain) == "function" and rawget(_G, "BINDING_NAME_TREEAI_MAIN"), "touche « ouvrir / fermer la fenêtre »")

  local function noOverlap(Ly, what)
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
      if a.x + a.w > Ly.W + 0.5 then return false, what .. " : « " .. a.label .. " » sort de la colonne" end
    end
    return true
  end

  -- ------------------------------------------------------------ a small plan: 4 steps, one flight
  local P, R = T.Planner, T.Route
  M.mapRects[1411] = { top = 1808.3, left = -1962.5, h = 3525.0, w = 5287.5, continent = 1 }   -- Durotar
  if T.Geo then T.Geo.rects = {} end
  M.pos = { mapID = 1411, x = 0.50, y = 0.50 }
  M.player.zone = "Durotar"
  local steps = {
    { k = "pick", q = 9101, map = 1411, x = 51, y = 50, label = "Prendre « Les sangliers »" },
    { k = "obj", q = 9102, o = 1, map = 1411, x = 55, y = 52 },
    { k = "turn", q = 9103, map = 1411, x = 52, y = 49, label = "Rendre « Lettre »" },
    { k = "pick", q = 9104, map = 1411, x = 60, y = 40, label = "Prendre « Plus loin »" },
    { k = "pick", q = 9105, map = 1411, x = 62, y = 38, label = "Prendre « Encore plus loin »" },
  }
  if P and R then
    P.route = { id = "auto", title = "Trajet TreeAI", steps = steps, verdict = "ok" }
    P.steps, P.cursor = steps, 1
    P.log = { [9102] = { t = "Les scorpides", lv = 8, obj = { { text = "Scorpide tué : 3/8", type = "monster", have = 3, n = 8 } } },
      [9103] = { t = "Lettre", lv = 7, done = 1, obj = {} } }
    if R.char then R.char.paused = false end
  end
  local g = W.Guide(3, true)
  check(#g.steps == 3, "guide : 3 étapes (" .. #g.steps .. ")")
  check(g.steps[1] and g.steps[1].mark and g.steps[1].title:find("Les sangliers", 1, true), "étape 1 : à prendre, pastille « ! »")
  check(g.steps[2] and g.steps[2].sub and g.steps[2].sub:find("objectif 3/8", 1, true) and g.steps[2].sub:find("Les scorpides", 1, true),
    "étape 2 : sous-titre gris « objectif 3/8 · « Les scorpides » » (" .. tostring(g.steps[2] and g.steps[2].sub) .. ")")
  -- (4.0 QA) « quête finie » / « nouvelle quête » : la pastille « ? » / « ! » le dit déjà, le sous-titre garde le lieu
  check(g.steps[3] and g.steps[3].mark == W.MARK.turn and not g.steps[3].sub:find("quête finie", 1, true),
    "étape 3 : à rendre (pastille « ? », pas de « quête finie » redit)")
  check(#g.upcoming >= 1, "quêtes à venir sur le trajet")
  for _, s in ipairs(g.steps) do
    check(not tostring(s.sub):find("nil") and not tostring(s.title):find("nil"), "pas de « nil » dans une étape")
  end

  -- ------------------------------------------------------------ the card replaces the text panel
  T:ClearSuggestions()
  T:HidePanel()                  -- (the harness storm clicked « afficher le panneau » : back to a fresh login)
  if T:GetPanel() then T:GetPanel():Show() end
  W.ShowCard()
  local card = W.GetCard()
  check(card and card:IsShown(), "carte « Prochaine étape » affichée")
  check(card and card:GetName() == "TreeAICard" and card:GetParent() == UIParent and not card:IsProtected(), "carte : à nous, non protégée")
  local p = T:GetPanel()
  check(not (p and p:IsShown()), "le panneau texte est caché quand la carte le remplace")
  W.RenderCard(true)
  check(card.hero.title:GetText() == "Durotar", "carte : le nom de la zone (" .. tostring(card.hero.title:GetText()) .. ")")
  check(card.steps[1] and card.steps[1]:IsShown() and card.steps[3] and card.steps[3]:IsShown(), "carte : 3 lignes d'étapes")
  check(card.steps[1].badge.num:GetText() == "1" and card.steps[3].badge.num:GetText() == "3", "carte : pastilles numérotées ("
    .. tostring(card.steps[1].badge.num:GetText()) .. "/" .. tostring(card.steps[1].title:GetText()) .. ")")
  local sub = card.hero.sub:GetText() or ""
  check(not sub:find("nil") and sub ~= "", "carte : résumé « n en cours · m proches · durée » (" .. sub .. ")")
  -- a click on a step = guide to it (user action), no blocked action
  local navigated
  local oldNav = P.Navigate
  P.Navigate = function(_, s) navigated = s end
  card.steps[2]:GetScript("OnClick")(card.steps[2], "LeftButton")
  check(navigated == steps[2], "clic sur l'étape 2 = guider vers elle")
  P.Navigate = oldNav
  -- danger first, in red
  T:Suggest("uiredo:danger", "Ton familier est mort", 3, { sound = false })
  W.RenderCard(true)
  check(card.steps[1].badge.num:GetText() == "!" and card.steps[1].title:GetText():find("familier", 1, true),
    "danger en tête de la carte (" .. tostring(card.steps[1].title:GetText()) .. ")")
  T:ClearSuggestion("uiredo:danger")
  -- the player opens the old panel himself: it stays
  T:ShowPanel()
  check(T:GetPanel():IsShown(), "panneau ouvert à la main : il reste")
  T:HidePanel()

  -- ------------------------------------------------------------ main window
  W.Open("now")
  local win = W.GetWindow()
  check(win and win:IsShown() and win:GetName() == "TreeAIMainWindow", "fenêtre TreeAI ouverte")
  check(win.title:GetText() == "TreeAI" and tostring(win.ver:GetText()):find("v", 1, true), "titre + version en haut à gauche")
  check(win.search ~= nil, "recherche en haut à droite")
  local ids = {}
  for _, pg in ipairs(W.PAGES) do ids[#ids + 1] = pg.id end
  check(table.concat(ids, ","):find("^now,quests,route,group,dungeon,hv,tools") and table.concat(ids, ","):find("options$"),
    "navigation : Maintenant / Quêtes / Trajet / Groupe / Donjons / HV / Outils / … / Options (" .. table.concat(ids, ",") .. ")")
  for _, pg in ipairs(W.PAGES) do
    W.Select(pg.id)
    check(W.CurrentPage() == pg.id, "page " .. pg.id)
    local ok, msg = noOverlap(W.lastLay, "page " .. pg.id)
    check(ok, msg or ("page " .. pg.id .. " sans chevauchement"))
    check(#W.lastLay.rects > 0, "page " .. pg.id .. " : du contenu ou un état vide (" .. #W.lastLay.rects .. ")")
    for _, r in ipairs(W.lastLay.rects) do
      if r.label:find("Erreur d'affichage", 1, true) then check(false, "page " .. pg.id .. " : " .. r.label) end
    end
  end
  -- Quêtes = Adventure Guide: header card, numbered steps, upcoming in level colours
  W.Select("quests")
  local kinds = {}
  for _, r in ipairs(W.lastLay.rects) do kinds[r.kind] = (kinds[r.kind] or 0) + 1 end
  check(kinds.hero == 1 and (kinds.step or 0) >= 3 and (kinds.quest or 0) >= 1, "Quêtes : carte d'en-tête + étapes + quêtes à venir")
  check(W.lastLay.rects[1].label == "Durotar", "Quêtes : carte d'en-tête « Durotar »")
  check(win.action:IsShown() and win.action.label:GetText() == "Pause", "Quêtes : bouton « Pause » en bas à droite")
  win.action:GetScript("OnClick")(win.action)
  check(R.char.paused == true and win.action.label:GetText() == "Reprendre", "Pause → Reprendre")
  win.action:GetScript("OnClick")(win.action)
  check(not R.char.paused, "Reprendre")
  -- collapsible section remembered
  W.char.collapsed["q:extra"] = nil
  -- Trajet: all stops numbered
  W.Select("route")
  local nstep = 0
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "step" then nstep = nstep + 1 end end
  check(nstep >= 4, "Trajet : chaque arrêt numéroté (" .. nstep .. ")")
  -- search filters the page
  W.Select("route")
  win.search:SetText("Lettre")
  win.search:GetScript("OnTextChanged")(win.search)
  nstep = 0
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "step" then nstep = nstep + 1 end end
  check(nstep == 1, "recherche « Lettre » : un seul arrêt (" .. nstep .. ")")
  win.search:SetText("")
  win.search:GetScript("OnTextChanged")(win.search)

  -- ------------------------------------------------------------ Donjons: cards → detail
  M.player.level = 18
  W.char.dungeon, W.char.dgAll = nil, nil
  W.Select("dungeon")
  local cards = {}
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "dcard" then cards[#cards + 1] = r end end
  check(#cards >= 3, "Donjons : des cartes à mon niveau (" .. #cards .. ")")
  local found = false
  for _, r in ipairs(cards) do if r.label == "Mortemines" or r.label == "Gouffre de Ragefeu" then found = true end end
  check(found, "Donjons : Mortemines / Gouffre de Ragefeu au niveau 18")
  local okO, msgO = noOverlap(W.lastLay, "cartes de donjon")
  check(okO, msgO or "cartes sans chevauchement")
  W.char.dungeon = "RFC"
  W.Render()
  local hasBoss, hero = false, nil
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "hero" then hero = r.label end
    if r.kind == "step" then hasBoss = true end
  end
  check(hero == "Gouffre de Ragefeu" and hasBoss, "détail : carte d'en-tête + boss numérotés")
  if not T.Dungeon._active then   -- mode sûr : le journal marche, le suivi en donjon est une fonction bêta
    check(win.action.label:GetText() == "Allumer « Donjon »", "mode sûr : « Allumer « Donjon » » d'abord")
    win.action:GetScript("OnClick")(win.action)
    check(T.Dungeon._active, "un clic : module Donjon allumé")
    W.Render()
  end
  check(win.action.label:GetText() == "Prévoir ce donjon", "détail : « Prévoir ce donjon » (" .. tostring(win.action.label:GetText()) .. ")")
  win.action:GetScript("OnClick")(win.action)
  check(T.Dungeon.Planned() and T.Dungeon.Planned().key == "RFC", "donjon prévu au clic")
  T.Dungeon.SetPlanned(nil)
  W.char.dungeon = nil

  -- ------------------------------------------------------------ Options: TIDY engine inside the window, search
  W.Select("options")
  local nav, page = 0, nil
  for _, r in ipairs(W.lastLay.rects) do
    if r.kind == "navrow" then nav = nav + 1 end
    if r.kind == "optpage" then page = r end
  end
  check(nav >= 10 and page and page.h > 40, "Options : liste des pages + page construite (" .. nav .. ")")
  win.search:SetText("boussole")
  win.search:GetScript("OnTextChanged")(win.search)
  local nav2 = 0
  for _, r in ipairs(W.lastLay.rects) do if r.kind == "navrow" then nav2 = nav2 + 1 end end
  check(nav2 >= 1 and nav2 < nav, "Options : la recherche filtre les pages (" .. nav2 .. "/" .. nav .. ")")
  win.search:SetText("")
  win.search:GetScript("OnTextChanged")(win.search)
  check(win.action.label:GetText() == "Recharger l'interface", "Options : un bouton « Recharger » en bas à droite")

  -- ------------------------------------------------------------ resize + memory, close, hub entry
  win:SetSize(700, 460)
  W.Render()
  check(W.lastLay.W <= 700 - W.NAV_W, "contenu à la largeur de la fenêtre")
  W.Close()
  check(not W.IsOpen() or (T.Theme and T.Theme:IsAnimating(win)), "fermée (fondu)")
  local hubOk = false
  for _, e in ipairs(T.HUB_EXTRA or {}) do if e.id == "window" then hubOk = true end end
  check(hubOk, "pastille : entrée « Fenêtre TreeAI »")
  T.ToggleMain()
  check(W.IsOpen(), "touche : ouvre la fenêtre")
  T.ToggleMain()
  M.inCombat = true
  W.Open("quests")
  check(W.IsOpen(), "s'ouvre aussi en combat (rien de protégé)")
  M.inCombat = false
  W.Close()

  -- ------------------------------------------------------------ one « Raccourcis » bar (Shortcuts.lua)
  local Sc = T.Shortcuts
  check(Sc and T.RegisterShortcutBar and T.SAFE_MODULES.Shortcuts, "barre « Raccourcis » : module + API d'amarrage")
  if Sc and Sc._active then
    local X = T.modules.Quick
    if X and X._active and X.Layout then X.Layout() M.Advance(0.5, 0.1) end
    local seg = CreateFrame("Frame", "TreeAITestSegment", UIParent)
    seg:SetSize(4 + 3 * 34, 38)
    seg:Show()
    T:RegisterShortcutBar(seg, { id = "test", label = "Essai", order = 5, inset = 4 })
    M.Advance(0.5, 0.1)
    local dock = Sc.Frame()
    check(dock and dock:IsShown() and dock:GetName() == "TreeAIShortcuts", "une seule barre « Raccourcis » affichée")
    check(seg:GetParent() == dock and Sc.Docked(seg), "segment amarré dans la barre")
    local movers = T:GetMovables()
    check(movers.shortcuts and movers.shortcuts.frame == dock and not movers.quick and not movers.quickfight,
      "mode édition : un seul cadre « Raccourcis » (plus de barres séparées)")
    if X and X.bar then
      check(X.bar:GetParent() == dock, "barre de quête amarrée")
      local qb = X.buttons and X.buttons.toggle
      check(qb and qb.__scStyled and qb.edges and #qb.edges == 4, "bouton au style commun (liseré 1 px, enfoncé, touche)")
    end
    -- segment vide : il disparaît ; plus rien : la barre se cache
    seg:SetWidth(4)
    Sc.Layout()
    local n1 = Sc.shown or 0
    seg:SetWidth(4 + 3 * 34)
    Sc.Layout()
    check((Sc.shown or 0) == n1 + 1, "segment vide retiré, puis remis")
    -- en combat : rien ne bouge (cadres sécurisés)
    M.inCombat = true
    local w0 = dock:GetWidth()
    seg:SetWidth(4 + 5 * 34)
    Sc.Layout()
    check(dock:GetWidth() == w0, "en combat : la barre ne bouge pas")
    M.inCombat = false
    M.Advance(0.5, 0.1)
    if T.RunPendingOutOfCombat then T:RunPendingOutOfCombat() end
    if ctx.fire then ctx.fire("PLAYER_REGEN_ENABLED") end
    M.Advance(0.5, 0.1)
    check(dock:GetWidth() > w0, "après le combat : la barre suit")
    -- option : barres séparées de nouveau
    Sc.db.dock = false
    Sc:OnOptionChanged("dock")
    check(seg:GetParent() == UIParent and not dock:IsShown(), "option « une seule barre » coupée : barres séparées")
    Sc.db.dock = true
    Sc:OnOptionChanged("dock")
    check(seg:GetParent() == dock, "et de nouveau réunies")
    seg:Hide()
    Sc.Layout()
  end
  check(#M.blocked == 0, "aucune action bloquée")
end
