-- TreeAI — Zone de travail + trajet préparé (agent ZONEPLAN). Toujours un chemin prêt, dans la zone que TU choisis.
--  * « Zone de travail » : Auto (ta zone ; une voisine seulement pour compléter quand la tienne a trop peu de
--    quêtes), une liste de zones (« Zones à faire » : quêtes à ton niveau, déjà finies) ou « Toute la région » (le
--    continent). (ZONEFIX) Une zone choisie = le trajet RESTE dedans, le plus proche d'abord, ≤ 1 h ; une remise juste
--    à côté seulement si elle est sur le chemin ; le reste est dit (« Autres zones : 6 quêtes (Les Tarides, …) »).
--    Jamais d'autre continent sans ton choix ; un détour hors zone très rentable est seulement PROPOSÉ, en clair.
--  * Modes du trajet (un clic sur le titre de la carte ou de la page Trajet, par personnage) : Le plus rapide, Finir
--    la zone, Par histoire (thèmes « Orneval · Bois-brisé : la flamme sacrée (3 quêtes) » ensemble), Récompenses,
--    Groupe. Suivi auto par zone (ZoneWatch.lua) : le suivi du jeu montre les quêtes du trajet, dans son ordre.
--  * Plan en jeu, sans l'appli : arrêts (prendre / objectif / rendre ; vols, bateaux, foyer comptés par Route)
--    construits sur le journal + QuestieDB (sinon : journal seul, aux points de quête du client), objectifs
--    voisins regroupés en un seul arrêt (même coin de carte), ordre glouton (valeur / temps) puis 2-opt + Or-opt
--    avec les précédences (prendre → objectifs → rendre). Recalculé tout de suite à chaque quête prise / rendue /
--    abandonnée, objectif fini, changement de zone ou de réglage ; HYSTÉRÉSIS : l'ordre en cours reste tant que
--    le nouveau ne fait pas gagner ≥ 10 % du trajet (et ≥ 1 min) — pas de valse des arrêts pour trois mètres.
--  * Travail découpé : tourne dans la tâche du Planner (≤ 2 ms par tranche, via l'ordonnanceur du cœur), hors combat.
--  * Personnalisable (page d'options « Trajet ») : élites, quêtes de groupe, donjons (jamais seul ou à deux), écart de
--    niveau max, distance max d'un arrêt, mode du trajet, suivi auto, liste « Ignorer », Pause / Recalculer.
--  * Plan de l'appli (TreeAI_Plan) : mêmes règles (zone + quêtes ignorées) appliquées à l'affichage.
-- Aucune fonction protégée, aucun cadre Blizzard touché (menu = cadre à nous), rien en combat. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

-- (ZONEFIX 4.0.1) le trajet par zone fait partie du guidage de base : allumé en mode sûr. Coupé (bêta jusqu'en 4.0.0),
-- le Planner retombait sur son ancien plan « tout le journal » : 4 zones mêlées, quêtes de donjon, vol vers Mille
-- pointes après avoir choisi Orneval (capture 20).
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.ZonePlan = true end
local Z = T:RegisterModule("ZonePlan", {
  title = "Trajet",
  description = "Tu choisis la zone, TreeAI prépare le meilleur ordre des arrêts. Clic droit sur un arrêt : l'ignorer.",
  defaults = { enabled = true, elites = false, groupQ = true, dungeons = false, gap = 3, maxMin = 0, prio = 1,
    detour = true, compass = true, autoWatch = true, watchMax = 6 },
  -- style = mode du trajet (par personnage, 0 = pas encore choisi : repris de l'ancien réglage « Priorité ») ;
  -- watchManual[q] = true / false : suivi posé / retiré À LA MAIN par le joueur (jamais repris par le suivi auto) ;
  -- watchOurs[q] = quête mise dans le suivi du jeu par TreeAI (la seule qu'il retire)
  charDefaults = { mode = "auto", zones = {}, ignore = {}, extra = {}, style = 0, watchManual = {}, watchOurs = {} },
  options = {
    { section = "Quêtes du trajet", key = "elites", type = "toggle", label = "Quêtes élites (toujours prises à 3 ou plus)" },
    { key = "groupQ", type = "toggle", label = "Quêtes de groupe" },
    { key = "dungeons", type = "toggle", label = "Quêtes de donjon" },
    { key = "gap", type = "range", label = "Niveau de quête max au-dessus du mien", min = 1, max = 6, step = 1 },
    { section = "Ordre des arrêts", key = "maxMin", type = "range", label = "Arrêt le plus loin (minutes de trajet, 0 = sans limite)", min = 0, max = 30, step = 1 },
    { key = "detour", type = "toggle", label = "Proposer un détour hors zone quand il rapporte gros" },
    { key = "compass", type = "toggle", label = "La boussole vise l'arrêt en cours" },
    { section = "Suivi du jeu", key = "autoWatch", type = "toggle",
      label = "Suivi auto par zone : le suivi du jeu montre les quêtes de ta zone, dans l'ordre du trajet" },
    { key = "watchMax", type = "range", label = "Quêtes suivies au plus", min = 3, max = 8, step = 1 },
  },
})
T.ZonePlan = Z

local floor, max, min, sqrt, huge = math.floor, math.max, math.min, math.sqrt, math.huge
local tsort, tconcat, tinsert, fmt = table.sort, table.concat, table.insert, string.format

local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function inCombat() return T.InCombat and T:InCombat() or false end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée
local function myLevel() return tonumber(_ns((try(G("UnitLevel"), "player")))) or 1 end
local function groupSize() return max(1, tonumber(_ns((try(G("GetNumGroupMembers"))))) or 0) end
local function opt(k) return Z.db and Z.db[k] end
local Geo = T.Geo or {}
local function X() return T.Export end
local function thousands(n)
  local s = tostring(floor((n or 0) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^%s+", ""))
end

-- ===================================================================== moteur pur (testable)
local N = {}
Z.Engine = N
N.AREA = 120          -- yd : objectifs à moins de ça = un seul arrêt
N.HYST, N.HYST_MIN = 0.10, 60
local RANK = { pick = 1, obj = 2, turn = 3 }
local function skey(s) return tostring(s.k) .. ":" .. tostring(s.q or "") .. ":" .. tostring(s.o or "") end
N.StepKey = skey

-- requests = { { q, steps = { ordered, k = pick|obj|turn, map, x, y, svc, value } } } → nodes
-- node = { steps, map, x, y, svc, value, qs = { [q] = rank }, key, area, delta (journal : +prises −remises) }
-- dist(a, b) → yards (nil = far / unknown)
-- (QUESTS 4.1) UNE quête à UN endroit = UN arrêt : les objectifs d'une même quête à moins de N.QUEST_AREA yd l'un de
-- l'autre (sites de réapparition de rechange compris, s.alt) ne font qu'un arrêt — « La revanche de Gann » : trois
-- objets de trois camps du même site (≈ 150 yd) étaient 3 arrêts « dans ~2 min », « ~13 min », « ~25 min » (capture
-- 4.0.5) ; on les ramasse ensemble, le temps sur place est en partie commun (N.SAME_Q_SHARE) ; et les remises / prises au
-- MÊME PNJ (≤ N.NPC_YD) sont un seul arrêt (« Rendre X + Rendre Y » chez Mangeclous)
N.QUEST_AREA, N.NPC_YD, N.SAME_Q_SHARE = 400, 25, 0.6
local function stepDelta(s) return (s.k == "pick" and 1) or (s.k == "turn" and -1) or 0 end
function N.Delta(nd)
  if nd.delta then return nd.delta end
  local d = 0
  for _, s in ipairs(nd.steps) do d = d + stepDelta(s) end
  return d
end
local function hasPick(nd) for _, s in ipairs(nd.steps) do if s.k == "pick" then return true end end return false end
N.HasPick = hasPick
function N.Nodes(requests, dist, area)
  area = area or N.AREA
  local nodes, clusters = {}, {}
  local objs, npcs = {}, {}
  for _, r in ipairs(requests or {}) do
    for _, s in ipairs(r.steps or {}) do
      if s.k == "obj" and s.x then objs[#objs + 1] = s
      -- (une remise d'une quête PAS finie reste seule : la fusionner liait deux quêtes dans l'ordre — Route.Groups numérote
      -- quand même d'un seul numéro les arrêts consécutifs au même PNJ)
      elseif (s.k == "pick" or (s.k == "turn" and s.ready)) and s.x then npcs[#npcs + 1] = s
      else
        nodes[#nodes + 1] = { steps = { s }, map = s.map, x = s.x, y = s.y, svc = s.svc or 0, value = s.value or 0,
          qs = { [s.q or -1] = RANK[s.k] or 2 }, key = skey(s), theme = s.theme }
      end
    end
  end
  local function byQ(a, b) if a.q ~= b.q then return (a.q or 0) < (b.q or 0) end return (tonumber(a.o) or 0) < (tonumber(b.o) or 0) end
  tsort(objs, byQ)
  -- 1. objectifs d'une même quête : un seul lieu (le 1er objectif) ; un objectif plus loin que N.QUEST_AREA mais dont un
  -- site de rechange (s.alt) est près, y est déplacé
  local home1 = {}                                   -- [q] = 1er objectif placé de la quête
  for _, s in ipairs(objs) do
    local h = home1[s.q or -1]
    if h and dist then
      local d = dist(h, s)
      if not (d and d <= N.QUEST_AREA) and type(s.alt) == "table" then
        for _, a in ipairs(s.alt) do
          local da = a.x and dist(h, a)
          if da and da <= N.QUEST_AREA then s.map, s.x, s.y, d = a.map, a.x, a.y, da break end
        end
      end
      s._sameQ = (d and d <= N.QUEST_AREA) and h or nil
    elseif not h then home1[s.q or -1] = s end
  end
  for _, s in ipairs(objs) do
    local home
    if s._sameQ then home = s._sameQ._node end
    if not home then
      for _, c in ipairs(clusters) do
        if c.map == s.map or dist then
          local d = dist and dist(c, s) or nil
          if d and d <= area then home = c break end
        end
      end
    end
    if home then
      home.steps[#home.steps + 1] = s
      local sv = s.svc or 0
      -- même quête au même endroit : on les fait ensemble (une part du temps est commune)
      if s._sameQ then
        sv = sv * N.SAME_Q_SHARE
        s.svc = sv                                   -- le trajet (eta) et Route (temps sur place) lisent l'arrêt
        if s._pred then s._pred = s._pred * N.SAME_Q_SHARE end
      end
      home.svc, home.value = home.svc + sv, home.value + (s.value or 0)
      home.qs[s.q or -1] = 2
      home.key = home.key .. "+" .. skey(s)
    else
      home = { steps = { s }, map = s.map, x = s.x, y = s.y, svc = s.svc or 0, value = s.value or 0,
        qs = { [s.q or -1] = 2 }, key = skey(s), theme = s.theme }
      clusters[#clusters + 1] = home
    end
    s._node, s._sameQ = home, nil
  end
  for _, s in ipairs(objs) do s._node = nil end
  -- 2. remises / prises au même PNJ (même sorte : jamais « prendre » et « rendre » d'un même arrêt, l'ordre compte)
  tsort(npcs, function(a, b) if a.k ~= b.k then return a.k < b.k end return byQ(a, b) end)
  local spots = {}
  for _, s in ipairs(npcs) do
    local home
    for _, c in ipairs(spots) do
      -- une remise prête (quête finie) n'attend jamais celle d'une quête pas finie au même PNJ
      if c.kind == s.k and c.map == s.map and not c.qs[s.q or -1] and (c.ready or false) == (s.ready or false) then
        local d = dist and dist(c, s) or nil
        if d and d <= N.NPC_YD then home = c break end
      end
    end
    if home then
      home.steps[#home.steps + 1] = s
      home.svc, home.value = home.svc + 5, home.value + (s.value or 0)
      home.qs[s.q or -1] = RANK[s.k]
      home.key = home.key .. "+" .. skey(s)
    else
      home = { steps = { s }, map = s.map, x = s.x, y = s.y, svc = s.svc or 0, value = s.value or 0, kind = s.k,
        ready = s.ready, qs = { [s.q or -1] = RANK[s.k] or 2 }, key = skey(s), theme = s.theme }
      spots[#spots + 1] = home
    end
  end
  local k = 0
  for _, c in ipairs(clusters) do
    if #c.steps > 1 then k = k + 1; c.area = "a" .. k end
    nodes[#nodes + 1] = c
  end
  for _, c in ipairs(spots) do
    if #c.steps > 1 then k = k + 1; c.area = "a" .. k end
    c.kind, c.ready = nil, nil
    nodes[#nodes + 1] = c
  end
  for _, nd in ipairs(nodes) do nd.delta = N.Delta(nd) end
  tsort(nodes, function(a, b) return a.key < b.key end)
  return nodes
end

-- order (list of node indices) valid? for each quest: every pick before every objective before every turn-in
function N.Valid(nodes, ord)
  local maxR = {}
  for _, i in ipairs(ord) do
    for q, r in pairs(nodes[i].qs) do
      if (maxR[q] or 0) > r then return false end
      if r > (maxR[q] or 0) then maxR[q] = r end
    end
  end
  return true
end

-- greedy: best value / (travel + service) among the nodes whose predecessors are placed. o = { horizon, cap,
-- logCount, yield, first = legFirst(start, node), bond = fn(lastNode, node) → multiplier (ZONEFIX : même thème —
-- même histoire ou même camp — que l'arrêt d'avant = bonus : on finit une histoire avant d'en mêler une autre) }
function N.Greedy(nodes, start, leg, o)
  o = o or {}
  local need = {}                      -- [q][rank] = nodes left
  for _, nd in ipairs(nodes) do
    for q, r in pairs(nd.qs) do
      need[q] = need[q] or { 0, 0, 0 }
      need[q][r] = need[q][r] + 1
    end
  end
  local used, ord = {}, {}
  local pos, logCount, cap = start, o.logCount or 0, o.cap or 99
  local last
  for _ = 1, o.horizon or 40 do
    local best, bestScore = nil, -huge
    for i, nd in ipairs(nodes) do
      if not used[i] then
        local ok = true
        for q, r in pairs(nd.qs) do
          local nq = need[q]
          for rr = 1, r - 1 do if nq[rr] > 0 then ok = false break end end
          if not ok then break end
        end
        if ok and hasPick(nd) and logCount + max(1, N.Delta(nd)) > cap then ok = false end
        if ok then
          local t = ((#ord == 0 and o.first) and o.first(nd) or leg(pos, nd)) + max(10, nd.svc or 0)
          local score = (nd.value or 0) / max(10, t)
          if o.bond and last then score = score * (o.bond(last, nd) or 1) end
          if score > bestScore then best, bestScore = i, score end
          if o.yield then o.yield() end
        end
      end
    end
    if not best then break end
    local nd = nodes[best]
    used[best] = true
    ord[#ord + 1] = best
    for q, r in pairs(nd.qs) do need[q][r] = need[q][r] - 1 end
    logCount = logCount + N.Delta(nd)
    if nd.x then pos = nd end
    last = nd
  end
  return ord
end
-- (ZONEFIX) mode « Par histoire » : les arrêts d'un même thème (histoire / camp) d'un seul tenant, dans l'ordre de leur
-- première apparition (ordre relatif gardé) — 2-opt / Or-opt ne regardent que le chemin et mêlaient tout ; refusé si
-- les précédences ou le journal ne suivent pas
function N.ThemeGroup(nodes, ord, cap, logCount)
  local order, groups = {}, {}
  for _, i in ipairs(ord) do
    local th = nodes[i].theme or ("#" .. i)
    if not groups[th] then groups[th] = {}; order[#order + 1] = th end
    local g = groups[th]
    g[#g + 1] = i
  end
  if #order == #ord then return ord end
  local out = {}
  for _, th in ipairs(order) do for _, i in ipairs(groups[th]) do out[#out + 1] = i end end
  if N.Valid(nodes, out) and N.CapOK(nodes, out, cap, logCount) then return out end
  return ord
end

-- travel matrix D[0..n][1..n] over the chosen nodes (index 0 = start, with the hearthstone option)
function N.Matrix(nodes, ord, start, leg, first, yield)
  local D = { [0] = {} }
  for a = 1, #ord do
    local na = nodes[ord[a]]
    D[0][ord[a]] = first and first(na) or leg(start, na)
    D[ord[a]] = {}
    for b = 1, #ord do
      if a ~= b then D[ord[a]][ord[b]] = leg(na, nodes[ord[b]]) end
      if yield then yield() end
    end
  end
  return D
end
function N.Cost(D, ord)
  local c, prev = 0, 0
  for _, i in ipairs(ord) do c = c + ((D[prev] and D[prev][i]) or 0); prev = i end
  return c
end

-- local search (2-opt with exact reversed-segment cost + Or-opt of 1–3 nodes), precedences kept
function N.Improve(nodes, ord, D, yield, passes)
  local n = #ord
  local function d(a, b) if b == nil then return 0 end return (D[a] and D[a][b]) or 0 end
  for _ = 1, passes or 3 do
    local improved = false
    -- 2-opt
    for i = 1, n - 1 do
      local a = (i == 1) and 0 or ord[i - 1]
      local seen = {}
      local fwd, rev = 0, 0
      for q, r in pairs(nodes[ord[i]].qs) do seen[q] = r end
      for j = i + 1, n do
        local bad = false
        for q, r in pairs(nodes[ord[j]].qs) do
          if seen[q] and seen[q] ~= r then bad = true break end
          seen[q] = r
        end
        if bad then break end
        fwd = fwd + d(ord[j - 1], ord[j])
        rev = rev + d(ord[j], ord[j - 1])
        local b = ord[j + 1]
        local old = d(a, ord[i]) + fwd + d(ord[j], b)
        local new = d(a, ord[j]) + rev + d(ord[i], b)
        if new < old - 1 then
          local x, y = i, j
          while x < y do ord[x], ord[y] = ord[y], ord[x]; x, y = x + 1, y - 1 end
          improved = true
          a = (i == 1) and 0 or ord[i - 1]
          fwd, rev, seen = 0, 0, {}
          break
        end
        if yield then yield() end
      end
    end
    -- Or-opt: move a segment of 1..3 nodes elsewhere
    for L = 1, 3 do
      local i = 1
      while i + L - 1 <= n do
        local a = (i == 1) and 0 or ord[i - 1]
        local s1, sL, b = ord[i], ord[i + L - 1], ord[i + L]
        local removeGain = d(a, s1) + d(sL, b) - (b and d(a, b) or 0)
        local bestJ, bestDelta = nil, -1
        for j = 0, n do
          if j < i - 1 or j > i + L - 1 then
            local p, q = (j == 0) and 0 or ord[j], ord[j + 1]
            local add = d(p, s1) + (q and d(sL, q) or 0) - (q and d(p, q) or 0)
            local delta = removeGain - add
            if delta > bestDelta + 1 then
              local trial = {}
              for k = 1, n do if k < i or k > i + L - 1 then trial[#trial + 1] = ord[k] end end
              local at = (j < i) and j or (j - L)
              for k = L, 1, -1 do tinsert(trial, at + 1, ord[i + k - 1]) end
              if N.Valid(nodes, trial) then bestJ, bestDelta = trial, delta end
            end
          end
          if yield then yield() end
        end
        if bestJ then
          for k = 1, n do ord[k] = bestJ[k] end
          improved = true
        end
        i = i + 1
      end
    end
    if not improved then break end
  end
  return ord
end

-- hysteresis: the previous order (step keys) patched with the new nodes (cheapest valid insertion), same node set.
function N.Patch(nodes, ord, oldKeys, D)
  local pos = {}
  for i, k in ipairs(oldKeys or {}) do pos[k] = i end
  local known, fresh = {}, {}
  for _, i in ipairs(ord) do
    local p
    for _, s in ipairs(nodes[i].steps) do
      local v = pos[skey(s)]
      if v and (not p or v < p) then p = v end
    end
    if p then known[#known + 1] = { i = i, p = p } else fresh[#fresh + 1] = i end
  end
  tsort(known, function(a, b) return a.p < b.p end)
  local out = {}
  for _, e in ipairs(known) do out[#out + 1] = e.i end
  if not N.Valid(nodes, out) then return nil end
  local function d(a, b) return (D[a] and D[a][b]) or 0 end
  for _, x in ipairs(fresh) do
    local bestAt, bestC = nil, huge
    for at = 0, #out do
      local p, q = (at == 0) and 0 or out[at], out[at + 1]
      local c = d(p, x) + (q and (d(x, q) - d(p, q)) or 0)
      if c < bestC then
        tinsert(out, at + 1, x)
        if N.Valid(nodes, out) then bestAt, bestC = at, c end
        table.remove(out, at + 1)
      end
    end
    if not bestAt then return nil end
    tinsert(out, bestAt + 1, x)
  end
  return out, #known
end
-- (QUESTQA) ce qui se fait sur place d'abord : rendre / prendre à moins de N.NEAR yd du joueur passe en tête
-- (l'optimisation du trajet seul pouvait envoyer chercher 10 œufs avant de rendre 5 quêtes au même PNJ, pour 2 s
-- de chemin gagnées) ; précédences gardées, ordre relatif inchangé sinon. (ZONEFIX) ≈ 100 m : un PNJ « ! » à 65 m
-- derrière toi est l'arrêt 1 (la boussole et la carte le disent ensemble), jamais un arrêt à côté du trajet
N.NEAR = 110
-- the quest log never overflows along the order (pick +1, turn-in −1)
function N.CapOK(nodes, ord, cap, logCount)
  if not cap then return true end
  local c = logCount or 0
  for _, i in ipairs(ord) do
    local d = N.Delta(nodes[i])
    if d > 0 and c + d > cap then return false end
    c = c + d
  end
  return true
end
function N.NearFirst(nodes, ord, start, dist, yd, cap, logCount)
  if not (start and start.x and dist) then return ord end
  local out, moved = {}, {}
  for _, i in ipairs(ord) do
    local nd = nodes[i]
    local k = nd.steps[1].k
    if (k == "turn" or k == "pick") and nd.x then
      local d = dist(start, nd)
      if d and d <= (yd or N.NEAR) then
        local trial = {}
        for _, j in ipairs(out) do trial[#trial + 1] = j end
        trial[#trial + 1] = i
        for _, j in ipairs(ord) do if j ~= i and not moved[j] then trial[#trial + 1] = j end end
        if N.Valid(nodes, trial) and N.CapOK(nodes, trial, cap, logCount) then out[#out + 1] = i; moved[i] = true end
      end
    end
  end
  if #out == 0 then return ord end
  for _, i in ipairs(ord) do if not moved[i] then out[#out + 1] = i end end
  return out
end
-- (QA-QUEST F2) quêtes chronométrées en cours (timers = { [q] = secondes }) : tous leurs arrêts (remise comprise)
-- passent en tête, avec ce qui doit les précéder (un objectif groupé avec celui d'une autre quête tire le « prendre »
-- de cette quête) ; ordre relatif inchangé ; refusé si les précédences ou le journal ne suivent pas
function N.TimersFirst(nodes, ord, timers, cap, logCount)
  if type(timers) ~= "table" or not next(timers) then return ord end
  local need = {}                                    -- [q] = rang : les arrêts de q de rang < need[q] vont en tête
  for q in pairs(timers) do need[q] = 4 end
  local hot, changed, guard = {}, true, 0
  while changed and guard < 20 do
    changed, guard = false, guard + 1
    for _, i in ipairs(ord) do
      if not hot[i] then
        for q, r in pairs(nodes[i].qs) do
          if need[q] and r < need[q] then
            hot[i], changed = true, true
            for q2, r2 in pairs(nodes[i].qs) do if (need[q2] or 0) < r2 then need[q2] = r2 end end
            break
          end
        end
      end
    end
  end
  local head, rest = {}, {}
  for _, i in ipairs(ord) do if hot[i] then head[#head + 1] = i else rest[#rest + 1] = i end end
  if #head == 0 or #rest == 0 then return ord end
  for _, i in ipairs(rest) do head[#head + 1] = i end
  if N.Valid(nodes, head) and N.CapOK(nodes, head, cap, logCount) then return head end
  return ord
end
-- (QA-QUEST F10) arrêts sans position (pas de Questie, pas de point du jeu) : après tous ceux qu'on sait trouver
-- (un trajet « fictif » de 45 s les mettait en tête, sans lieu : « Ramasse « Rat fouisseur » · trajet 0 s ») ;
-- ce qui doit les suivre (la remise de la même quête) part avec eux ; refusé si précédences / journal ne suivent pas
function N.PositionlessLast(nodes, ord, cap, logCount)
  local after = {}                                   -- [q] = rang : les arrêts de q de rang > after[q] vont au bout
  local any = false
  for _, i in ipairs(ord) do
    if not nodes[i].x then
      any = true
      for q, r in pairs(nodes[i].qs) do if not after[q] or r < after[q] then after[q] = r end end
    end
  end
  if not any then return ord end
  local tail, changed, guard = {}, true, 0
  while changed and guard < 20 do
    changed, guard = false, guard + 1
    for _, i in ipairs(ord) do
      if not tail[i] then
        local go = not nodes[i].x
        if not go then
          for q, r in pairs(nodes[i].qs) do if after[q] and r > after[q] then go = true break end end
        end
        if go then
          tail[i], changed = true, true
          for q, r in pairs(nodes[i].qs) do if not after[q] or r < after[q] then after[q] = r end end
        end
      end
    end
  end
  local head, rest = {}, {}
  for _, i in ipairs(ord) do if tail[i] then rest[#rest + 1] = i else head[#head + 1] = i end end
  if #head == 0 or #rest == 0 then return ord end
  for _, i in ipairs(rest) do head[#head + 1] = i end
  if N.Valid(nodes, head) and N.CapOK(nodes, head, cap, logCount) then return head end
  return ord
end
-- keep the old order unless the new one saves ≥ 10 % of the travel and ≥ 1 min
function N.Choose(newCost, oldCost)
  if not oldCost then return "new" end
  local gain = oldCost - newCost
  if gain >= N.HYST_MIN and gain >= N.HYST * oldCost then return "new" end
  return "old"
end

-- ===================================================================== cartes, zones
local infoCache = {}
local function mapInfo(m)
  m = tonumber(m)
  if not m then return nil end
  local c = infoCache[m]
  if c == nil then
    local i = try(C("C_Map", "GetMapInfo"), m)
    c = type(i) == "table" and i or false
    infoCache[m] = c
  end
  return c or nil
end
function Z.MapName(m)
  local i = mapInfo(m)
  if i and i.name and i.name ~= "" then return i.name end
  local exp = X() and X().Data and X():Data()
  for _, e in ipairs(exp and exp.maps or {}) do if e.id == tonumber(m) and e.name then return e.name end end
  return "zone " .. tostring(m)
end
-- zone (uiMapType 3) holding this map: caves / cities / sub-maps climb to their zone
function Z.ZoneOf(m)
  m = tonumber(m)
  local cur, guard = m, 0
  while cur and guard < 6 do
    local i = mapInfo(cur)
    local t = i and tonumber(i.mapType)
    if not t or t == 3 then return cur end
    if t <= 2 then return m end
    cur, guard = tonumber(i.parentMapID), guard + 1
  end
  return m
end
-- (QUESTS 4.1) carte d'instance (donjon : type 4) — jamais le nom d'une zone de trajet / d'un titre de carte
function Z.IsInstance(m)
  local i = mapInfo(m)
  return (i and tonumber(i.mapType) == 4) or false
end
--- zone « dehors » d'une carte (nil pour une instance : on ne sait pas où elle est sur le continent)
function Z.OutdoorZone(m)
  local z = Z.ZoneOf(m)
  if not z or Z.IsInstance(z) or Z.IsInstance(m) then return nil end
  return z
end
function Z.Cont(m)
  local r = Geo.Rect and Geo.Rect(m)
  return r and r.c or nil
end
local function touch(ra, rb, pad)
  if not (ra and rb) or ra.c ~= rb.c then return false end
  local a1, a2 = min(ra.x0, ra.x1) - pad, max(ra.x0, ra.x1) + pad
  local b1, b2 = min(rb.x0, rb.x1), max(rb.x0, rb.x1)
  if a2 < b1 or b2 < a1 then return false end
  a1, a2 = min(ra.y0, ra.y1) - pad, max(ra.y0, ra.y1) + pad
  b1, b2 = min(rb.y0, rb.y1), max(rb.y0, rb.y1)
  return not (a2 < b1 or b2 < a1)
end
-- every zone we can name on this continent: the client's list, the export, my log, the counts
function Z:KnownZones(cont)
  local set = {}
  local here = Geo.Player and Geo.Player()
  local m, guard = tonumber(here), 0
  local contMap
  while m and guard < 6 do
    local i = mapInfo(m)
    if i and tonumber(i.mapType) == 2 then contMap = m break end
    m, guard = i and tonumber(i.parentMapID), guard + 1
  end
  local kids = contMap and try(C("C_Map", "GetMapChildrenInfo"), contMap, 3, false)
  for _, k in ipairs(type(kids) == "table" and kids or {}) do if type(k) == "table" and tonumber(k.mapID) then set[k.mapID] = true end end
  local exp = X() and X().Data and X():Data()
  for _, e in ipairs(exp and exp.maps or {}) do if tonumber(e.id) then set[e.id] = true end end
  for _, r in ipairs(exp and exp.quests or {}) do if tonumber(r.zone) then set[r.zone] = true end end
  for mm in pairs(self.char and self.char.zones or {}) do if tonumber(mm) then set[tonumber(mm)] = true end end
  for mm in pairs(self.counts and self.counts.n or {}) do set[mm] = true end
  if tonumber(here) then set[Z.ZoneOf(here)] = true end
  local out = {}
  for mm in pairs(set) do
    local z = Z.ZoneOf(mm)
    if z == mm and (not cont or Z.Cont(mm) == cont) then out[#out + 1] = mm end
  end
  tsort(out)
  return out
end
local nbCache = {}
-- cartes relues (tests / changement de client) : noms, voisines, zone en cours
function Z.ResetMaps()
  for k in pairs(infoCache) do infoCache[k] = nil end
  for k in pairs(nbCache) do nbCache[k] = nil end
  Z._lastAllowed = nil
end
function Z:Neighbours(zone)
  local r0 = Geo.Rect and Geo.Rect(zone)
  if not r0 then return {} end
  local c = nbCache[zone]
  if c and c.n == #self:KnownZones(r0.c) then return c.list end
  local list = {}
  local all = self:KnownZones(r0.c)
  for _, m in ipairs(all) do
    if m ~= zone and touch(r0, Geo.Rect(m), 250) then list[#list + 1] = m end
  end
  nbCache[zone] = { n = #all, list = list }
  return list
end

-- zone de travail en cours : { mode, maps = set | nil (continent), cont, here, label, cross }
function Z:Allowed()
  local ch = self.char or {}
  local here = Geo.Player and Geo.Player()
  local hz = tonumber(here) and Z.ZoneOf(here) or nil
  if not hz then return self._lastAllowed or { mode = ch.mode, any = true, label = "Partout (position inconnue)" } end
  local zs = {}
  for m in pairs(ch.zones or {}) do zs[#zs + 1] = tostring(m) end
  tsort(zs)
  local ckey = fmt("%s|%s|%d|%s", tostring(hz), tostring(ch.mode), self.rev or 0, tconcat(zs, ","))
  local L = self._lastAllowed
  local tnow = tonumber(try(G("GetTime"))) or 0
  if L and L._key == ckey and tnow - (L._at or -100) < 10 then return L end
  local A = { mode = ch.mode or "auto", here = hz, cont = Z.Cont(hz) }
  if A.mode == "continent" and A.cont then
    A.label = "Toute la région"
  elseif A.mode == "zones" and next(ch.zones or {}) then
    A.maps = {}
    local names = {}
    for m in pairs(ch.zones) do
      m = tonumber(m)
      if m then
        A.maps[m] = true
        names[#names + 1] = Z.MapName(m)
        if A.cont and Z.Cont(m) and Z.Cont(m) ~= A.cont then A.cross = true end
      end
    end
    tsort(names)
    A.label = (#names <= 2) and tconcat(names, " + ") or fmt("%s + %d zones", names[1], #names - 1)
  else
    -- (ZONEFIX) auto = TA zone, et elle seule : les voisines ne viennent qu'en renfort quand ta zone n'a presque plus
    -- rien (BuildRoute, Z.fill) ; ce qui reste ailleurs est dit sur une ligne « Autres zones : … » (capture 20 :
    -- Orneval + Les Tarides + Mille pointes mêlées dans un trajet de 1 h 27)
    A.mode = "auto"
    A.maps = { [hz] = true }
    A.nb = {}
    for _, m in ipairs(self:Neighbours(hz)) do A.nb[m] = true end
    A.label = Z.MapName(hz)
  end
  A._key, A._at = ckey, tnow
  self._lastAllowed = A
  return A
end
function Z:InZone(map, A)
  local mine = A == nil
  A = A or self:Allowed()
  if A.any then return true end
  local m = tonumber(map)
  if not m then return true end
  if A.maps then
    local zm = Z.ZoneOf(m)
    if A.maps[zm] or A.maps[m] then return true end
    -- voisine prise en renfort par le dernier trajet (zone presque vide) : dans la zone de travail aussi (le calcul du
    -- trajet remet Z.fill à zéro avant de juger : il reste strict)
    local f = (mine or A == self._lastAllowed) and A.mode == "auto" and self.fill
    return (f and (f[zm] or f[m])) and true or false
  end
  local c = Z.Cont(m)
  return c ~= nil and c == A.cont
end
-- (QUESTS 4.1) quête passée par « Suivant » (QuestBrain, session)
function Z.SkippedQuest(q)
  local QBm = T.QuestBrain
  return (QBm and type(QBm.skipped) == "table" and QBm.skipped[tonumber(q) or -1]) and true or false
end
-- (QUESTS 4.1) mode du cerveau des quêtes (« zone » sans lui)
function Z.BrainMode()
  local QBm = T.QuestBrain
  if not (QBm and QBm.Mode) then return "zone" end
  if QBm.IsEnabled and not QBm:IsEnabled() then return "zone" end
  local ok, m = pcall(QBm.Mode)
  return ok and m or "zone"
end
--- zones (dehors) des arrêts d'un trajet, dans leur ordre : « Mille pointes », « Mille pointes + Les Tarides »,
--- « Mille pointes + 4 zones » ; nil sans arrêt placé
function Z.StepsZoneLabel(steps)
  local names, seen = {}, {}
  for _, s in ipairs(steps or {}) do
    if s.map and s.x and not s._done and not s._other then
      local z = Z.OutdoorZone(s.map)
      if z and not seen[z] then seen[z] = true; names[#names + 1] = Z.MapName(z) end
    end
  end
  if #names == 0 then return nil end
  if #names <= 2 then return tconcat(names, " + ") end
  return fmt("%s + %d zones", names[1], #names - 1)
end
-- signature for the Planner: replan when the work area / a setting / the quest mode changes
function Z:Sig()
  if not self:IsEnabled() then return "" end
  local ch = self.char or {}
  local here = (ch.mode or "auto") == "auto" and Geo.Player and Geo.Player() or nil
  return fmt("%s:%s:%d:%s", tostring(ch.mode), tostring(here and Z.ZoneOf(here) or ""), self.rev or 0, Z.BrainMode())
end
function Z:Bump(why)
  self.rev = (self.rev or 0) + 1
  local P = T.Planner
  if P and P.Dirty then P:Dirty(true, why or "zone / réglage du trajet") end
end

-- ===================================================================== tri des quêtes
-- (QUESTQA) mêmes règles que l'appli (treeaicoach/wow/planner/rules.py) : une quête prête à rendre est gardée
-- (le PNJ de remise est un PNJ normal) ; JcJ et raid jamais ; donjon seulement si coché ET groupe ≥ 3 ; élite
-- si coché ou groupe ≥ 3 ; quête de groupe si cochée ou en groupe ; au plus « gap » niveaux au-dessus ;
-- quête échouée jamais (elle n'avance plus : l'abandonner et la reprendre).
local TAG_ELITE, TAG_PVP = 1, 41
local DUNGEON_TAG = { [81] = true, [85] = true }
local RAID_TAG = { [62] = true, [88] = true, [89] = true }
Z.DUNGEON_TAG, Z.RAID_TAG = DUNGEON_TAG, RAID_TAG
local tagCache, tagMiss = {}, {}
local function questTag(q)
  q = tonumber(q)
  if not q then return nil end
  local c = tagCache[q]
  if c then return c end
  local t = tonumber(try(G("GetTime"))) or 0
  if tagMiss[q] and t - tagMiss[q] < 60 then return nil end      -- « pas de tag » relu au plus 1×/min (cache du client)
  -- (ZONEFIX) Questie d'abord : il corrige les tags que le client donne mal ou pas du tout (« Goûts changeants »,
  -- quête de donjon de Forever, arrivait sans tag : arrêt 1 d'un trajet solo, capture 20) ; puis le client
  local tag
  local QL = G("QuestieLoader")
  local mods = type(QL) == "table" and rawget(QL, "_modules")
  local qdb = type(mods) == "table" and rawget(mods, "QuestieDB")
  local qf = type(qdb) == "table" and rawget(qdb, "GetQuestTagInfo")
  if type(qf) == "function" then
    local id, name = try(qf, q)
    id = tonumber(_ns(id))
    if id and id > 0 then tag = { tagID = id, tagName = _ns(name) } end
  end
  if type(tag) ~= "table" then tag = try(C("C_QuestLog", "GetQuestTagInfo"), q) end
  if type(tag) ~= "table" then
    local id, name = try(G("GetQuestTagInfo"), q)                 -- API classique : (tagID, tagName)
    id = tonumber(_ns(id))
    tag = id and id > 0 and { tagID = id, tagName = _ns(name) } or nil
  end
  if type(tag) == "table" then tagCache[q] = tag return tag end
  tagMiss[q] = t
  return nil
end
Z.QuestTag = questTag
-- (ZONEFIX + JOURNAL 4.0.1) le donjon d'une quête (liste QuestieDB Forever des quêtes de donjon, index QuestieDB, en-tête
-- du journal égal au nom du donjon) et s'il est déjà fait (historique, verrou, quêtes finies) : contrats du module
-- Donjon (T.Dungeon.QuestDungeon / IsDone) quand il les offre — sinon les tags seuls
-- (mémorisés : le trajet en jeu juge des centaines de quêtes par calcul ; une minute pour le donjon d'une quête,
-- 30 s pour « fait »)
local dgCache, dgAt, dgFn, doneCache, doneFn = {}, -1e9, nil, setmetatable({}, { __mode = "k" }), nil
local function dungeonOf(q)
  local D = T.Dungeon
  local f = type(D) == "table" and rawget(D, "QuestDungeon")
  q = tonumber(q)
  if type(f) ~= "function" or not q then return nil end
  local t = tonumber(try(G("GetTime"))) or 0
  if t - dgAt > 60 or f ~= dgFn then dgCache, dgAt, dgFn = {}, t, f end
  local c = dgCache[q]
  if c == nil then
    c = try(f, q)
    c = type(c) == "table" and c or false
    dgCache[q] = c
  end
  return c or nil
end
local function dungeonDone(d)
  local D = T.Dungeon
  local f = type(D) == "table" and rawget(D, "IsDone")
  if d == nil or type(f) ~= "function" then return false end
  local t = tonumber(try(G("GetTime"))) or 0
  if f ~= doneFn then doneCache, doneFn = setmetatable({}, { __mode = "k" }), f end
  local c = doneCache[d]
  if c and t - c.t < 30 then return c.v end
  local v = try(f, d) and true or false
  doneCache[d] = { v = v, t = t }
  return v
end
Z.DungeonOf = dungeonOf
--- quête d'un donjon que tu as déjà fait ? (jamais proposée, jamais mise dans le suivi)
function Z.DungeonDone(q) return dungeonDone(dungeonOf(q)) end
-- → elite, group, dungeon, extra = { pvp, raid, boss, failed, dungeonDone }
function Z.Kind(q, rec, lq)
  local tag = questTag(q)
  local tid = tag and tonumber(tag.tagID) or tonumber(rec and rec.tag) or tonumber(lq and lq.tag) or -1
  local elite = (lq and lq.elite == 1) or (rec and rec.elite == 1) or (tag and tag.isElite) or tid == TAG_ELITE or false
  local boss = false
  for _, o in ipairs(rec and rec.o or {}) do
    local r = tonumber(o.rank) or 0
    if r == 1 or r == 2 then elite = true elseif r == 3 then boss = true end
  end
  local grp = max(tonumber(rec and rec.grp) or 0, tonumber(lq and lq.grp) or 0) > 1
  local dg = (rec and tonumber(rec.dg) or 0) > 0 or (lq and lq.dg == 1) or DUNGEON_TAG[tid] or boss or false
  local zi = rec and rec.zone and mapInfo(rec.zone)
  if zi and tonumber(zi.mapType) == 4 then dg = true end
  local raid = (rec and tonumber(rec.raid) or 0) > 0 or RAID_TAG[tid] or (zi and tonumber(zi.mapType) == 5) or false
  local pvp = (rec and tonumber(rec.pvp) or 0) > 0 or tid == TAG_PVP or false
  local failed = (lq and (lq.failed == 1 or lq.failed == true)) or false
  local dd = dungeonOf(q)
  if dd then dg = true end
  return elite and true or false, grp, dg and true or false,
    { pvp = pvp and true or false, raid = raid and true or false, boss = boss, failed = failed and true or false,
      dungeonDone = dd ~= nil and dungeonDone(dd) or false }
end
-- keep this quest? → ok, why ("ignorée" | "échouée" | "JcJ" | "raid" | "donjon fait" | "donjon" | "élite" | "groupe" |
-- "niveau") ; une quête prête à rendre reste (même d'un donjon fait : la remise est dehors)
function Z:Accept(q, rec, lq, level, group)
  if self.char and self.char.ignore[tonumber(q) or q] then return false, "ignorée" end
  local elite, grp, dg, ex = Z.Kind(q, rec, lq)
  if ex.failed then return false, "échouée" end
  if lq and lq.done == 1 then return true end
  if ex.pvp then return false, "JcJ" end
  if ex.raid then return false, "raid" end
  if ex.dungeonDone then return false, "donjon fait" end         -- (JOURNAL) donjon déjà fait : rien à y gagner
  if dg and not (opt("dungeons") and group >= 3) then return false, "donjon" end
  if elite and not (opt("elites") or group >= 3) then return false, "élite" end
  if grp and not (opt("groupQ") ~= false or group >= 2) then return false, "groupe" end
  local lv = tonumber(rec and rec.lv) or tonumber(lq and lq.lv) or level
  if lv > 0 and lv - level > (tonumber(opt("gap")) or 3) then return false, "niveau" end
  -- (QUESTS 4.1) grise (pas finie) : 10 à 20 % de son XP pour tout le temps des objectifs — hors trajet, et dit
  -- (« Hors trajet : 1 grise ») ; finie, elle reste (la remise ne coûte presque rien)
  local P = T.Planner
  local E = P and P.Engine
  if E and lv > 0 and lv <= E.GreyLevel(level) then return false, "grise" end
  return true
end

-- ===================================================================== modes du trajet (par personnage)
-- (ZONEFIX) cinq modes, visibles et choisis d'un clic (page Trajet, en-tête de la carte) ; chacun change le score
-- des arrêts du trajet en jeu. Gardé par personnage ; l'ancien réglage du compte « Priorité » sert de départ.
Z.STYLES = {
  { id = "fast", name = "Le plus rapide", tip = "le plus d'XP par heure : les arrêts rentables et proches d'abord" },
  { id = "clear", name = "Finir la zone", tip = "toutes les quêtes de la zone comptent autant : rien d'oublié derrière toi" },
  { id = "story", name = "Par histoire", tip = "une histoire (ou un camp) à la fois : tu finis une suite avant d'en ouvrir une autre" },
  { id = "loot", name = "Récompenses", tip = "d'abord les quêtes dont la récompense améliore ton équipement" },
  { id = "group", name = "Groupe", tip = "d'abord les quêtes que ton groupe a aussi ; les élites quand vous êtes 3 ou plus" },
}
local OLD_PRIO = { 1, 2, 4 }        -- ancien « Priorité » (compte) : XP/h, finir la zone, récompenses
function Z:StyleIndex()
  local ch = self.char
  local i = tonumber(ch and ch.style) or 0
  if not Z.STYLES[i] then
    i = OLD_PRIO[tonumber(opt("prio")) or 1] or 1
    if ch then ch.style = i end
  end
  return i
end
function Z:Style() return Z.STYLES[self:StyleIndex()].id end
-- (QUESTS 4.1) sans argument : ce que fait le trajet en ce moment — le mode du cerveau des quêtes quand ce n'est pas « Tout
-- faire ici » (« Rendre mes quêtes », « Quêtes à prendre », « Avec le groupe »), sinon l'ordre choisi dans la zone
function Z:StyleName(i)
  if i == nil then
    local bm = Z.BrainMode()
    local QBm = T.QuestBrain
    if bm ~= "zone" and QBm and QBm.Label then return QBm.Label(bm) end
  end
  local e = Z.STYLES[i or self:StyleIndex()]
  return e and e.name or Z.STYLES[1].name
end
--- by = "brain" (pilote auto) ou nil (le joueur) → char.styleBy : un choix explicite du joueur est reconnu même
--- quand il vaut le mode par défaut (le pilote ne le remplace jamais)
function Z:SetStyle(i, by)
  i = tonumber(i)
  if not (i and Z.STYLES[i] and self.char) then return false end
  self.char.styleBy = (by == "brain") and "brain" or "player"
  if self.char.style ~= i then
    self.char.style = i
    self.lastKeys = nil                -- nouveau mode = nouvel ordre tout de suite (pas d'hystérésis sur l'ancien)
    self:Bump()
  end
  return true
end
function Z:CycleStyle() return self:SetStyle(self:StyleIndex() % #Z.STYLES + 1) end
--- infobulle du sélecteur : chaque mode en une ligne, le mode en cours marqué
function Z:StyleTip()
  local cur, out = self:StyleIndex(), {}
  for i, e in ipairs(Z.STYLES) do out[#out + 1] = (i == cur and "● " or "○ ") .. e.name .. " : " .. e.tip end
  return tconcat(out, "\n")
end

-- ===================================================================== plan en jeu
local function rewardBoost(q)
  local n = tonumber(_ns((try(G("GetNumQuestLogChoices"), q)))) or 0
  local r = tonumber(_ns((try(G("GetNumQuestLogRewards"), q)))) or 0
  return (n + r) > 0 and 2 or 1.2
end
-- (ZONEFIX) mode « Récompenses » : une récompense qui améliore ton équipement (Gear : même score que les infobulles)
-- compte double de plus ; quête pas encore prise : récompenses inconnues → petit bonus seulement
local function lootBoost(q, lq)
  if not lq then return 1.2 end
  local n = tonumber(_ns((try(G("GetNumQuestLogChoices"), q, true)))) or 0
  local r = tonumber(_ns((try(G("GetNumQuestLogRewards"), q)))) or 0
  if n + r == 0 then return 1 end
  local Gr = T.Gear
  local up, IL = Gr and Gr.UpgradePct, G("GetQuestLogItemLink")
  if type(up) == "function" and IL then
    for _, kind in ipairs({ "choice", "reward" }) do
      for k = 1, min(6, kind == "choice" and n or r) do
        local link = try(IL, kind, k, q)
        local pct = link and tonumber((try(up, link)))
        if pct and pct >= 3 then return 4 end
      end
    end
  end
  return 1.5
end
-- (ZONEFIX) mode « Groupe » : la quête que tes amis ont aussi (journal de groupe du jeu) d'abord
-- (QUESTS 4.1) client classique : IsUnitOnQuest(index du journal, unité) (C_QuestLog.IsUnitOnQuest absent) — sans ça le
-- mode « Groupe » ne voyait jamais les quêtes communes
local function partyOn(q)
  local f = C("C_QuestLog", "IsUnitOnQuest")
  local idx
  if not f then
    local g, ix = G("IsUnitOnQuest"), G("GetQuestLogIndexByID")
    idx = ix and tonumber(_ns((try(ix, q)))) or nil
    if not (g and idx and idx > 0) then return 0 end
    f = function(u) return g(idx, u) end
  end
  local n = 0
  for i = 1, 4 do
    local u = "party" .. i
    if try(G("UnitExists"), u) and _ns((try(f, u, q))) then n = n + 1 end
  end
  return n
end
Z.PartyOn = partyOn
local function worldDist(a, b)
  if not (a and b and a.x and b.x and Geo.Dist) then return nil end
  return Geo.Dist(a.map, a.x, a.y, b.map, b.x, b.y)
end
Z.Dist = worldDist

-- legFirst: from the start, the hearthstone may be faster (Route : foyer prêt + auberge connue)
local function makeFirst(leg, start)
  local Rt = T.Route
  if not (start and Rt and Rt:IsEnabled() and Rt.HearthReady and Rt.HearthReady()) then return nil end
  local bind = Rt.BindPlace and try(Rt.BindPlace)
  if not (type(bind) == "table" and bind.map and bind.x) then return nil end
  local D = T.Data and T.Data.Travel
  local hs = ((D and D.hearthCast) or 10) + ((D and D.hearthLoad) or 5)
  local b = { map = bind.map, x = bind.x, y = bind.y }
  return function(nd) return min(leg(start, nd), hs + leg(b, nd)) end
end

-- ===================================================================== thèmes (« le bon thème » : camp + histoire)
-- (ZONEFIX) camp = les PNJ qui donnent les quêtes (ou les reprennent, pour une quête du journal) à moins de 300 yd l'un
-- de l'autre ; son nom = le maître de vol le plus proche (≤ 500 yd, même carte : « Poste de Bois-brisé » →
-- « Bois-brisé »). Histoire = suite de quêtes (QuestieDB : preQuestSingle / preQuestGroup, en remontant) : deux
-- quêtes ou plus de la même suite dans le trajet = une histoire, nommée d'après sa première quête ; sinon le thème
-- est le camp. Le trajet (bonus « même thème »), le suivi auto et l'en-tête de la carte s'en servent.
local HUB_YD, HUB_NAME_YD = 300, 500
local rootCache = {}                 -- [q] = { root, depth } (la suite ne change pas en jeu)
local function storyRoot(db, q)
  if not db then return q, 0 end
  local c = rootCache[q]
  if c then return c[1], c[2] end
  local cur, depth, seen = q, 0, { [q] = true }
  while depth < 8 do
    local p
    local single = db.Q(cur, "preQuestSingle")
    if type(single) == "table" then p = tonumber(single[1]) end
    if not p then
      local grp = db.Q(cur, "preQuestGroup")
      if type(grp) == "table" then p = tonumber(grp[1]) end
    end
    if not p or seen[p] then break end
    seen[p] = true
    cur, depth = p, depth + 1
  end
  rootCache[q] = { cur, depth }
  return cur, depth
end
local HUB_PREFIX = { "^Poste de ", "^Poste d'", "^Avant%-poste de ", "^Avant%-poste d'", "^Camp de ", "^Camp ", "^Village de ",
  "^Bastion de " }
local function shortHub(name)
  name = tostring(name or "")
  for _, p in ipairs(HUB_PREFIX) do
    local s2 = name:gsub(p, "")
    if s2 ~= name and s2 ~= "" then return s2 end
  end
  return name
end
Z.ShortHub = shortHub
local function hubName(anchor)
  local D = T.Data and T.Data.Travel
  if not (D and anchor and anchor.map and anchor.x) then return nil end
  local best, bd
  for _, r in pairs(D.taxi or {}) do
    if r[5] == anchor.map then
      local d = worldDist(anchor, { map = r[5], x = r[6], y = r[7] })
      if d and d <= HUB_NAME_YD and (not bd or d < bd) then best, bd = r[1], d end
    end
  end
  return best and shortHub(best) or nil
end
-- requests (filtrées) → [q] = { key, hub, hubName, story, n } (n = quêtes du même thème dans le trajet)
function Z:Themes(requests, recs, log)
  local Xm = X()
  local db = Xm and Xm.Source and try(Xm.Source, Xm)
  local hubs, out, stories = {}, {}, {}
  for _, r in ipairs(requests or {}) do
    local q, rec = r.q, recs and recs[r.q]
    local anchor = (rec and rec.s and rec.s.x and rec.s) or nil
    if not anchor then
      for _, s in ipairs(r.steps or {}) do if s.x and s.k == "pick" then anchor = s break end end
    end
    if not anchor then anchor = (rec and rec.f and rec.f.x and rec.f) or nil end
    if not anchor then
      for i = #(r.steps or {}), 1, -1 do local s = r.steps[i] if s.x and s.k == "turn" then anchor = s break end end
    end
    local hub
    if anchor and anchor.map and Z.IsInstance(anchor.map) then anchor = nil end   -- (QUESTS 4.1) jamais un camp « dans » une instance
    if anchor and anchor.map then
      for i, h in ipairs(hubs) do
        if h.map == anchor.map then
          local d = worldDist(h, anchor)
          if d and d <= HUB_YD then hub = i break end
        end
      end
      if not hub then hubs[#hubs + 1] = { map = anchor.map, x = anchor.x, y = anchor.y }; hub = #hubs end
    end
    local root, depth = storyRoot(db, q)
    local st = stories[root]
    if not st then st = {}; stories[root] = st end
    st[#st + 1] = { q = q, depth = depth }
    out[q] = { hub = hub, root = root }
  end
  local counts = {}
  for q, e in pairs(out) do
    local st = stories[e.root]
    if #st >= 2 then
      e.key = "s:" .. tostring(e.root)
      if e.story == nil then
        -- nom de l'histoire : sa première quête (journal / export : titre traduit), sinon QuestieDB, sinon la quête la
        -- plus en amont de la suite qu'on fait
        local function title(id) return (log and log[id] and log[id].t) or (recs and recs[id] and recs[id].t) or nil end
        local t = title(e.root)
        if not t and db then
          local nm = db.Q(e.root, "name")
          if type(nm) == "string" and nm ~= "" then t = nm end
        end
        if not t then
          local best
          for _, m in ipairs(st) do if not best or m.depth < best.depth or (m.depth == best.depth and m.q < best.q) then best = m end end
          t = best and title(best.q)
        end
        for _, m in ipairs(st) do if out[m.q] then out[m.q].story = t or false end end
      end
    elseif e.hub then e.key = "h:" .. e.hub
    else e.key = "q:" .. q end
    counts[e.key] = (counts[e.key] or 0) + 1
    if e.hub then
      local h = hubs[e.hub]
      if h.name == nil then h.name = hubName(h) or false end
      e.hubName, e.hubMap = h.name or nil, h.map
    end
  end
  for _, e in pairs(out) do
    e.n = counts[e.key]
    if e.story == false then e.story = nil end
  end
  return out
end
-- « La flamme sacrée » → « la flamme sacrée » (en milieu de phrase) ; « ON RECHERCHE : … » reste tel quel
local function lowerFirst(s)
  s = tostring(s or "")
  local a = s:match("^(%u)%l")
  if a then return a:lower() .. s:sub(2) end
  return s
end
--- en-tête du thème de la quête q (l'arrêt en cours par défaut) : parts = { zone, hub, story, n } ; full =
--- « Orneval · Bois-brisé : la flamme sacrée (3 quêtes) » ; nil sans trajet
function Z:ThemeParts(q)
  if q == nil then
    local P = T.Planner
    local steps, cur = P and P.steps, P and P.cursor
    if steps and cur then
      for i = cur, min(#steps, cur + 12) do
        local s = steps[i]
        if s and s.q and not s._done and not s._other then q = s.q break end
      end
    end
  end
  local th = q and self.themes and self.themes[q]
  local zone
  -- (QUESTS 4.1) la zone du titre = la zone de l'arrêt (le 1er arrêt placé de cette quête dans le trajet), jamais une
  -- instance ; le camp seulement s'il est dans cette même zone
  local stopZone
  do
    local P = T.Planner
    local steps, cur = P and P.steps, P and P.cursor
    if steps and cur and q then
      for i = cur, #steps do
        local s = steps[i]
        if s and s.q == q and s.map and s.x and not s._done and not s._other then stopZone = Z.OutdoorZone(s.map) if stopZone then break end end
      end
    end
  end
  local hubZone = th and th.hubMap and Z.OutdoorZone(th.hubMap) or nil
  if stopZone and hubZone and hubZone ~= stopZone and th then th = { story = th.story, n = th.n } end
  if stopZone then zone = Z.MapName(stopZone)
  elseif hubZone then zone = Z.MapName(hubZone)
  else
    local z = _ns(try(G("GetRealZoneText"))) or _ns(try(G("GetZoneText")))
    if type(z) == "string" and z ~= "" then zone = z
    else
      local A = self._lastAllowed or self:Allowed()
      zone = A and A.here and Z.MapName(A.here) or nil
    end
  end
  local parts = { zone = zone, hub = th and th.hubName or nil, story = th and th.story and lowerFirst(th.story) or nil,
    n = th and th.n or nil, q = q }
  local s = zone or ""
  if parts.hub then s = (s ~= "" and (s .. " · ") or "") .. parts.hub end
  if parts.story then s = (s ~= "" and (s .. " : ") or "") .. parts.story end
  if parts.n and parts.n >= 2 and (parts.story or parts.hub) then s = s .. fmt(" (%d quêtes)", parts.n) end
  parts.full = (parts.hub or parts.story) and s or nil          -- sans camp ni histoire : pas de thème (la zone seule)
  return parts
end

-- (QUESTQA / ZONEFIX) zone d'une quête pour « Autres zones » : où est le travail (objectif, sinon remise), sinon la
-- zone de Questie, sinon celle que donne le journal
local function questZone(q, rec, lq, steps)
  local m
  for _, s in ipairs(steps or {}) do if s.k ~= "pick" and s.map then m = s.map break end end
  m = m or tonumber(rec and rec.zone)
  if not m and lq then
    m = tonumber(_ns((try(G("GetQuestUiMapID"), q))))
    if m and m <= 0 then m = nil end
  end
  return m and Z.ZoneOf(m) or nil
end
--- zone (uiMapID) d'une quête du journal / de l'export : où est son travail
function Z.QuestZone(q)
  q = tonumber(q)
  if not q then return nil end
  local P = T.Planner
  local lq = P and P.log and P.log[q]
  local rec
  local Xm = X()
  local exp = Xm and Xm.Data and Xm:Data()
  for _, r in ipairs(exp and exp.quests or {}) do if r.id == q then rec = r break end end
  return questZone(q, rec, lq, nil)
end
Z.FILL_MIN = 3                       -- ta zone a moins de 3 quêtes faisables : les voisines viennent en renfort
Z.ONWAY_S = 45                       -- remise juste hors zone : dans le trajet si elle coûte ≤ 45 s de détour

-- Builds the in-game route (called by Planner:BuildAuto inside its time-sliced job). Returns route, export.
function Z:BuildRoute(P, yieldFn)
  local E = P.Engine
  local ctx = P.CostCtx()
  local Xm = X()
  local exp = Xm and Xm.Data and Xm:Data()
  local recs = {}
  for _, r in ipairs(exp and exp.quests or {}) do recs[r.id] = r end
  local log = (Xm and Xm.LogSnapshot and Xm.LogSnapshot()) or {}
  P.log = log
  local level, group = ctx.level or myLevel(), groupSize()
  self.fill = nil                                     -- jugé strictement : ta zone d'abord
  local A = self:Allowed()
  -- (ZONEFIX) zone auto : ta zone seule ; ses voisines jugées à part, seulement pour la compléter si elle est vide
  local AN
  if A.mode == "auto" and A.maps and A.nb and next(A.nb) then
    AN = { mode = "auto", maps = {} }
    for m in pairs(A.maps) do AN.maps[m] = true end
    for m in pairs(A.nb) do AN.maps[m] = true end
  end
  -- (QUESTS 4.1) mode du cerveau des quêtes (QuestBrain) : « Rendre mes quêtes » = les remises des quêtes finies, toutes
  -- zones (vols / bateaux / foyer) ; « Quêtes à prendre » = les meilleures prises ; « Avec le groupe » = mode Groupe ;
  -- « Tout faire ici » = le trajet de la zone, comme avant
  local bmode = Z.BrainMode()
  self.brainMode = bmode
  local pickSet
  if bmode == "turnin" or bmode == "pickup" then
    A = { mode = bmode, any = true, here = A.here, cont = A.cont, label = A.label }
    AN = nil
    if bmode == "pickup" then
      local QBm = T.QuestBrain
      pickSet = QBm and QBm.RouteSet and QBm.RouteSet("pickup") or {}
      -- quêtes proposées par un PNJ rencontré que Questie ne connaît pas (ou sans Questie) : leur fiche apprise
      for q in pairs(pickSet) do
        if not recs[q] and QBm.SyntheticRec then recs[q] = QBm.SyntheticRec(q) end
      end
    end
  end
  local equal = (bmode == "turnin" or bmode == "pickup")   -- chaque arrêt compte autant : l'ordre = le chemin le plus court
  local leg = P.Leg
  local m, x, y
  if Geo.Player then m, x, y = Geo.Player() end
  local start = (m and x) and { map = m, x = x, y = y } or nil
  -- (QA-QUEST F1) parti loin (> 400 yd) depuis le dernier calcul : l'ordre d'avant ne tient plus (hystérésis remise
  -- à zéro, sinon « foyer → Orgrimmar » restait en tête une fois arrivé aux Salines)
  if start and self.lastStart then
    local d = worldDist(start, self.lastStart)
    if not d or d > 400 then self.lastKeys = nil end
  end
  self.lastStart = start
  local style = self:Style()
  if bmode == "group" then style = "group" end
  local maxS = (tonumber(opt("maxMin")) or 0) * 60
  if equal then maxS = 0 end
  local grey = E.GreyLevel(level)
  local isDone = Xm and Xm.IsQuestDone
  local requests, far, fill = {}, {}, {}
  local info, levels = { nonGrey = 0 }, {}
  local skipped = { ["ignorée"] = 0, zone = 0, loin = 0 }
  local masked, qzone = {}, {}
  local Qm = T.modules and T.modules.Quests
  local timers = (Qm and Qm.Timers and Qm.lastList and select(2, pcall(Qm.Timers, Qm.lastList))) or {}
  if type(timers) ~= "table" then timers = {} end
  -- zone de travail ÉTAPE PAR ÉTAPE (QUESTQA) : les objectifs d'ici sont gardés même quand la remise est dans une
  -- capitale (« La flamme sacrée » : la fiole se remplit en Orneval, on la rend à Thunder Bluff) ; « prendre » hors
  -- zone = toute la quête ; un objectif hors zone retire aussi la remise (pas finissable ici). (QA-QUEST F10) arrêt
  -- sans position (pas de Questie, pas de point du jeu) : jugé sur la zone de sa quête. → étapes gardées, dans la zone ?
  local function judge(q, rec, lq, steps, area)
    local qm
    local function outOf(s)
      if s.x then return not self:InZone(s.map, area) end
      if qm == nil then
        qm = tonumber(rec and rec.zone) or (lq and tonumber(_ns((try(G("GetQuestUiMapID"), q))))) or false
        if qm and qm <= 0 then qm = false end
      end
      return qm and not self:InZone(qm, area) or false
    end
    local keep, pickOut, objOut, objIn, turnOut = {}, false, false, false, false
    for _, s in ipairs(steps) do
      if outOf(s) then
        if s.k == "pick" then pickOut = true elseif s.k == "obj" then objOut = true else turnOut = true end
      else
        if s.k == "obj" then objIn = true end
        keep[#keep + 1] = s
      end
    end
    if pickOut or (objOut and not objIn) or (turnOut and not objIn and not objOut) then return nil, false end
    if objOut or turnOut then
      local k2 = {}
      for _, s in ipairs(keep) do if s.k ~= "turn" or not objOut then k2[#k2 + 1] = s end end
      return k2, true
    end
    return steps, true
  end
  local boostCache = {}
  local function admit(q, rec, lq, steps, extra, inZone, area)
    if #steps == 0 then return end
    if maxS > 0 and start and not extra then
      for _, s in ipairs(steps) do
        if s.x then
          if leg(start, s) > maxS then skipped.loin = skipped.loin + 1 return end
          break
        end
      end
    end
    local lv = (rec and rec.lv) or (lq and lq.lv) or 0
    if lv > grey and (not rec or not rec.zone or self:InZone(rec.zone, area)) then
      info.nonGrey = info.nonGrey + 1
      levels[#levels + 1] = lv
      if lq then info.logHere = (info.logHere or 0) + 1 end
    end
    -- s.value = score de l'ordre (jamais affiché) ; s.xp = XP réelle de l'arrêt (XP/h, détour : QA-QUEST F6)
    local mult = 1
    if style == "loot" then
      mult = boostCache[q] or lootBoost(q, lq)
      boostCache[q] = mult
    elseif style == "group" then
      local n = partyOn(q)
      local elite, grp = Z.Kind(q, rec, lq)
      mult = 1 + 0.75 * n + ((elite or grp) and group >= 2 and 0.5 or 0)
    end
    for _, s in ipairs(steps) do
      if style == "clear" or equal then s.value = 1000             -- finir la zone / rendre / prendre : chaque arrêt compte autant
      elseif style == "loot" then
        if s.k == "turn" then s.value = (s.value or 0) * mult * (lq and 1 or rewardBoost(q) / 1.2) end
      elseif mult ~= 1 then s.value = (s.value or 0) * mult end
      if timers[q] then s.value = (s.value or 0) * 5 + 5000 end   -- (QUESTQA) chrono en cours : d'abord
      if extra and not inZone then s.out = true end
    end
    requests[#requests + 1] = { q = q, steps = steps, inLog = lq ~= nil }
  end
  local function consider(q, rec, lq)
    if Z.SkippedQuest(q) then                         -- « Suivant » (QuestBrain.Skip) : pas pour cette session
      skipped["passée"] = (skipped["passée"] or 0) + 1
      if lq then masked["passée"] = masked["passée"] or {}; masked["passée"][#masked["passée"] + 1] = q end
      return
    end
    if bmode == "turnin" and not (lq and lq.done == 1) then return end
    if bmode == "pickup" and (lq or not (pickSet and pickSet[q])) then return end
    local ok, why = self:Accept(q, rec, lq, level, group)
    if not ok then
      skipped[why] = (skipped[why] or 0) + 1
      if lq then masked[why] = masked[why] or {}; masked[why][#masked[why] + 1] = q end   -- du journal seulement
      return
    end
    local v = E.QuestVerdict(rec or { lv = lq and lq.lv or level, o = {} }, ctx)
    if v == "impossible" and not lq then return end
    local steps = P:QuestSteps(q, rec, lq, ctx)
    if yieldFn then yieldFn() end
    if bmode == "pickup" then steps = { steps[1] } end   -- seulement la prise (le reste viendra en « Tout faire ici »)
    if #steps == 0 or (bmode == "pickup" and not (steps[1] and steps[1].k == "pick")) then return end
    local extra = self.char and self.char.extra[q]
    if extra then          -- détour choisi : gardé tel quel, arrêts hors zone marqués « hors zone »
      local inZone = true
      for _, s in ipairs(steps) do if s.x and not self:InZone(s.map, A) then inZone = false end end
      return admit(q, rec, lq, steps, extra, inZone, A)
    end
    local kept, inZone = judge(q, rec, lq, steps, A)
    if inZone then return admit(q, rec, lq, kept, nil, true, A) end
    if AN then
      local k2, in2 = judge(q, rec, lq, steps, AN)
      if in2 and #k2 > 0 then fill[#fill + 1] = { q = q, rec = rec, lq = lq, steps = k2 } end
    end
    skipped.zone = skipped.zone + 1
    if lq then
      far[#far + 1] = { q = q, rec = rec, lq = lq, steps = P:QuestSteps(q, rec, lq, ctx) }
      masked.zone = masked.zone or {}
      masked.zone[#masked.zone + 1] = q
      qzone[q] = questZone(q, rec, lq, steps)
    end
  end
  for q, lq in pairs(log) do consider(q, recs[q], lq) end
  for q, rec in pairs(recs) do
    if bmode ~= "turnin" and not log[q] and rec.st == "avail" and not (isDone and isDone(q)) and not (P.char and P.char.turned[q])
        and (rec.rl or 0) <= level and not rec.pre and not rec.preAny then
      consider(q, rec, nil)
    end
  end
  -- (ZONEFIX) ta zone n'a presque plus rien (< 3 quêtes) : ses voisines complètent, et c'est dit (« Orneval + Les
  -- Tarides ») ; sinon elles restent dans « Autres zones »
  local fillNames = {}
  if #fill > 0 and #requests < Z.FILL_MIN then
    local used, inFill = {}, {}
    for _, f in ipairs(fill) do
      inFill[f.q] = true
      admit(f.q, f.rec, f.lq, f.steps, nil, true, AN)
      for _, s in ipairs(f.steps) do
        local zm = s.map and Z.ZoneOf(s.map)
        if zm and not A.maps[zm] and not used[zm] then used[zm] = true; fillNames[#fillNames + 1] = Z.MapName(zm) end
      end
    end
    if next(used) then self.fill = used end
    skipped.zone = max(0, skipped.zone - #fill)
    local keepFar, keepMask = {}, {}
    for _, f in ipairs(far) do if not inFill[f.q] then keepFar[#keepFar + 1] = f end end
    for _, q in ipairs(masked.zone or {}) do if not inFill[q] then keepMask[#keepMask + 1] = q end end
    far, masked.zone = keepFar, (#keepMask > 0) and keepMask or nil
    tsort(fillNames)
  end
  local Rt = T.Route                                  -- trop dures / trop loin → proposition (Route.lua)
  if Rt and Rt.FilterRequests and Rt:IsEnabled() and not equal then
    local ok, res = pcall(Rt.FilterRequests, Rt, requests, recs, log, ctx)
    if ok and type(res) == "table" then requests = res end
  elseif Rt and equal then Rt.selection = nil end     -- rendre / prendre : rien n'est « mis de côté »
  tsort(requests, function(a, b) return a.q < b.q end)
  tsort(levels)
  if #levels > 0 then info.zoneMedian = levels[floor((#levels + 1) / 2)] end
  -- (ZONEFIX) thèmes : chaque arrêt porte celui de sa quête (bonus « même thème » de l'ordre)
  local themes = self:Themes(requests, recs, log)
  self.themes = themes
  for _, r in ipairs(requests) do
    local th = themes[r.q]
    for _, s in ipairs(r.steps) do s.theme = th and th.key or nil end
  end
  -- nodes → greedy → 2-opt / Or-opt → hysteresis
  local nodes = N.Nodes(requests, worldDist)
  local cap = tonumber(try(C("C_QuestLog", "GetMaxNumQuestsCanAccept"))) or 20
  local logCount = 0
  for _ in pairs(log) do logCount = logCount + 1 end
  local first = makeFirst(leg, start)
  -- (QA-QUEST F1) la pierre de foyer seulement quand RIEN n'est à moins de 5 min d'ici (sinon : foyer → Orgrimmar →
  -- vol → petite quête → vol retour, alors que la zone de quêtes est à 4 min à pied) ; Route l'affiche pareil
  self.noHearth = nil
  self.hearthAny = (bmode == "turnin") or nil
  if first and start and not self.hearthAny then
    for _, nd in ipairs(nodes) do
      if nd.x and leg(start, nd) <= 300 then first, self.noHearth = nil, true break end
    end
  end
  local legN = function(a, b) return leg(a, b) end
  if self.hearthAny and first then                   -- (QUESTS 4.1) rendre : la pierre de foyer compte à chaque étape
    local Rh = T.Route
    local bind = Rh and Rh.BindPlace and try(Rh.BindPlace)
    local D = T.Data and T.Data.Travel
    if type(bind) == "table" and bind.map and bind.x then
      local hs = ((D and D.hearthCast) or 10) + ((D and D.hearthLoad) or 5)
      local b = { map = bind.map, x = bind.x, y = bind.y }
      legN = function(a, c) return min(leg(a, c), hs + leg(b, c)) end
    end
  end
  local st0 = start or { }
  local bondK = (style == "story") and 4 or 1.3
  local ord = N.Greedy(nodes, st0, legN, { horizon = (style == "clear") and 60 or 40, cap = cap - 2, logCount = logCount,
    yield = yieldFn, first = first, bond = function(a, b) return (a.theme and a.theme == b.theme) and bondK or 1 end })
  local D = N.Matrix(nodes, ord, st0, legN, first, yieldFn)
  local greedy = {}
  for i, v in ipairs(ord) do greedy[i] = v end
  N.Improve(nodes, ord, D, yieldFn, 3)
  -- (QUESTQA) 2-opt / Or-opt ne regardent que le chemin : jamais un « Prendre » qui ferait déborder le journal
  if not N.CapOK(nodes, ord, cap - 2, logCount) then ord = greedy end
  -- (ZONEFIX) « Par histoire » : une histoire / un camp d'un seul tenant (le 2-opt les avait remêlés)
  if style == "story" then ord = N.ThemeGroup(nodes, ord, cap - 2, logCount) end
  local cost = N.Cost(D, ord)
  local kept = "new"
  if self.lastKeys and #ord > 1 then
    local old = N.Patch(nodes, ord, self.lastKeys, D)
    if old and not N.CapOK(nodes, old, cap - 2, logCount) then old = nil end
    if old then
      local oc = N.Cost(D, old)
      kept = N.Choose(cost, oc)
      if kept == "old" then ord, cost = old, oc end
    end
  end
  -- (QUESTS 4.1) « Avec le groupe » : les quêtes que le groupe a aussi, d'un seul tenant, avant les autres (le 2-opt ne voit
  -- que le chemin et les remêlait) ; ce qui est à portée de main reste devant (NearFirst, ci-dessous)
  if bmode == "group" and group >= 2 then
    local shared = {}
    for _, r in ipairs(requests) do if r.inLog and partyOn(r.q) > 0 then shared[r.q] = true end end
    ord = N.TimersFirst(nodes, ord, shared, cap - 2, logCount)
  end
  ord = N.NearFirst(nodes, ord, start, worldDist, N.NEAR, cap - 2, logCount)
  ord = N.PositionlessLast(nodes, ord, cap - 2, logCount)   -- (QA-QUEST F10) sans lieu connu : au bout
  -- (QA-QUEST F2) chrono en cours : ses arrêts d'abord (le bonus du glouton ne survit pas au 2-opt / Or-opt, à
  -- l'hystérésis ni à NearFirst, qui ne voient que le chemin) ; précédences et journal gardés
  ord = N.TimersFirst(nodes, ord, timers, cap - 2, logCount)
  -- (ZONEFIX) quête finie à rendre juste hors de la zone, SUR le chemin (≤ 45 s de détour) : dans le trajet, marquée
  -- « hors zone » ; plus loin, elle reste une proposition (détour rentable) ou une ligne « Autres zones »
  local onWay = {}
  if start then
    for _, f in ipairs(far) do
      local turn = f.lq and f.lq.done == 1 and f.steps[#f.steps]
      if turn and turn.k == "turn" and turn.x and not onWay[f.q] then
        local bestAt, bestC = nil, huge
        local a = start
        for k = 0, #ord do
          local b = nodes[ord[k + 1]]
          if k > 0 and nodes[ord[k]].x then a = nodes[ord[k]] end
          local c = leg(a, turn) + ((b and b.x) and (leg(turn, b) - leg(a, b)) or 0)
          if c < bestC then bestAt, bestC = k, c end
          if yieldFn then yieldFn() end
        end
        if bestAt and bestC <= Z.ONWAY_S then
          turn.out, turn.onWay, turn.theme = true, true, nil
          nodes[#nodes + 1] = { steps = { turn }, map = turn.map, x = turn.x, y = turn.y, svc = turn.svc or 15,
            value = turn.value or 0, qs = { [f.q] = 3 }, key = skey(turn) }
          tinsert(ord, bestAt + 1, #nodes)
          onWay[f.q] = true
        end
      end
    end
  end
  -- (QUESTQA) journal plein : les quêtes à prendre attendent (dit une fois, en clair, dans la vue du trajet)
  local picks = 0
  for _, r in ipairs(requests) do if r.steps[1] and r.steps[1].k == "pick" then picks = picks + 1 end end
  self.logFull = (logCount >= cap and picks > 0) and { n = logCount, max = cap, waiting = picks } or nil
  -- flatten (un arrêt ajouté après le calcul de la matrice : trajet recalculé ici)
  local steps, keys, t, xp, prev, prevNd = {}, {}, 0, 0, 0, nil
  for _, i in ipairs(ord) do
    local nd = nodes[i]
    local tt = D[prev] and D[prev][i]
    if not tt then tt = (prevNd and prevNd.x and leg(prevNd, nd)) or (start and leg(start, nd)) or 0 end
    t = t + tt
    tsort(nd.steps, function(a, b)
      if a.q ~= b.q then return (a.q or 0) < (b.q or 0) end
      return (tonumber(a.o) or 0) < (tonumber(b.o) or 0)
    end)
    for _, s in ipairs(nd.steps) do
      t = t + (s.svc or 0)
      xp = xp + (s.xp or 0)                 -- (QA-QUEST F6) XP réelle, pas le score de l'ordre (×1,5–1,75)
      s.area = nd.area
      s.eta = floor(t / 60 + 0.5)
      steps[#steps + 1] = s
      keys[#keys + 1] = skey(s)
    end
    prev = i
    if nd.x then prevNd = nd end
  end
  self.lastKeys, self.kept = keys, kept
  -- « Autres zones : 10 quêtes (Les Tarides, Mille pointes…) » : quêtes du journal laissées hors du trajet par la
  -- zone (une remise prise en chemin n'en fait plus partie)
  if masked.zone and next(onWay) then
    local km = {}
    for _, q in ipairs(masked.zone) do if not onWay[q] then km[#km + 1] = q end end
    masked.zone = (#km > 0) and km or nil
  end
  local others, byZone = { n = 0, zones = {} }, {}
  for _, q in ipairs(masked.zone or {}) do
    local zm = qzone[q]
    local name = zm and Z.MapName(zm) or "zone inconnue"
    local e = byZone[name]
    if not e then e = { name = name, map = zm, n = 0, titles = {} }; byZone[name] = e; others.zones[#others.zones + 1] = e end
    e.n = e.n + 1
    e.titles[#e.titles + 1] = (log[q] and log[q].t) or (recs[q] and recs[q].t) or ("quête " .. q)
    others.n = others.n + 1
  end
  tsort(others.zones, function(a2, b2) if a2.n ~= b2.n then return a2.n > b2.n end return a2.name < b2.name end)
  self.others = others.n > 0 and others or nil
  self.skipped, self.masked = skipped, masked
  -- detour proposal: a finished quest / objectives out of the zone worth far more than the route rate
  self.detour = nil
  local minutes = max(1, floor(t / 60 + 0.5))
  local xph = (t > 0 and style ~= "clear") and floor(xp * 3600 / t) or 0
  if opt("detour") ~= false and start then
    local rate = (t > 0) and (xp * 3600 / t) or 0
    -- (QA-QUEST F21) coût du détour = son insertion la moins chère dans le trajet (entre deux arrêts, ou au bout), et
    -- non plus 2 × le trajet depuis ici : une remise à Fossoyeuse sur le chemin de Moulin-de-Tarren ne coûte presque rien
    local pts = { start }
    for _, i in ipairs(ord) do if nodes[i].x then pts[#pts + 1] = nodes[i] end end
    local function insertCost(turn)
      local bestC = huge
      for k = 1, #pts do
        local a, b = pts[k], pts[k + 1]
        local c = leg(a, turn) + (b and (leg(turn, b) - leg(a, b)) or 0)
        if c < bestC then bestC = c end
        if yieldFn then yieldFn() end
      end
      return max(1, bestC)
    end
    local best
    for _, f in ipairs(far) do
      if f.lq.done == 1 and not onWay[f.q] then
        local turn = f.steps[#f.steps]
        local s = turn and turn.x and insertCost(turn)
        local gain = turn and (turn.xp or turn.value) or 0
        if s and s < huge and s > 0 then
          local r = gain * 3600 / (s + 30)
          if r >= max(1.5 * rate, 1) and gain >= 0.5 * E.QuestXPEstimate(level) and (not best or r > best.r) then
            best = { q = f.q, title = (f.lq and f.lq.t) or (f.rec and f.rec.t) or ("quête " .. f.q), xp = floor(gain),
              sec = s, zone = Z.MapName(turn.map), r = r, cross = Z.Cont(turn.map) ~= A.cont }
          end
        end
      end
    end
    self.detour = best
  end
  local zoneLabel = A.label or "?"
  if #fillNames > 0 then zoneLabel = zoneLabel .. " + " .. tconcat(fillNames, " + ") end
  -- (QUESTS 4.1) le nom de zone du trajet = la zone de ses arrêts (jamais une instance : capture 4.0.5 « Kraal de
  -- Tranchebauge » au-dessus d'un trajet aux Tarides / Mille pointes) ; rendre / prendre : les zones traversées
  local sz = Z.StepsZoneLabel(steps)
  if (equal or (A.here and Z.IsInstance(A.here))) and sz then zoneLabel = sz end
  self.label = zoneLabel
  local route = { id = "auto", title = "Trajet TreeAI", zone = zoneLabel, steps = steps, minutes = minutes, xph = xph,
    cross = A.cross, style = style, mode = bmode }
  local v, why = E.RouteVerdict(route, ctx, info)
  if equal then                                      -- « Changez de zone » / « tuer rapporte plus » n'ont pas de sens ici
    if #steps == 0 then v, why = "impossible", (bmode == "turnin") and "aucune quête finie à rendre" or "aucune bonne quête à prendre ici"
    elseif v ~= "impossible" then v, why = "ok", nil end
  end
  route.verdict, route.reasons = v, why and { why } or {}
  if Rt and Rt.selection then Rt.selection.zone = (A.mode ~= "continent") and zoneLabel or nil end
  return route, exp
end

-- Plan de l'appli : quêtes ignorées, refusées par les règles du trajet (donjon / élite seul / JcJ / raid / échouée /
-- trop haute) et arrêts hors zone masqués (jamais de chemin vers un autre continent non choisi). Un arrêt sans
-- position est jugé sur la zone de sa quête (export). → nombre d'arrêts masqués ; self.masked[why] = quêtes.
local recMemo = setmetatable({}, { __mode = "k" })
local LEADS = { fly = true, boat = true, zep = true, hs = true, fp = true }
local function recsOf(exp)
  if type(exp) ~= "table" then return {} end
  local r = recMemo[exp]
  if r and r.n == #(exp.quests or {}) then return r.t end
  local t = {}
  for _, q in ipairs(exp.quests or {}) do if tonumber(q.id) then t[tonumber(q.id)] = q end end
  recMemo[exp] = { n = #(exp.quests or {}), t = t }
  return t
end
Z.Recs = recsOf
function Z:MaskSteps(steps, log)
  if not (self:IsEnabled() and type(steps) == "table") then return 0 end
  local A = self:Allowed()
  -- (QUESTS 4.1) trajet en jeu « Rendre mes quêtes » / « Quêtes à prendre » : toutes zones (il a été fait ainsi)
  local P0 = T.Planner
  local bm = Z.BrainMode()
  if P0 and P0.src ~= "file" and (bm == "turnin" or bm == "pickup") then A = { any = true } end
  local ch = self.char or {}
  local recs = recsOf(X() and X().Data and X():Data())
  log = log or (T.Planner and T.Planner.log) or {}
  local level, group = myLevel(), groupSize()
  local out, why = {}, {}                  -- par quête : refusée par les règles (out[q] = raison) ou false
  for _, s in ipairs(steps) do
    local q = tonumber(s.q)
    s._hide = nil
    if q and not s._done then
      if out[q] == nil and Z.SkippedQuest(q) then out[q], why[q] = "passée", "passée" end
      if out[q] == nil then
        local ok, w = self:Accept(q, recs[q], log[q], level, group)
        out[q] = (not ok) and w or false
        if not ok then why[q] = w end
      end
      local hide = out[q] or nil
      -- (ZONEFIX) remise hors zone prise en chemin par le trajet en jeu (≤ 45 s de détour) : gardée
      if not hide and not ch.extra[q] and not s.onWay then
        -- arrêt hors zone : seulement CET arrêt (objectif ailleurs, remise dans une capitale) ; « prendre » hors
        -- zone = toute la quête (impossible de faire la suite sans la prendre). Sans position : zone de la quête.
        local m
        if s.x then m = s.map else m = recs[q] and recs[q].zone end
        if m and not self:InZone(m, A) then
          hide = "zone"
          if s.k == "pick" then out[q] = "zone" end
          why[q] = why[q] or "zone"
        end
      end
      s._hide = hide
    end
  end
  self.maskedPlan = {}
  for q, w in pairs(why) do
    self.maskedPlan[w] = self.maskedPlan[w] or {}
    self.maskedPlan[w][#self.maskedPlan[w] + 1] = q
  end
  -- vol / bateau / foyer : gardé tant qu'un arrêt de quête gardé le suit (c'est le chemin pour y aller)
  local n, later = 0, false
  for i = #steps, 1, -1 do
    local s = steps[i]
    local q = tonumber(s.q)
    if (q and (s._hide or out[q])) or (not q and s.x and not s._done and not later and not self:InZone(s.map, A))
        or (not q and not later and not s._done and LEADS[s.k or ""]) then   -- transport vers rien de gardé
      if not s._done and not s._other then n = n + 1 end
      s._other, s._zone, s._why = true, true, q and (s._hide or out[q]) or "zone"
    else
      s._zone, s._why = nil, nil
      if q and not s._done and not s._other then later = true end
    end
  end
  return n
end
-- « 2 quêtes de donjon, 1 élite hors trajet » (infobulle de la ligne « Zone »), nil sans rien de masqué
local WHY_TXT = { ["passée"] = "passée (Suivant)", grise = "grise", donjon = "de donjon", ["donjon fait"] = "d'un donjon déjà fait", ["élite"] = "élite", JcJ = "JcJ",
  raid = "de raid", ["échouée"] = "échouée", niveau = "trop haute", groupe = "de groupe", zone = "hors zone",
  ["ignorée"] = "ignorée" }
function Z:MaskedText()
  local parts = {}
  local P = T.Planner
  local src = (P and P.src == "file") and self.maskedPlan or self.masked
  for _, w in ipairs({ "donjon", "donjon fait", "élite", "JcJ", "raid", "échouée", "niveau", "grise", "groupe", "zone", "ignorée", "passée" }) do
    local l = src and src[w]
    if l and #l > 0 then parts[#parts + 1] = fmt("%d %s", #l, (#l > 1 and (w == "grise" and "grises" or w == "passée" and "passées (Suivant)")) or WHY_TXT[w]) end
  end
  if #parts == 0 then return nil end
  return "Hors trajet : " .. tconcat(parts, ", ")
end

-- export pour l'appli (TreeAIDB.export.work) + zones à exporter (quêtes à prendre)
function Z:ExportWork()
  if not self:IsEnabled() then return nil end
  local A = self:Allowed()
  -- (QUESTQA) les réglages du trajet partent avec : l'appli applique les mêmes règles (rules.py)
  local w = { mode = A.mode, ignore = {}, extra = {}, opts = { elites = opt("elites") and true or false,
    groupQ = opt("groupQ") ~= false, dungeons = opt("dungeons") and true or false, gap = tonumber(opt("gap")) or 3 } }
  if A.any then
    w.mode = nil
  elseif A.maps then
    w.maps = {}
    for m in pairs(A.maps) do w.maps[#w.maps + 1] = m end
    tsort(w.maps)
  else
    w.cont = A.cont
  end
  for q in pairs(self.char and self.char.ignore or {}) do if tonumber(q) then w.ignore[#w.ignore + 1] = tonumber(q) end end
  for q in pairs(self.char and self.char.extra or {}) do if tonumber(q) then w.extra[#w.extra + 1] = tonumber(q) end end
  tsort(w.ignore)
  tsort(w.extra)
  return w
end
-- (QUESTS 4.1) zones dont les quêtes à prendre sont exportées : la zone de travail + (auto) ses voisines — « Quêtes à
-- prendre » classe aussi celles d'à côté (le trajet « Tout faire ici », lui, reste dans la zone de travail)
function Z:ExportZones()
  local A = self:IsEnabled() and self:Allowed() or nil
  if not (A and A.maps) then return nil end
  if not (A.mode == "auto" and A.nb and next(A.nb)) then return A.maps end
  local out = {}
  for m in pairs(A.maps) do out[m] = true end
  for m in pairs(A.nb) do out[m] = true end
  return out
end

-- ===================================================================== actions
function Z:SetMode(mode, map)
  local ch = self.char
  if not ch then return end
  if mode == "zone" and tonumber(map) then
    map = tonumber(map)
    if ch.mode ~= "zones" then ch.zones = {} end
    ch.mode = "zones"
    if ch.zones[map] then ch.zones[map] = nil else ch.zones[map] = true end
    if not next(ch.zones) then ch.mode = "auto" end
  elseif mode == "continent" then ch.mode = "continent"
  else ch.mode, ch.zones = "auto", {} end
  if T.Export and T.Export.Dirty then pcall(T.Export.Dirty, T.Export) end   -- quêtes à prendre dans la nouvelle zone
  self:Bump()
end
-- (ZONEFIX) choisir UNE zone d'un clic (liste « Zones à faire ») : elle seule, tout de suite (le menu, lui, coche
-- et décoche plusieurs zones)
function Z:PickZone(map, by)
  local ch = self.char
  map = tonumber(map)
  if not (ch and map) then return false end
  ch.zoneBy = (by == "brain") and "brain" or "player"      -- (ZONEFIX) qui l'a choisie (pilote auto ou joueur)
  ch.mode, ch.zones = "zones", { [map] = true }
  self.lastKeys, self._lastAllowed = nil, nil
  if T.Export and T.Export.Dirty then pcall(T.Export.Dirty, T.Export) end
  self:Bump()
  return true
end
function Z:Ignore(q, title)
  q = tonumber(q)
  if not (q and self.char) then return false end
  self.char.ignore[q] = cut(title or ("quête " .. q), 60)
  self:Bump()
  return true
end
function Z:Unignore(q)
  if q == nil then self.char.ignore = {} else self.char.ignore[tonumber(q) or q] = nil end
  self:Bump()
end
function Z:AddDetour()
  local d = self.detour
  if not (d and self.char) then return false end
  self.char.extra[d.q] = true
  self.detour = nil
  self:Bump()
  return true
end
function Z:Recalc()
  self.lastKeys = nil
  local P = T.Planner
  if not P then return end
  if P.char then P.char.repaired = nil end
  P.steps, P.key = nil, nil
  self.rev = (self.rev or 0) + 1
  if P.Refresh then P:Refresh(true) end
end
function Z:TogglePause()
  local R = T.Route
  if R and R.TogglePause then R:TogglePause() end
end
function Z:Paused() local R = T.Route return R and R.char and R.char.paused or false end
function Z:OnOptionChanged() self:Bump() end

-- boussole : l'arrêt en cours = l'arrêt 1 de la carte (ZONEFIX : calculé par Route, le même objet que la carte et la
-- page Trajet ; aussi quand ce module est coupé). Gardé ici pour l'ancien contrat (Compass.BuildTargets, tests).
function Z.CompassTarget(me)
  local Rt = T.Route
  if not (Rt and Rt.CompassTarget) then return nil end
  return Rt:CompassTarget(me)
end
function Z.LegTarget(me) local Rt = T.Route return Rt and Rt.LegTarget and Rt.LegTarget(me) or nil end
function Z:OnStep()
  if inCombat() then return end
  local Cm = T.Compass
  if Cm and type(Cm.Rebuild) == "function" and opt("compass") ~= false then pcall(Cm.Rebuild, "plan") end
end

-- ===================================================================== quêtes par zone (QuestieDB, découpé)
-- counts.n[map] = quêtes disponibles à mon niveau (± écart), calculé une fois par niveau, hors combat
function Z:CountZones()
  local lvl = myLevel()
  if self.counts and self.counts.lvl == lvl then return self.counts end
  if self._counting or inCombat() then return self.counts end
  local db = X() and X().Source and try(X().Source, X())
  -- n[m] = quêtes à mon niveau pas encore faites ; (ZONEFIX) tot[m] = toutes celles de ma tranche, faites comprises
  -- (« déjà finie » quand n = 0 mais tot > 0), lo / hi = leurs niveaux ; quêtes de l'autre faction écartées
  local n, tot, lo, hi = {}, {}, {}, {}
  local function add(m, ql, done)
    if not m then return end
    tot[m] = (tot[m] or 0) + 1
    if done then return end
    n[m] = (n[m] or 0) + 1
    if ql > 0 then
      if not lo[m] or ql < lo[m] then lo[m] = ql end
      if not hi[m] or ql > hi[m] then hi[m] = ql end
    end
  end
  if not db then
    local exp = X() and X().Data and X():Data()
    for _, r in ipairs(exp and exp.quests or {}) do
      local m = tonumber(r.zone)
      if m and (r.lv or 0) >= lvl - 4 and (r.lv or 0) <= lvl + 3 then add(m, tonumber(r.lv) or 0, false) end
    end
    self.counts = { lvl = lvl, n = n, tot = tot, lo = lo, hi = hi }
    return self.counts
  end
  self._counting = true
  local ids = try(db.ids) or {}
  local i, gap = 1, tonumber(opt("gap")) or 3
  local isDone = X().IsQuestDone
  local _, _, raceId = try(G("UnitRace"), "player")
  raceId = tonumber(raceId)
  local rbit = raceId and ((db.enum and type(db.enum.raceMaskById) == "table" and tonumber(db.enum.raceMaskById[raceId]))
    or 2 ^ (raceId - 1)) or nil
  local ticker
  local function step()
    if inCombat() then return end
    local t0 = try(G("debugprofilestop"))
    local k = 0
    while i <= #ids do
      local id = ids[i]
      i, k = i + 1, k + 1
      local zone = tonumber(db.Q(id, "zoneOrSort")) or 0
      if zone > 0 then
        local ql, rl = tonumber(db.Q(id, "questLevel")) or 0, tonumber(db.Q(id, "requiredLevel")) or 0
        if rl <= lvl and ql >= lvl - 4 and ql <= lvl + gap then
          local mask = rbit and tonumber(db.Q(id, "requiredRaces")) or 0
          if mask == 0 or floor(mask / rbit) % 2 == 1 then add(db.map(zone), ql, isDone and isDone(id)) end
        end
      end
      if k % 40 == 0 then
        local t1 = try(G("debugprofilestop"))
        if not (t0 and t1) or t1 - t0 > 2 then return end
      end
    end
    Z.counts, Z._counting = { lvl = lvl, n = n, tot = tot, lo = lo, hi = hi }, false
    if ticker then ticker:Cancel() end
    if Z.menuOpen then Z:FillMenu() end
  end
  ticker = T.NewTicker and T:NewTicker(0.05, step, self)
  if not ticker then while self._counting do step() end end
  return self.counts
end
-- (ZONEPICK) une tâche découpée (toutes les 0,05 s, ≤ 2 ms par tranche, arrêtée par la tâche elle-même quand elle a fini) :
-- le passage honnête de QuestBrain (« Choisir ma zone ») — jamais d'OnUpdate permanent
function Z.SliceJob(step)
  return T.NewTicker and T:NewTicker(0.05, step, Z) or nil
end
-- zones offered in the menu / options: same continent (+ the picked ones), most quests at my level first ;
-- (QA-QUEST F14) + les meilleures zones d'un autre continent (≥ 3 quêtes à mon niveau, 4 au plus) : sinon
-- « un autre continent n'est pris que si tu le coches » ne pouvait jamais se cocher à la souris.
-- (F24) e.n = nil tant que le comptage n'est pas fini (jamais « · 0 » pendant « comptage des quêtes »)
function Z:ZoneChoices()
  local A = self:Allowed()
  local ready = self.counts and self.counts.n
  local function cnt(m) return ready and (ready[m] or 0) or nil end
  local list, seen = {}, {}
  for _, m in ipairs(self:KnownZones(A.cont)) do
    list[#list + 1] = { map = m, n = cnt(m), name = Z.MapName(m) }
    seen[m] = true
  end
  for m in pairs(self.char and self.char.zones or {}) do
    m = tonumber(m)
    if m and not seen[m] then
      seen[m] = true
      list[#list + 1] = { map = m, n = cnt(m), name = Z.MapName(m), far = true }
    end
  end
  if ready and A.cont then
    local fz = {}
    for m, n in pairs(ready) do
      local c = (not seen[m] and n >= 3 and Z.ZoneOf(m) == m) and Z.Cont(m) or nil
      if c and c ~= A.cont then fz[#fz + 1] = { map = m, n = n, name = Z.MapName(m), far = true } end
    end
    tsort(fz, function(a, b) if a.n ~= b.n then return a.n > b.n end return a.name < b.name end)
    for i = 1, min(4, #fz) do list[#list + 1] = fz[i] end
  end
  tsort(list, function(a, b)
    local an, bn = a.n or 0, b.n or 0
    if an ~= bn then return an > bn end
    return a.name < b.name
  end)
  return list
end

-- (ZONEFIX) « Zones à faire » (page Trajet) : pour ton niveau, chaque zone avec ses niveaux de quêtes, le nombre de
-- quêtes à prendre et de quêtes du journal, le temps pour y aller (vols compris), « déjà finie » quand il n'y reste rien
-- (mais qu'il y en avait à ta tranche). Clic = cette zone. → liste triée : ta zone, puis la plus fournie, la plus proche.
local function zoneAnchor(m)
  local D = T.Data and T.Data.Travel
  local Rt = T.Route
  local fac = Rt and Rt.Faction and Rt.Faction()
  local best
  for id, r in pairs(D and D.taxi or {}) do
    if r[5] == m and (not Rt or not Rt.FactionOK or Rt.FactionOK(r[8], fac)) and (not best or id < best.id) then
      best = { id = id, map = r[5], x = r[6], y = r[7] }
    end
  end
  return best or { map = m, x = 50, y = 50 }
end
function Z:ZonesToDo(limit)
  limit = limit or 10
  local A = self:Allowed()
  local c = self.counts or {}
  local n, tot, lo, hi = c.n or {}, c.tot or {}, c.lo or {}, c.hi or {}
  local P = T.Planner
  local log = (P and P.log) or {}
  local recs = recsOf(X() and X().Data and X():Data())
  local inLog = {}
  for q, lq in pairs(log) do
    if not (lq.failed == 1 or lq.failed == true) then
      local m = questZone(q, recs[q], lq, nil)
      if m then inLog[m] = (inLog[m] or 0) + 1 end
    end
  end
  local set = {}
  for m in pairs(tot) do set[m] = true end
  for m in pairs(inLog) do set[m] = true end
  if A.here then set[A.here] = true end
  local Gp = Geo.Player
  local pm, px, py
  if Gp then pm, px, py = Gp() end
  local here = (pm and px) and { map = pm, x = px, y = py } or nil
  local Rt = T.Route
  local list = {}
  for m in pairs(set) do
    if Z.ZoneOf(m) == m then
      local e = { map = m, name = Z.MapName(m), avail = n[m] or 0, inLog = inLog[m] or 0, lvMin = lo[m], lvMax = hi[m],
        here = m == A.here, cross = A.cont ~= nil and Z.Cont(m) ~= nil and Z.Cont(m) ~= A.cont }
      e.done = (tot[m] or 0) > 0 and e.avail == 0 and e.inLog == 0
      e.on = self.char and self.char.mode == "zones" and self.char.zones[m] and true or false
      if (e.avail + e.inLog > 0 or e.done or e.here) and not (e.cross and e.avail < 3 and e.inLog == 0) then
        list[#list + 1] = e
      end
    end
  end
  tsort(list, function(a, b)
    if a.here ~= b.here then return a.here end
    if a.done ~= b.done then return not a.done end
    local na, nb = a.avail + a.inLog, b.avail + b.inLog
    if na ~= nb then return na > nb end
    return a.name < b.name
  end)
  local out, nAct, nFin = {}, 0, 0
  for _, e in ipairs(list) do
    if e.here or (not e.done and nAct < limit) or (e.done and nFin < 3) then
      if e.done then nFin = nFin + 1 elseif not e.here then nAct = nAct + 1 end
      if here and not e.here and Rt and Rt.LegSeconds then
        local s2 = Rt.LegSeconds(here, zoneAnchor(e.map))
        e.sec = s2
      end
      out[#out + 1] = e
    end
  end
  return out
end
--- une ligne de la liste : « Orneval · niv 24-30 · 12 à prendre, 5 en cours · tu y es »
function Z.ZoneRowText(e)
  local parts = { e.name }
  if e.lvMin and e.lvMax then parts[#parts + 1] = (e.lvMin == e.lvMax) and fmt("niv %d", e.lvMin) or fmt("niv %d-%d", e.lvMin, e.lvMax) end
  if e.done then parts[#parts + 1] = "déjà finie"
  else
    local q = {}
    if e.avail > 0 then q[#q + 1] = fmt("%d à prendre", e.avail) end
    if e.inLog > 0 then q[#q + 1] = fmt("%d en cours", e.inLog) end
    if #q > 0 then parts[#parts + 1] = tconcat(q, ", ") end
  end
  local where
  if e.here then where = "tu y es"
  elseif e.sec then
    local Pl = T.Plain
    where = (Pl and Pl.Minutes and Pl.Minutes(e.sec)) or fmt("%d min", floor(e.sec / 60 + 0.5))
  elseif e.cross then where = "autre continent" end
  return tconcat(parts, " · "), where
end

-- ===================================================================== menu (cadre à nous : clic gauche zone, clic droit arrêt)
local menu
local MENU_ROW = 18
local function buildMenu()
  if menu or not G("CreateFrame") then return menu end
  local tmpl = G("BackdropTemplateMixin") and "BackdropTemplate" or nil
  menu = G("CreateFrame")("Frame", "TreeAIZonePlanMenu", G("UIParent"), tmpl)
  menu:SetFrameStrata("DIALOG")
  menu:SetClampedToScreen(true)
  menu:EnableMouse(true)
  if menu.SetBackdrop then
    menu:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 10, insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    menu:SetBackdropColor(0.05, 0.06, 0.08, 0.96)
    menu:SetBackdropBorderColor(0.2, 0.8, 0.4, 0.6)
  end
  menu.rows = {}
  menu:SetScript("OnHide", function() Z.menuOpen = false end)
  if menu.RegisterEvent and pcall(menu.RegisterEvent, menu, "GLOBAL_MOUSE_DOWN") then
    menu:SetScript("OnEvent", function(self)
      if self:IsShown() and not (self.IsMouseOver and self:IsMouseOver()) then self:Hide() end
    end)
  end
  local list = G("UISpecialFrames")
  if type(list) == "table" then list[#list + 1] = "TreeAIZonePlanMenu" end   -- Échap ferme
  menu:Hide()
  return menu
end
local function menuRow(i)
  local b = menu.rows[i]
  if b then return b end
  b = G("CreateFrame")("Button", nil, menu)
  b:SetHeight(MENU_ROW)
  b:RegisterForClicks("LeftButtonUp")
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("LEFT", b, "LEFT", 8, 0)
  b.text:SetPoint("RIGHT", b, "RIGHT", -8, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  local hl = b:CreateTexture(nil, "HIGHLIGHT")
  hl:SetAllPoints(b)
  if hl.SetColorTexture then hl:SetColorTexture(1, 1, 1, 0.08) end
  b:SetScript("OnClick", function(self)
    local e = self.e
    if not (e and e.fn) then return end
    if not e.keep then menu:Hide() end
    T:UserAction(e.fn)
    if e.keep and menu:IsShown() then Z:FillMenu() end
  end)
  menu.rows[i] = b
  return b
end
-- entries = { { text, fn?, keep?, head? } }
function Z:ShowMenu(entries, kind)
  if not buildMenu() then return false end
  self.menuKind, self.menuEntries = kind, entries
  for _, r in ipairs(menu.rows) do r:Hide(); r.e = nil end
  local probe = menuRow(1)
  local w = 200
  for _, e in ipairs(entries) do
    probe.text:SetText(e.text)
    w = max(w, floor((probe.text:GetStringWidth() or 0) + 24))
  end
  w = min(w, 340)
  local y = -5
  for i, e in ipairs(entries) do
    local b = menuRow(i)
    b.e = e
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", menu, "TOPLEFT", 3, y)
    b:SetWidth(w - 6)
    b.text:SetText(e.text)
    if e.head then b.text:SetTextColor(0.2, 0.8, 0.4) elseif e.dim then b.text:SetTextColor(0.6, 0.6, 0.6)
    else b.text:SetTextColor(1, 1, 1) end
    b:Show()
    y = y - MENU_ROW
  end
  menu:SetSize(w, -y + 5)
  if not menu:IsShown() then
    menu:ClearAllPoints()
    local cx, cy = try(G("GetCursorPosition"))
    local UI = G("UIParent")
    local sc = UI and UI.GetEffectiveScale and UI:GetEffectiveScale() or 1
    if tonumber(cx) and tonumber(cy) and sc > 0 then menu:SetPoint("TOPLEFT", UI, "BOTTOMLEFT", cx / sc + 8, cy / sc - 4)
    else menu:SetPoint("CENTER", UI, "CENTER", 0, 0) end
  end
  menu:Show()
  self.menuOpen = true
  return true
end
function Z:GetMenu() return menu, self.menuEntries end
function Z:ZoneEntries()
  local ch = self.char or {}
  local A = self:Allowed()
  local mode = ch.mode or "auto"
  local out = { { text = "Mode du trajet", head = true } }
  local cur = self:StyleIndex()
  for i, e in ipairs(Z.STYLES) do
    out[#out + 1] = { text = (i == cur and "● " or "○ ") .. e.name, fn = function() Z:SetStyle(i) end, keep = true }
  end
  out[#out + 1] = { text = "Zone de travail", head = true }
  out[#out + 1] = { text = (mode == "auto" and "● " or "○ ") .. "Auto : ma zone (ses voisines si elle est vide)", fn = function() Z:SetMode("auto") end, keep = true }
  out[#out + 1] = { text = (mode == "continent" and "● " or "○ ") .. "Toute la région (continent)", fn = function() Z:SetMode("continent") end, keep = true }
  out[#out + 1] = { text = "Zones (quêtes à mon niveau)", head = true }
  local shown = 0
  for _, e in ipairs(self:ZoneChoices()) do
    if shown >= 12 then break end
    local on = mode == "zones" and ch.zones[e.map]
    local cross = A.cont and Z.Cont(e.map) and Z.Cont(e.map) ~= A.cont
    out[#out + 1] = { text = fmt("%s %s%s%s", on and "☑" or "☐", cut(e.name, 28), e.n and fmt(" · %d", e.n) or "",
      cross and " (autre continent)" or ""), fn = function() Z:SetMode("zone", e.map) end, keep = true }
    shown = shown + 1
  end
  if self._counting then out[#out + 1] = { text = "… comptage des quêtes", dim = true } end
  out[#out + 1] = { text = "Trajet", head = true }
  out[#out + 1] = { text = "Recalculer maintenant", fn = function() Z:Recalc() end }
  out[#out + 1] = { text = self:Paused() and "Reprendre le trajet" or "Mettre le trajet en pause", fn = function() Z:TogglePause() end }
  local n = 0
  for _ in pairs(ch.ignore or {}) do n = n + 1 end
  if n > 0 then out[#out + 1] = { text = fmt("Reprendre les %d quête%s ignorée%s", n, n > 1 and "s" or "", n > 1 and "s" or ""),
    fn = function() Z:Unignore() end } end
  out[#out + 1] = { text = "Réglages du trajet…", fn = function() if T.OpenOptions then T:OpenOptions("ZonePlan") end end }
  return out
end
-- (QUESTS 4.1) menu « Que faire » : les quatre modes (avec leur nombre) puis l'ordre des arrêts dans la zone
function Z:ModeEntries()
  local QBm = T.QuestBrain
  local out = { { text = "Que faire ?", head = true } }
  local ok, modes = pcall(QBm and QBm.Modes or function() return {} end)
  for _, m in ipairs(ok and modes or {}) do
    local n = (m.count and m.count > 0) and fmt(" (%d)", m.count) or ""
    out[#out + 1] = { text = (m.active and "● " or "○ ") .. m.label .. n, fn = function() QBm.SetMode(m.id) end }
  end
  out[#out + 1] = { text = "Ordre dans la zone", head = true }
  local cur = self:StyleIndex()
  for i, e in ipairs(Z.STYLES) do
    out[#out + 1] = { text = (i == cur and "● " or "○ ") .. e.name, fn = function() Z:SetStyle(i) end, keep = true }
  end
  return out
end
function Z:ShowModeMenu()
  if menu and menu:IsShown() and self.menuKind == "mode" then menu:Hide() return end
  self:ShowMenu(self:ModeEntries(), "mode")
end
function Z:FillMenu()
  if self.menuKind == "zone" then self:ShowMenu(self:ZoneEntries(), "zone")
  elseif self.menuKind == "mode" then self:ShowMenu(self:ModeEntries(), "mode") end
end
function Z:ShowZoneMenu()
  if menu and menu:IsShown() and self.menuKind == "zone" then menu:Hide() return end
  self:CountZones()
  self:ShowMenu(self:ZoneEntries(), "zone")
end
function Z:StopEntries(s)
  if type(s) ~= "table" then return { { text = "Arrêt", head = true } } end
  local P = T.Planner
  local label = (P and P.StepLabel and P:StepLabel(s)) or "Arrêt"
  local q = tonumber(s and s.q)
  local out = { { text = cut(label, 40), head = true } }
  if q then
    local title = (P and P.log and P.log[q] and P.log[q].t) or label
    out[#out + 1] = { text = "Ignorer cette quête", fn = function() Z:Ignore(q, title) end }
  end
  if s and s.map and s.x and P and P.Navigate then out[#out + 1] = { text = "Guider vers cet arrêt", fn = function() P:Navigate(s) end } end
  out[#out + 1] = { text = "Recalculer le trajet", fn = function() Z:Recalc() end }
  return out
end
function Z:StopMenu(s) return self:ShowMenu(self:StopEntries(s), "stop") end

-- lignes ajoutées à la vue « Trajet » (Route:PlanLines) : zone (menu) + détour proposé + quêtes ignorées
-- (ZONEFIX) « Autres zones : 10 quêtes (Les Tarides, Mille pointes…) » : les quêtes du journal laissées hors du trajet
-- par la zone, nommées ; infobulle = le détail par zone ; clic = choisir une zone. nil quand il n'y en a pas.
function Z:OthersLine()
  local o = self.others
  if not (o and o.n > 0) then return nil end
  local names = {}
  for i, e in ipairs(o.zones) do
    if i > 2 then names[#names] = names[#names] .. "…" break end
    names[#names + 1] = e.name
  end
  local tip = {}
  for i, e in ipairs(o.zones) do
    if i > 6 then tip[#tip + 1] = fmt("… et %d autre%s zone%s", #o.zones - 6, #o.zones - 6 > 1 and "s" or "", #o.zones - 6 > 1 and "s" or "") break end
    local t = {}
    for k, ti in ipairs(e.titles) do
      if k > 3 then t[#t + 1] = "…" break end
      t[#t + 1] = "« " .. cut(ti, 26) .. " »"
    end
    tip[#tip + 1] = fmt("%s : %d (%s)", e.name, e.n, tconcat(t, ", "))
  end
  return { text = fmt("Autres zones : %d quête%s (%s)", o.n, o.n > 1 and "s" or "", tconcat(names, ", ")), prio = 1,
    id = "zone:others", onClick = function() Z:OpenPicker() end,
    tooltip = tconcat(tip, "\n") .. "\nHors du trajet : seulement ta zone de travail.\nClic : choisir une autre zone (ou plusieurs)" }
end
function Z:HeadLines()
  if not self:IsEnabled() then return {} end
  local A = self._lastAllowed or self:Allowed()
  local out = {}
  local P = T.Planner
  local file = P and P.src == "file"
  -- (QUESTS 4.1) « Que faire » : les quatre modes du cerveau des quêtes, un clic (menu) — « Rendre mes quêtes » est ici
  local QBm = T.QuestBrain
  local bm = Z.BrainMode()
  if QBm and QBm.Modes and QBm.IsEnabled and QBm:IsEnabled() then
    local tip = {}
    for _, e in ipairs(QBm.MODES or {}) do tip[#tip + 1] = (e.id == bm and "● " or "○ ") .. e.label .. " : " .. e.tip end
    out[#out + 1] = { text = "Que faire : " .. tostring(QBm.Label(bm)) .. " ▾", prio = 1, id = "qb:mode",
      onClick = function() Z:ShowModeMenu() end, onRightClick = function() Z:ShowModeMenu() end,
      tooltip = tconcat(tip, "\n") .. "\nClic : choisir (/tai rendre, /tai prendre, /tai cerveau zone | groupe)" }
  end
  -- (ZONEFIX) le mode du trajet, choisi d'un clic (infobulle : les cinq modes) — l'ordre des arrêts dans la zone
  if bm == "zone" or bm == "group" then
    out[#out + 1] = { text = "Mode : " .. self:StyleName(self:StyleIndex()), prio = 1, id = "zone:mode", onClick = function() Z:CycleStyle() end,
      tooltip = self:StyleTip() .. "\nClic : mode suivant (gardé pour ce personnage)" }
  end
  -- (QA-QUEST) voisines prises en renfort : lesquelles, en clair
  local nb = {}
  for mm in pairs(self.fill or {}) do nb[#nb + 1] = Z.MapName(mm) end
  tsort(nb)
  local label = (A.mode == "auto" and self.label) or A.label or "?"
  out[#out + 1] = { text = "Zone : " .. cut(label, 40) .. " ▾", prio = 1, id = "zone:pick",
    onClick = function() Z:OpenPicker() end, onRightClick = function() Z:ShowZoneMenu() end,
    tooltip = "Clic : choisir ma zone (classées pour toi)\nClic droit : plusieurs zones, toute la région"
      .. (#nb > 0 and ("\nTa zone n'a presque plus de quêtes : " .. tconcat(nb, ", ") .. " en renfort") or "")
      .. (self:MaskedText() and ("\n" .. self:MaskedText()) or "")
      .. (file and "\nPlan de TreeAI Coach : mêmes règles que le trajet en jeu" or "") }
  if A.cross then
    out[#out + 1] = { text = "Changement de continent compris (tu l'as choisi)", prio = 1 }
  end
  if not file then
    local ol = self:OthersLine()
    if ol then out[#out + 1] = ol end
  end
  local lf = self.logFull
  if file and P.logFullSkipped then          -- plan de l'appli : « Prendre » masqués faute de place
    local n, w = 0, 0
    for _ in pairs(P.log or {}) do n = n + 1 end
    for _ in pairs(P.logFullSkipped) do w = w + 1 end
    lf = { n = n, max = tonumber((try(C("C_QuestLog", "GetMaxNumQuestsCanAccept")))) or 20, waiting = w }
  elseif file then lf = nil end
  if lf then
    -- (QA-QUEST F15) « plein » seulement quand il l'est ; sinon : les prises du plan ne tiendront pas toutes
    local w = lf.waiting
    out[#out + 1] = { text = (lf.n >= lf.max)
        and fmt("Journal plein (%d/%d) : %d quête%s à prendre ici attend%s une place", lf.n, lf.max, w, w > 1 and "s" or "",
          w > 1 and "ent" or "")
        or fmt("Journal %d/%d : %d quête%s à prendre attendr%s une place", lf.n, lf.max, w, w > 1 and "s" or "",
          w > 1 and "ont" or "a"),
      prio = 2, id = "zone:logfull",
      tooltip = "Les quêtes à prendre restent hors du trajet tant que le journal est plein.\nUne remise ou un abandon libère une place." }
  end
  local d = self.detour
  if d then
    out[#out + 1] = { text = fmt("Détour rentable : rendre « %s » (%s) · +%s XP pour %d min", cut(d.title, 24), cut(d.zone, 20),
      thousands(d.xp), max(1, floor(d.sec / 60 + 0.5))), prio = 2, id = "zone:detour",
      onClick = function() Z:AddDetour() end,
      tooltip = (d.cross and "Autre continent (bateau / zeppelin compris)\n" or "Hors de ta zone de travail\n") .. "Clic : l'ajouter au trajet" }
  end
  return out
end

-- ===================================================================== page d'options (moteur TIDY)
function Z:BuildOptions(P)
  P.header("Mode du trajet")
  P.text("|cff999999Un clic aussi sur « Mode : … » (page Trajet) ou sur l'en-tête de la carte. Gardé pour ce personnage.|r")
  for i, e in ipairs(Z.STYLES) do
    P.check(e.name .. " — " .. e.tip, function() return Z:StyleIndex() == i end, function() Z:SetStyle(i) end)
  end
  P.header("Zone de travail")
  P.text("|cff999999Auto suit ta zone (ses voisines seulement quand elle est vide). Choisis une ou plusieurs zones pour y rester ; un autre continent n'est pris que si tu le coches.|r")
  P.buttons({
    { "Auto (zone + voisines)", function() Z:SetMode("auto") end },
    { "Toute la région", function() Z:SetMode("continent") end },
  }, 2)
  self:CountZones()
  local shown = 0
  for _, e in ipairs(self:ZoneChoices()) do
    if shown >= 14 then break end
    P.check(e.n and fmt("%s · %d quête%s à ton niveau", e.name, e.n, e.n > 1 and "s" or "") or e.name,
      function() return Z.char and Z.char.mode == "zones" and Z.char.zones[e.map] and true or false end,
      function() Z:SetMode("zone", e.map) end)
    shown = shown + 1
  end
  P.header("Quêtes ignorées")
  P.text("|cff999999Clic droit sur un arrêt du trajet : « Ignorer cette quête ». Décoche pour la reprendre.|r")
  local ids = {}
  for q in pairs(self.char and self.char.ignore or {}) do ids[#ids + 1] = q end
  tsort(ids, function(a, b) return tostring(a) < tostring(b) end)
  for _, q in ipairs(ids) do
    P.check(tostring(self.char.ignore[q]), function() return Z.char.ignore[q] ~= nil end,
      function(v) if v then Z.char.ignore[q] = Z.char.ignore[q] or ("quête " .. q) else Z:Unignore(q) end end)
  end
  P.header("Actions")
  P.buttons({
    { "Recalculer", function() Z:Recalc() end },
    { "Pause / Reprendre", function() Z:TogglePause() end, function() return Z:Paused() and "Reprendre" or "Pause" end },
  }, 2)
end

-- ===================================================================== cycle de vie
-- (ZONEFIX 4.0.1) une fois : le mode sûr de 3.9.9 / 4.0.0 avait coupé ce module (liste « bêta ») ; il fait partie du
-- guidage de base, on le rallume pour qui est resté en mode sûr (un « coupé » choisi plus tard reste coupé)
if T.On then
  T:On("READY", "ZonePlan:safe401", function()
    local db = Z.db
    if type(db) ~= "table" or db.safeV then return end
    db.safeV = 1
    -- un choix du joueur noté par le cœur (db.modChoice, « Fonctions » / options / pastille) n'est jamais défait
    local mc = type(T.db) == "table" and T.db.modChoice
    local refused = type(mc) == "table" and mc.ZonePlan == false
    if db.enabled == false and not refused and T.ModuleProfile and T:ModuleProfile() == "sur" and T.SetModuleEnabled then
      T:SetModuleEnabled("ZonePlan", true)
    end
  end)
end
function Z:OnEnable()
  -- (ZONEFIX) un seul gestionnaire par évènement et par module : le suivi auto (ZoneWatch.lua) passe par ces deux-là
  local function zone()
    Z._lastAllowed = nil
    if (Z.char and Z.char.mode or "auto") == "auto" then Z:Bump() end
    if Z.WatchSoon then Z:WatchSoon(0.5) end
  end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", zone)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid)
    qid = tonumber(_ns(qid))
    if qid and Z.char and Z.char.extra[qid] then Z.char.extra[qid] = nil end
    if Z.WatchGameDid then Z.WatchGameDid(qid) end
  end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() Z.counts = nil end)
  self:On("EXPORT_UPDATED", function() Z._lastAllowed = nil end)   -- nouvelles zones connues → voisines revues
end
function Z:OnDisable()
  if menu then menu:Hide() end
  local P = T.Planner
  if P and P.Dirty then P:Dirty(true, "trajet par zone coupé") end
end

-- (ZONEPICK) « Choisir ma zone » : la fenêtre (page Quêtes), sinon le petit menu
function Z:OpenPicker()
  local Wn = T.Window
  if Wn and type(Wn.OpenZonePicker) == "function" and Wn.IsEnabled and Wn:IsEnabled() then
    local ok = pcall(Wn.OpenZonePicker, true)
    if ok then return true end
  end
  return self:ShowZoneMenu()
end
T:RegisterSlash("zone", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local QBm = T.QuestBrain
  if rest == "auto" then
    if QBm and QBm.ZoneAuto then QBm.ZoneAuto() else Z:SetMode("auto") end
  elseif rest == "region" or rest == "région" or rest == "continent" then Z:SetMode("continent")
  elseif rest == "recalc" then Z:Recalc()
  elseif rest == "menu" then Z:ShowZoneMenu()
  elseif rest == "conseil" or rest == "conseille" or rest == "conseille-moi" then
    local Wn = T.Window
    if Wn and Wn.ShowAdvice then Wn.ShowAdvice(true) else Z:OpenPicker() end
  else Z:OpenPicker() end
end, "zone : choisir ma zone (zone conseil · auto · région · recalc · menu)")
