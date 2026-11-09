-- TreeAI — module PathDraw (agent PATH, 4.2 ROUTE42) : VOIR le chemin calculé par l'Itinéraire (Modules/Route.lua + Planner).
--  * Carte du monde : le trajet DANS la carte, comme une épingle du jeu — notre calque est un enfant du canevas de la carte
--    (WorldMapFrame.ScrollContainer.Child) : il glisse et zoome avec elle, il est coupé au bord de la carte par son cadre
--    défilant, il s'efface quand la carte se ferme. Son échelle = l'inverse de celle du canevas (traits et pastilles de
--    taille constante à l'écran) ; repositionné seulement quand la carte affichée, le zoom ou le trajet change (post-crochets
--    du jeu sur le canevas : hooksecurefunc(Child, "SetScale"), OnMapChanged, OnShow — aucun OnUpdate ; rien carte fermée). AUCUN AddDataProvider ni
--    AcquirePin : sur Forever, la manette parcourt les épingles de la carte depuis du code sécurisé et un fournisseur
--    d'addon la contamine (étude des addons, §1). Aucun cadre sur UIParent par-dessus la carte.
--    Dessin à la Shortest Path (écrit par nous) : pointillés par étape (marche jaune / vert réglable, vol bleu, bateau /
--    zeppelin turquoise, ascenseur / tram violet, foyer : rien — on se téléporte), étape en cours (depuis ta position)
--    plus épaisse et pleine, petites flèches de sens tous les ~90 px, pastilles numérotées comme l'onglet (R.Groups).
--    Segments : objets Line (CreateLine + SetStartPoint / SetEndPoint), sinon des points (textures).
--  * Minimap : les 1–2 prochaines étapes en traînée de points qui tourne avec la minimap (même projection que
--    Modules/MinimapPins.lua : MP.YardsRadius / rotation / forme). ≤ 5 Hz, seulement si le joueur a bougé
--    (ou tourné, zoomé, ou si le trajet a changé) ; textures réutilisées (réserve), aucune table créée par passe.
--  * Rien n'est dessiné sans trajet actif (Itinéraire en pause = rien). Option « Guidage » respectée.
-- Taint : aucun cadre protégé, aucun hook de cadre sécurisé, aucune fonction protégée ; cadres créés hors combat.
-- T.MapCanvas (ci-dessous) sert aussi à QuestPins et CityRun. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
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
local IDLE = 1                          -- (4.0.1) immobile : 1 coup d'œil / s (vitesse + cap, deux lectures)
local SLOW = 15                         -- relecture de la position même immobile (téléportations : les événements de
                                        -- zone / d'écran de chargement relancent déjà tout)
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

-- ------------------------------------------------------------------ (4.2 ROUTE42) calques DANS la carte du monde
-- MC.Layer() : un cadre à nous, enfant du canevas (ScrollContainer.Child), souris coupée (seules nos épingles la
-- prennent) ; MC.Fit(layer) : échelle = 1 / échelle du canevas, taille = canevas × échelle → coordonnées du calque = pixels
-- d'écran du canevas (épingles et traits de taille constante, ils suivent glisser / zoom sans rien faire) ; niveau juste
-- au-dessus du fond de carte (canevas + MC.LEVEL), sous les épingles du jeu ; MC.Key = ce qui oblige à reposer (carte
-- affichée, zoom, taille). Le cadre défilant de la carte coupe ce qui dépasse.
local MC = type(T.MapCanvas) == "table" and T.MapCanvas or {}
T.MapCanvas = MC
MC.LEVEL = 4
function MC.Parts()
  local wm = G("WorldMapFrame")
  if type(wm) ~= "table" then return nil end
  local sc = wm.ScrollContainer
  local child = type(sc) == "table" and sc.Child or nil
  if type(child) ~= "table" or type(child.GetWidth) ~= "function" then return nil end
  return wm, sc, child
end
function MC.Layer()
  local _, _, child = MC.Parts()
  local CF = G("CreateFrame")
  if not (child and CF) then return nil end
  local f = try(CF, "Frame", nil, child)
  if not f then return nil end
  try(f.EnableMouse, f, false)
  f:SetPoint("TOPLEFT", child, "TOPLEFT", 0, 0)
  f:SetSize(1, 1)
  f._mc = true
  return f
end
function MC.MapID()
  local wm = MC.Parts()
  if not wm then return nil end
  local id = num(try(wm.GetMapID, wm))
  if id then return id end
  local CM = G("C_Map")
  return CM and num(try(CM.GetBestMapForUnit, "player")) or nil
end
--- → mapID, largeur, hauteur (unités du calque = pixels d'écran du canevas), échelle du canevas ; nil si illisible
function MC.Fit(f)
  local _, _, child = MC.Parts()
  if not (child and f) then return nil end
  local cs = num(try(child.GetScale, child)) or 1
  if cs <= 0.01 then cs = 1 end
  local cw, ch = num(try(child.GetWidth, child)), num(try(child.GetHeight, child))
  if not (cw and ch and cw > 0 and ch > 0) then return nil end
  if f._cs ~= cs then try(f.SetScale, f, 1 / cs) f._cs = cs end
  if f._w ~= cw * cs or f._h ~= ch * cs then f:SetSize(cw * cs, ch * cs) f._w, f._h = cw * cs, ch * cs end
  local lv = min(9000, (num(try(child.GetFrameLevel, child)) or 1) + MC.LEVEL + (f._lvl or 0))
  if f._lv ~= lv then try(f.SetFrameLevel, f, lv) f._lv = lv end
  return MC.MapID(), cw * cs, ch * cs, cs
end
--- ce qui oblige à reposer le calque (aucune table) : carte affichée, zoom, largeur, hauteur du canevas
function MC.Key()
  local _, _, child = MC.Parts()
  if not child then return nil end
  return MC.MapID() or 0, num(try(child.GetScale, child)) or 1, num(try(child.GetWidth, child)) or 0,
    num(try(child.GetHeight, child)) or 0
end
-- prévenus seulement quand la carte change : affichée, autre carte, zoom (le jeu appelle Child:SetScale à chaque pas de
-- zoom), taille du canevas — par des post-crochets du jeu (hooksecurefunc / HookScript : n'altèrent pas son code), aucun
-- OnUpdate ; glisser la carte ne demande rien (le calque est dans le canevas). Repli sans hooksecurefunc : lecture par image
-- carte ouverte (MC.polling).
MC.subs = MC.subs or {}
local function fire() for _, f in ipairs(MC.subs) do pcall(f) end end
MC.Fire = fire
function MC.Hook()
  local wm, _, child = MC.Parts()
  if not child then return false end
  if MC.hooked == child then return true end
  local hs = G("hooksecurefunc")
  if type(hs) ~= "function" then MC.polling = true return false end
  pcall(hs, child, "SetScale", fire)
  if type(wm.OnMapChanged) == "function" then pcall(hs, wm, "OnMapChanged", fire) end
  if type(wm.HookScript) == "function" then pcall(wm.HookScript, wm, "OnShow", fire) end
  if type(child.HookScript) == "function" then pcall(child.HookScript, child, "OnSizeChanged", fire) end
  MC.hooked = child
  return true
end
--- fn() appelé quand la carte change ; layer = le calque du module (repli : son OnUpdate, carte ouverte seulement)
function MC.Watch(layer, fn)
  local seen = false
  for _, f in ipairs(MC.subs) do if f == fn then seen = true end end
  if not seen then MC.subs[#MC.subs + 1] = fn end
  if not MC.Hook() and layer then layer:SetScript("OnUpdate", fn) end
end
--- le calque appartient-il bien à la carte (jamais à UIParent) ? (tests, /tai chemin)
function MC.InMap(f)
  local _, _, child = MC.Parts()
  local p = f and try(f.GetParent, f)
  while p do
    if p == child then return true end
    p = try(p.GetParent, p)
  end
  return false
end

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
  if PD.edge and PD.edge:IsShown() then PD.edge:Hide() end
  PD.edgeShown = false
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
-- (ROUTEFIX) 1er trajet à pied : le chemin APPRIS (PathLearn : rampe, virages) quand la ligne droite est fausse — la
-- traînée suit les points de passage, comme la flèche « par le chemin »
local MAX_PATH = 24
local function learnedPath(c, n, w, p)
  local PLm = T.PathLearn
  if not (PLm and PLm.Path and n and p and p.n) then return nil end
  local ok, pts = pcall(PLm.Path, { c = c, n = n, w = w }, { c = c, n = p.n, w = p.w })
  return ok and type(pts) == "table" and #pts > 1 and pts or nil
end
PD.LearnedPath = learnedPath
local function readLegs(c, pn, pw)
  nLegs = 0
  local st = PD.Stops()
  PD.npcMini = st and st[1] and PD.IsNpcStop(st[1]) or false
  if not st then return end
  PD.learnedPts = 0
  for i = 1, #st do
    local e = st[i]
    -- (QA-QUEST F19) pierre de foyer : on se téléporte, aucune traînée vers l'auberge ; vol / bateau : la traînée
    -- mène au départ (maître de vol, quai) et s'arrête là (on ne marche pas jusqu'à l'arrivée)
    if e.kind == "hs" then break end
    local dep = not e.step and e.from or nil
    local p = dep or e.place
    if p and p.n and p.w and p.c == c then
      local lp = nLegs == 0 and learnedPath(c, pn, pw, p) or nil
      if lp then                                     -- points du chemin appris, sans le dernier (l'arrêt, ajouté ci-dessous)
        local k = 0
        for j = 1, #lp - 1 do
          if k >= MAX_PATH then break end
          legs[nLegs * 2 + 1], legs[nLegs * 2 + 2] = lp[j].n, lp[j].w
          nLegs, k = nLegs + 1, k + 1
        end
        PD.learnedPts = k
      end
      legs[nLegs * 2 + 1], legs[nLegs * 2 + 2] = p.n, p.w
      nLegs = nLegs + 1
      if dep or nLegs >= 2 + (PD.learnedPts or 0) then break end
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
              local first = li <= 1 + (PD.learnedPts or 0)
              local nc = first and PD.npcMini and PD.NpcColor() or nil
              if nc then d:SetVertexColor(nc[1], nc[2], nc[3], 1)
              else d:SetVertexColor(cr, cg, cb, first and 0.95 or 0.6) end
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
  -- (4.2 ROUTE42) arrêt 1 hors de la minimap : une flèche sur le bord, tournée vers lui (comme les épingles du jeu)
  local li = (PD.learnedPts or 0) + 1
  local edge = false
  if nLegs >= li and trail then
    local bx, by = PD.Project(legs[li * 2 - 1] - n, legs[li * 2] - w, v.facing, v.rotate, k)
    local d = sqrt(bx * bx + by * by)
    local out = v.square and (abs(bx) > r or abs(by) > r) or d > r
    if out and d > 0 then
      local e = PD.edge
      if not e then
        e = trail:CreateTexture(nil, "OVERLAY")
        e:SetTexture("Interface\\Minimap\\MinimapArrow")
        PD.edge = e
      end
      local rr = r - 4
      local ex, ey = bx / d * rr, by / d * rr
      if v.square then local m = max(abs(bx), abs(by)) ex, ey = bx / m * rr, by / m * rr end
      e:ClearAllPoints()
      e:SetPoint("CENTER", trail, "CENTER", ex, ey)
      e:SetSize(12, 12)
      if e.SetRotation then pcall(e.SetRotation, e, (math.atan2 and math.atan2(by, bx) or 0) - math.pi / 2) end
      e:SetVertexColor(cr, cg, cb, 1)
      if not e:IsShown() then e:Show() end
      edge = true
    end
  end
  if not edge and PD.edge and PD.edge:IsShown() then PD.edge:Hide() end
  PD.edgeShown = edge
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
  if view and view:IsVisible() then PD.MapTick() end   -- carte ouverte : l'étape en cours suit le joueur (≤ 5 Hz)
  if force then wantAt = -100 end
  local P, R = T.Planner, T.Route
  local steps, cur = P and P.steps, P and P.cursor
  local len = steps and #steps
  local paused = (R and R.char and R.char.paused) and true or false
  local routeChanged = steps ~= last.steps or cur ~= last.cur or len ~= last.len or paused ~= last.paused
  -- bouge-t-on ? (vitesse / cap : bon marché) ; sinon relecture lente de la position
  local gs, gf = rawget(_G, "GetUnitSpeed"), rawget(_G, "GetPlayerFacing")
  local speed = gs and num((gs("player"))) or 0
  local facing = gf and num((gf())) or nil
  local moving = speed > 0 or facing ~= last.facing
  last.step = moving and STEP or IDLE     -- immobile : 1 passe / s suffit (bon marché)
  -- (4.0.1) en vol (taxi) : pas de traînée, rien à redessiner 5 fois par seconde ; elle revient à l'atterrissage
  local onTaxi = rawget(_G, "UnitOnTaxi")
  if onTaxi and try(onTaxi, "player") then
    if PD.dotsShown > 0 then PD.ClearMinimap() end
    last.step, last.n = IDLE, nil
    return
  end
  -- (4.0.1) immobile, même trajet, même cap : rien d'autre (ni options, ni position, ni arrêts relus) ; 3 fois de
  -- suite → l'OnUpdate s'endort (réveil : le joueur bouge / tourne, un événement, une option)
  if not (force or routeChanged or moving or t - last.pollAt >= SLOW) then
    PD.idleN = PD.idleN + 1
    if PD.idleN >= 3 then PD.Sleep() end
    return
  end
  PD.idleN = 0
  last.paused = paused
  if not (steps and cur and cur <= len) or paused or not wantMinimap(t) then
    if PD.dotsShown > 0 then PD.ClearMinimap() end
    last.steps, last.cur, last.len = steps, cur, len
    return
  end
  if not (trail or ensureTrail()) then return end
  last.pollAt = t
  local n, w, c = playerWorld()
  if not n then if PD.dotsShown > 0 then PD.ClearMinimap() end return end
  -- (4.0.1) arrêts relus quand le trajet change, ou toutes les 3 s si le joueur s'est déplacé (les transports en
  -- dépendent) ; immobile : jamais (avant : la liste de l'Itinéraire relue toutes les 3 s pour rien)
  if routeChanged or c ~= last.c or (t - last.stopsAt >= 3 and not (last.ln and abs(n - last.ln) < 5 and abs(w - last.lw) < 5)) then
    readLegs(c, n, w)
    last.steps, last.cur, last.len, last.stopsAt, last.ln, last.lw = steps, cur, len, t, n, w
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

-- ------------------------------------------------------------------ carte du monde : calque DANS la carte (MC)
local view, canvas = nil, nil            -- (4.2 ROUTE42) un seul cadre : enfant du canevas de la carte (MC.Layer)
local dashes, badges, arrows = {}, {}, {}
PD.dashes, PD.badges, PD.arrows = dashes, badges, arrows
PD.segments, PD.badgesShown, PD.dashesShown, PD.arrowsShown, PD.mapRedraws = 0, 0, 0, 0, 0
local mapState = { id = nil, cs = nil, w = nil, h = nil, steps = nil, cur = nil, len = nil, gen = nil, px = nil, py = nil,
  dirty = true }
local pts = {}                            -- points dessinés : x, y, sorte, n° de pastille — réutilisée
-- couleurs par sorte d'étape (la marche = la couleur choisie) ; l'étape en cours, plus épaisse et pleine
local KIND_RGB = { fly = { 0.35, 0.65, 1 }, boat = { 0.2, 0.9, 0.85 }, zeppelin = { 0.2, 0.9, 0.85 },
  elevator = { 0.75, 0.45, 1 }, tram = { 0.75, 0.45, 1 } }
PD.KIND_RGB = KIND_RGB
-- (4.2 ROUTE42) « PNJ à voir » : l'arrêt en cours est un PNJ (rendre, prendre, marchand, maître, maître de vol, auberge…)
-- → l'étape qui y mène change de couleur TOUT DE SUITE (or ; orange si le chemin est déjà jaune), carte et minimap, et sa
-- pastille grossit ; retour à la couleur normale quand l'arrêt change
local NPC_K = { pick = true, turn = true, vendor = true, train = true, fly = true, fp = true, hs = true, bank = true, mail = true }
local NPC_GOLD, NPC_ORANGE = { 1, 0.82, 0.1 }, { 1, 0.5, 0.05 }
function PD.NpcColor()
  if tonumber(PD.db and PD.db.color) == 2 then return NPC_ORANGE end
  return NPC_GOLD
end
--- l'entrée st (R:Stops) mène-t-elle à un PNJ ? (transport : son maître de vol / quai d'abord)
function PD.IsNpcStop(e)
  if type(e) ~= "table" then return false end
  if e.kind == "fly" then return true end                       -- le maître de vol
  local s = e.step
  if not s then return false end
  if s.buy then return true end                                 -- objet à acheter : le marchand
  return NPC_K[s.k or ""] and true or false
end
local ARROW = "Interface\\Minimap\\MinimapArrow"
PD.ARROW_PX = 90

local function ensureCanvas()
  if view then return view end
  if inCombat() then return nil end
  view = MC.Layer()
  if not view then return nil end
  view._lvl = 2                                    -- au-dessus des icônes de quêtes (QuestPins), sous les épingles du jeu
  canvas = view
  -- prévenu par la carte (affichée, autre carte, zoom) : un dessin seulement si quelque chose a changé ; le trajet qui
  -- change ou le joueur qui marche : PD.Now / la passe de la minimap (≤ 5 Hz en marchant) redessinent si la carte est ouverte
  PD.view, PD.canvas = view, canvas
  MC.Watch(view, function() if view:IsVisible() then PD.MapTick() end end)
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
local function arrow(i)
  local a = arrows[i]
  if a then return a end
  if i > 80 then return nil end
  a = canvas:CreateTexture(nil, "OVERLAY")
  a:SetTexture(ARROW)
  a:SetSize(10, 10)
  arrows[i] = a
  return a
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
local function hideMapFrom(di, bi, ai)
  for i = di, #dashes do local o = dashes[i].obj if o:IsShown() then o:Hide() end end
  for i = bi, #badges do
    local b = badges[i]
    if b.bg:IsShown() then b.ring:Hide() b.bg:Hide() b.text:Hide() b.icon:Hide() end
  end
  for i = ai or 1, #arrows do if arrows[i]:IsShown() then arrows[i]:Hide() end end
end
function PD.ClearMap()
  if not canvas then return end
  hideMapFrom(1, 1, 1)
  PD.segments, PD.badgesShown, PD.dashesShown, PD.arrowsShown = 0, 0, 0, 0
end

-- position (unités du calque, origine en haut à gauche) d'un lieu sur la carte affichée ; nil s'il est ailleurs
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

-- pointillés d'une étape (pleins pour l'étape en cours) + flèches de sens ; → index des tirets / flèches utilisés
local function drawSegment(di, ai, x1, y1, x2, y2, alpha, rgb, current)
  local len = sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2)
  if len < 1 then return di, ai end
  local th = thick() + (current and 1 or 0)
  local cr, cg, cb
  if rgb then cr, cg, cb = rgb[1], rgb[2], rgb[3] else cr, cg, cb = color() end
  local period = 10 + th * 3                        -- tiret + trou
  local n = floor(len / period)
  if n > 60 then n, period = 60, len / 60 end       -- très long trajet : tirets plus espacés
  if n < 1 then n, period = 1, len end
  local on = period * (current and 0.8 or 0.55)
  local ux, uy = (x2 - x1) / len, (y2 - y1) / len
  for k = 0, n - 1 do
    local d = dash(di + 1)
    if not d then break end
    di = di + 1
    local s0, e = k * period, min(len, k * period + on)
    local o = d.obj
    if d.line then
      o:SetStartPoint("TOPLEFT", canvas, x1 + ux * s0, -(y1 + uy * s0))
      o:SetEndPoint("TOPLEFT", canvas, x1 + ux * e, -(y1 + uy * e))
      if o.SetThickness then o:SetThickness(th) end
      if o.SetColorTexture then o:SetColorTexture(cr, cg, cb, alpha) end
    else                                              -- repli : un point par tiret
      local m = (s0 + e) / 2
      o:ClearAllPoints()
      o:SetPoint("CENTER", canvas, "TOPLEFT", x1 + ux * m, -(y1 + uy * m))
      o:SetSize(th + 2, th + 2)
      o:SetVertexColor(cr, cg, cb, alpha)
    end
    if not o:IsShown() then o:Show() end
  end
  -- flèches de sens : une tous les PD.ARROW_PX (au moins une au milieu d'une étape assez longue)
  if len >= 40 then
    local na = max(1, floor(len / PD.ARROW_PX))
    local ang = math.atan2 and math.atan2(-(y2 - y1), x2 - x1) or 0     -- angle à l'écran (y vers le haut)
    for k = 1, na do
      local a = arrow(ai + 1)
      if not a then break end
      ai = ai + 1
      local t = (k - 0.5) / na
      a:ClearAllPoints()
      a:SetPoint("CENTER", canvas, "TOPLEFT", x1 + (x2 - x1) * t, -(y1 + (y2 - y1) * t))
      a:SetSize(th + 8, th + 8)
      if a.SetRotation then pcall(a.SetRotation, a, ang - math.pi / 2) end   -- la flèche du jeu pointe vers le haut
      a:SetVertexColor(cr, cg, cb, min(1, alpha + 0.1))
      if not a:IsShown() then a:Show() end
    end
  end
  return di, ai
end

-- position du joueur sur la carte affichée (départ de l'étape en cours), nil s'il est ailleurs
local function playerOnMap(mapID, W, H)
  local Gp = T.Geo and T.Geo.Player
  if not Gp then return nil end
  local m, x, y = Gp()
  if not (m and x) then return nil end
  if m == mapID then return x / 100 * W, y / 100 * H end
  local Rt = T.Route
  local w = Rt and Rt.World and Rt.World({ map = m, x = x, y = y })
  if not w then return nil end
  return onMap(mapID, w, W, H)
end

function PD.DrawMap()
  if not ensureCanvas() then return end
  PD.mapRedraws = PD.mapRedraws + 1
  local steps = routeSig()
  if not (steps and opt("map") and PD:IsEnabled() and not PD.GuidanceOff()) then PD.ClearMap() return end
  local mapID, W, H = MC.Fit(view)
  if not (mapID and W) then PD.ClearMap() return end
  if locked(view) then return end
  local st = PD.Stops()
  -- points du dessin : départ du transport (maître de vol, quai) puis son arrivée ; un arrêt de quête ; pastilles
  local R = T.Route
  local okG, gr = false, nil
  if R and R.Groups and st then okG, gr = pcall(R.Groups, st, min(#st, MAX_STOPS)) end
  if not (okG and type(gr) == "table") then gr = nil end
  local np = 0
  local function put(x, y, kind, num2)
    np = np + 1
    local k = np * 4
    pts[k - 3], pts[k - 2], pts[k - 1], pts[k] = x or false, y or false, kind, num2 or false
  end
  local npc = st and st[1] and PD.IsNpcStop(st[1]) or false
  PD.npcLeg = npc
  local px, py = playerOnMap(mapID, W, H)
  put(px, py, "me", false)
  for i = 1, min(#(st or {}), MAX_STOPS) do
    local e = st[i]
    if e.from and e.kind ~= "quest" and e.kind ~= "hs" then
      local fx, fy = onMap(mapID, e.from, W, H)
      put(fx, fy, "walk", false)
    end
    local x, y = onMap(mapID, e.place, W, H)
    local same = gr and i > 1 and gr[i] == gr[i - 1]
    put(x, y, e.kind, (not same) and (gr and gr[i] or i) or false)
  end
  local di, bi, ai, segs = 0, 0, 0, 0
  local lx, ly, firstSeg = nil, nil, true
  for i = 1, np do
    local x, y, kind, nb = pts[i * 4 - 3], pts[i * 4 - 2], pts[i * 4 - 1], pts[i * 4]
    if x then
      if lx and kind ~= "hs" then                     -- foyer : on se téléporte, aucun trait jusqu'à l'auberge
        local rgb = (firstSeg and npc and not KIND_RGB[kind]) and PD.NpcColor() or KIND_RGB[kind]
        di, ai = drawSegment(di, ai, lx, ly, x, y, firstSeg and 1 or 0.75, rgb, firstSeg)
        segs = segs + 1
        firstSeg = false
      elseif lx then
        firstSeg = false
      end
      lx, ly = x, y
      if nb then
        bi = bi + 1
        local b = badge(bi)
        local sz = (nb == 1) and ((npc and 24) or 20) or 16
        local cr, cg, cb2 = color()
        local kr = KIND_RGB[kind]
        if nb == 1 and npc and not kr then kr = PD.NpcColor() end
        if kr then cr, cg, cb2 = kr[1], kr[2], kr[3] end
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
        b.text:SetText(tostring(nb))
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
  end
  hideMapFrom(di + 1, bi + 1, ai + 1)
  PD.segments, PD.badgesShown, PD.dashesShown, PD.arrowsShown = segs, bi, di, ai
  if not view:IsShown() then view:Show() end
end

-- appelé par la carte (affichée, zoom, autre carte), le trajet (PD.Now) ou la passe de la minimap carte ouverte : relit la
-- carte affichée, le zoom, la taille et le trajet ; redessine seulement si l'un d'eux a changé (glisser ne demande rien)
function PD.MapTick(force)
  if not PD._active then if PD.segments > 0 or PD.badgesShown > 0 then PD.ClearMap() end return end
  local id, cs, cw, ch = MC.Key()
  if not id then return end
  local steps, cur, len = routeSig()
  local m = mapState
  local Gp = T.Geo and T.Geo.Player
  local _, px, py
  if Gp and steps then _, px, py = Gp() end
  local moved = px and (not m.px or abs(px - m.px) + abs(py - m.py) > 0.15)
  if force or m.dirty or id ~= m.id or cs ~= m.cs or cw ~= m.w or ch ~= m.h or steps ~= m.steps or cur ~= m.cur
      or len ~= m.len or moved then
    m.id, m.cs, m.w, m.h, m.steps, m.cur, m.len, m.dirty = id, cs, cw, ch, steps, cur, len, false
    m.px, m.py = px, py
    PD.DrawMap()
  end
end
function PD.MapShown()
  mapState.dirty = true
  if not ensureCanvas() then return end
  PD.MapTick(true)
end
function PD.MapHidden() end                     -- le calque se cache avec la carte (enfant de son canevas)

-- (4.2 ROUTE42) plus de fournisseur de données ni de crochet sur la carte : le calque est un enfant de son canevas
local function attachMap()
  if PD._mapHooked then return true end
  if not ensureCanvas() then return false end
  PD._mapHooked, PD.mapEngine = true, "canvas"
  return true
end
PD.AttachMap = attachMap

-- ------------------------------------------------------------------ cycle de vie
local driver
local acc = 0
PD.idleN = 0
-- (4.0.1) par image : une addition (pas d'appel) tant que la prochaine passe n'est pas due
function PD.OnUpdate(_, el)
  acc = acc + (el or 0)
  if acc < last.step then return end
  acc = 0
  PD.MinimapTick()
end
-- (4.0.1) immobile : plus d'OnUpdate du tout ; réveillé par le coup d'œil du cœur (T:OnMotion), les événements de
-- zone / zoom / réglage, le cycle des :AutoRefresh() (il tourne dès qu'un événement arrive) et les options
function PD.Sleep()
  if driver and not PD.asleep then driver:SetScript("OnUpdate", nil) PD.asleep = true end
end
function PD.Wake()
  PD.idleN = 0
  if driver and PD.asleep and PD._active then
    PD.asleep, acc, last.at = false, 1, -100   -- prochaine image : une passe complète
    driver:SetScript("OnUpdate", PD.OnUpdate)
  end
end
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
  if driver then driver:SetScript("OnUpdate", PD.OnUpdate) driver:Show() PD.asleep, PD.idleN = false, 0 end
  ensureTrail()
  ensureCanvas()
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA", "MINIMAP_UPDATE_ZOOM", "CVAR_UPDATE" }) do
    self:RegisterEvent(ev, function() last.n, last.ln, last.stopsAt, last.pollAt, mapState.dirty = nil, nil, -100, -100, true PD.Wake() end)
  end
  if T.OnMotion then T:OnMotion(PD, PD.Wake) end
  self:On("QUESTS_UPDATED", function() PD.Wake() end)
  self:On("PLAN_BUILT", function() PD.Now() end)        -- (4.2) trajet refait : carte et minimap tout de suite
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() attachMap() ensureTrail() ensureCanvas() end)
  local wm = MC.Parts()
  if wm and try(wm.IsShown, wm) then PD.MapShown() end
end
function PD:OnDisable()
  PD._active = false
  if driver then driver:SetScript("OnUpdate", nil) end
  PD.asleep = false
  PD.ClearMinimap()
  PD.ClearMap()
  PD.MapHidden()
end
-- (4.0.1) carte du monde fermée : rien ; ouverte, ses propres événements (zoom, déplacement, trajet) la redessinent
PD.autoIdleSafe = true
function PD:AutoRefresh()
  mapState.dirty = true
  PD.Wake()
  if view and view:IsVisible() then PD.MapTick() end
end
--- (4.2 ROUTE42) l'arrêt a changé (Route:OnPlanStep) : carte et minimap redessinées TOUT DE SUITE (couleur « PNJ à voir »)
function PD.Now()
  if not PD._active then return end
  last.n, last.ln, last.stopsAt, last.pollAt, mapState.dirty, wantAt = nil, nil, -100, -100, true, -100
  PD.Wake()
  pcall(PD.MinimapTick, true)
  if view and view:IsVisible() then pcall(PD.MapTick, true) end
end
function PD:OnOptionChanged()
  PD.Wake()
  last.n, last.ln, last.stopsAt, last.pollAt, mapState.dirty, wantAt = nil, nil, -100, -100, true, -100
  PD.MinimapTick(true)
  if view and view:IsVisible() then PD.MapTick(true) end
end

function PD.Status()
  return ("Chemin : carte %d arrêt(s), %d segment(s) (%s, %s%s) · minimap %d point(s)"):format(PD.badgesShown, PD.segments,
    PD.mapEngine or "carte absente", PD._lines and "lignes" or "points", (view and MC.InMap(view)) and ", dans la carte" or "",
    PD.dotsShown)
end
if T.RegisterSlash then
  T:RegisterSlash("chemin", function() PD:Print(PD.Status()) end, "chemin : le trajet dessiné (statut)")
end
