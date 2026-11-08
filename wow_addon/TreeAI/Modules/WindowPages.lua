-- TreeAI — pages « Quêtes », « Trajet » et « Donjons » de la fenêtre TreeAI (agent UIREDO, extension du module
-- Window : même module, même coût mesuré). Rendu seulement : les données viennent de Planner / Route (étapes,
-- arrêts), Quests (journal analysé : ici, à rendre, gris, par zone), ZonePlan (zone de travail), Dungeon
-- (donjons, quêtes, boss, butin du groupe). Rien n'est recalculé ici ; fenêtre fermée = rien ne tourne.
local T = _G.TreeAI
local W = T and T.Window
if not W then return end
local G = _G
local fmt, floor, max, min = string.format, math.floor, math.max, math.min
local tconcat, tsort = table.concat, table.sort
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
local ICON = "Interface\\Icons\\"
local function mod(name) local m = T.modules and T.modules[name] if type(m) == "table" then return m end end
local function routeOn() local R = T.Route return R and R.IsEnabled and R:IsEnabled() and R or nil end
local function km(yd)
  local m = (tonumber(yd) or 0) * 0.9144
  if m < 1000 then return fmt("%d m", floor(m / 10 + 0.5) * 10) end
  return (fmt("%.1f km", m / 1000):gsub("%.", ","))
end
W.Km = km

