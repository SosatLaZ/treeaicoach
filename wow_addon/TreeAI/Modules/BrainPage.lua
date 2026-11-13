-- TreeAI — page « Pilote auto » de la fenêtre TreeAI (agent BRAIN, 4.0.2) : ce que le pilote a décidé, quand, pourquoi,
-- et un bouton pour l'annuler. Rendu seulement (extension du module Window, comme JournalPage.lua) : les données
-- viennent de TreeAI.Brain (contexte, quêtes en pause, décisions, courses, règles).
--  * « En pause » : une ligne par quête (niveau en couleur, raison du jeu), bouton « Reprendre » (la quête revient au
--    suivi et au trajet ; ton choix gagne pour la session) ; l'abandon reste à la page « Journal » (2 clics) ;
--  * « Décisions » : l'heure, ce qui a été fait, pourquoi ; « Annuler » défait (zone, mode, pause, course) ;
--  * « Courses » (réparer, vendre, munitions, maître) : clic = la boussole vise le PNJ connu le plus proche ;
--  * « Ce que TreeAI voit » (repliée) : le contexte du moment ; « Règles » : chaque règle d'un clic, interrupteur général
--    en bas à droite ; page « Quêtes » : section « En pause » (repliée) qui renvoie ici.
-- Fenêtre fermée = rien ne tourne ; aucune fonction protégée ; jamais le chat.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
local W = T and T.Window
if not (W and W.AddPage) then return end
local G = _G
local fmt, floor = string.format, math.floor
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "BrainPage") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function strip(s) return (tostring(s or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")) end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function ucut(s, n) return (W.UCut and W.UCut(s, n)) or (T.Cut and T.Cut(s, n, "")) or tostring(s or "") end
local ICON = "Interface\\Icons\\"

local P = {}
T.BrainPage = P
P.ICON = ICON .. "INV_Misc_EngGizmos_01"
local function B() local b = T.Brain if type(b) == "table" and b.Context then return b end end
local function on() local b = B() return b and b._active and b.db and b.db.enabled ~= false or false end
local BTN_W, ROW_H = 96, 40
local function hhmm(at) return try(G.date, "%H:%M", tonumber(at) or 0) or "" end
local function thousands(n)
  local s = tostring(floor((tonumber(n) or 0) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^%s+", ""))
end

-- ===================================================================== une ligne : titre + sous-titre + bouton
local rowPool
local function browPool()
  if rowPool then return rowPool end
  rowPool = W._pool("brow", function(parent)
    local f = W._newButton(parent)
    f.t = W._text(f, "M", "text")
    meth(f.t, "SetPoint", "TOPLEFT", f, "TOPLEFT", 10, -5)
    meth(f.t, "SetWordWrap", false)
    f.s = W._text(f, "S", "muted")
    meth(f.s, "SetPoint", "TOPLEFT", f.t, "BOTTOMLEFT", 0, -3)
    meth(f.s, "SetWordWrap", false)
    local th = Th()
    local b = th and th.Button and th:Button(f, "", BTN_W, 22) or W._newButton(f)
    if not b.label then b.label = W._text(b, "S", "text"); meth(b.label, "SetPoint", "CENTER", b, "CENTER", 0, 0) end
    meth(b, "SetSize", BTN_W, 22)
    meth(b, "SetPoint", "RIGHT", f, "RIGHT", -8, 0)
    meth(b, "SetScript", "OnClick", function(self)
      if not self.fn then return end
      local msg
      T:UserAction(function() msg = self.fn() end)
      if type(msg) == "string" and msg ~= "" then
        if W.Flash then W.Flash(msg, 8) elseif T.ClickNote then T:ClickNote(msg) end
      end
      W.Render()
    end)
    meth(b, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.labelText or "", nil, self.tip) end)
    meth(b, "SetScript", "OnLeave", W.HideTooltip)
    f.btn = b
    meth(f, "SetScript", "OnEnter", function(self) W.Tooltip(self, self.titleText, self.tipL) end)
    meth(f, "SetScript", "OnLeave", W.HideTooltip)
    return f
  end)
  return rowPool
end
--- r = { title, sub, color = {r,g,b}, button, fn, tip, tipL, dim }
local function brow(Ly, r)
  local f = W._acquire(browPool(), W.GetWindow().content)
  local hasBtn = r.button and r.fn
  local tw = Ly.col - (hasBtn and (BTN_W + 28) or 20)
  meth(f.t, "SetWidth", tw); meth(f.s, "SetWidth", tw)
  meth(f.t, "SetText", ucut(strip(r.title), floor(tw / 6.2)))
  local c = r.color
  if c then meth(f.t, "SetTextColor", c[1], c[2], c[3]) else meth(f.t, "SetTextColor", W._tok(r.dim and "muted" or "text", 0.92, 0.92, 0.92)) end
  meth(f.s, "SetText", ucut(strip(r.sub or ""), floor(tw / 5.4)))
  meth(f, "SetSize", Ly.col, ROW_H)
  W._color(f.bg, W._tok("button", 0.14, 0.15, 0.19, 0.55))
  W._border(f, 0, 0, 0, 0)
  local b = f.btn
  b.fn, b.tip, b.labelText = r.fn, r.tip, r.button
  if hasBtn then
    if b.SetLabel then b:SetLabel(r.button) else meth(b.label, "SetText", r.button) end
    meth(b, "Show"); meth(b, "Enable")
  else
    meth(b, "Hide")
  end
  f.titleText, f.tipL = strip(r.title), r.tipL
  Ly.at(f, 16, Ly.y)
  Ly.rec("brow", 16, Ly.y, Ly.col, ROW_H, r.title)
  Ly.y = Ly.y + ROW_H + 2
  return f
end
P.Row = brow

-- ===================================================================== morceaux
local function qcolor(level)
  local th = Th()
  local pl = tonumber(try(G.UnitLevel, "player")) or 1
  if th and th.QuestDiffColor and tonumber(level) then return { th:QuestDiffColor(level, pl) } end
  return nil
end
local function parkedRows(Ly, list)
  local Bn = B()
  local strong = 0
  for _, p in ipairs(list) do
    if W.Match(p.n, p.t, p.why) then
      if p.strong then strong = strong + 1 end
      brow(Ly, { title = ((tonumber(p.lv) or 0) > 0 and ("[" .. p.lv .. "] ") or "") .. tostring(p.n),
        sub = p.why .. " · " .. tostring(p.t), color = qcolor(p.lv), button = "Reprendre",
        fn = function() if Bn.Resume(p.q) then return "« " .. strip(p.n) .. " » reprise : de nouveau suivie et au trajet." end end,
        tip = "Clic : la quête revient au suivi du jeu et au trajet ; le pilote ne la remet plus en pause de la session.",
        tipL = { p.why .. " : " .. tostring(p.t), p.advice, "En pause depuis " .. hhmm(p.at) .. ". Rien n'est abandonné." } })
    end
  end
  if strong > 0 then
    Ly.row({ text = fmt("Abandonner %s : page « Journal » (2 clics, hors combat)", strong > 1 and ("ces " .. strong .. " quêtes") or "cette quête"),
      kind = "info", onClick = function() W.Select("journal") end, clickHint = "Clic : la page « Journal »" })
  end
end
local KIND_TXT = { park = "Pause", unpark = "Reprise", zone = "Zone", style = "Mode", errand = "Course", hearth = "Foyer",
  follow = "Groupe", level = "Niveau", undo = "Annulé", off = "Pilote" }
local function decisionRows(Ly, list)
  local Bn = B()
  for _, e in ipairs(list) do
    if W.Match(e.t, e.w) then
      local can = type(e.u) == "table" and not e.undone
      brow(Ly, { title = hhmm(e.at) .. " · " .. tostring(e.t), sub = e.undone and "annulé" or (e.w or KIND_TXT[e.k] or ""),
        dim = e.undone, button = can and "Annuler" or nil,
        fn = can and function() if Bn.Undo(e.id) then return "Annulé : ton choix gagne jusqu'à la fin de la session." end end or nil,
        tip = "Clic : défaire cette décision ; le pilote ne la reprend pas de la session.",
        tipL = { tostring(e.t), e.w, e.undone and "Annulé." or nil } })
    end
  end
end
local function contextLines(ctx)
  local f = ctx.flags or {}
  local out = { "Situation : " .. tostring(ctx.label or "?") }
  if (tonumber(f.group) or 1) > 1 then out[#out + 1] = fmt("Groupe : %d%s", f.group, f.gather and " (dispersé : autre zone)" or "") end
  if f.log then out[#out + 1] = fmt("Journal : %d/%d%s", f.log, f.logMax or 20, f.logFull and " (presque plein)" or "") end
  if f.rested and (tonumber(f.restedXP) or 0) > 0 then out[#out + 1] = fmt("Repos : %s XP (l'XP des monstres compte double)", thousands(f.restedXP)) end
  if f.resting then out[#out + 1] = "Dans une auberge ou une ville (repos qui se remplit)" end
  if f.pvp then out[#out + 1] = "JcJ activé dans une zone dangereuse" end
  if f.levelUp then out[#out + 1] = "Niveau tout juste pris : tout est revu" end
  local raw = ctx.raw or {}
  if raw.free then out[#out + 1] = fmt("Sacs : %d place%s libre%s", raw.free, raw.free > 1 and "s" or "", raw.free > 1 and "s" or "") end
  if raw.durMin then out[#out + 1] = fmt("Équipement : %d %% (pièce la plus usée)", raw.durMin) end
  if raw.ammo then out[#out + 1] = fmt("Munitions : %d %s", raw.ammo, raw.ammoWord or "") end
  return out
end
local RULES = {
  { "park", "Quêtes inutiles en pause" }, { "lowValue", "… détours ruineux aussi" }, { "zone", "Zone choisie seule" },
  { "style", "Mode du trajet" }, { "errands", "Courses sur le trajet" }, { "hearth", "Pierre de foyer" },
  { "follow", "Suivre le groupe" }, { "notice", "Annonces sur la carte" },
}
local RULE_TIP = {
  park = "Grise, échouée, chaîne bloquée, doublon, donjon déjà fait, trop dure pour le groupe actuel, autre continent : retirée du suivi et du trajet, jamais abandonnée.",
  lowValue = "Une quête dont le détour coûte plus que ce qu'elle rapporte à ton rythme (XP/h mesurée au moins 10 min).",
  zone = "Quand ton trajet n'a presque plus rien et que tu n'as pas choisi de zone : la meilleure zone voisine du continent.",
  style = "Groupe formé : trajet en mode « Groupe » (si ZonePlan offre les modes) ; groupe dissous : le mode d'avant.",
  errands = "Réparer, vendre, munitions, maître de classe : un arrêt quand le trajet passe par une capitale (PNJ connu), ou si c'est urgent.",
  hearth = "Plus rien ici et au moins 2 quêtes finies se rendent près de ton auberge : la pierre de foyer est conseillée.",
  follow = "La zone, le donjon ou le point de rencontre que ton groupe veut (module Groupe) : suivi tout seul. Coupé par défaut.",
  notice = "Chaque décision est dite une fois sur la carte « Prochaine étape » ; tout reste dans cette page.",
}

-- ===================================================================== page
local function render(Ly)
  local Bn = B()
  if not Bn then Ly.empty("Le module « Pilote auto » est absent.") return end
  local ctx = Bn.Context()
  local list = Bn.ParkedList()
  local dec = Bn.Decisions(20)
  local active = on()
  local title = active and ("Pilote auto · " .. tostring(ctx.label or "")) or "Pilote auto coupé"
  local sub = active and fmt("%d quête%s en pause · %d décision%s", #list, #list > 1 and "s" or "", #dec, #dec > 1 and "s" or "")
    or "Rien n'est décidé tout seul ; tes réglages restent ceux que tu as choisis"
  Ly.hero(P.ICON, title, sub, active and "Seulement ce qui est sûr et réversible ; chaque décision s'annule ici d'un clic" or nil, nil,
    { "Faits du jeu seulement (couleur de la quête, groupe, carte, trajet mesuré).", "Rien de protégé, rien d'abandonné, rien dans le chat." })
  if not active then
    Ly.empty("Le pilote auto est coupé.", { "Allumer le pilote", function() T:SetModuleEnabled("Brain", true) end,
      "Clic : le pilote reprend ses règles (sans /reload)" })
  end
  local shown = 0
  if #list > 0 and Ly.header("En pause", "b:park", #list, "Interface\\GossipFrame\\IncompleteQuestIcon") then
    Ly.text("Retirées du suivi du jeu et du trajet, avec la raison. Elles reviennent seules quand la raison disparaît.", "S", "muted", 4)
    parkedRows(Ly, list)
  end
  shown = shown + #list
  local needs = active and Bn.Needs and Bn.Needs() or {}
  if #needs > 0 and Ly.header("Courses", "b:needs", #needs, ICON .. "INV_Misc_Bag_10") then
    local stops = {}
    for _, s in ipairs(Bn.ErrandStops and Bn.ErrandStops() or {}) do if not s.done then stops[s.key] = s end end
    for _, n in ipairs(needs) do
      local st = stops["errand:" .. tostring(n.kind)] or (n.kind == "vendor" and stops["errand:repair"])
      Ly.row({ text = n.text, prio = n.urgent and 2 or 1, kind = "money", right = st and "au trajet" or nil,
        onClick = Bn.GoService and function() Bn.GoService(n.kind) end or nil,
        clickHint = "Clic : la boussole vise le PNJ connu le plus proche",
        tooltip = st and { "Arrêt ajouté au trajet : " .. tostring(st.label) } or { "Pas d'arrêt au trajet : aucun PNJ connu sur ton chemin." } })
    end
    shown = shown + #needs
  end
  if #dec > 0 and Ly.header("Décisions", "b:log", #dec, ICON .. "INV_Misc_Note_02") then
    decisionRows(Ly, dec)
  end
  shown = shown + #dec
  if active and Ly.header("Ce que TreeAI voit", "b:ctx", nil, ICON .. "INV_Misc_Spyglass_02", true) then
    for _, l in ipairs(contextLines(ctx)) do Ly.text(l, "S", "text", 4) end
  end
  if Ly.header("Règles", "b:rules", nil, ICON .. "INV_Misc_Gear_01", true) then
    local btns = {}
    for _, r in ipairs(RULES) do
      local k = r[1]
      local v = Bn.db and Bn.db[k] == true
      btns[#btns + 1] = { r[2] .. " : " .. (v and "oui" or "non"), function() Bn.SetRule(k, not (Bn.db and Bn.db[k] == true)) end,
        (RULE_TIP[k] or "") .. "\nClic : " .. (v and "couper" or "allumer") .. " cette règle." }
    end
    Ly.buttons(btns, 2)
  end
  if shown == 0 and active then
    Ly.empty(W.Query() ~= "" and ("Rien ne correspond à « " .. W.Query() .. " ».")
      or "Rien décidé pour l'instant : chaque quête de ton journal sert encore, et rien ne presse.")
  end
end

W.AddPage({ id = "brain", hidden = true, label = "Pilote auto", icon = P.ICON, title = "Pilote auto : pourquoi ?",
  desc = "Ce que TreeAI a décidé seul, quand, pourquoi ; annuler d'un clic",
  sub = function()
    local Bn = B()
    if not (Bn and on()) then return "Coupé : rien n'est décidé tout seul" end
    local n = #Bn.ParkedList()
    local ctx = Bn.Context()
    return fmt("%s · %d quête%s en pause", tostring(ctx.label or ""), n, n > 1 and "s" or "")
  end,
  render = render,
  action = function()
    local Bn = B()
    if not Bn then return nil end
    if on() then
      return "Couper le pilote", function() T:SetModuleEnabled("Brain", false) end,
        "Clic : plus aucune décision seule ; les quêtes en pause reviennent, zone et mode d'avant"
    end
    return "Allumer le pilote", function() T:SetModuleEnabled("Brain", true) end, "Clic : le pilote reprend ses règles"
  end,
  status = function() return on() and "Pilote auto : actif" or "Pilote auto : coupé" end,
})
-- page cachée (ouverte depuis Outils) : rangée avant « Options », qui reste la dernière de la navigation
do
  local Pg = W.PAGES
  local me = W.pageIndex and W.pageIndex.brain
  if type(Pg) == "table" and me then
    for i = #Pg, 1, -1 do if Pg[i] == me then table.remove(Pg, i) break end end
    local at = #Pg + 1
    for i, q in ipairs(Pg) do if type(q) == "table" and q.id == "options" then at = i break end end
    table.insert(Pg, at, me)
  end
end
-- page cachée de la navigation (la liste « Jeu » de la pastille reste courte, comme « Journal de la boussole ») : ouverte
-- par la pastille (Outils > « Pilote auto : pourquoi ? »), l'avis de la carte, la section « En pause » de la page
-- Quêtes et /tai pilote
T.HUB_EXTRA = type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}
P.HUB = { group = "outils", id = "pilote", title = "Pilote auto : pourquoi ?", icon = P.ICON,
  tip = "Clic : ce que TreeAI a décidé seul (quêtes en pause, zone, courses), pourquoi, et l'annuler",
  fn = function() if W.Open then W.Open("brain") end end }
T.HUB_EXTRA[#T.HUB_EXTRA + 1] = P.HUB

-- ===================================================================== section de la page « Quêtes »
--- « En pause » sur la page Quêtes (repliée) : les 3 premières, clic = la page « Pilote auto »
function P.QuestsSection(Ly)
  local Bn = B()
  if not (Bn and on()) then return false end
  local list = Bn.ParkedList()
  if #list == 0 then return false end
  if Ly.header("En pause (pilote auto)", "q:brain", #list, "Interface\\GossipFrame\\IncompleteQuestIcon", true) then
    local shown = 0
    for _, p in ipairs(list) do
      if shown < 3 and W.Match(p.n, p.t) then
        Ly.quest(p.lv, p.n, p.why .. " · " .. tostring(p.t), function() W.Select("brain") end, { p.advice },
          "Clic : la page « Pilote auto » (reprendre, pourquoi)")
        shown = shown + 1
      end
    end
    if #list > shown then
      Ly.text(fmt("… et %d autre%s : page « Pilote auto ».", #list - shown, #list - shown > 1 and "s" or ""), "S", "muted")
    end
  end
  return true
end
