-- Harness scenario (CLEAN 4.0) : module « Avantages » (Edge2.lua) — 9 avantages qui reposent sur un FAIT du jeu
-- (fusion des anciens « Coups d'avance » / « Avantages », sans les devinettes ni les doublons). Chaque avantage :
-- déclencheur réel sur le mock, une ligne verbe d'abord, sans « ? », cachée quand elle ne sert à rien, coupable.
-- Onglet « Avantages ». Noms factices. lua5.1 tests/wow/harness.lua --scenario tests/wow/edge2_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X = T.Edge2
  check(X and X.Engine and #X.FEATURES == 9, "module Avantages chargé, 9 avantages")
  if not (X and X.Engine) then return end
  local E = X.Engine
  local S = T._suggestions
  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance = false, false, false, false
  M.group, M.player.level, M.player.resting = {}, 26, false
  T:PlayerState(true)
  T:ClearSuggestions()
  X.ResetSession()
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
  -- les devinettes et doublons ne reviennent pas
  for _, k in ipairs({ "bestMob", "patrolWarn", "nodeRoute", "drinkSmart", "buffExpire", "bigPull", "restPlan",
      "trainerPick", "classQuest", "dropCheap" }) do
    check(X.db[k] == nil, "retiré : " .. k)
  end

  -- ---------------------------------------------------------------- moteur pur
  local who, tp, tot = E.Contest({ { name = "Kobold-test", tapped = true }, { name = "Kobold-test", tapped = true },
    { name = "Kobold-test" }, { name = "Autre", tapped = true } }, { ["Kobold-test"] = true })
  check(who == "Kobold-test" and tp == 2 and tot == 3, "camp disputé : 2 tapés sur 3")
  check(math.abs(E.Streak(5, 0.5) - 1 / 32) < 1e-9 and E.Streak(5, nil) == nil, "probabilité d'une série sans drop")
  local tn, txp, enough = E.TurnIn({ { complete = true, xp = 900 }, { complete = true, xp = 700 }, { complete = false, xp = 5000 } }, 1500)
  check(tn == 2 and txp == 1600 and enough, "quêtes finies couvrent le niveau")
  check(E.IsEscort({ title = "Escorte-test", obj = {} }) and not E.IsEscort({ title = "Pattes", obj = {} }), "escorte reconnue")
  check(E.WorldBuff("Le seigneur Rend Main-noire est tombé !", "Thrall") == E.WORLD_BUFFS.rend, "cri : buff de Rend")
  check(E.WorldBuff("Bonjour", "Garde") == nil, "cri ordinaire : rien")

  -- ---------------------------------------------------------------- déclencheurs réels
  local Q = T.modules.Quests
  local savedList = Q.lastList
  local function setQuests(l) Q.lastList = l end
  local function kill(name) M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", name .. " meurt, vous gagnez 100 points d'expérience.") end

  -- corps pas fouillés (solo) : kills publiés par Detect (un seul lecteur du message d'XP)
  setQuests({})
  for _ = 1, 3 do kill("Loup-test") end
  X.CheckLoot()
  ok("lootLeft", "Fouille tes corps")
  M.FireEvent("LOOT_OPENED")
  none("lootLeft", "un corps fouillé")
  M.group = { { name = "Amie", class = "MAGE", level = 26, online = true } }
  for _ = 1, 4 do kill("Loup-test") end
  X.CheckLoot()
  none("lootLeft", "en groupe")
  M.group = {}

  -- malchance sur un objet de quête (taux mesuré par Stats)
  local St = T.Stats or T.modules.Stats
  local oD, oB = St.DropByName, St.BestSourceByName
  St.DropByName = function(_, mob, item) if item == "Patte-test" then return mob == "Loup-test" and 0.3 or 0.6 end end
  St.BestSourceByName = function() return "Ours-test", 0.6, 20, 0.5 end
  setQuests({ { id = 9102, title = "Pattes", obj = { { name = "Patte-test", have = 0, need = 5, finished = false, t = "i" } } } })
  X.QuestProgress()
  for _ = 1, 8 do kill("Loup-test") end
  X.CheckLuck()
  ok("dropLuck", "Tue plutôt des Ours-test")
  setQuests({ { id = 9102, title = "Pattes", obj = { { name = "Patte-test", have = 1, need = 5, finished = false, t = "i" } } } })
  X.QuestProgress()
  X.CheckLuck()
  none("dropLuck", "l'objet est tombé : série remise à zéro")
  St.DropByName, St.BestSourceByName = oD, oB

  -- camp disputé (barres de nom tapées par d'autres)
  setQuests({ { id = 9103, title = "Kobolds", obj = { { name = "Kobold-test tué", have = 2, need = 10, finished = false, t = "m" } } } })
  for i = 1, 4 do M.units["nameplate" .. i] = { exists = true, name = "Kobold-test", guid = "Creature-0-0-0-0-77-" .. i, hostile = true } end
  rawset(_G, "UnitIsTapDenied", function(u) return u == "nameplate1" or u == "nameplate2" or u == "nameplate3" end)
  X.CheckContest()
  ok("contestCamp", "Change de camp ou groupe-toi : 3 Kobold-test sur 4")
  rawset(_G, "UnitIsTapDenied", function() return false end)
  X.CheckContest()
  none("contestCamp", "plus personne ne tape")
  for i = 1, 4 do M.units["nameplate" .. i] = nil end

  -- quêtes finies = niveau d'un coup
  M.player.xp, M.player.xpMax = 25000, 26000
  setQuests({ { id = 9104, title = "Finie", complete = true, xp = 1500, obj = {} } })
  X.CheckTurnIn()
  ok("turninLevel", "Rends ta quête finie avant l'entraîneur : niveau 27")
  M.player.xp = 12000
  X.CheckTurnIn()
  none("turninLevel", "pas assez d'XP à rendre")

  -- quête chronométrée (minuteur réel du jeu)
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

  -- escorte échouée (drapeau « échouée » du journal)
  setQuests({ { id = 9106, title = "Escorte-test", failed = true, obj = { { name = "Protège le marchand", finished = false } } } })
  X.CheckEscort()
  ok("escortFail", "Abandonne puis reprends « Escorte-test »")
  setQuests({ { id = 9106, title = "Escorte-test", failed = false, obj = {} } })
  X.CheckEscort()
  none("escortFail", "escorte en cours")

  -- buff mondial : cri en capitale seulement
  local oz = M.player.zone
  M.player.zone = "Orgrimmar"
  M.FireEvent("CHAT_MSG_MONSTER_YELL", "Le seigneur Rend Main-noire est tombé ! Sa tête pend aux portes !", "Thrall")
  ok("worldBuff", "Reste là 20 s : buff « Bénédiction du chef de guerre »")
  T:ClearSuggestion("Edge2:worldBuff")
  M.player.zone = "Les Tarides"
  M.FireEvent("CHAT_MSG_MONSTER_YELL", "Le seigneur Rend Main-noire est tombé !", "Thrall")
  none("worldBuff", "hors capitale")
  M.player.zone = oz

  -- off-switch par avantage + onglet + aucune action bloquée
  X.db.lootLeft = false
  for _ = 1, 3 do kill("Loup-test") end
  X.CheckLoot()
  none("lootLeft", "option coupée")
  X.db.lootLeft = true
  T:ClearSuggestions("Edge2:")
  X.BuildTab(true)
  local tab = T:GetTabLines("adv")
  check(#tab >= 1 and tostring(tab[1].text):find("^Rien pour l'instant"), "onglet Avantages : état vide qui explique")
  for _, l in ipairs(tab) do
    local tx = tostring(l.text or "")
    check(not tx:find("?", 1, true) and not tx:find("nil", 1, true), "ligne propre : " .. tx)
  end
  setQuests(savedList)
  check(#M.blocked == 0, "aucune action bloquée (" .. #M.blocked .. ")")
end
