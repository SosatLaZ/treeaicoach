-- TreeAI — module Edge2 (agent CHEAT2) : 15 nouveaux avantages « cheatés » mais LÉGAUX, en plus d'Edge (CHEAT25),
-- sans doublon avec lui ni avec Hunter / Boost / Inventory / Leveling / Squad / Stats. Conseils seulement : rien
-- n'est fait à la place du joueur (pas de cible, pas de sort, pas d'achat, pas d'objet détruit). Un clic pose au
-- plus un point sur la boussole TreeAI ou range la ligne.
-- Chaque avantage : UNE ligne qui commence par un verbe, cachée tant qu'elle ne sert à rien, coupable une par une
-- (options, sections). Prio 2 = peut monter dans « Maintenant » ; prio 1 = détail, visible dans l'onglet
-- « Avantages » (menu « Plus »). Chiffres (XP/h, taux, coûts) dans les infobulles. Jamais dans le chat.
--   Farm    : contestCamp, bestMob, patrolWarn, dropLuck, lootLeft, nodeRoute
--   Combat  : drinkSmart, buffExpire, bigPull
--   Progrès : restPlan, turninLevel, trainerPick, classQuest, questTimer
--   Sacs    : dropCheap (aussi dans l'infobulle « Sacs pleins » d'Inventory : une info = un propriétaire)
-- Coût : événements + AutoRefresh (3 s) qui ne fait qu'UNE petite vérification par passage, rien en combat
-- sauf compter les kills ; plafond session_sim ≤ 80 kinstr/min. Lua 5.1, aucune globale, API testées (pcall),
-- valeurs secrètes (Forever, en combat) ignorées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local FEATURES = {
  { "contestCamp", "Camp disputé : d'autres joueurs tapent tes monstres de quête", "Farm" },
  { "bestMob", "Monstre le plus rentable mesuré (XP/h par type de monstre)", "Farm" },
  { "patrolWarn", "Élite en patrouille repéré : attendre avant de tirer", "Farm" },
  { "dropLuck", "Objet de quête qui ne tombe pas : malchance ou mauvais monstre", "Farm" },
  { "lootLeft", "Corps pas fouillés (solo)", "Farm" },
  { "nodeRoute", "Récolte : prochain nœud repoussé de ta boucle", "Farm" },
  { "drinkSmart", "Après le combat : boire ou enchaîner", "Combat" },
  { "buffExpire", "Buff qui expire avant le prochain pull", "Combat" },
  { "bigPull", "Élite ciblé : tes gros sorts prêts", "Combat" },
  { "restPlan", "Repos : ce que tu gagnes en te déconnectant ici", "Progrès" },
  { "turninLevel", "Quêtes finies = niveau suivant d'un coup", "Progrès" },
  { "trainerPick", "Maître de classe : quoi apprendre d'abord si l'or manque", "Progrès" },
  { "classQuest", "Quête de classe de ton niveau", "Progrès" },
  { "questTimer", "Quête chronométrée : temps restant", "Progrès" },
  { "dropCheap", "Sacs pleins : quoi jeter en premier", "Sacs" },
}

