-- TreeAI — module Route sûre (agent SAFEPATH). PAS d'auto-déplacement : le joueur marche lui-même.
-- Calcule un chemin de points de passage sur la carte de zone (A* sur une grille ~1 % de l'uiMap) qui évite
-- le danger, puis donne les points UN PAR UN à la boussole (TreeAI.Compass) ; passage au suivant à 15 yd.
-- Danger : apparitions connues des monstres hostiles (QuestieDB, LECTURE seule : LibQuestieDB contrat 4, repli
-- QuestieLoader._modules.QuestieDB), pondérées par l'écart de niveau (rouge / orange / élite = fort, gris =
-- ignoré, rayon ≈ aggro 20 yd + écart de niveau) ; danger « en direct » (barres de nom hostiles non grises :
-- la position exacte des PNJ n'est pas disponible → coût temporaire dans le secteur devant le joueur) ; lieux
-- de mort récents. Routes : coût un peu plus faible près des maîtres de vol / PNJ amicaux (repère grossier).
-- Shortest Path Forever (si présent et s'il expose un itinéraire) : on garde sa route si elle évite déjà.
-- Calcul en coroutine (≤ 2 ms par image), hors combat seulement. Aucune API de mouvement, aucune frappe,
-- aucun cadre de Blizzard touché. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("SafePath", {
  title = "Route sûre",
  defaults = { enabled = true, elite = true, red = true, orange = true, yellow = true, green = false,
               live = true, deaths = true, roads = true, spf = true, announce = true },
  charDefaults = { deaths = {} },
  options = {
    { key = "elite", type = "toggle", label = "Route sûre : éviter les élites" },
    { key = "red", type = "toggle", label = "Route sûre : éviter les monstres rouges (+5 niveaux)" },
    { key = "orange", type = "toggle", label = "Route sûre : éviter les monstres orange (+3/+4)" },
    { key = "yellow", type = "toggle", label = "Route sûre : éviter (un peu) les monstres jaunes" },
    { key = "green", type = "toggle", label = "Route sûre : éviter (un peu) les monstres verts" },
    { key = "live", type = "toggle", label = "Route sûre : tenir compte des ennemis visibles" },
    { key = "deaths", type = "toggle", label = "Route sûre : éviter mes lieux de mort récents" },
    { key = "roads", type = "toggle", label = "Route sûre : préférer les abords des PNJ / maîtres de vol" },
    { key = "spf", type = "toggle", label = "Route sûre : comparer avec Shortest Path (si installé)" },
  },
})
ns.SafePath = M
T.SafePath = M

local floor, sqrt, max, min, abs = math.floor, math.sqrt, math.max, math.min, math.abs
local sin, cos, pi = math.sin, math.cos, math.pi
local YD2M = 0.9144

-- constantes (modifiables par les tests)
M.GRID = 100          -- cellules par côté (1 % de la carte)
M.BUDGET_MS = 2       -- temps max par image
M.ADVANCE_YD = 15     -- passage au point suivant
M.LIVE_TTL = 20       -- s, danger « en direct »
M.LIVE_RANGE = 40     -- yd, secteur devant le joueur
M.LIVE_HALF = 50 * pi / 180
M.DEATH_TTL = 3600    -- s
M.DEATH_R = 30        -- yd
M.ROAD_R = 40         -- yd
M.ROAD_MULT = 0.8
M.PEN_CAP = 40
M.WEIGHT = { elite = 8, red = 6, orange = 3, yellow = 1, green = 0.3, live = 4, death = 5 }

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function G(name) return rawget(_G, name) end
local function now() return try(G("GetTime")) or 0 end
M.Clock = function() return try(G("debugprofilestop")) or (now() * 1000) end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function compass() return T.Compass or (T.GetModule and T:GetModule("Compass")) or nil end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function opt(k) return not (M.db and M.db[k] == false) end

-- ------------------------------------------------------------------ niveaux (pur)
-- niveau gris (Classic) : en dessous = aucune menace
function M.GreyLevel(pl)
  pl = tonumber(pl) or 1
  if pl <= 5 then return 0 end
  if pl <= 39 then return pl - floor(pl / 10) - 5 end
  if pl <= 59 then return pl - floor(pl / 5) - 1 end
  return pl - 9
end
-- catégorie : "elite" | "red" | "orange" | "yellow" | "green" | "grey"
function M.Category(mobLvl, pl, elite)
  mobLvl, pl = tonumber(mobLvl) or 0, tonumber(pl) or 1
  if mobLvl < 0 then return "red" end           -- tête de mort (??)
  if mobLvl <= M.GreyLevel(pl) then return "grey" end
  if elite then return "elite" end
  local d = mobLvl - pl
  if d >= 5 then return "red" elseif d >= 3 then return "orange" elseif d >= -2 then return "yellow" end
  return "green"
end
-- rayon d'aggro approximatif (yd) : 20 à niveau égal, ±1 par niveau, 5..45, +5 élite
function M.Radius(mobLvl, pl, elite)
  mobLvl, pl = tonumber(mobLvl) or 0, tonumber(pl) or 1
  if mobLvl < 0 then mobLvl = pl + 10 end
  local r = 20 + (mobLvl - pl)
  if elite then r = r + 5 end
  return max(5, min(45, r))
end
-- danger { x, y (0..1), r (yd), w, cat, name } d'un monstre selon les options ; nil si ignoré
function M.Danger(x, y, mobLvl, pl, elite, name, db)
  local cat = M.Category(mobLvl, pl, elite)
  if cat == "grey" then return nil end
  db = db or M.db or {}
  if db[cat] == false then return nil end
  return { x = x, y = y, r = M.Radius(mobLvl, pl, elite), w = M.WEIGHT[cat] or 1, cat = cat, name = name }
end

-- ------------------------------------------------------------------ QuestieDB (lecture seule)
-- adaptateur : N(id, key), ids() → liste d'ids PNJ, map(areaId) → uiMapID
function M.DB()
  local lib = G("LibQuestieDB")
  if type(lib) == "table" and type(lib.RequireContract) == "function" then
    local ok, res = pcall(lib.RequireContract, 4)
    local Npc = lib.Npc or G("NpcDB")
    if ok and res ~= false and type(Npc) == "table" and type(Npc.Get) == "function" then
      local area2map
      return { src = "LibQuestieDB",
        N = function(id, key) return try(Npc.Get, id, key) end,
        ids = function() return try(Npc.GetAllIds) end,
        map = function(areaId)
          if area2map == nil then
            area2map = false
            local zdb = type(lib.Support) == "table" and try(lib.Support.Get, "ZoneDB")
            local s = type(zdb) == "table" and type(zdb.private) == "table" and zdb.private.areaIdToUiMapId
            if type(s) == "table" then area2map = s end
          end
          return area2map and tonumber(area2map[areaId]) or nil
        end }
    end
  end
  local QL = G("QuestieLoader")
  local mods = type(QL) == "table" and rawget(QL, "_modules") or nil
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) ~= "table" or type(db.QueryNPCSingle) ~= "function" then return nil end
  local zdb = rawget(mods, "ZoneDB")
  return { src = "QuestieLoader",
    N = function(id, key) return try(db.QueryNPCSingle, id, key) end,
    ids = function()
      local p = db.NPCPointers or db.npcData
      if type(p) ~= "table" then return nil end
      local out = {}
      for id in pairs(p) do if tonumber(id) then out[#out + 1] = tonumber(id) end end
      table.sort(out)
      return out
    end,
    map = function(areaId)
      if type(zdb) == "table" and type(zdb.GetUiMapIdByAreaId) == "function" then
        return tonumber((try(zdb.GetUiMapIdByAreaId, zdb, areaId)))
      end
    end }
end

local function myFaction()
  local f = try(G("UnitFactionGroup"), "player")
  return f == "Horde" and "H" or (f == "Alliance" and "A" or nil)
end
local function friendly(fac, me)
  if type(fac) ~= "string" or fac == "" then return false end
  return fac == "AH" or fac == "HA" or (me ~= nil and fac:find(me, 1, true) ~= nil)
end

-- index des PNJ d'une carte : { hostile = { {x,y,lvl,elite,name} }, friends = { {x,y} } } (coroutine)
M.cache = {}
function M.IndexMap(mapID, yield)
  if M.cache[mapID] then return M.cache[mapID] end
  local db = M.DB()
  local idx = { hostile = {}, friends = {}, src = db and db.src }
  if not db then return idx end
  local ids = db.ids() or {}
  local me = myFaction()
  local C = compass()
  local amap = {}
  local function toMap(area)
    if amap[area] == nil then
      local id = db.map(area)
      if not id and C and C.AreaToMap then id = C.AreaToMap(area, nil, mapID) end
      amap[area] = id or false
    end
    return amap[area]
  end
  for n, id in ipairs(ids) do
    if yield and n % 50 == 0 then yield() end
    local sp = db.N(id, "spawns")
    if type(sp) == "table" then
      local pts
      for area, list in pairs(sp) do
        if type(area) == "number" and toMap(area) == mapID and type(list) == "table" then pts = pts or {} pts[#pts + 1] = list end
      end
      if pts then
        local fr = friendly(db.N(id, "friendlyToFaction"), me)
        local lvl = tonumber((db.N(id, "maxLevel"))) or tonumber((db.N(id, "minLevel"))) or 1
        local rank = tonumber((db.N(id, "rank"))) or 0
        local elite = rank == 1 or rank == 2 or rank == 3
        local name = db.N(id, "name")
        for _, list in ipairs(pts) do
          for _, p in ipairs(list) do
            local x, y = type(p) == "table" and tonumber(p[1]), type(p) == "table" and tonumber(p[2])
            if x and y and x >= 0 and y >= 0 then
              if fr then idx.friends[#idx.friends + 1] = { x = x / 100, y = y / 100 }
              else idx.hostile[#idx.hostile + 1] = { x = x / 100, y = y / 100, lvl = lvl, elite = elite, name = name, id = id } end
            end
          end
        end
      end
    end
  end
  M.cache[mapID] = idx
  return idx
end

-- ------------------------------------------------------------------ grille de coût (pur)
-- ctx = { W, H (yd), N, dangers = {x,y,r,w,cat}, roads = {x,y}, live = {x,y,facing,w}, deaths = {x,y} }
function M.BuildGrid(ctx, yield)
  local N, W, H = ctx.N or M.GRID, ctx.W, ctx.H
  local cw, ch = W / N, H / N
  local pen, mult = {}, {}
  for i = 1, N * N do pen[i], mult[i] = 0, 1 end
  local function stamp(x, y, r, fn)
    local cx, cy = x * W, y * H
    local gx0, gx1 = max(1, floor((cx - r) / cw) + 1), min(N, floor((cx + r) / cw) + 1)
    local gy0, gy1 = max(1, floor((cy - r) / ch) + 1), min(N, floor((cy + r) / ch) + 1)
    for gy = gy0, gy1 do
      local py = (gy - 0.5) * ch - cy
      for gx = gx0, gx1 do
        local px = (gx - 0.5) * cw - cx
        local d = sqrt(px * px + py * py)
        if d <= r then fn((gy - 1) * N + gx, d, px, py) end
      end
    end
  end
  local function add(i, v) pen[i] = min(M.PEN_CAP, pen[i] + v) end
  for k, d in ipairs(ctx.dangers or {}) do
    if yield and k % 40 == 0 then yield() end
    -- cellule entière touchée : rayon + demi-diagonale de cellule
    local r = d.r + 0.5 * sqrt(cw * cw + ch * ch)
    stamp(d.x, d.y, r, function(i, dist) add(i, d.w * (1 - 0.5 * dist / r)) end)
  end
  for _, d in ipairs(ctx.deaths or {}) do
    stamp(d.x, d.y, M.DEATH_R, function(i, dist) add(i, (d.w or M.WEIGHT.death) * (1 - 0.5 * dist / M.DEATH_R)) end)
  end
  for _, l in ipairs(ctx.live or {}) do
    -- facing : 0 = nord, sens anti-horaire → direction (est, sud) = (-sin f, -cos f)
    local fx, fy = -sin(l.facing or 0), -cos(l.facing or 0)
    stamp(l.x, l.y, M.LIVE_RANGE, function(i, dist, px, py)
      if dist < 1 then return end
      local c = (px * fx + py * fy) / dist
      if c >= cos(M.LIVE_HALF) then add(i, l.w or M.WEIGHT.live) end
    end)
  end
  if ctx.roads then
    for _, r in ipairs(ctx.roads) do stamp(r.x, r.y, M.ROAD_R, function(i) mult[i] = M.ROAD_MULT end) end
  end
  return { N = N, W = W, H = H, cw = cw, ch = ch, pen = pen, mult = mult,
           minMult = (ctx.roads and #ctx.roads > 0) and M.ROAD_MULT or 1 }
end

local function cellOf(g, x, y)
  local gx = max(1, min(g.N, floor(x * g.N) + 1))
  local gy = max(1, min(g.N, floor(y * g.N) + 1))
  return (gy - 1) * g.N + gx, gx, gy
end
M.CellOf = cellOf
local function center(g, i)
  local gx, gy = (i - 1) % g.N + 1, floor((i - 1) / g.N) + 1
  return (gx - 0.5) / g.N, (gy - 0.5) / g.N
end
M.Center = center

-- coût d'un segment (échantillonné) : longueur yd × (mult + pénalité) ; renvoie coût, longueur, part danger
function M.SegCost(g, ax, ay, bx, by)
  local dx, dy = (bx - ax) * g.W, (by - ay) * g.H
  local len = sqrt(dx * dx + dy * dy)
  local n = max(1, floor(len / (0.5 * min(g.cw, g.ch))) + 1)
  local step, c, p = len / n, 0, 0
  for k = 1, n do
    local t = (k - 0.5) / n
    local i = cellOf(g, ax + (bx - ax) * t, ay + (by - ay) * t)
    c, p = c + step * (g.mult[i] + g.pen[i]), p + step * g.pen[i]
  end
  return c, len, p
end
-- exposition au danger d'une polyligne (somme pénalité × yd) + longueur
function M.Exposure(g, pts)
  local e, len = 0, 0
  for k = 2, #pts do
    local _, l, p = M.SegCost(g, pts[k - 1].x, pts[k - 1].y, pts[k].x, pts[k].y)
    e, len = e + p, len + l
  end
  return e, len
end

-- ------------------------------------------------------------------ A* (pur, tas binaire)
local function heapPush(h, f, i, v)
  local n = #h + 1
  h[n], f[n] = i, v
  while n > 1 do
    local p = floor(n / 2)
    if f[p] <= f[n] then break end
    h[p], h[n], f[p], f[n] = h[n], h[p], f[n], f[p]
    n = p
  end
end
local function heapPop(h, f)
  local n = #h
  if n == 0 then return nil end
  local top = h[1]
  h[1], f[1] = h[n], f[n]
  h[n], f[n] = nil, nil
  n = n - 1
  local k = 1
  while true do
    local l, r, s = 2 * k, 2 * k + 1, k
    if l <= n and f[l] < f[s] then s = l end
    if r <= n and f[r] < f[s] then s = r end
    if s == k then break end
    h[s], h[k], f[s], f[k] = h[k], h[s], f[k], f[s]
    k = s
  end
  return top
end

-- liste de cellules du départ à l'arrivée (nil si impossible)
function M.AStar(g, from, to, yield)
  local N, cw, ch = g.N, g.cw, g.ch
  local s = cellOf(g, from.x, from.y)
  local e, ex, ey = cellOf(g, to.x, to.y)
  local diag = sqrt(cw * cw + ch * ch)
  local DX = { 1, -1, 0, 0, 1, 1, -1, -1 }
  local DY = { 0, 0, 1, -1, 1, -1, 1, -1 }
  local DL = { cw, cw, ch, ch, diag, diag, diag, diag }
  local gs, came, closed = { [s] = 0 }, {}, {}
  local hp, hf = {}, {}
  local function h(i)
    local gx, gy = (i - 1) % N + 1, floor((i - 1) / N) + 1
    local dx, dy = (gx - ex) * cw, (gy - ey) * ch
    return sqrt(dx * dx + dy * dy) * g.minMult
  end
  heapPush(hp, hf, s, h(s))
  local n = 0
  while true do
    local c = heapPop(hp, hf)
    if not c then return nil end
    if c == e then break end
    if not closed[c] then
      closed[c] = true
      n = n + 1
      if yield and n % 200 == 0 then yield() end
      local gx, gy = (c - 1) % N + 1, floor((c - 1) / N) + 1
      local gc = gs[c]
      for k = 1, 8 do
        local nx, ny = gx + DX[k], gy + DY[k]
        if nx >= 1 and nx <= N and ny >= 1 and ny <= N then
          local j = (ny - 1) * N + nx
          if not closed[j] then
            local v = gc + DL[k] * (g.mult[j] + g.pen[j])
            if gs[j] == nil or v < gs[j] - 1e-9 then
              gs[j], came[j] = v, c
              heapPush(hp, hf, j, v + h(j))
            end
          end
        end
      end
    end
  end
  local path, c = {}, e
  while c do path[#path + 1] = c c = came[c] end
  for i = 1, floor(#path / 2) do path[i], path[#path + 1 - i] = path[#path + 1 - i], path[i] end
  return path
end

-- lissage : garde un segment droit tant qu'il ne coûte pas plus (≤ +1 %) que le chemin de cellules
function M.Smooth(g, from, to, cells, yield)
  local P = { { x = from.x, y = from.y } }
  for k = 2, #cells - 1 do local x, y = center(g, cells[k]) P[#P + 1] = { x = x, y = y } end
  P[#P + 1] = { x = to.x, y = to.y }
  local cum = { 0 }
  for k = 2, #P do cum[k] = cum[k - 1] + (M.SegCost(g, P[k - 1].x, P[k - 1].y, P[k].x, P[k].y)) end
  local out, a = { P[1] }, 1
  local j = a + 2
  while j <= #P do
    if yield then yield() end
    local c = M.SegCost(g, P[a].x, P[a].y, P[j].x, P[j].y)
    if c > (cum[j] - cum[a]) * 1.01 + 1e-6 then
      out[#out + 1] = P[j - 1]
      a = j - 1
    end
    j = j + 1
  end
  out[#out + 1] = P[#P]
  return out
end

-- dangers « touchés » par une polyligne (distance au segment < rayon), par catégorie
local function segDist(g, p, a, b)
  local ax, ay, bx, by = a.x * g.W, a.y * g.H, b.x * g.W, b.y * g.H
  local px, py = p.x * g.W, p.y * g.H
  local dx, dy = bx - ax, by - ay
  local L = dx * dx + dy * dy
  local t = L > 0 and max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L)) or 0
  local qx, qy = ax + t * dx - px, ay + t * dy - py
  return sqrt(qx * qx + qy * qy)
end
function M.Touched(g, pts, dangers)
  local hit = {}
  for k, d in ipairs(dangers or {}) do
    for s = 2, #pts do
      if segDist(g, d, pts[s - 1], pts[s]) < d.r then hit[k] = true break end
    end
  end
  return hit
end

local CAT_TXT = { elite = { "élite", "élites" }, red = { "rouge", "rouges" }, orange = { "orange", "orange" },
  yellow = { "jaune", "jaunes" }, green = { "vert", "verts" } }
local CAT_ORDER = { "elite", "red", "orange", "yellow", "green" }
function M.Summary(res)
  local parts = {}
  for _, c in ipairs(CAT_ORDER) do
    local n = res.avoided and res.avoided[c] or 0
    if n > 0 then parts[#parts + 1] = ("%d %s"):format(n, CAT_TXT[c][n > 1 and 2 or 1]) end
  end
  local detour = floor((res.length - res.direct) * YD2M + 0.5)
  if #res.waypoints <= 1 or (#parts == 0 and detour <= 5) then
    return "Route directe sûre (" .. floor(res.direct * YD2M + 0.5) .. " m)"
  end
  local s = ("Détour sûr : +%d m"):format(max(0, detour))
  if #parts > 0 then s = s .. ", évite " .. table.concat(parts, ", ") end
  if res.left and res.left > 0 then s = s .. (" (%d danger%s inévitable%s)"):format(res.left, res.left > 1 and "s" or "", res.left > 1 and "s" or "") end
  if res.via then s = s .. " · " .. res.via end
  return s
end

-- calcul complet (pur) : ctx = { W, H, N, from, to, dangers, roads, live, deaths, spfRoute? }
-- → { waypoints = {{x,y}} (sans le départ), length, direct, avoided = {cat=n}, left, exposure, directExposure }
function M.Plan(ctx, yield)
  local g = M.BuildGrid(ctx, yield)
  local from, to = ctx.from, ctx.to
  local direct = { from, to }
  local dExp, dLen = M.Exposure(g, direct)
  local res = { grid = g, direct = dLen, directExposure = dExp }
  local pts
  if dExp <= 1e-6 then
    pts = direct
  else
    local cells = M.AStar(g, from, to, yield)
    pts = cells and M.Smooth(g, from, to, cells, yield) or direct
  end
  local exp, len = M.Exposure(g, pts)
  -- itinéraire Shortest Path : gardé s'il évite déjà (pas plus exposé que le nôtre, à 10 % près)
  if ctx.spfRoute and #ctx.spfRoute >= 2 then
    local sp = { from }
    for _, p in ipairs(ctx.spfRoute) do sp[#sp + 1] = p end
    local se, sl = M.Exposure(g, sp)
    if se <= exp * 1.1 + 1e-6 then pts, exp, len, res.via = sp, se, sl, "itinéraire Shortest Path (déjà sûr)" end
  end
  if exp >= dExp - 1e-6 and pts ~= direct and not res.via then pts, exp, len = direct, dExp, dLen end
  local hd, hp = M.Touched(g, direct, ctx.dangers), M.Touched(g, pts, ctx.dangers)
  local avoided, left = {}, 0
  for k, d in ipairs(ctx.dangers or {}) do
    if hd[k] and not hp[k] then avoided[d.cat] = (avoided[d.cat] or 0) + 1 end
    if hp[k] then left = left + 1 end
  end
  res.avoided, res.left, res.exposure, res.length = avoided, left, exp, len
  res.waypoints = {}
  for k = 2, #pts do res.waypoints[#res.waypoints + 1] = { x = pts[k].x, y = pts[k].y } end
  res.text = M.Summary(res)
  return res
end

-- ------------------------------------------------------------------ données en jeu
local function mapSize(mapID)
  local C = compass()
  if C and C.Geo and C.Geo.MapSize then return C.Geo.MapSize(mapID) end
end
local function playerLevel() return tonumber(try(G("UnitLevel"), "player")) or 1 end

M.live = {}
local function plateUnits()
  local out = {}
  local np = G("C_NamePlate")
  local list = type(np) == "table" and try(np.GetNamePlates) or nil
  if type(list) == "table" then
    for _, f in ipairs(list) do
      local u = type(f) == "table" and (f.namePlateUnitToken or (type(f.UnitFrame) == "table" and f.UnitFrame.unit))
      if type(u) == "string" then out[#out + 1] = u end
    end
    if #out > 0 then return out end
  end
  for i = 1, 40 do
    local u = "nameplate" .. i
    if try(G("UnitExists"), u) then out[#out + 1] = u end
  end
  return out
end
-- plus fort danger visible (catégorie) parmi les barres de nom hostiles non grises
function M.ScanLive()
  local pl, best, bw = playerLevel(), nil, 0
  for _, u in ipairs(plateUnits()) do
    if try(G("UnitCanAttack"), "player", u) and not try(G("UnitIsDead"), u) and not try(G("UnitIsPlayer"), u) then
      local lvl = tonumber(try(G("UnitLevel"), u)) or pl
      local cls = try(G("UnitClassification"), u)
      local elite = cls == "elite" or cls == "rareelite" or cls == "worldboss"
      local cat = M.Category(lvl, pl, elite)
      local w = (cat ~= "grey" and opt(cat)) and (M.WEIGHT[cat] or 0) or 0
      if w > bw then best, bw = cat, w end
    end
  end
  return best, bw
end
function M.NoteLive()
  if not opt("live") then return nil end
  local cat, w = M.ScanLive()
  if not cat then return nil end
  local C = compass()
  local me = C and C.Player and C.Player()
  if not me then return nil end
  local e = { mapID = me.mapID, x = me.x, y = me.y, facing = try(G("GetPlayerFacing")) or 0, w = max(w, M.WEIGHT.live), t = now(), cat = cat }
  M.live[#M.live + 1] = e
  while #M.live > 10 do table.remove(M.live, 1) end
  return e
end

local function recentDeaths(mapID)
  local out, t = {}, now()
  local list = M.char and M.char.deaths
  if type(list) == "table" then
    for _, d in ipairs(list) do
      if d.mapID == mapID and t - (d.t or 0) <= M.DEATH_TTL and t >= (d.t or 0) then out[#out + 1] = d end
    end
  end
  return out
end

local function roadsOf(mapID, idx)
  local out = {}
  local TM = G("C_TaxiMap")
  local nodes = type(TM) == "table" and try(TM.GetTaxiNodesForMap, mapID) or nil
  if type(nodes) == "table" then
    for _, n in ipairs(nodes) do
      local p = type(n) == "table" and n.position
      local x, y
      if type(p) == "table" then
        if type(p.GetXY) == "function" then x, y = try(p.GetXY, p) else x, y = p.x, p.y end
      end
      if tonumber(x) and tonumber(y) then out[#out + 1] = { x = x, y = y } end
    end
  end
  for k, f in ipairs(idx and idx.friends or {}) do
    if k > 400 then break end
    out[#out + 1] = f
  end
  return out
end

-- route proposée par Shortest Path Forever (API non documentée : on essaie quelques noms, lecture seule)
function M.SPFRoute(from, to)
  local C = compass()
  local A = C and C.SPF and C.SPF()
  if not (A and C.SPFCall) or inCombat() then return nil end
  for _, fn in ipairs({ "GetRoute", "Route", "GetPath", "Path" }) do
    local r = C.SPFCall(A, fn, from.mapID, from.x, from.y, to.mapID, to.x, to.y)
    if type(r) == "table" and #r >= 1 then
      local out = {}
      for _, p in ipairs(r) do
        if type(p) == "table" then
          local m = p.mapID or p.map or (#p >= 3 and p[1]) or from.mapID
          local x, y = tonumber(p.x or (#p >= 3 and p[2]) or p[1]), tonumber(p.y or (#p >= 3 and p[3]) or p[2])
          if x and y then
            if x > 1 or y > 1 then x, y = x / 100, y / 100 end
            if m ~= from.mapID then return nil end   -- route multi-cartes : on ne compare pas
            out[#out + 1] = { x = x, y = y }
          end
        end
      end
      if #out >= 1 then
        local l = out[#out]
        if abs(l.x - to.x) > 0.02 or abs(l.y - to.y) > 0.02 then out[#out + 1] = { x = to.x, y = to.y } end
        return out
      end
    end
  end
  return nil
end

-- contexte complet pour la position actuelle (coroutine : yield pendant l'index)
function M.Context(from, to, yield)
  local W, H = mapSize(from.mapID)
  if not W then return nil, "taille de carte inconnue" end
  local pl = playerLevel()
  local idx = M.IndexMap(from.mapID, yield)
  local dangers = {}
  for _, s in ipairs(idx.hostile) do
    local d = M.Danger(s.x, s.y, s.lvl, pl, s.elite, s.name)
    if d then dangers[#dangers + 1] = d end
  end
  local live, t = {}, now()
  if opt("live") then
    for _, l in ipairs(M.live) do if l.mapID == from.mapID and t - l.t <= M.LIVE_TTL then live[#live + 1] = l end end
  end
  return { W = W, H = H, N = M.GRID, from = from, to = to, dangers = dangers, live = live,
           deaths = opt("deaths") and recentDeaths(from.mapID) or nil,
           roads = opt("roads") and roadsOf(from.mapID, idx) or nil,
           spfRoute = opt("spf") and M.SPFRoute(from, to) or nil, src = idx.src }
end

-- ------------------------------------------------------------------ coroutine (≤ 2 ms / image, hors combat)
local runner
local function ensureRunner()
  if runner or type(G("CreateFrame")) ~= "function" then return runner end
  runner = G("CreateFrame")("Frame")
  runner:Hide()
  runner:SetScript("OnUpdate", function() M.Step() end)
  return runner
end

-- lance un calcul ; done(res, err) appelé à la fin
function M.Start(from, to, done)
  local deadline = 0
  local function yield()
    if M.Clock() >= deadline or inCombat() then coroutine.yield() end
  end
  M.job = {
    co = coroutine.create(function()
      local ctx, err = M.Context(from, to, yield)
      if not ctx then return nil, err end
      return M.Plan(ctx, yield)
    end),
    done = done,
    begin = function() deadline = M.Clock() + M.BUDGET_MS end,
  }
  local r = ensureRunner()
  if r then r:Show() end
  return M.job
end
function M.Step()
  local j = M.job
  if not j then if runner then runner:Hide() end return true end
  if inCombat() then return false end           -- en pause pendant le combat
  j.begin()
  local ok, res, err = coroutine.resume(j.co)
  if coroutine.status(j.co) ~= "dead" then return false end
  M.job = nil
  if runner then runner:Hide() end
  if not ok then err, res = tostring(res), nil end
  if j.done then pcall(j.done, res, err) end
  return true
end
function M.RunToEnd(maxSteps)
  for _ = 1, maxSteps or 100000 do if M.Step() then return true end end
  return false
end

-- ------------------------------------------------------------------ guidage par la boussole
local function feed(p, label)
  local C = compass()
  if not C then return false end
  if type(C.SetTarget) == "function" then return pcall(C.SetTarget, C, { mapID = p.mapID, x = p.x, y = p.y, label = label }) end
  if type(C.SetWaypoint) == "function" then return pcall(C.SetWaypoint, p.x, p.y, label, p.mapID) end
  return false
end

local function restore()
  local C, r = compass(), M.route
  if not (C and r) then return end
  if r.savedCustom and C.char then
    C.char.custom = r.savedCustom
    if C.Rebuild then pcall(C.Rebuild) end
    if C.Refresh then pcall(C.Refresh) end
  elseif type(C.ClearWaypoint) == "function" then
    pcall(C.ClearWaypoint)
    if r.dest.questID and C.SetQuestTarget then pcall(C.SetQuestTarget, r.dest.questID) end
  end
end

function M.FeedCurrent()
  local r = M.route
  if not r then return end
  local p = r.waypoints[r.idx]
  if not p then return end
  local last = r.idx == #r.waypoints
  local label = last and cut(r.dest.label or "Arrivée", 40)
    or ("Route sûre %d/%d → %s"):format(r.idx, #r.waypoints, cut(r.dest.label or "cible", 24))
  feed({ mapID = r.mapID, x = p.x, y = p.y }, label)
end

local function dist(a, b)
  local C = compass()
  if C and C.Geo and C.Geo.Vector then return (C.Geo.Vector(a, b)) end
end

-- auto-avance : dans 15 yd d'un point (ou d'un point plus loin) → suivant ; arrivée → boussole rendue
function M.Tick()
  local r = M.route
  if not r then return end
  local C = compass()
  local me = C and C.Player and C.Player()
  if not (me and me.mapID == r.mapID) then return end
  local jump
  for k = #r.waypoints, r.idx, -1 do
    local d = dist(me, { mapID = r.mapID, x = r.waypoints[k].x, y = r.waypoints[k].y })
    if d and d <= M.ADVANCE_YD then jump = k break end
  end
  if not jump then return end
  if jump >= #r.waypoints then
    M:Print("Route sûre : arrivé.")
    M.Stop(true)
    return
  end
  r.idx = jump + 1
  M.FeedCurrent()
end

function M.Stop(quiet)
  if M.job then M.job = nil end
  if M.route then
    restore()
    M.route = nil
    if not quiet then M:Print("Route sûre arrêtée.") end
  end
end

-- départ : cible = { mapID, x, y, label, questID? } ; sinon la cible actuelle de la boussole
function M.Go(dest)
  if inCombat() then M:Print("Route sûre : hors combat seulement.") return nil end
  local C = compass()
  if not C then M:Print("Route sûre : boussole indisponible.") return nil end
  local me = C.Player and C.Player()
  if not me then M:Print("Route sûre : position indisponible (instance ?).") return nil end
  dest = dest or (C.Current and C.Current())
  if not (dest and dest.mapID and dest.x) then M:Print("Route sûre : aucune cible (/tai cible, ou /tai sur x y).") return nil end
  if dest.mapID ~= me.mapID then M:Print("Route sûre : la cible est sur une autre carte (même zone seulement).") return nil end
  local savedCustom = C.char and type(C.char.custom) == "table" and C.char.custom or nil
  if M.route then savedCustom = M.route.savedCustom M.route = nil end
  local target = { mapID = dest.mapID, x = dest.x, y = dest.y, label = dest.label or dest.title, questID = dest.questID }
  M:Print("Route sûre : calcul…")
  return M.Start(me, target, function(res, err)
    if not res then M:Print("Route sûre : " .. tostring(err or "échec du calcul")) return end
    M.last = res
    M.route = { mapID = me.mapID, dest = target, waypoints = res.waypoints, idx = 1, savedCustom = savedCustom,
                text = res.text, at = now() }
    M:Print(res.text .. (#res.waypoints > 1 and (" — %d points, suis la boussole."):format(#res.waypoints) or ".")
      .. (T.AutoRunHint and (" " .. T.AutoRunHint() .. ".") or ""))
    if opt("announce") then
      M:Suggest("route", res.text, 2, { ttl = 30, cooldown = 5, sound = false,
        tooltip = T.AutoRunHint and T.AutoRunHint() or nil })
    end
    M.FeedCurrent()
  end)
end

function M.Toggle(rest)
  rest = tostring(rest or "")
  local x, y = rest:match("^%s*([%d%.,]+)[%s,;]+([%d%.,]+)")
  if x then
    x, y = tonumber((x:gsub(",", "."))), tonumber((y:gsub(",", ".")))
    if x and y and (x > 1 or y > 1) then x, y = x / 100, y / 100 end
    local C = compass()
    local me = C and C.Player and C.Player()
    if not (x and y and me and x >= 0 and x <= 1 and y >= 0 and y <= 1) then M:Print("Usage : /tai sur [x y]") return nil end
    return M.Go({ mapID = me.mapID, x = x, y = y, label = ("%.1f, %.1f"):format(x * 100, y * 100) })
  end
  if rest:find("off") or rest:find("stop") or M.route or M.job then M.Stop() return nil end
  return M.Go()
end

-- ------------------------------------------------------------------ cycle de vie
function M:OnEnable()
  ensureRunner()
  self:RegisterEvent("PLAYER_DEAD", function()
    local C = compass()
    local me = C and C.Player and C.Player()
    if me and M.char then
      local list = M.char.deaths
      if type(list) ~= "table" then list = {} M.char.deaths = list end
      list[#list + 1] = { mapID = me.mapID, x = me.x, y = me.y, t = now() }
      while #list > 10 do table.remove(list, 1) end
    end
  end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function()
    local before = #M.live
    if not (M.route and M.NoteLive() and #M.live > before) then return end
    M.dirty = true
    -- hors combat : nouveau calcul depuis ici (au plus toutes les 5 s)
    if not inCombat() and not M.job and now() - (M.route.at or 0) >= 5 then
      local r = M.route
      T:After(1, function() if M.route == r and not M.job and not inCombat() then M.dirty = nil M.Go(r.dest) end end)
    end
  end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    if M.dirty and M.route and not M.job then
      M.dirty = nil
      local r = M.route
      T:After(1, function() if M.route == r then M.Go(r.dest) end end)
    end
  end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() if M.route then M.Stop() end end)
  if not M.ticker then M.ticker = T:NewTicker(0.5, function() pcall(M.Tick) end) end
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
  M.Stop(true)
end

T:RegisterSlash("sur", function(rest) M.Toggle(rest) end,
  "route sûre vers la cible de la boussole (évite élites / rouges) : /tai sur [x y] · /tai sur off")
