-- TreeAI — module Dungeon (agent PVE) : aide de donjon, INFORMATIVE / un clic, onglet « Donjon ».
-- Visible en instance, ou quand un donjon de groupe est prévu (/tai donjon <nom>, ou auto : quête de donjon
-- dans le journal + groupe). Données : Data/Dungeons.lua (id de rencontre + noms FR = tables client Forever ;
-- conseils / minuteurs / butin = indicatifs, écrits d'après Classic).
--   * avant : niveaux du groupe vs tranche, tank / soigneur présents, quêtes à prendre (QuestieDB, lecture
--     seule) ou déjà dans le journal, nourriture / eau / munitions / durabilité (Squad, à jour hors instance),
--     butin « amélioration pour toi » (Boost.UpgradePct) / « pour <pote> » (classes du groupe)
--   * pendant : boss détecté par ENCOUNTER_START (id Forever) ou BOSS_KILL / cris lisibles / clic sur le boss,
--     conseil FR court, minuteurs approximatifs (encadré du coin seulement pour les importants, et pas en
--     double de la frise de boss Blizzard C_EncounterTimeline quand elle est active)
--   * après : boss vaincus, wipe -> liste de reprise, compteur de pulls / morts / durée, XP et or du run,
--     « mini-compteur » de dégâts lu dans C_DamageMeter (le compteur Blizzard) hors combat seulement
--   * chasseur : pull au familier, piège, Feindre la mort, marque conseillée (un clic = une marque, hors combat)
-- Forever : pas de COMBAT_LOG_EVENT pour les addons, valeurs secrètes en combat / en instance (noms des
-- monstres, chat) -> chaque valeur passe par sv() (issecretvalue) ; rien n'est fait à la place du joueur.
-- Pas de doublon : compteur de pull / patrouilles = Fight.lua (repris tel quel), durabilité = Economy,
-- quêtes = Quests / Pro (on ne lit que ce qui concerne le donjon choisi).
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local G = _G
local API = T.API or {}

local M = T:RegisterModule("Dungeon", {
  title = "Donjon (PvE)",
  defaults = {
    enabled = true, tab = true, autoPlan = true, checklist = true, quests = true, loot = true,
    timers = true, corner = true, yells = true, meter = true, wipe = true, hunter = true, marks = true,
    forceTimers = false, learned = {},
  },
  charDefaults = { planned = nil, run = nil, runs = {} },
  options = {
    { key = "tab", type = "toggle", label = "Onglet « Donjon » (en instance ou donjon prévu)", section = "Donjon" },
    { key = "autoPlan", type = "toggle", label = "Prévoir le donjon tout seul (quête de donjon + groupe)" },
    { key = "checklist", type = "toggle", label = "Liste avant le donjon (niveaux, rôles, consommables)", section = "Avant" },
    { key = "quests", type = "toggle", label = "Quêtes du donjon à prendre (QuestieDB)" },
    { key = "loot", type = "toggle", label = "Butin : amélioration pour toi / pour tes potes" },
    { key = "timers", type = "toggle", label = "Minuteurs de boss (indicatifs)", section = "Boss" },
    { key = "corner", type = "toggle", label = "Encadré d'alerte pour les minuteurs importants" },
    { key = "forceTimers", type = "toggle", label = "Encadré même si la frise de boss Blizzard est active" },
    { key = "yells", type = "toggle", label = "Cris / émotes de boss (quand le jeu les laisse lire)" },
    { key = "meter", type = "toggle", label = "Mini-compteur (compteur de dégâts Blizzard, hors combat)", section = "Après" },
    { key = "wipe", type = "toggle", label = "Liste de reprise après un wipe" },
    { key = "hunter", type = "toggle", label = "Chasseur : pull au familier, piège, Feindre la mort", section = "Chasseur" },
    { key = "marks", type = "toggle", label = "Marque conseillée (clic = pose la marque, hors combat)" },
  },
})
T.Dungeon = M
local TAB = "dungeon"

---------------------------------------------------------------------------------------------------
-- utilitaires défensifs
---------------------------------------------------------------------------------------------------
local fmt, floor, min, max = string.format, math.floor, math.min, math.max
local tinsert, tconcat, tsort = table.insert, table.concat, table.sort
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function now() return try(G.GetTime) or 0 end
local function clock() return try(G.time) or floor(now()) end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function myClass() return T:PlayerClass() end
local function level(u) return tonumber(sv(try(G.UnitLevel, u or "player"))) or 0 end
local function mod(name) local m = T.modules and T.modules[name] if type(m) == "table" then return m end end

local ACCENTS = { ["é"] = "e", ["è"] = "e", ["ê"] = "e", ["ë"] = "e", ["à"] = "a", ["â"] = "a", ["î"] = "i",
  ["ï"] = "i", ["ô"] = "o", ["û"] = "u", ["ù"] = "u", ["ç"] = "c", ["œ"] = "oe", ["É"] = "e", ["’"] = "'" }
local function norm(s)
  if type(s) ~= "string" then return "" end
  s = s:lower()
  for a, b in pairs(ACCENTS) do s = s:gsub(a, b) end
  return s
end
M.Norm = norm

local function dur(sec)
  sec = floor(tonumber(sec) or 0)
  if sec >= 3600 then return fmt("%d h %02d", floor(sec / 3600), floor(sec % 3600 / 60)) end
  return fmt("%d:%02d", floor(sec / 60), sec % 60)
end
local function short(n)
  n = tonumber(n) or 0
  if n >= 1e6 then return fmt("%.1fM", n / 1e6) end
  if n >= 1e4 then return fmt("%.0fk", n / 1e3) end
  return tostring(floor(n))
end
local function money(c)
  c = floor(tonumber(c) or 0)
  if c >= 10000 then return fmt("%d po %d pa", floor(c / 10000), floor(c % 10000 / 100)) end
  if c >= 100 then return fmt("%d pa %d pc", floor(c / 100), c % 100) end
  return c .. " pc"
end

local CLASS_FR = { HUNTER = "Chasseur", ROGUE = "Voleur", WARRIOR = "Guerrier", PALADIN = "Paladin",
  SHAMAN = "Chaman", DRUID = "Druide", MAGE = "Mage", WARLOCK = "Démoniste", PRIEST = "Prêtre" }
local TANKS = { WARRIOR = true, PALADIN = true, DRUID = true }
local HEALERS = { PRIEST = true, DRUID = true, SHAMAN = true, PALADIN = true }
local REZ = { PRIEST = true, PALADIN = true, SHAMAN = true, DRUID = true }

---------------------------------------------------------------------------------------------------
-- données : index par carte, rencontre, nom
---------------------------------------------------------------------------------------------------
local index
function M.Index()
  if index then return index end
  index = { byMap = {}, byEnc = {}, byName = {}, list = {} }
  local D = T.Data and T.Data.Dungeons
  for _, d in ipairs(D and D.list or {}) do
    index.list[#index.list + 1] = d
    if d.map then index.byMap[d.map] = d end
    for bi, b in ipairs(d.bosses or {}) do
      b.index, b.dungeon = bi, d.key
      for _, id in ipairs(b.ids or {}) do index.byEnc[id] = { d = d, b = b } end
      if b.n then index.byName[norm(b.n)] = { d = d, b = b } end
      if b.en then index.byName[norm(b.en)] = { d = d, b = b } end
    end
  end
  return index
end
function M.Data() return T.Data and T.Data.Dungeons or {} end

--- Donjon par clé, nom ou mot-clé (FR/EN, accents ignorés).
function M.Find(q)
  if type(q) == "table" then return q end
  if q == nil then return nil end
  local I = M.Index()
  local n = tonumber(q)
  if n and I.byMap[n] then return I.byMap[n] end
  local s = norm(tostring(q))
  if s == "" then return nil end
  for _, d in ipairs(I.list) do if norm(d.key) == s then return d end end
  for _, d in ipairs(I.list) do
    if norm(d.n):find(s, 1, true) or s:find(norm(d.n), 1, true) then return d end
    for _, k in ipairs(d.kw or {}) do
      local kk = norm(k)
      if s:find(kk, 1, true) or kk:find(s, 1, true) then return d end
    end
  end
  return nil
end

--- Boss par id de rencontre (puis appris), puis par nom.
function M.FindBoss(encID, name)
  local I = M.Index()
  encID = tonumber(sv(encID))
  if encID and I.byEnc[encID] then return I.byEnc[encID].d, I.byEnc[encID].b end
  local learned = encID and opt("learned") and M.db.learned[encID]
  if type(learned) == "string" then
    local k, i = learned:match("^(.-):(%d+)$")
    local d = M.Find(k)
    local b = d and d.bosses and d.bosses[tonumber(i)]
    if b then return d, b end
  end
  name = sv(name)
  if type(name) == "string" and name ~= "" then
    local e = I.byName[norm(name)]
    if e then
      if encID and M.db and type(M.db.learned) == "table" then M.db.learned[encID] = e.d.key .. ":" .. e.b.index end
      return e.d, e.b
    end
  end
  return nil
end

---------------------------------------------------------------------------------------------------
-- instance / donjon actif
---------------------------------------------------------------------------------------------------
--- -> inInstance, instanceType, instanceID, nom
function M.Instance()
  local inInst, itype = try(G.IsInInstance)
  inInst, itype = sv(inInst), sv(itype)
  local name, itype2, _, _, _, _, _, instID = try(G.GetInstanceInfo)
  name, instID = sv(name), tonumber(sv(instID))
  itype = itype or sv(itype2)
  local dungeonLike = inInst and (itype == "party" or itype == "raid" or itype == "scenario") and true or false
  return dungeonLike, itype, instID, name
end

function M.Current()
  local inInst, _, instID, name = M.Instance()
  if not inInst then return nil end
  local I = M.Index()
  if instID and I.byMap[instID] then return I.byMap[instID] end
  return M.Find(name)
end

local function questDungeons()
  -- quêtes de donjon du journal (Quests.lastList : q.dungeon / zone) -> donjon prévu
  local Q = mod("Quests")
  local out = {}
  for _, q in ipairs(Q and Q.lastList or {}) do
    if type(q) == "table" and (q.dungeon or q.groupTag) then
      local d = M.Find(q.zone) or M.Find(q.header)
      if d then out[d.key] = (out[d.key] or 0) + 1 end
    end
  end
  return out
end

--- Donjon prévu (choix du joueur, sinon auto : groupe + quête de donjon à ton niveau).
function M.Planned()
  local c = M.char
  if c and c.planned then
    local d = M.Find(c.planned)
    if d then return d, "choisi" end
  end
  if not opt("autoPlan") then return nil end
  if (tonumber((try(G.GetNumGroupMembers))) or 0) < 2 then return nil end
  local pl, best, bestN = level(), nil, 0
  for key, n in pairs(questDungeons()) do
    local d = M.Find(key)
    if d and pl >= (d.min or 1) - 2 and pl <= (d.max or 60) + 2 and n > bestN then best, bestN = d, n end
  end
  if best then return best, "auto" end
  return nil
end

function M.Active()
  local d = M.Current()
  if d then return d, "instance" end
  return M.Planned()
end

function M.SetPlanned(q)
  if not M.char then return nil end
  if q == nil or q == "" or q == "stop" then M.char.planned = nil; M:Refresh(true) return nil end
  local d = M.Find(q)
  if d then M.char.planned = d.key end
  M:Refresh(true)
  return d
end

---------------------------------------------------------------------------------------------------
-- groupe : membres (unités + états Squad), rôles
---------------------------------------------------------------------------------------------------
function M.Members()
  local list = {}
  local _, cls = try(G.UnitClass, "player")
  list[1] = { unit = "player", name = sv(try(G.UnitName, "player")) or "toi", class = sv(cls) or myClass(), level = level(), me = true }
  for i = 1, 4 do
    local u = "party" .. i
    if sv(try(G.UnitExists, u)) then
      local _, c = try(G.UnitClass, u)
      local role = sv(try(G.UnitGroupRolesAssigned, u))
      list[#list + 1] = { unit = u, name = sv(try(G.UnitName, u)) or u, class = sv(c), level = level(u),
        role = (role and role ~= "NONE") and role or nil }
    end
  end
  local myRole = sv(try(G.UnitGroupRolesAssigned, "player"))
  if myRole and myRole ~= "NONE" then list[1].role = myRole end
  -- état Squad (nourriture, eau, munitions, durabilité) quand le pote a TreeAI
  local S = T.Squad
  if type(S) == "table" and type(S.MemberList) == "function" then
    local ok, ml = pcall(S.MemberList, S)
    if ok and type(ml) == "table" then
      local byName = {}
      for _, e in ipairs(ml) do if type(e) == "table" and e.name then byName[e.name] = e.st end end
      for i, m in ipairs(list) do
        m.st = byName[m.name] or (i == 1 and ml[1] and ml[1].st) or nil
      end
    end
  end
  return list
end

--- Rôles : rôle assigné, sinon devinés par la classe (tank : guerrier/paladin/druide ; soins : prêtre…).
function M.Roles(members)
  local tank, healer
  for _, m in ipairs(members) do if m.role == "TANK" then tank = m end if m.role == "HEALER" then healer = m end end
  if not healer then for _, m in ipairs(members) do if m.class == "PRIEST" then healer = m break end end end
  if not healer then for _, m in ipairs(members) do if HEALERS[m.class] and m ~= tank then healer = m break end end end
  if not tank then for _, m in ipairs(members) do if m.class == "WARRIOR" and m ~= healer then tank = m break end end end
  if not tank then for _, m in ipairs(members) do if TANKS[m.class] and m ~= healer then tank = m break end end end
  return tank, healer
end

---------------------------------------------------------------------------------------------------
-- quêtes du donjon (QuestieDB lecture seule : LibQuestieDB puis QuestieLoader)
---------------------------------------------------------------------------------------------------
local function questie()
  local lib = rawget(G, "LibQuestieDB")
  if type(lib) == "table" and type(lib.Quest) == "table" and type(lib.Quest.Get) == "function" then
    local Q = lib.Quest
    return function(id, key) return try(Q.Get, id, key) end,
      function() local ids = try(Q.GetAllIds, false) return type(ids) == "table" and ids or {} end
  end
  local L = rawget(G, "QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB")
  if type(db) == "table" and type(db.QueryQuestSingle) == "function" then
    return function(id, key) return try(db.QueryQuestSingle, id, key) end,
      function()
        local out = {}
        for k, v in pairs(type(db.QuestPointers) == "table" and db.QuestPointers or {}) do
          out[#out + 1] = type(k) == "number" and k or tonumber(v)
        end
        return out
      end
  end
  return nil
end

local function completed()
  local Q = rawget(G, "Questie")
  local c = type(Q) == "table" and type(Q.db) == "table" and type(Q.db.char) == "table" and Q.db.char.complete
  if type(c) == "table" then return c end
  return {}
end
local function isDone(id, done)
  if done[id] then return true end
  local CQ = G.C_QuestLog
  if CQ and CQ.IsQuestFlaggedCompleted then return sv(try(CQ.IsQuestFlaggedCompleted, id)) and true or false end
  return false
end

local questCache = {}
--- -> { inLog = { {id, title} }, toGet = { {id, title, level, giver?} } } (≤ 6 chacun)
function M.Quests(d)
  local res = { inLog = {}, toGet = {} }
  if not d then return res end
  local Q = mod("Quests")
  local logIds = {}
  for _, q in ipairs(Q and Q.lastList or {}) do
    if type(q) == "table" and q.id then
      logIds[q.id] = q
      if M.Find(q.zone) == d or M.Find(q.header) == d then res.inLog[#res.inLog + 1] = { id = q.id, title = q.title or ("#" .. q.id), done = q.complete } end
    end
  end
  local get, all = questie()
  if not (get and d.area and opt("quests")) then return res end
  local pl = level()
  local key = d.key .. "#" .. pl
  local c = questCache[key]
  if not c or now() - c.t > 60 then
    local list, done, n = {}, completed(), 0
    for _, id in ipairs(all()) do
      n = n + 1
      if n > 40000 then break end
      if type(id) == "number" and tonumber(get(id, "zoneOrSort")) == d.area then
        local rl = tonumber(get(id, "requiredLevel")) or 0
        local ql = tonumber(get(id, "questLevel")) or rl
        if rl <= pl + 2 and not isDone(id, done) then
          list[#list + 1] = { id = id, title = get(id, "name") or ("#" .. id), level = ql, req = rl }
        end
      end
    end
    tsort(list, function(a, b) return a.level < b.level end)
    c = { t = now(), list = list }
    questCache[key] = c
  end
  for _, q in ipairs(c.list) do
    if logIds[q.id] then
      local dup = false
      for _, e in ipairs(res.inLog) do if e.id == q.id then dup = true end end
      if not dup then res.inLog[#res.inLog + 1] = { id = q.id, title = q.title, done = logIds[q.id].complete } end
    elseif #res.toGet < 6 then
      res.toGet[#res.toGet + 1] = q
    end
  end
  return res
end

---------------------------------------------------------------------------------------------------
-- butin : pour toi (Boost) / pour tes potes (classes)
---------------------------------------------------------------------------------------------------
local function classSet(s)
  local t = {}
  if s == "ALL" then for k in pairs(CLASS_FR) do t[k] = true end return t end
  for w in tostring(s or ""):gmatch("%u+") do t[w] = true end
  return t
end
M.ClassSet = classSet

function M.ItemName(id, fallback)
  local name, link = try(G.GetItemInfo, id)
  if not name and G.C_Item and G.C_Item.GetItemInfo then name, link = try(G.C_Item.GetItemInfo, id) end
  return sv(name) or fallback or ("objet " .. tostring(id)), sv(link)
end

--- -> mine = { {boss, id, name, pct?} }, friends = { [nom] = { {boss, id, name} } }
function M.Loot(d, members)
  local mine, friends = {}, {}
  if not d then return mine, friends end
  local B = mod("Boost")
  local cls = myClass()
  for _, b in ipairs(d.bosses or {}) do
    for _, it in ipairs(b.loot or {}) do
      local set = classSet(it[3])
      local name, link = M.ItemName(it[1], it[2])
      if set[cls] then
        local pct, ok = nil, true
        if link and B and B.UpgradePct then pct = try(B.UpgradePct, link) end
        if link and B and B.CanUse then ok = try(B.CanUse, link) ~= false end
        if ok and (pct == nil or pct > 0) then
          mine[#mine + 1] = { boss = b.n, id = it[1], name = name, pct = pct, killed = M.Killed(b) }
        end
      end
      for _, m in ipairs(members or {}) do
        if not m.me and m.class and set[m.class] then
          friends[m.name] = friends[m.name] or {}
          tinsert(friends[m.name], { boss = b.n, id = it[1], name = name, class = m.class })
        end
      end
    end
  end
  tsort(mine, function(a, b) return (a.pct or 1) > (b.pct or 1) end)
  return mine, friends
end

---------------------------------------------------------------------------------------------------
-- run : pulls, morts, wipes, boss vaincus, XP, or, durée
---------------------------------------------------------------------------------------------------
local function run() return M.char and M.char.run end
function M.Killed(b)
  local r = run()
  return r and b and r.kills and r.kills[b.dungeon .. ":" .. b.index] and true or false
end

function M:StartRun(d)
  if not (d and self.char) then return end
  local r = self.char.run
  if r and r.key == d.key and clock() - (r.last or 0) < 3600 then r.last = clock() return r end
  if r and r.key then self:ArchiveRun() end
  r = { key = d.key, start = clock(), last = clock(), pulls = 0, deaths = 0, wipes = 0, kills = {}, nk = 0,
    xp = 0, money0 = tonumber((try(G.GetMoney))) or 0, gold = 0 }
  self.char.run = r
  self._lastXP = tonumber(sv(try(G.UnitXP, "player"))) or 0
  return r
end

function M:ArchiveRun()
  local r = self.char and self.char.run
  if not r then return end
  local runs = self.char.runs or {}
  self.char.runs = runs
  runs[#runs + 1] = { key = r.key, start = r.start, time = (r.last or r.start) - r.start, kills = r.nk, wipes = r.wipes,
    deaths = r.deaths, pulls = r.pulls, xp = r.xp, gold = r.gold }
  while #runs > 20 do table.remove(runs, 1) end
  self.char.run = nil
end

---------------------------------------------------------------------------------------------------
-- rencontres : début / fin / minuteurs
---------------------------------------------------------------------------------------------------
local enc            -- { d, b, start, warned = {}, src }
local wipeAt, lastKill

--- La frise de boss Blizzard est-elle là ? (C_EncounterTimeline, Midnight / Forever)
function M.BlizzardTimeline()
  local ET = G.C_EncounterTimeline
  if type(ET) ~= "table" then return false end
  if ET.IsFeatureAvailable and not try(ET.IsFeatureAvailable) then return false end
  if ET.IsFeatureEnabled and try(ET.IsFeatureEnabled) == false then return false end
  if ET.HasActiveEvents then return try(ET.HasActiveEvents) and true or false end
  return true
end

function M:StartEncounter(d, b, src)
  if not (d and b) then return end
  enc = { d = d, b = b, start = now(), warned = {}, src = src or "event" }
  local r = run() or self:StartRun(d)
  if r then r.last = clock() end
  local txt = "Boss : " .. b.n .. (b.tip and (" — " .. b.tip) or "")
  self:Suggest("boss", txt, 2, { ttl = 45, tab = TAB, cooldown = 60 })
  for i, a in ipairs(b.alerts or {}) do
    if (a.first or 0) <= 0 and opt("timers") then
      self:Suggest("timer" .. i, a.text, min(a.prio or 2, 2), { ttl = 20, tab = TAB, cooldown = 60 })
      enc.warned[i .. ":0"] = true
    end
  end
  self:Refresh(true)
end

function M:EndEncounter(success, d, b)
  local e = enc
  d, b = d or (e and e.d), b or (e and e.b)
  enc = nil
  for i = 1, 6 do self:Clear("timer" .. i) end
  self:Clear("boss"); self:Clear("yell")
  local r = run()
  if success and b then
    if r then
      local k = b.dungeon .. ":" .. b.index
      if not r.kills[k] then r.kills[k] = true; r.nk = (r.nk or 0) + 1 end
      r.last = clock()
    end
    lastKill = { b = b, t = now() }
    local loot = {}
    for _, it in ipairs(b.loot or {}) do loot[#loot + 1] = (M.ItemName(it[1], it[2])) end
    self:Suggest("kill", "Boss vaincu : " .. b.n .. (#loot > 0 and (" — butin possible : " .. tconcat(loot, ", ")) or ""),
      1, { ttl = 60, tab = TAB })
  elseif success == false then
    wipeAt = now()
    if r then r.wipes = (r.wipes or 0) + 1 end
    if opt("wipe") then
      self:Suggest("wipe", "Wipe" .. (b and (" sur " .. b.n) or "") .. " : pas de panique — regarde l'onglet Donjon (reprise).",
        3, { ttl = 30, tab = TAB, cooldown = 120 })
    end
  end
  self:Refresh(true)
end

--- Minuteurs : prévient 5 s avant chaque échéance approximative (first + k × every).
function M:TickTimers()
  if not (enc and opt("timers")) then return end
  local t = now() - enc.start
  local blizz = M.BlizzardTimeline() and not opt("forceTimers")
  for i, a in ipairs(enc.b.alerts or {}) do
    local first, every = a.first or 0, a.every
    if first > 0 then
      local k = 0
      if every and every > 0 and t > first then k = floor((t - first + 5) / every) end
      local due = first + k * (every or 0)
      local key = i .. ":" .. k
      if t >= due - 5 and t < due + 2 and not enc.warned[key] then
        enc.warned[key] = true
        local prio = a.prio or 2
        if prio >= 3 and (blizz or not opt("corner")) then prio = 2 end
        local left = max(0, floor(due - t + 0.5))
        self:Suggest("timer" .. i, a.text .. (left > 0 and (" (≈ " .. left .. " s)") or ""), prio,
          { ttl = 8, tab = TAB, cooldown = 4 })
      end
    end
  end
end
function M.Encounter() return enc end

---------------------------------------------------------------------------------------------------
-- mini-compteur : C_DamageMeter (compteur Blizzard), lu HORS combat seulement
---------------------------------------------------------------------------------------------------
local DM_DAMAGE, DM_INTERRUPTS, DM_DEATHS = 0, 5, 9
local function session(kind, stype)
  local DMA = G.C_DamageMeter
  if type(DMA) ~= "table" or type(DMA.GetCombatSessionFromType) ~= "function" then return nil end
  if DMA.IsDamageMeterAvailable and not try(DMA.IsDamageMeterAvailable) then return nil end
  local E = G.Enum and G.Enum.DamageMeterType
  local ST = G.Enum and G.Enum.DamageMeterSessionType
  local k = (E and kind == DM_DAMAGE and E.DamageDone) or (E and kind == DM_INTERRUPTS and E.Interrupts) or kind
  local s = (ST and stype == 1 and ST.Current) or (ST and stype == 0 and ST.Overall) or stype
  local res = try(DMA.GetCombatSessionFromType, s, k)
  if type(res) ~= "table" or secret(res) then return nil end
  return res
end

--- -> { total, dur, rows = { {name, amount, me, class} } } ou nil (API absente / valeurs secrètes)
function M.ReadMeter(stype, kind)
  if inCombat() then return nil end
  local s = session(kind or DM_DAMAGE, stype or 1)
  if not s then return nil end
  local rows, total = {}, 0
  for _, src in ipairs(type(s.combatSources) == "table" and s.combatSources or {}) do
    local amount = tonumber(sv(src.totalAmount))
    if amount and amount > 0 then
      rows[#rows + 1] = { name = sv(src.name) or (src.isLocalPlayer and "toi") or "?", amount = amount,
        me = sv(src.isLocalPlayer) and true or false, class = sv(src.classFilename), dps = tonumber(sv(src.amountPerSecond)) }
      total = total + amount
    end
  end
  if #rows == 0 then return nil end
  tsort(rows, function(a, b) return a.amount > b.amount end)
  return { total = total, dur = tonumber(sv(s.durationSeconds)), rows = rows }
end

function M:UpdateMeter()
  if not opt("meter") or inCombat() then return end
  self.lastMeter = M.ReadMeter(1, DM_DAMAGE)
  self.runMeter = M.ReadMeter(0, DM_DAMAGE)
  self.interrupts = M.ReadMeter(0, DM_INTERRUPTS)
end

local function meterText(m, label)
  if not m then return nil end
  local parts = {}
  for i = 1, min(4, #m.rows) do
    local r = m.rows[i]
    parts[#parts + 1] = fmt("%s %d %%", r.me and "toi" or r.name, floor(r.amount * 100 / m.total + 0.5))
  end
  return fmt("%s (%s) : %s", label, short(m.total), tconcat(parts, " · "))
end

---------------------------------------------------------------------------------------------------
-- marque conseillée (clic du joueur, hors combat, une marque par clic)
---------------------------------------------------------------------------------------------------
function M.MarkPlan(members)
  local have = {}
  for _, m in ipairs(members or {}) do if m.class then have[m.class] = true end end
  local out = {}
  for _, mk in ipairs((M.Data().marks) or {}) do
    local ok = not mk.class
    if mk.class then for c in mk.class:gmatch("%u+") do if have[c] then ok = true end end end
    if ok then out[#out + 1] = mk end
  end
  return out
end

function M:MarkTarget()
  if inCombat() then self:Print("Pas de marque en combat : pose-la avant le pull.") return end
  if not (sv(try(G.UnitExists, "target")) and sv(try(G.UnitCanAttack, "player", "target"))) then
    self:Print("Cible un monstre du prochain pack, puis clique : crâne, puis croix, puis CC.") return
  end
  local plan = M.MarkPlan(M.Members())
  if #plan == 0 then return end
  local cur = tonumber(sv(try(G.GetRaidTargetIndex, "target")))
  local used = {}
  for i = 1, 4 do
    local u = "party" .. i .. "target"
    local x = tonumber(sv(try(G.GetRaidTargetIndex, u)))
    if x then used[x] = true end
  end
  self._markStep = self._markStep or 0
  if now() - (self._markAt or 0) > 20 then self._markStep = 0 end  -- nouveau pack : on repart du crâne
  local pick
  for step = 1, #plan do
    local mk = plan[((self._markStep + step - 1) % #plan) + 1]
    if mk.icon ~= cur and not used[mk.icon] then pick = mk; self._markStep = self._markStep + step break end
  end
  pick = pick or plan[1]
  self._markAt = now()
  if G.SetRaidTarget then try(G.SetRaidTarget, "target", pick.icon) end
  self._lastMark = pick
  self:Refresh(true)
end

---------------------------------------------------------------------------------------------------
-- lignes de l'onglet « Donjon »
---------------------------------------------------------------------------------------------------
local function line(out, text, prio, extra)
  local l = extra or {}
  l.text, l.prio = text, prio or 1
  out[#out + 1] = l
  return l
end

local function bossTooltip(b)
  local tip = { b.n .. (b.en and b.en ~= b.n and (" (" .. b.en .. ")") or "") }
  if b.tip then tip[#tip + 1] = b.tip end
  if b.hunter and myClass() == "HUNTER" then tip[#tip + 1] = "Chasseur : " .. b.hunter end
  for _, a in ipairs(b.alerts or {}) do
    tip[#tip + 1] = (a.first or 0) > 0 and fmt("~%d s puis toutes les ~%d s : %s", a.first, a.every or 0, a.text) or a.text
  end
  if b.loot and #b.loot > 0 then
    tip[#tip + 1] = "Butin (indicatif) :"
    for _, it in ipairs(b.loot) do
      local name = M.ItemName(it[1], it[2])
      local who = {}
      for c in tostring(it[3]):gmatch("%u+") do who[#who + 1] = CLASS_FR[c] or c end
      tip[#tip + 1] = "  " .. name .. (it[3] == "ALL" and "" or (" — " .. tconcat(who, ", ")))
    end
  end
  tip[#tip + 1] = "(conseils indicatifs, d'après Classic)"
  return tconcat(tip, "\n")
end

function M:BuildLines()
  local out = {}
  local d, why = M.Active()
  if not d then return out end
  local D = M.Data()
  local inInst = why == "instance"
  local members = M.Members()
  local r = run()
  local hunter = myClass() == "HUNTER" and opt("hunter")

  -- en-tête
  line(out, fmt("%s (niv %d–%d)%s%s", d.n, d.min or 1, d.max or 60, d.new and " · nouveau Forever" or "",
    inInst and "" or (why == "auto" and " — prévu (quête de donjon)" or " — prévu")), 2,
    { tooltip = tconcat(d.tips or {}, "\n") .. "\n" .. tconcat(D.general or {}, "\n"),
      onClick = function() M:Report(d) end })

  -- boss en cours
  if enc and enc.d == d then
    local b = enc.b
    line(out, fmt("Boss : %s — %s", b.n, b.tip or "observez ses sorts"), 3, { tooltip = bossTooltip(b) })
    if hunter and b.hunter then line(out, "Chasseur : " .. b.hunter, 2) end
    local t = now() - enc.start
    for _, a in ipairs(b.alerts or {}) do
      if (a.first or 0) > 0 then
        local nextAt = a.first
        if a.every and t > a.first then nextAt = a.first + (floor((t - a.first) / a.every) + 1) * a.every end
        line(out, fmt("Prochain (≈) : %s dans %d s", a.text, max(0, floor(nextAt - t))), 2)
      end
    end
    if M.BlizzardTimeline() then line(out, "Frise de boss Blizzard active : nos minuteurs restent ici (pas de doublon).", 1) end
  end

  -- wipe : liste de reprise (5 min)
  if wipeAt and now() - wipeAt < 300 and opt("wipe") then
    local rez = {}
    for _, m in ipairs(members) do if REZ[m.class] then rez[#rez + 1] = m.me and "toi" or m.name end end
    line(out, "Wipe — reprise :" .. (#rez > 0 and (" rez possible par " .. tconcat(rez, ", ")) or " personne ne rez : course depuis le cimetière"), 3)
    for i, s in ipairs(D.wipe or {}) do if i > 1 or #rez > 0 then line(out, (i) .. ". " .. s, 2) end end
  end

  -- liste avant le donjon (hors combat, avant le 1er boss)
  if opt("checklist") and not inCombat() and not (r and (r.nk or 0) > 0 and inInst) then
    local low, high = {}, {}
    for _, m in ipairs(members) do
      if m.level > 0 and m.level < (d.min or 1) then low[#low + 1] = fmt("%s %d", m.me and "toi" or m.name, m.level) end
      if m.level > (d.max or 60) + 3 then high[#high + 1] = m.me and "toi" or m.name end
    end
    if #low > 0 then line(out, fmt("Trop bas (min %d) : %s", d.min or 1, tconcat(low, ", ")), 3) end
    if #high > 0 then line(out, "Trop haut (peu d'XP, butin gris) : " .. tconcat(high, ", "), 1) end
    if #low == 0 and #high == 0 then line(out, fmt("Niveaux OK pour %s (%d joueur%s).", d.n, #members, #members > 1 and "s" or ""), 1) end
    local tank, healer = M.Roles(members)
    local function who(m) return m and (m.me and "toi" or m.name) .. " (" .. (CLASS_FR[m.class] or "?") .. ")" or nil end
    if #members >= 2 then
      line(out, fmt("Tank : %s · Soigneur : %s%s", who(tank) or "PERSONNE", who(healer) or "PERSONNE",
        #members < 5 and fmt(" · %d/5", #members) or ""), (tank and healer) and 1 or 3,
        { tooltip = "Rôle assigné si posé, sinon deviné par la classe. Sans tank : le chasseur tanke au familier, pulls petits." })
    end
    -- consommables / durabilité (état Squad, frais hors instance)
    local warn = {}
    for _, m in ipairs(members) do
      local st = m.st
      if st then
        local nm = m.me and "toi" or m.name
        if (st.fo or 0) + (st.wa or 0) < 10 then warn[#warn + 1] = nm .. " : nourriture/eau " .. ((st.fo or 0) + (st.wa or 0)) end
        if st.du and st.du >= 0 and st.du < 50 then warn[#warn + 1] = nm .. " : durabilité " .. st.du .. " %" end
        if st.c == "HUNTER" and st.am and st.am >= 0 and st.am < 1000 then warn[#warn + 1] = nm .. " : munitions " .. st.am end
      end
    end
    if #warn > 0 then line(out, "À prévoir : " .. tconcat(warn, " · "), 2) end
    -- quêtes
    local qs = M.Quests(d)
    if #qs.toGet > 0 then
      local names = {}
      for i = 1, min(3, #qs.toGet) do names[#names + 1] = "« " .. qs.toGet[i].title .. " »" end
      local tip = {}
      for _, q in ipairs(qs.toGet) do tip[#tip + 1] = fmt("[%d] %s", q.level or 0, q.title) end
      line(out, fmt("Quêtes à prendre AVANT (%d) : %s", #qs.toGet, tconcat(names, ", ")), inInst and 1 or 2,
        { tooltip = tconcat(tip, "\n") .. "\n(QuestieDB ; donneurs dans Questie)" })
    end
    if #qs.inLog > 0 then
      local names = {}
      for _, q in ipairs(qs.inLog) do names[#names + 1] = q.title .. (q.done and " ✓" or "") end
      line(out, "Quêtes du donjon dans ton journal : " .. tconcat(names, ", "), 1)
    end
  end

  -- butin
  if opt("loot") then
    local mine, friends = M.Loot(d, members)
    if #mine > 0 then
      local parts = {}
      for i = 1, min(3, #mine) do
        local e = mine[i]
        parts[#parts + 1] = e.name .. (e.pct and e.pct < 900 and fmt(" +%d %%", floor(e.pct + 0.5)) or "") .. " (" .. e.boss .. ")" .. (e.killed and " ✓" or "")
      end
      line(out, "Amélioration pour toi : " .. tconcat(parts, ", "), 2)
    end
    local names = {}
    for n in pairs(friends) do names[#names + 1] = n end
    tsort(names)
    for _, n in ipairs(names) do
      local list, parts = friends[n], {}
      for i = 1, min(2, #list) do parts[#parts + 1] = list[i].name .. " (" .. list[i].boss .. ")" end
      line(out, fmt("Pour %s (%s) : %s%s", n, CLASS_FR[list[1].class] or "?", tconcat(parts, ", "), #list > 2 and fmt(" +%d", #list - 2) or ""), 1)
    end
  end

  -- boss (clic = conseil détaillé + minuteurs au prochain combat)
  for _, b in ipairs(d.bosses or {}) do
    local done = M.Killed(b)
    local cur = enc and enc.b == b
    if not done or not inInst or cur then
      line(out, fmt("%s %s%s", done and "✓" or "•", b.n, b.tip and (" — " .. b.tip) or ""), cur and 3 or 1,
        { tooltip = bossTooltip(b), onClick = function() M:Arm(b) end })
    end
  end

  -- chasseur
  if hunter then
    if d.hunter then line(out, "Chasseur : " .. d.hunter, 2) end
    local tips = D.hunter or {}
    if #tips > 0 then
      local i = (floor(now() / 30) % #tips) + 1
      line(out, "Chasseur : " .. tips[i], 1, { tooltip = tconcat(tips, "\n") })
    end
  end

  -- marque conseillée
  if opt("marks") and #members >= 2 then
    local plan = M.MarkPlan(members)
    local p = {}
    for _, mk in ipairs(plan) do p[#p + 1] = mk.text end
    line(out, "Marque conseillée : clique pour marquer ta cible" .. (self._lastMark and (" (dernière : " .. self._lastMark.name .. ")") or ""), 1,
      { tooltip = tconcat(p, "\n") .. "\n(un clic = une marque, hors combat)", onClick = function() M:MarkTarget() end })
  end

  -- pull / patrouille : repris de Fight (pas de doublon)
  local F = mod("Fight")
  local fl = F and type(F.Lines) == "table" and F.Lines.pull
  if fl and fl.text then line(out, fl.text, fl.prio or 1) end

  -- stats du run + mini-compteur
  if r and r.key == d.key then
    line(out, fmt("Run : %s · %d pull%s · %d boss · %d mort%s · %d wipe%s%s%s", dur(clock() - (r.start or clock())),
      r.pulls or 0, (r.pulls or 0) > 1 and "s" or "", r.nk or 0, r.deaths or 0, (r.deaths or 0) > 1 and "s" or "",
      r.wipes or 0, (r.wipes or 0) > 1 and "s" or "", (r.xp or 0) > 0 and (" · " .. short(r.xp) .. " XP") or "",
      (r.gold or 0) > 0 and (" · " .. money(r.gold)) or ""), 1)
  end
  if opt("meter") then
    local lm = meterText(self.lastMeter, "Dégâts, dernier combat")
    if lm then line(out, lm, 1, { tooltip = meterText(self.runMeter, "Dégâts, séance") }) end
    local it = self.interrupts
    if it and #it.rows > 0 then
      local p = {}
      for i = 1, min(3, #it.rows) do p[#p + 1] = (it.rows[i].me and "toi" or it.rows[i].name) .. " " .. it.rows[i].amount end
      line(out, "Interruptions : " .. tconcat(p, " · "), 1)
    end
  end
  return out
end

--- Clic sur un boss : conseil complet dans « Maintenant », minuteurs au prochain combat (sans évènement de boss).
function M:Arm(b)
  self._armed = b
  self:Suggest("armed", "Prêt pour " .. b.n .. " : " .. (b.tip or "pas de conseil connu") .. " (minuteurs au début du combat)",
    2, { ttl = 120, tab = TAB })
  self:Refresh(true)
end

---------------------------------------------------------------------------------------------------
-- onglet : présent seulement en instance / donjon prévu
---------------------------------------------------------------------------------------------------
function M:UpdateTab(lines)
  local show = opt("tab") and self:IsEnabled() and M.Active() ~= nil
  if show then
    if not (T.tabs and T.tabs[TAB]) then T:RegisterTab(TAB, "Donjon", 35) end
    if T.SetTabLines then T:SetTabLines(TAB, lines or {}, "Dungeon") end
  elseif T.tabs and T.tabs[TAB] then
    if T.SetTabLines then T:SetTabLines(TAB, {}, "Dungeon") end
    T.tabs[TAB] = nil
    if T.RequestRefresh then T:RequestRefresh() end
  end
  return show
end

local lastRefresh = 0
function M:Refresh(force)
  local t = now()
  if not force and t - lastRefresh < 0.5 then return end
  lastRefresh = t
  local ok, lines = pcall(self.BuildLines, self)
  if not ok then self:Debug("BuildLines", lines) lines = {} end
  self.lines = lines
  self:UpdateTab(lines)
end

---------------------------------------------------------------------------------------------------
-- évènements
---------------------------------------------------------------------------------------------------
function M:OnZone()
  local d = M.Current()
  if d then
    self:StartRun(d)
    if self.char and self.char.planned == d.key then self.char.planned = nil end  -- arrivé : le plan devient le run
  end
  self:Refresh(true)
end

function M:ENCOUNTER_START(_, encID, name)
  local d, b = M.FindBoss(encID, name)
  if not b then
    local cd = M.Current()
    if cd and sv(name) then b = { n = sv(name), dungeon = cd.key, index = 0 }; d = cd end
  end
  if d and b then self:StartEncounter(d, b, "event") end
end

function M:ENCOUNTER_END(_, encID, name, _, _, success)
  local d, b = M.FindBoss(encID, name)
  local ok = tonumber(sv(success))
  if ok == nil then ok = sv(success) and 1 or 0 end
  self:EndEncounter(ok == 1, d, b)
end

function M:BOSS_KILL(_, encID, name)
  if lastKill and now() - lastKill.t < 5 then return end
  local d, b = M.FindBoss(encID, name)
  if b then self:EndEncounter(true, d, b) end
end

--- Cris / émotes : souvent secrets en instance sur Forever -> ignorés ; sinon mots-clés du boss.
function M:OnYell(_, text, sender)
  if not opt("yells") then return end
  text, sender = sv(text), sv(sender)
  if type(text) ~= "string" then return end
  local d = M.Current() or (enc and enc.d)
  if not d then return end
  local s = norm(text) .. " " .. norm(sender or "")
  for _, b in ipairs(d.bosses or {}) do
    local hit = false
    for _, k in ipairs(b.yell or {}) do if s:find(norm(k), 1, true) then hit = true break end end
    if hit then
      if not enc then self:StartEncounter(d, b, "yell") end
      if b.yellText then self:Suggest("yell", b.yellText, opt("corner") and 3 or 2, { ttl = 6, tab = TAB, cooldown = 5 }) end
      return
    end
  end
end

function M:OnCombatStart()
  local r = run()
  local d = M.Current()
  if d and r and r.key == d.key then r.pulls = (r.pulls or 0) + 1; r.last = clock() end
  -- boss choisi au clic (pas d'évènement de rencontre) -> minuteurs dès le début du combat
  if not enc and self._armed and d then
    local b = self._armed
    if b.dungeon == d.key then self:StartEncounter(d, b, "click") end
  end
end

function M:OnCombatEnd()
  if enc and enc.src ~= "event" then
    -- rencontre sans évènement : fin de combat = fin (vivant -> victoire supposée)
    local dead = sv(try(G.UnitIsDeadOrGhost, "player"))
    self:EndEncounter(not dead, enc.d, enc.b)
    self._armed = nil
    self:Clear("armed")
  end
  T:After(0.5, function() M:UpdateMeter(); M:Refresh(true) end)
end

function M:PLAYER_DEAD()
  local r = run()
  if r and M.Current() then r.deaths = (r.deaths or 0) + 1 end
end

function M:PLAYER_XP_UPDATE()
  local xp = tonumber(sv(try(G.UnitXP, "player")))
  if not xp then return end
  local r = run()
  if r and M.Current() and self._lastXP and xp > self._lastXP then r.xp = (r.xp or 0) + (xp - self._lastXP) end
  self._lastXP = xp
end

function M:PLAYER_MONEY()
  local r = run()
  local m = tonumber((try(G.GetMoney)))
  if r and m and M.Current() then r.gold = max(0, m - (r.money0 or m)) end
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local d, why = M.Active()
  local r = run()
  s.dungeon = {
    key = d and d.key, name = d and d.n, why = why, boss = enc and enc.b and enc.b.n or nil,
    run = r and { key = r.key, time = clock() - (r.start or clock()), pulls = r.pulls, kills = r.nk, wipes = r.wipes,
      deaths = r.deaths, xp = r.xp, gold = r.gold } or nil,
    meter = self.lastMeter and { total = self.lastMeter.total, top = self.lastMeter.rows[1] and self.lastMeter.rows[1].name } or nil,
    runs = self.char and #(self.char.runs or {}) or 0,
  }
end

function M:Report(d)
  d = d or M.Active()
  if not d then self:Print("Aucun donjon prévu. /tai donjon <nom> (ex. : /tai donjon mortemines).") return end
  self:Print(fmt("%s (niv %d–%d) : %s", d.n, d.min or 1, d.max or 60, tconcat(d.tips or {}, " ")))
end

function M:OnEnable()
  M.Index()
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() M:OnZone() end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() M:OnZone() end)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() M:Refresh() end)
  self:RegisterEvent("ENCOUNTER_START")
  self:RegisterEvent("ENCOUNTER_END")
  self:RegisterEvent("BOSS_KILL")
  self:RegisterEvent("PLAYER_DEAD")
  self:RegisterEvent("PLAYER_XP_UPDATE")
  self:RegisterEvent("PLAYER_MONEY")
  for _, e in ipairs({ "CHAT_MSG_MONSTER_YELL", "CHAT_MSG_MONSTER_EMOTE", "RAID_BOSS_EMOTE", "CHAT_MSG_RAID_BOSS_EMOTE" }) do
    self:RegisterEvent(e, function(_, _, text, sender) M:OnYell(nil, text, sender) end)
  end
  self:RegisterEvent("DAMAGE_METER_COMBAT_SESSION_UPDATED", function() if not inCombat() then M:UpdateMeter() end end)
  self:On("COMBAT_START", function() M:OnCombatStart() end)
  self:On("COMBAT_END", function() M:OnCombatEnd() end)
  self:On("QUESTS_UPDATED", function() M:Refresh() end)
  self:On("SUMMARY", function() M:WriteSummary() end)
  if self._ticker then self._ticker:Cancel() end
  self._ticker = T:NewTicker(1, function()
    if not M:IsEnabled() then return end
    M:TickTimers()
    if enc or M.Active() or (T.tabs and T.tabs[TAB]) then M:Refresh() end
  end)
  self:OnZone()
end

function M:OnDisable()
  if self._ticker then self._ticker:Cancel(); self._ticker = nil end
  enc = nil
  self:UpdateTab({})
end

function M:OnOptionChanged() M:Refresh(true) end

T:RegisterSlash("donjon", function(arg)
  arg = type(arg) == "string" and arg:match("^%s*(.-)%s*$") or ""
  if arg == "" then M:Report() return end
  if arg == "liste" then
    local pl, out = level(), {}
    for _, d in ipairs(M.Index().list) do
      if pl >= (d.min or 1) - 3 and pl <= (d.max or 60) + 2 then out[#out + 1] = fmt("%s %d–%d", d.n, d.min or 1, d.max or 60) end
    end
    M:Print("Donjons à ton niveau : " .. (#out > 0 and tconcat(out, ", ") or "aucun"))
    return
  end
  if arg == "stop" then M.SetPlanned(nil) M:Print("Donjon prévu effacé.") return end
  if arg == "marque" then M:MarkTarget() return end
  local d = M.SetPlanned(arg)
  if d then M:Print("Donjon prévu : " .. d.n .. " — onglet « Donjon » ouvert.") else M:Print("Donjon inconnu : " .. arg .. " (/tai donjon liste)") end
end, "donjon prévu : /tai donjon <nom>|liste|stop|marque")
