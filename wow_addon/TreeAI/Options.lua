-- TreeAI - options. ONE entry in Blizzard's list (« TreeAI Coach ») ; inside it, our own layout like the best
-- addons (Ace3 AceConfigDialog / ElvUI): a category tree on the left (grouped, scrollable, always fits) and the
-- selected page on the right in a ScrollFrame. Every page is built by the same vertical layout engine: each widget
-- goes BELOW the previous one with a fixed rhythm (header 24, checkbox 26, slider ≈ 50, button 28, section gap
-- 14); labels have a fixed width, wrap, and their measured height pushes the next row down → nothing can
-- overlap and nothing leaves the column. The engine records every rectangle (TreeAI:GetOptionLayout) so the
-- harness can prove it. Plain frames only: nothing protected, no Blizzard frame is modified.
local TreeAI = TreeAI
if not TreeAI then return end
local _G = _G
local L = TreeAI.L
local pcall, ipairs, pairs, type, tostring, floor, ceil = pcall, ipairs, pairs, type, tostring, math.floor, math.ceil
local max, min = math.max, math.min

local category, canvas, appearance
local soundsPage, homePage
local pages = {}       -- { frame, module }  (one per module, shown inside our canvas)
local widgets = {}

-- rythme vertical (px) : identique sur toutes les pages
local R = { pad = 12, header = 24, check = 26, slider = 50, button = 28, gap = 14, line = 4, btnW = 280, btnH = 24,
  navW = 158, navRow = 18, navHead = 22 }
TreeAI.OPTIONS_RHYTHM = R

-- (UIREDO) une seule couleur d'accent partout : l'or du thème (Theme.lua), vert d'origine en repli
local function accent() local th = TreeAI.Theme return (th and th.Hex) and ("|c" .. th:Hex("gold")) or "|cff33cc66" end
local function accentRGB(a)
  local th = TreeAI.Theme
  if th and th.Color then local r, g, b = th:Color("gold") return r, g, b, a end
  return 0.2, 0.8, 0.4, a
