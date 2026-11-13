-- TreeAI — module Addons « Autres addons » (5.1, agent LOOK) : TreeAI se BRANCHE sur les addons du joueur au lieu de
-- les doubler. Lecture seule à l'exécution : aucun cadre d'un autre addon n'est déplacé, caché, re-parenté ni ancré ;
-- aucune de leurs options n'est écrite ; chaque lecture passe par rawget / pcall ; rien de leur code ni de leurs
-- données n'est repris (idées seulement : research/addons_study.md, research/look6.md).
--  * Shortest Path Forever (colonne « Suivi Forever », flèche du jeu) : quand il est là, TreeAI ne montre NI boussole
--    NI 2e liste de trajet ; « Y aller » lui confie la destination (son API publique « Navigate », au clic du joueur),
--    sinon le repère du jeu (que SPF et la carte suivent). TreeAI n'ajoute que ce que SPF n'a pas : quelle quête faire
--    ensuite et pourquoi, le groupe, l'équipement, les sacs, les métiers. Le petit bloc « Maintenant » peut se poser
--    contre sa colonne (même largeur, même style) — posé sur UIParent à la place LUE, jamais ancré à son cadre.
--  * Questie : sa base est lue ; ses icônes et son suivi ne sont pas touchés.
--  * Adventure Guide Forever / SkillUp Forever : leurs listes (étapes de montée, composants de métier) ne sont pas
--    répétées ; TreeAI garde ses avis et ses actions en un clic.
--  * Auctionator : ses prix sont lus par son API publique (module HV).
--  * Leatrix Plus : s'il vend déjà les gris / répare tout seul, la carte du marchand le dit et ne le propose pas.
--  * OpenMap, Tweaks Forever, compteurs, cadres d'unité : seulement évités au placement.
-- Une page « Autres addons » (Réglages) : chaque addon trouvé, ce que TreeAI lui prend, ce qu'il lui laisse, chaque
-- ligne se coupe d'un clic. A.Probe() : ce qui a été trouvé sur CE client (noms, versions, points d'entrée) pour le
-- rapport d'erreurs. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G

local A = T:RegisterModule("Addons", {
  title = "Autres addons",
  description = "TreeAI se branche sur tes autres addons (Suivi Forever, Questie, Auctionator, Leatrix Plus…) au lieu de les doubler.",
  defaults = { enabled = true, noticeV = 0, links = {} },
  charDefaults = {},
})
T.Addons = A
A.autoIdleSafe = true
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
local function meth(o, m, ...) local f = type(o) == "table" and o[m] if type(f) == "function" then return pass(pcall(f, o, ...)) end end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local ICON = "Interface\\Icons\\"
A.NOTICE_V = 2

