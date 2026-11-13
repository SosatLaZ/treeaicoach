-- Harness scenario (MENU): the main panel's look and the « suivi auto des quêtes par région ».
--  * Region: Ici → Zones voisines (by travel time) → Plus loin ; « Région déconseillée : … — on y va quand même
--    parce que … » for the best quest when its region is bad ; regrouped on ZONE_CHANGED_NEW_AREA ;
--    the game's quest watch is NEVER touched automatically, only by the « Suivi du jeu » click (out of combat).
--  * one icon + one colour per line type (Theme.LINE), red only for urgent, gold for money ;
--  * right-aligned number column, no overlap, no truncation (text fits in its wrapped lines) ;
--  * click affordance: tooltip first line = what the click does, cursor ; tab badges ; grouped « Plus » menu ;
--    empty state « Rien d'urgent — continue « … » » ; « meilleur guide » (distance · durée + direction).
-- lua5.1 tests/wow/harness.lua --scenario tests/wow/menu_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local Th = T.Theme
  M.inCombat = false
  check(not T.Region, "Region retiré (5.1)")
  check(Th and Th.LINE and Th.LineStyle and Th.QuestDiffColor, "Theme : genres de ligne + couleurs de difficulté")
  if not Th then return end
  if T.ApplyPreset then T:ApplyPreset("tout") end
  T:ShowPanel()
  T:SetMini(false)
  T.settings.panel.width = 340
  T.settings.panel.maxLines = 15

  -- (5.1 CLEAN) Region.lua (« quêtes par région », coupé pour tout le monde depuis la 4.1, 3e écrivain du suivi du jeu)
  -- est retiré : ses sections 1 à 4 (classement, onglet regroupé, changement de zone, « Suivi du jeu ») avec lui
  local Q = T.modules.Quests
  local Tr = T.Tracker
  local savedList, savedItems, savedZone = Q.lastList, Tr and Tr.items, M.player.zone
  local QL = rawget(_G, "C_QuestLog")
  local savedDist = QL and QL.GetDistanceSqToQuest
  local savedWatch = QL and QL.AddQuestWatch
  local fake = {
    { id = 9101, title = "Les loups des Contreforts", level = 28, zone = "Contreforts", obj = {} },
    { id = 9103, title = "Les fouilles de Tanaris", level = 44, zone = "Tanaris", obj = {} },
  }

  -- 5. lignes : genre → icône + couleur ; colonne de droite ; pas de chevauchement ni de troncature -----------
  T:RegisterTab("zzmenu", "Essai", 98)
  local samples = {
    { text = "Danger : élite en approche", prio = 3, want = "danger" },
    { text = "Rends « Le cavalier céleste » à Kanati · 118 m", want = "turnin", right = "118 m" },
    { text = "Tue 6 Loups des collines au nord (≈ 3 min)", want = "kill", right = "≈ 3 min" },
    { text = "Ramasse 10 Oeufs de wyverne à l'ouest du camp, près de la rivière et des huttes · 18 min", want = "collect", right = "18 min" },
    { text = "Utilise la Fiole sur le cadavre", want = "use" },
    { text = "Va au camp de Brumefeu · 396 m", want = "travel", right = "396 m" },
    { text = "Prends le vol vers Orgrimmar", want = "flight" },
    { text = "Prends le bateau pour Menethil", want = "boat" },
    { text = "Équipe Brassards du terreau (+21 %)", want = "gear" },
    { text = "Nourris ton familier (+25 %)", want = "pet" },
    { text = "Amie a fini « Ours » : on se retrouve au camp", kind = "group", want = "group" },
    { text = "Vends 3 Cuir moyen à l'HV · 12 po", want = "money", right = "12 po" },
    { text = "Refais le plein de boissons chez l'aubergiste", want = "consumable" },
    { text = "~439 monstres ou ~14 quêtes avant niv 29 · 33 666 XP/h", want = "info", right = "33 666 XP/h" },
  }
  local lines = {}
  for i, s in ipairs(samples) do
    lines[i] = { text = s.text, prio = s.prio or 1, kind = s.kind, onClick = (i % 2 == 0) and function() end or nil }
  end
  T:SetTabLines("zzmenu", lines, "Essai")
  T:SelectTab("zzmenu")
  T:RefreshPanel()
  local rl = T:GetRowLayout()
  check(#rl == #samples, ("toutes les lignes affichées (%d/%d)"):format(#rl, #samples))
  local okOverlap, okFit, okRight, okWidth = true, true, true, true
  for i, r in ipairs(rl) do
    local s = samples[i]
    if s then
      local want = s.want
      check(r.kind == want, ("genre de « %s » : %s (attendu %s)"):format(s.text, tostring(r.kind), want))
      local icon, cr, cg, cb = Th:LineStyle(want, s.prio or 1)
      if want ~= "group" and want ~= "pet" and want ~= "gear" and want ~= "consumable" and want ~= "money" and want ~= "info" then
        check(r.icon == icon, ("icône de « %s » = icône du genre %s"):format(s.text, want))
      end
      check(r.icon ~= nil, "chaque ligne a une icône : " .. s.text)
      local isRed = r.r == 1 and r.g < 0.4 and r.b < 0.4
      check(isRed == (want == "danger"), ("rouge seulement pour l'urgent : %s"):format(s.text))
      if want == "money" then check(math.abs(r.r - 1) < 0.01 and math.abs(r.g - 0.82) < 0.01, "argent en or") end
      if s.right then check(r.right == s.right, ("chiffres à droite : « %s » (%s)"):format(tostring(r.right), s.right)) end
      check(not tostring(r.text):find("…", 1, true), "pas de « … » : " .. tostring(r.text))
    end
    if r.textPx > r.lines * r.avail + 0.5 then okFit = false end
    if r.lines > 3 then okFit = false end
    if r.right and r.rightPx > r.rightW + 0.5 then okRight = false end
    if math.abs(r.iconW + r.avail + r.colW + r.chevW - r.rowW) > 0.5 then okWidth = false end
    if rl[i + 1] and rl[i + 1].top > r.top - r.h + 0.01 then okOverlap = false end
  end
  check(okOverlap, "lignes l'une sous l'autre, sans chevauchement")
  check(okFit, "texte entier dans ses lignes (retour à la ligne, jamais coupé)")
  check(okRight, "colonne de droite : chaque chiffre tient dans la colonne")
  check(okWidth, "icône + texte + colonne + chevron = largeur de la ligne (rien ne déborde)")
  local colW
  for _, r in ipairs(rl) do
    colW = colW or r.colW
    if r.colW ~= colW then colW = -1 end
  end
  check(colW and colW > 0, "colonne de droite : même largeur pour toutes les lignes de l'onglet")
  -- clic : infobulle (1re ligne = le clic) + curseur
  local cursor
  local sc, rc = rawget(_G, "SetCursor"), rawget(_G, "ResetCursor")
  rawset(_G, "SetCursor", function(x) cursor = x end)
  rawset(_G, "ResetCursor", function() cursor = "reset" end)
  local clickRow
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.entry and ch.entry.onClick and ch:IsShown() and ch.chev then clickRow = ch break end
  end
  check(clickRow and clickRow.chev:IsShown(), "ligne cliquable : chevron visible")
  if clickRow then
    M.Script(clickRow, "OnEnter")
    check(tostring(T._lastTooltipFirst):find("^Clic"), "infobulle : 1re ligne = ce que fait le clic (" .. tostring(T._lastTooltipFirst) .. ")")
    check(cursor and cursor ~= "reset", "curseur « main » sur une ligne cliquable")
    M.Script(clickRow, "OnLeave")
    check(cursor == "reset", "curseur normal en sortant")
  end
  rawset(_G, "SetCursor", sc); rawset(_G, "ResetCursor", rc)
  -- la ligne de quête : « Clic : boussole… » du module remonte en 1re ligne
  T:SelectTab("quests")
  T:RefreshPanel()
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.entry and ch.entry.questID == 9103 and ch:IsShown() then
      M.Script(ch, "OnEnter")
      check(tostring(T._lastTooltipFirst):find("^Clic : boussole"), "quête : le « Clic : … » du module en 1re ligne")
    end
  end

  -- 6. menu « Plus » groupé (Jeu / Outils / Réglages) avec icônes -------------------------------------------
  T:SelectTab("now")
  T:ToggleMoreMenu(true)
  local _, entries = T:GetMoreMenu()
  local heads, okW = {}, true
  for _, e in ipairs(entries) do
    if e.kind == "header" then heads[#heads + 1] = e.group end
    if e.textW and e.textMax and e.textW > e.textMax then okW = false end
  end
  check(table.concat(heads, ",") == "jeu,outils,reglages", "menu « Plus » : groupes Jeu / Outils / Réglages (" .. table.concat(heads, ",") .. ")")
  check(okW, "menu : libellés dans leur ligne")
  T:ToggleMoreMenu(false)

  -- 7. Maintenant : meilleur guide + mise en garde de région ; état vide --------------------------------------
  T:ClearSuggestions()
  Q.lastList = fake
  -- (seule étape épinglée : celles du plan / du module Quêtes du harnais sont vidées)
  T:SetTabLines("quests", {}, "plan"); T:SetTabLines("quests", {}, "Quests")
  T:SetTabLines("quests", { { text = "Étape : Les fouilles de Tanaris", prio = 1, pin = true, questID = 9103 } })
  local C = T.Compass
  local savedState = C and C.state
  if C then C.state = { target = { questID = 9103, label = "Les fouilles" }, dist = 250, eta = "36 s", bearing = 0 } end
  T:SelectTab("now")
  T:RefreshPanel()
  local guide
  for _, r in ipairs(T:GetRowLayout()) do if r.guide then guide = r end end
  check(guide and guide.right and guide.right:find("36 s", 1, true), "étape en cours : distance · durée à droite (" .. tostring(guide and guide.right) .. ")")
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.entry and ch.entry.pin and ch:IsShown() then
      check(ch.entry._guide and ch.entry._guide:find("^Direction : N"), "infobulle : direction (" .. tostring(ch.entry._guide) .. ")")
    end
  end
  if C then
    C.state = { target = { questID = 9103 }, dist = 120, eta = "17 s", bearing = 0 }
    T:_UpdateGuide()
    local g2
    for _, r in ipairs(T:GetRowLayout()) do if r.guide then g2 = r end end
    check(g2 and g2.right and g2.right:find("17 s", 1, true), "distance tenue à jour sans redessin complet")
    C.state = savedState
  end
  T:SetTabLines("quests", {})
  T:ClearSuggestions()
  T:RefreshPanel()
  if #T:GetVisibleLines() == 0 then
    local txt = T:GetPanel().empty:GetText() or ""
    check(txt:find("^Rien d'urgent"), "Maintenant vide : « " .. txt .. " »")
  end

  -- 8. pastille de la minimap + menu hub (tout à la souris) -------------------------------------------------
  local H = T.Hub
  check(H and H.Offset and H.Entries, "module Hub chargé")
  if H then
    local x, y = H.Offset(0, 140, 140, "ROUND")
    check(math.abs(x - 75) < 0.01 and math.abs(y) < 0.01, "pastille : bord droit d'une minimap ronde")
    x, y = H.Offset(45, 140, 140, "SQUARE")
    check(x > 60 and y > 60 and x <= 75 and y <= 75, "minimap carrée : la pastille suit le coin")
    check(math.abs(H.AngleOf(0, 0, 0, 10) - 90) < 0.01, "angle depuis le curseur")
    local btn = H.Button()
    check(btn and btn:GetParent() == rawget(_G, "Minimap") and btn:IsShown(), "pastille ronde enfant de la minimap")
    if btn then
      M.Script(btn, "OnEnter")
      local tt = rawget(_G, "GameTooltip")
      local all = table.concat(tt and tt.__lines or {}, "\n")
      check(all:find("TreeAI Coach", 1, true) and (all:find("Prochaine étape", 1, true) or all:find("Rien d'urgent", 1, true)),
        "infobulle de la pastille : résumé en direct")
      -- (UIREDO) clic gauche = la fenêtre TreeAI, clic droit = le menu rapide
      local Wn = T.Window
      btn:Click("LeftButton")
      check(Wn and Wn.IsOpen(), "clic gauche : fenêtre TreeAI ouverte")
      if Wn then Wn.Close() end
      btn:Click("RightButton")
      check(H.IsOpen(), "clic droit : menu ouvert")
      local heads, items = {}, {}
      for _, e in ipairs(H.layout) do
        if e.kind == "header" then heads[#heads + 1] = e.group end
        if e.kind == "item" then items[e.id] = e end
      end
      -- (5.0 LOOK) d'abord les actions rapides (guidage ON / OFF + les actions de la bande), puis les pages
      check(table.concat(heads, ",") == "rapide,jeu,outils,reglages", "menu : Actions rapides / Pages / Outils / Réglages (" .. table.concat(heads, ",") .. ")")
      check(items["guide"] and items["strip:rendre"] and items["strip:sacs"], "menu : guidage des quêtes, Rendre, Sacs en actions rapides")
      -- (4.1 LAYOUT) « Maintenant » est fondu dans « Quêtes » ; les 7 pages de la navigation (Personnage, Réglages…)
      -- (5.1 LOOK) « Maintenant » est la page d'accueil ; « Trajet » est un onglet de Quêtes (plus une entrée du menu)
      for _, id in ipairs({ "tab:home", "tab:quests", "tab:group", "tab:gear", "tab:hunter", "tab:settings", "options", "erreurs", "bloque", "profil", "macros" }) do
        check(items[id], "menu : entrée « " .. id .. " »")
      end
      local okW = true
      for _, e in pairs(items) do if e.textW > e.textMax then okW = false end end
      check(okW, "menu : libellés dans leur ligne")
      if items["tab:quests"] then
        items["tab:quests"].button:Click("LeftButton")
        check(Wn and Wn.IsOpen() and Wn.CurrentPage() == "quests" and not H.IsOpen(),
          "clic sur « Quêtes » : page Quêtes de la fenêtre, menu fermé")
        if Wn then Wn.Close() end
      end
    end
  end

  -- remise en état
  Q.lastList = savedList
  if Tr then Tr.items = savedItems end
  M.player.zone = savedZone
  if QL then QL.GetDistanceSqToQuest, QL.AddQuestWatch = savedDist, savedWatch end
  if T.ApplyPreset then T:ApplyPreset("normal") end
end
