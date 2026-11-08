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
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local N = T:RegisterModule("NavTime", {
  title = "Temps de trajet",
  defaults = { enabled = true },
  options = {},
})
T.NavTime = N

local floor, sqrt, abs, max, min = math.floor, math.sqrt, math.abs, math.max, math.min
local DEFAULT_RUN, DEFAULT_MOUNT, DEFAULT_SWIM = 7, 14, 4.7
local PRIOR_DETOUR = 1.15          -- prairie ouverte ≈ 1,05 ; relief ≈ 1,3 : appris ensuite par zone
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
function N.Detour(mapID)
  local db = store()
  local e = db and mapID and db.detour[mapID]
  if type(e) == "table" and (e.n or 0) >= 2 and (e.s or 0) > 0 then return e.s / e.n, e.n end
  return PRIOR_DETOUR, 0
end
function N.LearnDetour(mapID, ratio)
  local db = store()
  ratio = tonumber(ratio)
  if not (db and mapID and ratio) then return end
  ratio = max(1, min(2.5, ratio))
  local e = db.detour[mapID]
  if type(e) ~= "table" then e = { s = 0, n = 0 }; db.detour[mapID] = e end
  e.s, e.n = e.s + ratio, e.n + 1
  if e.n > 20 then e.s, e.n = e.s * 20 / e.n, 20 end  -- récent d'abord
end

-- ------------------------------------------------------------------ estimation a → b ({mapID, x, y} en 0..1)
-- → secondes, source ("spf" | "modèle"), distance à vol d'oiseau (yd)
function N.Model(yd, mapID, m)
  if not yd then return nil end
  local sp = N.Speed(m)
  return yd * N.Detour(mapID) / sp
end
function N.Estimate(a, b)
  local C = T.Compass
  if not (a and b) then return nil end
  local yd = C and C.Geo and C.Geo.Vector and C.Geo.Vector(a, b)
  local sec = C and C.Travel and C.Travel(a, b)
  if sec then return sec, "spf", yd end
  if not yd then return nil end
  return N.Model(yd, b.mapID or a.mapID), "modèle", yd
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
  return v
end

function N.Wake()
  if sampler then sampler:Show() return end
  if type(G("CreateFrame")) ~= "function" then return end
  sampler = G("CreateFrame")("Frame", nil, G("UIParent"))
  local acc = 0
  sampler:SetScript("OnUpdate", function(f, dt)
    acc = acc + (dt or 0)
    if acc < 1 then return end
    acc = 0
    N.Sample()
    if not (N.trip or N.moving) then forget() f:Hide() end
  end)
end

function N:OnEnable()
  store()
  self:RegisterEvent("PLAYER_STARTED_MOVING", function() N.moving = true N.Wake() end)
  self:RegisterEvent("PLAYER_STOPPED_MOVING", function() N.moving = nil end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if N.trip then N.trip.combat = true end end)
  self:RegisterEvent("PLAYER_DEAD", function() N.trip = nil end)
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
