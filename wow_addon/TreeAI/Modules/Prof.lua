-- TreeAI — module « Métiers » (agent PROF) : INFORMATIF, rien d'automatique (aucun craft lancé par l'addon).
-- * par métier : chemin de montée le moins cher de ta compétence à la cible (couleurs orange/jaune/vert, formule
--   Classic : orange 100 %, puis (gris − compétence) / (gris − jaune), + « Heures sup. » 4 %/rang du Legacy),
--   compos nécessaires, déjà en sac/banque, prix (vendeur → HV TreeAI `Auction.Stats` → Auctionator → fabriqué
--   soi-même → revente × 4 ≈), liste d'achat + total, « fais 5× X maintenant (orange) », prochain rang
--   (niveau + compétence) ;
-- * récolte (Herboristerie / Minage / Dépeçage) : nœuds par couleur, meilleures zones pour ta compétence, nœuds
--   que tu as récoltés (mémoire du module, clic = boussole), niveau max des bêtes à dépecer ; infobulles d'un nœud
--   (« récoltable 75/65 ») et d'une bête morte (« dépeçage 140 requis ») — repris des anciens Boost / Économie ;
-- * Cuisine / Secourisme / Pêche : astuces ; camp (Forever : objets de camp appris à 20) ;
-- * groupe : métiers des amis (Squad, champ `pr`) → qui peut fabriquer quoi pour qui ;
-- * Legacy (Forever) : les 6 métiers primaires de fabrication donnent 1 point à 150, 225 et 300.
-- Données : Data/Recipes.lua (tables client, tools/wow_build_data.py). Onglet « Métiers » (créé seulement si tu as
-- un métier), `/tai metiers`. « Fais N× X maintenant » ne monte dans « Maintenant » que si ta fenêtre de métier est
-- ouverte (sinon : onglet seulement). Calculs hors combat, ≤ 1×/3 s. Lua 5.1, aucune globale.
-- (PROFLEVEL 4.0.2) Le planificateur (E.Session / E.Split, = treeaicoach/wow/professions.py) sert l'onglet ET la page
-- « Métiers » + la session métier de Modules/ProfLevel.lua (extension de ce module : banque, courrier, marchands,
-- mode focus). Recettes : la compétence pour apprendre une recette d'entraîneur n'est pas dans les tables du client
-- (estimée = seuil jaune, la vraie est lue chez le maître) ; une recette CONNUE est utilisable tout de suite.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("Prof", {
  title = "Métiers",
  defaults = { enabled = true, overtime = 0, target = 0, knownOnly = true, nowTip = true, gather = true,
    squad = true, camp = true, steps = 4, plMode = "full", askAH = true, plCompass = true, compsCard = true, passHint = true },
  charDefaults = { known = {}, target = {}, nodes = {}, cal = {}, pl = {}, fix = {} },
  options = {
    { key = "overtime", type = "range", label = "« Heures sup. » (Legacy) : rangs pris", min = 0, max = 5, step = 1, section = "Calcul" },
    { key = "target", type = "range", label = "Compétence visée (0 = fin du rang actuel)", min = 0, max = 300, step = 5, section = "Calcul" },
    { key = "knownOnly", type = "toggle", label = "Seulement recettes connues + entraîneur (pas d'objets-recettes)", section = "Calcul" },
    { key = "steps", type = "range", label = "Étapes affichées par métier", min = 1, max = 10, step = 1, section = "Calcul" },
    { key = "nowTip", type = "toggle", label = "« Fais N× X maintenant » dans Maintenant (fenêtre de métier ouverte)", section = "Affichage" },
    { key = "gather", type = "toggle", label = "Aide à la récolte (nœuds, zones, infobulles des nœuds et des bêtes)", section = "Affichage" },
    { key = "squad", type = "toggle", label = "Métiers du groupe (fabriquer pour les amis)", section = "Affichage" },
    { key = "camp", type = "toggle", label = "Astuces de camp (Forever)", section = "Affichage" },
    { key = "askAH", type = "toggle", label = "Session métier : me demander avant de prévoir un achat à l'HV", section = "Session métier" },
    { key = "plCompass", type = "toggle", label = "Session métier : la boussole va au PNJ de chaque étape", section = "Session métier" },
    -- (4.2 FIX42) « Composants de métier » chez un marchand / un maître / à la banque (jamais pendant les quêtes)
    { key = "compsCard", type = "toggle", label = "Composants de métier : petite carte chez le marchand, le maître, la banque", section = "Session métier" },
    -- (5.0 PROF) en passant devant ton maître (place connue) : une alerte, une seule fois par lot de recettes
    { key = "passHint", type = "toggle", label = "En passant devant ton maître : « N recettes à apprendre » (une fois)", section = "Session métier" },
  },
})
if T.MODULE_TAB then T.MODULE_TAB.Prof = "prof" end

local floor, max, min, ceil = math.floor, math.max, math.min, math.ceil
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "Prof") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function now() return try(g("GetTime")) or 0 end
local function epoch() local f = g("time") or (g("os") and g("os").time) return (f and f()) or 0 end
local function opt(k) return M.db and M.db[k] ~= false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function D() return T.Data and T.Data.Recipes end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée

-- ------------------------------------------------------------------ moteur pur (testable, = professions.py)
local E = {}
M.Engine = E
E.OVERTIME = 0.04
E.LEGACY = { 150, 225, 300 }
-- rang, niveau du perso, compétence pour l'apprendre, compétence max (Forever : Artisan chez un maître spécial)
E.RANKS = { { "Apprenti", 5, 0, 75 }, { "Compagnon", 10, 50, 150 }, { "Expert", 20, 125, 225 }, { "Artisan", 35, 200, 300 } }
-- ligne de recette : 1 sort, 2 objet, 3 quantité, 4 min, 5 jaune, 6 gris, 7 points, 8 objet-recette, 9 nom, 10 compos
function E.Chance(skill, yellow, grey, overtime, minRank)
  skill, yellow, grey = tonumber(skill) or 0, tonumber(yellow) or 0, tonumber(grey) or 0
  if skill < (tonumber(minRank) or 1) or grey <= 0 or skill >= grey then return 0 end
  if skill < yellow or grey <= yellow then return 1 end
  local c = (grey - skill) / (grey - yellow) + E.OVERTIME * max(0, min(5, floor(tonumber(overtime) or 0)))
  return max(0, min(1, c))
end
function E.Color(skill, r)
  if skill < r[4] then return "red" end
  if r[6] <= 0 or skill >= r[6] then return "grey" end
  if skill < r[5] then return "orange" end
  return skill < (r[5] + r[6]) / 2 and "yellow" or "green"
end
E.COLOR_FR = { orange = "orange", yellow = "jaune", green = "vert", grey = "gris", red = "pas encore" }
E.COLOR_HEX = { orange = "ffff8040", yellow = "ffffff00", green = "ff40c040", grey = "ff808080", red = "ffff2020" }
-- coût (cuivre) des compos manquantes pour 1 fabrication ; stock[id] = quantité possédée
function E.CraftCost(r, price, stock)
  local total, m = 0, r[10] or {}
  for k = 1, #m - 1, 2 do
    local miss = max(0, m[k + 1] - ((stock and stock[m[k]]) or 0))
    if miss > 0 then total = total + miss * (price(m[k]) or 10000) end
  end
  return total
end
function E.Makeable(r, stock)
  local n, m = nil, r[10] or {}
  for k = 1, #m - 1, 2 do
    local c = floor(((stock and stock[m[k]]) or 0) / m[k + 1])
    n = n and min(n, c) or c
  end
  return n or 0
end
-- ------------------------------------------------------------------ planificateur (PROFLEVEL 4.0.2, = professions.py)
-- Une fabrication à la fois (comptes entiers) : à chaque fabrication, la recette UTILISABLE au meilleur rapport
-- (coût ou compos rares) / point attendu ; la compétence avance de chance × points (espérance : 1 / chance
-- fabrications par point en jaune / vert). Mode « full » (complet) : atteindre la cible, ce qui manque s'achète
-- (marchand au prix exact, sinon HV / estimation, sinon E.UNKNOWN : « à farmer / demander ») ; tes compos coûtent 0
-- (+ 5 % de leur valeur pour départager). Mode « exact » (pile-poil) : seulement tes compos + celles d'un marchand,
-- le plus de points possible pour la valeur consommée (vider les sacs). Au plafond du rang : le rang suivant chez le
-- maître si le niveau le permet (sinon arrêt). Utilisable = connue (lue dans la fenêtre du métier), apprise avec le
-- métier (drapeau 1), ou recette d'entraîneur dont l'exigence est atteinte (lue chez lui, sinon = seuil jaune) ;
-- un patron inconnu seulement si inp.patterns.
E.UNKNOWN = 10000
E.CRAFT_SEC = 3.5
E.STICKY, E.LEARN_COST = 0.85, 1.3
E.FLAG_AUTO, E.FLAG_EST = 1, 2
-- (5.0 PROF) une recette sous 25 % de chance (vert foncé : ≥ 4 fabrications par point) n'est prise que si AUCUNE autre
-- recette utilisable ne monte : plus de « 28× Peau traitée pour 2 points ». Fourchette d'une étape : espérance ± 1,28 σ
-- (8 fois sur 10), σ² = Σ (1 − chance) / chance par fabrication (loi géométrique par point).
E.MIN_CHANCE = 0.25
E.Z80 = 1.2816
local EPS = 1e-9
--- utilisable à `sk` ? → ok, à apprendre (« trainer » / « pattern » / nil)
function E.Usable(r, sk, known, learnAt, patterns)
  local flags = r[11] or 0
  if flags % 2 == 1 or (known and known[r[1]]) then return true, nil end   -- appris avec le métier / connue
  local item = r[8] or 0
  if item ~= 0 and not patterns then return false, nil end
  local need = (learnAt and learnAt[r[1]]) or r[4] or 1
  if sk < need then return false, nil end
  if item ~= 0 then return true, "pattern" end
  return true, known and "trainer" or nil      -- liste des recettes pas encore lue : peut-être déjà connue
