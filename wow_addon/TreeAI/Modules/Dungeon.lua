-- TreeAI — module Dungeon (agents PVE + DUNGEON2) : aide de donjon, CONSEILS + UN CLIC, onglet « Donjon ».
-- Visible en instance, quand un donjon est prévu (choisi dans la fenêtre « Prêt ? », /tai donjon <nom>, ou auto :
-- quête de donjon dans le journal + groupe), ou 30 min après la sortie (bilan). Données : Data/Dungeons.lua
-- (id de rencontre + noms FR = tables client Forever ; conseils / pulls / qui-fait-quoi / butin = indicatifs ≈,
-- d'après Classic ; donjons propres à Forever : seulement ce qui est vérifiable — boss et ordre du client).
--   * avant : fenêtre « Prêt pour <donjon> ? » (Modules/DungeonPrep.lua) : niveaux vs tranche, rôles, quêtes du
--     donjon (QuestieDB lecture seule : dans ton journal / à prendre chez <PNJ>, qui n'a pas quoi, partage au
--     clic), réparation, sacs, familier, munitions, nourriture (Squad pour les potes qui ont TreeAI)
--   * pendant : boss dans l'ordre + « prochain boss » (mécanique en une ligne + qui fait quoi pour TA compo),
--     pulls connus (patrouilles, packs liés), butin par boss filtré sur le groupe (classe / armure / arme, gain
--     via Boost / Team, Besoin / Cupidité par membre), objets de quête à ramasser sur un boss, minuteurs,
--     reprise après wipe, chrono + boss tués, chasseur (familier tank ou pas, cibles à contrôler)
--   * après : bilan (durée, boss, morts, wipes, butin, quêtes finies, XP, or)
-- Forever : pas de COMBAT_LOG_EVENT pour les addons, valeurs secrètes en combat / en instance (noms, chat) ->
-- chaque valeur passe par sv() ; rien n'est fait à la place du joueur (marque / partage = un clic, hors combat).
-- Coût : événementiel ; minuterie 1 s seulement en instance (ou fenêtre « Prêt ? » ouverte), lignes reconstruites
-- sur changement (drapeau « sale ») ; index des quêtes Questie construit une fois, par tranches, puis mémorisé.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local G = _G

local M = T:RegisterModule("Dungeon", {
  title = "Donjon (PvE)",
  defaults = {
    enabled = true, tab = true, autoPlan = true, checklist = true, quests = true, loot = true,
    timers = true, corner = true, yells = true, meter = true, wipe = true, hunter = true, marks = true,
    nextBoss = true, pulls = true, summary = true,
    forceTimers = false, learned = {}, qidx = nil,
  },
  charDefaults = { planned = nil, run = nil, runs = {}, last = nil },
  options = {
    { key = "tab", type = "toggle", label = "Onglet « Donjon » (en instance, donjon prévu ou bilan)", section = "Donjon" },
    { key = "autoPlan", type = "toggle", label = "Prévoir le donjon tout seul (quête de donjon + groupe)" },
    { key = "checklist", type = "toggle", label = "Préparation : niveaux, rôles, réparation, sacs, consommables", section = "Avant" },
    { key = "quests", type = "toggle", label = "Quêtes du donjon (QuestieDB) : à prendre, qui n'a pas quoi" },
    { key = "loot", type = "toggle", label = "Butin du groupe : Besoin / Cupidité par membre" },
    { key = "nextBoss", type = "toggle", label = "Prochain boss : mécanique + qui fait quoi", section = "Boss" },
    { key = "pulls", type = "toggle", label = "Pulls à surveiller (patrouilles, packs liés)" },
    { key = "timers", type = "toggle", label = "Minuteurs de boss (indicatifs)" },
    { key = "corner", type = "toggle", label = "Encadré d'alerte pour les minuteurs importants" },
    { key = "forceTimers", type = "toggle", label = "Encadré même si la frise de boss Blizzard est active" },
    { key = "yells", type = "toggle", label = "Cris / émotes de boss (quand le jeu les laisse lire)" },
    { key = "summary", type = "toggle", label = "Bilan à la sortie du donjon", section = "Après" },
    { key = "meter", type = "toggle", label = "Mini-compteur (compteur de dégâts Blizzard, hors combat)" },
    { key = "wipe", type = "toggle", label = "Liste de reprise après un wipe" },
    { key = "hunter", type = "toggle", label = "Chasseur : familier tank ou pas, cibles à contrôler", section = "Chasseur" },
    { key = "marks", type = "toggle", label = "Bouton « Marquer » (un clic = une marque, hors combat)" },
  },
})
T.Dungeon = M
local TAB = "dungeon"
M.TAB = TAB

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
M.try, M.sv = try, sv

local ACCENTS = { ["é"] = "e", ["è"] = "e", ["ê"] = "e", ["ë"] = "e", ["à"] = "a", ["â"] = "a", ["î"] = "i",
  ["ï"] = "i", ["ô"] = "o", ["û"] = "u", ["ù"] = "u", ["ç"] = "c", ["œ"] = "oe", ["É"] = "e", ["’"] = "'" }
local normCache, normN = {}, 0
local function norm(s)
  if type(s) ~= "string" then return "" end
  local c = normCache[s]
  if c then return c end
  local r = s:lower()
  if r:find("[\128-\255]") then for a, b in pairs(ACCENTS) do r = r:gsub(a, b) end end
  normN = normN + 1
  if normN > 2000 then normCache, normN = {}, 0 end
  normCache[s] = r
  return r
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
M.Dur, M.Money = dur, money

local CLASS_FR = { HUNTER = "Chasseur", ROGUE = "Voleur", WARRIOR = "Guerrier", PALADIN = "Paladin",
  SHAMAN = "Chaman", DRUID = "Druide", MAGE = "Mage", WARLOCK = "Démoniste", PRIEST = "Prêtre" }
local TANKS = { WARRIOR = true, PALADIN = true, DRUID = true }
local HEALERS = { PRIEST = true, DRUID = true, SHAMAN = true, PALADIN = true }
local REZ = { PRIEST = true, PALADIN = true, SHAMAN = true, DRUID = true }
M.CLASS_FR = CLASS_FR
local ICON = "Interface\\Icons\\"
M.ICONS = {
  dungeon = ICON .. "INV_Misc_Key_03", boss = ICON .. "Achievement_Boss_Generic", skull = "Interface\\TargetingFrame\\UI-RaidTargetingIcon_8",
  loot = ICON .. "INV_Misc_Bag_10", quest = "Interface\\GossipFrame\\AvailableQuestIcon", turnin = "Interface\\GossipFrame\\ActiveQuestIcon",
  prep = ICON .. "INV_Misc_Note_01", hunter = ICON .. "Ability_Hunter_Pet_Wolf", warn = "Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew",
  pull = ICON .. "Ability_Hunter_SniperShot", summary = ICON .. "INV_Misc_PocketWatch_01",
}
local QICON = "|T" .. M.ICONS.quest .. ":0|t"

---------------------------------------------------------------------------------------------------
-- données : index par carte, rencontre, nom ; fusion des ajouts DUNGEON2 (pulls, cc, jobs, opt)
---------------------------------------------------------------------------------------------------
local index
function M.Index()
  if index then return index end
  index = { byMap = {}, byEnc = {}, byName = {}, list = {}, byArea = {} }
  local D = T.Data and T.Data.Dungeons
  local extra = D and D.extra or {}
  for _, d in ipairs(D and D.list or {}) do
    index.list[#index.list + 1] = d
    if d.map then index.byMap[d.map] = d end
    if d.area then index.byArea[d.area] = d end
    local x = extra[d.key]
    if type(x) == "table" then
      d.pulls = d.pulls or x.pulls
      d.cc = d.cc or x.cc
    end
    for bi, b in ipairs(d.bosses or {}) do
      b.index, b.dungeon = bi, d.key
      if type(x) == "table" then
        if x.jobs and x.jobs[bi] and not b.jobs then b.jobs = x.jobs[bi] end
        if x.opt and x.opt[bi] then b.opt = true end
      end
      for _, id in ipairs(b.ids or {}) do index.byEnc[id] = { d = d, b = b } end
      if b.n then index.byName[norm(b.n)] = { d = d, b = b } end
      if b.en then index.byName[norm(b.en)] = { d = d, b = b } end
    end
  end
  return index
end
function M.Data() return T.Data and T.Data.Dungeons or {} end

--- Donjon par clé, nom ou mot-clé (FR/EN, accents ignorés).
local findCache = {}
local function find(q)
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
function M.Find(q)
  if type(q) == "table" then return q end
  if q == nil then return nil end
  local c = findCache[q]
  if c == nil then c = find(q) or false; findCache[q] = c end
  return c or nil
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

--- Donjons à portée d'un niveau (tri par niveau minimum)
function M.ForLevel(pl)
  pl = pl or level()
  local out = {}
  for _, d in ipairs(M.Index().list) do
    if pl >= (d.min or 1) - 3 and pl <= (d.max or 60) + 2 then out[#out + 1] = d end
  end
  return out
end

---------------------------------------------------------------------------------------------------
-- instance / donjon actif (mis en cache : recalculé sur évènement, jamais à chaque seconde)
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

local activeCache   -- { d, why } | false (rien) | nil (à recalculer)
function M.Active(force)
  if activeCache == nil or force then
    local d = M.Current()
    if d then activeCache = { d, "instance" }
    else
      local p, why = M.Planned()
      activeCache = p and { p, why } or false
    end
  end
  if activeCache then return activeCache[1], activeCache[2] end
  return nil
end
function M.Invalidate() activeCache = nil; M._dirty = true; M._questGen = (M._questGen or 0) + 1 end
local function inInstanceNow() return activeCache and activeCache[2] == "instance" or false end

function M.SetPlanned(q)
  if not M.char then return nil end
  if q == nil or q == "" or q == "stop" then M.char.planned = nil; M.Invalidate(); M:Refresh(true) return nil end
  local d = M.Find(q)
  if d then M.char.planned = d.key end
  M.Invalidate()
  M:Refresh(true)
  return d
end

---------------------------------------------------------------------------------------------------
-- groupe : membres (unités + états Squad + équipement Team), rôles
---------------------------------------------------------------------------------------------------
local membersCache, membersAt = nil, -1e9
function M.Members(force)
  if membersCache and not force and now() - membersAt < 2 then return membersCache end
  local list = {}
  local _, cls = try(G.UnitClass, "player")
  list[1] = { unit = "player", name = sv(try(G.UnitName, "player")) or "toi", class = sv(cls) or myClass(), level = level(), me = true }
  for i = 1, 4 do
    local u = "party" .. i
    if sv(try(G.UnitExists, u)) then
      local _, c = try(G.UnitClass, u)
      local role = sv(try(G.UnitGroupRolesAssigned, u))
      list[#list + 1] = { unit = u, name = sv(try(G.UnitName, u)) or u, class = sv(c), level = level(u),
        role = (role and role ~= "NONE") and role or nil, guid = sv(try(G.UnitGUID, u)) }
    end
  end
  local myRole = sv(try(G.UnitGroupRolesAssigned, "player"))
  if myRole and myRole ~= "NONE" then list[1].role = myRole end
  -- état Squad (nourriture, eau, munitions, durabilité, sacs, quêtes) quand le pote a TreeAI
  local S = T.Squad
  if type(S) == "table" and type(S.MemberList) == "function" then
    local ok, ml = pcall(S.MemberList, S)
    if ok and type(ml) == "table" then
      local byName = {}
      for _, e in ipairs(ml) do if type(e) == "table" and e.name then byName[e.name] = e end end
      for i, m in ipairs(list) do
        local e = byName[m.name] or (i == 1 and ml[1]) or nil
        m.st = e and e.st or nil
        m.quests = e and e.quests or nil
      end
    end
  end
  membersCache, membersAt = list, now()
  return list
end

--- Rôles : rôle assigné, sinon devinés par la classe (tank : guerrier/paladin/druide ; soins : prêtre…).
function M.Roles(members)
  local tank, healer
  for _, m in ipairs(members) do if m.role == "TANK" then tank = m end if m.role == "HEALER" then healer = m end end
  if not healer then for _, m in ipairs(members) do if m.class == "PRIEST" and m ~= tank then healer = m break end end end
  if not healer then for _, m in ipairs(members) do if HEALERS[m.class] and m ~= tank then healer = m break end end end
  if not tank then for _, m in ipairs(members) do if m.class == "WARRIOR" and m ~= healer then tank = m break end end end
  if not tank then for _, m in ipairs(members) do if TANKS[m.class] and m ~= healer then tank = m break end end end
  return tank, healer
end
local function who(m) return m and (m.me and "toi" or m.name) or nil end
M.Who = who

---------------------------------------------------------------------------------------------------
-- qui fait quoi (compo du groupe × besoins du boss) — codes de Data/Dungeons.lua `jobs`
---------------------------------------------------------------------------------------------------
--- -> { {job, text, m = membre|nil, spell} } pour un boss (ou une liste de codes « int cc »)
function M.Jobs(b, members)
  local J = M.Data().jobs or {}
  local out, used = {}, {}
  local codes = type(b) == "table" and b.jobs or b
  if type(codes) ~= "string" then return out end
  local tank, healer = M.Roles(members)
  for code in codes:gmatch("%a+") do
    local j = J[code]
    if j then
      local pick, spell
      for round = 1, 2 do           -- d'abord quelqu'un de libre (pas tank / soigneur / déjà pris), sinon n'importe qui
        for _, w in ipairs(j.who or {}) do
          for _, m in ipairs(members) do
            if not pick and m.class == w[1] and (m.level or 0) >= (w[3] or 1)
                and (round == 2 or (m ~= tank and m ~= healer and not used[m])) then
              pick, spell = m, w[2]
            end
          end
        end
        if pick then break end
      end
      if pick then used[pick] = true end
      out[#out + 1] = { job = code, text = j.text, m = pick, spell = spell }
    end
  end
  return out
end

--- Ligne « qui fait quoi » : tank, soins, puis chaque besoin du boss attribué à un membre (ou « personne »)
function M.JobsText(b, members, short)
  local tank, healer = M.Roles(members)
  local parts = {}
  if #members >= 2 then
    parts[#parts + 1] = tank and (who(tank) .. " tanke") or "pas de tank : le familier tanke"
    if healer then parts[#parts + 1] = who(healer) .. " soigne" end
  end
  local missing = {}
  for _, j in ipairs(M.Jobs(b, members)) do
    if j.m then parts[#parts + 1] = short and fmt("%s %s", who(j.m), j.text) or fmt("%s %s (%s)", who(j.m), j.text, j.spell)
    else missing[#missing + 1] = j.text end
  end
  for _, t in ipairs(missing) do
    parts[#parts + 1] = (t:find("^[aeiouéèêh]") and "personne n'" or "personne ne ") .. t
  end
  local s = tconcat(parts, " · ")
  return s ~= "" and s or nil
end

---------------------------------------------------------------------------------------------------
-- QuestieDB (lecture seule) : quêtes, PNJ, objets ; index « zone -> quêtes » construit une fois par tranches
---------------------------------------------------------------------------------------------------
local function questie()
  local lib = rawget(G, "LibQuestieDB")
  if type(lib) == "table" and type(lib.Quest) == "table" and type(lib.Quest.Get) == "function" then
    local Q, N, I = lib.Quest, lib.Npc, lib.Item
    return {
      quest = function(id, key) return try(Q.Get, id, key) end,
      npc = function(id, key) return type(N) == "table" and try(N.Get, id, key) or nil end,
      item = function(id, key) return type(I) == "table" and try(I.Get, id, key) or nil end,
      all = function() local ids = try(Q.GetAllIds, false) return type(ids) == "table" and ids or {} end,
    }
  end
  local L = rawget(G, "QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB")
  if type(db) == "table" and type(db.QueryQuestSingle) == "function" then
    return {
      quest = function(id, key) return try(db.QueryQuestSingle, id, key) end,
      npc = function(id, key) return try(db.QueryNPCSingle, id, key) end,
      item = function(id, key) return try(db.QueryItemSingle, id, key) end,
      all = function()
        local out = {}
        for k, v in pairs(type(db.QuestPointers) == "table" and db.QuestPointers or {}) do
          out[#out + 1] = type(k) == "number" and k or tonumber(v)
        end
        return out
      end,
    }
  end
  return nil
end
M.Questie = questie

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
  local f = rawget(G, "IsQuestFlaggedCompleted")
  if type(f) == "function" then return sv(try(f, id)) and true or false end
  return false
end

-- index : { sig = nombre d'ids, areas = { [zoneOrSort] = { ids } } } (compte, SavedVariables) ; scan par tranches
local scan        -- { ids, i, areas, want }
local SCAN_STEP = 1500
function M.QuestIndex()
  if not questie() then return nil end
  local q = M.db and M.db.qidx
  if type(q) == "table" and q.done and type(q.areas) == "table" then return q end
  return nil
end
function M.ScanStep(n)
  if inCombat() then return false end
  local db = questie()
  if not db or not M.db then return true end
  if not scan then
    local ids = db.all()
    local q = M.db.qidx
    if type(q) == "table" and q.done and q.sig == #ids then return true end
    local want = {}
    for a in pairs(M.Index().byArea) do want[a] = true end
    scan = { ids = ids, i = 1, areas = {}, want = want }
  end
  local ids, want, areas = scan.ids, scan.want, scan.areas
  local stop = min(#ids, scan.i + (n or SCAN_STEP) - 1)
  for k = scan.i, stop do
    local id = ids[k]
    if type(id) == "number" then
      local z = tonumber(db.quest(id, "zoneOrSort"))
      if z and want[z] then areas[z] = areas[z] or {}; tinsert(areas[z], id) end
    end
  end
  scan.i = stop + 1
  if scan.i > #ids then
    M.db.qidx = { sig = #ids, areas = areas, done = true }
    scan = nil
    M._dirty = true
    M._questGen = (M._questGen or 0) + 1
    return true
  end
  return false
end
--- Lance (ou poursuit) le scan en tâche de fond, seulement quand on en a besoin.
function M:EnsureQuestIndex()
  if not opt("quests") or M._scanning or M.QuestIndex() or not questie() then return end
  M._scanning = true
  local function step()
    if M.ScanStep() then M._scanning = false; M:Refresh(true) return end
    T:After(inCombat() and 3 or 0.25, step)
  end
  step()
end

local function npcLabel(db, npcID)
  local name = sv(db.npc(npcID, "name"))
  if type(name) ~= "string" then return nil end
  local zone = tonumber(db.npc(npcID, "zoneID"))
  local zn
  if zone and G.C_Map and G.C_Map.GetAreaInfo then zn = sv(try(G.C_Map.GetAreaInfo, zone)) end
  return name, zn
end

--- PNJ / objet qui donne la quête -> « Nom (zone) » ou nil
local giverCache = {}
function M.Giver(id)
  if giverCache[id] ~= nil then return giverCache[id] or nil end
  local db = questie()
  local res = false
  local sb = db and db.quest(id, "startedBy")
  if type(sb) == "table" then
    for _, npc in ipairs(type(sb[1]) == "table" and sb[1] or {}) do
      local name, zn = npcLabel(db, npc)
      if name then res = zn and (name .. " (" .. zn .. ")") or name break end
    end
    if not res and type(sb[3]) == "table" and sb[3][1] then
      local iname = sv(db.item(sb[3][1], "name"))
      if type(iname) == "string" then res = "objet « " .. iname .. " »" end
    end
  end
  giverCache[id] = res
  return res or nil
end

--- Objectifs d'une quête rattachés à un boss du donjon (Questie : objets -> npcDrops, monstres) -> { [nom normalisé] = boss }
local dropCache = {}
local function objectiveBosses(qid, d)
  local key = qid .. ":" .. d.key
  if dropCache[key] then return dropCache[key] end
  local res = {}
  local db = questie()
  local objs = db and db.quest(qid, "objectives")
  local I = M.Index()
  if type(objs) == "table" then
    for _, it in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
      local itemID = type(it) == "table" and it[1] or it
      if type(itemID) == "number" then
        local iname = sv(db.item(itemID, "name"))
        for n, npc in ipairs(db.item(itemID, "npcDrops") or {}) do
          if n > 30 then break end
          local nn = sv(db.npc(npc, "name"))
          local e = type(nn) == "string" and I.byName[norm(nn)]
          if e and e.d == d then
            if iname then res[norm(iname)] = e.b end
            break
          end
        end
      end
    end
    for _, mob in ipairs(type(objs[1]) == "table" and objs[1] or {}) do
      local npc = type(mob) == "table" and mob[1] or mob
      local nn = type(npc) == "number" and sv(db.npc(npc, "name"))
      local e = type(nn) == "string" and I.byName[norm(nn)]
      if e and e.d == d then res[norm(nn)] = e.b end
    end
  end
  dropCache[key] = res
  return res
end

local function bossForObjective(o, map, d)
  local nm = norm(o.name or o.text or "")
  if map[nm] then return map[nm] end
  for k, b in pairs(map) do if nm:find(k, 1, true) then return b end end
  for _, b in ipairs(d.bosses or {}) do
    local bn, be = norm(b.n), norm(b.en or "")
    if (bn ~= "" and nm:find(bn, 1, true)) or (be ~= "" and nm:find(be, 1, true)) then return b end
  end
  return nil
end

-- quête sur une unité du groupe : vrai / faux / nil (inconnu)
local function unitOnQuest(u, q)
  local CQ = G.C_QuestLog
  local f = CQ and CQ.IsUnitOnQuest
  if f and q.id and q.id < 100000 then
    local ok, r = pcall(f, u, q.id)
    if not ok then ok, r = pcall(f, q.id, u) end
    if ok and r ~= nil then return sv(r) and true or false end
  end
  local g = rawget(G, "IsUnitOnQuest")
  if type(g) == "function" and q.index then
    local r = sv(try(g, q.index, u))
    if r == nil or r == false or r == 0 then return false end
    return true
  end
  return nil
end
local function pushable(q)
  local CQ = G.C_QuestLog
  if CQ and CQ.IsPushableQuest and q.id and q.id < 100000 then
    local r = sv(try(CQ.IsPushableQuest, q.id))
    if r == false then return false end
  end
  return true
end

--- Quêtes du donjon :
--  inLog = { {id, index, title, done, obj = { {name, have, need, boss} }, missing = {noms}, pushable} }
--  toGet = { {id, title, level, req, giver, holders = {noms (Squad)}} } (≤ 8), pending = index en cours
local questsCache = {}
function M.Quests(d, members)
  if not d then return { inLog = {}, toGet = {} } end
  local c = questsCache
  if c.d == d and c.gen == M._questGen and now() - c.t < 10 then return c.res end
  local gen = M._questGen
  local res = M.QuestsNow(d, members)
  if not res.pending then questsCache = { d = d, gen = gen, t = now(), res = res } end
  return res
end
function M.QuestsNow(d, members)
  local res = { inLog = {}, toGet = {} }
  members = members or M.Members()
  local Q = mod("Quests")
  local logIds = {}
  local ix = M.QuestIndex()
  local areaIds = {}
  for _, id in ipairs(ix and d.area and ix.areas[d.area] or {}) do areaIds[id] = true end
  for _, q in ipairs(Q and Q.lastList or {}) do
    if type(q) == "table" and q.id then
      logIds[q.id] = q
      if areaIds[q.id] or M.Find(q.zone) == d or M.Find(q.header) == d then
        local map = objectiveBosses(q.id, d)
        local e = { id = q.id, index = q.index, title = q.title or ("#" .. q.id), done = q.complete, obj = {}, missing = {} }
        for _, o in ipairs(q.obj or {}) do
          if not o.finished then
            e.obj[#e.obj + 1] = { name = o.name or o.text, have = o.have or 0, need = o.need or 1, boss = bossForObjective(o, map, d) }
          end
        end
        for _, m in ipairs(members) do
          if not m.me and m.unit and unitOnQuest(m.unit, e) == false then e.missing[#e.missing + 1] = m.name end
        end
        e.pushable = pushable(e)
        res.inLog[#res.inLog + 1] = e
      end
    end
  end
  if not (d.area and opt("quests") and questie()) then return res end
  if not ix then res.pending = true; M:EnsureQuestIndex() return res end
  local db = questie()
  local pl, done = level(), completed()
  local list = {}
  for id in pairs(areaIds) do
    if not logIds[id] and not isDone(id, done) then
      local rl = tonumber(db.quest(id, "requiredLevel")) or 0
      local ql = tonumber(db.quest(id, "questLevel")) or rl
      if rl <= pl + 2 and ql <= (d.max or 60) + 5 then
        local e = { id = id, title = sv(db.quest(id, "name")) or ("#" .. id), level = ql, req = rl, holders = {} }
        for _, m in ipairs(members) do
          if not m.me and type(m.quests) == "table" and m.quests[id] then e.holders[#e.holders + 1] = m.name end
        end
        list[#list + 1] = e
      end
    end
  end
  tsort(list, function(a, b) if a.level ~= b.level then return a.level < b.level end return a.id < b.id end)
  for i = 1, min(8, #list) do list[i].giver = M.Giver(list[i].id); res.toGet[i] = list[i] end
  return res
end

--- Partage des quêtes du donjon à ceux qui ne les ont pas (clic, hors combat ; jamais la sélection du journal)
function M:ShareQuests(only)
  if inCombat() then self:Print("Partage après le combat.") return 0 end
  local d = M.Active()
  if not d then return 0 end
  local push = rawget(G, "QuestLogPushQuest")
  if type(push) ~= "function" then return 0 end
  local list = {}
  for _, q in ipairs(M.Quests(d).inLog) do
    if q.index and q.pushable ~= false and not q.done and (only == nil or only == q.id) and (#q.missing > 0 or only) then list[#list + 1] = q end
  end
  local k = 0
  local function step()
    k = k + 1
    local q = list[k]
    if not q or inCombat() then return end
    try(push, q.index)
    T:After(0.6, step)
  end
  step()
  return #list
end

---------------------------------------------------------------------------------------------------
-- butin : pour qui (classe / armure / arme / niveau), gain (Boost : toi ; Team : potes inspectés), Besoin / Cupidité
---------------------------------------------------------------------------------------------------
local function classSet(s)
  local t = {}
  if s == "ALL" then for k in pairs(CLASS_FR) do t[k] = true end return t end
  for w in tostring(s or ""):gmatch("%u+") do t[w] = true end
  return t
end
M.ClassSet = classSet

local requested = {}
local function itemInfo(id)
  local A = T.API
  local name, link, req, equipLoc, classID, subID, quality, _
  if A and A.ItemInfo then name, link, quality, _, req, _, _, _, equipLoc, _, _, classID, subID = A.ItemInfo(id)
  else name, link, quality, _, req, _, _, _, equipLoc, _, _, classID, subID = try(G.GetItemInfo, id) end
  if not name and type(id) == "number" and not requested[id] then
    requested[id] = true
    local CI = G.C_Item
    if CI and CI.RequestLoadItemDataByID then try(CI.RequestLoadItemDataByID, id) end
  end
  return sv(name), sv(link), tonumber(sv(req)), sv(equipLoc), tonumber(sv(classID)), tonumber(sv(subID)), tonumber(sv(quality))
end
function M.ItemName(id, fallback)
  local name, link = itemInfo(id)
  return name or fallback or ("objet " .. tostring(id)), link
end

-- (4.0) moteur d'équipement : Gear.Engine (poids, armures / armes par classe, score) — l'ancien Boost n'en a plus
local function boost() local Gm = T.Gear return type(Gm) == "table" and Gm.Engine or nil end
-- peut porter / manier ? -> ok, plus tard (niveau)
local function usableBy(m, info, set)
  local B = boost()
  if info and B then
    if info.classID == 4 and info.subID and info.subID > 0 and info.equipLoc ~= "INVTYPE_CLOAK" then
      local need = ((B.ARMOR_LEVEL or {})[m.class] or {})[info.subID]
      if not need then return false end
      if (m.level or 0) < need then return false, need end
    elseif info.classID == 2 and info.subID then
      if not ((B.WEAPON_TYPES or {})[m.class] or {})[info.subID] then return false end
    end
    if info.req and info.req > (m.level or 0) then return false, info.req end
    return true
  end
  return set[m.class] and true or false
end

local function teamGear(m, slot)
  local Tm = mod("Team")
  if type(Tm) == "table" and type(Tm.GearOf) == "function" and m.guid then
    local c = try(Tm.GearOf, m.guid)
    return type(c) == "table" and type(c.links) == "table" and c.links[slot] or nil
  end
end

-- gain en % pour un membre (nil = inconnu)
local function gainFor(m, link, equipLoc)
  local B = boost()
  if not (B and link) then return nil end
  if m.me then return B.UpgradePct and try(B.UpgradePct, link) or nil end
  local slots = B.EQUIP_SLOTS and equipLoc and B.EQUIP_SLOTS[equipLoc]
  if not slots or not (B.ScoreLink and B.WEIGHTS) then return nil end
  local cw = B.WEIGHTS[m.class]
  local w = cw and cw.default
  if not w then return nil end
  local new = try(B.ScoreLink, link, slots[1], w)
  local cur = teamGear(m, slots[1])
  if not new or not cur then return nil end
  local old = try(B.ScoreLink, cur, slots[1], w) or 0
  if old <= 0 then return new > 0 and 999 or 0 end
  return (new - old) / old * 100
end

--- Analyse d'un objet pour le groupe -> { id, name, link, boss, need = { {m, gain} }, later = { {m, lvl} },
--- verdict = { [nom] = "Besoin" | "Cupidité" }, approx = données Classic }
function M.LootItem(it, b, members)
  local name, link, req, equipLoc, classID, subID, quality = itemInfo(it[1])
  local info = (equipLoc and equipLoc ~= "") and { equipLoc = equipLoc, classID = classID, subID = subID, req = req } or nil
  local set = classSet(it[3])
  local e = { id = it[1], name = name or it[2] or ("objet " .. it[1]), link = link, boss = b, need = {}, later = {},
    verdict = {}, approx = true, quality = quality }
  for _, m in ipairs(members) do
    local ok, lvl = usableBy(m, info, set)
    local wanted = set[m.class]
    if ok and wanted then
      local g = gainFor(m, link, equipLoc)
      if g == nil or g > 0 then e.need[#e.need + 1] = { m = m, gain = g } end
    elseif wanted and lvl then
      e.later[#e.later + 1] = { m = m, lvl = lvl }
    end
  end
  tsort(e.need, function(a, c)
    local ga, gc = a.gain or 0.5, c.gain or 0.5
    if ga ~= gc then return ga > gc end
    return (a.m.me and 0 or 1) < (c.m.me and 0 or 1)
  end)
  local needers = {}
  for _, n in ipairs(e.need) do needers[n.m] = true end
  for _, m in ipairs(members) do e.verdict[m.name] = needers[m] and "Besoin" or "Cupidité" end
  return e
end

local function gainTxt(g) if not g then return "" end if g >= 900 then return " (vide)" end return fmt(" +%d %%", floor(g + 0.5)) end

--- Texte « Besoin : toi +12 %, Ami (vide) · Cupidité : les autres »
function M.LootVerdictText(e, members)
  if #e.need == 0 then return "Cupidité pour tous" end
  local p = {}
  for _, n in ipairs(e.need) do p[#p + 1] = who(n.m) .. gainTxt(n.gain) end
  local s = "Besoin : " .. tconcat(p, ", ")
  if #e.need < #members then s = s .. " · Cupidité : " .. (#members - #e.need == 1 and "l'autre" or "les autres") end
  return s
end

--- Butin par boss pour le groupe : liste de LootItem (mis en cache : groupe, équipement, objets reçus du serveur)
local lootCache = { sig = nil }
function M.LootPlan(d, members)
  if not d then return {} end
  local sig = d.key
  for _, m in ipairs(members) do sig = sig .. "|" .. tostring(m.name) .. tostring(m.level) end
  sig = sig .. "|" .. tostring(M._itemGen or 0) .. "|" .. tostring(M._equipGen or 0)
  if lootCache.sig == sig then return lootCache.list end
  local list = {}
  for _, b in ipairs(d.bosses or {}) do
    for _, it in ipairs(b.loot or {}) do list[#list + 1] = M.LootItem(it, b, members) end
  end
  lootCache = { sig = sig, list = list }
  return list
end

--- Ancien contrat : mine = { {boss, id, name, pct, killed} }, friends = { [nom] = { {boss, id, name, class} } }
function M.Loot(d, members)
  local mine, friends = {}, {}
  members = members or M.Members()
  for _, e in ipairs(M.LootPlan(d, members)) do
    for _, n in ipairs(e.need) do
      if n.m.me then mine[#mine + 1] = { boss = e.boss.n, id = e.id, name = e.name, pct = n.gain, killed = M.Killed(e.boss) }
      else
        friends[n.m.name] = friends[n.m.name] or {}
        tinsert(friends[n.m.name], { boss = e.boss.n, id = e.id, name = e.name, class = n.m.class })
      end
    end
  end
  tsort(mine, function(a, b) return (a.pct or 1) > (b.pct or 1) end)
  return mine, friends
end

--- Contrat Gear (`Dungeon.ItemSource(id)`) : objet d'un boss connu -> { text, min }
local itemBoss
function M.ItemSource(id)
  id = tonumber(id)
  if not id then return nil end
  if not itemBoss then
    itemBoss = {}
    for _, d in ipairs(M.Index().list) do
      for _, b in ipairs(d.bosses or {}) do
        for _, it in ipairs(b.loot or {}) do itemBoss[it[1]] = { d = d, b = b } end
      end
    end
  end
  local e = itemBoss[id]
  if not e then return nil end
  return { text = fmt("butin de %s (%s, ≈)", e.b.n, e.d.n), min = 60 + 5 * e.b.index }
end

---------------------------------------------------------------------------------------------------
-- run : pulls, morts, wipes, boss vaincus, butin, quêtes, XP, or, durée ; bilan à la sortie
---------------------------------------------------------------------------------------------------
local function run() return M.char and M.char.run end
M.Run = run
function M.Killed(b)
  local r = run()
  return r and b and r.kills and r.kills[b.dungeon .. ":" .. b.index] and true or false
end

--- Prochain boss (ordre habituel, sans les optionnels) ; -> boss, n tués, n total (sans optionnels)
function M.NextBoss(d)
  if not d then return nil, 0, 0 end
  local nxt, killed, total = nil, 0, 0
  for _, b in ipairs(d.bosses or {}) do
    if not b.opt then
      total = total + 1
      if M.Killed(b) then killed = killed + 1 elseif not nxt then nxt = b end
    end
  end
  return nxt, killed, total
end

local function completeSet(d)
  local s = {}
  local Q = mod("Quests")
  for _, q in ipairs(Q and Q.lastList or {}) do
    if type(q) == "table" and q.id and M.Find(q.zone) == d then
      local CQ = G.C_QuestLog
      local done = q.complete
      if CQ and CQ.IsComplete and q.id < 100000 then done = sv(try(CQ.IsComplete, q.id)) or done end
      if done then s[q.id] = q.title or true end
    end
  end
  return s
end

function M:StartRun(d)
  if not (d and self.char) then return end
  local r = self.char.run
  if r and r.key == d.key and clock() - (r.last or 0) < 3600 then r.last = clock() return r end
  if r and r.key then self:ArchiveRun() end
  r = { key = d.key, start = clock(), last = clock(), pulls = 0, deaths = 0, pdeaths = 0, wipes = 0, kills = {}, nk = 0,
    xp = 0, money0 = tonumber((try(G.GetMoney))) or 0, gold = 0, loot = {}, quests = {}, complete0 = completeSet(d) }
  self.char.run = r
  self._lastXP = tonumber(sv(try(G.UnitXP, "player"))) or 0
  return r
end

--- Bilan d'un run -> table (aussi sauvée dans char.last pour l'onglet et l'appli)
function M.Summarize(r)
  if not r then return nil end
  local d = M.Find(r.key)
  local _, _, total = M.NextBoss(d)
  local quests, seen = {}, {}
  for id, t in pairs(r.quests or {}) do quests[#quests + 1] = t; seen[id] = true end
  -- quêtes du donjon devenues « finies » pendant le run
  for id, t in pairs(d and completeSet(d) or {}) do
    if not (r.complete0 or {})[id] and not seen[id] then quests[#quests + 1] = type(t) == "string" and t or ("#" .. id) end
  end
  tsort(quests)
  return { key = r.key, name = d and d.n or r.key, time = (r.last or r.start) - r.start, kills = r.nk or 0, total = total,
    deaths = r.deaths or 0, pdeaths = r.pdeaths or 0, wipes = r.wipes or 0, pulls = r.pulls or 0, xp = r.xp or 0,
    gold = r.gold or 0, loot = r.loot or {}, quests = quests, at = clock() }
end

function M.SummaryLine(s)
  local deaths = (s.deaths or 0) + (s.pdeaths or 0)
  return fmt("Bilan %s : %s · %d/%d boss · %d mort%s%s", s.name, dur(s.time), s.kills, s.total, deaths,
    deaths > 1 and "s" or "", (#s.quests > 0) and fmt(" · %d quête%s finie%s", #s.quests, #s.quests > 1 and "s" or "", #s.quests > 1 and "s" or "") or "")
end
function M.SummaryTip(s)
  local t = { fmt("%s — %s", s.name, dur(s.time)),
    fmt("Boss : %d/%d · pulls : %d · wipes : %d", s.kills, s.total, s.pulls, s.wipes),
    fmt("Morts : toi %d · groupe %d", s.deaths, s.pdeaths) }
  if s.xp > 0 then t[#t + 1] = "XP : " .. short(s.xp) end
  if s.gold > 0 then t[#t + 1] = "Or : " .. money(s.gold) end
  if #s.loot > 0 then t[#t + 1] = "Butin : " .. tconcat(s.loot, ", ") end
  if #s.quests > 0 then t[#t + 1] = "Quêtes finies : " .. tconcat(s.quests, ", ") end
  return tconcat(t, "\n")
end

function M:ArchiveRun()
  local r = self.char and self.char.run
  if not r then return end
  local s = M.Summarize(r)
  local runs = self.char.runs or {}
  self.char.runs = runs
  runs[#runs + 1] = { key = r.key, start = r.start, time = s.time, kills = s.kills, wipes = s.wipes,
    deaths = s.deaths, pulls = s.pulls, xp = s.xp, gold = s.gold, loot = #s.loot, quests = #s.quests }
  while #runs > 20 do table.remove(runs, 1) end
  self.char.run = nil
  if (s.kills > 0 or #s.loot > 0) and opt("summary") then
    self.char.last = s
    self:Suggest("summary", M.SummaryLine(s), 1, { ttl = 600, tab = TAB, tooltip = M.SummaryTip(s), icon = M.ICONS.summary })
  end
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
  self:StartTicker()
  self:Refresh(true)
end

-- objets de quête lâchés par ce boss (quêtes du donjon dans le journal) -> { {item, quest} }
function M.QuestDropsFor(d, b, qs)
  local out = {}
  for _, q in ipairs((qs or M.Quests(d)).inLog) do
    for _, o in ipairs(q.obj) do if o.boss == b then out[#out + 1] = { item = o.name, quest = q.title } end end
  end
  return out
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
    local nxt = d and M.NextBoss(d)
    self:Suggest("kill", "Boss vaincu : " .. b.n .. (nxt and (" — prochain : " .. nxt.n) or (d and " — tous les boss sont tombés" or "")),
      1, { ttl = 60, tab = TAB })
    -- objet de quête à ramasser sur ce boss
    if d and (b.index or 0) > 0 then
      local drops = M.QuestDropsFor(d, b)
      if #drops > 0 then
        self:Suggest("qdrop", fmt("Ramasse « %s » sur %s (quête « %s »)", drops[1].item, b.n, drops[1].quest), 2,
          { ttl = 90, tab = TAB, kind = "collect" })
      end
    end
  elseif success == false then
    wipeAt = now()
    if r then r.wipes = (r.wipes or 0) + 1 end
    if opt("wipe") then
      self:Suggest("wipe", "Wipe" .. (b and (" sur " .. b.n) or "") .. " : plan de reprise dans l'onglet Donjon.",
        3, { ttl = 30, tab = TAB, cooldown = 120 })
    end
  end
  M._dirty = true
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
local DM_DAMAGE, DM_INTERRUPTS = 0, 5
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
  M._dirty = true
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
-- préparation (une seule source : onglet + fenêtre « Prêt ? ») -> { {sec, state = ok|warn|bad|info, text, tip} }
---------------------------------------------------------------------------------------------------
local function myDurability()
  local lo
  for slot = 1, 18 do
    local cur, mx = try(G.GetInventoryItemDurability, slot)
    cur, mx = tonumber(sv(cur)), tonumber(sv(mx))
    if cur and mx and mx > 0 then
      local p = floor(cur * 100 / mx)
      if not lo or p < lo then lo = p end
    end
  end
  return lo
end
local function myFreeSlots()
  local A = T.API
  if A and A.FreeSlots then return tonumber((A.FreeSlots())) end
end

function M.Checklist(d, members)
  local out = {}
  if not d then return out end
  members = members or M.Members()
  local function add(sec, state, text, tip) out[#out + 1] = { sec = sec, state = state, text = text, tip = tip } end
  -- Groupe : niveaux, rôles, taille
  local low, high = {}, {}
  for _, m in ipairs(members) do
    if m.level > 0 and m.level < (d.min or 1) then low[#low + 1] = fmt("%s %d", who(m), m.level) end
    if m.level > (d.max or 60) + 3 then high[#high + 1] = who(m) end
  end
  if #low > 0 then add("Groupe", "bad", fmt("Trop bas (min %d) : %s", d.min or 1, tconcat(low, ", ")))
  else add("Groupe", "ok", fmt("Niveaux : tout le monde dans la tranche %d–%d", d.min or 1, d.max or 60)) end
  if #high > 0 then add("Groupe", "info", "Trop haut (peu d'XP, butin gris) : " .. tconcat(high, ", ")) end
  if #members >= 2 then
    local tank, healer = M.Roles(members)
    local function wc(m) return m and fmt("%s (%s)", who(m), CLASS_FR[m.class] or "?") or "PERSONNE" end
    add("Groupe", (tank and healer) and "ok" or "warn", fmt("Tank : %s · Soigneur : %s%s", wc(tank), wc(healer),
      #members < 5 and fmt(" · %d/5", #members) or ""),
      "Rôle assigné si posé, sinon deviné par la classe. Sans tank : le chasseur tanke au familier, pulls petits.")
  else
    add("Groupe", "warn", "Seul : invite tes amis (un donjon se fait à 4–5)")
  end
  -- Préparation : réparation, sacs, consommables, familier, munitions
  local rep, bags, food, ammo = {}, {}, {}, {}
  local du = myDurability()
  local free = myFreeSlots()
  for _, m in ipairs(members) do
    local st = m.st
    local mdu = m.me and du or (st and st.du and st.du >= 0 and st.du) or nil
    local mfree = m.me and free or (st and st.b) or nil
    if mdu and mdu < 60 then rep[#rep + 1] = fmt("%s %d %%", who(m), mdu) end
    if mfree and mfree < 6 then bags[#bags + 1] = fmt("%s %d", who(m), mfree) end
    if st then
      local fw = (st.fo or 0) + (st.wa or 0)
      if fw < 10 then food[#food + 1] = fmt("%s %d", who(m), fw) end
      if st.c == "HUNTER" and st.am and st.am >= 0 and st.am < 1000 and not m.me then ammo[#ammo + 1] = fmt("%s %d", who(m), st.am) end
    end
  end
  add("Préparation", #rep > 0 and ((du and du < 30) and "bad" or "warn") or "ok",
    #rep > 0 and ("Répare avant d'entrer : " .. tconcat(rep, ", ")) or "Réparation : bon état", "Durabilité la plus basse de chacun (Squad pour les potes)")
  add("Préparation", #bags > 0 and "warn" or "ok", #bags > 0 and ("Fais de la place (cases libres) : " .. tconcat(bags, ", ")) or "Sacs : assez de place")
  if #food > 0 then add("Préparation", "warn", "Achète à manger / boire : " .. tconcat(food, ", ")) end
  if myClass() == "HUNTER" then
    local am = tonumber(sv(try(G.GetInventoryItemCount, "player", 0)))
    if am and am < 1000 then tinsert(ammo, 1, fmt("toi %d", am)) end
    add("Préparation", #ammo > 0 and "warn" or "ok", #ammo > 0 and ("Munitions (≈ 1 000 pour un donjon) : " .. tconcat(ammo, ", ")) or "Munitions : assez")
    local hasPet = sv(try(G.UnitExists, "pet")) and not sv(try(G.UnitIsDead, "pet"))
    local A = T.API
    local hap = A and A.PetHappiness and tonumber((A.PetHappiness())) or nil
    if not hasPet then add("Préparation", "warn", "Appelle ton familier avant d'entrer")
    elseif hap and hap < 3 then add("Préparation", "warn", "Nourris ton familier (pas content : moins de dégâts)")
    else add("Préparation", "ok", "Familier : prêt") end
  end
  return out
end

function M.Ready(list)
  local bad, warn = 0, 0
  for _, c in ipairs(list or {}) do
    if c.state == "bad" then bad = bad + 1 elseif c.state == "warn" then warn = warn + 1 end
  end
  return bad, warn
end

---------------------------------------------------------------------------------------------------
-- lignes de l'onglet « Donjon » : un bloc par section (titre = nom du bloc, icône de la 1re ligne)
---------------------------------------------------------------------------------------------------
local ROLE_FR = { tank = "Tank", heal = "Soins", dps = "DPS" }
M.BLOCKS = { "Dungeon", "Prochain boss", "Boss", "Butin du groupe", "Quêtes du donjon", "Préparation", "Chasseur", "Bilan" }
local STATE_ICON = { ok = "Interface\\RaidFrame\\ReadyCheck-Ready", warn = M.ICONS.warn,
  bad = "Interface\\RaidFrame\\ReadyCheck-NotReady", info = ICON .. "INV_Misc_QuestionMark" }
M.STATE_ICON = STATE_ICON

local function line(out, text, prio, extra)
  local l = extra or {}
  l.text, l.prio = text, prio or 1
  out[#out + 1] = l
  return l
end

local function bossTooltip(b, members, d, qs)
  local tip = { b.n .. (b.en and b.en ~= b.n and (" (" .. b.en .. ")") or "") .. (b.opt and " — optionnel" or "") }
  if b.tip then tip[#tip + 1] = b.tip end
  local jt = members and #members >= 2 and M.JobsText(b, members)
  if jt then tip[#tip + 1] = "Qui fait quoi : " .. jt end
  if b.hunter and myClass() == "HUNTER" then tip[#tip + 1] = "Chasseur : " .. b.hunter end
  for _, a in ipairs(b.alerts or {}) do
    tip[#tip + 1] = (a.first or 0) > 0 and fmt("~%d s puis toutes les ~%d s : %s", a.first, a.every or 0, a.text) or a.text
  end
  if d then
    for _, q in ipairs(M.QuestDropsFor(d, b, qs)) do tip[#tip + 1] = fmt("Quête « %s » : %s", q.quest, q.item) end
  end
  if b.loot and #b.loot > 0 then
    tip[#tip + 1] = "Butin (≈) :"
    local plan = (members and d) and M.LootPlan(d, members) or {}
    for _, it in ipairs(b.loot) do
      local verdict
      for _, e in ipairs(plan) do if e.boss == b and e.id == it[1] then verdict = M.LootVerdictText(e, members) end end
      local name = M.ItemName(it[1], it[2])
      if verdict then tip[#tip + 1] = "  " .. name .. " — " .. verdict
      else
        local w2 = {}
        for c in tostring(it[3]):gmatch("%u+") do w2[#w2 + 1] = CLASS_FR[c] or c end
        tip[#tip + 1] = "  " .. name .. (it[3] == "ALL" and "" or (" — " .. tconcat(w2, ", ")))
      end
    end
  end
  tip[#tip + 1] = (d and d.new) and "(donjon de Forever : mécaniques non vérifiées)" or "(d'après Classic, à confirmer sur Forever)"
  return tconcat(tip, "\n")
end
M.BossTooltip = bossTooltip

function M:BuildBlocks()
  local B = {}
  for _, k in ipairs(M.BLOCKS) do B[k] = {} end
  local d, why = M.Active()
  local last = self.char and self.char.last
  if not d then
    if last and opt("summary") and clock() - (last.at or 0) < 1800 then
      local out = B["Bilan"]
      line(out, M.SummaryLine(last), 2, { icon = M.ICONS.summary, tooltip = M.SummaryTip(last) })
      if #last.loot > 0 then line(out, "Butin : " .. tconcat(last.loot, ", "), 1, { icon = M.ICONS.loot }) end
      if #last.quests > 0 then line(out, "Rends tes quêtes finies : " .. tconcat(last.quests, ", "), 1, { icon = M.ICONS.turnin, kind = "turnin" }) end
      line(out, "Effacer", 1, { control = true, label = "Effacer", onClick = function() M.char.last = nil; M:Refresh(true) end,
        tooltip = "Effacer le bilan du dernier donjon" })
    end
    return B
  end
  local D = M.Data()
  local inInst = why == "instance"
  local members = M.Members()
  local r = run()
  if r and r.key ~= d.key then r = nil end
  local hunter = myClass() == "HUNTER" and opt("hunter")
  local nxt, nk, ntot = M.NextBoss(d)
  local qs = M.Quests(d, members)

  -- 1. en-tête + boutons d'onglet
  local main = B["Dungeon"]
  local status
  if inInst and r then status = fmt(" — en cours %s · %d/%d boss", dur(clock() - (r.start or clock())), nk, ntot)
  elseif inInst then status = " — en cours"
  else status = why == "auto" and " — prévu (quête de donjon)" or " — prévu" end
  line(main, fmt("%s (niv %d–%d)%s%s", d.n, d.min or 1, d.max or 60, d.new and " · Forever" or "", status), 2,
    { icon = M.ICONS.dungeon, tooltip = tconcat(d.tips or {}, "\n") .. "\n" .. tconcat(D.general or {}, "\n"),
      clickHint = "Clic : la fenêtre « Prêt ? » (préparation du groupe)", onClick = function() M:ToggleChecklist(true) end })
  line(main, "Prêt ?", 1, { control = true, label = "Prêt ?", onClick = function() M:ToggleChecklist() end,
    tooltip = "Préparation du groupe : niveaux, rôles, quêtes du donjon, réparation, sacs, familier" })
  if opt("marks") and #members >= 2 then
    local p = {}
    for _, mk in ipairs(M.MarkPlan(members)) do p[#p + 1] = mk.text end
    line(main, "Marquer", 1, { control = true, label = "Marquer", onClick = function() M:MarkTarget() end,
      tooltip = "Marque ta cible : crâne, croix, puis contrôle — un clic = une marque, hors combat\n" .. tconcat(p, "\n") })
  end
  -- fiche « joueur confirmé » : ce qui tue le groupe + le conseil de TON rôle (les 3 rôles en infobulle)
  local card = D.cards and D.cards[d.key]
  if card and not (enc and enc.d == d) then
    local tip = {}
    for _, k in ipairs({ "tank", "heal", "dps" }) do
      if card[k] then tip[#tip + 1] = ROLE_FR[k] .. " : " .. card[k] end
    end
    if card.danger then line(main, "Ce qui tue le groupe : " .. card.danger, 2, { kind = "warn", tooltip = tip }) end
    local tank, healer = M.Roles(members)
    local role = (tank and tank.me) and "tank" or ((healer and healer.me) and "heal") or "dps"
    if card[role] then line(main, ROLE_FR[role] .. " (toi) : " .. card[role], 2, { tooltip = tip }) end
  end
  -- wipe : liste de reprise (5 min)
  if wipeAt and now() - wipeAt < 300 and opt("wipe") then
    local rez = {}
    for _, m in ipairs(members) do if REZ[m.class] then rez[#rez + 1] = who(m) end end
    line(main, "Wipe — reprise :" .. (#rez > 0 and (" rez possible par " .. tconcat(rez, ", ")) or " pas de rez dans le groupe, retour depuis le cimetière"), 3)
    for i, s in ipairs(D.wipe or {}) do if i > 1 or #rez > 0 then line(main, i .. ". " .. s, 2) end end
  end

  -- 2. boss en cours, sinon prochain boss : mécanique + qui fait quoi + pulls avant lui
  local nb = B["Prochain boss"]
  if enc and enc.d == d then
    local b = enc.b
    line(nb, b.tip and fmt("Boss : %s — %s", b.n, b.tip) or ("Boss : " .. b.n), 3, { icon = M.ICONS.boss, tooltip = bossTooltip(b, members, d, qs) })
    if hunter and b.hunter then line(nb, "Chasseur : " .. b.hunter, 2) end
    local t = now() - enc.start
    for _, a in ipairs(b.alerts or {}) do
      if (a.first or 0) > 0 then
        local nextAt = a.first
        if a.every and t > a.first then nextAt = a.first + (floor((t - a.first) / a.every) + 1) * a.every end
        line(nb, fmt("Prochain (≈) : %s dans %d s", a.text, max(0, floor(nextAt - t))), 2)
      end
    end
  elseif nxt and opt("nextBoss") then
    line(nb, fmt("Prochain : %s%s", nxt.n, nxt.tip and (" — " .. nxt.tip) or ""), 2,
      { icon = M.ICONS.boss, tooltip = bossTooltip(nxt, members, d, qs), onClick = function() M:Arm(nxt) end,
        clickHint = "Clic : minuteurs dès le début du prochain combat" })
    local jt = #members >= 2 and M.JobsText(nxt, members, true)
    if jt then line(nb, "Qui fait quoi : " .. jt, 2, { kind = "group", tooltip = "Qui fait quoi : " .. (M.JobsText(nxt, members) or "") }) end
    if hunter and nxt.hunter then line(nb, "Chasseur : " .. nxt.hunter, 1) end
    if opt("pulls") then
      local np = 0
      for _, p in ipairs(d.pulls or {}) do
        if p.b == nxt.index and np < 2 then
          np = np + 1
          line(nb, "Pull : " .. p.t, 2, { kind = "warn", tooltip = "Pulls connus (≈, d'après Classic)" })
        end
      end
    end
    local drops = M.QuestDropsFor(d, nxt, qs)
    if #drops > 0 then line(nb, fmt("Quête : ramasse « %s » sur lui", drops[1].item), 2, { kind = "collect", icon = M.ICONS.quest }) end
    if opt("loot") then
      local nl = 0
      for _, e in ipairs(M.LootPlan(d, members)) do
        if e.boss == nxt and #e.need > 0 and nl < 3 then
          nl = nl + 1
          line(nb, fmt("Butin : %s → %s", e.name, M.LootVerdictText(e, members)), 1, { kind = "gear" })
        end
      end
    end
  elseif not nxt and ntot > 0 and inInst then
    line(nb, "Tous les boss sont tombés : sortez rendre les quêtes", 2, { icon = M.ICONS.skull })
  end

  -- 3. liste des boss (ordre habituel ; ✓ tués ; optionnels gris ; quête ; objets pour le groupe)
  local bl = B["Boss"]
  local lootPlan = opt("loot") and M.LootPlan(d, members) or {}
  local lootBy = {}
  for _, e in ipairs(lootPlan) do if #e.need > 0 then lootBy[e.boss] = (lootBy[e.boss] or 0) + 1 end end
  for i, b in ipairs(d.bosses or {}) do
    local done = M.Killed(b)
    local cur = enc and enc.b == b
    local q = #M.QuestDropsFor(d, b, qs) > 0
    local txt = fmt("%s %d. %s%s%s", done and "|cff33dd33✓|r" or (b.opt and "|cff888888○|r" or "•"), i,
      b.opt and ("|cff9d9d9d" .. b.n .. " (optionnel)|r") or b.n, q and (" " .. QICON) or "",
      (lootBy[b] and not done) and fmt(" |cffa0d0ff· %d objet%s pour vous|r", lootBy[b], lootBy[b] > 1 and "s" or "") or "")
    line(bl, txt, cur and 3 or 1, { tooltip = bossTooltip(b, members, d, qs), onClick = function() M:Arm(b) end,
      icon = i == 1 and M.ICONS.skull or nil, clickHint = "Clic : préparer ce boss (minuteurs au prochain combat)" })
  end

  -- 4. butin du groupe : une ligne par membre (ses objets des boss à venir), Besoin / Cupidité par objet en infobulle
  if opt("loot") then
    local lb = B["Butin du groupe"]
    local per = {}
    for _, e in ipairs(lootPlan) do
      if not (inInst and M.Killed(e.boss)) then
        for _, n in ipairs(e.need) do
          per[n.m] = per[n.m] or {}
          tinsert(per[n.m], { e = e, gain = n.gain })
        end
      end
    end
    for _, m in ipairs(members) do
      local list = per[m]
      if list then
        tsort(list, function(x, y) return (x.gain or 0.5) > (y.gain or 0.5) end)
        local parts, tip = {}, { fmt("Pour %s (%s) — tables de butin Classic ≈", who(m), CLASS_FR[m.class] or "?") }
        for i, x in ipairs(list) do
          if i <= 2 then parts[#parts + 1] = fmt("%s%s (%s)", x.e.name, gainTxt(x.gain), x.e.boss.n) end
          tip[#tip + 1] = fmt("%s (%s) : %s", x.e.name, x.e.boss.n, M.LootVerdictText(x.e, members))
        end
        line(lb, fmt("Pour %s%s : %s%s", who(m), m.me and "" or (" (" .. (CLASS_FR[m.class] or "?") .. ")"), tconcat(parts, ", "),
          #list > 2 and fmt(" +%d", #list - 2) or ""), m.me and 2 or 1,
          { icon = #lb == 0 and M.ICONS.loot or nil, tooltip = tconcat(tip, "\n"), kind = "gear" })
      end
    end
  end

  -- 5. quêtes du donjon : dans le journal (objectifs, qui ne l'a pas) / à prendre (donneur)
  if opt("quests") then
    local ql = B["Quêtes du donjon"]
    local first = true
    local nShare = 0
    for _, q in ipairs(qs.inLog) do
      local parts = {}
      for _, o in ipairs(q.obj) do
        parts[#parts + 1] = fmt("%s %d/%d%s", o.name, o.have, o.need, o.boss and (" (" .. o.boss.n .. ")") or "")
      end
      local txt = q.done and fmt("Rends « %s » : finie", q.title)
        or fmt("« %s »%s", q.title, #parts > 0 and (" : " .. tconcat(parts, ", ")) or "")
      if #q.missing > 0 then txt = txt .. fmt(" · manque à %s", tconcat(q.missing, ", ")) end
      local canShare = #q.missing > 0 and q.pushable ~= false and not q.done
      if canShare then nShare = nShare + 1 end
      line(ql, txt, (canShare and not inInst) and 2 or 1, { icon = first and M.ICONS.quest or nil, questID = q.id,
        kind = q.done and "turnin" or "quest",
        onClick = canShare and function() M:ShareQuests(q.id) end or nil,
        clickHint = canShare and "Clic : partager la quête au groupe (hors combat)" or nil })
      first = false
    end
    for i, q in ipairs(qs.toGet) do
      if i > 5 then break end
      local txt = fmt("Prends « %s » [%d]%s", q.title, q.level or 0, q.giver and (" chez " .. q.giver) or "")
      if #q.holders > 0 then txt = txt .. fmt(" · %s l'a", tconcat(q.holders, ", ")) end
      line(ql, txt, inInst and 1 or 2, { icon = first and M.ICONS.quest or nil, kind = "quest",
        tooltip = "QuestieDB (lecture seule) ; niveau requis " .. (q.req or 0) .. (#q.holders > 0 and "\nDemande-lui de la partager." or "") })
      first = false
    end
    if qs.pending then line(ql, "Quêtes du donjon : lecture de Questie en cours…", 1, { icon = M.ICONS.quest }) end
    if nShare > 0 then
      line(ql, "Partager", 1, { control = true, label = "Partager", onClick = function() M:ShareQuests() end,
        tooltip = fmt("Partage %d quête%s du donjon à ceux qui ne l'ont pas (hors combat)", nShare, nShare > 1 and "s" or "") })
    end
  end

  -- 6. préparation : seulement ce qui cloche (hors combat, avant le 1er boss)
  if opt("checklist") and not inCombat() and not (r and (r.nk or 0) > 0 and inInst) then
    local pr = B["Préparation"]
    for _, c in ipairs(M.Checklist(d, members)) do
      if c.state == "bad" or c.state == "warn" then
        line(pr, c.text, c.state == "bad" and 3 or 2, { icon = STATE_ICON[c.state], tooltip = c.tip })
      end
    end
  end

  -- 7. chasseur : familier tank ou pas, cibles à contrôler, conseil du donjon
  if hunter then
    local hb = B["Chasseur"]
    local tank = M.Roles(members)
    if #members >= 2 or inInst then
      if tank and not tank.me then
        line(hb, "Familier en Défensif, Grognement coupé : " .. who(tank) .. " garde l'aggro", 2, { icon = M.ICONS.hunter, kind = "pet" })
      else
        line(hb, "Pas de tank : ton familier tanke (Grognement actif, Défensif), un pack à la fois", 2, { icon = M.ICONS.hunter, kind = "pet" })
      end
    end
    if d.cc then line(hb, "À contrôler : " .. d.cc, 1, { icon = #hb == 0 and M.ICONS.hunter or nil,
      tooltip = "Piège givrant posé avant le pull, puis la cible marquée carré tirée dessus." }) end
    if d.hunter then line(hb, "Chasseur : " .. d.hunter, 1, { icon = #hb == 0 and M.ICONS.hunter or nil }) end
  end

  -- 8. chrono du run + mini-compteur
  local st = B["Bilan"]
  if r and inInst then
    local deaths = (r.deaths or 0) + (r.pdeaths or 0)
    line(st, fmt("Run : %s · %d pull%s · %d/%d boss · %d mort%s · %d wipe%s%s%s", dur(clock() - (r.start or clock())),
      r.pulls or 0, (r.pulls or 0) > 1 and "s" or "", nk, ntot, deaths, deaths > 1 and "s" or "",
      r.wipes or 0, (r.wipes or 0) > 1 and "s" or "",
      (r.xp or 0) > 0 and (" · " .. short(r.xp) .. " XP") or "", (r.gold or 0) > 0 and (" · " .. money(r.gold)) or ""), 1,
      { icon = M.ICONS.summary, tooltip = M.SummaryTip(M.Summarize(r)) })
  end
  if opt("meter") then
    local lm = meterText(self.lastMeter, "Dégâts, dernier combat")
    if lm then line(st, lm, 1, { tooltip = meterText(self.runMeter, "Dégâts, séance") }) end
    local it = self.interrupts
    if it and #it.rows > 0 then
      local p = {}
      for i = 1, min(3, #it.rows) do p[#p + 1] = (it.rows[i].me and "toi" or it.rows[i].name) .. " " .. it.rows[i].amount end
      line(st, "Interruptions : " .. tconcat(p, " · "), 1)
    end
  end
  return B
end

--- Toutes les lignes à plat (scénarios, résumé) + les blocs
function M:BuildLines()
  local B = self:BuildBlocks()
  local out = {}
  for _, k in ipairs(M.BLOCKS) do for _, l in ipairs(B[k] or {}) do out[#out + 1] = l end end
  return out, B
end

--- Clic sur un boss : conseil complet dans « Maintenant », minuteurs au prochain combat (sans évènement de boss).
function M:Arm(b)
  self._armed = b
  self:Suggest("armed", "Prêt pour " .. b.n .. (b.tip and (" : " .. b.tip) or "") .. " (minuteurs au début du combat)",
    2, { ttl = 120, tab = TAB })
  self:Refresh(true)
end

---------------------------------------------------------------------------------------------------
-- onglet : présent seulement en instance / donjon prévu / bilan frais
---------------------------------------------------------------------------------------------------
function M:UpdateTab(blocks)
  local any = false
  for _, k in ipairs(M.BLOCKS) do if blocks and blocks[k] and #blocks[k] > 0 then any = true end end
  local show = opt("tab") and self:IsEnabled() and any
  if show then
    if not (T.tabs and T.tabs[TAB]) then T:RegisterTab(TAB, "Donjon", 35) end
    if T.SetTabLines then
      for _, k in ipairs(M.BLOCKS) do T:SetTabLines(TAB, blocks[k] or {}, k) end
    end
  elseif T.tabs and T.tabs[TAB] then
    if T.SetTabLines then for _, k in ipairs(M.BLOCKS) do T:SetTabLines(TAB, {}, k) end end
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
  M._dirty = false
  local ok, lines, blocks = pcall(self.BuildLines, self)
  if not ok then self:Debug("BuildLines", lines) lines, blocks = {}, {} end
  self.lines = lines
  self:UpdateTab(blocks)
  if self.RefreshChecklist then self:RefreshChecklist() end
end

---------------------------------------------------------------------------------------------------
-- minuterie : seulement en instance, pendant un boss, ou fenêtre « Prêt ? » ouverte
---------------------------------------------------------------------------------------------------
local lastBuild = 0
function M:NeedTicker()
  return enc ~= nil or inInstanceNow() or (self.ChecklistShown and self:ChecklistShown()) or false
end
function M:StartTicker()
  if self._ticker or not self:IsEnabled() then return end
  self._ticker = T:NewTicker(1, function() M:Tick() end, self)
end
function M:StopTicker()
  if self._ticker then self._ticker:Cancel(); self._ticker = nil end
end
function M:Tick()
  if not M:IsEnabled() then return end
  if not M:NeedTicker() then M:StopTicker() return end
  M:TickTimers()
  local t = now()
  local shown = (not T.TabShown) or T:TabShown(TAB)
  -- reconstruit : sale, ou onglet visible (2 s pendant un boss à minuteurs, 15 s sinon), 60 s quand l'onglet est caché
  local timers = false
  if enc then for _, a in ipairs(enc.b.alerts or {}) do if (a.first or 0) > 0 then timers = true end end end
  local gap = (timers and shown) and 2 or (shown and 15 or 60)
  if M._dirty or t - lastBuild >= gap then lastBuild = t; M:Refresh(true) end
end

---------------------------------------------------------------------------------------------------
-- évènements
---------------------------------------------------------------------------------------------------
function M:OnZone()
  local prev = run()
  M.Invalidate()
  local d = M.Current()
  if d then
    if prev and prev.key ~= d.key then self:ArchiveRun() end
    self:StartRun(d)
    if self.char and self.char.planned == d.key then self.char.planned = nil end  -- arrivé : le plan devient le run
  elseif prev and prev.key then
    self:ArchiveRun()     -- sortie de l'instance : bilan
  end
  M.Active(true)
  if M:NeedTicker() then self:StartTicker() end
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

local partyDead = {}
function M:OnCombatStart()
  local r = run()
  local d = inInstanceNow() and activeCache[1] or nil
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
  -- morts du groupe (pas de journal de combat pour les addons) : qui est mort à la fin du combat
  local r = run()
  if r and inInstanceNow() then
    for i = 1, 4 do
      local u = "party" .. i
      local dead = (sv(try(G.UnitExists, u)) and sv(try(G.UnitIsDeadOrGhost, u))) and true or false
      if dead and not partyDead[u] then r.pdeaths = (r.pdeaths or 0) + 1 end
      partyDead[u] = dead
    end
  end
  M._dirty = true
  T:After(0.5, function() M:UpdateMeter(); M:Refresh(true) end)
end

function M:PLAYER_DEAD()
  local r = run()
  if r and inInstanceNow() then r.deaths = (r.deaths or 0) + 1; M._dirty = true end
end

function M:PLAYER_XP_UPDATE()
  local xp = tonumber(sv(try(G.UnitXP, "player")))
  if not xp then return end
  local r = run()
  if r and inInstanceNow() and self._lastXP and xp > self._lastXP then r.xp = (r.xp or 0) + (xp - self._lastXP) end
  self._lastXP = xp
end

function M:PLAYER_MONEY()
  local r = run()
  local m = tonumber((try(G.GetMoney)))
  if r and m and inInstanceNow() then r.gold = max(0, m - (r.money0 or m)) end
end

-- butin reçu (message de butin du jeu, lisible seulement hors valeurs secrètes ; qualité ≥ verte)
local lootPatterns
function M.ParseLoot(msg)
  msg = sv(msg)
  if type(msg) ~= "string" then return nil end
  if not lootPatterns then
    lootPatterns = {}
    for _, gs in ipairs({ G.LOOT_ITEM_SELF_MULTIPLE, G.LOOT_ITEM_SELF, G.LOOT_ITEM_PUSHED_SELF }) do
      if type(gs) == "string" then
        local p = gs:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1")
        p = p:gsub("%%%%s", "(.+)"):gsub("%%%%d", "(%%d+)")
        lootPatterns[#lootPatterns + 1] = "^" .. p .. "$"
      end
    end
    if #lootPatterns == 0 then lootPatterns[1] = "^Vous recevez le butin : (.+)%.$" end
  end
  for _, p in ipairs(lootPatterns) do
    local link = msg:match(p)
    if link then return link end
  end
  return nil
end
function M:OnLoot(msg)
  local r = run()
  if not (r and inInstanceNow()) then return end
  local link = M.ParseLoot(msg)
  if not link then return end
  local id = tonumber(link:match("item:(%d+)"))
  local q = select(7, itemInfo(id or link))
  if q and q < 2 then return end
  local name = link:match("%[(.-)%]") or link
  r.loot = r.loot or {}
  if #r.loot < 20 then r.loot[#r.loot + 1] = name end
end

function M:OnQuestTurnedIn(qid)
  local r = run()
  if not (r and qid) then return end
  local Q = mod("Quests")
  for _, q in ipairs(Q and Q.lastList or {}) do
    if q.id == qid then r.quests = r.quests or {}; r.quests[qid] = q.title end
  end
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local d, why = M.Active()
  local r = run()
  local nxt = d and M.NextBoss(d)
  local last = self.char and self.char.last
  s.dungeon = {
    key = d and d.key, name = d and d.n, why = why, boss = enc and enc.b and enc.b.n or nil, next = nxt and nxt.n or nil,
    run = r and { key = r.key, time = clock() - (r.start or clock()), pulls = r.pulls, kills = r.nk, wipes = r.wipes,
      deaths = r.deaths, pdeaths = r.pdeaths, xp = r.xp, gold = r.gold, loot = r.loot and #r.loot or 0 } or nil,
    last = last and { key = last.key, time = last.time, kills = last.kills, total = last.total, deaths = last.deaths,
      quests = #last.quests, loot = #last.loot } or nil,
    meter = self.lastMeter and { total = self.lastMeter.total, top = self.lastMeter.rows[1] and self.lastMeter.rows[1].name } or nil,
    runs = self.char and #(self.char.runs or {}) or 0,
  }
end

function M:Report(d)
  d = d or M.Active()
  if not d then self:Print("Aucun donjon prévu : clique « Prêt ? » (pastille de la minimap) ou /tai donjon <nom>.") return end
  self:Print(fmt("%s (niv %d–%d) : %s", d.n, d.min or 1, d.max or 60, tconcat(d.tips or {}, " ")))
end

local function dirty() M._dirty = true end
function M:OnEnable()
  M.Index()
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() M:OnZone() end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() M:OnZone() end)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function()
    membersCache = nil
    local had = activeCache
    M.Invalidate()
    if M:NeedTicker() then return end
    if had or M.Active() then M:Refresh() end
  end)
  self:RegisterEvent("ENCOUNTER_START")
  self:RegisterEvent("ENCOUNTER_END")
  self:RegisterEvent("BOSS_KILL")
  self:RegisterEvent("PLAYER_DEAD")
  self:RegisterEvent("PLAYER_XP_UPDATE")
  self:RegisterEvent("PLAYER_MONEY")
  self:RegisterEvent("CHAT_MSG_LOOT", function(_, _, msg) M:OnLoot(msg) end)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid) M:OnQuestTurnedIn(tonumber(sv(qid))) end)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", function() M._equipGen = (M._equipGen or 0) + 1; if activeCache then dirty() end end)
  self:RegisterEvent("GET_ITEM_INFO_RECEIVED", function() if activeCache then M._itemGen = (M._itemGen or 0) + 1; dirty() end end)
  for _, e in ipairs({ "CHAT_MSG_MONSTER_YELL", "CHAT_MSG_MONSTER_EMOTE", "RAID_BOSS_EMOTE", "CHAT_MSG_RAID_BOSS_EMOTE" }) do
    self:RegisterEvent(e, function(_, _, text, sender) if inInstanceNow() then M:OnYell(nil, text, sender) end end)
  end
  self:RegisterEvent("DAMAGE_METER_COMBAT_SESSION_UPDATED", function() if not inCombat() and activeCache then M:UpdateMeter() end end)
  self:On("COMBAT_START", function() if activeCache then M:OnCombatStart() end end)
  self:On("COMBAT_END", function() if activeCache or enc then M:OnCombatEnd() end end)
  self:On("QUESTS_UPDATED", function()
    M._questGen = (M._questGen or 0) + 1
    local had = activeCache
    if not (M.char and M.char.planned) and not inInstanceNow() then M.Invalidate() end
    if M:NeedTicker() then dirty() return end
    if had or M.Active() then M:Refresh() end
  end)
  self:On("SUMMARY", function() M:WriteSummary() end)
  self:OnZone()
end

function M:OnDisable()
  self:StopTicker()
  enc = nil
  self:UpdateTab({})
  if self.ToggleChecklist then self:ToggleChecklist(false) end
end

function M:OnOptionChanged() M.Invalidate(); M:Refresh(true) end

T:RegisterSlash("donjon", function(arg)
  arg = type(arg) == "string" and arg:match("^%s*(.-)%s*$") or ""
  if arg == "" then M:Report() return end
  if arg == "liste" then
    local out = {}
    for _, d in ipairs(M.ForLevel()) do out[#out + 1] = fmt("%s %d–%d", d.n, d.min or 1, d.max or 60) end
    M:Print("Donjons à ton niveau : " .. (#out > 0 and tconcat(out, ", ") or "aucun"))
    return
  end
  if arg == "stop" then M.SetPlanned(nil) M:Print("Donjon prévu effacé.") return end
  if arg == "marque" then M:MarkTarget() return end
  if arg == "pret" or arg == "prêt" then if M.ToggleChecklist then M:ToggleChecklist(true) end return end
  if arg == "bilan" then
    local s = M.char and (M.char.last or (M.char.run and M.Summarize(M.char.run)))
    M:Print(s and M.SummaryLine(s) or "Pas encore de bilan de donjon.")
    return
  end
  local d = M.SetPlanned(arg)
  if d then M:Print("Donjon prévu : " .. d.n .. " — onglet « Donjon » ouvert.") else M:Print("Donjon inconnu : " .. arg .. " (/tai donjon liste)") end
end, "donjon : /tai donjon <nom>|pret|bilan|liste|stop|marque")

-- pastille de la minimap : entrée « Prêt pour le donjon ? » (lue par Hub si présent)
T.HUB_EXTRA = T.HUB_EXTRA or {}
tinsert(T.HUB_EXTRA, { group = "outils", id = "donjon", title = "Prêt pour le donjon ?", icon = M.ICONS.dungeon,
  tip = "Clic : préparation du groupe (niveaux, quêtes du donjon, réparation, familier)",
  fn = function() if M.ToggleChecklist then M:ToggleChecklist(true) end end })
