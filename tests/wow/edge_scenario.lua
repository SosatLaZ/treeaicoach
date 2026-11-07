-- Harness scenario (CHEAT25) : module Edge — 25 avantages légaux, chacun une ligne (verbe d'abord, sans « ? »,
-- ≤ 2 lignes) cachée tant qu'elle ne sert à rien, coupable une par une. Moteur pur + déclencheurs réels sur le
-- mock. Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/edge_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X = T.Edge
  check(X and X.Engine and #X.FEATURES == 25, "module Edge chargé, 25 avantages")
  if not (X and X.Engine) then return end
  local E = X.Engine
  local S = T._suggestions
  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance = false, false, false, false
  M.group, M.player.level, M.player.zone, M.player.resting = {}, 26, "Contreforts de Hautebrande", false
  T:PlayerState(true)
  T:ClearSuggestions()
  local function line(k) local e = S["Edge:" .. k] return e and e.text end
  local seen = {}
  local function ok(k, pat)
    local t = line(k)
    seen[k] = t
    check(t and (not pat or t:find(pat, 1, true)), k .. " : " .. tostring(t))
    return t
  end
  local function none(k, why) check(line(k) == nil, k .. " caché : " .. why .. " (" .. tostring(line(k)) .. ")") end

  -- chaque fonction a son interrupteur
  for _, f in ipairs(X.FEATURES) do check(X.db[f[1]] == true, "option " .. f[1]) end

  -- ---------------------------------------------------------------- moteur pur
  local h = E.GuildHelpers({ { name = "Alpha-Royaume", level = 27, zone = "Marais", online = true },
    { name = "Beta", level = 40, zone = "Marais", online = true }, { name = "Gamma", level = 26, zone = "Ailleurs", online = true },
    { name = "Delta", level = 25, zone = "Marais", online = false }, { name = "Testeur", level = 26, zone = "Marais", online = true } },
    "Marais", "Testeur", 26, 2)
  check(#h == 1 and h[1] == "Alpha", "guilde : même zone, en ligne, niveau proche, pas moi, royaume retiré")
  local rec
  for _, s in ipairs({ 300, 20, 310, 290, 5000 }) do rec = E.LearnRespawn(rec, s) end
  check(E.Respawn(rec) == 300, "repop : médiane des écarts valides (" .. tostring(E.Respawn(rec)) .. ")")
  check(E.Respawn(E.LearnRespawn(nil, 300)) == nil, "repop : pas avant 3 passages")
  check(E.NextRepop({ { t = 100 } }, 300, 330) == 70 and E.NextRepop({ { t = 100 } }, 300, 600) == nil, "prochain repop")
  local hostiles = {}
  for i = 1, 8 do hostiles[#hostiles + 1] = { id = 1, name = "Loup-test", lvl = 25, x = 0.40 + i / 100, y = 0.5 } end
  for i = 1, 10 do hostiles[#hostiles + 1] = { id = 2, name = "Ogre-test", lvl = 26, elite = true, x = 0.2, y = 0.2 + i / 100 } end
  for i = 1, 3 do hostiles[#hostiles + 1] = { id = 3, name = "Ours-test", lvl = 26, x = 0.7, y = 0.7 } end
  local pick = E.GrindPick(hostiles, 26)
  check(pick and pick.id == 1 and pick.n == 8 and math.abs(pick.x - 0.445) < 1e-6, "coin de farm : non élite, assez dense, centre")
  check(E.RestedFinish(4000, 2500, 400) == 5 and E.RestedFinish(6000, 2500, 400) == nil, "repos : finir le niveau à XP ×2")
  check(E.BuyForHour(11.2, 2) == 10 and E.BuyForHour(3, 2) == nil, "achat pour 1 h arrondi à 5")
  check(E.VendorFlip(100, 5000, 50) and not E.VendorFlip(4000, 5000, 50), "revente marchand -> HV")
  check(E.BoeWorth(120000, 4500) and not E.BoeWorth(5000, 4000), "lié quand équipé : HV vs marchand")
  check(select(1, E.GrowlRank(26)) == 3 and select(2, E.GrowlRank(26)) == 20 and E.GrowlRank(5) == nil, "rang de Grondement")
  local miss = E.MissingWeapons("HUNTER", { "Arcs", "Haches", "Défense" })
  check(miss and miss[1] == "Armes d'hast" and #miss == 6, "armes manquantes chasseur")
  check(E.MissingWeapons("HUNTER", { "Défense" }) == nil, "aucune arme visible -> rien (API absente)")
  check(E.IsEscort({ title = "Escorte de la caravane" }) and not E.IsEscort({ title = "Peaux de loup" }), "escorte détectée")
  check(E.WorldBuff("Les gens de Hurlevent ! Onyxia est vaincue !", "Héraut") and not E.WorldBuff("Bonjour", "Garde"), "cri de buff mondial")
  check(E.MobOf("Kobold tué : 3/10") == "Kobold", "monstre d'un objectif")
  check(X.Name("|cff00ff00Nom-Royaume|r") == "Nom" and X.Name("?") == nil and X.Name(nil) == nil, "noms propres, jamais « ? »")

  -- ---------------------------------------------------------------- déclencheurs réels
  -- 2. monstre déjà tapé
  M.target = { exists = true, name = "Sanglier-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-99-1" }
  rawset(_G, "UnitIsTapDenied", function(u) return u == "target" end)
  X.CheckTag()
  ok("tagRace", "Laisse ce monstre")
  rawset(_G, "UnitIsTapDenied", function() return false end)
  X.CheckTag()
  none("tagRace", "plus tapé")
  M.target = { exists = false }

  -- 3. repop appris d'un monstre de quête (3 morts + 3 réapparitions de 300 s)
  local Q = T.modules.Quests
  local saved = Q.lastList
  Q.lastList = { { id = 9001, title = "Queues de kobold", level = 26, zone = M.player.zone,
    obj = { { name = "Kobold-test tué", text = "Kobold-test tué : 2/10", finished = false, have = 2, need = 10 } } } }
  -- GUID dont le spawnUID porte l'heure d'apparition (23 bits bas = heure serveur mod 2^23)
  local seq = 0
  local function guid(npc, at)
    seq = seq + 1
    return ("Creature-0-1-0-1-%d-%010X"):format(npc, (seq * 8388608) + (math.floor(at) % 8388608))
  end
  local function srv() return GetServerTime() end
  check(E.SpawnTime(guid(5, srv() - 100), srv()) == srv() - 100, "heure d'apparition lue dans le GUID")
  check(E.SpawnTime("Creature-0-0-0-0-5-00000012", srv()) == nil and E.SpawnTime("Player-1-0ABC", srv()) == nil,
    "GUID sans heure fiable : rien (pas de faux chiffre)")
  -- monstre déjà là avant la mort (pas un repop) : rien appris
  X.RecordKill("Kobold-test")
  M.Advance(40, 20)
  check(X.PlateAdded("Kobold-test", guid(5, srv() - 600)) == nil, "monstre plus ancien que la mort : pas un repop")
  for i = 1, 3 do
    X.RecordKill("Kobold-test")
    M.Advance(320, 40)
    local dt = X.PlateAdded("Kobold-test", guid(5, srv() - 20))
    check(dt and math.abs(dt - 300) <= 1, "repop appris : " .. tostring(dt))
  end
  -- le dernier est encore visible : pas de « reste ici »
  X.RecordKill("Kobold-test")
  M.Advance(220, 50)
  Q.lastList = { { id = 9001, title = "Queues de kobold", level = 26, zone = M.player.zone,
    obj = { { name = "Kobold-test tué", text = "Kobold-test tué : 2/10", finished = false, have = 2, need = 10 } } } }
  X.CheckRepop()
  ok("respawnCamp", "Reste ici 1 min")
  -- 4. rare
  M.target = { exists = true, name = "Rare-test", hostile = true, classification = "rare", guid = guid(6, srv()) }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  for i = 1, 3 do
    X.RecordKill("Rare-test")
    M.Advance(620, 100)
    X.SeeAlive("Rare-test", guid(6, srv() - 20))
  end
  X.RecordKill("Rare-test")
  M.Advance(500, 100)
  X.CheckRepop()
  ok("namedRepop", "(rare) repop")
  M.target = { exists = false }

  -- 6. repos qui suffit pour finir le niveau
  M.player.xp, M.player.xpMax, M.player.rested = 24000, 26000, 5000
  local B = T.modules.Boost
  if B and B.char then B.char.xpKills = { 400, 400, 400 } end
  X.CheckRested()
  ok("restedWindow", "Tue 3 monstres")
  M.player.resting = true
  X.CheckRested()
  none("restedWindow", "à l'auberge")
  M.player.resting = false

  -- 22. escorte échouée -> abandonner et reprendre (clic = ouvrir, jamais abandonner)
  Q.lastList = { { id = 9002, title = "Escorte de la caravane", level = 26, zone = M.player.zone, failed = true, obj = {} } }
  X.CheckEscort()
  ok("escortReady", "Abandonne puis reprends")
  Q.lastList = { { id = 9003, title = "Peaux de loup", level = 26, zone = M.player.zone, obj = {} } }
  X.CheckEscort()
  none("escortReady", "pas d'escorte")

  -- 1. quête élite + guilde
  Q.lastList = { { id = 9004, title = "Le chef ogre", level = 27, zone = M.player.zone, elite = true, obj = {} } }
  rawset(_G, "GetNumGuildMembers", function() return 3, 3 end)
  local R = { { "Alpha-Royaume", 27, M.player.zone, true }, { "Beta", 27, "Ailleurs", true }, { "Testeur", 26, M.player.zone, true } }
  rawset(_G, "GetGuildRosterInfo", function(i) local r = R[i] if r then return r[1], "Membre", 1, r[2], "Mage", r[3], "", "", r[4] end end)
  X.ResetCaches()
  X.CheckEliteGuild()
  ok("eliteGuild", "Demande à Alpha")
  M.group = { { name = "B-test", class = "MAGE", level = 26, online = true }, { name = "C-test", class = "PRIEST", level = 26, online = true } }
  X.CheckEliteGuild()
  none("eliteGuild", "déjà en groupe")

  -- 21. ami en danger (monde ouvert, à portée)
  M.group[1].health, M.group[1].healthMax = 200, 1000
  X.CheckFriends()
  ok("friendDanger", "Aide B-test : 20 %")
  M.instance = true
  X.CheckFriends()
  none("friendDanger", "en instance (le soigneur gère)")
  M.instance = false
  M.group[1].health = 1000
  X.CheckFriends()
  none("friendDanger", "vie remontée")

  -- 13. donjon : tout le groupe a la quête
  Q.lastList = { { id = 9005, title = "Les défias", level = 20, zone = "Les Mortemines", dungeon = true, obj = {} } }
  local CQ = rawget(_G, "C_QuestLog")
  local hadCQ = CQ ~= nil
  if not CQ then CQ = {} rawset(_G, "C_QuestLog", CQ) end
  local prevOn = CQ.IsUnitOnQuest
  CQ.IsUnitOnQuest = function(a, b) local u = type(a) == "string" and a or b return u == "party1" or u == "party2" end
  X.CheckDungeon()
  ok("dungeonReady", "Lancez Les Mortemines : 3/3")
  CQ.IsUnitOnQuest = function(a, b) local u = type(a) == "string" and a or b return u == "party1" end
  X.CheckDungeon()
  none("dungeonReady", "un membre n'a pas la quête")
  CQ.IsUnitOnQuest = prevOn
  if not hadCQ then rawset(_G, "C_QuestLog", nil) end
  M.group = {}

  -- 14. points de dressage du familier (chasseur)
  if T:PlayerClass() == "HUNTER" then
    M.pet.tp = 45
    X.CheckPet()
    ok("petTrainer", "Dépense 45 points de dressage")
    M.pet.tp = 5
  end

  -- 17 + 20. or : maître de classe puis monture
  M.player.money = 100
  X.CheckTrainerSave()
  ok("trainerSave", "Garde ton or : maître de classe")
  M.player.money, M.player.level = 300000, 34
  X.CheckTrainerSave()
  none("trainerSave", "assez d'or")
  X.CheckMount()
  ok("mountSave", "Garde 30 po sur 90 po : monture au niv 40")
  M.player.money = 1000000
  X.CheckMount()
  none("mountSave", "objectif atteint")
  M.player.level = 26

  -- 19. double spé (40, en capitale, avec l'or)
  M.player.level, M.numTalentGroups, M.player.zone = 40, 1, "Orgrimmar"
  X.CheckDualSpec()
  ok("dualSpec", "Achète la double spé")
  local e = S["Edge:dualSpec"]
  if e and e.onClick then e.onClick() end
  X.CheckDualSpec()
  none("dualSpec", "clic = fait")
  -- 15. maître d'armes (capitale) : pas de compétence d'arme visible -> rien
  X.CheckWeapons()
  M.player.level, M.numTalentGroups, M.player.zone = 26, 2, "Contreforts de Hautebrande"

  -- 10. nourriture pour 1 h chez le marchand (taux mesurés par Predict)
  local P = T.modules.Predict
  local st = P and P.State and P.State()
  local food = T.Data.Consumables.food[2][1]
  if st then
    st.rests, st.foodUsed, st.waterUsed, st.restT = 4, 12, 0, {}
    for i = 4, 1, -1 do st.restT[#st.restT + 1] = { M.now - i * 600, 1 } end
    rawset(_G, "GetMerchantNumItems", function() return 1 end)
    rawset(_G, "GetMerchantItemInfo", function() return "Pain-test", 1, 25, 1, -1 end)
    rawset(_G, "GetMerchantItemID", function() return food end)
    X.CheckFood(X.MerchantItems())
    ok("foodBuy", "Achète")
    rawset(_G, "GetMerchantNumItems", function() return 0 end)
  end

  -- 12. butin lié quand équipé qui vaut cher
  local A = T.modules.Auction
  local prevStats = A and A.Stats
  if A then
    A.Stats = function(id) if id == 4242 then return { market = 120000, conf = 0.6 } end end
    M.AddItem(4242, { name = "Épée-test", quality = 2, sellPrice = 4500, bindType = 2, equipLoc = "INVTYPE_WEAPON" })
    X.OnLoot("Vous recevez le butin : " .. M.ItemLink(4242) .. ".")
    ok("boeWorth", "Vends « Épée-test » à l'HV")
    -- 11. objet du marchand qui se revend plus cher à l'HV (stock limité)
    A.Stats = function(id) if id == 4343 then return { market = 9000, conf = 0.6 } end end
    M.AddItem(4343, { name = "Plan-test", quality = 2, sellPrice = 100 })
    rawset(_G, "GetMerchantNumItems", function() return 1 end)
    rawset(_G, "GetMerchantItemInfo", function() return "Plan-test", 1, 1500, 1, 2 end)
    rawset(_G, "GetMerchantItemID", function() return 4343 end)
    X.CheckFlip(X.MerchantItems())
    ok("vendorFlip", "Achète 2 × Plan-test ici (15 pa)")
    rawset(_G, "GetMerchantNumItems", function() return 0 end)
    A.Stats = prevStats
  end
  -- 18. amélioration pas chère à l'HV (plan de Gear)
  local Gm = T.modules.Gear
  if Gm then
    local prevPlan = Gm.plan
    Gm.plan = { shopping = { { kind = "ah", id = 4444, name = "Casque-test", cost = 12000, pct = 11.6 } } }
    M.player.money = 50000
    X.CheckCheapGear()
    ok("cheapGear", "Achète « Casque-test » à l'HV (~1 po 20 pa) : +12 %")
    M.player.money = 100
    X.CheckCheapGear()
    none("cheapGear", "pas les moyens")
    Gm.plan = prevPlan
  end

  -- 23. buff mondial en capitale seulement
  X.OnYell("Onyxia est vaincue ! Admirez sa tête !", "Héraut")
  none("worldBuff", "hors capitale")
  M.player.zone = "Hurlevent"
  X.OnYell("Onyxia est vaincue ! Admirez sa tête !", "Héraut")
  ok("worldBuff", "Reste là 20 s")
  M.player.zone = "Contreforts de Hautebrande"

  -- chaque avantage coupé = plus rien (et sa ligne retirée tout de suite)
  T:SetModuleEnabled("Edge", true)
  X.db.tagRace = false
  M.target = { exists = true, name = "Sanglier-test", hostile = true }
  rawset(_G, "UnitIsTapDenied", function(u) return u == "target" end)
  X.CheckTag()
  none("tagRace", "option coupée")
  X.db.tagRace = true
  rawset(_G, "UnitIsTapDenied", function() return false end)
  M.target = { exists = false }

  -- état du joueur : fantôme = conseils en attente (règle commune Core)
  M.player.ghost = true
  T:PlayerState(true)
  local hidden = true
  for _, l in ipairs(T:GetTabLines("now")) do if tostring(l.id or ""):find("^Edge:") and l.id ~= "Edge:friendDanger" then hidden = false end end
  check(hidden, "fantôme : aucun avantage dans « Maintenant »")
  M.player.ghost = false
  T:PlayerState(true)

  -- robustesse : chaque ligne vue = verbe d'abord, sans « ? » ni « nil », ≤ LINE_MAX
  local n = 0
  for k, t in pairs(seen) do
    if t then
      n = n + 1
      local plain = T.PlainText(t)
      check(not plain:find("?", 1, true) and not plain:find("nil", 1, true), k .. " sans « ? » / nil : " .. plain)
      check(T.TextLen(plain) <= T.LINE_MAX, k .. " tient sur 2 lignes : " .. plain)
      check(plain:find("^%u") ~= nil or plain:find("^[\195]") ~= nil, k .. " commence par un verbe : " .. plain)
    end
  end
  check(n >= 16, "au moins 15 avantages vus en action (" .. n .. ")")
  M.RunSlash("/tai avance")
  for _, k in ipairs({ "GetGuildRosterInfo", "GetNumGuildMembers", "GetMerchantItemID", "GetMerchantItemInfo" }) do rawset(_G, k, nil) end
  Q.lastList = saved
  T:ClearSuggestions("Edge:")
end
