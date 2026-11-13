-- TreeAI — module Shortcuts (5.0, agent LOOK) : la BANDE D'ACTIONS RAPIDES, impossible à rater, un clic = l'action.
--   Quêtes [● Guidage quêtes ON] : Rendre · Prendre · Suivant · Zone · Vite  |  Perso : Sacs · Équipement · Métiers  |
--   Groupe  |  TreeAI
-- Chaque bouton = icône du jeu + mot court dessous + pastille de nombre (coin haut droit) + sa touche (coin haut gauche).
-- Un clic FAIT la chose : changer le mode de quêtes (T.QuestBrain), passer l'étape, ouvrir le choix de zone, lancer la
-- session métier, ouvrir la carte du module (S.SetAction : les modules Sacs / Équipement y branchent leur action
-- directe) ; clic droit = la page de la fenêtre. La pastille « Guidage quêtes » allume / coupe TOUT le guidage de
-- quêtes d'un clic (T:SetQuestGuide du cœur ; sans lui : elle grise les boutons de quêtes, rien d'autre).
-- POURQUOI LE JOUEUR NE LA VOYAIT PAS (4.3.x, mesuré : research/look5.md) : 27 px de haut en 1080p à l'échelle 0,64,
-- texte de 9 px, fond à 42 %, et elle changeait de place à chaque changement de groupe / de cible (3 places dans la
-- même session), parfois sous la boussole. 5.0 : boutons pleins de 42 unités, mots de 11, taille plancher en pixels
-- réels (S.AutoScale), et une place STABLE : elle ne bouge que si un cadre du jeu vient vraiment dessus.
-- Place : jamais sur les cadres du jeu (joueur, cible, familier, groupe — même cachés —, suivi de quêtes, minimap,
-- améliorations, colonne « Suivi Forever », boussole TreeAI) ; trop étroit = mode compact (icônes seules).
-- Déplaçable (poignée, mode édition), verrou, taille, mode compact, « cacher en combat ». /tai interface reset.
-- Touches : une liaison par bouton (Bindings.xml, aucune d'office) + « action suivante » + « mode suivant » ;
-- « Touches conseillées » (un clic, hors combat) pose Alt-1… sur les seules touches libres.
-- Rien de protégé (cadres simples : affichés, posés et repeints même en combat). Événementiel, regroupé (≥ 0,3 s),
-- aucune minuterie ni OnUpdate au repos. Lua 5.1, aucune globale.
-- (UIREDO, gardée) T:RegisterShortcutBar : la « barre d'actions » où Quick / QuickFight amarrent leurs boutons
-- sécurisés (objet de quête, monture, familier…). Cachée par défaut ; une case des réglages la remet en bas.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local S = T:RegisterModule("Shortcuts", {
  title = "Actions rapides",
  description = "La bande d'actions rapides : Guidage quêtes ON / OFF, Rendre, Prendre, Suivant, Zone, Vite, Sacs, Équipement, Métiers, Groupe, TreeAI.",
  defaults = { enabled = true, strip = true, text = true, keys = true, stripLocked = false, stripScale = 100,
               keysHint = true, hideCombat = false, guide = true,
               actionBar = false, dock = true, locked = false, labels = true, scale = 100 },
  charDefaults = { pos = nil, strip = nil },
  options = {
    { section = "Actions rapides", key = "strip", type = "toggle", label = "Afficher la bande d'actions rapides" },
    { key = "text", type = "toggle", label = "Un mot sous chaque icône (décoché : mode compact, icônes seules)" },
    { key = "keys", type = "toggle", label = "Afficher la touche de chaque bouton" },
    { key = "hideCombat", type = "toggle", label = "Cacher la bande pendant les combats" },
    { key = "stripLocked", type = "toggle", label = "Verrouiller la bande (elle ne bouge plus)" },
    { key = "stripScale", type = "range", label = "Taille de la bande (%)", min = 70, max = 150, step = 5 },
    { key = "suggestKeys", type = "button", label = "Touches conseillées : Alt-1 à Alt-0 (seulement les touches libres)",
      onClick = function() if T.Shortcuts and T.Shortcuts.SuggestKeys then T.Shortcuts.SuggestKeys() end end },
    { key = "resetStrip", type = "button", label = "Remettre la bande à sa place (/tai interface reset)",
      onClick = function() if T.Shortcuts and T.Shortcuts.Reset then T.Shortcuts.Reset() end end },
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

-- ===================================================================== actions rapides : définitions
local ICON = "Interface\\Icons\\"
-- group = groupe de la bande ; match = morceaux d'identifiant (ou de libellé) d'un mode de T.QuestBrain ;
-- page = la page de la fenêtre (clic droit, ou repli quand rien de direct n'existe) ; quest = coupé avec le guidage
local DEFS = {
  { id = "rendre", group = "quetes", quest = true, label = "Rendre", icon = "Interface\\GossipFrame\\ActiveQuestIcon",
    bind = "TREEAI_QB_TURNIN", match = { "turnin", "turn_in", "rendre", "return", "handin", "turn" }, page = "quests",
    title = "Rendre mes quêtes au plus vite",
    tip = "Un clic : le trajet passe par les PNJ qui attendent tes quêtes finies, au plus court (pierre de foyer et vols compris)." },
  { id = "prendre", group = "quetes", quest = true, label = "Prendre", icon = "Interface\\GossipFrame\\AvailableQuestIcon",
    bind = "TREEAI_QB_PICKUP", match = { "pickup", "pick", "prendre", "take", "accept", "available" }, page = "quests",
    title = "Quêtes à prendre",
    tip = "Un clic : le trajet va chercher les meilleures quêtes près de toi (XP par minute trajet compris, suites, groupe, niveau)." },
  { id = "suivant", group = "quetes", quest = true, label = "Suivant", icon = ICON .. "Ability_Rogue_Sprint",
    bind = "TREEAI_STRIP_SUIVANT", page = "quests",
    title = "Étape suivante",
    tip = "Un clic : passe à l'étape suivante (celle-ci est faite, ou tu veux la sauter)." },
  { id = "zone", group = "quetes", quest = true, label = "Zone", icon = ICON .. "INV_Misc_Map_01", bind = "TREEAI_QB_ZONE",
    match = { "zone", "here", "ici", "all", "tout" }, page = "quests",
    title = "Tout faire ici · choisir ma zone",
    tip = "Un clic : tout faire dans ta zone, et la liste des zones pour toi, classées (quêtes, trajet, XP/h, groupe)." },
  { id = "vite", group = "quetes", quest = true, label = "Vite", icon = ICON .. "Spell_Nature_Swiftness", bind = "TREEAI_QB_FASTLVL",
    match = { "fastlvl", "fast", "vite", "speed", "monter" }, page = "quests",
    title = "Monter vite",
    tip = "Un clic : une seule consigne « fais ça maintenant », pour le plus d'XP par heure." },
  { id = "sacs", group = "perso", label = "Sacs", icon = ICON .. "INV_Misc_Bag_08", bind = "TREEAI_STRIP_SACS", page = "bags",
    title = "Sacs",
    tip = "Un clic : tes sacs s'ouvrent avec ce qui est à vendre, à garder, à mettre à l'HV ou à la banque." },
  { id = "equip", group = "perso", label = "Équipement", icon = ICON .. "INV_Chest_Chain", bind = "TREEAI_STRIP_EQUIP", page = "gear",
    title = "Équipement",
    tip = "Un clic : ce que tu peux équiper de mieux tout de suite, et les prochaines pièces à viser." },
  -- « Métiers » : seulement si tu as un métier de fabrication ; pastille = achats en attente du plan ;
  -- clic = lancer / quitter la session métier (le trajet de quêtes en pause), clic droit = la page Métiers
  { id = "metiers", group = "perso", label = "Métiers", icon = ICON .. "Trade_BlackSmithing", bind = "TREEAI_STRIP_METIERS", page = "prof",
    title = "Métiers",
    tip = "Un clic : lance (ou quitte) la session métier — le plan pour monter ton métier, étape par étape.",
    optional = true },
  { id = "groupe", group = "groupe", label = "Groupe", icon = ICON .. "Spell_Holy_PrayerOfHealing", bind = "TREEAI_QB_GROUP",
    match = { "group", "groupe", "party", "team" }, page = "group",
    title = "Avec le groupe",
    tip = "Un clic : les quêtes que tout le groupe peut faire ensemble ; seul : la page pour rejoindre un groupe." },
  { id = "fenetre", group = "treeai", label = "TreeAI", icon = ICON .. "INV_Misc_Book_09", bind = "TREEAI_MAIN",
    title = "Fenêtre TreeAI",
    tip = "Un clic : ouvre ou ferme la fenêtre TreeAI (toutes les pages, la recherche, les réglages)." },
  -- hors de la bande (sa touche et « /tai boutons journal » marchent toujours) : le ménage du journal
  { id = "journal", hidden = true, label = "Journal", icon = ICON .. "INV_Scroll_03", bind = "TREEAI_STRIP_JOURNAL",
    match = { "journal", "clean", "nettoy", "tidy", "log" }, page = "journal",
    title = "Nettoyer mon journal",
    tip = "Les quêtes à abandonner (grises, trop loin, impossibles seul), en un coup d'œil." },
}
S.DEFS = DEFS
local DEF = {}
for _, d in ipairs(DEFS) do DEF[d.id] = d end
-- groupes de la bande, dans l'ordre ; cap = petit titre au-dessus (les groupes d'un seul bouton n'en ont pas)
S.GROUPS = {
  { id = "quetes", cap = "Quêtes" }, { id = "perso", cap = "Perso" }, { id = "groupe" }, { id = "treeai" },
}
-- touches conseillées : Alt-1… (rien par défaut dans le jeu), sinon Ctrl-Maj-1… ; JAMAIS Ctrl-1… Ctrl-0 (barre du
-- familier / des postures) ni Maj-1… Maj-6 (pages de barres d'action)
S.KEYS = {
  TREEAI_QB_TURNIN = { "ALT-1", "CTRL-SHIFT-1" }, TREEAI_QB_PICKUP = { "ALT-2", "CTRL-SHIFT-2" },
  TREEAI_STRIP_SUIVANT = { "ALT-3", "CTRL-SHIFT-3" }, TREEAI_QB_ZONE = { "ALT-4", "CTRL-SHIFT-4" },
  TREEAI_QB_GROUP = { "ALT-5", "CTRL-SHIFT-5" }, TREEAI_QB_FASTLVL = { "ALT-6", "CTRL-SHIFT-6" },
  TREEAI_MAIN = { "ALT-7", "CTRL-SHIFT-7" }, TREEAI_ACTION_NEXT = { "ALT-8", "CTRL-SHIFT-8" },
  TREEAI_STRIP_SACS = { "ALT-9", "CTRL-SHIFT-9" }, TREEAI_STRIP_EQUIP = { "ALT-0", "CTRL-SHIFT-0" },
  TREEAI_STRIP_METIERS = { "ALT-F1", "CTRL-SHIFT-F1" }, TREEAI_GUIDE_TOGGLE = { "ALT-F2", "CTRL-SHIFT-F2" },
  TREEAI_MODE_NEXT = { "ALT-F3", "CTRL-SHIFT-F3" }, TREEAI_STRIP_JOURNAL = { "ALT-F5", "CTRL-SHIFT-F5" },
}
S.KEY_ORDER = { "TREEAI_QB_TURNIN", "TREEAI_QB_PICKUP", "TREEAI_STRIP_SUIVANT", "TREEAI_QB_ZONE", "TREEAI_QB_GROUP",
  "TREEAI_QB_FASTLVL", "TREEAI_MAIN", "TREEAI_ACTION_NEXT", "TREEAI_STRIP_SACS", "TREEAI_STRIP_EQUIP",
  "TREEAI_STRIP_METIERS", "TREEAI_GUIDE_TOGGLE", "TREEAI_MODE_NEXT", "TREEAI_STRIP_JOURNAL" }
-- noms lisibles (Échap > Raccourcis > AddOns > TreeAI)
G.BINDING_HEADER_TREEAI = G.BINDING_HEADER_TREEAI or "TreeAI"
G.BINDING_NAME_TREEAI_QB_TURNIN = "TreeAI : rendre mes quêtes au plus vite"
G.BINDING_NAME_TREEAI_QB_PICKUP = "TreeAI : quêtes à prendre"
G.BINDING_NAME_TREEAI_STRIP_SUIVANT = "TreeAI : étape suivante"
G.BINDING_NAME_TREEAI_QB_ZONE = "TreeAI : tout faire ici · choisir ma zone"
G.BINDING_NAME_TREEAI_QB_GROUP = "TreeAI : avec le groupe"
G.BINDING_NAME_TREEAI_QB_FASTLVL = "TreeAI : monter vite"
G.BINDING_NAME_TREEAI_STRIP_JOURNAL = "TreeAI : nettoyer mon journal"
G.BINDING_NAME_TREEAI_MODE_NEXT = "TreeAI : mode de quêtes suivant"
G.BINDING_NAME_TREEAI_STRIP_METIERS = "TreeAI : métiers (session, page)"
G.BINDING_NAME_TREEAI_STRIP_SACS = "TreeAI : sacs"
G.BINDING_NAME_TREEAI_STRIP_EQUIP = "TreeAI : équipement"
G.BINDING_NAME_TREEAI_GUIDE_TOGGLE = "TreeAI : guidage des quêtes (allumer / couper)"
G.BINDING_NAME_TREEAI_ACTION_NEXT = "TreeAI : action suivante (la plus utile maintenant)"

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

-- ===================================================================== guidage des quêtes : UN interrupteur
--- le guidage des quêtes est-il allumé ? (T:QuestGuideOn du cœur ; sans lui : notre réglage, qui ne fait que
--- griser les boutons de quêtes de la bande)
function S.GuideOn()
  if type(T.QuestGuideOn) == "function" then
    local ok, v = pcall(T.QuestGuideOn, T)
    if ok and not secret(v) then return v and true or false end
  end
  return not (S.db and S.db.guide == false)
end
--- vrai quand le cœur fournit le vrai interrupteur (sinon la pastille ne coupe que les boutons)
function S.GuideIsCore() return type(T.SetQuestGuide) == "function" end
function S.SetGuide(on)
  on = on and true or false
  if S.db then S.db.guide = on end
  local core = false
  if type(T.SetQuestGuide) == "function" then core = pcall(T.SetQuestGuide, T, on) end
  if not core and T.Fire then pcall(T.Fire, T, "QUEST_GUIDE_CHANGED", on) end   -- (le cœur l'envoie lui-même)
  S.Refresh(true)
  return on, core
end
function S.ToggleGuide() return S.SetGuide(not S.GuideOn()) end

-- ===================================================================== actions branchées par les modules
-- S.SetAction("sacs", { fn = function(bouton) … return true end, count = function() return n end,
--   active = function() return bool end, tip = function() return { "ligne", … } end }) : l'action directe, la
-- pastille et les lignes d'infobulle d'un bouton viennent du module qui sait (Sacs, Équipement, Métiers…).
S.actions = {}
function S.SetAction(id, spec)
  if not DEF[id] or (spec ~= nil and type(spec) ~= "table") then return false end
  S.actions[id] = spec
  if S._active then S.Refresh() end
  return true
end
local function hook(id, field, ...)
  local a = S.actions[id]
  local f = a and a[field]
  if type(f) ~= "function" then return nil end
  local ok, r = pcall(f, ...)
  if ok and not secret(r) then return r end
  return nil
end

-- « Métiers » : tu as un métier de fabrication (Prof a lu tes métiers) ; achats en attente (ProfLevel)
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

--- état de chaque bouton : { id, group, label, count, active, disabled, key, mode, title, tip } — le contrat des tests
function S.State()
  local list, act = S.Modes(), activeId()
  local guide = S.GuideOn()
  local out = {}
  for _, d in ipairs(DEFS) do
    if not d.hidden and (not d.optional or S.HasProf()) then
      local st = { id = d.id, group = d.group, label = d.label, icon = d.icon, title = d.title, tip = d.tip, bind = d.bind }
      local m = S.ModeFor(d.id, list)
      if m then
        st.mode = m.id
        st.active = (m.active == true) or (act ~= nil and act == m.id) or false
        if type(m.label) == "string" and m.label ~= "" then
          st.modeLabel = m.label
          if d.id ~= "zone" then st.title = m.label end
        end
        if type(m.tip) == "string" and m.tip ~= "" then st.modeTip = m.tip end
        -- (« Vite » : son nombre est rendre + prendre, déjà montrés à côté → pas de pastille sans sens propre)
        if type(m.count) == "number" and not secret(m.count) and d.id ~= "vite" then st.count = m.count end
        if type(m.key) == "string" and m.key ~= "" then st.modeKey = m.key end
      end
      if st.count == nil then
        if d.id == "rendre" then st.count = listLen(qcall("TurnInList")) or (not QB() and doneInLog() or nil)
        elseif d.id == "prendre" then st.count = listLen(qcall("PickupList", 20))
        elseif d.id == "metiers" then st.count = S.ProfBuys() end
      end
      if d.id == "metiers" then st.active = type(T.focus) == "table" and T.focus.kind == "prof" end
      local c = hook(d.id, "count")
      if type(c) == "number" then st.count = c end
      local a = hook(d.id, "active")
      if a ~= nil then st.active = a and true or false end
      if d.quest and not guide then st.disabled, st.active = true, false end
      st.key = S.KeyOf(d.bind)
      out[#out + 1] = st
    end
  end
  return out
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
--- la touche telle qu'elle tient dans le coin d'un bouton : « Alt-1 » ; au-delà de 6 lettres, modificateurs en une
--- lettre (« Ctrl-Maj-3 » → « C-M-3 ») — l'infobulle garde le nom entier
function S.KeyShort(text)
  if type(text) ~= "string" then return nil end
  if ulen(text) <= 6 then return text end
  return (text:gsub("Ctrl%-", "C-"):gsub("Maj%-", "M-"):gsub("Alt%-", "A-"):gsub("Cmd%-", "D-"))
end
--- liaisons de la bande encore sans touche (celles d'un bouton absent — pas de métier — ne comptent pas)
function S.MissingKeys()
  local out = {}
  for _, cmd in ipairs(S.KEY_ORDER) do
    local skip = (cmd == "TREEAI_STRIP_METIERS" and not S.HasProf())
    if not skip and not bindKey(cmd) then out[#out + 1] = cmd end
  end
  return out
end
local function flash(text)
  local Wn = T.Window
  if Wn and type(Wn.Flash) == "function" then try(Wn.Flash, text, 12) end
  S.lastMessage = text
end
S.Flash = flash
--- « Touches conseillées » : un clic du joueur, hors combat ; Alt-1… sur les seules touches libres, enregistré
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
--- étape suivante : QuestBrain, puis la boussole (cible suivante), sinon la page Quêtes
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
--- ouvre les sacs du jeu (jamais protégé) : repli de « Sacs » tant que le module Sacs n'a pas branché sa carte
local function openBags()
  for _, n in ipairs({ "OpenAllBags", "ToggleAllBags" }) do
    local f = rawget(G, n)
    if type(f) == "function" and pcall(f) then return true end
  end
  return false
end
--- « action suivante » : LA chose la plus utile maintenant, d'après ce qui est lu dans le jeu (jamais un pari) —
--- { id, why } ou nil. Session en cours → son étape suivante ; quêtes finies → les rendre ; plus rien à rendre en
--- mode « Rendre » → retour à la zone ; rien en cours et des quêtes à prendre → les prendre.
function S.NextAction(states)
  if not S.GuideOn() then return nil end
  if type(T.focus) == "table" and type(T.FocusStep) == "function" then
    return { id = "suivant", why = "session en cours : son étape suivante" }
  end
  local by = {}
  for _, st in ipairs(states or S.lastState or S.State()) do by[st.id] = st end
  local r, p, z = by.rendre, by.prendre, by.zone
  local nr, np = tonumber(r and r.count) or 0, tonumber(p and p.count) or 0
  if r and r.mode and nr > 0 and not r.active then
    return { id = "rendre", why = ("%d quête%s finie%s à rendre"):format(nr, nr > 1 and "s" or "", nr > 1 and "s" or "") }
  end
  if r and r.active and nr == 0 and z and z.mode then return { id = "zone", why = "plus rien à rendre : retour à ta zone" } end
  local nx = type(T.Next) == "function" and try(T.Next) or nil
  if p and p.mode and np > 0 and not p.active and nx == nil then
    return { id = "prendre", why = ("rien en cours, %d quête%s à prendre"):format(np, np > 1 and "s" or "") }
  end
  return nil
end
--- l'action d'un bouton (clic, ou sa touche). button = "RightButton" : la page de la fenêtre.
function S.Do(id, button)
  if id == "mode" then return S.CycleMode() end
  if id == "guide" then S.ToggleGuide() return "guide" end
  if id == "next" then
    local nx = S.NextAction(S.State())
    if not nx then flash("Rien de plus utile maintenant : continue ton étape.") return "none" end
    flash("Action suivante : " .. tostring(DEF[nx.id] and DEF[nx.id].title or nx.id) .. " (" .. nx.why .. ").")
    return S.Do(nx.id)
  end
  local d = DEF[id]
  if not d then return nil end
  S.last = id
  if d.quest and not S.GuideOn() and button ~= "RightButton" then
    flash("Guidage des quêtes coupé : clique « Guidage quêtes » sur la bande pour le rallumer.")
    return "off"
  end
  if button ~= "RightButton" then
    -- l'action directe du module qui sait (Sacs, Équipement…) passe d'abord
    local r = hook(id, "fn", button)
    if r then S.Refresh(true) return "module" end
  end
  if id == "fenetre" then
    local Wn = T.Window
    if Wn and type(Wn.Toggle) == "function" then T.safecall(Wn.Toggle) else openPage(nil) end
    return "window"
  end
  if id == "metiers" then
    local P = PLv()
    if button ~= "RightButton" and P and type(P.ToggleFocus) == "function" and S.HasProf() then
      if inCombat() then flash("Session métier : après le combat.") return "combat" end
      T.safecall(P.ToggleFocus, (type(P.Line) == "function" and try(P.Line)) or nil)
      S.Refresh(true)
      return "focus"
    end
    openPage("prof")
    return "page"
  end
  if id == "sacs" then
    if button == "RightButton" then openPage(d.page) return "page" end
    if openBags() then return "bags" end
    openPage(d.page)   -- (la page Sacs si son module l'a inscrite, sinon Équipement : W.Resolve)
    return "page"
  end
  if id == "equip" then openPage(d.page) return "page" end
  if id == "suivant" then
    -- session métier / courses en ville (T.focus) : étape suivante de la session, clic droit = précédente
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
  -- « Zone » : « Tout faire ici » ET le choix de la zone (fenêtre, page Quêtes) — un clic, pas juste un mode
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

-- ===================================================================== infobulles (une par bouton, toujours)
local COUNT_TIP = { rendre = "Pastille : %d quête%s finie%s à rendre", prendre = "Pastille : %d quête%s à prendre près de toi",
  zone = "Pastille : %d quête%s en cours dans ta zone", groupe = "Pastille : %d quête%s en commun avec le groupe" }
--- les lignes de l'infobulle d'un bouton : { { texte, r, g, b, coupé à la ligne }, … } — la 1re est le titre
function S.TipLines(id)
  local d = DEF[id]
  local out = {}
  local function add(s, r, g, b, wrap) if type(s) == "string" and s ~= "" and not secret(s) then out[#out + 1] = { s, r, g, b, wrap } end end
  if id == "guide" then
    local on = S.GuideOn()
    add("Guidage des quêtes : " .. (on and "allumé" or "coupé"), 1, 0.82, 0.2)
    if S.GuideIsCore() then
      add(on and "Un clic : coupe tout le guidage de quêtes (trajet, boussole, repères). Tu joues à ta façon, TreeAI se tait."
        or "Un clic : rallume le guidage de quêtes (trajet, boussole, repères).", 0.9, 0.9, 0.9, true)
    else
      add(on and "Un clic : coupe les boutons de quêtes de la bande." or "Un clic : rallume les boutons de quêtes de la bande.",
        0.9, 0.9, 0.9, true)
    end
    local k = S.KeyOf("TREEAI_GUIDE_TOGGLE")
    add(k and ("Touche : " .. k) or "Touche : aucune (« Touches conseillées » dans les réglages)", 0.4, 0.8, 1, true)
    return out
  end
  if not d then return out end
  local st
  for _, s in ipairs(S.lastState or {}) do if s.id == id then st = s end end
  st = st or {}
  add(st.title or d.title or d.label, 1, 0.82, 0.2)
  add(d.tip, 0.9, 0.9, 0.9, true)
  if st.disabled then add("Guidage des quêtes coupé : rallume-le avec la pastille « Guidage quêtes ».", 1, 0.6, 0.15, true) end
  local lines = {}
  if id == "rendre" then lines = qcall("TurnInList") or {} elseif id == "prendre" then lines = qcall("PickupList", 5) or {} end
  local n = 0
  for _, it in ipairs(type(lines) == "table" and lines or {}) do
    local s = itemText(it)
    if s then n = n + 1 add("• " .. s, 0.8, 0.8, 0.8) end
    if n >= 5 then break end
  end
  -- ce que compte la pastille (un nombre sans son nom ne dit rien)
  local nb = tonumber(st.count)
  if nb and nb > 0 and COUNT_TIP[id] then add(COUNT_TIP[id]:format(nb, nb > 1 and "s" or "", nb > 1 and "s" or ""), 1, 0.82, 0.2) end
  local extra = hook(id, "tip")
  for i, l in ipairs(type(extra) == "table" and extra or {}) do if i <= 6 and type(l) == "string" then add(l, 0.8, 0.8, 0.8, true) end end
  if id == "metiers" then
    local P = PLv()
    local cl = P and type(P.ComponentLines) == "function" and try(P.ComponentLines, true) or {}
    for i, l in ipairs(type(cl) == "table" and cl or {}) do if i <= 6 then add(l, 0.8, 0.8, 0.8, true) end end
    if (tonumber(st.count) or 0) > 0 then add(("%d achat%s en attente dans ton plan"):format(st.count, st.count > 1 and "s" or ""), 1, 0.82, 0.2) end
    if st.active then add("Session métier en cours (un clic : la quitter).", 0.4, 1, 0.4, true) end
  end
  local inFocus = id == "suivant" and type(T.focus) == "table" and type(T.FocusStep) == "function"
  if id == "suivant" and not inFocus and type(T.Next) == "function" then
    local nx = try(T.Next)
    local s = type(nx) == "table" and (nx.line or nx.title)
    if type(s) == "string" and not secret(s) then add("Maintenant : " .. s, 0.8, 0.8, 0.8, true) end
  end
  if inFocus then
    add((type(T.focus.label) == "string" and T.focus.label or "Session") .. " : clic = étape suivante, clic droit = précédente", 0.4, 1, 0.4, true)
  end
  if st.active and id ~= "metiers" then add("Mode choisi.", 0.4, 1, 0.4) end
  local nx = S.NextAction()
  if nx and nx.id == id then add("Conseillé maintenant : " .. nx.why .. ".", 0.4, 1, 0.4, true) end
  add(st.key and ("Touche : " .. st.key) or "Touche : aucune (« Touches conseillées » dans les réglages)", 0.4, 0.8, 1, true)
  if d.page and id ~= "fenetre" and id ~= "equip" and not inFocus then add("Clic droit : la page de la fenêtre", 0.4, 0.8, 1) end
  return out
end
local function hideTip() local tt = G.GameTooltip if tt then meth(tt, "Hide") end end
local function showTip(b)
  local tt = G.GameTooltip
  if not tt then return end
  local lines = S.TipLines(b.id)
  if #lines == 0 then return end
  meth(tt, "SetOwner", b, "ANCHOR_BOTTOMRIGHT")
  for _, l in ipairs(lines) do meth(tt, "AddLine", l[1], l[2], l[3], l[4], l[5]) end
  meth(tt, "Show")
end

-- ===================================================================== bande : cadre et boutons
local strip, grip, keysBtn, pill
local btns, caps, seps = {}, {}, {}
local function K() local th = Th() return th and th.TOKENS or nil end
local function dims()
  local k = K()
  local t, s = k and k.tile or { w = 46, h = 42, icon = 22, gap = 2, compact = 28 }, k and k.strip or { cap = 16, pad = 4, grip = 10, sep = 9 }
  return t, s
end
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
local function click(self, button)
  hideTip()
  local fn = function() S.Do(self.id, button) end
  if T.UserAction then T:UserAction(fn) else T.safecall(fn) end
end
--- bouton de repli (thème coupé / absent) : la même forme, sans les états du thème
local function plainButton(name, parent)
  local ok, b = pcall(G.CreateFrame, "Button", name, parent)
  if not ok or not b then return nil end
  local t = dims()
  meth(b, "SetSize", t.w, t.h)
  meth(b, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
  b.bg = meth(b, "CreateTexture", nil, "BACKGROUND")
  if b.bg then meth(b.bg, "SetAllPoints", b) color(b.bg, 0.09, 0.10, 0.13, 0.88) end
  local hl = meth(b, "CreateTexture", nil, "HIGHLIGHT")
  if hl then meth(hl, "SetAllPoints", b) color(hl, 1, 1, 1, 0.10) end
  b.icon = meth(b, "CreateTexture", nil, "ARTWORK")
  b.label = fstring(b, "S", "text")
  b.keyText = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmallGray")
  if b.keyText then meth(b.keyText, "SetPoint", "TOPLEFT", b, "TOPLEFT", 2, -2) end
  b.count = meth(b, "CreateFontString", nil, "OVERLAY", "NumberFontNormalSmall")
  if b.count then meth(b.count, "SetPoint", "TOPRIGHT", b, "TOPRIGHT", -2, -2) end
  b.__plain = {}
  function b.SetLayout(self2, kind)
    local st = self2.__plain
    if st.kind == kind then return end
    st.kind = kind
    local compact = kind == "icon"
    meth(self2, "SetHeight", compact and t.compact or t.h)
    if self2.icon then
      meth(self2.icon, "ClearAllPoints")
      meth(self2.icon, "SetSize", compact and (t.compact - 8) or t.icon, compact and (t.compact - 8) or t.icon)
      meth(self2.icon, "SetPoint", compact and "CENTER" or "TOP", self2, compact and "CENTER" or "TOP", 0, compact and 0 or -4)
    end
    if self2.label then
      meth(self2.label, "ClearAllPoints")
      if compact then meth(self2.label, "Hide") else meth(self2.label, "SetPoint", "BOTTOM", self2, "BOTTOM", 0, 3) meth(self2.label, "Show") end
    end
  end
  function b.Set(self2, v)
    local st = self2.__plain
    if v.icon and v.icon ~= st.icon then
      st.icon = v.icon
      meth(self2.icon, "SetTexture", v.icon)
      if v.icon:find("Icons", 1, true) then meth(self2.icon, "SetTexCoord", 0.08, 0.92, 0.08, 0.92) end
    end
    if v.label ~= nil and self2.label then st.label = v.label meth(self2.label, "SetText", v.label) end
    if self2.keyText then meth(self2.keyText, "SetText", v.key or "") end
    local n = tonumber(v.count)
    if self2.count then meth(self2.count, "SetText", (n and n > 0) and (n > 99 and "99+" or tostring(n)) or "") end
    if v.disabled then color(self2.bg, 0.09, 0.10, 0.13, 0.5) elseif v.active then color(self2.bg, 0.36, 0.27, 0.06, 0.94)
    else color(self2.bg, 0.09, 0.10, 0.13, 0.88) end
    meth(self2.icon, "SetDesaturated", v.disabled and true or false)
  end
  function b.Fit(self2, minW)
    local st = self2.__plain
    if st.kind == "icon" then meth(self2, "SetWidth", minW or t.compact) return minW or t.compact end
    local tw = self2.label and tonumber(meth(self2.label, "GetStringWidth")) or 0
    if not tw or tw <= 0 then tw = ulen(st.label) * 5.6 end
    local need = max(minW or t.w, floor(tw + 8 + 0.5))
    meth(self2, "SetWidth", need)
    return need
  end
  b:SetLayout("tile")
  return b
end
-- UN bouton par action : il change de forme (mot dessous / icône seule) sans qu'on en crée un second
local function newButton(def)
  local th = Th()
  local name = "TreeAIStrip_" .. def.id
  local b
  if th and th.ActionButton then
    b = th:ActionButton(strip, { name = name, layout = "tile", icon = def.icon, label = def.label,
      onClick = click, onEnter = showTip, onLeave = hideTip })
  end
  if not b then
    b = plainButton(name, strip)
    if not b then return nil end
    meth(b, "SetScript", "OnClick", click)
    meth(b, "SetScript", "OnEnter", showTip)
    meth(b, "SetScript", "OnLeave", hideTip)
  end
  b.id = def.id
  -- bascules (guidage, session métier) : « mod: » = l'orage de clics du harnais les laisse, comme les lignes « Fonctions »
  if def.id == "guide" or def.id == "metiers" then b.entry = { id = "mod:strip:" .. def.id } end
  meth(b, "Hide")
  return b
end
local GUIDE_DEF = { id = "guide", label = "Guidage", icon = ICON .. "Ability_Tracking" }   -- (mode compact : la pastille en icône)
local function button(id)
  local b = btns[id]
  if b == nil then
    local def = DEF[id] or (id == "guide" and GUIDE_DEF) or nil
    b = def and newButton(def) or false
    btns[id] = b
  end
  return b or nil
end

local function saveStripPos()
  if not strip then return end
  if T.AnchorFromRect then T:AnchorFromRect(strip) end
  local pt, _, rp, x, y = meth(strip, "GetPoint")
  if S.char and okAnchor(pt, x, y) then S.char.strip = { pt, okAnchor(rp, 0, 0) and rp or pt, floor(x + 0.5), floor(y + 0.5) } end
end
local function openSettings()
  local Wn = T.Window
  if Wn and Wn._active and Wn.Open then
    if Wn.char then Wn.char.optPage = "Shortcuts" end
    Wn.Open(Wn.pageIndex and Wn.pageIndex.display and "display" or "options")
  elseif T.OpenOptions then T:OpenOptions("Shortcuts") end
end

local function ensureStrip()
  if strip then return strip end
  -- cadres simples (aucun modèle sécurisé) sur UIParent : créés même en combat (/reload en combat = visibles)
  if type(G.CreateFrame) ~= "function" then return nil end
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIStrip", G.UIParent, G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if not ok or not f then return nil end
  strip = f
  local t, s = dims()
  meth(f, "SetSize", 400, t.h + s.cap + 2 + 2 * s.pad)
  meth(f, "SetFrameStrata", "MEDIUM")
  meth(f, "SetFrameLevel", 20)   -- au-dessus des cadres du jeu de même strate (jamais couverte)
  meth(f, "SetAlpha", 1)
  meth(f, "SetClampedToScreen", true)
  meth(f, "SetMovable", true)
  meth(f, "EnableMouse", true)
  local th = Th()
  -- petit cadre posé sur l'écran : fond à moitié transparent, liseré 1 px (Theme « small ») ; les boutons sont pleins
  if not (th and th:Skin(f, "small")) then
    f.bgt = meth(f, "CreateTexture", nil, "BACKGROUND")
    if f.bgt then meth(f.bgt, "SetAllPoints", f) color(f.bgt, 0.04, 0.05, 0.07, 0.5) end
  end
  -- poignée : glisser = déplacer (sauf verrou), clic droit = réglages
  local okG, h = pcall(G.CreateFrame, "Button", "TreeAIStripGrip", f)
  if okG and h then
    grip = h
    meth(h, "SetWidth", s.grip)
    meth(h, "SetPoint", "TOPLEFT", f, "TOPLEFT", 2, -2)
    meth(h, "SetPoint", "BOTTOMLEFT", f, "BOTTOMLEFT", 2, 2)
    meth(h, "RegisterForClicks", "RightButtonUp")
    meth(h, "RegisterForDrag", "LeftButton")
    for i = 1, 3 do
      local d = meth(h, "CreateTexture", nil, "ARTWORK")
      if d then meth(d, "SetSize", 3, 3) meth(d, "SetPoint", "CENTER", h, "CENTER", 0, (2 - i) * 6) tcolor(d, "gold", 1, 0.82, 0.2, 1) end
    end
    local hl = meth(h, "CreateTexture", nil, "HIGHLIGHT")
    if hl then meth(hl, "SetAllPoints", h) color(hl, 1, 1, 1, 0.10) end
    meth(h, "SetScript", "OnDragStart", function() if not inCombat() and not stripLocked() then meth(f, "StartMoving") end end)
    meth(h, "SetScript", "OnDragStop", function() meth(f, "StopMovingOrSizing") saveStripPos() end)
    meth(h, "SetScript", "OnClick", openSettings)
    meth(h, "SetScript", "OnEnter", function(self)
      local tt = G.GameTooltip
      if not tt then return end
      meth(tt, "SetOwner", self, "ANCHOR_BOTTOMRIGHT")
      meth(tt, "AddLine", "Actions rapides", 1, 0.82, 0.2)
      meth(tt, "AddLine", stripLocked() and "Verrouillée (réglages : clic droit)" or "Glisser : déplacer (hors combat)", 0.4, 0.8, 1)
      meth(tt, "AddLine", "Clic droit : réglages (taille, mode compact, touches conseillées)", 0.4, 0.8, 1, true)
      meth(tt, "AddLine", "/tai interface reset : tout remettre à sa place", 0.6, 0.6, 0.6, true)
      meth(tt, "Show")
    end)
    meth(h, "SetScript", "OnLeave", hideTip)
  end
  -- pastille « Guidage quêtes » ON / OFF (ligne des titres) ; mode compact : une icône qui s'éteint
  local function pillClick()
    hideTip()
    local fn = function() S.Do("guide") end
    if T.UserAction then T:UserAction(fn) else T.safecall(fn) end
  end
  if th and th.Pill then
    pill = th:Pill(f, { name = "TreeAIStripGuide", label = "Guidage quêtes", h = s.cap, onClick = pillClick, onEnter = showTip, onLeave = hideTip })
  end
  if not pill then
    local okP, p = pcall(G.CreateFrame, "Button", "TreeAIStripGuide", f)
    if okP and p then
      pill = p
      meth(p, "SetSize", 120, s.cap)
      p.bg = meth(p, "CreateTexture", nil, "BACKGROUND")
      if p.bg then meth(p.bg, "SetAllPoints", p) end
      p.label = fstring(p, "S", "text")
      if p.label then meth(p.label, "SetPoint", "CENTER", p, "CENTER", 0, 0) end
      function p.SetOn(self2, on)
        self2.on = on and true or false
        color(self2.bg, on and 0.10 or 0.16, on and 0.30 or 0.16, on and 0.14 or 0.18, 0.92)
        meth(self2.label, "SetText", "Guidage quêtes : " .. (on and "ON" or "OFF"))
      end
      function p.Fit(self2) meth(self2, "SetWidth", 130) return 130 end
      meth(p, "SetScript", "OnClick", pillClick)
      meth(p, "SetScript", "OnEnter", showTip)
      meth(p, "SetScript", "OnLeave", hideTip)
    end
  end
  if pill then pill.id, pill.entry = "guide", { id = "mod:strip:guide" } end
  -- « Touches » : seulement tant qu'une liaison manque (clic = touches conseillées, clic droit = ne plus proposer)
  local okK, kb = pcall(G.CreateFrame, "Button", "TreeAIStripKeys", f)
  if okK and kb then
    keysBtn = kb
    meth(kb, "SetHeight", s.cap)
    meth(kb, "RegisterForClicks", "LeftButtonUp", "RightButtonUp")
    kb.bg = meth(kb, "CreateTexture", nil, "BACKGROUND")
    if kb.bg then meth(kb.bg, "SetAllPoints", kb) tcolor(kb.bg, "tile", 0.09, 0.10, 0.13, 0.88) end
    local hl = meth(kb, "CreateTexture", nil, "HIGHLIGHT")
    if hl then meth(hl, "SetAllPoints", kb) color(hl, 1, 1, 1, 0.10) end
    kb.text = fstring(kb, "S", "key")
    if kb.text then meth(kb.text, "SetPoint", "CENTER", kb, "CENTER", 0, 0) meth(kb.text, "SetText", "Touches…") end
    meth(kb, "SetScript", "OnClick", function(_, btn)
      hideTip()
      if btn == "RightButton" then if S.db then S.db.keysHint = false end S.LayoutStrip() return end
      if T.UserAction then T:UserAction(S.SuggestKeys) else T.safecall(S.SuggestKeys) end
    end)
    meth(kb, "SetScript", "OnEnter", function(self)
      local tt = G.GameTooltip
      if not tt then return end
      meth(tt, "SetOwner", self, "ANCHOR_BOTTOMRIGHT")
      meth(tt, "AddLine", "Touches conseillées", 1, 0.82, 0.2)
      meth(tt, "AddLine", "Un clic : Alt-1 à Alt-0 pour ces boutons, seulement sur les touches libres (rien n'est remplacé).",
        0.9, 0.9, 0.9, true)
      meth(tt, "AddLine", "Clic droit : ne plus proposer", 0.4, 0.8, 1)
      meth(tt, "Show")
    end)
    meth(kb, "SetScript", "OnLeave", hideTip)
  end
  if T.RegisterMovable then
    T:RegisterMovable(f, "strip", "Actions rapides", {
      onMoved = function(point, x, y, sc)
        if S.char then S.char.strip = { point, point, floor(x or 0), floor(y or 0) } end
        if sc and S.db then S.db.stripScale = floor(sc / S.AutoScale() * 100 + 0.5) end
      end,
      onReset = function() if S.char then S.char.strip = nil end S.PlaceStrip(true) end,
      isPlaced = function() return S.char and type(S.char.strip) == "table" or false end,
    })
  end
  meth(f, "Hide")
  return strip
end
S.EnsureStrip = ensureStrip
function S.Strip() return strip end
--- les boutons montrés : id → bouton + « guide » = la pastille (mode compact : son icône)
function S.Buttons()
  local out = {}
  for id, b in pairs(btns) do if b and id ~= "guide" then out[id] = b end end
  if S.compact and btns.guide then out.guide = btns.guide elseif pill then out.guide = pill end
  return out
end

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
  PartyFrame = true, CompactPartyFrame = true, TreeAICompassFrame = true }
-- sous la cible / la focalisation : leurs améliorations et affaiblissements (deux rangées d'icônes)
local AURAS_BELOW = { TargetFrame = 46, FocusFrame = 46 }
-- cadre du jeu présent mais pas encore mesurable (jamais montré) : sa place d'origine (Classic) est réservée
local FALLBACK = {
  PlayerFrame = function(_, H) return { l = 0, b = H - 110, w = 240, h = 110 } end,
  TargetFrame = function(_, H) return { l = 245, b = H - 152, w = 245, h = 152 } end,
  PartyMemberFrame1 = function(_, H) return { l = 0, b = H - 440, w = 200, h = 284 } end,
}
-- client récent : le groupe est UN conteneur (PartyFrame), minuscule tant qu'on est seul → la place de 4 membres
-- sous son coin haut gauche est réservée (sinon la bande s'y pose et le groupe arrive dessus)
local PARTY_BOX = { w = 130, h = 250 }
S.AVOID, S.RESERVE = AVOID, RESERVE
local function finite(v) return type(v) == "number" and v == v and v > -1e6 and v < 1e6 end
local function screen()
  if T._ScreenSize then return T._ScreenSize() end
  local w, h = meth(G.UIParent, "GetWidth"), meth(G.UIParent, "GetHeight")
  if finite(w) and finite(h) and w > 200 and h > 200 then return w, h end
end
--- taille plancher en pixels réels : à petite échelle d'interface (0,64 en 1080p = 0,9 px par unité) la bande grossit
--- pour que ses mots fassent au moins ~11 px réels ; jamais plus de +40 %. 1 sans la taille réelle de l'écran.
function S.AutoScale()
  local f = rawget(G, "GetPhysicalScreenSize")
  local es = tonumber(meth(G.UIParent, "GetEffectiveScale"))
  if type(f) ~= "function" or not (es and finite(es) and es > 0) then return 1 end
  local ok, _, ph = pcall(f)
  ph = ok and tonumber(ph) or nil
  if not (ph and ph > 200) then return 1 end
  local px = es * ph / 768           -- pixels réels par unité d'interface
  if not finite(px) or px <= 0 then return 1 end
  return max(1, min(1.4, 1 / px))
end
local function stripScale()
  return max(0.7, min(1.5, (tonumber(S.db and S.db.stripScale) or 100) / 100)) * S.AutoScale()
end
--- rectangle d'un cadre du jeu en unités d'UIParent { l, b, w, h, name } ou nil
function S.RectOf(name, evenHidden)
  local f = rawget(G, name)
  if type(f) ~= "table" or (not evenHidden and not meth(f, "IsVisible")) then return nil end
  if meth(f, "IsForbidden") then return nil end
  local l, b, w, h = meth(f, "GetRect")
  if not (finite(l) and finite(b) and finite(w) and finite(h)) then return nil end
  local k = (tonumber(meth(f, "GetEffectiveScale")) or 1) / (tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1)
  if not finite(k) or k <= 0 then k = 1 end
  if name == "PartyFrame" and (w < 8 or h < PARTY_BOX.h * 0.5) and evenHidden then
    -- conteneur du groupe pas encore rempli : la place de 4 membres sous son coin haut gauche
    return { l = l * k, b = (b + h) * k - PARTY_BOX.h, w = max(w * k, PARTY_BOX.w), h = PARTY_BOX.h, name = name }
  end
  if w < 8 or h < 8 then return nil end
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
--- la bande est-elle (presque) entièrement à l'écran ? nil = pas encore mesurable
--- (lu sur l'ancrage, pas sur le rectangle : le client ramène à l'écran ce qu'il dessine, pas la place enregistrée)
local function fx(pt) pt = tostring(pt or "") return pt:find("LEFT") and 0 or pt:find("RIGHT") and 1 or 0.5 end
local function fy(pt) pt = tostring(pt or "") return pt:find("BOTTOM") and 0 or pt:find("TOP") and 1 or 0.5 end
function S.OnScreen()
  if not strip then return nil end
  local W, H = screen()
  local pt, _, rp, x, y = meth(strip, "GetPoint")
  local k = tonumber(meth(strip, "GetScale")) or 1
  local w, h = (tonumber(meth(strip, "GetWidth")) or 0) * k, (tonumber(meth(strip, "GetHeight")) or 0) * k
  if not (W and okAnchor(pt, x, y) and finite(w) and w > 0) then return nil end
  local l = W * fx(rp or pt) + x * k - w * fx(pt)
  local b = H * fy(rp or pt) + y * k - h * fy(pt)
  return l >= -4 and b >= -4 and l + w <= W + 4 and b + h <= H + 4
end
function S.Avoid(W, H)
  local out, seen = {}, {}
  local function push(r) if r and not (r.w >= W * 0.9 and r.h >= H * 0.6) then out[#out + 1] = r end end
  local function add(n)
    if seen[n] then return end
    seen[n] = true
    local r = S.RectOf(n, RESERVE[n])
    if not r and FALLBACK[n] and type(rawget(G, n)) == "table" then r = FALLBACK[n](W, H) r.name = n end
    if r and AURAS_BELOW[n] then r.b, r.h = r.b - AURAS_BELOW[n], r.h + AURAS_BELOW[n] end
    push(r)
  end
  -- d'abord les rectangles du placement du cœur : suivis de quêtes et colonnes d'autres addons (« Suivi Forever »…)
  -- avec la place qu'ils prennent en grandissant → la bande ne se pose pas juste sous un suivi qui va s'allonger
  if type(T._LightRects) == "function" then
    local ok, list = pcall(T._LightRects, W, H)
    for _, r in ipairs(ok and type(list) == "table" and list or {}) do
      local n = r.name
      if type(n) == "string" and not n:find("^TreeAI") and not RESERVE[n] and not AURAS_BELOW[n] and not seen[n]
          and finite(r.l) and finite(r.b) and finite(r.w) and finite(r.h) then
        seen[n] = true
        push({ l = r.l, b = r.b, w = r.w, h = r.h, name = n })
      end
    end
  end
  for _, n in ipairs(AVOID) do add(n) end
  for _, n in ipairs(T.LAYOUT_SCAN_NAMES or {}) do add(n) end
  -- la place d'origine de la boussole TreeAI (en haut au centre) est toujours réservée, qu'elle y soit déjà ou non
  -- (elle apparaît avec la 1re cible, puis le placement du cœur l'y ramène) : la bande ne s'y pose pas pour devoir
  -- en partir dix secondes plus tard
  local Cm = T.modules and T.modules.Compass
  if Cm and Cm._active and not (Cm.db and Cm.db.shown == false) then
    local k = tonumber(Cm.db and Cm.db.scale) or 1
    local cw, ch = ((tonumber(Cm.W) or 150) + 30) * k, ((tonumber(Cm.H) or 74) + 30) * k
    push({ l = W / 2 - cw / 2, b = H - 70 - ch, w = cw, h = ch, name = "TreeAICompassSlot" })
  end
  S.WatchCompass()
  return out
end
local function overlaps(a, b, m)
  m = m or 0
  return a.l < b.l + b.w + m and b.l < a.l + a.w + m and a.b < b.b + b.h + m and b.b < a.b + a.h + m
end
S.Overlaps = overlaps
--- meilleure place libre en haut à gauche pour w × h (unités UIParent) : (gauche, haut) ou nil
function S.FindSpot(w, h, W, H, avoid, maxRight, relaxed, wantLeast)
  local xs, tops = { 8 }, { H - 6 }
  maxRight = maxRight or (W - 4)
  -- relaxed (écran bondé, rien de libre) : le haut de la zone « centre » gardée au jeu est permis, mais seulement
  -- dans les 40 % hauts de l'écran — jamais le milieu, jamais sur un cadre
  local minB = relaxed and H * 0.6 or 4
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
      if x >= 4 and x + w <= maxRight and rc.b >= minB and top <= H - 2 then
        local free, area = true, 0
        for _, r in ipairs(avoid) do
          if overlaps(rc, r, 3) then
            free = false
            if not wantLeast then break end   -- (la place « la moins pire » n'est cherchée qu'au 1er essai)
            area = area + (min(rc.l + w, r.l + r.w) - max(rc.l, r.l)) * (min(top, r.b + r.h) - max(rc.b, r.b))
          end
        end
        if wantLeast and not free and (not least or area < least) then least, ll, lt = area, x, top end
        if free and not relaxed then for _, r in ipairs(keep) do if overlaps(rc, r, 0) then free = false break end end end
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
--- les cadres du jeu que la bande touche là où elle est (noms) — vide = elle est bien placée
function S.Blockers(W, H, avoid)
  local out = {}
  if not W then W, H = screen() end
  local rc = stripRect()
  if not (W and rc) then return out end
  for _, r in ipairs(avoid or S.Avoid(W, H)) do if overlaps(rc, r, 1) then out[#out + 1] = r.name or "?" end end
  return out
end

local placeTries, placing = 0, false
--- la boussole TreeAI se montre ou change de place (placement automatique du cœur, joueur qui la glisse) : un regard,
--- regroupé (0,5 s) — si elle est venue sur la bande non posée par le joueur, la bande s'écarte. Crochets sur NOTRE
--- cadre seulement (jamais un cadre du jeu) ; rien ne tourne tant que la boussole ne bouge pas.
function S.WatchCompass()
  local cf = rawget(G, "TreeAICompassFrame")
  if S._compassHooked or type(cf) ~= "table" then return end
  S._compassHooked = true
  local function look()
    if S._compassLook or placing then return end
    S._compassLook = true
    T:After(0.5, function()
      S._compassLook = nil
      if strip and stripOn() and S.placed == "auto" and S.UnderCompass() then S.PlaceStrip() end
    end)
  end
  if type(cf.HookScript) == "function" then pcall(cf.HookScript, cf, "OnShow", look) end
  local hk = rawget(G, "hooksecurefunc")
  if type(hk) == "function" and type(cf.SetPoint) == "function" then pcall(hk, cf, "SetPoint", look) end
  look()
end
--- la boussole TreeAI (visible) est-elle sur la bande ? (un seul rectangle lu : appelé à chaque rafraîchissement)
function S.UnderCompass()
  local rc, c = stripRect(), S.RectOf("TreeAICompassFrame")
  return (rc and c and overlaps(rc, c, 1)) and true or false
end
--- pose la bande : place du joueur si elle en a une, sinon la meilleure place libre en haut à gauche.
--- STABLE : déjà posée, à l'écran et sans rien dessous → elle ne bouge pas (force = recalculer quand même).
function S.PlaceStrip(force)
  if not strip then return false end
  local k = stripScale()
  meth(strip, "SetScale", k)
  meth(strip, "SetAlpha", 1)
  local function show() if stripOn() and not (S.db.hideCombat and inCombat()) then meth(strip, "Show") end end
  local p = S.char and S.char.strip
  if type(p) == "table" and not okAnchor(p[1], p[3], p[4]) then S.char.strip, p = nil, nil end
  if type(p) == "table" then
    local rp = okAnchor(p[2], 0, 0) and p[2] or p[1]
    meth(strip, "ClearAllPoints")
    if pcall(strip.SetPoint, strip, p[1], G.UIParent, rp, p[3], p[4]) and S.OnScreen() ~= false then
      S.placed = "player"
      show()
      return true
    end
    -- place enregistrée hors écran (autre résolution / échelle) : oubliée, place automatique
    S.char.strip = nil
  end
  local W, H = screen()
  if not W then
    -- écran pas encore posé (connexion) : essais espacés, puis les événements d'écran prennent le relais
    -- (cachée d'ici là : jamais une image sur les cadres de groupe)
    if placeTries < 30 then placeTries = placeTries + 1 T:After(2, S.PlaceStrip) end
    meth(strip, "Hide")
    S.placed = nil
    return false
  end
  placeTries = 0
  local avoid = S.Avoid(W, H)
  local sig = floor(W) .. "x" .. floor(H) .. ":" .. floor(k * 100)
  if not force and S.placed == "auto" and S.autoSig == sig and S.OnScreen() ~= false and #S.Blockers(W, H, avoid) == 0 then
    show()
    return true
  end
  -- taille de la bande dans chaque forme : lue si elle est déjà connue, sinon UNE disposition pour la mesurer (les
  -- essais ci-dessous ne redisposent rien : au plus deux mesures puis la forme retenue)
  local function size(compact)
    local key = compact and "c" or "f"
    if not S.sizes[key] then S.LayoutStrip(compact) end
    local z = S.sizes[key] or { tonumber(meth(strip, "GetWidth")) or 400, tonumber(meth(strip, "GetHeight")) or 68 }
    return z[1] * k, z[2] * k
  end
  -- les cadres du jeu d'abord : sur un petit écran, plutôt sur un cadre TreeAI (boussole) que sur le groupe
  local game = {}
  for _, r in ipairs(avoid) do if not tostring(r.name or ""):find("^TreeAI") then game[#game + 1] = r end end
  placing = true
  local l, top, fl, ft, w, h, chosen
  -- d'abord dans la partie gauche de l'écran (là où le joueur l'attend), avec les mots ; sinon icônes seules à
  -- gauche ; sinon n'importe où ; en dernier, plutôt sur un cadre TreeAI que sur un cadre du jeu
  local left = W * 0.62
  for i, try_ in ipairs({ { false, avoid, left }, { true, avoid, left }, { false, avoid }, { true, avoid },
      { true, avoid, nil, true }, { false, game, left }, { true, game, left }, { false, game }, { true, game },
      { true, game, nil, true } }) do
    local compact = try_[1] or S.db.text == false
    w, h = size(compact)
    local a, b, c, d = S.FindSpot(w, h, W, H, try_[2], try_[3], try_[4], i == 1)
    if i == 1 then fl, ft = c, d end
    chosen = compact
    if a then l, top = a, b break end
  end
  if (S.compact or false) ~= (chosen or false) or not S.sizes[chosen and "c" or "f"] then S.LayoutStrip(chosen) end
  w, h = size(chosen)
  placing = false
  if not l then
    -- rien de libre (écran minuscule) : icônes seules, à la place qui cache le moins, sinon sous les cadres de gauche
    local _, _, c2, d2 = S.FindSpot(w, h, W, H, avoid, nil, nil, true)
    if c2 then fl, ft = c2, d2 end
    l, top = fl, ft
    if not l then
      local low = H - 6
      for _, r in ipairs(avoid) do if r.l < W * 0.3 and r.b + r.h > H * 0.4 then low = min(low, r.b - 6) end end
      l, top = 8, max(h + 4, low)
    end
  end
  meth(strip, "ClearAllPoints")
  meth(strip, "SetPoint", "TOPLEFT", G.UIParent, "TOPLEFT", floor(l / k + 0.5), floor((top - H) / k + 0.5))
  S.placed, S.autoSig = "auto", sig
  show()
  return true
end

-- ===================================================================== disposition
local lastWide
local function caption(i, textStr)
  local c = caps[i]
  if not c then
    c = fstring(strip, "S", "caption")
    caps[i] = c
  end
  if c then meth(c, "SetText", textStr) end
  return c
end
local function separator(i)
  local d = seps[i]
  if not d then
    d = meth(strip, "CreateTexture", nil, "ARTWORK")
    if d then meth(d, "SetWidth", 1) tcolor(d, "smallBorder", 0.78, 0.62, 0.28, 0.55) end
    seps[i] = d
  end
  return d
end
--- dispose les boutons par groupes ; compact = icônes seules (étroit, ou option « un mot » coupée)
function S.LayoutStrip(compact)
  if not stripOn() then if strip then meth(strip, "Hide") end return end
  if not ensureStrip() then return end
  local states = S.lastState or S.State()
  S.lastState = states
  if compact == nil then compact = S.compact or false end
  if S.db.text == false then compact = true end
  -- 1re fois (connexion, /reload) : les boutons sont créés sur quelques images, 3 par image (jamais 10 boutons et
  -- leurs 150 textures dans la même image), puis la bande est disposée et montrée
  local missing = false
  for _, st in ipairs(states) do if btns[st.id] == nil then missing = true break end end
  if missing then
    local tnow = tonumber((G.GetTime and G.GetTime())) or 0
    if not S._building or tnow - S._building > 2 then
      S._building = tnow
      local i = 0
      local function step()
        local n = 0
        while n < 3 and i < #states do
          i = i + 1
          if btns[states[i].id] == nil then button(states[i].id) n = n + 1 end
        end
        if i < #states then T:After(0, step) return end
        S._building = nil
        S.LayoutStrip(compact)
      end
      step()
    end
    return
  end
  S.compact = compact
  local t, s = dims()
  local showKeys = S.db.keys ~= false
  local capH = compact and 0 or (s.cap + 2)
  local bh = compact and t.compact or t.h
  local x = s.pad + s.grip + 2
  local shown, nCap, nSep = {}, 0, 0
  local byGroup = {}
  for _, st in ipairs(states) do
    local g = byGroup[st.group or "?"]
    if not g then g = {} byGroup[st.group or "?"] = g end
    g[#g + 1] = st
  end
  local guideOn = S.GuideOn()
  local first = true
  for _, grp in ipairs(S.GROUPS) do
    local list = byGroup[grp.id]
    if list and #list > 0 then
      if not first then
        nSep = nSep + 1
        local d = separator(nSep)
        if d then
          meth(d, "ClearAllPoints")
          meth(d, "SetPoint", "TOPLEFT", strip, "TOPLEFT", x + floor(s.sep / 2) - 1, -s.pad)
          meth(d, "SetPoint", "BOTTOMLEFT", strip, "BOTTOMLEFT", x + floor(s.sep / 2) - 1, s.pad)
          meth(d, "Show")
        end
        x = x + s.sep
      end
      first = false
      local gx = x
      -- mode compact : la pastille devient la 1re icône du groupe Quêtes
      if grp.id == "quetes" and compact then
        local b = button("guide")
        if b then
          b:SetLayout("icon")
          b:Set({ icon = "Interface\\Icons\\Ability_Tracking", active = guideOn, disabled = not guideOn })
          meth(b, "ClearAllPoints")
          meth(b, "SetPoint", "TOPLEFT", strip, "TOPLEFT", x, -s.pad)
          b:Fit(t.compact)
          meth(b, "Show")
          shown[b] = true
          x = x + t.compact + t.gap
        end
      end
      for _, st in ipairs(list) do
        local b = button(st.id)
        if b then
          b:SetLayout(compact and "icon" or "tile")
          b:Set({ icon = st.icon, label = st.label, key = showKeys and S.KeyShort(st.key) or false, count = st.count,
            active = st.active, disabled = st.disabled })
          local w = b:Fit(compact and t.compact or t.w)
          meth(b, "ClearAllPoints")
          meth(b, "SetPoint", "TOPLEFT", strip, "TOPLEFT", x, -s.pad - capH)
          meth(b, "Show")
          shown[b] = true
          b.w = w
          x = x + w + t.gap
        end
      end
      x = x - t.gap
      if not compact and grp.cap then
        nCap = nCap + 1
        local c = caption(nCap, grp.cap)
        if c then
          meth(c, "ClearAllPoints")
          meth(c, "SetPoint", "TOPLEFT", strip, "TOPLEFT", gx + 2, -s.pad - 3)
          meth(c, "Show")
        end
        if grp.id == "quetes" and pill then
          local cw = (c and tonumber(meth(c, "GetStringWidth"))) or 0
          if not finite(cw) or cw <= 0 then cw = ulen(grp.cap) * 5.4 end
          pill:SetOn(guideOn)
          local pw = pill:Fit()
          meth(pill, "ClearAllPoints")
          meth(pill, "SetPoint", "TOPLEFT", strip, "TOPLEFT", gx + 2 + floor(cw + 0.5) + 6, -s.pad)
          meth(pill, "Show")
          shown[pill] = true
          x = max(x, gx + 2 + floor(cw + 0.5) + 6 + pw)   -- (groupe plus étroit que la pastille : elle fixe la largeur)
        end
      end
    end
  end
  for i = nCap + 1, #caps do if caps[i] then meth(caps[i], "Hide") end end
  for i = nSep + 1, #seps do if seps[i] then meth(seps[i], "Hide") end end
  if compact and pill then meth(pill, "Hide") end
  for _, b in pairs(btns) do if b and not shown[b] then meth(b, "Hide") end end
  -- « Touches… » : dans la ligne des titres, à droite, tant qu'une liaison manque
  local missing = #S.MissingKeys() > 0 and S.db.keysHint ~= false and showKeys
  if keysBtn then
    if missing and not compact then
      local tw = tonumber(meth(keysBtn.text, "GetStringWidth")) or 0
      if not finite(tw) or tw <= 0 then tw = 8 * 5.4 end
      local w = floor(tw + 12)
      meth(keysBtn, "SetWidth", w)
      meth(keysBtn, "ClearAllPoints")
      meth(keysBtn, "SetPoint", "TOPRIGHT", strip, "TOPLEFT", x, -s.pad)
      meth(keysBtn, "Show")
    else meth(keysBtn, "Hide") end
  end
  meth(strip, "SetSize", x + s.pad, bh + capH + 2 * s.pad)
  S.sizes[compact and "c" or "f"] = { x + s.pad, bh + capH + 2 * s.pad }   -- (gardée : le placement compare sans redisposer)
  local wide = tostring(compact) .. ":" .. floor(x)
  if wide ~= lastWide or not S.placed then lastWide = wide if not placing then S.PlaceStrip() end end
  if S.placed and not (S.db.hideCombat and inCombat()) then meth(strip, "Show") end
end
--- textes qui changent sans changer la taille : nombres, mode choisi, guidage (aussi en combat : cadres non protégés)
function S.Paint()
  local showKeys = not (S.db and S.db.keys == false)
  local guideOn = S.GuideOn()
  for _, st in ipairs(S.lastState or {}) do
    local b = btns[st.id]
    if b then
      b:Set({ icon = st.icon, label = st.label, key = showKeys and S.KeyShort(st.key) or false, count = st.count, active = st.active, disabled = st.disabled })
    end
  end
  if pill and not S.compact then pill:SetOn(guideOn) end
  local gb = btns.guide
  if gb and S.compact then gb:Set({ icon = "Interface\\Icons\\Ability_Tracking", active = guideOn, disabled = not guideOn }) end
end
local function sizeSig(states)
  local t = { S.db and S.db.text ~= false and "T" or "t", S.db and S.db.keys ~= false and "K" or "k", #S.MissingKeys() }
  for _, st in ipairs(states) do t[#t + 1] = (st.label or "") end
  return table.concat(t, ";")
end
local refreshPending, lastSizeSig = false, nil
S.sizes = {}   -- taille (sans échelle) de la bande par forme : f = mots, c = compact ; vidée quand un mot / une touche change
--- relit les modes / nombres / touches ; redispose seulement si une largeur change (regroupé, ≤ 1 / 0,3 s)
function S.Refresh(now_)
  if not stripOn() then return end
  local function run()
    refreshPending = false
    if not stripOn() then return end
    local states = S.State()
    S.lastState = states
    local sig = sizeSig(states)
    if sig ~= lastSizeSig or not strip or (not meth(strip, "IsShown") and not (S.db.hideCombat and inCombat())) then
      if sig ~= lastSizeSig then S.sizes = {} end
      lastSizeSig = sig
      S.LayoutStrip()
    else
      S.Paint()
      -- la boussole TreeAI vient de se poser dessus (elle apparaît avec la 1re cible) : la bande s'écarte, une fois
      if S.placed == "auto" and S.UnderCompass() then S.PlaceStrip() end
    end
  end
  if now_ then run() return end
  if refreshPending then return end
  refreshPending = true
  T:After(0.3, run)
end
--- ce que le joueur voit (tests, /tai boutons, research/look5.md) : « Rendre (4) [Alt-1] * » par bouton, par groupe
function S.Dump()
  local out = {}
  local states = S.lastState or S.State()
  local nx = S.NextAction(states)
  out[#out + 1] = "Guidage quêtes : " .. (S.GuideOn() and "ON" or "OFF")
  for _, st in ipairs(states) do
    local s = st.label .. ((tonumber(st.count) or 0) > 0 and (" (" .. st.count .. ")") or "")
    if st.key then s = s .. " [" .. st.key .. "]" end
    if st.active then s = s .. " *" end
    if st.disabled then s = s .. " (coupé)" end
    if nx and nx.id == st.id then s = s .. " <" end
    out[#out + 1] = s
  end
  return out
end
--- la bande en une ligne, par groupes : « Quêtes [Guidage quêtes ON] : Rendre (2) · … | Perso : … | Groupe | TreeAI »
function S.DumpLine()
  local states = S.lastState or S.State()
  local parts = {}
  for _, grp in ipairs(S.GROUPS) do
    local names = {}
    for _, st in ipairs(states) do
      if st.group == grp.id then
        names[#names + 1] = st.label .. ((tonumber(st.count) or 0) > 0 and (" (" .. st.count .. ")") or "") ..
          (st.key and (" [" .. st.key .. "]") or "") .. (st.active and " *" or "") .. (st.disabled and " (coupé)" or "")
      end
    end
    if #names > 0 then
      local head = grp.cap and (grp.cap .. (grp.id == "quetes" and (" [Guidage quêtes " .. (S.GuideOn() and "ON" or "OFF") .. "]") or "") .. " : ") or ""
      parts[#parts + 1] = head .. table.concat(names, " · ")
    end
  end
  return table.concat(parts, "  |  ")
end
--- contrôle : chaque bouton montré a une action et une infobulle → liste des manques (vide = tout va bien)
function S.Audit()
  local bad = {}
  local seen = 0
  for id, b in pairs(S.Buttons()) do
    seen = seen + 1
    if type(meth(b, "GetScript", "OnClick")) ~= "function" then bad[#bad + 1] = id .. " : pas d'action" end
    if type(meth(b, "GetScript", "OnEnter")) ~= "function" then bad[#bad + 1] = id .. " : pas d'infobulle" end
    local lines = S.TipLines(id)
    if #lines < 2 then bad[#bad + 1] = id .. " : infobulle vide" end
    if id ~= "guide" and not DEF[id] then bad[#bad + 1] = id .. " : action inconnue" end
  end
  return bad, seen
end
--- (Window.ShowHUD) « Réinitialiser l'interface » : la bande revient à sa place automatique
function S.ShowHUD(resetPos)
  if not stripOn() then return false end
  if resetPos and S.char then S.char.strip = nil end
  lastWide = nil
  S.Refresh(true)
  if strip and resetPos then S.PlaceStrip(true) end
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
  -- 5.0 : la bande a changé de forme et de taille → une fois par personnage, elle reprend sa place automatique
  if S.char and not S.char.v5 then S.char.v5 = true S.char.strip = nil end
  -- dessinée une fois l'écran posé, puis sur les seuls événements qui changent un nombre / une touche
  T:After(1.5, function() if S._active then S.Refresh(true) end end)
  local function soon() S.Refresh() end
  -- journal de quêtes : le rappel regroupé du cœur s'il existe (≥ 0,3 s après le dernier événement), sinon l'événement
  local okL, sub = false, nil
  if type(T.OnQuestLog) == "function" then okL, sub = pcall(T.OnQuestLog, T, self, soon) end
  if okL and sub ~= false then S._coreLog = true else self:RegisterEvent("QUEST_LOG_UPDATE", soon) end
  for _, ev in ipairs({ "ZONE_CHANGED_NEW_AREA", "UPDATE_BINDINGS" }) do self:RegisterEvent(ev, soon) end
  -- le groupe se forme (cadres de groupe du jeu), l'écran change : la bande non posée par le joueur ne bouge que si
  -- un cadre du jeu vient dessus (S.PlaceStrip : stable)
  local function replace()
    if not (strip and stripOn()) or (S.char and S.char.strip and S.OnScreen() ~= false) then return end
    S.PlaceStrip()
  end
  for _, ev in ipairs({ "GROUP_ROSTER_UPDATE", "PLAYER_ENTERING_WORLD", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED" }) do
    self:RegisterEvent(ev, function() soon() T:After(1, replace) end)
  end
  -- « cacher en combat » (option) : la bande s'efface à l'entrée en combat et revient à la sortie
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if strip and S.db and S.db.hideCombat then meth(strip, "Hide") end end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    if strip and S.db and S.db.hideCombat and stripOn() and S.placed then meth(strip, "Show") S.Refresh() end
  end)
  self:On("LAYOUT_DONE", replace)
  self:On("QUEST_MODE_CHANGED", function() S.Refresh(true) end)
  self:On("QUEST_GUIDE_CHANGED", function() S.Refresh(true) end)
  self:On("QUEST_BRAIN_UPDATED", soon)
  self:On("NEXT_CHANGED", function() if S.lastState then S.Refresh() end end)
  -- (ONBOARD) la visite guidée montre la bande (sinon la barre d'actions si le joueur l'a remise)
  local Tr = T.Tour
  if type(Tr) == "table" and type(Tr.Target) == "function" then
    Tr.Target("quick", function()
      if strip and meth(strip, "IsVisible") then return strip end
      return (dock and meth(dock, "IsVisible")) and dock or nil
    end)
  end
end
function S:OnDisable()
  S._building = nil
  if strip then meth(strip, "Hide") end
  T:RunOutOfCombat("Shortcuts:layout", function()
    for _, id in ipairs(segOrder) do undockSeg(segs[id]) end
    if dock then meth(dock, "Hide") end
  end)
end
function S:OnOptionChanged(key)
  if key == "strip" or key == "text" or key == "keys" or key == "keysHint" then
    lastSizeSig, lastWide, S.compact = nil, nil, nil
    if stripOn() then S.Refresh(true) elseif strip then meth(strip, "Hide") end
    return
  end
  if key == "hideCombat" then
    if strip and stripOn() then if S.db.hideCombat and inCombat() then meth(strip, "Hide") else S.Refresh(true) end end
    return
  end
  if key == "stripScale" then lastWide = nil S.PlaceStrip(true) return end
  if key == "stripLocked" or key == "guide" then S.Refresh(true) return end
  if key == "scale" then placeDock() end
  S.Layout()
end

--- /tai barre · /tai interface reset : la bande revient à coup sûr (module, option, taille, place automatique) ;
--- /tai barre actions : en plus la barre d'actions du bas (objet de quête, familier, aspect, manger)
function S.Reset(arg)
  if not S._active then T:SetModuleEnabled("Shortcuts", true) end
  if not S.db then return false end
  S.db.strip, S.db.stripScale = true, max(70, min(150, tonumber(S.db.stripScale) or 100))
  if S.char then S.char.strip = nil end
  lastSizeSig, lastWide, S.compact, S.placed, S.autoSig = nil, nil, nil, nil, nil
  S.Refresh(true)
  if strip then S.PlaceStrip(true) meth(strip, "SetAlpha", 1) end
  if tostring(arg or ""):lower():find("action") then
    for _, n in ipairs({ "Quick", "QuickFight" }) do
      local m = T.modules and T.modules[n]
      if m and not m._active then T:SetModuleEnabled(n, true) end
    end
    S.db.actionBar, S.db.dock = true, true
    S:OnOptionChanged("actionBar")
  end
  flash("Actions rapides : bande remise à sa place." .. (S.db.actionBar and " Barre d'actions : en bas." or ""))
  return true
end
--- « /tai interface reset » : TOUT ce que TreeAI pose à l'écran revient à sa place et à sa taille d'origine (bande,
--- fenêtre, boussole, alerte, cartes) ; « compact » / « complet » : la forme de la bande. Hors combat pour les cadres
--- du cœur (il le dit) ; la bande, elle, revient tout de suite.
function S.ResetInterface(arg)
  arg = tostring(arg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if arg == "compact" or arg == "complet" then
    if S.db then S.db.text = (arg == "complet") end
    S:OnOptionChanged("text")
    flash(arg == "compact" and "Bande : mode compact (icônes seules)." or "Bande : mots sous les icônes.")
    return arg
  end
  if arg ~= "reset" and arg ~= "raz" then
    -- « /tai interface » seul : la page où tout se règle (rien n'est remis à zéro sans le dire)
    flash("/tai interface reset : tout remettre à sa place · compact | complet : forme de la bande.")
    openPage("display")
    return "help"
  end
  if S.db then S.db.stripScale, S.db.text, S.db.hideCombat, S.db.stripLocked = 100, true, false, false end
  S.Reset()
  local Wn = T.Window
  if Wn and type(Wn.ResetPlace) == "function" then pcall(Wn.ResetPlace) end
  if inCombat() then
    flash("Interface : la bande est revenue ; le reste (boussole, alerte) après le combat.")
    T:RunOutOfCombat("Shortcuts:interface", function() if T.ResetInterface then T:ResetInterface() end end)
    return "combat"
  end
  if T.ResetInterface then T.safecall(T.ResetInterface, T) end
  flash("Interface remise à zéro : chaque cadre TreeAI est revenu à sa place et à sa taille.")
  return "reset"
end
T:RegisterSlash("interface", function(arg) S.ResetInterface(arg) end,
  "interface reset : tout remettre à sa place (/tai interface compact | complet : forme de la bande)")
T:RegisterSlash("barre", function(arg) S.Reset(arg) end,
  "actions rapides remises à l'écran (/tai barre [actions] : aussi la barre d'actions du bas)")

T:RegisterSlash("boutons", function(arg)
  arg = tostring(arg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if arg == "touches" then S.SuggestKeys() return end
  if DEF[arg] or arg == "mode" or arg == "guide" or arg == "next" then S.Do(arg) return end
  -- allumée mais pas à l'écran : on la remet (jamais coupée par un « /tai boutons » pour la voir)
  if S.db and S.db.strip ~= false and not (strip and meth(strip, "IsVisible")) then S.Reset() return end
  if S.db then S.db.strip = S.db.strip == false end
  S:OnOptionChanged("strip")
end, "actions rapides (/tai boutons [rendre|prendre|suivant|zone|vite|sacs|equip|metiers|groupe|journal|guide|next|mode|touches])")
-- « /tai guide » : allumer / couper le guidage des quêtes (comme la pastille de la bande) — sauf si le cœur l'a déjà
if not (T.HasSlash and T:HasSlash("guide")) then
  T:RegisterSlash("guide", function(arg)
    arg = tostring(arg or ""):lower()
    if arg:find("on") or arg:find("oui") then S.SetGuide(true) elseif arg:find("off") or arg:find("non") then S.SetGuide(false)
    else S.ToggleGuide() end
  end, "guidage des quêtes : allumer / couper (/tai guide [on|off])")
end
