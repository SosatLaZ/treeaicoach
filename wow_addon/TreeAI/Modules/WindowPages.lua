-- TreeAI — pages « Quêtes », « Trajet » et « Donjons » de la fenêtre TreeAI (agent UIREDO, extension du module
-- Window : même module, même coût mesuré). Rendu seulement : les données viennent de Planner / Route (étapes,
-- arrêts), Quests (journal analysé : ici, à rendre, gris, par zone), ZonePlan (zone de travail), Dungeon
-- (donjons, quêtes, boss, butin du groupe). Rien n'est recalculé ici ; fenêtre fermée = rien ne tourne.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
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
        (Zp and Zp.ShowZoneMenu and Zp.IsEnabled and Zp:IsEnabled()) and { "Choisir ma zone", function()
          if W.OpenZonePicker then W.OpenZonePicker(true) else Zp:ShowZoneMenu() end end,
          "Clic : toutes les zones pour toi, classées" } or nil)
    end
    return any or #extra > 0
end

-- (ZONEFIX) « Zones à faire » : tes zones à ton niveau (niveaux des quêtes, à prendre / en cours, trajet pour y aller,
-- « déjà finie »), un clic = travailler dans cette zone (le trajet est refait tout de suite)
function W.ZonesToDo(Ly)
  local Zp = T.ZonePlan
  if not (Zp and Zp.ZonesToDo and Zp.IsEnabled and Zp:IsEnabled()) then return end
  -- (ZONEPICK) les mêmes zones, le même classement que « Choisir ma zone » (comptes honnêtes) : les 3 premières + la liste
  local B = T.QuestBrain
  if type(B) == "table" and type(B.ZoneRows) == "function" and not (B.IsEnabled and not B:IsEnabled()) and W.OpenZonePicker then
    local ok, rows = pcall(B.ZoneRows, { limit = 3 })
    if not (ok and type(rows) == "table") then return end
    local shown = {}
    for _, r in ipairs(rows) do if W.Match(r.name) then shown[#shown + 1] = r end end
    if Ly.header("Zones à faire", "route:zones", #shown, ICON .. "INV_Misc_Map_01") then
      if rows.busy then Ly.text("Comptage des quêtes de chaque zone…", "S", "muted") end
      for _, r in ipairs(shown) do
        local map, name = r.map, r.name
        Ly.row({ text = (r.on and "● " or "") .. name .. " · " .. B.ZoneCountText(r), right = r.travel,
          kind = r.total > 0 and "quest" or "info", tooltip = B.ZoneTip and B.ZoneTip(r) or nil,
          clickHint = r.on and "Déjà ta zone de travail" or "Clic : travailler dans cette zone (trajet refait tout de suite)",
          onClick = (not r.on) and function() B.PickZone(map) if W.Flash then W.Flash("Zone : " .. name .. " — le trajet y va", 8) end end or nil })
      end
      Ly.buttons({ { "Toutes les zones…", function() W.OpenZonePicker(true) end, "Choisir ma zone : toutes, classées, avec filtres" } }, 2)
    end
    return
  end
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
--- compact (page Quêtes) : la carte du trajet et ses réglages ; les arrêts sont sur la page Trajet (4.3 V43)
function W.RenderRoute(Ly, compact)
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
    -- (ZONEPICK) « Trajet prévu : 54 min · 3,2 km » : la durée du trajet préparé (pas « finir la zone en 54 min »)
    local title = paused and "Trajet en pause" or fmt("Trajet prévu : %s · %s", W.MinutesText(st.total) or "< 1 min", km(st.yd))
    local route = P.route or {}
    local Zp = T.ZonePlan
    local zon = Zp and Zp.IsEnabled and Zp:IsEnabled()
    -- (ZONEFIX) le mode du trajet (pas « Le plus rapide » pour tous) et le thème en cours en sous-titre
    local name = (zon and Zp.StyleName and Zp:StyleName())
      or (T.Plain and T.Plain.RouteName and T.Plain.RouteName(route.id, route.title)) or route.title or "Trajet TreeAI"
    local th = zon and Zp.ThemeParts and select(2, pcall(Zp.ThemeParts, Zp)) or nil
    local travel = (tonumber(st.travel) or 0) > 0 and ("dont " .. (W.MinutesText(st.travel) or "") .. " de déplacement") or nil
    -- (ZONEPICK) « 12 arrêts · ordre : Finir la zone » / « 6 arrêts · mode : Rendre mes quêtes » : ce qu'est ce nom
    local bmode = zon and Zp.BrainMode and Zp.BrainMode() or "zone"
    local line2 = fmt("%d arrêt%s · %s : %s", nStops, nStops > 1 and "s" or "", (zon and bmode == "zone") and "ordre" or "mode", strip(name))
    if type(th) == "table" and th.full then
      Ly.hero(W.TAB_ICON.route, title, th.full, line2 .. (travel and (" · " .. travel) or ""),
        function() R:TogglePause() end, nil, paused and "Clic : reprendre" or "Clic : mettre en pause")
    else
      Ly.hero(W.TAB_ICON.route, title, line2, travel, function() R:TogglePause() end, nil,
        paused and "Clic : reprendre" or "Clic : mettre en pause")
    end
    -- mode, zone de travail, autres zones, détour, suivi (lignes du producteur, sans les arrêts dessinés en dessous)
    local headSeen = {}   -- (4.1 INTEG) les lignes du producteur reviennent dans PlanLines : une seule fois
    if Zp and Zp.HeadLines and zon then
      local ok, zl = pcall(Zp.HeadLines, Zp)
      -- (ZONEPICK) sur la page Quêtes : « Que faire : … ▾ » = les 4 tuiles du haut et « Zone : … ▾ » = la ligne « Zone »
      -- du haut (une seule entrée) → pas répétés ; « Mode : Finir la zone » dit ce qu'il est : l'ordre des arrêts
      local dup = { ["qb:mode"] = W.ModeTilesShown and true or nil, ["zone:pick"] = W.ZoneBarShown and true or nil }
      for _, l in ipairs(ok and type(zl) == "table" and zl or {}) do
        if l.text then
          headSeen[l.id or strip(l.text)] = true
          if not dup[l.id or ""] then
            local txt = l.text
            if l.id == "zone:mode" then txt = (tostring(txt):gsub("^Mode : ", "Ordre des arrêts : ")) end
            local b = Ly.row({ id = l.id, text = txt, prio = l.prio, icon = l.icon or W.TAB_ICON.route, tooltip = l.tooltip,
              onClick = l.onClick, onRightClick = l.onRightClick })
            if l.id == "zone:pick" then W.zoneRow = b end   -- (ONBOARD) cible « zone » de la visite guidée
          end
        end
      end
    end
    local plan = R.PlanLines and select(2, pcall(R.PlanLines, R, P)) or nil
    for _, l in ipairs(type(plan) == "table" and plan or {}) do
      local t = strip(l.text)
      if l.id ~= "route:head" and l.id ~= "plan:step" and not t:find("^Arrêt %d") and not t:find("^… %d") and not t:find("^Zone")
          and l.onClick and not headSeen[l.id or t] then
        Ly.row({ text = l.text, prio = l.prio, tooltip = l.tooltip, onClick = l.onClick, onRightClick = l.onRightClick })
      end
    end
    if paused then
      Ly.empty("Le trajet est en pause : rien n'est guidé.", { "Reprendre le trajet", function() R:TogglePause() end,
        "Clic : la boussole et la carte reprennent tes étapes" })
      return
    end
    if compact and W.Query() == "" then
      Ly.buttons({ { fmt("Voir les %d arrêts", nStops), function() W.Select("route") end,
        "Clic : la page Trajet (tous les arrêts dans l'ordre, les zones à faire)" } }, 2)
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
        -- (4.1 INTEG) un arrêt = une ligne (même quête au même endroit, même PNJ) : le titre de l'arrêt entier
        -- (Route.StopLabel), comme la carte et la boussole — plus deux lignes pour le même lieu
        local sameStop = grp and k > 1 and e.step and st[k - 1].step and grp[k] == grp[k - 1]
        if not sameStop then
          local item = W.StopItem(P, e, k, st, grp, via, k)
          if e.step and grp and grp[k + 1] == grp[k] and type(R.StopLabel) == "function" then
            local okL, gl = pcall(R.StopLabel, P, st, k, grp)
            if okL and type(gl) == "string" then item.title = strip(gl) end
          end
          if W.Match(item.title, item.sub) then Ly.step(item, k == 1); shown = shown + 1 end
        end
        if e.kind and e.kind ~= "quest" then via = true end
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
-- (5.0 LOOK) les 3 modes de tous les jours = les 3 grosses tuiles d'action en tête de la page Quêtes
W.TOP_MODES = { "turnin", "pickup", "zone" }
local TOP_ICON = { turnin = "Interface\\GossipFrame\\ActiveQuestIcon", pickup = "Interface\\GossipFrame\\AvailableQuestIcon",
  zone = ICON .. "INV_Misc_Map_01" }
--- (5.0) les tuiles d'action de la page Quêtes : { { titre, fn, infobulle, icon, sub, count, active } } (≤ 3)
function W.QuestTop()
  local B = QB()
  if not B then return {} end
  local ok, list = pcall(B.Modes)
  if not (ok and type(list) == "table") then return {} end
  local by = {}
  for _, m in ipairs(list) do if type(m) == "table" and m.id then by[m.id] = m end end
  local Sc = T.Shortcuts
  local off = type(Sc) == "table" and type(Sc.GuideOn) == "function" and not Sc.GuideOn()
  -- (5.1) UNE action principale : celle que TreeAI conseille maintenant (lue dans le jeu), sinon aucune n'est mise en avant
  local MODE_OF = { rendre = "turnin", prendre = "pickup", zone = "zone" }
  local nx = type(Sc) == "table" and type(Sc.NextAction) == "function" and select(2, pcall(Sc.NextAction)) or nil
  local want = type(nx) == "table" and MODE_OF[nx.id or ""] or nil
  local out = {}
  for _, id in ipairs(W.TOP_MODES) do
    local m = by[id]
    if m then
      local n = tonumber(m.count) or 0
      local cw = MODE_COUNT[id] or { "%d", "0" }
      local sub = n > 0 and fmt(cw[1], n) or cw[2]
      local key = m.key and (W.KeyText and W.KeyText(m.key) or m.key)
      if key then sub = sub .. " · " .. key end
      out[#out + 1] = { m.label or m.short or id, function() pcall(B.SetMode, id) end,
        off and "Guidage des quêtes coupé : rallume-le d'abord (la case juste dessous)"
          or (m.active and "Mode en cours" or "Clic : passer à ce mode (le trajet suit tout de suite)"),
        icon = TOP_ICON[id], sub = sub, count = (id ~= "zone") and n or nil, active = m.active and not off, disabled = off,
        primary = (want == id and not off) or false }
    end
  end
  return out
end
--- les tuiles de mode ; skip = { id = true } : sans ces modes (la page Quêtes met les 3 premiers en tuiles d'action)
function W.ModeTiles(Ly, skip)
  local B = QB()
  if not B then return false end
  local ok, list = pcall(B.Modes)
  if not (ok and type(list) == "table" and #list > 0) then return false end
  local tiles = {}
  for _, m in ipairs(list) do
    if type(m) == "table" and m.id and not (skip and skip[m.id]) then
      local n = tonumber(m.count) or 0
      local cw = MODE_COUNT[m.id] or { "%d", "0" }
      local sub = n > 0 and fmt(cw[1], n) or cw[2]
      local key = m.key and (W.KeyText and W.KeyText(m.key) or m.key)
      if key then sub = sub .. " · touche " .. key end
      if m.active then sub = "● " .. sub end
      local id = m.id
      tiles[#tiles + 1] = { m.label or m.short or id, sub, function() pcall(B.SetMode, id) end,
        { m.tip, key and ("Touche : " .. key) or "Pas de touche : Réglages → Touches → « Touches conseillées »" },
        m.active and true or false, m.active and "Mode en cours" or "Clic : passer à ce mode (le trajet suit tout de suite)" }
    end
  end
  if #tiles == 0 then return false end
  Ly.tiles(tiles, 2)
  return true
end
--- (5.1 LOOK) pas d'impasse : une étape sans clic reçoit « y aller » (la cible en cours de la boussole, confiée à
--- Suivi Forever / au repère du jeu quand ils tiennent la flèche)
function W.StepGo(st)
  if type(st) ~= "table" or st.onClick then return st end
  local C, A = T.Compass, T.Addons
  if not (type(C) == "table" and type(C.Current) == "function" and type(A) == "table" and type(A.GoTo) == "function") then return st end
  st.onClick = function()
    local cur = select(2, pcall(C.Current))
    if type(cur) == "table" and cur.mapID and cur.x and cur.y then A.GoTo(cur)
    elseif type(C.FollowRoute) == "function" then pcall(C.FollowRoute) end
  end
  st.actionText = "y aller (" .. tostring(A.GoHow and A.GoHow() or "la boussole t'y mène") .. ")"
  return st
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
    -- (4.1 INTEG) le texte entier de la cible (la boussole le coupe à 40 lettres faute de place), sa distance telle
    -- qu'elle est écrite sur la boussole
    local cur = Cm.Current and select(2, pcall(Cm.Current))
    local full = type(cur) == "table" and (cur.label or cur.title) or nil
    local lbl = strip(type(full) == "string" and full or meth(wd.label, "GetText") or "")
    local dst = strip(meth(wd.dist, "GetText") or ""):gsub("%s+", " ")
    if lbl ~= "" then return W.StepGo({ n = 1, title = lbl, sub = dst }) end
  end
  local g = W.Guide(1)
  local st = g and g.steps and g.steps[1]
  if st and not st.entry then return W.StepGo(st) end
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
      Ly.quest((tonumber(e.level) or 0) > 0 and e.level or nil, e.title, tconcat(sub, " · "), function()   -- (sans Questie : niveau inconnu, jamais « [0] »)
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
-- ===================================================================== (ZONEPICK 4.1) « Choisir ma zone » + « Conseille-moi »
-- UNE entrée : la ligne « Zone : … · Changer » en haut de la page Quêtes, le bouton « Zone » de la bande, /tai zone.
-- Le choix s'ouvre DANS la page Quêtes (même fenêtre, même style) : filtres en pastilles, « Automatique », puis chaque zone
-- en une ligne (nom, niveaux, « 12 quêtes pour toi (3 finies à rendre) », trajet réel, XP/h, groupe, pourquoi), la
-- conseillée en tête avec une étoile. Un clic = le trajet y va (choix gardé, le pilote auto n'y touche plus).
-- « Conseille-moi » : 3 conseils au plus, chacun s'applique d'un clic. Rien dans le chat (le pied de la fenêtre le dit).
local STAR = "|TInterface\\TargetingFrame\\UI-RaidTargetingIcon_1:13:13:0:0|t "
local function zpBrain()
  local B = QB()
  if B and type(B.ZoneRows) == "function" then return B end
end
local hooked = false
--- ouvre (on ≠ false) ou ferme le choix de zone, sur la page Quêtes de la fenêtre
function W.OpenZonePicker(on)
  W.zonePick = on ~= false
  if W.zonePick then
    local B = zpBrain()
    if B and B.ZoneScan then pcall(B.ZoneScan) end      -- le comptage démarre tout de suite (découpé)
    if not hooked then                                  -- fenêtre fermée = choix refermé (il ne revient pas tout seul)
      local win = W.GetWindow and W.GetWindow()
      if win and type(win.HookScript) == "function" then
        hooked = pcall(win.HookScript, win, "OnHide", function() W.zonePick, W.zoneAdvice = false, false end)
      end
    end
    if W.IsOpen() and W.CurrentPage() == "quests" then W.Render() else W.Open("quests") end
  elseif W.IsOpen() then W.Render() end
  return W.zonePick
end
--- (bande « Zone », /tai zone) : ouvre le choix ; déjà ouvert à l'écran → le referme
function W.ToggleZonePicker()
  local shown = W.zonePick and W.IsOpen() and W.CurrentPage() == "quests"
  return W.OpenZonePicker(not shown)
end
--- « Conseille-moi » : le bloc de conseils en haut de la page Quêtes (on = false : le masquer)
function W.ShowAdvice(on)
  W.zoneAdvice = on ~= false
  if W.zoneAdvice then W.zonePick = false end
  if W.IsOpen() and W.CurrentPage() == "quests" then W.Render() else W.Open("quests") end
  return W.zoneAdvice
end
local function zpDone(msg)
  W.zonePick, W.zoneAdvice = false, false
  if msg and W.Flash then W.Flash(msg, 8) end
  if W.IsOpen() then W.Render() end
end
--- le bloc « Conseil » : 3 lignes au plus, un clic applique
function W.AdviceRows(Ly)
  local B = zpBrain()
  if not (B and B.Advice) then return false end
  local ok, list = pcall(B.Advice)
  if not (ok and type(list) == "table") then return false end
  Ly.header("Conseil : où aller maintenant", nil, #list, ICON .. "INV_Misc_Note_01")
  if list.est then Ly.text("(sans Questie : estimation)", "S", "muted") end
  for i, a in ipairs(list) do
    local fn = a.apply
    Ly.row({ id = "zp:advice:" .. tostring(a.id or i), text = fmt("%d. %s", i, a.text) .. (a.why and ("\n|cffa0a0a0" .. a.why .. "|r") or ""),
      right = a.label, kind = fn and "quest" or "info", prio = i == 1 and 2 or 1,
      tooltip = { a.why or "" }, clickHint = fn and ("Clic : " .. (a.label or "appliquer")) or nil,
      onClick = fn and function()
        fn()
        zpDone("Conseil appliqué : " .. strip(a.text))
      end or nil })
  end
  return true
end
--- la ligne « Zone » du haut de la page Quêtes : la zone de travail, qui l'a choisie, « Changer » ; + « Conseille-moi »
function W.ZoneBar(Ly)
  local B = zpBrain()
  local zn = B and B.ZoneNow and select(2, pcall(B.ZoneNow)) or nil
  if type(zn) ~= "table" then return false end
  local b = Ly.row({ id = "zone:pick", text = "Zone : " .. strip(zn.label) .. " · " .. zn.who, right = "Changer",
    kind = "quest", icon = ICON .. "INV_Misc_Map_01",
    tooltip = { "Ta zone de quêtes : le trajet, la boussole et la bande la suivent.",
      "Changer : chaque zone qui a du sens pour toi, classée, avec le trajet et l'XP/h." },
    clickHint = "Clic : choisir ma zone", onClick = function() W.OpenZonePicker(true) end })
  W.zoneRow = b                                        -- (ONBOARD) cible « zone » de la visite guidée
  Ly.buttons({ { "Choisir ma zone", function() W.OpenZonePicker(true) end, "Toutes les zones pour toi, classées (/tai zone)" },
    { W.zoneAdvice and "Masquer le conseil" or "Conseille-moi", function() W.ShowAdvice(not W.zoneAdvice) end,
      "Où aller maintenant et pourquoi (3 lignes, un clic pour appliquer)" } }, 2)
  if W.zoneAdvice then W.AdviceRows(Ly) end
  return true
end
--- le choix de zone (page Quêtes, à la place du reste tant qu'il est ouvert)
function W.RenderZonePicker(Ly)
  local B = zpBrain()
  if not B then W.zonePick = false return false end
  local ch = B.char or {}
  Ly.header("Choisir ma zone", nil, nil, ICON .. "INV_Misc_Map_01")
  local zn = B.ZoneNow and select(2, pcall(B.ZoneNow)) or nil
  if type(zn) == "table" then Ly.text("Maintenant : " .. strip(zn.label) .. " · " .. zn.who, "S", "muted") end
  local chips = {}
  for _, s0 in ipairs(B.ZONE_SORTS or {}) do
    local id = s0.id
    chips[#chips + 1] = { ((ch.zpSort or "moi") == id and "● " or "") .. s0.label, function() B.SetZoneSort(id) W.Render() end, s0.tip }
  end
  chips[#chips + 1] = { (ch.zpNoElite and "● " or "") .. "Pas d'élites", function() B.ToggleNoElite() W.Render() end,
    "Ne compte pas les quêtes élites (clic : remettre)" }
  Ly.buttons(chips, 4)
  local ok, rows = pcall(B.ZoneRows)
  if not (ok and type(rows) == "table") then rows = {} end
  if rows.est then Ly.text("(sans Questie : estimation)", "S", "muted") end
  if rows.busy then Ly.text("Comptage des quêtes de chaque zone…", "S", "muted") end
  local auto = type(zn) == "table" and zn.by == "auto"
  Ly.row({ id = "zp:auto", text = (auto and "● " or "") .. "Automatique — TreeAI suit ta zone et passe à la suivante quand elle est finie",
    kind = auto and "info" or "quest", icon = ICON .. "INV_Misc_Gear_01",
    tooltip = { "Le trajet reste dans la zone où tu es (les voisines quand elle est presque vide).",
      "Le pilote auto choisit la zone suivante quand ton trajet n'a plus rien." },
    clickHint = auto and "Déjà en automatique" or "Clic : laisser TreeAI choisir",
    onClick = function() B.ZoneAuto() zpDone("Zone : automatique — TreeAI suit ta zone") end })
  if #rows == 0 and not rows.busy then
    Ly.text(B.HasQuestie() and "Aucune zone à ton niveau trouvée." or
      "Sans Questie, TreeAI ne connaît que les quêtes des PNJ à qui tu as parlé : parle aux PNJ « ! ».", "S", "muted")
  end
  for _, r in ipairs(rows) do
    local l1 = (r.star and (STAR .. "Conseillé — ") or "") .. (r.on and "● " or "") .. r.name
      .. ((r.lo and r.hi) and (r.lo == r.hi and fmt(" · niv %d", r.lo) or fmt(" · niv %d-%d", r.lo, r.hi)) or "")
    local l2 = { B.ZoneCountText(r) }
    if (r.xph or 0) > 0 then l2[#l2 + 1] = "~" .. (T.Fmt and T.Fmt.Num and T.Fmt.Num(r.xph) or tostring(r.xph)) .. " XP/h" end
    if r.group then l2[#l2 + 1] = r.group end
    local map, name = r.map, r.name
    Ly.row({ id = "zp:" .. tostring(map), text = l1 .. "\n" .. tconcat(l2, " · ") .. "\n|cffa0a0a0" .. strip(r.why or "") .. "|r",
      right = r.travel, kind = (r.total > 0) and "quest" or "info", prio = r.star and 2 or 1,
      tooltip = B.ZoneTip and B.ZoneTip(r) or nil,
      clickHint = r.on and "Ta zone : clic = la garder (trajet refait)" or "Clic : aller dans cette zone (le trajet suit, choix gardé)",
      onClick = function()
        if B.PickZone(map) then zpDone("Zone : " .. name .. " — le trajet y va") end
      end })
  end
  Ly.buttons({ { "Retour aux quêtes", function() zpDone(nil) end, "Fermer le choix de zone" },
    { "Conseille-moi", function() W.ShowAdvice(true) end, "Où aller maintenant et pourquoi" } }, 2)
  return true
end
W.AddPage({ id = "quests", label = "Quêtes", icon = W.TAB_ICON.quests, title = "Quêtes",
  desc = "Rendre, prendre, tout faire ici, avec le groupe : la prochaine étape, le trajet, le journal",
  top = function() return W.QuestTop() end,
  -- (ZONEPICK) « Mode : Tout faire ici · Zone : Mille pointes · trajet prévu 54 min » : chaque nombre dit ce qu'il est
  sub = function()
    local B = QB()
    local lab = B and B.Label and select(2, pcall(B.Label))
    local parts = { type(lab) == "string" and ("Mode : " .. lab) or W.RouteSub() }
    local zn = B and B.ZoneNow and select(2, pcall(B.ZoneNow))
    if type(lab) == "string" and type(zn) == "table" and zn.label then parts[#parts + 1] = "Zone : " .. strip(zn.label) end
    local g = W.Guide(1)
    local m = g and W.MinutesText and tonumber(g.total) and g.total > 0 and W.MinutesText(g.total)
    if m then parts[#parts + 1] = "trajet prévu " .. m end
    return tconcat(parts, " · ")
  end,
  badge = function()
    local n = 0
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("now") or {}) do if (e.prio or 1) >= 3 and not e.ownPage then n = n + 1 end end
    return n > 0 and n or nil
  end,
  render = function(Ly)
    local ps = T.PlayerState and T:PlayerState() or {}
    local Ad = T.Addons
    -- (ZONEPICK) le choix de zone ouvert : lui seul (filtres, « Automatique », les zones classées)
    if W.zonePick and W.Query() == "" and W.RenderZonePicker(Ly) then return end
    -- (5.0 LOOK) en tête : les 3 tuiles d'action (Rendre · Prendre · Tout faire ici : W.QuestTop), puis UNE case
    -- « Guidage des quêtes » — tout le guidage de quêtes s'allume ou se coupe ici, d'un clic
    local Sc = T.Shortcuts
    local guideOn = true
    if type(Sc) == "table" and type(Sc.GuideOn) == "function" and type(Sc.SetGuide) == "function"
        and W.Match("guidage des quêtes suivi trajet boussole") then
      guideOn = Sc.GuideOn()
      Ly.toggle({ id = "quests:guide", label = "Guidage des quêtes", on = guideOn, combatOK = true,
        desc = guideOn and "Trajet, boussole et repères de quêtes allumés. Décoche : TreeAI te laisse jouer à ta façon."
          or "Coupé : plus de trajet, de boussole ni de repères de quêtes. Coche pour les retrouver.",
        set = function(v) Sc.SetGuide(v) end })
    end
    -- 1. prochaine étape (mort / donjon : la course au corps / le donjon d'abord, dans les actions)
    if guideOn and not (ps.dead or ps.ghost or W.InDungeon()) then
      local nx = W.NextItem()
      if nx and W.Match(nx.title, nx.sub) then
        Ly.header("Prochaine étape", nil, nil, ICON .. "INV_Misc_Book_09")
        Ly.step(nx, true)
      end
    end
    -- 2. la zone de travail (« Changer ») + « Conseille-moi », repliable
    W.ZoneBarShown = false
    if W.Query() == "" and zpBrain() and Ly.header("Ma zone", "quests:zone", nil, ICON .. "INV_Misc_Map_01") then
      W.ZoneBarShown = W.ZoneBar(Ly) or false
    end
    -- 3. les autres façons de jouer (Avec le groupe, Monter vite…), repliable ; sans les tuiles d'action : tous les modes
    local topN = #W.QuestTop()
    W.ModeTilesShown = false
    if QB() and W.Query() == "" then
      local skip = {}
      if topN > 0 then for _, id in ipairs(W.TOP_MODES) do skip[id] = true end end
      local n = 0
      for _, m in ipairs(select(2, pcall(QB().Modes)) or {}) do if type(m) == "table" and m.id and not skip[m.id] then n = n + 1 end end
      -- (5.1) les autres modes sont repliés d'office (un clic les déplie) ; la ligne « Que faire » du trajet ne
      -- revient pas pour autant (les modes sont sur la page, tuiles d'action comprises)
      if n > 0 then
        if Ly.header(topN > 0 and "Autres façons de jouer" or "Façons de jouer", "quests:modes", n, ICON .. "Spell_Holy_PrayerOfHealing", topN > 0) then
          W.ModeTilesShown = W.ModeTiles(Ly, skip)
        else W.ModeTilesShown = topN > 0 end
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
    -- (5.1) Suivi Forever / Adventure Guide tiennent déjà la liste des étapes et la flèche : pas de 2e liste ici
    -- (l'onglet « Trajet » garde l'ordre des arrêts de TreeAI pour qui le veut ; « Autres addons » rebranche tout)
    local defer = type(Ad) == "table" and type(Ad.Link) == "function" and (Ad.Link("spf", "arrow") or Ad.Link("agf", "guide"))
    W.RouteDeferred = defer and true or false
    if defer and W.Query() == "" then
      Ly.row({ id = "quests:defer", text = "Le trajet pas à pas et la flèche : dans Suivi Forever (TreeAI ne les répète pas).",
        right = "Trajet ›", kind = "travel", tooltip = { "TreeAI dit quelle quête faire et pourquoi ; Suivi Forever t'y mène.",
          "L'ordre des arrêts de TreeAI reste dans l'onglet « Trajet ». Tout se rebranche dans Réglages → Autres addons." },
        clickHint = "Clic : l'onglet « Trajet » (les arrêts de TreeAI)", onClick = function() W.Select("route") end })
    else
      W.RenderRoute(Ly, true)
    end
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

-- ===================================================================== (4.3 V43) page « Trajet » : tous les arrêts
W.AddPage({ id = "route", label = "Trajet", icon = W.TAB_ICON.route, title = "Trajet",
  desc = "Tous les arrêts dans l'ordre, la zone de travail, les zones à faire",
  sub = function() return W.RouteSub() end,
  top = function()
    local out = {}
    local Zp = T.ZonePlan
    if Zp and Zp.Recalc and Zp.IsEnabled and Zp:IsEnabled() then
      out[#out + 1] = { "Recalculer", function() Zp:Recalc() end, "Clic : TreeAI refait l'ordre des arrêts" }
    end
    out[#out + 1] = { "Choisir ma zone", function() W.OpenZonePicker(true) end,
      "Clic : la liste des zones (Automatique ou la tienne)" }
    out[#out + 1] = { "Conseille-moi", function() W.ShowAdvice(true) end, "Où aller maintenant et pourquoi" }
    return out
  end,
  render = function(Ly) W.RenderRoute(Ly) end,
  action = function() return W.RouteAction() end,
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
