-- TreeAI — module Shortcuts (4.1, agent STRIP) : les BOUTONS RAPIDES, une seule bande horizontale en haut à gauche.
-- « Au lieu du suivi chelou en haut à gauche, mets des boutons simples pour faire les trucs les plus utiles » :
--   Rendre (4) · À prendre (3) · Suivant · Zone · Groupe · Journal · TreeAI
-- Chaque bouton = icône + mot court + pastille de nombre + sa touche en petit ; le mode de quêtes actif est surligné ;
-- l'infobulle dit ce qu'il fait, les quêtes concernées et sa touche. Les modes viennent de T.QuestBrain (Modes(),
-- SetMode(id), Mode(), PickupList(n), TurnInList(), message QUEST_MODE_CHANGED) ; sans lui (ou sans le mode), le
-- bouton ouvre la page de la fenêtre qui fait la même chose (Quêtes, Trajet, Groupe, Journal).
-- Place : en haut à gauche par défaut, JAMAIS sur les cadres du jeu (joueur, cible, familier, groupe 1-4, suivi de
-- quêtes, minimap, améliorations) : leurs rectangles sont lus (même cachés pour le groupe / la cible / le familier)
-- et la bande se pose dessous ou à droite ; trop étroit = icônes seules. Déplaçable (T:RegisterMovable), verrou,
-- taille. Touches : une liaison par bouton (Bindings.xml, sans touche d'office) + « mode suivant » + « fenêtre » ;
-- « Raccourcis conseillés » (un clic, hors combat) pose Alt-1… seulement sur les touches libres (jamais Ctrl-1…
-- Ctrl-0 : la barre du familier du jeu, ni Maj-1… Maj-6 : les pages de barres).
-- Rien de protégé : les clics changent un mode ou ouvrent une page. Disposition / place : hors combat seulement.
-- Événementiel (journal, groupe, modes, touches), aucune minuterie au repos. Lua 5.1, aucune globale.
-- (UIREDO, gardée) T:RegisterShortcutBar : la « barre d'actions » où Quick / QuickFight amarrent leurs boutons
-- sécurisés (objet de quête, monture, familier…). Cachée par défaut (4.1 : rien d'autre en haut à gauche ni au-dessus
-- des barres) ; une case dans les options la remet en bas de l'écran.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local S = T:RegisterModule("Shortcuts", {
  title = "Boutons rapides",
  description = "Les boutons rapides en haut à gauche : Rendre, À prendre, Suivant, Zone, Groupe, Journal, TreeAI.",
  defaults = { enabled = true, strip = true, text = true, keys = true, stripLocked = false, stripScale = 100,
               keysHint = true, actionBar = false, dock = true, locked = false, labels = true, scale = 100 },
  charDefaults = { pos = nil, strip = nil },
  options = {
    { section = "Boutons rapides", key = "strip", type = "toggle", label = "Afficher les boutons rapides (en haut à gauche)" },
    { key = "text", type = "toggle", label = "Un mot sous chaque icône (« Rendre », « À prendre »… ; sinon icônes seules)" },
    { key = "keys", type = "toggle", label = "Afficher la touche de chaque bouton" },
    { key = "stripLocked", type = "toggle", label = "Verrouiller les boutons rapides (ils ne bougent plus)" },
    { key = "stripScale", type = "range", label = "Taille des boutons rapides (%)", min = 70, max = 150, step = 5 },
    { key = "suggestKeys", type = "button", label = "Raccourcis conseillés : Alt-1 à Alt-8 (seulement les touches libres)",
      onClick = function() if T.Shortcuts and T.Shortcuts.SuggestKeys then T.Shortcuts.SuggestKeys() end end },
    { section = "Barre d'actions", key = "actionBar", type = "toggle",
      label = "Barre d'actions en bas de l'écran (objet de quête, monture, familier, manger)" },
    { key = "dock", type = "toggle", label = "Réunir ces boutons dans une seule barre" },
    { key = "locked", type = "toggle", label = "Verrouiller la barre d'actions" },
    { key = "labels", type = "toggle", label = "Titre de chaque groupe au-dessus des boutons" },
    { key = "scale", type = "range", label = "Taille de la barre d'actions (%)", min = 70, max = 150, step = 5 },
  },
})
T.Shortcuts = S

