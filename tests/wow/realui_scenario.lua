-- Harness scenario (5.0 INTEG): THE PLAYER'S REAL SCREEN. Reproduces, rectangle by rectangle, the interface of the
-- player who judges TreeAI in the real game (capture 2000×1125, TreeAI off, a crowded capital): damage / threat meters
-- top left, the « Suivi Forever » column of Shortest Path Forever on the left (Nettoyer mon journal, Trajet, Composants
-- de métier), chat bottom left, a custom XP bar at the top centre with the other addon's waypoint arrow and text under
-- it, minimap top right, the game's quest tracker under it (it grows down), custom unit frames and action bars at the
-- bottom centre, micro / bag bar bottom right, buffs. Known addons have a named frame; meters, unit frames, bars and
-- the arrow are ANONYMOUS frames (no global name: only a generic scan of visible top-level frames can see them).
--   REALUI_SCALE = 0.64 | 0.75 (UI scale), REALUI_SPF = named | anon (the column has a known name or not).
-- Proves: the strip, the compass, toasts, cards and every other TreeAI frame on screen land in free space — ZERO overlap
-- with any occupied rectangle — at both scales; nothing in the middle of the screen; the generic scan runs on placement
-- only (never from an OnUpdate); with Shortest Path Forever present TreeAI does not show a second route list nor a second
-- reagent list. Dumps « ce que le joueur voit ». Fake names only.
return function(M, ctx)
  local T = TreeAI
  local check = ctx.check
  local geo = M.geo
  if not check(geo ~= nil, "realui_scenario needs --geometry w=2000,h=1125") then return end
  local UIP = UIParent
  local SCALE = tonumber(os.getenv("REALUI_SCALE") or "") or 0.64
  local SPF = os.getenv("REALUI_SPF") or "named"
  local function out(s) io.write("REALUI ", s, "\n") end
  local function r0(v) return math.floor((tonumber(v) or 0) + 0.5) end
  local chat0, b0, v0 = #M.output, #(M.blocked or {}), #M.violations
  rawset(_G, "GetPhysicalScreenSize", function() return geo.opts.w, geo.opts.h end)
  geo.SetUIScale(SCALE)
  local PX = SCALE * geo.opts.h / 768                     -- real pixels per UI unit
  local SW, SH = geo.Screen()

  -- ------------------------------------------------------------------ the occupied rectangles (pixels, origin top left)
  local OCC = {}
  local function occupy(label, name, x0, y0, x1, y1, parent)
    local f = name and rawget(_G, name) or nil
    if type(f) ~= "table" then f = CreateFrame("Frame", name, parent or UIP) end
    f.__parent = parent or UIP
    f.__w, f.__h, f.__shown = (x1 - x0) / PX, (y1 - y0) / PX, true
    f.__points = { { "TOPLEFT", UIP, "TOPLEFT", x0 / PX, -y0 / PX } }
    OCC[#OCC + 1] = { label = label, f = f, l = x0 / PX, b = SH - y1 / PX, w = (x1 - x0) / PX, h = (y1 - y0) / PX }
    return f
  end
  -- the default unit frames are replaced by an addon's: hidden
  for _, n in ipairs({ "PlayerFrame", "TargetFrame", "PetFrame", "PartyMemberFrame1", "PartyMemberFrame2", "PartyMemberFrame3",
    "PartyMemberFrame4", "MainMenuBar", "MultiBarBottomLeft", "MultiBarRight", "CastingBarFrame" }) do
    local f = rawget(_G, n)
    if type(f) == "table" then f.__shown = false f.__points = {} end
  end
  occupy("compteur de dégâts", nil, 0, 0, 262, 110)
  occupy("compteur de menace", nil, 262, 0, 525, 110)
  local spfName = SPF == "named" and "ShortestPathForeverTrackerFrame" or nil
  local spf = occupy("colonne Suivi Forever", spfName, 0, 400, 250, 930)
  rawset(_G, "ShortestPathForever", rawget(_G, "ShortestPathForever") or { API = {} })   -- the addon is loaded
  occupy("discussion", "ChatFrame1", 0, 900, 490, 1125)
  occupy("barre d'XP", nil, 810, 18, 1190, 36)
  occupy("flèche et texte du repère", nil, 880, 40, 1120, 245)
  occupy("minimap", "MinimapCluster", 1780, 30, 2000, 250)
  local mm = rawget(_G, "Minimap")
  if type(mm) == "table" then mm.__parent = MinimapCluster mm.__w, mm.__h = 200 / PX, 200 / PX mm.__points = { { "CENTER", MinimapCluster, "CENTER", 0, 0 } } end
  occupy("suivi de quêtes du jeu", "ObjectiveTrackerFrame", 1745, 250, 2000, 570)
  occupy("cadres d'unité", nil, 615, 738, 840, 915)
  occupy("barres d'action", nil, 740, 840, 1265, 1125)
  occupy("micro-menu et sacs", nil, 1275, 1080, 1850, 1125)
  occupy("améliorations", "BuffFrame", 1590, 0, 1730, 40)
  -- the three free areas of the capture (for the dump)
  local FREE = { { "gauche", 0, 115, 250, 395 }, { "droite", 1745, 590, 2000, 1070 }, { "haut gauche", 530, 0, 800, 110 },
    { "haut droite", 1200, 0, 1580, 110 } }

  -- ------------------------------------------------------------------ world: level-30 hunter, group of 4, in a capital
  M.LeaveCombat()
  M.player.level = 30
  M.player.zone, M.player.subzone = "Orgrimmar", "La Vallée de l'Honneur"
  M.quests = {
    { header = "Désolace" },
    { id = 801, title = "Chasse de test", level = 39, complete = true, objectives = {} },
    { id = 802, title = "Collection de test", level = 39, objectives = { { text = "Os de test : 0/10", done = false, have = 0, need = 10 } } },
    { id = 803, title = "En selle de test", level = 40, objectives = { { text = "Trouver un éclaireur de test", done = false } } },
  }
  M.group = {
    { name = "Amie", class = "PRIEST", level = 28, online = true },
    { name = "Compagnon", class = "WARRIOR", level = 29, online = true },
    { name = "Camarade", class = "MAGE", level = 30, online = true },
  }
  M.skills = { { name = "Travail du cuir", rank = 116, max = 150 }, { name = "Dépeçage", rank = 140, max = 150 },
    { name = "Cuisine", rank = 94, max = 150 }, { name = "Secourisme", rank = 125, max = 150 } }
  ctx.fire("GROUP_ROSTER_UPDATE")
  ctx.fire("QUEST_LOG_UPDATE")
  ctx.fire("SKILL_LINES_CHANGED")
  if T.Tour and T.Tour.Store then T.Tour.Store().done = 999 end
  -- count the generic scans, and catch any scan started from a per-frame script
  local scans, scanInUpdate = 0, 0
  local realEnum = rawget(_G, "EnumerateFrames")
  rawset(_G, "EnumerateFrames", function(f)
    if f == nil then scans = scans + 1 if M.inOnUpdate then scanInUpdate = scanInUpdate + 1 end end
    return realEnum(f)
  end)
  local realKids = UIP.GetChildren
  local kidsCalls = 0
  UIP.GetChildren = function(self, ...) kidsCalls = kidsCalls + 1 return realKids(self, ...) end
  M.Advance(30, 0.1)
  if T.GetWizard and T:GetWizard() then T:GetWizard():Hide() end
  -- a toast, and a target for the compass
  local C = T.modules.Compass
  if C and C.SetTarget then pcall(C.SetTarget, { mapID = 1454, x = 0.60, y = 0.40, title = "Maître de test" }) end
  T:ShowAlert({ id = "realui", text = "Sacs presque pleins : 2 places", prio = 3 })
  M.Advance(4, 0.1)

  -- ------------------------------------------------------------------ what is on screen, and where
  local function overlap(a, b) return a.l < b.l + b.w - 0.5 and b.l < a.l + a.w - 0.5 and a.b < b.b + b.h - 0.5 and b.b < a.b + a.h - 0.5 end
  local function rectPx(r) return ("x %d–%d, y %d–%d px"):format(r0(r.l * PX), r0((r.l + r.w) * PX), r0((SH - r.b - r.h) * PX), r0((SH - r.b) * PX)) end
  local function zoneOf(r)
    for _, z in ipairs(FREE) do
      local zr = { l = z[2] / PX, b = SH - z[5] / PX, w = (z[4] - z[2]) / PX, h = (z[5] - z[3]) / PX }
      if r.l >= zr.l - 2 and r.b >= zr.b - 2 and r.l + r.w <= zr.l + zr.w + 2 and r.b + r.h <= zr.b + zr.h + 2 then return z[1] end
    end
    return nil
  end
  out(("écran 2000×1125, échelle %.2f : %d×%d unités, %.2f px par unité ; colonne Suivi Forever %s"):format(SCALE, r0(SW), r0(SH), PX,
    SPF == "named" and "nommée" or "sans nom"))
  local ours = {}
  for _, f in ipairs(geo.Ours()) do
    if f:IsShown() then
      local r = geo.Rect(f)
      if r and r.w > 4 and r.h > 4 then ours[#ours + 1] = { f = f, r = r, name = f:GetName() or "?" } end
    end
  end
  table.sort(ours, function(a, b) return a.name < b.name end)
  -- frames that are ON a game frame by design (a button on the minimap) or opened by the player (the window)
  local SKIP = { TreeAIMinimapButton = "minimap", TreeAIMainWindow = "fenêtre" }
  local seenStrip, seenCompass, seenAlert = false, false, false
  for _, o in ipairs(ours) do
    local where = zoneOf(o.r)
    out(("  %s : %s (%d×%d px)%s"):format(o.name, rectPx(o.r), r0(o.r.w * PX), r0(o.r.h * PX), where and (" — zone libre « " .. where .. " »") or ""))
    if o.name == "TreeAIStrip" then seenStrip = true end
    if o.name == "TreeAICompassFrame" then seenCompass = true end
    if o.name == "TreeAIAlert" then seenAlert = true end
    if not SKIP[o.name] then
      check(geo.OnScreen(o.f, 2), o.name .. " entièrement à l'écran")
      for _, oc in ipairs(OCC) do
        check(not overlap(o.r, oc), ("%s (%s) ne touche pas « %s » (%s)"):format(o.name, rectPx(o.r), oc.label, rectPx(oc)))
      end
      check(not overlap(o.r, { l = SW * 0.38, b = SH * 0.34, w = SW * 0.24, h = SH * 0.30 }), o.name .. " : jamais au milieu de l'écran")
      -- our own frames do not sit on each other either
      for _, p in ipairs(ours) do
        if p ~= o and not SKIP[p.name] and p.name > o.name then
          check(not overlap(o.r, p.r), ("%s et %s séparés"):format(o.name, p.name))
        end
      end
    end
  end
  check(seenStrip, "la bande d'actions est à l'écran")
  -- (5.1 LOOK) Shortest Path Forever already draws the route and its arrow: by default TreeAI shows NO second arrow
  -- (the link « spf / arrow » of the page « Autres addons ») ; the player who cuts that link gets the compass back —
  -- checked at the end of this scenario, placed in free space like everything else.
  local AD = T.Addons
  local deferArrow = AD and AD.Link and AD.Link("spf", "arrow") and true or false
  if deferArrow then
    check(not seenCompass, "Suivi Forever guide déjà : pas de 2e flèche TreeAI à l'écran")
  else
    check(seenCompass or not (C and C._active), "la boussole est à l'écran")
  end
  check(seenAlert, "l'alerte est à l'écran")
  -- the generic scan: on placement only
  out(("  scans génériques : %d (EnumerateFrames) + %d (enfants d'UIParent) en 34 s de jeu, dont %d depuis un OnUpdate"):format(scans, kidsCalls, scanInUpdate))
  check(scanInUpdate == 0, "aucun scan d'écran depuis un OnUpdate")
  local s1, k1 = scans, kidsCalls
  M.Advance(30, 0.1)
  check(scans == s1 and kidsCalls == k1, ("au repos, plus aucun scan d'écran (%d + %d en 30 s)"):format(scans - s1, kidsCalls - k1))

  -- ------------------------------------------------------------------ (5.1 LOOK) the link is cut: TreeAI's arrow is back
  -- … in free space, with the same zero-overlap rule as every other frame ; link restored: no second arrow again
  if deferArrow then
    AD.SetLink("spf", "arrow", false)
    M.Advance(8, 0.1)
    local cf = rawget(_G, "TreeAICompassFrame")
    local back = cf and cf:IsShown() and geo.OnScreen(cf, 2)
    check(back or not (C and C._active), "lien « Suivi Forever » coupé : la boussole TreeAI revient, à l'écran")
    if back then
      local r = geo.Rect(cf)
      out(("  TreeAICompassFrame : %s (%d×%d px) — lien « spf / arrow » coupé"):format(rectPx(r), r0(r.w * PX), r0(r.h * PX)))
      for _, oc in ipairs(OCC) do
        check(not overlap(r, oc), ("TreeAICompassFrame (%s) ne touche pas « %s » (%s)"):format(rectPx(r), oc.label, rectPx(oc)))
      end
      check(not overlap(r, { l = SW * 0.38, b = SH * 0.34, w = SW * 0.24, h = SH * 0.30 }), "TreeAICompassFrame : jamais au milieu de l'écran")
      for _, n in ipairs({ "TreeAIStrip", "TreeAINow" }) do
        local f = rawget(_G, n)
        local fr = f and f:IsShown() and geo.Rect(f)
        if fr then check(not overlap(r, fr), "TreeAICompassFrame et " .. n .. " séparés") end
      end
    end
    AD.SetLink("spf", "arrow", true)
    M.Advance(4, 0.1)
    check(not (cf and cf:IsShown()), "lien rétabli : de nouveau pas de 2e flèche")
  end

  -- ------------------------------------------------------------------ Shortest Path Forever is there: no duplicate lists
  local txt = {}
  for _, tab in ipairs({ "now", "prof", "route", "quests" }) do
    local okL, lines = pcall(T.GetTabLines, T, tab)
    for _, l in ipairs(okL and type(lines) == "table" and lines or {}) do txt[#txt + 1] = tab .. " | " .. T.PlainText(l.text or "") end
  end
  out("  lignes TreeAI (onglets) avec Suivi Forever présent :")
  for _, l in ipairs(txt) do out("    " .. l) end
  if T.OtherColumn then
    local col = T:OtherColumn()
    check(type(col) == "table" and col.route and col.reagents, "TreeAI sait que Suivi Forever montre déjà le trajet et les composants")
  else
    check(false, "T:OtherColumn() existe")
  end
  local all = table.concat(txt, "\n")
  check(not all:find("Composants de métier", 1, true) and not all:find("prof | %d+/%d+ "), "pas de 2e liste de composants de métier")
  -- (default profile — what the player runs: no second list of quests / route beside Suivi Forever ; a player who
  -- switches « Suivi TreeAI » on himself gets it, placed in free space like the rest)
  if os.getenv("REALUI_DEFAULT") == "1" then
    for _, n in ipairs({ "TreeAIWatchFrame", "TreeAITrackerFrame", "TreeAITodoFrame" }) do
      local f = rawget(_G, n)
      check(not (f and f:IsShown()), n .. " : pas de 2e liste de trajet / de quêtes à côté de Suivi Forever")
    end
  end
  -- at a merchant, the profession card does not repeat the component list that Suivi Forever shows all the time
  local PLv = T.ProfLevel
  if PLv and PLv.NpcModel then
    M.units.npc = { name = "Marchand-Test" }
    M.merchantItems = { { id = 2321, price = 100, stack = 1, maxStack = 20 } }
    M.SetMerchant(true)
    M.Advance(2, 0.1)
    local okM, m = pcall(PLv.NpcModel)
    local body = okM and type(m) == "table" and table.concat(m.lines or {}, " | ") or ""
    out("  carte Métiers chez le marchand (Suivi Forever présent) : " .. (body ~= "" and body or "aucune"))
    check(not body:find("%d+/%d+ ") and not (okM and type(m) == "table" and m.title == "Composants de métier"),
      "chez le marchand : pas de 2e liste « Composants de métier »")
    rawset(_G, "ShortestPathForever", nil)
    local okM2, m2 = pcall(PLv.NpcModel)
    out("  … sans Suivi Forever : " .. ((okM2 and type(m2) == "table") and table.concat(m2.lines or {}, " | ") or "aucune"))
    rawset(_G, "ShortestPathForever", { API = {} })
    M.SetMerchant(false)
    M.Advance(1, 0.1)
  end

  -- ------------------------------------------------------------------ the scale changes: everything finds free space again
  geo.SetUIScale(SCALE == 0.64 and 0.75 or 0.64)
  M.Advance(12, 0.1)
  local s2 = geo.scale
  local PX2 = s2 * geo.opts.h / 768
  local _, SH2 = geo.Screen()
  local strip = rawget(_G, "TreeAIStrip")
  if check(strip and strip:IsShown() and geo.OnScreen(strip, 2), "échelle changée : la bande est toujours à l'écran") then
    local r = geo.Rect(strip)
    -- (the occupied frames keep their unit sizes: other addons do not re-anchor in this scenario; only on-screen is judged)
    out(("  après changement d'échelle (%.2f) : bande x %d–%d, y %d–%d px"):format(s2, r0(r.l * PX2), r0((r.l + r.w) * PX2),
      r0((SH2 - r.b - r.h) * PX2), r0((SH2 - r.b) * PX2)))
  end

  for i = chat0 + 1, #M.output do if not tostring(M.output[i]):find("^%[UIError%]") then out("chat: " .. M.output[i]) check(false, "rien dans le chat") end end
  check(#(M.blocked or {}) == b0, "aucune action bloquée")
  check(#M.violations == v0, "aucune violation")
end