-- ===================================================================== le registre : addons connus, liens, points d'entrée
-- test = motif sur le nom de dossier (minuscules) ; globals = globales publiques qui prouvent qu'il est chargé ;
-- entries = points d'entrée sondés (chemins « A.b.c ») ; links = ce que TreeAI lui PREND (take) ou lui LAISSE (leave),
-- chaque ligne se coupe (A.SetLink) ; layout = seulement évité au placement.
A.KNOWN = {
  { key = "spf", title = "Shortest Path Forever", icon = ICON .. "INV_Misc_Map_01",
    test = function(l) return l:find("shortestpath", 1, true) ~= nil end, globals = { "ShortestPathForever" },
    entries = { "ShortestPathForever.API.Navigate", "ShortestPathForever.API.NavigateRoute", "ShortestPathForever.API.Estimate",
      "ShortestPathForever.API.EstimateDetail", "ShortestPathForever.API.Active", "ShortestPathForever.API.CurrentStop",
      "ShortestPathForever.API.Subscribe", "ShortestPathForever.API.Ended", "ShortestPathForeverDB", "ForeverMapMarkers" },
    links = {
      { id = "arrow", kind = "leave", label = "Flèche et trajet pas à pas",
        on = "Laissés à Suivi Forever : TreeAI ne montre ni boussole ni 2e liste de trajet ; « Y aller » lui donne la destination.",
        off = "TreeAI montre sa propre boussole et son trajet (deux flèches possibles)." },
      { id = "dock", kind = "take", label = "« Maintenant » posé contre sa colonne",
        on = "Le petit bloc « Maintenant » de TreeAI se pose contre la colonne Suivi Forever (même largeur), sans y toucher.",
        off = "Pas de bloc « Maintenant » à l'écran : tout reste dans la fenêtre TreeAI." },
    } },
  { key = "questie", title = "Questie", icon = ICON .. "INV_Misc_Book_09",
    test = function(l) return l == "questie" or l == "questiedb" end, globals = { "Questie", "QuestieLoader", "LibQuestieDB" },
    entries = { "QuestieLoader.ImportModule", "LibQuestieDB.Quest.Get", "LibQuestieDB.Npc.Get", "Questie.db.profile", "Questie_BaseFrame" },
    links = {
      { id = "data", kind = "take", label = "Base de quêtes",
        on = "Lue par TreeAI (donneurs, objectifs, chaînes) pour dire quelle quête faire ensuite. Rien n'y est écrit.",
        off = "TreeAI n'utilise que les points du jeu (quêtes de ton journal)." },
      { id = "icons", kind = "leave", label = "Icônes et suivi de quêtes",
        on = "Laissés à Questie et au jeu : TreeAI ne dessine pas ses propres icônes ni un 2e suivi.",
        off = "TreeAI peut dessiner ses icônes / son suivi si tu les allumes (Réglages → Affichage)." },
    } },
  { key = "agf", title = "Adventure Guide Forever", icon = ICON .. "INV_Misc_Book_08",
    test = function(l) return l:find("adventureguide", 1, true) ~= nil end, globals = { "AdventureGuideForever", "AdventureGuide" },
    entries = { "AdventureGuideForever.API", "AdventureGuideForeverDB", "AdventureGuide.API" },
    links = {
      { id = "guide", kind = "leave", label = "Guide de montée (étapes, chapitres)",
        on = "Laissé à Adventure Guide : TreeAI ne répète pas sa liste d'étapes ; il garde ses avis (rendre, groupe, équipement).",
        off = "TreeAI montre aussi sa propre liste d'étapes (page Quêtes)." },
    } },
  { key = "skillup", title = "SkillUp Forever", icon = ICON .. "Trade_BlackSmithing",
    test = function(l) return l:find("skillup", 1, true) ~= nil end, globals = { "SkillUpForever", "SkillUp" },
    entries = { "SkillUpForever.API", "SkillUpForeverDB", "SkillUp.API" },
    links = {
      { id = "prof", kind = "leave", label = "Liste de montée de métier et composants",
        on = "Laissées à SkillUp : TreeAI ne répète pas la liste ; il garde « où est le maître », « quoi vendre », « quoi garder ».",
        off = "TreeAI montre aussi sa propre liste de composants (page Métiers, chez le marchand)." },
    } },
  { key = "auctionator", title = "Auctionator", icon = ICON .. "INV_Misc_Coin_02",
    test = function(l) return l:find("auctionator", 1, true) ~= nil end, globals = { "Auctionator" },
    entries = { "Auctionator.API.v1.GetAuctionPriceByItemID", "Auctionator.API.v1.GetAuctionPriceByItemLink",
      "Auctionator.API.v1.MultiSearch", "Auctionator.API.v1.MultiSearchExact", "Auctionator.API.v1.CreateShoppingList" },
    links = {
      { id = "prices", kind = "take", label = "Prix de l'hôtel des ventes",
        on = "Lus chez Auctionator (son API publique) pour les avis « vendre / garder / HV ». Rien n'y est écrit.",
        off = "TreeAI n'utilise que ses propres relevés de prix." },
    } },
  { key = "leatrix", title = "Leatrix Plus", icon = ICON .. "INV_Misc_Gear_01",
    test = function(l) return l:find("leatrix", 1, true) ~= nil and l:find("plus", 1, true) ~= nil end, globals = { "LeaPlusDB" },
    entries = { "LeaPlusDB.AutoSellJunk", "LeaPlusDB.AutoRepairGear", "LeaPlusDB.FasterLooting" },
    links = {
      { id = "merchant", kind = "leave", label = "Vendre les gris et réparer tout seul",
        on = "Si ces options de Leatrix sont cochées, TreeAI le dit chez le marchand et ne propose pas la même chose.",
        off = "La carte du marchand de TreeAI propose toujours « vendre + réparer »." },
    } },
  { key = "openmap", title = "OpenMap", icon = ICON .. "INV_Misc_Map02", layout = true,
    test = function(l) return l:find("openmap", 1, true) ~= nil end, globals = { "OpenMap" }, entries = { "OpenMap" } },
  { key = "tweaks", title = "Tweaks Forever", icon = ICON .. "INV_Misc_Wrench_01", layout = true,
    test = function(l) return l:find("tweaks", 1, true) ~= nil end, globals = { "TweaksForever" }, entries = { "TweaksForever" } },
  { key = "details", title = "Compteur de dégâts", icon = ICON .. "Ability_Hunter_SniperShot", layout = true,
    test = function(l) return l == "details" or l:find("^details_") ~= nil or l == "recount" or l == "skada" end,
    globals = { "Details", "Recount", "Skada" }, entries = { "Details", "Recount", "Skada", "C_DamageMeter" } },
  { key = "journal", title = "DungeonJournal", icon = ICON .. "INV_Misc_Key_03", layout = true,
    test = function(l) return l:find("dungeon", 1, true) ~= nil and l:find("journal", 1, true) ~= nil end, globals = {}, entries = {} },
  { key = "sparkles", title = "Forever Loot Sparkles", icon = ICON .. "INV_Misc_Gem_Pearl_04", layout = true,
    test = function(l) return l:find("sparkle", 1, true) ~= nil end, globals = {}, entries = {} },
}
local BY = {}
for _, k in ipairs(A.KNOWN) do BY[k.key] = k end

