-- Squad (cerveau de groupe) : moteur pur + simulation de 4 clients qui échangent leurs messages
-- par un faux canal PARTY. Chaque client charge Modules/Squad.lua dans son propre environnement
-- (setfenv) avec ses propres API simulées. Noms 100 % fictifs.
-- Usage : lua5.1 tests/wow/squad_spec.lua [chemin/Squad.lua]
local path = arg[1] or "wow_addon/TreeAI/Modules/Squad.lua"
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
local function has(list, pat)
  for _, a in ipairs(list) do if a.text:find(pat, 1, true) then return a end end
  return nil
end

local clock = 1000
local NAMES = { "Alpha", "Bravo", "Charlie", "Delta" }

-- -------------------------------------------------------------- un client simulé
local function newClient(me, cfg, bus)
  local env = setmetatable({}, { __index = _G })
  env._G = env
  local comm, events = {}, {}
  local T = { settings = { comms = true }, db = {}, summary = {}, prints = {}, sugg = {}, sends = {} }
  T.API = {
    ItemCount = function(id) return cfg.items[id] or 0 end,
    FreeSlots = function() return cfg.bags, 64 end,
    ItemInfo = function() return nil end,
  }
  T.Data = { Macros = { items = { food = { { 117, 1 } }, water = { { 159, 1 } } } } }
  function T:RegisterModule(name, tbl)
    tbl.name, tbl.core = name, self
    tbl.db = copy(tbl.defaults); tbl.db.enabled = true
    tbl.char = copy(tbl.charDefaults)
    tbl._active = true
    function tbl:IsEnabled() return true end
    function tbl:RegisterEvent(ev, fn) events[ev] = fn end
    function tbl:On() end
    function tbl:Suggest(id, text) T.sugg[id] = text end
    function tbl:Clear(id) T.sugg[id] = nil end
    return tbl
  end
  function T:Send(kind, payload)
    self.sends[#self.sends + 1] = clock
    bus[#bus + 1] = { from = me, kind = kind, payload = copy(payload) }
    return true
  end
  function T:DefaultChannel() return "PARTY" end
  function T:RegisterComm(kind, owner, fn) comm[kind] = function(p, s) fn(owner, p, s, "PARTY", kind) end end
  function T:InCombat() return false end
  function T:Serialize(t) local n = 0 for k, v in pairs(t) do n = n + #tostring(k) + #tostring(v) + 2 end return string.rep("x", n) end
  function T:Print(...) self.prints[#self.prints + 1] = table.concat({ ... }, " ") end
  function T:RegisterSlash() end
  function T:NewTicker() return { Cancel = function() end } end
  function T:Fire() end
  function T:Summary() return self.summary end
  T.db = { chars = {} }
  env.TreeAI = T

  local others = {}
  for _, n in ipairs(NAMES) do if n ~= me then others[#others + 1] = n end end
  env.GetTime = function() return clock end
  env.time = function() return 1762500000 + clock end
  env.UnitName = function(u)
    if u == "player" then return me end
    local i = tonumber(tostring(u):match("^party(%d)$"))
    return i and others[i] or nil
  end
  env.UnitExists = function(u)
    if u == "pet" then return cfg.pet end
    local i = tonumber(tostring(u):match("^party(%d)$"))
    return i and others[i] ~= nil or false
  end
  env.IsInRaid = function() return false end
  env.UnitClass = function() return cfg.class, cfg.class end
  env.UnitLevel = function() return cfg.level end
  env.UnitXP = function() return cfg.xp end
  env.UnitXPMax = function() return 10000 end
  env.GetXPExhaustion = function() return cfg.rested end
  env.GetRealZoneText = function() return cfg.zone end
  env.C_Map = { GetBestMapForUnit = function() return 1424 end }
  env.UnitPosition = function() return cfg.y, cfg.x, 0, 0 end
  env.UnitHealth = function() return cfg.hp end
  env.UnitHealthMax = function() return 100 end
  env.UnitPowerType = function() return cfg.class == "WARRIOR" and 1 or 0 end
  env.UnitPower = function() return cfg.mana end
  env.UnitPowerMax = function() return 100 end
  env.UnitIsGhost = function() return false end
  env.UnitIsDeadOrGhost = function() return false end
  env.UnitIsDead = function() return false end
  env.GetPetHappiness = function() return 3 end
  env.GetInventoryItemDurability = function(s) if s == 1 then return cfg.dur, 100 end end
  env.GetBindLocation = function() return "Moulin-de-Tarren" end
  env.GetItemCooldown = function() return 0, 0 end
  env.GetInventoryItemCount = function() return 1500 end
  env.UnitIsAFK = function() return false end
  local log = cfg.quests
  env.C_QuestLog = {
    GetNumQuestLogEntries = function() return #log end,
    GetInfo = function(i) return log[i] end,
    IsComplete = function(id) for _, q in ipairs(log) do if q.questID == id then return q.done end end end,
    GetQuestObjectives = function(id) for _, q in ipairs(log) do if q.questID == id then return q.obj end end return {} end,
    GetMaxNumQuestsCanAccept = function() return 25 end,
  }
  local ns = { Group = { members = {} } }
  local chunk = assert(loadfile(path))
  setfenv(chunk, env)
  chunk("TreeAI", ns)
  local S = T.Squad
  S:OnInit()
  S:OnEnable()
  return { name = me, env = env, T = T, S = S, comm = comm, events = events, ns = ns, cfg = cfg }
end

local function q(id, title, done, objs, extra)
  local t = { questID = id, title = title, level = 22, done = done, obj = {} }
  for _, o in ipairs(objs) do
    t.obj[#t.obj + 1] = { type = o[1], numFulfilled = o[2], numRequired = o[3], finished = o[2] >= o[3] }
  end
  for k, v in pairs(extra or {}) do t[k] = v end
  return t
end
local H = { isHeader = true, title = "Les Mortemines" }

local cfgs = {
  Alpha = { class = "WARRIOR", level = 22, xp = 5000, rested = 0, zone = "Marche de l'Ouest", y = 100, x = 100,
    hp = 100, mana = 100, bags = 10, dur = 90, items = { [6948] = 1, [117] = 10 }, quests = {
      q(100, "Les sangliers", true, { { "item", 10, 10 } }), q(200, "Ogres", false, { { "monster", 3, 8 } }),
      H, q(400, "Le défias", false, { { "monster", 0, 1 } }) } },
  Bravo = { class = "HUNTER", level = 22, xp = 1000, rested = 8000, zone = "Marche de l'Ouest", y = 110, x = 100,
    hp = 100, mana = 90, bags = 1, dur = 80, pet = false, items = { [6948] = 1, [117] = 10, [159] = 5 }, quests = {
      q(100, "Les sangliers", true, { { "item", 10, 10 } }), q(200, "Ogres", false, { { "monster", 5, 8 } }),
      H, q(400, "Le défias", false, { { "monster", 0, 1 } }) } },
  Charlie = { class = "MAGE", level = 22, xp = 3000, rested = 0, zone = "Marche de l'Ouest", y = 100, x = 110,
    hp = 100, mana = 20, bags = 8, dur = 90, items = { [6948] = 1, [117] = 3, [159] = 5 }, quests = {
      q(100, "Les sangliers", false, { { "item", 2, 10 } }), q(200, "Ogres", false, { { "monster", 1, 8 } }),
      H, q(400, "Le défias", false, { { "monster", 0, 1 } }) } },
  Delta = { class = "ROGUE", level = 20, xp = 9000, rested = 0, zone = "Marche de l'Ouest", y = 600, x = 100,
    hp = 100, mana = 0, bags = 0, dur = 70, items = { [117] = 4 }, quests = {
      q(100, "Les sangliers", false, { { "item", 5, 10 } }), q(300, "Lettre; à Bob", false, { { "event", 0, 1 } }) } },
}

local bus = {}
local clients = {}
for _, n in ipairs(NAMES) do clients[n] = newClient(n, cfgs[n], bus) end
clients.Alpha.S:SetLeave(20)

for _ = 1, 40 do
  clock = clock + 1
  for _, n in ipairs(NAMES) do clients[n].S:Tick() end
  local snapshot = {}
  for i, m in ipairs(bus) do snapshot[i] = m end
  for i = #bus, 1, -1 do bus[i] = nil end
  for _, m in ipairs(snapshot) do
    for _, n in ipairs(NAMES) do
      if n ~= m.from and clients[n].comm[m.kind] then clients[n].comm[m.kind](copy(m.payload), m.from .. "-Royaume") end
    end
  end
end
for _, n in ipairs(NAMES) do clients[n].S:Recompute() end

-- même conseils chez les 4 (déterminisme)
local ref = clients.Alpha.S:Advices()
ok(#ref >= 5, "au moins 5 conseils (" .. #ref .. ")")
for _, n in ipairs(NAMES) do
  local a = clients[n].S:Advices()
  local same = #a == #ref
  for i = 1, #ref do same = same and a[i] and a[i].text == ref[i].text end
  ok(same, "conseils identiques chez " .. n)
end
for _, a in ipairs(ref) do print("  " .. a.cat .. " p" .. a.prio .. " " .. a.text) end

ok(has(ref, "Delta n'a pas « Ogres » : Alpha, partage-la"), "partage Ogres")
ok(has(ref, "Attendez Charlie (2/10) avant de rendre « Les sangliers »"), "synchro du rendu")
ok(has(ref, "Delta est 2 niveaux derrière"), "écart de niveau")
ok(has(ref, "Pause boisson : Charlie 20 % mana"), "pause boisson")
ok(has(ref, "Bravo : familier absent"), "familier")
ok(has(ref, "Passage marchand : 2 joueurs ont les sacs pleins"), "marchand")
ok(has(ref, "Delta est loin"), "loin")
ok(has(ref, "Alpha part dans 20 min : finissez"), "départ")
local all = clients.Alpha.S.Engine.Advise(clients.Alpha.S:MemberList(), { opts = {}, all = true })
local engineAll = {}
for _, a in ipairs(all) do engineAll[#engineAll + 1] = a end
ok(has(engineAll, "Prêts pour Les Mortemines : 3/4 ont les quêtes"), "donjon")
ok(has(engineAll, "Laissez Charlie looter : « Les sangliers » (2/10)"), "butin quête")
ok(has(engineAll, "À faire à part : Delta — « Lettre à Bob »"), "à faire à part (titre nettoyé)")
ok(has(engineAll, "En repos (XP x2) : Bravo 80 %"), "repos")
ok(has(engineAll, "4/4 ont la pierre prête") == nil and has(engineAll, "3/4 ont la pierre prête, bind à Moulin-de-Tarren"), "pierre de foyer")
for _, a in ipairs(engineAll) do ok(not a.text:find("|", 1, true), "pas de code | : " .. a.text) end

-- action Partager seulement chez le partageur, jamais en combat
local share = has(ref, "partage-la")
ok(share and share.action and share.action.by == "Alpha" and share.action.quest == 200, "action partager")

-- API publique
local S = clients.Charlie.S
ok(#S:Members() == 4 and S:Members()[1] == "Alpha", "Members()")
ok(S:State("Bravo") and S:State("Bravo").c == "HUNTER" and S:State("Bravo").b == 1, "State()")
ok(S:Leader() == "Alpha", "Leader()")
ok(S:Common()[1].id == 100 and #S:Common()[1].holders == 4, "Common()")
clients.Charlie.comm.SQS({ v = 2, c = "ROGUE", l = 20, z = "Elwynn|cffff0000\n" }, "Delta-Royaume")
ok(S:State("Delta").z == "Elwynn cffff0000", "zone nettoyée : " .. tostring(S:State("Delta").z))

-- compat Group.members (Quests.lua)
local gm = clients.Charlie.ns.Group.members
ok(gm.Bravo and gm.Bravo.level == 22 and gm.Bravo.quests[200] and gm.Bravo.quests[200].obj[1].have == 5, "Group.members alimenté")

-- débit : ≤ 1 envoi/s par client
for _, n in ipairs(NAMES) do
  local s = clients[n].T.sends
  local okRate = true
  for i = 2, #s do if s[i] - s[i - 1] < 1 then okRate = false end end
  ok(okRate and #s <= 40 and #s >= 3, n .. " débit (" .. #s .. " envois)")
end

-- sécurité : intrus, version inconnue, déchets, inondation
local C = clients.Charlie
C.comm.SQS({ v = 2, c = "MAGE", l = 30 }, "Intrus-Royaume")
ok(C.S:State("Intrus") == nil, "intrus ignoré")
C.comm.SQS({ v = 3, c = "MAGE", l = 59 }, "Bravo-Royaume")
ok(C.S:State("Bravo").l == 22, "version inconnue ignorée")
C.comm.SQS("garbage", "Bravo-Royaume")
C.comm.SQQ("9:1,1,,x,", "Bravo-Royaume")
ok(C.S:Quests("Bravo")[200] ~= nil, "quêtes v9 ignorées")
C.comm.SQS({ v = 2, c = "MAGE", l = 9999, h = -5, z = string.rep("é", 60) }, "Bravo-Royaume")
local st = C.S:State("Bravo")
ok(st.l == 80 and st.h == 0 and #st.z <= 40 and st.c == "MAGE", "bornes")
local accepted = 0
for i = 1, 30 do
  C.comm.SQS({ v = 2, c = "ROGUE", l = i }, "Delta-Royaume")
  if C.S:State("Delta").l == i then accepted = accepted + 1 end
end
ok(accepted < 12, "inondation limitée (" .. accepted .. " acceptés)")

-- moteur pur : ordre d'entrée indifférent, seul = rien
local E = C.S.Engine
local list = C.S:MemberList()
local rev = {}
for i = #list, 1, -1 do rev[#rev + 1] = list[i] end
local a1, a2 = E.Advise(list, {}), E.Advise(rev, {})
local same = #a1 == #a2
for i = 1, #a1 do same = same and a1[i].text == a2[i].text end
ok(same, "ordre d'entrée indifférent")
ok(#E.Advise({ list[1] }, {}) == 0, "seul : aucun conseil")
local off = E.Advise(list, { opts = { quests = false, xp = false, regroup = false, pull = false, loot = false, logistics = false, dungeons = false } })
ok(#off == 0, "tout désactivable")

-- conseil de jet
list = {
  { name = "Alpha", st = C.S.DecodeState({ v = 2, c = "WARRIOR", l = 22 }), quests = {} },
  { name = "Bravo", st = C.S.DecodeState({ v = 2, c = "HUNTER", l = 22 }), quests = {} },
  { name = "Charlie", st = C.S.DecodeState({ v = 2, c = "MAGE", l = 22 }), quests = {} },
  { name = "Delta", st = C.S.DecodeState({ v = 2, c = "ROGUE", l = 20 }), quests = {} },
}
local hint = E.LootHint({ name = "Arc long", classID = 2, subClassID = 2 }, list)
ok(hint and hint:find("Besoin Bravo", 1, true), "jet arc -> chasseur : " .. tostring(hint))
hint = E.LootHint({ name = "Haubert", classID = 4, subClassID = 3 }, list)
ok(hint and hint:find("Besoin Alpha", 1, true) and hint:find("maille", 1, true), "jet maille niv 22 -> guerrier : " .. tostring(hint))
hint = E.LootHint({ name = "Robe", classID = 4, subClassID = 1 }, list)
ok(hint and hint:find("Besoin Charlie", 1, true), "jet tissu -> mage")
ok(E.LootHint({ name = "Potion", classID = 0, subClassID = 1 }, list) == nil, "pas de conseil hors équipement")

-- codec
local enc = C.S.EncodeQuests({ { id = 5, level = 10, title = "A,b;c|d", done = true, group = true, dungeon = 2,
  obj = { { t = "item", have = 12, need = 10 } } } })
local dec = C.S.DecodeQuests(enc)[5]
ok(dec and dec.done and dec.group and dec.dungeon == 2 and dec.obj[1].have == 10 and dec.obj[1].t == "i"
  and not dec.title:find("[,;|]"), "codec quêtes : " .. enc)

-- instantané pour l'IA
C.S:SaveSnapshot()
local g = C.T.summary.group
ok(g and g.v == 2 and #g.members == 4 and #g.advices > 0 and g.leader == "Alpha" and g.common[1].holders == 4, "summary.group")

print(("RESULT %s squad checks=%d fails=%d"):format(fails == 0 and "ok" or "FAIL", checks, fails))
os.exit(fails == 0 and 0 or 1)
