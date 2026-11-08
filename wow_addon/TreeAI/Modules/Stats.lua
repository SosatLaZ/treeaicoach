-- TreeAI — module « Stats » (agent STATS) : UN seul centre de statistiques mesurées, `TreeAI.Stats`.
--  a) fiche du personnage (UnitStat 1-5, PA / PA distance, critique, hâte, toucher, armure, esquive / parade /
--     blocage, résistances, vitesse, dégâts mêlée / distance, regen mana, PV / mana max, niveau d'objet moyen),
--     relue sur UNIT_STATS / PLAYER_EQUIPMENT_CHANGED / UNIT_AURA(player) avec 1 s d'anti-rebond, jamais en combat
--     (valeurs « secrètes » de Forever : ignorées).
--  b) stats de jeu apprises par personnage : temps de kill (écart de niveau × type de monstre, solo / groupe),
--     vie perdue par combat (% PV : lue hors combat sur Forever, où elle est secrète en combat), temps de repos (manger / boire) par combat, morts / h
--     et causes, XP/h par activité (quêtes / farm / donjon / trajet), or/h, durée des quêtes (prise → finie →
--     rendue), vitesse de déplacement (à pied / monté), trajets bateau / zeppelin, taux de drop par objet et par
--     monstre (compte), HV (taux de vente, temps pour vendre), réparations / h, munitions / nourriture / boisson / h.
--     Totaux + fenêtre glissante (12 tranches de 5 min). Taille bornée (≈ 10-20 Ko).
--  c) API pour les autres modules : Stats:Get(key), :KillTime(levelDiff [, cls]), :XPPerHour(kind),
--     :TravelSpeed(mounted), :Drop(itemID, mobID), :Downtime(), :Sheet(), :GoldPerHour(), :PerHour(what),
--     :DeathsPerHour(), :QuestMinutes(), :SaleRate(name), :Snapshot().
--  d) onglet « Stats » (sous-menu « Plus ») : 5-8 lignes + « Exporter pour TreeAI » (summary.stats, lu par l'appli).
-- Coût : uniquement des événements + 1 minuterie de 60 s ; aucun OnUpdate ; pas de journal de combat (Forever).
-- Lua 5.1, aucune globale.
local function _ns(v) local f = rawget(_G, "issecretvalue") if f and f(v) then return nil end return v end   -- valeurs secrètes (combat) → nil
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Stats", {
  title = "Statistiques",
  defaults = { enabled = true, learn = true, tab = true },
  charDefaults = { v = 1 },
  options = {
    { key = "learn", type = "toggle", label = "Mesurer mes stats de jeu (kills, XP/h, trajets, drops…)", section = "Mesures" },
    { key = "tab", type = "toggle", label = "Page « Stats » (Outils > Autres pages)", section = "Affichage" },
  },
})
T.Stats = M

local floor, max, min, sqrt = math.floor, math.max, math.min, math.sqrt
local fmt, tconcat, tremove = string.format, table.concat, table.remove
local TAB = "stats"
local WIN_N, WIN_SEC = 12, 300            -- fenêtre glissante : 12 tranches de 5 min
local CAP_DROP_NPC, CAP_DROP_ITEM = 120, 10
local CAP_AH, CAP_CAUSES, CAP_TRANS = 40, 15, 12

local function g(name) return rawget(_G, name) end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, h = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, h end
end
local function secret(v) local f = g("issecretvalue") return v ~= nil and f ~= nil and try(f, v) and true or false end
local function num(v) if secret(v) then return nil end return tonumber(v) end
local function str(v) if secret(v) or type(v) ~= "string" or v == "" then return nil end return v end
local function now() return num(try(g("GetTime"))) or 0 end
local function epoch() return num(try(g("GetServerTime"))) or num(try(g("time"))) or floor(now()) end
local function r1(v) return v and floor(v * 10 + 0.5) / 10 or nil end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function learning() return not (M.db and M.db.learn == false) end
local function onTaxi() return try(g("UnitOnTaxi"), "player") and true or false end
local function mounted() return try(g("IsMounted")) and true or false end

-- {s, n} : somme / nombre, bornée (les données récentes pèsent plus)
local function acc(t, k, v, cap)
  if type(t) ~= "table" or v == nil then return end
  local e = t[k]
  if type(e) ~= "table" then e = { s = 0, n = 0 }; t[k] = e end
  e.s, e.n = floor(((e.s or 0) + v) * 10 + 0.5) / 10, (e.n or 0) + 1
  if cap and e.n > cap then e.s, e.n = floor(e.s * cap / e.n * 10 + 0.5) / 10, cap end
end
local function mean(e, minN)
  if type(e) == "table" and (e.n or 0) >= (minN or 3) and e.n > 0 then return e.s / e.n end
end
M.Acc, M.Mean = acc, mean

-- ------------------------------------------------------------------ stockage
local checked   -- table perso déjà vérifiée (C() est appelé souvent : la vérification ne se fait qu'une fois)
local function C()
  local c = M.char
  if c == checked then return c end
  if type(c) ~= "table" then return nil end
  checked = c
  if c.v ~= 1 then for k in pairs(c) do c[k] = nil end c.v = 1 end
  c.kill = type(c.kill) == "table" and c.kill or {}
  c.fight = type(c.fight) == "table" and c.fight or { n = 0, s = 0 }
  c.fight.dm = type(c.fight.dm) == "table" and c.fight.dm or { s = 0, n = 0 }
  c.down = type(c.down) == "table" and c.down or { s = 0 }
  c.death = type(c.death) == "table" and c.death or { n = 0, hi = 0, el = 0 }
  c.death.t = type(c.death.t) == "table" and c.death.t or {}
  c.death.c = type(c.death.c) == "table" and c.death.c or {}
  c.act = type(c.act) == "table" and c.act or {}
  for _, k in ipairs({ "q", "g", "d", "t" }) do
    if type(c.act[k]) ~= "table" then c.act[k] = { x = 0, s = 0 } end
  end
  c.gold = type(c.gold) == "table" and c.gold or { i = 0, o = 0 }
  c.played = tonumber(c.played) or 0
  c.quest = type(c.quest) == "table" and c.quest or { n = 0 }
  local q = c.quest
  for _, k in ipairs({ "a2c", "c2t", "tot", "open" }) do if type(q[k]) ~= "table" then q[k] = {} end end
  c.move = type(c.move) == "table" and c.move or {}
  for _, k in ipairs({ "w", "m" }) do if type(c.move[k]) ~= "table" then c.move[k] = { d = 0, s = 0 } end end
  c.trans = type(c.trans) == "table" and c.trans or {}
  c.rep = tonumber(c.rep) or 0
  c.use = type(c.use) == "table" and c.use or { am = 0, fd = 0, dr = 0 }
  c.ah = type(c.ah) == "table" and c.ah or {}
  c.win = type(c.win) == "table" and c.win or {}
  return c
end
M.Data = C
local function acct()   -- compte : taux de drop (les monstres sont les mêmes pour tous tes persos)
  local db = g("TreeAIDB")
  if type(db) ~= "table" then return nil end
  local s = db.stats
  if type(s) ~= "table" or s.v ~= 1 then s = { v = 1 }; db.stats = s end
  if type(s.drops) ~= "table" then s.drops = {} end
  if type(s.names) ~= "table" then s.names = {} end
  return s
end
M.Account = acct

-- ------------------------------------------------------------------ a) fiche du personnage
local ilvlCache, ilvlDirty = nil, true
local function itemLevel()
  if not ilvlDirty then return ilvlCache end
  local sum, n = 0, 0
  for slot = 1, 18 do
    if slot ~= 4 then
      local link = try(g("GetInventoryItemLink"), "player", slot)
      if type(link) == "string" then
        local lv = select(4, try(g("GetItemInfo"), link))
        local CI = g("C_Item")
        if not num(lv) and CI and CI.GetDetailedItemLevelInfo then lv = try(CI.GetDetailedItemLevelInfo, link) end
        lv = num(lv)
        if lv and lv > 0 then sum, n = sum + lv, n + 1 end
      end
    end
  end
  ilvlCache, ilvlDirty = n > 0 and r1(sum / n) or nil, false
  return ilvlCache
end
local function sum3(a, b, c) a, b, c = num(a), num(b), num(c) if not a then return nil end return a + (b or 0) + (c or 0) end
local function pct(f, ...) local v = num(try(f, ...)) return v and r1(v) or nil end

