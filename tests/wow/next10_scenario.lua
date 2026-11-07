-- Scenario NEXT10 : modules Session (bilan, fin de session, pourquoi je meurs, pause) et Errands (courrier,
-- contre-remboursement / expiration, pierre de foyer ou vol, entraîneur, appel « prêt », limite d'instances).
-- Vérifie les textes (pas de « ? », de nil, ≤ 2 lignes), le silence quand il n'y a rien à dire, que chaque ligne
-- est cliquable, et qu'aucune fonction qui agirait à la place du joueur n'est appelée. Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/next10_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local S, E = T.modules and T.modules.Session, T.modules and T.modules.Errands
  check(S and S.db and S.db.enabled, "Session chargé")
  check(E and E.db and E.db.enabled, "Errands chargé")
  if not (S and E) then return end
  M.LeaveCombat()
  local BASE, oldTime = 1800000000, rawget(_G, "time")
  rawset(_G, "time", function() return math.floor(BASE + M.now) end)
  -- jamais appelées par l'addon (ce serait agir à la place du joueur)
  local forbidden, stubs = {}, {}
  local function stub(name, fn) if rawget(_G, name) == nil then stubs[#stubs + 1] = name end rawset(_G, name, fn) end
  for _, f in ipairs({ "TakeTaxiNode", "BuyTrainerService", "TakeInboxMoney", "TakeInboxItem", "AutoLootMailItem",
      "OpenAllMail", "ConfirmReadyCheck", "ResetInstances", "DeleteInboxItem" }) do
    stub(f, function() forbidden[#forbidden + 1] = f end)
  end
  local function sugg(id)
    for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == id then return e end end
  end
  local function find(tab, pat)
    for _, l in ipairs(T:GetTabLines(tab) or {}) do if tostring(l.text):find(pat, 1, true) then return l end end
  end
  local function clean(text, where)
    local p = T.PlainText(tostring(text or ""))
    check(not p:find("?", 1, true), where .. " sans « ? » : " .. p)
    check(not p:find("%f[%a]nil%f[%A]"), where .. " sans nil : " .. p)
    check(T.TextLen(p) <= T.LINE_MAX, where .. " ≤ 2 lignes : " .. p)
  end

  -- ---------------------------------------------------------------- fonctions pures
  check(S.DeathTrend({ { gap = 0 } }) == nil, "une seule mort : pas de tendance")
  local tx, tip, tab = S.DeathTrend({ { gap = 4, killer = "Ours-test" }, { gap = 3, killer = "Loup-test" } })
  check(tx and tx:find("3 niveaux", 1, true) and tab == "quests" and tip ~= "", "tendance écart de niveau : " .. tostring(tx))
  tx = S.DeathTrend({ { gap = 0, killer = "Ours-test" }, { gap = 1, killer = "Ours-test" }, { gap = 0, killer = "Loup-test" } })
  check(tx and tx:find("2 fois face à Ours-test", 1, true), "tendance même tueur : " .. tostring(tx))
  tx = S.DeathTrend({ { attackers = 4 }, { attackers = 3 } })
  check(tx and tx:find("3 monstres", 1, true), "tendance trop de monstres : " .. tostring(tx))
  local rt = S.RecapText({ start = 0, last = 3900, xp = 12000, quests = 9, money0 = 0, money = 24000, lv0 = 26, lv = 27 })
  check(rt == "Ma session : 1 h 05, +1 niveau, 9 quêtes, +2 po 40 pa", "bilan : " .. tostring(rt))
  check(not S.RecapText({ start = 0, last = 600, money0 = 50000, money = 10000, lv0 = 5, lv = 5 }):find("-", 1, true),
    "or dépensé jamais en négatif dans la ligne")

  local d = E.MailDigest({ { ah = true, money = 50000, items = 0, days = 29 }, { ah = true, money = 0, items = 1, days = 2 },
    { sender = "Bravo-test", cod = 50000, items = 1, days = 10 } })
  check(d.n == 3 and d.ah == 2 and d.ahMoney == 50000 and #d.cod == 1 and d.expiring == 1, "résumé du courrier")
  local ml = E.MailLines(d, true)
  check(#ml == 3 and ml[1].text == "2 lettres de l'HV, 5 po : clique « Tout ouvrir »", "ligne HV : " .. tostring(ml[1] and ml[1].text))
  check(ml[2].text:find("5 po demandés par Bravo-test", 1, true), "ligne contre-remboursement : " .. ml[2].text)
  check(ml[3].text == "1 lettre bientôt périmée (moins de 3 jours) : ouvre-la", "ligne expiration : " .. ml[3].text)
  check(#E.MailLines(E.MailDigest({}), true) == 0, "boîte vide : rien")

  local nodes = { { name = "Hurlevent, Elwynn", cost = 120 }, { name = "Moulin-de-Tarren, Hautebrande", cost = 4500 } }
  local nd = E.MatchBind(nodes, "Moulin-de-Tarren")
  check(nd and nd.cost == 4500, "nœud de vol de l'auberge trouvé")
  check(E.MatchBind(nodes, "Comté-de-l'Or") == nil, "pas de faux nœud")
  local ht, hp = E.HearthOrFly(0, nd, "Moulin-de-Tarren")
  check(hp == 2 and ht == "Pour Moulin-de-Tarren, ta pierre de foyer est prête : garde 45 pa", "foyer prêt : " .. tostring(ht))
  ht, hp = E.HearthOrFly(25 * 60, nd, "Moulin-de-Tarren")
  check(hp == 1 and ht == "Pierre de foyer dans 25 min : vole vers Moulin-de-Tarren pour 45 pa", "foyer en recharge : " .. tostring(ht))

  local tp = E.TrainerPlan({ { name = "Tir des arcanes", cost = 18000 }, { name = "Morsure de serpent", cost = 2000 },
    { name = "Marque du chasseur", cost = 3000 } }, 6000)
  check(tp == "Apprends d'abord Morsure de serpent, Marque du chasseur (50 pa), il manque 1 po 70 pa pour le reste",
    "entraîneur sans assez d'or : " .. tostring(tp))
  check(E.TrainerPlan({ { name = "X", cost = 100 } }, 500):find("apprends tout", 1, true), "entraîneur : assez d'or")
  check(E.TrainerPlan({}, 0, 28) == "Rien de neuf ici : prochains sorts au niveau 28", "entraîneur : rien de neuf")
  local k, wait = E.InstanceWindow({ 0, 600, 1200, 1800, 2400 }, 3000)
  check(k == 5 and wait == 600, "fenêtre d'instances : 5, place dans 10 min")
  check(select(1, E.InstanceWindow({ 0, 600 }, 5000)) == 0, "entrées de plus d'une heure oubliées")

  -- ---------------------------------------------------------------- session en jeu
  S.char.cur, S.char.prev = false, false
  S.StartOrResume()
  local s = S.Cur()
  check(s and s.start, "session ouverte")
  check(not find("info", "Ma session"), "carte cachée les 5 premières minutes")
  M.Advance(6 * 60, 5)
  M.player.money = (M.player.money or 0) + 25000
  ctx.fire("PLAYER_MONEY")
  ctx.fire("QUEST_COMPLETE")
  ctx.fire("QUEST_TURNED_IN", 9001, 1450, 0)
  M.player.xp = (M.player.xp or 0) + 500
  ctx.fire("PLAYER_XP_UPDATE", "player")
  M.Advance(2, 1)
  S.Update()
  local card = find("info", "Ma session")
  check(card and card.text:find("1 quête", 1, true) and card.text:find("+2 po 50 pa", 1, true), "carte : " .. tostring(card and card.text))
  check(card and type(card.onClick) == "function", "carte cliquable")
  if card then T:UserAction(card.onClick) end
  check(find("info", "XP gagnée") and find("info", "déconnecte-toi"), "détail ouvert au clic")
  if card then T:UserAction(find("info", "Ma session").onClick) end
  check(not find("info", "XP gagnée"), "détail refermé au 2e clic")
  -- morts : 3 en moins de 30 min -> une ligne « Maintenant » avec le conseil
  M.target = { exists = true, name = "Ours-test", level = M.player.level + 4, hostile = true, guid = "Creature-0-0-0-0-9-1" }
  for i = 1, 3 do
    S.OnDeath()
    M.Advance(60, 5)
  end
  S.Update()
  local dl = sugg("Session:deaths")
  check(dl and dl.prio == 2 and dl.text:find("en 30 min", 1, true) and type(dl.onClick) == "function", "pourquoi je meurs : " .. tostring(dl and dl.text))
  if dl then clean(dl.text, "morts") end
  check(find("info", "morts ce soir"), "tendance dans la carte")
  -- pause après 2 h
  M.Advance(2 * 3600, 30)
  S.Update()
  local pz = sugg("Session:pause")
  check(pz and pz.text:find("pause", 1, true) and type(pz.onClick) == "function", "rappel de pause")
  if pz then T:UserAction(pz.onClick) end
  check(not sugg("Session:pause"), "pause rangée au clic")
  -- reload rapide : même session ; longue absence : bilan gardé et montré
  local start = s.start
  S.StartOrResume()
  check(S.char.cur.start == start and not S.char.prev, "reload : même session")
  ctx.fire("PLAYER_LOGOUT")
  -- déconnecté 3 h (en vrai, rien ne tourne pendant ce temps)
  S.char.cur.last = time() - 3 * 3600
  S.char.cur.start = S.char.cur.last - 2 * 3600
  local _, fresh = S.StartOrResume()
  check(fresh and type(S.char.prev) == "table" and S.char.prev.quests == 1, "nouvelle session, ancienne gardée")
  S.Update()
  local pv = find("info", "Dernière session")
  check(pv and pv.onClick, "bilan de la dernière session affiché")
  if pv then clean(pv.text, "dernière session"); T:UserAction(pv.onClick) end
  check(not find("info", "Dernière session"), "rangé au clic")
  T:Summary()
  if S.WriteSummary then S.WriteSummary() end
  check(type(T:Summary().session) == "table", "résumé session pour TreeAI Coach")

  -- ---------------------------------------------------------------- courrier
  M.ah.mail = {}
  M.AddMail("Vente aux enchères réussie : Peau-test", 50000)
  M.AddMail("Une lettre", 0, nil, nil)
  M.ah.mail[2].daysLeft = 1.5
  M.ah.mail[2].item = { id = 2589, count = 1 }
  ctx.fire("MAIL_SHOW")
  ctx.fire("MAIL_INBOX_UPDATE")
  local ml1 = sugg("Errands:mail")
  check(ml1 and ml1.text:find("de l'HV", 1, true), "boîte : lettres d'HV : " .. tostring(ml1 and ml1.text))
  check(sugg("Errands:expire"), "boîte : lettre bientôt périmée")
  check(type(E.char.mail) == "table" and E.char.mail.expireAt, "échéance gardée")
  for _, id in ipairs({ "Errands:mail", "Errands:expire" }) do local e = sugg(id) if e then clean(e.text, id) end end
  T:UserAction(ml1.onClick)
  M.Advance(10, 1)
  check(not sugg("Errands:mail") and sugg("Errands:expire"), "ligne rangée au clic, pas remise par le rafraîchissement")
  ctx.fire("MAIL_CLOSED")
  check(not sugg("Errands:mail") and not sugg("Errands:expire"), "boîte fermée : lignes parties")
  E.MailReminder()
  check(sugg("Errands:expireLogin"), "rappel d'expiration au retour")

  -- ---------------------------------------------------------------- maître de vol
  rawset(_G, "GetBindLocation", function() return "Moulin-de-Tarren" end)
  stub("NumTaxiNodes", function() return 2 end)
  stub("TaxiNodeName", function(i) return nodes[i] and nodes[i].name end)
  stub("TaxiNodeGetType", function() return "REACHABLE" end)
  stub("TaxiNodeCost", function(i) return nodes[i] and nodes[i].cost end)
  M.AddItem(6948, { name = "Pierre de foyer" })
  local oldCount = T.API.ItemCount
  T.API.ItemCount = function(id, ...) if id == 6948 then return 1 end return oldCount(id, ...) end
  ctx.fire("TAXIMAP_OPENED", 1)
  local tx1 = sugg("Errands:taxi")
  check(tx1 and tx1.text:find("prête", 1, true) and tx1.prio == 2, "vol : pierre prête : " .. tostring(tx1 and tx1.text))
  ctx.fire("TAXIMAP_CLOSED")
  check(not sugg("Errands:taxi"), "carte de vol fermée : ligne partie")
  T.API.ItemCount = oldCount

  -- ---------------------------------------------------------------- entraîneur
  local services = { { "Tir des arcanes", "available", 18000 }, { "Morsure de serpent", "available", 2000 },
    { "Flèches multiples", "unavailable", 0, 30 } }
  stub("GetNumTrainerServices", function() return #services end)
  stub("GetTrainerServiceInfo", function(i) local v = services[i] return v[1], "Rang 2", v[2] end)
  stub("GetTrainerServiceCost", function(i) return services[i][3] end)
  stub("GetTrainerServiceLevelReq", function(i) return services[i][4] or 1 end)
  stub("IsTradeskillTrainer", function() return false end)
  M.player.money = 5000
  ctx.fire("TRAINER_SHOW")
  local tr = sugg("Errands:trainer")
  check(tr and tr.text:find("Morsure de serpent", 1, true) and tr.text:find("il manque", 1, true), "entraîneur : " .. tostring(tr and tr.text))
  if tr then clean(tr.text, "entraîneur") end
  ctx.fire("TRAINER_CLOSED")
  check(not sugg("Errands:trainer"), "entraîneur fermé : ligne partie")

  -- ---------------------------------------------------------------- appel « prêt »
  M.player.health = M.player.healthMax
  ctx.fire("READY_CHECK", "Bravo-test", 30)
  local rc = sugg("Errands:ready")
  if rc then clean(rc.text, "appel") end
  M.player.health = math.floor(M.player.healthMax * 0.3)
  ctx.fire("READY_CHECK", "Bravo-test", 30)
  rc = sugg("Errands:ready")
  check(rc and rc.text:find("PV 30 %", 1, true), "appel prêt : PV bas signalés : " .. tostring(rc and rc.text))
  ctx.fire("READY_CHECK_FINISHED")
  check(not sugg("Errands:ready"), "appel fini : ligne partie")
  M.player.health = M.player.healthMax

  -- ---------------------------------------------------------------- limite d'instances
  E.db.inst = {}
  local zones = { "Donjon-A", "Donjon-B", "Donjon-C" }
  for _, z in ipairs(zones) do
    M.player.zone = z
    M.instance = true
    ctx.fire("ZONE_CHANGED_NEW_AREA")
    M.Advance(300, 10)
    M.instance = false
    ctx.fire("ZONE_CHANGED_NEW_AREA")
  end
  local il = find("group", "Instances : 3 sur 5")
  check(il and il.onClick, "onglet Groupe : 3 instances sur 5")
  -- re-entrer dans la même instance ne compte pas
  M.player.zone = "Donjon-C"; M.instance = true; ctx.fire("ZONE_CHANGED_NEW_AREA"); M.instance = false; ctx.fire("ZONE_CHANGED_NEW_AREA")
  check(#E.db.inst == 3, "même instance : pas recomptée")
  for _, z in ipairs({ "Donjon-D", "Donjon-E" }) do
    M.player.zone = z; M.instance = true; ctx.fire("ZONE_CHANGED_NEW_AREA"); M.instance = false; ctx.fire("ZONE_CHANGED_NEW_AREA")
  end
  check(sugg("Errands:inst") and find("group", "prochaine entrée dans"), "limite atteinte signalée")
  M.player.zone = "Contreforts-test"

  check(#forbidden == 0, "aucune action à la place du joueur (" .. table.concat(forbidden, ", ") .. ")")
  for _, tabId in ipairs({ "info", "group", "now" }) do
    for _, l in ipairs(T:GetTabLines(tabId) or {}) do
      if l.module == "Session" or l.module == "Errands" or l.source == "Session" or l.source == "Errands" then
        clean(l.text, "onglet " .. tabId)
      end
    end
  end
  for _, name in ipairs(stubs) do rawset(_G, name, nil) end
  rawset(_G, "time", oldTime)
end
