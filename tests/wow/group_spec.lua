-- Groupe (Modules/Group.lua, GROUPMERGE 4.0) : moteur pur + fil v4 + simulation de 4 clients qui échangent leurs
-- messages par un faux canal PARTY (chacun charge Group.lua dans son propre environnement setfenv, avec ses propres
-- API simulées), + un 5e membre sans l'addon et un ami resté en 3.9 (Squad v2). Noms 100 % fictifs.
-- 4.0.1 (GROUPJOIN) : Modules/GroupJoin.lua chargé avec (même module) — positions vraies (géographie réelle des tables
-- de vol : Mille pointes, Les Tarides, Orneval), « Rejoindre » au plus vite / rentable, rendez-vous « J'y vais », vote,
-- trajet partagé + « Suivre le groupe », intention déduite du chat de groupe, une seule ligne du groupe dans « Maintenant ».
-- Usage : lua5.1 tests/wow/group_spec.lua [chemin/Group.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Group.lua"
local jpath = path:gsub("Group%.lua$", "GroupJoin.lua")
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. tostring(msg)) end
end
local function copy(t)
  if type(t) ~= "table" then return t end
  local o = {}
  for k, v in pairs(t) do o[k] = copy(v) end
  return o
end
local function find(list, pat)
  for _, x in ipairs(list or {}) do
    local s = type(x) == "table" and (x.text or "") or tostring(x)
    if s:find(pat, 1, true) then return x end
  end
  return nil
end

