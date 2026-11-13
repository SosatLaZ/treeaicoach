-- TreeAI — Temps de trajet (agent NAV) : des durées justes, mesurées sur TES déplacements.
--  * TreeAI guide seul par défaut : distance à vol d'oiseau × détour de la zone ÷ ta vitesse.
--      - vitesse MESURÉE (déplacements de position / temps, hors combat, à pied / monté / à la nage séparés) ;
--        GetUnitSpeed n'est lu que hors combat (valeur « secrète » en combat sur Forever) et seulement comme
--        point de départ tant qu'on n'a pas 5 mesures ;
--      - (Shortest Path n'est consulté que si le joueur a choisi « Guidage : Suivi Forever ») ;
--      - détour APPRIS par zone à chaque arrivée : temps en mouvement × vitesse ÷ distance à vol d'oiseau.
--  * Chaque trajet de la boussole est chronométré : prévu (au départ) contre réel (à l'arrivée), l'écart est
--    gardé (30 derniers) → « Temps de trajet : ±6 % (14 trajets) ». Trajets interrompus (combat, longue pause,
--    changement de cible) écartés de la mesure.
-- Coût : un échantillon par seconde, seulement quand un trajet est en cours ou que tu bouges ; rien à l'arrêt.
-- Lecture seule, aucune fonction protégée, aucun cadre Blizzard. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local N = T:RegisterModule("NavTime", {
  title = "Temps de trajet",
  defaults = { enabled = true, spfCost = true },
  charDefaults = { mount = false, deaths = {} },
  options = {
    { key = "spfCost", type = "toggle", label = "Temps de trajet : lire l'estimation de Shortest Path Forever quand il est installé (lecture seule, jamais son guidage)" },
  },
})
T.NavTime = N

local floor, sqrt, abs, max, min = math.floor, math.sqrt, math.abs, math.max, math.min
-- (ROUTEFIX) monture des niveaux 30–40 = +60 % (7 × 1,6 = 11,2 yd/s) : l'ancien défaut 14 (+100 %) faisait des trajets
-- montés 25 % trop courts tant que la vitesse n'était pas mesurée
local DEFAULT_RUN, DEFAULT_MOUNT, DEFAULT_SWIM = 7, 11.2, 4.7
local PRIOR_DETOUR = 1.15          -- prairie ouverte ≈ 1,05 ; relief ≈ 1,3 : appris ensuite par zone
local MOUNT_LEVEL, MOUNT_UP = 30, 3  -- monture possible dès 30 sur Forever ; 3 s pour monter en selle
-- (ROUTEFIX) relief A PRIORI des zones (facteur de détour, jamais une coordonnée) : montagnes, mesas, cratères, villes
-- à étages ; remplacé dès 2 trajets mesurés dans la zone (N.Detour)
N.RELIEF = {
  [1442] = 1.45,  -- Serres-Rocheuses
  [1441] = 1.35,  -- Mille pointes (mesas, rampes)
  [1456] = 1.5,   -- Les Pitons-du-Tonnerre (ascenseurs, ponts)
  [1454] = 1.3,   -- Orgrimmar
  [1458] = 1.6,   -- Fossoyeuse
  [1455] = 1.3,   -- Forgefer
  [1416] = 1.4,   -- Montagnes d'Alterac
  [1425] = 1.35,  -- Les Hinterlands
  [1427] = 1.35,  -- Gorge des Vents brûlants
  [1418] = 1.3,   -- Terres ingrates
  [1428] = 1.25,  -- Steppes ardentes
  [1426] = 1.25,  -- Dun Morogh
  [1449] = 1.25,  -- Cratère d'Un'Goro
  [1444] = 1.25,  -- Féralas
  [1447] = 1.3,   -- Azshara
  [1452] = 1.3,   -- Berceau-de-l'Hiver
  [1434] = 1.3,   -- Strangleronce
  [1430] = 1.3,   -- Défilé de Deuillevent
  [1438] = 1.25,  -- Teldrassil
  [1440] = 1.2,   -- Orneval
  [1443] = 1.15,  -- Désolace
  [1413] = 1.08,  -- Les Tarides (plaine)
  [1412] = 1.12,  -- Mulgore
  [1411] = 1.15,  -- Durotar
  [1446] = 1.1,   -- Tanaris
  [1436] = 1.08,  -- Marche de l'Ouest
}
local ARRIVE_YD = 15
local MIN_SAMPLES = 5

local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return tryPass(pcall(fn, ...)) end
local function G(n) return rawget(_G, n) end
local function secret(v) local f = G("issecretvalue") return f and v ~= nil and f(v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function now() return num(try(G("GetTime"))) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end

-- ------------------------------------------------------------------ stockage (compte : vitesses + détours ; perso : journal)
local function store()
  local db = N.db
  if type(db) ~= "table" then return nil end
  if type(db.detour) ~= "table" then db.detour = {} end
  if type(db.speed) ~= "table" then db.speed = {} end
  if type(db.log) ~= "table" then db.log = {} end
  return db
end
N.Store = store

-- ------------------------------------------------------------------ position monde (yards) : UnitPosition sinon carte
function N.WorldPos()
  local y, x, _, inst = try(G("UnitPosition"), "player")
  y, x, inst = num(y), num(x), num(inst)
  if x and y then return inst or 0, y, x end
  local C = T.Compass
  if not (C and C.Player and C.Geo) then return nil end
  local me = C.Player()
  if not me then return nil end
  local c, wx, wy = C.Geo.World(me.mapID, me.x, me.y)
  if c then return c, wx, wy, me.mapID end
  return nil
end

local function mode()
  if try(G("IsMounted")) then return "m" end
  if try(G("IsSwimming")) then return "s" end
  return "w"
end
N.Mode = mode

-- ------------------------------------------------------------------ vitesse
-- moyenne mobile des mesures (yd/s) ; avant 5 mesures : vitesse de course du jeu (hors combat), sinon défaut
local lastRun = {}
function N.Speed(m)
  m = m or mode()
  local db = store()
  local e = db and db.speed[m]
  if type(e) == "table" and (e.n or 0) >= MIN_SAMPLES and (e.v or 0) > 1 then return e.v, "mesure" end
  if not inCombat() then
    local _, run = try(G("GetUnitSpeed"), "player")
    run = num(run)
    if run and run > 1 and run < 60 then lastRun[m] = run end
  end
  if lastRun[m] then return lastRun[m], "jeu" end
  return (m == "m" and DEFAULT_MOUNT) or (m == "s" and DEFAULT_SWIM) or DEFAULT_RUN, "défaut"
end

-- (ROUTEFIX) monture connue : vue (montée une fois, gardé par personnage), ou le journal des montures du jeu
local mountAt, mountV = -1e9, false
function N.MountKnown()
  if try(G("IsMounted")) then
    if N.char then N.char.mount = true end
    return true
  end
  if N.char and N.char.mount then return true end
  if now() - mountAt < 30 then return mountV end      -- le journal des montures relu au plus toutes les 30 s
  mountAt, mountV = now(), false
  local MJ = G("C_MountJournal")
  local ids = MJ and MJ.GetMountIDs and try(MJ.GetMountIDs)
  if type(ids) == "table" and MJ.GetMountInfoByID then
    for _, id in ipairs(ids) do
      local _, _, _, _, usable, _, _, _, _, _, collected = try(MJ.GetMountInfoByID, id)
      if collected and usable then
        if N.char then N.char.mount = true end
        mountV = true
        return true
      end
    end
  end
  return false
end
-- vitesse d'un trajet : monté si tu l'es, ou si tu as une monture (niveau ≥ 30) ; à pied sinon
function N.TravelMode()
  if try(G("IsMounted")) then return "m" end
  local lv = num(try(G("UnitLevel"), "player")) or 1
  if lv >= MOUNT_LEVEL and N.MountKnown() then return "m", true end
  return "w"
end
function N.TravelSpeed()
  local m = N.TravelMode()
  if m == "m" then
    local db = store()
    local e = db and db.speed.m
    if type(e) == "table" and (e.n or 0) >= MIN_SAMPLES and (e.v or 0) > 1 then return e.v end
    if try(G("IsMounted")) then return (N.Speed("m")) end
    return max(DEFAULT_MOUNT, N.Speed("w") * 1.6)
  end
  return (N.Speed("w"))
end
-- secondes pour monter en selle à chaque vrai trajet (monture connue, pas encore en selle), sinon nil
function N.MountUp()
  local m, later = N.TravelMode()
  return (m == "m" and later) and MOUNT_UP or nil
end

function N.LearnSpeed(m, v)
  local db = store()
  if not (db and v and v > 1 and v < 60) then return end
  local e = db.speed[m]
  if type(e) ~= "table" then e = { v = v, n = 0 }; db.speed[m] = e end
  e.n = min((e.n or 0) + 1, 1000)
  local k = e.n < 20 and (1 / e.n) or 0.05            -- moyenne simple au début, puis glissante
  e.v = e.v + (v - e.v) * k
end

-- ------------------------------------------------------------------ détour par zone
-- (5.1 ROUTE6, audit B3) d'où vient le facteur, dit à qui le demande (3e retour) : "spf" (étalonné sur les estimations
-- de Shortest Path Forever, ≥ 3 trajets de cette zone), "appris" (tes arrivées chronométrées, ≥ 2), "relief" (table a
-- priori de la zone), "défaut". Un seul facteur par zone pour TOUS les trajets comparés : l'ordre reste cohérent.
function N.Detour(mapID)
  local db = store()
  local sp = N.spf and mapID and N.spf.zone[tonumber(mapID) or -1]
  if sp and sp.n >= 3 and sp.s > 0 then return sp.s / sp.n, sp.n, "spf" end
  local e = db and mapID and db.detour[mapID]
  if type(e) == "table" and (e.n or 0) >= 2 and (e.s or 0) > 0 then return e.s / e.n, e.n, "appris" end
  local r = mapID and N.RELIEF[tonumber(mapID) or -1]
  return r or PRIOR_DETOUR, 0, r and "relief" or "défaut"
end
N.rev = 0                          -- (ROUTEFIX) change quand un détour est appris (le graphe du trajet est refait)
function N.LearnDetour(mapID, ratio)
  local db = store()
  ratio = tonumber(ratio)
  if not (db and mapID and ratio) then return end
  ratio = max(1, min(2.5, ratio))
  local e = db.detour[mapID]
  if type(e) ~= "table" then e = { s = 0, n = 0 }; db.detour[mapID] = e end
  e.s, e.n = e.s + ratio, e.n + 1
  if e.n > 20 then e.s, e.n = e.s * 20 / e.n, 20 end  -- récent d'abord
  N.rev = (N.rev or 0) + 1
end

-- ------------------------------------------------------------------ estimation a → b ({mapID, x, y} en 0..1)
-- → secondes, source ("spf" | "modèle"), distance à vol d'oiseau (yd)
function N.Model(yd, mapID, m)
  if not yd then return nil end
  local sp = m and N.Speed(m) or N.TravelSpeed()   -- (ROUTEFIX) la même vitesse que le trajet (Route)
  return yd * N.Detour(mapID) / sp
end
-- (ROUTEFIX) temps à pied / monté a → b ({mapID, x, y} en 0..1) : le chemin appris (PathLearn) si les deux bouts sont
-- sur des endroits parcourus, sinon le modèle — LA formule que la boussole et le trajet partagent ; → secondes, "appris"|nil
function N.Walk(a, b, yd)
  local PL = T.PathLearn
  if PL and PL.LegSecMap then
    local s = PL.LegSecMap(a, b, N.TravelSpeed())
    if s then return s, "appris" end
  end
  return N.Model(yd, b and b.mapID), nil
end
function N.Estimate(a, b)
  local C = T.Compass
  if not (a and b) then return nil end
  local yd = C and C.Geo and C.Geo.Vector and C.Geo.Vector(a, b)
  local sec = C and C.Travel and C.Travel(a, b)
  if sec then return sec, "spf", yd end
  if not yd then return nil end
  local w, how = N.Walk(a, b, yd)
  return w, how or "modèle", yd
end

-- ------------------------------------------------------------------ (5.1 ROUTE6, audit B3) Shortest Path Forever : son estimation
-- Le joueur a Shortest Path Forever. Son API publique donne un temps de trajet (ShortestPathForever.API.Estimate(carte, x, y,
-- carte, x, y), coordonnées 0..1 → secondes, ou nil + raison « combat » / « invalid » / « unreachable » ; lu dans son code :
-- appel « . », réponse gardée 5 s chez lui, « au plus un appel neuf par image »). TreeAI s'en sert SANS que le joueur ait
-- choisi « Guidage : Suivi Forever » — lecture seule, jamais son guidage, jamais sa flèche :
--  * au plus UN appel par image (T:Spread), hors combat, sous pcall ; une erreur Lua = plus aucun appel de la session ;
--  * réponses gardées par paire de cases de 100 yd (N.SPF_TTL s, N.SPF_MAX entrées) ;
--  * chaque réponse d'un trajet à pied étalonne le facteur de détour de SA zone (N.Detour → "spf") : tous les trajets de
--    la zone en profitent, sans demander les n² trajets d'un ordre ;
--  * le trajet affiché prend la réponse exacte quand elle est là (Route:StopsRaw).
-- Sans Shortest Path : chemins appris (PathLearn), puis le facteur de la zone. N.CostLines() dit ce qui a servi.
N.SPF_TTL, N.SPF_MAX, N.SPF_CELL = 900, 400, 100
N.spf = { cache = {}, n = 0, queue = {}, qn = 0, asked = 0, ok = 0, zone = {}, last = nil, bad = nil }
local function spfAPI()
  if N.spf.bad then return nil end
  if N.db and N.db.spfCost == false then return nil end
  local S = G("ShortestPathForever")
  local A = type(S) == "table" and S.API or nil
  if type(A) ~= "table" or type(A.Estimate) ~= "function" then return nil end
  return A
end
function N.SPFPresent() return spfAPI() ~= nil end
local function cellKey(Geo, p)
  local n, w, c = Geo.World(p.map, p.x, p.y)
  if not n then return nil end
  return c .. ":" .. floor(n / N.SPF_CELL) .. ":" .. floor(w / N.SPF_CELL), n, w, c
end
local function pairKey(a, b)
  local Geo = T.Geo
  if not (Geo and Geo.World and a and b and a.map and b.map and tonumber(a.x) and tonumber(b.x)) then return nil end
  local ka, an, aw, ac = cellKey(Geo, a)
  local kb, bn, bw, bc = cellKey(Geo, b)
  if not (ka and kb) then return nil end
  return ka .. ">" .. kb, (ac == bc) and sqrt((an - bn) ^ 2 + (aw - bw) ^ 2) or nil
end
local function spfRun()
  local sp = N.spf
  sp.running = false
  local q = sp.queue[1]
  if not q then return end
  if inCombat() then return end                          -- repris au prochain trajet demandé (rien n'insiste en combat)
  table.remove(sp.queue, 1)
  sp.qn = #sp.queue
  local A = spfAPI()
  if not A then sp.queue, sp.qn = {}, 0 return end
  sp.asked = sp.asked + 1
  local ok, sec, why = pcall(A.Estimate, q.a.map, q.a.x / 100, q.a.y / 100, q.b.map, q.b.x / 100, q.b.y / 100)
  if not ok then                                         -- l'API a changé : on n'insiste pas, et c'est dit dans le rapport
    sp.bad, sp.last = tostring(sec):sub(1, 120), "erreur"
    sp.queue, sp.qn = {}, 0
    return
  end
  sec = num(sec)
  local e = { at = now(), s = (sec and sec >= 0) and sec or false, d = q.d, why = (not sec) and tostring(why or "?") or nil }
  if not sp.cache[q.key] then sp.n = sp.n + 1 end
  sp.cache[q.key] = e
  sp.last = e.s and "ok" or (e.why or "?")
  if e.s then
    sp.ok = sp.ok + 1
    -- étalonnage : un trajet À PIED dans une seule zone (≥ 150 yd, plus lent qu'un vol) → rapport temps × vitesse / distance
    if q.d and q.d >= 150 and q.a.map == q.b.map then
      local ratio = e.s * N.TravelSpeed() / q.d
      if ratio >= 1 and ratio <= 3 then
        local z = sp.zone[q.a.map]
        if not z then z = { s = 0, n = 0 } sp.zone[q.a.map] = z end
        local before = z.n >= 3 and z.s / z.n or nil
        z.s, z.n = z.s + ratio, z.n + 1
        if z.n > 12 then z.s, z.n = z.s * 12 / z.n, 12 end
        local after = z.n >= 3 and z.s / z.n or nil
        -- le facteur de la zone vient de changer pour de bon (≥ 5 %) : les trajets sont recomptés (graphe refait en tranches)
        if after and (not before or abs(after - before) / before >= 0.05) then N.rev = (N.rev or 0) + 1 end
      end
    end
  end
  if sp.n > N.SPF_MAX then                               -- borne : les plus vieilles réponses partent
    local t = now()
    for k, v in pairs(sp.cache) do if t - v.at > N.SPF_TTL / 2 then sp.cache[k] = nil sp.n = sp.n - 1 end end
    if sp.n > N.SPF_MAX then sp.cache, sp.n = {}, 0 end
  end
  if sp.queue[1] and T.Spread then sp.running = true T:Spread(N, spfRun, nil, nil, nil, nil, "NavTime:spf") end
end
--- demande (sans attendre) l'estimation a → b ({ map, x, y } en %) ; rien si elle est connue, en file, ou sans Shortest Path
function N.SPFAsk(a, b)
  local sp = N.spf
  if not spfAPI() or sp.qn >= 40 then return false end
  local key, d = pairKey(a, b)
  if not key then return false end
  local e = sp.cache[key]
  if e and now() - e.at < N.SPF_TTL then return false end
  for i = 1, sp.qn do if sp.queue[i].key == key then return false end end
  sp.queue[sp.qn + 1] = { key = key, d = d, a = { map = a.map, x = a.x, y = a.y }, b = { map = b.map, x = b.x, y = b.y } }
  sp.qn = sp.qn + 1
  if not sp.running and T.Spread then sp.running = true T:Spread(N, spfRun, nil, nil, nil, nil, "NavTime:spf") end
  return true
end
--- l'estimation gardée de Shortest Path pour a → b → secondes, ou nil (inconnue, inaccessible, pas de Shortest Path).
--- Jamais d'appel ici (lecture du cache) ; corrigée de l'écart entre ces points et ceux demandés dans les mêmes cases.
function N.SPFSec(a, b)
  local sp = N.spf
  if sp.n == 0 then return nil end
  local key, d = pairKey(a, b)
  local e = key and sp.cache[key]
  if not (e and e.s and now() - e.at < N.SPF_TTL) then return nil end
  if d and e.d then return max(0, e.s + (d - e.d) / N.TravelSpeed()) end
  return e.s
end

-- ------------------------------------------------------------------ (5.1 ROUTE6, audit B3) là où tu es mort
-- Aucune donnée inventée : seulement TES morts. Une case de 100 yd où tu es mort DEUX fois (ou plus) à un niveau encore
-- proche du tien (≤ 2 niveaux de moins) est « dangereuse » : les trajets qui y arrivent ou la traversent coûtent
-- N.DANGER_S secondes de plus dans l'ORDRE des arrêts (jamais dans le temps affiché), et le trajet le dit.
N.DANGER_S, N.DANGER_MAX, N.DANGER_N = 120, 150, 2
N.dangerRev = 0
local function deaths()
  local c = N.char
  if type(c) ~= "table" then return nil end
  if type(c.deaths) ~= "table" then c.deaths = {} end
  return c.deaths
end
local function dkey(c, n, w) return c .. ":" .. floor(n / 100) .. ":" .. floor(w / 100) end
--- mort ici (PLAYER_DEAD) : la case est notée ; à la 2e, les trajets sont recomptés
function N.NoteDeath()
  local D = deaths()
  local c, n, w = N.WorldPos()
  if not (D and c and n and w) then return nil end
  if try(G("IsInInstance")) then return nil end           -- un donjon n'est pas un trajet
  local k = dkey(c, n, w)
  local lv = num(try(G("UnitLevel"), "player")) or 1
  local e = D[k]
  if type(e) ~= "table" then e = { n = 0 } D[k] = e end
  e.n, e.lv, e.at = (tonumber(e.n) or 0) + 1, lv, num(try(G("time"))) or 0
  local cnt, oldK, oldAt = 0, nil, nil
  for kk, v in pairs(D) do
    cnt = cnt + 1
    if not oldAt or (tonumber(v.at) or 0) < oldAt then oldK, oldAt = kk, tonumber(v.at) or 0 end
  end
  if cnt > N.DANGER_MAX and oldK and oldK ~= k then D[oldK] = nil end
  if e.n >= N.DANGER_N then
    N.dangerRev = N.dangerRev + 1
    local NTr = T.NavTrace
    if NTr and NTr.Log then pcall(NTr.Log, "danger", ("%d morts dans la même case (niveau %d) : zone dangereuse, trajets recomptés"):format(e.n, lv)) end
    local Zp = T.ZonePlan
    if Zp and Zp.Bump and Zp.IsEnabled and Zp:IsEnabled() then pcall(Zp.Bump, Zp, "zone dangereuse") end
  end
  return e.n
end
--- nombre de morts retenues pour la case d'un point monde (c, n, w) : ≥ N.DANGER_N et encore de ton niveau, sinon nil
function N.DangerAt(c, n, w)
  local D = N.char and N.char.deaths
  if type(D) ~= "table" or not (c and n and w) then return nil end
  local e = D[dkey(c, n, w)]
  if type(e) ~= "table" or (tonumber(e.n) or 0) < N.DANGER_N then return nil end
  local lv = num(try(G("UnitLevel"), "player")) or 1
  if lv - (tonumber(e.lv) or lv) > 2 then return nil end   -- 3 niveaux de plus : ce n'est plus un danger
  return e.n
end
--- secondes de pénalité d'un trajet a → b (points monde { c, n, w }) : il arrive dans une case dangereuse ou en traverse
function N.DangerPenalty(a, b)
  local D = N.char and N.char.deaths
  if type(D) ~= "table" or not next(D) or not (a and b and a.c and a.c == b.c) then return 0 end
  local d = sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2)
  local steps = min(40, max(1, floor(d / 70)))
  local seen, pen = nil, 0
  for i = 1, steps do
    local t = i / steps
    local n, w = a.n + (b.n - a.n) * t, a.w + (b.w - a.w) * t
    local k = dkey(a.c, n, w)
    if k ~= seen then
      seen = k
      if N.DangerAt(a.c, n, w) then pen = pen + N.DANGER_S if pen >= 2 * N.DANGER_S then break end end
    end
  end
  return pen
end
--- point de carte { map, x, y } (%) dans une case dangereuse → nombre de morts ou nil
function N.DangerAtMap(p)
  local Geo = T.Geo
  if not (N.char and type(N.char.deaths) == "table" and next(N.char.deaths) and Geo and Geo.World and p and p.map and p.x) then return nil end
  local n, w, c = Geo.World(p.map, p.x, p.y)
  if not n then return nil end
  return N.DangerAt(c, n, w)
end

-- ------------------------------------------------------------------ (5.1 ROUTE6) sonde : d'où viennent les temps de trajet
N.served = { spf = 0, appris = 0, modele = 0 }
function N.Count(src) N.served[src] = (N.served[src] or 0) + 1 end
--- lignes pour le rapport d'erreurs (/tai erreurs, via le journal du trajet)
function N.CostLines()
  local sp = N.spf
  local S = G("ShortestPathForever")
  local out = {}
  local present = type(S) == "table" and type(S.API) == "table" and type(S.API.Estimate) == "function"
  out[#out + 1] = ("== Coût des trajets : Shortest Path %s%s"):format(present and "présent" or "absent",
    present and (" · Estimate : %d demande%s, %d réponse%s, %d en cache, %d en file · dernier : %s%s"):format(sp.asked, sp.asked > 1 and "s" or "",
      sp.ok, sp.ok > 1 and "s" or "", sp.n, sp.qn, tostring(sp.last or "jamais appelé"), sp.bad and (" · COUPÉ (erreur : " .. sp.bad .. ")") or "") or "")
  local PL = T.PathLearn
  local plOn = PL and PL.IsEnabled and PL:IsEnabled() or false
  local cells = 0
  if plOn and PL.Count then for c = 0, 1 do cells = cells + (tonumber((try(PL.Count, c))) or 0) end end
  out[#out + 1] = ("  chemins appris : %s%s · trajets comptés : %d par Shortest Path, %d par un chemin appris, %d par le facteur de zone"):format(
    plOn and "allumés" or "coupés", plOn and (" (" .. cells .. " cases)") or "", N.served.spf or 0, N.served.appris or 0, N.served.modele or 0)
  local map = num(try(G("C_Map") and G("C_Map").GetBestMapForUnit, "player"))
  if map then
    local v, n, src = N.Detour(map)
    out[#out + 1] = ("  facteur de détour de ta zone (%s) : %.2f — source %s%s · vitesse %.1f yd/s (%s)"):format(tostring(map), v, tostring(src),
      (n or 0) > 0 and (" (" .. n .. " mesures)") or "", (N.Speed("w")), select(2, N.Speed("w")))
  end
  local dn = 0
  for _, e in pairs(N.char and type(N.char.deaths) == "table" and N.char.deaths or {}) do if (tonumber(e.n) or 0) >= N.DANGER_N then dn = dn + 1 end end
  out[#out + 1] = ("  cases dangereuses (≥ %d morts) : %d · ascenseurs / tram : %d ligne(s) mesurée(s) (aucune position inventée)"):format(N.DANGER_N, dn,
    #((T.Data and T.Data.Travel and T.Data.Travel.lifts) or {}))
  local Xm = T.Export
  if Xm and Xm.AreaSources then out[#out + 1] = "  " .. tostring((try(Xm.AreaSources))) end
  return out
end

-- ------------------------------------------------------------------ chronométrage d'un trajet
-- trip = { key, target, t0, crow0, pred, src, map, mode, moving, idle, combat, lastD }
N.trip = nil
function N.StartTrip(target, me)
  if not (target and target.mapID and target.x and me) then N.trip = nil return nil end
  local pred, src, yd = N.Estimate(me, target)
  if not (pred and yd) or yd < 40 then N.trip = nil return nil end     -- trop court pour mesurer quoi que ce soit
  N.trip = { key = target.key or (target.mapID .. ":" .. target.x), target = target, t0 = now(), crow0 = yd,
    pred = pred, src = src, map = me.mapID, mode = mode(), moving = 0, idle = 0, combat = false, lastD = yd,
    minD = yd }
  N.Wake()
  return N.trip
end

-- appelé par la boussole à chaque calcul complet (≈ 1 s) : même cible ? arrivé ?
function N.Track(target, me, dist)
  local tr = N.trip
  if not (target and target.mapID) then N.trip = nil return nil end
  local key = target.key or (target.mapID .. ":" .. target.x)
  if not tr or tr.key ~= key then return N.StartTrip(target, me) end
  if not dist then return tr end
  tr.lastD, tr.minD = dist, min(tr.minD, dist)
  if dist <= (target.arrive or ARRIVE_YD) then return N.Arrive(tr) end
  if mode() ~= tr.mode then tr.combat = true end      -- monté / descendu en route : prévision faussée, pas de calibrage
  if dist > tr.crow0 * 1.6 + 100 or now() - tr.t0 > 1800 then N.trip = nil end   -- autre direction / abandon
  return tr
end

local function cutZone(z) z = tostring(z or "?") return (T.Cut and T.Cut(z, 28)) or z end
function N.Arrive(tr)
  tr = tr or N.trip
  N.trip = nil
  if not tr then return nil end
  local actual = now() - tr.t0
  if actual < 5 then return nil end
  -- trajet propre : ni combat, ni longue pause (≤ 10 s et ≤ 20 % du temps)
  local clean = not tr.combat and tr.idle <= max(10, 0.2 * actual)
  local entry = { d = floor(tr.crow0 + 0.5), p = floor(tr.pred * 10 + 0.5) / 10, a = floor(actual * 10 + 0.5) / 10,
    s = tr.src, m = tr.map, mo = tr.mode, ok = clean or nil }
  entry.e = floor((tr.pred - actual) / actual * 1000 + 0.5) / 10      -- % (positif = on prévoyait trop long)
  local db = store()
  if db then
    db.log[#db.log + 1] = entry
    while #db.log > 30 do table.remove(db.log, 1) end
  end
  if clean and tr.moving > 3 and tr.mode ~= "s" then
    local sp = N.Speed(tr.mode)
    N.LearnDetour(tr.map, tr.moving * sp / tr.crow0)
  end
  N.last = entry
  -- (ZONEFIX) journal de la boussole : prévu contre réel, pour chaque trajet chronométré
  local NTr = T.NavTrace
  if NTr and NTr.Log then
    local Zp = T.ZonePlan
    local zone = (Zp and Zp.MapName and Zp.MapName(tr.map)) or tostring(tr.map or "?")
    local over = floor((actual - tr.pred) / max(1, tr.pred) * 100 + 0.5)       -- + = plus long que prévu
    NTr.Log("temps", ("trajet de %d m : prévu %s, réel %s (%+d %%) · %s · %s%s%s"):format(floor(tr.crow0 * 0.9144 + 0.5),
      NTr.Sec(tr.pred), NTr.Sec(actual), over, cutZone(zone),
      (tr.mode == "m" and "monté") or (tr.mode == "s" and "à la nage") or "à pied",
      tr.src == "spf" and " · Suivi Forever" or "", clean and "" or " (gêné : combat / pause)"))
  end
  if T.Fire then pcall(T.Fire, T, "NAV_ARRIVED", entry) end
  return entry
end

-- précision moyenne (écart absolu %) sur les trajets propres ; src = "spf" | "modèle" | nil (tous)
function N.Accuracy(src)
  local db = store()
  local s, n = 0, 0
  for _, e in ipairs(db and db.log or {}) do
    if e.ok and (not src or e.s == src) and tonumber(e.e) then s, n = s + abs(e.e), n + 1 end
  end
  if n == 0 then return nil, 0 end
  return s / n, n
end
function N.AccuracyText()
  local a, n = N.Accuracy()
  if not a then return nil end
  return ("Temps de trajet : ±%d %% (%d trajet%s mesuré%s)"):format(floor(a + 0.5), n, n > 1 and "s" or "", n > 1 and "s" or "")
end

-- ------------------------------------------------------------------ échantillonneur (1 Hz, seulement utile)
local sampler, pc, px, py, pt = nil, nil, nil, nil, nil       -- dernier échantillon (pas de table par appel)
local function forget() pc = nil end
function N.Sample()
  local tnow = now()
  local c, wx, wy = N.WorldPos()
  local fight = inCombat()
  local taxi = try(G("UnitOnTaxi"), "player")
  local tr = N.trip
  local ps = T.PlayerState and T:PlayerState()
  local gone = ps and (ps.dead or ps.ghost)
  if tr and (fight or taxi or gone) then tr.combat = true end
  if not c or taxi then pc = nil return nil end
  -- (4.2 ROUTE42) même échantillon pour le trajet : quai atteint (départ du bateau vu), cage d'ascenseur, repère natif
  local Rt = T.Route
  if Rt and Rt.NoteSample and not gone then pcall(Rt.NoteSample, c, wx, wy, tnow) end
  local oc, ox, oy, ot = pc, px, py, pt
  pc, px, py, pt = c, wx, wy, tnow
  if not oc or oc ~= c then return nil end
  local dt = tnow - ot
  if dt < 0.5 or dt > 3 then return nil end
  local d = sqrt((wx - ox) ^ 2 + (wy - oy) ^ 2)
  local v = d / dt
  local m = mode()
  if v < 1 then
    if tr then tr.idle = tr.idle + dt end
    return 0
  end
  if v > 60 then return nil end                       -- téléport / foyer
  if tr then tr.moving = tr.moving + dt end
  if not fight and not gone then N.LearnSpeed(m, v) end
  -- (ROUTEFIX) chemin réellement parcouru (PathLearn) : même échantillon, jamais en vol / en instance / mort
  local PL = T.PathLearn
  if PL and PL.Feed and not gone then pcall(PL.Feed, c, wx, wy, tnow, m) end
  return v
end

-- (5.1 ROUTE6) le cadre de l'échantillonneur est créé une fois à l'allumage, CACHÉ (un cadre caché ne reçoit pas
-- d'OnUpdate : rien au repos) — avant, il naissait au premier trajet, n'importe quand dans la session
local function makeSampler()
  if sampler or type(G("CreateFrame")) ~= "function" then return sampler end
  sampler = G("CreateFrame")("Frame", nil, G("UIParent"))
  sampler:Hide()
  local acc = 0
  sampler:SetScript("OnUpdate", function(f, dt)
    acc = acc + (dt or 0)
    if acc < 1 then return end
    acc = 0
    N.Sample()
    if not (N.trip or N.moving) then forget() f:Hide() end
  end)
  return sampler
end
function N.Wake()
  local f = makeSampler()
  if f then f:Show() end
end

function N:OnEnable()
  store()
  makeSampler()
  self:RegisterEvent("PLAYER_STARTED_MOVING", function() N.moving = true N.Wake() end)
  self:RegisterEvent("PLAYER_STOPPED_MOVING", function() N.moving = nil end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if N.trip then N.trip.combat = true end end)
  self:RegisterEvent("PLAYER_DEAD", function() N.trip = nil pcall(N.NoteDeath) end)
  self:On("SUMMARY", function()
    local s = T.Summary and T:Summary()
    if type(s) ~= "table" then return end
    local a, n = N.Accuracy()
    s.navTime = { walk = floor(N.Speed("w") * 10 + 0.5) / 10, mount = floor(N.Speed("m") * 10 + 0.5) / 10,
      err = a and floor(a * 10 + 0.5) / 10 or nil, trips = n }
  end)
end
function N:OnDisable()
  N.trip = nil
  if sampler then sampler:Hide() end
end
