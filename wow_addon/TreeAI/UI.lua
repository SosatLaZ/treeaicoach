-- TreeAI - small movable suggestion panel (no minimap icon).
local TreeAI = TreeAI
if not TreeAI then return end
local _G = _G
local L = TreeAI.L
local tostring = tostring

local PRIO_COLORS = {
  [1] = { 0.85, 0.85, 0.85 },
  [2] = { 1.00, 0.82, 0.20 },
  [3] = { 1.00, 0.30, 0.25 },
}
local LINE_H = 16
local WIDTH = 260
local MAX_LINES = 12

local panel, lines

local function settings()
  return TreeAI.settings and TreeAI.settings.panel
end

local function savePosition()
  local s = settings()
  if not (panel and s) then return end
  local point, _, _, x, y = panel:GetPoint(1)
  s.point, s.x, s.y = point or "CENTER", x or 0, y or 0
end

local function applyPosition()
  local s = settings()
  if not (panel and s) then return end
  panel:ClearAllPoints()
  panel:SetPoint(s.point or "CENTER", _G.UIParent, s.point or "CENTER", s.x or 0, s.y or 0)
  panel:SetScale(s.scale or 1)
  panel:SetAlpha(s.alpha or 0.9)
end

local function makeLine(i)
  local b = _G.CreateFrame("Button", nil, panel)
  b:SetHeight(LINE_H)
  b:SetPoint("TOPLEFT", panel, "TOPLEFT", 8, -22 - (i - 1) * LINE_H)
  b:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -8, -22 - (i - 1) * LINE_H)
  b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(14, 14)
  b.icon:SetPoint("LEFT", b, "LEFT", 0, 0)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("LEFT", b.icon, "RIGHT", 4, 0)
  b.text:SetPoint("RIGHT", b, "RIGHT", 0, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b:SetScript("OnClick", function(self, button)
    local e = self.entry
    if not e then return end
    if button == "RightButton" then
      TreeAI:ClearSuggestion(e.id)
    elseif e.onClick then
      TreeAI.safecall(e.onClick, e)
    end
  end)
  b:SetScript("OnEnter", function(self)
    local e, tt = self.entry, _G.GameTooltip
    if not (e and tt) then return end
    tt:SetOwner(self, "ANCHOR_LEFT")
    tt:AddLine(e.text, 1, 1, 1, true)
    if e.macro then
      tt:AddLine(" ")
      tt:AddLine(L["Macro proposée"] .. ":", 0.6, 0.8, 1)
      tt:AddLine(e.macro, 0.9, 0.9, 0.9, true)
    end
    tt:AddLine(L["Clic droit: masquer"], 0.5, 0.5, 0.5)
    tt:Show()
  end)
  b:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
  return b
end

function TreeAI:CreatePanel()
  if panel or not _G.CreateFrame then return panel end
  local template = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
  panel = _G.CreateFrame("Frame", "TreeAIPanel", _G.UIParent, template)
  panel:SetSize(WIDTH, 40)
  panel:SetFrameStrata("MEDIUM")
  panel:SetClampedToScreen(true)
  panel:SetMovable(true)
  panel:EnableMouse(true)
  panel:RegisterForDrag("LeftButton")
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
  panel:SetScript("OnDragStart", function(self)
    local s = settings()
    if s and s.locked then return end
    if TreeAI:InCombat() then return end
    self:StartMoving()
  end)
  panel:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    savePosition()
  end)

  local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  title:SetPoint("TOPLEFT", panel, "TOPLEFT", 8, -6)
  title:SetText("|cff33cc66TreeAI|r " .. L["Coach"])
  panel.title = title

  local close = _G.CreateFrame("Button", nil, panel, "UIPanelCloseButton")
  close:SetSize(18, 18)
  close:SetPoint("TOPRIGHT", panel, "TOPRIGHT", 0, 0)
  close:SetScript("OnClick", function() TreeAI:HidePanel() end)

  panel.empty = panel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  panel.empty:SetPoint("TOPLEFT", panel, "TOPLEFT", 8, -22)
  panel.empty:SetText(L["Rien à signaler."])

  lines = {}
  for i = 1, MAX_LINES do
    lines[i] = makeLine(i)
    lines[i]:Hide()
  end

  -- expire old suggestions even without new events
  TreeAI:NewTicker(1, function() if panel:IsShown() then TreeAI:RefreshPanel() end end)

  applyPosition()
  local s = settings()
  if s and s.shown == false then panel:Hide() else panel:Show() end
  self:RefreshPanel()
  return panel
end

function TreeAI:RefreshPanel()
  if not panel then return end
  local list = self:GetSuggestions()
  local s = settings()
  local maxLines = (s and s.maxLines) or 6
  if maxLines > MAX_LINES then maxLines = MAX_LINES end
  local shown = 0
  for i = 1, MAX_LINES do
    local b, e = lines[i], list[i]
    if e and i <= maxLines then
      shown = shown + 1
      b.entry = e
      local c = PRIO_COLORS[e.prio] or PRIO_COLORS[1]
      b.text:SetText(tostring(e.text))
      b.text:SetTextColor(c[1], c[2], c[3])
      if e.icon then
        b.icon:SetTexture(e.icon)
        b.icon:Show()
        b.icon:SetWidth(14)
      else
        b.icon:Hide()
        b.icon:SetWidth(1)
      end
      b:Show()
    else
      b.entry = nil
      b:Hide()
    end
  end
  if shown == 0 then panel.empty:Show() else panel.empty:Hide() end
  panel:SetHeight(28 + LINE_H * (shown > 0 and shown or 1))
  if #list > maxLines then
    panel.title:SetText("|cff33cc66TreeAI|r " .. L["Coach"] .. " (+" .. (#list - maxLines) .. ")")
  else
    panel.title:SetText("|cff33cc66TreeAI|r " .. L["Coach"])
  end
end

function TreeAI:ShowPanel()
  if not panel then self:CreatePanel() end
  if panel then panel:Show(); self:RefreshPanel() end
  local s = settings(); if s then s.shown = true end
end

function TreeAI:HidePanel()
  if panel then panel:Hide() end
  local s = settings(); if s then s.shown = false end
end

function TreeAI:TogglePanel()
  if panel and panel:IsShown() then self:HidePanel() else self:ShowPanel() end
end

function TreeAI:ResetPanel()
  local s = settings()
  if not s then return end
  s.point, s.x, s.y, s.scale, s.alpha = "CENTER", 300, 120, 1, 0.9
  applyPosition()
end

function TreeAI:ApplyPanelSettings()
  applyPosition()
  self:RefreshPanel()
end

function TreeAI:GetPanel() return panel end
