-- tour_scenario.lua — ONBOARD: « Visite guidée » + contextual one-time tips + /tai aide. Run with --no-storm:
--   lua5.1 tests/wow/harness.lua -q --no-storm --flavor forever --class HUNTER --scenario tests/wow/tour_scenario.lua wow_addon/TreeAI
-- Asserts: first run waits for the start-up wizard and a calm moment (never in combat); every shown step points at
-- a REAL visible frame (minimap button, panel / card, compass, Hub rows « Fonctions bêta » / « Erreurs »…) and
-- steps without a target are skipped (counter stays honest); combat / death hide the bubble and it comes back;
-- « Passer » / « Terminer » close everything (Hub menu included) and are remembered (TreeAIDB.tour, no second
-- first-run, /reload mid-tour resumes); replay from the Hub entry, Options button, /tai visite (after combat);
-- tips: once each, ≤ 1 per 10 min (server clock), never in combat nor during the tour, « Couper les astuces » =
-- forever; /tai aide = a bubble on the minimap button (/tai aide tout = the old list); no chat during all that.
return function(M, ctx)
  local T = TreeAI
  local check, fire = ctx.check, ctx.fire
  local Tr = T.Tour
  check(Tr and Tr.Start and Tr.STEPS and Tr.TIPS, "module Tour chargé")
  if not Tr then return end
  check(T.SAFE_MODULES.Tour and not T:IsBeta("Tour"), "Tour fait partie du mode sûr (allumé pour tout le monde)")
  check(T.MODULE_INFO.Tour and T.MODULE_INFO.Tour.lite, "Tour : MODULE_INFO (léger)")
  check(#Tr.STEPS >= 6 and #Tr.STEPS <= 8, "6 à 8 étapes au plus (" .. #Tr.STEPS .. ")")
  local H = T.Hub
  local function box() return Tr.Box() end
  local function shown() local b = Tr.Box() return b and b:IsShown() and true or false end
  local function stepId() return Tr.active and Tr.active.step and Tr.active.step.id end
  local function hubEntry(id)
    for _, e in ipairs(H.Entries()) do if e.id == id then return e end end
  end
  local function chatSince(n) return #M.output - n end
  local function finishTour()
    local g = 0
    while Tr.active and g < 12 do g = g + 1; box().secondary:Click("LeftButton") end
  end
  -- the bubble must never appear in combat: watch every Show of our three frames
  local inCombatShows = 0
  local function watchShow(f)
    if not f then return end
    local orig = f.Show
    f.Show = function(self, ...) if M.inCombat then inCombatShows = inCombatShows + 1 end return orig(self, ...) end
  end

  -- 0. contrats ----------------------------------------------------------------------------------------------
  local help = T:GetSlashHelp()
  local start = help[1] and help[1].items or {}
  check(start[1] and start[1].cmd == "aide" and start[2] and start[2].cmd == "visite",
    "aide groupée : « visite » juste après « aide » dans Démarrer")
  check(start[1] and start[1].help:find("pastille", 1, true), "aide : /tai aide montre la pastille")
  local te = hubEntry("tour")
  check(te and te.group == "reglages" and te.title == "Visite guidée" and te.fn, "menu de la pastille : entrée « Visite guidée »")

  -- 1. première fois : l'assistant d'abord, puis le premier moment calme (jamais en combat) ---------------------
  TreeAIDB.tour = nil
  check(not Tr.Done(), "1re fois : visite pas encore vue")
  te = hubEntry("tour")
  check(te and te.state and te.state:find("nouveau", 1, true), "entrée du menu marquée « nouveau » tant qu'elle n'est pas vue")
  local out0 = #M.output
  M.Advance(10, 0.5)
  -- (5.1 LOOK) plus rien ne s'ouvre tout seul : ni l'assistant (une petite ligne « Régler · Plus tard » à la place),
  -- ni la visite — c'est le joueur qui les lance (ligne, menu de la pastille, /tai bienvenue, /tai visite)
  local wiz = T.GetWizard and T:GetWizard()
  check(not (wiz and wiz:IsShown()), "1er lancement : l'assistant ne s'ouvre pas tout seul")
  local hello = rawget(_G, "TreeAIHello")
  check(hello and hello:IsShown(), "1er lancement : la petite ligne « TreeAI est prêt » à la place")
  M.Hw(function() TreeAIHelloGo:Click("LeftButton") end)
  wiz = T.GetWizard and T:GetWizard()
  check(wiz and wiz:IsShown() and not hello:IsShown(), "« Régler (1 min) » : l'assistant s'ouvre au clic")
  check(not Tr.active and not shown(), "pas de visite pendant l'assistant")
  if wiz and wiz.skip then wiz.skip:Click("LeftButton") end
  check(T.db.onboarded, "assistant passé")
  out0 = #M.output   -- (« Prêt ! » de l'assistant)
  M.EnterCombat()
  M.Advance(20, 0.5)
  check(not Tr.active and not shown(), "1re fois : jamais en combat")
  M.LeaveCombat()
  M.Advance(60, 0.5)
  check(not Tr.active and not shown() and Tr.AUTO == false, "la visite ne démarre plus toute seule, même au calme")
  te = hubEntry("tour")
  check(te and te.fn, "elle reste proposée dans le menu de la pastille")
  M.Hw(function() te.fn() end)
  M.Advance(1, 0.5)
  check(Tr.active and shown() and Tr.mode == "tour", "visite montrée au clic du joueur")
  if not Tr.active then return end
  watchShow(box()); watchShow(Tr.UI.glow); watchShow(Tr.UI.arrow)
  check(stepId() == "hub", "étape 1 = la pastille de la minimap")
  check(Tr.curTarget == H.Button() and Tr.UI.glow:IsShown() and Tr.UI.arrow:IsShown(),
    "étape 1 : flèche + halo sur le VRAI bouton de la minimap")
  local N = #Tr.active.plan
  check(N >= 4 and N <= #Tr.STEPS, "étapes montrables : " .. N)
  check(box().count:GetText() == "1/" .. N, "compteur 1/" .. N .. " (" .. tostring(box().count:GetText()) .. ")")
  check(tostring(box().note:GetText() or ""):find("Visite guidée", 1, true), "étape 1 : comment la rejouer")
  for _, f in ipairs({ box(), Tr.UI.glow, Tr.UI.arrow }) do
    local p = f.__points[1]
    check(p and (p[2] == UIParent or p[2] == nil) and f:GetParent() == UIParent,
      (f:GetName() or "?") .. " posé sur UIParent, jamais ancré sur la cible (pas de protection implicite)")
  end
  check(T.db.tour and T.db.tour.step == 1, "étape en cours mémorisée (reprise après /reload)")

  -- 2. étape 2, combat puis mort : la bulle se cache et revient au même endroit --------------------------------
  box().primary:Click("LeftButton")
  local second = stepId()
  check(second and second ~= "hub" and box().count:GetText() == "2/" .. #Tr.active.plan, "Suivant : étape 2 (" .. tostring(second) .. ")")
  M.EnterCombat()
  M.Advance(1, 0.5)
  check(not shown() and not Tr.UI.glow:IsShown() and Tr.paused == "combat", "combat : bulle et halo cachés")
  M.Advance(10, 0.5)
  check(not shown(), "combat : rien ne revient pendant le combat")
  M.LeaveCombat()
  M.Advance(4, 0.5)
  check(shown() and stepId() == second and not Tr.paused, "après le combat : la même étape revient")
  M.player.dead = true
  fire("PLAYER_DEAD")
  check(not shown() and Tr.paused == "dead", "mort : bulle cachée")
  M.player.dead = false
  fire("PLAYER_ALIVE")
  M.Advance(3, 0.5)
  check(shown() and stepId() == second, "vivant : la même étape revient")

  -- 3. toutes les étapes : chaque cible est un vrai cadre visible ; Fonctions bêta / Erreurs = lignes du menu ------
  local visited, guard = { "hub", second }, 0
  local hubSteps = 0
  while Tr.active and guard < 12 do
    guard = guard + 1
    local a = Tr.active
    check(a.target and a.target:IsVisible(), "étape « " .. tostring(stepId()) .. " » : cible visible")
    if stepId() == "beta" or stepId() == "bug" then
      hubSteps = hubSteps + 1
      check(H.IsOpen() and H.mode == "main", "étape « " .. stepId() .. " » : le menu de la pastille est ouvert")
      local want = stepId() == "beta" and "profil" or "erreurs"
      local row
      for _, e in ipairs(H.layout) do if e.kind == "item" and e.id == want then row = e.button end end
      check(row and a.target == row, "étape « " .. stepId() .. " » : la flèche montre la ligne « " .. want .. " » du menu")
      if stepId() == "beta" and row then
        row:Click("LeftButton")   -- le joueur essaie : la liste bêta remplace le menu principal
        M.Advance(1.5, 0.5)
        check(H.mode == "beta" and not Tr.UI.glow:IsShown() and shown(), "le joueur ouvre la liste bêta : halo retiré, bulle gardée")
      end
    elseif stepId() == "compass" then
      check(a.target == rawget(_G, "TreeAICompassFrame"), "étape boussole : le cadre de la boussole")
    end
    local last = a.pos >= #a.plan
    if last then
      check(box().primary.label and box().primary.label:GetText() == "Terminer", "dernière étape : bouton « Terminer »")
      check(tostring(box().note:GetText() or ""):find("C'est tout", 1, true), "dernière étape : « C'est tout ! » + comment la rejouer")
    end
    box().primary:Click("LeftButton")
    if Tr.active then visited[#visited + 1] = stepId() end
  end
  check(hubSteps == 2, "Fonctions bêta et Erreurs montrées dans le menu (" .. hubSteps .. ")")
  check(not Tr.active and not shown() and not H.IsOpen(), "Terminer : tout est refermé (menu de la pastille compris)")
  check(Tr.Done() and T.db.tour.done == Tr.TOUR_V and T.db.tour.how == "done" and T.db.tour.step == nil,
    "visite terminée mémorisée pour le compte")
  check(inCombatShows == 0, "jamais affichée en combat")
  check(chatSince(out0) == 0, "visite : rien dans le chat (" .. chatSince(out0) .. ")")
  local seenIds = {}
  for _, id in ipairs(visited) do seenIds[id] = true end
  -- (4.1 STRIP) the card is off by default: the tour shows the « boutons rapides » (quick) instead of it
  check(seenIds.hub and (seenIds.next or seenIds.quick) and seenIds.beta and seenIds.bug, "étapes vues : " .. table.concat(visited, ", "))

  -- 4. persistance : pas de deuxième « première fois » ; /reload en pleine visite = reprise ------------------------
  te = hubEntry("tour")
  check(te and not te.state, "entrée du menu : plus « nouveau » une fois vue")
  Tr:OnEnable()   -- comme une nouvelle connexion
  M.Advance(30, 0.5)
  check(not Tr.active and not shown(), "déjà vue : pas reproposée à la connexion suivante")
  TreeAIDB.tour = { step = 4, seen = {} }   -- /reload pendant l'étape 4
  Tr:OnEnable()
  M.Advance(15, 0.5)
  check(not Tr.active, "(5.1) reprise : jamais pendant la 1re minute")
  M.Advance(60, 0.5)
  local idx
  for i, st in ipairs(Tr.STEPS) do if st.id == stepId() then idx = i end end
  check(Tr.active and idx and idx >= 4, "après /reload : la visite reprend à l'étape mémorisée (" .. tostring(stepId()) .. ")")
  box().secondary:Click("LeftButton")
  check(not Tr.active and not shown() and Tr.Done() and T.db.tour.how == "skip", "Passer : fermée et mémorisée")

  -- 5. une étape sans cible est sautée, le compteur reste juste ; une cible enregistrée passe d'abord ---------------
  local function planHas(id) for _, i in ipairs(Tr.Plan()) do if Tr.STEPS[i].id == id then return true end end end
  T:SetModuleEnabled("ZonePlan", false)
  check(not planHas("zone"), "zone de travail coupée : étape « zone » hors du plan")
  T:SetModuleEnabled("ZonePlan", true)
  local compassStep
  for _, st in ipairs(Tr.STEPS) do if st.id == "compass" then compassStep = st end end
  local origFind = compassStep.find
  compassStep.find = function() return nil end
  Tr.Start("test")
  local n0, skipped, lastCount, hadCompass = #Tr.active.plan, true, nil, false
  for _, i in ipairs(Tr.active.plan) do if Tr.STEPS[i].id == "compass" then hadCompass = true end end
  while Tr.active do
    if stepId() == "compass" then skipped = false end
    lastCount = box().count:GetText()
    box().primary:Click("LeftButton")
  end
  check(skipped, "boussole introuvable : étape sautée")
  check(hadCompass and lastCount == (n0 - 1) .. "/" .. (n0 - 1),
    "étape sautée : le compteur reste juste (" .. tostring(lastCount) .. " pour " .. n0 .. " prévues)")
  compassStep.find = origFind
  local fake = CreateFrame("Frame", nil, UIParent)
  fake:SetSize(60, 40); fake:Show()
  Tr.Target("compass", function() return fake end)
  check(Tr.Resolve("compass") == fake, "cible enregistrée (T.Tour.Target) : passe avant le repère intégré")
  fake:Hide()
  check(Tr.Resolve("compass") ~= fake, "cible enregistrée cachée : repère intégré")
  Tr.targets.compass = nil
  -- (4.0) la fenêtre TreeAI remplace le panneau par défaut : on l'affiche pour tester sa ligne « Zone »
  if T.ShowPanel then T:ShowPanel() end
  local p = T:GetPanel()
  local row = CreateFrame("Button", nil, p)
  row.entry = { id = "zone:pick", text = "Zone : Auto ▾" }
  row:Show()
  check(Tr.FindRow("zone:pick") == row and Tr.Resolve("zone") == row, "ligne « Zone ▾ » du panneau trouvée par son id")
  row:Hide()
  check(Tr.FindRow("zone:pick") == nil, "ligne cachée : pas de cible")
  -- (4.0) la fenêtre TreeAI enregistre ses cibles : la carte « Prochaine étape » et la ligne « Zone » du trajet
  local Wn = T.Window
  if Wn and Wn._active then
    check(type(Tr.targets.next) == "function" and type(Tr.targets.zone) == "function", "fenêtre : cibles « next » et « zone » enregistrées")
    local cardF = rawget(_G, "TreeAICard")
    if cardF and cardF:IsVisible() then check(Tr.Resolve("next") == cardF, "étape « next » : la carte de la fenêtre") end
  end

  -- 6. rejouer : menu de la pastille, options, /tai visite (pas en combat) ---------------------------------------
  H.Toggle()
  local rowTour
  for _, e in ipairs(H.layout) do if e.id == "tour" then rowTour = e end end
  check(rowTour and rowTour.button, "menu ouvert : ligne « Visite guidée »")
  if rowTour then rowTour.button:Click("LeftButton") end
  check(Tr.active and stepId() == "hub" and not H.IsOpen(), "clic dans le menu : la visite repart de la pastille")
  finishTour()
  local captured
  local P = { button = function(label, _, fn, textFn) captured = { fn = fn, textFn = textFn } return {} end }
  Tr.OptionsButton(P)
  check(captured and captured.textFn():find("Visite guidée", 1, true), "options : bouton « Visite guidée »")
  local sp = CreateFrame("Frame", nil, UIParent)
  sp:Show()
  rawset(_G, "SettingsPanel", sp)
  captured.fn()
  check(not Tr.active and Tr.pending == "options" and captured.textFn():find("Ferme les options", 1, true),
    "options ouvertes : la visite attend leur fermeture")
  sp:Hide()
  M.Advance(4, 0.5)
  check(Tr.active and stepId() == "hub", "options fermées : la visite démarre")
  rawset(_G, "SettingsPanel", nil)
  finishTour()
  M.EnterCombat()
  M.RunSlash("/tai visite")
  check(not Tr.active and not shown(), "/tai visite en combat : rien")
  M.LeaveCombat()
  check(Tr.active and shown(), "/tai visite : lancée dès la fin du combat")
  finishTour()

  -- 7. /tai aide : une bulle sur la pastille ; /tai aide tout = toutes les commandes --------------------------------
  local outA = #M.output
  M.RunSlash("/tai aide")
  check(Tr.mode == "pointer" and shown() and Tr.curTarget == H.Button(), "/tai aide : bulle sur la pastille de la minimap")
  check(box().head:GetText() == "Aide" and tostring(box().body:GetText()):find("pastille", 1, true), "bulle d'aide : courte, montre la pastille")
  check(chatSince(outA) == 0, "/tai aide : rien dans le chat")
  M.Advance(Tr.TIP_SHOW + 2, 0.5)
  check(not shown() and Tr.mode == nil, "bulle d'aide : s'efface seule")
  M.RunSlash("/tai")
  check(Tr.mode == "pointer", "/tai seul : même bulle")
  box().primary:Click("LeftButton")
  check(Tr.active and stepId() == "hub", "bulle d'aide → « Visite guidée »")
  finishTour()
  M.RunSlash("/tai aide tout")
  local hf = T.GetHelpFrame and T:GetHelpFrame()
  check(hf and hf:IsShown() and not shown(), "/tai aide tout : la liste complète des commandes")
  if hf then hf:Hide() end
  M.EnterCombat()
  outA = #M.output
  M.RunSlash("/tai aide")
  check(not shown() and chatSince(outA) == 1 and ctx.out("pastille de la minimap"), "/tai aide en combat : une ligne de chat, aucun cadre")
  M.LeaveCombat()
  M.Advance(2, 0.5)

  -- 8. astuces : une fois chacune, ≤ 1 / 10 min, jamais en combat ni pendant la visite ----------------------------
  local A = T.modules.Auction
  -- (4.0.1) calme : après la visite, les astuces sont coupées par défaut (une case des options les rallume)
  check(Tr.db.tips == false and Tr.Done(), "astuces coupées par défaut après la visite")
  Tr.db.tips = true
  Tr:OnOptionChanged("tips")
  check(Tr.db.tips ~= false and Tr.Done(), "astuces allumées")
  local shows = {}
  local origShowTip = Tr.ShowTip
  Tr.ShowTip = function(tip)
    shows[#shows + 1] = { id = tip.id, w = GetServerTime(), combat = M.inCombat }
    return origShowTip(tip)
  end
  local function waitTip(maxS)
    for _ = 1, maxS do
      if Tr.mode == "tip" then return Tr.tip and Tr.tip.id end
      M.Advance(1, 0.5)
    end
    return Tr.mode == "tip" and Tr.tip and Tr.tip.id or nil
  end
  TreeAIDB.tour.last = 0
  Tr.Start("test")
  M.SetAuctionHouse(true)
  M.Advance(8, 0.5)
  check(Tr.mode == "tour" and #shows == 0, "pendant la visite : aucune astuce")
  finishTour()
  local finishedAt = GetServerTime()
  local id1 = waitTip(200)
  check(id1 == "ah", "HV ouvert : astuce « Première fois à l'HV » (" .. tostring(id1) .. ")")
  check(shows[1] and shows[1].w - finishedAt >= 110, "première astuce : pas avant 2 min après la visite")
  if Tr.mode == "tip" then
    check(box().head:GetText() == "Première fois à l'HV" and tostring(box().body:GetText()):find("rafale", 1, true),
      "astuce HV : le mode rafale = une action par appui")
    check(A and A.win and (Tr.curTarget == A.win.rafBtn or Tr.curTarget == A.win), "astuce HV : flèche sur la fenêtre TreeAI de l'HV")
  end
  M.Advance(2, 0.5)
  M.EnterCombat()
  M.Advance(1, 0.5)
  check(not shown() and Tr.mode == nil, "combat : l'astuce disparaît")
  check(not (TreeAIDB.tour.seen or {}).ah, "astuce coupée trop tôt : pas comptée comme vue")
  M.Advance(10, 0.5)
  M.LeaveCombat()
  M.Advance(60, 0.5)
  check(#shows == 1, "≤ 1 astuce / 10 min : rien tout de suite")
  local id2 = waitTip(Tr.TIP_GAP + 30)
  check(id2 == "ah" and shows[2] and shows[2].w - shows[1].w >= Tr.TIP_GAP, "10 min plus tard : l'astuce HV revient")
  if Tr.mode == "tip" then box().primary:Click("LeftButton") end
  check(TreeAIDB.tour.seen.ah and not shown(), "OK : astuce HV vue pour de bon (mémorisée)")
  M.SetAuctionHouse(false)
  M.group = { { name = "Amie", class = "PRIEST", level = 27, health = 700, healthMax = 800, online = true } }
  fire("GROUP_ROSTER_UPDATE")
  M.Advance(60, 0.5)
  check(#shows == 2, "en groupe : pas d'autre astuce avant 10 min")
  local id3 = waitTip(Tr.TIP_GAP + 30)
  check(id3 and id3 ~= "ah" and shows[3] and shows[3].w - shows[2].w >= Tr.TIP_GAP,
    "10 min plus tard : une autre astuce (" .. tostring(id3) .. ")")
  if id3 == "group" then
    check(tostring(box().body:GetText()):find("Groupe", 1, true) and Tr.curTarget == H.Button(), "astuce groupe : la page Groupe, flèche sur la pastille")
  end
  local outT = #M.output
  if Tr.mode == "tip" then box().secondary:Click("LeftButton") end
  check(Tr.db.tips == false and not shown(), "« Couper les astuces » : coupées")
  check(id3 and TreeAIDB.tour.seen[id3], "astuce coupée = vue")
  T._modErrors = T._modErrors or {}
  T._modErrors.Fake = { n = 1, last = "boom", t = 0 }
  local before = #shows
  M.Advance(Tr.TIP_GAP + 60, 1)
  check(#shows == before and Tr.mode == nil and not shown(), "astuces coupées : plus jamais")
  check(chatSince(outT) == 0, "astuces : rien dans le chat")
  for k = 2, #shows do check(shows[k].w - shows[k - 1].w >= Tr.TIP_GAP, "écart entre astuces ≥ 10 min") end
  for _, sh in ipairs(shows) do check(not sh.combat, "astuce jamais montrée en combat") end
  check(inCombatShows == 0, "bulle jamais affichée en combat (astuces comprises)")
  Tr.ShowTip = origShowTip
  Tr.ResetTips()
  check(Tr.db.tips == true and next(TreeAIDB.tour.seen) == nil, "« Revoir les astuces » : remises à zéro")
  T._modErrors.Fake = nil
  M.group = {}
  fire("GROUP_ROSTER_UPDATE")
  Tr.db.tips = false
  M.Advance(2, 0.5)

  -- 9. module coupé : plus rien (ni bulle, ni entrée du menu, ni /tai aide détourné) --------------------------------
  T:SetModuleEnabled("Tour", false)
  check(not hubEntry("tour") and not shown(), "module coupé : ni entrée de menu, ni bulle")
  M.RunSlash("/tai aide")
  hf = T:GetHelpFrame()
  check(hf and hf:IsShown() and not shown(), "module coupé : /tai aide = l'ancienne page « Tout »")
  if hf then hf:Hide() end
  T:SetModuleEnabled("Tour", true)
  Tr.db.tips = true
end
