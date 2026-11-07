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
local MAX_ROWS = 15
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
  panel:SetWidth(s.width or 280)
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
    if isSuggestion(e) then TreeAI:ClearSuggestion(e.id) end
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
  b.icon:SetPoint("LEFT", b, "LEFT", 0, 0)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("LEFT", b.icon, "RIGHT", 3, 0)
  b.text:SetPoint("RIGHT", b, "RIGHT", 0, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
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

local function buildTabs()
  for _, t in ipairs(TreeAI:GetTabs()) do
    if not tabButtons[t.id] then
      local b = _G.CreateFrame("Button", nil, panel)
      b:SetHeight(TABS_H)
      draggable(b)
      b.text = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
      b.text:SetPoint("CENTER", b, "CENTER", 0, 0)
      b.line = b:CreateTexture(nil, "ARTWORK")
      b.line:SetHeight(2)
      b.line:SetPoint("BOTTOMLEFT", b, "BOTTOMLEFT", 2, 0)
      b.line:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -2, 0)
      b.line:SetTexture("Interface\\Buttons\\WHITE8X8")
      if b.line.SetVertexColor then b.line:SetVertexColor(0.2, 0.8, 0.4, 0.9) end
      b.id = t.id
      b:SetScript("OnClick", function(self) TreeAI:SelectTab(self.id) end)
      b:SetScript("OnEnter", function(self)
        local t2 = TreeAI.tabs[self.id]
        showTooltip(self, t2 and t2.title or self.id, { { L["Molette : faire défiler"], 0.6, 0.6, 0.6 } })
      end)
      b:SetScript("OnLeave", hideTooltip)
      tabButtons[t.id] = b
    end
  end
end

local function layoutTabs()
  local s = acc()
  local width = (s and s.width or 280) - 2 * PAD
  local list = TreeAI:GetTabs()
  local total = 0
  for _, t in ipairs(list) do
    local b = tabButtons[t.id]
    if b then
      b.text:SetText(t.title)
      b.w = (b.text:GetStringWidth() or 30) + 8
      total = total + b.w
    end
  end
  local k = total > 0 and width / total or 1
  local x = PAD
  for _, t in ipairs(list) do
    local b = tabButtons[t.id]
    if b then
      local w = floor(b.w * k)
      b:ClearAllPoints()
      b:SetPoint("TOPLEFT", panel, "TOPLEFT", x, -HEADER_H)
      b:SetWidth(w)
      x = x + w
    end
  end
end

function TreeAI:CreatePanel()
  if panel or not _G.CreateFrame then return panel end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  panel = _G.CreateFrame("Frame", "TreeAIPanel", _G.UIParent, template)
  panel:SetSize(280, 40)
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
  toolButton("group", L["Groupe"], function() TreeAI:SelectTab("group") end)
  toolButton("macros", L["Macros (clic : prendre une macro)"], toggleMacros)
  toolButton("edit", L["Déplacer les cadres (mode édition)"], function() TreeAI:SetEditMode(not TreeAI:IsEditMode()) end)
  toolButton("all", L["Tout : toutes les actions des modules"], function() if TreeAI.ShowHelp then TreeAI:ShowHelp() end end)
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
  panel.more:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -PAD, 3)

  -- expire old suggestions even without new events
  TreeAI:NewTicker(1, function() if panel:IsShown() then TreeAI:RequestRefresh() end end)

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

