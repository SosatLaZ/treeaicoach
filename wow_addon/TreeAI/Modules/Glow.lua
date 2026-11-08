-- TreeAI — Lueurs animées (agent NAV), utilisées par Highlight (objets de quête dans les sacs, le butin,
-- les infobulles). Deux effets, tous deux joués par le client (AnimationGroup) : AUCUN code Lua par image.
--  * « fourmis » : un contour en pointillés qui tourne autour du bouton (4 bords découpés, chacun avec une
--    bande de tirets qui glisse d'une période en boucle) ;
--  * « pulsation » : halo qui grossit / s'éteint en boucle.
-- Idée connue (contours animés des addons de surbrillance) ; code écrit ici, rien de copié.
-- Taint : en combat on ne touche qu'aux textures et aux animations (jamais Show/Hide/SetPoint d'un cadre).
-- Uniquement sur NOS cadres (le calque de Highlight, enfant d'UIParent) : jamais sur un cadre Blizzard / tiers.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not T then return end
local Glow = {}
T.Glow = Glow

local WHITE = "Interface\\Buttons\\WHITE8X8"
local HALO = "Interface\\Buttons\\UI-ActionButton-Border"
local DASH, GAP, THICK = 4, 4, 2
local PERIOD = DASH + GAP
Glow.PERIOD = PERIOD
local ceil = math.ceil
local function locked(f) return (T.FrameLocked and T:FrameLocked(f)) or false end   -- combat + cadre protégé

-- bords : point d'ancrage du découpage, sens de la marche (dx, dy), horizontal ?
local EDGES = {
  { "TOPLEFT", "TOPRIGHT", PERIOD, 0, true },      -- haut → droite
  { "TOPRIGHT", "BOTTOMRIGHT", 0, -PERIOD, false },-- droite → bas
  { "BOTTOMRIGHT", "BOTTOMLEFT", -PERIOD, 0, true },-- bas → gauche
  { "BOTTOMLEFT", "TOPLEFT", 0, PERIOD, false },   -- gauche → haut
}

local function newEdge(host, i)
  local e = EDGES[i]
  local clip = _G.CreateFrame("Frame", nil, host)
  for k = 1, 2 do                                    -- 1 px à l'extérieur du bouton
    local c = e[k]
    clip:SetPoint(c, host, c, c:find("LEFT") and -1 or 1, c:find("TOP") and 1 or -1)
  end
  if e[5] then clip:SetHeight(THICK) else clip:SetWidth(THICK) end
  if clip.SetClipsChildren then clip:SetClipsChildren(true) end
  local run = _G.CreateFrame("Frame", nil, clip)
  run.dashes, run.horiz, run.i = {}, e[5], i
  local ag = run.CreateAnimationGroup and run:CreateAnimationGroup()
  if ag then
    local tr = ag:CreateAnimation("Translation")
    if tr and tr.SetOffset then tr:SetOffset(e[3], e[4]) end
    if tr and tr.SetDuration then tr:SetDuration(0.5) end
    if ag.SetLooping then ag:SetLooping("REPEAT") end
    run.ag = ag
  end
  clip.run = run
  return clip
end

-- (re)taille la bande de tirets à la longueur du bord (+ une période pour le glissement)
local function layout(clip, len, r, g, b, a)
  local run = clip.run
  local n = ceil(len / PERIOD) + 2
  run:ClearAllPoints()
  local e = EDGES[run.i]
  -- la bande démarre une période EN AMONT du sens de marche : le glissement d'une période la garde couvrante
  if run.horiz then
    run:SetSize(n * PERIOD, THICK)
    if e[3] > 0 then run:SetPoint("LEFT", clip, "LEFT", -PERIOD, 0) else run:SetPoint("RIGHT", clip, "RIGHT", PERIOD, 0) end
  else
    run:SetSize(THICK, n * PERIOD)
    if e[4] < 0 then run:SetPoint("TOP", clip, "TOP", 0, PERIOD) else run:SetPoint("BOTTOM", clip, "BOTTOM", 0, -PERIOD) end
  end
  for k = 1, n do
    local d = run.dashes[k]
    if not d then
      d = run:CreateTexture(nil, "OVERLAY")
      d:SetTexture(WHITE)
      if d.SetBlendMode then d:SetBlendMode("ADD") end
      run.dashes[k] = d
    end
    d:ClearAllPoints()
    if run.horiz then d:SetSize(DASH, THICK) d:SetPoint("LEFT", run, "LEFT", (k - 1) * PERIOD, 0)
    else d:SetSize(THICK, DASH) d:SetPoint("TOP", run, "TOP", 0, -(k - 1) * PERIOD) end
    d:SetVertexColor(r, g, b, a)
    d:Show()
  end
  for k = n + 1, #run.dashes do run.dashes[k]:Hide() end
  run.n = n
end
-- montrer / cacher les tirets : des textures (jamais protégées), les cadres de découpage restent tels quels
local function showDashes(clip, on)
  local run = clip.run
  for k = 1, run.n or 0 do local d = run.dashes[k] if on then d:Show() else d:Hide() end end
end

--- contour en marche autour de host (notre cadre) ; w, h = taille connue (sinon lue sur host)
function Glow.Ants(host, r, g, b, a, w, h)
  if not (host and _G.CreateFrame) then return nil end
  local A = host._ants
  if not A then
    A = {}
    for i = 1, 4 do A[i] = newEdge(host, i) end
    host._ants = A
  end
  w = tonumber(w) or tonumber(host.GetWidth and host:GetWidth()) or 36
  h = tonumber(h) or tonumber(host.GetHeight and host:GetHeight()) or 36
  if w < 8 then w = 36 end
  if h < 8 then h = 36 end
  local key = ("%d:%d:%.2f:%.2f:%.2f:%.2f"):format(w, h, r, g, b, a or 1)
  for i = 1, 4 do
    local clip = A[i]
    if A.key ~= key then layout(clip, (i % 2 == 1) and w + 2 or h + 2, r, g, b, a or 1)
    elseif not A.on then showDashes(clip, true) end
    local ag = clip.run.ag
    if ag and ag.Play and not (ag.IsPlaying and ag:IsPlaying()) and not locked(clip.run) then ag:Play() end
  end
  A.key = key
  A.on = true
  return A
end

--- halo qui pulse (taille + éclat) autour de host
function Glow.Pulse(host, r, g, b, a)
  if not (host and host.CreateTexture) then return nil end
  local p = host._pulse
  if not p then
    p = host:CreateTexture(nil, "OVERLAY")
    p:SetTexture(HALO)
    if p.SetBlendMode then p:SetBlendMode("ADD") end
    p:SetPoint("TOPLEFT", host, "TOPLEFT", -16, 16)
    p:SetPoint("BOTTOMRIGHT", host, "BOTTOMRIGHT", 16, -16)
    local ag = p.CreateAnimationGroup and p:CreateAnimationGroup()
    if ag then
      local al = ag:CreateAnimation("Alpha")
      if al and al.SetFromAlpha then al:SetFromAlpha(0.35) al:SetToAlpha(1) end
      if al and al.SetDuration then al:SetDuration(0.55) end
      local sc = ag:CreateAnimation("Scale")
      if sc and sc.SetScaleFrom then sc:SetScaleFrom(0.92, 0.92) sc:SetScaleTo(1.12, 1.12)
      elseif sc and sc.SetScale then sc:SetScale(1.2, 1.2) end
      if sc and sc.SetDuration then sc:SetDuration(0.55) end
      if ag.SetLooping then ag:SetLooping("BOUNCE") end
      p.ag = ag
    end
    host._pulse = p
  end
  p:SetVertexColor(r, g, b, a or 1)
  p:Show()
  if p.ag and p.ag.Play and not (p.ag.IsPlaying and p.ag:IsPlaying()) then p.ag:Play() end
  return p
end

--- tout arrêter (le cadre hôte reste, prêt à resservir)
function Glow.Stop(host)
  if not host then return end
  local A = host._ants
  if A and A.on then
    for i = 1, 4 do
      local ag = A[i].run.ag
      if ag and ag.Stop and not locked(A[i].run) then ag:Stop() end
      showDashes(A[i], false)
    end
    A.on = false
  end
  local p = host._pulse
  if p then
    if p.ag and p.ag.Stop then p.ag:Stop() end
    p:Hide()
  end
end

function Glow.IsOn(host) return host and host._ants and host._ants.on or false end
