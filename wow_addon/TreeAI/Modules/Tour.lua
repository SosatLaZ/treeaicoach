-- TreeAI — module Tour (agent ONBOARD) : comprendre TreeAI en 60 secondes, à la souris.
--  * « Visite guidée » (7 étapes au plus) : une petite bulle + une flèche sur le VRAI élément (pastille de la
--    minimap, prochaine étape, zone de travail, boussole, raccourcis, Fonctions bêta, Erreurs). Suivant / Passer.
--    Montrée une fois (premier moment calme après l'assistant de démarrage), rejouable : menu de la pastille →
--    « Visite guidée », bouton des options, /tai visite. Une étape dont l'élément n'existe pas (module coupé, cadre
--    caché) est sautée. Jamais en combat (bulle cachée, reprise après), ni mort, ni en vol.
--  * Astuces au bon moment (« Première fois à l'HV : … ») : une fois chacune, au plus une toutes les 10 min, jamais
--    en combat ni pendant la visite ; « Couper les astuces » = plus jamais (réglage du module).
--  * /tai aide (et /tai seul) : une bulle qui montre la pastille ; « /tai aide tout » = la liste des commandes.
-- Cibles par contrats stables : T.Tour.Target(id, cadre | fonction), ids RegisterMovable (« card », « panel »,
-- « compass », « quick »…), entrées du menu Hub (H.layout), lignes à id (« plan:step », « zone:pick »). Nos cadres
-- sont posés sur UIParent à la position LUE de la cible, jamais ancrés sur elle (pas de protection implicite) ;
-- rien de protégé, aucun cadre Blizzard modifié, rien dans le chat (sauf la réponse à /tai aide en combat).
-- Coût : aucun AutoRefresh ; un réveil ponctuel tant qu'il y a de quoi attendre, un ticker 1 s pendant qu'une bulle est là.
-- État : TreeAIDB.tour (compte) = { done, step, seen = { [astuce] = date }, last }. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

-- mode sûr : la visite ne fait que lire des cadres à nous et en poser trois → robuste par construction, allumée
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Tour = true end
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Tour then
  T.MODULE_INFO.Tour = { lite = true, desc = "Visite guidée et astuces (une fois chacune)" }
end
do   -- page d'options rangée dans « Outils » (comme Quick / Finder se rangent eux-mêmes)
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[#T.OPTIONS_FAMILY]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Tour" then has = true end end
    if not has then table.insert(fam.mods, math.min(#fam.mods + 1, 4), "Tour") end
  end
end

local M = T:RegisterModule("Tour", {
  title = "Visite guidée",
  description = "Où cliquer dans TreeAI, en 60 secondes. Et une astuce au bon moment, une seule fois chacune.",
  defaults = { enabled = true, tips = false },   -- (4.0.1) calme : les astuces se rallument dans les options
  options = {
    { key = "tips", type = "toggle", label = "Astuces au bon moment (coupées par défaut ; une fois chacune, au plus une toutes les 10 min)" },
    { type = "button", label = "Lancer la visite guidée", onClick = function() T.Tour.Start("options") end },
    { type = "button", label = "Revoir les astuces déjà vues", onClick = function() T.Tour.ResetTips() end },
  },
})
T.Tour = M
M.TOUR_V = 1        -- monter ce numéro = la visite est reproposée une fois à tout le monde
M.TIP_GAP = 600     -- s (horloge du serveur, survit au /reload) entre deux astuces
M.TIP_SHOW = 30     -- s : une astuce ou la bulle d'aide s'efface seule
M.TIP_SEEN = 8      -- s : vue au moins ce temps = vue pour de bon (sinon elle pourra revenir)
M.LOGIN_QUIET = 8   -- s après la connexion avant de montrer la visite
M.CALM_FOR = 3      -- s de calme d'affilée avant de la montrer
M.TIPS_AFTER = 60   -- s après la connexion avant la première astuce

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local atan2 = math.atan2
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function meth(o, m, ...)
  local f = type(o) == "table" and o[m]
  if type(f) == "function" then return pass(pcall(f, o, ...)) end
end
local function now() return tonumber((try(G.GetTime))) or 0 end
local function wall() return tonumber((try(G.GetServerTime))) or tonumber((try(G.time))) or 0 end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function Th() local t = T.Theme if type(t) == "table" and type(t.Color) == "function" then return t end end
local function color(token, r, g, b, a)
  local th = Th()
  if th then
    local cr, cg, cb, ca = th:Color(token)
    if cr then return cr, cg, cb, ca or 1 end
  end
  return r, g, b, a or 1
end
local function modOn(name)
  local m = T.modules and T.modules[name]
  return type(m) == "table" and m.IsEnabled and m:IsEnabled() and true or false
end
local function visible(f) return type(f) == "table" and meth(f, "IsVisible") and true or false end

-- ===================================================================== état (compte, survit au /reload)
local function store()
  local db = T.db
  if type(db) ~= "table" then
    M._tmp = M._tmp or { seen = {} }
    return M._tmp
  end
  local s = db.tour
  if type(s) ~= "table" then s = {}; db.tour = s end
  if type(s.seen) ~= "table" then s.seen = {} end
  return s
end
M.Store = store
function M.Done() return (tonumber(store().done) or 0) >= M.TOUR_V end

-- ===================================================================== cibles (contrats, jamais de position devinée)
M.targets = {}
--- Contrat : T.Tour.Target(id, cadre | function() return cadre end) = l'élément que la visite montre pour `id`
--- (« hub », « next », « zone », « compass », « quick », « beta », « bug ») ; passe avant les repères intégrés.
function M.Target(id, f) if type(id) == "string" then M.targets[id] = f end end
local function fromRegistry(id)
  local t = M.targets[id]
  if type(t) == "function" then t = try(t) end
  if visible(t) then return t end
end
local function movable(id)
  if type(T.GetMovables) ~= "function" then return nil end
  local list = T:GetMovables()
  local m = type(list) == "table" and list[id]
  return type(m) == "table" and m.frame or nil
end
local function firstVisible(...)
  for i = 1, select("#", ...) do
    local f = select(i, ...)
    if visible(f) then return f end
  end
end
-- une ligne cliquable porte sa ligne de contrat (`entry` du panneau, `e` des menus…) avec un id stable
local ROW_KEYS = { "entry", "e", "line", "data" }
local function rowEntry(f)
  for i = 1, #ROW_KEYS do
    local v = rawget(f, ROW_KEYS[i])
    if type(v) == "table" and rawget(v, "id") ~= nil then return v end
  end
end
local function children(f)
  if type(f) ~= "table" or type(f.GetChildren) ~= "function" then return {} end
  local r = { pcall(f.GetChildren, f) }
  if not r[1] then return {} end
  table.remove(r, 1)
  return r
end
local function scan(f, test, depth)
  if type(f) ~= "table" or depth > 6 or not visible(f) then return nil end
  local e = rowEntry(f)
  if e and test(e, f) then return f end
  for _, c in ipairs(children(f)) do
    local r = scan(c, test, depth + 1)
    if r then return r end
  end
end
local function rowRoots()
  local out = {}
  for _, f in ipairs({ T.GetPanel and T:GetPanel() or false, rawget(G, "TreeAIMainWindow") or false,
    rawget(G, "TreeAICard") or false }) do
    if f then out[#out + 1] = f end
  end
  return out
end
--- Ligne visible dont l'id de contrat vaut `id` (panneau, fenêtre TreeAI, carte « Prochaine étape »).
function M.FindRow(id)
  local test = function(e) return e.id == id end
  for _, root in ipairs(rowRoots()) do
    local f = scan(root, test, 0)
    if f then return f end
  end
end
local function hubRow(id)
  local H = T.Hub
  if not (H and type(H.layout) == "table") then return nil end
  for _, e in ipairs(H.layout) do
    if e.kind == "item" and e.id == id and visible(e.button) then return e.button end
  end
end
local function hubButton()
  local H = T.Hub
  local b = H and H.Button and try(H.Button) or nil
  return firstVisible(b, rawget(G, "TreeAIMinimapButton"))
end
local function windowOn() local Wn = T.Window return type(Wn) == "table" and Wn._active and true or false end
local function openHub(a)
  local H = T.Hub
  if not (H and H.Toggle and H.IsOpen) then return false end
  if not H.IsOpen() then
    H.mode = "main"
    try(H.Toggle)
    if a then a.hubOpened = true end
  elseif H.mode ~= "main" then
    H.mode = "main"
    try(H.Fill)
  end
  return H.IsOpen() and true or false
end
local function closeHub(a)
  local H = T.Hub
  if a and a.hubOpened and H and H.Close then try(H.Close) end
  if a then a.hubOpened = nil end
end
local function zoneMenu()
  local Z = T.ZonePlan
  local f = Z and Z.GetMenu and try(Z.GetMenu, Z) or nil
  return f
end

-- ===================================================================== étapes (ordre = ce qui compte le plus)
local function hasHubEntry(id)
  local H = T.Hub
  if not (modOn("Hub") and H and H.Entries) then return false end
  local mode = H.mode
  H.mode = "main"   -- la liste bêta ouverte ne cache pas les entrées du menu principal
  local list = try(H.Entries)
  H.mode = mode
  for _, e in ipairs(type(list) == "table" and list or {}) do if e.id == id then return true end end
  return false
end
-- (polices du jeu : ni « → » ni « ▾ », des carrés vides en jeu ; « > » comme le reste de TreeAI)
local REPLAY = "La rejouer : clic droit sur la pastille > « Visite guidée »."
M.STEPS = {
  { id = "hub", title = "Le menu TreeAI",
    can = function() return fromRegistry("hub") ~= nil or hubButton() ~= nil end,
    find = function() return fromRegistry("hub") or hubButton() end,
    text = function()
      if windowOn() then return "Tout TreeAI part d'ici. Clic : la fenêtre TreeAI · clic droit : le menu rapide." end
      return "Tout TreeAI part d'ici. Clic : le menu · clic droit : les options."
    end,
    note = function(a) if a.pos == 1 then return "« Passer » = ne plus la voir. " .. REPLAY end end },
  { id = "next", title = "Ta prochaine étape",
    can = function()
      return fromRegistry("next") ~= nil or visible(movable("card")) or visible(rawget(G, "TreeAICard"))
        or visible(T.GetPanel and T:GetPanel() or nil)
    end,
    find = function()
      local f = fromRegistry("next")
      if f then return f, "card" end
      f = firstVisible(movable("card"), rawget(G, "TreeAICard"))
      if f then return f, "card" end
      f = M.FindRow("plan:step")
      if f then return f, "card" end
      f = firstVisible(movable("panel"), T.GetPanel and T:GetPanel() or nil)
      if f then return f, "panel" end
    end,
    text = function(_, kind)
      if kind == "panel" then
        return "Quoi faire maintenant. Clic sur une ligne : l'action ; survol : le pourquoi."
      end
      return "Ce qu'il faut faire maintenant. Clic sur une étape : la boussole t'y guide."
    end },
  { id = "zone", title = "Ta zone de quêtes",
    can = function() return fromRegistry("zone") ~= nil or modOn("ZonePlan") end,   -- cible enregistrée ET visible
    prepare = function(a)
      if fromRegistry("zone") or M.FindRow("zone:pick") then return end
      local Z = T.ZonePlan
      if not (modOn("ZonePlan") and Z and Z.ShowZoneMenu) then return end
      if visible(zoneMenu()) and Z.menuKind == "zone" then return end
      try(Z.ShowZoneMenu, Z)
      a.zoneOpened = true
    end,
    find = function()
      local f = fromRegistry("zone") or M.FindRow("zone:pick")
      if f then return f, "row" end
      f = zoneMenu()
      if visible(f) then return f, "menu" end
    end,
    cleanup = function(a)
      if a.zoneOpened then
        local f = zoneMenu()
        if f then meth(f, "Hide") end
        a.zoneOpened = nil
      end
    end,
    text = function() return "Choisis où quêter : TreeAI y range tes arrêts dans le meilleur ordre." end,
    note = function(_, kind)
      if kind == "menu" then return "Plus tard : la ligne « Zone » du trajet, ou /tai zone." end
    end },
  { id = "compass", title = "La boussole",
    can = function() return fromRegistry("compass") ~= nil or visible(movable("compass")) or visible(rawget(G, "TreeAICompassFrame")) end,
    find = function() return fromRegistry("compass") or firstVisible(movable("compass"), rawget(G, "TreeAICompassFrame")) end,
    text = function() return "La flèche vise ta prochaine étape, avec la distance et le temps de trajet." end },
  { id = "quick", title = "Les actions rapides",
    can = function()
      return fromRegistry("quick") ~= nil or firstVisible(movable("quick"), movable("quickfight"), movable("quicktravel")) ~= nil
    end,
    find = function()
      return fromRegistry("quick") or firstVisible(movable("quick"), movable("quickfight"), movable("quicktravel"))
    end,
    text = function()
      local Sc = T.Shortcuts
      if type(Sc) == "table" and type(Sc.Strip) == "function" and visible(Sc.Strip()) then
        return "Un clic, une action : rendre, prendre, étape suivante, zone, sacs, équipement, groupe. La pastille « Guidage quêtes » allume ou coupe tout le guidage."
      end
      return "Des boutons qui n'apparaissent que s'ils servent. Un clic, une action."
    end },
  { id = "beta", title = "Fonctions bêta", hub = true,
    can = function() return M.targets.beta ~= nil or hasHubEntry("profil") end,
    prepare = function(a) if not fromRegistry("beta") then openHub(a) end end,
    find = function() return fromRegistry("beta") or hubRow("profil") end,
    text = function() return "Testées hors du jeu seulement, donc coupées. Un clic ici pour en essayer une." end,
    note = function() return "Par exemple : raccourcis, HV, groupe, donjon." end },   -- (4.0.1) la zone de travail est allumée d'office
  { id = "bug", title = "Un bug ?", hub = true,
    can = function() return M.targets.bug ~= nil or hasHubEntry("erreurs") end,
    prepare = function(a) if not fromRegistry("bug") then openHub(a) end end,
    find = function() return fromRegistry("bug") or hubRow("erreurs") end,
    text = function() return "Clic ici, puis /reload : l'appli écrit TreeAI-erreurs.txt. Envoie ce fichier." end },
}
local STEP_BY_ID = {}
for i, st in ipairs(M.STEPS) do STEP_BY_ID[st.id] = i end

local function okCall(fn, ...) if type(fn) ~= "function" then return true end return pass(pcall(fn, ...)) and true or false end
--- Étapes montrables maintenant (indices dans M.STEPS), sans rien ouvrir.
function M.Plan()
  local out = {}
  for i, st in ipairs(M.STEPS) do
    if okCall(st.can) then out[#out + 1] = i end
  end
  return out
end
--- (tests) Cadre visé par l'étape `id` maintenant, sans rien ouvrir : cadre, genre.
function M.Resolve(id)
  local st = M.STEPS[STEP_BY_ID[id] or 0]
  if not st then return nil end
  return pass(pcall(st.find))
end

-- ===================================================================== astuces (une fois chacune)
local function groupOn() return modOn("Group") end   -- (4.0) Squad / Missions / Team = le module Groupe
local function inGroup()
  if try(G.IsInGroup) then return true end
  return (tonumber((try(G.GetNumGroupMembers))) or 0) > 0 or (tonumber((try(G.GetNumPartyMembers))) or 0) > 0
end
local function suggestionRow()
  local p = T.GetPanel and T:GetPanel() or nil
  if not visible(p) then return nil end
  for _, c in ipairs(children(p)) do
    local e = visible(c) and rawget(c, "entry")
    if type(e) == "table" and e.source == nil and e.time ~= nil and not e.danger then return c end
  end
end
M.TIPS = {
  { id = "ah", title = "Première fois à l'HV",
    text = "Mode rafale : une touche = une action sûre (acheter, annuler une vente dépassée, poster).",
    note = "Bouton « Lier une touche » en bas du panneau TreeAI de l'HV.",
    when = function()
      local A = T.modules and T.modules.Auction
      local D = T.AHDesk   -- (5.1 AH) le bureau d'achat-revente collé à l'HV remplace l'ancienne fenêtre
      return modOn("Auction") and type(A) == "table" and (visible(A.win) or (type(D) == "table" and visible(D.frame)))
    end,
    target = function()
      local A = T.modules and T.modules.Auction
      local D = T.AHDesk
      local d = type(D) == "table" and visible(D.frame) and D.frame or nil
      if d then return firstVisible(d.bind, d.next, d) end
      local w = type(A) == "table" and A.win or nil
      return firstVisible(type(w) == "table" and w.rafBtn or nil, w)
    end,
    gone = function()
      local A = T.modules and T.modules.Auction
      local D = T.AHDesk
      return not ((type(A) == "table" and visible(A.win)) or (type(D) == "table" and visible(D.frame)))
    end },
  { id = "questitem", title = "Objet de quête",
    text = "Clic : utiliser l'objet de la quête. Maj + glisser : le déplacer.",
    when = function()
      local Q = T.modules and T.modules.Quests
      return modOn("Quests") and type(Q) == "table" and visible(Q.GetItemButton and try(Q.GetItemButton) or nil)
    end,
    target = function() local Q = T.modules and T.modules.Quests return Q and Q.GetItemButton and try(Q.GetItemButton) or nil end },
  { id = "dismiss", title = "Trop de conseils ?",
    text = "Clic droit sur un conseil : il disparaît 2 h. Un vrai danger revient toujours.",
    when = function() return now() - (M._loginAt or 0) >= 300 and suggestionRow() ~= nil end,
    target = suggestionRow },
  { id = "group", title = "En groupe",
    text = function()
      if groupOn() then return "Page « Groupe » du menu : vie et zone de tes amis, et leurs quêtes s'ils ont TreeAI." end
      return "Menu > Fonctions bêta > Groupe : vie et zone de tes amis, et leurs quêtes s'ils ont TreeAI."
    end,
    when = function() return inGroup() and hubButton() ~= nil end,
    target = hubButton },
  { id = "error", title = "Petit souci détecté",
    text = "Menu > Erreurs, puis /reload : l'appli écrit TreeAI-erreurs.txt. Envoie-le.",
    when = function() return type(T._modErrors) == "table" and next(T._modErrors) ~= nil and hubButton() ~= nil end,
    target = hubButton },
}

-- ===================================================================== bulle + flèche + halo (cadres à nous)
local WHITE = "Interface\\Buttons\\WHITE8X8"
local ARROW = "Interface\\Minimap\\MinimapArrow"   -- même flèche que la boussole (pointe vers le haut)
local BOX_W, PAD, GAP, ARROW_S = 270, 10, 34, 30
local box, glow, arrow
M.UI = {}

local function fontString(parent, size, token, r, g, b)
  local th = Th()
  local fs
  if th and th.Text then fs = th:Text(parent, size, token) end
  if not fs then
    fs = parent:CreateFontString(nil, "OVERLAY", size == "S" and "GameFontHighlightSmall" or "GameFontHighlight")
    if fs and r then meth(fs, "SetTextColor", r, g, b) end
  end
  meth(fs, "SetJustifyH", "LEFT")
  meth(fs, "SetWordWrap", true)
  return fs
end
local function setLabel(b, t)
  if not b then return end
  if b.SetLabel then meth(b, "SetLabel", t) else meth(b, "SetText", t) end
end
local function newButton(parent, w, onClick, primary)
  local th = Th()
  local b
  if th and th.Button then b = th:Button(parent, "", w, 20, onClick) end
  if not b then
    local ok, nb = pcall(G.CreateFrame, "Button", nil, parent, "UIPanelButtonTemplate")
    if ok and nb then
      b = nb
      meth(b, "SetSize", w, 20)
      meth(b, "SetScript", "OnClick", onClick)
    end
  end
  if b and primary then
    local st = th and type(th._buttons) == "table" and th._buttons[b]
    if st then st.selected = true; pcall(th._PaintButton, th, b) end
    if b.label then meth(b.label, "SetTextColor", color("gold", 1, 0.82, 0.2)) end
  end
  return b
end

local onPrimary, onSecondary
local function build()
  if box then return box end
  if type(G.CreateFrame) ~= "function" then return nil end
  local tpl = G.BackdropTemplateMixin and "BackdropTemplate" or nil
  box = G.CreateFrame("Frame", "TreeAITourCallout", G.UIParent, tpl)
  meth(box, "SetFrameStrata", "FULLSCREEN_DIALOG")
  meth(box, "SetClampedToScreen", true)
  meth(box, "EnableMouse", true)
  meth(box, "SetSize", BOX_W, 110)
  -- (4.0 QA) Échap ferme la bulle (UISpecialFrames) au lieu d'ouvrir le menu du jeu par-dessus
  local sf = rawget(G, "UISpecialFrames")
  if type(sf) == "table" then sf[#sf + 1] = "TreeAITourCallout" end
  meth(box, "SetScript", "OnHide", function()
    if M._hiding then return end            -- caché par nous (étape suivante, combat…) : rien à faire
    if M.mode == "tour" then M.Finish("skip")
    elseif M.mode == "tip" then M.HideTip(true)
    elseif M.mode == "pointer" then M.HidePointer() end
  end)
  local th = Th()
  if not (th and th.Skin and th:Skin(box, "window")) and box.SetBackdrop then
    meth(box, "SetBackdrop", { bgFile = WHITE, edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    meth(box, "SetBackdropColor", 0.05, 0.06, 0.08, 0.96)
    meth(box, "SetBackdropBorderColor", 0.78, 0.62, 0.28, 0.95)
  end
  box.head = fontString(box, "M", "gold", 1, 0.82, 0.2)
  meth(box.head, "SetPoint", "TOPLEFT", box, "TOPLEFT", PAD, -PAD)
  meth(box.head, "SetWidth", BOX_W - 2 * PAD - 40)
  meth(box.head, "SetWordWrap", false)
  box.count = fontString(box, "S", "muted", 0.6, 0.6, 0.62)
  meth(box.count, "SetPoint", "TOPRIGHT", box, "TOPRIGHT", -PAD, -PAD - 1)
  meth(box.count, "SetJustifyH", "RIGHT")
  box.body = fontString(box, "M", "text", 0.92, 0.92, 0.92)
  meth(box.body, "SetWidth", BOX_W - 2 * PAD)
  meth(box.body, "SetPoint", "TOPLEFT", box.head, "BOTTOMLEFT", 0, -5)
  box.note = fontString(box, "S", "muted", 0.6, 0.6, 0.62)
  meth(box.note, "SetWidth", BOX_W - 2 * PAD)
  meth(box.note, "SetPoint", "TOPLEFT", box.body, "BOTTOMLEFT", 0, -5)
  -- créé en premier = cliqué en premier par l'orage du harnais (Suivant puis Passer : la visite finit fermée)
  box.primary = newButton(box, 112, function() T:UserAction(onPrimary) end, true)
  meth(box.primary, "SetPoint", "BOTTOMRIGHT", box, "BOTTOMRIGHT", -PAD, PAD)
  box.secondary = newButton(box, 120, function() T:UserAction(onSecondary) end, false)
  meth(box.secondary, "SetPoint", "BOTTOMLEFT", box, "BOTTOMLEFT", PAD, PAD)
  M._hiding = true
  meth(box, "Hide")
  M._hiding = false

  -- halo pulsé autour de la cible (n'attrape pas la souris : on peut cliquer le vrai élément à travers)
  glow = G.CreateFrame("Frame", "TreeAITourGlow", G.UIParent)
  meth(glow, "SetFrameStrata", "FULLSCREEN_DIALOG")
  meth(glow, "EnableMouse", false)
  local gr, gg, gb = color("gold", 1, 0.82, 0.2)
  local function edge(p1, p2, w, h)
    local t = meth(glow, "CreateTexture", nil, "OVERLAY")
    if not t then return end
    if t.SetColorTexture then meth(t, "SetColorTexture", gr, gg, gb, 0.95)
    else meth(t, "SetTexture", WHITE); meth(t, "SetVertexColor", gr, gg, gb, 0.95) end
    meth(t, "SetPoint", p1, glow, p1, 0, 0)
    meth(t, "SetPoint", p2, glow, p2, 0, 0)
    if w then meth(t, "SetWidth", w) end
    if h then meth(t, "SetHeight", h) end
  end
  edge("TOPLEFT", "TOPRIGHT", nil, 2)
  edge("BOTTOMLEFT", "BOTTOMRIGHT", nil, 2)
  edge("TOPLEFT", "BOTTOMLEFT", 2, nil)
  edge("TOPRIGHT", "BOTTOMRIGHT", 2, nil)
  local fill = meth(glow, "CreateTexture", nil, "BACKGROUND")
  if fill then
    meth(fill, "SetAllPoints", glow)
    if fill.SetColorTexture then meth(fill, "SetColorTexture", gr, gg, gb, 0.12)
    else meth(fill, "SetTexture", WHITE); meth(fill, "SetVertexColor", gr, gg, gb, 0.12) end
  end
  local ag = meth(glow, "CreateAnimationGroup")
  if ag then
    meth(ag, "SetLooping", "BOUNCE")
    local a = meth(ag, "CreateAnimation", "Alpha")
    if a then meth(a, "SetFromAlpha", 1); meth(a, "SetToAlpha", 0.35); meth(a, "SetDuration", 0.7) end
    glow.pulse = ag
  end
  meth(glow, "Hide")

  arrow = G.CreateFrame("Frame", "TreeAITourArrow", G.UIParent)
  meth(arrow, "SetFrameStrata", "FULLSCREEN_DIALOG")
  meth(arrow, "EnableMouse", false)
  meth(arrow, "SetSize", ARROW_S, ARROW_S)
  arrow.tex = meth(arrow, "CreateTexture", nil, "OVERLAY")
  if arrow.tex then
    meth(arrow.tex, "SetAllPoints", arrow)
    meth(arrow.tex, "SetTexture", ARROW)
    meth(arrow.tex, "SetVertexColor", gr, gg, gb, 1)
  end
  meth(arrow, "Hide")
  M.UI.box, M.UI.glow, M.UI.arrow = box, glow, arrow
  return box
end
function M.Box() return box end

local function screen()
  local UI = G.UIParent
  local w, h = tonumber((meth(UI, "GetWidth"))), tonumber((meth(UI, "GetHeight")))
  if not (w and w > 300 and h and h > 200) then
    local sc = tonumber((meth(UI, "GetEffectiveScale"))) or 1
    if sc <= 0 then sc = 1 end
    w, h = (tonumber((try(G.GetScreenWidth))) or 1920) / sc, (tonumber((try(G.GetScreenHeight))) or 1080) / sc
  end
  return w, h
end
--- Rectangle de `f` en coordonnées d'UIParent : { l, b, w, h } (nil si inconnu).
function M.RectOf(f)
  local l, b = tonumber((meth(f, "GetLeft"))), tonumber((meth(f, "GetBottom")))
  local w, h = tonumber((meth(f, "GetWidth"))), tonumber((meth(f, "GetHeight")))
  if not (l and b and w and h) or w <= 0 or h <= 0 then return nil end
  local es = tonumber((meth(f, "GetEffectiveScale"))) or 1
  local us = tonumber((meth(G.UIParent, "GetEffectiveScale"))) or 1
  local k = (us > 0 and es > 0) and es / us or 1
  return { l * k, b * k, w * k, h * k }
end

local function layoutBox(title, body, note, count, primary, secondary)
  build()
  if not box then return end
  meth(box.head, "SetText", title or "")
  meth(box.count, "SetText", count or "")
  meth(box.body, "SetText", body or "")
  local bh = tonumber((meth(box.body, "GetStringHeight"))) or 14
  local nh = 0
  if note and note ~= "" then
    meth(box.note, "SetText", note)
    meth(box.note, "Show")
    nh = (tonumber((meth(box.note, "GetStringHeight"))) or 12) + 5
  else
    meth(box.note, "SetText", "")
    meth(box.note, "Hide")
  end
  setLabel(box.primary, primary)
  setLabel(box.secondary, secondary)
  meth(box, "SetHeight", floor(PAD + 16 + 5 + bh + nh + 10 + 20 + PAD + 0.5))
end

--- Pose la bulle à côté de `f` (le côté le plus libre), la flèche entre les deux, le halo autour. Sans cible :
--- en haut au centre, sans flèche ni halo. Renvoie le côté choisi (« right », « left », « top », « bottom »,
--- « inside », « none »).
function M.PlaceOn(f)
  build()
  if not box then return "none" end
  local SW, SH = screen()
  local bw, bh = tonumber((meth(box, "GetWidth"))) or BOX_W, tonumber((meth(box, "GetHeight"))) or 110
  local r = f and M.RectOf(f) or nil
  M.curTarget, M.lastRect = f, r
  meth(box, "ClearAllPoints")
  if not r then
    meth(box, "SetPoint", "TOP", G.UIParent, "TOP", 0, -330)   -- sous les fenêtres surgissantes du jeu
    meth(arrow, "Hide")
    if glow.pulse then meth(glow.pulse, "Stop") end
    meth(glow, "Hide")
    M.side = "none"
    return "none"
  end
  local l, b, w, h = r[1], r[2], r[3], r[4]
  local cx, cy = l + w / 2, b + h / 2
  local room = { right = SW - (l + w), left = l, top = SH - (b + h), bottom = b }
  local need = { right = bw + GAP, left = bw + GAP, top = bh + GAP, bottom = bh + GAP }
  local side, best = nil, -1
  for _, s in ipairs({ "right", "left", "bottom", "top" }) do
    if room[s] >= need[s] and room[s] > best then side, best = s, room[s] end
  end
  local x, y, ax, ay, dx, dy
  if side == "right" then x, y, ax, ay, dx, dy = l + w + GAP, cy - bh / 2, l + w + GAP / 2, cy, -1, 0
  elseif side == "left" then x, y, ax, ay, dx, dy = l - GAP - bw, cy - bh / 2, l - GAP / 2, cy, 1, 0
  elseif side == "top" then x, y, ax, ay, dx, dy = cx - bw / 2, b + h + GAP, cx, b + h + GAP / 2, 0, -1
  elseif side == "bottom" then x, y, ax, ay, dx, dy = cx - bw / 2, b - GAP - bh, cx, b - GAP / 2, 0, 1
  else side, x, y = "inside", cx - bw / 2, b + h - bh - 12 end   -- cible plus grande que l'écran libre : dedans, en haut
  x = max(4, min(x, SW - bw - 4))
  y = max(4, min(y, SH - bh - 4))
  meth(box, "SetPoint", "BOTTOMLEFT", G.UIParent, "BOTTOMLEFT", floor(x + 0.5), floor(y + 0.5))
  if ax then
    meth(arrow, "ClearAllPoints")
    meth(arrow, "SetPoint", "CENTER", G.UIParent, "BOTTOMLEFT", floor(ax + 0.5), floor(ay + 0.5))
    if arrow.tex and arrow.tex.SetRotation then meth(arrow.tex, "SetRotation", atan2(-dx, dy)) end
    meth(arrow, "Show")
  else
    meth(arrow, "Hide")
  end
  meth(glow, "ClearAllPoints")
  meth(glow, "SetPoint", "BOTTOMLEFT", G.UIParent, "BOTTOMLEFT", floor(l - 4 + 0.5), floor(b - 4 + 0.5))
  meth(glow, "SetSize", floor(w + 8 + 0.5), floor(h + 8 + 0.5))
  meth(glow, "Show")
  if glow.pulse then meth(glow.pulse, "Play") end
  M.side = side
  return side
end

local function hideFrames()
  M._hiding = true   -- (4.0 QA) le OnHide de la bulle ne prend que l'Échap du joueur
  if box then meth(box, "Hide") end
  M._hiding = false
  if arrow then meth(arrow, "Hide") end
  if glow then
    if glow.pulse then meth(glow.pulse, "Stop") end
    meth(glow, "Hide")
  end
  M.curTarget, M.lastRect = nil, nil
end

-- ticker 1 s seulement pendant qu'une bulle est affichée : suit la cible, l'efface si elle disparaît
local watch
local function stopWatch() if watch then meth(watch, "Cancel") end watch = nil end
local function startWatch()
  if watch or not T.NewTicker then return end
  watch = T:NewTicker(1, function() M.Watch() end, M)
end

-- ===================================================================== calme
M.BLOCKERS = { "GameMenuFrame", "SettingsPanel", "InterfaceOptionsFrame", "VideoOptionsFrame", "CinematicFrame", "MovieFrame",
  "StaticPopup1", "ReadyCheckFrame" }   -- (fenêtres du jeu : lues seulement, jamais touchées)
--- Fenêtre qui empêche de montrer la visite (menu du jeu, options, cinématique, assistant, mode édition) ou nil.
function M.Blocking()
  for _, n in ipairs(M.BLOCKERS) do
    local f = rawget(G, n)
    if type(f) == "table" and meth(f, "IsShown") then return n end
  end
  local wiz = T.GetWizard and T:GetWizard()
  if wiz and meth(wiz, "IsShown") then return "wizard" end
  if T.IsEditMode and T:IsEditMode() then return "edit" end
end
--- Moment calme : pas en combat, vivant (sauf `allowDead`), pas en vol, rien de bloquant (ni donjon si `noInstance`).
function M.Calm(allowDead, noInstance)
  if inCombat() then return false, "combat" end
  local st = T.PlayerState and T:PlayerState() or {}
  if not allowDead and (st.dead or st.ghost) then return false, "dead" end
  if st.taxi then return false, "taxi" end
  if noInstance and st.inInstance then return false, "instance" end
  local b = M.Blocking()
  if b then return false, b end
  return true
end

-- ===================================================================== la visite
M.mode = nil      -- "tour" | "tip" | "pointer" | nil : ce que montre la bulle
M.active = nil    -- visite en cours : { from, plan, pos, step, kind, target }

local function stepNote(st, a, kind)
  local n = type(st.note) == "function" and st.note(a, kind) or nil
  if a.pos >= #a.plan then
    local last = "C'est tout ! " .. REPLAY
    n = n and (n .. "\n" .. last) or last
  end
  return n
end
local function cleanupStep(a)
  local st = a and a.step
  if st and st.cleanup then pcall(st.cleanup, a) end
end
local function showStep()
  local a = M.active
  if not a or not a.step then return end
  local st = a.step
  if not st.hub then closeHub(a) end
  local body = type(st.text) == "function" and st.text(a, a.kind) or st.text
  layoutBox(st.title, body, stepNote(st, a, a.kind), ("%d/%d"):format(a.pos, #a.plan),
    a.pos >= #a.plan and "Terminer" or "Suivant", "Passer")
  M.mode = "tour"
  M.PlaceOn(a.target)
  meth(box, "Show")
  startWatch()
end
--- Étape suivante montrable (les autres sont sautées), sinon fin de la visite.
function M.Next()
  local a = M.active
  if not a then return false end
  cleanupStep(a)
  a.step, a.kind, a.target = nil, nil, nil
  while true do
    a.pos = a.pos + 1
    local idx = a.plan[a.pos]
    if not idx then
      M.Finish("done")
      return false
    end
    local st = M.STEPS[idx]
    if st.prepare then pcall(st.prepare, a) end
    local f, kind = pass(pcall(st.find))
    if visible(f) then
      a.step, a.kind, a.target = st, kind, f
      if a.from == "auto" then store().step = idx end   -- première visite coupée par un /reload : on reprendra là
      showStep()
      return true
    end
    if st.cleanup then pcall(st.cleanup, a) end
    table.remove(a.plan, a.pos)   -- rien à montrer : l'étape sort du compte (« 3/6 » reste juste)
    a.pos = a.pos - 1
  end
end

--- Lance la visite (from = "auto", "hub", "options", "slash", "aide"…). Jamais en combat : reportée après.
function M.Start(from)
  if not M:IsEnabled() then return false, "off" end
  if inCombat() then
    T:RunOutOfCombat("Tour:start", function() M.Start(from) end)
    return false, "combat"
  end
  local ok, why = M.Calm(true)
  if not ok then
    M.pending, M.pendingAt = from or "?", now()   -- options ouvertes, cinématique… : dès que c'est fermé
    M.Kick(1)
    return false, why
  end
  M.pending = nil
  if M.active then
    cleanupStep(M.active)
    closeHub(M.active)
  end
  M.mode, M.tip = nil, nil
  hideFrames()
  local a = { from = from or "?", plan = M.Plan(), pos = 0, started = now() }
  if #a.plan == 0 then
    if from == "auto" then M.Finish("empty")   -- rien à montrer : on ne la repropose pas
    else T:Print("Visite guidée : rien à montrer pour l'instant (pastille et panneau cachés).") end
    return false, "empty"
  end
  local saved = from == "auto" and tonumber(store().step) or nil   -- /reload en pleine visite : on reprend
  if saved then
    while a.plan[1] and a.plan[1] < saved and #a.plan > 1 do table.remove(a.plan, 1) end
  end
  M.active, M.paused = a, nil
  return M.Next()
end

--- Fin de la visite (« done », « skip »…) : tout est refermé, rien ne reste ouvert, mémorisé pour le compte.
function M.Finish(why)
  local a = M.active
  M.active, M.paused = nil, nil
  if a then
    cleanupStep(a)
    closeHub(a)
  end
  if M.mode == "tour" or not M.mode then
    M.mode = nil
    hideFrames()
    stopWatch()
  end
  local s = store()
  s.done, s.step, s.how, s.at = M.TOUR_V, nil, why or "done", wall()
  s.last = max(tonumber(s.last) or 0, wall() - M.TIP_GAP + 120)   -- première astuce : pas avant 2 min
  M.Kick(120)
  return true
end

--- Combat, mort : la bulle se cache ; elle revient au même endroit dès que c'est calme.
function M.Pause(why)
  if M.mode == "tip" then M.HideTip(false)
  elseif M.mode == "pointer" then M.HidePointer() end
  local a = M.active
  if not a then return false end
  M.paused = why or "?"
  cleanupStep(a)   -- nos menus ouverts pour l'étape se ferment aussi (rouverts à la reprise)
  closeHub(a)
  hideFrames()
  stopWatch()
  return true
end
function M.Resume()
  local a = M.active
  if not (a and M.paused) then return false end
  if not M.Calm(false) then return false end
  M.paused = nil
  local st = a.step
  if st then
    if st.prepare then pcall(st.prepare, a) end
    local f, kind = pass(pcall(st.find))
    if visible(f) then
      a.target, a.kind = f, kind
      showStep()
      return true
    end
  end
  a.pos = max(0, a.pos - 1)
  if st then table.remove(a.plan, a.pos + 1) end
  return M.Next()
end

-- ===================================================================== astuces
local function tipsAllowed()
  if not M:IsEnabled() or (M.db and M.db.tips == false) then return false end
  if not M.Done() or M.active or M.pending or M.mode then return false end
  if now() - (M._loginAt or 0) < M.TIPS_AFTER then return false end
  if wall() - (tonumber(store().last) or 0) < M.TIP_GAP then return false end
  return M.Calm(false, true) and true or false
end
M.TipsAllowed = tipsAllowed
local function tipText(tip) return type(tip.text) == "function" and tip.text() or tip.text end
function M.ShowTip(tip)
  if not (tip and build()) then return false end
  M.mode, M.tip, M.tipAt = "tip", tip, now()
  store().last = wall()
  layoutBox(tip.title, tipText(tip), tip.note, "Astuce", "OK", "Couper les astuces")
  local f = tip.target and pass(pcall(tip.target)) or nil
  M.PlaceOn(visible(f) and f or nil)
  meth(box, "Show")
  startWatch()
  return true
end
--- Première astuce dont le moment est venu (une seule, si tout le permet). `id` = n'essayer que celle-là.
function M.TryTips(id)
  if not tipsAllowed() then return nil end
  local seen = store().seen
  for _, tip in ipairs(M.TIPS) do
    if (not id or id == tip.id) and not seen[tip.id] and okCall(tip.when) then
      M.ShowTip(tip)
      return tip.id
    end
  end
end
--- Ferme l'astuce. `user` = le joueur a répondu (vue pour de bon) ; sinon vue seulement si restée ≥ TIP_SEEN s.
function M.HideTip(user)
  if M.mode ~= "tip" then return false end
  local tip = M.tip
  if tip and (user or now() - (M.tipAt or 0) >= M.TIP_SEEN) then store().seen[tip.id] = wall() end
  M.mode, M.tip = nil, nil
  hideFrames()
  stopWatch()
  M.Kick(5)
  return true
end
function M.TipsOff()
  if M.db then M.db.tips = false end
  M.HideTip(true)
end
function M.ResetTips()
  local s = store()
  s.seen, s.last = {}, 0
  if M.db then M.db.tips = true end
  M.Kick(5)
end

-- ===================================================================== /tai aide : une bulle sur la pastille
local HELP_COMBAT = "Aide : tout part de la pastille de la minimap (clic : la fenêtre TreeAI, clic droit : le menu)."
--- Montre la pastille (vrai) ; faux = pas de pastille visible (l'appelant garde l'ancienne aide).
function M.HelpPointer()
  if inCombat() then T:Print(HELP_COMBAT) return true end
  if M.active then
    if M.paused then M.Resume() end
    return true
  end
  local b = hubButton()
  if not (b and build()) then return false end
  if M.mode == "tip" then M.HideTip(false) end
  M.mode, M.tipAt = "pointer", now()
  local body = windowOn() and "Tout part de cette pastille : clic = la fenêtre TreeAI, clic droit = le menu rapide."
    or "Tout TreeAI part de cette pastille : clic = le menu, clic droit = les options."
  layoutBox("Aide", body, "Toutes les commandes : /tai aide tout", nil, "Visite guidée", "OK")
  M.PlaceOn(b)
  meth(box, "Show")
  startWatch()
  return true
end
function M.HidePointer()
  if M.mode ~= "pointer" then return false end
  M.mode = nil
  hideFrames()
  stopWatch()
  M.Kick(5)
  return true
end

-- boutons de la bulle : même cadre pour la visite, les astuces et l'aide
onPrimary = function()
  if M.mode == "tour" then M.Next()
  elseif M.mode == "tip" then M.HideTip(true)
  elseif M.mode == "pointer" then M.HidePointer(); M.Start("aide") end
end
onSecondary = function()
  if M.mode == "tour" then M.Finish("skip")
  elseif M.mode == "tip" then M.TipsOff()
  elseif M.mode == "pointer" then M.HidePointer() end
end

-- ===================================================================== suivi (ticker 1 s, bulle visible seulement)
local function moved(a, b)
  if not (a and b) then return a ~= b end
  for i = 1, 4 do if abs((a[i] or 0) - (b[i] or 0)) > 2 then return true end end
  return false
end
function M.Watch()
  if not (box and meth(box, "IsShown")) then stopWatch() return end
  if inCombat() then M.Pause("combat") return end
  local f = M.curTarget
  if M.mode == "tour" and M.active then
    local st = M.active.step
    -- ligne d'un menu (le joueur a pu ouvrir la liste bêta, fermer le menu…) ou cible disparue : on la recherche
    if st and (st.hub or (f and not visible(f))) then
      local nf, kind = pass(pcall(st.find))
      if visible(nf) then
        if nf ~= f or moved(M.RectOf(nf), M.lastRect) then
          M.active.target, M.active.kind = nf, kind
          M.PlaceOn(nf)
        end
      else   -- cible fermée par le joueur : la bulle reste, sans flèche ni halo
        meth(arrow, "Hide")
        if glow.pulse then meth(glow.pulse, "Stop") end
        meth(glow, "Hide")
      end
    elseif f and moved(M.RectOf(f), M.lastRect) then
      M.PlaceOn(f)
    end
    return
  end
  if M.mode == "tip" then
    local tip = M.tip
    if now() - (M.tipAt or 0) >= M.TIP_SHOW or (tip and tip.gone and okCall(tip.gone))
        or (f and not visible(f)) then
      M.HideTip(false)
    elseif f and moved(M.RectOf(f), M.lastRect) then
      M.PlaceOn(f)
    end
    return
  end
  if M.mode == "pointer" then
    if now() - (M.tipAt or 0) >= M.TIP_SHOW or (f and not visible(f)) then M.HidePointer() end
    return
  end
  hideFrames()
  stopWatch()
end

-- ===================================================================== réveils (un seul minuteur, seulement s'il y a de quoi attendre)
-- Pas d'AutoRefresh : une fois la visite vue et les astuces finies (ou coupées), le module ne coûte plus rien ;
-- pendant l'assistant de démarrage il attend sa fermeture (OnHide) au lieu de repasser.
local kickGen, kickAt = 0, nil
--- Réveil (M.Tick) dans `sec` s ; le plus proche gagne.
function M.Kick(sec)
  sec = max(0.5, tonumber(sec) or 1)
  local t = now()
  local at = t + sec
  if kickAt and kickAt > t and kickAt <= at + 0.05 then return end   -- un réveil plus tôt est déjà prévu
  kickGen = kickGen + 1
  local gen = kickGen
  kickAt = at
  T:After(sec, function()
    if gen ~= kickGen then return end
    kickAt = nil
    M.Tick()
  end)
end
local function tipsLeft()
  if M.db and M.db.tips == false then return false end
  local seen = store().seen
  for _, tip in ipairs(M.TIPS) do if not seen[tip.id] then return true end end
  return false
end
M.TIP_POLL = 30   -- s entre deux regards quand une astuce attend son moment (HV : tout de suite, par événement)
function M.Tick()
  if not M:IsEnabled() then return end
  local t = now()
  if M.pending then
    if t - (M.pendingAt or t) > 180 then M.pending = nil   -- options restées ouvertes 3 min : on oublie
    elseif M.Calm(true) then M.Start(M.pending)
    else M.Kick(1) end
    return
  end
  if M.paused then
    if not M.Resume() and M.paused then M.Kick(3) end
    return
  end
  if M.active then return end   -- la visite avance au clic du joueur
  if not M.Done() then
    -- première fois : après la connexion et l'assistant de démarrage, au premier moment calme qui dure
    local wiz = T.GetWizard and T:GetWizard()
    if wiz and meth(wiz, "IsShown") then
      if not M._wizHooked then
        M._wizHooked = true
        meth(wiz, "HookScript", "OnHide", function()
          M._wizardClosed = true
          if M:IsEnabled() and not M.Done() then M.Kick(3) end
        end)
      end
      return   -- réveil à la fermeture de l'assistant
    end
    local onboarded = not T.ShowWizard or (T.db and T.db.onboarded) or M._wizardClosed
    if not onboarded then
      M._waitN = (M._waitN or 0) + 1   -- l'assistant attend encore son moment calme : on repasse (15 min au plus)
      if M._waitN <= 90 then M.Kick(10) end
      return
    end
    local wait = M.LOGIN_QUIET - (t - (M._loginAt or t))
    if wait > 0 then M.Kick(wait) return end
    if M.mode ~= "pointer" and M.Calm(false, true) then
      if M._calmSince and t - M._calmSince >= M.CALM_FOR - 0.05 then
        M._calmSince = nil
        M.Start("auto")
        return
      end
      M._calmSince = M._calmSince or t
      M.Kick(M.CALM_FOR)
      return
    end
    M._calmSince = nil
    M.Kick(5)
    return
  end
  if not tipsLeft() then return end                    -- plus rien à attendre : aucun réveil
  if M.mode then M.Kick(M.TIP_SHOW) return end         -- une bulle est là : on repasse après
  local gap = M.TIP_GAP - (wall() - (tonumber(store().last) or 0))
  local quiet = M.TIPS_AFTER - (t - (M._loginAt or t))
  if gap > 0 or quiet > 0 then M.Kick(max(gap, quiet, 1)) return end
  M.TryTips()
  if M.mode ~= "tip" then M.Kick(M.TIP_POLL) end
end

-- ===================================================================== branchements (Hub, Options, /tai)
--- Entrée « Visite guidée » du menu de la pastille (Hub.lua l'ajoute en fin de Réglages si T.Tour existe).
function M.HubEntry()
  if not M:IsEnabled() then return nil end
  return { group = "reglages", id = "tour", title = "Visite guidée", icon = "Interface\\Icons\\INV_Misc_Map_01",
    desc = "Où cliquer, en 60 secondes", tip = "Clic : la visite guidée (une bulle par élément, Suivant / Passer)",
    state = not M.Done() and "|cff33dd33nouveau|r" or nil,
    fn = function() T:UserAction(function() M.Start("hub") end) end }
end
--- Bouton « Visite guidée » de la page d'accueil des options (Options.lua l'appelle avec son moteur de page).
function M.OptionsButton(P)
  if type(P) ~= "table" or type(P.button) ~= "function" or not M:IsEnabled() then return nil end
  return P.button("", nil, function() M.Start("options") end, function()
    return M.pending == "options" and "Ferme les options : la visite démarre" or "Visite guidée (60 secondes)"
  end)
end

local HELP_CMDS = { [""] = true, aide = true, help = true, ["?"] = true }
local ALL = { tout = true, all = true, commandes = true, liste = true }
function M:OnInit()
  -- /tai et /tai aide : une bulle sur la pastille (l'ancienne page « Tout » reste : /tai aide tout)
  if not M._origSlash and type(T.HandleSlash) == "function" then
    local orig = T.HandleSlash
    M._origSlash = orig
    T.HandleSlash = function(self, input)
      local cmd, rest = tostring(input or ""):match("^%s*(%S*)%s*(.-)%s*$")
      cmd, rest = (cmd or ""):lower(), (rest or ""):lower()
      if HELP_CMDS[cmd] and M:IsEnabled() then
        if ALL[rest] then return orig(self, "aide") end
        if rest == "" and M.HelpPointer() then return end
      end
      return orig(self, input)
    end
  end
end

function M:OnEnable()
  -- (4.0.1) une fois pour tous : astuces coupées après la visite (la 4.0 les avait posées à « oui » d'office) ;
  -- « Revoir les astuces » ou l'option les rallume
  local st = store()
  if (tonumber(st.calmV) or 0) < 1 then
    st.calmV = 1
    if M.db then M.db.tips = false end
  end
  -- (5.0 LOOK) le réglage en 3 étapes (personnage · affichage · touches) est proposé UNE fois à ceux qui avaient déjà
  -- fini l'ancien assistant : au premier moment calme (même garde que le 1er lancement), « Passer » = plus jamais
  local db = T.db
  if type(db) == "table" and (tonumber(db.setupV) or 0) < 5 then
    local had = db.onboarded
    db.setupV = 5
    if had and type(T._MaybeWizard) == "function" and type(T.ShowWizard) == "function" then
      db.onboarded = nil
      T:After(6, function()
        local wz = T.GetWizard and T:GetWizard()
        if T.db and not T.db.onboarded and not (wz and meth(wz, "IsShown")) then T:_MaybeWizard(0) end   -- (déjà ouvert par le cœur : rien)
      end)
    end
  end
  M._loginAt = now()
  M._calmSince, M._waitN, kickAt = nil, nil, nil
  self:On("COMBAT_START", function() M.Pause("combat") end)
  self:On("COMBAT_END", function() M.Kick(1.5) end)
  self:RegisterEvent("PLAYER_DEAD", function() M.Pause("dead") end)
  self:RegisterEvent("PLAYER_UNGHOST", function() M.Kick(1) end)
  self:RegisterEvent("PLAYER_ALIVE", function() M.Kick(1) end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function()
    if M.active and not M.paused and M.curTarget then T:After(1, function() if M.curTarget then M.PlaceOn(M.curTarget) end end) end
  end)
  self:RegisterEvent("AUCTION_HOUSE_SHOW", function() T:After(1.5, function() M.TryTips("ah") end) end)
  M.Kick(M.LOGIN_QUIET)
end
function M:OnOptionChanged(key) if key == "tips" then M.Kick(5) end end
function M:OnDisable()
  local a = M.active
  M.active, M.paused, M.pending, M.mode, M.tip = nil, nil, nil, nil, nil
  kickAt = nil
  if a then cleanupStep(a) closeHub(a) end
  hideFrames()
  stopWatch()
end

T:RegisterSlash("visite", function() M.Start("slash") end, "visite guidée : où cliquer dans TreeAI (60 s)")
T:RegisterSlash("tour", function() M.Start("slash") end)
T:RegisterSlash("aide", function() end, "où tout trouver : la pastille de la minimap (/tai aide tout : toutes les commandes)")
-- rubrique « Démarrer » de l'aide groupée : la visite juste après « aide »
for _, g in ipairs(type(T.SLASH_GROUPS) == "table" and T.SLASH_GROUPS or {}) do
  if g[1] == "Démarrer" and type(g[2]) == "table" then
    local has = false
    for _, c in ipairs(g[2]) do if c == "visite" then has = true end end
    if not has then table.insert(g[2], 2, "visite") end
  end
end
