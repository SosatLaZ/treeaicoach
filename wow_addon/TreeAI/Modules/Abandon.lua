-- TreeAI — module « Nettoyer mon journal » (agents ABANDON puis JOURNAL 4.0.1).
-- Pour chaque quête du journal, les FAITS du jeu qui justifient de la retirer, rangés par raison (page « Journal »
-- de la fenêtre TreeAI, Modules/JournalPage.lua) : échouée / chrono écoulé (C_QuestLog.IsFailed, GetTimeAllowed),
-- chaîne bloquée (relais dont la suite est déjà prise ou faite, objet de livraison qui n'est plus dans les sacs :
-- QuestieDB + C_QuestLog.IsQuestFlaggedCompleted + C_Item.GetItemCount), doublon (quêtes exclusives : une seule
-- compte), grise (couleur du jeu : UnitQuestTrivialLevelRange ; XP au rendu = GetQuestLogRewardXP), donjon déjà fait
-- (Dungeon.IsDone), trop dure pour le groupe ACTUEL (étiquette élite / donjon, groupe conseillé du jeu, niveau rouge,
-- contre GetNumGroupMembers), autre continent ou très loin (carte de la quête GetQuestUiMapID, trajet Route/NavTime).
-- Jamais une durée estimée comme raison d'abandonner : le modèle de coût (XP/min, cœur A.Base / A.Judge) ne sert plus
-- qu'aux étiquettes du suivi (Garde / Plus tard), et « À retirer » n'y vient que d'un fait.
-- Abandon : DANS le clic du joueur seulement (bouton « Abandonner » → « Confirmer », 2 clics), hors combat, une quête
-- par clic (lot « Tout ce qui est gris » : 1 confirmation puis un abandon par clic) ; voir M.AbandonNow pour
-- l'analyse de contamination (taint) et le repli « où cliquer » sans aucun appel.
-- Sorties : page « Journal » (rendu par JournalPage.lua), rappel sur la carte « Prochaine étape » quand le journal
-- est chargé (≥ 20 quêtes, réglable), bloc « Nettoyer mon journal » de l'onglet Quêtes (panneau texte),
-- summary.abandon (appli), /tai abandon (ouvre la page). Calculs hors combat (valeurs secrètes de Forever).
-- Le cœur (A.*) est pur (aucun appel WoW) : testé seul par tests/wow/abandon_spec.lua. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Abandon", {
  title = "Nettoyer mon journal",
  defaults = { enabled = true, maxLines = 6, hint = true, hintMin = 20, direct = true },
  options = {
    { key = "hint", type = "toggle", label = "Journal chargé : rappel sur la carte « Prochaine étape »", section = "Affichage" },
    { key = "hintMin", type = "range", label = "Rappel à partir de (quêtes dans le journal)", min = 10, max = 40, step = 1 },
    { key = "maxLines", type = "range", label = "Lignes max (panneau texte)", min = 2, max = 20, step = 1 },
    { key = "direct", type = "toggle", label = "Abandon en 2 clics depuis TreeAI (sinon : où cliquer dans ton journal)",
      section = "Abandon" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Abandon then T.MODULE_TAB.Abandon = "quests" end
-- (JOURNAL 4.0.1) faits du jeu seulement, deux clics pour agir : rangé avec les fonctions sûres (allumé en mode sûr)
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Abandon = true end
if type(T.MODULE_INFO) == "table" and type(T.MODULE_INFO.Abandon) == "table" then
  T.MODULE_INFO.Abandon.desc = "Nettoyer mon journal : quêtes grises, échouées, bloquées, donjon déjà fait…"
end

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
local function cutT(s, n) return T.Cut(s or "?", n) end   -- (4.0) Core : coupe UTF-8, jamais une lettre accentuée coupée

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
  -- (QUESTQA) abandonner est irréversible : seulement sur un FAIT du jeu, jamais sur une durée estimée (« ≈ 13 min
  -- pour 2000 XP ») ; une quête trop dure seul (donjon, élite, groupe) se garde pour quand les amis sont là.
  -- (JOURNAL 4.0.1) le fait vient de A.Facts (q.strongFact : grise, échouée, chaîne bloquée, doublon, donjon déjà
  -- fait) ; « journal plein » n'en est plus un (QUELLE quête retirer serait alors choisie sur une estimation)
  local fact = q.strongFact
  if fact == nil then fact = e.color == "gris" or q.breadcrumbUseless end
  if verdict == "abandon" and not fact then verdict = "later" end
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
  -- (QA-QUEST F20) quête de groupe / donjon : le fait, pas une estimation d'XP/min d'un donjon fait seul
  -- (« trop dure seul, presque pas d'XP » pour une quête de donjon niveau 33)
  if e.verdict ~= "keep" and e.hard and not e.q.complete then return "à faire en groupe" end
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


---------------------------------------------------------------------------------------------------
-- (JOURNAL 4.0.1) faits du jeu → raisons de retirer une quête (pur, testé par abandon_spec.lua)
-- q : id, title, level, xp, complete, failed, timerOut, grey, color, elite, suggestedGroup, dungeon (étiquette du
--   jeu), dungeonName, dungeonDone = { name, label }, breadcrumb = { title, state = "log"|"done" }, lostItem (nom),
--   dupOf (titre), continent (nom), travelSec, planned (arrêt du trajet), mates = { noms }, keep (elle ouvre une
--   suite / sa récompense est une amélioration : jamais « grise » ni « à voir »)
-- ctx : pl, group (GetNumGroupMembers, 1 seul)
---------------------------------------------------------------------------------------------------
A.GROUPS = {
  { key = "failed", title = "Échouées", advice = "Elles n'avancent plus : retire-les (tu peux les reprendre chez le donneur)." },
  { key = "blocked", title = "Chaîne bloquée", advice = "Elles ne peuvent plus être rendues." },
  { key = "dup", title = "Doublons", advice = "Une seule des deux compte : retire l'autre." },
  { key = "grey", title = "Grises", advice = "Presque plus d'XP : la place vaut mieux qu'elles." },
  { key = "dungeon", title = "Donjon déjà fait", advice = "Tu as fini ce donjon : à retirer, sauf si tu y retournes." },
  { key = "hard", title = "Trop dures pour ton groupe actuel", advice = "À garder si tes amis viennent, sinon à retirer." },
  { key = "far", title = "Autre continent ou très loin", advice = "À retirer si tu ne repasses pas par là." },
}
A.GROUP = {}
for i, gr in ipairs(A.GROUPS) do gr.rank = i; A.GROUP[gr.key] = gr end
A.STRONG = { failed = true, blocked = true, dup = true, grey = true, dungeon = true }   -- « Retire » ; hard / far : à voir
A.FAR_SEC = 15 * 60      -- trajet (Route / NavTime) au-delà duquel une quête du même continent est « très loin »
A.HINT_MIN = 20          -- journal chargé : rappel sur la carte à partir de 20 quêtes (option)

local function you(k) return k <= 1 and "tu es seul" or fmt("vous êtes %d", k) end
--- Les faits d'une quête, dans l'ordre des groupes (le 1er = la raison principale) : { {key, text} }
function A.Facts(q, ctx)
  ctx = ctx or {}
  local pl, k = tonumber(ctx.pl) or 1, max(1, tonumber(ctx.group) or 1)
  local out = {}
  local function add(key, text) out[#out + 1] = { key = key, text = text } end
  local ql = tonumber(q.level) or 0
  -- encore à faire, hors trajet, sans ami dessus, sans suite ni récompense qui la justifie
  local open = not q.complete and not q.failed and not q.planned and #(q.mates or {}) == 0 and not q.keep
  if q.failed then add("failed", q.timerOut and "échouée : chrono écoulé" or "échouée") end
  if q.breadcrumb then
    add("blocked", fmt("relais vers « %s », %s", cutT(q.breadcrumb.title, 30),
      q.breadcrumb.state == "log" and "déjà dans ton journal" or "déjà faite"))
  end
  if q.lostItem then add("blocked", fmt("« %s » n'est plus dans tes sacs", cutT(q.lostItem, 30))) end
  if q.dupOf then add("dup", fmt("même suite que « %s » : une seule compte", cutT(q.dupOf, 30))) end
  if q.grey and open then
    add("grey", (tonumber(q.xp) and q.xp > 0) and fmt("grise : %d XP au rendu (niv %d, toi %d)", q.xp, ql, pl)
      or fmt("grise (niv %d, toi %d)", ql, pl))
  end
  if q.dungeonDone and not q.complete and not q.failed and not q.planned then
    local lab = tostring(q.dungeonDone.label or "fait"):gsub("^%u", string.lower)
    add("dungeon", fmt("%s : %s", cutT(q.dungeonDone.name, 30), lab))
  end
  if open then
    local sg = tonumber(q.suggestedGroup) or 0
    if q.dungeon then   -- donjon (5 joueurs) : à 4 c'est jouable, à 3 ou moins non
      if k < 4 then add("hard", fmt("%s : à faire en groupe de 5, %s", q.dungeonName and cutT(q.dungeonName, 26) or "donjon", you(k))) end
    elseif sg > k then add("hard", fmt("prévue pour %d, %s", sg, you(k)))
    elseif q.elite and k < 3 then add("hard", "élite, " .. you(k))
    elseif q.color == "rouge" and k < 3 then add("hard", fmt("niv %d, toi %d : rouge, %s", ql, pl, you(k)))
    end
    if q.continent then add("far", "autre continent (" .. q.continent .. ")")
    elseif tonumber(q.travelSec) and q.travelSec >= A.FAR_SEC then add("far", fmt("≈ %d min de trajet", r0(q.travelSec / 60))) end
  end
  return out
end

--- Toutes les quêtes → { groups = { {key, title, advice, items = { {q, facts, key, strong} }} }, count, strong } ;
--- une quête une seule fois, dans le groupe de sa raison principale ; grises : la moins payée d'abord
function A.Cleanup(qs, ctx)
  local byKey = {}
  for _, gr in ipairs(A.GROUPS) do byKey[gr.key] = { key = gr.key, title = gr.title, advice = gr.advice, items = {} } end
  local n, strong = 0, 0
  for _, q in ipairs(qs or {}) do
    local facts = A.Facts(q, ctx)
    if #facts > 0 then
      local key = facts[1].key
      local it = { q = q, facts = facts, key = key, strong = A.STRONG[key] or false }
      local list = byKey[key].items
      list[#list + 1] = it
      n = n + 1
      if it.strong then strong = strong + 1 end
    end
  end
  local out = { groups = {}, count = n, strong = strong }
  for _, gr in ipairs(A.GROUPS) do
    local e = byKey[gr.key]
    if #e.items > 0 then
      table.sort(e.items, function(a, b)
        local xa, xb = tonumber(a.q.xp) or 1e9, tonumber(b.q.xp) or 1e9
        if gr.key == "grey" and xa ~= xb then return xa < xb end
        local la, lb = tonumber(a.q.level) or 0, tonumber(b.q.level) or 0
        if la ~= lb then return la < lb end
        return tostring(a.q.title) < tostring(b.q.title)
      end)
      out.groups[#out.groups + 1] = e
    end
  end
  return out
end
--- « Retire « Vieille corvée » : grise : 120 XP au rendu (niv 17, toi 28) » (« Retire » seulement sur un fait sûr)
function A.Line(it, n)
  return fmt("%s« %s » : %s", it.strong and "Retire " or "", cutT(it.q.title, n or 28), it.facts[1].text)
end
--- tous les faits d'une quête : « grise : … · autre continent (Kalimdor) »
function A.Detail(it)
  local t = {}
  for _, f in ipairs(it.facts or {}) do t[#t + 1] = f.text end
  return table.concat(t, " · ")
end
A.HOWTO = "Ouvre ton journal (L) > « %s » > « Abandonner »."   -- (« → » : absent des polices du jeu)
function A.HowTo(title) return fmt(A.HOWTO, cutT(title or "?", 40)) end

---------------------------------------------------------------------------------------------------
-- Collecte (WoW, lecture seule)
---------------------------------------------------------------------------------------------------
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function now() return try(g("GetTime")) or 0 end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and f(v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function low(s) return tostring(s or ""):lower() end
local function short(n) return (tostring(n or "?"):gsub("%-.*$", "")) end
local function inCombat() return T.InCombat and T:InCombat() or false end

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

-- QuestieDB (lecture seule) : celui du module Donjon (LibQuestieDB, sinon QuestieLoader) → db.quest(id, clé)
local function questie()
  local D = T.Dungeon
  local db = D and type(D.Questie) == "function" and try(D.Questie) or nil
  if type(db) == "table" and type(db.quest) == "function" then return db end
  return nil
end
-- quête finie selon le JEU (pas le cache de Questie)
local function flagged(id)
  local CQ = g("C_QuestLog")
  local f = (CQ and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  return (type(f) == "function" and sv(try(f, id))) and true or false
end

-- suites : Quests.Chain (Questie) → { n, title, xp, lv } ; false si Questie dit « aucune » ; nil sans Questie
local function chainOf(id, pl, db)
  if not db then return nil end
  local Q = mod("Quests")
  local nexts
  if Q and Q.Chain then nexts = try(Q.Chain, id, 3) end
  if type(nexts) ~= "table" then
    nexts = {}
    local nx = tonumber((db.quest(id, "nextQuestInChain")))
    if nx and nx > 0 then nexts[1] = { id = nx, title = db.quest(nx, "name") } end
  end
  if #nexts == 0 then return false end
  local xp, lv = 0, 0
  for _, n in ipairs(nexts) do
    local ql = tonumber((db.quest(n.id, "questLevel"))) or pl
    if ql <= 0 then ql = pl end
    if ql > lv then lv = ql end
    xp = xp + r0(A.EstimateXP(ql) * A.XPFactor(pl - ql))
  end
  return { n = #nexts, title = nexts[1].title or ("#" .. tostring(nexts[1].id)), xp = xp, lv = lv }
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

-- objets (objet de livraison perdu ?) : sacs + banque connue du client ; nil = inconnu (jamais « perdu » alors)
local function itemCount(id)
  local CI = g("C_Item")
  local n = CI and type(CI.GetItemCount) == "function" and tonumber(sv(try(CI.GetItemCount, id, true))) or nil
  if n == nil then n = tonumber(sv(try(g("GetItemCount"), id, true))) end
  return n
end
local function itemName(id, db)
  local CI = g("C_Item")
  local n = CI and type(CI.GetItemNameByID) == "function" and sv(try(CI.GetItemNameByID, id)) or nil
  if type(n) ~= "string" or n == "" then n = sv((try(g("GetItemInfo"), id))) end
  if (type(n) ~= "string" or n == "") and db and db.item then n = sv(db.item(id, "name")) end
  return (type(n) == "string" and n ~= "") and n or "l'objet de la quête"
end

-- géographie : continent d'une carte (C_Map.GetWorldPosFromMapPos via T.Geo), position du joueur, de la quête
local CONTINENT = { [0] = "Royaumes de l'Est", [1] = "Kalimdor" }
local function continentOf(map)
  local Geo = T.Geo
  local r = map and Geo and Geo.Rect and try(Geo.Rect, map)
  return type(r) == "table" and tonumber(r.c) or nil
end
local function playerSpot()
  local Geo = T.Geo
  if not (Geo and Geo.Player) then return nil end
  local m, x, y = try(Geo.Player)
  if not tonumber(m) then return nil end
  return { map = m, x = tonumber(x), y = tonumber(y), c = continentOf(m) }
end
local function questMap(id)
  local m = tonumber(sv(try(g("GetQuestUiMapID"), id)))
  return (m and m > 0) and m or nil
end
local function questSpot(id, qmap)
  local CQ = g("C_QuestLog")
  if CQ and CQ.GetNextWaypoint then
    local m, x, y = try(CQ.GetNextWaypoint, id)
    m, x, y = tonumber(sv(m)), tonumber(sv(x)), tonumber(sv(y))
    if m and x and y then return { map = m, x = x * 100, y = y * 100 } end
  end
  return qmap and { map = qmap, x = 50, y = 50 } or nil
end

-- contexte joueur
function M.Context(list)
  local pl = tonumber((try(g("UnitLevel"), "player"))) or 1
  local Tr = mod("Tracker")
  local tc = Tr and Tr.Context and try(Tr.Context) or {}
  local St = T.Stats   -- mesures réelles (Stats) d'abord
  local spk = tonumber(tc.secPerKill) or (St and St.KillTime and St:KillTime(0))
  if not spk then
    local P = T.Predict   -- (4.0) façade de Stats (taux mesurés)
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
  local Q = mod("Quests")
  local ctx = {
    pl = pl, group = max(1, tonumber((try(g("GetNumGroupMembers")))) or 1),
    zone = try(g("GetRealZoneText")) or try(g("GetZoneText")) or "",
    secPerKill = spk, drops = type(tc.drops) == "table" and tc.drops or {},
    speed = tonumber(tc.speed) or (St and St.TravelSpeed and St:TravelSpeed(false)) or (C and C.Speed and tonumber((try(C.Speed)))) or A.SPEED,
    xpm = xph and xph > 0 and xph / 60 or nil,
    xpToLevel = (xp and xpMax and xpMax > 0) and (xpMax - xp) or nil,
    logCount = #(list or {}), logMax = logMax(),
    -- niveau gris du JEU (UnitQuestTrivialLevelRange, via Quests) : une suite au-dessus vaut la quête
    grey = (Q and Q.GreyLevel and tonumber((try(Q.GreyLevel, pl)))) or A.GreyLevel(pl),
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

-- (QA-QUEST F20) quêtes que le trajet en cours fait (arrêts visibles du Planner) : jamais « à retirer » à côté
function M.Planned()
  local P = T.Planner
  local set = {}
  for _, s in ipairs(P and P.steps or {}) do
    if s.q and not s._done and not s._other then set[tonumber(s.q) or s.q] = true end
  end
  return set
end

--- Journal → entrées du modèle de coût (quêtes non échouées) ET entrées des faits (toutes les quêtes)
function M.Gather(list, ctx)
  local db = questie()
  local Tr = mod("Tracker")
  local mates = Tr and Tr.SquadMates and try(Tr.SquadMates) or {}
  local items = {}
  for _, it in ipairs((Tr and Tr.items) or {}) do if it.key then items[it.key] = it end end
  local inLog, zoneN, titleOf = {}, {}, {}
  for _, q in ipairs(list) do
    inLog[q.id], titleOf[q.id] = true, q.title
    zoneN[q.zone or "?"] = (zoneN[q.zone or "?"] or 0) + 1
  end
  local planned = M.Planned()
  local D = T.Dungeon
  local here = playerSpot()
  local R = T.Route
  -- doublons : quêtes exclusives (QuestieDB exclusiveTo) toutes deux dans le journal → la plus récente (id le plus
  -- grand) est le doublon ; deux relais vers la même suite (absente du journal) → le second
  local dupOf, bcTo = {}, {}
  if db then
    for _, q in ipairs(list) do
      local ex = db.quest(q.id, "exclusiveTo")
      for _, x in ipairs(type(ex) == "table" and ex or {}) do
        x = tonumber(x)
        if x and x ~= q.id and inLog[x] then
          local a, b = min(x, q.id), max(x, q.id)
          if not dupOf[b] then dupOf[b] = titleOf[a] end
        end
      end
    end
  end
  local out, fin = {}, {}
  for _, q in ipairs(list) do
    if not secret(q.title) then
      local f = { id = q.id, index = q.index, title = q.title, level = q.level, xp = q.xp, complete = q.complete,
        failed = q.failed, grey = q.grey, color = q.color, elite = q.elite, suggestedGroup = q.suggestedGroup,
        dungeon = q.dungeon, zone = q.zone, planned = planned[q.id] and true or false, dupOf = dupOf[q.id] }
      local e
      if not q.failed then
        e = { id = q.id, title = q.title, level = q.level, xp = q.xp, complete = q.complete, elite = q.elite,
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
        e.reward = rewardOf(q.id)
        e.isolated = (zoneN[q.zone or "?"] or 0) == 1 and q.zone ~= ctx.zone and #list >= 4
        e.far = (tonumber(e.dist) or 0) > 1500
        f.mates = m
        f.keep = ((type(e.chain) == "table" and (e.chain.lv or 0) > (tonumber(ctx.grey) or 0))
          or (type(e.reward) == "table" and (e.reward.pct or 0) >= 1)) or nil
      else
        -- échouée : chrono écoulé ? (C_QuestLog.GetTimeAllowed → total, écoulé)
        local CQ = g("C_QuestLog")
        local tot, el = nil, nil
        if CQ and CQ.GetTimeAllowed then tot, el = try(CQ.GetTimeAllowed, q.id) end
        tot, el = tonumber(sv(tot)), tonumber(sv(el))
        f.timerOut = (tot and el and tot > 0 and el >= tot) and true or false
      end
      if db then
        -- relais dont la suite est déjà prise ou faite (le JEU le dit : IsQuestFlaggedCompleted)
        local to = tonumber((db.quest(q.id, "breadcrumbForQuestId")))
        if to and to > 0 then
          if inLog[to] then f.breadcrumb = { title = titleOf[to] or "?", state = "log" }
          elseif flagged(to) then f.breadcrumb = { title = sv(db.quest(to, "name")) or ("#" .. to), state = "done" }
          elseif bcTo[to] then f.dupOf = f.dupOf or titleOf[bcTo[to]]
          else bcTo[to] = q.id end
          if e then e.breadcrumbUseless = f.breadcrumb and true or false end
        end
        -- quête de livraison (aucun objectif) dont l'objet donné au départ n'est plus là : impossible à rendre
        local src = tonumber((db.quest(q.id, "sourceItemId")))
        if src and src > 0 and #(q.obj or {}) == 0 and db.quest(q.id, "objectives") == nil and itemCount(src) == 0 then
          f.lostItem = itemName(src, db)
        end
      end
      -- donjon : déjà fait (historique TreeAI ou verrou du jeu), sinon son nom pour « trop dure »
      local d = D and D.QuestDungeon and try(D.QuestDungeon, q.id) or nil
      if d then
        f.dungeonName = d.n
        local done, info = false, nil
        if D.IsDone then done, info = try(D.IsDone, d) end
        if done and type(info) == "table" and info.src ~= "quests" then f.dungeonDone = { name = d.n, label = info.label } end
      end
      -- loin : autre continent (carte de la quête, fait du jeu) ; sinon trajet (Route : vols, bateaux, vitesse NavTime)
      if here and not q.complete and not q.failed then
        local qm = questMap(q.id)
        local qc = continentOf(qm)
        if qc and here.c and qc ~= here.c then f.continent = CONTINENT[qc] or "un autre continent"
        elseif q.zone ~= ctx.zone and here.x and R and type(R.LegSeconds) == "function" then
          local dest = questSpot(q.id, qm)
          if dest then f.travelSec = tonumber((try(R.LegSeconds, here, dest))) end
        end
      end
      if e then e.strongFact = false out[#out + 1] = e end
      fin[#fin + 1] = f
      f._e = e
    end
  end
  -- un fait sûr = « À retirer » possible dans le modèle de coût (étiquettes du suivi)
  for _, f in ipairs(fin) do
    if f._e then
      local facts = A.Facts(f, ctx)
      f._e.strongFact = (facts[1] and A.STRONG[facts[1].key]) and true or false
    end
    f._e = nil
  end
  return out, fin
end

---------------------------------------------------------------------------------------------------
-- Calcul, liste visible, rappel
---------------------------------------------------------------------------------------------------
local EMPTY = { groups = {}, count = 0, strong = 0 }
M.gone = {}              -- [questID] = heure : abandonnée, le journal ne l'a pas encore retirée (cachée 15 s)

function M.Compute()
  local list = scan()
  local ctx = M.Context(list)
  local qs, fin = M.Gather(list, ctx)
  M.ctx = ctx
  M.evals = A.EvaluateAll(qs, ctx)
  M.cleanup = A.Cleanup(fin, ctx)
  M.cleanupAt = now()
  -- une seule vérité pour « À retirer » : le suivi (Tracker / Todo / Plain) lit les mêmes faits que la page
  local strong = {}
  for _, gr in ipairs(M.cleanup.groups) do
    for _, it in ipairs(gr.items) do if it.strong then strong[it.q.id] = it end end
  end
  for _, e in ipairs(M.evals) do
    local it = e.q and strong[e.q.id]
    if it then e.verdict, e.plain = "abandon", it.facts[1].text
    elseif e.verdict == "abandon" then e.verdict = "later" end
  end
  return M.cleanup
end

--- la liste sans les quêtes abandonnées à l'instant (le journal ne les a pas encore retirées)
function M.Visible(cl)
  cl = cl or EMPTY
  local t, any = now(), false
  for id, at in pairs(M.gone) do if t - at > 15 then M.gone[id] = nil else any = true end end
  if not any then return cl end
  local out = { groups = {}, count = 0, strong = 0 }
  for _, gr in ipairs(cl.groups) do
    local list = {}
    for _, it in ipairs(gr.items) do if not M.gone[it.q.id] then list[#list + 1] = it end end
    if #list > 0 then
      out.groups[#out.groups + 1] = { key = gr.key, title = gr.title, advice = gr.advice, items = list }
      out.count = out.count + #list
      for _, it in ipairs(list) do if it.strong then out.strong = out.strong + 1 end end
    end
  end
  return out
end

--- la liste pour la page « Journal » : tenue à jour par le module, sinon calculée maintenant (≤ 1 fois / 2 s, hors
--- combat) → liste, contexte
function M.CleanupNow()
  local fresh = M.cleanup and now() - (M.cleanupAt or -100) < ((M._active and 30) or 2)
  if not fresh and not inCombat() then pcall(M.Compute) end
  return M.Visible(M.cleanup or EMPTY), M.ctx or {}
end

local function openJournal()
  local Wn = T.Window
  if Wn and Wn._active and Wn.Open and Wn.pageIndex and Wn.pageIndex.journal then Wn.Open("journal") return end
  if T.ShowPanel then T:ShowPanel() end
  if T.SelectTab then T:SelectTab("quests") end
end
M.OpenJournal = openJournal

-- bloc de l'onglet « Quêtes » (panneau texte ; la fenêtre a sa page) : en-tête + les quêtes, clic = la page
function M.Lines()
  local cl = M.Visible(M.cleanup or EMPTY)
  if cl.count == 0 then return {} end
  local out = { { text = fmt("Nettoyer mon journal : %d quête%s", cl.count, cl.count > 1 and "s" or ""), header = true, prio = 1,
    tooltip = "Les quêtes à retirer, avec la raison du jeu. Clic sur une ligne : la page « Journal » (abandon en 2 clics, hors combat)." } }
  local cap = tonumber(M.db and M.db.maxLines) or 6
  for _, gr in ipairs(cl.groups) do
    for _, it in ipairs(gr.items) do
      if #out > cap then break end
      out[#out + 1] = { text = A.Line(it), prio = it.strong and 2 or 1, onClick = openJournal,
        tooltip = gr.title .. " : " .. A.Detail(it) .. ".\n" .. gr.advice, data = { questID = it.q.id, reason = it.key } }
    end
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

-- rappel « journal chargé » : une ligne de « Maintenant », montrée en avis (badge « i ») sur la carte « Prochaine
-- étape » ; clic = la page, clic droit = plus tard (2 h, Core)
local HINT = "journal"
function M.Hint()
  local cl, ctx = M.Visible(M.cleanup or EMPTY), M.ctx or {}
  local n, cap = tonumber(ctx.logCount) or 0, tonumber(ctx.logMax) or 40
  local free = cap - n
  local th = tonumber(M.db and M.db.hintMin) or A.HINT_MIN
  if not (M.db and M.db.hint ~= false and cl.count > 0 and (n >= th or free <= 3)) then M:Clear(HINT) return end
  local Wn = T.Window
  if Wn and type(Wn.CARD_NOTICE) == "table" then
    Wn.CARD_NOTICE["Abandon:" .. HINT] = { fmt("Journal : %d quêtes, %d à nettoyer", n, cl.count), "clic : la liste · clic droit : plus tard" }
  end
  M:Suggest(HINT, fmt("Journal chargé (%d/%d) : %d quête%s à nettoyer", n, cap, cl.count, cl.count > 1 and "s" or ""),
    free <= 3 and 2 or 1, { ttl = 0, onClick = openJournal, sound = false, cooldown = 3600,
      tooltip = "Grises, échouées, bloquées, donjon déjà fait… : la page « Journal » les liste avec la raison du jeu.\n"
        .. "Abandon en 2 clics, hors combat." })
end

local last = -100
function M.Update(force)
  if not (M.IsEnabled and M:IsEnabled()) then return end
  if inCombat() then return end
  if not force and now() - last < 3 then return end
  last = now()
  M.Compute()
  M.lines = M.Lines()
  push(M.lines)
  M.Hint()
  local Wn = T.Window
  if Wn and Wn.IsOpen and Wn.IsOpen() and Wn.CurrentPage and Wn.CurrentPage() == "journal" and Wn.Dirty then Wn.Dirty() end
  return M.evals
end

local pending = false
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 1, function() pending = false pcall(M.Update, true) end)
end

---------------------------------------------------------------------------------------------------
-- Abandon dans le clic du joueur (JOURNAL 4.0.1)
-- Analyse de contamination (taint) : on n'appelle QUE des fonctions C du jeu, jamais une fonction Lua de Blizzard
-- et jamais une variable de son interface : C_QuestLog.GetSelectedQuest / SetSelectedQuest / SetAbandonQuest /
-- GetAbandonQuest / AbandonQuest (documentées SANS restriction ni protection dans l'API de Forever 1.60.1 ;
-- clients classiques : GetQuestLogSelection / SelectQuestLogEntry / SetAbandonQuest / GetAbandonQuestName /
-- AbandonQuest). Une fonction C ne contamine rien (ses valeurs rendues ne portent pas la marque de l'addon) ; ni
-- QuestMapFrame_*, ni ShowUIPanel, ni StaticPopup_Show(« ABANDON_QUEST ») : la fenêtre « Abandonner » de
-- Blizzard et son journal restent propres. Ce qui avait fait bloquer « Abandonner » en 3.8 (la sélection changée
-- par du code HORS CLIC, le journal ouvert par l'addon) n'arrive jamais : seulement dans le clic du joueur
-- (événement matériel), hors combat, UNE quête par clic, après une confirmation ; la quête « à abandonner » du jeu
-- et la sélection du journal sont remises aussitôt (une fenêtre « Abandonner ? » de Blizzard restée ouverte garde
-- sa quête). Sécurité : la quête visée est relue (GetAbandonQuest / GetAbandonQuestName) avant AbandonQuest.
-- Repli « où cliquer » SANS aucun appel : API absente, option coupée, ou le jeu a bloqué une fois (ADDON_ACTION_* :
-- gardé, plus jamais tenté).
---------------------------------------------------------------------------------------------------
M.ARM_S = 8              -- secondes pour confirmer (2e clic)
M.BATCH_S = 60           -- un lot confirmé reste ouvert 60 s entre deux clics
local armed              -- { id, title, t }
local batch              -- { kind, items = { {id, title} }, i, confirmed, t }

function M.GuidedText(title) return A.HowTo(title) end

local function api()
  local CQ = g("C_QuestLog")
  if type(CQ) == "table" and type(CQ.SetSelectedQuest) == "function" and type(CQ.SetAbandonQuest) == "function"
      and type(CQ.AbandonQuest) == "function" then
    return "modern", CQ
  end
  if type(g("SelectQuestLogEntry")) == "function" and type(g("SetAbandonQuest")) == "function"
      and type(g("AbandonQuest")) == "function" then
    return "legacy"
  end
  return nil
end
--- abandon direct possible ? (API du jeu présente, option, jamais bloqué par le jeu)
function M.CanDirect()
  if not (M.db and M.db.direct ~= false and not M.db.refused) then return false end
  return api() ~= nil
end

-- la quête est-elle (encore) dans le journal ? → oui, index du journal
local function logIndex(id)
  local CQ = g("C_QuestLog")
  if CQ and type(CQ.GetLogIndexForQuestID) == "function" then
    local i = tonumber(sv(try(CQ.GetLogIndexForQuestID, id)))
    return (i and i > 0) and true or false, i
  end
  local f = g("GetQuestLogIndexByID")
  if type(f) == "function" then
    local i = tonumber(sv(try(f, id)))
    return (i and i > 0) and true or false, i
  end
  for _, q in ipairs(scan()) do if q.id == id then return true, q.index end end
  return false
end

--- Abandonne UNE quête. À appeler DANS le clic du joueur (T:UserAction), hors combat ; jamais deux par clic.
--- → état ("done" | "guided" | "refused" | "gone"), message (pour le pied de la fenêtre ; jamais le chat)
function M.AbandonNow(id, title)
  id, title = tonumber(id), tostring(title or "?")
  if not id then return "refused", "Quête inconnue." end
  if inCombat() then return "refused", "Abandon : hors combat seulement." end
  if not (T.InUserAction and T:InUserAction()) then return "refused", "Abandon : seulement sur un clic." end
  if not M.CanDirect() then return "guided", M.GuidedText(title) end
  local here, idx = logIndex(id)
  if not here then M.gone[id] = now() return "gone", fmt("« %s » n'est plus dans ton journal.", cutT(title, 40)) end
  local kind, CQ = api()
  local blocked0 = tonumber(T._blockedSession) or 0
  local ok = false
  if kind == "modern" then
    if CQ.CanAbandonQuest and try(CQ.CanAbandonQuest, id) == false then
      return "refused", fmt("Le jeu ne permet pas d'abandonner « %s ».", cutT(title, 40))
    end
    local prevSel = tonumber(sv(try(CQ.GetSelectedQuest))) or 0
    local prevAb = CQ.GetAbandonQuest and tonumber(sv(try(CQ.GetAbandonQuest))) or 0
    try(CQ.SetSelectedQuest, id)
    try(CQ.SetAbandonQuest)
    local target = CQ.GetAbandonQuest and tonumber(sv(try(CQ.GetAbandonQuest)))
    ok = (target == nil and not CQ.GetAbandonQuest) or target == id
    if ok then try(CQ.AbandonQuest) end
    -- remis aussitôt : la quête « à abandonner » du jeu (une fenêtre « Abandonner ? » de Blizzard ouverte garde la
    -- sienne), puis la sélection du journal
    if prevAb ~= 0 and prevAb ~= id then try(CQ.SetSelectedQuest, prevAb); try(CQ.SetAbandonQuest) end
    try(CQ.SetSelectedQuest, prevSel)
  else
    local getSel, sel, setAb, ab, abName = g("GetQuestLogSelection"), g("SelectQuestLogEntry"), g("SetAbandonQuest"),
      g("AbandonQuest"), g("GetAbandonQuestName")
    local prev = tonumber(sv(try(getSel))) or 0
    if idx then
      try(sel, idx)
      try(setAb)
      local name = abName and sv(try(abName))
      ok = (name == nil and not abName) or name == title
      if ok then try(ab) end
    end
    try(sel, prev)
  end
  if (tonumber(T._blockedSession) or 0) > blocked0 then
    if M.db then M.db.refused = true end   -- le jeu a refusé : plus jamais tenté, la marche à suivre à la place
    return "guided", "Le jeu a refusé l'abandon direct. " .. M.GuidedText(title)
  end
  if not ok then return "guided", M.GuidedText(title) end
  M.gone[id] = now()
  M.Refresh(0.5)
  return "done", fmt("« %s » abandonnée.", cutT(title, 40))
end

function M.Armed(id) return (armed and armed.id == id and now() - armed.t <= M.ARM_S) and true or false end
function M.Disarm() armed, batch = nil, nil end

--- état du bouton d'une quête : "go" (Abandonner), "confirm" (Confirmer), "combat", "guided" (Où cliquer ?)
function M.ButtonState(id)
  if not M.CanDirect() then return "guided" end
  if inCombat() then return "combat" end
  if M.Armed(id) then return "confirm" end
  return "go"
end

--- clic sur « Abandonner » : 1er clic = « Confirmer l'abandon de « X » ? », 2e clic (même quête, ≤ 8 s) = l'abandon
--- → état ("armed" | "done" | "guided" | "refused" | "gone"), message
function M.Request(id, title)
  id = tonumber(id)
  if inCombat() then armed = nil return "refused", "Abandon : hors combat seulement." end
  if not M.CanDirect() then return "guided", M.GuidedText(title) end
  if M.Armed(id) then
    armed = nil
    return M.AbandonNow(id, title)
  end
  armed, batch = { id = id, title = title, t = now() }, nil
  return "armed", fmt("Confirmer l'abandon de « %s » ? Clique « Confirmer ».", cutT(title or "?", 40))
end

--- quêtes d'un groupe (ex. "grey") encore visibles : { {id, title} }
function M.BatchItems(kind)
  local out = {}
  for _, gr in ipairs(M.Visible(M.cleanup or EMPTY).groups) do
    if gr.key == kind then for _, it in ipairs(gr.items) do out[#out + 1] = { id = it.q.id, title = it.q.title } end end
  end
  return out
end
function M.BatchState()
  if batch and now() - batch.t <= (batch.confirmed and M.BATCH_S or M.ARM_S) then return batch end
  batch = nil
  return nil
end
local KIND_WORD = { grey = { "grise", "grises" } }
--- lot « Tout ce qui est gris » : 1er clic = confirmation, puis UN abandon par clic (une action par clic du joueur)
--- → état, message
function M.BatchRequest(kind)
  if inCombat() then batch = nil return "refused", "Abandon : hors combat seulement." end
  if not M.CanDirect() then return "guided", "Abandon direct indisponible : retire-les depuis ton journal (L)." end
  local words = KIND_WORD[kind] or { "quête", "quêtes" }
  local b = M.BatchState()
  if not (b and b.kind == kind) then
    local list = M.BatchItems(kind)
    if #list == 0 then return "refused", "Plus rien à retirer ici." end
    batch, armed = { kind = kind, items = list, i = 0, confirmed = false, t = now() }, nil
    return "armed", fmt("Confirmer : retirer %d quête%s %s ? Clique encore : une quête par clic.", #list,
      #list > 1 and "s" or "", #list > 1 and words[2] or words[1])
  end
  b.confirmed, b.t = true, now()
  while true do
    b.i = b.i + 1
    local it = b.items[b.i]
    if not it then batch = nil return "done", "Terminé : plus rien à retirer dans ce groupe." end
    if not M.gone[it.id] then
      local st, msg = M.AbandonNow(it.id, it.title)
      if st == "done" then
        if b.i >= #b.items then batch = nil return "done", fmt("« %s » abandonnée : c'était la dernière.", cutT(it.title, 30)) end
        return "done", fmt("« %s » abandonnée (%d/%d). Clique pour la suivante.", cutT(it.title, 30), b.i, #b.items)
      elseif st ~= "gone" then
        batch = nil
        return st, msg
      end
    end
  end
end

---------------------------------------------------------------------------------------------------
-- Commande, résumé pour l'appli
---------------------------------------------------------------------------------------------------
local VERB = { abandon = "À retirer", later = "Plus tard", keep = "Garder" }
M.VERB = VERB

--- /tai abandon : la page « Journal » (fenêtre TreeAI) ; sans fenêtre, la liste dans le chat (commande tapée) ;
--- /tai abandon detail : le modèle de coût (chiffres) ; /tai abandon <n> : où cliquer pour la n-ième
function M.Report(arg)
  arg = tostring(arg or "")
  local n = tonumber(arg)
  local detail = arg:find("detail", 1, true) ~= nil
  local Wn = T.Window
  if not n and not detail and Wn and Wn._active and Wn.Open and Wn.pageIndex and Wn.pageIndex.journal then
    Wn.Open("journal")
    return
  end
  if not inCombat() then pcall(M.Compute) end
  local cl, ctx = M.Visible(M.cleanup or EMPTY), M.ctx or {}
  local flat = {}
  for _, gr in ipairs(cl.groups) do for _, it in ipairs(gr.items) do flat[#flat + 1] = it end end
  if n then
    local it = flat[n]
    M:Print(it and (A.Line(it, 40) .. ". " .. A.HowTo(it.q.title)) or "Rien à ce numéro : /tai abandon pour la liste.")
    return
  end
  M:Print(fmt("Journal %d/%d : %d quête%s à nettoyer.", ctx.logCount or 0, ctx.logMax or A.LOG_MAX, cl.count, cl.count > 1 and "s" or ""))
  if detail then
    for _, e in ipairs(M.evals or {}) do M:Print(fmt("[%s, %d/100] %s", VERB[e.verdict] or "?", e.score or 0, e.text or "")) end
    return
  end
  for i, it in ipairs(flat) do if i <= 12 then M:Print(fmt("%d. %s", i, A.Line(it, 40))) end end
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local cl = M.Visible(M.cleanup or EMPTY)
  local out = {}
  for _, gr in ipairs(cl.groups) do
    for _, it in ipairs(gr.items) do
      if #out < 10 then
        out[#out + 1] = { title = it.q.title, verdict = it.strong and "abandon" or "later", why = it.facts[1].text,
          detail = A.Detail(it), reason = it.key, group = gr.title }
      end
    end
  end
  s.abandon = { list = out, count = #out, strong = cl.strong, log = (M.ctx or {}).logCount, logMax = (M.ctx or {}).logMax,
    updated = try(g("time")) }   -- (QA-APP) l'appli écarte un bloc qui n'est plus réécrit (module coupé)
end

--- quêtes à retirer sur un fait sûr (résumé de Pro)
function M.Count() return M.Visible(M.cleanup or EMPTY).strong or 0 end

function M:OnInit()
  -- (JOURNAL 4.0.1) devenu une fonction sûre : allumé une fois pour qui est en mode sûr (il y était bêta, coupé)
  if self.db and not self.db.v401 then
    self.db.v401 = true
    local db = T.db
    if type(db) == "table" and db.moduleProfile == "sur" then self.db.enabled = true end
  end
end

function M:OnEnable()
  -- (LITE) profil essentiel : calculé quand le journal gagne / perd une quête (et la page le calcule à l'ouverture)
  local lite = T.Lite and T:Lite()
  local evs = lite and { "QUEST_ACCEPTED", "PLAYER_LEVEL_UP" }
    or { "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA", "GROUP_ROSTER_UPDATE" }
  for _, ev in ipairs(evs) do
    M:RegisterEvent(ev, function() M.Refresh(1.5) end)
  end
  M:RegisterEvent("QUEST_REMOVED", function() M.Refresh(1) end)
  if not lite then M:On("COMBAT_END", function() M.Refresh(1.5) end) end
  M:On("COMBAT_START", function() M.Disarm() end)   -- une confirmation ne survit pas au combat
  M:On("SUMMARY", function() pcall(M.WriteSummary, M) end)
  -- (QA-QUEST F20) le trajet vient d'être refait : un arrêt du trajet n'est jamais « à retirer » à côté
  M:On("PLAN_BUILT", function() M.Refresh(0.5) end)
  if T.RegisterSlash then
    T:RegisterSlash("abandon", function(rest) M.Report(rest) end, "nettoyer mon journal : la page « Journal » (/tai abandon detail = chiffres)")
  end
  M.Refresh(3)
end

function M:OnDisable()
  M.Disarm()
  if T.SetTabLines then pcall(T.SetTabLines, T, "quests", {}, "Abandon") end
end
function M:OnOptionChanged() M.Refresh(0.2) end

T.Abandon = M
