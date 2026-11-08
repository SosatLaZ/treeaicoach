-- TreeAI — module « Quêtes à retirer » (agent ABANDON, remplace la fonction 2 de Pro.lua).
-- Pour chaque quête du journal : verdict Garder / Plus tard / À abandonner, un score sur 100 et le POURQUOI
-- en français simple (« Abandonne « X » : grise (tu as 8 niveaux de plus), 120 XP seulement pour ~14 min de
-- trajet et de farm → 9 XP/min contre 410 XP/min pour tes autres quêtes ; elle n'ouvre aucune suite. »).
-- Facteurs : XP maintenant et au rendu (baisse 80/60/40/20/10 % au-delà de niv+5), temps (trajet Shortest
-- Path / Compass, monstres × temps par monstre mesuré, taux de drop mesuré par TreeAI.Predict), difficulté
-- (écart de niveau, élite, groupe suggéré, donjon, escorte), valeur de chaîne (suites Questie), récompense
-- (amélioration pour ta classe via Boost, prix de vente), place dans le journal, amis qui l'ont (Squad),
-- zone (quête isolée, loin, zone que tu quittes).
-- RIEN n'est fait à la place du joueur : jamais d'abandon, jamais de sélection dans le journal de Blizzard
-- (taint 3.8.2). Un clic affiche seulement la marche à suivre. Calculs hors combat (valeurs secrètes).
-- Sortie : onglet « Quêtes », bloc « À retirer » (TreeAI:SetTabLines, source "Abandon") + /tai abandon.
-- Le cœur (A.*) est pur (aucun appel WoW) : testé seul par tests/wow/abandon_spec.lua. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Abandon", {
  title = "Quêtes à retirer",
  defaults = { enabled = true, maxLines = 6, showLater = true, pin = true },
  options = {
    { key = "showLater", type = "toggle", label = "Montrer aussi « à faire plus tard »", section = "Affichage" },
    { key = "pin", type = "toggle", label = "Journal presque plein : alerte dans « Maintenant »", section = "Affichage" },
    { key = "maxLines", type = "range", label = "Lignes max", min = 2, max = 20, step = 1, section = "Affichage" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Abandon then T.MODULE_TAB.Abandon = "quests" end

local floor, ceil, max, min, log = math.floor, math.ceil, math.max, math.min, math.log
local fmt = string.format

---------------------------------------------------------------------------------------------------
-- Cœur pur
---------------------------------------------------------------------------------------------------
local A = {}
M.Core = A
A.KILL_SEC = 25        -- secondes par monstre sans mesure (comme Tracker)
A.DROP = 0.35          -- taux de drop supposé sans mesure
A.SPEED = 7            -- m/s à pied
A.PATH = 1.4           -- chemin réel ≈ 1,4 × vol d'oiseau
A.TALK = 15            -- secondes de dialogue (prendre / rendre)
A.LOG_MAX = 20

local function r0(x) return floor(x + 0.5) end
local function clamp(x, a, b) if x < a then return a elseif x > b then return b end return x end

-- part de l'XP d'une quête selon l'écart joueur - quête (Classic : pleine jusqu'à +5)
function A.XPFactor(d)
  d = tonumber(d) or 0
  if d <= 5 then return 1 elseif d == 6 then return 0.8 elseif d == 7 then return 0.6
  elseif d == 8 then return 0.4 elseif d == 9 then return 0.2 end
  return 0.1
end
-- XP pleine estimée d'une quête pas encore lue (ordre de grandeur Classic)
function A.EstimateXP(ql) ql = tonumber(ql) or 1 return 55 * ql + ql * ql end
function A.GreyLevel(pl)
  if pl <= 5 then return 0 elseif pl <= 39 then return pl - floor(pl / 10) - 5
  elseif pl <= 59 then return pl - floor(pl / 5) - 1 end
  return pl - 9
end
function A.Color(ql, pl, grey)
  grey = grey or A.GreyLevel(pl)
  if ql >= pl + 5 then return "rouge" elseif ql >= pl + 3 then return "orange"
  elseif ql >= pl - 2 then return "jaune" elseif ql > grey then return "vert" end
  return "gris"
end
-- XP d'un monstre de même niveau (base Classic 5 × niv + 45) → référence « farmer des monstres »
function A.GrindXPM(pl, spk)
  return (5 * pl + 45) * 60 / ((spk or A.KILL_SEC) + 10)
end

-- secondes restantes pour un objectif o = { name, have, need, t (m/i/o/e/r/x), finished }
-- ctx.secPerKill, ctx.drops = { [nom en minuscules] = { rate, mob } } → sec, monstres
function A.ObjSeconds(o, ctx)
  if type(o) ~= "table" or o.finished then return 0, 0 end
  local need = tonumber(o.need) or 1
  local left = max(0, need - (tonumber(o.have) or 0))
  if left == 0 then return 0, 0 end
  local spk = ctx.secPerKill or A.KILL_SEC
  local drop = ctx.drops and ctx.drops[tostring(o.name or ""):lower()]
  if o.t == "i" or drop then
    local rate = drop and tonumber(drop.rate) or A.DROP
    local mobs = ceil(left / max(0.02, rate) - 1e-9)
    return mobs * spk, mobs
  elseif o.t == "o" then return left * 20, 0
  elseif o.t == "e" or o.t == "x" then return 90, 0
  elseif o.t == "r" then return left * 2, 0
  elseif o.t == "m" or need > 1 then return left * spk, left end
  return 60, 0
end

-- difficulté : multiplicateur de temps + raisons + infaisable ?
function A.Difficulty(q, pl, k)
  k = max(1, k or 1)
  local d, why, hard = 1, {}, false
  local dl = (tonumber(q.level) or pl) - pl
  if dl > 0 then d = d * (1 + 0.5 * dl * dl) end
  if dl >= 3 and k == 1 then d = d * 2 why[#why + 1] = fmt("niv %d, %d au-dessus de toi", q.level, dl) end
  if dl >= 5 and k < 3 then hard = true end
  if q.elite then
    d = d * max(1.2, 3 / k)
    if k == 1 then why[#why + 1] = "élite" if dl >= 0 then hard = true end end
  end
  local sg = tonumber(q.suggestedGroup) or 0
  if sg > k then
    d = d * 1.8
    why[#why + 1] = fmt("prévue pour %d (vous êtes %d)", sg, k)
    if sg >= 3 and k == 1 then hard = true end
  end
  if q.dungeon then
    if k >= 3 then d = d * 1.5 else d = d * 4 hard = true end
    why[#why + 1] = "donjon"
  end
  if q.escort then d = d * 1.25 end
  return d, why, hard
end

local function dur(sec)
  local m = r0((sec or 0) / 60)
  if m < 1 then m = 1 end
  if m < 60 then return "~" .. m .. " min" end
  return fmt("~%d h %02d", floor(m / 60), m % 60)
end
A.Dur = dur
local function names(list)
  if #list <= 1 then return list[1] or "" end
  return table.concat(list, ", ", 1, #list - 1) .. " et " .. list[#list]
end
local function cutT(s, n) s = tostring(s or "?") if #s > n then return s:sub(1, n - 1) .. "…" end return s end

-- Valeur brute (sans référence) : XP, temps, XP/min. q : voir Evaluate.
function A.Base(q, ctx)
  local pl, k = ctx.pl or 1, max(1, ctx.group or 1)
  local ql = tonumber(q.level) or pl
  if ql <= 0 then ql = pl end
  local d = pl - ql
  local e = { q = q, d = d, color = A.Color(ql, pl, ctx.grey) }
  local f = A.XPFactor(d)
  if tonumber(q.xp) and q.xp > 0 then e.xp, e.exact = q.xp, true
  else e.xp, e.exact = r0(A.EstimateXP(ql) * f), false end
  -- temps
  local travel = tonumber(q.travelSec)
  if not travel and tonumber(q.dist) then travel = q.dist * A.PATH / (ctx.speed or A.SPEED) end
  if not travel then travel = (q.zone and q.zone == ctx.zone) and 240 or 600 end
  local work, mobs = 0, 0
  e.diff, e.diffWhy, e.hard = 1, {}, false
  if not q.complete then
    for _, o in ipairs(q.obj or {}) do
      local s, n = A.ObjSeconds(o, ctx)
      work, mobs = work + s, mobs + n
    end
    if work == 0 and #(q.obj or {}) == 0 then work = 60 end
    e.diff, e.diffWhy, e.hard = A.Difficulty(q, pl, k)
  end
  e.travel, e.work, e.mobs = travel, work * e.diff, mobs
  e.sec = travel + work * e.diff + A.TALK
  -- XP au rendu : un niveau pris d'ici là (rythme du joueur) fait baisser une quête déjà au bord
  e.xpTurn = e.xp
  if ctx.xpm and ctx.xpToLevel and ctx.xpm * e.sec / 60 >= ctx.xpToLevel then
    e.xpTurn = r0(e.xp * A.XPFactor(d + 1) / f)
  end
  e.xpm = e.xpTurn / (e.sec / 60)
  return e
end

-- Verdict + score + explication. e = A.Base(q, ctx), ref = XP/min de référence (tes autres quêtes).
-- q (champs facultatifs) : title, level, xp, complete, elite, suggestedGroup, dungeon, escort, obj, zone,
--   dist, travelSec, ratio (progression 0..1), mates = { noms }, chain = { n, title, xp } | false (aucune
--   suite connue) | nil (inconnu), breadcrumbUseless, reward = { pct, item, slot, sell (pc) }, isolated, far
-- ctx : pl, group, zone, secPerKill, speed, drops, xpm, xpToLevel, logCount, logMax, leavingZone
function A.Judge(e, ref, ctx)
  local q, pl = e.q, ctx.pl or 1
  local neg, pos = {}, {}      -- { w = poids, t = texte }
  local s = 50
  local ratio = 1
  if ref and ref > 0 then
    ratio = e.xpm / ref
    s = s + clamp(25 * log(max(ratio, 1e-6)) / log(2), -35, 35)
  end
  if e.color == "gris" then
    s = s - 10
    neg[#neg + 1] = { w = 30, t = fmt("grise (tu as %d niveaux de plus)", e.d), p = "trop facile maintenant" }
  end
  if e.xpTurn < e.xp then
    s = s - 5
    neg[#neg + 1] = { w = 8, t = fmt("elle perdra %d XP si tu montes de niveau avant de la rendre", e.xp - e.xpTurn), p = "elle rapportera moins bientôt" }
  end
  if not q.complete then
    if e.hard then
      s = s - 20
      neg[#neg + 1] = { w = 25, t = (#e.diffWhy > 0 and table.concat(e.diffWhy, ", ") or "trop dure") .. " : pas faisable seul", p = "trop dure seul" }
    elseif #e.diffWhy > 0 then
      s = s - 8
      neg[#neg + 1] = { w = 12, t = table.concat(e.diffWhy, ", ") .. " : ça va galérer", p = "un peu dure" }
    end
    if q.escort then s = s - 5 neg[#neg + 1] = { w = 6, t = "escorte (peut échouer, à refaire)", p = "escorte qui peut rater" } end
    if (tonumber(q.ratio) or 0) >= 0.5 then
      s = s + 5
      pos[#pos + 1] = { w = 5, t = fmt("déjà faite à %d %%", r0(q.ratio * 100)), p = "presque finie" }
    end
  end
  if q.breadcrumbUseless then
    s = s - 40
    neg[#neg + 1] = { w = 40, t = "relais inutile (la quête où elle mène est déjà prise ou faite)", p = "elle ne sert plus à rien" }
  end
  local ch = q.chain
  if type(ch) == "table" and (ch.n or 0) > 0 then
    s = s + min(25, 8 + 5 * ch.n)
    pos[#pos + 1] = { w = 20, t = fmt("elle ouvre une suite (« %s »%s%s)", cutT(ch.title, 30),
      ch.n > 1 and fmt(", %d quêtes", ch.n) or "", (ch.xp or 0) > 0 and fmt(", ~%d XP", ch.xp) or ""), p = "elle ouvre une suite" }
  end
  local rw = q.reward
  if type(rw) == "table" then
    if (rw.pct or 0) >= 1 then
      s = s + min(20, rw.pct)
      pos[#pos + 1] = { w = 15, t = (fmt("récompense : %s (+%d %% %s)", rw.item or "objet", r0(rw.pct), rw.slot or ""):gsub("%s+%)", ")")),
        p = "bonne récompense" }
    elseif (rw.sell or 0) >= 5000 then
      s = s + min(5, floor(rw.sell / 5000))
      pos[#pos + 1] = { w = 3, t = fmt("récompense revendable %d pa", floor(rw.sell / 100)), p = "récompense qui se revend bien" }
    end
  end
  local mates = q.mates or {}
  if #mates > 0 then
    s = s + min(25, 12 * #mates)
    pos[#pos + 1] = { w = 30, t = names(mates) .. (#mates > 1 and " l'ont aussi : à faire ensemble" or " l'a aussi : à faire ensemble"),
      p = names(mates) .. (#mates > 1 and " l'ont aussi" or " l'a aussi") }
  end
  if not q.complete then
    if q.isolated then
      s = s - 8
      neg[#neg + 1] = { w = 9, t = fmt("seule quête en %s, loin de tes autres", q.zone or "?"), p = "toute seule, loin de tes autres quêtes" }
    elseif q.far then
      s = s - 5
      neg[#neg + 1] = { w = 7, t = "loin d'ici", p = "trop loin" }
    end
    if ctx.leavingZone and q.zone == ctx.leavingZone then
      s = s - 6
      neg[#neg + 1] = { w = 8, t = fmt("tu quittes %s (zone trop facile)", q.zone), p = "tu quittes cette zone" }
    end
  end
  if q.complete then
    s = max(s, 90)
    pos[#pos + 1] = { w = 50, t = "prête à rendre", p = "prête à rendre" }
  end
  s = clamp(r0(s), 0, 100)
  -- seuils : un journal presque plein rend plus sévère
  local free = (ctx.logMax or A.LOG_MAX) - (ctx.logCount or 0)
  local tAb = free <= 0 and 45 or free <= 2 and 40 or 30
  local verdict = (s < tAb and "abandon") or (s < 55 and "later") or "keep"
  if q.complete then verdict = "keep" end
  if #mates > 0 and verdict == "abandon" then verdict = "later" end
  -- trop dure seul mais rentable : à garder pour une sortie en groupe, pas à jeter
  if verdict == "abandon" and e.hard and ratio >= 0.6 and e.color ~= "gris" and not q.breadcrumbUseless and free > 0 then
    verdict = "later"
  end
  table.sort(neg, function(a, b) return a.w > b.w end)
  table.sort(pos, function(a, b) return a.w > b.w end)
  e.score, e.verdict, e.ratio, e.ref, e.neg, e.pos = s, verdict, ratio, ref, neg, pos
  e.text = A.Explain(e)
  e.short = A.Short(e)
  e.plain = A.Plain(e)
  return e
end

-- phrase XP / temps
local function xpClause(e)
  local what = (e.work <= 0 and "trajet") or (e.travel < 0.2 * e.sec and "farm") or "trajet et de farm"
  local xpm = r0(e.xpm)
  if e.q.complete then return fmt("%d XP à la clé, %s pour aller la rendre", e.xpTurn, dur(e.sec)) end
  if not (e.ref and e.ref > 0) then return fmt("%d XP pour %s de %s (%d XP/min)", e.xpTurn, dur(e.sec), what, xpm) end
  if e.ratio < 0.6 then
    return fmt("%d XP seulement pour %s de %s → %d XP/min contre %d XP/min %s", e.xpTurn, dur(e.sec), what, xpm, r0(e.ref),
      e.refGrind and "en tuant simplement des monstres" or "pour tes autres quêtes")
  elseif e.ratio >= 1.2 then
    return fmt("%d XP pour %s → %d XP/min, mieux que %s (%d)", e.xpTurn, dur(e.sec), xpm,
      e.refGrind and "farmer des monstres" or "tes autres quêtes", r0(e.ref))
  end
  return fmt("%d XP pour %s → %d XP/min, dans la moyenne (%d)", e.xpTurn, dur(e.sec), xpm, r0(e.ref))
end
A.XPClause = xpClause

local HEAD = { abandon = "Abandonne", later = "Plus tard", keep = "Garde" }
function A.Explain(e)
  local title = e.q.title or "?"
  local list = {}
  local tail = {}
  if e.verdict == "keep" then
    for _, p in ipairs(e.pos) do list[#list + 1] = p.t end
    list[#list + 1] = xpClause(e)
    for i, n in ipairs(e.neg) do if i <= 2 then tail[#tail + 1] = "mais " .. n.t end end
  else
    for _, n in ipairs(e.neg) do list[#list + 1] = n.t end
    list[#list + 1] = xpClause(e)
    for _, p in ipairs(e.pos) do tail[#tail + 1] = p.t end
    if e.q.chain == false then tail[#tail + 1] = "elle n'ouvre aucune suite" end
    if e.verdict == "later" and #tail == 0 then
      tail[#tail + 1] = (e.hard and "garde-la pour quand tes amis seront là") or "fais-la si tu passes à côté"
    elseif e.verdict == "later" and e.hard then
      tail[#tail + 1] = "garde-la pour quand tes amis seront là"
    end
  end
  local txt = fmt("%s « %s » : %s", HEAD[e.verdict], title, table.concat(list, ", "))
  if #tail > 0 then txt = txt .. " ; " .. table.concat(tail, " ; ") end
  return txt .. "."
end

-- version courte (ligne de l'onglet)
function A.Short(e)
  local why = {}
  for i, n in ipairs(e.neg) do
    if i > 2 then break end
    why[#why + 1] = (n.t:gsub(" %(.*%)$", ""):gsub(" : .*$", ""))
  end
  local cmp = (e.ref and e.ref > 0) and fmt("%d XP/min (autres %d)", r0(e.xpm), r0(e.ref)) or fmt("%d XP/min", r0(e.xpm))
  why[#why + 1] = cmp
  return table.concat(why, ", ")
end

-- version EN CLAIR (agent SIMPLE) : 1–2 raisons en mots, sans aucun nombre
-- (« trop facile maintenant, presque pas d'XP »). Les calculs ne changent pas.
function A.Plain(e)
  local list = {}
  local src = e.verdict == "keep" and e.pos or e.neg
  for _, r in ipairs(src or {}) do if r.p then list[#list + 1] = r.p end if #list >= 2 then break end end
  if e.verdict ~= "keep" and #list < 2 and not e.q.complete and e.ref and e.ref > 0 and (e.ratio or 1) < 0.6 then
    list[#list + 1] = (e.ratio < 0.25 or e.color == "gris") and "presque pas d'XP" or "peu d'XP pour le temps"
  end
  if #list == 0 then
    if e.verdict == "keep" then list[1] = (e.ratio or 1) >= 1.2 and "elle rapporte bien" or "elle vaut le coup"
    elseif e.verdict == "later" then list[1] = e.hard and "attends tes potes" or "fais-la si tu passes à côté"
    else list[1] = "elle ne vaut plus le coup" end
  end
  return table.concat(list, ", ", 1, min(2, #list))
end

-- ligne d'interface : « Retire « X » : trop facile maintenant, presque pas d'XP »
local PLAIN_HEAD = { abandon = "Retire", later = "Plus tard", keep = "Garde" }
function A.PlainLine(e, n)
  return fmt("%s « %s » : %s", PLAIN_HEAD[e.verdict] or "Garde", cutT(e.q.title, n or 28), e.plain or A.Plain(e))
end

local function median(t)
  if #t == 0 then return nil end
  table.sort(t)
  local n = #t
  if n % 2 == 1 then return t[(n + 1) / 2] end
  return (t[n / 2] + t[n / 2 + 1]) / 2
end
A.Median = median

-- Toutes les quêtes : référence = médiane des XP/min des AUTRES quêtes en cours (repli : farmer des monstres)
-- → liste triée du pire au meilleur score (= ordre « libérer de la place »)
function A.EvaluateAll(qs, ctx)
  ctx = ctx or {}
  ctx.grey = ctx.grey or A.GreyLevel(ctx.pl or 1)
  local base = {}
  for i, q in ipairs(qs or {}) do base[i] = A.Base(q, ctx) end
  local out = {}
  for i, e in ipairs(base) do
    local others = {}
    for j, o in ipairs(base) do if j ~= i and not o.q.complete then others[#others + 1] = o.xpm end end
    local ref = (#others >= 2) and median(others) or nil
    if not ref then ref, e.refGrind = A.GrindXPM(ctx.pl or 1, ctx.secPerKill), true end
    out[#out + 1] = A.Judge(e, ref, ctx)
    out[#out]._i = i
  end
  table.sort(out, function(a, b) if a.score ~= b.score then return a.score < b.score end return a._i < b._i end)
  return out
end

-- « Libérer de la place » : à abandonner puis plus tard, du pire au meilleur
function A.FreeList(evals)
  local out = {}
  for _, e in ipairs(evals or {}) do if e.verdict == "abandon" then out[#out + 1] = e end end
  for _, e in ipairs(evals or {}) do if e.verdict == "later" then out[#out + 1] = e end end
  return out
end

A.HOWTO = "Pour la retirer : ouvre ton journal (L), clique « %s », puis « Abandonner » et confirme. " ..
  "TreeAI ne le fait jamais à ta place."
function A.HowTo(title) return fmt(A.HOWTO, title or "?") end

---------------------------------------------------------------------------------------------------
-- Collecte (WoW, lecture seule)
---------------------------------------------------------------------------------------------------
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function now() return try(g("GetTime")) or 0 end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and f(v) and true or false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function low(s) return tostring(s or ""):lower() end
local function short(n) return (tostring(n or "?"):gsub("%-.*$", "")) end

local ESCORT = { "escort", "protég", "proteg", "accompagn", "garde du corps", "protect" }
local function isEscort(s)
  s = low(s)
  for _, k in ipairs(ESCORT) do if s:find(k, 1, true) then return true end end
  return false
end

local function logMax()
  local CQ = g("C_QuestLog")
  local n = CQ and CQ.GetMaxNumQuestsCanAccept and tonumber((try(CQ.GetMaxNumQuestsCanAccept)))
  if not n then n = tonumber(g("MAX_QUESTLOG_QUESTS")) end
  return (n and n > 0) and n or A.LOG_MAX
end

-- liste du journal : module Quests (scan sans sélection) ; repli minimal
local function scan()
  local Q = mod("Quests")
  local list = Q and Q.Scan and try(Q.Scan)
  if type(list) == "table" then return list end
  return Q and Q.lastList or {}
end

local function questieDB()
  local Q = mod("Quests")
  if Q and Q.QuestieDB then local db = try(Q.QuestieDB) if db then return db end end
  local L = g("QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) == "table" and type(db.QueryQuestSingle) == "function" then return db end
  return nil
end
local function questieDone()
  local Q = g("Questie")
  local c = type(Q) == "table" and type(Q.db) == "table" and type(Q.db.char) == "table" and Q.db.char.complete
  return type(c) == "table" and c or {}
end

-- suites : Quests.Chain (Questie) → { n, title, xp } ; false si Questie dit « aucune » ; nil sans Questie
local function chainOf(id, pl, db)
  if not db then return nil end
  local Q = mod("Quests")
  local nexts
  if Q and Q.Chain then nexts = try(Q.Chain, id, 3) end
  if type(nexts) ~= "table" then
    nexts = {}
    local nx = tonumber((try(db.QueryQuestSingle, id, "nextQuestInChain")))
    if nx and nx > 0 then nexts[1] = { id = nx, title = try(db.QueryQuestSingle, nx, "name") } end
  end
  if #nexts == 0 then return false end
  local xp = 0
  for _, n in ipairs(nexts) do
    local ql = tonumber((try(db.QueryQuestSingle, n.id, "questLevel"))) or pl
    if ql <= 0 then ql = pl end
    xp = xp + r0(A.EstimateXP(ql) * A.XPFactor(pl - ql))
  end
  return { n = #nexts, title = nexts[1].title or ("#" .. tostring(nexts[1].id)), xp = xp }
end

-- récompense : choix vus chez le donneur (Pro) + API par questID (famille Mainline seulement)
local function rewardOf(id)
  local links = {}
  local P = mod("Pro")
  if P and P.char and type(P.char.rewards) == "table" and type(P.char.rewards[id]) == "table" then
    for _, l in ipairs(P.char.rewards[id]) do links[#links + 1] = l end
  end
  local CQ = g("C_QuestLog")
  if #links == 0 and CQ and CQ.GetInfo and g("GetNumQuestLogChoices") and g("GetQuestLogItemLink") then
    local n = tonumber((try(g("GetNumQuestLogChoices"), id, true))) or 0
    for i = 1, min(n, 6) do
      local l = try(g("GetQuestLogItemLink"), "choice", i, id)
      if l then links[#links + 1] = l end
    end
  end
  if #links == 0 then return nil end
  local B = mod("Boost")
  local best = { pct = 0, sell = 0 }
  for _, l in ipairs(links) do
    local name, _, _, _, _, _, _, _, _, _, sell = try(g("GetItemInfo"), l)
    local okUse = B and B.CanUse and try(B.CanUse, l)
    local pct = okUse and B.UpgradePct and tonumber((try(B.UpgradePct, l))) or nil
    if pct and pct > best.pct then best.pct, best.item = min(pct, 100), name or tostring(l):match("%[(.-)%]") end
    if (tonumber(sell) or 0) > best.sell then best.sell = tonumber(sell) end
  end
  return best
end

-- contexte joueur
function M.Context(list)
  local pl = tonumber((try(g("UnitLevel"), "player"))) or 1
  local Tr = mod("Tracker")
  local tc = Tr and Tr.Context and try(Tr.Context) or {}
  local St = T.Stats   -- mesures réelles (Stats) d'abord
  local spk = tonumber(tc.secPerKill) or (St and St.KillTime and St:KillTime(0))
  if not spk then
    local P = mod("Predict")
    local r = P and P.Rates and try(P.Rates) or {}
    local kph = tonumber(r.killsPerHour)
    spk = (kph and kph > 5) and max(8, min(120, 3600 / kph)) or A.KILL_SEC
  end
  local C = T.Compass
  local xp, xpMax = tonumber((try(g("UnitXP"), "player"))), tonumber((try(g("UnitXPMax"), "player")))
  local B = mod("Boost")
  local st = B and B.XPStats and try(B.XPStats, B)
  local xph = type(st) == "table" and tonumber(st.perHour or st.xph) or nil
  if not xph and St and St.XPPerHour then xph = St:XPPerHour("questing") or St:XPPerHour() end
  local ctx = {
    pl = pl, group = max(1, tonumber((try(g("GetNumGroupMembers")))) or 1),
    zone = try(g("GetRealZoneText")) or try(g("GetZoneText")) or "",
    secPerKill = spk, drops = type(tc.drops) == "table" and tc.drops or {},
    speed = tonumber(tc.speed) or (St and St.TravelSpeed and St:TravelSpeed(false)) or (C and C.Speed and tonumber((try(C.Speed)))) or A.SPEED,
    xpm = xph and xph > 0 and xph / 60 or nil,
    xpToLevel = (xp and xpMax and xpMax > 0) and (xpMax - xp) or nil,
    logCount = #(list or {}), logMax = logMax(),
  }
  -- zone trop facile (Leveling) = zone que tu quittes
  local L = mod("Leveling")
  local CM = g("C_Map")
  if L and L.ZoneAdvice and CM and CM.GetBestMapForUnit then
    local txt = try(L.ZoneAdvice, try(CM.GetBestMapForUnit, "player"), pl)
    if type(txt) == "string" and txt:find("trop facile", 1, true) then ctx.leavingZone = ctx.zone end
  end
  return ctx
end

-- quête du journal → entrée du cœur pur
function M.Gather(list, ctx)
  local db = questieDB()
  local done = db and questieDone() or {}
  local Tr = mod("Tracker")
  local mates = Tr and Tr.SquadMates and try(Tr.SquadMates) or {}
  local items = {}
  for _, it in ipairs((Tr and Tr.items) or {}) do if it.key then items[it.key] = it end end
  local inLog, zoneN = {}, {}
  for _, q in ipairs(list) do inLog[q.id] = true zoneN[q.zone or "?"] = (zoneN[q.zone or "?"] or 0) + 1 end
  local out = {}
  for _, q in ipairs(list) do
    if not q.failed and not secret(q.title) then
      local e = { id = q.id, title = q.title, level = q.level, xp = q.xp, complete = q.complete, elite = q.elite,
        suggestedGroup = q.suggestedGroup, dungeon = q.dungeon, zone = q.zone, ratio = q.ratio, obj = q.obj, dist = q.dist }
      e.escort = isEscort(q.title)
      for _, o in ipairs(q.obj or {}) do if isEscort(o.text) then e.escort = true end end
      local it = items[q.id]
      if it then e.travelSec, e.dist = it.travel, it.dist or e.dist end
      -- amis TreeAI (Squad via Tracker) + groupe sans addon
      local m = {}
      if Tr and Tr.GroupOn then
        for _, gm in ipairs(try(Tr.GroupOn, q.id, mates) or {}) do if not gm.done then m[#m + 1] = short(gm.name) end end
      end
      e.mates = m
      e.chain = chainOf(q.id, ctx.pl, db)
      if db then
        local to = tonumber((try(db.QueryQuestSingle, q.id, "breadcrumbForQuestId")))
        e.breadcrumbUseless = (to and to > 0 and (inLog[to] or done[to])) and true or false
      end
      e.reward = rewardOf(q.id)
      e.isolated = (zoneN[q.zone or "?"] or 0) == 1 and q.zone ~= ctx.zone and #list >= 4
      e.far = (tonumber(e.dist) or 0) > 1500
      out[#out + 1] = e
    end
  end
  return out
end

---------------------------------------------------------------------------------------------------
-- Affichage
---------------------------------------------------------------------------------------------------
local VERB = { abandon = "À retirer", later = "Plus tard", keep = "Garder" }
M.VERB = VERB

function M.Instructions(e)
  if not e then return end
  M:Print(A.PlainLine(e, 60) .. ".")
  M:Print(A.HowTo(e.q.title))
end

function M.Lines()
  local out = {}
  local ev = M.evals or {}
  local ctx = M.ctx or {}
  local free = A.FreeList(ev)
  local full = (ctx.logMax or A.LOG_MAX) - (ctx.logCount or 0)
  local nAb = 0
  for _, e in ipairs(free) do if e.verdict == "abandon" then nAb = nAb + 1 end end
  if #free == 0 then return out end
  out[1] = { text = full <= 2 and fmt("— À retirer (journal presque plein : %d/%d) —", ctx.logCount or 0, ctx.logMax or A.LOG_MAX)
    or "— À retirer —", prio = full <= 2 and 2 or 1,
    tooltip = "Des quêtes qui ne valent plus le coup. Clic sur une quête = comment la retirer." }
  local cap = tonumber(M.db and M.db.maxLines) or 6
  for _, e in ipairs(free) do
    if #out > cap then break end
    if e.verdict == "abandon" or (M.db and M.db.showLater ~= false) then
      out[#out + 1] = {
        text = A.PlainLine(e),
        prio = e.verdict == "abandon" and 2 or 1,
        tooltip = A.PlainLine(e, 60) .. "." .. fmt("\n≈ %d min pour %d XP.", max(1, r0(e.sec / 60)), e.xpTurn or e.xp or 0)
          .. "\n" .. A.HowTo(e.q.title),
        onClick = function() M.Instructions(e) end,
        data = { questID = e.q.id, verdict = e.verdict, score = e.score },
      }
    end
  end
  if full <= 2 and nAb > 0 and M.db and M.db.pin ~= false then
    out[1].pin = true
    out[1].text = fmt("Journal %d/%d : %d quête%s à retirer (onglet Quêtes)", ctx.logCount or 0, ctx.logMax or A.LOG_MAX,
      nAb, nAb > 1 and "s" or "")
  end
  return out
end

local function push(lines)
  if T.SetTabLines then pcall(T.SetTabLines, T, "quests", lines, "Abandon") return end
  for i = 1, 5 do M:Clear("ab" .. i) end
  for i, l in ipairs(lines) do
    if i > 5 then break end
    M:Suggest("ab" .. i, l.text, l.prio or 1, { ttl = 0, tab = "quests", tooltip = l.tooltip, onClick = l.onClick })
  end
end

local last = -100
function M.Update(force)
  if not (M.IsEnabled and M:IsEnabled()) then return end
  if T.InCombat and T:InCombat() then return end
  if not force and now() - last < 3 then return end
  last = now()
  local list = scan()
  local ctx = M.Context(list)
  local qs = M.Gather(list, ctx)
  M.ctx = ctx
  M.evals = A.EvaluateAll(qs, ctx)
  M.lines = M.Lines()
  push(M.lines)
  return M.evals
end

local pending = false
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 1, function() pending = false pcall(M.Update, true) end)
end

function M.Report(arg)
  M.Update(true)
  local ev, ctx = M.evals or {}, M.ctx or {}
  local free = A.FreeList(ev)
  local n = tonumber(arg)
  if n then
    local e = free[n]
    if e then M.Instructions(e) else M:Print("Rien à ce numéro. Tape /tai abandon pour la liste.") end
    return
  end
  M:Print(fmt("Journal %d/%d. Verdict pour chaque quête (du pire au meilleur) :", ctx.logCount or 0, ctx.logMax or A.LOG_MAX))
  local detail = tostring(arg or ""):find("detail") ~= nil
  for _, e in ipairs(ev) do M:Print(detail and fmt("[%s, %d/100] %s", VERB[e.verdict], e.score, e.text) or (A.PlainLine(e, 40) .. ".")) end
  if #free > 0 then
    local t = {}
    for i, e in ipairs(free) do if i <= 5 then t[#t + 1] = fmt("%d. %s", i, cutT(e.q.title, 26)) end end
    M:Print("Libérer de la place : " .. table.concat(t, " · ") .. ". /tai abandon <n> = comment la retirer.")
  else
    M:Print("Rien à retirer : toutes tes quêtes valent le coup.")
  end
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local out = {}
  for i, e in ipairs(A.FreeList(M.evals or {})) do
    if i > 5 then break end
    out[#out + 1] = { title = e.q.title, verdict = e.verdict, score = e.score, why = e.plain, detail = e.short }
  end
  s.abandon = { list = out, count = #out, log = (M.ctx or {}).logCount, logMax = (M.ctx or {}).logMax }
end

function M.Count()
  local n = 0
  for _, e in ipairs(M.evals or {}) do if e.verdict == "abandon" then n = n + 1 end end
  return n
end

function M:OnEnable()
  -- (LITE) profil essentiel : calculé à la demande (/tai abandon) et quand le journal gagne / perd une quête
  local lite = T.Lite and T:Lite()
  local evs = lite and { "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP" }
    or { "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA", "GROUP_ROSTER_UPDATE" }
  for _, ev in ipairs(evs) do
    M:RegisterEvent(ev, function() M.Refresh(1.5) end)
  end
  if not lite then M:On("COMBAT_END", function() M.Refresh(1.5) end) end
  M:On("SUMMARY", function() pcall(M.WriteSummary, M) end)
  if T.RegisterSlash then
    T:RegisterSlash("abandon", function(rest) M.Report(rest) end, "quêtes à retirer : verdict + pourquoi (/tai abandon 2 = comment)")
  end
  M.Refresh(3)
end

function M:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, "quests", {}, "Abandon") end
end
function M:OnOptionChanged() M.Refresh(0.2) end

T.Abandon = M
