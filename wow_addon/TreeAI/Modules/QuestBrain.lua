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
local tsort, tconcat, fmt, tinsert = table.sort, table.concat, string.format, table.insert
local function tryPass(ok, a, ...) if ok then return a, ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw(a, "QuestBrain") end return nil end
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
  { id = "fastlvl", label = "Monter vite", short = "Vite", binding = "TREEAI_QB_FASTLVL",
    tip = "Une seule ligne « Fais ça maintenant » et les 3 suivantes : le plus d'XP par heure (quêtes finies tout près, meilleur paquet, XP reposée, donjon en groupe, monstres quand les quêtes manquent)." },
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

-- ===================================================================== (5.1 ROUTE6, audit B7) nombres RÉELS des objectifs
-- QuestieDB ne donne pas « 8 » dans « Tue 8 pillards » : le nombre n'est connu que par le journal. Chaque quête vue dans TON
-- journal laisse ses nombres ici (par quête, pour tous tes personnages) : une quête pas encore prise dont le nombre est connu
-- se chiffre pour de vrai ; sinon rien n'est affiché à l'unité près (plus de « 545 XP/min » sur 8 monstres supposés).
QB.COUNT_MAX = 2000
local function countDB()
  local db = QB.db
  if type(db) ~= "table" then return nil end
  if type(db.counts) ~= "table" then db.counts = { n = 0 } end
  return db.counts
end
--- retient les nombres demandés des quêtes du journal (léger : appelé quand le journal change)
function QB.LearnCounts(log)
  local C0 = countDB()
  if not C0 then return end
  for q, lq in pairs(log or logSnap()) do
    if type(lq) == "table" and type(lq.obj) == "table" and #lq.obj > 0 and not C0[q] and (C0.n or 0) < QB.COUNT_MAX then
      local t, ok = {}, true
      for j, o in ipairs(lq.obj) do
        local n = tonumber(o.n)
        if not n or n < 1 then ok = false break end
        t[j] = n
      end
      if ok then C0[q] = t C0.n = (C0.n or 0) + 1 end
    end
  end
end
--- nombre demandé de l'objectif j de la quête q, lu un jour dans un journal → n ou nil (jamais vu)
function QB.LearnedCount(q, j)
  local C0 = countDB()
  local t = C0 and C0[tonumber(q) or -1]
  return type(t) == "table" and tonumber(t[tonumber(j) or 0]) or nil