local defaults = { enabled = true }
local options = {}
for _, f in ipairs(FEATURES) do
  defaults[f[1]] = true
  options[#options + 1] = { key = f[1], type = "toggle", label = f[2], section = f[3] }
end

local M = T:RegisterModule("Edge2", {
  title = "Avantages",
  defaults = defaults,
  charDefaults = { ack = {}, mobs = {}, fight = {} },
  options = options,
})
T.Edge2 = M
M.FEATURES = FEATURES
local TAB = "adv"
M.TAB = TAB
if T.MODULE_TAB then T.MODULE_TAB.Edge2 = TAB end
if T.TAB_OWNER then T.TAB_OWNER[TAB] = "Edge2" end

local floor, max, min, ceil, abs, sqrt = math.floor, math.max, math.min, math.ceil, math.abs, math.sqrt
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function num(v) v = sv(v) return tonumber(v) end
local function now() return try(g("GetTime")) or 0 end
local function opt(k) return M.db and M.db[k] ~= false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function inInstance() return try(g("IsInInstance")) and true or false end
local function resting() return try(g("IsResting")) and true or false end
local function money() return num(API.Money and API.Money() or try(g("GetMoney"))) or 0 end
local function groupSize() return num(try(g("GetNumGroupMembers"))) or 0 end
local function playerClass() return T.PlayerClass and T:PlayerClass() or nil end
local function low(s) return tostring(s or ""):lower() end

local function nm(s, n)
  if s == nil or secret(s) then return nil end
  s = tostring(s):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1"):gsub("^%s+", ""):gsub("%s+$", "")
  if s == "" or s:find("?", 1, true) or s == "nil" then return nil end
  n = n or 30
  if #s > n then s = s:sub(1, n - 1):gsub("[\192-\255][\128-\191]*$", "") .. "…" end
  return s
end
M.Name = nm
local function goldText(c)
  c = floor(abs(tonumber(c) or 0))
  local po, pa, pc = floor(c / 10000), floor(c / 100) % 100, c % 100
  if po > 0 then return pa > 0 and fmt("%d po %d pa", po, pa) or fmt("%d po", po) end
  if pa > 0 then return fmt("%d pa", pa) end
  return fmt("%d pc", pc)
end
M.Gold = goldText
local function durText(s)
  s = floor(tonumber(s) or 0)
  if s < 60 then return fmt("%d s", max(1, s)) end
  local m = floor(s / 60)
  if m >= 60 then return fmt("%d h %02d", floor(m / 60), m % 60) end
  return fmt("%d min", m)
end
M.Dur = durText
local function thousands(n)
  local s = tostring(floor(tonumber(n) or 0))
  local out = s:reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (out:gsub("^ ", ""))
end

-- ===================================================================================== sortie (une seule voie)
-- o.window (s) + o.sig : une même ligne n'est montrée que `window` s, puis se tait jusqu'à ce que le sens change.
local firstAt, firstN = {}, 0
M.shown = {}
local tabDirty = true
local function say(key, text, prio, o)
  if not opt(key) or not text then M:Clear(key) return nil end
  o = o or {}
  if o.window then
    local sig = key .. "|" .. tostring(o.sig or text)
    if not firstAt[sig] then
      firstN = firstN + 1
      if firstN > 300 then firstAt, firstN = {}, 0 end
      firstAt[sig] = now()
    end
    if now() - firstAt[sig] > o.window then M:Clear(key) return nil end
  end
  o.window, o.sig = nil, nil
  if o.sound == nil then o.sound = false end
  if o.ttl == nil then o.ttl = 60 end
  if o.cooldown == nil then o.cooldown = 120 end
  o.chat, o.tab = false, TAB
  M.shown[key] = (M.shown[key] or 0) + 1
  tabDirty = true
  return M:Suggest(key, text, prio or 1, o)
end
local function drop(key)
  if T._suggestions and T._suggestions["Edge2:" .. key] then tabDirty = true end
  M:Clear(key)
end
M.Say, M.Drop = say, drop

-- position (Compass) et distance en yards (même carte)
local function me()
  local C = T.Compass or (T.modules and T.modules.Compass)
  if C and C.Player then return try(C.Player) end
  return nil
end
local function distYd(a, b)
  if not (a and b and a.x and b.x) then return nil end
  if a.mapID and b.mapID and a.mapID ~= b.mapID then return nil end
  local dx, dy = (a.x - b.x) * 3000, (a.y - b.y) * 2000
  return sqrt(dx * dx + dy * dy)
end
local function meters(yd) return floor((yd or 0) * 0.9144 / 10 + 0.5) * 10 end
local function compassTo(mapID, x, y, label)
  return function()
    local C = T.Compass or (T.modules and T.modules.Compass)
    if C and C.SetTarget then try(C.SetTarget, C, { mapID = mapID, x = x, y = y, label = label, source = "Edge2" }) end
  end
end
local function quests()
  local Q = mod("Quests")
  return (Q and Q.lastList) or {}
end
local function mobOf(objName)
  local s = T.ObjectiveName and T.ObjectiveName(objName) or tostring(objName or "")
  s = s:gsub("%s+tuée?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", "")
  return s
end
M.MobOf = mobOf

-- ===================================================================================== moteur pur (testable)
local E = {}
M.Engine = E

--- XP gagnée dans le message « X meurt, vous gagnez 120 points d'expérience (+60 exp. repos) » -> nom, xp
function E.ParseKill(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local name, rest = msg:match("^(.-) meurt, vous gagnez (.*)$")
  if not name then name, rest = msg:match("^(.-) dies, you gain (.*)$") end
  if not name then return nil end
  return name, tonumber(tostring(rest):match("^(%d+)"))
end

--- 1. camp disputé : plates = { {name, tapped} }, mobs = set des monstres de quête -> nom, tapés, total
function E.Contest(plates, mobs)
  local by = {}
  for _, p in ipairs(plates or {}) do
    if p.name and mobs[p.name] then
      local b = by[p.name] or { n = 0, t = 0 }
      b.n = b.n + 1
      if p.tapped then b.t = b.t + 1 end
      by[p.name] = b
    end
  end
  local best, bt, bn
  for name, b in pairs(by) do
    if b.t >= 2 and b.t / b.n >= 0.5 and (not bt or b.t > bt) then best, bt, bn = name, b.t, b.n end
  end
  return best, bt, bn
end

--- 2. XP/h par monstre : rec[nom] = { k = kills, x = xp, s = secondes de combat } ; down = repos s / combat
--- -> liste triée { name, xph, xpk, sec, k } (≥ 3 kills)
function E.MobRates(rec, down)
  local out = {}
  for name, r in pairs(rec or {}) do
    if type(r) == "table" and (r.k or 0) >= 3 and (r.x or 0) > 0 then
      local xpk = r.x / r.k
      local sec = max(4, (r.s or 0) / r.k) + max(0, tonumber(down) or 0) + 4   -- + 4 s pour aller au suivant
      out[#out + 1] = { name = name, xph = xpk * 3600 / sec, xpk = xpk, sec = sec, k = r.k }
    end
  end
  tsort(out, function(a, b) if a.xph ~= b.xph then return a.xph > b.xph end return a.name < b.name end)
  return out
end
--- meilleur que le monstre courant de ≥ 25 % ? -> best, gain (0.4 = +40 %)
function E.BetterMob(rates, cur)
  local c
  for _, r in ipairs(rates) do if r.name == cur then c = r end end
  local b = rates[1]
  if not (b and c) or b.name == cur or c.xph <= 0 then return nil end
  local gain = b.xph / c.xph - 1
  if gain < 0.25 then return nil end
  return b, gain, c
end

--- 3. patrouille : apparitions (s) d'un même GUID élite -> période (médiane des écarts), prochaine dans (s)
function E.Patrol(seen, t)
  if type(seen) ~= "table" or #seen < 3 then return nil end
  local gaps = {}
  for i = 2, #seen do
    local d = seen[i] - seen[i - 1]
    if d >= 8 and d <= 600 then gaps[#gaps + 1] = d end
  end
  if #gaps < 2 then return nil end
  tsort(gaps)
  local per = gaps[ceil(#gaps / 2)]
  local nextIn = per - ((t or seen[#seen]) - seen[#seen])
  while nextIn < -5 do nextIn = nextIn + per end
  return per, max(0, nextIn)
end

--- 4. malchance : n kills sans drop pour un taux p -> probabilité (0-1) d'une telle série
function E.Streak(n, p)
  n, p = tonumber(n) or 0, tonumber(p)
  if not p or p <= 0 or p >= 1 then return nil end
  return (1 - p) ^ n
end

--- 6. prochain nœud : nodes = { {n, m, x, y, t} } (Economy), pos, epoch -> nœud, yards (repoussé ≥ 5 min, > 25 m)
function E.NextNode(nodes, pos, epoch, respawn)
  if not (pos and pos.x) then return nil end
  respawn = respawn or 300
  local best, bd
  for _, e in ipairs(nodes or {}) do
    if type(e) == "table" and e.x and e.y and (not e.m or not pos.mapID or e.m == pos.mapID)
        and (epoch or 0) - (tonumber(e.t) or 0) >= respawn then
      local d = distYd(pos, { x = e.x, y = e.y })
      if d and d > 27 and (not bd or d < bd) then best, bd = e, d end
    end
  end
  return best, bd
end

--- 7. boire ou enchaîner : pourcentages actuels et coût moyen d'un combat (0-1) -> "drink" | "chain" | nil
function E.DrinkCall(hp, mana, costHp, costMana, hasMana)
  if not (hp and costHp) then return nil end
  local short = hp < costHp + 0.1 or (hasMana and mana and costMana and mana < costMana + 0.1)
  if short then return "drink" end
  local fights = hp / max(0.05, costHp)
  if hasMana and mana and costMana then fights = min(fights, mana / max(0.05, costMana)) end
  if fights >= 2 and hp < 0.95 then return "chain", floor(fights) end
  return nil
end

--- 8. buffs qui expirent : auras = API.Auras, t -> { name, left } (durée d'origine ≥ 5 min, reste < 90 s)
function E.Expiring(auras, t)
  local out = {}
  for _, a in ipairs(auras or {}) do
    local d, x = tonumber(sv(a.duration)), tonumber(sv(a.expires))
    local name = nm(sv(a.name), 28)
    if name and d and x and d >= 300 and x > 0 then
      local left = x - t
      if left > 0 and left < 90 then out[#out + 1] = { name = name, left = left } end
    end
  end
  tsort(out, function(a, b) return a.left < b.left end)
  return out
end

--- 10. repos : xp reposée, xp du niveau, heures à l'auberge -> % d'un niveau gagné, heures jusqu'au plein
function E.RestGain(rested, xpMax, hours, inn)
  rested, xpMax = tonumber(rested) or 0, tonumber(xpMax) or 0
  if xpMax <= 0 then return nil end
  local cap = xpMax * 1.5
  local perHour = xpMax * 0.05 / 8 * (inn == false and 0.25 or 1)
  local gain = min(cap - rested, perHour * (hours or 8))
  if gain <= 0 then return 0, 0 end
  return floor(gain * 100 / xpMax + 0.5), (cap - rested) / perHour
end

--- 11. quêtes finies : list (Quests.lastList), need -> n, xp, couvre le niveau ?
function E.TurnIn(list, need)
  local n, xp = 0, 0
  for _, q in ipairs(list or {}) do
    if q.complete and tonumber(q.xp) then n, xp = n + 1, xp + q.xp end
  end
  return n, xp, (need or 0) > 0 and xp >= need
end

--- 12. maître de classe : services = { {name, rank, cost, core} }, or -> à apprendre, reportés, total
function E.TrainerPick(services, gold)
  local total = 0
  for _, s in ipairs(services) do total = total + (s.cost or 0) end
  if total <= (gold or 0) or #services == 0 then return nil, nil, total end
  local list = {}
  for i, s in ipairs(services) do list[#list + 1] = { s = s, i = i } end
  tsort(list, function(a, b)
    if (a.s.core and 1 or 0) ~= (b.s.core and 1 or 0) then return a.s.core and true or false end
    if (a.s.low and 1 or 0) ~= (b.s.low and 1 or 0) then return not a.s.low end
    if (a.s.cost or 0) ~= (b.s.cost or 0) then return (a.s.cost or 0) < (b.s.cost or 0) end
    return a.i < b.i
  end)
  local left, take, later = gold or 0, {}, {}
  for _, e in ipairs(list) do
    if not e.s.low and (e.s.cost or 0) <= left then take[#take + 1] = e.s left = left - (e.s.cost or 0)
    else later[#later + 1] = e.s end
  end
  return take, later, total
end

-- sorts essentiels par classe (noms FR, recherche « contient ») et sorts qui peuvent attendre
local CORE = {
  HUNTER = { "Morsure de serpent", "Tir des arcanes", "Coup de raptor", "Tir multiple", "Guérison du familier",
    "Morsure de la mangouste", "Tir assuré", "Feindre la mort", "Piège givrant", "Flèche-baguette" },
  WARRIOR = { "Frappe héroïque", "Pourfendre", "Exécution", "Fulgurance", "Frappe mortelle", "Sanguinaire", "Vengeance" },
  MAGE = { "Éclair de givre", "Boule de feu", "Projectiles des arcanes", "Nova de givre", "Explosion des arcanes" },
  ROGUE = { "Attaque pernicieuse", "Éviscération", "Débiter", "Coup de pied", "Évasion" },
  PRIEST = { "Soins", "Rénovation", "Mot de pouvoir : Bouclier", "Mot de l'ombre : Douleur", "Châtiment", "Mot de pouvoir : Robustesse" },
  PALADIN = { "Lumière sacrée", "Éclair lumineux", "Sceau", "Jugement", "Bénédiction" },
  DRUID = { "Toucher guérisseur", "Récupération", "Colère", "Lacérer", "Griffe", "Mutiler", "Marque du fauve" },
  SHAMAN = { "Éclair", "Horion de terre", "Vague de soins", "Arme Croque-roc", "Arme Furie-des-vents" },
  WARLOCK = { "Trait de l'ombre", "Corruption", "Malédiction d'agonie", "Drain de vie", "Captation de vie" },
}
local LOW = { "Pistage", "Œil de la bête", "Œil d'aigle", "Effrayer une bête", "Sens des démons", "Respiration aquatique",
  "Détection", "Lenteur", "Création de nourriture", "Création d'eau", "Création de pierre" }
local function hasAny(name, list)
  for _, k in ipairs(list or {}) do if name:find(k, 1, true) then return true end end
  return false
end
function E.Classify(name, cls)
  name = tostring(name or "")
  return hasAny(name, CORE[cls or ""]), hasAny(name, LOW)
end

--- 13. quêtes de classe (règles Classic, Forever peut différer : dit dans l'infobulle)
local CLASS_QUESTS = {
  HUNTER = { { 10, "apprivoise ton premier familier (quête du maître des chasseurs)" } },
  WARRIOR = { { 10, "Posture défensive (quête du maître guerrier)" }, { 30, "Posture berserker (quête du maître guerrier)" } },
  DRUID = { { 10, "Forme d'ours (quête du maître druide)" }, { 16, "Forme aquatique (quête du maître druide)" } },
  WARLOCK = { { 10, "Marcheur du Vide (quête du maître démoniste)" }, { 20, "Succube (quête du maître démoniste)" },
    { 30, "Chasseur corrompu (quête du maître démoniste)" } },
  SHAMAN = { { 4, "Totem de terre (quête du maître chaman)" }, { 10, "Totem de feu (quête du maître chaman)" },
    { 20, "Totem d'eau (quête du maître chaman)" }, { 30, "Totem d'air (quête du maître chaman)" } },
  PALADIN = { { 12, "Rédemption (quête du maître paladin)" } },
  ROGUE = { { 20, "Poisons (quête du maître voleur)" } },
}
M.CLASS_QUESTS = CLASS_QUESTS
function E.ClassQuest(cls, L, ack)
  for _, q in ipairs(CLASS_QUESTS[cls or ""] or {}) do
    if L >= q[1] and L <= q[1] + 4 and not (ack and ack["cq" .. q[1]]) then return q[1], q[2] end
  end
  return nil
end

--- 15. objets à jeter : items = { {name, value (pc la case), quest, quality} } -> 3 moins chers (pas quête)
function E.Cheapest(items, n)
  local list = {}
  for _, it in ipairs(items or {}) do
    if not it.quest and not it.keep and it.name and (it.quality or 1) <= 2 then list[#list + 1] = it end
  end
  tsort(list, function(a, b)
    local ga, gb = (a.quality or 1) == 0, (b.quality or 1) == 0
    if ga ~= gb then return ga end                                    -- le gris d'abord
    if a.value ~= b.value then return a.value < b.value end
    return a.name < b.name
  end)
  local out = {}
  for i = 1, min(n or 3, #list) do out[i] = list[i] end
  return out
end

-- gros sorts par classe (ID, rang 1) : seulement ceux que tu connais, prêts
local BIG = {
  HUNTER = { 3045, 19574, 19577, 19263 }, WARRIOR = { 1719, 12292, 20230 }, ROGUE = { 13750, 13877, 5277, 14177 },
  MAGE = { 12042, 11129, 12043, 11958 }, PRIEST = { 10060, 14751 }, PALADIN = { 642, 633 },
  DRUID = { 29166, 17116 }, SHAMAN = { 16166, 16188 }, WARLOCK = { 18288 },
}
M.BIG = BIG

-- ===================================================================================== état (session)
local fight = { start = nil, kills = {}, hp = nil, mana = nil }
local cost = { hp = nil, mana = nil, n = 0 }
local lastKillAt, pendingLoot = -1e9, 0
local curMob = nil
local streak = {}      -- [item|mob] = kills sans drop
local haveSeen = {}    -- [qid|i] = have
local patrol = {}      -- [guid] = { name, seen = {t…}, vis }
local patrolN = 0
local tick = 0

local function mobs()
  M.char.mobs = type(M.char.mobs) == "table" and M.char.mobs or {}
  return M.char.mobs
end
local function downtime()
  local S = T.Stats or mod("Stats")
  local d = S and S.Downtime and try(S.Downtime, S)
  return tonumber(d) or 0
end

-- ----------------------------------------------------------------------------- kills (XP) : 2, 4, 5
function M.OnKill(msg)
  local name, xp = E.ParseKill(msg)
  name = nm(name, 40)
  if not name then return end
  local t = now()
  lastKillAt = t
  if groupSize() <= 1 then pendingLoot = pendingLoot + 1 end
  curMob = name
  fight.kills[#fight.kills + 1] = { name = name, xp = xp }
  if not fight.start then M.Credit(fight.kills, 6) fight.kills = {} end   -- tué hors combat (rare)
  -- 4. malchance : +1 kill pour chaque objet de quête dont ce monstre est une source connue
  local S = T.Stats or mod("Stats")
  if S and S.DropByName then
    for _, q in ipairs(quests()) do
      for _, o in ipairs(q.obj or {}) do
        if o.t == "i" and not o.finished and o.name then
          local m = try(S.DropByName, S, name, o.name)
          if tonumber(m) then
            local k = o.name .. "|" .. name
            streak[k] = (streak[k] or 0) + 1
          end
        end
      end
    end
  end
end
--- crédite le temps de combat aux monstres tués pendant ce combat
function M.Credit(kills, sec)
  if #kills == 0 then return end
  local db, each = mobs(), (sec or 0) / #kills
  local n = 0
  for _ in pairs(db) do n = n + 1 end
  for _, k in ipairs(kills) do
    local r = db[k.name]
    if not r and n < 40 then r = { k = 0, x = 0, s = 0 } db[k.name] = r n = n + 1 end
    if r and k.xp then r.k, r.x, r.s = r.k + 1, r.x + k.xp, r.s + each end
    if r and r.k > 60 then r.k, r.x, r.s = r.k / 2, r.x / 2, r.s / 2 end   -- les mesures récentes pèsent plus
  end
  tabDirty = true
end

local function pct(u, maxFn, curFn)
  local m, c = num(try(g(maxFn), u)), num(try(g(curFn), u))
  if not (m and c) or m <= 0 then return nil end
  return c / m
end
local function hasMana() return num(try(g("UnitPowerType"), "player")) == 0 end

function M.FightStart()
  fight.start, fight.kills = now(), {}
  fight.hp, fight.mana = pct("player", "UnitHealthMax", "UnitHealth"), hasMana() and pct("player", "UnitPowerMax", "UnitPower") or nil
  drop("drinkSmart") drop("buffExpire") drop("bigPull") drop("patrolWarn")
end
function M.FightEnd()
  local t = now()
  local sec = fight.start and (t - fight.start) or 0
  M.Credit(fight.kills, sec)
  local hp, mana = pct("player", "UnitHealthMax", "UnitHealth"), hasMana() and pct("player", "UnitPowerMax", "UnitPower") or nil
  if #fight.kills > 0 and fight.hp and hp then
    local dh = max(0, fight.hp - hp)
    local dm = (fight.mana and mana) and max(0, fight.mana - mana) or nil
    local c = M.char.fight
    c.n = min(20, (c.n or 0) + 1)
    c.hp = ((c.hp or dh) * (c.n - 1) + dh) / c.n
    if dm then c.mana = ((c.mana or dm) * (c.n - 1) + dm) / c.n end
  end
  fight.start, fight.kills = nil, {}
  M.CheckDrink(hp, mana)
  M.CheckBestMob()
end

-- ----------------------------------------------------------------------------- 7. boire ou enchaîner
function M.CheckDrink(hp, mana)
  local c = M.char.fight
  if not opt("drinkSmart") or (c.n or 0) < 4 or inInstance() then return drop("drinkSmart") end
  local call, n = E.DrinkCall(hp, mana, c.hp, c.mana, hasMana())
  local tip = { "Coût moyen d'un combat (tes " .. c.n .. " derniers) :",
    fmt("vie %d %%%s", floor((c.hp or 0) * 100 + 0.5), c.mana and fmt(", mana %d %%", floor(c.mana * 100 + 0.5)) or "") }
  if call == "drink" then
    say("drinkSmart", "Mange / bois maintenant : pas assez pour le prochain combat", 2, { ttl = 25, cooldown = 60, tooltip = tip })
  elseif call == "chain" then
    say("drinkSmart", fmt("Enchaîne sans boire : de quoi faire encore %d combats", n), 1, { ttl = 20, cooldown = 90, tooltip = tip })
  else
    drop("drinkSmart")
  end
end

-- ----------------------------------------------------------------------------- 2. monstre le plus rentable
function M.Rates() return E.MobRates(mobs(), downtime()) end
function M.CheckBestMob()
  if not opt("bestMob") or not curMob or inInstance() then return drop("bestMob") end
  -- monstre d'un objectif encore à faire : on en a besoin, pas de conseil
  for _, q in ipairs(quests()) do
    for _, o in ipairs(q.obj or {}) do if not o.finished and mobOf(o.name) == curMob then return drop("bestMob") end end
  end
  local rates = M.Rates()
  local b, gain, c = E.BetterMob(rates, curMob)
  if not b then return drop("bestMob") end
  say("bestMob", fmt("Tue plutôt des %s : +%d %% d'XP/h mesurée que %s", nm(b.name, 24), floor(gain * 100 + 0.5), nm(curMob, 24)), 2,
    { ttl = 120, cooldown = 600, window = 180, sig = b.name .. curMob,
      tooltip = { "Mesuré sur tes kills (temps de combat + repos) :",
        fmt("%s : %s XP/h (%d kills)", b.name, thousands(b.xph), b.k), fmt("%s : %s XP/h (%d kills)", c.name, thousands(c.xph), c.k) } })
end

-- ----------------------------------------------------------------------------- 4. malchance
function M.QuestProgress()
  for _, q in ipairs(quests()) do
    for i, o in ipairs(q.obj or {}) do
      local k = tostring(q.id) .. "|" .. i
      local h = tonumber(o.have) or 0
      if haveSeen[k] and h > haveSeen[k] and o.name then
        for sk in pairs(streak) do if sk:sub(1, #o.name + 1) == o.name .. "|" then streak[sk] = 0 end end
      end
      haveSeen[k] = h
    end
  end
end
function M.CheckLuck()
  if not opt("dropLuck") then return drop("dropLuck") end
  local S = T.Stats or mod("Stats")
  if not (S and S.DropByName) then return drop("dropLuck") end
  local worst, wp, wn, wm, witem, wmob
  for k, n in pairs(streak) do
    if n >= 5 then
      local item, mob = k:match("^(.-)|(.*)$")
      local m = tonumber((try(S.DropByName, S, mob, item)))
      local p = E.Streak(n, m)
      if p and p < 0.1 and (not wp or p < wp) then worst, wp, wn, wm, witem, wmob = k, p, n, m, item, mob end
    end
  end
  if not worst then return drop("dropLuck") end
  local best, bm = nil, nil
  if S.BestSourceByName then best, bm = try(S.BestSourceByName, S, witem) end
  local tip = { fmt("%d %s sans « %s »", wn, wmob, witem), fmt("Taux mesuré : %d %% (1 chance sur %d d'une telle série)",
    floor(wm * 100 + 0.5), max(2, floor(1 / wp + 0.5))) }
  if best and best ~= wmob and tonumber(bm) and bm > wm * 1.3 then
    tip[#tip + 1] = fmt("%s : %d %%", best, floor(bm * 100 + 0.5))
    say("dropLuck", fmt("Tue plutôt des %s pour « %s » : meilleur taux mesuré", nm(best, 22), nm(witem, 22)), 2,
      { ttl = 120, cooldown = 300, tooltip = tip })
  elseif wp < 0.03 then
    say("dropLuck", fmt("Vérifie la source de « %s » : %d %s sans en avoir", nm(witem, 22), wn, nm(wmob, 20)), 2,
      { ttl = 120, cooldown = 300, tooltip = tip })
  else
    drop("dropLuck")
  end
end

-- ----------------------------------------------------------------------------- 5. corps pas fouillés
function M.OnLoot() pendingLoot = max(0, pendingLoot - 1) drop("lootLeft") end   -- il fouille : on se tait
function M.CheckLoot()
  if not opt("lootLeft") or inCombat() or groupSize() > 1 then return drop("lootLeft") end
  if now() - lastKillAt > 60 then pendingLoot = 0 end
  if pendingLoot < 3 then return drop("lootLeft") end
  say("lootLeft", fmt("Fouille tes corps : %d monstres tués sans ramasser", pendingLoot), 2,
    { ttl = 30, cooldown = 120, tooltip = "Butin, objets de quête et argent restent sur les corps (solo)." })
end

-- ----------------------------------------------------------------------------- 1. camp disputé
local function questMobs()
  local set, any = {}, false
  for _, q in ipairs(quests()) do
    if not q.complete then
      for _, o in ipairs(q.obj or {}) do
        if not o.finished and o.name then
          local m = mobOf(o.name)
          if m ~= "" then set[m], any = true, true end
        end
      end
    end
  end
  return set, any
end
function M.ScanPlates()
  local out = {}
  for i = 1, 40 do
    local u = "nameplate" .. i
    if try(g("UnitExists"), u) and not try(g("UnitIsPlayer"), u) and not try(g("UnitIsDead"), u)
        and try(g("UnitCanAttack"), "player", u) then
      local name = nm(sv(try(g("UnitName"), u)), 40)
      if name then out[#out + 1] = { name = name, tapped = sv(try(g("UnitIsTapDenied"), u)) and true or false } end
    end
  end
  return out
end
function M.CheckContest()
  if not opt("contestCamp") or inCombat() or inInstance() then return drop("contestCamp") end
  local set, any = questMobs()
  if not any then return drop("contestCamp") end
  local name, t, n = E.Contest(M.ScanPlates(), set)
  if not name then return drop("contestCamp") end
  say("contestCamp", fmt("Change de camp ou groupe-toi : %d %s sur %d tapés par d'autres", t, nm(name, 24), n), 2,
    { ttl = 40, cooldown = 180, tooltip = { "Camp disputé : les monstres tapés par un autre ne te comptent pas.",
      "En groupe, les kills de quête sont partagés : inviter l'autre joueur fait gagner les deux." } })
end

-- ----------------------------------------------------------------------------- 3. patrouille d'élite
function M.PlateSeen(u)
  if not u or inInstance() then return end
  local c = sv(try(g("UnitClassification"), u))
  if c ~= "elite" and c ~= "rareelite" then return end
  if try(g("UnitIsPlayer"), u) or not try(g("UnitCanAttack"), "player", u) then return end
  local guid, name = sv(try(g("UnitGUID"), u)), nm(sv(try(g("UnitName"), u)), 30)
  if not (guid and name) then return end
  local p = patrol[guid]
  if not p then
    if patrolN > 30 then patrol, patrolN = {}, 0 end
    p = { name = name, seen = {} }
    patrol[guid], patrolN = p, patrolN + 1
  end
  local t = now()
  if not p.vis and (#p.seen == 0 or t - p.seen[#p.seen] >= 8) then
    p.seen[#p.seen + 1] = t
    while #p.seen > 6 do table.remove(p.seen, 1) end
  end
  p.vis, p.unit = true, u
end
function M.PlateGone(u)
  for _, p in pairs(patrol) do if p.unit == u then p.vis, p.unit = false, nil end end
end
M.Patrols = function() return patrol end
function M.CheckPatrol()
  if not opt("patrolWarn") or inCombat() or inInstance() then return drop("patrolWarn") end
  local U = "target"
  if not try(g("UnitExists"), U) or try(g("UnitIsDead"), U) or try(g("UnitIsPlayer"), U)
      or not try(g("UnitCanAttack"), "player", U) then return drop("patrolWarn") end
  local tg = sv(try(g("UnitGUID"), U))
  local t = now()
  local best, bnext, bper, bvis
  for guid, p in pairs(patrol) do
    if guid ~= tg then
      local per, nextIn = E.Patrol(p.seen, t)
      local key = p.vis and -1 or nextIn
      if per and (p.vis or nextIn <= 20) and (not bnext or key < bnext) then
        best, bnext, bper, bvis = p, key, per, p.vis
      end
    end
  end
  if not best then return drop("patrolWarn") end
  local text = bvis and fmt("Attends avant de tirer : %s (élite) patrouille ici, laisse-le passer", best.name)
    or fmt("Attends avant de tirer : %s (élite) repasse dans ~%s", best.name, durText(bnext))
  say("patrolWarn", text, 2, { ttl = 15, cooldown = 30,
    tooltip = fmt("Patrouille apprise : il repasse toutes les ~%s (vu %d fois).", durText(bper), #best.seen) })
end

-- ----------------------------------------------------------------------------- 8. buff qui expire, 9. élite ciblé
local function hostileTarget()
  local U = "target"
  return try(g("UnitExists"), U) and not try(g("UnitIsDead"), U) and not try(g("UnitIsPlayer"), U)
    and try(g("UnitCanAttack"), "player", U) and true or false
end
function M.CheckBuffs()
  if not opt("buffExpire") or inCombat() or not hostileTarget() then return drop("buffExpire") end
  local list = E.Expiring(API.Auras and API.Auras("player", "HELPFUL") or {}, now())
  local b = list[1]
  if not b then return drop("buffExpire") end
  say("buffExpire", fmt("Renouvelle « %s » avant de tirer : il reste %s", b.name, durText(b.left)), 2,
    { ttl = ceil(b.left) + 2, cooldown = 60, kind = "buff" })
end
function M.ReadyBig(cls)
  local out = {}
  for _, id in ipairs(BIG[cls or ""] or {}) do
    if API.IsSpellKnown and API.IsSpellKnown(id) then
      local name = API.SpellName and API.SpellName(id)
      local d = 0
      if API.SpellCooldown then
        local _, dd = API.SpellCooldown(id)
        d = tonumber(sv(dd)) or 0
      end
      if name and d <= 1.5 then out[#out + 1] = name end
    end
  end
  return out
end
function M.CheckBig()
  if not opt("bigPull") or inCombat() or not hostileTarget() then return drop("bigPull") end
  local c = sv(try(g("UnitClassification"), "target"))
  if c ~= "elite" and c ~= "rareelite" and c ~= "worldboss" then return drop("bigPull") end
  local ready = M.ReadyBig(playerClass())
  if #ready == 0 then return drop("bigPull") end
  say("bigPull", fmt("Garde %s pour cet élite : prêt%s", tconcat(ready, " + ", 1, min(2, #ready)), #ready > 1 and "s" or ""), 2,
    { ttl = 20, cooldown = 60, tooltip = #ready > 2 and ("Aussi prêts : " .. tconcat(ready, ", ", 3)) or nil })
end

-- ----------------------------------------------------------------------------- 10. repos
function M.CheckRest()
  if not opt("restPlan") or not resting() or inCombat() or level() >= 60 then return drop("restPlan") end
  local xpMax = num(try(g("UnitXPMax"), "player"))
  local rested = num(try(g("GetXPExhaustion"))) or 0
  if not xpMax or rested >= xpMax * 1.4 then return drop("restPlan") end
  local gain, full = E.RestGain(rested, xpMax, 12, true)
  if not gain or gain < 5 then return drop("restPlan") end
  local out = E.RestGain(rested, xpMax, 12, false)
  say("restPlan", fmt("Déconnecte-toi ici si tu arrêtes : +%d %% d'un niveau en XP ×2 d'ici demain", gain), 1,
    { ttl = 300, cooldown = 1800, window = 300, sig = level(),
      tooltip = { fmt("À l'auberge / en ville : plein (150 %%) dans %s.", durText((full or 0) * 3600)),
        fmt("Dehors : seulement +%d %% (4 fois moins).", out or 0) } })
end

-- ----------------------------------------------------------------------------- 11. quêtes finies = niveau
function M.CheckTurnIn()
  if not opt("turninLevel") or level() >= 60 then return drop("turninLevel") end
  local xp, xpMax = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  if not (xp and xpMax) then return drop("turninLevel") end
  local n, total, enough = E.TurnIn(quests(), xpMax - xp)
  if not enough or n == 0 then return drop("turninLevel") end
  local what = n > 1 and fmt("tes %d quêtes finies", n) or "ta quête finie"
  say("turninLevel", fmt("Rends %s avant l'entraîneur : niveau %d d'un coup", what, level() + 1), 2,
    { ttl = 600, cooldown = 900, window = 600, sig = level(),
      tooltip = fmt("%s XP à rendre, il manque %s XP pour le niveau.", thousands(total), thousands(xpMax - xp)) })
end

-- ----------------------------------------------------------------------------- 12. maître de classe
function M.TrainerServices()
  local out = {}
  local CT = g("C_Trainer")
  local nFn = (CT and CT.GetNumTrainerServices) or g("GetNumTrainerServices")
  local n = num(try(nFn)) or 0
  local cls = playerClass()
  for i = 1, min(n, 80) do
    local name, rank, cat = try(g("GetTrainerServiceInfo"), i)
    if name and cat == "available" then
      local c = num(try(g("GetTrainerServiceCost"), i)) or 0
      local core, lowv = E.Classify(name, cls)
      out[#out + 1] = { name = nm(name, 30), rank = sv(rank), cost = c, core = core, low = lowv }
    end
  end
  return out
end
function M.CheckTrainer()
  if not opt("trainerPick") or inCombat() then return drop("trainerPick") end
  local take, later, total = E.TrainerPick(M.TrainerServices(), money())
  if not take or #take == 0 then return drop("trainerPick") end
  local names, spent = {}, 0
  for _, s in ipairs(take) do names[#names + 1] = s.name spent = spent + (s.cost or 0) end
  local skip = {}
  for _, s in ipairs(later or {}) do skip[#skip + 1] = s.name .. " (" .. goldText(s.cost) .. ")" end
  say("trainerPick", fmt("Apprends d'abord : %s", tconcat(names, ", ", 1, min(3, #names))), 2,
    { ttl = 300, cooldown = 30, tooltip = { fmt("Tout coûte %s, tu as %s.", goldText(total), goldText(money())),
      fmt("Ce choix : %s.", goldText(spent)), #skip > 0 and ("Plus tard : " .. tconcat(skip, ", ", 1, min(5, #skip))) or nil } })
end

-- ----------------------------------------------------------------------------- 13. quête de classe
function M.CheckClassQuest()
  if not opt("classQuest") then return drop("classQuest") end
  local at, what = E.ClassQuest(playerClass(), level(), M.char.ack)
  if not at then return drop("classQuest") end
  say("classQuest", "Fais ta quête de classe : " .. what, 2,
    { ttl = 900, cooldown = 3600, window = 900, sig = at,
      onClick = function() M.char.ack["cq" .. at] = true drop("classQuest") end,
      tooltip = { fmt("Quête de classe du niveau %d (règles Classic : Forever peut l'avoir changée).", at), "Clic : c'est fait, ne plus le dire." } })
end

-- ----------------------------------------------------------------------------- 14. quête chronométrée
function M.CheckTimers()
  if not opt("questTimer") then return drop("questTimer") end
  local CQ = g("C_QuestLog")
  local fn = CQ and CQ.GetTimeAllowed
  if type(fn) ~= "function" then return drop("questTimer") end
  local best, bleft, bq
  for _, q in ipairs(quests()) do
    local total, elapsed = try(fn, q.id)
    total, elapsed = tonumber(sv(total)), tonumber(sv(elapsed))
    if total and elapsed and total > 0 then
      local left = total - elapsed
      if left > 0 and (not bleft or left < bleft) then best, bleft, bq = q, left, q end
    end
  end
  if not best or bleft > 900 then return drop("questTimer") end
  local title = nm(bq.title, 28) or "ta quête"
  local text = bq.complete and fmt("Rends « %s » vite : chrono, il reste %s", title, durText(bleft))
    or fmt("Finis « %s » : chrono, il reste %s", title, durText(bleft))
  say("questTimer", text, bleft < 90 and 3 or 2, { ttl = 30, cooldown = 60, sound = bleft < 90 or false })
end

-- ----------------------------------------------------------------------------- 15. quoi jeter
local bagCache, bagDirty = nil, true
-- jamais proposés : consommables (nourriture, potions), sacs, munitions, carquois, quête
local KEEP_CLASS = { [0] = true, [1] = true, [6] = true, [11] = true, [12] = true }
function M.BagItems()
  local out = {}
  if not API.IterBags then return out end
  for _, _, it in API.IterBags() do
    if type(it) == "table" and it.itemID then
      local name, _, q, _, _, _, _, _, _, _, price, classID = API.ItemInfo(it.link or it.itemID)
      name = nm(name, 26)
      if name then
        out[#out + 1] = { name = name, value = (tonumber(price) or 0) * (tonumber(it.count) or 1), quality = tonumber(q) or 1,
          quest = tonumber(classID) == 12 or false, keep = KEEP_CLASS[tonumber(classID) or -1] or false }
      end
    end
  end
  return out
end
function M.Cheapest()
  if bagDirty or not bagCache then bagCache, bagDirty = E.Cheapest(M.BagItems(), 3), false end
  return bagCache
end
--- infobulle pour Inventory (« Sacs pleins ») : nil si coupé / rien
function M.DropTip()
  if not (M:IsEnabled() and opt("dropCheap")) then return nil end
  local list = M.Cheapest()
  if #list == 0 then return nil end
  local parts = {}
  for _, it in ipairs(list) do parts[#parts + 1] = fmt("%s (%s)", it.name, goldText(it.value)) end
  return { "À jeter d'abord (vaut le moins par case) :", tconcat(parts, ", ") }
end

-- ----------------------------------------------------------------------------- 6. boucle de récolte
local lastGather = -1e9
function M.CheckNode()
  if not opt("nodeRoute") or inCombat() or inInstance() then return drop("nodeRoute") end
  if now() - lastGather > 90 then return drop("nodeRoute") end
  local Ec = mod("Economy")
  local nodes = Ec and Ec.char and Ec.char.nodes
  local p = me()
  if type(nodes) ~= "table" or not p then return drop("nodeRoute") end
  local e, d = E.NextNode(nodes, p, num(try(g("time"))) or 0)
  if not e then return drop("nodeRoute") end
  say("nodeRoute", fmt("Va au prochain nœud : %s à %d m (clic = boussole)", nm(e.n, 22) or "nœud", meters(d)), 2,
    { ttl = 60, cooldown = 60, onClick = compassTo(e.m or p.mapID, e.x, e.y, e.n),
      tooltip = "Nœud déjà récolté il y a plus de 5 min : il a eu le temps de repousser." })
end
function M.OnCast(unit, spellID)
  if unit ~= "player" then return end
  local s = low(API.SpellName and API.SpellName(spellID))
  if s:find("herbori", 1, true) or s:find("minage", 1, true) or s:find("mining", 1, true) or s:find("herb gathering", 1, true) then
    lastGather = now()
    if T.After then T:After(1, function() pcall(M.CheckNode) end) end
  end
end

-- ===================================================================================== onglet « Avantages »
local lastSig
function M.BuildTab(force)
  if not (T.SetTabLines and (tabDirty or force)) then return end
  tabDirty = false
  local lines = {}
  -- détail : classement des monstres mesurés (infobulle = chiffres)
  local rates = M.Rates()
  if opt("bestMob") and #rates >= 2 then
    local tip = { "XP/h mesurée par monstre (combat + repos) :" }
    for i = 1, min(5, #rates) do tip[#tip + 1] = fmt("%s : %s XP/h", rates[i].name, thousands(rates[i].xph)) end
    lines[#lines + 1] = { text = fmt("Farme de préférence : %s (le plus rentable mesuré)", nm(rates[1].name, 26)), prio = 1, tooltip = tip }
  end
  if opt("dropCheap") then
    local fr, tot = 0, 0
    if API.FreeSlots then fr, tot = API.FreeSlots() end
    if (tonumber(tot) or 0) > 0 and (tonumber(fr) or 99) <= 2 then
      local tip = M.DropTip()
      if tip then lines[#lines + 1] = { text = "Jette d'abord : " .. tip[2], prio = 1, tooltip = "Les objets qui valent le moins par case (jamais les objets de quête)." } end
    end
  end
  local np = 0
  for _, p in pairs(patrol) do if E.Patrol(p.seen) then np = np + 1 end end
  if opt("patrolWarn") and np > 0 then
    lines[#lines + 1] = { text = fmt("Méfie-toi des patrouilles : %d élite%s qui tourne%s ici", np, np > 1 and "s" or "", np > 1 and "nt" or ""), prio = 1 }
  end
  if #lines == 0 and not next(M.shown) then
    lines[1] = { text = "Joue : les avantages apparaissent ici quand ils servent", prio = 1 }
  end
  local parts = {}
  for _, l in ipairs(lines) do parts[#parts + 1] = l.text end
  local sig = tconcat(parts, "\n")
  if sig == lastSig and not force then return end
  lastSig = sig
  pcall(T.SetTabLines, T, TAB, lines, "Edge2")
end

-- ===================================================================================== boucle + évènements
local function run(fn, ...) return pcall(fn, ...) end
--- une seule petite vérification par passage (3 s) ; rien de lourd en combat (AutoRefresh n'y tourne pas)
local ROT = { "CheckContest", "CheckTurnIn", "CheckLuck", "CheckLoot", "CheckTimers", "CheckRest", "CheckClassQuest" }
function M:AutoRefresh()
  tick = tick + 1
  if inCombat() then return end
  run(M[ROT[(tick % #ROT) + 1]])
  if tick % 4 == 0 then run(M.BuildTab) end
end

function M:OnEnable()
  if T.RegisterTab and not (T.tabs and T.tabs[TAB] and not T.tabs[TAB].auto) then T:RegisterTab(TAB, "Avantages", 31) end
  if T.TAB_OWNER then T.TAB_OWNER[TAB] = "Edge2" end
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg) run(M.OnKill, msg) end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() run(M.FightStart) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() run(M.FightEnd) end)
  self:RegisterEvent("LOOT_OPENED", function() run(M.OnLoot) end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function()
    if inCombat() then return end
    run(M.CheckBuffs) run(M.CheckBig) run(M.CheckPatrol)
  end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, u) run(M.PlateSeen, u) end)
  self:RegisterEvent("NAME_PLATE_UNIT_REMOVED", function(_, _, u) run(M.PlateGone, u) end)
  self:RegisterEvent("QUEST_LOG_UPDATE", function() run(M.QuestProgress) end)
  self:RegisterEvent("TRAINER_SHOW", function() run(M.CheckTrainer) end)
  self:RegisterEvent("TRAINER_UPDATE", function() run(M.CheckTrainer) end)
  self:RegisterEvent("TRAINER_CLOSED", function() drop("trainerPick") end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() if T.After then T:After(2, function() run(M.CheckClassQuest) end) end end)
  self:RegisterEvent("PLAYER_UPDATE_RESTING", function() run(M.CheckRest) end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() bagDirty = true end)
  self:RegisterEvent("UNIT_SPELLCAST_SUCCEEDED", function(_, _, unit, _, spellID) run(M.OnCast, unit, spellID) end)
  tabDirty = true
  run(M.BuildTab, true)
end
function M:OnDisable()
  if T.SetTabLines then pcall(T.SetTabLines, T, TAB, {}, "Edge2") end
  lastSig = nil
end
function M:OnOptionChanged(key, v)
  if v == false and key then drop(key) end
  tabDirty = true
  run(M.BuildTab, true)
end
function M.ResetSession()
  fight, cost = { start = nil, kills = {} }, { n = 0 }
  lastKillAt, pendingLoot, curMob, streak, haveSeen, patrol, patrolN = -1e9, 0, nil, {}, {}, {}, 0
  bagCache, bagDirty, lastGather, firstAt, firstN = nil, true, -1e9, {}, 0
  M.shown = {}
end
M.Streaks = function() return streak end
