-- Harness scenario (QUICK1) : module Quick « Raccourcis » — barre contextuelle (objet de quête, cible, partage,
-- vente/réparation, courrier, foyer, monture), automatisme de quêtes (accepter / rendre / récompense évidente),
-- dialogues sûrs, butin auto. Zéro action bloquée : cadres sécurisés jamais touchés en combat. Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/quick_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local X = T.Quick
  check(X and X.Engine, "module Quick chargé")
  if not (X and X.Engine) then return end
  local E = X.Engine
  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance, M.player.resting = false, false, false, false, false
  M.group = {}

  -- ------------------------------------------------------------- options : une case par réglage, rangées
  -- (4.0 QA) les automatismes de quête / dialogue ne s'allument jamais tout seuls : coupés, proposés une fois
  local AUTO = { autoQuest = true, autoReward = true, autoGossip = true }
  for _, o in ipairs(X.OPTIONS) do
    check(X.db[o.key] == (not AUTO[o.key]) and o.section and o.label, "option " .. o.key .. " = " .. tostring(X.db[o.key]))
  end
  check(X.db.autoV == 1, "migration unique faite")
  -- la question, une fois : une ligne « Accepter et rendre les quêtes tout seul ? » (clic = oui)
  if X.ProposeAuto then
    X.db.autoAsked = nil
    X.ProposeAuto()
    local q
    for _, e in ipairs(T:GetSuggestions()) do if e.id == X.AUTO_ID then q = e end end
    check(q and q.onClick and q.text:find("tout seul", 1, true), "question proposée une fois : " .. tostring(q and q.text))
    T:UserAction(q and q.onClick or function() end)
    check(X.db.autoQuest == true and X.db.autoReward == true and X.db.autoAsked == true, "« oui » : quêtes automatiques allumées")
    local conf, still
    for _, e in ipairs(T:GetSuggestions()) do
      if e.id == "Quick:auto" then conf = e end
      if e.id == X.AUTO_ID then still = e end
    end
    check(conf and conf.text:find("activées", 1, true) and not still, "ligne de confirmation, question retirée")
    X.ProposeAuto()
    still = nil
    for _, e in ipairs(T:GetSuggestions()) do if e.id == X.AUTO_ID then still = e end end
    check(not still, "jamais reposée")
    -- « Tout allumer » ne rallume pas les automatismes coupés
    X.db.autoQuest, X.db.autoReward = false, false
    T:SetModuleProfile("tout")
    check(X.db.autoQuest == false and X.db.autoReward == false and X.db.autoGossip == false, "« Tout allumer » : automatismes intacts")
  end
  X.db.autoQuest, X.db.autoReward, X.db.autoGossip = true, true, true   -- le joueur les a cochés : la suite les teste
  local fam = T.OPTIONS_FAMILY and T.OPTIONS_FAMILY[1]
  local inFam = false
  for _, n in ipairs(fam and fam.mods or {}) do if n == "Quick" then inFam = true end end
  check(inFam, "options : « Raccourcis » rangé dans « Quêtes et trajet »")
  check(T.MODULE_INFO.Quick and T.MODULE_INFO.Quick.lite, "profil essentiel : module léger")

  -- ------------------------------------------------------------- moteur pur
  check(E.PickReward({}) == 0, "aucun choix : rendre")
  check(E.PickReward({ { sell = 5 } }) == 1, "un seul choix : le prendre")
  local i, why = E.PickReward({ { usable = true, upgrade = 12, sell = 10 }, { usable = true, sell = 90 } }, true)
  check(i == 1 and why == "upgrade", "une seule amélioration : elle (" .. tostring(why) .. ")")
  i, why = E.PickReward({ { usable = true, upgrade = 12, sell = 10 }, { usable = true, upgrade = 5, sell = 90 } }, true)
  check(i == nil and why == "conflict", "deux améliorations : jamais de choix automatique")
  i, why = E.PickReward({ { usable = true, sell = 10 }, { usable = false, upgrade = 30, sell = 90 }, { sell = 40 } }, true)
  check(i == 2 and why == "sell", "aucune amélioration utilisable : la plus chère à la revente")
  i, why = E.PickReward({ { usable = true, equip = true, sell = 10 }, { sell = 40 } }, false)
  check(i == nil, "sans score d'équipement + objet portable : pas de choix")
  check(select(2, E.PickReward({ { unknown = true }, { sell = 1 } }, true)) == "unknown", "objet pas en cache : attendre")
  check(E.GossipPick({ { id = 7, icon = 132053 }, { id = 9, icon = 132057 } }).id == 9, "maître de vol : le vol")
  check(E.GossipPick({ { id = 3, icon = 132060 } }).id == 3, "option unique sûre (marchand)")
  check(E.GossipPick({ { id = 3, icon = 132053 } }) == nil, "option unique de discussion : jamais (escorte, événement)")
  check(E.GossipPick({ { id = 3, icon = 132060 } }, true) == nil, "PNJ avec quêtes : pas d'option automatique")
  local sl = E.ShareList({ { index = 2, id = 1 }, { index = 3, id = 2 }, { index = 4, id = 3, complete = true } },
    function(q) return q.id ~= 1 end, 2)
  check(#sl == 1 and sl[1].index == 2, "partage : seulement ce qu'un membre n'a pas, pas les quêtes finies")
  check(E.MobName("Ours enragé tué") == "Ours enragé" and E.MobName("Gnolls tués") == "Gnolls" and E.MobName("Sang de lion") == nil
    and E.MobName("Kobold", true) == "Kobold", "nom du monstre d'un objectif")
  check(E.TargetMacro("Ours enragé") == "/targetexact Ours enragé" and E.TargetMacro("a\n/cast X") == "/targetexact a/cast X"
    and E.TargetMacro("") == nil and E.TargetMacro(nil) == nil, "macro de ciblage propre")

  -- ------------------------------------------------------------- quêtes (fausses API de dialogue)
  local calls = {}
  local function stub(name, ret) rawset(_G, name, function(...) calls[#calls + 1] = { name, ... } return ret end) end
  local function called(name)
    for _, c in ipairs(calls) do if c[1] == name then return c end end
  end
  stub("AcceptQuest") stub("CompleteQuest") stub("GetQuestReward") stub("SelectActiveQuest") stub("SelectAvailableQuest")
  stub("TakeInboxMoney") stub("AutoLootMailItem") stub("QuestLogPushQuest")
  rawset(_G, "IsQuestCompletable", function() return true end)
  rawset(_G, "GetQuestMoneyToGet", function() return 0 end)
  rawset(_G, "QuestGetAutoAccept", function() return false end)
  local gossip = { active = {}, avail = {}, options = {} }
  rawset(_G, "C_GossipInfo", {
    GetActiveQuests = function() return gossip.active end,
    GetAvailableQuests = function() return gossip.avail end,
    GetOptions = function() return gossip.options end,
    SelectActiveQuest = function(id) calls[#calls + 1] = { "GI.SelectActiveQuest", id } end,
    SelectAvailableQuest = function(id) calls[#calls + 1] = { "GI.SelectAvailableQuest", id } end,
    SelectOption = function(id) calls[#calls + 1] = { "GI.SelectOption", id } end,
  })

  ctx.fire("QUEST_DETAIL")
  check(called("AcceptQuest"), "QUEST_DETAIL : quête acceptée")
  calls = {}
  M.shift = true
  ctx.fire("QUEST_DETAIL")
  check(not called("AcceptQuest"), "Maj enfoncée : pause")
  M.shift = false
  ctx.fire("QUEST_PROGRESS")
  check(called("CompleteQuest"), "QUEST_PROGRESS : rendue")
  calls = {}
  rawset(_G, "GetQuestMoneyToGet", function() return 500 end)
  ctx.fire("QUEST_PROGRESS")
  check(not called("CompleteQuest"), "quête payante : laissée au joueur")
  rawset(_G, "GetQuestMoneyToGet", function() return 0 end)

  -- récompenses : deux objets vendables sans amélioration -> le plus cher
  M.AddItem(9101, { name = "Bague terne", sellPrice = 50, classID = 15 })
  M.AddItem(9102, { name = "Pierre polie", sellPrice = 300, classID = 15 })
  M.questChoices = { 9101, 9102 }
  calls = {}
  ctx.fire("QUEST_COMPLETE")
  M.Advance(1)
  local r = called("GetQuestReward")
  check(r and r[2] == 2, "récompense : la plus chère à la revente (" .. tostring(r and r[2]) .. ")")
  -- deux améliorations (score Boost simulé) -> aucun choix
  local B = T.modules.Boost
  local oldUp, oldUse = B and B.UpgradePct, B and B.CanUse
  if B then
    B.UpgradePct = function() return 20 end
    B.CanUse = function() return true end
    M.AddItem(9103, { name = "Gants test", sellPrice = 10, equipLoc = "INVTYPE_HAND", classID = 4 })
    M.AddItem(9104, { name = "Bottes test", sellPrice = 12, equipLoc = "INVTYPE_FEET", classID = 4 })
    M.questChoices = { 9103, 9104 }
    calls = {}
    ctx.fire("QUEST_COMPLETE")
    M.Advance(2)
    check(not called("GetQuestReward") and X.lastReward == "conflict", "deux améliorations : le joueur choisit")
    B.UpgradePct = function(link) return tostring(link):find("9104") and 15 or 0 end
    calls = {}
    ctx.fire("QUEST_COMPLETE")
    M.Advance(1)
    r = called("GetQuestReward")
    check(r and r[2] == 2, "une seule amélioration : prise")
    B.UpgradePct, B.CanUse = oldUp, oldUse
  end
  M.questChoices = {}

  -- dialogues
  gossip.active = { { questID = 11, isComplete = false }, { questID = 12, isComplete = true } }
  gossip.avail = { { questID = 21, isTrivial = true }, { questID = 22 } }
  calls = {}
  ctx.fire("GOSSIP_SHOW")
  r = called("GI.SelectActiveQuest")
  check(r and r[2] == 12, "dialogue : quête finie choisie d'abord")
  gossip.active = {}
  calls = {}
  ctx.fire("GOSSIP_SHOW")
  r = called("GI.SelectAvailableQuest")
  check(r and r[2] == 22, "dialogue : nouvelle quête, pas la grise")
  gossip.avail = {}
  gossip.options = { { gossipOptionID = 5, icon = 132053, name = "Parler" }, { gossipOptionID = 6, icon = 132057, name = "Vol" } }
  calls = {}
  ctx.fire("GOSSIP_SHOW")
  r = called("GI.SelectOption")
  check(r and r[2] == 6, "maître de vol : carte des vols ouverte")
  gossip.options = { { gossipOptionID = 5, icon = 132053, name = "Je suis prêt" } }
  calls = {}
  ctx.fire("GOSSIP_SHOW")
  check(not called("GI.SelectOption"), "option de discussion seule : jamais automatique")
  X.db.autoQuest = false
  gossip.avail = { { questID = 22 } }
  calls = {}
  ctx.fire("QUEST_DETAIL") ctx.fire("GOSSIP_SHOW")
  check(not called("AcceptQuest") and not called("GI.SelectAvailableQuest"), "automatisme coupé : rien")
  X.db.autoQuest = true
  gossip.avail, gossip.options = {}, {}

  -- ------------------------------------------------------------- barre
  M.Advance(3)
  local bar = X.bar
  check(bar and bar:IsShown(), "barre affichée")
  local movers = T.GetMovables and T:GetMovables()
  -- (UIREDO) amarrée dans la barre « Raccourcis » : c'est elle qui se déplace (un seul cadre en mode édition)
  local Sc = T.Shortcuts
  local docked = Sc and Sc.Docked and Sc.Docked(bar)
  check(movers and ((movers.quick and movers.quick.frame == bar) or (docked and movers.shortcuts and not movers.quick
    and bar:GetParent() == movers.shortcuts.frame)), "barre déplaçable (seule ou dans « Raccourcis »)")
  local function shown(k) for _, s in ipairs(X.shown or {}) do if s == k then return true end end return false end
  check(shown("toggle"), "interrupteur de quêtes toujours là")
  check(not shown("sell") and not shown("mail"), "vente / courrier cachés hors marchand / boîte")

  -- objet de quête : porté par la barre, le bouton du module Quêtes se tait
  M.AddItem(9001, { name = "Totem de purification", classID = 12 })
  M.quests = {
    { header = "Contreforts de Hautebrande" },
    { title = "Sang de lion", level = 26, item = 9001, objectives = { { text = "Sang de lion : 2/10", done = false, have = 2, need = 10 } } },
    { title = "Les ours", level = 26, objectives = { { text = "Ours enragé tué : 3/8", done = false, have = 3, need = 8, type = "monster" } } },
  }
  local Q = T.modules.Quests
  if Q and Q.Update then Q.Update() end
  M.Advance(1)
  check(shown("item"), "bouton d'objet de quête dans la barre")
  local ib = X.buttons.item
  check(ib and ib:GetAttribute("type") == "item" and ib:GetAttribute("item") == "item:9001", "objet : attributs sécurisés")
  local qb = Q and Q.GetItemButton and Q.GetItemButton()
  check(not (qb and qb:IsShown()), "un seul bouton d'objet à l'écran")
  check(shown("target") and tostring(X.buttons.target:GetAttribute("macrotext")):find("^/targetexact "), "bouton de ciblage : "
    .. tostring(X.buttons.target and X.buttons.target:GetAttribute("macrotext")))

  -- foyer + monture (chasseur : guépard) s'il y en a
  M.AddItem(6948, { name = "Pierre de foyer", classID = 15 })
  M.SetBagItem(0, 16, 6948, 1)
  ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(1)
  check(shown("hearth") and X.buttons.hearth:GetAttribute("item") == "item:6948", "pierre de foyer")
  M.player.resting = true
  ctx.fire("PLAYER_UPDATE_RESTING")
  M.Advance(1)
  check(not shown("hearth"), "en ville (repos) : pas de foyer")
  M.player.resting = false
  ctx.fire("PLAYER_UPDATE_RESTING")
  M.Advance(1)

  -- combat : aucune modification de cadre sécurisé, rattrapée à la sortie
  local nb = #M.blocked
  M.EnterCombat()
  M.quests[2].item = nil
  if Q and Q.Update then Q.Update() end
  ctx.fire("BAG_UPDATE_DELAYED")
  M.Advance(2)
  check(#M.blocked == nb, "combat : rien de bloqué (" .. (#M.blocked - nb) .. ")")
  check(shown("item"), "combat : la barre ne change pas")
  M.LeaveCombat()
  M.Advance(1)
  check(not shown("item"), "après le combat : objet retiré")

  -- marchand : vente auto des gris + réparation, bouton visible tant qu'il reste quelque chose
  M.AddItem(9201, { name = "Fourrure abîmée", quality = 0, sellPrice = 25 })
  M.SetBagItem(1, 1, 9201, 3)
  M.SetBagItem(1, 2, 9201, 2)
  local sold, before = M.sold or 0, M.player.money
  M.SetMerchant(true)
  M.Advance(1)
  check((M.sold or 0) - sold == 2 and M.player.money > before, "marchand : gris vendus tout seuls")
  M.SetMerchant(false)
  M.Advance(1)
  X.db.autoSell = false
  M.SetBagItem(1, 1, 9201, 1)
  M.SetMerchant(true)
  M.Advance(1)
  check(shown("sell"), "vente manuelle : bouton visible")
  sold = M.sold or 0
  X.buttons.sell:Click()
  M.Advance(1)
  check((M.sold or 0) - sold == 1, "clic : gris vendu")
  M.SetMerchant(false)
  X.db.autoSell = true

  -- courrier : tout prendre, sauf contre-remboursement
  M.ah.mail = {}
  M.AddMail("Vente réussie", 5000)
  M.AddMail("Colis", 0, nil, { id = 9101, count = 1 })
  ctx.fire("MAIL_SHOW")
  M.Advance(1)
  check(shown("mail"), "boîte aux lettres : bouton visible")
  calls = {}
  X.buttons.mail:Click()
  M.Advance(2)
  check(called("TakeInboxMoney") and called("AutoLootMailItem"), "courrier : or et objets pris")
  ctx.fire("MAIL_CLOSED")

  -- groupe : partage d'un clic
  M.group = { { name = "Amie", class = "PRIEST", level = 26, online = true, quests = {} } }
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(1)
  check(shown("share"), "groupe : bouton de partage (" .. table.concat(X.shown, ",") .. ")")
  if shown("share") then
    calls = {}
    X.buttons.share:Click()
    M.Advance(3)
    check(called("QuestLogPushQuest"), "partage : QuestLogPushQuest appelé")
  end
  M.group = {}
  ctx.fire("GROUP_ROSTER_UPDATE")

  -- interrupteur
  X.buttons.toggle:Click()
  check(X.db.autoQuest == false, "clic : quêtes automatiques coupées")
  X.buttons.toggle:Click()
  check(X.db.autoQuest == true, "clic : réactivées")

  -- butin auto
  M.cvars.autoLootDefault = "0"
  X._lootHinted = false
  X.LootHint()
  local e = T._suggestions["Quick:loot"]
  check(e and e.text:find("^Active le butin automatique"), "rappel butin auto")
  if e and e.onClick then M.Hw(e.onClick) end
  check(M.cvars.autoLootDefault == "1", "clic : butin auto activé")

  -- cacher la barre : le module Quêtes reprend son bouton
  X.db.bar = false
  X:OnOptionChanged("bar")
  M.Advance(1)
  check(not bar:IsShown(), "barre cachée par l'option")
  X.db.bar = true
  X:OnOptionChanged("bar")
  M.Advance(1)
  check(#M.blocked == 0, "aucune action bloquée (" .. #M.blocked .. ")")
  for _, n in ipairs({ "AcceptQuest", "CompleteQuest", "GetQuestReward", "SelectActiveQuest", "SelectAvailableQuest",
    "TakeInboxMoney", "AutoLootMailItem", "QuestLogPushQuest", "IsQuestCompletable", "GetQuestMoneyToGet",
    "QuestGetAutoAccept", "C_GossipInfo" }) do rawset(_G, n, nil) end
end
