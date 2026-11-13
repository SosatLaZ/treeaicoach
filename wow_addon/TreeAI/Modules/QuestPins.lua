-- TreeAI — module QuestPins (agent REPLACE, 4.0.1) : LES icônes de quêtes, dessinées par TreeAI, pour jouer avec
-- Questie installé mais SILENCIEUX (sa base de données seule) — plus besoin de ses icônes.
--   « ! »  quête à prendre : couleur de niveau du jeu ; faction / classe / prérequis / exclusives / déjà faite filtrés ;
--          un seul « ! » par donneur (plusieurs quêtes = une icône, toutes dans l'infobulle) ;
--   « ? »  quête du journal prête à rendre, chez celui qui la reprend ;
--   zones d'objectifs des quêtes du journal (amas d'apparitions, rayon réel), seulement les objectifs pas finis.
-- Seulement ce qui compte : les arrêts du trajet d'abord (même numéro que l'onglet et la carte), puis les quêtes à
-- rendre, les objectifs, les quêtes à prendre à ton niveau ; plafonds (carte 40, minimap 10), quêtes grises cachées.
-- Infobulle : quête, niveau, donneur, XP (base de Questie), distance + durée à pied, pourquoi ; clic = boussole.
-- Données : l'export du planificateur (T.Export:Data(), construit hors combat depuis QuestieDB, LU à l'exécution,
-- rien de copié) + le journal réel (Export.LogSnapshot) ; sans QuestieDB : les points du jeu (GetNextWaypoint).
-- Questie dessine encore ses icônes → rien ici (pas de doublon ; le module « Confort » propose de le rendre
-- silencieux en un clic). Carte : NOTRE calque (enfant d'UIParent posé sur le canevas, comme le Chemin dessiné),
-- prévenu par un fournisseur de données de la carte (déplacer la carte = 1 point posé, zoomer = les icônes
-- replacées) ; minimap : HereBeDragons-Pins s'il est chargé (Questie l'embarque : la lib place, rien par image ici),
-- sinon la projection de MinimapPins, ≤ 2,5 Hz en marchant, rien à l'arrêt. Carte fermée + joueur immobile = zéro
-- travail (aucun minuteur : la liste n'est refaite que si le journal, l'export, le niveau ou le trajet changent).
-- Taint : nos cadres seulement (aucun cadre de Blizzard modifié, accroché ni ancré), aucune fonction protégée.
-- Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local QP = T:RegisterModule("QuestPins", {
  title = "Icônes de quêtes",
  description = "Les « ! » à prendre, les « ? » à rendre et les zones d'objectifs sur la carte et la minimap "
    .. "(base de QuestieDB lue en jeu, Questie peut rester silencieux).",
  defaults = { enabled = true, map = true, minimap = true, avail = true, turnin = true, objectives = true,
               trivial = false, force = false },
  options = {
    { key = "map", type = "toggle", label = "Icônes sur la carte du monde", section = "Où" },
    { key = "minimap", type = "toggle", label = "Icônes sur la minimap", section = "Où" },
    { key = "avail", type = "toggle", label = "« ! » quêtes à prendre (couleur de niveau)", section = "Quoi" },
    { key = "turnin", type = "toggle", label = "« ? » quêtes à rendre", section = "Quoi" },
    { key = "objectives", type = "toggle", label = "Zones d'objectifs de mes quêtes", section = "Quoi" },
    { key = "trivial", type = "toggle", label = "Montrer aussi les quêtes grises (trop faciles)", section = "Quoi" },
    { key = "force", type = "toggle", label = "Dessiner même quand Questie dessine les siennes (doublons)", section = "Questie" },
  },
})
T.QuestPins = QP

do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[1]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local at = #fam.mods + 1
    for i, n in ipairs(fam.mods) do
      if n == "QuestPins" then at = nil break end
      if n == "MinimapPins" or n == "PathDraw" then at = i + 1 end
    end
    if at then table.insert(fam.mods, at, "QuestPins") end
  end
end

local floor, sqrt, max, min, abs = math.floor, math.sqrt, math.max, math.min, math.abs
local tsort, fmt = table.sort, string.format
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "QuestPins") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function opt(k) return QP.db and QP.db[k] ~= false end
local function now() local f = G.GetTime return f and f() or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function locked(f) return T.FrameLocked and T:FrameLocked(f) or false end
local cut = T.Cut or function(s, n) s = tostring(s or "") return #s > n and s:sub(1, n) or s end

local CIRCLE = "Interface\\CharacterFrame\\TempPortraitAlphaMask"
local TEX = {
  avail = "Interface\\GossipFrame\\AvailableQuestIcon",
  turnin = "Interface\\GossipFrame\\ActiveQuestIcon",
  kill = "Interface\\Cursor\\Attack",
  item = "Interface\\Cursor\\Pickup",
  obj = "Interface\\Cursor\\Interact",
  other = CIRCLE,
}
QP.TEX = TEX
QP.MAX_MAP, QP.MAX_MINI = 40, 10          -- plafonds de densité (carte affichée / minimap)
QP.MAX_AVAIL_MAP, QP.MAX_OBJ_MAP = 20, 24
local STEP = 0.4                          -- minimap (moteur maison) : ≤ 2,5 placements / s en marchant
local GOLD = { 1, 0.82, 0.1 }

-- ===================================================================== sources (lecture seule)
local function X() local x = T.Export return type(x) == "table" and x or nil end
local function expData()
  local x = X()
  local d = x and type(x.Data) == "function" and try(x.Data, x)
  return type(d) == "table" and d or nil
end
local function logSnap()
  local x = X()
  local f = x and x.LogSnapshot
  local out = type(f) == "function" and try(f)
  return type(out) == "table" and out or {}