local function loadedAddon(name)
  local CA = g("C_AddOns")
  if type(CA) == "table" and type(CA.IsAddOnLoaded) == "function" then
    local r = try(CA.IsAddOnLoaded, name)
    if r ~= nil then return r and true or false end
  end
  return try(g("IsAddOnLoaded"), name) and true or false
end
local function versionOf(name)
  local CA = g("C_AddOns")
  local v = (type(CA) == "table" and type(CA.GetAddOnMetadata) == "function" and try(CA.GetAddOnMetadata, name, "Version"))
    or try(g("GetAddOnMetadata"), name, "Version")
  if type(v) ~= "string" or secret(v) or v == "" then return nil end
  return v:sub(1, 24)
end
--- valeur au bout d'un chemin « A.b.c » (rawget à chaque pas, jamais une métaméthode d'un autre addon)
local function at(path)
  local cur = G
  for part in tostring(path):gmatch("[^%.]+") do
    if type(cur) ~= "table" then return nil end
    local ok, v = pcall(rawget, cur, part)
    if not ok then return nil end
    cur = v
  end
  return cur
end
A.At = at

--- { [clé] = { name, title, loaded, version } } des addons connus trouvés dans la liste du jeu (mémo 30 s)
local seenAt, seen = -100, {}
function A.Installed(force)
  local t = tonumber(try(g("GetTime"))) or 0
  if not force and t - seenAt < 30 then return seen end
  seenAt, seen = t, {}
  local CA = g("C_AddOns")
  local n = (type(CA) == "table" and try(CA.GetNumAddOns)) or try(g("GetNumAddOns")) or 0
  for i = 1, math.min(tonumber(n) or 0, 400) do
    local name, title
    if type(CA) == "table" and type(CA.GetAddOnInfo) == "function" then name, title = try(CA.GetAddOnInfo, i)
    else name, title = try(g("GetAddOnInfo"), i) end
    if type(name) == "string" and not secret(name) then
      local l = name:lower()
      for _, k in ipairs(A.KNOWN) do
        if not seen[k.key] and k.test(l) then
          seen[k.key] = { name = name, title = (type(title) == "string" and not secret(title)) and title or name, loaded = loadedAddon(name),
            version = versionOf(name) }
        end
      end
    end
  end
  -- chargés sans être listés (noms de dossier différents, tests) : leurs globales publiques
  for _, k in ipairs(A.KNOWN) do
    if not seen[k.key] then
      for _, gn in ipairs(k.globals or {}) do
        if type(g(gn)) == "table" then seen[k.key] = { name = gn, title = k.title, loaded = true } break end
      end
    end
  end
  if seen.questie and not seen.questie.loaded and (type(g("Questie")) == "table" or type(g("LibQuestieDB")) == "table") then seen.questie.loaded = true end
  return seen