local clock = 1000
local NAMES = { "Alpha", "Bravo", "Charlie", "Delta" }
local OUTSIDER = "Echo"   -- dans le groupe, sans TreeAI
-- (GROUPJOIN) le monde : POS[nom] = { carte, x, y } (0..1), vide au début (les tests d'avant restent sans positions) ;
-- rectangles monde des cartes tirés des maîtres de vol de Data/Travel.lua (même repère que UnitPosition)
local POS = {}
local RECTS = { [1441] = { top = -3967.4, left = -434.3, w = 4400, h = 2933.33, c = 1 },
  [1413] = { top = 1611.9, left = 2626.3, w = 10132.6, h = 6755.7, c = 1 },
  [1440] = { top = 4674.0, left = 1702.4, w = 5766.7, h = 3843.75, c = 1 } }
local MAPNAME = { [1441] = "Mille pointes", [1413] = "Les Tarides", [1440] = "Orneval" }
local function world(map, x, y) local r = RECTS[map] if not r then return nil end return r.top - y * r.h, r.left - x * r.w, r.c end
local DUNGEONS = { { key = "RFK", n = "Kraal de Tranchebauge", min = 24, max = 32, kw = { "kraal", "razorfen kraul" } },
  { key = "WC", n = "Cavernes des Lamentations", min = 17, max = 24, kw = { "lamentations", "wailing" } },
  { key = "BFD", n = "Profondeurs de Brassenoire", min = 22, max = 30, kw = { "brassenoire" } } }

-- -------------------------------------------------------------- un client simulé (cœur minimal + API du jeu)
local function newClient(me, cfg, bus)
  local env = setmetatable({}, { __index = _G })
  env._G = env
  local comm, events, tickers = {}, {}, {}
  local T = { settings = { comms = true }, summary = {}, prints = {}, sugg = {}, toasts = {}, sends = {}, tabs = {},
              compass = {}, modules = {}, tabShown = false }
  T.API = {
    ItemCount = function(id) return cfg.items[id] or 0 end,
    FreeSlots = function() return cfg.bags or 10, 64 end,
    ItemInfo = function() return nil end,
    SpellName = function(id) return cfg.spellNames and cfg.spellNames[id] end,
    IsSpellKnown = function(id) return cfg.spells and cfg.spells[id] or false end,
  }
  T.Data = { Macros = { items = { food = { { 117, 1 } }, water = { { 159, 1 } } } }, Dungeons = { list = DUNGEONS } }
  T.Compass = { SetTarget = function(_, tg) T.compass[#T.compass + 1] = tg return tg end, targets = {} }
  function T.Compass.Current()
    local tg = T.compass[#T.compass]
    return tg and not tg.cleared and { kind = "custom", label = tg.label, mapID = tg.mapID, x = tg.x, y = tg.y } or nil
  end
  function T.Compass.ClearWaypoint() T.compass[#T.compass + 1] = { cleared = true } end
  -- (GROUPJOIN) géométrie (Export.Geo), zone de travail (ZonePlan), donjon prévu, trajet, journal — doublures fidèles
  T.Geo = {
    World = function(map, x, y) return world(map, (tonumber(x) or 0) / 100, (tonumber(y) or 0) / 100) end,
    FromWorld = function(map, a, b) local r = RECTS[map] if not r then return nil end return (r.left - b) / r.w * 100, (r.top - a) / r.h * 100 end,
    Player = function() local p = POS[me] if not p then return nil end return p[1], p[2] * 100, p[3] * 100 end,
  }
  T.ZonePlan = { _active = true, calls = {}, char = { mode = "auto", zones = {} } }
  function T.ZonePlan.ZoneOf(m) return m end
  function T.ZonePlan:IsEnabled() return true end
  function T.ZonePlan:SetMode(mode, map)
    self.calls[#self.calls + 1] = { mode, map }
    if mode == "zone" then self.char.mode = "zones"; self.char.zones[map] = true else self.char.mode, self.char.zones = mode, {} end
  end
  function T.ZonePlan:Allowed()
    if self.char.mode ~= "zones" then return { mode = "auto" } end
    local maps = {}
    for k in pairs(self.char.zones) do maps[k] = true end
    return { mode = "zones", maps = maps }
  end
  T.Dungeon = { _active = true, IsEnabled = function() return true end, IsDone = function() return false end }
  function T.Dungeon.SetPlanned(k) T.Dungeon.planned = k end
  function T.Dungeon.FindExact(name) for _, d in ipairs(DUNGEONS) do if d.n == name then return d end end end
  T.Planner = { cursor = 1, StepLabel = function(_, s2) return s2.label end }
  T.Fmt = { Num = function(n) return tostring(math.floor(n)) end }
  function T:RegisterModule(name, tbl)
    tbl.name, tbl.core = name, self
    tbl.db = copy(tbl.defaults); tbl.db.enabled = not cfg.startOff
    tbl.char = copy(tbl.charDefaults)
    tbl._active = true
    function tbl:IsEnabled() return true end
    function tbl:RegisterEvent(ev, fn) events[ev] = events[ev] or {}; table.insert(events[ev], fn) end
    function tbl:On() end
    function tbl:Suggest(id, text, prio, opts)
      T.sugg[name .. ":" .. id] = { text = text, prio = prio, opts = opts }
      T.toasts[#T.toasts + 1] = { text = text, prio = prio, opts = opts }
    end
    function tbl:Clear(id) T.sugg[name .. ":" .. id] = nil end
    function tbl:Print(...) T.prints[#T.prints + 1] = table.concat({ ... }, " ") end
    self.modules[name] = tbl
    return tbl
  end
  function T:Send(kind, payload, channel)
    self.sends[#self.sends + 1] = { t = clock, kind = kind, payload = payload, channel = channel }
    bus[#bus + 1] = { from = me, kind = kind, payload = copy(payload) }
    return true
  end
  function T:CommStatus() return { queued = 0 } end
  function T:RegisterComm(kind, owner, fn) comm[kind] = function(p, s) fn(owner, p, s, "PARTY", kind) end end
  function T:InCombat() return cfg.combat or false end
  function T:Print(...) self.prints[#self.prints + 1] = table.concat({ ... }, " ") end
  T.slash = {}
  function T:RegisterSlash(cmd, fn) self.slash[cmd] = fn end
  function T:NewTicker(_, fn) tickers[#tickers + 1] = fn return { Cancel = function() end } end
  function T:After(_, fn) fn() end
  function T:Fire() end
  function T:Summary() return self.summary end
  function T:SetTabLines(id, lines, source) if source == "Group" then self.tabs[id] = lines end end
  function T:TabShown(id) return id == "group" and self.tabShown end
  function T:PlayerState() return {} end
  function T:SelectTab() end
  T.db = { chars = cfg.dbChars or {}, modules = cfg.dbModules or {} }
  env.TreeAI = T

  local others = {}
  for _, n in ipairs(NAMES) do if n ~= me then others[#others + 1] = n end end
  others[#others + 1] = OUTSIDER
  local function unitName(u)
    if u == "player" then return me end
    local i = tonumber(tostring(u):match("^party(%d)$"))
    return i and others[i] or nil
  end
  local function other(u) return cfg.party and cfg.party[unitName(u) or ""] or {} end
  env.GetTime = function() return clock end
  env.time = function() return 1762500000 + clock end
  env.UnitName = unitName
  env.UnitExists = function(u)
    if u == "pet" then return cfg.pet ~= false end
    if u == "target" then return cfg.target ~= nil end
    if tostring(u):match("^party%dpet$") then return other(u:sub(1, 6)).pet ~= false end
    return unitName(u) ~= nil
  end
  env.IsInGroup = function() return true end
  env.IsInRaid = function() return false end
  env.UnitIsGroupLeader = function(u) return unitName(u) == "Alpha" end
  env.UnitIsConnected = function(u) return other(u).online ~= false end
  env.UnitClass = function(u)
    if u == "player" then return cfg.class, cfg.class end
    local n = unitName(u)
    local c = (n == OUTSIDER) and "ROGUE" or ((cfg.classes or {})[n] or "WARRIOR")
    return c, c
  end
  env.UnitGUID = function(u) return "Player-0-" .. tostring(unitName(u)) end
  env.UnitLevel = function(u)
    if u == "player" then return cfg.level end
    return other(u).level or ((cfg.levels or {})[unitName(u) or ""]) or 22
  end
  env.UnitXP = function() return cfg.xp end
  env.UnitXPMax = function() return 10000 end
  env.GetXPExhaustion = function() return cfg.rested or 0 end
  env.GetRealZoneText = function() return cfg.zone end
  env.GetSubZoneText = function() return "Moulin" end
  env.C_Map = { GetBestMapForUnit = function(u)
      local p = POS[unitName(u) or ""]
      if p then return p[1] end
      return u == "player" and 1424 or nil
    end,
    GetMapInfo = function(id) return { name = MAPNAME[id] or ("Carte " .. id) } end }
  env.IsInInstance = function() return false, "none" end
  env.UnitFactionGroup = function() return "Horde", "Horde" end
  env.UnitHealth = function(u) return u == "player" and 100 or (other(u).hp or 100) end
  env.UnitHealthMax = function() return 100 end
  env.UnitPowerType = function(u)
    local c = select(2, env.UnitClass(u))
    return (c == "WARRIOR" or c == "ROGUE") and 1 or 0
  end
  env.UnitPower = function(u) return u == "player" and (cfg.mana or 100) or (other(u).mana or 100) end
  env.UnitPowerMax = function() return 100 end
  env.UnitIsGhost = function() return false end
  env.UnitIsDeadOrGhost = function(u) return u == "player" and cfg.dead or other(u).dead or false end
  env.UnitIsDead = function() return false end
  env.UnitIsAFK = function(u) return other(u).afk or false end
  env.UnitInRange = function(u) return other(u).far ~= true, true end
  env.UnitIsVisible = function() return true end
  env.UnitGroupRolesAssigned = function() return "NONE" end
  env.UnitPosition = function(u)
    local p = POS[unitName(u) or ""]
    if not p then return nil end
    local n, w, c = world(p[1], p[2], p[3])
    return n, w, 0, c
  end
  env.UnitCanAttack = function(_, u) return u == "target" and cfg.target ~= nil end
  env.UnitIsPlayer = function() return false end
  env.GetInventoryItemDurability = function(s) if s == 1 then return cfg.dur or 90, 100 end end
  env.GetBindLocation = function() return "Moulin-de-Tarren" end
  env.GetItemCooldown = function() return 0, 0 end
  env.GetInventoryItemCount = function() return cfg.ammo or 1500 end
  env.GetQuestID = function() return cfg.turnIn end
  env.NotifyInspect = nil   -- pas d'inspection dans cette simulation (voir le scénario du harnais)
  local function log() return cfg.quests end
  local function byId(id) for _, x in ipairs(log()) do if x.questID == id then return x end end end
  env.C_QuestLog = {
    GetNumQuestLogEntries = function() return #log() end,
    GetInfo = function(i) return log()[i] end,
    IsComplete = function(id) local x = byId(id) return x and x.done end,
    GetQuestObjectives = function(id) local x = byId(id) return x and x.obj or {} end,
    GetMaxNumQuestsCanAccept = function() return 25 end,
    IsPushableQuest = function() return true end,
    IsUnitOnQuest = function(u, id)
      local o = other(u)
      if not o.quests then return nil end
      return o.quests[id] == true
    end,
  }
  local ns = { Quests = { QuestieNext = function(id) return cfg.chain and cfg.chain[id] end } }
  local chunk = assert(loadfile(path))
  setfenv(chunk, env)
  chunk("TreeAI", ns)
  local jf = io.open(jpath, "r")
  if jf then
    jf:close()
    local jchunk = assert(loadfile(jpath))
    setfenv(jchunk, env)
    jchunk("TreeAI", ns)
  end
  T.modules.Quests = { lastList = {} }
  local G = T.modules.Group
  G:OnInit()
  G:OnEnable()
  local c = { name = me, env = env, T = T, G = G, comm = comm, events = events, cfg = cfg, ns = ns, tickers = tickers }
  function c.fire(ev, ...) for _, fn in ipairs(events[ev] or {}) do fn(G, ev, ...) end end
  function c.tick() for _, fn in ipairs(tickers) do fn() end end
  return c
end

local function q(id, title, done, objs, extra)
  local t = { questID = id, title = title, level = 22, done = done, obj = {} }
  for _, o in ipairs(objs) do
    t.obj[#t.obj + 1] = { type = o[1], numFulfilled = o[2], numRequired = o[3], finished = o[2] >= o[3],
      text = o[4] and (o[4] .. " : " .. o[2] .. "/" .. o[3]) or nil }
  end
  for k, v in pairs(extra or {}) do t[k] = v end
  return t
end
local function setDone(quest)
  quest.done = true
  for _, o in ipairs(quest.obj) do o.numFulfilled = o.numRequired; o.finished = true end
end
local H = { isHeader = true, title = "Marche de l'Ouest" }
local cfgs = {
  Alpha = { class = "WARRIOR", level = 22, xp = 5000, zone = "Marche de l'Ouest", items = { [6948] = 1, [117] = 10 },
    classes = { Bravo = "HUNTER", Charlie = "MAGE", Delta = "ROGUE" }, levels = { Bravo = 22, Charlie = 22, Delta = 20 },
    party = { Echo = { quests = { [100] = true, [200] = false } }, Charlie = { mana = 20 } },
    quests = { H, q(100, "Les sangliers", true, { { "item", 10, 10, "Défense de sanglier" } }),
      q(200, "Ogres", false, { { "monster", 3, 8, "Ogre tué" } }),
      q(500, "Sang de lion", true, { { "item", 6, 6, "Sang de lion" } }) } },
  Bravo = { class = "HUNTER", level = 22, xp = 9500, rested = 8000, zone = "Marche de l'Ouest", items = { [6948] = 1 },
    classes = { Alpha = "WARRIOR", Charlie = "MAGE", Delta = "ROGUE" }, levels = { Alpha = 22, Charlie = 22, Delta = 20 },
    quests = { H, q(100, "Les sangliers", true, { { "item", 10, 10 } }), q(200, "Ogres", false, { { "monster", 5, 8 } }),
      q(500, "Sang de lion", true, { { "item", 6, 6 } }) } },
  Charlie = { class = "MAGE", level = 22, xp = 3000, zone = "Marche de l'Ouest", items = {}, mana = 20,
    classes = { Alpha = "WARRIOR", Bravo = "HUNTER", Delta = "ROGUE" }, levels = { Alpha = 22, Bravo = 22, Delta = 20 },
    quests = { H, q(100, "Les sangliers", false, { { "item", 2, 10 } }), q(200, "Ogres", false, { { "monster", 1, 8 } }),
      q(500, "Sang de lion", true, { { "item", 6, 6 } }) } },
  Delta = { class = "ROGUE", level = 20, xp = 9000, zone = "Marche de l'Ouest", items = {},
    classes = { Alpha = "WARRIOR", Bravo = "HUNTER", Charlie = "MAGE" }, levels = { Alpha = 22, Bravo = 22, Charlie = 22 },
    quests = { H, q(100, "Les sangliers", false, { { "item", 5, 10 } }), q(300, "Lettre à Bob", false, { { "event", 0, 1 } }) } },
}

local bus, clients = {}, {}
for _, n in ipairs(NAMES) do clients[n] = newClient(n, cfgs[n], bus) end
for _, n in ipairs(NAMES) do clients[n].fire("GROUP_ROSTER_UPDATE") end

local drop = nil   -- function(m) -> true : message perdu (test de trou)
local function run(sec)
  for _ = 1, sec do
    clock = clock + 1
    if clock % 2 == 0 then for _, n in ipairs(NAMES) do clients[n].tick() end end
    local snapshot = {}
    for i, m in ipairs(bus) do snapshot[i] = m end
    for i = #bus, 1, -1 do bus[i] = nil end
    for _, m in ipairs(snapshot) do
      if not (drop and drop(m)) then
        for _, n in ipairs(NAMES) do
          if n ~= m.from and clients[n].comm[m.kind] then clients[n].comm[m.kind](copy(m.payload), m.from .. "-Royaume") end
        end
      end
    end
  end
end
local function settle() for _, n in ipairs(NAMES) do clients[n].T.tabShown = true; clients[n].G:Build() end end
run(40)
settle()

-- -------------------------------------------------------------- tout le monde se voit
local A, Bv, C, D = clients.Alpha, clients.Bravo, clients.Charlie, clients.Delta
for _, n in ipairs(NAMES) do
  local Sq = clients[n].T.Squad
  ok(#Sq:Members() == 4, n .. " voit 4 membres TreeAI (" .. #Sq:Members() .. ")")
end
ok(A.T.Squad:State("Bravo") and A.T.Squad:State("Bravo").x == 95 and A.T.Squad:State("Bravo").r == 80, "état de Bravo reçu (XP, repos)")
ok(A.T.Squad:Quests("Delta")[300] and A.T.Squad:Quests("Delta")[300].title == "Lettre à Bob", "journal de Delta reçu")
ok(A.G._test.members.Echo and not A.G._test.members.Echo.addon, "Echo : membre sans TreeAI")

-- -------------------------------------------------------------- tableau / prochain arrêt identiques chez les 4 (déterminisme)
local ref = A.T.Missions.board
ok(#ref == 4, "4 quêtes au tableau (" .. #ref .. ")")
for _, n in ipairs(NAMES) do
  local b = clients[n].T.Missions.board
  local same = #b == #ref
  for i = 1, #ref do same = same and b[i] and b[i].id == ref[i].id and b[i].pct == ref[i].pct end
  ok(same, "tableau identique chez " .. n)
end
ok(ref[1].id == 500 and ref[1].allDone and #ref[1].holders == 3, "1er : Sang de lion, tous prêts")
ok(A.T.Missions.next and A.T.Missions.next.kind == "turnin" and A.T.Missions.next.text == "Rendez ensemble « Sang de lion » (3/3 prêts)",
  "prochain arrêt : rendre ensemble — " .. tostring(A.T.Missions.next and A.T.Missions.next.text))
ok(D.T.Missions.next and D.T.Missions.next.kind == "theirs" and D.T.Missions.next.text:find("^Alpha, Bravo, Charlie peuvent rendre"),
  "Delta n'a pas la quête : les autres peuvent la rendre, jamais « Rendez ensemble » — " .. tostring(D.T.Missions.next and D.T.Missions.next.text))
local sanglier
for _, e in ipairs(ref) do if e.id == 100 then sanglier = e end end
ok(sanglier and #sanglier.holders == 5 and sanglier.unknown and sanglier.unknown[1] == "Echo" and not sanglier.allDone,
  "Echo (sans TreeAI) est sur « Les sangliers » via IsUnitOnQuest : progression inconnue, pas « tous prêts »")
local status = A.T.Missions.Engine.StatusLine(sanglier)
ok(status == "Charlie : Défense de sanglier 2/10, Delta : Défense de sanglier 5/10, toi : terminé, Bravo : terminé",
  "ligne d'état avec les noms d'objectifs de mon journal : " .. status)

-- -------------------------------------------------------------- lignes de l'onglet
local tab = A.T.tabs.group
local function tabFind(cl, pat) return find(cl.T.tabs.group, pat) end
ok(tab and #tab >= 8, "onglet Groupe rempli (" .. #(tab or {}) .. ")")
ok(tabFind(A, "Rendez ensemble « Sang de lion » (3/3 prêts)"), "ligne du prochain arrêt")
ok(tabFind(A, "Delta est 2 niveaux derrière : partage-lui « Ogres »"), "écart de niveau + quête à partager")
ok(tabFind(D, "Tu es 2 niveaux derrière : demande « Ogres » à Alpha"), "Delta : ce qu'il peut demander (pas les quêtes déjà finies)")
ok(tabFind(A, "Charlie · Mage 22 (30 %) · mana 20 %"), "carte de Charlie : classe, niveau, % de niveau, mana (lu par le jeu)")
ok(tabFind(A, "Demande « Lettre à Bob » à Delta"), "quête qu'un ami a et pas moi : une ligne")
ok(tabFind(D, "Partage « Lettre à Bob » à Alpha, Bravo et Charlie"), "une ligne par quête à partager (pas une par membre)")
ok(not tabFind(D, "Demande « Ogres »"), "déjà dite dans la ligne d'écart de niveau : pas répétée")
ok(tabFind(A, "Echo · Voleur 22"), "carte d'Echo (sans TreeAI)")
ok(tabFind(A, "Partage « Ogres » à Echo"), "Echo n'a pas Ogres (IsUnitOnQuest) ; Delta déjà dans sa ligne d'écart")
ok(not tabFind(A, "Intelligence des Arcanes"), "auras illisibles (pas d'API) : aucun « n'a pas » inventé")
local ctrl = {}
for _, l in ipairs(tab or {}) do if l.control then ctrl[l.label] = l end end
ok(ctrl["Objectif"] and ctrl["Prêt ?"], "boutons « Objectif » et « Prêt ? » dans la barre de l'onglet")
for _, l in ipairs(tab or {}) do
  ok(type(l.text) == "string" and not l.text:find("|", 1, true) and (l.prio or 1) >= 1 and (l.prio or 1) <= 3, "ligne valide : " .. tostring(l.text))
  ok(not l.text:find("nil", 1, true) and not l.text:find("?", 1, true) or l.text:find("Prêt ?", 1, true), "pas de « nil » ni de « ? » : " .. tostring(l.text))
end

-- -------------------------------------------------------------- différences seulement, débit
local function sendsOf(cl, t0) local n = 0 for _, s in ipairs(cl.T.sends) do if s.t > t0 then n = n + 1 end end return n end
local t0 = clock
run(120)
for _, n in ipairs(NAMES) do ok(sendsOf(clients[n], t0) == 0, n .. " : rien ne change → aucun message en 2 min (" .. sendsOf(clients[n], t0) .. ")") end
for _, n in ipairs(NAMES) do
  for _, s in ipairs(clients[n].T.sends) do
    ok(s.kind == "GRP" and s.channel == "PARTY", n .. " : un seul type de message (GRP), canal PARTY")
  end
end
t0 = clock
cfgs.Bravo.quests[3].obj[1].numFulfilled = 7
Bv.fire("QUEST_LOG_UPDATE")
cfgs.Bravo.xp = 9900
Bv.fire("PLAYER_XP_UPDATE")
run(12)
local deltas = {}
for _, s in ipairs(Bv.T.sends) do if s.t > t0 then deltas[#deltas + 1] = s.payload end end
ok(#deltas == 1 and deltas[1]:match("^4;D;%d+;") and deltas[1]:find("+200,22,,Ogres,m7/8", 1, true) and not deltas[1]:find("Sang de lion", 1, true),
  "changement de quête : une différence (D) avec seulement la quête changée — " .. tostring(deltas[1]))
ok(deltas[1] and #deltas[1] < 120, "différence courte (" .. #(deltas[1] or "") .. " octets)")
ok(A.T.Squad:Quests("Bravo")[200].obj[1].have == 7 and C.T.Squad:Quests("Bravo")[200].obj[1].have == 7, "différence appliquée chez les autres")
-- rafale de changements : jamais plus d'1 message / 5 s
t0 = clock
for i = 1, 30 do
  cfgs.Charlie.xp = 3000 + i * 150
  C.fire("PLAYER_XP_UPDATE")
  run(1)
end
local times = {}
for _, s in ipairs(C.T.sends) do if s.t > t0 then times[#times + 1] = s.t end end
local okRate = true
for i = 2, #times do if times[i] - times[i - 1] < 5 then okRate = false end end
ok(okRate and #times >= 3 and #times <= 7, "XP qui bouge sans arrêt : ≤ 1 message / 5 s (" .. #times .. " en 30 s)")

-- -------------------------------------------------------------- message perdu → trou détecté → état complet redemandé
t0 = clock
local dropped = false
drop = function(m) if m.from == "Delta" and not dropped and m.payload:match("^4;D;") then dropped = true return true end end
cfgs.Delta.quests[2].obj[1].numFulfilled = 7
D.fire("QUEST_LOG_UPDATE")
run(8)
drop = nil
cfgs.Delta.quests[2].obj[1].numFulfilled = 8
D.fire("QUEST_LOG_UPDATE")
run(40)
local asked = false
for _, s in ipairs(A.T.sends) do if s.t > t0 and s.payload:match("^4;R;") then asked = true end end
ok(dropped and asked, "trou dans la suite : état complet redemandé (R)")
ok(A.T.Squad:Quests("Delta")[100].obj[1].have == 8, "puis l'état de Delta est juste (" .. tostring(A.T.Squad:Quests("Delta")[100].obj[1].have) .. "/10)")

settle()
ok(tabFind(A, "XP de la soirée : Charlie +"), "course à l'XP de la soirée (amis TreeAI) : " .. tostring((tabFind(A, "XP de la soirée") or {}).text))

-- -------------------------------------------------------------- objectif du groupe (partagé)
A.G:SetGoal("level", 23)
run(10)
settle()
ok(Bv.G.char.goal and Bv.G.char.goal.k == "level" and Bv.G.char.goal.n == 23 and Bv.G.char.goal.by == "Alpha", "objectif reçu chez Bravo")
ok(find(Bv.T.toasts, "Alpha propose l'objectif : niveau 23 ensemble"), "annonce de l'objectif")
ok(tabFind(Bv, "Objectif niveau 23 ensemble : 0/5 — "), "ligne d'objectif : " .. tostring((tabFind(Bv, "Objectif") or {}).text))
ok(tabFind(Bv, "toi 22 (99 %)"), "progression avec le % de niveau (amis TreeAI)")
-- zone : chacun compte ses quêtes de la zone et l'envoie
A.G:SetGoal("zone", "Marche de l'Ouest")
run(20)
settle()
local zl = tabFind(C, "Objectif : finir « Marche de l'Ouest »")
ok(zl and zl.text:find("restantes", 1, true) and zl.text:find("à rendre", 1, true), "objectif de zone : quêtes restantes et à rendre — " .. tostring(zl and zl.text))
ok(zl and zl.tooltip and table.concat(zl.tooltip, " "):find("Echo", 1, true), "Echo (sans TreeAI) : inconnu, dit dans l'infobulle")
-- choix à la souris
A.G:Picker(true)
local pick = find(A.T.tabs.group, "Niveau 23 ensemble")
ok(pick and pick.onClick and find(A.T.tabs.group, "Finir « Marche de l'Ouest »") and find(A.T.tabs.group, "Pas d'objectif"), "choix de l'objectif en lignes cliquables")
pick.onClick()
ok(A.G.char.goal.k == "level" and A.G.char.goal.n == 23, "clic = objectif posé")
A.G:SetGoal("off")
run(10)
ok(not Bv.G.char.goal, "objectif retiré partout")

-- -------------------------------------------------------------- « Prêt ? »
settle()
A.G:ReadyAsk()
run(4)
local rq = Bv.T.sugg["Group:ready"]
ok(rq and rq.opts and rq.opts.onClick and rq.text:find("Alpha demande : prêt ?", 1, true), "demande « prêt ? » reçue, cliquable")
rq.opts.onClick()
local rqC = C.T.sugg["Group:ready"]
ok(rqC and rqC.text:find("(toi : mana 20 %)", 1, true), "Charlie voit sa raison : " .. tostring(rqC and rqC.text))
D.G:ReadyAnswer(false, "boisson")
run(4)
A.G:Build()
local rl = tabFind(A, "Prêt ? ")
ok(rl and rl.text:find("Bravo oui", 1, true) and rl.text:find("Delta non (boisson)", 1, true) and rl.text:find("Charlie ?", 1, true),
  "ligne d'états chez le demandeur : " .. tostring(rl and rl.text))
run(34)
ok(find(A.T.toasts, "Pas prêts : Charlie (pas de réponse), Delta (boisson)"), "résultat après le délai")

-- -------------------------------------------------------------- point de rencontre
A.env.C_Map.GetPlayerMapPosition = function() return { x = 0.42, y = 0.61 } end
local okM = A.G:SendMeet()
local okM2, why2 = A.G:SendMeet()
run(4)
ok(okM and not okM2 and why2 == "gap", "point de rencontre : 1 envoi, le 2e bridé")
local ml = C.T.sugg["Group:meet"]
ok(ml and ml.text:find("Rejoins Alpha au point de rencontre (Moulin)", 1, true) and ml.opts.onClick, "rendez-vous reçu : " .. tostring(ml and ml.text))
local tg = C.T.compass[#C.T.compass]
ok(tg and tg.source == "meet" and math.abs(tg.x - 0.42) < 0.001, "la boussole vise le rendez-vous")

-- -------------------------------------------------------------- « attends » au moment de rendre une quête qui ouvre une suite
cfgs.Alpha.turnIn, cfgs.Alpha.chain = 100, { [100] = "Suite" }
A.fire("QUEST_COMPLETE")
local w = A.T.sugg["Group:turnin"]
ok(w and w.text == "Attends Charlie (2/10) et Delta (8/10) : « Les sangliers » ouvre une suite, rendez ensemble", "attends : " .. tostring(w and w.text))
cfgs.Alpha.chain = nil
A.T.sugg["Group:turnin"] = nil
A.fire("QUEST_COMPLETE")
ok(not A.T.sugg["Group:turnin"], "sans suite : rien (rendre seul ne gêne personne)")

-- -------------------------------------------------------------- « Avant de tirer »
cfgs.Alpha.party.Echo.afk = true
cfgs.Alpha.target = {}
A.G:CheckPull()
local pl = A.G._test.pull()
ok(pl == "Avant de tirer, attends : Charlie 20 % mana et Echo AFK", "avant de tirer : mana du mage + AFK — " .. tostring(pl))
local pinned = A.T.tabs.group[1]
ok(pinned and pinned.pin and pinned.prio == 2 and pinned.text == pl, "ligne épinglée en tête (« Maintenant »)")
cfgs.Alpha.combat = true
A.fire("PLAYER_REGEN_DISABLED")
ok(A.T.tabs.group[1].text ~= pl, "en combat : la ligne disparaît")
cfgs.Alpha.combat, cfgs.Alpha.target, cfgs.Alpha.party.Echo.afk = false, nil, nil

-- -------------------------------------------------------------- façades (anciens noms)
local Sq, Mi, Tm = A.T.Squad, A.T.Missions, A.T.Team
ok(A.T.modules.Squad == Sq and A.T.modules.Missions == Mi and A.T.modules.Team == Tm, "T.modules.Squad / Missions / Team = façades")
ok(Sq:IsActive() and Sq:IsEnabled() and Mi:IsEnabled() and Tm:IsEnabled(), "façades actives")
local ml2 = Sq:MemberList()
ok(#ml2 == 4 and ml2[1].name == "Alpha" and ml2[2].st and ml2[2].st.c == "HUNTER" and ml2[2].quests[200], "Squad:MemberList()")
ok(Sq:State("Bravo").du == 90 and Sq:State("Bravo").b == 10 and Sq:State("Bravo").am == 1500, "état logistique (Dungeon : durabilité, sacs, munitions)")
ok(Sq:Common()[1].id and #Sq:Common()[1].holders >= 3, "Squad:Common()")
ok(Sq.Engine.Progress({ obj = { { have = 3, need = 8 } } }) == 3, "Squad.Engine.Progress")
ok(Sq.char == A.G.char and A.G.char.leaveAt == 0, "Squad.char (Export / Planner : leaveAt)")
ok(Sq:IsMember("Bravo-Royaume") and not Sq:IsMember("Intrus-Royaume"), "Squad:IsMember")
ok(Tm.GearOf("Player-0-Bravo") == nil, "Team.GearOf : rien sans inspection")
ok(Mi.Engine.Next({ { id = 9, title = "Fantôme", multi = true, allDone = true, holders = { "Bravo", "Charlie" }, done = { "Bravo", "Charlie" } } }).kind == "theirs",
  "Missions.Engine.Next : quête des potes absente de mon journal = « peuvent rendre »")
A.T.Squad:SetLeave(20)
A.G:CollectMine()
ok(A.G._test.mine.lm == 20, "/tai partir 20 → champ lm")
run(8)
ok(Bv.T.Squad:State("Alpha").lm == 20 or Bv.T.Squad:State("Alpha").lm == 19, "le départ d'Alpha est connu de Bravo")
settle()
ok(tabFind(Bv, "part dans 20 min") or tabFind(Bv, "part dans 19 min"), "carte d'Alpha : départ annoncé")
A.T.Squad:SetLeave(0)

-- -------------------------------------------------------------- 3.9.x : Squad v2 décodé (une version)
local legacyState = { v = 2, c = "HUNTER", l = 22, x = 40, r = 5, z = "Marche de l'Ouest", b = 3, bt = 64, du = 77, am = 900, pr = "Couture 90" }
local Ex = clients.Alpha
Ex.comm.SQS(copy(legacyState), "Echo-Royaume")
Ex.comm.SQQ("2:700,22,,Les murlocs,m2/6;100,22,C,Les sangliers,i10/10", "Echo-Royaume")
local e = Ex.G._test.members.Echo
ok(e.addon == "3" and e.st.du == 77 and e.st.pr == "Couture 90" and e.quests[700] and e.quests[700].obj[1].have == 2,
  "ami en 3.9 : état et journal Squad v2 décodés")
local entries = Ex.G.Legacy.Entries("700,22,,Les murlocs,m2/6;100,22,C,Les sangliers,i10/10")
local new = Ex.G.Legacy.ApplyDiff(entries, "+700,22,,Les murlocs,m3/6")
Ex.comm.SQD(("2:%d:%d:+700,22,,Les murlocs,m3/6"):format(Ex.G.Legacy.Hash(entries), Ex.G.Legacy.Hash(new)), "Echo-Royaume")
ok(e.quests[700].obj[1].have == 3, "différence Squad v2 (SQD) appliquée")
Ex.comm.SQD("2:1:2:+999,1,,X,", "Echo-Royaume")
ok(e.quests[999] == nil and e.quests[700], "SQD sur une base inconnue : ignoré")
Ex.G:Build()
local et = find(Ex.T.tabs.group, "Echo · Voleur 22")
ok(et and table.concat(et.tooltip or {}, " "):find("3.9", 1, true), "carte : « TreeAI 3.9 : demande-lui de mettre à jour »")

-- -------------------------------------------------------------- sécurité : intrus, versions, déchets, inondation, bornes
local G = C.G
C.comm.GRP("4;F;1;x=50;+1,1,,Intrus,m1/2", "Intrus-Royaume")
ok(not G._test.members.Intrus, "hors groupe : ignoré")
C.comm.GRP("5;F;1;x=50", "Bravo-Royaume")
ok(C.T.Squad:State("Bravo").x ~= 50, "version inconnue ignorée")
for _, junk in ipairs({ "", "4", "4;Z;1", "4;D;abc;x=1", string.rep("x", 7000), "4;E;", "4;F;9;" .. string.rep("+a,b;", 100) }) do
  ok(pcall(C.comm.GRP, junk, "Bravo-Royaume"), "déchet sans erreur : " .. junk:sub(1, 20))
end
run(20)
C.comm.GRP("4;F;99;x=999;du=-50;z=" .. string.rep("é", 60) .. "|cffff0000;b=abc;zz=1", "Bravo-Royaume")
local bst = C.T.Squad:State("Bravo")
ok(bst.x == 100 and bst.du == -1 and #bst.z <= 30 and not bst.z:find("|", 1, true) and bst.b == 0, "bornes / nettoyage")
local accepted = 0
for i = 1, 40 do
  C.comm.GRP(("4;D;%d;x=%d"):format(99 + i, i), "Delta-Royaume")
  if C.T.Squad:State("Delta").x == i then accepted = accepted + 1 end
end
ok(accepted < 15, "inondation limitée (" .. accepted .. " acceptés sur 40)")

-- -------------------------------------------------------------- moteur pur
local E = A.G.Engine
local list = {
  { name = "A", me = true, level = 30, online = true, st = { x = 10, xs = 9000 }, quests = {} },
  { name = "B", addon = "4", level = 27, online = true, st = { x = 50, xs = 12000, r = 30 }, quests = {} },
  { name = "C", level = 30, online = true, st = {}, quests = {} },
}
local gaps = E.LevelGaps(list, {}, 30, nil)
ok(#gaps == 0, "(4.0 QA S21) écart de 3 sans quête à partager : rien à faire, pas de ligne — " .. tostring(gaps[1] and gaps[1].text))
list[2].level = 26
gaps = E.LevelGaps(list, {}, 30, nil)
ok(#gaps == 1 and gaps[1].text == "B est 4 niveaux derrière (en repos : il rattrape)", "vrai écart (4) sans quête à partager : repos dit — " .. tostring(gaps[1] and gaps[1].text))
list[2].level = 27
local lb = E.Leaderboard(list)
ok(#lb == 2 and lb[1].name == "B" and lb[2].name == "A", "course à l'XP : amis TreeAI seulement, triée")
local gl = E.GoalLine({ k = "level", n = 30 }, list)
ok(gl.text == "Objectif niveau 30 ensemble : 2/3 — B 27 (50 %)", "objectif niveau : " .. gl.text)
gl = E.GoalLine({ k = "level", n = 27 }, list)
ok(gl.done and gl.text == "Objectif atteint : niveau 27 pour tout le monde !", "objectif atteint")
local blk = E.PullBlockers({ { name = "X", online = false }, { name = "Y", dead = true, ghost = true }, { name = "Z", afk = true },
  { name = "W", far = true }, { name = "P", class = "PRIEST", mana = 20 }, { name = "H", class = "HUNTER", mana = 10, hp = 40 },
  { name = "K", class = "HUNTER", petMissing = true }, { name = "R", addon = "4", st = { rd = 0 } }, { name = "OK", class = "MAGE", mana = 90 } })
ok(table.concat(blk, ", ") == "X hors ligne, Y fantôme, Z AFK, W loin, P 20 % mana, H 40 % PV, K sans familier, R pas prêt",
  "raisons avant un pull : " .. table.concat(blk, ", "))
local rg = E.Regroup({ { name = "A", me = true, zoneKey = "Z1", zoneName = "Z1" }, { name = "B", zoneKey = "Z1" },
  { name = "C", zoneKey = "Z2", zoneName = "Les Paluns" } })
ok(rg and rg.text == "Regroupez-vous : C (Les Paluns) est dans une autre zone", "regroupement : " .. tostring(rg and rg.text))
rg = E.Regroup({ { name = "A", me = true, zoneKey = "Z2", zoneName = "Z2" }, { name = "B", zoneKey = "Z1", zoneName = "Moulin" },
  { name = "C", zoneKey = "Z1", zoneName = "Moulin" } })
ok(rg and rg.prio == 2 and rg.text == "Rejoins le groupe : ils sont dans « Moulin »", "c'est moi qui suis loin")
local f, p = E.Flags({ class = "HUNTER", level = 25, addon = "4", st = { du = 8, b = 0, bt = 64, am = 80 },
  gear = { weapon = { quality = 0 }, missing = { 5 }, ilvl = 12 } })
ok(table.concat(f, " · ") == "arme grise · rien en torse · équipement niv. 12 · durabilité 8 % · sacs pleins · 80 munitions" and p == 2,
  "signaux d'alerte : " .. table.concat(f, " · "))
local loot = { { name = "Alpha", class = "WARRIOR", level = 22 }, { name = "Bravo", class = "HUNTER", level = 22 },
  { name = "Charlie", class = "MAGE", level = 22, me = true } }
ok(E.LootHint({ name = "Arc long", classID = 2, subClassID = 2 }, loot) == "Jet « Arc long » : Besoin Bravo ; les autres Cupidité", "jet arc → chasseur")
ok(E.LootHint({ name = "Robe", classID = 4, subClassID = 1 }, loot):find("Besoin toi (tissu)", 1, true), "jet tissu → toi (mage)")
ok(E.LootHint({ name = "Potion", classID = 0, subClassID = 1 }, loot) == nil, "pas de conseil hors équipement")
-- codec
local Wr = A.G.Wire
local enc = Wr.EncodeEntry({ id = 5, level = 10, title = "A,b;c|d=e", done = true, group = true, obj = { { t = "item", have = 12, need = 10 } } })
local dec = Wr.DecodeEntry(enc)
ok(dec and dec.done and dec.group and dec.obj[1].have == 10 and dec.obj[1].t == "i" and not dec.title:find("[,;|=]"), "codec quêtes : " .. enc)
local msg = Wr.Parse(Wr.Build("D", 7, { x = "40", z = "Les Paluns" }, { enc }, { 9 }))
ok(msg and msg.kind == "D" and msg.seq == 7 and msg.fields.x == "40" and msg.fields.z == "Les Paluns" and msg.add[1].id == 5 and msg.del[1] == 9, "fil : construire / lire")
local st = Wr.DecodeStep(Wr.EncodeStep("obj", 200, 1, "Tue « Ogre », vite"))
ok(st and st.k == "obj" and st.q == 200 and st.o == 1 and st.l == "Tue « Ogre », vite", "étape en cours : codec")
ok(A.G.Significant("am", 1500, 1480) == false and A.G.Significant("am", 210, 190) == true and A.G.Significant("du", 30, 24) == true
  and A.G.Significant("xs", 1000, 1050) == false, "seuils : on n'envoie que ce qui compte (valeur exacte)")

-- -------------------------------------------------------------- 4.0.1 GROUPJOIN : moteur pur
local JE = A.G.Join and A.G.Join.Engine
ok(JE, "GroupJoin chargé dans le module Groupe (M.Join)")
local function P(n, w) return { c = 1, n = n, w = w } end
local walk = function(a, b) local d = JE.Dist(a, b) return d and d / 7 end   -- 7 yd/s, sans détour (calcul simple)
-- tout droit 700 yd = 100 s ; un rendu à 30 yd du chemin (15 s) passe, un objectif à 300 yd non (≤ 20 %)
local from, to = P(0, 0), P(700, 0)
local legs = { { mode = "walk", sec = 100, to = to } }
local cands = { { place = P(350, 30), svc = 15, xp = 1450, value = 1450, kind = "turn", label = "rendre « A »" },
  { place = P(350, 300), svc = 15, xp = 900, value = 900, kind = "turn", label = "rendre « B »" } }
local d = JE.Detour(from, legs, 100, cands, walk)
ok(d and #d.stops == 1 and d.stops[1].cand.label == "rendre « A »" and d.turnins == 1 and d.xp == 1450 and not d.approx,
  "rentable : le rendu sur le chemin, pas celui à 300 yd")
ok(d and math.abs(d.extra - (2 * math.sqrt(350 ^ 2 + 30 ^ 2) / 7 - 100 + 15)) < 0.01 and d.extra <= 30 + 1e-9 and d.total == 100 + d.extra,
  "rentable : temps ajouté exact (détour + 15 s au PNJ), ≤ 20 % (au moins 30 s) : " .. tostring(d and d.extra))
ok(JE.Extra(d.extra) == "+15 s" and JE.Extra(185) == "+3 min" and JE.Min(30) == "moins d'1 min" and JE.Min(582) == "10 min",
  "textes : +15 s, +3 min, moins d'1 min, 10 min : " .. JE.Extra(d.extra))
-- un vol ne s'arrête pas en route ; avant une pierre de foyer on peut s'arrêter
local fl = { { mode = "walk", sec = 10, to = P(70, 0) }, { mode = "fly", sec = 300, to = P(9000, 0) }, { mode = "walk", sec = 10, to = P(9070, 0) } }
ok(JE.Detour(P(0, 0), fl, 320, { { place = P(4500, 10), svc = 15, value = 1, kind = "turn", label = "x" } }, walk) == nil,
  "rentable : rien au milieu d'un vol")
local hs = { { mode = "hs", sec = 15, to = P(5000, 0) }, { mode = "walk", sec = 100, to = P(5700, 0) } }
local dh = JE.Detour(P(0, 0), hs, 115, { { place = P(20, 0), svc = 15, xp = 300, value = 300, kind = "turn", label = "avant" } }, walk)
ok(dh and dh.stops[1].k == 1 and math.abs(dh.extra - (20 / 7 + 15)) < 0.01, "rentable : un rendu juste avant la pierre de foyer")
ok(JE.MainMode(fl) == "fly" and JE.MainMode(hs) == "hs" and JE.MainMode(legs) == "walk", "mode principal du trajet")
local ll = JE.LegLines(fl, "Bravo")
ok(ll[1]:find("maître de vol", 1, true) and ll[2]:find("^Vol") and ll[3] == "À pied jusqu'à Bravo (moins d'1 min)", "étapes en clair : " .. table.concat(ll, " | "))
ok(JE.Jusqua("le groupe (Bravo)") == "jusqu'au groupe (Bravo)" and JE.Jusqua("l'entrée de X") == "jusqu'à l'entrée de X", "« jusqu'au » / « jusqu'à »")
-- point de rencontre : minimax ; le prochain arrêt commun l'emporte s'il coûte ≤ 25 % de plus au plus loin
local pos = { { name = "toi", place = P(0, 0) }, { name = "Bravo", place = P(2000, 0) } }
local spots = { { place = P(1000, 0), kind = "fm", label = "Milieu" }, { place = P(200, 0), kind = "fm", label = "Chez toi" },
  { place = P(1150, 0), kind = "hub", useful = true, label = "rendre « C »" } }
local best, far, farName = JE.Meeting(pos, spots)
ok(best and best.label == "rendre « C »" and math.floor(far) == 1150 and farName == "toi", "rencontre : l'arrêt commun (1 150 ≤ 1,25 × 1 000)")
spots[3].place = P(1400, 0)
best = JE.Meeting(pos, spots)
ok(best and best.label == "Milieu", "rencontre : sinon le milieu (minimax)")
-- vote
local v = { choices = { { label = "A" }, { label = "B" }, { label = "C" } }, ballots = { Alpha = 2, Bravo = 2, Charlie = 1 } }
local n, win, tie, waiting = JE.Tally(v, { "Alpha", "Bravo", "Charlie", "Delta" })
ok(win == 2 and n[2] == 2 and not tie and #waiting == 1 and waiting[1] == "Delta", "vote : B gagne, Delta n'a pas voté")
v.ballots.Delta = 1
_, win, tie = JE.Tally(v, { "Alpha", "Bravo", "Charlie", "Delta" })
ok(win == nil or tie, "vote : égalité 2–2")
-- « se rapproche »
ok(JE.Approach({ { t = 0, d = 900 }, { t = 15, d = 820 }, { t = 30, d = 700 } }) ~= nil, "se rapproche : 200 yd en 30 s")
ok(JE.Approach({ { t = 0, d = 900 }, { t = 15, d = 950 }, { t = 30, d = 700 } }) == nil, "pas toujours plus près : non")
ok(JE.Approach({ { t = 0, d = 900 }, { t = 15, d = 880 }, { t = 30, d = 860 } }) == nil, "trop lent / trop peu : non")
-- chat : mots-clés FR / EN, noms de lieux (sans accents ni ponctuation), abréviations
ok(JE.Norm("Kraal de Tranchebauge, ÉTÉ !") == " kraal de tranchebauge ete ", "normalisation : " .. JE.Norm("Kraal de Tranchebauge, ÉTÉ !"))
local IDX = { { pat = " kraal de tranchebauge ", kind = "dg", key = "RFK", name = "Kraal de Tranchebauge", q = 1 },
  { pat = " tarides ", kind = "z", key = 1413, name = "Les Tarides", q = 1 },
  { pat = " camp taurajo ", kind = "fm", key = 77, name = "Camp Taurajo", q = 0.9 },
  { pat = " tranchebauge ", kind = "dg", key = "RFK", name = "Kraal de Tranchebauge", q = 0.9 },
  { pat = " kraal ", kind = "dg", key = "RFK", name = "Kraal de Tranchebauge", q = 0.8 },
  { pat = " rfk ", kind = "dg", key = "RFK", name = "Kraal de Tranchebauge", q = 0.8 } }
local function kinds(list) local o = {} for _, x in ipairs(list) do o[#o + 1] = x.kind .. (x.key and (":" .. tostring(x.key)) or "") end table.sort(o) return table.concat(o, ",") end
local s1 = JE.ParseChat("On va au Kraal de Tranchebauge ?", IDX)
ok(kinds(s1) == "dg:RFK" and s1[1].w == 1.5, "« on va au Kraal de Tranchebauge » : donjon, poids fort (verbe), une fois : " .. kinds(s1))
ok(kinds(JE.ParseChat("go rfk", IDX)) == "dg:RFK", "« go rfk » : abréviation")
ok(kinds(JE.ParseChat("rdv à Camp Taurajo", IDX)) == "fm:77", "« rdv à Camp Taurajo » : maître de vol")
ok(kinds(JE.ParseChat("attendez 2 min", IDX)) == "wait", "« attendez 2 min » : pause")
ok(kinds(JE.ParseChat("je rends et on finit les Tarides", IDX)) == "finish:1413,turn", "« je rends et on finit les Tarides »")
ok(kinds(JE.ParseChat("on fait un donjon ce soir", IDX)) == "dgany", "« on fait un donjon » : donjon sans nom (→ vote)")
ok(kinds(JE.ParseChat("lol", IDX)) == "", "rien d'intentionnel : rien")
-- intention : déclin, hystérésis, confiance (avance sur la 2e lecture)
local ev = { { key = "dg:RFK", w = 1.5, at = 0, tau = 300, what = "chat" }, { key = "dg:RFK", w = 1.2, at = 0, tau = 300, what = "chat" },
  { key = "z:1441", w = 0.5, what = "here" } }
local it = JE.Intent(ev, 0)
ok(it and it.key == "dg:RFK" and it.conf > 0.75, "intention : 2 messages pour le Kraal > zone actuelle (conf " .. string.format("%.2f", it and it.conf or 0) .. ")")
it = JE.Intent(ev, 1200)
ok(it and it.key == "z:1441", "20 min plus tard : le chat s'est éteint, la zone reprend")
ok(JE.Intent({ { key = "a", w = 1.0 }, { key = "b", w = 1.1 } }, 0, "a").key == "a", "hystérésis : il faut 25 % de plus pour changer")
ok(JE.Intent({ { key = "a", w = 1.0 }, { key = "b", w = 1.3 } }, 0, "a").key == "b", "… et au-delà, ça change")

-- -------------------------------------------------------------- 4.0.1 GROUPJOIN : 4 clients, positions vraies
POS.Alpha, POS.Bravo, POS.Charlie = { 1441, 0.455, 0.50 }, { 1413, 0.449, 0.585 }, { 1413, 0.425, 0.890 }
POS.Delta, POS.Echo = { 1441, 0.70, 0.70 }, { 1441, 0.46, 0.505 }
for _, nm in ipairs(NAMES) do clients[nm].fire("ZONE_CHANGED_NEW_AREA") end
run(30)
settle()
ok(Bv.T.Squad:State("Alpha").ps == "455.500" and Bv.T.Squad:State("Alpha").m == 1441, "position partagée par le fil (ps = ‰ de la carte m) : "
  .. tostring(Bv.T.Squad:State("Alpha").ps))
local function line(cl, pat) return find(cl.T.tabs.group, pat) end
local rj = line(A, "Rejoindre Bravo (Les Tarides) : au plus vite")
ok(rj and rj.right == "à pied · 10 min" and rj.onClick, "rejoindre Bravo : à pied 10 min (3,1 km, 7 yd/s, détour 1,3) : " .. tostring(rj and rj.right))
ok(line(A, "Rejoindre Delta : au plus vite"), "rejoindre Delta (sans TreeAI) : position lue par le jeu")
ok(not line(A, "Rejoindre Echo"), "Echo à côté (≤ 120 yd) : rien à rejoindre")
ok(line(A, "Delta · Voleur 20 (90 %) · à 1,1 km"), "carte de Delta : distance vraie (pas « à côté » sur la foi de UnitInRange)")
-- rentable : le rendu prêt de « Sang de lion » près de Camp Taurajo (XP du jeu)
A.T.Compass.targets = { { kind = "turnin", questID = 500, title = "Sang de lion", xp = 1450, mapID = 1413, x = 0.452, y = 0.570 } }
A.G.Join._state.cache = {}
settle()
local rt = line(A, "Rentable : ")
ok(rt and rt.text == "Rentable : +50 s · 1 rendu · +1450 XP" and rt.right == "11 min" and rt.onClick, "rentable : " .. tostring(rt and rt.text) .. " / " .. tostring(rt and rt.right))
-- clic « Rentable » : la boussole passe d'abord par le PNJ, le groupe sait que j'arrive
A.T.modules.Quests.lastList = { { id = 500, complete = true }, { id = 100, complete = true }, { id = 200, complete = false } }
rt.onClick()
local tgA = A.T.compass[#A.T.compass]
ok(tgA and tgA.source == "join" and tostring(tgA.label):find("Sang de lion", 1, true) and math.abs(tgA.x - 0.452) < 0.001,
  "rentable : la boussole vise d'abord la remise : " .. tostring(tgA and tgA.label))
ok(A.T.sugg["Group:join"] and A.T.sugg["Group:join"].text:find("^Rejoindre Bravo") and A.T.sugg["Group:join"].opts.tab == "now",
  "« Maintenant » : la ligne du trajet en cours (une seule ligne du groupe) : " .. tostring(A.T.sugg["Group:join"] and A.T.sugg["Group:join"].text))
run(8)
settle()
local arr = line(Bv, "Alpha arrive vers toi : ≈ ")
ok(arr and arr.text:find("— attends-le", 1, true), "Bravo voit qu'Alpha arrive (champ j / je du fil) : " .. tostring(arr and arr.text))
ok(line(Bv, "Alpha · Guerrier 22") and line(Bv, "vient vers toi"), "carte d'Alpha chez Bravo : « vient vers toi »")
-- la quête rendue → la boussole vise Bravo ; arrivé à côté → « Tu as rejoint Bravo », plus de « j » sur le fil
A.T.modules.Quests.lastList = { { id = 100, complete = true }, { id = 200, complete = false } }
A.tick()
local tgB = A.T.compass[#A.T.compass]
ok(tgB and tgB.label == "Rejoindre Bravo (Les Tarides)", "rendu fait : la boussole vise Bravo : " .. tostring(tgB and tgB.label))
POS.Alpha = { 1413, 0.4492, 0.5852 }
A.tick()
ok(A.T.sugg["Group:joined"] and A.T.sugg["Group:joined"].text == "Tu as rejoint Bravo" and not A.T.sugg["Group:join"], "arrivée : « Tu as rejoint Bravo »")
run(12)
settle()
ok(not line(Bv, "Alpha arrive vers toi"), "plus de « arrive vers toi » chez Bravo")
POS.Alpha = { 1441, 0.455, 0.50 }
run(25)
-- rendez-vous : Bravo propose Camp Taurajo ; Alpha « J'y vais » d'un clic ; Bravo voit qui vient (Echo : sans TreeAI)
local BJ = Bv.G.Join
local tx, ty = 44.5, 59.1
ok(BJ.Propose({ place = BJ.WorldPct(1413, tx, ty), label = "Camp Taurajo", kind = "fm" }), "Bravo propose un point de rencontre")
run(4)
local rv = A.T.sugg["Group:rdv"]
ok(rv and rv.text:find("^Bravo propose un rendez%-vous : Camp Taurajo · toi ") and rv.opts.onClick and rv.opts.sound, "proposition reçue, « J'y vais » d'un clic : " .. tostring(rv and rv.text))
rv.opts.onClick()
local tgR = A.T.compass[#A.T.compass]
ok(tgR and tgR.source == "join" and math.abs(tgR.x - tx / 100) < 0.001, "j'y vais : la boussole vise le point")
run(6)
settle()
local rl = line(Bv, "Rendez-vous Camp Taurajo : ")
local rlTip = rl and table.concat(rl.tooltip, " | ") or ""
ok(rl and rl.text:find(": Alpha (≈ 10 min) y va · 3 sans réponse", 1, true) and rlTip:find("Charlie : pas encore répondu", 1, true)
  and rlTip:find("Echo : sans TreeAI", 1, true) and #rl.text <= 96, "Bravo voit qui vient (ligne courte, détails dans l'infobulle) : "
  .. tostring(rl and rl.text) .. " / " .. rlTip)
A.G.Join.Stop()
for _, nm in ipairs(NAMES) do clients[nm].G.Join._state.rdv = nil end   -- rendez-vous fini (isole la suite)
-- « Ce que veut le groupe » : où va chacun ; Bravo, Charlie et Delta suivent une étape en Orneval → « Suivre le groupe »
for _, nm in ipairs({ "Bravo", "Charlie", "Delta" }) do
  clients[nm].T.Planner.steps = { { k = "obj", q = 200, o = 1, map = 1440, x = 50, y = 50, label = "Tue « Ogre »" } }
  clients[nm].G._state.lastStep = -1e9
end
run(20)
settle()
local cf = line(A, " vont vers « Orneval », toi et Echo dans « Mille pointes »")
ok(cf and cf.prio == 2 and cf.onClick and cf.text:find("^Bravo, Charlie et Delta"), "conflit : « Bravo, Charlie et Delta vont vers « Orneval », toi et Echo dans « Mille pointes » » : "
  .. tostring((line(A, "vont vers") or {}).text))
local follow
for _, l in ipairs(A.T.tabs.group) do if l.control and l.label == "Suivre le groupe" then follow = l end end
ok(follow and follow.onClick, "bouton « Suivre le groupe »")
local it2 = line(A, "Le groupe ")
ok(it2 and it2.text:find("Orneval", 1, true), "intention déduite (étapes TreeAI : 3/5 y vont) : " .. tostring(it2 and it2.text))
follow.onClick()
local zc = A.T.ZonePlan.calls
ok(#zc >= 2 and zc[#zc - 1][1] == "auto" and zc[#zc][1] == "zone" and zc[#zc][2] == 1440, "suivre : la zone de travail du trajet passe en Orneval")
-- « Partager mon trajet » : Alpha partage, Bravo peut le suivre d'un clic
A.T.Planner.steps = { { k = "turn", q = 100, map = 1441, x = 46, y = 51, label = "Rendre « Les sangliers »" } }
A.G._state.lastStep = -1e9
A.G.Join._state.lastShare = -1e9
ok(A.G.Join.ShareRoute(), "partager mon trajet")
run(6)
local pl = Bv.T.sugg["Group:plan:Alpha"]
local pline = find(Bv.T.tabs.group, "Trajet de Alpha")
ok((pl or pline), "Bravo reçoit le trajet d'Alpha")
Bv.G:Build()
pline = line(Bv, "Trajet de Alpha : « Mille pointes »")
ok(pline and pline.onClick and pline.text:find("Rendre « Les sangliers »", 1, true), "ligne « Trajet de Alpha » : " .. tostring(pline and pline.text))
pline.onClick()
local bz = Bv.T.ZonePlan.calls
ok(bz[#bz] and bz[#bz][1] == "zone" and bz[#bz][2] == 1441, "Bravo suit le trajet d'Alpha (zone Mille pointes)")
-- vote léger : Alpha lance, chacun clique, même résultat partout (Echo, sans TreeAI, ne vote pas)
for _, nm in ipairs(NAMES) do clients[nm].G.Join._state.lastVote = -1e9 end
ok(A.G.Join.StartVote(), "vote lancé")
run(10)
local vb = Bv.T.sugg["Group:vote"]
ok(vb and vb.text == "Alpha lance un vote : la suite du groupe — clic : voter", "vote reçu : " .. tostring(vb and vb.text))
local function choiceOf(cl, label) for i, c in ipairs(cl.G.Join._state.vote.choices) do if c.label == label then return i end end end
local wc = choiceOf(A, "Cavernes des Lamentations")
ok(wc and choiceOf(Bv, "Cavernes des Lamentations") == wc and not choiceOf(A, "Kraal de Tranchebauge"), "donjons à portée de tous (20–22) : les Lamentations, pas le Kraal")
Bv.G.Join.Ballot(wc); C.G.Join.Ballot(wc); D.G.Join.Ballot(1)
run(6)
A.G.Join.Ballot(wc)
run(6)
for _, nm in ipairs(NAMES) do
  local vv = clients[nm].G.Join._state.vote
  ok(vv and vv.closed and vv.result == wc and vv.count == 3, nm .. " : vote clos, les Lamentations gagnent (3 voix)")
end
settle()
local vr = line(C, "Vote : Cavernes des Lamentations gagne")
ok(vr and vr.onClick and vr.text:find("Les Lamentations", 1, true) == nil, "résultat affiché : " .. tostring(vr and vr.text))
vr.onClick()
ok(C.T.Dungeon.planned == "WC", "suivre le vote : donjon prévu (Cavernes des Lamentations)")
-- intention déduite du chat de groupe (lu, jamais écrit ; en mémoire seulement) : tous les clients le lisent
for _, nm in ipairs(NAMES) do
  clients[nm].fire("CHAT_MSG_PARTY", "on va au kraal après ?", "Bravo-Royaume")
  clients[nm].fire("CHAT_MSG_PARTY_LEADER", "go rfk, rdv à l'entrée", "Alpha-Royaume")
end
settle()
for _, nm in ipairs(NAMES) do
  local il = line(clients[nm], "Kraal de Tranchebauge")
  ok(il and il.text:find("^Le groupe ") and table.concat(il.tooltip or {}, " "):find("confiance", 1, true), nm .. " : « " .. tostring(il and il.text) .. " »")
end
local il = line(Bv, "Le groupe semble aller vers : Kraal de Tranchebauge (1/5 à l'entrée)")
local tipB = il and table.concat(il.tooltip, " | ") or ""
ok(il and tipB:find("Chat de Alpha", 1, true) and tipB:find("Chat de Bravo", 1, true) == nil and tipB:find("Ton message", 1, true)
  and tipB:find("confiance moyenne", 1, true), "preuves et confiance dans l'infobulle (pas sûr = « semble ») : " .. tipB)
local ic = line(C, "Kraal de Tranchebauge")
ok(ic and table.concat(ic.tooltip, " "):find("Tu y es déjà", 1, true) and not ic.onClick, "Charlie, déjà à l'entrée : « Tu y es déjà », rien à suivre")
local seenText = false
local function scan(t, depth)
  if depth > 6 or type(t) ~= "table" then return end
  for k, x in pairs(t) do
    if type(x) == "string" and (x:find("on va au kraal", 1, true) or x:find("go rfk", 1, true)) then seenText = true end
    if type(k) == "string" and k:find("on va au", 1, true) then seenText = true end
    if type(x) == "table" then scan(x, depth + 1) end
  end
end
for _, nm in ipairs(NAMES) do scan(clients[nm].G.Join._state, 0); scan(clients[nm].G.db, 0); scan(clients[nm].G.char, 0) end
ok(not seenText, "vie privée : aucun texte du chat gardé (seulement le lieu déduit)")
clock = clock + 1800
settle()
ok(not line(Bv, "Kraal de Tranchebauge"), "30 min plus tard : l'intention du chat s'est éteinte")
-- « Maintenant » : une seule ligne du groupe à la fois (la plus importante), le reste dans la page
local G2 = Bv.G
G2:Offer("t1", "news", "Annonce-test", 1, { ttl = 60 })
G2:Offer("t2", "ready", "Prêt-test", 2, { ttl = 60 })
G2:Offer("t3", "vote", "Vote-test", 2, { ttl = 60 })
local posted = {}
for k in pairs(Bv.T.sugg) do if k:find("^Group:t%d$") then posted[#posted + 1] = k end end
ok(#posted == 1 and posted[1] == "Group:t2", "une seule annonce dans « Maintenant » : la plus importante (prêt) : " .. table.concat(posted, ","))
G2:Withdraw("t2")
posted = {}
for k in pairs(Bv.T.sugg) do if k:find("^Group:t%d$") then posted[#posted + 1] = k end end
ok(#posted == 1 and posted[1] == "Group:t3", "elle partie, la suivante prend sa place (vote)")
G2:Build()
ok(line(Bv, "Annonce-test") and not line(Bv, "Vote-test"), "les autres restent dans la page (le vote a déjà sa section)")
G2:Withdraw("t1"); G2:Withdraw("t3")
-- débit : positions immobiles → rien de plus sur le fil
for _, nm in ipairs(NAMES) do clients[nm].G.Join.Stop() end
run(30)
local t1 = clock
run(60)
for _, nm in ipairs(NAMES) do ok(sendsOf(clients[nm], t1) == 0, nm .. " : immobile, rien ne change → aucun message (" .. sendsOf(clients[nm], t1) .. ")") end
-- en combat : aucun clic n'agit, aucune lecture de position
clients.Alpha.cfg.combat = true
local before = #A.T.compass
local rj2 = line(A, "Rejoindre Bravo")
if rj2 and rj2.onClick then rj2.onClick() end
ok(#A.T.compass == before and A.T.sugg["Group:say"] and A.T.sugg["Group:say"].text == "Hors combat seulement", "en combat : « Hors combat seulement », boussole inchangée")
clients.Alpha.cfg.combat = false

-- -------------------------------------------------------------- instantanés pour TreeAI Coach (formats gardés)
A.G:SaveSummary()
local sm = A.T.summary
ok(sm.group and sm.group.v == 2 and #sm.group.members == 5 and sm.group.leader == "Alpha" and #sm.group.common > 0, "summary.group v2")
ok(sm.missions and sm.missions.v == 1 and #sm.missions.members == 5 and sm.missions.noAddon == 0 and #sm.missions.board >= 3 and sm.missions.next,
  "summary.missions v1 (Echo, resté en 3.9, compte comme ami TreeAI)")
ok(sm.team and sm.team.v == 1 and #sm.team.members == 4, "summary.team v1")
for _, b in ipairs(sm.missions.board) do ok(not tostring(b.title):find("|", 1, true), "résumé sans codes |") end

-- -------------------------------------------------------------- migration 4.0 (une fois) : anciens réglages repris, données effacées
do
  local cfgM = copy(cfgs.Alpha)
  cfgM.startOff = true   -- mode sûr : un module bêta jamais vu démarre coupé…
  cfgM.dbModules = { Squad = { enabled = true }, Missions = { enabled = false }, Team = { enabled = false, gear = false },
    Hunter = { enabled = true } }
  cfgM.dbChars = { ["Alpha-R"] = { modules = { Squad = { leaveAt = 5 }, Missions = { session = { turned = 3 } }, Hunter = { x = 1 } } } }
  local mc = newClient("Alpha", cfgM, {})
  local mods = mc.T.db.modules
  ok(mc.G.db.enabled == true, "…sauf si l'un des anciens modules était allumé (Squad)")
  ok(mc.G.db.gear == false, "réglage repris : inspection coupée (Team.gear = false)")
  ok(mods.Squad == nil and mods.Missions == nil and mods.Team == nil and mods.Hunter, "anciens réglages effacés, les autres gardés")
  local ch = mc.T.db.chars["Alpha-R"].modules
  ok(ch.Squad == nil and ch.Missions == nil and ch.Hunter and ch.Hunter.x == 1, "anciennes données par perso effacées")
  mc.G.db.enabled = false
  mc.G:Migrate()
  ok(mc.G.db.enabled == false, "migration une seule fois (le choix du joueur ensuite est gardé)")
end

print(("RESULT %s group checks=%d fails=%d"):format(fails == 0 and "ok" or "FAIL", checks, fails))
os.exit(fails == 0 and 0 or 1)