--- relit la fiche (pure lecture de l'API, ne lève jamais) -> table compacte
function M.ReadSheet()
  local s = { t = epoch() }
  local keys = { "str", "agi", "sta", "int", "spi" }
  for i = 1, 5 do local _, v = try(g("UnitStat"), "player", i) s[keys[i]] = num(v) end
  s.ap = sum3(try(g("UnitAttackPower"), "player"))
  s.rap = sum3(try(g("UnitRangedAttackPower"), "player"))
  s.crit = pct(g("GetCritChance"))
  s.rcrit = pct(g("GetRangedCritChance"))
  local sc
  for school = 2, 7 do local v = num(try(g("GetSpellCritChance"), school)) if v and (not sc or v > sc) then sc = v end end
  s.scrit = r1(sc)
  s.hit = pct(g("GetHitModifier"))
  s.shit = pct(g("GetSpellHitModifier"))
  s.haste = pct(g("GetHaste")) or pct(g("GetMeleeHaste"))
  local gcr = g("GetCombatRating")
  if gcr then
    local cr = num(try(gcr, num(g("CR_HASTE_MELEE")) or 18))
    if cr and cr > 0 then s.hasteR = cr end
  end
  local _, armor = try(g("UnitArmor"), "player")
  s.armor = num(armor)
  s.dodge, s.parry, s.block = pct(g("GetDodgeChance")), pct(g("GetParryChance")), pct(g("GetBlockChance"))
  local res, any = {}, false
  for i, school in ipairs({ 2, 3, 4, 5, 6 }) do   -- feu, nature, givre, ombre, arcanes
    local _, tot = try(g("UnitResistance"), "player", school)
    res[i] = num(tot) or 0
    if res[i] > 0 then any = true end
  end
  s.res = any and res or nil
  local _, run = try(g("GetUnitSpeed"), "player")
  run = _ns(run)
  s.speed = r1(num(run))
  local lo, hi = try(g("UnitDamage"), "player")
  local spd = try(g("UnitAttackSpeed"), "player")
  if num(lo) and num(hi) then s.dmg = { r1(num(lo)), r1(num(hi)), r1(num(spd)) } end
  local rspd, rlo, rhi = try(g("UnitRangedDamage"), "player")
  if num(rlo) and num(rhi) and num(rhi) > 0 then s.rdmg = { r1(num(rlo)), r1(num(rhi)), r1(num(rspd)) } end
  local base, cast = try(g("GetManaRegen"))
  if not num(base) then base, cast = try(g("GetPowerRegen")) end
  if num(base) then s.mp5, s.mp5c = r1(num(base) * 5), r1((num(cast) or 0) * 5) end
  s.hp = num(try(g("UnitHealthMax"), "player"))
  s.mp = num(try(g("UnitPowerMax"), "player", 0))
  s.ilvl = itemLevel()
  return s
end

local sheetPending, sheetWanted, lastSheetAt = false, false, -1e9
local auraPending = false
local auraScan   -- défini plus bas
local function refreshSheet()
  if inCombat() then sheetWanted = true return end
  sheetWanted = false
  lastSheetAt = now()
  M.sheet = M.ReadSheet()
  local c = C()
  if c then c.sheet = M.sheet end
end
-- anti-rebond : la rafale d'UNIT_AURA / UNIT_STATS d'un changement d'équipement = 1 seule relecture (1 s) ;
-- une simple aura (buff qui tombe) relit au plus toutes les 10 s
local function schedule(withSheet, fast)
  if withSheet then sheetWanted = true end
  if sheetPending then return end
  sheetPending = true
  T:After(fast and 1 or max(1, 10 - (now() - lastSheetAt)), function()
    sheetPending = false
    if sheetWanted then refreshSheet() end
  end)
end
M.Schedule = schedule
-- repas : on ne regarde les auras que si PV ou mana ne sont pas pleins (on ne mange pas plein), ou pendant un repas
local eatingNow   -- défini plus bas (vrai pendant un repas)
local function maybeEat()
  if auraPending or inCombat() then return end
  if not (eatingNow and eatingNow()) then
    local hp, hpM = num(try(g("UnitHealth"), "player")), num(try(g("UnitHealthMax"), "player"))
    local mp, mpM = num(try(g("UnitPower"), "player", 0)), num(try(g("UnitPowerMax"), "player", 0))
    if not ((hp and hpM and hp < hpM) or (mp and mpM and mpM > 0 and mp < mpM)) then return end
  end
  auraPending = true
  T:After(1, function() auraPending = false if auraScan then auraScan() end end)
end

-- ------------------------------------------------------------------ b) mesures en jouant
local CLS = { normal = "n", trivial = "n", minus = "n", elite = "e", rareelite = "e", rare = "r", worldboss = "b" }
local fight          -- combat en cours
local tick = { xp = 0, kills = 0 }
local questMark, turnInAt, lastTick = -1e9, -1e9, nil
local lastMoney, lastXP, lastXPMax, lastAmmo

-- (FIDELITY F4) Forever : la vie du joueur est SECRÈTE en combat. On la suit HORS combat (UNIT_HEALTH /
-- UNIT_MAXHEALTH : elle ne change jamais sans eux) ; au pull on part de la dernière lecture, et à la fin du combat
-- (lisible à nouveau) : vie perdue = départ − fin (net : un soin pendant le combat la réduit). Rien de deviné.
local ooc = { hp = nil, mx = nil }
function M.SnapHealth()
  if inCombat() then return end
  local hp, mx = num(try(g("UnitHealth"), "player")), num(try(g("UnitHealthMax"), "player"))
  if hp and mx and mx > 0 then ooc.hp, ooc.mx = hp, mx else ooc.hp, ooc.mx = nil, nil end
end
local function onHealth()
  local f = fight
  if not f then M.SnapHealth() return end
  if f.snap then return end
  local v = num(try(g("UnitHealth"), "player"))
  if not v then f.secret = true return end
  if f.hp and v < f.hp then f.lost = f.lost + (f.hp - v) end
  f.hp = v
end

function M.FightStart()
  local my = num(try(g("UnitLevel"), "player"))
  local lvl = num(try(g("UnitLevel"), "target"))
  local f = { t0 = now(), k = 0, lost = 0, grp = (num(try(g("GetNumGroupMembers"))) or 0) > 1 }
  f.hp = num(try(g("UnitHealth"), "player"))
  f.mx = num(try(g("UnitHealthMax"), "player"))
  if not (f.hp and f.mx and f.mx > 0) then
    if ooc.hp then f.snap, f.hp, f.mx = true, ooc.hp, ooc.mx   -- secrète en combat : la lecture d'avant le pull
    else f.secret = true end
  end
  if try(g("UnitCanAttack"), "player", "target") then
    if lvl and my and lvl > 0 then f.delta = max(-5, min(5, lvl - my)) elseif lvl == -1 then f.delta = 5 end
    f.cls = CLS[str(try(g("UnitClassification"), "target")) or "normal"] or "n"
    f.name = str(try(g("UnitName"), "target")) or M.lastFoe
  end
  fight = f
end

--- clé de temps de kill : écart (-5..5) .. type (n/e/r/b) .. "g" en groupe
function M.KillKey(delta, cls, grp)
  return tostring(max(-5, min(5, floor(tonumber(delta) or 0)))) .. (cls or "n") .. (grp and "g" or "")
end

function M.FightEnd()
  local f = fight
  fight = nil
  if not f then return end
  local c = C()
  if not (c and learning()) then return end
  if try(g("UnitIsDeadOrGhost"), "player") then return end
  local t = now()
  local dur = t - f.t0
  if dur < 1 or dur > 600 then return end
  local k = f.k
  if k == 0 and f.delta and try(g("UnitIsDead"), "target") then k = 1 end
  c.fight.n, c.fight.s = (c.fight.n or 0) + 1, r1((c.fight.s or 0) + dur)
  if k >= 1 and f.delta and dur / k <= 180 then acc(c.kill, M.KillKey(f.delta, f.cls, f.grp), dur / k, 200) end
  if f.snap then   -- départ lu avant le combat : la fin se lit maintenant (hors combat)
    local hp = num(try(g("UnitHealth"), "player"))
    if hp and f.hp then f.lost = max(0, f.hp - hp) else f.secret = true end
  end
  if not f.secret and f.mx and f.mx > 0 then acc(c.fight, "dm", min(300, f.lost * 100 / f.mx), 200) end
  M.lastFightEnd = t
  M.SnapHealth()
end

function M.OnXPMsg()
  if now() - turnInAt < 3 then return end   -- XP d'une quête rendue, pas un kill
  tick.kills = tick.kills + 1
  if fight then fight.k = fight.k + 1 end
