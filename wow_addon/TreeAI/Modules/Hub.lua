-- TreeAI — module Hub (agent MENU) : la pastille ronde autour de la minimap (comme les vrais addons) + son menu.
--  * Pastille : bouton rond enfant de la minimap (comme LibDBIcon : bordure de suivi, glisser autour du bord,
--    angle sauvé ; minimaps carrées / arrondies selon GetMinimapShape), aussi dans le compartiment d'addons
--    du jeu s'il existe (AddonCompartmentFrame:RegisterAddon, API publique).
--  * Clic gauche : menu « hub » (sections Jeu / Outils / Réglages, icônes) : chaque onglet du panneau, Options,
--    Avantages, Recherche, Meilleur familier, HV, Macros, Touches, Erreurs, Blocages, Profil… tout à la souris.
--    Clic droit : Options. Infobulle : résumé en direct (prochaine étape, XP/h, niveau dans…).
-- Rien d'automatique ; seul le glisser a un OnUpdate (pendant le glisser seulement). Pas de cadre de Blizzard
-- modifié : notre bouton est notre enfant de Minimap (non protégée), jamais de SetScript / SetSize sur elle.
-- Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end

local H = T:RegisterModule("Hub", {
  title = "Pastille de la minimap",
  defaults = { enabled = true, button = true, angle = 205, compartment = true },
  options = {
    { key = "button", type = "toggle", label = "Pastille ronde autour de la minimap (clic : menu de TreeAI)" },
    { key = "compartment", type = "toggle", label = "Aussi dans le compartiment d'addons du jeu (s'il existe)" },
  },
})
T.Hub = H
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Hub then
  T.MODULE_INFO.Hub = { lite = true, desc = "Pastille de la minimap : tout TreeAI en un clic" }
end

local floor, max, min, sqrt = math.floor, math.max, math.min, math.sqrt
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function G(name) return rawget(_G, name) end
local function cfg(k) return not (H.db and H.db[k] == false) end
local function secret(v) local f = G("issecretvalue") return type(f) == "function" and f(v) and true or false end
local L = T.L or setmetatable({}, { __index = function(_, k) return k end })
local ICON = "Interface\\Icons\\"
H.ICON = ICON .. "Spell_Nature_ResistNature"

-- ------------------------------------------------------------------ position (pur : testé)
-- quadrants arrondis (true) ou carrés (false) selon la forme de la minimap (même géométrie que les addons connus)
H.SHAPES = {
  ROUND = { true, true, true, true }, SQUARE = { false, false, false, false },
  ["CORNER-TOPLEFT"] = { false, false, false, true }, ["CORNER-TOPRIGHT"] = { false, false, true, false },
  ["CORNER-BOTTOMLEFT"] = { false, true, false, false }, ["CORNER-BOTTOMRIGHT"] = { true, false, false, false },
  ["SIDE-LEFT"] = { false, true, false, true }, ["SIDE-RIGHT"] = { true, false, true, false },
  ["SIDE-TOP"] = { false, false, true, true }, ["SIDE-BOTTOM"] = { true, true, false, false },
  ["TRICORNER-TOPLEFT"] = { false, true, true, true }, ["TRICORNER-TOPRIGHT"] = { true, false, true, true },
  ["TRICORNER-BOTTOMLEFT"] = { true, true, false, true }, ["TRICORNER-BOTTOMRIGHT"] = { true, true, true, false },
}
--- Décalage (x, y) du centre de la pastille depuis le centre de la minimap pour un angle (degrés).
function H.Offset(angle, w, h, shape)
  local rad = math.rad(tonumber(angle) or 205)
  local x, y, q = math.cos(rad), math.sin(rad), 1
  if x < 0 then q = q + 1 end
  if y > 0 then q = q + 2 end
  local quad = H.SHAPES[shape or "ROUND"] or H.SHAPES.ROUND
  local rw, rh = (w or 140) / 2 + 5, (h or 140) / 2 + 5
  if quad[q] then return x * rw, y * rh end
  local dw, dh = sqrt(2 * rw * rw) - 10, sqrt(2 * rh * rh) - 10
  return max(-rw, min(x * dw, rw)), max(-rh, min(y * dh, rh))
end
--- Angle (degrés, 0–360) d'un point écran par rapport au centre de la minimap.
function H.AngleOf(cx, cy, px, py)
  local a = math.deg(math.atan2(py - cy, px - cx))
  if a < 0 then a = a + 360 end
  return a
end

local button
local function minimap() return G("Minimap") end
local function shape()
  local f = G("GetMinimapShape")
  local s = type(f) == "function" and try(f) or nil
  return type(s) == "string" and s or "ROUND"
