-- Scenario STATS : le centre de stats `TreeAI.Stats` (Modules/Stats.lua) sur le mock, événements simulés.
--  * taux de drop justes : a posteriori Beta (chiffres exacts), intervalle 80 %, monstres à tuer (attendu /
--    pessimiste), occasions = corps fouillés par toi, objet de quête compté seulement tant qu'il est utile,
--    même corps jamais compté 2 fois, dépeçage ignoré, butin de zone (plusieurs corps), a priori QuestieDB ;
--  * adoption : Predict.DropRate / BestSource, Tracker (« jusqu'à N si pas de chance »), Planner ;
--  * fiche (anti-rebond 1 s, rien en combat), temps de kill, XP/h, vitesse, quêtes, réparations, HV, onglet + export.
-- Noms factices. lua5.1 tests/wow/harness.lua --scenario tests/wow/stats_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local S = T.Stats
  check(S and S.db and S.db.enabled, "Stats chargé")
  if not S then return end
  M.LeaveCombat()
  local function near(a, b, eps) return type(a) == "number" and math.abs(a - b) <= (eps or 1e-6) end
  local saved = {}
  local function stub(name, fn) if saved[name] == nil then saved[name] = rawget(_G, name) or false end rawset(_G, name, fn) end

  -- ------------------------------------------------------------ a posteriori Beta : chiffres exacts
  local m, lo, hi, a, b = S.DropPosterior(3, 10)            -- a priori faible Beta(1,3) -> Beta(4,10)
  check(a == 4 and b == 10 and near(m, 4 / 14), "Beta(1,3) + 3/10 -> Beta(4,10), moyenne 0,2857 : " .. tostring(m))
  check(near(lo, 0.14161109987515277, 1e-7) and near(hi, 0.44426338192215414, 1e-7),
    "intervalle 80 % de Beta(4,10) = [0,14161 ; 0,44426] : " .. tostring(lo) .. " " .. tostring(hi))
  m, lo, hi = S.DropPosterior(0, 0)
  check(near(m, 0.25) and near(lo, 0.034510615394370246, 1e-7) and near(hi, 0.5358411166387221, 1e-7), "a priori seul Beta(1,3)")
  m, lo, hi = S.DropPosterior(0, 0, 0.35 * 8, 0.65 * 8)     -- a priori QuestieDB 35 % pesant 8 corps
  check(near(m, 0.35) and near(lo, 0.14988423116670846, 1e-7) and near(hi, 0.569080816035842, 1e-7), "a priori QuestieDB 35 %")
  m, lo, hi = S.DropPosterior(12, 20, 2.8, 5.2)
  check(near(m, 0.5285714285714286) and near(lo, 0.407942591839726, 1e-7) and near(hi, 0.6482808931533312, 1e-7),
    "QuestieDB 35 % + 12/20 mesurés -> 52,9 % [40,8 ; 64,8]")
  local e, p = S.KillsFor(3, 4 / 14, 0.14161109987515277)
  check(e == 11 and p == 22, "3 objets à 28,6 % : ~11 monstres, jusqu'à 22 : " .. tostring(e) .. "/" .. tostring(p))
  check(S.KillsFor(0, 0.5, 0.3) == nil and S.KillsFor(3, nil) == nil, "rien à faire / pas de taux : nil")
  check(near(S.ParseRate("35 %"), 0.35) and near(S.ParseRate(35), 0.35) and near(S.ParseRate(0.2), 0.2)
    and near(S.ParseRate({ "12.5" }), 0.125) and S.ParseRate(0) == nil and S.ParseRate("x") == nil, "taux QuestieDB lus (%, 0-1, texte, table)")

  -- ------------------------------------------------------------ occasions / réussites
  TreeAIDB.stats = nil
  local Q = T.modules.Quests
  local oldObj, oldList = Q and Q.ObjectiveFor, Q and Q.lastList
  local need = true
  if Q then
    Q.ObjectiveFor = function(name) if need and tostring(name):lower() == "patte de loup" then return {}, {} end end
    Q.lastList = { { title = "Les loups", complete = false, obj = { { name = "Patte de loup", t = "i", finished = false, have = 0, need = 10 } } } }
  end
  local function guid(npc, n) return ("Creature-0-1-2-3-%d-%08X"):format(npc, n) end
  local LINEN, PAW, HIDE = 2589, 884, 783
  local function corpse(npc, n, items, money)
    local loot = {}
    for _, it in ipairs(items or {}) do
      loot[#loot + 1] = { item = it[1], name = it[2], quest = it[3], cls = it[4] or 15, src = { guid(npc, n) } }
    end
    if money or #loot == 0 then loot[#loot + 1] = { src = { guid(npc, n) } } end
    return S.RecordCorpses(loot, money or #loot == 1 and not items[1])
  end
  -- objet hors quête (Tissu) sur le monstre 1131
  corpse(1131, 1, { { LINEN, "Tissu de lin" } })
  corpse(1131, 2, {}, true)
  corpse(1131, 3, { { LINEN, "Tissu de lin" } })
  local r = TreeAIDB.stats.drops[1131].i[LINEN]
  check(r[1] == 3 and r[2] == 2, "Tissu : 2 réussites sur 3 corps : " .. r[2] .. "/" .. r[1])
  check(corpse(1131, 3, { { LINEN, "Tissu de lin" } }) == 0 and r[1] == 3 and r[2] == 2, "même corps rouvert : pas compté 2 fois")
  check(S.RecordCorpses({ { item = HIDE, name = "Cuir léger", cls = 7, src = { guid(1131, 9) } } }, false) == 0 and r[1] == 3,
    "dépeçage (que du cuir) ignoré")
  check(S.RecordCorpses({ { item = LINEN, src = { "Player-1-0000ABCD" } } }, false) == 0, "pas un monstre : ignoré")
  -- butin de zone : 2 corps dans la même fenêtre
  check(S.RecordCorpses({ { item = LINEN, name = "Tissu de lin", src = { guid(1131, 4) } }, { src = { guid(1131, 5) } } }, true) == 2
    and r[1] == 5 and r[2] == 3, "butin de zone : 2 corps, 1 réussite : " .. r[2] .. "/" .. r[1])
  local local_o
  m, local_o = S:Drop(LINEN, 1131)
  check(near(m, (1 + 3) / (4 + 5)) and local_o == 5, "Drop(Tissu, 1131) = Beta(4,5) -> 44,4 % : " .. tostring(m))

  -- objet de quête (Patte de loup) sur le monstre 69 : corps en attente avant le 1er drop
  corpse(69, 1, {}, true)
  corpse(69, 2, {}, true)
  check(TreeAIDB.stats.drops[69].p and TreeAIDB.stats.drops[69].p["patte de loup"] == 2, "2 corps en attente (objet utile, jamais vu)")
  corpse(69, 3, { { PAW, "Patte de loup", true } })
  r = TreeAIDB.stats.drops[69].i[PAW]
  check(r[1] == 3 and r[2] == 1 and r[3] == 1, "1er drop : les 2 corps d'avant comptent : " .. r[2] .. "/" .. r[1])
  corpse(69, 4, {}, true)
  check(r[1] == 4, "objectif pas fini : corps sans patte = occasion ratée")
  need = false
  corpse(69, 5, {}, true)
  corpse(69, 6, {}, true)
  check(r[1] == 4 and r[2] == 1, "objectif fini : la patte ne tombe plus, pas d'occasion comptée : " .. r[2] .. "/" .. r[1])
  need = true
  -- groupe : un ami tue et fouille (on ne voit pas le corps) -> rien ; tu fouilles ton corps -> compté pour toi
  M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup des bois meurt, vous gagnez 45 points d'expérience.")
  check(r[1] == 4, "kill sans fouille (corps d'un ami) : pas une occasion")
  corpse(69, 7, { { PAW, "Patte de loup", true } })
  check(r[1] == 5 and r[2] == 2, "groupe : ton corps avec ta patte (tirage perso) = 2/5")
  m, local_o, lo, hi = S:Drop(PAW, 69)
  check(near(m, 3 / 9) and local_o == 5, "Drop(Patte, 69) = Beta(3,6) -> 33,3 % : " .. tostring(m))
  check(lo < m and m < hi, "intervalle autour de la moyenne")
  local ke, kp = S:KillsNeeded(3, PAW, 69)
  check(ke == 9 and kp > ke, "3 pattes : ~9 loups, plus si pas de chance : " .. tostring(ke) .. "/" .. tostring(kp))
  check(S:Drop(99999) == nil, "objet inconnu, pas de QuestieDB : nil (jamais un chiffre inventé)")

  -- a priori QuestieDB (si sa base donne un taux)
  local X = T.Export
  if X then
    local oldSrc = X.Source
    X.Source = function() return { drop = function(item, npc) if item == 4444 and npc == 70 then return "40%" end end } end
    m, local_o = S:Drop(4444, 70)
    check(near(m, 0.4) and local_o == 0, "aucun corps mais QuestieDB 40 % : 40 % (a priori seul)")
    X.Source = oldSrc
  end

  -- noms (Predict / Tracker / Highlight passent par là)
  TreeAIDB.stats.drops[69].nm = "Loup des bois"
  m = S:DropByName("Loup des bois", "Patte de loup")
  check(near(m, 3 / 9), "DropByName")
  local P = T.modules.Predict
  if P and P.DropRate then
    local pr, pn = P.DropRate("Loup des bois", "Patte de loup")
    check(near(pr, 3 / 9) and pn == 5, "Predict.DropRate -> Stats : " .. tostring(pr))
    local mob, br, bn, blo = P.BestSource("Patte de loup")
    check(mob == "Loup des bois" and near(br, 3 / 9) and bn == 5 and blo and blo < br, "Predict.BestSource -> Stats (+ borne basse)")
  end
  local TR = T.modules.Tracker
  if TR and TR.ObjEstimate then
    local est = TR.ObjEstimate({ name = "Patte de loup", have = 7, need = 10, t = "i" },
      { drops = { ["patte de loup"] = { mob = "Loup des bois", rate = 4 / 14, lo = 0.14161109987515277, measured = true, n = 10 } } })
    check(est.mobs == 11 and est.mobsMax == 22, "Tracker : ~11 loups, jusqu'à 22")
    local h = TR.ObjHint(est)
    check(h:find("encore ~11 × Loup des bois (29 %), jusqu'à 22 si pas de chance", 1, true) ~= nil, "Tracker : texte simple : " .. h)
  end
  if Q then Q.ObjectiveFor, Q.lastList = oldObj, oldList end

  -- ------------------------------------------------------------ fiche (anti-rebond, rien en combat)
  stub("UnitStat", function(_, i) return 0, ({ 40, 120, 80, 30, 45 })[i] end)
  stub("UnitAttackPower", function() return 100, 20, 0 end)
  stub("UnitRangedAttackPower", function() return 300, 30, -10 end)
  stub("GetCritChance", function() return 5.25 end)
  stub("GetRangedCritChance", function() return 8.2 end)
  stub("UnitArmor", function() return 900, 1200 end)
  stub("GetDodgeChance", function() return 6.1 end)
  stub("UnitRangedDamage", function() return 2.9, 40, 60 end)
  local reads = 0
  local oldRead = S.ReadSheet
  S.ReadSheet = function() reads = reads + 1 return oldRead() end
  for _ = 1, 6 do M.FireEvent("UNIT_AURA", "player") end
  M.FireEvent("UNIT_STATS", "player")
  M.Advance(1.5, 0.5)
  check(reads == 1, "rafale UNIT_AURA/UNIT_STATS -> 1 seule relecture : " .. reads)
  local sh = S:Sheet()
  check(sh.agi == 120 and sh.rap == 320 and sh.ap == 120 and near(sh.rcrit, 8.2) and sh.armor == 1200, "fiche lue")
  check(sh.rdmg and sh.rdmg[1] == 40 and sh.rdmg[2] == 60, "dégâts à distance")
  M.EnterCombat()
  M.FireEvent("UNIT_STATS", "player")
  M.Advance(1.5, 0.5)
  check(reads == 1, "en combat : pas de relecture")
  M.LeaveCombat()
  M.Advance(1.5, 0.5)
  check(reads == 2, "relue à la sortie du combat")
  S.ReadSheet = oldRead

  -- ------------------------------------------------------------ temps de kill, XP/h, vitesse
  M.target = { exists = true, name = "Ours-test", level = (M.player.level or 1) + 2, hostile = true, guid = guid(1186, 1),
    classification = "normal", health = 100, healthMax = 100 }
  local dm = S.Data().fight.dm
  dm.s, dm.n = 0, 0
  M.player.health = UnitHealthMax("player")
  for _ = 1, 3 do
    M.EnterCombat()
    M.Advance(1, 0.5)
    M.player.health = UnitHealthMax("player") * 0.7
    M.FireEvent("UNIT_HEALTH", "player")
    M.Advance(13, 1)
    M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Ours-test meurt, vous gagnez 80 points d'expérience.")
    M.LeaveCombat()
    M.player.health = UnitHealthMax("player")
    M.Advance(5, 1)
  end
  local kt, kn = S:KillTime(2)
  local grouped = (GetNumGroupMembers() or 0) > 1
  local raw = S.Data().kill[S.KillKey(2, "n", grouped)]
  check(raw and raw.n == 3 and near(raw.s / raw.n, 14, 0.6), "temps de kill +2 mesuré ~14 s")
  check(kn == 3 and near(kt, grouped and 17.5 or 14, 0.8), "KillTime(2) solo (groupe ×1,25 si mesuré en groupe) : " .. tostring(kt))
  check(S:KillTime(-4) == nil or T.Export, "écart sans mesure : nil (ou Export)")
  check(dm.n == 3 and near(dm.s / dm.n, 30, 0.01), "dégâts subis 30 % PV / combat (mesurés) : " .. dm.n .. " " .. dm.s)
  M.target = { exists = false }
  -- XP en quêtes : 12 min de jeu, une quête rendue, +12000 XP
  M.FireEvent("QUEST_ACCEPTED", 1, 4242)
  for _ = 1, 12 do
    M.player.xp = (M.player.xp or 0) + 1000
    M.FireEvent("PLAYER_XP_UPDATE", "player")
    M.FireEvent("QUEST_WATCH_UPDATE", 1)
    M.Advance(60, 5)
  end
  local xq = S:XPPerHour("questing")
  check(xq and xq > 50000 and xq < 70000, "XP/h en quêtes ~60k : " .. tostring(xq))
  M.FireEvent("QUEST_TURNED_IN", 4242, 500, 100)
  local qm = S:QuestMinutes()
  check(qm and qm > 11 and qm < 14, "durée de quête ~12 min : " .. tostring(qm))
  -- déplacements : 70 m en 10 s à pied
  local px = 0
  stub("UnitPosition", function() return 100, px, 0, 0 end)
  for _ = 1, 7 do
    M.FireEvent("PLAYER_STARTED_MOVING")
    M.Advance(10, 1)
    px = px + 70
    M.FireEvent("PLAYER_STOPPED_MOVING")
  end
  check(near(S:TravelSpeed(false), 7, 0.01) and S:TravelSpeed(true) == nil, "vitesse à pied 7 m/s mesurée, monté inconnue")
  -- bateau : continent 0 -> 1 en 5 min
  M.FireEvent("ZONE_CHANGED")
  M.Advance(300, 10)
  stub("UnitPosition", function() return 100, px, 0, 1 end)
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  local nTrans = 0
  for _, v in pairs(S.Data().trans) do nTrans = nTrans + 1 check(near(v.s / v.n, 300, 11), "trajet bateau ~300 s") end
  check(nTrans == 1, "1 trajet bateau / zeppelin")
  -- réparations : 1234 -> 0 chez le marchand
  local cost = 1234
  stub("GetRepairAllCost", function() return cost, true end)
  M.FireEvent("MERCHANT_SHOW")
  cost = 0
  M.FireEvent("UPDATE_INVENTORY_DURABILITY")
  M.FireEvent("MERCHANT_CLOSED")
  check(S.Data().rep == 1234, "réparation payée 12 pa 34 pc mesurée")
  -- HV : 3 mises en vente, 2 vendues, 1 expirée
  for _ = 1, 3 do S.NotePost("Fer-test") end
  S.NoteAuction("sell", "Fer-test", GetServerTime() + 3600)
  S.NoteAuction("sell", "Fer-test", GetServerTime() + 7200)
  S.NoteAuction("expired", "Fer-test")
  local rate, hrs, nAH = S:SaleRate("Fer-test")
  check(near(rate, 2 / 3) and nAH == 3 and hrs and hrs > 0.9 and hrs < 2.1, "HV : 67 % vendu, ~1,5 h")

  -- ------------------------------------------------------------ onglet « Stats » + export + Get
  S.Refresh(true)
  local lines = T:GetTabLines("stats") or {}
  local exp, txt = nil, {}
  for _, l in ipairs(lines) do txt[#txt + 1] = l.text if l.text == "Exporter pour TreeAI" then exp = l end end
  check(#lines >= 4 and #lines <= 9, "onglet Stats : 5-8 lignes + export : " .. #lines)
  for _, t in ipairs(txt) do
    local pl = T.PlainText and T.PlainText(t) or t
    check(not pl:find("nil", 1, true) and not pl:find("?", 1, true) and (not T.TextLen or T.TextLen(pl) <= T.LINE_MAX), "ligne propre : " .. pl)
  end
  check(exp and exp.onClick, "bouton « Exporter pour TreeAI »")
  if exp then exp.onClick() end
  local sm = T:Summary().stats
  check(type(sm) == "table" and sm.v == 1 and sm.sheet and sm.sheet.agi == 120 and sm.speed.w == 7, "summary.stats exporté")
  check(S:Get("sheet.agi") == 120 and S:Get("speed.w") == 7 and S:Get("nope.x") == nil, "Stats:Get(chemin)")

  -- ------------------------------------------------------------ taille bornée (≤ 60 Ko de SavedVariables)
  for npc = 1000, 1300 do
    for n = 1, 3 do
      local loot = {}
      for it = 1, 14 do loot[#loot + 1] = { item = 50000 + it * 7 + npc % 5, name = "Objet-" .. it, src = { guid(npc, n) } } end
      S.RecordCorpses(loot, true)
    end
  end
  for i = 1, 80 do S.NotePost("Objet-test-" .. i) S.NoteAuction("sell", "Objet-test-" .. i, GetServerTime()) end
  for i = 1, 60 do M.FireEvent("QUEST_ACCEPTED", 1, 9000 + i) end
  local function size(v, seen)
    local t = type(v)
    if t == "string" then return #v + 4 elseif t ~= "table" then return #tostring(v) + 2 end
    local n = 2
    for k, x in pairs(v) do n = n + size(k) + size(x) + 3 end
    return n
  end
  local bytes = size(TreeAIDB.stats) + size(S.Data())
  check(bytes < 60 * 1024, "SavedVariables Stats bornées : " .. bytes .. " o < 60 Ko")
  local nNpc = 0
  for _ in pairs(TreeAIDB.stats.drops) do nNpc = nNpc + 1 end
  check(nNpc <= 120, "≤ 120 monstres gardés : " .. nNpc)
  for name, v in pairs(saved) do rawset(_G, name, v or nil) end
end
