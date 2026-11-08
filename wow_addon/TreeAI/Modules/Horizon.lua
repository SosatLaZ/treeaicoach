-- TreeAI — Bande d'horizon (agent NAV) : le chemin « en 3D » le plus proche que le jeu permet.
-- L'API de WoW ne donne AUCUNE projection monde → écran aux addons (pas de WorldToScreen ; seules les barres de
-- nom suivent des unités). On fait donc comme les boussoles de jeux modernes : une bande en haut de l'écran,
-- graduée N / NE / E…, où les prochains arrêts sont placés selon leur cap par rapport à ta caméra-personnage
-- (champ de 180°). Un arrêt hors champ se colle au bord avec « ‹ » / « › ».
-- Coût : rien quand la boussole n'a pas de cible ; sinon cap lu 4 fois par seconde à l'arrêt, 20 fois en
-- mouvement / en tournant, caps des arrêts recalculés 1 fois par seconde. Aucune table créée par image.
-- Respecte la règle « un seul guide » : visible seulement quand la boussole TreeAI guide (pas pendant SPF).
-- Lua 5.1, aucune globale, aucun cadre Blizzard.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local Hz = T:RegisterModule("Horizon", {
  title = "Bande d'horizon",
  defaults = { enabled = true, shown = true, stops = 3 },
  charDefaults = { point = "TOP", x = 0, y = -18 },
  options = {
    { key = "shown", type = "toggle", label = "Bande d'horizon en haut de l'écran (prochains arrêts selon ton cap)" },
    { key = "stops", type = "range", label = "Arrêts affichés sur la bande", min = 1, max = 3, step = 1 },
  },
})
T.Horizon = Hz

local pi, floor = math.pi, math.floor
local W, HGT = 420, 30
local FOV = pi / 2                       -- ±90°
Hz.W, Hz.H = W, HGT
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return tryPass(pcall(fn, ...)) end
local function sv(v) local f = rawget(_G, "issecretvalue") if f and v ~= nil and f(v) then return nil end return v end
local function C() return T.Compass end

-- position x (pixels depuis le centre) d'un cap relatif (rad, sens anti-horaire = à gauche) ; clamp au bord
function Hz.X(rel)
  if not rel then return nil end
  local r = rel % (2 * pi)
  if r > pi then r = r - 2 * pi end              -- −pi..pi, positif = à gauche
  local out = r > FOV and 1 or (r < -FOV and -1) or 0
  if out ~= 0 then r = out * FOV end
  return -r / FOV * (W / 2 - 14), out
end

local CARD = { { "N", 0 }, { "NO", pi / 4 }, { "O", pi / 2 }, { "SO", 3 * pi / 4 }, { "S", pi },
  { "SE", -3 * pi / 4 }, { "E", -pi / 2 }, { "NE", -pi / 4 } }
local COL = { { 0.3, 1, 0.4 }, { 1, 0.85, 0.2 }, { 0.6, 0.75, 1 } }

local frame, cards, marks = nil, {}, {}
local stops = {}                           -- { bearing, dist } réutilisés (3 max)
for i = 1, 3 do stops[i] = { b = nil, d = nil, label = nil } end
local lastFacing, acc, full, busy = nil, 0, 0, false

local function place(fs, x)
  fs:ClearAllPoints()
  fs:SetPoint("CENTER", frame, "CENTER", x, 0)
end

--- caps des arrêts (1 Hz) : arrêt 1 = cible de la boussole, puis les suivantes de sa liste
function Hz.Bearings()
  local Cm = C()
  local st = Cm and Cm.state
  local n = 0
  local want = math.max(1, math.min(3, tonumber(Hz.db and Hz.db.stops) or 3))
  if st and st.bearing then
    n = 1
    stops[1].b, stops[1].d, stops[1].label = st.bearing, st.dist, st.target and st.target.label
  end
  local me = Cm and Cm.me
  if n == 1 and me and Cm.targets and Cm.Geo then
    local i = (Cm.idx or 1) + 1
    while n < want and Cm.targets[i] do
      local t = Cm.targets[i]
      if t.mapID and t.x then
        local d, b = Cm.Geo.Vector(me, t)
        if b then n = n + 1 stops[n].b, stops[n].d, stops[n].label = b, d, t.label end
      end
      i = i + 1
    end
  end
  for k = n + 1, 3 do stops[k].b = nil end
  Hz.count = n
  return n
end

