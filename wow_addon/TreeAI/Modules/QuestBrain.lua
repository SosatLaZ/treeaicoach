-- TreeAI — Cerveau des quêtes (agent QUESTS, 4.1). « Dis-moi quelle quête prendre, et rends mes quêtes au plus vite. »
-- Quatre modes, UN seul actif, choisis d'un clic (bande de boutons, page Quêtes, ligne « Mode » du trajet, /tai rendre…) :
--  * « Rendre mes quêtes » (turnin) : seulement les quêtes FINIES du journal, toutes zones, dans l'ordre le plus rapide
--    (vols connus, bateaux / zeppelins, pierre de foyer quand elle fait gagner du temps — Route.lua compte les trajets).
--  * « Quêtes à prendre » (pickup) : les quêtes disponibles classées par XP par minute RÉELLE (XP de QuestieDB, objectifs
--    au temps du modèle de coût du Planner, trajets Route / NavTime / PathLearn, taille du groupe, écart de niveau, suite
--    de chaîne ouverte) ; jamais grise, jamais élite en solo, donjon seulement à 4 et si tu l'as coché ; chacune dit pourquoi.
--  * « Tout faire ici » (zone) : le trajet de la zone de travail (ZonePlan), comme avant mais juste.
--  * « Avec le groupe » (group) : les quêtes que le groupe a en commun d'abord, puis celles à partager.
-- Le trajet (ZonePlan:BuildRoute → Planner.steps → Route) suit le mode actif ; QUEST_MODE_CHANGED est envoyé à chaque
-- changement. Tout est calculé à la demande et mémorisé jusqu'au prochain évènement de quête / groupe / export ou un
-- déplacement de plus de 60 yd : aucun OnUpdate, rien dans le chat, aucune action protégée. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.QuestBrain = true end   -- guidage de base : allumé en mode sûr
local QB = T:RegisterModule("QuestBrain", {
  title = "Cerveau des quêtes",
  description = "Quatre modes d'un clic : rendre mes quêtes, quêtes à prendre, tout faire ici, avec le groupe.",
  defaults = { enabled = true, autoBack = true, pickRoute = 6 },
  charDefaults = { mode = "zone", zpSort = "moi", zpNoElite = false },
  options = {
    { key = "autoBack", type = "toggle", label = "Revenir à « Tout faire ici » quand il n'y a plus rien à rendre / à prendre" },
    { key = "pickRoute", type = "range", label = "Quêtes à prendre mises dans le trajet", min = 2, max = 10, step = 1 },
  },
})
T.QuestBrain = QB