end
local function isDone(id)
  local x = X()
  if x and type(x.IsQuestDone) == "function" then return try(x.IsQuestDone, id) and true or false end
  local CQ = g("C_QuestLog")
  local f = (type(CQ) == "table" and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  return f and try(f, id) and true or false
end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function greyLevel(pl)
  local Q = T.modules and T.modules.Quests
  if Q and type(Q.GreyLevel) == "function" then
    local gl = num(try(Q.GreyLevel, pl))
    if gl then return gl end
  end
  if pl <= 5 then return 0 elseif pl <= 39 then return pl - floor(pl / 10) - 5 elseif pl <= 59 then return pl - floor(pl / 5) - 1 end
  return pl - 9
end
--- couleur de difficulté (comme le jeu) : r, g, b, mot
function QP.LevelColor(ql, pl)
  local Th = T.Theme
  if type(Th) == "table" and type(Th.QuestDiffColor) == "function" then
    local r, gg, b, w = Th:QuestDiffColor(ql, pl)
    if r then return r, gg, b, w end
  end
  ql, pl = tonumber(ql), tonumber(pl)
  if not (ql and pl) or ql <= 0 then return 1, 0.82, 0, "jaune" end
  local d = ql - pl
  if d >= 5 then return 1, 0.1, 0.1, "rouge" elseif d >= 3 then return 1, 0.5, 0.25, "orange"
  elseif d >= -2 then return 1, 1, 0, "jaune" elseif ql > greyLevel(pl) then return 0.25, 0.75, 0.25, "vert" end
  return 0.5, 0.5, 0.5, "gris"
end
local DIFF_RANK = { jaune = 0, orange = 1, vert = 2, rouge = 3, gris = 4 }

-- point du jeu (sans QuestieDB) : C_QuestLog.GetNextWaypoint(questID) → carte, x, y en pour-cent
local function waypoint(qid)
  local CQ = g("C_QuestLog")
  local f = type(CQ) == "table" and CQ.GetNextWaypoint
  if not f then return nil end
  local m, x, y = try(f, qid)
  m, x, y = num(m), num(x), num(y)
  if m and x and y and (x > 0 or y > 0) then return m, x * 100, y * 100 end
  return nil
end

-- Questie dessine-t-il encore ses icônes ? (lecture de SES options : carte, minimap) → pas de doublon
function QP.QuestieDraws(where)
  if opt("force") then return false end
  local Q = g("Questie")
  if type(Q) ~= "table" then return false end            -- Questie absent (QuestieDB seul, ou rien)
  local db = rawget(Q, "db")
  local p = type(db) == "table" and db.profile or nil
  if type(p) ~= "table" then return true end             -- options illisibles : on suppose que oui (jamais en double)
  if p.enabled == false then return false end
  if where == "map" then return p.enableMapIcons ~= false end
  if where == "minimap" then return p.enableMiniMapIcons ~= false end
  return p.enableMapIcons ~= false or p.enableMiniMapIcons ~= false
end
local function want(where)
  return QP._active and opt(where) and not QP.QuestieDraws(where) and true or false
end
QP.Want = want

-- arrêts du trajet : quête → n° d'arrêt (prendre / objectif j / rendre), mêmes numéros que l'onglet et la carte
local function routeIndex()
  local R = T.Route
  if not (R and type(R.Stops) == "function" and type(R.HasRoute) == "function") then return nil end
  if R.char and R.char.paused then return nil end
  if R.IsEnabled and not try(R.IsEnabled, R) then return nil end
  if not try(R.HasRoute, R) then return nil end
  local st = try(R.Stops, R, 64)
  if type(st) ~= "table" then return nil end
  local gr = type(R.Groups) == "function" and try(R.Groups, st) or nil
  local idx = {}
  for i, e in ipairs(st) do
    local s = e.step
    if type(s) == "table" and tonumber(s.q) then
      local n = type(gr) == "table" and gr[i] or i
      local q = idx[s.q]
      if not q then q = { obj = {} }; idx[s.q] = q end
      if s.k == "pick" and not q.pick then q.pick = n
      elseif s.k == "turn" and not q.turn then q.turn = n
      elseif s.k == "obj" then
        local o = tonumber(s.o) or 0
        if not q.obj[o] then q.obj[o] = n end
        if not q.anyObj then q.anyObj = n end
      end
    end
  end
  return idx
end
QP.RouteIndex = routeIndex

-- ===================================================================== liste des icônes (pure : données → pins)
-- pin = { key, kind = avail|turnin|obj, map, x, y (pour-cent), n, w, c (yards monde), r (rayon %), ryd (yards),
--         qs = { {id, title, level, xp, tags, who} }, npc = {k, id}, objText, otype, stop, prio, cr, cg, cb }
local function radiusPct(o)
  local r = tonumber(o and o.r)
  if not r then return nil end
  return max(1.2, (r - 30) / 25)          -- l'export garde r = rayon % × 25 + 30 (amas de 6 % de carte)
end
local OTYPE = { kill = "kill", monster = "kill", item = "item", obj = "obj", object = "obj" }

--- règles d'une quête à prendre (export « avail ») : faisable MAINTENANT par ce perso, pas grise sauf option
function QP.AvailOK(rec, lvl, grey, log, trivialOK)
  if type(rec) ~= "table" or not tonumber(rec.id) then return false, "data" end
  if log[rec.id] then return false, "log" end
  if (tonumber(rec.rl) or 0) > lvl then return false, "level" end
  local ql = tonumber(rec.lv) or 0
  if ql > 0 and ql <= grey and not trivialOK then return false, "grey" end
  if rec.pvp or rec.raid then return false, "kind" end
  for _, p in ipairs(type(rec.pre) == "table" and rec.pre or {}) do
    if tonumber(p) and not isDone(tonumber(p)) then return false, "pre" end
  end
  if type(rec.preAny) == "table" and #rec.preAny > 0 then
    local one = false
    for _, p in ipairs(rec.preAny) do if tonumber(p) and isDone(tonumber(p)) then one = true break end end
    if not one then return false, "pre" end
  end
  for _, x in ipairs(type(rec.excl) == "table" and rec.excl or {}) do
    if tonumber(x) and (log[tonumber(x)] or isDone(tonumber(x))) then return false, "excl" end
  end
  if isDone(rec.id) then return false, "done" end
  return true
end

local function tagsOf(rec)
  local t = {}
  if rec.dg then t[#t + 1] = "Donjon" end
  if tonumber(rec.elite) == 1 then t[#t + 1] = "Élite" end
  if rec.esc then t[#t + 1] = "Escorte" end
  return t
end

--- (re)construit la liste : export + journal réel + trajet. opts = { avail, turnin, objectives, trivial } (défaut : options)
-- (BRAIN) quête mise en pause par le pilote auto : pas d'icône d'objectif (une quête prête à rendre garde son « ? »)
local function parkedQ(qid)
  local B = T.Brain
  if type(B) ~= "table" or type(B.Parked) ~= "function" then return nil end
  return try(B.Parked, qid)
end
QP.ParkedQ = parkedQ
function QP.Build(exp, log, lvl, route, o)
  o = o or { avail = opt("avail"), turnin = opt("turnin"), objectives = opt("objectives"), trivial = QP.db and QP.db.trivial }
  local grey = greyLevel(lvl)
  local recs = {}
  for _, rec in ipairs(exp and exp.quests or {}) do if tonumber(rec.id) then recs[rec.id] = rec end end
  local pins, byKey = {}, {}
  local function pin(key, kind, map, x, y)
    local p = byKey[key]
    if p then return p, false end
    p = { key = key, kind = kind, map = tonumber(map), x = tonumber(x), y = tonumber(y), qs = {}, prio = 1e6 }
    byKey[key] = p
    pins[#pins + 1] = p
    return p, true
  end
  local function addQ(p, q, prio)
    p.qs[#p.qs + 1] = q
    if prio < p.prio then p.prio, p.lead = prio, q end
  end
  -- 1. journal : à rendre, objectifs pas finis
  local ids = {}
  for qid in pairs(log) do if tonumber(qid) then ids[#ids + 1] = tonumber(qid) end end
  tsort(ids)
  for _, qid in ipairs(ids) do
    local lq, rec = log[qid], recs[qid]
    local rq = route and route[qid] or nil
    local q = { id = qid, title = (lq.t ~= nil and tostring(lq.t)) or (rec and rec.t) or ("#" .. qid),
      level = tonumber(lq.lv) or (rec and tonumber(rec.lv)) or 0, xp = rec and tonumber(rec.xp) or 0,
      tags = rec and tagsOf(rec) or {}, inLog = true }
    if tonumber(lq.done) == 1 then
      if o.turnin then
        local f = rec and rec.f
        local m, x, y = f and f.map, f and f.x, f and f.y
        if not (m and x) then m, x, y = waypoint(qid) end
        if m and x then
          local key = (f and f.id and f.id ~= 0) and ("t:" .. tostring(f.k) .. ":" .. f.id) or ("t:q" .. qid)
          local p, new = pin(key, "turnin", m, x, y)
          if new and f and f.id and f.id ~= 0 then p.npc = { k = f.k, id = f.id } end
          q.stop = rq and rq.turn or nil
          addQ(p, q, q.stop or 100)
        end
      end
    elseif not lq.failed and o.objectives and not parkedQ(qid) then
      local any = false
      for j, lo in ipairs(type(lq.obj) == "table" and lq.obj or {}) do
        local ob = rec and rec.o and rec.o[j]
        if not lo.fin and type(ob) == "table" and ob.map and ob.x then
          local stop = rq and (rq.obj[j] or rq.anyObj) or nil
          local cl = { { map = ob.map, x = ob.x, y = ob.y, r = radiusPct(ob), sp = ob.sp } }
          local alt = type(ob.alt) == "table" and ob.alt[1]
          if alt and alt.map and alt.x and (tonumber(alt.sp) or 0) >= 3 then
            cl[2] = { map = alt.map, x = alt.x, y = alt.y, r = 2, sp = alt.sp }
          end
          for ci, c in ipairs(cl) do
            local p = pin("o:" .. qid .. ":" .. j .. ":" .. ci, "obj", c.map, c.x, c.y)
            p.r, p.objText = c.r, lo.text ~= nil and tostring(lo.text) or ob.t
            p.otype = OTYPE[ob.k or ""] or OTYPE[lo.type or ""] or "other"
            p.have, p.need = tonumber(lo.have), tonumber(lo.n)
            addQ(p, { id = qid, title = q.title, level = q.level, xp = q.xp, tags = q.tags, inLog = true, stop = stop },
              (stop or 200) + (ci - 1) * 0.5)
            any = true
          end
        end
      end
      if not any then                         -- sans QuestieDB : le point du jeu (objectif suivant)
        local m, x, y = waypoint(qid)
        if m and x then
          local p = pin("o:" .. qid .. ":w", "obj", m, x, y)
          for _, lo in ipairs(type(lq.obj) == "table" and lq.obj or {}) do
            if not lo.fin then
              p.objText, p.otype = lo.text ~= nil and tostring(lo.text) or nil, OTYPE[lo.type or ""] or "other"
              p.have, p.need = tonumber(lo.have), tonumber(lo.n)
              break
            end
          end
          q.stop = rq and rq.anyObj or nil
          addQ(p, q, q.stop or 200)
        end
      end
    end
  end
  -- 2. à prendre : un « ! » par donneur
  if o.avail and exp then
    for _, rec in ipairs(exp.quests or {}) do
      local s = rec.s
      if rec.st == "avail" and type(s) == "table" and s.map and s.x and QP.AvailOK(rec, lvl, grey, log, o.trivial) then
        local key = (s.id and s.id ~= 0) and ("a:" .. tostring(s.k) .. ":" .. s.id) or ("a:q" .. rec.id)
        local p, new = pin(key, "avail", s.map, s.x, s.y)
        if new and s.id and s.id ~= 0 then p.npc = { k = s.k, id = s.id } end
        local rq = route and route[rec.id] or nil
        local ql = tonumber(rec.lv) or 0
        local _, _, _, word = QP.LevelColor(ql, lvl)
        local q = { id = rec.id, title = rec.t ~= nil and tostring(rec.t) or ("#" .. rec.id), level = ql,
          xp = tonumber(rec.xp) or 0, tags = tagsOf(rec), who = rec.who, stop = rq and rq.pick or nil, diff = word }
        local prio = q.stop or (300 + (DIFF_RANK[word] or 2) * 20 + ((rec.dg or tonumber(rec.elite) == 1) and 10 or 0)
          - min(9, (q.xp or 0) / 1000))
        addQ(p, q, prio)
      end
    end
  end
  -- couleur, monde, rayon en yards
  local Geo = T.Geo
  for _, p in ipairs(pins) do
    local lead = p.lead or p.qs[1]
    if p.kind == "avail" then
      p.cr, p.cg, p.cb = QP.LevelColor(lead and lead.level, lvl)
    else
      p.cr, p.cg, p.cb = GOLD[1], GOLD[2], GOLD[3]
    end
    p.stop = lead and lead.stop or nil
    if Geo and Geo.World and p.map and p.x and p.y then
      p.n, p.w, p.c = Geo.World(p.map, p.x, p.y)
      if p.r and Geo.Rect then
        local rc = Geo.Rect(p.map)
        if rc then p.ryd = abs((rc.y1 or 0) - (rc.y0 or 0)) * p.r / 100 end
      end
    end
  end
  tsort(pins, function(a, b)
    if a.prio ~= b.prio then return a.prio < b.prio end
    return a.key < b.key
  end)
  return pins
end

-- ===================================================================== état : liste refaite seulement quand ça change
QP.gen, QP.collects = 0, 0
local pinsCache, sigCache = {}, {}
local function routeSig()
  local P, R = T.Planner, T.Route
  return P and P.steps, P and P.cursor, R and R.char and R.char.paused
end
local function changed()
  local exp = expData()
  local s1, s2, s3 = routeSig()
  local sg = sigCache
  local qg = T.QuestGen and T:QuestGen() or 0
  local lvl = level()
  local made = exp and exp.made or 0
  local optGen = QP.optGen or 0
  if sg.qg == qg and sg.made == made and sg.lvl == lvl and sg.s1 == s1 and sg.s2 == s2 and sg.s3 == s3
      and sg.opt == optGen and sg.exp == exp then
    return false
  end
  sg.qg, sg.made, sg.lvl, sg.s1, sg.s2, sg.s3, sg.opt, sg.exp = qg, made, lvl, s1, s2, s3, optGen, exp
  return true
end
--- la liste à jour (refaite si le journal / l'export / le niveau / le trajet / une option a changé ; jamais en combat)
function QP.Pins(force)
  if inCombat() and #pinsCache > 0 then return pinsCache end   -- en combat : la liste d'avant (refaite après)
  if force or changed() then
    local exp = expData()
    local ok, res = pcall(QP.Build, exp, logSnap(), level(), routeIndex())
    if ok and type(res) == "table" then pinsCache = res else QP.lastError = tostring(res) end
    QP.gen, QP.collects = QP.gen + 1, QP.collects + 1
  end
  return pinsCache
end
function QP.Invalidate() sigCache.qg = nil end

-- ===================================================================== textes (infobulle)
local function dist(p, me)
  if not (me and p.n and me.c == p.c) then return nil end
  return sqrt((p.n - me.n) ^ 2 + (p.w - me.w) ^ 2)
end
local function meters(yd)
  local m = floor(yd * 0.9144 + 0.5)
  if m >= 1000 then return (fmt("%.1f km", m / 1000):gsub("%.", ",")) end
  return m .. " m"
end
-- position monde du joueur (lecture du cœur, une fois par instant, partagée avec le Chemin dessiné) ; table réutilisée
local meT = { n = 0, w = 0, c = 0, map = 0 }
local function playerWorld(fresh)
  local Geo = T.Geo
  if not (Geo and Geo.World) then return nil end
  local m, x, y
  if T.Here then m, x, y = T:Here() if x then x, y = x * 100, y * 100 end
  elseif Geo.Player then m, x, y = Geo.Player() end
  if not (m and x and y) then return nil end
  local n, w, c = Geo.World(m, x, y)
  if not n then return nil end
  local me = fresh and {} or meT
  me.n, me.w, me.c, me.map = n, w, c, m
  return me
end
QP.PlayerWorld = playerWorld
-- (4.1 LAYOUT) noms en français seulement : la base de Questie est en anglais ; sur un client non anglais, un nom n'est
-- montré que si la traduction de Questie (l10n) le donne, sinon il est omis (« Prête à rendre ») — jamais l'anglais
local function localized(kind, id)
  local QL = rawget(_G, "QuestieLoader")
  local l10n = type(QL) == "table" and type(QL.ImportModule) == "function" and try(QL.ImportModule, QL, "l10n") or nil
  local look = type(l10n) == "table" and (kind == "obj" and l10n.objectNameLookup or l10n.npcNameLookup) or nil
  if type(look) ~= "table" then return nil, false end
  local loc = try(rawget(_G, "GetLocale"))
  local e = look[id]
  if e == nil and type(loc) == "string" and type(look[loc]) == "table" then e = look[loc][id] end
  if type(e) == "table" then e = e[1] end
  return type(e) == "string" and e ~= "" and e or nil, true
end
QP.Localized = localized
local memoN, memoO = {}, {}
local npcName0
local function npcName(npc)
  if type(npc) ~= "table" or not npc.id then return nil end
  local m = npc.k == "obj" and memoO or memoN
  local v = m[npc.id]
  if v == nil then v = npcName0(npc) or false m[npc.id] = v end
  return v or nil
end
function npcName0(npc)
  local loc = try(rawget(_G, "GetLocale"))
  local english = loc == nil or loc == "enUS" or loc == "enGB"
  if not english then
    local fr, hasL10n = localized(npc.k, npc.id)
    if fr or hasL10n then return fr end   -- la traduction de Questie existe : son nom français, sinon rien
  end
  local x = X()
  local db = x and type(x.Source) == "function" and try(x.Source, x)
  local get = type(db) == "table" and (npc.k == "obj" and db.O or db.N) or nil
  local nm = type(get) == "function" and try(get, npc.id, "name") or nil
  return type(nm) == "string" and nm ~= "" and nm or nil
end
QP.NpcName = npcName
local function fmtNum(n) return (T.Fmt and T.Fmt.Num and T.Fmt.Num(n)) or tostring(floor((n or 0) + 0.5)) end

--- titre + lignes { texte, r, g, b } de l'infobulle d'une icône
function QP.TipLines(p, me)
  local out = {}
  local lead = p.lead or p.qs[1] or {}
  local pl = level()
  local function qline(q)
    local r, gg, b = QP.LevelColor(q.level, pl)
    local lv = (tonumber(q.level) or 0) > 0 and ("[" .. q.level .. "] ") or ""
    return lv .. cut(q.title, 44), r, gg, b
  end
  local title, tr, tg, tb = qline(lead)
  if p.kind == "obj" then
    if p.objText then out[#out + 1] = { cut(p.objText, 60), 1, 1, 1 } end
  elseif p.kind == "turnin" then
    local nm = npcName(p.npc)
    out[#out + 1] = { nm and ("À rendre à " .. cut(nm, 30)) or "Prête à rendre", 0.6, 1, 0.6 }
  else
    local nm = npcName(p.npc)
    out[#out + 1] = { nm and ("À prendre chez " .. cut(nm, 30)) or "À prendre ici", 1, 0.82, 0.2 }
  end
  -- plusieurs quêtes au même endroit : la liste (4 au plus)
  if #p.qs > 1 then
    local shown = 0
    for _, q in ipairs(p.qs) do
      if q ~= lead and shown < 3 then
        local s, r, gg, b = qline(q)
        out[#out + 1] = { "+ " .. s, r, gg, b }
        shown = shown + 1
      end
    end
    if #p.qs - 1 > shown then out[#out + 1] = { fmt("+ %d autres", #p.qs - 1 - shown), 0.7, 0.7, 0.7 } end
  end
  if (tonumber(lead.xp) or 0) > 0 and p.kind ~= "obj" then
    out[#out + 1] = { fmtNum(lead.xp) .. " XP au rendu", 0.6, 0.8, 1 }
  end
  local d = dist(p, me)
  if d then
    local NT = T.NavTime
    local sec = (NT and type(NT.Model) == "function" and num(try(NT.Model, d, p.map))) or d * 1.3 / 7
    local Pl = T.Plain
    local eta = (Pl and Pl.Minutes and Pl.Minutes(sec)) or ("≈ " .. max(1, floor(sec / 60 + 0.5)) .. " min")
    out[#out + 1] = { meters(d) .. " · " .. eta .. " à pied", 0.6, 0.8, 1 }
  end
  -- pourquoi elle compte maintenant
  local why
  if lead.stop then why = fmt("Sur ton trajet : arrêt %d", lead.stop)
  elseif p.kind == "turnin" then why = "L'XP tombe au rendu"
  elseif p.kind == "obj" then why = "Objectif de ta quête"
  else
    local tags = lead.tags or {}
    if tags[1] == "Donjon" then why = "Quête de donjon : à faire avec ton groupe"
    elseif tags[1] == "Élite" or tags[2] == "Élite" then why = "Élite : à 3 ou plus"
    elseif type(lead.who) == "table" and lead.who[1] then why = "Ton groupe l'a déjà : " .. cut(tostring(lead.who[1]), 20)
    elseif lead.diff == "vert" then why = "Facile : XP réduite, vite faite"
    elseif lead.diff == "rouge" or lead.diff == "orange" then why = "Difficile pour ton niveau"
    else why = "À ton niveau, dans ta zone" end
  end
  out[#out + 1] = { why, 1, 0.82, 0.2 }
  return title, out, tr, tg, tb
end

local function showTip(owner, p)
  local tt = g("GameTooltip")
  if not (tt and p) then return end
  try(tt.SetOwner, tt, owner, "ANCHOR_RIGHT")
  local title, lines, r, gg, b = QP.TipLines(p, playerWorld())
  try(tt.AddLine, tt, title, r or 1, gg or 1, b or 1, true)
  for _, l in ipairs(lines) do try(tt.AddLine, tt, l[1], l[2], l[3], l[4], true) end
  try(tt.AddLine, tt, "Clic : la boussole vise ce point", 0.4, 0.8, 1, true)
  try(tt.Show, tt)
end
local function hideTip() local tt = g("GameTooltip") if tt then try(tt.Hide, tt) end end

--- clic : la boussole vise (quête du journal : sa cible de boussole ; sinon ce point)
function QP.Click(p)
  local C = T.Compass
  if not (p and type(C) == "table" and type(C.SetTarget) == "function") then return nil end
  local lead = p.lead or p.qs[1] or {}
  if lead.inLog and lead.id and type(C.SetQuestTarget) == "function" then
    local t = try(C.SetQuestTarget, lead.id)
    if t then return t end
  end
  if not (p.map and p.x) then return nil end
  local label = (p.kind == "avail" and ("Prendre « " .. cut(lead.title, 30) .. " »"))
    or (p.kind == "turnin" and ("Rendre « " .. cut(lead.title, 30) .. " »")) or cut(p.objText or lead.title, 40)
  return try(C.SetTarget, C, { mapID = p.map, x = p.x, y = p.y, label = label, source = "pins" })
end
local function click(self)
  local p = self and self.pin
  if not p then return end
  if T.UserAction then T:UserAction(QP.Click, p) else QP.Click(p) end
end

-- ===================================================================== carte du monde : notre calque
local view, canvas
local mapPins = {}                        -- réserve { b = bouton, disk = texture }
QP.mapPins = mapPins
QP.mapShown, QP.mapRedraws, QP.mapMoves = 0, 0, 0
local ms = { id = nil, w = nil, h = nil, gen = nil, l = nil, b = nil, vl = nil, vb = nil, vw = nil, vh = nil }
local function mapKind(id)
  local CM = g("C_Map")
  local info = CM and type(CM.GetMapInfo) == "function" and try(CM.GetMapInfo, id)
  return type(info) == "table" and num(info.mapType) or 3
end
-- position (px canevas, origine en haut à gauche) d'une icône sur la carte affichée ; nil si ailleurs
function QP.OnMap(mapID, p, W, H)
  if not (p and W and H) then return nil end
  local x, y
  if p.map == mapID and p.x and p.y then
    x, y = p.x, p.y
  elseif p.n and T.Geo and T.Geo.Rect and T.Geo.FromWorld then
    local r = T.Geo.Rect(mapID)
    if not (r and r.c == p.c) then return nil end
    x, y = T.Geo.FromWorld(mapID, p.n, p.w)
  end
  x, y = tonumber(x), tonumber(y)
  if not (x and y) or x < -2 or x > 102 or y < -2 or y > 102 then return nil end
  return x / 100 * W, y / 100 * H
end

-- (4.2 ROUTE42) les icônes DANS la carte : un calque enfant de son canevas (T.MapCanvas, PathDraw.lua) — elles glissent et
-- zooment avec elle, coupées à son bord, cachées quand elle se ferme ; aucun fournisseur de données (contamine la manette
-- sur Forever), aucun cadre sur UIParent par-dessus la carte ; souris seulement sur les icônes
local function MCv() local m = T.MapCanvas return type(m) == "table" and type(m.Layer) == "function" and m or nil end
local function ensureCanvas()
  if view then return view end
  local MC = MCv()
  if not MC or inCombat() then return nil end
  view = MC.Layer()
  if not view then return nil end
  view._lvl = 1                                    -- sous le Chemin dessiné (pastilles numérotées)
  canvas = view
  QP.view, QP.canvas = view, canvas
  return view
end
local function mapPin(i)
  local e = mapPins[i]
  if e then return e end
  local CF = g("CreateFrame")
  if not (canvas and CF) then return nil end
  e = {}
  e.disk = canvas:CreateTexture(nil, "BORDER")
  e.disk:SetTexture(CIRCLE)
  e.disk:Hide()
  local b = CF("Button", nil, canvas)
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  try(b.RegisterForClicks, b, "LeftButtonUp")
  b:SetScript("OnEnter", function(self) showTip(self, self.pin) end)
  b:SetScript("OnLeave", hideTip)
  b:SetScript("OnClick", click)
  b:Hide()
  e.b = b
  mapPins[i] = e
  return e
end
local function styleIcon(tex, p)
  if p.kind == "obj" then
    tex:SetTexture(TEX[p.otype or "other"] or CIRCLE)
    try(tex.SetDesaturated, tex, false)
    if p.otype == "other" then tex:SetVertexColor(GOLD[1], GOLD[2], GOLD[3], 1) else tex:SetVertexColor(1, 1, 1, 1) end
  elseif p.kind == "turnin" then
    tex:SetTexture(TEX.turnin)
    try(tex.SetDesaturated, tex, false)
    tex:SetVertexColor(1, 1, 1, 1)
  else
    tex:SetTexture(TEX.avail)
    -- couleur de niveau : « ! » désaturé puis teinté (le jaune du jeu reste jaune)
    try(tex.SetDesaturated, tex, true)
    tex:SetVertexColor(p.cr or 1, p.cg or 1, p.cb or 0, 1)
  end
end
local function hideMapFrom(i)
  for k = i, #mapPins do
    local e = mapPins[k]
    if e.b:IsShown() then e.b:Hide() end
    if e.disk:IsShown() then e.disk:Hide() end
    e.b.pin = nil
  end
end
function QP.ClearMap()
  hideMapFrom(1)
  QP.mapShown = 0
end

-- sélection des icônes de la carte affichée (plafonds par sorte puis total), sans table neuve par passe
local sel = {}
local function selectFor(mapID, W, H, list)
  local n, nAvail, nObj = 0, 0, 0
  for _, p in ipairs(list) do
    if n >= QP.MAX_MAP then break end
    local cap = (p.kind == "avail" and nAvail >= QP.MAX_AVAIL_MAP) or (p.kind == "obj" and nObj >= QP.MAX_OBJ_MAP)
    if not cap then
      local x, y = QP.OnMap(mapID, p, W, H)
      if x then
        n = n + 1
        local k = n * 3
        sel[k - 2], sel[k - 1], sel[k] = p, x, y
        if p.kind == "avail" then nAvail = nAvail + 1 elseif p.kind == "obj" then nObj = nObj + 1 end
      end
    end
  end
  return n
end
QP.SelectFor = function(mapID, W, H, list)
  local n = selectFor(mapID, W, H, list)
  local out = {}
  for i = 1, n do out[i] = sel[i * 3 - 2] end
  return out
end

function QP.DrawMap()
  if not ensureCanvas() then return end
  if not want("map") then QP.ClearMap() return end
  local MC = MCv()
  local mapID, cw, ch = MC.Fit(view)
  if not (mapID and cw) then QP.ClearMap() return end
  if locked(view) then return end
  local list = QP.Pins()
  ms.id, ms.w, ms.h, ms.gen = mapID, cw, ch, QP.gen
  QP.mapRedraws = QP.mapRedraws + 1
  local mk = mapKind(mapID)
  if mk ~= nil and mk < 2 then QP.ClearMap() return end   -- carte du monde / cosmique : rien
  local small = mk == 2                                    -- continent : icônes plus petites
  local n = selectFor(mapID, cw, ch, list)
  local used = 0
  for i = 1, n do
    local p, x, y = sel[i * 3 - 2], sel[i * 3 - 1], sel[i * 3]
    local e = mapPin(i)
    if not e then break end
    used = i
    local b = e.b
    local sz = small and (p.kind == "obj" and 9 or 11) or (p.kind == "obj" and 14 or (p.stop and 20 or 17))
    styleIcon(b.icon, p)
    b:SetSize(sz, sz)
    b:ClearAllPoints()
    -- arrêt du trajet : l'icône se pose à côté de la pastille numérotée du Chemin dessiné (pas dessous)
    if p.stop and p.kind ~= "obj" then b:SetPoint("BOTTOMLEFT", canvas, "TOPLEFT", x + 6, -y + 4)
    else b:SetPoint("CENTER", canvas, "TOPLEFT", x, -y) end
    b.pin = p
    b:Show()
    if p.kind == "obj" and p.r and not small then
      local rad = min(80, max(8, p.r / 100 * (cw + ch) / 2))
      e.disk:ClearAllPoints()
      e.disk:SetPoint("CENTER", canvas, "TOPLEFT", x, -y)
      e.disk:SetSize(rad * 2, rad * 2)
      e.disk:SetVertexColor(GOLD[1], GOLD[2], GOLD[3], p.stop and 0.28 or 0.18)
      e.disk:Show()
    elseif e.disk:IsShown() then
      e.disk:Hide()
    end
  end
  hideMapFrom(used + 1)
  QP.mapShown = used
  if not view:IsShown() then view:Show() end
end

-- (4.2 ROUTE42) prévenu par la carte (affichée, autre carte, zoom : T.MapCanvas.Watch) ou par la liste (QP.MapDirty) ; un
-- dessin seulement si la carte affichée, le zoom, la taille ou la liste a changé — glisser la carte ne coûte rien
local function mapOnUpdate()
  if not QP._active then return end
  local MC = MCv()
  local id, cs, cw, ch = MC.Key()
  if not id then return end
  if ms.dirty or id ~= ms.kid or cs ~= ms.kcs or cw ~= ms.kw or ch ~= ms.kh or ms.gen ~= QP.gen then
    ms.kid, ms.kcs, ms.kw, ms.kh, ms.dirty = id, cs, cw, ch, false
    QP.DrawMap()
  end
end
function QP.MapDirty()
  ms.dirty = true
  if view and view:IsVisible() and QP._active then mapOnUpdate() end
end
function QP.MapPanned() end
function QP.MapShown()
  if not ensureCanvas() then return end
  ms.gen, ms.dirty = nil, true
  if want("map") then mapOnUpdate() end
end
function QP.MapHidden() end                         -- le calque se cache avec la carte
local function attachMap()
  if QP._mapHooked then return true end
  if not ensureCanvas() then return false end
  MCv().Watch(view, function() if view:IsVisible() then mapOnUpdate() end end)
  QP._mapHooked, QP.mapEngine = true, "canvas"
  return true
end
QP.AttachMap = attachMap

-- ===================================================================== minimap
local miniPins = {}                       -- réserve de boutons (moteur maison et HereBeDragons)
QP.miniPins = miniPins
QP.miniShown, QP.miniPlaces = 0, 0
local function minimap() return g("Minimap") end
local function MP() local m = T.MinimapPins return type(m) == "table" and m or nil end
local function hbd() local m = MP() return m and type(m.HBD) == "function" and m.HBD() or nil end
local function miniPin(i)
  local b = miniPins[i]
  if b then return b end
  local mm, CF = minimap(), g("CreateFrame")
  if not (mm and CF) then return nil end
  b = CF("Button", nil, mm)                   -- enfant de Minimap (comme HereBeDragons) ; Minimap intacte
  b:SetSize(12, 12)
  try(b.SetFrameLevel, b, (num(try(mm.GetFrameLevel, mm)) or 1) + 6)
  b.ring = b:CreateTexture(nil, "BACKGROUND")
  b.ring:SetTexture(CIRCLE)
  b.ring:SetPoint("CENTER", b, "CENTER", 0, 0)
  b.ring:Hide()
  b.icon = b:CreateTexture(nil, "OVERLAY")
  b.icon:SetAllPoints(b)
  try(b.RegisterForClicks, b, "LeftButtonUp")
  b:SetScript("OnEnter", function(self) showTip(self, self.pin) end)
  b:SetScript("OnLeave", hideTip)
  b:SetScript("OnClick", click)
  b:Hide()
  miniPins[i] = b
  return b
end
local function styleMini(b, p, k)
  styleIcon(b.icon, p)
  local sz = p.kind == "obj" and 11 or 13
  b:SetSize(sz, sz)
  if p.kind == "obj" and p.ryd and k then
    local s = max(10, min(44, floor(2 * p.ryd * k + 0.5)))
    b.ring:SetSize(s, s)
    b.ring:SetVertexColor(GOLD[1], GOLD[2], GOLD[3], 0.22)
    b.ring:Show()
  elseif b.ring:IsShown() then
    b.ring:Hide()
  end
end
local function hideMiniFrom(i)
  for k = i, #miniPins do local b = miniPins[k] if b:IsShown() then b:Hide() end b.pin, b.px, b.inn = nil, nil, nil end
end

-- état de la minimap : rayon yards / pixels, rotation, forme — relus seulement après un événement (zoom, réglage,
-- intérieur / extérieur, zone) ou toutes les 5 s ; le cap seulement si la minimap tourne (table réutilisée)
local view2 = { yr = 233, pr = 70, facing = nil, rotate = false, shape = "ROUND", rot = false }
QP.viewDirty = true
QP.step = STEP
local function fillMini(full)
  local mmf = minimap()
  if full or QP.viewDirty then
    QP.viewDirty = false
    local M2 = MP()
    local zoom = num(try(mmf.GetZoom, mmf)) or 0
    local indoor = try(g("IsIndoors")) and true or false
    view2.yr = (M2 and M2.YardsRadius and M2.YardsRadius(zoom, indoor)) or (indoor and 150 or 700 / 3)
    view2.pr = (num(try(mmf.GetWidth, mmf)) or 140) / 2
    local rot = try(g("GetCVar"), "rotateMinimap")
    view2.rot = (rot == "1" or rot == 1 or rot == true)
    view2.shape = try(g("GetMinimapShape")) == "SQUARE" and "SQUARE" or "ROUND"
    -- cadence = environ 1,5 pixel de minimap par passe en courant (7 yd/s) : ~1,4 / s dézoomé, 5 / s au zoom maximal
    local k = view2.pr / view2.yr
    QP.step = max(0.15, min(0.75, 1.5 / max(0.1, k * 7)))
  end
  local facing = nil
  if view2.rot then facing = num(try(g("GetPlayerFacing"))) end
  view2.facing = facing
  view2.rotate = view2.rot and facing ~= nil
  return view2
end

-- candidats de la minimap : même continent, à moins du rayon + marge, plafond (refait si la liste / la carte change)
local near, nearN = {}, 0
local function pickNear(me, yr)
  nearN = 0
  for _, p in ipairs(QP.Pins()) do
    if nearN >= QP.MAX_MINI then break end
    if p.n and p.c == me.c and abs(p.n - me.n) <= yr + 60 and abs(p.w - me.w) <= yr + 60 then
      nearN = nearN + 1
      near[nearN] = p
    end
  end
  for i = nearN + 1, #near do near[i] = nil end
end

local mm = { at = -100, n = nil, w = nil, f = nil, yr = nil, gen = nil, pickN = nil, pickW = nil, idle = 0, dirty = nil }
QP.miniState = mm
local cos, sin = math.cos, math.sin
-- support des icônes de la minimap (notre cadre, enfant de Minimap) : minimap fixe (nord en haut) = les icônes sont
-- posées UNE fois par rapport à lui, et en marchant seul lui glisse (1 point posé par passe) ; minimap qui tourne =
-- chaque icône reposée (seulement si elle bouge d'un demi-pixel)
local holder
local function ensureHolder()
  if holder then return holder end
  local mmf, CF = minimap(), g("CreateFrame")
  if not (mmf and CF) then return nil end
  holder = CF("Frame", nil, mmf)
  holder:SetSize(1, 1)
  try(holder.EnableMouse, holder, false)
  holder:SetPoint("CENTER", mmf, "CENTER", 0, 0)
  holder.hx, holder.hy = 0, 0
  QP.holder = holder
  return holder
end
local base = { n = 0, w = 0, k = 0, rot = false }
QP.miniBase = base
local function inside(px, py, lim, square)
  if square then return abs(px) <= lim and abs(py) <= lim end
  return px * px + py * py <= lim * lim
end
--- repose toutes les icônes candidates autour de la position `me` (liste, zoom ou rotation changés)
function QP.PlaceMini(me, v)
  local h = ensureHolder()
  if not h then return end
  local k = v.pr / v.yr
  local c, sn = 1, 0
  if v.rotate and v.facing then c, sn = cos(-v.facing), sin(-v.facing) end
  base.n, base.w, base.k, base.rot = me.n, me.w, k, v.rotate and true or false
  if h.hx ~= 0 or h.hy ~= 0 then h:SetPoint("CENTER", minimap(), "CENTER", 0, 0) h.hx, h.hy = 0, 0 end
  local used = 0
  for i = 1, nearN do
    local p = near[i]
    local sx, sy = -(p.w - me.w), p.n - me.n
    if v.rotate then sx, sy = sx * c - sy * sn, sx * sn + sy * c end
    local px, py = sx * k, sy * k
    used = used + 1
    local b = miniPin(used)
    if not b then used = used - 1 break end
    if b.pin ~= p or b.k ~= k then styleMini(b, p, k) b.k = k b.px = nil end
    b.pin = p
    b.lim = v.pr - ((p.kind == "obj" and 5) or 6)
    if not b.px or abs(px - b.px) >= 0.5 or abs(py - b.py) >= 0.5 then
      b.px, b.py = px, py
      b:ClearAllPoints()
      b:SetPoint("CENTER", h, "CENTER", px, py)
    end
    local inn = inside(px, py, b.lim, v.shape == "SQUARE")
    if inn ~= b.inn then b.inn = inn if inn then b:Show() else b:Hide() end end
  end
  for i = used + 1, #miniPins do local b = miniPins[i] if b:IsShown() then b:Hide() end b.pin, b.px, b.inn = nil, nil, nil end
  QP.miniUsed = used
  QP.miniPlaces = QP.miniPlaces + 1
  local n = 0
  for i = 1, used do if miniPins[i].inn then n = n + 1 end end
  QP.miniShown = n
end
--- minimap fixe : la marche ne fait glisser que le support (et montre / cache ce qui passe le bord) ; vrai si glissé
function QP.SlideMini(me, v)
  local h = holder
  if not h then return false end
  local k = base.k
  local hx, hy = (me.w - base.w) * k, (base.n - me.n) * k
  local ex, ey = hx - h.hx, hy - h.hy
  if ex < 0.5 and ex > -0.5 and ey < 0.5 and ey > -0.5 then return false end
  h.hx, h.hy = hx, hy
  h:SetPoint("CENTER", minimap(), "CENTER", hx, hy)
  local sq, n = v.shape == "SQUARE", 0
  for i = 1, QP.miniUsed or 0 do
    local b = miniPins[i]
    local bx = b.px
    if bx then
      local x, y, lim = bx + hx, b.py + hy, b.lim
      local inn
      if sq then inn = x <= lim and x >= -lim and y <= lim and y >= -lim else inn = x * x + y * y <= lim * lim end
      if inn ~= b.inn then b.inn = inn if inn then b:Show() else b:Hide() end end
      if inn then n = n + 1 end
    end
  end
  QP.miniShown = n
  QP.miniSlides = (QP.miniSlides or 0) + 1
  return true
end

-- HereBeDragons (Questie l'embarque) : la lib place et tourne les icônes ; resynchronisé quand la liste change
QP.hbdShown = {}
function QP.SyncHBD(me)
  local H = hbd()
  if not H then return false end
  local want2 = {}
  local used = 0
  if me then
    pickNear(me, 600)                       -- la lib cache ce qui sort du cadre : une couronne large suffit
    for i = 1, nearN do
      local p = near[i]
      if p.map and p.x then
        used = used + 1
        local b = miniPin(used)
        if not b then break end
        styleMini(b, p, nil)
        b.pin = p
        if pcall(H.AddMinimapIconMap, H, QP, b, p.map, p.x / 100, p.y / 100, false, false) then want2[b] = true end
      end
    end
  end
  for b in pairs(QP.hbdShown) do
    if not want2[b] then pcall(H.RemoveMinimapIcon, H, QP, b) b.pin = nil b:Hide() end
  end
  QP.hbdShown = want2
  QP.miniShown = used
  QP.miniEngine = "hbd"
  return true
end
local function clearHBD()
  local H = hbd()
  for b in pairs(QP.hbdShown) do if H then pcall(H.RemoveMinimapIcon, H, QP, b) end b:Hide() b.pin = nil end
  QP.hbdShown = {}
end
function QP.ClearMini()
  if next(QP.hbdShown) then clearHBD() end
  hideMiniFrom(1)
  QP.miniShown = 0
  mm.n = nil
end

-- une passe minimap (moteur maison) : liste (seulement après un changement) → candidats proches → minimap fixe : le
-- support glisse ; minimap qui tourne / zoom / liste changée : les icônes se reposent ; immobile : le minuteur s'arrête
QP.dirtyGen = 0
function QP.MiniTick(force)
  if force or mm.dirty ~= QP.dirtyGen then
    mm.dirty = QP.dirtyGen
    mm.want, mm.hbd = want("minimap"), hbd()
    QP.Pins()
    mm.listN = #pinsCache
  end
  if not mm.want then QP.ClearMini() QP.Sleep() return end
  local me = playerWorld()
  if not me then QP.ClearMini() return end
  if mm.hbd then
    if force or mm.gen ~= QP.gen or mm.hbdMap ~= me.map then
      mm.gen, mm.hbdMap = QP.gen, me.map
      QP.SyncHBD(me)
    end
    QP.Sleep()                               -- la lib s'occupe du reste : plus rien ici
    return
  end
  QP.miniEngine = "own"
  if mm.listN == 0 then if QP.miniShown > 0 then QP.ClearMini() end QP.Sleep() return end
  local tk = (mm.tick or 0) + 1
  mm.tick = tk
  local v = view2
  local full = tk % 12 == 0
  if full or v.rot or QP.viewDirty then v = fillMini(full) end
  -- candidats : quand la liste change, au zoom, ou après 60 yd de marche
  local pn, pw = mm.pickN, mm.pickW
  local repick = force or not pn or mm.gen ~= QP.gen or mm.yr ~= v.yr or mm.pr ~= v.pr
  if not repick then
    local dn, dw = me.n - pn, me.w - pw
    repick = dn * dn + dw * dw > 3600
  end
  local did
  if repick then
    pickNear(me, v.yr)
    mm.gen, mm.pickN, mm.pickW, mm.yr, mm.pr = QP.gen, me.n, me.w, v.yr, v.pr
    QP.PlaceMini(me, v)
    did = true
  elseif v.rotate or v.rotate ~= base.rot then
    local f = v.facing or 0
    if v.rotate ~= base.rot or abs(f - (mm.f or 0)) >= 0.02 or abs(me.n - (mm.n or 0)) >= 0.5 or abs(me.w - (mm.w or 0)) >= 0.5 then
      mm.n, mm.w, mm.f = me.n, me.w, f
      QP.PlaceMini(me, v)
      did = true
    end
  else
    did = QP.SlideMini(me, v)
  end
  if did then mm.idle = 0 return end
  mm.idle = mm.idle + 1
  if mm.idle >= 3 and not (T.Moving and T:Moving(1)) then QP.Sleep() end   -- immobile : réveil au mouvement
end
local ticker
function QP.Sleep()
  if ticker then ticker:Cancel() ticker = nil end
  QP.asleep = true
end
function QP.Wake()
  mm.idle = 0
  -- rien à suivre en marchant (HereBeDragons place tout, liste vide, minimap coupée) tant que rien n'a changé
  if mm.dirty == QP.dirtyGen and (mm.hbd or not mm.want or mm.listN == 0) then return end
  if ticker and abs((ticker.sec or QP.step) - QP.step) > 0.04 then ticker:Cancel() ticker = nil end   -- zoom changé
  if not ticker and QP._active and opt("minimap") and T.NewTicker then
    QP.asleep = false
    ticker = T:NewTicker(QP.step, function() QP.MiniTick() end, QP)
  end
end

-- ===================================================================== cycle de vie
-- un changement (journal, export, trajet, niveau, option) : la carte ouverte se redessine, la minimap se réveille ;
-- la liste elle-même n'est refaite qu'au moment où une surface en a besoin (rien si tout est fermé et immobile)
local function onChange()
  QP.dirtyGen = QP.dirtyGen + 1
  QP.MapDirty()
  QP.Wake()
end
QP.OnChange = onChange
function QP:OnEnable()
  QP._active = true
  attachMap()
  QP.asleep = true
  QP.Wake()
  ensureCanvas()
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA", "PLAYER_LEVEL_UP", "QUEST_ACCEPTED",
    "QUEST_TURNED_IN", "QUEST_REMOVED" }) do
    self:RegisterEvent(ev, function() mm.n, mm.pickN, QP.viewDirty = nil, nil, true onChange() end)
  end
  for _, ev in ipairs({ "MINIMAP_UPDATE_ZOOM", "CVAR_UPDATE", "ZONE_CHANGED_INDOORS" }) do
    self:RegisterEvent(ev, function() mm.n, QP.viewDirty = nil, true if minimap() then fillMini(true) end QP.Wake() end)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() attachMap() ensureCanvas() onChange() end)
  self:On("QUESTS_UPDATED", onChange)
  self:On("EXPORT_UPDATED", onChange)
  self:On("PLAN_BUILT", onChange)
  self:On("BRAIN_PARKED", function() QP.Invalidate() onChange() end)   -- (BRAIN) quêtes mises en pause / reprises
  if T.OnMotion then T:OnMotion(QP, QP.Wake) end
  local MC = MCv()
  local wm = MC and MC.Parts()
  if wm and try(wm.IsShown, wm) then QP.MapShown() end
end
function QP:OnDisable()
  QP._active = false
  QP.Sleep()
  QP.ClearMini()
  QP.ClearMap()
  QP.MapHidden()
  if T.OnMotion then T:OnMotion(QP, nil) end
end
QP.autoIdleSafe = true
function QP:OnOptionChanged()
  QP.optGen = (QP.optGen or 0) + 1
  QP.dirtyGen, QP.viewDirty = QP.dirtyGen + 1, true
  ms.gen, mm.n, mm.gen = nil, nil, nil
  QP.ClearMini()
  onChange()
  if want("minimap") then QP.MiniTick(true) end
  if view and view:IsShown() then QP.DrawMap() end
end

--- résumé (statut, page « Mes addons ») : nombre d'icônes par sorte et ce qui les tait
function QP.Counts()
  local c = { avail = 0, turnin = 0, obj = 0, total = 0 }
  for _, p in ipairs(pinsCache) do
    c[p.kind] = (c[p.kind] or 0) + 1
    c.total = c.total + 1
  end
  return c
end
function QP.Status()
  if not QP._active then return "Icônes de quêtes : coupées" end
  local c = QP.Counts()
  local why = (QP.QuestieDraws("map") and QP.QuestieDraws("minimap")) and " · tues : Questie dessine les siennes" or ""
  return fmt("Icônes de quêtes : %d à prendre, %d à rendre, %d objectifs · carte %d, minimap %d (%s)%s", c.avail, c.turnin,
    c.obj, QP.mapShown, QP.miniShown, QP.miniEngine == "hbd" and "HereBeDragons" or "TreeAI", why)
end
if T.RegisterSlash then
  T:RegisterSlash("icones", function() QP:Print(QP.Status()) end, "icones : icônes de quêtes de la carte et de la minimap (statut)")
end
