-- TreeAI — module Boussole (agent QUEST-PRO). Une flèche à la TomTom vers la cible courante :
-- prochain objectif / PNJ de remise / point perso (/tai way x y). Distance (m ou yd), temps estimé selon
-- ta vitesse, passage automatique à la cible suivante quand un objectif se termine.
-- Sources des cibles, dans l'ordre : point perso → API de quêtes du jeu (C_QuestLog.GetNextWaypoint,
-- C_SuperTrack : LECTURE seule) → base Questie (lecture seule de QuestieLoader._modules, apparitions des
-- PNJ / objets, la plus proche) → zone seulement (pas de flèche, juste le nom).
-- Position : C_Map.GetBestMapForUnit + GetPlayerMapPosition + C_Map.GetWorldPosFromMapPos (si présent),
-- cap : GetPlayerFacing. Cachée en instance (positions indisponibles).
-- Règles 3.8.2 : aucun appel aux cadres / fonctions de Blizzard (pas de SetSuperTrackedQuestID, pas de
-- sélection du journal, pas d'ouverture de panneaux), cadre 100 % à nous, aucune automatisation.
-- Résumé IA : TreeAIDB.chars[perso].summary.compass. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Compass", {
  title = "Boussole",
  defaults = { enabled = true, shown = true, units = "m", autoNext = true, announce = true, scale = 1, locked = false,
               hideCombat = true, hideDup = true },
  charDefaults = { point = "TOP", x = 0, y = -120, custom = nil },
  options = {
    { key = "shown", type = "toggle", label = "Afficher la boussole (flèche vers la cible)" },
    { key = "autoNext", type = "toggle", label = "Passer à la cible suivante quand un objectif est fini" },
    { key = "announce", type = "toggle", label = "Annoncer la nouvelle cible dans « Maintenant »" },
    { key = "locked", type = "toggle", label = "Verrouiller la boussole" },
    { key = "hideCombat", type = "toggle", label = "Cacher la boussole en combat" },
    { key = "hideDup", type = "toggle",
      label = "Une seule flèche : cacher la boussole si Blizzard / Shortest Path / TomTom guide déjà vers la même cible" },
    { key = "scale", type = "range", label = "Taille de la boussole", min = 0.5, max = 2, step = 0.1 },
  },
})
ns.Compass = M
T.Compass = M

local floor, sqrt, atan2, pi = math.floor, math.sqrt, math.atan2, math.pi
local YD2M = 0.9144
local RUN = 7  -- vitesse de course (yd/s) si l'API ne donne rien

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function now() return try(_G.GetTime) or 0 end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function low(s) return tostring(s or ""):lower() end
local function quests() return T.GetModule and T:GetModule("Quests") or nil end

-- ------------------------------------------------------------------ géométrie (pure, testable)
local G = {}
M.Geo = G

local function vec(x, y)
  local mk = _G.CreateVector2D
  if type(mk) == "function" then
    local v = try(mk, x, y)
    if v then return v end
  end
  return { x = x, y = y, GetXY = function(p) return p.x, p.y end }
end
local function xy(p)
  if type(p) ~= "table" then return nil end
  if type(p.GetXY) == "function" then
    local x, y = try(p.GetXY, p)
    if x then return x, y end
  end
  return p.x, p.y
end

-- coordonnées monde (yards) d'un point de carte (x, y en 0..1) → continent, wx (nord), wy (ouest)
function G.World(mapID, x, y)
  local CM = _G.C_Map
  if not (CM and CM.GetWorldPosFromMapPos and mapID and x and y) then return nil end
  local cont, w = try(CM.GetWorldPosFromMapPos, mapID, vec(x, y))
  local wx, wy = xy(w)
  if not (cont and wx and wy) then return nil end
  return cont, wx, wy
end

-- taille de la carte en yards (GetMapWorldSize sinon deux conversions)
local sizeCache = {}
function G.MapSize(mapID)
  if not mapID then return nil end
  if sizeCache[mapID] then return sizeCache[mapID][1], sizeCache[mapID][2] end
  local w, h
  local CM = _G.C_Map
  if CM and CM.GetMapWorldSize then
    w, h = try(CM.GetMapWorldSize, mapID)
    if not (tonumber(w) and w > 0) then w, h = nil, nil end
  end
  if not w then
    local c1, ax, ay = G.World(mapID, 0, 0)
    local c2, bx, by = G.World(mapID, 1, 1)
    if c1 and c2 and c1 == c2 then w, h = math.abs(ay - by), math.abs(ax - bx) end
  end
  if w and w > 0 and h and h > 0 then sizeCache[mapID] = { w, h } return w, h end
  return nil
end

-- distance (yd) + cap absolu (radians, 0 = nord, sens anti-horaire comme GetPlayerFacing) de a vers b.
-- a, b = { mapID, x, y }. Retourne dist, bearing, approx (true si estimation sans conversion monde).
function G.Vector(a, b)
  if not (a and b and a.mapID and b.mapID and a.x and b.x) then return nil end
  local ca, ax, ay = G.World(a.mapID, a.x, a.y)
  local cb, bx, by = G.World(b.mapID, b.x, b.y)
  if ca and cb then
    if ca ~= cb then return nil end
    local north, west = bx - ax, by - ay
    return sqrt(north * north + west * west), atan2(west, north), false
  end
  if a.mapID ~= b.mapID then return nil end
  local w, h = G.MapSize(a.mapID)
  local approx = not w
  w, h = w or 3000, h or 2000  -- taille typique d'une zone : direction juste, distance approximative
  local north, west = -(b.y - a.y) * h, -(b.x - a.x) * w
  return sqrt(north * north + west * west), atan2(west, north), approx