local floor, max, min, huge = math.floor, math.max, math.min, math.huge
local tsort, tconcat, fmt = table.sort, table.concat, string.format
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return tryPass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return tonumber(try(G("GetTime"))) or 0 end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8, jamais une lettre accentuée coupée
local function myLevel() return tonumber(_ns((try(G("UnitLevel"), "player")))) or 1 end
local function groupSize() return max(1, tonumber(_ns((try(G("GetNumGroupMembers"))))) or 0) end
local function thousands(n)
  local s = tostring(floor((n or 0) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^%s+", ""))
end

-- ===================================================================== modes
-- binding = action de raccourci (Bindings / Binds.lua, agent STRIP) ; key = la touche liée (GetBindingKey), si elle existe
QB.MODES = {
  { id = "turnin", label = "Rendre mes quêtes", short = "Rendre", binding = "TREEAI_QB_TURNIN",
    tip = "Seulement tes quêtes finies, toutes zones, dans l'ordre le plus rapide (vols, bateaux, pierre de foyer comptés)." },
  { id = "pickup", label = "Quêtes à prendre", short = "Prendre", binding = "TREEAI_QB_PICKUP",
    tip = "Les meilleures quêtes à prendre près de toi, classées par XP par minute (trajet et objectifs compris), avec la raison." },
  { id = "zone", label = "Tout faire ici", short = "Zone", binding = "TREEAI_QB_ZONE",
    tip = "Le trajet de ta zone de travail : prendre, faire et rendre dans le meilleur ordre." },
  { id = "group", label = "Avec le groupe", short = "Groupe", binding = "TREEAI_QB_GROUP",
    tip = "D'abord les quêtes que ton groupe a aussi, puis celles à partager ; les élites quand vous êtes 3 ou plus." },
}
local BY_ID = {}
for i, m in ipairs(QB.MODES) do BY_ID[m.id] = i end
QB.DEFAULT = "zone"

function QB.Mode()
  local m = QB.char and QB.char.mode
  if not (m and BY_ID[m]) then return QB.DEFAULT end
  if QB.IsEnabled and not QB:IsEnabled() then return QB.DEFAULT end
  return m
end
function QB.ModeInfo(id) local i = BY_ID[id or QB.Mode()] return i and QB.MODES[i] or nil end
function QB.Label(id) local m = QB.ModeInfo(id) return m and m.label or nil end

-- ===================================================================== mémo (évènementiel)
-- gen avance à chaque évènement qui change les quêtes / le groupe / l'export ; la position compte par pas de 60 yd
QB.gen = 0
QB.MOVE_YD = 60
local anchor                                       -- { map, x, y } : la position des calculs (hystérésis 60 yd)
local function Geo() return T.Geo or {} end
local function here()
  local g = Geo()
  local m, x, y
  if g.Player then m, x, y = g.Player() end
  if not (m and x) then return anchor end
  if anchor and g.Dist then
    local d = g.Dist(anchor.map, anchor.x, anchor.y, m, x, y)
    if d and d <= QB.MOVE_YD then return anchor end
  end
  anchor = { map = m, x = x, y = y }
  return anchor
end
function QB.Bump() QB.gen = QB.gen + 1 end

-- ===================================================================== temps de trajet (secondes)
-- PathLearn (agent PATHS : durées apprises sur tes trajets) d'abord, puis Route (vols / bateaux), puis NavTime (vitesse
-- mesurée × détour appris), puis la marche à 7 yd/s × 1,3
local function legSec(a, b)
  if not (a and b and a.map and a.x and b.map and b.x) then return nil end
  local PL = T.PathLearn
  if type(PL) == "table" and type(PL.LegSec) == "function" then
    local s = tonumber((try(PL.LegSec, a, b)))
    if s and s >= 0 and s < huge then return s end
  end
  local R = T.Route
  if R and R.LegSeconds and R.IsEnabled and R:IsEnabled() then
    local s = tonumber((try(R.LegSeconds, a, b)))
    if s then return s end
  end
  local g = Geo()
  local d = g.Dist and g.Dist(a.map, a.x, a.y, b.map, b.x, b.y)
  if not d then return nil end
  local NT = T.NavTime
  if NT and NT.Model then
    local s = tonumber((try(NT.Model, d, b.map)))
    if s then return s end
  end
  return d * 1.3 / 7
end
QB.LegSec = legSec
local function distYd(a, b)
  local g = Geo()
  if not (a and b and a.x and b.x and g.Dist) then return nil end
  return g.Dist(a.map, a.x, a.y, b.map, b.x, b.y)
end

-- ===================================================================== données
local function X() return T.Export end
local function exportData() local Xm = X() return Xm and Xm.Data and Xm:Data() or nil end
local function logSnap() local Xm = X() return (Xm and Xm.LogSnapshot and Xm.LogSnapshot()) or {} end
local function recsOf(exp)
  local Z = T.ZonePlan
  if Z and Z.Recs then return Z.Recs(exp) end
  local t = {}
  for _, r in ipairs(exp and exp.quests or {}) do t[r.id] = r end
  return t
end
local function isDone(q)
  local P = T.Planner
  if P and P.char and P.char.turned and P.char.turned[q] then return true end
  local Xm = X()
  if Xm and Xm.IsQuestDone then return Xm.IsQuestDone(q) and true or false end
  local f = C("C_QuestLog", "IsQuestFlaggedCompleted") or G("IsQuestFlaggedCompleted")
  return f and try(f, q) and true or false
end
local function engine() local P = T.Planner return P and P.Engine or nil end
local function instance(m) local Z = T.ZonePlan return Z and Z.IsInstance and Z.IsInstance(m) or false end
local function questName(q)
  local Xm = X()
  local db = Xm and Xm.Source and try(Xm.Source, Xm)
  local nm = db and db.Q and db.Q(q, "name")
  return type(nm) == "string" and nm ~= "" and nm or nil
end
local function partyOn(q)
  local Z = T.ZonePlan
  if Z and Z.PartyOn then return tonumber((try(Z.PartyOn, q))) or 0 end
  return 0
end
QB.PartyOn = partyOn
-- XP réelle d'une quête du journal (récompense du jeu), sinon QuestieDB, sinon l'estimation
local function rewardXP(q, rec, lv, level)
  local E = engine()
  local x = tonumber(_ns((try(G("GetQuestLogRewardXP"), q))))
  if x and x > 0 then return floor(x) end
  local base = (rec and tonumber(rec.xp) and rec.xp > 0 and rec.xp) or (E and E.QuestXPEstimate(lv or level)) or 0
  local f = E and E.QuestXPFactor(level, lv or level) or 1
  return floor(base * f + 0.5)
end
QB.RewardXP = rewardXP

-- ===================================================================== appris en jouant (sans Questie)
-- (QUESTS 4.1) « réduis la dépendance aux autres addons » : chaque PNJ à qui tu parles est retenu avec SES quêtes — celles
-- qu'il propose (GOSSIP_SHOW : C_GossipInfo.GetAvailableQuests ; QUEST_GREETING : GetAvailableQuestInfo ; QUEST_DETAIL) et
-- celles qu'il reprend (GOSSIP_SHOW : quêtes actives ; QUEST_PROGRESS / QUEST_COMPLETE) — à TA position à ce moment, avec
-- son nom donné par le jeu (en français). Compte (les PNJ sont les mêmes pour tous tes personnages), 600 entrées au plus.
QB.LEARN_MAX = 600
local function learned()
  local db = QB.db
  if type(db) ~= "table" then return nil end
  local L = db.learned
  if type(L) ~= "table" or L.v ~= 1 then L = { v = 1, give = {}, fin = {} }; db.learned = L end
  return L
end
QB.Learned = learned
local function epoch() return tonumber(try(G("time"))) or floor(now()) end
local function prune(t)
  local n, oldest, ok = 0, nil, nil
  for k, e in pairs(t) do
    n = n + 1
    if not oldest or (e.at or 0) < oldest then oldest, ok = e.at or 0, k end
  end
  if n > QB.LEARN_MAX and ok then t[ok] = nil end
end
local function npcHere()
  local g = Geo()
  local m, x, y
  if g.Player then m, x, y = g.Player() end
  if not (m and x) then return nil end
  local name = _ns((try(G("UnitName"), "npc")))
  if type(name) ~= "string" or name == "" then name = nil end
  return { m = m, x = floor(x * 10 + 0.5) / 10, y = floor(y * 10 + 0.5) / 10, n = name, at = epoch() }
end
local function note(kind, q, extra)
  q = tonumber(_ns(q))
  local L = learned()
  if not (L and q and q > 0) then return end
  local e = npcHere()
  if not e then return end
  for k, v in pairs(extra or {}) do e[k] = v end
  L[kind][q] = e
  prune(L[kind])
  QB.Bump()
end
QB.Note = note
function QB.LearnGossip()
  local GI = G("C_GossipInfo")
  if type(GI) ~= "table" then return end
  for _, a in ipairs((GI.GetAvailableQuests and try(GI.GetAvailableQuests)) or {}) do
    if type(a) == "table" then
      note("give", a.questID, { t = _ns(a.title), lv = tonumber(_ns(a.questLevel)), triv = _ns(a.isTrivial) and true or nil,
        rep = _ns(a.repeatable) and true or nil })
    end
  end
  for _, a in ipairs((GI.GetActiveQuests and try(GI.GetActiveQuests)) or {}) do
    if type(a) == "table" then note("fin", a.questID, { t = _ns(a.title) }) end
  end
end
function QB.LearnGreeting()
  local nA = tonumber(_ns((try(G("GetNumAvailableQuests"))))) or 0
  for i = 1, min(nA, 12) do
    local _, _, rep, _, qid = try(G("GetAvailableQuestInfo"), i)
    if tonumber(_ns(qid)) then
      note("give", qid, { t = _ns((try(G("GetAvailableTitle"), i))), lv = tonumber(_ns((try(G("GetAvailableLevel"), i)))),
        rep = _ns(rep) and true or nil })
    end
  end
end
local function questIDNow() return tonumber(_ns((try(G("GetQuestID"))))) end
function QB.LearnDetail()
  local q = questIDNow()
  if q then note("give", q, { t = _ns((try(G("GetTitleText")))) }) end
end
function QB.LearnFinisher()
  local q = questIDNow()
  if q then note("fin", q, { t = _ns((try(G("GetTitleText")))) }) end
end
--- où rendre q d'après ce qu'on a vu (PNJ qui l'a reprise / proposée) → { map, x, y, name } ou nil
function QB.LearnedFinisher(q)
  local L = learned()
  local e = L and L.fin[tonumber(q) or -1]
  if e and e.m and e.x then return { map = e.m, x = e.x, y = e.y, name = e.n } end
end
--- fiche « export » faite de ce qu'on a appris (quête proposée par un PNJ rencontré), pour une quête que Questie ne connaît pas
function QB.SyntheticRec(q)
  local L = learned()
  local e = L and L.give[tonumber(q) or -1]
  if not (e and e.m and e.x) then return nil end
  return { id = q, t = e.t, lv = tonumber(e.lv) or 0, rl = 0, st = "avail", xp = 0, o = {}, learned = true,
    s = { map = e.m, x = e.x, y = e.y, k = "npc", name = e.n } }
end
--- titre d'une quête vue chez un PNJ (le jeu, en français) ; nil si jamais vue
function QB.LearnedTitle(q)
  local L = learned()
  q = tonumber(q) or -1
  local e = L and (L.give[q] or L.fin[q])
  return e and type(e.t) == "string" and e.t ~= "" and e.t or nil
end
--- Questie est-il là (base de quêtes lisible) ?
function QB.HasQuestie()
  local Xm = X()
  return (Xm and Xm.Source and try(Xm.Source, Xm)) and true or false
end
--- ce qu'il manque, dit en clair (nil si rien) : « Quêtes à prendre » sans Questie ne connaît que les PNJ rencontrés
function QB.PickupNote()
  if QB.HasQuestie() then return nil end
  local L, n = learned(), 0
  for _ in pairs(L and L.give or {}) do n = n + 1 end
  if n == 0 then
    return "Sans Questie, TreeAI ne connaît que les quêtes des PNJ à qui tu as parlé : parle aux PNJ « ! » (Questie montre aussi les autres)"
  end
  return "Sans Questie : seulement les quêtes des PNJ à qui tu as parlé (XP estimée)"
end

-- ===================================================================== « Quêtes à prendre »
-- règles : pas dans le journal, pas faite, niveau requis atteint, prérequis faits (l'export ne garde que ceux-là),
-- race / classe (Export), exclusivité, jamais grise, au plus « écart » niveaux au-dessus (réglage du trajet), jamais
-- JcJ / raid / donjon déjà fait, donjon seulement à 4 ET coché, élite jamais en solo (à 2 seulement si « élites » est coché),
-- jamais ignorée, un donneur à un endroit connu hors instance.
function QB.Eligible(q, rec, ctx)
  local E, Z = engine(), T.ZonePlan
  if not (rec and rec.st == "avail") then return false, "pas disponible" end
  if ctx.log[q] or isDone(q) then return false, "faite" end
  if (tonumber(rec.rl) or 0) > ctx.level then return false, "niveau requis" end
  if rec.pre or rec.preAny then return false, "prérequis" end
  for _, x in ipairs(type(rec.excl) == "table" and rec.excl or {}) do
    if ctx.log[tonumber(x) or -1] or isDone(tonumber(x) or -1) then return false, "exclusive" end
  end
  local lv = tonumber(rec.lv) or 0
  if E and lv > 0 and lv <= E.GreyLevel(ctx.level) then return false, "grise" end
  local gap = tonumber(Z and Z.db and Z.db.gap) or 3
  if lv > 0 and lv - ctx.level > gap then return false, "trop haute" end
  if Z and Z.char and Z.char.ignore and Z.char.ignore[q] then return false, "ignorée" end
  local elite, grp, dg, ex = false, false, false, {}
  if Z and Z.Kind then elite, grp, dg, ex = Z.Kind(q, rec, nil) end
  if ex.pvp or ex.raid then return false, ex.pvp and "JcJ" or "raid" end
  if ex.dungeonDone then return false, "donjon fait" end
  if dg and not (ctx.group >= 4 and Z and Z.db and Z.db.dungeons) then return false, "donjon" end
  if elite and not (ctx.group >= 3 or (ctx.group >= 2 and Z and Z.db and Z.db.elites)) then return false, "élite" end
  if not (rec.s and rec.s.map and rec.s.x) or instance(rec.s.map) then return false, "donneur inconnu" end
  return true, nil, { elite = elite, group = grp, dungeon = dg }
end

-- valeur d'une quête à prendre → { xp, sec (tout compris), travel (jusqu'au donneur), why = { … }, score (XP / min) }
function QB.Value(q, rec, ctx, kind)
  local E = engine()
  local lv = (tonumber(rec.lv) or 0) > 0 and rec.lv or ctx.level
  local xp = rewardXP(q, rec, lv, ctx.level)
  local cost = ctx.cost or {}
  local mob, objMin = 0, 0
  local pos = rec.s
  local walk = 0
  local first = true
  for _, o in ipairs(rec.o or {}) do
    local need = tonumber(o.n) or 1
    if need <= 1 and E and E.DEFAULT_N then need = E.DEFAULT_N[o.k or ""] or need end
    local oo = { k = o.k, n = need, have = 0, p = o.p, plf = o.plf, ml = o.ml, mx = o.mx, rank = o.rank }
    local m = E and E.ObjMinutes(oo, cost) or 5
    if o.map and o.x and not instance(o.map) then
      local s = legSec(pos, o)
      if s then
        -- même quête, même site (≤ 400 yd) : faits ensemble, une part du temps est commune (comme ZonePlan)
        walk = walk + s
        if not first and (distYd(pos, o) or huge) <= 400 then m = m * 0.6 end
        pos = o
      end
    end
    first = false
    objMin = objMin + m
    if (o.k == "kill" or o.k == "item") and E then
      mob = mob + E.KillXP(ctx.level, tonumber(o.mx) or tonumber(o.ml) or ctx.level) * E.GroupShare(ctx.group) * need
    end
  end
  if kind and kind.dungeon then objMin = max(objMin, 45) end      -- un donjon : ~45 min à 4, au moins
  if rec.learned and #(rec.o or {}) == 0 then objMin = 10 end      -- objectifs inconnus (sans Questie) : ~10 min
  local fin = (rec.f and rec.f.x and not instance(rec.f.map)) and rec.f or rec.s
  walk = walk + (legSec(pos, fin) or 0)
  local travel = legSec(ctx.here, rec.s)
  if not travel then travel = 600 end
  local sec = travel + walk + objMin * 60 + 30
  local gain = xp + mob
  local why = {}
  -- suite de chaîne : la quête ouvre la suivante (QuestieDB nextQuestInChain) → la moitié de son XP comptée en plus
  local nxName
  if rec.nx then
    nxName = questName(rec.nx)
    if E then gain = gain + 0.5 * E.QuestXPEstimate(lv) * E.QuestXPFactor(ctx.level, lv) end
  end
  local score = gain / max(1, sec / 60)
  -- groupe : une quête que tes amis ont (export : who) se fait ensemble ; élite / groupe : à faire à plusieurs
  local who = type(rec.who) == "table" and #rec.who or 0
  if who > 0 and ctx.group > 1 then score = score * 1.25 end
  if (lv - ctx.level) >= 2 then score = score * 0.85 end
  local minutes = max(1, floor(sec / 60 + 0.5))
  why[#why + 1] = fmt("%s%s XP en ~%d min", rec.learned and "≈ " or "", thousands(gain), minutes)
  if rec.learned then why[#why + 1] = "PNJ déjà rencontré" end
  if ctx.here and rec.s then
    local d = distYd(ctx.here, rec.s)
    if d and d <= 110 then why[#why + 1] = "tout près"
    elseif travel >= 300 then why[#why + 1] = fmt("%d min de trajet", floor(travel / 60 + 0.5)) end
  end
  if nxName then why[#why + 1] = "ouvre la suite « " .. cut(nxName, 28) .. " »"
  elseif rec.nx then why[#why + 1] = "ouvre la suite de la chaîne" end
  if who > 0 and ctx.group > 1 then why[#why + 1] = "ton groupe l'a" end
  if kind and kind.dungeon then why[#why + 1] = fmt("donjon (vous êtes %d)", ctx.group)
  elseif kind and kind.elite then why[#why + 1] = fmt("élite : à faire à %d", ctx.group) end
  if (lv - ctx.level) >= 2 then why[#why + 1] = fmt("niveau %d : un peu haute", lv) end
  return { xp = floor(gain + 0.5), sec = sec, travel = travel, score = score, why = why, minutes = minutes }
end

local pickMemo
local function ctxNow()
  local P = T.Planner
  local cost = (P and P.CostCtx and try(P.CostCtx)) or { level = myLevel(), group = groupSize() }
  return { level = myLevel(), group = groupSize(), log = logSnap(), here = here(), cost = cost }
end
QB.Ctx = ctxNow
-- 2 chiffres significatifs : de petites variations de trajet ne changent pas l'ordre (pas de « valse » des quêtes)
local function bucket(v)
  v = tonumber(v) or 0
  if v <= 0 then return 0 end
  local p = 10 ^ max(0, floor(math.log10 and math.log10(v) or (math.log(v) / math.log(10))) - 1)
  return floor(v / p + 0.5) * p
end
QB.Bucket = bucket
--- → { { questID, title, level, npc, map, x, y, dist, eta, xp, why, score, minutes } }, au plus n (8 par défaut)
function QB.PickupList(n)
  n = tonumber(n) or 8
  local exp = exportData()
  local level, group = myLevel(), groupSize()
  local Z = T.ZonePlan
  local key = fmt("%s|%d|%d|%d|%s|%s|%s|%s", tostring(exp and exp.made), #(exp and exp.quests or {}), QB.gen, level, tostring(group),
    tostring(Z and Z.db and Z.db.dungeons), tostring(Z and Z.db and Z.db.elites), tostring(Z and Z.db and Z.db.gap))
  local ctx
  local hpos = here()
  if pickMemo and pickMemo.key == key and pickMemo.here == hpos then
    local out = {}
    for i = 1, min(n, #pickMemo.list) do out[i] = pickMemo.list[i] end
    return out
  end
  ctx = ctxNow()
  ctx.here = hpos
  local rank0 = {}
  for i, e in ipairs(pickMemo and pickMemo.list or {}) do rank0[e.questID] = i end
  local list = {}
  local cands = {}
  for q, rec in pairs(recsOf(exp)) do cands[q] = rec end
  local L = learned()                                -- PNJ rencontrés : leurs quêtes, même sans Questie
  for q, e in pairs(L and L.give or {}) do
    if not cands[q] and not e.triv and not e.rep then cands[q] = QB.SyntheticRec(q) end
  end
  for q, rec in pairs(cands) do
    local ok, _, kind = QB.Eligible(q, rec, ctx)
    if ok then
      local v = QB.Value(q, rec, ctx, kind)
      local npc = rec.s and rec.s.name or nil
      list[#list + 1] = { questID = q, title = rec.t or questName(q) or ("quête " .. q), level = tonumber(rec.lv) or 0,
        npc = npc, map = rec.s.map, x = rec.s.x, y = rec.s.y, dist = distYd(hpos, rec.s), eta = v.travel, xp = v.xp,
        why = tconcat(v.why, " · "), score = v.score, minutes = v.minutes, _b = bucket(v.score),
        elite = kind and kind.elite or nil, dungeon = kind and kind.dungeon or nil }
    end
  end
  tsort(list, function(a, b)
    if a._b ~= b._b then return a._b > b._b end
    local ra, rb = rank0[a.questID] or huge, rank0[b.questID] or huge
    if ra ~= rb then return ra < rb end
    return a.questID < b.questID
  end)
  pickMemo = { key = key, here = hpos, list = list }
  local out = {}
  for i = 1, min(n, #list) do out[i] = list[i] end
  return out
end

-- ===================================================================== « Rendre mes quêtes »
local function waypoint(q)
  local wp = C("C_QuestLog", "GetNextWaypoint")
  local m, x, y
  if wp then m, x, y = try(wp, q) end
  m, x, y = tonumber(_ns(m)), tonumber(_ns(x)), tonumber(_ns(y))
  if m and x and y then return { map = m, x = floor(x * 1000 + 0.5) / 10, y = floor(y * 1000 + 0.5) / 10 } end
end
local turnMemo
--- → { { questID, title, npc, map, x, y, eta, xp, zone } } : les quêtes finies du journal ; dans l'ordre du trajet quand le
--- mode « Rendre mes quêtes » est actif (eta = arrivée dans ce trajet), sinon les plus proches d'abord (eta = trajet direct)
function QB.TurnInList()
  local exp = exportData()
  local log = logSnap()
  local recs = recsOf(exp)
  local hpos = here()
  local level = myLevel()
  local P = T.Planner
  local key = fmt("%s|%d|%d|%s", tostring(exp and exp.made), QB.gen, level, QB.Mode())
  local list
  if turnMemo and turnMemo.key == key and turnMemo.here == hpos then list = turnMemo.list
  else
    list = {}
    for q, lq in pairs(log) do
      if lq.done == 1 and not lq.failed then
        local rec = recs[q]
        local f = rec and rec.f and rec.f.x and not instance(rec.f.map) and rec.f or QB.LearnedFinisher(q) or waypoint(q)
        local Zm = T.ZonePlan
        local zone = f and f.map and Zm and Zm.OutdoorZone and Zm.OutdoorZone(f.map)
        list[#list + 1] = { questID = q, title = lq.t or (rec and rec.t) or ("quête " .. q), npc = (rec and rec.f and rec.f.name) or (f and f.name) or nil,
          map = f and f.map or nil, x = f and f.x or nil, y = f and f.y or nil, eta = f and legSec(hpos, f) or nil,
          xp = rewardXP(q, rec, lq.lv, level), zone = zone and Zm.MapName(zone) or nil }
      end
    end
    tsort(list, function(a, b)
      local ea, eb = a.eta or huge, b.eta or huge
      if ea ~= eb then return ea < eb end
      return a.questID < b.questID
    end)
    turnMemo = { key = key, here = hpos, list = list }
  end
  -- mode actif : l'ordre et les temps du trajet (Route : vols, bateaux, foyer compris) — le même que la carte / la boussole
  local out = {}
  for i, e in ipairs(list) do out[i] = e end
  local R = T.Route
  if QB.Mode() == "turnin" and R and R.Stops and P and P.steps then
    local ok, st = pcall(R.Stops, R, 64)
    if ok and type(st) == "table" then
      local at, pos = {}, {}
      local k = 0
      for _, e in ipairs(st) do
        local s = e.step
        if s and s.k == "turn" and s.q and not at[s.q] then k = k + 1; at[s.q], pos[s.q] = e.at, k end
      end
      for _, e in ipairs(out) do if at[e.questID] then e.eta = at[e.questID] end end
      tsort(out, function(a, b)
        local pa, pb = pos[a.questID] or huge, pos[b.questID] or huge
        if pa ~= pb then return pa < pb end
        return a.questID < b.questID
      end)
    end
  end
  return out
end

-- ===================================================================== « Avec le groupe »
--- quêtes du journal que ton groupe a aussi (n membres) et celles que tu peux partager → { { questID, title, n, share } }
function QB.GroupList()
  local log = logSnap()
  local out = {}
  if groupSize() < 2 then return out end
  local push = C("C_QuestLog", "IsPushableQuest")          -- (par questID ; l'API classique demande la sélection : jamais)
  for q, lq in pairs(log) do
    if not lq.failed then
      local n = partyOn(q)
      local share = (n < groupSize() - 1) and push and _ns((try(push, q))) and true or false
      if n > 0 or share then out[#out + 1] = { questID = q, title = lq.t or ("quête " .. q), n = n, share = share } end
    end
  end
  tsort(out, function(a, b) if a.n ~= b.n then return a.n > b.n end return a.questID < b.questID end)
  return out
end

-- ===================================================================== comptes + modes publics
local function zoneCount()
  local Z = T.ZonePlan
  local log = logSnap()
  local n = 0
  for q, lq in pairs(log) do
    if not lq.failed then
      local zq = Z and Z.QuestZone and Z.QuestZone(q)
      if not zq or (Z.InZone and Z:InZone(zq)) then n = n + 1 end
    end
  end
  return n
end
local function counts()
  local c = { turnin = #QB.TurnInList(), pickup = #QB.PickupList(99), zone = zoneCount() }
  local g = 0
  for _, e in ipairs(QB.GroupList()) do if e.n > 0 then g = g + 1 end end
  c.group = g
  return c
end
QB.Counts = counts
local function bindingKey(action)
  local f = G("GetBindingKey")
  local k = f and _ns((try(f, action)))
  return type(k) == "string" and k ~= "" and k or nil
end
--- → { { id, label, short, key, binding, tip, count, active } } dans l'ordre : turnin, pickup, zone, group
function QB.Modes()
  local cur, c = QB.Mode(), counts()
  local out = {}
  for i, m in ipairs(QB.MODES) do
    local tip = m.tip
    if m.id == "pickup" then local nt = QB.PickupNote() if nt then tip = tip .. "\n" .. nt end end
    out[i] = { id = m.id, label = m.label, short = m.short, key = bindingKey(m.binding), binding = m.binding, tip = tip,
      count = c[m.id] or 0, active = m.id == cur }
  end
  return out
end
--- change de mode (un clic, une touche, /tai rendre…) : le trajet suit tout de suite ; false pour un mode inconnu
function QB.SetMode(id, by)
  if not (id and BY_ID[id] and QB.char) then return false end
  local prev = QB.Mode()
  QB.char.mode = id
  QB.char.modeBy = (by == "auto") and "auto" or "player"
  QB._backSaid = nil
  local R = T.Route
  if R and R.char and R.char.paused then R.char.paused = false end   -- choisir un mode = y aller
  if prev ~= id then
    local Z = T.ZonePlan
    if Z then
      Z.lastKeys = nil                               -- nouvel ordre tout de suite (pas d'hystérésis sur l'ancien mode)
      if Z.Bump then pcall(Z.Bump, Z, "mode : " .. tostring(QB.Label(id))) end
    end
    if T.Export and T.Export.Dirty and id == "pickup" then pcall(T.Export.Dirty, T.Export) end
    if T.Fire then pcall(T.Fire, T, "QUEST_MODE_CHANGED", id, prev) end
  end
  return true
end
function QB.Cycle()
  local i = BY_ID[QB.Mode()] or 3
  return QB.SetMode(QB.MODES[i % #QB.MODES + 1].id)
end

-- ===================================================================== « Suivant » : passer l'arrêt 1 (bande de boutons)
-- le choix du joueur, gardé pour la SESSION (comme les choix faits au pilote auto) : les quêtes de l'arrêt 1 sortent du
-- trajet tout de suite (ZonePlan:MaskSteps), le trajet est refait sans elles ; Unskip(q) / Prev() les remettent
QB.skipped, QB.skipOrder = {}, {}
local function replanNow(why)
  local P, Z = T.Planner, T.ZonePlan
  if Z then Z.lastKeys = nil; if Z.Bump then pcall(Z.Bump, Z, why) end end
  if P and P.Evaluate and P.steps then
    pcall(P.Evaluate, P)
    P._sig, P._lastRender = nil, nil
    if P.StepsChanged then pcall(P.StepsChanged, P) end
    if P.Render then pcall(P.Render, P) end
  end
  local Cm = T.Compass
  if Cm and type(Cm.Rebuild) == "function" then pcall(Cm.Rebuild, "plan") end
end
--- quêtes de l'arrêt 1 du trajet (même numéro d'arrêt : une quête à un endroit, un PNJ) → { q, … }
function QB.FirstStopQuests()
  local R, P = T.Route, T.Planner
  if not (R and R.Stops and P and P.steps) then return {} end
  local ok, st = pcall(R.Stops, R, 64)
  if not (ok and type(st) == "table") then return {} end
  local first
  for i, e in ipairs(st) do if e.step and e.step.q then first = i break end end
  if not first then return {} end
  local grp = R.Groups and R.Groups(st) or {}
  local out, seen = {}, {}
  for i = first, #st do
    local s = st[i].step
    if i > first and grp[i] ~= grp[first] then break end
    if s and s.q and not seen[s.q] then seen[s.q] = true; out[#out + 1] = s.q end
  end
  return out
end
--- passe l'arrêt 1 (ses quêtes, pour la session) → true si quelque chose a changé
function QB.Skip()
  if T.InCombat and T:InCombat() then return false end
  local qs = QB.FirstStopQuests()
  if #qs == 0 then return false end
  for _, q in ipairs(qs) do QB.skipped[q] = true end
  QB.skipOrder[#QB.skipOrder + 1] = qs
  replanNow("arrêt passé (Suivant)")
  if T.Fire then pcall(T.Fire, T, "QUEST_SKIPPED", qs) end
  return true
end
QB.SkipStop, QB.NextStop = QB.Skip, QB.Skip
--- remet une quête passée (nil = toutes) → true si quelque chose a changé
function QB.Unskip(q)
  local changed = false
  if q == nil then
    changed = next(QB.skipped) ~= nil
    QB.skipped, QB.skipOrder = {}, {}
  else
    q = tonumber(q)
    if q and QB.skipped[q] then QB.skipped[q], changed = nil, true end
  end
  if changed then
    replanNow("arrêt repris")
    if T.Fire then pcall(T.Fire, T, "QUEST_SKIPPED", {}) end
  end
  return changed
end
--- « Précédent » : reprend le dernier arrêt passé → true si quelque chose a changé
function QB.Prev()
  local last = table.remove(QB.skipOrder)
  if not last then return false end
  for _, q in ipairs(last) do QB.skipped[q] = nil end
  replanNow("arrêt repris")
  if T.Fire then pcall(T.Fire, T, "QUEST_SKIPPED", {}) end
  return true
end
function QB.Skipped(q) return QB.skipped[tonumber(q) or -1] and true or false end

-- ===================================================================== trajet : les étapes du mode (contrat ZonePlan)
--- quêtes à prendre mises dans le trajet du mode « Quêtes à prendre » : les meilleures, au plus pickRoute
function QB.RouteSet(mode)
  mode = mode or QB.Mode()
  if mode ~= "pickup" then return nil end
  local set = {}
  for _, e in ipairs(QB.PickupList(tonumber(QB.db and QB.db.pickRoute) or 6)) do set[e.questID] = e.score or 1 end
  return set
end

-- ===================================================================== retour automatique (dit une fois, jamais le chat)
function QB:CheckEmpty()
  if not (self.db and self.db.autoBack ~= false) then return end
  local m = QB.Mode()
  if m ~= "turnin" and m ~= "pickup" then return end
  if (T.InCombat and T:InCombat()) then return end
  local n = (m == "turnin") and #QB.TurnInList() or #QB.PickupList(1)
  if n > 0 then return end
  QB.SetMode("zone", "auto")
  local txt = (m == "turnin") and "Toutes tes quêtes finies sont rendues : retour à « Tout faire ici »"
    or "Plus de bonne quête à prendre ici : retour à « Tout faire ici »"
  pcall(self.Suggest, self, "qbback", txt, 1, { ttl = 8, sound = false })
end

-- ===================================================================== « Choisir ma zone » + « Conseille-moi » (ZONEPICK 4.1)
-- « Rien n'est simple, je peux toujours pas choisir une région ou être conseillé comme je veux. » Une seule entrée : le
-- bouton « Zone » de la bande, le haut de la page Quêtes (« Changer »), /tai zone. Chaque zone qui a du sens pour TOI
-- maintenant, une ligne chacune, classées : nom (le jeu, en français), niveaux, « 12 quêtes pour toi (3 finies à
-- rendre) », trajet réel (vols, bateaux / zeppelins, pierre de foyer : Route), XP/h estimée, ce que donne le groupe,
-- et pourquoi en une ligne. Filtres : Pour moi · Pour le groupe · Rendre d'abord · Pas d'élites.
-- HONNÊTE : une quête n'est comptée que si tu peux la prendre (niveau requis, faction / race / classe, prérequis faits,
-- pas faite, pas dans le journal, pas grise, pas répétable, pas JcJ / raid / donjon, pas ignorée) — QuestieDB quand il
-- est là (un passage découpé, ≤ 2 ms par tranche, hors combat, refait au passage de niveau et quand une quête est prise
-- ou rendue) ; sinon ce que le jeu montre (« ! » de la carte) et les PNJ rencontrés (carnet des PNJ, quêtes vues chez
-- eux), dit une fois : « (sans Questie : estimation) ». Choisir = ZonePlan:PickZone(zone, "player") — gardée, le pilote
-- auto n'y touche plus — et le mode « Tout faire ici ». Calculé à la demande (fenêtre ouverte), jamais d'OnUpdate.
QB.scanGen = 0
QB.SCAN_MS = 2
QB.ZONE_SORTS = {
  { id = "moi", label = "Pour moi", tip = "Le plus d'XP par heure pour toi, trajet compris." },
  { id = "groupe", label = "Pour le groupe", tip = "Quêtes communes, élites possibles à plusieurs, niveaux de tout le groupe." },
  { id = "rendre", label = "Rendre d'abord", tip = "D'abord les zones où des quêtes finies attendent d'être rendues." },
}
local scan, scanJob
local function Zp() return T.ZonePlan end
local function outdoor(m)
  local Z = Zp()
  m = tonumber(m)
  if not (Z and m and Z.OutdoorZone) then return nil end
  return Z.OutdoorZone(m)
end
local function mapName(m) local Z = Zp() return Z and Z.MapName and Z.MapName(m) or ("zone " .. tostring(m)) end
local function greyLevel(L) local E = engine() return E and E.GreyLevel(L) or (L - 6) end
local function qxp(ql, L)
  local E = engine()
  if not E then return 0 end
  return E.QuestXPEstimate(ql) * E.QuestXPFactor(L, ql)
end
local function raceOK(db, mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local _, _, raceId = try(G("UnitRace"), "player")
  raceId = tonumber(_ns(raceId))
  local bit = raceId and ((db.enum and type(db.enum.raceMaskById) == "table" and tonumber(db.enum.raceMaskById[raceId])) or 2 ^ (raceId - 1))
  if not bit then return true end
  return floor(mask / bit) % 2 == 1
end
local function classOK(mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local _, _, classId = try(G("UnitClass"), "player")
  classId = tonumber(_ns(classId))
  if not classId then return true end
  return floor(mask / 2 ^ (classId - 1)) % 2 == 1
end
local function allDone(list) for _, p in ipairs(list) do p = tonumber(p) if p and not isDone(p) then return false end end return true end
local function anyDone(list) for _, p in ipairs(list) do p = tonumber(p) if p and isDone(p) then return true end end return false end
local function zacc(acc, z)
  local e = acc[z]
  if not e then e = { n = 0, xp = 0, el = 0, elXp = 0, cells = {} } acc[z] = e end
  return e
end
local function addQuest(acc, z, ql, xp, elite, x, y)
  local e = zacc(acc, z)
  if elite then e.el, e.elXp = e.el + 1, e.elXp + xp else e.n, e.xp = e.n + 1, e.xp + xp end
  if ql and ql > 0 then e.lo, e.hi = min(e.lo or ql, ql), max(e.hi or ql, ql) end
  x, y = tonumber(x), tonumber(y)
  if x and y then                                  -- le coin de la zone où les donneurs sont les plus nombreux
    local k = floor(x / 10) * 100 + floor(y / 10)
    local c = e.cells[k] or { n = 0, x = 0, y = 0 }
    c.n, c.x, c.y = c.n + 1, c.x + x, c.y + y
    e.cells[k] = c
  end
end
-- point de départ d'une quête (premier PNJ donneur, dans SA zone) → x, y en %
local function starterXY(db, id, zone)
  local sb = db.Q(id, "startedBy")
  local npc = type(sb) == "table" and type(sb[1]) == "table" and tonumber(sb[1][1])
  local sp = npc and db.N(npc, "spawns")
  if type(sp) ~= "table" then return nil end
  for area, pts in pairs(sp) do
    local m = db.map(tonumber(area) or -1)
    if m and tonumber(m) == zone and type(pts) == "table" and type(pts[1]) == "table" then
      return tonumber(pts[1][1]), tonumber(pts[1][2])
    end
  end
end
local TAG_SKIP = { [41] = true, [62] = true, [81] = true, [85] = true, [88] = true, [89] = true }
local function rankOf(db, npc) return tonumber(npc and db.N(npc, "rank")) or 0 end
--- QuestieDB : cette quête est-elle à prendre pour TOI ? → zone, niveau, XP, élite, x, y (sinon nil)
function QB.ZoneQuestOK(db, id, st)
  if st.log[id] or st.ignore[id] then return nil end
  local area = tonumber(db.Q(id, "zoneOrSort")) or 0
  if area <= 0 or (db.dungeon and db.dungeon(area)) then return nil end
  local z = outdoor(db.map(area))
  if not z then return nil end
  local rl = tonumber(db.Q(id, "requiredLevel")) or 0
  if rl > st.L then return nil end
  local ql = tonumber(db.Q(id, "questLevel")) or 0
  if ql <= 0 then ql = st.L end                      -- quête « à ton niveau » (-1 dans QuestieDB)
  if ql <= st.grey or ql - st.L > st.gap then return nil end
  if (tonumber(db.Q(id, "specialFlags")) or 0) % 2 == 1 then return nil end          -- répétable
  if not (raceOK(db, db.Q(id, "requiredRaces")) and classOK(db.Q(id, "requiredClasses"))) then return nil end
  if isDone(id) then return nil end
  local pg = db.Q(id, "preQuestGroup")
  if type(pg) == "table" and #pg > 0 and not allDone(pg) then return nil end
  local ps = db.Q(id, "preQuestSingle")
  if type(ps) == "table" and #ps > 0 and not anyDone(ps) then return nil end
  local ex = db.Q(id, "exclusiveTo")
  for _, x in ipairs(type(ex) == "table" and ex or {}) do
    x = tonumber(x)
    if x and (st.log[x] or isDone(x)) then return nil end
  end
  if db.doable and db.doable(id) == false then return nil end
  local elite = false
  local Z = Zp()
  local tag = Z and Z.QuestTag and Z.QuestTag(id)
  local tid = type(tag) == "table" and tonumber(tag.tagID) or nil
  if tid and TAG_SKIP[tid] then return nil end
  if tid == 1 or (type(tag) == "table" and tag.isElite) then elite = true end
  local objs = db.Q(id, "objectives")
  if type(objs) == "table" then
    for _, e in ipairs(type(objs[1]) == "table" and objs[1] or {}) do
      local r = rankOf(db, type(e) == "table" and tonumber(e[1]) or tonumber(e))
      if r == 3 then return nil end                  -- un boss : c'est un donjon
      if r == 1 or r == 2 then elite = true end
    end
    for _, e in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
      local item = type(e) == "table" and tonumber(e[1]) or tonumber(e)
      local drops = item and db.I(item, "npcDrops")
      local r = type(drops) == "table" and rankOf(db, tonumber(drops[1])) or 0
      if r == 1 or r == 2 then elite = true end
    end
  end
  local xp = tonumber(db.xp and db.xp(id)) or 0
  local E = engine()
  if xp <= 0 then xp = E and E.QuestXPEstimate(ql) or 0 end
  xp = xp * (E and E.QuestXPFactor(st.L, ql) or 1)
  local x, y = starterXY(db, id, z)
  return z, ql, xp, elite, x, y
end
-- sans Questie : quêtes vues chez les PNJ (QUEST_DETAIL, dialogues), carnet des PNJ (Finder), « ! » du jeu sur ta carte
local function learnedScan(st)
  local acc, seen = {}, {}
  local function add(q, map, x, y, lv)
    q = tonumber(q)
    if not q or seen[q] or st.log[q] or st.ignore[q] or isDone(q) then return end
    local z = outdoor(map)
    if not z then return end
    lv = tonumber(lv)
    if lv and lv > 0 and (lv <= st.grey or lv - st.L > st.gap) then return end
    seen[q] = true
    local ql = (lv and lv > 0) and lv or st.L
    addQuest(acc, z, lv, qxp(ql, st.L), false, x, y)
  end
  local L = learned()
  for q, e in pairs(L and L.give or {}) do
    if type(e) == "table" and not e.triv and not e.rep then add(q, e.m, e.x, e.y, e.lv) end
  end
  local NB = T.NpcBook
  local s = NB and NB.Store and try(NB.Store)
  if type(s) == "table" and type(s.qs) == "table" and type(s.n) == "table" then
    for q, id in pairs(s.qs) do
      local n = s.n[id]
      if type(n) == "table" and n.m and n.x then add(q, n.m, n.x * 100, (n.y or 0) * 100, nil) end
    end
  end
  local GQ = T.GameQuests
  local list = GQ and GQ.Available and try(GQ.Available)
  for _, e in ipairs(type(list) == "table" and list or {}) do
    if type(e) == "table" then add(e.questID, e.mapID, e.x and e.x * 100, e.y and e.y * 100, nil) end
  end
  return acc
end
local function scanKey()
  local Z = Zp()
  return fmt("%d|%d|%s|%s|%d", myLevel(), QB.scanGen, tostring(Z and Z.db and Z.db.gap), tostring(QB.HasQuestie()),
    (function() local n = 0 for _ in pairs(Z and Z.char and Z.char.ignore or {}) do n = n + 1 end return n end)())
end
local function scanCtx()
  local Z = Zp()
  local L = myLevel()
  return { L = L, grey = greyLevel(L), gap = tonumber(Z and Z.db and Z.db.gap) or 3, log = logSnap(),
    ignore = (Z and Z.char and Z.char.ignore) or {} }
end
local function scanDone(acc, key, est)
  scan, scanJob = { key = key, ready = true, est = est, z = acc }, nil
  local Wn = T.Window
  -- fenêtre ouverte sur la page Quêtes (choix de zone, « Zones à faire ») : redessinée avec les vrais comptes
  if QB.onScan then pcall(QB.onScan)
  elseif Wn and Wn.Dirty and Wn.IsOpen and Wn.IsOpen() and Wn.CurrentPage and Wn.CurrentPage() == "quests" then pcall(Wn.Dirty) end
end
--- les quêtes à prendre par zone (passage honnête) → scan { ready, est, z = { [zone] = { n, xp, el, elXp, lo, hi, cells } } }
--- ou l'ancien (ou nil) pendant le comptage ; QB.ScanBusy() le dit
function QB.ZoneScan()
  local key = scanKey()
  if scan and scan.key == key then return scan end
  if scanJob and scanJob.key == key then return scan end
  local Xm = X()
  local db = Xm and Xm.Source and try(Xm.Source, Xm)
  local st = scanCtx()
  if not db then scanDone(learnedScan(st), key, true) return scan end
  if scanJob and scanJob.ticker then pcall(scanJob.ticker.Cancel, scanJob.ticker) end
  local ids = try(db.ids) or {}
  local job = { key = key, i = 1, acc = {} }
  scanJob = job
  local function step()
    if scanJob ~= job then if job.ticker then pcall(job.ticker.Cancel, job.ticker) end return end
    if T.InCombat and T:InCombat() then return end
    local t0 = tonumber(try(G("debugprofilestop")))
    local k = 0
    while job.i <= #ids do
      local id = ids[job.i]
      job.i, k = job.i + 1, k + 1
      local ok, z, ql, xp, elite, x, y = pcall(QB.ZoneQuestOK, db, id, st)
      if ok and z then addQuest(job.acc, z, ql, xp, elite, x, y) end
      if k % 40 == 0 then
        local t1 = tonumber(try(G("debugprofilestop")))
        if not (t0 and t1) or t1 - t0 > QB.SCAN_MS then return end
      end
    end
    if job.ticker then pcall(job.ticker.Cancel, job.ticker) end
    scanDone(job.acc, key, false)
  end
  local Z = Zp()
  job.ticker = Z and Z.SliceJob and Z.SliceJob(step) or nil   -- (découpé par ZonePlan, comme son comptage de zones)
  if not job.ticker then while scanJob == job do step() end end
  return scan
end
function QB.ScanBusy() return scanJob ~= nil end
function QB.ScanEstimate() return (scan and scan.est) or not QB.HasQuestie() end

-- ---------------------------------------------------------------- trajet réel vers une zone
local MODE_WORD = { fly = "vol", boat = "bateau", zeppelin = "zeppelin", zep = "zeppelin", hs = "foyer" }
--- secondes + étapes { mode, sec, to } de ta position à un point { map, x, y } (vols, bateaux, foyer : Route)
function QB.TravelTo(pt)
  local hp = here()
  if not (hp and pt and pt.map and pt.x) then return nil end
  local R = T.Route
  if R and R.IsEnabled and R:IsEnabled() and R.Graph and R.FastestWorld and R.World then
    local g = try(R.Graph)
    local wa, wb = R.World(hp), R.World(pt)
    if g and wa and wb then
      local hs = R.HearthReady and try(R.HearthReady)
      local bind = hs and R.BindPlace and try(R.BindPlace) or nil
      local s, legs = R.FastestWorld(g, wa, wb, { hearth = (hs and bind) and true or false, bind = bind })
      if tonumber(s) and s < huge then return s, legs end
    end
  end
  return legSec(hp, pt), nil
end
--- « 6 min (vol) », « 14 min (foyer + vol) », « 3 min à pied » ; nil sans temps
function QB.TravelText(sec, legs)
  if not tonumber(sec) then return nil end
  local mins = max(1, floor(sec / 60 + 0.5))
  local words, seen = {}, {}
  for _, l in ipairs(legs or {}) do
    local w = MODE_WORD[l.mode or ""]
    if w and not seen[w] then seen[w] = true; words[#words + 1] = w end
  end
  if #words > 0 then return fmt("%d min (%s)", mins, tconcat(words, " + ")) end
  if legs then return fmt("%d min à pied", mins) end
  return fmt("~%d min", mins)
end
local function flightTo(legs)
  local name
  for _, l in ipairs(legs or {}) do
    if (l.mode == "fly" or l.mode == "boat" or l.mode == "zeppelin") and type(l.to) == "table" and type(l.to.name) == "string" then name = l.to.name end
  end
  return name
end
-- point d'arrivée d'une zone : le coin où ses donneurs sont les plus nombreux, sinon son maître de vol, sinon le centre
local function zonePoint(z, e)
  local best
  for _, c in pairs(e and e.cells or {}) do if not best or c.n > best.n then best = c end end
  if best then return { map = z, x = best.x / best.n, y = best.y / best.n } end
  local D = T.Data and T.Data.Travel
  local R = T.Route
  local fac = R and R.Faction and R.Faction()
  local pick
  for id, r in pairs(D and D.taxi or {}) do
    if r[5] == z and (not (R and R.FactionOK) or R.FactionOK(r[8], fac)) and (not pick or id < pick.id) then pick = { id = id, map = z, x = r[6], y = r[7] } end
  end
  return pick or { map = z, x = 50, y = 50 }
end

-- ---------------------------------------------------------------- les lignes du choix de zone
local function members()
  local n = groupSize()
  local lv = {}
  for i = 1, min(4, n - 1) do
    local l = tonumber(_ns((try(G("UnitLevel"), "party" .. i))))
    if l and l > 0 then lv[#lv + 1] = l end
  end
  tsort(lv)
  return n, lv
end
-- journal par zone : en cours (pas finies) et finies (zone du PNJ de remise), communes avec le groupe
local function logZones(level, group)
  local Z = Zp()
  local log = logSnap()
  local recs = recsOf(exportData())
  local out = {}
  local function zr(z) local e = out[z] if not e then e = { open = 0, fin = 0, xp = 0, common = 0, el = 0 } out[z] = e end return e end
  local finZ = {}
  for _, e in ipairs(QB.TurnInList()) do
    local z = e.map and outdoor(e.map)
    if z then finZ[e.questID] = z end
  end
  for q, lq in pairs(log) do
    if not lq.failed then
      local rec = recs[q]
      if lq.done == 1 then
        local z = finZ[q]
        if z then local e = zr(z) e.fin, e.xp = e.fin + 1, e.xp + rewardXP(q, rec, lq.lv, level) end
      elseif Z and Z.QuestZone then
        local ok = true
        if Z.Accept then ok = Z:Accept(q, rec, lq, level, group) end
        local z = ok and outdoor(Z.QuestZone(q))
        if z then
          local e = zr(z)
          e.open, e.xp = e.open + 1, e.xp + rewardXP(q, rec, lq.lv, level)
          if partyOn(q) > 0 then e.common = e.common + 1 end
          local el = Z.Kind and Z.Kind(q, rec, lq)
          if el then e.el = e.el + 1 end
        end
      end
    end
  end
  return out
end
local QMIN = { 9, 7.5, 6.5, 6, 6 }                    -- minutes par quête à prendre (trajet dans la zone compris), selon le groupe
--- toutes les zones qui ont du sens pour toi, classées → { rows…, est = bool, busy = bool, sort, noElite }
--- row = { map, name, lo, hi, avail, open, fin, total, elites, sec, travel, xph, group, why, here, on, star, cross, score }
function QB.ZoneRows(opts)
  opts = opts or {}
  local ch = QB.char or {}
  local sort = opts.sort or ch.zpSort or "moi"
  local noElite = opts.noElite
  if noElite == nil then noElite = ch.zpNoElite and true or false end
  local Z = Zp()
  local level = myLevel()
  local n, lv = members()
  local sc = QB.ZoneScan()
  local acc = (sc and sc.z) or {}
  local lz = logZones(level, n)
  local hp = here()
  local hz = hp and outdoor(hp.map)
  local myCont = hz and Z and Z.Cont and Z.Cont(hz)
  local chosen = {}
  if Z and Z.char and Z.char.mode == "zones" then for m in pairs(Z.char.zones or {}) do chosen[tonumber(m) or -1] = true end end
  local set = {}
  for z in pairs(acc) do set[z] = true end
  for z in pairs(lz) do set[z] = true end
  if hz then set[hz] = true end
  for z in pairs(chosen) do if z > 0 then set[z] = true end end
  local E = engine()
  local eliteOK = n >= 3 and not noElite
  local rows = {}
  for z in pairs(set) do
    local a, l = acc[z] or { n = 0, xp = 0, el = 0, elXp = 0 }, lz[z] or { open = 0, fin = 0, xp = 0, common = 0, el = 0 }
    local avail = a.n + (eliteOK and a.el or 0)
    local total = avail + l.open + l.fin
    local cross = myCont and Z.Cont(z) and Z.Cont(z) ~= myCont or false
    if (total > 0 or z == hz or chosen[z]) and not (cross and total < 3 and not chosen[z]) then
      local r = { map = z, name = mapName(z), lo = a.lo, hi = a.hi, avail = avail, open = l.open, fin = l.fin, total = total,
        elites = a.el, common = l.common, here = z == hz, on = chosen[z] or false, cross = cross }
      if z == hz then r.sec = 0
      else
        local pt = zonePoint(z, a)
        r.sec, r.legs = QB.TravelTo(pt)
        r.via = flightTo(r.legs)
      end
      r.travel = r.here and "tu y es" or QB.TravelText(r.sec, r.legs)
      -- XP/h estimée : quêtes à prendre (élites seulement à 3+), en cours, finies (≈ 1 min chacune), + monstres tués
      local qmin = QMIN[min(5, n)]
      local work = avail * qmin + (eliteOK and a.el or 0) * 4 + l.open * 6 + l.fin * 1
      local xp = a.xp + (eliteOK and a.elXp or 0) + l.xp
      if E then
        local ml = r.hi and floor(((r.lo or level) + r.hi) / 2) or level
        xp = xp + (avail + l.open) * 8 * E.KillXP(level, ml) * E.GroupShare(n)
      end
      -- l'heure qui vient : trajet, puis les quêtes de la zone ; s'il reste du temps, des monstres à ton niveau (une zone
      -- de 2 quêtes n'est pas « la plus rentable » parce qu'elle se fait vite)
      local avail0 = max(0, 60 - (tonumber(r.sec) or 600) / 60)
      local fill = E and (2 * E.KillXP(level, level) * E.GroupShare(n)) or 0
      local inHour = (work <= avail0) and (xp + (avail0 - work) * fill) or (xp * avail0 / max(1, work))
      r.xp, r.work = floor(xp + 0.5), work
      r.xph = (work > 0) and floor(inHour / 100 + 0.5) * 100 or 0
      -- le groupe
      if n >= 2 then
        local g = { fmt("à %d", n) }
        if a.el > 0 and not noElite then
          g[#g + 1] = (n >= 3) and fmt("élite%s possible%s", a.el > 1 and "s" or "", a.el > 1 and "s" or "") or "élites risquées à 2"
        end
        local low = lv[1]
        if low and r.lo and low < r.lo - 2 then g[#g + 1] = fmt("un membre trop bas (%d)", low)
        elseif #lv > 0 and r.lo then g[#g + 1] = "tout le groupe au niveau" end
        if l.common > 0 then g[#g + 1] = fmt("%d quête%s commune%s", l.common, l.common > 1 and "s" or "", l.common > 1 and "s" or "") end
        r.group = (#g > 1) and (g[1] .. " : " .. tconcat(g, ", ", 2)) or nil
        r.lowMember = low and r.lo and low < r.lo - 2 or false
      elseif a.el > 0 and not noElite then
        r.group = fmt("seul : %d élite%s laissée%s de côté", a.el, a.el > 1 and "s" or "", a.el > 1 and "s" or "")
      end
      -- score du filtre
      local s = r.xph
      if sort == "groupe" then
        s = s * (1 + 0.2 * l.common) * (r.lowMember and 0.6 or 1) * ((n >= 3 and a.el > 0 and not noElite) and 1.15 or 1)
      elseif sort == "rendre" then
        s = l.fin * 1e7 - (tonumber(r.sec) or 0) * 1000 + r.xph / 100
      end
      r.score = s
      rows[#rows + 1] = r
    end
  end
  tsort(rows, function(x, y)
    local bx, by = bucket(x.score), bucket(y.score)
    if sort == "rendre" then bx, by = x.score, y.score end
    if bx ~= by then return bx > by end
    if x.here ~= y.here then return x.here end
    if (x.sec or 0) ~= (y.sec or 0) then return (x.sec or 0) < (y.sec or 0) end
    return x.name < y.name
  end)
  local out = { est = QB.ScanEstimate(), busy = QB.ScanBusy(), sort = sort, noElite = noElite }
  local lim = tonumber(opts.limit) or 10
  for i = 1, min(lim, #rows) do out[i] = rows[i] end
  local top = out[1]
  if top and (top.total > 0) then top.star = true end
  for i, r in ipairs(out) do r.why = QB.ZoneWhy(r, i == 1 and top.star, sort) end
  return out
end
local FIRST = { moi = "la plus rentable", groupe = "la meilleure à plusieurs", rendre = "le plus à rendre" }
--- la raison en une ligne : « la plus rentable : 3 quêtes finies à rendre, Camp Taurajo à 6 min (vol) »
function QB.ZoneWhy(r, first, sort)
  local parts = {}
  if r.fin > 0 then parts[#parts + 1] = fmt("%d quête%s finie%s à rendre", r.fin, r.fin > 1 and "s" or "", r.fin > 1 and "s" or "") end
  if r.here then parts[#parts + 1] = "tu y es"
  elseif r.via and r.travel then parts[#parts + 1] = r.via .. " à " .. r.travel
  elseif r.travel then parts[#parts + 1] = r.travel .. " de trajet" end
  if r.common > 0 and #parts < 3 then parts[#parts + 1] = fmt("%d quête%s commune%s avec ton groupe", r.common, r.common > 1 and "s" or "", r.common > 1 and "s" or "") end
  if #parts < 2 and r.lo and r.hi then parts[#parts + 1] = fmt("quêtes niv %d-%d", r.lo, r.hi) end
  if r.lowMember then parts[#parts + 1] = "un membre est trop bas" end
  if r.total == 0 then parts = { "plus rien à faire pour toi ici" } end
  local s = tconcat(parts, ", ")
  if first then s = (FIRST[sort] or FIRST.moi) .. " : " .. s end
  return s
end
--- « 12 quêtes pour toi (3 finies à rendre) »
function QB.ZoneCountText(r)
  if r.total == 0 then return "rien pour toi" end
  local s = fmt("%d quête%s pour toi", r.total, r.total > 1 and "s" or "")
  if r.fin > 0 then s = s .. fmt(" (%d finie%s à rendre)", r.fin, r.fin > 1 and "s" or "") end
  return s
end
--- infobulle d'une ligne : le détail, d'où viennent les chiffres
function QB.ZoneTip(r)
  local t = { fmt("%d à prendre, %d en cours, %d finie%s à rendre", r.avail, r.open, r.fin, r.fin > 1 and "s" or "") }
  if r.lo and r.hi then t[#t + 1] = fmt("Quêtes à prendre : niveaux %d à %d", r.lo, r.hi) end
  if r.travel and not r.here then t[#t + 1] = "Trajet : " .. r.travel .. (r.via and (", arrivée " .. r.via) or "") end
  if r.xph and r.xph > 0 then t[#t + 1] = fmt("~%s XP/h estimés (quêtes + monstres, trajet compris)", thousands(r.xph)) end
  if r.group then t[#t + 1] = "Groupe " .. r.group end
  if r.cross then t[#t + 1] = "Autre continent : bateau ou zeppelin compris" end
  if QB.ScanEstimate() then t[#t + 1] = "Sans Questie : seulement les quêtes des PNJ rencontrés et du jeu (estimation)" end
  return t
end
function QB.SetZoneSort(id)
  if not QB.char then return false end
  for _, s in ipairs(QB.ZONE_SORTS) do if s.id == id then QB.char.zpSort = id return true end end
  return false
end
function QB.ToggleNoElite() if QB.char then QB.char.zpNoElite = not QB.char.zpNoElite end return QB.char and QB.char.zpNoElite end
--- choisir une zone (un clic) : le trajet y va, et y reste (choix du joueur, le pilote auto n'y touche plus)
function QB.PickZone(map, by)
  local Z = Zp()
  map = tonumber(map)
  if not (Z and Z.PickZone and map) then return false end
  if not Z:PickZone(map, by == "brain" and "brain" or "player") then return false end
  QB.SetMode("zone", by == "brain" and "auto" or "player")
  if T.Fire then pcall(T.Fire, T, "ZONE_PICKED", map) end
  return true
end
--- « Automatique » : TreeAI suit ta zone et choisit la suivante quand elle est finie (pilote auto)
function QB.ZoneAuto()
  local Z = Zp()
  if not (Z and Z.SetMode) then return false end
  Z:SetMode("auto")
  if Z.char then Z.char.zoneBy = nil end
  QB.SetMode("zone", "player")
  if T.Fire then pcall(T.Fire, T, "ZONE_PICKED", nil) end
  return true
end
--- la zone de travail en clair : texte, qui l'a choisie (« toi » / « pilote auto » / « auto »), carte
function QB.ZoneNow()
  local Z = Zp()
  if not (Z and Z.Allowed and Z.IsEnabled and Z:IsEnabled()) then return nil end
  local A = try(Z.Allowed, Z)
  if type(A) ~= "table" then return nil end
  local ch = Z.char or {}
  local by = (A.mode == "auto") and "auto" or (A.mode == "continent" and "région") or (ch.zoneBy == "brain" and "pilote" or "toi")
  local map = A.mode == "zones" and next(A.maps or {}) or A.here
  local label = A.label or "?"
  local who = ({ auto = "automatique (ta zone)", ["région"] = "toute la région", pilote = "choisie par le pilote auto", toi = "choisie par toi" })[by]
  return { label = label, by = by, who = who, map = map }
end

-- ---------------------------------------------------------------- « Conseille-moi » : où aller maintenant et pourquoi
local function errands()
  local out = {}
  local API = T.API or {}
  local free, total = try(API.FreeSlots)
  free, total = tonumber(free), tonumber(total)
  if free and total and total > 0 and free <= 2 then out[#out + 1] = free == 0 and "sacs pleins" or fmt("sacs presque pleins (%d place%s)", free, free > 1 and "s" or "") end
  local cls = T.PlayerClass and T:PlayerClass()
  if cls == "HUNTER" then
    local ammo = tonumber(_ns((try(G("GetInventoryItemCount"), "player", 0))))
    if ammo and ammo < 200 then out[#out + 1] = fmt("munitions : %d", ammo) end
  end
  local worst
  for slot = 1, 18 do
    local c, mx = try(G("GetInventoryItemDurability"), slot)
    c, mx = tonumber(_ns(c)), tonumber(_ns(mx))
    if c and mx and mx > 0 then local p = c * 100 / mx if not worst or p < worst then worst = p end end
  end
  if worst and worst < 20 then out[#out + 1] = "équipement à réparer" end
  return out
end
QB.Errands = errands
--- au plus 3 conseils classés { text, why, label, apply = function, id } construits sur les vrais chiffres (journal,
--- zones, trajets, groupe, repos, sacs / munitions) ; chaque apply = un clic
function QB.Advice()
  local out = {}
  local n, lv = members()
  local level = myLevel()
  local rows = QB.ZoneRows({ sort = "moi", limit = 6 })
  local cur = QB.ZoneNow()
  -- 1. courses qui bloquent (sacs pleins, plus de munitions, équipement rouge) : avant de partir loin
  local er = errands()
  if #er > 0 then
    local CR = T.CityRun
    out[#out + 1] = { id = "errands", text = "D'abord les courses : " .. tconcat(er, ", "),
      why = "Sans ça, tu reviendras en ville au milieu de ta zone.",
      label = (CR and CR.Start) and "Courses en ville" or nil,
      apply = (CR and CR.Start) and function() CR.Start() end or nil, prio = (er[1]:find("pleins", 1, true) or er[1]:find("munitions", 1, true)) and 3 or 1 }
  end
  -- 2. quêtes finies : rendues d'abord quand il y en a 3+ (ou beaucoup d'XP)
  local tl = QB.TurnInList()
  local txp, far = 0, 0
  for _, e in ipairs(tl) do txp = txp + (tonumber(e.xp) or 0); if (tonumber(e.eta) or 0) > far then far = e.eta end end
  if #tl >= 3 or (#tl > 0 and txp >= 4 * (engine() and engine().QuestXPEstimate(level) or 2000)) then
    out[#out + 1] = { id = "turnin", text = fmt("Rends tes %d quête%s finie%s : +%s XP", #tl, #tl > 1 and "s" or "", #tl > 1 and "s" or "", thousands(txp)),
      why = fmt("Le trajet le plus court par les PNJ%s, vols et foyer compris.", far > 0 and fmt(" (la plus loin à %s)", QB.TravelText(far) or "?") or ""),
      label = "Rendre mes quêtes", apply = function() QB.SetMode("turnin") end, prio = 2 }
  end
  -- 3. la meilleure zone (ou rester ici), avec le repos
  local best = rows[1]
  if best and best.total > 0 then
    local rested = tonumber(_ns((try(G("GetXPExhaustion"))))) or 0
    local rest = rested > 0 and fmt(" · repos : %s XP en double sur les monstres", thousands(rested)) or ""
    local stay = best.here or (cur and cur.map == best.map)
    local txt = stay and fmt("Reste à %s : %s", best.name, QB.ZoneCountText(best))
      or fmt("Va à %s : %s", best.name, QB.ZoneCountText(best))
    out[#out + 1] = { id = "zone", map = best.map,
      text = txt .. (best.xph > 0 and fmt(", ~%s XP/h", thousands(best.xph)) or ""),
      why = (best.why or "") .. rest,
      label = stay and "Faire cette zone" or "Y aller", apply = function() QB.PickZone(best.map) end, prio = 1 }
  end
  -- 4. le groupe : la zone qui va à tout le monde (quêtes communes, élites à 3+, membre trop bas)
  if n >= 2 then
    local grows = QB.ZoneRows({ sort = "groupe", limit = 4 })
    local g = grows[1]
    local same = best and g and g.map == best.map
    if g and g.total > 0 and (g.common > 0 or (n >= 3 and g.elites > 0) or not same) then
      local bits = {}
      if g.common > 0 then bits[#bits + 1] = fmt("%d quête%s commune%s", g.common, g.common > 1 and "s" or "", g.common > 1 and "s" or "") end
      if n >= 3 and g.elites > 0 then bits[#bits + 1] = "élites possibles" end
      if lv[1] and g.lo and lv[1] < g.lo - 2 then bits[#bits + 1] = fmt("attention : un membre niv %d", lv[1]) end
      -- même zone que le conseil « zone » : le conseil du groupe, c'est le mode « Avec le groupe » ici
      out[#out + 1] = { id = "group", map = g.map,
        text = (same and fmt("À %d : « Avec le groupe » à %s", n, g.name) or fmt("À %d : %s", n, g.name))
          .. (#bits > 0 and (" — " .. tconcat(bits, ", ")) or ""),
        why = "« Avec le groupe » : vos quêtes communes d'abord, puis celles à partager.",
        label = "Avec le groupe", apply = function() QB.PickZone(g.map) QB.SetMode("group") end, prio = 1 }
    end
  end
  -- classement : courses bloquantes, quêtes finies, zone, groupe ; 3 au plus
  for i, e in ipairs(out) do e.ord = i end
  tsort(out, function(a, b) if a.prio ~= b.prio then return a.prio > b.prio end return a.ord < b.ord end)
  local keep = {}
  for i = 1, min(3, #out) do keep[i] = out[i] end
  if #keep == 0 then
    keep[1] = { id = "none", text = "Rien de sûr à conseiller pour l'instant",
      why = QB.HasQuestie() and "Aucune quête à ton niveau trouvée près de toi." or "Sans Questie : parle aux PNJ « ! » pour que TreeAI connaisse leurs quêtes.", prio = 0 }
  end
  keep.est = QB.ScanEstimate()
  return keep
end

-- ===================================================================== résumé (appli / IA) + cycle de vie
function QB:WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local picks = {}
  for i, e in ipairs(QB.PickupList(5)) do picks[i] = { q = e.questID, xp = e.xp, min = e.minutes } end
  s.questBrain = { v = 1, mode = QB.Mode(), turnin = #QB.TurnInList(), picks = picks }
end
function QB:OnEnable()
  local function bump() QB.Bump() end
  -- (ZONEPICK) quête prise / retirée, niveau : les quêtes à prendre par zone sont à recompter (un gestionnaire par évènement)
  local RESCAN = { QUEST_ACCEPTED = true, QUEST_REMOVED = true, PLAYER_LEVEL_UP = true }
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP", "GROUP_ROSTER_UPDATE" }) do
    self:RegisterEvent(ev, RESCAN[ev] and function() QB.scanGen = QB.scanGen + 1 QB.Bump() end or bump)
  end
  self:RegisterEvent("QUEST_TURNED_IN", function()
    QB.scanGen = QB.scanGen + 1
    QB.Bump()
    T:After(2, function() QB:CheckEmpty() end)
  end)
  self:On("EXPORT_UPDATED", bump)
  -- PNJ rencontrés (sans Questie) : seulement quand tu leur parles (aucune minuterie)
  self:RegisterEvent("GOSSIP_SHOW", function() pcall(QB.LearnGossip) end)
  self:RegisterEvent("QUEST_GREETING", function() pcall(QB.LearnGreeting) end)
  self:RegisterEvent("QUEST_DETAIL", function() pcall(QB.LearnDetail) end)
  self:RegisterEvent("QUEST_PROGRESS", function() pcall(QB.LearnFinisher) end)
  self:RegisterEvent("QUEST_COMPLETE", function() pcall(QB.LearnFinisher) end)
  self:On("PLAN_BUILT", function() if QB.Mode() == "pickup" then QB:CheckEmpty() end end)
  self:On("SUMMARY", function() pcall(QB.WriteSummary, QB) end)
end
function QB:OnDisable()
  local Z = T.ZonePlan
  if Z and Z.Bump then pcall(Z.Bump, Z, "cerveau des quêtes coupé") end
end

local SLASH = { rendre = "turnin", prendre = "pickup", zone = "zone", ici = "zone", groupe = "group", group = "group",
  turnin = "turnin", pickup = "pickup" }
local function slashMode(id)
  QB.SetMode(id)
  pcall(QB.Suggest, QB, "qbmode", "Mode : " .. tostring(QB.Label(id)), 1, { ttl = 5, sound = false })
end
T:RegisterSlash("rendre", function() slashMode("turnin") end, "rendre : mode « Rendre mes quêtes » (toutes tes quêtes finies, au plus vite)")
T:RegisterSlash("prendre", function() slashMode("pickup") end, "prendre : mode « Quêtes à prendre » (les meilleures près de toi)")
T:RegisterSlash("cerveau", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local id = SLASH[rest]
  if id then slashMode(id) else QB.Cycle() end
end, "cerveau : rendre · prendre · zone · groupe (mode du suivi de quêtes ; sans mot : le suivant)")