function TreeAI:RefreshPanel()
  if not panel then return end
  local s = acc()
  local width = s and s.width or 280
  local mini = isMini()
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
    for i = 1, MAX_ROWS do rows[i].entry = nil; rows[i]:Hide() end
    panel.empty:Hide(); panel.thumb:Hide(); panel.more:Hide()
    panel:SetHeight(HEADER_H + 6)
    return
  end
  panel.miniText:Hide()
  panel.miniEntry = nil

  buildTabs()
  layoutTabs()
  local tab = activeTab()
  for id, b in pairs(tabButtons) do
    local list = self:GetTabLines(id)
    local urgent = false
    for _, e in ipairs(list) do if e.prio >= 3 then urgent = true break end end
    b:Show()
    if id == tab then b.text:SetTextColor(1, 1, 1); b.line:Show()
    elseif urgent then b.text:SetTextColor(1, 0.35, 0.3); b.line:Hide()
    elseif #list == 0 then b.text:SetTextColor(0.5, 0.5, 0.5); b.line:Hide()
    else b.text:SetTextColor(1, 0.82, 0); b.line:Hide() end
  end

  current = self:GetTabLines(tab)
  local maxRows = min(MAX_ROWS, max(3, (s and s.maxLines) or 8))
  local n = #current
  local first = scroll[tab] or 1
  if first > n - maxRows + 1 then first = n - maxRows + 1 end
  if first < 1 then first = 1 end
  scroll[tab] = first
  local top = -(HEADER_H + TABS_H + 2)
  local shown = 0
  for i = 1, MAX_ROWS do
    local b, e = rows[i], current[first + i - 1]
    if e and i <= maxRows then
      shown = shown + 1
      b.entry = e
      local c = PRIO_COLORS[e.prio] or PRIO_COLORS[1]
      b:ClearAllPoints()
      b:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, top - (i - 1) * LINE_H)
      b:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -PAD - 4, top - (i - 1) * LINE_H)
      b.text:SetText(tostring(e.text))
      b.text:SetTextColor(c[1], c[2], c[3])
      local ic = iconOf(e)
      if ic then b.icon:SetTexture(ic); b.icon:Show(); b.icon:SetWidth(13)
      else b.icon:Hide(); b.icon:SetWidth(1) end
      b:Show()
    else
      b.entry = nil
      b:Hide()
    end
  end
  if shown == 0 then
    panel.empty:ClearAllPoints()
    panel.empty:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, top - 1)
    panel.empty:SetText(L[EMPTY[tab] or "Rien à signaler."])
    panel.empty:Show()
  else
    panel.empty:Hide()
  end
  local bodyRows = shown > 0 and shown or 1
  panel:SetHeight(HEADER_H + TABS_H + 8 + LINE_H * bodyRows)
  if n > maxRows then
    local h = LINE_H * bodyRows
    local th = max(8, floor(h * maxRows / n))
    local off = floor((h - th) * (first - 1) / max(1, n - maxRows))
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

function TreeAI:ScrollTab(step)
  local tab = activeTab()
  scroll[tab] = (scroll[tab] or 1) + (step or 1)
  self:RefreshPanel()
end

