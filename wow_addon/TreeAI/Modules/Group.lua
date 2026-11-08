-- TreeAI — module Groupe (agent F)
-- Synchro des quêtes entre amis qui ont TreeAI (C_ChatInfo.SendAddonMessage, préfixe "TREEAI",
-- messages autorisés par Blizzard) : qui doit encore quel objectif, qui est loin / dans une autre zone,
-- écart de niveau. Rien n'est automatisé : uniquement des conseils.
-- Protocole (sous-types préfixés par une lettre + version, ignorés par les autres modules) :
--   S1|niveau|classe|mapID|y|x|instance|zone      état (toutes les 20 s en groupe, ou au changement)
--   Q1|seq|i|n|id=h/n.h/n;id=C;...                quêtes, découpées en morceaux de ≤ 200 caractères
--   R1                                            « renvoyez-moi vos infos » (nouveau membre)
-- Lua 5.1, aucune globale créée sauf les variables de commande slash si le cœur n'en fournit pas.
local _, ns = ...
ns = ns or {}

local M = { name = "Group", title = "Groupe" }
ns.Group = M

local PREFIX = "TREEAI"
local FAR_YARDS = 300
local STATUS_EVERY = 20
local MAX_QUESTS = 30

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
    pcall(M.Suggest, M, id, text, prio or 1, { cooldown = every or 120, ttl = math.min(every or 120, 300) })
  elseif T and T.Suggest then
    pcall(T.Suggest, T, id, text, prio or 1)
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

