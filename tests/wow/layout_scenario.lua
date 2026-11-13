-- Harness scenario (4.1 LAYOUT): « réfléchis comment tout agencer ». What the player sees by default and the window.
-- Run on the real-client geometry (harness --geometry) with a level-29 hunter in a group of 4 (party frames placed
-- like the default UI, top-left):
--   1. screen dump: every TreeAI frame shown by default = the button strip, the compass, the minimap button and its
--      pins (the « Prochaine étape » card only if it is still switched on: agent STRIP turns it off) ; no Horizon, no
--      Watch, no meter, no radar, no combat bar ; none of them over the party / player / target frames, the quest
--      tracker or the minimap ; nothing in the chat, no blocked action ;
--   2. the one-time 4.1 migration (SAFE_V 3): Horizon, Radar, QuickFight, Quick, MinimapPins off once, Meter / Watch
--      windows and the Abandon card hint hidden once, never again after the player turns them back on ;
--   3. window: first opening on the right half (never the screen centre), then where the player left it ; navigation
--      Quêtes · Groupe · Donjons · Personnage · HV · Conseils pro · Réglages ; every old page id lands on the right place ;
--      Quêtes = the 4 modes (fake cerveau des quêtes) + « Prochaine étape » + route + « À prendre près de toi » (elite
--      badge « à 2+ ») ; Raccourcis lists every TreeAI binding ; Affichage toggles what is on screen ; Groupe has
--      « Partager » ; search finds actions on other pages ; unsolicited notices only on their own page ;
--   4. QoL quest dialogs (opt-in): accept what the plan wants, Maj = not this time, turn in, single reward taken,
--      best reward highlighted (never picked) when there is a choice, gossip turn-in first ;
--   5. Suivi TreeAI (Watch), when the player turns it on, never drawn over Questie's tracker.
-- lua5.1 tests/wow/harness.lua -q --no-storm --geometry ready=1 --profile sur --flavor forever --class HUNTER --level 29 \
--   --scenario tests/wow/layout_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check, fire = ctx.check, ctx.fire
  local geo = M.geo
  if not check(geo ~= nil, "layout_scenario needs --geometry") then return end
  local UIP = UIParent
  local function name(f) return f and f.GetName and f:GetName() or "?" end
  local function out(s) if os.getenv("TREEAI_LAYOUT_DUMP") then io.write(s, "\n") end end

  -- a returning player (no first-run wizard / tour), in a group of 4 ; party frames like the default UI
  T.db.onboarded = T.version or true
  T.db.tour = { done = 999 }
  local wiz = T.GetWizard and T:GetWizard()
  if wiz then wiz:Hide() end
  M.group = { { name = "Ami", class = "WARRIOR", level = 29, online = true }, { name = "Amie", class = "PRIEST", level = 28, online = true },
    { name = "Autre", class = "MAGE", level = 30, online = true } }
  local party = {}
  for i = 1, 4 do
    local f = rawget(_G, "PartyMemberFrame" .. i)
    if type(f) ~= "table" then f = CreateFrame("Frame", "PartyMemberFrame" .. i, UIP) end
    f.__parent, f.__w, f.__h = UIP, 128, 53
    f.__points = { { "TOPLEFT", UIP, "TOPLEFT", 10, -160 - (i - 1) * 60 } }
    f.__shown = i <= 3
    party[#party + 1] = f
  end
  fire("GROUP_ROSTER_UPDATE")
  local chat0 = #M.output
  local blocked0 = tonumber(T._blockedSession) or 0
  M.Advance(40, 0.5)
  if wiz then wiz:Hide() end

  -- ------------------------------------------------------------ 1. what is on screen by default
  local W = T.Window
  local ALLOWED = { "^TreeAIShortcut", "^TreeAIStrip", "^TreeAICompass", "^TreeAIMinimap", "^TreeAIQuestPin", "^TreeAIPath",
    "^TreeAIPin", "^TreeAIHub" }
  local function allowed(n)
    for _, p in ipairs(ALLOWED) do if n:find(p) then return true end end
    return n == "TreeAICard" and W.db.card ~= false   -- (agent STRIP switches the card off by default)
  end
  local shown, bad = {}, {}
  local mm = rawget(_G, "Minimap")
  for _, f in ipairs(M.frames) do
    local n = f.GetName and f:GetName()
    local par = f.GetParent and f:GetParent()
    local top = par == UIP or par == nil or par == mm
    if n and n:find("^TreeAI") and n ~= "TreeAICoreFrame" and top and f:IsVisible() then
      local r = geo.Rect(f)
      local w = r and r.w or 0
      if w > 1 then
        shown[#shown + 1] = n
        out(("SHOWN %s %s"):format(n, r and ("%d,%d %dx%d"):format(r.l, r.b, r.w, r.h) or ""))
        if not allowed(n) then bad[#bad + 1] = n end
      end
    end
  end
  check(#bad == 0, "à l'écran par défaut : boutons rapides, boussole, pastille et repères seulement (en trop : "
    .. table.concat(bad, ", ") .. " ; tout : " .. table.concat(shown, ", ") .. ")")
  for _, n in ipairs({ "TreeAIHorizonFrame", "TreeAIWatchFrame", "TreeAIMeterFrame", "TreeAIRadarFrame", "TreeAIQuickFight", "TreeAIQuickBar" }) do
    local f = rawget(_G, n)
    check(not (f and f:IsVisible()), n .. " : pas à l'écran par défaut")
  end
  -- never over the game's unit / party frames, the quest tracker, the minimap
  local function overlap(a, b)
    return a and b and a.l < b.l + b.w - 1 and a.l + a.w > b.l + 1 and a.b < b.b + b.h - 1 and a.b + a.h > b.b + 1
  end
  local guards = { "PlayerFrame", "TargetFrame", "ObjectiveTrackerFrame", "MinimapCluster" }
  for _, f in ipairs(party) do if f:IsShown() then guards[#guards + 1] = name(f) end end
  for _, n in ipairs(shown) do
    local f = rawget(_G, n)
    local par = f and f:GetParent()
    if f and par ~= mm then
      local r = geo.Rect(f)
      for _, gname in ipairs(guards) do
        local gf = rawget(_G, gname)
        local gr = gf and gf:IsShown() and geo.Rect(gf)
        check(not overlap(r, gr), ("%s pas sur %s"):format(n, gname))
      end
    end
  end
  local nChat = 0
  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then nChat = nChat + 1 out("CHAT " .. M.output[i]) end end
  check(nChat == 0, "rien dans le chat à la connexion (" .. nChat .. ")")
  check((tonumber(T._blockedSession) or 0) == blocked0, "aucune action bloquée")
  -- the unsolicited notices (mode sûr, macros, addons, conseils pro) are never on the screen nor in « Maintenant »
  for _, e in ipairs(T:GetTabLines("now")) do
    check(not e.ownPage, "« Maintenant » sans avis non demandé (" .. tostring(e.text) .. ")")
    check(not tostring(e.text):find("Mes addons", 1, true) and not tostring(e.text):find("macros prêtes", 1, true),
      "« Maintenant » sans pub (" .. tostring(e.text) .. ")")
  end

  -- ------------------------------------------------------------ 2. the one-time 4.1 migration
  do
    local db = T.db
    local keep = { safeV = db.safeV, prof = db.moduleProfile }
    db.safeV, db.moduleProfile = 2, "tout"
    for _, n in ipairs({ "Horizon", "Radar", "QuickFight", "Quick", "MinimapPins", "Meter", "Watch", "Abandon" }) do
      local m = T.modules[n]
      if m and m.db then m.db.enabled = true end
    end
    local Mt, Wt, Ab = T.modules.Meter, T.modules.Watch, T.modules.Abandon
    if Mt and Mt.db then Mt.db.shown = true end
    if Wt and Wt.db then Wt.db.shown = true end
    if Ab and Ab.db then Ab.db.hint = true end
    T:_ApplyProfileOnce()
    for _, n in ipairs({ "Horizon", "Radar", "QuickFight", "Quick", "MinimapPins" }) do
      local m = T.modules[n]
      if m and m.db then check(m.db.enabled == false, "migration 4.1 : " .. n .. " coupé une fois") end
    end
    check(not (Mt and Mt.db and Mt.db.shown) and not (Ab and Ab.db and Ab.db.hint) and not (Wt and Wt.db and Wt.db.shown),
      "migration 4.1 : fenêtres Dégâts / Suivi TreeAI et rappel du journal cachés")
    check(db.safeV == T.SAFE_V and T.SAFE_V >= 3, "migration 4.1 : faite (safeV " .. tostring(db.safeV) .. ")")
    local Hz = T.modules.Horizon
    Hz.db.enabled = true
    T:_ApplyProfileOnce()
    check(Hz.db.enabled == true, "migration 4.1 : une seule fois (rallumé par le joueur = gardé)")
    Hz.db.enabled = false
    db.moduleProfile = keep.prof
    T:SetModuleProfile(keep.prof == "tout" and "tout" or "sur")
    M.Advance(2, 0.5)
  end

  -- ------------------------------------------------------------ 3. the window
  -- a fake « cerveau des quêtes » (agent QUESTS) with its public API
  local setTo
  local QB = {
    Modes = function() return {
      { id = "turnin", label = "Rendre mes quêtes", short = "Rendre", key = "ALT-1", count = 2, active = false, tip = "Rendre" },
      { id = "pickup", label = "Quêtes à prendre", short = "Prendre", count = 5, active = false, tip = "Prendre" },
      { id = "zone", label = "Tout faire ici", short = "Zone", count = 4, active = true, tip = "Zone" },
      { id = "group", label = "Avec le groupe", short = "Groupe", count = 1, active = false, tip = "Groupe" } } end,
    SetMode = function(id) setTo = id return true end,
    Mode = function() return "zone" end,
    Label = function() return "Tout faire ici" end,
    PickupList = function(n)
      local l = { { questID = 501, title = "Pierres de foudre", level = 29, npc = "Garde", dist = 120, why = "1 200 XP · sur ton trajet" },
        { questID = 502, title = "Le chef des Kolkars", level = 30, npc = "Capitaine", dist = 300, why = "élite", elite = true } }
      local o = {}
      for i = 1, math.min(n or 5, #l) do o[i] = l[i] end
      return o
    end,
    TurnInList = function() return {} end,
    GroupList = function()
      local id
      for i, q in ipairs(M.quests) do if not q.header and not id then id = q.id or (1000 + i) end end
      return { { questID = id or 7101, title = "Protéger le guide", n = 0, share = true } }
    end,
  }
  T.QuestBrain = QB
  W.Open()
  M.Advance(1, 0.5)
  local win = W.GetWindow()
  local sw, sh = geo.Screen()
  local r = geo.Rect(win)
  out(("WINDOW %d,%d %dx%d on %dx%d"):format(r.l, r.b, r.w, r.h, sw, sh))
  check(r and r.l >= sw / 2 - 1 and r.l + r.w <= sw + 1, ("1re ouverture : moitié droite de l'écran (%d..%d sur %d)"):format(r.l, r.l + r.w, sw))
  check(geo.OnScreen(win), "fenêtre sur l'écran")
  -- then where the player left it
  geo.Drag(win.nav, -200, -40)
  local r2 = geo.Rect(win)
  W.Close() M.Advance(1, 0.5)
  W.Open() M.Advance(1, 0.5)
  local r3 = geo.Rect(win)
  check(math.abs(r3.l - r2.l) < 2 and math.abs(r3.b - r2.b) < 2, "rouverte là où le joueur l'a laissée")
  -- navigation
  local ids = {}
  for _, p in ipairs(W.PAGES) do if not p.hidden then ids[#ids + 1] = p.id end end
  -- (5.0 LOOK) 11 catégories au plus : « Sacs » entre quand son module inscrit sa page, Macros & touches → Réglages
  check((table.concat(ids, ","):gsub(",bags", "")) == "quests,route,group,dungeon,gear,prof,hv,hunter,coach,settings" and #ids <= 11,
    "navigation : 11 catégories au plus (" .. table.concat(ids, ",") .. ")")
  local labels = {}
  for _, p in ipairs(W.PAGES) do if not p.hidden then labels[#labels + 1] = p.label end end
  check((table.concat(labels, ","):gsub(",Sacs", "")) == "Quêtes,Trajet,Groupe,Donjons,Équipement,Métiers,HV,Chasseur,Conseils,Réglages", "libellés : " .. table.concat(labels, ","))
  -- old ids land on the right place
  local REDIR = { now = { "quests", "quests" }, route = { "route", "route" }, journal = { "journal", "quests" },
    brain = { "brain", "quests" }, tools = { "tools", "settings" }, modules = { "modules", "settings" },
    options = { "options", "settings" }, addons = { "addons", "settings" }, macros = { "macros", "settings" },
    prof = { "prof", "prof" }, talents = { "talents", "hunter" }, gear = { "gear", "gear" }, keys = { "keys", "settings" },
    binds = { "keys", "settings" },
    perso = { "gear", "gear" }, pet = { "pet", "hunter" },
    display = { "display", "settings" }, group = { "group", "group" }, dungeon = { "dungeon", "dungeon" }, hv = { "hv", "hv" },
    coach = { "coach", "coach" } }
  for old, want in pairs(REDIR) do
    if W.pageIndex[want[1]] then
      W.Open(old)
      M.Advance(0.2, 0.1)
      check(W.CurrentPage() == want[1] and W.NavOf(W.CurrentPage()) == want[2],
        ("W.Open(%q) → %s sous %s (%s / %s)"):format(old, want[1], want[2], tostring(W.CurrentPage()), tostring(W.NavOf(W.CurrentPage()))))
      for _, rr in ipairs(W.lastLay.rects) do
        if rr.label:find("Erreur d'affichage", 1, true) then check(false, old .. " : " .. rr.label) end
      end
    end
  end
  -- slash commands of the strip / old shortcuts still open the right page
  W.Close()
  T.ToggleMain("route")
  check(W.IsOpen() and W.CurrentPage() == "route", "touche « Trajet » : page Trajet")
  -- (5.0 LOOK) Réglages : sous-liste à gauche (façon Leatrix Plus), la dernière sous-page rouverte
  W.Select("keys")
  W.Select("macros")
  W.Select("settings")
  check(W.CurrentPage() == "macros", "Réglages rouvre la dernière sous-page (Macros)")
  local side = {}
  for _, rr in ipairs(W.lastLay.rects) do if rr.kind == "side" then side[#side + 1] = rr.label end end
  check(table.concat(side, ","):find("^Affichage,Profil : Simple / Complet,Touches,Macros,Sorts & barres,Fonctions,Mes addons,Options,Outils"),
    "Réglages : sous-liste (" .. table.concat(side, ",") .. ")")
  -- Quêtes : the 4 modes on top, next step, route, « À prendre près de toi » with the elite badge
  W.Select("quests")
  local Ly = W.lastLay
  -- (5.0 LOOK) 3 grosses tuiles d'action en tête (Rendre · Prendre · Tout faire ici), les autres modes dans leur section
  local tiles, order = {}, {}
  for _, rr in ipairs(Ly.rects) do
    if rr.kind == "tile" or rr.kind == "action" then tiles[#tiles + 1] = rr.label end
    if rr.kind == "header" or rr.kind == "tile" then order[#order + 1] = rr.label end
  end
  check(table.concat(tiles, ",") == "Rendre mes quêtes,Quêtes à prendre,Tout faire ici,Avec le groupe", "Quêtes : les 4 modes en gros boutons (" .. table.concat(tiles, ",") .. ")")
  check(Ly.rects[1].kind == "action" and Ly.rects[3].kind == "action" and Ly.rects[4].kind == "toggle", "Quêtes : 3 tuiles d'action tout en haut, puis la case « Guidage des quêtes »")
  local hasPick, hasElite = false, false
  for _, rr in ipairs(Ly.rects) do
    if rr.kind == "header" and rr.label == "À prendre près de toi" then hasPick = true end
  end
  for _, b in ipairs(win.content.__children or {}) do
    if b:IsShown() and b.s and tostring(b.s:GetText() or ""):find("Élite · à 2+", 1, true) then hasElite = true end
  end
  check(hasPick and hasElite, "Quêtes : « À prendre près de toi » avec le badge « Élite · à 2+ »")
  -- tile click = SetMode
  for _, b in ipairs(win.content.__children or {}) do
    if b:IsShown() and b.titleText == "Rendre mes quêtes" and b.fn then b:GetScript("OnClick")(b) end
  end
  check(setTo == "turnin", "clic sur « Rendre mes quêtes » : le mode change (" .. tostring(setTo) .. ")")
  -- no API → the route as before (guarded)
  T.QuestBrain = nil
  W.Render()
  local anyTile = false
  for _, rr in ipairs(W.lastLay.rects) do if rr.kind == "tile" or rr.kind == "action" then anyTile = true end end
  check(not anyTile and #W.lastLay.rects > 0, "sans cerveau des quêtes : la page Quêtes comme avant (pas de modes)")
  T.QuestBrain = QB
  -- Suivi Forever shows its OWN route: one line, two choices, never asked again once chosen
  local spfDB = { tracker = true, journeyTracker = true }
  rawset(_G, "ShortestPathForever", { API = { Estimate = function() return nil end } })
  rawset(_G, "ShortestPathForeverDB", spfDB)
  local function spfLine()
    for _, rr in ipairs(W.lastLay.rects) do if rr.label:find("Suivi Forever affiche son propre trajet", 1, true) then return rr end end
  end
  W.Render()
  local firstText
  for _, rr in ipairs(W.lastLay.rects) do if rr.kind ~= "action" then firstText = rr break end end
  check(spfLine() and firstText and firstText.label:find("Suivi Forever", 1, true), "Suivi Forever chargé : une ligne en tête de la page Quêtes (sous les tuiles d'action)")
  for _, b in ipairs(win.content.__children or {}) do
    if b:IsShown() and b.labelText == "Le rendre silencieux" and b.fn then b:GetScript("OnClick")(b) end
  end
  check(spfDB.tracker == false and T.Addons.db.spfChoice == "silent", "« Le rendre silencieux » : son suivi coupé, choix gardé")
  spfDB.tracker = true
  W.Render()
  check(not spfLine(), "choix fait : la ligne ne revient jamais")
  rawset(_G, "ShortestPathForever", nil)
  rawset(_G, "ShortestPathForeverDB", nil)
  -- Raccourcis : every TreeAI binding
  W.Select("keys")
  local keyRows = 0
  local hasMain = false
  for _, rr in ipairs(W.lastLay.rects) do
    if rr.kind == "row" then keyRows = keyRows + 1 if rr.label:find("ouvrir / fermer la fenêtre", 1, true) then hasMain = true end end
  end
  check(hasMain and keyRows >= 5, "Raccourcis : toutes les touches TreeAI (" .. keyRows .. ")")
  -- Affichage : Horizon off, one click on, one click off
  W.Select("display")
  local function displayRow(prefix)
    for _, b in ipairs(win.content.__children or {}) do
      if b:IsShown() and b.entry and tostring(b.entry.label):find(prefix, 1, true) == 1 then return b end
    end
  end
  local hz = displayRow("Bande d'horizon")
  check(hz and hz.on == false, "Affichage : bande d'horizon coupée")
  if hz then hz:GetScript("OnClick")(hz) end
  local Hz = T.modules.Horizon
  check(Hz.db.enabled ~= false and Hz.db.shown == true, "Affichage : un clic l'allume")
  W.Render()
  hz = displayRow("Bande d'horizon")
  if hz then hz:GetScript("OnClick")(hz) end
  check(Hz.db.shown == false, "Affichage : un clic la coupe")
  -- Groupe : « Partager »
  local pushed = {}
  rawset(_G, "QuestLogPushQuest", function(i) pushed[#pushed + 1] = i end)
  W.Select("group")
  local share
  for _, rr in ipairs(W.lastLay.rects) do if rr.kind == "button" and rr.label:find("Partager", 1, true) then share = rr end end
  check(share ~= nil, "Groupe : bouton « Partager »")
  W.ShareAll()
  check(#pushed >= 1, "Partager : la quête est proposée au groupe (" .. #pushed .. ")")
  -- search finds actions on other pages
  win.search:SetText("horizon")
  win.search:GetScript("OnTextChanged")(win.search)
  local found = false
  for _, rr in ipairs(W.lastLay.rects) do if rr.label:find("Bande d'horizon", 1, true) then found = true end end
  check(found, "recherche « horizon » depuis Groupe : trouvée dans Réglages → Affichage")
  win.search:SetText("")
  win.search:GetScript("OnTextChanged")(win.search)
  -- an unsolicited notice shows on its own page only
  T:Suggest("Macros:ready", "Nouvelles macros prêtes : clique pour installer", 2, { ttl = 0, ownPage = "macros" })
  local inNow = false
  for _, e in ipairs(T:GetTabLines("now")) do if e.id == "Macros:ready" then inNow = true end end
  check(not inNow, "avis Macros : pas dans « Maintenant »")
  W.Select("macros")
  local onPage = false
  for _, rr in ipairs(W.lastLay.rects) do if rr.label:find("macros prêtes", 1, true) then onPage = true end end
  check(onPage, "avis Macros : en tête de la page Macros")
  T:ClearSuggestion("Macros:ready")
  -- measures: every page (sub-pages too) at UI scale 1 and 1.15 — nothing overlaps, nothing leaves the column, every
  -- tile / tab label fits its box (game font ≈ 6.2 px per character), headers / empty states present
  local function noOverlap(L2, what)
    local rr = L2 and L2.rects or {}
    for i = 1, #rr do
      for j = i + 1, #rr do
        local a, b = rr[i], rr[j]
        if a.x < b.x + b.w - 0.5 and b.x < a.x + a.w - 0.5 and a.y < b.y + b.h - 0.5 and b.y < a.y + a.h - 0.5 then
          return false, ("%s : « %s » (%s) chevauche « %s » (%s)"):format(what, a.label, a.kind, b.label, b.kind)
        end
      end
      if rr[i].x + rr[i].w > L2.W + 0.5 then return false, what .. " : « " .. rr[i].label .. " » sort de la colonne" end
    end
    return true
  end
  local function ulen(s2) local _, n = tostring(s2):gsub("[^\128-\191]", "") return n end
  for _, scale in ipairs({ 1, 1.15 }) do
    geo.SetUIScale(geo.finalScale * scale)
    M.Advance(0.5, 0.1)
    for id in pairs(W.pageIndex) do
      if id ~= "tab" and id ~= "hunter" and id ~= "binds" and id ~= "settings" then
        W.Select(id)
        local L2 = W.lastLay
        local ok, msg = noOverlap(L2, ("échelle %.2f, page %s"):format(scale, id))
        check(ok, msg or "")
        check(#L2.rects > 0, ("échelle %.2f, page %s : du contenu ou un état vide qui dit quoi faire"):format(scale, id))
        for _, rr in ipairs(L2.rects) do
          if rr.kind == "tile" or rr.kind == "tab" or rr.kind == "button" then
            check(ulen(rr.label) * 6.2 <= rr.w - 6, ("échelle %.2f, page %s : « %s » tient dans son bouton (%d px)"):format(scale, id, rr.label, rr.w))
          end
        end
      end
    end
    check(geo.OnScreen(win), ("échelle %.2f : fenêtre sur l'écran"):format(scale))
  end
  geo.SetUIScale(geo.finalScale)
  M.Advance(0.5, 0.1)
  W.Close()

  -- ------------------------------------------------------------ 4. QoL quest dialogs
  local calls = {}
  local function stub(n, ret) rawset(_G, n, function(...) calls[#calls + 1] = { n, ... } return ret end) end
  local function called(n) for _, c in ipairs(calls) do if c[1] == n then return c end end end
  stub("AcceptQuest") stub("CompleteQuest") stub("GetQuestReward")
  rawset(_G, "IsQuestCompletable", function() return true end)
  rawset(_G, "GetQuestMoneyToGet", function() return 0 end)
  rawset(_G, "QuestGetAutoAccept", function() return false end)
  local qid = 501
  rawset(_G, "GetQuestID", function() return qid end)
  rawset(_G, "GetTitleText", function() return qid == 501 and "Pierres de foudre" or "Une autre" end)
  local gossip = { active = {}, avail = {} }
  rawset(_G, "C_GossipInfo", {
    GetActiveQuests = function() return gossip.active end, GetAvailableQuests = function() return gossip.avail end,
    GetOptions = function() return {} end,
    SelectActiveQuest = function(id) calls[#calls + 1] = { "GI.SelectActiveQuest", id } end,
    SelectAvailableQuest = function(id) calls[#calls + 1] = { "GI.SelectAvailableQuest", id } end })
  T:SetModuleEnabled("QoL", true)
  local Qo = T.QoL
  check(Qo and Qo._active and Qo.db.questAuto == false, "Confort : « Accepter / rendre tout seul » coupé d'office")
  fire("QUEST_DETAIL")
  check(not called("AcceptQuest"), "option coupée : rien n'est accepté")
  Qo.SetQuestAuto(true)
  fire("QUEST_DETAIL")
  check(called("AcceptQuest"), "option allumée : la quête du plan est acceptée")
  calls = {}
  qid = 777
  fire("QUEST_DETAIL")
  check(not called("AcceptQuest"), "quête hors du plan : pas acceptée")
  qid = 501
  M.shift = true
  fire("QUEST_DETAIL")
  check(not called("AcceptQuest"), "Maj enfoncée : pas cette fois")
  M.shift = false
  fire("QUEST_PROGRESS")
  check(called("CompleteQuest"), "quête finie : rendue")
  -- one reward: taken ; a choice: highlighted, never picked
  M.AddItem(9201, { name = "Bague terne", sellPrice = 50, classID = 15 })
  M.AddItem(9202, { name = "Pierre polie", sellPrice = 300, classID = 15 })
  M.questChoices = { 9201 }
  calls = {}
  T.modules.QoL.db.questTake = true   -- (4.1.2) prendre la récompense unique = option à part, coupée d'office
  fire("QUEST_COMPLETE")
  local gr = called("GetQuestReward")
  check(gr and gr[2] == 1, "une seule récompense : prise")
  local btn2 = CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem2", UIP)
  btn2.__w, btn2.__h, btn2.__points = 140, 40, { { "CENTER", UIP, "CENTER", 0, 0 } }
  local btn1 = CreateFrame("Button", "QuestInfoRewardsFrameQuestInfoItem1", UIP)
  btn1.__w, btn1.__h, btn1.__points = 140, 40, { { "CENTER", UIP, "CENTER", -160, 0 } }
  M.questChoices = { 9201, 9202 }
  calls = {}
  fire("QUEST_COMPLETE")
  M.Advance(1, 0.2)
  local glow = rawget(_G, "TreeAIRewardGlow")
  check(not called("GetQuestReward"), "choix de récompense : jamais pris à ta place")
  -- (5.0 GEAR) Équipement est allumé en mode sûr : c'est lui qui entoure (QoL ne le fait que s'il est coupé) — une seule fois
  local Gm = T.modules.Gear
  local bestI = (Gm and Gm._active and Gm.lastReward and Gm.lastReward.i) or (Qo.lastBest and Qo.lastBest.i)
  check(glow and glow:IsShown() and bestI == 2, "choix : la meilleure (la plus chère ici) entourée (" .. tostring(bestI) .. ")")
  if glow then
    local gR, bR = geo.Rect(glow), geo.Rect(btn2)
    check(gR and bR and overlap(gR, bR), "le cadre entoure le bon bouton")
  end
  fire("QUEST_FINISHED")
  M.questChoices = {}
  gossip.active = { { questID = 11, isComplete = false }, { questID = 12, isComplete = true } }
  gossip.avail = { { questID = 501 } }
  calls = {}
  fire("GOSSIP_SHOW")
  local ga = called("GI.SelectActiveQuest")
  check(ga and ga[2] == 12, "dialogue : la quête finie d'abord")
  gossip.active = {}
  calls = {}
  fire("GOSSIP_SHOW")
  local gv = called("GI.SelectAvailableQuest")
  check(gv and gv[2] == 501, "dialogue : puis la quête que le plan veut")
  Qo.SetQuestAuto(false)

  -- ------------------------------------------------------------ 5. Watch never over Questie's tracker
  local Wt = T.Watch
  local qf = CreateFrame("Frame", "Questie_BaseFrame", UIP)
  qf.__w, qf.__h, qf.__points = 260, 400, { { "TOPRIGHT", UIP, "TOPRIGHT", -40, -230 } }
  qf.__shown = true
  T:SetModuleEnabled("Watch", true)
  Wt.db.shown = true
  if Wt.char then Wt.char.pos = { point = "TOPRIGHT", rel = "TOPRIGHT", x = -60, y = -240 } end   -- auto place over it
  Wt.Apply()
  M.Advance(1, 0.5)
  local wf = rawget(_G, "TreeAIWatchFrame")
  local wR = wf and wf:IsShown() and geo.Rect(wf)
  local qR = geo.Rect(qf)
  check(not overlap(wR, qR), "Suivi TreeAI jamais sur le suivi de Questie (" .. tostring(Wt.blockedBy) .. ")")
  check((wf and wf:IsShown()) or Wt.blockedBy ~= nil, "Suivi TreeAI : à côté, ou caché avec une ligne sur la page Quêtes")
  T:SetModuleEnabled("Watch", false)
  qf:Hide()

  -- the whole run: nothing in the chat, no blocked action
  nChat = 0
  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then nChat = nChat + 1 out("CHAT " .. M.output[i]) end end
  check(nChat == 0, "rien dans le chat de tout le parcours (" .. nChat .. ")")
  check((tonumber(T._blockedSession) or 0) == blocked0, "aucune action bloquée de tout le parcours")
end
