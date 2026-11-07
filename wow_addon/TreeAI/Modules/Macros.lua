-- TreeAI — Modules/Macros.lua : gestionnaire de macros intelligentes (9 classes).
-- Données : Data/Macros.lua (généré par tools/wow_build_macros.py).
-- Règles Blizzard : création/édition UNIQUEMENT hors combat (InCombatLockdown), aucune
-- automatisation : une macro = une capacité par appui, le joueur appuie lui-même.
if type(TreeAI) ~= "table" or type(TreeAI.RegisterModule) ~= "function" then return end

local T = TreeAI
local _G = _G
local pairs, ipairs, type, tostring, tonumber, pcall = pairs, ipairs, type, tostring, tonumber, pcall
local tconcat, tsort, select = table.concat, table.sort, select

local PREFIX = "TAI "
local MAX_BODY = 255
local EMPTY_TEXT = "TreeAI : rien dans les sacs pour cette macro"

---------------------------------------------------------------------------
-- API défensive (TreeAI.API de Core.lua si présent, sinon repli local)
---------------------------------------------------------------------------
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h, i, j, k, l, m, n = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h, i, j, k, l, m, n end
  return nil
end

local function API() return type(T.API) == "table" and T.API or {} end

local function spellName(id)
  local A = API()
  if A.SpellName then return A.SpellName(id) end
  local C = _G.C_Spell
  if C and C.GetSpellInfo then
    local info = try(C.GetSpellInfo, id)
    if type(info) == "table" then return info.name end
  end
  return (try(_G.GetSpellInfo, id))
end

local function spellKnownId(id)
  local A = API()
  if A.IsSpellKnown then return A.IsSpellKnown(id) end
  return (try(_G.IsPlayerSpell, id) or try(_G.IsSpellKnown, id)) and true or false
end

local function itemInfo(id)
  local A = API()
  if A.ItemInfo then return A.ItemInfo(id) end
  local C = _G.C_Item
  return try((C and C.GetItemInfo) or _G.GetItemInfo, id)
end

local function itemSpell(id)
  local C = _G.C_Item
  return (try((C and C.GetItemSpell) or _G.GetItemSpell, id))
end

local function eachBagItem(fn)
  local A = API()
  if A.IterBags then
    for _, _, item in A.IterBags() do
      if item and item.itemID then fn(item.itemID, item.count or 1) end
    end
    return
  end
  local C = _G.C_Container
  local nSlots = (C and C.GetContainerNumSlots) or _G.GetContainerNumSlots
  local getId = (C and C.GetContainerItemID) or _G.GetContainerItemID
  local getInfo = (C and C.GetContainerItemInfo) or _G.GetContainerItemInfo
  for bag = 0, (_G.NUM_BAG_SLOTS or 4) do
    for slot = 1, (try(nSlots, bag) or 0) do
      local id = try(getId, bag, slot)
      if id then
        local info, count = try(getInfo, bag, slot)
        if type(info) == "table" then count = info.stackCount else count = select(2, try(getInfo, bag, slot)) end
        fn(id, count or 1)
      end
    end
  end
end

local function inCombat()
  if T.InCombat then return T:InCombat() end
  return try(_G.InCombatLockdown) and true or false
end

local function runOOC(key, fn)
  if T.RunOutOfCombat then return T:RunOutOfCombat(key, fn) end
  if not inCombat() then fn() end
end

local function after(sec, fn)
  if T.After then return T:After(sec, fn) end
  if _G.C_Timer and _G.C_Timer.After then _G.C_Timer.After(sec, fn) else fn() end
end

local function playerClass()
  if T.PlayerClass then return T:PlayerClass() end
  local _, cls = try(_G.UnitClass, "player")
  return cls
end

local function playerLevel() return try(_G.UnitLevel, "player") or 1 end

---------------------------------------------------------------------------
-- Module
---------------------------------------------------------------------------
local M = T:RegisterModule("Macros", {
  title = "Macros",
  defaults = { enabled = true, autoConsumables = true, perCharacter = true, autoUpgrade = true },
  charDefaults = { setup = false, installed = {}, bodies = {} },
  options = {
    { key = "autoConsumables", type = "toggle", label = "Macros consommables mises à jour (nourriture, potions, bandages, munitions)" },
    { key = "autoUpgrade", type = "toggle", label = "Améliorer les macros quand tu apprends un sort" },
    { key = "perCharacter", type = "toggle", label = "Créer les macros dans l'onglet du personnage" },
  },
})
if not M then return end
T.Macros = M

local function data() return T.Data and T.Data.Macros end
local function db() return M.db or {} end
local function char()
  local c = M.char
  if type(c) ~= "table" then c = {}; M.char = c end
  c.installed = c.installed or {}
  c.bodies = c.bodies or {}
  return c
end

local function suggest(id, text, prio, opts)
  if M.Suggest then return M:Suggest(id, text, prio, opts) end
  if T.Suggest then return T:Suggest("Macros:" .. id, text, prio, opts) end
end