-- Quand le module Squad (cerveau de groupe, protocole v2) est actif, Group se tait : plus d'envoi
-- FSTAT/FQUEST, plus de suggestions ni d'écriture de summary.group. Il garde ses récepteurs et
-- M.members (Squad l'alimente aussi, même forme) pour les modules qui le lisent (Quests.lua).
local function squadActive()
  local T = core()
  local sq = T and T.Squad
  if type(sq) ~= "table" or type(sq.IsActive) ~= "function" then return false end
  local ok, v = pcall(sq.IsActive, sq)
  return ok and v or false
end
M.SquadActive = squadActive

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

local function store()
  local section = "group"
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

local function later(sec, fn)
  if C_Timer and C_Timer.After then C_Timer.After(sec, fn) else fn() end
end

local function print(msg)
  if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cff44dd88TreeAI|r " .. msg) end
end

local function grouped()
  return IsInGroup and IsInGroup() or (GetNumPartyMembers and GetNumPartyMembers() > 0) or false
end

local function channel()
  local T = core()
  if T and T.DefaultChannel then
    local ok, ch = pcall(T.DefaultChannel, T)
    if ok then return ch end
  end
  if IsInRaid and IsInRaid() then return "RAID" end
  if grouped() then return "PARTY" end
  return nil
end

local function shortName(full)
  if type(full) ~= "string" then return nil end
  if Ambiguate then return Ambiguate(full, "none") end
  return (full:gsub("%-.*$", ""))
end

local function myName() return UnitName and UnitName("player") or "?" end

-- unité ("party2", "raid7") d'un nom, ou nil s'il n'est pas dans le groupe
function M.UnitOf(name)
  if not name or not UnitExists then return nil end
  local raid = IsInRaid and IsInRaid()
  local prefix, n = raid and "raid" or "party", raid and 40 or 4
  for i = 1, n do
    local u = prefix .. i
    if UnitExists(u) then
      local un = GetUnitName and GetUnitName(u, true) or (UnitName and UnitName(u))
      if un == name or shortName(un) == name then return u end
    end
  end
  return nil
end

-- ---------------------------------------------------------------- envoi
-- Le cœur (agent B) fournit TreeAI:Send / RegisterComm (découpage, anti-flood) : on l'utilise s'il est là.
local function comms()
  local T = core()
  if T and T.Send and T.RegisterComm then return T end
  return nil
end

local function send(msg)
  local ch = channel()
  if not ch or #msg > 250 then return false end
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then
    return pcall(C_ChatInfo.SendAddonMessage, PREFIX, msg, ch)
  elseif SendAddonMessage then
    return pcall(SendAddonMessage, PREFIX, msg, ch)
  end
  return false
end

local function clean(s, max)
  s = tostring(s or ""):gsub("[|;=\n]", " ")
  return s:sub(1, max or 40)
end

local function scan()
  local f = ns.QuestScan
  if type(f) ~= "function" then return {} end
  local ok, qs = pcall(f)
  return ok and type(qs) == "table" and qs or {}
end

-- Encode les quêtes : "id=h/n.h/n" ou "id=C" (terminée). Pur, testable.
function M.EncodeQuests(quests)
  local parts = {}
  for _, q in ipairs(quests or {}) do
    if q.id and #parts < MAX_QUESTS then
      if q.done then
        parts[#parts + 1] = q.id .. "=C"
      else
        local o = {}
        for _, ob in ipairs(q.obj or {}) do
          local need = tonumber(ob.need) or 1
          local have = tonumber(ob.have) or (ob.finished and need or 0)
          o[#o + 1] = have .. "/" .. need
        end
        parts[#parts + 1] = q.id .. "=" .. table.concat(o, ".")
      end
    end
  end
  return table.concat(parts, ";")
end

function M.DecodeQuests(payload)
  local out, count = {}, 0
  if type(payload) ~= "string" then return out end
  for id, body in payload:gmatch("(%d+)=([^;]*)") do
    count = count + 1
    if count > MAX_QUESTS then break end
    local q = { obj = {} }
    if body == "C" then
      q.done = true
    else
      for h, n in body:gmatch("(%d+)/(%d+)") do
        q.obj[#q.obj + 1] = { have = tonumber(h), need = tonumber(n) }
      end
    end
    out[tonumber(id)] = q
  end
  return out
end

-- Découpe en morceaux ≤ size, sans couper une quête.
function M.Chunk(payload, size)
  size = size or 200
  local chunks, cur = {}, ""
  for item in (payload or ""):gmatch("[^;]+") do
    if #cur > 0 and #cur + #item + 1 > size then chunks[#chunks + 1] = cur cur = "" end
    cur = (#cur > 0) and (cur .. ";" .. item) or item
  end
  if #cur > 0 or #chunks == 0 then chunks[#chunks + 1] = cur end
  return chunks
end

local seq = 0
local lastQuestSend, questQueued = -1e9, false
function M.SendQuests()
  if not channel() or squadActive() then return end
  local delay = 10 - (now() - lastQuestSend)
  if delay > 0 then
    if not questQueued then questQueued = true later(delay, function() questQueued = false M.SendQuests() end) end
    return
  end
  lastQuestSend = now()
  local T = comms()
  if T then pcall(T.Send, T, "FQUEST", M.EncodeQuests(scan())) return end
  seq = (seq % 99) + 1
  local chunks = M.Chunk(M.EncodeQuests(scan()), 200)
  for i, c in ipairs(chunks) do
    later((i - 1) * 1.1, function() send(("Q1|%d|%d|%d|%s"):format(seq, i, #chunks, c)) end)
  end
end

local function position()
  if not UnitPosition then return 0, 0, -1 end
  local y, x, _, inst = UnitPosition("player")
  if not y then return 0, 0, -1 end
  return math.floor(y + 0.5), math.floor(x + 0.5), inst or -1
end

local function mapID()
  if C_Map and C_Map.GetBestMapForUnit then return C_Map.GetBestMapForUnit("player") or 0 end
  return 0
end

local lastStatus = -1e9
function M.SendStatus(force)
  if not channel() or squadActive() then return end
  if not force and now() - lastStatus < 5 then return end
  lastStatus = now()
  local y, x, inst = position()
  local class = UnitClass and select(2, UnitClass("player")) or "?"
  local zone = GetRealZoneText and GetRealZoneText() or ""
  local lvl = UnitLevel and UnitLevel("player") or 0
  local T = comms()
  if T then
    pcall(T.Send, T, "FSTAT", { l = lvl, c = clean(class, 12), m = mapID(), y = y, x = x, i = inst, z = clean(zone, 40) })
    return
  end
  send(("S1|%d|%s|%d|%d|%d|%d|%s"):format(UnitLevel and UnitLevel("player") or 0, clean(class, 12),
    mapID(), y, x, inst, clean(zone, 40)))
end

-- ---------------------------------------------------------------- réception
local members = {}   -- [nom] = { level, class, mapID, y, x, inst, zone, quests = {[id]=q}, seen }
local buffers = {}   -- [nom] = { seq, n, parts }
M.members = members

local function analyseSoon() end -- défini plus bas

local function accept(sender)
  local name = shortName(sender)
  if not name or name == myName() then return nil end
  -- n'accepter que des membres du groupe (si l'API le permet)
  if UnitExists and not M.UnitOf(name) then return nil end
  return name
end

-- t = { l, c, m, y, x, i, z } (valeurs non fiables : tout est revalidé)
function M.OnStatus(sender, t)
  local name = accept(sender)
  if not name or type(t) ~= "table" or not tonumber(t.l) then return end
  local m = members[name] or { quests = {} }
  m.level, m.class, m.mapID = tonumber(t.l), clean(t.c, 12), tonumber(t.m)
  m.y, m.x, m.inst = tonumber(t.y), tonumber(t.x), tonumber(t.i)
  m.zone, m.seen = clean(t.z, 40), now()
  members[name] = m
  analyseSoon()
end

function M.OnQuests(sender, payload)
  local name = accept(sender)
  if not name or type(payload) ~= "string" or #payload > 4000 then return end
  local m = members[name] or { quests = {} }
  m.quests = M.DecodeQuests(payload)
  m.seen = now()
  members[name] = m
  analyseSoon()
end

function M.OnRequest(sender)
  if not accept(sender) then return end
  later(math.random and (1 + math.random() * 2) or 2, function() M.SendStatus(true) M.SendQuests() end)
end

local function sendRequest()
  if squadActive() then return end
  local T = comms()
  if T then pcall(T.Send, T, "FREQ", "1") else send("R1") end
end

-- Protocole brut (seulement sans le cœur ; avec lui les messages ont la forme 1~TYPE~… et sont ignorés ici)
function M.OnAddonMessage(prefix, msg, _, sender)
  if prefix ~= PREFIX or type(msg) ~= "string" or #msg > 255 then return end
  local name = accept(sender)
  if not name then return end
  local kind = msg:sub(1, 2)
  if kind == "S1" then
    local lvl, class, map, y, x, inst, zone = msg:match("^S1|(%d+)|([^|]*)|(%d+)|(%-?%d+)|(%-?%d+)|(%-?%d+)|(.*)$")
    if not lvl then return end
    M.OnStatus(sender, { l = lvl, c = class, m = map, y = y, x = x, i = inst, z = zone })
  elseif kind == "Q1" then
    local s, i, n, body = msg:match("^Q1|(%d+)|(%d+)|(%d+)|(.*)$")
    s, i, n = tonumber(s), tonumber(i), tonumber(n)
    if not s or not i or not n or n < 1 or n > 10 or i > n then return end
    local b = buffers[name]
    if not b or b.seq ~= s then b = { seq = s, n = n, parts = {}, got = 0 } buffers[name] = b end
    if not b.parts[i] then b.parts[i] = body b.got = b.got + 1 end
    if b.got >= b.n then
      buffers[name] = nil
      M.OnQuests(sender, table.concat(b.parts, ";"))
    end
  elseif kind == "R1" then
    M.OnRequest(sender)
  end
end

-- ---------------------------------------------------------------- analyse
local function questTitle(id, mine)
  if mine and mine.title then return mine.title end
  if C_QuestLog and C_QuestLog.GetQuestInfo then
    local ok, t = pcall(C_QuestLog.GetQuestInfo, id)
    if ok and t then return t end
  end
  return "quête #" .. tostring(id)
end

-- Pur : compare mes quêtes à celles des membres. Renvoie une liste triée
-- { {id, title, need = { {name, text} }, done = {noms}, missing = bool} }
function M.Compare(myQuests, mem)
  local mine, res = {}, {}
  for _, q in ipairs(myQuests or {}) do if q.id then mine[q.id] = q end end
  local seen = {}
  for name, m in pairs(mem or {}) do
    for id, q in pairs(m.quests or {}) do
      local e = seen[id]
      if not e then
        e = { id = id, title = questTitle(id, mine[id]), need = {}, done = {}, missing = not mine[id] }
        seen[id] = e
        res[#res + 1] = e
      end
      if q.done then
        e.done[#e.done + 1] = name
      else
        local bits = {}
        for j, ob in ipairs(q.obj or {}) do
          if ob.have and ob.need and ob.have < ob.need then
            local my = mine[id] and mine[id].obj and mine[id].obj[j]
            local on = _G.TreeAI and _G.TreeAI.ObjectiveName
            local label = my and my.text and (on and on(my.text)
              or my.text:gsub("%s*:?%s*%d+%s*/%s*%d+.*$", "")) or ("obj " .. j)
            bits[#bits + 1] = ("%s %d/%d"):format(label, ob.have, ob.need)
          end
        end
        if #bits > 0 then e.need[#e.need + 1] = { name = name, text = table.concat(bits, ", ") } end
      end
    end
  end
  table.sort(res, function(a, b)
    if a.missing ~= b.missing then return not a.missing end
    if #a.need ~= #b.need then return #a.need > #b.need end
    return a.id < b.id
  end)
  return res
end

local function distanceTo(m)
  local y, x, inst = position()
  if inst == -1 or not m.y or m.inst ~= inst or (m.y == 0 and m.x == 0) then return nil end
  local dy, dx = m.y - y, m.x - x
  return math.sqrt(dy * dy + dx * dx)
end

local farWarned = {}
function M.Analyse()
  local list = M.Compare(scan(), members)
  if squadActive() then
    clear("grp_quest")
    for name in pairs(members) do clear("grp_far_" .. name) clear("grp_lvl_" .. name) end
    return list
  end
  local d = store()
  local inInstance = IsInInstance and IsInInstance()
  local mySummary = {}
  for name, m in pairs(members) do
    local dist = distanceTo(m)
    mySummary[name] = { level = m.level, class = m.class, zone = m.zone, far = dist and math.floor(dist) or nil }
    if not inInstance and not inCombat() then
      local myMap = mapID()
      local t = now()
      if (farWarned[name] or -1e9) + 90 < t then
        if dist and dist > FAR_YARDS then
          farWarned[name] = t
          suggest("grp_far_" .. name, ("%s est loin (~%d m)."):format(name, math.floor(dist * 0.9144 / 10) * 10), 1, 90)
        elseif m.mapID and m.mapID ~= 0 and myMap ~= 0 and m.mapID ~= myMap and m.zone and m.zone ~= "" then
          farWarned[name] = t
          suggest("grp_far_" .. name, ("%s est dans une autre zone : %s."):format(name, m.zone), 1, 300)
        else
          clear("grp_far_" .. name)
        end
      end
    end
    local myLvl = UnitLevel and UnitLevel("player") or 0
    if m.level and math.abs(m.level - myLvl) >= 5 then
      suggest("grp_lvl_" .. name, ("%s est niveau %d (toi %d) : l'XP de groupe en souffre."):format(name, m.level, myLvl), 1, 1800)
    end
  end
  -- Une seule suggestion de quête : celle que le plus d'amis doivent encore faire.
  local top = list[1]
  if top and not top.missing and #top.need > 0 and not inCombat() then
    local who = {}
    for i, n in ipairs(top.need) do if i <= 3 then who[#who + 1] = n.name .. " : " .. n.text end end
    suggest("grp_quest", ("« %s » — %s"):format(top.title, table.concat(who, " ; ")), 1, 180)
  else
    clear("grp_quest")
  end
  if d then
    d.members = mySummary
    local shared = {}
    for i, e in ipairs(list) do
      if i > 20 then break end
      local need = {}
      for _, n in ipairs(e.need) do need[#need + 1] = n.name .. " : " .. n.text end
      shared[#shared + 1] = { id = e.id, title = e.title, need = need, done = e.done, missing = e.missing or nil }
    end
    d.shared = shared
    d.updated = time and time() or nil
  end
  return list
end

local analysePending = false
analyseSoon = function()
  if analysePending then return end
  analysePending = true
  later(2, function() analysePending = false pcall(M.Analyse) end)
end

-- ---------------------------------------------------------------- commande
function M.Report()
  if not next(members) then
    print("Groupe : aucun ami avec TreeAI détecté (il faut l'addon chez eux).")
    return
  end
  for name, m in pairs(members) do
    local dist = distanceTo(m)
    print(("%s — niv %s, %s%s"):format(name, tostring(m.level or "?"), m.zone or "?",
      dist and (" (~%d m)"):format(math.floor(dist * 0.9144)) or ""))
  end
  for i, e in ipairs(M.Compare(scan(), members)) do
    if i > 6 then break end
    local who = {}
    for _, n in ipairs(e.need) do who[#who + 1] = n.name .. " (" .. n.text .. ")" end
    local tail = #who > 0 and ("doit encore : " .. table.concat(who, ", "))
      or (#e.done > 0 and ("fini par " .. table.concat(e.done, ", ")) or "")
    print(("%s« %s » %s"):format(e.missing and "[pas chez toi] " or "", e.title, tail))
  end
end

-- ---------------------------------------------------------------- événements
local ticker
local function startTicker()
  if ticker then return end
  local fn = function() if channel() then M.SendStatus() analyseSoon() end end
  -- (SPIKE) par l'ordonnanceur du cœur (phase propre, jamais dans la même image que les autres)
  local T = core()
  if T and T.NewTicker then ticker = T:NewTicker(STATUS_EVERY, fn) return end
  if not (C_Timer and C_Timer.NewTicker) then return end
  ticker = C_Timer.NewTicker(STATUS_EVERY, fn)
end

local lastRoster = 0
local handlers = {
  PLAYER_LOGIN = function()
    if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
      pcall(C_ChatInfo.RegisterAddonMessagePrefix, PREFIX)
    elseif RegisterAddonMessagePrefix then
      pcall(RegisterAddonMessagePrefix, PREFIX)
    end
    startTicker()
  end,
  CHAT_MSG_ADDON = function(...) if not comms() then M.OnAddonMessage(...) end end,
  GROUP_ROSTER_UPDATE = function()
    -- oublier ceux qui sont partis
    for name in pairs(members) do
      if not M.UnitOf(name) then members[name] = nil clear("grp_far_" .. name) end
    end
    if channel() and now() - lastRoster > 5 then
      lastRoster = now()
      later(2, function() sendRequest() M.SendStatus(true) M.SendQuests() end)
    end
  end,
  QUEST_LOG_UPDATE = function() if channel() then M.SendQuests() analyseSoon() end end,
  ZONE_CHANGED_NEW_AREA = function() later(2, function() M.SendStatus(true) end) end,
  PLAYER_LEVEL_UP = function() later(2, function() M.SendStatus(true) end) end,
  PLAYER_LOGOUT = function() pcall(M.Analyse) end,
}
M.handlers = handlers

if CreateFrame then
  local f = CreateFrame("Frame")
  for ev in pairs(handlers) do pcall(f.RegisterEvent, f, ev) end
  f:SetScript("OnEvent", function(_, ev, ...)
    local h = handlers[ev]
    if M.core and M.IsEnabled and not M:IsEnabled() then return end
    if h then
      local ok, err = pcall(h, ...)
      if not ok and core() and core().Debug then pcall(core().Debug, core(), "Group: " .. tostring(err)) end
    end
  end)
end

do
  local T = core()
  if T and T.RegisterModule then pcall(T.RegisterModule, T, "Group", M) end
  if comms() then
    T:RegisterComm("FSTAT", M, function(_, p, sender) M.OnStatus(sender, p) end)
    T:RegisterComm("FQUEST", M, function(_, p, sender) M.OnQuests(sender, p) end)
    T:RegisterComm("FREQ", M, function(_, _, sender) M.OnRequest(sender) end)
  end
  local cmd = T and (T.RegisterCommand or T.RegisterSlash)
  if not (cmd and pcall(cmd, T, "groupe", function() M.Report() end, "quêtes et position des amis")) and SlashCmdList then
    _G.SLASH_TREEAIGROUP1 = "/taig"
    SlashCmdList["TREEAIGROUP"] = function() M.Report() end
  end
end
