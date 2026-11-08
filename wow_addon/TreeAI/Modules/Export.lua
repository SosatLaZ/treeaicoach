-- TreeAI — module Export (agent PLAN-LUA). Données pour le planificateur de TreeAI Coach.
-- Contrat : treeaicoach/wow/planner/SCHEMA.md (v1).
--  * TreeAIDB.export : sous-ensemble COMPACT des quêtes utiles (mon journal + quêtes disponibles dans la zone
--    courante et 2 zones voisines + journal de l'escouade), lu à l'exécution dans la base QuestieDB du joueur
--    (API publique LibQuestieDB / QuestDB / NpcDB / ObjectDB / ItemDB, contrat 4 ; repli
--    QuestieLoader._modules.QuestieDB). Rien n'est copié dans nos fichiers : l'export reste sur le PC du joueur.
--    Construit hors combat (coroutine ≤ 2 ms/image) après un changement de zone ; à la déconnexion on ne fait
--    que rafraîchir l'état du joueur et du journal (rapide).
--  * TreeAIDB.learn (compte) + chars[perso].learn : statistiques APPRISES en jouant (temps de kill par écart de
--    niveau, taux de drop des objets de quête, réapparition, vols, détours, affluence, XP/h…), sommes {s, n}.
-- Lecture seule partout : jamais d'écriture dans Questie/QuestieDB, aucun hook, rien en combat
-- (valeurs « secrètes » de Forever ignorées). Lua 5.1, aucune globale (T.Geo est partagé via TreeAI).
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local X = T:RegisterModule("Export", {
  title = "Export planificateur",
  defaults = { enabled = true, learn = true, questie = true, maxQuests = 300 },
  charDefaults = { accepted = {} },
  options = {
    { key = "questie", type = "toggle", label = "Exporter les quêtes de Questie (pour le planificateur)" },
    { key = "learn", type = "toggle", label = "Apprendre mes temps (kills, drops, vols…)" },
  },
})
T.Export = X

local floor, max, min, sqrt, abs = math.floor, math.max, math.min, math.sqrt, math.abs
local tinsert, tsort, tconcat = table.insert, table.sort, table.concat
local unpack = unpack or table.unpack

-- (LITE) sans table par appel (pcall direct) ; toujours au moins une valeur
local function tryPass(ok, a, ...) if ok then return a, ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return tryPass(pcall(fn, ...))
end
local function G(name) return rawget(_G, name) end
local function C(ns, fn) local t = G(ns) return type(t) == "table" and t[fn] or nil end
local function now() return try(G("GetTime")) or 0 end
local function epoch() return try(G("time")) or floor(now()) end
local function secret(v) local f = G("issecretvalue") return f and try(f, v) and true or false end
local function num(v) if secret(v) then return nil end return tonumber(v) end
local function r1(v) return floor((tonumber(v) or 0) * 10 + 0.5) / 10 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function clock() return try(G("debugprofilestop")) or now() * 1000 end

-- ------------------------------------------------------------------ géométrie partagée (Planner aussi)
-- Position = uiMapID + x, y en POURCENT (0–100). Rectangle monde d'une carte via C_Map.GetWorldPosFromMapPos
-- (même repère que UnitPosition : 1er retour ↔ vec.x, 2e ↔ vec.y). Repli : taille de zone supposée.
local Geo = { rects = {}, FALLBACK_W = 3000, FALLBACK_H = 2000 }
T.Geo = Geo
local function vec(x, y)
  local mk = G("CreateVector2D")
  if mk then return try(mk, x, y) end
  return { x = x, y = y, GetXY = function(p) return p.x, p.y end }
end
local function vxy(v)
  if type(v) ~= "table" then return nil end
  if type(v.GetXY) == "function" then return try(v.GetXY, v) end
  return v.x, v.y
end
function Geo.Rect(map)
  map = tonumber(map)
  if not map or map <= 0 then return nil end
  local c = Geo.rects[map]
  if c ~= nil then return c or nil end
  local f = C("C_Map", "GetWorldPosFromMapPos")
  local r = false
  if f then
    local c0, w0 = try(f, map, vec(0, 0))
    local c1, w1 = try(f, map, vec(1, 1))
    local ax, ay = vxy(w0)
    local bx, by = vxy(w1)
    if tonumber(ax) and tonumber(bx) and (ax ~= bx or ay ~= by) then
      r = { c = tonumber(c0) or tonumber(c1) or 0, x0 = ax, y0 = ay, x1 = bx, y1 = by }
    end
  end
  Geo.rects[map] = r
  return r or nil
end
-- world position (a, b, continent) of a map point in percent; nil if unknown
function Geo.World(map, x, y)
  local r = Geo.Rect(map)
  if not (r and tonumber(x) and tonumber(y)) then return nil end
  return r.x0 + (r.x1 - r.x0) * x / 100, r.y0 + (r.y1 - r.y0) * y / 100, r.c
end
-- distance in yards between two map points (percent); `nil` when it cannot be estimated
function Geo.Dist(m1, x1, y1, m2, x2, y2)
  if not (tonumber(x1) and tonumber(y1) and tonumber(x2) and tonumber(y2)) then return nil end
  local a1, b1, c1 = Geo.World(m1, x1, y1)
  local a2, b2, c2 = Geo.World(m2, x2, y2)
  if a1 and a2 then
    if c1 ~= c2 then return nil end
    return sqrt((a1 - a2) ^ 2 + (b1 - b2) ^ 2)
  end
  if m1 == m2 then
    return sqrt(((x1 - x2) * Geo.FALLBACK_W / 100) ^ 2 + ((y1 - y2) * Geo.FALLBACK_H / 100) ^ 2)
  end
  return nil
end
-- player position: map, x, y (percent) — nil inside instances / unknown
function Geo.Player()
  local map = try(C("C_Map", "GetBestMapForUnit"), "player")
  if not map then return nil end
  local p = try(C("C_Map", "GetPlayerMapPosition"), map, "player")
  local x, y = vxy(p)
  if not (num(x) and num(y)) then return map end
  return map, r1(x * 100), r1(y * 100)
end
-- percent position on `map` of a world point (a, b) — used for squad members (Squad sends UnitPosition)
function Geo.FromWorld(map, a, b)
  local r = Geo.Rect(map)
  if not (r and tonumber(a) and tonumber(b)) then return nil end
  local dx, dy = r.x1 - r.x0, r.y1 - r.y0
  if dx == 0 or dy == 0 then return nil end
  return r1((a - r.x0) / dx * 100), r1((b - r.y0) / dy * 100)
end

-- ------------------------------------------------------------------ apprentissage {s, n}
local function acc(t, k, v, cap)
  if type(t) ~= "table" or v == nil then return end
  local e = t[k]
  if type(e) ~= "table" then e = { s = 0, n = 0 }; t[k] = e end
  e.s, e.n = e.s + v, e.n + 1
  if cap and e.n > cap then e.s, e.n = e.s * cap / e.n, cap end  -- bounded memory → recent data weighs more
end
X.Acc = acc
local function learnDB()
  local db = G("TreeAIDB")
  if type(db) ~= "table" then return nil end
  local L = db.learn
  if type(L) ~= "table" or L.v ~= 1 then L = { v = 1 }; db.learn = L end
  for _, k in ipairs({ "drops", "respawn", "flights", "detour", "comp" }) do
    if type(L[k]) ~= "table" then L[k] = {} end
  end
  return L
end
local function charLearn()
  local cdb = T.CharDB and try(T.CharDB, T)
  if type(cdb) ~= "table" then return nil end
  local L = cdb.learn
  if type(L) ~= "table" or L.v ~= 1 then L = { v = 1 }; cdb.learn = L end
  if type(L.ttk) ~= "table" then L.ttk = {} end
  if type(L.quests) ~= "table" then L.quests = {} end
  return L
end
X.LearnDB, X.CharLearn = learnDB, charLearn
local function learning() return X.db and X.db.learn ~= false end

-- public hooks for the Planner (travel legs, efficiency)
function X:LearnDetour(map, ratio)
  if not learning() or not tonumber(map) or not tonumber(ratio) then return end
  local L = learnDB()
  if L then acc(L.detour, tonumber(map), max(0.5, min(4, ratio)), 200) end
end
function X:LearnEff(ratio)
  if not learning() or not tonumber(ratio) then return end
  local L = charLearn()
  if L then acc(L, "eff", max(0.2, min(5, ratio)), 200) end
end
-- learned means, used by the Planner's cost model (nil when < 3 samples)
local function mean(e, minN) if type(e) == "table" and (e.n or 0) >= (minN or 3) and e.n > 0 then return e.s / e.n end end
X.Mean = mean
function X:TTK(delta)
  local L = charLearn()
  return L and mean(L.ttk[tostring(max(-5, min(5, floor(delta or 0))))]) or nil
end
function X:Search() local L = charLearn() return L and mean(L.search) or nil end
function X:Downtime() local L = charLearn() return L and mean(L.downtime) or nil end
function X:Eff() local L = charLearn() return L and mean(L.eff, 5) or nil end
function X:DropRate(itemID)
  local L = learnDB()
  local e = L and L.drops[tonumber(itemID) or -1]
  if type(e) == "table" and (e.n or 0) >= 5 then return (e.k + 2) / (e.n + 5) end  -- Beta(2,3) prior
end
function X:Comp(map) local L = learnDB() return L and mean(L.comp[tonumber(map) or -1], 5) or nil end
function X:Detour(map) local L = learnDB() return L and mean(L.detour[tonumber(map) or -1], 3) or nil end

local function npcFromGUID(guid)
  if type(guid) ~= "string" or secret(guid) then return nil end
  local parts = {}
  for p in guid:gmatch("[^%-]+") do parts[#parts + 1] = p end
  local kind, id = parts[1], parts[6]
  if kind ~= "Creature" and kind ~= "Vehicle" then return nil end
  return tonumber(id)
end
X.NpcFromGUID = npcFromGUID

-- combat segments → TTK by level difference, search time between fights
local seg = { t0 = nil, kills = 0, delta = nil, lastEnd = nil }
local session = { t0 = nil, xp = 0, eat = 0, eatT0 = nil }
function X:PLAYER_REGEN_DISABLED()
  seg.t0, seg.kills, seg.delta = now(), 0, nil
  local lvl, my = num(try(G("UnitLevel"), "target")), num(try(G("UnitLevel"), "player"))
  if lvl and my and lvl > 0 and try(G("UnitCanAttack"), "player", "target") then seg.delta = max(-5, min(5, lvl - my)) end
  if seg.lastEnd and learning() then
    local gap = seg.t0 - seg.lastEnd
    local L = charLearn()
    if L and gap > 1 and gap < 120 then acc(L, "search", gap, 200) end
  end
end
function X:CHAT_MSG_COMBAT_XP_GAIN()
  if seg.t0 then seg.kills = seg.kills + 1 end
  self:NoteKill()
end
function X:PLAYER_REGEN_ENABLED()
  local t0 = seg.t0
  seg.t0 = nil
  if not t0 or seg.kills < 1 then return end
  seg.lastEnd = now()
  local dur = (seg.lastEnd - t0) / seg.kills
  if learning() and seg.delta and dur > 1 and dur < 180 then
    local L = charLearn()
    if L then acc(L.ttk, tostring(seg.delta), dur, 200) end
  end
end

-- kills of the current target → respawn estimate (same npc id, same 2 % cell)
local kills = {}   -- [npc..cell] = time
function X:NoteKill()
  if not learning() then return end
  local guid = try(G("UnitGUID"), "target")
  local dead = try(G("UnitIsDead"), "target")
  local npc = dead and npcFromGUID(guid)
  if not npc then return end
  local map, x, y = Geo.Player()
  if not (map and x) then return end
  local key = npc .. ":" .. map .. ":" .. floor(x / 2) .. ":" .. floor(y / 2)
  local t, prev = now(), kills[key]
  kills[key] = t
  if prev and t - prev >= 60 and t - prev <= 900 then
    local L = learnDB()
    if L then acc(L.respawn, npc, t - prev, 50) end
  end
end

-- competition: share of hostile npcs we target that are already tapped by someone else
local seen = {}
function X:PLAYER_TARGET_CHANGED()
  if not learning() or inCombat() then return end
  local guid = try(G("UnitGUID"), "target")
  if type(guid) ~= "string" or secret(guid) or seen[guid] then return end
  if not npcFromGUID(guid) or try(G("UnitIsDead"), "target") or not try(G("UnitCanAttack"), "player", "target") then return end
  if try(G("UnitPlayerControlled"), "target") then return end
  seen[guid] = true
  local tapped = try(G("UnitIsTapDenied"), "target")
  if tapped == nil then return end
  local map = try(C("C_Map", "GetBestMapForUnit"), "player")
  local L = learnDB()
  if L and map then acc(L.comp, map, tapped and 3 or 1, 300) end  -- κ ≈ 1 + 2 × share tapped by others
end

-- loot → drop rates of quest items (per corpse of a creature known to drop the item)
local lootedCorpses = {}
function X:LOOT_OPENED()
  if not learning() then return end
  local n = try(G("GetNumLootItems")) or 0
  local exp = self.data
  local track = exp and exp._itemNpcs
  if type(track) ~= "table" or n <= 0 then return end
  local got, corpses = {}, {}
  for slot = 1, min(n, 20) do
    local link = try(G("GetLootSlotLink"), slot)
    local item = type(link) == "string" and tonumber(link:match("item:(%d+)"))
    local srcs = { try(G("GetLootSourceInfo"), slot) }
    for i = 1, #srcs, 2 do
      local npc = npcFromGUID(srcs[i])
      if npc and not lootedCorpses[srcs[i]] then corpses[srcs[i]] = npc end
      if npc and item then got[item .. ":" .. srcs[i]] = true end
    end
  end
  local L = learnDB()
  if not L then return end
  for guid, npc in pairs(corpses) do
    lootedCorpses[guid] = true
    for item, npcs in pairs(track) do
      if npcs[npc] then
        local e = L.drops[item]
        if type(e) ~= "table" then e = { k = 0, n = 0 }; L.drops[item] = e end
        e.n = e.n + 1
        if got[item .. ":" .. guid] then e.k = e.k + 1 end
      end
    end
  end
end

-- flights: origin node at the flight master, destination = nearest known node at landing
local flight = {}
function X:TAXIMAP_OPENED()
  local map = try(C("C_Map", "GetBestMapForUnit"), "player")
  local nodes = map and try(C("C_TaxiMap", "GetAllTaxiNodes"), map)
  flight.from = nil
  for _, nd in ipairs(type(nodes) == "table" and nodes or {}) do
    if nd.state == 0 or (G("Enum") and G("Enum").FlightPathState and nd.state == G("Enum").FlightPathState.Current) then
      flight.from = nd.nodeID
    end
  end
end
local function onTaxi() return try(G("UnitOnTaxi"), "player") and true or false end
function X:PLAYER_CONTROL_LOST()
  self:After(1, function()
    if onTaxi() then flight.t0 = now() end
  end)
end
function X:PLAYER_CONTROL_GAINED()
  if not flight.t0 then return end
  local dur = now() - flight.t0
  flight.t0 = nil
  local map, x, y = Geo.Player()
  if not (map and x and flight.from and dur > 10 and dur < 1800) then return end
  local nodes = try(C("C_TaxiMap", "GetTaxiNodesForMap"), map)
  local best, bd
  for _, nd in ipairs(type(nodes) == "table" and nodes or {}) do
    local px, py = vxy(nd.position)
    if tonumber(px) then
      local d = (px * 100 - x) ^ 2 + (py * 100 - y) ^ 2
      if not bd or d < bd then best, bd = nd.nodeID, d end
    end
  end
  if best and best ~= flight.from and bd < 25 then
    local L = learnDB()
    if L then acc(L.flights, flight.from .. "-" .. best, dur, 20) end
  end
end
function X:After(sec, fn) if T.After then T:After(sec, fn) end end

-- quests: accept → turn-in time; session XP/h; eating share
local function xpNow() return num(try(G("UnitXP"), "player")) or 0, num(try(G("UnitXPMax"), "player")) or 0 end
function X:QUEST_ACCEPTED(_, a, b)
  local qid = tonumber(b) or tonumber(a)
  if qid and self.char then self.char.accepted[qid] = epoch() end
  self:Dirty()
end
function X:QUEST_TURNED_IN(_, qid)
  qid = tonumber(qid)
  local t0 = qid and self.char and self.char.accepted[qid]
  if t0 then
    self.char.accepted[qid] = nil
    local d = epoch() - t0
    local L = charLearn()
    if L and learning() and d > 5 and d < 3 * 3600 then acc(L.quests, qid, d, 5) end
  end
end
local lastXP, lastMax
function X:PLAYER_XP_UPDATE()
  local xp, mx = xpNow()
  if lastXP then
    local d = xp - lastXP
    if d < 0 and lastMax then d = d + lastMax end
    if d > 0 and d < 200000 then session.xp = session.xp + d end
  end
  lastXP, lastMax = xp, mx
end
local EAT = { ["Nourriture"] = true, ["Boisson"] = true, ["Food"] = true, ["Drink"] = true,
  ["Manger"] = true, ["Boire"] = true, ["Food & Drink"] = true, ["Nourriture et boisson"] = true }
function X:UNIT_AURA(_, unit)
  if unit ~= "player" or inCombat() then return end
  local eating = false
  local API = T.API
  if API and API.Auras then
    for _, a in ipairs(try(API.Auras, "player", "HELPFUL") or {}) do
      if type(a) == "table" and type(a.name) == "string" and not secret(a.name) and EAT[a.name] then eating = true end
    end
  end
  if eating and not session.eatT0 then session.eatT0 = now()
  elseif not eating and session.eatT0 then session.eat = session.eat + (now() - session.eatT0); session.eatT0 = nil end
end
local function flushSession()
  if not learning() or not session.t0 then return end
  local dur = now() - session.t0
  local L = charLearn()
  if L and dur >= 900 then
    acc(L, "xph", session.xp * 3600 / dur, 50)
    acc(L, "downtime", max(0, min(0.9, session.eat / dur)), 50)
  end
  session.t0, session.xp, session.eat = now(), 0, 0
end

-- ------------------------------------------------------------------ source de données Questie (lecture seule)
-- Adapter: Q(id, key), N(id, key), O(id, key), I(id, key), ids() -> list, map(areaId) -> uiMapID, src
local function libDB()
  local lib = G("LibQuestieDB")
  if type(lib) ~= "table" or type(lib.RequireContract) ~= "function" then return nil end
  local ok = try(lib.RequireContract, 4)
  if not ok then return nil end
  local Q, N, O, I = lib.Quest or G("QuestDB"), lib.Npc or G("NpcDB"), lib.Object or G("ObjectDB"), lib.Item or G("ItemDB")
  if type(Q) ~= "table" or type(Q.Get) ~= "function" then return nil end
  local function getter(E) return function(id, key) if type(E) == "table" then return try(E.Get, id, key) end end end
  local area2map
  local function map(areaId)
    if area2map == nil then
      area2map = false
      local zdb = lib.Support and try(lib.Support.Get, "ZoneDB")
      local s = type(zdb) == "table" and type(zdb.private) == "table" and zdb.private.areaIdToUiMapId
      if type(s) == "string" then
        local ls = G("loadstring")
        local f = ls and ls(s)
        local t = f and try(f)
        if type(t) == "table" then area2map = t end
      elseif type(s) == "table" then area2map = s end
    end
    return area2map and tonumber(area2map[areaId]) or nil
  end
  local xpdb
  local function qxp(id)
    if xpdb == nil then
      local s = lib.Support and try(lib.Support.Get, "QuestXP")
      xpdb = type(s) == "table" and type(s.db) == "table" and s.db or false
    end
    local e = xpdb and xpdb[id]
    return type(e) == "table" and tonumber(e[2]) or nil
  end
  return { src = "LibQuestieDB", Q = getter(Q), N = getter(N), O = getter(O), I = getter(I),
    ids = function() return try(Q.GetAllIds) end, map = map, xp = qxp,
    enum = lib.Enum }
end
local function legacyDB()
  local L = G("QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules")
  local db = type(mods) == "table" and rawget(mods, "QuestieDB")
  if type(db) ~= "table" or type(db.QueryQuestSingle) ~= "function" then return nil end
  local zdb = rawget(mods, "ZoneDB")
  local function q(fn) return function(id, key) if type(db[fn]) == "function" then return try(db[fn], id, key) end end end
  return { src = "QuestieLoader", Q = q("QueryQuestSingle"), N = q("QueryNPCSingle"), O = q("QueryObjectSingle"),
    I = q("QueryItemSingle"),
    ids = function()
      local p = db.QuestPointers or db.questData
      if type(p) ~= "table" then return nil end
      local out = {}
      for id in pairs(p) do if tonumber(id) then out[#out + 1] = tonumber(id) end end
      tsort(out)
      return out
    end,
    map = function(areaId)
      if type(zdb) == "table" and type(zdb.GetUiMapIdByAreaId) == "function" then return tonumber(try(zdb.GetUiMapIdByAreaId, zdb, areaId)) end
    end,
    xp = function() return nil end,
    doable = type(db.IsDoable) == "function" and function(id) return try(db.IsDoable, id) end or nil,
    drop = type(db.GetItemDroprate) == "function" and function(item, npc) return try(db.GetItemDroprate, item, npc) end or nil }
end
function X:Source()
  if self.db and self.db.questie == false then return nil end
  return libDB() or legacyDB()
end

-- ------------------------------------------------------------------ journal & joueur (rapide)
local function logSnapshot()
  local out, n = {}, 0
  local CQ = G("C_QuestLog")
  if type(CQ) == "table" and CQ.GetInfo and CQ.GetNumQuestLogEntries then
    local entries = try(CQ.GetNumQuestLogEntries) or 0
    for i = 1, min(entries, 80) do
      local info = try(CQ.GetInfo, i)
      if type(info) == "table" and not info.isHeader and tonumber(info.questID) and info.questID > 0 then
        local qid = info.questID
        local q = { id = qid, t = info.title, lv = tonumber(info.level) or 0, grp = tonumber(info.suggestedGroup) or 0,
          done = try(CQ.IsComplete, qid) and 1 or 0, obj = {} }
        for j, o in ipairs(try(CQ.GetQuestObjectives, qid) or {}) do
          q.obj[j] = { type = o.type, have = tonumber(o.numFulfilled) or 0, n = tonumber(o.numRequired) or 0,
            fin = o.finished and true or false, text = o.text }
        end
        local tag = CQ.GetQuestTagInfo and try(CQ.GetQuestTagInfo, qid)
        if type(tag) == "table" and (tag.isElite or tag.tagID == 1) then q.elite = 1 end
        out[qid] = q; n = n + 1
      end
    end
    return out, n
  end
  local entries = try(G("GetNumQuestLogEntries")) or 0
  for i = 1, min(entries, 80) do
    local title, level, grp, header, _, complete, _, qid = try(G("GetQuestLogTitle"), i)
    if title and not header and tonumber(qid) then
      local q = { id = qid, t = title, lv = tonumber(level) or 0, grp = tonumber(grp) or 0,
        done = (complete == 1 or complete == true) and 1 or 0, obj = {} }
      for j = 1, (try(G("GetNumQuestLeaderBoards"), i) or 0) do
        local text, typ, fin = try(G("GetQuestLogLeaderBoard"), j, i)
        local have, need = tostring(text or ""):match("(%d+)%s*/%s*(%d+)")
        q.obj[j] = { type = typ, have = tonumber(have) or 0, n = tonumber(need) or 0, fin = fin and true or false, text = text }
      end
      out[qid] = q; n = n + 1
    end
  end
  return out, n
end
X.LogSnapshot = logSnapshot
local function isDone(qid)
  local f = C("C_QuestLog", "IsQuestFlaggedCompleted") or G("IsQuestFlaggedCompleted")
  return f and try(f, qid) and true or false
end
X.IsQuestDone = isDone

local function knownTaxi()
  local out, seenNode = {}, {}
  local f = C("C_TaxiMap", "GetTaxiNodesForMap")
  if not f then return out end
  for _, m in ipairs({ 1414, 1415, try(C("C_Map", "GetBestMapForUnit"), "player") }) do
    for _, nd in ipairs(try(f, m) or {}) do
      if type(nd) == "table" and nd.nodeID and nd.isUndiscovered == false and not seenNode[nd.nodeID] then
        seenNode[nd.nodeID] = true; out[#out + 1] = nd.nodeID
      end
    end
  end
  tsort(out)
  return out
end

function X:PlayerState()
  local xp, xpMax = xpNow()
  local map, x, y = Geo.Player()
  local _, runSpeed = try(G("GetUnitSpeed"), "player")
  local cap = try(C("C_QuestLog", "GetMaxNumQuestsCanAccept")) or tonumber(G("MAX_QUESTLOG_QUESTS")) or 20
  local _, logCount = logSnapshot()
  local cdS, cdD = try(C("C_Container", "GetItemCooldown"), 6948)
  if not cdS then cdS, cdD = try(G("GetItemCooldown"), 6948) end
  local cd = (tonumber(cdS) and cdS > 0 and tonumber(cdD)) and max(0, floor(cdS + cdD - now())) or 0
  local n = try(G("GetNumGroupMembers")) or 0
  local p = {
    class = T.PlayerClass and T:PlayerClass() or nil, level = num(try(G("UnitLevel"), "player")) or 1,
    xp = xp, xpMax = xpMax, rested = num(try(G("GetXPExhaustion"))) or 0,
    faction = try(G("UnitFactionGroup"), "player"), map = map, x = x, y = y,
    speed = (num(runSpeed) and runSpeed > 0) and r1(runSpeed) or 7.0, mount = 0,
    logMax = tonumber(cap) or 20, logCount = logCount,
    hearth = { cd = cd, name = try(G("GetBindLocation")) },
    taxi = knownTaxi(), group = max(1, n), raid = try(G("IsInRaid")) and true or false,
  }
  local SQ = T.Squad
  local lm = SQ and SQ.char and tonumber(SQ.char.leaveAt) or 0
  if lm > 0 then p.session = max(0, floor((lm - epoch()) / 60)) end
  local B = T.Boost
  local xph = B and type(B.XPPerHour) == "function" and try(B.XPPerHour, B)
  if tonumber(xph) then p.xph = floor(xph) end
  return p
end

-- ------------------------------------------------------------------ construction de l'export (coroutine)
local KIND_ORDER = { "kill", "obj", "item" }   -- default Questie order: creatures, objects, items
local LOGTYPE = { monster = "kill", item = "item", object = "obj", event = "explore", reputation = "other",
  log = "other", spell = "spell", player = "other" }

local function cluster(spawns, map2)
  -- spawns = { [areaId] = { {x, y}, ... } } → up to 3 clusters {map, x, y, sp, r}, biggest first
  if type(spawns) ~= "table" then return nil end
  local cells = {}
  for area, pts in pairs(spawns) do
    if type(pts) == "table" then
      local m = map2(area)
      for i = 1, min(#pts, 200) do
        local p = pts[i]
        local x, y = type(p) == "table" and tonumber(p[1]), type(p) == "table" and tonumber(p[2])
        if m and x and y and x >= 0 then
          local k = m .. ":" .. floor(x / 6) .. ":" .. floor(y / 6)
          local c = cells[k]
          if not c then c = { map = m, sx = 0, sy = 0, sp = 0, pts = {} }; cells[k] = c end
          c.sx, c.sy, c.sp = c.sx + x, c.sy + y, c.sp + 1
          c.pts[#c.pts + 1] = p
        end
      end
    end
  end
  local list = {}
  for _, c in pairs(cells) do
    local cx, cy = c.sx / c.sp, c.sy / c.sp
    local rr = 0
    for _, p in ipairs(c.pts) do rr = max(rr, ((p[1] - cx) ^ 2 + (p[2] - cy) ^ 2) ^ 0.5) end
    list[#list + 1] = { map = c.map, x = r1(cx), y = r1(cy), sp = c.sp, r = floor(rr * 25 + 30) }
  end
  tsort(list, function(a, b) if a.sp ~= b.sp then return a.sp > b.sp end return a.x < b.x end)
  return list
end

local function firstPos(db, kind, id, maps)
  local getter = kind == "obj" and db.O or db.N
  local cl = cluster(getter(id, "spawns"), db.map)
  local best
  for _, c in ipairs(cl or {}) do
    if maps and maps[c.map] then return c end
    best = best or c
  end
  return best
end

local function raceOK(db, mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local _, _, raceId = try(G("UnitRace"), "player")
  local bitv = db.enum and type(db.enum.raceMaskById) == "table" and db.enum.raceMaskById[raceId]
  bitv = tonumber(bitv) or (raceId and 2 ^ (raceId - 1))
  if not bitv then return true end
  return floor(mask / bitv) % 2 == 1
end
local function classOK(mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local _, _, classId = try(G("UnitClass"), "player")
  if not tonumber(classId) then return true end
  return floor(mask / 2 ^ (classId - 1)) % 2 == 1
end

-- build one quest record (schema §1); `lq` = my log entry (or nil)
local function questRecord(db, id, lq, st, maps, budget)
  local Q = db.Q
  local name = Q(id, "name")
  if name == nil and not lq then return nil end
  local rec = { id = id, t = (lq and lq.t) or name, lv = tonumber(Q(id, "questLevel")) or (lq and lq.lv) or 0,
    rl = tonumber(Q(id, "requiredLevel")) or 0, st = st, done = lq and lq.done or 0,
    flags = tonumber(Q(id, "questFlags")) or 0, elite = lq and lq.elite or 0, grp = lq and lq.grp or 0,
    xp = db.xp(id) or 0 }
  if rec.lv <= 0 and lq then rec.lv = lq.lv end
  local zone = tonumber(Q(id, "zoneOrSort")) or 0
  if zone > 0 then rec.zone = db.map(zone) end
  local sf = tonumber(Q(id, "specialFlags")) or 0
  if floor(sf / 2) % 2 == 1 then rec.esc = 1 end
  if rec.flags and floor(rec.flags / 2) % 2 == 1 then rec.esc = 1 end
  local function notDone(list)
    if type(list) ~= "table" then return nil end
    local out = {}
    for _, p in ipairs(list) do if tonumber(p) and not isDone(p) then out[#out + 1] = p end end
    return #out > 0 and out or nil
  end
  rec.pre = notDone(Q(id, "preQuestGroup"))
  local any = Q(id, "preQuestSingle")
  if type(any) == "table" then
    local one = false
    for _, p in ipairs(any) do if isDone(p) then one = true end end
    if not one and #any > 0 then rec.preAny = any end
  end
  local ex = Q(id, "exclusiveTo")
  if type(ex) == "table" and #ex > 0 then rec.excl = ex end
  -- starter / finisher
  if not lq then
    local sb = Q(id, "startedBy")
    if type(sb) == "table" then
      if type(sb[1]) == "table" and sb[1][1] then
        local c = firstPos(db, "npc", sb[1][1], maps)
        if c then rec.s = { map = c.map, x = c.x, y = c.y, k = "npc", id = sb[1][1] } end
      elseif type(sb[2]) == "table" and sb[2][1] then
        local c = firstPos(db, "obj", sb[2][1], maps)
        if c then rec.s = { map = c.map, x = c.x, y = c.y, k = "obj", id = sb[2][1] } end
      elseif type(sb[3]) == "table" and sb[3][1] then
        rec.s = { k = "item", id = sb[3][1] }
      end
    end
  end
  local fb = Q(id, "finishedBy")
  if type(fb) == "table" then
    if type(fb[1]) == "table" and fb[1][1] then
      local c = firstPos(db, "npc", fb[1][1], maps)
      if c then rec.f = { map = c.map, x = c.x, y = c.y, k = "npc", id = fb[1][1] } end
    elseif type(fb[2]) == "table" and fb[2][1] then
      local c = firstPos(db, "obj", fb[2][1], maps)
      if c then rec.f = { map = c.map, x = c.x, y = c.y, k = "obj", id = fb[2][1] } end
    end
  end
  budget()
  -- objectives: DB entries by kind, then matched to the log's objective order by type
  local objs = Q(id, "objectives")
  local byKind = { kill = {}, obj = {}, item = {} }
  if type(objs) == "table" then
    for slot, kind in ipairs({ "kill", "obj", "item" }) do
      for _, e in ipairs(type(objs[slot]) == "table" and objs[slot] or {}) do
        local oid = type(e) == "table" and tonumber(e[1]) or tonumber(e)
        if oid then byKind[kind][#byKind[kind] + 1] = { k = kind, id = oid } end
      end
    end
    if type(objs[5]) == "table" then   -- killCredit {{npcIds}, rootNpc, text}
      local kc = objs[5][1] or objs[5]
      local root = type(kc) == "table" and (tonumber(kc[2]) or (type(kc[1]) == "table" and tonumber(kc[1][1])))
      if root then tinsert(byKind.kill, 1, { k = "kill", id = root }) end
    end
  end
  local order = {}
  if lq and #lq.obj > 0 then
    local used = { kill = 0, obj = 0, item = 0 }
    for j, lo in ipairs(lq.obj) do
      local kind = LOGTYPE[lo.type or ""] or "other"
      local e
      if byKind[kind] then used[kind] = used[kind] + 1; e = byKind[kind][used[kind]] end
      local o = { k = kind, id = e and e.id or 0, n = lo.n, have = lo.have }
      order[j] = o
    end
  else
    for _, kind in ipairs(KIND_ORDER) do
      for _, e in ipairs(byKind[kind]) do order[#order + 1] = { k = kind, id = e.id, n = 1 } end
    end
  end
  for _, o in ipairs(order) do
    if o.id and o.id > 0 then
      local spawnsOf
      if o.k == "kill" then
        spawnsOf = { { "npc", o.id } }
        o.ml, o.mx, o.rank = tonumber(db.N(o.id, "minLevel")), tonumber(db.N(o.id, "maxLevel")), tonumber(db.N(o.id, "rank"))
      elseif o.k == "obj" then
        spawnsOf = { { "obj", o.id } }
      elseif o.k == "item" then
        local drops = db.I(o.id, "npcDrops")
        local odrops = db.I(o.id, "objectDrops")
        spawnsOf = {}
        if type(drops) == "table" and #drops > 0 then
          o.npc = {}
          for i = 1, min(#drops, 6) do o.npc[i] = drops[i]; spawnsOf[#spawnsOf + 1] = { "npc", drops[i] } end
          o.ml, o.mx, o.rank = tonumber(db.N(drops[1], "minLevel")), tonumber(db.N(drops[1], "maxLevel")), tonumber(db.N(drops[1], "rank"))
          if db.drop then local p = tonumber(db.drop(o.id, drops[1])) if p and p > 0 then o.p = r1(p) / 100 end end
        elseif type(odrops) == "table" and #odrops > 0 then
          for i = 1, min(#odrops, 4) do spawnsOf[#spawnsOf + 1] = { "obj", odrops[i] } end
        end
        local fl = tonumber(db.I(o.id, "flags")) or 0
        if floor(fl / 2048) % 2 == 1 then o.plf = 1 end
      end
      local all = {}
      for _, s in ipairs(spawnsOf or {}) do
        for _, c in ipairs(cluster((s[1] == "obj" and db.O or db.N)(s[2], "spawns"), db.map) or {}) do all[#all + 1] = c end
        budget()
      end
      tsort(all, function(a, b)
        local am, bm = maps[a.map] and 1 or 0, maps[b.map] and 1 or 0
        if am ~= bm then return am > bm end
        return a.sp > b.sp
      end)
      if all[1] then
        o.map, o.x, o.y, o.r, o.sp = all[1].map, all[1].x, all[1].y, all[1].r, all[1].sp
        if all[2] then o.alt = { { map = all[2].map, x = all[2].x, y = all[2].y, sp = all[2].sp } } end
        if all[3] and o.alt then o.alt[2] = { map = all[3].map, x = all[3].x, y = all[3].y, sp = all[3].sp } end
      end
    end
  end
  rec.o = order
  return rec
end

local function serializedSize(v, depth)
  depth = depth or 0
  if depth > 8 then return 0 end
  local t = type(v)
  if t == "table" then
    local s = 2
    for k, x in pairs(v) do s = s + 6 + serializedSize(k, depth + 1) + serializedSize(x, depth + 1) end
    return s
  elseif t == "string" then return #v + 2 end
  return 6
end
X.Size = serializedSize

function X:Build(yieldFn)
  local budget = yieldFn or function() end
  local db = self:Source()
  local log = logSnapshot()
  local player = self:PlayerState()
  local exp = { v = 1, made = epoch(), char = T.CharKey and T:CharKey() or "?", src = db and db.src or "log",
    player = player, maps = {}, quests = {}, squad = {} }
  local maps = {}          -- uiMapID set (for map rects)
  local function useMap(m) if tonumber(m) then maps[tonumber(m)] = true end end
  useMap(player.map)
  -- squad
  local SQ = T.Squad
  local squadQ = {}        -- [qid] = { names }
  if SQ and type(SQ.Members) == "function" and try(SQ.IsActive, SQ) then
    local me = try(G("UnitName"), "player")
    for _, name in ipairs(try(SQ.Members, SQ) or {}) do
      if name ~= me then
        local st = try(SQ.State, SQ, name) or {}
        local qs = try(SQ.Quests, SQ, name) or {}
        local m = { key = name, class = st.c, level = st.l, xp = st.x, map = st.m, speed = 7.0, dps = 0, q = {}, done = {} }
        if st.m and st.py and st.px then m.x, m.y = Geo.FromWorld(st.m, st.py, st.px) end
        for qid in pairs(qs) do
          qid = tonumber(qid)
          if qid then m.q[#m.q + 1] = qid; squadQ[qid] = squadQ[qid] or {}; tinsert(squadQ[qid], name) end
        end
        tsort(m.q)
        exp.squad[#exp.squad + 1] = m
        useMap(st.m)
      end
    end
  end
  -- zones: current + 2 neighbours (most referenced by my log / the squad's quests)
  local cap = tonumber(self.db and self.db.maxQuests) or 300
  local picked = {}
  local function add(rec)
    if rec and not picked[rec.id] and #exp.quests < cap then
      picked[rec.id] = true
      exp.quests[#exp.quests + 1] = rec
      useMap(rec.zone)
      if rec.s then useMap(rec.s.map) end
      if rec.f then useMap(rec.f.map) end
      for _, o in ipairs(rec.o or {}) do useMap(o.map) end
      return true
    end
  end
  local ok = true
  if db then
    for qid, lq in pairs(log) do
      local rec = questRecord(db, qid, lq, "log", maps, budget)
      if rec then rec.who = squadQ[qid] end
      add(rec)
    end
    for qid, who in pairs(squadQ) do
      if not log[qid] and not isDone(qid) then
        local rec = questRecord(db, qid, nil, "avail", maps, budget)
        if rec then rec.who = who; add(rec) end
      end
    end
    -- neighbour zones: count maps referenced so far
    local cnt = {}
    for _, rec in ipairs(exp.quests) do
      for _, m in ipairs({ rec.zone, rec.f and rec.f.map }) do if m and m ~= player.map then cnt[m] = (cnt[m] or 0) + 1 end end
    end
    local zl = {}
    for m, c in pairs(cnt) do zl[#zl + 1] = { m = m, c = c } end
    tsort(zl, function(a, b) if a.c ~= b.c then return a.c > b.c end return a.m < b.m end)
    local zones = {}
    if player.map then zones[player.map] = true end
    for i = 1, min(2, #zl) do zones[zl[i].m] = true end
    -- available quests in those zones (one pass over the DB ids, budgeted)
    local ids = db.ids() or {}
    local lvl = player.level or 1
    for i = 1, #ids do
      local id = ids[i]
      if not picked[id] and not log[id] then
        local zone = tonumber(db.Q(id, "zoneOrSort")) or 0
        local m = zone > 0 and db.map(zone)
        if m and zones[m] then
          local rl, ql = tonumber(db.Q(id, "requiredLevel")) or 0, tonumber(db.Q(id, "questLevel")) or 0
          local sf = tonumber(db.Q(id, "specialFlags")) or 0
          if rl <= lvl + 2 and (ql <= 0 or ql >= lvl - 8) and sf % 2 == 0 and not isDone(id)
              and raceOK(db, db.Q(id, "requiredRaces")) and classOK(db.Q(id, "requiredClasses"))
              and (not db.doable or db.doable(id) ~= false) then
            local rec = questRecord(db, id, nil, "avail", maps, budget)
            if rec and (rec.pre == nil or (log[rec.pre[1]] ~= nil)) and (rec.preAny == nil or log[rec.preAny[1]]) then add(rec) end
          end
        end
      end
      if i % 50 == 0 then budget() end
      if #exp.quests >= cap then break end
    end
  else
    ok = false
    for qid, lq in pairs(log) do       -- no Questie: just my log (titles, objectives, waypoints)
      local rec = { id = qid, t = lq.t, lv = lq.lv, rl = 0, xp = 0, st = "log", done = lq.done, elite = lq.elite or 0,
        grp = lq.grp, o = {} }
      for j, lo in ipairs(lq.obj) do rec.o[j] = { k = LOGTYPE[lo.type or ""] or "other", id = 0, n = lo.n, have = lo.have } end
      local wp = C("C_QuestLog", "GetNextWaypoint")
      local m, x, y
      if wp then m, x, y = try(wp, qid) end
      if tonumber(m) and tonumber(x) then rec.f = { map = m, x = r1(x * 100), y = r1(y * 100), k = "npc", id = 0 } end
      rec.who = squadQ[qid]
      add(rec)
    end
  end
  tsort(exp.quests, function(a, b)
    if a.st ~= b.st then return a.st == "log" end
    return a.id < b.id
  end)
  -- map rects
  for m in pairs(maps) do
    local r = Geo.Rect(m)
    local info = try(C("C_Map", "GetMapInfo"), m)
    local e = { id = m, name = type(info) == "table" and info.name or nil }
    if r then e.c, e.x0, e.y0, e.x1, e.y1 = r.c, r1(r.x0), r1(r.y0), r1(r.x1), r1(r.y1) end
    exp.maps[#exp.maps + 1] = e
  end
  tsort(exp.maps, function(a, b) return a.id < b.id end)
  -- size cap (~300 KB): drop neighbour avail quests, then alt clusters, then squad-only quests
  local function shrink()
    if serializedSize(exp) <= 300000 then return true end
    for i = #exp.quests, 1, -1 do
      local q = exp.quests[i]
      if q.st == "avail" and not q.who and q.zone ~= player.map then table.remove(exp.quests, i) end
    end
    if serializedSize(exp) <= 300000 then return true end
    for _, q in ipairs(exp.quests) do for _, o in ipairs(q.o or {}) do o.alt = nil end end
    if serializedSize(exp) <= 300000 then return true end
    for i = #exp.quests, 1, -1 do if exp.quests[i].st == "avail" then table.remove(exp.quests, i) end end
    return serializedSize(exp) <= 300000
  end
  shrink()
  -- items tracked for drop learning (in memory only)
  local track = {}
  for _, q in ipairs(exp.quests) do
    for _, o in ipairs(q.o or {}) do
      if o.k == "item" and o.npc and o.id and o.id > 0 then
        track[o.id] = track[o.id] or {}
        for _, n in ipairs(o.npc) do track[o.id][n] = true end
      end
    end
  end
  return exp, track, ok
end

-- store: TreeAIDB.export (the in-memory copy keeps a non-saved index for drop learning)
function X:Store(exp, track)
  local db = G("TreeAIDB")
  if type(db) ~= "table" or type(exp) ~= "table" then return end
  db.export = exp
  self.data = { export = exp, _itemNpcs = track or {} }
  self.builtAt = now()
  T:Fire("EXPORT_UPDATED", exp)
end
function X:Data() return self.data and self.data.export or (type(G("TreeAIDB")) == "table" and G("TreeAIDB").export) or nil end

-- coroutine driver: ≤ 2 ms per frame, out of combat only (paused in combat)
local driver
local co
local function resume()
  if not co then return end
  if inCombat() then return end
  local frameStart = clock()
  X._frameStart = frameStart
  local ok, err = coroutine.resume(co)
  if not ok then X.lastError = tostring(err); T:Debug("Export: " .. tostring(err)); co = nil
  elseif coroutine.status(co) == "dead" then co = nil end
  if not co and driver then driver:Hide() end
end
function X:Rebuild()
  if co then return false end
  local yieldFn = function()
    X._ops = (X._ops or 0) + 1
    if clock() - (X._frameStart or 0) > 2 or X._ops >= 40 then X._ops = 0; coroutine.yield() end
  end
  co = coroutine.create(function()
    local exp, track = X:Build(yieldFn)
    X:Store(exp, track)
  end)
  if not driver then
    local CF = G("CreateFrame")
    driver = CF and try(CF, "Frame")
    if driver then driver:SetScript("OnUpdate", resume) end
  end
  if driver then driver:Show() else while co do resume() end end
  return true
end
function X:Dirty()
  self.dirty = true
  if not (T.RunOutOfCombat and T.After) then return end
  if self._pending then return end
  self._pending = true
  T:After(5, function()
    self._pending = false
    T:RunOutOfCombat("Export:rebuild", function() X:Rebuild() end)
  end)
end

-- quick refresh at logout: player state + my log progress (no DB scan)
function X:QuickRefresh()
  local exp = self:Data()
  local db = G("TreeAIDB")
  if type(exp) ~= "table" or type(db) ~= "table" then
    local log = logSnapshot()
    exp = { v = 1, made = epoch(), char = T.CharKey and T:CharKey() or "?", src = "log", maps = {}, quests = {}, squad = {} }
    for qid, lq in pairs(log) do
      local rec = { id = qid, t = lq.t, lv = lq.lv, rl = 0, xp = 0, st = "log", done = lq.done, grp = lq.grp, o = {} }
      for j, lo in ipairs(lq.obj) do rec.o[j] = { k = LOGTYPE[lo.type or ""] or "other", id = 0, n = lo.n, have = lo.have } end
      exp.quests[#exp.quests + 1] = rec
    end
  else
    local log = logSnapshot()
    local qs = exp.quests or {}
    for i = #qs, 1, -1 do
      local q = qs[i]
      local lq = log[q.id]
      if lq then
        q.st, q.done = "log", lq.done
        for j, lo in ipairs(lq.obj) do if q.o and q.o[j] then q.o[j].have = lo.have end end
      elseif q.st == "log" then
        table.remove(qs, i)            -- turned in or abandoned since the last build
      end
    end
  end
  exp.made = epoch()
  exp.player = self:PlayerState()
  db.export = exp
end

-- ------------------------------------------------------------------ cycle de vie
function X:OnEnable()
  session.t0 = now()
  lastXP, lastMax = xpNow()
  for _, ev in ipairs({ "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED", "CHAT_MSG_COMBAT_XP_GAIN", "PLAYER_TARGET_CHANGED",
    "LOOT_OPENED", "TAXIMAP_OPENED", "PLAYER_CONTROL_LOST", "PLAYER_CONTROL_GAINED", "QUEST_ACCEPTED", "QUEST_TURNED_IN",
    "PLAYER_XP_UPDATE" }) do self:RegisterEvent(ev) end
  self:RegisterUnitEvent("UNIT_AURA", "player")
  self:RegisterEvent("ZONE_CHANGED_NEW_AREA", function() X:Dirty() end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() X:Dirty() end)
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() X:Dirty() end)
  self:RegisterEvent("PLAYER_LOGOUT", function() pcall(flushSession); pcall(X.QuickRefresh, X) end)
  self:Dirty()
end
function X:OnDisable() co = nil if driver then driver:Hide() end end

T:RegisterSlash("export", function()
  if inCombat() then T:Print("Export : hors combat seulement.") return end
  X:Rebuild()
  T:Print("Export du planificateur en cours… (/reload pour l'écrire, TreeAI Coach le lit ensuite)")
end, "export : données pour le planificateur de TreeAI Coach")