end
local function plain(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")) end
local function ulen(s) local _, n = plain(s):gsub("[^\128-\191]", "") return n end
local AVG = { GameFontHighlightSmall = 5.4, GameFontDisableSmall = 5.4, GameFontNormalSmall = 5.4,
  GameFontHighlight = 6.2, GameFontNormal = 6.4, GameFontNormalLarge = 8.2 }
local LINE = { GameFontHighlightSmall = 13, GameFontDisableSmall = 13, GameFontNormalSmall = 13,
  GameFontHighlight = 15, GameFontNormal = 15, GameFontNormalLarge = 19 }
-- hauteur d'un texte à largeur fixe : la mesure du jeu, jamais moins que l'estimation (mots coupés compris)
local function measure(fs, text, width, font)
  local lh = LINE[font or ""] or 14
  local lines = max(1, ceil(ulen(text) * (AVG[font or ""] or 6) / max(40, width - 8)))
  local est = lines * lh
  local got = 0
  if fs and fs.GetStringHeight then
    local ok, v = pcall(fs.GetStringHeight, fs)
    got = ok and tonumber(v) or 0
  end
  return max(est, got, lh)
end
local function wrapFS(parent, font, width, text)
  local fs = parent:CreateFontString(nil, "OVERLAY", font)
  fs:SetJustifyH("LEFT")
  if fs.SetJustifyV then fs:SetJustifyV("TOP") end
  fs:SetWidth(width)
  if fs.SetWordWrap then fs:SetWordWrap(true) end
  if fs.SetNonSpaceWrap then fs:SetNonSpaceWrap(true) end
  fs:SetText(TreeAI.SafeText and TreeAI.SafeText(tostring(text or "")) or text)   -- (4.0 QA) « → » « ✓ » : carrés vides
  return fs
end
local function tooltip(owner, title, tip)
  if not tip then return end
  owner:SetScript("OnEnter", function(self)
    local tt = _G.GameTooltip
    if not tt then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    tt:AddLine(title, 1, 1, 1)
    tt:AddLine(tip, 0.8, 0.8, 0.8, true)
    tt:Show()
  end)
  owner:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
end

local function newButton(parent, label, width, fn, textFn)
  local ok, b = pcall(_G.CreateFrame, "Button", nil, parent, "UIPanelButtonTemplate")
  if not ok or not b then return nil end
  b:SetSize(width or 160, 22)
  b:SetText(label)
  b:SetScript("OnClick", function(self) TreeAI.safecall(fn, self); if self.refresh then self.refresh() end end)
  if textFn then b.refresh = function() b:SetText(textFn()) end; b.refresh() end
  return b
end

-- ---------------------------------------------------------------- moteur de mise en page (vertical, sans chevauchement)
-- P.rects = { {x, y, w, h, kind, label, widget} } en px depuis le coin haut-gauche du contenu (y vers le bas).
local function layout(content, width)
  width = floor(tonumber(width) or 440)
  local col = width - 2 * R.pad
  local P = { y = 8, W = width, col = col, rects = {}, sections = 0 }
  content.__layout = P
  local function rec(w, x, y, ww, h, kind, label)
    P.rects[#P.rects + 1] = { x = x, y = y, w = ww, h = h, kind = kind, label = plain(label), widget = w }
  end
  local function at(w, x, y) w:ClearAllPoints(); w:SetPoint("TOPLEFT", content, "TOPLEFT", x, -y) end
  function P.text(str, font, _)
    font = font or "GameFontHighlightSmall"
    local fs = wrapFS(content, font, col, str)
    local h = measure(fs, str, col, font)
    at(fs, R.pad, P.y)
    rec(fs, R.pad, P.y, col, h, "text", str)
    P.y = P.y + h + R.line
    return fs
  end
  function P.header(str)
    if P.sections > 0 or #P.rects > 0 then P.y = P.y + R.gap end
    P.sections = P.sections + 1
    local fs = wrapFS(content, "GameFontNormal", col, accent() .. str .. "|r")
    at(fs, R.pad, P.y)
    local rule = content:CreateTexture(nil, "ARTWORK")
    if rule.SetColorTexture then rule:SetColorTexture(accentRGB(0.35)) end
    rule:SetSize(col, 1)
    rule:SetPoint("TOPLEFT", content, "TOPLEFT", R.pad, -(P.y + R.header - 4))
    rec(fs, R.pad, P.y, col, R.header, "header", str)
    P.y = P.y + R.header
    return fs
  end
  -- case à cocher ; `right` = largeur réservée à droite (bouton « Écouter »)
  function P.check(label, getter, setter, tip, right)
    local ok, cb = pcall(_G.CreateFrame, "CheckButton", nil, content, "UICheckButtonTemplate")
    if not ok or not cb then return nil end
    cb:SetSize(24, 24)
    local tw = col - 28 - (right or 0)
    local old = cb.text or cb.Text
    if type(old) == "table" and old.SetText then old:SetText("") end
    local fs = wrapFS(cb, "GameFontHighlight", tw, label)
    fs:SetPoint("TOPLEFT", cb, "TOPLEFT", 28, -5)
    cb.label = fs
    local h = max(R.check, measure(fs, label, tw, "GameFontHighlight") + 10)
    at(cb, R.pad, P.y)
    cb.refresh = function() cb:SetChecked(getter() and true or false) end
    cb:SetScript("OnClick", function(self) setter(self:GetChecked() and true or false) end)
    tooltip(cb, label, tip)
    cb.refresh()
    widgets[#widgets + 1] = cb
    rec(cb, R.pad, P.y, col - (right or 0), h, "check", label)
    cb.__rowY, cb.__rowH = P.y, h
    P.y = P.y + h
    return cb
  end
  -- curseur : titre (ligne à lui, coupé sur plusieurs lignes au besoin) · barre + valeur à droite · min / max dessous
  function P.slider(label, lo, hi, step, getter, setter)
    local fs = wrapFS(content, "GameFontNormalSmall", col, label)
    local lh = measure(fs, label, col, "GameFontNormalSmall")
    local ok, s = pcall(_G.CreateFrame, "Slider", nil, content, "OptionsSliderTemplate")
    if not ok or not s then return nil end
    at(fs, R.pad, P.y)
    local sw = min(300, col - 70)
    s:SetSize(sw, 17)
    s:SetMinMaxValues(lo, hi)
    s:SetValueStep(step)
    if s.SetObeyStepOnDrag then s:SetObeyStepOnDrag(true) end
    at(s, R.pad + 6, P.y + lh + 3)
    local t = s.Text or s.text
    if type(t) == "table" and t.SetText then t:SetText("") end
    local l, h = s.Low, s.High
    if type(l) == "table" and l.SetText then l:SetText(tostring(lo)) end
    if type(h) == "table" and h.SetText then h:SetText(tostring(hi)) end
    local val = content:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    val:SetJustifyH("LEFT")
    val:SetWidth(56)
    val:SetPoint("LEFT", s, "RIGHT", 10, 0)
    s.label, s.valueText = fs, val
    local function show(v) val:SetText(tostring(v)) end
    s.refresh = function()
      local v = getter() or lo
      s:SetValue(v)
      show(v)
    end
    s:SetScript("OnValueChanged", function(_, v)
      v = floor(v / step + 0.5) * step
      if step < 1 then v = floor(v * 100 + 0.5) / 100 end
      show(v)
      setter(v)
    end)
    s.refresh()
    widgets[#widgets + 1] = s
    local rowH = max(R.slider, lh + 3 + 17 + 16 + 4)
    rec(fs, R.pad, P.y, col, rowH, "slider", label)
    P.rects[#P.rects].ctl = s
    P.y = P.y + rowH
    return s
  end
  -- bouton : largeur commune (R.btnW) ; toute la colonne si le texte ne tient pas
  function P.button(label, _, fn, textFn, opts)
    local b = newButton(content, label, R.btnW, fn, textFn)
    if not b then return nil end
    local txt = b.GetText and b:GetText() or label
    local bw = (ulen(txt) * 6.4 + 28 > R.btnW) and col or min(R.btnW, col)
    local bh = (opts and opts.h) or R.btnH
    b:SetSize(bw, bh)
    at(b, R.pad, P.y + 2)
    widgets[#widgets + 1] = b
    local rowH = max(R.button, bh + 4)
    rec(b, R.pad, P.y, bw, rowH, "button", txt)
    P.y = P.y + rowH
    return b
  end
  -- grille de boutons de même largeur (cols colonnes) : list = { {label, fn, textFn}, … }
  function P.buttons(list, cols)
    cols = cols or 2
    local bw = floor((col - (cols - 1) * 8) / cols)
    local out = {}
    for i, it in ipairs(list) do
      local c = (i - 1) % cols
      if c == 0 and i > 1 then P.y = P.y + R.button end
      local b = newButton(content, it[1], bw, it[2], it[3])
      if b then
        local x = R.pad + c * (bw + 8)
        at(b, x, P.y + 2)
        widgets[#widgets + 1] = b
        rec(b, x, P.y, bw, R.button, "button", b.GetText and b:GetText() or it[1])
        out[#out + 1] = b
      end
    end
    if #list > 0 then P.y = P.y + R.button end
    return out
  end
  -- petit bouton aligné à droite sur la ligne d'une case à cocher (réservée avec P.check(..., right))
  function P.side(cb, label, w, fn)
    if not cb then return nil end
    local b = newButton(content, label, w, fn)
    if not b then return nil end
    local x = R.pad + col - w
    at(b, x, cb.__rowY + 1)
    widgets[#widgets + 1] = b
    rec(b, x, cb.__rowY, w, min(cb.__rowH, R.button), "button", label)
    return b
  end
  function P.finish() content:SetHeight(P.y + 16); content:SetWidth(width) end
  return P
end

local function refreshAll()
  for _, w in ipairs(widgets) do if w.refresh then pcall(w.refresh) end end
end

-- largeur utile de la colonne de droite (canvas Blizzard ≈ 600–700 px ; repli 440)
local function pageWidth()
  local w = canvas and tonumber(canvas.GetWidth and canvas:GetWidth()) or 0
  w = w - R.navW - 12 - 30
  if w < 360 then w = 440 end
  return min(640, w)
end

-- une page = un cadre de contenu (enfant de la ScrollFrame de droite), construit à la première ouverture
local scroll, navChild, navButtons, selected
local function newPage(name, build, key)
  local f = _G.CreateFrame("Frame", nil, scroll or _G.UIParent)
  f.name, f.key = name, key
  f.builder = build   -- (UIREDO) la même page, reconstruite dans la fenêtre TreeAI (page « Options »)
  f:SetSize(440, 100)
  f:Hide()
  local built = false
  f.build = function()
    if built then return end
    built = true
    TreeAI.safecall(build, f, pageWidth())
  end
  return f
end
---------------------------------------------------------------------------
-- page « Accueil » : démarrer · panneau · encadré d'alerte · sons et messages · cadres · général
---------------------------------------------------------------------------
local function buildMain(content, width)
  -- toujours le profil actif (compte ou perso), même après « /tai profil perso »
  local S = setmetatable({}, {
    __index = function(_, k) return TreeAI.settings and TreeAI.settings[k] end,
    __newindex = function(_, k, v) if TreeAI.settings then TreeAI.settings[k] = v end end,
  })
  local P = layout(content, width)
  local Wn = TreeAI.Window
  local ver = (Wn and Wn.Version and Wn.Version()) or tostring(TreeAI.version)   -- (4.0 QA) la version du TOC
  P.text("TreeAI Coach  |cff999999v" .. ver .. "|r", "GameFontNormalLarge")
  P.text(L["TreeAI conseille ; il n'agit à ta place que pour les automatismes que tu coches (quêtes, marchand)."])
  P.text("|cff999999" .. L["Chaque module a sa page (liste à gauche)."] .. "|r")

  P.header(L["Démarrer"])
  local function presetText()
    local cur = S.preset or "normal"
    return L["Conseils"] .. " : " .. L[(TreeAI.PRESET_LABEL or {})[cur] or "Perso"] .. "  (" .. L["clic : changer"] .. ")"
  end
  P.button("", nil, function()
    local order, cur = TreeAI.PRESET_ORDER or { "normal" }, S.preset
    local nxt = order[1]
    for i, p in ipairs(order) do if p == cur then nxt = order[i % #order + 1] end end
    TreeAI:ApplyPreset(nxt)
    refreshAll()
  end, presetText)
  P.text("|cff999999" .. L["Simple : 3 conseils utiles · Mode calme : 3, alertes rares · Normal : 5 · Tout : 8, même les petits."] .. "|r")
  -- (COHERE) qui guide les quêtes à l'écran : Suivi Forever / Questie (s'ils sont là) ou TreeAI — jamais deux
  P.button("", nil, function()
    TreeAI:SetGuideMode(TreeAI:GuideMode() == "treeai" and "auto" or "treeai")
    refreshAll()
  end, function()
    local m = TreeAI.GuideMode and TreeAI:GuideMode() or "auto"
    return L["Guidage"] .. " : " .. L[(TreeAI.GUIDE_LABEL or {})[m] or m] .. "  (" .. L["clic : changer"] .. ")"
  end)
  P.text("|cff999999" .. L["Suivi Forever : sa liste et sa flèche guident, TreeAI garde l'équipement, le groupe, les conseils. TreeAI : la boussole et les étapes de TreeAI."] .. "|r")
  P.button("", nil, function()
    TreeAI:SetProfile(TreeAI:ProfileMode() == "char" and "account" or "char")
    refreshAll()
  end, function()
    return L["Réglages"] .. " : " .. (TreeAI:ProfileMode() == "char" and L["propres à ce personnage"] or L["communs au compte"])
  end)
  P.buttons({
    { L["Assistant de démarrage"], function() if TreeAI.ShowWizard then TreeAI:ShowWizard(1) end end },
    { L["Toutes les actions"], function() if TreeAI.ShowHelp then TreeAI:ShowHelp() end end },
  }, 2)
  if TreeAI.Tour and TreeAI.Tour.OptionsButton then TreeAI.Tour.OptionsButton(P) end   -- (ONBOARD) « Visite guidée »

  P.header(L["Panneau texte (ancien)"])
  P.check(L["Afficher le panneau"], function() return S.panel.shown end,
    function(v) if v then TreeAI:ShowPanel() else TreeAI:HidePanel() end end)
  P.check(L["Mode mini (une seule ligne) pour ce personnage"], function() return TreeAI.IsMini and TreeAI:IsMini() end,
    function(v) if TreeAI.SetMini then TreeAI:SetMini(v) end end)
  P.check(L["Verrouiller le panneau"], function() return S.panel.locked end,
    function(v) S.panel.locked = v; TreeAI:RefreshPanel() end)
  P.slider(L["Lignes visibles (molette pour défiler)"], 3, 15, 1, function() return S.panel.maxLines end,
    function(v) S.panel.maxLines = v; TreeAI:RefreshPanel() end)
  P.slider(L["Conseils dans « Maintenant »"], 1, 8, 1, function() return S.panel.nowMax end,
    function(v)
      if S.panel.nowMax ~= v then S.panel.nowMax = v; S.preset = "perso" end
      TreeAI:RefreshPanel()
    end)
  P.slider(L["Largeur"], 300, 460, 10, function() return S.panel.width end,
    function(v) S.panel.width = v; TreeAI:ApplyPanelSettings() end)
  P.slider(L["Échelle"], 0.6, 1.6, 0.05, function() return S.panel.scale end,
    function(v) S.panel.scale = v; TreeAI:ApplyPanelSettings() end)
  P.slider(L["Opacité"], 0.2, 1, 0.05, function() return S.panel.alpha end,
    function(v) S.panel.alpha = v; TreeAI:ApplyPanelSettings() end)
  P.button(L["Remettre le panneau au centre"], nil, function() TreeAI:ResetPanel() end)

  P.header(L["Encadré d'alerte (important seulement)"])
  P.check(L["Afficher les alertes importantes dans un coin"], function() return S.alert.enabled end,
    function(v) S.alert.enabled = v end)
  local CORNER_FR = { TOPRIGHT = L["en haut à droite"], TOPLEFT = L["en haut à gauche"],
    BOTTOMLEFT = L["en bas à gauche"], BOTTOMRIGHT = L["en bas à droite"] }
  P.buttons({
    { "", function()
      local list = TreeAI.ALERT_CORNERS or { "TOPRIGHT" }
      local nxt = list[1]
      for i, c in ipairs(list) do if c == S.alert.corner then nxt = list[i % #list + 1] end end
      S.alert.corner = nxt
      if TreeAI.ApplyAlertSettings then TreeAI:ApplyAlertSettings() end
    end, function() return L["Coin"] .. " : " .. (CORNER_FR[S.alert.corner] or S.alert.corner or "?") end },
    { L["Tester l'encadré"], function() TreeAI:HandleSlash("alerte") end },
  }, 2)
  P.slider(L["Durée d'affichage (s)"], 3, 30, 1, function() return S.alert.duration end,
    function(v) S.alert.duration = v end)
  P.slider(L["Échelle de l'encadré"], 0.6, 1.6, 0.05, function() return S.alert.scale end,
    function(v) S.alert.scale = v; if TreeAI.ApplyAlertSettings then TreeAI:ApplyAlertSettings() end end)
  P.slider(L["Opacité de l'encadré"], 0.3, 1, 0.05, function() return S.alert.alpha end,
    function(v) S.alert.alpha = v end)

  P.header(L["Sons et messages"])
  local snd = P.check(L["Sons TreeAI (alertes, quêtes, rares…)"], function() return S.sound end,
    function(v) S.sound = v end, nil, 96)
  P.side(snd, L["Réglages"], 88, function() TreeAI:OpenOptions("sounds") end)
  P.check(L["Sons aussi pour certains conseils (rares…)"], function() return S.soundTips end,
    function(v) S.soundTips = v end, L["Par défaut, seul l'important (priorité 3) fait du bruit."])
  P.check(L["Écrire aussi les conseils dans le chat (déconseillé)"], function() return S.chat end,
    function(v) S.chat = v end, L["Par défaut, aucun conseil n'est écrit dans le chat : tout va dans le panneau."])
  P.slider(L["Priorité minimale"], 1, 3, 1, function() return S.minPrio end,
    function(v) S.minPrio = v; TreeAI:RefreshPanel() end)

  P.header(L["Cadres et performances"])
  P.buttons({
    { L["Déplacer les cadres"], function() if TreeAI.SetEditMode then TreeAI:SetEditMode(true) end end },
    { L["Placer automatiquement"], function() if TreeAI.AutoLayout then TreeAI:AutoLayout(true) end end },
    -- (LITE) performances : coût par module, profil essentiel / tout, liste des modules (Plus → Modules)
    { L["Coût des fonctions"], function()
      local Wn = TreeAI.Window   -- (4.0 QA) dans la page « Fonctions » de la fenêtre, jamais dans le chat
      if not (Wn and Wn.ShowPerf and Wn.ShowPerf()) and TreeAI.PrintPerf then TreeAI:PrintPerf() end
    end },
    { L["Fonctions (allumer / couper)"], function() if TreeAI.SelectTab then TreeAI:SelectTab("modules") end end },
  }, 2)
  P.button(L["Réinitialiser l'interface"], nil, function()
    if TreeAI.ResetInterface then TreeAI:ResetInterface() end
    refreshAll()
  end, nil, { h = 28 })
  P.text("|cff999999" .. L["Tous les cadres TreeAI reviennent à leur place automatique (loin de la minimap, des suivis de quêtes et du groupe)."] .. "|r")

  P.header(L["Général"])
  P.check(L["Activer TreeAI"], function() return S.enabled end, function(v) TreeAI:SetEnabled(v) end)
  P.check(L["Synchro avec le groupe (messages d'addon)"], function() return S.comms end, function(v) S.comms = v end)
  P.check(L["Aides PvP (contre les joueurs, champs de bataille)"], function() return S.pvp end,
    function(v) S.pvp = v end, L["Désactivé : l'addon aide à quêter et se tait face aux joueurs ennemis."])
  P.finish()
end
---------------------------------------------------------------------------
-- page « Apparence » : micro-réglages de chaque cadre (taille, transparence, largeur, police, interligne,
-- icônes) + afficher / cacher chaque cadre ; défauts réglés sur un écran 2000×1125.
---------------------------------------------------------------------------
local function modDB(name)
  local m = TreeAI.modules and TreeAI.modules[name]
  return m, m and m.db
end
local function modSet(name, key, v)
  local m, db = modDB(name)
  if not db then return end
  db[key] = v
  if m.OnOptionChanged then TreeAI.safecall(m.OnOptionChanged, m, key, v) end
  TreeAI:RequestRefresh()
end
local function modGet(name, key) local _, db = modDB(name); return db and db[key] end

local function buildAppearance(content, width)
  local P = layout(content, width)
  local function S() return TreeAI.settings end
  local function panelSet(k, v) local s = S(); if s and s.panel then s.panel[k] = v; TreeAI:ApplyPanelSettings() end end
  local function panelGet(k) local s = S(); return s and s.panel and s.panel[k] end
  P.text(L["Apparence"], "GameFontNormalLarge")
  P.text("|cff999999" .. L["Chaque cadre TreeAI, réglé au pixel près. Rien ne touche aux cadres de Blizzard."] .. "|r")
  P.buttons({
    { L["Réinitialiser l'interface"], function()
      if TreeAI.ResetInterface then TreeAI:ResetInterface() end
      refreshAll()
    end },
    { L["Déplacer les cadres"], function() if TreeAI.SetEditMode then TreeAI:SetEditMode(true) end end },
  }, 2)

  P.header(L["Disposition (un clic)"])
  P.text("|cff999999" .. L["Compacte : petit, groupé près de la minimap · Confort : grand, sur un bord · Coins : chaque cadre dans un coin libre."] .. "|r")
  local disp = {}
  for _, key in ipairs(TreeAI.DISPOSITION_ORDER or {}) do
    local D = TreeAI.DISPOSITIONS[key]
    disp[#disp + 1] = { L[D.label], function()
      if TreeAI.ApplyDisposition then TreeAI:ApplyDisposition(key) end
      refreshAll()
    end, function()
      return ((TreeAI.GetDisposition and TreeAI:GetDisposition() == key) and "|cff33ff66> " or "") .. L[D.label]
    end }
  end
  P.buttons(disp, 3)
  P.check(L["S'écarter quand un autre cadre grandit (suivi de quêtes, groupe…)"],
    function() local s = S() return s and s.reflow ~= false end,
    function(v) local s = S(); if s then s.reflow = v and true or false end end,
    L["Le cadre TreeAI glisse vers la place libre la plus proche, puis revient quand c'est libre."])
  P.check(L["Déposé sur un autre cadre : glisser vers la place libre"],
    function() local s = S() return s and s.snapFree ~= false end,
    function(v) local s = S(); if s then s.snapFree = v and true or false end end)

  P.header(L["Panneau texte (ancien)"])
  P.check(L["Afficher le panneau"], function() return panelGet("shown") end,
    function(v) if v then TreeAI:ShowPanel() else TreeAI:HidePanel() end end)
  P.slider(L["Échelle"], 0.6, 1.6, 0.05, function() return panelGet("scale") end, function(v) panelSet("scale", v) end)
  P.slider(L["Opacité"], 0.2, 1, 0.05, function() return panelGet("alpha") end, function(v) panelSet("alpha", v) end)
  P.slider(L["Largeur"], 300, 460, 10, function() return panelGet("width") end, function(v) panelSet("width", v) end)
  P.slider(L["Taille du texte"], 9, 16, 1, function() return panelGet("fontSize") or 11 end, function(v) panelSet("fontSize", v) end)
  P.slider(L["Espace entre les lignes"], 0, 10, 1, function() return panelGet("lineGap") or 3 end, function(v) panelSet("lineGap", v) end)
  P.slider(L["Taille des icônes"], 10, 20, 1, function() return panelGet("iconSize") or 13 end, function(v) panelSet("iconSize", v) end)
  P.slider(L["Lignes visibles (molette pour défiler)"], 3, 15, 1, function() return panelGet("maxLines") end,
    function(v) panelSet("maxLines", v) end)

  P.header(L["Encadré d'alerte"])
  local function al() local s = S(); return s and s.alert or {} end
  P.check(L["Afficher les alertes importantes"], function() return al().enabled end, function(v) al().enabled = v end)
  P.slider(L["Échelle de l'encadré"], 0.6, 1.6, 0.05, function() return al().scale end,
    function(v) al().scale = v; if TreeAI.ApplyAlertSettings then TreeAI:ApplyAlertSettings() end end)
  P.slider(L["Opacité de l'encadré"], 0.3, 1, 0.05, function() return al().alpha end, function(v) al().alpha = v end)

  P.header(L["Radar"])
  P.check(L["Afficher le radar"], function() return modGet("Radar", "shown") ~= false end,
    function(v) local m = TreeAI.modules and TreeAI.modules.Radar; if m and m.Show then TreeAI.safecall(m.Show, v) end end)
  P.check(L["Radar compact (120 px)"], function() return modGet("Radar", "compact") end, function(v) modSet("Radar", "compact", v) end)
  P.check(L["Cacher le radar en combat"], function() return modGet("Radar", "hideCombat") end, function(v) modSet("Radar", "hideCombat", v) end)
  P.slider(L["Taille du radar"], 110, 300, 10, function() return modGet("Radar", "size") end, function(v) modSet("Radar", "size", v) end)
  P.slider(L["Opacité du fond du radar"], 0.2, 1, 0.05, function() return modGet("Radar", "opacity") end, function(v) modSet("Radar", "opacity", v) end)

  -- points TreeAI sur la vraie minimap (seulement ce que Questie ne montre pas)
  local MPm = TreeAI.modules and TreeAI.modules.MinimapPins
  if MPm and type(MPm.LAYERS) == "table" then
    P.header(L["Points sur la minimap"])
    for _, layer in ipairs(MPm.LAYERS) do
      P.check(L[(MPm.LABELS and MPm.LABELS[layer]) or layer], function() return modGet("MinimapPins", layer) ~= false end,
        function(v) modSet("MinimapPins", layer, v) end)
    end
  end

  P.header(L["Boussole"])
  P.check(L["Afficher la boussole"], function() return modGet("Compass", "shown") ~= false end,
    function(v) local m = TreeAI.modules and TreeAI.modules.Compass; if m and m.Show then TreeAI.safecall(m.Show, v) end end)
  P.check(L["Une seule flèche (cacher si Blizzard / Shortest Path / TomTom guide déjà)"],
    function() return modGet("Compass", "hideDup") ~= false end, function(v) modSet("Compass", "hideDup", v) end)
  P.slider(L["Taille de la boussole"], 0.5, 2, 0.1, function() return modGet("Compass", "scale") end, function(v) modSet("Compass", "scale", v) end)

  P.header(L["Suivi des quêtes TreeAI"])
  P.check(L["Afficher le cadre de suivi"], function() return modGet("Tracker", "frame") end, function(v) modSet("Tracker", "frame", v) end)
  P.slider(L["Échelle (%)"], 50, 200, 5, function() return modGet("Tracker", "scale") end, function(v) modSet("Tracker", "scale", v) end)
  P.slider(L["Opacité du fond (%)"], 0, 100, 5, function() return modGet("Tracker", "alpha") end, function(v) modSet("Tracker", "alpha", v) end)
  P.slider(L["Largeur"], 200, 460, 10, function() return modGet("Tracker", "width") end, function(v) modSet("Tracker", "width", v) end)
  P.slider(L["Taille du texte"], 8, 16, 1, function() return modGet("Tracker", "fontSize") end, function(v) modSet("Tracker", "fontSize", v) end)

  P.header(L["Fenêtre « À faire »"])
  P.slider(L["Échelle (%)"], 50, 200, 5, function() return modGet("Todo", "scale") end, function(v) modSet("Todo", "scale", v) end)
  P.slider(L["Opacité (%)"], 20, 100, 2, function() return modGet("Todo", "alpha") end, function(v) modSet("Todo", "alpha", v) end)
  P.slider(L["Taille du texte"], 9, 16, 1, function() return modGet("Todo", "fontSize") end, function(v) modSet("Todo", "fontSize", v) end)
  P.finish()
end

---------------------------------------------------------------------------
-- one page per module
---------------------------------------------------------------------------
-- page « Sons » : un son par genre (sons du jeu), on/off + écouter, canal, écart minimum ; jamais en combat sauf danger
local function buildSounds(content, width)
  local P = layout(content, width)
  local function S() return TreeAI.settings end
  local function SS() return TreeAI.SoundSettings and TreeAI:SoundSettings() end
  P.text(L["Sons"], "GameFontNormalLarge")
  P.text("|cff999999" .. L["Sons du jeu, discrets. En combat : seulement le danger. Jamais deux sons à la suite."] .. "|r")
  P.check(L["Sons TreeAI activés"], function() local s = S() return s and s.sound ~= false end,
    function(v) local s = S(); if s then s.sound = v and true or false end end)
  P.button("", nil, function()
    local ss = SS(); if not ss then return end
    local list, cur = TreeAI.SOUND_CHANNELS, ss.channel or "Master"
    local nxt = list[1]
    for i, c in ipairs(list) do if c == cur then nxt = list[i % #list + 1] end end
    ss.channel = nxt
  end, function()
    local ss = SS()
    local c = ss and ss.channel or "Master"
    return L["Canal"] .. " : " .. L[(TreeAI.SOUND_CHANNEL_LABEL or {})[c] or c] .. "  (" .. L["clic : changer"] .. ")"
  end)
  P.slider(L["Écart minimum entre deux sons (s)"], 1, 10, 0.5,
    function() local ss = SS() return ss and ss.gap or TreeAI.SOUND_GAP end,
    function(v) local ss = SS(); if ss then ss.gap = v end end)
  P.header(L["Par genre"])
  for _, kind in ipairs(TreeAI.SOUND_ORDER or {}) do
    local def = TreeAI.SOUND_KINDS[kind]
    local cb = P.check(L[def.label], function() return TreeAI:SoundKindOn(kind) end,
      function(v) TreeAI:SetSoundKind(kind, v) end, nil, 92)
    P.side(cb, L["Écouter"], 84, function() TreeAI:PreviewSound(kind) end)
  end
  P.header(L["Défaut"])
  P.button(L["Tout remettre par défaut"], nil, function()
    local ss = SS()
    if ss then ss.kinds, ss.channel, ss.gap = {}, "Master", TreeAI.SOUND_GAP end
    refreshAll()
  end)
  P.finish()
end

local function buildModule(content, name, mod, width)
  local P = layout(content, width)
  local beta = TreeAI.IsBeta and TreeAI:IsBeta(name)
  P.text(tostring(mod.title or name) .. (beta and "  |cffb48cff(bêta)|r" or ""), "GameFontNormalLarge")
  if mod.description then P.text(tostring(mod.description)) end
  if beta then
    P.text("|cff999999" .. L["Fonction bêta : testée hors du jeu seulement. Coupée en mode sûr, à allumer ici."] .. "|r")
  end
  if type(mod.classes) == "table" then
    local cls = {}
    for c in pairs(mod.classes) do cls[#cls + 1] = c end
    table.sort(cls)
    P.text("|cff999999" .. L["Classes"] .. " : " .. table.concat(cls, ", ") .. "|r")
  end
  P.check(L["Module activé"], function() return mod.db and mod.db.enabled end,
    function(v) TreeAI:SetModuleEnabled(name, v) end)
  local function changed(key, v)
    if mod.OnOptionChanged then TreeAI.safecall(mod.OnOptionChanged, mod, key, v) end
    TreeAI:RequestRefresh()
  end
  if type(mod.options) == "table" and #mod.options > 0 then
    -- (4.0 QA) un titre par section, pas un par case : beaucoup de modules répètent `section` sur chaque option,
    -- d'autres reviennent à une section plus haut. Une option sans `section` suit la précédente ; les sections
    -- gardent l'ordre de leur première apparition, les options leur ordre dans la section.
    local secOrder, bySec, cur = {}, {}, false
    for _, o in ipairs(mod.options) do
      if o.section then cur = o.section end
      if not bySec[cur] then bySec[cur] = {}; secOrder[#secOrder + 1] = cur end
      local b = bySec[cur]
      b[#b + 1] = o
    end
    local ordered = {}
    for _, sec in ipairs(secOrder) do
      for i, o in ipairs(bySec[sec]) do ordered[#ordered + 1] = { o = o, head = i == 1 and (sec or L["Réglages"]) or nil } end
    end
    for _, it in ipairs(ordered) do
      local o = it.o
      local key = o.key
      if it.head then P.header(it.head) end
      if o.type == "choice" and type(o.values) == "table" and #o.values > 0 then
        P.button("", nil, function()
          if not mod.db then return end
          local n = (tonumber(mod.db[key]) or 1) % #o.values + 1
          mod.db[key] = n; changed(key, n)
        end, function()
          return (o.label or key) .. " : " .. tostring(o.values[tonumber(mod.db and mod.db[key]) or 1] or "?")
        end)
      elseif o.type == "button" and type(o.onClick) == "function" then   -- action du module (clic = action du joueur)
        P.button(o.label or key or "?", nil, function() TreeAI:UserAction(o.onClick, mod) end)
      elseif o.type == "range" then
        P.slider(o.label or key, o.min or 0, o.max or 100, o.step or 1,
          function() return mod.db and mod.db[key] end,
          function(v) if mod.db then mod.db[key] = v; changed(key, v) end end)
      elseif key then
        P.check(o.label or key, function() return mod.db and mod.db[key] end,
          function(v) if mod.db then mod.db[key] = v; changed(key, v) end end, o.tip)
      end
    end
  elseif type(mod.BuildOptions) ~= "function" then
    P.text("|cff999999" .. L["Pas d'autre réglage pour ce module."] .. "|r")
  end
  -- (ZONEPLAN) réglages propres au module (listes, boutons), posés par le même moteur de mise en page
  if type(mod.BuildOptions) == "function" then TreeAI.safecall(mod.BuildOptions, mod, P) end
  P.finish()
end

-- ---------------------------------------------------------------- canvas : arbre des catégories + page à droite
-- familles de modules (ordre logique : jouer → combat → groupe → sacs → réglages) ; inconnu → « Autres »
local FAMILY = {
  { title = "Quêtes et trajet", mods = { "Quests", "Tracker", "Todo", "ZonePlan", "Planner", "Route", "Compass", "NavTime",
    "Horizon", "Radar", "MinimapPins", "Highlight", "Abandon", "Region", "Speed", "Leveling", "Errands", "Edge2" } },
  { title = "Combat et classe", mods = { "Hunter", "Classes", "Fight", "Boost", "Talents", "Macros", "Binds", "Detect",
    "QuickFight", "PvPMark" } },
  { title = "Groupe", mods = { "Group", "Dungeon", "QuickTravel", "PvP" } },
  { title = "Sacs, or, métiers", mods = { "Inventory", "Auction", "Gear", "Prof" } },
  { title = "Outils", mods = { "Window", "Shortcuts", "Hub", "Session", "Stats", "Pro", "Theme", "Export", "SelfTest", "Bugs" } },
}
TreeAI.OPTIONS_FAMILY = FAMILY

local function navEntries()
  local out = { { head = "TreeAI" }, { page = homePage, label = L["Accueil"] }, { page = appearance, label = L["Apparence"] },
    { page = soundsPage, label = L["Sons"] } }
  local byName, used = {}, {}
  for _, p in ipairs(pages) do byName[p.module] = p end
  for _, fam in ipairs(FAMILY) do
    local first = true
    for _, n in ipairs(fam.mods) do
      local p = byName[n]
      if p and not used[n] then
        if first then out[#out + 1] = { head = L[fam.title] }; first = false end
        used[n] = true
        out[#out + 1] = { page = p.frame, label = p.frame.name }
      end
    end
  end
  local first = true
  for _, p in ipairs(pages) do
    if not used[p.module] then
      if first then out[#out + 1] = { head = L["Autres"] }; first = false end
      out[#out + 1] = { page = p.frame, label = p.frame.name }
    end
  end
  return out
end

local function selectPage(page)
  if not page then return end
  if selected and selected ~= page then selected:Hide() end
  selected = page
  page.build()
  page:Show()
  if scroll then
    scroll:SetScrollChild(page)
    if scroll.SetVerticalScroll then pcall(scroll.SetVerticalScroll, scroll, 0) end
  end
  for _, b in ipairs(navButtons or {}) do
    if b.page then
      local on = b.page == page
      b.fs:SetText((on and accent() or "|cffffffff") .. b.label .. "|r")
      if b.sel then if on then b.sel:Show() else b.sel:Hide() end end
    end
  end
  refreshAll()
end
TreeAI.SelectOptionPage = function(_, page) selectPage(page) end

local function buildNav()
  if navButtons or not canvas then return end
  navButtons = {}
  local ok, ns = pcall(_G.CreateFrame, "ScrollFrame", nil, canvas, "UIPanelScrollFrameTemplate")
  local parent = canvas
  if ok and ns then
    ns:SetPoint("TOPLEFT", canvas, "TOPLEFT", 4, -8)
    ns:SetPoint("BOTTOMLEFT", canvas, "BOTTOMLEFT", 4, 8)
    ns:SetWidth(R.navW - 22)
    navChild = _G.CreateFrame("Frame", nil, ns)
    navChild:SetSize(R.navW - 22, 100)
    ns:SetScrollChild(navChild)
    parent = navChild
  end
  local y = 2
  local rects = {}
  local iw = R.navW - 26
  for _, e in ipairs(navEntries()) do
    if e.head then
      if y > 2 then y = y + 6 end
      local fs = wrapFS(parent, "GameFontNormalSmall", iw, accent() .. e.head .. "|r")
      if fs.SetWordWrap then fs:SetWordWrap(false) end
      fs:SetPoint("TOPLEFT", parent, "TOPLEFT", 2, -(y + 6))
      rects[#rects + 1] = { x = 2, y = y, w = iw, h = R.navHead, label = e.head }
      y = y + R.navHead
    elseif e.page then
      local b = _G.CreateFrame("Button", nil, parent)
      b:SetSize(iw, R.navRow)
      b:SetPoint("TOPLEFT", parent, "TOPLEFT", 2, -y)
      b:RegisterForClicks("LeftButtonUp")
      local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
      fs:SetPoint("LEFT", b, "LEFT", 8, 0)
      fs:SetWidth(iw - 10)
      fs:SetJustifyH("LEFT")
      if fs.SetWordWrap then fs:SetWordWrap(false) end
      b.fs, b.page, b.label = fs, e.page, e.label
      local hl = b:CreateTexture(nil, "HIGHLIGHT")
      hl:SetAllPoints(b)
      if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end
      local sel = b:CreateTexture(nil, "BACKGROUND")
      sel:SetAllPoints(b)
      if sel.SetColorTexture then sel:SetColorTexture(accentRGB(0.18)) end
      sel:Hide()
      b.sel = sel
      fs:SetText(e.label)
      b:SetScript("OnClick", function(self) selectPage(self.page) end)
      navButtons[#navButtons + 1] = b
      rects[#rects + 1] = { x = 2, y = y, w = iw, h = R.navRow, label = e.label }
      y = y + R.navRow
    end
  end
  if navChild then navChild:SetHeight(y + 8) end
  canvas.__nav = { W = R.navW - 22, rects = rects, height = y }
  -- séparateur vertical entre l'arbre et la page
  local sep = canvas:CreateTexture(nil, "ARTWORK")
  if sep.SetColorTexture then sep:SetColorTexture(accentRGB(0.3)) end
  sep:SetWidth(1)
  sep:SetPoint("TOPLEFT", canvas, "TOPLEFT", R.navW, -8)
  sep:SetPoint("BOTTOMLEFT", canvas, "BOTTOMLEFT", R.navW, 8)
end

local function buildCanvas()
  if not canvas or canvas.__built then return end
  canvas.__built = true
  buildNav()
  selectPage(selected or homePage)
end

function TreeAI:InitOptions()
  if canvas or not _G.CreateFrame then return end
  canvas = _G.CreateFrame("Frame", nil)
  canvas.name = "TreeAI Coach"
  local ok, sc = pcall(_G.CreateFrame, "ScrollFrame", nil, canvas, "UIPanelScrollFrameTemplate")
  if ok and sc then
    scroll = sc
    scroll:SetPoint("TOPLEFT", canvas, "TOPLEFT", R.navW + 8, -8)
    scroll:SetPoint("BOTTOMRIGHT", canvas, "BOTTOMRIGHT", -28, 8)
  end
  homePage = newPage(L["Accueil"], buildMain, "home")
  appearance = newPage(L["Apparence"], buildAppearance, "appearance")
  soundsPage = newPage(L["Sons"], buildSounds, "sounds")
  for name, mod in self:IterModules() do
    local page = newPage(mod.title or name, function(content, w) buildModule(content, name, mod, w) end, name)
    pages[#pages + 1] = { frame = page, module = name }
  end
  canvas.build = function() buildCanvas(); homePage.build() end
  canvas:SetScript("OnShow", function() buildCanvas(); refreshAll() end)

  local Settings = _G.Settings
  local modern = Settings and Settings.RegisterCanvasLayoutCategory and Settings.RegisterAddOnCategory
  if modern then
    local okc, cat = pcall(Settings.RegisterCanvasLayoutCategory, canvas, canvas.name)
    if okc and cat then
      category = cat
      pcall(Settings.RegisterAddOnCategory, cat)
    else
      modern = false
    end
  end
  if not modern and _G.InterfaceOptions_AddCategory then pcall(_G.InterfaceOptions_AddCategory, canvas) end
end

-- (UIREDO) la fenêtre TreeAI montre les mêmes pages (même moteur) : { {head} | {key, label, module, builder} }
function TreeAI:OptionEntries()
  if not canvas then self:InitOptions() end
  local out = {}
  local modOf = {}
  for _, p in ipairs(pages) do modOf[p.frame] = p.module end
  for _, e in ipairs(navEntries()) do
    if e.head then out[#out + 1] = { head = e.head }
    elseif e.page then
      out[#out + 1] = { key = e.page.key or e.label, label = e.label, module = modOf[e.page], builder = e.page.builder }
    end
  end
  return out
end
function TreeAI:RefreshOptionWidgets() refreshAll() end

-- canvas (racine), pages de modules, Apparence, Sons, Accueil
function TreeAI:GetOptionPages() return canvas, pages, appearance, soundsPage, homePage end
--- Rectangles posés par le moteur sur une page (nil si pas encore construite) ; `nav` = l'arbre de gauche.
function TreeAI:GetOptionLayout(page)
  if page == "nav" then return canvas and canvas.__nav end
  return page and page.__layout
end

function TreeAI:OpenOptions(which)
  if self:InCombat() then if self.CombatRefused then self:CombatRefused() else self:Print(L["Impossible en combat."]) end return end
  if not canvas then self:InitOptions() end
  local target = homePage
  if which == "sounds" then target = soundsPage
  elseif which == "appearance" then target = appearance
  elseif type(which) == "string" then
    for _, p in ipairs(pages) do if p.module == which then target = p.frame end end
  end
  if canvas.__built then selectPage(target) else selected = target end
  local Settings = _G.Settings
  if category and Settings and Settings.OpenToCategory then
    local id = category.GetID and category:GetID() or category.ID
    if pcall(Settings.OpenToCategory, id) then return end
  end
  if _G.InterfaceOptionsFrame_OpenToCategory and canvas then
    -- called twice: known Blizzard quirk on first open
    pcall(_G.InterfaceOptionsFrame_OpenToCategory, canvas)
    pcall(_G.InterfaceOptionsFrame_OpenToCategory, canvas)
  end
end

---------------------------------------------------------------------------
-- Assistant de démarrage (3 étapes) et page d'aide (/tai aide). Cadres simples enfants d'UIParent,
-- rien de protégé, aucun cadre Blizzard touché.
---------------------------------------------------------------------------
local function dialog(name, w, h)
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  local f = _G.CreateFrame("Frame", name, _G.UIParent, template)
  f:SetSize(w, h)
  f:SetPoint("CENTER", _G.UIParent, "CENTER", 0, 80)
  f:SetFrameStrata("DIALOG")
  f:SetClampedToScreen(true)
  f:SetMovable(true)
  f:EnableMouse(true)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", function(self) self:StartMoving() end)
  f:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
  if f.SetBackdrop then
    f:SetBackdrop({
      bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      tile = true, tileSize = 16, edgeSize = 14,
      insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    f:SetBackdropColor(0.05, 0.08, 0.06, 0.95)
    f:SetBackdropBorderColor(0.2, 0.8, 0.4, 0.9)
  end
  -- en-tête commun (icône + titre + réduire + fermer, Échap ferme) ; repli : titre + « x »
  local Th = TreeAI.Theme
  local hd = Th and Th.Window and Th:Window(f, { id = name, title = "", icon = "Interface\\Icons\\INV_Misc_Book_09",
    min = false })
  if hd and hd.title then
    f.title, f.close = hd.title, hd.close
  else
    f.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    f.title:SetPoint("TOPLEFT", f, "TOPLEFT", 14, -12)
    f.close = newButton(f, "x", 22, function() f:Hide() end)
    if f.close then f.close:SetPoint("TOPRIGHT", f, "TOPRIGHT", -6, -6) end
    local list = rawget(_G, "UISpecialFrames")
    if type(list) == "table" and name then list[#list + 1] = name end
  end
  f:Hide()
  return f
end

local function label(parent, font, w)
  local fs = parent:CreateFontString(nil, "OVERLAY", font or "GameFontHighlight")
  fs:SetJustifyH("LEFT")
  if w then fs:SetWidth(w) end
  return fs
end

-- ---------------------------------------------------------------- assistant
local wiz
local WIZ_STEPS = 3

local function finishWizard()
  if TreeAI.db then TreeAI.db.onboarded = TreeAI.version or true end
  if wiz then wiz:Hide() end   -- (4.0 QA) la fenêtre qui se ferme suffit : rien dans le chat
end

local function quickToggle(modName, fallbackSlash)
  local M = TreeAI.GetModule and TreeAI:GetModule(modName)
  if type(M) == "table" then
    for _, fn in ipairs({ "TogglePanel", "Toggle", "ToggleShown", "ToggleFrame", "Show" }) do
      if type(M[fn]) == "function" then TreeAI.safecall(M[fn], M); return true end
    end
  end
  if fallbackSlash then TreeAI:HandleSlash(fallbackSlash) end
  return false
end

local function buildWizard()
  wiz = dialog("TreeAIWizard", 400, 250)
  local W = wiz
  W.body = label(W, "GameFontHighlight", 370)
  W.body:SetPoint("TOPLEFT", W, "TOPLEFT", 16, -42)
  W.note = label(W, "GameFontDisableSmall", 370)
  W.note:SetPoint("BOTTOMLEFT", W, "BOTTOMLEFT", 16, 40)
  W.groups = { {}, {}, {} }
  local function add(step, b, x, y)
    if not b then return end
    b:SetPoint("TOPLEFT", W, "TOPLEFT", x, y)
    local g = W.groups[step]
    g[#g + 1] = b
    return b
  end
  -- étape 2 : préréglage + position du panneau
  for i, p in ipairs(TreeAI.PRESET_ORDER or {}) do
    add(2, newButton(W, "", 88, function()
      TreeAI:ApplyPreset(p)
      W.refresh()
    end, function()
      local on = (TreeAI.settings and TreeAI.settings.preset) == p
      return (on and "|cff33ff66> " or "") .. L[TreeAI.PRESET_LABEL[p]] .. (on and " <|r" or "")
    end), 16 + (i - 1) * 92, -82)
  end
  W.presetHelp = label(W, "GameFontDisableSmall", 370)
  W.presetHelp:SetPoint("TOPLEFT", W, "TOPLEFT", 16, -110)
  local spots = { { "AUTO", "Auto (scan)" }, { "TOPRIGHT", "Haut droite" }, { "RIGHT", "Droite" },
                  { "BOTTOMRIGHT", "Bas droite" }, { "BOTTOMLEFT", "Bas gauche" }, { "CENTER", "Centre" } }
  for i, sp in ipairs(spots) do
    add(2, newButton(W, L[sp[2]], 118, function()
      if sp[1] == "AUTO" then
        if TreeAI.AutoLayout then TreeAI:AutoLayout(true) end
      else
        TreeAI:SetPanelPosition(sp[1])
      end
      -- (4.0 QA) la carte « Prochaine étape » suit la place choisie (celle du panneau) ; le panneau texte, seulement
      -- si la carte ne le remplace pas
      local Wn = TreeAI.Window
      if not (Wn and Wn.ShowHUD and Wn.ShowHUD(true)) then
        TreeAI:ShowPanel()
        if TreeAI.SetMini then TreeAI:SetMini(false) end
      end
    end), 16 + ((i - 1) % 3) * 124, -150 - math.floor((i - 1) / 3) * 26)
  end
  -- étape 3 : boutons rapides
  local quick = {
    { "Macros", function() if TreeAI:InCombat() then return end quickToggle("Macros", "macros") end },
    { "Touches", function() quickToggle("Binds", "macros") end },
    { "Boussole", function() quickToggle("Compass", "boussole") end },
    { "Aide", function() if TreeAI.ShowHelp then TreeAI:ShowHelp() end end },
  }
  for i, q in ipairs(quick) do
    add(3, newButton(W, L[q[1]], 88, q[2]), 16 + (i - 1) * 92, -128)
  end
  -- pied : Passer · Retour · Suivant / Terminer
  W.skip = newButton(W, L["Passer"], 80, finishWizard)
  W.skip:SetPoint("BOTTOMLEFT", W, "BOTTOMLEFT", 12, 10)
  W.prev = newButton(W, L["Retour"], 80, function() TreeAI:ShowWizard(math.max(1, W.step - 1)) end)
  W.prev:SetPoint("BOTTOMRIGHT", W, "BOTTOMRIGHT", -100, 10)
  W.next = newButton(W, L["Suivant"], 84, function()
    if W.step >= WIZ_STEPS then finishWizard() else TreeAI:ShowWizard(W.step + 1) end
  end)
  W.next:SetPoint("BOTTOMRIGHT", W, "BOTTOMRIGHT", -12, 10)

  local TEXT = {
    L["TreeAI te conseille pendant que tu joues : prochaine quête, familier, munitions, sacs, groupe, HV."]
      .. "\n\n" .. L["Ta prochaine étape s'affiche sur une petite carte ; tout le reste est dans la fenêtre TreeAI (clic sur la pastille de la minimap)."]
      .. "\n" .. L["Aucun automatisme sans ton accord : c'est toi qui joues."],
    L["Combien de conseils veux-tu voir ?"] .. "\n\n\n\n\n" .. L["Où mettre la carte « Prochaine étape » ? (ou glisse-la)"],
    L["Raccourcis utiles :"] .. "\n" .. L["Macros : les macros prêtes pour ta classe (glisse-les dans ta barre)."]
      .. "\n" .. L["Touches : quoi lier où.  Boussole : flèche vers ton objectif."],
  }
  function W.refresh()
    local st = W.step or 1
    W.title:SetText(("%s  |cff999999%d/%d|r"):format(L["Bienvenue dans TreeAI"], st, WIZ_STEPS))
    W.body:SetText(TEXT[st])
    for i, g in ipairs(W.groups) do
      for _, b in ipairs(g) do
        if i == st then b:Show(); if b.refresh then b.refresh() end else b:Hide() end
      end
    end
    local p = TreeAI.settings and TreeAI.settings.preset or "normal"
    W.presetHelp:SetText(st == 2 and L[(TreeAI.PRESET_HELP or {})[p] or ""] or "")
    W.note:SetText(st == 3 and L["Toutes les commandes : /tai aide · revoir cet assistant : /tai bienvenue"] or "")
    if st > 1 then W.prev:Show() else W.prev:Hide() end
    W.next:SetText(st >= WIZ_STEPS and L["Terminer"] or L["Suivant"])
  end
  return wiz
end

--- Ouvre l'assistant à l'étape `step` (1..3). Hors combat seulement.
function TreeAI:ShowWizard(step)
  if not _G.CreateFrame then return false end
  if self:InCombat() then if self.CombatRefused then self:CombatRefused() else self:Print(L["Impossible en combat."]) end return false end
  if not wiz then buildWizard() end
  wiz.step = math.min(WIZ_STEPS, math.max(1, tonumber(step) or 1))
  wiz.refresh()
  wiz:Show()
  return true
end
function TreeAI:GetWizard() return wiz end

-- ---------------------------------------------------------------- aide (/tai aide)
local help
local HELP_ROWS = 16

local function buildHelp()
  help = dialog("TreeAIHelp", 520, 372)
  local H = help
  H.title:SetText(L["TreeAI : tout"] .. "  |cff999999" .. L["clic = lancer"] .. "|r")
  H.groupButtons, H.rows = {}, {}
  for i = 1, HELP_ROWS do
    -- chaque ligne est un bouton : même fonction que la commande /tai (l'interface d'abord)
    local b = _G.CreateFrame("Button", nil, H)
    b:SetSize(360, 17)
    b:SetPoint("TOPLEFT", H, "TOPLEFT", 150, -44 - (i - 1) * 18)
    b:RegisterForClicks("LeftButtonUp")
    local fs = label(b, "GameFontHighlightSmall", 360)
    fs:SetPoint("LEFT", b, "LEFT", 0, 0)
    if fs.SetWordWrap then fs:SetWordWrap(false) end
    b.fs = fs
    local hl = b:CreateTexture(nil, "HIGHLIGHT")
    hl:SetAllPoints(b)
    hl:SetTexture("Interface\\Buttons\\WHITE8X8")
    if hl.SetVertexColor then hl:SetVertexColor(1, 1, 1, 0.08) end
    b:SetScript("OnClick", function(self)
      if self.cmd then TreeAI:UserAction(TreeAI.HandleSlash, TreeAI, self.cmd) end
    end)
    b.SetText = function(self, t) self.fs:SetText(t) end
    b.GetText = function(self) return self.fs:GetText() end
    H.rows[i] = b
  end
  H.foot = label(H, "GameFontDisableSmall", 490)
  H.foot:SetPoint("BOTTOMLEFT", H, "BOTTOMLEFT", 14, 12)
  H.foot:SetText(L["Rubrique à gauche, clic sur une ligne = lancer · les commandes /tai marchent aussi"])
  return help
end

local function fillHelp(groups, idx)
  local H = help
  for i = 1, #groups do
    local b = H.groupButtons[i]
    if not b then
      b = newButton(H, "", 126, function(self) H.sel = self.idx; fillHelp(TreeAI:GetSlashHelp(), self.idx) end)
      b:SetPoint("TOPLEFT", H, "TOPLEFT", 12, -42 - (i - 1) * 24)
      H.groupButtons[i] = b
    end
    b.idx = i
    b:SetText((i == idx and "|cff33ff66" or "") .. groups[i].title .. (i == idx and "|r" or ""))
    b:Show()
  end
  for i = #groups + 1, #H.groupButtons do H.groupButtons[i]:Hide() end
  local g = groups[idx] or { items = {} }
  for i = 1, HELP_ROWS do
    local it = g.items[i]
    H.rows[i].cmd = it and it.cmd or nil
    H.rows[i]:SetText(it and ("|cffffff00" .. it.help .. "|r  |cff888888/tai " .. it.cmd .. "|r") or "")
    if it then H.rows[i]:Show() else H.rows[i]:Hide() end
  end
end

--- Page d'aide : toutes les commandes, groupées par rubrique. Renvoie false si impossible (→ chat).
function TreeAI:ShowHelp(groupIdx)
  if not _G.CreateFrame or self:InCombat() then return false end
  if not help then buildHelp() end
  local groups = self:GetSlashHelp()
  help.sel = tonumber(groupIdx) or help.sel or 1
  if help.sel > #groups then help.sel = 1 end
  fillHelp(groups, help.sel)
  help:Show()
  return true
end
function TreeAI:GetHelpFrame() return help end
