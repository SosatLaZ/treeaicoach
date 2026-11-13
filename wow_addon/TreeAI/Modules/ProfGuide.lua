-- TreeAI — « Métiers sans erreur » (agent PROF, 5.0). Extension de Prof / ProfLevel (même module, mêmes réglages,
-- coupée avec lui) : tout ce qui rend le plan VRAI ou absent, et la session plus autonome.
--  * LU DANS LE JEU D'ABORD : exigences, prix et niveau requis des recettes lus chez le maître (ProfLevel.LearnTrainer,
--    les deux signatures du client), rang suivant lu chez lui (PL.Ranks) ; ce qui n'est pas lu est dit « à confirmer
--    chez le maître ». Tant que le maître d'un métier n'a jamais été lu : « Va d'abord chez ton maître » passe AVANT
--    tout achat à l'HV (l'achat est marqué « à confirmer ») — plus de « achète à l'HV » quand une recette du maître
--    faisait mieux.
--  * GARDE-FOUS (PL.Check = Prof.Engine.Audit + logistique) : aucune étape sous ta compétence, aucune quantité au-delà
--    de ce que la plage demande, aucune recette pas encore apprenable ; un plan incohérent n'est jamais montré.
--  * CHEZ LE MAÎTRE : « tout ce que tu peux apprendre d'utile » avec le prix (PL.Offers), une carte sous sa fenêtre,
--    UN clic = UNE recette (T:ShopCall : dans ton clic, hors combat ; mode sûr = TreeAI montre, tu cliques « Former »).
--  * EN PASSANT : « tu passes devant ton maître : 2 recettes à apprendre », une seule fois par lot de recettes, seulement
--    si sa place est connue (vue) et que tu es à moins de 60 m — rien ne tourne ailleurs.
--  * SACS (contrat BAGS) : gardien « Prof » (T.Bags.RegisterKeeper) — les compos du plan (compte exact + marge) ne sont
--    jamais vendues, le surplus est déclaré (PL.Surplus) ; recalcul découpé (T:Spread), jamais dans l'image d'un butin.
--  * PAGE « Métiers » : une carte par métier (compétence, objectif, coût, durée, prochaine étape, inconnues dites).
-- Lua 5.1, aucune globale, rien dans le chat, aucune action du jeu hors clic.
local tonumber = function(v) return _G.tonumber(v) end
local T = _G.TreeAI
local M = T and T.Prof
local PL = M and M.Level
if not (PL and type(M.Engine) == "table") then return end
local E = M.Engine
local API = T.API or {}
local G = _G
local floor, max, min, ceil = math.floor, math.max, math.min, math.ceil
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function pub(v) if secret(v) then return nil end return v end
local function num(v) v = pub(v) return tonumber(v) end
local function str(v) v = pub(v) return type(v) == "string" and v ~= "" and v or nil end
local function now() return try(g("GetTime")) or 0 end
local function epoch() local f = g("time") return (f and try(f)) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function D() return T.Data and T.Data.Recipes end
local function cut(s, n) return (T.Cut and T.Cut(s, n)) or tostring(s or "") end
local function money(c) return M.Money(c) end
local function fold(s) return M.Fold(s) end
local function say(msg) if T.ClickNote then T:ClickNote(msg) end end
local function refuse(msg) if T.CombatRefused then T:CombatRefused(msg) end end
local function enabled() return M.IsEnabled and M:IsEnabled() or false end
local function A(k)
  local d = M.db
  if type(d) ~= "table" then return {} end
  if type(d[k]) ~= "table" then d[k] = {} end
  return d[k]
end
local function S()
  local c = M.char
  if type(c) ~= "table" then return {} end
  if type(c.pl) ~= "table" then c.pl = {} end
  return c.pl
end
local function profOf(line) return PL.ProfOf(line) end
local function lineName(line) local d = D() local v = d and d.lines[line] return v and v[2] or ("métier " .. tostring(line)) end
local function lineKind(line) local d = D() local v = d and d.lines[line] return v and v[1] or "?" end
local function level() return num((try(g("UnitLevel"), "player"))) or 1 end
local function gold() return num(API.Money and API.Money()) or num((try(g("GetMoney")))) or 0 end
local ICON = "Interface\\Icons\\"

