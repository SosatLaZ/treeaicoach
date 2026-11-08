-- TreeAI — module Leveling (agent F, CLEAN 4.0) : « Progression ».
--  • XP reposée : une INFO (onglet « Infos », jamais dans « Maintenant ») — « Ton repos double l'XP des monstres
--    (19 % d'un niveau) » ; « repos plein » à l'auberge. Les quêtes restent le plus rapide : on ne dit pas « tue ».
--  • Zone dangereuse pour ton niveau (monstres 3+ niveaux au-dessus d'après les niveaux de zone Classic).
--  • Points de talent à dépenser (quand le module Talents n'en parle pas), paliers 10 et 40.
--  • Compétence de l'arme que tu PORTES en retard (≥ 20 points sous le maximum).
-- Laissé aux autres : maître de classe → Guichets (prix réel lu chez lui) ; « change de zone / coin conseillé » →
-- planificateur ; quêtes trop dures → Abandon. Résumé : summary.leveling (lu par TreeAI Coach).
-- Lua 5.1, aucune globale créée, toutes les API testées.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local _, ns = ...
ns = ns or {}

local M = { name = "Leveling", title = "Progression" }
ns.Leveling = M

-- ---------------------------------------------------------------- utilitaires (copie locale)
local function core() return _G.TreeAI end
local function now() return (GetTime and GetTime()) or 0 end

local lastSent = {}
local function suggest(id, text, prio, every)
  local prev = lastSent[id]
  if prev and prev.text == text and now() - prev.t < (every or 120) then return end
  lastSent[id] = { text = text, t = now() }
  local T = core()
  if M.core and M.Suggest then
    pcall(M.Suggest, M, id, text, prio or 2, { cooldown = every or 120, ttl = math.min(every or 120, 300) })
  elseif T and T.Suggest then
    pcall(T.Suggest, T, id, text, prio or 2)
  elseif DEFAULT_CHAT_FRAME then
    DEFAULT_CHAT_FRAME:AddMessage("|cff44dd88TreeAI|r " .. text)
  end
end

local function clear(id)
  if not lastSent[id] then return end
  lastSent[id] = nil
  if M.core and M.Clear then pcall(M.Clear, M, id) return end
  local T = core()
  if not T then return end
  local f = T.ClearSuggestion or T.Unsuggest or T.RemoveSuggestion
  if f then pcall(f, T, id) end
end

local function inCombat()
  return (InCombatLockdown and InCombatLockdown()) or (UnitAffectingCombat and UnitAffectingCombat("player")) or false
end

local function charKey()
  local n = UnitName and UnitName("player") or "?"
  local r = GetRealmName and GetRealmName() or "?"
  local T = core()
  if T and T.CharKey then
    local ok, k = pcall(T.CharKey, T)
    if ok and k and k ~= "?" then return k end
  end
  return ((tostring(n) .. "-" .. tostring(r)):gsub("%s", ""))
end

local function store(section)
  -- avec le cœur : TreeAIDB.chars[perso].summary[section] (schéma v1 de l'agent B)
  local T = core()
  if T and T.db and T.Summary then
    local ok, s = pcall(T.Summary, T)
    if ok and type(s) == "table" then
      s[section] = type(s[section]) == "table" and s[section] or {}
      return s[section]
    end
  end
  if type(TreeAIDB) ~= "table" then return nil end
  -- sans le cœur : même chemin, créé à la main
  TreeAIDB.chars = type(TreeAIDB.chars) == "table" and TreeAIDB.chars or {}
  local k = charKey()
  local c = type(TreeAIDB.chars[k]) == "table" and TreeAIDB.chars[k] or {}
  TreeAIDB.chars[k] = c
  c.summary = type(c.summary) == "table" and c.summary or {}
  c.summary[section] = type(c.summary[section]) == "table" and c.summary[section] or {}
  return c.summary[section]
end

local function later(sec, fn)   -- répartiteur du cœur (jamais C_Timer directement)
  local T = core()
  if T and T.After then T:After(sec, fn) else fn() end
end

local function level() return (UnitLevel and UnitLevel("player")) or 1 end

-- ---------------------------------------------------------------- données de zones (uiMapID Classic)
-- { min, max, nom FR }. Forever peut avoir retouché des zones : simple aide, jamais bloquant.
local ZONES = {
  [1429] = { 1, 10, "Forêt d'Elwynn" },          [1426] = { 1, 10, "Dun Morogh" },
  [1438] = { 1, 10, "Teldrassil" },              [1411] = { 1, 10, "Durotar" },
  [1412] = { 1, 10, "Mulgore" },                 [1420] = { 1, 10, "Clairières de Tirisfal" },
  [1436] = { 10, 20, "Marche de l'Ouest" },      [1432] = { 10, 20, "Loch Modan" },
  [1439] = { 10, 20, "Sombrivage" },             [1413] = { 10, 25, "Les Tarides" },
  [1421] = { 10, 20, "Forêt des Pins-Argentés" },[1433] = { 15, 25, "Les Carmines" },
  [1442] = { 15, 27, "Les Serres-Rocheuses" },   [1440] = { 18, 30, "Orneval" },
  [1431] = { 18, 30, "Bois de la Pénombre" },    [1437] = { 20, 30, "Les Paluns" },
  [1424] = { 20, 30, "Contreforts de Hautebrande" }, [1441] = { 25, 35, "Mille pointes" },
  [1416] = { 30, 40, "Montagnes d'Alterac" },    [1417] = { 30, 40, "Hautes-terres d'Arathi" },
  [1443] = { 30, 40, "Désolace" },               [1434] = { 30, 45, "Vallée de Strangleronce" },
  [1445] = { 35, 45, "Marécage d'Âprefange" },   [1418] = { 35, 45, "Terres ingrates" },
  [1435] = { 35, 45, "Marais des Chagrins" },    [1444] = { 40, 50, "Féralas" },
  [1425] = { 40, 50, "Les Hinterlands" },        [1446] = { 40, 50, "Tanaris" },
  [1427] = { 43, 50, "Gorge des Vents brûlants" },[1447] = { 45, 55, "Azshara" },
  [1419] = { 45, 55, "Terres foudroyées" },      [1449] = { 48, 55, "Cratère d'Un'Goro" },
  [1448] = { 48, 55, "Gangrebois" },             [1428] = { 50, 58, "Steppes ardentes" },
  [1422] = { 51, 58, "Maleterres de l'Ouest" },  [1423] = { 53, 60, "Maleterres de l'Est" },
  [1452] = { 53, 60, "Berceau-de-l'Hiver" },     [1451] = { 55, 60, "Silithus" },
  [1430] = { 55, 60, "Défilé de Deuillevent" },
}
M.ZONES = ZONES

-- Hubs conseillés : { min, max, faction ("Alliance"/"Horde"/nil = les deux), texte }
local HUBS = {
  { 10, 15, "Alliance", "Marche de l'Ouest (Colline des Sentinelles) ou Loch Modan / Sombrivage" },
  { 10, 15, "Horde", "Les Tarides (La Croisée) ou Forêt des Pins-Argentés (Fossoyeuse)" },
  { 15, 20, "Alliance", "Les Carmines (Lakeshire), puis Sombrivage" },
  { 15, 20, "Horde", "Les Tarides, puis Serres-Rocheuses" },
  { 20, 25, "Alliance", "Orneval (Astranaar) ou Les Paluns (Menethil)" },
  { 20, 25, "Horde", "Serres-Rocheuses (Roc-Soleil) ou Hautebrande (Moulin-de-Tarren)" },
  { 25, 30, "Alliance", "Bois de la Pénombre (Sombre-Comté) et Hautebrande (Austrivage)" },
  { 25, 30, "Horde", "Mille pointes (Librevent) et Orneval (Bois-Brisé)" },
  { 30, 35, nil, "Arathi, Désolace ou Strangleronce (Baie-du-Butin)" },
  { 35, 40, nil, "Strangleronce, Âprefange, Terres ingrates" },
  { 40, 45, nil, "Tanaris (Gadgetzan), Féralas, Hinterlands" },
  { 45, 50, nil, "Hinterlands, Tanaris, Azshara, Terres foudroyées" },
  { 50, 55, nil, "Un'Goro (Refuge des Marshal), Gangrebois, Steppes ardentes" },
  { 55, 61, nil, "Maleterres, Berceau-de-l'Hiver (Long-Guet), Silithus" },
}
M.HUBS = HUBS

function M.HubFor(lvl, faction)
  for _, h in ipairs(HUBS) do
    if lvl >= h[1] and lvl < h[2] and (h[3] == nil or h[3] == faction) then return h[4] end
  end
  return nil
end

-- Avis sur la zone : seulement le DANGER (monstres bien au-dessus de toi) ; « trop facile, change de zone » est
-- le verdict du planificateur (il sait ce qui reste à faire ici). Renvoie texte, priorité (ou nil).
function M.ZoneAdvice(mapID, lvl)
  local z = mapID and ZONES[mapID]
  if not z then return nil end
  if lvl < z[1] - 2 then
    return ("Prudence dans %s : monstres niv %d-%d, toi niv %d"):format(z[3], z[1], z[2], lvl), 2
  end
  return nil
end

local function currentMap()
  if C_Map and C_Map.GetBestMapForUnit then return C_Map.GetBestMapForUnit("player") end
end

-- ---------------------------------------------------------------- quêtes (partagé avec Group.lua via ns)
local function parseCount(text)
  if type(text) ~= "string" then return nil end
  local h, n = text:match("(%d+)%s*/%s*(%d+)")
  return tonumber(h), tonumber(n)
end

-- { {id, title, level, done, obj = { {text, have, need, finished} } } }
function M.ScanQuests()
  local out = {}
  if GetNumQuestLogEntries and GetQuestLogTitle then
    local n = GetNumQuestLogEntries() or 0
    for i = 1, n do
      local title, qlvl, _, isHeader, _, isComplete, _, qid = GetQuestLogTitle(i)
      if title and not isHeader then
        local q = { id = qid, title = title, level = qlvl, done = (isComplete == 1 or isComplete == true), obj = {} }
        local nb = GetNumQuestLeaderBoards and GetNumQuestLeaderBoards(i) or 0
        for j = 1, nb do
          local text, _, finished = GetQuestLogLeaderBoard(j, i)
          local h, need = parseCount(text)
          q.obj[#q.obj + 1] = { text = text, have = h, need = need, finished = finished and true or false }
        end
        if isComplete == -1 then q.failed = true end
        out[#out + 1] = q
      end
    end
  elseif C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
    local n = C_QuestLog.GetNumQuestLogEntries() or 0
    for i = 1, n do
      local info = C_QuestLog.GetInfo(i)
      if info and not info.isHeader and info.questID then
        local q = { id = info.questID, title = info.title, level = info.level, obj = {} }
        q.done = C_QuestLog.IsComplete and C_QuestLog.IsComplete(info.questID) or false
        local objs = C_QuestLog.GetQuestObjectives and C_QuestLog.GetQuestObjectives(info.questID) or {}
        for _, o in ipairs(objs) do
          q.obj[#q.obj + 1] = { text = o.text, have = o.numFulfilled, need = o.numRequired, finished = o.finished and true or false }
        end
        out[#out + 1] = q
      end
    end
  end
  return out
end
ns.QuestScan = M.ScanQuests

-- ---------------------------------------------------------------- compétences (armes / métiers)
-- compétence d'arme de chaque type d'arme (sous-classe d'objet 2 → nom de la compétence, FR / EN)
local WEAPON_SKILL = { [0] = { "Haches à une main", "Axes" }, [1] = { "Haches à deux mains", "Two-Handed Axes" },
  [2] = { "Arcs", "Bows" }, [3] = { "Armes à feu", "Guns" }, [4] = { "Masses à une main", "Maces" },
  [5] = { "Masses à deux mains", "Two-Handed Maces" }, [6] = { "Armes d'hast", "Polearms" },
  [7] = { "Épées à une main", "Swords" }, [8] = { "Épées à deux mains", "Two-Handed Swords" }, [10] = { "Bâtons", "Staves" },
  [13] = { "Armes de pugilat", "Fist Weapons" }, [15] = { "Dagues", "Daggers" }, [16] = { "Armes de jet", "Thrown" },
  [18] = { "Arbalètes", "Crossbows" }, [19] = { "Baguettes", "Wands" } }
M.WEAPON_SKILL = WEAPON_SKILL
--- noms des compétences des armes PORTÉES (main droite, main gauche, distance) : set
local function wornWeaponSkills()
  local set = {}
  local A = core() and core().API
  for _, slot in ipairs({ 16, 17, 18 }) do
    local id = GetInventoryItemID and GetInventoryItemID("player", slot)
    local classID, sub
    if id and A and A.ItemInfo then
      local r = { pcall(A.ItemInfo, id) }
      if r[1] then classID, sub = r[13], r[14] end
    end
    local names = classID == 2 and WEAPON_SKILL[sub]
    if names then set[names[1]], set[names[2]] = true, true end
  end
  return set
end
-- Compétences d'armes : max = 5 x niveau. Renvoie toutes les lignes et celles « en retard » (armes portées seulement).
function M.ScanSkills()
  local all, behind = {}, {}
  local A = core() and core().API   -- C_SkillInfo sur Forever (GetSkillLineInfo global retiré)
  if not (A and A.SkillLines) then return all, behind end
  local lvl = level()
  local worn = wornWeaponSkills()
  for _, line in ipairs(A.SkillLines()) do
    local name, isHeader, rank, maxRank = line.name, line.isHeader, line.rank, line.maxRank
    if name and not isHeader and rank and maxRank and maxRank > 1 then
      local s = { name = name, rank = rank, max = maxRank }
      all[#all + 1] = s
      if worn[name] and maxRank == lvl * 5 and maxRank - rank >= 20 then behind[#behind + 1] = s end
    end
  end
  return all, behind
end

-- ---------------------------------------------------------------- vérifications
local function factionName()
  return UnitFactionGroup and UnitFactionGroup("player") or nil
end

function M.CheckZone(announce)
  local lvl, map = level(), currentMap()
  local text, prio = M.ZoneAdvice(map, lvl)
  local d = store("leveling")
  if d then d.mapID, d.zoneWarn = map, text end
  if text and announce then suggest("lvl_zone", text, prio, 600) elseif not text then clear("lvl_zone") end
end

--- texte d'info sur le repos (pur) : pct = repos en % d'un niveau, resting = à l'auberge / en ville
function M.RestedText(pct, resting)
  pct = tonumber(pct) or 0
  if resting and pct >= 148 then return "Ton repos est plein (150 % d'un niveau) : il ne monte plus ici" end
  if pct >= 1 then return ("Ton repos double l'XP des monstres (%d %% d'un niveau)"):format(pct) end
  return nil
end
local restLine
function M.CheckRested()
  local rested = GetXPExhaustion and GetXPExhaustion() or nil
  local maxXP = UnitXPMax and UnitXPMax("player") or 0
  local resting = IsResting and IsResting() or false
  local text
  if level() < 60 and maxXP > 0 then
    text = M.RestedText(rested and math.floor(rested * 100 / maxXP) or 0, resting)
  end
  if text == restLine then return end
  restLine = text
  -- INFO, jamais dans « Maintenant » : les quêtes restent plus rapides que tuer des monstres
  local T = core()
  if T and T.SetTabLines then
    local tip = { "Le repos double l'XP des monstres tués (pas celle des quêtes) jusqu'à épuisement.",
      "Il monte en te déconnectant à une auberge ou en ville (4 fois moins vite dehors)." }
    pcall(T.SetTabLines, T, "info", text and { { text = text, prio = 1, tooltip = tip } } or {}, "Leveling")
  end
end

local function talentPoints()
  local CT = _G.C_ClassTalents   -- Forever : arbres de talents modernes
  if CT and CT.HasUnspentTalentPoints then
    local ok, has, a, b = pcall(CT.HasUnspentTalentPoints)
    if ok then return has and math.max(1, (tonumber(a) or 0) + (tonumber(b) or 0)) or 0 end
  end
  if UnitCharacterPoints then return UnitCharacterPoints("player") or 0 end
  if GetUnspentTalentPoints then return GetUnspentTalentPoints() or 0 end
  return 0
end

function M.CheckTalents()
  local TA = core() and core().Talents   -- module Talents actif : il affiche « Prochain point : X (pourquoi) »
  if TA and TA.IsEnabled and TA:IsEnabled() and TA.db and TA.db.nowLine ~= false then clear("lvl_talents") return end
  local p = talentPoints()
  if p > 0 and not inCombat() then
    suggest("lvl_talents", p > 1 and ("Dépense tes %d points de talent (touche N)"):format(p)
      or "Dépense ton point de talent (touche N)", 2, 900)
  elseif p == 0 then
    clear("lvl_talents")
  end
end

local MILESTONES = {
  [10] = "Niveau 10 : tes talents s'ouvrent, ouvre l'arbre (touche N)",
  [40] = "Niveau 40 : va apprendre la monte et acheter ta monture en ville",
  [60] = "Niveau 60 : bravo, niveau maximum atteint",
}

function M.OnLevelUp(newLevel)
  newLevel = tonumber(newLevel) or level()
  if MILESTONES[newLevel] then suggest("lvl_milestone", MILESTONES[newLevel], 2, 60) end
  later(2, function() M.CheckZone(true) M.CheckRested() end)
end

function M.CheckSkills()
  local _, behind = M.ScanSkills()
  table.sort(behind, function(a, b) return (a.max - a.rank) > (b.max - b.rank) end)
  local s = behind[1]
  if s then
    suggest("lvl_skill", ("Monte ta compétence %s (%d/%d) : en retard, tu rates plus de coups"):format(s.name, s.rank, s.max), 1, 3600)
  else
    clear("lvl_skill")
  end
end

-- ---------------------------------------------------------------- résumé pour TreeAI
local function features()
  return {
    camping = (_G.C_Camping ~= nil) or nil,
    legacy = (_G.C_Legacy ~= nil or _G.C_LegacyPoints ~= nil) or nil,
  }
end

function M.SaveSummary()
  local d = store("leveling")
  if not d then return end
  local lvl = level()
  d.name = UnitName and UnitName("player") or nil
  d.realm = GetRealmName and GetRealmName() or nil
  d.class = UnitClass and select(2, UnitClass("player")) or nil
  d.className = UnitClass and UnitClass("player") or nil
  d.race = UnitRace and select(2, UnitRace("player")) or nil
  d.faction = factionName()
  d.level = lvl
  d.xp = UnitXP and UnitXP("player") or nil
  d.xpMax = UnitXPMax and UnitXPMax("player") or nil
  d.rested = GetXPExhaustion and GetXPExhaustion() or 0
  d.resting = IsResting and IsResting() or false
  d.zone = GetRealZoneText and GetRealZoneText() or nil
  d.subzone = GetSubZoneText and GetSubZoneText() or nil
  d.mapID = currentMap()
  d.money = GetMoney and GetMoney() or nil
  d.talentPoints = talentPoints()
  d.hint = M.HubFor(lvl, d.faction)
  d.zoneWarn = (M.ZoneAdvice(d.mapID, lvl))
  local skills = M.ScanSkills()
  d.skills = skills
  local qs = M.ScanQuests()
  local slim = {}
  for i, q in ipairs(qs) do
    if i > 25 then break end
    local objs = {}
    for _, o in ipairs(q.obj) do objs[#objs + 1] = o.text end
    slim[#slim + 1] = { id = q.id, title = q.title, level = q.level, done = q.done and true or false, obj = objs }
  end
  d.quests = slim
  d.features = features()
  d.updated = time and time() or nil
end

-- ---------------------------------------------------------------- événements
local handlers = {
  PLAYER_ENTERING_WORLD = function()
    later(4, function()
      M.CheckRested()
      M.CheckZone(true)
      M.CheckTalents()
    end)
  end,
  PLAYER_LEVEL_UP = function(newLevel) M.OnLevelUp(newLevel) end,
  PLAYER_UPDATE_RESTING = function() M.CheckRested() end,
  PLAYER_XP_UPDATE = function() M.CheckRested() end,
  UPDATE_EXHAUSTION = function() M.CheckRested() end,
  ZONE_CHANGED_NEW_AREA = function() later(1, function() M.CheckZone(true) end) end,
  CHARACTER_POINTS_CHANGED = function() M.CheckTalents() end,
  PLAYER_REGEN_ENABLED = function() later(3, function() M.CheckTalents() end) end,
  PLAYER_CAMPING = function()
    if not (IsResting and IsResting()) then
      suggest("lvl_camp", "Déconnecte-toi plutôt à une auberge : ton repos y monte 4 fois plus vite", 1, 300)
    end
  end,
  PLAYER_LOGOUT = function() pcall(M.SaveSummary) end,
}
M.handlers = handlers

-- Vérification des compétences d'armes, au plus 1 fois / 10 min, hors combat.
local lastSkillCheck = -1e9
handlers.SKILL_LINES_CHANGED = function()
  if now() - lastSkillCheck > 600 and not inCombat() then lastSkillCheck = now() M.CheckSkills() end
end

if CreateFrame then
  local f = CreateFrame("Frame")
  for ev in pairs(handlers) do pcall(f.RegisterEvent, f, ev) end
  f:SetScript("OnEvent", function(_, ev, ...)
    local h = handlers[ev]
    if M.core and M.IsEnabled and not M:IsEnabled() then return end
    if h then
      local ok, err = pcall(h, ...)
      if not ok and core() and core().Debug then pcall(core().Debug, core(), "Leveling: " .. tostring(err)) end
    end
  end)
end

do
  local T = core()
  if T and T.RegisterModule then pcall(T.RegisterModule, T, "Leveling", M) end
end
