-- Harness scenario (CLEAN 4.0) : ce qui reste après le grand ménage dit la vérité du jeu, une fois, au bon endroit.
-- Combat (pourquoi je suis mort, avant le pull), Chasseur (fiche, munitions, aspects, pièges, Feindre la mort),
-- Statistiques (XP/h avec sa durée de mesure), Équipement (des mots, pas des %), Inventaire (nourriture),
-- Guichets (prix réel du maître de classe), Ma session (monture), Progression (repos = info), Touches (nouveaux
-- sorts), cœur (ralenti auto). Noms factices. lua5.1 tests/wow/harness.lua --scenario tests/wow/clean_scenario.lua …
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local S = T._suggestions
  local cls = T:PlayerClass()
  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance = false, false, false, false
  M.group = {}
  T:PlayerState(true)
  local function sug(id) local e = S[id] return e and e.text end
  local function tabText(id)
    local t = {}
    for _, l in ipairs(T:GetTabLines(id) or {}) do t[#t + 1] = tostring(l.text or "") end
    return table.concat(t, "\n")
  end

  -- ------------------------------------------------------------------ modules retirés : plus là, plus listés
  for _, n in ipairs({ "SafePath", "HunterPro", "Comfort", "Economy", "Predict", "Edge" }) do
    check(T.modules[n] == nil and (T.MODULE_INFO[n] == nil), "module retiré : " .. n)
  end

  -- ------------------------------------------------------------------ Combat : fonctions pures
  local F = T.modules.Fight
  check(F and F.title == "Combat", "module Combat")
  local bad = F.PrePullReasons({ hp = 40, mana = 30, petHp = 20, hunter = true }, 60, 40, 50)
  check(#bad == 2 and bad[1] == "vie 40 %" and bad[2] == "familier 20 %", "avant le pull : pas de mana pour le chasseur")
  check(#F.PrePullReasons({ hp = 90, mana = 30 }, 60, 40, 50) == 1, "avant le pull : mana pour un lanceur de sorts")
  check(F.DeathAdvice({ elite = true, group = 1 }):find("^Cet élite est fait pour un groupe"), "conseil : élite seul")
  check(F.DeathAdvice({ killerLvl = 30, myLevel = 26, group = 4 }) == "Monstre 4 niveaux au-dessus de toi : vise ton niveau", "conseil : écart de niveau")
  check(F.DeathAdvice({ startHP = 45 }):find("45 %%"), "conseil : vie au départ")
  check(F.DeathAdvice({ env = "BREATH" }):find("^Remonte respirer"), "conseil : noyade (minuteur du jeu)")
  check(F.DeathAdvice({ killerLvl = 26, myLevel = 26, startHP = 100, duration = 12 }) == nil, "pas de conseil générique")
  check(F.RecapText({ killer = "Brute-test", killerLvl = 30, elite = true, advice = "Reviens à plusieurs" })
    == "Mort contre Brute-test (niv 30, élite) — Reviens à plusieurs", "texte du récap")
  for _, k in ipairs({ "CheckPull", "CheckPatrol", "OnCastStart", "ScanCC", "CheckPetAggro", "ShotTip", "CheckPetMood" }) do
    check(F[k] == nil, "retiré du Combat (faux / muet en vrai) : " .. k)
  end

  -- ------------------------------------------------------------------ Combat : avant le pull, puis mort
  local QF = T.modules.QuickFight
  local oldReady = QF and QF.db and QF.db.ready
  if QF and QF.db then QF.db.ready = false end   -- la barre ne revendique pas « prêt / pas prêt » : Combat parle
  M.target = { exists = true, name = "Brute-test", level = 30, hostile = true, classification = "elite",
    guid = "Creature-0-0-0-0-55-1" }
  M.player.health, M.player.healthMax = 400, 1000
  M.FireEvent("PLAYER_TARGET_CHANGED")
  check((sug("Fight:prepull") or ""):find("^Récupère avant le pull : vie 40 %%"), "avant le pull : " .. tostring(sug("Fight:prepull")))
  M.EnterCombat()
  check(sug("Fight:prepull") == nil, "en combat : la ligne d'avant le pull s'en va")
  M.FireEvent("PLAYER_DEAD")
  local d = sug("Fight:death") or ""
  check(d:find("^Mort contre Brute%-test %(niv 30, élite%)"), "pourquoi je suis mort : " .. d)
  check(d:find("reviens à plusieurs"), "conseil tiré des faits (élite, seul)")
  M.LeaveCombat()
  M.player.health = 1000
  M.Advance(1, 0.5)
  T:Fire("SUMMARY")
  local sf = T:Summary().fight
  check(sf and sf.lastDeath and sf.lastDeath.killer == "Brute-test" and sf.lastDeath.attackers == nil,
    "summary.fight : faits seulement (pas d'attaquants / dégâts inventés)")
  -- (FIDELITY) vie au départ lue AVANT le pull (secrète en combat sur Forever) : 40 % au ciblage
  check(sf and sf.lastDeath and sf.lastDeath.startHP == 40, "vie au départ lue hors combat : " .. tostring(sf and sf.lastDeath and sf.lastDeath.startHP))
  if QF and QF.db then QF.db.ready = oldReady end
  M.target = { exists = false }
  M.FireEvent("PLAYER_TARGET_CHANGED")
  T:ClearSuggestion("Fight:death")

  -- ------------------------------------------------------------------ Chasseur
  if cls == "HUNTER" then
    local H = T.modules.Hunter
    M.AddItem(2504, { name = "Arc-test", classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED", quality = 2 })
    M.inventory[18] = { id = 2504, count = 1, dur = 50, durMax = 60 }
    M.inventory[0] = { id = 2515, count = 120 }
    for b = 0, 4 do for s = 1, 16 do local it = M.bags[b] and M.bags[b].slots[s] if it and it.id == 2515 then M.SetBagItem(b, s, nil) end end end
    M.SetBagItem(1, 1, 2515, 120)
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(1, 0.5)
    H:CheckAmmo()
    local a = S["Hunter:ammo"]
    check(a and a.prio == 2 and a.text:find("^Munitions basses : 120 flèches"), "munitions basses : " .. tostring(a and a.text))
    M.inventory[0].count = 30
    M.SetBagItem(1, 1, 2515, 30)
    M.FireEvent("BAG_UPDATE_DELAYED")
    M.Advance(1, 0.5)
    H:CheckAmmo()
    a = S["Hunter:ammo"]
    check(a and a.prio == 3 and a.text == "Rachète des flèches vite : plus que 30", "munitions critiques : " .. tostring(a and a.text))
    -- fiche « Chasseur » (5.1 : onglet de l'ancien panneau et barre de piège = options, coupées d'office)
    H.db.tab, H.db.trapBar, H.db.fdTimer = true, true, true
    H:OnOptionChanged("tab", true) H:OnOptionChanged("trapBar", true)
    H:PushTab(true)
    local ht = tabText("hunter")
    check(T.tabs.hunter and T.tabs.hunter.title == "Chasseur", "onglet « Chasseur »")
    check(ht:find("Familier : Crocdur %(Loup, niv 25%) · content, 100 %% de ses dégâts"), "fiche : familier, humeur, vrais %")
    check(ht:find("5 points de dressage libres"), "fiche : points de dressage libres (total − utilisés)")
    check(ht:find("Munitions : 30 flèches"), "fiche : munitions")
    check(not ht:find("%?") and not ht:find("nil"), "fiche propre : " .. ht)
    -- pièges : barre honnête (jamais « déclenché ») ; Feindre la mort réussi → piège
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 1499)
    local tr = H.Trap()
    check(tr and H.TrapText(tr, M.now):find("^Piège givrant posé : 60 s au plus · recharge 15 s"), "barre de piège : " ..
      tostring(tr and H.TrapText(tr, M.now)))
    M.EnterCombat()
    check(H.TrapText(H.Trap(), M.now):find("^Pièges : recharge"), "en combat : seulement la recharge (le reste est secret)")
    M.Advance(20, 1)
    M.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-2", 5384)
    M.Advance(1, 0.5)
    M.LeaveCombat()
    check((sug("Hunter:fd") or ""):find("^Feindre la mort a marché : pose ton piège maintenant"), "Feindre la mort réussi → piège")
    check(sug("Hunter:pethp") == nil, "pas de PV du familier en combat (secret sur Forever)")
    -- avant le pull : aspect et marque (5.1 : rappels avant le pull = option « pullTips », coupée d'office)
    H.db.pullTips = true H:OnOptionChanged("pullTips", true)
    M.auras.player = {}
    M.target = { exists = true, name = "Ogre-test", level = 27, hostile = true, classification = "elite", guid = "Creature-0-0-0-0-56-1" }
    M.FireEvent("PLAYER_TARGET_CHANGED")
    check((sug("Hunter:aspect") or ""):find("^Active « Aspect du faucon » avant le pull"), "aspect avant le pull : " .. tostring(sug("Hunter:aspect")))
    check((sug("Hunter:mark") or ""):find("^Pose « Marque du chasseur » avant le pull : c'est un élite"), "marque avant un élite")
    check(not H.CheckDeadZone and not H.UpdateFeedMacro and not H.OnCombatLog, "zone morte, macro « TAI Nourrir », journal de combat : retirés")
    M.target = { exists = false }
    M.FireEvent("PLAYER_TARGET_CHANGED")
  end

  -- ------------------------------------------------------------------ Statistiques : un taux = sa durée de mesure
  local St = T.modules.Stats
  local lines = St.Lines({ xph = { recent = 24000, recentMin = 15, q = 30000, min = 130 }, goldH = 20000, played = 130 },
    { left = 12000, next = 27 })
  check(lines[1] == "Niveau 27 dans ≈ 30 min à ton rythme (24k XP/h, mesuré sur 15 min)", "ETA niveau avec mesure : " .. tostring(lines[1]))
  check(lines[2] and lines[2]:find("^XP/h par activité %(sur 2 h 10 de jeu%) : quêtes 30k"), "XP/h par activité : " .. tostring(lines[2]))
  check(#St.Lines({ xph = {} }, nil) == 0, "rien de mesuré = aucune ligne")
  check(T.Predict and T.Predict.Rates and T.Predict.DropRate, "contrat des taux mesurés (anciens lecteurs de Prévisions)")

  -- courses (liste À faire, points de la minimap) = les lignes réelles des propriétaires, sans calcul à part
  T:Suggest("Inventory:inv_repair", "Répare au plus vite : équipement cassé (rouge)", 3, { module = "Inventory", sound = false })
  local rep
  for _, e in ipairs(T.Predict.list) do if e.id == "repair" then rep = e end end
  check(rep and rep.text:find("^Répare") and rep.prio == 3, "courses : la ligne réelle de l'Inventaire (façade)")
  T:ClearSuggestion("Inventory:inv_repair")

  -- ------------------------------------------------------------------ Équipement : des mots
  local Gr = T.modules.Gear
  check(Gr.Verdict(999) == "emplacement vide" and Gr.Verdict(20) == "nettement mieux" and Gr.Verdict(5) == "mieux"
    and Gr.Verdict(0) == "équivalent" and Gr.Verdict(-10) == "moins bien", "verdicts en mots")

  -- ------------------------------------------------------------------ Inventaire : nourriture
  local Inv = T.modules.Inventory
  check(Inv.FoodText(3, 10, false) == "Refais le plein chez un marchand : plus que 3 repas", "nourriture : repas")
  check(Inv.FoodText(10, 2, true) == "Refais le plein chez un marchand : plus que 2 boissons", "boissons : lanceur de sorts")
  check(Inv.FoodText(10, 0, false) == nil, "chasseur : jamais de rappel de boisson")
  local ch = Inv.Cheapest({ { name = "Os", value = 3, quality = 0, classID = 15 }, { name = "Patte", value = 1, classID = 12 },
    { name = "Gemme", value = 5000, quality = 2, classID = 7 }, { name = "Pain", value = 1, classID = 0 } }, 3)
  check(#ch == 2 and ch[1].name == "Os", "à jeter d'abord : jamais quête ni consommable")

  -- ------------------------------------------------------------------ Guichets : prix réel du maître de classe
  local Er = T.modules.Errands
  check(Er.SaveText({ level = 30, cost = 32000 }, 28, 10000) == "Garde 3 po 20 pa pour ton maître de classe au niveau 30 : tu as 1 po",
    "garde ton or : " .. tostring(Er.SaveText({ level = 30, cost = 32000 }, 28, 10000)))
  check(Er.SaveText({ level = 30, cost = 32000 }, 27, 10000) == nil and Er.SaveText({ level = 30, cost = 32000 }, 28, 40000) == nil,
    "pas trop tôt, pas si tu as l'or")

  -- « Joindre compos » (repris d'Économie) : rien sans l'onglet « Envoyer » ouvert, jamais d'envoi
  check(Er.MatsClick() == false and Er.Mats and type(Er.Mats()) == "table", "joindre compos : seulement onglet Envoyer ouvert")

  -- ------------------------------------------------------------------ Ma session : monture (niveaux 30 à 39)
  local Se = T.modules.Session
  check((Se.MountText(32, 340000, 900000, nil) or "") == "Monture au niveau 40 : 34 po de côté sur 90 po", "monture : or réel / objectif")
  check(Se.MountText(28, 0, 900000) == nil and Se.MountText(40, 0, 900000) == nil, "monture : seulement de 30 à 39")

  -- ------------------------------------------------------------------ Progression : le repos est une info
  local Lv = T.modules.Leveling
  check(Lv.RestedText(19, false) == "Ton repos double l'XP des monstres (19 % d'un niveau)", "repos : info")
  check(Lv.RestedText(150, true):find("^Ton repos est plein"), "repos plein")
  check(Lv.ZoneAdvice(1418, 28) and select(2, Lv.ZoneAdvice(1418, 28)) == 2, "zone dangereuse : prio 2 (pas d'alerte)")
  check(Lv.ZoneAdvice(1413, 40) == nil, "zone trop facile : c'est le planificateur qui le dit")
  check(sug("Leveling:lvl_rested") == nil and sug("Leveling:lvl_train") == nil and sug("Leveling:lvl_hub") == nil,
    "repos / maître de classe / coin conseillé : plus dans « Maintenant » depuis Progression")

  -- ------------------------------------------------------------------ Touches : nouveaux sorts appris
  local Bd = T.modules.Binds
  if Bd and Bd.char then
    local oldNames = Bd.SpellbookNames
    Bd.char.spells = { ["Ancien-test"] = true }
    Bd.SpellbookNames = function() return { ["Ancien-test"] = true, ["Nouveau-test"] = 77001 }, 2 end
    Bd:Refresh()
    Bd.SpellbookNames = oldNames
    check(Bd.char.newSpells["Nouveau-test"], "nouveau sort retenu")
    check(tabText("binds"):find("Nouveau%-test"), "onglet Touches : nouveau sort à poser")
  end

  -- ------------------------------------------------------------------ cœur : ralenti auto (repris de Confort)
  local oldFR = rawget(_G, "GetFramerate")
  rawset(_G, "GetFramerate", function() return 20 end)
  T._PerfSample() T._PerfSample()
  check(T.perfSlow == true, "ralenti auto sous 30 images/s")
  rawset(_G, "GetFramerate", function() return 60 end)
  for _ = 1, 5 do T._PerfSample() end
  check(not T.perfSlow, "retour à la normale")
  rawset(_G, "GetFramerate", oldFR)

  check(#M.blocked == 0, "aucune action bloquée (" .. #M.blocked .. ")")
end