-- ===================================================================== rangs : lus chez le maître, sinon « à confirmer »
--- les rangs du métier : { nom, niveau, compétence, plafond } ; le rang suivant porte ce que le maître a montré
--- (niveau requis, compétence). → rangs, estimé (vrai tant que le rang suivant n'a pas été lu chez un maître)
function PL.Ranks(line)
  local pr = profOf(line)
  local tr = A("trank")[line]
  local out, est = {}, true
  for i, r in ipairs(E.RANKS) do out[i] = r end
  if pr and type(tr) == "table" and tr.cap == pr.max then
    for i, r in ipairs(out) do
      if r[4] > pr.max then
        local lv = tonumber(tr.lv)
        if not lv and tr.a == 1 then lv = 1 end              -- apprenable à la visite : aucun niveau ne bloquait
        if lv then out[i] = { r[1], lv, tonumber(tr.r) or r[3], r[4] } est = false end
        break
      end
    end
  end
  return out, est
end
--- le maître de ce métier a-t-il déjà été lu (compte) ?
function PL.TrainerSeen(line)
  local e = A("tseen")[line]
  if type(e) == "table" then return true, e.t end
  return next(A("treq")[line] or {}) ~= nil, nil
end
--- recette « du maître » (exigence seulement estimée) que le maître, une fois lu, ne propose PAS alors qu'elle tombe
--- dans la plage de sa liste : elle ne s'apprend pas chez lui (autre source) → jamais planifiée tant qu'elle n'est pas connue
function PL.NotTaught(line, r)
  local seen = A("tseen")[line]
  if type(seen) ~= "table" or not tonumber(seen.hi) then return false end
  local flags = tonumber(r[11]) or 0
  if flags % 2 == 1 or floor(flags / 2) % 2 == 0 or (r[8] or 0) ~= 0 then return false end
  local req = A("treq")[line]
  if type(req) == "table" and type(req[fold(r[9])]) == "table" then return false end
  return (r[5] or 0) <= seen.hi
end
--- recettes du maître dont l'exigence n'est PAS lue et qui peuvent compter d'ici 25 points : { {spell, name, at} }
function PL.Unread(line)
  local pr = profOf(line)
  local out = {}
  if not pr then return out end
  local known, req = PL.Known(line), A("treq")[line]
  for _, r in ipairs(PL.Rows(line)) do
    local flags = tonumber(r[11]) or 0
    if floor(flags / 2) % 2 == 1 and flags % 2 == 0 and (r[8] or 0) == 0 and not (known and known[r[1]])
        and r[6] > pr.rank and r[5] <= pr.rank + 25 and not PL.NotTaught(line, r) then
      local e = type(req) == "table" and req[fold(r[9])] or nil
      if not (type(e) == "table" and (tonumber(e.r) or 0) > 0) then out[#out + 1] = { spell = r[1], name = r[9], at = r[5] } end
    end
  end
  return out
end

-- ===================================================================== garde-fous d'un plan
--- incohérences d'un plan (moteur + logistique) ; vide = sain. Un plan incohérent est noté (diag) et jamais montré.
function PL.Check(plan)
  if type(plan) ~= "table" or type(plan.res) ~= "table" then return { "plan absent" } end
  local bad = E.Audit(plan.res, plan.inp or {})
  local function no(msg) if #bad < 12 then bad[#bad + 1] = msg end end
  local sp, have, d = plan.split or {}, plan.have or {}, D()
  for _, k in ipairs({ "bank", "mail" }) do
    for id, n in pairs(sp[k] or {}) do
      if n > ((have[k] and have[k][id]) or 0) then no(fmt("%s : %d %s à prendre pour %d vus", k, n, PL.MatName(id), (have[k] and have[k][id]) or 0)) end
    end
  end
  for _, v in ipairs(sp.vendor or {}) do
    local lot = max(1, tonumber((select(4, PL.PriceOf(v.id)))) or (d and d.mats[v.id] and d.mats[v.id][5]) or 1)
    if v.buy < v.n or v.buy - v.n > max(1, ceil(v.n * 0.1)) + lot - 1 then
      no(fmt("marchand : %d %s achetés pour %d utiles", v.buy, PL.MatName(v.id), v.n))
    end
  end
  for _, x in ipairs(plan.prep or {}) do if (x.crafts or 0) < 1 then no("préparation sans fabrication") end end
  for _, l in ipairs(plan.res.learn or {}) do
    if l.at < plan.skill then no(fmt("« %s » à apprendre à %d alors que tu es à %d", tostring(l.name), l.at, plan.skill)) end
  end
  if #bad > 0 and type(T.db) == "table" then
    T.db.diag = type(T.db.diag) == "table" and T.db.diag or {}
    T.db.diag.profBad = { t = epoch(), line = plan.line, skill = plan.skill, target = plan.target, mode = plan.mode,
      bad = { bad[1], bad[2], bad[3] } }
  end
  return bad
end

-- ===================================================================== étapes : honnêteté
local function trainerWhere(line)
  local w = PL.WhereTrainer(line)
  return w, PL.WhereText(w, "maître de " .. lineName(line):lower() .. " (pas encore vu : demande à un garde)")
end
--- (contrat ProfLevel.Steps) après le texte des étapes, avant ton choix d'étape
function PL.Steps5(steps, plan)
  if plan.bad then
    return { { kind = "bad", key = "bad", title = "Plan incohérent : TreeAI ne l'affiche pas",
      sub = "ouvre ta fenêtre de métier puis passe chez ton maître (TreeAI relira tout)", icon = ICON .. "INV_Misc_QuestionMark" } }
  end
  -- tu as Dépeçage : un cuir / une peau à acheter à l'HV se dépèce aussi (zones connues de la table)
  if profOf(393) then
    for _, st in ipairs(steps) do
      local it = (st.kind == "ask" or st.kind == "ah") and st.items and st.items[1] or nil
      local d = D()
      local f = it and d and d.farm and d.farm[it.id]
      if f and not st.skin then
        st.skin = true
        st.sub = (st.sub or "") .. fmt(" · ou dépèce-les : bêtes niv. %d–%d", f[1], f[2])
      end
    end
  end
  for _, st in ipairs(steps) do
    if st.kind == "trainer" and not st.est then
      local est = st.train and plan.ranksEst or false
      for _, l in ipairs(st.learn or {}) do if l.est then est = true end end
      if est then st.est = true st.sub = (st.sub or "") .. " · à confirmer chez le maître" end
    end
  end
  if PL.TrainerSeen(plan.line) then return steps end
  local unread = PL.Unread(plan.line)
  if #unread == 0 then return steps end
  local hv = false
  for _, st in ipairs(steps) do
    if st.kind == "ah" or st.kind == "ask" then
      hv = true
      st.provisional = true
      st.sub = "à confirmer : le maître d'abord · " .. (st.sub or "")
    end
  end
  local w, wt = trainerWhere(plan.line)
  table.insert(steps, 1, { kind = "trainer", key = "trainer:read", learn = {}, where = w, read = true, line = plan.line,
    title = fmt("Va d'abord chez ton maître : %d recette%s à lire", #unread, #unread > 1 and "s" or ""),
    sub = wt .. " · gratuit" .. (hv and " · l'achat à l'HV peut devenir inutile" or " · le plan sera exact"),
    guideLabel = "Maître de " .. lineName(plan.line):lower(), icon = ICON .. "INV_Misc_Book_11" })
  return steps
end

-- ===================================================================== chez le maître : tout ce qui est utile
--- ce que le maître de ce métier peut t'apprendre (lu chez lui) :
--- { list = { {spell, name, cost, why, rank = true|nil} } (utile MAINTENANT), cost, later = { {name, at} },
---   useless = n (recettes déjà grises), unread = n, seen = bool, short = or manquant }
local offerMemo = {}
local offersRaw
function PL.Offers(line)
  local pr = profOf(line)
  if not pr then return { list = {}, later = {}, cost = 0, useless = 0, unread = 0, line = line } end
  local key = tostring(PL.ver and PL.ver.known) .. ":" .. pr.rank .. ":" .. pr.max .. ":" .. level()
  local c = offerMemo[line]
  if not (c and c.key == key) then c = { key = key, v = offersRaw(line, pr) } offerMemo[line] = c end
  local out = c.v
  out.short = out.cost > gold() and (out.cost - gold()) or nil
  return out
end
offersRaw = function(line, pr)
  local out = { list = {}, later = {}, cost = 0, useless = 0, unread = 0, line = line }
  out.seen = PL.TrainerSeen(line)
  local known, req = PL.Known(line), A("treq")[line]
  local lvl = level()
  local tr = A("trank")[line]
  if type(tr) == "table" and tr.cap == pr.max and E.NextRank(pr.max) then
    local nr = E.NextRank(pr.max)
    local okSkill = (tonumber(tr.r) or 0) <= pr.rank
    local okLvl = (tonumber(tr.lv) or 0) <= lvl
    if tr.a == 1 or (tr.r and okSkill and okLvl and tr.lv) then
      out.list[#out.list + 1] = { rank = true, name = tr.name or nr[1], cost = tonumber(tr.c) or 0,
        why = fmt("rang %s : plafond %d → %d", nr[1], pr.max, nr[4]) }
      out.cost = out.cost + (tonumber(tr.c) or 0)
    elseif tr.r or tr.lv then
      out.later[#out.later + 1] = { name = nr[1], at = tonumber(tr.r), lv = tonumber(tr.lv), rank = true }
    end
  end
  for _, r in ipairs(PL.Rows(line)) do
    local flags = tonumber(r[11]) or 0
    if flags % 2 == 0 and (r[8] or 0) == 0 and not (known and known[r[1]]) then
      local e = type(req) == "table" and req[fold(r[9])] or nil
      local need = type(e) == "table" and tonumber(e.r) or nil
      if need and need > 0 and (known or e.a == 1 or need > pr.rank) then
        if need <= pr.rank and (tonumber(e.lv) or 0) <= lvl then
          local col = E.Color2(r, pr.rank)
          -- utile = orange ou jaune maintenant (verte : presque plus de points, grise : aucun)
          if col == "orange" or col == "yellow" then
            out.list[#out.list + 1] = { spell = r[1], name = r[9], cost = tonumber(e.c) or 0, row = r,
              why = (col == "orange" and fmt("orange jusqu'à %d", r[5])) or fmt("%s, des points jusqu'à %d", E.COLOR_FR[col] or col, r[6]) }
            out.cost = out.cost + (tonumber(e.c) or 0)
          else out.useless = out.useless + 1 end
        elseif r[6] > pr.rank and need <= pr.rank + 50 then
          out.later[#out.later + 1] = { name = r[9], at = need, lv = tonumber(e.lv) }
        end
      elseif not need and floor(flags / 2) % 2 == 1 and r[6] > pr.rank and r[5] <= pr.rank + 25 and not PL.NotTaught(line, r) then
        out.unread = out.unread + 1
      end
    end
  end
  tsort(out.later, function(a, b) return (a.at or 0) < (b.at or 0) end)
  return out
end
--- tous tes métiers : { {line, name, offers} } où il y a quelque chose à apprendre maintenant ; total
function PL.LearnList()
  local out, total, n = {}, 0, 0
  for _, pr in ipairs(M.profs or {}) do
    local d = D()
    if d and d.r[pr.line] and #d.r[pr.line] > 0 then
      local o = PL.Offers(pr.line)
      out[#out + 1] = { line = pr.line, name = lineName(pr.line), offers = o }
      total, n = total + o.cost, n + #o.list
    end
  end
  return out, n, total
end
--- la ligne (index) d'un service par son nom, relue À L'INSTANT dans la fenêtre du maître (jamais un index gardé)
local function liveService(name)
  local n = num((try(g("GetNumTrainerServices")))) or 0
  local key = fold(name)
  for i = 1, min(n, 300) do
    local sv = PL.TrainerService(i)
    if sv and fold(sv.name) == key then return sv end
  end
  return nil
end
--- un clic = UNE recette (ou le rang) apprise : revérifiée à l'instant (encore proposée, assez d'or), puis l'appel du jeu
--- par T:ShopCall (dans ton clic, hors combat) ; en mode sûr TreeAI dit seulement quelle ligne cliquer.
function PL.LearnNext()
  if inCombat() then refuse("Apprendre : après le combat.") return false end
  local line = PL.trainerOpen and PL.trainerLine or nil
  if not line then say("Ouvre la fenêtre de ton maître de métier.") return false end
  local o = PL.Offers(line)
  local it
  for _, x in ipairs(o.list) do
    local sv = liveService(x.name)
    if sv and sv.cat == "available" then it = x it.sv = sv break end
  end
  if not it then say("Rien à apprendre d'utile ici pour l'instant.") PL.Soon(0.1) return false end
  local cost = it.sv.cost or it.cost or 0
  if cost > gold() then say(fmt("Il te manque %s pour « %s ».", money(cost - gold()), cut(it.name, 30))) return false end
  local buy = g("BuyTrainerService")
  local ok, why = false, "api"
  if T.ShopCall then ok, why = T:ShopCall("BuyTrainerService", buy, it.sv.i) end
  if ok then
    say(fmt("Appris : %s (%s).", cut(it.name, 34), cost > 0 and money(cost) or "gratuit"))
    PL.Soon(0.3)
    return true
  end
  if why == "safe" or why == "api" then
    say(fmt("Clique « %s » dans la liste du maître, puis « Former ».", cut(it.name, 34)))
  end
  return false
end
--- la carte sous la fenêtre du maître ; vrai si elle est montrée
function PL.TrainerCard()
  if not (PL.trainerOpen and PL.trainerLine and enabled()) or (M.db and M.db.compsCard == false and not PL.focus) then return false end
  local line = PL.trainerLine
  local o = PL.Offers(line)
  local lines = {}
  for i, x in ipairs(o.list) do
    if i > 4 then lines[#lines + 1] = fmt("… +%d", #o.list - 4) break end
    lines[#lines + 1] = fmt("%s — %s%s", cut(x.name, 30), x.why, x.cost > 0 and (" · " .. money(x.cost)) or "")
  end
  if #o.list > 0 then
    lines[#lines + 1] = fmt("Total : %s%s", money(o.cost), o.short and fmt(" (il te manque %s)", money(o.short)) or "")
  end
  local nx = o.later[1]
  if nx then
    lines[#lines + 1] = "Ensuite : " .. cut(nx.name, 28) .. (nx.at and fmt(" dès %d", nx.at) or "")
      .. ((nx.lv and nx.lv > level()) and fmt(" (niveau %d)", nx.lv) or "")
  end
  if o.useless > 0 then lines[#lines + 1] = fmt("Pas utile pour monter : %d recette%s déjà verte%s ou grise%s", o.useless, o.useless > 1 and "s" or "", o.useless > 1 and "s" or "", o.useless > 1 and "s" or "") end
  if #lines == 0 then lines[1] = "Rien à apprendre d'utile ici pour l'instant." end
  local first = o.list[1]
  local label = first and ("Apprendre " .. cut(first.name, 26)) or nil
  local f = PL.ShowCard(PL.OwnerOf("trainer"), "Ici : maître de " .. lineName(line):lower() .. fmt(" · utile : %d", #o.list),
    tconcat(lines, "\n"), label, first and PL.LearnNext or nil,
    first and { "Apprendre", fmt("Clic : apprendre « %s »%s — une recette par clic.", first.name, first.cost > 0 and (" (" .. money(first.cost) .. ")") or ""),
      "Seulement ce qui te donne des points (ou le rang suivant). Rien n'est appris sans ton clic." } or nil)
  PL.npcShown = f ~= nil and "trainer" or nil
  return f ~= nil
end

-- ===================================================================== en passant devant ton maître (une fois)
local HINT_M = 60
local function offerSig(o)
  local t = {}
  for _, x in ipairs(o.list) do t[#t + 1] = x.name end
  tsort(t)
  return tconcat(t, "|")
end
--- à vérifier sur cette carte : { {line, sig, n, cost} } — seulement les maîtres VUS ici qui ont du neuf pour toi
function PL.HintCandidates()
  local out = {}
  if PL.focus or T.focus or not enabled() or (M.db and M.db.passHint == false) then return out end
  local st = S()
  st.hint = type(st.hint) == "table" and st.hint or {}
  for _, pr in ipairs(M.profs or {}) do
    local d = D()
    if d and d.r[pr.line] and #d.r[pr.line] > 0 and PL.TrainerSeen(pr.line) then
      local o = PL.Offers(pr.line)
      local sig = offerSig(o)
      if #o.list > 0 and not o.short and st.hint[tostring(pr.line)] ~= sig then
        out[#out + 1] = { line = pr.line, sig = sig, n = #o.list, cost = o.cost }
      end
    end
  end
  return out
end
--- regarde si tu passes devant un de ces maîtres (≤ 60 m) : l'alerte, une fois par lot de recettes
function PL.HintCheck()
  PL.hintTimer = nil
  if inCombat() or PL.focus or T.focus then return false end
  local cands = PL.hintCands
  if type(cands) ~= "table" or #cands == 0 then return false end
  local left, hit = false, {}
  for _, c in ipairs(cands) do
    if not c.done then
      local w = PL.WhereTrainer(c.line)
      if w and w.src == "vu" and w.dist then
        if w.dist <= HINT_M then
          c.done = true
          local st = S()
          st.hint = type(st.hint) == "table" and st.hint or {}
          st.hint[tostring(c.line)] = c.sig
          hit[#hit + 1] = c
        else left = true end
      end
    end
  end
  if #hit > 0 then
    -- UNE alerte, même si plusieurs maîtres sont côte à côte
    local line, cost, n, names = hit[1].line, 0, 0, {}
    for _, c in ipairs(hit) do
      cost, n = cost + c.cost, n + c.n
      names[#names + 1] = fmt("%s (%d)", lineName(c.line):lower(), c.n)
    end
    local text
    if #hit == 1 then
      text = fmt("Tu passes devant ton maître de %s : %d recette%s à apprendre", lineName(line):lower(), n, n > 1 and "s" or "")
    else
      text = "Tu passes devant tes maîtres : " .. tconcat(names, ", ") .. " à apprendre"
    end
    if cost > 0 then text = text .. " (" .. money(cost) .. ")" end
    if T.ShowAlert then
      pcall(T.ShowAlert, T, { id = "Prof:pass", prio = 3, tab = "now", text = text,
        onClick = function() PL.Guide({ kind = "trainer", where = PL.WhereTrainer(line), guideLabel = "Maître de " .. lineName(line):lower() }, true) end }, true)
    end
    PL.lastHint = { line = line, n = n, t = now(), text = text }
  end
  if left then PL.hintTimer = true T:After(3, function() pcall(PL.HintCheck) end) end
  return true
end
--- (changement de zone / sous-zone, hors combat) : arme la vérification seulement s'il y a un maître vu sur cette carte
function PL.HintArm()
  if PL.hintArming then return end
  PL.hintArming = true
  T:After(1.5, function()
    PL.hintArming = false
    if inCombat() or not enabled() then return end
    -- rien à calculer sur une carte où aucun maître n'a été vu
    local here = T.Here and select(1, T:Here()) or nil
    local seen = here and M.db and type(M.db.trn) == "table" and M.db.trn[tostring(here)] or nil
    if type(seen) ~= "table" or not next(seen) then PL.hintCands = nil return end
    local cands = {}
    for _, c in ipairs(PL.HintCandidates()) do
      local w = PL.WhereTrainer(c.line)
      if w and w.src == "vu" then cands[#cands + 1] = c end
    end
    PL.hintCands = cands
    if #cands > 0 and not PL.hintTimer then PL.HintCheck() end
  end)
end

-- ===================================================================== sacs : gardien « Prof » + surplus (contrat BAGS)
-- keep = { [id] = { n, why } } du dernier calcul ; recalculé en tâche découpée (une ligne de métier par image) après un
-- changement de sacs / de recettes, jamais dans l'image de l'événement. Tant que rien n'est calculé : une compo d'un de
-- TES métiers est gardée en entier (dans le doute on ne vend pas).
local keep = { map = nil, sig = nil, busy = false, at = -100 }
PL._keep = keep   -- (tests)
local function keepSig()
  local v = PL.ver or {}
  return tostring(API.BagGen and API.BagGen() or 0) .. ":" .. tostring(v.known) .. ":" .. tostring(v.bank) .. ":" .. tostring(v.prices)
    .. ":" .. tostring(T.QuestGen and T:QuestGen() or 0)
end
local function craftLines()
  local out, d = {}, D()
  for _, p in ipairs(M.profs or {}) do
    local k = d and d.lines[p.line]
    if k and k[1] ~= "g" and d.r[p.line] and #d.r[p.line] > 0 then out[#out + 1] = p.line end
  end
  return out
end
PL.CraftLines = craftLines
--- recalcul (une étape par appel de T:Spread ; sans Spread : d'un coup, hors événement)
function PL.KeepRebuild()
  if keep.busy or not enabled() then return end
  if inCombat() then PL.keepLater = true return end        -- comptes illisibles en combat : après
  keep.busy = true
  local sig = keepSig()
  local lines, acc = craftLines(), {}
  local i = 0
  local function finish()
    local reserve, why = {}, {}
    local okR, r, w = pcall(PL.Reserve)
    if okR and type(r) == "table" then reserve, why = r, w or {} end
    for id, n in pairs(reserve) do
      local e = acc[id]
      if e then e.n = e.n + n else acc[id] = { n = n, why = fmt("quête « %s » (%d)", cut(why[id] or "?", 28), n) } end
    end
    -- (5.0 INTEG) le « en trop » SÛR, dit par « Courses en ville » (plans des deux modes, quêtes, recettes d'ici 50
    -- points) : une compo qu'aucun de tes plans ne réclame, qui ne servira pas plus tard, et qui n'est pas un
    -- consommable → avis explicite pour Sacs (« 0 + raison »). Tout le reste : pas d'avis (Sacs garde).
    local surplus = {}
    local CR = T.CityRun
    if type(CR) == "table" and type(CR.Verdicts) == "function" and (not CR.IsEnabled or CR:IsEnabled()) then
      local okV, vm = pcall(CR.Verdicts)
      for id, v in pairs(okV and type(vm) == "table" and vm or {}) do
        if type(v) == "table" and (tonumber(v.extra) or 0) > 0 and not v.keep and not v.later and not acc[id]
          and not (CR.IsConsumable and CR.IsConsumable(id)) and (tonumber(v.need) or 0) == 0 then
          surplus[id] = tostring(v.text or "aucun de tes plans de métier n'en a besoin")
        end
      end
    end
    local before, beforeS = keep.map, keep.surplus or {}
    keep.surplus = surplus
    keep.map, keep.sig, keep.at, keep.busy = acc, sig, now(), false
    PL.keepGen = (PL.keepGen or 0) + 1
    if T.Fire then T:Fire("PROF_KEEP_CHANGED") end
    -- (5.0 INTEG) l'avis a changé : Sacs refait SEULEMENT les objets touchés (tout, la première fois)
    local B = T.Bags
    if PL.keeperOn and type(B) == "table" and type(B.Invalidate) == "function" then
      if not before then pcall(B.Invalidate)
      else
        for id, e in pairs(acc) do
          local o = before[id]
          if not o or o.n ~= e.n then pcall(B.Invalidate, id) end
        end
        for id in pairs(before) do if not acc[id] then pcall(B.Invalidate, id) end end
        for id, w in pairs(surplus) do if beforeS[id] ~= w then pcall(B.Invalidate, id) end end
        for id in pairs(beforeS) do if not surplus[id] then pcall(B.Invalidate, id) end end
      end
      -- marchand / banque ouverts : sa carte se remet à jour (avis refaits par petits paquets)
      if (B.merchantOpen or B.bankOpen) and type(B.Refresh) == "function" then pcall(B.Refresh) end
    end
  end
  local function step()
    i = i + 1
    local line = lines[i]
    if not line then return finish() end
    local planned = 0
    for _, mode in ipairs({ "full", "exact" }) do
      local ok, plan = pcall(PL.Compute, line, mode, mode == "exact" and 300 or PL.Target(line, mode))
      if ok and type(plan) == "table" and not plan.bad then
        planned = planned + 1
        local need = {}
        for id, n in pairs(plan.use or {}) do need[id] = (need[id] or 0) + n end
        if mode == "full" then for id, n in pairs(plan.res and plan.res.buy or {}) do need[id] = (need[id] or 0) + n end end
        for id, n in pairs(need) do
          local margin = (plan.res.var and plan.res.var[id]) and max(1, ceil(n * 0.1)) or 0
          local e = acc[id]
          if not e then e = { n = 0, plan = 0 } acc[id] = e end
          if n + margin > (e.plan or 0) then
            e.n = e.n - (e.plan or 0) + n + margin
            e.plan = n + margin
            e.why = fmt("ton plan de %s en utilise %d%s", lineName(line):lower(), n, margin > 0 and fmt(" (+%d de marge)", margin) or "")
          end
        end
      end
    end
    if planned < 2 then
      -- (5.0 INTEG) un plan de ce métier manque (incohérent, pas calculable) : dans le doute TOUTES ses compos sont
      -- gardées — jamais « personne ne le réclame » pour le cuir d'un travailleur du cuir dont le plan est muet
      local d = D()
      for _, r in ipairs(d and d.r[line] or {}) do
        local m = r[10] or {}
        for k = 1, #m - 1, 2 do
          local e = acc[m[k]]
          if not e then e = { n = 0, plan = 0 } acc[m[k]] = e end
          e.n, e.all = math.huge, true
          e.why = e.why or fmt("compo de ton métier (%s) : plan pas sûr, je garde", lineName(line):lower())
        end
      end
    end
    if T.Spread then T:Spread(M, step) else step() end
  end
  if T.Spread then T:Spread(M, step) else T:After(0, step) end
end
local usesCache = {}
local function mineUses(id)
  local c = usesCache[id]
  if c ~= nil then return c end
  local d, hit = D(), false
  for _, line in ipairs(craftLines()) do
    for _, r in ipairs(d and d.r[line] or {}) do
      local m = r[10] or {}
      for k = 1, #m - 1, 2 do if m[k] == id then hit = true break end end
      if hit then break end
    end
    if hit then break end
  end
  usesCache[id] = hit
  return hit
end
--- (gardien BAGS) à garder de cet objet : nombre (compte exact du plan + marge), raison ; 0 = rien à garder pour les métiers
function PL.KeepFor(id)
  id = num(id)
  if not id or not enabled() then return 0, nil end
  if keep.sig ~= keepSig() and not keep.busy and now() - keep.at > (keep.map and 5 or 2) then   -- (1er calcul vite, puis ≤ 1 / 5 s)
    keep.at = now()
    PL.KeepSoon()
  end
  if not keep.map then
    -- pas encore calculé : tout ce que tu en as est gardé (dans le doute on ne vend pas)
    if mineUses(id) then return max(1, PL.BagCount(id)), "compo d'un de tes métiers (plan en cours de calcul)" end
    return 0, nil
  end
  local e = keep.map[id]
  if e and e.n > 0 then return e.n, e.why end
  local w = keep.surplus and keep.surplus[id]
  if w then return 0, w end                                -- avis explicite « en trop » (voir KeepRebuild)
  return 0, nil
end
function PL.KeepSoon()
  if PL.keepPending or keep.busy then return end
  PL.keepPending = true
  T:After(0.5, function() PL.keepPending = false pcall(PL.KeepRebuild) end)
end
--- le surplus déclaré : { {id, n, why} } (ce que ni un plan ni une quête ne demandent, d'après les courses en ville)
function PL.Surplus()
  local CR = T.CityRun
  local out = {}
  if not (CR and type(CR.ExtraList) == "function") then return out end
  local ok, l = pcall(CR.ExtraList)
  for _, x in ipairs(ok and type(l) == "table" and l or {}) do out[#out + 1] = { id = x.id, n = x.n, why = x.why } end
  return out
end
function PL.RegisterKeeper()
  if PL.keeperOn then return true end
  local B = T.Bags
  if not (type(B) == "table" and type(B.RegisterKeeper) == "function") then return false end
  PL.keeperOn = pcall(B.RegisterKeeper, "Prof", function(itemID) return PL.KeepFor(itemID) end) and true or false
  return PL.keeperOn
end

-- ===================================================================== cartes de la page « Métiers »
local MODE_FR = { full = "Complet", exact = "Pile-poil" }
--- une carte par métier : { line, icon, title, goal, cost, time, next, unknown = { textes }, kind, plan }
function PL.Cards()
  local out = {}
  if not M.profs or #M.profs == 0 then M.profs = M.ScanProfessions() end
  local d = D()
  for _, pr in ipairs(M.profs or {}) do
    local kind = lineKind(pr.line)
    local rank = E.RankOf(pr.max)
    local c = { line = pr.line, kind = kind, icon = PL.LineIcon(pr.line), unknown = {},
      title = fmt("%s %d/%d (%s)", lineName(pr.line), pr.rank, pr.max, rank and rank[1] or "") }
    if kind ~= "g" and d and d.r[pr.line] and #d.r[pr.line] > 0 then
      local mode = (PL.focus and PL.focus.line == pr.line and PL.focus.mode) or PL.Mode()
      local ok, plan = pcall(PL.Compute, pr.line, mode, mode == "exact" and 300 or PL.Target(pr.line, mode))
      plan = ok and plan or nil
      if plan and not plan.bad then
        c.plan = plan
        local res = plan.res
        local gain = res.reached - plan.skill
        -- le but (fin du rang / palier) et où ton or t'arrête : dit, avec le prix de la suite
        local goal = M.TargetFor(pr)
        if plan.mode == "full" and plan.target < goal and PL.Afford then
          local _, _, fullCost, budget = PL.Afford(pr.line, goal)
          if fullCost then
            c.short = fmt("Jusqu'à %d il faut %s : tu as %s (20 %% gardés). Farme, vends, ou reviens plus tard.", goal, money(fullCost), money(gold()))
            c.budget = budget
          end
        end
        if gain > 0 then
          if c.short then c.goal = fmt("Avec ton or : %d → %d (but %d)", plan.skill, res.reached, goal)
          else c.goal = fmt("Objectif %d (%s) : %d → %d", plan.target, MODE_FR[plan.mode], plan.skill, res.reached) end
          c.cost = plan.cost
          c.time = plan.time
          c.perPt = plan.cost > 0 and floor(plan.cost / gain + 0.5) or 0
        elseif pr.rank >= pr.max then
          local nr = E.NextRank(pr.max)
          c.goal = nr and fmt("Au plafond : rang %s chez le maître", nr[1]) or "Compétence maximale"
        else
          c.goal = "Rien à gagner avec ce que tu as"
        end
        local steps = (PL.focus and PL.focus.line == pr.line and PL.steps) or PL.Steps(plan)
        local st = steps[1]
        c.next = st and st.title or nil
        c.nextSub = st and st.sub or nil
        c.step = st
        if not plan.known then c.unknown[#c.unknown + 1] = "tes recettes connues (ouvre une fois la fenêtre du métier)" end
        if not PL.TrainerSeen(pr.line) then c.unknown[#c.unknown + 1] = "les exigences du maître (passe le voir une fois)" end
        local nf = #(plan.split.farm or {})
        if nf > 0 then c.unknown[#c.unknown + 1] = fmt("le prix de %d compo%s (ni marchand ni HV scannée)", nf, nf > 1 and "s" or "") end
        if res.stop == "level" and plan.ranksEst then c.unknown[#c.unknown + 1] = "le niveau du rang suivant (valeur Classic)" end
      elseif plan and plan.bad then
        c.goal = "Plan incohérent : non affiché"
        c.unknown[#c.unknown + 1] = "ouvre ta fenêtre de métier puis passe chez ton maître"
      else
        c.goal = "Pas de recettes dans les données de TreeAI"
      end
    else
      if pr.line == 393 then c.goal = fmt("Tu peux dépecer les bêtes jusqu'au niveau %d", E.SkinLevel(pr.rank))
      elseif pr.rank >= pr.max then
        local nr = E.NextRank(pr.max)
        c.goal = nr and fmt("Au plafond : rang %s chez le maître", nr[1]) or "Compétence maximale"
      else c.goal = fmt("Monte en récoltant : %d → %d", pr.rank, pr.max) end
    end
    out[#out + 1] = c
  end
  return out
end
--- texte court d'une carte (une ligne) : « ≈ 12 min · 3 pa 40 (8 pc par point) »
function PL.CardSub(c)
  local parts = {}
  if c.goal then parts[#parts + 1] = c.goal end
  if c.time and c.time > 0 then parts[#parts + 1] = "≈ " .. PL.Minutes(c.time) end
  if c.cost then parts[#parts + 1] = c.cost > 0 and (money(c.cost) .. (c.perPt and c.perPt > 0 and fmt(" (%s par point)", money(c.perPt)) or "")) or "rien à acheter" end
  return tconcat(parts, " · ")
end

-- ===================================================================== branchements (événements du module Prof)
if type(PL.On) == "table" then
  local function arm() if not inCombat() then PL.HintArm() end end
  PL.On.ZONE_CHANGED = arm
  PL.On.ZONE_CHANGED_INDOORS = arm
  PL.On.ZONE_CHANGED_NEW_AREA = arm
end
if type(PL.On) == "table" then
  local baseRegen = PL.On.PLAYER_REGEN_ENABLED
  PL.On.PLAYER_REGEN_ENABLED = function(...)
    if baseRegen then baseRegen(...) end
    if PL.keepLater then PL.keepLater = nil PL.KeepSoon() end
  end
end
local baseEnable = PL.OnEnable
function PL.OnEnable()
  if baseEnable then pcall(baseEnable) end
  PL.RegisterKeeper()
  -- (contrat STABLE) changements de sacs regroupés : T:OnBags (≥ 0,3 s après le dernier événement, jamais dans son image)
  if type(T.OnBags) == "function" and not PL.viaOnBags then
    PL.viaOnBags = pcall(T.OnBags, T, M, function()
      if PL.BagsChanged then pcall(PL.BagsChanged) end
      if PL.keeperOn then PL.KeepSoon() end
    end) and true or false
  end
  if not PL.keeperOn then T:After(3, function() PL.RegisterKeeper() end) end   -- Bags chargé après nous
  -- (5.0 INTEG, contrat LOOK) le bouton « Métiers » de la bande : un clic = la session guidée (lancer / quitter) ;
  -- pastille = achats en attente du plan ; allumé pendant la session ; infobulle = la prochaine étape
  local Sc = T.Shortcuts
  if type(Sc) == "table" and type(Sc.SetAction) == "function" then
    pcall(Sc.SetAction, "metiers", {
      fn = function()
        if inCombat() or not enabled() or type(PL.ToggleFocus) ~= "function" then return false end   -- (la bande dit « après le combat »)
        local line = type(PL.Line) == "function" and PL.Line() or nil
        if not line and not PL.focus then return false end
        PL.ToggleFocus(line)
        return true
      end,
      count = function() local n = type(PL.PendingBuys) == "function" and tonumber(PL.PendingBuys()) or 0 if n > 0 then return n end return nil end,
      active = function() return PL.focus ~= nil end,
      tip = function()
        if PL.focus and type(PL.steps) == "table" and PL.steps[1] and PL.steps[1].title then
          return { "Session en cours — prochaine étape : " .. tostring(PL.steps[1].title), "Clic : quitter la session." }
        end
        return { "Clic : session guidée (achats exacts, banque, maître, fabrication)." }
      end })
  end
end
-- (5.0 INTEG) module coupé : le gardien se retire (le pont « Métiers » de Sacs reprend la main)
local baseDisable = PL.OnDisable
function PL.OnDisable()
  if baseDisable then pcall(baseDisable) end
  local B = T.Bags
  if PL.keeperOn and type(B) == "table" and type(B.UnregisterKeeper) == "function" then pcall(B.UnregisterKeeper, "Prof") end
  PL.keeperOn = nil
end
