-- TreeAI — module Suivi des quêtes (QUEST-PRO, refait par TRACK-PRO). But : mieux qu'Adventure Guide Forever
-- (voir research/agf_vs_treeai.md), en complément de Questie / Shortest Path / TomTom, jamais en doublon.
--  * tri par MODÈLE DE COÛT : XP/min = XP ÷ (trajet + travail × difficulté). Trajet = Shortest Path Forever
--    (Estimate, 8 plus proches, hors combat) sinon distance ÷ vitesse ; travail = objectifs restants × temps
--    par monstre MESURÉ (Predict) / taux de drop MESURÉ (« encore ~6 × Loup ») ; difficulté = AGF
--    (1 + 0,5 × Δniv²) × élite × groupe suggéré × donjon ; quête partagée avec un ami ×0,7. Hystérésis : la
--    tête ne change que pour ≥ 10 % de mieux. Autres tris : itinéraire, distance, niveau, zone.
--  * filtres tous / zone / prêtes / groupe / faciles ; « prête à rendre » + PNJ + distance ; ETA par objectif ;
--    aperçu de chaîne (→ A → B, « chaîne 2/4 ») ; calque groupe (amis TreeAI via Squad : progression ; autres
--    via C_QuestLog.IsUnitOnQuest) ; histoire (résumé de la description) au survol ; son à la fin d'une quête.
--  * plan TreeAI (TreeAI.Planner, s'il a un plan actif) : étape courante + 2 suivantes en tête.
--  * affichage : onglet « Quêtes » (TreeAI:SetTabLines) + cadre compact OPTIONNEL à nous (déplaçable,
--    verrouillable, réduction par quête, police / largeur / échelle / fond réglables).
--  * clic = cible de la boussole ; Maj-clic = « Aller là » (SPF / TomTom / repère) ; Ctrl-clic = itinéraire
--    complet → SPF ; Alt-clic = masquer jusqu'au /reload ; clic droit (cadre) = réduire / déplier.
--  * astuces conservées : faciles à côté, « passe prendre … en chemin », « tu pars sans prendre … »,
--    infobulle « lâche <objet> (taux) ».
-- Règles 3.8.2 : aucune fonction / aucun cadre de Blizzard touché (pas de suivi Blizzard modifié, pas de
-- sélection du journal) ; infobulles : AddLine sur GameTooltip via TooltipDataProcessor / HookScript.
-- Calculs lourds hors combat seulement (en combat : progression recopiée, rien de recalculé).
-- Résumé IA : TreeAIDB.chars[perso].summary.tracker. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Tracker", {
  title = "Suivi des quêtes",
  defaults = { enabled = true, frame = true, standalone = false, easy = true, pickup = true, hubWarn = true,
               dropTips = true, maxLines = 12, group = true, story = true, chain = true, sound = true, plan = true,
               lock = false, fontSize = 11, width = 300, scale = 100, alpha = 35, maxQuests = 10,
               sort = "cout", filter = "tous" },
  charDefaults = { point = "RIGHT", x = -40, y = 120, warned = {}, collapsed = {}, allCollapsed = false },
  options = {
    { key = "standalone", type = "toggle", label = "Cadre de suivi compact à l'écran (en plus de l'onglet)" },
    { key = "frame", type = "toggle", label = "Cadre de suivi si le panneau n'a pas d'onglets" },
    { key = "lock", type = "toggle", label = "Verrouiller le cadre de suivi" },
    { key = "fontSize", type = "range", label = "Taille du texte", min = 8, max = 16, step = 1 },
    { key = "width", type = "range", label = "Largeur du cadre", min = 200, max = 460, step = 10 },
    { key = "scale", type = "range", label = "Échelle du cadre (%)", min = 60, max = 160, step = 5 },
    { key = "alpha", type = "range", label = "Opacité du fond (%)", min = 0, max = 100, step = 5 },
    { key = "maxQuests", type = "range", label = "Quêtes affichées", min = 3, max = 25, step = 1 },
    { key = "plan", type = "toggle", label = "Étapes du plan TreeAI en tête (si un plan est actif)" },
    { key = "group", type = "toggle", label = "Calque groupe : qui a la quête, sa progression" },
    { key = "chain", type = "toggle", label = "Aperçu des chaînes (quêtes débloquées ensuite)" },
    { key = "story", type = "toggle", label = "Histoire de la quête au survol" },
    { key = "sound", type = "toggle", label = "Son quand une quête est finie (seul son du suivi)" },
    { key = "easy", type = "toggle", label = "Quêtes faciles à côté (Questie)" },
    { key = "pickup", type = "toggle", label = "« Passe prendre … en chemin »" },
    { key = "hubWarn", type = "toggle", label = "Alerte : tu quittes un coin sans prendre ses quêtes" },
    { key = "dropTips", type = "toggle", label = "Infobulle : ce monstre lâche un objet de quête" },
  },
})
ns.Tracker = M
T.Tracker = M

local floor, min, max, ceil = math.floor, math.min, math.max, math.ceil

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local r = { pcall(fn, ...) }
  if not r[1] then return nil end
  return unpack(r, 2)
end
local function now() return try(_G.GetTime) or 0 end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function low(s) return tostring(s or ""):lower() end
local function cfg(k) return not (M.db and M.db[k] == false) end
local function opt(k, def) local v = M.db and M.db[k] if v == nil then return def end return v end
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
local function fmtNum(n)
  local s = tostring(floor((n or 0) + 0.5))
  return (s:reverse():gsub("(%d%d%d)", "%1 "):reverse():gsub("^ ", ""))
end
local function playerLevel() return try(_G.UnitLevel, "player") or 1 end
local function curZone() return try(_G.GetRealZoneText) or try(_G.GetZoneText) or "" end
local function inCombat() return T.InCombat and T:InCombat() or false end
-- Forever : certaines valeurs sont « secrètes » pour les addons en combat (comparer / concaténer = erreur)
local function secret(v) local f = rawget(_G, "issecretvalue") return type(f) == "function" and f(v) and true or false end
local function shift() return _G.IsShiftKeyDown and try(_G.IsShiftKeyDown) and true or false end
local function ctrl() return _G.IsControlKeyDown and try(_G.IsControlKeyDown) and true or false end
local function alt() return _G.IsAltKeyDown and try(_G.IsAltKeyDown) and true or false end
local function shortName(n) return (tostring(n or "?"):gsub("%-.*$", "")) end

M.DEFAULT_DROP = 0.35     -- taux de drop supposé tant que rien n'est mesuré
M.DEFAULT_KILL = 25       -- secondes par monstre (tuer + dépecer + reprendre) sans mesure
M.SORTS = { "cout", "route", "distance", "niveau", "zone" }
M.FILTERS = { "tous", "zone", "pretes", "groupe", "faciles" }
local SORT_TXT = { cout = "XP/min", route = "itinéraire", distance = "distance", niveau = "niveau", zone = "zone" }
local FILTER_TXT = { tous = "toutes", zone = "cette zone", pretes = "prêtes", groupe = "groupe", faciles = "faciles" }
M.SORT_TXT, M.FILTER_TXT = SORT_TXT, FILTER_TXT
local COLOR = { gris = "|cff9d9d9d", vert = "|cff40c040", jaune = "|cffffff00", orange = "|cffff8040", rouge = "|cffff2020" }

-- ------------------------------------------------------------------ pur : formats, barres, itinéraire
function M.Bar(have, need, width)
  width = width or 8
  need = (need and need > 0) and need or 1
  local n = floor(min(1, (have or 0) / need) * width + 0.5)
  return "[" .. ("="):rep(n) .. ("-"):rep(width - n) .. "]"
end

function M.Time(sec)
  sec = tonumber(sec)
  if not sec then return "?" end
  if sec < 60 then return floor(sec + 0.5) .. " s" end
  if sec < 3600 then return floor(sec / 60 + 0.5) .. " min" end
  local h = floor(sec / 3600)
  return ("%d h %02d"):format(h, floor((sec - h * 3600) / 60))
end

