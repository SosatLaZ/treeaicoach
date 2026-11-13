-- TreeAI — Sacs, les PRÉDICTEURS (agent BAGS, 5.1) : « vends » n'est dit que si tout besoin futur est écarté par des
-- données. Ce fichier complète Modules/Bags.lua (même module « Sacs », pas de nouveau module) : T.Bags.Predict.
-- Un prédicteur = une façon d'avoir besoin d'un objet, avec SA source (jamais inventée) et ce qu'il ne peut pas savoir :
--   quêtes du journal ......... Bags.lua (« quête ») : le journal du jeu + Surbrillance (nombre demandé exact)
--   quêtes à venir ............ ici (veto) : base de Questie LUE EN JEU (objet → quêtes), quêtes encore prenables par TOI
--                               (pas rendue — C_QuestLog.IsQuestFlaggedCompleted —, ta race / faction, ta classe, ton
--                               métier, pas exclue), de ton niveau à +8 → garde ; plus loin → banque ; objet qui LANCE
--                               une quête. Ne sait pas : le NOMBRE demandé (la base ne l'a pas → tout est gardé) ; sans
--                               Questie, ou tant que le lien objet → quêtes n'est pas vérifié : « à confirmer », jamais
--                               le clic unique.
--   métiers ................... ici : Data/Recipes (tables du client) — composant d'une recette qui donne encore des
--                               points à TON rang → gardé (le compte exact vient du plan de PROF : « Garde : 20 pour
--                               Travail du cuir (150→165) · 14 en trop ») ; « Pas besoin : sert en Couture (pas ton
--                               métier) » ; « … tu l'as dépassé » ; « … pas sur ton chemin » (plan connu qui ne s'en
--                               sert pas : à confirmer). Ne sait pas : les recettes hors des tables du client.
--   consommables sans catégorie ici : classe « consommable » du jeu hors de nos tables (élixirs, parchemins…) → gardés,
--                               en le disant (effet et stock utiles inconnus).
--   ami du groupe ............. ici (veto) : métiers partagés par les amis qui ont TreeAI (relevé du groupe) → « Donne ».
--   consommables / munitions / familier / composants de classe / outils, clés, sacs / recettes / équipement / marques :
--                               Bags.lua (Data/Consumables, Data/PetFood, arme à distance et familier lus en jeu).
-- Perf : rien sur un événement ; index des recettes construit une fois par petits morceaux (T:Spread) ; lectures de
-- Questie seulement pour un objet dont la vente est envisagée (mémoire partagée du cœur) ; la relecture complète de sa
-- base (objet → quêtes) ne part que sur TON clic (« Vérifier mes quêtes à venir »), par morceaux, hors combat, et son
-- résultat est gardé d'une session à l'autre. Lua 5.1, aucune globale, rien dans le chat.
local tonumber = function(v) return _G.tonumber(v) end   -- tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
local M = T and T.Bags
if not (type(M) == "table" and type(M._int) == "table") then return end
local I = M._int
local API = T.API or {}
local g, try, num, pub = I.g, I.try, I.num, I.pub
local fmt, tconcat, tsort = string.format, table.concat, table.sort
local floor, max, min, huge = math.floor, math.max, math.min, math.huge

local P = { stats = { reads = 0, scanned = 0, built = 0 } }
M.Predict = P

--- ce que chaque prédicteur lit, et ce qu'il ne peut pas savoir (documentation vivante : rapport, tests)
P.LIST = {
  { "marque", "tes choix « toujours garder / vendre / banque » et tes rachats (par personnage)", "rien : ton choix gagne" },
  { "quête", "journal du jeu + Surbrillance (objectifs, objets fournis, objet qui lance une quête)", "une quête que le jeu ne décrit pas par un objet" },
  { "quêtes à venir", "base de Questie lue en jeu, quêtes prenables de ton niveau à +8 (au-delà : banque)", "le nombre demandé ; rien sans Questie (alors : à confirmer)" },
  { "consommable", "Data/Consumables (client) : meilleur palier utilisable, stock cible par classe", "élixirs, parchemins : gardés sans stock cible" },
  { "munitions", "arme à distance équipée (jeu) + Data/Consumables", "une arme rangée à la banque" },
  { "familier", "régime et niveau du familier (jeu, sinon dernier régime vu) + Data/PetFood", "un familier jamais sorti" },
  { "composant", "liste fixe des composants de sorts par classe", "les composants ajoutés par un talent" },
  { "outil", "classe d'objet du jeu : clés, sacs, carquois, monture, mascotte, outils de métier, pierre de foyer", "—" },
  { "équipement", "Gear (stats lues, spé) : mieux / équivalent / portable d'ici 10 niveaux / sets / porté récemment", "les stats pas encore chargées (alors : gardé)" },
  { "recette", "Data/Recipes : objet-recette → métier et compétence ; infobulle « déjà connue »", "une recette absente des tables" },
  { "métiers", "Data/Recipes : composants des recettes ; plan de PROF pour le compte ; au-delà de ton rang max : gardé", "ce que tu fabriqueras hors du plan ; le compte après ton rang max" },
  { "consommable+", "classe « consommable » du jeu, hors de nos tables", "son effet, donc son stock utile" },
  { "ami du groupe", "métiers partagés par les amis qui ont TreeAI ; GroupGear pour l'équipement", "un ami sans TreeAI" },
}