end
--- chance d'un point (Classic) d'une recette utilisable : orange 1, jaune / vert (gris − sk) / (gris − jaune) + heures sup.
function E.Chance2(r, sk, overtime)
  local y, gr = r[5] or 0, r[6] or 0
  if gr <= 0 or sk >= gr then return 0 end
  if sk < y or gr <= y then return 1 end
  local c = (gr - sk) / (gr - y) + E.OVERTIME * max(0, min(5, floor(tonumber(overtime) or 0)))
  return max(0, min(1, c))
end
--- couleur d'une recette utilisable à sk (sans « pas encore » : l'exigence est jugée par E.Usable)
function E.Color2(r, sk)
  local y, gr = r[5] or 0, r[6] or 0
  if gr <= 0 or sk >= gr then return "grey" end
  if sk < y then return "orange" end
  return sk < (y + gr) / 2 and "yellow" or "green"
end
--- inp = { rows, skill, target, cap, level, mode = "full"|"exact", stock = {[id]=n}, known = {[sort]=true}|nil,
---   learnAt = {[sort]=exigence lue chez l'entraîneur}, patterns, vendor = {[id]=prix unitaire}, price = {[id]=prix}
---   ou fonction(id), value = {[id]=valeur d'une compo possédée}, overtime, ranks, craftSec, maxCrafts }
--- → { seq = { {kind="craft", spell, name, from, to, crafts, color, colorTo, learn, row} | {kind="train", at, rank,
---   level, cap} }, steps (les « craft »), train, learn = { {spell, name, at, kind, item} }, use = {[id]=n},
---   buy = {[id]=n}, unknown = {[id]=n}, var = {[id]=true} (compo d'une fabrication jaune / verte), cost, crafts,
---   reached, skill (espérance), cap, stop = "target"|"cap"|"level"|"none"|"mats"|"guard", nextRank, time }
function E.Session(inp)
  local rows = inp.rows or {}
  local skill = floor(tonumber(inp.skill) or 0)
  local target = min(300, floor(tonumber(inp.target) or 0))
  local cap = floor(tonumber(inp.cap) or 300)
  local level = tonumber(inp.level) or 60
  local exact = inp.mode == "exact"
  local known, learnAt, patterns = inp.known, inp.learnAt, inp.patterns
  local vendor, value, price = inp.vendor or {}, inp.value or {}, inp.price
  local pf
  if type(price) == "function" then pf = price else pf = function(id) return price and price[id] end end
  local ot = inp.overtime
  local st = {}
  for k, v in pairs(inp.stock or {}) do if (tonumber(v) or 0) > 0 then st[k] = v end end
  local res = { seq = {}, steps = {}, train = {}, learn = {}, use = {}, buy = {}, unknown = {}, var = {}, cost = 0, crafts = 0 }
  local planned, last, stick = {}, nil, nil
  local s, guard = skill, 0
  local maxCrafts = tonumber(inp.maxCrafts) or 3000
  while true do
    local sk = floor(s + EPS)
    if sk >= target then res.stop = "target" break end
    if sk >= cap then
      local nr
      for _, r in ipairs(inp.ranks or E.RANKS) do if r[4] > cap then nr = r break end end
      if not nr then res.stop = "cap" break end
      if level < nr[2] then res.stop, res.nextRank = "level", nr break end
      local t = { kind = "train", at = sk, rank = nr[1], level = nr[2], cap = nr[4] }
      res.train[#res.train + 1] = t
      res.seq[#res.seq + 1] = t
      cap, last = nr[4], nil
    else
      guard = guard + 1
      if guard > maxCrafts then res.stop = "guard" break end
      local best, bestV, bestC, bestL, bestT
      for _, r in ipairs(rows) do
        local ok, needL = E.Usable(r, sk, known, learnAt, patterns)
        if ok then
          local c = E.Chance2(r, sk, ot)
          if c > 0 then
            local m, cst, scarce, feasible = r[10] or {}, 0, 0, true
            for k = 1, #m - 1, 2 do
              local id, n = m[k], m[k + 1]
              local have = st[id] or 0
              local u = have < n and have or n
              local miss = n - u
              if miss > 0 then
                local vp = vendor[id]
                if vp then cst = cst + miss * vp
                elseif exact then feasible = false break
                else cst = cst + miss * (pf(id) or E.UNKNOWN) end
              end
              scarce = scarce + u * (value[id] or 1)
            end
            if feasible then
              local v
              if exact then v = (scarce + cst + 1) / (c * (r[7] or 1))
              else v = (cst + scarce * 0.05 + 1) / (c * (r[7] or 1)) end
              -- moins d'allers-retours : la recette en cours garde la main à 15 % près ; une recette encore à
              -- apprendre (visite au maître) doit faire 30 % mieux
              if stick == r[1] then v = v * E.STICKY end
              if needL and not planned[r[1]] then v = v * E.LEARN_COST end
              local tier = c >= E.MIN_CHANCE and 0 or 1
              if not bestV or tier < bestT or (tier == bestT and (v < bestV - EPS
                  or (v <= bestV + EPS and (c > bestC + EPS or (c >= bestC - EPS and r[1] < best[1]))))) then
                best, bestV, bestC, bestL, bestT = r, v, c, needL, tier
              end
            end
          end
        end
      end
      if not best then res.stop = exact and "mats" or "none" break end
      local cost0 = res.cost
      local m = best[10] or {}
      for k = 1, #m - 1, 2 do
        local id, n = m[k], m[k + 1]
        local have = st[id] or 0
        local u = have < n and have or n
        if u > 0 then st[id] = have - u res.use[id] = (res.use[id] or 0) + u end
        local miss = n - u
        if miss > 0 then
          res.buy[id] = (res.buy[id] or 0) + miss
          local p = vendor[id] or pf(id)
          if p then res.cost = res.cost + miss * p else res.unknown[id] = (res.unknown[id] or 0) + miss end
        end
        if bestC < 1 then res.var[id] = true end
      end
      if bestL and not planned[best[1]] then
        planned[best[1]] = true
        -- est : exigence non lue chez le maître (drapeau 2 de la table) → « à confirmer chez le maître »
        res.learn[#res.learn + 1] = { spell = best[1], name = best[9], at = sk, kind = bestL, item = best[8],
          req = (learnAt and learnAt[best[1]]) or best[4] or 1,
          est = (not (learnAt and learnAt[best[1]]) and floor((best[11] or 0) / 2) % 2 == 1) or nil }
      end
      s = s + bestC * (best[7] or 1)
      if s > cap then s = cap end                    -- (5.0) le jeu ne dépasse jamais le plafond du rang
      res.crafts, stick = res.crafts + 1, best[1]
      local to = floor(s + EPS)
      if last and last.spell == best[1] then
        last.crafts, last.to, last.colorTo = last.crafts + 1, to, E.Color2(best, sk)
        last.var, last.minC, last.cost1 = last.var + (1 - bestC) / bestC, min(last.minC, bestC), res.cost
      else
        last = { kind = "craft", spell = best[1], name = best[9], from = sk, to = to, crafts = 1, color = E.Color2(best, sk),
          colorTo = E.Color2(best, sk), learn = bestL, row = best, var = (1 - bestC) / bestC, minC = bestC,
          cost0 = cost0, cost1 = res.cost }   -- coût cumulé avant / après l'étape (le plan d'une cible plus basse en est le début)
        res.seq[#res.seq + 1] = last
        res.steps[#res.steps + 1] = last
      end
    end
  end
  res.reached, res.skill, res.cap = floor(s + EPS), s, cap
  res.time = res.crafts * (tonumber(inp.craftSec) or E.CRAFT_SEC)
  for _, x in ipairs(res.steps) do x.lo, x.hi = E.Range(x.crafts, x.var, x.to - x.from) end
  return res
end
--- fourchette (8 fois sur 10) du nombre de fabrications d'une étape : espérance n, variance var, points gagnés
function E.Range(n, var, points)
  n, var = tonumber(n) or 0, tonumber(var) or 0
  if var <= EPS then return n, n end
  local sd = math.sqrt(var)
  local lo = max(1, max(tonumber(points) or 0, floor(n - E.Z80 * sd + 0.5)))
  if lo > n then lo = n end
  return lo, ceil(n + E.Z80 * sd - EPS)
end
--- (5.0 PROF) garde-fous d'un plan : la liste des incohérences (vide = plan sain). Vérifié par les tests sur tous les
--- métiers et toutes les compétences, ET en jeu avant d'afficher (un plan incohérent n'est jamais montré).
---  * aucune étape sous ta compétence, au-dessus de la cible ou du plafond du rang en cours ; étapes croissantes ;
---  * aucune recette grise / pas encore apprenable / patron non demandé ; rang suivant seulement au bon niveau ;
---  * fabrications d'une étape ≤ ce que la plage de compétence demande (Σ 1 / chance + 1) ;
---  * compos = Σ fabrications × quantité, pas une de plus ; rien d'acheté tant qu'il reste du stock ; coût = Σ prix.
function E.Audit(res, inp)
  local bad = {}
  local function no(msg) if #bad < 12 then bad[#bad + 1] = msg end end
  if type(res) ~= "table" or type(inp) ~= "table" then return { "plan absent" } end
  local skill = floor(tonumber(inp.skill) or 0)
  local target = min(300, floor(tonumber(inp.target) or 0))
  local cap = floor(tonumber(inp.cap) or 300)
  local level = tonumber(inp.level) or 60
  local known, learnAt = inp.known, inp.learnAt
  local at, need, crafts = skill, {}, 0
  local curCap = cap
  for _, x in ipairs(res.seq or {}) do
    if x.kind == "train" then
      if x.at ~= curCap then no(fmt("rang %s pris à %d (plafond %d)", tostring(x.rank), x.at, curCap)) end
      if level < (x.level or 0) then no(fmt("rang %s : niveau %d requis, tu es %d", tostring(x.rank), x.level or 0, level)) end
      if (x.cap or 0) <= curCap then no("rang suivant sans plafond plus haut") end
      curCap = x.cap or curCap
    else
      local r = x.row
      if type(r) ~= "table" then no("étape sans recette") break end
      if x.from < skill then no(fmt("« %s » commence à %d, sous ta compétence %d", tostring(x.name), x.from, skill)) end
      if x.from < at then no(fmt("« %s » revient en arrière (%d après %d)", tostring(x.name), x.from, at)) end
      if x.to < x.from then no(fmt("« %s » descend (%d → %d)", tostring(x.name), x.from, x.to)) end
      if x.from >= target then no(fmt("« %s » au-delà de la cible %d", tostring(x.name), target)) end
      if x.to > curCap then no(fmt("« %s » dépasse le plafond %d", tostring(x.name), curCap)) end
      if (x.crafts or 0) < 1 then no(fmt("« %s » : 0 fabrication", tostring(x.name))) end
      local ok = E.Usable(r, x.from, known, learnAt, inp.patterns)
      if not ok then no(fmt("« %s » pas encore apprenable à %d", tostring(x.name), x.from)) end
      local c0 = E.Chance2(r, x.from, inp.overtime)
      if c0 <= 0 then no(fmt("« %s » est grise à %d", tostring(x.name), x.from)) end
      -- borne : Σ 1 / chance sur la plage (+ 1 fabrication d'arrondi, + 1 / chance au dernier point entamé)
      local bound, ups = 1, max(1, r[7] or 1)
      for k = x.from, max(x.from, x.to) do
        local c = E.Chance2(r, k, inp.overtime)
        if c > 0 then bound = bound + 1 / (c * ups) end
      end
      if (x.crafts or 0) > ceil(bound + EPS) then
        no(fmt("« %s » : %d fabrications pour %d → %d (au plus %d)", tostring(x.name), x.crafts, x.from, x.to, ceil(bound)))
      end
      if x.lo and (x.lo > x.crafts or x.hi < x.crafts or x.lo < 1) then no(fmt("« %s » : fourchette %s–%s hors de %d", tostring(x.name), tostring(x.lo), tostring(x.hi), x.crafts)) end
      local m = r[10] or {}
      for k = 1, #m - 1, 2 do need[m[k]] = (need[m[k]] or 0) + m[k + 1] * x.crafts end
      crafts = crafts + (x.crafts or 0)
      at = x.to
    end
  end
  if crafts ~= (res.crafts or 0) then no(fmt("total des fabrications %d ≠ %d", res.crafts or 0, crafts)) end
  if (res.reached or 0) < skill then no("compétence atteinte sous la compétence de départ") end
  if (res.reached or 0) > max(curCap, skill) then no(fmt("compétence atteinte %d au-delà du plafond %d", res.reached or 0, curCap)) end
  local ids = {}
  for id in pairs(need) do ids[id] = true end
  for id in pairs(res.use or {}) do ids[id] = true end
  for id in pairs(res.buy or {}) do ids[id] = true end
  local stock = inp.stock or {}
  local cost = 0
  local vendor, price = inp.vendor or {}, inp.price
  local pf
  if type(price) == "function" then pf = price else pf = function(id) return price and price[id] end end
  for id in pairs(ids) do
    local u, b = (res.use and res.use[id]) or 0, (res.buy and res.buy[id]) or 0
    if u + b ~= (need[id] or 0) then no(fmt("objet %s : %d prévus pour %d consommés", tostring(id), u + b, need[id] or 0)) end
    local have = max(0, tonumber(stock[id]) or 0)
    if u > have then no(fmt("objet %s : %d pris pour %d en stock", tostring(id), u, have)) end
    if b > 0 and u < have then no(fmt("objet %s : %d achetés alors qu'il en reste %d", tostring(id), b, have - u)) end
    local p = vendor[id] or pf(id)
    if b > 0 then
      if p then cost = cost + b * p
      elseif ((res.unknown and res.unknown[id]) or 0) ~= b then no(fmt("objet %s : sans prix, pas marqué « à farmer »", tostring(id))) end
    end
  end
  if math.abs(cost - (res.cost or 0)) > 0.5 then no(fmt("coût %d ≠ somme des achats %d", floor(res.cost or 0), floor(cost))) end
  for _, l in ipairs(res.learn or {}) do
    if l.at < skill then no(fmt("apprendre « %s » à %d, sous ta compétence %d", tostring(l.name), l.at, skill)) end
    if (l.req or 0) > l.at then no(fmt("apprendre « %s » à %d : exigence %d", tostring(l.name), l.at, l.req or 0)) end
    if l.kind == "pattern" and not inp.patterns then no(fmt("patron « %s » non demandé", tostring(l.name))) end
  end
  return bad
end
--- logistique d'un plan : d'où viennent les compos utilisées (sacs, puis banque, puis courrier ; la réserve de quête
--- est prise dans les sacs d'abord) et quoi acheter où (marchand : marge si fabrications jaunes / vertes et prix
--- ≤ 1 pa, arrondi au lot ; HV : prix connu ; sinon à farmer / demander).
--- have = { bags = {}, bank = {}, mail = {} } ; reserve = {[id]=n} ; vinfo = {[id]={prix unitaire, lot}} ;
--- price = {[id]=prix} ou fonction → { bank = {[id]=n}, mail = {[id]=n}, vendor = { {id, n, buy, margin, unit, cost} },
--- ah = { {id, n, unit, cost} }, farm = { {id, n} }, vendorCost, ahCost }
function E.Split(res, have, reserve, vinfo, price)
  have, reserve, vinfo = have or {}, reserve or {}, vinfo or {}
  local bags, bank, mail = have.bags or {}, have.bank or {}, have.mail or {}
  local pf
  if type(price) == "function" then pf = price else pf = function(id) return price and price[id] end end
  local out = { bank = {}, mail = {}, vendor = {}, ah = {}, farm = {}, vendorCost = 0, ahCost = 0 }
  local ids = {}
  for id in pairs(res.use or {}) do ids[#ids + 1] = id end
  tsort(ids)
  for _, id in ipairs(ids) do
    local n = res.use[id]
    local rsv = reserve[id] or 0
    local inBags = max(0, (bags[id] or 0) - rsv)
    rsv = max(0, rsv - (bags[id] or 0))
    local inBank = max(0, (bank[id] or 0) - rsv)
    rsv = max(0, rsv - (bank[id] or 0))
    local inMail = max(0, (mail[id] or 0) - rsv)
    local rest = max(0, n - inBags)
    local b = min(rest, inBank)
    if b > 0 then out.bank[id] = b end
    rest = rest - b
    local ml = min(rest, inMail)
    if ml > 0 then out.mail[id] = ml end
  end
  ids = {}
  for id in pairs(res.buy or {}) do ids[#ids + 1] = id end
  tsort(ids)
  for _, id in ipairs(ids) do
    local n = res.buy[id]
    local vi = vinfo[id]
    if vi then
      local unit, lot = vi[1] or 0, max(1, vi[2] or 1)
      local margin = (res.var and res.var[id] and unit <= 100) and max(1, ceil(n * 0.1)) or 0
      local want = n + margin
      local purchase = ceil(want / lot) * lot
      out.vendor[#out.vendor + 1] = { id = id, n = n, buy = purchase, margin = purchase - n, unit = unit, cost = purchase * unit }
      out.vendorCost = out.vendorCost + purchase * unit
    elseif res.unknown and res.unknown[id] then
      out.farm[#out.farm + 1] = { id = id, n = n }
    else
      local unit = pf(id) or 0
      out.ah[#out.ah + 1] = { id = id, n = n, unit = unit, cost = n * unit }
      out.ahCost = out.ahCost + n * unit
    end
  end
  return out
end
-- ancien contrat (onglet « Métiers », tests) : { steps, shopping, used, cost, reached } ; opts.knownOnly = pas de
-- patrons inconnus (défaut des nouvelles pages : ils sont listés à part, « patrons qui valent le coup »)
function E.Plan(rows, skill, target, price, stock, opts)
  opts = opts or {}
  local res = E.Session({ rows = rows, skill = skill, target = target, cap = 300, price = price, stock = stock,
    known = opts.known, learnAt = opts.learnAt, patterns = not opts.knownOnly, overtime = opts.overtime, mode = "full",
    vendor = opts.vendor })
  local steps = {}
  for _, x in ipairs(res.steps) do
    steps[#steps + 1] = { spell = x.spell, name = x.name, from = x.from, to = x.to, crafts = x.crafts, color = x.color,
      recipe = (x.learn == "pattern") and x.row[8] or 0, row = x.row, learn = x.learn }
  end
  return { steps = steps, shopping = res.buy, used = res.use, cost = floor(res.cost), reached = res.reached, res = res }
end
-- couleur d'un nœud de récolte (Classic) : orange < +25, jaune < +50, vert < +100, gris ensuite
function E.NodeColor(skill, req)
  if skill < req then return "red" end
  if skill < req + 25 then return "orange" end
  if skill < req + 50 then return "yellow" end
  if skill < req + 100 then return "green" end
  return "grey"
end
-- niveau max des bêtes à dépecer : compétence < 100 → /10 + 10, sinon /5
function E.SkinLevel(skill)
  skill = tonumber(skill) or 0
  if skill < 100 then return floor(skill / 10) + 10 end
  return floor(skill / 5)
end
-- points Legacy : { [line] = compétence } + kinds[line] ("c" = primaire de fabrication) → gagnés, prochain
function E.Legacy(profs, kinds)
  local earned, nextLine, nextStep, gap = 0, nil, nil, nil
  for line, sk in pairs(profs) do
    if (kinds[line] or "") == "c" then
      for _, step in ipairs(E.LEGACY) do
        if sk >= step then earned = earned + 1
        else
          if not gap or step - sk < gap then nextLine, nextStep, gap = line, step, step - sk end
          break
        end
      end
    end
  end
  return earned, nextLine, nextStep, gap
end
function E.NextRank(maxSkill)
  for _, r in ipairs(E.RANKS) do if r[4] > (tonumber(maxSkill) or 0) then return r end end
  return nil
end
function E.RankOf(maxSkill)
  local best = E.RANKS[1]
  for _, r in ipairs(E.RANKS) do if r[4] <= (tonumber(maxSkill) or 0) then best = r end end
  return best
end

-- ------------------------------------------------------------------ textes
local function money(c)
  c = floor(tonumber(c) or 0)
  local f = T.ProUtil and T.ProUtil.Money
  if f then local ok, s = pcall(f, c) if ok and s then return s end end
  local go, si, co = floor(c / 10000), floor(c / 100) % 100, c % 100
  if go > 0 then return si > 0 and fmt("%d po %d pa", go, si) or fmt("%d po", go) end
  if si > 0 then return co > 0 and fmt("%d pa %d pc", si, co) or fmt("%d pa", si) end
  return fmt("%d pc", co)
end
M.Money = money
local function colored(color, text)
  local hex = E.COLOR_HEX[color]
  return hex and ("|c" .. hex .. text .. "|r") or text
end

local FOLD = { ["à"] = "a", ["â"] = "a", ["ä"] = "a", ["é"] = "e", ["è"] = "e", ["ê"] = "e", ["ë"] = "e", ["î"] = "i",
  ["ï"] = "i", ["ô"] = "o", ["ö"] = "o", ["ù"] = "u", ["û"] = "u", ["ü"] = "u", ["ç"] = "c", ["É"] = "e", ["’"] = "'" }
local function fold(s)
  s = tostring(s or ""):gsub("%s*%(.-%)%s*$", "")
  for k, v in pairs(FOLD) do s = s:gsub(k, v) end
  return (s:lower():gsub("%s+", " "):gsub("^ ", ""):gsub(" $", ""))
end
M.Fold = fold

-- noms de métiers (FR client + EN) → SkillLine
local NAMES = { alchemy = 171, blacksmithing = 164, enchanting = 333, engineering = 202, leatherworking = 165,
  tailoring = 197, herbalism = 182, ["herb gathering"] = 182, mining = 186, skinning = 393, cooking = 185,
  ["first aid"] = 129, fishing = 356, herboristerie = 182, ["depecage"] = 393, ["peche"] = 356, ["secourisme"] = 129,
  ["premiers soins"] = 129 }
function M.LineOf(name)
  local f = fold(name)
  if NAMES[f] then return NAMES[f] end
  local d = D()
  for line, v in pairs(d and d.lines or {}) do if fold(v[2]) == f then return line end end
  return nil
end
local function lineName(line) local d = D() local v = d and d.lines[line] return v and v[2] or ("Métier " .. tostring(line)) end
local function lineKind(line) local d = D() local v = d and d.lines[line] return v and v[1] or "?" end
local function matName(id)
  local d = D()
  local v = d and d.mats[id]
  return (v and v[1]) or (API.ItemName and API.ItemName(id)) or ("objet " .. tostring(id))
end

-- ------------------------------------------------------------------ lecture du jeu (gardée)
-- liste { line, name, rank, max }
function M.ScanProfessions()
  local list, seen = {}, {}
  local function add(name, rank, mx, line)
    line = tonumber(line) or M.LineOf(name)
    if not line or seen[line] or (D() and not D().lines[line]) then return end
    seen[line] = true
    list[#list + 1] = { line = line, name = lineName(line), rank = tonumber(rank) or 0, max = tonumber(mx) or 75 }
  end
  local GP, GPI = g("GetProfessions"), g("GetProfessionInfo")
  if type(GP) == "function" and type(GPI) == "function" then
    local idx = { try(GP) }
    for i = 1, 6 do
      if idx[i] then
        local name, _, rank, mx, _, _, line = try(GPI, idx[i])
        if name then add(name, rank, mx, (tonumber(line) and D() and D().lines[tonumber(line)]) and line or nil) end
      end
    end
  end
  if #list == 0 and API.SkillLines then   -- Forever : C_SkillInfo (GetSkillLineInfo global retiré), Era : globales
    for _, l in ipairs(API.SkillLines()) do
      if l.name and not l.isHeader then add(l.name, l.rank, l.maxRank) end
    end
  end
  if #list == 0 then
    local n = tonumber((try(g("GetNumSkillLines")))) or 0
    for i = 1, min(n, 80) do
      local name, header, _, rank, _, _, mx = try(g("GetSkillLineInfo"), i)
      if name and not header then add(name, rank, mx) end
    end
  end
  tsort(list, function(a, b)
    local ka, kb = lineKind(a.line), lineKind(b.line)
    if ka ~= kb then return (ka == "c" and 1 or ka == "g" and 2 or 3) < (kb == "c" and 1 or kb == "g" and 2 or 3) end
    return a.line < b.line
  end)
  return list
end

-- recettes connues (fenêtre de métier ouverte) → char.known[line][sort] = 1
function M.ScanKnown()
  local d = D()
  if not d then return end
  local C = g("C_TradeSkillUI")
  local line, rows, known = nil, nil, {}
  -- Forever : pas de GetAllRecipeIDs ; GetFilteredRecipeIDs (celle de l'interface du jeu) liste la fenêtre ouverte
  local listIDs = C and (C.GetAllRecipeIDs or C.GetFilteredRecipeIDs)
  if C and type(listIDs) == "function" and C.GetRecipeInfo then
    local info = C.GetBaseProfessionInfo and try(C.GetBaseProfessionInfo)
    line = type(info) == "table" and tonumber(info.professionID or info.skillLineID) or nil
    if not (line and d.lines[line]) and C.GetTradeSkillLine then
      local a, b = try(C.GetTradeSkillLine)
      line = (tonumber(a) and d.lines[tonumber(a)]) and tonumber(a) or M.LineOf(b or a)
    end
    if not (line and d.lines[line]) and type(info) == "table" then line = M.LineOf(info.professionName or info.parentProfessionName) end
    local ids = try(listIDs)
    local cal, sk = {}, nil
    for _, p in ipairs(M.profs or {}) do if p.line == line then sk = p.rank end end
    for _, id in ipairs(type(ids) == "table" and ids or {}) do
      local r = try(C.GetRecipeInfo, id)
      if type(r) == "table" and r.learned == true then
        known[tonumber(id) or 0] = 1
        -- (PROFLEVEL) couleurs du jeu à ta compétence : gris exact (maxTrivialLevel), jaune corrigé (relativeDifficulty)
        local gx, dif = tonumber(r.maxTrivialLevel), tonumber(r.relativeDifficulty)
        if (gx and gx > 0) or (dif and sk) then cal[tonumber(id) or 0] = { g = gx, d = dif, s = sk } end
        -- (5.0 PROF) compos RÉELLES de la recette (fenêtre du jeu) : gardées seulement si elles diffèrent de la table
        if M.ReadReagents then pcall(M.ReadReagents, C, line, tonumber(id) or 0) end
      end
    end
    if line and next(cal) then
      M.char.cal = type(M.char.cal) == "table" and M.char.cal or {}
      M.char.cal[line] = cal
    end
  else
    line = M.LineOf((try(g("GetTradeSkillLine"))))
    rows = line and d.r[line]
    if rows then
      local byName = {}
      for _, r in ipairs(rows) do byName[fold(r[9])] = r[1] end
      local DIFF = { optimal = 0, medium = 1, easy = 2, trivial = 3 }
      local cal, sk = {}, nil
      for _, p in ipairs(M.profs or {}) do if p.line == line then sk = p.rank end end
      for i = 1, min(tonumber((try(g("GetNumTradeSkills")))) or 0, 500) do
        local name, kind = try(g("GetTradeSkillInfo"), i)
        local spell = name and kind ~= "header" and byName[fold(name)]
        if spell then
          known[spell] = 1
          if DIFF[kind] and sk then cal[spell] = { d = DIFF[kind], s = sk } end   -- (PROFLEVEL) couleur du jeu
        end
      end
      if next(cal) then
        M.char.cal = type(M.char.cal) == "table" and M.char.cal or {}
        M.char.cal[line] = cal
      end
    end
  end
  if line and d.lines[line] and next(known) then
    M.char.known[line] = known
    M.dirty = true
  end
  return line, known
end

--- (5.0 PROF) les compos d'une recette connue lues dans la fenêtre du métier (C_TradeSkillUI.GetRecipeSchematic : cases
--- « de base » avec un objet et une quantité). Une lecture par recette et par session, au plus 25 par passage (la
--- liste se relit à chaque mise à jour) ; char.fix[ligne][sort] = { objet, n, … } seulement si la table se trompe,
--- effacé si elle a raison. Rien n'est gardé d'une lecture vide ou incomplète.
local rgSeen, rgBudget = {}, 0
function M.ReadReagents(C, line, spell)
  if not (line and spell and spell > 0) or rgSeen[spell] or type(C.GetRecipeSchematic) ~= "function" then return end
  if rgBudget >= 25 then return end
  rgBudget = rgBudget + 1
  local sch = try(C.GetRecipeSchematic, spell, false)
  local slots = type(sch) == "table" and sch.reagentSlotSchematics
  if type(slots) ~= "table" then return end          -- pas encore chargée : relue à la prochaine mise à jour
  rgSeen[spell] = true
  local got, n = {}, 0
  for _, sl in ipairs(slots) do
    if type(sl) == "table" then
      local rt = tonumber(sl.reagentType)
      local it = type(sl.reagents) == "table" and sl.reagents[1]
      local id, q = type(it) == "table" and tonumber(it.itemID) or nil, tonumber(sl.quantityRequired)
      if (rt == nil or rt == 1) and sl.required ~= false then
        if not (id and id > 0 and q and q > 0) then return end        -- case illisible : on ne garde rien
        got[id] = (got[id] or 0) + q
        n = n + 1
      end
    end
  end
  if n == 0 then return end
  local d = D()
  local row
  for _, r in ipairs(d and d.r[line] or {}) do if r[1] == spell then row = r break end end
  if not row then return end
  local same, m, cnt = true, row[10] or {}, 0
  for k = 1, #m - 1, 2 do cnt = cnt + 1 if got[m[k]] ~= m[k + 1] then same = false end end
  if cnt ~= n then same = false end
  M.char.fix = type(M.char.fix) == "table" and M.char.fix or {}
  local fx = M.char.fix[line]
  if same then if type(fx) == "table" then fx[spell] = nil end return end
  local ids, flat = {}, {}
  for id in pairs(got) do ids[#ids + 1] = id end
  tsort(ids)
  for _, id in ipairs(ids) do flat[#flat + 1] = id flat[#flat + 1] = got[id] end
  if type(fx) ~= "table" then fx = {} M.char.fix[line] = fx end
  fx[spell] = flat
  M.fixGen = (M.fixGen or 0) + 1
end
function M.ResetReagentBudget() rgBudget = 0 end

-- stock : sacs (+ banque si le jeu la connaît) pour les compos d'un métier
local function count(id)
  if API.ItemCount then return API.ItemCount(id, true) or 0 end
  return tonumber((try(g("GetItemCount"), id, true))) or 0
end
function M.Stock(rows)
  local st = {}
  for _, r in ipairs(rows or {}) do
    local m = r[10] or {}
    for k = 1, #m - 1, 2 do if st[m[k]] == nil then st[m[k]] = count(m[k]) end end
  end
  return st
end

-- prix unitaire (cuivre) + source : "vendeur", "HV", "Auctionator", "fabriqué", "≈"
local priceCache, priceStamp = {}, 0
function M.Price(id, depth)
  depth = depth or 0
  if depth == 0 and priceCache[id] then return priceCache[id][1], priceCache[id][2] end
  local d = D()
  local v = d and d.mats[id]
  local p, src
  if v and v[3] == 1 and (v[2] or 0) > 0 then p, src = v[2], "vendeur" end
  if not p then
    local A = mod("Auction")
    if A and A.Stats then
      local ok, s = pcall(A.Stats, id)
      if ok and type(s) == "table" and tonumber(s.market) and (s.conf or 0) >= 0.3 then
        p, src = floor(s.market), s.src == "Auctionator" and "Auctionator" or "HV"
      end
    end
  end
  if not p then
    local atr = g("Auctionator")
    local api = type(atr) == "table" and atr.API and atr.API.v1
    local a = api and try(api.GetAuctionPriceByItemID, "TreeAI", id)
    if type(a) == "number" and a > 0 then p, src = floor(a), "Auctionator" end
  end
  if depth < 1 and d then  -- fabriqué par un de mes métiers (lingots, rouleaux d'étoffe…)
    if not M.madeBy then  -- index objet créé → { ligne, recette }
      M.madeBy = {}
      for line, rows in pairs(d.r) do
        for _, r in ipairs(rows) do
          if r[2] > 0 then local t = M.madeBy[r[2]] or {} t[#t + 1] = { line, r } M.madeBy[r[2]] = t end
        end
      end
    end
    local ranks = {}
    for _, pr in ipairs(M.profs or {}) do ranks[pr.line] = pr.rank end
    for _, lr in ipairs(M.madeBy[id] or {}) do
      local r = lr[2]
      do
        if ranks[lr[1]] and r[4] <= ranks[lr[1]] then
          local c = 0
          local m = r[10] or {}
          for k = 1, #m - 1, 2 do c = c + m[k + 1] * ((M.Price(m[k], depth + 1)) or 10000) end
          c = floor(c / max(1, r[3]))
          if not p or c < p then p, src = c, "fabriqué" end
        end
      end
    end
  end
  if not p and v and (v[4] or 0) > 0 then p, src = v[4] * 4, "≈" end
  if p and depth == 0 then priceCache[id] = { p, src } end
  return p, src
end
local function price(id) return (M.Price(id)) end

-- ------------------------------------------------------------------ calcul par métier
function M.TargetFor(pr)
  local t = tonumber(M.char.target[pr.line]) or tonumber(M.db.target) or 0
  if t <= pr.rank then
    t = pr.max
    if lineKind(pr.line) == "c" then  -- prochain palier Legacy s'il est dans le rang
      for _, s in ipairs(E.LEGACY) do if s > pr.rank and s <= pr.max then t = s break end end
    end
  end
  return min(300, max(t, pr.rank))
end
function M.PlanFor(pr)
  local d = D()
  local rows = d and d.r[pr.line]
  if not rows or #rows == 0 then return nil end
  local target = M.TargetFor(pr)
  local known = M.char.known[pr.line]
  if type(known) == "table" and not next(known) then known = nil end
  local L = M.Level
  local res, stock
  -- (PROFLEVEL) même plan que la page « Métiers » : sacs + banque + courrier − réserve de quête, prix du marchand / HV
  local plan = L and L.Compute and L.Compute(pr.line, "full", target)
  if plan then
    res = { steps = {}, shopping = plan.res.buy, used = plan.res.use, cost = plan.cost, reached = plan.res.reached, plan = plan }
    for _, x in ipairs(plan.res.steps) do
      res.steps[#res.steps + 1] = { spell = x.spell, name = x.name, from = x.from, to = x.to, crafts = x.crafts, color = x.color,
        recipe = 0, row = x.row, learn = x.learn }
    end
    stock = {}
    for id, n in pairs(plan.have.bags) do stock[id] = n end
  else
    stock = M.Stock(rows)
    res = E.Plan(rows, pr.rank, target, price, stock,
      { known = known, knownOnly = M.db.knownOnly, overtime = M.db.overtime })
  end
  res.target, res.stock = target, stock
  -- « maintenant » : meilleure recette faisable avec les sacs, jusqu'à ce qu'elle change de couleur
  -- (4.0 QA) seulement une recette que tu CONNAIS (lue dans la fenêtre du métier) : sans liste lue, pas de « maintenant »
  local best, bestN
  local cal = (L and L.Rows) and L.Rows(pr.line) or rows
  for _, r in ipairs(cal) do
    if known and known[r[1]] and pr.rank < r[6] then
      local n = E.Makeable(r, stock)
      local c = E.Chance2(r, pr.rank, M.db.overtime)
      if n > 0 and c >= 0.5 and (not best or c > bestN.c or (c == bestN.c and r[6] > best[6])) then
        best, bestN = r, { n = n, c = c }
      end
    end
  end
  if best then
    local col = E.Color2(best, pr.rank)
    local untilChange = col == "orange" and (best[5] - pr.rank) or ceil((best[5] + best[6]) / 2 - pr.rank)
    res.now = { row = best, n = max(1, min(bestN.n, untilChange > 0 and untilChange or bestN.n)), color = col }
  end
  return res
end

-- ------------------------------------------------------------------ récolte / astuces
local CAMP = {  -- Forever : objets de camp (appris à 20, avancés via plans de donjon) ≈ buff de classe
  [171] = "Puits de mana → Labo d'alchimie (≈ Bénédiction de sagesse)",
  [164] = "Meule → Forge de maître (≈ Totem de force de la terre)",
  [333] = "Luth enchanté → Forge arcanique (≈ Marque du fauve)",
  [202] = "Robot à compos → Établi (marchand de compos au camp)",
  [165] = "Tente → Machine à coudre (bonus d'XP de repos)",
  [197] = "Bannière de faction → Métier à tisser (≈ Esprit divin)",
  [182] = "Bougie d'encens → Hybrideur (≈ Intelligence des arcanes)",
  [186] = "Pierre d'aimant → Fonderie (≈ Bénédiction de puissance)",
  [393] = "Chaise de camp → Établi du trappeur (≈ Aura de sélénien)",
  [185] = "Feu de camp basique → expert, festin (nourriture d'endurance)",
  [129] = "Trousse de soins → Labo du médecin (≈ Mot de pouvoir : Robustesse)",
  [356] = "Bocal → Cabane de pêche (≈ Bénédiction des rois)",
}
local TIPS = {
  [185] = "Cuisine : cuisine la viande de tes zones de quête au feu de camp (orange = 100 %) ; plats Forever = petit bonus d'XP + stats.",
  [129] = "Secourisme : les bandages montent avec l'étoffe que tu loot ; Forever : les potions de soins sont en Secourisme.",
  [356] = "Pêche : monte dans les eaux de départ jusqu'à 75 avec un appât (Appât brillant, vendeur) ; la compétence exigée par zone ≈ niveau de zone × 5.",
}
function M.GatherLines(pr)
  local lines, d = {}, D()
  if not d then return lines end
  local sk = pr.rank
  if pr.line == 393 then
    lines[#lines + 1] = { text = fmt("Dépeçage %d : bêtes jusqu'au niveau %d (orange jusqu'à ~%d).", sk, E.SkinLevel(sk),
      max(1, E.SkinLevel(max(0, sk - 25)))), prio = 1 }
  end
  local nodes = d.gather[pr.line]
  if nodes then
    local byColor = { orange = {}, yellow = {}, green = {} }
    for _, n in ipairs(nodes) do
      local c = E.NodeColor(sk, n[2])
      if byColor[c] then byColor[c][#byColor[c] + 1] = fmt("%s (%d)", matName(n[1]), n[2]) end
    end
    local parts = {}
    for _, c in ipairs({ "orange", "yellow", "green" }) do
      if #byColor[c] > 0 then parts[#parts + 1] = colored(c, E.COLOR_FR[c] .. " : " .. tconcat(byColor[c], ", ", 1, min(3, #byColor[c]))) end
    end
    if #parts > 0 then lines[#lines + 1] = { text = "Nœuds : " .. tconcat(parts, " · "), prio = 1 } end
  end
  local bands = d.zones[pr.line]
  if bands then
    local here = fold(try(g("GetRealZoneText")) or try(g("GetZoneText")) or "")
    for _, b in ipairs(bands) do
      if sk >= b[1] and sk < b[2] then
        local good = false
        for _, z in ipairs(b[3]) do if fold(z) == here then good = true end end
        local zs = {}
        for i = 1, math.min(#b[3], 3) do zs[#zs + 1] = b[3][i] end
        lines[#lines + 1] = { text = good and fmt("Meilleures zones (%d–%d) : tu y es !", b[1], b[2] - 1)
          or fmt("Meilleures zones (%d–%d) : %s", b[1], b[2] - 1, tconcat(zs, ", ")), prio = good and 1 or 2,
          tooltip = tconcat(b[3], ", ") }
      end
    end
  end
  if pr.line ~= 393 then
    local l = M.NodeLine()
    if l then lines[#lines + 1] = l end
  end
  return lines
end

-- ------------------------------------------------------------------ mémoire des nœuds que TU as récoltés
local GATHER = { "herbori", "minage", "minière", "miniere", "mining", "herb gathering" }
local function isGather(spell)
  local s = type(spell) == "string" and spell:lower() or ""
  for _, k in ipairs(GATHER) do if s:find(k, 1, true) then return true end end
  return false
end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function myPos()
  local C = g("C_Map")
  if not (C and C.GetBestMapForUnit) then return nil end
  local map = try(C.GetBestMapForUnit, "player")
  local p = map and C.GetPlayerMapPosition and try(C.GetPlayerMapPosition, map, "player")
  if type(p) ~= "table" then return nil end
  local x, y
  if p.GetXY then x, y = try(p.GetXY, p) else x, y = p.x, p.y end
  if not x or secret(x) then return nil end
  return map, x, y
end
function M.RecordNode(name)
  if type(name) ~= "string" or name == "" or secret(name) or not M.char then return nil end
  local map, x, y = myPos()
  local z = try(g("GetRealZoneText")) or ""
  M.char.nodes = type(M.char.nodes) == "table" and M.char.nodes or {}
  local list = M.char.nodes
  for _, e in ipairs(list) do
    if e.n == name and e.m == map and x and e.x and math.abs(e.x - x) < 0.01 and math.abs(e.y - y) < 0.01 then
      e.t, e.c = epoch(), (e.c or 1) + 1
      return e
    end
  end
  local e = { n = name, z = z, m = map, x = x and floor(x * 1000) / 1000, y = y and floor(y * 1000) / 1000, t = epoch(), c = 1 }
  list[#list + 1] = e
  while #list > 300 do table.remove(list, 1) end
  return e
end
function M:UNIT_SPELLCAST_SENT(_, unit, target, _, spellID)
  if unit ~= "player" or not opt("gather") then return end
  local spell = API.SpellName and API.SpellName(spellID) or nil
  if isGather(spell) then M.RecordNode(target) end
end
--- « Nœuds récoltés ici : … » + clic = la boussole vers le plus proche (ou nil)
function M.NodeLine()
  local list = M.char and M.char.nodes
  if type(list) ~= "table" or #list == 0 then return nil end
  local z = try(g("GetRealZoneText")) or ""
  local map, x, y = myPos()
  local counts, nearest, nd = {}, nil, nil
  for _, e in ipairs(list) do
    if e.z == z then
      counts[e.n] = (counts[e.n] or 0) + 1
      if x and e.x and e.m == map then
        local d = (e.x - x) ^ 2 + (e.y - y) ^ 2
        if not nd or d < nd then nearest, nd = e, d end
      end
    end
  end
  local parts = {}
  for n, c in pairs(counts) do parts[#parts + 1] = cut(n, 18) .. " ×" .. c end
  tsort(parts)
  if #parts == 0 then return nil end
  local l = { text = "Nœuds récoltés ici : " .. tconcat(parts, ", ", 1, min(#parts, 4)), prio = 1 }
  if nearest then
    l.onClick = function()
      local C = T.Compass
      if C and C.SetTarget then pcall(C.SetTarget, C, { mapID = nearest.m, x = nearest.x, y = nearest.y, label = nearest.n, source = "Prof" }) end
    end
    l.tooltip = "Clic : la boussole va au plus proche (" .. cut(nearest.n, 24) .. ")."
  end
  return l
end

-- ------------------------------------------------------------------ infobulles de récolte (nœud, bête morte)
-- compétence requise par nœud (Classic, noms affichés FR / EN)
local NODES = {
  ["Filon de cuivre"] = 1, ["Copper Vein"] = 1, ["Filon d'étain"] = 65, ["Tin Vein"] = 65,
  ["Filon d'argent"] = 75, ["Silver Vein"] = 75, ["Gisement de fer"] = 125, ["Iron Deposit"] = 125,
  ["Filon d'or"] = 155, ["Gold Vein"] = 155, ["Gisement de mithril"] = 175, ["Mithril Deposit"] = 175,
  ["Filon de vrai-argent"] = 230, ["Truesilver Deposit"] = 230, ["Petit filon de thorium"] = 245,
  ["Small Thorium Vein"] = 245, ["Riche filon de thorium"] = 275, ["Rich Thorium Vein"] = 275,
  ["Pacifique"] = 1, ["Peacebloom"] = 1, ["Feuillargent"] = 1, ["Silverleaf"] = 1, ["Terrestrine"] = 15,
  ["Earthroot"] = 15, ["Mage royal"] = 50, ["Mageroyal"] = 50, ["Eglantine"] = 70, ["Églantine"] = 70,
  ["Briarthorn"] = 70, ["Etouffante"] = 85, ["Stranglekelp"] = 85, ["Doulourante"] = 100, ["Bruiseweed"] = 100,
  ["Aciérite sauvage"] = 115, ["Wild Steelbloom"] = 115, ["Tombeline"] = 120, ["Grave Moss"] = 120,
  ["Sang-royal"] = 125, ["Kingsblood"] = 125, ["Vietérule"] = 150, ["Liferoot"] = 150, ["Pâlerette"] = 160,
  ["Fadeleaf"] = 160, ["Dorépine"] = 170, ["Goldthorn"] = 170, ["Moustache de Khadgar"] = 185,
  ["Khadgar's Whisker"] = 185, ["Hivernale"] = 195, ["Wintersbite"] = 195, ["Fleur de feu"] = 205,
  ["Firebloom"] = 205, ["Lotus pourpre"] = 210, ["Purple Lotus"] = 210, ["Soleillette"] = 230, ["Sungrass"] = 230,
}
M.NODES = NODES
--- compétence de dépeçage requise pour une bête de ce niveau (Classic)
function E.SkinReq(lvl)
  lvl = tonumber(lvl) or 1
  if lvl <= 10 then return 1 elseif lvl <= 20 then return (lvl - 10) * 10 end
  return lvl * 5
end
local function skillOf(line)
  for _, pr in ipairs(M.profs or {}) do if pr.line == line then return pr.rank end end
end
local function pub(v) if secret(v) then return nil end return v end
function M.DecorateUnit(tip, unit)
  if not (M:IsEnabled() and opt("gather")) or not tip or not tip.AddLine then return end
  unit = unit or "mouseover"
  if not pub(try(g("UnitExists"), unit)) or not pub(try(g("UnitIsDead"), unit)) or pub(try(g("UnitIsPlayer"), unit)) then return end
  local skin = skillOf(393)
  if not skin then return end
  local ctype = pub(try(g("UnitCreatureType"), unit))
  if ctype ~= "Bête" and ctype ~= "Beast" and ctype ~= "Draconien" and ctype ~= "Dragonkin" then return end
  local req = E.SkinReq(pub(try(g("UnitLevel"), unit)))
  if skin >= req then tip:AddLine(("TreeAI : tu peux le dépecer (%d/%d)"):format(skin, req), 0.2, 1, 0.3)
  else tip:AddLine(("TreeAI : dépeçage %d requis (toi %d)"):format(req, skin), 1, 0.4, 0.4) end
end
function M.DecorateNode(tip)
  if not (M:IsEnabled() and opt("gather")) or not tip or T:InCombat() then return end   -- texte d'infobulle secret en combat
  local line = g("GameTooltipTextLeft1")
  local text = pub(line and line.GetText and line:GetText())
  local req = text and NODES[text]
  if not req then return end
  local mine = text:find("[Ff]ilon") or text:find("[Gg]isement") or text:find("Vein") or text:find("Deposit")
  local have = skillOf(mine and 186 or 182)
  if not have then return end
  if have >= req then tip:AddLine(("TreeAI : récoltable (%d/%d)"):format(have, req), 0.2, 1, 0.3)
  else tip:AddLine(("TreeAI : compétence %d requise (toi %d)"):format(req, have), 1, 0.4, 0.4) end
  if tip.Show then tip:Show() end
end
local hooked = false
local function hookTooltips()
  if hooked then return end
  hooked = true
  local TDP, En = g("TooltipDataProcessor"), g("Enum") and g("Enum").TooltipDataType
  local GT = g("GameTooltip")
  -- (5.0 STABLE, petit ajout gardé) crochet unique du cœur : une réponse gardée par unité, rien pour les joueurs
  local viaCore = T.OnUnitTip and T:OnUnitTip(M, function(_, unit)
    local line
    M.DecorateUnit({ AddLine = function(_, text, r, gr, b) line = { text, r, gr, b } end }, unit)
    if line then return line[1], line[2], line[3], line[4] end
  end, { combat = true })
  if viaCore then   -- (les nœuds de récolte gardent leur lecture du titre, plus bas)
  elseif TDP and TDP.AddTooltipPostCall and En and En.Unit then
    TDP.AddTooltipPostCall(En.Unit, function(tip)
      local unit = tip and tip.GetUnit and select(2, tip:GetUnit())
      pcall(M.DecorateUnit, tip, unit)
    end)
  elseif GT and GT.HookScript then
    pcall(GT.HookScript, GT, "OnTooltipSetUnit", function(self)
      local unit = self.GetUnit and select(2, self:GetUnit())
      pcall(M.DecorateUnit, self, unit)
    end)
  end
  -- objets du monde (nœuds) : pas d'unité ni d'objet -> lecture du titre de l'infobulle
  if GT and GT.HookScript then
    local busy = false
    pcall(GT.HookScript, GT, "OnShow", function(self)
      if busy or not M:IsEnabled() then return end
      if self.GetUnit and self:GetUnit() then return end
      if self.GetItem and self:GetItem() then return end
      busy = true
      pcall(M.DecorateNode, self)
      busy = false
    end)
  end
end

-- métiers des amis (Squad : st.pr = "Alchimie 150 Couture 75")
function M.ParsePr(s)
  local out = {}
  for name, rank in tostring(s or ""):gmatch("([^%d]+)%s+(%d+)") do
    name = name:gsub("^%s+", ""):gsub("%s+$", "")
    local line = M.LineOf(name)
    if line then out[#out + 1] = { line = line, rank = tonumber(rank) } end
  end
  return out
end
local OFFER = { [171] = "potions / élixirs", [164] = "armes, pierres à aiguiser", [333] = "enchantements",
  [202] = "munitions, lunettes, explosifs", [165] = "armures de cuir, nécessaires d'armure", [197] = "sacs, tissu",
  [185] = "nourriture à buff", [129] = "bandages, potions de soins", [182] = "herbes", [186] = "minerai, lingots",
  [393] = "cuir" }
function M.SquadLines()
  local lines = {}
  local SQ = T.Squad
  if not (opt("squad") and SQ and SQ.MemberList) then return lines end
  local ok, list = pcall(SQ.MemberList, SQ)
  if not ok or type(list) ~= "table" then return lines end
  local me = try(g("UnitName"), "player")
  local mine = {}
  for _, pr in ipairs(M.profs or {}) do mine[pr.line] = pr end
  for _, m in ipairs(list) do
    if m.name ~= me and type(m.st) == "table" and m.st.pr then
      local parts, offers = {}, {}
      for _, p in ipairs(M.ParsePr(m.st.pr)) do
        parts[#parts + 1] = fmt("%s %d", lineName(p.line), p.rank)
        if OFFER[p.line] and not mine[p.line] then offers[#offers + 1] = OFFER[p.line] end
      end
      if #parts > 0 then
        lines[#lines + 1] = { text = fmt("%s : %s%s", m.name, tconcat(parts, ", "),
          #offers > 0 and (" — peut te faire : " .. tconcat(offers, ", ")) or ""), prio = 1 }
      end
    end
  end
  if #lines > 0 then
    local mo = {}
    for line in pairs(mine) do if OFFER[line] then mo[#mo + 1] = OFFER[line] end end
    tsort(mo)
    if #mo > 0 then lines[#lines + 1] = { text = "Pour le groupe, tu peux fournir : " .. tconcat(mo, ", "), prio = 1 } end
  end
  return lines
end

-- sonde de l'API Legacy (noms inconnus : on ne fait que lister, jamais d'appel)
function M.LegacyAPI()
  local found = {}
  for k, v in pairs(_G) do
    if type(k) == "string" and type(v) == "table" and k:find("^C_Legacy") then found[#found + 1] = k end
  end
  tsort(found)
  return found
end

-- ------------------------------------------------------------------ assemblage
local function stepText(x)
  return fmt("%d→%d : %d× %s (%s)", x.from, x.to, x.crafts, cut(x.name, 30), colored(x.color, E.COLOR_FR[x.color] or x.color))
    .. ((x.recipe or 0) > 0 and " [recette]" or "")
end
local function shopList(res)
  local items = {}
  for id, n in pairs(res.shopping) do
    local p, src = M.Price(id)
    items[#items + 1] = { id = id, n = n, p = p, src = src, cost = n * (p or 0) }
  end
  tsort(items, function(a, b) if a.cost ~= b.cost then return a.cost > b.cost end return a.id < b.id end)
  return items
end
M.StepText = stepText

function M.Build()
  local d = D()
  local lines, out = {}, { profs = {}, plans = {} }
  M.profs = M.ScanProfessions()
  -- (5.0 INTEG) métiers relus : le gardien « Métiers » des sacs refait son relevé s'ils ont changé (compétence, métier)
  if M.Level and M.Level.KeepCheck then pcall(M.Level.KeepCheck) end
  if #M.profs == 0 then return lines, out end
  if not d then
    lines[1] = { text = "Données de recettes absentes (Data/Recipes.lua).", prio = 1 }
    return lines, out
  end
  local kinds, profsBy = {}, {}
  for line, v in pairs(d.lines) do kinds[line] = v[1] end
  for _, pr in ipairs(M.profs) do profsBy[pr.line] = pr.rank end
  -- (PROFLEVEL) session métier en cours : l'étape du moment (épinglée) + « Quitter la session »
  if M.Level and M.Level.TabLines then for _, l in ipairs(M.Level.TabLines()) do lines[#lines + 1] = l end end
  local earned, nl, ns, gap = E.Legacy(profsBy, kinds)
  out.legacy = { earned = earned, nextLine = nl, nextStep = ns, gap = gap }
  local hasCraft = false
  for _, pr in ipairs(M.profs) do if kinds[pr.line] == "c" then hasCraft = true end end
  if hasCraft then
    lines[#lines + 1] = { text = fmt("Legacy (métiers) : %d point%s gagné%s%s", earned, earned > 1 and "s" or "", earned > 1 and "s" or "",
      nl and fmt(" · prochain : %s %d (encore %d)", lineName(nl), ns, gap) or " · tout pris"), prio = 1,
      tooltip = { "Forever : 1 point d'héritage à 150, 225 et 300 dans chaque métier primaire de fabrication.",
        "« Heures sup. » (arbre Métiers) : +4 % de chance de point par rang — réglage du module." } }
  end
  lines[#lines + 1] = { text = fmt("Heures sup. : rang %d (+%d %%) · %s · [Recalculer]", tonumber(M.db.overtime) or 0,
      4 * (tonumber(M.db.overtime) or 0), M.db.knownOnly and "recettes connues + entraîneur" or "toutes recettes"),
    prio = 1, onClick = function(_, button)
      if button == "RightButton" or (g("IsShiftKeyDown") and try(g("IsShiftKeyDown"))) then M.db.knownOnly = not M.db.knownOnly
      else M.db.overtime = ((tonumber(M.db.overtime) or 0) + 1) % 6 end
      M.Refresh(0.1)
    end,
    tooltip = { "Clic : rang « Heures sup. » (arbre Legacy Métiers) +1 (0–5).",
      "Maj-clic : seulement recettes connues + entraîneur / toutes.", "Clic sur un métier : changer sa cible." } }
  local nowSet = false
  for _, pr in ipairs(M.profs) do
    local kind = kinds[pr.line]
    local rank = E.RankOf(pr.max)
    local nr = E.NextRank(pr.max)
    local head = fmt("%s %d/%d (%s)", pr.name, pr.rank, pr.max, rank[1])
    local prio = 1
    local tip = {}
    if nr then
      tip[#tip + 1] = fmt("Rang suivant : %s — niveau %d, compétence %d (max %d)%s.", nr[1], nr[2], nr[3], nr[4],
        nr[1] == "Artisan" and ", maître artisan (entraîneur spécial)" or ", entraîneur de ville")
      if pr.rank >= nr[3] and pr.max - pr.rank <= 25 then
        local lvl = tonumber((try(g("UnitLevel"), "player"))) or 1
        head = head .. (lvl >= nr[2] and fmt(" — va apprendre %s !", nr[1]) or fmt(" — %s au niveau %d", nr[1], nr[2]))
        prio = lvl >= nr[2] and 2 or 1
      end
    end
    tip[#tip + 1] = fmt("Clic : changer la cible (actuelle %d).", M.TargetFor(pr))
    local line = pr.line
    lines[#lines + 1] = { text = head, prio = prio, tooltip = tip, onClick = function() M.CycleTarget(line) end }
    out.profs[#out.profs + 1] = { line = pr.line, name = pr.name, rank = pr.rank, max = pr.max }
    if kind ~= "g" and d.r[pr.line] and #d.r[pr.line] > 0 then
      local res = M.PlanFor(pr)
      if res and #res.steps > 0 then
        out.plans[#out.plans + 1] = { line = pr.line, target = res.target, cost = res.cost, reached = res.reached,
          first = res.steps[1] and res.steps[1].name }
        if res.now and opt("nowTip") then
          local r = res.now.row
          -- (CLEAN) dans « Maintenant » seulement quand ta fenêtre de métier est ouverte ; sinon l'onglet suffit
          local open = M.TradeOpen and true or false
          local l = { text = fmt("Fais %d× %s maintenant (%s) — compos en sac", res.now.n, cut(r[9], 30),
            colored(res.now.color, E.COLOR_FR[res.now.color])), prio = open and 2 or 1, pin = open and not nowSet }
          lines[#lines + 1] = l
          nowSet = true
        end
        for k = 1, min(#res.steps, tonumber(M.db.steps) or 4) do
          local x = res.steps[k]
          local mats = {}
          local m = x.row[10] or {}
          for j = 1, #m - 1, 2 do mats[#mats + 1] = fmt("%d× %s", m[j + 1] * x.crafts, matName(m[j])) end
          lines[#lines + 1] = { text = "  " .. stepText(x), prio = 1,
            tooltip = { x.name, "Compos : " .. (#mats > 0 and tconcat(mats, ", ") or "aucune"),
              (x.recipe or 0) > 0 and ("Recette à acheter / trouver : " .. matName(x.recipe)) or "Entraîneur (ou déjà connue)" } }
        end
        if res.reached < res.target then
          lines[#lines + 1] = { text = fmt("  (aucune recette connue/entraîneur après %d)", res.reached), prio = 1 }
        end
        local shop = shopList(res)
        if #shop > 0 then
          local short, tipL = {}, {}
          for k, it in ipairs(shop) do
            local s = fmt("%d× %s", it.n, matName(it.id))
            if k <= 3 then short[#short + 1] = s end
            tipL[#tipL + 1] = fmt("%s — %s/u (%s)", s, it.p and money(it.p) or "?", it.src or "prix inconnu")
          end
          local have = {}
          for id, n in pairs(res.used) do have[#have + 1] = fmt("%d× %s", n, matName(id)) end
          tsort(have)
          if #have > 0 then tipL[#tipL + 1] = "Déjà en sac/banque : " .. tconcat(have, ", ", 1, min(8, #have)) end
          lines[#lines + 1] = { text = fmt("  Achats → %d : %s%s — total %s", res.target, tconcat(short, ", "),
            #shop > 3 and fmt(" +%d", #shop - 3) or "", money(res.cost)), prio = 1, tooltip = tipL }
        elseif #res.steps > 0 then
          lines[#lines + 1] = { text = fmt("  Tout est en sac/banque jusqu'à %d.", res.reached), prio = 1 }
        end
      end
    end
    if kind == "g" and opt("gather") then
      for _, l in ipairs(M.GatherLines(pr)) do l.text = "  " .. l.text lines[#lines + 1] = l end
    end
    if TIPS[pr.line] then lines[#lines + 1] = { text = "  " .. TIPS[pr.line], prio = 1 } end
    if opt("camp") and CAMP[pr.line] and pr.rank >= 20 then
      lines[#lines + 1] = { text = "  Camp : " .. CAMP[pr.line], prio = 1,
        tooltip = "Forever : objets de camp appris à 20 de compétence, avancés via plans (donjons) ; pose 1×/h." }
    end
  end
  for _, l in ipairs(M.SquadLines()) do lines[#lines + 1] = l end
  return lines, out
end

-- cible suivante au clic : paliers Legacy / fin de rang (75, 150, 225, 300), puis retour en auto
function M.CycleTarget(line)
  local pr
  for _, p in ipairs(M.profs or {}) do if p.line == line then pr = p end end
  if not pr then return end
  local cur = M.TargetFor(pr)
  local nextT
  for _, s in ipairs({ 75, 150, 225, 300 }) do if s > cur and s > pr.rank then nextT = s break end end
  M.char.target[line] = nextT
  M.Refresh(0.1)
end

local last = 0
function M.Update(force)
  if not M:IsEnabled() or T:InCombat() then return end  -- Forever : calculs hors combat
  if force then M.dirty = true end
  if not force and now() - last < 3 then return end
  last = now()
  -- recalcul complet seulement si quelque chose a changé (sacs, compétence, fenêtre, options, prix de l'HV) ; (5.0 PROF)
  -- au repos : plus rien toutes les 60 s (c'était 3 kinstr/min sans rien faire), une relecture de sécurité par 10 min
  if not M.dirty and M.lines and now() - (M.builtAt or 0) < 600 then return end
  M.dirty, M.builtAt = false, now()
  if now() - priceStamp > 60 or force then priceCache, priceStamp = {}, now() end
  local ok, lines, out = pcall(M.Build)
  if not ok then M:Debug("Build: " .. tostring(lines)) return end
  M.lines, M.out = lines, out
  if #(M.profs or {}) > 0 and not M.tabRegistered and T.RegisterTab then
    pcall(T.RegisterTab, T, "prof", "Métiers", 4.5)
    M.tabRegistered = true
  end
  if T.SetTabLines then
    if M.tabRegistered then pcall(T.SetTabLines, T, "prof", lines, "Prof") end  -- jamais d'onglet vide auto-créé
  else
    local first = lines[1]
    for _, l in ipairs(lines) do if l.pin then first = l end end
    if first then M:Suggest("top", T.PlainText and T.PlainText(first.text) or first.text, 1, { ttl = 120 }) end
  end
end
function M.Refresh(delay)
  M.dirty = true
  if M.pending then return end
  M.pending = true
  T:After(delay or 0.5, function() M.pending = false pcall(M.Update, true) end)
end
function M:AutoRefresh() M.Update() end

function M:TRADE_SKILL_SHOW() M.TradeOpen = true M.Refresh(0.3) end
-- (PROFLEVEL) la banque compte : relevée par ProfLevel (PL.On.BANKFRAME_OPENED), puis recalcul
function M:BANKFRAME_OPENED() M.Refresh(1) end
function M:TRADE_SKILL_CLOSE() M.TradeOpen = false M.Refresh(0.3) end
function M:TRADE_SKILL_LIST_UPDATE() if not T:InCombat() then M.ResetReagentBudget() pcall(M.ScanKnown) end M.Refresh(0.5) end
M.TRADE_SKILL_UPDATE = M.TRADE_SKILL_LIST_UPDATE

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" or not M.out then return end
  s.prof = { profs = M.out.profs, legacy = M.out.legacy, plans = M.out.plans, updated = epoch(),
    level = M.Level and M.Level.Summary and M.Level.Summary() or nil }
end

local function slash(rest)
  rest = tostring(rest or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  local a, b = rest:match("^(%S*)%s*(.-)$")
  if a == "cible" then
    local n, name = b:match("^(%d+)%s*(.*)$")
    n = tonumber(n)
    if not n then M:Print("Usage : /tai metiers cible 225 [métier]") return end
    for _, pr in ipairs(M.profs or M.ScanProfessions()) do
      if name == "" or fold(pr.name):find(fold(name), 1, true) then M.char.target[pr.line] = n end
    end
    M:Print(fmt("Cible : %d", n))
  elseif M.Level and M.Level.Slash and M.Level.Slash(a) then
    return
  elseif a == "debug" then
    local api = M.LegacyAPI()
    M:Print(fmt("Recettes : %s · métiers : %d · API Legacy : %s", D() and D().build or "absentes", #(M.profs or {}),
      #api > 0 and tconcat(api, ", ") or "aucune trouvée"))
  end
  M.Update(true)
  if T.SelectTab and M.tabRegistered then pcall(T.SelectTab, T, "prof") end
end

function M:OnEnable()
  -- un seul handler par événement et par module (Core) : celui de Prof, puis le complément de ProfLevel
  local L = M.Level
  local mine = {}
  for _, ev in ipairs({ "SKILL_LINES_CHANGED", "CHAT_MSG_SKILL", "TRADE_SKILL_SHOW", "TRADE_SKILL_LIST_UPDATE",
    "TRADE_SKILL_UPDATE", "TRADE_SKILL_CLOSE", "BAG_UPDATE_DELAYED", "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA",
    "BANKFRAME_OPENED", "UNIT_SPELLCAST_SENT" }) do
    mine[ev] = true
    local base = M[ev] or function() M.Refresh(1) end
    local extra = L and ((L.After and L.After[ev]) or (L.On and L.On[ev]))
    M:RegisterEvent(ev, extra and function(...) base(...) extra(...) end or base)
  end
  if L and L.On then
    for ev, fn in pairs(L.On) do if not mine[ev] then M:RegisterEvent(ev, fn) end end
  end
  hookTooltips()
  M:On("SUMMARY", function() pcall(M.WriteSummary, M) end)
  if T.RegisterSlash then
    T:RegisterSlash("metiers", slash, "metiers [focus|pilepoil|complet|page|cible N [métier]|debug] : montée de métier, session métier, achats, récolte")
  end
  if L and L.OnEnable then pcall(L.OnEnable) end
  M.Refresh(2)
end
function M:OnDisable()
  if M.Level and M.Level.OnDisable then pcall(M.Level.OnDisable) end
  if T.SetTabLines and M.tabRegistered then pcall(T.SetTabLines, T, "prof", {}, "Prof") end
end
function M:OnOptionChanged() M.Refresh(0.2) end
TreeAI.Prof = M