function M.Rate(xpm)
  xpm = tonumber(xpm) or 0
  if xpm >= 1000 then return (("%.1fk"):format(xpm / 1000):gsub("%.", ",")) end
  return tostring(floor(xpm + 0.5))
end

-- Plus proche voisin : items = { {key, p = {mapID,x,y}} } ; dist(a, b) → yards|nil. Départ = me.
function M.Route(items, me, dist)
  local left, order = {}, {}
  for _, it in ipairs(items) do if it.p then left[#left + 1] = it end end
  local cur = me
  while #left > 0 do
    local bi, bd
    for i, it in ipairs(left) do
      local d = cur and dist(cur, it.p)
      if d and (not bd or d < bd) then bi, bd = i, d end
    end
    if not bi then break end
    local it = table.remove(left, bi)
    it.leg = bd
    order[#order + 1] = it
    cur = it.p
  end
  for _, it in ipairs(items) do if not it.p then order[#order + 1] = it end end
  for _, it in ipairs(left) do order[#order + 1] = it end  -- inaccessibles (autre continent) à la fin
  return order
end

-- distance d'un point p au segment [a, b] (en yards, via la géométrie de la boussole)
function M.DetourDist(a, b, p, vector)
  local dab = vector(a, b)
  local dap, bap = vector(a, p)
  local dbp = vector(b, p)
  if not (dab and dap and dbp) then return nil end
  if dab < 1 then return dap end
  local _, bab = vector(a, b)
  local along = dap * math.cos(bap - bab)
  if along <= 0 then return dap end
  if along >= dab then return dbp end
  return math.abs(dap * math.sin(bap - bab))
end

-- ------------------------------------------------------------------ pur : modèle de coût
-- nom de monstre depuis « Ours enragé tué » / « Gnolls tués » / « Ogre slain »
function M.MobName(objName)
  local s = tostring(objName or ""):gsub("%s+tu[ée]+e?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", "")
  return s
end

-- Estimation d'un objectif non fini. o = {name, have, need, t (m/i/o/e/r/x), finished} ; ctx = { secPerKill,
-- drops = { [nom d'objet en minuscules] = {mob, rate, measured, n} } }.
-- → { left, mobs, mob, rate, measured, sec } ; nil si fini.
function M.ObjEstimate(o, ctx)
  if type(o) ~= "table" or o.finished then return nil end
  ctx = ctx or {}
  local need = tonumber(o.need) or 1
  local left = max(0, need - (tonumber(o.have) or 0))
  if left == 0 then return nil end
  local spk = ctx.secPerKill or M.DEFAULT_KILL
  local t = o.t
  local e = { left = left, t = t }
  local drop = ctx.drops and ctx.drops[low(o.name)]
  if t == "i" or drop then   -- un objet connu comme butin prime sur le type annoncé par l'API
    local rate = drop and drop.rate or M.DEFAULT_DROP
    e.mob, e.rate, e.measured, e.n = drop and drop.mob, rate, drop and drop.measured or false, drop and drop.n
    e.mobs = ceil(left / max(0.02, rate) - 1e-9)
    e.sec = e.mobs * spk
  elseif t == "o" then
    e.sec = left * 20
  elseif t == "e" or t == "x" then
    e.sec = 90
  elseif t == "r" then
    e.sec = left * 2
  elseif t == "m" or need > 1 then
    e.mob, e.mobs = M.MobName(o.name), left
    e.sec = left * spk
  else
    e.sec = 60   -- objectif unique inconnu (« Trouver … », « Parler à … »)
  end
  return e
end

-- Difficulté (AGF + groupe) : (1 + 0,5 × max(0, Δniv)²) × seul à +3 ou plus (falaise raté/écrasement, ×2)
-- × élite (×3 seul ≈ 3× les PV, ÷ taille du groupe, au moins ×1,2) × groupe suggéré > taille du groupe (×1,8)
-- × donjon (×4 à moins de 3, ×1,5 sinon). → mult, raisons
function M.Difficulty(q, ctx)
  ctx = ctx or {}
  local pl, k = ctx.pl or playerLevel(), max(1, ctx.group or 1)
  local d, why = 1, {}
  local dl = (tonumber(q.level) or pl) - pl
  if dl > 0 then d = d * (1 + 0.5 * dl * dl) why[#why + 1] = "niv +" .. dl end
  if dl >= 3 and k == 1 then d = d * 2 why[#why + 1] = "trop haut seul" end
  if q.elite then
    d = d * max(1.2, 3 / k)
    if k == 1 then why[#why + 1] = "élite : à plusieurs" end
  end
  local sg = tonumber(q.suggestedGroup) or 0
  if sg > k then d = d * 1.8 why[#why + 1] = "groupe de " .. sg end
  if q.dungeon then
    if k >= 3 then d = d * 1.5 else d = d * 4 end
    why[#why + 1] = "donjon"
  end
  return d, why
end

-- Score d'une entrée it = { q, dist, travel, shared } → it.xpm, it.sec (temps total), it.work, it.diff, it.why
function M.Score(it, ctx)
  ctx = ctx or {}
  local q = it.q
  local travel = it.travel or (it.dist and it.dist / max(1, ctx.speed or 7)) or ((q.zone == ctx.zone) and 240 or 600)
  local work, diff, why = 0, 1, {}
  it.objs = {}
  if not q.complete then
    for _, o in ipairs(q.obj or {}) do
      local e = M.ObjEstimate(o, ctx)
      if e then work = work + e.sec it.objs[o.name or #it.objs + 1] = e end
    end
    if work == 0 and #(q.obj or {}) == 0 then work = 60 end
    diff, why = M.Difficulty(q, ctx)
    if (it.shared or 0) > 0 then work = work * 0.7 end
  end
  local xp = tonumber(q.xp) or ctx.avgXP or 0
  local sec = travel + work * diff + 15
  it.travelSec, it.work, it.diff, it.why, it.sec = travel, work, diff, why, sec
  it.xpm = xp > 0 and xp / (sec / 60) or 0
  return it.xpm
end

local function cmpKey(a, b) return tostring(a.key) < tostring(b.key) end

-- Tri. mode = cout | route | distance | niveau | zone ; prevTop = clé de la tête précédente (hystérésis 10 %).
function M.Sort(items, mode, prevTop, zone)
  local out = {}
  for i, it in ipairs(items) do out[i] = it it._route = it._route or i end
  mode = mode or "cout"
  if mode == "route" then
    table.sort(out, function(a, b) return a._route < b._route end)
  elseif mode == "distance" then
    table.sort(out, function(a, b)
      if (a.dist or 1e12) ~= (b.dist or 1e12) then return (a.dist or 1e12) < (b.dist or 1e12) end
      return cmpKey(a, b)
    end)
  elseif mode == "niveau" then
    table.sort(out, function(a, b)
      if (a.q.level or 0) ~= (b.q.level or 0) then return (a.q.level or 0) < (b.q.level or 0) end
      return (a.xpm or 0) > (b.xpm or 0)
    end)
  else
    table.sort(out, function(a, b)
      if mode == "zone" then
        local za, zb = a.q.zone == zone, b.q.zone == zone
        if za ~= zb then return za end
        if a.q.zone ~= b.q.zone then return tostring(a.q.zone) < tostring(b.q.zone) end
      end
      if (a.xpm or 0) ~= (b.xpm or 0) then return (a.xpm or 0) > (b.xpm or 0) end
      if (a.dist or 1e12) ~= (b.dist or 1e12) then return (a.dist or 1e12) < (b.dist or 1e12) end
      return cmpKey(a, b)
    end)
    if mode == "cout" and prevTop and out[1] and out[1].key ~= prevTop then
      for i, it in ipairs(out) do
        if it.key == prevTop then
          if (it.xpm or 0) * 1.1 >= (out[1].xpm or 0) then table.remove(out, i) table.insert(out, 1, it) end
          break
        end
      end
    end
  end
  for i, it in ipairs(out) do it.rank = i end
  return out
end

-- Filtre : tous | zone | pretes | groupe | faciles
function M.Pass(it, f, ctx)
  local q = it.q
  if f == "zone" then
    return q.zone == ctx.zone or (q.complete and q.turnZone == ctx.zone) or (it.dist ~= nil and it.dist < 800)
  elseif f == "pretes" then return q.complete and true or false
  elseif f == "groupe" then
    return (it.shared or 0) > 0 or q.groupTag or q.elite or (tonumber(q.suggestedGroup) or 0) > 1 or false
  elseif f == "faciles" then
    return not q.hard and q.color ~= "orange" and (it.diff or 1) < 1.6
  end
  return true
end

-- ------------------------------------------------------------------ données externes (gardées)
local function compass() return T.Compass end
local function geo() local C = compass() return C and C.Geo end
local function questieDB()
  local C = compass()
  if C and C.Questie then return C.Questie() end
  return nil
end

local function groupSize()
  local S = mod("Squad")
  local n = S and S.IsActive and try(S.IsActive, S) and try(S.Size, S) or nil
  local g = try(_G.GetNumGroupMembers) or ((try(_G.GetNumPartyMembers) or 0) + 1)
  return max(1, tonumber(n) or 0, tonumber(g) or 0)
end

-- Membres de l'escouade (amis TreeAI, Squad) sauf moi : { {name, quests = {[id] = {done, obj}} } }
function M.SquadMates()
  local S = mod("Squad")
  if not (cfg("group") and S and S.IsActive and try(S.IsActive, S)) then return {} end
  local me = try(_G.UnitName, "player")
  local list = try(S.MemberList, S) or {}
  local out = {}
  for _, m in ipairs(list) do
    if type(m) == "table" and m.name and m.name ~= me and type(m.quests) == "table" then out[#out + 1] = m end
  end
  return out
end

-- Calque groupe d'une quête : amis TreeAI (progression) + membres du groupe sans addon (IsUnitOnQuest).
function M.GroupOn(qid, mates)
  local out, seen = {}, {}
  local S = mod("Squad")
  local prog = S and S.Engine and S.Engine.Progress
  for _, m in ipairs(mates or {}) do
    local q = m.quests[qid]
    if q then
      local h, n = 0, 1
      if prog then h, n = prog(q) end
      out[#out + 1] = { name = shortName(m.name), have = h, need = n, done = q.done and true or false, stale = m.stale }
      seen[shortName(m.name)] = true
    end
  end
  local QL = _G.C_QuestLog
  if cfg("group") and QL and QL.IsUnitOnQuest then
    local n = try(_G.GetNumSubgroupMembers) or 0
    for i = 1, min(4, n) do
      local unit = "party" .. i
      local nm = try(_G.UnitName, unit)
      if nm and not secret(nm) and not seen[shortName(nm)] and try(QL.IsUnitOnQuest, unit, qid) then
        out[#out + 1] = { name = shortName(nm), noAddon = true }
        seen[shortName(nm)] = true
      end
    end
  end
  return out
end

local function groupText(mates)
  local o = {}
  for _, g in ipairs(mates or {}) do
    if g.noAddon then o[#o + 1] = g.name
    elseif g.done then o[#o + 1] = "|cff55ff55" .. g.name .. " fini|r"
    else o[#o + 1] = ("%s %d/%d"):format(g.name, g.have or 0, g.need or 1) end
  end
  return table.concat(o, ", ")
end
M.GroupText = groupText

-- Taux de drop connus pour les objets demandés : [objet] = { mob, rate, measured, n }
function M.DropInfo(list)
  local P = T.Predict
  local out = {}
  local byItem = {}
  for mob, d in pairs(M.drops or {}) do if d.item and not byItem[low(d.item)] then byItem[low(d.item)] = d.mobName or mob end end
  for _, q in ipairs(list or {}) do
    for _, o in ipairs(q.obj or {}) do
      if not o.finished then
        local k = low(o.name)
        local mob, rate, n = nil, nil, nil
        if P and P.BestSource then mob, rate, n = P.BestSource(o.name) end
        if rate then out[k] = { mob = mob, rate = rate, measured = true, n = n }
        elseif byItem[k] then
          local r2, n2 = nil, nil
          if P and P.DropRate then r2, n2 = P.DropRate(byItem[k], o.name) end
          out[k] = { mob = byItem[k], rate = r2, measured = r2 and true or false, n = n2 }
        end
      end
    end
  end
  return out
end

function M.Context()
  local P = T.Predict
  local r = P and P.Rates and try(P.Rates) or {}
  local kph = tonumber(r.killsPerHour)
  local C = compass()
  local list = (mod("Quests") or {}).lastList or {}
  local sum, n = 0, 0
  for _, q in ipairs(list) do if tonumber(q.xp) then sum, n = sum + q.xp, n + 1 end end
  local pl = playerLevel()
  return {
    pl = pl, group = groupSize(), zone = curZone(),
    speed = C and C.Speed and try(C.Speed) or 7,
    secPerKill = kph and kph > 0 and max(8, min(120, 3600 / kph)) or M.DEFAULT_KILL,
    measuredKill = kph and kph > 0 or false,
    avgXP = n > 0 and sum / n or 80 * pl,
    drops = M.DropInfo(list),
  }
end

-- ------------------------------------------------------------------ plan TreeAI (TreeAI.Planner, gardé)
-- Contrat attendu (module Planner, agent PLAN-LUA) : Planner:GetActiveSteps(n) → steps, index, total, titre
-- (étapes à partir de la courante ; step = { k, q, o, map, x, y, label, eta, who }). Repli : tables
-- Planner.active = { route = { title, steps }, idx }. Absent / pas de plan actif → rien.
local KIND = { pick = "Prendre", obj = "Faire", turn = "Rendre", fly = "Vol", hs = "Foyer", train = "Formation",
               vendor = "Marchand" }
local function planner()
  local P = rawget(T, "Planner") or mod("Planner")
  if type(P) ~= "table" then return nil end
  if P.IsEnabled and try(P.IsEnabled, P) == false then return nil end
  return P
end
function M.PlanSteps(n)
  if not cfg("plan") then return nil end
  local P = planner()
  if not P then return nil end
  n = n or 3
  local steps, idx, total, title
  local fn = P.GetActiveSteps or P.ActiveSteps
  if type(fn) == "function" then
    local ok, a, b, c, d = pcall(fn, P, n)
    if ok and type(a) == "table" then steps, idx, total, title = a, b, c, d end
  end
  if not steps then
    local act = rawget(P, "active")
    local route = type(act) == "table" and (act.route or act) or nil
    if type(route) == "table" and type(route.steps) == "table" then
      local i = tonumber(act.idx or act.step) or 1
      steps = {}
      for j = i, min(#route.steps, i + n - 1) do steps[#steps + 1] = route.steps[j] end
      idx, total, title = i, #route.steps, route.title
    end
  end
  if type(steps) ~= "table" or #steps == 0 then return nil end
  local out = {}
  for i = 1, min(n, #steps) do if type(steps[i]) == "table" then out[#out + 1] = steps[i] end end
  if #out == 0 then return nil end
  return out, tonumber(idx) or 1, tonumber(total) or #out, title
end

local function stepPoint(s)
  local x, y = tonumber(s.x), tonumber(s.y)
  if not (s.map and x and y) then return nil end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  return { mapID = s.map, x = x, y = y }
end
local function questTitle(qid)
  for _, q in ipairs((mod("Quests") or {}).lastList or {}) do if q.id == qid then return q.title end end
  local db = questieDB()
  return db and try(db.QueryQuestSingle, qid, "name") or (qid and ("#" .. qid)) or nil
end
function M.StepText(s)
  local label = s.label
  if not label or label == "" then
    local t = s.q and questTitle(s.q)
    label = (KIND[s.k] or "Étape") .. (t and (" « " .. cut(t, 30) .. " »") or "")
  end
  return label .. (s.who and (" (" .. shortName(s.who) .. ")") or "")
end

function M.PlanLines(me)
  local steps, idx, total, title = M.PlanSteps(3)
  if not steps then return nil end
  local G, C = geo(), compass()
  local units = C and C.db and C.db.units
  local out = {}
  for i, s in ipairs(steps) do
    local p = stepPoint(s)
    local d = (p and me and G) and G.Vector(me, p) or nil
    local tail = {}
    if d then tail[#tail + 1] = G.Dist(d, units) end
    if tonumber(s.eta) then tail[#tail + 1] = "~" .. M.Time(s.eta * 60) end
    local head = i == 1 and ("|cffffd100Étape %d/%d|r : "):format(idx, total) or (i == 2 and "ensuite : " or "puis : ")
    local text = head .. M.StepText(s) .. (#tail > 0 and (" — " .. table.concat(tail, " · ")) or "")
    local tip = { (title and ("Plan « " .. title .. " »")) or "Plan TreeAI" }
    if s.k then tip[#tip + 1] = KIND[s.k] or s.k end
    tip[#tip + 1] = "Clic : boussole · Maj-clic : Aller là"
    out[#out + 1] = { text = text, prio = i == 1 and 2 or 1, tooltip = tip, plan = true, pin = i == 1 or nil,
      onClick = function()
        if not (p and C) then return end
        if C.SetWaypoint then C.SetWaypoint(p.x, p.y, cut(M.StepText(s), 40), p.mapID) end
        if shift() and C.GoTo then C.GoTo({ mapID = p.mapID, x = p.x, y = p.y, label = M.StepText(s) }) end
      end }
  end
  return out
end

-- ------------------------------------------------------------------ construction de la liste
-- Quêtes du journal enrichies : point le plus proche, distance, trajet, coût, groupe ; triées et filtrées.
M.hidden = {}
function M.Build()
  local Q, C = mod("Quests"), compass()
  local list = Q and Q.lastList or {}
  local me = C and C.Player() or nil
  local vector = geo() and geo().Vector
  local ctx = M.Context()
  local mates = M.SquadMates()
  local items = {}
  for _, q in ipairs(list) do
    if not q.failed then
      local p, d
      if C then
        local pts = C.QuestPoints(q, me)
        p, d = C.Nearest(pts, me)
      end
      local it = { key = q.id, q = q, p = p and p.mapID and p or nil, dist = d or q.dist, npc = p and p.name }
      it.mates = M.GroupOn(q.id, mates)
      local sh = 0
      for _, g in ipairs(it.mates) do if not g.done then sh = sh + 1 end end
      it.shared = sh
      items[#items + 1] = it
    end
  end
  -- itinéraire (plus proche voisin) : sert au tri « route » et à /tai itineraire
  local order = vector and M.Route(items, me, function(a, b) return (vector(a, b)) end) or items
  for i, it in ipairs(order) do it._route = i end
  -- vrai temps de trajet (Shortest Path Forever, hors combat) pour les 8 plus proches à vol d'oiseau
  if C and C.Travel and me then
    local near = {}
    for _, it in ipairs(items) do if it.p and it.dist then near[#near + 1] = it end end
    table.sort(near, function(a, b) return a.dist < b.dist end)
    for i = 1, min(8, #near) do near[i].travel = C.Travel(me, near[i].p) end
  end
  for _, it in ipairs(items) do M.Score(it, ctx) end
  local sorted = M.Sort(order, opt("sort", "cout"), M.top, ctx.zone)
  M.top = sorted[1] and sorted[1].key or nil
  local shown, f = {}, opt("filter", "tous")
  for _, it in ipairs(sorted) do
    if not M.hidden[it.key] and M.Pass(it, f, ctx) then shown[#shown + 1] = it end
  end
  local total = 0
  for _, it in ipairs(sorted) do total = total + (it.sec or 0) end
  M.me, M.items, M.shown, M.ctx, M.totalSec = me, sorted, shown, ctx, total
  return sorted
end

-- En combat : on recopie seulement la progression lue par Quests (aucun calcul, aucune API lourde).
function M.LightRefresh()
  local Q = mod("Quests")
  if not (Q and Q.lastList and M.items) then return end
  local byId = {}
  for _, q in ipairs(Q.lastList) do if q.id and not secret(q.title) and not secret(q.complete) then byId[q.id] = q end end
  for _, it in ipairs(M.items) do if byId[it.key] then it.q = byId[it.key] end end
end

-- ------------------------------------------------------------------ son de fin de quête
local prevDone, primed = {}, false
function M.CheckCompletions(list)
  local newly = {}
  local seen = {}
  for _, q in ipairs(list or {}) do
    if q.id and not secret(q.complete) then
      seen[q.id] = true
      if q.complete and primed and prevDone[q.id] == false then newly[#newly + 1] = q end
      prevDone[q.id] = q.complete and true or false
    end
  end
  for id in pairs(prevDone) do if not seen[id] then prevDone[id] = nil end end
  primed = true
  if #newly > 0 and cfg("sound") then
    local SK = rawget(_G, "SOUNDKIT")
    local id = (type(SK) == "table" and SK.IG_QUEST_LIST_COMPLETE) or 878
    try(_G.PlaySound, id)
  end
  return newly
end

-- ------------------------------------------------------------------ astuces Questie
local function starterPoints(id, me)
  local db, zdb = questieDB()
  local C = compass()
  if not (db and C) then return nil end
  local sb = try(db.QueryQuestSingle, id, "startedBy")
  if type(sb) ~= "table" then return nil end
  local out = {}
  for _, npc in ipairs(type(sb[1]) == "table" and sb[1] or {}) do C.SpawnsOf("npc", npc, db, zdb, me and me.mapID, out) end
  for _, ob in ipairs(type(sb[2]) == "table" and sb[2] or {}) do C.SpawnsOf("obj", ob, db, zdb, me and me.mapID, out) end
  return out
end
M.StarterPoints = starterPoints

-- quêtes disponibles ici (Quests.QuestieScan) avec la position du donneur
function M.Available(me)
  local Q, C = mod("Quests"), compass()
  local qs = Q and Q.lastQuestie
  if not (qs and C and me) then return {} end
  local out = {}
  for _, e in ipairs(qs.here or {}) do
    local p, d = C.Nearest(starterPoints(e.id, me) or {}, me)
    if p and d then
      out[#out + 1] = { id = e.id, title = e.title, level = e.level, giver = e.giver or p.name, p = p, dist = d }
    end
  end
  table.sort(out, function(a, b) return a.dist < b.dist end)
  return out
end

-- « faciles à côté » : donneur à < 300 yd de toi ou d'un de tes objectifs, niveau ≤ le tien + 1
function M.EasyNearby(avail, items)
  local vector = geo() and geo().Vector
  local out = {}
  if not vector then return out end
  local pl = playerLevel()
  for _, a in ipairs(avail) do
    if (a.level or 0) <= pl + 1 then
      local near = a.dist <= 300
      if not near then
        for _, it in ipairs(items or {}) do
          local d = it.p and vector(it.p, a.p)
          if d and d <= 250 then near = true break end
        end
      end
      if near then out[#out + 1] = a end
    end
  end
  return out
end

-- « passe prendre » : donneur à < 120 yd du trajet vers la cible de la boussole (et pas déjà à côté)
function M.OnTheWay(avail, me, target)
  local vector = geo() and geo().Vector
  if not (vector and me and target and target.mapID) then return nil end
  local best, bd
  for _, a in ipairs(avail) do
    if a.dist > 60 then
      local d = M.DetourDist(me, target, a.p, vector)
      local total = vector(me, target)
      if d and d < 120 and total and a.dist < total and (not bd or d < bd) then best, bd = a, d end
    end
  end
  return best
end

-- Alerte de départ : on mémorise le « coin » quand tu es à < 40 yd d'un donneur dont la quête n'est pas
-- prise ; à > 200 yd de ce coin, si elles ne sont toujours pas prises → une alerte par coin.
local hub
function M.CheckHub(avail, me)
  if not (cfg("hubWarn") and me) then return nil end
  local vector = geo() and geo().Vector
  if not vector then return nil end
  local close = {}
  for _, a in ipairs(avail) do if a.dist <= 40 then close[#close + 1] = a end end
  if #close > 0 then
    hub = { p = { mapID = me.mapID, x = me.x, y = me.y }, quests = close }
    return nil
  end
  if not hub then return nil end
  local d = vector(me, hub.p)
  if d and d > 200 then
    local inLog = {}
    for _, q in ipairs((mod("Quests") or {}).lastList or {}) do inLog[q.id] = true end
    local missed = {}
    for _, a in ipairs(hub.quests) do if not inLog[a.id] then missed[#missed + 1] = a end end
    hub = nil
    if #missed > 0 then
      local key = tostring(missed[1].id)
      if not M.char.warned[key] then
        M.char.warned[key] = true
        local names = {}
        for i = 1, min(3, #missed) do names[#names + 1] = "« " .. cut(missed[i].title, 24) .. " »" .. (missed[i].giver and (" (" .. missed[i].giver .. ")") or "") end
        local text = "Tu pars sans prendre : " .. table.concat(names, ", ") .. (#missed > 3 and (" +" .. (#missed - 3)) or "")
        M:Suggest("hub", text, 2, { ttl = 90, cooldown = 600 })
        return text
      end
    end
  end
  return nil
end

-- objets demandés lâchés par des monstres : [nom du monstre en minuscules] = { item, have, need, quest, mobName }
function M.NeededDrops()
  local Q = mod("Quests")
  local db = questieDB()
  local out = {}
  if not (Q and Q.lastList) then return out end
  for _, q in ipairs(Q.lastList) do
    if not q.complete and db then
      local objs = try(db.QueryQuestSingle, q.id, "objectives")
      local items = type(objs) == "table" and objs[3]
      for _, it in ipairs(type(items) == "table" and items or {}) do
        local itemID = type(it) == "table" and it[1] or it
        if type(itemID) == "number" and db.QueryItemSingle then
          local iname = try(db.QueryItemSingle, itemID, "name") or (T.API and T.API.ItemName and T.API.ItemName(itemID))
          local have, need, done
          for _, o in ipairs(q.obj or {}) do
            if iname and low(o.name) == low(iname) then have, need, done = o.have, o.need, o.finished end
          end
          if not done then
            for _, npc in ipairs(try(db.QueryItemSingle, itemID, "npcDrops") or {}) do
              local nm = try(db.QueryNPCSingle, npc, "name")
              if nm then out[low(nm)] = { item = iname or ("objet " .. itemID), have = have, need = need, quest = q.title, mobName = nm } end
            end
          end
        end
      end
    end
  end
  M.drops = out
  return out
end

function M.DropTip(unitName)
  if not cfg("dropTips") or not unitName or secret(unitName) or inCombat() then return nil end
  local d = (M.drops or {})[low(unitName)]
  if not d then return nil end
  local P = T.Predict
  local rate = P and P.DropRate and P.DropRate(unitName, d.item)
  local prog = (d.have and d.need) and (" " .. d.have .. "/" .. d.need) or ""
  return ("TreeAI : lâche %s%s%s"):format(cut(d.item, 30), prog, rate and (" (~" .. floor(rate * 100 + 0.5) .. " %)") or "")
end

local hooked
local function hookTips()
  if hooked then return end
  hooked = true
  local function onUnit(tt)
    if tt ~= _G.GameTooltip then return end
    local name = tt.GetUnit and try(tt.GetUnit, tt)
    local ok, line = pcall(M.DropTip, name)   -- erreur ici = fenêtre d'erreur Lua dans l'infobulle Blizzard
    if not ok then line = nil end
    if line and tt.AddLine then pcall(tt.AddLine, tt, line, 1, 0.82, 0.2) end
  end
  local TDP, E = _G.TooltipDataProcessor, _G.Enum
  if TDP and TDP.AddTooltipPostCall and E and E.TooltipDataType then
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Unit, onUnit)
  elseif _G.GameTooltip and _G.GameTooltip.HookScript then
    pcall(_G.GameTooltip.HookScript, _G.GameTooltip, "OnTooltipSetUnit", onUnit)
  end
end

-- ------------------------------------------------------------------ textes
-- « encore ~16 × Lion des collines (taux 50 %) · ≈ 7 min »
function M.ObjHint(e)
  if not e then return nil end
  local parts = {}
  if e.mobs and e.mob and e.mob ~= "" then
    local approx = (e.rate and "~") or ""
    parts[#parts + 1] = ("encore %s%d × %s"):format(approx, e.mobs, cut(e.mob, 22))
    if e.rate and e.measured then parts[#parts] = parts[#parts] .. (" (%d %%)"):format(floor(e.rate * 100 + 0.5)) end
  end
  if e.sec then parts[#parts + 1] = "≈ " .. M.Time(e.sec) end
  return table.concat(parts, " · ")
end

local function chainOf(q)
  if not cfg("chain") then return nil end
  local Q = mod("Quests")
  if not (Q and Q.Chain) then return nil end
  local nexts, pos, len = Q.Chain(q.id, 3)
  if not nexts then return nil end
  return nexts, pos, len
end

local function distText(it)
  local G, C = geo(), compass()
  local units = C and C.db and C.db.units
  if it.dist and G then
    return G.Dist(it.dist, units) .. (it.travel and (" ~" .. G.ETA(it.travel * 7, 7)) or "")
  end
  local Q = mod("Quests")
  if it.q.zone and it.q.zone ~= (Q and Q.last and Q.last.zone) then return cut(it.q.zone, 20) end
  return nil
end

function M.Tooltip(it)
  local q = it.q
  local tip = { (COLOR[q.color] or "") .. q.title .. "|r" .. (q.level and (" [" .. q.level .. "]") or "") }
  if q.complete then
    tip[#tip + 1] = "|cff33ff33Prête à rendre|r" .. (it.npc and (" à " .. it.npc) or "") .. (distText(it) and (" — " .. distText(it)) or "")
  else
    for _, o in ipairs(q.obj or {}) do
      local col = o.finished and "|cff55ff55" or "|cffdddddd"
      local hint = (it.objs and it.objs[o.name]) and M.ObjHint(it.objs[o.name]) or nil
      tip[#tip + 1] = ("%s%s %s %d/%d|r%s"):format(col, M.Bar(o.have, o.need, 6), cut(o.name, 30), o.have or 0, o.need or 1,
        hint and ("  " .. hint) or "")
    end
  end
  local why = { M.Rate(it.xpm) .. " XP/min" }
  if it.travelSec then why[#why + 1] = "trajet " .. M.Time(it.travelSec) .. (it.travel and " (Shortest Path)" or "") end
  if (it.work or 0) > 0 then why[#why + 1] = "travail " .. M.Time(it.work) end
  for _, w in ipairs(it.why or {}) do why[#why + 1] = w end
  if (it.shared or 0) > 0 then why[#why + 1] = "partagée (×0,7)" end
  tip[#tip + 1] = "|cff99ccffPourquoi ce rang|r : " .. table.concat(why, ", ")
  local nexts, pos, len = chainOf(q)
  if nexts and (#nexts > 0 or (len or 0) > 1) then
    local o = {}
    for _, n in ipairs(nexts) do o[#o + 1] = "« " .. cut(n.title, 26) .. " »" .. (n.giver and (" (" .. n.giver .. ")") or "") end
    tip[#tip + 1] = ("Chaîne %d/%d%s"):format(pos or 1, max(len or 1, pos or 1), #o > 0 and (" : → " .. table.concat(o, " → ")) or "")
  end
  if #(it.mates or {}) > 0 then tip[#tip + 1] = "Groupe : " .. groupText(it.mates) end
  if cfg("story") then
    local Q = mod("Quests")
    local s = Q and Q.Story and Q.Story(q)
    if s then tip[#tip + 1] = "|cffbbbbbb« " .. s .. " »|r" end
  end
  tip[#tip + 1] = "|cff888888Clic : boussole · Maj : Aller là · Ctrl : itinéraire · Alt : masquer|r"
  return tip
end

function M.OnLineClick(qid)
  local C = compass()
  if alt() and not shift() then
    M.hidden[qid] = true
    M:Print("Masquée jusqu'au /reload (/tai suivi montrer pour tout réafficher).")
    M.Push()
    return "hidden"
  end
  local t = C and C.SetQuestTarget and C.SetQuestTarget(qid)
  if t and shift() and C.GoTo then C.GoTo(t) return "goto" end
  if ctrl() and not shift() then M.SendRoute() return "route" end
  return t and "target" or nil
end

function M.QuestText(it)
  local q = it.q
  local Q = mod("Quests")
  local parts, head = {}, nil
  if q.complete then
    head = ("|cff33ff33Prête à rendre|r %s"):format(cut(q.title, 30))
    local nexts = chainOf(q)
    if nexts and nexts[1] then
      parts[#parts + 1] = "→ suite « " .. cut(nexts[1].title, 26) .. " »" .. (nexts[2] and (" → « " .. cut(nexts[2].title, 18) .. " »") or "")
    elseif Q and Q.QuestieNext then
      local nx = try(Q.QuestieNext, q.id)
      if nx then parts[#parts + 1] = "→ " .. nx:gsub("^Suite : ", "suite ") end
    end
    if it.npc then parts[#parts + 1] = it.npc end
  else
    head = ("%s[%d]|r %s"):format(COLOR[q.color] or "", q.level or 0, cut(q.title, 30))
    local o = {}
    for _, ob in ipairs(q.obj or {}) do
      if not ob.finished then
        local e = it.objs and it.objs[ob.name]
        local s = M.Bar(ob.have, ob.need, 5) .. " " .. (ob.have or 0) .. "/" .. (ob.need or 1)
        if e and e.mobs and e.mob and e.rate then s = s .. (" encore ~%d × %s"):format(e.mobs, cut(e.mob, 18)) end
        o[#o + 1] = s
      end
      if #o >= 2 then break end
    end
    if #o > 0 then parts[#parts + 1] = table.concat(o, " ") end
    if (it.work or 0) > 0 then parts[#parts + 1] = "≈ " .. M.Time(it.work * (it.diff or 1)) end
  end
  local dt = distText(it)
  if dt then parts[#parts + 1] = dt end
  if q.xp then parts[#parts + 1] = fmtNum(q.xp) .. " XP" end
  if (it.xpm or 0) > 0 then parts[#parts + 1] = M.Rate(it.xpm) .. "/min" end
  if #(it.mates or {}) > 0 then parts[#parts + 1] = "|cff88ccff" .. groupText(it.mates) .. "|r" end
  return it.rank .. ". " .. head .. (#parts > 0 and (" · " .. table.concat(parts, " · ")) or "")
end

function M.HeaderText()
  local n, ready = #(M.shown or {}), 0
  for _, it in ipairs(M.items or {}) do if it.q.complete then ready = ready + 1 end end
  local s = ("|cffffd100Suivi|r · tri %s · %s (%d)"):format(SORT_TXT[opt("sort", "cout")] or "?", FILTER_TXT[opt("filter", "tous")] or "?", n)
  if ready > 0 then s = s .. (" · |cff33ff33%d prête%s|r"):format(ready, ready > 1 and "s" or "") end
  if (M.totalSec or 0) > 0 then s = s .. " · ≈ " .. M.Time(M.totalSec) .. " pour tout" end
  return s
end

function M.CycleFilter(step)
  local f, idx = opt("filter", "tous"), 1
  for i, v in ipairs(M.FILTERS) do if v == f then idx = i end end
  M.db.filter = M.FILTERS[(idx - 1 + (step or 1)) % #M.FILTERS + 1]
  M.Update(true)
  return M.db.filter
end
function M.CycleSort(step)
  local f, idx = opt("sort", "cout"), 1
  for i, v in ipairs(M.SORTS) do if v == f then idx = i end end
  M.db.sort = M.SORTS[(idx - 1 + (step or 1)) % #M.SORTS + 1]
  M.top = nil
  M.Update(true)
  return M.db.sort
end

-- Lignes de l'onglet « Quêtes » (plates : texte + infobulle + clic).
function M.Lines()
  local Q = mod("Quests")
  local lines = {}
  local planLines = M.PlanLines(M.me)
  lines[#lines + 1] = { text = M.HeaderText(), prio = 1, header = true,
    tooltip = { "Clic : changer de filtre (toutes / zone / prêtes / groupe / faciles)", "Maj-clic : changer de tri",
      "Tri XP/min = XP ÷ (trajet + travail × difficulté)" },
    onClick = function() if shift() then M.CycleSort(1) else M.CycleFilter(1) end end }
  if planLines then
    for _, l in ipairs(planLines) do lines[#lines + 1] = l end
  elseif not T.SetTabLines and Q and Q.nextText then
    lines[#lines + 1] = { text = Q.nextText, prio = 2 }
  end
  local maxQ = tonumber(opt("maxQuests", 10)) or 10
  local nq = 0
  for _, it in ipairs(M.shown or {}) do
    if nq >= maxQ or #lines >= (tonumber(M.db.maxLines) or 12) + (planLines and #planLines or 0) + 1 then break end
    nq = nq + 1
    local qid = it.q.id
    lines[#lines + 1] = { text = M.QuestText(it), prio = it.q.complete and 2 or 1, tooltip = M.Tooltip(it),
      onClick = function() M.OnLineClick(qid) end, questID = qid }
  end
  local G, C = geo(), compass()
  local units = C and C.db and C.db.units
  for _, a in ipairs(M.easy or {}) do
    lines[#lines + 1] = { text = ("|cff88ccffFacile à côté|r : « %s » (%s, %s)"):format(cut(a.title, 28), a.giver or "?",
      G and G.Dist(a.dist, units) or "?"), prio = 1 }
  end
  if M.pickup then
    lines[#lines + 1] = { text = ("Passe prendre « %s » chez %s en chemin"):format(cut(M.pickup.title, 28), M.pickup.giver or "?"), prio = 2 }
  end
  if nq == 0 then
    lines[#lines + 1] = { text = (#(M.items or {}) == 0) and "Aucune quête dans le journal."
      or ("Aucune quête pour le filtre « " .. (FILTER_TXT[opt("filter", "tous")] or "?") .. " » (clic sur l'en-tête)."), prio = 1 }
  end
  return lines
end

-- Lignes du cadre compact : en-tête, plan, quêtes (+ objectifs dépliés, barre réelle), groupe, astuces.
function M.FrameRows()
  local rows = {}
  local lines = M.Lines()
  local collapsed = M.char.collapsed or {}
  for _, l in ipairs(lines) do
    if l.questID then
      local it
      for _, x in ipairs(M.shown or {}) do if x.q.id == l.questID then it = x break end end
      local fold = M.char.allCollapsed or collapsed[tostring(l.questID)]
      local q = it and it.q
      local title = it and ((fold and "+ " or "- ") .. it.rank .. ". " .. (q.complete and "|cff33ff33" or (COLOR[q.color] or "")) ..
        cut(q.title, 34) .. "|r" .. (it.xpm and it.xpm > 0 and (" |cff99ccff" .. M.Rate(it.xpm) .. "/min|r") or "")) or l.text
      rows[#rows + 1] = { kind = "quest", text = title, line = l, qid = l.questID }
      if it and not fold then
        if q.complete then
          rows[#rows + 1] = { kind = "obj", indent = 12, line = l, qid = l.questID,
            text = "Rendre" .. (it.npc and (" à " .. it.npc) or "") .. (distText(it) and (" · " .. distText(it)) or "") }
        else
          for _, o in ipairs(q.obj or {}) do
            if not o.finished then
              local hint = it.objs and M.ObjHint(it.objs[o.name])
              rows[#rows + 1] = { kind = "obj", indent = 12, line = l, qid = l.questID, bar = { o.have or 0, o.need or 1 },
                text = ("%s %d/%d%s"):format(cut(o.name, 26), o.have or 0, o.need or 1, (hint and hint ~= "") and (" · " .. hint) or "") }
            end
          end
          local dt = distText(it)
          if dt then rows[#rows + 1] = { kind = "obj", indent = 12, line = l, qid = l.questID, text = "|cffaaaaaa" .. dt .. "|r" } end
        end
        if #(it.mates or {}) > 0 then
          rows[#rows + 1] = { kind = "obj", indent = 12, line = l, qid = l.questID, text = "|cff88ccff" .. groupText(it.mates) .. "|r" }
        end
      end
    else
      rows[#rows + 1] = { kind = l.header and "header" or "hint", text = l.text, line = l }
    end
  end
  return rows
end

-- ------------------------------------------------------------------ cadre compact (à nous)
local frame, pool = nil, {}
local function fontPath()
  local f = _G.GameFontHighlightSmall
  local p = f and f.GetFont and try(f.GetFont, f)
  return p or "Fonts\\FRIZQT__.TTF"
end
local function savePos(f)
  local p, _, _, x, y = f:GetPoint(1)
  M.char.point, M.char.x, M.char.y = p or "RIGHT", x or -40, y or 120
end
local function createFrame()
  if frame or type(_G.CreateFrame) ~= "function" then return frame end
  frame = _G.CreateFrame("Frame", "TreeAITrackerFrame", _G.UIParent)
  frame:SetSize(opt("width", 300), 20)
  frame:SetPoint(M.char.point or "RIGHT", _G.UIParent, M.char.point or "RIGHT", M.char.x or -40, M.char.y or 120)
  frame:SetMovable(true)
  frame:EnableMouse(true)
  frame:SetClampedToScreen(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", function(f) if not M.db.lock then f:StartMoving() end end)
  frame:SetScript("OnDragStop", function(f) f:StopMovingOrSizing() savePos(f) end)
  frame.bg = frame:CreateTexture(nil, "BACKGROUND")
  frame.bg:SetAllPoints(frame)
  frame:Hide()
  return frame
end

local function rowWidget(i)
  if pool[i] then return pool[i] end
  local b = _G.CreateFrame("Button", nil, frame)
  b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  b:RegisterForDrag("LeftButton")
  b:SetScript("OnDragStart", function() if not M.db.lock then frame:StartMoving() end end)
  b:SetScript("OnDragStop", function() frame:StopMovingOrSizing() savePos(frame) end)
  b.fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.fs:SetPoint("LEFT", b, "LEFT", 0, 0)
  if b.fs.SetJustifyH then b.fs:SetJustifyH("LEFT") end
  if b.fs.SetWordWrap then b.fs:SetWordWrap(false) end
  b.bar = _G.CreateFrame("StatusBar", nil, b)
  b.bar:SetPoint("LEFT", b, "LEFT", 0, 0)
  if b.bar.SetStatusBarTexture then b.bar:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar") end
  b.bar:SetMinMaxValues(0, 1)
  b.bar:Hide()
  b:SetScript("OnClick", function(self, button)
    local r = self.row
    if not r then return end
    if button == "RightButton" then
      if r.qid then M.ToggleCollapse(r.qid) elseif r.kind == "header" then M.ToggleCollapse(nil) end
      return
    end
    if r.line and r.line.onClick then pcall(r.line.onClick, r.line) end
  end)
  b:SetScript("OnEnter", function(self)
    local tt = _G.GameTooltip
    local tip = self.row and self.row.line and self.row.line.tooltip
    if not (tt and tip) then return end
    pcall(tt.SetOwner, tt, self, "ANCHOR_LEFT")
    if type(tip) == "table" then
      for _, l in ipairs(tip) do pcall(tt.AddLine, tt, tostring(l), 1, 1, 1, true) end
    else
      for l in tostring(tip):gmatch("[^\n]+") do pcall(tt.AddLine, tt, l, 1, 1, 1, true) end
    end
    if self.row.qid then pcall(tt.AddLine, tt, "|cff888888Clic droit : réduire / déplier|r", 1, 1, 1) end
    pcall(tt.Show, tt)
  end)
  b:SetScript("OnLeave", function() if _G.GameTooltip then pcall(_G.GameTooltip.Hide, _G.GameTooltip) end end)
  pool[i] = b
  return b
end

function M.ToggleCollapse(qid)
  if qid == nil then
    M.char.allCollapsed = not M.char.allCollapsed
  else
    local k = tostring(qid)
    M.char.collapsed[k] = (not M.char.collapsed[k]) or nil
  end
  M.Push()
  return qid == nil and M.char.allCollapsed or M.char.collapsed[tostring(qid)] or false
end

function M.RenderFrame()
  createFrame()
  if not frame then return nil end
  local rows = M.FrameRows()
  local size = tonumber(opt("fontSize", 11)) or 11
  local w = tonumber(opt("width", 300)) or 300
  local h = size + 4
  local font = fontPath()
  frame:SetWidth(w)
  frame:SetScale((tonumber(opt("scale", 100)) or 100) / 100)
  local a = (tonumber(opt("alpha", 35)) or 35) / 100
  if frame.bg.SetColorTexture then frame.bg:SetColorTexture(0, 0, 0, a) else frame.bg:SetAlpha(a) end
  local n = min(#rows, 40)
  local y = -3
  for i = 1, n do
    local r, b = rows[i], rowWidget(i)
    b.row = r
    b:ClearAllPoints()
    b:SetPoint("TOPLEFT", frame, "TOPLEFT", 4 + (r.indent or 0), y)
    b:SetSize(w - 8 - (r.indent or 0), h)
    pcall(b.fs.SetFont, b.fs, font, r.kind == "header" and size + 1 or (r.kind == "obj" and size - 1 or size), "")
    local textX = 0
    if r.bar then
      b.bar:SetSize(46, max(4, h - 6))
      b.bar:SetMinMaxValues(0, max(1, r.bar[2]))
      b.bar:SetValue(min(r.bar[1], r.bar[2]))
      if b.bar.SetStatusBarColor then
        local done = r.bar[1] >= r.bar[2]
        b.bar:SetStatusBarColor(done and 0.3 or 1, done and 1 or 0.8, 0.2)
      end
      b.bar:Show()
      textX = 50
    else
      b.bar:Hide()
    end
    b.fs:ClearAllPoints()
    b.fs:SetPoint("LEFT", b, "LEFT", textX, 0)
    b.fs:SetWidth(w - 8 - (r.indent or 0) - textX)
    b.fs:SetText(r.text)
    b:Show()
    y = y - h
  end
  for i = n + 1, #pool do pool[i]:Hide() pool[i].row = nil end
  frame:SetHeight(-y + 3)
  frame:Show()
  M.frameRows = rows
  return n
end

local lastPush, pushPending = 0, false
function M.Push()
  local lines = M.Lines()
  M.lines = lines
  local mode
  if T.SetTabLines then
    pcall(T.SetTabLines, T, "quests", lines)
    mode = "tab"
  end
  local wantFrame = cfg("enabled") and (M.db.standalone or (not T.SetTabLines and cfg("frame")))
  if wantFrame then
    if M.RenderFrame() then mode = mode and "tab+frame" or "frame" end
  elseif frame then
    frame:Hide()
  end
  return mode or "off"
end

local function pushSoon()
  if pushPending then return end
  local wait = max(0, 0.5 - (now() - lastPush))
  pushPending = true
  T:After(wait, function()
    pushPending = false
    lastPush = now()
    pcall(M.Push)
  end)
end
M.PushSoon = pushSoon

-- ------------------------------------------------------------------ mise à jour
local lastUpdate = -10
function M.Update(force)
  -- planification lourde hors combat seulement ; refaite en sortie de combat
  if inCombat() then M.dirty = true return M.items end
  lastUpdate = now()
  M.dirty = nil
  M.NeededDrops()
  local order = M.Build()
  local me = M.me
  local avail = M.Available(me)
  M.avail = avail
  M.easy = cfg("easy") and M.EasyNearby(avail, order) or {}
  local C = compass()
  local target = C and C.Current and C.Current()
  M.pickup = cfg("pickup") and M.OnTheWay(avail, me, target) or nil
  if M.pickup then
    M:Suggest("pickup", ("Passe prendre « %s » chez %s en chemin."):format(cut(M.pickup.title, 28), M.pickup.giver or "?"), 2,
      { ttl = 120, cooldown = 900 })
  end
  if #M.easy > 0 then
    local a = M.easy[1]
    M:Suggest("easy", ("Facile à côté : « %s » (%s)%s"):format(cut(a.title, 28), a.giver or "?",
      #M.easy > 1 and (" +" .. (#M.easy - 1)) or ""), 1, { ttl = 120, cooldown = 1200, sound = false })
  end
  M.CheckHub(avail, me)
  M.WriteSummary()
  pushSoon()
  return order
end

function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local route = {}
  for i, it in ipairs(M.items or {}) do
    if i > 15 then break end
    route[#route + 1] = { id = it.q.id, title = cut(it.q.title, 60), ready = it.q.complete or nil,
      dist = it.dist and floor(it.dist + 0.5) or nil, xp = it.q.xp, zone = it.q.zone,
      done = it.q.done, total = it.q.total, xpm = it.xpm and floor(it.xpm + 0.5) or nil,
      sec = it.sec and floor(it.sec + 0.5) or nil, mates = (it.mates and #it.mates > 0) and #it.mates or nil }
  end
  local easy = {}
  for i, a in ipairs(M.easy or {}) do
    if i > 5 then break end
    easy[#easy + 1] = { id = a.id, title = cut(a.title, 60), giver = a.giver, dist = floor(a.dist + 0.5) }
  end
  local nd = 0
  for _ in pairs(M.drops or {}) do nd = nd + 1 end
  s.tracker = {
    updated = try(_G.time), route = route, easyNearby = easy, dropMobs = nd,
    pickup = M.pickup and { id = M.pickup.id, title = cut(M.pickup.title, 60), giver = M.pickup.giver } or nil,
    available = #(M.avail or {}), sort = opt("sort", "cout"), filter = opt("filter", "tous"),
    totalMin = M.totalSec and floor(M.totalSec / 60 + 0.5) or nil, plan = M.PlanSteps(1) and true or nil,
  }
end

local pending
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 1, function() pending = false pcall(M.Update, true) end)
end

local function onQuests(list)
  pcall(M.CheckCompletions, list or (mod("Quests") or {}).lastList)
  if inCombat() then
    M.dirty = true
    pcall(M.LightRefresh)
    pushSoon()
  else
    M.Refresh(1)
  end
end

function M:OnEnable()
  if T.RegisterTab then pcall(T.RegisterTab, T, "quests", "Quêtes") end
  for _, ev in ipairs({ "QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_TURNED_IN", "ZONE_CHANGED_NEW_AREA", "PLAYER_LEVEL_UP" }) do
    self:RegisterEvent(ev, function() M.Refresh(1.2) end)
  end
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  self:On("QUESTS_UPDATED", function(_, _, list) onQuests(type(list) == "table" and list or nil) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() if M.dirty then M.Refresh(1.5) end end)
  hookTips()
  -- le cœur appelle :AutoRefresh() (StartAutoRefresh) ; sinon notre propre minuterie
  if not T.StartAutoRefresh and not M.ticker then M.ticker = T:NewTicker(4, function() pcall(M.Update) end) end
  M.Refresh(2.5)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
  if frame then frame:Hide() end
end

function M:OnOptionChanged() M.Refresh(0.2) end

-- actualisation automatique (Core:StartAutoRefresh) : hors combat, recalcul complet ; en combat, différé
function M:AutoRefresh()
  if inCombat() then M.dirty = true return end
  if M.items and now() - lastUpdate < 1.5 then return end
  pcall(M.Update)
end

-- Itinéraire complet confié à Shortest Path Forever (NavigateRoute, ≤ 64 étapes) — sur commande du joueur.
function M.RouteStops()
  local stops = {}
  for _, it in ipairs(M.items or {}) do
    if it.p and #stops < 64 and not M.hidden[it.key] then
      local q = it.q
      stops[#stops + 1] = { map = it.p.mapID, x = it.p.x, y = it.p.y, questID = q.id,
        title = cut((q.complete and "Rendre « " .. q.title .. " »") or q.title, 60),
        tooltip = q.complete and "Prête à rendre" or (mod("Quests") and mod("Quests").Remaining and mod("Quests").Remaining(q, 3)) or nil }
    end
  end
  return stops
end
function M.SendRoute()
  if not M.items then M.Update(true) end
  local stops = M.RouteStops()
  local C = compass()
  if #stops == 0 then M:Print("Itinéraire : aucun point connu (Questie / API de quêtes).") return nil end
  local A = C and C.SPF and C.SPF()
  if A and not inCombat() and C.SPFCall(A, "NavigateRoute", "TreeAI", stops) then
    M:Print(("Itinéraire (%d étapes) envoyé à Shortest Path."):format(#stops))
    return "spf"
  end
  if C and C.GoTo and #M.items > 0 then
    C.SetQuestTarget(M.items[1].q.id)
    return C.GoTo()
  end
end
T:RegisterSlash("itineraire", function() M.SendRoute() end, "itinéraire conseillé → Shortest Path (ou boussole)")
T:RegisterSlash("itinéraire", function() M.SendRoute() end)

local ALIAS = { ["prêtes"] = "pretes", pret = "pretes", prete = "pretes", ["prête"] = "pretes", tout = "tous",
  toutes = "tous", facile = "faciles", cout = "cout", ["coût"] = "cout", xp = "cout", itineraire = "route",
  ["itinéraire"] = "route", niv = "niveau" }
local function inList(v, list) for _, x in ipairs(list) do if x == v then return true end end return false end

T:RegisterSlash("suivi", function(rest)
  local cmd, arg = low(rest):match("^%s*(%S*)%s*(.-)%s*$")
  arg = ALIAS[arg] or arg
  if cmd:find("^cach") or cmd == "off" then
    M.db.frame, M.db.standalone = false, false
    if frame then frame:Hide() end
    return
  elseif cmd == "cadre" or cmd == "on" then
    M.db.standalone = not M.db.standalone
    if cmd == "on" then M.db.standalone = true end
    M.Update(true) M.Push()
    M:Print("Cadre de suivi : " .. (M.db.standalone and "affiché" or "caché") .. ".")
    return
  elseif cmd == "filtre" then
    if inList(arg, M.FILTERS) then M.db.filter = arg M.Update(true) else M.CycleFilter(1) end
    M.Push()
    M:Print("Filtre : " .. FILTER_TXT[M.db.filter] .. " (tous, zone, pretes, groupe, faciles).")
    return
  elseif cmd == "tri" then
    if inList(arg, M.SORTS) then M.db.sort = arg M.top = nil M.Update(true) else M.CycleSort(1) end
    M.Push()
    M:Print("Tri : " .. SORT_TXT[M.db.sort] .. " (cout, route, distance, niveau, zone).")
    return
  elseif cmd == "police" then
    local n = tonumber(arg)
    if n then M.db.fontSize = max(8, min(16, floor(n))) end
    M.Push()
    return
  elseif cmd == "verrou" then
    M.db.lock = not M.db.lock
    M:Print("Cadre " .. (M.db.lock and "verrouillé" or "déverrouillé") .. ".")
    return
  elseif cmd == "masquer" then
    local id = tonumber(arg)
    if id then M.hidden[id] = true M.Update(true) M.Push() end
    return
  elseif cmd == "montrer" then
    M.hidden = {}
    M.Update(true) M.Push()
    return
  elseif cmd == "reduire" or cmd == "réduire" then
    M.ToggleCollapse(nil)
    return
  end
  M.Update(true)
  M.Push()
  for i, l in ipairs(M.lines or {}) do
    if i > 16 then break end
    M:Print(l.text)
  end
end, "suivi des quêtes : /tai suivi [cadre|filtre X|tri X|police N|verrou|masquer id|montrer|reduire|cacher]")
