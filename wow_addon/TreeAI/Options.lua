-- TreeAI - options: a main page (Aides PvP, panneau, encadré d'alerte, messages) and one page per module.
-- Modern Settings API if present (canvas category + subcategories), else InterfaceOptions (.parent).
-- Plain frames only: nothing protected, no Blizzard frame is modified.
local TreeAI = TreeAI
if not TreeAI then return end
local _G = _G
local L = TreeAI.L
local pcall, ipairs, pairs, type, tostring, floor = pcall, ipairs, pairs, type, tostring, math.floor

local category, canvas
local pages = {}       -- { frame, category }
local widgets = {}

local function newCheck(parent, label, getter, setter, tip)
  local ok, cb = pcall(_G.CreateFrame, "CheckButton", nil, parent, "UICheckButtonTemplate")
  if not ok or not cb then return nil end
  cb:SetSize(24, 24)
  local fs = cb.text or cb.Text
  if not fs then
    fs = cb:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    fs:SetPoint("LEFT", cb, "RIGHT", 2, 0)
  end
  fs:SetText(label)
  cb.refresh = function() cb:SetChecked(getter() and true or false) end
  cb:SetScript("OnClick", function(self) setter(self:GetChecked() and true or false) end)
  if tip then
    cb:SetScript("OnEnter", function(self)
      local tt = _G.GameTooltip
      if not tt then return end
      tt:SetOwner(self, "ANCHOR_RIGHT")
      tt:AddLine(label, 1, 1, 1)
      tt:AddLine(tip, 0.8, 0.8, 0.8, true)
      tt:Show()
    end)
    cb:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
  end
  cb.refresh()
  return cb
end

local function newSlider(parent, label, min, max, step, getter, setter)
  local ok, s = pcall(_G.CreateFrame, "Slider", nil, parent, "OptionsSliderTemplate")
  if not ok or not s then return nil end
  s:SetWidth(220)
  s:SetMinMaxValues(min, max)
  s:SetValueStep(step)
  if s.SetObeyStepOnDrag then s:SetObeyStepOnDrag(true) end
  local text = s.Text or s.text
  local lo, hi = s.Low, s.High
  if lo and lo.SetText then lo:SetText(tostring(min)) end
  if hi and hi.SetText then hi:SetText(tostring(max)) end
  local function fmt(v)
    if text and text.SetText then text:SetText(label .. " : " .. tostring(v)) end
  end
  s.refresh = function()
    local v = getter() or min
    s:SetValue(v)
    fmt(v)
  end
  s:SetScript("OnValueChanged", function(_, v)
    v = floor(v / step + 0.5) * step
    if step < 1 then v = floor(v * 100 + 0.5) / 100 end
    fmt(v)
    setter(v)
  end)
  s.refresh()
  return s
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

-- layout helper: a page is a vertical list
local function layout(content)
  local P = { y = -10 }
  function P.text(str, font, h)
    local fs = content:CreateFontString(nil, "OVERLAY", font or "GameFontHighlightSmall")
    fs:SetPoint("TOPLEFT", content, "TOPLEFT", 14, P.y)
    fs:SetWidth(540)
    fs:SetJustifyH("LEFT")
    fs:SetText(str)
    P.y = P.y - (h or 18)
    return fs
  end
  function P.header(str)
    P.y = P.y - 8
    P.text("|cff33cc66" .. str .. "|r", "GameFontNormal", 22)
  end
  function P.add(w, h)
    if not w then return end
    w:SetPoint("TOPLEFT", content, "TOPLEFT", 16, P.y)
    widgets[#widgets + 1] = w
    P.y = P.y - (h or 26)
    return w
  end
  function P.slider(...) return P.add(newSlider(content, ...), 42) end
  function P.check(...) return P.add(newCheck(content, ...)) end
  function P.button(...) return P.add(newButton(content, ...), 28) end
  function P.finish() content:SetHeight(-P.y + 20) end
  return P
end

local function refreshAll()
  for _, w in ipairs(widgets) do if w.refresh then pcall(w.refresh) end end
end

local function newPage(name, parentName, build)
  local f = _G.CreateFrame("Frame", nil)
  f.name = name
  if parentName then f.parent = parentName end
  local content
  local ok, scroll = pcall(_G.CreateFrame, "ScrollFrame", nil, f, "UIPanelScrollFrameTemplate")
  if ok and scroll then
    scroll:SetPoint("TOPLEFT", f, "TOPLEFT", 0, -4)
    scroll:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -28, 4)
    content = _G.CreateFrame("Frame", nil, scroll)
    content:SetSize(580, 800)
    scroll:SetScrollChild(content)
  else
    content = f
  end
  local built = false
  f:SetScript("OnShow", function()
    if not built then built = true; TreeAI.safecall(build, content) end
    refreshAll()
  end)
  f.build = function() if not built then built = true; TreeAI.safecall(build, content) end end
  return f
end

---------------------------------------------------------------------------
-- main page
---------------------------------------------------------------------------
local function buildMain(content)
  -- toujours le profil actif (compte ou perso), même après « /tai profil perso »
  local S = setmetatable({}, {
    __index = function(_, k) return TreeAI.settings and TreeAI.settings[k] end,
    __newindex = function(_, k, v) if TreeAI.settings then TreeAI.settings[k] = v end end,
  })
  local P = layout(content)
  P.text("TreeAI Coach  |cff999999v" .. tostring(TreeAI.version) .. "|r", "GameFontNormalLarge", 24)
  P.text(L["Conseils uniquement: l'addon n'agit jamais à ta place."], nil, 16)
  P.text("|cff999999" .. L["Chaque module a sa page (liste à gauche, sous « TreeAI Coach »)."] .. "|r", nil, 16)

  P.header(L["Démarrer"])
  local function presetText()
    local cur = S.preset or "normal"
    return L["Conseils"] .. " : " .. L[(TreeAI.PRESET_LABEL or {})[cur] or "Perso"] .. "  (" .. L["clic : changer"] .. ")"
  end
  P.button("", 300, function()
    local order, cur = TreeAI.PRESET_ORDER or { "normal" }, S.preset
    local nxt = order[1]
    for i, p in ipairs(order) do if p == cur then nxt = order[i % #order + 1] end end
    TreeAI:ApplyPreset(nxt)
    refreshAll()
  end, presetText)
  P.text("|cff999999" .. L["Calme : 3 lignes, l'utile seulement · Normal : 5 lignes · Tout : 8 lignes, même les petits conseils."] .. "|r", nil, 16)
  P.button("", 300, function()
    TreeAI:SetProfile(TreeAI:ProfileMode() == "char" and "account" or "char")
    refreshAll()
  end, function()
    return L["Réglages"] .. " : " .. (TreeAI:ProfileMode() == "char" and L["propres à ce personnage"] or L["communs au compte"])
  end)
  P.button(L["Assistant de démarrage"], 200, function() if TreeAI.ShowWizard then TreeAI:ShowWizard(1) end end)
  P.button(L["Tout : toutes les actions (/tai aide)"], 260, function() if TreeAI.ShowHelp then TreeAI:ShowHelp() end end)
  P.button(L["Déplacer les cadres (mode édition)"], 260, function() if TreeAI.SetEditMode then TreeAI:SetEditMode(true) end end)
  P.button(L["Placer automatiquement"], 260, function() if TreeAI.AutoLayout then TreeAI:AutoLayout(true) end end)

  P.header(L["Aides PvP"])
  P.check(L["Aides PvP (contre les joueurs, champs de bataille)"], function() return S.pvp end,
    function(v) S.pvp = v end, L["Désactivé : l'addon aide à quêter et se tait face aux joueurs ennemis."])

  P.header(L["Panneau"])
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
  P.slider(L["Largeur"], 220, 420, 10, function() return S.panel.width end,
    function(v) S.panel.width = v; TreeAI:ApplyPanelSettings() end)
  P.slider(L["Échelle"], 0.6, 1.6, 0.05, function() return S.panel.scale end,
    function(v) S.panel.scale = v; TreeAI:ApplyPanelSettings() end)
  P.slider(L["Opacité"], 0.2, 1, 0.05, function() return S.panel.alpha end,
    function(v) S.panel.alpha = v; TreeAI:ApplyPanelSettings() end)
  P.button(L["Remettre le panneau au centre"], 200, function() TreeAI:ResetPanel() end)

  P.header(L["Encadré d'alerte (important seulement)"])
  P.check(L["Afficher les alertes importantes dans un coin"], function() return S.alert.enabled end,
    function(v) S.alert.enabled = v end)
  local CORNER_FR = { TOPRIGHT = L["en haut à droite"], TOPLEFT = L["en haut à gauche"],
    BOTTOMLEFT = L["en bas à gauche"], BOTTOMRIGHT = L["en bas à droite"] }
  P.button("", 240, function()
    local list = TreeAI.ALERT_CORNERS or { "TOPRIGHT" }
    local nxt = list[1]
    for i, c in ipairs(list) do if c == S.alert.corner then nxt = list[i % #list + 1] end end
    S.alert.corner = nxt
    if TreeAI.ApplyAlertSettings then TreeAI:ApplyAlertSettings() end
  end, function() return L["Coin"] .. " : " .. (CORNER_FR[S.alert.corner] or S.alert.corner or "?") end)
  P.slider(L["Durée d'affichage (s)"], 3, 30, 1, function() return S.alert.duration end,
    function(v) S.alert.duration = v end)
  P.slider(L["Échelle de l'encadré"], 0.6, 1.6, 0.05, function() return S.alert.scale end,
    function(v) S.alert.scale = v; if TreeAI.ApplyAlertSettings then TreeAI:ApplyAlertSettings() end end)
  P.slider(L["Opacité de l'encadré"], 0.3, 1, 0.05, function() return S.alert.alpha end,
    function(v) S.alert.alpha = v end)
  P.button(L["Tester l'encadré"], 160, function() TreeAI:HandleSlash("alerte") end)

  P.header(L["Sons et messages"])
  P.check(L["Son pour les alertes importantes"], function() return S.sound end, function(v) S.sound = v end)
  P.check(L["Sons aussi pour certains conseils (rares…)"], function() return S.soundTips end,
    function(v) S.soundTips = v end, L["Par défaut, seul l'important (priorité 3) fait du bruit."])
  P.check(L["Écrire aussi les conseils dans le chat (déconseillé)"], function() return S.chat end,
    function(v) S.chat = v end, L["Par défaut, aucun conseil n'est écrit dans le chat : tout va dans le panneau."])
  P.slider(L["Priorité minimale"], 1, 3, 1, function() return S.minPrio end,
    function(v) S.minPrio = v; TreeAI:RefreshPanel() end)

  P.header(L["Général"])
  P.check(L["Activer TreeAI"], function() return S.enabled end, function(v) TreeAI:SetEnabled(v) end)
  P.check(L["Synchro avec le groupe (messages d'addon)"], function() return S.comms end, function(v) S.comms = v end)
  P.finish()
end

---------------------------------------------------------------------------
-- one page per module
---------------------------------------------------------------------------
local function buildModule(content, name, mod)
  local P = layout(content)
  P.text(mod.title or name, "GameFontNormalLarge", 24)
  if mod.description then P.text(tostring(mod.description), nil, 18) end
  if type(mod.classes) == "table" then
    local cls = {}
    for c in pairs(mod.classes) do cls[#cls + 1] = c end
    table.sort(cls)
    P.text("|cff999999" .. L["Classes"] .. " : " .. table.concat(cls, ", ") .. "|r", nil, 16)
  end
  P.check(L["Module activé"], function() return mod.db and mod.db.enabled end,
    function(v) TreeAI:SetModuleEnabled(name, v) end)
  local function changed(key, v)
    if mod.OnOptionChanged then TreeAI.safecall(mod.OnOptionChanged, mod, key, v) end
    TreeAI:RequestRefresh()
  end
  if type(mod.options) == "table" and #mod.options > 0 then
    P.header(L["Réglages"])
    for _, o in ipairs(mod.options) do
      local key = o.key
      if o.section then P.header(o.section) end
      if o.type == "choice" and type(o.values) == "table" and #o.values > 0 then
        P.button("", 240, function()
          if not mod.db then return end
          local n = (tonumber(mod.db[key]) or 1) % #o.values + 1
          mod.db[key] = n; changed(key, n)
        end, function()
          return (o.label or key) .. " : " .. tostring(o.values[tonumber(mod.db and mod.db[key]) or 1] or "?")
        end)
      elseif o.type == "button" and type(o.onClick) == "function" then   -- action du module (clic = action du joueur)
        P.button(o.label or key or "?", o.width or 220, function() TreeAI:UserAction(o.onClick, mod) end)
      elseif o.type == "range" then
        P.slider(o.label or key, o.min or 0, o.max or 100, o.step or 1,
          function() return mod.db and mod.db[key] end,
          function(v) if mod.db then mod.db[key] = v; changed(key, v) end end)
      elseif key then
        P.check(o.label or key, function() return mod.db and mod.db[key] end,
          function(v) if mod.db then mod.db[key] = v; changed(key, v) end end, o.tip)
      end
    end
  else
    P.text("|cff999999" .. L["Pas d'autre réglage pour ce module."] .. "|r", nil, 18)
  end
  P.finish()
end

function TreeAI:InitOptions()
  if canvas or not _G.CreateFrame then return end
  canvas = newPage("TreeAI Coach", nil, buildMain)
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

  for name, mod in self:IterModules() do
    local page = newPage(mod.title or name, canvas.name, function(content) buildModule(content, name, mod) end)
    local sub
    if modern and Settings.RegisterCanvasLayoutSubcategory then
      local oks, c = pcall(Settings.RegisterCanvasLayoutSubcategory, category, page, page.name)
      if oks then sub = c end
    elseif _G.InterfaceOptions_AddCategory then
      pcall(_G.InterfaceOptions_AddCategory, page)
    end
    pages[#pages + 1] = { frame = page, category = sub, module = name }
  end
end

function TreeAI:GetOptionPages() return canvas, pages end

function TreeAI:OpenOptions()
  if self:InCombat() then self:Print(L["Impossible en combat."]); return end
  if not canvas then self:InitOptions() end
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
  f.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  f.title:SetPoint("TOPLEFT", f, "TOPLEFT", 14, -12)
  f.close = newButton(f, "x", 22, function() f:Hide() end)
  if f.close then f.close:SetPoint("TOPRIGHT", f, "TOPRIGHT", -6, -6) end
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
  if wiz then wiz:Hide() end
  TreeAI:Print(L["Prêt ! /tai aide pour toutes les commandes, /tai bienvenue pour revoir l'assistant."])
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
    add(2, newButton(W, "", 118, function()
      TreeAI:ApplyPreset(p)
      W.refresh()
    end, function()
      local on = (TreeAI.settings and TreeAI.settings.preset) == p
      return (on and "|cff33ff66> " or "") .. L[TreeAI.PRESET_LABEL[p]] .. (on and " <|r" or "")
    end), 16 + (i - 1) * 124, -82)
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
      TreeAI:ShowPanel()
      if TreeAI.SetMini then TreeAI:SetMini(false) end
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
      .. "\n\n" .. L["Tout arrive dans un petit panneau ; seul l'important s'affiche dans un encadré en coin."]
      .. "\n" .. L["Rien n'est automatique : c'est toi qui joues."],
    L["Combien de conseils veux-tu voir ?"] .. "\n\n\n\n\n" .. L["Où mettre le panneau ? (tu peux aussi le glisser)"],
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
  if self:InCombat() then self:Print(L["Impossible en combat."]); return false end
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