end
--- l'addon `key` est-il chargé ?
function A.Has(key)
  local e = A.Installed()[key]
  return (e and e.loaded) and true or false
end

-- ===================================================================== liens : ce que TreeAI prend / laisse, coupable
local function linkDef(key, id)
  local k = BY[key]
  for _, l in ipairs(k and k.links or {}) do if l.id == id then return l end end
  return nil
end
--- le réglage du joueur pour ce lien (vrai = branché ; d'office : branché)
function A.Wanted(key, id)
  local s = A.db and type(A.db.links) == "table" and A.db.links[key .. ":" .. id]
  return s ~= false
end
--- le lien est-il ACTIF maintenant : l'addon est chargé, le lien existe, le joueur ne l'a pas coupé
function A.Link(key, id)
  if not (A._active and linkDef(key, id)) then return false end
  return A.Has(key) and A.Wanted(key, id)
end
A.Defers = A.Link
function A.SetLink(key, id, on)
  if not (A.db and linkDef(key, id)) then return false end
  if type(A.db.links) ~= "table" then A.db.links = {} end
  A.db.links[key .. ":" .. id] = (on == false) and false or nil
  pcall(A.Apply)
  if T.Fire then pcall(T.Fire, T, "ADDON_LINKS", key, id, on ~= false) end
  if T.MarkDirty then T:MarkDirty() end
  if T.RequestRefresh then T:RequestRefresh() end
  return true
end

-- ===================================================================== Questie (lecture seule)
local function questieProfile()
  local Q = g("Questie")
  if type(Q) ~= "table" then return nil end
  local db = rawget(Q, "db")
  local p = type(db) == "table" and rawget(db, "profile") or nil
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
--- { loaded, readable, icons, tracker, button, db } — ce que Questie dessine, lu dans ses options (jamais écrit)
function A.QuestieState()
  local p = questieProfile()
  local loaded = type(g("Questie")) == "table" or type(g("QuestieLoader")) == "table" or loadedAddon("Questie")
  local st = { loaded = loaded and true or false, readable = p ~= nil, db = A.QuestDB() }
  if p then
    st.icons = rawget(p, "enabled") ~= false and (rawget(p, "enableMapIcons") ~= false or rawget(p, "enableMiniMapIcons") ~= false)
    st.tracker = rawget(p, "trackerEnabled") ~= false
    local mm = rawget(p, "minimap")
    st.button = not (type(mm) == "table" and rawget(mm, "hide") == true)
  elseif st.loaded then
    st.icons, st.tracker, st.button = true, true, true    -- illisible : on suppose tout visible (jamais en double)
  end
  st.silent = st.loaded and st.readable and not st.icons and not st.tracker or false
  return st
end

-- ===================================================================== Shortest Path Forever (lecture seule)
local SPF_API = { "Navigate", "NavigateRoute", "Estimate", "EstimateDetail", "Active", "CurrentStop", "Subscribe", "Ended" }
--- { loaded, api = { Navigate = true, … }, canNavigate, tracker } — son API publique telle qu'elle répond ICI
function A.SPFState()
  local inst = A.Installed()
  local S = g("ShortestPathForever")
  local api = type(S) == "table" and rawget(S, "API") or nil
  local st = { loaded = (inst.spf and inst.spf.loaded) or type(S) == "table" or false, api = {} }
  if type(api) == "table" then
    for _, n in ipairs(SPF_API) do if type(rawget(api, n)) == "function" then st.api[n] = true end end
  end
  st.canNavigate = st.api.Navigate == true
  local d = g("ShortestPathForeverDB")
  st.readable = type(d) == "table"
  if st.readable then st.tracker = rawget(d, "tracker") ~= false or rawget(d, "journeyTracker") ~= false else st.tracker = st.loaded end
  return st
end
--- (compat 4.1) TreeAI et Suivi Forever n'affichent plus deux trajets : plus de conflit à arbitrer
function A.SPFConflict() return false end
--- la colonne « Suivi Forever » (SPF, Adventure Guide, SkillUp la partagent) : cadre nommé connu, sinon nil.
--- Lecture seule : son rectangle en unités d'UIParent { l, b, w, h, name } — jamais ancré, jamais déplacé.
A.COLUMN_NAMES = { "ShortestPathForeverTrackerFrame", "ShortestPathForeverTracker", "ShortestPathForever_Tracker",
  "ShortestPathForeverTrackerBaseFrame", "SPFTrackerFrame", "SPF_TrackerFrame", "SPF_Tracker", "ShortestPathTrackerFrame",
  "ForeverTrackerHost", "ForeverTrackerHostFrame", "ForeverTrackerFrame", "ForeverTracker", "TrackerHostFrame" }
local function finite(v) return type(v) == "number" and v == v and v > -1e6 and v < 1e6 end
function A.Column()
  if not (A.Has("spf") or A.Has("agf") or A.Has("skillup")) then return nil end
  local ue = tonumber(meth(G.UIParent, "GetEffectiveScale")) or 1
  for _, n in ipairs(A.COLUMN_NAMES) do
    local f = g(n)
    if type(f) == "table" and not meth(f, "IsForbidden") and meth(f, "IsVisible") then
      local l, b, w, h = meth(f, "GetRect")
      if finite(l) and finite(b) and finite(w) and finite(h) and w >= 60 and h >= 20 and not secret(l) then
        local k = (tonumber(meth(f, "GetEffectiveScale")) or ue) / ue
        if finite(k) and k > 0 then return { l = l * k, b = b * k, w = w * k, h = h * k, name = n } end
      end
    end
  end
  return nil
end

-- ===================================================================== Leatrix Plus (lecture seule de ses réglages)
--- { loaded, sell = bool, repair = bool } : « vendre les gris » / « réparer » automatiques cochés chez lui ?
function A.Leatrix()
  local d = g("LeaPlusDB")
  local out = { loaded = A.Has("leatrix") or type(d) == "table" }
  if type(d) == "table" then
    out.sell = rawget(d, "AutoSellJunk") == "On"
    out.repair = rawget(d, "AutoRepairGear") == "On"
  end
  return out
end
--- chez le marchand : ce que Leatrix fait déjà tout seul (le lien « merchant » branché) → { sell, repair, note } ou nil
function A.MerchantAuto()
  if not A.Link("leatrix", "merchant") then return nil end
  local l = A.Leatrix()
  if not (l.sell or l.repair) then return nil end
  local what = (l.sell and l.repair and "vend les objets gris et répare") or (l.sell and "vend les objets gris") or "répare"
  return { sell = l.sell or false, repair = l.repair or false, note = "Leatrix Plus " .. what .. " déjà tout seul : TreeAI ne le propose pas." }
end

-- ===================================================================== « Y aller » : UN connecteur pour toutes les lignes
-- Au clic du joueur seulement. Avec Suivi Forever branché : sa navigation (API publique), sinon le repère du jeu — que
-- SPF et la carte suivent —, sinon la boussole TreeAI. Renvoie "spf" | "tomtom" | "blizzard" | "compass" | nil.
function A.GoTo(t)
  local C = T.Compass
  if type(t) ~= "table" or not (C and t.mapID and t.x and t.y) then return nil end
  local x, y = tonumber(t.x), tonumber(t.y)
  if not (x and y) then return nil end
  if x > 1 or y > 1 then x, y = x / 100, y / 100 end
  local tg = { mapID = t.mapID, x = x, y = y, label = t.label or t.title, title = t.title or t.label, key = t.key, questID = t.questID,
    source = t.source or "now" }
  local how
  if type(C.GoTo) == "function" then how = try(C.GoTo, tg) end
  if not how and type(C.SetTarget) == "function" then
    if try(C.SetTarget, tg) ~= nil then how = "compass" end
  end
  A.lastGo = { how = how, mapID = tg.mapID, x = x, y = y }
  return how
end
--- ce que « Y aller » fera (texte de l'infobulle) : « Suivi Forever t'y mène » / « repère sur la carte »
function A.GoHow()
  if A.Link("spf", "arrow") then
    return A.SPFState().canNavigate and "Suivi Forever t'y mène" or "le repère du jeu t'y mène (Suivi Forever et la carte le suivent)"
  end
  return "la boussole TreeAI t'y mène"
end

-- ===================================================================== appliquer les liens (au démarrage, à chaque bascule)
-- Rien n'est touché chez les autres : seuls NOS réglages changent, et l'état d'avant est gardé pour être rendu quand
-- le lien est coupé ou l'addon désinstallé.
function A.Apply()
  if not (A.db and A._active) then return end
  local was = type(A.db.was) == "table" and A.db.was or {}
  A.db.was = was
  -- Suivi Forever guide : notre guidage passe en « auto » (la boussole s'efface quand il guide, « Y aller » lui parle)
  -- et notre boussole n'est plus affichée d'office
  local arrow = A.Link("spf", "arrow")
  local C = T.modules and T.modules.Compass
  if arrow then
    -- une seule fois, au moment où le lien devient actif : ensuite le joueur reste maître de ses réglages (s'il
    -- rallume la boussole dans Affichage, elle reste allumée)
    if was.guide == nil and T.GuideMode then
      was.guide = T:GuideMode()
      if T.SetGuideMode and was.guide ~= "auto" then T:SetGuideMode("auto") end
    end
    if C and C.db and was.compass == nil then
      was.compass = C.db.shown ~= false
      if C.db.shown ~= false then
        C.db.shown = false
        if C.OnOptionChanged then pcall(C.OnOptionChanged, C, "shown") end
      end
    end
  else
    if was.guide ~= nil then
      if T.SetGuideMode and T.GuideMode and T:GuideMode() ~= was.guide then T:SetGuideMode(was.guide) end
      was.guide = nil
    end
    if was.compass ~= nil then
      if C and C.db and (C.db.shown ~= false) ~= was.compass then
        C.db.shown = was.compass
        if C.OnOptionChanged then pcall(C.OnOptionChanged, C, "shown") end
      end
      was.compass = nil
    end
  end
end

-- ===================================================================== sonde : ce qui est VRAIMENT là sur ce client
--- lignes pour le rapport d'erreurs (TreeAI-erreurs.txt) : chaque addon connu, sa version, ses points d'entrée
function A.Probe()
  local out = { "-- Autres addons (détection TreeAI)" }
  local inst = A.Installed(true)
  for _, k in ipairs(A.KNOWN) do
    local e = inst[k.key]
    if e then
      local ent = {}
      for _, p in ipairs(k.entries or {}) do
        local v = at(p)
        ent[#ent + 1] = p .. "=" .. ((v == nil and "absent") or type(v))
      end
      local links = {}
      for _, l in ipairs(k.links or {}) do links[#links + 1] = l.id .. "=" .. (A.Link(k.key, l.id) and "branché" or (A.Wanted(k.key, l.id) and "inactif" or "coupé")) end
      out[#out + 1] = fmt("%s : dossier « %s »%s, %s%s%s", k.title, tostring(e.name), e.version and (" v" .. e.version) or "",
        e.loaded and "chargé" or "installé, pas chargé", #links > 0 and (" ; liens " .. table.concat(links, ", ")) or "",
        #ent > 0 and (" ; " .. table.concat(ent, ", ")) or "")
    else
      out[#out + 1] = k.title .. " : absent"
    end
  end
  local col = A.Column()
  out[#out + 1] = col and fmt("Colonne Suivi Forever : cadre « %s » %d×%d en %d,%d", col.name, col.w, col.h, col.l, col.b)
    or "Colonne Suivi Forever : aucun cadre nommé connu trouvé (le bloc « Maintenant » ne s'y pose pas)"
  local lea = A.Leatrix()
  if lea.loaded then out[#out + 1] = fmt("Leatrix Plus : vendre les gris = %s, réparer = %s", tostring(lea.sell), tostring(lea.repair)) end
  local go = A.lastGo
  if go then out[#out + 1] = fmt("Dernier « Y aller » : %s (carte %s)", tostring(go.how), tostring(go.mapID)) end
  for _, n in ipairs({ "C_Map.SetUserWaypoint", "C_Map.ClearUserWaypoint", "C_SuperTrack.SetSuperTrackedUserWaypoint", "UiMapPoint.CreateFromCoordinates",
      "TomTom.AddWaypoint" }) do
    out[#out + 1] = n .. " : " .. (type(at(n)) == "function" and "présente" or "absente")
  end
  return out
end

-- ===================================================================== page « Autres addons »
--- sections de la page : { { key, title, icon, version, lines = { { text, label, kind, on, key, id } }, layout } }
function A.Sections()
  local inst = A.Installed()
  local out = {}
  for _, k in ipairs(A.KNOWN) do
    local e = inst[k.key]
    if e and e.loaded then
      local s = { key = k.key, title = k.title, icon = k.icon, version = e.version, layout = k.layout, lines = {} }
      if k.layout then
        s.lines[1] = { text = "Rien n'est lu ni pris : TreeAI évite seulement ses cadres quand il place les siens." }
      end
      for _, l in ipairs(k.links or {}) do
        local on = A.Wanted(k.key, l.id)
        s.lines[#s.lines + 1] = { label = l.label, kind = l.kind, text = on and l.on or l.off, on = on, key = k.key, id = l.id }
      end
      if k.key == "spf" then
        local st = A.SPFState()
        local col = A.Column()
        s.lines[#s.lines + 1] = { text = (st.canNavigate and "Son API publique répond : « Y aller » lui confie la destination."
          or "Son API de navigation ne répond pas ici : « Y aller » pose le repère du jeu (il le suit).")
          .. (col and "" or " Sa colonne n'a pas de cadre nommé connu : le bloc « Maintenant » reste dans la fenêtre.") }
      elseif k.key == "questie" then
        local q = A.QuestieState()
        s.lines[#s.lines + 1] = { text = q.db and ("Base lue : " .. q.db .. ".") or "Base illisible ici : TreeAI n'a que les points du jeu." }
      elseif k.key == "leatrix" then
        local l = A.Leatrix()
        s.lines[#s.lines + 1] = { text = fmt("Chez lui : vendre les gris %s, réparer %s.", l.sell and "coché" or "pas coché", l.repair and "coché" or "pas coché") }
      end
      out[#out + 1] = s
    end
  end
  return out
end
local function render(Ly)
  local W = T.Window
  local secs = A.Sections()
  Ly.text("TreeAI se branche sur tes addons au lieu de les doubler. Il ne touche à rien chez eux : il lit, et il se tait là où ils font déjà le travail.",
    "S", "muted")
  if #secs == 0 then
    Ly.empty("Aucun addon connu n'est chargé : TreeAI fait tout lui-même.")
    return
  end
  for _, s in ipairs(secs) do
    if Ly.header(s.title .. (s.version and ("  v" .. s.version) or ""), "addons:" .. s.key, nil, s.icon) then
      for _, l in ipairs(s.lines) do
        if l.id and Ly.toggle then
          Ly.toggle({ label = (l.kind == "take" and "TreeAI prend : " or "TreeAI laisse : ") .. l.label, desc = l.text, on = l.on,
            set = function(v) A.SetLink(l.key, l.id, v) if W and W.Dirty then W.Dirty() end end })
        else Ly.text(l.text, "S", "muted", 6) end
      end
    end
  end
end
A.Render = render
do
  local W = T.Window
  if W and W.AddPage then
    W.AddPage({ id = "addons", hidden = true, label = "Autres addons", icon = ICON .. "INV_Misc_EngGizmos_27", title = "Autres addons",
      desc = "Ce que TreeAI prend à chaque addon, ce qu'il lui laisse — chaque ligne se coupe d'un clic",
      sub = function()
        local n = #A.Sections()
        return n == 0 and "Aucun addon connu chargé" or fmt("%d addon%s branché%s : ce que TreeAI prend, ce qu'il laisse", n, n > 1 and "s" or "", n > 1 and "s" or "")
      end,
      render = render })
    -- rangée avec l'autre page cachée (« Onglet »), avant « Fonctions » / « Options » qui restent en dernier
    local pages, atI, from = W.PAGES, nil, nil
    for i, pg in ipairs(pages) do
      if pg.id == "addons" then from = i elseif not atI and (pg.id == "modules" or pg.id == "options") then atI = i end
    end
    if from and atI and from > atI then table.insert(pages, atI, table.remove(pages, from)) end
  end
end
function A.Open()
  local W = T.Window
  if W and W.Open then W.Open("addons") return true end
  return false
end
T.HUB_EXTRA = T.HUB_EXTRA or {}
table.insert(T.HUB_EXTRA, { group = "reglages", id = "addons", title = "Autres addons", icon = ICON .. "INV_Misc_EngGizmos_27",
  tip = "Clic : ce que TreeAI prend à tes autres addons et ce qu'il leur laisse (Suivi Forever, Questie, Auctionator, Leatrix Plus…)",
  fn = function() A.Open() end })
if T.RegisterSlash then
  T:RegisterSlash("addons", function() if not A.Open() then A:Print("La fenêtre TreeAI est coupée.") end end,
    "addons : ce que TreeAI prend à tes autres addons, ce qu'il leur laisse")
end

function A:OnEnable()
  -- les autres addons finissent de se charger pendant les secondes qui suivent : un regard à 2 s, un autre à 12 s
  A.Installed(true)
  pcall(A.Apply)
  if T.After then
    T:After(2, function() if A._active then A.Installed(true) pcall(A.Apply) end end)
    T:After(12, function() if A._active then A.Installed(true) pcall(A.Apply) if T.Fire then pcall(T.Fire, T, "ADDON_LINKS") end end end)
  end
end
function A:OnDisable()
  -- module coupé : nos réglages d'avant sont rendus (boussole, guidage)
  local was = A.db and A.db.was
  if type(was) ~= "table" then return end
  local C = T.modules and T.modules.Compass
  if was.guide ~= nil and T.SetGuideMode then T:SetGuideMode(was.guide) was.guide = nil end
  if was.compass ~= nil and C and C.db then
    C.db.shown = was.compass
    was.compass = nil
    if C.OnOptionChanged then pcall(C.OnOptionChanged, C, "shown") end
  end
end
