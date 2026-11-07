-- SampleAddon core: event dispatch, module registry, suggestions, comms, slash.
local ADDON, ns = ...
local A = {}
SampleAddon = A
A.modules, A.suggestions = {}, {}
local PREFIX = "SAMPLEADD"

local frame = CreateFrame("Frame", "SampleAddonEvents")
local handlers = {}
function A:On(event, fn)
  handlers[event] = handlers[event] or {}
  table.insert(handlers[event], fn)
  frame:RegisterEvent(event)
end
frame:SetScript("OnEvent", function(_, event, ...)
  for _, fn in ipairs(handlers[event] or {}) do fn(event, ...) end
end)

function A:RegisterModule(name, mod)
  self.modules[name] = mod
  return mod
end

function A:Suggest(id, text, prio)
  self.suggestions[id] = { text = text, prio = prio or 1, t = GetTime() }
end

function A:Send(msg)
  local send = (C_ChatInfo and C_ChatInfo.SendAddonMessage) or SendAddonMessage
  if send and IsInGroup and IsInGroup() then send(PREFIX, msg, "PARTY") end
end

A:On("ADDON_LOADED", function(_, name)
  if name ~= ADDON then return end
  SampleAddonDB = SampleAddonDB or {}
  SampleAddonDB.version = 1
  SampleAddonDB.logins = (SampleAddonDB.logins or 0) + 1
  local reg = (C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix) or RegisterAddonMessagePrefix
  if reg then reg(PREFIX) end
end)
A:On("PLAYER_LOGIN", function()
  for _, mod in pairs(A.modules) do if mod.OnLogin then mod:OnLogin() end end
  local _, class = UnitClass("player")
  SampleAddonDB.char = { class = class, level = UnitLevel("player"), zone = GetZoneText() }
end)
A:On("GROUP_ROSTER_UPDATE", function() A:Send("HELLO") end)
A:On("CHAT_MSG_ADDON", function(_, prefix, msg, channel, sender)
  if prefix ~= PREFIX then return end
  local kind = strsplit(":", msg or "")
  if kind == "HELLO" then A.lastPeer = Ambiguate(sender or "?", "none") end
end)
A:On("PLAYER_LOGOUT", function()
  SampleAddonDB.suggestions = 0
  for _ in pairs(A.suggestions) do SampleAddonDB.suggestions = SampleAddonDB.suggestions + 1 end
end)

SLASH_SAMPLEADDON1, SLASH_SAMPLEADDON2 = "/sampleaddon", "/sa"
SlashCmdList.SAMPLEADDON = function(msg)
  msg = strtrim(msg or ""):lower()
  for _, mod in pairs(A.modules) do
    if mod.OnSlash and mod:OnSlash(msg) then return end
  end
  print("SampleAddon: /sa macros | /sa status")
end

local panel = CreateFrame("Frame", "SampleAddonPanel", UIParent, BackdropTemplateMixin and "BackdropTemplate")
panel:SetSize(200, 60)
panel:SetPoint("CENTER")
panel:SetMovable(true)
panel:EnableMouse(true)
panel.text = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
panel.text:SetPoint("TOPLEFT", 6, -6)
local elapsed = 0
panel:SetScript("OnUpdate", function(self, dt)
  elapsed = elapsed + dt
  if elapsed < 0.5 then return end
  elapsed = 0
  local best
  for _, s in pairs(A.suggestions) do if not best or s.prio > best.prio then best = s end end
  self.text:SetText(best and best.text or "")
end)