end

-- angle relatif pour la flèche (rotation anti-horaire d'une texture pointant vers le haut)
function G.Relative(bearing, facing)
  if not (bearing and facing) then return nil end
  local r = (bearing - facing) % (2 * pi)
  return r
end

function G.Dist(yd, units)
  if not yd then return "?" end
  if units == "yd" then
    if yd >= 1760 then return ("%.1f mi"):format(yd / 1760) end
    return floor(yd + 0.5) .. " yd"
  end
  local m = yd * YD2M
  if m >= 1000 then return (("%.1f km"):format(m / 1000):gsub("%.", ",")) end
  return floor(m + 0.5) .. " m"
end

function G.ETA(yd, speed)
  if not yd then return nil end
  speed = (speed and speed > 0.5) and speed or RUN
  local s = yd / speed
  if s < 60 then return floor(s + 0.5) .. " s" end
  if s < 3600 then
    local mn, sec = floor(s / 60), floor(s % 60)
    return sec >= 10 and (mn .. " min " .. sec) or (mn .. " min")
  end
  return ("%d h %02d"):format(floor(s / 3600), floor((s % 3600) / 60))
end

-- 8 directions (texte, quand pas de cap)
local DIRS = { "N", "NO", "O", "SO", "S", "SE", "E", "NE" }
function G.Cardinal(bearing)
  if not bearing then return "" end
  local i = floor(((bearing % (2 * pi)) + pi / 8) / (pi / 4)) % 8
  return DIRS[i + 1]
end

-- ------------------------------------------------------------------ position du joueur
function M.Player()
  local inInst = try(_G.IsInInstance)
  if inInst then return nil, "instance" end
  local CM = _G.C_Map
  if not (CM and CM.GetBestMapForUnit and CM.GetPlayerMapPosition) then return nil, "api" end
  local mapID = try(CM.GetBestMapForUnit, "player")
  if not mapID then return nil, "map" end
  local x, y = xy(try(CM.GetPlayerMapPosition, mapID, "player"))
  if not (x and y) or (x == 0 and y == 0) then return nil, "pos" end
  return { mapID = mapID, x = x, y = y }
end

function M.MapName(mapID)
  local CM = _G.C_Map
  local info = mapID and CM and CM.GetMapInfo and try(CM.GetMapInfo, mapID)
  return type(info) == "table" and info.name or nil
end

local speedAvg
function M.Speed()
  local s = try(_G.GetUnitSpeed, "player")
  s = tonumber(s) or 0
  if s > 0.5 then speedAvg = speedAvg and (speedAvg * 0.8 + s * 0.2) or s end
  return s > 0.5 and s or (speedAvg or RUN)
end

-- ------------------------------------------------------------------ Shortest Path Forever / TomTom
-- SPF (si installé) : ShortestPathForever.API.Estimate(fromMap,x,y,toMap,x,y) (coordonnées 0..1) → secondes
-- réelles (navmesh, vols, bateaux, foyer) ou nil + raison ; Navigate / NavigateRoute pour guider.
-- Estimate hors combat seulement ; guidage UNIQUEMENT sur clic du joueur. Forme d'appel (. ou :) détectée.
local function inCombat() return T.InCombat and T:InCombat() or false end
M.InCombat = inCombat
function M.SPF()
  local S = rawget(_G, "ShortestPathForever")
  local A = type(S) == "table" and S.API or nil
  if type(A) ~= "table" or type(A.Estimate) ~= "function" then return nil end
  return A
end
local spfColon
local function good(ok, a, b) return ok and (a ~= nil or b == "combat" or b == "unreachable") end
local function spfCall(A, fn, ...)
  if type(A[fn]) ~= "function" then return nil end
  if spfColon ~= true then
    local ok, a, b = pcall(A[fn], ...)
    if good(ok, a, b) then spfColon = false return a, b end
    if spfColon == false then return nil, ok and b or "error" end
  end
  local ok, a, b = pcall(A[fn], A, ...)
  if good(ok, a, b) then spfColon = true return a, b end
  return nil, ok and b or "error"
end
M.SPFCall = spfCall
function M.ResetSPF() spfColon = nil end
-- temps de trajet (s) a → b ; nil si pas de SPF / en combat / inaccessible
function M.Travel(a, b)
  local A = M.SPF()
  if not (A and a and b and a.mapID and b.mapID) or inCombat() then return nil end
  local sec = tonumber((spfCall(A, "Estimate", a.mapID, a.x, a.y, b.mapID, b.x, b.y)))
  if sec and sec >= 0 then return sec end
  return nil
end

-- « Aller là » (clic du joueur seulement) : SPF → TomTom → repère utilisateur de Blizzard (non protégé)
function M.Pin(t)
  if not (t and t.mapID and t.x) then return nil end
  local title = cut(t.label or t.title or "TreeAI", 60)
  local A = M.SPF()
  if A and not inCombat() and spfCall(A, "Navigate", "TreeAI", t.mapID, t.x, t.y, title) then return "spf" end
  local TT = rawget(_G, "TomTom")
  if type(TT) == "table" and type(TT.AddWaypoint) == "function" then
    if M.tomtomUID and type(TT.RemoveWaypoint) == "function" then pcall(TT.RemoveWaypoint, TT, M.tomtomUID) end
    local ok, uid = pcall(TT.AddWaypoint, TT, t.mapID, t.x, t.y, { title = title, persistent = false, from = "TreeAI" })
    if ok then M.tomtomUID = uid return "tomtom" end
  end
  local CM, ST, UMP = _G.C_Map, _G.C_SuperTrack, rawget(_G, "UiMapPoint")
  if CM and CM.SetUserWaypoint and type(UMP) == "table" and type(UMP.CreateFromCoordinates) == "function" then
    if CM.CanSetUserWaypointOnMap and not try(CM.CanSetUserWaypointOnMap, t.mapID) then return nil end
    local pt = try(UMP.CreateFromCoordinates, t.mapID, t.x, t.y)
    if pt and pcall(CM.SetUserWaypoint, pt) then
      if ST and ST.SetSuperTrackedUserWaypoint then pcall(ST.SetSuperTrackedUserWaypoint, true) end
      return "blizzard"
    end
  end
  return nil
end
-- Une seule flèche à l'écran : un autre guide (repère / quête suivie de Blizzard, Shortest Path, TomTom)
-- vise-t-il déjà la même cible ? Lecture seule. Renvoie "blizzard" / "spf" / "tomtom" ou nil.
local function near(a, b) return a and b and math.abs(a - b) < 0.012 end
function M.OtherGuide(t)
  if not t then return nil end
  local ST, CM = _G.C_SuperTrack, _G.C_Map
  if t.questID and ST and ST.GetSuperTrackedQuestID then
    local q = try(ST.GetSuperTrackedQuestID)
    if q and q ~= 0 and q == t.questID then return "blizzard" end
  end
  if t.mapID and t.x and CM and CM.HasUserWaypoint and try(CM.HasUserWaypoint) and CM.GetUserWaypoint then
    local wp = try(CM.GetUserWaypoint)
    local pos = type(wp) == "table" and wp.position
    local px, py
    if type(pos) == "table" then
      if type(pos.GetXY) == "function" then local ok, a, b = pcall(pos.GetXY, pos) if ok then px, py = a, b end end
      px, py = px or pos.x, py or pos.y
    end
    local onTarget = wp and wp.uiMapID == t.mapID and near(px, t.x) and near(py, t.y)
    local superUser = not (ST and ST.IsSuperTrackingUserWaypoint) or try(ST.IsSuperTrackingUserWaypoint)
    if onTarget and superUser then return "blizzard" end
  end
  -- nos propres « Aller là » : le guide posé par un clic sur CETTE cible
  if M.guide and M.guide.key == t.key then
    if M.guide.how == "tomtom" then
      local arrow = rawget(_G, "TomTomCrazyArrow")
      if not (type(arrow) == "table" and arrow.IsVisible) or try(arrow.IsVisible, arrow) then return "tomtom" end
    elseif M.guide.how == "spf" then
      local A = M.SPF()
      local active = A and (type(A.IsNavigating) ~= "function" or spfCall(A, "IsNavigating"))
      if active then return "spf" end
    elseif M.guide.how == "blizzard" then
      if not (CM and CM.HasUserWaypoint) or try(CM.HasUserWaypoint) then return "blizzard" end
    end
  end
  return nil
end

local PIN_TXT = { spf = "Shortest Path te guide", tomtom = "point TomTom posé", blizzard = "repère posé sur la carte" }
function M.GoTo(t)
  t = t or M.Current()
  local how = M.Pin(t)
  M.guide = (how and t) and { key = t.key, how = how } or nil
  if how then M:Print(("Aller là : %s (%s)."):format(cut(t.label or t.title, 40), PIN_TXT[how]))
  elseif t then M:Print("Aller là : suis la flèche de la boussole" .. (inCombat() and " (guidage après le combat)." or "."))
  else M:Print("Aucune cible (/tai cible, /tai way x y).") end
  return how
end

-- ------------------------------------------------------------------ Questie (lecture seule)
-- 1) addon QuestieDB (API publique, contrat 4 : LibQuestieDB.Quest/Npc/Object/Item.Get(id, champ), appel
--    « . ») ; 2) repli : QuestieLoader._modules.QuestieDB (anciennes versions). Jamais ImportModule, jamais
--    d'écriture (les lectures renvoient des copies).
local libAdapter
local function libQuestieDB(mods)
  local L = rawget(_G, "LibQuestieDB")
  if type(L) ~= "table" then return nil end
  if libAdapter and libAdapter.lib == L then return libAdapter end
  if type(L.RequireContract) == "function" then
    local ok, res = pcall(L.RequireContract, 4)
    if not ok or res == false then return nil end
  end
  local function getter(field, short)
    local t = type(L[field]) == "table" and L[field] or rawget(_G, short)
    if type(t) == "table" and type(t.Get) == "function" then
      local g = t.Get
      return function(id, key) local ok, v = pcall(g, id, key) if ok then return v end end
    end
  end
  local qd = getter("Quest", "QuestDB")
  if not qd then return nil end
  local old = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  libAdapter = { lib = L, QueryQuestSingle = qd, QueryNPCSingle = getter("Npc", "NpcDB"),
    QueryObjectSingle = getter("Object", "ObjectDB"), QueryItemSingle = getter("Item", "ItemDB"),
    IsDoable = type(old) == "table" and old.IsDoable or nil }
  return libAdapter
