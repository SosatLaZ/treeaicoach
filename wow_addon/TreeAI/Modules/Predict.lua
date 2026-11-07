-- TreeAI — module Prévisions (agent QUEST-PRO) : « prédit ce qui va me manquer ».
-- Mesure en direct (rien d'automatique, lecture d'API + journal de combat du client) :
--   munitions/min, nourriture & boisson par repos, monstres/heure, taux de butin par objet de quête et par
--   monstre (LOOT_OPENED), XP/h, or/h, perte de durabilité/h, places de sac prises par monstre.
-- → prévisions courtes : « Munitions : vides dans ~12 min, achète 1000 au prochain vendeur »,
--   « Sacs pleins dans ~25 monstres », « Il te faut ~7 Loup de plus pour 4 Croc (taux 55 %) »,
--   « Formation niv 28 : 3 po 20 — il te manque 1 po 40 », « Réparation bientôt », « Pierre de foyer prête
--   dans 6 min », objets à garder pour des quêtes à venir (Questie, lecture seule).
-- Onglet « Prévisions » (TreeAI:SetTabLines) si présent ; les urgentes vont dans « Maintenant » (Suggest).
-- COÛTS DE FORMATION : table APPROXIMATIVE (ordre de grandeur Classic, tous sorts du palier, toutes
-- classes confondues ; les 9 classes sont retravaillées dans Forever) → affichée « ≈ ».
-- Résumé IA : TreeAIDB.chars[perso].summary.predict. Lua 5.1, aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Predict", {
  title = "Prévisions",
  defaults = { enabled = true, ammo = true, food = true, bags = true, drops = true, money = true, repair = true,
               hearth = true, keep = true, mobs = {} },
  charDefaults = {},
  options = {
    { key = "ammo", type = "toggle", label = "Munitions : quand seront-elles vides" },
    { key = "food", type = "toggle", label = "Nourriture / boisson : repos restants" },
    { key = "bags", type = "toggle", label = "Sacs pleins dans N monstres" },
    { key = "drops", type = "toggle", label = "Taux de butin des objets de quête" },
    { key = "money", type = "toggle", label = "Or pour la prochaine formation (≈)" },
    { key = "repair", type = "toggle", label = "Réparation bientôt" },
    { key = "hearth", type = "toggle", label = "Pierre de foyer bientôt prête" },
  },
})
ns.Predict = M
T.Predict = M

local floor, ceil, max, min = math.floor, math.ceil, math.max, math.min
local API = T.API or {}
local HEARTH = 6948

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
local function mod(name) return T.GetModule and T:GetModule(name) or nil end
-- Forever : valeurs « secrètes » en combat / en instance (noms, GUID…) : ne jamais les comparer
local function secret(v) local f = rawget(_G, "issecretvalue") return type(f) == "function" and f(v) and true or false end

-- ------------------------------------------------------------------ formats (purs)
function M.Money(c)
  c = floor(tonumber(c) or 0)
  local g, s, cu = floor(c / 10000), floor((c % 10000) / 100), c % 100
  if g > 0 then return s > 0 and (g .. " po " .. s .. " pa") or (g .. " po") end
  if s > 0 then return cu > 0 and (s .. " pa " .. cu) or (s .. " pa") end
  return cu .. " pc"
end
function M.Minutes(mn)
  mn = tonumber(mn)
  if not mn then return "?" end
  if mn < 1 then return "moins d'1 min" end
  if mn < 90 then return floor(mn + 0.5) .. " min" end
  return ("%d h %02d"):format(floor(mn / 60), floor(mn % 60))
end

-- Coût APPROXIMATIF (cuivre) des sorts à apprendre au palier (niveaux pairs). Ordre de grandeur Classic.
M.TRAINING = {
  [2] = 10, [4] = 100, [6] = 300, [8] = 600, [10] = 1200, [12] = 2000, [14] = 3000, [16] = 4000, [18] = 6000,
  [20] = 10000, [22] = 14000, [24] = 18000, [26] = 24000, [28] = 32000, [30] = 40000, [32] = 50000,
  [34] = 60000, [36] = 70000, [38] = 80000, [40] = 100000, [42] = 110000, [44] = 120000, [46] = 130000,
  [48] = 140000, [50] = 160000, [52] = 180000, [54] = 200000, [56] = 220000, [58] = 250000, [60] = 280000,
}
function M.NextTraining(level)
  level = tonumber(level) or 1
  local nx = level + 1
  if nx % 2 == 1 then nx = nx + 1 end
  if nx > 60 then return nil end
  return nx, M.TRAINING[nx]
