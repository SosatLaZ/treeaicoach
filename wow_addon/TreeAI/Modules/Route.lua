-- TreeAI — Itinéraire (agent ROUTE) : le chemin le plus rapide entre les étapes du plan, présenté aussi
-- clairement que « Suivi Forever » (Shortest Path Forever), et confié à lui quand il est installé.
--  * Le plus rapide entre deux points = min( marche, vol (maîtres de vol CONNUS, durées des tables client),
--    bateau / zeppelin (table statique Forever : attente moyenne = période / 2 + traversée), pierre de foyer
--    (depuis ta position, si prête, vers ton auberge apprise) ). Données : Data/Travel.lua (généré, même table
--    que le solveur Python treeaicoach/wow/planner/transport.py).
--  * Affichage (onglet Quêtes / À faire) : « Trajet 12 min · 2,4 km », « Arrêt 1/8 : Tue « Loup » (2/6) »,
--    les vols / bateaux / zeppelins / foyer comme arrêts (« Zeppelin pour Clairières de Tirisfal · 6 min »).
--    Clic sur le titre = pause / reprise (« Itinéraire en pause. Clique sur le titre pour reprendre »).
--  * Shortest Path Forever présent : « Suivre ce trajet » = ShortestPathForever.API.NavigateRoute("TreeAI",
--    arrêts dans NOTRE ordre) → un seul trajet, dans le Suivi Forever que le joueur aime déjà ; zones
--    d'objectif tenues (hold + radius + questID) et itinéraire renvoyé quand une étape est faite.
--  * Tri des quêtes avant le calcul : trop loin / trop dures (niveau, élite en solo) → « Garde 6 quêtes,
--    mets-en 3 de côté (trop loin) » ; un clic adopte (ces quêtes sortent du trajet, rien n'est abandonné).
-- Guidage UNIQUEMENT sur clic, jamais en combat ; aucune fonction protégée, aucun cadre de Blizzard touché.
-- Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local R = T:RegisterModule("Route", {
  title = "Itinéraire",
  defaults = { enabled = true, spf = true, filter = true, hearth = true, transports = true, farMin = 10 },
  charDefaults = { paused = false, drop = {}, dropLevel = 0, bind = {}, taxi = {} },
  options = {
    { key = "transports", type = "toggle", label = "Chemin le plus rapide : vols, bateaux, zeppelins" },
    { key = "hearth", type = "toggle", label = "Compter la pierre de foyer quand elle est prête" },
    { key = "spf", type = "toggle", label = "« Suivre ce trajet » dans Shortest Path Forever (s'il est installé)" },
    { key = "filter", type = "toggle", label = "Proposer de laisser les quêtes trop loin / trop dures" },
    { key = "farMin", type = "range", label = "Trop loin au-delà de (minutes de trajet)", min = 5, max = 30, step = 1 },
  },
})
T.Route = R

local floor, max, min, sqrt, huge = math.floor, math.max, math.min, math.sqrt, math.huge
local tsort, tconcat, fmt = table.sort, table.concat, string.format
local OWNER = "TreeAI"
local HEARTHSTONE, HEARTH_SPELL = 6948, 8690

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d = pcall(fn, ...)
  if ok then return a, b, c, d end
  return nil
end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return try(G("GetTime")) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function opt(k) return not (R.db and R.db[k] == false) end
local function data() return T.Data and T.Data.Travel or nil end
-- (QA-QUEST F27) réponse à un clic : une ligne courte dans « Maintenant » (6 s), jamais le chat
function R:Say(text) pcall(self.Suggest, self, "say", text, 1, { ttl = 6, sound = false }) end

-- ===================================================================== formats
local function minutes(sec)
  sec = tonumber(sec) or 0
  if sec < 60 then return fmt("%d s", max(0, floor(sec + 0.5))) end
  return fmt("%d min", floor(sec / 60 + 0.5))
end
R.Minutes = minutes
local function km(yd)
  local m = (tonumber(yd) or 0) * 0.9144
  if m < 1000 then return fmt("%d m", floor(m / 10 + 0.5) * 10) end
  return (fmt("%.1f km", m / 1000):gsub("%.", ","))
end
R.Km = km

-- ===================================================================== lieux (repère monde : c, n = nord, w = ouest)
local FACTION = { Alliance = "A", Horde = "H" }
local function factionOK(tag, faction)
  if not tag or tag == false or tag == "" then return true end
  local f = FACTION[faction or ""] or faction
  if not f or f == "" then return true end
  return tag == f or FACTION[tag] == f
end
R.FactionOK = factionOK

-- Graphe des « hubs » (maîtres de vol connus + quais) : H[i][j] secondes, nxt (saut suivant), mode des arêtes.
-- Calculé une fois par (faction, maîtres de vol connus) ; m ≈ 20–60 → Floyd–Warshall en ~O(m³).
local hubCache
function R.BuildHubs(faction, known, o)
  local D = data()
  if not D then return nil end
  o = o or {}
  local speed, detour = o.speed or D.walk or 7, o.detour or D.detour or 1.3
  local pl, idx = {}, {}
  local knownSet = {}
  for _, id in ipairs(known or {}) do knownSet[tonumber(id) or -1] = true end
  for id, r in pairs(D.taxi or {}) do
    if knownSet[id] and factionOK(r[8], faction) then
      pl[#pl + 1] = { kind = "taxi", id = id, name = r[1], c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7] }
    end
  end
  tsort(pl, function(a, b) return a.id < b.id end)
  for id, r in pairs(D.docks or {}) do
    pl[#pl + 1] = { kind = "dock", id = id, name = r[1], c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7] }
  end
  tsort(pl, function(a, b) if a.kind ~= b.kind then return a.kind == "taxi" end return a.id < b.id end)
  for i, p in ipairs(pl) do idx[p.kind .. p.id] = i end
  local m = #pl
  local H, nxt, mode = {}, {}, {}
  for i = 1, m do
    local Hi, Ni, Mi = {}, {}, {}
    H[i], nxt[i], mode[i] = Hi, Ni, Mi
    local a = pl[i]
    for j = 1, m do
      local b = pl[j]
      if i == j then Hi[j], Ni[j] = 0, j
      elseif a.c == b.c then
        Hi[j], Ni[j], Mi[j] = sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2) * detour / speed, j, "walk"
      else Hi[j], Ni[j] = huge, -1 end
    end
  end
  -- vols : arêtes directes (durée apprise si connue), puis vols à plusieurs sauts = un seul vol continu
  local F = {}
  for _, e in ipairs(D.flights or {}) do
    local i, j = idx["taxi" .. e[1]], idx["taxi" .. e[2]]
    if i and j then
      local learned = o.flights and o.flights[e[1] .. "-" .. e[2]]
      local s = tonumber(learned) or e[3]
      F[i] = F[i] or {}
      if not F[i][j] or s < F[i][j] then F[i][j] = s end
    end
  end
  local taxis = {}
  for i, p in ipairs(pl) do if p.kind == "taxi" then taxis[#taxis + 1] = i end end
  for _, k in ipairs(taxis) do
    local Fk = F[k]
    if Fk then
      for _, i in ipairs(taxis) do
        local ik = F[i] and F[i][k]
        if ik and i ~= k then
          for j, kj in pairs(Fk) do
            if i ~= j and (not F[i][j] or ik + kj < F[i][j]) then F[i][j] = ik + kj end
          end
        end
      end
    end
  end
  local board = D.board or 20
  for i, row in pairs(F) do
    for j, s in pairs(row) do
      if s + board < H[i][j] then H[i][j], nxt[i][j], mode[i][j] = s + board, j, "fly" end
    end
  end
  for _, ln in ipairs(D.lines or {}) do
    if opt("transports") and factionOK(ln[2], faction) then
      for _, lg in ipairs(ln[4] or {}) do
        local i, j = idx["dock" .. lg[1]], idx["dock" .. lg[2]]
        local s = (ln[3] or 0) / 2 + (lg[3] or 0)
        if i and j and s < H[i][j] then H[i][j], nxt[i][j], mode[i][j] = s, j, ln[1] end
      end
    end
  end
  for k = 1, m do
    local Hk = H[k]
    for i = 1, m do
      local hik = H[i][k]
      if hik < huge and i ~= k then
        local Hi, Ni = H[i], nxt[i]
        for j = 1, m do
          local v = hik + Hk[j]
          if v < Hi[j] then Hi[j], Ni[j] = v, Ni[k] end
        end
      end
    end
  end
  return { pl = pl, H = H, nxt = nxt, mode = mode, speed = speed, detour = detour, minGain = D.minGain or 30,
           hs = (D.hearthCast or 10) + (D.hearthLoad or 5) }
end

local function walkS(g, a, b)
  if a.c ~= b.c then return huge end
  return sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2) * g.detour / g.speed
end
local function nearest(g, p, k)
  local list = {}
  for i, h in ipairs(g.pl) do
    if h.c == p.c then list[#list + 1] = { i = i, s = walkS(g, p, h) } end
  end
  tsort(list, function(a, b) return a.s < b.s end)
  local out = {}
  for i = 1, min(k, #list) do out[i] = list[i] end
  return out
end
-- best a → b through the hubs: seconds, entry hub, exit hub
local function viaHubs(g, a, b)
  local best, bi, bj = huge, nil, nil
  local A, B = nearest(g, a, 4), nearest(g, b, 4)
  for _, ea in ipairs(A) do
    local Hi = g.H[ea.i]
    for _, eb in ipairs(B) do
      local v = ea.s + Hi[eb.i] + eb.s
      if v < best then best, bi, bj = v, ea.i, eb.i end
    end
  end
  return best, bi, bj
end
local function pushLeg(legs, mode, sec, to, yd)
  local last = legs[#legs]
  if mode == "walk" and last and last.mode == "walk" then
    last.sec, last.yd, last.to = last.sec + sec, last.yd + (yd or 0), to
  else
    legs[#legs + 1] = { mode = mode, sec = sec, to = to, yd = yd or 0 }
  end
end
local function hubLegs(g, legs, from, bi, bj, b)
  local p0 = g.pl[bi]
  pushLeg(legs, "walk", walkS(g, from, p0), p0, sqrt((from.n - p0.n) ^ 2 + (from.w - p0.w) ^ 2))
  local i, guard = bi, 0
  while i ~= bj and guard < 64 do
    local k = g.nxt[i][bj]
    if not k or k < 0 then break end
    local mode = g.mode[i][k] or "walk"
    local a, c = g.pl[i], g.pl[k]
    local s = (mode == "walk") and walkS(g, a, c) or g.H[i][k]
    -- H[i][k] after Floyd is the best i→k; for a direct edge it is the edge itself
    pushLeg(legs, mode, s, c, mode == "walk" and sqrt((a.n - c.n) ^ 2 + (a.w - c.w) ^ 2) or 0)
    i, guard = k, guard + 1
  end
  local pe = g.pl[bj]
  pushLeg(legs, "walk", walkS(g, pe, b), b, sqrt((pe.n - b.n) ^ 2 + (pe.w - b.w) ^ 2))
end
-- Plus rapide a → b (points monde { c, n, w }). o.bind = { c, n, w, name } + o.hearth = true : le foyer est
-- possible au départ. Renvoie secondes, étapes { mode = walk|fly|boat|zeppelin|hs, sec, yd, to }.
function R.FastestWorld(g, a, b, o)
  if not (g and a and b) then return huge, {} end
  o = o or {}
  local direct = walkS(g, a, b)
  local best, how = direct, "walk"
  local sv, bi, bj = viaHubs(g, a, b)
  if sv < best then best, how = sv, "hub" end
  local hsTotal, hsHow, hbi, hbj
  if o.hearth and o.bind and o.bind.c then
    local bd = walkS(g, o.bind, b)
    local bv, i2, j2 = viaHubs(g, o.bind, b)
    if bv < bd then hsTotal, hsHow, hbi, hbj = g.hs + bv, "hub", i2, j2 else hsTotal, hsHow = g.hs + bd, "walk" end
    if hsTotal < best then best, how = hsTotal, "hs" end
  end
  local legs = {}
  if how == "walk" or direct <= best + g.minGain then
    if direct == huge then return huge, {} end
    pushLeg(legs, "walk", direct, b, sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2))
    return direct, legs
  end
  if how == "hub" then
    hubLegs(g, legs, a, bi, bj, b)
  else
    local bind = { c = o.bind.c, n = o.bind.n, w = o.bind.w, name = o.bind.name or "auberge" }
    legs[1] = { mode = "hs", sec = g.hs, to = bind, yd = 0 }
    if hsHow == "hub" then hubLegs(g, legs, bind, hbi, hbj, b)
    else pushLeg(legs, "walk", walkS(g, bind, b), b, sqrt((bind.n - b.n) ^ 2 + (bind.w - b.w) ^ 2)) end
  end
  return best, legs
end

-- ===================================================================== état du jeu (gardé)
local Geo = T.Geo or {}
local function world(p)
  if not (p and p.map and p.x and Geo.World) then return nil end
  local n, w, c = Geo.World(p.map, p.x, p.y)
  if not n then return nil end
  return { c = c, n = n, w = w, map = p.map, x = p.x, y = p.y, name = p.name }
end
R.World = world
function R.Faction() return try(G("UnitFactionGroup"), "player") end
local knownT, knownAt
function R.KnownTaxi()
  if knownT and now() - (knownAt or 0) < 30 then return knownT end
  local f = C("C_TaxiMap", "GetTaxiNodesForMap")
  local set = {}
  for id, v in pairs(R.char and R.char.taxi or {}) do if v then set[tonumber(id) or id] = true end end
  if f then
    for _, m in ipairs({ 1414, 1415, try(C("C_Map", "GetBestMapForUnit"), "player") }) do
      for _, nd in ipairs(try(f, m) or {}) do
        if type(nd) == "table" and nd.nodeID and nd.isUndiscovered == false then
          set[nd.nodeID] = true
          if R.char then R.char.taxi[nd.nodeID] = true end
        end
      end
    end
  end
  local out = {}
  for id in pairs(set) do if tonumber(id) then out[#out + 1] = tonumber(id) end end
  tsort(out)
  knownT, knownAt = out, now()
  return out
end
local function learnedFlights()
  local db = G("TreeAIDB")
  local L = type(db) == "table" and type(db.learn) == "table" and db.learn.flights or nil
  if type(L) ~= "table" then return nil end
  local out = {}
  for k, e in pairs(L) do if type(e) == "table" and (e.n or 0) > 0 then out[k] = e.s / e.n end end
  return out
end
function R.Graph()
  local faction, known = R.Faction(), R.KnownTaxi()
  -- (NAV) marche = TA vitesse mesurée × détour appris de la zone (NavTime), arrondis pour garder le cache
  local NT, speed, detour = T.NavTime, nil, nil
  if NT and NT.Speed then
    speed = floor(NT.Speed("w") * 2 + 0.5) / 2
    local m = try(C("C_Map", "GetBestMapForUnit"), "player")
    detour = floor(NT.Detour(m) * 10 + 0.5) / 10
  end
  local key = tostring(faction) .. "|" .. tconcat(known, ",") .. "|" .. tostring(opt("transports")) .. "|"
    .. tostring(speed) .. "|" .. tostring(detour)
  if hubCache and hubCache.key == key then return hubCache.g end
  local g = R.BuildHubs(faction, known, { flights = learnedFlights(), speed = speed, detour = detour })
  hubCache = { key = key, g = g }
  R.legCache = {}
  return g
end
-- hearthstone: ready? bind point (learned on arrival, else matched by name in the travel data)
function R.HearthReady()
  if not opt("hearth") then return false end
  -- (QA-QUEST F8) Forever n'a que C_Item.GetItemCount (le global GetItemCount n'existe plus) : sans ça, une pierre
  -- de foyer laissée en banque était comptée dans le trajet
  local cnt = try(C("C_Item", "GetItemCount"), HEARTHSTONE)
  if cnt == nil then cnt = try(G("GetItemCount"), HEARTHSTONE) end
  if cnt ~= nil and (tonumber(cnt) or 0) < 1 then return false end
  local s, d = try(C("C_Container", "GetItemCooldown"), HEARTHSTONE)
  if s == nil then s, d = try(G("GetItemCooldown"), HEARTHSTONE) end
  s, d = tonumber(s) or 0, tonumber(d) or 0
  return not (s > 0 and d > 1.5 and s + d - now() > 0)
end
local function low(s) return tostring(s or ""):lower() end
function R.BindPlace()
  local name = try(G("GetBindLocation"))
  -- one source: the inn Speed memorised at HEARTHSTONE_BOUND; then our arrival memory; then a name match
  local Sp = T.Speed
  local sb = Sp and Sp.Bind and try(Sp.Bind, Sp)
  if type(sb) == "table" and sb.map and sb.x then
    local w = world(sb)
    if w then w.name = sb.name or name return w end
  end
  if not name or name == "" then return nil end
  local p = R.char and R.char.bind[name]
  if type(p) == "table" and p.map then
    local w = world(p)
    if w then w.name = name return w end
  end
  local D = data()
  if not D then return nil end
  local ln = low(name)
  for _, set in ipairs({ D.taxi or {}, D.docks or {} }) do
    for _, r in pairs(set) do
      local rn = low(r[1])
      if rn ~= "" and (rn:find(ln, 1, true) or ln:find(rn, 1, true)) then
        return { c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7], name = name }
      end
    end
  end
  return nil
end

-- secondes de trajet entre deux positions { map, x, y } en % (Planner) ; nil = inconnu
function R.LegSeconds(a, b)
  if not (opt("transports") and a and b and a.x and b.x) then return nil end
  local g = R.Graph()
  if not g then return nil end
  local key = fmt("%s:%.0f:%.0f>%s:%.0f:%.0f", tostring(a.map), a.x, a.y, tostring(b.map), b.x, b.y)
  local cache = R.legCache or {}
  R.legCache = cache
  if cache[key] then return cache[key] end
  local wa, wb = world(a), world(b)
  if not (wa and wb) then return nil end
  local direct = walkS(g, wa, wb)
  local s = direct
  if direct > 90 then s = R.FastestWorld(g, wa, wb) end
  if s == huge then s = nil end
  cache[key] = s
  return s
end

-- ===================================================================== tri des quêtes (pur)
-- cands = { { q, title, lv, elite, dist (s de trajet), done, inLog } } ; ctx = { level, group, farMin }
-- → keep, drop = { { q, title, why } }. Règles : trop dure (élite en solo, niveau ≥ +4 solo / +5 groupe),
-- trop loin (trajet > max(farMin, 2,5 × médiane) ; ×1,5 pour une quête prête à rendre ; autre continent sans
-- transport) ; jamais moins de 3 quêtes gardées pour « trop loin ». (QA-QUEST F9) position inconnue (pas de
-- Questie) = gardée, jamais « trop loin » ; quête chronométrée en cours (c.timer) = jamais proposée au retrait.
function R.Select(cands, ctx)
  ctx = ctx or {}
  local level, group = tonumber(ctx.level) or 1, max(1, tonumber(ctx.group) or 1)
  local farS = (tonumber(ctx.farMin) or 10) * 60
  local keep, drop, rest = {}, {}, {}
  for _, c in ipairs(cands or {}) do
    local lv = tonumber(c.lv) or level
    if c.timer then keep[#keep + 1] = c
    elseif group <= 1 and c.elite then drop[#drop + 1] = { q = c.q, title = c.title, why = "élite" }
    elseif lv - level >= (group <= 1 and 4 or 5) then drop[#drop + 1] = { q = c.q, title = c.title, why = "trop dure" }
    else rest[#rest + 1] = c end
  end
  local ds = {}
  for _, c in ipairs(rest) do if c.dist and c.dist < huge then ds[#ds + 1] = c.dist end end
  tsort(ds)
  local med = #ds > 0 and ds[floor((#ds + 1) / 2)] or 0
  local limit = max(farS, 2.5 * med)
  local far = {}
  for _, c in ipairs(rest) do
    local lim = c.done and limit * 1.5 or limit
    if c.dist and (c.dist == huge or c.dist > lim) then far[#far + 1] = c else keep[#keep + 1] = c end
  end
  tsort(far, function(a, b) return (a.dist or huge) < (b.dist or huge) end)
  for _, c in ipairs(far) do
    if #keep < 3 then keep[#keep + 1] = c
    else drop[#drop + 1] = { q = c.q, title = c.title, why = "trop loin" } end
  end
  return keep, drop
end
-- (ZONEFIX) accord : « 9 trop dures », « 2 élites » (« trop loin » ne s'accorde pas)
local WHY_PL = { ["trop dure"] = "trop dures", ["élite"] = "élites" }
function R.ProposalText(keep, drop, zone)
  if not (drop and #drop > 0) then return nil end
  local n = {}
  local order = {}
  for _, d in ipairs(drop) do
    if not n[d.why] then n[d.why] = 0; order[#order + 1] = d.why end
    n[d.why] = n[d.why] + 1
  end
  local why
  if #order == 1 then why = order[1] == "élite" and "élites, en groupe seulement" or ((#drop > 1 and WHY_PL[order[1]]) or order[1])
  else
    local parts = {}
    for _, w in ipairs(order) do parts[#parts + 1] = n[w] .. " " .. ((n[w] > 1 and WHY_PL[w]) or w) end
    why = tconcat(parts, ", ")
  end
  local k = #(keep or {})
  -- (QA-QUEST) « Garde 8 quêtes, mets-en 1 de côté (trop loin) » (avant : « laisse cette 1 ») ; (ZONEFIX) les quêtes
  -- comptées sont celles de la zone choisie : « Orneval : garde 12 quêtes, … » (avant : « Garde 27 quêtes » sur 4 zones)
  local head = fmt("%s %d quête%s", zone and "garde" or "Garde", k, k > 1 and "s" or "")
  return fmt("%s%s, mets-en %d de côté (%s)", zone and (cut(zone, 30) .. " : ") or "", head, #drop, why)
end

-- Planner:BuildAuto → requêtes filtrées (quêtes laissées adoptées) + proposition calculée sur le reste
local function firstPos(req)
  for _, s in ipairs(req.steps or {}) do if s.x then return s end end
end
function R:FilterRequests(requests, recs, log, ctx)
  if not opt("filter") or type(requests) ~= "table" then self.selection = nil return requests end
  local ch = self.char
  ctx = ctx or {}
  if ch and ch.dropLevel ~= ctx.level then ch.drop, ch.dropLevel = {}, ctx.level end  -- nouveau niveau : tout revoir
  local Gp = Geo.Player
  local pm, px, py
  if Gp then pm, px, py = Gp() end
  local here = (pm and px) and { map = pm, x = px, y = py } or nil
  local out, cands, dropped = {}, {}, 0
  local Qm = T.modules and T.modules.Quests            -- (QA-QUEST F9) chronos en cours : jamais « laisse »
  local okT, timers = false, nil
  if Qm and Qm.Timers and Qm.lastList then okT, timers = pcall(Qm.Timers, Qm.lastList) end
  timers = (okT and type(timers) == "table") and timers or {}
  for _, req in ipairs(requests) do
    if ch and ch.drop[req.q] then dropped = dropped + 1
    else
      out[#out + 1] = req
      local rec, lq = recs and recs[req.q], log and log[req.q]
      local elite = false
      for _, o in ipairs(rec and rec.o or {}) do if (tonumber(o.rank) or 0) >= 1 and o.rank ~= 4 then elite = true end end
      local p = firstPos(req)
      local dist = (here and p) and (R.LegSeconds(here, p) or (Geo.Dist and Geo.Dist(here.map, here.x, here.y, p.map, p.x, p.y)
        and Geo.Dist(here.map, here.x, here.y, p.map, p.x, p.y) * 1.3 / 7)) or nil
      if here and p and dist == nil then dist = huge end
      cands[#cands + 1] = { q = req.q, title = (lq and lq.t) or (rec and rec.t) or ("quête " .. req.q),
        lv = (rec and rec.lv and rec.lv > 0 and rec.lv) or (lq and lq.lv) or nil, elite = elite, dist = dist,
        done = lq and lq.done == 1 or false, inLog = lq ~= nil, timer = timers[req.q] and true or nil }
    end
  end
  local keep, drop = R.Select(cands, { level = ctx.level, group = ctx.group, farMin = self.db and self.db.farMin or 10 })
  self.selection = { keep = keep, drop = drop, dropped = dropped }
  return out
end
function R:Adopt()
  local sel = self.selection
  if not (sel and self.char and #sel.drop > 0) then return 0 end
  for _, d in ipairs(sel.drop) do self.char.drop[d.q] = true end
  local n = #sel.drop
  self:Say(fmt("%d quête%s mise%s de côté (rien n'est abandonné)", n, n > 1 and "s" or "", n > 1 and "s" or ""))
  self.selection = nil
  local P = T.Planner
  if P and P.Refresh then pcall(P.Refresh, P, true) end
  return n
end
function R:ResetDrops()
  if self.char then self.char.drop = {} end
  local P = T.Planner
  if P and P.Refresh then pcall(P.Refresh, P, true) end
end

-- ===================================================================== Shortest Path Forever
local function spf()
  if not opt("spf") then return nil end
  if T.GuideMode and T:GuideMode() == "treeai" then return nil end   -- (COHERE) option « Guidage : TreeAI »
  local Cm = T.Compass
  local A = Cm and Cm.SPF and Cm.SPF()
  if A and type(A.NavigateRoute) == "function" then return A, Cm end
end
R.SPF = spf
local function call(fn, ...)
  local A, Cm = spf()
  if not A then return nil end
  if Cm.SPFCall then return Cm.SPFCall(A, fn, ...) end
  return try(A[fn], ...)
end
local KIND = { pick = "pickup", turn = "turnin", obj = "objective", fly = "flightmaster", hs = "innkeeper",
  train = "trainer", vendor = "innkeeper", boat = "boat", zep = "zeppelin" }
local TRANSPORT = { fly = true, boat = true, zep = true, hs = true }
-- remaining quest stops for SPF, in OUR order (SPF finds the flights / boats between them itself)
function R:SPFStops()
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  if not (steps and cur) then return {} end
  local stops = {}
  for i = cur, #steps do
    local s = steps[i]
    if not s._done and not s._other and s.x and not TRANSPORT[s.k] and #stops < 64 then
      local label = P:StepLabel(s)
      local st = { map = s.map, x = s.x / 100, y = s.y / 100, title = cut(label, 60), kind = KIND[s.k], questID = s.q }
      if s.k == "obj" then st.hold, st.radius = true, 60 end
      stops[#stops + 1] = st
    end
  end
  return stops
end
function R:HasRoute()
  local P = T.Planner
  return P and P.steps and P.cursor and P.cursor <= #P.steps or false
end
function R:Following()
  if not self.following or not spf() then return nil end
  local stop = call("CurrentStop", OWNER)
  if stop then return stop end
  local why = call("Ended", OWNER)
  if why then self.following = nil end
  return nil
end
-- (COHERE) étape du Planner qui correspond à l'arrêt courant de SPF (CurrentStop = n° ou { index = n }), nil sinon
function R:CurrentStep()
  local stop = self.following and self:Following()
  local n = type(stop) == "number" and stop or (type(stop) == "table" and tonumber(stop.index or stop.i)) or nil
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  if not (n and n >= 1 and steps and cur) then return nil end
  local k = 0
  for i = cur, #steps do
    local s = steps[i]
    if not s._done and not s._other and s.x and not TRANSPORT[s.k] then
      k = k + 1
      if k == n then return s end
    end
  end
  return nil
end
-- « Suivre ce trajet » (clic) : SPF si présent, sinon la boussole / repère du Planner sur l'arrêt 1
function R:Follow()
  if inCombat() then self:Say("Guidage : après le combat") return false end
  local P = T.Planner
  if not self:HasRoute() then self:Say("Pas de trajet en cours") return false end
  self.char.paused = false
  local stops = self:SPFStops()
  if #stops > 0 and spf() then
    if call("NavigateRoute", OWNER, stops) then
      self.following, self._sentAt = true, now()
      self:Say(fmt("Trajet envoyé à Shortest Path : %d arrêt%s, dans le Suivi Forever", #stops, #stops > 1 and "s" or ""))
      self:Render()
      return "spf"
    end
  end
  local first = self:Stops(1)[1]
  if first and first.kind == "hs" and not first.step then  -- foyer d'abord : on se téléporte, rien à viser
    self:Say("D'abord la pierre de foyer, vers " .. cut((first.place and first.place.name) or "ton auberge", 28))
    return "hs"
  end
  if first and P and P.Navigate then
    local p = first.from or first.place            -- vol / bateau : le départ (maître de vol, quai), pas l'arrivée
    local target = first.step or (p and p.map and { k = "fly", map = p.map, x = p.x, y = p.y, label = first.text })
    return P:Navigate(target) and "planner"
  end
  return false
end
function R:OnPlanStep()
  if not self.following or self.char.paused then return end
  if inCombat() then self._resend = true return end
  if self._sentAt and now() - self._sentAt < 2 then self._resend = true return end
  local stops = self:SPFStops()
  if #stops == 0 then call("Cancel", OWNER); self.following = nil return end
  if call("NavigateRoute", OWNER, stops) then self._sentAt = now() else self.following = nil end
end
function R:TogglePause()
  local ch = self.char
  ch.paused = not ch.paused
  if ch.paused then
    if self.following then call("Cancel", OWNER) end
    self.following = nil
  end
  self:Render()
  local P = T.Planner
  if P and P.Render then P._sig = nil; pcall(P.Render, P) end
end

-- ===================================================================== arrêts affichés (quêtes + transports)
local LEGTXT = { fly = "Vol pour %s", boat = "Bateau pour %s", zeppelin = "Zeppelin pour %s" }
local DIRS = { "N", "NE", "E", "SE", "S", "SO", "O", "NO" }
local function dirTo(a, b)
  if not (a and b) then return nil end
  local dn, dw = b.n - a.n, b.w - a.w           -- nord, ouest
  local ang = math.atan2 and math.atan2(-dw, dn) or 0  -- 0 = nord, sens horaire (est = −ouest)
  local i = floor(((ang * 180 / math.pi) + 360 + 22.5) % 360 / 45) + 1
  return DIRS[i]
end
local function stepSvc(steps, i)
  local s = steps[i]
  if s._pred then return s._pred * 60 end
  if s.eta then
    local prev = 0
    for j = i - 1, 1, -1 do if steps[j].eta then prev = steps[j].eta break end end
    return max(0, s.eta - prev) * 60 * 0.6       -- the plan's eta includes travel: ~60 % is the work itself
  end
  return (s.k == "pick" or s.k == "turn") and 15 or 0
end
-- liste des arrêts restants : { kind = "quest"|"walk"|"fly"|"boat"|"zeppelin"|"hs", text, sec, step, place, from,
-- at, ydAt, trAt } + totaux (secondes, yards de marche). n = nombre max d'arrêts de quête parcourus.
-- place = où l'entrée mène (arrêt, ou ARRIVÉE du vol / bateau) ; from = (QA-QUEST F4) départ d'un vol / bateau /
-- zeppelin (maître de vol, quai : là où marcher), nil pour le foyer ; at / ydAt / trAt = secondes, yards de marche,
-- secondes de déplacement cumulées depuis maintenant à la fin de l'entrée.
-- (LITE) mémorisé 5 s pour le même plan / la même étape (le panneau se redessine souvent, le trajet bouge peu) ;
-- (ZONEFIX, PERF4) immobile (même position, revue au plus une fois par seconde) : gardé jusqu'à 5 min
local stopsMemo = {}                 -- (QUESTQA) un mémo par n (1 = 1er arrêt, 64 = vue) : plus de recalcul en ping-pong
R.STOPS_IDLE_S = 300
local function idleNoHs() local Zp = T.ZonePlan return Zp and Zp.noHearth or false end
local function graphNow() return opt("transports") and R.Graph() or false end
function R:Stops(n)
  local P = T.Planner
  local tnow = now()
  local m = stopsMemo[n or 0] or {}
  if P and m.steps == P.steps and m.cur == P.cursor and m.len == #(P.steps or {}) and m.rev == P.stepsRev
      and m.skipN == (P.char and P.char.skip and next(P.char.skip) and true or false) then
    local age = tnow - (m.at or -1e9)
    if age < 5 then return m.out end
    -- immobile : même position, mêmes étapes faites (Evaluate), mêmes vols connus (graphe), même pierre de foyer,
    -- même règle « sans foyer »
    if age < R.STOPS_IDLE_S and m.ev == P.evalRev and m.src == P.src and m.noHs == idleNoHs() then
      if tnow - (m.chk or -1e9) < 1 then return m.out end
      local Gp = Geo.Player
      local pm, px, py
      if Gp then pm, px, py = Gp() end
      if pm and pm == m.pm and px == m.px and py == m.py and m.hs == R.HearthReady() and m.g == graphNow() then
        m.chk = tnow
        return m.out
      end
    end
  end
  local out = self:StopsRaw(n)
  local pm, px, py
  if Geo.Player then pm, px, py = Geo.Player() end
  stopsMemo[n or 0] = { steps = P and P.steps, cur = P and P.cursor, len = P and #(P.steps or {}) or 0, rev = P and P.stepsRev,
    skipN = (P and P.char and P.char.skip and next(P.char.skip) and true or false), at = tnow, chk = tnow, out = out,
    pm = pm, px = px, py = py, g = graphNow(), src = P and P.src, noHs = idleNoHs(), hs = R.HearthReady(),
    ev = P and P.evalRev }
  R.firstKind = out[1] and out[1].kind or nil
  return out
end
function R:StopsRaw(n)
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  local out = { total = 0, travel = 0, yd = 0 }
  if not (steps and cur) then return out end
  local g = opt("transports") and R.Graph() or nil
  local Gp = Geo.Player
  local pm, px, py
  if Gp then pm, px, py = Gp() end
  local prev = world((pm and px) and { map = pm, x = px, y = py } or nil)
  local first = true
  local quests, prevKind = 0, nil
  local spfN, Cm = 0, T.Compass
  -- (QA-QUEST F1) trajet en jeu calculé sans foyer (un arrêt à moins de 5 min) : pas de foyer ici non plus
  local Zp = T.ZonePlan
  local noHs = Zp and Zp.noHearth and P.src ~= "file" and Zp.IsEnabled and Zp:IsEnabled()
  for i = cur, #steps do
    local s = steps[i]
    if not s._done and not s._other then
      local here = world(s.x and s or nil)
      if prev and here then
        local o = first and not noHs and R.HearthReady() and { hearth = true, bind = R.BindPlace() } or nil
        local sec, legs
        if g then sec, legs = R.FastestWorld(g, prev, here, o) end
        if not sec or sec == huge then
          local d = (prev.c == here.c) and sqrt((prev.n - here.n) ^ 2 + (prev.w - here.w) ^ 2) or nil
          local NT = T.NavTime
          sec = d and ((NT and NT.Model) and NT.Model(d, here.map) or d * 1.3 / 7) or 0
          legs = { { mode = "walk", sec = sec, yd = d or 0, to = here } }
        end
        -- (NAV) les 3 premiers trajets à pied : la durée navmesh de Shortest Path quand il est là (hors combat)
        if spfN < 3 and legs and #legs == 1 and legs[1].mode == "walk" and Cm and Cm.Travel and prev.map and here.map then
          spfN = spfN + 1
          local ss = Cm.Travel({ mapID = prev.map, x = prev.x / 100, y = prev.y / 100 },
            { mapID = here.map, x = here.x / 100, y = here.y / 100 })
          if ss then sec = ss legs[1].sec = ss end
        end
        local run, trRun, lastTo = out.total, out.travel, prev
        for _, lg in ipairs(legs or {}) do
          run, trRun = run + (lg.sec or 0), trRun + (lg.sec or 0)
          if lg.mode == "walk" then out.yd = out.yd + (lg.yd or 0)
          elseif lg.mode ~= prevKind then          -- a plan step already shows this transport
            -- (QA-QUEST F16) « Pierre de foyer vers Orgrimmar » (la flèche → n'existe pas dans les polices du jeu)
            local txt = lg.mode == "hs" and ("Pierre de foyer vers " .. cut(lg.to.name or "l'auberge", 28))
              or fmt(LEGTXT[lg.mode] or "%s", cut(lg.to.name or "?", 30))
            out[#out + 1] = { kind = lg.mode, text = txt, sec = lg.sec, place = lg.to, from = lg.mode ~= "hs" and lastTo or nil,
              at = run, ydAt = out.yd, trAt = trRun }
          end
          lastTo = lg.to
        end
        out.travel, out.total = out.travel + sec, out.total + sec
      end
      local svc = stepSvc(steps, i)
      out.total = out.total + svc
      out[#out + 1] = { kind = TRANSPORT[s.k] and (s.k == "zep" and "zeppelin" or s.k) or "quest", step = s, sec = svc, place = here,
        at = out.total, ydAt = out.yd, trAt = out.travel }
      prev = here or prev
      prevKind = TRANSPORT[s.k] and (s.k == "zep" and "zeppelin" or s.k) or nil
      first = false
      quests = quests + 1
      if n and quests >= n then break end
    end
  end
  local left = 0                                   -- arrêts restants au total (au-delà de n aussi)
  for i = cur, #steps do if not steps[i]._done and not steps[i]._other then left = left + 1 end end
  out.left = left
  out.here = world((pm and px) and { map = pm, x = px, y = py } or nil)
  return out
end
-- ===================================================================== cible de la boussole = arrêt 1
-- (ZONEFIX) la boussole vise TOUJOURS l'arrêt 1 de la carte « Prochaine étape » : même liste (R:Stops), même objet.
-- (QA-QUEST F4) 1er trajet = un transport (foyer, vol, bateau, zeppelin) : la boussole vise CE départ (maître de vol /
-- quai à pied, ou « Pierre de foyer vers … » sans flèche), jamais la quête lointaine en ligne droite.
local function withDist(t, me, map, x, y)
  if me and me.mapID and me.x and Geo.Dist then
    local mx, my = me.x, me.y
    if mx <= 1 and my <= 1 then mx, my = mx * 100, my * 100 end
    t.dist = Geo.Dist(me.mapID, mx, my, map, x, y)
  end
  return t
end
function R.LegTarget(me)
  if not R:IsEnabled() then return nil end
  local ok, st = pcall(R.Stops, R, 1)
  local f = ok and type(st) == "table" and st[1] or nil
  if not f or f.step then return nil end
  if f.kind == "hs" then
    local name = cut((f.place and f.place.name) or "l'auberge", 28)
    local label = "Pierre de foyer vers " .. name
    return { key = "plan:hs:" .. name, kind = "zone", plan = true, leg = "hs", title = label, label = label,
      note = "Lance ta pierre de foyer", src = "trajet" }
  end
  local p = f.from or f.place
  if not (p and p.map and p.x and p.y) then return nil end
  local label = (T.SafeText and T.SafeText(tostring(f.text or "Trajet"))) or tostring(f.text or "Trajet")
  return withDist({ key = fmt("plan:%s:%s:%d:%d", tostring(f.kind), tostring(p.map), floor(p.x * 10), floor(p.y * 10)),
    kind = "travel", plan = true, leg = f.kind, title = label, label = label, mapID = p.map, x = p.x / 100, y = p.y / 100,
    src = "trajet" }, me, p.map, p.x, p.y)
end
--- cible de la boussole (Compass.BuildTargets) : nil sans trajet, en pause, ou boussole détachée du trajet (option)
function R:CompassTarget(me)
  if not (self:IsEnabled() and self.char) or self.char.paused then return nil end
  local Zp = T.ZonePlan
  if Zp and Zp.db and Zp.db.compass == false then return nil end
  local P = T.Planner
  local s = P and P.steps and P.cursor and P.steps[P.cursor]
  if not (s and not s._done and not s._other) then return nil end
  local lt = R.LegTarget(me)
  if lt then return lt end
  local label = P.StepLabel and P:StepLabel(s) or "Arrêt"
  local key = "plan:" .. tostring(s.k) .. ":" .. tostring(s.q or "") .. ":" .. tostring(s.o or "")
  if not (s.map and s.x) then
    -- pas de lieu connu (sans Questie, pas de point du jeu) : l'arrêt 1 quand même, sans flèche (« Zone : … »), jamais
    -- une autre quête du journal à sa place
    local Zp = T.ZonePlan
    local zm = s.map or (Zp and Zp.QuestZone and Zp.QuestZone(s.q))
    local zone = zm and Zp and Zp.MapName and Zp.MapName(zm) or nil
    return { key = key, kind = "zone", plan = true, questID = s.q, step = s, title = label, label = label, zone = zone,
      src = "trajet" }
  end
  return withDist({ key = key, kind = s.k == "turn" and "turnin" or "objective", plan = true, questID = s.q, step = s,
    title = label, label = label, mapID = s.map, x = s.x / 100, y = s.y / 100, src = "trajet" }, me, s.map, s.x, s.y)
end

-- (QUESTQA) horizon du trajet affiché : 1 h 30 ou 30 arrêts au plus (jamais « Trajet 307 min · 75 arrêts ») ;
-- la suite est refaite en route depuis le journal. → nombre d'entrées gardées, durée, yards, arrêts au-delà,
-- (QA-QUEST F7) secondes de déplacement dans l'horizon : LE total du trajet, le même partout (onglet, page, carte)
-- (ZONEFIX) 1 h au plus (était 1 h 30) : « Trajet ≈ 1 h 27 · 22 arrêts » sur 4 zones n'était pas un trajet à suivre
R.HORIZON_S, R.HORIZON_STOPS = 60 * 60, 30
function R.Horizon(st)
  local keep, quests = 0, 0
  for i, e in ipairs(st) do
    if i > 1 and ((e.at or 0) > R.HORIZON_S or quests >= R.HORIZON_STOPS) then break end
    keep = i
    if e.step then quests = quests + 1 end
  end
  local last = st[keep]
  local total = last and (last.at or st.total) or 0
  local yd = last and (last.ydAt or st.yd) or 0
  local travel = last and (last.trAt or st.travel) or 0
  return keep, total, yd, max(0, (st.left or quests) - quests), min(travel, total)
end
-- (QA-QUEST F18) numéros d'arrêt partagés par l'onglet, la page Trajet et la carte du monde : des objectifs
-- consécutifs du même coin (step.area, ZonePlan) = UN arrêt (« Arrêt 3/12 : Tue X + Ramasse Y »).
-- → g = { [i] = numéro de l'arrêt de l'entrée st[i], n = nombre d'arrêts } (entrées 1..n, ou toutes)
function R.Groups(st, n)
  local g, k, lastArea = { n = 0 }, 0, nil
  for i = 1, min(n or #st, #st) do
    local e = st[i]
    local a = e.step and e.step.area
    if not (a and a == lastArea) then k = k + 1 end
    g[i], lastArea = k, a
  end
  g.n = k
  return g
end

-- ===================================================================== lignes (remplace la vue du Planner)
local function progressTxt(P, s)
  if s.k ~= "obj" or not s.q then return "" end
  local lq = P.log and P.log[s.q]
  local o = lq and lq.obj and lq.obj[tonumber(s.o) or 0]
  if o and (o.n or 0) > 0 then return fmt(" (%d/%d)", o.have or 0, o.n) end
  return ""
end
function R:PlanLines(P)
  if not (self:IsEnabled() and P and P.route and P.steps) then return nil end
  local lines = {}
  local route = P.route
  local cur = P.cursor or 1
  if cur > #P.steps then return nil end
  local togg = function() R:TogglePause() end
  if self.char.paused then
    lines[1] = { text = "Trajet en pause", prio = 1, onClick = togg, tooltip = "Clic : reprendre l'itinéraire" }
    lines[2] = { text = "Itinéraire en pause. Clique sur le titre pour reprendre.", prio = 1, onClick = togg }
    return lines
  end
  local all = self:Stops(64)
  local keepN, total, yd, beyond, travelH = R.Horizon(all)
  local st = {}
  for i = 1, keepN do st[i] = all[i] end
  st.here = all.here
  local count = #st
  local head = fmt("Trajet %s · %s", minutes(total), km(yd))
  local E = P.Engine
  local verdict = route.verdict or "ok"
  local xph = (tonumber(route.xph) or 0) > 0 and E and E.XPHTrusted and E.XPHTrusted(route.xph, route.minutes,
    { class = T.PlayerClass and T:PlayerClass() or nil, level = tonumber((try(G("UnitLevel"), "player"))) or 1 }) or nil
  lines[#lines + 1] = { text = head, prio = (E and E.PRIO and E.PRIO[verdict]) or 1, onClick = togg, id = "route:head",
    tooltip = fmt("%s%s · dont %s de déplacement%s\nClic : mettre en pause",
      (T.Plain and T.Plain.RouteName and T.Plain.RouteName(route.id, route.title)) or cut(route.title or route.id or "Plan", 30),
      xph and (" · " .. ((T.Fmt and T.Fmt.Num) and T.Fmt.Num(xph) or xph) .. " XP/h") or "",
      minutes(travelH or min(all.travel or 0, total)),
      beyond > 0 and fmt("\nPuis %d arrêt%s de plus, recalculé%s en route", beyond, beyond > 1 and "s" or "", beyond > 1 and "s" or "") or "") }
  if P.planError then lines[#lines + 1] = { text = P.planError, prio = 1, id = "route:stale" } end
  if verdict ~= "ok" and P.VerdictText then
    lines[#lines + 1] = { text = P.VerdictText(verdict, route.reasons), prio = (E and E.PRIO and E.PRIO[verdict]) or 2 }
  end
  -- (ZONEPLAN) zone de travail (menu) + détour proposé
  local Zp = T.ZonePlan
  if Zp and Zp.HeadLines then
    local ok, zl = pcall(Zp.HeadLines, Zp)
    if ok and type(zl) == "table" then for _, l in ipairs(zl) do lines[#lines + 1] = l end end
  end
  -- guidage : Suivi Forever (SPF) ou boussole
  local following = self:Following()
  if spf() then
    if following then
      -- (4.0) le numéro d'arrêt de l'onglet / de la carte (R.Groups), pas l'index du point chez SPF (un par
      -- objectif) ; CurrentStop peut aussi rendre { index = n }
      local k = type(following) == "number" and following or (type(following) == "table" and tonumber(following.index or following.i)) or 1
      local cs = self:CurrentStep()
      if cs then
        local num = R.Groups(st)
        for i, e in ipairs(st) do if e.step == cs then k = num[i] break end end
      end
      lines[#lines + 1] = { text = fmt("Suivi Forever te guide · arrêt %d", k), prio = 1,
        tooltip = "Shortest Path Forever suit l'ordre TreeAI\nClic : renvoyer le trajet", onClick = function() R:Follow() end }
    else
      lines[#lines + 1] = { text = "▶ Suivre ce trajet (Suivi Forever)", prio = 2, onClick = function() R:Follow() end,
        tooltip = "Envoie les arrêts dans l'ordre TreeAI à Shortest Path Forever :\nun seul trajet, vols et bateaux compris." }
    end
  end
  -- tri des quêtes
  local sel = self.selection
  local prop = sel and R.ProposalText(sel.keep, sel.drop, sel.zone)
  if prop then
    -- (QA-QUEST F23) 8 lignes au plus dans l'infobulle, puis « … et 22 autres »
    local tip = {}
    for i, d in ipairs(sel.drop) do
      if i > 8 then tip[#tip + 1] = fmt("… et %d autre%s", #sel.drop - 8, #sel.drop - 8 > 1 and "s" or "") break end
      tip[#tip + 1] = "• " .. cut(d.title, 40) .. " — " .. d.why
    end
    lines[#lines + 1] = { text = prop, prio = 2, onClick = function() R:Adopt() end,
      tooltip = tconcat(tip, "\n") .. "\nClic : adopter (rien n'est abandonné)" }
  elseif sel and (sel.dropped or 0) > 0 then
    lines[#lines + 1] = { text = fmt("%d quête%s mise%s de côté · clic : tout reprendre", sel.dropped,
      sel.dropped > 1 and "s" or "", sel.dropped > 1 and "s" or ""), prio = 1, onClick = function() R:ResetDrops() end }
  end
  -- arrêts (ZONEPLAN : objectifs du même coin = un seul arrêt numéroté « A + B ») ; (QA-QUEST F18) mêmes numéros
  -- que la page Trajet et la carte du monde (R.Groups)
  local num = R.Groups(st)
  local groups = {}
  for i, e in ipairs(st) do
    local last = groups[#groups]
    if last and num[i] == num[i - 1] then last.more[#last.more + 1] = e
    else groups[#groups + 1] = { e = e, more = {} } end
  end
  count = num.n
  local shown = 0
  for k, gr in ipairs(groups) do
    local e = gr.e
    if shown >= 6 then
      lines[#lines + 1] = { text = fmt("… %d autre%s arrêt%s", count - k + 1, count - k + 1 > 1 and "s" or "", count - k + 1 > 1 and "s" or ""), prio = 1 }
      break
    end
    local txt, click, rclick
    if e.kind == "quest" or e.step then
      local s = e.step
      txt = (s and P:StepLabel(s) or "?") .. (s and progressTxt(P, s) or "")
      for _, o in ipairs(gr.more) do
        local s2 = o.step
        txt = txt .. " + " .. (s2 and P:StepLabel(s2) or "?") .. (s2 and progressTxt(P, s2) or "")
      end
      if s and s.out then txt = txt .. " (hors zone)" end
      click = function() P:Navigate(s) end
      if s and Zp and Zp.StopMenu and Zp:IsEnabled() then rclick = function() Zp:StopMenu(s) end end
    elseif e.kind == "hs" then
      -- (QA-QUEST F16) on se téléporte : ni direction ni distance vers l'auberge, un seul temps
      txt = e.text .. " (" .. minutes(e.sec) .. ")"
    else
      txt = e.text .. " · " .. minutes(e.sec)
      local p = e.from or e.place                    -- vol / bateau : guider vers le départ (maître de vol, quai)
      click = p and p.map and function() P:Navigate({ k = "fly", map = p.map, x = p.x, y = p.y, label = e.text }) end or nil
    end
    local line = { text = fmt("Arrêt %d/%d : %s", k, count, txt), prio = k == 1 and 2 or 1, onClick = click, onRightClick = rclick }
    if k == 1 then
      line.id, line.pin = "plan:step", true
      local Pl = T.Plain                             -- mots simples (agent SIMPLE) : « tout près, au sud (≈ 3 min) »
      local dest = e.kind ~= "hs" and (e.from or e.place) or nil
      if dest and st.here and dest.c == st.here.c then
        local d = sqrt((dest.n - st.here.n) ^ 2 + (dest.w - st.here.w) ^ 2)
        local w = Pl and Pl.Where and Pl.Where(d, dirTo(st.here, dest)) or nil
        if w then line.text = line.text .. " " .. w
        elseif not Pl then line.text = line.text .. fmt(" — %s %s", km(d), dirTo(st.here, dest) or "") end
      end
      -- (QUESTQA) « (≈ 6 min) » = fini dans ~6 min À PARTIR DE MAINTENANT (trajet + sur place), plus le seul
      -- temps sur place (« ≈ 1 min » pour un arrêt à 2 km : faux)
      local done = e.at or e.sec
      local tm = e.kind ~= "hs" and Pl and Pl.Minutes and Pl.Minutes(done) or nil
      if tm then line.text = line.text .. " (" .. tm .. ")" end
      local travel = max(0, (done or 0) - (e.sec or 0))
      line.tooltip = (click and "Clic : guider vers cet arrêt" or "Utilise ta pierre de foyer : tu arrives à l'auberge")
        .. (rclick and "\nClic droit : ignorer cette quête…" or "\n/tai plan passer : étape suivante")
        .. (((done or 0) >= 30) and fmt("\nFait dans %s (trajet %s, sur place %s)", tm or minutes(done), minutes(travel),
          minutes(e.sec or 0)) or "")
    end
    lines[#lines + 1] = line
    -- technique de vitesse de l'appli (SPEED) : une ligne simple sous l'arrêt courant
    if k == 1 and e.step and type(e.step.tip) == "string" and e.step.tip ~= "" then
      lines[#lines + 1] = { text = e.step.tip, prio = 2, id = "plan:tip" }
    end
    shown = shown + 1
  end
  -- groupe (repris du Planner, inchangé)
  local wait = P.WaitFor and P:WaitFor(P.steps[cur]) or {}
  if #wait > 0 then lines[#lines + 1] = { text = "Attendez " .. tconcat(wait, ", ") .. " avant de rendre", prio = 2 } end
  local routes = P.Routes and P:Routes() or {}
  if #routes > 1 then
    local Pl = T.Plain
    local function name(r) return (Pl and Pl.RouteName and Pl.RouteName(r.id, r.title)) or cut(r.title or r.id, 24) end
    local tip = {}
    for i, r in ipairs(routes) do
      tip[#tip + 1] = fmt("%s %d. %s · %d min · %s", r == route and "●" or "○", i, name(r), r.minutes or 0,
        (E and E.LABEL and E.LABEL[r.verdict or "ok"]) or "?")
    end
    lines[#lines + 1] = { text = fmt("Choix : %s (clic : changer)", name(route)), prio = 1,
      tooltip = tconcat(tip, "\n"), onClick = function() P:CycleRoute() end }
  end
  return lines
end
function R:Render()
  local P = T.Planner
  if P and P.Render then P._sig = nil; pcall(P.Render, P) end
end

-- ===================================================================== cycle de vie
-- (3.9.8) ménage : les anciennes versions empilaient nos trajets dans Shortest Path Forever (jusqu'à ~50 → carte
-- qui rame). Au chargement, hors combat, on retire TOUS les trajets au nom « TreeAI » (API publique de SPF, testée).
function R.PurgeSPF()
  local S = rawget(_G, "ShortestPathForever")
  local A = type(S) == "table" and S.API or nil
  if type(A) ~= "table" then return 0 end
  local Cm = T.Compass
  local n = 0
  local function c(fn, ...)
    if type(A[fn]) ~= "function" then return nil end
    if Cm and Cm.SPFCall then return Cm.SPFCall(A, fn, ...) end
    return try(A[fn], ...)
  end
  for _, fn in ipairs({ "CancelAll", "ClearRoutes", "ClearAll" }) do
    if type(A[fn]) == "function" then c(fn, OWNER) n = n + 1 end
  end
  for _, fn in ipairs({ "Cancel", "StopRoute", "ClearRoute" }) do
    if type(A[fn]) == "function" then
      for _ = 1, 60 do c(fn, OWNER) n = n + 1 end
      break
    end
  end
  return n
end

function R:OnEnable()
  T:After(6, function()
    if T.RunOutOfCombat then T:RunOutOfCombat("route-purge", R.PurgeSPF) else pcall(R.PurgeSPF) end
  end)
  self:RegisterUnitEvent("UNIT_SPELLCAST_SUCCEEDED", "player", function(_, _, _, _, spellID)
    if tonumber(spellID) == HEARTH_SPELL then R._hearthAt = now() end
  end)
  local function landed()
    if not (R._hearthAt and now() - R._hearthAt < 90) then return end
    R._hearthAt = nil
    T:After(2, function()
      local name = try(G("GetBindLocation"))
      local m, x, y
      if Geo.Player then m, x, y = Geo.Player() end
      if name and m and x and R.char then R.char.bind[name] = { map = m, x = x, y = y } end
    end)
  end
  self:RegisterEvent("PLAYER_ENTERING_WORLD", landed)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", landed)
  self:RegisterEvent("TAXIMAP_OPENED", function() knownT = nil end)
  self:On("COMBAT_END", function()
    if R._resend then R._resend = false; T:After(1, function() R:OnPlanStep() end) end
  end)
  local A = spf()
  if A and type(A.Subscribe) == "function" and not self._unsub then
    self._unsub = call("Subscribe", function(changes)
      if type(changes) == "table" and (changes.ended or changes.stop or changes.changed) then
        if R.following and not call("CurrentStop", OWNER) and call("Ended", OWNER) then R.following = nil end
        R:Render()
      end
    end) or true
  end
end
function R:OnDisable()
  if self.following then call("Cancel", OWNER) end
  self.following = nil
end

T:RegisterSlash("route", function(rest)
  rest = low(rest):match("^%s*(.-)%s*$")
  if rest == "pause" or rest == "reprendre" then R:TogglePause()
  elseif rest == "garde" or rest == "adopter" then R:Adopt()
  elseif rest == "tout" then R:ResetDrops()
  else R:Follow() end
end, "route : suivre le trajet (Suivi Forever / boussole) · pause · garde · tout")
