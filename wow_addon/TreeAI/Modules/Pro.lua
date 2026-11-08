-- TreeAI — module « Quêtes pro » (agent X1) : 15 aides de quête, INFORMATIVES uniquement.
--  1 quêtes du journal, les plus rapides d'abord       2 (déplacé : Modules/Abandon.lua, « À retirer » + pourquoi)
--  3 camp saturé (monstres déjà tagués)            4 PNJ nommé de quête en vue
--  5 escorte : « prépare-toi avant de lancer »     6 chaînes : ce qui se débloque ensuite et où (Questie)
--  7 quêtes relais (breadcrumbs, Questie)          8 quêtes à prendre avant un donjon
--  9 checklist au niveau (entraîneur au prix lu, talents)
-- 10 repos : où se déconnecter                    11 meilleur lieu pour la pierre de foyer
-- 12 meilleure récompense connue avant de finir   13 temps restant par objectif (« 4/10 : ~3 min »)
-- 14 respawn des monstres de quête (observé)      15 vrai taux de drop (mesuré : Stats, sinon le nôtre)
-- Sortie : onglet « Quêtes » (TreeAI:SetTabLines, bloc "Pro") + « Infos » ; repli Suggest. Jamais le chat.
-- Rien n'est fait à la place du joueur. Aucune sélection dans le journal de quêtes Blizzard (taint).
-- Questie : lecture seule (QuestieLoader._modules.QuestieDB ou LibQuestieDB), rien n'est copié.
-- Forever : valeurs « secrètes » en combat → calculs hors combat, issecretvalue testé.
-- Lua 5.1, aucune globale. Expose TreeAI.ProUtil (outils partagés).
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("Pro", {
  title = "Quêtes pro",
  -- (QUESTQA) coupés par défaut : ce qui reposait sur des estimations jamais vérifiées (classement XP/min, temps
  -- par objectif), les doublons (checklist = Errands, repos / foyer = Speed) et les quêtes de donjon que tu n'as pas
  defaults = {
    enabled = true, xpRank = false, camp = true, named = true, escort = true, chains = true,
    breadcrumbs = true, dungeons = false, checklist = false, rested = false, hearth = false, rewards = true,
    eta = false, respawn = true, drops = true, maxLines = 12,
  },
  charDefaults = { rewards = {}, inns = {}, trainerLevel = 0, escort = {}, prog = {} },
  options = {
    { key = "xpRank", type = "toggle", label = "Quêtes classées (les plus rapides d'abord)", section = "Journal" },
    { key = "rewards", type = "toggle", label = "Meilleure récompense connue", section = "Journal" },
    { key = "eta", type = "toggle", label = "Temps restant par objectif", section = "Objectifs" },
    { key = "drops", type = "toggle", label = "Vrai taux de drop", section = "Objectifs" },
    { key = "respawn", type = "toggle", label = "Respawn des monstres de quête", section = "Objectifs" },
    { key = "camp", type = "toggle", label = "Camp saturé (tagué par d'autres)", section = "Terrain" },
    { key = "named", type = "toggle", label = "PNJ nommé de quête en vue", section = "Terrain" },
    { key = "escort", type = "toggle", label = "Escortes : se préparer avant", section = "Terrain" },
    { key = "chains", type = "toggle", label = "Chaînes : suite et lieu (Questie)", section = "Suites" },
    { key = "breadcrumbs", type = "toggle", label = "Quêtes relais (Questie)", section = "Suites" },
    { key = "dungeons", type = "toggle", label = "Quêtes à prendre avant un donjon", section = "Suites" },
    { key = "checklist", type = "toggle", label = "Checklist au niveau", section = "Niveau" },
    { key = "rested", type = "toggle", label = "Repos : où se déconnecter", section = "Niveau" },
    { key = "hearth", type = "toggle", label = "Meilleur lieu pour la pierre de foyer", section = "Niveau" },
    { key = "maxLines", type = "range", label = "Lignes max dans l'onglet", min = 4, max = 30, step = 1, section = "Affichage" },
  },
})
if T.MODULE_TAB and not T.MODULE_TAB.Pro then T.MODULE_TAB.Pro = "quests" end

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local function g(name) return rawget(_G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function now() return try(g("GetTime")) or 0 end
local function epoch() local f = g("time") or (g("os") and g("os").time) return (f and f()) or 0 end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and f(v) and true or false end
local function opt(k) return M.db and M.db[k] ~= false end
local function low(s) return type(s) == "string" and s:lower() or "" end
local function inCombat() return T:InCombat() end
local function level() return tonumber((try(g("UnitLevel"), "player"))) or 1 end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function zoneName() return try(g("GetRealZoneText")) or try(g("GetZoneText")) or "" end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end

local U = T.ProUtil or {}
T.ProUtil = U
U.try, U.secret, U.g = try, secret, g

function U.Money(c)
  c = floor(tonumber(c) or 0)
  local neg = c < 0 and "-" or ""
  c = abs(c)
  local go, s, co = floor(c / 10000), floor(c / 100) % 100, c % 100
  if go > 0 then return neg .. go .. " po" .. (s > 0 and (" " .. s .. " pa") or "") end
  if s > 0 then return neg .. s .. " pa" .. (co > 0 and (" " .. co .. " pc") or "") end
  return neg .. co .. " pc"
end
function U.Dur(sec)
  sec = tonumber(sec)
  if not sec or sec ~= sec or sec < 0 then return "?" end
  if sec < 60 then return "<1 min" end
  if sec < 3600 then return floor(sec / 60 + 0.5) .. " min" end
  return floor(sec / 3600) .. " h " .. string.format("%02d", floor(sec % 3600 / 60))
end

-- Sortie vers un onglet (bloc par source), repli Suggest (ids "src:i")
local lastOut = {}
function U.Out(mod_, tab, lines, source)
  source = source or mod_.name
  if T.SetTabLines then
    pcall(T.SetTabLines, T, tab, lines, source)
    return
  end
  local key = tab .. source
  for i = 1, (lastOut[key] or 0) do mod_:Clear(tab .. i) end
  for i, l in ipairs(lines) do
    if i > 5 then break end
    mod_:Suggest(tab .. i, l.text, l.prio or 1, { ttl = 0, tab = tab, tooltip = l.tooltip, onClick = l.onClick })
  end
  lastOut[key] = min(#lines, 5)
end

-- ------------------------------------------------------------------ objets : amélioration (niveau d'objet)
local SLOTS = {
  INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_BODY = { 4 },
  INVTYPE_CHEST = { 5 }, INVTYPE_ROBE = { 5 }, INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 },
  INVTYPE_WRIST = { 9 }, INVTYPE_HAND = { 10 }, INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 },
  INVTYPE_CLOAK = { 15 }, INVTYPE_WEAPON = { 16, 17 }, INVTYPE_2HWEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 },
  INVTYPE_WEAPONOFFHAND = { 17 }, INVTYPE_SHIELD = { 17 }, INVTYPE_HOLDABLE = { 17 }, INVTYPE_RANGED = { 18 },
  INVTYPE_RANGEDRIGHT = { 18 }, INVTYPE_THROWN = { 18 },
}
local SLOTNAME = { "Tête", "Cou", "Épaules", "Chemise", "Torse", "Taille", "Jambes", "Pieds", "Poignets", "Mains",
  "Doigt", "Doigt", "Bijou", "Bijou", "Dos", "Main droite", "Main gauche", "À distance" }
