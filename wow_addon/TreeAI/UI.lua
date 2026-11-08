-- TreeAI - compact movable panel with tabs (Maintenant, Quêtes, Groupe, HV, Infos), toolbar,
-- one-line « mini » mode, and a small corner alert box for important (prio 3) items.
-- Taint-safe: plain frames parented to UIParent only, no secure template, never touches Blizzard frames.
local TreeAI = TreeAI
if not TreeAI then return end
local _G = _G
local L = TreeAI.L
local tostring, type, ipairs, pairs, floor, max, min = tostring, type, ipairs, pairs, math.floor, math.max, math.min

local PRIO_COLORS = {
  [1] = { 0.85, 0.85, 0.85 },
  [2] = { 1.00, 0.82, 0.20 },
  [3] = { 1.00, 0.30, 0.25 },
}
local LINE_H, HEADER_H, TABS_H, PAD = 15, 18, 16, 6
local TAB_ROW_H, TAB_ICON, ROW_GAP = 18, 12, 3
local MIN_W, DEFAULT_W = 300, 320
local MAX_ROWS = 15
-- interface SIMPLE : 4 onglets visibles (Maintenant · Quêtes · Groupe · Plus) ; « Plus » ouvre une 2e rangée
local PRIMARY = { "now", "quests", "group" }
local IS_PRIMARY = { now = true, quests = true, group = true }
local MORE = "__more"
local SHORT = { now = "Maint.", quests = "Quêtes", group = "Groupe", [MORE] = "Plus", predict = "Prévis.",
  gear = "Équip.", prof = "Métiers", talents = "Talents", dungeon = "Donjon", pvp = "JcJ", hv = "HV", info = "Infos",
  binds = "Touches" }
local TAB_ICONS = {
  now = "Interface\\Icons\\Spell_Nature_TimeStop", quests = "Interface\\Icons\\INV_Misc_Note_02",
  group = "Interface\\Icons\\Ability_Hunter_Pet_Wolf", [MORE] = "Interface\\Buttons\\UI-PlusButton-Up",
  predict = "Interface\\Icons\\INV_Misc_PocketWatch_01", hv = "Interface\\Icons\\INV_Misc_Coin_01",
  prof = "Interface\\Icons\\Trade_BlackSmithing", talents = "Interface\\Icons\\Ability_Marksmanship",
  gear = "Interface\\Icons\\INV_Chest_Chain", dungeon = "Interface\\Icons\\INV_Misc_Key_03",
  pvp = "Interface\\Icons\\Ability_DualWield", info = "Interface\\Icons\\INV_Misc_QuestionMark",
  binds = "Interface\\Icons\\INV_Misc_Key_14",
}
TreeAI.PANEL_MIN_WIDTH = MIN_W
local moreOpen = false
local function panelWidth()
  local s = TreeAI.settings and TreeAI.settings.panel
  return max(MIN_W, tonumber(s and s.width) or DEFAULT_W)
end
-- réglages d'apparence (page « Apparence ») : police, interligne, icônes
local function look()
  local s = TreeAI.settings and TreeAI.settings.panel
  local fs = tonumber(s and s.fontSize) or 11
  return fs, tonumber(s and s.lineGap) or ROW_GAP, tonumber(s and s.iconSize) or 13
end
local EMPTY = {
  now = "Rien à signaler.", quests = "Aucune étape de quête pour l'instant.",
  group = "Pas de groupe avec TreeAI.", hv = "Ouvre l'hôtel des ventes pour chercher des affaires.",
  info = "Rien à signaler.",
}
local ICONS = {
  compass = "Interface\\Icons\\INV_Misc_Spyglass_02",
  macros = "Interface\\Icons\\INV_Misc_Note_01",
  group = "Interface\\Icons\\Ability_Hunter_Pet_Wolf",
  options = "Interface\\Icons\\INV_Misc_Gear_01",
  locked = "Interface\\Buttons\\LockButton-Locked-Up",
  unlocked = "Interface\\Buttons\\LockButton-Unlocked-Up",
  mini = "Interface\\Buttons\\UI-MinusButton-Up",
  maxi = "Interface\\Buttons\\UI-PlusButton-Up",
  close = "Interface\\Buttons\\UI-StopButton",
  edit = "Interface\\Icons\\INV_Misc_Wrench_01",
  all = "Interface\\Icons\\INV_Misc_Book_09",
  reset = "Interface\\Buttons\\UI-RefreshButton",
  radar = "Interface\\Icons\\INV_Misc_Map_01",
}

-- icône par défaut d'une ligne sans icône : par sujet, sinon par module (repères visuels constants)
local TOPIC_ICONS = {
  ammo = "Interface\\Icons\\INV_Ammo_Arrow_02", bags = "Interface\\Icons\\INV_Misc_Bag_08",
  repair = "Interface\\Icons\\Trade_BlackSmithing", food = "Interface\\Icons\\INV_Misc_Food_15",
  train = "Interface\\Icons\\INV_Misc_Book_09", aspect = "Interface\\Icons\\Ability_Hunter_AspectOfTheMonkey",
  fd = "Interface\\Icons\\Ability_Rogue_FeignDeath", petfood = "Interface\\Icons\\Ability_Hunter_BeastTraining",
  pet = "Interface\\Icons\\Ability_Hunter_BeastCall", hub = "Interface\\Icons\\INV_Misc_Map_01",
  interrupt = "Interface\\Icons\\Ability_Kick", cc = "Interface\\Icons\\Spell_Frost_ChainsOfIce",
}
local MODULE_ICONS = {
  Quests = "Interface\\Icons\\INV_Misc_Note_02", Tracker = "Interface\\Icons\\INV_Misc_Note_02",
  Leveling = "Interface\\Icons\\INV_Misc_Book_09", Compass = "Interface\\Icons\\INV_Misc_Spyglass_02",
  Predict = "Interface\\Icons\\INV_Misc_PocketWatch_01", Squad = "Interface\\Icons\\Ability_Hunter_Pet_Wolf",
  Group = "Interface\\Icons\\Ability_Hunter_Pet_Wolf", Missions = "Interface\\Icons\\INV_Misc_Note_06",
  Auction = "Interface\\Icons\\INV_Misc_Coin_01", Economy = "Interface\\Icons\\INV_Misc_Coin_02",
  Hunter = "Interface\\Icons\\Ability_Hunter_BeastCall", HunterPro = "Interface\\Icons\\Ability_Hunter_BeastCall",
  Fight = "Interface\\Icons\\Ability_DualWield", Boost = "Interface\\Icons\\Spell_Holy_BorrowedTime",
  Inventory = "Interface\\Icons\\INV_Misc_Bag_08", Macros = "Interface\\Icons\\INV_Misc_Note_01",
  Classes = "Interface\\Icons\\Spell_Holy_WordFortitude", Comfort = "Interface\\Icons\\INV_Misc_Gear_01",
  Pro = "Interface\\Icons\\INV_Misc_Note_02",
}
local function iconOf(e)
  if e.icon then return e.icon end
  local topic = TreeAI.TopicOf and TreeAI:TopicOf(e)
  if topic and TOPIC_ICONS[topic] then return TOPIC_ICONS[topic] end
  local m = e.module or e.source
  return m and MODULE_ICONS[m] or nil
end
TreeAI._IconOf = iconOf

local panel, rows, tabButtons, tools = nil, {}, {}, {}
local scroll = {}          -- tabId -> first visible index
local current = {}         -- entries of the visible tab
local alert

local function acc() return TreeAI.settings and TreeAI.settings.panel end
local function char() return TreeAI.PanelChar and TreeAI:PanelChar() or acc() end
local function activeTab()
  local c = char()
  local id = c and c.tab or "now"
  if not TreeAI.tabs[id] then id = "now" end
  return id
end
local function isMini() local c = char(); return c and c.mini and true or false end

local function savePosition()
  local c = char()
  if not (panel and c) then return end
  local point, _, _, x, y = panel:GetPoint(1)
  c.point, c.x, c.y = point or "CENTER", x or 0, y or 0
end

local function applyPosition()
  local s, c = acc(), char()
  if not (panel and s and c) then return end
  panel:ClearAllPoints()
  panel:SetPoint(c.point or "CENTER", _G.UIParent, c.point or "CENTER", c.x or 0, c.y or 0)
  panel:SetScale(s.scale or 1)
  panel:SetAlpha(s.alpha or 0.9)
  panel:SetWidth(panelWidth())
end

local function startMove()
  local s = acc()
  if not panel or (s and s.locked) or TreeAI:InCombat() then return end
  panel:StartMoving()
end
local function stopMove()
  if not panel then return end
  panel:StopMovingOrSizing()
  savePosition()
  local c = char()
  if c then c.manual = true end   -- déplacé à la main : le placement auto ne le bouge plus
  local st = TreeAI.db and TreeAI:CharKey() ~= "?" and TreeAI:CharDB().layout
  if type(st) == "table" and type(st.manual) == "table" then st.manual.panel = true end
end
local function draggable(f)
  f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", startMove)
  f:SetScript("OnDragStop", stopMove)
end

local function showTooltip(owner, title, lines)
  local tt = _G.GameTooltip
  if not tt then return end
  tt:SetOwner(owner, "ANCHOR_LEFT")
  tt:AddLine(title, 1, 1, 1, true)
  for _, l in ipairs(lines or {}) do
    if l == " " then tt:AddLine(" ") else tt:AddLine(l[1], l[2] or 0.8, l[3] or 0.8, l[4] or 0.8, true) end
  end
  tt:Show()
end
local function hideTooltip() if _G.GameTooltip then _G.GameTooltip:Hide() end end

local function isSuggestion(e) return e and e.source == nil and e.time ~= nil end

