-- TreeAI — module Addons « Mes addons » (agent REPLACE, 4.0.1) : jouer avec TreeAI seul, les autres addons en
-- silence ou désinstallés. Une page de la fenêtre TreeAI (« Mes addons ») : pour chaque addon trouvé chez le joueur,
-- ce que TreeAI fait à sa place, ce qui manque encore, et le clic qui règle ça :
--  * Questie : TreeAI garde SA BASE (QuestieDB, lue en jeu, rien de copié) et dessine lui-même les icônes (QuestPins) et
--    le suivi (Watch). « Rendre Questie silencieux » écrit dans SES options (Questie.db.profile : enabled = icônes,
--    trackerEnabled = suivi, minimap.hide = bouton — ce que sa page d'options écrit elle-même), puis recharge
--    l'interface (Questie le demande pour son suivi) ; « Réafficher Questie » remet ce qu'il y avait. Options
--    illisibles : les 3 clics exacts dans ses options, rien d'écrit ;
--  * Shortest Path Forever : TreeAI trace et guide ; son suivi (horaires des bateaux, voyage) se coupe / revient par
--    SES options sauvegardées (ShortestPathForeverDB.tracker / journeyTracker), sans recharge ;
--  * Leatrix Plus → module « Confort » ; Details → module « Dégâts » (compteur intégré du jeu, C_DamageMeter) ;
--    Auctionator, DungeonJournal, Forever Loot Sparkles, Tweaks Forever : ce que TreeAI couvre, honnêtement.
-- Une ligne une seule fois (« Maintenant », info) quand un de ces addons est là. Lecture seule ailleurs, aucun cadre
-- d'un autre addon touché, rien en combat. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local A = T:RegisterModule("Addons", {
  title = "Mes addons",
  description = "Ce que TreeAI remplace (Questie, Suivi Forever, Leatrix Plus, Details…) et le clic qui met les autres en silence.",
  defaults = { enabled = true, noticeV = 0 },
  charDefaults = {},
})
T.Addons = A
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[#T.OPTIONS_FAMILY]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "Addons" then has = true end end
    if not has then table.insert(fam.mods, 1, "Addons") end
  end
end

local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function inCombat() return T.InCombat and T:InCombat() or false end
local ICON = "Interface\\Icons\\"
A.NOTICE_V = 1

-- ===================================================================== addons installés (lecture seule)
local function loadedAddon(name)
  local CA = g("C_AddOns")
  if type(CA) == "table" and type(CA.IsAddOnLoaded) == "function" then
    local r = try(CA.IsAddOnLoaded, name)
    if r ~= nil then return r and true or false end
  end
  return try(g("IsAddOnLoaded"), name) and true or false
end
-- clé → motif sur le nom de dossier (minuscules)
local KNOWN = {
  { key = "questie", test = function(l) return l == "questie" end },
  { key = "questiedb", test = function(l) return l == "questiedb" end },
  { key = "spf", test = function(l) return l:find("shortestpath", 1, true) ~= nil end },
  { key = "details", test = function(l) return l == "details" or l:find("^details_") ~= nil end },
  { key = "leatrix", test = function(l) return l:find("leatrix", 1, true) ~= nil end },
  { key = "auctionator", test = function(l) return l:find("auctionator", 1, true) ~= nil end },
  { key = "journal", test = function(l) return l:find("dungeon", 1, true) ~= nil and l:find("journal", 1, true) ~= nil end },
  { key = "sparkles", test = function(l) return l:find("sparkle", 1, true) ~= nil end },
  { key = "tweaks", test = function(l) return l:find("tweaks", 1, true) ~= nil end },
}
--- { [clé] = { name, title, loaded } } des addons connus trouvés dans la liste du jeu (mémo 30 s)
local seenAt, seen = -100, {}
function A.Installed(force)
  local t = try(g("GetTime")) or 0
  if not force and t - seenAt < 30 then return seen end
  seenAt, seen = t, {}
  local CA = g("C_AddOns")
  local n = (type(CA) == "table" and try(CA.GetNumAddOns)) or try(g("GetNumAddOns")) or 0
  for i = 1, math.min(tonumber(n) or 0, 400) do
    local name, title = nil, nil
    if type(CA) == "table" and type(CA.GetAddOnInfo) == "function" then name, title = try(CA.GetAddOnInfo, i)
    else name, title = try(g("GetAddOnInfo"), i) end
    if type(name) == "string" then
      local l = name:lower()
      for _, k in ipairs(KNOWN) do
        if not seen[k.key] and k.test(l) then
          seen[k.key] = { name = name, title = type(title) == "string" and title or name, loaded = loadedAddon(name) }
        end
      end
    end
  end
  -- chargés sans être listés (tests, noms de dossier différents) : leurs globales publiques
  if not seen.questie and type(g("Questie")) == "table" then seen.questie = { name = "Questie", title = "Questie", loaded = true } end
  if not seen.questiedb and type(g("LibQuestieDB")) == "table" then seen.questiedb = { name = "QuestieDB", title = "QuestieDB", loaded = true } end
  if not seen.spf and type(g("ShortestPathForever")) == "table" then
    seen.spf = { name = "ShortestPathForever", title = "Shortest Path Forever", loaded = true }
  end
  return seen
end

-- ===================================================================== Questie : en silence par SES options
local function questieProfile()
  local Q = g("Questie")
  if type(Q) ~= "table" then return nil end
  local db = rawget(Q, "db")
  local p = type(db) == "table" and db.profile or nil
  return type(p) == "table" and p or nil
end
--- base de quêtes lue par TreeAI ? (QuestieDB / Questie, via la boussole ou l'export)
function A.QuestDB()
  local C = T.Compass
  local db = C and type(C.Questie) == "function" and try(C.Questie) or nil
  if type(db) == "table" then return db.lib and "QuestieDB" or "Questie" end
  local X = T.Export
  local s = X and type(X.Source) == "function" and try(X.Source, X) or nil
  return type(s) == "table" and (s.src == "LibQuestieDB" and "QuestieDB" or "Questie") or nil
end
--- { loaded, readable, icons, tracker, button, silent, db }
function A.QuestieState()
  local p = questieProfile()
  local loaded = type(g("Questie")) == "table" or type(g("QuestieLoader")) == "table" or loadedAddon("Questie")
  local st = { loaded = loaded and true or false, readable = p ~= nil, db = A.QuestDB() }
  if p then
    st.icons = p.enabled ~= false and (p.enableMapIcons ~= false or p.enableMiniMapIcons ~= false)
    st.tracker = p.trackerEnabled ~= false
    st.button = not (type(p.minimap) == "table" and p.minimap.hide == true)
  elseif st.loaded then
    st.icons, st.tracker, st.button = true, true, true    -- illisible : on suppose tout visible (jamais en double)
  end
  st.silent = st.loaded and st.readable and not st.icons and not st.tracker or false
  return st
end
A.QUESTIE_CLICKS = "Échap > Options > AddOns > Questie : onglet « Icônes », décoche « Activer les icônes » ; "
  .. "onglet « Suivi », décoche « Activer le suivi »."
local function reload()
  local f = g("ReloadUI")
  if type(f) == "function" then return pcall(f) end
  local CU = g("C_UI")
  if type(CU) == "table" and type(CU.Reload) == "function" then return pcall(CU.Reload) end
  return false
end
--- un clic : icônes, suivi et bouton de minimap de Questie coupés dans SES options ; recharge (doReload ~= false)
function A.SilenceQuestie(doReload)
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return false, "combat" end
  local p = questieProfile()
  if not p then
    if T.ClickNote then T:ClickNote("Questie : " .. A.QUESTIE_CLICKS) end
    return false, "unreadable"
  end
  if A.db then
    A.db.questieWas = { enabled = p.enabled ~= false, tracker = p.trackerEnabled ~= false,
      button = not (type(p.minimap) == "table" and p.minimap.hide == true) }
  end
  p.enabled = false
  p.trackerEnabled = false
  if type(p.minimap) == "table" then p.minimap.hide = true end
  if doReload ~= false then reload() end
  return true
end
--- remet Questie comme avant (ou tout visible) ; recharge
function A.RestoreQuestie(doReload)
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return false, "combat" end
  local p = questieProfile()
  if not p then return false, "unreadable" end
  local was = A.db and A.db.questieWas or {}
  p.enabled = was.enabled ~= false
  p.trackerEnabled = was.tracker ~= false
  if type(p.minimap) == "table" then p.minimap.hide = not (was.button ~= false) end
  if A.db then A.db.questieWas = nil end
  if doReload ~= false then reload() end
  return true
end

-- ===================================================================== Shortest Path Forever : son suivi
local function spfDB() local d = g("ShortestPathForeverDB") return type(d) == "table" and d or nil end
function A.SPFState()
  local inst = A.Installed()
  local d = spfDB()
  local st = { loaded = (inst.spf and inst.spf.loaded) or type(g("ShortestPathForever")) == "table" or false, readable = d ~= nil }
  if d then st.tracker = d.tracker ~= false or d.journeyTracker ~= false end
  return st
end
function A.SetSPFTracker(on)
  local d = spfDB()
  if not d then return false end
  if on then
    local was = A.db and A.db.spfWas or {}
    d.tracker, d.journeyTracker = was.tracker ~= false, was.journey ~= false
    if A.db then A.db.spfWas = nil end
  else
    if A.db then A.db.spfWas = { tracker = d.tracker ~= false, journey = d.journeyTracker ~= false } end
    d.tracker, d.journeyTracker = false, false
  end
  return true
end

-- ===================================================================== ce que TreeAI remplace (textes de la page)
local function modOn(name) local m = T.modules and T.modules[name] return (m and m._active) and true or false end
local function setMod(name, on) if T.SetModuleEnabled then T:SetModuleEnabled(name, on) end end
local function openWin(id) local Wn = T.Window if Wn and Wn.Open then Wn.Open(id) end end

--- sections de la page : { {title, icon, lines = {textes}, buttons = { {label, fn, tip} }, ok = bool} }
function A.Sections()
  local inst = A.Installed()
  local out = {}
  local q = A.QuestieState()
  -- Questie
  if q.loaded or inst.questiedb then
    local lines, buttons = {}, {}
    lines[#lines + 1] = q.db and ("Base de quêtes lue par TreeAI : " .. q.db .. " (à garder installée).")
      or "Base de quêtes illisible : TreeAI n'a que les points du jeu (quêtes du journal)."
    if q.loaded then
      if q.silent then
        lines[#lines + 1] = "Questie est silencieux : icônes et suivi dessinés par TreeAI (« Icônes de quêtes », « Suivi TreeAI »)."
        buttons[#buttons + 1] = { "Réafficher Questie", function() A.RestoreQuestie(true) end, "Rend ses icônes, son suivi et son bouton (recharge l'interface)" }
      elseif q.readable then
        lines[#lines + 1] = "Questie dessine encore " .. ((q.icons and q.tracker) and "ses icônes et son suivi"
          or (q.icons and "ses icônes") or "son suivi") .. " : TreeAI se tait pour ne rien montrer en double."
        buttons[#buttons + 1] = { "Rendre Questie silencieux", function() A.SilenceQuestie(true) end,
          "Coupe ses icônes, son suivi et son bouton de minimap dans SES options ; sa base reste lue par TreeAI. Recharge l'interface." }
      else
        lines[#lines + 1] = "Options de Questie illisibles ici. À la main : " .. A.QUESTIE_CLICKS
      end
    end
    out[#out + 1] = { title = "Questie", icon = ICON .. "INV_Misc_Book_09", lines = lines, buttons = buttons, ok = q.silent or not q.loaded }
  end
  -- Shortest Path Forever
  local s = A.SPFState()
  if s.loaded then
    local lines, buttons = {}, {}
    lines[#lines + 1] = "TreeAI calcule et guide le trajet (vols, bateaux, foyer), le dessine sur la carte et la minimap ; "
      .. "Suivi Forever reste utile pour contourner les falaises (son navmesh) : Options > Trajet > Guidage."
    if s.readable then
      if s.tracker then
        lines[#lines + 1] = "Son suivi est affiché (horaires des bateaux, voyage en cours)."
        buttons[#buttons + 1] = { "Couper son suivi", function() A.SetSPFTracker(false) end, "Dans SES options : son suivi se cache, rien d'autre ne change" }
      else
        lines[#lines + 1] = "Son suivi est coupé."
        buttons[#buttons + 1] = { "Remettre son suivi", function() A.SetSPFTracker(true) end, "Remet son suivi comme avant" }
      end
    end
    out[#out + 1] = { title = "Shortest Path Forever (Suivi Forever)", icon = ICON .. "INV_Misc_Map_01", lines = lines, buttons = buttons, ok = not s.tracker }
  end
  -- Leatrix Plus → Confort
  if inst.leatrix then
    local on = modOn("QoL")
    out[#out + 1] = { title = "Leatrix Plus : le « Confort » de TreeAI", icon = ICON .. "INV_Misc_Gear_01", ok = on,
      lines = { "Vendre les gris + réparer, butin instantané, butin automatique, recul de caméra : module « Confort »"
        .. (on and " (allumé)." or " (coupé)."),
        "Pas repris : ce qui modifie les cadres de Blizzard (masquer des messages, déplacer des cadres) — risque de blocages." },
      buttons = { on and { "Réglages du confort", function() openWin("options") end, "La page « Confort » des options" }
        or { "Allumer le confort", function() setMod("QoL", true) end, "Un clic : vendre les gris, réparer, butin instantané" } } }
  end
  -- Details → Dégâts
  if inst.details then
    local M = T.Meter
    local avail, why = true, nil
    if M and type(M.Available) == "function" then avail, why = M.Available() end
    local on = modOn("Meter")
    out[#out + 1] = { title = "Details : la fenêtre « Dégâts » de TreeAI", icon = ICON .. "Ability_Hunter_SniperShot", ok = on and avail,
      lines = { avail and "Le jeu a son propre compteur (C_DamageMeter) : la fenêtre « Dégâts » de TreeAI le lit après chaque combat."
        or ("Compteur du jeu indisponible : " .. tostring(why or "?") .. ". Details reste utile ici."),
        "Pendant le combat les chiffres sont secrets pour les addons (règle de Forever) : mis à jour à la fin du combat." },
      buttons = { on and { "Montrer / cacher Dégâts", function() if M and M.Toggle then M.Toggle() end end, "La petite fenêtre des dégâts" }
        or { "Allumer Dégâts", function()
          setMod("Meter", true)
          local Mt = T.Meter
          if Mt and Mt.db then Mt.db.shown = true if Mt.Apply then Mt.Apply() end end
        end, "Un clic : la fenêtre des dégâts du groupe" } } }
  end
  if inst.auctionator then
    out[#out + 1] = { title = "Auctionator", icon = ICON .. "INV_Misc_Coin_02", ok = false,
      lines = { "TreeAI : page « HV » (achats-reventes sûrs, ventes, bilan) et il relit les scans d'Auctionator.",
        "Garde Auctionator pour mettre en vente en masse et chercher vite : TreeAI ne le remplace pas encore." },
      buttons = { { "Page HV", function() openWin("hv") end, "Hôtel des ventes de TreeAI" } } }
  end
  if inst.tweaks then
    out[#out + 1] = { title = inst.tweaks.title or "Tweaks Forever", icon = ICON .. "INV_Misc_Wrench_01", ok = false,
      lines = { "Compatibilité HV pour les addons d'HV sur Forever : à garder tant qu'Auctionator est installé (TreeAI n'en a pas besoin)." } }
  end
  if inst.journal then
    out[#out + 1] = { title = "DungeonJournal", icon = ICON .. "INV_Misc_Key_03", ok = false,
      lines = { "TreeAI : page « Donjons » (quêtes du donjon, qui n'a pas quoi, prêts, donjons déjà faits).",
        "Garde-le pour les fiches de boss et leur butin complet : TreeAI ne les a pas." },
      buttons = { { "Page Donjons", function() openWin("dungeon") end, "Donjons de TreeAI" } } }
  end
  if inst.sparkles then
    out[#out + 1] = { title = "Forever Loot Sparkles", icon = ICON .. "INV_Misc_Gem_Pearl_04", ok = false,
      lines = { "Effet visuel dans le monde : TreeAI ne dessine rien dans le monde 3D (il surligne seulement tes sacs et le butin). À garder si tu l'aimes." } }
  end
  return out
end

-- ===================================================================== page « Mes addons »
local function render(Ly)
  local W = T.Window
  local secs = A.Sections()
  Ly.text("TreeAI fait le travail de tes autres addons quand il le peut ; ce qui manque est dit ici, sans détour.", "S", "muted")
  if #secs == 0 then
    Ly.empty("Aucun des addons qu'il remplace n'est installé : rien à régler.")
    return
  end
  for i, s in ipairs(secs) do
    if Ly.header(s.title, "addons:" .. i, s.ok and "réglé" or nil, s.icon) then
      for _, l in ipairs(s.lines or {}) do Ly.text(l, "S", "text", 6) end
      local b = {}
      for _, x in ipairs(s.buttons or {}) do
        if type(x) == "table" and x[1] and x[2] then b[#b + 1] = { x[1], function() x[2]() if W and W.Dirty then W.Dirty() end end, x[3] } end
      end
      if #b > 0 then Ly.buttons(b, math.min(2, #b)) end
    end
  end
end
A.Render = render

-- page de la fenêtre, hors navigation (la pastille garde sa hauteur) : ouverte par « Outils » / la pastille
-- (entrée « Mes addons »), la ligne d'avis et /tai addons
do
  local W = T.Window
  if W and W.AddPage then
    W.AddPage({ id = "addons", hidden = true, label = "Mes addons", icon = ICON .. "INV_Misc_EngGizmos_27", title = "Mes addons",
      desc = "Ce que TreeAI remplace, et le clic qui met les autres en silence",
      sub = function()
        local q = A.QuestieState()
        if q.loaded and not q.silent then return "Questie dessine encore : un clic pour qu'il se taise" end
        return "Questie, Suivi Forever, Leatrix Plus, Details : ce que TreeAI fait à leur place"
      end,
      render = render })
    -- rangée avec l'autre page cachée (« Onglet »), avant « Fonctions » / « Options » qui restent en dernier
    local pages, at, from = W.PAGES, nil, nil
    for i, pg in ipairs(pages) do
      if pg.id == "addons" then from = i elseif not at and (pg.id == "modules" or pg.id == "options") then at = i end
    end
    if from and at and from > at then table.insert(pages, at, table.remove(pages, from)) end
  end
end
function A.Open()
  local W = T.Window
  if W and W.Open then W.Open("addons") return true end
  return false
end
T.HUB_EXTRA = T.HUB_EXTRA or {}
table.insert(T.HUB_EXTRA, { group = "reglages", id = "addons", title = "Mes addons", icon = ICON .. "INV_Misc_EngGizmos_27",
  tip = "Clic : ce que TreeAI remplace (Questie, Suivi Forever, Leatrix Plus, Details) et le clic qui les met en silence",
  fn = function() A.Open() end })
if T.RegisterSlash then
  T:RegisterSlash("addons", function() if not A.Open() then A:Print("La fenêtre TreeAI est coupée.") end end,
    "addons : ce que TreeAI remplace (Questie silencieux, confort, dégâts)")
end

-- ===================================================================== une ligne, une fois
function A.Notice()
  if not (A.db and A._active) or (tonumber(A.db.noticeV) or 0) >= A.NOTICE_V then return false end
  local inst = A.Installed(true)
  local q = A.QuestieState()
  local names = {}
  if q.loaded and not q.silent then names[#names + 1] = "Questie" end
  if inst.leatrix then names[#names + 1] = "Leatrix Plus" end
  if inst.details then names[#names + 1] = "Details" end
  if inst.spf and A.SPFState().tracker then names[#names + 1] = "Suivi Forever" end
  if #names == 0 then return false end
  A.db.noticeV = A.NOTICE_V
  local list = (#names == 1) and names[1] or (table.concat(names, ", ", 1, #names - 1) .. " et " .. names[#names])
  A:Suggest("notice", fmt("TreeAI peut faire le travail de %s : page « Mes addons »", list), 2, {
    ttl = 900, tab = "now", sound = false, kind = "always", icon = ICON .. "INV_Misc_EngGizmos_27",
    tooltip = "Questie en silence (TreeAI dessine les icônes et le suivi), confort à la Leatrix, fenêtre de dégâts.",
    onClick = function() A.Open() A:Clear("notice") end })
  return true
end

function A:OnEnable()
  if T.After then T:After(8, function() pcall(A.Notice) end) end
end
