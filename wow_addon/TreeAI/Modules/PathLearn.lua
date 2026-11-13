-- TreeAI — Chemins appris (agent ROUTEFIX, 4.1) : le relief et les virages, appris sur TES déplacements.
--  * Pendant que tu bouges (échantillon de NavTime, ≈ 1 par seconde, seulement en mouvement : aucun OnUpdate à nous), un
--    point tous les ~20 yd dans une grille de cases de 20 yd par continent ; chaque passage d'une case à l'autre est une
--    arête avec la longueur réellement parcourue. Jamais en vol (taxi), en instance, mort / fantôme.
--  * Les positions du groupe que le module Groupe lit déjà (UnitPosition des membres) comptent aussi, avec un poids
--    plus faible (une case n'est « parcourue » qu'après ~3 passages d'un ami, 1 des tiens).
--  * PL.LegSec(a, b, vitesse) : la durée par le chemin appris quand les deux bouts sont près de cases parcourues et que
--    la ligne droite passe par des cases jamais parcourues (falaise, mesa, rivière) ; nil sinon (le modèle s'applique).
--  * PL.Waypoint(a, b) : le prochain point de passage quand la ligne droite est fausse (virage > 35° ou cases jamais
--    parcourues alors qu'un chemin appris existe) ; la boussole vise ce point « par le chemin ».
--  * PL.Path(a, b) : les points du chemin (dessinés sur la minimap par PathDraw).
-- Mémoire bornée (MAX_CELLS cases par continent, les moins vues partent d'abord). Rien n'est inventé : uniquement des
-- positions où toi ou ton groupe êtes passés. Lecture seule, aucune fonction protégée. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

-- (5.1 ROUTE6, audit B3) dans le profil par défaut : ce module n'a ni cadre ni fonction protégée ; il range une position
-- par seconde SEULEMENT quand tu bouges (échantillon de NavTime), et ses requêtes sont mémorisées (PL.QUERY_S). Coût
-- mesuré : voir research/route6.md. Sans lui, « le plus court chemin » n'était qu'une ligne droite × un facteur de zone.
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.PathLearn = true end
local PL = T:RegisterModule("PathLearn", {
  title = "Chemins appris",
  description = "Le trajet suit les chemins que tu as vraiment parcourus (rampes, virages, relief).",
  defaults = { enabled = true },
  options = {},
})
T.PathLearn = PL

local floor, sqrt, abs, max, atan2 = math.floor, math.sqrt, math.abs, math.max, math.atan2
local CELL, SAMPLE_YD, NEAR_YD = 20, 20, 45
local MAX_CELLS, MIN_WALKED = 6000, 1
local GROUP_W, TURN_DEG = 1 / 3, 35
PL.CELL, PL.TURN_DEG = CELL, TURN_DEG

local function G(n) return rawget(_G, n) end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b = pcall(fn, ...)
  if ok then return a, b end
  return nil
end
local function secret(v) local f = G("issecretvalue") return f and v ~= nil and f(v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end

-- ------------------------------------------------------------------ stockage : db.maps[c] = { w = { [cell] = poids },
-- e = { [cell] = { [cell] = longueur yd } }, n = nombre de cases }
local function store(c)
  local db = PL.db
  if type(db) ~= "table" then return nil end
  if type(db.maps) ~= "table" then db.maps = {} end
  if c == nil then return db end
  local m = db.maps[c]
  if type(m) ~= "table" then m = { w = {}, e = {}, n = 0 }; db.maps[c] = m end
  if type(m.w) ~= "table" then m.w = {} end
  if type(m.e) ~= "table" then m.e = {} end
  return m
end
PL.Store = store
PL.rev = 0
local function cellOf(n, w) return floor(n / CELL), floor(w / CELL) end
local function key(cx, cy) return (cx + 5000) * 10000 + (cy + 5000) end
local function unkey(k) return floor(k / 10000) - 5000, k % 10000 - 5000 end
local function center(k) local cx, cy = unkey(k) return (cx + 0.5) * CELL, (cy + 0.5) * CELL end
PL.Key, PL.Center = function(n, w) return key(cellOf(n, w)) end, center

local function prune(m)
  -- les cases les moins vues partent (seuil qui monte jusqu'à repasser sous 90 % du maximum)
  local th = 0.5
  while m.n > MAX_CELLS * 0.9 and th < 1e6 do
    for k, wt in pairs(m.w) do
      if wt <= th then
        m.w[k], m.e[k] = nil, nil
        m.n = m.n - 1
      end
    end
    th = th * 2
  end
  for _, row in pairs(m.e) do for k2 in pairs(row) do if not m.w[k2] then row[k2] = nil end end end
end
local function mark(m, k, wt)
  local old = m.w[k]
  if not old then
    m.n = (m.n or 0) + 1
    if old == nil and (wt or 0) >= MIN_WALKED then PL.rev = PL.rev + 1 end
  elseif old < MIN_WALKED and old + wt >= MIN_WALKED then PL.rev = PL.rev + 1 end
  m.w[k] = math.min(255, (old or 0) + wt)
  if m.n > MAX_CELLS then prune(m) end
end
local function link(m, k1, k2, d)
  if k1 == k2 then return end
  for _ = 1, 2 do
    local row = m.e[k1]
    if not row then row = {}; m.e[k1] = row end
    local old = row[k2]
    if not old then PL.rev = PL.rev + 1; row[k2] = floor(d * 10 + 0.5) / 10
    else row[k2] = floor((old * 0.8 + d * 0.2) * 10 + 0.5) / 10 end
    k1, k2 = k2, k1
  end
end
-- un segment parcouru (a → b, ≤ 120 yd) : toutes les cases traversées, arêtes entre cases consécutives ; carry = dernier
-- passage de case { k, n, w } (gardé d'un segment au suivant : la longueur parcourue n'est jamais perdue en route)
local function addSegment(m, an, aw, bn, bw, wt, carry)
  local d = sqrt((bn - an) ^ 2 + (bw - aw) ^ 2)
  local steps = max(1, floor(d / (CELL / 4)))
  local k0 = key(cellOf(an, aw))
  carry = carry or {}
  if carry.k ~= k0 then carry.k, carry.n, carry.w = k0, an, aw end
  local prevK, pn, pw, run = carry.k, carry.n, carry.w, 0
  local qn, qw = an, aw
  if carry.run then run = carry.run end
  mark(m, prevK, wt)
  for i = 1, steps do
    local t = i / steps
    local n, w = an + (bn - an) * t, aw + (bw - aw) * t
    run = run + sqrt((n - qn) ^ 2 + (w - qw) ^ 2)
    qn, qw = n, w
    local k = key(cellOf(n, w))
    if k ~= prevK then
      mark(m, k, wt)
      link(m, prevK, k, max(run, sqrt((n - pn) ^ 2 + (w - pw) ^ 2)))
      prevK, pn, pw, run = k, n, w, 0
    end
  end
  carry.k, carry.n, carry.w, carry.run = prevK, pn, pw, run
end
PL.AddSegment = function(c, an, aw, bn, bw, wt, carry) local m = store(c) if m then addSegment(m, an, aw, bn, bw, wt or 1, carry) end end

-- ------------------------------------------------------------------ apprentissage
local last = {}                                  -- [qui] = { c, n, w, t }
local function feedOne(who, c, n, w, t, wt)
  local L = last[who]
  if not (L and L.c == c) then last[who] = { c = c, n = n, w = w, t = t } return end
  local d = sqrt((n - L.n) ^ 2 + (w - L.w) ^ 2)
  local dt = t - L.t
  if dt <= 0 then return end
  if d / dt > 60 or d > 120 or dt > 30 then last[who] = { c = c, n = n, w = w, t = t } return end   -- téléport, saut
  if d < SAMPLE_YD then return end
  local m = store(c)
  L.carry = L.carry or {}
  if m then addSegment(m, L.n, L.w, n, w, wt, L.carry) end
  L.n, L.w, L.t = n, w, t
end
--- échantillon du joueur (NavTime.Sample, en mouvement seulement) : c = continent, n / w = yards monde
function PL.Feed(c, n, w, t, mode)
  if not (PL.db and PL:IsEnabled()) then return end
  if try(G("UnitOnTaxi"), "player") or try(G("IsInInstance")) then last.me = nil return end
  c, n, w = num(c), num(n), num(w)
  if not (c and n and w) then return end
  if mode == "s" then last.me = nil return end        -- à la nage : pas un chemin à pied
  feedOne("me", c, n, w, t or 0, 1)
  PL.FeedGroup(c, t or 0)
end
-- positions du groupe déjà lues par le module Groupe (st.py / st.px / st.pi, yards monde arrondis à 5) : poids faible
function PL.FeedGroup(c, t)
  local Gm = T.modules and T.modules.Group
  local list = Gm and type(Gm._roster) == "function" and try(Gm._roster) or nil
  if type(list) ~= "table" then return end
  for i, m in ipairs(list) do
    local st = type(m) == "table" and m.st
    local pn, pw, pi = st and num(st.py), st and num(st.px), st and num(st.pi)
    if pn and pw and pi == c and not m.dead and not m.ghost then
      feedOne("g" .. tostring(m.name or i), c, pn, pw, t, GROUP_W)
    end
  end
end

-- ------------------------------------------------------------------ requêtes
local function walked(m, k) return (m.w[k] or 0) >= MIN_WALKED end
-- case parcourue la plus proche de (n, w), à moins de NEAR_YD → clé, distance ; mémorisée par case (au centre de la case)
-- tant que rien de neuf n'est appris (le trajet en demande des centaines par calcul)
-- (5.1 ROUTE6) les REQUÊTES suivent ce qui est appris au plus une fois toutes les PL.QUERY_S secondes : en terrain neuf une
-- case s'apprend toutes les ~3 s, et chaque case vidait les mémos (case la plus proche, A*) — le trajet refaisait alors tous
-- ses chemins à chaque rafraîchissement. Après une pause (≥ QUERY_S sans requête neuve) la première requête est à jour.
PL.QUERY_S = 30
local qRev, qAt = -1, -1e9
local function queryRev()
  if qRev ~= PL.rev then
    local t = tonumber(try(G("GetTime"))) or 0
    if qRev < 0 or t - qAt >= PL.QUERY_S then qRev, qAt = PL.rev, t end
  end
  return qRev
end
PL.QueryRev = queryRev
--- (tests, « Réinitialiser ») ce qui vient d'être appris sert tout de suite
function PL.Sync() qRev, qAt = PL.rev, tonumber(try(G("GetTime"))) or 0 return qRev end
local nearMemo, nearRev = {}, -1
local function nearest(m, n, w)
  local cx, cy = cellOf(n, w)
  local qr = queryRev()
  if nearRev ~= qr then nearMemo, nearRev = {}, qr end
  local mk = key(cx, cy)
  local hit = nearMemo[mk]
  if hit ~= nil then return hit or nil end
  n, w = (cx + 0.5) * CELL, (cy + 0.5) * CELL
  local best, bd = nil, NEAR_YD
  for dx = -2, 2 do
    for dy = -2, 2 do
      local k = key(cx + dx, cy + dy)
      if walked(m, k) and m.e[k] then
        local a, b = center(k)
        local d = sqrt((a - n) ^ 2 + (b - w) ^ 2)
        if d <= bd then best, bd = k, d end
      end
    end
  end
  nearMemo[mk] = best or false
  return best, bd
end
-- la ligne droite a → b ne passe-t-elle que par des cases parcourues (un trou d'une case toléré) ?
local function lineClear(m, an, aw, bn, bw)
  local d = sqrt((bn - an) ^ 2 + (bw - aw) ^ 2)
  local steps = max(1, floor(d / (CELL / 2)))
  local gap = 0
  for i = 0, steps do
    local t = i / steps
    if walked(m, key(cellOf(an + (bn - an) * t, aw + (bw - aw) * t))) then gap = 0
    else
      gap = gap + 1
      if gap > 2 then return false end
    end
  end
  return true
end
PL.LineClear = function(c, an, aw, bn, bw) local m = store(c) return m and lineClear(m, an, aw, bn, bw) or false end
-- A* sur les arêtes observées (tas binaire) → liste de clés, longueur
local function astar(m, ka, kb, limit)
  if ka == kb then return { ka }, 0 end
  local bn, bw = center(kb)
  local function h(k) local a, b = center(k) return sqrt((a - bn) ^ 2 + (b - bw) ^ 2) end
  local heap, hn = {}, 0
  local gS, par, closed = { [ka] = 0 }, {}, {}
  local function push(k, f)
    hn = hn + 1
    local i = hn
    heap[i] = { k, f }
    while i > 1 do
      local p = floor(i / 2)
      if heap[p][2] <= heap[i][2] then break end
      heap[p], heap[i] = heap[i], heap[p]
      i = p
    end
  end
  local function pop()
    local top = heap[1]
    heap[1] = heap[hn]; heap[hn] = nil; hn = hn - 1
    local i = 1
    while true do
      local l, r, s = i * 2, i * 2 + 1, i
      if l <= hn and heap[l][2] < heap[s][2] then s = l end
      if r <= hn and heap[r][2] < heap[s][2] then s = r end
      if s == i then break end
      heap[s], heap[i] = heap[i], heap[s]
      i = s
    end
    return top
  end
  push(ka, h(ka))
  local n = 0
  while hn > 0 do
    local top = pop()
    local k = top[1]
    if k == kb then
      local path = { kb }
      while par[k] do k = par[k]; path[#path + 1] = k end
      local out = {}
      for i = #path, 1, -1 do out[#out + 1] = path[i] end
      return out, gS[kb]
    end
    if not closed[k] then
      closed[k] = true
      n = n + 1
      if n > (limit or 5000) then return nil end
      for k2, d in pairs(m.e[k] or {}) do
        if not closed[k2] and walked(m, k2) then
          local g2 = gS[k] + d
          if not gS[k2] or g2 < gS[k2] then
            gS[k2], par[k2] = g2, k
            push(k2, g2 + h(k2))
          end
        end
      end
    end
  end
  return nil
end
-- chemin appris a → b (points monde { c, n, w }) → { points (centres, sans a, avec b) }, longueur totale, "clair"|"appris"
local memo, memoRev = {}, -1
local function solve(a, b)
  if not (a and b and a.c and a.c == b.c and a.n and b.n) then return nil end
  local m = store(a.c)
  if not (m and next(m.e)) then return nil end
  local straight = sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2)
  if straight < 40 then return nil end
  local ka = nearest(m, a.n, a.w)
  local kb = ka and nearest(m, b.n, b.w)
  if not (ka and kb) then return nil end
  local qr = queryRev()
  if memoRev ~= qr then memo, memoRev = {}, qr end
  local mk = ka .. ">" .. kb
  local r = memo[mk]
  if r == nil then
    local path, len = astar(m, ka, kb)
    r = path and { path = path, len = len } or false
    memo[mk] = r
  end
  if lineClear(m, a.n, a.w, b.n, b.w) then return { b }, straight, "clair" end
  if not r then return nil end
  local an, aw = center(ka)
  local bn2, bw2 = center(kb)
  local total = sqrt((a.n - an) ^ 2 + (a.w - aw) ^ 2) + r.len + sqrt((b.n - bn2) ^ 2 + (b.w - bw2) ^ 2)
  if total > straight * 3 then return nil end          -- données étranges : on ne s'y fie pas
  if total < straight then total = straight end
  local pts = {}
  for i = 2, #r.path do
    local n, w = center(r.path[i])
    pts[#pts + 1] = { c = a.c, n = n, w = w }
  end
  pts[#pts + 1] = b
  return pts, total, "appris", m
end
PL.Solve = solve
--- secondes a → b par le chemin appris (points monde), nil si les données ne couvrent pas ce trajet
function PL.LegSec(a, b, speed)
  if not (PL.db and PL:IsEnabled()) then return nil end
  local pts, len, how = solve(a, b)
  if not pts or how ~= "appris" then return nil end
  return len / max(1, speed or 7), len
end
local function worldOf(p)
  local Geo = T.Geo
  if not (p and p.mapID and p.x and Geo and Geo.World) then return nil end
  local x, y = p.x, p.y
  if x <= 1 and y <= 1 then x, y = x * 100, y * 100 end
  local n, w, c = Geo.World(p.mapID, x, y)
  if not n then return nil end
  return { c = c, n = n, w = w }
end
PL.WorldOf = worldOf
--- même chose pour des points de carte { mapID, x, y } (0..1 ou %), la boussole
function PL.LegSecMap(a, b, speed) return PL.LegSec(worldOf(a), worldOf(b), speed) end
--- points du chemin (pour le dessin), nil si la ligne droite convient
function PL.Path(a, b)
  if not (PL.db and PL:IsEnabled()) then return nil end
  local pts, _, how = solve(a, b)
  if how ~= "appris" then return nil end
  return pts
end
--- prochain point de passage a → b (points monde) : le point le plus loin du chemin appris visible en ligne droite
--- (cases parcourues) ; nil quand la ligne droite convient (pas de chemin appris, ou virage ≤ 35°)
function PL.Waypoint(a, b)
  if not (PL.db and PL:IsEnabled()) then return nil end
  local pts, _, how, m = solve(a, b)
  if how ~= "appris" or not pts then return nil end
  local pick
  for i = #pts - 1, 1, -1 do
    local p = pts[i]
    if lineClear(m, a.n, a.w, p.n, p.w) then pick = p break end
  end
  pick = pick or pts[1]
  local d = sqrt((pick.n - a.n) ^ 2 + (pick.w - a.w) ^ 2)
  if d < 8 then pick = pts[2] or b end                -- déjà sur ce point : le suivant
  local ang1 = atan2(-(b.w - a.w), b.n - a.n)
  local ang2 = atan2(-(pick.w - a.w), pick.n - a.n)
  local diff = abs(ang1 - ang2) * 180 / math.pi
  if diff > 180 then diff = 360 - diff end
  if diff <= TURN_DEG then return nil end
  return pick, diff
end
function PL.Count(c) local m = store(c) return m and m.n or 0 end
function PL.Reset() local db = store() if db then db.maps = {} end last = {} PL.rev = PL.rev + 1 PL.Sync() end

-- une fois : qui est resté en mode sûr (où ce module était coupé d'office) le reçoit allumé ; un « coupé » choisi par le
-- joueur (db.modChoice du cœur) n'est jamais défait
if T.On then
  T:On("READY", "PathLearn:safe51", function()
    local db = PL.db
    if type(db) ~= "table" or db.safeV then return end
    db.safeV = 1
    local mc = type(T.db) == "table" and T.db.modChoice
    local refused = type(mc) == "table" and mc.PathLearn == false
    if db.enabled == false and not refused and T.ModuleProfile and T:ModuleProfile() == "sur" and T.SetModuleEnabled then
      T:SetModuleEnabled("PathLearn", true)
    end
  end)
end
function PL:OnEnable()
  store()
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() last = {} end)
end