--- dessine selon l'orientation (appelé souvent : aucune allocation)
function Hz.Draw(facing)
  if not frame then return end
  for i = 1, #CARD do
    local x, out = Hz.X(CARD[i][2] - facing)
    local fs = cards[i]
    if out == 0 then fs:Show() place(fs, x) else fs:Hide() end
  end
  local Cm = C()
  for i = 1, 3 do
    local m, s = marks[i], stops[i]
    if s.b then
      local x, out = Hz.X(s.b - facing)
      m:Show()
      m:ClearAllPoints()
      m:SetPoint("CENTER", frame, "CENTER", x, 0)
      m.edge:SetText(out > 0 and "‹" or (out < 0 and "›" or ""))
      if Cm and Cm.Geo and s.d and m.lastD ~= floor(s.d / 10) then
        m.lastD = floor(s.d / 10)
        m.dist:SetText(Cm.Geo.Dist(s.d, Cm.db and Cm.db.units))
      end
    else
      m:Hide()
    end
  end
end

local function create()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Frame", "TreeAIHorizonFrame", _G.UIParent)
  frame:SetSize(W, HGT)
  local ch = Hz.char or {}
  frame:SetPoint(ch.point or "TOP", _G.UIParent, ch.point or "TOP", ch.x or 0, ch.y or -18)
  frame:SetFrameStrata("LOW")
  frame:SetClampedToScreen(true)
  if frame.EnableMouse then frame:EnableMouse(false) end   -- jamais de clic volé au jeu
  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(frame)
  bg:SetColorTexture(0, 0, 0, 0.28)
  local mid = frame:CreateTexture(nil, "BORDER")
  mid:SetSize(2, HGT)
  mid:SetPoint("CENTER", frame, "CENTER")
  mid:SetColorTexture(1, 1, 1, 0.35)
  for i = 1, #CARD do
    local fs = frame:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
    fs:SetText(CARD[i][1])
    if #CARD[i][1] == 1 then fs:SetTextColor(1, 1, 1, 0.9) else fs:SetTextColor(0.7, 0.7, 0.7, 0.7) end
    cards[i] = fs
  end
  for i = 3, 1, -1 do                                -- l'arrêt 1 dessiné au-dessus
    local m = _G.CreateFrame("Frame", nil, frame)
    m:SetSize(18, HGT)
    m:SetFrameLevel((frame.GetFrameLevel and frame:GetFrameLevel() or 1) + 4 - i)
    local bar = m:CreateTexture(nil, "OVERLAY")
    bar:SetSize(i == 1 and 4 or 3, HGT - 6)
    bar:SetPoint("CENTER", m, "CENTER")
    bar:SetColorTexture(COL[i][1], COL[i][2], COL[i][3], i == 1 and 1 or 0.8)
    local num = m:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    num:SetPoint("BOTTOM", m, "TOP", 0, -2)
    num:SetText(tostring(i))
    num:SetTextColor(COL[i][1], COL[i][2], COL[i][3])
    m.dist = m:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    m.dist:SetPoint("TOP", m, "BOTTOM", 0, 2)
    m.edge = m:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    m.edge:SetPoint("CENTER", m, "CENTER", 0, 0)
    m:Hide()
    marks[i] = m
  end
  frame:SetScript("OnUpdate", function(_, el)
    el = el or 0
    acc, full = acc + el, full + el
    if acc < (busy and 0.05 or 0.25) then return end
    acc = 0
    local dirty = false
    if full >= 1 then full = 0 Hz.Bearings() dirty = true end
    local Cm = C()
    local st = Cm and Cm.state
    local facing = sv(try(_G.GetPlayerFacing))
    if not facing then return end
    if st and st.bearing and stops[1].b and stops[1].b ~= st.bearing then stops[1].b = st.bearing dirty = true end
    local NT = T.NavTime
    busy = facing ~= lastFacing or (NT and NT.moving) or false
    if dirty or facing ~= lastFacing then Hz.Draw(facing) end   -- rien ne bouge : rien à redessiner
    lastFacing = facing
  end)
  frame:Hide()
  if T.RegisterMovable then
    pcall(T.RegisterMovable, T, frame, "horizon", "Bande d'horizon", {
      onMoved = function(point, x, y) if Hz.char then Hz.char.point, Hz.char.x, Hz.char.y = point, x, y end end })
  end
  return frame
end
Hz.Create = create

--- appelé par la boussole après chaque calcul (≈ 1 Hz) : visible seulement si la boussole TreeAI guide
function Hz.Sync(st)
  local on = Hz.db and Hz.db.shown ~= false and Hz:IsEnabled()
  local Cm = C()
  local combat = Cm and Cm.db and Cm.db.hideCombat ~= false and Cm.InCombat and Cm.InCombat()
  local vis = on and st and st.bearing and not (st.none or st.hidden or st.dup or st.hiddenDead or st.arrived or combat)
  if not vis then
    if frame and frame:IsShown() then frame:Hide() end
    return false
  end
  create()
  if not frame then return false end
  if not frame:IsShown() then
    Hz.Bearings()
    frame:Show()
    local f = sv(try(_G.GetPlayerFacing))
    if f then Hz.Draw(f) end
  end
  return true
end

function Hz:OnDisable() if frame then frame:Hide() end end
function Hz:OnOptionChanged() if frame and frame:IsShown() then Hz.Bearings() end end