end

function M.OnXP()
  local xp, mx = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  if not (xp and mx) then return end
  if lastXP then
    local d = xp - lastXP
    if d < 0 and lastXPMax then d = d + lastXPMax end
    if d > 0 and d < 500000 then tick.xp = tick.xp + d end
  end
  lastXP, lastXPMax = xp, mx
end

function M.OnMoney()
  local m = num(try(g("GetMoney")))
  if not m then return end
  local c = C()
  if lastMoney and c and learning() then
    local d = m - lastMoney
    if d > 0 then c.gold.i = c.gold.i + d elseif d < 0 then c.gold.o = c.gold.o - d end
  end
  lastMoney = m
end

-- manger / boire : temps de repos et consommation
local EAT = { ["Nourriture"] = "f", ["Food"] = "f", ["Manger"] = "f", ["Boisson"] = "d", ["Drink"] = "d",
  ["Boire"] = "d", ["Nourriture et boisson"] = "b", ["Food & Drink"] = "b", ["Rafraîchissement"] = "b",
  ["Refreshment"] = "b" }
M.EAT = EAT
local eatT0, eatF, eatD
eatingNow = function() return eatT0 ~= nil end
auraScan = function()
  if inCombat() then return end
  local food, drink = false, false
  local API = T.API
  for _, a in ipairs((API and API.Auras and try(API.Auras, "player", "HELPFUL")) or {}) do
    local k = type(a) == "table" and str(a.name) and EAT[a.name]
    if k == "f" or k == "b" then food = true end
    if k == "d" or k == "b" then drink = true end
  end
  local c = C()
  if not (c and learning()) then return end
  local t = now()
  if food and not eatF then c.use.fd = c.use.fd + 1 end
  if drink and not eatD then c.use.dr = c.use.dr + 1 end
  eatF, eatD = food, drink
  if (food or drink) and not eatT0 then eatT0 = t
  elseif not (food or drink) and eatT0 then
    local d = t - eatT0
    eatT0 = nil
    if d > 0 and d < 120 then c.down.s = r1((c.down.s or 0) + d) end
  end
end

-- morts et causes
function M.OnDeath()
  local c = C()
  if not (c and learning()) then return end
  local d = c.death
  d.n = d.n + 1
  d.t[#d.t + 1] = epoch()
  while #d.t > 20 do tremove(d.t, 1) end
  local f = fight
  fight = nil
  T:After(0.5, function()
    local F = T.modules and T.modules.Fight
    local r = F and F.lastRecap
    local name = (type(r) == "table" and str(r.killer)) or (f and f.name)
    if f and (f.delta or 0) >= 3 then d.hi = d.hi + 1 end
    if (f and f.cls == "e") or (type(r) == "table" and (tonumber(r.elites) or 0) > 0) then d.el = d.el + 1 end
    if name then
      name = T.Cut(name, 40, "")
      d.c[name] = (d.c[name] or 0) + 1
      local n, low, lowK = 0, nil, nil
      for k, v in pairs(d.c) do n = n + 1 if not low or v < low then low, lowK = v, k end end
      if n > CAP_CAUSES and lowK ~= name then d.c[lowK] = nil end
    end
  end)
end

-- quêtes : prise → finie → rendue
local function questComplete(idx)
  local CQ = g("C_QuestLog")
  local qid, done
  local glt = g("GetQuestLogTitle")
  if glt then
    local _, _, _, isHeader, _, isComplete, _, id = try(glt, idx)
    if not isHeader then qid, done = num(id), (isComplete == 1 or isComplete == true) end
  elseif CQ and CQ.GetInfo then
    local info = try(CQ.GetInfo, idx)
    qid = type(info) == "table" and num(info.questID) or nil
    if qid and CQ.IsComplete then done = try(CQ.IsComplete, qid) and true or false end
  end
  return qid, done
end
function M.OnAccept(a, b)
  questMark = now()
  local qid = num(b) or num(a)
  local c = C()
  if not (qid and c and learning()) then return end
  local open, n = c.quest.open, 0
  open[qid] = { a = epoch() }
  for k, v in pairs(open) do
    n = n + 1
    if type(v) ~= "table" or epoch() - (tonumber(v.a) or 0) > 7 * 86400 then open[k] = nil end
  end
  if n > 40 then   -- borne : on oublie la plus ancienne
    local oldK, oldT
    for k, v in pairs(open) do if type(v) == "table" and (not oldT or v.a < oldT) then oldK, oldT = k, v.a end end
    if oldK then open[oldK] = nil end
  end
end
function M.OnQuestWatch(idx)
  questMark = now()
  local c = C()
  if not (c and next(c.quest.open)) then return end
  T:After(1, function()
    local qid, done = questComplete(tonumber(idx) or 0)
    local o = qid and c.quest.open[qid]
    if done and type(o) == "table" and not o.c then o.c = epoch() end
  end)
end
function M.OnTurnIn(qid)
  questMark, turnInAt = now(), now()
  local c = C()
  qid = num(qid)
  local o = c and qid and c.quest.open[qid]
  if not (type(o) == "table" and learning()) then return end
  c.quest.open[qid] = nil
  local t = epoch()
  local tot = t - (tonumber(o.a) or t)
  if tot < 5 or tot > 4 * 3600 then return end
  c.quest.n = (c.quest.n or 0) + 1
  acc(c.quest, "tot", tot, 100)
  local cT = tonumber(o.c)
  if cT and cT >= o.a and cT <= t then
    acc(c.quest, "a2c", cT - o.a, 100)
    acc(c.quest, "c2t", t - cT, 100)
  end
end

-- déplacements (à pied / monté) : début → arrêt, distance à vol d'oiseau
local mv
local function position()
  local y, x, _, inst = try(g("UnitPosition"), "player")
  y, x, inst = num(y), num(x), num(inst)
  if x and y then return { w = true, x = x, y = y, i = inst } end
  local Geo = T.Geo
  if Geo and Geo.Player then
    local map, px, py = Geo.Player()
    if map and px then return { map = map, x = px, y = py } end
  end
end
local function distance(a, b)
  if not (a and b) then return nil end
  if a.w and b.w then
    if a.i ~= b.i then return nil end
    return sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2)
  end
  local Geo = T.Geo
  if a.map and b.map and Geo and Geo.Dist then return Geo.Dist(a.map, a.x, a.y, b.map, b.x, b.y) end
end
M.Distance = distance
function M.MoveStart()
  if onTaxi() or inCombat() then mv = nil return end
  mv = { t = now(), p = position(), m = mounted() }
end
function M.MoveStop()
  local s = mv
  mv = nil
  if not s or onTaxi() or s.m ~= mounted() then return end
  local c = C()
  if not (c and learning()) then return end
  local dt = now() - s.t
  local d = distance(s.p, position())
  if not d or dt < 3 or d < 15 or d / dt > 40 then return end
  local e = c.move[s.m and "m" or "w"]
  e.d, e.s = floor(e.d + d + 0.5), r1(e.s + dt)
  if e.s > 3600 then e.d, e.s = floor(e.d * 3600 / e.s + 0.5), 3600 end   -- 1 h glissante
end

-- bateau / zeppelin : changement de continent (0 <-> 1) sans vol, sans mort
local lastSub
local function continent()
  local _, _, _, inst = try(g("UnitPosition"), "player")
  return num(inst)
end
function M.OnSubZone()
  lastSub = { t = now(), z = str(try(g("GetRealZoneText"))) or str(try(g("GetZoneText"))), c = continent() }
end
function M.OnNewArea()
  local prev = lastSub
  M.OnSubZone()
  local c = C()
  if not (prev and c and learning() and prev.c and lastSub.c) then return end
  if prev.c == lastSub.c or prev.c > 1 or lastSub.c > 1 then return end
  if onTaxi() or try(g("UnitIsDeadOrGhost"), "player") then return end
  local dt = lastSub.t - prev.t
  if dt < 30 or dt > 900 then return end
  local key = T.Cut(prev.z or "?", 30, "") .. ">" .. T.Cut(lastSub.z or "?", 30, "")
  acc(c.trans, key, dt, 10)
  local n, low, lowK = 0, nil, nil
  for k, v in pairs(c.trans) do n = n + 1 if not low or (v.n or 0) < low then low, lowK = v.n or 0, k end end
  if n > CAP_TRANS and lowK ~= key then c.trans[lowK] = nil end
end

