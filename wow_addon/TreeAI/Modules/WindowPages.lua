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
local function openQuest(q)
  local Q = mod("Quests")
  return function() if Q and Q.OpenQuest then Q.OpenQuest(q) end end
end

W.AddPage({ id = "quests", label = "Quêtes", icon = W.TAB_ICON.quests, title = "Quêtes",
  desc = "L'histoire de la zone : étapes numérotées, quêtes à venir, journal",
  sub = function()
    local g = W.Guide(1)
    local s = W.GuideSummary(g)
    return s ~= "" and s or "Aucune quête en cours"
  end,
  render = function(Ly)
    local g = W.Guide(5, true)
    local Q = mod("Quests")
    local a = Q and Q.last
    local first = g.steps[1]
    local more = max(0, (g.count or 0) - 1)
    Ly.hero(first and first.icon or (ICON .. "INV_Misc_Book_09"), (g.paused and "Trajet en pause" or ("Histoire : " .. g.zone)),
      W.GuideSummary(g) ~= "" and W.GuideSummary(g) or "Aucune quête suivie ici",
      first and (first.title .. (more > 0 and fmt(" et %d autre%s arrêt%s", more, more > 1 and "s" or "", more > 1 and "s" or "") or "")) or nil,
      function() W.Select("route") end, { "Le trajet complet, arrêt par arrêt, est dans « Trajet »." }, "Clic : voir le trajet")
    if g.paused then
      local R = routeOn()
      Ly.empty("Trajet en pause : rien n'est guidé.", R and { "Reprendre le trajet", function() R:TogglePause() end,
        "Clic : la boussole et la carte reprennent tes étapes" } or nil)
    end
    local any = false
    for i, s in ipairs(g.steps) do
      if W.Match(s.title, s.sub) then Ly.step(s, i == 1); any = true end
    end
    -- quêtes à venir sur le trajet (couleur de niveau)
    for _, u in ipairs(g.upcoming) do
      if W.Match(u.title) then
        Ly.quest(u.level, u.title, "Plus tard · à prendre sur ton trajet", u.onClick)
        any = true
      end
    end
    if a then
      -- à rendre (hors des étapes déjà montrées)
      local turn = {}
      for _, q in ipairs(a.turnIn or {}) do if W.Match(q.title, q.zone) then turn[#turn + 1] = q end end
      if #turn > 0 and Ly.header("À rendre", "q:turn", #turn, W.MARK.turn) then
        for _, q in ipairs(turn) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, "Clic : ouvrir la quête") end
      end
      local here = {}
      for _, q in ipairs(a.here or {}) do if W.Match(q.title) then here[#here + 1] = q end end
      if #here > 0 and Ly.header("En cours ici", "q:here", #here, W.MARK.pick) then
        for _, q in ipairs(here) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, "Clic : ouvrir la quête") end
      end
      local grey = {}
      for _, q in ipairs(a.grey or {}) do if W.Match(q.title) then grey[#grey + 1] = q end end
      if #grey > 0 and Ly.header("Nettoyer mon journal", "q:clean", #grey, "Interface\\GossipFrame\\IncompleteQuestIcon", true) then
        for _, q in ipairs(grey) do
          Ly.quest(q.level, q.title, "grise : presque pas d'XP · ouvre-la pour l'abandonner", openQuest(q),
            { "L'abandon reste ton choix : TreeAI ouvre la quête, tu cliques « Abandonner »." }, "Clic : ouvrir la quête")
        end
      end
      -- ailleurs, par zone (plus de quêtes d'abord), repliés
      local zones = {}
      for _, q in ipairs(a.elsewhere or {}) do
        if not q.grey and W.Match(q.title, q.zone) then
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
          for _, q in ipairs(it.list) do Ly.quest(q.level, q.title, questProgress(q), openQuest(q), nil, "Clic : ouvrir la quête") end
        end
      end
      any = any or #turn > 0 or #here > 0 or #zl > 0
    end
    -- le reste de l'onglet « Quêtes » (objets de quête, conseils de zone…) sans le trajet déjà montré au-dessus
    local extra = {}
    for _, e in ipairs(T.GetTabLines and T:GetTabLines("quests") or {}) do
      if e.source ~= "plan" and not e.header and not e.control and W.Match(e.text, e.right) then extra[#extra + 1] = e end
    end
    if #extra > 0 and Ly.header("Autres conseils", "q:extra", #extra, ICON .. "INV_Misc_Note_01", true) then
      for _, e in ipairs(extra) do Ly.row(e) end
    end
    if not any and #extra == 0 then
      local Zp = T.ZonePlan
      Ly.empty(W.Query() ~= "" and ("Rien ne correspond à « " .. W.Query() .. " ».")
        or "Aucune quête à suivre ici. Parle aux PNJ marqués « ! », ou choisis une autre zone de quêtes.",
        (Zp and Zp.ShowZoneMenu and Zp.IsEnabled and Zp:IsEnabled()) and { "Choisir une zone", function() Zp:ShowZoneMenu() end,
          "Clic : la liste des zones à ton niveau" } or nil)
    end
  end,
  action = function()
    local R = routeOn()
    if not R then return nil end
    local paused = R.char and R.char.paused
    return paused and "Reprendre" or "Arrêter", function() R:TogglePause() end,
      paused and "Clic : reprendre le guidage du trajet" or "Clic : mettre le trajet en pause (plus de guidage)"
  end,
  status = function()
    local Q = mod("Quests")
    local n = Q and Q.lastList and #Q.lastList or 0
    local cap = tonumber(try(G.C_QuestLog and G.C_QuestLog.GetMaxNumQuestsCanAccept)) or 25
    return fmt("Journal : %d/%d", n, cap)
  end,
})

-- ===================================================================== Trajet (tous les arrêts, zone, choix)
W.AddPage({ id = "route", label = "Trajet", icon = W.TAB_ICON.route, title = "Trajet",
  desc = "Tous les arrêts dans l'ordre, la zone de travail, les vols",
  sub = function()
    local Zp = T.ZonePlan
    local A = Zp and Zp.IsEnabled and Zp:IsEnabled() and try(Zp.Allowed, Zp)
    return A and A.label and ("Zone de travail : " .. A.label) or "Le chemin le plus rapide entre tes étapes"
  end,
  render = function(Ly)
    local R, P = routeOn(), T.Planner
    if not (R and P and P.steps and P.cursor) then
      local Zp = T.ZonePlan
      Ly.empty("Pas encore de trajet. Prends des quêtes : TreeAI prépare l'ordre des arrêts tout seul.",
        (Zp and Zp.Recalc and Zp.IsEnabled and Zp:IsEnabled()) and { "Calculer le trajet", function() Zp:Recalc() end,
          "Clic : TreeAI range tes quêtes en arrêts" } or nil)
      return
    end
    local st = R:Stops(64) or {}
    local paused = R.char and R.char.paused
    local nQ = 0
    for _, e in ipairs(st) do if e.step and e.step.q then nQ = nQ + 1 end end
    local title = paused and "Trajet en pause" or fmt("Trajet %s · %s", W.MinutesText(st.total) or "< 1 min", km(st.yd))
    local route = P.route or {}
    local name = (T.Plain and T.Plain.RouteName and T.Plain.RouteName(route.id, route.title)) or route.title or "Trajet TreeAI"
    Ly.hero(W.TAB_ICON.route, title, fmt("%d arrêt%s · %s", #st, #st > 1 and "s" or "", strip(name)),
      (tonumber(st.travel) or 0) > 0 and ("dont " .. (W.MinutesText(st.travel) or "") .. " de déplacement") or nil,
      function() R:TogglePause() end, nil, paused and "Clic : reprendre" or "Clic : mettre en pause")
    -- zone de travail, détour, suivi (lignes du producteur, sans les arrêts déjà dessinés en dessous)
    local Zp = T.ZonePlan
    if Zp and Zp.HeadLines and Zp.IsEnabled and Zp:IsEnabled() then
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
    if Ly.header("Arrêts", nil, #st, W.TAB_ICON.route) then
      local shown = 0
      for k, e in ipairs(st) do
        if shown >= 30 then
          Ly.text(fmt("… et %d autres arrêts, affichés au fur et à mesure.", #st - k + 1), "S", "muted")
          break
        end
        local s = e.step
        local item
        if s then
          local lq = P.log and s.q and P.log[s.q]
          local o = lq and lq.obj and lq.obj[tonumber(s.o) or 0]
          local prog = (o and (o.n or 0) > 0) and fmt(" (%d/%d)", o.have or 0, o.n) or ""
          local sub = s.k == "pick" and "à prendre" or s.k == "turn" and "à rendre" or s.k == "obj" and "objectif" or "étape"
          if e.sec and e.sec >= 30 then sub = sub .. " · ≈ " .. (W.MinutesText(e.sec) or "") .. " sur place" end
          if s.out then sub = sub .. " · hors zone" end
          item = { n = k, title = strip(P:StepLabel(s)) .. prog, sub = sub, mark = W.MARK[s.k], kind = s.k,
            onClick = function() P:Navigate(s) end, actionText = "guider vers cet arrêt" }
          if Zp and Zp.StopMenu and Zp.IsEnabled and Zp:IsEnabled() then item.onRightClick = function() Zp:StopMenu(s) end end
        else
          local p = e.place
          item = { n = k, title = strip(e.text), sub = "trajet · " .. (W.MinutesText(e.sec) or "≈ 1 min"), kind = e.kind,
            onClick = p and p.map and function() P:Navigate({ k = "fly", map = p.map, x = p.x, y = p.y, label = e.text }) end or nil,
            actionText = "guider vers ce départ" }
        end
        if W.Match(item.title, item.sub) then Ly.step(item, k == 1); shown = shown + 1 end
      end
    end
  end,
  action = function()
    local R = routeOn()
    if not R then return nil end
    local paused = R.char and R.char.paused
    if not paused and R.Following and T.ExternalGuide and T:ExternalGuide() == "spf" then
      return "Suivre (Suivi Forever)", function() R:Follow() end, "Clic : envoyer les arrêts dans l'ordre à Shortest Path Forever"
    end
    return paused and "Reprendre" or "Arrêter", function() R:TogglePause() end,
      paused and "Clic : reprendre le guidage" or "Clic : mettre le trajet en pause"
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
  cardPool = W._newPool(function(parent)
    local f = W._newButton(parent)
    f.icon = meth(f, "CreateTexture", nil, "ARTWORK")
    meth(f.icon, "SetSize", 34, 34)
    meth(f.icon, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -10)
    meth(f.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92)
    f.name = W._text(f, "L", "gold")
    meth(f.name, "SetPoint", "TOPLEFT", f, "TOPLEFT", 52, -10)
    meth(f.name, "SetWordWrap", false)
    f.q = W._text(f, "S", "text")
    meth(f.q, "SetPoint", "TOPRIGHT", f, "TOPRIGHT", -10, -12)
    meth(f.q, "SetJustifyH", "RIGHT")
    f.check = meth(f, "CreateTexture", nil, "OVERLAY")
    meth(f.check, "SetSize", 14, 14)
    meth(f.check, "SetTexture", "Interface\\RaidFrame\\ReadyCheck-Ready")
    meth(f.check, "SetPoint", "RIGHT", f.q, "LEFT", -3, 0)
    f.lv = W._text(f, "S", "text")
    meth(f.lv, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 10, 8)
    f.tag = W._text(f, "S", "muted")
    meth(f.tag, "SetPoint", "BOTTOMRIGHT", f, "BOTTOMRIGHT", -10, 8)
    meth(f.tag, "SetJustifyH", "RIGHT")
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
    meth(f.name, "SetWidth", cw - 150)
    meth(f.name, "SetText", d.n)
    local q = dungeonQuests(D, d)
    local qtxt
    if q.pending and q.total == 0 then qtxt = "quêtes…"
    elseif q.inLog > 0 then qtxt = fmt("%d/%d quête%s", q.done, q.total, q.total > 1 and "s" or "")
    else qtxt = fmt("%d quête%s", q.total, q.total > 1 and "s" or "") end
    meth(f.q, "SetText", qtxt)
    if q.total > 0 and q.done == q.total then meth(f.check, "Show") else meth(f.check, "Hide") end
    meth(f.lv, "SetText", fmt("Niv. %d-%d", d.min or 1, d.max or 60))
    local tags = {}
    if cur == d then tags[#tags + 1] = "|cff33dd33tu y es|r"
    elseif planned == d then tags[#tags + 1] = "|cffffd100prévu|r" end
    if d.new then tags[#tags + 1] = "propre à Forever" end
    if d.faction then tags[#tags + 1] = d.faction end
    meth(f.tag, "SetWidth", max(40, cw - 96))
    meth(f.tag, "SetWordWrap", false)
    meth(f.tag, "SetText", tconcat(tags, " · "))
    f.key, f.label = d.key, d.n
    f.tipL = { fmt("Niveaux %d à %d · %d boss", d.min or 1, d.max or 60, #(d.bosses or {})),
      (q.total > 0) and fmt("Quêtes : %d dans ton journal, %d à prendre", q.inLog, q.total - q.inLog) or "Aucune quête connue" }
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
  Ly.hero(ICON .. (DG_ICON[d.key] or "INV_Misc_Key_03"), d.n,
    fmt("Niv. %d-%d · %d boss%s", d.min or 1, d.max or 60, #(d.bosses or {}), d.faction and (" · " .. d.faction) or ""),
    planned == d and "Donjon prévu : la préparation est dans « Prêt ? »" or (d.new and "Donjon propre à WoW Forever" or nil))
  -- quêtes
  local res = q.res or {}
  local nq = #(res.inLog or {}) + #(res.toGet or {})
  if Ly.header(fmt("Quêtes (%d/%d)", q.done, nq), "dg:quests", nil, W.MARK.pick) then
    if nq == 0 then Ly.text(res.pending and "Lecture des quêtes du donjon…" or
      "Aucune quête connue pour ce donjon (pas de QuestieDB, ou rien à ton niveau).", "S", "muted") end
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
      local QM = mod("Quests")
      Ly.quest(nil, e.title, sub, function() if QM and QM.OpenQuest then QM.OpenQuest(e) end end, nil, "Clic : ouvrir la quête")
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
    for i, b in ipairs(d.bosses) do
      local killed = D.Killed and D.Killed(b)
      local tip = strip(b.tip or "")
      if #tip > 110 then tip = tip:sub(1, 107):gsub("[\128-\191]*$", "") .. "…" end
      Ly.step({ n = i, title = b.n .. (killed and "  |cff33dd33(tué)|r" or ""), sub = tip }, false)
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
    local d = W.char and W.char.dungeon and D.Find(W.char.dungeon)
    if d then renderDungeonDetail(Ly, D, d) else renderDungeonList(Ly, D) end
    -- en instance / donjon prévu : l'onglet « Donjon » en direct (prochain boss, minuteurs, bilan)
    local lines = T.GetTabLines and T:GetTabLines("dungeon") or {}
    if #lines > 0 and Ly.header("En direct", "dg:live", nil, W.TAB_ICON.dungeon) then W.RenderTab(Ly, "dungeon") end
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
