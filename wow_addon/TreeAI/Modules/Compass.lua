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
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Compass", {
  title = "Boussole",
  -- (ZONEFIX) en combat : la flèche reste (sans texte), sauf si l'option « Cacher en combat » est cochée
  defaults = { enabled = true, shown = true, units = "m", autoNext = true, announce = true, scale = 1, locked = false,
               hideCombat = false, hideDup = true },
  charDefaults = { point = "TOP", x = 0, y = -120, custom = nil },
  options = {
    { key = "shown", type = "toggle", label = "Afficher la boussole (flèche vers la cible)" },
    { key = "autoNext", type = "toggle", label = "Passer à la cible suivante quand un objectif est fini" },
    { key = "announce", type = "toggle", label = "Annoncer la nouvelle cible dans « Maintenant »" },
    { key = "locked", type = "toggle", label = "Verrouiller la boussole" },
    { key = "hideCombat", type = "toggle", label = "Cacher la boussole en combat (sinon : la flèche seule, sans texte)" },
    { key = "hideDup", type = "toggle",
      label = "Une seule flèche : la boussole s'efface quand une autre flèche vise le même endroit (à 20 m près)" },
    { key = "scale", type = "range", label = "Taille de la boussole", min = 0.5, max = 2, step = 0.1 },
  },
})
ns.Compass = M
T.Compass = M

local floor, sqrt, atan2, pi, abs = math.floor, math.sqrt, math.atan2, math.pi, math.abs
local YD2M = 0.9144
local RUN = 7  -- vitesse de course (yd/s) si l'API ne donne rien

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function now() return try(_G.GetTime) or 0 end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
--- (4.1 INTEG) le texte de la cible dans la boîte (40 lettres) : un arrêt à plusieurs quêtes trop long garde la 1re
--- entière et le nombre des autres (« Rendre « A » (+2) »), pas « Rendre « A » + Ren… » ; la fenêtre dit tout
function M.ShortLabel(s)
  s = tostring(s or "")
  local _, n = s:gsub("[^\128-\191]", "")
  if n <= 40 then return s end
  local head, rest = s:match("^(.-) %+ (.+)$")
  if head then
    local _, more = rest:gsub(" %+ ", "")
    return cut(head .. " (+" .. (more + 1) .. ")", 40)
  end
  return cut(s, 40)
end
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
  if s < 600 then                                   -- « 1 min 05 » : les secondes comptent sur un court trajet
    s = floor(s + 0.5)
    return ("%d min %02d"):format(floor(s / 60), s % 60)
  end
  if s < 3600 then return floor(s / 60 + 0.5) .. " min" end
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
  local NT = T.NavTime                               -- vitesse mesurée (à pied / monté) si le module est là
  if NT and NT.Speed then return (NT.Speed()) end
  local s = _ns((try(_G.GetUnitSpeed, "player")))
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
  -- (3.9.8) TreeAI autonome : Suivi Forever ignoré tant que le joueur n'a pas choisi « Guidage : Suivi Forever »
  if not (T.GuideMode and T:GuideMode() == "auto") then return nil end
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
  if not M.SPFOptIn() then return nil end           -- notre modèle calibré (NavTime) par défaut
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
  if A and M.SPFOptIn() and not inCombat() and spfCall(A, "Navigate", "TreeAI", t.mapID, t.x, t.y, title) then return "spf" end
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
      M.ownWP = { mapID = t.mapID, x = t.x, y = t.y }   -- NOTRE repère (retiré si le joueur passe à Suivi Forever)
      return "blizzard"
    end
  end
  return nil
end
-- Une seule flèche à l'écran : un autre guide (repère / quête suivie de Blizzard, Shortest Path, TomTom)
-- vise-t-il déjà la même cible ? Lecture seule. Renvoie "blizzard" / "spf" / "tomtom" ou nil.
local function near(a, b) return a and b and math.abs(a - b) < 0.012 end
-- repère utilisateur de Blizzard : carte, x, y (nil sans repère)
local function userWaypoint()
  local CM = _G.C_Map
  if not (CM and CM.HasUserWaypoint and try(CM.HasUserWaypoint) and CM.GetUserWaypoint) then return nil end
  local wp = try(CM.GetUserWaypoint)
  local pos = type(wp) == "table" and wp.position
  local px, py
  if type(pos) == "table" then
    if type(pos.GetXY) == "function" then local ok, a, b = pcall(pos.GetXY, pos) if ok then px, py = a, b end end
    px, py = px or pos.x, py or pos.y
  end
  if not wp then return nil end
  return wp.uiMapID, px, py
end
--- (FIDELITY F11) le joueur confie le guidage à Suivi Forever : NOTRE repère Blizzard (posé par « Aller là ») est
--- retiré, sinon deux flèches à l'écran (le repère + celle de SPF). Seulement le nôtre : même carte, même point.
function M.DropOwnWaypoint()
  local w = M.ownWP
  if not w then return false end
  M.ownWP = nil
  if M.guide and M.guide.how == "blizzard" then M.guide = nil end
  local CM = _G.C_Map
  local m, px, py = userWaypoint()
  if m and m == w.mapID and near(px, w.x) and near(py, w.y) and CM and CM.ClearUserWaypoint then
    return pcall(CM.ClearUserWaypoint) and true or false
  end
  return false
end
-- (ZONEFIX) « une seule flèche » seulement quand l'autre flèche vise LE MÊME endroit (à 20 m près) : un repère
-- Blizzard sans rapport (« 467 m » sur la capture 20) ne cache plus la boussole. → "blizzard" / "spf" / "tomtom" ou nil
M.DUP_YD = 20 / YD2M
local function sameSpot(t, mapID, x, y)
  if not (t and t.mapID and t.x and tonumber(mapID) and tonumber(x) and tonumber(y)) then return false end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  local d = G.Vector({ mapID = mapID, x = x, y = y }, t)
  return d ~= nil and d <= M.DUP_YD
end
M.SameSpot = sameSpot
function M.OtherGuide(t)
  if not t then return nil end
  local ST, CM = _G.C_SuperTrack, _G.C_Map
  -- quête suivie en super-suivi par le jeu : sa flèche vise le point de la quête (GetNextWaypoint) → même endroit ?
  if t.questID and ST and ST.GetSuperTrackedQuestID then
    local q = _ns(try(ST.GetSuperTrackedQuestID))
    if q and q ~= 0 and q == t.questID then
      local QL = _G.C_QuestLog
      local wm, wx, wy
      if QL and QL.GetNextWaypoint then wm, wx, wy = try(QL.GetNextWaypoint, q) end
      if sameSpot(t, wm, wx, wy) then return "blizzard" end
    end
  end
  local wm, px, py
  if t.mapID and t.x then wm, px, py = userWaypoint() end
  if wm then
    local superUser = not (ST and ST.IsSuperTrackingUserWaypoint) or try(ST.IsSuperTrackingUserWaypoint)
    if superUser and sameSpot(t, wm, px, py) then return "blizzard" end
  end
  -- nos propres « Aller là » : le guide posé par un clic sur CETTE cible
  if M.guide and M.guide.key == t.key then
    if M.guide.how == "tomtom" then
      local arrow = rawget(_G, "TomTomCrazyArrow")
      if not (type(arrow) == "table" and arrow.IsVisible) or try(arrow.IsVisible, arrow) then return "tomtom" end
    elseif M.guide.how == "spf" then
      local A = M.SPF()
      local active = A and (type(A.Active) ~= "function" or spfCall(A, "Active"))
      if active then return "spf" end
    elseif M.guide.how == "blizzard" then
      if not (CM and CM.HasUserWaypoint) or try(CM.HasUserWaypoint) then return "blizzard" end
    end
  end
  return nil
