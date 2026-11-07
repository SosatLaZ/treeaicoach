-- Harness scenario (CONTENTFIX, round 5 in-game screenshot): état du joueur (mort / fantôme / vol / monture /
-- repos) -> silence, une seule ligne « macros », récap de mort sans chiffres inventés, texte d'objectif propre,
-- glyphes illisibles en jeu retirés, mode simple (défaut + proposé une fois). Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/content_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local S = T._suggestions
  M.LeaveCombat()
  T:ClearSuggestions()
  T:ResetAlertGate()
  local function nowHas(pat)
    for _, e in ipairs(T:GetTabLines("now")) do if tostring(e.text):find(pat, 1, true) then return e end end
  end
  local function setState(k, v)
    if k == "ghost" or k == "dead" then M.player[k] = v
    elseif k == "taxi" then M.onTaxi = v
    elseif k == "mounted" then M.mounted = v
    elseif k == "resting" then M.player.resting = v end
    T:PlayerState(true)
  end

  -- 0. mode simple : préréglage par défaut d'une nouvelle installation
  -- (le harness a déjà cliqué partout, préréglages compris : on rejoue l'arrivée d'une installation neuve)
  check(T.settings.simpleV == T.SIMPLE_VERSION, "drapeau de version posé au login")
  T:ApplyPreset("normal")
  T.settings.simpleV = nil
  T:_OfferSimpleMode(true)
  check(T.settings.preset == "simple" and not S["core:simple"], "nouvelle installation : mode simple, sans annonce")
  T:ApplyPreset("normal")

  -- 1. état du joueur
  local st = T:PlayerState(true)
  check(st.dead == false and st.ghost == false and st.taxi == false and st.mounted == false, "vivant à pied")
  for _, k in ipairs({ "dead", "ghost", "taxi" }) do
    setState(k, true)
    check(T:PlayerState()[k] == true and T:Quiet("advice") and T:Quiet("buff") and not T:Quiet("death"),
      k .. " : conseils et rappels muets, mort permise")
    -- rappel d'aspect (comme sur la capture) : jeté
    check(T:Suggest("Hunter:aspect", "Active « Aspect du faucon »", 2, { module = "Hunter" }) == nil
      and not S["Hunter:aspect"], k .. " : pas de rappel d'aspect")
    T:Suggest("Inventory:inv_bags", "Vends tes gris : sacs presque pleins", 2, { module = "Inventory", onClick = function() end })
    check(not nowHas("Vends tes gris"), k .. " : conseil ordinaire mis en attente")
    T:Suggest("Fight:death", "Tué par Loup-test (niv 20) — recule plus tôt", 2, { module = "Fight", death = true })
    check(nowHas("Tué par Loup-test"), k .. " : la ligne de mort reste")
    setState(k, false)
    check(nowHas("Vends tes gris"), k .. " fini : le conseil revient")
    T:ClearSuggestions()
  end
  for _, k in ipairs({ "mounted", "resting" }) do
    setState(k, true)
    check(T:Suggest("Hunter:pethp", "Soigne ton familier", 2, { module = "Hunter" }) == nil, k .. " : pas de rappel familier")
    check(T:Suggest("Classes:WARRIOR.shout", "Lance « Cri de guerre »", 1, { module = "Classes" }) == nil, k .. " : pas de rappel de buff")
    T:Suggest("Quests:test", "Rends « Quête-test » : terminée", 2, { module = "Quests" })
    check(nowHas("Rends « Quête-test »"), k .. " : les vrais conseils restent")
    setState(k, false)
    T:ClearSuggestions()
  end
  -- fantôme : course au corps (texte pur)
  local R = T.modules.Radar
  if R and R.CorpseText then
    local a, b = R.CorpseText(243, 27), R.CorpseText(1460, 27)
    check(a and a:find("Cours à ton corps : 240 m"), "course au corps : " .. tostring(a))
    check(b and b:find("gardien des esprits") and b:find("1,5 km"), "corps loin -> gardien des esprits : " .. tostring(b))
  end

  -- 2. une seule ligne macros (installation + nouvelles macros + à mettre à jour)
  local Mac = T.modules.Macros
  if Mac and Mac._SuggestReady then
    Mac._SuggestReady()
    Mac._SuggestReady(nil, { "TAI Tir", "TAI Marque" })
    Mac._SuggestReady("Macros à mettre à jour : clique pour les mettre à jour")
    local n = 0
    for _, e in ipairs(T:GetSuggestions()) do
      if tostring(e.text):lower():find("macro") then n = n + 1 end
    end
    check(n == 1, "une seule ligne macros (" .. n .. ")")
    check(not S["Macros:setup"] and not S["Macros:new"] and not S["Macros:pending"], "plus d'anciens id macros")
    T:ClearSuggestions()
  end

  -- 3. récap de mort sans journal de combat : « Tué par X (niv N) » + un conseil, jamais « 0 dégâts »
  local F = T.modules.Fight
  if F and F.RecapText then
    M.target = { exists = true, name = "Ours gris-test", level = 23, hostile = true, guid = "Creature-0-0-0-0-9-1" }
    M.player.dead = true
    T:PlayerState(true)
    ctx.fire("PLAYER_DEAD")
    local d = S["Fight:death"]
    local txt = d and d.text or ""
    check(txt:find("^Tué par Ours gris%-test %(niv 23%)") and not txt:find("dégâts") and not txt:find("%f[%d]0%f[%D]"),
      "récap sans chiffres inventés : " .. txt)
    check(nowHas("Tué par Ours gris-test"), "récap visible quand on est mort")
    local tip = d and d.tooltip or {}
    for _, l in ipairs(type(tip) == "table" and tip or {}) do
      check(not tostring(l):find("Dégâts subis") and not tostring(l):find("?", 1, true), "infobulle sans inconnu : " .. tostring(l))
    end
    check(F.RecapText({ killer = "Loup", total = 0, hits = 0, advice = "x" }) == "Tué par Loup — x", "pur : sans niveau")
    check(F.RecapText({ killer = "Loup", killerLvl = 12, total = 840, hits = 4, advice = "x" }) == "Tué par Loup (niv 12) — x",
      "pur : la ligne reste courte, les chiffres vont dans l'infobulle")
    check(not F.AdviceFor({ attackers = 1, total = 0, biggest = 0, hits = 0, maxHP = 1000 }):find("Gros coup"),
      "conseil : pas de « gros coup » sans dégâts mesurés")
    M.player.dead = false
    M.target = { exists = false }
    T:PlayerState(true)
    T:ClearSuggestions()
  end

  -- 4. texte d'objectif : nom propre, compteur une seule fois, verbe d'abord
  local ON = T.ObjectiveName
  check(ON("Atrexis le Chevalier de la tombe\194\160: 0/1") == "Atrexis le Chevalier de la tombe", "objectif FR (insécable)")
  check(ON("0/5 Oeil d'Aileron déchiré") == "Oeil d'Aileron déchiré", "objectif compteur devant")
  check(ON("Ours tué : 3/8 (Terminé)") == "Ours tué", "objectif (Terminé)")
  check(ON(nil) == "", "objectif nil")
  local P = T.modules.Planner
  if P and P.StepLabel then
    local saved = P.log
    P.log = { [9001] = { obj = { { text = "Atrexis le Chevalier de la tombe\194\160: 0/1", type = "monster", have = 0, n = 1 } } } }
    local lab = P:StepLabel({ k = "obj", q = 9001, o = 1 })
    check(lab == "Tue « Atrexis le Chevalier de la tombe »", "étape d'objectif : " .. lab)
    P.log = saved
  end

  -- 5. glyphes absents des polices WoW (carré vide en jeu)
  T:Suggest("x:glyph", "▶ Rends « Quête-test » ✔", 2)
  local g = S["x:glyph"]
  check(g and not g.text:find("▶") and not g.text:find("✔") and g.text:find("^Rends"), "glyphes retirés : " .. tostring(g and g.text))
  T:ClearSuggestions()

  -- 6. mode simple : 3 lignes, seulement l'utile (clic ou conseil prio ≥ 2) ; proposé une fois aux anciens
  T:ApplyPreset("simple")
  for i = 1, 4 do T:Suggest("a:" .. i, "Bavardage " .. i, 1) end
  T:Suggest("b:1", "Clique pour réparer", 1, { onClick = function() end })
  T:Suggest("b:2", "Rends « Quête-test »", 2)
  local lines = T:GetTabLines("now")
  local chatter = 0
  for _, e in ipairs(lines) do if tostring(e.text):find("Bavardage") then chatter = chatter + 1 end end
  check(#lines <= 3 and chatter == 0 and nowHas("Clique pour réparer") and nowHas("Rends « Quête-test »"),
    ("simple : %d lignes, %d bavardage(s)"):format(#lines, chatter))
  T:ClearSuggestions()
  T:ApplyPreset("normal")
  T.settings.simpleV = nil
  T:_OfferSimpleMode(false)
  local note = S["core:simple"]
  check(T.settings.preset == "simple" and note and note.onClick and note.text:find("Normal"), "ancien utilisateur : simple + 1 ligne")
  note.onClick()
  check(T.settings.preset == "normal" and not S["core:simple"], "clic : retour au mode précédent")
  T:_OfferSimpleMode(false)
  check(T.settings.preset == "normal" and not S["core:simple"], "proposé une seule fois")
  T:ClearSuggestions()
end