-- réparations (coût réellement payé chez le marchand)
local repCost
function M.MerchantShow() repCost = num(try(g("GetRepairAllCost"))) end
function M.MerchantClosed() repCost = nil end
function M.OnDurability()
  if not repCost then return end
  local cur = num(try(g("GetRepairAllCost")))
  local c = C()
  if cur and cur < repCost and c and learning() then c.rep = c.rep + (repCost - cur) end
  if cur then repCost = cur end
end

-- ------------------------------------------------------------------ taux de drop (compte) — LA source juste
-- Occasions : 1 par corps que TU as fouillé (LOOT_OPENED, GUID source → id du monstre) d'un monstre qui peut
-- lâcher l'objet ; un objet de quête ne compte que tant que l'objectif n'est pas fini (il ne tombe plus après).
-- Pas les corps fouillés par d'autres (on ne les voit pas), ni le dépeçage / la pêche, ni deux fois le même corps.
-- En groupe, un objet de quête est tiré pour chaque membre qui en a besoin : tes corps = tes occasions.
-- Réussite = l'objet est dans le butin de CE corps (case de butin → id d'objet ; la place dans les sacs ne change
-- pas le taux). Estimation = loi Beta a posteriori : a priori QuestieDB (si sa base donne un taux, poids 8 corps)
-- sinon faible Beta(1, 3) ≈ 25 % ; moyenne + intervalle à 80 % (quantiles 10 % / 90 %).
-- Stockage : TreeAIDB.stats.drops[npc] = { n = corps, nm = nom, t = time(), i = { [item] = {o, k, q} },
--   p = { [nom d'objet] = corps en attente } } ; TreeAIDB.stats.names[nom d'objet] = item.
local abs, exp, log = math.abs, math.exp, math.log
local LANCZOS = { 0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
  -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7 }
local function lgamma(z)
  if z < 0.5 then return log(math.pi / abs(math.sin(math.pi * z))) - lgamma(1 - z) end
  z = z - 1
  local x = LANCZOS[1]
  for i = 1, 8 do x = x + LANCZOS[i + 1] / (z + i) end
  local t = z + 7.5
  return 0.5 * log(2 * math.pi) + (z + 0.5) * log(t) - t + log(x)
end
local function betacf(a, b, x)
  local FPMIN = 1e-30
  local qab, qap, qam = a + b, a + 1, a - 1
  local c, d = 1, 1 - qab * x / qap
  if abs(d) < FPMIN then d = FPMIN end
  d = 1 / d
  local h = d
  for m = 1, 300 do
    local m2 = 2 * m
    local aa = m * (b - m) * x / ((qam + m2) * (a + m2))
    d = 1 + aa * d; if abs(d) < FPMIN then d = FPMIN end
    c = 1 + aa / c; if abs(c) < FPMIN then c = FPMIN end
    d = 1 / d; h = h * d * c
    aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
    d = 1 + aa * d; if abs(d) < FPMIN then d = FPMIN end
    c = 1 + aa / c; if abs(c) < FPMIN then c = FPMIN end
    d = 1 / d
    local del = d * c
    h = h * del
    if abs(del - 1) < 1e-12 then break end
  end
  return h
end
--- fonction de répartition de Beta(a, b) en x (bêta incomplète régularisée)
function M.BetaCDF(x, a, b)
  if x <= 0 then return 0 elseif x >= 1 then return 1 end
  local bt = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1 - x))
  if x < (a + 1) / (a + b + 2) then return bt * betacf(a, b, x) / a end
  return 1 - bt * betacf(b, a, 1 - x) / b
end
--- quantile p de Beta(a, b) (dichotomie, précision ~1e-12)
function M.BetaQuantile(p, a, b)
  local lo, hi = 0, 1
  for _ = 1, 40 do
    local mid = (lo + hi) / 2
    if M.BetaCDF(mid, a, b) < p then lo = mid else hi = mid end
  end
  return (lo + hi) / 2
end
M.PRIOR_WEAK = { 1, 3 }      -- ≈ 25 %, pèse 4 corps
M.PRIOR_QUESTIE_N = 8        -- un taux QuestieDB pèse 8 corps
--- a posteriori : k réussites sur o occasions, a priori Beta(a0, b0) -> moyenne, bas 80 %, haut 80 %, a, b
function M.DropPosterior(k, o, a0, b0)
  local a, b = (a0 or 1) + (k or 0), (b0 or 3) + max(0, (o or 0) - (k or 0))
  return a / (a + b), M.BetaQuantile(0.1, a, b), M.BetaQuantile(0.9, a, b), a, b
end
--- monstres à tuer pour `remaining` objets : attendu (moyenne), pessimiste (borne basse à 80 %)
function M.KillsFor(remaining, mean_, lo)
  remaining = tonumber(remaining) or 0
  if remaining <= 0 or not mean_ or mean_ <= 0 then return nil end
  local e = math.ceil(remaining / max(0.01, mean_) - 1e-9)
  return e, math.ceil(remaining / max(0.01, lo or mean_) - 1e-9)
end

-- taux QuestieDB (si sa base en donne un) : nombre 0-1 ou 0-100, texte « 35 % », ou table de ces valeurs
local priorCache = {}
local function parseRate(v)
  if type(v) == "table" then for _, x in pairs(v) do local r = parseRate(x) if r then return r end end return nil end
  local n = tonumber(v) or (type(v) == "string" and tonumber(v:match("([%d%.]+)")))
  if not n or n <= 0 then return nil end
  if n > 1 then n = n / 100 end
  return (n > 0 and n < 1) and n or nil
end
M.ParseRate = parseRate
local function questiePrior(item, npc)
  if not (item and npc) then return nil end
  local key = item .. ":" .. npc
  local c = priorCache[key]
  if c == nil then
    c = false
    local X = T.Export
    local src = X and X.Source and try(X.Source, X)
    if type(src) == "table" and type(src.drop) == "function" then c = parseRate(try(src.drop, item, npc)) or false end
    priorCache[key] = c
  end
  return c or nil
end
M.QuestiePrior = questiePrior
local function priorFor(item, npc)
  local p = questiePrior(item, npc)
  if p then return p * M.PRIOR_QUESTIE_N, (1 - p) * M.PRIOR_QUESTIE_N, "questie" end
  return M.PRIOR_WEAK[1], M.PRIOR_WEAK[2], nil
end

local lootedGuid, lootedN = {}, 0
local function npcFromGUID(guid)
  if type(guid) ~= "string" or secret(guid) then return nil end
  local kind, id = guid:match("^(%a+)%-%d+%-%d+%-%d+%-%d+%-(%d+)")
  if kind ~= "Creature" and kind ~= "Vehicle" then return nil end
  return tonumber(id)
end
M.NpcFromGUID = npcFromGUID
local function low(s) return type(s) == "string" and s:lower() or nil end
-- objet encore utile ? objet hors quête : toujours ; objet de quête : objectif pas fini (module Quests)
local function needed(name)
  local Q = T.modules and T.modules.Quests
  if not (Q and Q.ObjectiveFor) then return true end
  if not name then return false end
  return try(Q.ObjectiveFor, name, false) ~= nil
end
M.Needed = needed
local function itemNameOf(A, item)
  local n = str(try(g("GetItemInfo"), item))
  if n then return n end
  for nm, id in pairs(A.names) do if id == item then return nm end end
end
local function corpseName(guid)
  if try(g("UnitGUID"), "target") == guid then return str(try(g("UnitName"), "target")) end
  return nil
end
-- item -> carte Questie des monstres qui le lâchent (module Export), sinon nil
local function questieNpcs(item)
  local X = T.Export
  local tr = X and X.data and X.data._itemNpcs
  return type(tr) == "table" and type(tr[item]) == "table" and tr[item] or nil
end