end
--- temps de combat MESURÉ pour ce personnage (Stats, sinon l'export) ? sinon le modèle part d'une valeur de classe
function QB.KillMeasured()
  local St, Xm = T.Stats, X()
  if St and St.KillTime and St.IsEnabled and St:IsEnabled() and tonumber((try(St.KillTime, St, 0))) then return true end
  return (Xm and Xm.TTK and tonumber((try(Xm.TTK, Xm, 0)))) and true or false
end
local function round2(v)                              -- 2 chiffres significatifs : 545 → 550, 2 934 → 2 900
  v = tonumber(v) or 0
  if v <= 0 then return 0 end
  local p = 10 ^ max(0, floor(math.log(v) / math.log(10)) - 1)
  return floor(v / p + 0.5) * p
end
QB.Round2 = round2

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
  if QB.Phantom and QB.Phantom(q) then return false, "pas proposée" end   -- (4.3.5 PICKFIX) vue absente chez le PNJ
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
  local known = true                                 -- tous les nombres demandés sont RÉELS (base, ou vus dans un journal)
  for j, o in ipairs(rec.o or {}) do
    local need = tonumber(o.n) or 1
    if need <= 1 then
      local lc = QB.LearnedCount(q, j)
      if lc then need = lc
      elseif o.k == "kill" or o.k == "item" then
        known = false                                 -- inconnu : un ordre de grandeur pour CLASSER, jamais affiché
        if E and E.DEFAULT_N then need = E.DEFAULT_N[o.k or ""] or need end
      end
    end
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
  -- (4.3.5 PICKFIX) XP et XP / min affichées côte à côte ; verte (≥ 3 niveaux sous toi) : XP de quête réduite au-delà de
  -- 5 niveaux (règle Classic : 80 / 60 / 40 / 20 / 10 %), monstres presque rien → dit
  -- (5.1 ROUTE6, audit B7) un chiffre à l'écran vient de données réelles, ou n'y est pas :
  --  * XP / min seulement quand TOUT est réel — récompense (jeu / QuestieDB), nombres demandés (base ou journal déjà vu),
  --    temps de combat mesuré sur ce personnage — et alors arrondi à 2 chiffres (« ≈ 550 XP/min ») ;
  --  * sinon la récompense de la quête (réelle) et la distance, sans durée ni rythme inventés.
  local rewardReal = (tonumber(_ns((try(G("GetQuestLogRewardXP"), q)))) or 0) > 0 or (tonumber(rec.xp) or 0) > 0
  local real = rewardReal and known and not rec.learned and #(rec.o or {}) > 0 and QB.KillMeasured()
  if real then
    why[#why + 1] = fmt("%s XP en ~%d min (≈ %s XP/min)", thousands(round2(gain)), minutes, thousands(round2(score)))
  elseif rewardReal then
    why[#why + 1] = fmt("+%s XP à la remise", thousands(xp))
  else
    why[#why + 1] = "récompense inconnue"
  end
  local green = lv > 0 and ctx.level - lv >= 3
  if green then why[#why + 1] = fmt("verte (niveau %d) : XP réduite", lv) end
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
  -- xp = la récompense de la quête (réelle) ; est = vrai tant qu'un nombre ou un temps est supposé (minutes / xpm : pour
  -- classer seulement, jamais à afficher tels quels)
  return { xp = xp, gain = floor(gain + 0.5), sec = sec, travel = travel, score = score, why = why, minutes = minutes, green = green,
    xpm = real and round2(score) or nil, est = not real, rewardReal = rewardReal }
end
-- (4.3.5 PICKFIX) « il me donne des quêtes vertes pas rentables » : une verte qui rapporte moins de la moitié de l'XP / min
-- de la meilleure quête est écartée, sauf en chemin (≤ 1 min de trajet) ou pour une suite de chaîne. Pure.
QB.GREEN_ROI, QB.GREEN_ONWAY_S = 0.5, 60
function QB.DropLowGreens(list)
  local best = 0
  for _, e in ipairs(list) do best = max(best, e.score or 0) end
  local out = {}
  for _, e in ipairs(list) do
    if not (e.green and (e.score or 0) < QB.GREEN_ROI * best and not ((e.eta or huge) <= QB.GREEN_ONWAY_S) and not e.chain) then
      out[#out + 1] = e
    end
  end
  return out
end

local pickMemo
local function ctxNow()
  local P = T.Planner
  local cost = (P and P.CostCtx and try(P.CostCtx)) or { level = myLevel(), group = groupSize() }
  local log = logSnap()
  if QB._countGen ~= QB.gen then QB._countGen = QB.gen pcall(QB.LearnCounts, log) end   -- nombres réels du journal
  return { level = myLevel(), group = groupSize(), log = log, here = here(), cost = cost }
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
        why = tconcat(v.why, " · "), score = v.score, minutes = v.minutes, _b = bucket(v.score), xpm = v.xpm, green = v.green,
        est = v.est,
        chain = rec.nx and true or nil,
        elite = kind and kind.elite or nil, dungeon = kind and kind.dungeon or nil }
    end
  end
  list = QB.DropLowGreens(list)
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
  c.fastlvl = c.turnin + c.pickup
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
    if T.Export and T.Export.Dirty and id == "pickup" then pcall(T.Export.Dirty, T.Export, true) end
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

-- ===================================================================== (4.3.5 PICKFIX) quêtes « fantômes »
-- « des quêtes qu'affiche Questie n'existent pas » : quand la fenêtre d'un donneur prévu s'ouvre (GOSSIP_SHOW,
-- QUEST_GREETING, QUEST_DETAIL seul) et qu'une prise prévue chez CE PNJ n'y est pas, elle est retenue « pas proposée »
-- pour ce personnage (raison + date + niveau), sortie de « Quêtes à prendre » et du trajet ; idem pour un donneur
-- atteint (≤ 10 m, PNJ ciblé / vu) puis quitté 2 fois sans la quête. Retentée au niveau suivant.
QB.LEAVE_YD, QB.GIVER_YD, QB.SAME_NAME_YD = 40, 15, 80
local function phantoms()
  local c = QB.char
  if type(c) ~= "table" then return nil end
  if type(c.phantom) ~= "table" then c.phantom = {} end
  return c.phantom
end
--- quête écartée → { why, at, day, lv, t, by } ; nil sinon. by = "npc" (son PNJ ne la proposait pas : retentée au niveau
--- suivant) ou "player" (5.1 ROUTE6 : TU as dit « Pas là » — gardée pour ce personnage jusqu'à ce que tu la remettes)
function QB.Phantom(q)
  local ph = phantoms()
  q = tonumber(q)
  local e = ph and q and ph[q]
  if not e then return nil end
  if e.by ~= "player" and (tonumber(e.lv) or 0) < myLevel() then ph[q] = nil return nil end   -- niveau suivant : on retente
  return e
end
function QB.MarkPhantom(q, why, title, by)
  local ph = phantoms()
  q = tonumber(q)
  if not (ph and q) or QB.Phantom(q) then return false end
  ph[q] = { why = why, at = epoch(), day = _ns((try(G("date"), "%Y-%m-%d"))), lv = myLevel(), t = title, by = by or "npc" }
  QB.Bump()
  return true
end
function QB.ClearPhantom(q)
  q = tonumber(q)
  local ph = phantoms()
  if ph and q then ph[q] = nil end
  if q and type(QB.char) == "table" and type(QB.char.visits) == "table" then QB.char.visits[q] = nil end
  if q then QB._visit[q] = nil end
end
QB._visit = {}
local function sameName(a, b) return type(a) == "string" and type(b) == "string" and a ~= "" and a:lower() == b:lower() end
local function playerPt()
  local g = Geo()
  local m, x, y
  if g.Player then m, x, y = g.Player() end
  return (m and x) and { map = m, x = x, y = y } or nil
end
--- prises prévues (trajet, puis « Quêtes à prendre ») pas encore dans le journal → { { q, map, x, y, name, title } }
function QB.PlannedPicks()
  local out, seen = {}, {}
  local recs = recsOf(exportData())
  local log = logSnap()
  local function add(q, map, x, y, name, title)
    q = tonumber(q)
    if not q or seen[q] or log[q] or isDone(q) then return end
    seen[q] = true
    out[#out + 1] = { q = q, map = map, x = x, y = y, name = name, title = title }
  end
  local P = T.Planner
  for i = (P and P.cursor) or 1, #((P and P.steps) or {}) do
    local s = P.steps[i]
    if s.k == "pick" and s.q and not s._done then
      local rec = recs[tonumber(s.q)]
      local g = rec and rec.s or {}
      add(s.q, s.map or g.map, s.x or g.x, s.y or g.y, g.name or s.npc, (rec and rec.t) or QB.LearnedTitle(s.q))
      if out[#out] and out[#out].q == tonumber(s.q) then out[#out].npcId = (g.k == "npc") and tonumber(g.id) or nil end
    end
  end
  for _, e in ipairs(QB.PickupList(12)) do
    add(e.questID, e.map, e.x, e.y, e.npc, e.title)
    local rec = recs[tonumber(e.questID) or -1]
    local g = rec and rec.s
    if g and out[#out] and out[#out].q == tonumber(e.questID) then out[#out].npcId = (g.k == "npc") and tonumber(g.id) or nil end
  end
  return out
end
local function fromNpc(c, npc, me)
  local d = me and c.x and distYd(me, c)
  if c.name and npc then return sameName(c.name, npc) and (d == nil or d <= QB.SAME_NAME_YD) end
  return d ~= nil and d <= QB.GIVER_YD
end
local function phantomSaid(n, npc)
  if n <= 0 then return end
  replanNow("quête non proposée")
  pcall(QB.Suggest, QB, "phantom", fmt("%d quête%s pas proposée%s par %s : écartée%s du trajet (à remettre : « Quêtes écartées »)",
    n, n > 1 and "s" or "", n > 1 and "s" or "", npc or "ce PNJ", n > 1 and "s" or ""), 1,
    { ttl = 10, sound = false, onClick = function() QB.ShowDismissed() end })
  if T.Fire then pcall(T.Fire, T, "QUEST_DISMISSED") end
end
--- la fenêtre d'un PNJ montre ce qu'il propose (offered = { ids = {}, titles = {} } ; nil = on ne sait pas) → nombre retiré
function QB.CheckOffered(offered)
  if type(offered) ~= "table" then return 0 end
  if _ns((try(G("UnitIsPlayer"), "npc"))) then return 0 end          -- quête partagée par un joueur : rien à juger
  local npc = _ns((try(G("UnitName"), "npc")))
  if type(npc) ~= "string" or npc == "" then npc = nil end
  local me = playerPt()
  local n = 0
  for _, c in ipairs(QB.PlannedPicks()) do
    local judge = next(offered.ids) ~= nil or type(c.title) == "string" or (not next(offered.titles))   -- sans id ni titre : on ne sait pas
    if judge and fromNpc(c, npc, me) and not offered.ids[c.q] and not (type(c.title) == "string" and offered.titles[c.title:lower()]) then
      if QB.MarkPhantom(c.q, "pas proposée par " .. (npc or "le PNJ"), c.title) then n = n + 1 end
    end
  end
  phantomSaid(n, npc)
  return n
end
local function titleSet(ok, ...)
  if not ok then return nil end
  local t = {}
  for i = 1, select("#", ...) do
    local v = _ns((select(i, ...)))
    if type(v) == "string" and v ~= "" then t[v:lower()] = true end
  end
  return t
end
--- ce que propose la fenêtre ouverte : "gossip" | "greeting" | "detail" → { ids, titles } ou nil (illisible)
function QB.Offered(kind)
  local ids, titles = {}, {}
  if kind == "gossip" then
    local GI = G("C_GossipInfo")
    if type(GI) == "table" and GI.GetAvailableQuests then
      local list = try(GI.GetAvailableQuests)
      if type(list) ~= "table" then return nil end
      for _, a in ipairs(list) do
        if type(a) == "table" then
          local q, t = tonumber(_ns(a.questID)), _ns(a.title)
          if q then ids[q] = true end
          if type(t) == "string" then titles[t:lower()] = true end
        end
      end
      return { ids = ids, titles = titles }
    end
    local f = G("GetGossipAvailableQuests")                   -- 4.3.4 : titre, niveau, … à plat
    if type(f) ~= "function" then return nil end
    titles = titleSet(pcall(f))
    return titles and { ids = ids, titles = titles } or nil
  elseif kind == "greeting" then
    local nA = tonumber(_ns((try(G("GetNumAvailableQuests"))))) or 0
    for i = 1, min(nA, 12) do
      local q = tonumber(_ns((select(5, try(G("GetAvailableQuestInfo"), i)))))
      local t = _ns((try(G("GetAvailableTitle"), i)))
      if q then ids[q] = true end
      if type(t) == "string" and t ~= "" then titles[t:lower()] = true end
    end
    if nA > 0 and not next(ids) and not next(titles) then return nil end
    return { ids = ids, titles = titles }
  elseif kind == "detail" then
    local q, t = questIDNow(), _ns((try(G("GetTitleText"))))
    if not q and type(t) ~= "string" then return nil end
    if q then ids[q] = true end
    if type(t) == "string" and t ~= "" then titles[t:lower()] = true end
    return { ids = ids, titles = titles }
  end
end
QB.LIST_HOLD_S = 120
function QB.OnQuestWindow(kind)
  pcall(QB.NoteNpcWindow)                              -- (5.1 ROUTE6) ce PNJ existe : jamais « pas là » pour lui
  local npc = _ns((try(G("UnitName"), "npc")))
  if kind == "detail" then
    -- une quête ouverte depuis la liste d'un PNJ (gossip / accueil) : sa liste a déjà été jugée
    local l = QB._listFrom
    if l and sameName(l.n, npc) and now() - l.at < QB.LIST_HOLD_S then return 0 end
  else
    QB._listFrom = { n = npc, at = now() }
  end
  return QB.CheckOffered(QB.Offered(kind))
end
-- ===================================================================== (5.1 ROUTE6, audit B4) le donneur n'est pas là
-- « des quêtes qu'affiche Questie n'existent pas » — fait comme il faut :
--  * JAMAIS une quête retirée parce que tu es passé devant son PNJ (l'ancienne règle « atteint 2 fois puis quitté » retirait
--    toutes les quêtes d'un PNJ croisé deux fois avec la souris dessus) ;
--  * le cas réel : tu es SUR le point du donneur (≤ QB.ABSENT_YD) depuis QB.ABSENT_S secondes, hors combat, et PERSONNE de
--    ce nom / cet identifiant n'a été vu pendant ce temps — ni ciblé, ni survolé, ni barre de nom, ni fenêtre de PNJ ouverte
--    → UNE petite question « Ce PNJ n'est pas là ? » avec deux réponses : « Pas là » (ses quêtes sont écartées, gardé pour ce
--    personnage) et « Je reviens plus tard » (rien n'est écarté ; l'arrêt est passé pour cette session) ;
--  * tout ce qui est écarté est LISIBLE et se remet d'un clic : QB.DismissedList() (ligne « Quêtes écartées (n) » du
--    trajet, page « Pilote auto »), QB.Restore(q).
-- Coût : rien tant que tu n'es pas à ≤ 15 yd d'une prise prévue ; sur place, une lecture toutes les 3 s (cible, survol,
-- ≤ 40 barres de nom), jamais en combat.
QB.ABSENT_YD, QB.ABSENT_S, QB.ABSENT_AGAIN_S = 15, 20, 600
local function npcIdOf(unit)
  local g = _ns((try(G("UnitGUID"), unit)))
  if type(g) ~= "string" then return nil end
  local kind, id = g:match("^(%a+)%-%d+%-%d+%-%d+%-%d+%-(%d+)")
  if kind ~= "Creature" and kind ~= "Vehicle" and kind ~= "GameObject" then return nil end
  return tonumber(id)
end
local function unitIs(unit, name, npcId)
  if not try(G("UnitExists"), unit) then return false end
  if npcId and npcIdOf(unit) == npcId then return true end
  return sameName(name, _ns((try(G("UnitName"), unit))))
end
--- preuve que le PNJ est là : ciblé, survolé, en discussion, ou une barre de nom à son nom → vrai / faux
function QB.GiverSeen(name, npcId)
  if not (name or npcId) then return false end
  if unitIs("target", name, npcId) or unitIs("mouseover", name, npcId) or unitIs("npc", name, npcId) then return true end
  -- barres de nom : par jeton d'unité (« nameplate1 »…), jamais par leurs cadres (interdits aux addons sur Forever)
  local exists = G("UnitExists")
  if type(exists) ~= "function" then return false end
  for i = 1, 40 do
    if unitIs("nameplate" .. i, name, npcId) then return true end
  end
  return false
end
--- une fenêtre de PNJ s'ouvre (gossip / accueil / détail) : ce PNJ existe — plus jamais « pas là » pour lui cette visite
function QB.NoteNpcWindow()
  local name, id = _ns((try(G("UnitName"), "npc"))), npcIdOf("npc")
  local me = playerPt()
  for q, v in pairs(QB._visit) do
    if (v.npcId and v.npcId == id) or sameName(v.name, name) or (me and v.pt and (distYd(me, v.pt) or huge) <= QB.GIVER_YD) then
      v.seen = true
      if QB._absent and QB._absent.qs[q] then QB._absent = nil pcall(QB.Clear, QB, "absent") end
    end
  end
end
--- réponse « Pas là » : les quêtes de ce donneur sont écartées (gardé pour ce personnage, remis d'un clic)
function QB.AbsentYes()
  local a = QB._absent
  QB._absent = nil
  pcall(QB.Clear, QB, "absent")
  if not a then return 0 end
  local n = 0
  for q, title in pairs(a.qs) do
    if QB.MarkPhantom(q, "PNJ absent : " .. tostring(a.name or "donneur"), type(title) == "string" and title or nil, "player") then n = n + 1 end
  end
  if n > 0 then
    replanNow("PNJ absent (choix du joueur)")
    pcall(QB.Suggest, QB, "phantom", fmt("%d quête%s écartée%s (PNJ absent) · à remettre : « Quêtes écartées » dans le trajet",
      n, n > 1 and "s" or "", n > 1 and "s" or ""), 1, { ttl = 10, sound = false, onClick = function() QB.ShowDismissed() end })
    if T.Fire then pcall(T.Fire, T, "QUEST_DISMISSED") end
  end
  return n
end
--- réponse « Je reviens plus tard » : rien n'est écarté ; ses quêtes sortent du trajet pour cette session seulement
function QB.AbsentLater()
  local a = QB._absent
  QB._absent = nil
  pcall(QB.Clear, QB, "absent")
  if not a then return 0 end
  local qs = {}
  for q in pairs(a.qs) do QB.skipped[q] = true qs[#qs + 1] = q end
  if #qs == 0 then return 0 end
  tsort(qs)
  QB.skipOrder[#QB.skipOrder + 1] = qs
  replanNow("PNJ absent : plus tard")
  if T.Fire then pcall(T.Fire, T, "QUEST_SKIPPED", qs) end
  return #qs
end
--- le menu à deux réponses (cadre à nous : celui du trajet) ; sans le menu, la ligne elle-même : clic = « Pas là »
function QB.AbsentMenu()
  local a = QB._absent
  if not a then return false end
  local Z = T.ZonePlan
  if Z and Z.ShowMenu then
    return Z:ShowMenu({ { text = cut(tostring(a.name or "Ce PNJ"), 30) .. " n'est pas là ?", head = true },
      { text = "Pas là", fn = function() QB.AbsentYes() end },
      { text = "Je reviens plus tard", fn = function() QB.AbsentLater() end } }, "absent")
  end
  return QB.AbsentYes() > 0
end
local function askAbsent(c, group)
  local qs, n = {}, 0
  for _, g in ipairs(group) do qs[g.q] = g.title or true n = n + 1 end
  QB._absent = { name = c.name, npcId = c.npcId, qs = qs, at = now(), n = n }
  QB._askedAt = QB._askedAt or {}
  QB._askedAt[c.name or c.q] = now()
  local NTr = T.NavTrace
  if NTr and NTr.Log then pcall(NTr.Log, "quête", fmt("donneur introuvable sur place depuis %d s : %s (%d quête%s) → question posée",
    QB.ABSENT_S, tostring(c.name or "?"), n, n > 1 and "s" or "")) end
  pcall(QB.Suggest, QB, "absent", fmt("Ce PNJ n'est pas là ? %s%s", cut(tostring(c.name or "le donneur"), 28),
      n > 1 and fmt(" (%d quêtes)", n) or (type(c.title) == "string" and (" (« " .. cut(c.title, 26) .. " »)") or "")), 3,
    { ttl = 90, sound = false, cooldown = 0, tab = "now", topic = "quest", onClick = function() QB.AbsentMenu() end,
      tooltip = { "Tu es sur son point depuis " .. QB.ABSENT_S .. " s et personne de ce nom n'a été vu (cible, survol, barres de nom).",
        "Clic : [Pas là] (ses quêtes sont écartées, à remettre d'un clic) ou [Je reviens plus tard]" } })
end
--- (toutes les 3 s en marchant, par le Planner ; puis à l'échéance, sur place) → nombre de questions posées
function QB.VisitTick()
  local me, g = playerPt(), Geo()
  if not (me and g.Dist and type(QB.char) == "table") then return 0 end
  if T.InCombat and T:InCombat() then return 0 end
  local t = now()
  local asked = 0
  local picks = QB.PlannedPicks()
  local live = {}
  for _, c in ipairs(picks) do
    local d = c.x and c.map and g.Dist(me.map, me.x, me.y, c.map, c.x, c.y)
    local v = QB._visit[c.q]
    live[c.q] = true
    if d and d <= QB.ABSENT_YD then
      if not v then
        v = { since = t, name = c.name, npcId = c.npcId, pt = { map = c.map, x = c.x, y = c.y } }
        QB._visit[c.q] = v
        if T.After and not QB._visitTimer then           -- immobile sur place : le ticker du trajet ne passe plus
          QB._visitTimer = true
          T:After(QB.ABSENT_S + 1, function() QB._visitTimer = nil pcall(QB.VisitTick) end)
        end
      end
      if not v.seen and QB.GiverSeen(c.name, c.npcId) then v.seen = true end
    elseif v and d and d > QB.LEAVE_YD then
      QB._visit[c.q] = nil                              -- reparti : rien n'est compté, rien n'est retiré
      if QB._absent and QB._absent.qs[c.q] then QB._absent = nil pcall(QB.Clear, QB, "absent") end
    end
  end
  for q in pairs(QB._visit) do if not live[q] then QB._visit[q] = nil end end   -- prise entre-temps / plus prévue
  -- la question : un donneur NOMMÉ, jamais vu depuis ABSENT_S secondes sur son point, et tu es venu POUR lui (sa prise est
  -- l'arrêt en cours du trajet : passer 20 s près d'un PNJ qu'on ne regarde pas n'est pas une question) ; une à la fois
  local cur = {}
  do
    local P = T.Planner
    local s0 = P and P.steps and P.steps[P.cursor or 0]
    if s0 and s0.x then
      for i = P.cursor, math.min(#P.steps, P.cursor + 8) do
        local s2 = P.steps[i]
        if s2.k == "pick" and s2.q and not s2._done and not s2._other and s2.map == s0.map and (distYd(s0, s2) or huge) <= QB.GIVER_YD then cur[tonumber(s2.q)] = true end
      end
    end
  end
  if not QB._absent then
    for _, c in ipairs(picks) do
      local v = QB._visit[c.q]
      if v and cur[c.q] and not v.seen and not v.asked and (c.name or c.npcId) and t - v.since >= QB.ABSENT_S
          and t - ((QB._askedAt or {})[c.name or c.q] or -1e9) >= QB.ABSENT_AGAIN_S then
        local group = {}
        for _, c2 in ipairs(picks) do
          local v2 = QB._visit[c2.q]
          if v2 and not v2.seen and ((c.npcId and c2.npcId == c.npcId) or sameName(c.name, c2.name)) then
            v2.asked = true
            group[#group + 1] = c2
          end
        end
        if #group > 0 then askAbsent(c, group) asked = asked + 1 end
        break
      end
    end
  end
  return asked
end

-- ===================================================================== (5.1 ROUTE6) « Quêtes écartées » : la liste, remettre
--- tout ce que TreeAI tient hors du trajet pour ce personnage, avec la raison → { { q, title, why, kind, at } }
--- kind = "absent" (tu as dit « Pas là ») | "npc" (son PNJ ne la proposait pas) | "ignore" (clic droit : ignorer) |
--- "later" (passée pour cette session : « Suivant », « Je reviens plus tard »)
function QB.DismissedList()
  local out = {}
  local recs = recsOf(exportData())
  local function title(q, t) return (type(t) == "string" and t ~= "" and t) or (recs[q] and recs[q].t) or questName(q) or ("quête " .. q) end
  for q in pairs(phantoms() or {}) do
    local e = QB.Phantom(q)
    if e then
      out[#out + 1] = { q = q, title = title(q, e.t), why = e.why or "pas proposée", kind = (e.by == "player") and "absent" or "npc", at = e.at }
    end
  end
  local Z = T.ZonePlan
  for q, t in pairs(Z and Z.char and Z.char.ignore or {}) do
    q = tonumber(q)
    if q then out[#out + 1] = { q = q, title = title(q, t), why = "ignorée par toi", kind = "ignore" } end
  end
  for q in pairs(QB.skipped) do
    out[#out + 1] = { q = q, title = title(q), why = "passée pour cette session", kind = "later" }
  end
  tsort(out, function(a, b) if a.kind ~= b.kind then return a.kind < b.kind end return a.q < b.q end)
  return out
end
--- remet une quête écartée (nil = toutes) dans le trajet → nombre remis
function QB.Restore(q)
  local n = 0
  local Z = T.ZonePlan
  for _, e in ipairs(QB.DismissedList()) do
    if q == nil or e.q == tonumber(q) then
      if e.kind == "absent" or e.kind == "npc" then QB.ClearPhantom(e.q)
      elseif e.kind == "ignore" then if Z and Z.char and Z.char.ignore then Z.char.ignore[e.q] = nil end
      else QB.skipped[e.q] = nil end
      n = n + 1
    end
  end
  if n > 0 then
    if q == nil then QB.skipOrder = {} end
    QB.Bump()
    replanNow("quête remise")
    if T.Fire then pcall(T.Fire, T, "QUEST_DISMISSED") end
  end
  return n
end
local KIND_TXT = { absent = "PNJ absent", npc = "pas proposée", ignore = "ignorée", later = "plus tard" }
--- le menu « Quêtes écartées » (cadre à nous) : une ligne par quête, clic = la remettre
function QB.ShowDismissed()
  local Z = T.ZonePlan
  if not (Z and Z.ShowMenu) then return false end
  local list = QB.DismissedList()
  local ent = { { text = fmt("Quêtes écartées (%d) — clic : remettre", #list), head = true } }
  for i, e in ipairs(list) do
    if i > 14 then ent[#ent + 1] = { text = fmt("… et %d autres", #list - 14), dim = true } break end
    local q = e.q
    ent[#ent + 1] = { text = fmt("« %s » · %s", cut(e.title, 30), KIND_TXT[e.kind] or e.kind), fn = function() QB.Restore(q) end }
  end
  if #list > 1 then ent[#ent + 1] = { text = "Tout remettre", fn = function() QB.Restore(nil) end } end
  if #list == 0 then ent[#ent + 1] = { text = "Aucune quête écartée", dim = true } end
  return Z:ShowMenu(ent, "dismissed")
end
--- la ligne du trajet (ZonePlan:HeadLines) ; nil quand rien n'est écarté
function QB.DismissedLine()
  local list = QB.DismissedList()
  if #list == 0 then return nil end
  local tip = { "Hors du trajet pour ce personnage, avec la raison :" }
  for i, e in ipairs(list) do
    if i > 10 then tip[#tip + 1] = "…" break end
    tip[#tip + 1] = fmt("« %s » : %s", cut(e.title, 34), e.why)
  end
  tip[#tip + 1] = "Clic : la liste, un clic pour remettre"
  return { text = fmt("Quêtes écartées (%d) ▾", #list), prio = 1, id = "qb:dismissed", tooltip = tconcat(tip, "\n"),
    onClick = function() QB.ShowDismissed() end, onRightClick = function() QB.ShowDismissed() end }
end

-- ===================================================================== (4.3.5 PICKFIX) mode « Monter vite » (fastlvl)
-- « Fais ça maintenant » + les 3 suivants, le plus d'XP par heure d'après les données réelles : quêtes finies tout près
-- d'abord, puis le meilleur paquet (XP / min trajet compris : Route / PathLearn), l'XP reposée (monstres × 2), le donjon
-- en groupe (≥ 4, niveau dedans), un coin à monstres appris (XP et temps entre deux morts) quand les quêtes manquent,
-- vol / pierre de foyer quand le trajet les prend. Chaque étape : XP, temps, pourquoi.
QB.FAST_NEAR_S = 180
QB.GRIND_MIN_KILLS = 5
local function grindDB()
  local db = QB.db
  if type(db) ~= "table" then return nil end
  if type(db.grind) ~= "table" then db.grind = {} end
  return db.grind
end
--- XP d'un monstre tué (CHAT_MSG_COMBAT_XP_GAIN) : le coin (zone, carré de 3 %) apprend XP et temps entre deux morts
function QB.LearnKill(xp, name)
  xp = tonumber(xp)
  local me, G0 = playerPt(), grindDB()
  if not (xp and xp > 0 and me and G0) then return end
  local key = fmt("%d:%d:%d", me.map, floor(me.x / 3), floor(me.y / 3))
  local e = G0[key] or { m = me.map, x = floor(me.x / 3) * 3 + 1.5, y = floor(me.y / 3) * 3 + 1.5, xp = 0, sec = 0, n = 0 }
  local t = now()
  local gap = QB._lastKill and t - QB._lastKill or nil
  QB._lastKill = t
  e.n, e.xp, e.lv, e.name, e.at = e.n + 1, e.xp + xp, myLevel(), name or e.name, epoch()
  e.sec = e.sec + ((gap and gap <= 90) and gap or 30)
  G0[key] = e
  prune(G0)
end
function QB.ParseXP(msg)
  msg = _ns(msg)
  if type(msg) ~= "string" then return nil end
  local xp = msg:match("(%d+) points? d'expérience") or msg:match("(%d+) experience")
  local name = msg:match("^(.-) meurt") or msg:match("^(.-) dies")
  return tonumber(xp), name
end
--- meilleur coin à monstres appris (niveau à ±2) → { map, x, y, rate (XP/s), name } ou nil
function QB.BestGrind()
  local best, L = nil, myLevel()
  for _, e in pairs(grindDB() or {}) do
    if e.n >= QB.GRIND_MIN_KILLS and e.sec > 0 and math.abs((e.lv or L) - L) <= 2 then
      local r = e.xp / e.sec
      if not best or r > best.rate then best = { map = e.m, x = e.x, y = e.y, rate = r, name = e.name } end
    end
  end
  return best
end
local function restedXP() return tonumber(_ns((try(G("GetXPExhaustion"))))) or 0 end
local function meters(yd)
  local m = (tonumber(yd) or 0) * 0.9144
  if m < 1000 then return fmt("%d m", floor(m / 10 + 0.5) * 10) end
  return (fmt("%.1f km", m / 1000):gsub("%.", ","))
end
-- (5.1 ROUTE6, audit B6) UNE SEULE RÉPONSE à « que faire maintenant ». La boussole, la bande, la ligne « Arrêt 1/N » de
-- « Maintenant » lisent l'arrêt 1 du trajet (Route:Stops) ; « Vite » aussi, désormais : en mode « Monter vite » le trajet
-- EST le plan (ZonePlan le calcule au plus d'XP par heure), et « Fais ça maintenant » + les 3 suivantes sont ses 4 premiers
-- arrêts, mêmes libellés. (5.0.0 : une 2e liste classée à part disait « Rends X à Mangeclous · finie, tout près » — une
-- autre zone, à un vol de là — pendant que la boussole et le trajet disaient « Tue « Pillard des collines » » ici.)
-- Chiffres : la distance réelle, l'XP RÉELLE des remises de l'arrêt ; « tout près » seulement à ≤ 100 m (110 yd).
--- les n premiers arrêts du trajet → { { text, kind, q, qs, dist (yd), sec (arrivée), xp (remises prêtes), transport } }
function QB.RouteSteps(n)
  n = n or 4
  local R, P = T.Route, T.Planner
  local out = {}
  if not (R and R.Stops and R.Groups and R.StopLabel and P and P.steps and R.IsEnabled and R:IsEnabled()) then return out end
  local ok, st = pcall(R.Stops, R, 64)
  if not (ok and type(st) == "table") then return out end
  local grp = R.Groups(st)
  local recs = recsOf(exportData())
  local log = logSnap()
  local me = playerPt()
  local level = myLevel()
  local i = 1
  while i <= #st and #out < n do
    local e = st[i]
    if e.step then
      local label, last = R.StopLabel(P, st, i, grp)
      local s = e.step
      local c = { text = label or (P.StepLabel and P:StepLabel(s)) or "?", kind = s.k, q = tonumber(s.q), qs = {}, sec = e.at,
        dist = (me and s.x) and distYd(me, s) or nil, xp = 0 }
      for j = i, last or i do
        local sj = st[j].step
        local q = sj and tonumber(sj.q)
        if q then
          c.qs[#c.qs + 1] = q
          if sj.k == "turn" and log[q] and log[q].done == 1 then c.xp = c.xp + rewardXP(q, recs[q], log[q].lv, level) end
        end
        c.sec = st[j].at or c.sec
      end
      out[#out + 1] = c
      i = (last or i) + 1
    else
      -- vol / bateau / zeppelin / pierre de foyer : un arrêt du trajet comme les autres
      out[#out + 1] = { text = tostring(e.text or "?"), kind = e.kind, transport = true, sec = e.at, xp = 0, qs = {} }
      i = i + 1
    end
  end
  return out
end
--- l'étape à faire MAINTENANT (la même partout) ; nil sans trajet
function QB.NextStep() return QB.RouteSteps(1)[1] end
--- → { now = étape, nexts = { 3 étapes }, rested } ; étape = { kind, text, xp, dist, sec, why, q }
function QB.FastPlan()
  local rested = restedXP()
  local steps = QB.RouteSteps(4)
  for _, c in ipairs(steps) do
    local w = {}
    if c.dist and c.dist <= 110 then w[#w + 1] = "tout près" elseif c.dist then w[#w + 1] = "à " .. meters(c.dist) end
    if c.xp and c.xp > 0 then w[#w + 1] = fmt("+%s XP à la remise", thousands(c.xp)) end
    if c.kind == "obj" and rested > 0 then w[#w + 1] = "repos : XP des monstres × 2" end
    if c.kind == "hs" then w[#w + 1] = "la pierre de foyer est le plus court vers la suite"
    elseif c.transport then w[#w + 1] = "le plus court vers la suite" end
    c.why = tconcat(w, " · ")
  end
  -- le trajet s'arrête avant 4 arrêts (plus de quête après) : le coin à monstres APPRIS (XP et temps mesurés entre deux
  -- morts) vient APRÈS ses arrêts, dit comme une mesure — jamais à la place de l'arrêt 1
  if #steps < 4 then
    local gr = QB.BestGrind()
    if gr then
      local me = playerPt()
      local rate = gr.rate * (rested > 0 and 2 or 1)
      steps[#steps + 1] = { kind = "grind", xp = 0, dist = me and distYd(me, gr) or nil, qs = {},
        text = fmt("Tue des monstres%s (%.0f, %.0f)", gr.name and (" « " .. cut(gr.name, 24) .. " »") or "", gr.x, gr.y),
        why = fmt("plus de quête dans le trajet · %s XP/h mesurés ici%s", thousands(round2(rate * 3600)), rested > 0 and " · XP reposée × 2" or "") }
    end
  end
  return { now = steps[1], nexts = { steps[2], steps[3], steps[4] }, rested = rested > 0 }
end
local function stepText(c)
  if c.why and c.why ~= "" then return fmt("%s — %s", c.text, c.why) end
  return tostring(c.text)
end
QB.FastStepText = stepText
--- lignes du mode « Monter vite » : « Fais ça maintenant : … » puis les 3 arrêts suivants du trajet
function QB.FastLines()
  local p = QB.FastPlan()
  local out = {}
  if not p.now then
    out[1] = { text = "Fais ça maintenant : rien de rentable connu ici — choisis une zone (/tai zone)", prio = 1 }
    return out
  end
  out[1] = { text = "Fais ça maintenant : " .. stepText(p.now), prio = 2, id = "qb:now",
    tooltip = "La même étape que la boussole, la bande et la carte : l'arrêt 1 du trajet" }
  for i = 1, 3 do
    local c = p.nexts[i]
    if c then out[#out + 1] = { text = fmt("%d. %s", i, stepText(c)), prio = 1 } end
  end
  return out
end

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
-- ===================================================================== (4.2 ROUTE42) histoires : « Chapitre n sur N »
-- La suite d'une quête se lit dans QuestieDB (lecture seule) : on remonte les prérequis jusqu'à la tête, puis on avance par
-- « quête suivante ». « sur N » seulement pour une chaîne PROUVÉE : aucune bifurcation (un seul prérequis à chaque pas, la
-- suivante n'a que celle-ci comme prérequis), aucune boucle, fin connue, et la quête est bien sur ce chemin ; sinon
-- « Chapitre n » seul. Une quête seule (ni avant ni après) n'a pas de chapitre.
QB.CHAIN_MAX = 30
local function preList(db, q)
  local single = db.Q(q, "preQuestSingle")
  if type(single) == "table" and #single > 0 then return single end
  local grp = db.Q(q, "preQuestGroup")
  if type(grp) == "table" and #grp > 0 then return grp end
  return nil
end
--- (pur sur db = { Q = fn(id, champ) }) → { head, n, N (nil = pas prouvée), len } ou nil (quête seule)
function QB.Chain(db, q)
  q = tonumber(q)
  if not (q and type(db) == "table" and type(db.Q) == "function") then return nil end
  local cur, depth, proven, seen = q, 0, true, { [q] = true }
  while true do
    local list = preList(db, cur)
    if not list then break end
    if #list > 1 then proven = false end
    local p = tonumber(list[1])
    if not p or seen[p] then proven = false break end
    seen[p] = true
    cur, depth = p, depth + 1
    if depth >= QB.CHAIN_MAX then proven = false break end
  end
  local head, len, onPath, seen2 = cur, 1, cur == q, { [cur] = true }
  while true do
    local nx = tonumber(db.Q(cur, "nextQuestInChain"))
    if not nx or nx <= 0 then break end
    if seen2[nx] then proven = false break end
    local pre = preList(db, nx)
    if pre and not (#pre == 1 and tonumber(pre[1]) == cur) then proven = false end
    seen2[nx] = true
    cur, len = nx, len + 1
    if nx == q then onPath = true end
    if len >= QB.CHAIN_MAX then proven = false break end
  end
  if len <= 1 and depth == 0 then return nil end
  if not onPath then proven = false end
  return { head = head, n = depth + 1, N = proven and len or nil, len = len }
end
local chapMemo = {}
function QB.Chapter(q)
  q = tonumber(q)
  if not q then return nil end
  local m = chapMemo[q]
  if m ~= nil then return m or nil end
  local Xm = X()
  local db = Xm and Xm.Source and try(Xm.Source, Xm)
  if not db then return nil end                      -- sans Questie : rien (on ne devine pas une histoire)
  local ok, c = pcall(QB.Chain, db, q)
  c = ok and c or nil
  chapMemo[q] = c or false
  return c
end
--- quête rendue : la dernière d'une histoire prouvée → « Histoire terminée : X », une fois par personnage (dans « Maintenant »)
function QB.StoryDone(q)
  local c = QB.Chapter(q)
  if not (c and c.N and c.n == c.N and QB.char) then return nil end
  QB.char.storyDone = type(QB.char.storyDone) == "table" and QB.char.storyDone or {}
  if QB.char.storyDone[c.head] then return nil end
  QB.char.storyDone[c.head] = true
  local Xm = X()
  local db = Xm and Xm.Source and try(Xm.Source, Xm)
  local nm = db and db.Q and try(db.Q, c.head, "name")
  local txt = "Histoire terminée" .. ((type(nm) == "string" and nm ~= "") and (" : « " .. (T.Cut and T.Cut(nm, 34) or nm) .. " »") or "")
  pcall(QB.Suggest, QB, "story", txt, 1, { ttl = 10, sound = false })
  return txt
end

function QB:OnEnable()
  local function bump() QB.Bump() end
  -- (ZONEPICK) quête prise / retirée, niveau : les quêtes à prendre par zone sont à recompter (un gestionnaire par évènement)
  local RESCAN = { QUEST_ACCEPTED = true, QUEST_REMOVED = true, PLAYER_LEVEL_UP = true }
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP", "GROUP_ROSTER_UPDATE" }) do
    self:RegisterEvent(ev, RESCAN[ev] and function(_, _, a, b)
      if ev == "QUEST_ACCEPTED" then pcall(QB.ClearPhantom, tonumber(_ns(b)) or tonumber(_ns(a))) end   -- (4.3.5) vraiment prise
      QB.scanGen = QB.scanGen + 1 QB.Bump()
    end or bump)
  end
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, q)
    pcall(QB.StoryDone, tonumber(_ns(q)))
    QB.scanGen = QB.scanGen + 1
    QB.Bump()
    T:After(2, function() QB:CheckEmpty() end)
  end)
  self:On("EXPORT_UPDATED", bump)
  -- PNJ rencontrés (sans Questie) : seulement quand tu leur parles (aucune minuterie)
  self:RegisterEvent("GOSSIP_SHOW", function() pcall(QB.LearnGossip) pcall(QB.OnQuestWindow, "gossip") end)
  self:RegisterEvent("QUEST_GREETING", function() pcall(QB.LearnGreeting) pcall(QB.OnQuestWindow, "greeting") end)
  self:RegisterEvent("QUEST_DETAIL", function() pcall(QB.LearnDetail) pcall(QB.OnQuestWindow, "detail") end)
  -- (4.3.5 PICKFIX) monstre tué : le coin à monstres apprend
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg)
    local xp, name = QB.ParseXP(msg)
    if xp then pcall(QB.LearnKill, xp, name) end
  end)
  -- (5.1 ROUTE6, audit B4) preuve que le donneur est là : tu l'as ciblé ou survolé pendant que tu es sur son point (rien
  -- n'est lu tant qu'aucune visite n'est ouverte : une comparaison et c'est tout)
  local function sawUnit(unit)
    if next(QB._visit) == nil then return end
    for _, v in pairs(QB._visit) do
      if not v.seen and unitIs(unit, v.name, v.npcId) then
        v.seen = true
        if QB._absent then QB._absent = nil pcall(QB.Clear, QB, "absent") end
      end
    end
  end
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", function() sawUnit("mouseover") end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() sawUnit("target") end)
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
  turnin = "turnin", pickup = "pickup", vite = "fastlvl", fastlvl = "fastlvl" }
local function slashMode(id)
  QB.SetMode(id)
  pcall(QB.Suggest, QB, "qbmode", "Mode : " .. tostring(QB.Label(id)), 1, { ttl = 5, sound = false })
end
T:RegisterSlash("rendre", function() slashMode("turnin") end, "rendre : mode « Rendre mes quêtes » (toutes tes quêtes finies, au plus vite)")
T:RegisterSlash("vite", function()
  slashMode("fastlvl")
  local ok, l = pcall(QB.FastLines)
  if ok and l and l[1] then pcall(QB.Suggest, QB, "fastlvl", l[1].text, 2, { ttl = 20, sound = false }) end
end, "vite : mode « Monter vite » (« Fais ça maintenant » + les 3 suivantes, le plus d'XP par heure)")
T:RegisterSlash("prendre", function() slashMode("pickup") end, "prendre : mode « Quêtes à prendre » (les meilleures près de toi)")
T:RegisterSlash("cerveau", function(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local id = SLASH[rest]
  if id then slashMode(id) else QB.Cycle() end
end, "cerveau : rendre · prendre · zone · groupe (mode du suivi de quêtes ; sans mot : le suivant)")
