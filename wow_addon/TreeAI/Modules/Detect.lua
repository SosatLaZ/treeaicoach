-- TreeAI — module Detect (agent DETECT) : ce que l'addon VOIT dans le monde, centralisé, API légitimes seulement.
--  1. Rares / élites / trésors : C_VignetteInfo (VIGNETTES_UPDATED, GetVignettes, GetVignetteInfo,
--     GetVignettePosition — présents sur Forever 1.60.1, famille Mainline) = position exacte sur la carte, bien
--     mieux que les barres de nom ; repli barres de nom / cible (UnitClassification) sans position exacte.
--  2. Monstres / objets de quête : barres de nom dont C_QuestLog.UnitIsRelatedToActiveQuest / UnitIsQuestBoss
--     disent « lié à une quête », + correspondance nom ↔ objectif (reste à tuer / à ramasser). Aucune infobulle.
--  3. Progression des quêtes : QUEST_LOG_UPDATE, QUEST_WATCH_UPDATE, UNIT_QUEST_LOG_CHANGED, QUEST_ACCEPTED,
--     QUEST_TURNED_IN, QUEST_REMOVED → un seul relevé (anti-rebond 0,3 s) via C_QuestLog.GetQuestObjectives ;
--     différences (objectif +1, quête finie / rendue / abandonnée) ; morts comptées par le message d'XP (Forever
--     interdit le journal de combat aux addons).
--  4. Services proches (marchand, réparation, vol, maître, aubergiste, boîte aux lettres, banque, HV, écuries) :
--     C_TaxiMap.GetTaxiNodesForMap, C_AreaPoiInfo, PNJ APPRIS quand tu leur parles (MERCHANT_SHOW, TAXIMAP_OPENED,
--     TRAINER_SHOW, MAIL_SHOW… : position du joueur à ce moment), habitants Questie (lecture seule) si présents.
--     (Le suivi de la minimap — C_Minimap.GetTrackingInfo — ne donne AUCUNE position : seulement lu pour l'état.)
--  5. Groupe : position (C_Map par unité, sinon UnitPosition, sinon position partagée par Squad), zone,
--     hors ligne / ABS / mort / fantôme, à portée.
--  6. Combat sans journal de combat : UNIT_COMBAT (coups reçus), UNIT_HEALTH, menace (UnitThreatSituation) —
--     toute valeur secrète (combat sur Forever) est ignorée, jamais de chiffre inventé.
-- API : TreeAI.Detect:Rares() :QuestUnits() :QuestUnit(unit) :Quests() :Services(kind) :Nearest(kind)
--       :NearestText(kind) :Group() :Combat() :Kills(name) :Pins() :OnChange(cb) → cb(kind, data).
-- Rien n'est fait à la place du joueur. Lua 5.1, aucune globale, toute API testée avant usage.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local D = T:RegisterModule("Detect", {
  title = "Détection",
  defaults = { enabled = true, vignettes = true, treasures = true, questUnits = true, services = true, learn = true,
               group = true, combat = true, learned = {} },
  charDefaults = {},
  options = {
    { key = "vignettes", type = "toggle", label = "Rares et élites sur la carte (vignettes du jeu)", section = "Détection" },
    { key = "treasures", type = "toggle", label = "Trésors (coffres) sur la carte", section = "Détection" },
    { key = "questUnits", type = "toggle", label = "Monstres / objets liés à mes quêtes (barres de nom)", section = "Détection" },
    { key = "services", type = "toggle", label = "Services proches (marchand, réparation, vol…)", section = "Détection" },
    { key = "learn", type = "toggle", label = "Retenir les PNJ à qui je parle (marchand, maître…)", section = "Détection" },
    { key = "group", type = "toggle", label = "Groupe : positions, hors ligne, ABS, morts", section = "Détection" },
    { key = "combat", type = "toggle", label = "Combat : coups reçus, menace, nombre d'ennemis", section = "Détection" },
  },
})
T.Detect = D

local floor, sqrt, atan2, abs, pi = math.floor, math.sqrt, math.atan2, math.abs, math.pi
local tinsert, tsort = table.insert, table.sort

-- ------------------------------------------------------------------ utilitaires (valeurs secrètes → nil)
local function secret(v)
  local f = rawget(G, "issecretvalue")
  if type(f) ~= "function" or v == nil then return false end
  local ok, r = pcall(f, v)
  return ok and r and true or false
end
local function pub(v) if secret(v) then return nil end return v end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e = pcall(fn, ...)
  if ok then return a, b, c, d, e end
  return nil
end
local function now() return try(G.GetTime) or 0 end
local function low(s) return tostring(s or ""):lower() end
local function on(k) return not (D.db and D.db[k] == false) end
local function num(v) v = pub(v) return type(v) == "number" and v or nil end
local function str(v) v = pub(v) return type(v) == "string" and v ~= "" and v or nil end
local function xy(p)
  if type(p) ~= "table" then return nil end
  if type(p.GetXY) == "function" then
    local x, y = try(p.GetXY, p)
    if x then return num(x), num(y) end
  end
  return num(p.x), num(p.y)
end
local function vec(x, y)
  local mk = rawget(G, "CreateVector2D")
  if type(mk) == "function" then local v = try(mk, x, y) if v then return v end end
  return { x = x, y = y, GetXY = function(p) return p.x, p.y end }
end
local cut = T.Cut   -- (4.0) Core : n caractères UTF-8 (« … » compris), jamais une lettre accentuée coupée