local floor, max, min = math.floor, math.max, math.min
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and t.Color then return t end end
local function tok() local th = Th() return (th and th.SHORTCUT) or { size = 30, gap = 4, pad = 4, handle = 10 } end
local function secret(v) local f = rawget(G, "issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function ulen(s) local _, n = tostring(s or ""):gsub("[^\128-\191]", "") return n end

-- ===================================================================== boutons rapides : définitions
local ICON = "Interface\\Icons\\"
-- match = morceaux d'identifiant (ou de libellé) d'un mode de T.QuestBrain ; page = repli dans la fenêtre
local DEFS = {
  { id = "rendre", label = "Rendre", icon = "Interface\\GossipFrame\\ActiveQuestIcon", bind = "TREEAI_QB_TURNIN",
    match = { "turnin", "turn_in", "rendre", "return", "handin", "turn" }, page = "quests",
    title = "Rendre mes quêtes au plus vite",
    tip = "Le trajet le plus court par les PNJ qui attendent tes quêtes finies (pierre de foyer et vols compris)." },
  { id = "prendre", label = "À prendre", icon = "Interface\\GossipFrame\\AvailableQuestIcon", bind = "TREEAI_QB_PICKUP",
    match = { "pickup", "pick", "prendre", "take", "accept", "available" }, page = "quests",
    title = "Quêtes à prendre",
    tip = "Les meilleures quêtes à prendre près de toi : XP par minute trajet compris, suites, groupe, niveau." },
  { id = "suivant", label = "Suivant", icon = ICON .. "Ability_Rogue_Sprint", bind = "TREEAI_STRIP_SUIVANT", page = "quests",
    title = "Étape suivante",
    tip = "Passe à l'étape suivante (celle-ci est faite, ou tu veux la sauter)." },
  { id = "zone", label = "Zone", icon = ICON .. "INV_Misc_Map_01", bind = "TREEAI_QB_ZONE",
    match = { "zone", "here", "ici", "all", "tout" }, page = "quests",
    title = "Choisir ma zone",
    tip = "Clic : toutes les zones pour toi, classées (quêtes, trajet, XP/h, groupe) ; un clic sur une zone, le trajet y va." },
  { id = "groupe", label = "Groupe", icon = ICON .. "Spell_Holy_PrayerOfHealing", bind = "TREEAI_QB_GROUP",
    match = { "group", "groupe", "party", "team" }, page = "group",
    title = "Avec le groupe",
    tip = "Les quêtes que tout le groupe peut faire ensemble ; seul : rejoindre un groupe." },
  { id = "journal", label = "Journal", icon = ICON .. "INV_Scroll_03", bind = "TREEAI_STRIP_JOURNAL",
    match = { "journal", "clean", "nettoy", "tidy", "log" }, page = "journal",
    title = "Nettoyer mon journal",
    tip = "Les quêtes à abandonner (grises, trop loin, impossibles seul), en un coup d'œil." },
  -- (4.2 FIX42) « Métiers » : seulement si tu as un métier de fabrication ; pastille = achats en attente du plan ;
  -- clic = Personnage → Métiers, clic droit = lancer / quitter la session métier (le trajet de quêtes en pause)
  { id = "metiers", label = "Métiers", icon = ICON .. "Trade_LeatherWorking", bind = "TREEAI_STRIP_METIERS", page = "prof",
    title = "Métiers",
    tip = "Clic : Personnage → Métiers (le plan pour monter ton métier). Clic droit : lancer ou quitter la session métier.",
    optional = true },
  { id = "fenetre", label = "TreeAI", icon = ICON .. "INV_Misc_Book_09", bind = "TREEAI_MAIN",
    title = "Fenêtre TreeAI",
    tip = "Ouvre ou ferme la fenêtre TreeAI (Quêtes, Groupe, Donjons, Personnage, HV, Conseils pro, Réglages)." },
}
S.DEFS = DEFS
local DEF = {}
for _, d in ipairs(DEFS) do DEF[d.id] = d end
-- touches conseillées : Alt-1… (rien par défaut dans le jeu), sinon Ctrl-Maj-1… ; JAMAIS Ctrl-1… Ctrl-0 (barre du
-- familier / des postures) ni Maj-1… Maj-6 (pages de barres d'action)
S.KEYS = {
  TREEAI_QB_TURNIN = { "ALT-1", "CTRL-SHIFT-1" }, TREEAI_QB_PICKUP = { "ALT-2", "CTRL-SHIFT-2" },
  TREEAI_STRIP_SUIVANT = { "ALT-3", "CTRL-SHIFT-3" }, TREEAI_QB_ZONE = { "ALT-4", "CTRL-SHIFT-4" },
  TREEAI_QB_GROUP = { "ALT-5", "CTRL-SHIFT-5" }, TREEAI_STRIP_JOURNAL = { "ALT-6", "CTRL-SHIFT-6" },
  TREEAI_MAIN = { "ALT-7", "CTRL-SHIFT-7" }, TREEAI_MODE_NEXT = { "ALT-8", "CTRL-SHIFT-8" },
  TREEAI_STRIP_METIERS = { "ALT-9", "CTRL-SHIFT-9" },
}
S.KEY_ORDER = { "TREEAI_QB_TURNIN", "TREEAI_QB_PICKUP", "TREEAI_STRIP_SUIVANT", "TREEAI_QB_ZONE",
  "TREEAI_QB_GROUP", "TREEAI_STRIP_JOURNAL", "TREEAI_MAIN", "TREEAI_MODE_NEXT", "TREEAI_STRIP_METIERS" }
-- noms lisibles (Échap > Raccourcis > AddOns > TreeAI)
G.BINDING_HEADER_TREEAI = G.BINDING_HEADER_TREEAI or "TreeAI"
G.BINDING_NAME_TREEAI_QB_TURNIN = "TreeAI : rendre mes quêtes au plus vite"
G.BINDING_NAME_TREEAI_QB_PICKUP = "TreeAI : quêtes à prendre"
G.BINDING_NAME_TREEAI_STRIP_SUIVANT = "TreeAI : étape suivante"
G.BINDING_NAME_TREEAI_QB_ZONE = "TreeAI : choisir ma zone"
G.BINDING_NAME_TREEAI_QB_GROUP = "TreeAI : avec le groupe"
G.BINDING_NAME_TREEAI_STRIP_JOURNAL = "TreeAI : nettoyer mon journal"
G.BINDING_NAME_TREEAI_MODE_NEXT = "TreeAI : mode de quêtes suivant"
G.BINDING_NAME_TREEAI_STRIP_METIERS = "TreeAI : métiers (page, session)"

-- ===================================================================== données : T.QuestBrain (gardé)
local function QB() local q = T.QuestBrain return type(q) == "table" and q or nil end
local function qcall(name, ...)
  local q = QB()
  local f = q and q[name]
  if type(f) ~= "function" then return nil end
  return try(f, ...)
end
local function lower(s) return tostring(s or ""):lower() end
--- la liste des modes de T.QuestBrain (vide sans lui)
function S.Modes()
  local r = qcall("Modes")
  if type(r) ~= "table" then return {} end
  local out = {}
  for _, m in ipairs(r) do if type(m) == "table" and m.id ~= nil and not secret(m.id) then out[#out + 1] = m end end
  return out
end
local function activeId()
  local m = qcall("Mode")
  if type(m) == "table" then m = m.id end
  return m
end
--- le mode de T.QuestBrain d'un bouton (identifiant, puis libellé), ou nil
function S.ModeFor(id, list)
  local d = DEF[id]
  if not (d and d.match) then return nil end
  list = list or S.Modes()
  for pass_ = 1, 3 do
    for _, m in ipairs(list) do
      local hay = pass_ == 3 and lower(m.label) .. " " .. lower(m.short) or lower(m.id)
      for _, p in ipairs(d.match) do
        if (pass_ == 1 and hay == p) or (pass_ > 1 and hay:find(p, 1, true)) then return m end
      end
    end
  end
  return nil
end
local function listLen(t) return type(t) == "table" and #t or nil end
-- quêtes finies du journal (repli sans QuestBrain), lu sur événement seulement
local function doneInLog()
  local Q = T.modules and T.modules.Quests
  if not (Q and type(Q.Scan) == "function") then return nil end
  local list = try(Q.Scan)
  if type(list) ~= "table" then return nil end
  local n = 0
  for _, q in ipairs(list) do if q.complete and not q.failed then n = n + 1 end end
  return n
end
local function itemText(it)
  if type(it) == "string" then return it end
  if type(it) ~= "table" then return nil end
  local s = it.title or it.name or it.label or it.text or it.line
  if type(s) ~= "string" or secret(s) then return nil end
  local extra = it.npc or it.giver or it.where or it.zone
  if type(extra) == "string" and not secret(extra) and extra ~= "" then s = s .. " · " .. extra end
  return s
end

--- état de chaque bouton : { id, label, count, active, key, mode, title, tip } — le contrat des tests
function S.State()
  local list, act = S.Modes(), activeId()
  local out = {}
  for _, d in ipairs(DEFS) do
    if not d.optional or S.HasProf() then
    local st = { id = d.id, label = d.label, icon = d.icon, title = d.title, tip = d.tip, bind = d.bind }
    local m = S.ModeFor(d.id, list)
    if m then
      st.mode = m.id
      st.active = (m.active == true) or (act ~= nil and act == m.id) or false
      if type(m.label) == "string" and m.label ~= "" then st.title = m.label end
      if type(m.tip) == "string" and m.tip ~= "" then st.tip = m.tip end
      if type(m.count) == "number" and not secret(m.count) then st.count = m.count end
      if type(m.key) == "string" and m.key ~= "" then st.modeKey = m.key end
    end
    if st.count == nil then
      if d.id == "rendre" then st.count = listLen(qcall("TurnInList")) or (not QB() and doneInLog() or nil)
      elseif d.id == "prendre" then st.count = listLen(qcall("PickupList", 20))
      elseif d.id == "metiers" then st.count = S.ProfBuys() st.active = type(T.focus) == "table" and T.focus.kind == "prof" end
    end
    st.key = S.KeyOf(d.bind)
    out[#out + 1] = st
    end
  end
  return out
end
-- (4.2 FIX42) « Métiers » : tu as un métier de fabrication (Prof a lu tes métiers) ; achats en attente (ProfLevel)
local function PLv() local p = T.ProfLevel return type(p) == "table" and p or nil end
function S.HasProf()
  local P = PLv()
  if not (P and type(P.Line) == "function") then return false end
  local Pm = T.modules and T.modules.Prof
  if not (Pm and Pm._active) then return false end
  return try(P.Line) ~= nil
end
function S.ProfBuys()
  local P = PLv()
  if not (P and type(P.PendingBuys) == "function") then return nil end
  local n = tonumber(try(P.PendingBuys))
  return (n and n > 0) and n or nil
end

-- ===================================================================== touches
local function bindKey(cmd)
  if type(cmd) ~= "string" then return nil end
  local k = try(G.GetBindingKey, cmd)
  if type(k) ~= "string" or k == "" or secret(k) then return nil end
  return k
end
S.BindKey = bindKey
--- texte lisible d'une touche (« Alt-1 », jamais l'abréviation « a-1 » du jeu)
function S.KeyText(k)
  local B = T.Binds
  if type(B) == "table" and type(B.KeyText) == "function" then return B.KeyText(k) end
  return (tostring(k):gsub("SHIFT%-", "Maj-"):gsub("CTRL%-", "Ctrl-"):gsub("ALT%-", "Alt-"))
end
function S.KeyOf(cmd) local k = bindKey(cmd) return k and S.KeyText(k) or nil end
--- liaisons des boutons rapides encore sans touche
function S.MissingKeys()
  local out = {}
  for _, cmd in ipairs(S.KEY_ORDER) do if not bindKey(cmd) then out[#out + 1] = cmd end end
  return out
end
local function flash(text)
  local Wn = T.Window
  if Wn and type(Wn.Flash) == "function" then try(Wn.Flash, text, 12) end
  S.lastMessage = text
end
--- « Raccourcis conseillés » : un clic du joueur, hors combat ; Alt-1… sur les seules touches libres, enregistré
function S.SuggestKeys()
  if inCombat() then flash("Touches : après le combat.") return nil, "combat" end
  local B = T.Binds
  if not (type(B) == "table" and type(B.ApplyKeys) == "function") then
    flash("Touches : Échap > Raccourcis > AddOns > TreeAI.")
    return nil, "api"
  end
  local list = {}
  for _, cmd in ipairs(S.KEY_ORDER) do
    list[#list + 1] = { cmd = cmd, keys = S.KEYS[cmd], name = G["BINDING_NAME_" .. cmd] or cmd }
  end
  local done, why = B.ApplyKeys(list)
  done = type(done) == "table" and done or {}
  local names = {}
  for _, d in ipairs(done) do names[#names + 1] = S.KeyText(d.key) end
  if why then
    flash("Touches : le jeu a refusé (" .. tostring(why) .. "). Échap > Raccourcis > AddOns > TreeAI.")
  elseif #names > 0 then
    flash("Touches posées : " .. table.concat(names, ", ") .. ".")
  else
    flash("Touches : rien à poser (déjà liées, ou touches conseillées prises).")
  end
  S.Refresh(true)
  return done, why
end

-- ===================================================================== actions (jamais protégées)
local function openPage(page)
  local Wn = T.Window
  if Wn and type(Wn.Open) == "function" then T.safecall(Wn.Open, page) return true end
  if T.ShowPanel then T.safecall(T.ShowPanel, T) end
  return false
end
S.OpenPage = openPage
local function setMode(id)
  local q = QB()
  if not (q and type(q.SetMode) == "function") then return false end
  local ok, r = pcall(q.SetMode, id)
  return ok and r ~= false
end
--- étape suivante : QuestBrain, puis la boussole (cible suivante), sinon la page Trajet
local function skip()
  local q = QB()
  for _, n in ipairs({ "Skip", "SkipStop", "NextStop" }) do
    if q and type(q[n]) == "function" and pcall(q[n]) then return "brain" end
  end
  if type(T.SkipNext) == "function" and pcall(T.SkipNext) then return "core" end
  local C = T.modules and T.modules.Compass
  if C and C._active and type(C.Cycle) == "function" and type(C.targets) == "table" then
    if #C.targets == 0 and type(C.Rebuild) == "function" then try(C.Rebuild) end
    if #C.targets > 1 then try(C.Cycle, 1) return "compass" end
  end
  openPage("quests")
  return "page"
end
--- mode de quêtes suivant (touche « mode suivant »)
function S.CycleMode()
  local q = QB()
  if q and type(q.Cycle) == "function" and pcall(q.Cycle) then S.Refresh(true) return activeId() end
  local list = S.Modes()
  if #list == 0 then openPage("quests") return nil end
  local act, at = activeId(), 0
  for i, m in ipairs(list) do if m.active == true or (act ~= nil and m.id == act) then at = i end end
  local nx = list[(at % #list) + 1]
  setMode(nx.id)
  S.Refresh(true)
  return nx.id
end
--- l'action d'un bouton (clic, ou sa touche). button = "RightButton" : la page de la fenêtre.
function S.Do(id, button)
  local d = DEF[id]
  if id == "mode" then return S.CycleMode() end
  if not d then return nil end
  S.last = id
  if id == "fenetre" then
    local Wn = T.Window
    if Wn and type(Wn.Toggle) == "function" then T.safecall(Wn.Toggle) else openPage(nil) end
    return "window"
  end
  if id == "metiers" then
    local P = PLv()
    if button == "RightButton" and P and type(P.ToggleFocus) == "function" then
      if inCombat() then flash("Session métier : après le combat.") return "combat" end
      T.safecall(P.ToggleFocus, (type(P.Line) == "function" and try(P.Line)) or nil)
      S.Refresh(true)
      return "focus"
    end
    openPage("prof")
    return "page"
  end
  if id == "suivant" then
    -- (4.1 CITYRUN) session métier / courses en ville (T.focus) : étape suivante de la session, clic droit = précédente
    if type(T.focus) == "table" and type(T.FocusStep) == "function" then
      pcall(T.FocusStep, button == "RightButton" and -1 or 1)
      S.Refresh(true)
      return "focus"
    end
    if button == "RightButton" then openPage(d.page) return "page" end
    local how = skip()
    S.Refresh(true)
    return how
  end
  local m = button ~= "RightButton" and S.ModeFor(id) or nil
  -- (4.1 ZONEPICK) « Zone » : « Tout faire ici » ET le choix de la zone (fenêtre, page Quêtes) — un clic, pas juste un mode
  if id == "zone" and button ~= "RightButton" then
    if m then setMode(m.id) end
    local Wn = T.Window
    if Wn and type(Wn.ToggleZonePicker) == "function" then T.safecall(Wn.ToggleZonePicker) else openPage(d.page) end
    S.Refresh(true)
    return "zone", m and m.id
  end
  if m and setMode(m.id) then S.Refresh(true) return "mode", m.id end
  openPage(d.page)
  return "page"
end
T.StripAction = function(id) return S.Do(id) end   -- appelé par Bindings.xml

-- ===================================================================== bande : cadre et boutons
local strip, grip, keysBtn
local btns, order = {}, {}
local BTN_H, ICO, PAD, GAP = 24, 16, 4, 2   -- (4.2 FIX42) plus petite : 24 px de haut, icônes 16
local function stripOn() return S._active and S.db and S.db.strip ~= false end
local function stripLocked() return (S.db and S.db.stripLocked) or (T.FramesLocked and T:FramesLocked()) or false end
local function okAnchor(p, x, y) return T.ValidAnchor and T.ValidAnchor(p, x, y) or false end
local function color(tex, r, g, b, a)
  if not tex then return end
  if type(tex.SetColorTexture) == "function" then meth(tex, "SetColorTexture", r, g, b, a or 1)
  else meth(tex, "SetTexture", "Interface\\Buttons\\WHITE8X8"); meth(tex, "SetVertexColor", r, g, b, a or 1) end
end
local function tcolor(tex, token, r, g, b, a)
  local th = Th()
  if th then local cr, cg, cb, ca = th:Color(token) color(tex, cr, cg, cb, a or ca) else color(tex, r, g, b, a) end
end
local function fstring(parent, size, token)
  local th = Th()
  local fs = th and th:Text(parent, size, token) or meth(parent, "CreateFontString", nil, "OVERLAY",
    size == "S" and "GameFontHighlightSmall" or "GameFontHighlight")
  if fs then meth(fs, "SetWordWrap", false) end
  return fs
end

local function hideTip() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end
local function stateOf(id)
  for _, st in ipairs(S.lastState or {}) do if st.id == id then return st end end
end
local function showTip(b)
  local tt = G.GameTooltip
  if not tt then return end
  local st = stateOf(b.id) or {}
  local d = DEF[b.id] or {}
  meth(tt, "SetOwner", b, "ANCHOR_BOTTOMRIGHT")
  meth(tt, "AddLine", st.title or d.title or d.label, 1, 0.82, 0.2)
  meth(tt, "AddLine", st.tip or d.tip, 0.9, 0.9, 0.9, true)
  local lines = {}
  if b.id == "rendre" then lines = qcall("TurnInList") or {}
  elseif b.id == "prendre" then lines = qcall("PickupList", 5) or {} end
  local n = 0
  for _, it in ipairs(type(lines) == "table" and lines or {}) do
    local s = itemText(it)
    if s then n = n + 1 meth(tt, "AddLine", "• " .. s, 0.8, 0.8, 0.8) end
    if n >= 5 then break end
  end
  if b.id == "metiers" then
    local P = PLv()
    local cl = P and type(P.ComponentLines) == "function" and try(P.ComponentLines, true) or {}
    for i, l in ipairs(type(cl) == "table" and cl or {}) do
      if i > 6 then break end
      meth(tt, "AddLine", l, 0.8, 0.8, 0.8, true)
    end
    if (tonumber(st.count) or 0) > 0 then meth(tt, "AddLine", ("%d achat%s en attente dans ton plan"):format(st.count, st.count > 1 and "s" or ""), 1, 0.82, 0.2) end
    if st.active then meth(tt, "AddLine", "Session métier en cours (clic droit : la quitter).", 0.4, 1, 0.4, true) end
  end
  if b.id == "suivant" and type(T.Next) == "function" then
    local nx = try(T.Next)
    local s = type(nx) == "table" and (nx.line or nx.title)
    if type(s) == "string" and not secret(s) then meth(tt, "AddLine", "Maintenant : " .. s, 0.8, 0.8, 0.8, true) end
  end
  local inFocus = b.id == "suivant" and type(T.focus) == "table" and type(T.FocusStep) == "function"
  if inFocus then
    meth(tt, "AddLine", (type(T.focus.label) == "string" and T.focus.label or "Session") .. " : clic = étape suivante, clic droit = précédente",
      0.4, 1, 0.4, true)
  end
  if st.active then meth(tt, "AddLine", "Mode choisi.", 0.4, 1, 0.4) end
  meth(tt, "AddLine", st.key and ("Touche : " .. st.key)
    or "Touche : aucune (options des boutons rapides : « Raccourcis conseillés »)", 0.4, 0.8, 1, true)
  if d.page and b.id ~= "fenetre" and not inFocus then meth(tt, "AddLine", "Clic droit : la page de la fenêtre", 0.4, 0.8, 1) end
  meth(tt, "Show")
end

local function newButton(def)
  local ok, b = pcall(G.CreateFrame, "Button", "TreeAIStrip_" .. def.id, strip)
  if not ok or not b then return nil end
  b.id = def.id
  meth(b, "SetHeight", BTN_H)
  meth(b, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  b.bg = meth(b, "CreateTexture", nil, "BACKGROUND")
  if b.bg then meth(b.bg, "SetAllPoints", b) tcolor(b.bg, "button", 0.14, 0.15, 0.19, 0.35) end
  b.sel = meth(b, "CreateTexture", nil, "BORDER")   -- mode choisi : fond or doux + liseré bas
  if b.sel then meth(b.sel, "SetAllPoints", b) tcolor(b.sel, "gold", 1, 0.82, 0.2, 0.22) meth(b.sel, "Hide") end
  b.line = meth(b, "CreateTexture", nil, "ARTWORK")
  if b.line then
    meth(b.line, "SetPoint", "BOTTOMLEFT", b, "BOTTOMLEFT", 0, 0); meth(b.line, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", 0, 0)
    meth(b.line, "SetHeight", 2) tcolor(b.line, "gold", 1, 0.82, 0.2, 1) meth(b.line, "Hide")
  end
  local hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", b) color(hl, 1, 1, 1, 0.08) end
  b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
  if b.icon then
    meth(b.icon, "SetSize", ICO, ICO)
    meth(b.icon, "SetPoint", "LEFT", b, "LEFT", PAD, 0)
    meth(b.icon, "SetTexture", def.icon)
    if def.icon:find("Icons", 1, true) then meth(b.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92) end
  end
  b.text = fstring(b, "S", "text")
  if b.text then meth(b.text, "SetPoint", "LEFT", b, "LEFT", PAD + ICO + 4, 1) meth(b.text, "SetJustifyH", "LEFT") end
  b.badgeBg = meth(b, "CreateTexture", nil, "OVERLAY")
  if b.badgeBg then
    meth(b.badgeBg, "SetSize", 13, 11)
    meth(b.badgeBg, "SetPoint", "TOPRIGHT", b.icon or b, "TOPRIGHT", 4, 3)
    color(b.badgeBg, 0.75, 0.12, 0.10, 0.95)
  end
  b.badge = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmall")
  if b.badge then meth(b.badge, "SetPoint", "CENTER", b.badgeBg or b, "CENTER", 0, 0) end
  b.key = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmallGray")
  if b.key then meth(b.key, "SetPoint", "BOTTOMRIGHT", b, "BOTTOMRIGHT", -2, 2) meth(b.key, "SetJustifyH", "RIGHT") end
  meth(b, "SetScript", "OnClick", function(self, button)
    hideTip()
    local fn = function() S.Do(self.id, button) end
    if T.UserAction then T:UserAction(fn) else T.safecall(fn) end
  end)
  meth(b, "SetScript", "OnEnter", showTip)
  meth(b, "SetScript", "OnLeave", hideTip)
  btns[def.id] = b
  order[#order + 1] = b
  return b
end

local function saveStripPos()
  if not strip then return end
  if T.AnchorFromRect then T:AnchorFromRect(strip) end
  local pt, _, rp, x, y = meth(strip, "GetPoint")
  if S.char and okAnchor(pt, x, y) then S.char.strip = { pt, okAnchor(rp, 0, 0) and rp or pt, floor(x + 0.5), floor(y + 0.5) } end
end

local function ensureStrip()
  if strip then return strip end
  if inCombat() or type(G.CreateFrame) ~= "function" then return nil end
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIStrip", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if not ok or not f then return nil end
  strip = f
  meth(f, "SetSize", 200, BTN_H + 6)
  meth(f, "SetFrameStrata", "MEDIUM")
  meth(f, "SetClampedToScreen", true)
  meth(f, "SetMovable", true)
  meth(f, "EnableMouse", true)
  local th = Th()
  -- (4.2 FIX42) petit cadre posé sur l'écran : fond transparent (~0,4), liseré 1 px (Theme « small »)
  if not (th and th:Skin(f, "small")) then
    f.bgt = meth(f, "CreateTexture", nil, "BACKGROUND")
    if f.bgt then meth(f.bgt, "SetAllPoints", f) color(f.bgt, 0.05, 0.06, 0.08, 0.42) end
  end
  -- poignée : glisser = déplacer (hors combat, sauf verrou), clic droit = réglages
  local okG, h = pcall(G.CreateFrame, "Button", "TreeAIStripGrip", f)
  if okG and h then
    grip = h
    meth(h, "SetSize", 8, BTN_H)
    meth(h, "SetPoint", "LEFT", f, "LEFT", 3, 0)
    meth(h, "RegisterForClicks", "RightButtonUp")
    meth(h, "RegisterForDrag", "LeftButton")
    for i = 1, 3 do
      local d = meth(h, "CreateTexture", nil, "ARTWORK")
      if d then meth(d, "SetSize", 3, 3) meth(d, "SetPoint", "CENTER", h, "CENTER", 0, (2 - i) * 6) tcolor(d, "gold", 1, 0.82, 0.2, 1) end
    end
    meth(h, "SetScript", "OnDragStart", function() if not inCombat() and not stripLocked() then meth(f, "StartMoving") end end)
    meth(h, "SetScript", "OnDragStop", function() meth(f, "StopMovingOrSizing") saveStripPos() end)
    meth(h, "SetScript", "OnClick", function()
      local Wn = T.Window
      if Wn and Wn._active and Wn.Open then
        if Wn.char then Wn.char.optPage = "Shortcuts" end
        Wn.Open("options")
      elseif T.OpenOptions then T:OpenOptions("Shortcuts") end
    end)
    meth(h, "SetScript", "OnEnter", function(self)
      local tt = G.GameTooltip
      if not tt then return end
      meth(tt, "SetOwner", self, "ANCHOR_BOTTOMRIGHT")
      meth(tt, "AddLine", "Boutons rapides", 1, 0.82, 0.2)
      meth(tt, "AddLine", stripLocked() and "Verrouillés (réglages : clic droit)" or "Glisser : déplacer (hors combat)", 0.4, 0.8, 1)
      meth(tt, "AddLine", "Clic droit : réglages, touches conseillées", 0.4, 0.8, 1)
      meth(tt, "Show")
    end)
    meth(h, "SetScript", "OnLeave", hideTip)
  end
  for _, d in ipairs(DEFS) do newButton(d) end
  -- « Touches » : seulement tant qu'une liaison manque (clic = touches conseillées, clic droit = ne plus proposer)
  local okK, kb = pcall(G.CreateFrame, "Button", "TreeAIStripKeys", f)
  if okK and kb then
    keysBtn = kb
    meth(kb, "SetHeight", BTN_H)
    meth(kb, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
    kb.bg = meth(kb, "CreateTexture", nil, "BACKGROUND")
    if kb.bg then meth(kb.bg, "SetAllPoints", kb) tcolor(kb.bg, "button", 0.14, 0.15, 0.19, 0.4) end
    kb.text = fstring(kb, "S", "muted")
    if kb.text then meth(kb.text, "SetPoint", "CENTER", kb, "CENTER", 0, 0) meth(kb.text, "SetText", "Touches") end
    meth(kb, "SetScript", "OnClick", function(_, button)
      hideTip()
      if button == "RightButton" then if S.db then S.db.keysHint = false end S.LayoutStrip() return end
      if T.UserAction then T:UserAction(S.SuggestKeys) else T.safecall(S.SuggestKeys) end
    end)
    meth(kb, "SetScript", "OnEnter", function(self)
      local tt = G.GameTooltip
      if not tt then return end
      meth(tt, "SetOwner", self, "ANCHOR_BOTTOMRIGHT")
      meth(tt, "AddLine", "Raccourcis conseillés", 1, 0.82, 0.2)
      meth(tt, "AddLine", "Un clic : Alt-1 à Alt-9 pour ces boutons, seulement sur les touches libres (rien n'est remplacé).",
        0.9, 0.9, 0.9, true)
      meth(tt, "AddLine", "Clic droit : ne plus proposer", 0.4, 0.8, 1)
      meth(tt, "Show")
    end)
    meth(kb, "SetScript", "OnLeave", hideTip)
  end
  if T.RegisterMovable then
    T:RegisterMovable(f, "strip", "Boutons rapides", {
      onMoved = function(point, x, y, sc)
        if S.char then S.char.strip = { point, point, floor(x or 0), floor(y or 0) } end
        if sc and S.db then S.db.stripScale = floor(sc * 100 + 0.5) end
      end,
      onReset = function() if S.char then S.char.strip = nil end S.PlaceStrip() end,
      isPlaced = function() return S.char and type(S.char.strip) == "table" or false end,
    })
  end
  meth(f, "Hide")
  return strip
end
S.EnsureStrip = ensureStrip
function S.Strip() return strip end
function S.Buttons() return btns end

-- ===================================================================== place : jamais sur les cadres du jeu
-- cadres du jeu évités ; RESERVE = mesurés même cachés (le groupe, la cible, le familier arrivent plus tard)
local AVOID = { "PlayerFrame", "TargetFrame", "TargetFrameToT", "FocusFrame", "PetFrame", "PartyMemberFrame1",
  "PartyMemberFrame2", "PartyMemberFrame3", "PartyMemberFrame4", "PartyFrame", "CompactPartyFrame",
  "CompactRaidFrameContainer", "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame", "QuestTimerFrame",
  "Questie_BaseFrame", "QuestieTrackerBaseFrame", "MinimapCluster", "Minimap", "BuffFrame", "DebuffFrame",
  "TemporaryEnchantFrame", "UIWidgetTopCenterContainerFrame", "DurabilityFrame", "ElvUF_Player", "ElvUF_Target",
  "ElvUF_Party", "TreeAICompassFrame", "TreeAIWatchFrame" }
local RESERVE = { PlayerFrame = true, TargetFrame = true, TargetFrameToT = true, FocusFrame = true, PetFrame = true,
  PartyMemberFrame1 = true, PartyMemberFrame2 = true, PartyMemberFrame3 = true, PartyMemberFrame4 = true,
  PartyFrame = true, CompactPartyFrame = true }
-- sous la cible / la focalisation : leurs améliorations et affaiblissements (deux rangées d'icônes)
local AURAS_BELOW = { TargetFrame = 46, FocusFrame = 46 }
-- cadre du jeu présent mais pas encore mesurable (jamais montré) : sa place d'origine (Classic) est réservée
local FALLBACK = {
  PlayerFrame = function(_, H) return { l = 0, b = H - 110, w = 240, h = 110 } end,
  TargetFrame = function(_, H) return { l = 245, b = H - 152, w = 245, h = 152 } end,
  PartyMemberFrame1 = function(_, H) return { l = 0, b = H - 440, w = 200, h = 284 } end,
}
S.AVOID, S.RESERVE = AVOID, RESERVE
local function finite(v) return type(v) == "number" and v == v and v > -1e6 and v < 1e6 end
local function screen()
  if T._ScreenSize then return T._ScreenSize() end
  local w, h = meth(G.UIParent, "GetWidth"), meth(G.UIParent, "GetHeight")
  if finite(w) and finite(h) and w > 200 and h > 200 then return w, h end
end
--- rectangle d'un cadre du jeu en unités d'UIParent { l, b, w, h, name } ou nil
function S.RectOf(name, evenHidden)
  local f = rawget(G, name)
  if type(f) ~= "table" or (not evenHidden and not meth(f, "IsVisible")) then return nil end
  if meth(f, "IsForbidden") then return nil end
  local l, b, w, h = meth(f, "GetRect")
  if not (finite(l) and finite(b) and finite(w) and finite(h)) or w < 8 or h < 8 then return nil end
  local k = (tonumber(meth(f, "GetEffectiveScale")) or 1) / (tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1)
  if not finite(k) or k <= 0 then k = 1 end
  return { l = l * k, b = b * k, w = w * k, h = h * k, name = name }
end
local function stripRect()
  if not strip then return nil end
  local l, b, w, h = meth(strip, "GetRect")
  if not (finite(l) and finite(b) and finite(w) and finite(h)) then return nil end
  local k = (tonumber(meth(strip, "GetEffectiveScale")) or 1) / (tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1)
  return { l = l * k, b = b * k, w = w * k, h = h * k }
end
S.StripRect = stripRect
function S.Avoid(W, H)
  local out, seen = {}, {}
  local function add(n)
    if seen[n] then return end
    seen[n] = true
    local r = S.RectOf(n, RESERVE[n])
    if not r and FALLBACK[n] and type(rawget(G, n)) == "table" then r = FALLBACK[n](W, H) r.name = n end
    if r and AURAS_BELOW[n] then r.b, r.h = r.b - AURAS_BELOW[n], r.h + AURAS_BELOW[n] end
    if r and not (r.w >= W * 0.9 and r.h >= H * 0.6) then out[#out + 1] = r end
  end
  for _, n in ipairs(AVOID) do add(n) end
  for _, n in ipairs(T.LAYOUT_SCAN_NAMES or {}) do
    -- les barres / la discussion du bas ne gênent pas une bande du haut, mais rien ne coûte de les éviter
    add(n)
  end
  return out
end
local function overlaps(a, b, m)
  m = m or 0
  return a.l < b.l + b.w + m and b.l < a.l + a.w + m and a.b < b.b + b.h + m and b.b < a.b + a.h + m
end
S.Overlaps = overlaps
--- meilleure place libre en haut à gauche pour w × h (unités UIParent) : (gauche, haut) ou nil
function S.FindSpot(w, h, W, H, avoid)
  local xs, tops = { 8 }, { H - 6 }
  for _, r in ipairs(avoid) do
    xs[#xs + 1] = r.l + r.w + 6
    tops[#tops + 1] = r.b - 6
  end
  local keep = T.ReservedRects and T.ReservedRects(W, H) or {}
  local best, bl, bt
  local least, ll, lt   -- rien de libre : la place qui cache le moins de cadres du jeu
  for _, x in ipairs(xs) do
    for _, top in ipairs(tops) do
      local rc = { l = x, b = top - h, w = w, h = h }
      if x >= 4 and x + w <= W - 4 and rc.b >= 4 and top <= H - 2 then
        local free, area = true, 0
        for _, r in ipairs(avoid) do
          if overlaps(rc, r, 3) then
            free = false
            area = area + (min(rc.l + w, r.l + r.w) - max(rc.l, r.l)) * (min(top, r.b + r.h) - max(rc.b, r.b))
          end
        end
        if not free and (not least or area < least) then least, ll, lt = area, x, top end
        if free then for _, r in ipairs(keep) do if overlaps(rc, r, 0) then free = false break end end end
        if free then
          local score = (H - top) + x * 0.5
          if not best or score < best then best, bl, bt = score, x, top end
        end
      end
    end
  end
  if bl then return bl, bt end
  return nil, nil, ll, lt
end

local placeTries, placing = 0, false
--- pose la bande : place du joueur si elle en a une, sinon la meilleure place libre en haut à gauche (hors combat)
function S.PlaceStrip()
  if not strip then return false end
  if inCombat() then T:RunOutOfCombat("Shortcuts:strip", S.PlaceStrip) return false end
  local k = max(0.7, min(1.5, (tonumber(S.db and S.db.stripScale) or 100) / 100))
  meth(strip, "SetScale", k)
  local p = S.char and S.char.strip
  if type(p) == "table" and not okAnchor(p[1], p[3], p[4]) then S.char.strip, p = nil, nil end
  if type(p) == "table" then
    local rp = okAnchor(p[2], 0, 0) and p[2] or p[1]
    meth(strip, "ClearAllPoints")
    if pcall(strip.SetPoint, strip, p[1], G.UIParent, rp, p[3], p[4]) then
      S.placed = "player"
      if stripOn() then meth(strip, "Show") end
      return true
    end
  end
  local W, H = screen()
  if not W then
    -- écran pas encore posé (connexion) : quelques essais espacés, puis les événements d'écran prennent le relais
    -- (cachée d'ici là : jamais une image sur les cadres de groupe)
    if placeTries < 5 then placeTries = placeTries + 1 T:After(2, S.PlaceStrip) end
    meth(strip, "Hide")
    S.placed = nil
    return false
  end
  placeTries = 0
  local avoid = S.Avoid(W, H)
  local function size() return (tonumber(meth(strip, "GetWidth")) or 200) * k, (tonumber(meth(strip, "GetHeight")) or 34) * k end
  -- les cadres du jeu d'abord : sur un petit écran, plutôt sur un cadre TreeAI (boussole) que sur le groupe
  local game = {}
  for _, r in ipairs(avoid) do if not tostring(r.name or ""):find("^TreeAI") then game[#game + 1] = r end end
  placing = true
  local l, top, fl, ft, w, h
  for _, try_ in ipairs({ { false, avoid }, { true, avoid }, { false, game }, { true, game } }) do
    -- avec les mots d'abord (l'écran a pu grandir depuis), icônes seules si la bande ne tient nulle part
    local compact = try_[1] or S.db.text == false
    if (S.compact or false) ~= compact then S.LayoutStrip(compact) end
    w, h = size()
    local a, b, c, d = S.FindSpot(w, h, W, H, try_[2])
    if not fl then fl, ft = c, d end
    if a then l, top = a, b break end
  end
  placing = false
  if not l then
    -- rien de libre (écran minuscule) : la place qui cache le moins, sinon sous les cadres de gauche
    l, top = fl, ft
    if not l then
      local low = H - 6
      for _, r in ipairs(avoid) do if r.l < W * 0.3 and r.b + r.h > H * 0.4 then low = min(low, r.b - 6) end end
      l, top = 8, max(h + 4, low)
    end
  end
  meth(strip, "ClearAllPoints")
  meth(strip, "SetPoint", "TOPLEFT", G.UIParent, "TOPLEFT", floor(l / k + 0.5), floor((top - H) / k + 0.5))
  S.placed = "auto"
  if stripOn() then meth(strip, "Show") end
  return true
end

-- ===================================================================== disposition (hors combat)
local function textW(fs, s)
  local w = fs and tonumber(meth(fs, "GetStringWidth")) or 0
  if not finite(w) or w <= 0 then w = ulen(s) * 5.6 end
  return w
end
local lastWide
--- dispose les boutons ; compact = icônes seules (étroit, ou option « un mot » coupée)
function S.LayoutStrip(compact)
  if not stripOn() then if strip then meth(strip, "Hide") end return end
  if inCombat() then T:RunOutOfCombat("Shortcuts:strip", function() S.LayoutStrip(compact) end) return end
  if not ensureStrip() then return end
  local states = S.lastState or S.State()
  S.lastState = states
  if compact == nil then compact = S.compact or false end
  if S.db.text == false then compact = true end
  S.compact = compact
  local showKeys = S.db.keys ~= false
  local x = 3 + 8 + 4
  for _, st in ipairs(states) do
    local b = btns[st.id]
    if b then
      local label = st.label or ""
      meth(b.text, "SetText", label)
      local w
      if compact then
        meth(b.text, "Hide")
        w = PAD + ICO + PAD
      else
        meth(b.text, "Show")
        w = PAD + ICO + 4 + textW(b.text, label) + 7
      end
      local kt = showKeys and st.key or nil
      meth(b.key, "SetText", kt or "")
      if kt then meth(b.key, "Show") else meth(b.key, "Hide") end
      if kt and not compact then w = max(w, PAD + ICO + 4 + ulen(kt) * 5 + 4) end
      w = floor(w + 0.5)
      meth(b, "SetWidth", w)
      meth(b, "ClearAllPoints")
      meth(b, "SetPoint", "LEFT", strip, "LEFT", x, 0)
      meth(b, "Show")
      b.w = w
      x = x + w + GAP
    end
  end
  local missing = #S.MissingKeys() > 0 and S.db.keysHint ~= false and showKeys
  if keysBtn then
    if missing and not compact then
      local w = floor(textW(keysBtn.text, "Touches") + 12)
      meth(keysBtn, "SetWidth", w)
      meth(keysBtn, "ClearAllPoints")
      meth(keysBtn, "SetPoint", "LEFT", strip, "LEFT", x, 0)
      meth(keysBtn, "Show")
      x = x + w + GAP
    else meth(keysBtn, "Hide") end
  end
  meth(strip, "SetSize", x - GAP + 3, BTN_H + 6)
  S.Paint()
  local wide = tostring(compact) .. ":" .. floor(x)
  if wide ~= lastWide or not S.placed then lastWide = wide if not placing then S.PlaceStrip() end end
  if S.placed then meth(strip, "Show") end
end
--- textes qui changent sans changer la taille : nombres, mode choisi (aussi en combat : cadres non protégés)
function S.Paint()
  for _, st in ipairs(S.lastState or {}) do
    local b = btns[st.id]
    if b then
      local n = tonumber(st.count)
      if n and n > 0 then
        meth(b.badge, "SetText", n > 99 and "99" or tostring(n))
        meth(b.badge, "Show") meth(b.badgeBg, "Show")
        meth(b.badgeBg, "SetWidth", n > 9 and 17 or 12)
      else meth(b.badge, "SetText", "") meth(b.badge, "Hide") meth(b.badgeBg, "Hide") end
      if st.active then meth(b.sel, "Show") meth(b.line, "Show") else meth(b.sel, "Hide") meth(b.line, "Hide") end
    end
  end
end
local function sizeSig(states)
  local t = { S.db and S.db.text ~= false and "T" or "t", S.db and S.db.keys ~= false and "K" or "k", #S.MissingKeys() }
  for _, st in ipairs(states) do t[#t + 1] = (st.label or "") .. "|" .. (st.key or "") end
  return table.concat(t, ";")
end
local refreshPending, lastSizeSig = false, nil
--- relit les modes / nombres / touches ; redispose seulement si une largeur change (regroupé, ≤ 1 / 0,3 s)
function S.Refresh(now_)
  if not stripOn() then return end
  local function run()
    refreshPending = false
    if not stripOn() then return end
    local states = S.State()
    S.lastState = states
    local sig = sizeSig(states)
    if sig ~= lastSizeSig or not (strip and meth(strip, "IsShown")) then
      lastSizeSig = sig
      S.LayoutStrip()
    else S.Paint() end
  end
  if now_ then run() return end
  if refreshPending then return end
  refreshPending = true
  T:After(0.3, run)
end
--- ce que le joueur voit (tests, /tai boutons) : « Rendre (4) [Alt-1] * » par bouton
function S.Dump()
  local out = {}
  for _, st in ipairs(S.lastState or S.State()) do
    local s = st.label .. ((tonumber(st.count) or 0) > 0 and (" (" .. st.count .. ")") or "")
    if st.key then s = s .. " [" .. st.key .. "]" end
    if st.active then s = s .. " *" end
    out[#out + 1] = s
  end
  return out
end
--- (Window.ShowHUD) « Réinitialiser l'interface » : la bande revient à sa place automatique
function S.ShowHUD(resetPos)
  if not stripOn() then return false end
  if resetPos and S.char then S.char.strip = nil end
  lastWide = nil
  S.Refresh(true)
  if strip and resetPos then S.PlaceStrip() end
  return true
end
--- la bande tient lieu d'affichage (le panneau texte reste caché tant que le joueur ne l'ouvre pas)
function S.IsHUD() return stripOn() and true or false end

-- ===================================================================== barre d'actions (amarrage UIREDO)
local segs, segOrder = {}, {}       -- id -> { frame, label, order, inset, chrome, onUndock, docked }
S.segs = segs
local dock, handle, dividers, labels = nil, nil, {}, {}

local function docking() return S._active and S.db and S.db.dock ~= false end
-- (4.1) la barre d'actions n'est à l'écran que si le joueur la demande (les boutons rapides la remplacent)
local function barOn() return S.db and S.db.actionBar == true end

--- Un module amarre sa barre (appelé à la création de la barre, hors combat). opts = { id, label, order,
--- inset (px masqués à gauche : poignée propre au module), chrome = { régions à cacher une fois amarrée },
--- movable = id de déplaçable à retirer, onUndock = fn (remettre la barre seule) }. Renvoie vrai si amarrée.
function T:RegisterShortcutBar(frame, opts)
  if type(frame) ~= "table" or type(opts) ~= "table" or not opts.id then return false end
  local sg = segs[opts.id]
  if not sg then
    sg = {}
    segs[opts.id] = sg
    segOrder[#segOrder + 1] = opts.id
    table.sort(segOrder, function(a, b) return (segs[a].order or 9) < (segs[b].order or 9) end)
  end
  sg.frame, sg.label, sg.order, sg.inset = frame, opts.label or opts.id, opts.order or 9, opts.inset or 0
  sg.chrome, sg.onUndock, sg.movable = opts.chrome or {}, opts.onUndock, opts.movable
  table.sort(segOrder, function(a, b) return (segs[a].order or 9) < (segs[b].order or 9) end)
  if not sg.hooked and type(frame.HookScript) == "function" then
    sg.hooked = true
    local function changed() S.Changed() end
    pcall(frame.HookScript, frame, "OnSizeChanged", changed)
    pcall(frame.HookScript, frame, "OnShow", changed)
    pcall(frame.HookScript, frame, "OnHide", changed)
  end
  if docking() then S.Changed() end
  return docking()
end
--- La barre d'un module est-elle amarrée ? (le module ne la place plus lui-même)
function S.Docked(frame)
  for _, sg in pairs(segs) do if sg.frame == frame and sg.docked then return true end end
  return false
end
function T:ShortcutDocked(frame) return S.Docked(frame) end

-- ===================================================================== cadre
local function locked() return (S.db and S.db.locked) or (T.FramesLocked and T:FramesLocked()) or false end
local function savePos()
  if not dock then return end
  -- (PLACEBUG 4.0.1) le crochet du cœur a reposé la barre sur UIParent depuis son rectangle réel (ancrage explicite) ;
  -- sans lui, même calcul ici — jamais l'ancrage du client relu tel quel, jamais « nil → 0 »
  if T.AnchorFromRect then T:AnchorFromRect(dock) end
  local pt, _, rp, x, y = meth(dock, "GetPoint")
  if S.char and okAnchor(pt, x, y) then S.char.pos = { pt, okAnchor(rp, 0, 0) and rp or pt, floor(x + 0.5), floor(y + 0.5) } end
end
local function placeDock()
  if not dock or inCombat() then return end
  local p = S.char and S.char.pos
  meth(dock, "SetScale", max(0.7, math.min(1.5, (tonumber(S.db and S.db.scale) or 100) / 100)))
  -- (PLACEBUG) place incomplète / invalide = la place par défaut ; un SetPoint raté ne laisse jamais la barre sans ancrage
  if type(p) == "table" and not okAnchor(p[1], p[3], p[4]) then S.char.pos, p = nil, nil end
  if type(p) == "table" then
    local rp = okAnchor(p[2], 0, 0) and p[2] or p[1]
    meth(dock, "ClearAllPoints")
    if pcall(dock.SetPoint, dock, p[1], G.UIParent, rp, p[3], p[4]) then return end
  end
  if T.SetAnchor then T:SetAnchor(dock, "BOTTOM", 0, 190)
  else meth(dock, "ClearAllPoints") meth(dock, "SetPoint", "BOTTOM", G.UIParent, "BOTTOM", 0, 190) end
end
S.Place = placeDock

local function tip(owner)
  local tt = G.GameTooltip
  if not tt then return end
  meth(tt, "SetOwner", owner, "ANCHOR_TOP")
  meth(tt, "AddLine", "Barre d'actions", 1, 0.82, 0.2)
  meth(tt, "AddLine", "Seulement les boutons utiles maintenant : quêtes, voyage, combat, familier.", 0.85, 0.85, 0.85, true)
  meth(tt, "AddLine", "Survole un bouton : ce qu'il fait, pourquoi maintenant, sa touche.", 0.85, 0.85, 0.85, true)
  meth(tt, "AddLine", locked() and "Barre verrouillée (Options > Boutons rapides)" or "Glisser : déplacer (hors combat)", 0.4, 0.8, 1)
  meth(tt, "AddLine", "Clic droit : réglages de la barre", 0.4, 0.8, 1)
  meth(tt, "Show")
end

local function ensureDock()
  if dock then return dock end
  if inCombat() or type(G.CreateFrame) ~= "function" then return nil end
  local k = tok()
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIShortcuts", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if not ok or not f then return nil end
  dock = f
  meth(f, "SetSize", k.handle + 2 * k.pad, k.size + 2 * k.pad)
  meth(f, "SetFrameStrata", "MEDIUM")
  meth(f, "SetClampedToScreen", true)
  meth(f, "SetMovable", true)
  local th = Th()
  if th then th:Skin(f, "card") end
  -- poignée : trois points or, glisser = déplacer, clic droit = options
  local okH, h = pcall(G.CreateFrame, "Button", nil, f)
  if okH and h then
    handle = h
    meth(h, "SetSize", k.handle, k.size)
    meth(h, "SetPoint", "LEFT", f, "LEFT", k.pad, 0)
    meth(h, "RegisterForClicks", "RightButtonUp")
    meth(h, "RegisterForDrag", "LeftButton")
    h.dots = {}
    for i = 1, 3 do
      local d = meth(h, "CreateTexture", nil, "ARTWORK")
      if d then
        meth(d, "SetSize", 3, 3)
        meth(d, "SetPoint", "CENTER", h, "CENTER", 0, (2 - i) * 7)
        if th then meth(d, "SetColorTexture", th:Color("gold")) else meth(d, "SetColorTexture", 1, 0.82, 0.2, 1) end
        h.dots[i] = d
      end
    end
    local hl = meth(h, "CreateTexture", nil, "HIGHLIGHT")
    if hl then meth(hl, "SetAllPoints", h); meth(hl, "SetColorTexture", 1, 1, 1, 0.08) end
    meth(h, "SetScript", "OnDragStart", function() if not inCombat() and not locked() then meth(f, "StartMoving") end end)
    meth(h, "SetScript", "OnDragStop", function() meth(f, "StopMovingOrSizing") savePos() end)
    meth(h, "SetScript", "OnClick", function()
      local Wn = T.Window
      if Wn and Wn._active and Wn.Open then
        if Wn.char then Wn.char.optPage = "Shortcuts" end
        Wn.Open("options")
      elseif T.OpenOptions then T:OpenOptions("Shortcuts") end
    end)
    meth(h, "SetScript", "OnEnter", tip)
    meth(h, "SetScript", "OnLeave", function() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end)
  end
  placeDock()
  if T.RegisterMovable then
    T:RegisterMovable(f, "shortcuts", "Barre d'actions", {
      onMoved = function(point, x, y, sc)
        if S.char then S.char.pos = { point, point, floor(x or 0), floor(y or 0) } end
        if sc and S.db then S.db.scale = floor(sc * 100 + 0.5) end
      end,
      onReset = function() if S.char then S.char.pos = nil end placeDock() end,
      -- (PLACEBUG) glissée par sa poignée (même avant 4.0.1) = posée par le joueur : rien ne la reprend
      isPlaced = function() return S.char and type(S.char.pos) == "table" or false end,
    })
  end
  meth(f, "Hide")
  return dock
end
S.Ensure = ensureDock
function S.Frame() return dock end

local function divider(i)
  local d = dividers[i]
  if d or not dock then return d end
  d = meth(dock, "CreateTexture", nil, "ARTWORK")
  if d then
    meth(d, "SetWidth", 1)
    local th = Th()
    if th then meth(d, "SetColorTexture", th:Color("borderSoft")) else meth(d, "SetColorTexture", 0.78, 0.62, 0.28, 0.35) end
    dividers[i] = d
  end
  return d
end
-- titre d'un groupe dans `w` px (petite police ≈ 5,4 px / lettre) : entier, sinon sa première partie, sinon rien
local function fitLabel(txt, w)
  txt = tostring(txt or "")
  local function fits(t) local _, n = t:gsub("[^\128-\191]", "") return n * 5.4 <= w end
  if txt == "" then return nil end
  if fits(txt) then return txt end
  local head = txt:match("^(.-) · ")
  if head and fits(head) then return head end
  return nil
end
S.FitLabel = fitLabel
local function label(i)
  local l = labels[i]
  if l or not dock then return l end
  local th = Th()
  l = th and th:Text(dock, "S", "muted") or meth(dock, "CreateFontString", nil, "OVERLAY", "GameFontDisableSmall")
  if l then meth(l, "SetJustifyH", "LEFT"); meth(l, "SetWordWrap", false) end
  labels[i] = l
  return l
end

-- ===================================================================== amarrage / disposition (hors combat)
local function dockSeg(sg)
  local f = sg.frame
  if sg.docked or not f then return end
  meth(f, "SetParent", dock)
  meth(f, "SetFrameStrata", "MEDIUM")
  if meth(f, "GetFrameLevel") and meth(dock, "GetFrameLevel") then meth(f, "SetFrameLevel", (meth(dock, "GetFrameLevel") or 1) + 1) end
  meth(f, "SetScale", 1)
  for _, r in ipairs(sg.chrome) do meth(r, "Hide") end
  local th = Th()
  if th and th.IsSkinned and th:IsSkinned(f) then th:Skin(f, "hud", { noBg = true, border = false }) end
  if sg.movable and T.UnregisterMovable then T:UnregisterMovable(sg.movable) end
  sg.docked = true
end
local function undockSeg(sg)
  local f = sg.frame
  if not (sg.docked and f) then return end
  meth(f, "SetParent", G.UIParent)
  for _, r in ipairs(sg.chrome) do meth(r, "Show") end
  local th = Th()
  if th and th.IsSkinned and th:IsSkinned(f) then th:Skin(f, "hud") end
  sg.docked = false
  if sg.onUndock then T.safecall(sg.onUndock) end
end

local lastSig, segSig   -- (définies plus bas) dernière disposition faite
--- Dispose les segments amarrés côte à côte ; cache la barre quand aucun ne sert.
function S.Layout()
  if inCombat() then T:RunOutOfCombat("Shortcuts:layout", S.Layout) return end
  if not docking() then
    for _, id in ipairs(segOrder) do undockSeg(segs[id]) end
    if dock then meth(dock, "Hide") end
    return
  end
  if not ensureDock() then return end
  local k = tok()
  local x = k.pad + k.handle + k.gap
  local n, h = 0, k.size + 2 * k.pad
  local showLabels = S.db.labels ~= false
  for _, id in ipairs(segOrder) do
    local sg = segs[id]
    local f = sg.frame
    if f then
      dockSeg(sg)
      local w = tonumber(meth(f, "GetWidth")) or 0
      local vis = (meth(f, "IsShown") and w - sg.inset > 8) and true or false
      if vis then
        n = n + 1
        if n > 1 then
          local d = divider(n - 1)
          if d then
            meth(d, "ClearAllPoints")
            meth(d, "SetPoint", "TOPLEFT", dock, "TOPLEFT", x, -k.pad)
            meth(d, "SetPoint", "BOTTOMLEFT", dock, "BOTTOMLEFT", x, k.pad)
            meth(d, "Show")
          end
          x = x + 1 + k.gap
        end
        meth(f, "ClearAllPoints")
        meth(f, "SetPoint", "LEFT", dock, "LEFT", x - sg.inset, 0)
        local l = label(n)
        if l then
          -- (4.0 QA) le titre tient dans la largeur de son groupe (sinon il déborde sur le suivant) : titre court
          -- (« Quêtes » de « Quêtes · voyage ») quand c'est étroit, rien s'il ne tient pas
          local wl = w - sg.inset
          local txt = fitLabel(sg.label, wl)
          meth(l, "ClearAllPoints")
          meth(l, "SetPoint", "BOTTOMLEFT", dock, "TOPLEFT", x, 3)
          meth(l, "SetWidth", max(1, wl))
          meth(l, "SetText", txt or "")
          if showLabels and txt then meth(l, "Show") else meth(l, "Hide") end
        end
        x = x + (w - sg.inset)
        h = max(h, (tonumber(meth(f, "GetHeight")) or 0))
      end
    end
  end
  for i = n, #dividers do if i >= n and dividers[i] then meth(dividers[i], "Hide") end end
  for i = n + 1, #labels do meth(labels[i], "Hide") end
  S.shown = n
  if n == 0 or not barOn() then meth(dock, "Hide") lastSig = segSig() return end
  meth(dock, "SetSize", x + k.pad, h)
  meth(dock, "Show")
  lastSig = segSig()
end
local pending = false
-- signature bon marché des segments (affiché, largeur, hauteur) + réglages : rien de neuf = rien à refaire
function segSig()
  local t = { S.db and S.db.labels ~= false and "L" or "l", dock and (meth(dock, "IsShown") and "S" or "s") or "-" }
  for _, id in ipairs(segOrder) do
    local f = segs[id].frame
    t[#t + 1] = id .. (meth(f, "IsShown") and "1" or "0") .. ":" .. floor(tonumber(meth(f, "GetWidth")) or 0)
      .. "x" .. floor(tonumber(meth(f, "GetHeight")) or 0) .. (segs[id].docked and "d" or "u")
  end
  return table.concat(t, "|")
end
S.Sig = segSig
--- Un segment a changé (taille, affichage) : une disposition, regroupée, hors combat — seulement si quelque
--- chose a vraiment changé (QuickFight se redessine jusqu'à 4 fois par seconde).
local pendingAt = -10
function S.Changed()
  if not docking() then return end
  -- (4.0 QA) un rappel perdu (demandé pendant le code d'un module coupé entre-temps : T:After le saute) ne bloque
  -- plus la barre pour toujours : au-delà de 2 s, on redemande
  local t = tonumber((G.GetTime and G.GetTime())) or 0
  if pending and t - pendingAt < 2 then return end
  if segSig() == lastSig then return end
  pending, pendingAt = true, t
  T:After(0, function() pending = false S.Layout() end)
end

function S:OnEnable()
  T:After(0.5, S.Layout)
  -- boutons rapides : dessinés une fois l'écran posé, puis sur les seuls événements qui changent un nombre / une touche
  T:After(1.5, function() if S._active then S.Refresh(true) end end)
  local function soon() S.Refresh() end
  for _, ev in ipairs({ "QUEST_LOG_UPDATE", "ZONE_CHANGED_NEW_AREA", "UPDATE_BINDINGS" }) do self:RegisterEvent(ev, soon) end
  -- le groupe se forme (cadres de groupe du jeu), l'écran change : la bande non posée par le joueur se repose
  local function replace()
    if not (strip and stripOn()) or (S.char and S.char.strip) then return end
    T:RunOutOfCombat("Shortcuts:strip", S.PlaceStrip)
  end
  for _, ev in ipairs({ "GROUP_ROSTER_UPDATE", "PLAYER_ENTERING_WORLD", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED" }) do
    self:RegisterEvent(ev, function() soon() T:After(1, replace) end)
  end
  self:On("LAYOUT_DONE", replace)
  self:On("QUEST_MODE_CHANGED", function() S.Refresh(true) end)
  self:On("QUEST_BRAIN_UPDATED", soon)
  self:On("NEXT_CHANGED", function() if S.lastState then S.Refresh() end end)
  -- (ONBOARD) la visite guidée montre les boutons rapides (sinon la barre d'actions si le joueur l'a remise)
  local Tr = T.Tour
  if type(Tr) == "table" and type(Tr.Target) == "function" then
    Tr.Target("quick", function()
      if strip and meth(strip, "IsVisible") then return strip end
      return (dock and meth(dock, "IsVisible")) and dock or nil
    end)
  end
end
function S:OnDisable()
  T:RunOutOfCombat("Shortcuts:layout", function()
    for _, id in ipairs(segOrder) do undockSeg(segs[id]) end
    if dock then meth(dock, "Hide") end
    if strip then meth(strip, "Hide") end
  end)
end
function S:OnOptionChanged(key)
  if key == "strip" or key == "text" or key == "keys" or key == "keysHint" then
    lastSizeSig, lastWide, S.compact = nil, nil, nil
    if stripOn() then S.Refresh(true) elseif strip then T:RunOutOfCombat("Shortcuts:strip", function() meth(strip, "Hide") end) end
    return
  end
  if key == "stripScale" then lastWide = nil S.PlaceStrip() return end
  if key == "stripLocked" then return end
  if key == "scale" then placeDock() end
  S.Layout()
end

T:RegisterSlash("boutons", function(arg)
  arg = tostring(arg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if arg == "touches" then S.SuggestKeys() return end
  if DEF[arg] or arg == "mode" then S.Do(arg) return end
  if S.db then S.db.strip = S.db.strip == false end
  S:OnOptionChanged("strip")
end, "boutons rapides (/tai boutons [rendre|prendre|suivant|zone|groupe|journal|mode|touches])")