local function say(...)
  if M.Print then M:Print(...) elseif _G.print then _G.print("TreeAI Macros:", ...) end
end

---------------------------------------------------------------------------
-- Sorts : nom localisé (ID -> GetSpellInfo, repli nom FR) + connu ?
---------------------------------------------------------------------------
-- Nom -> connu ? (grimoire) : C_Spell.GetSpellInfo(nom).spellID + IsPlayerSpell, sinon l'ancienne
-- GetSpellInfo(nom) qui ne répond que pour les sorts du grimoire (familles Classic).
local function knownByName(name)
  if not name then return nil end
  local C = _G.C_Spell
  if C and C.GetSpellInfo then
    local info = try(C.GetSpellInfo, name)
    if type(info) == "table" and info.spellID then
      if spellKnownId(info.spellID) then return info.name or name end
      return nil
    end
  end
  if _G.GetSpellInfo then
    local n = try(_G.GetSpellInfo, name)
    if n then return (type(n) == "string") and n or name end
  end
  return nil
end

local resolveCache = {}
local function resolveSpell(id)
  local hit = resolveCache[id]
  if hit then return hit[1], hit[2] end
  local D = data()
  local fr = D and D.spells and D.spells[id]
  local name = spellName(id)
  local res
  if D and D.pet and D.pet[id] then
    res = { name or fr, true }        -- sort de familier : jamais requis
  elseif name and spellKnownId(id) then
    res = { name, true }
  else
    -- ID inconnu ou déplacé (classes refaites dans Forever) : test par nom dans le grimoire
    local byName = knownByName(name) or knownByName(fr)
    if byName then res = { byName, true } else res = { name or fr, false } end
  end
  resolveCache[id] = res
  return res[1], res[2]
end
M.ResolveSpell = resolveSpell
function M:ClearSpellCache() resolveCache = {} end

---------------------------------------------------------------------------
-- Consommables : meilleur objet des sacs par catégorie (façon Buffet)
---------------------------------------------------------------------------
local DIET = { -- GetPetFoodTypes() renvoie des noms localisés
  meat = { "viande", "meat" }, fish = { "poisson", "fish" }, bread = { "pain", "bread" },
  cheese = { "fromage", "cheese" }, fruit = { "fruit" }, fungus = { "champignon", "fung" },
}

local function firstAidSkill()
  local n = try(_G.GetNumSkillLines)
  if not n then return nil end
  for i = 1, n do
    local name, header, _, rank = try(_G.GetSkillLineInfo, i)
    if name and not header then
      local l = name:lower()
      if l:find("secourisme", 1, true) or l:find("first aid", 1, true) then return rank end
    end
  end
  return 0
end

local function rangedAmmoType()
  local id = try(_G.GetInventoryItemID, "player", 18)
  if not id then return nil end
  local r = { itemInfo(id) }
  local classID, subID = r[12], r[13]
  if classID ~= 2 then return nil end
  if subID == 3 then return "bullet" end
  if subID == 2 or subID == 18 then return "arrow" end
  return nil
end

local function petDiet()
  local types = { try(_G.GetPetFoodTypes) }
  if #types == 0 then return nil end
  local ok = {}
  for _, t in ipairs(types) do
    local l = tostring(t):lower()
    for tag, pats in pairs(DIET) do
      for _, p in ipairs(pats) do
        if l:find(p, 1, true) then ok[tag] = true end
      end
    end
  end
  return ok
end