-- armure (classID 4) : sous-classe max portable (1 tissu, 2 cuir, 3 mailles, 4 plaques) ; « best » dès le 40
local ARMOR = { MAGE = 1, PRIEST = 1, WARLOCK = 1, ROGUE = 2, DRUID = 2, HUNTER = 2, SHAMAN = 2, WARRIOR = 3, PALADIN = 3 }
local ARMOR40 = { HUNTER = 3, SHAMAN = 3, WARRIOR = 4, PALADIN = 4 }
local function itemInfo(x)
  if API.ItemInfo then return API.ItemInfo(x) end
  return try(g("GetItemInfo"), x)
end
U.ItemInfo = itemInfo
function U.ItemID(link)
  if type(link) == "number" then return link end
  return tonumber(type(link) == "string" and link:match("item:(%d+)") or nil)
end
function U.CanWear(link)
  local _, _, _, _, minLevel, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link)
  if not equipLoc or equipLoc == "" or not SLOTS[equipLoc] then return false end
  if (tonumber(minLevel) or 0) > level() then return false, "niveau" end
  if classID == 4 and equipLoc ~= "INVTYPE_CLOAK" and (subID or 0) >= 1 and (subID or 0) <= 4 then
    local cls = T:PlayerClass() or "WARRIOR"
    local cap = (level() >= 40 and ARMOR40[cls]) or ARMOR[cls] or 1
    if subID > cap then return false, "armure" end
  end
  return true
end
-- gain de niveau d'objet par rapport au plus faible objet équipé du même emplacement
function U.Upgrade(link)
  local ok = U.CanWear(link)
  if not ok then return nil end
  local _, _, quality, ilvl, _, _, _, _, equipLoc = itemInfo(link)
  ilvl = tonumber(ilvl) or 0
  local bestDelta, bestSlot
  for _, slot in ipairs(SLOTS[equipLoc] or {}) do
    local cur = try(g("GetInventoryItemLink"), "player", slot)
    local cl = 0
    if cur then
      local _, _, cq, il = itemInfo(cur)
      cl = (tonumber(il) or 0) + ((tonumber(cq) or 1) - 1) * 2
    end
    local d = ilvl + ((tonumber(quality) or 1) - 1) * 2 - cl
    if not bestDelta or d > bestDelta then bestDelta, bestSlot = d, slot end
  end
  if not bestDelta or bestDelta <= 0 then return nil end
  return bestDelta, SLOTNAME[bestSlot] or "?", bestSlot
end

