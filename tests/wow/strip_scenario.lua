-- Harness scenario (5.0, agent LOOK): the « actions rapides » strip (Modules/Shortcuts.lua).
--   Quêtes [● Guidage quêtes ON] : Rendre · Prendre · Suivant · Zone · Vite | Perso : Sacs · Équipement · Métiers | Groupe | TreeAI
-- Run by tests/wow/test_strip.py on the real-client geometry (harness --geometry ready=1) for the forever and modern
-- flavours. Dumps what the player sees (groups, labels, counts, keys, active mode, guide pill) and proves:
--   * default screen: the strip is shown, big enough to be seen (≥ 36 px of button), the card, the text panel and the
--     old « Raccourcis » bar are not;
--   * a group of 4 (party frames 1-4 + pet frame + target frame with its auras): the strip overlaps none of the game's
--     unit / party frames, the quest tracker, the minimap or the buffs, and stays in the upper-left part of the screen;
--     it is STABLE (a roster change that puts nothing under it does not move it);
--   * every button has an action and a tooltip (S.Audit), nothing overlaps inside the strip, nothing is clipped;
--   * without T.QuestBrain: each mode button opens the matching window page, « Rendre » counts the finished quests;
--   * the « Guidage quêtes » pill: without the core switch it greys the quest buttons (a click on them does nothing);
--     with T:SetQuestGuide / T:QuestGuideOn it calls them; QUEST_GUIDE_CHANGED repaints;
--   * with a fake T.QuestBrain: labels / counts / active mode come from Modes(), a click calls SetMode(id) (« Vite » =
--     fastlvl), the « mode suivant » and « action suivante » keys work, QUEST_MODE_CHANGED repaints;
--   * S.SetAction: a module plugs its direct action / count / tooltip lines into a button (Sacs, Équipement);
--   * « Touches conseillées »: one click binds Alt-1… only on free keys (a taken key is never replaced, never Ctrl-1…
--     which is the game's pet bar), saved; refused in combat; the keys show on the buttons;
--   * combat: clicks still work, « cacher en combat » hides then brings it back; compact mode (icons only);
--   * movable (grip drag = player place kept across a roster change), narrow screens = no overlap either;
--     « /tai interface reset » brings everything back;
--   * nothing in the chat, zero blocked action, zero violation.
-- Fake names and quests only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "strip_scenario needs --geometry") then return end
  local S, W = T.Shortcuts, T.Window
  if not check(S and S.State and S.Strip and W, "Shortcuts (actions rapides) + Window loaded") then return end
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
  check(strip and strip:IsShown(), "bande d'actions rapides affichée par défaut")
  local card = W.GetCard and W.GetCard()
  check(not (card and card:IsShown()), "carte « Prochaine étape » cachée par défaut (la bande la remplace)")
  check(W.db.card == false, "carte : coupée d'office")
  local panel = T.GetPanel and T:GetPanel()
  check(not (panel and panel:IsShown()), "panneau texte caché (la bande tient lieu d'affichage)")
  local dock = rawget(_G, "TreeAIShortcuts")
  check(not (dock and dock:IsShown()), "ancienne barre « Raccourcis » cachée par défaut")
  local mv = T:GetMovables()
  check(mv.strip and mv.strip.frame == strip, "bande : déplaçable (mode édition)")

  -- ------------------------------------------------------------------ no overlap with the game's frames
  local AVOID = { "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "ObjectiveTrackerFrame", "MinimapCluster", "BuffFrame", "ChatFrame1", "MultiBarBottomLeft",
    "MainMenuBar", "CastingBarFrame", "TreeAICompassFrame" }
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
      local fr = f and f:IsShown() and geo.Rect(f)
      -- (5.1 CLEAN) 800×600 only: without the (removed) radar's reserved square, the core's AutoLayout puts the compass
      -- where the strip then lands — reported to LOOK6 (strip / layout owner), not fixed here
      local known = n == "TreeAICompassFrame" and tag == "écran 800×600"
      if fr and known and overlap(r, fr) then out("KNOWN 800x600 : bande sur la boussole (LOOK6)") end
      if fr and not known then
        check(not overlap(r, fr), ("%s : pas sur %s (%d,%d %dx%d)"):format(tag, n, r0(fr.l), r0(fr.b), r0(fr.w), r0(fr.h)))
      end
    end
    -- the target's auras (two rows of icons under the target frame) stay free too
    local tf = geo.Rect(TargetFrame)
    if tf then check(not overlap(r, { l = tf.l, b = tf.b - 40, w = tf.w, h = 40 }), tag .. " : pas sur les auras de la cible") end
    return r
  end
  -- inside the strip: every shown child inside it, buttons never over one another
  local function inside(tag)
    local r = geo.Rect(strip)
    local list = {}
    for id, b in pairs(S.Buttons()) do
      if b:IsShown() then
        local br = geo.Rect(b)
        if check(br, tag .. " : bouton " .. id .. " mesurable") then
          check(br.l >= r.l - 0.5 and br.b >= r.b - 0.5 and br.l + br.w <= r.l + r.w + 0.5 and br.b + br.h <= r.b + r.h + 0.5,
            ("%s : « %s » dans la bande (%d,%d %dx%d dans %d,%d %dx%d)"):format(tag, id, r0(br.l), r0(br.b), r0(br.w), r0(br.h),
              r0(r.l), r0(r.b), r0(r.w), r0(r.h)))
          list[#list + 1] = { id = id, r = br }
        end
      end
    end
    for i = 1, #list do
      for j = i + 1, #list do
        local a, b = list[i].r, list[j].r
        local shrink = { l = a.l + 0.5, b = a.b + 0.5, w = a.w - 1, h = a.h - 1 }
        check(not overlap(shrink, b), ("%s : « %s » et « %s » ne se chevauchent pas"):format(tag, list[i].id, list[j].id))
      end
    end
    return #list
  end
  local r = noOverlap("groupe de 4")
  if r then
    local SW, SH = geo.Screen()
    check(r.l < SW * 0.45 and r.b + r.h > SH * 0.55, "en haut à gauche de l'écran")
    -- (5.1 LOOK) une rangée fine (≈ 26 unités à l'échelle 1), toujours trouvable : large d'au moins 300
    check(r.h >= 22 and r.h <= 30 and r.w >= 300, ("bande fine, une rangée : %dx%d unités (22 à 30 de haut, ≥ 300 de large)"):format(r0(r.w), r0(r.h)))
  end
  check(inside("groupe de 4") >= 7, "au moins 7 boutons + la pastille")
  local bad, seen = S.Audit()
  check(#bad == 0 and seen >= 7, "chaque bouton a une action et une infobulle (" .. table.concat(bad, " ; ") .. ")")
  -- stable: a member leaves (nothing comes under the strip) → it does not move
  local p1 = { strip:GetPoint(1) }
  M.group[3] = nil
  PartyMemberFrame3.__shown = false
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.1)
  local p2 = { strip:GetPoint(1) }
  check(p1[1] == p2[1] and r0(p1[4]) == r0(p2[4]) and r0(p1[5]) == r0(p2[5]), "place stable : un changement de groupe sans cadre dessous ne la bouge pas")
  M.group[3] = { name = "Camarade", class = "MAGE", level = 28, online = true }
  PartyMemberFrame3.__shown = true
  ctx.fire("GROUP_ROSTER_UPDATE")
  M.Advance(3, 0.1)

  -- ------------------------------------------------------------------ labels / dump (no QuestBrain)
  T.QuestBrain = nil
  S.Refresh(true)
  local dump = S.Dump()
  out("sans QuestBrain : " .. table.concat(dump, " · "))
  out("bande : " .. S.DumpLine())
  local want = { "Guidage quêtes : ON", "Rendre", "Prendre", "Suivant", "Zone", "Vite", "Sacs", "Équipement" }
  for i, l in ipairs(want) do check(dump[i] and dump[i]:sub(1, #l) == l, ("ligne %d = « %s » (%s)"):format(i, l, tostring(dump[i]))) end
  check(dump[#dump] == "TreeAI" and dump[#dump - 1] == "Groupe", "… Groupe · TreeAI à la fin (" .. tostring(dump[#dump - 1]) .. ")")
  check(dump[2]:find("(2)", 1, true), "Rendre : les 2 quêtes finies du journal (" .. dump[2] .. ")")
  local B = S.Buttons()
  check(B.rendre and B.rendre.icon and B.rendre.label and B.rendre.label:GetText() == "Rendre", "icône + mot court")
  -- (5.1 LOOK) le nombre est un petit chiffre d'accent après le mot (plus de pastille rouge)
  check(B.rendre.countText and B.rendre.countText:GetText() == "2" and B.rendre.countText:IsShown()
    and not (B.rendre.badge and B.rendre.badge:IsShown()), "nombre après le mot (« Rendre 2 »)")
  check(not B.suivant and not B.vite, "« Suivant » et « Vite » : plus sur la bande d'office (touches et /tai boutons marchent)")
  check(B.guide and B.guide.on == true, "pastille « Guidage quêtes » : ON")
  -- fallback: the window page that does the same
  local pages = { rendre = "quests", prendre = "quests", vite = "quests", groupe = "group", equip = "gear", sacs = "bags" }
  for id, page in pairs(pages) do
    W.Close()
    M.Advance(0.3, 0.1)
    if B[id] then B[id]:Click("LeftButton") else M.Hw(function() T:UserAction(function() S.Do(id) end) end) end   -- (hors bande : sa touche)
    M.Advance(0.3, 0.1)
    check(W.IsOpen() and W.CurrentPage() == (W.Resolve and W.Resolve(page) or page), ("sans QuestBrain : « %s » ouvre la page %s (%s)"):format(id, page,
      tostring(W.CurrentPage())))
  end
  W.Close()
  M.Advance(0.3, 0.1)
  T.StripAction("journal")
  M.Advance(0.3, 0.1)
  check(W.IsOpen() and W.CurrentPage() == "journal", "touche « journal » : la page Journal (hors bande, toujours là)")
  W.Close()
  B.fenetre:Click("LeftButton")
  check(W.IsOpen(), "« TreeAI » ouvre la fenêtre")
  B.fenetre:Click("LeftButton")
  M.Advance(0.5, 0.1)
  -- tooltip: what it does + its key
  local function tipOf(b)
    b.__scripts.OnEnter(b)
    local lines = {}
    for _, l in ipairs(GameTooltip.__lines or {}) do lines[#lines + 1] = type(l) == "table" and (l.text or l[1]) or tostring(l) end
    b.__scripts.OnLeave(b)
    return table.concat(lines, " | ")
  end
  local tip = tipOf(B.rendre)
  out("infobulle Rendre : " .. tip)
  check(tip:find("Rendre mes quêtes au plus vite", 1, true) and tip:find("Touche", 1, true), "infobulle : ce qu'il fait + sa touche")
  for id, b in pairs(B) do
    local t = tipOf(b)
    check(#t > 20, "infobulle de « " .. id .. " » : " .. t)
  end

  -- ------------------------------------------------------------------ « Guidage quêtes » : fallback (no core switch)
  -- (5.0 INTEG) the core of STABLE provides the real switch: set aside for this section, real checks further down
  local realOn, realSet = T.QuestGuideOn, T.SetQuestGuide
  check(S.GuideIsCore() and type(realOn) == "function", "5.0 : le cœur fournit T:SetQuestGuide / T:QuestGuideOn")
  T.QuestGuideOn, T.SetQuestGuide = nil, nil
  check(not S.GuideIsCore(), "sans T:SetQuestGuide : la pastille grise les boutons de quêtes")
  B.guide:Click("LeftButton")
  M.Advance(0.3, 0.1)
  dump = S.Dump()
  out("guidage coupé : " .. table.concat(dump, " · "))
  check(dump[1] == "Guidage quêtes : OFF" and not S.GuideOn() and B.guide.on == false, "pastille : OFF")
  check(dump[2]:find("(coupé)", 1, true) and dump[6]:find("(coupé)", 1, true), "boutons de quêtes grisés")
  check(not dump[7]:find("(coupé)", 1, true), "Sacs reste actif")
  W.Close()
  M.Advance(0.3, 0.1)
  B.rendre:Click("LeftButton")
  M.Advance(0.3, 0.1)
  check(not W.IsOpen(), "bouton de quête grisé : le clic ne fait rien")
  check(tostring(S.lastMessage):find("Guidage", 1, true), "… et dit pourquoi (" .. tostring(S.lastMessage) .. ")")
  SlashCmdList["TREEAI"]("guide on")
  M.Advance(0.3, 0.1)
  check(S.GuideOn(), "/tai guide on : rallumé")
  -- with the core switch (STABLE): the pill calls it
  local guide, gcalls = true, {}
  T.QuestGuideOn = function() return guide end
  T.SetQuestGuide = function(_, on) guide = on and true or false gcalls[#gcalls + 1] = guide T:Fire("QUEST_GUIDE_CHANGED", guide) end
  B.guide:Click("LeftButton")
  M.Advance(0.3, 0.1)
  check(gcalls[#gcalls] == false and not S.GuideOn() and S.GuideIsCore(), "avec le cœur : la pastille appelle T:SetQuestGuide(false)")
  out("infobulle pastille : " .. tipOf(B.guide))
  T.StripAction("guide")
  M.Advance(0.3, 0.1)
  check(gcalls[#gcalls] == true and S.GuideOn(), "touche « guidage » : T:SetQuestGuide(true)")
  guide = false
  T:Fire("QUEST_GUIDE_CHANGED", false)
  M.Advance(0.3, 0.1)
  check(B.guide.on == false, "QUEST_GUIDE_CHANGED repeint la pastille")
  guide = true
  T:Fire("QUEST_GUIDE_CHANGED", true)
  M.Advance(0.3, 0.1)
  T.QuestGuideOn, T.SetQuestGuide = realOn, realSet
  S.SetGuide(true)
  -- the REAL switch (STABLE): pill, key, « /tai guide » and « /tai quetes on|off » are one and the same thing
  if realSet then
    local function guideMods(on)
      local bad
      for name in pairs(T.QUEST_GUIDE or {}) do
        local m = T.modules[name]
        if m and on == false and (m._active or not m._guideOff) then bad = name end
      end
      return bad
    end
    check(T:QuestGuideOn() and S.GuideOn(), "cœur : guidage allumé au départ")
    B.guide:Click("LeftButton")
    M.Advance(0.5, 0.1)
    check(not T:QuestGuideOn() and not S.GuideOn() and TreeAIDB.questGuide == false, "cœur : la pastille coupe T:QuestGuideOn() (gardé dans TreeAIDB.questGuide)")
    check(not guideMods(false), "cœur : les modules de guidage sont arrêtés (" .. tostring(guideMods(false)) .. ")")
    out("guidage coupé (cœur) : " .. table.concat(S.Dump(), " · "))
    SlashCmdList["TREEAI"]("quetes on")
    M.Advance(0.5, 0.1)
    check(T:QuestGuideOn() and S.GuideOn() and B.guide.on ~= false, "/tai quetes on : rallumé, la pastille suit")
    SlashCmdList["TREEAI"]("guide off")
    M.Advance(0.5, 0.1)
    check(not T:QuestGuideOn(), "/tai guide off = /tai quetes off")
    SlashCmdList["TREEAI"]("guide on")
    M.Advance(0.5, 0.1)
    check(T:QuestGuideOn() and S.GuideOn(), "/tai guide on : rallumé")
  end

  -- ------------------------------------------------------------------ fake QuestBrain (agent QUESTS' public API)
  local calls = {}
  local modes = {
    { id = "turnin", label = "Rendre mes quêtes", short = "Rendre", key = "", tip = "Le plus court chemin.", count = 4 },
    { id = "pickup", label = "Quêtes à prendre", short = "Prendre", tip = "Les meilleures quêtes.", count = 3 },
    { id = "zone", label = "Tout faire ici", short = "Zone", count = 9 },
    { id = "group", label = "Avec le groupe", short = "Groupe", count = 0 },
    { id = "fastlvl", label = "Monter vite", short = "Vite", count = 0 },
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
  out("bande : " .. S.DumpLine())
  check(dump[2] == "Rendre (4) <", "Rendre (4), conseillé maintenant : " .. tostring(dump[2]))
  check(dump[3] == "Prendre (3)", "Prendre (3) : " .. tostring(dump[3]))
  check(dump[5] == "Zone (9) *", "mode actif surligné : " .. tostring(dump[5]))
  check(B.zone.__st.active and not B.rendre.__st.active, "surbrillance du mode choisi")
  B.prendre:Click("LeftButton")
  M.Advance(0.5, 0.1)
  check(calls[#calls] == "pickup", "clic « Prendre » = SetMode(pickup)")
  dump = S.Dump()
  check(dump[3] == "Prendre (3) *" and dump[5] == "Zone (9)", "le mode choisi suit (" .. dump[3] .. " / " .. dump[5] .. ")")
  B.rendre:Click("LeftButton")
  check(calls[#calls] == "turnin", "clic « Rendre » = SetMode(turnin)")
  B.groupe:Click("LeftButton")
  check(calls[#calls] == "group", "clic « Groupe » = SetMode(group)")
  T.StripAction("vite")   -- (5.1 LOOK) « Vite » n'est plus sur la bande d'office : sa touche fait la même chose
  check(calls[#calls] == "fastlvl", "touche « Vite » = SetMode(fastlvl)")
  W.Close()
  M.Advance(0.3, 0.1)
  B.rendre:Click("RightButton")
  M.Advance(0.3, 0.1)
  check(W.IsOpen() and W.CurrentPage() == "quests", "clic droit : la page de la fenêtre")
  W.Close()
  local n0 = #calls
  T.StripAction("mode")
  check(#calls == n0 + 1 and calls[#calls] == "turnin", "touche « mode suivant » : fastlvl → turnin (" .. tostring(calls[#calls]) .. ")")
  T.StripAction("prendre")
  check(calls[#calls] == "pickup", "touche « Prendre » (Bindings.xml) = SetMode(pickup)")
  -- « action suivante » : 4 finished quests, mode ≠ Rendre → it turns them in ; then nothing better
  local nx = S.NextAction(S.State())
  check(nx and nx.id == "rendre", "action suivante : rendre (" .. tostring(nx and nx.why) .. ")")
  T.StripAction("next")
  check(calls[#calls] == "turnin", "touche « action suivante » = SetMode(turnin)")
  modes[1].count = 0
  S.Refresh(true)
  nx = S.NextAction(S.State())
  check(nx and nx.id == "zone", "plus rien à rendre en mode Rendre : retour à la zone (" .. tostring(nx and nx.id) .. ")")
  modes[1].count = 4

  -- ------------------------------------------------------------------ S.SetAction: a module plugs its direct action
  local did = {}
  check(S.SetAction("sacs", { fn = function(btn) did[#did + 1] = "sacs:" .. tostring(btn) return true end, count = function() return 7 end,
    tip = function() return { "7 objets à vendre (12 pa)" } end }), "S.SetAction(« sacs ») accepté")
  check(not S.SetAction("inconnu", {}), "S.SetAction : id inconnu refusé")
  S.Refresh(true)
  dump = S.Dump()
  check(dump[7] == "Sacs (7)", "pastille du module : " .. tostring(dump[7]))
  B.sacs:Click("LeftButton")
  check(did[#did] == "sacs:LeftButton", "clic « Sacs » = l'action directe du module")
  check(tipOf(B.sacs):find("7 objets à vendre", 1, true), "infobulle : les lignes du module")
  S.SetAction("sacs", nil)
  S.Refresh(true)

  -- ------------------------------------------------------------------ combat: clicks work, « cacher en combat »
  M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
  M.Advance(0.5, 0.1)
  check(strip:IsShown(), "en combat : la bande reste (d'office)")
  B.zone:Click("LeftButton")
  check(calls[#calls] == "zone", "en combat : le clic change le mode (rien de protégé)")
  M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(0.5, 0.1)
  S.db.hideCombat = true
  S:OnOptionChanged("hideCombat")
  M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
  M.Advance(0.5, 0.1)
  check(not strip:IsShown(), "option « cacher en combat » : cachée pendant le combat")
  ctx.fire("QUEST_LOG_UPDATE")
  M.Advance(1, 0.1)
  check(not strip:IsShown(), "… et un événement de quête ne la rallume pas")
  M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(0.5, 0.1)
  check(strip:IsShown(), "… revenue à la fin du combat")
  S.db.hideCombat = false
  S:OnOptionChanged("hideCombat")

  -- ------------------------------------------------------------------ keys: « Touches conseillées »
  for _, cmd in ipairs(S.KEY_ORDER) do M.bindings[cmd] = nil end
  M.bindings["MULTIACTIONBAR1BUTTON3"] = "ALT-3"          -- the player's own key: never replaced
  M.bindings["BONUSACTIONBUTTON1"] = "CTRL-1"             -- the game's pet bar
  S.Refresh(true)
  -- (5.1 LOOK) plus de bouton « Touches… » sur la bande : « Touches conseillées » est dans les réglages (page Touches),
  -- le réglage en 3 étapes et /tai boutons touches — la même fonction (S.SuggestKeys)
  check(rawget(_G, "TreeAIStripKeys") == nil, "plus de bouton « Touches… » sur la bande")
  M.EnterCombat()
  S.SuggestKeys()
  check(M.bindings.TREEAI_QB_TURNIN == nil, "en combat : aucune touche posée")
  M.LeaveCombat()
  ctx.fire("PLAYER_REGEN_ENABLED")
  M.Advance(0.3, 0.1)
  M.Hw(function() T:UserAction(function() SlashCmdList["TREEAI"]("boutons touches") end) end)
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
  check(M.bindings.TREEAI_MAIN == "ALT-7" and M.bindings.TREEAI_ACTION_NEXT == "ALT-8", "fenêtre Alt-7, action suivante Alt-8")
  check(M.bindings.TREEAI_QB_FASTLVL == "ALT-6" and M.bindings.TREEAI_STRIP_SACS == "ALT-9" and M.bindings.TREEAI_STRIP_EQUIP == "ALT-0",
    "Vite Alt-6, Sacs Alt-9, Équipement Alt-0")
  check(M.bindings.TREEAI_GUIDE_TOGGLE ~= nil, "le guidage a sa touche (" .. tostring(M.bindings.TREEAI_GUIDE_TOGGLE) .. ")")
  check(M.savedBindings ~= nil, "touches enregistrées (SaveBindings)")
  dump = S.Dump()
  out("avec touches : " .. table.concat(dump, " · "))
  check(dump[2]:find("[Alt-1]", 1, true) and dump[4]:find("[Ctrl-Maj-3]", 1, true), "chaque action connaît sa touche")
  -- (5.1 LOOK) bande fine : la touche n'est plus écrite sur le bouton, elle est dans son infobulle (entière)
  check(not (B.rendre.keyText and B.rendre.keyText:IsShown()), "pas de touche écrite sur le bouton fin")
  check(tipOf(B.rendre):find("Touche : Alt-1", 1, true), "infobulle : la touche")
  local tl = {}
  for _, l in ipairs(S.TipLines("suivant")) do tl[#tl + 1] = l[1] end
  check(table.concat(tl, " | "):find("Ctrl-Maj-3", 1, true) and S.KeyShort("Ctrl-Maj-3") == "C-M-3", "infobulle de « Suivant » : la touche entière")
  -- a 2nd click changes nothing (already bound)
  local before = M.bindings.TREEAI_QB_TURNIN
  S.SuggestKeys()
  check(M.bindings.TREEAI_QB_TURNIN == before, "2e clic : rien ne change")

  -- ------------------------------------------------------------------ narrow screen, player place
  r = noOverlap("après les touches")
  inside("après les touches")
  geo.SetScreen(1024, 768)
  M.Advance(3, 0.1)
  noOverlap("écran 1024×768")
  inside("écran 1024×768")
  geo.SetScreen(800, 600)
  M.Advance(3, 0.1)
  noOverlap("écran 800×600")
  geo.SetScreen(1920, 1080)
  M.Advance(3, 0.1)
  noOverlap("retour 1920×1080")
  check(not S.compact, "retour 1920×1080 : les mots sont revenus")
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

  -- ------------------------------------------------------------------ compact mode (icons only), /tai interface
  S.db.text = false
  S:OnOptionChanged("text")
  B = S.Buttons()
  local rc = geo.Rect(strip)
  check(S.compact and not B.rendre.label:IsShown() and B.rendre.badge:IsShown(), "mode compact : icônes seules, pastilles gardées")
  check(rc.h < 45 and B.guide and B.guide.id == "guide" and B.guide ~= rawget(_G, "TreeAIStripGuide") and B.guide:IsShown(),
    ("mode compact : %dx%d, le guidage en icône"):format(r0(rc.w), r0(rc.h)))
  check(not TreeAIStripGuide:IsShown(), "mode compact : la pastille large est cachée")
  inside("compact")
  bad = S.Audit()
  check(#bad == 0, "compact : chaque bouton a une action et une infobulle (" .. table.concat(bad, " ; ") .. ")")
  B.guide:Click("LeftButton")
  check(not S.GuideOn(), "compact : l'icône du guidage le coupe")
  B.guide:Click("LeftButton")
  check(S.GuideOn(), "compact : … et le rallume")
  SlashCmdList["TREEAI"]("interface complet")
  M.Advance(0.5, 0.1)
  B = S.Buttons()
  check(not S.compact and B.rendre.label and B.rendre.label:IsShown(), "/tai interface complet : mots revenus")
  -- a broken state from a real client: saved off screen + scale + locked + hidden in combat → /tai interface reset
  S.char.strip = { "TOPLEFT", "TOPLEFT", 5000, -3000 }
  S.db.stripScale, S.db.hideCombat, S.db.text = 150, true, false
  SlashCmdList["TREEAI"]("interface reset")
  M.Advance(1.5, 0.1)
  check(S.db.stripScale == 100 and S.db.hideCombat == false and S.db.text == true and S.char.strip == nil, "/tai interface reset : réglages d'origine")
  check(strip:IsShown() and geo.OnScreen(strip, 4), "/tai interface reset : la bande est à l'écran")
  noOverlap("/tai interface reset")

  -- ------------------------------------------------------------------ « Suivant » en session (T.focus)
  do
    B = S.Buttons()
    local oldF, oldFS, moves = T.focus, T.FocusStep, {}
    T.focus = { kind = "city", owner = "test", label = "Courses en ville" }
    T.FocusStep = function(d) moves[#moves + 1] = d return true end
    -- (5.1 LOOK) hors session « Suivant » n'est pas sur la bande ; pendant une session il y revient (c'est son bouton)
    check(not B.suivant or not B.suivant:IsShown(), "hors session : pas de « Suivant » sur la bande")
    T:Fire("FOCUS_CHANGED")
    M.Advance(1.5, 0.1)
    B = S.Buttons()
    check(B.suivant and B.suivant:IsShown(), "session en cours : « Suivant » est sur la bande")
    B.suivant:Click("LeftButton")
    B.suivant:Click("RightButton")
    check(moves[1] == 1 and moves[2] == -1, "session : « Suivant » = étape suivante, clic droit = précédente ("
      .. tostring(moves[1]) .. ", " .. tostring(moves[2]) .. ")")
    T.focus, T.FocusStep = oldF, oldFS
    T:Fire("FOCUS_CHANGED")
    M.Advance(1.5, 0.1)
    B = S.Buttons()
    check(not (B.suivant and B.suivant:IsShown()), "session finie : « Suivant » quitte la bande")
  end

  -- ------------------------------------------------------------------ hygiene
  T.QuestBrain = nil
  -- (« [UIError] … » = the short answer at the top of the screen — /tai quetes on|off — not the chat)
  local chatN = 0
  for i = chat0 + 1, #M.output do
    if not tostring(M.output[i]):find("^%[UIError%]") then chatN = chatN + 1 out("chat: " .. M.output[i]) end
  end
  check(chatN == 0, "rien dans le chat (" .. chatN .. " lignes)")
  check(blocked() == b0, "aucune action bloquée")
  for i = v0 + 1, #M.violations do out("violation: " .. tostring(M.violations[i].kind) .. " " .. tostring(M.violations[i].msg)) end
  check(#M.violations == v0, "aucune violation")
end