-- Construit le contexte : comptes du sac + sélection par catégorie (mémoïsée).
local function newContext()
  local counts, unknown = {}, {}
  eachBagItem(function(id, n) counts[id] = (counts[id] or 0) + n end)
  local D = data() or {}
  local items = D.items or {}
  -- Préfère les listes générées depuis le client (Data/Consumables.lua) quand elles existent.
  local C = T.Data and T.Data.Consumables
  if type(C) == "table" then
    local merged = {}
    for k, v in pairs(items) do merged[k] = v end
    for k, v in pairs(C) do
      if type(v) == "table" and #v > 0 then merged[k] = v end
    end
    items = merged
  end
  local known = {}
  for _, rows in pairs(items) do
    for _, row in ipairs(rows) do known[row[1]] = true end
  end
  for id in pairs(counts) do
    if not known[id] then unknown[#unknown + 1] = id end
  end
  tsort(unknown)
  local level = playerLevel()
  local ctx = { counts = counts, cache = {} }

  local function bestOf(rows, okRow, lowest)
    local best, bestScore
    for _, row in ipairs(rows or {}) do
      local id, req, val, tag = row[1], row[2], row[3], row[4]
      if (counts[id] or 0) > 0 and okRow(req, tag) then
        local score = val + ((tag == "conj") and 0.5 or 0) -- invoqué = gratuit : priorité à égalité
        if lowest then score = -score end
        if not bestScore or score > bestScore then best, bestScore = id, score end
      end
    end
    return best, bestScore
  end

  local function byLevel(req) return req <= level end

  -- nourriture / boisson inconnue de la base : détectée par son sort « Nourriture » / « Boisson »
  local function unknownConsumable(spellId)
    local want = spellName(spellId)
    if not want then return nil end
    local best, bestLvl
    for _, id in ipairs(unknown) do
      if itemSpell(id) == want then
        local r = { itemInfo(id) }
        local minLvl = r[5] or 1
        if minLvl <= level and (not bestLvl or minLvl > bestLvl) then best, bestLvl = id, minLvl end
      end
    end
    return best, bestLvl
  end

  local pick = {}
  function pick.food()
    local id, score = bestOf(items.food, byLevel)
    local u, ulvl = unknownConsumable(433)
    if u and (not score or ulvl * 45 > score) then return u end
    return id
  end
  function pick.water()
    local id, score = bestOf(items.water, byLevel)
    local u, ulvl = unknownConsumable(430)
    if u and (not score or ulvl * 60 > score) then return u end
    return id
  end
  function pick.hpot() return (bestOf(items.hpot, byLevel)) end
  function pick.mpot() return (bestOf(items.mpot, byLevel)) end
  function pick.hs() return (bestOf(items.hs, byLevel)) end
  function pick.managem() return (bestOf(items.managem, byLevel)) end
  function pick.poison_mh() return (bestOf(items.poison_mh, byLevel)) end
  function pick.poison_oh() return (bestOf(items.poison_oh, byLevel)) end
  function pick.bandage()
    local skill = firstAidSkill()
    return (bestOf(items.bandage, function(req) return not skill or req <= skill end))
  end
  function pick.ammo()
    local want = rangedAmmoType()
    return (bestOf(items.ammo, function(req, tag) return req <= level and (not want or tag == want) end))
  end
  function pick.petfood()
    local H = T.GetModule and T:GetModule("Hunter")
    if H and type(H.GetBestPetFood) == "function" then
      local ok, id = pcall(H.GetBestPetFood, H)
      if ok and id then return id end
    end
    local diet = petDiet()
    if not diet then return nil end
    local petLvl = try(_G.UnitLevel, "pet") or level
    -- la plus petite nourriture encore « appréciée » (niveau objet >= niveau du familier - 10)
    -- pour garder la bonne nourriture ; sinon la meilleure disponible.
    local function ilvl(req) return (req <= 1) and 5 or (req + 10) end
    local id = bestOf(items.food, function(req, tag)
      return diet[tag] and ilvl(req) >= petLvl - 10
    end, true)
    return id or (bestOf(items.food, function(_, tag) return diet[tag] end))
  end

  function ctx.item(cat)
    local c = ctx.cache[cat]
    if c == nil then
      local f = pick[cat]
      c = (f and f()) or false
      ctx.cache[cat] = c
    end
    return c or nil
  end
  return ctx
end
M.NewContext = newContext

---------------------------------------------------------------------------
-- Rendu : variante -> texte final (ou nil si impossible)
---------------------------------------------------------------------------
local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

-- remplace les jetons ; renvoie texte, ok (tous résolus/connus)
local function subTokens(s, ctx)
  local ok = true
  s = s:gsub("{item:([%a_]+)}", function(cat)
    local id = ctx.item(cat)
    if not id then ok = false; return "" end
    return "item:" .. id
  end)
  s = s:gsub("{slot:(%d+)}", function(n)
    -- bijou équipé avec un effet « Utiliser : » (sinon la ligne optionnelle disparaît)
    local id = try(_G.GetInventoryItemID, "player", tonumber(n))
    if id and itemSpell(id) then return n end
    ok = false
    return ""
  end)
  s = s:gsub("{(%d+)}", function(id)
    local name, known = resolveSpell(tonumber(id))
    if not (name and known) then ok = false end
    return name or ""
  end)
  return s, ok
end

local function renderVariant(v, ctx)
  for _, sid in ipairs(v.r or {}) do
    local name, known = resolveSpell(sid)
    if not (name and known) then return nil end
  end
  for _, cat in ipairs(v.it or {}) do
    if not ctx.item(cat) then return nil end
  end
  local out = {}
  for line in ((v.b or "") .. "\n"):gmatch("(.-)\n") do
    local optional = line:sub(1, 1) == "?"
    if optional then line = line:sub(2) end
    local cmd, rest = line:match("^(/%S+)%s+(.+)$")
    local keep = true
    if cmd and cmd ~= "/run" and cmd ~= "/script" and rest:find("?", 1, true) then
      local kept = {}
      for clause in (rest .. ";"):gmatch("([^;]*);") do
        local c = trim(clause)
        if c:sub(1, 1) == "?" then
          local r, ok = subTokens(c:sub(2), ctx)
          if ok then kept[#kept + 1] = r end
        elseif c ~= "" then
          kept[#kept + 1] = c
        end
      end
      if #kept == 0 then keep = false else line = cmd .. " " .. tconcat(kept, "; ") end
    end
    if keep then
      local r, ok = subTokens(line, ctx)
      if ok then
        out[#out + 1] = r
      elseif not optional then
        return nil
      end
    end
  end
  local body = tconcat(out, "\n")
  if #body > MAX_BODY or body == "" then return nil end
  if body == "#showtooltip" then return nil end
  -- une macro de consommables sans aucun objet trouvé ne sert à rien
  if (v.b or ""):find("{item:", 1, true) and not body:find("item:%d") then return nil end
  return body
end
M.RenderVariant = renderVariant

function M:Render(def, ctx)
  ctx = ctx or newContext()
  for _, v in ipairs(def.v or {}) do
    local body = renderVariant(v, ctx)
    if body then return body end
  end
  if def.c == "consumable" then
    return "/run print(\"" .. EMPTY_TEXT .. "\")"
  end
  return nil
end

-- Sorts manquants de la 1re variante (pour l'aperçu « pas encore »).
function M:Missing(def)
  local miss = {}
  local v = def.v and def.v[#def.v]
  for _, sid in ipairs((v and v.r) or {}) do
    local name, known = resolveSpell(sid)
    if not known then miss[#miss + 1] = name or tostring(sid) end
  end
  return miss
end

---------------------------------------------------------------------------
-- Spécialisation (talents ou choix du joueur) et contexte (solo / groupe)
---------------------------------------------------------------------------
local function classSpecs()
  local D, cls = data(), playerClass()
  return (D and D.specs and cls and D.specs[cls]) or {}
end

local function detectSpec()
  local specs = classSpecs()
  if #specs == 0 then return nil end
  local group = try(_G.GetActiveTalentGroup) or nil
  local best, bestPts = nil, 0
  for i = 1, #specs do
    local r = { try(_G.GetTalentTabInfo, i, false, false, group) }
    local pts
    if type(r[1]) == "number" and type(r[5]) == "number" then pts = r[5]   -- id, nom, desc, icône, points
    elseif type(r[3]) == "number" then pts = r[3] end                      -- nom, icône, points
    if pts and pts > bestPts then best, bestPts = specs[i].id, pts end
  end
  if best then return best end
  local D, cls = data(), playerClass()
  return (D and D.leveling and cls and D.leveling[cls]) or specs[1].id
end

-- spé effective, auto ? (le choix du joueur dans le panneau prime sur les talents)
function M:Spec()
  local want = char().spec
  if want then
    for _, s in ipairs(classSpecs()) do if s.id == want then return want, false end end
  end
  return detectSpec(), true
end

function M:SpecName(id)
  for _, s in ipairs(classSpecs()) do if s.id == id then return s.n, s.role end end
  return id or "?"
end

local function inGroup()
  if try(_G.IsInGroup) then return true end
  return ((try(_G.GetNumGroupMembers) or 0) > 0) or ((try(_G.GetNumPartyMembers) or 0) > 0)
end

function M:Context()
  local want = char().ctx
  if want == "solo" or want == "groupe" then return want, false end
  return inGroup() and "groupe" or "solo", true
end

local function forSpec(def, spec)
  if not def.s or #def.s == 0 or not spec then return true end
  for _, s in ipairs(def.s) do if s == spec then return true end end
  return false
end

-- Toutes les macros de la classe pour la spé courante (une seule définition par nom).
function M:Defs()
  local D = data()
  local cls = playerClass()
  local all = (D and D.classes and cls and D.classes[cls]) or {}
  local spec = self:Spec()
  local out = {}
  for _, def in ipairs(all) do
    if forSpec(def, spec) then out[#out + 1] = def end
  end
  return out
end

-- Fait partie du jeu installé par défaut pour la spé + le contexte ?
function M:IsDefault(def)
  if not def.def then return false end
  local ctx = self:Context()
  return def.x == nil or def.x == "" or def.x == ctx
end

---------------------------------------------------------------------------
-- Écriture des macros (hors combat, pendant une action du joueur uniquement)
---------------------------------------------------------------------------
local function macroIndex(name)
  local i = try(_G.GetMacroIndexByName, name)
  if i and i > 0 then return i end
  return nil
end

local function freeSlot(perChar)
  local g, c = try(_G.GetNumMacros)
  g, c = g or 0, c or 0
  local maxG = _G.MAX_ACCOUNT_MACROS or 120
  local maxC = _G.MAX_CHARACTER_MACROS or 18
  if perChar and c < maxC then return true, true end
  if g < maxG then return true, false end
  if c < maxC then return true, true end
  return false
end

local function same(a, b) return a and b and trim(a) == trim(b) end

local function userDo(fn, ...)
  if T.UserAction then return T:UserAction(fn, ...) end
  return pcall(fn, ...)
end
M.UserDo = userDo

-- État d'une macro : "user" (modifiée à la main / macro du joueur au même nom), "installed",
-- "ready" (installable), "later" (sorts pas encore appris).
function M:Status(def, ctx)
  local idx = macroIndex(def.n)
  local stored = char().bodies[def.n]
  if idx then
    local _, _, cur = try(_G.GetMacroInfo, idx)
    if not stored or (cur and not same(cur, stored)) then return "user", cur end
    return "installed", cur
  end
  if self:Render(def, ctx) then return "ready" end
  return "later"
end

-- write a macro; returns "created" | "updated" | "same" | "user" | "full" | "fail" | "pending"
function M:Write(def, body, force)
  if inCombat() then return "fail" end
  -- WoW Forever bloque CreateMacro/EditMacro hors d'une action du joueur (« Action bloquée ») :
  -- sans clic / commande, on prépare et on propose « clique pour mettre à jour ».
  if T.InUserAction and not T:InUserAction() then
    M._pending = true
    suggest("pending", "Macros à mettre à jour : clique ici", 1,
      { ttl = 0, onClick = function() M._pending = false; M:Refresh(false) end })
    return "pending"
  end
  local ch = char()
  local idx = macroIndex(def.n)
  if idx then
    local _, _, cur = try(_G.GetMacroInfo, idx)
    local stored = ch.bodies[def.n]
    -- macro modifiée à la main, ou macro du joueur qui porte déjà ce nom : on n'y touche jamais
    -- (sauf « /tai macros forcer »)
    if not force and (not stored or (cur and not same(cur, stored))) then
      if same(cur, body) then ch.bodies[def.n] = cur; ch.installed[def.n] = true; return "same" end
      return "user"
    end
    if same(cur, body) then ch.bodies[def.n] = cur; return "same" end
    local ok = pcall(_G.EditMacro, idx, def.n, nil, body)
    if not ok then return "fail" end
    ch.bodies[def.n] = body
    ch.installed[def.n] = true
    return "updated"
  end
  local okSlot, perChar = freeSlot(db().perCharacter ~= false)
  if not okSlot then return "full" end
  local ok = pcall(_G.CreateMacro, def.n, def.i or "INV_MISC_QUESTIONMARK", body, perChar)
  if not ok or not macroIndex(def.n) then
    ok = pcall(_G.CreateMacro, def.n, "INV_MISC_QUESTIONMARK", body, perChar)
  end
  if not ok or not macroIndex(def.n) then return "fail" end
  ch.bodies[def.n] = body
  ch.installed[def.n] = true
  return "created"
end

local function keyHints(defs)
  local parts = {}
  for _, def in ipairs(defs) do
    if def.k and def.k ~= "-" and char().installed[def.n] then
      parts[#parts + 1] = def.n:sub(#PREFIX + 1) .. "=" .. def.k
    end
  end
  return tconcat(parts, ", ")
end

-- Installe le jeu par défaut de ta spé + contexte (+ celles déjà installées) ; all=true : toutes.
function M:Install(all, force)
  runOOC("Macros:install", function()
    local ctx = newContext()
    local defs = self:Defs()
    local n, full, user, pend = 0, 0, 0, 0
    for _, def in ipairs(defs) do
      if all or self:IsDefault(def) or char().installed[def.n] then
        local body = self:Render(def, ctx)
        if body then
          local r = self:Write(def, body, force)
          if r == "created" or r == "updated" then n = n + 1
          elseif r == "full" then full = full + 1
          elseif r == "user" then user = user + 1
          elseif r == "pending" then pend = pend + 1 end
        end
      end
    end
    if pend > 0 then return end
    char().setup = true
    if self.Clear then self:Clear("setup"); self:Clear("pending") end
    local spec = self:Spec()
    say(("%d macro(s) installée(s)/mise(s) à jour (%s, %s)."):format(n, self:SpecName(spec), (self:Context())))
    if full > 0 then
      suggest("full", ("%d macro(s) non créée(s) : plus de place (18 perso / 120 compte)."):format(full), 2)
    end
    if user > 0 then
      say(("%d macro(s) modifiée(s) à la main laissée(s) telle(s) (/tai macros forcer)."):format(user))
    end
    local hints = keyHints(defs)
    if hints ~= "" then
      suggest("binds", "Glisse les macros TAI dans tes barres : " .. hints .. " (/tai macros)", 1,
        { ttl = 180, onClick = function() M:TogglePanel() end })
    end
  end)
  if inCombat() then say("En combat : les macros seront installées à la sortie du combat.") end
end

-- Installe / met à jour UNE macro (bouton du panneau). force : écrase une version modifiée.
function M:InstallOne(def, force)
  if inCombat() then say("Impossible en combat."); return "fail" end
  local body = self:Render(def)
  if not body then say(def.n .. " : sorts pas encore appris."); return "later" end
  local r = self:Write(def, body, force)
  if r == "user" then say(def.n .. " : modifiée à la main, laissée telle quelle (Maj+clic pour écraser).") end
  if r == "created" or r == "updated" then char().setup = true end
  return r
end

local function usesItems(def)
  if def.c == "consumable" then return true end
  for _, v in ipairs(def.v or {}) do
    local b = v.b or ""
    if b:find("{item:", 1, true) or b:find("{slot:", 1, true) then return true end
  end
  return false
end

-- Met à jour les macros déjà installées (kind = "consumable" ou toutes).
function M:Refresh(onlyConsumables)
  runOOC(onlyConsumables and "Macros:consumables" or "Macros:refresh", function()
    local ctx = newContext()
    for _, def in ipairs(self:Defs()) do
      if char().installed[def.n] and (not onlyConsumables or usesItems(def)) and macroIndex(def.n) then
        local body = self:Render(def, ctx)
        if body then
          if self:Write(def, body, false) == "pending" then return end
        end
      end
    end
    if self.panel and self.panel:IsShown() then self:FillPanel() end
  end)
end

function M:Remove()
  runOOC("Macros:remove", function()
    local n = 0
    for name in pairs(char().installed) do
      local idx = macroIndex(name)
      if idx and name:sub(1, #PREFIX) == PREFIX then
        local _, _, cur = try(_G.GetMacroInfo, idx)
        local stored = char().bodies[name]
        -- jamais supprimer une macro modifiée par le joueur
        if stored and same(cur, stored) and pcall(_G.DeleteMacro, idx) then n = n + 1 end
      end
    end
    char().installed, char().bodies, char().setup = {}, {}, false
    say(("%d macro(s) TreeAI supprimée(s)."):format(n))
  end)
end

local STATUS_TXT = { installed = "installée", user = "modifiée par toi", ready = "dispo", later = "pas encore" }

function M:List()
  local ctx = newContext()
  local spec = self:Spec()
  say(("Spé : %s — contexte : %s"):format(self:SpecName(spec), (self:Context())))
  for _, def in ipairs(self:Defs()) do
    say(("%s [%s] %s — %s"):format(def.n, def.k or "-", STATUS_TXT[self:Status(def, ctx)] or "?", def.d or ""))
  end
end

---------------------------------------------------------------------------
-- Panneau : liste (gauche) + aperçu, « pourquoi » et boutons (droite)
---------------------------------------------------------------------------
local ROWS, ROW_H, W, H = 20, 17, 660, 430
local STATUS_COLOR = { installed = "|cff40ff40", user = "|cffffa040", ready = "|cffffffff", later = "|cff808080" }

local function button(parent, text, w)
  local b = _G.CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  b:SetSize(w or 120, 20)
  b:SetText(text)
  return b
end

local function cycleSpec()
  local specs = classSpecs()
  local cur = char().spec
  local nextId
  if not cur then nextId = specs[1] and specs[1].id
  else
    for i, s in ipairs(specs) do
      if s.id == cur then nextId = specs[i + 1] and specs[i + 1].id end
    end
  end
  char().spec = nextId -- nil = auto (talents)
end

local function cycleCtx()
  local cur = char().ctx
  char().ctx = (cur == nil and "solo") or (cur == "solo" and "groupe") or nil
end

function M:TogglePanel()
  if self.panel then
    if self.panel:IsShown() then self.panel:Hide() else self:FillPanel(); self.panel:Show() end
    return
  end
  if not _G.CreateFrame then return end
  local f = _G.CreateFrame("Frame", nil, _G.UIParent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  f:SetSize(W, H)
  f:SetPoint("CENTER")
  f:SetFrameStrata("DIALOG")
  f:SetMovable(true); f:EnableMouse(true); f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving); f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.SetBackdrop then
    f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    f:SetBackdropColor(0, 0, 0, 0.9)
  end
  local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", 10, -8)
  title:SetText("Macros TreeAI")
  local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 2, 2)

  f.specBtn = button(f, "Spé", 170); f.specBtn:SetPoint("TOPLEFT", 120, -6)
  f.specBtn:SetScript("OnClick", function() cycleSpec(); M:FillPanel() end)
  f.ctxBtn = button(f, "Contexte", 130); f.ctxBtn:SetPoint("LEFT", f.specBtn, "RIGHT", 6, 0)
  f.ctxBtn:SetScript("OnClick", function() cycleCtx(); M:FillPanel() end)

  -- liste
  f.offset = 0
  f.rows = {}
  for i = 1, ROWS do
    local row = _G.CreateFrame("Button", nil, f)
    row:SetSize(250, ROW_H - 1)
    row:SetPoint("TOPLEFT", 10, -32 - (i - 1) * ROW_H)
    row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.text:SetAllPoints(); row.text:SetJustifyH("LEFT")
    row.hl = row:CreateTexture(nil, "BACKGROUND")
    row.hl:SetAllPoints()
    if row.hl.SetColorTexture then row.hl:SetColorTexture(0.2, 0.6, 0.3, 0.35)
    else row.hl:SetTexture("Interface\\Buttons\\WHITE8X8"); row.hl:SetVertexColor(0.2, 0.6, 0.3, 0.35) end
    row.hl:Hide()
    row:SetScript("OnClick", function(b) if b.def then M.selected = b.def.n; M:FillPanel() end end)
    f.rows[i] = row
  end
  f:EnableMouseWheel(true)
  f:SetScript("OnMouseWheel", function(_, delta)
    f.offset = math.max(0, (f.offset or 0) - delta * 3)
    M:FillPanel()
  end)

  -- détail
  local x = 272
  f.name = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  f.name:SetPoint("TOPLEFT", x, -34)
  f.meta = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.meta:SetPoint("TOPLEFT", x, -56); f.meta:SetWidth(W - x - 14); f.meta:SetJustifyH("LEFT")
  f.desc = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  f.desc:SetPoint("TOPLEFT", x, -74); f.desc:SetWidth(W - x - 14); f.desc:SetJustifyH("LEFT")
  local box = _G.CreateFrame("Frame", nil, f, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  box:SetPoint("TOPLEFT", x - 4, -104); box:SetSize(W - x - 6, 120)
  if box.SetBackdrop then
    box:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background" })
    box:SetBackdropColor(0.08, 0.08, 0.1, 0.95)
  end
  f.body = box:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.body:SetPoint("TOPLEFT", 6, -6); f.body:SetWidth(W - x - 20)
  f.body:SetJustifyH("LEFT"); f.body:SetJustifyV("TOP")
  f.whyT = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.whyT:SetPoint("TOPLEFT", x, -232); f.whyT:SetText("Pourquoi")
  f.why = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.why:SetPoint("TOPLEFT", x, -248); f.why:SetWidth(W - x - 14)
  f.why:SetJustifyH("LEFT"); f.why:SetJustifyV("TOP")

  f.one = button(f, "Installer cette macro", 160); f.one:SetPoint("BOTTOMLEFT", x, 34)
  f.one:SetScript("OnClick", function()
    local def = M:SelectedDef()
    if def then
      local force = _G.IsShiftKeyDown and _G.IsShiftKeyDown()
      userDo(M.InstallOne, M, def, force)
      M:FillPanel()
    end
  end)
  f.pick = button(f, "Prendre (barre)", 120); f.pick:SetPoint("LEFT", f.one, "RIGHT", 6, 0)
  f.pick:SetScript("OnClick", function()
    local def = M:SelectedDef()
    local idx = def and macroIndex(def.n)
    if idx and not inCombat() then try(_G.PickupMacro, idx) end
  end)
  f.all = button(f, "Tout installer (ma spé)", 180); f.all:SetPoint("BOTTOMLEFT", x, 8)
  f.all:SetScript("OnClick", function()
    userDo(M.Install, M, false)
    after(0.3, function() M:FillPanel() end)
  end)
  f.upd = button(f, "Mettre à jour", 110); f.upd:SetPoint("LEFT", f.all, "RIGHT", 6, 0)
  f.upd:SetScript("OnClick", function() userDo(M.Refresh, M, false); after(0.3, function() M:FillPanel() end) end)
  f.del = button(f, "Retirer", 80); f.del:SetPoint("LEFT", f.upd, "RIGHT", 6, 0)
  f.del:SetScript("OnClick", function() userDo(M.Remove, M); after(0.3, function() M:FillPanel() end) end)
  f.hint = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  f.hint:SetPoint("BOTTOMLEFT", 10, 10); f.hint:SetWidth(250); f.hint:SetJustifyH("LEFT")
  f.hint:SetText("Rien n'est créé sans ton clic. Une macro modifiée à la main n'est jamais écrasée.")
  self.panel = f
  self:FillPanel()
end

function M:SelectedDef()
  for _, def in ipairs(self:Defs()) do
    if def.n == self.selected then return def end
  end
  return nil
end

function M:FillPanel()
  local f = self.panel
  if not f then return end
  local ctx = newContext()
  local defs = self:Defs()
  local spec, autoSpec = self:Spec()
  local where, autoCtx = self:Context()
  f.specBtn:SetText("Spé : " .. self:SpecName(spec) .. (autoSpec and " (auto)" or ""))
  f.ctxBtn:SetText((where == "groupe" and "Groupe" or "Solo") .. (autoCtx and " (auto)" or ""))
  -- tri : installées / dispo d'abord, puis par niveau
  local rows = {}
  for _, def in ipairs(defs) do
    rows[#rows + 1] = { def = def, st = self:Status(def, ctx) }
  end
  local rank = { installed = 1, user = 1, ready = 2, later = 3 }
  tsort(rows, function(a, b)
    if rank[a.st] ~= rank[b.st] then return rank[a.st] < rank[b.st] end
    if (a.def.l or 1) ~= (b.def.l or 1) then return (a.def.l or 1) < (b.def.l or 1) end
    return a.def.n < b.def.n
  end)
  f.offset = math.min(f.offset or 0, math.max(0, #rows - ROWS))
  if not self.selected and rows[1] then self.selected = rows[1].def.n end
  for i = 1, ROWS do
    local row, r = f.rows[i], rows[i + f.offset]
    if r then
      local def = r.def
      local tag = self:IsDefault(def) and "" or " |cff808080(option)|r"
      local lvl = (r.st == "later" and def.l and def.l <= 60) and (" |cff808080niv." .. def.l .. "|r") or ""
      row.def = def
      row.text:SetText(("%s%s|r  [%s]%s%s"):format(STATUS_COLOR[r.st] or "", def.n:sub(#PREFIX + 1),
        def.k or "-", lvl, tag))
      if def.n == self.selected then row.hl:Show() else row.hl:Hide() end
      row:Show()
    else
      row.def = nil
      row:Hide()
    end
  end
  local def = self:SelectedDef()
  if not def then return end
  local st, cur = self:Status(def, ctx)
  local body = self:Render(def, ctx)
  f.name:SetText(def.n)
  local ctxTxt = (def.x == "groupe" and "groupe") or (def.x == "solo" and "solo") or "solo + groupe"
  f.meta:SetText(("Touche conseillée : %s   |   niveau %s   |   %s   |   %s%s|r"):format(
    def.k or "-", tostring(def.l or 1), ctxTxt, STATUS_COLOR[st] or "", STATUS_TXT[st] or ""))
  f.desc:SetText(def.d or "")
  if st == "user" and cur then
    f.body:SetText("|cffffa040Ta version (gardée) :|r\n" .. cur .. (body and ("\n|cff40ff40TreeAI propose :|r\n" .. body) or ""))
  elseif body then
    f.body:SetText(body .. ("\n|cff808080%d / 255 caractères|r"):format(#body))
  else
    local miss = self:Missing(def)
    f.body:SetText("|cff808080Pas encore disponible" .. (#miss > 0 and (" : il te manque " .. tconcat(miss, ", ")) or "") .. ".|r")
  end
  f.why:SetText(def.w or "")
  f.one:SetText((st == "installed" and "Mettre à jour") or (st == "user" and "Maj+clic : écraser") or "Installer cette macro")
end

---------------------------------------------------------------------------
-- Événements
---------------------------------------------------------------------------
local pendingCons, pendingSpells = false, false

local function scheduleConsumables()
  if pendingCons or db().autoConsumables == false or not char().setup then return end
  pendingCons = true
  after(1, function() pendingCons = false; M:Refresh(true) end)
end

local function scheduleSpells()
  resolveCache = {}
  if pendingSpells or db().autoUpgrade == false or not char().setup then return end
  pendingSpells = true
  after(2, function()
    pendingSpells = false
    M:Refresh(false)
    -- nouvelles macros par défaut devenues possibles (sort appris)
    local ctx, fresh = newContext(), {}
    for _, def in ipairs(M:Defs()) do
      if M:IsDefault(def) and def.c ~= "consumable" and not char().installed[def.n]
        and not macroIndex(def.n) and M:Render(def, ctx) then
        fresh[#fresh + 1] = def.n
      end
    end
    if #fresh > 0 then
      suggest("new", "Nouvelle(s) macro(s) dispo : " .. tconcat(fresh, ", ") .. " (clique pour voir)", 2,
        { ttl = 300, onClick = function() M:TogglePanel() end })
    end
  end)
end

function M:OnEnable()
  self:RegisterEvent("BAG_UPDATE_DELAYED", scheduleConsumables)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() scheduleConsumables(); scheduleSpells() end)
  self:RegisterEvent("SKILL_LINES_CHANGED", scheduleConsumables)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", scheduleConsumables)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", scheduleConsumables)
  self:RegisterEvent("UNIT_PET", scheduleConsumables)
  self:RegisterEvent("SPELLS_CHANGED", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_TAB", scheduleSpells)
  self:RegisterEvent("ACTIVE_TALENT_GROUP_CHANGED", scheduleSpells)
  self:RegisterEvent("CHARACTER_POINTS_CHANGED", scheduleSpells)
  if not data() then return end
  if char().setup then
    after(3, function() M:Refresh(false) end)
  else
    suggest("setup", "Macros expertes prêtes pour ta classe : clique pour les voir et les installer", 2,
      { ttl = 0, onClick = function() M:TogglePanel() end })
  end
end

---------------------------------------------------------------------------
-- Slash : /tai macros [installer|tout|maj|forcer|liste|retirer|spe|solo|groupe|auto]
---------------------------------------------------------------------------
local function slash(rest)
  local cmd = trim((rest or ""):lower())
  if cmd == "installer" or cmd == "install" then M:Install(false)
  elseif cmd == "tout" or cmd == "all" then M:Install(true)
  elseif cmd == "forcer" then M:Install(false, true)
  elseif cmd == "maj" or cmd == "update" then M:Refresh(false)
  elseif cmd == "liste" or cmd == "list" then M:List()
  elseif cmd == "retirer" or cmd == "remove" then M:Remove()
  elseif cmd == "spe" or cmd == "spé" then cycleSpec(); say("Spé : " .. M:SpecName((M:Spec())))
  elseif cmd == "solo" or cmd == "groupe" then char().ctx = cmd; say("Contexte : " .. cmd)
  elseif cmd == "auto" then char().ctx, char().spec = nil, nil; say("Spé et contexte automatiques.")
  else M:TogglePanel() end
end
M.Slash = slash
if T.RegisterSlash then
  T:RegisterSlash("macros", slash,
    "macros [installer|tout|maj|forcer|liste|retirer|spe|solo|groupe|auto] : macros expertes")
end