-- ===================================================================== Quêtes (façon Adventure Guide)
local function questProgress(q)
  if q.complete then return "finie · à rendre" .. (q.turnZone and q.turnZone ~= "" and (" (" .. q.turnZone .. ")") or "") end
  local parts = {}
  for _, o in ipairs(q.obj or {}) do
    if not o.finished then
      local name = T.ObjectiveName and T.ObjectiveName(o.text or o.name or "") or (o.name or o.text or "")
      parts[#parts + 1] = (o.need or 0) > 1 and fmt("%s %d/%d", strip(name), o.have or 0, o.need) or strip(name)
      if #parts >= 2 then break end
    end
  end
  return #parts > 0 and tconcat(parts, " · ") or "en cours"
end
-- (4.0) clic sur une quête du journal : la boussole la vise (comme dans le suivi) ; sans position connue, le pied de
-- la fenêtre le dit (avant : « Ouvre ton journal… Abandonner » dans le chat, même pour une quête à rendre)
local QUEST_HINT = "Clic : la boussole vise cette quête"
local function openQuest(q)
  return function()
    local C = T.Compass
    local t = q and q.id and C and C.SetQuestTarget and C.SetQuestTarget(q.id)
    if not t and W.Flash then W.Flash(fmt("« %s » : pas encore de position connue pour la boussole.", strip(q and q.title or "?"))) end
  end
end

--- (4.1 LAYOUT) le journal sur la page Quêtes : à rendre, en cours ici, ménage du journal, en pause, ailleurs par zone,
--- autres conseils de l'onglet Quêtes ; vrai si quelque chose a été dessiné
function W.RenderQuestLog(Ly, skipEmpty)
    local Q = mod("Quests")
    local a = Q and Q.last
    local any = false
    if a then
      -- à rendre (hors des étapes déjà montrées)
      local turn = {}
      for _, q in ipairs(a.turnIn or {}) do if W.Match(q.title, q.zone) then turn[#turn + 1] = q end end
      if #turn > 0 and Ly.header("À rendre", "q:turn", #turn, W.MARK.turn) then
        for _, q in ipairs(turn) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, QUEST_HINT) end
      end
      local Bn = T.Brain                                   -- (BRAIN) une quête en pause n'est listée qu'une fois (« En pause »)
      local function shown(q) return not (Bn and Bn.Parked and Bn.Parked(q.id)) end
      local here = {}
      for _, q in ipairs(a.here or {}) do if W.Match(q.title) and shown(q) then here[#here + 1] = q end end
      if #here > 0 and Ly.header("En cours ici", "q:here", #here, W.MARK.pick) then
        for _, q in ipairs(here) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, QUEST_HINT) end
      end
      -- (JOURNAL 4.0.1) « Nettoyer mon journal » : les faits du jeu (grises, échouées, bloquées, donjon fait…), la page
      -- « Journal » pour abandonner (2 clics) — Modules/JournalPage.lua
      local Jp = T.JournalPage
      if Jp and Jp.QuestsSection then Jp.QuestsSection(Ly) end
      local Bp = T.BrainPage                               -- (BRAIN) « En pause » : quêtes mises de côté par le pilote auto
      if Bp and Bp.QuestsSection then Bp.QuestsSection(Ly) end
      -- ailleurs, par zone (plus de quêtes d'abord), repliés
      local zones = {}
      for _, q in ipairs(a.elsewhere or {}) do
        if not q.grey and W.Match(q.title, q.zone) and shown(q) then
          local z = q.zone or "?"
          zones[z] = zones[z] or {}
          zones[z][#zones[z] + 1] = q
        end
      end
      local zl = {}
      for z, list in pairs(zones) do zl[#zl + 1] = { z = z, list = list } end
      tsort(zl, function(x, y) if #x.list ~= #y.list then return #x.list > #y.list end return x.z < y.z end)
      for _, it in ipairs(zl) do
        if Ly.header("Direction " .. it.z, "q:zone:" .. it.z, #it.list, W.MARK.pick, true) then
          for _, q in ipairs(it.list) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, QUEST_HINT) end
        end
      end
      any = any or #turn > 0 or #here > 0 or #zl > 0
    end
    -- le reste de l'onglet « Quêtes » (objets de quête, conseils de zone…) sans le trajet déjà montré au-dessus
    local extra = {}
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("quests") or {}) do
      -- (JOURNAL) le bloc « Nettoyer mon journal » du panneau texte (Abandon) : déjà la section ci-dessus
      if e.source ~= "plan" and e.source ~= "Abandon" and not e.header and not e.control and W.Match(e.text, e.right) then
        extra[#extra + 1] = e
      end
    end
    if #extra > 0 and Ly.header("Autres conseils", "q:extra", #extra, ICON .. "INV_Misc_Note_01", true) then
      for _, e in ipairs(extra) do Ly.row(e) end
    end
    if not any and #extra == 0 and not skipEmpty then
      local Zp = T.ZonePlan
      Ly.empty(W.Query() ~= "" and ("Rien ne correspond à « " .. W.Query() .. " ».")
        or "Aucune quête à suivre ici. Parle aux PNJ marqués « ! », ou choisis une autre zone de quêtes.",
        (Zp and Zp.ShowZoneMenu and Zp.IsEnabled and Zp:IsEnabled()) and { "Choisir une zone", function() Zp:ShowZoneMenu() end,
          "Clic : la liste des zones à ton niveau" } or nil)
    end
    return any or #extra > 0
end

-- (ZONEFIX) « Zones à faire » : tes zones à ton niveau (niveaux des quêtes, à prendre / en cours, trajet pour y aller,
-- « déjà finie »), un clic = travailler dans cette zone (le trajet est refait tout de suite)
function W.ZonesToDo(Ly)
  local Zp = T.ZonePlan
  if not (Zp and Zp.ZonesToDo and Zp.IsEnabled and Zp:IsEnabled()) then return end
  if Zp.CountZones then pcall(Zp.CountZones, Zp) end
  local ok, list = pcall(Zp.ZonesToDo, Zp)
  if not (ok and type(list) == "table") or #list == 0 then return end
  local rows = {}
  for _, e in ipairs(list) do
    local text, right = Zp.ZoneRowText(e)
    if W.Match(text) then rows[#rows + 1] = { e = e, text = text, right = right } end
  end
  if #rows == 0 then return end
  if Ly.header("Zones à faire", "route:zones", #rows, ICON .. "INV_Misc_Map_01") then
    if Zp._counting then Ly.text("Comptage des quêtes de chaque zone…", "S", "muted") end
    for _, r in ipairs(rows) do
      local e = r.e
      local tip = { e.done and "Plus de quête à ton niveau ici : toutes faites." or
        fmt("%d quête%s à prendre à ton niveau, %d dans ton journal.", e.avail, e.avail > 1 and "s" or "", e.inLog) }
      if e.cross then tip[#tip + 1] = "Autre continent : bateau ou zeppelin compris dans le trajet." end
      Ly.row({ text = (e.on and "● " or "") .. r.text, right = r.right, kind = e.done and "info" or "quest",
        prio = e.here and 2 or 1, tooltip = tip,
        clickHint = e.on and "Déjà ta zone de travail" or "Clic : travailler dans cette zone (trajet refait tout de suite)",
        onClick = (not e.on) and function() Zp:PickZone(e.map) end or nil })
    end
  end
end

-- ===================================================================== Trajet (tous les arrêts, zone, choix)
-- (4.1 LAYOUT) l'ancienne page « Trajet » est une section de la page Quêtes (W.Open("route") y mène)
function W.RouteSub()
  local Zp = T.ZonePlan
  local A = Zp and Zp.IsEnabled and Zp:IsEnabled() and try(Zp.Allowed, Zp)
  return A and A.label and ("Zone de travail : " .. A.label) or "Le chemin le plus rapide entre tes étapes"
end
function W.RenderRoute(Ly)
    local R, P = routeOn(), T.Planner
    if not (R and P and P.steps and P.cursor) then
      local Zp = T.ZonePlan
      Ly.empty("Pas encore de trajet. Prends des quêtes : TreeAI prépare l'ordre des arrêts tout seul.",
        (Zp and Zp.Recalc and Zp.IsEnabled and Zp:IsEnabled()) and { "Calculer le trajet", function() Zp:Recalc() end,
          "Clic : TreeAI range tes quêtes en arrêts" } or nil)
      return
    end
    local all = R:Stops(64) or {}
    local paused = R.char and R.char.paused
    -- (QUESTQA F7) même horizon que l'onglet « Trajet » et la carte : une seule durée pour un trajet
    local keepN, total, yd, beyond, travel = #all, tonumber(all.total) or 0, all.yd, 0, all.travel
    if type(R.Horizon) == "function" then
      local okH, a, b, c, d, e = pcall(R.Horizon, all)
      if okH and tonumber(a) then
        keepN, total, yd, beyond = a, tonumber(b) or total, c or yd, tonumber(d) or 0
        if tonumber(e) then travel = e end            -- (QA-QUEST F7) le déplacement DANS l'horizon
      end
    end
    local st = { total = total, yd = yd, travel = travel, here = all.here }
    for i = 1, keepN do st[i] = all[i] end
    -- (QA-QUEST F18) numéros d'arrêt partagés avec l'onglet « Trajet », la carte et la carte du monde (R.Groups)
    local grp
    if type(R.Groups) == "function" then
      local okG, gg = pcall(R.Groups, st, keepN)
      if okG and type(gg) == "table" then grp = gg end
    end
    local nStops = (grp and tonumber(grp.n)) or #st
    local title = paused and "Trajet en pause" or fmt("Trajet %s · %s", W.MinutesText(st.total) or "< 1 min", km(st.yd))
    local route = P.route or {}
    local Zp = T.ZonePlan
    local zon = Zp and Zp.IsEnabled and Zp:IsEnabled()
    -- (ZONEFIX) le mode du trajet (pas « Le plus rapide » pour tous) et le thème en cours en sous-titre
    local name = (zon and Zp.StyleName and Zp:StyleName())
      or (T.Plain and T.Plain.RouteName and T.Plain.RouteName(route.id, route.title)) or route.title or "Trajet TreeAI"
    local th = zon and Zp.ThemeParts and select(2, pcall(Zp.ThemeParts, Zp)) or nil
    local travel = (tonumber(st.travel) or 0) > 0 and ("dont " .. (W.MinutesText(st.travel) or "") .. " de déplacement") or nil
    local line2 = fmt("%d arrêt%s · %s", nStops, nStops > 1 and "s" or "", strip(name))
    if type(th) == "table" and th.full then
      Ly.hero(W.TAB_ICON.route, title, th.full, line2 .. (travel and (" · " .. travel) or ""),
        function() R:TogglePause() end, nil, paused and "Clic : reprendre" or "Clic : mettre en pause")
    else
      Ly.hero(W.TAB_ICON.route, title, line2, travel, function() R:TogglePause() end, nil,
        paused and "Clic : reprendre" or "Clic : mettre en pause")
    end
    -- mode, zone de travail, autres zones, détour, suivi (lignes du producteur, sans les arrêts dessinés en dessous)
    if Zp and Zp.HeadLines and zon then
      local ok, zl = pcall(Zp.HeadLines, Zp)
      for _, l in ipairs(ok and type(zl) == "table" and zl or {}) do
        if l.text then
          local b = Ly.row({ id = l.id, text = l.text, prio = l.prio, icon = l.icon or W.TAB_ICON.route, tooltip = l.tooltip,
            onClick = l.onClick, onRightClick = l.onRightClick })
          if l.id == "zone:pick" then W.zoneRow = b end   -- (ONBOARD) cible « zone » de la visite guidée
        end
      end
    end
    local plan = R.PlanLines and select(2, pcall(R.PlanLines, R, P)) or nil
    for _, l in ipairs(type(plan) == "table" and plan or {}) do
      local t = strip(l.text)
      if l.id ~= "route:head" and l.id ~= "plan:step" and not t:find("^Arrêt %d") and not t:find("^… %d") and not t:find("^Zone")
          and l.onClick then
        Ly.row({ text = l.text, prio = l.prio, tooltip = l.tooltip, onClick = l.onClick, onRightClick = l.onRightClick })
      end
    end
    if paused then
      Ly.empty("Le trajet est en pause : rien n'est guidé.", { "Reprendre le trajet", function() R:TogglePause() end,
        "Clic : la boussole et la carte reprennent tes étapes" })
      return
    end
    if Ly.header("Arrêts", nil, nStops, W.TAB_ICON.route) then
      -- (ZONEFIX) la MÊME ligne que la carte « Prochaine étape » (W.StopItem) : un seul format, plus de « ≈ ≈ »
      local shown, via = 0, false
      for k, e in ipairs(st) do
        if shown >= 30 then
          Ly.text(fmt("… et %d autres arrêts, affichés au fur et à mesure.", #st - k + 1), "S", "muted")
          break
        end
        local item = W.StopItem(P, e, k, st, grp, via, k)
        if e.kind and e.kind ~= "quest" then via = true end
        if W.Match(item.title, item.sub) then Ly.step(item, k == 1); shown = shown + 1 end
      end
      if (tonumber(beyond) or 0) > 0 and W.Query() == "" then
        Ly.text(fmt("… et %d arrêt%s de plus, recalculé%s en route.", beyond, beyond > 1 and "s" or "", beyond > 1 and "s" or ""),
          "S", "muted")
      end
    end
    W.ZonesToDo(Ly)
end
function W.RouteAction()
    local R = routeOn()
    local P = T.Planner
    if not (R and P and P.steps and #P.steps > 0) then return nil end   -- (4.0 QA) pas de trajet : rien à mettre en pause
    local paused = R.char and R.char.paused
    if not paused and R.Following and T.ExternalGuide and T:ExternalGuide() == "spf" then
      return "Suivre (Suivi Forever)", function() R:Follow() end, "Clic : envoyer les arrêts dans l'ordre à Shortest Path Forever"
    end
    return paused and "Reprendre" or "Pause", function() R:TogglePause() end,
      paused and "Clic : reprendre le guidage" or "Clic : mettre le trajet en pause"
end

-- ===================================================================== (4.1 LAYOUT) page « Quêtes » : le moyeu des quêtes
-- haut : les 4 modes du cerveau des quêtes (gros boutons, compte, touche) ; « Prochaine étape » (le texte de la
-- boussole) ; au plus 3 actions utiles ; le trajet (temps, zone) ; « À prendre près de toi » (5, avec la raison) ;
-- « Ménage du journal » ; le journal (à rendre, en cours ici, ailleurs). Sans cerveau des quêtes : le trajet comme avant.
local function QB()
  local q = T.QuestBrain
  if type(q) == "table" and type(q.Modes) == "function" and not (q.IsEnabled and not q:IsEnabled()) then return q end
end
W.QB = QB
local MODE_COUNT = { turnin = { "%d à rendre", "rien à rendre" }, pickup = { "%d à prendre", "rien à prendre ici" },
  zone = { "%d en cours ici", "rien en cours ici" }, group = { "%d en commun", "rien en commun" } }
function W.ModeTiles(Ly)
  local B = QB()
  if not B then return false end
  local ok, list = pcall(B.Modes)
  if not (ok and type(list) == "table" and #list > 0) then return false end
  local tiles = {}
  for _, m in ipairs(list) do
    if type(m) == "table" and m.id then
      local n = tonumber(m.count) or 0
      local cw = MODE_COUNT[m.id] or { "%d", "0" }
      local sub = n > 0 and fmt(cw[1], n) or cw[2]
      local key = m.key and (W.KeyText and W.KeyText(m.key) or m.key)
      if key then sub = sub .. " · touche " .. key end
      if m.active then sub = "● " .. sub end
      local id = m.id
      tiles[#tiles + 1] = { m.label or m.short or id, sub, function() pcall(B.SetMode, id) end,
        { m.tip, key and ("Touche : " .. key) or "Pas de touche : Réglages → Raccourcis" },
        m.active and true or false, m.active and "Mode en cours" or "Clic : passer à ce mode (le trajet suit tout de suite)" }
    end
  end
  Ly.tiles(tiles, 2)
  return true
end
--- « Prochaine étape » : la même chose que la boussole (T.Next si présent, sinon le texte de la boussole, sinon le
--- 1er arrêt du trajet)
function W.NextItem()
  local nx = type(T.Next) == "function" and select(2, pcall(T.Next)) or nil
  if type(nx) == "table" and nx.title then
    local parts = {}
    if nx.line and nx.line ~= "" then parts[#parts + 1] = nx.line end
    local Cm = T.Compass
    local dtxt = Cm and Cm.DistText and select(2, pcall(Cm.DistText, nx.dist, nx.eta)) or nil
    if type(dtxt) == "string" and dtxt ~= "" then parts[#parts + 1] = dtxt end
    return { n = nx.index or 1, title = nx.title, sub = tconcat(parts, " · "), mark = nx.kind == "turn" and "turn" or nil,
      onClick = function() local C = T.Compass if C and C.Resume then pcall(C.Resume) end end, actionText = "la boussole vise cette étape" }
  end
  local Cm = T.Compass
  local wd = Cm and Cm.Widgets and select(2, pcall(Cm.Widgets))
  local fr = type(wd) == "table" and wd.frame
  if fr and meth(fr, "IsShown") and not (wd.mini) then
    local lbl = strip(meth(wd.label, "GetText") or "")
    local dst = strip(meth(wd.dist, "GetText") or "")
    if lbl ~= "" then return { n = 1, title = lbl, sub = dst } end
  end
  local g = W.Guide(1)
  local st = g and g.steps and g.steps[1]
  if st and not st.entry then return st end
end
--- « À prendre près de toi » : les 5 meilleures quêtes à prendre (cerveau des quêtes), avec la raison ; élite / groupe :
--- un badge « Élite · à 2+ »
function W.PickupRows(Ly, n)
  local B = QB()
  if not (B and type(B.PickupList) == "function") then return false end
  local ok, list = pcall(B.PickupList, n or 5)
  if not (ok and type(list) == "table") then return false end
  local shown = {}
  for _, e in ipairs(list) do if type(e) == "table" and W.Match(e.title, e.npc, e.why) then shown[#shown + 1] = e end end
  if Ly.header("À prendre près de toi", "q:pickup", #shown, W.MARK and W.MARK.pick or nil) then
    if #shown == 0 then Ly.text("Rien de rentable à prendre près de toi pour l'instant.", "S", "muted", 6) end
    for _, e in ipairs(shown) do
      local sub = {}
      if e.elite then sub[#sub + 1] = "|cffff8040Élite · à 2+|r" end
      if e.dungeon then sub[#sub + 1] = "|cffff8040Donjon · à 4+|r" end
      if e.npc then sub[#sub + 1] = strip(e.npc) end
      if tonumber(e.dist) then sub[#sub + 1] = km(e.dist) end
      if e.why and e.why ~= "" then sub[#sub + 1] = strip(e.why) end
      local qid = e.questID
      Ly.quest(e.level, e.title, tconcat(sub, " · "), function()
        local C = T.Compass
        if C and C.SetWaypoint and e.x and e.y then pcall(C.SetWaypoint, e.x / (e.x > 1 and 100 or 1), e.y / (e.y > 1 and 100 or 1),
          e.npc or e.title, e.map, "pickup") end
      end, { e.why or "", qid and ("Quête " .. qid) or nil }, "Clic : la boussole vise le PNJ")
    end
  end
  return true
end
--- « Rendre mes quêtes » : la liste des quêtes finies (mode actif) — même ordre que le trajet
function W.TurnInRows(Ly)
  local B = QB()
  if not (B and type(B.TurnInList) == "function" and B.Mode and B.Mode() == "turnin") then return false end
  local ok, list = pcall(B.TurnInList)
  if not (ok and type(list) == "table") then return false end
  if Ly.header("Rendre mes quêtes", "q:turnin", #list, W.MARK and W.MARK.turn or nil) then
    if #list == 0 then Ly.text("Aucune quête finie à rendre.", "S", "muted", 6) end
    for _, e in ipairs(list) do
      local sub = {}
      if e.npc then sub[#sub + 1] = strip(e.npc) end
      if e.zone then sub[#sub + 1] = strip(e.zone) end
      if tonumber(e.eta) then sub[#sub + 1] = W.MinutesText(e.eta) or "" end
      Ly.quest(nil, e.title, tconcat(sub, " · "), nil)
    end
  end
  return true
end
W.AddPage({ id = "quests", label = "Quêtes", icon = W.TAB_ICON.quests, title = "Quêtes",
  desc = "Rendre, prendre, tout faire ici, avec le groupe : la prochaine étape, le trajet, le journal",
  sub = function()
    local B = QB()
    local lab = B and B.Label and select(2, pcall(B.Label))
    local g = W.Guide(1)
    local s = W.GuideSummary(g, true)
    local head = type(lab) == "string" and ("Mode : " .. lab) or W.RouteSub()
    return head .. (s ~= "" and (" · " .. s) or "")
  end,
  badge = function()
    local n = 0
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do if (e.prio or 1) >= 3 and not e.ownPage then n = n + 1 end end
    return n > 0 and n or nil
  end,
  render = function(Ly)
    local ps = T.PlayerState and T:PlayerState() or {}
    -- (4.1) Suivi Forever montre SON trajet (différent du tien) : UNE ligne, deux choix, jamais redemandé une fois choisi
    local Ad = T.Addons
    if type(Ad) == "table" and type(Ad.SPFConflict) == "function" and Ad.SPFConflict() and W.Query() == "" then
      Ly.text("Suivi Forever affiche son propre trajet (différent du tien).", "S", "prio2")
      Ly.buttons({ { "Lui donner l'ordre de TreeAI", function() Ad.SPFChoose("order") end,
          "Clic : Suivi Forever suit l'ordre de TreeAI (renvoyé à chaque trajet refait) — un seul trajet à l'écran" },
        { "Le rendre silencieux", function() Ad.SPFChoose("silent") end,
          "Clic : son suivi se cache (dans SES options), TreeAI guide seul" } }, 2)
      Ly.gap(4)
    end
    -- 1. les 4 modes
    W.ModeTiles(Ly)
    -- 2. prochaine étape (mort / donjon : la course au corps / le donjon d'abord, dans les actions)
    if not (ps.dead or ps.ghost or W.InDungeon()) then
      local nx = W.NextItem()
      if nx and W.Match(nx.title, nx.sub) then
        Ly.header("Prochaine étape", nil, nil, ICON .. "INV_Misc_Book_09")
        Ly.step(nx, true)
      end
    end
    -- 3. au plus 3 actions utiles (danger, mort, courses) ; jamais d'avis non demandé
    local acts = W.NowActions and W.NowActions(3) or {}
    local keep = {}
    for _, e in ipairs(acts) do if W.Match(e.text, e.right) then keep[#keep + 1] = e end end
    if #keep > 0 then
      Ly.header("À faire maintenant", nil, #keep, W.TAB_ICON.now)
      for _, e in ipairs(keep) do Ly.row(e) end
    end
    -- (4.1) Suivi TreeAI allumé mais caché : il passerait sur le suivi de Questie / du jeu (jamais l'un sur l'autre)
    local Wt = T.Watch
    if type(Wt) == "table" and Wt._active and Wt.blockedBy and W.Query() == "" then
      Ly.row({ id = "watch:blocked", text = "Suivi TreeAI caché : il passerait sur " .. tostring(Wt.blockedBy) .. ".",
        kind = "info", tooltip = { "Déplace-le à côté (Réglages → Affichage → Déplacer les cadres), ou garde le suivi du jeu." },
        clickHint = "Clic : déplacer les cadres", onClick = function() if T.SetEditMode then T:SetEditMode(true) end end })
    end
    -- 4. « Rendre mes quêtes » (mode actif) puis le trajet
    W.TurnInRows(Ly)
    W.RenderRoute(Ly)
    -- 5. à prendre près de toi
    W.PickupRows(Ly, 5)
    -- 6. ménage + journal
    W.RenderQuestLog(Ly, true)
    -- 7. l'option des dialogues de quête (Confort), en bas : un clic
    local Qo = T.QoL
    if type(Qo) == "table" and type(Qo.SetQuestAuto) == "function" and W.Query() == "" then
      local on = Qo.QuestAutoOn and Qo.QuestAutoOn() and Qo._active
      Ly.row({ id = "mod:qol:quests", text = "Accepter / rendre les quêtes tout seul" .. (on and "" or " (coupé)"),
        right = on and "allumé" or "clic : allumer", kind = on and "info" or "quest", icon = W.MARK and W.MARK.turn or nil,
        tooltip = { "Accepte les quêtes que le plan veut, rend les quêtes finies.",
          "Récompense à choisir : la meilleure est entourée, jamais prise à ta place.", "Maj enfoncée : pas cette fois." },
        clickHint = on and "Clic : couper" or "Clic : allumer", onClick = function() Qo.SetQuestAuto(not on) end })
    end
  end,
  action = function() return W.RouteAction() end,
  status = function()
    local Q = mod("Quests")
    local n = Q and Q.lastList and #Q.lastList or 0
    local cap = tonumber(try(G.C_QuestLog and G.C_QuestLog.GetMaxNumQuestsCanAccept)) or 20
    return fmt("Journal : %d/%d", n, cap)
  end,
})

-- ===================================================================== Donjons (façon Journal des donjons)
local DG_ICON = {
  RFC = "Spell_Fire_Fire", THANES = "INV_Hammer_09", LORD = "Spell_Shadow_RaiseDead", DM = "INV_Misc_Bone_HumanSkull_01",
  WC = "Spell_Nature_Regeneration", SFK = "Spell_Shadow_Charm", BFD = "Spell_Frost_SummonWaterElemental",
  STOCKS = "INV_Misc_Key_04", RFK = "INV_Misc_Root_02", GNOMER = "INV_Misc_Gear_08", EXCAV = "INV_Pick_02",
  DALARAN = "Spell_Holy_MagicalSentry", SM = "Spell_Holy_SealOfWrath", RFD = "Spell_Shadow_AnimateDead",
  ULDA = "INV_Misc_StoneTablet_01", SUNKCITY = "Spell_Frost_FrostWard", KROLDOK = "INV_Axe_09", ZF = "INV_Misc_Head_Troll_01",
  MARA = "Spell_Nature_ResistNature", ST = "INV_Misc_Head_Dragon_Green", BRD = "Spell_Fire_Incinerate",
  ALCAZ = "INV_Misc_Key_13", BRS = "INV_Misc_Head_Dragon_Black", DIREMAUL = "INV_Misc_Bone_ElfSkull_01",
  NOIREG = "INV_Misc_Head_Dragon_01", STRAT = "Spell_Shadow_RaiseDead", SCHOLO = "Spell_Shadow_DeathCoil",
  SHAPER = "INV_Misc_Gem_Variety_01",
}
-- couleur de fond par tranche de niveau (vert → bleu → violet → orange → rouge), jamais d'illustration
local BAND = { { 13, 0.20, 0.45, 0.25 }, { 20, 0.18, 0.35, 0.50 }, { 28, 0.35, 0.22, 0.50 }, { 38, 0.50, 0.32, 0.12 },
  { 48, 0.50, 0.15, 0.12 }, { 99, 0.30, 0.30, 0.34 } }
local function bandColor(lv)
  for _, b in ipairs(BAND) do if (lv or 1) <= b[1] then return b[2], b[3], b[4] end end
  return 0.3, 0.3, 0.3
end
W.DungeonColor = bandColor
local function gradient(tex, r, g, b)
  meth(tex, "SetTexture", "Interface\\Buttons\\WHITE8X8")
  meth(tex, "SetVertexColor", 1, 1, 1, 1)
  local CC = rawget(G, "CreateColor")
  local ok = false
  if type(CC) == "function" and type(tex.SetGradient) == "function" then
    ok = pcall(tex.SetGradient, tex, "VERTICAL", CC(r * 0.35, g * 0.35, b * 0.35, 0.95), CC(r, g, b, 0.95))
  end
  if not ok and type(tex.SetGradientAlpha) == "function" then
    ok = pcall(tex.SetGradientAlpha, tex, "VERTICAL", r * 0.35, g * 0.35, b * 0.35, 0.95, r, g, b, 0.95)
  end
  if not ok then W._color(tex, r, g, b, 0.9) end
end

local qCache = {}   -- [clé] = { t, done, total, pending }
local function dungeonQuests(D, d)
  local now = try(G.GetTime) or 0
  local c = qCache[d.key]
  if c and now - c.t < 20 then return c end
  local ok, res = pcall(D.Quests, d)
  res = ok and type(res) == "table" and res or { inLog = {}, toGet = {} }
  local done = 0
  for _, e in ipairs(res.inLog or {}) do if e.done then done = done + 1 end end
  c = { t = now, done = done, total = #(res.inLog or {}) + #(res.toGet or {}), inLog = #(res.inLog or {}), pending = res.pending, res = res }
  qCache[d.key] = c
  return c
end
W.DungeonQuests = dungeonQuests

local cardPool
local function dcardPool(content)
  if cardPool then return cardPool end
  -- (4.0 QA) W._pool : la réserve est vidée par la fenêtre à chaque rendu → 18 cartes recyclées, jamais recréées
  cardPool = W._pool("dcard", function(parent)
    local f = W._newButton(parent)
    f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
    meth(f.icon, "SetSize", 34, 34)
    meth(f.icon, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -10)
    meth(f.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
    f.name = W._text(f, "L", "gold")
    meth(f.name, "SetPoint", "TOPLEFT", f, "TOPLEFT", 52, -10)
    meth(f.name, "SetWordWrap", false)
    f.q = W._text(f, "S", "text")
    meth(f.q, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", -10, 8)
    meth(f.q, "SetJustifyH", "RIGHT")
    f.check = meth(f, "CreateTexture", nil, "OVERLAY")
    meth(f.check, "SetSize", 14, 14)
    meth(f.check, "SetTexture", "Interface\\RaidFrame\\ReadyCheck-Ready")
    meth(f.check, "SetPoint", "RIGHT", f.q, "LEFT", -3, 0)
    f.done = W._text(f, "S", "text")      -- (JOURNAL) « Fait le 3 nov. » / « Fait en partie : 2/5 boss »
    meth(f.done, "SetPoint", "TOPLEFT", f, "TOPLEFT", 52, -33)
    meth(f.done, "SetWordWrap", false)
    f.lv = W._text(f, "S", "text")
    meth(f.lv, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 10, 8)
    f.tag = W._text(f, "S", "muted")
    meth(f.tag, "SetPoint", "LEFT", f.lv, "RIGHT", 8, 0)
    meth(f.tag, "SetJustifyH", "LEFT")
    meth(f, "SetScript", "OnClick", function(self)
      if self.key and W.char then W.char.dungeon = self.key; W.Render() end
    end)
    meth(f, "SetScript", "OnEnter", function(self)
      W.Tooltip(self, self.label, self.tipL, "Clic : boss, butin pour ton groupe, quêtes")
    end)
    meth(f, "SetScript", "OnLeave", W.HideTooltip)
    return f
  end)
  return cardPool
end

-- (JOURNAL 4.0.1) ce qu'on SAIT d'un donjon (Dungeon.DoneInfo : boss tués par TreeAI, verrou du jeu, quêtes finies)
local function doneInfo(D, d)
  if type(D.DoneInfo) ~= "function" then return nil end
  local ok, i = pcall(D.DoneInfo, d)
  return ok and type(i) == "table" and i or nil
end
local DONE_SRC = { run = "tous les boss tués (historique TreeAI)", lock = "verrou d'instance du jeu",
  quests = "toutes ses quêtes rendues (selon le jeu)" }
-- « Quêtes faites 2/6 » (faits du jeu) ; repli : le compte QuestieDB ; « Aucune quête pour toi » (quêtes de l'autre faction)
local function questText(D, d, q, di)
  if di and (di.qTotal or 0) > 0 then return fmt("Quêtes faites %d/%d", di.qDone or 0, di.qTotal) end
  if q.pending and q.total == 0 then return "quêtes…" end
  if q.total > 0 then return fmt("%d quête%s", q.total, q.total > 1 and "s" or "") end
  local data = D.Data and D.Data()
  if data and data.quests and data.quests[d.key] then return "Aucune quête pour toi" end
  return ""
end

local function renderDungeonList(Ly, D)
  local pl = tonumber(try(G.UnitLevel, "player")) or 1
  local all = W.char and W.char.dgAll
  Ly.buttons({
    { all and "Seulement à mon niveau" or "Tous les donjons", function() if W.char then W.char.dgAll = not W.char.dgAll end W.Render() end,
      "Clic : changer de liste" },
  }, 1)
  local list = {}
  local planned = try(D.Planned)
  local cur = try(D.Current)
  for _, d in ipairs(all and (D.Index().list or {}) or D.ForLevel(pl)) do
    if W.Match(d.n, d.key, tconcat(d.kw or {}, " ")) then list[#list + 1] = d end
  end
  -- (JOURNAL 4.0.1) pas encore faits et à ton niveau d'abord ; ceux déjà faits à la fin
  local info, fit = {}, {}
  for _, d in ipairs(list) do
    info[d] = doneInfo(D, d)
    fit[d] = (pl >= (d.min or 1) - 1 and pl <= (d.max or 60)) and 0 or 1
  end
  tsort(list, function(a, b)
    local da, db = (info[a] and info[a].done) and 1 or 0, (info[b] and info[b].done) and 1 or 0
    if da ~= db then return da < db end
    if fit[a] ~= fit[b] then return fit[a] < fit[b] end
    if (a.min or 0) ~= (b.min or 0) then return (a.min or 0) < (b.min or 0) end
    return tostring(a.key) < tostring(b.key)
  end)
  if #list == 0 then
    Ly.empty(W.Query() ~= "" and ("Aucun donjon ne correspond à « " .. W.Query() .. " ».")
      or "Aucun donjon à ton niveau pour l'instant.",
      { "Voir tous les donjons", function() if W.char then W.char.dgAll = true end W.Render() end, "Clic : la liste complète" })
    return
  end
  local cols = Ly.col >= 520 and 2 or 1
  local cw = floor((Ly.col - (cols - 1) * 10) / cols)
  local ch = 76
  local p = dcardPool(W.GetWindow().content)
  for i, d in ipairs(list) do
    local c = (i - 1) % cols
    if c == 0 and i > 1 then Ly.y = Ly.y + ch + 10 end
    local f = W._acquire(p, W.GetWindow().content)
    local x = 14 + c * (cw + 10)
    meth(f, "SetSize", cw, ch)
    local r, g, b = bandColor(d.min)
    gradient(f.bg, r, g, b)
    local hot = (planned == d) or (cur == d)
    W._border(f, W._tok(hot and "gold" or "borderSoft", 0.78, 0.62, 0.28, hot and 1 or 0.5))
    meth(f.icon, "SetTexture", ICON .. (DG_ICON[d.key] or "INV_Misc_Key_03"))
    meth(f.name, "SetWidth", cw - 62)
    meth(f.name, "SetText", d.n)
    local q = dungeonQuests(D, d)
    local di = info[d]
    local qtxt = questText(D, d, q, di)
    meth(f.q, "SetText", qtxt)
    if di and di.done then meth(f.check, "Show") else meth(f.check, "Hide") end
    -- (JOURNAL) « Fait le 3 nov. » en vert ; « Fait en partie : 2/5 boss » / « Verrou : 2/5 boss » en doré
    meth(f.done, "SetWidth", cw - 62)
    meth(f.done, "SetText", (di and di.label) and ((di.done and "|cff33dd33" or "|cffd8b04a") .. di.label .. "|r") or "")
    meth(f.lv, "SetText", fmt("Niv. %d-%d", d.min or 1, d.max or 60))
    local tags = {}
    if cur == d then tags[#tags + 1] = "|cff33dd33tu y es|r"
    elseif planned == d then tags[#tags + 1] = "|cffffd100prévu|r" end
    if d.new then tags[#tags + 1] = "propre à Forever" end
    if d.faction then tags[#tags + 1] = d.faction end
    -- la ligne du bas : niveau · étiquettes à gauche, quêtes (+ coche) à droite, sans se toucher
    local _, qn = tostring(qtxt):gsub("[^\128-\191]", "")
    meth(f.tag, "SetWidth", max(40, cw - 72 - 10 - floor(qn * 5.4) - 24))
    meth(f.tag, "SetWordWrap", false)
    meth(f.tag, "SetText", tconcat(tags, " · "))
    f.key, f.label = d.key, d.n
    local qline
    if di and (di.qTotal or 0) > 0 then
      qline = fmt("Quêtes : %d/%d faites (selon le jeu), %d dans ton journal", di.qDone or 0, di.qTotal, di.qInLog or 0)
    elseif q.total > 0 then qline = fmt("Quêtes : %d dans ton journal, %d à prendre", q.inLog, q.total - q.inLog)
    else qline = qtxt ~= "" and qtxt or "Aucune quête connue" end
    f.tipL = { fmt("Niveaux %d à %d · %d boss", d.min or 1, d.max or 60, #(d.bosses or {})), qline }
    if di and di.label then
      f.tipL[#f.tipL + 1] = di.label .. ((di.done and DONE_SRC[di.src]) and (" : " .. DONE_SRC[di.src]) or "")
    end
    Ly.at(f, x, Ly.y)
    Ly.rec("dcard", x, Ly.y, cw, ch, d.n)
  end
  Ly.y = Ly.y + ch + 10
end

local function renderDungeonDetail(Ly, D, d)
  Ly.buttons({ { "< Tous les donjons", function() if W.char then W.char.dungeon = nil end W.Render() end, "Clic : revenir à la liste" } }, 1)
  local members = (D.Members and select(2, pcall(D.Members))) or {}
  local q = dungeonQuests(D, d)
  local planned = try(D.Planned)
  local di = doneInfo(D, d)
  Ly.hero(ICON .. (DG_ICON[d.key] or "INV_Misc_Key_03"), d.n,
    fmt("Niv. %d-%d · %d boss%s", d.min or 1, d.max or 60, #(d.bosses or {}), d.faction and (" · " .. d.faction) or ""),
    planned == d and "Donjon prévu : la préparation est dans « Prêt ? »" or (di and di.label)
      or (d.new and "Donjon propre à WoW Forever" or nil))
  -- quêtes
  local res = q.res or {}
  local nq = #(res.inLog or {}) + #(res.toGet or {})
  if Ly.header("Quêtes du donjon", "dg:quests", (di and (di.qTotal or 0) > 0) and di.qTotal or nq, W.MARK.pick) then
    -- (JOURNAL) les faits du jeu d'abord : combien sont déjà rendues
    if di and (di.qTotal or 0) > 0 then
      Ly.text(fmt("Faites : %d/%d (selon le jeu) · dans ton journal : %d", di.qDone or 0, di.qTotal, di.qInLog or 0), "S", "text")
    end
    if nq == 0 then Ly.text(res.pending and "Lecture des quêtes du donjon…" or
      ((di and (di.qTotal or 0) > 0 and (di.qDone or 0) >= di.qTotal) and "Toutes ses quêtes sont faites."
        or "Rien à prendre pour toi ici (pas de QuestieDB, ou rien à ton niveau)."), "S", "muted") end
    for _, e in ipairs(res.inLog or {}) do
      local sub
      if e.done then sub = "finie · à rendre"
      else
        local parts = {}
        for _, o in ipairs(e.obj or {}) do
          parts[#parts + 1] = fmt("%s %d/%d%s", strip(o.name or ""), o.have or 0, o.need or 1, o.boss and (" (" .. o.boss.n .. ")") or "")
          if #parts >= 2 then break end
        end
        sub = #parts > 0 and tconcat(parts, " · ") or "dans ton journal"
      end
      if #(e.missing or {}) > 0 then sub = sub .. " · pas chez " .. tconcat(e.missing, ", ") end
      Ly.quest(nil, e.title, sub, openQuest(e), nil, QUEST_HINT)
    end
    for _, e in ipairs(res.toGet or {}) do
      local sub = "à prendre" .. (e.giver and (" · " .. e.giver) or "")
      if #(e.holders or {}) > 0 then sub = sub .. " · l'ont : " .. tconcat(e.holders, ", ") end
      Ly.quest(e.level, e.title, sub, nil, { "Niveau requis : " .. tostring(e.req or "?") })
    end
    if #(res.inLog or {}) > 0 and D.ShareQuests then
      Ly.buttons({ { "Partager les quêtes du donjon", function() D:ShareQuests() end,
        "Clic : propose tes quêtes du donjon à ceux qui ne les ont pas (hors combat)" } }, 1)
    end
  end
  -- boss
  local nb = #(d.bosses or {})
  if nb > 0 and Ly.header("Boss", "dg:boss", nb, ICON .. "INV_Misc_Bone_HumanSkull_01") then
    local ever = D.char and type(D.char.kills) == "table" and D.char.kills[d.key] or nil   -- (JOURNAL) historique
    for i, b in ipairs(d.bosses) do
      local killed = D.Killed and D.Killed(b)
      local tip = strip(b.tip or "")
      tip = T.Cut(tip, 110)
      local mark = (killed and "  |cff33dd33(tué)|r") or ((type(ever) == "table" and ever[i]) and "  |cff33dd33(déjà tué)|r") or ""
      Ly.step({ n = i, title = b.n .. mark, sub = tip }, false)
    end
  end
  -- butin pour le groupe (objets qui servent à quelqu'un)
  local okL, loot = pcall(D.LootPlan, d, members)
  local useful = {}
  for _, e in ipairs(okL and loot or {}) do if #(e.need or {}) > 0 then useful[#useful + 1] = e end end
  if #useful > 0 and Ly.header("Butin pour ton groupe", "dg:loot", #useful, ICON .. "INV_Chest_Chain") then
    for _, e in ipairs(useful) do
      if W.Match(e.name, e.boss and e.boss.n) then
        Ly.row({ text = fmt("%s · %s", e.name, D.LootVerdictText(e, members)), right = e.boss and e.boss.n or nil, kind = "gear",
          tooltip = "Butin d'après Classic : à vérifier en jeu." })
      end
    end
  end
  if d.tips and #d.tips > 0 and Ly.header("Conseils", "dg:tips", #d.tips, ICON .. "INV_Misc_Note_01", true) then
    for _, t in ipairs(d.tips) do Ly.text("• " .. t, "S", "text", 6) end
    if d.hunter then Ly.text("Chasseur : " .. d.hunter, "S", "muted", 6) end
  end
end

W.AddPage({ id = "dungeon", label = "Donjons", icon = W.TAB_ICON.dungeon, title = "Donjons",
  desc = "Journal des donjons : niveaux, quêtes, boss, butin pour ton groupe",
  sub = function()
    local D = T.Dungeon
    local d = W.char and W.char.dungeon and D and D.Find(W.char.dungeon)
    if d then return "Boss, butin et quêtes pour ton groupe" end
    return "Tes donjons à ton niveau (clic sur une carte : le détail)"
  end,
  render = function(Ly)
    local D = T.Dungeon
    if not (D and D.Index) then Ly.empty("Données des donjons absentes.") return end
    if not D._active then   -- le journal marche sans le module ; le suivi en donjon (prochain boss, bilan) non
      Ly.row({ text = "Suivi en donjon coupé (prochain boss, butin du groupe en direct, bilan) : fonction bêta.",
        kind = "info", onClick = function() T:SetModuleEnabled("Dungeon", true) end,
        clickHint = "Clic : allumer le module Donjon (sans /reload)" })
    end
    -- (4.0 QA) en instance : le donjon où tu es s'ouvre tout seul (une fois par entrée : « < Tous les donjons »
    -- reste possible) et le direct (prochain boss, qui fait quoi, objets de quête) passe AVANT la fiche
    local cur = W.InDungeon and W.InDungeon() and try(D.Current) or nil
    if cur and W.char and W._autoDg ~= cur.key then W._autoDg = cur.key; W.char.dungeon = cur.key end
    if not cur then W._autoDg = nil end
    local lines = T.GetTabLines and T:GetTabLines("dungeon") or {}
    local live = #lines > 0
    if live and cur and Ly.header("En direct", "dg:live", nil, W.TAB_ICON.dungeon) then W.RenderTab(Ly, "dungeon") end
    local d = W.char and W.char.dungeon and D.Find(W.char.dungeon)
    if d then renderDungeonDetail(Ly, D, d) else renderDungeonList(Ly, D) end
    -- hors instance (donjon prévu, bilan) : le direct sous la liste
    if live and not cur and Ly.header("En direct", "dg:live", nil, W.TAB_ICON.dungeon) then W.RenderTab(Ly, "dungeon") end
  end,
  action = function()
    local D = T.Dungeon
    if not D then return nil end
    if not D._active then
      return "Allumer « Donjon »", function() T:SetModuleEnabled("Dungeon", true) end, "Clic : allume le suivi en donjon (bêta)"
    end
    local d = W.char and W.char.dungeon and D.Find(W.char.dungeon)
    local planned = try(D.Planned)
    if d and planned ~= d then
      return "Prévoir ce donjon", function() D.SetPlanned(d.key) end, "Clic : TreeAI prépare ce donjon (groupe, quêtes, réparation…)"
    end
    if D.ToggleChecklist then
      return "Prêt ? (préparation)", function() D:ToggleChecklist(true) end, "Clic : la fenêtre « Prêt pour le donjon ? »"
    end
  end,
})
