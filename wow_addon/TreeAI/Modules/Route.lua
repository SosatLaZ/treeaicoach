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
-- (QUESTS 4.1) nom d'un maître de vol / quai à l'écran : celui du jeu (carte des vols, en français) quand on l'a lu ; sinon
-- celui de la table, sauf les quelques capitales que la table écrit en anglais (« Vol pour Thunder Bluff », capture 4.0.5)
-- → le nom de leur carte dans le client
local ENGLISH_NAME = { ["Thunder Bluff"] = true, ["Undercity"] = true, ["Stormwind"] = true, ["Ironforge"] = true,
  ["Darnassus"] = true, ["Silvermoon"] = true, ["Booty Bay"] = true, ["Ratchet"] = true }
R.taxiNames = {}
function R.PlaceName(kind, id, r)
  local n = kind == "taxi" and (R.taxiNames[id] or (R.char and type(R.char.taxiName) == "table" and R.char.taxiName[id])) or nil
  if type(n) == "string" and n ~= "" then return n end
  n = r and r[1]
  if ENGLISH_NAME[n or ""] then
    local i = try(C("C_Map", "GetMapInfo"), r[5])
    local mn = type(i) == "table" and i.name or nil
    if type(mn) == "string" and mn ~= "" and mn ~= n then return mn end
  end
  return n
end
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
      pl[#pl + 1] = { kind = "taxi", id = id, name = R.PlaceName("taxi", id, r), c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7] }
    end
  end
  tsort(pl, function(a, b) return a.id < b.id end)
  for id, r in pairs(D.docks or {}) do
    pl[#pl + 1] = { kind = "dock", id = id, name = R.PlaceName("dock", id, r), c = r[2], n = r[3], w = r[4], map = r[5], x = r[6], y = r[7] }
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
        local dd = sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2)
        local det = o.detourOf and o.detourOf(b.map) or detour
        Hi[j], Ni[j], Mi[j] = dd * det / speed + ((o.mountUp and dd > 60) and o.mountUp or 0), j, "walk"
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
  -- (ROUTEFIX) vol appris (Export : durée réelle chronométrée) entre deux maîtres de vol connus, même sans vol direct
  -- dans la table (le jeu passe par des relais) : la durée mesurée compte
  for k, sec in pairs(o.flights or {}) do
    local fa, fb = tostring(k):match("^(%d+)%-(%d+)$")
    local i, j = idx["taxi" .. tostring(fa)], idx["taxi" .. tostring(fb)]
    sec = tonumber(sec)
    if i and j and i ~= j and sec and sec > 0 then
      F[i] = F[i] or {}
      F[i][j] = sec
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
           hs = (D.hearthCast or 10) + (D.hearthLoad or 5), detourOf = o.detourOf, mountUp = o.mountUp, learned = o.learned }
end

-- (ROUTEFIX) marche = distance × détour DE LA ZONE du point d'arrivée (relief : NavTime, appris > a priori) ÷ vitesse
-- (monté quand tu as une monture : NavTime.TravelSpeed) + le temps de monter en selle sur un vrai trajet
local function walkS(g, a, b)
  if a.c ~= b.c then return huge end
  local d = sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2)
  local det = g.detourOf and g.detourOf(b.map or a.map) or g.detour
  return d * det / g.speed + ((g.mountUp and d > 60) and g.mountUp or 0)
end
-- trajet à pied direct a → b : le chemin APPRIS (PathLearn : virages, rampes, relief) quand les deux bouts sont près
-- d'endroits déjà parcourus, sinon le modèle ci-dessus
local function walkDirect(g, a, b)
  if a.c ~= b.c then return huge end
  local PL = T.PathLearn
  if PL and PL.LegSec and g.learned ~= false then
    local s = PL.LegSec(a, b, g.speed)
    if s then return s + ((g.mountUp and s * g.speed > 60) and g.mountUp or 0) end
  end
  return walkS(g, a, b)
end
R.WalkDirect = walkDirect
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
  local direct = walkDirect(g, a, b)
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

-- ===================================================================== ordre des arrêts (ROUTEFIX 4.1)
-- « Le chemin le plus court qui marche réellement » : l'ordre des arrêts est EXACT jusqu'à R.EXACT_N arrêts (Held-Karp :
-- programmation dynamique sur les sous-ensembles, avec l'état « pierre de foyer déjà utilisée »), puis une bonne
-- heuristique au-delà (insertion la moins chère + Or-opt + 2-opt, plusieurs départs). Mêmes règles partout :
--  * précédences : prendre → objectifs → rendre, pour chaque quête (node.qs = { [q] = rang 1|2|3 }) ;
--  * journal jamais plein en route (prise +1, remise −1, o.cap / o.logCount) ;
--  * pierre de foyer : UNE fois (1 h de recharge), à n'importe quel trajet du parcours, seulement si elle fait gagner du
--    temps (avant : comptée à chaque trajet en « Rendre mes quêtes », ou au seul premier trajet sinon) ;
--  * stabilité : l'ordre en place reste tant que le nouveau ne gagne pas > 10 % ET > 30 s (R.Choose).
-- Coût d'un ordre = Σ trajets (D[i][j], D[0][j] depuis le joueur) − la meilleure économie de foyer (D.hs[j] = foyer +
-- trajet auberge → j). Pur, testable (tests/wow/routefix_scenario.lua : comparé à la force brute).
R.EXACT_N = 10
R.HYST, R.HYST_MIN = 0.10, 30
local pow2 = {}
do local p = 1 for k = 1, 31 do pow2[k] = p; p = p * 2 end end
local function hasBit(mask, k) return floor(mask / pow2[k]) % 2 == 1 end
-- ordre valide : pour chaque quête, prendre (1) avant objectifs (2) avant remise (3)
local function N_valid(nodes, ord)
  local maxR = {}
  for _, i in ipairs(ord) do
    for q, r in pairs(nodes[i].qs or {}) do
      if (maxR[q] or 0) > r then return false end
      if r > (maxR[q] or 0) then maxR[q] = r end
    end
  end
  return true