-- ===================================================================== personnage (lu une fois ; imposable par un audit)
local RACE_BIT = { Human = 1, Orc = 2, Dwarf = 4, NightElf = 8, Scourge = 16, Undead = 16, Tauren = 32, Gnome = 64, Troll = 128 }
local SIDE_MASK = { Horde = 178, Alliance = 77 }
local CLASS_ID = { WARRIOR = 1, PALADIN = 2, HUNTER = 3, ROGUE = 4, PRIEST = 5, SHAMAN = 7, MAGE = 8, WARLOCK = 9, DRUID = 11 }
local OVR
--- un audit impose le personnage : { class, level, profs = { [ligne] = rang } | false, sig, raceBit, side, done = { [quête] = true },
--- log = { [quête] = true }, bank = { [objet] = n } } ; nil = le vrai personnage
function P.SetOverride(o)
  OVR = type(o) == "table" and o or nil
  P.override = OVR
  I.setOverride(OVR)
  P.me = nil
  if P.QF then P.QF.el = {} end
end
local function me()
  if OVR and (OVR.raceBit or OVR.side) then return OVR end
  if P.me then return P.me end
  local _, file, rid = try(g("UnitRace"), "player")
  file, rid = pub(file), num(rid)
  local bit = (rid and rid >= 1 and rid <= 8 and 2 ^ (rid - 1)) or (type(file) == "string" and RACE_BIT[file]) or nil
  local side = pub(try(g("UnitFactionGroup"), "player"))
  if type(side) ~= "string" or not SIDE_MASK[side] then side = nil end
  P.me = { raceBit = bit, side = side }
  return P.me
