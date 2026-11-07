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
}

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
    else showTooltip(self, "TreeAI Coach", { { L["Glisser : déplacer · clic droit : réduire/agrandir"], 0.6, 0.6, 0.6 } }) end
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
      if e.icon then b.icon:SetTexture(e.icon); b.icon:Show(); b.icon:SetWidth(13)
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
  if c then c.point, c.x, c.y = "CENTER", 300, 120 end
  applyPosition()
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
  local corner = CORNERS[s.corner] and s.corner or "TOPRIGHT"
  local off = CORNERS[corner]
  alert:ClearAllPoints()
  alert:SetPoint(corner, _G.UIParent, corner, off[1], off[2])
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
