-- TreeAI - options panel (modern Settings API if present, else InterfaceOptions).
local TreeAI = TreeAI
if not TreeAI then return end
local _G = _G
local L = TreeAI.L
local pcall, ipairs, type, tostring, floor = pcall, ipairs, type, tostring, math.floor

local category, canvas

local function newCheck(parent, label, getter, setter)
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
  cb.refresh()
  return cb
end

local function newSlider(parent, label, min, max, step, getter, setter)
  local ok, s = pcall(_G.CreateFrame, "Slider", nil, parent, "OptionsSliderTemplate")
  if not ok or not s then return nil end
  s:SetWidth(200)
  s:SetMinMaxValues(min, max)
  s:SetValueStep(step)
  if s.SetObeyStepOnDrag then s:SetObeyStepOnDrag(true) end
  local text = s.Text or s.text
  local lo, hi = s.Low, s.High
  if lo and lo.SetText then lo:SetText(tostring(min)) end
  if hi and hi.SetText then hi:SetText(tostring(max)) end
  local function fmt(v)
    if text and text.SetText then text:SetText(label .. ": " .. tostring(v)) end
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

local function header(parent, text, y)
  local fs = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  fs:SetPoint("TOPLEFT", parent, "TOPLEFT", 12, y)
  fs:SetText(text)
  return fs
end

local widgets = {}

local function build(content)
  local y = -10
  local title = content:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  title:SetPoint("TOPLEFT", content, "TOPLEFT", 12, y)
  title:SetText("TreeAI Coach  |cff999999v" .. TreeAI.version .. "|r")
  y = y - 22
  local sub = content:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  sub:SetPoint("TOPLEFT", content, "TOPLEFT", 12, y)
  sub:SetText(L["Conseils uniquement: l'addon n'agit jamais à ta place."])
  y = y - 24

  local S = TreeAI.settings
  local function add(w, h)
    if not w then return end
    w:SetPoint("TOPLEFT", content, "TOPLEFT", 14, y)
    widgets[#widgets + 1] = w
    y = y - (h or 26)
  end

  header(content, L["Général"], y); y = y - 20
  add(newCheck(content, L["Activer TreeAI"], function() return S.enabled end, function(v) S.enabled = v end))
  add(newCheck(content, L["Afficher le panneau"], function() return S.panel.shown end,
    function(v) if v then TreeAI:ShowPanel() else TreeAI:HidePanel() end end))
  add(newCheck(content, L["Verrouiller le panneau"], function() return S.panel.locked end, function(v) S.panel.locked = v end))
  add(newCheck(content, L["Son pour les alertes urgentes"], function() return S.sound end, function(v) S.sound = v end))
  add(newCheck(content, L["Écrire les conseils dans le chat"], function() return S.chat end, function(v) S.chat = v end))
  add(newCheck(content, L["Synchro avec le groupe (messages d'addon)"], function() return S.comms end, function(v) S.comms = v end))
  y = y - 10
  add(newSlider(content, L["Priorité minimale"], 1, 3, 1, function() return S.minPrio end,
    function(v) S.minPrio = v; TreeAI:RefreshPanel() end), 40)
  add(newSlider(content, L["Lignes max"], 1, 12, 1, function() return S.panel.maxLines end,
    function(v) S.panel.maxLines = v; TreeAI:RefreshPanel() end), 40)
  add(newSlider(content, L["Échelle"], 0.6, 1.6, 0.05, function() return S.panel.scale end,
    function(v) S.panel.scale = v; TreeAI:ApplyPanelSettings() end), 40)
  add(newSlider(content, L["Opacité"], 0.2, 1, 0.05, function() return S.panel.alpha end,
    function(v) S.panel.alpha = v; TreeAI:ApplyPanelSettings() end), 40)

  for name, mod in TreeAI:IterModules() do
    if mod.db then
      y = y - 6
      header(content, mod.title or name, y); y = y - 20
      add(newCheck(content, L["Activé"], function() return mod.db.enabled end,
        function(v) TreeAI:SetModuleEnabled(name, v) end))
      if type(mod.options) == "table" then
        for _, o in ipairs(mod.options) do
          local key = o.key
          if o.type == "range" then
            add(newSlider(content, o.label or key, o.min or 0, o.max or 100, o.step or 1,
              function() return mod.db[key] end,
              function(v) mod.db[key] = v; if mod.OnOptionChanged then TreeAI.safecall(mod.OnOptionChanged, mod, key, v) end end), 40)
          else
            add(newCheck(content, o.label or key, function() return mod.db[key] end,
              function(v) mod.db[key] = v; if mod.OnOptionChanged then TreeAI.safecall(mod.OnOptionChanged, mod, key, v) end end))
          end
        end
      end
    end
  end
  content:SetHeight(-y + 20)
end

local function refreshAll()
  for _, w in ipairs(widgets) do if w.refresh then pcall(w.refresh) end end
end

function TreeAI:InitOptions()
  if canvas or not _G.CreateFrame then return end
  canvas = _G.CreateFrame("Frame", "TreeAIOptionsPanel")
  canvas.name = "TreeAI Coach"
  local built = false
  local ok, scroll = pcall(_G.CreateFrame, "ScrollFrame", nil, canvas, "UIPanelScrollFrameTemplate")
  local content
  if ok and scroll then
    scroll:SetPoint("TOPLEFT", canvas, "TOPLEFT", 0, -4)
    scroll:SetPoint("BOTTOMRIGHT", canvas, "BOTTOMRIGHT", -28, 4)
    content = _G.CreateFrame("Frame", nil, scroll)
    content:SetSize(560, 800)
    scroll:SetScrollChild(content)
  else
    content = canvas
  end
  -- built lazily so modules registered late are included
  canvas:SetScript("OnShow", function()
    if not built then built = true; TreeAI.safecall(build, content) end
    refreshAll()
  end)

  local Settings = _G.Settings
  if Settings and Settings.RegisterCanvasLayoutCategory and Settings.RegisterAddOnCategory then
    local okc, cat = pcall(Settings.RegisterCanvasLayoutCategory, canvas, canvas.name)
    if okc and cat then
      category = cat
      pcall(Settings.RegisterAddOnCategory, cat)
      return
    end
  end
  if _G.InterfaceOptions_AddCategory then pcall(_G.InterfaceOptions_AddCategory, canvas) end
end

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
