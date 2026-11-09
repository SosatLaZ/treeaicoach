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
  local R = T.Region
  local Th = T.Theme
  M.inCombat = false
  check(R and R.ClassifyRaw and R.Arrange, "module Region chargé")
  check(Th and Th.LINE and Th.LineStyle and Th.QuestDiffColor, "Theme : genres de ligne + couleurs de difficulté")
  if not (R and Th) then return end
  if T.ApplyPreset then T:ApplyPreset("tout") end
  T:ShowPanel()
  T:SetMini(false)
  T.settings.panel.width = 340
  T.settings.panel.maxLines = 15

  -- 1. classement pur ------------------------------------------------------------------------------
  local ctxR = { zone = "Contreforts", level = 28, group = 1 }
  local c = R.ClassifyRaw({ id = 1, zone = "Contreforts", level = 28 }, nil, ctxR)
  check(c.band == "here" and not c.bad, "zone actuelle : Ici, pas de mise en garde")
  c = R.ClassifyRaw({ id = 2, zone = "Hautebrande", level = 27 }, { dist = 900, travelSec = 140 }, ctxR)
  check(c.band == "near" and not c.bad, "zone voisine (2 min 20) : Zones voisines")
  c = R.ClassifyRaw({ id = 3, zone = "Tanaris", level = 44 }, nil, { zone = "Contreforts", level = 28, group = 1, onCont = false })
  check(c.band == "far" and c.bad and table.concat(c.reasons, ","):find("autre continent", 1, true)
    and table.concat(c.reasons, ","):find("trop haut", 1, true), "autre continent + trop haut : Plus loin, déconseillée")
  c = R.ClassifyRaw({ id = 4, zone = "Contreforts", level = 29, elite = true }, nil, ctxR)
  check(c.band == "here" and c.bad and c.reasons[1] == "élite, tu es seul", "élite seul : déconseillée même ici")
  c = R.ClassifyRaw({ id = 4, zone = "Contreforts", level = 29, elite = true }, nil, { zone = "Contreforts", level = 28, group = 3 })
  check(not c.bad, "élite à 3 : plus de mise en garde")
  c = R.ClassifyRaw({ id = 5, zone = "Désolace", level = 30 }, { dist = 9000, travelSec = 900 }, ctxR)
  check(c.band == "far" and c.reasons[1] == "loin (~15 min de trajet)", "15 min de trajet : loin (" .. tostring(c.reasons[1]) .. ")")
  check(R.WhyAnyway({ complete = true }, nil, {}):find("prête à rendre", 1, true), "pourquoi quand même : prête à rendre")
  check(R.WhyAnyway({}, nil, { hereOK = 0 }):find("plus rien de mieux", 1, true), "pourquoi quand même : rien ici")

  -- 2. onglet Quêtes regroupé par région --------------------------------------------------------------
  local Q = T.modules.Quests
  local Tr = T.Tracker
  local savedList, savedItems, savedZone = Q.lastList, Tr and Tr.items, M.player.zone
  local QL = rawget(_G, "C_QuestLog")
  local savedDist = QL and QL.GetDistanceSqToQuest
  local savedWatch = QL and QL.AddQuestWatch
  local watchCalls = 0
  if QL then
    QL.GetDistanceSqToQuest = function(id)
      if id == 9103 then return 4000 * 4000, false end
      return 500 * 500, true
    end
    QL.AddQuestWatch = function() watchCalls = watchCalls + 1 return true end
  end
  M.player.zone = "Contreforts"
  local fake = {
    { id = 9101, title = "Les loups des Contreforts", level = 28, zone = "Contreforts", obj = {} },
    { id = 9102, title = "Ours de Hautebrande", level = 27, zone = "Hautebrande", obj = {} },
    { id = 9103, title = "Les fouilles de Tanaris", level = 44, zone = "Tanaris", obj = {} },
    { id = 9104, title = "Le cavalier céleste", level = 29, zone = "Contreforts", complete = true, turnZone = "Contreforts", obj = {} },
    { id = 9105, title = "Courrier de Moulin-de-l'Ambre", level = 26, zone = "Moulin", obj = {} },
  }
  Q.lastList = fake
  if Tr then Tr.items = { { key = 9102, dist = 900, travelSec = 300 }, { key = 9105, dist = 700, travelSec = 120 } } end
  R.Invalidate()
  -- l'ordre du suivi (meilleure d'abord) : Tanaris en tête = la meilleure option malgré la région
  local qlines = {
    { text = "|cffffd100À faire maintenant|r : Ramasse 5 pierres", prio = 2 },
    { text = "|cff99ccffTanaris|r (1)", prio = 1, header = true },
    { text = "Les fouilles de Tanaris · Long", questID = 9103, onClick = function() end, tooltip = { "Clic : boussole · Maj-clic : Y aller" } },
    { text = "Ours de Hautebrande · Rapide", questID = 9102, onClick = function() end },
    { text = "Les loups des Contreforts · Rapide", questID = 9101, onClick = function() end },
    { text = "Le cavalier céleste · Prête à rendre", questID = 9104, prio = 2, onClick = function() end },
    { text = "Courrier de Moulin-de-l'Ambre · Rapide", questID = 9105, onClick = function() end },
    { text = "Ouvrir la fenêtre « À faire »", onClick = function() end },
  }
  T:SetTabLines("quests", qlines)
  T:SelectTab("quests")
  T:RefreshPanel()
  local vis = T:GetVisibleLines()
  local pos, warn = {}, nil
  for i, e in ipairs(vis) do
    if e.questID then pos[e.questID] = pos[e.questID] or i end
    if e.kind == "warn" then warn = e; pos.warn = i end
    check(not (e.header and not e.onClick and e.source == "main"), "ancien titre de zone retiré : " .. tostring(e.text))
  end
  check(pos[9101] and pos[9104] and pos[9105] and pos[9102] and pos[9103], "les 5 quêtes restent listées")
  check(pos[9101] < pos[9105] and pos[9104] < pos[9105], "Ici (Contreforts) avant les zones voisines")
  check(pos[9105] < pos[9102], "zones voisines triées par temps de trajet (2 min avant 5 min)")
  check(pos[9102] < pos[9103], "Plus loin (autre continent) en dernier")
  check(warn and pos.warn == pos[9103] + 1, "ligne de mise en garde juste sous la meilleure quête déconseillée")
  check(warn and warn.text:find("^Région déconseillée : ") and (warn.text:find("autre continent", 1, true) or not QL)
    and warn.text:find("— on y va quand même parce que", 1, true), "mise en garde : raison + pourquoi : " .. tostring(warn and warn.text))
  local labels = {}
  for _, e in ipairs(vis) do if e._bandLabel then labels[#labels + 1] = e._bandLabel end end
  local lj = table.concat(labels, "|")
  check(lj:find("Ici · Contreforts", 1, true) and lj:find("Zones voisines", 1, true) and lj:find("Plus loin", 1, true),
    "sections Ici · zone / Zones voisines / Plus loin")
  check((T:GetPanel().sections or 0) >= 4, "titres de section dessinés (" .. tostring(T:GetPanel().sections) .. ")")
  -- pastille de l'onglet : nombre de quêtes (pastilles relues au plus toutes les 2 s depuis 3.9.9)
  M.Advance(2.1, 0.5)
  T:RefreshPanel()
  local qtab
  for _, r in ipairs(T:GetTabLayout()) do if r.id == "quests" then qtab = r end end
  check(qtab and qtab.badge == "5", "onglet Quêtes : pastille « 5 » (" .. tostring(qtab and qtab.badge) .. ")")
  check(watchCalls == 0, "aucun appel au suivi du jeu sans clic")

  -- 3. ZONE_CHANGED_NEW_AREA : on regroupe pour la nouvelle zone (sans clic, sans suivi du jeu) ---------------
  M.player.zone = "Hautebrande"
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(1)
  T:RefreshPanel()
  vis = T:GetVisibleLines()
  local firstQ
  for _, e in ipairs(vis) do if e.questID then firstQ = e break end end
  check(firstQ and firstQ.questID == 9102 and firstQ._bandLabel == "Ici · Hautebrande", "nouvelle zone : ses quêtes en tête (Ici · Hautebrande)")
  check(watchCalls == 0, "changement de zone : toujours aucun appel au suivi du jeu")

  -- 4. « Suivi du jeu » : seulement sur clic, hors combat, ≤ 5 ---------------------------------------------
  M.player.zone = "Contreforts"
  M.FireEvent("ZONE_CHANGED_NEW_AREA")
  M.Advance(1)
  Q.lastList = fake   -- (le module Quêtes relit le vrai journal du harnais au changement de zone)
  R.Invalidate()
  T:RefreshPanel()
  local ctrl
  for _, ch in ipairs({ T:GetPanel():GetChildren() }) do
    if ch.entry and ch.entry.label == "Suivi du jeu" and ch:IsShown() then ctrl = ch end
  end
  check(ctrl ~= nil, "bouton d'onglet « Suivi du jeu »")
  check(R.WatchZone() == 0 and watchCalls == 0, "appel direct hors clic : refusé")
  if ctrl and QL then
    M.EnterCombat()
    ctrl:Click("LeftButton")
    check(watchCalls == 0, "en combat : rien n'est ajouté")
    M.LeaveCombat()
    ctrl:Click("LeftButton")
    check(watchCalls == 2, "clic : les 2 quêtes de la zone ajoutées au suivi du jeu (" .. watchCalls .. ")")
    M.Script(ctrl, "OnEnter")
    check(tostring(T._lastTooltipFirst):find("^Clic : ajoute les quêtes"), "infobulle du bouton : ce que fait le clic")
  end

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
  R.Invalidate()
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
  check(guide and guide.note and guide.note:find("^Région déconseillée"), "étape en cours déconseillée : dit pourquoi (sous la ligne)")
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
      check(table.concat(heads, ",") == "jeu,outils,reglages", "menu : Jeu / Outils / Réglages (" .. table.concat(heads, ",") .. ")")
      -- (4.1 LAYOUT) « Maintenant » est fondu dans « Quêtes » ; les 7 pages de la navigation (Personnage, Réglages…)
      for _, id in ipairs({ "tab:quests", "tab:route", "tab:group", "tab:gear", "tab:hunter", "tab:binds", "tab:settings", "options", "erreurs", "bloque", "profil", "macros" }) do
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
  R.Invalidate()
  if T.ApplyPreset then T:ApplyPreset("normal") end
end
