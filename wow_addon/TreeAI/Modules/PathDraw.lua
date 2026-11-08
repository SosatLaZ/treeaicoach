-- TreeAI — module PathDraw (agent PATH) : VOIR le chemin calculé par l'Itinéraire (Modules/Route.lua + Planner).
--  * Carte du monde : le trajet en pointillés entre les arrêts consécutifs, pastilles numérotées 1, 2, 3… et
--    icône du transport (vol, bateau, zeppelin, pierre de foyer). Dessiné sur NOTRE calque : un cadre enfant
--    d'UIParent posé (coordonnées absolues, jamais ancré ni reparenté sur un cadre de Blizzard) au-dessus du
--    canevas de WorldMapFrame, qui coupe ce qui dépasse. Prévenu par un fournisseur de données
--    (WorldMapFrame:AddDataProvider, le modèle prévu par Blizzard) s'il existe, sinon par HookScript OnShow/OnHide.
--    Segments : objets Line (CreateLine + SetStartPoint / SetEndPoint) en tirets, sinon des points (textures).
--  * Minimap : les 1–2 prochaines étapes en traînée de points qui tourne avec la minimap (même projection que
--    Modules/MinimapPins.lua : MP.YardsRadius / rotation / forme). ≤ 5 Hz, seulement si le joueur a bougé
--    (ou tourné, zoomé, ou si le trajet a changé) ; textures réutilisées (réserve), aucune table créée par passe.
--  * Rien n'est dessiné sans trajet actif (Itinéraire en pause = rien). Shortest Path Forever guide déjà :
--    on garde la carte du monde, la traînée minimap seulement si l'option le demande. Option « Guidage » du
--    module de guidage (s'il existe) respectée.
-- Taint : aucun cadre protégé, aucun hook de cadre sécurisé, aucune fonction protégée ; cadres créés hors combat.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local COLORS = { "Vert", "Jaune", "Bleu clair", "Orange", "Blanc" }
local RGB = { { 0.25, 1, 0.35 }, { 1, 0.85, 0.15 }, { 0.35, 0.8, 1 }, { 1, 0.55, 0.1 }, { 1, 1, 1 } }

local PD = T:RegisterModule("PathDraw", {
  title = "Chemin dessiné",
  description = "Le trajet de l'Itinéraire dessiné sur la carte du monde et la minimap.",
  defaults = { enabled = true, map = true, minimap = true, minimapSPF = false, color = 1, width = 3 },
  options = {
    { key = "map", type = "toggle", label = "Afficher le chemin sur la carte du monde", section = "Afficher le chemin" },
    { key = "minimap", type = "toggle", label = "Afficher le chemin sur la minimap (1–2 prochaines étapes)" },
    { key = "minimapSPF", type = "toggle", label = "Garder la traînée minimap quand Shortest Path guide" },
    { key = "color", type = "choice", label = "Couleur", values = COLORS },
    { key = "width", type = "range", label = "Épaisseur", min = 1, max = 8, step = 1 },
  },
})
T.PathDraw = PD
-- profil « essentiel » (Core.MODULE_INFO) : utile tout le temps, sans trajet il ne fait rien.
-- Page d'options dans la famille « Quêtes et trajet », juste après « Points sur la minimap ».
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[1]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local at = #fam.mods + 1
    for i, n in ipairs(fam.mods) do
      if n == "PathDraw" then at = nil break end
      if n == "MinimapPins" then at = i + 1 end
    end
    if at then table.insert(fam.mods, at, "PathDraw") end
  end
end

local floor, sqrt, cos, sin, abs, max, min = math.floor, math.sqrt, math.cos, math.sin, math.abs, math.max, math.min
local STEP = 0.2                        -- minimap : ≤ 5 passes / s
local IDLE = 0.5                        -- immobile : 2 passes / s
local SLOW = 3                          -- relecture de la position même immobile (téléportations)
local MAX_DOTS, MAX_DASH, MAX_STOPS = 48, 400, 16
local CIRCLE = "Interface\\CharacterFrame\\TempPortraitAlphaMask"
local ICON = {
  fly = "Interface\\TaxiFrame\\UI-Taxi-Icon-Green",
  boat = "Interface\\Icons\\INV_Fishingpole_01",
  zeppelin = "Interface\\Icons\\INV_Gizmo_02",
  hs = "Interface\\Icons\\INV_Misc_Rune_01",
}

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d = pcall(fn, ...)
  if ok then return a, b, c, d end
  return nil
end
local function G(name) return rawget(_G, name) end
local function now() local f = rawget(_G, "GetTime") return f and f() or 0 end
local function num(v)
  if type(v) == "number" then
    local f = rawget(_G, "issecretvalue")
    if f and f(v) then return nil end
    return v
  end
  return tonumber(v)
end
local function opt(k) return PD.db and PD.db[k] ~= false end
local function minimap() return G("Minimap") end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function locked(f) return T.FrameLocked and T:FrameLocked(f) or false end
local function color()
  local c = RGB[tonumber(PD.db and PD.db.color) or 1] or RGB[1]
  return c[1], c[2], c[3]
end
local function thick() return max(1, min(8, tonumber(PD.db and PD.db.width) or 3)) end

-- ------------------------------------------------------------------ trajet actif (Route / Planner)
-- signature bon marché : identité de la liste d'étapes, curseur, longueur, pause (aucune table créée)
local function routeSig()
  local P, R = T.Planner, T.Route
  if not (P and R and R.HasRoute) then return nil end
  if R.char and R.char.paused then return nil end
  if R.IsEnabled and not R:IsEnabled() then return nil end
  local ok, has = pcall(R.HasRoute, R)
  if not (ok and has) then return nil end
  return P.steps, P.cursor, #P.steps
end
PD.RouteSig = routeSig
function PD.Stops()
  local R = T.Route
  if not (R and R.Stops) then return nil end
  local ok, st = pcall(R.Stops, R, 64)        -- même mémo que la vue de l'Itinéraire (R:PlanLines)
  return ok and type(st) == "table" and st or nil
end

-- option « Guidage » d'un module de guidage (COHERE) si présent ; SPF qui dessine déjà son trajet
local function guide()
  local m = T.modules
  return T.Guide or T.Guidage or T.Guidance or (type(m) == "table" and (m.Guide or m.Guidage)) or nil
end
function PD.GuidanceOff()
  local Gd = guide()
  if type(Gd) ~= "table" then return false end
  if type(Gd.db) == "table" and (Gd.db.enabled == false or Gd.db.guidage == false or Gd.db.path == false) then return true end
  if Gd.IsEnabled and not try(Gd.IsEnabled, Gd) then return true end
  return false
end
function PD.SPFDraws()
  -- Core (COHERE) : Suivi Forever ne guide que si le joueur l'a choisi (T:ExternalGuide() == "spf")
  if T.ExternalGuide then
    local ok, g = pcall(T.ExternalGuide, T)
    return ok and g == "spf" or false
  end
  return false
end

-- ------------------------------------------------------------------ géométrie (pure, testable)
-- partie [t0, t1] d'un segment A→B (px) dans la minimap (rond de rayon r, ou carré de demi-côté r)
function PD.Clip(ax, ay, bx, by, r, square)
  local dx, dy = bx - ax, by - ay
  if square then                                    -- Liang–Barsky (sans table)
    local t0, t1 = 0, 1
    for i = 1, 4 do
      local pp, qq
      if i == 1 then pp, qq = -dx, ax + r elseif i == 2 then pp, qq = dx, r - ax
      elseif i == 3 then pp, qq = -dy, ay + r else pp, qq = dy, r - ay end
      if pp == 0 then
        if qq < 0 then return nil end
      else
        local t = qq / pp
        if pp < 0 then if t > t0 then t0 = t end elseif t < t1 then t1 = t end
      end
    end
    if t0 > t1 then return nil end
    return t0, t1
  end
  local a = dx * dx + dy * dy
  local c = ax * ax + ay * ay - r * r
  if a == 0 then if c <= 0 then return 0, 1 end return nil end
  local b = 2 * (ax * dx + ay * dy)
  local disc = b * b - 4 * a * c
  if disc < 0 then return nil end
  local s = sqrt(disc)
  local t0, t1 = max(0, (-b - s) / (2 * a)), min(1, (-b + s) / (2 * a))
  if t0 > t1 then return nil end
  return t0, t1
end
-- écart écran (px depuis le centre) d'un point à dn yd au nord / dw yd à l'ouest, sans coller au bord
function PD.Project(dn, dw, facing, rotate, k)
  local sx, sy = -dw, dn
  if rotate and facing then
    local c, s = cos(-facing), sin(-facing)
    sx, sy = sx * c - sy * s, sx * s + sy * c
  end
  return sx * k, sy * k
end

-- ------------------------------------------------------------------ minimap : traînée de points
local trail, dots = nil, {}
local mmView = { yr = 1, pr = 70, facing = 0, rotate = false, square = false }   -- réutilisée
local last = { n = nil, w = nil, c = nil, facing = nil, yr = nil, pr = nil, rot = nil, sq = nil, steps = nil, cur = nil,
  len = nil, at = -100, pollAt = -100, stopsAt = -100, step = 0.2 }
local legs = { 0, 0, 0, 0, 0, 0 }                    -- n, w des 2 prochains arrêts (+ continent) réutilisé
local nLegs = 0
PD.dotsShown, PD.mmRedraws = 0, 0

local function ensureTrail()
  if trail then return trail end
  local mm = minimap()
  local CF = G("CreateFrame")
  if not (mm and CF) or inCombat() then return nil end
  trail = CF("Frame", nil, mm)                      -- NOTRE cadre, enfant de la minimap (non protégée)
  trail:SetAllPoints(mm)
  try(trail.EnableMouse, trail, false)
  try(trail.SetFrameLevel, trail, (num(try(mm.GetFrameLevel, mm)) or 1) + 4)
  PD.trail = trail
  return trail
end
local function dot(i)
  local d = dots[i]
  if d then return d end
  if not trail or i > MAX_DOTS then return nil end
  d = trail:CreateTexture(nil, "OVERLAY")
  d:SetTexture(CIRCLE)
  dots[i] = d
  return d
end
PD.dots = dots
local function hideDots(from)
  for i = from, #dots do local d = dots[i] if d:IsShown() then d:Hide() end end
end
function PD.ClearMinimap()
  hideDots(1)
  PD.dotsShown = 0
  last.n = nil
end

local function playerWorld()
  local CM = G("C_Map")
  if not (CM and CM.GetBestMapForUnit and CM.GetPlayerMapPosition) then return nil end
  local map = num(try(CM.GetBestMapForUnit, "player"))
  if not map then return nil end
  local p = try(CM.GetPlayerMapPosition, map, "player")
  if type(p) ~= "table" then return nil end
  local x, y
  if type(p.GetXY) == "function" then x, y = try(p.GetXY, p) else x, y = p.x, p.y end
  x, y = num(x), num(y)
  if not (x and y) or (x == 0 and y == 0) then return nil end
  local Geo = T.Geo
  if not (Geo and Geo.World) then return nil end
  local n, w, c = Geo.World(map, x * 100, y * 100)
  return n, w, c
end
PD.PlayerWorld = playerWorld

local function fillView()
  local mm = minimap()
  local MP = T.MinimapPins
  local zoom = num(try(mm.GetZoom, mm)) or 0
  local indoor = try(G("IsIndoors")) and true or false
  if MP and MP.YardsRadius then mmView.yr = MP.YardsRadius(zoom, indoor)
  else mmView.yr = (indoor and 150 or 700 / 3) end
  mmView.pr = (num(try(mm.GetWidth, mm)) or 140) / 2
  local facing = num(try(G("GetPlayerFacing")))
  local rot = try(G("GetCVar"), "rotateMinimap")
  mmView.facing = facing
  mmView.rotate = (rot == "1" or rot == 1 or rot == true) and facing ~= nil
  mmView.square = try(G("GetMinimapShape")) == "SQUARE"
end

-- relit les 1–2 prochains arrêts (sur changement du trajet, ou toutes les 3 s : les transports dépendent de la position)
local function readLegs(c)
  nLegs = 0
  local st = PD.Stops()
  if not st then return end
  for i = 1, #st do
    local e = st[i]
    -- (QA-QUEST F19) pierre de foyer : on se téléporte, aucune traînée vers l'auberge ; vol / bateau : la traînée
    -- mène au départ (maître de vol, quai) et s'arrête là (on ne marche pas jusqu'à l'arrivée)
    if e.kind == "hs" then break end
    local dep = not e.step and e.from or nil
    local p = dep or e.place
    if p and p.n and p.w and p.c == c then
      legs[nLegs * 2 + 1], legs[nLegs * 2 + 2] = p.n, p.w
      nLegs = nLegs + 1
      if dep or nLegs >= 2 then break end
    elseif p then
      break                                          -- autre continent : la traînée s'arrête là
    end
  end
end

local function drawTrail(n, w)
  local v = mmView
  local k = v.pr / v.yr
  local r = v.pr - 3
  local gap = 7 + thick()
  local size = 2 + thick()
  local cr, cg, cb = color()
  local used = 0
  local ax, ay = 0, 0                                -- départ : le joueur (centre)
  for li = 1, nLegs do
    local bx, by = PD.Project(legs[li * 2 - 1] - n, legs[li * 2] - w, v.facing, v.rotate, k)
    local t0, t1 = PD.Clip(ax, ay, bx, by, r, v.square)
    if t0 then
      local len = sqrt((bx - ax) ^ 2 + (by - ay) ^ 2)
      if len > 0 then
        -- points fixes dans le monde : comptés depuis l'arrêt B (ils ne « glissent » pas quand on marche)
        local dFar, dNear = (1 - t0) * len, (1 - t1) * len
        local dd = floor(dFar / gap) * gap
        if li == 1 then dNear = max(dNear, 0) end
        while dd >= dNear and used < MAX_DOTS do
          if dd <= dFar and dd > 0 then
            local t = 1 - dd / len
            local px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            if li > 1 or (px * px + py * py) > 36 then  -- pas de point sous la flèche du joueur
              used = used + 1
              local d = dot(used)
              if not d then used = used - 1 break end
              d:ClearAllPoints()
              d:SetPoint("CENTER", trail, "CENTER", px, py)
              d:SetSize(size, size)
              d:SetVertexColor(cr, cg, cb, li == 1 and 0.95 or 0.6)
              if not d:IsShown() then d:Show() end
            end
          end
          dd = dd - gap
        end
      end
    end
    ax, ay = bx, by
  end
  hideDots(used + 1)
  PD.dotsShown = used
  PD.mmRedraws = PD.mmRedraws + 1
end

-- options / guidage / SPF relus au plus 1 fois par seconde (le reste de la passe ne lit que des champs)
local wantAt, wantV = -100, false
local function wantMinimap(t)
  if t - wantAt < 1 then return wantV end
  wantAt = t
  wantV = (opt("minimap") and PD:IsEnabled() and routeSig() and not PD.GuidanceOff()
    and (opt("minimapSPF") or not PD.SPFDraws())) and true or false
  return wantV
end
function PD.MinimapTick(force)
  local t = now()
  if not force and t - last.at < last.step then return end
  last.at = t
  if force then wantAt = -100 end
  local P, R = T.Planner, T.Route
  local steps, cur = P and P.steps, P and P.cursor
  local len = steps and #steps
  if not (steps and cur and cur <= len) or (R and R.char and R.char.paused) or not wantMinimap(t) then
    if PD.dotsShown > 0 then PD.ClearMinimap() end
    return
  end
  if not (trail or ensureTrail()) then return end
  local routeChanged = steps ~= last.steps or cur ~= last.cur or len ~= last.len
  -- bouge-t-on ? (vitesse / cap : bon marché) ; sinon relecture lente de la position
  local gs, gf = rawget(_G, "GetUnitSpeed"), rawget(_G, "GetPlayerFacing")
  local speed = gs and num((gs("player"))) or 0
  local facing = gf and num((gf())) or nil
  local moving = speed > 0 or facing ~= last.facing
  last.step = moving and STEP or IDLE     -- immobile : 2 passes / s suffisent (bon marché)
  if not (force or routeChanged or moving or t - last.pollAt >= SLOW) then return end
  last.pollAt = t
  local n, w, c = playerWorld()
  if not n then if PD.dotsShown > 0 then PD.ClearMinimap() end return end
  if routeChanged or c ~= last.c or t - last.stopsAt >= 3 then
    readLegs(c)
    last.steps, last.cur, last.len, last.stopsAt = steps, cur, len, t
    routeChanged = true
  end
  fillView()
  local v = mmView
  if not (force or routeChanged) and last.n and abs(n - last.n) < 1.5 and abs(w - last.w) < 1.5
      and v.yr == last.yr and v.pr == last.pr and v.rotate == last.rot and v.square == last.sq
      and (not v.rotate or abs((v.facing or 0) - (last.facing or 0)) < 0.03) then
    last.facing = facing
    return
  end
  last.n, last.w, last.c, last.facing, last.yr, last.pr, last.rot, last.sq = n, w, c, facing, v.yr, v.pr, v.rotate, v.square
  if nLegs == 0 then if PD.dotsShown > 0 then hideDots(1) PD.dotsShown = 0 end return end
  if locked(trail) then last.n = nil return end
  drawTrail(n, w)
end

-- ------------------------------------------------------------------ carte du monde : calque à nous
local view, canvas = nil, nil            -- view = fenêtre visible (coupe), canvas = toute la carte (déplacée / zoomée)
local dashes, badges = {}, {}
PD.dashes, PD.badges = dashes, badges
PD.segments, PD.badgesShown, PD.dashesShown, PD.mapRedraws = 0, 0, 0, 0
local mapState = { id = nil, l = nil, b = nil, w = nil, h = nil, vl = nil, vb = nil, vw = nil, vh = nil, steps = nil,
  cur = nil, len = nil, at = -100, dirty = true }
local pts = {}                            -- points dessinés : x, y (px canevas), kind — réutilisée

local function wmParts()
  local wm = G("WorldMapFrame")
  if type(wm) ~= "table" then return nil end
  local sc = wm.ScrollContainer
  local child = type(sc) == "table" and sc.Child or nil
  if type(child) ~= "table" then return nil end
  return wm, sc, child
end
local function shownMapID(wm)
  local id = num(try(wm.GetMapID, wm))
  if id then return id end
  local CM = G("C_Map")
  return CM and num(try(CM.GetBestMapForUnit, "player")) or nil
end
-- rectangle écran d'un cadre en coordonnées d'UIParent (lecture seule)
local function rectOf(f)
  local l, b = num(try(f.GetLeft, f)), num(try(f.GetBottom, f))
  local w, h = num(try(f.GetWidth, f)), num(try(f.GetHeight, f))
  if not (l and b and w and h) or w <= 0 or h <= 0 then return nil end
  local es = num(try(f.GetEffectiveScale, f)) or 1
  local up = G("UIParent")
  local us = up and num(try(up.GetEffectiveScale, up)) or 1
  local s = es / (us > 0 and us or 1)
  return l * s, b * s, w * s, h * s
end

local function ensureCanvas()
  if view then return view end
  local CF, up = G("CreateFrame"), G("UIParent")
  if not (CF and up) or inCombat() then return nil end
  view = CF("Frame", nil, up)                       -- NOTRE cadre, enfant d'UIParent, jamais ancré sur la carte
  try(view.EnableMouse, view, false)
  try(view.SetClipsChildren, view, true)
  view:Hide()
  canvas = CF("Frame", nil, view)
  try(canvas.EnableMouse, canvas, false)
  view:SetScript("OnUpdate", function() PD.MapTick() end)
  PD.view, PD.canvas = view, canvas
  return view
end
local function hasLines()
  if not canvas then return false end
  if type(canvas.CreateLine) ~= "function" then PD._lines = false return false end
  if PD._lines == nil then
    local l = canvas:CreateLine(nil, "ARTWORK")
    PD._lines = (type(l) == "table" and type(l.SetStartPoint) == "function" and type(l.SetEndPoint) == "function") and true or false
    if PD._lines then dashes[#dashes + 1] = { obj = l, line = true } elseif l and l.Hide then l:Hide() end
  end
  return PD._lines
end
local function dash(i)
  local d = dashes[i]
  if d then return d end
  if i > MAX_DASH then return nil end
  if hasLines() then
    d = { obj = canvas:CreateLine(nil, "ARTWORK"), line = true }
  else
    local tx = canvas:CreateTexture(nil, "ARTWORK")
    tx:SetTexture(CIRCLE)
    d = { obj = tx }
  end
  dashes[#dashes + 1] = d
  return dashes[i]
end
local function badge(i)
  local b = badges[i]
  if b then return b end
  b = {}
  b.ring = canvas:CreateTexture(nil, "OVERLAY")
  b.ring:SetTexture(CIRCLE)
  b.bg = canvas:CreateTexture(nil, "OVERLAY")
  b.bg:SetTexture(CIRCLE)
  b.text = canvas:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.icon = canvas:CreateTexture(nil, "OVERLAY")
  badges[i] = b
  return b
end
local function hideMapFrom(di, bi)
  for i = di, #dashes do local o = dashes[i].obj if o:IsShown() then o:Hide() end end
  for i = bi, #badges do
    local b = badges[i]
    if b.bg:IsShown() then b.ring:Hide() b.bg:Hide() b.text:Hide() b.icon:Hide() end
  end
end
function PD.ClearMap()
  if not canvas then return end
  hideMapFrom(1, 1)
  PD.segments, PD.badgesShown, PD.dashesShown = 0, 0, 0
end

-- position (px canevas, origine en haut à gauche) d'un arrêt sur la carte affichée ; nil si ailleurs
local function onMap(mapID, p, W, H)
  if not (p and W) then return nil end
  local x, y
  if p.map == mapID and p.x and p.y then
    x, y = p.x, p.y
  elseif p.n and T.Geo and T.Geo.Rect and T.Geo.FromWorld then
    local r = T.Geo.Rect(mapID)
    if not (r and r.c == p.c) then return nil end
    x, y = T.Geo.FromWorld(mapID, p.n, p.w)
  end
  if not (x and y) then return nil end
  return x / 100 * W, y / 100 * H
end
PD.OnMap = onMap

local function drawSegment(di, x1, y1, x2, y2, alpha)
  local len = sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2)
  if len < 1 then return di end
  local th = thick()
  local cr, cg, cb = color()
  local period = 10 + th * 3                        -- tiret + trou
  local n = floor(len / period)
  if n > 60 then n, period = 60, len / 60 end       -- très long trajet : tirets plus espacés
  if n < 1 then n, period = 1, len end
  local on = period * 0.6
  local ux, uy = (x2 - x1) / len, (y2 - y1) / len
  for k = 0, n - 1 do
    local d = dash(di + 1)
    if not d then return di end
    di = di + 1
    local s, e = k * period, min(len, k * period + on)
    local o = d.obj
    if d.line then
      o:SetStartPoint("TOPLEFT", canvas, x1 + ux * s, -(y1 + uy * s))
      o:SetEndPoint("TOPLEFT", canvas, x1 + ux * e, -(y1 + uy * e))
      if o.SetThickness then o:SetThickness(th) end
      if o.SetColorTexture then o:SetColorTexture(cr, cg, cb, alpha) end
    else                                              -- repli : un point par tiret
      local m = (s + e) / 2
      o:ClearAllPoints()
      o:SetPoint("CENTER", canvas, "TOPLEFT", x1 + ux * m, -(y1 + uy * m))
      o:SetSize(th + 2, th + 2)
      o:SetVertexColor(cr, cg, cb, alpha)
    end
    if not o:IsShown() then o:Show() end
  end
  return di
end

function PD.DrawMap()
  local wm, sc, child = wmParts()
  if not (wm and ensureCanvas()) then return end
  PD.mapRedraws = PD.mapRedraws + 1
  local steps = routeSig()
  if not (steps and opt("map") and PD:IsEnabled() and not PD.GuidanceOff()) then PD.ClearMap() return end
  local mapID = shownMapID(wm)
  local vl, vb, vw, vh = rectOf(sc)
  local cl, cb, cw, ch = rectOf(child)
  if not (mapID and vl and cl) then PD.ClearMap() return end
  if locked(view) then return end
  view:ClearAllPoints()
  view:SetPoint("BOTTOMLEFT", G("UIParent"), "BOTTOMLEFT", vl, vb)
  view:SetSize(vw, vh)
  canvas:ClearAllPoints()
  canvas:SetPoint("BOTTOMLEFT", G("UIParent"), "BOTTOMLEFT", cl, cb)
  canvas:SetSize(cw, ch)
  local st = PD.Stops()
  local np = 0
  for i = 1, #(st or {}) do
    local e = st[i]
    local x, y = onMap(mapID, e.place, cw, ch)
    np = np + 1
    local k = np * 3
    pts[k - 2], pts[k - 1], pts[k] = x or false, y or false, e.kind
    if np >= MAX_STOPS then break end
  end
  local di, bi, segs = 0, 0, 0
  local lx, ly                                       -- dernier arrêt placé (ceux sans position sont sautés)
  -- (QA-QUEST F18) mêmes numéros que l'onglet et la page Trajet : objectifs du même coin = un seul badge
  local R = T.Route
  local okG, gr = false, nil
  if R and R.Groups and st then okG, gr = pcall(R.Groups, st, np) end
  if not (okG and type(gr) == "table") then gr = nil end
  for i = 1, np do
    local x, y, kind = pts[i * 3 - 2], pts[i * 3 - 1], pts[i * 3]
    if x and gr and i > 1 and gr[i] == gr[i - 1] then   -- même arrêt que le précédent : le trait, pas de 2e badge
      if lx then
        di = drawSegment(di, lx, ly, x, y, segs == 0 and 0.95 or 0.75)
        segs = segs + 1
      end
      lx, ly = x, y
    elseif x then
      if lx then
        di = drawSegment(di, lx, ly, x, y, segs == 0 and 0.95 or 0.75)
        segs = segs + 1
      end
      lx, ly = x, y
      bi = bi + 1
      local b = badge(bi)
      local sz = (i == 1) and 20 or 16
      local cr, cg, cb2 = color()
      b.ring:ClearAllPoints()
      b.ring:SetPoint("CENTER", canvas, "TOPLEFT", x, -y)
      b.ring:SetSize(sz + 4, sz + 4)
      b.ring:SetVertexColor(cr, cg, cb2, 1)
      b.bg:ClearAllPoints()
      b.bg:SetPoint("CENTER", canvas, "TOPLEFT", x, -y)
      b.bg:SetSize(sz, sz)
      b.bg:SetVertexColor(0.05, 0.05, 0.05, 0.9)
      b.text:ClearAllPoints()
      b.text:SetPoint("CENTER", b.bg, "CENTER", 0, 0)
      b.text:SetText(tostring(gr and gr[i] or i))
      b.ring:Show() b.bg:Show() b.text:Show()
      local ic = ICON[kind]
      if ic then
        b.icon:SetTexture(ic)
        b.icon:ClearAllPoints()
        b.icon:SetPoint("LEFT", b.bg, "RIGHT", 1, 0)
        b.icon:SetSize(14, 14)
        b.icon:Show()
      elseif b.icon:IsShown() then
        b.icon:Hide()
      end
    end
  end
  hideMapFrom(di + 1, bi + 1)
  PD.segments, PD.badgesShown, PD.dashesShown = segs, bi, di
  -- calque juste au-dessus de la carte (même strate, niveau au-dessus du canevas)
  try(view.SetFrameStrata, view, try(wm.GetFrameStrata, wm) or "HIGH")
  try(view.SetFrameLevel, view, min(9000, (num(try(wm.GetFrameLevel, wm)) or 1) + 200))
  if not view:IsShown() then view:Show() end
end

-- ≤ 5 Hz tant que la carte est ouverte : redessine si la carte affichée / le zoom / le trajet a changé
function PD.MapTick(force)
  local t = now()
  if not force and t - mapState.at < STEP then return end
  mapState.at = t
  local wm, sc, child = wmParts()
  if not (wm and try(wm.IsShown, wm)) then PD.MapHidden() return end
  local id = shownMapID(wm)
  local l, b, w, h = rectOf(child)
  local vl, vb, vw, vh = rectOf(sc)
  local steps, cur, len = routeSig()
  local m = mapState
  if force or m.dirty or id ~= m.id or l ~= m.l or b ~= m.b or w ~= m.w or h ~= m.h or vl ~= m.vl or vb ~= m.vb
      or vw ~= m.vw or vh ~= m.vh or steps ~= m.steps or cur ~= m.cur or len ~= m.len then
    m.id, m.l, m.b, m.w, m.h, m.vl, m.vb, m.vw, m.vh = id, l, b, w, h, vl, vb, vw, vh
    m.steps, m.cur, m.len, m.dirty = steps, cur, len, false
    PD.DrawMap()
  end
end
function PD.MapShown()
  mapState.dirty = true
  if not ensureCanvas() then return end
  if not locked(view) then view:Show() end
  PD.MapTick(true)
end
function PD.MapHidden()
  if view and view:IsShown() and not locked(view) then view:Hide() end
end

-- fournisseur de données de la carte (modèle Blizzard) ; sinon HookScript OnShow / OnHide
local function attachMap()
  if PD._mapHooked then return true end
  local wm = wmParts()
  if not wm then return false end
  local mk, mixin = G("CreateFromMixins"), G("MapCanvasDataProviderMixin")
  if type(wm.AddDataProvider) == "function" and type(mk) == "function" and type(mixin) == "table" then
    local p = try(mk, mixin)
    if type(p) == "table" then
      p.RefreshAllData = function() mapState.dirty = true end
      p.RemoveAllData = function() PD.ClearMap() end
      p.OnMapChanged = function() mapState.dirty = true end
      p.OnShow = function() PD.MapShown() end
      p.OnHide = function() PD.MapHidden() end
      p.OnCanvasScaleChanged = function() mapState.dirty = true end
      p.OnCanvasPanChanged = function() mapState.dirty = true end
      p.OnCanvasSizeChanged = function() mapState.dirty = true end
      if pcall(wm.AddDataProvider, wm, p) then
        PD._mapHooked, PD.mapEngine, PD.provider = true, "provider", p
        return true
      end
    end
  end
  if type(wm.HookScript) == "function" then
    pcall(wm.HookScript, wm, "OnShow", function() if PD._active then PD.MapShown() end end)
    pcall(wm.HookScript, wm, "OnHide", function() PD.MapHidden() end)
    PD._mapHooked, PD.mapEngine = true, "hook"
    return true
  end
  return false
end
PD.AttachMap = attachMap

-- ------------------------------------------------------------------ cycle de vie
local driver
function PD.OnUpdate() PD.MinimapTick() end
function PD:OnEnable()
  PD._active = true
  attachMap()
  local mm = minimap()
  if mm and G("CreateFrame") and not driver and not inCombat() then
    driver = G("CreateFrame")("Frame", nil, mm)    -- OnUpdate seulement quand la minimap est visible
    driver:SetSize(1, 1)
    try(driver.EnableMouse, driver, false)
    PD.driver = driver
  end
  if driver then driver:SetScript("OnUpdate", PD.OnUpdate) driver:Show() end
  ensureTrail()
  ensureCanvas()
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA", "MINIMAP_UPDATE_ZOOM", "CVAR_UPDATE" }) do
    self:RegisterEvent(ev, function() last.n, last.stopsAt, last.pollAt, mapState.dirty = nil, -100, -100, true end)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() attachMap() ensureTrail() ensureCanvas() end)
  local wm = wmParts()
  if wm and try(wm.IsShown, wm) then PD.MapShown() end
end
function PD:OnDisable()
  PD._active = false
  if driver then driver:SetScript("OnUpdate", nil) end
  PD.ClearMinimap()
  PD.ClearMap()
  PD.MapHidden()
end
function PD:AutoRefresh() mapState.dirty = true end
function PD:OnOptionChanged()
  last.n, last.stopsAt, last.pollAt, mapState.dirty, wantAt = nil, -100, -100, true, -100
  PD.MinimapTick(true)
  if view and view:IsShown() then PD.MapTick(true) end
end

function PD.Status()
  return ("Chemin : carte %d arrêt(s), %d segment(s) (%s, %s) · minimap %d point(s)"):format(PD.badgesShown, PD.segments,
    PD.mapEngine or "carte absente", PD._lines and "lignes" or "points", PD.dotsShown)
end
if T.RegisterSlash then
  T:RegisterSlash("chemin", function() PD:Print(PD.Status()) end, "chemin : le trajet dessiné (statut)")
end