-- ------------------------------------------------------------------ abonnés
local subs = {}
--- cb(kind, data) ; kinds : "rares" (nouveaux), "questunits", "quests" (diff), "progress", "kill", "services",
--- "group" (changements d'état), "combat". Retourne une fonction pour se désabonner.
function D.OnChange(a, b)
  local cb = type(a) == "function" and a or b
  if type(cb) ~= "function" then return nil end
  subs[#subs + 1] = cb
  return function() for i = #subs, 1, -1 do if subs[i] == cb then table.remove(subs, i) end end end
end
local function emit(kind, data)
  for _, cb in ipairs(subs) do
    local ok, err = pcall(cb, kind, data)
    if not ok and D.Debug then D:Debug("Detect " .. kind .. " : " .. tostring(err)) end
  end
  if T.Fire then pcall(T.Fire, T, "DETECT", kind, data) end
end
D.Emit = emit

-- anti-rebond : un seul travail en attente par clé
local pending = {}
local function soon(key, delay, fn)
  if pending[key] then return end
  pending[key] = true
  local function run() pending[key] = nil pcall(fn) end
  if T.After then T:After(delay, run) else run() end
end

-- ------------------------------------------------------------------ position / géométrie
function D.Me()
  if try(G.IsInInstance) then return nil, "instance" end
  local CM = G.C_Map
  if not (CM and CM.GetBestMapForUnit and CM.GetPlayerMapPosition) then return nil, "api" end
  local mapID = num(try(CM.GetBestMapForUnit, "player"))
  if not mapID then return nil, "map" end
  local x, y = xy(try(CM.GetPlayerMapPosition, mapID, "player"))
  if not (x and y) or (x == 0 and y == 0) then return nil, "pos" end
  return { mapID = mapID, x = x, y = y }
end

local function geo() local C = T.Compass return type(C) == "table" and type(C.Geo) == "table" and C.Geo or nil end
local function world(mapID, x, y)
  local CM = G.C_Map
  if not (CM and CM.GetWorldPosFromMapPos and mapID and x and y) then return nil end
  local c, w = try(CM.GetWorldPosFromMapPos, mapID, vec(x, y))
  local n, wst = xy(w)
  if not (num(c) and n and wst) then return nil end
  return c, n, wst
end
--- distance (yards) + cap (0 = nord, anti-horaire) de a vers b ({mapID, x, y}) ; nil si autre continent.
function D.Vector(a, b)
  local Gm = geo()
  if Gm and Gm.Vector then return Gm.Vector(a, b) end
  if not (a and b and a.mapID and b.mapID and a.x and b.x) then return nil end
  local ca, ax, ay = world(a.mapID, a.x, a.y)
  local cb, bx, by = world(b.mapID, b.x, b.y)
  if ca and cb then
    if ca ~= cb then return nil end
    local n, w = bx - ax, by - ay
    return sqrt(n * n + w * w), atan2(w, n)
  end
  if a.mapID ~= b.mapID then return nil end
  local n, w = -(b.y - a.y) * 2000, -(b.x - a.x) * 3000
  return sqrt(n * n + w * w), atan2(w, n)
end
local DIRS = { "N", "NO", "O", "SO", "S", "SE", "E", "NE" }
function D.Cardinal(bearing)
  if not bearing then return "" end
  return DIRS[floor(((bearing % (2 * pi)) + pi / 8) / (pi / 4)) % 8 + 1]
end
function D.Dist(yd)
  if not yd then return "?" end
  local Gm = geo()
  if Gm and Gm.Dist then return Gm.Dist(yd, T.Compass and T.Compass.db and T.Compass.db.units) end
  local m = yd * 0.9144
  if m >= 1000 then return (("%.1f km"):format(m / 1000):gsub("%.", ",")) end
  return floor(m + 0.5) .. " m"
end
local function measure(list, me)
  for _, e in ipairs(list) do
    e.dist, e.bearing = nil, nil
    if me and e.mapID and e.x and e.y then e.dist, e.bearing = D.Vector(me, e) end
  end
  tsort(list, function(a, b)
    if (a.dist or 1e12) ~= (b.dist or 1e12) then return (a.dist or 1e12) < (b.dist or 1e12) end
    return tostring(a.key or a.name) < tostring(b.key or b.name)
  end)
  return list
end
D.Measure = measure

-- ------------------------------------------------------------------ 1. rares / élites / trésors
-- Atlas des vignettes (Classic / Mainline) : VignetteKill, VignetteKillElite, VignetteLoot, VignetteLootElite,
-- VignetteEvent… ; Enum.VignetteType.Treasure = 3 ; objectiveType Defeat = 1/2 (monstre à vaincre).
function D.Classify(info)
  if type(info) ~= "table" then return nil end
  local atlas = low(str(info.atlasName))
  local vt = num(info.type)
  if vt == 3 or atlas:find("loot", 1, true) or atlas:find("treasure", 1, true) or atlas:find("chest", 1, true) then
    return "treasure"
  end
  if atlas:find("killelite", 1, true) or (atlas:find("kill", 1, true) and atlas:find("elite", 1, true)) then return "rareelite" end
  if atlas:find("kill", 1, true) or (num(info.objectiveType) or 0) > 0 then return "rare" end
  if atlas:find("event", 1, true) then return "event" end
  return "vignette"
end

local V = { list = {}, byId = {}, at = -1e9, measured = -1e9, plate = {}, seen = {} }
D._v = V
local KIND_FR = { rare = "Rare", rareelite = "Rare élite", worldboss = "Boss", treasure = "Trésor", event = "Évènement",
                  vignette = "Point d'intérêt", elite = "Élite" }
D.KIND_FR = KIND_FR

function D.ScanVignettes()
  local VI = G.C_VignetteInfo
  if not (on("vignettes") or on("treasures")) or not (VI and VI.GetVignettes and VI.GetVignetteInfo) then
    return 0
  end
  local me = D.Me()
  local ids = try(VI.GetVignettes)
  local cur, list, new = {}, {}, {}
  for _, id in ipairs(type(ids) == "table" and ids or {}) do
    if not secret(id) and #list < 60 then
      local info = try(VI.GetVignetteInfo, id)
      if type(info) == "table" and not pub(info.isDead) then
        local kind = D.Classify(info)
        local wanted = (kind == "treasure" and on("treasures")) or (kind ~= "treasure" and on("vignettes"))
        if wanted then
          local e = V.byId[id]
          if not e then
            e = { key = "vig:" .. tostring(id), id = id, src = "vignette", first = now() }
            new[#new + 1] = e
          end
          e.kind, e.name = kind, str(info.name) or KIND_FR[kind]
          e.guid, e.vignetteID = str(info.objectGUID), num(info.vignetteID)
          e.atlas, e.onMinimap, e.questID = str(info.atlasName), pub(info.onMinimap), num(info.rewardQuestID)
          if me and VI.GetVignettePosition then
            local x, y = xy(try(VI.GetVignettePosition, id, me.mapID))
            if x and y and not (x == 0 and y == 0) then e.mapID, e.x, e.y = me.mapID, x, y end
          end
          cur[id] = e
          list[#list + 1] = e
        end
      end
    end
  end
  V.byId, V.at = cur, now()
  -- rares vus seulement en barre de nom (pas de vignette) : gardés 2 min
  local t = now()
  for guid, e in pairs(V.plate) do
    local dup = false
    for _, v in ipairs(list) do if v.guid and v.guid == guid then dup = true break end end
    if t - (e.t or 0) > 120 then V.plate[guid] = nil elseif not dup then list[#list + 1] = e end
  end
  V.list = measure(list, me)
  V.measured = t
  local fresh = {}
  for _, e in ipairs(new) do
    local k = e.guid or e.key
    if not V.seen[k] or t - V.seen[k] > 600 then V.seen[k] = t fresh[#fresh + 1] = e end
  end
  if #fresh > 0 then emit("rares", fresh) end
  return #list
end

-- repli barre de nom / cible : pas de position exacte (on note la position du joueur, « ± 40 m »)
function D.ScanUnitRare(unit)
  if not on("vignettes") or not unit or not try(G.UnitExists, unit) then return nil end
  if try(G.UnitIsPlayer, unit) or pub(try(G.UnitIsDead, unit)) then return nil end
  local cls = str(try(G.UnitClassification, unit))
  if cls ~= "rare" and cls ~= "rareelite" and cls ~= "worldboss" then return nil end
  local guid = str(try(G.UnitGUID, unit))
  if not guid then return nil end
  for _, v in pairs(V.byId) do if v.guid == guid then return v end end
  local t = now()
  local e = V.plate[guid]
  local isNew = not e
  if not e then
    e = { key = "plate:" .. guid, guid = guid, src = "plate", approx = true, first = t }
    V.plate[guid] = e
  end
  local me = D.Me()
  e.kind, e.unit, e.t = cls, unit, t
  e.name = str(try(G.UnitName, unit)) or e.name or KIND_FR[cls]
  e.level = num(try(G.UnitLevel, unit))
  if me then e.mapID, e.x, e.y = me.mapID, me.x, me.y end
  if isNew and (not V.seen[guid] or t - V.seen[guid] > 600) then
    V.seen[guid] = t
    V.list[#V.list + 1] = e
    emit("rares", { e })
  end
  return e
end

--- Liste des rares / élites / trésors connus (triée par distance). kind = "treasure" | "rare" | nil (tout).
function D.Rares(a, b)
  local kind = type(a) == "string" and a or b
  if now() - V.measured > 1 then V.list = measure(V.list, D.Me()) V.measured = now() end
  if not kind then return V.list end
  local out = {}
  for _, e in ipairs(V.list) do
    if e.kind == kind or (kind == "rare" and (e.kind == "rareelite" or e.kind == "worldboss")) then out[#out + 1] = e end
  end
  return out
end

--- Texte court : « Rare élite : Nom — 180 m NE »
function D.Describe(e)
  if type(e) ~= "table" then return nil end
  local where = e.dist and (" — " .. D.Dist(e.dist) .. (e.bearing and (" " .. D.Cardinal(e.bearing)) or "")) or ""
  if e.approx and e.dist then where = where .. " (±)" end
  return (KIND_FR[e.kind] or "Point") .. " : " .. cut(e.name or "?", 32)
    .. ((e.level and e.level > 0) and (" (niv " .. e.level .. ")") or "") .. where
end

-- ------------------------------------------------------------------ 3. quêtes (relevé + différences)
local Q = { byId = {}, list = {}, at = -1e9, turned = {}, kills = {} }
D._q = Q

local function objectivesOf(qid, index)
  local out = {}
  local QL = G.C_QuestLog
  if qid and QL and QL.GetQuestObjectives then
    for _, o in ipairs(try(QL.GetQuestObjectives, qid) or {}) do
      if type(o) == "table" then
        out[#out + 1] = { text = str(o.text) or "", type = str(o.type), finished = pub(o.finished) and true or false,
          have = num(o.numFulfilled), need = num(o.numRequired) }
      end
    end
    return out
  end
  local NB, LB = rawget(G, "GetNumQuestLeaderBoards"), rawget(G, "GetQuestLogLeaderBoard")
  if index and NB and LB then
    for j = 1, (num(try(NB, index)) or 0) do
      local text, typ, done = try(LB, j, index)
      if text then out[#out + 1] = { text = text, type = typ, finished = done and true or false } end
    end
  end
  return out
end
-- « Ours enragé tué : 7/8 » -> nom « ours enragé », 7, 8
function D.ParseObjective(o)
  local text = tostring(o and o.text or "")
  local h, n = text:match("(%d+)%s*/%s*(%d+)")
  local name = text:gsub("%d+%s*/%s*%d+", ""):gsub("^%s*%-%s*", ""):gsub("%s*:%s*$", ""):gsub("^%s+", ""):gsub("%s+$", "")
  name = name:gsub("%s+tués?$", ""):gsub("%s+slain$", ""):gsub("%s+tuées?$", "")
  return low(name), (o and o.have) or tonumber(h), (o and o.need) or tonumber(n)
end

function D.SnapshotQuests()
  local entries = (T.API and T.API.QuestLogEntries and T.API.QuestLogEntries()) or {}
  local byId, list = {}, {}
  for _, q in ipairs(entries) do
    local qid = num(q.questID)
    if qid and not q.isHeader then
      local e = { id = qid, title = str(q.title) or ("quête " .. qid), level = num(q.level),
        complete = q.isComplete and q.isComplete ~= 0 and q.isComplete ~= -1 or false, objectives = objectivesOf(qid, q.index) }
      for _, o in ipairs(e.objectives) do
        local nm, h, n = D.ParseObjective(o)
        o.name, o.have, o.need = nm, h or (o.finished and 1 or 0), n or 1
      end
      byId[qid] = e
      list[#list + 1] = e
    end
  end
  return byId, list
end

function D.DiffQuests(old, new)
  local diff = { accepted = {}, removed = {}, turnedIn = {}, completed = {}, progress = {} }
  for qid, q in pairs(new) do
    local o = old[qid]
    if not o then diff.accepted[#diff.accepted + 1] = q
    else
      if q.complete and not o.complete then diff.completed[#diff.completed + 1] = q end
      for i, ob in ipairs(q.objectives) do
        local before = o.objectives[i]
        if before and (ob.have or 0) > (before.have or 0) then
          diff.progress[#diff.progress + 1] = { questID = qid, title = q.title, name = ob.name, text = ob.text,
            have = ob.have, need = ob.need, finished = ob.finished, gained = (ob.have or 0) - (before.have or 0) }
        end
      end
    end
  end
  for qid, o in pairs(old) do
    if not new[qid] then
      if Q.turned[qid] then diff.turnedIn[#diff.turnedIn + 1] = o else diff.removed[#diff.removed + 1] = o end
    end
  end
  diff.changed = (#diff.accepted + #diff.removed + #diff.turnedIn + #diff.completed + #diff.progress) > 0
  return diff
end

function D.RefreshQuests()
  local byId, list = D.SnapshotQuests()
  local first = Q.at < 0
  local diff = D.DiffQuests(Q.byId, byId)
  Q.byId, Q.list, Q.at = byId, list, now()
  Q.objIndex, Q.npcIdx = nil, nil
  if not first and diff.changed then
    for _, p in ipairs(diff.progress) do emit("progress", p) end
    emit("quests", diff)
  end
  for qid in pairs(Q.turned) do if not byId[qid] then Q.turned[qid] = nil end end
  D.RescanPlates()
  return diff
end
local function questsSoon() soon("quests", 0.3, D.RefreshQuests) end
D.QuestsSoon = questsSoon

function D.Quests() if Q.at < 0 then D.RefreshQuests() end return Q.list, Q.byId end

-- ------------------------------------------------------------------ vérité des données (agent DATA)
-- Règle unique : un « tue X / ramasse Y / monstre de quête » ne vient QUE d'un objectif NON fini d'une quête
-- présente dans le journal, retrouvée par son questID (jamais par son titre). Les noms ne servent qu'en repli,
-- en correspondance EXACTE (après normalisation), et seulement pour les objectifs « monstre ».
--- nom d'objectif / de monstre normalisé : « Ours gris tués : 3/5 » -> « ours gris »
function D.Norm(s)
  s = low(str(s) or "")
  s = s:gsub("%d+%s*/%s*%d+", ""):gsub("^%s*%-%s*", ""):gsub("[%s:%.!]+$", ""):gsub("^%s+", "")
  s = s:gsub("%s+tu[ée]+e?s?$", ""):gsub("%s+slain$", ""):gsub("%s+killed$", ""):gsub("%s+$", "")
  return s
end
local MOBTYPE = { monster = true }
-- index nom normalisé -> objectif « monstre » non fini (reconstruit à chaque relevé du journal)
local function objIndex()
  if Q.objIndex then return Q.objIndex end
  local idx = {}
  for _, q in ipairs(Q.list) do
    if not q.complete then
      for i, o in ipairs(q.objectives) do
        local nm = D.Norm(o.name)
        if not o.finished and #nm >= 3 then
          local e = { name = nm, quest = q.title, questID = q.id, index = i, have = o.have, need = o.need, type = o.type }
          local prev = idx[nm]
          if not prev or ((e.need or 1) - (e.have or 0)) > ((prev.need or 1) - (prev.have or 0)) then idx[nm] = e end
        end
      end
    end
  end
  Q.objIndex = idx
  return idx
end
--- objectif non fini dont le nom est EXACTEMENT ce monstre (types « monstre » ou inconnu), ou nil.
--- kind = "any" accepte aussi les objectifs objet / objet du monde (morts par l'XP : non).
function D.ObjectiveFor(name, kind)
  local n = D.Norm(name)
  if n == "" then return nil end
  local o = objIndex()[n]
  if not o then return nil end
  if kind ~= "any" and o.type and not MOBTYPE[o.type] then return nil end
  return o
end
--- quête du journal par ID (nil si absente) ; par titre seulement pour lire une infobulle (le titre seul)
function D.InLog(qid) if Q.at < 0 then D.RefreshQuests() end return Q.byId[tonumber(qid) or -1] end
function D.QuestByTitle(title)
  title = str(title)
  if not title then return nil end
  if Q.at < 0 then D.RefreshQuests() end
  local hit, n = nil, 0
  for _, q in ipairs(Q.list) do if q.title == title then hit, n = q, n + 1 end end
  return hit, n
end

-- Questie (lecture seule) : objectifs d'une quête du journal, dans l'ordre de Questie (créatures, objets du monde,
-- objets, réputation, crédit de mort), associés aux objectifs du jeu : nom exact d'abord, puis rang si les
-- comptes et les genres concordent. Une entrée sans association n'est JAMAIS montrée.
local QZ = { obj = {}, n = 0 }
local function questieDB()
  local C = T.Compass
  local db = type(C) == "table" and type(C.Questie) == "function" and try(C.Questie) or nil
  return type(db) == "table" and type(db.QueryQuestSingle) == "function" and db or nil
end
D.QuestieDB = questieDB
local KIND_OK = { monster = { monster = true, credit = true }, item = { item = true }, object = { object = true, item = true },
  event = { credit = true, object = true, monster = true } }
function D.QuestieObjectives(qid)
  qid = tonumber(qid)
  local db = qid and questieDB()
  if not db then return nil end
  local c = QZ.obj[qid]
  if c then return c end
  local objs = try(db.QueryQuestSingle, qid, "objectives")
  local out = {}
  if type(objs) == "table" then
    local function nm(fn, id) return type(fn) == "function" and str(try(fn, id, "name")) or nil end
    for _, e in ipairs(type(objs[1]) == "table" and objs[1] or {}) do
      local id = type(e) == "table" and e[1] or e
      if type(id) == "number" then
        local npcName = nm(db.QueryNPCSingle, id)
        out[#out + 1] = { kind = "monster", ids = { id }, name = (type(e) == "table" and str(e[2])) or npcName, alt = npcName }
      end
    end
    for _, e in ipairs(type(objs[2]) == "table" and objs[2] or {}) do
      local id = type(e) == "table" and e[1] or e
      if type(id) == "number" then
        out[#out + 1] = { kind = "object", obj = id, name = (type(e) == "table" and str(e[2])) or nm(db.QueryObjectSingle, id) }
      end
    end
    for _, e in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
      local id = type(e) == "table" and e[1] or e
      if type(id) == "number" then
        out[#out + 1] = { kind = "item", item = id, name = nm(db.QueryItemSingle, id) or (T.API and T.API.ItemName and T.API.ItemName(id))
          or (type(e) == "table" and str(e[2])) }
      end
    end
    if type(objs[4]) == "table" and objs[4][1] then out[#out + 1] = { kind = "rep", name = nil } end
    for _, e in ipairs(type(objs[5]) == "table" and objs[5] or {}) do
      local ids = type(e) == "table" and e[1]
      if type(ids) == "table" and type(ids[1]) == "number" then
        local all = {}
        for i, id in ipairs(ids) do if i <= 12 and type(id) == "number" then all[#all + 1] = id end end
        if type(e[2]) == "number" then all[#all + 1] = e[2] end
        local npcName = nm(db.QueryNPCSingle, type(e[2]) == "number" and e[2] or ids[1])
        out[#out + 1] = { kind = "credit", ids = all, name = str(e[3]) or npcName, alt = npcName }
      end
    end
  end
  QZ.n = QZ.n + 1
  if QZ.n > 200 then QZ.obj, QZ.n = {}, 0 end
  QZ.obj[qid] = out
  return out
end
--- objectifs du jeu (liste {name|text, type, finished}) -> [rang du jeu] = entrée Questie (ou rien)
function D.MapObjectives(qid, game)
  local qz = D.QuestieObjectives(qid)
  local map = {}
  if not (qz and type(game) == "table") then return map end
  local used = {}
  local function compat(g, e) return not g.type or not KIND_OK[g.type] or KIND_OK[g.type][e.kind] or false end
  for i, g in ipairs(game) do        -- 1. nom exact (normalisé), genre compatible
    local gn = D.Norm(g.name or g.text)
    for j, e in ipairs(qz) do
      if not used[j] and gn ~= "" and (D.Norm(e.name) == gn or D.Norm(e.alt) == gn) and compat(g, e) then
        map[i], used[j] = e, true break
      end
    end
  end
  local freeG, freeE = {}, {}
  for i in ipairs(game) do if not map[i] then freeG[#freeG + 1] = i end end
  for j in ipairs(qz) do if not used[j] then freeE[#freeE + 1] = j end end
  if #game == #qz then                -- 2. même nombre : même rang (l'ordre de Questie suit celui du jeu)
    for _, i in ipairs(freeG) do
      if not used[i] and qz[i] and compat(game[i], qz[i]) then map[i], used[i] = qz[i], true end
    end
  elseif #freeG == 1 and #freeE == 1 and compat(game[freeG[1]], qz[freeE[1]]) and game[freeG[1]].type then
    map[freeG[1]] = qz[freeE[1]]      -- 3. un seul de chaque côté, genre annoncé par le jeu et compatible
  end
  return map
end

-- monstre (ID de PNJ) -> objectif non fini qu'il sert : tué (créature / crédit) ou butin (objet, npcDrops Questie)
local function npcIndex()
  if Q.npcIdx then return Q.npcIdx end
  local idx, db = {}, questieDB()
  local budget = 600
  local function put(npc, e)
    local prev = idx[npc]
    if not prev or (e.left or 0) > (prev.left or 0) then idx[npc] = e end
  end
  if db then
    for _, q in ipairs(Q.list) do
      if not q.complete and budget > 0 then
        local map = D.MapObjectives(q.id, q.objectives)
        for i, o in ipairs(q.objectives) do
          local z = map[i]
          if z and not o.finished then
            local e = { quest = q.title, questID = q.id, index = i, obj = o.name, have = o.have, need = o.need,
              left = math.max(0, (o.need or 1) - (o.have or 0)) }
            if z.kind == "monster" or z.kind == "credit" then
              for _, id in ipairs(z.ids or {}) do put(id, e) end
            elseif z.kind == "item" and db.QueryItemSingle then
              budget = budget - 1
              local de = setmetatable({ item = z.item }, { __index = e })
              for n, npc in ipairs(try(db.QueryItemSingle, z.item, "npcDrops") or {}) do
                if n > 40 then break end
                if type(npc) == "number" then put(npc, de) end
              end
            end
          end
        end
      end
    end
  end
  Q.npcIdx = idx
  return idx
end
--- objectif non fini d'une quête du journal que ce PNJ (ID) sert, ou nil : {quest, questID, index, obj, have, need, left, item?}
function D.NpcObjective(npcID)
  npcID = tonumber(npcID)
  if not npcID then return nil end
  if Q.at < 0 then D.RefreshQuests() end
  return npcIndex()[npcID]
end
function D.NpcID(guid)
  guid = str(guid)
  if not guid then return nil end
  local kind, id = guid:match("^(%a+)%-%d+%-%d+%-%d+%-%d+%-(%d+)")
  if kind == "Creature" or kind == "Vehicle" then return tonumber(id) end
  return nil
end
--- signal du jeu : vrai / faux / nil (API absente, valeur secrète, ou jamais vue « vraie » cette session :
--- une API qui répond toujours faux ne doit pas effacer les correspondances par ID)
function D.GameRelated(unit)
  local QL = G.C_QuestLog
  if not (QL and QL.UnitIsRelatedToActiveQuest) then return nil end
  local v = pub(try(QL.UnitIsRelatedToActiveQuest, unit))
  if v then Q.relSeen = true return true end
  if v == nil or not Q.relSeen then return nil end
  return false
end

-- morts par le message d'XP (FR / EN) — compte de session par nom
function D.ParseKillXP(msg)
  if type(msg) ~= "string" or secret(msg) then return nil end
  return msg:match("^(.-) meurt, vous gagnez") or msg:match("^(.-) dies, you gain")
end
function D.OnXP(msg)
  local name = D.ParseKillXP(msg)
  if not name then return nil end
  local k = low(name)
  Q.kills[k] = (Q.kills[k] or 0) + 1
  local o = D.ObjectiveFor(name)
  emit("kill", { name = name, count = Q.kills[k], objective = o })
  if o then questsSoon() end
  return name
end
function D.Kills(a, b)
  local name = type(a) == "string" and a or b
  if name then return Q.kills[low(name)] or 0 end
  return Q.kills
end

-- ------------------------------------------------------------------ 2. monstres / objets de quête (barres de nom)
local P = { units = {}, at = -1e9 }
D._p = P

--- infos de quête d'une unité, ou nil : {unit, name, guid, hostile, boss, related, quest, questID, obj, have, need, left, how}
--- Ordre de confiance : signal du jeu (UnitIsRelatedToActiveQuest / UnitIsQuestBoss) > ID de PNJ d'un objectif
--- non fini (Questie, par questID du journal) > nom EXACT d'un objectif « monstre » (seulement si le jeu ne dit pas non).
function D.QuestUnit(a, b)
  local unit = type(a) == "string" and a or b
  if not (unit and on("questUnits")) or not try(G.UnitExists, unit) then return nil end
  if try(G.UnitIsPlayer, unit) or pub(try(G.UnitIsDead, unit)) then return nil end
  local rel = D.GameRelated(unit)
  local boss = rawget(G, "UnitIsQuestBoss") and pub(try(G.UnitIsQuestBoss, unit)) and true or false
  local name = str(try(G.UnitName, unit))
  local guid = str(try(G.UnitGUID, unit))
  local o, how = D.NpcObjective(D.NpcID(guid)), nil
  if o then how = o.item and "drop" or "kill" end
  if not o and name and rel ~= false then
    o = D.ObjectiveFor(name)
    if o then how = "kill" end
  end
  if not (rel or boss or o) then return nil end
  local hostile = pub(try(G.UnitCanAttack, "player", unit)) and true or false
  local info = { unit = unit, name = name, guid = guid, hostile = hostile, boss = boss,
    related = rel and true or false, quest = o and o.quest, questID = o and o.questID, obj = o and (o.obj or o.name),
    have = o and o.have, need = o and o.need, how = how, item = o and o.item }
  info.left = (o and o.need and o.have) and math.max(0, o.need - o.have) or nil
  return info
end

function D.UpdatePlate(unit)
  if type(unit) ~= "string" then return nil end
  local prev = P.units[unit]
  local info = D.QuestUnit(unit)
  -- en combat sur Forever : nom / GUID secrets -> on garde ce qu'on savait de cette barre
  if not info and prev and T.InCombat and T:InCombat() and not str(try(G.UnitName, unit)) then return prev end
  P.units[unit] = info
  D.ScanUnitRare(unit)
  if (info ~= nil) ~= (prev ~= nil) then soon("qunits", 0.2, function() emit("questunits", D.QuestUnits()) end) end
  return info
end
function D.RemovePlate(unit)
  if P.units[unit] then
    P.units[unit] = nil
    soon("qunits", 0.2, function() emit("questunits", D.QuestUnits()) end)
  end
end
function D.RescanPlates()
  local NP = G.C_NamePlate
  local seen = {}
  if NP and NP.GetNamePlates then
    for _, p in ipairs(try(NP.GetNamePlates) or {}) do
      local u = type(p) == "table" and (p.namePlateUnitToken or (type(p.UnitFrame) == "table" and p.UnitFrame.unit))
      if type(u) == "string" then seen[u] = true D.UpdatePlate(u) end
    end
  end
  for u in pairs(P.units) do if not seen[u] then D.UpdatePlate(u) end end
end

--- barres de nom liées à une quête : liste triée (reste à faire d'abord) + total « reste » par objectif
function D.QuestUnits()
  local out, perObj, left = {}, {}, 0
  for _, info in pairs(P.units) do
    out[#out + 1] = info
    local k = info.obj and (tostring(info.questID) .. ":" .. info.obj)
    if k and info.left and not perObj[k] then perObj[k] = true left = left + info.left end
  end
  tsort(out, function(a, b)
    if (a.left or 0) ~= (b.left or 0) then return (a.left or 0) > (b.left or 0) end
    return tostring(a.unit) < tostring(b.unit)
  end)
  out.left = left
  return out
end

-- ------------------------------------------------------------------ 4. services proches
local KINDS = { vendor = "Marchand", repair = "Réparation", flight = "Maître de vol", trainer = "Maître",
  innkeeper = "Aubergiste", mailbox = "Boîte aux lettres", bank = "Banque", auction = "Hôtel des ventes",
  stable = "Maître des écuries", spirit = "Gardien des esprits", poi = "Lieu" }
D.SERVICE_FR = KINDS
-- catégories des habitants Questie (lecture seule) -> nos genres
local QUESTIE_KINDS = { ["Repair"] = "repair", ["Innkeeper"] = "innkeeper", ["Flight Master"] = "flight",
  ["Mailbox"] = "mailbox", ["Banker"] = "bank", ["Auctioneer"] = "auction", ["Stable Master"] = "stable",
  ["Spirit Healer"] = "spirit", ["Weapon Master"] = "trainer", ["Class Trainer"] = "trainer",
  ["Profession Trainer"] = "trainer", ["Vendor"] = "vendor", ["General Goods"] = "vendor", ["Food"] = "vendor",
  ["Drink"] = "vendor", ["Ammo"] = "vendor", ["Reagents"] = "vendor", ["Trade Goods"] = "vendor" }

local S = { taxi = {}, poi = {}, questie = {} }
D._s = S

local function flightNodes(mapID)
  local c = S.taxi[mapID]
  if c and now() - c.t < 60 then return c.list end
  local list = {}
  local TM = G.C_TaxiMap
  if TM and TM.GetTaxiNodesForMap then
    for _, nd in ipairs(try(TM.GetTaxiNodesForMap, mapID) or {}) do
      if type(nd) == "table" then
        local x, y = xy(nd.position)
        local name = str(nd.name)
        if x and y and name then
          list[#list + 1] = { key = "taxi:" .. tostring(nd.nodeID or name), kind = "flight", name = name, mapID = mapID,
            x = x, y = y, known = not pub(nd.isUndiscovered), src = "taxi" }
        end
      end
    end
  end
  S.taxi[mapID] = { t = now(), list = list }
  return list
end

local function areaPois(mapID)
  local c = S.poi[mapID]
  if c and now() - c.t < 60 then return c.list end
  local list = {}
  local AP = G.C_AreaPoiInfo
  if AP and AP.GetAreaPOIForMap and AP.GetAreaPOIInfo then
    for _, id in ipairs(try(AP.GetAreaPOIForMap, mapID) or {}) do
      local info = not secret(id) and try(AP.GetAreaPOIInfo, mapID, id)
      if type(info) == "table" then
        local x, y = xy(info.position)
        local name = str(info.name)
        if x and y and name and #list < 30 then
          list[#list + 1] = { key = "poi:" .. tostring(id), kind = "poi", name = name, mapID = mapID, x = x, y = y,
            text = str(info.description), src = "poi" }
        end
      end
    end
  end
  S.poi[mapID] = { t = now(), list = list }
  return list
end

-- habitants Questie : Questie.db.(char|global).townsfolk = { [catégorie] = { npcID / objectID, ... } }
local function questieTown()
  local Qe = rawget(G, "Questie")
  local db = type(Qe) == "table" and type(Qe.db) == "table" and Qe.db or nil
  if not db then return nil end
  for _, scope in ipairs({ "char", "global", "profile" }) do
    local t = type(db[scope]) == "table" and db[scope].townsfolk
    if type(t) == "table" then return t end
  end
  return nil
end
local function questieServices(kind, me)
  local town = questieTown()
  local C = T.Compass
  if not (town and me and type(C) == "table" and type(C.Questie) == "function" and type(C.SpawnsOf) == "function") then
    return {}
  end
  local ck = tostring(kind) .. ":" .. me.mapID
  local c = S.questie[ck]
  if c and now() - c.t < 30 then return c.list end
  local db, zdb = C.Questie()
  local list = {}
  if db then
    local budget = 120
    for cat, ids in pairs(town) do
      local k = QUESTIE_KINDS[cat]
      if k and (kind == nil or k == kind) and type(ids) == "table" then
        for _, id in ipairs(ids) do
          if budget <= 0 or #list >= 40 then break end
          budget = budget - 1
          local pts = {}
          pcall(C.SpawnsOf, k == "mailbox" and "obj" or "npc", id, db, zdb, me.mapID, pts)
          for _, p in ipairs(pts) do
            if p.mapID == me.mapID then
              list[#list + 1] = { key = "qt:" .. k .. ":" .. tostring(id), kind = k, name = p.name or KINDS[k],
                mapID = p.mapID, x = p.x, y = p.y, src = "questie" }
            end
          end
        end
      end
    end
  end
  S.questie[ck] = { t = now(), list = list }
  return list
end

-- PNJ appris (compte) : learned[mapID] = { {k=genre, n=nom, x=, y=, t=epoch}, ... } (≤ 30 par carte, ≤ 80 cartes)
function D.Learn(kind, name)
  if not (on("learn") and KINDS[kind] and D.db) then return nil end
  local me = D.Me()
  if not me then return nil end
  D.db.learned = type(D.db.learned) == "table" and D.db.learned or {}
  local L = D.db.learned
  local key = tostring(me.mapID)
  local list = L[key]
  if type(list) ~= "table" then
    local n = 0 for _ in pairs(L) do n = n + 1 end
    if n >= 80 then return nil end
    list = {} L[key] = list
  end
  name = cut(name or KINDS[kind], 40)
  local t = try(G.time) or 0
  for _, e in ipairs(list) do
    if e.k == kind and (e.n == name or (abs(e.x - me.x) < 0.01 and abs(e.y - me.y) < 0.01)) then
      e.x, e.y, e.t, e.n = floor(me.x * 1e4) / 1e4, floor(me.y * 1e4) / 1e4, t, name
      return e
    end
  end
  local e = { k = kind, n = name, x = floor(me.x * 1e4) / 1e4, y = floor(me.y * 1e4) / 1e4, t = t }
  list[#list + 1] = e
  while #list > 30 do table.remove(list, 1) end
  emit("services", { learned = e, mapID = me.mapID })
  return e
end
local function learned(mapID)
  local L = D.db and type(D.db.learned) == "table" and D.db.learned[tostring(mapID)]
  local out = {}
  for i, e in ipairs(type(L) == "table" and L or {}) do
    if type(e) == "table" and KINDS[e.k] and tonumber(e.x) and tonumber(e.y) then
      out[#out + 1] = { key = "learn:" .. mapID .. ":" .. i, kind = e.k, name = e.n, mapID = mapID, x = e.x, y = e.y,
        src = "learned" }
    end
  end
  return out
end

--- Services de la carte actuelle triés par distance. kind = "vendor"|"repair"|"flight"|"trainer"|"innkeeper"|
--- "mailbox"|"bank"|"auction"|"stable"|"spirit"|"poi"|nil (tout). Un marchand qui répare compte pour "vendor".
function D.Services(a, b)
  local kind = type(a) == "string" and a or b
  if not on("services") then return {} end
  local me = D.Me()
  if not me then return {} end
  local out = {}
  local function take(list)
    for _, e in ipairs(list) do
      if kind == nil or e.kind == kind or (kind == "vendor" and e.kind == "repair") then out[#out + 1] = e end
    end
  end
  take(learned(me.mapID))
  if kind == nil or kind == "flight" then take(flightNodes(me.mapID)) end
  if kind == nil or kind == "poi" then take(areaPois(me.mapID)) end
  if kind ~= "poi" and not (T.InCombat and T:InCombat()) then take(questieServices(kind, me)) end
  return measure(out, me)
end
function D.Nearest(a, b)
  local kind = type(a) == "string" and a or b
  local list = D.Services(kind)
  for _, e in ipairs(list) do if e.kind ~= "flight" or e.known ~= false then return e end end
  return list[1]
end
--- « Marchand : Nom — 230 m NE » ou nil
function D.NearestText(a, b)
  local kind = type(a) == "string" and a or b
  local e = D.Nearest(kind)
  if not e then return nil end
  local where = e.dist and (" — " .. D.Dist(e.dist) .. (e.bearing and (" " .. D.Cardinal(e.bearing)) or "")) or ""
  local nm = (e.name and e.name ~= KINDS[e.kind]) and (" : " .. cut(e.name, 28)) or ""
  return (KINDS[e.kind] or "Service") .. nm .. where, e
end

-- suivi de la minimap (lecture seule : genres actifs, aucune position)
function D.TrackingActive()
  local MM = G.C_Minimap
  local out = {}
  if not (MM and MM.GetNumTrackingTypes and MM.GetTrackingInfo) then return out end
  for i = 1, math.min(40, num(try(MM.GetNumTrackingTypes)) or 0) do
    local info = try(MM.GetTrackingInfo, i)
    if type(info) == "table" and pub(info.active) and str(info.name) then out[#out + 1] = info.name end
  end
  return out
end

-- ------------------------------------------------------------------ 5. groupe
local GR = { state = {} }
D._g = GR
--- membres du groupe : {unit, name, class, level, online, afk, dead, ghost, mapID, x, y, dist, bearing, inRange, zone, src}
function D.Group()
  local out = {}
  if not on("group") then return out end
  local raid = try(G.IsInRaid)
  local prefix, n = raid and "raid" or "party", raid and 40 or 4
  local me = D.Me()
  local CM = G.C_Map
  local S2 = T.Squad
  local members = type(S2) == "table" and type(S2._members) == "table" and S2._members or {}
  for i = 1, n do
    local u = prefix .. i
    if try(G.UnitExists, u) and not try(G.UnitIsUnit, u, "player") then
      local name = str(try(G.UnitName, u))
      local _, cls = try(G.UnitClass, u)
      local m = { unit = u, name = name, class = str(cls), level = num(try(G.UnitLevel, u)),
        online = pub(try(G.UnitIsConnected, u)) ~= false,
        afk = pub(try(G.UnitIsAFK, u)) and true or false,
        ghost = pub(try(G.UnitIsGhost, u)) and true or false }
      m.dead = (pub(try(G.UnitIsDeadOrGhost, u)) and not m.ghost) and true or false
      local r1, r2 = try(G.UnitInRange, u)
      if pub(r2) then m.inRange = pub(r1) and true or false end
      if me and CM and CM.GetPlayerMapPosition then
        local x, y = xy(try(CM.GetPlayerMapPosition, me.mapID, u))
        if x and y and not (x == 0 and y == 0) then m.mapID, m.x, m.y, m.src = me.mapID, x, y, "map" end
      end
      if not m.x and G.UnitPosition then   -- yards monde, même instance seulement (limité sur Forever)
        local ay, ax, _, ai = try(G.UnitPosition, "player")   -- X = nord, Y = ouest
        local by, bx, _, bi = try(G.UnitPosition, u)
        ay, ax, by, bx, ai, bi = num(ay), num(ax), num(by), num(bx), num(ai), num(bi)
        if ay and by and ax and bx and ai == bi then
          local dn, dw = by - ay, bx - ax
          m.dist, m.bearing, m.src = sqrt(dn * dn + dw * dw), atan2(dw, dn), "world"
        end
      end
      local sq = name and type(members[name]) == "table" and members[name].st
      if type(sq) == "table" then
        m.zone = str(sq.z)
        if not m.src and tonumber(sq.px) and tonumber(sq.py) then m.src = "squad" end
      end
      if m.x and me then m.dist, m.bearing = D.Vector(me, m) end
      out[#out + 1] = m
    end
  end
  return out
end

-- changements d'état (hors ligne / ABS / mort / fantôme / revenu) -> emit("group", {name, what, unit})
local WHAT = { online = { "est revenu", "est hors ligne" }, afk = { "est ABS", "n'est plus ABS" },
               dead = { "est mort", "est vivant" }, ghost = { "est en fantôme", "a repris son corps" } }
function D.CheckGroup()
  local changes = {}
  local cur = {}
  for _, m in ipairs(D.Group()) do
    if m.name then
      cur[m.name] = { online = m.online, afk = m.afk, dead = m.dead, ghost = m.ghost }
      local old = GR.state[m.name]
      if old then
        for k, txt in pairs(WHAT) do
          if old[k] ~= cur[m.name][k] then
            local what = (k == "online") and (m.online and txt[1] or txt[2]) or (cur[m.name][k] and txt[1] or txt[2])
            changes[#changes + 1] = { name = m.name, unit = m.unit, what = what, key = k, value = cur[m.name][k] }
          end
        end
      end
    end
  end
  GR.state = cur
  if #changes > 0 then emit("group", changes) end
  return changes
end

-- ------------------------------------------------------------------ 6. combat (sans journal de combat)
local CB = { hits = {}, petHits = {}, hp = nil, threat = nil }
D._c = CB
function D.OnUnitCombat(unit, action, _, amount)
  if not on("combat") then return end
  if unit ~= "player" and unit ~= "pet" then return end
  action = str(action)
  if not action then return end
  local list = unit == "pet" and CB.petHits or CB.hits
  list[#list + 1] = { t = now(), action = action, amount = num(amount) }
  while #list > 40 do table.remove(list, 1) end
end
function D.OnHealth(unit)
  if unit ~= "player" then return end
  local h, m = num(try(G.UnitHealth, "player")), num(try(G.UnitHealthMax, "player"))
  if h and m and m > 0 then CB.hp = h / m end
end
--- {inCombat, hpPct, hits5s, dmg5s (nil si inconnu), petHits5s, threat (0..3 / nil), attackers (barres de nom), petAttacked}
function D.Combat()
  local t = now()
  local res = { inCombat = T.InCombat and T:InCombat() or false, hpPct = CB.hp, hits5s = 0, petHits5s = 0 }
  local dmg, known = 0, true
  for _, h in ipairs(CB.hits) do
    if t - h.t <= 5 and (h.action == "WOUND" or h.action == "CRUSHING" or h.action == "CRITICAL") then
      res.hits5s = res.hits5s + 1
      if h.amount then dmg = dmg + h.amount else known = false end
    end
  end
  if res.hits5s > 0 and known then res.dmg5s = dmg end
  for _, h in ipairs(CB.petHits) do if t - h.t <= 5 and h.action == "WOUND" then res.petHits5s = res.petHits5s + 1 end end
  res.petAttacked = res.petHits5s > 0
  local th = num(try(G.UnitThreatSituation, "player"))
  res.threat = th
  -- ennemis engagés sur moi (barres de nom) : UnitThreatSituation(player, unité) lisible seulement si non secret
  local n = 0
  local NP = G.C_NamePlate
  if NP and NP.GetNamePlates then
    for _, p in ipairs(try(NP.GetNamePlates) or {}) do
      local u = type(p) == "table" and (p.namePlateUnitToken or (type(p.UnitFrame) == "table" and p.UnitFrame.unit))
      if type(u) == "string" and pub(try(G.UnitCanAttack, "player", u)) then
        local s = num(try(G.UnitThreatSituation, "player", u))
        if s and s >= 1 then n = n + 1 end
      end
    end
  end
  res.attackers = n
  return res
end

-- ------------------------------------------------------------------ épingles génériques (Radar / autres cartes)
--- {key, kind, mapID, x, y, label, approx} : rares / trésors (+ services appris si withServices)
function D.Pins(a, b)
  local withServices = (a == true) or (b == true)
  local out = {}
  for _, e in ipairs(D.Rares()) do
    if e.mapID and e.x and e.y then
      out[#out + 1] = { key = e.key, kind = e.kind == "treasure" and "treasure" or "rare", mapID = e.mapID, x = e.x,
        y = e.y, label = D.Describe(e), approx = e.approx, src = e.src }
    end
  end
  if withServices then
    local me = D.Me()
    for _, e in ipairs(me and learned(me.mapID) or {}) do
      out[#out + 1] = { key = e.key, kind = "service", service = e.kind, mapID = e.mapID, x = e.x, y = e.y,
        label = (KINDS[e.kind] or "Service") .. " : " .. tostring(e.name) }
    end
  end
  return out
end

--- un clic : la boussole guide vers ce point (rare, trésor, service). Retourne la cible ou nil.
function D.Target(e)
  local C = T.Compass
  if not (type(e) == "table" and e.mapID and e.x and type(C) == "table" and type(C.SetTarget) == "function") then
    return nil
  end
  local label = e.label or (e.kind and KINDS[e.kind] and (KINDS[e.kind] .. " : " .. tostring(e.name)))
    or (KIND_FR[e.kind] or "Point") .. " : " .. tostring(e.name or "?")
  return try(C.SetTarget, C, { mapID = e.mapID, x = e.x, y = e.y, label = cut(label, 40), source = "detect" })
end

-- ------------------------------------------------------------------ résumé IA / slash
function D.WriteSummary()
  local s = T.Summary and T:Summary()
  if type(s) ~= "table" then return end
  local r, tr = 0, 0
  for _, e in ipairs(V.list) do if e.kind == "treasure" then tr = tr + 1 else r = r + 1 end end
  local nl = 0
  for _, l in pairs(D.db and type(D.db.learned) == "table" and D.db.learned or {}) do
    if type(l) == "table" then nl = nl + #l end
  end
  local qu = D.QuestUnits()
  s.detect = { rares = r, treasures = tr, questUnits = #qu, questLeft = qu.left, learnedServices = nl }
end

local function slash(rest)
  rest = low(rest):gsub("^%s+", ""):gsub("%s+$", "")
  local kinds = { marchand = "vendor", vendeur = "vendor", reparation = "repair", ["réparation"] = "repair",
    vol = "flight", maitre = "trainer", ["maître"] = "trainer", auberge = "innkeeper", aubergiste = "innkeeper",
    courrier = "mailbox", boite = "mailbox", banque = "bank", hv = "auction", ecuries = "stable" }
  if rest == "rare" or rest == "tresor" or rest == "trésor" then
    local list = D.Rares(rest == "rare" and "rare" or "treasure")
    local e = list[1]
    if not e then D:Print("Aucun " .. rest .. " repéré ici pour l'instant.") return end
    D.Target(e)
    D:Print(D.Describe(e) .. " → boussole.")
    return
  end
  if kinds[rest] then
    local txt, e = D.NearestText(kinds[rest])
    if not e then D:Print("Pas encore de " .. rest .. " connu sur cette carte (parle à un PNJ : il est retenu).") return end
    D.Target(e)
    D:Print(txt .. " → boussole.")
    return
  end
  local rares, qu = D.Rares(), D.QuestUnits()
  D:Print(("Détection : %d rare(s)/trésor(s), %d monstre(s) de quête en vue (reste %d), %d service(s) sur la carte.")
    :format(#rares, #qu, qu.left or 0, #D.Services()))
  for i = 1, math.min(3, #rares) do D:Print("  " .. D.Describe(rares[i])) end
  D:Print("  /tai detect rare | tresor | marchand | reparation | vol | auberge | courrier → la boussole y va.")
end

-- ------------------------------------------------------------------ cycle de vie
local SERVICE_EVENTS = { MERCHANT_SHOW = "vendor", TAXIMAP_OPENED = "flight", TRAINER_SHOW = "trainer",
  MAIL_SHOW = "mailbox", BANKFRAME_OPENED = "bank", AUCTION_HOUSE_SHOW = "auction", PET_STABLE_SHOW = "stable",
  CONFIRM_BINDER = "innkeeper" }
D.SERVICE_EVENTS = SERVICE_EVENTS

function D:OnEnable()
  self.db.learned = type(self.db.learned) == "table" and self.db.learned or {}
  self:RegisterEvent("VIGNETTES_UPDATED", function() soon("vig", 0.5, D.ScanVignettes) end)
  self:RegisterEvent("VIGNETTE_MINIMAP_UPDATED", function() soon("vig", 0.5, D.ScanVignettes) end)
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() V.byId, V.list = {}, {} soon("vig", 1, D.ScanVignettes) end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function()
    soon("vig", 2, D.ScanVignettes) questsSoon() soon("grp", 2, D.CheckGroup)
  end)
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "QUEST_WATCH_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED" }) do
    self:RegisterEvent(ev, questsSoon)
  end
  self:RegisterEvent("UNIT_QUEST_LOG_CHANGED", function(_, _, unit) if unit == "player" or unit == nil then questsSoon() end end)
  self:RegisterEvent("QUEST_TURNED_IN", function(_, _, qid) if num(qid) then Q.turned[qid] = true end questsSoon() end)
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN", function(_, _, msg) D.OnXP(msg) end)
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED", function(_, _, unit) D.UpdatePlate(unit) end)
  self:RegisterEvent("NAME_PLATE_UNIT_REMOVED", function(_, _, unit) D.RemovePlate(unit) end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() D.ScanUnitRare("target") end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() soon("plates", 0.5, D.RescanPlates) end)
  for ev, kind in pairs(SERVICE_EVENTS) do
    self:RegisterEvent(ev, function()
      local name = str(try(G.UnitName, "npc"))
      D.Learn(kind, name)
      if ev == "MERCHANT_SHOW" and pub(try(G.CanMerchantRepair)) then D.Learn("repair", name) end
    end)
  end
  for _, ev in ipairs({ "GROUP_ROSTER_UPDATE", "UNIT_CONNECTION", "PLAYER_FLAGS_CHANGED" }) do
    self:RegisterEvent(ev, function() soon("grp", 1, D.CheckGroup) end)
  end
  self:RegisterEvent("UNIT_FLAGS", function(_, _, unit)
    if type(unit) == "string" and (unit:match("^party%d$") or unit:match("^raid%d+$")) then soon("grp", 1, D.CheckGroup) end
  end)
  -- (4.0.1) unités filtrées par le cœur avant tout appel : la vie / les coups des membres du groupe et des barres de
  -- nom ne coûtent plus rien ici
  self:RegisterUnitEvent("UNIT_COMBAT", "player", "pet", function(_, _, ...) D.OnUnitCombat(...) end)
  self:RegisterUnitEvent("UNIT_HEALTH", "player", nil, function(_, _, unit) D.OnHealth(unit) end)
  self:RegisterUnitEvent("UNIT_THREAT_SITUATION_UPDATE", "player", nil, function(_, _, unit)
    if unit == "player" then local th = num(try(G.UnitThreatSituation, "player")) if th ~= CB.threat then CB.threat = th emit("combat", { threat = th }) end end
  end)
  self:On("COMBAT_END", function() CB.hits, CB.petHits = {}, {} end)
  self:On("SUMMARY", function() pcall(D.WriteSummary) end)
end

function D:OnDisable()
  V.byId, V.list, V.plate = {}, {}, {}
  P.units = {}
end

if T.RegisterSlash then
  T:RegisterSlash("detect", slash, "detect [rare|tresor|marchand|reparation|vol|auberge|courrier] : ce que TreeAI voit, la boussole y va")
end
