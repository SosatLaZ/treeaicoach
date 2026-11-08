-- Harness scenario (CHEAT2) : module Edge2 — 15 avantages légaux de plus (aucun doublon avec Edge / Hunter / Boost /
-- Inventory / Leveling / Squad). Chaque avantage : moteur pur + déclencheur réel sur le mock, une ligne verbe d'abord,
-- sans « ? », cachée quand elle ne sert à rien, coupable. Onglet « Avantages ». Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/edge2_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X = T.Edge2
  check(X and X.Engine and #X.FEATURES == 15, "module Edge2 chargé, 15 avantages")
  if not (X and X.Engine) then return end
  local E = X.Engine
  local S = T._suggestions
  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance = false, false, false, false
  M.group, M.player.level, M.player.resting = {}, 26, false
  T:PlayerState(true)
  T:ClearSuggestions()
  X.ResetSession()
  local cls = T:PlayerClass()
  local function line(k) local e = S["Edge2:" .. k] return e and e.text end
  local function ok(k, pat)
    local t = line(k)
    check(t and (not pat or t:find(pat, 1, true)) and not t:find("?", 1, true), k .. " : " .. tostring(t))
    local e = S["Edge2:" .. k]
    check(not e or e.tab == "adv", k .. " : rangée dans l'onglet Avantages")
    return t
  end
  local function none(k, why) check(line(k) == nil, k .. " caché : " .. why .. " (" .. tostring(line(k)) .. ")") end
  for _, f in ipairs(X.FEATURES) do check(X.db[f[1]] == true, "option " .. f[1]) end
  check(T.tabs and T.tabs.adv and T.tabs.adv.title == "Avantages", "onglet « Avantages » (menu Plus)")

  -- ---------------------------------------------------------------- moteur pur
  local n, xp = E.ParseKill("Loup-test meurt, vous gagnez 120 points d'expérience. (+60 exp. bonus de repos)")
  check(n == "Loup-test" and xp == 120, "kill + XP lus dans le message")
  local who, tp, tot = E.Contest({ { name = "Kobold-test", tapped = true }, { name = "Kobold-test", tapped = true },
    { name = "Kobold-test" }, { name = "Autre", tapped = true } }, { ["Kobold-test"] = true })
  check(who == "Kobold-test" and tp == 2 and tot == 3, "camp disputé : 2 tapés sur 3")
  local rates = E.MobRates({ A = { k = 4, x = 400, s = 80 }, B = { k = 4, x = 800, s = 80 }, C = { k = 1, x = 900, s = 5 } }, 10)
  check(#rates == 2 and rates[1].name == "B", "XP/h par monstre (≥ 3 kills)")
  local b, gain = E.BetterMob(rates, "A")
  check(b and b.name == "B" and gain > 0.9, "meilleur monstre : +" .. tostring(gain))
  check(E.BetterMob(rates, "B") == nil, "déjà le meilleur -> rien")
  local per, nx = E.Patrol({ 100, 160, 221, 280 }, 300)
  check(per == 60 and math.abs(nx - 40) < 1e-6, "patrouille : période 60 s, prochaine dans 40 s")
  check(E.Patrol({ 100, 101 }) == nil, "pas de patrouille sans 3 passages")
  check(math.abs(E.Streak(5, 0.5) - 1 / 32) < 1e-9 and E.Streak(5, nil) == nil, "probabilité d'une série sans drop")
  local nd, dd = E.NextNode({ { n = "Mageroyale", x = 0.5, y = 0.41, t = 0 }, { n = "Feuillargent", x = 0.52, y = 0.4, t = 0 },
    { n = "Récent", x = 0.505, y = 0.4, t = 990 } }, { x = 0.5, y = 0.4 }, 1000)
  check(nd and nd.n == "Feuillargent" and dd > 50, "prochain nœud : repoussé et pas sous tes pieds")
  check(E.DrinkCall(0.3, 0.9, 0.35, 0.2, true) == "drink" and E.DrinkCall(0.9, 0.9, 0.3, 0.2, true) == "chain"
    and E.DrinkCall(1, 1, 0.3, 0.2, true) == nil, "boire / enchaîner / rien")
  local ex = E.Expiring({ { name = "Fortitude", duration = 1800, expires = 1040 }, { name = "Aspect", duration = 0, expires = 0 },
    { name = "Court", duration = 30, expires = 1010 } }, 1000)
  check(#ex == 1 and ex[1].name == "Fortitude" and ex[1].left == 40, "buff long qui expire")
  local gp, full = E.RestGain(0, 10000, 8, true)
  check(gp == 5 and math.abs(full - 240) < 1e-6 and E.RestGain(0, 10000, 8, false) == 1, "repos : 5 % / 8 h à l'auberge")
  local tn, txp, enough = E.TurnIn({ { complete = true, xp = 900 }, { complete = true, xp = 700 }, { complete = false, xp = 5000 } }, 1500)
  check(tn == 2 and txp == 1600 and enough, "quêtes finies couvrent le niveau")
  local take, later = E.TrainerPick({ { name = "Pistage des bêtes", cost = 100, low = true }, { name = "Morsure de serpent", cost = 900, core = true },
    { name = "Aspect", cost = 500 } }, 1000)
  check(take and #take == 1 and take[1].name == "Morsure de serpent" and #later == 2, "maître : l'essentiel d'abord, le peu utile après")
  check(E.TrainerPick({ { name = "X", cost = 10 } }, 100) == nil, "assez d'or -> rien à trier")
  check(select(1, E.Classify("Morsure de serpent", "HUNTER")) and select(2, E.Classify("Pistage des humanoïdes", "HUNTER")), "sorts essentiels / peu utiles")
  check(E.ClassQuest("WARLOCK", 21, {}) == 20 and E.ClassQuest("WARLOCK", 21, { cq20 = true }) == nil and E.ClassQuest("MAGE", 20) == nil,
    "quête de classe par niveau")
  local ch = E.Cheapest({ { name = "Gemme", value = 5000, quality = 2 }, { name = "Os", value = 3 }, { name = "Patte", value = 1, quest = true },
    { name = "Épique", value = 0, quality = 4 } }, 3)
  check(#ch == 2 and ch[1].name == "Os", "à jeter : moins cher par case, jamais quête ni épique")

  -- ---------------------------------------------------------------- déclencheurs réels
  local Q = T.modules.Quests
  local savedList = Q.lastList
  local function setQuests(l) Q.lastList = l end

  -- 7 + 2. combats mesurés : boire / enchaîner, monstre le plus rentable
  local function fightKill(name, xpv, sec, hpEnd)
    M.player.health = 1000
    M.EnterCombat()
    M.Advance(sec, 1)
    M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", name .. " meurt, vous gagnez " .. xpv .. " points d'expérience.")
    M.player.health = hpEnd or 800
    M.LeaveCombat()
    M.Advance(1, 1)
  end
  setQuests({})
  for _ = 1, 4 do fightKill("Ours-test", 300, 10) end
  for _ = 1, 4 do fightKill("Loup-test", 100, 10) end
  ok("bestMob", "Tue plutôt des Ours-test")
  M.player.health = 1000
  local rows = X.Rates()
  check(rows[1] and rows[1].name == "Ours-test", "classement mesuré")
  fightKill("Loup-test", 100, 10, 150)
  ok("drinkSmart", "Mange / bois maintenant")
  fightKill("Loup-test", 100, 10, 900)
  ok("drinkSmart", "Enchaîne sans boire")
  -- objectif sur le loup : pas de conseil de changer
  setQuests({ { id = 9101, title = "Queues de loup", obj = { { name = "Loup-test tué", have = 1, need = 8, finished = false, t = "m" } } } })
  fightKill("Loup-test", 100, 10)
  none("bestMob", "monstre d'un objectif")

  -- 5. corps pas fouillés (solo)
  setQuests({})
  X.ResetSession()
  for _ = 1, 3 do M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup-test meurt, vous gagnez 100 points d'expérience.") end
  X.CheckLoot()
  ok("lootLeft", "Fouille tes corps")
  M.FireEvent("LOOT_OPENED")
  none("lootLeft", "un corps fouillé")
  M.group = { { name = "Amie", class = "MAGE", level = 26, online = true } }
  for _ = 1, 4 do M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup-test meurt, vous gagnez 100 points d'expérience.") end
  X.CheckLoot()
  none("lootLeft", "en groupe")
  M.group = {}

  -- 4. malchance sur un objet de quête (taux mesuré par Stats)
  local St = T.Stats or T.modules.Stats
  local oD, oB = St.DropByName, St.BestSourceByName
  St.DropByName = function(_, mob, item) if item == "Patte-test" then return mob == "Loup-test" and 0.3 or 0.6 end end
  St.BestSourceByName = function() return "Ours-test", 0.6, 20, 0.5 end
  setQuests({ { id = 9102, title = "Pattes", obj = { { name = "Patte-test", have = 0, need = 5, finished = false, t = "i" } } } })
  X.QuestProgress()
  for _ = 1, 8 do M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup-test meurt, vous gagnez 100 points d'expérience.") end
  X.CheckLuck()
  ok("dropLuck", "Tue plutôt des Ours-test")
  setQuests({ { id = 9102, title = "Pattes", obj = { { name = "Patte-test", have = 1, need = 5, finished = false, t = "i" } } } })
  X.QuestProgress()
  X.CheckLuck()
  none("dropLuck", "l'objet est tombé : série remise à zéro")
  St.DropByName, St.BestSourceByName = oD, oB

  -- 1. camp disputé (barres de nom tapées par d'autres)
  setQuests({ { id = 9103, title = "Kobolds", obj = { { name = "Kobold-test tué", have = 2, need = 10, finished = false, t = "m" } } } })
  for i = 1, 4 do M.units["nameplate" .. i] = { exists = true, name = "Kobold-test", guid = "Creature-0-0-0-0-77-" .. i, hostile = true } end
  rawset(_G, "UnitIsTapDenied", function(u) return u == "nameplate1" or u == "nameplate2" or u == "nameplate3" end)
  X.CheckContest()
  ok("contestCamp", "Change de camp ou groupe-toi : 3 Kobold-test sur 4")
  rawset(_G, "UnitIsTapDenied", function() return false end)
  X.CheckContest()
  none("contestCamp", "plus personne ne tape")
  for i = 1, 4 do M.units["nameplate" .. i] = nil end

  -- 3. patrouille d'élite : le même GUID revient toutes les 60 s
  M.units.nameplate5 = { exists = true, name = "Ogre-test", guid = "Creature-0-0-0-0-88-1", hostile = true, classification = "elite" }
  for i = 1, 4 do
    M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate5")
    M.Advance(10, 5)
    M.FireEvent("NAME_PLATE_UNIT_REMOVED", "nameplate5")
    if i < 4 then M.Advance(50, 10) end
  end
  M.Advance(35, 5)
  M.target = { exists = true, name = "Sanglier-test", level = 26, hostile = true, guid = "Creature-0-0-0-0-99-3" }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  ok("patrolWarn", "Attends avant de tirer : Ogre-test (élite) repasse dans ~15 s")
  M.FireEvent("NAME_PLATE_UNIT_ADDED", "nameplate5")
  X.CheckPatrol()
  ok("patrolWarn", "laisse-le passer")
  M.units.nameplate5 = nil

  -- 8. buff qui expire, 9. élite ciblé : gros sorts prêts
  M.auras.player = { { name = "Fortitude-test", duration = 1800, expirationTime = M.now + 40, kind = "buff" } }
  X.CheckBuffs()
  ok("buffExpire", "Renouvelle « Fortitude-test » avant de tirer")
  M.auras.player = {}
  X.CheckBuffs()
  none("buffExpire", "plus de buff court")
  local bigId = X.BIG[cls][1]
  M.spells[bigId] = { name = "Gros-sort-test", known = true }
  M.target = { exists = true, name = "Ogre-test", level = 27, hostile = true, classification = "elite", guid = "Creature-0-0-0-0-88-2" }
  X.CheckBig()
  ok("bigPull", "Garde Gros-sort-test pour cet élite")
  M.spellCooldowns[bigId] = { start = M.now, duration = 300 }
  local CS = rawget(_G, "C_Spell")
  local injected = not rawget(_G, "GetSpellCooldown") and CS and not CS.GetSpellCooldown   -- Forever : C_Spell seulement
  if injected then
    CS.GetSpellCooldown = function(id) local cd = M.spellCooldowns[id] return { startTime = cd and cd.start or 0, duration = cd and cd.duration or 0 } end
  end
  X.CheckBig()
  if injected then CS.GetSpellCooldown = nil end
  none("bigPull", "sort en recharge")
  M.spellCooldowns[bigId], M.spells[bigId] = nil, nil
  M.target = { exists = false }
  M.FireEvent("PLAYER_TARGET_CHANGED")

  -- 10. repos à l'auberge
  M.player.resting, M.player.rested, M.player.xpMax = true, 0, 26000
  T:PlayerState(true)
  X.CheckRest()
  ok("restPlan", "Déconnecte-toi ici")
  M.player.rested = 26000 * 1.45
  X.CheckRest()
  none("restPlan", "repos presque plein (Leveling le dit)")
  M.player.resting, M.player.rested = false, 5000
  T:PlayerState(true)

  -- 11. quêtes finies = niveau d'un coup
  M.player.xp, M.player.xpMax = 25000, 26000
  setQuests({ { id = 9104, title = "Finie", complete = true, xp = 1500, obj = {} } })
  X.CheckTurnIn()
  ok("turninLevel", "Rends ta quête finie avant l'entraîneur : niveau 27")
  M.player.xp = 12000
  X.CheckTurnIn()
  none("turninLevel", "pas assez d'XP à rendre")

  -- 12. maître de classe (or insuffisant)
  local svc = { { "Morsure de serpent", "Rang 3", 9000 }, { "Pistage des géants", "", 5000 }, { "Coup de raptor", "Rang 3", 8000 } }
  rawset(_G, "GetNumTrainerServices", function() return #svc end)
  rawset(_G, "GetTrainerServiceInfo", function(i) local s = svc[i] if s then return s[1], s[2], "available" end end)
  rawset(_G, "GetTrainerServiceCost", function(i) return svc[i] and svc[i][3] or 0 end)
  local oldMoney = M.player.money
  M.player.money = 10000
  M.FireEvent("TRAINER_SHOW")
  if cls == "HUNTER" then ok("trainerPick", "Apprends d'abord : Coup de raptor") else ok("trainerPick", "Apprends d'abord") end
  M.FireEvent("TRAINER_CLOSED")
  none("trainerPick", "maître fermé")
  M.player.money = 1000000
  X.CheckTrainer()
  none("trainerPick", "assez d'or pour tout")
  M.player.money = oldMoney
  for _, k in ipairs({ "GetNumTrainerServices", "GetTrainerServiceInfo", "GetTrainerServiceCost" }) do rawset(_G, k, nil) end

  -- 13. quête de classe (chasseur 10 / guerrier 10 et 30 / mage : aucune)
  M.player.level = 11
  X.CheckClassQuest()
  if X.CLASS_QUESTS[cls] then
    local t = ok("classQuest", "Fais ta quête de classe")
    local e = S["Edge2:classQuest"]
    if t and e and e.onClick then e.onClick() end
    none("classQuest", "clic = fait")
  else
    none("classQuest", "pas de quête de classe connue")
  end
  M.player.level = 26

  -- 14. quête chronométrée
  local CQ = rawget(_G, "C_QuestLog")
  local madeCQ = CQ == nil
  if madeCQ then CQ = {} rawset(_G, "C_QuestLog", CQ) end
  local oldTA = CQ.GetTimeAllowed
  CQ.GetTimeAllowed = function(id) if id == 9105 then return 1800, 1560 end end
  setQuests({ { id = 9105, title = "Course-test", complete = false, obj = {} } })
  X.CheckTimers()
  ok("questTimer", "Finis « Course-test » : chrono, il reste 4 min")
  CQ.GetTimeAllowed = oldTA
  if madeCQ then rawset(_G, "C_QuestLog", nil) end
  X.CheckTimers()
  none("questTimer", "plus de chrono")

  -- 15. quoi jeter : infobulle d'Inventory « Sacs pleins » + onglet
  M.AddItem(99001, { name = "Os-test", sellPrice = 2, quality = 0 })
  M.AddItem(99002, { name = "Gemme-test", sellPrice = 4000, quality = 2 })
  M.AddItem(99003, { name = "Patte-test", sellPrice = 0, classID = 12 })
  M.SetBagItem(0, 1, 99001, 3) M.SetBagItem(0, 2, 99002, 1) M.SetBagItem(0, 3, 99003, 1)
  M.FireEvent("BAG_UPDATE_DELAYED")
  local tip = X.DropTip()
  check(tip and tip[2]:find("Os-test", 1, true) == 1 and not tip[2]:find("Patte-test", 1, true), "à jeter : " .. tostring(tip and tip[2]))
  local Inv = T.modules.Inventory
  check(Inv and Inv.DropTip and Inv.DropTip() and Inv.DropTip()[2] == tip[2], "infobulle « Sacs pleins » d'Inventory = Edge2")
  X.db.dropCheap = false
  check(X.DropTip() == nil and Inv.DropTip() == nil, "coupé -> rien dans l'infobulle")
  X.db.dropCheap = true

  -- 6. boucle de récolte : prochain nœud repoussé (mémoire d'Economy)
  local Ec = T.modules.Economy
  if Ec and Ec.char and X.Engine then
    local saved = Ec.char.nodes
    Ec.char.nodes = { { n = "Feuillargent-test", m = M.pos.mapID, x = M.pos.x + 0.03, y = M.pos.y, t = 0 } }
    local castId = 2366
    M.spells[castId] = { name = "Herboristerie", known = true }
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", castId)
    M.Advance(1.5, 0.5)
    ok("nodeRoute", "Va au prochain nœud : Feuillargent-test")
    Ec.char.nodes = saved
    M.spells[castId] = nil
  end

  -- off-switch par avantage + onglet + combat
  X.db.lootLeft = false
  for _ = 1, 3 do M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup-test meurt, vous gagnez 100 points d'expérience.") end
  X.CheckLoot()
  none("lootLeft", "option coupée")
  X.db.lootLeft = true
  X.BuildTab(true)
  local tab = T:GetTabLines("adv")
  check(#tab >= 1, "onglet Avantages rempli (" .. #tab .. " lignes)")
  for _, l in ipairs(tab) do
    local tx = tostring(l.text or "")
    check(not tx:find("?", 1, true) and not tx:find("nil", 1, true), "ligne propre : " .. tx)
  end
  M.EnterCombat()
  check(line("drinkSmart") == nil and line("buffExpire") == nil and line("bigPull") == nil, "combat : conseils d'avant-pull retirés")
  M.LeaveCombat()
  setQuests(savedList)
  check(#M.blocked == 0, "aucune action bloquée (" .. #M.blocked .. ")")
end