end
local function hasBit(mask, bit) return floor(mask / bit) % 2 == 1 end
--- la quête est-elle ouverte à ta race / ta faction ? (inconnu = oui : dans le doute on garde)
local function raceOK(mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local m = me()
  if m.raceBit then return hasBit(mask, m.raceBit) end
  local sm = m.side and SIDE_MASK[m.side]
  if not sm then return true end
  for b = 0, 7 do if hasBit(mask, 2 ^ b) and hasBit(sm, 2 ^ b) then return true end end
  return false
end
local function classOK(mask)
  mask = tonumber(mask) or 0
  if mask == 0 then return true end
  local cid = CLASS_ID[I.class() or ""]
  if not cid then return true end
  return hasBit(mask, 2 ^ (cid - 1))
end
--- niveau sous lequel une quête ne donne plus d'expérience (règle du jeu)
local function greyLevel(L)
  if L <= 5 then return 0 end
  if L <= 39 then return L - floor(L / 10) - 5 end
  if L <= 59 then return L - floor(L / 5) - 1 end
  return L - 9
end
P.GreyLevel = greyLevel

-- ===================================================================== base de Questie (lecture seule, en jeu)
local DB, dbAt = nil, -100
local function buildDB()
  local QM = API.QuestieMemo
  local lib = g("LibQuestieDB")
  if type(lib) == "table" then
    local ok = true
    if type(lib.RequireContract) == "function" then
      local a, b = pcall(lib.RequireContract, 4)
      ok = a and b ~= false and b ~= nil
    end
    local Qe = type(lib.Quest) == "table" and lib.Quest or g("QuestDB")
    local Ie = type(lib.Item) == "table" and lib.Item or g("ItemDB")
    if ok and type(Qe) == "table" and type(Qe.Get) == "function" then
      local function getter(E)
        if type(E) ~= "table" or type(E.Get) ~= "function" then return function() return nil end end
        return (QM and QM(E, "Get", E.Get)) or function(id, key) return try(E.Get, id, key) end
      end
      return { src = "LibQuestieDB", Q = getter(Qe), I = getter(Ie),
        raw = function(id, key) return try(Qe.Get, id, key) end,          -- relecture complète : hors de la mémoire partagée
        ids = function() local l = try(Qe.GetAllIds) return type(l) == "table" and l or {} end }
    end
  end
  local L = g("QuestieLoader")
  local mods = type(L) == "table" and rawget(L, "_modules") or nil
  local db = type(mods) == "table" and rawget(mods, "QuestieDB") or nil
  if type(db) == "table" and type(db.QueryQuestSingle) == "function" then
    local function getter(fn)
      if type(db[fn]) ~= "function" then return function() return nil end end
      return (QM and QM(db, fn, db[fn])) or function(id, key) return try(db[fn], id, key) end
    end
    return { src = "Questie", Q = getter("QueryQuestSingle"), I = getter("QueryItemSingle"),
      raw = function(id, key) return try(db.QueryQuestSingle, id, key) end,
      ids = function()
        local p = db.QuestPointers or db.questData
        local out = {}
        for id in pairs(type(p) == "table" and p or {}) do if tonumber(id) then out[#out + 1] = tonumber(id) end end
        tsort(out)
        return out
      end }
  end
  return false
end
--- la base de Questie ou nil (absente : re-cherchée au plus toutes les 15 s, Questie se charge après nous)
local function questDB()
  if DB then return DB end
  local t = I.now()
  if DB == false and t - dbAt < 15 and t >= dbAt then return nil end
  dbAt = t
  DB = buildDB()
  return DB or nil
end
P.QuestDB = questDB
--- (tests) la base a été remplacée : tout est relu
function P.ResetQuestie()
  DB, dbAt = nil, -100
  local QF = P.QF
  QF.by, QF.cal, QF.scan, QF.boot, QF.el, QF.src, QF.calN, QF.calMiss = nil, nil, nil, nil, {}, nil, nil, nil
  M.Invalidate(nil, "quest")
end

local QF = { el = {} }
P.QF = QF
local function spread(fn, ctx)
  if T.Spread then T:Spread(M, fn, nil, nil, nil, nil, ctx or "Bags:quêtes") else fn() end
end
local function questDone(qid)
  if OVR and OVR.done then return OVR.done[qid] and true or false end
  local CQ = g("C_QuestLog")
  local f = (type(CQ) == "table" and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  if type(f) ~= "function" then return false end           -- le jeu ne le dit pas : « pas faite » (donc gardé)
  return pub(try(f, qid)) and true or false
end
local logSet, logGen
local function logIds()
  if OVR and OVR.log then return OVR.log end
  local qg = T.QuestGen and T:QuestGen() or 0
  if logSet and logGen == qg then return logSet end
  logSet, logGen = {}, qg
  local Q = T.modules and T.modules.Quests
  for _, q in ipairs(type(Q) == "table" and type(Q.lastList) == "table" and Q.lastList or {}) do
    if type(q) == "table" and tonumber(q.id) then logSet[tonumber(q.id)] = true end
  end
  return logSet
end
--- signature de la LISTE des quêtes du journal (pas de leurs compteurs) : change quand une quête est prise / rendue / abandonnée
function P.LogSig()
  local n, sum = 0, 0
  for qid in pairs(logIds()) do n, sum = n + 1, sum + qid end
  return n * 1000003 + sum
end
local function onQuest(qid)
  if logIds()[qid] then return true end
  if OVR and OVR.log then return false end
  local CQ = g("C_QuestLog")
  if type(CQ) == "table" and type(CQ.IsOnQuest) == "function" then return pub(try(CQ.IsOnQuest, qid)) and true or false end
  return false
end
local function sigOf(ids)
  local n = type(ids) == "table" and #ids or 0
  local CA = g("C_AddOns")
  local ver = pub(try((type(CA) == "table" and CA.GetAddOnMetadata) or g("GetAddOnMetadata"), "Questie", "Version"))
  return fmt("%d:%s:%s:%s", n, tostring(ids[1]), tostring(ids[n]), tostring(ver or "?"))
end
local function stateChanged()
  QF.el = {}
  M.Invalidate(nil, "quest")
  if M.Refresh then M.Refresh() end
end
--- première demande : l'index gardé (s'il est encore celui de cette base), sinon le lien « objet → quêtes » de Questie
--- est VÉRIFIÉ sur de vraies quêtes (celles de ton journal, puis un échantillon) avant qu'on s'y fie
local function boot()
  local db = questDB()
  if not db then QF.boot = nil return end
  local saved = M.db and M.db.qidx
  local ids
  if type(saved) == "table" and type(saved.by) == "table" then
    ids = db.ids()
    if sigOf(ids) == saved.sig then
      QF.by, QF.src, QF.boot = saved.by, "index gardé", "done"
      return stateChanged()
    end
    M.db.qidx = false
  end
  local okN, miss = 0, 0
  local function check(qid, objs)
    for _, e in ipairs(type(objs) == "table" and type(objs[3]) == "table" and objs[3] or {}) do
      local item = tonumber(type(e) == "table" and e[1] or e)
      if item then
        local rel = db.I(item, "relatedQuests")
        local hit = false
        for _, q in ipairs(type(rel) == "table" and rel or {}) do if tonumber(q) == qid then hit = true break end end
        if hit then okN = okN + 1 else miss = miss + 1 end
      end
    end
  end
  for qid in pairs(logIds()) do if miss == 0 then check(qid, db.Q(qid, "objectives")) end end
  if miss == 0 and okN < 3 then
    ids = ids or db.ids()
    local n = #ids
    local step = max(1, floor(n / 16))
    for i = 1, n, step do
      check(ids[i], db.raw(ids[i], "objectives"))
      if miss > 0 then break end
    end
  end
  QF.calN, QF.calMiss = okN, miss
  if miss > 0 then QF.cal = false
  elseif okN >= 3 then QF.cal, QF.src = true, fmt("lien objet → quêtes de Questie, vérifié sur %d objectifs", okN)
  else QF.cal = nil end                                      -- pas assez de preuves : on redemandera (journal changé)
  QF.boot = "done"
  QF.bootAt = I.now()
  return stateChanged()
end
--- "absent" (pas de Questie) · "boot" (vérification en cours) · "scan" (relecture complète en cours) · "index" (objet →
--- quêtes relu en entier) · "fast" (lien de Questie vérifié) · "unverified" (Questie là, lien pas fiable : à confirmer)
function QF.State()
  if OVR and OVR.qstate then return OVR.qstate end
  local db = questDB()
  if not db then return "absent" end
  if QF.by then return "index" end
  if QF.scan then return "scan" end
  if not QF.boot then
    QF.boot = "run"
    spread(boot, "Bags:quêtes (vérification)")
  end
  if QF.boot ~= "done" then return "boot" end
  if QF.cal == true then return "fast" end
  return "unverified"
end
function P.QuestState() return QF.State() end
--- le journal a changé : les réponses par quête sont à refaire ; une vérification sans preuve est retentée (≤ 1 / 30 s)
function P.QuestsChanged()
  QF.el = {}
  if QF.boot == "done" and QF.cal == nil and not QF.by and not QF.scan and I.now() - (QF.bootAt or 0) > 30 then QF.boot = nil end
end
local SCAN_CHUNK = 20
--- LE clic « Vérifier mes quêtes à venir » : relit toute la base (objet → quêtes qui le demandent), 20 quêtes par
--- image, jamais en combat ; gardé pour les sessions suivantes (M.db.qidx, tant que la base de Questie est la même)
function P.QuestScan()
  local db = questDB()
  if not db or QF.scan or QF.by then return false end
  local ids = db.ids()
  if #ids == 0 then return false end
  local sc = { ids = ids, i = 0, by = {}, n = #ids, sig = sigOf(ids) }
  QF.scan = sc
  local function add(item, qid)
    item = tonumber(type(item) == "table" and item[1] or item)
    if not item or item <= 0 then return end
    local e = sc.by[item]
    if e == nil then sc.by[item] = qid
    elseif type(e) == "number" then if e ~= qid then sc.by[item] = e .. " " .. qid end
    elseif not (" " .. e .. " "):find(" " .. qid .. " ", 1, true) then sc.by[item] = e .. " " .. qid end
  end
  local function step()
    if QF.scan ~= sc then return end
    if I.inCombat() then I.after(2, function() spread(step) end) return end
    local stop = min(sc.n, sc.i + SCAN_CHUNK)
    for k = sc.i + 1, stop do
      local qid = sc.ids[k]
      local objs = db.raw(qid, "objectives")
      for _, e in ipairs(type(objs) == "table" and type(objs[3]) == "table" and objs[3] or {}) do add(e, qid) end
      for _, e in ipairs(db.raw(qid, "requiredSourceItems") or {}) do add(e, qid) end
    end
    P.stats.scanned = P.stats.scanned + (stop - sc.i)
    sc.i = stop
    if stop < sc.n then return spread(step) end
    QF.by, QF.scan, QF.src = sc.by, nil, fmt("base de Questie relue en entier (%d quêtes)", sc.n)
    if M.db then M.db.qidx = { sig = sc.sig, by = sc.by, n = sc.n } end
    stateChanged()
    if M.UpdateCard then M.UpdateCard() end
  end
  spread(step)
  stateChanged()
  return true
end
--- les quêtes liées à un objet : liste d'identifiants (+ .starter = celle qu'il lance) ; nil = on ne sait pas
local function relOf(db, id)
  local out, seen = {}, {}
  local function add(q, starter)
    q = tonumber(q)
    if q and q > 0 then
      if not seen[q] then seen[q] = true out[#out + 1] = q end
      if starter then out.starter = q end
    end
  end
  if QF.by then
    local e = QF.by[id]
    if type(e) == "number" then add(e)
    elseif type(e) == "string" then for q in e:gmatch("%d+") do add(q) end end
  elseif QF.cal == true then
    for _, q in ipairs(db.I(id, "relatedQuests") or {}) do add(q) end
  else
    return nil
  end
  local s = db.I(id, "startQuest")
  add(type(s) == "table" and s[1] or s, true)
  return out
end
--- cette quête peut-elle encore te demander quelque chose ? → { kind, rl, name }
---   kind : "log" (dans ton journal) · "now" (prenable de ton niveau à +8) · "later" (plus tard) · "done" · "notyours"
---          (race, faction, classe) · "noskill" (métier que tu n'as pas) · "excluded" (une quête exclusive est faite) ·
---          "grey" (trop basse : plus d'expérience)
local function eligOf(db, qid, L, profs)
  local c = QF.el[qid]
  if c and c.L == L then return c end
  local e = { qid = qid, L = L, name = db.Q(qid, "name") }
  local function done(kind) e.kind = kind QF.el[qid] = e return e end
  e.rl = tonumber(db.Q(qid, "requiredLevel")) or 0
  if onQuest(qid) then return done("log") end
  local rep = (tonumber(db.Q(qid, "specialFlags")) or 0) % 2 == 1          -- répétable (rendus de réputation, d'étoffes…)
  e.rep = rep
  if not rep and questDone(qid) then return done("done") end
  if not (raceOK(db.Q(qid, "requiredRaces")) and classOK(db.Q(qid, "requiredClasses"))) then return done("notyours") end
  local rs = db.Q(qid, "requiredSkill")
  local line = type(rs) == "table" and tonumber(rs[1]) or nil
  if line and line > 0 and profs and not profs[line] then return done("noskill") end
  for _, x in ipairs(type(db.Q(qid, "exclusiveTo")) == "table" and db.Q(qid, "exclusiveTo") or {}) do
    x = tonumber(x)
    if x and (questDone(x) or onQuest(x)) then return done("excluded") end
  end
  if e.rl > L + 8 then return done("later") end
  local ql = tonumber(db.Q(qid, "questLevel")) or 0
  if ql > 0 and ql <= greyLevel(L) and not rep then e.ql = ql return done("grey") end
  return done("now")
end
P.Eligible = function(qid) local db = questDB() if not db then return nil end return eligOf(db, qid, I.level(), I.profRanks()) end
local NOTE = { done = "déjà faite", notyours = "pas pour ta classe / ta faction", noskill = "demande un métier que tu n'as pas",
  excluded = "fermée par une autre quête", grey = "trop basse pour toi" }
local function qname(e) return I.cut(type(e.name) == "string" and e.name or ("n° " .. tostring(e.qid)), 30) end
--- un objet dont la vente est envisagée : une quête à venir le réclame-t-elle ? → { why, where } ou nil (écarté, noté)
local function questClaim(db, id, Q)
  local list = relOf(db, id)
  if not list then return nil, "quêtes à venir pas encore vérifiées" end
  if #list > 24 then return nil, fmt("%d quêtes liées : trop pour être sûr", #list) end
  local L, profs = I.level(), I.profRanks()
  local nowE, laterE
  local notes = 0
  for _, qid in ipairs(list) do
    local e = eligOf(db, qid, L, profs)
    if e.kind == "log" or e.kind == "now" then
      if not nowE or (e.kind == "log" and nowE.kind ~= "log") or (e.kind == nowE.kind and e.rl < nowE.rl) then nowE = e end
    elseif e.kind == "later" then
      if not laterE or e.rl < laterE.rl then laterE = e end
    elseif notes < 2 and Q then
      notes = notes + 1
      Q.reasons[#Q.reasons + 1] = fmt("quête « %s » : %s", qname(e), NOTE[e.kind] or e.kind)
    end
  end
  if nowE then
    if nowE.kind == "log" then return { why = fmt("quête « %s » de ton journal", qname(nowE)), keeper = "quête" } end
    if list.starter == nowE.qid then
      return { why = fmt("lance la quête « %s » (clic droit dessus)", qname(nowE)), where = nowE.rl <= L and "use" or nil, keeper = "quêtes à venir" }
    end
    local when = nowE.rl > L and fmt("dès le niveau %d", nowE.rl) or "pas encore prise"
    return { why = fmt("pour la quête%s « %s » (%s) — nombre demandé inconnu, je garde tout", nowE.rep and " répétable" or "", qname(nowE), when),
      keeper = "quêtes à venir" }
  end
  if laterE then
    return { why = fmt("pour la quête « %s » (niveau %d) : à la banque en attendant", qname(laterE), laterE.rl), where = "bank", keeper = "quêtes à venir" }
  end
  return nil
end
P.QuestClaim = function(id) local db = questDB() if not db then return nil, "absent" end return questClaim(db, id, nil) end

-- ===================================================================== métiers : index des composants (toutes les lignes)
-- Par composant, UNE chaîne compacte : « 165=170:100,150:60;197=95:40 » = par ligne de métier, l'escalier des recettes
-- qui s'en servent (gris décroissant : compétence pour l'apprendre, seulement quand elle baisse). C'est tout ce qu'il
-- faut pour répondre, à n'importe quel rang : une recette donne-t-elle encore des points (gris > rang) ? de quand à
-- quand ? — en ~60 Ko au lieu d'une table par recette (la simulation mesure la mémoire).
local RX = { by = nil, ready = false }
P.RX = RX
local function recipes() local D = T.Data and T.Data.Recipes return type(D) == "table" and type(D.r) == "table" and D or nil end
local ROWS_PER_STEP = 150
--- lit les recettes [from, to] d'une ligne dans tmp[composant] = { gris * 1000 + apprendre, … }
local function rxCollect(rows, from, to, tmp)
  for ri = from, to do
    local r = rows[ri]
    local mats = r[10]                                       -- (table générée : { compo, n, … }, gris et « apprendre » = nombres)
    local key = r[6] * 1000 + r[4]
    for k = 1, #mats, 2 do
      local e = tmp[mats[k]]
      if e then e[#e + 1] = key else tmp[mats[k]] = { key } end
    end
  end
end
--- ferme une ligne : l'escalier de chaque composant, ajouté à sa chaîne (au plus `budget` composants ; → true = fini)
local function rxClose(line, tmp, budget)
  local by = RX.by
  local mid, keys = next(tmp)
  local n = 0
  while mid do
    if budget and n >= budget then return false end
    n = n + 1
    tsort(keys)
    local parts, low = {}, nil
    for i = #keys, 1, -1 do
      local learn = keys[i] % 1000
      if not low or learn < low then
        low = learn
        parts[#parts + 1] = floor(keys[i] / 1000) .. ":" .. learn
      end
    end
    local seg = line .. "=" .. tconcat(parts, ",")
    by[mid] = by[mid] and (by[mid] .. ";" .. seg) or seg
    tmp[mid] = nil
    mid, keys = next(tmp)
  end
  P.stats.built = P.stats.built + 1
  return true
end
local function rxLines(D)
  local lines = {}
  for line in pairs(D.r) do lines[#lines + 1] = line end
  tsort(lines)
  return lines
end
--- construit l'index d'un coup (audits, tests) ; en jeu : RX.Start, par petits morceaux
function RX.BuildNow()
  local D = recipes()
  RX.by, RX.started = {}, true
  for _, line in ipairs(D and rxLines(D) or {}) do
    local tmp = {}
    rxCollect(D.r[line], 1, #D.r[line], tmp)
    rxClose(line, tmp)
  end
  RX.ready = true
end
local CLOSE_PER_STEP = 60
function RX.Start()
  if RX.started then return end
  RX.started = true
  local D = recipes()
  RX.by = {}
  if not D then RX.ready = true return end
  local lines, li, at, tmp = rxLines(D), 1, 0, {}
  -- une image = ≤ 150 recettes lues OU ≤ 60 composants rangés (≈ 0,3 ms), jamais les deux : ~40 images en tout, une
  -- fois par session (la simulation de session mesure la pire image)
  local function step()
    local rows = lines[li] and D.r[lines[li]]
    if rows and at < #rows then
      local to = min(#rows, at + ROWS_PER_STEP)
      rxCollect(rows, at + 1, to, tmp)
      at = to
    elseif rows then
      if rxClose(lines[li], tmp, CLOSE_PER_STEP) then li, at, tmp = li + 1, 0, {} end
    end
    if lines[li] then return spread(step, "Bags:recettes") end
    RX.ready = true
    M.Invalidate(nil, "prof")
    if M.Refresh then M.Refresh() end
  end
  spread(step, "Bags:recettes")
end
--- la chaîne des recettes qui consomment cet objet ; nil = aucune ; false = index pas prêt
function RX.Uses(id)
  if not RX.ready then RX.Start() if not RX.ready then return false end end
  return RX.by[id] or nil
end
--- pour un rang : cette ligne s'en sert-elle encore ? → vivant, dès, jusqu'à (gris le plus haut), apprendre au plus tôt
local function stairs(seg, rank)
  local top, from
  for grey, learn in seg:gmatch("(%d+):(%d+)") do
    grey, learn = tonumber(grey), tonumber(learn)
    top = top or grey
    if grey > rank then from = learn else break end
  end
  return from ~= nil, from and max(rank, from) or nil, top, from
end
--- ce que tes métiers font de cet objet : { live (une recette donne encore des points), liveLine, from, to, pastAt,
--- pastLine, mine = { noms }, other = { noms } } ; nil = pas un composant ; false = index pas prêt
local UC = { ranks = false, by = {} }                      -- réponses gardées tant que tes rangs sont les mêmes
local function profUse(id, ranks)
  local uses = RX.Uses(id)
  if not uses then return uses end
  if UC.ranks ~= (ranks or false) then UC.ranks, UC.by = ranks or false, {} end
  local c = UC.by[id]
  if c then return c end
  local u = { mine = {}, other = {} }
  UC.by[id] = u
  for line, seg in uses:gmatch("(%d+)=([^;]+)") do
    line = tonumber(line)
    local rank = ranks and ranks[line]
    local name = I.lineName(line)
    if rank then
      u.mine[#u.mine + 1] = name
      local live, from, top, learn = stairs(seg, rank)
      if live then
        -- une plage PAR métier (jamais le « dès » de l'un avec le « jusqu'à » de l'autre) ; affichée : d'abord celle qui
        -- sert dès maintenant (une recette apprenable à ton rang), puis celle qui va le plus loin
        local nowL = learn <= rank
        local better = not u.live or (nowL and not u.nowL) or (nowL == u.nowL and (top > u.to or (top == u.to and name < u.liveLine)))
        if better then
          u.live, u.liveLine, u.liveId, u.from, u.to, u.learn, u.nowL, u.rank = true, name, line, from, top, learn, nowL, rank
        end
      elseif not u.pastAt or (top or 0) > u.pastAt then
        u.pastAt, u.pastLine = top or 0, name
      end
    else
      u.other[#u.other + 1] = name
    end
  end
  if u.to then u.to = min(300, u.to) end
  tsort(u.mine)
  tsort(u.other)
  return u
end
P.ProfUse = function(id) return profUse(id, I.profRanks()) end

I.PRE["métiers"] = function(_, it) return it.q > 0 and it.cls ~= 12 end
I.builtin("métiers", function(id)
  local Q = M._ask
  local ranks = I.profRanks()
  local u = profUse(id, ranks)
  if u == false then Q.wait, Q.deps.prof = "je lis les recettes de métier", true return nil end
  if not u then return nil end
  Q.deps.prof = true                                        -- (seul un composant de recette dépend de tes métiers)
  if not ranks then
    -- tes métiers n'ont pas encore été lus (connexion) : un composant de métier ne se vend pas à l'aveugle
    I.EXT.profSilent = true
    return huge, "composant de métier (tes métiers ne sont pas encore lus)"
  end
  if u.live then
    local range = fmt("%s (%d à %d)", u.liveLine, u.from, u.to)
    Q.live = { range = range, line = u.liveLine, id = u.liveId, learn = u.learn, to = u.to, rank = u.rank }
    Q.phrase.Prof = "pour " .. range
    if I.EXT.prof then return nil end                       -- le plan de PROF donne le compte ; P.After tranche s'il se tait
    Q.phrase["métiers"] = "pour " .. range
    return huge, "sert à ton métier : " .. range
  end
  if #u.mine > 0 then
    local also = #u.other > 0 and fmt(", et en %s (pas ton métier)", tconcat(u.other, ", ", 1, min(2, #u.other))) or ""
    return 0, fmt("Pas besoin : sert en %s mais tu l'as dépassé (recettes grises depuis %d)%s", u.pastLine, u.pastAt or 0, also)
  end
  if #u.other > 0 then return 0, fmt("Pas besoin : sert en %s (pas ton métier)", tconcat(u.other, ", ", 1, min(2, #u.other))) end
end)

--- après tous les gardiens : un composant « vivant » (une de tes recettes donne encore des points) que le plan de PROF
--- ne réclame pas → plan connu : « pas sur ton chemin » (à confirmer, jamais le clic unique) ; plan pas prêt : gardé
function P.After(_, _, _, Q, EXT)
  local lv = Q.live
  if not (lv and EXT and EXT.prof) then return end
  local said = Q.said.Prof
  if said == "claim" then return end
  local PLv = T.Prof and T.Prof.Level
  local ready = type(PLv) == "table" and (tonumber(PLv.keepGen) or 0) > 0
  if ready or said == "opinion" then
    -- le plan de PROF s'arrête vers ton rang maximal actuel (75 / 150 / 225 / 300) : une recette qui donne encore des
    -- points APRÈS lui (rang suivant, chez le maître) n'y figure pas — elle est sur ton chemin quand même → gardé
    -- (banque si elle est encore loin) ; au-delà du rang suivant : avis « à confirmer » comme le reste
    local cap = lv.id and I.profCaps and I.profCaps()[lv.id]
    local rank = lv.rank or 0
    if cap and lv.to and lv.to > cap and (lv.learn or 0) <= cap + 75 then
      if not Q.all then Q.keep, Q.all = Q.keep + huge, true end
      if not Q.why then Q.why, Q.keeper = fmt("pour la suite de %s, au-delà de ton rang max (%d)", lv.range, cap), "métiers" end
      if (lv.learn or 0) > rank + 25 and Q.where == nil then Q.where = "bank" end
      return
    end
  end
  if said == "opinion" then
    -- PROF dit « en trop » alors qu'une recette à venir s'en sert : c'est dit, et le clic est à part
    Q.unsure = Q.unsure or fmt("une recette de %s s'en sert encore", lv.range)
    return
  end
  if ready then
    if not Q.opinion then
      Q.opinion = fmt("Pas besoin : sert en %s mais pas sur ton chemin (ton plan passe par d'autres recettes)", lv.range)
      Q.unsure = Q.unsure or "ton plan de métier ne s'en sert pas : à toi de voir"
    end
  elseif not Q.all then
    Q.keep, Q.all = Q.keep + huge, true
    if not Q.why then Q.why, Q.keeper = fmt("sert à ton métier : %s (plan pas encore calculé)", lv.range), "métiers" end
  end
end

-- ===================================================================== consommables hors de nos tables, coffres
local SUB_FR = { [1] = "potion", [2] = "élixir", [3] = "flacon", [4] = "parchemin", [5] = "nourriture", [7] = "bandage" }
I.PRE["consommable+"] = function(id, it)
  if it.cls ~= 0 or it.q == 0 then return false end
  local c, pf = I.cat(id)
  return c == nil and pf == nil
end
I.builtin("consommable+", function(_, it)
  local L = I.level()
  local kind = SUB_FR[it.sub or -1] or "consommable"
  if it.req > L + 5 then return huge, fmt("%s utilisable au niveau %d : à la banque en attendant", kind, it.req), "bank" end
  if it.req > L then return huge, fmt("%s utilisable au niveau %d", kind, it.req) end
  return huge, fmt("%s utilisable : je ne connais pas son effet, je garde", kind)
end, { "level" })
local LOCKBOX = { [4632] = true, [4633] = true, [4634] = true, [4636] = true, [4637] = true, [4638] = true, [5758] = true, [5759] = true,
  [5760] = true, [6354] = true, [6355] = true, [7209] = true, [12033] = true, [13875] = true, [13918] = true, [16882] = true,
  [16883] = true, [16884] = true, [16885] = true }
I.PRE["coffre"] = function(id) return LOCKBOX[id] ~= nil end
I.builtin("coffre", function() return huge, "coffre verrouillé : fais-le ouvrir (voleur, forgeron, ingénieur) avant de décider" end)

-- ===================================================================== ami du groupe (métier partagé par TreeAI)
local function friendUse(id)
  if (I.C.gn or 0) <= 0 then return nil end
  local uses = RX.Uses(id)
  if not uses then return nil end
  local Gm = I.mod("Group")
  if not (Gm and type(Gm.List) == "function") then return nil end
  local ok, list = pcall(Gm.List, Gm)
  for _, m in ipairs(ok and type(list) == "table" and list or {}) do
    local pr = type(m) == "table" and not m.me and type(m.st) == "table" and m.st.pr or nil
    if type(pr) == "string" and pr ~= "" then
      for line, seg in uses:gmatch("(%d+)=([^;]+)") do
        local name = I.lineName(tonumber(line))
        local rank = tonumber(pr:match(name:gsub("%p", "%%%0") .. " (%d+)"))
        if rank then
          -- une recette de SON métier lui donne encore des points, et il peut l'apprendre d'ici 25 points
          local live, _, _, learn = stairs(seg, rank)
          if live and learn <= rank + 25 then
            return fmt("%s peut s'en servir (%s %d) : donne-le-lui", I.cut(tostring(m.name or "un ami"), 14), name, rank)
          end
        end
      end
    end
  end
  return nil
end
P.FriendUse = friendUse

-- ===================================================================== le veto : avant de dire « vends »
--- → réclamation { why, where = nil | "bank" | "use" | "give", keeper } (l'objet est gardé), ou nil + ce qui n'est pas su
function P.Veto(id, it, _, Q, deps, _)
  deps.quest, deps.level, deps.roster = true, true, true
  local grey = it.q == 0
  local st = QF.State()
  local unsure
  if st == "index" or st == "fast" then
    local c, u = questClaim(questDB(), id, Q)
    if c then return c end
    if not grey then unsure = u end
  elseif not grey then
    -- un objet gris n'est jamais un objet de quête à venir connu ; pour tout le reste, sans données : à confirmer
    -- (sans Questie : l'option « vendre quand même d'un clic » est le choix du joueur, coupée d'office)
    if st == "absent" then unsure = (not I.optOn("noQuestie")) and "quêtes à venir inconnues (Questie absent)" or nil
    elseif st == "scan" then unsure = fmt("vérification des quêtes à venir en cours (%d %%)", floor(100 * QF.scan.i / max(1, QF.scan.n)))
    elseif st == "boot" then unsure = "vérification des quêtes à venir en cours"
    else unsure = "quêtes à venir pas encore vérifiées (carte du marchand : « Vérifier »)" end
  end
  local f = friendUse(id)
  if f then return { why = f, where = "give", keeper = "ami du groupe" } end
  return nil, unsure
end