end
-- une ligne courte qui dit pourquoi la boussole s'efface (option « une seule flèche », guidage confié à Suivi Forever)
M.DUP_TXT = { blizzard = "Flèche du jeu sur la même cible", spf = "Suivi Forever te guide", tomtom = "TomTom vise la même cible" }

-- (COHERE) Shortest Path guide-t-il en ce moment (sa flèche + « Arrêt n sur N » à l'écran) ? Lecture seule :
-- API publique (Active / IsNavigating / IsActive / CurrentStop), sinon notre propre envoi (Route.following).
-- Alors la boussole TreeAI se cache : une seule flèche, la sienne, sur l'arrêt courant.
local SPF_ACTIVE = { "Active", "IsNavigating", "IsActive" }
function M.SPFGuiding()
  local A = M.SPF()
  if not A then return false end
  for _, fn in ipairs(SPF_ACTIVE) do
    if type(A[fn]) == "function" then
      local on = spfCall(A, fn)
      if on then return true end
    end
  end
  if type(A.CurrentStop) == "function" and spfCall(A, "CurrentStop", "TreeAI") then return true end
  local R = T.Route
  return (R and R.following) and true or false
end

-- Un itinéraire TreeAI confié à Shortest Path (clic du joueur) s'affiche en ligne flottante en haut de l'écran
-- (« Arrêt 1 sur 1 : … »). À la mort, il recouvre la fenêtre de mort de Blizzard et vise une quête au lieu du
-- corps : on retire NOTRE itinéraire (propriétaire « TreeAI » seulement, API publique de SPF, testée avant usage).
-- Rien n'est relancé tout seul : le joueur reclique (« Aller là » / itinéraire) une fois relevé.
local SPF_STOP = { "StopRoute", "ClearRoute", "StopNavigation", "Stop", "Clear", "Cancel" }
function M.MarkRoute(how) M.routeBy = how end
function M.ClearRouteOnDeath()
  if M.routeBy ~= "spf" and not (M.guide and M.guide.how == "spf") then return false end
  local A = M.SPF()
  if not A then M.routeBy = nil return false end
  for _, fn in ipairs(SPF_STOP) do
    if type(A[fn]) == "function" and spfCall(A, fn, "TreeAI") ~= nil then break end
  end
  M.routeBy = nil
  if M.guide and M.guide.how == "spf" then M.guide = nil end
  return true
end

local PIN_TXT = { spf = "Shortest Path te guide", tomtom = "point TomTom posé", blizzard = "repère posé sur la carte" }
function M.GoTo(t, say)
  t = t or M.Current()
  local how = M.Pin(t)
  M.guide = (how and t) and { key = t.key, how = how } or nil
  if not say then return how end   -- (4.0 QA) un clic : la flèche / le repère répondent, rien dans le chat
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
  -- DATA : nom EXACT (normalisé) — « loup » ne ramène plus les points de « Loup des bois » d'un objectif fini
  local Dt = T.Detect
  local norm = (type(Dt) == "table" and type(Dt.Norm) == "function") and Dt.Norm or low
  local function wanted(name)
    if not objNames then return true end
    local n = norm(name)
    if n == "" then return false end
    return objNames[n] and true or false
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
        -- (ROUTEFIX) objet VENDU (« achète des épices ») : les marchands qui le vendent sont des points de l'objectif —
        -- avant, seuls le butin et les objets du monde comptaient : TreeAI ne disait rien, Suivi Forever le disait
        local n0 = #out
        for _, npc in ipairs(try(db.QueryItemSingle, itemID, "vendors") or {}) do
          if #out >= 40 then break end
          spawnsOf("npc", npc, db, zdb, playerMap, out)
        end
        for i = n0 + 1, #out do out[i].vendor = iname or true end
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

-- noms (normalisés) des objectifs NON finis, + noms Questie associés à ces objectifs (Detect.MapObjectives) ;
-- anyDone = un objectif est déjà fini (alors jamais de repli « tous les objectifs »)
local T2TYPE = { m = "monster", i = "item", o = "object", e = "event", r = "reputation" }
local function unfinishedNames(q)
  local Dt = T.Detect
  local norm = (type(Dt) == "table" and type(Dt.Norm) == "function") and Dt.Norm
    or function(s) return (low(s):gsub("%s+tu[ée]+e?s?$", ""):gsub("%s+slain$", "")) end
  local map = {}
  if type(Dt) == "table" and type(Dt.MapObjectives) == "function" and type(q.id) == "number" then
    local game = {}
    for i, o in ipairs(q.obj or {}) do game[i] = { name = o.name, type = T2TYPE[o.t or ""] } end
    map = Dt.MapObjectives(q.id, game) or {}
  end
  local t, any, anyDone = {}, false, false
  for i, o in ipairs(q.obj or {}) do
    if not o.finished then
      t[norm(o.name)] = true
      if map[i] and map[i].name then t[norm(map[i].name)] = true end
      if map[i] and map[i].alt then t[norm(map[i].alt)] = true end
      any = true
    else
      anyDone = true
    end
  end
  return any and t or nil, anyDone
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
      local names, anyDone = unfinishedNames(q)
      pts = M.QuestieObjectivePoints(q.id, names, pmap)
      -- repli « tous les points » seulement si AUCUN objectif n'est fini (sinon on montrerait un objectif fait)
      if pts and #pts == 0 and not anyDone then pts = M.QuestieObjectivePoints(q.id, nil, pmap) end
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

-- (QA-QUEST F3) un point posé à la main (repère de la minicarte, « Y aller », /tai way) ne passe plus devant le
-- trajet pour toute la session : effacé à l'arrivée (≤ 20 m pendant 3 s), au changement de zone s'il est sur un
-- autre continent, jamais gardé après un /reload sans un clic ; s'il est hors d'atteinte d'ici (autre continent)
-- ou posé il y a plus de 10 min (sauf ton propre /tai way et ton corps), il passe après l'arrêt du trajet.
M.CLEAR_YD, M.CLEAR_SEC, M.STALE_SEC = 20 / YD2M, 3, 600
-- (ZONEFIX) un arrêt du trajet choisi à la main (clic sur la carte / la page) : devant le trajet 5 min au plus (et
-- rendu au trajet dès que cet arrêt est fait : Planner.ReleaseNav)
M.PLAN_STALE_SEC = 300
-- → "far" (hors d'atteinte d'ici : pas de flèche possible → après les quêtes qu'on peut viser), "old" (posé il y a
-- plus de 10 min → après l'arrêt du trajet) ou false
function M.CustomStale(c, me)
  if type(c) ~= "table" or c.source == "corpse" then return false end
  if me and not G.Vector(me, { mapID = c.mapID, x = c.x, y = c.y }) then return "far" end
  local life = (c.source == "plan") and M.PLAN_STALE_SEC or M.STALE_SEC
  return (c.source ~= "perso" and tonumber(c.at) ~= nil and now() - c.at > life) and "old" or false
end

-- Construit la liste triée des cibles. list = liste des quêtes (Quests.lastList) ; me = position.
-- (ZONEFIX) la cible du trajet = l'arrêt 1 de la carte (Route, même objet) ; repli : l'ancien contrat ZonePlan
local function routeTarget(me)
  local Rt = T.Route
  if Rt and Rt.CompassTarget then return try(Rt.CompassTarget, Rt, me) end
  local Zp = T.ZonePlan
  return Zp and Zp.CompassTarget and try(Zp.CompassTarget, me) or nil
end
M.RouteTarget = routeTarget
-- (ZONEFIX) rang : le corps d'abord (fantôme) ; un point posé à la main juste avant (clic, /tai way) ; puis TOUJOURS
-- l'arrêt 1 du trajet ; un point à la main vieilli (5 min pour un arrêt du trajet, 10 min sinon) passe après
local function targetRank(t)
  if t.kind == "custom" then return (t.corpse and -1) or (t.stale == "far" and 2.5) or (t.stale and 0.6) or 0 end
  return (t.plan and 0.5) or (t.super and 1) or (t.kind == "zone" and 3) or 2
end
local function byRank(a, b)
  if a._rank ~= b._rank then return a._rank < b._rank end
  local da, db = a.dist or 1e12, b.dist or 1e12
  if da ~= db then return da < db end
  return tostring(a.key) < tostring(b.key)
end
function M.BuildTargets(list, me)
  local out = {}
  local c = M.char and M.char.custom
  if type(c) == "table" and c.mapID and c.x then
    out[#out + 1] = { key = "custom", kind = "custom", title = c.name or "Point perso", label = c.name or "Point perso",
      mapID = c.mapID, x = c.x, y = c.y, src = "perso", stale = M.CustomStale(c, me) or nil, source = c.source,
      corpse = c.source == "corpse" or nil }
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
        if q.complete and p.name then t.label = ("Rendre « %s » à %s"):format(cut(q.title, 26), p.name)
        elseif p.vendor and p.name then
          t.label = ("Acheter « %s » chez %s"):format(cut(type(p.vendor) == "string" and p.vendor or t.label, 24), cut(p.name, 22))
        end
      else
        t.kind = "zone"
      end
      out[#out + 1] = t
    end
  end
  -- (ZONEPLAN) l'arrêt en cours du trajet passe devant (après un point perso) ; (ZONEFIX) pendant un recalcul du
  -- trajet (quelques images sans étapes), l'ancien arrêt 1 reste visé : la boussole ne saute plus d'une cible à l'autre
  local pt = routeTarget(me)
  if type(pt) ~= "table" then
    local P = T.Planner
    local busy = P and ((P.Busy and P:Busy()) or (P.route and not P.steps))
    local last = M.lastPlan
    if busy and type(last) == "table" and now() - (last.seenAt or 0) < 30 then pt = last end
  else
    pt.seenAt = now()
    M.lastPlan = pt
  end
  if type(pt) == "table" then out[#out + 1] = pt end
  for _, t in ipairs(out) do t._rank = targetRank(t) end   -- une fois par cible (pas à chaque comparaison du tri)
  table.sort(out, byRank)
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
-- (ZONEFIX) journal de la boussole (NavTrace.lua) : chaque changement de cible, avec sa raison
function M.TraceTarget(prev, cur, why)
  local NTr = T.NavTrace
  if not (NTr and NTr.Log) or (prev and prev.key) == (cur and cur.key) then return end
  local r
  if not cur then r = "plus rien à viser"
  elseif cur.corpse then r = "ton corps"
  elseif cur.kind == "custom" then r = (cur.source == "plan" and "arrêt choisi à la main") or "point posé à la main"
  elseif why == "cycle" or why == "quest" then r = "clic : " .. (why == "cycle" and "cible suivante" or "cette quête")
  elseif cur.plan then
    r = (why == "plan" and "trajet recalculé : son arrêt 1") or (cur.leg and ("départ du trajet : " .. tostring(cur.leg)))
      or "arrêt 1 du trajet"
  elseif cur.super then r = "quête suivie par le jeu"
  else r = "quête la plus proche (pas de trajet)" end
  NTr.Log("cible", NTr.Target(prev) .. " → " .. NTr.Target(cur) .. " (" .. r .. ")")
end
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
        -- (QA-QUEST F13) un trajet recalculé / réordonné n'est pas « Objectif fini » : rien n'est annoncé quand
        -- l'ancienne ou la nouvelle cible est un arrêt du trajet (le Planner dit déjà « Étape suivante : … »)
        local nt = M.targets[M.idx]
        if not (prev.plan or (nt and nt.plan)) then
          local fini = progressed
          if not fini and prev.kind == "objective" and prev.questID then
            for _, t in ipairs(M.targets) do if t.key == "turn:" .. prev.questID then fini = true end end
          end
          announce(nt, (prev.kind == "objective" and fini) and "Objectif fini → suivant" or "Cible suivante")
        end
      elseif found then
        M.idx = found
      end
    end
  end
  M.WriteSummary()
  -- (ZONEFIX) nouvelle cible (trajet recalculé, arrêt fini) : affichée tout de suite, même immobile (l'animation ne
  -- tourne plus à l'arrêt : sans ça la boussole gardait l'ancienne cible jusqu'au prochain pas)
  local cur = M.Current()
  M.TraceTarget(prev, cur, reason)
  if M._frame and reason ~= "arrived" and not M._inRefresh and (not prev ~= not cur or (prev and cur and (prev.key ~= cur.key
      or prev.mapID ~= cur.mapID or prev.x ~= cur.x or prev.y ~= cur.y or prev.label ~= cur.label))) then
    M.Refresh()
  end
  return M.targets
end

function M.Cycle(step)
  local n = #M.targets
  if n == 0 then M.Rebuild() n = #M.targets end
  if n == 0 then M:Print("Aucune cible : pas de quête suivie ni de point perso (/tai way x y).") return nil end
  local before = M.Current()
  M.idx = ((M.idx - 1 + (step or 1)) % n) + 1
  M.pinned = true
  local t = M.Current()
  M.TraceTarget(before, t, "cycle")
  announce(t, "Cible")
  M.Refresh()
  return t
end

function M.SetQuestTarget(qid)
  if #M.targets == 0 then M.Rebuild() end
  for i, t in ipairs(M.targets) do
    if t.questID == qid then
      local before = M.Current()
      M.idx, M.pinned = i, true
      M.TraceTarget(before, t, "quest")
      announce(t, "Cible")
      M.Refresh()
      return t
    end
  end
  return nil
end

-- point perso : /tai way 45.2 60.1 [nom] (coordonnées de la carte où tu es) ; source = "perso" (/tai way), "corpse"
-- (Speed), "detect", "finder", "plan"… ou nil (repère de la minicarte, suivi) ; at = posé quand (GetTime)
function M.SetWaypoint(x, y, name, mapID, source)
  x, y = tonumber(x), tonumber(y)
  if not (x and y) then return nil end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  if x < 0 or x > 1 or y < 0 or y > 1 then return nil end
  local me = M.Player()
  mapID = mapID or (me and me.mapID)
  if not mapID then return nil end
  M.char.custom = { mapID = mapID, x = x, y = y, name = (name and name ~= "") and cut(name, 40) or nil, source = source,
    at = now() }
  M.resume = nil
  M:Clear("resume")
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
  local r = M.SetWaypoint(t.x, t.y, t.label, t.mapID, t.source)
  if r then r.source, r.questID, r.stepKey = t.source, t.questID, t.stepKey end
  return r
end
--- (ZONEFIX) « guider » vers l'arrêt en cours : la boussole revient au trajet (aucun point perso, sauf le corps) ;
--- vrai quand elle vise bien l'arrêt 1 du trajet
function M.FollowRoute()
  local c = M.char and M.char.custom
  if type(c) == "table" and c.source ~= "corpse" then M.char.custom = nil end
  M.pinned = nil
  M.Rebuild("force")
  for i, t in ipairs(M.targets) do if t.plan then M.idx = i break end end
  M.Refresh()
  local cur = M.Current()
  return (cur and cur.plan) and true or false
end

-- (QA-QUEST F3) hors d'atteinte d'ici (autre continent : pas de distance possible) → effacé ; le corps jamais
-- (Speed le gère), et rien n'est décidé sans position (instance, chargement)
function M.DropUnreachable()
  local c = M.char and M.char.custom
  if type(c) ~= "table" or c.source == "corpse" or not (c.mapID and c.x) then return false end
  local me = M.Player()
  if not me or G.Vector(me, { mapID = c.mapID, x = c.x, y = c.y }) then return false end
  M.char.custom, M.pinned = nil, nil
  return true
end
-- (QA-QUEST F3) /reload ou connexion : le point d'avant n'est pas repris tout seul ; une ligne propose de le
-- reprendre d'un clic (le corps : Speed le revise s'il le faut)
function M.ForgetSaved()
  if M._sessionChecked then return end
  M._sessionChecked = true
  local c = M.char and M.char.custom
  if type(c) ~= "table" then return end
  M.char.custom = nil
  if c.source == "corpse" or not (c.mapID and c.x) then return end
  M.resume = c
  M:Suggest("resume", ("Boussole : reprendre « %s » ?"):format(cut(c.name or "ton point", 30)), 1, { ttl = 120, sound = false,
    tooltip = "La boussole visait ce point avant le /reload.\nClic : le viser de nouveau.", onClick = function() M.Resume() end })
end
function M.Resume()
  local c = M.resume
  M.resume = nil
  M:Clear("resume")
  if type(c) ~= "table" or not M.char then return nil end
  c.at, c.arrivedAt = now(), nil
  M.char.custom, M.pinned = c, nil
  M.Rebuild()
  M.idx = 1
  M.Refresh()
  return c
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
local ARRIVE = { custom = 12, turnin = 15, objective = 30, zone = 0 }
M.ARRIVE = ARRIVE
function M.Compute()
  local t = M.Current()
  local me = M.Player()
  M.me = me
  if not t then return { none = true, hidden = me == nil } end
  local st = { target = t, me = me, hidden = me == nil }
  local d, bearing, approx
  if t.mapID then
    -- coordonnées monde de la cible : la flèche suit le joueur entre deux calculs (UnitPosition, sans table)
    st.tc, st.tn, st.tw = G.World(t.mapID, t.x, t.y)
    if me then d, bearing, approx = G.Vector(me, t) end
    if not d and st.tn then
      -- (ZONEFIX) pas de position sur la carte (grotte, sous-carte sans coordonnées) : la position monde suffit — la
      -- boussole ne disparaît plus « de temps en temps » en passant sous un pont ou dans une mine
      local py, px, _, inst = try(_G.UnitPosition, "player")
      py, px, inst = _ns(py), _ns(px), _ns(inst)
      if py and px and (inst == nil or inst == st.tc) then
        local n, w = st.tn - py, st.tw - px
        d, bearing, approx = sqrt(n * n + w * w), atan2(w, n), false
        st.hidden, st.world = false, true
      end
    end
  end
  if d then
    st.dist, st.bearing, st.approx = d, bearing, approx
    local facing = _ns(try(_G.GetPlayerFacing))
    st.facing = facing
    st.rel = G.Relative(bearing, facing)
    local tnow = now()
    if me and (t ~= M.travelT or tnow - (M.travelAt or 0) > 3) then
      M.travelT, M.travelAt, M.travelSec = t, tnow, M.Travel(me, t)
    end
    local NT = T.NavTime
    if M.travelSec and M.travelT == t then st.sec, st.spf = M.travelSec, true
    else
      -- (ROUTEFIX) UNE formule avec le trajet (Route.WalkSecMap : chemin appris, relief de la zone, monture) — la
      -- boussole, la fenêtre et la bande disent le même temps pour l'arrêt 1
      local Rt = T.Route
      local ws = me and Rt and Rt.WalkSecMap and Rt.WalkSecMap(me, t)
      st.sec = ws or ((NT and NT.Model) and NT.Model(d, t.mapID)) or d / M.Speed()
    end
    -- (ROUTEFIX) « par le chemin » : la ligne droite traverse une falaise / tourne trop (chemin appris, PathLearn) → la
    -- flèche vise le prochain point de passage ; la distance et le temps restent ceux de l'arrêt
    st.an, st.aw, st.via = nil, nil, nil
    local PLm = T.PathLearn
    if PLm and PLm.Waypoint and st.tn and not approx then
      local py, px, _, inst = try(_G.UnitPosition, "player")
      py, px, inst = _ns(py), _ns(px), _ns(inst)
      if not (py and px) and me then inst, py, px = G.World(me.mapID, me.x, me.y) end   -- client sans UnitPosition
      if py and px and (inst == nil or inst == st.tc) then
        local okW, wp = pcall(PLm.Waypoint, { c = st.tc, n = py, w = px }, { c = st.tc, n = st.tn, w = st.tw })
        if okW and type(wp) == "table" and wp.n then
          st.an, st.aw, st.via = wp.n, wp.w, true
          local n2, w2 = wp.n - py, wp.w - px
          st.bearing = atan2(w2, n2)
          st.rel = G.Relative(st.bearing, facing)
        end
      end
    end
    st.eta = st.sec and G.ETA(st.sec * RUN, RUN)
    st.secPerYd = (st.sec and d > 1) and st.sec / d or nil      -- (ZONEFIX) temps relu 5 ×/s en marchant
    t.dist = d
    t.arrive = t.arrive or ARRIVE[t.kind] or 15
    if d <= t.arrive then st.arrived = true end
    if NT and NT.Track and not approx and me then pcall(NT.Track, t, me, d) end
    -- (QA-QUEST F3) point posé à la main atteint (≤ 20 m pendant 3 s) : effacé, la boussole revient au trajet
    local c = M.char and M.char.custom
    if t.kind == "custom" and type(c) == "table" and c.source ~= "corpse" and d and not approx then
      if d <= M.CLEAR_YD then
        c.arrivedAt = c.arrivedAt or tnow
        if tnow - c.arrivedAt >= M.CLEAR_SEC then
          M.char.custom, M.pinned = nil, nil
          M.Rebuild("arrived")
          if M.Current() ~= t then return M.Compute() end
        end
      else
        c.arrivedAt = nil
      end
    end
  end
  return st
end

-- ------------------------------------------------------------------ cadre (le nôtre, jamais Blizzard)
-- Inspiré des meilleures flèches (idées seulement) : TomTom (grande flèche, couleur selon l'alignement,
-- demi-tour), Shortest Path (une seule flèche, durée réelle), Questie / Zygor / RXP (étape en sous-titre,
-- arrivée claire). (ZONEFIX) Rotation lissée 20 à 30 Hz et distance / temps 5 fois par seconde quand tu bouges ou
-- tournes ; à l'arrêt : aucun OnUpdate (un coup d'œil par seconde, réveil aux évènements de mouvement).
local W, H = 200, 112
M.W, M.H = W, H
local frame, arrow, shadow, ring, check, dist, label, tag
local ARROW = "Interface\\Minimap\\MinimapArrow"
local RING = "Interface\\CHARACTERFRAME\\TempPortraitAlphaMask"
local CHECK = "Interface\\RaidFrame\\ReadyCheck-Ready"
local BEHIND = 2.55          -- ~146° : « derrière toi »
local function savePos()
  if not frame or not M.char then return end
  local p, _, _, x, y = frame:GetPoint(1)
  M.char.point, M.char.x, M.char.y = p or "TOP", x or 0, y or -120
end

-- couleur selon l'écart (0 = en face → vert ; 90° → jaune ; 180° → rouge), sans table
function G.AlignColor(rel)
  if not rel then return 1, 0.85, 0.2 end
  local a = rel % (2 * pi)
  if a > pi then a = 2 * pi - a end
  local k = a / pi                                  -- 0..1
  if k < 0.5 then return 0.25 + 1.5 * k, 1, 0.25 end -- vert → jaune
  return 1, 1 - 1.5 * (k - 0.5) * 1.33, 0.2          -- jaune → rouge
end
-- plus court chemin angulaire de a vers b (−pi..pi)
function G.Shortest(a, b)
  local d = (b - a) % (2 * pi)
  if d > pi then d = d - 2 * pi end
  return d
end

-- (ZONEFIX) mises à jour : la flèche ≥ 20 Hz (jusqu'à 30) et la distance / le temps 5 fois par seconde TANT QUE tu
-- bouges ou tournes (évènements du jeu PLAYER_STARTED_MOVING / TURNING / LOOKING, sinon un coup d'œil par seconde à
-- ta position) ; à l'arrêt ou cachée : plus d'OnUpdate du tout. Le texte n'est réécrit que si l'arrondi change.
local shown, target, lastFacing = nil, nil, nil
local lastR, lastLabel, lastInfo, lastTag
local lastQ, lastE                                 -- dernier texte rapide : yards, secondes
local mini, textOff, anim = false, false, false
local moving, turning = false, false
local acc, textAcc, fullAcc, still = 0, 0, 0, 0
local lastPy, lastPx
local glancePos   -- (défini plus bas, avant SetAnim)
local function paint(rel)
  if rel == lastR then return end
  lastR = rel
  if arrow.SetRotation then arrow:SetRotation(rel) end
  if shadow.SetRotation then shadow:SetRotation(rel) end
  local r, g, b = G.AlignColor(rel)
  arrow:SetVertexColor(r, g, b)
end
M.Paint = function(rel) if arrow then paint(rel) end end

local function setTag(s, r, g, b)
  if s == lastTag then return end
  lastTag = s
  tag:SetText(s or "")
  if s then tag:SetTextColor(r, g, b) end
end

--- un pas d'animation : cible d'angle (cap absolu de la cible − orientation), approche lissée ;
--- → vrai tant que quelque chose bouge (ta position, ton orientation, la flèche pas encore sur sa cible)
function M.Step(dt)
  local st = M.state
  if not (frame and st and st.bearing and frame:IsShown() and not st.arrived and not mini) then return false end
  local facing = _ns(try(_G.GetPlayerFacing))
  if not facing then return false end
  local bearing = st.bearing
  local posMoved = false
  if st.tn and st.tc then                            -- position fine sans allocation (UnitPosition)
    local py, px, _, inst = try(_G.UnitPosition, "player")
    py, px, inst = _ns(py), _ns(px), _ns(inst)
    if py and px and (inst == nil or inst == st.tc) then
      if py ~= lastPy or px ~= lastPx then posMoved, lastPy, lastPx = true, py, px end
      local n, w = (st.an or st.tn) - py, (st.aw or st.tw) - px
      if n * n + w * w > 4 then bearing = atan2(w, n) st.bearing = bearing end
    end
  end
  local turned = facing ~= lastFacing
  lastFacing = facing
  target = (bearing - facing) % (2 * pi)
  st.facing, st.rel = facing, target
  if not shown then shown = target end
  local d = G.Shortest(shown, target)
  if abs(d) < 0.004 then shown = target
  else shown = (shown + d * math.min(1, (dt or 0.033) * 14)) % (2 * pi) end
  paint(shown)
  local behind = target > BEHIND and target < 2 * pi - BEHIND
  st.behind = behind
  if not textOff then
    if behind then setTag("Derrière toi", 1, 0.35, 0.3)
    elseif st.via then setTag("Par le chemin", 0.6, 0.9, 1)
    else setTag(nil) end
  end
  return posMoved or turned or abs(d) >= 0.004
end
M.Spin = function() M.Step(0.1) end

-- distance + temps depuis UnitPosition (aucune conversion de carte) ; → vrai à l'arrivée (calcul complet tout de suite)
local function fastText()
  local st = M.state
  if textOff or mini or not (frame and st and st.tn and st.dist and not st.arrived and not st.approx) then return false end
  local py, px, _, inst = try(_G.UnitPosition, "player")
  py, px, inst = _ns(py), _ns(px), _ns(inst)
  local d
  if py and px then
    if inst ~= nil and inst ~= st.tc then return false end
    local n, w = st.tn - py, st.tw - px
    d = sqrt(n * n + w * w)
  else                                               -- client sans UnitPosition : la position sur la carte
    local me = M.Player()
    d = me and st.target and G.Vector(me, st.target)
    if not d then return false end
  end
  st.dist = d
  if st.target then st.target.dist = d end
  -- (ZONEFIX) au yard / à la seconde près : le texte n'est refait (et posé) que si ce qui s'affiche a pu changer
  local q, e = floor(d + 0.5), st.secPerYd and floor(d * st.secPerYd + 0.5) or -1
  if q ~= lastQ or e ~= lastE or not st.facing or not lastInfo then
    lastQ, lastE = q, e
    if st.secPerYd then st.sec = d * st.secPerYd; st.eta = G.ETA(st.sec * RUN, RUN) end
    local s = G.Dist(d, M.db.units)
    if st.eta then s = s .. "  ·  " .. st.eta end
    if not st.facing then s = s .. " " .. G.Cardinal(st.bearing) end
    if s ~= lastInfo then lastInfo = s; dist:SetText(s) end
  end
  local t = st.target
  local arrive = t and (t.arrive or ARRIVE[t.kind] or 15)
  return arrive ~= nil and d <= arrive
end
M.FastText = fastText

local animAt, animN, textN, moveS, rateAt = 0, 0, 0, 0, -1e9   -- cadence obtenue (journal), pas de table par image
local function onUpdate(_, el)
  el = el or 0
  acc, textAcc, fullAcc = acc + el, textAcc + el, fullAcc + el
  if acc < 0.033 then return end                   -- ≤ 30 Hz
  local dt = acc
  acc = 0
  if fullAcc >= 1 then fullAcc = 0 M.Refresh() end  -- calcul complet 1 fois par seconde tant que ça bouge
  local active = M.Step(dt)
  animN = animN + 1
  if moving then moveS = moveS + dt end
  if textAcc >= 0.2 then                            -- 5 Hz : distance / temps
    textAcc = 0
    if not textOff then textN = textN + 1 end
    if fastText() then fullAcc = 0 M.Refresh() end
  end
  if active or moving or turning then still = 0
  else
    still = still + dt
    if still >= 0.5 then M.SetAnim(false) end      -- immobile : plus rien par image
  end
end
--- l'animation (OnUpdate) posée seulement quand quelque chose bouge et que la flèche est à l'écran
--- (journal) une marche d'au moins 3 s : la cadence vraiment obtenue (flèche, texte), une ligne par minute au plus
local function traceRate()
  local NTr = T.NavTrace
  local dur = now() - animAt
  if not (NTr and NTr.Log) or dur < 3 or moveS < 2 or now() - rateAt < 60 then return end
  rateAt = now()
  NTr.Log("marche", ("%s en mouvement : flèche %d Hz, texte %d Hz%s"):format(NTr.Sec(dur), floor(animN / dur + 0.5),
    floor(textN / dur + 0.5), textOff and " (combat : flèche seule)" or ""))
end
-- (ZONEFIX) la position telle que le coup d'œil la lit (UnitPosition, sinon la carte) et l'orientation
glancePos = function()
  local py, px = try(_G.UnitPosition, "player")
  py, px = _ns(py), _ns(px)
  if not py then                                     -- client sans UnitPosition : la position sur la carte
    local me = M.Player()
    if me then py, px = me.x * 100000 + me.mapID, me.y end
  end
  return py, px, _ns(try(_G.GetPlayerFacing))
end
function M.SetAnim(on)
  if not frame then return false end
  if on and frame:IsShown() and not mini then
    still = 0
    if not anim then
      anim, acc, textAcc = true, 0, 0
      animAt, animN, textN, moveS = now(), 0, 0, 0
      frame:SetScript("OnUpdate", onUpdate)
    end
  elseif not on and anim then
    anim = false
    frame:SetScript("OnUpdate", nil)
    -- (4.1 INTEG) arrêt de l'animation = l'endroit où tu t'es arrêté : le coup d'œil suivant ne croit pas « tu as
    -- bougé » (sans UnitPosition, le pas d'animation ne tient pas cette position à jour) et ne relance rien au repos
    local py, px, f = glancePos()
    if py then lastPy, lastPx = py, px end
    if f then lastFacing = f end
    traceRate()
  end
  return anim
end
function M.Animating() return anim end
function M.SetMoving(kind, on)
  if kind == "turn" then turning = on and true or false else moving = on and true or false end
  if on then M.SetAnim(true) end
end

local function createFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Button", "TreeAICompassFrame", _G.UIParent)
  M._frame = frame
  frame:SetSize(W, H)
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
    local st = M.state or {}
    if mini and st.dup then
      -- (ZONEFIX) pourquoi la boussole s'efface : une autre flèche vise le même endroit, à 20 m près
      pcall(tt.AddLine, tt, M.DUP_TXT[st.dup] or "Une autre flèche te guide", 1, 0.82, 0.2)
      pcall(tt.AddLine, tt, "Elle vise le même endroit que la boussole (à moins de 20 m) : une seule flèche à l'écran.",
        0.85, 0.85, 0.85, true)
      pcall(tt.AddLine, tt, "Option : Boussole > « Une seule flèche » (décocher : les deux restent)", 0.6, 0.6, 0.6, true)
      pcall(tt.Show, tt)
      return
    end
    -- (UIREDO) ce que c'est · distance + temps · pourquoi · ce que fait chaque clic (une ligne chacun)
    local t = M.Current()
    if t then
      pcall(tt.AddLine, tt, t.title or t.label or "Cible", 1, 0.82, 0.2, true)
      if t.npc then pcall(tt.AddLine, tt, t.npc, 0.85, 0.85, 0.85) end
      if st.dist then
        pcall(tt.AddLine, tt, (st.approx and "≈ " or "") .. G.Dist(st.dist, M.db.units) .. (st.eta and (" · " .. st.eta) or "")
          .. (st.spf and " (chemin réel)" or " à pied"), 0.6, 0.8, 1)
      end
      if t.plan then pcall(tt.AddLine, tt, "Arrêt 1 du trajet (la carte « Prochaine étape » dit le même)", 0.85, 0.85, 0.85, true)
      elseif t.kind == "custom" and t.source ~= "corpse" then
        pcall(tt.AddLine, tt, "Point choisi à la main : la boussole revient au trajet une fois atteint", 0.85, 0.85, 0.85, true)
      end
      if t.why then pcall(tt.AddLine, tt, t.why, 0.85, 0.85, 0.85, true) end
    else
      pcall(tt.AddLine, tt, "Boussole TreeAI : pas de cible", 1, 0.82, 0.2)
    end
    pcall(tt.AddLine, tt, "Clic : cible suivante", 0.4, 0.8, 1)
    pcall(tt.AddLine, tt, "Clic droit : cible précédente", 0.4, 0.8, 1)
    pcall(tt.AddLine, tt, "Maj-clic : y aller (point sur la carte)", 0.4, 0.8, 1)
    pcall(tt.AddLine, tt, "Molette : taille · glisser : déplacer", 0.6, 0.6, 0.6)
    if T.AutoRunHint then pcall(tt.AddLine, tt, T.AutoRunHint(), 0.55, 0.55, 0.55, true) end
    pcall(tt.Show, tt)
  end)
  frame:SetScript("OnLeave", function() if _G.GameTooltip then pcall(_G.GameTooltip.Hide, _G.GameTooltip) end end)
  local bg = frame:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(frame)
  bg:SetColorTexture(0, 0, 0, 0.3)
  ring = frame:CreateTexture(nil, "BORDER")
  ring:SetSize(70, 70)
  ring:SetPoint("TOP", frame, "TOP", 0, -2)
  ring:SetTexture(RING)
  ring:SetVertexColor(0.3, 1, 0.4, 0.45)
  ring:Hide()
  local rg = ring.CreateAnimationGroup and ring:CreateAnimationGroup()
  if rg then                                         -- pulsation jouée par le client (aucun Lua par image)
    local a = rg:CreateAnimation("Alpha")
    if a and a.SetFromAlpha then a:SetFromAlpha(0.25) a:SetToAlpha(0.9) end
    if a and a.SetDuration then a:SetDuration(0.6) end
    local s = rg:CreateAnimation("Scale")
    if s and s.SetScaleFrom then s:SetScaleFrom(0.85, 0.85) s:SetScaleTo(1.08, 1.08)
    elseif s and s.SetScale then s:SetScale(1.25, 1.25) end
    if s and s.SetDuration then s:SetDuration(0.6) end
    if rg.SetLooping then rg:SetLooping("BOUNCE") end
    ring.ag = rg
  end
  shadow = frame:CreateTexture(nil, "ARTWORK")
  shadow:SetSize(66, 66)
  shadow:SetPoint("TOP", frame, "TOP", 2, -6)
  shadow:SetTexture(ARROW)
  shadow:SetVertexColor(0, 0, 0, 0.55)
  arrow = frame:CreateTexture(nil, "OVERLAY")
  arrow:SetSize(64, 64)
  arrow:SetPoint("TOP", frame, "TOP", 0, -4)
  arrow:SetTexture(ARROW)
  check = frame:CreateTexture(nil, "OVERLAY")
  check:SetSize(36, 36)
  check:SetPoint("CENTER", ring, "CENTER")
  check:SetTexture(CHECK)
  check:Hide()
  tag = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  tag:SetPoint("TOP", frame, "TOP", 0, -50)
  dist = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightLarge")
  dist:SetPoint("TOP", frame, "TOP", 0, -70)
  dist:SetWidth(W - 4)
  label = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  label:SetPoint("TOP", dist, "BOTTOM", 0, -2)
  label:SetWidth(W - 8)
  if label.SetWordWrap then label:SetWordWrap(false) end
  do  -- thème commun (visuel seulement)
    local Th = T and T.Theme
    if Th then Th:Skin(frame, "hud", { bg = bg }); Th:Font(label, "S", "gold"); Th:Font(dist, "L") end
  end
  if dist.SetFont then pcall(dist.SetFont, dist, "Fonts\\FRIZQT__.TTF", 16, "OUTLINE") end
  if tag.SetFont then pcall(tag.SetFont, tag, "Fonts\\FRIZQT__.TTF", 11, "OUTLINE") end
  frame:SetScale(tonumber(M.db.scale) or 1)
  frame:SetScript("OnShow", function() if not mini then M.SetAnim(true) end end)
  return frame
end
M.CreateFrame = createFrame
--- (tests, Horizon) les éléments du cadre : flèche, distance, étape, étiquette
function M.Widgets() return { frame = frame, arrow = arrow, dist = dist, label = label, tag = tag, mini = mini } end

-- TreeAI est le seul guide par défaut (choix du joueur : « SPF m'induit en erreur ») : Shortest Path ne sert —
-- durées ET effacement de la boussole — que si le joueur a choisi « Guidage : Suivi Forever » (réglage commun
-- `settings.guide == "spf"`, ou arbitre `T:GuideOwner()` s'il existe).
function M.SPFOptIn()
  if T.GuideOwner then
    local o = try(T.GuideOwner, T)
    if o ~= nil then return o == "spf" end
  end
  if T.GuideMode then return T:GuideMode() == "auto" end   -- Core : "auto" = Suivi Forever choisi, "treeai" = défaut
  local s = T.settings
  return type(s) == "table" and (s.guide == "spf" or s.guide == "auto") or false
end
function M.SPFGuiding()
  if not M.SPFOptIn() then return false end
  local A = M.SPF()
  if not (A and type(A.Active) == "function") then return false end
  return spfCall(A, "Active") == true
end

-- (ZONEFIX) la boussole s'efface pour une autre flèche sur la même cible : une ligne courte à sa place (le pourquoi)
local function showMini(text)
  if not mini then
    mini = true
    M.SetAnim(false)
    arrow:Hide() shadow:Hide() ring:Hide() check:Hide() dist:Hide() label:Hide()
    if ring.ag and ring.ag.Stop then ring.ag:Stop() end
    frame:SetHeight(22)
    tag:ClearAllPoints()
    tag:SetPoint("CENTER", frame, "CENTER", 0, 0)
    tag:Show()
    lastTag = nil
  end
  setTag(text, 0.8, 0.8, 0.8)
  if not frame:IsShown() then frame:Show() end
end
local function leaveMini()
  if not mini then return end
  mini = false
  frame:SetHeight(H)
  tag:ClearAllPoints()
  tag:SetPoint("TOP", frame, "TOP", 0, -50)
  dist:Show() label:Show()
  lastTag, lastR = nil, nil
  setTag(nil)
end
-- combat : la flèche seule (ni distance, ni étape, ni « Derrière toi »)
local function setTextOff(off)
  if off == textOff then return end
  textOff = off
  if off then dist:Hide() label:Hide() tag:Hide()
  else dist:Show() label:Show() tag:Show(); lastInfo, lastLabel, lastTag = nil, nil, nil end
end
local function hideAll(why)
  M.hideWhy = why
  if mini then leaveMini() end
  if frame:IsShown() then frame:Hide() end
  if ring.ag and ring.ag.Stop then ring.ag:Stop() end
  M.SetAnim(false)
end

local OFF = { none = true, hidden = true, off = true }
local function refresh()
  local want = M.db and M.db.shown ~= false and M:IsEnabled()
  local Hz = T.Horizon
  local wantHz = Hz and Hz.db and Hz.db.shown ~= false and Hz:IsEnabled()
  local fight = inCombat()
  local hideFight = fight and M.db and M.db.hideCombat == true
  -- (NAV) rien à montrer (boussole et bande coupées, ou combat avec l'option cochée) : aucun calcul, aucune lecture
  if frame and (hideFight or not (want or wantHz)) then
    hideAll(hideFight and "combat" or "off")
    return OFF
  end
  local st = M.Compute()
  M.state = st
  if not frame then return st end
  local visible = want and not (st.hidden or st.none)
  -- course au corps (DATA, Speed) : la cible « corps » passe avant tout (itinéraire, autre guide, Shortest Path)
  local cc = M.char and M.char.custom
  st.corpse = (not st.none and st.target and st.target.kind == "custom" and type(cc) == "table" and cc.source == "corpse") or nil
  if visible or wantHz then
    -- mort (pas encore libéré) : la fenêtre de mort de Blizzard reste lisible ; fantôme : seulement la flèche du corps
    local dg = _ns(try(_G.UnitIsDeadOrGhost, "player")) and true or nil   -- un seul appel (pas tout l'état)
    local ghost = dg and _ns(try(_G.UnitIsGhost, "player")) and true or nil
    st.hiddenDead = (dg and not (ghost and st.corpse)) or nil
    if dg then M.ClearRouteOnDeath() end
    st.dup = nil
    if M.ownWP and M.SPFOptIn() and M.SPFGuiding() then M.DropOwnWaypoint() end   -- une seule flèche : celle de SPF
    if not (st.hiddenDead or st.none or st.corpse) then
      st.dup = (M.db.hideDup ~= false) and M.OtherGuide(st.target) or nil
      if not st.dup and M.SPFGuiding() then st.dup = "spf" end
    end
  end
  if not want or st.hidden or st.none or st.hiddenDead or (st.dup and fight) then
    hideAll((not want and "off") or (st.hiddenDead and "dead") or (st.hidden and "pos") or (st.none and "none") or "dup")
    return st
  end
  if st.dup then
    M.hideWhy = "dup"
    showMini(M.DUP_TXT[st.dup] or "Une autre flèche te guide")
    return st
  end
  M.hideWhy = nil
  leaveMini()
  if not frame:IsShown() then frame:Show() end
  setTextOff(fight)
  local t = st.target
  local lt = M.ShortLabel(t.label or t.title or "Cible")
  if lt ~= lastLabel then lastLabel = lt; label:SetText(lt) end
  if st.arrived then
    arrow:Hide() shadow:Hide()
    ring:Show() check:Show()
    if ring.ag and ring.ag.Play and not (ring.ag.IsPlaying and ring.ag:IsPlaying()) then ring.ag:Play() end
    if not textOff then setTag(t.kind == "objective" and "Dans la zone" or "Arrivé", 0.3, 1, 0.4) end
  else
    if ring:IsShown() then ring:Hide() check:Hide() if ring.ag and ring.ag.Stop then ring.ag:Stop() end end
    if st.rel then
      arrow:Show() shadow:Show()
      if not shown then shown = st.rel end
      paint(shown)
      if lastTag == "Arrivé" or lastTag == "Dans la zone" then setTag(nil) end
      -- la flèche pas encore sur sa cible (nouvelle cible, tu as tourné) : l'animation repart
      if abs(G.Shortest(shown, st.rel)) >= 0.004 then M.SetAnim(true) end
    else
      arrow:Hide() shadow:Hide()
    end
  end
  local s
  if st.dist then
    s = (st.approx and "≈" or "") .. G.Dist(st.dist, M.db.units)
    if st.eta and not st.arrived then s = s .. "  ·  " .. st.eta end
    if not st.facing then s = s .. " " .. G.Cardinal(st.bearing) end
  else
    -- (QA-QUEST F4) « Pierre de foyer vers Orgrimmar » : pas de flèche, la consigne à la place de la distance
    s = t.note or (t.zone and ("Zone : " .. cut(t.zone, 28))) or ""
  end
  if s ~= lastInfo then lastInfo, lastQ = s, nil; dist:SetText(s) end
  return st
end
-- (journal) boussole affichée / cachée / effacée en ligne courte, et pourquoi (sur un changement seulement)
local VIS_TXT = { shown = "affichée", off = "cachée : coupée (option ou module)",
  combat = "cachée en combat (option « Cacher en combat »)", dead = "cachée : mort (la fenêtre du jeu reste lisible)",
  pos = "cachée : pas de position (instance, chargement, taxi)", none = "cachée : aucune cible" }
local function traceVis(st)
  local why = M.hideWhy or "shown"
  if why == "dup" then why = "dup:" .. tostring(type(st) == "table" and st.dup or "?") end
  if why == M._traceVis then return end
  M._traceVis = why
  local NTr = T.NavTrace
  if not (NTr and NTr.Log) then return end
  local txt = VIS_TXT[why]
  if not txt then
    local d = type(st) == "table" and st.dup
    txt = "en ligne courte : " .. ((d and M.DUP_TXT[d]) or "une autre flèche te guide") .. " (même endroit à 20 m près)"
  end
  NTr.Log("boussole", txt)
end
function M.Refresh()
  M._inRefresh = true
  local ok, st = pcall(refresh)
  M._inRefresh = false
  if not ok then error(st, 0) end
  traceVis(st)
  local Hz = T.Horizon                               -- bande d'horizon : suit la boussole (même règle d'un seul guide)
  if Hz and Hz.Sync then pcall(Hz.Sync, st) end
  return st
end

--- (ZONEFIX) coup d'œil d'une seconde quand rien n'anime la flèche : as-tu bougé / tourné depuis ?
function M.Glance()
  if not (frame and frame:IsShown()) or anim or mini then return false end
  local py, px, f = glancePos()
  if (py and (py ~= lastPy or px ~= lastPx)) or (f and f ~= lastFacing) then
    -- (ZONEFIX) l'orientation vue ici aussi : à l'arrivée (flèche cachée), le pas d'animation ne la relit pas, et
    -- un coup d'œil croyait « tu as tourné » à chaque seconde (calcul complet + animation pour rien)
    lastPy, lastPx, lastFacing = py, px, f or lastFacing
    M.Refresh()
    M.SetAnim(true)
    return true
  end
  return false
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
      distText = t.dist and G.Dist(t.dist, units) or nil, eta = st.eta or (t.dist and G.ETA(t.dist, M.Speed())) or nil } or nil,
  }
end

-- ------------------------------------------------------------------ cycle de vie
local function horizonWanted()
  local Hz = T.Horizon
  return Hz and Hz.db and Hz.db.shown ~= false and Hz:IsEnabled() and true or false
end
local pending
local function rebuildSoon(reason)
  if pending then return end
  pending = true
  T:After(0.7, function() pending = false pcall(M.Rebuild, reason) end)
end
M.RebuildSoon = rebuildSoon

function M:OnEnable()
  M.ForgetSaved()                                    -- (QA-QUEST F3) rien de repris d'avant le /reload sans un clic
  -- (ZONEFIX) une fois : la boussole reste en combat (flèche seule) ; avant, l'option « Cacher en combat » était
  -- cochée d'office et la boussole « disparaissait de temps en temps » (à chaque combat)
  if M.db and not M.db.combatV then M.db.combatV, M.db.hideCombat = 1, false end
  createFrame()
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_WATCH_LIST_CHANGED", "QUEST_TURNED_IN", "QUEST_ACCEPTED",
    "QUEST_REMOVED", "ZONE_CHANGED_NEW_AREA", "SUPER_TRACKING_CHANGED" }) do
    if ev == "ZONE_CHANGED_NEW_AREA" then
      -- (QA-QUEST F3) nouvelle zone : un point posé à la main sur un autre continent ne guide plus
      self:RegisterEvent(ev, function() pcall(M.DropUnreachable) rebuildSoon() end)
    else
      self:RegisterEvent(ev, function() rebuildSoon() end)
    end
  end
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() rebuildSoon("init") M.Refresh() end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if M.dirty then rebuildSoon() end M.Refresh() end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() M.Refresh() end)
  -- (ZONEFIX) l'animation suit tes mouvements (rien par image à l'arrêt) ; un autre repère posé / retiré = revu
  for ev, v in pairs({ PLAYER_STARTED_MOVING = { "move", true }, PLAYER_STOPPED_MOVING = { "move", false },
    PLAYER_STARTED_TURNING = { "turn", true }, PLAYER_STOPPED_TURNING = { "turn", false },
    PLAYER_STARTED_LOOKING = { "turn", true }, PLAYER_STOPPED_LOOKING = { "turn", false } }) do
    self:RegisterEvent(ev, function() M.SetMoving(v[1], v[2]) end)
  end
  self:RegisterEvent("USER_WAYPOINT_UPDATED", function() M.Refresh() end)
  self:On("PLAN_BUILT", function() if not inCombat() then pcall(M.Rebuild, "plan") end end)
  -- mort / relevé : cacher tout de suite (fenêtre de mort lisible), réafficher une fois vivant
  for _, ev in ipairs({ "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST" }) do
    self:RegisterEvent(ev, function()
      if T.InvalidatePlayerState then T:InvalidatePlayerState() end
      if ev == "PLAYER_DEAD" then M.ClearRouteOnDeath() end
      M.Refresh()
    end)
  end
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  -- Shortest Path commence / arrête de guider : la boussole s'efface / revient tout de suite (une seule flèche)
  local A = M.SPF()
  if A and type(A.Subscribe) == "function" and not M._spfSub then
    M._spfSub = spfCall(A, "Subscribe", function() if frame then pcall(M.Refresh) end end) or true
  end
  self:On("QUESTS_UPDATED", function() rebuildSoon() end)
  -- ticker (1 s) : immobile et affichée → un coup d'œil (as-tu bougé ?) ; effacée pour une raison qui peut changer
  -- seule (autre flèche, pas de position, pas de cible) → revue toutes les 2 s ; coupée, morte, en instance, en combat
  -- (option) → rien (les évènements la réveillent) ; cibles revues toutes les 10 s quand elle est à l'écran et que tu
  -- as bougé, sinon toutes les 60 s (le journal, le trajet, un point posé la refont tout de suite, par évènement)
  if not M.ticker then
    local n = 0
    local SOFT = { dup = true, pos = true, none = true }
    M.ticker = T:NewTicker(1, function()
      n = n + 1
      if frame and frame:IsShown() and not M.hideWhy then M.Glance()
      elseif frame and SOFT[M.hideWhy or ""] and n % 2 == 0 then M.Refresh()
      elseif frame and M.hideWhy == "off" and horizonWanted() then M.Refresh() end   -- la bande seule : elle suit
      if n % 10 == 0 and frame and (frame:IsShown() or M.hideWhy == "none") and not inCombat() then
        local py, px = lastPy, lastPx
        if M.hideWhy == "none" or n % 60 == 0 or py ~= M._rbPy or px ~= M._rbPx then
          M._rbPy, M._rbPx = py, px
          pcall(M.Rebuild)
        end
      end
    end)
  end
  -- (PERF4, si le cœur l'offre) réveil dès que tu te remets à bouger : la flèche repart sans attendre le coup d'œil
  if T.OnMotion then T:OnMotion("Compass", function() if frame and frame:IsShown() then M.Glance() end end) end
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
  local w = x and M.SetWaypoint(x, y, name, nil, "perso")
  if w then
    M:Print(("Point perso : %.1f, %.1f%s"):format(w.x * 100, w.y * 100, w.name and (" (" .. w.name .. ")") or ""))
  else
    M:Print("Usage : /tai way 45.2 60.1 [nom] (coordonnées de ta carte actuelle) · /tai way effacer")
  end
end, "point perso pour la boussole : /tai way x y [nom]")

-- toolbar button of the panel (UI.lua toggleCompass): a real toggle, silent (the user clicks, no chat help)
function M.Toggle() M.Show(not (frame and frame:IsShown())) end

T:RegisterSlash("aller", function() M.GoTo(nil, true) end, "boussole : « Aller là » (Shortest Path / TomTom / repère de carte)")
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

-- (COHERE) /tai guidage [forever|treeai] : qui guide les quêtes à l'écran (aussi dans les options, « Démarrer »)
T:RegisterSlash("guidage", function(rest)
  rest = tostring(rest or ""):lower()
  local m = (rest:find("tree") and "treeai") or ((rest:find("forever") or rest:find("auto") or rest:find("spf")) and "auto")
    or (T:GuideMode() == "treeai" and "auto" or "treeai")
  T:SetGuideMode(m)
  M.Refresh()
  M:Print("Guidage : " .. (T.GUIDE_LABEL[m] or m) .. ".")
end, "guidage [forever|treeai] : Suivi Forever / Questie guident (TreeAI ne double pas) ou TreeAI guide")
