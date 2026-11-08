-- TreeAI — module Quêtes (agent Q). PvE / leveling. Informatif + aides « un clic » seulement :
-- rien n'est automatisé (pas d'abandon, pas d'acceptation, pas de rendu automatique).
--  * à rendre, groupé par zone (+ rappel en entrant dans la zone / près du PNJ si l'API donne la distance)
--  * objectifs restants compacts, toasts de progression « Sang de lion : 2/10 »
--  * « finis ici avant de partir » en quittant une zone avec des objectifs inachevés
--  * quêtes grises → proposer d'abandonner (clic = ouvre la quête, le joueur clique « Abandonner » lui-même)
--  * rouges / élites / groupe → « à faire à 4 » (+ amis qui l'ont, via le module Group)
--  * bouton sécurisé (SecureActionButtonTemplate, type=item) pour l'objet de quête utilisable : créé et
--    modifié HORS COMBAT uniquement, utilisé par un clic du joueur (événement matériel)
--  * butin / objets de quête, objets qui lancent une quête, ligne « Étape : … », /tai quetes, XP restante
--  * si Questie est chargé : lecture SEULE de sa base en mémoire (pcall partout, jamais d'écriture, aucun
--    hook, aucune donnée copiée dans nos fichiers) → quêtes disponibles ici, nouvelles quêtes au niveau
--    suivant, suite d'une chaîne, quêtes de donjon, qui lâche l'objet demandé. Sans Questie : rien.
--  * pas de doublon avec Questie / guides : pas d'épingles carte, pas d'itinéraire, pas de lignes
--    d'infobulle si Questie les fait déjà.
-- Résumé : TreeAIDB.chars[perso].summary.quests (titres, niveaux, objectifs faits/total, sans nom de joueur).
-- Lua 5.1 ; aucune globale.
local _, ns = ...
ns = ns or {}

local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local M = T:RegisterModule("Quests", {
  title = "Quêtes",
  defaults = { enabled = true, toasts = true, itemButton = true, tooltip = true, nextStep = true,
               greyHint = true, zoneHints = true, questieHints = true },
  charDefaults = { btn = { point = "CENTER", x = -260, y = -140 } },
  options = {
    { key = "nextStep", type = "toggle", label = "Ligne « Étape : … » dans le panneau" },
    { key = "toasts", type = "toggle", label = "Progression des objectifs (Sang de lion : 2/10)" },
    { key = "zoneHints", type = "toggle", label = "Rappels « à rendre » / « finis ici avant de partir »" },
    { key = "greyHint", type = "toggle", label = "Proposer d'abandonner les quêtes grises" },
    { key = "itemButton", type = "toggle", label = "Bouton d'objet de quête (clic = utiliser)" },
    { key = "tooltip", type = "toggle", label = "Infobulles : objet/monstre utile pour une quête" },
    { key = "questieHints", type = "toggle", label = "Conseils tirés de Questie (si installé)" },
  },
})
ns.Quests = M

local API = T.API or {}
local floor, max, min = math.floor, math.max, math.min

-- ------------------------------------------------------------------ utilitaires
-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function now() return try(_G.GetTime) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function trim(s) return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")) end
local function low(s) return trim(s):lower() end
local function cfg(k) return not (M.db and M.db[k] == false) end
local function playerLevel() return try(_G.UnitLevel, "player") or 1 end
local function curZone() return try(_G.GetRealZoneText) or try(_G.GetZoneText) or "" end
local function cut(s, n) s = tostring(s or "") if #s > n then return s:sub(1, n - 1) .. "…" end return s end
local function fmtNum(n)
  local s = tostring(floor((n or 0) + 0.5))
  local out = s:reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return trim(out)
end
local function say(text, prio, opts)
  opts = opts or {}
  if opts.sound == nil then opts.sound = (prio or 1) >= 2 end
  return M:Suggest(opts.id or "x", text, prio or 1, opts)
end
local function suggest(id, text, prio, opts)
  opts = opts or {}
  opts.id = id
  local ok = pcall(say, text, prio, opts)
  return ok
end
local function clear(id) pcall(M.Clear, M, id) end

local function mapName(mapID)
  if not mapID or not (_G.C_Map and _G.C_Map.GetMapInfo) then return nil end
  local info = try(_G.C_Map.GetMapInfo, mapID)
  return info and info.name or nil
end

-- addons tiers (détection seulement, jamais modifiés)
local function addonLoaded(name)
  if _G.C_AddOns and _G.C_AddOns.IsAddOnLoaded then
    local r = try(_G.C_AddOns.IsAddOnLoaded, name)
    if r ~= nil then return r and true or false end
  end
  return try(_G.IsAddOnLoaded, name) and true or false
end
local others = {}
function M.DetectAddons()
  others = { questie = addonLoaded("Questie") or (type(_G.QuestieLoader) == "table") }
  local n = (_G.C_AddOns and try(_G.C_AddOns.GetNumAddOns)) or try(_G.GetNumAddOns) or 0
  for i = 1, n do
    local name = (_G.C_AddOns and try(_G.C_AddOns.GetAddOnInfo, i)) or try(_G.GetAddOnInfo, i)
    if type(name) == "string" and addonLoaded(name) then
      local l = name:lower()
      if l:find("adventure") and l:find("guide") then others.guide = name end
      if l:find("shortest") and l:find("path") then others.route = name end
    end
  end
  return others
end
M.others = function() return others end

-- ------------------------------------------------------------------ difficulté
-- Niveau gris (Classic) ; API si présente (UnitQuestTrivialLevelRange moderne, GetQuestGreenRange ancien).
function M.GreyLevel(pl)
  local range = try(_G.UnitQuestTrivialLevelRange, "player") or try(_G.GetQuestGreenRange, "player")
  if type(range) == "number" and range > 0 then return pl - range end
  if pl <= 5 then return 0 elseif pl <= 39 then return pl - floor(pl / 10) - 5
  elseif pl <= 59 then return pl - floor(pl / 5) - 1 end
  return pl - 9
end
function M.Color(ql, pl)
  if not ql or ql <= 0 then return "?" end
  if ql >= pl + 5 then return "rouge" elseif ql >= pl + 3 then return "orange"
  elseif ql >= pl - 2 then return "jaune" elseif ql > M.GreyLevel(pl) then return "vert" end
  return "gris"
end

-- ------------------------------------------------------------------ objectifs
-- « Sang de lion : 2/10 » (Classic) ou « 2/10 Sang de lion » (moderne).
function M.ParseObjective(text)
  text = trim(text)
  local name, h, n = text:match("^(.-)%s*:%s*(%d+)%s*/%s*(%d+)%s*$")
  if not name then h, n, name = text:match("^(%d+)%s*/%s*(%d+)%s+(.+)$") end
  if not name or name == "" then name = text end
  return trim(name), tonumber(h), tonumber(n)
end
-- nom de monstre depuis « Ours enragé tué » / « Gnolls tués » / « Ogre slain »
local function mobName(objName)
  local s = low(objName)
  s = s:gsub("%s+tu[ée]+s?$", ""):gsub("%s+slain$", ""):gsub("%s+tu[ée]+e?s?$", "")
  return s
end

-- type d'objectif (API : "monster", "item", "object", "event", "reputation", "log"…) → m/i/o/e/r/x
local OBJ_T = { monster = "m", item = "i", object = "o", event = "e", reputation = "r", log = "x", player = "m" }
M.OBJ_T = OBJ_T
local function newObj(text, finished, have, need, otype)
  local name, h, n = M.ParseObjective(text)
  have, need = tonumber(have) or h, tonumber(need) or n
  if need and need <= 0 then need = nil end
  if not need then need, have = 1, finished and 1 or 0 end
  if finished then have = need end
  return { text = trim(text), name = name, have = min(have or 0, need), need = need, finished = finished and true or false,
           t = OBJ_T[tostring(otype or ""):lower()] }
end

-- ------------------------------------------------------------------ scan du journal
local xpCache = {}
local function rewardXP(q, modern)
  if xpCache[q.id] ~= nil then return xpCache[q.id] or nil end
  local x
  if modern then
    x = try(_G.GetQuestLogRewardXP, q.id)
  elseif _G.GetQuestLogRewardXP then
    -- (0.2) jamais SelectQuestLogEntry : changer la sélection du journal « contamine » l'interface de
    -- Blizzard, qui bloque ensuite Abandonner / Partager / Rendre (« TreeAI a été bloqué… »)
    x = try(_G.GetQuestLogRewardXP, q.id)
  end
  x = tonumber(x)
  xpCache[q.id] = (x and x > 0) and x or false
  return xpCache[q.id] or nil
end

local function tagInfo(id)
  local QL = _G.C_QuestLog
  local tagID, tagName, elite
  if QL and QL.GetQuestTagInfo then
    local t = try(QL.GetQuestTagInfo, id)
    if type(t) == "table" then tagID, tagName, elite = t.tagID, t.tagName, t.isElite end
  elseif _G.GetQuestTagInfo then
    tagID, tagName = try(_G.GetQuestTagInfo, id)
  end
  local n = low(tagName)
  elite = elite or n:find("lite") ~= nil
  local group = tagID == 1 or n:find("groupe") ~= nil or n:find("group") ~= nil
  local dungeon = tagID == 81 or tagID == 62 or n:find("donjon") ~= nil or n:find("raid") ~= nil
  return elite, group, dungeon
end

local function questItem(index)
  if not _G.GetQuestLogSpecialItemInfo then return nil end
  local link, icon, charges, showComplete = try(_G.GetQuestLogSpecialItemInfo, index)
  if not link then return nil end
  local itemID = tonumber(tostring(link):match("item:(%d+)"))
  if not itemID then return nil end
  return { link = link, id = itemID, icon = icon, charges = charges, showComplete = showComplete and true or false }
end

local function watched(q, modern)
  local QL = _G.C_QuestLog
  if modern and QL and QL.GetQuestWatchType then return try(QL.GetQuestWatchType, q.id) ~= nil end
  return try(_G.IsQuestWatched, q.index) and true or false
end

local function distance(q)
  local QL = _G.C_QuestLog
  if not (QL and QL.GetDistanceSqToQuest) then return nil end
  local d2, onCont = try(QL.GetDistanceSqToQuest, q.id)
  if type(d2) == "number" and d2 >= 0 and onCont ~= false then return math.sqrt(d2) end
  return nil
end

local function finish(q, pl, modern)
  q.done, q.total = 0, #q.obj
  local have, need = 0, 0
  for _, o in ipairs(q.obj) do
    if o.finished then q.done = q.done + 1 end
    have, need = have + o.have, need + o.need
  end
  q.ratio = q.complete and 1 or (need > 0 and have / need or 0)
  q.color = M.Color(q.level, pl)
  q.grey = q.color == "gris"
  q.elite, q.groupTag, q.dungeon = tagInfo(q.id)
  q.hard = (q.color == "rouge") or q.elite or q.groupTag or ((tonumber(q.suggestedGroup) or 0) > 1)
  q.item = questItem(q.index)
  q.xp = rewardXP(q, modern)
  q.watched = watched(q, modern)
  q.dist = distance(q)
  if not q.zone or q.zone == "" then
    local mid = try(_G.GetQuestUiMapID, q.id)
    q.zone = mapName(mid)
  end
  -- zone de remise (moderne : prochain point de passage = PNJ de fin quand la quête est finie)
  q.turnZone = q.zone
  if q.complete and _G.C_QuestLog and _G.C_QuestLog.GetNextWaypoint then
    local mid = try(_G.C_QuestLog.GetNextWaypoint, q.id)
    q.turnZone = mapName(mid) or q.zone
  end
  q.zone = q.zone or "?"
  q.turnZone = q.turnZone or q.zone
  return q
end

-- Retourne la liste des quêtes (sans en-têtes). C_QuestLog d'abord, API classique en secours.
function M.Scan()
  local list, pl = {}, playerLevel()
  local QL = _G.C_QuestLog
  if QL and QL.GetNumQuestLogEntries and QL.GetInfo then
    local n = try(QL.GetNumQuestLogEntries) or 0
    local header
    for i = 1, n do
      local info = try(QL.GetInfo, i)
      if type(info) == "table" then
        if info.isHeader then header = info.title
        elseif info.questID and not info.isHidden and not info.isTask then
          local id = info.questID
          local q = { id = id, index = i, title = info.title or "?", level = tonumber(info.level) or 0,
                      zone = header, suggestedGroup = info.suggestedGroup, obj = {} }
          q.complete = (try(QL.IsComplete, id) or try(QL.ReadyForTurnIn, id)) and true or false
          q.failed = try(QL.IsFailed, id) and true or false
          for _, o in ipairs(try(QL.GetQuestObjectives, id) or {}) do
            if type(o) == "table" and o.text then
              q.obj[#q.obj + 1] = newObj(o.text, o.finished, o.numFulfilled, o.numRequired, o.type)
            end
          end
          list[#list + 1] = finish(q, pl, true)
        end
      end
    end
  elseif _G.GetNumQuestLogEntries and _G.GetQuestLogTitle then
    local n = try(_G.GetNumQuestLogEntries) or 0
    local header
    for i = 1, n do
      local title, ql, sg, isHeader, _, isComplete, _, id = try(_G.GetQuestLogTitle, i)
      if title then
        if isHeader then header = title
        else
          local q = { id = tonumber(id) or (100000 + i), index = i, title = title, level = tonumber(ql) or 0,
                      zone = header, suggestedGroup = sg, obj = {} }
          q.complete = isComplete == 1 or isComplete == true
          q.failed = isComplete == -1
          local nb = try(_G.GetNumQuestLeaderBoards, i) or 0
          for j = 1, nb do
            local text, otype, finished = try(_G.GetQuestLogLeaderBoard, j, i)
            if text then q.obj[#q.obj + 1] = newObj(text, finished, nil, nil, otype) end
          end
          if not q.complete and nb > 0 then
            local all = true
            for _, o in ipairs(q.obj) do if not o.finished then all = false end end
            q.complete = all
          end
          list[#list + 1] = finish(q, pl, false)
        end
      end
    end
  end
  return list
end

-- objectifs restants, compacts : « Sang de lion 2/10, Ours tué 4/10 »
function M.Remaining(q, maxN)
  local out = {}
  for _, o in ipairs(q.obj or {}) do
    if not o.finished then
      out[#out + 1] = (o.need > 1) and (o.name .. " " .. o.have .. "/" .. o.need) or o.name
      if maxN and #out >= maxN then break end
    end
  end
  return table.concat(out, ", ")
end

-- ------------------------------------------------------------------ analyse
-- Trie et regroupe : à rendre par zone, en cours ici (plus proche de la fin d'abord), ailleurs par zone.
function M.Analyse(list, zone)
  zone = zone or curZone()
  local a = { turnIn = {}, turnInByZone = {}, here = {}, elsewhere = {}, byZone = {}, grey = {}, hard = {},
              zone = zone, count = #list }
  for _, q in ipairs(list) do
    if q.complete then
      a.turnIn[#a.turnIn + 1] = q
      local z = q.turnZone or "?"
      a.turnInByZone[z] = a.turnInByZone[z] or {}
      table.insert(a.turnInByZone[z], q)
    elseif not q.failed then
      if q.zone == zone then a.here[#a.here + 1] = q else
        a.elsewhere[#a.elsewhere + 1] = q
        a.byZone[q.zone] = (a.byZone[q.zone] or 0) + 1
      end
      if q.grey then a.grey[#a.grey + 1] = q end
      if q.hard then a.hard[#a.hard + 1] = q end
    end
  end
  table.sort(a.here, function(x, y)
    if x.ratio ~= y.ratio then return x.ratio > y.ratio end
    return (x.dist or 1e9) < (y.dist or 1e9)
  end)
  table.sort(a.turnIn, function(x, y)
    local hx, hy = x.turnZone == zone, y.turnZone == zone
    if hx ~= hy then return hx end
    return (x.dist or 1e9) < (y.dist or 1e9)
  end)
  -- zone suivante : celle qui a le plus de quêtes en cours
  local best, bn = nil, 0
  for z, n in pairs(a.byZone) do if z ~= "?" and n > bn then best, bn = z, n end end
  a.nextZone, a.nextZoneCount = best, bn
  return a
end

-- XP : récompenses connues → quêtes restantes estimées avant le niveau suivant.
function M.XPEstimate(list)
  local xp, xpMax = try(_G.UnitXP, "player") or 0, try(_G.UnitXPMax, "player") or 0
  local need = max(0, xpMax - xp)
  local sumAll, nAll, turnIn = 0, 0, 0
  for _, q in ipairs(list) do
    if q.xp then
      sumAll, nAll = sumAll + q.xp, nAll + 1
      if q.complete then turnIn = turnIn + q.xp end
    end
  end
  local e = { need = need, xpMax = xpMax, turnIn = turnIn, rested = try(_G.GetXPExhaustion) }
  if nAll > 0 then
    e.avg = sumAll / nAll
    e.quests = math.ceil(max(0, need - turnIn) / e.avg)
    e.levelsFromLog = (xpMax > 0) and (sumAll / xpMax) or nil
  end
  e.turnInPct = (xpMax > 0) and floor(turnIn * 100 / xpMax + 0.5) or 0
  return e
end

-- Amis du groupe (module Group de F) qui ont aussi la quête.
function M.FriendsWith(qid)
  local G = ns.Group or (T.GetModule and T:GetModule("Group"))
  local n, names = 0, {}
  if G and type(G.members) == "table" then
    for name, m in pairs(G.members) do
      if type(m) == "table" and type(m.quests) == "table" and m.quests[qid] then
        n = n + 1
        names[#names + 1] = tostring(name)
      end
    end
  end
  return n, names
end
local function groupSize()
  return try(_G.GetNumGroupMembers) or ((try(_G.GetNumPartyMembers) or 0) + 1)
end

-- ------------------------------------------------------------------ Questie (lecture seule)
-- On lit QuestieLoader._modules (table exposée par Questie) au lieu d'ImportModule, qui CRÉERAIT un
-- module vide s'il manque : aucune écriture dans Questie.
local function questieDB()
  if not (M.db and M.db.questieHints ~= false) then return nil end
  local L = rawget(_G, "QuestieLoader")
  if type(L) ~= "table" then return nil end
  local mods = rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) ~= "table" or type(db.QueryQuestSingle) ~= "function" then return nil end
  return db, (type(mods.ZoneDB) == "table" and mods.ZoneDB or nil)
end
M.QuestieDB = questieDB
local function qq(db, id, key) return try(db.QueryQuestSingle, id, key) end
local function qn(db, id, key) if db.QueryNPCSingle then return try(db.QueryNPCSingle, id, key) end end
local function areaName(areaID)
  if type(areaID) ~= "number" or areaID <= 0 then return nil end
  if _G.C_Map and _G.C_Map.GetAreaInfo then return try(_G.C_Map.GetAreaInfo, areaID) end
  return nil
end
local function questieComplete()
  local Q = rawget(_G, "Questie")
  local c = type(Q) == "table" and type(Q.db) == "table" and type(Q.db.char) == "table" and Q.db.char.complete
  return type(c) == "table" and c or {}
end
local function questieIds(db)
  local p = db.QuestPointers
  if type(p) ~= "table" then return nil end
  return p
end
local function starterName(db, id)
  local sb = qq(db, id, "startedBy")
  if type(sb) ~= "table" then return nil end
  local npcs = sb[1]
  if type(npcs) == "table" and npcs[1] then return qn(db, npcs[1], "name") end
  if type(sb[3]) == "table" and sb[3][1] then return API.ItemName and API.ItemName(sb[3][1]) or nil end
  return nil
end

-- DATA : sans db.IsDoable (LibQuestieDB, vieux Questie), on filtre nous-mêmes : faction / race (requiredRaces,
-- masque 2^(raceID-1), 0 = toutes), classe (requiredClasses), déjà faite (IsQuestFlaggedCompleted), prérequis
-- (preQuestSingle) faits, quête exclusive déjà faite (exclusiveTo). Sinon : quêtes de l'autre faction affichées.
local function bit(mask, i)
  mask = tonumber(mask) or 0
  if mask <= 0 or not i or i < 1 then return true end
  return math.floor(mask / 2 ^ (i - 1)) % 2 == 1
end
local function flagged(id)
  local QL = _G.C_QuestLog
  local f = (QL and QL.IsQuestFlaggedCompleted) or _G.IsQuestFlaggedCompleted
  return f and try(f, id) and true or false
end
function M.Doable(db, id)
  if db.IsDoable then return try(db.IsDoable, id) and true or false end
  if flagged(id) then return false end
  local _, _, raceID = try(_G.UnitRace, "player")
  local _, _, classID = try(_G.UnitClass, "player")
  if not bit(qq(db, id, "requiredRaces"), tonumber(raceID)) then return false end
  if not bit(qq(db, id, "requiredClasses"), tonumber(classID)) then return false end
  local pre = qq(db, id, "preQuestSingle")
  for _, p in ipairs(type(pre) == "table" and pre or { pre }) do
    if tonumber(p) and tonumber(p) > 0 and not flagged(tonumber(p)) then return false end
  end
  local ex = qq(db, id, "exclusiveTo")
  for _, x in ipairs(type(ex) == "table" and ex or {}) do if tonumber(x) and flagged(tonumber(x)) then return false end end
  return true
end

-- Quêtes disponibles (pas prises, faisables) dans la zone, + celles qui s'ouvrent au niveau suivant,
-- + quêtes de donjon de ton niveau. Résultat mis en cache par (zone, niveau).
local qCache = {}
function M.QuestieScan(force)
  local db = questieDB()
  if not db then return nil end
  local zone, pl = curZone(), playerLevel()
  local key = zone .. "#" .. pl
  if qCache.key == key and not force then return qCache.res end
  local ids = questieIds(db)
  if not ids then return nil end
  local done = questieComplete()
  local inLog = {}
  for _, q in ipairs(M.lastList or {}) do inLog[q.id] = true end
  local res = { here = {}, nextLevel = {}, dungeons = {}, zone = zone, level = pl }
  local zl, grey = low(zone), M.GreyLevel(pl)
  local count = 0
  for k, v in pairs(ids) do
    local id = (type(k) == "number") and k or tonumber(v)
    count = count + 1
    if count > 30000 then break end
    if id and not done[id] and not inLog[id] then
      local zs = qq(db, id, "zoneOrSort")
      local zname = areaName(zs)
      local rl = tonumber(qq(db, id, "requiredLevel")) or 0
      local ql = tonumber(qq(db, id, "questLevel")) or rl
      if zname and low(zname) == zl then
        if rl == pl + 1 then
          local _, _, raceID = try(_G.UnitRace, "player")
          if bit(qq(db, id, "requiredRaces"), tonumber(raceID)) then res.nextLevel[#res.nextLevel + 1] = { id = id, level = ql } end
        elseif rl <= pl and ql > grey then
          if M.Doable(db, id) then res.here[#res.here + 1] = { id = id, level = ql } end
        end
      end
      if db.IsDungeonQuest and ql >= pl - 1 and ql <= pl + 8 and rl <= pl and try(db.IsDungeonQuest, id) then
        if M.Doable(db, id) then res.dungeons[#res.dungeons + 1] = { id = id, level = ql, zone = zname } end
      end
    end
  end
  local function named(t, n)
    table.sort(t, function(a, b) return a.level < b.level end)
    local out = {}
    for i = 1, min(#t, n) do
      local e = t[i]
      e.title = qq(db, e.id, "name") or ("#" .. e.id)
      e.giver = starterName(db, e.id)
      out[#out + 1] = e
    end
    return out, #t
  end
  res.here, res.hereCount = named(res.here, 8)
  res.nextLevel, res.nextLevelCount = named(res.nextLevel, 5)
  res.dungeons, res.dungeonCount = named(res.dungeons, 6)
  qCache.key, qCache.res = key, res
  return res
end

-- Qui lâche l'objet demandé (objectifs « objet » NON finis de la quête du journal) : 2 noms max.
local T2TYPE = { m = "monster", i = "item", o = "object", e = "event", r = "reputation" }
function M.QuestieDrops(qid)
  local db = questieDB()
  local Dt = T.Detect
  if not (db and db.QueryItemSingle and type(Dt) == "table" and type(Dt.MapObjectives) == "function") then return nil end
  local q
  for _, x in ipairs(M.lastList or {}) do if x.id == qid then q = x end end
  if not q or q.complete then return nil end
  local game = {}
  for i, o in ipairs(q.obj or {}) do game[i] = { name = o.name, type = T2TYPE[o.t or ""] } end
  local map = Dt.MapObjectives(qid, game)
  local out = {}
  for i, o in ipairs(q.obj or {}) do
    local z = map[i]
    if z and z.kind == "item" and z.item and not o.finished then
      local names = {}
      for _, npc in ipairs(try(db.QueryItemSingle, z.item, "npcDrops") or {}) do
        local nm = qn(db, npc, "name")
        if nm then names[#names + 1] = nm end
        if #names >= 2 then break end
      end
      if #names > 0 then out[#out + 1] = (o.name or ("objet " .. z.item)) .. " : " .. table.concat(names, ", ") end
    end
  end
  return (#out > 0) and table.concat(out, " ; ") or nil
end

-- Suite d'une chaîne après un rendu.
function M.QuestieNext(qid)
  local db = questieDB()
  if not db then return nil end
  local nx = tonumber(qq(db, qid, "nextQuestInChain"))
  if not nx or nx <= 0 then return nil end
  local name = qq(db, nx, "name")
  if not name then return nil end
  local giver = starterName(db, nx)
  -- DATA : niveau requis de la suite (Questie requiredLevel) quand tu ne l'as pas encore
  local rl = tonumber(qq(db, nx, "requiredLevel"))
  local lvl = (rl and rl > playerLevel()) and (" (dès le niv " .. rl .. ")") or ""
  return ("Suite : « %s »%s%s"):format(name, giver and (" chez " .. giver) or "", lvl)
end

-- Chaîne (TRACK-PRO) : les n quêtes suivantes (Questie nextQuestInChain) → { {id, title, giver}, ... }, position
-- dans la chaîne (maillons avant + 1) et longueur connue. Lecture seule, bornée (boucles évitées).
local chainCache = {}
local function num(v) if v == nil then return nil end return tonumber(v) end
function M.Chain(qid, n)
  local db = questieDB()
  if not (db and qid) then return nil end
  n = n or 3
  local c = chainCache[qid]
  if c and c.n >= n then return c.next, c.pos, c.len end
  local nexts, seen, cur, after = {}, { [qid] = true }, qid, 1
  for _ = 1, 12 do
    local nx = num((qq(db, cur, "nextQuestInChain")))
    if not nx or nx <= 0 or seen[nx] then break end
    seen[nx] = true
    after = after + 1
    if #nexts < n then nexts[#nexts + 1] = { id = nx, title = qq(db, nx, "name") or ("#" .. nx), giver = starterName(db, nx) } end
    cur = nx
  end
  -- maillons précédents : preQuestSingle (un seul prérequis) remonté au plus 12 fois
  local before, p = 0, qid
  for _ = 1, 12 do
    local pre = qq(db, p, "preQuestSingle")
    local pid
    if type(pre) == "table" then pid = num(pre[1]) else pid = num(pre) end
    if not pid or pid <= 0 or seen[pid] then break end
    seen[pid] = true
    before, p = before + 1, pid
  end
  chainCache[qid] = { n = n, next = nexts, pos = before + 1, len = before + after }
  return nexts, before + 1, before + after
end

-- « Histoire » : résumé court de la description (2 phrases, ≤ 200 car.). Questie (objectivesText) d'abord ;
-- sinon journal moderne GetQuestLogQuestText(index) (Mainline : par index, SANS sélection du journal). Jamais
-- en API classique (elle lit la quête sélectionnée → il faudrait sélectionner = contamination 3.8.2).
local storyCache = {}
function M.Summarize(text, maxLen)
  text = tostring(text or ""):gsub("%$[BbNnCcRrGg]", " "):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("%s+", " ")
  text = trim(text)
  if text == "" then return nil end
  maxLen = maxLen or 200
  local out, n = "", 0
  for sentence in text:gmatch("[^%.!%?]+[%.!%?]*") do
    sentence = trim(sentence)
    if sentence ~= "" then
      if n >= 2 or #out + #sentence + 1 > maxLen then break end
      out = (out == "" and sentence) or (out .. " " .. sentence)
      n = n + 1
    end
  end
  if out == "" then out = cut(text, maxLen) end
  return out
end
function M.Story(q)
  if type(q) ~= "table" or not q.id then return nil end
  local c = storyCache[q.id]
  if c and c.title == q.title then return c.s or nil end
  local text
  local db = questieDB()
  if db then
    local ot = qq(db, q.id, "objectivesText")
    if type(ot) == "table" then
      local t = {}
      for _, l in ipairs(ot) do if type(l) == "string" then t[#t + 1] = l end end
      text = table.concat(t, " ")
    elseif type(ot) == "string" then text = ot end
  end
  local QL = _G.C_QuestLog
  if (not text or text == "") and QL and QL.GetInfo and q.index and type(_G.GetQuestLogQuestText) == "function" then
    local desc = try(_G.GetQuestLogQuestText, q.index)
    if type(desc) == "string" then text = desc end
  end
  local s = M.Summarize(text)
  if s or db then storyCache[q.id] = { title = q.title, s = s or false } end
  return s
end

-- ------------------------------------------------------------------ prochaine étape
function M.NextStep(a)
  a = a or M.last
  if not a then return nil end
  local function names(t, n)
    local o = {}
    for i = 1, min(#t, n) do o[#o + 1] = "« " .. cut(t[i].title, 28) .. " »" end
    return table.concat(o, ", ")
  end
  local here = a.turnInByZone[a.zone]
  if here and #here > 0 then
    return ("Étape : rendre %s ici%s"):format(names(here, 2), #here > 2 and (" (+" .. (#here - 2) .. ")") or "")
  end
  if #a.here > 0 then
    local q = a.here[1]
    local rem = M.Remaining(q, 2)
    return ("Étape : finir « %s »%s"):format(cut(q.title, 30), rem ~= "" and (" (" .. rem .. ")") or "")
  end
  if #a.turnIn > 0 then
    local bestZ, bestN = nil, 0
    for z, t in pairs(a.turnInByZone) do if #t > bestN then bestZ, bestN = z, #t end end
    local where = (bestZ and bestZ ~= "?" and bestZ ~= "") and (" en " .. bestZ) or ""
    return ("Étape : aller rendre %d quête%s%s"):format(bestN, bestN > 1 and "s" or "", where)
  end
  if a.nextZone then
    local tail = others.guide and (" (suis " .. others.guide .. ")") or ""
    return ("Étape : aller en %s (%d quête%s)%s"):format(a.nextZone, a.nextZoneCount, a.nextZoneCount > 1 and "s" or "", tail)
  end
  local qs = M.lastQuestie
  if qs and (qs.hereCount or 0) > 0 then
    return ("Étape : prendre %d quête%s ici (ex. « %s »)"):format(qs.hereCount, qs.hereCount > 1 and "s" or "", cut(qs.here[1].title, 28))
  end
  local L = ns.Leveling
  local hub = L and L.HubFor and try(L.HubFor, playerLevel(), try(_G.UnitFactionGroup, "player"))
  return "Étape : prendre de nouvelles quêtes" .. (hub and (" (" .. hub .. ")") or "")
end

-- ------------------------------------------------------------------ toasts / rappels
-- un plan TreeAI suivi (Planner + Itinéraire) : ses arrêts couvrent déjà les rendus
local function planActive()
  local P = T.Planner
  if not (P and P.IsEnabled and P:IsEnabled() and P.GetActiveSteps) then return false end
  local ok, st = pcall(P.GetActiveSteps, P, 1)
  return ok and st ~= nil
end
local prev = {}          -- [qid] = { complete=, obj = { have... } }
local primed = false
local function toasts(list)
  local seen = {}
  for _, q in ipairs(list) do
    seen[q.id] = true
    local p = prev[q.id]
    if primed and p and cfg("toasts") then
      if q.complete and not p.complete and planActive() then
        -- (LITE) le plan / l'itinéraire dit déjà « Arrêt n/N : Rendre « … » » : pas de deuxième ligne
      elseif q.complete and not p.complete then
        suggest("done:" .. q.id, ("Rends « %s » : terminée%s"):format(cut(q.title, 34),
          q.turnZone and (" (" .. q.turnZone .. ")") or ""), 2,
          { ttl = 20, cooldown = 60 })
      else
        for j, o in ipairs(q.obj) do
          local was = p.obj[j]
          if was and o.have > was and o.need > 1 then
            suggest("prog:" .. q.id, ("%s : %d/%d"):format(cut(o.name, 30), o.have, o.need), 1,
              { ttl = 8, cooldown = 0, sound = false })
          end
        end
      end
    end
    local o2 = {}
    for j, o in ipairs(q.obj) do o2[j] = o.have end
    prev[q.id] = { complete = q.complete, obj = o2, zone = q.zone, title = q.title }
  end
  for id in pairs(prev) do if not seen[id] then prev[id] = nil end end
  primed = true
end

local function greyHint(a)
  if not cfg("greyHint") then return end
  -- le module Abandon (verdicts expliqués) parle seul des quêtes à retirer quand il est actif
  local ab = T.modules and T.modules.Abandon
  if ab and ab._active then clear("grey") return end
  local q = a.grey[1]
  if not q then clear("grey") return end
  suggest("grey", ("« %s » est grise (niv %d) : presque pas d'XP. Clic = ouvrir pour l'abandonner."):format(
    cut(q.title, 30), q.level), 1, { ttl = 300, cooldown = 1800, sound = false,
    onClick = function() M.OpenQuest(q) end })
end

local function hardHint(a)
  local q = a.hard[1]
  if not q or groupSize() >= 3 then clear("hard") return end
  local n = M.FriendsWith(q.id)
  local why = q.elite and "élite" or (q.groupTag and "de groupe") or "rouge"
  suggest("hard", ("« %s » (%s) : à faire à 4%s."):format(cut(q.title, 30), why,
    n > 0 and (", " .. n .. " ami" .. (n > 1 and "s l'ont" or " l'a")) or ""), 1, { ttl = 600, cooldown = 3600, sound = false })
end

-- ------------------------------------------------------------------ bouton d'objet (sécurisé)
local btn
local function pickItem(list, zone)
  local best, bs
  for _, q in ipairs(list) do
    local it = q.item
    if it and (not q.complete or it.showComplete) then
      local s = (q.watched and 4 or 0) + (q.zone == zone and 2 or 0) + q.ratio
      if q.dist then s = s + max(0, 1 - q.dist / 1000) end
      if not bs or s > bs then best, bs = q, s end
    end
  end
  return best
end

local function createButton()
  if btn or inCombat() or not _G.CreateFrame then return btn end
  local ok, b = pcall(_G.CreateFrame, "Button", nil, _G.UIParent, "SecureActionButtonTemplate")
  if not ok or not b then return nil end
  btn = b
  b:SetSize(36, 36)
  local pos = (M.char and M.char.btn) or {}
  b:SetPoint(pos.point or "CENTER", _G.UIParent, pos.point or "CENTER", pos.x or -260, pos.y or -140)
  b:SetFrameStrata("MEDIUM")
  if b.RegisterForClicks then b:RegisterForClicks("AnyUp", "AnyDown") end
  b:SetMovable(true)
  if b.RegisterForDrag then b:RegisterForDrag("LeftButton") end
  b:SetScript("OnDragStart", function(self)
    if not inCombat() and _G.IsShiftKeyDown and _G.IsShiftKeyDown() then self:StartMoving() end
  end)
  b:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local p, _, _, x, y = self:GetPoint()
    if M.char then M.char.btn = { point = p or "CENTER", x = floor(x or 0), y = floor(y or 0) } end
  end)
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  b.count = b:CreateFontString(nil, "OVERLAY", "NumberFontNormal")
  b.count:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -2, 2)
  local cok, cd = pcall(_G.CreateFrame, "Cooldown", nil, b, "CooldownFrameTemplate")
  if cok and cd then cd:SetAllPoints(b) b.cd = cd end
  b:SetScript("OnEnter", function(self)
    local tt = _G.GameTooltip
    if not tt then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    if self.link then tt:SetHyperlink(self.link) end
    tt:AddLine("TreeAI : clic = utiliser (" .. (self.questTitle or "quête") .. "). Maj+glisser = déplacer.", 0.4, 0.8, 0.4, true)
    tt:Show()
  end)
  b:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
  b:Hide()
  return btn
end
M.GetItemButton = function() return btn end

local function updateCooldown()
  if not (btn and btn.cd and btn.itemID) then return end
  local s, d
  if _G.C_Container and _G.C_Container.GetItemCooldown then s, d = try(_G.C_Container.GetItemCooldown, btn.itemID) end
  if not s then s, d = try(_G.GetItemCooldown, btn.itemID) end
  if s and d and d > 0 and btn.cd.SetCooldown then pcall(btn.cd.SetCooldown, btn.cd, s, d) end
end

-- Appliquée HORS COMBAT seulement (RunOutOfCombat) : attributs + Show/Hide d'un cadre sécurisé.
function M.UpdateButton()
  local q = cfg("itemButton") and M.lastList and pickItem(M.lastList, curZone()) or nil
  local want = q and q.item and q.item.id or nil
  if btn and btn.itemID == want then
    if btn.count and q then btn.count:SetText((q.item.charges and q.item.charges > 1) and q.item.charges or "") end
    return
  end
  T:RunOutOfCombat("Quests:button", function()
    if not want then
      if btn then btn:SetAttribute("type", nil) btn:SetAttribute("item", nil) btn.itemID = nil btn:Hide() end
      return
    end
    if not createButton() then return end
    btn:SetAttribute("type", "item")
    btn:SetAttribute("item", "item:" .. want)
    btn.itemID, btn.link, btn.questTitle = want, q.item.link, q.title
    btn.icon:SetTexture(q.item.icon or (API.ItemInfo and select(10, API.ItemInfo(want))) or 134400)
    if btn.count then btn.count:SetText((q.item.charges and q.item.charges > 1) and q.item.charges or "") end
    btn:Show()
    updateCooldown()
  end)
end

-- ------------------------------------------------------------------ ouvrir une quête (pour l'abandon)
function M.OpenQuest(q)
  if not q then return end
  -- (0.2) on n'ouvre plus le journal nous-mêmes (QuestMapFrame_OpenToQuestDetails / ShowUIPanel /
  -- SetSelectedQuest depuis un addon contaminent le journal : le jeu bloquait ensuite « Abandonner »)
  M:Print(("Ouvre ton journal (L) → « %s » → « Abandonner » si tu veux la retirer."):format(q.title or "?"))
end

-- ------------------------------------------------------------------ infobulles / butin / objets
local function objectiveFor(name, mob)
  if not name or not M.lastList then return nil end
  local n = low(name)
  if n == "" then return nil end
  for _, q in ipairs(M.lastList) do
    if not q.complete then
      for _, o in ipairs(q.obj) do
        if not o.finished then
          local on = mob and mobName(o.name) or low(o.name)
          if on == n then return q, o end   -- DATA : nom exact (« Ours » ne vaut plus « Ours gris vicieux »)
        end
      end
    end
  end
  return nil
end
M.ObjectiveFor = objectiveFor

local function tipLine(tt, q, o)
  if not (tt and tt.AddLine) then return end
  local H = T.Highlight  -- (HIGHLIGHT) la surbrillance écrit déjà sa ligne : pas de doublon
  if H and H.TooltipActive and H:IsEnabled() and H.TooltipActive() then return end
  pcall(tt.AddLine, tt, ("TreeAI : « %s » %d/%d"):format(cut(q.title, 30), o.have, o.need), 0.4, 0.8, 0.4)
  if tt.Show then pcall(tt.Show, tt) end
end
-- infobulles : en combat, le nom lu dans l'infobulle peut être une valeur secrète (Forever) -> on s'abstient
local function tipName(v) if v == nil or inCombat() or (API.IsSecret and API.IsSecret(v)) then return nil end return v end
local function onItemTip(tt)
  if not cfg("tooltip") or others.questie then return end  -- Questie le fait déjà
  local name = tipName(tt and tt.GetItem and try(tt.GetItem, tt))
  if not name then return end
  local q, o = objectiveFor(name, false)
  if q then tipLine(tt, q, o) end
end
local function onUnitTip(tt)
  if not cfg("tooltip") or others.questie then return end
  local name = tipName(tt and tt.GetUnit and try(tt.GetUnit, tt))
  if not name then return end
  local q, o = objectiveFor(name, true)
  if q then tipLine(tt, q, o) end
end
local hooked = false
local function hookTooltips()
  if hooked then return end
  hooked = true
  local TDP, E = _G.TooltipDataProcessor, _G.Enum
  if TDP and TDP.AddTooltipPostCall and E and E.TooltipDataType then
    -- pcall : une erreur ici s'afficherait dans l'infobulle de Blizzard (fenêtre d'erreur Lua)
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Item, function(tt) if tt == _G.GameTooltip then pcall(onItemTip, tt) end end)
    pcall(TDP.AddTooltipPostCall, E.TooltipDataType.Unit, function(tt) if tt == _G.GameTooltip then pcall(onUnitTip, tt) end end)
  elseif _G.GameTooltip and _G.GameTooltip.HookScript then
    pcall(_G.GameTooltip.HookScript, _G.GameTooltip, "OnTooltipSetItem", onItemTip)
    pcall(_G.GameTooltip.HookScript, _G.GameTooltip, "OnTooltipSetUnit", onUnitTip)
  end
end

function M.CheckLoot()
  local n = try(_G.GetNumLootItems) or 0
  local useful = {}
  for i = 1, n do
    local _, name, _, _, _, _, isQuestItem, questID, isActive = try(_G.GetLootSlotInfo, i)
    if name then
      if questID and questID > 0 and not isActive then
        suggest("lootstart", ("Ce butin lance une quête : %s"):format(name), 2, { ttl = 30, cooldown = 60 })
      end
      local q, o = objectiveFor(name, false)
      if q then useful[#useful + 1] = ("%s (%d/%d)"):format(o.name, o.have, o.need)
      elseif isQuestItem then useful[#useful + 1] = name end
    end
  end
  if #useful > 0 then
    suggest("loot", "Butin de quête : " .. table.concat(useful, ", "), 1, { ttl = 12, cooldown = 0, sound = false })
  end
  return useful
end

local function bagQuestInfo(bag, slot)
  local C = _G.C_Container
  if C and C.GetContainerItemQuestInfo then
    local t = try(C.GetContainerItemQuestInfo, bag, slot)
    if type(t) == "table" then return t.isQuestItem, t.questID, t.isActive end
    return nil
  end
  return try(_G.GetContainerItemQuestInfo, bag, slot)
end
function M.CheckBags()
  if not API.IterBags then return {} end
  local found = {}
  local okIter, iter, s, v = pcall(API.IterBags)
  if not okIter or type(iter) ~= "function" then return found end
  for bag, slot, item in iter, s, v do
    local _, questID, isActive = bagQuestInfo(bag, slot)
    if questID and questID > 0 and not isActive then
      local done = _G.C_QuestLog and _G.C_QuestLog.IsQuestFlaggedCompleted and try(_G.C_QuestLog.IsQuestFlaggedCompleted, questID)
      if not done then
        local name = (item and item.link) or (API.ItemName and API.ItemName(item and item.itemID)) or "objet"
        found[#found + 1] = name
        suggest("starter:" .. tostring(item and item.itemID or questID),
          ("Cet objet lance une quête : %s (clic droit dans ton sac)"):format(name), 2, { ttl = 600, cooldown = 1800 })
      end
    end
  end
  return found
end

-- ------------------------------------------------------------------ résumé pour TreeAI (IA)
function M.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" or not M.lastList then return end
  local a, e = M.last, M.xp or {}
  local list = {}
  for _, q in ipairs(M.lastList) do
    if #list >= 25 then break end
    local objs = {}
    for _, o in ipairs(q.obj) do objs[#objs + 1] = cut(o.name, 40) .. " " .. o.have .. "/" .. o.need end
    list[#list + 1] = { id = q.id, title = cut(q.title, 60), level = q.level, zone = q.zone, done = q.done,
      total = q.total, complete = q.complete or nil, color = q.color, hard = q.hard or nil, grey = q.grey or nil,
      xp = q.xp, obj = objs }
  end
  local turn = {}
  for z, t in pairs(a and a.turnInByZone or {}) do turn[z] = #t end
  local qs = M.lastQuestie
  s.quests = {
    updated = try(_G.time), count = #M.lastList, complete = a and #a.turnIn or 0, zone = a and a.zone,
    turnInByZone = turn, next = M.nextText, xpNeed = e.need, xpTurnIn = e.turnIn, questsToLevel = e.quests,
    list = list,
    available = qs and { here = qs.hereCount, nextLevel = qs.nextLevelCount, dungeons = qs.dungeonCount } or nil,
    addons = { questie = others.questie or nil, guide = others.guide and true or nil },
  }
end

-- ------------------------------------------------------------------ mise à jour
local lastZone
function M.Update()
  local list = M.Scan()
  M.lastList = list
  local a = M.Analyse(list)
  M.last = a
  M.xp = M.XPEstimate(list)
  toasts(list)
  greyHint(a)
  hardHint(a)
  if not inCombat() then
    local ok, res = pcall(M.QuestieScan)
    if ok then M.lastQuestie = res end
  end
  local text = M.NextStep(a)
  if T.SetTabLines then
    -- panel with tabs: the « Étape » line heads the « Quêtes » tab (and is offered to « Maintenant »)
    clear("next")
    T:SetTabLines("quests", (cfg("nextStep") and text) and { { text = text, prio = 1, pin = true,
      tooltip = "Prochaine étape conseillée (journal de quêtes analysé)." } } or {}, "Quests")
  elseif cfg("nextStep") and text then
    if text ~= M.nextText then
      suggest("next", text, 1, { ttl = 0, cooldown = 1e6, sound = false, chat = false })
    end
  else
    clear("next")
  end
  M.nextText = text
  M.UpdateButton()
  M.WriteSummary()
  -- (LITE) Boussole / Suivi / À faire ne recalculent que si le journal a vraiment changé (ou toutes les 15 s)
  local sig = M.Signature(list)
  local t = now()
  if T.Fire and (sig ~= M._firedSig or t - (M._firedAt or -100) >= 15) then
    M._firedSig, M._firedAt = sig, t
    pcall(T.Fire, T, "QUESTS_UPDATED", list)  -- Boussole / Suivi (QUEST-PRO)
  end
  return a
end

--- empreinte du journal (quêtes, objectifs, zone) : sans les distances qui bougent à chaque pas
function M.Signature(list)
  local parts = { curZone() }
  for _, q in ipairs(list or {}) do
    local o = {}
    for i, ob in ipairs(q.obj or {}) do o[i] = (ob.have or 0) .. "/" .. (ob.need or 0) end
    parts[#parts + 1] = tostring(q.id) .. (q.complete and "C" or "") .. (q.failed and "F" or "") .. ":" .. table.concat(o, ",")
  end
  return table.concat(parts, ";")
end

local pending = false
function M.Refresh(delay)
  if pending then return end
  pending = true
  T:After(delay or 0.5, function() pending = false pcall(M.Update) end)
end

-- Changement de zone : « finis ici avant de partir » + « à rendre ici ».
function M.OnZone(newZone)
  newZone = newZone or curZone()
  local old = lastZone
  lastZone = newZone
  if not M.lastList or not cfg("zoneHints") then return end
  if old and old ~= "" and old ~= newZone then
    local left = {}
    for _, q in ipairs(M.lastList) do
      if q.zone == old and not q.complete and not q.failed and not q.grey then left[#left + 1] = q end
    end
    table.sort(left, function(x, y) return x.ratio > y.ratio end)
    if #left > 0 then
      local q = left[1]
      local rem = M.Remaining(q, 2)
      suggest("left", ("Finis « %s » avant de quitter %s%s"):format(
        cut(q.title, 28), old, #left > 1 and (" (+" .. (#left - 1) .. ")") or ""), 2,
        { ttl = 90, cooldown = 300, tooltip = rem ~= "" and ("Reste : " .. rem) or nil })
    end
  end
  local a = M.Analyse(M.lastList, newZone)
  local here = a.turnInByZone[newZone]
  if here and #here > 0 then
    local o = {}
    for i = 1, min(#here, 3) do o[#o + 1] = "« " .. cut(here[i].title, 26) .. " »" end
    suggest("turnin", ("À rendre ici (%s) : %s"):format(newZone, table.concat(o, ", ")), 2, { ttl = 180, cooldown = 300 })
  end
  local qs = M.lastQuestie
  if qs and qs.zone == newZone and (qs.nextLevelCount or 0) > 0 then
    suggest("unlock", ("Au niveau %d : %d nouvelle%s quête%s à %s."):format(playerLevel() + 1, qs.nextLevelCount,
      qs.nextLevelCount > 1 and "s" or "", qs.nextLevelCount > 1 and "s" or "", newZone), 1, { ttl = 120, cooldown = 1800, sound = false })
  end
end

-- Proche du PNJ de remise (si l'API de distance existe).
function M.CheckNear()
  if inCombat() or not M.last or not cfg("zoneHints") then return end
  for _, q in ipairs(M.last.turnIn) do
    if q.dist and q.dist < 120 then
      suggest("near", ("Tu es près de la remise de « %s »."):format(cut(q.title, 32)), 2, { ttl = 30, cooldown = 600 })
      return q
    end
  end
end

-- ------------------------------------------------------------------ rapport /tai quetes
function M.Report()
  local a = M.Update()
  local P = function(s) M:Print(s) end
  P(("|cff33cc66Quêtes|r : %d dans le journal, %d à rendre. Zone : %s"):format(a.count, #a.turnIn, a.zone))
  local zones = {}
  for z in pairs(a.turnInByZone) do zones[#zones + 1] = z end
  table.sort(zones, function(x, y) if x == a.zone then return true elseif y == a.zone then return false end return x < y end)
  for _, z in ipairs(zones) do
    local o = {}
    for _, q in ipairs(a.turnInByZone[z]) do o[#o + 1] = q.title end
    P(("À rendre : %s (%s)"):format(table.concat(o, ", "), z))
  end
  for i, q in ipairs(a.here) do
    if i > 6 then P(("… +%d ici"):format(#a.here - 6)) break end
    local flag = q.hard and " |cffff5555[à 4]|r" or (q.grey and " |cff888888[grise]|r" or "")
    P(("Ici : [%d] %s — %s%s"):format(q.level, q.title, M.Remaining(q, 3), flag))
    local drops = M.QuestieDrops(q.id)
    if drops then P("   sur : " .. drops) end
  end
  for z, n in pairs(a.byZone) do P(("Ailleurs : %s — %d quête%s"):format(z, n, n > 1 and "s" or "")) end
  for _, q in ipairs(a.hard) do
    local n, names = M.FriendsWith(q.id)
    P(("À faire à 4 : %s (niv %d, %s)%s"):format(q.title, q.level, q.color,
      n > 0 and (" — l'ont aussi : " .. table.concat(names, ", ")) or ""))
  end
  for _, q in ipairs(a.grey) do P(("Grise (à abandonner ?) : %s (niv %d)"):format(q.title, q.level)) end
  local e = M.xp or {}
  if e.turnIn and e.turnIn > 0 then
    P(("Rendre tes quêtes finies : +%s XP (%d %% du niveau)."):format(fmtNum(e.turnIn), e.turnInPct or 0))
  end
  if e.quests then P(("Encore ≈ %d quête%s pour le niveau %d."):format(e.quests, e.quests > 1 and "s" or "", playerLevel() + 1)) end
  if e.rested and e.rested > 0 then P(("Repos : %s XP bonus sur les monstres (pas sur les quêtes)."):format(fmtNum(e.rested))) end
  local qs = M.lastQuestie
  if qs then
    if (qs.hereCount or 0) > 0 then
      local o = {}
      for _, x in ipairs(qs.here) do o[#o + 1] = x.title .. (x.giver and (" (" .. x.giver .. ")") or "") end
      P(("Pas encore prises ici (%d) : %s"):format(qs.hereCount, table.concat(o, ", ")))
    end
    if (qs.nextLevelCount or 0) > 0 then
      P(("Au niveau %d : %d nouvelle(s) quête(s) ici."):format(playerLevel() + 1, qs.nextLevelCount))
    end
    if (qs.dungeonCount or 0) > 0 then
      local o = {}
      for _, x in ipairs(qs.dungeons) do o[#o + 1] = x.title .. (x.zone and (" — " .. x.zone) or "") end
      P("Quêtes de donjon à prendre avant d'y aller : " .. table.concat(o, ", "))
    end
  end
  if M.nextText then P(M.nextText) end
end

-- ------------------------------------------------------------------ cycle de vie
function M:OnEnable()
  M.DetectAddons()
  local function refresh() M.Refresh() end
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_WATCH_LIST_CHANGED",
    "QUEST_WATCH_UPDATE", "PLAYER_LEVEL_UP", "PLAYER_XP_UPDATE" }) do
    self:RegisterEvent(ev, refresh)
  end
  self:RegisterEvent("UNIT_QUEST_LOG_CHANGED", function(_, _, unit) if unit == "player" then M.Refresh() end end)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid)
    M.Refresh()
    local txt = M.QuestieNext(qid)
    if txt then suggest("chain", txt, 2, { ttl = 120, cooldown = 30 }) end
  end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function()
    T:After(1, function()
      pcall(M.Update)
      pcall(M.OnZone, curZone())
    end)
  end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function()
    lastZone = curZone()
    M.Refresh(2)
    T:After(4, function() pcall(M.CheckBags) end)
  end)
  local bagPending = false
  self:RegisterEvent("BAG_UPDATE_DELAYED", function()
    if bagPending then return end
    bagPending = true
    T:After(2, function() bagPending = false pcall(M.CheckBags) end)
  end)
  self:RegisterEvent("BAG_UPDATE_COOLDOWN", function() updateCooldown() end)
  self:RegisterEvent("LOOT_OPENED", function() pcall(M.CheckLoot) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() M.Refresh(1) end)
  self:On("SUMMARY", function() pcall(M.WriteSummary) end)
  hookTooltips()
  if not M.ticker then M.ticker = T:NewTicker(5, function() pcall(M.CheckNear) end) end
  lastZone = curZone()
  M.Refresh(1)
end

function M:OnDisable()
  if M.ticker and M.ticker.Cancel then M.ticker:Cancel() end
  M.ticker = nil
  if btn then T:RunOutOfCombat("Quests:button", function() btn:Hide() end) end
end

function M:OnOptionChanged() M.Refresh(0.2) end

local function slash() M.Report() end
T:RegisterSlash("quetes", slash, "quêtes : à rendre, objectifs, XP, étape suivante")
T:RegisterSlash("quêtes", slash)
T:RegisterSlash("quests", slash)

-- actualisation automatique (Core:StartAutoRefresh) : journal relu, conseils recalculés
function M:AutoRefresh()
  if InCombatLockdown and InCombatLockdown() then return end
  -- (LITE) les événements du journal relisent déjà tout (QUEST_LOG_UPDATE, différé 0,5 s) : filet toutes les 10 s
  local t = now()
  if t - (M._autoAt or -100) < 10 then return end
  M._autoAt = t
  pcall(M.Update)
end
