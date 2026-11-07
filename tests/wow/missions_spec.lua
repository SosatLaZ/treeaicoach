-- Missions (suivi des missions du groupe) : simulation de 4 clients (Squad + Missions chargés dans
-- leur propre environnement setfenv, faux canal PARTY) + un 5e membre sans l'addon. Noms fictifs.
-- Usage : lua5.1 tests/wow/missions_spec.lua [Squad.lua] [Missions.lua]
local squadPath = arg[1] or "wow_addon/TreeAI/Modules/Squad.lua"
local path = arg[2] or "wow_addon/TreeAI/Modules/Missions.lua"
local fails, checks = 0, 0
local function ok(cond, msg)
  checks = checks + 1
  if not cond then fails = fails + 1; print("FAIL " .. msg) end
end
local function copy(t)
  if type(t) ~= "table" then return t end
  local o = {}
  for k, v in pairs(t) do o[k] = copy(v) end
  return o
end
local function find(list, pat)
  for _, x in ipairs(list) do
    local s = type(x) == "table" and (x.text or "") or tostring(x)
    if s:find(pat, 1, true) then return x end
  end
  return nil
end

local clock = 1000
local NAMES = { "Alpha", "Bravo", "Charlie", "Delta" }
local OUTSIDER = "Echo"  -- dans le groupe, sans TreeAI