end
function H.Place()
  local mm = minimap()
  if not (button and mm) then return end
  local x, y = H.Offset(H.db and H.db.angle, mm:GetWidth(), mm:GetHeight(), shape())
  button:ClearAllPoints()
  button:SetPoint("CENTER", mm, "CENTER", x, y)
end

-- ------------------------------------------------------------------ résumé en direct (infobulle)
local function plain(s) return T.PlainText and T.PlainText(s) or tostring(s or "") end
-- (4.0 QA) un avis du cœur (« Mode sûr… », profil) n'est pas une étape ; « Arrêt 1/20 : » répète l'étiquette
local function isNotice(e) return e.kind == "always" or tostring(e.id or ""):find("^core:") ~= nil end
function H.Summary()
  local out = {}
  local now = T.GetTabLines and T:GetTabLines("now") or {}
  local txt
  -- (4.0 QA) avec la carte « Prochaine étape » : sa première ligne (course au corps, donjon, étape), la même
  local Wn = T.Window
  local top = Wn and Wn._active and Wn.TopLine and select(2, pcall(Wn.TopLine)) or nil
  if type(top) == "table" and type(top.title) == "string" and not secret(top.title) then
    txt = top.title .. ((top.sub and top.sub ~= "" and not secret(top.sub)) and (" · " .. top.sub) or "")
  else
    local first
    for _, e in ipairs(now) do if e.pin and not isNotice(e) then first = e break end end
    if not first then
      for _, e in ipairs(now) do if not (isNotice(e) or e.header or e.control) then first = e break end end
    end
    if first and type(first.text) == "string" and not secret(first.text) then txt = plain(first.text) end
  end
  if txt then
    txt = txt:gsub("^Arrêt %d+/%d+ : ", "")
    out[#out + 1] = { "Prochaine étape : " .. (T.SafeText and T.SafeText(txt) or txt), 1, 1, 1 }
  else
    out[#out + 1] = { "Rien d'urgent.", 0.7, 0.7, 0.7 }
  end
  local B = T.modules and T.modules.Boost
  local r = B and B.XPStats and select(2, pcall(B.XPStats, B))
  if type(r) == "table" and tonumber(r.perHour) and r.perHour > 0 then
    local Fm = T.Fmt
    local xp = Fm and Fm.Num and Fm.Num(r.perHour) or tostring(floor(r.perHour))
    local eta = tonumber(r.eta) and Fm and Fm.Time and Fm.Time(r.eta) or nil
    out[#out + 1] = { "XP/h : " .. xp .. (eta and (" · niveau suivant dans ~" .. eta) or ""), 0.55, 0.8, 1 }
  end
  local n = 0
  for _, e in ipairs(now) do if (e.prio or 1) >= 3 then n = n + 1 end end
  if n > 0 then out[#out + 1] = { n .. " alerte" .. (n > 1 and "s" or "") .. " urgente" .. (n > 1 and "s" or ""), 1, 0.35, 0.3 } end
  return out
end
local function showTip(owner)
  local tt = G("GameTooltip")
  if not tt then return end
  tt:SetOwner(owner, "ANCHOR_LEFT")
  tt:AddLine("TreeAI Coach", 1, 0.82, 0.2)
  for _, l in ipairs(H.Summary()) do tt:AddLine(l[1], l[2], l[3], l[4], true) end
  local Wn = T.Window
  if Wn and Wn._active then tt:AddLine("Clic : fenêtre TreeAI · Clic droit : menu rapide · Glisser : déplacer", 0.4, 0.8, 1, true)
  else tt:AddLine("Clic : menu · Clic droit : options · Glisser : déplacer", 0.5, 0.5, 0.5, true) end
  tt:Show()
end
local function hideTip() local tt = G("GameTooltip") if tt then tt:Hide() end end

-- ------------------------------------------------------------------ entrées du menu (tout à la souris)
local function act(fn, ...) local a = { ... } return function() T:UserAction(function() fn(unpack(a)) end) end end
local function hasSlash(c) return T.HasSlash and T:HasSlash(c:match("^(%S+)")) or false end
-- (UIREDO) un onglet = sa page dans la fenêtre TreeAI (le panneau texte seulement si la fenêtre est coupée)
local function tab(id)
  return act(function()
    local Wn = T.Window
    if Wn and Wn.OpenTab and Wn._active then Wn.OpenTab(id) else T:ShowPanel() T:SelectTab(id) end
  end)
end
H.GROUPS = {
  { id = "jeu", title = "Jeu", icon = ICON .. "INV_Misc_Map_01" },
  { id = "outils", title = "Outils", icon = ICON .. "INV_Misc_Wrench_01" },
  { id = "reglages", title = "Réglages", icon = ICON .. "INV_Misc_Gear_01" },
  { id = "beta", title = "Fonctions bêta", icon = ICON .. "INV_Misc_EngGizmos_20" },
}
-- (REVIEW) mode sûr : la liste des fonctions bêta (un clic = allumer / couper, le menu reste ouvert)
H.mode = "main"
function H.BetaEntries()
  local out = {}
  out[#out + 1] = { group = "beta", id = "back", title = "< Retour", icon = ICON .. "INV_Misc_Note_01",
    tip = "Clic : revenir au menu", desc = "Le menu principal", keep = true, fn = function() H.mode = "main" end }
  local prof = T.ModuleProfile and T:ModuleProfile() or "tout"
  out[#out + 1] = { group = "beta", id = "profile", keep = true, icon = ICON .. "INV_Misc_Book_09",
    title = prof == "sur" and "Tout allumer (bêta compris)" or "Revenir au mode sûr",
    desc = prof == "sur" and "Mode sûr actif" or "Tout est allumé",
    tip = "Mode sûr = quêtes, zone de travail, trajet (et son tracé), boussole, carte « Prochaine étape », fenêtre TreeAI, surbrillance des objets de quête, course au corps, erreurs, macros.",
    fn = act(function() T:SetModuleProfile(prof == "sur" and "tout" or "sur") end) }
  for _, n in ipairs(T.moduleOrder or {}) do
    local m = T.modules[n]
    if m and T.IsBeta and T:IsBeta(n) and m._classOK ~= false then
      local on = m.db and m.db.enabled ~= false
      local info = T.MODULE_INFO and T.MODULE_INFO[n] or {}
      out[#out + 1] = { group = "beta", id = "beta:" .. n, keep = true, on = on,
        title = tostring(m.title or n), state = on and "|cff33dd33ON|r" or "|cff888888OFF|r",
        icon = on and "Interface\\RaidFrame\\ReadyCheck-Ready" or "Interface\\RaidFrame\\ReadyCheck-NotReady",
        tip = (info.desc or n) .. "\nBêta : testé hors du jeu seulement.\nClic : " .. (on and "couper" or "allumer"),
        fn = act(function() T:SetModuleEnabled(n, not on) end) }
    end
  end
  return out
end
local TAB_DESC, ENTRY_DESC
local TAB_ICON = {
  now = ICON .. "Spell_Nature_TimeStop", quests = ICON .. "INV_Misc_Note_02", group = ICON .. "Ability_Hunter_Pet_Wolf",
  hunter = ICON .. "Ability_Hunter_BeastCall", hv = ICON .. "INV_Misc_Coin_01", prof = ICON .. "Trade_BlackSmithing",
  talents = ICON .. "Ability_Marksmanship", gear = ICON .. "INV_Chest_Chain", dungeon = ICON .. "INV_Misc_Key_03",
  pvp = ICON .. "Ability_DualWield", info = ICON .. "INV_Misc_QuestionMark", binds = ICON .. "INV_Misc_Key_14",
  adv = ICON .. "Ability_Hunter_SniperShot", finder = ICON .. "INV_Misc_Spyglass_02", stats = ICON .. "INV_Misc_PocketWatch_01",
}
-- une description courte sous chaque entrée (lanceur groupé, comme les menus des gros addons)
TAB_DESC = {
  now = "Ce que tu peux faire tout de suite", quests = "Étapes, quêtes par zone, trajet", group = "Tes amis et les missions",
  hunter = "Familier, munitions, pièges", hv = "Achats-reventes (bêta)", prof = "Montée de métier", talents = "Où mettre tes points",
  gear = "Améliorations par emplacement", dungeon = "Préparation et boss", pvp = "Aides JcJ", info = "Autotest, erreurs, détails",
  binds = "Raccourcis clavier conseillés", adv = "Petits avantages (bêta)", modules = "Allumer / couper chaque fonction",
  finder = "Meilleur familier, rares, maîtres", stats = "Tes chiffres mesurés",
}
ENTRY_DESC = {
  cherche = "Familier, rares, maîtres, vols près de toi", pet = "La meilleure bête à apprivoiser", macros = "Créer et poser les macros",
  todo = "La liste complète des quêtes", boussole = "Afficher / cacher la flèche", radar = "Petit radar rond",
  options = "Taille, transparence, sons", erreurs = "Fichier d'erreurs pour l'appli", bloque = "Actions refusées par le jeu",
  panel = "Le panneau de TreeAI", edit = "Glisser les cadres, molette = taille", reset = "Tout remettre à sa place",
  profil = "Testées hors du jeu : un clic chacune",
}
H.TAB_ICON, H.TAB_DESC = TAB_ICON, TAB_DESC   -- (UIREDO) mêmes icônes / descriptions dans la fenêtre TreeAI
function H.Entries()
  if H.mode == "beta" then return H.BetaEntries() end
  local out = {}
  local Wn = T.Window
  local hasWin = Wn and Wn._active and Wn.Open and type(Wn.PAGES) == "table"
  if hasWin then
    -- (4.0 QA) avec la fenêtre TreeAI (clic gauche sur la pastille) : « Jeu » = ses pages, une ligne chacune
    -- (sans description : le menu tient à l'écran) ; les autres onglets sont dans Outils > Autres pages
    for _, p in ipairs(Wn.PAGES) do
      if not p.hidden and p.id ~= "options" then
        local id = p.id
        out[#out + 1] = { group = "jeu", id = "tab:" .. id, title = p.label, icon = p.icon or ICON .. "INV_Misc_Note_01",
          tip = "Clic : ouvrir la page « " .. tostring(p.label) .. " »", fn = act(function() Wn.Open(id) end) }
      end
    end
  else
    for _, t in ipairs(T.GetTabs and T:GetTabs() or {}) do
      out[#out + 1] = { group = "jeu", id = "tab:" .. t.id, title = L[t.title or t.id], icon = TAB_ICON[t.id] or ICON .. "INV_Misc_Note_01",
        desc = TAB_DESC[t.id], tip = "Clic : ouvrir la page « " .. L[t.title or t.id] .. " »", fn = tab(t.id) }
    end
  end
  local function add(g, id, title, icon, tip, fn, ok)
    if ok == false then return end
    out[#out + 1] = { group = g, id = id, title = title, icon = icon, tip = tip, fn = fn, desc = ENTRY_DESC[id] }
  end
  for _, e in ipairs(type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}) do   -- (UIREDO) entrées « Jeu » des modules d'abord
    if type(e) == "table" and e.group == "jeu" and type(e.fn) == "function" and e.title and not (hasWin and e.id == "window") then
      add("jeu", e.id or e.title, e.title, e.icon or ICON .. "INV_Misc_Note_01", e.tip, act(e.fn))
    end
  end
  local F, Mc, Td = T.Finder, T.modules and T.modules.Macros, T.Todo
  -- (4.0 QA) Chercher est une fonction bêta : ouverte seulement quand elle tourne, sinon une ligne pour l'allumer
  local fOn = F and F._active and F.Show and true or false
  add("outils", "cherche", "Recherche", ICON .. "INV_Misc_Spyglass_02", "Clic : chercher autour de moi (familier, rares, maîtres, vols)",
    act(function() F.Show(true) end), fOn)
  add("outils", "pet", "Meilleur familier", ICON .. "Ability_Hunter_BeastTaming", "Clic : la meilleure bête à apprivoiser maintenant",
    act(function() F.lastQuery = "familier" F:Refresh(true) F.Show(true) end),
    (fOn and T.PlayerClass and T:PlayerClass() == "HUNTER") and true or false)
  if F and not fOn and F.db and T.SetModuleEnabled then
    out[#out + 1] = { group = "outils", id = "cherche:off", title = "Chercher (bêta, coupé)", icon = ICON .. "INV_Misc_Spyglass_02",
      desc = "Clic : l'allumer", tip = "Clic : allume « Chercher » (familier, rares, maîtres, vols près de toi), sans /reload",
      fn = act(function() T:SetModuleEnabled("Finder", true) end) }
  end
  add("outils", "macros", "Macros", ICON .. "INV_Misc_Note_01", "Clic : ouvrir les macros (hors combat)",
    act(function() if Mc and Mc.TogglePanel then Mc:TogglePanel() end end), (Mc and Mc.TogglePanel) and true or false)
  add("outils", "todo", "Fenêtre « À faire »", ICON .. "INV_Misc_Note_02", "Clic : la liste complète des quêtes (épingler, trier)",
    act(function() Td.Toggle() end), (Td and Td.Toggle) and true or false)
  -- (4.0 QA) une vraie bascule, sans aide dans le chat
  local Cm = T.modules and T.modules.Compass
  add("outils", "boussole", "Boussole", ICON .. "INV_Misc_Spyglass_02", "Clic : afficher / cacher la flèche",
    act(function() if Cm and Cm.Toggle then Cm.Toggle() end end), (Cm and Cm.Toggle and Cm._active) and true or false)
  local Rd = T.modules and T.modules.Radar
  add("outils", "radar", "Radar", ICON .. "INV_Misc_Map_01", "Clic : afficher / cacher le radar",
    act(function() Rd.Show(not (Rd.db and Rd.db.shown ~= false)) end), (Rd and Rd._active and Rd.Show) and true or false)
  -- entrées des modules (Dungeon…) : Outils d'abord puis Réglages, quel que soit l'ordre de chargement (un seul
  -- en-tête par groupe)
  for pass = 1, 2 do
    for _, e in ipairs(type(T.HUB_EXTRA) == "table" and T.HUB_EXTRA or {}) do
      if type(e) == "table" and type(e.fn) == "function" and e.title and e.group ~= "jeu"
          and ((e.group == "reglages") == (pass == 2)) then
        add(e.group or "outils", e.id or e.title, e.title, e.icon or ICON .. "INV_Misc_Note_01", e.tip, act(e.fn))
      end
    end
  end
  add("reglages", "options", "Options", ICON .. "INV_Misc_Gear_01", "Clic : toutes les options (taille, transparence, modules)",
    act(function()
      local Wn = T.Window
      if Wn and Wn._active and Wn.Open then Wn.Open("options") elseif T.OpenOptions then T:OpenOptions() end
    end))
  local nBeta, nOn = 0, 0
  for _, n in ipairs(T.moduleOrder or {}) do
    local m = T.modules[n]
    if m and T.IsBeta and T:IsBeta(n) and m._classOK ~= false then
      nBeta = nBeta + 1
      if m.db and m.db.enabled ~= false then nOn = nOn + 1 end
    end
  end
  out[#out + 1] = { group = "reglages", id = "profil", keep = true, icon = ICON .. "INV_Misc_EngGizmos_20",
    title = ("Fonctions bêta (%d/%d)"):format(nOn, nBeta),
    tip = "Clic : la liste des fonctions bêta (testées hors du jeu seulement), un clic pour chacune",
    fn = function() H.mode = "beta" end }
  -- (4.0 QA) Erreurs / Blocages : le résultat dans la fenêtre TreeAI (page Infos + pied de page), pas dans le chat
  add("reglages", "erreurs", "Erreurs", "Interface\\DialogFrame\\UI-Dialog-Icon-AlertOther", "Clic : préparer le fichier d'erreurs pour l'appli",
    act(function() H.Errors() end), hasSlash("erreurs"))
  add("reglages", "bloque", "Blocages", "Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew", "Clic : actions bloquées par le jeu (doit rester à 0)",
    act(function() H.Blocked() end), hasSlash("bloque"))
  add("reglages", "panel", "Afficher / cacher le panneau", ICON .. "INV_Misc_Note_06", "Clic : le panneau de TreeAI", act(function() T:TogglePanel() end))
  add("reglages", "edit", "Déplacer les cadres", ICON .. "INV_Misc_Wrench_01", "Clic : mode édition (glisser, molette = taille)",
    act(function() if T.SetEditMode then T:SetEditMode(not T:IsEditMode()) end end), T.SetEditMode and true or false)
  add("reglages", "reset", "Réinitialiser l'interface", "Interface\\Buttons\\UI-RefreshButton", "Clic : tout remettre à sa place automatique",
    act(function() if T.ResetInterface then T:ResetInterface() end end), T.ResetInterface and true or false)
  if T.Tour and T.Tour.HubEntry then out[#out + 1] = T.Tour.HubEntry() end   -- (ONBOARD) « Visite guidée »
  return out
end

-- (4.0 QA) Erreurs / Blocages : la réponse dans la fenêtre TreeAI (page « Infos » + pied de page), jamais dans le
-- chat ; sans la fenêtre, la commande d'avant
function H.Errors()
  local Wn, B = T.Window, T.Bugs
  if not (Wn and Wn._active and Wn.OpenTab and Wn.Flash) then T:HandleSlash("erreurs") return end
  local ok = B and type(B.Build) == "function" and pcall(B.Build, true)
  Wn.OpenTab("info")
  Wn.Flash(ok and "Fichier d'erreurs prêt : fais /reload, TreeAI l'écrit dans TreeAI-erreurs.txt."
    or "Erreurs : la liste est dans cette page.")
end
function H.Blocked()
  local Wn = T.Window
  if not (Wn and Wn._active and Wn.OpenTab and Wn.Flash) then T:HandleSlash("bloque") return end
  local total, sess = 0, 0
  if type(T.BlockedTop) == "function" then
    local ok, _, a, b = pcall(T.BlockedTop, T, 1)
    if ok then total, sess = tonumber(a) or 0, tonumber(b) or 0 end
  end
  Wn.OpenTab("info")
  Wn.Flash(total > 0 and fmt("Blocages : %d (%d cette session) : survole la ligne « Blocages » pour le détail.", total, sess)
    or "Blocages : aucune action bloquée par le jeu. Parfait.")
end

-- ------------------------------------------------------------------ menu (cadre à nous)
local menu
H.layout = {}
local TH = T.Theme
local SP = (TH and TH.SPACE) or { xs = 2, s = 4, m = 8, l = 12, xl = 16 }
local RW = (TH and TH.ROW) or { h = 20, h2 = 32, head = 18, icon = 14, iconL = 18 }
local ROW, ROW2, HEAD, W = RW.h, RW.h2, RW.head, 260
local MAXH = 560   -- au-delà (ou plus haut que l'écran − 120), le groupe suivant passe sur une autre colonne
local function closeMenu() H.mode = "main" if menu and menu:IsShown() then menu:Hide() end end
H.Close = closeMenu
local function buildMenu()
  if menu then return menu end
  local tpl = G("BackdropTemplateMixin") and "BackdropTemplate" or nil
  menu = G("CreateFrame")("Frame", "TreeAIHubMenu", G("UIParent"), tpl)
  menu:SetFrameStrata("DIALOG")
  menu:SetClampedToScreen(true)
  menu:EnableMouse(true)
  if menu.SetBackdrop then
    menu:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      edgeSize = 10, insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    menu:SetBackdropColor(0.05, 0.06, 0.08, 0.96)
    menu:SetBackdropBorderColor(0.78, 0.62, 0.28, 0.7)
  end
  if TH and TH.Skin then pcall(TH.Skin, TH, menu, "window") end
  menu.rows, menu.heads, menu.seps = {}, {}, {}
  if menu.RegisterEvent and pcall(menu.RegisterEvent, menu, "GLOBAL_MOUSE_DOWN") then
    menu:SetScript("OnEvent", function(self)
      if not self:IsShown() then return end
      local over = (self.IsMouseOver and self:IsMouseOver()) or (button and button.IsMouseOver and button:IsMouseOver())
      if not over then closeMenu() end
    end)
  end
  local list = G("UISpecialFrames")
  if type(list) == "table" then list[#list + 1] = "TreeAIHubMenu" end
  -- (4.0 QA) fermé par Échap (UISpecialFrames) : le prochain menu repart du menu principal
  menu:SetScript("OnHide", function() H.mode = "main" end)
  menu:Hide()
  return menu
end
local function row(i)
  local b = menu.rows[i]
  if b then return b end
  b = G("CreateFrame")("Button", nil, menu)
  b:SetHeight(ROW)
  b:RegisterForClicks("LeftButtonUp")
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetSize(RW.iconL, RW.iconL)
  b.icon:SetPoint("LEFT", b, "LEFT", SP.m, 0)
  if b.icon.SetTexCoord then b.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92) end
  b.tag = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.tag:SetPoint("TOPRIGHT", b, "TOPRIGHT", -SP.m, -SP.xs - 1)
  b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  b.text:SetPoint("TOPLEFT", b.icon, "TOPRIGHT", SP.m, 1)
  b.text:SetPoint("RIGHT", b, "RIGHT", -SP.m - 34, 0)
  b.text:SetJustifyH("LEFT")
  if b.text.SetWordWrap then b.text:SetWordWrap(false) end
  b.desc = b:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  b.desc:SetPoint("TOPLEFT", b.text, "BOTTOMLEFT", 0, -1)
  b.desc:SetPoint("RIGHT", b, "RIGHT", -SP.m, 0)
  b.desc:SetJustifyH("LEFT")
  if b.desc.SetWordWrap then b.desc:SetWordWrap(false) end
  if TH and TH.Font then TH:Font(b.text, "M") TH:Font(b.desc, "S", "muted") TH:Font(b.tag, "S") end
  b.hl = b:CreateTexture(nil, "HIGHLIGHT")
  b.hl:SetAllPoints(b)
  b.hl:SetTexture("Interface\\Buttons\\WHITE8X8")
  if b.hl.SetVertexColor then b.hl:SetVertexColor(1, 1, 1, 0.10) end
  b:SetScript("OnClick", function(self)
    local e = self.e
    if e and e.keep then
      if e.fn then e.fn() end
      if menu and menu:IsShown() then H.Fill() end
      return
    end
    closeMenu()
    if e and e.fn then e.fn() end
  end)
  b:SetScript("OnEnter", function(self)
    local tt, e = G("GameTooltip"), self.e
    if not (tt and e) then return end
    tt:SetOwner(self, "ANCHOR_RIGHT")
    tt:AddLine(e.tip or e.title, 0.4, 0.8, 1, true)
    tt:Show()
  end)
  b:SetScript("OnLeave", hideTip)
  menu.rows[i] = b
  return b
end
function H.Fill()
  buildMenu()
  local lay = H.layout
  for i = #lay, 1, -1 do lay[i] = nil end
  for _, r in ipairs(menu.rows) do r:Hide(); r.e = nil end
  for _, h in ipairs(menu.heads) do h:Hide(); h.icon:Hide() end
  for _, s in ipairs(menu.seps) do s:Hide() end
  -- (4.0 QA) le menu tient à l'écran quelle que soit l'échelle de l'interface : un groupe qui ne tient plus dans
  -- la colonne passe entier dans la suivante (un groupe plus haut que l'écran continue sur la colonne d'à côté)
  local up = G("UIParent")
  local ph = up and tonumber((try(up.GetHeight, up))) or 0
  if ph < 200 then ph = tonumber((try(G("GetScreenHeight")))) or 768 end   -- (même repli que le placement auto)
  local maxH = min(MAXH, max(240, ph - 120))
  local groupOf = {}
  for _, g in ipairs(H.GROUPS) do groupOf[g.id] = g end
  local entries = H.Entries()
  local gh = {}   -- hauteur de chaque groupe (titre + lignes)
  for _, e in ipairs(entries) do gh[e.group] = (gh[e.group] or (groupOf[e.group] and HEAD or 0)) + (e.desc and ROW2 or ROW) end
  local y, n, nh, ns, last = -5, 0, 0, 0, nil
  local colX, colRows, minY = 0, 0, -5
  local function sep()
    ns = ns + 1
    local s = menu.seps[ns]
    if not s then
      s = menu:CreateTexture(nil, "ARTWORK")
      s:SetTexture("Interface\\Buttons\\WHITE8X8")
      if s.SetVertexColor then s:SetVertexColor(0.78, 0.62, 0.28, 0.35) end
      s:SetHeight(1)
      menu.seps[ns] = s
    end
    s:ClearAllPoints()
    s:SetPoint("TOPLEFT", menu, "TOPLEFT", colX + 8, y - 3)
    s:SetWidth(W - 16)
    s:Show()
    lay[#lay + 1] = { kind = "sep", y = y - 3, x = colX }
    y = y - 7
  end
  local function header(g)
    nh = nh + 1
    local h = menu.heads[nh]
    if not h then
      h = menu:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
      h:SetJustifyH("LEFT")
      h.icon = menu:CreateTexture(nil, "ARTWORK")
      h.icon:SetSize(12, 12)
      menu.heads[nh] = h
    end
    h.icon:ClearAllPoints()
    h.icon:SetPoint("TOPLEFT", menu, "TOPLEFT", colX + 8, y - 2)
    h.icon:SetTexture(g.icon)
    h.icon:Show()
    h:ClearAllPoints()
    h:SetPoint("TOPLEFT", menu, "TOPLEFT", colX + 24, y - 2)
    h:SetText(L[g.title])
    h:SetTextColor(0.78, 0.62, 0.28)
    h:Show()
    lay[#lay + 1] = { kind = "header", group = g.id, y = y, x = colX }
    y = y - HEAD
  end
  for _, e in ipairs(entries) do
    local h = e.desc and ROW2 or ROW
    if e.group ~= last then
      if colRows > 0 and y - 7 - (gh[e.group] or h) < -maxH then colX, y, colRows = colX + W, -5, 0 end
      if colRows > 0 then sep() end
      last = e.group
      if groupOf[e.group] then header(groupOf[e.group]) end
    elseif colRows > 0 and y - h < -maxH then   -- groupe plus haut que l'écran : la suite à côté, alignée
      colX, y, colRows = colX + W, -5 - HEAD, 0
    end
    n = n + 1
    local b = row(n)
    b.e = e
    -- (même convention que « Plus → Modules » : l'orage de clics du harnais laisse ces bascules tranquilles)
    b.entry = { id = "mod:hub:" .. e.id }
    b:ClearAllPoints()
    b:SetHeight(h)
    b:SetPoint("TOPLEFT", menu, "TOPLEFT", colX + SP.xs, y)
    b:SetWidth(W - 2 * SP.xs)
    b.icon:SetTexture(e.icon)
    b.icon:SetSize(e.desc and RW.iconL or RW.icon, e.desc and RW.iconL or RW.icon)
    if e.on == false and b.icon.SetDesaturated then b.icon:SetDesaturated(true)
    elseif b.icon.SetDesaturated then b.icon:SetDesaturated(false) end
    b.text:SetText(e.title)
    b.text:SetTextColor(1, 0.82, 0.2)
    if e.group ~= "jeu" then b.text:SetTextColor(0.92, 0.92, 0.92) end
    b.desc:SetText(e.desc or "")
    b.tag:SetText(e.state or (e.beta and ((TH and TH.BetaTag and TH:BetaTag()) or "|cffb48cffbêta|r")) or "")
    b:Show()
    lay[#lay + 1] = { kind = "item", id = e.id, group = e.group, y = y, x = colX, text = e.title, desc = e.desc, beta = e.beta,
      textW = b.text:GetStringWidth() or 0, textMax = W - 2 * SP.xs - SP.m - RW.iconL - SP.m - SP.m - 34, button = b }
    y = y - h
    colRows = colRows + 1
    minY = min(minY, y)
  end
  menu:SetSize(colX + W, -minY + 5)
  menu:ClearAllPoints()
  if button then menu:SetPoint("TOPRIGHT", button, "BOTTOMLEFT", 8, 4)
  else menu:SetPoint("TOPRIGHT", G("UIParent"), "TOPRIGHT", -200, -200) end
  return lay
end
function H.Toggle(anchor)
  buildMenu()
  if menu:IsShown() then closeMenu() return false end
  H.Fill()
  if anchor and anchor ~= button then menu:ClearAllPoints() menu:SetPoint("TOPRIGHT", anchor, "BOTTOMLEFT", 8, 4) end
  menu:Show()
  return true
end
function H.IsOpen() return menu and menu:IsShown() or false end

-- ------------------------------------------------------------------ pastille
local function onDragUpdate()
  local mm = minimap()
  local cur = G("GetCursorPosition")
  if not (mm and cur) then return end
  local px, py = cur()
  local sc = mm:GetEffectiveScale() or 1
  local l, b, w, h = mm:GetLeft(), mm:GetBottom(), mm:GetWidth(), mm:GetHeight()
  if not (l and b) then return end
  H.db.angle = floor(H.AngleOf(l + w / 2, b + h / 2, px / sc, py / sc) + 0.5)
  H.Place()
end
function H.CreateButton()
  if button then return button end
  local mm = minimap()
  if not (mm and G("CreateFrame")) then return nil end
  button = G("CreateFrame")("Button", "TreeAIMinimapButton", mm)
  button:SetSize(31, 31)
  button:SetFrameStrata("MEDIUM")
  if button.SetFrameLevel and mm.GetFrameLevel then button:SetFrameLevel((mm:GetFrameLevel() or 1) + 8) end
  button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  button:RegisterForDrag("LeftButton")
  local bg = button:CreateTexture(nil, "BACKGROUND")
  bg:SetSize(20, 20)
  bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
  bg:SetPoint("CENTER", button, "CENTER", 0, 0)
  local icon = button:CreateTexture(nil, "ARTWORK")
  icon:SetSize(18, 18)
  icon:SetTexture(H.ICON)
  icon:SetPoint("CENTER", button, "CENTER", 0, 1)
  if icon.SetTexCoord then icon:SetTexCoord(0.07, 0.93, 0.07, 0.93) end
  button.icon = icon
  local border = button:CreateTexture(nil, "OVERLAY")
  border:SetSize(53, 53)
  border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
  border:SetPoint("TOPLEFT", button, "TOPLEFT", 0, 0)
  if button.SetHighlightTexture then pcall(button.SetHighlightTexture, button, "Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight") end
  button:SetScript("OnClick", function(self, btn)
    hideTip()
    -- (UIREDO) clic gauche : la fenêtre TreeAI ; clic droit : le menu rapide (sans fenêtre : menu / options)
    local Wn = T.Window
    local hasWin = Wn and Wn.Toggle and Wn._active
    if btn == "RightButton" then
      if hasWin then H.Toggle() else
        closeMenu()
        T:UserAction(function() if T.OpenOptions then T:OpenOptions() end end)
      end
    elseif hasWin then
      closeMenu()
      T:UserAction(function() Wn.Toggle() end)
    else
      H.Toggle()
    end
  end)
  button:SetScript("OnEnter", function(self) if not H.IsOpen() then showTip(self) end end)
  button:SetScript("OnLeave", hideTip)
  button:SetScript("OnDragStart", function(self)
    closeMenu()
    self:SetScript("OnUpdate", onDragUpdate)
  end)
  button:SetScript("OnDragStop", function(self)
    self:SetScript("OnUpdate", nil)
    H.Place()
  end)
  H.Place()
  return button
end
function H.Button() return button end

local compartment = false
local function registerCompartment()
  if compartment or not cfg("compartment") then return end
  local acf = G("AddonCompartmentFrame")
  if type(acf) == "table" and type(acf.RegisterAddon) == "function" then
    compartment = pcall(acf.RegisterAddon, acf, {
      text = "TreeAI", icon = H.ICON, notCheckable = true, registerForAnyClick = true,
      func = function(_, input, owner)
        local btn = type(input) == "table" and input.buttonName or input
        if btn == "RightButton" then T:UserAction(function() if T.OpenOptions then T:OpenOptions() end end)
        else H.Toggle(type(owner) == "table" and owner.GetObjectType and owner or nil) end
      end,
      funcOnEnter = function(f) if type(f) == "table" and f.GetObjectType then showTip(f) end end,
      funcOnLeave = hideTip,
    }) and true or false
  end
end

function H:OnEnable()
  if cfg("button") then
    H.CreateButton()
    if button then button:Show() end
  elseif button then button:Hide() end
  registerCompartment()
end
function H:OnDisable()
  if button then button:Hide() end
  closeMenu()
end
function H:OnOptionChanged()
  if cfg("button") then
    H.CreateButton()
    if button then button:Show(); H.Place() end
  elseif button then button:Hide() end
  registerCompartment()
end
T:RegisterSlash("menu", function() H.Toggle() end, "menu de TreeAI (comme la pastille de la minimap)")