end
local function questie()
  local Q = quests()
  if Q and Q.db and Q.db.questieHints == false then return nil end
  local QL = rawget(_G, "QuestieLoader")
  local mods = type(QL) == "table" and rawget(QL, "_modules") or nil
  local zdb = type(mods) == "table" and rawget(mods, "ZoneDB") or nil
  local lib = libQuestieDB(mods)
  if lib then return lib, zdb end
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) ~= "table" or type(db.QueryQuestSingle) ~= "function" then return nil end
  return db, zdb
end
M.Questie = questie

-- zone Questie (AreaTable) → uiMapID : ZoneDB si dispo, sinon par le nom de la carte du joueur
local areaCache = {}
local function areaToMap(areaID, zdb, playerMap)
  if areaCache[areaID] ~= nil then return areaCache[areaID] or nil end
  local id
  if type(zdb) == "table" and type(zdb.GetUiMapIdByAreaId) == "function" then
    id = tonumber((try(zdb.GetUiMapIdByAreaId, zdb, areaID)))
  end
  if not id and playerMap and _G.C_Map and _G.C_Map.GetAreaInfo then
    local an = try(_G.C_Map.GetAreaInfo, areaID)
    if an and low(an) == low(M.MapName(playerMap)) then id = playerMap end
  end
  areaCache[areaID] = id or false
  return id
