-- Harness scenario (5.1, agent LOOK) : « une vraie maj graphique » — calme, simple, branchée sur les autres addons.
-- Run by tests/wow/test_look6.py on the player's real screen (2000×1125, UI scale 0.64, tests/wow/realui_scenario.lua :
-- meters, the « Suivi Forever » column, the game's quest tracker, custom unit frames and bars). LOOK6_PART picks a part :
--   look     tokens V6 (flat, thin, translucent, ONE accent, gold for titles only), the strip on ONE row of 24–28 real
--            pixels that fades (idle 60 %, combat 30 %, hover 100 %), « Suivant » / « Vite » off the strip by default but
--            still doing their job, a 5.0 saved position kept, /tai interface reset ; one small short toast, never in
--            the middle, « discret » on by default ;
--   login    a brand-new player : NO window by itself — one small line « Régler · Plus tard » ; nothing proposes
--            itself in the first minute nor in combat ; « Plus tard » is remembered ; /tai bienvenue still opens it ;
--   window   opens on « Maintenant » (≤ 5 lines, one button each, each button does something), ≤ 9 categories in plain
--            words, at most ONE primary action per page, old pages still reachable, search still finds everything ;
--   addons   Shortest Path Forever present : no TreeAI compass, no second route list ; « Y aller » hands the
--            destination to its public API (else the game's waypoint) on the player's click ; the small « Maintenant »
--            block sits against its column (same width, never on a frame, never anchored to it) ; nothing is written
--            in another addon's tables, none of its frames is touched ; every link switches off ; Leatrix Plus doing
--            the selling / repairing = TreeAI does not offer it twice ; the probe names what was found ;
--   connect  no dead end : advice lines without a click get the obvious one (repairer, vendor, AH, talents).
-- Dumps « ce que le joueur voit ». Fake names only. Nothing in the chat, zero blocked action, zero violation.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "look6_scenario needs --geometry w=2000,h=1125") then return end
  local UIP = UIParent
  local PART = os.getenv("LOOK6_PART") or "look"
  local SCALE = 0.64
  local function out(s) io.write("LOOK6 ", tostring(s), "\n") end
  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end
  local function plain(s) return (T.PlainText and T.PlainText(tostring(s or "")) or tostring(s or "")):gsub("|T.-|t", ""):gsub("%s+", " ") end
  local chat0, b0, v0 = #M.output, #(M.blocked or {}), #M.violations
  rawset(_G, "GetPhysicalScreenSize", function() return geo.opts.w, geo.opts.h end)
  geo.SetUIScale(SCALE)
  local PX = SCALE * geo.opts.h / 768
  local SW, SH = geo.Screen()

  -- ------------------------------------------------------------------ the player's screen (occupied rectangles)
  local OCC = {}
  local function occupy(label, name, x0, y0, x1, y1)
    local f = name and rawget(_G, name) or nil
    if type(f) ~= "table" then f = CreateFrame("Frame", name, UIP) end
    f.__parent = UIP
    f.__w, f.__h, f.__shown = (x1 - x0) / PX, (y1 - y0) / PX, true
    f.__points = { { "TOPLEFT", UIP, "TOPLEFT", x0 / PX, -y0 / PX } }
    OCC[#OCC + 1] = { label = label, f = f }
    return f
  end
  for _, n in ipairs({ "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "MainMenuBar", "MultiBarBottomLeft", "MultiBarRight", "CastingBarFrame" }) do
    local f = rawget(_G, n)
    if type(f) == "table" then f.__shown = false f.__points = {} end
  end
  occupy("compteur de dégâts", nil, 0, 0, 262, 110)
  occupy("compteur de menace", nil, 262, 0, 525, 110)
  local column = occupy("colonne Suivi Forever", "ShortestPathForeverTrackerFrame", 0, 400, 250, 930)
  occupy("discussion", "ChatFrame1", 0, 900, 490, 1125)
  occupy("barre d'XP", nil, 810, 18, 1190, 36)
  occupy("flèche et texte du repère", nil, 880, 40, 1120, 245)
  occupy("minimap", "MinimapCluster", 1780, 30, 2000, 250)
  local tracker = occupy("suivi de quêtes du jeu", "ObjectiveTrackerFrame", 1745, 250, 2000, 570)
  occupy("cadres d'unité", nil, 615, 738, 840, 915)
  occupy("barres d'action", nil, 740, 840, 1265, 1125)
  occupy("micro-menu et sacs", nil, 1275, 1080, 1850, 1125)
  occupy("améliorations", "BuffFrame", 1590, 0, 1730, 40)
  -- Shortest Path Forever with its public API (calls recorded) ; nothing in it may ever be written by TreeAI
  local navigated = {}
  local SPF = { API = {
    Navigate = function(owner, mapID, x, y, title) navigated[#navigated + 1] = { owner = owner, mapID = mapID, x = x, y = y, title = title } return true end,
    Estimate = function() return 60 end,
    Active = function() return #navigated > 0 end,
  } }
  if PART ~= "login" then rawset(_G, "ShortestPathForever", SPF) rawset(_G, "ShortestPathForeverDB", { tracker = true, journeyTracker = true }) end
  local function snapshot(t, depth)
    depth = depth or 0
    local n = 0
    for k, v in pairs(t) do
      n = n + 1
      if type(v) == "table" and depth < 3 then n = n + snapshot(v, depth + 1) * 7 elseif type(v) ~= "function" then n = n + #tostring(k) + #tostring(v) end
    end
    return n
  end
  local spfSig = snapshot(SPF) + snapshot(rawget(_G, "ShortestPathForeverDB") or {})
  local colPoints = #column.__points

  -- ------------------------------------------------------------------ the character : level-30 hunter in a group
  M.LeaveCombat()
  M.player.level = 30
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de l'Honneur"
  M.player.resting = true
  M.mapNames[1454] = "Orgrimmar"
  M.pos = { mapID = 1454, x = 0.50, y = 0.60 }
  M.quests = {
    { header = "Désolace" },
    { id = 801, title = "Chasse de test", level = 39, complete = true, objectives = {} },
    { id = 802, title = "Collection de test", level = 39, objectives = { { text = "Os de test : 0/10", done = false, have = 0, need = 10 } } },
  }
  M.group = { { name = "Amie", class = "PRIEST", level = 28, online = true }, { name = "Compagnon", class = "WARRIOR", level = 29, online = true } }
  local S, W, Th, A, N = T.Shortcuts, T.Window, T.Theme, T.Addons, T.Now
  if not check(S and W and Th and A and N, "Shortcuts, Window, Theme, Addons, Now chargés") then return end
  local function overlap(a, b) return a.l < b.l + b.w - 0.5 and b.l < a.l + a.w - 0.5 and a.b < b.b + b.h - 0.5 and b.b < a.b + a.h - 0.5 end
  local function free(f, tag)
    local r = geo.Rect(f)
    if not check(r, tag .. " : mesurable") then return nil end
    check(geo.OnScreen(f, 2), tag .. " : à l'écran")
    for _, oc in ipairs(OCC) do
      local o = geo.Rect(oc.f)
      if o then check(not overlap(r, o), tag .. " ne touche pas « " .. oc.label .. " »") end
    end
    return r
  end
  local function click(f, btn) M.Hw(function() f:Click(btn or "LeftButton") end) end

  -- ====================================================================================== login (brand-new player)
  if PART == "login" then
    ctx.fire("PLAYER_ENTERING_WORLD", true, false) ctx.fire("GROUP_ROSTER_UPDATE") ctx.fire("QUEST_LOG_UPDATE")
    local wizSeen, tourSeen = false, false
    local function watch(sec)
      local t = 0
      while t < sec do
        M.Advance(1, 0.1)
        t = t + 1
        local wz = T.GetWizard and T:GetWizard()
        if wz and wz:IsShown() then wizSeen = true end
        if T.Tour and T.Tour.active then tourSeen = true end
      end
    end
    watch(12)
    local hello = rawget(_G, "TreeAIHello")
    if check(hello and hello:IsShown(), "1er lancement : la petite ligne « TreeAI est prêt » est à l'écran") then
      local r = free(hello, "ligne du 1er lancement")
      if r then
        out(("1er lancement : ligne %d×%d px en x %d, y %d (depuis le haut)"):format(r0(r.w * PX), r0(r.h * PX), r0(r.l * PX), r0((SH - r.b - r.h) * PX)))
        check(r.h * PX <= 26 and r.w * PX <= 340, "la ligne est petite (" .. r0(r.w * PX) .. "×" .. r0(r.h * PX) .. " px)")
        check(SH - r.b - r.h < SH * 0.36, "la ligne n'est pas au milieu de l'écran")
      end
      local texts = {}
      for _, c in ipairs(hello.__children) do if c.__kind == "FontString" and c:GetText() then texts[#texts + 1] = c:GetText() end end
      out("1er lancement : « " .. table.concat(texts, " ") .. " » [Régler (1 min)] [Plus tard]")
    end
    check(not wizSeen, "1er lancement : l'assistant ne s'ouvre PAS tout seul")
    check(not tourSeen, "1er lancement : la visite guidée ne démarre PAS toute seule")
    check(T.Calm and T.Calm.Ok and not T.Calm.Ok("spec"), "1re minute : aucune question ne se propose (T.Calm.Ok)")
    -- nothing by itself for 6 minutes of play (the line fades by itself)
    watch(360)
    check(not wizSeen and not tourSeen, "6 minutes de jeu : ni assistant ni visite ouverts tout seuls")
    check(not (hello and hello:IsShown()), "la ligne s'est effacée toute seule")
    local okSpec = T.Calm.Ok("spec")
    check(okSpec == true, "après la 1re minute, hors combat : une question peut se proposer")
    local okMount, why = T.Calm.Ok("mount")
    check(okMount == false and why == "busy", "une seule à la fois (2e refusée : " .. tostring(why) .. ")")
    M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
    T.Calm.cur = nil
    local okC, whyC = T.Calm.Ok("spec")
    check(okC == false and whyC == "combat", "en combat : rien ne se propose")
    check(T:Hello() == false, "en combat : la ligne ne revient pas")
    M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
    M.Advance(2, 0.1)
    -- « Plus tard » is remembered ; the setup stays reachable by slash
    check(T:Hello() == true and hello:IsShown(), "la ligne peut être reproposée (2e fois)")
    click(rawget(_G, "TreeAIHelloLater"))
    check(not hello:IsShown() and T.Calm.IsLater("setup"), "« Plus tard » : ligne fermée, choix retenu")
    check(T:Hello() == false, "« Plus tard » retenu : la ligne ne revient plus toute seule")
    check(type(TreeAIDB.quiet) == "table" and TreeAIDB.quiet.later.setup ~= nil, "« Plus tard » est gardé dans les variables sauvegardées")
    local lines = N.Dump(5)
    for _, l in ipairs(lines) do check(not l:find("Réglage en 1 minute", 1, true), "« Plus tard » : la ligne de réglage quitte aussi « Maintenant »") end
    M.RunSlash("/tai bienvenue")
    M.Advance(0.5, 0.1)
    local wz = T.GetWizard and T:GetWizard()
    check(wz and wz:IsShown(), "/tai bienvenue ouvre toujours le réglage en 3 étapes")
    if wz then wz:Hide() end
    M.RunSlash("/tai visite")
    M.Advance(1, 0.1)
    check(T.Tour and (T.Tour.active or T.Tour.pending), "/tai visite lance toujours la visite guidée")
  end

  if PART ~= "login" then
    if T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
    ctx.fire("PLAYER_ENTERING_WORLD", true, false) ctx.fire("GROUP_ROSTER_UPDATE") ctx.fire("QUEST_LOG_UPDATE")
    M.Advance(30, 0.1)
  end

  -- ====================================================================================== look
  if PART == "look" then
    -- tokens
    check(Th.V == 6, "thème V6")
    local br, bg, bb, ba = Th:Color("border")
    check(br == bg and bg == bb and ba <= 0.2, ("liseré neutre et fin (%.2f %.2f %.2f, alpha %.2f) — plus de liseré doré"):format(br, bg, bb, ba))
    local _, _, _, bga = Th:Color("bg")
    check(bga < 0.95, ("fenêtre translucide (alpha %.2f)"):format(bga))
    local _, _, _, sa = Th:Color("small")
    check(sa <= 0.5, ("petits cadres translucides (alpha %.2f)"):format(sa))
    local ar, ag, ab = Th:Color("accent")
    local onr, ong, onb = Th:Color("on")
    check(ar == onr and ag == ong and ab == onb, "UN accent : « allumé » = la couleur d'accent")
    local cr, cg, cb = Th:Color("count")
    check(cr == ar and cg == ag and cb == ab, "UN accent : les nombres sont en accent (plus de pastille rouge)")
    check(Th.TOKENS.font.L <= 14 and Th.TOKENS.font.H <= 16 and Th.TOKENS.font.S == 10, "petit texte net (S 10, L ≤ 14, H ≤ 16)")
    for _, l in ipairs(Th:Describe()) do out("JETON " .. l) end
    -- the strip : one slim row
    local strip = S.Strip()
    if check(strip and strip:IsShown(), "la bande est à l'écran") then
      local r = free(strip, "bande")
      local hpx = r and r.h * PX or 0
      out(("bande : %d×%d px réels en x %d, y %d ; alpha %.2f"):format(r0(r.w * PX), r0(hpx), r0(r.l * PX), r0((SH - r.b - r.h) * PX), strip:GetAlpha()))
      check(hpx >= 24 and hpx <= 28, ("bande fine : %.1f px réels de haut (24 à 28)"):format(hpx))
      out("bande : " .. S.DumpLine())
      local ys, n = {}, 0
      for id, b in pairs(S.Buttons()) do
        if b:IsShown() then
          n = n + 1
          local br2 = geo.Rect(b)
          ys[r0(br2.b + br2.h / 2)] = true
          check(br2.l >= r.l - 0.5 and br2.l + br2.w <= r.l + r.w + 0.5, "« " .. id .. " » dans la bande")
        end
      end
      local rowsN = 0
      for _ in pairs(ys) do rowsN = rowsN + 1 end
      check(rowsN == 1, "UNE rangée (" .. rowsN .. ")")
      check(n >= 7 and n <= 9, "7 à 9 boutons sur la bande (" .. n .. ")")
      local btns = S.Buttons()
      check(not (btns.suivant and btns.suivant:IsShown()) and not (btns.vite and btns.vite:IsShown()), "« Suivant » et « Vite » ne sont plus sur la bande d'office")
      check(not rawget(_G, "TreeAIStripKeys"), "plus de bouton « Touches… » sur la bande")
      -- fade : idle, hover, combat
      check(math.abs(strip:GetAlpha() - 0.60) < 0.01, ("au repos : estompée (%.2f)"):format(strip:GetAlpha()))
      M.Script(btns.rendre, "OnEnter")
      check(strip:GetAlpha() == 1, "au survol : pleine")
      M.Script(btns.rendre, "OnLeave")
      M.Advance(1.2, 0.1)
      check(math.abs(strip:GetAlpha() - 0.60) < 0.01, "la souris partie : estompée de nouveau")
      M.EnterCombat() ctx.fire("PLAYER_REGEN_DISABLED")
      M.Advance(0.3, 0.1)
      check(math.abs(strip:GetAlpha() - 0.30) < 0.01, ("en combat : %.2f"):format(strip:GetAlpha()))
      check(strip:IsShown(), "en combat : toujours là (trouvable)")
      M.LeaveCombat() ctx.fire("PLAYER_REGEN_ENABLED")
      M.Advance(0.5, 0.1)
      S.db.fade = false S:OnOptionChanged("fade")
      check(strip:GetAlpha() == 1, "option « estompée » coupée : pleine")
      S.db.fade = true S:OnOptionChanged("fade")
      -- a game window opened over the strip (a NPC's dialog) : the strip fades like in combat, back when it closes
      local gf = rawget(_G, "GossipFrame") or CreateFrame("Frame", "GossipFrame", UIParent)
      gf:ClearAllPoints() gf:SetPoint("TOPLEFT", strip, "TOPLEFT", 10, 6) gf:SetSize(330, 420) gf:Show()
      ctx.fire("GOSSIP_SHOW") M.Advance(0.5, 0.1)
      check(math.abs(strip:GetAlpha() - 0.30) < 0.01, ("une fenêtre du jeu par-dessus la bande : elle s'efface (%.2f)"):format(strip:GetAlpha()))
      gf:Hide() ctx.fire("GOSSIP_CLOSED") M.Advance(0.5, 0.1)
      check(math.abs(strip:GetAlpha() - 0.60) < 0.01, ("la fenêtre fermée : estompée comme avant (%.2f)"):format(strip:GetAlpha()))
      -- off the strip, still working (key / slash)
      check(S.Do("suivant") ~= nil, "« Suivant » marche toujours (touche, /tai boutons suivant)")
      S.db.more = true S:OnOptionChanged("more")
      M.Advance(1.5, 0.1)
      btns = S.Buttons()
      check(btns.suivant and btns.suivant:IsShown() and btns.vite and btns.vite:IsShown(), "option « Boutons en plus » : Suivant et Vite reviennent")
      S.db.more = false S:OnOptionChanged("more")
      M.Advance(1.5, 0.1)
      -- a 5.0 player's saved position is kept (the strip only got thinner)
      S.char.strip = { "TOPLEFT", "TOPLEFT", 700, -300 }
      S.PlaceStrip()
      M.Advance(0.5, 0.1)
      local pr = geo.Rect(strip)
      local k = S.AutoScale()   -- (les décalages enregistrés sont dans l'échelle de la bande)
      check(pr and math.abs(pr.l - 700 * k) < 2 and math.abs((SH - pr.b - pr.h) - 300 * k) < 2, "place enregistrée en 5.0 : gardée")
      check(S.placed == "player", "place du joueur respectée")
      M.RunSlash("/tai interface reset")
      M.Advance(3, 0.1)
      check(S.char.strip == nil and strip:IsShown(), "/tai interface reset : la bande revient à sa place automatique")
      free(strip, "bande après reset")
    end
    -- toast : one, small, short, never stacking, never in the middle
    T:ShowAlert({ id = "a1", text = "Sacs presque pleins : 2 places", prio = 3 })
    T:ShowAlert({ id = "a2", text = "Quête finie : Chasse de test", prio = 3 })
    M.Advance(0.5, 0.1)
    local al = T:GetAlert()
    if check(al and al:IsShown(), "alerte à l'écran") then
      local r = free(al, "alerte")
      local l1, l2 = al.lines[1]:GetText(), al.lines[2]:IsShown() and al.lines[2]:GetText() or ""
      out(("alerte : « %s » %d×%d px en x %d, y %d ; %.0f s"):format(tostring(l1), r0(r.w * PX), r0(r.h * PX), r0(r.l * PX), r0((SH - r.b - r.h) * PX), al.left or 0))
      check(l2 == "" and l1 == "Quête finie : Chasse de test", "une seule ligne : la plus récente (jamais une pile)")
      check(r.h * PX <= 26 and r.w * PX <= 300, "alerte petite")
      check((al.left or 99) <= 8, ("alerte courte en mode discret (%.0f s)"):format(al.left or 0))
      check(SH - r.b - r.h < SH * 0.36 and SH - r.b - r.h > SH * 0.05, "alerte en haut, sous la flèche — jamais au milieu")
      check(Th:ToastQuiet() == true, "« discret » allumé d'office")
      T.settings.alert.quiet = false
      T:ShowAlert({ id = "a3", text = "Familier affamé", prio = 3 })
      M.Advance(0.3, 0.1)
      check((al.left or 0) >= 7.5, "« discret » coupé : 8 s et plus")
      T.settings.alert.quiet = nil
      al:Hide()
    end
  end

  -- ====================================================================================== window
  if PART == "window" then
    M.Hw(function() T:UserAction(function() W.Open() end) end)
    M.Advance(1.5, 0.1)
    local win = W.GetWindow()
    check(win and win:IsShown() and W.CurrentPage() == "home", "la fenêtre s'ouvre sur « Maintenant » (" .. tostring(W.CurrentPage()) .. ")")
    local wr = geo.Rect(win)
    local tr = geo.Rect(tracker)
    check(wr and tr and not overlap(wr, tr), "la fenêtre ne s'ouvre pas sur le suivi de quêtes du jeu")
    out(("fenêtre : %d×%d px en x %d, y %d"):format(r0(wr.w * PX), r0(wr.h * PX), r0(wr.l * PX), r0((SH - wr.b - wr.h) * PX)))
    local lines = W.HomeLines()
    check(#lines >= 1 and #lines <= 5, "« Maintenant » : 1 à 5 lignes (" .. #lines .. ")")
    out("MAINTENANT (page d'accueil) :")
    for _, l in ipairs(lines) do
      out("   " .. plain(l.text) .. (l.btn and ("  [" .. l.btn .. "]") or ""))
      check(l.btn and type(l.fn) == "function", "ligne « " .. plain(l.text) .. " » : un bouton qui agit")
    end
    local kinds = {}
    for _, r in ipairs(W.lastLay.rects) do kinds[r.kind] = (kinds[r.kind] or 0) + 1 end
    check((kinds.todo or 0) == #lines and not kinds.header and not kinds.action and not kinds.row, "la page d'accueil n'a QUE ces lignes")
    local nav, names = W.NavPages(), {}
    for _, p in ipairs(nav) do names[#names + 1] = p.label end
    out("navigation : " .. table.concat(names, " · "))
    check(#nav <= 9 and nav[1].id == "home", "9 catégories au plus, « Maintenant » en tête (" .. #nav .. ")")
    -- no duplicate : the footer button that repeats an action of the page's top is not shown twice
    check(W.DupOfTop("Me concentrer sur les métiers", { { "Me concentrer" } }) and W.DupOfTop("Pause", { { "Pause" } })
      and not W.DupOfTop("Pause", { { "Rendre mes quêtes" }, { "Tout faire ici" } }) and not W.DupOfTop("Mettre", { { "Me" } }),
      "le bouton du pied ne redit pas une action de la tête")
    -- every page : at most ONE primary action ; old pages still reachable
    local ids = {}
    for _, p in ipairs(W.PAGES) do ids[#ids + 1] = p.id end
    for _, id in ipairs(ids) do
      W.Select(id)
      M.Advance(1.1, 0.1)
      local prim, acts = 0, 0
      for i = 1, #M.frames do
        local f = M.frames[i]
        if rawget(f, "primary") ~= nil and f:IsVisible() and rawget(f, "titleText") then
          acts = acts + 1
          if f.primary then prim = prim + 1 end
        end
      end
      check(prim <= 1, "page « " .. id .. " » : au plus UNE action principale (" .. prim .. " sur " .. acts .. ")")
    end
    for _, id in ipairs({ "route", "dungeon", "coach", "journal", "keys", "addons", "display" }) do
      W.Open(id)
      M.Advance(1.1, 0.1)
      check(W.pageIndex[id] == nil or W.CurrentPage() == W.Resolve(id), "page « " .. id .. " » toujours joignable")
    end
    W.Open("quests") M.Advance(1.1, 0.1)
    local tabs = {}
    for _, t in ipairs(W.headTabList) do tabs[#tabs + 1] = t.label end
    check(W.lastLay.rects[1] and W.lastLay.rects[1].kind == "action", "les onglets sont dans l'en-tête : la page commence par ses actions")
    out("onglets de Quêtes : " .. table.concat(tabs, " | "))
    check(#tabs == 2 and tabs[1]:find("Quêtes", 1, true) and tabs[2]:find("Trajet", 1, true), "Quêtes | Trajet en onglets")
    check(W.RouteDeferred == true, "Suivi Forever là : la page Quêtes ne répète pas la liste du trajet")
    local found = false
    for _, r in ipairs(W.lastLay.rects) do if r.kind == "hero" and r.label:find("Trajet prévu", 1, true) then found = true end end
    check(not found, "pas de 2e « Trajet prévu » à côté de Suivi Forever")
    -- search still finds actions and settings
    local eb = rawget(_G, "TreeAIMainSearch")
    if eb then
      eb:SetText("rendre") M.Script(eb, "OnTextChanged") M.Advance(1.1, 0.1)
      local hits = W.SearchHits(W.CurrentPage())
      check(#hits >= 1, "la recherche trouve « rendre » (" .. #hits .. ")")
      eb:SetText("") M.Script(eb, "OnTextChanged")
    end
    -- closing and opening again : home again
    W.Close() M.Advance(0.5, 0.1)
    M.Hw(function() T:UserAction(function() W.Open() end) end)
    M.Advance(1.2, 0.1)
    check(W.CurrentPage() == "home", "rouverte : de nouveau « Maintenant »")
    W.Close() M.Advance(0.5, 0.1)
  end

  -- ====================================================================================== addons
  if PART == "addons" then
    check(A.Has("spf") and A.Link("spf", "arrow") and A.Link("spf", "dock"), "Shortest Path Forever trouvé, liens branchés d'office")
    local C = T.modules.Compass
    local cf = rawget(_G, "TreeAICompassFrame")
    check(C.db.shown == false and not (cf and cf:IsShown()), "Suivi Forever guide : pas de boussole TreeAI à l'écran")
    check(T:GuideMode() == "auto", "guidage : Suivi Forever (mode « auto »)")
    for _, n in ipairs({ "TreeAIWatchFrame", "TreeAITrackerFrame", "TreeAITodoFrame", "TreeAICard" }) do
      local f = rawget(_G, n)
      check(not (f and f:IsShown()), n .. " : pas de 2e liste à côté de Suivi Forever")
    end
    -- the small block against the column
    local blk = N.Block()
    if check(blk and blk:IsShown(), "bloc « Maintenant » à l'écran") then
      local r = free(blk, "bloc Maintenant")
      local c = geo.Rect(column)
      out(("bloc « Maintenant » : %d×%d px en x %d, y %d ; colonne Suivi Forever x %d–%d, y %d–%d"):format(r0(r.w * PX), r0(r.h * PX), r0(r.l * PX),
        r0((SH - r.b - r.h) * PX), r0(c.l * PX), r0((c.l + c.w) * PX), r0((SH - c.b - c.h) * PX), r0((SH - c.b) * PX)))
      check(math.abs(r.w - c.w) <= 2 and math.abs(r.l - c.l) <= 1, "même largeur et même bord que la colonne")
      local gapAbove, gapUnder = r.b - (c.b + c.h), c.b - (r.b + r.h)
      check((gapAbove >= 0 and gapAbove <= 12) or (gapUnder >= 0 and gapUnder <= 12), "posé contre la colonne (dessus ou dessous)")
      for _, p in ipairs(blk.__points) do check(p[2] == UIP, "le bloc est ancré à UIParent, jamais au cadre de Suivi Forever") end
      local shown = 0
      for _, row in ipairs(N.BlockRows()) do if row:IsShown() then shown = shown + 1 end end
      check(shown >= 1 and shown <= 3, "1 à 3 lignes dans le bloc (" .. shown .. ")")
      for _, l in ipairs(N.Dump(3)) do out("   " .. l) end
    end
    -- « Y aller » : the destination goes to SPF's public API, on the player's click only
    check(#navigated == 0, "rien n'est envoyé à Suivi Forever sans clic")
    M.mapRects[1454] = M.mapRects[1454] or { top = 2000, left = 2000, w = 1500, h = 1000, continent = 1 }
    local how
    M.Hw(function() T:UserAction(function() how = A.GoTo({ mapID = 1454, x = 0.60, y = 0.40, title = "Maître de test" }) end) end)
    check(how == "spf" and #navigated == 1 and navigated[1].owner == "TreeAI" and navigated[1].mapID == 1454,
      "« Y aller » : destination confiée à Suivi Forever (" .. tostring(how) .. ", " .. #navigated .. " appel)")
    -- any « go there » of any module (they all use the TreeAI compass) is handed over too, inside a click only
    local n0 = #navigated
    T.Compass.SetTarget({ mapID = 1454, x = 0.30, y = 0.30, label = "Hors clic" })
    check(#navigated == n0, "hors d'un clic du joueur : rien n'est confié")
    M.Hw(function() T:UserAction(function() T.Compass.SetTarget({ mapID = 1454, x = 0.70, y = 0.20, label = "Réparateur de test" }) end) end)
    check(#navigated == n0 + 1 and navigated[#navigated].title == "Réparateur de test", "un « va là » d'un autre module, au clic : confié aussi")
    -- without a Navigate entry point : the game's own waypoint
    local nav = SPF.API.Navigate
    SPF.API.Navigate = nil
    M.userWaypoint = nil
    M.Hw(function() T:UserAction(function() how = A.GoTo({ mapID = 1454, x = 0.55, y = 0.45, title = "Banque de test" }) end) end)
    out("sans API Navigate : « Y aller » = " .. tostring(how) .. (M.userWaypoint and " (repère du jeu posé)" or ""))
    check(how == "blizzard" and M.userWaypoint ~= nil, "sans API de navigation : le repère du jeu est posé")
    SPF.API.Navigate = nav
    -- every link switches off, and gives back what it took
    A.SetLink("spf", "arrow", false)
    M.Advance(1.5, 0.1)
    check(C.db.shown ~= false and T:GuideMode() == "treeai", "lien « flèche » coupé : la boussole TreeAI et son guidage reviennent")
    A.SetLink("spf", "dock", false)
    M.Advance(1.5, 0.1)
    check(not (blk and blk:IsShown()), "lien « bloc » coupé : plus de bloc à l'écran")
    A.SetLink("spf", "arrow", true) A.SetLink("spf", "dock", true)
    M.Advance(1.5, 0.1)
    check(C.db.shown == false and blk and blk:IsShown(), "liens rebranchés : tout revient")
    -- no space against the column → no block (it never lands on a frame)
    local above = occupy("cadre au-dessus de la colonne", nil, 0, 115, 260, 396)
    T:ObstaclesChanged()
    N.Changed() M.Advance(1.5, 0.1)
    check(not blk:IsShown(), "pas de place contre la colonne : pas de bloc (tout reste dans la fenêtre)")
    above.__shown = false
    T:ObstaclesChanged()
    N.Changed() M.Advance(1.5, 0.1)
    check(blk:IsShown(), "la place revient : le bloc aussi")
    -- Leatrix Plus already sells greys / repairs → said once, never offered twice
    check(A.MerchantAuto() == nil, "sans Leatrix : rien à dire")
    rawset(_G, "LeaPlusDB", { AutoSellJunk = "On", AutoRepairGear = "On" })
    A.Installed(true)
    local auto = A.MerchantAuto()
    check(type(auto) == "table" and auto.sell and auto.repair, "Leatrix Plus vend les gris et répare : lu dans ses réglages")
    out("marchand : " .. tostring(auto and auto.note))
    local B = T.Bags
    if B and B.CardModel and B._active then
      M.inventory[18] = { id = 2504, count = 1, dur = 10, durMax = 60 }
      M.merchantRepair = true
      M.units.npc = { name = "Marchand-Test" }
      M.SetMerchant(true) M.Advance(2, 0.1)
      local okM, m = pcall(B.CardModel)
      if okM and type(m) == "table" then
        out("carte du marchand avec Leatrix : " .. tostring(m.label) .. " | " .. tostring(m.info))
        check(not tostring(m.label or ""):find("réparer", 1, true) and (m.repair or 0) == 0, "carte du marchand : pas de « réparer » en double")
      end
      M.SetMerchant(false) M.Advance(1, 0.1)
    end
    A.SetLink("leatrix", "merchant", false)
    check(A.MerchantAuto() == nil, "lien Leatrix coupé : TreeAI repropose tout")
    A.SetLink("leatrix", "merchant", true)
    -- nothing was written in the other addons ; none of their frames was touched
    check(snapshot(SPF) + snapshot(rawget(_G, "ShortestPathForeverDB")) == spfSig, "rien n'a été écrit dans les tables de Shortest Path Forever")
    check(#column.__points == colPoints and column.__parent == UIP and column.__shown == true, "la colonne Suivi Forever n'a pas été touchée")
    check(LeaPlusDB.AutoSellJunk == "On" and LeaPlusDB.AutoRepairGear == "On", "réglages de Leatrix Plus intacts")
    -- the page and the probe
    local secs = A.Sections()
    out("AUTRES ADDONS :")
    for _, s in ipairs(secs) do
      out("  " .. s.title)
      for _, l in ipairs(s.lines) do out("     " .. (l.label and ((l.kind == "take" and "prend : " or "laisse : ") .. l.label .. " — ") or "") .. l.text) end
    end
    check(#secs >= 2, "page « Autres addons » : chaque addon trouvé (" .. #secs .. ")")
    W.Open("addons") M.Advance(1.2, 0.1)
    local toggles = 0
    for _, r in ipairs(W.lastLay.rects) do if r.kind == "toggle" then toggles = toggles + 1 end end
    check(W.CurrentPage() == "addons" and toggles >= 3, "chaque lien a sa case (" .. toggles .. ")")
    W.Close()
    local probe = A.Probe()
    out("SONDE :")
    for _, l in ipairs(probe) do out("  " .. l) end
    local all = table.concat(probe, "\n")
    check(all:find("Shortest Path Forever : ", 1, true) and all:find("ShortestPathForever.API.Navigate=function", 1, true), "sonde : points d'entrée de SPF")
    check(all:find("Leatrix Plus : ", 1, true) and all:find("Auctionator : absent", 1, true), "sonde : présents et absents")
    local Bg = T.modules.Bugs
    if Bg and Bg.ProbeAPI then
      local lines = Bg.ProbeAPI()
      local seen = false
      for _, l in ipairs(lines) do if l == "-- Autres addons (détection TreeAI)" then seen = true end end
      check(seen, "la sonde part dans le rapport d'erreurs")
    end
  end

  -- ====================================================================================== connect
  if PART == "connect" then
    local n0 = #navigated
    local function clickable(e) local fn, hint = W.DefaultAction(e) return type(fn) == "function" and hint or nil end
    check(clickable({ text = "Répare au prochain marchand : équipement à 12 %" }), "« Répare… » → le réparateur")
    check(clickable({ text = "Refais le plein chez un marchand : plus de repas" }), "« Refais le plein… » → le marchand")
    check(clickable({ text = "24 Teinture grise — 84 pa", service = "vendor" }), "composant vendu → le marchand")
    check(clickable({ text = "30 Cuir lourd — à farmer", itemID = 4234 }), "composant manquant → l'HV")
    check(clickable({ text = "Vérifie tes talents dans la fenêtre du jeu (touche N)" }), "talents → la fenêtre de talents")
    check(clickable({ text = "Ton repos double l'XP des monstres" }) == nil, "rien d'évident : pas de faux clic")
    check(clickable({ text = "x", onClick = function() end }) == nil, "une ligne qui a déjà son clic : inchangée")
    -- every line of « Maintenant » acts
    for _, l in ipairs(N.Model(5)) do
      out("maintenant : " .. plain(l.text) .. "  [" .. tostring(l.btn) .. "]")
      check(l.btn and type(l.fn) == "function", "« " .. plain(l.text) .. " » : un clic")
    end
    -- the « next step » of the Quêtes page is never a dead end
    local it = W.NextItem and W.NextItem()
    if type(it) == "table" then check(type(it.onClick) == "function", "« Prochaine étape » : un clic (y aller)") end
    check(#navigated == n0, "rien n'est parti chez Suivi Forever sans clic")
  end

  for i = chat0 + 1, #M.output do
    if not tostring(M.output[i]):find("^%[UIError%]") then out("chat: " .. M.output[i]) check(false, "rien dans le chat") end
  end
  check(#(M.blocked or {}) == b0, "aucune action bloquée")
  check(#M.violations == v0, "aucune violation")
end