--- loot = { {item=, name=, quest=bool, cls=classID, src={guid,...}}, ... } , money = bool (fonction testable)
function M.RecordCorpses(loot, hasMoney)
  local A = acct()
  if not (A and learning()) then return 0 end
  -- dépeçage / minage d'un corps : que des marchandises (classe 7), pas d'argent, rien de quête -> ignoré
  local gather = not hasMoney and #loot > 0
  for _, it in ipairs(loot) do if it.quest or tonumber(it.cls) ~= 7 then gather = false end end
  if gather or try(g("IsFishingLoot")) then return 0 end
  local corpses, got = {}, {}
  for _, it in ipairs(loot) do
    for _, guid in ipairs(it.src or {}) do
      local npc = npcFromGUID(guid)
      if npc and not lootedGuid[guid] then
        corpses[guid] = npc
        if it.item then got[guid] = got[guid] or {}; got[guid][it.item] = it end
      end
    end
  end
  local drops, nCorpses = A.drops, 0
  for guid, npc in pairs(corpses) do
    nCorpses = nCorpses + 1
    lootedGuid[guid], lootedN = true, lootedN + 1
    local e = drops[npc]
    if type(e) ~= "table" then e = { n = 0, i = {} }; drops[npc] = e end
    e.i = type(e.i) == "table" and e.i or {}
    e.n, e.t = e.n + 1, epoch()
    e.nm = corpseName(guid) or e.nm
    local here = got[guid] or {}
    -- objets déjà connus sur ce monstre, absents de ce corps : une occasion ratée s'ils étaient encore utiles
    for item, r in pairs(e.i) do
      if not here[item] and (not r[3] or needed(itemNameOf(A, item))) then r[1] = r[1] + 1 end
    end
    -- objets de ce corps : réussite (+ occasion) ; nouveau sur ce monstre = les corps d'avant comptent aussi
    for item, it in pairs(here) do
      local nm = low(it.name)
      if nm then A.names[nm] = item end
      local r = e.i[item]
      if not r then
        local before = it.quest and (e.p and nm and e.p[nm] or 0) or (e.n - 1)
        r = { before, 0, it.quest and 1 or nil }
        e.i[item] = r
      end
      r[1], r[2] = r[1] + 1, r[2] + 1
      if it.quest then r[3] = 1 end
      if e.p and nm then e.p[nm] = nil end
    end
    -- objets de quête encore utiles, jamais vus sur ce monstre : corps en attente (comptés s'il finit par le lâcher)
    local Q = T.modules and T.modules.Quests
    if Q and type(Q.lastList) == "table" then
      local want = {}
      for _, q in ipairs(Q.lastList) do
        for _, o in ipairs(type(q) == "table" and not q.complete and q.obj or {}) do
          local nm = not o.finished and o.t == "i" and low(o.name)
          if nm then
            local id = A.names[nm]
            local map = id and questieNpcs(id)
            if not (id and e.i[id]) and not (map and not map[npc]) then want[nm] = true end
          end
        end
      end
      local p = {}
      for nm in pairs(want) do if not here[A.names[nm] or -1] then p[nm] = ((e.p and e.p[nm]) or 0) + 1 end end
      e.p = next(p) and p or nil
    end
    local cnt, lowK, lowV = 0, nil, nil
    for it, r in pairs(e.i) do cnt = cnt + 1 if not lowV or r[2] < lowV then lowK, lowV = it, r[2] end end
    if cnt > CAP_DROP_ITEM then e.i[lowK] = nil end
  end
  if lootedN > 300 then lootedGuid, lootedN = {}, 0 end
  local cnt, oldT, oldK = 0, nil, nil
  for k, e in pairs(drops) do
    cnt = cnt + 1
    local score = (tonumber(e.t) or 0) + (tonumber(e.n) or 0) * 3600   -- vieux et peu vus partent d'abord
    if not oldT or score < oldT then oldT, oldK = score, k end
  end
  if cnt > CAP_DROP_NPC and oldK then drops[oldK] = nil end
  local nn = 0
  for _ in pairs(A.names) do nn = nn + 1 end
  if nn > 200 then A.names = {} end
  return nCorpses
end
local function sources(srcInfo, slot)
  local raw, out = { try(srcInfo, slot) }, {}
  for i = 1, #raw, 2 do out[#out + 1] = raw[i] end
  return out
end
function M.OnLoot()
  if not learning() then return end
  local srcInfo = g("GetLootSourceInfo")
  local n = num(try(g("GetNumLootItems"))) or 0
  if not srcInfo or n <= 0 then return end
  local loot, money = {}, false
  for slot = 1, min(n, 20) do
    local _, name, _, _, _, _, isQuest = try(g("GetLootSlotInfo"), slot)
    local link = try(g("GetLootSlotLink"), slot)
    local item = type(link) == "string" and not secret(link) and tonumber(link:match("item:(%d+)"))
    if item then
      local cls = select(12, try(g("GetItemInfo"), item))
      loot[#loot + 1] = { item = item, name = str(name), quest = (isQuest and true) or tonumber(cls) == 12,
        cls = num(cls), src = sources(srcInfo, slot) }
    elseif name ~= nil and not secret(name) then
      money = true   -- argent : le corps compte quand même
      loot[#loot + 1] = { src = sources(srcInfo, slot) }
    end
  end
  M.RecordCorpses(loot, money)
end

-- hôtel des ventes : appelés par le module Auction (mise en vente, courrier de vente / d'expiration)
local function ahEntry(name, create)
  local c = C()
  name = str(name)
  if not (c and name) then return nil end
  name = T.Cut(name, 40, "")
  local e = c.ah[name]
  if type(e) ~= "table" and create then
    local n, oldK, oldT = 0, nil, nil
    for k, v in pairs(c.ah) do
      n = n + 1
      local last = type(v) == "table" and (tonumber(v.l) or 0) or 0
      if not oldT or last < oldT then oldK, oldT = k, last end
    end
    if n >= CAP_AH and oldK then c.ah[oldK] = nil end
    e = { p = 0, s = 0, x = 0 }
    c.ah[name] = e
  end
  return e
end
function M.NotePost(name)
  if not learning() then return end
  local e = ahEntry(name, true)
  if not e then return end
  e.p, e.l = e.p + 1, epoch()
  e.o = type(e.o) == "table" and e.o or {}
  e.o[#e.o + 1] = epoch()
  while #e.o > 5 do tremove(e.o, 1) end
end
--- kind = "sell" | "expired" | "cancelled" ; at = heure d'arrivée estimée du courrier (time())
function M.NoteAuction(kind, name, at)
  if not learning() or (kind ~= "sell" and kind ~= "expired") then return end
  local e = ahEntry(name, true)
  if not e then return end
  e.l = epoch()
  local o = type(e.o) == "table" and e.o or {}
  local posted = tremove(o, 1)
  if kind == "sell" then
    e.s = e.s + 1
    local d = posted and ((tonumber(at) or epoch()) - posted) or nil
    if d and d > 0 and d < 3 * 86400 then acc(e, "ts", d, 20) end
  else
    e.x = e.x + 1
  end
end

-- minuterie 60 s : temps joué, XP par activité, munitions, fenêtre glissante
local function activity()
  local inInst, kind = try(g("IsInInstance"))
  if inInst and (kind == "party" or kind == "raid") then return "d" end
  if now() - questMark < 300 then return "q" end
  if tick.kills > 0 then return "g" end
  return "t"
end
M.Activity = activity
function M.Tick()
  local t = now()
  local dt = lastTick and (t - lastTick) or 0
  lastTick = t
  local c = C()
  local xp, kills = tick.xp, tick.kills
  local kind = activity()
  tick.xp, tick.kills = 0, 0
  -- (FIDELITY F10) UnitIsAFK est secret pendant un verrou du chat (combat de boss) : un test booléen dessus = erreur
  local afk = try(g("UnitIsAFK"), "player")
  if secret(afk) then afk = nil end
  if not (c and learning()) or dt <= 0 or dt > 180 or afk then return end
  local a = c.act[kind]
  a.x, a.s = a.x + xp, r1(a.s + dt)
  c.played = r1(c.played + dt)
  if T.PlayerClass and T:PlayerClass() == "HUNTER" then
    local am = num(try(g("GetInventoryItemCount"), "player", 0))
    if am and lastAmmo and am < lastAmmo and lastAmmo - am < 2000 then c.use.am = c.use.am + (lastAmmo - am) end
    lastAmmo = am
  end
  local slot = floor(epoch() / WIN_SEC)
  local w = c.win[#c.win]
  if not (type(w) == "table" and w.b == slot) then
    w = { b = slot, x = 0, k = 0, s = 0, g = 0 }
    c.win[#c.win + 1] = w
    while #c.win > WIN_N do tremove(c.win, 1) end
  end
  w.x, w.k, w.s = w.x + xp, w.k + kills, w.s + floor(dt + 0.5)
  local m = lastMoney
  if m and w.m0 == nil then w.m0 = m end
  if m and w.m0 then w.g = m - w.m0 end
  if M.Refresh then M.Refresh() end
end

-- ------------------------------------------------------------------ c) API
--- temps de kill mesuré (s) pour un écart de niveau (et un type n/e/r/b) ; solo d'abord, puis tout mélangé,
--- puis l'apprentissage du module Export ; nil si pas assez de mesures. -> sec, nb de mesures
function M:KillTime(levelDiff, cls)
  local c = C()
  local d = max(-5, min(5, floor(tonumber(levelDiff) or 0)))
  cls = cls or "n"
  if c then
    local e = c.kill[M.KillKey(d, cls, false)]
    local v = mean(e)
    if v then return v, e.n end
    local s, n = 0, 0
    for _, grp in ipairs({ false, true }) do
      local x = c.kill[M.KillKey(d, cls, grp)]
      if type(x) == "table" then s, n = s + (x.s or 0) * (grp and 1.25 or 1), n + (x.n or 0) end
    end
    if n >= 3 then return s / n, n end
    -- écart voisin (±1) corrigé de 12 % par niveau
    for _, dd in ipairs({ d - 1, d + 1 }) do
      local v2 = mean(c.kill[M.KillKey(dd, cls, false)], 5)
      if v2 then return v2 * (1 + 0.12 * (d - dd)), c.kill[M.KillKey(dd, cls, false)].n end
    end
  end
  local X = T.Export
  if cls == "n" and X and X.TTK then local v = try(X.TTK, X, d) if v then return v, 3 end end
  return nil
end

local KIND = { quest = "q", questing = "q", quests = "q", q = "q", grind = "g", grinding = "g", g = "g",
  dungeon = "d", d = "d", travel = "t", t = "t" }
--- XP/h mesurée par activité ("questing" | "grinding" | "dungeon" | nil = tout), après ≥ 10 min de mesure
function M:XPPerHour(kind)
  local c = C()
  if not c then return nil end
  local k = KIND[kind or ""]
  local x, s = 0, 0
  for key, a in pairs(c.act) do
    if (not k and key ~= "t") or key == k then x, s = x + (a.x or 0), s + (a.s or 0) end
  end
  if s < 600 then return nil end
  return x * 3600 / s, s
end
--- XP/h sur la fenêtre glissante (≈ 1 h) -> XP/h, secondes réellement mesurées ; nil sous 10 min de mesure
function M:RecentXPPerHour()
  local c = C()
  if not c then return nil end
  local x, s = 0, 0
  for _, w in ipairs(c.win) do x, s = x + (w.x or 0), s + (w.s or 0) end
  if s < 600 then return nil end
  return x * 3600 / s, s
end
--- monstres tués / h sur la fenêtre glissante ; nil sous 10 min de mesure
function M:RecentKillsPerHour()
  local c = C()
  if not c then return nil end
  local k, s = 0, 0
  for _, w in ipairs(c.win) do k, s = k + (w.k or 0), s + (w.s or 0) end
  if s < 600 then return nil end
  return k * 3600 / s, s
end
--- secondes de jeu mesurées par activité (tout sauf trajet) : la taille de l'échantillon des XP/h
function M:PlayedSeconds(kind)
  local c = C()
  if not c then return 0 end
  local s = 0
  for key, a in pairs(c.act) do if (not kind and key ~= "t") or key == kind then s = s + (a.s or 0) end end
  return s
end
--- vitesse de déplacement réelle (m/s, à vol d'oiseau) à pied / monté ; nil si < 1 min de mesure
function M:TravelSpeed(isMounted)
  local c = C()
  local e = c and c.move[isMounted and "m" or "w"]
  if not (e and e.s >= 60 and e.d > 0) then return nil end
  return e.d / e.s
end
--- taux de drop d'un objet (sur un monstre, ou regroupé sur tous les monstres qui l'ont lâché)
--- -> moyenne, occasions, bas 80 %, haut 80 %, a priori ("questie" | nil). nil si ni mesure ni QuestieDB.
function M:Drop(itemID, mobID)
  itemID = tonumber(itemID)
  local A = acct()
  if A and itemID then
    local k, o = 0, 0
    mobID = tonumber(mobID)
    if mobID then
      local e = A.drops[mobID]
      local r = type(e) == "table" and e.i and e.i[itemID]
      if r then k, o = r[2] or 0, r[1] or 0 end
    else
      for _, e in pairs(A.drops) do
        local r = type(e) == "table" and e.i and e.i[itemID]
        if r then k, o = k + (r[2] or 0), o + (r[1] or 0) end
      end
    end
    local a0, b0, prior = priorFor(itemID, mobID)
    if o > 0 or prior then
      local m, lo, hi = M.DropPosterior(k, o, a0, b0)
      return m, o, lo, hi, prior
    end
  end
  return nil
end
--- monstres à tuer : attendu, pessimiste (« encore ~8, jusqu'à 12 si pas de chance »), taux, occasions
function M:KillsNeeded(remaining, itemID, mobID)
  local m, o, lo = M:Drop(itemID, mobID)
  local e, p = M.KillsFor(remaining, m, lo)
  return e, p, m, o
end
--- par noms (modules qui ne connaissent que « Loup des bois » / « Patte de loup ») : moyenne, occasions, bas, haut
function M:DropByName(mobName, itemName)
  local A = acct()
  local item = A and low(itemName) and A.names[low(itemName)]
  if not item then return nil end
  local mob = low(mobName)
  if not mob then return M:Drop(item) end
  for npc, e in pairs(A.drops) do
    if type(e) == "table" and low(e.nm) == mob and e.i and e.i[item] then return M:Drop(item, npc) end
  end
  return nil
end
--- meilleur monstre mesuré pour un objet : nom, moyenne, occasions, bas
function M:BestSourceByName(itemName)
  local A = acct()
  local item = A and low(itemName) and A.names[low(itemName)]
  if not item then return nil end
  local best, bm, bo, blo
  for npc, e in pairs(A.drops) do
    if type(e) == "table" and e.nm and e.i and e.i[item] then
      local m, o, lo = M:Drop(item, npc)
      if m and (not bm or m > bm) then best, bm, bo, blo = e.nm, m, o, lo end
    end
  end
  return best, bm, bo, blo
end
--- repos (manger / boire) : secondes par combat, part du temps (repos / (combat + repos)) ; nil si < 5 combats
function M:Downtime()
  local c = C()
  if not c or (c.fight.n or 0) < 5 then return nil end
  local down, fs = c.down.s or 0, c.fight.s or 0
  return down / c.fight.n, (down + fs) > 0 and down / (down + fs) or 0
end
function M:Sheet()
  if not M.sheet then M.sheet = M.ReadSheet() end
  return M.sheet
end
function M:GoldPerHour()
  local c = C()
  if not c or c.played < 600 then return nil end
  return (c.gold.i - c.gold.o) * 3600 / c.played
end
--- "ammo" | "food" | "water" | "repair" (cuivre) par heure jouée
function M:PerHour(what)
  local c = C()
  if not c or c.played < 600 then return nil end
  local v = ({ ammo = c.use.am, food = c.use.fd, water = c.use.dr, repair = c.rep })[what or ""]
  return v and v * 3600 / c.played or nil
end
function M:DeathsPerHour()
  local c = C()
  if not c or c.played < 1800 then return nil end
  return c.death.n * 3600 / c.played
end
--- durée moyenne d'une quête en minutes : totale, prise → finie, finie → rendue
function M:QuestMinutes()
  local c = C()
  if not c then return nil end
  local q = c.quest
  local tot = mean(q.tot, 1)
  if not tot then return nil end
  local a, b = mean(q.a2c, 1), mean(q.c2t, 1)
  return tot / 60, a and a / 60, b and b / 60
end
--- taux de vente HV d'un objet (ou global) -> taux, heures pour vendre, nb de résultats
function M:SaleRate(name)
  local c = C()
  if not c then return nil end
  local s, x, ts, tn = 0, 0, 0, 0
  for k, e in pairs(c.ah) do
    if type(e) == "table" and (not name or k == name) then
      s, x = s + (e.s or 0), x + (e.x or 0)
      if type(e.ts) == "table" then ts, tn = ts + (e.ts.s or 0), tn + (e.ts.n or 0) end
    end
  end
  if s + x < (name and 2 or 3) then return nil end
  return s / (s + x), tn > 0 and ts / tn / 3600 or nil, s + x
end
function M:Respawn(npc)
  local X = T.Export
  local L = X and X.LearnDB and try(X.LearnDB)
  return L and L.respawn and mean(L.respawn[tonumber(npc) or -1]) or nil
end
function M:FlightTime(from, to)
  local X = T.Export
  local L = X and X.LearnDB and try(X.LearnDB)
  return L and L.flights and mean(L.flights[tostring(from) .. "-" .. tostring(to)], 1) or nil
end
function M:FightDamagePct() local c = C() return c and mean(c.fight.dm, 5) or nil end
function M:TopDeathCause()
  local c = C()
  local best, bn
  for k, v in pairs(c and c.death.c or {}) do if not bn or v > bn then best, bn = k, v end end
  return best, bn
end

-- instantané compact (export pour l'appli et Stats:Get)
local function rnd(v, k) if v == nil then return nil end k = k or 1 return floor(v * k + 0.5) / k end
function M:Snapshot()
  local c = C()
  if not c then return { v = 1 } end
  local s = { v = 1, t = epoch(), played = floor(c.played / 60), sheet = M:Sheet() }
  local kill = {}
  for d = -3, 5 do local v = M:KillTime(d) if v then kill[tostring(d)] = rnd(v, 10) end end
  s.kill = next(kill) and kill or nil
  local ke = M:KillTime(0, "e")
  s.killElite = ke and rnd(ke, 10) or nil
  s.fights = c.fight.n
  s.dmgPct = rnd(M:FightDamagePct())
  local dps, share = M:Downtime()
  s.downSec, s.downShare = rnd(dps, 10), rnd(share, 100)
  s.deaths, s.deathsH = c.death.n, rnd(M:DeathsPerHour(), 100)
  s.deathHigh, s.deathElite = c.death.hi, c.death.el
  local top = M:TopDeathCause()
  s.deathTop = top
  local rx, rs = M:RecentXPPerHour()
  s.xph = { q = rnd(M:XPPerHour("q")), g = rnd(M:XPPerHour("g")), d = rnd(M:XPPerHour("d")), all = rnd(M:XPPerHour()),
    recent = rnd(rx), recentMin = rs and floor(rs / 60) or nil, min = floor(M:PlayedSeconds() / 60) }
  s.goldH = rnd(M:GoldPerHour())
  local qt, qa, qc = M:QuestMinutes()
  s.questMin, s.questA2C, s.questC2T, s.quests = rnd(qt, 10), rnd(qa, 10), rnd(qc, 10), c.quest.n
  s.speed = { w = rnd(M:TravelSpeed(false), 10), m = rnd(M:TravelSpeed(true), 10) }
  local tr = {}
  for k, e in pairs(c.trans) do local v = mean(e, 1) if v then tr[#tr + 1] = { k, floor(v + 0.5) } end end
  s.trans = #tr > 0 and tr or nil
  s.perH = { ammo = rnd(M:PerHour("ammo")), food = rnd(M:PerHour("food"), 10), water = rnd(M:PerHour("water"), 10),
    repair = rnd(M:PerHour("repair")) }
  local rate, hrs, nAH = M:SaleRate()
  s.ah = rate and { rate = rnd(rate, 100), hours = rnd(hrs, 10), n = nAH } or nil
  local A = acct()
  local nd = 0
  for _ in pairs(A and A.drops or {}) do nd = nd + 1 end
  s.dropMobs = nd
  return s
end
--- Stats:Get("xph.q"), Get("speed.w"), Get("sheet.agi"), Get("goldH")… (chemin à points dans l'instantané)
function M:Get(key)
  if type(key) ~= "string" then return nil end
  local now_ = now()
  if not M._snap or now_ - (M._snapAt or 0) > 10 then M._snap, M._snapAt = M:Snapshot(), now_ end
  local v = M._snap
  for part in key:gmatch("[^%.]+") do
    if type(v) ~= "table" then return nil end
    v = v[part] ~= nil and v[part] or v[tonumber(part) or part]
  end
  return v
end

-- ------------------------------------------------------------------ d) onglet « Stats » + export
local Fmt = T.Fmt or {}
local function money(c) return Fmt.Money and Fmt.Money(c) or (floor((c or 0) / 10000) .. " po") end
local function kxp(v)
  if v >= 10000 then return floor(v / 1000 + 0.5) .. "k" end
  return (tostring(floor(v / 100 + 0.5) / 10):gsub("%.", ",")) .. "k"   -- « 8,5k » (virgule française)
end
local function dec(v) return (tostring(floor(v * 10 + 0.5) / 10):gsub("%.", ",")) end
local function dur(sec) return Fmt.Time and Fmt.Time(sec) or (floor((sec or 0) / 60 + 0.5) .. " min") end
local function levelInfo()
  local xp, mx = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  local lv = num(try(g("UnitLevel"), "player"))
  if not (xp and mx and lv) or mx <= 0 or lv >= 60 then return nil end
  return { left = mx - xp, next = lv + 1 }
end

--- lignes de l'onglet (pure sur un instantané) : jamais un chiffre qu'on n'a pas ; chaque taux dit sur combien
--- de temps il est mesuré (≥ 10 min, sinon rien)
function M.Lines(s, lvl)
  local out = {}
  local function add(t) if t and t ~= "" then out[#out + 1] = t end end
  local x = s.xph or {}
  -- niveau suivant à ton rythme de la dernière heure (XP/h mesurée sur au moins 10 min)
  if x.recent and x.recent > 0 and lvl and lvl.left and lvl.left > 0 then
    add(("Niveau %d dans ≈ %s à ton rythme (%s XP/h, mesuré sur %s)"):format(lvl.next, dur(lvl.left / x.recent * 3600),
      kxp(x.recent), dur((x.recentMin or 10) * 60)))
  end
  local p = {}
  if x.q then p[#p + 1] = "quêtes " .. kxp(x.q) end
  if x.g then p[#p + 1] = "farm " .. kxp(x.g) end
  if x.d then p[#p + 1] = "donjon " .. kxp(x.d) end
  if #p > 0 then add(("XP/h par activité (sur %s de jeu) : %s"):format(dur((x.min or 10) * 60), tconcat(p, " · "))) end
  if s.goldH then add("Or : " .. (s.goldH < 0 and "-" or "") .. money(math.abs(s.goldH)) .. " par heure (net, sur "
    .. dur((s.played or 10) * 60) .. ")") end
  local sh = s.sheet or {}   -- (4.0) la fiche For / Agi / End… est déjà celle du jeu : gardée pour l'appli, pas redite ici
  p = {}
  local ranged = (sh.rap or 0) > (sh.ap or 0)
  if ranged and sh.rap then p[#p + 1] = "PA dist. " .. sh.rap elseif sh.ap then p[#p + 1] = "PA " .. sh.ap end
  local crit = ranged and sh.rcrit or sh.crit
  if (sh.scrit or 0) > (crit or 0) and (sh.int or 0) > (sh.agi or 0) then crit = sh.scrit end
  if crit then p[#p + 1] = "crit " .. dec(crit) .. " %" end
  if sh.armor then p[#p + 1] = "armure " .. sh.armor end
  if sh.dodge and sh.dodge > 0 then p[#p + 1] = "esquive " .. dec(sh.dodge) .. " %" end
  if sh.mp5 and sh.mp5 > 0 then p[#p + 1] = "mana " .. floor(sh.mp5 + 0.5) .. "/5 s" end
  if #p > 0 then add("Fiche : " .. tconcat(p, " · ")) end
  p = {}
  local k = s.kill or {}
  if k["0"] then p[#p + 1] = "kill ~" .. floor(k["0"] + 0.5) .. " s (même niveau)" end
  if k["2"] then p[#p + 1] = "~" .. floor(k["2"] + 0.5) .. " s (+2)" end
  if s.killElite then p[#p + 1] = "élite ~" .. floor(s.killElite + 0.5) .. " s" end
  if s.dmgPct then p[#p + 1] = "vie perdue " .. s.dmgPct .. " % / combat" end
  if #p > 0 then add("Combat : " .. tconcat(p, " · ")) end
  p = {}
  if s.downSec then p[#p + 1] = "repos " .. floor(s.downSec + 0.5) .. " s / combat" end
  if s.deathsH then p[#p + 1] = "morts " .. dec(s.deathsH) .. "/h" end
  if s.deathTop then p[#p + 1] = "tué surtout par " .. T.Cut(s.deathTop, 24, "") end
  if #p > 0 then add("Repos : " .. tconcat(p, " · ")) end
  p = {}
  local sp = s.speed or {}
  if sp.w then p[#p + 1] = dec(sp.w) .. " m/s à pied" end
  if sp.m then p[#p + 1] = dec(sp.m) .. " monté" end
  if s.questMin then p[#p + 1] = "quête ~" .. floor(s.questMin + 0.5) .. " min" end
  if #p > 0 then add("Trajets : " .. tconcat(p, " · ")) end
  p = {}
  local ph = s.perH or {}
  if ph.ammo and ph.ammo > 0 then p[#p + 1] = floor(ph.ammo + 0.5) .. " munitions" end
  if ph.food and ph.food > 0 then p[#p + 1] = dec(ph.food) .. " nourriture" end
  if ph.water and ph.water > 0 then p[#p + 1] = dec(ph.water) .. " boisson" end
  if ph.repair and ph.repair > 0 then p[#p + 1] = "réparations " .. money(ph.repair) end
  if s.ah then p[#p + 1] = "HV " .. floor(s.ah.rate * 100 + 0.5) .. " % vendu" end
  if #p > 0 then add("Par heure : " .. tconcat(p, " · ")) end
  return out
end

function M.WriteSummary()
  local sm = T.Summary and try(T.Summary, T)
  if type(sm) ~= "table" then return nil end
  local s = M:Snapshot()
  sm.stats = s
  return s
end
function M.ExportNow()
  M.WriteSummary()
  M:Suggest("export", "Fais /reload : tes stats sont prêtes pour l'appli TreeAI", 1, { ttl = 30, tab = TAB, sound = false })
end

local lastSig, lastRefresh = nil, -1e9
function M.Refresh(force)
  if not (T.SetTabLines and M.db) then return end
  if M.db.tab == false then
    if lastSig ~= "" and T.tabs and T.tabs[TAB] then pcall(T.SetTabLines, T, TAB, {}, "Stats") end
    lastSig = ""
    return
  end
  local t = now()
  if not force and t - lastRefresh < 10 then return end
  if not force and lastSig ~= nil and T.GetActiveTab and T:GetActiveTab() ~= TAB and t - lastRefresh < 300 then return end
  lastRefresh = t
  M._snap, M._snapAt = M:Snapshot(), t
  local texts = M.Lines(M._snap, levelInfo())
  local sig = tconcat(texts, "\n")
  if sig == lastSig then return end
  lastSig = sig
  -- (CLEAN) onglet caché tant qu'il n'y a rien de mesuré : il apparaît dans « Plus » avec la 1re mesure
  if #texts == 0 then
    if T.tabs and T.tabs[TAB] then pcall(T.SetTabLines, T, TAB, {}, "Stats") end
    return
  end
  if not M.hasData then
    M.hasData = true
    if T.RegisterTab then T:RegisterTab(TAB, "Stats", 30) end
  end
  local lines = {}
  for _, tx in ipairs(texts) do lines[#lines + 1] = { text = tx, prio = 1 } end
  lines[#lines + 1] = { text = "Exporter pour TreeAI", prio = 1, icon = "Interface\\Icons\\INV_Misc_Note_01",
    onClick = function() M.ExportNow() end,
    tooltip = { "Exporter pour TreeAI", "Prépare tes stats pour l'appli TreeAI Coach (onglet Session).",
      "Puis /reload ou déconnexion : WoW écrit le fichier." } }
  pcall(T.SetTabLines, T, TAB, lines, "Stats")
end

-- ------------------------------------------------------------------ cycle de vie
function M:OnEnable()
  if T.TAB_OWNER then T.TAB_OWNER[TAB] = "Stats" end
  lastMoney = num(try(g("GetMoney")))
  lastXP, lastXPMax = num(try(g("UnitXP"), "player")), num(try(g("UnitXPMax"), "player"))
  lastTick = now()
  M.OnSubZone()
  M:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", function() ilvlDirty = true schedule(true, true) end)
  M:RegisterUnitEvent("UNIT_STATS", "player", function() schedule(true, true) end)
  M:RegisterUnitEvent("UNIT_AURA", "player", function() if not inCombat() then schedule(true) maybeEat() end end)
  M:RegisterEvent("PLAYER_LEVEL_UP", function() schedule(true, true) end)
  M:RegisterEvent("PLAYER_REGEN_DISABLED", function() M.FightStart() end)
  M:RegisterEvent("PLAYER_REGEN_ENABLED", function() M.FightEnd() if sheetWanted then schedule(true, true) end end)
  M:RegisterEvent("PLAYER_TARGET_CHANGED", function()
    if not inCombat() then M.lastFoe = str(try(g("UnitName"), "target")) or M.lastFoe end
  end)
  M:RegisterUnitEvent("UNIT_HEALTH", "player", onHealth)
  M:RegisterUnitEvent("UNIT_MAXHEALTH", "player", onHealth)
  M.SnapHealth()
  -- un seul lecteur du message d'XP : Detect (« X meurt, vous gagnez… ») publie chaque mort
  M:On("DETECT", function(_, _, kind) if kind == "kill" then M.OnXPMsg() end end)
  M:RegisterEvent("PLAYER_XP_UPDATE", function() M.OnXP() end)
  M:RegisterEvent("PLAYER_MONEY", function() M.OnMoney() end)
  M:RegisterEvent("PLAYER_DEAD", function() M.OnDeath() end)
  M:RegisterEvent("QUEST_ACCEPTED", function(_, _, a, b) M.OnAccept(a, b) end)
  M:RegisterEvent("QUEST_WATCH_UPDATE", function(_, _, idx) M.OnQuestWatch(idx) end)
  M:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid) M.OnTurnIn(qid) end)
  M:RegisterEvent("LOOT_OPENED", function() M.OnLoot() end)
  M:RegisterEvent("PLAYER_STARTED_MOVING", function() M.MoveStart() end)
  M:RegisterEvent("PLAYER_STOPPED_MOVING", function() M.MoveStop() end)
  M:RegisterEvent("ZONE_CHANGED", function() M.OnSubZone() end)
  M:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() M.OnNewArea() end)
  M:RegisterEvent("PLAYER_ENTERING_WORLD", function(_, _, login, reload) if login or reload then M.OnSubZone() end end)
  M:RegisterEvent("MERCHANT_SHOW", function() M.MerchantShow() end)
  M:RegisterEvent("MERCHANT_CLOSED", function() M.MerchantClosed() end)
  M:RegisterEvent("UPDATE_INVENTORY_DURABILITY", function() M.OnDurability() end)
  M:RegisterEvent("PLAYER_LOGOUT", function() pcall(M.WriteSummary) end)
  M:On("SUMMARY", function() pcall(M.WriteSummary) end)
  if not M.ticker and T.NewTicker then M.ticker = T:NewTicker(60, function() pcall(M.Tick) end) end
  if T.RegisterSlash then
    T:RegisterSlash("stats", function()
      M.Refresh(true)
      if T.SelectTab then pcall(T.SelectTab, T, TAB) end
      if T.Show then pcall(T.Show, T) end
    end, "stats : tes stats mesurées (kills, XP/h, vitesse, drops…) + export pour TreeAI")
  end
  schedule(true)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker, fight = nil, nil
  if T.SetTabLines and T.tabs and T.tabs[TAB] then pcall(T.SetTabLines, T, TAB, {}, "Stats") end
  lastSig = nil
end
function M:OnOptionChanged() lastSig = nil M.Refresh(true) end

-- (CLEAN 4.0) l'ancien module « Prévisions » est retiré : ses lecteurs (Tracker, Todo…) trouvent ici les mêmes
-- taux, MESURÉS par Stats (rien sous 10 min de jeu ou 3 mesures) ; plus aucune prévision tirée d'une table.
T.Predict = T.Predict or {
  DropRate = function(mob, item) local m, o = M:DropByName(mob, item) return m, o end,
  BestSource = function(item) return M:BestSourceByName(item) end,
  Rates = function()
    return { killsPerHour = (M:RecentKillsPerHour()), xpPerHour = (M:RecentXPPerHour()), goldPerHour = M:GoldPerHour() }
  end,
}
-- « Courses » (lues par la liste À faire) : les lignes que leurs propriétaires affichent VRAIMENT en ce moment
-- (Inventaire : réparation, sacs, nourriture ; Chasseur : munitions ; Guichets : maître de classe) — aucun calcul ici.
local ERRAND_OF = { ["Inventory:inv_repair"] = "repair", ["Hunter:ammo"] = "ammo", ["Inventory:inv_bags"] = "bags",
  ["Inventory:inv_food"] = "food", ["Errands:trainer"] = "train" }
if type(T.Predict) == "table" and getmetatable(T.Predict) == nil then
  setmetatable(T.Predict, { __index = function(_, k)
    if k ~= "list" then return nil end
    local out, S = {}, T._suggestions or {}
    for id, kind in pairs(ERRAND_OF) do
      local e = S[id]
      if type(e) == "table" and type(e.text) == "string" then
        if kind == "food" and e.text:find("boisson") then kind = "water" end
        out[#out + 1] = { id = kind, text = e.text, prio = tonumber(e.prio) or 1 }
      end
    end
    return out
  end })
end