end
M.AreaToMap = areaToMap

-- points d'apparition { {mapID, x, y}, ... } (coordonnées Questie 0..100 → 0..1), 40 max
local function spawnsOf(kind, id, db, zdb, playerMap, out, label)
  local fn = kind == "npc" and db.QueryNPCSingle or db.QueryObjectSingle
  if type(fn) ~= "function" then return end
  local sp = try(fn, id, "spawns")
  if type(sp) ~= "table" then return end
  local name = label or try(fn, id, "name")
  for area, pts in pairs(sp) do
    local mapID = type(area) == "number" and areaToMap(area, zdb, playerMap)
    if mapID and type(pts) == "table" then
      for _, p in ipairs(pts) do
        if type(p) == "table" and tonumber(p[1]) and tonumber(p[2]) and p[1] >= 0 then
          out[#out + 1] = { mapID = mapID, x = p[1] / 100, y = p[2] / 100, name = name }
          if #out >= 40 then return end
        end
      end
    end
  end
end
M.SpawnsOf = spawnsOf

-- points des objectifs Questie d'une quête ; objNames = noms (minuscules) des objectifs NON finis
-- (nil = tous). Retourne liste de points {mapID,x,y,name}.
function M.QuestieObjectivePoints(qid, objNames, playerMap)
  local db, zdb = questie()
  if not db then return nil end
  local objs = try(db.QueryQuestSingle, qid, "objectives")
  if type(objs) ~= "table" then return nil end
  local out = {}
  local function wanted(name)
    if not objNames then return true end
    local n = low(name)
    if n == "" then return true end
    for on in pairs(objNames) do
      if on == n or on:find(n, 1, true) or n:find(on, 1, true) then return true end
    end
    return false
  end
  -- [1] créatures, [2] objets du monde, [3] objets (butin), [5] crédit de mort
  for _, c in ipairs(type(objs[1]) == "table" and objs[1] or {}) do
    local id = type(c) == "table" and c[1] or c
    if type(id) == "number" then
      local nm = try(db.QueryNPCSingle, id, "name")
      if wanted(nm) then spawnsOf("npc", id, db, zdb, playerMap, out, nm) end
    end
  end
  for _, o in ipairs(type(objs[2]) == "table" and objs[2] or {}) do
    local id = type(o) == "table" and o[1] or o
    if type(id) == "number" then
      local nm = db.QueryObjectSingle and try(db.QueryObjectSingle, id, "name")
      if wanted(nm or (type(o) == "table" and o[2])) then spawnsOf("obj", id, db, zdb, playerMap, out, nm) end
    end
  end
  for _, it in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
    local itemID = type(it) == "table" and it[1] or it
    if type(itemID) == "number" and db.QueryItemSingle then
      local iname = try(db.QueryItemSingle, itemID, "name") or (T.API and T.API.ItemName and T.API.ItemName(itemID))
      if wanted(iname) then
        for _, npc in ipairs(try(db.QueryItemSingle, itemID, "npcDrops") or {}) do
          if #out >= 40 then break end
          spawnsOf("npc", npc, db, zdb, playerMap, out)
        end
        for _, ob in ipairs(try(db.QueryItemSingle, itemID, "objectDrops") or {}) do
          if #out >= 40 then break end
          spawnsOf("obj", ob, db, zdb, playerMap, out)
        end
      end
    end
  end
  for _, k in ipairs(type(objs[5]) == "table" and objs[5] or {}) do
    local ids = type(k) == "table" and k[1]
    if type(ids) == "table" and type(ids[1]) == "number" then
      local nm = try(db.QueryNPCSingle, ids[1], "name")
      if wanted(nm) then spawnsOf("npc", ids[1], db, zdb, playerMap, out, nm) end
    end
  end
  return out
end

-- PNJ (ou objet) de remise
function M.QuestieFinisherPoints(qid, playerMap)
  local db, zdb = questie()
  if not db then return nil end
  local fb = try(db.QueryQuestSingle, qid, "finishedBy")
  if type(fb) ~= "table" then return nil end
  local out = {}
  for _, npc in ipairs(type(fb[1]) == "table" and fb[1] or {}) do spawnsOf("npc", npc, db, zdb, playerMap, out) end
  for _, ob in ipairs(type(fb[2]) == "table" and fb[2] or {}) do spawnsOf("obj", ob, db, zdb, playerMap, out) end
  return out
end

-- le point le plus proche du joueur (même continent), ou le 1er de sa carte, ou le 1er tout court
function M.Nearest(points, me)
  if type(points) ~= "table" or #points == 0 then return nil end
  local best, bd
  for _, p in ipairs(points) do
    local d = me and G.Vector(me, p)
    if d and (not bd or d < bd) then best, bd = p, d end
  end
  if best then return best, bd end
  if me then for _, p in ipairs(points) do if p.mapID == me.mapID then return p end end end
  return points[1]
end

-- ------------------------------------------------------------------ cibles
-- cible = { key, kind = custom|objective|turnin|zone, questID, title, label, mapID, x, y, zone, src }
local function waypointAPI(qid)
  local QL = _G.C_QuestLog
  if not (QL and QL.GetNextWaypoint) then return nil end
  local mapID, x, y = try(QL.GetNextWaypoint, qid)
  if tonumber(mapID) and tonumber(x) and tonumber(y) and (x > 0 or y > 0) then
    return { mapID = mapID, x = x, y = y }
  end
  return nil
end

local function unfinishedNames(q)
  local t, any = {}, false
  for _, o in ipairs(q.obj or {}) do
    if not o.finished then
      local n = low(o.name):gsub("%s+tu[ée]+e?s?$", ""):gsub("%s+slain$", "")
      t[n] = true
      any = true
    end
  end
  return any and t or nil
end

-- toutes les cibles possibles d'une quête (points bruts, non triés) — utilisé aussi par le suivi
function M.QuestPoints(q, me)
  local pmap = me and me.mapID
  local pts, src
  if q.complete then
    local w = waypointAPI(q.id)
    if w then pts, src = { w }, "jeu" else
      pts = M.QuestieFinisherPoints(q.id, pmap)
      src = pts and #pts > 0 and "questie" or nil
    end
  else
    local w = waypointAPI(q.id)
    if w then pts, src = { w }, "jeu" else
      pts = M.QuestieObjectivePoints(q.id, unfinishedNames(q), pmap)
      if pts and #pts == 0 then pts = M.QuestieObjectivePoints(q.id, nil, pmap) end
      src = pts and #pts > 0 and "questie" or nil
    end
  end
  return pts or {}, src
end

local function objLabel(q)
  for _, o in ipairs(q.obj or {}) do
    if not o.finished then
      return (o.need or 1) > 1 and ("%s %d/%d"):format(o.name, o.have or 0, o.need) or o.name
    end
  end
  return q.title
end

-- Construit la liste triée des cibles. list = liste des quêtes (Quests.lastList) ; me = position.
function M.BuildTargets(list, me)
  local out = {}
  local c = M.char and M.char.custom
  if type(c) == "table" and c.mapID and c.x then
    out[#out + 1] = { key = "custom", kind = "custom", title = c.name or "Point perso", label = c.name or "Point perso",
      mapID = c.mapID, x = c.x, y = c.y, src = "perso" }
  end
  local st = _G.C_SuperTrack and _G.C_SuperTrack.GetSuperTrackedQuestID and try(_G.C_SuperTrack.GetSuperTrackedQuestID)
  for _, q in ipairs(list or {}) do
    if not q.failed and q.id then
      local pts, src = M.QuestPoints(q, me)
      local p, d = M.Nearest(pts, me)
      local t = { key = (q.complete and "turn:" or "obj:") .. q.id, kind = q.complete and "turnin" or "objective",
        questID = q.id, title = q.title, src = src, xp = q.xp, ready = q.complete,
        label = q.complete and ("Rendre « " .. cut(q.title, 30) .. " »") or objLabel(q),
        zone = q.complete and (q.turnZone or q.zone) or q.zone, super = (st and st == q.id) or nil }
      if p then
        t.mapID, t.x, t.y, t.dist, t.npc = p.mapID, p.x, p.y, d, p.name
        if q.complete and p.name then t.label = ("Rendre « %s » à %s"):format(cut(q.title, 26), p.name) end
      else
        t.kind = "zone"
      end
      out[#out + 1] = t
    end
  end
  table.sort(out, function(a, b)
    local ra = a.kind == "custom" and 0 or (a.super and 1) or (a.kind == "zone" and 3) or 2
    local rb = b.kind == "custom" and 0 or (b.super and 1) or (b.kind == "zone" and 3) or 2
    if ra ~= rb then return ra < rb end
    if (a.dist or 1e12) ~= (b.dist or 1e12) then return (a.dist or 1e12) < (b.dist or 1e12) end
    return tostring(a.key) < tostring(b.key)
  end)
  return out
end

M.targets, M.idx = {}, 1
function M.Current() return M.targets[M.idx] end

local function announce(t, why)
  if not t or not (t.label or t.title) or not (M.db and M.db.announce ~= false) then return end
  local where = t.dist and (" — " .. G.Dist(t.dist, M.db.units)) or (t.zone and (" — " .. t.zone) or "")
  M:Suggest("target", (why or "Boussole") .. " : " .. cut(t.label or t.title, 50) .. where, 1,
    { ttl = 20, cooldown = 5, sound = false })
end

-- Garde la cible choisie à la main (pinned) tant qu'elle existe ; sinon la plus proche. Si la cible
-- suivie disparaît (quête finie → remise) ou change d'objectif (objectif fini) : annonce la suivante.
function M.Rebuild(reason)
  -- planification hors combat (Forever : valeurs « secrètes » en combat) ; refaite à PLAYER_REGEN_ENABLED
  if inCombat() and reason ~= "force" then M.dirty = true return M.targets end
  M.dirty = nil
  local Q = quests()
  local me = M.Player()
  M.me = me
  local prev = M.Current()
  M.targets = M.BuildTargets(Q and Q.lastList or {}, me)
  M.idx = 1
  local auto = not (M.db and M.db.autoNext == false)
  if prev then
    local found
    for i, t in ipairs(M.targets) do if t.key == prev.key then found = i end end
    local cur = found and M.targets[found]
    local progressed = cur and prev.kind == "objective" and cur.label ~= prev.label
    if found and (M.pinned or not auto) and not progressed then
      M.idx = found
    elseif reason ~= "init" and (not found or progressed) then
      M.pinned = nil
      if auto then
        if progressed then M.idx = found end
        announce(M.targets[M.idx], prev.kind == "objective" and "Objectif fini → suivant" or "Cible suivante")
      elseif found then
        M.idx = found
      end
    end
  end
  M.WriteSummary()
  return M.targets
end

function M.Cycle(step)
  local n = #M.targets
  if n == 0 then M.Rebuild() n = #M.targets end
  if n == 0 then M:Print("Aucune cible : pas de quête suivie ni de point perso (/tai way x y).") return nil end
  M.idx = ((M.idx - 1 + (step or 1)) % n) + 1
  M.pinned = true
  local t = M.Current()
  announce(t, "Cible")
  M.Refresh()
  return t
end

function M.SetQuestTarget(qid)
  if #M.targets == 0 then M.Rebuild() end
  for i, t in ipairs(M.targets) do
    if t.questID == qid then
      M.idx, M.pinned = i, true
      announce(t, "Cible")
      M.Refresh()
      return t
    end
  end
  return nil
end

-- point perso : /tai way 45.2 60.1 [nom] (coordonnées de la carte où tu es)
function M.SetWaypoint(x, y, name, mapID)
  x, y = tonumber(x), tonumber(y)
  if not (x and y) then return nil end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  if x < 0 or x > 1 or y < 0 or y > 1 then return nil end
  local me = M.Player()
  mapID = mapID or (me and me.mapID)
  if not mapID then return nil end
  M.char.custom = { mapID = mapID, x = x, y = y, name = (name and name ~= "") and cut(name, 40) or nil }
  M.pinned = nil
  M.Rebuild()
  M.idx = 1
  announce(M.Current(), "Point perso")
  M.Refresh()
  return M.char.custom
end
--- Shared contract used by Planner / Missions / SafePath / Radar: `Compass:SetTarget{mapID, x, y, label, source,
--- questID}` (x, y in 0-1 or 0-100). Called with `:` (self = M) or `.` (first arg = the table). Returns the target.
function M.SetTarget(a, b)
  local t = (type(b) == "table" and b) or (type(a) == "table" and a ~= M and a) or nil
  if not t then return nil end
  if t.questID and not (t.x and t.y) then return M.SetQuestTarget(t.questID) end
  local r = M.SetWaypoint(t.x, t.y, t.label, t.mapID)
  if r then r.source, r.questID = t.source, t.questID end
  return r
end

--- Un clic « y aller » vers ce que voit TreeAI.Detect : kind = "rare" | "treasure" | genre de service
--- ("vendor", "repair", "flight", "innkeeper", "mailbox"…). Retourne la cible ou nil.
function M.TargetNearest(kind)
  local Dt = T.Detect
  if type(Dt) ~= "table" then return nil end
  local e
  if kind == "rare" or kind == "treasure" then
    e = type(Dt.Rares) == "function" and Dt.Rares(kind)[1]
  elseif type(Dt.Nearest) == "function" then
    e = Dt.Nearest(kind)
  end
  if type(e) == "table" and type(Dt.Target) == "function" then return Dt.Target(e) end
  return nil
end

function M.ClearWaypoint()
  if M.char then M.char.custom = nil end
  M.Rebuild()
  M.Refresh()
end

-- état calculé pour l'affichage (pur sauf lectures d'API)
function M.Compute()
  local t = M.Current()
  local me = M.Player()
  M.me = me
  if not t then return { none = true, hidden = me == nil } end
  local st = { target = t, me = me, hidden = me == nil }
  if me and t.mapID then
    local d, bearing, approx = G.Vector(me, t)
    st.dist, st.bearing, st.approx = d, bearing, approx
    local facing = try(_G.GetPlayerFacing)
    st.facing = facing
    st.rel = G.Relative(bearing, facing)
    local tnow = now()
    if t ~= M.travelT or tnow - (M.travelAt or 0) > 3 then
      M.travelT, M.travelAt, M.travelSec = t, tnow, M.Travel(me, t)
    end
    if M.travelSec then st.eta, st.spf = G.ETA(M.travelSec * RUN, RUN), true
    else st.eta = d and G.ETA(d, M.Speed()) end
    t.dist = d or t.dist
    if d and d < 12 and t.kind == "custom" then st.arrived = true end
  end
  return st
end

-- ------------------------------------------------------------------ cadre (le nôtre, jamais Blizzard)
local frame, arrow, label, info
local ARROW = "Interface\\Minimap\\MinimapArrow"
local function savePos()
  if not frame or not M.char then return end
  local p, _, _, x, y = frame:GetPoint(1)
  M.char.point, M.char.x, M.char.y = p or "TOP", x or 0, y or -120
end

local function createFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Button", "TreeAICompassFrame", _G.UIParent)
  frame:SetSize(150, 74)
  frame:SetPoint(M.char.point or "TOP", _G.UIParent, M.char.point or "TOP", M.char.x or 0, M.char.y or -120)
  frame:SetFrameStrata("MEDIUM")
  frame:SetClampedToScreen(true)
  frame:SetMovable(true)
  frame:EnableMouse(true)
  frame:RegisterForDrag("LeftButton")
  frame:RegisterForClicks("LeftButtonUp", "RightButtonUp", "MiddleButtonUp")
  frame:SetScript("OnDragStart", function(f) if not (M.db and M.db.locked) then f:StartMoving() end end)
  frame:SetScript("OnDragStop", function(f) f:StopMovingOrSizing() savePos() end)
  frame:SetScript("OnClick", function(_, button)
    if button == "MiddleButton" or (_G.IsShiftKeyDown and _G.IsShiftKeyDown()) then M.GoTo()  -- « Aller là »
    elseif button == "RightButton" then M.Cycle(-1) else M.Cycle(1) end
  end)
  if frame.EnableMouseWheel then
    frame:EnableMouseWheel(true)
    frame:SetScript("OnMouseWheel", function(_, delta)
      local s = math.max(0.5, math.min(2, (tonumber(M.db.scale) or 1) + (delta > 0 and 0.1 or -0.1)))
      M.db.scale = s
      frame:SetScale(s)
    end)
  end
  frame:SetScript("OnEnter", function(f)
    local tt = _G.GameTooltip
    if not tt then return end
    pcall(tt.SetOwner, tt, f, "ANCHOR_BOTTOM")
    pcall(tt.AddLine, tt, "Boussole TreeAI")
    local t = M.Current()
    if t then
      pcall(tt.AddLine, tt, t.title or t.label, 1, 1, 1)
      if t.npc then pcall(tt.AddLine, tt, t.npc, 0.8, 0.8, 0.8) end
      pcall(tt.AddLine, tt, "Source : " .. (t.src or "zone"), 0.6, 0.6, 0.6)
    end
    pcall(tt.AddLine, tt, "Clic : cible suivante · clic droit : précédente · Maj-clic : Aller là (Shortest Path / TomTom / carte) · molette : taille · glisser : déplacer", 0.6, 0.8, 1, true)
    if T.AutoRunHint then pcall(tt.AddLine, tt, T.AutoRunHint(), 0.55, 0.55, 0.55, true) end
    pcall(tt.Show, tt)
  end)
  frame:SetScript("OnLeave", function() if _G.GameTooltip then pcall(_G.GameTooltip.Hide, _G.GameTooltip) end end)
  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(frame)
  bg:SetColorTexture(0, 0, 0, 0.35)
  arrow = frame:CreateTexture(nil, "ARTWORK")
  arrow:SetSize(36, 36)
  arrow:SetPoint("TOP", frame, "TOP", 0, -2)
  arrow:SetTexture(ARROW)
  label = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  label:SetPoint("TOP", arrow, "BOTTOM", 0, -1)
  label:SetWidth(146)
  info = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  info:SetPoint("TOP", label, "BOTTOM", 0, -1)
  do  -- thème commun (visuel seulement)
    local Th = T and T.Theme
    if Th then Th:Skin(frame, "hud", { bg = bg }); Th:Font(label, "S", "gold"); Th:Font(info, "S") end
  end
  frame:SetScale(tonumber(M.db.scale) or 1)
  -- (LITE) la flèche tourne avec le joueur (10 Hz, seulement si l'orientation change) ; cible, distance et
  -- textes 1 fois par seconde (flèche : 10 Hz)
  local acc, full = 0, 0
  frame:SetScript("OnUpdate", function(_, dt)
    acc, full = acc + (dt or 0), full + (dt or 0)
    if acc < 0.1 then return end
    acc = 0
    if full >= 1 or not (M.state and M.state.bearing) then full = 0; M.Refresh(); return end
    M.Spin()
  end)
  return frame
end

local GREEN, GOLD = { 0.3, 1, 0.3 }, { 1, 0.85, 0.2 }
local lastRel, lastLabel, lastInfo
local function setArrow(rel)
  if rel == lastRel then return end
  lastRel = rel
  if arrow.SetRotation then pcall(arrow.SetRotation, arrow, rel) end
  local col = (rel < 0.35 or rel > 2 * pi - 0.35) and GREEN or GOLD
  arrow:SetVertexColor(col[1], col[2], col[3])
end
--- entre deux calculs complets : seulement l'orientation du joueur -> rotation de la flèche
function M.Spin()
  local st = M.state
  if not (frame and arrow and st and st.bearing and frame:IsShown()) then return end
  local gf = _G.GetPlayerFacing
  if not gf then return end
  local ok, facing = pcall(gf)
  if not ok or facing == nil or facing == st.facing then return end
  st.facing = facing
  st.rel = G.Relative(st.bearing, facing)
  if st.rel then setArrow(st.rel) end
end

function M.Refresh()
  local st = M.Compute()
  M.state = st
  if not frame then return st end
  local want = M.db and M.db.shown ~= false and M:IsEnabled()
  st.dup = (M.db and M.db.hideDup ~= false and not st.none) and M.OtherGuide(st.target) or nil
  if not want or st.hidden or st.none or st.dup or (M.db.hideCombat ~= false and inCombat()) then
    -- caché : instance / pas de position / pas de cible ; on garde le OnUpdate via un ticker léger
    if frame:IsShown() then frame:Hide() end
    return st
  end
  if not frame:IsShown() then frame:Show() end
  local t = st.target
  local lt = cut(t.label or t.title or "Cible", 34)
  if lt ~= lastLabel then lastLabel = lt; label:SetText(lt) end
  if st.rel then
    arrow:Show()
    setArrow(st.rel)
  else
    arrow:Hide()
  end
  if st.dist then
    local s = (st.approx and "≈" or "") .. G.Dist(st.dist, M.db.units)
    if st.eta then s = s .. " · " .. st.eta end
    if not st.facing then s = s .. " " .. G.Cardinal(st.bearing) end
    if st.arrived then s = "Arrivé !" end
    if s ~= lastInfo then lastInfo = s; info:SetText(s) end
  else
    local s = t.zone and ("Zone : " .. cut(t.zone, 28)) or ""
    if s ~= lastInfo then lastInfo = s; info:SetText(s) end
  end
  return st
end

function M.Show(on)
  M.db.shown = on and true or false
  createFrame()
  M.Refresh()
end

-- ------------------------------------------------------------------ résumé IA
function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local t = M.Current()
  local st = M.state or {}
  local units = M.db and M.db.units
  s.compass = {
    updated = try(_G.time), targets = #M.targets, hidden = st.hidden or nil,
    mapID = M.me and M.me.mapID, custom = (M.char and M.char.custom) and true or nil,
    target = t and { kind = t.kind, questID = t.questID, title = cut(t.title, 60), label = cut(t.label, 60),
      zone = t.zone, src = t.src, dist = t.dist and floor(t.dist + 0.5) or nil,
      distText = t.dist and G.Dist(t.dist, units) or nil, eta = t.dist and G.ETA(t.dist, M.Speed()) or nil } or nil,
  }
end

-- ------------------------------------------------------------------ cycle de vie
local pending
local function rebuildSoon(reason)
  if pending then return end
  pending = true
  T:After(0.7, function() pending = false pcall(M.Rebuild, reason) end)
end
M.RebuildSoon = rebuildSoon

function M:OnEnable()
  createFrame()
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_WATCH_LIST_CHANGED", "QUEST_TURNED_IN", "QUEST_ACCEPTED",
    "QUEST_REMOVED", "ZONE_CHANGED_NEW_AREA", "SUPER_TRACKING_CHANGED" }) do
    self:RegisterEvent(ev, function() rebuildSoon() end)
  end
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() rebuildSoon("init") end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if M.dirty then rebuildSoon() end M.Refresh() end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() M.Refresh() end)
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  self:On("QUESTS_UPDATED", function() rebuildSoon() end)
  -- ticker : flèche même cadre caché (pour réapparaître en sortant d'instance), cibles toutes les 10 s
  if not M.ticker then
    local n = 0
    M.ticker = T:NewTicker(1, function()
      n = n + 1
      if frame and not frame:IsShown() then M.Refresh() end
      if n % 10 == 0 then pcall(M.Rebuild) end
    end)
  end
  T:After(2, function() pcall(M.Rebuild, "init") M.Refresh() end)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
  if frame then frame:Hide() end
end

function M:OnOptionChanged(key, value)
  if key == "scale" and frame then frame:SetScale(tonumber(value) or 1) end
  M.Refresh()
end

-- ------------------------------------------------------------------ slash
T:RegisterSlash("way", function(rest)
  rest = tostring(rest or "")
  if rest:match("^%s*$") or rest:match("^%s*effacer") or rest:match("^%s*clear") then
    M.ClearWaypoint()
    M:Print("Point perso effacé.")
    return
  end
  local x, y, name = rest:match("^%s*([%d%.,]+)[%s,;]+([%d%.,]+)%s*(.-)%s*$")
  if x then x, y = x:gsub(",", "."), y:gsub(",", ".") end
  local w = x and M.SetWaypoint(x, y, name)
  if w then
    M:Print(("Point perso : %.1f, %.1f%s"):format(w.x * 100, w.y * 100, w.name and (" (" .. w.name .. ")") or ""))
  else
    M:Print("Usage : /tai way 45.2 60.1 [nom] (coordonnées de ta carte actuelle) · /tai way effacer")
  end
end, "point perso pour la boussole : /tai way x y [nom]")

-- toolbar button of the panel (UI.lua toggleCompass): a real toggle, silent (the user clicks, no chat help)
function M.Toggle() M.Show(not (frame and frame:IsShown())) end

T:RegisterSlash("aller", function() M.GoTo() end, "boussole : « Aller là » (Shortest Path / TomTom / repère de carte)")
local function cibleSlash() local t = M.Cycle(1) if t then M:Print("Cible : " .. (t.label or t.title or "?")) end end
T:RegisterSlash("cible", cibleSlash, "boussole : cible suivante")
T:RegisterSlash("target", cibleSlash)
T:RegisterSlash("boussole", function(rest)
  rest = low(rest)
  if rest:find("cach") or rest:find("off") then M.Show(false) M:Print("Boussole cachée (/tai boussole pour la remettre).")
  elseif rest:find("yd") or rest:find("yard") then M.db.units = "yd" M.Refresh()
  elseif rest:match("^%s*m%s*$") or rest:find("metre") or rest:find("mètre") then M.db.units = "m" M.Refresh()
  else
    local sc = tonumber(rest:match("([%d%.]+)"))
    if sc and sc >= 0.5 and sc <= 2 then M.db.scale = sc if frame then frame:SetScale(sc) end end
    M.Show(true)
    local t = M.Current()
    M:Print("Boussole : " .. (t and (t.label or t.title) or "aucune cible") .. " · /tai cible, /tai way x y, /tai boussole yd|m|1.2|off")
  end
end, "boussole : afficher / cacher / yd|m / taille")
