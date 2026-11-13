-- Harness scenario (POLISH): anti-bruit (dédoublonnage par sens, budget « Maintenant », encadré ≤ 1 / 20 s,
-- combat), préréglages, assistant 3 étapes, page « Tout » cliquable, modules on/off en direct, profil perso,
-- placement automatique (rectangles d'écran simulés), mode édition (aimant, priorité au manuel). Noms factices.
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/polish_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  T:SetProfile("account")
  T:ApplyPreset("normal")
  T:ClearSuggestions()
  T:ResetAlertGate()
  M.inCombat = false

  -- 1. même sens, plusieurs modules -> une ligne (la plus prioritaire), tooltip « aussi signalé par »
  T:Suggest("Hunter:ammo", "Munitions basses : 150 flèches.", 2, { module = "Hunter" })
  T:Suggest("Stats:ammo", "Munitions : vides dans ~12 min, achète 400", 3, { module = "Stats", sound = false })
  T:Suggest("Session:ammo", "Munitions pour finir le niveau : ~900", 2, { module = "Session" })
  T:Suggest("Boost:ammo30", "Munitions : ~20 min de jeu restantes", 1, { module = "Boost" })
  T:Suggest("Inventory:inv_bags", "Sacs presque pleins (2 places).", 2, { module = "Inventory" })
  T:Suggest("Stats:bags", "Sacs pleins dans ~4 monstres", 3, { module = "Stats", sound = false })
  T:Suggest("Squad:top1", "Passage marchand : 2 joueurs ont les sacs pleins", 2, { module = "Squad" })
  local now = T:GetTabLines("now")
  local ammo, bags = 0, 0
  for _, e in ipairs(now) do
    if T:TopicOf(e) == "ammo" then ammo = ammo + 1 end
    if T:TopicOf(e) == "bags" then bags = bags + 1 end
  end
  check(ammo == 1 and bags == 1, ("une ligne munitions (%d) et une ligne sacs (%d)"):format(ammo, bags))
  check(now[1].prio == 3, "la ligne gardée est la plus prioritaire")
  local kept
  for _, e in ipairs(now) do if T:TopicOf(e) == "ammo" then kept = e end end
  check(kept and kept.id == "Stats:ammo" and kept._also and kept._also.Hunter and kept._also.Session,
    "munitions : la plus urgente gardée, les autres fusionnées (aussi signalé par)")

  -- 2. encadré : 1 max / 20 s, sujet déjà signalé, pas en combat sauf danger
  T:ClearSuggestions(); T:ResetAlertGate()
  local st = T._alertState
  local shown0 = st.shown
  T:Suggest("Inventory:inv_bags", "Sacs pleins ! Le butin est perdu.", 3, { module = "Inventory", sound = false })
  T:Suggest("Hunter:petdead", "Familier mort.", 3, { module = "Hunter", sound = false })
  check(st.shown == shown0 + 1, "2 urgences en 1 s -> 1 seul encadré")
  M.Advance(21, 1)
  T:Suggest("Stats:bags", "Sacs pleins dans ~2 monstres", 3, { module = "Stats", sound = false })
  check(st.shown == shown0 + 1, "même sujet (sacs) 21 s après : pas de nouvel encadré")
  local snd = #M.sounds
  M.inCombat = true
  T:Suggest("Hunter:pethp", "Familier à 20 % !", 3, { module = "Hunter" })
  check(st.shown == shown0 + 1 and #M.sounds == snd, "en combat : pas d'encadré ni de son pour un non-danger")
  M.Advance(21, 1)
  T:Suggest("Hunter:aspect", "Quitte « Aspect du guépard » : tu es en combat", 3, { module = "Hunter", sound = false })
  check(st.shown == shown0 + 2, "en combat : le danger de mort passe")
  M.inCombat = false
  T:ResetAlertGate()

  -- 3. préréglages
  T:ClearSuggestions()
  for i = 1, 9 do T:Suggest("x:" .. i, "Conseil numéro " .. i, (i % 2 == 0) and 2 or 1) end
  T:ApplyPreset("calme")
  local n = #T:GetTabLines("now")
  local allP2 = true
  for _, e in ipairs(T:GetTabLines("now")) do if e.prio < 2 and not e.pin then allP2 = false end end
  check(n <= 3 and allP2, "calme : 3 lignes max, prio ≥ 2 (" .. n .. ")")
  T:ApplyPreset("tout")
  check(#T:GetTabLines("now") == 8, "tout : 8 lignes")
  T:ApplyPreset("normal")
  check(#T:GetTabLines("now") == 5 and T.settings.preset == "normal", "normal : 5 lignes")
  M.RunSlash("/tai mode calme")
  check(T.settings.preset == "calme", "/tai mode calme")
  M.RunSlash("/tai mode normal")
  -- budget par module (normal : 2 lignes d'un même module)
  T:ClearSuggestions()
  for i = 1, 4 do T:Suggest("Fight:t" .. i, "Astuce combat " .. i, 2, { module = "Fight" }) end
  local fight = 0
  for _, e in ipairs(T:GetTabLines("now")) do if e.module == "Fight" then fight = fight + 1 end end
  check(fight == 2, "normal : 2 lignes max par module (" .. fight .. ")")
  T:ClearSuggestions()

  -- 4. module coupé en direct : plus de conseils ni de bloc d'onglet, rallumé sans /reload
  T:SetModuleEnabled("Edge2", false)
  T:Suggest("Edge2:test", "Avantage : test", 2, { module = "Edge2" })
  check(not T._suggestions["Edge2:test"], "module coupé : suggestion ignorée")
  local tabs = {}
  for _, t in ipairs(T:GetTabs()) do tabs[t.id] = true end
  check(not tabs.adv, "module coupé : son onglet disparaît")
  T:SetModuleEnabled("Edge2", true)
  check(T.modules.Edge2._active, "module rallumé en direct")
  T:SetEnabled(false)
  check(not T.modules.Quests._active, "TreeAI coupé : modules arrêtés")
  T:SetEnabled(true)
  check(T.modules.Quests._active, "TreeAI rallumé : modules relancés")

  -- 5. profil propre au personnage, en direct
  T:SetProfile("char")
  check(T:ProfileMode() == "char" and T.settings ~= TreeAIDB.settings, "profil perso actif")
  T:ApplyPreset("calme")
  check(TreeAIDB.settings.preset ~= "calme" or TreeAIDB.settings == T.settings, "réglage perso sans toucher au compte")
  check(T.modules.Quests.db == TreeAIDB.chars[T:CharKey()].moduleSettings.Quests, "réglages des modules par perso")
  T:SetProfile("account")
  check(T:ProfileMode() == "account" and T.settings == TreeAIDB.settings, "retour au profil du compte")
  T:ApplyPreset("normal")

  -- 6. assistant de démarrage (3 étapes) + page « Tout » cliquable
  TreeAIDB.onboarded = nil
  check(T:ShowWizard(1), "assistant ouvert")
  local W = T:GetWizard()
  check(W:IsShown() and W.step == 1, "étape 1")
  W.next:Click("LeftButton")
  check(W.step == 2, "étape 2")
  -- (5.0 LOOK) étape 2 : profil Simple / Complet (une phrase chacun) + ce qui est à l'écran
  local function wizClick(word)
    for _, b in ipairs(W.groups[2]) do
      if b:IsShown() and tostring(b:GetText()):find(word, 1, true) then b:Click("LeftButton") return true end
    end
  end
  local prof0 = T:ModuleProfile()
  check(wizClick("Simple") and T:ModuleProfile() == "sur", "assistant : profil Simple choisi")
  check(tostring(W.presetHelp:GetText()):find("Simple", 1, true), "assistant : le profil choisi est expliqué en une phrase")
  check(wizClick("Complet") and T:ModuleProfile() == "tout", "assistant : profil Complet choisi")
  T:SetModuleProfile(prof0)
  W.next:Click("LeftButton")
  check(W.step == 3, "étape 3")
  W.next:Click("LeftButton")
  check(not W:IsShown() and TreeAIDB.onboarded, "assistant terminé et mémorisé")
  T:ApplyPreset("normal")
  check(T:ShowHelp(), "page « Tout » ouverte")
  local H = T:GetHelpFrame()
  local groups = T:GetSlashHelp()
  check(#groups >= 6, "commandes groupées (" .. #groups .. " rubriques)")
  local cmds = {}
  for _, g in ipairs(groups) do for _, it in ipairs(g.items) do cmds[it.cmd] = g.title end end
  check(cmds.edit and cmds.placer and cmds.mode and cmds.quetes and cmds.macros, "edit, placer, mode, quêtes, macros listés")
  -- clic sur une ligne = même fonction que la commande
  T:ShowHelp(1)
  local row
  for _, r in ipairs(H.rows) do if r.cmd == "mode" then row = r end end
  local before = #M.output
  if row then row:Click("LeftButton") end
  check(row and #M.output > before, "ligne « mode » cliquée -> commande lancée")
  H:Hide()

  -- 7. placement automatique : écran 1920×1080 avec minimap, barres, chat, suivi, cible
  UIParent:SetSize(1920, 1080)
  local function fake(name, l, b, w, h)
    local f = CreateFrame("Frame", name, UIParent)
    f.GetRect = function() return l, b, w, h end
    _G[name] = f
    return f
  end
  fake("MinimapCluster", 1720, 860, 200, 220)
  fake("MainMenuBar", 560, 0, 800, 90)
  fake("MultiBarRight", 1870, 300, 50, 500)
  fake("ChatFrame1", 30, 90, 430, 200)
  fake("TargetFrame", 1100, 950, 240, 100)
  fake("PlayerFrame", 600, 950, 240, 100)
  fake("ObjectiveTrackerFrame", 1500, 400, 300, 420)
  local placed = T:AutoLayout(true)
  check(placed and placed.panel and placed.alert, "placement auto : panneau + encadré placés")
  local function overlaps(a, b)
    return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h
  end
  local obstacles = T:ScanScreen().rects
  for role, r in pairs(placed) do
    for _, o in ipairs(obstacles) do
      check(not overlaps(r, o), ("%s ne chevauche pas %s"):format(role, tostring(o.name)))
    end
  end
  local a = placed.alert
  check(a.b > 700 and a.l + a.w > 1400, ("encadré près de la minimap (%d,%d %dx%d)"):format(a.l, a.b, a.w, a.h))
  if placed.compass then
    local c = placed.compass
    check(math.abs(c.l + c.w / 2 - 960) < 400 and c.b > 700, "boussole en haut, vers le centre")
  end
  check(TreeAIDB.chars[T:CharKey()].layout.sig == T:LayoutSignature(), "signature écran mémorisée")
  check(not T:CheckAutoLayout(), "même écran : pas de nouveau placement")

  -- 8. mode édition : calques, glisser aimanté, manuel prioritaire, réinitialiser = auto
  -- (4.0 QA) la carte « Prochaine étape » est l'affichage : elle a son calque, le panneau texte ne s'ouvre pas tout seul
  local Wn = T.Window
  if Wn and Wn.CardIsHUD and Wn.CardIsHUD() then
    if T:GetPanel() then T:GetPanel():Hide() end
    check(T:SetEditMode(true) and T:IsEditMode(), "mode édition (carte)")
    check(T:GetOverlays().card and T:GetOverlays().card:IsShown() and Wn.GetCard():IsShown(), "calque sur la carte « Prochaine étape »")
    check(not (T:GetPanel() and T:GetPanel():IsShown()), "le panneau texte ne s'ouvre pas tout seul en mode édition")
    T:SetEditMode(false)
    T:ShowPanel()   -- le joueur ouvre lui-même le panneau (Outils) : il se place aussi
  end
  check(T:SetEditMode(true) and T:IsEditMode(), "mode édition")
  local movers = T:GetMovables()
  check(movers.panel and movers.alert, "panneau et encadré déplaçables")
  check(T:GetOverlays().panel and T:GetOverlays().panel:IsShown(), "calque sur le panneau")
  local panel = T:GetPanel()
  panel.GetRect = function() return 0, 0, 280, 120 end
  local rec = T:CommitMove("panel", 703, 404)
  check(rec and rec.l == 700 and rec.b == 400, "aimant : grille de 10 px")
  rec = T:CommitMove("panel", 1915 - 280, 5)
  check(rec and rec.l == 1920 - 280 and rec.b == 0, "aimant : bords de l'écran")
  check(TreeAIDB.chars[T:CharKey()].movers.panel, "position gardée par personnage")
  local placed2 = T:AutoLayout(false)
  check(placed2 and not placed2.panel, "placement auto : la place manuelle du panneau gagne")
  T:ResetMovable("panel")
  check(not TreeAIDB.chars[T:CharKey()].movers.panel and T._lastLayout.panel, "réinitialiser : retour à l'auto")
  T:SetFramesLocked(true)
  check(T:FramesLocked(), "tout verrouillé")
  T:SetFramesLocked(false)
  T:SetEditMode(false)
  check(not T:IsEditMode() and not T:GetOverlays().panel:IsShown(), "fin du mode édition")

  -- 9. formats communs
  local F = T.Fmt
  check(F.Num(1234567) == "1 234 567" and F.Money(123456) == "12 po 34 pa" and F.Money(99) == "99 pc",
    "formats : nombres et monnaie (" .. F.Money(123456) .. ")")
  check(F.Time(45) == "45 s" and F.Time(150) == "2 min 30" and F.Time(3900) == "1 h 05", "formats : durées")
  T:ClearSuggestions()
end
