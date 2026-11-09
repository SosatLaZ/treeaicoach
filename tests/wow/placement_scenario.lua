-- Harness scenario (PLACEBUG, 4.0.1): « quand je lance le jeu, tout est bien au bon endroit, mais une seconde après
-- tout se remet dans un coin » (real client, 4.0.0). Runs on the real-client geometry of tests/wow/geometry.lua
-- (harness --geometry): UIParent unsized and every rect nil until the first frame after PLAYER_ENTERING_WORLD, the UI
-- scale applied after login (UI_SCALE_CHANGED / DISPLAY_SIZE_CHANGED — early = during the loading screen), Blizzard
-- frames re-anchored by « Edit Mode » ~0.8 s in, StartMoving / StopMovingOrSizing re-anchoring like the client.
-- Mode = env TREEAI_PLACEBUG_MODE (tests/wow/test_placement.py chains the sessions with --dump / --savedvars):
--   fresh   first login: the first automatic placement waits for a valid, stable screen, nothing in a corner, the
--           « Prochaine étape » card appears once at its place; then the player drags the card (header), the
--           « Raccourcis » bar (handle), the compass, the main window, and moves the alert box in edit mode;
--           the expected anchors are kept in the SavedVariables for the next session.
--   relog   next login (same race): every frame keeps EXACTLY its saved anchor at +2 s and +12 s, nothing re-anchored;
--           a 2nd PLAYER_ENTERING_WORLD (zoning) + UI_SCALE_CHANGED moves nothing; then UI scale change and resolution
--           changes: the player's frames keep their anchors, every frame stays on screen, none in a corner.
--   reload  /reload (screen already laid out): same as relog without the race.
--   olddata 4.0.0 SavedVariables with broken records (anchor saved off screen, x / y missing): repaired, nothing
--           in a corner or off screen, valid records untouched.
-- lua5.1 tests/wow/harness.lua -q --no-storm --geometry events=early,scaleAt=2,rectsAt=3 --profile tout \
--   --flavor forever --class HUNTER --scenario tests/wow/placement_scenario.lua wow_addon/TreeAI
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "placement_scenario needs --geometry") then return end
  local mode = (os.getenv("TREEAI_PLACEBUG_MODE") or "fresh"):lower()
  local UIP = UIParent
  -- (4.1 STRIP) the « Prochaine étape » card is off by default now (the « boutons rapides » strip took its place, see
  -- strip_scenario.lua); this player keeps it on, so its placement is still checked through every session
  local Wn = T.Window
  if Wn and Wn.db and Wn.db.card == false then Wn.db.card = true Wn:OnOptionChanged("card") end

  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end
  local function name(f) return f and f.GetName and f:GetName() or "?" end
  -- the anchor of a frame: « POINT REL RELPOINT x y » (UIParent-relative, one point), or nil
  local function anchorOf(f)
    if f:GetNumPoints() ~= 1 then return nil end
    local p, rel, rp, x, y = f:GetPoint(1)
    if not p then return nil end
    return ("%s %s %s %d %d"):format(p, name(rel), tostring(rp), r0(x), r0(y))
  end
  local SKIP = { TreeAICoreFrame = true, TreeAIWizard = true, TreeAITourCallout = true, TreeAITourGlow = true,
    TreeAITourArrow = true, TreeAIEditBar = true }
  local function visible()
    local out = {}
    for _, f in ipairs(geo.Ours()) do
      if not SKIP[name(f)] and f:IsVisible() and geo.Rect(f) then out[#out + 1] = f end
    end
    return out
  end
  -- every visible TreeAI frame: on screen, never in a corner / at the origin (the 4.0.0 symptom)
  local function sane(tag)
    local n = 0
    for _, f in ipairs(visible()) do
      n = n + 1
      local r = geo.Rect(f)
      check(geo.OnScreen(f), ("%s : %s sur l'écran (%d,%d %dx%d)"):format(tag, name(f), r0(r.l), r0(r.b), r0(r.w), r0(r.h)))
      check(not geo.Corner(f), ("%s : %s pas dans un coin (%s : %d,%d %dx%d)"):format(tag, name(f),
        tostring(geo.Corner(f)), r0(r.l), r0(r.b), r0(r.w), r0(r.h)))
      check(not (r.l < 3 and r.b < 3), ("%s : %s pas à l'origine"):format(tag, name(f)))
    end
    return n
  end
  local function anchors()
    local t = {}
    for _, f in ipairs(geo.Ours()) do
      if not SKIP[name(f)] then t[name(f)] = anchorOf(f) end
    end
    return t
  end
  local function sameAnchors(want, tag, only)
    local got = anchors()
    for n, a in pairs(want) do
      if not only or only[n] then
        local f = rawget(_G, n)
        if f and f:IsShown() then check(got[n] == a, ("%s : %s garde sa place (%s → %s)"):format(tag, n, tostring(a), tostring(got[n]))) end
      end
    end
  end
  -- no re-anchoring of these frames in the log between `since` and `upto` (geo time)
  local function noMovesSince(since, names, tag, upto)
    for _, e in ipairs(geo.Log) do
      if e.t > since and e.t < (upto or math.huge) and names[e.frame]
          and (e.what == "SetPoint" or e.what == "ClearAllPoints" or e.what == "StopMovingOrSizing") then
        check(false, ("%s : %s replacé à t=%.1f s (%s %s)"):format(tag, e.frame, e.t, e.what, tostring(e.args[1])))
      end
    end
  end
  local function db() return TreeAIDB.__placebug end

  ------------------------------------------------------------------------------------------------- fresh
  if mode == "fresh" then
    local st = TreeAIDB.chars[T:CharKey()].layout
    -- login + 2 s: the screen is laid out but not stable / settled yet → no automatic placement so far
    check(not (st and st.sig), "connexion + 2 s : aucun placement automatique sur un écran pas encore posé")
    sane("connexion + 2 s")
    M.Advance(3, 0.02)   -- (4.4 SMART) the first placement is sliced over frames: real frames (50 / s), not 10 / s
    st = TreeAIDB.chars[T:CharKey()].layout
    check(st and st.sig and st.sig:match("^1920x1080"), "premier placement sur l'écran final (" .. tostring(st and st.sig) .. ")")
    check(st and st.v == T.LAYOUT_VERSION, "version du placement notée")
    -- the frames created at login are not re-anchored during the race (4.0.0: at +1 s on the transient screen)
    noMovesSince(0.05, { TreeAICompassFrame = true, TreeAIPanel = true, TreeAIRadarFrame = true, TreeAIAlert = true },
      "pendant la connexion", 2.9)
    local card = T.Window.GetCard()
    check(card and card:IsShown(), "carte « Prochaine étape » affichée")
    local first = anchors()
    local shownAt = geo.t
    sane("premier placement")
    M.Advance(12, 0.1)
    sane("+15 s")
    -- nothing moves again once placed (the card appears ONCE at its place)
    noMovesSince(shownAt, { TreeAICard = true, TreeAICompassFrame = true, TreeAIShortcuts = true, TreeAIAlert = true,
      TreeAIRadarFrame = true, TreeAIPanel = true }, "placé une fois")
    sameAnchors(first, "placé une fois")

    -- the player drags with the mouse (the real handles), then moves the alert box in edit mode
    local SW, SH = geo.Screen()
    local cr = geo.Rect(card)
    local mv = geo.Drag(card.hero, (SW - 360) - cr.l, 0)
    check(mv == card, "carte glissée par son en-tête")
    local dock = rawget(_G, "TreeAIShortcuts")
    local dockOn = dock and dock:IsShown() and geo.Rect(dock)
    if dockOn then
      local h = select(1, dock:GetChildren())
      for _, c in ipairs({ dock:GetChildren() }) do if c.__scripts and c.__scripts.OnDragStart then h = c break end end
      check(geo.Drag(h, -300, 120) == dock, "barre « Raccourcis » glissée par sa poignée")
    end
    local cf = rawget(_G, "TreeAICompassFrame")
    if cf and cf:IsShown() then check(geo.Drag(cf, -420, -150) == cf, "boussole glissée") end
    local W = T.Window
    W.Open()
    local win = W.GetWindow and W.GetWindow() or rawget(_G, "TreeAIMainWindow")
    if win and win.nav then geo.Drag(win.nav, 200, -60) end
    -- every drag = an explicit UIParent anchor (same point on both sides), never the client's own anchor
    for _, f in ipairs({ card, dockOn and dock or nil, cf, win }) do
      if f then
        local p, rel, rp = f:GetPoint(1)
        check(rel == UIP and p == rp, ("%s : ancrage explicite sur UIParent après le glisser (%s %s %s)"):format(name(f), tostring(p), name(rel), tostring(rp)))
      end
    end
    check(T:IsPlayerPlaced("card"), "carte glissée = posée par le joueur")
    if dockOn then check(T:IsPlayerPlaced("shortcuts"), "barre glissée = posée par le joueur") end
    W.Close()
    -- edit mode: the alert box
    T:SetEditMode(true)
    local alert = T:GetAlert()
    local rec = T:CommitMove("alert", 600, 700)
    check(rec and rec.point, "encadré d'alerte déplacé en mode édition")
    T:SetEditMode(false)
    -- the live placement never pushes a frame the player placed, even over a growing tracker
    local ot = rawget(_G, "ObjectiveTrackerFrame")
    ot.__h = 900
    local before = anchors()
    M.Advance(12, 0.5)
    sameAnchors(before, "suivi agrandi", { TreeAICard = true, TreeAIShortcuts = true, TreeAICompassFrame = true })
    ot.__h = 520
    sane("après les glisser")
    -- expectations for the next session
    local want = anchors()
    want.TreeAIAlert = nil   -- (hidden between alerts; its saved place is checked through settings.alert.pos)
    want.TreeAIStrip = nil   -- (4.1: placed by its own rules once the game's frames are measurable — strip_scenario.lua)
    -- (4.2 ROUTE42) the Suivi column, never moved by the player, hangs under the game's tracker when there is room (checked
    -- by route42_scenario.lua / replace_scenario.lua); a place the player chose is kept (TreeAI.Watch.char.pos.user)
    local Wc = T.Watch and T.Watch.char
    if not (Wc and type(Wc.pos) == "table" and Wc.pos.user) then want.TreeAIWatchFrame = nil end
    TreeAIDB.__placebug = { anchors = want, alert = rec and { rec.point, rec.x, rec.y },
      window = T.settings.windows and T.settings.windows.main and { T.settings.windows.main.point, r0(T.settings.windows.main.x), r0(T.settings.windows.main.y) } }
    check(alert ~= nil, "encadré d'alerte créé")
    return
  end

  ------------------------------------------------------------------------------------------------- relog / reload
  if mode == "relog" or mode == "reload" then
    local exp = db()
    if not check(exp and exp.anchors, "attentes de la session précédente présentes") then return end
    local names = {}
    for n in pairs(exp.anchors) do names[n] = true end
    -- +2 s: exactly the saved anchors (the card was placed at 1.5 s from its saved place)
    sameAnchors(exp.anchors, "connexion + 2 s")
    sane("connexion + 2 s")
    local t2 = geo.t
    M.Advance(12, 0.1)
    sameAnchors(exp.anchors, "connexion + 14 s")
    noMovesSince(t2, names, "connexion")
    sane("connexion + 14 s")
    local al = T.settings.alert and T.settings.alert.pos
    check(al and exp.alert and al.point == exp.alert[1] and r0(al.x) == r0(exp.alert[2]) and r0(al.y) == r0(exp.alert[3]),
      "encadré d'alerte : place du mode édition gardée")
    local ws = T.settings.windows and T.settings.windows.main
    if exp.window then
      check(ws and ws.point == exp.window[1] and r0(ws.x) == exp.window[2] and r0(ws.y) == exp.window[3], "fenêtre TreeAI : place gardée")
    end
    -- zoning (2nd PLAYER_ENTERING_WORLD) + UI_SCALE_CHANGED with the same screen: nothing moves
    local t3 = geo.t
    ctx.fire("PLAYER_ENTERING_WORLD", false, false)
    M.FireEvent("UI_SCALE_CHANGED")
    M.FireEvent("DISPLAY_SIZE_CHANGED")
    M.Advance(8, 0.1)
    sameAnchors(exp.anchors, "2e PLAYER_ENTERING_WORLD")
    noMovesSince(t3, names, "2e PLAYER_ENTERING_WORLD")
    -- UI scale slider (same monitor): player frames keep their anchors, auto frames re-placed once, all on screen
    local placed = {}
    for n in pairs(exp.anchors) do if n == "TreeAICard" or n == "TreeAIShortcuts" or n == "TreeAICompassFrame" then placed[n] = true end end
    geo.SetUIScale(0.9)
    M.Advance(8, 0.1)
    sameAnchors(exp.anchors, "échelle d'interface changée", placed)
    sane("échelle d'interface 0,9")
    local st = TreeAIDB.chars[T:CharKey()].layout
    local SW, SH = geo.Screen()
    check(st.sig and st.sig:match("^" .. r0(SW) .. "x" .. r0(SH)), "échelle changée : nouvelle signature (" .. tostring(st.sig) .. ")")
    -- resolution changes
    for _, res in ipairs({ { 2560, 1440 }, { 1366, 768 }, { 1920, 1080 } }) do
      geo.SetScreen(res[1], res[2])
      M.Advance(8, 0.1)
      sameAnchors(exp.anchors, ("résolution %dx%d"):format(res[1], res[2]), placed)
      sane(("résolution %dx%d"):format(res[1], res[2]))
    end
    return
  end

  ------------------------------------------------------------------------------------------------- olddata
  if mode == "olddata" then
    M.Advance(12, 0.1)
    local n = sane("anciennes données")
    check(n >= 2, "des cadres TreeAI sont affichés (" .. n .. ")")
    local W = T.Window
    local pos = W.char and W.char.cardPos
    check(pos == nil or (pos.y or 0) <= 0, "carte : place hors écran (ancrage relu de travers) oubliée")
    local mv = TreeAIDB.chars[T:CharKey()].movers or {}
    check(mv.horizon == nil, "mode édition : place sans x / y oubliée")
    local C = T.modules.Compass
    check(C and C.char and C.char.point == "TOP" and r0(C.char.y) < 0, "boussole : place hors écran remise ("
      .. tostring(C and C.char and C.char.point) .. " " .. tostring(C and C.char and C.char.y) .. ")")
    local S = T.modules.Shortcuts
    check(S and S.char and type(S.char.pos) == "table" and S.char.pos[1] == "BOTTOMLEFT" and S.char.pos[3] == 700,
      "barre « Raccourcis » : place valide gardée telle quelle")
    local al = T.settings.alert
    check(not (al.pos and al.pos.x == nil), "encadré : place sans x / y ignorée")
    return
  end
  check(false, "mode inconnu " .. mode)
end
