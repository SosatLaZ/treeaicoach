-- TreeAI — module Meter « Dégâts » (agent REPLACE, 4.0.1) : la petite fenêtre des dégâts du groupe, pour se passer
-- de Details. WoW: Forever a son PROPRE compteur (C_DamageMeter, documenté dans le client 1.60.1 : sessions
-- « en cours » / « tout », sources = membres du groupe avec total et par seconde) ; le journal de combat, lui, est
-- interdit aux addons. Règle de Forever : pendant un combat ces chiffres sont SECRETS pour un addon (SecretWhenInCombat)
-- → la fenêtre se met à jour à la fin du combat (et à l'ouverture), jamais en combat ; aucune valeur secrète lue.
-- Deux vues : dernier combat / toute la session ; « Remise à zéro » = celle du jeu (clic). Fenêtre = Theme:Window,
-- déplaçable, cachée = aucun événement suivi. Sans C_DamageMeter (Classic Era) : la fenêtre le dit. Lua 5.1.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local DM = T:RegisterModule("Meter", {
  title = "Dégâts",
  description = "Petite fenêtre des dégâts du groupe (dernier combat / session), lue dans le compteur du jeu après chaque combat.",
  defaults = { enabled = true, shown = false, mode = 1 },   -- la fenêtre s'ouvre sur demande (page « Mes addons », /tai degats)
  charDefaults = { pos = nil },
  options = {
    { key = "shown", type = "toggle", label = "Afficher la fenêtre « Dégâts »", section = "Fenêtre" },
    { key = "mode", type = "choice", label = "Montrer", values = { "Dernier combat", "Toute la session" }, section = "Fenêtre" },
  },
})
T.Meter = DM
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[2]   -- « Combat et classe »
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Meter" then has = true end end
    if not has then fam.mods[#fam.mods + 1] = "Meter" end
  end
end

local floor, max, min = math.floor, math.max, math.min
local fmt, tsort = string.format, table.sort
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function str(v) if type(v) ~= "string" or secret(v) then return nil end return v end
local function opt(k) return DM.db and DM.db[k] ~= false end
local function inCombat() return T.InCombat and T:InCombat() or false end
local cut = T.Cut or function(s, n) s = tostring(s or "") return #s > n and s:sub(1, n) or s end

-- ===================================================================== lecture (hors combat seulement)
local function CDM() local c = g("C_DamageMeter") return type(c) == "table" and c or nil end
--- le compteur du jeu est-il là ? → vrai | faux, raison (texte du jeu ou la nôtre)
function DM.Available()
  local c = CDM()
  if not (c and type(c.GetCombatSessionFromType) == "function") then return false, "pas de compteur de dégâts dans ce client" end
  if type(c.IsDamageMeterAvailable) == "function" then
    local ok, avail, why = pcall(c.IsDamageMeterAvailable)
    if ok and avail == false then return false, str(why) or "désactivé dans le jeu" end
  end
  return true
end
local function enums()
  local E = g("Enum") or {}
  local ST = type(E.DamageMeterSessionType) == "table" and E.DamageMeterSessionType or {}
  local TY = type(E.DamageMeterType) == "table" and E.DamageMeterType or {}
  return tonumber(ST.Current) or 1, tonumber(ST.Overall) or 0, tonumber(TY.DamageDone) or 0
end
--- { rows = { {name, class, amount, dps, me, pct} }, total, dur } ou nil, raison ; jamais en combat
function DM.Read(mode)
  if inCombat() then return nil, "combat" end
  local ok, why = DM.Available()
  if not ok then return nil, why end
  local c = CDM()
  local cur, all, dmg = enums()
  local st = (mode == 2) and all or cur
  local s = try(c.GetCombatSessionFromType, st, dmg)
  if type(s) ~= "table" or secret(s) then return nil, "vide" end
  local rows, total = {}, 0
  local src = s.combatSources
  if type(src) == "table" and not secret(src) then
    for _, e in ipairs(src) do
      if type(e) == "table" then
        local amount = num(e.totalAmount)
        local name = str(e.name)
        if amount and amount > 0 and name then
          local me = e.isLocalPlayer
          rows[#rows + 1] = { name = name, class = str(e.classFilename), amount = amount, dps = num(e.amountPerSecond),
            me = (not secret(me)) and me == true or false }
          total = total + amount
        end
      end
    end
  end
  tsort(rows, function(a, b) if a.amount ~= b.amount then return a.amount > b.amount end return a.name < b.name end)
  local sum = num(s.totalAmount) or total
  for _, r in ipairs(rows) do r.pct = sum > 0 and floor(r.amount * 100 / sum + 0.5) or 0 end
  local dur = num(s.durationSeconds)
  if not dur and type(c.GetSessionDurationSeconds) == "function" then dur = num(try(c.GetSessionDurationSeconds, st)) end
  return { rows = rows, total = sum, dur = dur }
end
local function short(n)
  n = tonumber(n) or 0
  if n >= 1e6 then return (fmt("%.1f M", n / 1e6):gsub("%.", ",")) end
  if n >= 1e4 then return (fmt("%.1f k", n / 1e3):gsub("%.", ",")) end
  return tostring(floor(n + 0.5))
end
DM.Short = short
local function clock(sec)
  sec = floor(tonumber(sec) or 0)
  if sec >= 3600 then return fmt("%d h %02d", floor(sec / 3600), floor(sec / 60) % 60) end
  return fmt("%d:%02d", floor(sec / 60), sec % 60)
end
DM.Clock = clock

-- ===================================================================== fenêtre
local frame, rows = nil, {}
DM.rows = rows
DM.renders = 0
local W_, ROW, HEAD = 230, 18, 22
local function classColor(class)
  local Th = T.Theme
  if type(Th) == "table" and type(Th.ClassColor) == "function" and class then
    local r, gg, b = Th:ClassColor(class)
    if tonumber(r) then return r, gg, b end
  end
  return 0.8, 0.6, 0.2
end
local function savePos()
  if not frame then return end
  local p, _, rp, x, y = meth(frame, "GetPoint", 1)
  if p and tonumber(x) and tonumber(y) and DM.char then DM.char.pos = { point = p, rel = rp or p, x = floor(x + 0.5), y = floor(y + 0.5) } end
end
local function ensure()
  if frame then return frame end
  local CF, up = g("CreateFrame"), g("UIParent")
  if not (CF and up) or inCombat() then return nil end
  frame = CF("Frame", "TreeAIMeterFrame", up)
  frame:SetSize(W_, HEAD + 3 * ROW + 26)
  frame:SetFrameStrata("MEDIUM")
  try(frame.SetClampedToScreen, frame, true)
  frame:SetMovable(true)
  frame:EnableMouse(true)
  local Th = T.Theme
  local hd
  if type(Th) == "table" and Th.Window then
    hd = Th:Window(frame, { id = "meter", title = "Dégâts", icon = "Interface\\Icons\\Ability_Hunter_SniperShot", esc = false,
      locked = function() return inCombat() end, onMoved = savePos,
      onClose = function() if DM.db then DM.db.shown = false end DM.Apply() end })
  end
  local mb = CF("Button", nil, hd or frame)
  mb:SetSize(90, 16)
  if hd and hd.min then mb:SetPoint("RIGHT", hd.min, "LEFT", -4, 0) else mb:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -44, -4) end
  mb.t = mb:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  mb.t:SetAllPoints(mb)
  try(mb.t.SetJustifyH, mb.t, "RIGHT")
  mb:SetScript("OnClick", function() T:UserAction(DM.ToggleMode) end)
  frame.modeBtn = mb
  frame.foot = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  frame.foot:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 8, 6)
  frame.foot:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -8, 6)
  try(frame.foot.SetJustifyH, frame.foot, "LEFT")
  local rb = CF("Button", nil, frame)
  rb:SetSize(70, 14)
  rb:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -6, 5)
  rb.t = rb:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  rb.t:SetAllPoints(rb)
  try(rb.t.SetJustifyH, rb.t, "RIGHT")
  rb.t:SetText("Remise à zéro")
  rb:SetScript("OnClick", function() T:UserAction(DM.Reset) end)
  frame.resetBtn = rb
  local p = DM.char and DM.char.pos
  if type(p) == "table" then frame:SetPoint(p.point or "RIGHT", up, p.rel or p.point or "RIGHT", p.x or -40, p.y or -120)
  else frame:SetPoint("BOTTOMRIGHT", up, "BOTTOMRIGHT", -320, 220) end
  if T.RegisterMovable then T:RegisterMovable(frame, "meter", "Dégâts", { onMoved = savePos }) end
  DM.frame = frame
  frame:Hide()
  return frame