-- ------------------------------------------------------------------ Questie (lecture seule, jamais copié)
local function questie()
  local L = g("QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) == "table" and type(db.QueryQuestSingle) == "function" then return db end
  local LS = g("LibStub")
  local lib = type(LS) == "table" and try(LS.GetLibrary or LS, LS, "LibQuestieDB", true) or nil
  if type(lib) == "table" and type(lib.QueryQuestSingle) == "function" then return lib end
  return nil
end
U.Questie = questie
local qcache = {}
local function qq(id, key)
  local db = questie()
  if not db then return nil end
  local c = qcache[id]
  if not c then c = {} qcache[id] = c end
  if c[key] == nil then
    local v = try(db.QueryQuestSingle, id, key)
    c[key] = (v == nil) and false or v
  end
  return c[key] or nil
end
local function qnpc(id, key)
  local db = questie()
  if not (db and db.QueryNPCSingle and id) then return nil end
  return try(db.QueryNPCSingle, id, key)
end
local function areaName(id)
  if type(id) ~= "number" or id <= 0 then return nil end
  local C = g("C_Map")
  if C and C.GetAreaInfo then return try(C.GetAreaInfo, id) end
  return nil
end
local function npcWhere(npcID)
  local name = qnpc(npcID, "name")
  local zone = areaName(qnpc(npcID, "zoneID"))
  return name, zone
end
local function starter(id)
  local sb = qq(id, "startedBy")
  if type(sb) == "table" and type(sb[1]) == "table" and sb[1][1] then return npcWhere(sb[1][1]) end
  return nil
end
local function finisher(id)
  local fb = qq(id, "finishedBy")
  if type(fb) == "table" and type(fb[1]) == "table" and fb[1][1] then return npcWhere(fb[1][1]) end
  return nil
end
local function questieDone()
  local Q = g("Questie")
  local c = type(Q) == "table" and type(Q.db) == "table" and type(Q.db.char) == "table" and Q.db.char.complete
  return type(c) == "table" and c or {}
end

-- ------------------------------------------------------------------ journal (sans toucher à la sélection)
local function objName(text)
  if type(text) ~= "string" then return nil end
  local n = T.ObjectiveName and T.ObjectiveName(text) or text
  if n == "" then n = text end
  for _, suf in ipairs({ " tuées", " tués", " tuée", " tué", " slain", " killed" }) do
    if #n > #suf and n:sub(-#suf):lower() == suf then n = n:sub(1, -#suf - 1) break end
  end
  return n
end
M.ObjName = objName
local function parseCount(text)
  local h, n = tostring(text or ""):match("(%d+)%s*/%s*(%d+)")
  return tonumber(h), tonumber(n)
end
local ESCORT = { "escort", "protég", "proteg", "accompagn", "garde du corps", "protect" }
local function isEscortText(s)
  s = low(s)
  for _, k in ipairs(ESCORT) do if s:find(k, 1, true) then return true end end
  return false
end

function M.ScanLog()
  local list = {}
  local CQ = g("C_QuestLog")
  local n = tonumber((CQ and CQ.GetNumQuestLogEntries and try(CQ.GetNumQuestLogEntries)) or try(g("GetNumQuestLogEntries"))) or 0
  local header
  for i = 1, min(n, 60) do
    local q
    if CQ and CQ.GetInfo then
      local info = try(CQ.GetInfo, i)
      if type(info) == "table" then
        if info.isHeader then header = info.title
        elseif info.questID and not info.isHidden then
          q = { id = info.questID, title = info.title, level = tonumber(info.level) or level(), group = tonumber(info.suggestedGroup) or 0,
            complete = (CQ.IsComplete and try(CQ.IsComplete, info.questID)) and true or false, obj = {} }
          for j, o in ipairs((CQ.GetQuestObjectives and try(CQ.GetQuestObjectives, info.questID)) or {}) do
            local h, nd = tonumber(o.numFulfilled), tonumber(o.numRequired)
            if not nd then h, nd = parseCount(o.text) end
            q.obj[j] = { text = o.text, name = objName(o.text), have = h or 0, need = nd or 1, done = o.finished and true or false, kind = o.type }
          end
          local tag = CQ.GetQuestTagInfo and try(CQ.GetQuestTagInfo, info.questID)
          q.elite = type(tag) == "table" and (tag.isElite or tag.tagID == 1) and true or false
          q.failed = (CQ.IsFailed and try(CQ.IsFailed, info.questID)) and true or false
        end
      end
    else
      local title, lvl, grp, isHeader, _, isComplete, _, qid = try(g("GetQuestLogTitle"), i)
      if isHeader then header = title
      elseif title then
        q = { id = tonumber(qid) or (10000 + i), title = title, level = tonumber(lvl) or level(), group = tonumber(grp) or 0,
          complete = (isComplete == 1 or isComplete == true), failed = isComplete == -1, obj = {} }
        for j = 1, tonumber((try(g("GetNumQuestLeaderBoards"), i))) or 0 do
          local text, kind, done = try(g("GetQuestLogLeaderBoard"), j, i)
          local h, nd = parseCount(text)
          q.obj[j] = { text = text, name = objName(text), have = h or (done and 1 or 0), need = nd or 1, done = done and true or false, kind = kind }
        end
      end
    end
    if q and not secret(q.title) then
      q.zone = header
      q.escort = isEscortText(q.title) or (M.char and M.char.escort[q.id]) and true or false
      for _, o in ipairs(q.obj) do if isEscortText(o.text) then q.escort = true end end
      list[#list + 1] = q
    end
  end
  M.log = list
  return list
end

-- ------------------------------------------------------------------ XP (1, 2)
-- XP estimée d'une quête (formule d'ordre de grandeur Classic) + réduction des quêtes trop basses
function M.EstimateXP(ql, pl)
  ql, pl = tonumber(ql) or 1, tonumber(pl) or 1
  local base = floor(ql * ql * 1.7 + ql * 20 + 50)
  local d = pl - ql
  local f = (d <= 5 and 1) or (d == 6 and 0.8) or (d == 7 and 0.6) or (d == 8 and 0.4) or (d == 9 and 0.2) or 0.1
  return floor(base * f)
end
local function questXP(q)
  local CQ = g("C_QuestLog")
  if CQ and q.id < 10000 then
    local x = tonumber((try(g("GetQuestLogRewardXP"), q.id)))
    if x and x > 0 then return x, true end
  end
  return M.EstimateXP(q.level, level()), false
end

-- kills : mesurés par Stats (façade T.Predict, ≥ 10 min de mesure) sinon par les morts que Detect publie (« X meurt »)
local S = { kills = {}, deaths = {}, plates = {}, seen = {}, killT = {}, lastKill = nil, start = 0 }
local function killSeconds()
  local P = T.Predict
  if P and P.Rates then
    local ok, r = pcall(P.Rates)
    if ok and type(r) == "table" and tonumber(r.killsPerHour) and r.killsPerHour > 5 then return 3600 / r.killsPerHour end
  end
  local n = #S.killT
  if n >= 4 then
    local span = S.killT[n] - S.killT[1]
    if span > 30 then return max(8, span / (n - 1)) end
  end
  return 40
end
M.KillSeconds = killSeconds

-- taux de drop (15) : mesuré par Stats (façade T.Predict) si connu, sinon nos propres observations
local function dropRate(item)
  local P = T.Predict
  if P and P.BestSource then
    local ok, mob, r, n = pcall(P.BestSource, item)
    if ok and mob and tonumber(r) then return r, n, mob, "Stats" end
  end
  local d = M.db and M.db.dropData and M.db.dropData[low(item)]
  if d and (d.k or 0) >= 3 then return (d.n or 0) / d.k, d.k, d.mob, "TreeAI" end
  return nil
end
M.DropRate = dropRate

-- secondes restantes estimées pour un objectif
local function objETA(q, j, o)
  if o.done or o.have >= o.need then return 0 end
  local rem = o.need - o.have
  -- (13) progression observée
  local p = M.char and M.char.prog[q.id .. ":" .. j]
  if p and p.h0 and o.have > p.h0 and now() - p.t0 > 20 then
    local rate = (o.have - p.h0) / (now() - p.t0)
    if rate > 0 then return rem / rate, "obs" end
  end
  local ks = killSeconds()
  local r = dropRate(o.name or "")
  if r and r > 0 then return rem / max(0.05, r) * ks, "drop" end
  if o.kind == "item" then return rem / 0.5 * ks, "est" end
  return rem * ks, "est"
end
M.ObjETA = objETA

local function questSeconds(q)
  local s = 90
  if q.complete then return 60 end
  for j, o in ipairs(q.obj) do s = s + (objETA(q, j, o) or 0) end
  return s
end

function M.Rank(list)
  local out = {}
  for _, q in ipairs(list or M.log or {}) do
    local xp, exact = questXP(q)
    local sec = questSeconds(q)
    out[#out + 1] = { q = q, xp = xp, exact = exact, sec = sec, xpm = xp / max(1, sec / 60) }
  end
  table.sort(out, function(a, b) return a.xpm > b.xpm end)
  return out
end


-- ------------------------------------------------------------------ objectifs → noms de monstres
local function targets()
  local t = {}
  for _, q in ipairs(M.log or {}) do
    if not q.complete then
      for j, o in ipairs(q.obj) do
        if not o.done and o.name and o.name ~= "" then
          if o.kind ~= "item" then t[low(o.name)] = { q = q, o = o, j = j, named = (o.need == 1) } end
          local _, _, mob = dropRate(o.name)
          if mob then t[low(mob)] = t[low(mob)] or { q = q, o = o, j = j, src = true } end
        end
      end
    end
  end
  return t
end

-- (3, 4, 14) barres de nom / cible / survol
local function observe(unit)
  if not unit then return end
  local name, guid = try(g("UnitName"), unit), try(g("UnitGUID"), unit)
  if not name or secret(name) or secret(guid) or not guid then return end
  if not try(g("UnitCanAttack"), "player", unit) and not try(g("UnitIsEnemy"), "player", unit) then return end
  if try(g("UnitIsPlayer"), unit) then return end
  local k = low(name)
  local tgt = M.targetsCache and M.targetsCache[k]
  local dead = try(g("UnitIsDead"), unit) and true or false
  -- respawn : revu vivant après une mort observée
  local dt = S.deaths[k]
  if dt and not dead and not S.seen[guid] then
    local d = now() - dt
    if d >= 30 and d <= 1800 then
      M.db.respawnData = M.db.respawnData or {}
      local r = M.db.respawnData[k] or { s = 0, n = 0, name = name }
      r.s, r.n = r.s + d, r.n + 1
      M.db.respawnData[k] = r
    end
    S.deaths[k] = nil
  end
  if not tgt then S.seen[guid] = now() return end
  if not dead then
    local tapped = try(g("UnitIsTapDenied"), unit)
    if secret(tapped) then tapped = nil end
    local p = S.plates[k] or {}
    S.plates[k] = p
    p[guid] = { t = now(), tapped = tapped and true or false }
    if tgt.named and not S.seen[guid] and opt("named") then
      M.nearNamed = { name = name, quest = tgt.q.title, t = now(), rare = try(g("UnitClassification"), unit) }
    end
  end
  S.seen[guid] = now()
end
M.Observe = observe

function M.CampState()
  local out, t = {}, now()
  for k, p in pairs(S.plates) do
    local seen, tapped = 0, 0
    for guid, e in pairs(p) do
      if t - e.t > 120 then p[guid] = nil else
        seen = seen + 1
        if e.tapped then tapped = tapped + 1 end
      end
    end
    if seen >= 4 and tapped / seen >= 0.5 then out[#out + 1] = { key = k, seen = seen, tapped = tapped } end
  end
  return out
end

-- ------------------------------------------------------------------ events
-- (4.0) un seul lecteur du message d'XP : Detect publie chaque mort (« X meurt, vous gagnez… ») par l'événement DETECT
function M.OnKill(name)
  if type(name) ~= "string" or name == "" or secret(name) then return end
  S.killT[#S.killT + 1] = now()
  while #S.killT > 30 do table.remove(S.killT, 1) end
  S.lastKill = { name = name, t = now() }
  S.deaths[low(name)] = now()
end

function M:LOOT_OPENED()
  local mob = S.lastKill and now() - S.lastKill.t < 120 and S.lastKill.name or nil
  if not mob then return end
  M.db.dropData = M.db.dropData or {}
  local want = {}
  for _, q in ipairs(M.log or {}) do
    for _, o in ipairs(q.obj) do if not o.done and o.name then want[low(o.name)] = o.name end end
  end
  local got = {}
  for i = 1, tonumber((try(g("GetNumLootItems")))) or 0 do
    local _, nm, cnt = try(g("GetLootSlotInfo"), i)
    if nm and not secret(nm) then got[low(nm)] = (got[low(nm)] or 0) + (tonumber(cnt) or 1) end
  end
  local n = 0
  for _ in pairs(M.db.dropData) do n = n + 1 end
  for k, nm in pairs(want) do
    local d = M.db.dropData[k]
    -- entrée créée au premier drop vu (les objectifs « tuer » n'en ont donc jamais)
    if not d and got[k] and n < 200 then d = { k = 0, n = 0, mob = mob, name = nm } M.db.dropData[k] = d n = n + 1 end
    if d then d.k, d.n, d.mob = d.k + 1, d.n + (got[k] and 1 or 0), mob end
  end
  S.lastKill = nil  -- un seul butin par mort
end

function M:QUEST_DETAIL()
  local qid = tonumber((try(g("GetQuestID"))))
  if not qid then return end
  local text = (try(g("GetQuestText")) or "") .. " " .. (try(g("GetObjectiveText")) or "")
  if isEscortText(text) then M.char.escort[qid] = true end
  M.CaptureRewards(qid)
end
function M:QUEST_COMPLETE() M.CaptureRewards(tonumber((try(g("GetQuestID"))))) end

function M.CaptureRewards(qid)
  if not qid then return end
  local n = tonumber((try(g("GetNumQuestChoices")))) or 0
  local links = {}
  for i = 1, min(n, 10) do
    local l = try(g("GetQuestItemLink"), "choice", i)
    if l then links[#links + 1] = l end
  end
  if #links > 0 then M.char.rewards[qid] = links end
end

function M:TRAINER_SHOW() M.char.trainerLevel = level() end
function M:PLAYER_LEVEL_UP() M.Refresh(1) end
function M:NAME_PLATE_UNIT_ADDED(_, unit) observe(unit) end
function M:PLAYER_TARGET_CHANGED() observe("target") end
function M:UPDATE_MOUSEOVER_UNIT() observe("mouseover") end
function M:QUEST_LOG_UPDATE() M.Refresh(1) end
function M:PLAYER_UPDATE_RESTING() M.NoteInn() end

-- (10) mémoire des auberges (lieu où IsResting est vrai)
function M.NoteInn()
  if not try(g("IsResting")) then return end
  local z, sub = zoneName(), try(g("GetSubZoneText")) or ""
  if z == "" or secret(z) then return end
  M.char.inns[z] = { sub = sub ~= "" and sub or z, t = epoch() }
end

-- ------------------------------------------------------------------ niveau (8–11)
-- Donjons Classic (FR), tranche de niveau conseillée (ordre de grandeur ; Forever peut différer)
local DUNGEONS = {
  { "Gouffre de Ragefeu", 13, 18 }, { "Cavernes des lamentations", 17, 24 }, { "Mortemines", 17, 26 },
  { "Donjon d'Ombrecroc", 22, 30 }, { "Profondeurs de Brassenoire", 24, 32 }, { "La Prison", 24, 32 },
  { "Gnomeregan", 29, 38 }, { "Kraal de Tranchebauge", 29, 38 }, { "Monastère écarlate", 34, 45 },
  { "Souilles de Tranchebauge", 37, 46 }, { "Uldaman", 41, 51 }, { "Zul'Farrak", 44, 54 }, { "Maraudon", 46, 55 },
  { "Temple d'Atal'Hakkar", 50, 60 }, { "Profondeurs de Rochenoire", 52, 60 }, { "Scholomance", 58, 60 },
  { "Stratholme", 58, 60 }, { "Hache-tripes", 56, 60 }, { "Pic de Rochenoire", 55, 60 },
}
M.DUNGEONS = DUNGEONS
function M.DungeonsFor(pl)
  local out = {}
  for _, d in ipairs(DUNGEONS) do if pl >= d[2] - 1 and pl <= d[3] - 2 then out[#out + 1] = d end end
  return out
end

local function talentPoints()
  local n = tonumber((try(g("UnitCharacterPoints"), "player")))
  if not n then n = tonumber((try(g("GetUnspentTalentPoints")))) end
  return n or 0
end

function M.Checklist()
  local pl, items = level(), {}
  if pl % 2 == 0 and (M.char.trainerLevel or 0) < pl then
    -- (4.0) prix RÉEL lu chez le maître de classe à la dernière visite (Guichets), jamais une estimation
    local Er = T.modules and T.modules.Errands
    local nt = Er and type(Er.char) == "table" and Er.char.nextTrain
    local cost = type(nt) == "table" and tonumber(nt.level) == pl and tonumber(nt.cost) or nil
    items[#items + 1] = "entraîneur" .. ((cost and cost > 0) and (" (" .. U.Money(cost) .. ")") or "")
  end
  local tp = talentPoints()
  if tp > 0 then items[#items + 1] = tp .. " point(s) de talent" end
  return items
end

function M.HearthAdvice()
  local counts, best, bn = {}, nil, 0
  local db = questie()
  for _, q in ipairs(M.log or {}) do
    local z
    if db then local _, fz = finisher(q.id) z = fz end
    z = z or q.zone
    if z and not secret(z) then
      counts[z] = (counts[z] or 0) + 1
      if counts[z] > bn then best, bn = z, counts[z] end
    end
  end
  if not best or bn < 3 then return nil end
  local bind = try(g("GetBindLocation")) or "?"
  local inn = M.char.inns[best]
  if inn and low(inn.sub) == low(bind) then return nil end
  for z, e in pairs(M.char.inns) do if low(e.sub) == low(bind) and z == best then return nil end end
  if low(bind) == low(best) then return nil end
  return ("Foyer : lie-toi à %s (%s) — %d quêtes à rendre là-bas (actuel : %s)"):format(inn and inn.sub or "l'auberge", best, bn, bind)
end

function M.RestAdvice()
  local xpMax = tonumber((try(g("UnitXPMax"), "player"))) or 0
  local rested = tonumber((try(g("GetXPExhaustion")))) or 0
  local pct = xpMax > 0 and floor(rested * 100 / xpMax) or 0
  if try(g("IsResting")) then
    return ("Repos : déconnecte-toi ici (+5 %%/8 h, %d %% en réserve, max 150 %%)"):format(pct)
  end
  if pct >= 150 then return nil end
  local z = zoneName()
  local inn = M.char.inns[z]
  if inn then return ("Repos (%d %%) : déconnecte-toi à %s (auberge connue)"):format(pct, inn.sub) end
  return ("Repos (%d %%) : déconnecte-toi dans une auberge / ville (ta pierre : %s)"):format(pct, try(g("GetBindLocation")) or "?")
end

-- ------------------------------------------------------------------ lignes
local function respawnText(k, name)
  local r = M.db.respawnData and M.db.respawnData[k]
  local dt = S.deaths[k]
  local avg = r and r.n > 0 and r.s / r.n or nil
  if dt then
    local left = (avg or 300) - (now() - dt)
    if left > 0 then return ("Respawn de %s : ~%s (%s)"):format(name, U.Dur(left), avg and "observé" or "estimé") end
  elseif avg then
    return ("Respawn de %s : ~%s (observé ×%d)"):format(name, U.Dur(avg), r.n)
  end
end

function M.BuildLines()
  local quests, info = {}, {}
  local function add(t, text, prio, extra)
    local l = extra or {}
    l.text, l.prio = text, prio or 1
    t[#t + 1] = l
  end
  local list = M.log or {}
  M.targetsCache = targets()
  local ranked = M.Rank(list)
  M.ranked = ranked
  -- (5) escortes
  if opt("escort") then
    for _, q in ipairs(list) do
      if q.escort and not q.complete and not q.failed then   -- (QUESTQA) escorte échouée : « a échoué » (Quests)
        local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end  -- secret en combat
        local hp = (num(try(g("UnitHealth"), "player")) or 1) / max(1, num(try(g("UnitHealthMax"), "player")) or 1)
        local miss = {}
        if hp < 0.95 then miss[#miss + 1] = "PV" end
        local pm = num(try(g("UnitPowerMax"), "player")) or 0
        if pm > 0 and (num(try(g("UnitPower"), "player")) or 0) / pm < 0.9 then miss[#miss + 1] = "mana" end
        if T:PlayerClass() == "HUNTER" and not try(g("UnitExists"), "pet") then miss[#miss + 1] = "familier" end
        add(quests, ("Escorte « %s » : prépare-toi avant de lancer%s"):format(cut(q.title, 30),
          #miss > 0 and (" (manque : " .. table.concat(miss, ", ") .. ")") or " — prêt"), #miss > 0 and 2 or 1,
          { tooltip = { "Escorte : PV/mana pleins, familier en vie, temps de recharge prêts,", "zone dégagée, groupe à côté. Le PNJ ne t'attend pas." } })
      end
    end
  end
  -- (4) PNJ nommé
  if opt("named") and M.nearNamed and now() - M.nearNamed.t < 60 then
    add(quests, ("PNJ de quête en vue : %s (« %s »)"):format(M.nearNamed.name, cut(M.nearNamed.quest, 28)), 2)
  end
  -- (3) camp saturé
  if opt("camp") then
    for _, c in ipairs(M.CampState()) do
      local tgt = M.targetsCache[c.key]
      local alt
      for _, r in ipairs(ranked) do if r.q ~= (tgt and tgt.q) and not r.q.complete then alt = r.q.title break end end
      add(quests, ("Trop de monde sur %s : reviens dans 5 min%s"):format(
        tgt and tgt.o.name or c.key, alt and (" ou fais « " .. cut(alt, 24) .. " »") or ""), 2)
    end
  end
  -- (1) classement XP/min
  if opt("xpRank") then
    for i, r in ipairs(ranked) do
      if i > 5 then break end
      local Pl = T.Plain
      add(quests, ("%d. %s — %s"):format(i, cut(r.q.title, 26), r.q.complete and "prête à rendre"
        or ((Pl and Pl.Minutes(r.sec)) or ("~" .. U.Dur(r.sec)))), 1, { rank = i, title = r.q.title, xpm = floor(r.xpm) })
    end
  end
  -- (13, 15, 14) objectifs
  for _, q in ipairs(list) do
    if not q.complete then
      for j, o in ipairs(q.obj) do
        if not o.done and o.need > 1 then
          local parts = {}
          local Pl = T.Plain
          if opt("drops") then
            local r, _, mob = dropRate(o.name or "")
            if r and r > 0 then
              local k = math.ceil((o.need - o.have) / max(0.05, r))
              local who = mob and (Pl and Pl.Plural(mob, k) or mob) or "monstres"
              parts[#parts + 1] = ("encore ~%d %s à tuer"):format(k, who)
            end
          end
          if opt("eta") then
            local s = objETA(q, j, o)
            local tm = (Pl and Pl.Minutes(s)) or ("~" .. U.Dur(s))
            parts[#parts + 1] = #parts > 0 and ("(" .. tm .. ")") or tm
          end
          if #parts > 0 then add(quests, ("%s %d/%d : %s"):format(cut(o.name or q.title, 24), o.have, o.need, table.concat(parts, " ")), 1, { eta = true }) end
        end
      end
    end
  end
  if opt("respawn") then
    for k, tgt in pairs(M.targetsCache) do
      local txt = respawnText(k, tgt.o.name or k)
      if txt then add(quests, txt, 1) end
    end
  end
  -- (12) récompense connue
  if opt("rewards") then
    for _, q in ipairs(list) do
      local links = M.char.rewards[q.id]
      if links then
        local best, bd, bslot, bv, bl = nil, nil, nil, -1, nil
        for _, l in ipairs(links) do
          local d, sl = U.Upgrade(l)
          if d and (not bd or d > bd) then best, bd, bslot = l, d, sl end
          local sell = select(11, itemInfo(l)) or 0
          if sell > bv then bv, bl = sell, l end
        end
        local name = function(l) return (itemInfo(l)) or tostring(l):match("%[(.-)%]") or "?" end
        if best then add(quests, ("Récompense « %s » : prends %s (+%d niv. %s)"):format(cut(q.title, 22), name(best), bd, bslot), 1)
        elseif bl then add(quests, ("Récompense « %s » : rien de mieux, prends %s (vente %s)"):format(cut(q.title, 22), name(bl), U.Money(bv)), 1) end
      end
    end
  end
  -- (6, 7) Questie : chaînes et relais
  if questie() and (opt("chains") or opt("breadcrumbs")) then
    local inLog, done = {}, questieDone()
    for _, q in ipairs(list) do inLog[q.id] = true end
    for _, q in ipairs(list) do
      if opt("chains") then
        local nx = tonumber(qq(q.id, "nextQuestInChain"))
        local Qm = mod("Quests")
        local qdb = Qm and Qm.QuestieDB and Qm.QuestieDB()
        -- (QUESTQA) suite faisable par toi (faction / classe / pas déjà faite) et faisable seul
        local doable = not (qdb and Qm.Doable and nx and not done[nx] and Qm.Doable(qdb, nx) == false)
          and not (Qm and Qm.SoloOK and nx and not Qm.SoloOK(nx))
        if nx and nx > 0 and not inLog[nx] and not done[nx] and doable then
          local who, where = starter(nx)
          add(quests, ("Chaîne : après « %s » → « %s »%s"):format(cut(q.title, 30), cut(qq(nx, "name") or ("#" .. nx), 26),
            who and (" chez " .. who .. (where and (" (" .. where .. ")") or "")) or ""), 1)
        end
      end
      if opt("breadcrumbs") then
        local to = tonumber(qq(q.id, "breadcrumbForQuestId"))
        if to and to > 0 then
          if inLog[to] or done[to] then
            if not mod("Abandon") then add(quests, ("Relais inutile : « %s » (la suite est déjà prise/faite) → abandonne-la"):format(cut(q.title, 28)), 2) end
          else
            add(quests, ("Relais : « %s » mène à « %s »"):format(cut(q.title, 24), cut(qq(to, "name") or ("#" .. to), 24)), 1)
          end
        end
      end
    end
  end
  -- (8) donjons
  if opt("dungeons") then
    local pl = level()
    local ds = M.DungeonsFor(pl)
    if #ds > 0 then
      local Q = mod("Quests")
      local scan = Q and Q.QuestieScan and try(Q.QuestieScan) or nil
      local names = {}
      for i, d in ipairs(ds) do if i <= 3 then names[#names + 1] = d[1] end end
      local take = {}
      if type(scan) == "table" then
        for i, e in ipairs(scan.dungeons or {}) do
          if i > 3 then break end
          take[#take + 1] = cut(e.title or ("#" .. tostring(e.id)), 22) .. (e.giver and (" (" .. e.giver .. ")") or "")
        end
      end
      add(info, ("Donjons niv %d : %s%s"):format(pl, table.concat(names, ", "),
        #take > 0 and (" — prends avant : " .. table.concat(take, ", ")) or " — prends leurs quêtes avant d'y aller"), 1,
        { short = ("Prends les quêtes de donjon avant d'y aller : %s"):format(table.concat(names, ", ")),
          tooltip = #take > 0 and ("À prendre : " .. table.concat(take, ", ")) or nil })
    end
  end
  -- (9) checklist
  if opt("checklist") then
    local items = M.Checklist()
    if #items > 0 then add(info, ("Niveau %d, à faire : %s"):format(level(), table.concat(items, " · ")), #items >= 2 and 2 or 1) end
  end
  if opt("hearth") then local h = M.HearthAdvice() if h then add(info, h, 1) end end
  if opt("rested") then local r = M.RestAdvice() if r then add(info, r, 1) end end
  -- urgent d'abord (ordre d'origine conservé à prio égale), puis plafond
  for i, l in ipairs(quests) do l._i = i end
  table.sort(quests, function(a, b) if a.prio ~= b.prio then return a.prio > b.prio end return a._i < b._i end)
  local cap = tonumber(M.db.maxLines) or 12
  while #quests > cap do table.remove(quests) end
  return quests, info
end

-- suivi de progression (13) : premier relevé de chaque objectif, remis à zéro quand il recule
local function trackProgress(list)
  local keep = {}
  for _, q in ipairs(list) do
    for j, o in ipairs(q.obj) do
      local k = q.id .. ":" .. j
      keep[k] = true
      local p = M.char.prog[k]
      if not p or o.have < (p.h or 0) or (now() - (p.t0 or 0)) > 1800 or (p.t0 or 0) > now() then
        p = { h0 = o.have, t0 = now() }
        M.char.prog[k] = p
      end
      p.h = o.have
    end
  end
  for k in pairs(M.char.prog) do if not keep[k] then M.char.prog[k] = nil end end
  local ids = {}
  for _, q in ipairs(list) do ids[q.id] = true end
  for id in pairs(M.char.rewards) do if not ids[id] then M.char.rewards[id] = nil end end
  for id in pairs(M.char.escort) do if not ids[id] then M.char.escort[id] = nil end end
end

local lastUpdate = 0
function M.Update(force)
  if not M:IsEnabled() then return end
  if inCombat() then return end                 -- Forever : valeurs masquées en combat
  if not force and now() - lastUpdate < 2 then return end
  lastUpdate = now()
  local list = M.ScanLog()
  trackProgress(list)
  M.NoteInn()
  local quests, info = M.BuildLines()
  M.lines = { quests = quests, info = info }
  U.Out(M, "quests", M.Display(quests), "Pro")
  U.Out(M, "info", M.Display(info), "Pro")
end

--- Règle d'affichage (README « Où va quelle info ») : le classement XP/min tient en UNE ligne (top 5 dans
--- l'infobulle) ; les temps par objectif ne sont pas répétés quand « À faire » (Todo) montre déjà les étapes
--- avec leur durée ; une ligne peut avoir une forme courte (`short`, verbe d'abord). M.lines garde tout.
function M.Display(list)
  local Td = mod("Todo")
  local todo = Td and Td.Active and try(Td.Active)
  local out, rank = {}, nil
  for _, l in ipairs(list or {}) do
    if l.rank then
      if not rank then
        rank = { text = ("Meilleure quête : « %s » (%d XP/min)"):format(cut(l.title or "", 30), l.xpm or 0), prio = 1,
          tooltip = { "Quêtes du journal, la plus rentable d'abord :" } }
        out[#out + 1] = rank
      end
      rank.tooltip[#rank.tooltip + 1] = l.text
    elseif not (l.eta and todo) then
      out[#out + 1] = l.short and { text = l.short, prio = l.prio, tooltip = l.tooltip or l.text, onClick = l.onClick } or l
    end
  end
  return out
end

local pending = false
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 0.5, function() pending = false pcall(M.Update, true) end)
end

function M:AutoRefresh()
  if inCombat() then return end
  for i = 1, 20 do local u = "nameplate" .. i if try(g("UnitExists"), u) then observe(u) end end
  M.Update()
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local top = {}
  for i, r in ipairs(M.ranked or {}) do
    if i > 5 then break end
    top[#top + 1] = { title = r.q.title, xp = r.xp, minutes = floor(r.sec / 60), xpm = floor(r.xpm) }
  end
  local A = mod("Abandon")
  s.pro = { top = top, abandon = A and A.Count and A.Count() or 0, checklist = M.Checklist(), updated = epoch() }
end

function M:OnEnable()
  S.start = now()
  for _, ev in ipairs({ "LOOT_OPENED", "QUEST_DETAIL", "QUEST_COMPLETE", "TRAINER_SHOW",
    "PLAYER_LEVEL_UP", "NAME_PLATE_UNIT_ADDED", "PLAYER_TARGET_CHANGED", "UPDATE_MOUSEOVER_UNIT", "QUEST_LOG_UPDATE",
    "PLAYER_UPDATE_RESTING" }) do
    M:RegisterEvent(ev)
  end
  M:On("SUMMARY", function() pcall(M.WriteSummary, M) end)
  M:On("COMBAT_END", function() M.Refresh(1) end)
  M:On("DETECT", function(_, _, kind, data) if kind == "kill" and type(data) == "table" then M.OnKill(data.name) end end)
  if T.RegisterSlash then
    T:RegisterSlash("pro", function() M.Update(true) end, "quêtes pro : recalcule (ordre, temps, chaînes…)")
  end
  M.Refresh(2)
end

function M:OnDisable()
  if T.SetTabLines then
    pcall(T.SetTabLines, T, "quests", {}, "Pro")
    pcall(T.SetTabLines, T, "info", {}, "Pro")
  end
end
function M:OnOptionChanged() M.Refresh(0.2) end

M.State = function() return S end
TreeAI.Pro = M