local function newClient(me, cfg, bus)
  local env = setmetatable({}, { __index = _G })
  env._G = env
  local comm, events = {}, {}
  local T = { settings = { comms = true }, summary = {}, prints = {}, sugg = {}, toasts = {}, sends = {}, tabs = {},
              whispers = {}, compass = {} }
  T.API = {
    ItemCount = function(id) return cfg.items[id] or 0 end,
    FreeSlots = function() return 10, 64 end,
    ItemInfo = function() return nil end,
  }
  T.Data = { Macros = { items = { food = {}, water = {} } } }
  T.Boost = { XPPerHour = function() return cfg.xph or 6000 end }
  T.Compass = { SetTarget = function(_, tg) T.compass[#T.compass + 1] = tg end }
  function T:RegisterModule(name, tbl)
    tbl.name, tbl.core = name, self
    tbl.db = copy(tbl.defaults); tbl.db.enabled = true
    tbl.char = copy(tbl.charDefaults)
    tbl._active = true
    function tbl:IsEnabled() return true end
    function tbl:RegisterEvent(ev, fn)
      events[ev] = events[ev] or {}
      table.insert(events[ev], { mod = tbl, fn = fn })
    end
    function tbl:On() end
    function tbl:Suggest(id, text, prio, opts)
      T.sugg[name .. ":" .. id] = { text = text, prio = prio, opts = opts }
      T.toasts[#T.toasts + 1] = { text = text, prio = prio, opts = opts }
    end
    function tbl:Clear(id) T.sugg[name .. ":" .. id] = nil end
    return tbl
  end
  function T:Send(kind, payload)
    self.sends[#self.sends + 1] = { t = clock, kind = kind }
    bus[#bus + 1] = { from = me, kind = kind, payload = copy(payload) }
    return true
  end
  function T:DefaultChannel() return "PARTY" end
  function T:RegisterComm(kind, owner, fn) comm[kind] = function(p, s) fn(owner, p, s, "PARTY", kind) end end
  function T:InCombat() return false end
  function T:Serialize(t) local n = 0 for k, v in pairs(t) do n = n + #tostring(k) + #tostring(v) + 2 end return string.rep("x", n) end
  function T:Print(...) self.prints[#self.prints + 1] = table.concat({ ... }, " ") end
  T.slash = {}
  function T:RegisterSlash(cmd, fn) self.slash[cmd] = fn end
  function T:NewTicker() return { Cancel = function() end } end
  function T:Fire() end
  function T:Summary() return self.summary end
  function T:SetTabLines(id, lines, source) if source == nil or source == "missions" or source == "main" then self.tabs[id] = lines end end
  function T:RegisterTab(id, title) self.tabTitle = title end
  T.db = { chars = {} }
  env.TreeAI = T

  local others = {}
  for _, n in ipairs(NAMES) do if n ~= me then others[#others + 1] = n end end
  others[#others + 1] = OUTSIDER
  local function unitName(u)
    if u == "player" then return me end
    local i = tonumber(tostring(u):match("^party(%d)$"))
    return i and others[i] or nil
  end
  env.GetTime = function() return clock end
  env.time = function() return 1762500000 + clock end
  env.UnitName = unitName
  env.UnitExists = function(u)
    if u == "pet" then return true end
    if u == "target" then return cfg.target ~= nil end
    return unitName(u) ~= nil
  end
  env.IsInRaid = function() return false end
  env.UnitIsGroupLeader = function(u) return unitName(u) == "Alpha" end
  env.UnitIsConnected = function() return true end
  env.UnitClass = function() return cfg.class, cfg.class end
  env.UnitLevel = function(u) return u == "player" and cfg.level or 20 end
  env.UnitXP = function() return cfg.xp end
  env.UnitXPMax = function() return 10000 end
  env.GetXPExhaustion = function() return 0 end
  env.GetRealZoneText = function() return cfg.zone end
  env.C_Map = { GetBestMapForUnit = function() return 1424 end }
  env.UnitPosition = function() return cfg.y, cfg.x, 0, 0 end
  env.UnitHealth = function() return 100 end
  env.UnitHealthMax = function() return 100 end
  env.UnitPowerType = function() return 0 end
  env.UnitPower = function() return 100 end
  env.UnitPowerMax = function() return 100 end
  env.UnitIsGhost = function() return false end
  env.UnitIsDeadOrGhost = function(u) return u == "player" and cfg.dead or false end
  env.UnitIsDead = function() return false end
  env.GetPetHappiness = function() return 3 end
  env.GetInventoryItemDurability = function() return 90, 100 end
  env.GetBindLocation = function() return "Moulin-de-Tarren" end
  env.GetItemCooldown = function() return 0, 0 end
  env.GetInventoryItemCount = function() return 1500 end
  env.UnitIsAFK = function() return false end
  env.GetQuestID = function() return cfg.turnIn end
  env.LOOT_ITEM = "%s reçoit le butin : %s."
  env.LOOT_ITEM_MULTIPLE = "%s reçoit le butin : %sx%d."
  env.LOOT_ITEM_SELF = "Vous recevez le butin : %s."
  env.LOOT_ITEM_SELF_MULTIPLE = "Vous recevez le butin : %sx%d."
  env.GetPlayerFacing = function() return cfg.facing or 0 end
  env.GetProfessions = function() if cfg.profs then return 1, cfg.profs[2] and 2 or nil end end
  env.GetProfessionInfo = function(i) local p = cfg.profs and cfg.profs[i] if p then return p[1], nil, p[2] end end
  env.UnitCanAttack = function(_, u) return u == "target" and cfg.target ~= nil end
  env.UnitClassification = function() return cfg.target and cfg.target.cls or "normal" end
  env.GetRaidTargetIndex = function() return cfg.target and cfg.target.mark end
  env.SetRaidTarget = function(u, i) T.marks = T.marks or {}; T.marks[#T.marks + 1] = { u, i }; if cfg.target then cfg.target.mark = i end end
  env.UnitIsPlayer = function() return false end
  env.SendChatMessage = function(msg, kind, _, target) T.whispers[#T.whispers + 1] = { msg = msg, kind = kind, target = target } end
  local function log() return cfg.quests end
  local function byId(id) for _, x in ipairs(log()) do if x.questID == id then return x end end end
  env.C_QuestLog = {
    GetNumQuestLogEntries = function() return #log() end,
    GetInfo = function(i) return log()[i] end,
    IsComplete = function(id) local x = byId(id) return x and x.done end,
    GetQuestObjectives = function(id) local x = byId(id) return x and x.obj or {} end,
    GetMaxNumQuestsCanAccept = function() return 25 end,
    GetDistanceSqToQuest = function(id) local x = byId(id) return x and x.d2 or nil end,
    GetQuestsOnMap = function() return cfg.pois or {} end,
  }
  local ns = { Group = { members = {} }, Quests = { QuestieNext = function(_, id) return cfg.chain and cfg.chain[id] end } }
  ns.Quests.QuestieNext = function(id) return cfg.chain and cfg.chain[id] end
  for _, p in ipairs({ squadPath, path }) do
    local chunk = assert(loadfile(p))
    setfenv(chunk, env)
    chunk("TreeAI", ns)
  end
  local S, Mi = T.Squad, T.Missions
  S:OnInit(); Mi:OnInit()
  S:OnEnable(); Mi:OnEnable()
  local c = { name = me, env = env, T = T, S = S, Mi = Mi, comm = comm, events = events, cfg = cfg }
  function c.fire(ev, ...) for _, h in ipairs(events[ev] or {}) do h.fn(h.mod, ev, ...) end end
  function c.slash(cmd, rest) T.slash[cmd](rest or "") end
  return c
end

local function q(id, title, done, objs, d2)
  local t = { questID = id, title = title, level = 22, done = done, obj = {}, d2 = d2 }
  for _, o in ipairs(objs) do
    t.obj[#t.obj + 1] = { type = o[1], numFulfilled = o[2], numRequired = o[3], finished = o[2] >= o[3],
      text = o[4] and (o[4] .. " : " .. o[2] .. "/" .. o[3]) or nil }
  end
  return t
end
local function setDone(quest, done)
  quest.done = done
  for _, o in ipairs(quest.obj) do if done then o.numFulfilled = o.numRequired; o.finished = true end end
end
local function remove(list, id)
  for i = #list, 1, -1 do if list[i].questID == id then table.remove(list, i) end end
end

local cfgs = {
  Alpha = { class = "WARRIOR", level = 22, xp = 5000, zone = "Marche de l'Ouest", y = 100, x = 100, items = {},
    pois = { { questID = 500, x = 0.42, y = 0.61 } }, quests = {
      q(100, "Les sangliers", true, { { "item", 10, 10, "Défense de sanglier" } }, 90000),
      q(200, "Ogres", false, { { "monster", 3, 8, "Ogre tué" } }, 40000),
      q(500, "Sang de lion", true, { { "item", 6, 6, "Sang de lion" } }, 250000) } },
  Bravo = { class = "HUNTER", level = 22, xp = 9500, zone = "Marche de l'Ouest", y = 110, x = 100, items = {},
    quests = {
      q(100, "Les sangliers", true, { { "item", 10, 10, "Défense de sanglier" } }),
      q(200, "Ogres", false, { { "monster", 5, 8, "Ogre tué" } }),
      q(500, "Sang de lion", true, { { "item", 6, 6, "Sang de lion" } }) } },
  Charlie = { class = "MAGE", level = 22, xp = 3000, zone = "Marche de l'Ouest", y = 100, x = 110, items = {},
    quests = {
      q(100, "Les sangliers", false, { { "item", 2, 10, "Défense de sanglier" } }),
      q(200, "Ogres", false, { { "monster", 1, 8, "Ogre tué" } }),
      q(500, "Sang de lion", true, { { "item", 6, 6, "Sang de lion" } }) } },
  Delta = { class = "ROGUE", level = 20, xp = 9000, zone = "Marche de l'Ouest", y = 600, x = 100, items = {},
    quests = {
      q(100, "Les sangliers", false, { { "item", 5, 10, "Défense de sanglier" } }),
      q(300, "Lettre à Bob", false, { { "event", 0, 1 } }) } },
}

local bus, clients = {}, {}
for _, n in ipairs(NAMES) do clients[n] = newClient(n, cfgs[n], bus) end

local function run(ticks)
  for _ = 1, ticks do
    clock = clock + 1
    for _, n in ipairs(NAMES) do clients[n].S:Tick(); clients[n].Mi:Tick() end
    local snapshot = {}
    for i, m in ipairs(bus) do snapshot[i] = m end
    for i = #bus, 1, -1 do bus[i] = nil end
    for _, m in ipairs(snapshot) do
      for _, n in ipairs(NAMES) do
        if n ~= m.from and clients[n].comm[m.kind] then clients[n].comm[m.kind](copy(m.payload), m.from .. "-Royaume") end
      end
    end
  end
  for _, n in ipairs(NAMES) do clients[n].S:Recompute(); clients[n].Mi:Tick() end
end
run(30)

-- ---------------------------------------------------------------- tableau identique chez les 4
local A = clients.Alpha.Mi
local ref = A.board
ok(#ref == 4, "4 missions au tableau (" .. #ref .. ")")
for _, n in ipairs(NAMES) do
  local b = clients[n].Mi.board
  local same = #b == #ref
  for i = 1, #ref do same = same and b[i] and b[i].id == ref[i].id and b[i].pct == ref[i].pct end
  ok(same, "tableau identique chez " .. n)
end
ok(ref[1].id == 500 and ref[1].allDone and #ref[1].holders == 3, "1er : Sang de lion, tous prêts")
ok(ref[2].id == 200 and ref[2].shared, "2e : Ogres (que des monstres, finissable ensemble)")
ok(ref[3].id == 100 and #ref[3].holders == 4 and #ref[3].done == 2, "3e : Les sangliers 2/4")
ok(ref[3].pct == 67, "progression du groupe en % (" .. tostring(ref[3].pct) .. ")")
ok(ref[4].id == 300 and not ref[4].multi, "dernier : quête solo")
ok(#ref[1].missing == 1 and ref[1].missing[1] == "Delta", "qui n'a pas la quête")
ok(ref[1].poi and ref[1].poi.x == 0.42 and ref[1].poi.from == "Alpha", "point carte partagé")
ok(ref[2].dist == 200, "distance moyenne partagée (" .. tostring(ref[2].dist) .. ")")
local status = clients.Delta.Mi.Engine.StatusLine(clients.Delta.Mi.board[3])
print("  " .. status)
ok(status == "Charlie : 8 Défense de sangli restants, Delta : 5 Défense de sangli restants, Alpha : terminé, Bravo : terminé",
  "ligne d'état « X : N restants, Y : terminé » : " .. status)

-- prochaine mission / répartition
ok(A.next and A.next.kind == "turnin" and A.next.text == "Rendez ensemble « Sang de lion » (3/3 prêts)", "prochaine : rendre ensemble")
for _, a in ipairs(A.assign) do print("  • " .. a.text) end
ok(find(A.assign, "« Ogres » : tuez ensemble Ogre tué (crédit partagé) — reste 7 pour Charlie, Alpha 5, Bravo 3"), "tuer ensemble")
local loot = find(A.assign, "« Les sangliers » : Charlie loote Défense de sangli d'abord (8), puis Delta 5")
ok(loot, "qui loote d'abord")
ok(loot and loot.text:find("Delta (loin) : de ton côté", 1, true), "groupe éloigné : de ton côté")

-- onglet « Groupe » (contrat panneau)
local tab = clients.Alpha.T.tabs.group
ok(tab and #tab >= 6 and clients.Alpha.T.tabTitle == "Groupe", "onglet group rempli")
ok(tab and find(tab, "Sang de lion 3/3 · 100 %"), "ligne de mission avec barre")
ok(tab and tab[1].text:find("Missions", 1, true) and tab[2].onClick, "en-tête + ligne cible cliquable")
for _, l in ipairs(tab or {}) do ok(type(l.text) == "string" and l.prio >= 1 and l.prio <= 3, "ligne valide") end

-- ---------------------------------------------------------------- « attendez » au moment de rendre
cfgs.Alpha.turnIn = 100
clients.Alpha.fire("QUEST_COMPLETE")
local w = clients.Alpha.T.sugg["Missions:turnin"]
ok(w and w.text == "Attendez Charlie (2/10), Delta (5/10) avant de rendre « Les sangliers »" and w.prio == 2, "attendez avant de rendre : " .. tostring(w and w.text))
cfgs.Alpha.chain = { [100] = "Suite : « Le chef sanglier »" }
clients.Alpha.fire("QUEST_COMPLETE")
w = clients.Alpha.T.sugg["Missions:turnin"]
ok(w and w.prio == 3 and w.text:find("ouvre une suite", 1, true), "chaîne : urgent")

-- ---------------------------------------------------------------- prise en charge partagée
clients.Charlie.slash("prends", "sangliers")
run(6)
local b100
for _, e in ipairs(A.board) do if e.id == 100 then b100 = e end end
ok(b100 and b100.claims[1] and b100.claims[1].by == "Charlie" and b100.claims[1].j == 0, "prise de Charlie reçue chez Alpha")
ok(find(A.assign, "[Charlie s'en charge]"), "répartition : Charlie s'en charge")
clients.Alpha.slash("prends", "ogres 1")
run(6)
ok(find(clients.Bravo.Mi.assign, "reste 7 pour Charlie") and find(clients.Bravo.Mi.assign, "[Alpha s'en charge]"), "prise d'un objectif")
clients.Alpha.slash("prends", "ogres 1")
run(6)
ok(not find(clients.Bravo.Mi.assign, "[Alpha s'en charge]"), "lâcher (bascule)")
clients.Delta.slash("prends", "inconnue")
ok(find(clients.Delta.T.prints, "Mission introuvable"), "mission introuvable")
-- clic sur une ligne de mission = prendre
local line
for _, l in ipairs(clients.Bravo.T.tabs.group) do if l.text:find("Ogres 0/3", 1, true) then line = l end end
ok(line and line.onClick, "ligne Ogres cliquable")
if line then line.onClick() end
run(6)
local b200
for _, e in ipairs(A.board) do if e.id == 200 then b200 = e end end
ok(b200 and b200.claims[1] and b200.claims[1].by == "Bravo", "clic = prise partagée")

-- ---------------------------------------------------------------- cible du groupe (chef seulement)
clients.Alpha.slash("mission", "cible")
run(4)
local tg = clients.Charlie.T.compass[#clients.Charlie.T.compass]
ok(tg and tg.label == "Sang de lion" and tg.x == 0.42 and tg.mapID == 1424, "cible du chef -> boussole des amis")
ok(clients.Alpha.T.compass[1] and clients.Alpha.T.compass[1].label == "Sang de lion", "boussole du chef")
ok(clients.Charlie.T.sugg["Missions:ev:target"], "annonce cible du groupe")
local before = #clients.Charlie.T.compass
clients.Charlie.comm.MST({ v = 1, q = 1, m = 1424, x = 0.1, y = 0.1, l = "Pirate" }, "Bravo-Royaume")
ok(#clients.Charlie.T.compass == before, "cible d'un non-chef ignorée")
clients.Charlie.comm.MST({ v = 1, x = 7, y = 0.1, l = "x" }, "Alpha-Royaume")
clients.Charlie.comm.MST({ v = 9, x = 0.1, y = 0.1 }, "Alpha-Royaume")
ok(#clients.Charlie.T.compass == before, "cible invalide / version ignorée")

-- ---------------------------------------------------------------- objectif de session
clients.Alpha.slash("mission", "objectif niveau 23")
run(4)
local sd = clients.Delta.Mi.char.session
ok(type(sd) == "table" and sd.goal and sd.goal.k == "level" and sd.goal.n == 23 and sd.goal.by == "Alpha", "objectif reçu chez Delta")
local gs = clients.Delta.Mi.Engine.Goal(sd.goal, clients.Delta.Mi.list, sd, 1762500000 + clock)
print("  " .. gs.text)
ok(gs.text:find("Objectif : niveau 23 pour tous — 0/4", 1, true) and gs.text:find("~", 1, true) and gs.slow == "Delta", "progression + ETA (Delta le plus loin)")
ok(find(clients.Delta.T.toasts, "Objectif de Alpha : niveau 23 pour tous"), "annonce objectif")

-- ---------------------------------------------------------------- événements
clock = clock + 10
setDone(cfgs.Charlie.quests[1], true)
remove(cfgs.Delta.quests, 300)
cfgs.Bravo.level = 23
cfgs.Charlie.dead = true
run(25)
local toasts = clients.Alpha.T.toasts
for _, s in ipairs({ "Charlie a fini « Les sangliers »", "Delta a abandonné « Lettre à Bob »", "Bravo passe niveau 23 !",
  "Charlie est mort", "Echo n'a pas TreeAI" }) do
  ok(find(toasts, s), "annonce : " .. s)
end
ok(not find(clients.Charlie.T.toasts, "Charlie a fini"), "pas d'annonce de ses propres quêtes")
local noA = find(toasts, "Echo n'a pas TreeAI")
ok(noA and noA.opts and noA.opts.onClick, "annonce sans TreeAI cliquable")
if noA then noA.opts.onClick(); noA.opts.onClick() end
ok(#clients.Alpha.T.whispers == 1 and clients.Alpha.T.whispers[1].target == "Echo" and clients.Alpha.T.whispers[1].kind == "WHISPER",
  "message d'installation au clic, une seule fois")
ok(clients.Alpha.Mi.noAddon[1] == "Echo", "liste des membres sans l'addon")

setDone(cfgs.Delta.quests[1], true)
run(15)
ok(find(clients.Bravo.T.toasts, "Tous prêts à rendre « Les sangliers »"), "tous prêts à rendre")

for _, n in ipairs({ "Alpha", "Bravo", "Charlie" }) do remove(cfgs[n].quests, 500) end
run(15)
ok(find(clients.Delta.T.toasts, "Mission terminée : « Sang de lion »"), "mission terminée")
local sa = clients.Alpha.Mi.char.session
ok(sa.turned == 1 and sa.missions == 1 and sa.abandoned == 1 and sa.deaths.Charlie == 1 and sa.byMember.Alpha == 1, "stats de session")

-- ---------------------------------------------------------------- récap + résumé IA
local rec = clients.Alpha.Mi:Recap(false)
for _, l in ipairs(rec) do print("  " .. l) end
ok(find(rec, "1 quête rendue (Sang de lion), 1 mission terminée ensemble, 1 abandon."), "récap quêtes")
ok(find(rec, "Bravo 22→23"), "récap niveaux")
ok(find(rec, "Morts : Charlie 1"), "récap morts")
ok(find(rec, "Objectif : niveau 23"), "récap objectif")
clients.Alpha.slash("mission", "recap")
ok(find(clients.Alpha.T.prints, "Récap de session"), "/tai mission recap")
clients.Alpha.slash("mission", "")
ok(find(clients.Alpha.T.prints, "1. ["), "/tai mission : tableau")
clients.Alpha.Mi:SaveSummary()
local sm = clients.Alpha.T.summary.missions
ok(sm and sm.v == 1 and #sm.members == 4 and #sm.board >= 2 and sm.session and sm.session.turned == 1
  and sm.session.goal and sm.session.goal.kind == "level" and sm.next, "summary.missions")
for _, b in ipairs(sm and sm.board or {}) do ok(not tostring(b.title):find("|", 1, true), "résumé sans codes |") end

-- ---------------------------------------------------------------- sécurité / débit
local C = clients.Charlie
C.comm.MSC("1:200.0", "Intrus-Royaume")
ok(C.Mi._claims.Intrus == nil, "intrus ignoré")
C.comm.MSM("2:100,1,1,1,1,x", "Bravo-Royaume")
ok(C.Mi._info.Bravo and C.Mi._info.Bravo[100], "version inconnue ignorée")
C.comm.MSM({ evil = true }, "Bravo-Royaume")
C.comm.MSC(string.rep("9", 500), "Bravo-Royaume")
C.comm.MSG({ v = 1, k = "rm -rf", n = 1 }, "Bravo-Royaume")
ok(C.Mi.char.session.goal.k == "level", "déchets ignorés")
for _, n in ipairs(NAMES) do
  local s, last, okRate = clients[n].T.sends, {}, true
  local mine = {}
  for _, x in ipairs(s) do if x.kind:match("^MS") then mine[#mine + 1] = x.t end end
  for i = 2, #mine do if mine[i] - mine[i - 1] < 2 then okRate = false end end
  ok(okRate and #mine >= 2, n .. " débit Missions ≤ 1 msg / 2 s (" .. #mine .. ")")
end

-- ---------------------------------------------------------------- moteur pur
local Mi = C.Mi
local E = Mi.Engine
local list = C.S:MemberList()
local rev = {}
for i = #list, 1, -1 do rev[#rev + 1] = list[i] end
local b1, b2 = E.Board(list, {}), E.Board(rev, {})
local same = #b1 == #b2
for i = 1, #b1 do same = same and b1[i].id == b2[i].id end
ok(same, "ordre d'entrée indifférent")
ok(#E.Board({}, {}) == 0 and E.Next({}) == nil and #E.Assign({}, {}, {}) == 0, "vide : rien")
local d = Mi.DecodeInfo(Mi.EncodeInfo({ { id = 7, d = 123, m = 1424, x = 0.5, y = 0.25, names = { "Croc, de; loup|r", "B" } } }))[7]
ok(d and d.d == 120 and d.x == 0.5 and d.y == 0.25 and d.names[1] == "Croc de loup r" and d.names[2] == "B", "codec infos")
local cl = Mi.DecodeClaims(Mi.EncodeClaims({ { q = 5, j = 2 }, { q = 6 } }))
ok(#cl == 2 and cl[1].q == 5 and cl[1].j == 2 and cl[2].j == 0, "codec prises")
ok(Mi.ObjName("Croc de loup : 3/8") == "Croc de loup" and Mi.ObjName("3/8 Croc") == "Croc"
  and Mi.ObjName("Lettre livrée (Terminé)") == "Lettre livrée", "nom d'objectif")
ok(Mi.DecodeGoal({ v = 1, k = "level", n = 999 }).n == 60 and Mi.DecodeGoal({ v = 1, k = "x" }) == nil, "objectif borné")
ok(Mi.TotalXP(2, 0) == 400 and Mi.TotalXP(60, 0) == Mi.TotalXP(80, 50), "table XP")
ok(Mi.Duration(5400) == "1 h 30" and Mi.Duration(30) == "1 min", "durées")
ok(Mi.Bar(50, 10):find(":8:5:", 1, true) and Mi.AsciiBar(50) == "[=====-----]", "barres")
-- diff pur : arrivée / départ / hors ligne / AFK / loin
local p = { m = { X = { l = 10, d = 0, af = 0, quests = {} } }, ready = {}, held = {} }
local c2 = { m = { X = { l = 10, d = 0, af = 1, off = true, far = true, farDist = 400, quests = {} },
  Y = { addon = false, quests = false } }, ready = {}, held = {} }
local ev = E.Diff(p, c2)
ok(find(ev, "X est AFK") and find(ev, "X est hors ligne") and find(ev, "X est loin : hors portée du crédit de kill") and find(ev, "Y a rejoint le groupe"), "diff : AFK / hors ligne / loin / arrivée")
ev = E.Diff(c2, { m = { X = c2.m.X }, ready = {}, held = {} })
ok(find(ev, "Y a quitté le groupe"), "diff : départ")
ok(#E.Diff(nil, c2) == 0, "premier instantané : aucun événement")


-- ---------------------------------------------------------------- 41 « Prêt ? »
cfgs.Charlie.dead = false
run(4)
clients.Alpha.slash("mission", "pret")
run(3)
local rq = clients.Bravo.T.sugg["Missions:ready"]
ok(rq and rq.prio == 3 and rq.text:find("Alpha demande : prêt ?", 1, true) and rq.opts.tab == "group" and rq.opts.onClick, "demande « prêt ? » reçue (clic)")
rq.opts.onClick()
clients.Charlie.slash("mission", "pret oui")
clients.Delta.slash("mission", "pret non boisson")
run(3)
local states = clients.Alpha.Mi.Engine.ReadyState(clients.Alpha.Mi.readyCheck, clients.Alpha.Mi.list, clients.Alpha.Mi._rost)
local byName = {}
for _, e in ipairs(states) do byName[e.name] = e end
ok(byName.Bravo.state == "ok" and byName.Charlie.state == "ok" and byName.Delta.state == "no" and byName.Delta.why == "boisson"
  and byName.Echo.state == "noaddon", "états prêt / pas prêt / sans TreeAI")
local rline = find(clients.Alpha.T.tabs.group, "Prêt ? ")
ok(rline and rline.text:find("Delta", 1, true), "ligne d'états dans l'onglet")
run(32)
ok(find(clients.Alpha.T.toasts, "Pas prêts : Delta (boisson), Echo"), "résultat après délai")
ok(clients.Alpha.Mi.SelfReadiness({ h = 100, p = 20, d = 0 }) == false and select(2, clients.Alpha.Mi.SelfReadiness({ h = 30, d = 0 })) == "PV 30 %", "auto-évaluation")

-- ---------------------------------------------------------------- 42 butin
local Am = clients.Alpha
Am.fire("CHAT_MSG_LOOT", "Bravo reçoit le butin : |cff0070dd|Hitem:1:0|h[Arc runique]|h|r.")
Am.fire("CHAT_MSG_LOOT", "Bravo reçoit le butin : |cff1eff00|Hitem:2:0|h[Gants verts]|h|r.")
Am.fire("CHAT_MSG_LOOT", "Vous recevez le butin : |cffffffff|Hitem:3:0|h[Croc]|h|rx3.")
Am.fire("CHAT_MSG_LOOT", "Intrus reçoit le butin : |cffa335ee|Hitem:4:0|h[Epée]|h|r.")
local ll = Am.Mi.Engine.LootLines(Am.Mi.char.session.loot)
ok(ll[1] == "Bravo : 2 objets, 1 bleu, 1 vert (meilleur : Arc runique)" and ll[2] == "Alpha : 3 objets", "butin : " .. tostring(ll[1]) .. " / " .. tostring(ll[2]))
ok(#ll == 2, "butin hors groupe ignoré")
ok(find(Am.T.toasts, "Bravo a obtenu Arc runique"), "annonce objet bleu")
ok(Am.Mi.ItemFromLink("|cnIQ4:|Hitem:5|h[Truc]|h|r") == "Truc" and select(2, Am.Mi.ItemFromLink("|cnIQ4:|Hitem:5|h[Truc]|h|r")) == 4, "lien moderne")

-- ---------------------------------------------------------------- 43 où est chacun
local wl = Am.Mi:WhereLines()
local wb, wd, we
for _, w in ipairs(wl) do if w.name == "Bravo" then wb = w elseif w.name == "Delta" then wd = w elseif w.name == "Echo" then we = w end end
ok(wb and wb.text == "Bravo : < 9 m à gauche", "Bravo : " .. tostring(wb and wb.text))
ok(wd and wd.text:find("m à gauche", 1, true), "Delta : " .. tostring(wd and wd.text))
ok(we and we.text:find("sans TreeAI", 1, true), "Echo sans TreeAI")
local E2 = Am.Mi.Engine
local mk = function(y, x) return { st = { py = y, px = x, pi = 0 } } end
ok(E2.Direction(mk(0, 0), mk(0, 100), 0).word == "devant" and E2.Direction(mk(0, 0), mk(0, -100), 0).word == "derrière"
  and E2.Direction(mk(0, 0), mk(100, 0), 0).word == "à gauche" and E2.Direction(mk(0, 0), mk(-100, 0), 0).word == "à droite"
  and E2.Direction(mk(0, 0), mk(100, 0), math.pi / 2).word == "devant" and E2.Direction(mk(0, 0), mk(0, 100)).word == "au N", "directions")

-- ---------------------------------------------------------------- 44 marquage
cfgs.Alpha.target = { cls = "elite" }
Am.Mi._dirty = true
Am.Mi:Tick()
local ml = find(Am.T.tabs.group, "[Marquer]")
ok(ml and ml.text:find("crâne", 1, true) and ml.onClick, "marquage conseillé : crâne sur élite")
ml.onClick(); ml.onClick()
ok(Am.T.marks and #Am.T.marks == 1 and Am.T.marks[1][1] == "target" and Am.T.marks[1][2] == 8, "une marque par clic, pas de doublon")
Am.T.InCombat = function() return true end
cfgs.Alpha.target = { cls = "normal" }
ok(Am.Mi:MarkTarget() == false and #Am.T.marks == 1, "jamais en combat")
Am.T.InCombat = function() return false end
ok(Am.Mi.SuggestMark({ level = 30, myLevel = 22 }) == 8 and Am.Mi.SuggestMark({ level = 20, myLevel = 22 }) == 7, "choix de la marque")
cfgs.Alpha.target = nil

-- ---------------------------------------------------------------- 45 classement XP
cfgs.Charlie.xp = 6000
run(20)
local lb = Am.Mi.Engine.Leaderboard(Am.Mi.list, Am.Mi.char.session.startLv)
ok(lb[1].name == "Bravo" and lb[1].xp > 0 and lb[2].name == "Charlie" and lb[2].xp == 8190, "classement XP (" .. lb[1].name .. " " .. lb[1].xp .. ", " .. lb[2].name .. " " .. lb[2].xp .. ")")
ok(find(Am.T.tabs.group, "XP session : Bravo +"), "ligne classement")
ok(Am.Mi.KFmt(6300) == "6.3k" and Am.Mi.KFmt(2000) == "2k" and Am.Mi.KFmt(450) == "450", "format k")

-- ---------------------------------------------------------------- 46 plan de voyage
cfgs.Delta.zone = "Forêt d'Elwynn"
run(20)
local plan = Am.Mi.plan
ok(plan and plan.dest == "Marche de l'Ouest" and plan.steps[1] and plan.steps[1].name == "Delta", "plan : Delta rejoint")
ok(plan and plan.steps[1].text == "Delta (Forêt d'Elwynn) : en route vers Marche de l'Ouest (pas de pierre)", "étape : " .. tostring(plan and plan.steps[1] and plan.steps[1].text))
local list2 = {
  { name = "A", st = { z = "Marche de l'Ouest", l = 20 } },
  { name = "B", st = { z = "Elwynn", hb = "Marche de l'Ouest", hs = 0, l = 20 } },
  { name = "C", st = { z = "Elwynn", hb = "Marche de l'Ouest", hs = 600, l = 20 } },
}
local p2 = Am.Mi.Engine.TravelPlan(list2, { zone = "Marche de l'Ouest" })
ok(p2.steps[1].text:find("pierre de foyer prête", 1, true) and p2.steps[2].text:find("pierre dans 10 min", 1, true), "pierre de foyer dans le plan")

-- ---------------------------------------------------------------- 47 artisans
local list3 = {
  { name = "A", st = { c = "WARRIOR", pr = "Forge 130 Premiers soins 85", b = 0, bt = 48, fo = 0, am = -1 } },
  { name = "B", st = { c = "HUNTER", pr = "Couture 90", b = 10, bt = 64, fo = 20, am = 400 } },
  { name = "C", st = { c = "MAGE", pr = "Ingénierie 80", b = 10, bt = 64, fo = 20, am = -1 } },
}
local cr = Am.Mi.Engine.Crafters(list3)
local function crf(p) for _, c in ipairs(cr) do if c.text:find(p, 1, true) then return c end end end
ok(crf("B (Couture 90) : sacs en laine (8) pour A"), "couture -> sacs pour qui en manque")
ok(crf("C (Ingénierie 80) : balles lourdes pour B"), "ingénierie -> balles pour le chasseur")
ok(crf("A (Forge 130) : pierres à aiguiser solides pour B"), "forge -> pierres")
ok(crf("A (Premiers soins 85) : bandages en laine"), "bandages pour tous")
ok(cr[1].targeted, "utiles d'abord")
ok(#Am.Mi.ParseProfessions("Travail du cuir 75 Dépeçage 80") == 2, "métiers à plusieurs mots")

clients.Alpha.Mi:SaveSummary()
sm = clients.Alpha.T.summary.missions
ok(sm.session.leaderboard[1] and sm.session.loot[1] and sm.travel and sm.crafts, "résumé : classement, butin, voyage, artisans")
for _, cmd in ipairs({ "ou", "xp", "voyage", "metiers", "butin", "marque", "inconnu" }) do
  local okc = pcall(clients.Alpha.slash, "mission", cmd)
  ok(okc, "/tai mission " .. cmd)
end

print(("RESULT %s missions checks=%d fails=%d"):format(fails == 0 and "ok" or "FAIL", checks, fails))
os.exit(fails == 0 and 0 or 1)