end
local function row(i)
  local r = rows[i]
  if r then return r end
  local CF = g("CreateFrame")
  r = CF("Frame", nil, frame)
  r:SetHeight(ROW - 2)
  r.bar = r:CreateTexture(nil, "BACKGROUND")
  r.bar:SetTexture("Interface\\Buttons\\WHITE8X8")
  r.bar:SetPoint("LEFT", r, "LEFT", 0, 0)
  r.l = r:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  r.l:SetPoint("LEFT", r, "LEFT", 4, 0)
  try(r.l.SetJustifyH, r.l, "LEFT")
  r.r = r:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  r.r:SetPoint("RIGHT", r, "RIGHT", -4, 0)
  try(r.r.SetJustifyH, r.r, "RIGHT")
  rows[i] = r
  return r
end

function DM.Render()
  if not (DM._active and opt("shown")) then return end
  if not ensure() then return end
  local mode = (tonumber(DM.db and DM.db.mode) or 1) == 2 and 2 or 1
  frame.modeBtn.t:SetText(mode == 2 and "Toute la session" or "Dernier combat")
  local data, why
  if inCombat() then data, why = nil, "combat" else data, why = DM.Read(mode) end
  if why == "combat" then
    frame.foot:SetText("En combat : les chiffres arrivent à la fin du combat")
    if not frame:IsShown() then frame:Show() end
    return
  end
  DM.renders = DM.renders + 1
  DM.last = data
  local n = 0
  local list = data and data.rows or {}
  local top = list[1] and list[1].amount or 0
  for i = 1, min(#list, 6) do
    local e = list[i]
    local r = row(i)
    n = i
    r:ClearAllPoints()
    r:SetPoint("TOPLEFT", frame, "TOPLEFT", 6, -(HEAD + 4 + (i - 1) * ROW))
    r:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -6, -(HEAD + 4 + (i - 1) * ROW))
    local cr, cg, cb = classColor(e.class)
    local w = max(2, floor((W_ - 12) * (top > 0 and e.amount / top or 0) + 0.5))
    r.bar:SetSize(w, ROW - 2)
    r.bar:SetVertexColor(cr, cg, cb, e.me and 0.75 or 0.5)
    r.l:SetText(fmt("%d. %s", i, cut(e.name, 16)))
    r.r:SetText(fmt("%s · %s/s · %d %%", short(e.amount), short(e.dps or 0), e.pct or 0))
    r:Show()
  end
  for i = n + 1, #rows do rows[i]:Hide() end
  if n == 0 then
    frame.foot:SetText(why == "vide" and "Pas encore de combat" or (why and ("Compteur indisponible : " .. tostring(why))) or "Pas encore de combat")
  else
    frame.foot:SetText(fmt("%s · total %s", data.dur and clock(data.dur) or "?", short(data.total)))
  end
  frame:SetHeight(HEAD + 8 + max(1, n) * ROW + 22)
  if not frame:IsShown() then frame:Show() end