function TreeAI:SelectTab(id)
  if not self.tabs[id] then return false end
  local c = char()
  if c then c.tab = id; c.mini = false end
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
  alert:SetAlpha((s and s.alpha) or 0.95)
  alert:Show()
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
for i = 1, 10 do SCAN_NAMES[#SCAN_NAMES + 1] = "ChatFrame" .. i end
TreeAI.LAYOUT_SCAN_NAMES = SCAN_NAMES
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
local function rectOf(f, W, H)
  if type(f) ~= "table" or isOurs(f) then return nil end
  if call(f, "IsForbidden") then return nil end
  if not call(f, "IsVisible") then return nil end
  local l, b, w, h = call(f, "GetRect")
  if not (l and b and w and h) or w < 12 or h < 8 then return nil end
  local k = (call(f, "GetEffectiveScale") or 1) / (call(_G.UIParent, "GetEffectiveScale") or 1)
  l, b, w, h = l * k, b * k, w * k, h * k
  if w >= W * 0.9 and h >= H * 0.6 then return nil end      -- plein écran (conteneurs, ElvUIParent…)
  if SKIP_STRATA[call(f, "GetFrameStrata") or ""] then return nil end
  local a = call(f, "GetEffectiveAlpha") or call(f, "GetAlpha") or 1
  if a < 0.05 then return nil end
  if l + w < 0 or b + h < 0 or l > W or b > H then return nil end
  return { l = l, b = b, w = w, h = h, name = call(f, "GetName") }
end

--- Scan lecture seule. Renvoie { W=, H=, rects = { {l,b,w,h,name}... }, named = { [name] = rect } }.
function TreeAI:ScanScreen()
  local W, H = screenSize()
  local rects, named, seen = {}, {}, {}
  local function add(f)
    if seen[f] then return end
    seen[f] = true
    local r = rectOf(f, W, H)
    if r then
      rects[#rects + 1] = r
      if r.name then named[r.name] = r end
    end
  end
  for _, n in ipairs(SCAN_NAMES) do
    local f = _G[n]
    if type(f) == "table" then add(f) end
  end
  if type(_G.EnumerateFrames) == "function" then
    local ok = pcall(function()
      local f, guard = _G.EnumerateFrames(), 0
      while f and guard < 20000 do
        guard = guard + 1
        if call(f, "GetParent") == _G.UIParent then add(f) end
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
  for _, r in ipairs(scan.rects) do g.mark(r, 4) end
  return g
end

-- meilleur emplacement libre pour un rectangle w×h (unités UI) près de (tx, ty) ; renvoie l, b, score
local function bestSpot(g, w, h, tx, ty)
  local cw, ch = math.ceil(w / CELL), math.ceil(h / CELL)
  if cw > g.cols or ch > g.rows then return nil end
  local best, bl, bb
  for y = 0, g.rows - ch do
    for x = 0, g.cols - cw do
      if g.used(x, y, cw, ch) == 0 then
        local cx, cy = (x + cw / 2) * CELL, (y + ch / 2) * CELL
        local d = math.sqrt((cx - tx) ^ 2 + (cy - ty) ^ 2)
        if not best or d < best then best, bl, bb = d, x * CELL, y * CELL end
      end
    end
  end
  return bl, bb, best
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

local PANEL_W = { 300, 280, 260, 240, 220 }
local PANEL_L = { 8, 6, 5, 4 }

--- Place nos cadres. force = oublie les déplacements manuels (bouton « Placer automatiquement »).
--- Renvoie la table des placements { role = { point, x, y, l, b, w, h } } (aussi gardée pour /tai placer).
function TreeAI:AutoLayout(force)
  if self:InCombat() then
    self:RunOutOfCombat("autolayout", function() self:AutoLayout(force) end)
    return nil, "combat"
  end
  local st = layoutState()
  if force then st.manual = {} end
  local scan = self:ScanScreen()
  local W, H = scan.W, scan.H
  local g = newGrid(scan)
  local S = self.settings or {}
  local placed = {}
  local named = scan.named
  local function keep(role) return st.manual[role] and not force end

  -- 1. encadré d'alerte : juste sous la minimap (sinon coin haut droit)
  local mm = named.MinimapCluster or named.Minimap or named.MMHolder or named.ElvUI_MinimapHolder
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

  -- 2. boussole : en haut au centre, jamais sur le cadre de la cible
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

  -- 3. panneau : le côté le plus libre, taille adaptée (largeur, lignes)
  local P = S.panel
  local c = char()
  local side
  if P and c and not keep("panel") then
    local k = P.scale or 1
    local r0, r1 = math.floor(g.rows * 0.2), math.floor(g.rows * 0.85)
    local third = math.floor(g.cols / 3)
    local fl, fr = g.freeIn(0, third, r0, r1), g.freeIn(g.cols - third, g.cols - 1, r0, r1)
    side = (fr >= fl) and "RIGHT" or "LEFT"
    local tx = side == "RIGHT" and (W - 160) or 160
    local ty = H * 0.62
    local bestL, bestB, bestScore, bw, bl
    for wi, pw in ipairs(PANEL_W) do
      for li, lines in ipairs(PANEL_L) do
        if pw <= (P.width or 280) + 20 then
          local w, h = pw * k, (HEADER_H + TABS_H + 8 + LINE_H * lines) * k
          local l, b, d = bestSpot(g, w, h, tx, ty)
          if l then
            local score = d + (wi - 1) * 25 + (li - 1) * 40     -- garder la taille si possible
            if not bestScore or score < bestScore then bestL, bestB, bestScore, bw, bl = l, b, score, pw, lines end
          end
        end
      end
    end
    if bestL then
      local w, h = bw * k, (HEADER_H + TABS_H + 8 + LINE_H * bl) * k
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

  -- 4. cadre de suivi (s'il existe) : sous le suivi des quêtes, sinon côté opposé au panneau
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

  -- 5. barres du chasseur (tir auto, pièges) : au-dessus des barres d'action, au centre
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
  if st.sig == self:LayoutSignature() then return false end
  self:AutoLayout(false)
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
  local k = scale or call(f, "GetScale") or 1
  local point, x, y = anchorFor(l, b, w, h, W, H, k, id == "panel")
  placeFrame(f, point, x, y, scale)
  local rec = { point = point, x = x, y = y, scale = k, l = l, b = b }
  moverDB()[id] = rec
  layoutState().manual[id] = true
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
  if not ok or not b then return nil end
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
  editBar:SetSize(112 + 122 * 4, 30)
  editBar:SetPoint("TOP", _G.UIParent, "TOP", 0, -8)
  editBar:SetFrameStrata("DIALOG")
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