end
-- garde l'ordre en place sauf gain réel : > 10 % du trajet et > 30 s
function R.Choose(newCost, oldCost)
  if not oldCost then return "new" end
  local gain = oldCost - newCost
  if gain > R.HYST_MIN and gain > R.HYST * oldCost then return "new" end
  return "old"
end
-- contexte local d'un sous-ensemble d'arrêts (ord = indices de nodes) : coûts, précédences, journal
local function orderCtx(nodes, ord, D, o)
  local n = #ord
  local cx = { n = n, c0 = {}, c = {}, hs = nil, pred = {}, delta = {}, cap = o and o.cap, log = (o and o.logCount) or 0 }
  local hsT = D.hs
  if type(hsT) == "table" and next(hsT) then cx.hs = {} end
  for a = 1, n do
    local ia = ord[a]
    cx.c0[a] = (D[0] and D[0][ia]) or 0
    if cx.hs then cx.hs[a] = hsT[ia] or huge end
    local row, Da = {}, D[ia]
    for b = 1, n do row[b] = (a ~= b and Da and Da[ord[b]]) or 0 end
    cx.c[a] = row
    local nd = nodes[ia]
    local d = 0
    for _, s in ipairs(nd.steps or {}) do d = d + ((s.k == "pick" and 1) or (s.k == "turn" and -1) or 0) end
    cx.delta[a] = nd.delta or d
  end
  -- a doit précéder b quand une même quête y a un rang plus petit
  for b = 1, n do
    local pb = {}
    for a = 1, n do
      if a ~= b then
        for q, r in pairs(nodes[ord[a]].qs or {}) do
          local rb = (nodes[ord[b]].qs or {})[q]
          if rb and r < rb then pb[#pb + 1] = a break end
        end
      end
    end
    cx.pred[b] = pb
  end
  return cx
end
-- coût exact d'un ordre local (positions 1..n) : Σ trajets − meilleure économie de foyer → coût, position du foyer
local function evalLocal(cx, seq)
  local s, best, at, prev = 0, 0, nil, 0
  local hs, c0, c = cx.hs, cx.c0, cx.c
  for i = 1, #seq do
    local k = seq[i]
    local lg = (prev == 0) and c0[k] or c[prev][k]
    s = s + lg
    if hs then
      local save = lg - hs[k]
      if save > best then best, at = save, i end
    end
    prev = k
  end
  return s - best, at
end
local function validLocal(cx, seq)
  local pos = {}
  for i = 1, #seq do pos[seq[i]] = i end
  local lg, cap = cx.log, cx.cap
  for i = 1, #seq do
    local k = seq[i]
    for _, a in ipairs(cx.pred[k]) do if not pos[a] or pos[a] > i then return false end end
    local d = cx.delta[k]
    if cap and d > 0 and lg + d > cap then return false end
    lg = lg + d
  end
  return true
end
-- Held-Karp exact (n ≤ R.EXACT_N) : dp[(mask, dernier, foyer)] ; → ordre local, coût (nil si aucun ordre valide)
local function heldKarp(cx, yield)
  local n, c0, c, hs = cx.n, cx.c0, cx.c, cx.hs
  local full = pow2[n + 1] - 1
  local dp, par = {}, {}
  local predM = {}
  for k = 1, n do
    local m = 0
    for _, a in ipairs(cx.pred[k]) do m = m + pow2[a] end
    predM[k] = m
  end
  local logOf = { [0] = cx.log }
  local function contains(mask, sub)              -- sub ⊆ mask (sub petit : quelques bits)
    if sub == 0 then return true end
    local k = 1
    while sub > 0 do
      if sub % 2 == 1 and not hasBit(mask, k) then return false end
      sub, k = floor(sub / 2), k + 1
    end
    return true
  end
  local function key(mask, last, h) return (mask * 16 + last) * 2 + h end
  local cap = cx.cap
  for k = 1, n do
    if predM[k] == 0 and not (cap and cx.delta[k] > 0 and cx.log + cx.delta[k] > cap) then
      local m = pow2[k]
      dp[key(m, k, 0)] = c0[k]
      if hs and hs[k] < huge then dp[key(m, k, 1)] = hs[k] end
    end
  end
  local ops = 0
  for mask = 1, full do
    local lg = logOf[mask]
    if not lg then                                   -- journal après les arrêts du masque
      local low = 1
      while not hasBit(mask, low) do low = low + 1 end
      lg = (logOf[mask - pow2[low]] or cx.log) + cx.delta[low]
      logOf[mask] = lg
    end
    for last = 1, n do
      if hasBit(mask, last) then
        for h = 0, 1 do
          local v = dp[key(mask, last, h)]
          if v then
            local cl = c[last]
            for j = 1, n do
              if not hasBit(mask, j) and contains(mask, predM[j]) and not (cap and cx.delta[j] > 0 and lg + cx.delta[j] > cap) then
                local nm = mask + pow2[j]
                local k1 = key(nm, j, h)
                local nv = v + cl[j]
                local ov = dp[k1]
                if not ov or nv < ov - 1e-9 then dp[k1], par[k1] = nv, key(mask, last, h) end
                if h == 0 and hs and hs[j] < huge then
                  local k2 = key(nm, j, 1)
                  local hv = v + hs[j]
                  local ov2 = dp[k2]
                  if not ov2 or hv < ov2 - 1e-9 then dp[k2], par[k2] = hv, key(mask, last, h) end
                end
              end
            end
            ops = ops + n
            if yield and ops >= 400 then ops = 0; yield() end
          end
        end
      end
    end
  end
  local best, bk = nil, nil
  for last = 1, n do
    for h = 0, 1 do
      local v = dp[key(full, last, h)]
      if v and (not best or v < best - 1e-9) then best, bk = v, key(full, last, h) end
    end
  end
  if not bk then return nil end
  local seq = {}
  local k = bk
  while k do
    local last = floor(k / 2) % 16
    seq[#seq + 1] = last
    k = par[k]
  end
  local out = {}
  for i = #seq, 1, -1 do out[#out + 1] = seq[i] end
  return out, best
end
-- heuristique (n > R.EXACT_N) : Or-opt (1–3 arrêts) + 2-opt en temps constant par essai (sommes préfixées, trajet
-- d'arrivée au foyer fixé pendant une passe, puis le meilleur foyer recalculé exactement), validité vérifiée seulement
-- pour un essai qui gagne ; quelques passes au plus
local function improveLocal(cx, seq, yield, passes)
  local n = #seq
  local hs, c0, c = cx.hs, cx.c0, cx.c
  local cur, at = evalLocal(cx, seq)
  local hsNode = at and seq[at] or nil
  local function leg(a, b)
    if b == nil then return 0 end
    if hs and b == hsNode then return hs[b] end
    if a == 0 then return c0[b] end
    return c[a][b]
  end
  local ops = 0
  local function tick() ops = ops + 1 if yield and ops >= 200 then ops = 0; yield() end end
  for _ = 1, passes or 6 do
    local improved = false
    -- Or-opt : déplacer seq[s..e] entre u = seq[t] et v = seq[t + 1]
    for L = 1, 3 do
      local s0 = 1
      while s0 + L - 1 <= n do
        local e = s0 + L - 1
        local p, q = (s0 == 1) and 0 or seq[s0 - 1], seq[e + 1]
        local first, last = seq[s0], seq[e]
        local rem = -leg(p, first) - leg(last, q) + (q and leg(p, q) or 0)
        local bestT, bestD = nil, -0.5
        for t = 0, n do
          if t < s0 - 1 or t > e then
            local u, v = (t == 0) and 0 or seq[t], seq[t + 1]
            if t == e then v = nil end
            local d = rem + leg(u, first) + leg(last, v) - (v and leg(u, v) or 0)
            if d < bestD then
              local trial = {}
              for k = 1, n do if k < s0 or k > e then trial[#trial + 1] = seq[k] end end
              local pos = (t < s0) and t or (t - L)
              for k = L, 1, -1 do table.insert(trial, pos + 1, seq[s0 + k - 1]) end
              if validLocal(cx, trial) then bestT, bestD = trial, d end
            end
            tick()
          end
        end
        if bestT then seq, improved = bestT, true end
        s0 = s0 + 1
      end
    end
    -- 2-opt : inverser seq[i..j] (coûts asymétriques : sommes avant / arrière préfixées)
    local F, Rv = { 0 }, { 0 }
    local function prefix()
      F[1], Rv[1] = 0, 0
      for k = 2, n do F[k] = F[k - 1] + leg(seq[k - 1], seq[k]); Rv[k] = Rv[k - 1] + leg(seq[k], seq[k - 1]) end
    end
    prefix()
    for i = 1, n - 1 do
      for j = i + 1, n do
        local a0, b0 = (i == 1) and 0 or seq[i - 1], seq[j + 1]
        local old = leg(a0, seq[i]) + (F[j] - F[i]) + leg(seq[j], b0)
        local new = leg(a0, seq[j]) + (Rv[j] - Rv[i]) + leg(seq[i], b0)
        if new < old - 0.5 then
          local trial = {}
          for k = 1, i - 1 do trial[k] = seq[k] end
          for k = i, j do trial[k] = seq[j - (k - i)] end
          for k = j + 1, n do trial[k] = seq[k] end
          if validLocal(cx, trial) then seq, improved = trial, true; prefix() end
        end
        tick()
      end
    end
    local nc, nat = evalLocal(cx, seq)
    if not improved or nc >= cur - 0.5 then cur = math.min(cur, nc) break end
    cur, hsNode = nc, nat and seq[nat] or nil
  end
  return seq, evalLocal(cx, seq)
end
-- départ glouton valide : le plus proche parmi les arrêts dont les précédents sont faits (et le journal le permet)
local function insertionLocal(cx)
  local seq, placed, prev, lg = {}, {}, 0, cx.log
  for _ = 1, cx.n do
    local best, bc = nil, huge
    for k = 1, cx.n do
      if not placed[k] then
        local ok = not (cx.cap and cx.delta[k] > 0 and lg + cx.delta[k] > cx.cap)
        if ok then for _, a in ipairs(cx.pred[k]) do if not placed[a] then ok = false break end end end
        if ok then
          local v = (prev == 0) and cx.c0[k] or cx.c[prev][k]
          if v < bc then best, bc = k, v end
        end
      end
    end
    if not best then return nil end
    seq[#seq + 1], placed[best], prev, lg = best, true, best, lg + cx.delta[best]
  end
  return seq
end
--- Meilleur ordre de ord (indices de nodes) sur la matrice D (D[0][j] depuis le joueur, D[i][j], D.hs[j] foyer → j).
--- o = { cap, logCount, yield, exactN } → nouvel ordre (table neuve, indices de nodes), coût, position du foyer, "exact"|"heur"
function R.Solve(nodes, ord, D, o)
  o = o or {}
  local n = #(ord or {})
  if n == 0 then return {}, 0, nil, "exact" end
  local cx = orderCtx(nodes, ord, D, o)
  local seq, how
  if n <= (o.exactN or R.EXACT_N) then
    seq = heldKarp(cx, o.yield)
    how = "exact"
  end
  if not seq then
    local starts = {}
    local given = {}
    for i = 1, n do given[i] = i end
    if validLocal(cx, given) then starts[#starts + 1] = given end
    local ins = insertionLocal(cx)
    if ins then starts[#starts + 1] = ins end
    local bestC
    for _, s0 in ipairs(starts) do
      local s1, c1 = improveLocal(cx, s0, o.yield, o.passes)
      if not bestC or c1 < bestC then seq, bestC = s1, c1 end
    end
    how = "heur"
  end
  if not seq then return nil end
  local cost, at = evalLocal(cx, seq)
  local out = {}
  for i, k in ipairs(seq) do out[i] = ord[k] end
  return out, cost, at, how
end
--- (ROUTEFIX) quêtes à prendre EN CHEMIN : chaque arrêt facultatif (prise d'une quête disponible : node.value = XP
--- attendue, node.svc = secondes sur place) est inséré à sa place la moins chère dans l'ordre (validité gardée : avant
--- ses objectifs / sa remise s'ils sont dans l'ordre) quand le détour (trajet en plus + sur place) vaut moins que son XP
--- au rythme du trajet (rate = XP par seconde) — on le prend en passant devant son PNJ, jamais un aller-retour plus tard.
--- D doit couvrir les arrêts facultatifs. → ordre (en place), liste des insérés
function R.InsertOptional(nodes, ord, D, optional, rate, o)
  o = o or {}
  rate = math.max(1e-6, tonumber(rate) or 0)
  local added = {}
  for _, x in ipairs(optional or {}) do
    local base = R.OrderCost(D, ord)
    local bestAt, bestC = nil, huge
    for at = 0, #ord do
      table.insert(ord, at + 1, x)
      if N_valid(nodes, ord) then
        local c = R.OrderCost(D, ord) - base
        if c < bestC then bestAt, bestC = at, c end
      end
      table.remove(ord, at + 1)
    end
    local nd = nodes[x]
    local cost = bestC + (tonumber(nd.svc) or 15)
    if bestAt and cost * rate <= (tonumber(nd.value) or 0) and (not o.maxDetour or bestC <= o.maxDetour) then
      table.insert(ord, bestAt + 1, x)
      added[#added + 1] = { i = x, at = bestAt + 1, detour = bestC }
    end
  end
  return ord, added
end
--- coût exact d'un ordre (indices de nodes) sur D : Σ trajets − meilleure économie de foyer → coût, position du foyer
function R.OrderCost(D, ord)
  local s, best, at, prev = 0, 0, nil, 0
  local hs = type(D.hs) == "table" and D.hs or nil
  for i, j in ipairs(ord) do
    local lg = (D[prev] and D[prev][j]) or 0
    s = s + lg
    if hs and hs[j] then
      local save = lg - hs[j]
      if save > best then best, at = save, i end
    end
    prev = j
  end
  return s - best, at
end
-- force brute (tests, ≤ 9 arrêts) : même modèle de coût, toutes les permutations valides
function R.BruteForce(nodes, ord, D, o)
  local cx = orderCtx(nodes, ord, D, o or {})
  local n = cx.n
  local best, bestSeq = nil, nil
  local seq, used = {}, {}
  local function rec(depth)
    if depth > n then
      if validLocal(cx, seq) then
        local c = evalLocal(cx, seq)
        if not best or c < best then
          best, bestSeq = c, {}
          for i = 1, n do bestSeq[i] = ord[seq[i]] end
        end
      end
      return
    end
    for k = 1, n do
      local ok = not used[k]
      if ok then for _, a in ipairs(cx.pred[k]) do if not used[a] then ok = false break end end end
      if ok then
        used[k] = true; seq[depth] = k
        rec(depth + 1)
        used[k] = false; seq[depth] = nil
      end
    end
  end
  rec(1)
  return bestSeq, best
end
-- (ROUTEFIX) moteur du trajet par zone (ZonePlan.Engine) branché sur ce solveur : même contrat que ses fonctions
-- (Matrix → D, Improve(ord) en place, Cost, Choose), sans toucher au fichier de ZonePlan. Matrix : trajets SANS foyer
-- (Planner.Leg) + D.hs (foyer → arrêt) quand la pierre est prête et l'auberge connue ; Improve = R.Solve (exact ≤ 10
-- arrêts) ; Cost = coût exact (foyer une seule fois) ; Choose = > 10 % et > 30 s. Rien n'est remplacé deux fois.
function R.InstallEngine(N)
  if type(N) ~= "table" or N._routefix or type(N.Matrix) ~= "function" or type(N.Improve) ~= "function" then return false end
  N._routefix = true
  local oldMatrix, oldImprove, oldCost = N.Matrix, N.Improve, N.Cost
  N._old = { Matrix = oldMatrix, Improve = oldImprove, Cost = oldCost, Choose = N.Choose }   -- (audit / tests)
  N.Matrix = function(nodes, ord, start, leg, first, yield)
    local P = T.Planner
    local Zp = T.ZonePlan
    if not (R:IsEnabled() and type(leg) == "function") then return oldMatrix(nodes, ord, start, leg, first, yield) end
    -- le trajet donné par ZonePlan, sauf en « Rendre mes quêtes » où il compte le foyer à CHAQUE trajet : le trajet sans
    -- foyer du Planner (le foyer est ajouté une seule fois, ci-dessous)
    local plain = (Zp and Zp.hearthAny and P and type(P.Leg) == "function") and P.Leg or leg
    local D = { [0] = {}, routefix = true }
    for a = 1, #ord do
      local na = nodes[ord[a]]
      D[0][ord[a]] = (start and start.x) and plain(start, na) or (first and first(na)) or leg(start, na)
      local row = {}
      D[ord[a]] = row
      for b = 1, #ord do
        if a ~= b then row[ord[b]] = plain(na, nodes[ord[b]]) end
        if yield then yield() end
      end
    end
    -- pierre de foyer : ZonePlan l'a jugée prête (first) ou l'a retirée pour « un arrêt à moins de 5 min » (noHearth) ;
    -- ici, le coût exact décide (une fois, au meilleur trajet)
    if start and start.map and (first or (Zp and Zp.noHearth)) and opt("hearth") then
      local bind = R.BindPlace()
      local Dt = data()
      if type(bind) == "table" and bind.map and bind.x then
        local hsS = ((Dt and Dt.hearthCast) or 10) + ((Dt and Dt.hearthLoad) or 5)
        local b = { map = bind.map, x = bind.x, y = bind.y }
        D.hs = {}
        for a = 1, #ord do
          local nd = nodes[ord[a]]
          if nd.x then D.hs[ord[a]] = hsS + plain(b, nd) end
        end
      end
    end
    local cap = tonumber(try(C("C_QuestLog", "GetMaxNumQuestsCanAccept"))) or 20
    local lc = 0
    for _ in pairs((P and P.log) or {}) do lc = lc + 1 end
    D.cap, D.logCount = cap - 2, lc
    return D
  end
  N.Improve = function(nodes, ord, D, yield, passes)
    if not (type(D) == "table" and D.routefix) then return oldImprove(nodes, ord, D, yield, passes) end
    local out, cost, at, how = R.Solve(nodes, ord, D, { cap = D.cap, logCount = D.logCount, yield = yield })
    if not out then return oldImprove(nodes, ord, D, yield, passes) end
    for i = 1, #ord do ord[i] = out[i] end
    R.lastSolve = { n = #ord, cost = cost, hsAt = at, how = how }
    return ord
  end
  N.Cost = function(D, ord)
    if type(D) == "table" and D.routefix then return (R.OrderCost(D, ord)) end
    return oldCost(D, ord)
  end
  N.Choose = R.Choose
  N.HYST, N.HYST_MIN = R.HYST, R.HYST_MIN
  return true
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
          if type(nd.name) == "string" and nd.name ~= "" then R.taxiNames[nd.nodeID] = nd.name end
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
  -- (NAV) marche = TA vitesse mesurée × détour appris de la zone (NavTime), arrondis pour garder le cache ;
  -- (ROUTEFIX) vitesse de déplacement réelle (monté quand tu as une monture, NavTime.TravelSpeed), détour de la zone DE
  -- CHAQUE trajet (relief a priori des zones de montagne, remplacé par ce que tu as appris), plus celui de ta zone actuelle
  local NT, speed, rev, mountUp = T.NavTime, nil, 0, nil
  local detourOf
  if NT and NT.Speed then
    speed = floor(((NT.TravelSpeed and NT.TravelSpeed()) or NT.Speed("w")) * 2 + 0.5) / 2
    mountUp = NT.MountUp and NT.MountUp() or nil
    rev = NT.rev or 0
    local memo = {}
    detourOf = function(m)
      if m == nil then return (data() or {}).detour or 1.3 end
      local v = memo[m]
      if not v then v = floor(NT.Detour(m) * 20 + 0.5) / 20; memo[m] = v end
      return v
    end
  end
  if hubCache and hubCache.known == known and hubCache.faction == faction and hubCache.tr == opt("transports")
      and hubCache.speed == speed and hubCache.rev == rev and hubCache.mountUp == mountUp then
    return hubCache.g                                -- (ROUTEFIX) sans chaîne construite à chaque appel (boussole ≈ 1 Hz)
  end
  local key = tostring(faction) .. "|" .. tconcat(known, ",") .. "|" .. tostring(opt("transports")) .. "|"
    .. tostring(speed) .. "|" .. tostring(rev) .. "|" .. tostring(mountUp)
  if hubCache and hubCache.key == key then
    hubCache.known = known
    return hubCache.g
  end
  local g = R.BuildHubs(faction, known, { flights = learnedFlights(), speed = speed, detourOf = detourOf, mountUp = mountUp })
  hubCache = { key = key, g = g, known = known, faction = faction, tr = opt("transports"), speed = speed, rev = rev, mountUp = mountUp }
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
  if not (opt("transports") and a and b and tonumber(a.x) and tonumber(b.x) and tonumber(a.y) and tonumber(b.y)) then return nil end
  local g = R.Graph()
  if not g then return nil end
  local key = fmt("%s:%.0f:%.0f>%s:%.0f:%.0f", tostring(a.map), a.x, a.y, tostring(b.map), b.x, b.y)
  local PL = T.PathLearn
  local plRev = PL and PL.rev or 0
  -- (ROUTEFIX) un chemin appris change les trajets : cache refait, au plus toutes les 30 s (pas à chaque case apprise)
  if R.legRev ~= plRev and now() - (R.legRevAt or -1e9) > 30 then R.legCache, R.legRev, R.legRevAt = {}, plRev, now() end
  local cache = R.legCache or {}
  R.legCache = cache
  if cache[key] then return cache[key] end
  local wa, wb = world(a), world(b)
  if not (wa and wb) then return nil end
  local direct = walkDirect(g, wa, wb)
  local s = direct
  if direct > 90 then s = R.FastestWorld(g, wa, wb) end
  if s == huge then s = nil end
  cache[key] = s
  return s
end

-- (ROUTEFIX) temps à pied / monté entre deux points de carte { mapID, x, y } (0..1 ou %) : LA formule du trajet (chemin
-- appris, détour de la zone, vitesse de déplacement) — la boussole s'en sert pour l'arrêt 1 ; nil si inconnu
function R.WalkSecMap(a, b)
  local function pt(p)
    if not (p and (p.mapID or p.map) and tonumber(p.x) and tonumber(p.y)) then return nil end
    local x, y = p.x, p.y
    if x <= 1 and y <= 1 then x, y = x * 100, y * 100 end
    return world({ map = p.mapID or p.map, x = x, y = y })
  end
  local wa, wb = pt(a), pt(b)
  if not (wa and wb and wa.c == wb.c) then return nil end
  local g = R.Graph()
  if not g then return nil end
  local s = walkDirect(g, wa, wb)
  return s < huge and s or nil
end

-- (ROUTEFIX) objectif « acheter X » : le marchand qui le vend le plus vite atteint depuis `from` ({ map, x, y } en %) —
-- marchands vus en jouant (CityRun, objets de leur vitrine) puis QuestieDB (« vendors » de l'objet, points d'apparition)
-- → { map, x, y, name, src = "vu"|"questie", sec } ou nil
function R.VendorFor(itemID, from)
  itemID = tonumber(itemID)
  if not itemID then return nil end
  local cands = {}
  local CR = T.CityRun
  local mem = CR and CR.Mem and try(CR.Mem)
  for mapKey, list in pairs(type(mem) == "table" and mem or {}) do
    for name, e in pairs(type(list) == "table" and list or {}) do
      if type(e) == "table" and type(e.k) == "table" and e.k.vendor and tonumber(e.x) and CR.ItemsOf then
        local items = try(CR.ItemsOf, e) or {}
        if items[itemID] or items[tostring(itemID)] then
          local x, y = tonumber(e.x), tonumber(e.y)
          if x and y and x <= 1 and y <= 1 then x, y = x * 100, y * 100 end
          cands[#cands + 1] = { map = tonumber(mapKey), x = x, y = y, name = name, src = "vu" }
        end
      end
    end
  end
  local Cm = T.Compass
  local db, zdb
  if Cm and Cm.Questie then db, zdb = Cm.Questie() end
  if db and db.QueryItemSingle and Cm.SpawnsOf then
    local pts = {}
    for _, npc in ipairs(try(db.QueryItemSingle, itemID, "vendors") or {}) do Cm.SpawnsOf("npc", npc, db, zdb, from and from.map, pts) end
    for _, p in ipairs(pts) do cands[#cands + 1] = { map = p.mapID, x = p.x * 100, y = p.y * 100, name = p.name, src = "questie" } end
  end
  local best, bs
  for _, c in ipairs(cands) do
    if c.map and c.x then
      local s = from and from.x and R.LegSeconds(from, c) or 0
      if s and (not bs or s < bs) then best, bs = c, s end
    end
  end
  if best then best.sec = bs end
  return best
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
-- (QUESTS 4.1) signature d'une liste d'arrêts envoyée à SPF : on ne renvoie que ce qui a changé
local function stopsSig(stops)
  local t = {}
  for i, st in ipairs(stops) do
    if i > 24 then break end
    t[#t + 1] = fmt("%s:%s:%s:%d:%d", tostring(st.kind), tostring(st.questID), tostring(st.map), floor((st.x or 0) * 1000), floor((st.y or 0) * 1000))
  end
  return tconcat(t, "|")
end
R.StopsSig = stopsSig
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
      self.following, self._sentAt, self._sentSig = true, now(), stopsSig(stops)
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
-- étape faite OU trajet refait (Planner : un nouvel ordre garde parfois le même n° d'étape — capture 4.0.5 : Suivi
-- Forever guidait encore vers « Arikara » quand la carte disait « La revanche de Gann »)
-- (ROUTEFIX) le joueur a choisi « Lui donner l'ordre de TreeAI » (Addons, gardé) : notre ordre est renvoyé à Suivi
-- Forever après CHAQUE trajet refait, même après un /reload (avant : seulement si « Suivre » avait été cliqué dans la
-- session — après un rechargement, Suivi Forever gardait son propre trajet)
function R.WantFollow()
  local Ad = T.Addons
  return (Ad and type(Ad.db) == "table" and Ad.db.spfChoice == "order" and spf()) and true or false
end
function R:OnPlanStep()
  if self.char.paused then return end
  if not self.following then
    if not (R.WantFollow() and self:HasRoute()) or inCombat() then return end
    local stops = self:SPFStops()
    if #stops == 0 then return end
    if call("NavigateRoute", OWNER, stops) then self.following, self._sentAt, self._sentSig = true, now(), stopsSig(stops) end
    return
  end
  if inCombat() then self._resend = true return end
  if self._sentAt and now() - self._sentAt < 2 then
    self._resend = true
    T:After(2.1, function() if R._resend and not inCombat() then R._resend = false; R:OnPlanStep() end end)
    return
  end
  local stops = self:SPFStops()
  if #stops == 0 then call("Cancel", OWNER); self.following, self._sentSig = nil, nil return end
  local sig = stopsSig(stops)
  if sig == self._sentSig then return end
  if call("NavigateRoute", OWNER, stops) then self._sentAt, self._sentSig = now(), sig else self.following = nil end
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
  if not ch.paused then pcall(self.OnPlanStep, self) end   -- (ROUTEFIX) reprise : l'ordre renvoyé (choix « ordre de TreeAI »)
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
  -- (ROUTEFIX) le temps sur place connu de l'arrêt (prendre / rendre 15 s) : avant, 60 % de l'écart d'« eta » — qui
  -- compte le TRAJET — passait pour du travail (3 min « sur place » pour une remise après 5 min de vol)
  if tonumber(s.svc) then return max(0, tonumber(s.svc)) end
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
-- (ROUTEFIX) UNE liste : Stops(1) (boussole, « Suivre », voyage rapide) est le début de Stops(64) (onglet, fenêtre,
-- carte) — avant, chacune calculait la sienne (pierre de foyer jugée sur le seul 1er trajet d'un côté, sur tout le
-- parcours de l'autre : arrêt 1 différent)
function R:Stops(n)
  if n and n < 64 then
    local all = self:Stops(64)
    local m = stopsMemo["v" .. n]
    if m and m.all == all then return m.out end
    local out, quests = { left = all.left, here = all.here }, 0
    for _, e in ipairs(all) do
      out[#out + 1] = e
      if e.step or e.kind == "quest" then quests = quests + 1 end
      if quests >= n then break end
    end
    local last = out[#out]
    out.total, out.travel, out.yd = last and last.at or 0, last and last.trAt or 0, last and last.ydAt or 0
    stopsMemo["v" .. n] = { all = all, out = out }
    return out
  end
  n = 64
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
-- trajet a → b sans foyer : le plus rapide (marche / vol / bateau / zeppelin), sinon le modèle de marche
local function legNoHs(g, a, b)
  local sec, legs
  if g then sec, legs = R.FastestWorld(g, a, b) end
  if not sec or sec == huge then
    local d = (a.c == b.c) and sqrt((a.n - b.n) ^ 2 + (a.w - b.w) ^ 2) or nil
    local NT = T.NavTime
    sec = d and ((NT and NT.Model) and NT.Model(d, b.map) or d * 1.3 / 7) or 0
    legs = { { mode = "walk", sec = sec, yd = d or 0, to = b } }
  end
  return sec, legs
end
-- (ROUTEFIX) la pierre de foyer : UNE fois, au trajet où elle fait gagner le plus (≥ minGain), sur tout le parcours —
-- le même coût que l'ordre des arrêts (R.Solve / R.OrderCost) ; → index de l'entrée, secondes, étapes
local function hearthChoice(g, items)
  if not (g and R.HearthReady()) then return nil end
  local bind = R.BindPlace()
  if not (type(bind) == "table" and bind.c) then return nil end
  local b = { c = bind.c, n = bind.n, w = bind.w, map = bind.map, x = bind.x, y = bind.y, name = bind.name or "auberge" }
  local best, bi, bsec, blegs = g.minGain or 30, nil, nil, nil
  for k, it in ipairs(items) do
    if it.sec and it.here then
      local s2, l2 = R.FastestWorld(g, b, it.here)
      if s2 and s2 < huge then
        local alt = g.hs + s2
        if it.sec - alt > best then
          best, bi, bsec = it.sec - alt, k, alt
          blegs = { { mode = "hs", sec = g.hs, to = b, yd = 0 } }
          for _, lg in ipairs(l2 or {}) do blegs[#blegs + 1] = lg end
        end
      end
    end
  end
  return bi, bsec, blegs
end
function R:StopsRaw()
  local P = T.Planner
  local steps, cur = P and P.steps, P and P.cursor
  local out = { total = 0, travel = 0, yd = 0 }
  if not (steps and cur) then return out end
  local g = opt("transports") and R.Graph() or nil
  local Gp = Geo.Player
  local pm, px, py
  if Gp then pm, px, py = Gp() end
  local prev = world((pm and px) and { map = pm, x = px, y = py } or nil)
  local Cm = T.Compass
  -- 1. chaque trajet, sans foyer
  local items = {}
  for i = cur, #steps do
    local s = steps[i]
    if not s._done and not s._other then
      local here = world(s.x and s or nil)
      local it = { s = s, i = i, here = here }
      if prev and here then it.from = prev; it.sec, it.legs = legNoHs(g, prev, here) end
      items[#items + 1] = it
      prev = here or prev
      if #items >= 64 then break end
    end
  end
  -- 2. la pierre de foyer, au meilleur trajet (une seule fois)
  local hi, hsec, hlegs = hearthChoice(g, items)
  if hi then items[hi].sec, items[hi].legs, items[hi].hs = hsec, hlegs, true end
  -- 3. (NAV) les 3 premiers trajets à pied : la durée navmesh de Shortest Path quand le joueur l'a choisi (hors combat)
  local spfN = 0
  for _, it in ipairs(items) do
    if spfN >= 3 then break end
    local legs, a, b = it.legs, it.from, it.here
    if legs and #legs == 1 and legs[1].mode == "walk" and Cm and Cm.Travel and a.map and b.map then
      spfN = spfN + 1
      local ss = Cm.Travel({ mapID = a.map, x = a.x / 100, y = a.y / 100 }, { mapID = b.map, x = b.x / 100, y = b.y / 100 })
      if ss then it.sec = ss legs[1].sec = ss end
    end
  end
  -- 4. la liste affichée : les transports comme arrêts, puis l'arrêt
  local prevKind
  for _, it in ipairs(items) do
    local s = it.s
    if it.legs then
      local run, trRun, lastTo = out.total, out.travel, it.from
      for _, lg in ipairs(it.legs) do
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
      out.travel, out.total = out.travel + (it.sec or 0), out.total + (it.sec or 0)
    end
    local svc = stepSvc(steps, it.i)
    out.total = out.total + svc
    local kind = TRANSPORT[s.k] and (s.k == "zep" and "zeppelin" or s.k) or "quest"
    out[#out + 1] = { kind = kind, step = s, sec = svc, place = it.here, at = out.total, ydAt = out.yd, trAt = out.travel }
    prevKind = TRANSPORT[s.k] and kind or nil
  end
  local left = 0                                   -- arrêts restants au total (au-delà de la liste aussi)
  for i = cur, #steps do if not steps[i]._done and not steps[i]._other then left = left + 1 end end
  out.left = left
  out.hsAt = hi
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
  -- (QUESTS 4.1) l'arrêt entier (même texte que la carte et l'onglet) : « Ramasse « A » 0/6, « B » 0/6 »
  local okS, st1 = pcall(R.Stops, R, 64)            -- (la liste de l'onglet, déjà mémorisée)
  if okS and type(st1) == "table" and st1[1] and st1[1].step == s then
    local okL, gl = pcall(R.StopLabel, P, st1, 1, R.Groups(st1))
    if okL and type(gl) == "string" then label = gl end
  end
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
-- (QUESTS 4.1) et des prises / remises consécutives au même PNJ (≤ 25 yd) = UN arrêt (« Rendre X + Rendre Y »)
local function samePlace(a, b)
  local sa, sb = a and a.step, b and b.step
  if not (sa and sb and sa.x and sb.x and sa.map == sb.map) then return false end
  if not ((sa.k == "pick" or sa.k == "turn") and (sb.k == "pick" or sb.k == "turn")) then return false end
  local d = Geo.Dist and Geo.Dist(sa.map, sa.x, sa.y, sb.map, sb.x, sb.y)
  return d ~= nil and d <= 25
end
function R.Groups(st, n)
  local g, k, lastArea = { n = 0 }, 0, nil
  for i = 1, min(n or #st, #st) do
    local e = st[i]
    local a = e.step and e.step.area
    if not ((a and a == lastArea) or (i > 1 and samePlace(st[i - 1], e))) then k = k + 1 end
    g[i], lastArea = k, a
  end
  g.n = k
  return g
end

-- (QUESTS 4.1) UN libellé par arrêt (onglet, carte, boussole) : les étapes de l'arrêt numéro grp[i] — même quête, même verbe :
-- « Ramasse « Nitroglycérine » 0/6, « Pulpe de bois » 0/6, « Nitrate de sodium » 0/6 » ; sinon « Rendre « X » + Rendre « Y » »
-- → libellé, index de la dernière entrée de l'arrêt
function R.StopLabel(P, st, i, grp)
  local e0 = st and st[i]
  if not (P and e0 and e0.step) then return nil, i end
  local items, last = {}, i
  for j = i, #st do
    local e = st[j]
    if j > i and not (grp and grp[j] == grp[i] and e.step) then break end
    local s = e.step
    local lbl = P:StepLabel(s)
    local lq = s.k == "obj" and s.q and P.log and P.log[s.q]
    local o = lq and lq.obj and lq.obj[tonumber(s.o) or 0]
    local prog = (o and (o.n or 0) > 0) and fmt(" %d/%d", o.have or 0, o.n) or ""
    items[#items + 1] = { verb = lbl:match("^(.-) «"), name = lbl:match("(«.*»)$"), lbl = lbl, prog = prog, q = s.q }
    last = j
  end
  if #items == 1 then return items[1].lbl .. items[1].prog, last end
  local same = items[1].verb ~= nil
  -- la forme courte (un verbe, des noms) seulement pour UNE quête ; plusieurs quêtes : « Rendre « A » + Rendre « B » »
  for _, it in ipairs(items) do if it.verb ~= items[1].verb or not it.name or it.q ~= items[1].q then same = false end end
  local parts = {}
  for k, it in ipairs(items) do parts[k] = (same and it.name or it.lbl) .. it.prog end
  if same then return items[1].verb .. " " .. tconcat(parts, ", "), last end
  return tconcat(parts, " + "), last
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
    else groups[#groups + 1] = { e = e, more = {}, i = i } end
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
      if #gr.more > 0 and gr.i then txt = R.StopLabel(P, st, gr.i, num) or txt end
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
  -- (ROUTEFIX) ordre exact / heuristique + foyer une fois + stabilité 10 % / 30 s pour le trajet par zone
  local Zp = T.ZonePlan
  if Zp and Zp.Engine then R.InstallEngine(Zp.Engine) end
  T:After(6, function()
    -- ménage des trajets empilés, PUIS (choix « ordre de TreeAI ») notre trajet renvoyé, un seul
    local function purgeThenSend() pcall(R.PurgeSPF); R.following, R._sentSig = nil, nil; pcall(R.OnPlanStep, R) end
    if T.RunOutOfCombat then T:RunOutOfCombat("route-purge", purgeThenSend) else purgeThenSend() end
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
