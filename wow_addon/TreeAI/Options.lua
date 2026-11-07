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
  local S = TreeAI.settings
  local P = layout(content)
  P.text("TreeAI Coach  |cff999999v" .. tostring(TreeAI.version) .. "|r", "GameFontNormalLarge", 24)
  P.text(L["Conseils uniquement: l'addon n'agit jamais à ta place."], nil, 16)
  P.text("|cff999999" .. L["Chaque module a sa page (liste à gauche, sous « TreeAI Coach »)."] .. "|r", nil, 16)

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
    function(v) S.panel.nowMax = v; TreeAI:RefreshPanel() end)
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
  P.check(L["Activer TreeAI"], function() return S.enabled end, function(v) S.enabled = v end)
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
      if o.type == "button" and type(o.onClick) == "function" then   -- action du module (clic = action du joueur)
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
