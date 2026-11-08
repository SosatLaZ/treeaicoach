-- TreeAI — module Radar (agent RADAR) : un petit radar ROND, propre, à nous (jamais la minimap de Blizzard).
-- Verre sombre discret, fin anneau doré, cercles de distance, flèche du joueur au centre (carte nord en haut,
-- ou carte qui tourne avec toi), zoom à la molette (50–400 yd). Points :
--   objectifs des quêtes en cours (amas d'apparitions Questie / API du jeu, via la Boussole), PNJ de remise,
--   cible de la boussole + étapes du planificateur + chemin sûr (TreeAI.SafePath si présent),
--   amis du groupe (TreeAI.Squad + positions du groupe, couleur de classe, flèche au bord si loin),
--   rares vus, élites (zones dangereuses), ton corps, maîtres de vol (C_TaxiMap, connus en vif).
-- Points loin = collés au bord dans leur direction. Survol = infobulle, clic = cible de la boussole.
-- Mise à jour fluide 20 i/s SEULEMENT quand le radar est visible (OnUpdate d'un cadre caché ne tourne pas),
-- calcul lourd (Questie) hors combat et mis en cache en coordonnées monde. Caché en instance.
-- Taint : cadres simples enfants d'UIParent, aucun cadre Blizzard touché, aucune automatisation.
-- Géométrie pure testable : R.Project / R.WorldToMap / R.Cluster. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local LAYERS = { "quests", "turnins", "target", "path", "squad", "rares", "death", "taxi", "danger" }
local R = T:RegisterModule("Radar", {
  title = "Radar",
  defaults = { enabled = true, shown = false, size = 170, zoom = 150, opacity = 0.8, rotate = false,
               hideCombat = false, locked = false, rings = true, compact = false, labels = true, heading = true,
               quests = true, turnins = true, target = true, path = true, squad = true, rares = true,
               death = true, taxi = true, danger = true },
  charDefaults = { point = "TOPRIGHT", x = -210, y = -24, death = nil, rares = {}, dangers = {} },
  options = {
    { key = "shown", type = "toggle", label = "Afficher le radar", section = "Radar" },
    { key = "rotate", type = "toggle", label = "La carte tourne avec moi (sinon nord en haut)", section = "Radar" },
    { key = "rings", type = "toggle", label = "Cercles de distance", section = "Radar" },
    { key = "hideCombat", type = "toggle", label = "Cacher en combat", section = "Radar" },
    { key = "locked", type = "toggle", label = "Verrouiller la position", section = "Radar" },
    { key = "compact", type = "toggle", label = "Radar compact (petit, 120 px)", section = "Apparence" },
    { key = "labels", type = "toggle", label = "Distances écrites (cercle, cible)", section = "Apparence" },
    { key = "heading", type = "toggle", label = "Repère de direction sur le bord", section = "Apparence" },
    { key = "size", type = "range", label = "Taille (pixels)", min = 110, max = 300, step = 10, section = "Apparence" },
    { key = "zoom", type = "range", label = "Portée (yards, molette)", min = 50, max = 400, step = 10, section = "Apparence" },
    { key = "opacity", type = "range", label = "Opacité du fond", min = 0.2, max = 1, step = 0.05, section = "Apparence" },
    { key = "quests", type = "toggle", label = "Objectifs de quête (zones d'apparition)", section = "Points" },
    { key = "turnins", type = "toggle", label = "PNJ de remise", section = "Points" },
    { key = "target", type = "toggle", label = "Cible de la boussole / plan", section = "Points" },
    { key = "path", type = "toggle", label = "Chemin sûr (étapes)", section = "Points" },
    { key = "squad", type = "toggle", label = "Amis du groupe", section = "Points" },
    { key = "rares", type = "toggle", label = "Rares vus", section = "Points" },
    { key = "death", type = "toggle", label = "Mon corps", section = "Points" },
    { key = "taxi", type = "toggle", label = "Maîtres de vol", section = "Points" },
    { key = "danger", type = "toggle", label = "Élites (zones dangereuses)", section = "Points" },
  },
})
ns.Radar = R
T.Radar = R
R.LAYERS = LAYERS

local floor, sqrt, atan2, cos, sin, pi, abs = math.floor, math.sqrt, math.atan2, math.cos, math.sin, math.pi, math.abs
local MIN_ZOOM, MAX_ZOOM = 50, 400
local FPS = 1 / 20
local CIRCLE = "Interface\\CharacterFrame\\TempPortraitAlphaMask"
local ARROW = "Interface\\Minimap\\MinimapArrow"
local GOLD = { 0.95, 0.78, 0.32 }

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function now() return try(_G.GetTime) or 0 end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function secret(v)
  local f = rawget(_G, "issecretvalue")
  return type(f) == "function" and try(f, v) and true or false
end
local function num(v) if secret(v) then return nil end return tonumber(v) end
local function opt(k) return R.db and R.db[k] end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function compass() return T.Compass or mod("Compass") end

-- ------------------------------------------------------------------ géométrie (pure, testable)
local function xy(p)
  if type(p) ~= "table" then return nil end
  if type(p.GetXY) == "function" then
    local x, y = try(p.GetXY, p)
    if x then return x, y end
  end
  return p.x, p.y
end
local function vec(x, y)
  local mk = rawget(_G, "CreateVector2D")
  if type(mk) == "function" then
    local v = try(mk, x, y)
    if v then return v end
  end
  return { x = x, y = y, GetXY = function(p) return p.x, p.y end }
end

-- point de carte (0..1) → continent, nord, ouest (yards, convention WoW : X monde = nord, Y monde = ouest)
function R.World(mapID, x, y)
  local CM = _G.C_Map
  if not (CM and CM.GetWorldPosFromMapPos and mapID and x and y) then return nil end
  local c, w = try(CM.GetWorldPosFromMapPos, mapID, vec(x, y))
  local n, wst = xy(w)
  n, wst = num(n), num(wst)
  if not (c and n and wst) then return nil end
  return c, n, wst
end

-- inverse : monde → carte (0..1) ; nil hors des bornes de la carte (± marge)
local rectCache = {}
function R.WorldToMap(mapID, c, n, w)
  if not (mapID and n and w) then return nil end
  local r = rectCache[mapID]
  if not r then
    local c1, top, left = R.World(mapID, 0, 0)
    local c2, bottom, right = R.World(mapID, 1, 1)
    if not (c1 and c2 and c1 == c2) or top == bottom or left == right then return nil end
    r = { c1, top, left, bottom, right }
    rectCache[mapID] = r
  end
  if c and r[1] ~= c then return nil end
  local x, y = (r[3] - w) / (r[3] - r[5]), (r[2] - n) / (r[2] - r[4])
  if x < -0.05 or x > 1.05 or y < -0.05 or y > 1.05 then return nil end
  return x, y
end

-- Projection écran d'un point à (dn yards au nord, dw yards à l'ouest) du joueur.
-- facing = GetPlayerFacing (0 = nord, sens anti-horaire) ; rotate = la carte tourne (le haut = devant moi).
-- zoom = yards au bord ; radius = pixels au bord. Retourne px (droite +), py (haut +), dist (yd), edge
-- (true si au-delà du zoom : point collé au bord, à exactement `radius` px du centre), angle écran.
function R.Project(dn, dw, facing, rotate, zoom, radius)
  local sx, sy = -dw, dn
  if rotate and facing then
    local c, s = cos(-facing), sin(-facing)
    sx, sy = sx * c - sy * s, sx * s + sy * c
  end
  local dist = sqrt(dn * dn + dw * dw)
  local k = radius / zoom
  local px, py = sx * k, sy * k
  local edge = dist > zoom
  if edge and dist > 0 then
    local f = radius / (dist * k)
    px, py = px * f, py * f
  end
  return px, py, dist, edge, atan2(py, px)
end

-- regroupe des points monde proches (≤ cell yards) : { {c,n,w,count,name}, ... } (centre = moyenne)
function R.Cluster(points, cell)
  cell = cell or 40
  local out = {}
  for _, p in ipairs(points or {}) do
    local hit
    for _, q in ipairs(out) do
      if q.c == p.c and abs(q.n - p.n) <= cell and abs(q.w - p.w) <= cell then hit = q break end
    end
    if hit then
      local k = hit.count
      hit.n, hit.w, hit.count = (hit.n * k + p.n) / (k + 1), (hit.w * k + p.w) / (k + 1), k + 1
      hit.name = hit.name or p.name
    else
      out[#out + 1] = { c = p.c, n = p.n, w = p.w, count = 1, name = p.name }
    end
  end
  return out
end

function R.ClampZoom(z)
  z = tonumber(z) or 150
  if z < MIN_ZOOM then z = MIN_ZOOM elseif z > MAX_ZOOM then z = MAX_ZOOM end
  return z
end

function R.Dist(yd)
  local C = compass()
  if C and C.Geo and C.Geo.Dist then return C.Geo.Dist(yd, C.db and C.db.units) end
  return floor((yd or 0) * 0.9144 + 0.5) .. " m"
end

-- ------------------------------------------------------------------ position du joueur
-- { mapID, x, y, c, n, w } ou nil, raison
function R.Me()
  local inst = try(_G.IsInInstance)
  if inst then return nil, "instance" end
  local CM = _G.C_Map
  local mapID = CM and CM.GetBestMapForUnit and num(try(CM.GetBestMapForUnit, "player"))
  if mapID and CM.GetPlayerMapPosition then
    local x, y = xy(try(CM.GetPlayerMapPosition, mapID, "player"))
    x, y = num(x), num(y)
    if x and y and not (x == 0 and y == 0) then
      local c, n, w = R.World(mapID, x, y)
      if c then return { mapID = mapID, x = x, y = y, c = c, n = n, w = w } end
    end
  end
  local n, w, _, inst2 = try(_G.UnitPosition, "player")
  n, w = num(n), num(w)
  if n and w then return { mapID = mapID, c = num(inst2) or -1, n = n, w = w } end
  return nil, "pos"
end

-- ------------------------------------------------------------------ sources de points
-- pin = { key, kind, layer, c, n, w, label, lines = {..}, count, color = {r,g,b}, mapID, x, y, questID, far }
local PRIO = { danger = 1, taxi = 2, quest = 3, turnin = 4, path = 5, treasure = 6, rare = 6, death = 7, target = 8, squad = 9 }
local CLASS_FR = { HUNTER = "Chasseur", WARRIOR = "Guerrier", MAGE = "Mage", PRIEST = "Prêtre", ROGUE = "Voleur",
  DRUID = "Druide", PALADIN = "Paladin", SHAMAN = "Chaman", WARLOCK = "Démoniste" }
local CLASS_RGB = { HUNTER = { 0.67, 0.83, 0.45 }, WARRIOR = { 0.78, 0.61, 0.43 }, MAGE = { 0.25, 0.78, 0.92 },
  PRIEST = { 1, 1, 1 }, ROGUE = { 1, 0.96, 0.41 }, DRUID = { 1, 0.49, 0.04 }, PALADIN = { 0.96, 0.55, 0.73 },
  SHAMAN = { 0, 0.44, 0.87 }, WARLOCK = { 0.53, 0.53, 0.93 } }
local function classRGB(c)
  local rc = rawget(_G, "RAID_CLASS_COLORS")
  local e = type(rc) == "table" and c and rc[c]
  if type(e) == "table" and e.r then return { e.r, e.g, e.b } end
  return CLASS_RGB[c] or { 0.8, 0.8, 0.8 }
end
R.ClassRGB = classRGB

local function addMapPin(out, me, t)
  if not (t.mapID and t.x and t.y) then return nil end
  local x, y = t.x, t.y
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  local c, n, w = R.World(t.mapID, x, y)
  if not c or (me and me.c ~= c) then return nil end
  t.c, t.n, t.w, t.x, t.y = c, n, w, x, y
  out[#out + 1] = t
  return t
end

-- objectifs + remises : lourd (Questie) → hors combat, mis en cache (monde) toutes les ~6 s ou sur évènement
R.questCache = { at = -1e9, pins = {} }
function R.BuildQuestPins(me)
  local C, Q = compass(), mod("Quests")
  local out = {}
  if not (C and C.QuestPoints and Q and me and me.mapID) then return out end
  local tgt = C.Current and C.Current()
  for _, q in ipairs(Q.lastList or {}) do
    if q.id and not q.failed then
      local okp, pts = pcall(C.QuestPoints, q, { mapID = me.mapID, x = me.x, y = me.y })
      local world = {}
      for _, p in ipairs(okp and type(pts) == "table" and pts or {}) do
        if #world >= 60 then break end
        local c, n, w = R.World(p.mapID, p.x, p.y)
        if c and c == me.c then world[#world + 1] = { c = c, n = n, w = w, name = p.name } end
      end
      local kind = q.complete and "turnin" or "quest"
      local clusters = R.Cluster(world, q.complete and 15 or 45)
      for i, cl in ipairs(clusters) do
        if i > 8 then break end
        local lines = {}
        if q.complete then lines[#lines + 1] = { "Prête à rendre", 0.4, 1, 0.4 }
        else
          for _, o in ipairs(q.obj or {}) do
            if not o.finished then
              lines[#lines + 1] = { (o.need or 1) > 1 and ("%s %d/%d"):format(o.name or "?", o.have or 0, o.need) or (o.name or "?"), 1, 0.82, 0 }
            end
          end
          if cl.count > 1 then lines[#lines + 1] = { cl.count .. " apparitions ici", 0.7, 0.7, 0.7 } end
        end
        if cl.name then lines[#lines + 1] = { (q.complete and "PNJ : " or "") .. cl.name, 0.8, 0.8, 0.8 } end
        out[#out + 1] = { key = kind .. ":" .. q.id .. ":" .. i, kind = kind, layer = q.complete and "turnins" or "quests",
          c = cl.c, n = cl.n, w = cl.w, count = cl.count, questID = q.id,
          label = q.complete and ("Rendre « " .. cut(q.title, 34) .. " »") or cut(q.title, 40), lines = lines,
          tracked = (tgt and tgt.questID == q.id) or nil }
      end
    end
  end
  return out
end

function R.RefreshQuests(me, force)
  local t = now()
  if inCombat() and not force then return R.questCache.pins end
  if not force and not R.questDirty and t - R.questCache.at < 6 then return R.questCache.pins end
  if not me then return R.questCache.pins end
  local ok, pins = pcall(R.BuildQuestPins, me)
  R.questCache = { at = t, pins = ok and pins or {}, map = me.mapID }
  R.questDirty = nil
  return R.questCache.pins
end

-- cible de la boussole + étapes du planificateur
function R.TargetPins(me)
  local out = {}
  local C = compass()
  local t = C and C.Current and C.Current()
  if t and t.mapID and t.x then
    addMapPin(out, me, { key = "target", kind = "target", layer = "target", mapID = t.mapID, x = t.x, y = t.y,
      label = cut(t.label or t.title or "Cible", 50), questID = t.questID,
      lines = { { "Cible de la boussole", 1, 0.82, 0 }, t.zone and { t.zone, 0.7, 0.7, 0.7 } or nil } })
  end
  local P = T.Planner
  if P and type(P.GetActiveSteps) == "function" then
    local steps = try(P.GetActiveSteps, P, 3)
    for i, s in ipairs(type(steps) == "table" and steps or {}) do
      if s.map and s.x and s.y then
        addMapPin(out, me, { key = "plan:" .. i, kind = "target", layer = "target", mapID = s.map, x = s.x, y = s.y,
          label = cut(s.label or "Étape", 50), step = i, questID = s.questID or s.quest,
          lines = { { ("Plan : étape %d"):format(i), 0.6, 0.9, 1 } } })
      end
    end
  end
  return out
end

-- chemin sûr : TreeAI.SafePath (module optionnel, lecture seule) → liste de { mapID|map, x, y, label? }
function R.PathPins(me)
  local out = {}
  local SP = T.SafePath or mod("SafePath")
  if type(SP) ~= "table" then return out end
  local wps
  for _, fn in ipairs({ "GetWaypoints", "Waypoints", "GetPath" }) do
    if type(SP[fn]) == "function" then wps = try(SP[fn], SP) if type(wps) == "table" then break end end
  end
  wps = type(wps) == "table" and wps or (type(SP.waypoints) == "table" and SP.waypoints) or (type(SP.path) == "table" and SP.path) or {}
  for i, p in ipairs(wps) do
    if i > 24 then break end
    if type(p) == "table" then
      addMapPin(out, me, { key = "path:" .. i, kind = "path", layer = "path", mapID = p.mapID or p.map, x = p.x, y = p.y,
        label = cut(p.label or p.name or ("Chemin sûr — étape " .. i), 50), lines = { { "Chemin sûr", 0.5, 0.9, 1 } } })
    end
  end
  return out
end

-- amis : positions en direct du groupe (C_Map / UnitPosition) puis état Squad (UnitPosition arrondie à 5 yd)
function R.SquadPins(me)
  local out, seen = {}, {}
  if not me then return out end
  local raid = try(_G.IsInRaid)
  local prefix, n = raid and "raid" or "party", raid and 40 or 4
  local CM = _G.C_Map
  for i = 1, n do
    local u = prefix .. i
    if try(_G.UnitExists, u) and not try(_G.UnitIsUnit, u, "player") then
      local name = try(_G.UnitName, u)
      if type(name) == "string" and not secret(name) then
        local c, pn, pw
        if me.mapID and CM and CM.GetPlayerMapPosition then
          local x, y = xy(try(CM.GetPlayerMapPosition, me.mapID, u))
          x, y = num(x), num(y)
          if x and y and not (x == 0 and y == 0) then c, pn, pw = R.World(me.mapID, x, y) end
        end
        if not c then
          local a, b, _, inst = try(_G.UnitPosition, u)
          a, b = num(a), num(b)
          if a and b then c, pn, pw = num(inst) or me.c, a, b end
        end
        if c and c == me.c then
          local _, cls = try(_G.UnitClass, u)
          seen[name] = true
          out[#out + 1] = { key = "sq:" .. name, kind = "squad", layer = "squad", c = c, n = pn, w = pw, label = name,
            class = cls, color = classRGB(cls), dead = try(_G.UnitIsDeadOrGhost, u) and true or nil,
            lines = { { (CLASS_FR[cls] or cls or "?") .. (try(_G.UnitLevel, u) and (" " .. tostring(try(_G.UnitLevel, u))) or ""), 0.8, 0.8, 0.8 } } }
        end
      end
    end
  end
  local S = T.Squad
  local members = type(S) == "table" and type(S._members) == "table" and S._members or {}
  local t = now()
  for name, m in pairs(members) do
    local st = type(m) == "table" and m.st
    if not seen[name] and type(st) == "table" and tonumber(st.py) and tonumber(st.px)
      and (st.pi == nil or st.pi == me.c or st.pi == -1) and (not m.seen or t - m.seen < 120) then
      out[#out + 1] = { key = "sq:" .. name, kind = "squad", layer = "squad", c = me.c, n = st.py, w = st.px, label = name,
        class = st.c, color = classRGB(st.c), dead = (tonumber(st.d) or 0) >= 1 or nil,
        lines = { { (CLASS_FR[st.c] or st.c or "?") .. " " .. tostring(st.l or "?") .. (st.z and (" — " .. st.z) or ""), 0.8, 0.8, 0.8 },
          { "Position partagée (TreeAI)", 0.6, 0.6, 0.6 } } }
    end
  end
  return out
end

-- rares / élites vus (nos relevés : position du joueur au moment où on les voit, ± 40 yd)
local RARE_TTL, DANGER_TTL = 2 * 3600, 7 * 86400
local function epoch() return try(_G.time) or floor(now()) end
function R.Record(list, me, name, extra, maxn, ttl)
  if not (list and me and name) then return nil end
  local t = epoch()
  for i = #list, 1, -1 do if t - (list[i].t or 0) > ttl then table.remove(list, i) end end
  for _, e in ipairs(list) do
    if e.name == name and e.c == me.c and abs(e.n - me.n) < 80 and abs(e.w - me.w) < 80 then
      e.t, e.seen = t, (e.seen or 1) + 1
      return e
    end
  end
  local e = { name = name, c = me.c, n = floor(me.n + 0.5), w = floor(me.w + 0.5), t = t, seen = 1 }
  for k, v in pairs(extra or {}) do e[k] = v end
  list[#list + 1] = e
  while #list > maxn do table.remove(list, 1) end
  return e
end

function R:ScanUnit(unit)
  if not (unit and R.char) or not try(_G.UnitExists, unit) or try(_G.UnitIsPlayer, unit) then return end
  local cls = try(_G.UnitClassification, unit)
  if secret(cls) or type(cls) ~= "string" then return end
  local rare = cls == "rare" or cls == "rareelite"
  local elite = cls == "elite" or cls == "rareelite" or cls == "worldboss"
  if not (rare or elite) then return end
  if try(_G.UnitIsDead, unit) then return end
  local name = try(_G.UnitName, unit)
  if secret(name) or type(name) ~= "string" then return end
  local me = R.Me()
  if not me then return end
  local lvl = num(try(_G.UnitLevel, unit))
  if rare then R.Record(R.char.rares, me, name, { lvl = lvl, elite = elite or nil }, 40, RARE_TTL) end
  if elite and try(_G.UnitCanAttack, "player", unit) then
    R.Record(R.char.dangers, me, name, { lvl = lvl }, 60, DANGER_TTL)
  end
  R.slowAt = 0
end

local function ago(t)
  local s = epoch() - (t or 0)
  if s < 60 then return "à l'instant" end
  if s < 3600 then return ("il y a %d min"):format(floor(s / 60)) end
  if s < 86400 then return ("il y a %d h"):format(floor(s / 3600)) end
  return ("il y a %d j"):format(floor(s / 86400))
end

function R.SeenPins(me)
  local out = {}
  if not (me and R.char) then return out end
  local t = epoch()
  for i, e in ipairs(R.char.rares or {}) do
    if e.c == me.c and t - (e.t or 0) <= RARE_TTL then
      out[#out + 1] = { key = "rare:" .. i, kind = "rare", layer = "rares", c = e.c, n = e.n, w = e.w,
        label = (e.elite and "Rare élite : " or "Rare : ") .. e.name .. (e.lvl and (" (" .. e.lvl .. ")") or ""),
        lines = { { "Vu " .. ago(e.t) .. " (± 40 m)", 0.8, 0.6, 1 } } }
    end
  end
  for i, e in ipairs(R.char.dangers or {}) do
    if e.c == me.c then
      out[#out + 1] = { key = "dg:" .. i, kind = "danger", layer = "danger", c = e.c, n = e.n, w = e.w, far = false,
        label = "Élite : " .. e.name .. (e.lvl and (" (" .. (e.lvl < 0 and "??" or e.lvl) .. ")") or ""),
        lines = { { "Zone dangereuse — à plusieurs, ou évite", 1, 0.4, 0.3 }, { "Vu " .. ago(e.t), 0.6, 0.6, 0.6 } } }
    end
  end
  local d = R.char.death
  if type(d) == "table" and d.c == me.c then
    out[#out + 1] = { key = "death", kind = "death", layer = "death", c = d.c, n = d.n, w = d.w, label = "Ton corps",
      lines = { { "Mort " .. ago(d.t), 0.8, 0.8, 0.8 } } }
  end
  return out
end

-- maîtres de vol : C_TaxiMap.GetTaxiNodesForMap(carte) (positions relatives à cette carte), cache 60 s
local taxiCache = {}
function R.TaxiPins(me)
  local out = {}
  local TM = _G.C_TaxiMap
  if not (me and me.mapID and TM and TM.GetTaxiNodesForMap) then return out end
  local tc = taxiCache[me.mapID]
  if not tc or now() - tc.at > 60 then
    local list, seenNode = {}, {}
    local maps = { me.mapID }
    local info = _G.C_Map and _G.C_Map.GetMapInfo and try(_G.C_Map.GetMapInfo, me.mapID)
    if type(info) == "table" and info.parentMapID and info.parentMapID ~= me.mapID then maps[2] = info.parentMapID end
    local fac = try(_G.UnitFactionGroup, "player")
    local myFac = fac == "Horde" and 1 or (fac == "Alliance" and 2) or nil
    for _, m in ipairs(maps) do
      for _, nd in ipairs(try(TM.GetTaxiNodesForMap, m) or {}) do
        if type(nd) == "table" and nd.nodeID and not seenNode[nd.nodeID] then
          local x, y = xy(nd.position)
          local f = tonumber(nd.faction)
          if x and y and (not f or f == 0 or not myFac or f == myFac) then
            local c, n, w = R.World(m, x, y)
            if c then
              seenNode[nd.nodeID] = true
              list[#list + 1] = { c = c, n = n, w = w, name = nd.name, known = nd.isUndiscovered ~= true, id = nd.nodeID }
            end
          end
        end
      end
    end
    tc = { at = now(), list = list }
    taxiCache[me.mapID] = tc
  end
  for _, e in ipairs(tc.list) do
    if e.c == me.c then
      out[#out + 1] = { key = "taxi:" .. e.id, kind = "taxi", layer = "taxi", c = e.c, n = e.n, w = e.w, known = e.known,
        label = "Vol : " .. cut(e.name or "?", 40),
        lines = { e.known and { "Trajet connu", 0.4, 1, 0.4 } or { "Pas encore découvert", 0.6, 0.6, 0.6 } } }
    end
  end
  return out
end

-- rares / trésors des vignettes du jeu (TreeAI.Detect : position exacte, mieux que nos relevés ± 40 m)
function R.DetectPins(me)
  local out = {}
  local Dt = T.Detect
  if not (me and type(Dt) == "table" and type(Dt.Pins) == "function") then return out end
  local ok, pins = pcall(Dt.Pins)
  for _, p in ipairs(ok and type(pins) == "table" and pins or {}) do
    if #out >= 20 then break end
    if not p.approx then
      addMapPin(out, me, { key = "dt:" .. tostring(p.key), kind = p.kind, layer = "rares", mapID = p.mapID, x = p.x, y = p.y,
        label = p.label, lines = { { "Vu par le jeu (vignette) — clic : y aller", 0.8, 0.6, 1 } } })
    end
  end
  return out
end

-- toutes les sources (couches activées), triées par priorité d'affichage
function R.Collect(me)
  local pins = {}
  if not me then return pins end
  local function add(layerKey, fn)
    if opt(layerKey) == false then return end
    local ok, list = pcall(fn, me)
    if ok and type(list) == "table" then
      for _, p in ipairs(list) do if opt(p.layer or layerKey) ~= false then pins[#pins + 1] = p end end
    end
  end
  if opt("quests") ~= false or opt("turnins") ~= false then add("_quests", function(m) return R.RefreshQuests(m) end) end
  add("target", R.TargetPins)
  add("path", R.PathPins)
  add("squad", R.SquadPins)
  add("taxi", R.TaxiPins)
  add("rares", R.DetectPins)
  if opt("rares") ~= false or opt("danger") ~= false or opt("death") ~= false then
    local ok, list = pcall(R.SeenPins, me)
    for _, p in ipairs(ok and list or {}) do if opt(p.layer) ~= false then pins[#pins + 1] = p end end
  end
  table.sort(pins, function(a, b)
    local pa, pb = PRIO[a.kind] or 0, PRIO[b.kind] or 0
    if pa ~= pb then return pa < pb end
    return tostring(a.key) < tostring(b.key)
  end)
  while #pins > 80 do table.remove(pins, 1) end  -- garde les plus importants
  return pins
end

-- ------------------------------------------------------------------ clic : cible de la boussole
function R.SetTarget(pin)
  local C = compass()
  if not (pin and C) then return nil end
  if pin.questID and C.SetQuestTarget then
    local t = C.SetQuestTarget(pin.questID)
    if t then return t end
  end
  local me = R.Me()
  local mapID = pin.mapID or (me and me.mapID)
  local x, y = pin.x, pin.y
  if not (pin.mapID and x) then x, y = R.WorldToMap(mapID, pin.c, pin.n, pin.w) end
  if not (x and C.SetWaypoint) then return nil end
  return C.SetWaypoint(x, y, cut(pin.label or "Radar", 40), mapID)
end

-- ------------------------------------------------------------------ cadre (le nôtre, jamais Blizzard)
local frame, glass, sheen, arrow, north, zoomText, statusText, ringLabel, headTick
local ringSegs, pinPool = {}, {}
R.pinPool = pinPool

local ICON = {
  quest = { CIRCLE, { 1, 0.82, 0.1 } },
  turnin = { "Interface\\GossipFrame\\ActiveQuestIcon" },
  target = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_1" },
  path = { CIRCLE, { 0.35, 0.85, 1 } },
  squad = { CIRCLE },
  rare = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_3" },
  treasure = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_1" },
  danger = { "Interface\\TargetingFrame\\UI-RaidTargetingIcon_8", { 1, 0.45, 0.4 } },
  death = { "Interface\\WorldStateFrame\\SkullBones", nil, { 0, 0.5, 0, 0.5 } },
  taxi = { "Interface\\TaxiFrame\\UI-Taxi-Icon-Green" },
}
local SIZE = { quest = 9, turnin = 14, target = 14, path = 6, squad = 10, rare = 13, treasure = 12, danger = 12, death = 14, taxi = 13 }

local COMPACT = 120
local function size()
  if R.db and R.db.compact then return COMPACT end
  return R.db and tonumber(R.db.size) or 170
end
R.Size = size
local function radius() return size() / 2 - 10 end

local function showTip(owner, pin)
  local tt = _G.GameTooltip
  if not (tt and pin) then return end
  try(tt.SetOwner, tt, owner, "ANCHOR_LEFT")
  try(tt.AddLine, tt, pin.label or "?", 1, 1, 1, true)
  if pin.dist then try(tt.AddLine, tt, R.Dist(pin.dist), 0.6, 0.8, 1) end
  for _, l in ipairs(pin.lines or {}) do
    if type(l) == "table" then try(tt.AddLine, tt, l[1], l[2] or 0.8, l[3] or 0.8, l[4] or 0.8, true) end
  end
  try(tt.AddLine, tt, "Clic : cible de la boussole", 0.5, 0.5, 0.5)
  try(tt.Show, tt)
end
local function hideTip() local tt = _G.GameTooltip if tt then try(tt.Hide, tt) end end

local function newPin()
  local b = _G.CreateFrame("Button", nil, frame)
  b:SetSize(12, 12)
  b:SetFrameLevel((frame:GetFrameLevel() or 1) + 3)
  b:EnableMouse(true)
  b:RegisterForClicks("LeftButtonUp")
  b.outline = b:CreateTexture(nil, "ARTWORK")
  b.outline:SetTexture(CIRCLE)
  b.outline:SetVertexColor(0, 0, 0, 0.95)
  b.outline:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.icon = b:CreateTexture(nil, "OVERLAY")
  b.icon:SetAllPoints(b)
  b.edgeArrow = b:CreateTexture(nil, "OVERLAY")
  b.edgeArrow:SetTexture(ARROW)
  b.edgeArrow:SetSize(10, 10)
  b.edgeArrow:Hide()
  b.count = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  b.count:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.dist = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.dist:SetPoint("TOP", b, "BOTTOM", 0, -1)
  b.dist:Hide()
  b:SetScript("OnEnter", function(self) showTip(self, self.pin) end)
  b:SetScript("OnLeave", hideTip)
  b:SetScript("OnClick", function(self)
    if not self.pin or (self.pin.dist or 1e9) < 5 then return end  -- déjà dessus : rien à viser
    local t = R.SetTarget(self.pin)
    if t then R:Print("Boussole → " .. cut(self.pin and self.pin.label or "?", 50)) end
  end)
  pinPool[#pinPool + 1] = b
  return b
end

local function stylePin(b, p)
  local ic = ICON[p.kind] or ICON.quest
  local sz = SIZE[p.kind] or 10
  if p.kind == "quest" and (p.count or 1) > 1 then sz = sz + math.min(5, p.count) end
  if p.tracked then sz = sz + 3 end
  local tex = ic[1]
  if p.kind == "taxi" and not p.known then tex = "Interface\\TaxiFrame\\UI-Taxi-Icon-Gray" end
  b.icon:SetTexture(tex)
  if ic[3] then b.icon:SetTexCoord(ic[3][1], ic[3][2], ic[3][3], ic[3][4]) else b.icon:SetTexCoord(0, 1, 0, 1) end
  local col = p.color or ic[2]
  if col then b.icon:SetVertexColor(col[1], col[2], col[3], 1) else b.icon:SetVertexColor(1, 1, 1, 1) end
  if p.kind == "squad" and p.dead then b.icon:SetVertexColor(0.5, 0.5, 0.5, 1) end
  local round = tex == CIRCLE
  b.outline:SetShown(round)
  b.outline:SetSize(sz + 4, sz + 4)
  b.size = sz
  b:SetSize(sz, sz)
  b.count:SetText("")
  b.edgeArrow:SetVertexColor((col or GOLD)[1], (col or GOLD)[2], (col or GOLD)[3], 1)
end

local function placeRing(segs, r, n, a)
  -- polygone de n segments (Line) ; repli : n points (textures)
  for i = 1, n do
    local s = segs[i]
    local a1, a2 = (i - 1) / n * 2 * pi, i / n * 2 * pi
    if s.SetStartPoint and s.SetEndPoint then
      s:SetStartPoint("CENTER", frame, r * cos(a1), r * sin(a1))
      s:SetEndPoint("CENTER", frame, r * cos(a2), r * sin(a2))
    else
      s:ClearAllPoints()
      s:SetPoint("CENTER", frame, "CENTER", r * cos(a1), r * sin(a1))
    end
    if s.SetAlpha then s:SetAlpha(a) end
  end
end

local function makeRing(n, col, alpha, thick)
  local segs = {}
  for i = 1, n do
    local s
    if frame.CreateLine then s = frame:CreateLine(nil, "ARTWORK") end
    if s and s.SetThickness then
      s:SetThickness(thick)
      s:SetColorTexture(col[1], col[2], col[3], 1)
    else
      s = frame:CreateTexture(nil, "ARTWORK")
      s:SetSize(thick + 0.5, thick + 0.5)
      s:SetColorTexture(col[1], col[2], col[3], 1)
    end
    s.baseAlpha = alpha
    segs[i] = s
  end
  return segs
end

function R.Layout()
  if not frame then return end
  local sz = size()
  frame:SetSize(sz, sz)
  local r = radius()
  glass:SetSize(2 * r + 6, 2 * r + 6)
  glass:SetAlpha(tonumber(opt("opacity")) or 0.8)
  sheen:SetSize(2 * r * 0.78, 2 * r * 0.62)
  placeRing(ringSegs.rim, r + 2, #ringSegs.rim, 0.95)
  placeRing(ringSegs.halo, r + 3.5, #ringSegs.halo, 0.25)
  local rings = opt("rings") ~= false
  placeRing(ringSegs.mid, r / 2, #ringSegs.mid, rings and 0.22 or 0)
  placeRing(ringSegs.inner, r / 4, #ringSegs.inner, rings and 0.12 or 0)
  if ringLabel then
    ringLabel:ClearAllPoints()
    ringLabel:SetPoint("BOTTOMLEFT", frame, "CENTER", r * 0.36, r * 0.36)
  end
  R.lastSig = nil
  if R.controls then R.PlaceControls() end
end

-- zoom fluide : 35 % du chemin vers le but à chaque image (20 im/s)
function R.StepZoom()
  local goal = R.zoomGoal
  if not goal then return end
  local z = tonumber(opt("zoom")) or goal
  if abs(goal - z) < 1 then R.SetZoom(goal, true) R.zoomGoal = nil
  else R.SetZoom(z + (goal - z) * 0.35, true) end
  R.lastSig = nil
end

-- survol du radar : légende (couleur · couche · nombre de points) + portée
local LEGEND = {
  { "quest", "quests", "Objectifs de quête" }, { "turnin", "turnins", "PNJ de remise" },
  { "target", "target", "Cible de la boussole" }, { "path", "path", "Chemin sûr" }, { "squad", "squad", "Amis du groupe" },
  { "rare", "rares", "Rares vus" }, { "danger", "danger", "Élites (danger)" }, { "death", "death", "Mon corps" },
  { "taxi", "taxi", "Maîtres de vol" }, { "treasure", "rares", "Trésors" },
}
R.LEGEND = LEGEND
function R.ShowLegend(owner)
  local tt = _G.GameTooltip
  if not tt then return nil end
  local counts = {}
  for _, p in ipairs(R.pins or {}) do counts[p.kind] = (counts[p.kind] or 0) + 1 end
  try(tt.SetOwner, tt, owner, "ANCHOR_LEFT")
  try(tt.AddLine, tt, "Radar — portée " .. R.Dist(R.ClampZoom(opt("zoom"))), 1, 1, 1)
  local n = 0
  for _, l in ipairs(LEGEND) do
    if opt(l[2]) ~= false and (counts[l[1]] or 0) > 0 then
      local col = (ICON[l[1]] and ICON[l[1]][2]) or GOLD
      try(tt.AddLine, tt, ("|T%s:12|t %s : %d"):format(ICON[l[1]][1], l[3], counts[l[1]]), col[1], col[2], col[3])
      n = n + 1
    end
  end
  if n == 0 then try(tt.AddLine, tt, "Aucun point à portée.", 0.6, 0.6, 0.6) end
  try(tt.AddLine, tt, "Molette : zoom · clic droit : menu · survol d'un point : détail", 0.5, 0.5, 0.5, true)
  try(tt.Show, tt)
  return n
end

local function savePos()
  if not (frame and R.char) then return end
  local p, _, _, x, y = frame:GetPoint(1)
  R.char.point, R.char.x, R.char.y = p or "TOPRIGHT", x or 0, y or 0
end

function R.SetZoom(z, quiet)
  z = R.ClampZoom(z)
  if R.db then R.db.zoom = z end
  if zoomText then
    zoomText:SetText(R.Dist(z))
    if not quiet then R.zoomShownAt = now() zoomText:Show() end
  end
  return z
end

local function createFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Frame", "TreeAIRadarFrame", _G.UIParent)
  R.frame = frame
  local sz0 = size()
  frame:SetSize(sz0, sz0)
  frame:SetPoint(R.char.point or "TOPRIGHT", _G.UIParent, R.char.point or "TOPRIGHT", R.char.x or -24, R.char.y or -250)
  frame:SetFrameStrata("MEDIUM")
  frame:SetClampedToScreen(true)
  frame:EnableMouse(true)
  frame:EnableMouseWheel(true)
  -- verre sombre rond (masque circulaire si dispo, sinon texture ronde teintée)
  glass = frame:CreateTexture(nil, "BACKGROUND")
  glass:SetPoint("CENTER", frame, "CENTER", 0, 0)
  local masked = false
  if frame.CreateMaskTexture and glass.AddMaskTexture then
    local mask = frame:CreateMaskTexture()
    if mask and mask.SetTexture then
      mask:SetTexture(CIRCLE, "CLAMPTOBLACKADDITIVE", "CLAMPTOBLACKADDITIVE")
      mask:SetAllPoints(glass)
      glass:SetColorTexture(0.03, 0.045, 0.07, 1)
      glass:AddMaskTexture(mask)
      masked = true
    end
  end
  if not masked then
    glass:SetTexture(CIRCLE)
    glass:SetVertexColor(0.03, 0.045, 0.07, 1)
  end
  R.masked = masked
  sheen = frame:CreateTexture(nil, "BORDER")
  sheen:SetTexture(CIRCLE)
  sheen:SetVertexColor(0.55, 0.7, 1, 0.06)
  sheen:SetPoint("CENTER", frame, "CENTER", 0, 10)
  ringSegs.halo = makeRing(64, { 0, 0, 0 }, 0.25, 2.5)
  ringSegs.rim = makeRing(72, GOLD, 0.95, 1.4)
  ringSegs.mid = makeRing(48, GOLD, 0.22, 1)
  ringSegs.inner = makeRing(32, GOLD, 0.12, 1)
  arrow = frame:CreateTexture(nil, "OVERLAY")
  arrow:SetTexture(ARROW)
  arrow:SetSize(20, 20)
  arrow:SetPoint("CENTER", frame, "CENTER", 0, 0)
  north = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  north:SetText("N")
  north:SetTextColor(GOLD[1], GOLD[2], GOLD[3])
  zoomText = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  zoomText:SetPoint("BOTTOM", frame, "BOTTOM", 0, 12)
  zoomText:SetTextColor(0.85, 0.85, 0.85)
  zoomText:Hide()
  ringLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ringLabel:SetTextColor(GOLD[1], GOLD[2], GOLD[3])
  ringLabel:SetAlpha(0.75)
  headTick = frame:CreateTexture(nil, "OVERLAY")
  headTick:SetTexture("Interface\\Buttons\\WHITE8X8")
  headTick:SetSize(3, 9)
  headTick:SetVertexColor(1, 0.95, 0.6, 0.95)
  R.headTick, R.ringLabel = headTick, ringLabel
  statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  statusText:SetPoint("CENTER", frame, "CENTER", 0, -24)
  statusText:SetText("")
  frame:SetScript("OnMouseWheel", function(_, delta)
    local z = R.zoomGoal or tonumber(opt("zoom")) or 150
    R.zoomGoal = R.ClampZoom(delta > 0 and z * 0.8 or z * 1.25)
    if zoomText then zoomText:SetText(R.Dist(R.zoomGoal)) zoomText:Show() R.zoomShownAt = now() end
    R.lastSig = nil
  end)
  frame:SetScript("OnEnter", function(self)
    if zoomText then zoomText:Show() R.zoomShownAt = now() end
    R.ShowLegend(self)
  end)
  frame:SetScript("OnLeave", function() hideTip() end)
  frame:SetScript("OnUpdate", function(_, elapsed)
    R.acc = (R.acc or 0) + (elapsed or 0)
    if R.acc < FPS then return end
    R.acc = 0
    R.StepZoom()
    R.Update()
  end)
  -- déplaçable / redimensionnable via le mode édition du cœur (agent POLISH), sinon glisser simple
  local moved = false
  if type(T.RegisterMovable) == "function" then
    moved = pcall(T.RegisterMovable, T, frame, "radar", "Radar", {
      resizable = true, square = true, minSize = 110, maxSize = 300,
      onResize = function(w) if R.db and tonumber(w) then R.db.size = floor(w + 0.5) R.Layout() end end,
      onMove = savePos,
    })
  end
  if not moved then
    frame:SetMovable(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", function(f) if not opt("locked") then f:StartMoving() end end)
    frame:SetScript("OnDragStop", function(f) f:StopMovingOrSizing() savePos() end)
  end
  R.movable = moved
  R.MakeControls()
  R.SetZoom(opt("zoom"), true)
  R.Layout()
  return frame
end

-- ------------------------------------------------------------------ boutons + menu (tout à la souris)
-- 4 petits boutons sur l'anneau : − / + (zoom), cadenas, menu (couches & réglages). Clic droit sur le radar
-- = même menu. Les commandes /tai radar restent en secours.
local LAYER_FR = { quests = "Objectifs de quête", turnins = "PNJ de remise", target = "Cible boussole / plan",
  path = "Chemin sûr", squad = "Amis du groupe", rares = "Rares vus", death = "Mon corps", taxi = "Maîtres de vol",
  danger = "Élites (danger)" }
R.LAYER_FR = LAYER_FR

local function ctlButton(label, angle, tip, fn)
  local b = _G.CreateFrame("Button", nil, frame)
  b:SetSize(16, 16)
  b:SetFrameLevel((frame:GetFrameLevel() or 1) + 6)
  b:RegisterForClicks("LeftButtonUp")
  local ring = b:CreateTexture(nil, "BACKGROUND")
  ring:SetTexture(CIRCLE)
  ring:SetVertexColor(GOLD[1], GOLD[2], GOLD[3], 0.7)
  ring:SetPoint("CENTER", b, "CENTER", 0, 0)
  ring:SetSize(18, 18)
  local bg = b:CreateTexture(nil, "BORDER")
  bg:SetTexture(CIRCLE)
  bg:SetVertexColor(0.05, 0.06, 0.09, 1)
  bg:SetAllPoints(b)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  b.text:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.text:SetText(label)
  b.angle = angle
  b:SetAlpha(0.7)
  b:SetScript("OnEnter", function(self)
    self:SetAlpha(1)
    local tt = _G.GameTooltip
    if tt then
      try(tt.SetOwner, tt, self, "ANCHOR_LEFT")
      try(tt.AddLine, tt, type(tip) == "function" and tip() or tip, 1, 1, 1)
      try(tt.Show, tt)
    end
  end)
  b:SetScript("OnLeave", function(self) self:SetAlpha(0.7) hideTip() end)
  b:SetScript("OnClick", function(self) fn(self) end)
  return b
end

function R.PlaceControls()
  if not frame then return end
  local r = radius() + 2
  for _, b in ipairs(R.controls or {}) do
    b:ClearAllPoints()
    b:SetPoint("CENTER", frame, "CENTER", r * cos(b.angle), r * sin(b.angle))
  end
end

function R.ToggleLock()
  R.db.locked = not R.db.locked
  if R.lockBtn then R.lockBtn.text:SetText(R.db.locked and "L" or "D") end
  return R.db.locked
end

function R.Zoom(step)
  R.SetZoom((tonumber(opt("zoom")) or 150) * (step > 0 and 0.8 or 1.25))
  R.lastSig = nil
  return R.db.zoom
end

function R.MakeControls()
  if R.controls or not frame then return end
  R.controls = {
    ctlButton("-", 1.25 * pi, "Dézoomer (ou molette)", function() R.Zoom(-1) end),
    ctlButton("+", 1.75 * pi, "Zoomer (ou molette)", function() R.Zoom(1) end),
    ctlButton("=", 0.75 * pi, "Menu : couches et réglages (ou clic droit)", function() R.ToggleMenu() end),
  }
  R.lockBtn = ctlButton(R.db.locked and "L" or "D", 0.25 * pi,
    function() return R.db.locked and "Verrouillé (clic : déverrouiller)" or "Déplaçable (clic : verrouiller)" end,
    function() R.ToggleLock() end)
  R.controls[#R.controls + 1] = R.lockBtn
  frame:SetScript("OnMouseUp", function(_, button) if button == "RightButton" then R.ToggleMenu() end end)
  R.PlaceControls()
end

-- menu : lignes cliquables { text, on = fn → bool (case à cocher), click = fn }
function R.MenuItems()
  local items = {}
  for _, l in ipairs(LAYERS) do
    items[#items + 1] = { text = LAYER_FR[l], layer = l, on = function() return opt(l) ~= false end,
      click = function() R.db[l] = not (opt(l) ~= false) R.slowAt, R.lastSig = 0, nil end }
  end
  items[#items + 1] = { text = "La carte tourne avec moi", on = function() return opt("rotate") and true or false end,
    click = function() R.db.rotate = not R.db.rotate R.lastSig = nil end }
  items[#items + 1] = { text = "Cercles de distance", on = function() return opt("rings") ~= false end,
    click = function() R.db.rings = not (opt("rings") ~= false) R.Layout() end }
  items[#items + 1] = { text = "Verrouiller", on = function() return opt("locked") and true or false end, click = R.ToggleLock }
  items[#items + 1] = { text = "Plus grand", click = function()
    R.db.size = math.min(300, (tonumber(opt("size")) or 170) + 20) R.Layout() end }
  items[#items + 1] = { text = "Plus petit", click = function()
    R.db.size = math.max(110, (tonumber(opt("size")) or 170) - 20) R.Layout() end }
  items[#items + 1] = { text = "Oublier rares / élites", click = function()
    R.char.rares, R.char.dangers = {}, {} R.slowAt = 0 end }
  items[#items + 1] = { text = "Cacher le radar (/tai radar pour le revoir)", close = true, click = function() R.Show(false) end }
  return items
end

function R.ToggleMenu(force)
  if not frame then return nil end
  local m = R.menu
  if not m then
    m = _G.CreateFrame("Frame", "TreeAIRadarMenu", _G.UIParent)
    if m.SetClampedToScreen then m:SetClampedToScreen(true) end
    m:SetFrameStrata("DIALOG")
    m:SetClampedToScreen(true)
    m:EnableMouse(true)
    local bg = m:CreateTexture(nil, "BACKGROUND")
    bg:SetAllPoints(m)
    bg:SetColorTexture(0.04, 0.05, 0.08, 0.94)
    local top = m:CreateTexture(nil, "BORDER")
    top:SetPoint("TOPLEFT", m, "TOPLEFT", 0, 0)
    top:SetPoint("TOPRIGHT", m, "TOPRIGHT", 0, 0)
    top:SetHeight(1)
    top:SetColorTexture(GOLD[1], GOLD[2], GOLD[3], 0.8)
    m.rows = {}
    R.menu = m
    m:Hide()
  end
  local want = force
  if want == nil then want = not m:IsShown() end
  if not want then m:Hide() return false end
  local items = R.MenuItems()
  for i, it in ipairs(items) do
    local row = m.rows[i]
    if not row then
      row = _G.CreateFrame("Button", nil, m)
      row:SetSize(230, 16)
      row:SetPoint("TOPLEFT", m, "TOPLEFT", 6, -4 - (i - 1) * 16)
      row:RegisterForClicks("LeftButtonUp")
      row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
      row.text:SetPoint("LEFT", row, "LEFT", 2, 0)
      row:SetScript("OnClick", function(self)
        local itm = self.item
        if itm and itm.click then itm.click() end
        if itm and itm.close then m:Hide() else R.ToggleMenu(true) end  -- redessine les coches
      end)
      m.rows[i] = row
    end
    row.item = it
    local mark = it.on and (it.on() and "|cff40ff40[x]|r " or "|cff808080[ ]|r ") or "      "
    row.text:SetText(mark .. it.text)
    row:Show()
  end
  for i = #items + 1, #m.rows do m.rows[i]:Hide() end
  m:SetSize(240, 8 + #items * 16)
  m:ClearAllPoints()
  m:SetPoint("TOPRIGHT", frame, "TOPLEFT", -4, 0)
  m:Show()
  return true
end

-- ------------------------------------------------------------------ boucle d'affichage (20 i/s, visible)
R.cost, R.costMax, R.updates = 0, 0, 0
function R.Update()
  if not frame then return end
  local t0 = try(_G.debugprofilestop)
  local me = R.Me()
  if not me then R.Visibility() return end
  local t = now()
  if not R.pins or t - (R.slowAt or 0) > 1 or (R.pinsMap and R.pinsMap ~= me.mapID) then
    R.pins = R.Collect(me)
    R.slowAt, R.pinsMap = t, me.mapID
    R.lastSig = nil
  end
  local facing = num(try(_G.GetPlayerFacing))
  local rotate = opt("rotate") and facing ~= nil
  local zoom = R.ClampZoom(opt("zoom"))
  local r = radius()
  -- rien n'a bougé ? on saute le placement (signature arrondie)
  local sig = floor(me.n * 2) .. ":" .. floor(me.w * 2) .. ":" .. floor((facing or 0) * 100) .. ":" .. zoom .. ":" .. tostring(R.pins)
  if sig ~= R.lastSig then
    R.lastSig = sig
    if arrow and arrow.SetRotation then arrow:SetRotation(rotate and 0 or (facing or 0)) end
    local nx, ny = R.Project(1, 0, facing, rotate, 1, r + 9)
    north:ClearAllPoints()
    north:SetPoint("CENTER", frame, "CENTER", nx, ny)
    if headTick then
      if opt("heading") ~= false and facing then
        local f = rotate and 0 or facing
        headTick:ClearAllPoints()
        headTick:SetPoint("CENTER", frame, "CENTER", -(r + 1) * sin(f), (r + 1) * cos(f))
        if headTick.SetRotation then pcall(headTick.SetRotation, headTick, f) end
        headTick:Show()
      else
        headTick:Hide()
      end
    end
    if ringLabel then
      if opt("labels") ~= false and opt("rings") ~= false then ringLabel:SetText(R.Dist(zoom / 2)) ringLabel:Show()
      else ringLabel:Hide() end
    end
    local used = 0
    for _, p in ipairs(R.pins) do
      if p.c == me.c then
        local px, py, dist, edge, ang = R.Project(p.n - me.n, p.w - me.w, facing, rotate, zoom, r)
        p.dist, p.px, p.py, p.edge = dist, px, py, edge
        if not (edge and p.far == false) then
          used = used + 1
          local b = pinPool[used] or newPin()
          if b.pin ~= p or b.kind ~= p.kind then stylePin(b, p) b.kind = p.kind end
          b.pin = p
          local ex, ey = px, py
          if edge then
            local inset = (b.size or 10) / 2 - 1
            local k = (r - inset) / r
            ex, ey = px * k, py * k
            b:SetAlpha((p.kind == "squad" or p.kind == "target") and 0.95 or 0.6)
            if p.kind == "squad" or p.kind == "target" then
              b.edgeArrow:ClearAllPoints()
              b.edgeArrow:SetPoint("CENTER", frame, "CENTER", px * (r + 7) / r, py * (r + 7) / r)
              if b.edgeArrow.SetRotation then b.edgeArrow:SetRotation(ang - pi / 2) end
              b.edgeArrow:Show()
            else
              b.edgeArrow:Hide()
            end
          else
            b:SetAlpha(1)
            b.edgeArrow:Hide()
          end
          b:ClearAllPoints()
          b:SetPoint("CENTER", frame, "CENTER", ex, ey)
          if opt("labels") ~= false and (p.kind == "target" or p.tracked) and dist and dist > 20 then
            b.dist:SetText(R.Dist(dist)) b.dist:Show()
          else
            b.dist:Hide()
          end
          b:Show()
        end
      end
    end
    for i = used + 1, #pinPool do pinPool[i]:Hide() pinPool[i].pin = nil end
    R.shownPins = used
  end
  if zoomText and zoomText:IsShown() and t - (R.zoomShownAt or 0) > 2.5 then zoomText:Hide() end
  if t0 then
    local dt = (try(_G.debugprofilestop) or t0) - t0
    R.cost = R.cost * 0.9 + dt * 0.1
    if dt > R.costMax then R.costMax = dt end
  end
  R.updates = R.updates + 1
end

-- visible = activé + option + hors instance + position connue (+ pas en combat si demandé)
function R.Visibility()
  local want = R.IsEnabled and R:IsEnabled() and opt("shown") ~= false
  local me, why = R.Me()
  if want and not me then want = false end
  if want and opt("hideCombat") and inCombat() then want = false end
  if want then
    createFrame()
    if frame and not frame:IsShown() then frame:Show() R.lastSig = nil end
  elseif frame and frame:IsShown() then
    frame:Hide()
    hideTip()
    if R.menu then R.menu:Hide() end
  end
  R.hiddenWhy = (not me) and why or nil
  return want and true or false
end

function R.Show(on)
  if R.db then R.db.shown = on and true or false end
  return R.Visibility()
end

-- ------------------------------------------------------------------ évènements
function R:OnEnable()
  -- 3.9.2 : « pas de minimap à part » → radar CACHÉ par défaut, une seule fois pour tout le monde (les points
  -- utiles vont sur la vraie minimap : module MinimapPins). Réactivable : Apparence → Radar.
  if self.db and not self.db.offByDefault then self.db.shown, self.db.offByDefault = false, true end
  self.char.rares = self.char.rares or {}
  self.char.dangers = self.char.dangers or {}
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA", "ZONE_CHANGED", "ZONE_CHANGED_INDOORS" }) do
    self:RegisterEvent(ev, function() taxiCache = {} R.questDirty = true R.slowAt = 0 R.Visibility() end)
  end
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_TURNED_IN", "QUEST_REMOVED" }) do
    self:RegisterEvent(ev, function() R.questDirty = true end)
  end
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if opt("hideCombat") then R.Visibility() end end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() R.Visibility() R.slowAt = 0 end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() R:ScanUnit("target") end)
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", function() R:ScanUnit("mouseover") end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, unit) R:ScanUnit(unit) end)
  self:RegisterEvent("PLAYER_DEAD", function()
    local me = R.Me()
    if me then R.char.death = { c = me.c, n = me.n, w = me.w, t = epoch() } R.slowAt = 0 end
  end)
  local function alive()
    if not try(_G.UnitIsDeadOrGhost, "player") then R.char.death = nil R.slowAt = 0 R:Clear("corpse") end
  end
  self:RegisterEvent("PLAYER_ALIVE", alive)
  self:RegisterEvent("PLAYER_UNGHOST", alive)
  R.Visibility()
  if not R.ticker and T.NewTicker then
    R.ticker = T:NewTicker(1, function() if R:IsEnabled() then R.Visibility() R.CorpseLine() end end)
  end
end

-- fantôme : UNE ligne « course au corps » (seul conseil permis quand on est mort, avec le choix du
-- gardien des esprits quand le corps est loin). Clic : boussole vers le corps.
R.CORPSE_FAR = 900   -- m
function R.CorpseText(meters, level)
  if not meters then return nil end
  local m = floor(meters / 10 + 0.5) * 10
  local dist = m >= 1000 and (("%.1f km"):format(m / 1000):gsub("%.", ",")) or (m .. " m")
  if m >= R.CORPSE_FAR and (tonumber(level) or 0) >= 10 then
    return "Cours à ton corps (" .. dist .. ") ou relève-toi au gardien des esprits (10 min de mal de résurrection)"
  end
  return "Cours à ton corps : " .. dist .. " — clique pour la flèche"
end
function R.CorpseLine()
  if not (R.char and try(_G.UnitIsGhost, "player")) then return end
  -- un seul propriétaire de la course au corps : Speed (ligne + boussole) ; Radar garde son point « Ton corps »
  local Sp = T.Speed
  if type(Sp) == "table" and Sp.IsEnabled and Sp:IsEnabled() then R:Clear("corpse") return end
  local d, me = R.char.death, R.Me()
  if type(d) ~= "table" or not me or me.c ~= d.c or not (d.n and me.n) then R:Clear("corpse") return end
  local yd = sqrt((d.n - me.n) ^ 2 + (d.w - me.w) ^ 2)
  local txt = R.CorpseText(yd * 0.9144, num(try(_G.UnitLevel, "player")))
  R:Suggest("corpse", txt, 2, { ttl = 5, cooldown = 600, sound = false, death = true, tooltip = "Clic : boussole vers ton corps",
    onClick = function() R.SetTarget({ c = d.c, n = d.n, w = d.w, label = "Ton corps" }) end })
end

function R:OnDisable()
  if R.ticker and R.ticker.Cancel then R.ticker:Cancel() end
  R.ticker = nil
  if frame then frame:Hide() end
  hideTip()
end

function R:OnOptionChanged(key, value)
  if key == "zoom" then R.SetZoom(value, true) end
  if key == "size" or key == "opacity" or key == "rings" or key == "compact" then R.Layout() end
  if key == "shown" or key == "hideCombat" then R.Visibility() end
  R.slowAt, R.lastSig = 0, nil
end

function R:AutoRefresh()
  R.Visibility()
end

-- résumé lisible (statut)
function R.Status()
  local me, why = R.Me()
  local counts = {}
  for _, p in ipairs(R.pins or {}) do counts[p.layer or p.kind] = (counts[p.layer or p.kind] or 0) + 1 end
  local parts = {}
  for _, l in ipairs(LAYERS) do if counts[l] then parts[#parts + 1] = l .. " " .. counts[l] end end
  return ("Radar : %s, portée %s, %d points (%s), %.2f ms/maj (max %.2f)"):format(
    me and "actif" or ("caché (" .. tostring(why) .. ")"), R.Dist(R.ClampZoom(opt("zoom"))),
    #(R.pins or {}), #parts > 0 and table.concat(parts, ", ") or "aucun", R.cost or 0, R.costMax or 0)
end

T:RegisterSlash("radar", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local cmd, arg = rest:match("^(%S+)%s*(.*)$")
  if cmd == "zoom" or cmd == "portee" or cmd == "portée" then
    R.SetZoom(tonumber(arg) or 150)
    R:Print("Portée du radar : " .. R.Dist(R.ClampZoom(opt("zoom"))))
  elseif cmd == "tourne" or cmd == "rotate" then
    R.db.rotate = not R.db.rotate
    R.lastSig = nil
    R:Print(R.db.rotate and "Radar : la carte tourne avec toi." or "Radar : nord en haut.")
  elseif cmd == "statut" or cmd == "status" then
    R:Print(R.Status())
  elseif cmd == "reset" then
    R.char.point, R.char.x, R.char.y = "TOPRIGHT", -210, -24
    if frame then frame:ClearAllPoints() frame:SetPoint("TOPRIGHT", _G.UIParent, "TOPRIGHT", -210, -24) end
    if T.ResetMovable then T:ResetMovable("radar") end   -- place automatique (à côté de la minimap)
    R:Print("Radar replacé.")
  elseif cmd == "oublier" then
    R.char.rares, R.char.dangers, R.char.death = {}, {}, nil
    R.slowAt = 0
    R:Print("Radar : rares, élites et corps oubliés.")
  else
    local on = R.Show(not (R.db and R.db.shown ~= false))
    R:Print((R.db.shown and "Radar affiché" or "Radar caché") .. (R.db.shown and not on and (" (" .. tostring(R.hiddenWhy or "position inconnue") .. ")") or "") .. ".")
  end
end, "radar : afficher/cacher · zoom <50-400> · tourne · statut · reset · oublier")