end

local soon = false
function DM.Soon(delay)
  if soon then return end
  soon = true
  local function run() soon = false DM.Render() end
  if T.After then T:After(delay or 0.3, run) else run() end
end
function DM.ToggleMode()
  if not DM.db then return end
  DM.db.mode = ((tonumber(DM.db.mode) or 1) == 2) and 1 or 2
  DM.Render()
end
function DM.Reset()
  local c = CDM()
  if c and type(c.ResetAllCombatSessions) == "function" and not inCombat() then try(c.ResetAllCombatSessions) end
  DM.Soon(0.1)
end
-- événements suivis seulement fenêtre ouverte
local EVENTS = { "DAMAGE_METER_COMBAT_SESSION_UPDATED", "DAMAGE_METER_CURRENT_SESSION_UPDATED", "DAMAGE_METER_RESET" }
local listening = false
local function listen(on)
  if on == listening then return end
  listening = on
  for _, ev in ipairs(EVENTS) do
    if on then DM:RegisterEvent(ev, function() if not inCombat() then DM.Soon() end end) else DM:UnregisterEvent(ev) end
  end
  if on then
    DM:RegisterEvent("PLAYER_REGEN_ENABLED", function() DM.Soon(0.6) end)
    DM:RegisterEvent("PLAYER_REGEN_DISABLED", function() if frame and frame:IsShown() then frame.foot:SetText("En combat : les chiffres arrivent à la fin du combat") end end)
  else
    DM:UnregisterEvent("PLAYER_REGEN_ENABLED")
    DM:UnregisterEvent("PLAYER_REGEN_DISABLED")
  end
end
function DM.Apply()
  if not DM._active then return end
  if opt("shown") then
    listen(true)
    DM.Render()
  else
    listen(false)
    if frame then if inCombat() then meth(frame, "SetAlpha", 0) T:RunOutOfCombat("Meter:hide", function() if frame and not opt("shown") then frame:Hide() frame:SetAlpha(1) end end) else frame:Hide() end end
  end
end
function DM.Toggle()
  if not DM.db then return end
  DM.db.shown = not opt("shown")
  DM.Apply()
end
function DM:OnEnable()
  DM._active = true
  T:RunOutOfCombat("Meter:start", DM.Apply)
end
function DM:OnDisable()
  DM._active = false
  listen(false)
  if frame then if inCombat() then meth(frame, "SetAlpha", 0) else frame:Hide() end end
end
function DM:OnOptionChanged() DM.Apply() end
if T.RegisterSlash then
  T:RegisterSlash("degats", function() DM.Toggle() end, "degats : la fenêtre des dégâts (compteur du jeu)")
end
