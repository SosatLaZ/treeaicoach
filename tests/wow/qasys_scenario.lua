-- Harness scenario (4.0 QA, FIX-SYSTEMS) : régressions des constats « systèmes » de research/qa_systems.md.
-- P0 : S1 récap de mort (jamais l'ennemi d'un combat précédent), S2 / S3 familier « content » = prêt, S32 butin de donjon
-- (maîtrise d'armure par classe ET niveau, rien avant les infos de l'objet), S4 Talents sans lecture de l'arbre (Forever :
-- jamais « Ensuite : … » inventé), S5 rare élite trop fort = prévenir, sans son. P1 : S6 plus de minuteurs de boss
-- inventés, S7 un seul propriétaire du chrono de quête (Quêtes), S8 « Fais N× X » seulement pour une recette apprise.
-- + finitions (S9–S31). Chasseur 28, noms factices. lua5.1 tests/wow/harness.lua --class HUNTER --scenario … wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check, fire = ctx.check, ctx.fire
  local S = T._suggestions
  local forever = M.flavor == "forever"
  local function sug(id) local e = S[id] return e and e.text end
  local function tabLines(id)
    local out = {}
    for _, l in ipairs(T:GetTabLines(id) or {}) do out[#out + 1] = T.PlainText(tostring(l.text or l.label or "")) end
    return out
  end
  local function has(lines, pat, plain)
    for _, t in ipairs(lines) do if t:find(pat, 1, plain) then return t end end
  end
  local function dump(lines) return table.concat(lines, " || ") end

  if M.inCombat then M.LeaveCombat() end
  M.player.dead, M.player.ghost, M.onTaxi, M.instance, M.encounter = false, false, false, false, false
  M.player.level, M.player.health, M.player.healthMax = 28, 1450, 1450
  M.player.zone, M.player.subzone = "Mille pointes", "Camp Freewind"
  M.group = {}
  M.target = { exists = false }
  M.units = M.units or {}
  M.pet = { exists = true, name = "Griffe", family = "Félin", health = 1100, healthMax = 1100, happiness = 2,
    loyalty = "Loyauté (4)", tp = 120, level = 28, dead = false }
  fire("GROUP_ROSTER_UPDATE"); fire("UNIT_PET", "player"); fire("PLAYER_TARGET_CHANGED"); fire("ZONE_CHANGED_NEW_AREA")
  T:PlayerState(true)
  M.Advance(2, 0.5)

  ---------------------------------------------------------------------------------------------- S1 récap de mort
  local F = T.modules.Fight
  check(F and F.db and F.db.death, "Combat : module actif")
  T:ClearSuggestion("Fight:death")
  -- a) tue un coyote (ciblé hors combat), fin du combat, cible vidée ; 30 s plus tard un élite niv 33 t'aggro
  --    (rien de ciblé au pull), tu le cibles EN combat (nom secret sur Forever) et il te tue
  M.target = { exists = true, name = "Coyote-test", level = 27, health = 600, healthMax = 600, hostile = true,
    classification = "normal", guid = "Creature-0-0-0-0-4166-0001" }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(1)
  M.EnterCombat()
  M.Advance(8, 1)
  M.target.dead, M.target.health = true, 0
  fire("UNIT_HEALTH", "target")
  M.LeaveCombat()
  M.target = { exists = false }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(30, 1)
  M.EnterCombat()
  M.target = { exists = true, name = "Ogre-test", level = 33, health = 3000, healthMax = 3000, hostile = true,
    classification = "elite", guid = "Creature-0-0-0-0-9999-0001" }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(12, 1)
  M.player.health, M.player.dead = 0, true
  fire("PLAYER_DEAD")
  M.LeaveCombat()
  local d = sug("Fight:death") or ""
  check(d ~= "" and not d:find("Coyote", 1, true), "S1 : jamais le coyote tué 30 s avant : " .. d)
  check(d:find("niv 33", 1, true) and d:find("élite", 1, true), "S1 : le vrai tueur (niveau et rang lisibles en combat) : " .. d)
  check(d:find("reviens à plusieurs", 1, true), "S1 : conseil tiré des faits (élite, seul) : " .. d)
  if forever then
    check(d:find("^Mort contre un élite niv 33"), "S1 Forever : nom secret en combat, niveau + rang dits : " .. d)
  end
  M.player.dead, M.player.health = false, 1450
  fire("PLAYER_ALIVE")
  M.target = { exists = false }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(5, 1)
  -- b) S11 : le combat suivant efface le récap de « Maintenant »
  M.EnterCombat()
  check(sug("Fight:death") == nil, "S11 : récap de mort retiré au combat suivant")
  M.Advance(2, 1)
  M.LeaveCombat()
  M.Advance(2, 1)
  -- c) un coyote tué, puis un combat SANS cible, mort avec PLAYER_REGEN_ENABLED avant PLAYER_DEAD : « Mort » sans nom
  M.target = { exists = true, name = "Coyote-test", level = 27, health = 600, healthMax = 600, hostile = true,
    classification = "normal", guid = "Creature-0-0-0-0-4166-0002" }
  fire("PLAYER_TARGET_CHANGED")
  M.EnterCombat()
  M.Advance(6, 1)
  M.target.dead, M.target.health = true, 0
  M.LeaveCombat()
  M.target = { exists = false }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(20, 1)
  M.EnterCombat()
  M.Advance(9, 1)
  M.player.health, M.player.dead = 0, true
  M.LeaveCombat()                -- l'ordre inverse : fin de combat d'abord
  M.Advance(0.5)
  fire("PLAYER_DEAD")
  d = sug("Fight:death") or ""
  check(d:find("^Mort") and not d:find("Coyote", 1, true), "S1 : combat sans cible = tueur inconnu, pas l'ancien ennemi : " .. d)
  check(F.lastRecap and (F.lastRecap.duration or 0) >= 8, "S1 : fin de combat avant la mort, le combat fatal reste compté ("
    .. tostring(F.lastRecap and F.lastRecap.duration) .. " s)")
  M.player.dead, M.player.health = false, 1450
  fire("PLAYER_ALIVE")
  M.Advance(2, 1)
  T:ClearSuggestion("Fight:death")
  -- d) S10 : vie au départ relue sur UNIT_HEALTH (repas jusqu'à 100 % puis tir dans la seconde)
  M.player.health = 700
  M.target = { exists = true, name = "Galak-test", level = 28, health = 900, healthMax = 900, hostile = true,
    classification = "normal", guid = "Creature-0-0-0-0-4094-0001" }
  fire("PLAYER_TARGET_CHANGED")
  M.Advance(0.2)
  M.player.health = 1450
  fire("UNIT_HEALTH", "player")
  M.Advance(0.2)
  M.EnterCombat()
  M.Advance(4, 1)
  M.player.health, M.player.dead = 0, true
  fire("PLAYER_DEAD")
  M.LeaveCombat()
  check(F.lastRecap and F.lastRecap.startHP == 100, "S10 : vie au départ = 100 % (relue après le repas) : "
    .. tostring(F.lastRecap and F.lastRecap.startHP))
  check(F.lastRecap and F.lastRecap.killer == "Galak-test", "S1 : la cible lue au pull (même monstre) garde son nom : "
    .. tostring(F.lastRecap and F.lastRecap.killer))
  M.player.dead, M.player.health = false, 1450
  fire("PLAYER_ALIVE")
  M.target = { exists = false }
  fire("PLAYER_TARGET_CHANGED")
  T:ClearSuggestion("Fight:death")
  M.Advance(2, 1)

  ---------------------------------------------------------------------------------------------- S9 seuils de Combat
  local QF = T.modules.QuickFight
  if QF and QF.db then
    local oldReady, oldHp, oldPull = QF.db.ready, QF.db.hp, F.db.hpPull
    QF.db.ready, QF.db.hp, F.db.hpPull = false, 70, 80      -- la barre ne montre pas « Prêt / Pas prêt » : Combat parle
    M.player.health = 1100                                  -- 76 %
    M.target = { exists = true, name = "Galak-test", level = 28, health = 900, healthMax = 900, hostile = true,
      classification = "normal", guid = "Creature-0-0-0-0-4094-0002" }
    fire("PLAYER_TARGET_CHANGED")
    check((sug("Fight:prepull") or ""):find("vie 76 %%"), "S9 : le curseur « Vie minimale avant un pull » (80 %) s'applique : "
      .. tostring(sug("Fight:prepull")))
    QF.db.ready, QF.db.hp, F.db.hpPull = oldReady, oldHp, oldPull
    M.player.health = 1450
    M.target = { exists = false }
    fire("PLAYER_TARGET_CHANGED")
  end

  ---------------------------------------------------------------------------------------------- S2 barre de combat
  if QF and QF.Plan and QF.Snapshot then
    M.pet.happiness = 2
    M.target = { exists = true, name = "Galak-test", level = 29, health = 900, healthMax = 900, hostile = true,
      classification = "normal", guid = "Creature-0-0-0-0-4094-0003" }
    fire("PLAYER_TARGET_CHANGED")
    M.Advance(1, 0.5)
    local P = QF:Plan(QF:Snapshot())
    local why = table.concat(P.reasons or {}, ", ")
    check(not why:find("familier", 1, true), "S2 : familier content = prêt (100 % des dégâts) : " .. why)
    check(not tostring(P.status or ""):find("familier", 1, true), "S2 : pas de « Pas prêt : familier … » : " .. tostring(P.status))
    M.pet.happiness = 1
    P = QF:Plan(QF:Snapshot())
    why = table.concat(P.reasons or {}, ", ")
    check(why:find("familier mécontent", 1, true), "S2 : mécontent (75 %) = pas prêt : " .. why)
    M.pet.happiness = 2
    M.target = { exists = false }
    fire("PLAYER_TARGET_CHANGED")
  end

  ---------------------------------------------------------------------------------------------- S5 rares
  local B = T.modules.Boost
  check(type(B.CanHandle) == "function", "S5 : Boost.CanHandle")
  local CH = B.CanHandle or function() return nil end
  check(CH("rareelite", 33, 28, 1) == false and CH("rare", 30, 28, 1) == true
    and CH("rare", 31, 28, 1) == false and CH("rareelite", 29, 28, 4) == true
    and CH("rareelite", 28, 28, 1) == false and CH("worldboss", 60, 28, 5) == false
    and CH("rare", -1, 28, 4) == false and CH("rare", nil, 28, 1) == true, "S5 : M.CanHandle (pur)")
  local snd = #M.sounds
  T:ClearSuggestion("Boost:rare")
  M.units.nameplate7 = { exists = true, name = "Œil-de-fer-test", level = 33, hostile = true, classification = "rareelite",
    guid = "Creature-0-0-0-0-5555-0007", health = 3000, healthMax = 3000 }
  fire("NAME_PLATE_UNIT_ADDED", "nameplate7")
  M.Advance(1)
  local r = S["Boost:rare"]
  check(r and r.text == "Rare élite Œil-de-fer-test (niv 33) tout près : trop fort pour toi seul, évite-le" and r.prio == 1
    and not r.onClick, "S5 : rare élite niv 33, seul = « évite-le », sans crâne : " .. tostring(r and r.text))
  check(#M.sounds == snd, "S5 : aucun son pour un rare trop fort")
  M.units.nameplate7 = nil
  T:ClearSuggestion("Boost:rare")
  M.group = { { name = "Bravo", class = "MAGE", level = 28, online = true }, { name = "Charlie", class = "PRIEST", level = 27, online = true },
    { name = "Delta", class = "WARRIOR", level = 26, online = true } }
  fire("GROUP_ROSTER_UPDATE")
  M.units.nameplate8 = { exists = true, name = "Croc-test", level = 29, hostile = true, classification = "rareelite",
    guid = "Creature-0-0-0-0-5555-0008", health = 3000, healthMax = 3000 }
  fire("NAME_PLATE_UNIT_ADDED", "nameplate8")
  M.Advance(1)
  r = S["Boost:rare"]
  check(r and r.text:find("^Vise le rare élite Croc%-test %(niv 29%)") and r.onClick, "S5 : à 4, un rare élite niv 29 se vise : "
    .. tostring(r and r.text))
  M.units.nameplate8 = nil
  T:ClearSuggestion("Boost:rare")

  ---------------------------------------------------------------------------------------------- S3 / S22 / S32 donjon
  local D = T.modules.Dungeon
  local rfk = D.Find("RFK")
  check(rfk and rfk.n == "Kraal de Tranchebauge", "Kraal de Tranchebauge dans les données")
  local function prep()
    local out = {}
    for _, c in ipairs(D.Checklist(rfk, D.Members(true))) do if c.sec == "Préparation" then out[#out + 1] = c.state .. ":" .. c.text end end
    return out
  end
  M.pet.happiness = 2
  local pl = prep()
  check(not has(pl, "^warn:Nourris"), "S3 : familier content = pas d'alerte « nourris-le » : " .. dump(pl))
  check(has(pl, "^info:Nourris ton familier : heureux = %+25 %% de dégâts"), "S3 : content = simple info (+25 % heureux) : " .. dump(pl))
  M.pet.happiness = 1
  pl = prep()
  check(has(pl, "^warn:Nourris ton familier : mécontent, il fait 25 %% de dégâts en moins"), "S3 : mécontent = alerte : " .. dump(pl))
  M.pet.happiness = 3
  check(has(prep(), "^ok:Familier : prêt"), "S3 : heureux = prêt")
  M.pet.happiness = 2
  -- S22 : déjà dans l'instance -> plus de « avant d'entrer »
  M.player.zone, M.player.subzone = "Kraal de Tranchebauge", ""
  M.instance = true
  fire("ZONE_CHANGED_NEW_AREA"); fire("PLAYER_ENTERING_WORLD", false, false)
  M.Advance(2, 1)
  D.Active(true)
  M.inventory[1] = M.inventory[1] or {}
  local oldInv1 = { id = M.inventory[1].id, dur = M.inventory[1].dur, durMax = M.inventory[1].durMax }
  M.AddItem(90901, { name = "Casque-test", classID = 4, subclassID = 3, equipLoc = "INVTYPE_HEAD", quality = 2 })
  M.inventory[1] = { id = 90901, dur = 10, durMax = 60 }
  M.pet.exists = false
  pl = prep()
  check(not has(pl, "avant d'entrer", true), "S22 : en instance, jamais « avant d'entrer » : " .. dump(pl))
  check(has(pl, "Appelle ton familier", true), "S22 : « Appelle ton familier » : " .. dump(pl))
  M.pet.exists = true
  M.inventory[1] = oldInv1.id and oldInv1 or nil
  -- S32 : butin (maîtrise d'armure par classe ET niveau ; rien avant les infos de l'objet)
  local boss
  for _, b in ipairs(rfk.bosses) do for _, it in ipairs(b.loot or {}) do if it[1] == 6686 then boss = b end end end
  local members = { { name = "toi", me = true, class = "HUNTER", level = 28 }, { name = "Delta", class = "WARRIOR", level = 26 } }
  local helm
  for _, it in ipairs(boss and boss.loot or {}) do if it[1] == 6686 then helm = it end end
  M.items[6686] = nil
  local e = D.LootItem(helm, boss, members)
  check(e.pending and #e.need == 0 and D.LootVerdictText(e, members) == "données de l'objet en attente",
    "S32 : objet pas encore reçu du serveur = aucun verdict : " .. D.LootVerdictText(e, members))
  M.AddItem(6686, { name = "Heaume Tusken", classID = 4, subclassID = 3, equipLoc = "INVTYPE_HEAD", minLevel = 26, quality = 2,
    type = "Armure", subType = "Mailles" })
  e = D.LootItem(helm, boss, members)
  local v = D.LootVerdictText(e, members)
  check(v == "Besoin : Delta · Cupidité : l'autre", "S32 : mailles avant 40 = guerrier (Besoin), pas le chasseur 28 : " .. v)
  check(e.later[1] and e.later[1].m.me and e.later[1].lvl == 40, "S32 : le chasseur portera les mailles au niveau 40")
  members[1].level = 40
  e = D.LootItem(helm, boss, members)
  local meNeed = false
  for _, n in ipairs(e.need) do if n.m.me then meNeed = true end end
  check(meNeed or (e.later[1] == nil), "S32 : au niveau 40 le chasseur peut prendre les mailles")
  M.instance = false
  M.player.zone, M.player.subzone = "Mille pointes", "Camp Freewind"
  fire("ZONE_CHANGED_NEW_AREA"); fire("PLAYER_ENTERING_WORLD", false, false)
  M.Advance(2, 1)

  ---------------------------------------------------------------------------------------------- S6 minuteurs de boss
  check(D.TickTimers == nil and not (D.db and D.db.timers), "S6 : plus de minuteurs de boss inventés")
  M.player.zone = "Monastère écarlate"
  M.instance = true
  fire("ZONE_CHANGED_NEW_AREA"); fire("PLAYER_ENTERING_WORLD", false, false)
  M.Advance(2, 1)
  M.EnterCombat()
  fire("ENCOUNTER_START", 447, "Arcaniste Doan", 1, 5)
  local timed
  for _ = 1, 60 do
    M.Advance(1, 0.5)
    for id, x in pairs(S) do
      if id:find("^Dungeon:") and (tostring(x.text):find("≈ %d+ s") or tostring(x.text):find("dans %d+ s")) then timed = x.text end
    end
  end
  check(timed == nil, "S6 : aucun « reculez ! (≈ 5 s) » pendant Doan : " .. tostring(timed))
  fire("ENCOUNTER_END", 447, "Arcaniste Doan", 1, 5, 1)
  M.LeaveCombat()
  M.instance = false
  M.player.zone = "Mille pointes"
  fire("ZONE_CHANGED_NEW_AREA"); fire("PLAYER_ENTERING_WORLD", false, false)
  M.Advance(2, 1)

  ---------------------------------------------------------------------------------------------- S7 chrono de quête
  local X = T.Edge2
  check(X and X.CheckTimers == nil, "S7 : Avantages ne double plus le chrono de quête")
  for _, f in ipairs(X and X.FEATURES or {}) do check(f[1] ~= "questTimer", "S7 : option « Quête chronométrée » retirée d'Avantages") end
  check(S["Edge2:questTimer"] == nil, "S7 : pas de ligne de chrono dans Avantages")

  ---------------------------------------------------------------------------------------------- S4 Talents sans lecture de l'arbre
  local TL = T.modules.Talents
  local savedG = {}
  local GONE = { "GetNumTalentTabs", "GetNumTalents", "GetTalentInfo", "GetTalentTabInfo", "UnitCharacterPoints",
    "GetUnspentTalentPoints", "GetActiveTalentGroup", "GetNumTalentGroups" }
  for _, n in ipairs(GONE) do savedG[n] = rawget(_G, n) rawset(_G, n, nil) end
  local CT = rawget(_G, "C_ClassTalents")
  local madeCT = CT == nil
  if madeCT then CT = {} rawset(_G, "C_ClassTalents", CT) end
  local oldHas = rawget(CT, "HasUnspentTalentPoints")
  CT.HasUnspentTalentPoints = function() return true, 1, 0 end
  M.player.level = 28
  if TL.char then TL.char.mode = "auto" end
  TL.full = false
  TL.Update()
  local tl = tabLines("info")
  check(not has(tl, "^Ensuite"), "S4 : jamais « Ensuite : … » quand l'arbre est illisible : " .. dump(tl))
  check(not has(tl, "hors parcours", true) and not has(tl, "^Parcours complet%."), "S4 : ni « hors parcours » ni « Parcours complet. »")
  check(has(tl, "Vérifie tes talents dans la fenêtre du jeu (touche N)", true), "S4 : renvoi clair à la fenêtre du jeu : " .. dump(tl))
  local free = has(tl, "^Point de talent libre %(touche N%) : au niveau 28, le parcours prend « Ferocité » %(3/5%)")
  check(free, "S4 : point libre lu (C_ClassTalents) + le guide pour ce niveau : " .. dump(tl))
  check((sug("Talents:next") or ""):find("^Point de talent libre %(touche N%) : au niveau 28, le parcours prend"),
    "S4 : « Maintenant » sans « Prochain point » inventé : " .. tostring(sug("Talents:next")))
  TL.full = true
  TL.Update()
  tl = tabLines("info")
  check(has(tl, "^Niv 10%-14 : Aspects mortels 1 à 5/5 %(Maîtrise des bêtes, ligne 1%)"), "S4 : guide par niveau, sans ✓ : " .. dump(tl))
  check(not has(tl, "^OK Aspects") and not has(tl, "^✓") and not has(tl, "^· "), "S4 : aucune coche de progrès inventée")
  TL.full = false
  T:Fire("SUMMARY")
  local st = T:Summary().talents
  check(st and st.next == nil and st.readable == false and st.points == nil, "S4 : summary.talents sans prochain point ni points inventés")
  CT.HasUnspentTalentPoints = function() return false, 0, 0 end
  TL.Update()
  check(sug("Talents:next") == nil, "S4 : aucun point libre = aucune ligne")
  CT.HasUnspentTalentPoints = oldHas
  if madeCT then rawset(_G, "C_ClassTalents", nil) end
  for _, n in ipairs(GONE) do rawset(_G, n, savedG[n]) end
  TL.Update()

  ---------------------------------------------------------------------------------------------- S8 métiers (recettes apprises)
  local P = T.modules.Prof
  if P and P.PlanFor and P.char then
    local oldKnown = P.char.known
    P.char.known = {}
    local pr = { line = 129, rank = 40, max = 75, name = "Secourisme" }
    M.AddItem(2589, { name = "Étoffe de lin", stack = 20, sellPrice = 13 })
    M.SetBagItem(1, 2, 2589, 12)
    local res = P.PlanFor(pr)
    check(res and res.now == nil, "S8 : aucune recette lue = pas de « Fais N× X maintenant » (avant : toutes celles de l'entraîneur)")
    P.char.known = { [129] = { [3276] = 1 } }
    res = P.PlanFor(pr)
    check(res and res.now and res.now.row[1] == 3276, "S8 : recette apprise = « Fais N× » possible")
    P.char.known = oldKnown
    M.SetBagItem(1, 2, nil)
  end

  ---------------------------------------------------------------------------------------------- finitions
  -- S12 : auras masquées en combat = inconnu (pas « absent »)
  if T:PlayerClass() == "HUNTER" then
    M.auras = M.auras or {}
    M.auras.player = { { name = "Aspect du faucon", spellId = 13165, kind = "buff", source = "player" } }
    M.target = { exists = true, name = "Galak-test", level = 29, health = 900, healthMax = 900, hostile = true,
      classification = "normal", guid = "Creature-0-0-0-0-4094-0004" }
    fire("PLAYER_TARGET_CHANGED")
    M.EnterCombat()
    M.secretPlayer = true
    M.Advance(6, 0.5)
    local H = T.modules.Hunter
    if H and H.CheckAspect then H:CheckAspect() end
    check(not (sug("Hunter:aspect") or ""):find("^Active", 1), "S12 : auras illisibles : pas de « Active Aspect du faucon » : "
      .. tostring(sug("Hunter:aspect")))
    M.secretPlayer = nil
    M.LeaveCombat()
    M.target = { exists = false }
    fire("PLAYER_TARGET_CHANGED")
    -- S13 : mouvement suivi par événements (GetUnitSpeed est secret en combat)
    fire("PLAYER_STARTED_MOVING")
    check(H and H.moving == true, "S13 : PLAYER_STARTED_MOVING suivi")
    fire("PLAYER_STOPPED_MOVING")
    check(H and H.moving == false, "S13 : PLAYER_STOPPED_MOVING suivi")
  end
  -- S16 / S17
  check(type(T.version) == "string" and T.version:match("^4%.%d+%.%d+$") ~= nil, "S17 : version 4.x.y (autotest, rapport d'erreurs)")
  -- S19 : une entrée par objet dans « à jeter d'abord »
  local Inv = T.modules.Inventory
  local g = (Inv.GroupByName or function() return {} end)({ { name = "Griffe cassée", value = 7, unit = 7, count = 1, quality = 0, classID = 15 },
    { name = "Griffe cassée", value = 7, unit = 7, count = 1, quality = 0, classID = 15 },
    { name = "Griffe cassée", value = 14, unit = 7, count = 2, quality = 0, classID = 15 } })
  check(#g == 1 and g[1].count == 4 and g[1].value == 7, "S19 : regroupé par objet (×4)")
  -- S20 : objectif sans « tué »
  local Grp = T.modules.Group
  local E = Grp and Grp.Engine
  if E and E.MemberStatus then
    local st2 = E.MemberStatus({ per = { Delta = { obj = { { have = 2, need = 8 } } } }, objNames = { "Rassembleur Fléau-Mort" } }, "Delta")
    check(st2 == "Delta : Rassembleur Fléau-Mort 2/8", "S20 : « Delta : Rassembleur Fléau-Mort 2/8 » : " .. tostring(st2))
  end
  -- S25 : virgule française
  local St = T.modules.Stats
  local sl = St.Lines({ xph = { q = 8500, min = 30 } }, nil)
  check(sl[1] and sl[1]:find("quêtes 8,5k", 1, true), "S25 : « 8,5k » : " .. tostring(sl[1]))
  sl = St.Lines({ deathTop = "Ours-test", downSec = 25, sheet = { rap = 150, rcrit = 5, armor = 900 } }, nil)
  check(has(sl, "tué surtout par Ours-test", true) and has(sl, "^Fiche : "), "S25 : « tué surtout par », « Fiche : » : " .. dump(sl))
  -- S30 : maître de vol
  local Er = T.modules.Errands
  check(Er.HearthOrFly(0, { name = "Camp Freewind", cost = 0 }, "Camp Freewind") == "Vers Camp Freewind : prends ta pierre de foyer (prête) plutôt que le vol",
    "S30 : sans prix du vol : " .. tostring(Er.HearthOrFly(0, { name = "Camp Freewind", cost = 0 }, "Camp Freewind")))
  M.group = {}
  fire("GROUP_ROSTER_UPDATE")
  M.Advance(1)
end
