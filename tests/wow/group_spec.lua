-- Groupe (Modules/Group.lua, GROUPMERGE 4.0) : moteur pur + fil v4 + simulation de 4 clients qui échangent leurs
-- messages par un faux canal PARTY (chacun charge Group.lua dans son propre environnement setfenv, avec ses propres
-- API simulées), + un 5e membre sans l'addon et un ami resté en 3.9 (Squad v2). Noms 100 % fictifs.
-- Usage : lua5.1 tests/wow/group_spec.lua [chemin/Group.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Group.lua"
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
  T.Data = { Macros = { items = { food = { { 117, 1 } }, water = { { 159, 1 } } } } }
  T.Compass = { SetTarget = function(_, tg) T.compass[#T.compass + 1] = tg return tg end }
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
  env.C_Map = { GetBestMapForUnit = function(u) return u == "player" and 1424 or nil end,
    GetMapInfo = function(id) return { name = "Carte " .. id } end }
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
  env.UnitPosition = function() return nil end
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
