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
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.QuestBrain = true end   -- guidage de base : allumé en mode sûr
local QB = T:RegisterModule("QuestBrain", {
  title = "Cerveau des quêtes",
  description = "Quatre modes d'un clic : rendre mes quêtes, quêtes à prendre, tout faire ici, avec le groupe.",
  defaults = { enabled = true, autoBack = true, pickRoute = 6 },
  charDefaults = { mode = "zone" },
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
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP", "GROUP_ROSTER_UPDATE" }) do
    self:RegisterEvent(ev, bump)
  end
  self:RegisterEvent("QUEST_TURNED_IN", function()
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