end

-- ------------------------------------------------------------------ mesures
local S  -- état de session
local function reset()
  S = {
    start = now(), ammo = {}, lastAmmo = nil, kills = {}, killCount = 0, lastKill = nil, guids = {},
    rests = 0, restT = {}, foodUsed = 0, waterUsed = 0, lastFood = nil, lastWater = nil, eating = false,
    xpGained = 0, lastXP = nil, lastXPMax = nil, moneyIn = 0, moneyOut = 0, lastMoney = nil,
    dur = {}, lastDur = nil, loots = 0, slotsUsed = 0, freeBefore = nil, resting = false,
  }
end
reset()
M.ResetSession = reset
function M.State() return S end

-- fenêtre glissante : liste de {t, n} ; somme sur les `win` dernières secondes
local function push(list, n, keep)
  list[#list + 1] = { now(), n }
  while #list > (keep or 400) do table.remove(list, 1) end
end
local function windowRate(list, win, minSpan)
  local t = now()
  local sum, first = 0, nil
  for _, e in ipairs(list) do
    if t - e[1] <= win then
      sum = sum + e[2]
      first = first or e[1]
    end
  end
  if not first then return nil end
  local span = max(minSpan or 60, t - first)
  return sum / span, sum
end

-- munitions (chasseur) : Hunter:AmmoInfo si dispo, sinon emplacement 0
function M.AmmoCount()
  local H = mod("Hunter")
  if H and H.AmmoInfo then
    local ok, info = pcall(H.AmmoInfo, H)
    if ok and type(info) == "table" then
      if not info.needs then return nil end
      return max(info.total or 0, info.equipped or 0)
    end
  end
  if try(_G.UnitClass, "player") and select(2, _G.UnitClass("player")) ~= "HUNTER" then return nil end
  local n = try(_G.GetInventoryItemCount, "player", 0)
  return tonumber(n)
end

local function countList(list)
  local n = 0
  if type(list) ~= "table" or not API.ItemCount then return 0 end
  for _, e in ipairs(list) do
    local id = type(e) == "table" and e[1] or e
    if id then n = n + (API.ItemCount(id) or 0) end
  end
  return n
end
function M.FoodCount()
  local C = T.Data and T.Data.Consumables or {}
  return countList(C.food), countList(C.water)
end

function M.Durability()
  if type(_G.GetInventoryItemDurability) ~= "function" then return nil end
  local sum, nb, worst = 0, 0, nil
  for slot = 1, 18 do
    local cur, mx = try(_G.GetInventoryItemDurability, slot)
    if cur and mx and mx > 0 then
      local p = cur * 100 / mx
      sum, nb = sum + p, nb + 1
      if not worst or p < worst then worst = p end
    end
  end
  if nb == 0 then return nil end
  return sum / nb, worst
end

function M.HearthLeft()
  local have = API.ItemCount and (API.ItemCount(HEARTH) or 0) or 0
  if have <= 0 then return nil end
  local start, dur
  local C = _G.C_Container
  if C and C.GetItemCooldown then start, dur = try(C.GetItemCooldown, HEARTH) end
  if not start then start, dur = try(_G.GetItemCooldown, HEARTH) end
  if start and dur and start > 0 and dur > 0 then return max(0, start + dur - now()) end
  return 0
end

function M.SampleAmmo()
  local n = M.AmmoCount()
  if not n then S.lastAmmo = nil return nil end
  if S.lastAmmo and n < S.lastAmmo and S.lastAmmo - n < 200 then push(S.ammo, S.lastAmmo - n) end
  S.lastAmmo = n
  return n
end

function M.SampleFood()
  local f, w = M.FoodCount()
  if S.lastFood and f < S.lastFood then S.foodUsed = S.foodUsed + (S.lastFood - f) end
  if S.lastWater and w < S.lastWater then S.waterUsed = S.waterUsed + (S.lastWater - w) end
  S.lastFood, S.lastWater = f, w
  return f, w
end

local EAT = { "Manger", "Boire", "Food", "Drink", "Nourriture", "Boisson" }
local function eatingNow()
  if not API.Aura then return false end
  for _, n in ipairs(EAT) do if API.Aura("player", n) then return true end end
  return false
end
function M.CheckRest()
  local e = eatingNow()
  if e and not S.eating then
    S.rests = S.rests + 1
    push(S.restT, 1, 100)
  end
  S.eating = e
  if e then M.SampleFood() end
end

function M.SampleXP()
  local xp, mx = try(_G.UnitXP, "player"), try(_G.UnitXPMax, "player")
  if not (xp and mx) then return end
  if S.lastXP then
    if xp >= S.lastXP then S.xpGained = S.xpGained + (xp - S.lastXP)
    else S.xpGained = S.xpGained + max(0, (S.lastXPMax or mx) - S.lastXP) + xp end
  end
  S.lastXP, S.lastXPMax = xp, mx
end

function M.SampleMoney()
  local m = try(_G.GetMoney)
  if not m then return end
  if S.lastMoney then
    if m > S.lastMoney then S.moneyIn = S.moneyIn + (m - S.lastMoney) else S.moneyOut = S.moneyOut + (S.lastMoney - m) end
  end
  S.lastMoney = m
end

function M.SampleDurability()
  local avg = M.Durability()
  if not avg then return end
  if S.lastDur and avg > S.lastDur + 1 then S.dur = {} end  -- réparé : on repart
  if S.lastDur and avg < S.lastDur then push(S.dur, S.lastDur - avg, 200) end
  S.lastDur = avg
end

-- monstres tués (PARTY_KILL par toi ou ton familier)
function M.OnKill(guid, name)
  if secret(guid) or secret(name) then return end
  if guid and S.guids[guid] then return end
  if guid then S.guids[guid] = name or true end
  S.killCount = S.killCount + 1
  push(S.kills, 1, 600)
  S.lastKill = { name = name, guid = guid, t = now() }
end

function M.CLEU()
  local info = { try(_G.CombatLogGetCurrentEventInfo) }
  local sub, srcGUID, destGUID, destName = info[2], info[4], info[8], info[9]
  if sub ~= "PARTY_KILL" then return end
  local me, pet = try(_G.UnitGUID, "player"), try(_G.UnitGUID, "pet")
  if srcGUID and (srcGUID == me or srcGUID == pet) then M.OnKill(destGUID, destName) end
end

-- Forever retire COMBAT_LOG_EVENT aux addons : repli sur le message d'XP « X meurt, vous gagnez N points
-- d'expérience » (FR) / « X dies, you gain N experience » (EN).
function M.ParseKillXP(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  local name = msg:match("^(.-) meurt, vous gagnez") or msg:match("^(.-) dies, you gain")
  return name
end
function M.OnXPMessage(msg)
  local name = M.ParseKillXP(msg)
  if not name then return end
  local lk = S.lastKill
  if lk and lk.name == name and now() - lk.t < 1.5 then return end  -- déjà compté par le journal de combat
  M.OnKill(nil, name)
end

-- butin : nom du monstre (GetLootSourceInfo → GUID connu, sinon dernier tué < 2 min)
local function lootSource()
  local guid = try(_G.GetLootSourceInfo, 1)
  if guid and not secret(guid) and type(S.guids[guid]) == "string" then return S.guids[guid] end
  if S.lastKill and now() - S.lastKill.t < 120 then return S.lastKill.name end
  if try(_G.UnitIsDead, "target") then
    local n = try(_G.UnitName, "target")
    if n and not secret(n) then return n end
  end
  return nil
end

local function mobsDB()
  M.db.mobs = type(M.db.mobs) == "table" and M.db.mobs or {}
  return M.db.mobs
end

-- items = { {name=, questItem=}, ... } (le butin ouvert) ; on compte toutes les lignes « quête »
function M.RecordLoot(mob, items)
  if not mob then return end
  local db = mobsDB()
  local k = low(mob)
  local e = db[k]
  if not e then
    local n = 0
    for _ in pairs(db) do n = n + 1 end
    if n >= 300 then return end
    e = { n = mob, l = 0, d = {} }
    db[k] = e
  end
  e.l = e.l + 1
  local Q = mod("Quests")
  for _, it in ipairs(items or {}) do
    local nm = it.name
    local useful = it.questItem or (Q and Q.ObjectiveFor and Q.ObjectiveFor(nm, false))
    if nm and useful then e.d[low(nm)] = (e.d[low(nm)] or 0) + (it.count or 1) end
  end
end

function M.DropRate(mob, item)
  local e = mobsDB()[low(mob)]
  if not (e and e.l and e.l >= 3) then return nil end
  local d = e.d and e.d[low(item)]
  if not d then return nil end
  return d / e.l, e.l
end

-- meilleur monstre (taux mesuré) pour un objet
function M.BestSource(item)
  local best, br, bn
  for _, e in pairs(mobsDB()) do
    local d = e.d and e.d[low(item)]
    if d and e.l >= 3 then
      local r = d / e.l
      if not br or r > br then best, br, bn = e.n, r, e.l end
    end
  end
  return best, br, bn
end

function M.OnLootOpened()
  local n = try(_G.GetNumLootItems) or 0
  local items = {}
  for i = 1, n do
    local _, name, count, _, _, _, isQuestItem = try(_G.GetLootSlotInfo, i)
    if name and not secret(name) then items[#items + 1] = { name = name, count = tonumber(count) or 1, questItem = isQuestItem and true or false } end
  end
  local mob = lootSource()
  if mob then M.RecordLoot(mob, items) end
  S.freeBefore = API.FreeSlots and API.FreeSlots() or nil
  S.lootPending = true
end

function M.OnLootClosed()
  if not S.lootPending then return end
  S.lootPending = false
  local before = S.freeBefore
  T:After(1, function()
    local after = API.FreeSlots and API.FreeSlots() or nil
    if before and after then
      S.loots = S.loots + 1
      S.slotsUsed = S.slotsUsed + max(0, before - after)
    end
  end)
end

-- ------------------------------------------------------------------ taux
function M.Rates()
  local t = now()
  local elapsedH = max(1 / 60, (t - S.start) / 3600)
  local r = { sessionMin = floor((t - S.start) / 60) }
  local ar = windowRate(S.ammo, 1200, 120)
  r.ammoPerMin = ar and ar * 60 or nil
  local kr = windowRate(S.kills, 1800, 300)
  r.killsPerHour = kr and kr * 3600 or nil
  r.kills = S.killCount
  local rr = windowRate(S.restT, 3600, 600)
  r.restsPerHour = rr and rr * 3600 or nil
  r.rests = S.rests
  r.foodPerRest = S.rests > 0 and S.foodUsed / S.rests or nil
  r.waterPerRest = S.rests > 0 and S.waterUsed / S.rests or nil
  if t - S.start >= 300 then
    r.xpPerHour = S.xpGained / elapsedH
    r.goldPerHour = (S.moneyIn - S.moneyOut) / elapsedH
  end
  local dr, dsum = windowRate(S.dur, 7200, 600)
  r.durPerHour = (dr and dsum and dsum > 0) and dr * 3600 or nil
  r.slotsPerKill = S.loots >= 5 and S.slotsUsed / S.loots or nil
  r.loots = S.loots
  return r
end

-- ------------------------------------------------------------------ prévisions (pur : r + ctx)
-- ctx = { ammo, food, water, free, durAvg, durWorst, money, level, xp, xpMax, hearth, objectives =
--   { {name, have, need, mob, rate} }, keep = { {item, quest} } }
function M.Forecasts(r, ctx)
  local out = {}
  local function add(id, text, prio) out[#out + 1] = { id = id, text = text, prio = prio } end
  -- munitions
  if ctx.ammo and r.ammoPerMin and r.ammoPerMin > 0.5 then
    local mn = ctx.ammo / r.ammoPerMin
    if mn < 60 then
      local buy = min(2000, max(200, ceil(r.ammoPerMin * 60 / 200) * 200))
      add("ammo", ("Munitions : vides dans ~%s, achète %d au prochain vendeur"):format(M.Minutes(mn), buy), mn < 15 and 3 or 2)
    else
      add("ammo", ("Munitions : ~%s de tir (%d/min)"):format(M.Minutes(mn), floor(r.ammoPerMin + 0.5)), 1)
    end
  elseif ctx.ammo and ctx.ammo < 200 then
    add("ammo", ("Munitions : plus que %d, achète-en au prochain vendeur"):format(ctx.ammo), 2)
  end
  -- nourriture / boisson
  for _, k in ipairs({ { "food", "Nourriture", r.foodPerRest }, { "water", "Boisson", r.waterPerRest } }) do
    local have, per = ctx[k[1]], k[3]
    if have and per and per > 0 then
      local restsLeft = have / per
      local mn = r.restsPerHour and r.restsPerHour > 0 and (restsLeft / r.restsPerHour * 60) or nil
      local txt = ("%s : ~%d repos%s"):format(k[2], floor(restsLeft), mn and (" (≈ " .. M.Minutes(mn) .. ")") or "")
      add(k[1], txt .. (restsLeft < 3 and ", rachète au prochain aubergiste" or ""), restsLeft < 2 and 3 or (restsLeft < 4 and 2 or 1))
    end
  end
  -- sacs
  if ctx.free and ctx.free > 0 and r.slotsPerKill and r.slotsPerKill > 0.05 then
    local mobs = floor(ctx.free / r.slotsPerKill)
    local kh = r.killsPerHour
    add("bags", ("Sacs pleins dans ~%d monstres%s"):format(mobs, kh and kh > 0 and (" (≈ " .. M.Minutes(mobs / kh * 60) .. ")") or ""),
      mobs <= 5 and 3 or (mobs <= 15 and 2 or 1))
  end
  -- objets de quête : monstres à tuer
  for _, o in ipairs(ctx.objectives or {}) do
    local left = (o.need or 0) - (o.have or 0)
    if left > 0 and o.mob and o.rate and o.rate > 0 then
      local kills = ceil(left / o.rate)
      add("drop:" .. low(o.name), ("Il te faut ~%d %s de plus pour %d %s (taux %d %%)"):format(kills, o.mob, left, o.name,
        floor(o.rate * 100 + 0.5)), 1)
    end
  end
  -- formation
  if ctx.level and ctx.money then
    local lv, cost = M.NextTraining(ctx.level)
    if lv and cost then
      local missing = cost - ctx.money
      local soon = (lv == ctx.level + 1) and ctx.xpMax and ctx.xpMax > 0 and (ctx.xp or 0) / ctx.xpMax >= 0.6
      if missing > 0 then
        local eta = r.goldPerHour and r.goldPerHour > 0 and (" (≈ " .. M.Minutes(missing / r.goldPerHour * 60) .. " de farm)") or ""
        add("train", ("Formation niv %d : ≈ %s — il te manque %s%s"):format(lv, M.Money(cost), M.Money(missing), eta), soon and 2 or 1)
      else
        add("train", ("Formation niv %d : ≈ %s — OK, garde-les"):format(lv, M.Money(cost)), 1)
      end
    end
  end
  -- réparation
  if ctx.durAvg then
    local worst = ctx.durWorst or ctx.durAvg
    local mn = r.durPerHour and r.durPerHour > 0 and ((ctx.durAvg - 20) / r.durPerHour * 60) or nil
    if worst <= 15 then
      add("repair", ("Réparation urgente : un objet à %d %%"):format(floor(worst)), 3)
    elseif (mn and mn < 45) or ctx.durAvg <= 35 then
      add("repair", ("Réparation bientôt : durabilité %d %%%s"):format(floor(ctx.durAvg),
        mn and (" (20 %% dans ~" .. M.Minutes(max(0, mn)) .. ")") or ""), 2)
    end
  end
  -- pierre de foyer
  if ctx.hearth and ctx.hearth > 0 and ctx.hearth <= 600 then
    add("hearth", ("Pierre de foyer prête dans %s"):format(M.Minutes(ctx.hearth / 60)), 1)
  end
  -- objets à garder
  for _, k in ipairs(ctx.keep or {}) do
    add("keep:" .. low(k.item), ("Garde %s : sert pour « %s »"):format(cut(k.item, 30), cut(k.quest, 30)), 1)
  end
  table.sort(out, function(a, b) if a.prio ~= b.prio then return a.prio > b.prio end return a.id < b.id end)
  return out
end

-- ------------------------------------------------------------------ contexte réel
-- objectifs « objet » non finis + meilleur monstre mesuré
local function objectives()
  local Q = mod("Quests")
  local out = {}
  for _, q in ipairs(Q and Q.lastList or {}) do
    if not q.complete then
      for _, o in ipairs(q.obj or {}) do
        if not o.finished and (o.need or 1) > 1 then
          local mob, rate = M.BestSource(o.name)
          if mob then out[#out + 1] = { name = o.name, have = o.have, need = o.need, mob = mob, rate = rate } end
        end
      end
    end
  end
  return out
end

-- objets en sac demandés par des quêtes à venir (Questie : disponibles ici + suites de chaînes)
local function keepItems()
  local C = T.Compass
  local db = C and C.Questie and C.Questie()
  local Q = mod("Quests")
  if not (db and Q and API.ItemCount) then return {} end
  local ids = {}
  for _, e in ipairs((Q.lastQuestie and Q.lastQuestie.here) or {}) do ids[#ids + 1] = e.id end
  for _, q in ipairs(Q.lastList or {}) do
    local nx = tonumber((try(db.QueryQuestSingle, q.id, "nextQuestInChain")))
    if nx and nx > 0 then ids[#ids + 1] = nx end
  end
  local out, seen = {}, {}
  for _, id in ipairs(ids) do
    local items = {}
    for _, it in ipairs(try(db.QueryQuestSingle, id, "requiredSourceItems") or {}) do items[#items + 1] = it end
    local objs = try(db.QueryQuestSingle, id, "objectives")
    for _, it in ipairs(type(objs) == "table" and type(objs[3]) == "table" and objs[3] or {}) do
      items[#items + 1] = type(it) == "table" and it[1] or it
    end
    for _, itemID in ipairs(items) do
      if type(itemID) == "number" and not seen[itemID] and (API.ItemCount(itemID) or 0) > 0 then
        seen[itemID] = true
        out[#out + 1] = { item = (API.ItemName and API.ItemName(itemID)) or ("objet " .. itemID),
          quest = try(db.QueryQuestSingle, id, "name") or ("#" .. id) }
        if #out >= 3 then return out end
      end
    end
  end
  return out
end

function M.Context()
  local f, w = M.FoodCount()
  local avg, worst = M.Durability()
  return {
    ammo = cfg("ammo") and M.AmmoCount() or nil,
    food = cfg("food") and f or nil, water = cfg("food") and w or nil,
    free = cfg("bags") and API.FreeSlots and API.FreeSlots() or nil,
    durAvg = cfg("repair") and avg or nil, durWorst = worst,
    money = cfg("money") and try(_G.GetMoney) or nil,
    level = try(_G.UnitLevel, "player"), xp = try(_G.UnitXP, "player"), xpMax = try(_G.UnitXPMax, "player"),
    hearth = cfg("hearth") and M.HearthLeft() or nil,
    objectives = cfg("drops") and objectives() or {},
    keep = cfg("keep") and keepItems() or {},
  }
end

-- ------------------------------------------------------------------ sortie
local shown = {}
function M.Update()
  M.SampleAmmo()
  M.SampleFood()
  M.SampleDurability()
  local r = M.Rates()
  local list = M.Forecasts(r, M.Context())
  M.rates, M.list = r, list
  -- « courses » : le service le plus proche connu (TreeAI.Detect : PNJ appris, maîtres de vol, Questie)
  local Dt = T.Detect
  if type(Dt) == "table" and type(Dt.NearestText) == "function" then
    local SERVICE = { ammo = "vendor", food = "vendor", water = "vendor", repair = "repair", train = "trainer" }
    for _, f in ipairs(list) do
      local kind = SERVICE[f.id]
      if kind and f.prio >= 2 then
        local ok, txt = pcall(Dt.NearestText, kind)
        if ok and type(txt) == "string" then f.text, f.near = f.text .. " · " .. txt, txt end
      end
    end
  end
  -- urgentes (prio ≥ 2) → « Maintenant » ; les autres seulement dans l'onglet
  local keep = {}
  for _, f in ipairs(list) do
    -- bags / repair : Inventory already warns in « Maintenant » (same meaning twice = noise) -> tab only
    local inv = T.GetModule and T:GetModule("Inventory")
    local owned = (f.id == "bags" or f.id == "repair") and inv and (not inv.IsEnabled or inv:IsEnabled())
    if f.prio >= 2 and not owned then
      keep[f.id] = true
      M:Suggest(f.id, f.text, f.prio, { ttl = 120, cooldown = f.prio >= 3 and 120 or 600, sound = f.prio >= 3 })
    end
  end
  for id in pairs(shown) do if not keep[id] then M:Clear(id) end end
  shown = keep
  if T.SetTabLines then
    local lines = {}
    for _, f in ipairs(list) do lines[#lines + 1] = { text = f.text, prio = f.prio } end
    local rl = {}
    if r.killsPerHour then rl[#rl + 1] = floor(r.killsPerHour + 0.5) .. " monstres/h" end
    if r.xpPerHour then rl[#rl + 1] = floor(r.xpPerHour / 100 + 0.5) / 10 .. "k XP/h" end
    if r.goldPerHour then rl[#rl + 1] = M.Money(max(0, r.goldPerHour)) .. "/h" end
    if #rl > 0 then lines[#lines + 1] = { text = "Rythme : " .. table.concat(rl, " · "), prio = 1 } end
    if #lines == 0 then lines[1] = { text = "Prévisions : mesures en cours (quelques minutes de jeu)…", prio = 1 } end
    pcall(T.SetTabLines, T, "predict", lines)
  end
  M.WriteSummary()
  return list
end

function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local r = M.rates or {}
  local function rnd(x, k) if not x then return nil end k = k or 1 return floor(x * k + 0.5) / k end
  local fc = {}
  for i, f in ipairs(M.list or {}) do
    if i > 10 then break end
    fc[#fc + 1] = { id = f.id, text = f.text, prio = f.prio }
  end
  s.predict = {
    updated = try(_G.time), sessionMin = r.sessionMin,
    rates = { ammoPerMin = rnd(r.ammoPerMin, 10), killsPerHour = rnd(r.killsPerHour), xpPerHour = rnd(r.xpPerHour),
      goldPerHour = rnd(r.goldPerHour), durPerHour = rnd(r.durPerHour, 10), foodPerRest = rnd(r.foodPerRest, 10),
      waterPerRest = rnd(r.waterPerRest, 10), restsPerHour = rnd(r.restsPerHour, 10), slotsPerKill = rnd(r.slotsPerKill, 100),
      kills = r.kills, loots = r.loots },
    forecasts = fc,
  }
end

local pending
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 2, function() pending = false pcall(M.Update) end)
end

function M:OnEnable()
  if T.RegisterTab then pcall(T.RegisterTab, T, "predict", "Prévisions", 2.5) end
  reset()
  M.SampleXP()
  M.SampleMoney()
  self:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED", function() M.CLEU() end)
  self:RegisterEvent("LOOT_OPENED", function() M.OnLootOpened() end)
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg) M.OnXPMessage(msg) end)
  self:RegisterEvent("LOOT_CLOSED", function() M.OnLootClosed() end)
  self:RegisterEvent("PLAYER_XP_UPDATE", function() M.SampleXP() end)
  self:RegisterEvent("PLAYER_MONEY", function() M.SampleMoney() end)
  self:RegisterEvent("UPDATE_INVENTORY_DURABILITY", function() M.SampleDurability() end)
  self:RegisterUnitEvent("UNIT_AURA", "player", function() M.CheckRest() end)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", function() M.SampleAmmo() end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() M.SampleAmmo() M.Refresh(3) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() M.Refresh(1) end)
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  if not M.ticker then M.ticker = T:NewTicker(15, function() pcall(M.Update) end) end
  M.Refresh(5)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
end

local function slash()
  local list = M.Update()
  local r = M.rates or {}
  M:Print(("|cff33cc66Prévisions|r (session %d min, %d monstres)"):format(r.sessionMin or 0, r.kills or 0))
  if #list == 0 then M:Print("Rien à signaler pour l'instant (les taux se mesurent en jouant).") end
  for _, f in ipairs(list) do M:Print((f.prio >= 3 and "|cffff5555" or (f.prio == 2 and "|cffffd100" or "")) .. f.text .. "|r") end
end
T:RegisterSlash("prevoir", slash, "prévisions : munitions, sacs, butin, formation, réparation…")
T:RegisterSlash("prévoir", slash)
T:RegisterSlash("predict", slash)