local function entryTooltip(owner, e)
  local extra = {}
  local t = e.tooltip
  if type(t) == "string" then extra[#extra + 1] = { t, 0.9, 0.9, 0.9 }
  elseif type(t) == "table" then
    for _, l in ipairs(t) do extra[#extra + 1] = { tostring(l), 0.9, 0.9, 0.9 } end
  end
  if e.macro then
    extra[#extra + 1] = " "
    extra[#extra + 1] = { L["Macro proposée"] .. " :", 0.6, 0.8, 1 }
    extra[#extra + 1] = { e.macro, 0.9, 0.9, 0.9 }
  end
  if type(e._also) == "table" then
    local names = {}
    for m in pairs(e._also) do
      local mod = TreeAI.modules and TreeAI.modules[m]
      names[#names + 1] = L[(mod and mod.title) or m]
    end
    table.sort(names)
    if #names > 0 then extra[#extra + 1] = { L["Aussi signalé par"] .. " : " .. table.concat(names, ", "), 0.6, 0.6, 0.6 } end
  end
  if e.onClick then extra[#extra + 1] = { L["Clic : action"], 0.4, 0.8, 1 } end
  if isSuggestion(e) then extra[#extra + 1] = { L["Clic droit: masquer"], 0.5, 0.5, 0.5 } end
  showTooltip(owner, e.text, extra)
end

local function runEntry(e, button)
  if not e then return end
  if button == "RightButton" then
    if isSuggestion(e) then if TreeAI.DismissSuggestion then TreeAI:DismissSuggestion(e.id) else TreeAI:ClearSuggestion(e.id) end end
  elseif e.onClick then
    TreeAI:UserAction(e.onClick, e)
  end
end

local function makeRow(i)
  local b = _G.CreateFrame("Button", nil, panel)
  b:SetHeight(LINE_H)
  b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  draggable(b)
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(13, 13)
  b.icon:SetPoint("TOPLEFT", b, "TOPLEFT", 0, -1)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("TOPLEFT", b, "TOPLEFT", 16, 0)
  b.text:SetPoint("TOPRIGHT", b, "TOPRIGHT", 0, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetJustifyV then b.text:SetJustifyV("TOP") end
  -- 2 lignes au lieu d'une ligne coupée « … » (texte complet dans l'infobulle)
  if b.text.SetWordWrap then b.text:SetWordWrap(true) end
  if b.text.SetNonSpaceWrap then b.text:SetNonSpaceWrap(false) end
  if b.text.SetMaxLines then b.text:SetMaxLines(2) end
  b.hl = b:CreateTexture(nil, "HIGHLIGHT")
  b.hl:SetAllPoints(b)
  b.hl:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.hl.SetVertexColor then b.hl:SetVertexColor(1, 1, 1, 0.08) end
  b:SetScript("OnClick", function(self, button) runEntry(self.entry, button) end)
  b:SetScript("OnEnter", function(self) if self.entry then entryTooltip(self, self.entry) end end)
  b:SetScript("OnLeave", hideTooltip)
  b.index = i
  return b
end

local function toolButton(key, tip, fn)
  local b = _G.CreateFrame("Button", nil, panel)
  b:SetSize(14, 14)
  b.tex = b:CreateTexture(nil, "ARTWORK")
  b.tex:SetAllPoints(b)
  b.tex:SetTexture(ICONS[key])
  b:SetScript("OnClick", function(self, button) TreeAI.safecall(fn, self, button) end)
  b:SetScript("OnEnter", function(self) showTooltip(self, type(tip) == "function" and tip() or tip) end)
  b:SetScript("OnLeave", hideTooltip)
  tools[#tools + 1] = b
  tools[key] = b
  return b
end

local function toggleCompass()
  local C = TreeAI.Compass or (TreeAI.GetModule and TreeAI:GetModule("Compass"))
  if type(C) == "table" then
    for _, fn in ipairs({ "Toggle", "ToggleShown", "ToggleFrame" }) do
      if type(C[fn]) == "function" then TreeAI.safecall(C[fn], C); return end
    end
  end
  TreeAI:HandleSlash("boussole")
end

local function toggleMacros()
  if TreeAI:InCombat() then TreeAI:Print(L["Impossible en combat."]); return end
  local M = TreeAI.GetModule and TreeAI:GetModule("Macros")
  if M and type(M.TogglePanel) == "function" then TreeAI.safecall(M.TogglePanel, M) end
end

local function tabTitle(id)
  if id == MORE then return L["Plus"] end
  local t = TreeAI.tabs[id]
  return L[(t and t.title) or id]
end

local function newTabButton(id, icon)
  local b = _G.CreateFrame("Button", nil, panel)
  b:SetHeight(TAB_ROW_H)
  draggable(b)
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(TAB_ICON, TAB_ICON)
  b.icon:SetPoint("LEFT", b, "LEFT", 2, 0)
  b.icon:SetTexture(icon or TAB_ICONS[id] or TAB_ICONS.info)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  b.text:SetPoint("LEFT", b.icon, "RIGHT", 2, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b.line = b:CreateTexture(nil, "ARTWORK")
  b.line:SetHeight(2)
  b.line:SetPoint("BOTTOMLEFT", b, "BOTTOMLEFT", 2, 0)
  b.line:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -2, 0)
  b.line:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.line.SetVertexColor then b.line:SetVertexColor(0.2, 0.8, 0.4, 0.9) end
  b.bg = b:CreateTexture(nil, "BACKGROUND")
  b.bg:SetAllPoints(b)
  b.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.bg.SetVertexColor then b.bg:SetVertexColor(1, 1, 1, 0.05) end
  b:SetScript("OnLeave", hideTooltip)
  return b
end

local function tabButton(id)
  local b = tabButtons[id]
  if b then return b end
  b = newTabButton(id)
  b.id = id
  if id == MORE then
    b:SetScript("OnClick", function() TreeAI:ToggleMoreMenu() end)
    b:SetScript("OnEnter", function(self)
      showTooltip(self, L["Plus"], { { L["Prévisions, HV, Métiers, Talents, Équipement, Donjon, JcJ, Infos…"], 0.8, 0.8, 0.8 } })
    end)
  else
    b:SetScript("OnClick", function(self) TreeAI:SelectTab(self.id) end)
    b:SetScript("OnEnter", function(self)
      showTooltip(self, tabTitle(self.id), { { L["Molette : faire défiler"], 0.6, 0.6, 0.6 } })
    end)
  end
  tabButtons[id] = b
  return b
end

-- actions du sous-menu « Plus » (boutons, pas des onglets)
local actionButtons = {}
local MORE_ACTIONS = {
  { key = "act:macros", title = "Macros", icon = ICONS.macros, fn = function() toggleMacros() end,
    tip = "Clic : prendre une macro" },
  { key = "act:edit", title = "Déplacer", icon = ICONS.edit, fn = function() TreeAI:SetEditMode(not TreeAI:IsEditMode()) end,
    tip = "Déplacer les cadres (mode édition)" },
  { key = "act:all", title = "Tout", icon = ICONS.all, fn = function() if TreeAI.ShowHelp then TreeAI:ShowHelp() end end,
    tip = "Tout : toutes les actions des modules" },
  { key = "act:reset", title = "Réinitialiser l'interface", icon = ICONS.reset, wide = true,
    fn = function() TreeAI:ResetInterface() end,
    tip = "Remet tous les cadres TreeAI à leur place automatique, taille et onglets par défaut" },
}
local function actionButton(a)
  local b = actionButtons[a.key]
  if b then return b end
  b = newTabButton(a.key, a.icon)
  b.action = a.key
  b:SetScript("OnClick", function() TreeAI.safecall(a.fn) end)
  b:SetScript("OnEnter", function(self) showTooltip(self, L[a.title], a.tip and { { L[a.tip], 0.8, 0.8, 0.8 } } or nil) end)
  actionButtons[a.key] = b
  return b
end

local function buildTabs()
  for _, t in ipairs(TreeAI:GetTabs()) do tabButton(t.id) end
  for _, id in ipairs(PRIMARY) do tabButton(id) end
  tabButton(MORE)
end

-- texte qui tient dans maxW : libellé complet, sinon court, sinon coupé + « … » (jamais de chevauchement)
local function fitLabel(fs, full, short, maxW)
  fs:SetText(full)
  if (fs:GetStringWidth() or 0) <= maxW then return full end
  if short and short ~= full then
    fs:SetText(short)
    if (fs:GetStringWidth() or 0) <= maxW then return short end
  end
  local s = short or full
  while #s > 0 do
    s = s:gsub("[%z\1-\127\192-\253][\128-\191]*$", "")
    fs:SetText(s .. "…")
    if (fs:GetStringWidth() or 0) <= maxW then return s .. "…" end
  end
  fs:SetText("")
  return ""
end
TreeAI._FitLabel = fitLabel

local function isMoreTab(id) return id ~= nil and not IS_PRIMARY[id] end

-- onglets secondaires (sous-menu « Plus ») + actions, dans l'ordre des onglets
local function moreEntries()
  local out = {}
  for _, t in ipairs(TreeAI:GetTabs()) do
    if not IS_PRIMARY[t.id] then out[#out + 1] = { id = t.id, order = t.order } end
  end
  local tal = TreeAI.tabs.talents
  if tal and #TreeAI:GetTabLines("talents") > 0 then out[#out + 1] = { id = "talents", order = tal.order } end
  table.sort(out, function(a, b) if a.order ~= b.order then return a.order < b.order end return a.id < b.id end)
  for _, a in ipairs(MORE_ACTIONS or {}) do out[#out + 1] = { id = a.key, action = a } end
  return out
end

-- rangée 1 = 4 cases égales (Maintenant · Quêtes · Groupe · Plus) ; rangées suivantes (« Plus » ouvert ou
-- onglet secondaire actif) = boutons à leur largeur mesurée, renvoyés à la ligne. Renvoie la hauteur prise.
-- tabRects garde la géométrie (tests : aucun chevauchement, texte ≤ case).
local tabRects = {}
local function shortOf(id) return SHORT[id] and L[SHORT[id]] or nil end
local function layoutTabs(width)
  for i = #tabRects, 1, -1 do tabRects[i] = nil end
  for _, b in pairs(tabButtons) do b:Hide() end
  local inner = width - 2 * PAD
  local slot = floor(inner / 4)
  local function place(b, x, row, w, label)
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", panel, "TOPLEFT", x, -HEADER_H - row * (TAB_ROW_H + 2))
    b:SetWidth(w)
    b:SetHeight(TAB_ROW_H)
    b:Show()
    tabRects[#tabRects + 1] = { id = b.id or b.action, x = x, w = w, row = row, text = label,
      textW = b.text:GetStringWidth() or 0, textMax = w - TAB_ICON - 6 }
  end
  -- une seule rangée : Maintenant · Quêtes · Groupe · « Plus » (= l'onglet secondaire actif s'il y en a un)
  local act = activeTab()
  for i, id in ipairs({ PRIMARY[1], PRIMARY[2], PRIMARY[3], MORE }) do
    local b = tabButton(id)
    local w = slot - 2
    local full, short = tabTitle(id), shortOf(id)
    if id == MORE then
      if isMoreTab(act) then
        full, short = tabTitle(act), shortOf(act)
        b.icon:SetTexture(TAB_ICONS[act] or TAB_ICONS.info)
      else
        b.icon:SetTexture(TAB_ICONS[MORE])
      end
    end
    place(b, PAD + (i - 1) * slot, 0, w, fitLabel(b.text, full, short, w - TAB_ICON - 6))
  end
  return TAB_ROW_H + 2
end

-- menu déroulant « Plus » : une liste propre sous l'onglet (onglets secondaires, séparateur, actions) ;
-- se ferme au clic (dedans ou ailleurs) et à Échap. Cadre à nous, enfant d'UIParent.
local menu
local menuEntries = {}
local MENU_ROW, MENU_MIN_W = 20, 180
local function closeMenu()
  moreOpen = false
  if menu and menu:IsShown() then menu:Hide() end
end
local function buildMenu()
  if menu or not _G.CreateFrame then return menu end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  menu = _G.CreateFrame("Frame", "TreeAIMoreMenu", _G.UIParent, template)
  menu:SetFrameStrata("DIALOG")
  menu:SetClampedToScreen(true)
  menu:EnableMouse(true)
  if menu.SetBackdrop then
    menu:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 10, insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    menu:SetBackdropColor(0.05, 0.06, 0.08, 0.96)
    menu:SetBackdropBorderColor(0.78, 0.62, 0.28, 0.6)
  end
  menu.rows, menu.seps = {}, {}
  menu:SetScript("OnHide", function() moreOpen = false; if panel then TreeAI:RequestRefresh() end end)
  -- clic ailleurs = fermer (événement du client moderne, gardé)
  if menu.RegisterEvent and pcall(menu.RegisterEvent, menu, "GLOBAL_MOUSE_DOWN") then
    menu:SetScript("OnEvent", function(self)
      if not self:IsShown() then return end
      local over = (self.IsMouseOver and self:IsMouseOver()) or false
      local mb = tabButtons[MORE]
      if not over and not (mb and mb.IsMouseOver and mb:IsMouseOver()) then closeMenu() end
    end)
  end
  local list = rawget(_G, "UISpecialFrames")
  if type(list) == "table" then list[#list + 1] = "TreeAIMoreMenu" end   -- Échap ferme (notre cadre seulement)
  menu:Hide()
  return menu
end
local function menuRow(i)
  local b = menu.rows[i]
  if b then return b end
  b = _G.CreateFrame("Button", nil, menu)
  b:SetHeight(MENU_ROW)
  b:RegisterForClicks("LeftButtonUp")
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(14, 14)
  b.icon:SetPoint("LEFT", b, "LEFT", 6, 0)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("LEFT", b.icon, "RIGHT", 6, 0)
  b.text:SetPoint("RIGHT", b, "RIGHT", -6, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b.hl = b:CreateTexture(nil, "HIGHLIGHT")
  b.hl:SetAllPoints(b)
  b.hl:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.hl.SetVertexColor then b.hl:SetVertexColor(1, 1, 1, 0.10) end
  b.sel = b:CreateTexture(nil, "BACKGROUND")
  b.sel:SetAllPoints(b)
  b.sel:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.sel.SetVertexColor then b.sel:SetVertexColor(0.78, 0.62, 0.28, 0.25) end
  b:SetScript("OnClick", function(self)
    local e = self.e
    closeMenu()
    if not e then return end
    if e.action then TreeAI.safecall(e.action.fn) else TreeAI:SelectTab(e.id) end
  end)
  b:SetScript("OnEnter", function(self)
    local e = self.e
    if not e then return end
    if e.action then showTooltip(self, L[e.action.title], e.action.tip and { { L[e.action.tip], 0.8, 0.8, 0.8 } } or nil)
    else showTooltip(self, tabTitle(e.id), { { L["Clic : ouvrir l'onglet"], 0.6, 0.6, 0.6 } }) end
  end)
  b:SetScript("OnLeave", hideTooltip)
  menu.rows[i] = b
  return b
end
local function fillMenu()
  if not buildMenu() then return end
  for i = #menuEntries, 1, -1 do menuEntries[i] = nil end
  for _, r in ipairs(menu.rows) do r:Hide(); r.e = nil end
  for _, t in ipairs(menu.seps) do t:Hide() end
  local act = activeTab()
  local entries = moreEntries()
  -- largeur : le libellé le plus long (mesuré), au moins MENU_MIN_W
  local probe = menuRow(1)
  local w = MENU_MIN_W
  for _, e in ipairs(entries) do
    probe.text:SetText(e.action and L[e.action.title] or tabTitle(e.id))
    w = max(w, floor((probe.text:GetStringWidth() or 0) + 14 + 6 + 6 + 12 + 6))
  end
  w = min(w, 320)
  local y, n, seps, prevAction = -4, 0, 0, false
  for _, e in ipairs(entries) do
    if e.action and not prevAction and n > 0 then   -- séparateur entre onglets et actions
      seps = seps + 1
      local t = menu.seps[seps]
      if not t then
        t = menu:CreateTexture(nil, "ARTWORK")
        t:SetTexture("Interface\\Buttons\\WHITE8X8")
        if t.SetVertexColor then t:SetVertexColor(0.78, 0.62, 0.28, 0.35) end
        t:SetHeight(1)
        menu.seps[seps] = t
      end
      t:ClearAllPoints()
      t:SetPoint("TOPLEFT", menu, "TOPLEFT", 8, y - 3)
      t:SetPoint("TOPRIGHT", menu, "TOPRIGHT", -8, y - 3)
      t:Show()
      menuEntries[#menuEntries + 1] = { kind = "sep", y = y - 3, h = 1 }
      y = y - 7
    end
    prevAction = e.action and true or false
    n = n + 1
    local b = menuRow(n)
    b.e = e
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", menu, "TOPLEFT", 3, y)
    b:SetPoint("TOPRIGHT", menu, "TOPRIGHT", -3, y)
    b:SetWidth(w - 6)
    local label = e.action and L[e.action.title] or tabTitle(e.id)
    b.text:SetText(label)
    b.icon:SetTexture(e.action and e.action.icon or TAB_ICONS[e.id] or TAB_ICONS.info)
    if e.action then b.text:SetTextColor(0.6, 0.85, 1); b.sel:Hide()
    else
      local list = TreeAI:GetTabLines(e.id)
      local urgent = false
      for _, l in ipairs(list) do if l.prio >= 3 then urgent = true break end end
      if e.id == act then b.text:SetTextColor(1, 1, 1); b.sel:Show()
      elseif urgent then b.text:SetTextColor(1, 0.35, 0.3); b.sel:Hide()
      elseif #list == 0 then b.text:SetTextColor(0.55, 0.55, 0.55); b.sel:Hide()
      else b.text:SetTextColor(1, 0.82, 0); b.sel:Hide() end
    end
    b:Show()
    menuEntries[#menuEntries + 1] = { kind = e.action and "action" or "tab", id = e.id, y = y, h = MENU_ROW,
      w = w - 6, text = label, textW = b.text:GetStringWidth() or 0, textMax = w - 6 - 14 - 6 - 6 - 6 }
    y = y - MENU_ROW
  end
  menu:SetSize(w, -y + 4)
  local mb = tabButtons[MORE]
  menu:ClearAllPoints()
  if mb then menu:SetPoint("TOPRIGHT", mb, "BOTTOMRIGHT", 0, -2)
  else menu:SetPoint("TOP", _G.UIParent, "TOP", 0, -200) end
  menu:SetScale((acc() and acc().scale) or 1)
end
TreeAI._FillMoreMenu = fillMenu
function TreeAI:GetMoreMenu() return menu, menuEntries end

function TreeAI:GetTabLayout() return tabRects end
function TreeAI:ToggleMoreMenu(v)
  if v == nil then v = not moreOpen end
  moreOpen = v and true or false
  if moreOpen and isMini() then local c = char(); if c then c.mini = false end end
  self:RefreshPanel()
  if moreOpen and panel and panel:IsShown() then
    fillMenu()
    if menu then menu:Show() end
  else
    closeMenu()
  end
  return moreOpen
end
function TreeAI:IsMoreMenuOpen() return moreOpen end

function TreeAI:CreatePanel()
  if panel or not _G.CreateFrame then return panel end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  panel = _G.CreateFrame("Frame", "TreeAIPanel", _G.UIParent, template)
  panel:SetSize(DEFAULT_W, 40)
  panel:SetFrameStrata("MEDIUM")
  panel:SetClampedToScreen(true)
  panel:SetMovable(true)
  panel:EnableMouse(true)
  if panel.EnableMouseWheel then panel:EnableMouseWheel(true) end
  draggable(panel)
  if panel.SetBackdrop then
    panel:SetBackdrop({
      bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      tile = true, tileSize = 16, edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    panel:SetBackdropColor(0.05, 0.08, 0.06, 0.85)
    panel:SetBackdropBorderColor(0.2, 0.8, 0.4, 0.8)
  end
  panel:SetScript("OnMouseWheel", function(_, delta) TreeAI:ScrollTab(delta > 0 and -1 or 1) end)

  -- header: title (right-click = mini), mini line, toolbar
  local head = _G.CreateFrame("Button", nil, panel)
  head:SetHeight(HEADER_H)
  head:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, -2)
  head:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  draggable(head)
  panel.head = head
  local title = head:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  title:SetPoint("LEFT", head, "LEFT", 0, 0)
  title:SetText("|cff33cc66TreeAI|r")
  panel.title = title
  local miniText = head:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  miniText:SetPoint("LEFT", title, "RIGHT", 6, 0)
  miniText:SetPoint("RIGHT", head, "RIGHT", 0, 0)
  miniText:SetJustifyH("LEFT")
  if miniText.SetWordWrap then miniText:SetWordWrap(false) end
  panel.miniText = miniText
  head:SetScript("OnClick", function(_, button)
    if button == "RightButton" then TreeAI:ToggleMini(); return end
    if isMini() then
      local e = panel.miniEntry
      if e and e.onClick then runEntry(e, button) else TreeAI:SetMini(false) end
    end
  end)
  head:SetScript("OnEnter", function(self)
    if isMini() and panel.miniEntry then entryTooltip(self, panel.miniEntry)
    else
      local S = TreeAI.settings or {}
      showTooltip(self, "TreeAI Coach", {
        { L["Mode"] .. " : " .. L[(TreeAI.PRESET_LABEL or {})[S.preset or "normal"] or "Perso"] .. "  (/tai mode)", 0.4, 0.8, 1 },
        { L["Glisser : déplacer · clic droit : réduire/agrandir"], 0.6, 0.6, 0.6 },
        { L["/tai aide : toutes les commandes"], 0.6, 0.6, 0.6 } })
    end
  end)
  head:SetScript("OnLeave", hideTooltip)

  -- toolbar, right to left
  toolButton("close", L["Cacher le panneau (/tai afficher)"], function() TreeAI:HidePanel() end)
  toolButton("mini", function() return isMini() and L["Agrandir"] or L["Réduire à une ligne"] end,
    function() TreeAI:ToggleMini() end)
  toolButton("locked", function() local s = acc(); return s and s.locked and L["Déverrouiller"] or L["Verrouiller"] end,
    function()
      local s = acc(); if not s then return end
      s.locked = not s.locked
      TreeAI:RefreshPanel()
    end)
  toolButton("options", L["Options"], function() if TreeAI.OpenOptions then TreeAI:OpenOptions() end end)
  -- (Macros, Déplacer, Tout, Réinitialiser : dans le sous-menu « Plus », la barre reste courte)
  toolButton("radar", L["Radar (afficher / cacher)"], function()
    local R = TreeAI.GetModule and TreeAI:GetModule("Radar")
    if R and type(R.Show) == "function" then TreeAI.safecall(R.Show, not (R.db and R.db.shown ~= false))
    else TreeAI:HandleSlash("radar") end
  end)
  if TreeAI.Compass or (TreeAI.GetModule and TreeAI:GetModule("Compass")) then
    toolButton("compass", L["Boussole"], toggleCompass)
  end
  local x = -PAD + 2
  for _, b in ipairs(tools) do
    b:SetPoint("TOPRIGHT", panel, "TOPRIGHT", x, -4)
    x = x - 16
  end

  buildTabs()

  for i = 1, MAX_ROWS do
    rows[i] = makeRow(i)
    rows[i]:Hide()
  end
  panel.empty = panel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  panel.empty:SetJustifyH("LEFT")
  panel.thumb = panel:CreateTexture(nil, "OVERLAY")
  panel.thumb:SetTexture("Interface\\Buttons\\WHITE8X8")
  if panel.thumb.SetVertexColor then panel.thumb:SetVertexColor(0.2, 0.8, 0.4, 0.6) end
  panel.thumb:SetWidth(2)
  panel.more = panel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  panel.more:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -PAD - 14, 3)   -- place pour la poignée de largeur

  -- expire old suggestions even without new events
  TreeAI:NewTicker(1, function() if panel:IsShown() and TreeAI:PanelDirty() then TreeAI:RequestRefresh() end end)

  applyPosition()
  local s = acc()
  if s and s.shown == false then panel:Hide() else panel:Show() end
  self:RefreshPanel()
  return panel
end

local function renderMini()
  local e = TreeAI:GetTabLines("now")[1] or TreeAI:GetTabLines("quests")[1]
  panel.miniEntry = e
  if e then
    local c = PRIO_COLORS[e.prio] or PRIO_COLORS[1]
    panel.miniText:SetText(tostring(e.text))
    panel.miniText:SetTextColor(c[1], c[2], c[3])
  else
    panel.miniText:SetText(L["Rien à signaler."])
    panel.miniText:SetTextColor(0.5, 0.5, 0.5)
  end
end

-- barre de commandes d'un onglet : vrais petits boutons (largeur mesurée, 2 rangées max), infobulle = celle
-- de la ligne, clic = son onClick. Renvoie la hauteur prise (0 sans commande).
local ctrlButtons = {}
local ctrlRects = {}
local CTRL_H = 18
local function ctrlButton(i)
  local b = ctrlButtons[i]
  if b then return b end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  b = _G.CreateFrame("Button", nil, panel, template)
  b:SetHeight(CTRL_H)
  b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  if b.SetBackdrop then
    b:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Buttons\\WHITE8X8", edgeSize = 1 })
    b:SetBackdropColor(0.14, 0.15, 0.19, 0.95)
    b:SetBackdropBorderColor(0.78, 0.62, 0.28, 0.45)
  end
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("CENTER", b, "CENTER", 0, 0)
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b.hl = b:CreateTexture(nil, "HIGHLIGHT")
  b.hl:SetAllPoints(b)
  b.hl:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.hl.SetVertexColor then b.hl:SetVertexColor(1, 1, 1, 0.12) end
  b:SetScript("OnClick", function(self, button) runEntry(self.entry, button) end)
  b:SetScript("OnEnter", function(self) if self.entry then entryTooltip(self, self.entry) end end)
  b:SetScript("OnLeave", hideTooltip)
  ctrlButtons[i] = b
  return b
end
local function layoutControls(list, width, top)
  for i = #ctrlRects, 1, -1 do ctrlRects[i] = nil end
  for _, b in ipairs(ctrlButtons) do b:Hide(); b.entry = nil end
  if #list == 0 then return 0 end
  local inner = width - 2 * PAD
  local x, row = PAD, 0
  for i, e in ipairs(list) do
    if row > 1 then break end
    local b = ctrlButton(i)
    b.entry = e
    local label = e.label or e.text
    local w = min(inner, floor((function() b.text:SetText(label); return b.text:GetStringWidth() or 40 end)() + 14))
    if x > PAD and x + w > PAD + inner then x, row = PAD, row + 1 end
    if row > 1 then b:Hide(); break end
    fitLabel(b.text, label, nil, w - 8)
    b:SetWidth(w)
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", panel, "TOPLEFT", x, -top - row * (CTRL_H + 2))
    b:Show()
    ctrlRects[#ctrlRects + 1] = { id = e.id, x = x, w = w, row = row, text = b.text:GetText(),
      textW = b.text:GetStringWidth() or 0, textMax = w - 8 }
    x = x + w + 3
  end
  return (row + 1) * (CTRL_H + 2) + 2
end
function TreeAI:GetTabControls() return ctrlRects end

-- ---------------------------------------------------------------- sections dans les onglets (rangé)
-- « Maintenant » : Urgent → Quête en cours → Ensuite ; autres onglets : un titre par bloc (module) quand il y en a
-- au moins deux. Les titres sont dessinés entre les lignes (pas des entrées : rien ne change pour les clics).
local SECTION_SRC = { Quests = "Étape conseillée", plan = "Trajet", main = nil, Auction = "Hôtel des ventes",
  missions = "Missions", Talents = "Talents", Squad = "Groupe", Missions = "Missions" }
local function nowGroup(e)
  if e.prio >= 3 and not e.pin then return 1, L["Urgent"] end
  -- (COHERE) seule l'étape de quête (trajet / « Étape ») est « Quête en cours » ; les autres lignes épinglées
  -- (équipe, abandon, objet qui lance une quête) suivent : danger → étape → actions rapides → infos
  if e.pin and (TreeAI.STEP_SOURCES or {})[e.source or ""] then return 2, L["Quête en cours"] end
  if (e.prio or 1) >= 2 or e.pin then return 3, L["Ensuite"] end
  return 4, L["Infos"]
end
local function blockGroup(e, tab)
  local src = e.source
  if not src then return "~sugg", L["Conseils"] end
  if src == "main" then
    local owner = TreeAI.MAIN_OWNER and TreeAI.MAIN_OWNER[tab]
    local om = owner and TreeAI.modules and TreeAI.modules[owner]
    local t = TreeAI.tabs[tab]
    return "main", L[(om and om.title) or (t and t.title) or tab]
  end
  local m = TreeAI.modules and TreeAI.modules[src]
  return src, L[SECTION_SRC[src] or (m and m.title) or src]
end
TreeAI.PanelSectionOf = function(_, e, tab)
  if tab == "now" then return nowGroup(e) end
  return blockGroup(e, tab)
end
-- « Maintenant » : ordre logique stable (danger d'abord, puis l'étape de quête, puis le reste par priorité)
local function orderNow(list)
  for i, e in ipairs(list) do e._ord = i end
  table.sort(list, function(a, b)
    local ga, gb = nowGroup(a), nowGroup(b)
    if ga ~= gb then return ga < gb end
    return a._ord < b._ord
  end)
end
local secPool = {}
local function sectionFS(i)
  local fs = secPool[i]
  if not fs then
    fs = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    fs:SetJustifyH("LEFT")
    if fs.SetWordWrap then fs:SetWordWrap(false) end
    fs.rule = panel:CreateTexture(nil, "ARTWORK")
    fs.rule:SetTexture("Interface\\Buttons\\WHITE8X8")
    if fs.rule.SetVertexColor then fs.rule:SetVertexColor(0.2, 0.8, 0.4, 0.25) end
    fs.rule:SetHeight(1)
    secPool[i] = fs
  end
  return fs
end

function TreeAI:RefreshPanel()
  if not panel then return end
  if TreeAI._PanelRendered then TreeAI:_PanelRendered() end
  local s = acc()
  local width = panelWidth()
  panel:SetWidth(width)
  local mini = isMini()
  local fsize, gap, isize = look()
  tools.mini.tex:SetTexture(mini and ICONS.maxi or ICONS.mini)
  tools.locked.tex:SetTexture(s and s.locked and ICONS.locked or ICONS.unlocked)
  -- toolbar: only mini/close in mini mode
  local shownTools = 0
  for _, b in ipairs(tools) do
    local keep = not mini or b == tools.mini or b == tools.close
    if keep then b:Show(); shownTools = shownTools + 1 else b:Hide() end
  end
  panel.head:SetWidth(width - 2 * PAD - shownTools * 16 - 2)

  if mini then
    renderMini()
    panel.miniText:Show()
    for _, b in pairs(tabButtons) do b:Hide() end
    closeMenu()
    for _, b in ipairs(ctrlButtons) do b:Hide() end
    for i = 1, MAX_ROWS do rows[i].entry = nil; rows[i]:Hide() end
    for _, fs in ipairs(secPool) do fs:Hide(); fs.rule:Hide() end
    panel.empty:Hide(); panel.thumb:Hide(); panel.more:Hide()
    panel:SetHeight(HEADER_H + 6)
    return
  end
  panel.miniText:Hide()
  panel.miniEntry = nil

  -- en instance, l'onglet « Donjon » apparaît : le sous-menu « Plus » s'ouvre une fois pour le montrer
  -- (en instance, « Plus » passe en rouge pour signaler l'onglet « Donjon » ; plus d'ouverture forcée)
  buildTabs()
  local tabsH = layoutTabs(width)
  local tab = activeTab()
  local secUrgent = false
  for id, b in pairs(tabButtons) do
    if id ~= MORE then
      local list = self:GetTabLines(id)
      local urgent = false
      for _, e in ipairs(list) do if e.prio >= 3 then urgent = true break end end
      if urgent and isMoreTab(id) then secUrgent = true end
      if id == tab then b.text:SetTextColor(1, 1, 1); b.line:Show()
      elseif urgent then b.text:SetTextColor(1, 0.35, 0.3); b.line:Hide()
      elseif #list == 0 then b.text:SetTextColor(0.5, 0.5, 0.5); b.line:Hide()
      else b.text:SetTextColor(1, 0.82, 0); b.line:Hide() end
    end
  end
  local mb = tabButtons[MORE]
  if mb then
    if isMoreTab(tab) or moreOpen then mb.text:SetTextColor(1, 1, 1); mb.line:Show()
    elseif secUrgent or self.tabs.dungeon then mb.text:SetTextColor(1, 0.35, 0.3); mb.line:Hide()
    else mb.text:SetTextColor(1, 0.82, 0); mb.line:Hide() end
  end
  if moreOpen and menu and menu:IsShown() then fillMenu() end

  -- commandes de l'onglet (lignes control = true) : une barre de petits boutons sous les onglets
  local all = self:GetTabLines(tab)
  current = {}
  local ctrls = {}
  for _, e in ipairs(all) do
    if e.control then ctrls[#ctrls + 1] = e else current[#current + 1] = e end
  end
  if tab == "now" then orderNow(current) end
  tabsH = tabsH + layoutControls(ctrls, width, HEADER_H + tabsH + 2)
  local maxRows = min(MAX_ROWS, max(3, (s and s.maxLines) or 8))
  local n = #current
  local first = scroll[tab] or 1
  if first > n - maxRows + 1 then first = n - maxRows + 1 end
  if first < 1 then first = 1 end
  scroll[tab] = first
  local top = -(HEADER_H + tabsH + 4)
  local lineH = fsize + 4
  -- largeur utile du texte : panneau − marges − barre de défilement − icône
  local avail = width - 2 * PAD - 4 - (isize + 3)
  local y = top
  local shown = 0
  -- sections : seulement s'il y a au moins deux groupes dans l'onglet
  local groups, nG = {}, 0
  for _, e in ipairs(current) do
    local g = TreeAI:PanelSectionOf(e, tab)
    if not groups[g] then groups[g] = true; nG = nG + 1 end
  end
  local lastG, nSec = nil, 0
  local secH = fsize + 5
  for i = 1, MAX_ROWS do
    local b, e = rows[i], current[first + i - 1]
    if e and i <= maxRows then
      shown = shown + 1
      local g, gl = TreeAI:PanelSectionOf(e, tab)
      if nG > 1 and g ~= lastG and gl then
        nSec = nSec + 1
        local fs = sectionFS(nSec)
        local font, _, flags = fs:GetFont()
        if font then fs:SetFont(font, max(9, fsize - 1), flags) end
        fs:ClearAllPoints()
        fs:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, y - 1)
        fs:SetWidth(width - 2 * PAD - 4)
        fs:SetText("|cff33cc66" .. tostring(gl) .. "|r")
        fs:Show()
        fs.rule:ClearAllPoints()
        fs.rule:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, y - secH + 1)
        fs.rule:SetWidth(width - 2 * PAD - 4)
        fs.rule:Show()
        y = y - secH - 1
      end
      lastG = g
      b.entry = e
      local c = PRIO_COLORS[e.prio] or PRIO_COLORS[1]
      local font, _, flags = b.text:GetFont()
      if font then b.text:SetFont(font, fsize, flags) end
      b.text:SetText(tostring(e.text))
      b.text:SetTextColor(c[1], c[2], c[3])
      local tw = (b.text.GetUnboundedStringWidth and b.text:GetUnboundedStringWidth()) or b.text:GetStringWidth() or 0
      local nl = tw > avail and 2 or 1
      local h = nl * lineH
      b.lines = nl
      b:SetHeight(h)
      b.text:SetHeight(h)
      b.text:SetWidth(avail)
      b:ClearAllPoints()
      b:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, y)
      b:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -PAD - 4, y)
      local ic = iconOf(e)
      b.icon:SetSize(isize, isize)
      if ic then b.icon:SetTexture(ic); b.icon:Show() else b.icon:Hide() end
      b.text:ClearAllPoints()
      b.text:SetPoint("TOPLEFT", b, "TOPLEFT", ic and (isize + 3) or 0, 0)
      b.text:SetPoint("TOPRIGHT", b, "TOPRIGHT", 0, 0)
      b:Show()
      y = y - h - gap
    else
      b.entry = nil
      b:Hide()
    end
  end
  for i = nSec + 1, #secPool do secPool[i]:Hide(); secPool[i].rule:Hide() end
  panel.sections = nSec
  if shown == 0 then
    panel.empty:ClearAllPoints()
    panel.empty:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, top - 1)
    panel.empty:SetText(L[EMPTY[tab] or "Rien à signaler."])
    panel.empty:Show()
    y = top - lineH
  else
    panel.empty:Hide()
  end
  local bodyH = top - y
  panel:SetHeight(-y + 8 + (n > maxRows and 10 or 0))
  if n > maxRows then
    local th = max(8, floor(bodyH * maxRows / n))
    local off = floor((bodyH - th) * (first - 1) / max(1, n - maxRows))
    panel.thumb:ClearAllPoints()
    panel.thumb:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -PAD + 2, top - off)
    panel.thumb:SetHeight(th)
    panel.thumb:Show()
    panel.more:SetText(("%d-%d/%d"):format(first, min(n, first + maxRows - 1), n))
    panel.more:Show()
  else
    panel.thumb:Hide()
    panel.more:Hide()
  end
end

-- géométrie des lignes visibles (tests) : { {top, h, lines}, ... }
function TreeAI:GetRowLayout()
  local out = {}
  for i = 1, MAX_ROWS do
    local b = rows[i]
    if b and b.entry and b:IsShown() then
      local _, _, _, _, yy = b:GetPoint(1)
      out[#out + 1] = { top = yy, h = b:GetHeight(), lines = b.lines }
    end
  end
  return out
end

function TreeAI:ScrollTab(step)
  local tab = activeTab()
  scroll[tab] = (scroll[tab] or 1) + (step or 1)
  self:RefreshPanel()
end

function TreeAI:SelectTab(id)
  if not self.tabs[id] then return false end
  local c = char()
  if c then c.tab = id; c.mini = false end
  closeMenu()
  if not panel then self:CreatePanel() end
  if panel and not panel:IsShown() then self:ShowPanel() end
  self:RefreshPanel()
  return true
end

function TreeAI:GetActiveTab() return activeTab() end
function TreeAI:GetVisibleLines() return current end

function TreeAI:SetMini(v)
  local c = char()
  if c then c.mini = v and true or false end
  self:RefreshPanel()
end
function TreeAI:ToggleMini() self:SetMini(not isMini()) end
function TreeAI:IsMini() return isMini() end

function TreeAI:ShowPanel()
  if not panel then self:CreatePanel() end
  if panel then panel:Show(); self:RefreshPanel() end
  local s = acc(); if s then s.shown = true end
end

function TreeAI:HidePanel()
  closeMenu()
  if panel then panel:Hide() end
  local s = acc(); if s then s.shown = false end
end

function TreeAI:TogglePanel()
  if panel and panel:IsShown() then self:HidePanel() else self:ShowPanel() end
end

function TreeAI:ResetPanel()
  local s, c = acc(), char()
  if not s then return end
  s.scale, s.alpha = 1, 0.9
  if c then c.point, c.x, c.y, c.manual = "CENTER", 300, 120, nil end
  applyPosition()
  -- « réinitialiser » = retour au placement automatique (zones libres de l'écran)
  if self.ResetMovable then self:ResetMovable("panel") end
end

-- position rapide (assistant) : "TOPRIGHT", "RIGHT", "BOTTOMRIGHT", "BOTTOMLEFT", "CENTER"
TreeAI.PANEL_SPOTS = {
  TOPRIGHT = { "TOPRIGHT", -40, -240 }, RIGHT = { "RIGHT", -40, 0 }, BOTTOMRIGHT = { "BOTTOMRIGHT", -40, 220 },
  BOTTOMLEFT = { "BOTTOMLEFT", 40, 220 }, CENTER = { "CENTER", 300, 120 },
}
function TreeAI:SetPanelPosition(spot)
  local p, c = self.PANEL_SPOTS[spot], char()
  if not (p and c) then return false end
  c.point, c.x, c.y = p[1], p[2], p[3]
  if not panel then self:CreatePanel() end
  applyPosition()
  return true
end

function TreeAI:ApplyPanelSettings()
  applyPosition()
  self:RefreshPanel()
  if self.ApplyAlertSettings then self:ApplyAlertSettings() end
end

function TreeAI:GetPanel() return panel end

---------------------------------------------------------------------------
-- Corner alert box: important (prio 3) items only, 1-2 lines, fades out, click = open the panel tab
---------------------------------------------------------------------------
local CORNERS = {
  TOPRIGHT = { -24, -210 },   -- under the minimap
  TOPLEFT = { 24, -110 },
  BOTTOMLEFT = { 24, 210 },
  BOTTOMRIGHT = { -24, 160 },
}
TreeAI.ALERT_CORNERS = { "TOPRIGHT", "TOPLEFT", "BOTTOMLEFT", "BOTTOMRIGHT" }
local alertItems = {}

local function alertSettings() return TreeAI.settings and TreeAI.settings.alert end

local function makeAlert()
  if alert or not _G.CreateFrame then return alert end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  alert = _G.CreateFrame("Button", "TreeAIAlert", _G.UIParent, template)
  alert:SetSize(230, 22)
  alert:SetFrameStrata("HIGH")
  alert:SetClampedToScreen(true)
  alert:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  if alert.SetBackdrop then
    alert:SetBackdrop({
      bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      tile = true, tileSize = 16, edgeSize = 10,
      insets = { left = 2, right = 2, top = 2, bottom = 2 },
    })
    alert:SetBackdropColor(0.15, 0.03, 0.02, 0.85)
    alert:SetBackdropBorderColor(1, 0.3, 0.25, 0.9)
  end
  alert.lines = {}
  for i = 1, 2 do
    local fs = alert:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    fs:SetPoint("TOPLEFT", alert, "TOPLEFT", 7, -5 - (i - 1) * 13)
    fs:SetPoint("TOPRIGHT", alert, "TOPRIGHT", -7, -5 - (i - 1) * 13)
    fs:SetJustifyH("LEFT")
    if fs.SetWordWrap then fs:SetWordWrap(false) end
    fs:SetTextColor(1, 0.45, 0.4)
    alert.lines[i] = fs
  end
  alert:SetScript("OnClick", function(_, button)
    local it = alertItems[1]
    alert:Hide()
    if button == "RightButton" then return end
    local tab = it and it.tab or "now"
    TreeAI:SelectTab(TreeAI.tabs[tab] and tab or "now")
  end)
  alert:SetScript("OnEnter", function(self)
    self.hold = true
    self:SetAlpha((alertSettings() or {}).alpha or 0.95)
    showTooltip(self, L["Important"], { { L["Clic : ouvrir le panneau · clic droit : fermer"], 0.6, 0.6, 0.6 } })
  end)
  alert:SetScript("OnLeave", function(self) self.hold = false; hideTooltip() end)
  alert:SetScript("OnHide", function() for i = #alertItems, 1, -1 do alertItems[i] = nil end end)
  alert:SetScript("OnUpdate", function(self, dt)
    if self.hold then return end
    self.left = (self.left or 0) - (dt or 0)
    local a = (alertSettings() or {}).alpha or 0.95
    if self.left <= 0 then self:Hide()
    elseif self.left < 1 then self:SetAlpha(a * self.left) end
  end)
  alert:Hide()
  return alert
end

function TreeAI:ApplyAlertSettings()
  local s = alertSettings()
  if not (alert and s) then return end
  alert:ClearAllPoints()
  local pos = type(s.pos) == "table" and s.pos.point and s.pos.corner == s.corner and s.pos  -- auto / édition
  if pos then
    alert:SetPoint(pos.point, _G.UIParent, pos.point, pos.x or 0, pos.y or 0)
  else
    local corner = CORNERS[s.corner] and s.corner or "TOPRIGHT"
    local off = CORNERS[corner]
    alert:SetPoint(corner, _G.UIParent, corner, off[1], off[2])
  end
  alert:SetScale(s.scale or 1)
end

-- e = suggestion or { text=, prio=3, tab=? }; force shows it even when the box is disabled (test)
function TreeAI:ShowAlert(e, force)
  local s = alertSettings()
  if not e or not e.text or (not force and (not s or s.enabled == false or (e.prio or 0) < 3)) then return end
  if not makeAlert() then return end
  local tab = e.tab
  if not tab and self.TabOf and e.time then tab = self:TabOf(e) end
  for i = #alertItems, 1, -1 do
    if (e.id and alertItems[i].id == e.id) or alertItems[i].text == e.text then table.remove(alertItems, i) end
  end
  table.insert(alertItems, 1, { id = e.id, text = tostring(e.text), tab = tab })
  for i = #alertItems, 3, -1 do alertItems[i] = nil end
  for i = 1, 2 do
    local it = alertItems[i]
    alert.lines[i]:SetText(it and it.text or "")
    if it then alert.lines[i]:Show() else alert.lines[i]:Hide() end
  end
  alert:SetHeight(10 + 13 * #alertItems)
  self:ApplyAlertSettings()
  alert.left = (s and s.duration) or 8
  local wasShown = alert:IsShown()
  alert:SetAlpha((s and s.alpha) or 0.95)
  alert:Show()
  -- fondu d'entrée (une seule fois à l'ouverture, pas à chaque nouvelle ligne)
  if not wasShown and self.Theme and self.Theme.Animate then self.Theme:Animate(alert, 0, (s and s.alpha) or 0.95, 0.2) end
end

function TreeAI:GetAlert() return alert, alertItems end

---------------------------------------------------------------------------
-- Placement automatique de NOS cadres (panneau, encadré d'alerte, boussole, suivi, barres chasseur).
-- 1. scan en LECTURE SEULE de l'écran : cadres connus (minimap, barres, chat, suivi des quêtes, cadres
--    d'unité, Details/Recount/Bartender/ElvUI…) + cadres visibles enfants d'UIParent (EnumerateFrames),
--    IsVisible + GetRect, sans les cadres plein écran / cachés / minuscules / les nôtres ;
-- 2. grille d'occupation (cases de 16 unités d'interface, échelle d'UI respectée) ;
-- 3. chaque rôle prend le meilleur rectangle libre près de sa cible (taille adaptée à la place).
-- Jamais un cadre de Blizzard ou d'un autre addon n'est déplacé ; hors combat seulement.
-- Un cadre déplacé à la main garde sa place (« réinitialiser » / « Placer automatiquement » le rendent).
---------------------------------------------------------------------------
local CELL = 16
local SCAN_NAMES = {
  "MinimapCluster", "Minimap", "MinimapBackdrop", "MainMenuBar", "MainMenuBarArtFrame", "MainActionBar",
  "MultiBarBottomLeft", "MultiBarBottomRight", "MultiBarRight", "MultiBarLeft", "MultiBar5", "MultiBar6",
  "MultiBar7", "StanceBar", "StanceBarFrame", "PetActionBar", "PetActionBarFrame", "MicroButtonAndBagsBar",
  "MicroMenuContainer", "BagsBar", "MainMenuExpBar", "ReputationWatchBar", "StatusTrackingBarManager",
  "MainStatusTrackingBarContainer", "GeneralDockManager", "ChatFrameMenuButton", "QuickJoinToastButton",
  "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame", "QuestTimerFrame", "Questie_BaseFrame",
  "QuestieTrackerBaseFrame", "PlayerFrame", "TargetFrame", "TargetFrameToT", "FocusFrame", "PetFrame",
  "PartyFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3", "PartyMemberFrame4",
  "CompactPartyFrame", "CompactRaidFrameContainer", "BuffFrame", "DebuffFrame", "TemporaryEnchantFrame",
  "CastingBarFrame", "PlayerCastingBarFrame", "DurabilityFrame", "VehicleSeatIndicator", "UIWidgetTopCenterContainerFrame",
  "DetailsBaseFrame1", "DetailsBaseFrame2", "Details_BaseFrame1", "Recount_MainWindow", "SkadaBarWindowSkada",
  "ElvUI_Bar1", "ElvUI_Bar2", "ElvUI_Bar3", "ElvUI_Bar4", "ElvUI_Bar5", "ElvUI_Bar6", "ElvUF_Player",
  "ElvUF_Target", "ElvUF_Party", "LeftChatPanel", "RightChatPanel", "MMHolder", "ElvUI_MinimapHolder",
  "BT4Bar1", "BT4Bar2", "BT4Bar3", "BT4Bar4", "BT4Bar5", "BT4Bar6", "BT4Bar7", "BT4Bar8", "BT4Bar9", "BT4Bar10",
  "BT4BarMicroMenu", "BT4BarBagBar", "BT4BarStanceBar", "BT4BarPetBar",
}
for _, n in ipairs({
  -- boutons autour de la minimap
  "MiniMapTracking", "MiniMapTrackingFrame", "MinimapZoneTextButton", "MinimapBorderTop", "GameTimeFrame",
  "TimeManagerClockButton", "MiniMapMailFrame", "MinimapZoomIn", "MinimapZoomOut", "MiniMapWorldMapButton",
  "MiniMapBattlefieldFrame", "MiniMapLFGFrame", "MinimapToggleButton", "ExpansionLandingPageMinimapButton",
  -- suivis de quêtes (Questie…), compteurs (Details / Recount / Skada), raid, barres spéciales
  "Questie_BaseFrame", "QuestieTrackerBaseFrame", "Questie_TrackerBaseFrame", "QuestieTracker_BaseFrame",
  "Questie_HeaderFrame", "Questie_TrackedQuests", "DetailsBaseFrame3", "Details_BaseFrame2", "Recount_DetailWindow",
  "CompactRaidFrameManager", "ExtraActionBarFrame", "ZoneAbilityFrame", "ChatFrame1EditBox", "PlayerPowerBarAlt",
  -- (COHERE) Shortest Path Forever : colonne « Suivi Forever » (nettoyer le journal, trajet, composants) ;
  -- noms probables, le préfixe ci-dessous attrape les autres (lecture seule, jamais déplacés)
  "ShortestPathForeverTracker", "ShortestPathForeverTrackerFrame", "ShortestPathForeverFrame",
  "ShortestPathForever_Tracker", "ShortestPathForeverTrackerBaseFrame", "SPFTrackerFrame", "SPF_TrackerFrame",
  "SPF_Tracker", "ShortestPathTrackerFrame",
}) do SCAN_NAMES[#SCAN_NAMES + 1] = n end
for i = 1, 10 do SCAN_NAMES[#SCAN_NAMES + 1] = "ChatFrame" .. i end
TreeAI.LAYOUT_SCAN_NAMES = SCAN_NAMES
-- cadres d'autres addons repérés par leur préfixe (enfants de la minimap compris : boutons LibDBIcon)
local SCAN_PREFIX = { "LibDBIcon10_", "Questie_", "QuestieTracker", "Details", "Recount", "Skada",
  "CompactRaid", "CompactParty", "PartyMemberFrame", "ShortestPath", "SPF_", "SPFTracker" }
-- (COHERE) suivis d'autres addons : leur cadre racine peut être une petite ancre dont les lignes pendent
-- dessous -> rectangle = union des enfants visibles ; et ils grandissent comme un suivi de quêtes
local GUIDE_PREFIX = { "ShortestPath", "SPF_", "SPFTracker", "Questie" }
local function guideName(n)
  if type(n) ~= "string" then return false end
  for _, p in ipairs(GUIDE_PREFIX) do if n:sub(1, #p) == p then return true end end
  return false
end
local function trackerish(n)
  if not guideName(n) then return false end
  local l = n:lower()
  return (l:find("track") or l:find("suivi") or l:find("list") or l:find("base")) and true or false
end
TreeAI.LAYOUT_GUIDE_PREFIX = GUIDE_PREFIX
TreeAI.LAYOUT_SCAN_PREFIX = SCAN_PREFIX
-- suivis de quêtes : ils grandissent avec les quêtes -> on réserve la colonne jusqu'au tiers bas de l'écran
local GROWS = { ObjectiveTrackerFrame = true, QuestWatchFrame = true, WatchFrame = true, Questie_BaseFrame = true,
  QuestieTrackerBaseFrame = true, Questie_TrackerBaseFrame = true, QuestieTracker_BaseFrame = true,
  Questie_TrackedQuests = true }
-- cadres de groupe : réservés même cachés (le groupe se forme plus tard, ils ne doivent pas tomber sur nous)
local RESERVE_HIDDEN = { PartyMemberFrame1 = true, PartyMemberFrame2 = true, PartyMemberFrame3 = true,
  PartyMemberFrame4 = true, CompactPartyFrame = true, PartyFrame = true }
local PARTY_NAMES = { "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3", "PartyMemberFrame4",
  "CompactPartyFrame", "PartyFrame", "CompactRaidFrameContainer", "ElvUF_Party" }
local TRACKER_NAMES = { "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame", "Questie_BaseFrame",
  "QuestieTrackerBaseFrame", "Questie_TrackerBaseFrame", "QuestieTracker_BaseFrame", "Questie_TrackedQuests" }
local function hasPrefix(n)
  if type(n) ~= "string" then return false end
  for _, p in ipairs(SCAN_PREFIX) do if n:sub(1, #p) == p then return true end end
  return false
end
local SKIP_STRATA = { BACKGROUND = true, FULLSCREEN = true, FULLSCREEN_DIALOG = true, TOOLTIP = true }

local function call(f, m, ...)
  local fn = f and f[m]
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d = pcall(fn, f, ...)
  if ok then return a, b, c, d end
end

local function screenSize()
  local P = _G.UIParent
  local w, h = call(P, "GetWidth"), call(P, "GetHeight")
  if not (w and h and w > 200 and h > 200) then
    w = (_G.GetScreenWidth and _G.GetScreenWidth()) or 1920
    h = (_G.GetScreenHeight and _G.GetScreenHeight()) or 1080
  end
  return w, h
end

local function layoutState()
  local c = TreeAI.db and TreeAI:CharKey() ~= "?" and TreeAI:CharDB() or nil
  local st = c and c.layout
  if c and type(st) ~= "table" then st = { manual = {} }; c.layout = st end
  st = st or TreeAI._layoutTmp or { manual = {} }
  TreeAI._layoutTmp = st
  st.manual = type(st.manual) == "table" and st.manual or {}
  return st
end

local function isOurs(f)
  if f == panel or f == alert then return true end
  local n = call(f, "GetName")
  return type(n) == "string" and n:sub(1, 6) == "TreeAI"
end

-- rectangle d'un cadre en unités d'UIParent (gauche, bas, largeur, hauteur) ou nil
local function rectOf(f, W, H, evenHidden)
  if type(f) ~= "table" or isOurs(f) then return nil end
  if call(f, "IsForbidden") then return nil end
  if not evenHidden and not call(f, "IsVisible") then return nil end
  local l, b, w, h = call(f, "GetRect")
  if not (l and b and w and h) or w < 12 or h < 8 then return nil end
  local k = (call(f, "GetEffectiveScale") or 1) / (call(_G.UIParent, "GetEffectiveScale") or 1)
  l, b, w, h = l * k, b * k, w * k, h * k
  if w >= W * 0.9 and h >= H * 0.6 then return nil end      -- plein écran (conteneurs, ElvUIParent…)
  if SKIP_STRATA[call(f, "GetFrameStrata") or ""] then return nil end
  local a = call(f, "GetEffectiveAlpha") or call(f, "GetAlpha") or 1
  if a < 0.05 and not evenHidden then return nil end
  if l + w < 0 or b + h < 0 or l > W or b > H then return nil end
  return { l = l, b = b, w = w, h = h, name = call(f, "GetName") }
end
-- (COHERE) union des enfants visibles (≤ 60, un niveau) d'un cadre de suivi d'un autre addon
local function childUnion(f, W, H)
  if type(f) ~= "table" or type(f.GetChildren) ~= "function" or not call(f, "IsVisible") then return nil end
  local kids = { pcall(f.GetChildren, f) }
  if not kids[1] then return nil end
  local l0, b0, r0, t0
  for i = 2, math.min(#kids, 61) do
    local r = rectOf(kids[i], W, H)
    if r then
      l0, b0 = math.min(l0 or r.l, r.l), math.min(b0 or r.b, r.b)
      r0, t0 = math.max(r0 or (r.l + r.w), r.l + r.w), math.max(t0 or (r.b + r.h), r.b + r.h)
    end
  end
  if not l0 then return nil end
  return { l = l0, b = b0, w = r0 - l0, h = t0 - b0, name = call(f, "GetName") }
end
local function guideRect(f, W, H, name)
  local r = rectOf(f, W, H)
  if guideName(name) then
    local u = childUnion(f, W, H)
    if u and (not r or u.w * u.h > r.w * r.h) then r = u end
  end
  return r
end
TreeAI._GuideRect = guideRect
TreeAI._discovered = TreeAI._discovered or {}   -- noms de cadres d'addons trouvés par préfixe (reflow léger)

--- Scan lecture seule. Renvoie { W=, H=, rects = { {l,b,w,h,name}... }, named = { [name] = rect } }.
function TreeAI:ScanScreen()
  local W, H = screenSize()
  local rects, named, seen = {}, {}, {}
  local function add(f, name)
    if seen[f] then return end
    seen[f] = true
    name = name or call(f, "GetName")
    local r = guideRect(f, W, H, name)
    if not r and name and RESERVE_HIDDEN[name] then r = rectOf(f, W, H, true); if r then r.hidden = true end end
    if r and guideName(name) then TreeAI._discovered[name] = true end
    if r then
      if r.name and (GROWS[r.name] or trackerish(r.name)) then
        -- la colonne du suivi jusqu'au tiers bas (au moins 250 de large, côté de l'écran conservé)
        local top = r.b + r.h
        local bottom = math.min(r.b, math.max(H * 0.30, top - 560))
        local w = math.max(r.w, 250)
        local l = (r.l + r.w / 2 > W / 2) and math.min(r.l, r.l + r.w - w) or r.l
        r = { l = l, b = bottom, w = w, h = top - bottom, name = r.name, grown = true }
      end
      rects[#rects + 1] = r
      if r.name then named[r.name] = r end
    end
  end
  for _, n in ipairs(SCAN_NAMES) do
    local f = _G[n]
    if type(f) == "table" then add(f, n) end
  end
  if type(_G.EnumerateFrames) == "function" then
    local ok = pcall(function()
      local f, guard = _G.EnumerateFrames(), 0
      while f and guard < 20000 do
        guard = guard + 1
        if call(f, "GetParent") == _G.UIParent or hasPrefix(call(f, "GetName")) then add(f) end
        f = _G.EnumerateFrames(f)
      end
    end)
    if not ok then self:Debug("scan: EnumerateFrames") end
  end
  return { W = W, H = H, rects = rects, named = named }
end

-- grille d'occupation + sommes cumulées (test « rectangle libre » en O(1))
local function newGrid(scan)
  local cols, rowsN = math.floor(scan.W / CELL), math.floor(scan.H / CELL)
  local g = { cols = cols, rows = rowsN, occ = {} }
  for i = 1, cols * rowsN do g.occ[i] = 0 end
  function g.mark(r, margin)
    margin = margin or 0
    local c0 = math.max(0, math.floor((r.l - margin) / CELL))
    local c1 = math.min(cols - 1, math.floor((r.l + r.w + margin - 1) / CELL))
    local r0 = math.max(0, math.floor((r.b - margin) / CELL))
    local r1 = math.min(rowsN - 1, math.floor((r.b + r.h + margin - 1) / CELL))
    for y = r0, r1 do for x = c0, c1 do g.occ[y * cols + x + 1] = 1 end end
    g.sum = nil
  end
  function g.build()
    local S = {}
    local W1 = cols + 1
    for i = 0, (rowsN + 1) * W1 - 1 do S[i] = 0 end
    for y = 1, rowsN do
      local run = 0
      for x = 1, cols do
        run = run + g.occ[(y - 1) * cols + x]
        S[y * W1 + x] = S[(y - 1) * W1 + x] + run
      end
    end
    g.sum, g.W1 = S, W1
  end
  function g.used(x, y, w, h)   -- cases occupées dans [x, x+w) × [y, y+h)
    if not g.sum then g.build() end
    local S, W1 = g.sum, g.W1
    return S[(y + h) * W1 + x + w] - S[y * W1 + x + w] - S[(y + h) * W1 + x] + S[y * W1 + x]
  end
  function g.freeIn(c0, c1, r0, r1)
    local n, f = 0, 0
    for y = math.max(0, r0), math.min(rowsN - 1, r1) do
      for x = math.max(0, c0), math.min(cols - 1, c1) do n = n + 1; if g.occ[y * cols + x + 1] == 0 then f = f + 1 end end
    end
    return n > 0 and f / n or 0
  end
  for _, r in ipairs(scan.rects) do g.mark(r, 8) end
  return g
end

-- meilleur emplacement libre pour un rectangle w×h (unités UI) près de (tx, ty) ; renvoie l, b, score
local function bestSpot(g, w, h, tx, ty)
  local cw, ch = math.ceil(w / CELL), math.ceil(h / CELL)
  if cw > g.cols or ch > g.rows then return nil end
  -- (LITE) une case sur deux puis affinage autour du meilleur (balayage complet si rien trouvé)
  local best, bx, by
  local function scan(x0, x1, y0, y1, step)
    for y = math.max(0, y0), math.min(g.rows - ch, y1), step do
      for x = math.max(0, x0), math.min(g.cols - cw, x1), step do
        if g.used(x, y, cw, ch) == 0 then
          local cx, cy = (x + cw / 2) * CELL, (y + ch / 2) * CELL
          local d = math.sqrt((cx - tx) ^ 2 + (cy - ty) ^ 2)
          if not best or d < best then best, bx, by = d, x, y end
        end
      end
    end
  end
  scan(0, g.cols - cw, 0, g.rows - ch, 2)
  if best then scan(bx - 1, bx + 1, by - 1, by + 1, 1) else scan(0, g.cols - cw, 0, g.rows - ch, 1) end
  if not best then return nil end
  return bx * CELL, by * CELL, best
end

-- point d'ancrage selon le quart d'écran + décalages dans l'échelle du cadre (k)
local function anchorFor(l, b, w, h, W, H, k, forceTop)
  k = k or 1
  local cx, cy = l + w / 2, b + h / 2
  local top = forceTop or cy > H / 2
  local v = top and "TOP" or "BOTTOM"
  local hz
  if math.abs(cx - W / 2) < W * 0.12 then hz = "" elseif cx < W / 2 then hz = "LEFT" else hz = "RIGHT" end
  local x = (hz == "LEFT" and l) or (hz == "RIGHT" and (l + w - W)) or (cx - W / 2)
  local y = top and (b + h - H) or b
  return v .. hz, math.floor(x / k + 0.5), math.floor(y / k + 0.5)
end

local function moduleFrame(modName, frameName)
  local m = TreeAI.modules and TreeAI.modules[modName]
  local f = _G[frameName]
  return m, (type(f) == "table") and f or nil
end

-- les cadres de nos modules : un déplacement à la main = place gardée (crochet sur notre propre cadre)
local hooked = {}
local function hookManual(f, role)
  if not f or hooked[f] or not f.HookScript then return end
  hooked[f] = true
  pcall(f.HookScript, f, "OnDragStop", function() layoutState().manual[role] = true end)
end

local PANEL_W = { 360, 340, 320, 300 }
local PANEL_L = { 8, 6, 5, 4 }
local LAYOUT_V = 3          -- version du placement : +1 = replacement unique pour les joueurs existants
                            -- (3 = COHERE : colonne « Suivi Forever » de Shortest Path évitée)
TreeAI.LAYOUT_VERSION = LAYOUT_V

-- le rectangle (unités UI) est-il entièrement libre sur la grille ?
local function fitsAt(g, l, b, w, h)
  if l < 0 or b < 0 then return false end
  local c0, r0 = math.floor(l / CELL), math.floor(b / CELL)
  local c1, r1 = math.ceil((l + w) / CELL), math.ceil((b + h) / CELL)
  if c1 > g.cols or r1 > g.rows then return false end
  return g.used(c0, r0, c1 - c0, r1 - r0) == 0
end
TreeAI._FitsAt = fitsAt

-- meilleur emplacement libre selon une fonction de score (plus petit = mieux)
-- (LITE) une case sur deux, puis affinage autour du meilleur : 4× moins de calculs au placement ; rien
-- trouvé ainsi -> balayage complet (un trou étroit n'est jamais raté)
local function bestBy(g, w, h, score)
  local cw, ch = math.ceil(w / CELL), math.ceil(h / CELL)
  if cw > g.cols or ch > g.rows then return nil end
  local best, bx, by
  local function scan(x0, x1, y0, y1, step)
    for y = math.max(0, y0), math.min(g.rows - ch, y1), step do
      for x = math.max(0, x0), math.min(g.cols - cw, x1), step do
        if g.used(x, y, cw, ch) == 0 then
          local d = score(x * CELL, y * CELL)
          if not best or d < best then best, bx, by = d, x, y end
        end
      end
    end
  end
  scan(0, g.cols - cw, 0, g.rows - ch, 2)
  if best then scan(bx - 1, bx + 1, by - 1, by + 1, 1) else scan(0, g.cols - cw, 0, g.rows - ch, 1) end
  if not best then return nil end
  return bx * CELL, by * CELL, best
end


-- UI2 : profils d'écran (16:9 / 21:9 / 4:3), zones réservées au jeu (centre + personnage), dispositions
--- "16:9" | "21:9" | "4:3" selon le rapport largeur / hauteur d'UIParent.
function TreeAI.ScreenProfile(W, H)
  local r = (tonumber(W) or 1920) / math.max(1, tonumber(H) or 1080)
  if r >= 2.0 then return "21:9" elseif r <= 1.45 then return "4:3" end
  return "16:9"
end
--- Retrait des colonnes de HUD : en 21:9 on vise les bords de la bande centrale 16:9 (pas l'extrême bord
--- qu'on ne regarde jamais), ailleurs le bord de l'écran.
function TreeAI.ScreenInset(W, H)
  if TreeAI.ScreenProfile(W, H) ~= "21:9" then return 0 end
  return math.max(0, math.floor((W - H * 16 / 9) / 2 * 0.75))
end
--- Rectangles gardés libres pour le jeu : le centre de l'écran et la zone du personnage (juste dessous).
function TreeAI.ReservedRects(W, H)
  local inset = TreeAI.ScreenInset(W, H)
  local iw = W - 2 * inset
  return {
    { name = "centre", l = inset + iw * 0.34, b = H * 0.38, w = iw * 0.32, h = H * 0.32 },
    { name = "personnage", l = W / 2 - 110, b = H * 0.30, w = 220, h = H * 0.30 },
  }
end
-- dispositions en un clic : tailles + préférence de placement du panneau
TreeAI.DISPOSITIONS = {
  compacte = { label = "Compacte", width = 300, lines = 4, scale = 0.9, radar = 140, alertScale = 0.9,
               widths = { 300 }, lines_ = { 4, 3 } },
  confort  = { label = "Confort", width = 340, lines = 8, scale = 1, radar = 170, alertScale = 1,
               widths = { 360, 340, 320, 300 }, lines_ = { 8, 6, 5, 4 } },
  coins    = { label = "Coins", width = 320, lines = 6, scale = 1, radar = 160, alertScale = 1,
               widths = { 320, 300 }, lines_ = { 6, 5, 4 } },
}
TreeAI.DISPOSITION_ORDER = { "compacte", "confort", "coins" }
local function dispo()
  local s = TreeAI.settings
  local k = s and s.layoutStyle
  return TreeAI.DISPOSITIONS[k or ""] and k or "confort"
end
TreeAI._Dispo = dispo
-- zone (3×3) d'un rectangle : apprise quand le joueur dépose un cadre
function TreeAI.ZoneOf(l, b, w, h, W, H)
  local cx, cy = l + w / 2, b + h / 2
  local v = (cy > H * 2 / 3) and "TOP" or ((cy < H / 3) and "BOTTOM" or "")
  local hz = (cx < W / 3) and "LEFT" or ((cx > W * 2 / 3) and "RIGHT" or "")
  local z = v .. hz
  return z ~= "" and z or "CENTER"
end
local ZONE_CENTER = { TOPLEFT = { 0, 1 }, TOP = { 0.5, 1 }, TOPRIGHT = { 1, 1 }, LEFT = { 0, 0.5 }, CENTER = { 0.5, 0.5 },
  RIGHT = { 1, 0.5 }, BOTTOMLEFT = { 0, 0 }, BOTTOM = { 0.5, 0 }, BOTTOMRIGHT = { 1, 0 } }
TreeAI.ZONE_CENTER = ZONE_CENTER

local function gapBetween(a, l, b, w, h)
  local dx = math.max(a.l - (l + w), l - (a.l + a.w), 0)
  local dy = math.max(a.b - (b + h), b - (a.b + a.h), 0)
  return dx, dy
end

--- Place nos cadres. force = oublie les déplacements manuels (bouton « Placer automatiquement »).
--- Renvoie la table des placements { role = { point, x, y, l, b, w, h } } (aussi gardée pour /tai placer).
function TreeAI:AutoLayout(force)
  if self:InCombat() then
    self:RunOutOfCombat("autolayout", function() self:AutoLayout(force) end)
    return nil, "combat"
  end
  local st = layoutState()
  if force then st.manual = {} end
  if self.ResetReflow then self:ResetReflow() end   -- les nouvelles places deviennent « chez lui »
  local scan = self:ScanScreen()
  local W, H = scan.W, scan.H
  local g = newGrid(scan)
  -- le centre de l'écran et le personnage restent au jeu (UI2) ; en 21:9 la bande centrale 16:9
  for _, r in ipairs(TreeAI.ReservedRects(W, H)) do g.mark(r, 0) end
  local S = self.settings or {}
  local D = TreeAI.DISPOSITIONS[dispo()]
  local inset = TreeAI.ScreenInset(W, H)
  local placed = {}
  local named = scan.named
  local function keep(role) return st.manual[role] and not force end
  local mm = named.MinimapCluster or named.Minimap or named.MMHolder or named.ElvUI_MinimapHolder
  local party = {}
  for _, n in ipairs(PARTY_NAMES) do if named[n] then party[#party + 1] = named[n] end end

  -- 1. radar : SOUS la minimap si c'est libre, sinon à sa gauche ; jamais sur un suivi de quêtes
  local R, rf = moduleFrame("Radar", "TreeAIRadarFrame")
  hookManual(rf, "radar")
  if R and R.char and R.db and not keep("radar") then   -- placé même caché : juste quand il réapparaît
    local k = call(rf, "GetScale") or 1
    local sz = ((type(R.Size) == "function" and R.Size()) or tonumber(R.db.size) or 170) * k
    local l, b
    if mm then
      local cands = {
        { mm.l + mm.w - sz, mm.b - 10 - sz },            -- sous la minimap, aligné à droite
        { mm.l + (mm.w - sz) / 2, mm.b - 10 - sz },      -- sous la minimap, centré
        { mm.l - 10 - sz, mm.b + mm.h - sz },            -- à gauche, en haut
        { mm.l - 10 - sz, mm.b + (mm.h - sz) / 2 },      -- à gauche, centré
      }
      for _, c in ipairs(cands) do
        if fitsAt(g, c[1], c[2], sz, sz) then l, b = c[1], c[2]; break end
      end
      if not l then l, b = bestSpot(g, sz, sz, mm.l - sz / 2 - 10, mm.b + mm.h / 2) end
    else
      l, b = bestSpot(g, sz, sz, W - 300, H - sz / 2 - 20)
    end
    if l then
      local pt, x, y = anchorFor(l, b, sz, sz, W, H, k, true)
      R.char.point, R.char.x, R.char.y = pt, x, y
      if rf then call(rf, "ClearAllPoints"); call(rf, "SetPoint", pt, _G.UIParent, pt, x, y) end
      placed.radar = { point = pt, x = x, y = y, l = l, b = b, w = sz, h = sz }
      g.mark({ l = l, b = b, w = sz, h = sz }, 6)
    end
  end

  -- 2. encadré d'alerte : juste sous la minimap (sinon la place libre la plus proche)
  local al = S.alert
  if al and not keep("alert") then
    local aw, ah = 230 * (al.scale or 1), 40 * (al.scale or 1)
    local tx, ty = W - 140, H - 230
    if mm then tx, ty = mm.l + mm.w / 2, mm.b - ah / 2 - 8 end
    local l, b = bestSpot(g, aw, ah, tx, ty)
    if l then
      local pt, x, y = anchorFor(l, b, aw, ah, W, H, al.scale or 1, true)
      al.pos = { point = pt, x = x, y = y, corner = al.corner }
      placed.alert = { point = pt, x = x, y = y, l = l, b = b, w = aw, h = ah }
      g.mark({ l = l, b = b, w = aw, h = ah }, 4)
      if self.ApplyAlertSettings then self:ApplyAlertSettings() end
    end
  end

  -- 3. boussole : en haut au centre, jamais sur le cadre de la cible
  local C, cf = moduleFrame("Compass", "TreeAICompassFrame")
  hookManual(cf, "compass")
  if C and C.char and not keep("compass") then
    local k = tonumber(C.db and C.db.scale) or 1
    local cw, chh = 150 * k, 74 * k
    local l, b = bestSpot(g, cw, chh, W / 2, H - 70 - chh / 2)
    if l then
      local pt, x, y = anchorFor(l, b, cw, chh, W, H, k, true)
      C.char.point, C.char.x, C.char.y = pt, x, y
      if cf then call(cf, "ClearAllPoints"); call(cf, "SetPoint", pt, _G.UIParent, pt, x, y) end
      placed.compass = { point = pt, x = x, y = y, l = l, b = b, w = cw, h = chh }
      g.mark({ l = l, b = b, w = cw, h = chh }, 4)
    end
  end

  -- 4. panneau : la plus grande zone libre d'un BORD de l'écran (le centre reste au jeu), loin des cadres
  --    de groupe ; taille adaptée (largeur ≥ 300, lignes sur 2 lignes possibles)
  local P = S.panel
  local c = char()
  local side
  if P and c and not keep("panel") then
    local k = P.scale or 1
    local fsz, gap = look()
    local rowH = (fsz + 4) * 1.4 + gap
    local function heightFor(lines) return (HEADER_H + TAB_ROW_H + 2 + 12 + rowH * lines) * k end
    local style = dispo()
    local function score(l, b, w, h)
      local cx, cy = l + w / 2, b + h / 2
      local sc
      if style == "coins" then                              -- le coin libre le plus proche
        local dx = math.min(math.abs(l - inset), math.abs(W - inset - (l + w)))
        local dy = math.min(b, H - (b + h))
        sc = dx + dy
      elseif style == "compacte" then                       -- groupé avec la minimap / le radar (haut droite)
        local tx = mm and (mm.l + mm.w / 2) or (W - inset - w / 2)
        local ty = mm and (mm.b - h / 2 - 200) or (H * 0.6)
        sc = math.sqrt((cx - tx) ^ 2 + (cy - ty) ^ 2) * 0.6
      else                                                  -- confort : colonne du bord, à hauteur des yeux
        sc = math.min(math.abs(l - inset), math.abs(W - inset - (l + w))) + math.min(cx, W - cx) * 0.15
        sc = sc + math.abs(cy - H * 0.5) * 0.35
      end
      for _, p in ipairs(party) do
        local dx, dy = gapBetween(p, l, b, w, h)
        if dx < 160 and dy < 160 then sc = sc + 700 - dx - dy end
      end
      return sc
    end
    local bestL, bestB, bestScore, bw, bl
    local function search(grid, widths, linesList)
      for wi, pw in ipairs(widths) do
        for li, lines in ipairs(linesList) do
          local w, h = pw * k, heightFor(lines)
          local l, b, d = bestBy(grid, w, h, function(x, y) return score(x, y, w, h) end)
          if l then
            local sc = d + (wi - 1) * 20 + (li - 1) * 45     -- garder la taille si possible
            if not bestScore or sc < bestScore then bestL, bestB, bestScore, bw, bl = l, b, sc, pw, lines end
          end
        end
      end
    end
    search(g, D.widths or PANEL_W, D.lines_ or PANEL_L)
    if not bestL then
      -- petit écran : sans la réserve du centre (nos cadres déjà placés restent évités)
      local g2 = newGrid(scan)
      for _, r in pairs(placed) do g2.mark(r, 6) end
      search(g2, PANEL_W, PANEL_L)
    end
    if bestL then
      local w, h = bw * k, heightFor(bl)
      side = (bestL + w / 2 > W / 2) and "RIGHT" or "LEFT"
      local pt, x, y = anchorFor(bestL, bestB, w, h, W, H, k, true)
      if pt == "TOP" then pt, x = (side == "RIGHT") and "TOPRIGHT" or "TOPLEFT",
        math.floor(((side == "RIGHT") and (bestL + w - W) or bestL) / k + 0.5) end
      c.point, c.x, c.y, c.manual = pt, x, y, nil
      P.width, P.maxLines = bw, bl
      placed.panel = { point = pt, x = x, y = y, l = bestL, b = bestB, w = w, h = h, width = bw, lines = bl }
      g.mark({ l = bestL, b = bestB, w = w, h = h }, 6)
      if panel then applyPosition(); self:RefreshPanel() end
    end
  end

  -- 5. cadre de suivi (s'il existe) : sous le suivi des quêtes, sinon côté opposé au panneau
  local TR, tf = moduleFrame("Tracker", "TreeAITrackerFrame")
  hookManual(tf, "tracker")
  if TR and TR.char and not keep("tracker") then
    local k = tonumber(TR.db and TR.db.scale) or 100
    if k > 3 then k = k / 100 end   -- le suivi garde son échelle en %
    local tw = (tonumber(TR.db and TR.db.width) or 300) * k
    local th = 160
    local qt = named.ObjectiveTrackerFrame or named.QuestWatchFrame or named.WatchFrame
      or named.Questie_BaseFrame or named.QuestieTrackerBaseFrame
    local tx, ty
    if qt then tx, ty = qt.l + qt.w / 2, qt.b - th / 2 - 8
    else tx, ty = (side == "RIGHT") and 170 or (W - 170), H * 0.6 end
    local l, b = bestSpot(g, tw, th, tx, ty)
    if l then
      local pt, x, y = anchorFor(l, b, tw, th, W, H, k, true)
      TR.char.point, TR.char.x, TR.char.y = pt, x, y
      if tf then call(tf, "ClearAllPoints"); call(tf, "SetPoint", pt, _G.UIParent, pt, x, y) end
      placed.tracker = { point = pt, x = x, y = y, l = l, b = b, w = tw, h = th }
      g.mark({ l = l, b = b, w = tw, h = th }, 4)
    end
  end

  -- 6. barres du chasseur (tir auto, pièges) : au-dessus des barres d'action, au centre
  local bars = { { "Hunter", "barPos", 160, 10, "map" }, { "HunterPro", "trapPos", 170, 12, "list" } }
  local floorY = 0
  for _, n in ipairs({ "MainMenuBar", "MainActionBar", "MultiBarBottomLeft", "MultiBarBottomRight", "ElvUI_Bar1", "BT4Bar1" }) do
    local r = named[n]
    if r and r.l < W * 0.75 and r.l + r.w > W * 0.25 then floorY = math.max(floorY, r.b + r.h) end
  end
  for _, bd in ipairs(bars) do
    local m = self.modules and self.modules[bd[1]]
    if m and m._active and type(m.db) == "table" and not keep(bd[2]) then
      local l, b = bestSpot(g, bd[3], bd[4] + 6, W / 2, floorY + 110)
      if l then
        local x, y = math.floor(l + bd[3] / 2 - W / 2 + 0.5), math.floor(b + bd[4] / 2 - H / 2 + 0.5)
        if bd[5] == "map" then m.db[bd[2]] = { point = "CENTER", x = x, y = y }
        else m.db[bd[2]] = { "CENTER", x, y } end
        placed[bd[2]] = { point = "CENTER", x = x, y = y, l = l, b = b, w = bd[3], h = bd[4] }
        g.mark({ l = l, b = b, w = bd[3], h = bd[4] + 6 }, 2)
      end
    end
  end

  st.sig = self:LayoutSignature()
  st.last = placed
  self._lastLayout = placed
  self:Fire("LAYOUT_DONE", placed)
  return placed
end

function TreeAI:LayoutSignature()
  local W, H = screenSize()
  local sc = call(_G.UIParent, "GetEffectiveScale") or 1
  return ("%dx%d@%.2f"):format(math.floor(W + 0.5), math.floor(H + 0.5), sc)
end

-- premier lancement (rien de placé) ou résolution / échelle changée : placement auto (places manuelles gardées)
function TreeAI:CheckAutoLayout()
  local st = layoutState()
  if st.sig == self:LayoutSignature() and (tonumber(st.v) or 0) >= LAYOUT_V then return false end
  -- nouvelle version du placement : refait UNE fois (les cadres déplacés à la main gardent leur place)
  if (tonumber(st.v) or 0) < LAYOUT_V then
    local P = self.settings and self.settings.panel
    if P and (tonumber(P.width) or 0) < MIN_W then P.width = DEFAULT_W end
  end
  local placed = self:AutoLayout(false)
  if placed then st.v = LAYOUT_V end
  return true
end



do
  local owner = {}
  local pending = false
  local function later()
    if pending then return end
    pending = true
    TreeAI:After(1, function() pending = false; TreeAI:CheckAutoLayout() end)
  end
  for _, ev in ipairs({ "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED" }) do TreeAI:RegisterEvent(ev, owner, later) end
  TreeAI:On("READY", owner, function() TreeAI:After(6, function() TreeAI:CheckAutoLayout() end) end)
end

TreeAI:RegisterSlash("placer", function(rest)
  rest = tostring(rest or ""):lower()
  local placed = TreeAI:AutoLayout(rest ~= "doux")
  if not placed then TreeAI:Print(L["Placement après le combat."]); return end
  local n = 0
  for _ in pairs(placed) do n = n + 1 end
  TreeAI:Print(L["Cadres TreeAI placés dans les zones libres"] .. " (" .. n .. ").  " .. L["/tai placer doux : garde ceux que tu as déplacés"])
end, "placer automatiquement les cadres TreeAI dans les zones libres de l'écran")

---------------------------------------------------------------------------
-- Cadres déplaçables + mode édition (/tai edit, bouton du panneau).
-- TreeAI:RegisterMovable(frame, id, label, opts) : chaque module y inscrit ses cadres.
--   opts = { onMoved = function(point, x, y, scale) end,  -- le module enregistre sa position (sinon TreeAI)
--            onReset = function() end,                    -- retour à la place par défaut / auto
--            minScale = 0.5, maxScale = 2, show = function() end, hide = function() end }
-- Positions manuelles gardées PAR PERSONNAGE (CharDB().movers) ; elles passent avant le placement auto.
---------------------------------------------------------------------------
local movables, moverOrder = {}, {}
local editing, editBar = false, nil
local SNAP, EDGE, NEAR = 10, 12, 8

local function moverDB()
  if not (TreeAI.db and TreeAI:CharKey() ~= "?") then
    TreeAI._moverTmp = TreeAI._moverTmp or {}
    return TreeAI._moverTmp
  end
  local c = TreeAI:CharDB()
  c.movers = type(c.movers) == "table" and c.movers or {}
  return c.movers
end

local function placeFrame(f, point, x, y, scale)
  if not f then return end
  if scale then call(f, "SetScale", scale) end
  call(f, "ClearAllPoints")
  call(f, "SetPoint", point, _G.UIParent, point, x, y)
end

function TreeAI:RegisterMovable(frame, id, label, opts)
  if type(frame) ~= "table" or type(id) ~= "string" then return false end
  opts = opts or {}
  local m = movables[id]
  if not m then moverOrder[#moverOrder + 1] = id; m = {}; movables[id] = m end
  m.frame, m.id, m.label, m.opts = frame, id, label or id, opts
  if frame.SetMovable then pcall(frame.SetMovable, frame, true) end
  if frame.SetClampedToScreen then pcall(frame.SetClampedToScreen, frame, true) end   -- jamais hors écran
  local saved = moverDB()[id]
  if type(saved) == "table" and saved.point and not opts.onMoved then
    placeFrame(frame, saved.point, saved.x or 0, saved.y or 0, saved.scale)
  end
  hookManual(frame, id)
  if editing and self._BuildOverlay then self._BuildOverlay(m) end
  return true
end
function TreeAI:GetMovables() return movables, moverOrder end

local function ourRect(f)
  local l, b, w, h = call(f, "GetRect")
  if not l then return nil end
  local k = (call(f, "GetEffectiveScale") or 1) / (call(_G.UIParent, "GetEffectiveScale") or 1)
  return l * k, b * k, w * k, h * k
end

-- aimantation : grille de 10, bords de l'écran, bords des autres cadres TreeAI
function TreeAI.SnapRect(l, b, w, h, W, H, others)
  l, b = math.floor(l / SNAP + 0.5) * SNAP, math.floor(b / SNAP + 0.5) * SNAP
  if l < EDGE then l = 0 elseif W - (l + w) < EDGE then l = W - w end
  if b < EDGE then b = 0 elseif H - (b + h) < EDGE then b = H - h end
  for _, o in ipairs(others or {}) do
    if math.abs(l - o.l) < NEAR then l = o.l elseif math.abs(l - (o.l + o.w)) < NEAR then l = o.l + o.w
    elseif math.abs((l + w) - o.l) < NEAR then l = o.l - w end
    if math.abs(b - o.b) < NEAR then b = o.b elseif math.abs(b - (o.b + o.h)) < NEAR then b = o.b + o.h
    elseif math.abs((b + h) - o.b) < NEAR then b = o.b - h end
  end
  return l, b
end

--- Position manuelle (après glisser / molette) : aimant, ancrage, sauvegarde ; passe avant le placement auto.
function TreeAI:CommitMove(id, l, b, scale)
  local m = movables[id]
  if not (m and m.frame) then return nil end
  local f = m.frame
  local W, H = screenSize()
  local _, _, w, h = ourRect(f)
  if not w then
    local k0 = scale or call(f, "GetScale") or 1
    w, h = (call(f, "GetWidth") or 100) * k0, (call(f, "GetHeight") or 20) * k0
  end
  w, h = w or 100, h or 20
  local others = {}
  for _, oid in ipairs(moverOrder) do
    local o = movables[oid]
    if oid ~= id and o.frame and call(o.frame, "IsShown") then
      local ol, ob, ow, oh = ourRect(o.frame)
      if ol then others[#others + 1] = { l = ol, b = ob, w = ow, h = oh } end
    end
  end
  l, b = TreeAI.SnapRect(l, b, w, h, W, H, others)
  -- déposé SUR un cadre de Blizzard / d'un addon (plus d'un quart caché) : glisse vers la place libre la plus
  -- proche (≤ 300 px) ; sinon la place du joueur est gardée telle quelle
  local S = self.settings
  if S and S.snapFree ~= false and TreeAI._LightRects then
    local area = 0
    for _, r in ipairs(TreeAI._LightRects(W, H)) do
      local ox = math.min(r.l + r.w, l + w) - math.max(r.l, l)
      local oy = math.min(r.b + r.h, b + h) - math.max(r.b, b)
      if ox > 0 and oy > 0 then area = area + ox * oy end
    end
    if area > 0.25 * w * h then
      local g = newGrid(self:ScanScreen())
      for _, o in ipairs(others) do g.mark(o, 2) end
      local nl, nb = bestSpot(g, w, h, l + w / 2, b + h / 2)
      if nl and math.abs(nl - l) + math.abs(nb - b) <= 300 then l, b = nl, nb end
    end
  end
  local k = scale or call(f, "GetScale") or 1
  local point, x, y = anchorFor(l, b, w, h, W, H, k, id == "panel")
  placeFrame(f, point, x, y, scale)
  local rec = { point = point, x = x, y = y, scale = k, l = l, b = b, zone = TreeAI.ZoneOf(l, b, w, h, W, H) }
  moverDB()[id] = rec
  layoutState().manual[id] = true
  if self._AcceptOverlaps then self:_AcceptOverlaps(id, l, b, w, h) end
  if m.opts.onMoved then TreeAI.safecall(m.opts.onMoved, point, x, y, k) end
  return rec
end

--- Remet un cadre à sa place automatique (ou par défaut) ; id nil = tous.
function TreeAI:ResetMovable(id)
  local db, st = moverDB(), layoutState()
  local list = id and { id } or moverOrder
  for _, i in ipairs(list) do
    db[i] = nil
    st.manual[i] = nil
    local m = movables[i]
    if m and m.opts.onReset then TreeAI.safecall(m.opts.onReset) end
  end
  if not self:InCombat() then self:AutoLayout(false) end
  if self._RefreshOverlays then self._RefreshOverlays() end
end

function TreeAI:SetFramesLocked(v)
  local S = self.settings
  if S and S.panel then S.panel.locked = v and true or false end
  local C, TR = self.modules.Compass, self.modules.Tracker
  if C and C.db then C.db.locked = v and true or false end
  if TR and TR.db then TR.db.lock = v and true or false end
  if self._RefreshOverlays then self._RefreshOverlays() end
  self:RequestRefresh()
end
function TreeAI:FramesLocked()
  local S = self.settings
  return (S and S.panel and S.panel.locked) and true or false
end

local overlays = {}
local function refreshOverlay(o)
  local m = o.mover
  local locked = TreeAI:FramesLocked()
  o:ClearAllPoints()
  if m.frame then o:SetAllPoints(m.frame) end
  if o.SetBackdropColor then
    if locked then o:SetBackdropColor(0.4, 0.4, 0.4, 0.35) else o:SetBackdropColor(0.1, 0.6, 1, 0.35) end
  end
  local sc = call(m.frame, "GetScale") or 1
  o.text:SetText(("%s%s  |cffaaaaaa×%.2f|r"):format(L[m.label], locked and (" (" .. L["verrouillé"] .. ")") or "", sc))
end

local function buildOverlay(m)
  local o = overlays[m.id]
  if not o then
    local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
    o = _G.CreateFrame("Button", nil, _G.UIParent, template)
    o:SetFrameStrata("DIALOG")
    o:SetMovable(true)
    o:EnableMouse(true)
    if o.EnableMouseWheel then o:EnableMouseWheel(true) end
    o:RegisterForDrag("LeftButton")
    o:RegisterForClicks("RightButtonUp")
    if o.SetBackdrop then
      o:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Buttons\\WHITE8X8", edgeSize = 1 })
      o:SetBackdropBorderColor(0.2, 0.8, 1, 0.9)
    end
    o.text = o:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    o.text:SetPoint("CENTER", o, "CENTER", 0, 0)
    o:SetScript("OnDragStart", function(self)
      if TreeAI:FramesLocked() or TreeAI:InCombat() then return end
      local l, b, w, h = ourRect(self)
      self:ClearAllPoints()
      if l then self:SetPoint("BOTTOMLEFT", _G.UIParent, "BOTTOMLEFT", l, b); self:SetSize(w, h) end
      self:StartMoving()
    end)
    o:SetScript("OnDragStop", function(self)
      self:StopMovingOrSizing()
      local l, b = ourRect(self)
      if l then TreeAI:CommitMove(self.mover.id, l, b) end
      refreshOverlay(self)
    end)
    o:SetScript("OnMouseWheel", function(self, delta)
      if TreeAI:FramesLocked() or TreeAI:InCombat() then return end
      local mv = self.mover
      local sc = call(mv.frame, "GetScale") or 1
      sc = math.max(mv.opts.minScale or 0.5, math.min(mv.opts.maxScale or 2, sc + (delta > 0 and 0.05 or -0.05)))
      sc = math.floor(sc * 100 + 0.5) / 100
      local l, b = ourRect(mv.frame)
      call(mv.frame, "SetScale", sc)
      if l then TreeAI:CommitMove(mv.id, l, b, sc) end
      refreshOverlay(self)
    end)
    o:SetScript("OnClick", function(self, button)
      if button == "RightButton" then TreeAI:ResetMovable(self.mover.id) end
    end)
    o:SetScript("OnEnter", function(self)
      showTooltip(self, L[self.mover.label], {
        { L["Glisser : déplacer (aimanté)"], 0.8, 0.8, 0.8 }, { L["Molette : taille"], 0.8, 0.8, 0.8 },
        { L["Clic droit : remettre à sa place auto"], 0.8, 0.8, 0.8 } })
    end)
    o:SetScript("OnLeave", hideTooltip)
    overlays[m.id] = o
  end
  o.mover = m
  refreshOverlay(o)
  o:Show()
  return o
end
TreeAI._BuildOverlay = buildOverlay
TreeAI._RefreshOverlays = function()
  if not editing then return end
  for _, o in pairs(overlays) do if o:IsShown() then refreshOverlay(o) end end
  if editBar and editBar.refresh then editBar.refresh() end
end
function TreeAI:GetOverlays() return overlays end

local function barButton(parent, text, x, fn)
  local ok, b = pcall(_G.CreateFrame, "Button", nil, parent, "UIPanelButtonTemplate")
  if not ok or not b then
    -- (3.9.2) template missing: plain button with its own text, so « Fini » always exists
    ok, b = pcall(_G.CreateFrame, "Button", nil, parent)
    if not ok or not b then return nil end
    local fs = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    fs:SetAllPoints()
    b.SetText = function(self, v) fs:SetText(v) end
    if b.SetNormalFontObject then pcall(b.SetNormalFontObject, b, "GameFontNormalSmall") end
  end
  b:SetSize(118, 20)
  b:SetPoint("LEFT", parent, "LEFT", x, 0)
  b:SetText(text)
  b:SetScript("OnClick", function() TreeAI.safecall(fn) end)
  return b
end

local function buildEditBar()
  if editBar then return editBar end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  editBar = _G.CreateFrame("Frame", "TreeAIEditBar", _G.UIParent, template)
  editBar:SetClampedToScreen(true)
  editBar:SetSize(112 + 122 * 4, 30)
  -- (3.9.2) below the top-centre compass and ABOVE every overlay: the compass overlay used to cover « Fini »
  editBar:SetPoint("TOP", _G.UIParent, "TOP", 0, -150)
  editBar:SetFrameStrata("FULLSCREEN_DIALOG")
  if editBar.SetFrameLevel then editBar:SetFrameLevel(200) end
  if editBar.SetToplevel then editBar:SetToplevel(true) end
  editBar:SetMovable(true); editBar:EnableMouse(true)
  editBar:RegisterForDrag("LeftButton")
  editBar:SetScript("OnDragStart", function(f) f:StartMoving() end)
  editBar:SetScript("OnDragStop", function(f) f:StopMovingOrSizing() end)
  -- Échap closes the edit mode (our frame only) ; hiding the bar for any reason ends the mode
  editBar:SetScript("OnHide", function() if editing then TreeAI:SetEditMode(false) end end)
  if type(_G.UISpecialFrames) == "table" then table.insert(_G.UISpecialFrames, "TreeAIEditBar") end
  if editBar.SetBackdrop then
    editBar:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 10,
      insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    editBar:SetBackdropColor(0.05, 0.08, 0.06, 0.95)
  end
  local t = editBar:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  t:SetPoint("LEFT", editBar, "LEFT", 8, 0)
  t:SetText("|cff33cc66TreeAI|r " .. L["édition"])
  editBar.lock = barButton(editBar, "", 112, function() TreeAI:SetFramesLocked(not TreeAI:FramesLocked()) end)
  editBar.auto = barButton(editBar, L["Tout remettre auto"], 112 + 122, function() TreeAI:ResetMovable(nil) end)
  editBar.help = barButton(editBar, L["Comment ?"], 112 + 244, function()
    TreeAI:Print(L["Glisse un cadre (aimant : grille 10 px, bords, autres cadres), molette = taille, clic droit = place auto."])
  end)
  editBar.done = barButton(editBar, L["Fini"], 112 + 366, function() TreeAI:SetEditMode(false) end)
  -- Échap = « Fini » (UISpecialFrames cache la barre -> on quitte le mode édition)
  if TreeAI.Theme and TreeAI.Theme.Window then
    TreeAI.Theme:Window(editBar, { id = "editbar", header = false })
  end
  editBar:SetScript("OnHide", function() if editing then TreeAI:SetEditMode(false) end end)
  function editBar.refresh()
    if editBar.lock then editBar.lock:SetText(TreeAI:FramesLocked() and L["Déverrouiller tout"] or L["Verrouiller tout"]) end
  end
  return editBar
end

--- Mode édition : calque coloré + nom sur chaque cadre TreeAI ; glisser, molette, clic droit.
function TreeAI:SetEditMode(on)
  if on and self:InCombat() then self:Print(L["Impossible en combat."]); return false end
  editing = on and true or false
  if editing then
    if self._RegisterCoreMovables then self._RegisterCoreMovables() end
    if not panel then self:CreatePanel() end
    if panel and not panel:IsShown() then self:ShowPanel() end
    if self.ShowAlert then self:ShowAlert({ text = L["Encadré d'alerte (exemple)"], prio = 3 }, true) end
    if alert then alert.hold = true end
    for _, id in ipairs(moverOrder) do
      local m = movables[id]
      if m.opts.show then self.safecall(m.opts.show) end
      if m.frame and call(m.frame, "IsShown") then buildOverlay(m) end
    end
    buildEditBar()
    editBar.refresh()
    editBar:Show()
  else
    for _, o in pairs(overlays) do o:Hide() end
    if editBar then editBar:Hide() end
    if alert then alert.hold = false; alert:Hide() end
    for _, id in ipairs(moverOrder) do
      local m = movables[id]
      if m.opts.hide then self.safecall(m.opts.hide) end
    end
  end
  self:Fire("EDIT_MODE", editing)
  return true
end
function TreeAI:IsEditMode() return editing end

TreeAI:RegisterSlash("edit", function() TreeAI:SetEditMode(not editing) end,
  "mode édition : déplacer / agrandir les cadres TreeAI (aimantés), verrouiller, remettre en place")
TreeAI:RegisterSlash("editer", function() TreeAI:SetEditMode(not editing) end)

-- cadres de base : panneau, encadré d'alerte ; boussole et suivi dès qu'ils existent
local function registerCore()
  if panel and not movables.panel then
    TreeAI:RegisterMovable(panel, "panel", "Panneau TreeAI", {
      onMoved = function(point, x, y, sc)
        local c, s = char(), acc()
        if c then c.point, c.x, c.y, c.manual = point, x, y, true end
        if s and sc then s.scale = sc end
      end,
    })
  end
  if not movables.alert and (alert or makeAlert()) then
    TreeAI:RegisterMovable(alert, "alert", "Encadré d'alerte", {
      onMoved = function(point, x, y, sc)
        local s = alertSettings()
        if s then s.pos = { point = point, x = x, y = y, corner = s.corner }; if sc then s.scale = sc end end
      end,
      onReset = function() local s = alertSettings(); if s then s.pos = nil end end,
    })
  end
  local C, cf = moduleFrame("Compass", "TreeAICompassFrame")
  if C and cf and not movables.compass then
    TreeAI:RegisterMovable(cf, "compass", "Boussole", {
      onMoved = function(point, x, y, sc)
        if C.char then C.char.point, C.char.x, C.char.y = point, x, y end
        if C.db and sc then C.db.scale = sc end
      end,
    })
  end
  local TR, tf = moduleFrame("Tracker", "TreeAITrackerFrame")
  if TR and tf and not movables.tracker then
    TreeAI:RegisterMovable(tf, "tracker", "Suivi des quêtes", {
      onMoved = function(point, x, y, sc)
        if TR.char then TR.char.point, TR.char.x, TR.char.y = point, x, y end
        if TR.db and sc then TR.db.scale = math.floor(sc * 100 + 0.5) end
      end,
    })
  end
end
TreeAI._RegisterCoreMovables = registerCore
do
  local owner = {}
  TreeAI:On("READY", owner, function() TreeAI:After(1, registerCore) end)
end

--- « Réinitialiser l'interface » : tous les cadres TreeAI reviennent à leur place automatique, tailles,
--- onglets et transparence par défaut, panneau affiché sur « Maintenant ». Hors combat.
function TreeAI:ResetInterface()
  if self:InCombat() then
    self:RunOutOfCombat("resetui", function() self:ResetInterface() end)
    self:Print(L["Réinitialisation après le combat."])
    return false
  end
  local db = moverDB()
  for k in pairs(db) do db[k] = nil end
  if self.ResetReflow then self:ResetReflow() end
  for k in pairs(self._accepted or {}) do self._accepted[k] = nil end
  local st = layoutState()
  st.manual = {}
  local s, c = acc(), char()
  if s then
    s.scale, s.alpha, s.width, s.maxLines, s.locked, s.shown = 1, 0.9, DEFAULT_W, 8, false, true
    s.fontSize, s.lineGap, s.iconSize = 11, ROW_GAP, 13
  end
  if c then c.tab, c.mini, c.manual = "now", false, nil end
  local al = alertSettings()
  if al then al.pos, al.scale, al.corner = nil, 1, "TOPRIGHT" end
  moreOpen = false
  for _, id in ipairs(moverOrder) do
    local m = movables[id]
    if m and m.opts.onReset then self.safecall(m.opts.onReset) end
    if m and m.frame then call(m.frame, "SetScale", 1) end
  end
  local mods = self.modules or {}
  if mods.Compass and mods.Compass.db then mods.Compass.db.scale = 1 end
  if mods.Radar and mods.Radar.db then mods.Radar.db.size, mods.Radar.db.compact = 170, false end
  if editing then self:SetEditMode(false) end
  self:ShowPanel()
  self:AutoLayout(true)
  st.v = LAYOUT_V
  self:Print(L["Interface TreeAI réinitialisée : chaque cadre est à sa place automatique."])
  return true
end

TreeAI:RegisterSlash("reinit", function() TreeAI:ResetInterface() end,
  "réinitialiser l'interface TreeAI (places, tailles, onglets par défaut)")

---------------------------------------------------------------------------
-- UI2 : placement vivant. Quand un cadre de Blizzard / d'un addon apparaît, grandit ou bouge (le suivi de
-- Questie s'allonge, un cadre de groupe arrive…) et tombe sur un cadre TreeAI, celui-ci glisse vers la place
-- libre la plus proche ; il revient chez lui dès que la place se libère. Les chevauchements acceptés par le
-- joueur (il a déposé le cadre là) restent acceptés tant que l'obstacle ne change pas. Lecture seule des
-- cadres des autres, hors combat, 1 passage léger / 2 s (cadres connus seulement, pas d'EnumerateFrames).
---------------------------------------------------------------------------
do
  local displaced = {}       -- id -> { point, rp, x, y, l, b } : la vraie place (« chez lui »)
  local accepted = {}        -- id -> { [nom] = signature } : chevauchements voulus par le joueur
  local lastSig
  TreeAI._displaced, TreeAI._accepted = displaced, accepted

  -- 1er point d'ancrage (5 valeurs : `call` n'en rend que 4)
  local function pointOf(f)
    if type(f) ~= "table" or type(f.GetPoint) ~= "function" then return nil end
    local ok, pt, rel, rp, x, y = pcall(f.GetPoint, f, 1)
    if ok then return pt, rel, rp, x, y end
  end
  -- rectangle d'un cadre à nous (GetRect, sinon depuis son point d'ancrage sur UIParent)
  local function frameRect(f, W, H)
    local l, b, w, h = ourRect(f)
    if l and w and w > 0 then return l, b, w, h end
    local k = call(f, "GetScale") or 1
    w, h = (call(f, "GetWidth") or 0) * k, (call(f, "GetHeight") or 0) * k
    if w <= 0 or h <= 0 then return nil end
    local pt, rel, rp, x, y = pointOf(f)
    if not pt or (rel and rel ~= _G.UIParent) then return nil end
    rp = rp or pt
    x, y = (x or 0) * k, (y or 0) * k
    local ax = (rp:find("LEFT") and 0) or (rp:find("RIGHT") and W) or W / 2
    local ay = (rp:find("TOP") and H) or (rp:find("BOTTOM") and 0) or H / 2
    local ox = (pt:find("LEFT") and 0) or (pt:find("RIGHT") and w) or w / 2
    local oy = (pt:find("TOP") and h) or (pt:find("BOTTOM") and 0) or h / 2
    return ax + x - ox, ay + y - oy, w, h
  end
  TreeAI._FrameRect = frameRect

  local function rsig(r) return ("%d,%d,%d,%d"):format(math.floor(r.l / 16), math.floor(r.b / 16), math.floor(r.w / 16), math.floor(r.h / 16)) end
  local function overlap(a, l, b, w, h)
    local ox = math.min(a.l + a.w, l + w) - math.max(a.l, l)
    local oy = math.min(a.b + a.h, b + h) - math.max(a.b, b)
    if ox <= 4 or oy <= 4 then return 0 end
    return ox * oy
  end

  -- passage léger : cadres connus par leur nom (suivis, groupe, compteurs, minimap, barres…)
  local function lightRects(W, H)
    local out = {}
    local seen = {}
    local function one(n)
      local f = _G[n]
      if type(f) == "table" and not seen[f] then
        seen[f] = true
        local r = guideRect(f, W, H, n)
        if r then r.name = r.name or n; out[#out + 1] = r end
      end
    end
    for _, n in ipairs(SCAN_NAMES) do one(n) end
    for n in pairs(TreeAI._discovered) do one(n) end   -- (COHERE) suivis d'addons vus par préfixe
    return out
  end
  TreeAI._LightRects = lightRects

  --- Obstacles (nouveaux ou changés) sur le cadre `id` placé en l, b, w, h.
  local function blockers(id, rects, l, b, w, h)
    local hits, area = {}, 0
    local acc = accepted[id] or {}
    for _, r in ipairs(rects) do
      local a = overlap(r, l, b, w, h)
      if a > 0 and acc[r.name or "?"] ~= rsig(r) then hits[#hits + 1] = r; area = area + a end
    end
    return hits, area
  end

  --- Le joueur dépose un cadre : ce qu'il recouvre déjà est accepté (sa décision), la zone est apprise.
  function TreeAI:_AcceptOverlaps(id, l, b, w, h)
    local W, H = screenSize()
    local acc = {}
    for _, r in ipairs(lightRects(W, H)) do
      if overlap(r, l, b, w, h) > 0 then acc[r.name or "?"] = rsig(r) end
    end
    accepted[id] = acc
    displaced[id] = nil
  end

  local function shownMovers(skip, W, H)
    local out = {}
    for _, oid in ipairs(moverOrder) do
      local o = movables[oid]
      if oid ~= skip and o and o.frame and call(o.frame, "IsShown") then
        local l, b, w, h = frameRect(o.frame, W, H)
        if l then out[#out + 1] = { id = oid, l = l, b = b, w = w, h = h } end
      end
    end
    return out
  end

  --- Un passage : renvoie { id = { l, b } | { home = true } } des cadres déplacés (nil = rien à faire).
  function TreeAI:Reflow(force)
    local S = self.settings
    if not S or S.reflow == false or editing then return nil end
    if self:InCombat() then return nil end
    local W, H = screenSize()
    local rects = lightRects(W, H)
    local parts = {}
    for _, r in ipairs(rects) do parts[#parts + 1] = tostring(r.name) .. "=" .. rsig(r) end
    for _, o in ipairs(shownMovers(nil, W, H)) do parts[#parts + 1] = "@" .. o.id .. "=" .. rsig(o) end
    table.sort(parts)
    local sig = table.concat(parts, ";")
    if sig == lastSig and not force then return nil end
    lastSig = sig
    local moved, grid = {}, nil
    for _, id in ipairs(moverOrder) do
      local m = movables[id]
      local f = m and m.frame
      if id ~= "alert" and f and call(f, "IsShown") and not (m.opts and m.opts.noReflow) then
        local l, b, w, h = frameRect(f, W, H)
        if l then
          local home = displaced[id]
          local hl, hb = l, b
          if home then hl, hb = home.l, home.b end
          local hits = blockers(id, rects, hl, hb, w, h)
          if #hits > 0 then
            if not grid then
              grid = newGrid(self:ScanScreen())
              for _, r in ipairs(TreeAI.ReservedRects(W, H)) do grid.mark(r, 0) end
            end
            -- nos autres cadres visibles comptent aussi (on ne glisse pas sur un voisin)
            local g = grid
            for _, o in ipairs(shownMovers(id, W, H)) do g.mark(o, 4) end
            local nl, nb = bestSpot(g, w, h, hl + w / 2, hb + h / 2)
            if nl and (math.abs(nl - l) > 2 or math.abs(nb - b) > 2) then
              if not home then
                local pt, _, rp, x, y = pointOf(f)
                displaced[id] = { point = pt or "CENTER", rp = rp or pt or "CENTER", x = x or 0, y = y or 0, l = l, b = b }
              end
              local k = call(f, "GetScale") or 1
              local pt, x, y = anchorFor(nl, nb, w, h, W, H, k, id == "panel")
              placeFrame(f, pt, x, y)
              moved[id] = { l = nl, b = nb, w = w, h = h }
            end
            grid = nil   -- les marques de ce cadre ne doivent pas gêner le suivant
          elseif home then
            call(f, "ClearAllPoints")
            call(f, "SetPoint", home.point, _G.UIParent, home.rp, home.x, home.y)
            displaced[id] = nil
            moved[id] = { home = true, l = home.l, b = home.b, w = w, h = h }
          end
        end
      end
    end
    if not next(moved) then return nil end
    self:Fire("LAYOUT_REFLOW", moved)
    return moved
  end
  function TreeAI:IsDisplaced(id) return displaced[id] ~= nil end
  function TreeAI:ResetReflow() for k in pairs(displaced) do displaced[k] = nil end; lastSig = nil end

  local owner = {}
  TreeAI:On("READY", owner, function()
    TreeAI:NewTicker(5, function()   -- (LITE) 5 s : la place des cadres change rarement
      local S = TreeAI.settings
      if S and S.reflow ~= false and not TreeAI:InCombat() then TreeAI.safecall(TreeAI.Reflow, TreeAI) end
    end)
  end)
end

--- Disposition en un clic : "compacte" | "confort" | "coins". Tailles + placement auto refait (hors combat).
function TreeAI:ApplyDisposition(name)
  local D = self.DISPOSITIONS[name or ""]
  if not D then return false end
  if self:InCombat() then
    self:RunOutOfCombat("dispo", function() self:ApplyDisposition(name) end)
    self:Print(L["Disposition après le combat."])
    return false
  end
  local S = self.settings
  if not S then return false end
  S.layoutStyle = name
  local P = S.panel
  if P then P.width, P.maxLines, P.scale = D.width, D.lines, D.scale end
  if S.alert then S.alert.scale = D.alertScale end
  local R = self.modules and self.modules.Radar
  if R and R.db then R.db.size = D.radar end
  local db = moverDB()
  for k in pairs(db) do db[k] = nil end
  if self.ResetReflow then self:ResetReflow() end
  for k in pairs(self._accepted or {}) do self._accepted[k] = nil end
  for _, id in ipairs(moverOrder) do
    local m = movables[id]
    if m and m.opts.onReset then self.safecall(m.opts.onReset) end
  end
  if panel then applyPosition() end
  local placed = self:AutoLayout(true)
  if panel then self:RefreshPanel() end
  self:Fire("DISPOSITION", name)
  return placed and true or false, placed
end
function TreeAI:GetDisposition() return self._Dispo() end

TreeAI:RegisterSlash("disposition", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local map = { compact = "compacte", compacte = "compacte", confort = "confort", coins = "coins", coin = "coins" }
  local n = map[rest]
  if not n then
    TreeAI:Print(L["Disposition"] .. " : " .. L[TreeAI.DISPOSITIONS[TreeAI:GetDisposition()].label]
      .. "  (/tai disposition compacte|confort|coins)")
    return
  end
  TreeAI:ApplyDisposition(n)
  TreeAI:Print(L["Disposition"] .. " : " .. L[TreeAI.DISPOSITIONS[n].label])
end, "disposition des cadres en un clic : compacte, confort ou coins")
