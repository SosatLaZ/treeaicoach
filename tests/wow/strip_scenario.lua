-- Harness scenario (4.1, agent STRIP): the « boutons rapides » strip that replaces the « Prochaine étape » card at the
-- top left. Run by tests/wow/test_strip.py on the real-client geometry (harness --geometry ready=1) for the forever and
-- modern flavours. Dumps what the player sees (labels, counts, keys, active mode) and proves:
--   * default screen: the strip is shown, the card, the text panel and the old « Raccourcis » bar are not;
--   * a group of 4 (party frames 1-4 + pet frame + target frame with its auras): the strip overlaps none of the game's
--     unit / party frames, the quest tracker, the minimap or the buffs, and stays in the upper-left part of the screen;
--   * without T.QuestBrain: each mode button opens the matching window page (Quêtes, Trajet, Groupe, Journal),
--     « Rendre » counts the finished quests of the log;
--   * with a fake T.QuestBrain: labels / counts / active mode come from Modes(), a click calls SetMode(id), the
--     « mode suivant » key cycles, QUEST_MODE_CHANGED repaints;
--   * « Raccourcis conseillés »: one click binds Alt-1… only on free keys (a taken key is never replaced, never
--     Ctrl-1… which is the game's pet bar), saved; refused in combat; the keys show on the buttons;
--   * movable (grip drag = player place kept across a roster change), narrow screen = no overlap either;
--   * nothing in the chat, zero blocked action, zero violation.
-- Fake names and quests only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "strip_scenario needs --geometry") then return end
  local S, W = T.Shortcuts, T.Window
  if not check(S and S.State and S.Strip and W, "Shortcuts (boutons rapides) + Window loaded") then return end
  local UIP = UIParent
  local function out(s) io.write("STRIP ", s, "\n") end
  local function blocked() return #(M.blocked or {}) end
  local chat0, b0, v0 = #M.output, blocked(), #M.violations
  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end

  -- ------------------------------------------------------------------ world: hunter in a group of 4, quests
  M.LeaveCombat()
  M.player.level = 29
  M.quests = {
    { header = "Les Tarides" },
    { id = 801, title = "La revanche de test", level = 28, complete = true, objectives = {} },
    { id = 802, title = "Crocs de test", level = 27, complete = true, objectives = {} },
    { id = 803, title = "Plumes de test", level = 28, objectives = { { text = "Plume : 2/6", done = false, have = 2, need = 6 } } },
  }
  M.group = {
    { name = "Amie", class = "PRIEST", level = 28, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 29, online = true },
    { name = "Camarade", class = "MAGE", level = 28, online = true },
  }
  -- the game's party frames (Classic places: under the player frame), pet frame, target frame with a target
  for i = 1, 4 do
    local f = rawget(_G, "PartyMemberFrame" .. i)
    if f then
      f.__parent, f.__w, f.__h, f.__shown = UIP, 128, 53, i <= 3
      f.__points = { { "TOPLEFT", UIP, "TOPLEFT", 10, -160 - (i - 1) * 64 } }
    end
  end
  local pf = rawget(_G, "PetFrame")
  if pf then pf.__parent, pf.__w, pf.__h, pf.__shown = UIP, 128, 53, true pf.__points = { { "TOPLEFT", PlayerFrame, "TOPLEFT", 80, -60 } } end
  M.target = { exists = true, name = "Hyène de test", level = 27, health = 500, healthMax = 500, hostile = true }
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("PLAYER_TARGET_CHANGED")
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(4, 0.1)

  -- ------------------------------------------------------------------ default screen
  local strip = S.Strip()
  check(strip and strip:IsShown(), "boutons rapides affichés par défaut")
  local card = W.GetCard and W.GetCard()
  check(not (card and card:IsShown()), "carte « Prochaine étape » cachée par défaut (les boutons la remplacent)")
  check(W.db.card == false, "carte : coupée d'office")
  local panel = T.GetPanel and T:GetPanel()
  check(not (panel and panel:IsShown()), "panneau texte caché (les boutons tiennent lieu d'affichage)")
  local dock = rawget(_G, "TreeAIShortcuts")
  check(not (dock and dock:IsShown()), "ancienne barre « Raccourcis » cachée par défaut")
  local mv = T:GetMovables()
  check(mv.strip and mv.strip.frame == strip, "boutons rapides : déplaçables (mode édition)")

  -- ------------------------------------------------------------------ no overlap with the game's frames
  local AVOID = { "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "ObjectiveTrackerFrame", "MinimapCluster", "BuffFrame", "ChatFrame1", "MultiBarBottomLeft",
    "MainMenuBar", "CastingBarFrame" }
  local function overlap(a, b) return a.l < b.l + b.w and b.l < a.l + a.w and a.b < b.b + b.h and b.b < a.b + a.h end
  local function noOverlap(tag)
    local r = geo.Rect(strip)
    if not check(r, tag .. " : la bande a un rectangle") then return end
    local SW, SH = geo.Screen()
    out(("%s rect l=%d b=%d w=%d h=%d (écran %dx%d) compact=%s"):format(tag, r0(r.l), r0(r.b), r0(r.w), r0(r.h), r0(SW), r0(SH),
      tostring(S.compact)))
    check(geo.OnScreen(strip), tag .. " : sur l'écran")
    for _, n in ipairs(AVOID) do
      local f = rawget(_G, n)
      local fr = f and geo.Rect(f)
      if fr then
        check(not overlap(r, fr), ("%s : pas sur %s (%d,%d %dx%d)"):format(tag, n, r0(fr.l), r0(fr.b), r0(fr.w), r0(fr.h)))
      end
    end
    -- the target's auras (two rows of icons under the target frame) stay free too
    local tf = geo.Rect(TargetFrame)
    if tf then check(not overlap(r, { l = tf.l, b = tf.b - 40, w = tf.w, h = 40 }), tag .. " : pas sur les auras de la cible") end
    return r
  end
  local r = noOverlap("groupe de 4")
  if r then
    local SW, SH = geo.Screen()
    check(r.l < SW * 0.45 and r.b + r.h > SH * 0.55, "en haut à gauche de l'écran")
  end

  -- ------------------------------------------------------------------ labels / dump (no QuestBrain)
  T.QuestBrain = nil
  S.Refresh(true)
  local dump = S.Dump()
  out("sans QuestBrain : " .. table.concat(dump, " · "))
  local want = { "Rendre", "À prendre", "Suivant", "Zone", "Groupe", "Journal", "TreeAI" }
  for i, l in ipairs(want) do check(dump[i] and dump[i]:sub(1, #l) == l, ("bouton %d = « %s » (%s)"):format(i, l, tostring(dump[i]))) end
  check(dump[1]:find("(2)", 1, true), "Rendre : les 2 quêtes finies du journal (" .. dump[1] .. ")")
  local B = S.Buttons()
  check(B.rendre and B.rendre.icon and B.rendre.text and B.rendre.text:GetText() == "Rendre", "icône + mot court")
  check(B.rendre.badge and B.rendre.badge:GetText() == "2" and B.rendre.badge:IsShown(), "pastille de nombre")
  -- fallback: the window page that does the same
  local pages = { rendre = "quests", prendre = "quests", zone = "route", groupe = "group", journal = "journal" }
  for id, page in pairs(pages) do
    W.Close()
    M.Advance(0.3, 0.1)
    B[id]:Click("LeftButton")
    M.Advance(0.3, 0.1)
    check(W.IsOpen() and W.CurrentPage() == page, ("sans QuestBrain : « %s » ouvre la page %s (%s)"):format(id, page,
      tostring(W.CurrentPage())))
  end
  W.Close()
  B.fenetre:Click("LeftButton")
  check(W.IsOpen(), "« TreeAI » ouvre la fenêtre")
  B.fenetre:Click("LeftButton")
  M.Advance(0.5, 0.1)
  -- tooltip: what it does + its key
  B.rendre.__scripts.OnEnter(B.rendre)
  local tt = GameTooltip
  local lines = {}
  for _, l in ipairs(tt.__lines or {}) do lines[#lines + 1] = type(l) == "table" and (l.text or l[1]) or tostring(l) end
  out("infobulle Rendre : " .. table.concat(lines, " | "))
  local joined = table.concat(lines, "\n")
  check(joined:find("Rendre mes quêtes au plus vite", 1, true) and joined:find("Touche", 1, true), "infobulle : ce qu'il fait + sa touche")
  B.rendre.__scripts.OnLeave(B.rendre)

  -- ------------------------------------------------------------------ fake QuestBrain (agent QUESTS' public API)
  local calls = {}
  local modes = {
    { id = "turnin", label = "Rendre mes quêtes au plus vite", short = "Rendre", key = "", tip = "Le plus court chemin.", count = 4 },
    { id = "pickup", label = "Quêtes à prendre", short = "À prendre", tip = "Les meilleures quêtes.", count = 3 },
    { id = "zone", label = "Tout faire dans la zone", short = "Zone", count = 9 },
    { id = "group", label = "Avec le groupe", short = "Groupe", count = 0 },
  }
  local active = "zone"
  T.QuestBrain = {
    Modes = function()
      local o = {}
      for _, m in ipairs(modes) do
        local c = {}
        for k, v in pairs(m) do c[k] = v end
        c.active = (m.id == active)
        o[#o + 1] = c
      end
      return o
    end,
    Mode = function() return active end,
    SetMode = function(id) calls[#calls + 1] = id active = id T:Fire("QUEST_MODE_CHANGED", id) end,
    PickupList = function(n) return { { title = "Nouvelle de test", npc = "Chef de test" }, "Autre de test" } end,
    TurnInList = function() return { { title = "La revanche de test" }, { title = "Crocs de test" } } end,
  }
  T:Fire("QUEST_MODE_CHANGED", active)
  M.Advance(0.5, 0.1)
  dump = S.Dump()
  out("avec QuestBrain : " .. table.concat(dump, " · "))
  check(dump[1] == "Rendre (4)", "Rendre (4) : " .. tostring(dump[1]))
  check(dump[2] == "À prendre (3)", "À prendre (3) : " .. tostring(dump[2]))
  check(dump[4] == "Zone (9) *", "mode actif surligné : " .. tostring(dump[4]))
  check(B.zone.sel:IsShown() and not B.rendre.sel:IsShown(), "surbrillance du mode choisi")
  B.prendre:Click("LeftButton")
  M.Advance(0.5, 0.1)
  check(calls[#calls] == "pickup", "clic « À prendre » = SetMode(pickup)")
  dump = S.Dump()
  check(dump[2] == "À prendre (3) *" and dump[4] == "Zone (9)", "le mode choisi suit (" .. dump[2] .. " / " .. dump[4] .. ")")
  B.rendre:Click("LeftButton")
  check(calls[#calls] == "turnin", "clic « Rendre » = SetMode(turnin)")
  B.groupe:Click("LeftButton")
  check(calls[#calls] == "group", "clic « Groupe » = SetMode(group)")
  W.Close()
  B.journal:Click("LeftButton")   -- no « journal » mode in this brain: its window page
  M.Advance(0.3, 0.1)
  check(W.IsOpen() and W.CurrentPage() == "journal", "mode absent : la page Journal")
  W.Close()
  M.Advance(0.3, 0.1)
  B.rendre:Click("RightButton")
  M.Advance(0.3, 0.1)
  check(W.IsOpen() and W.CurrentPage() == "quests", "clic droit : la page de la fenêtre")
  W.Close()
  local n0 = #calls
  T.StripAction("mode")
  check(#calls == n0 + 1 and calls[#calls] == "turnin", "touche « mode suivant » : group → turnin (" .. tostring(calls[#calls]) .. ")")
  T.StripAction("prendre")
  check(calls[#calls] == "pickup", "touche « À prendre » (Bindings.xml) = SetMode(pickup)")
  -- in combat: clicks still work (nothing protected), the layout does not move
  M.EnterCombat()
  local w0 = strip:GetWidth()
  B.zone:Click("LeftButton")
  check(calls[#calls] == "zone", "en combat : le clic change le mode (rien de protégé)")
  M.bindings.TREEAI_QB_TURNIN = "ALT-SHIFT-9"
  S.Refresh(true)
  check(strip:GetWidth() == w0, "en combat : la bande ne change pas de taille")
  M.LeaveCombat()
  if T.RunPendingOutOfCombat then T:RunPendingOutOfCombat() end
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(0.5, 0.1)
  check(strip:GetWidth() > w0, "après le combat : la bande suit")
  M.bindings.TREEAI_QB_TURNIN = nil
  S.Refresh(true)

  -- ------------------------------------------------------------------ keys: « Raccourcis conseillés »
  for _, cmd in ipairs(S.KEY_ORDER) do M.bindings[cmd] = nil end
  M.bindings["MULTIACTIONBAR1BUTTON3"] = "ALT-3"          -- the player's own key: never replaced
  M.bindings["BONUSACTIONBUTTON1"] = "CTRL-1"             -- the game's pet bar
  S.Refresh(true)
  local kb = rawget(_G, "TreeAIStripKeys")
  check(kb and kb:IsShown(), "bouton « Touches » tant qu'une touche manque")
  M.EnterCombat()
  S.SuggestKeys()
  check(M.bindings.TREEAI_QB_TURNIN == nil, "en combat : aucune touche posée")
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(0.3, 0.1)
  kb:Click("LeftButton")
  M.Advance(0.5, 0.1)
  local got = {}
  for _, cmd in ipairs(S.KEY_ORDER) do got[#got + 1] = cmd:gsub("TREEAI_", "") .. "=" .. tostring(M.bindings[cmd]) end
  out("touches : " .. table.concat(got, " "))
  check(M.bindings.TREEAI_QB_TURNIN == "ALT-1" and M.bindings.TREEAI_QB_PICKUP == "ALT-2", "Alt-1 / Alt-2 posées")
  check(M.bindings.MULTIACTIONBAR1BUTTON3 == "ALT-3" and M.bindings.TREEAI_STRIP_SUIVANT == "CTRL-SHIFT-3",
    "touche prise jamais remplacée (Suivant → Ctrl-Maj-3)")
  check(M.bindings.BONUSACTIONBUTTON1 == "CTRL-1", "Ctrl-1 (barre du familier) intact")
  for _, cmd in ipairs(S.KEY_ORDER) do
    check(not tostring(M.bindings[cmd]):find("^CTRL%-%d"), cmd .. " : jamais Ctrl-chiffre")
  end
  check(M.bindings.TREEAI_MAIN == "ALT-7" and M.bindings.TREEAI_MODE_NEXT == "ALT-8", "fenêtre Alt-7, mode suivant Alt-8")
  check(M.savedBindings ~= nil, "touches enregistrées (SaveBindings)")
  dump = S.Dump()
  out("avec touches : " .. table.concat(dump, " · "))
  check(dump[1]:find("[Alt-1]", 1, true) and dump[3]:find("[Ctrl-Maj-3]", 1, true), "touche affichée sur chaque bouton")
  check(B.rendre.key:GetText() == "Alt-1" and B.rendre.key:IsShown(), "petite touche sur le bouton")
  check(not kb:IsShown(), "bouton « Touches » retiré une fois tout lié")
  -- a 2nd click changes nothing (already bound)
  local before = M.bindings.TREEAI_QB_TURNIN
  S.SuggestKeys()
  check(M.bindings.TREEAI_QB_TURNIN == before, "2e clic : rien ne change")

  -- ------------------------------------------------------------------ narrow screen, player place
  r = noOverlap("après les touches")
  geo.SetScreen(1024, 768)
  M.Advance(2, 0.1)
  noOverlap("écran 1024×768")
  geo.SetScreen(800, 600)
  M.Advance(2, 0.1)
  noOverlap("écran 800×600")
  geo.SetScreen(1920, 1080)
  M.Advance(2, 0.1)
  noOverlap("retour 1920×1080")
  local g = rawget(_G, "TreeAIStripGrip")
  check(g and geo.Drag(g, 300, -200) == strip, "bande glissée par sa poignée")
  check(type(S.char.strip) == "table", "place du joueur gardée")
  local a1 = { strip:GetPoint(1) }
  M.group[3] = nil
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(2, 0.1)
  local a2 = { strip:GetPoint(1) }
  check(a1[1] == a2[1] and r0(a1[4]) == r0(a2[4]) and r0(a1[5]) == r0(a2[5]), "place du joueur : le groupe qui change ne la reprend pas")
  T:ResetMovable("strip")
  M.Advance(1, 0.1)
  check(S.char.strip == nil and S.placed == "auto", "réinitialiser : place automatique")
  noOverlap("réinitialisée")

  -- ------------------------------------------------------------------ option: icons only
  S.db.text = false
  S:OnOptionChanged("text")
  check(S.compact and not B.rendre.text:IsShown() and B.rendre.badge:IsShown(), "icônes seules : pastilles gardées")
  S.db.text = true
  S:OnOptionChanged("text")
  check(not S.compact and B.rendre.text:IsShown(), "mots revenus")

  -- ------------------------------------------------------------------ hygiene
  T.QuestBrain = nil
  for i = chat0 + 1, #M.output do out("chat: " .. M.output[i]) end
  check(#M.output == chat0, "rien dans le chat (" .. (#M.output - chat0) .. " lignes)")
  check(blocked() == b0, "aucune action bloquée")
  for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].kind) .. " " .. tostring(M.violations[i].msg)) end
  check(#M.violations == v0, "aucune violation")
end
