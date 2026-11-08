-- TreeAI — Zone de travail + trajet préparé (agent ZONEPLAN). Toujours un chemin prêt, dans la zone que TU choisis.
--  * « Zone de travail » : Auto (ta zone + ses voisines), une liste de zones (triées par nombre de quêtes à ton
--    niveau) ou « Toute la région » (le continent). Jamais d'autre continent sans ton choix ; un détour hors zone
--    très rentable (rendre une quête finie, ex.) est seulement PROPOSÉ, en clair, et ajouté sur un clic.
--  * Plan en jeu, sans l'appli : arrêts (prendre / objectif / rendre ; vols, bateaux, foyer comptés par Route)
--    construits sur le journal + QuestieDB (sinon : journal seul, aux points de quête du client), objectifs
--    voisins regroupés en un seul arrêt (même coin de carte), ordre glouton (valeur / temps) puis 2-opt + Or-opt
--    avec les précédences (prendre → objectifs → rendre). Recalculé tout de suite à chaque quête prise / rendue /
--    abandonnée, objectif fini, changement de zone ou de réglage ; HYSTÉRÉSIS : l'ordre en cours reste tant que
--    le nouveau ne fait pas gagner ≥ 10 % du trajet (et ≥ 1 min) — pas de valse des arrêts pour trois mètres.
--  * Travail découpé : tourne dans la tâche du Planner (≤ 2 ms par tranche, via l'ordonnanceur du cœur), hors combat.
--  * Personnalisable (page d'options « Trajet ») : élites, quêtes de groupe, donjons, écart de niveau max,
--    distance max d'un arrêt, priorité (XP/h, finir la zone, récompenses), liste « Ignorer », Pause / Recalculer.
--  * Plan de l'appli (TreeAI_Plan) : mêmes règles (zone + quêtes ignorées) appliquées à l'affichage.
-- Aucune fonction protégée, aucun cadre Blizzard touché (menu = cadre à nous), rien en combat. Lua 5.1, aucune globale.
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local Z = T:RegisterModule("ZonePlan", {
  title = "Trajet",
  description = "Tu choisis la zone, TreeAI prépare le meilleur ordre des arrêts. Clic droit sur un arrêt : l'ignorer.",
  defaults = { enabled = true, elites = false, groupQ = true, dungeons = false, gap = 3, maxMin = 0, prio = 1,
    detour = true, compass = true },
  charDefaults = { mode = "auto", zones = {}, ignore = {}, extra = {} },
  options = {
    { section = "Quêtes du trajet", key = "elites", type = "toggle", label = "Quêtes élites (toujours prises à 3 ou plus)" },
    { key = "groupQ", type = "toggle", label = "Quêtes de groupe" },
    { key = "dungeons", type = "toggle", label = "Quêtes de donjon" },
    { key = "gap", type = "range", label = "Niveau de quête max au-dessus du mien", min = 1, max = 6, step = 1 },
    { section = "Ordre des arrêts", key = "prio", type = "choice", label = "Priorité",
      values = { "XP par heure", "Finir la zone", "Récompenses" } },
    { key = "maxMin", type = "range", label = "Arrêt le plus loin (minutes de trajet, 0 = sans limite)", min = 0, max = 30, step = 1 },
    { key = "detour", type = "toggle", label = "Proposer un détour hors zone quand il rapporte gros" },
    { key = "compass", type = "toggle", label = "La boussole vise l'arrêt en cours" },
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
-- node = { steps, map, x, y, svc, value, qs = { [q] = rank }, key, area }
-- dist(a, b) → yards (nil = far / unknown)
function N.Nodes(requests, dist, area)
  area = area or N.AREA
  local nodes, clusters = {}, {}
  local objs = {}
  for _, r in ipairs(requests or {}) do
    for _, s in ipairs(r.steps or {}) do
      if s.k == "obj" and s.x then objs[#objs + 1] = s
      else
        nodes[#nodes + 1] = { steps = { s }, map = s.map, x = s.x, y = s.y, svc = s.svc or 0, value = s.value or 0,
          qs = { [s.q or -1] = RANK[s.k] or 2 }, key = skey(s) }
      end
    end
  end
  tsort(objs, function(a, b) if a.q ~= b.q then return (a.q or 0) < (b.q or 0) end return (tonumber(a.o) or 0) < (tonumber(b.o) or 0) end)
  for _, s in ipairs(objs) do
    local home
    for _, c in ipairs(clusters) do
      if c.map == s.map or dist then
        local d = dist and dist(c, s) or nil
        if d and d <= area then home = c break end
      end
    end
    if home then
      home.steps[#home.steps + 1] = s
      home.svc, home.value = home.svc + (s.svc or 0), home.value + (s.value or 0)
      home.qs[s.q or -1] = 2
      home.key = home.key .. "+" .. skey(s)
    else
      home = { steps = { s }, map = s.map, x = s.x, y = s.y, svc = s.svc or 0, value = s.value or 0,
        qs = { [s.q or -1] = 2 }, key = skey(s) }
      clusters[#clusters + 1] = home
    end
  end
  for i, c in ipairs(clusters) do
    if #c.steps > 1 then c.area = "a" .. i end
    nodes[#nodes + 1] = c
  end
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
-- logCount, yield, first = legFirst(start, node) }
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
        if ok and nd.steps[1].k == "pick" and logCount >= cap then ok = false end
        if ok then
          local t = ((#ord == 0 and o.first) and o.first(nd) or leg(pos, nd)) + max(10, nd.svc or 0)
          local score = (nd.value or 0) / max(10, t)
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
    local k = nd.steps[1].k
    if k == "pick" then logCount = logCount + 1 elseif k == "turn" then logCount = logCount - 1 end
    if nd.x then pos = nd end
  end
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
-- de chemin gagnées) ; précédences gardées, ordre relatif inchangé sinon
N.NEAR = 60
-- the quest log never overflows along the order (pick +1, turn-in −1)
function N.CapOK(nodes, ord, cap, logCount)
  if not cap then return true end
  local c = logCount or 0
  for _, i in ipairs(ord) do
    local k = nodes[i].steps[1].k
    if k == "pick" then
      if c >= cap then return false end
      c = c + 1
    elseif k == "turn" then c = c - 1 end
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
    A.mode = "auto"
    A.maps = { [hz] = true }
    local nb = self:Neighbours(hz)
    for _, m in ipairs(nb) do A.maps[m] = true end
    A.label = Z.MapName(hz) .. (#nb > 0 and fmt(" + %d voisine%s", #nb, #nb > 1 and "s" or "") or "")
  end
  A._key, A._at = ckey, tnow
  self._lastAllowed = A
  return A
end
function Z:InZone(map, A)
  A = A or self:Allowed()
  if A.any then return true end
  local m = tonumber(map)
  if not m then return true end
  if A.maps then return (A.maps[Z.ZoneOf(m)] or A.maps[m]) and true or false end
  local c = Z.Cont(m)
  return c ~= nil and c == A.cont
end
-- signature for the Planner: replan when the work area / a setting changes
function Z:Sig()
  if not self:IsEnabled() then return "" end
  local ch = self.char or {}
  local here = (ch.mode or "auto") == "auto" and Geo.Player and Geo.Player() or nil
  return fmt("%s:%s:%d", tostring(ch.mode), tostring(here and Z.ZoneOf(here) or ""), self.rev or 0)
end
function Z:Bump()
  self.rev = (self.rev or 0) + 1
  local P = T.Planner
  if P and P.Dirty then P:Dirty(true) end
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
  local tag = try(C("C_QuestLog", "GetQuestTagInfo"), q)
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
-- → elite, group, dungeon, extra = { pvp, raid, boss, failed }
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
  return elite and true or false, grp, dg and true or false,
    { pvp = pvp and true or false, raid = raid and true or false, boss = boss, failed = failed and true or false }
end
-- keep this quest? → ok, why ("ignorée" | "échouée" | "JcJ" | "raid" | "donjon" | "élite" | "groupe" | "niveau")
function Z:Accept(q, rec, lq, level, group)
  if self.char and self.char.ignore[tonumber(q) or q] then return false, "ignorée" end
  local elite, grp, dg, ex = Z.Kind(q, rec, lq)
  if ex.failed then return false, "échouée" end
  if lq and lq.done == 1 then return true end
  if ex.pvp then return false, "JcJ" end
  if ex.raid then return false, "raid" end
  if dg and not (opt("dungeons") and group >= 3) then return false, "donjon" end
  if elite and not (opt("elites") or group >= 3) then return false, "élite" end
  if grp and not (opt("groupQ") ~= false or group >= 2) then return false, "groupe" end
  local lv = tonumber(rec and rec.lv) or tonumber(lq and lq.lv) or level
  if lv > 0 and lv - level > (tonumber(opt("gap")) or 3) then return false, "niveau" end
  return true
end

-- ===================================================================== plan en jeu
local function rewardBoost(q)
  local n = tonumber(_ns((try(G("GetNumQuestLogChoices"), q)))) or 0
  local r = tonumber(_ns((try(G("GetNumQuestLogRewards"), q)))) or 0
  return (n + r) > 0 and 2 or 1.2
end
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
  local A = self:Allowed()
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
  local prio = tonumber(opt("prio")) or 1
  local maxS = (tonumber(opt("maxMin")) or 0) * 60
  local grey = E.GreyLevel(level)
  local isDone = Xm and Xm.IsQuestDone
  local requests, far = {}, {}
  local info, levels = { nonGrey = 0 }, {}
  local skipped = { ["ignorée"] = 0, zone = 0, loin = 0 }
  local masked = {}
  local Qm = T.modules and T.modules.Quests
  local timers = (Qm and Qm.Timers and Qm.lastList and select(2, pcall(Qm.Timers, Qm.lastList))) or {}
  if type(timers) ~= "table" then timers = {} end
  local function consider(q, rec, lq)
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
    if #steps == 0 then return end
    -- (QUESTQA) zone de travail ÉTAPE PAR ÉTAPE : les objectifs d'ici sont gardés même quand la remise est dans
    -- une capitale (« La flamme sacrée » : la fiole se remplit en Orneval, on la rend à Thunder Bluff) ;
    -- « prendre » hors zone = toute la quête ; un objectif hors zone retire aussi la remise (pas finissable ici)
    local extra = self.char and self.char.extra[q]
    local inZone = true
    -- (QA-QUEST F10) arrêt sans position (pas de Questie, pas de point du jeu) : jugé sur la zone de sa quête
    -- (export, sinon GetQuestUiMapID du journal) — avant, il passait le filtre et partait aux Tarides
    local qm
    local function outOf(s)
      if s.x then return not self:InZone(s.map, A) end
      if qm == nil then
        qm = tonumber(rec and rec.zone) or (lq and tonumber(_ns((try(G("GetQuestUiMapID"), q))))) or false
        if qm and qm <= 0 then qm = false end
      end
      return qm and not self:InZone(qm, A) or false
    end
    if extra then          -- détour choisi : gardé tel quel, arrêts hors zone marqués « hors zone »
      for _, s in ipairs(steps) do if s.x and not self:InZone(s.map, A) then inZone = false end end
    else
      local keep, pickOut, objOut, objIn, turnOut = {}, false, false, false, false
      for _, s in ipairs(steps) do
        local outS = outOf(s)
        if outS then
          if s.k == "pick" then pickOut = true elseif s.k == "obj" then objOut = true else turnOut = true end
        else
          if s.k == "obj" then objIn = true end
          keep[#keep + 1] = s
        end
      end
      if pickOut or (objOut and not objIn) or (turnOut and not objIn and not objOut) then
        inZone = false
      elseif objOut or turnOut then
        local k2 = {}
        for _, s in ipairs(keep) do if s.k ~= "turn" or not objOut then k2[#k2 + 1] = s end end
        steps = k2
      end
    end
    if not inZone and not extra then
      skipped.zone = skipped.zone + 1
      if lq then
        far[#far + 1] = { q = q, rec = rec, lq = lq, steps = P:QuestSteps(q, rec, lq, ctx) }
        masked.zone = masked.zone or {}
        masked.zone[#masked.zone + 1] = q
      end
      return
    end
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
    if lv > grey and (not rec or not rec.zone or self:InZone(rec.zone, A)) then
      info.nonGrey = info.nonGrey + 1
      levels[#levels + 1] = lv
      if lq then info.logHere = (info.logHere or 0) + 1 end
    end
    for _, s in ipairs(steps) do
      -- s.value = score de l'ordre (jamais affiché) ; s.xp = XP réelle de l'arrêt (XP/h, détour : QA-QUEST F6)
      if prio == 2 then s.value = 1000                           -- finir la zone : chaque arrêt compte autant
      elseif prio == 3 and s.k == "turn" then s.value = (s.value or 0) * rewardBoost(q) end
      if timers[q] then s.value = (s.value or 0) * 5 + 5000 end   -- (QUESTQA) chrono en cours : d'abord
      if extra and not inZone then s.out = true end
    end
    requests[#requests + 1] = { q = q, steps = steps, inLog = lq ~= nil }
  end
  for q, lq in pairs(log) do consider(q, recs[q], lq) end
  for q, rec in pairs(recs) do
    if not log[q] and rec.st == "avail" and not (isDone and isDone(q)) and not (P.char and P.char.turned[q])
        and (rec.rl or 0) <= level and not rec.pre and not rec.preAny then
      consider(q, rec, nil)
    end
  end
  local Rt = T.Route                                  -- trop dures / trop loin → proposition (Route.lua)
  if Rt and Rt.FilterRequests and Rt:IsEnabled() then
    local ok, res = pcall(Rt.FilterRequests, Rt, requests, recs, log, ctx)
    if ok and type(res) == "table" then requests = res end
  end
  tsort(requests, function(a, b) return a.q < b.q end)
  tsort(levels)
  if #levels > 0 then info.zoneMedian = levels[floor((#levels + 1) / 2)] end
  -- nodes → greedy → 2-opt / Or-opt → hysteresis
  local nodes = N.Nodes(requests, worldDist)
  local cap = tonumber(try(C("C_QuestLog", "GetMaxNumQuestsCanAccept"))) or 20
  local logCount = 0
  for _ in pairs(log) do logCount = logCount + 1 end
  local first = makeFirst(leg, start)
  -- (QA-QUEST F1) la pierre de foyer seulement quand RIEN n'est à moins de 5 min d'ici (sinon : foyer → Orgrimmar →
  -- vol → petite quête → vol retour, alors que la zone de quêtes est à 4 min à pied) ; Route l'affiche pareil
  self.noHearth = nil
  if first and start then
    for _, nd in ipairs(nodes) do
      if nd.x and leg(start, nd) <= 300 then first, self.noHearth = nil, true break end
    end
  end
  local legN = function(a, b) return leg(a, b) end
  local st0 = start or { }
  local ord = N.Greedy(nodes, st0, legN, { horizon = (prio == 2) and 60 or 40, cap = cap - 2, logCount = logCount,
    yield = yieldFn, first = first })
  local D = N.Matrix(nodes, ord, st0, legN, first, yieldFn)
  local greedy = {}
  for i, v in ipairs(ord) do greedy[i] = v end
  N.Improve(nodes, ord, D, yieldFn, 3)
  -- (QUESTQA) 2-opt / Or-opt ne regardent que le chemin : jamais un « Prendre » qui ferait déborder le journal
  if not N.CapOK(nodes, ord, cap - 2, logCount) then ord = greedy end
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
  ord = N.NearFirst(nodes, ord, start, worldDist, N.NEAR, cap - 2, logCount)
  ord = N.PositionlessLast(nodes, ord, cap - 2, logCount)   -- (QA-QUEST F10) sans lieu connu : au bout
  -- (QA-QUEST F2) chrono en cours : ses arrêts d'abord (le bonus du glouton ne survit pas au 2-opt / Or-opt, à
  -- l'hystérésis ni à NearFirst, qui ne voient que le chemin) ; précédences et journal gardés
  ord = N.TimersFirst(nodes, ord, timers, cap - 2, logCount)
  -- (QUESTQA) journal plein : les quêtes à prendre attendent (dit une fois, en clair, dans la vue du trajet)
  local picks = 0
  for _, r in ipairs(requests) do if r.steps[1] and r.steps[1].k == "pick" then picks = picks + 1 end end
  self.logFull = (logCount >= cap and picks > 0) and { n = logCount, max = cap, waiting = picks } or nil
  -- flatten
  local steps, keys, t, xp, prev = {}, {}, 0, 0, 0
  for _, i in ipairs(ord) do
    local nd = nodes[i]
    t = t + ((D[prev] and D[prev][i]) or 0)
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
  end
  self.lastKeys, self.kept = keys, kept
  self.skipped, self.masked = skipped, masked
  -- detour proposal: a finished quest / objectives out of the zone worth far more than the route rate
  self.detour = nil
  local minutes = max(1, floor(t / 60 + 0.5))
  local xph = (t > 0 and prio ~= 2) and floor(xp * 3600 / t) or 0
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
      if f.lq.done == 1 then
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
  local route = { id = "auto", title = "Trajet TreeAI", zone = zoneLabel, steps = steps, minutes = minutes, xph = xph,
    cross = A.cross }
  local v, why = E.RouteVerdict(route, ctx, info)
  route.verdict, route.reasons = v, why and { why } or {}
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
  local ch = self.char or {}
  local recs = recsOf(X() and X().Data and X():Data())
  log = log or (T.Planner and T.Planner.log) or {}
  local level, group = myLevel(), groupSize()
  local out, why = {}, {}                  -- par quête : refusée par les règles (out[q] = raison) ou false
  for _, s in ipairs(steps) do
    local q = tonumber(s.q)
    s._hide = nil
    if q and not s._done then
      if out[q] == nil then
        local ok, w = self:Accept(q, recs[q], log[q], level, group)
        out[q] = (not ok) and w or false
        if not ok then why[q] = w end
      end
      local hide = out[q] or nil
      if not hide and not ch.extra[q] then
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
local WHY_TXT = { donjon = "de donjon", ["élite"] = "élite", JcJ = "JcJ", raid = "de raid", ["échouée"] = "échouée",
  niveau = "trop haute", groupe = "de groupe", zone = "hors zone", ["ignorée"] = "ignorée" }
function Z:MaskedText()
  local parts = {}
  local P = T.Planner
  local src = (P and P.src == "file") and self.maskedPlan or self.masked
  for _, w in ipairs({ "donjon", "élite", "JcJ", "raid", "échouée", "niveau", "groupe", "zone", "ignorée" }) do
    local l = src and src[w]
    if l and #l > 0 then parts[#parts + 1] = fmt("%d %s", #l, WHY_TXT[w]) end
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
function Z:ExportZones()
  local A = self:IsEnabled() and self:Allowed() or nil
  return A and A.maps or nil
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

-- (QA-QUEST F4) 1er trajet du trajet = un transport (foyer, vol, bateau, zeppelin) : la boussole vise CE départ
-- (maître de vol / quai à pied, ou « Pierre de foyer vers … » sans flèche), jamais la quête lointaine en ligne
-- droite avec une durée à pied (« 8 km · 24 min » alors que la carte dit foyer + 2 min de vol)
local function legTarget(me)
  local Rt = T.Route
  if not (Rt and Rt.IsEnabled and Rt:IsEnabled() and Rt.Stops) then return nil end
  local ok, st = pcall(Rt.Stops, Rt, 1)
  local f = ok and type(st) == "table" and st[1] or nil
  if not f or f.step then return nil end
  local Safe = T.SafeText or function(s) return s end
  if f.kind == "hs" then
    local name = cut((f.place and f.place.name) or "l'auberge", 28)
    local label = "Pierre de foyer vers " .. name
    return { key = "plan:hs:" .. name, kind = "zone", plan = true, leg = "hs", title = label, label = label,
      note = "Lance ta pierre de foyer", src = "trajet" }
  end
  local p = f.from or f.place
  if not (p and p.map and p.x and p.y) then return nil end
  local label = Safe(tostring(f.text or "Trajet"))
  local t = { key = fmt("plan:%s:%s:%d:%d", tostring(f.kind), tostring(p.map), floor(p.x * 10), floor(p.y * 10)),
    kind = "travel", plan = true, leg = f.kind, title = label, label = label, mapID = p.map, x = p.x / 100, y = p.y / 100,
    src = "trajet" }
  if me and me.mapID and me.x and Geo.Dist then
    local mx, my = me.x, me.y
    if mx <= 1 and my <= 1 then mx, my = mx * 100, my * 100 end
    t.dist = Geo.Dist(me.mapID, mx, my, p.map, p.x, p.y)
  end
  return t
end
Z.LegTarget = legTarget

-- boussole : l'arrêt en cours (Compass.BuildTargets)
function Z.CompassTarget(me)
  if not (Z:IsEnabled() and opt("compass") ~= false) or Z:Paused() then return nil end
  local P = T.Planner
  local s = P and P.steps and P.cursor and P.steps[P.cursor]
  if not (s and not s._done and not s._other) then return nil end
  local lt = legTarget(me)
  if lt then return lt end
  if not (s.map and s.x) then return nil end
  local label = P.StepLabel and P:StepLabel(s) or "Arrêt"
  local t = { key = "plan:" .. skey(s), kind = s.k == "turn" and "turnin" or "objective", plan = true, questID = s.q,
    title = label, label = label, mapID = s.map, x = s.x / 100, y = s.y / 100, src = "trajet" }
  if me and me.mapID and me.x and Geo.Dist then
    local mx, my = me.x, me.y
    if mx <= 1 and my <= 1 then mx, my = mx * 100, my * 100 end
    t.dist = Geo.Dist(me.mapID, mx, my, s.map, s.x, s.y)
  end
  return t
end
function Z:OnStep()
  if inCombat() then return end
  local Cm = T.Compass
  local P = T.Planner
  -- un repère posé par un clic sur l'arrêt précédent ne doit pas retenir la boussole
  if P and P.navBy == "compass" and Cm and Cm.char and Cm.char.custom and type(Cm.ClearWaypoint) == "function" then
    P.navBy = nil
    pcall(Cm.ClearWaypoint)
    return
  end
  if Cm and type(Cm.Rebuild) == "function" and opt("compass") ~= false then pcall(Cm.Rebuild, "plan") end
end

-- ===================================================================== quêtes par zone (QuestieDB, découpé)
-- counts.n[map] = quêtes disponibles à mon niveau (± écart), calculé une fois par niveau, hors combat
function Z:CountZones()
  local lvl = myLevel()
  if self.counts and self.counts.lvl == lvl then return self.counts end
  if self._counting or inCombat() then return self.counts end
  local db = X() and X().Source and try(X().Source, X())
  local n = {}
  if not db then
    local exp = X() and X().Data and X():Data()
    for _, r in ipairs(exp and exp.quests or {}) do
      local m = tonumber(r.zone)
      if m and (r.lv or 0) >= lvl - 4 and (r.lv or 0) <= lvl + 3 then n[m] = (n[m] or 0) + 1 end
    end
    self.counts = { lvl = lvl, n = n }
    return self.counts
  end
  self._counting = true
  local ids = try(db.ids) or {}
  local i, gap = 1, tonumber(opt("gap")) or 3
  local isDone = X().IsQuestDone
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
        if rl <= lvl and ql >= lvl - 4 and ql <= lvl + gap and not (isDone and isDone(id)) then
          local m = db.map(zone)
          if m then n[m] = (n[m] or 0) + 1 end
        end
      end
      if k % 40 == 0 then
        local t1 = try(G("debugprofilestop"))
        if not (t0 and t1) or t1 - t0 > 2 then return end
      end
    end
    Z.counts, Z._counting = { lvl = lvl, n = n }, false
    if ticker then ticker:Cancel() end
    if Z.menuOpen then Z:FillMenu() end
  end
  ticker = T.NewTicker and T:NewTicker(0.05, step, self)
  if not ticker then while self._counting do step() end end
  return self.counts
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
  local out = { { text = "Zone de travail", head = true } }
  out[#out + 1] = { text = (mode == "auto" and "● " or "○ ") .. "Auto : ma zone + ses voisines", fn = function() Z:SetMode("auto") end, keep = true }
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
function Z:FillMenu()
  if self.menuKind == "zone" then self:ShowMenu(self:ZoneEntries(), "zone") end
end
function Z:ShowZoneMenu()
  if menu and menu:IsShown() and self.menuKind == "zone" then menu:Hide() return end
  self:CountZones()
  self:ShowMenu(self:ZoneEntries(), "zone")
end
function Z:StopEntries(s)
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
function Z:HeadLines()
  if not self:IsEnabled() then return {} end
  local A = self._lastAllowed or self:Allowed()
  local out = {}
  local P = T.Planner
  local file = P and P.src == "file"
  -- (QA-QUEST) « + 1 voisine » : laquelle, en clair dans l'infobulle
  local nb = {}
  if A.mode == "auto" and A.maps then
    for mm in pairs(A.maps) do if mm ~= A.here then nb[#nb + 1] = Z.MapName(mm) end end
    tsort(nb)
  end
  out[#out + 1] = { text = "Zone : " .. cut(A.label or "?", 40) .. " ▾", prio = 1, id = "zone:pick",
    onClick = function() Z:ShowZoneMenu() end, onRightClick = function() Z:ShowZoneMenu() end,
    tooltip = "Clic : choisir la zone de travail\n(auto, une ou plusieurs zones, toute la région)"
      .. (#nb > 0 and ("\nVoisine" .. (#nb > 1 and "s" or "") .. " : " .. tconcat(nb, ", ")) or "")
      .. (self:MaskedText() and ("\n" .. self:MaskedText()) or "")
      .. (file and "\nPlan de TreeAI Coach : mêmes règles que le trajet en jeu" or "") }
  if A.cross then
    out[#out + 1] = { text = "Changement de continent compris (tu l'as choisi)", prio = 1 }
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
  P.header("Zone de travail")
  P.text("|cff999999Auto suit ta zone et ses voisines. Choisis une ou plusieurs zones pour y rester ; un autre continent n'est pris que si tu le coches.|r")
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
function Z:OnEnable()
  local function zone() Z._lastAllowed = nil; if (Z.char and Z.char.mode or "auto") == "auto" then Z:Bump() end end
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", zone)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid)
    qid = tonumber(qid)
    if qid and Z.char and Z.char.extra[qid] then Z.char.extra[qid] = nil end
  end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() Z.counts = nil end)
  self:On("EXPORT_UPDATED", function() Z._lastAllowed = nil end)   -- nouvelles zones connues → voisines revues
end
function Z:OnDisable()
  if menu then menu:Hide() end
  local P = T.Planner
  if P and P.Dirty then P:Dirty(true) end
end

T:RegisterSlash("zone", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if rest == "auto" then Z:SetMode("auto")
  elseif rest == "region" or rest == "région" or rest == "continent" then Z:SetMode("continent")
  elseif rest == "recalc" then Z:Recalc()
  else Z:ShowZoneMenu() end
end, "zone : zone de travail du trajet (auto · région · recalc)")
