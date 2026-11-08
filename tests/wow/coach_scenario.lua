-- Harness scenario (ADVICE 4.0.1) : module « Conseils pro » (Coach.lua). Chaque conseil : un déclencheur réel sur le
-- mock (et un cas où il se tait), une ligne courte, verbe d'abord, sans « ? », un « pourquoi » chiffré ; un seul
-- conseil à la fois dans « Maintenant » (le plus rentable gagne), temps de repos, interrupteur par conseil, page
-- « Conseils pro » (8 thèmes + techniques), rien en combat, ni chat ni son, aucune action bloquée. Noms factices.
-- lua5.1 tests/wow/harness.lua --flavor forever --class HUNTER --scenario tests/wow/coach_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local C = T.Coach
  check(C and C.Engine and C.CHECK, "module Conseils pro chargé")
  if not (C and C.Engine) then return end
  local E = C.Engine
  local S = T._suggestions
  local hunter = T:PlayerClass() == "HUNTER"

  -- ------------------------------------------------------------------ déclaration
  check(T.SAFE_MODULES.Coach == true and not T:IsBeta("Coach"), "mode sûr : Conseils pro allumé par défaut")
  check(T.MODULE_INFO.Coach and T.MODULE_INFO.Coach.lite == true and not T.MODULE_INFO.Coach.combat, "MODULE_INFO : léger, rien en combat")
  check(C._active, "module actif")
  check(#C.TIPS >= 18 and #C.TECH >= 35 and #C.THEMES == 8, "conseils / techniques / 8 thèmes (" .. #C.TIPS .. "/" .. #C.TECH .. ")")
  local themes = {}
  for _, th in ipairs(C.THEMES) do themes[th.id] = 0 end
  for _, tp in ipairs(C.TIPS) do
    check(C.db[tp.key] == true, "option ON par défaut : " .. tp.key)
    check(type(C.CHECK[tp.key]) == "function", "test du conseil : " .. tp.key)
    check(themes[tp.theme] ~= nil, "thème connu : " .. tp.key)
    themes[tp.theme] = themes[tp.theme] + 1
  end
  for _, tc in ipairs(C.TECH) do
    check(themes[tc.theme] ~= nil and tc.title and tc.line and tc.src, "technique complète : " .. tostring(tc.title))
    check(not tc.line:find("?", 1, true), "technique sans « ? » : " .. tc.title)
  end
  local W = T.Window
  if W then
    local ids = {}
    for _, pg in ipairs(W.PAGES) do ids[#ids + 1] = pg.id end
    local nav = table.concat(ids, ",")
    -- (4.1 LAYOUT) « Conseils pro » : 6e page de la navigation, avant « Réglages »
    check(nav:find("^quests,group,dungeon,perso,hv,coach,settings"), "page « Conseils pro » avant Réglages (" .. nav .. ")")
  end

  -- ------------------------------------------------------------------ moteur pur
  local n, xp, enough = E.TurnIn({ { complete = true, xp = 900 }, { complete = true, xp = 700 }, { complete = false, xp = 5000 } }, 1500)
  check(n == 2 and xp == 1600 and enough, "quêtes finies couvrent le niveau")
  local q, loss, part = E.Penalty({ { id = 1, title = "Vieille", level = 22, complete = true, xp = 2000 },
    { id = 2, title = "Fraîche", level = 27, complete = true, xp = 3000 } }, 28)
  check(q and q.id == 1 and loss == 500 and math.abs(part - 0.25) < 1e-9, "règle Classic : 6 → 7 niveaux d'écart = −25 % (" .. tostring(loss) .. ")")
  check(C.Q.mul(5) == 1 and C.Q.mul(6) == 0.8 and C.Q.mul(10) == 0.1, "XP de quête : 100 / 80 / 10 %")
  local kind, cur, best = E.AmmoTier(2515, 26)
  check(kind == "arrow" and cur == 2 and best == 3, "munitions : Flèche pointue → Flèche rasoir au niveau 25")
  local _, c2, b2 = E.AmmoTier(3030, 26)
  check(c2 == 3 and b2 == 3, "munitions : déjà au bon palier")
  check(select(2, E.AmmoTier(2519, 9)) == 2 and select(3, E.AmmoTier(2519, 9)) == 2, "munitions : niveau trop bas, pas de palier")
  check(E.TrainAt(30) and E.TrainAt(2) and not E.TrainAt(29) and not E.TrainAt(1), "maître de classe : niveaux pairs (Classic)")

  -- ------------------------------------------------------------------ remise à zéro
  local Q = T.modules.Quests
  local savedList = Q.lastList
  local function reset()
    if M.inCombat then M.LeaveCombat() end
    M.player.dead, M.player.ghost, M.onTaxi, M.instance, M.mounted = false, false, false, false, false
    M.player.resting = false
    M.group, M.player.level, M.player.xp, M.player.xpMax, M.player.rested = {}, 28, 12000, 26000, 0
    M.player.health, M.player.healthMax, M.player.power, M.player.powerMax, M.player.powerType = 1000, 1000, 600, 600, 0
    M.auras.player = {}
    M.target = { exists = false }
    M.pet.exists, M.pet.dead, M.pet.level = true, false, 28
    M.skills = nil
    Q.lastList = {}
    rawset(_G, "GetPetActionInfo", function() return nil end)
    T:PlayerState(true)
    C.ResetSession()
    M.FireEvent("UNIT_AURA", "player")
    T:ClearSuggestions("Coach:")
    if T.API.BagChanged then T.API.BagChanged() end
  end
  reset()
  -- les barres qui possèdent déjà un geste (manger, bandage, aspect, monture) : coupées pour tester nos lignes
  local qfWas, quickWas = T.modules.QuickFight and T.modules.QuickFight._active, T.modules.Quick and T.modules.Quick._active
  T:SetModuleEnabled("QuickFight", false)
  T:SetModuleEnabled("Quick", false)

  local function ev(key) local r = C.Evaluate(key) return r and r.text end
  local function yes(key, pat)
    local t = ev(key)
    check(t and t:find(pat, 1, true) and not t:find("?", 1, true) and not t:find("nil", 1, true), key .. " : " .. tostring(t))
    check(t == nil or T.TextLen(t) <= 96, key .. " : ligne ≤ 96 caractères (" .. tostring(t and T.TextLen(t)) .. ")")
    local r = C.active[key]
    check(r and type(r.why) == "table" and #r.why >= 1 and (r.score or 0) > 0, key .. " : pourquoi chiffré + poids")
    for _, l in ipairs(r and r.why or {}) do check(not l:find("nil", 1, true), key .. " : pourquoi propre : " .. l) end
    return t
  end
  local function no(key, why) local t = ev(key) check(t == nil, key .. " caché : " .. why .. " (" .. tostring(t) .. ")") end
  local function fights(nf, f)
    for _ = 1, nf do
      local e = {}
      for k, v in pairs(f) do e[k] = v end
      C.fights[#C.fights + 1] = e
    end
  end
  local function kills(nk, k)
    for _ = 1, nk do
      local e = { t = M.now }
      for kk, v in pairs(k) do e[kk] = v end
      C.kills[#C.kills + 1] = e
    end
  end
  local FOOD = { name = "Nourriture", spellId = 433, duration = 18, expirationTime = M.now + 18, kind = "buff" }
  local function aura(a) local x = {} for k, v in pairs(a) do x[k] = v end x.expirationTime = M.now + (a.duration or 18) return x end
  local function auras(list) M.auras.player = list or {} M.FireEvent("UNIT_AURA", "player") end

  -- ------------------------------------------------------------------ Repos et ressources
  -- plat « Bien nourri » (Forever : +5 % d'XP des monstres, sort 1243969)
  M.AddItem(2685, { name = "Côtes de porc aux herbes", stack = 20, classID = 0, subclassID = 5, minLevel = 15,
    type = "Consommable", subType = "Nourriture et boissons" })
  M.SetBagItem(2, 1, 2685, 3)
  T.API.BagChanged()
  yes("wellFed", "Mange « Côtes de porc aux herbes » 10 s : Bien nourri, +5 % d'XP sur les monstres")
  auras({ aura({ name = "Bien nourri – bonus d’EXP", spellId = 1243969, duration = 900 }) })
  no("wellFed", "bonus d'XP déjà actif")
  auras({})
  M.player.level = 60
  no("wellFed", "niveau 60 : plus d'XP")
  M.player.level = 28
  M.SetBagItem(2, 1, nil)
  T.API.BagChanged()
  no("wellFed", "aucun plat de ce type dans les sacs")

  -- manger ET boire
  M.player.power = 150
  auras({ aura(FOOD) })
  yes("eatDrink", "Bois aussi pendant que tu manges")
  M.player.power = 560
  no("eatDrink", "mana presque plein")
  M.player.power = 150
  T:SetModuleEnabled("QuickFight", true)
  no("eatDrink", "la barre de combat a son bouton « Manger et boire »")
  T:SetModuleEnabled("QuickFight", false)
  auras({ aura({ name = "Boisson", spellId = 430, duration = 18 }) })
  M.player.power, M.player.health = 300, 400
  yes("eatDrink", "Mange aussi pendant que tu bois")
  auras({})

  -- bandage au lieu d'un repas (Bandage en laine : 161 PV, tables du client)
  M.player.health, M.player.power = 840, 500
  yes("bandage", "Bande-toi au lieu de manger : Bandage en laine rend 161 PV")
  auras({ aura({ name = "Bandage appliqué récemment", spellId = 11196, duration = 60, kind = "debuff" }) })
  no("bandage", "bandage récent")
  auras({})
  auras({})
  M.player.health = 500
  no("bandage", "il manque bien plus que ce que rend le bandage")
  M.player.health = 840
  M.skills = { { name = "Secourisme", rank = 40, max = 75 } }
  T.API.BagChanged()
  no("bandage", "secourisme trop bas pour ce bandage")
  M.skills = nil
  T.API.BagChanged()

  -- boire avant de tomber à court (mana mesuré par combat)
  fights(5, { loss = 10, mloss = 25, pet = true, grp = 0 })
  M.player.health, M.player.power = 1000, 120
  yes("drinkNow", "Bois avant le pull : il te reste moins d'un combat de mana (−25 % par combat, mesuré)")
  M.player.power = 420
  no("drinkNow", "assez de mana pour 2 combats")

  -- enchaîner au lieu de manger (vie perdue mesurée)
  C.ResetSession()
  fights(6, { loss = 15, mloss = 5, pet = true, grp = 0 })
  M.player.health, M.player.power = 800, 600
  auras({ aura(FOOD) })
  yes("chainPull", "Enchaîne : à 80 % de vie tu tiens 3 combats (−15 % par combat, mesuré)")
  M.player.health = 600
  no("chainPull", "60 % : le repas est justifié")
  auras({})

  -- ------------------------------------------------------------------ Pull et combat
  C.ResetSession()
  if hunter then
    fights(6, { loss = 40, mloss = 10, pet = true, grp = 0 })
    yes("petTank", "Laisse ton familier prendre l'aggro 2 s avant de tirer : tu perds 40 % de vie par combat")
    C.ResetSession()
    fights(6, { loss = 12, pet = true, grp = 0 })
    no("petTank", "peu de vie perdue")
  else
    no("petTank", "pas chasseur")
  end
  -- monstres trop faibles (XP de base mesurée)
  C.ResetSession()
  kills(5, { base = 60, lvl = 21, grp = 0, quest = false })
  yes("greenMobs", "Monstres trop faibles (niv 21) : 60 XP chacun, vise les niv 26 à 30 (≈ 185 XP)")
  C.kills[5].quest = true
  no("greenMobs", "un monstre de quête")
  -- vrai chemin de mesure : message d'XP (Detect) + écart d'XP
  C.ResetSession()
  M.player.xp, M.player.rested = 12000, 300
  C.ResetSession()
  M.player.xp, M.player.rested = 12370, 115
  M.FireEvent("CHAT_MSG_COMBAT_XP_GAIN", "Loup-test meurt, vous gagnez 370 points d'expérience. (+185 exp. bonus de repos)")
  M.FireEvent("PLAYER_XP_UPDATE", "player")
  local k = C.kills[#C.kills]
  check(k and k.base == 185 and k.rested == 185, "XP d'un monstre mesurée : 185 de base + 185 de repos (" .. tostring(k and k.base) .. ")")
  -- vrai chemin de mesure : un combat (vie lue hors combat avant et après)
  C.ResetSession()
  M.player.health = 1000
  C.ReadVitals()
  M.EnterCombat()
  M.Advance(3, 0.5)
  M.player.health = 650
  M.FireEvent("UNIT_HEALTH", "player")
  M.LeaveCombat()
  M.Advance(1, 0.5)
  local f = C.fights[#C.fights]
  check(f and f.loss == 35, "combat mesuré : −35 % de vie (" .. tostring(f and f.loss) .. ")")

  -- nouveau sort de chasseur (une fois)
  C.ResetSession()
  if hunter then
    ev("newTool")                      -- 1re lecture : ce que tu sais déjà n'est pas « nouveau »
    no("newTool", "rien de neuf")
    M.spells[13809] = { name = "Piège de givre", rank = "", icon = 135840, castTime = 0, known = true }
    M.FireEvent("SPELLS_CHANGED")
    yes("newTool", "Nouveau : « Piège de givre » — pose-le avant de tirer sur un groupe")
    local r = C.active.newTool
    if r and r.onShown then r.onShown() end
    no("newTool", "déjà montré une fois")
  end

  -- élite seul
  M.target = { exists = true, name = "Élite-test", level = 31, classification = "elite", hostile = true, health = 3000, healthMax = 3000 }
  yes("eliteSolo", "Élite niv 31 et tu es seul : fais-le à deux, ou garde tes sorts à recharge pour lui")
  if hunter then                       -- Tir rapide nommé seulement quand il est appris (niveau 26)
    M.spells[3045] = { name = "Tir rapide", rank = "", icon = 132208, castTime = 0, known = true }
    yes("eliteSolo", "Élite niv 31 et tu es seul : fais-le à deux, ou garde Tir rapide pour lui")
    M.spells[3045] = nil
  end
  M.target.level = 28
  if hunter then no("eliteSolo", "élite à ton niveau avec ton familier") end
  M.target.level = 31
  M.group = { { name = "Amie-test", class = "MAGE", level = 28, online = true } }
  no("eliteSolo", "en groupe")
  M.group = {}
  M.target = { exists = false }

  -- ------------------------------------------------------------------ Familier
  local function petBar(growl, enabled, mode)
    rawset(_G, "GetPetActionInfo", function(i)
      if i == 1 then return "PET_ACTION_ATTACK", "PET_ATTACK_TEXTURE", true, false, false, false, nil end
      if i == 4 and growl then return "Grondement", 132270, false, false, true, enabled, 2649 end
      if i == 8 and mode then return "PET_MODE_" .. mode:upper(), "PET_MODE_TEXTURE", true, true, false, false, nil end
      return nil
    end)
    M.FireEvent("PET_BAR_UPDATE")
  end
  if hunter then
    petBar(true, false, "defensive")
    yes("growlAuto", "Mets « Grondement » en automatique (clic droit sur son bouton)")
    C.ResetSession()
    fights(6, { loss = 40, pet = true, grp = 0 })
    no("petTank", "« Grondement en auto » le dit mieux")
    petBar(true, true, "defensive")
    no("growlAuto", "déjà en automatique")
    -- donjon avec un tank : Grondement coupé, familier défensif
    M.instance = true
    M.group = { { name = "Tank-test", class = "WARRIOR", level = 28, online = true },
      { name = "Mage-test", class = "MAGE", level = 28, online = true } }
    yes("growlDungeon", "Coupe « Grondement » en donjon (clic droit dessus) : il vole l'aggro à Tank-test")
    no("growlAuto", "en donjon")
    M.group = { { name = "Mage-test", class = "MAGE", level = 28, online = true },
      { name = "Voleur-test", class = "ROGUE", level = 28, online = true } }
    no("growlDungeon", "pas de tank dans le groupe")
    petBar(true, true, "aggressive")
    yes("petDefensive", "Passe ton familier en défensif : en agressif il attaque des packs tout seul")
    petBar(true, true, "defensive")
    no("petDefensive", "déjà défensif")
    M.instance, M.group = false, {}
    -- familier en retard
    M.pet.level = 24
    yes("petLevel", "Ton familier a 4 niveaux de retard (niv 24)")
    M.pet.level = 27
    no("petLevel", "1 niveau d'écart")
    M.pet.level = 28
    rawset(_G, "GetPetActionInfo", function() return nil end)
  end

  -- ------------------------------------------------------------------ Quêtes et trajet
  M.player.rested = 9000
  yes("restedKill", "Repos : tes ≈ 48 prochains monstres valent double, tue ceux sur ton chemin")
  M.player.resting = true
  T:PlayerState(true)
  no("restedKill", "en ville / à l'auberge")
  M.player.resting = false
  T:PlayerState(true)
  M.player.rested = 2000
  no("restedKill", "peu de repos")
  M.player.rested = 0
  if hunter then
    C.SetMoving(M.now - 20)
    yes("cheetah", "Passe en « Aspect du guépard » pour marcher : +30 % de vitesse")
    auras({ aura({ name = "Aspect du guépard", spellId = 5118, duration = 0 }) })
    no("cheetah", "déjà en Guépard")
    auras({})
    M.mounted = true
    T:PlayerState(true)
    no("cheetah", "à monture")
    M.mounted = false
    T:PlayerState(true)
    C.SetMoving(nil)
    no("cheetah", "à l'arrêt")
  end
  -- quêtes finies = niveau
  M.player.xp, M.player.xpMax = 25000, 26000
  Q.lastList = { { id = 9301, title = "Finie-test", level = 27, complete = true, xp = 1500, obj = {} } }
  yes("turninLevel", "Rends ta quête finie : niveau 29 d'un coup, +1 point de talent")   -- 29 impair : rien chez le maître
  M.player.level = 29
  yes("turninLevel", "Rends ta quête finie : niveau 30 d'un coup, puis ton maître de classe")
  M.player.level = 28
  M.player.xp = 12000
  no("turninLevel", "pas assez d'XP à rendre")
  -- quête rendue trop tard (règle Classic)
  M.player.xp = 24500
  Q.lastList = { { id = 9302, title = "Vieille-test", level = 22, complete = true, xp = 2000, obj = {} } }
  yes("questPenalty", "Rends « Vieille-test » avant le niveau 29 : après, elle rapportera 25 % d'XP en moins (−500)")
  M.player.xp = 10000
  no("questPenalty", "le niveau suivant est loin")
  M.player.xp = 24500
  Q.lastList = { { id = 9303, title = "Récente-test", level = 26, complete = true, xp = 2000, obj = {} } }
  no("questPenalty", "quête à 2 niveaux : pas de perte")
  Q.lastList = {}

  -- ------------------------------------------------------------------ Argent et sacs
  if hunter then
    local inv18, inv0 = M.inventory[18], M.inventory[0]
    M.AddItem(2504, { name = "Arc-test", classID = 2, subclassID = 2, equipLoc = "INVTYPE_RANGED", minLevel = 20 })
    M.inventory[18] = { id = 2504, dur = 50, durMax = 60 }
    M.inventory[0] = { id = 2515, count = 600 }
    yes("ammoTier", "Passe à « Flèche rasoir » (niv 25) : +4 dégâts/s pour 3 pa les 200")
    M.player.level = 20
    no("ammoTier", "niveau 20 : la Flèche pointue est le bon palier")
    M.player.level = 28
    M.inventory[0] = { id = 3030, count = 600 }
    no("ammoTier", "déjà en Flèche rasoir")
    M.inventory[18], M.inventory[0] = inv18, inv0
  end
  local bag4 = M.bags[4]
  M.bags[4] = nil
  T.API.BagChanged()
  yes("emptyBag", "Équipe un sac dans ta case de sac vide")
  M.bags[4] = bag4
  T.API.BagChanged()
  no("emptyBag", "4 sacs équipés")

  -- ------------------------------------------------------------------ Duo et groupe
  C.ResetSession()
  M.group = { { name = "Amie-test", class = "MAGE", level = 28, online = true }, { name = "Ami-test", class = "WARRIOR", level = 28, online = true } }
  kills(6, { base = 40, lvl = 28, grp = 3, quest = false })
  yes("groupXP", "À 3, ces monstres hors quête vous rapportent 40 XP chacun : allez sur vos quêtes")
  M.group = {}
  no("groupXP", "seul")

  -- ------------------------------------------------------------------ choix : un seul conseil, le plus rentable
  reset()
  M.bags[4] = nil
  T.API.BagChanged()
  if hunter then M.pet.level = 24 end                    -- petLevel (45) + emptyBag (40)
  M.player.rested = 9000                                 -- restedKill (45)
  C.RunAll()
  local function coachLines()
    local out = {}
    for _, e in ipairs(T:GetTabLines("now")) do if tostring(e.id or ""):find("^Coach:") then out[#out + 1] = e end end
    return out
  end
  -- (4.1 LAYOUT) d'office : seulement sur sa page (avis non demandé), ni dans « Maintenant » ni sur la carte
  check(#coachLines() == 0, "d'office : pas de conseil pro dans « Maintenant »")
  local own
  for k, e in pairs(S) do if tostring(k):find("^Coach:") and e.ownPage == "coach" then own = e end end
  check(own ~= nil and C.CardId() == nil, "d'office : le conseil pro sur la page « Conseils pro » seulement")
  -- le joueur coche « Aussi dans la page Quêtes et la carte » : le comportement d'avant
  C.db.onScreen = true
  for k in pairs(S) do if tostring(k):find("^Coach:") then T:ClearSuggestion(k) end end
  C.ResetCooldowns()
  C.RunAll()
  local lines = coachLines()
  check(#lines == 1, "un seul conseil pro dans « Maintenant » (" .. #lines .. ")")
  local first = C.Shown()
  check(first == "restedKill" or first == "petLevel", "le plus rentable d'abord (" .. tostring(first) .. ")")
  local e1 = S["Coach:" .. tostring(first)]
  check(e1 and e1.prio == 2 and e1.tab == "now" and type(e1.tooltip) == "table" and tostring(e1.tooltip[1]):find("^Pourquoi : "),
    "ligne prio 2, infobulle « Pourquoi : … »")
  check(e1 and e1.onClick and e1.clickHint, "clic = la page Conseils pro")
  -- carte « Prochaine étape » (HUD) : le conseil y passe en avis « i », jamais à la place du mode sûr
  if W and W.CardModel then
    local rows = W.CardModel(3)
    check(rows[1] and rows[1].notice and rows[1].entry and rows[1].entry.id == "Coach:" .. tostring(first),
      "carte : le conseil pro en avis « i » (" .. tostring(rows[1] and rows[1].entry and rows[1].entry.id) .. ")")
    T:Suggest("core:safe", "Mode sûr : les fonctions bêta sont à activer dans le menu", 2, { ttl = 30, tab = "now", kind = "always" })
    C.Pick()
    local hide0 = C.Shown()
    T:ClearSuggestion("Coach:" .. tostring(hide0))
    C.ResetCooldowns()
    reset()
    M.player.rested = 9000
    T:Suggest("core:safe", "Mode sûr : les fonctions bêta sont à activer dans le menu", 2, { ttl = 30, tab = "now", kind = "always" })
    C.RunAll()
    check(C.Shown() ~= nil and C.CardId() == nil, "carte : l'avis « mode sûr » garde sa place (pas de conseil pro sur la carte)")
    T:ClearSuggestion("core:safe")
    C.db.card = false
    C.RunAll()
    check(C.CardId() == nil, "option : pas sur la carte")
    C.db.card = true
    reset()
    M.bags[4] = nil
    T.API.BagChanged()
    if hunter then M.pet.level = 24 end
    M.player.rested = 9000
    C.RunAll()
    first = C.Shown()
  end
  -- un urgent passe devant ; un ordinaire attend MIN_SHOW
  M.Advance(5, 1)
  C.RunAll()
  check(C.Shown() == first, "il reste affiché au moins 20 s")
  -- rien en combat : la ligne s'efface au pull, rien ne revient pendant le combat
  M.EnterCombat()
  check(#coachLines() == 0, "en combat : plus de conseil pro")
  C.Pick()
  check(#coachLines() == 0 and C.Evaluate("emptyBag") == nil, "en combat : rien d'évalué ni de montré")
  M.LeaveCombat()
  M.Advance(2, 0.5)
  -- interrupteur : coupé = jamais montré, page à jour
  C.SetTip("emptyBag", false)
  C.SetTip("restedKill", false)
  C.SetTip("petLevel", false)
  C.ResetCooldowns()
  C.RunAll()
  check(#coachLines() == 0, "conseils coupés : rien dans « Maintenant »")
  C.SetTip("emptyBag", true)
  C.SetTip("restedKill", true)
  C.SetTip("petLevel", true)
  -- « page seulement »
  C.db.nowLine = false
  C.RunAll()
  check(#coachLines() == 0, "option « page seulement » : rien dans « Maintenant »")
  C.db.nowLine = true

  -- pas de répétition : 20 min avec 3 conseils toujours vrais → chacun une fois, puis son temps de repos
  reset()
  M.bags[4] = nil
  T.API.BagChanged()
  if hunter then M.pet.level = 24 end
  M.player.rested = 9000
  local seen, changes, withLine, samples, last = {}, 0, 0, 0, nil
  for _ = 1, 600 do
    M.Advance(2, 1)
    local l = coachLines()
    samples = samples + 1
    if #l > 0 then withLine = withLine + 1 end
    check(#l <= 1, "jamais deux conseils pro à la fois")
    local id = C.Shown()
    if id ~= last and id then changes = changes + 1 seen[id] = (seen[id] or 0) + 1 end
    last = id
  end
  local nseen = 0
  for _, v in pairs(seen) do nseen = nseen + 1 check(v == 1, "chaque conseil une seule fois en 20 min (" .. v .. ")") end
  check(changes <= 6, "au plus 6 conseils en 20 min (" .. changes .. ")")
  check(nseen >= 2, "les conseils vrais passent chacun leur tour (" .. nseen .. ")")
  check(withLine / samples <= 0.35, ("conseil pro à l'écran %d %% du temps (≤ 35 %%)"):format(math.floor(withLine * 100 / samples)))
  -- masqué au clic droit : ne revient pas
  reset()
  M.bags[4] = nil
  T.API.BagChanged()
  C.RunAll()
  check(C.Shown() == "emptyBag", "case vide montrée")
  T:DismissSuggestion("Coach:emptyBag")
  C.ResetCooldowns()
  C.RunAll()
  check(C.Shown() ~= "emptyBag" and S["Coach:emptyBag"] == nil, "masqué par le joueur : ne revient pas")
  M.bags[4] = bag4
  T.API.BagChanged()

  -- ------------------------------------------------------------------ page « Conseils pro »
  if W then
    reset()
    M.player.rested = 9000                         -- repos : vrai pour toutes les classes
    local rows, headers = {}, {}
    local Ly = { text = function() end, empty = function(s) rows[#rows + 1] = { text = s } end,
      header = function(s) headers[#headers + 1] = s return true end,
      row = function(e) rows[#rows + 1] = e end, buttons = function() end, gap = function() end }
    C.RenderPage(Ly)
    check(headers[1] == "En ce moment" and #headers == (hunter and 9 or 8),
      "page : « En ce moment » + les thèmes (Familier : chasseurs) (" .. #headers .. ")")
    local now1, tipRow, techN = nil, nil, 0
    for _, r in ipairs(rows) do
      local t = tostring(r.text)
      if t:find("prochains monstres valent double", 1, true) and r.right ~= "ON" and r.right ~= "actif" then now1 = r end
      if t:find("^Repos d'XP : tue les monstres") then tipRow = r end
      if r.right == "technique" then techN = techN + 1 end
      check(not t:find("?", 1, true) and not t:find("nil", 1, true), "page : ligne propre : " .. t)
    end
    check(now1 ~= nil, "page : le conseil vrai en ce moment est en tête")
    check(tipRow and tipRow.right == "actif" and tipRow.onClick, "page : ligne du conseil avec son état et un clic")
    check(techN >= (hunter and 35 or 15), "page : techniques à appliquer (" .. techN .. ")")
    if tipRow then
      T:UserAction(tipRow.onClick)
      check(C.db.restedKill == false, "page : un clic coupe le conseil")
      C.SetTip("restedKill", true)
    end
    W.Open("coach")
    check(W.CurrentPage() == "coach" and #W.lastLay.rects >= 8, "fenêtre : page « Conseils pro » dessinée (" .. tostring(W.CurrentPage()) .. ", " .. #W.lastLay.rects .. ")")
    for _, r in ipairs(W.lastLay.rects) do
      check(not r.label:find("Erreur d'affichage", 1, true), "fenêtre : pas d'erreur d'affichage")
    end
    W.Close()
  end

  -- ------------------------------------------------------------------ rien dans le chat, aucun son, aucune action bloquée
  for _, l in ipairs(M.output) do check(not tostring(l):find("Conseils pro", 1, true), "rien dans le chat : " .. tostring(l)) end
  M.bags[4] = bag4
  T.API.BagChanged()
  Q.lastList = savedList
  if qfWas then T:SetModuleEnabled("QuickFight", true) end
  if quickWas then T:SetModuleEnabled("Quick", true) end
  reset()
  check(#M.blocked == 0, "aucune action bloquée (" .. #M.blocked .. ")")
end
