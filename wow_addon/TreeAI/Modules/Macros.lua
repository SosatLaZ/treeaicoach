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

-- UNE seule ligne « macros » dans les conseils (installer, nouvelles, à mettre à jour : même id)
local READY_TEXT = "Nouvelles macros prêtes : clique pour installer"
local function suggestReady(text, names)
  local tip = { "Macros TreeAI", "Clic : installer · Maj-clic : voir la liste" }
  if names and #names > 0 then tip[#tip + 1] = tconcat(names, ", ") end
  suggest("ready", text or READY_TEXT, 2, { ttl = 0, tooltip = tip, onClick = function()
    if _G.IsShiftKeyDown and _G.IsShiftKeyDown() then M:TogglePanel() return end
    M._pending = false
    if M.Clear then M:Clear("ready") end
    -- nouvelles macros (sort appris) : on les crée ; sinon simple mise à jour des macros installées
    if M:IsBlocked() then M:ShowCopy(M:PlanList(false))
    elseif char().setup and not (names and #names > 0) then M:Refresh(false) else M:Install(false) end
  end })
end
M._SuggestReady = suggestReady

-- Pas de chat (règle TreeAI) : les messages vont dans la ligne d'état du panneau (M._log, 20 derniers).
local function say(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  local msg = tconcat(parts, " ")
  local log = M._log or {}
  M._log = log
  log[#log + 1] = msg
  if #log > 20 then table.remove(log, 1) end
  M.lastMsg = msg
  local f = M.panel
  if f and f.status then f.status:SetText(msg) end
end
M._Say = say

---------------------------------------------------------------------------
-- Sorts : nom localisé (ID -> GetSpellInfo, repli nom FR) + connu ?
---------------------------------------------------------------------------
-- Nom -> connu ? Forever a des rangs (C_SpellBook.IsSpellBookItemLowRank) : l'ID du rang 1 peut ne pas
-- être « connu » quand un rang supérieur l'est. On teste donc aussi le NOM (sans rang) dans le grimoire :
-- FindSpellBookSlotForSpell(nom), C_Spell.GetSpellInfo(nom).spellID, puis un scan du grimoire.
local bookNames
local function spellBookSet()
  if bookNames then return bookNames end
  local set = {}
  local A = API()
  local list = A.SpellBookSpells and A.SpellBookSpells(false) or {}
  for _, s in ipairs(type(list) == "table" and list or {}) do
    if type(s) == "table" and type(s.name) == "string" and not s.passive then set[s.name:lower()] = s.name end
  end
  bookNames = set   -- vidé à SPELLS_CHANGED / LEARNED_SPELL_* et à chaque installation / diagnostic
  return set
end

local function baseName(n)
  if type(n) ~= "string" then return nil end
  n = n:gsub("%s*%(.-%)%s*$", ""):gsub("^%s+", ""):gsub("%s+$", "")
  if n == "" then return nil end
  return n
end

local function knownByName(name)
  name = baseName(name)
  if not name then return nil end
  local SB = _G.C_SpellBook
  if SB and SB.FindSpellBookSlotForSpell then
    -- includeHidden, includeFlyouts, includeFutureSpells, includeOffSpec = false : seulement les sorts appris
    local slot = try(SB.FindSpellBookSlotForSpell, name, false, true, false, false)
    if slot then return name end
  end
  local C = _G.C_Spell
  if C and C.GetSpellInfo then
    local info = try(C.GetSpellInfo, name)
    if type(info) == "table" and info.spellID and spellKnownId(info.spellID) then return info.name or name end
  end
  local hit = spellBookSet()[name:lower()]
  if hit then return hit end
  if _G.GetSpellInfo and not (C and C.GetSpellInfo) then
    local n, _, _, _, _, _, sid = try(_G.GetSpellInfo, name)
    if n and (type(sid) ~= "number" or spellKnownId(sid)) then return (type(n) == "string") and n or name end
  end
  return nil
end
M.KnownByName = knownByName

local resolveCache = {}
local function resolveSpell(id)
  local hit = resolveCache[id]
  if hit then return hit[1], hit[2] end
  local D = data()
  local fr = D and D.spells and D.spells[id]
  local name = baseName(spellName(id))
  local res
  if D and D.pet and D.pet[id] then
    res = { name or fr, true }        -- sort de familier : jamais requis
  elseif name and spellKnownId(id) then
    res = { name, true }
  else
    -- rang inférieur, ID déplacé (classes refaites dans Forever) : test par nom dans le grimoire
    local byName = knownByName(name) or knownByName(fr)
    if byName then res = { byName, true } else res = { name or fr, false } end
  end
  resolveCache[id] = res   -- vidé à SPELLS_CHANGED (grimoire chargé / sort appris) et avant chaque installation
  return res[1], res[2]
end
M.ResolveSpell = resolveSpell
function M:ClearSpellCache() resolveCache = {}; bookNames = nil end

---------------------------------------------------------------------------
-- Consommables : meilleur objet des sacs par catégorie (façon Buffet)
---------------------------------------------------------------------------
local DIET = { -- GetPetFoodTypes() renvoie des noms localisés
  meat = { "viande", "meat" }, fish = { "poisson", "fish" }, bread = { "pain", "bread" },
  cheese = { "fromage", "cheese" }, fruit = { "fruit" }, fungus = { "champignon", "fung" },
}

local function firstAidSkill()
  local A = API()   -- C_SkillInfo sur Forever (GetSkillLineInfo global retiré)
  local lines = A.SkillLines and A.SkillLines() or {}
  if #lines == 0 then return nil end
  for _, s in ipairs(lines) do
    local name, header, rank = s.name, s.isHeader, s.rank
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
  local A = API()   -- C_PetInfo.GetPetFoodTypes() sur Forever (rend une table)
  local types = A.PetFoodTypes and A.PetFoodTypes() or { try(_G.GetPetFoodTypes) }
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
  -- Les niveaux requis de Data/Macros.lua (bandage = Secourisme requis pour UTILISER, pierre de soins =
  -- niveau de la pierre) restent prioritaires quand la liste client les met à 1.
  local C = T.Data and T.Data.Consumables
  if type(C) == "table" then
    local merged = {}
    for k, v in pairs(items) do merged[k] = v end
    for k, v in pairs(C) do
      if type(v) == "table" and #v > 0 then
        local reqOf = {}
        for _, row in ipairs(items[k] or {}) do reqOf[row[1]] = row[2] end
        local rows = {}
        for i, row in ipairs(v) do
          local r = reqOf[row[1]]
          if r and r > (row[2] or 0) then rows[i] = { row[1], r, row[3], row[4] } else rows[i] = row end
        end
        merged[k] = rows
      end
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
  function pick.hearth() return (bestOf(items.hearth, byLevel)) end
  -- monture : objet du sac de classe « Divers / Monture » (15/5) dont le niveau requis est atteint
  function pick.mount()
    local best, bestLvl
    for id in pairs(counts) do
      local r = { itemInfo(id) }
      if r[12] == 15 and r[13] == 5 then
        local lvl = tonumber(r[5]) or 1
        if lvl <= level and (not bestLvl or lvl > bestLvl or (lvl == bestLvl and id < best)) then
          best, bestLvl = id, lvl
        end
      end
    end
    return best
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

-- Texte de macro accepté par le jeu ? (≤ 255 octets, aucun jeton restant, aucune commande vide,
-- chaque clause /cast /use a un sort / objet). Renvoie ok, raison.
local SPELL_CMDS = { ["/cast"] = true, ["/use"] = true, ["/castsequence"] = true, ["/castrandom"] = true }
function M.ValidBody(body)
  if type(body) ~= "string" or body == "" then return false, "vide" end
  if #body > MAX_BODY then return false, ("trop long (%d/255)"):format(#body) end
  if body:find("[{}]") then return false, "jeton non remplacé" end
  local cmds = 0
  for line in (body .. "\n"):gmatch("(.-)\n") do
    local l = trim(line)
    if l:sub(1, 1) == "/" then
      cmds = cmds + 1
      local cmd, rest = l:match("^(/%S+)%s*(.*)$")
      cmd = cmd and cmd:lower()
      if SPELL_CMDS[cmd] then
        if rest == "" then return false, "commande vide : " .. cmd end
        if rest:find(";%s*$") or rest:find("^%s*;") or rest:find(";%s*;") then return false, "clause vide" end
        for clause in (rest .. ";"):gmatch("([^;]*);") do
          local c = clause:gsub("%b[]", ""):gsub("reset=%S+", "")
          c = c:gsub("[!%s,]", "")
          if c == "" then return false, "clause sans sort : " .. cmd end
        end
      end
    elseif l ~= "" and l:sub(1, 1) ~= "#" then
      return false, "ligne invalide"
    end
  end
  if cmds == 0 then return false, "aucune commande" end
  return true
end

-- [form:N] des modèles = ordre Classic (1 ours, 2 aquatique, 3 félin, 4 voyage, 5 sélénien). Un druide à qui
-- il manque une forme a les suivantes décalées : on réécrit N d'après GetShapeshiftFormInfo (sorts réels).
local function formMap()
  local D = data()
  local want = D and D.forms and D.forms[playerClass() or ""]
  local n = tonumber((try(_G.GetNumShapeshiftForms))) or 0
  if not want or n <= 0 then return nil end
  local byId = {}
  for i = 1, n do
    local r = { try(_G.GetShapeshiftFormInfo, i) }
    local sid = tonumber(r[4]) or tonumber(r[5])
    if sid then byId[sid] = i end
  end
  local map, changed = {}, false
  for c, ids in ipairs(want) do
    local idx
    for _, sid in ipairs(ids) do idx = idx or byId[sid] end
    map[c] = idx or 9          -- forme inconnue : 9 n'existe pas -> [form:9] faux, [noform:9] vrai
    if map[c] ~= c then changed = true end
  end
  return changed and map or nil
end
M.FormMap = formMap

local function remapForms(body, map)
  if not map then return body end
  return (body:gsub("(form:)([%d/]+)", function(k, list)
    local out = {}
    for d in list:gmatch("%d+") do out[#out + 1] = tostring(map[tonumber(d)] or d) end
    return k .. tconcat(out, "/")
  end))
end
M.RemapForms = remapForms

local function renderVariant(v, ctx, consumable)
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
  if ctx.forms == nil then ctx.forms = formMap() or false end
  if ctx.forms then body = remapForms(body, ctx.forms) end
  if not M.ValidBody(body) then return nil end
  if body == "#showtooltip" then return nil end
  -- une macro de consommables sans aucun objet trouvé ne sert à rien (une macro de sorts garde ses sorts
  -- quand sa ligne d'objet optionnelle disparaît : TAI Familier sans nourriture garde Appel / Guérison)
  if consumable and (v.b or ""):find("{item:", 1, true) and not body:find("item:%d") then return nil end
  return body
end
M.RenderVariant = renderVariant

function M:Render(def, ctx)
  ctx = ctx or newContext()
  for _, v in ipairs(def.v or {}) do
    local body = renderVariant(v, ctx, def.c == "consumable")
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
-- Limites : Forever = Constants.MacroConsts (120 compte / 30 perso) ; Era = globales (120 / 18).
local function macroLimits()
  local K = _G.Constants and _G.Constants.MacroConsts
  local maxG = (type(K) == "table" and tonumber(K.MAX_ACCOUNT_MACROS)) or tonumber(_G.MAX_ACCOUNT_MACROS) or 120
  local maxC = (type(K) == "table" and tonumber(K.MAX_CHARACTER_MACROS)) or tonumber(_G.MAX_CHARACTER_MACROS) or 18
  return maxG, maxC
end
M.Limits = macroLimits

local function macroCounts()
  local g, c = try(_G.GetNumMacros)
  return tonumber(g) or 0, tonumber(c) or 0
end

local function macroName(idx)
  local n = try(_G.GetMacroInfo, idx)
  if type(n) == "string" then return n end
  return nil
end

-- Index d'une macro par son nom. GetMacroIndexByName n'est référencé nulle part dans l'UI Forever : s'il
-- manque, on parcourt GetMacroInfo (onglet compte puis personnage).
local function macroIndex(name)
  if type(name) ~= "string" then return nil end
  if type(_G.GetMacroIndexByName) == "function" then
    local i = tonumber((try(_G.GetMacroIndexByName, name)))
    if i and i > 0 then return i end
    if i then return nil end
  end
  local maxG = macroLimits()
  local g, c = macroCounts()
  for idx = 1, g do if macroName(idx) == name then return idx end end
  for idx = maxG + 1, maxG + c do if macroName(idx) == name then return idx end end
  return nil
end
M.MacroIndex = macroIndex

local function freeSlot(perChar)
  local g, c = macroCounts()
  local maxG, maxC = macroLimits()
  if perChar and c < maxC then return true, true end
  if g < maxG then return true, false end
  if c < maxC then return true, true end
  return false
end
M.FreeSlot = freeSlot

local function same(a, b) return type(a) == "string" and type(b) == "string" and trim(a) == trim(b) end

local function userDo(fn, ...)
  if T.UserAction then return T:UserAction(fn, ...) end
  return pcall(fn, ...)
end
M.UserDo = userDo

local function now() return (try(_G.GetTime)) or 0 end
local function stamp() return (try(_G.time)) or 0 end

-- Le jeu a refusé l'écriture (ADDON_ACTION_FORBIDDEN, ou CreateMacro qui ne crée rien) : mode
-- « copier-coller » (gardé d'une session à l'autre ; « Réessayer » dans la fenêtre le remet à zéro).
function M:SetBlocked(why)
  local d = M.db
  if type(d) == "table" then d.blocked = { t = stamp(), func = tostring(why or "?") } end
  M._blockedNow = true
end
function M:IsBlocked() return M._blockedNow == true or type(db().blocked) == "table" end
function M:ClearBlocked()
  if type(M.db) == "table" then M.db.blocked = nil end
  M._blockedNow = nil
end

local function onForbidden(_, _, addon, func)
  if addon ~= "TreeAI" then return end
  local f = tostring(func or "")
  local recent = M._lastWrite and (now() - M._lastWrite) < 3
  if f:find("Macro", 1, true) or (recent and f:find("UNKNOWN", 1, true)) then M:SetBlocked(f) end
end
M._OnForbidden = onForbidden

local function blockedCount()
  local root = T.db or _G.TreeAIDB
  local b = type(root) == "table" and type(root.diag) == "table" and root.diag.blocked
  return type(b) == "table" and #b or 0
end

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

local function isLimitError(err)
  local e = tostring(err or ""):lower()
  return e:find("limit", 1, true) or e:find("maximum", 1, true) or e:find("too many", 1, true)
end

-- write a macro; returns "created" | "updated" | "same" | "user" | "full" | "fail" | "pending" | "blocked"
function M:Write(def, body, force)
  if inCombat() then return "fail" end
  if not (_G.CreateMacro and _G.EditMacro) then self:SetBlocked("CreateMacro absent"); return "blocked" end
  -- WoW Forever bloque CreateMacro/EditMacro hors d'une action du joueur (« Action bloquée ») :
  -- sans clic / commande, on prépare et on propose « clique pour mettre à jour ».
  if T.InUserAction and not T:InUserAction() then
    M._pending = true
    suggestReady(char().setup and "Macros à mettre à jour : clique pour les mettre à jour" or READY_TEXT)
    return "pending"
  end
  local ch = char()
  local before = blockedCount()
  M._lastWrite = now()
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
    if same(cur, body) then ch.bodies[def.n] = cur; ch.installed[def.n] = true; return "same" end
    local ok, err = pcall(_G.EditMacro, idx, def.n, nil, body)
    if not ok then self.lastError = tostring(err); return "fail" end
    local _, _, after2 = try(_G.GetMacroInfo, idx)
    if not same(after2, body) or blockedCount() > before then self:SetBlocked("EditMacro"); return "blocked" end
    ch.bodies[def.n] = body
    ch.installed[def.n] = true
    return "updated"
  end
  local okSlot, perChar = freeSlot(db().perCharacter ~= false)
  if not okSlot then return "full" end
  local ok, ret = pcall(_G.CreateMacro, def.n, def.i or "INV_MISC_QUESTIONMARK", body, perChar)
  if not ok then
    if isLimitError(ret) then return "full" end
    -- icône refusée : une seule autre tentative avec l'ID de fichier du « ? » (rien n'a été créé)
    ok, ret = pcall(_G.CreateMacro, def.n, 134400, body, perChar)
    if not ok then
      self.lastError = tostring(ret)
      if isLimitError(ret) then return "full" end
      return "fail"
    end
  end
  ret = tonumber(ret)
  local newIdx = (ret and ret > 0 and macroName(ret) == def.n) and ret or macroIndex(def.n)
  if not newIdx or blockedCount() > before then
    -- appel accepté mais rien créé : le jeu l'a bloqué (ADDON_ACTION_FORBIDDEN) ou refusé en silence
    self:SetBlocked(blockedCount() > before and "CreateMacro (interdit)" or "CreateMacro (rien créé)")
    return "blocked"
  end
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

-- Macros du jeu par défaut (corps rendu), pour l'installation ou le copier-coller.
function M:PlanList(all, ctx)
  ctx = ctx or newContext()
  local out = {}
  for _, def in ipairs(self:Defs()) do
    if all or self:IsDefault(def) or char().installed[def.n] then
      local body = self:Render(def, ctx)
      if body then out[#out + 1] = { def = def, body = body } end
    end
  end
  return out
end

-- Installe le jeu par défaut de ta spé + contexte (+ celles déjà installées) ; all=true : toutes.
function M:Install(all, force)
  if inCombat() then say("En combat : refais-le après le combat (rien n'est créé en combat)."); return end
  self:ClearSpellCache()
  -- le jeu a déjà bloqué l'écriture : pas de nouvelle fenêtre « TreeAI a été bloqué », copier-coller direct
  if self:IsBlocked() and not force then
    if T.InUserAction and not T:InUserAction() then suggestReady(); return end
    self:ShowCopy(self:PlanList(all))
    return
  end
  runOOC("Macros:install", function()
    local ctx = newContext()
    local defs = self:Defs()
    local n, full, user, pend, blocked, fail, same0 = 0, 0, 0, 0, 0, 0, 0
    local failed = {}
    for _, p in ipairs(self:PlanList(all, ctx)) do
      local r = (blocked > 0) and "blocked" or self:Write(p.def, p.body, force)
      if r == "created" or r == "updated" then n = n + 1
      elseif r == "same" then same0 = same0 + 1
      elseif r == "full" then full = full + 1; failed[#failed + 1] = p
      elseif r == "user" then user = user + 1
      elseif r == "pending" then pend = pend + 1; break
      elseif r == "blocked" then blocked = blocked + 1; failed[#failed + 1] = p
      else fail = fail + 1; failed[#failed + 1] = p end
    end
    if pend > 0 then return end
    if self.Clear then self:Clear("ready") end
    if blocked > 0 then
      say("Le jeu bloque la création de macros par les addons : copie-les à la main (fenêtre ouverte).")
      suggestReady("Le jeu bloque la création auto : clique pour copier-coller les macros")
      self:ShowCopy(self:PlanList(all, ctx))
      self:Diagnose(false)
      return
    end
    if n + same0 > 0 then char().setup = true end
    local spec = self:Spec()
    say(("%d macro(s) créée(s)/mise(s) à jour, %d déjà à jour (%s, %s)."):format(n, same0, self:SpecName(spec),
      (self:Context())))
    if full > 0 then
      local maxG, maxC = macroLimits()
      local g, c = macroCounts()
      say(("%d macro(s) non créée(s) : plus de place (perso %d/%d, compte %d/%d). Supprime des macros inutiles (Échap > Macros) ou copie-les à la main."):format(full, c, maxC, g, maxG))
      M._copyList = failed
      suggest("ready", ("Plus de place pour %d macro(s) : clique pour les copier-coller"):format(full), 2,
        { ttl = 0, onClick = function() M:ShowCopy(M._copyList) end })
    elseif fail > 0 then
      say(("%d macro(s) refusée(s) par le jeu (%s) : bouton « Diagnostic »."):format(fail,
        tostring(self.lastError or "?")))
    end
    if user > 0 then
      say(("%d macro(s) modifiée(s) à la main gardée(s) telle(s) (Maj+clic sur « Installer » pour écraser)."):format(user))
    end
    if full == 0 and n > 0 then
      local hints = keyHints(defs)
      say("Pose-les sur ta barre : « Glisser sur la barre » (ou fais glisser une ligne de la liste)." ..
        (hints ~= "" and (" Touches conseillées : " .. hints) or ""))
      suggest("ready", ("%d macro(s) TAI prête(s) — clique pour les glisser sur ta barre"):format(n + same0), 1,
        { ttl = 300, onClick = function() M:OpenPanel() end })
    end
    self:Diagnose(false)
  end)
end

-- Installe / met à jour UNE macro (bouton du panneau). force : écrase une version modifiée.
-- Succès : la macro est mise sur le curseur (pose-la sur ta barre d'un clic).
function M:InstallOne(def, force)
  if inCombat() then say("Impossible en combat."); return "fail" end
  self:ClearSpellCache()
  local body = self:Render(def)
  if not body then
    local miss = self:Missing(def)
    say(def.n .. " : sort pas encore appris" .. (#miss > 0 and (" (" .. tconcat(miss, ", ") .. ")") or "") .. ".")
    return "later"
  end
  if self:IsBlocked() and not force then self:ShowCopy({ { def = def, body = body } }); return "blocked" end
  local r = self:Write(def, body, force)
  if r == "user" then say(def.n .. " : modifiée à la main, laissée telle quelle (Maj+clic pour écraser).")
  elseif r == "full" then
    local g, c = macroCounts()
    local maxG, maxC = macroLimits()
    say(("%s : plus de place (perso %d/%d, compte %d/%d)."):format(def.n, c, maxC, g, maxG))
    self:ShowCopy({ { def = def, body = body } })
  elseif r == "blocked" then
    say(def.n .. " : bloquée par le jeu — copie-la à la main (fenêtre ouverte).")
    self:ShowCopy({ { def = def, body = body } })
  elseif r == "fail" then say(def.n .. " : refusée par le jeu (" .. tostring(self.lastError or "?") .. ").")
  end
  if r == "created" or r == "updated" or r == "same" then
    char().setup = true
    local idx = macroIndex(def.n)
    if idx and _G.PickupMacro then try(_G.PickupMacro, idx) end
    say("|cff40ff40OK|r " .. def.n .. " " .. ((r == "created" and "créée") or (r == "updated" and "mise à jour") or "déjà à jour") ..
      " — elle est sur ton curseur : glisse-la sur ta barre (clic droit pour annuler).")
  end
  return r
end

-- Diagnostic : par macro OK / X + raison, écrit dans TreeAIDB.diag.macros (lu par l'app TreeAI / l'autotest).
local REASON = {
  installed = "installée", same = "installée", user = "modifiée par toi (gardée)", later = "sort pas encore appris",
  full = "emplacement plein", blocked = "bloqué par le jeu", ready = "prête, pas encore installée",
  invalid = "texte refusé",
}
function M:Diagnose(show)
  self:ClearSpellCache()
  local ctx = newContext()
  local g, c = macroCounts()
  local maxG, maxC = macroLimits()
  local hasFree = freeSlot(db().perCharacter ~= false)
  local blocked = self:IsBlocked()
  local items, okN, koN = {}, 0, 0
  for _, def in ipairs(self:Defs()) do
    if self:IsDefault(def) or char().installed[def.n] then
      local st, detail = self:Status(def, ctx)
      local body = self:Render(def, ctx)
      local code = st
      if st == "later" then
        local miss = self:Missing(def)
        detail = #miss > 0 and tconcat(miss, ", ") or nil
      elseif st == "ready" then
        local okB, why = M.ValidBody(body)
        if not okB then code, detail = "invalid", why
        elseif blocked then code = "blocked"
        elseif not hasFree then code = "full"; detail = ("perso %d/%d, compte %d/%d"):format(c, maxC, g, maxG) end
      else
        detail = nil
      end
      local ok = (code == "installed")
      if ok then okN = okN + 1 else koN = koN + 1 end
      items[#items + 1] = { n = def.n, ok = ok, status = code, reason = REASON[code] or code,
                            detail = detail and tostring(detail) or nil, len = body and #body or nil }
    end
  end
  local rep = {
    t = stamp(), ok = okN, ko = koN, items = items, mode = blocked and "copier-coller" or "auto",
    blocked = type(db().blocked) == "table" and db().blocked.func or (blocked and "?" or nil),
    slots = { account = g, accountMax = maxG, char = c, charMax = maxC },
    api = { create = _G.CreateMacro ~= nil, byName = _G.GetMacroIndexByName ~= nil,
            spellbook = (_G.C_SpellBook and _G.C_SpellBook.FindSpellBookSlotForSpell) ~= nil },
    lastError = self.lastError,
  }
  local root = T.db or _G.TreeAIDB
  if type(root) == "table" then
    root.diag = type(root.diag) == "table" and root.diag or {}
    root.diag.macros = rep
  end
  self.lastDiag = rep
  if show then
    -- affiché dans le panneau (OK / X + raison par macro), pas dans le chat
    say(("Diagnostic : %d OK, %d à voir (mode %s)."):format(okN, koN, rep.mode))
    self._view = "diag"
    self:OpenPanel()
  end
  return rep
end

---------------------------------------------------------------------------
-- Copier-coller : quand le jeu refuse la création (ou plus de place), chaque macro dans une zone de
-- texte sélectionnable (Ctrl+C), avec le nom, l'icône conseillée et les étapes.
---------------------------------------------------------------------------
local function mkButton(parent, text, w)
  local b = _G.CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  b:SetSize(w or 120, 20)
  b:SetText(text)
  return b
end

local function openMacroUI()
  if inCombat() then say("Impossible en combat."); return end
  if type(_G.ShowMacroFrame) == "function" and pcall(_G.ShowMacroFrame) then return end
  say("Tape /macro dans le chat (ou Échap > Macros).")
end
M.OpenMacroUI = openMacroUI

local function readOnlyBox(box)
  box:SetAutoFocus(false)
  box:SetScript("OnEscapePressed", function(b) b:ClearFocus() end)
  box:SetScript("OnEditFocusGained", function(b) b:HighlightText() end)
  box:SetScript("OnTextChanged", function(b, userInput)
    if userInput and b._orig and b:GetText() ~= b._orig then b:SetText(b._orig); b:HighlightText() end
  end)
end

function M:ShowCopy(list)
  if not _G.CreateFrame then return end
  list = (type(list) == "table" and #list > 0) and list or self:PlanList(false)
  self._copyList = list
  self._copyIdx = 1
  local f = self.copy
  if not f then
    f = _G.CreateFrame("Frame", nil, _G.UIParent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
    f:SetSize(560, 400)
    f:SetPoint("CENTER", 0, 40)
    f:SetFrameStrata("DIALOG")
    if f.SetClampedToScreen then f:SetClampedToScreen(true) end
    f:SetMovable(true); f:EnableMouse(true); f:RegisterForDrag("LeftButton")
    f:SetScript("OnDragStart", f.StartMoving); f:SetScript("OnDragStop", f.StopMovingOrSizing)
    if f.SetBackdrop then
      f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
        insets = { left = 3, right = 3, top = 3, bottom = 3 } })
      f:SetBackdropColor(0, 0, 0, 0.92)
    end
    f.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    f.title:SetPoint("TOPLEFT", 12, -10)
    if T.Theme then pcall(T.Theme.Skin, T.Theme, f, "window"); pcall(T.Theme.Font, T.Theme, f.title, "M", "gold") end
    local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", 2, 2)
    f.steps = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    f.steps:SetPoint("TOPLEFT", 12, -30); f.steps:SetWidth(536); f.steps:SetJustifyH("LEFT")
    f.steps:SetText("1. « Ouvrir les macros » (ou Échap > Macros, ou /macro) > onglet de ton perso > « Nouveau ».\n" ..
      "2. Nom : clique dans la case Nom ci-dessous, Ctrl+C, puis Ctrl+V dans le jeu ; icône « ? » ; OK.\n" ..
      "3. Clique dans le texte ci-dessous (tout se sélectionne), Ctrl+C, puis Ctrl+V dans la macro.\n" ..
      "4. Glisse l'icône de la macro sur ta barre. « Suivante » pour la macro d'après.")
    local nameL = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    nameL:SetPoint("TOPLEFT", 12, -100); nameL:SetText("Nom :")
    f.name = _G.CreateFrame("EditBox", nil, f, "InputBoxTemplate")
    f.name:SetSize(180, 20); f.name:SetPoint("LEFT", nameL, "RIGHT", 10, 0)
    readOnlyBox(f.name)
    f.icon = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    f.icon:SetPoint("LEFT", f.name, "RIGHT", 12, 0); f.icon:SetWidth(300); f.icon:SetJustifyH("LEFT")
    local box = _G.CreateFrame("Frame", nil, f, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
    box:SetPoint("TOPLEFT", 10, -126); box:SetSize(540, 180)
    if box.SetBackdrop then
      box:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background" })
      box:SetBackdropColor(0.08, 0.08, 0.1, 0.95)
    end
    local sf = _G.CreateFrame("ScrollFrame", nil, box, "UIPanelScrollFrameTemplate")
    sf:SetPoint("TOPLEFT", 6, -6); sf:SetPoint("BOTTOMRIGHT", -26, 6)
    f.body = _G.CreateFrame("EditBox", nil, sf)
    f.body:SetMultiLine(true); f.body:SetFontObject(_G.ChatFontNormal or "GameFontHighlight")
    f.body:SetWidth(500); f.body:SetHeight(170)
    sf:SetScrollChild(f.body)
    readOnlyBox(f.body)
    f.count = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    f.count:SetPoint("TOPLEFT", 12, -312); f.count:SetWidth(536); f.count:SetJustifyH("LEFT")
    f.sel = mkButton(f, "Sélectionner le texte", 160); f.sel:SetPoint("BOTTOMLEFT", 12, 40)
    f.sel:SetScript("OnClick", function() f.body:SetFocus(); f.body:HighlightText() end)
    f.open = mkButton(f, "Ouvrir les macros", 150); f.open:SetPoint("LEFT", f.sel, "RIGHT", 6, 0)
    f.open:SetScript("OnClick", function() openMacroUI() end)
    f.prev = mkButton(f, "< Précédente", 100); f.prev:SetPoint("LEFT", f.open, "RIGHT", 6, 0)
    f.prev:SetScript("OnClick", function() M._copyIdx = math.max(1, (M._copyIdx or 1) - 1); M:FillCopy() end)
    f.nextB = mkButton(f, "Suivante >", 100); f.nextB:SetPoint("LEFT", f.prev, "RIGHT", 6, 0)
    f.nextB:SetScript("OnClick", function()
      M._copyIdx = math.min(#(M._copyList or {}), (M._copyIdx or 1) + 1); M:FillCopy()
    end)
    f.retry = mkButton(f, "Réessayer la création auto", 190); f.retry:SetPoint("BOTTOMLEFT", 12, 12)
    f.retry:SetScript("OnClick", function()
      M:ClearBlocked(); f:Hide(); userDo(M.Install, M, false)
    end)
    f.diag = mkButton(f, "Diagnostic macros", 150); f.diag:SetPoint("LEFT", f.retry, "RIGHT", 6, 0)
    f.diag:SetScript("OnClick", function() M:Diagnose(true) end)
    self.copy = f
  end
  self:FillCopy()
  f:Show()
end

function M:FillCopy()
  local f = self.copy
  if not f then return end
  local list = self._copyList or {}
  local i = math.max(1, math.min(self._copyIdx or 1, #list))
  self._copyIdx = i
  local p = list[i]
  f.title:SetText(self:IsBlocked() and "Macros TreeAI — à copier-coller (le jeu bloque la création auto)"
    or "Macros TreeAI — à copier-coller")
  if not p then
    f.name._orig = ""; f.name:SetText("")
    f.body._orig = ""; f.body:SetText("")
    f.count:SetText("Aucune macro disponible pour l'instant (sorts pas encore appris).")
    return
  end
  f.name._orig = p.def.n; f.name:SetText(p.def.n)
  local ic = p.def.i or "INV_Misc_QuestionMark"
  f.icon:SetText("Icône : " .. ((ic:lower() == "inv_misc_questionmark") and "« ? » (l'icône suit le sort)" or ic))
  f.body._orig = p.body; f.body:SetText(p.body)
  f.count:SetText(("Macro %d / %d — %s — %d / 255 caractères%s"):format(i, #list, p.def.d or "", #p.body,
    p.def.k and p.def.k ~= "-" and (" — touche conseillée : " .. p.def.k) or ""))
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
        local idx = body and macroIndex(def.n)
        local _, _, cur = try(_G.GetMacroInfo, idx)
        -- rien à changer : pas d'écriture (donc pas de « clique pour mettre à jour » à chaque sac)
        if body and not same(cur, body) and not self:IsBlocked() then
          local r = self:Write(def, body, false)
          if r == "pending" or r == "blocked" then
            if r == "blocked" then suggestReady("Le jeu bloque la mise à jour auto : clique pour copier-coller") end
            return
          end
        end
      end
    end
    if self.panel and self.panel:IsShown() then self:FillPanel() end
  end)
end

function M:Remove()
  -- DeleteMacro : seulement pendant l'action du joueur et hors combat (sinon ADDON_ACTION_FORBIDDEN sur
  -- Forever) : pas de file d'attente « après le combat », qui s'exécuterait hors de la commande.
  if inCombat() or (T.InUserAction and not T:InUserAction()) then
    say("Suppression des macros : refais la commande hors combat.")
    return
  end
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
-- Panneau (TIDY) : liste groupée par état à gauche (icône, nom, touche · niveau), fiche à droite empilée
-- par un seul moteur (icône + nom, « À quoi ça sert », « Où la mettre », texte, « Pourquoi »), grille de
-- boutons de même largeur en bas, ligne d'état à gauche. Diagnostic : la fiche devient la liste OK / X.
---------------------------------------------------------------------------
local W, H, LIST_W, ROW_H, ROWS = 720, 486, 282, 19, 21
local PAD, GAP, BTN_H = 10, 6, 22
local STATUS_COLOR = { installed = "|cff40ff40", user = "|cffffa040", ready = "|cffffffff", later = "|cff808080" }
local GROUP_TXT = { installed = "Installées", user = "Installées", ready = "Prêtes à installer", later = "Plus tard" }
local OK_ICON = "|TInterface\\RaidFrame\\ReadyCheck-Ready:14|t"
local KO_ICON = "|TInterface\\RaidFrame\\ReadyCheck-NotReady:14|t"
local QM = "Interface\\Icons\\INV_Misc_QuestionMark"

local function button(parent, text, w)
  local b = _G.CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  b:SetSize(w or 120, BTN_H)
  b:SetText(text)
  return b
end

local function tip(b, title, lines)
  if not (b and b.SetScript) then return end
  b:SetScript("OnEnter", function(self)
    local tt = _G.GameTooltip
    if not tt then return end
    tt:SetOwner(self, "ANCHOR_TOP")
    tt:SetText(title)
    for _, l in ipairs(lines or {}) do tt:AddLine(l, 1, 1, 1, true) end
    tt:Show()
  end)
  b:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
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

-- icône d'une macro : 1er sort de la variante rendue, sinon 1er objet, sinon l'icône conseillée
local function iconOf(def, ctx)
  for _, v in ipairs(def.v or {}) do
    local b = v.b or ""
    local sid = tonumber(b:match("{(%d+)}"))
    if sid then
      local C = _G.C_Spell
      local ic = (C and C.GetSpellTexture and try(C.GetSpellTexture, sid)) or try(_G.GetSpellTexture, sid)
      if ic then return ic end
    end
    local cat = b:match("{item:([%a_]+)}")
    local id = cat and ctx and ctx.item(cat)
    if id then
      local CI = _G.C_Item
      local ic = (CI and CI.GetItemIconByID and try(CI.GetItemIconByID, id)) or try(_G.GetItemIcon, id)
      if ic then return ic end
    end
  end
  if def.i and def.i:lower() ~= "inv_misc_questionmark" then return "Interface\\Icons\\" .. def.i end
  return QM
end
M.IconOf = iconOf

-- la macro est-elle déjà posée sur une barre ? (GetActionInfo 1..120, lu seulement panneau ouvert)
local function onBars(idx)
  if not idx or type(_G.GetActionInfo) ~= "function" then return false end
  for slot = 1, 120 do
    local t, id = try(_G.GetActionInfo, slot)
    if t == "macro" and id == idx then return true end
  end
  return false
end
M.OnBars = onBars

-- prochaine macro TAI installée qui n'est sur aucune barre (flux « Glisser sur la barre »)
function M:NextToPlace()
  local sel = self:SelectedDef()
  local selIdx = sel and macroIndex(sel.n)
  if selIdx and not onBars(selIdx) then return sel, selIdx end
  for _, def in ipairs(self:Defs()) do
    local idx = char().installed[def.n] and macroIndex(def.n)
    if idx and not onBars(idx) then return def, idx end
  end
  return nil
end

-- « Glisser sur la barre » : installe si besoin, puis met la macro sur le curseur (pose-la d'un clic)
function M:PickToBar(def)
  if inCombat() then say("Impossible en combat : refais-le après."); return end
  def = def or select(1, self:NextToPlace()) or self:SelectedDef()
  if not def then say("Toutes tes macros TAI sont déjà sur tes barres."); return end
  local idx = macroIndex(def.n)
  if not idx then
    userDo(M.InstallOne, M, def, false)
    return
  end
  try(_G.PickupMacro, idx)
  say(def.n .. " est sur ton curseur : clique sur une case de ta barre pour la poser (clic droit : annuler).")
end

local function strH(fs)
  if not (fs and fs.GetStringHeight) then return nil end
  return tonumber((try(fs.GetStringHeight, fs)))
end

-- empileur vertical (TIDY règle 2/5) : chaque élément sous le précédent, hauteur mesurée
local function stack(f, items, x, top, width)
  local y = top
  f._rects = {}
  for _, it in ipairs(items) do
    local w = it.frame
    if w and (not w.IsShown or w:IsShown()) then
      w:ClearAllPoints()
      w:SetPoint("TOPLEFT", f, "TOPLEFT", x + (it.dx or 0), -y)
      local h = it.h
      if not h and w.GetStringHeight then
        w:SetWidth(width - (it.dx or 0))
        h = math.max(it.min or 12, strH(w) or 12)
      end
      h = h or 14
      f._rects[#f._rects + 1] = { y = y, h = h, name = it.name }
      y = y + h + (it.gap or GAP)
    end
  end
  return y
end

function M:TogglePanel()
  if self.panel then
    if self.panel:IsShown() then self.panel:Hide() else self:FillPanel(); self.panel:Show() end
    return
  end
  if not _G.CreateFrame then return end
  local f = _G.CreateFrame("Frame", nil, _G.UIParent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if f.SetClampedToScreen then f:SetClampedToScreen(true) end
  f:SetSize(W, H)
  f:SetPoint("CENTER")
  f:SetFrameStrata("DIALOG")
  f:SetMovable(true); f:EnableMouse(true); f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving); f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.SetBackdrop then
    f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    f:SetBackdropColor(0, 0, 0, 0.92)
  end
  local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", PAD, -10)
  title:SetText("Macros TreeAI")
  if T.Theme then T.Theme:Skin(f, "window"); T.Theme:Font(title, "M", "gold") end  -- thème commun
  local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 2, 2)
  if T.Theme and T.Theme.Window then pcall(T.Theme.Window, T.Theme, f, { id = "macros", header = false }) end

  f.specBtn = button(f, "Spé", 190); f.specBtn:SetPoint("TOPLEFT", 130, -6)
  f.specBtn:SetScript("OnClick", function() cycleSpec(); M:FillPanel() end)
  tip(f.specBtn, "Spécialisation", { "Clic : choisir la spé des macros (auto = d'après tes talents)." })
  f.ctxBtn = button(f, "Contexte", 140); f.ctxBtn:SetPoint("LEFT", f.specBtn, "RIGHT", GAP, 0)
  f.ctxBtn:SetScript("OnClick", function() cycleCtx(); M:FillPanel() end)
  tip(f.ctxBtn, "Solo / groupe", { "Le jeu conseillé change un peu en groupe (provocation, soins au survol…)." })

  -- liste (gauche) : en-têtes de groupe + lignes (icône, nom, touche · niveau) ; molette = défiler
  local list = _G.CreateFrame("Frame", nil, f, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  list:SetPoint("TOPLEFT", PAD, -34); list:SetSize(LIST_W, ROWS * ROW_H + 6)
  if list.SetBackdrop then
    list:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background" })
    list:SetBackdropColor(0.06, 0.06, 0.08, 0.9)
  end
  f.list = list
  f.offset = 0
  f.rows = {}
  for i = 1, ROWS do
    local row = _G.CreateFrame("Button", nil, list)
    row:SetSize(LIST_W - 8, ROW_H - 1)
    row:SetPoint("TOPLEFT", 4, -3 - (i - 1) * ROW_H)
    row.icon = row:CreateTexture(nil, "ARTWORK")
    row.icon:SetSize(16, 16); row.icon:SetPoint("LEFT", 2, 0)
    row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    row.text:SetPoint("LEFT", 22, 0); row.text:SetWidth(LIST_W - 100); row.text:SetJustifyH("LEFT")
    row.right = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    row.right:SetPoint("RIGHT", -4, 0); row.right:SetWidth(70); row.right:SetJustifyH("RIGHT")
    row.hl = row:CreateTexture(nil, "BACKGROUND")
    row.hl:SetAllPoints()
    if row.hl.SetColorTexture then row.hl:SetColorTexture(0.2, 0.6, 0.3, 0.35)
    else row.hl:SetTexture("Interface\\Buttons\\WHITE8X8"); row.hl:SetVertexColor(0.2, 0.6, 0.3, 0.35) end
    row.hl:Hide()
    row:SetScript("OnClick", function(b)
      if b.def then M.selected = b.def.n; M._view = nil; M:FillPanel() end
    end)
    if row.RegisterForDrag then row:RegisterForDrag("LeftButton") end
    row:SetScript("OnDragStart", function(b) if b.def then M.selected = b.def.n; M:PickToBar(b.def); M:FillPanel() end end)
    f.rows[i] = row
  end
  f:EnableMouseWheel(true)
  f:SetScript("OnMouseWheel", function(_, delta)
    f.offset = math.max(0, (f.offset or 0) - delta * 3)
    M:FillPanel()
  end)
  f.status = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.status:SetPoint("TOPLEFT", list, "BOTTOMLEFT", 2, -8); f.status:SetWidth(LIST_W - 4)
  f.status:SetJustifyH("LEFT"); f.status:SetJustifyV("TOP")
  f.status:SetText(M.lastMsg or "Rien n'est créé sans ton clic ; une macro modifiée à la main n'est jamais écrasée.")

  -- fiche (droite) : éléments empilés par stack() dans FillPanel
  local x = PAD + LIST_W + 14
  local cw = W - x - PAD
  f.x, f.cw = x, cw
  f.icon = f:CreateTexture(nil, "ARTWORK"); f.icon:SetSize(36, 36)
  f.name = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge"); f.name:SetJustifyH("LEFT")
  f.desc = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight"); f.desc:SetJustifyH("LEFT")
  f.where = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall"); f.where:SetJustifyH("LEFT")
  f.meta = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall"); f.meta:SetJustifyH("LEFT")
  f.box = _G.CreateFrame("Frame", nil, f, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  if f.box.SetBackdrop then
    f.box:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background" })
    f.box:SetBackdropColor(0.08, 0.08, 0.1, 0.95)
  end
  f.body = f.box:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.body:SetPoint("TOPLEFT", 6, -6); f.body:SetWidth(cw - 12)
  f.body:SetJustifyH("LEFT"); f.body:SetJustifyV("TOP")
  f.whyT = f:CreateFontString(nil, "OVERLAY", "GameFontNormal"); f.whyT:SetText("Pourquoi elle est faite ainsi")
  f.why = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  f.why:SetJustifyH("LEFT"); f.why:SetJustifyV("TOP")

  -- grille de boutons (TIDY règle 6) : 3 colonnes de même largeur, 3 rangées, le dangereux en dernier
  local bw = math.floor((cw - 2 * GAP) / 3)
  local function cell(b, col, rowN)
    b:SetWidth(bw)
    b:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", x + (col - 1) * (bw + GAP), PAD + (3 - rowN) * (BTN_H + GAP))
  end
  f.all = button(f, "Installer tout"); cell(f.all, 1, 1)
  f.all:SetScript("OnClick", function() M._view = nil; userDo(M.Install, M, false); after(0.3, function() M:FillPanel() end) end)
  tip(f.all, "Installer tout", { "Crée ou met à jour le jeu conseillé pour ta spé et ton contexte.",
    "Les « options » s'installent une par une." })
  f.one = button(f, "Installer"); cell(f.one, 2, 1)
  f.one:SetScript("OnClick", function()
    local def = M:SelectedDef()
    if def then
      local force = _G.IsShiftKeyDown and _G.IsShiftKeyDown()
      M._view = nil
      userDo(M.InstallOne, M, def, force)
      M:FillPanel()
    end
  end)
  tip(f.one, "Installer cette macro", { "Elle arrive sur ton curseur : clique une case de ta barre.",
    "Maj+clic : écrase ta version modifiée." })
  f.pick = button(f, "Glisser sur la barre"); cell(f.pick, 3, 1)
  f.pick:SetScript("OnClick", function() M:PickToBar(); M:FillPanel() end)
  tip(f.pick, "Glisser sur la barre", { "Met sur ton curseur la prochaine macro TAI qui n'est sur aucune barre.",
    "Astuce : tu peux aussi faire glisser une ligne de la liste." })
  f.upd = button(f, "Mettre à jour"); cell(f.upd, 1, 2)
  f.upd:SetScript("OnClick", function() userDo(M.Refresh, M, false); after(0.3, function() M:FillPanel() end) end)
  tip(f.upd, "Mettre à jour", { "Réécrit les macros installées (nouveaux sorts, meilleurs objets du sac)." })
  f.diag = button(f, "Diagnostic"); cell(f.diag, 2, 2)
  f.diag:SetScript("OnClick", function()
    if M._view == "diag" then M._view = nil; M:FillPanel() else M:Diagnose(true) end
  end)
  tip(f.diag, "Diagnostic macros", { "Chaque macro : installée ou non, et pourquoi." })
  f.copyB = button(f, "Copier-coller"); cell(f.copyB, 3, 2)
  f.copyB:SetScript("OnClick", function() M:ShowCopy(M:PlanList(false)) end)
  tip(f.copyB, "Copier-coller", { "Si le jeu bloque la création automatique : le texte de chaque macro à copier." })
  f.openB = button(f, "Ouvrir les macros"); cell(f.openB, 1, 3)
  f.openB:SetScript("OnClick", function() openMacroUI() end)
  f.del = button(f, "Retirer les macros TAI"); cell(f.del, 2, 3)
  f.del:SetWidth(2 * bw + GAP)
  f.del:SetScript("OnClick", function() userDo(M.Remove, M); after(0.3, function() M:FillPanel() end) end)
  tip(f.del, "Retirer", { "Supprime les macros TAI non modifiées (les tiennes ne sont jamais touchées)." })
  f.contentBottom = PAD + 3 * (BTN_H + GAP) + 4   -- la fiche s'arrête au-dessus de la grille
  self.panel = f
  self:FillPanel()
end

function M:OpenPanel()
  if self.panel and self.panel:IsShown() then self:FillPanel() return end
  if self.panel then self:FillPanel(); self.panel:Show() return end
  self:TogglePanel()
end

function M:SelectedDef()
  for _, def in ipairs(self:Defs()) do
    if def.n == self.selected then return def end
  end
  return nil
end

-- lignes de la liste : en-tête par groupe (Installées / Prêtes / Plus tard), triées par niveau
function M:PanelRows(ctx)
  local groups = { installed = {}, ready = {}, later = {} }
  for _, def in ipairs(self:Defs()) do
    local st = self:Status(def, ctx)
    local g = (st == "user") and "installed" or st
    local list = groups[g] or groups.later
    list[#list + 1] = { def = def, st = st }
  end
  local out = {}
  for _, g in ipairs({ "installed", "ready", "later" }) do
    local list = groups[g]
    if #list > 0 then
      tsort(list, function(a, b)
        local da, db_ = self:IsDefault(a.def), self:IsDefault(b.def)
        if da ~= db_ then return da end
        if (a.def.l or 1) ~= (b.def.l or 1) then return (a.def.l or 1) < (b.def.l or 1) end
        return a.def.n < b.def.n
      end)
      out[#out + 1] = { header = ("%s (%d)"):format(GROUP_TXT[g], #list) }
      for _, r in ipairs(list) do out[#out + 1] = r end
    end
  end
  return out
end

local function fillDiag(f, rep)
  local lines = {}
  for _, it in ipairs(rep.items or {}) do
    lines[#lines + 1] = ("%s %s : %s%s"):format(it.ok and OK_ICON or KO_ICON, it.n:sub(#PREFIX + 1), it.reason,
      it.detail and (" (" .. it.detail .. ")") or "")
  end
  f.icon:SetTexture("Interface\\RaidFrame\\ReadyCheck-Ready")
  f.name:SetText("Diagnostic macros")
  f.desc:SetText(("%d installée(s), %d à voir — mode %s."):format(rep.ok or 0, rep.ko or 0, rep.mode or "auto"))
  local s = rep.slots or {}
  f.where:SetText(("Emplacements : perso %d/%d, compte %d/%d."):format(s.char or 0, s.charMax or 0,
    s.account or 0, s.accountMax or 0))
  f.meta:SetText(rep.lastError and ("Dernière erreur du jeu : " .. tostring(rep.lastError)) or "")
  f.body:SetText(#lines > 0 and tconcat(lines, "\n") or "Aucune macro conseillée pour l'instant.")
  f.whyT:SetText("Que faire")
  f.why:SetText((rep.mode == "copier-coller")
    and "Le jeu bloque la création automatique : « Copier-coller » donne le texte de chaque macro."
    or "« Installer tout » crée celles qui manquent ; « pas encore appris » : elle arrivera avec ton prochain sort.")
end

function M:FillPanel()
  local f = self.panel
  if not f then return end
  local ctx = newContext()
  local spec, autoSpec = self:Spec()
  local where, autoCtx = self:Context()
  f.specBtn:SetText("Spé : " .. self:SpecName(spec) .. (autoSpec and " (auto)" or ""))
  f.ctxBtn:SetText((where == "groupe" and "Groupe" or "Solo") .. (autoCtx and " (auto)" or ""))
  local rows = self:PanelRows(ctx)
  f.offset = math.min(f.offset or 0, math.max(0, #rows - ROWS))
  if not self:SelectedDef() then
    self.selected = nil
    for _, r in ipairs(rows) do if r.def then self.selected = r.def.n; break end end
  end
  for i = 1, ROWS do
    local row, r = f.rows[i], rows[i + f.offset]
    if r and r.header then
      row.def = nil
      row.icon:Hide()
      row.text:SetText("|cffffd100" .. r.header .. "|r")
      row.right:SetText("")
      row.hl:Hide()
      row:Show()
    elseif r then
      local def = r.def
      row.def = def
      row.icon:SetTexture(iconOf(def, ctx)); row.icon:Show()
      local opt = self:IsDefault(def) and "" or " |cff808080(option)|r"
      row.text:SetText(("%s%s|r%s"):format(STATUS_COLOR[r.st] or "", def.n:sub(#PREFIX + 1), opt))
      local key = (def.k and def.k ~= "-") and def.k or ""
      row.right:SetText((r.st == "later" and def.l and def.l <= 60) and ("niv. " .. def.l) or key)
      if def.n == self.selected then row.hl:Show() else row.hl:Hide() end
      row:Show()
    else
      row.def = nil
      row:Hide()
    end
  end
  f.status:SetText(self.lastMsg or "Rien n'est créé sans ton clic ; une macro modifiée à la main n'est jamais écrasée.")

  if self._view == "diag" and self.lastDiag then
    fillDiag(f, self.lastDiag)
  else
    local def = self:SelectedDef()
    if not def then return end
    local st, cur = self:Status(def, ctx)
    local body = self:Render(def, ctx)
    f.icon:SetTexture(iconOf(def, ctx))
    f.name:SetText(def.n)
    f.desc:SetText(def.d or "")
    f.where:SetText("|cffffd100Où la mettre :|r " .. (def.o or ""))
    local ctxTxt = (def.x == "groupe" and "groupe") or (def.x == "solo" and "solo") or "solo + groupe"
    local idx = (st == "installed" or st == "user") and macroIndex(def.n)
    local placed = idx and (onBars(idx) and " · sur ta barre" or " · pas encore sur une barre") or ""
    f.meta:SetText(("%s%s|r%s · niveau %s · %s · touche conseillée %s"):format(STATUS_COLOR[st] or "",
      STATUS_TXT[st] or "", placed, tostring(def.l or 1), ctxTxt, def.k or "-"))
    if st == "user" and cur then
      f.body:SetText("|cffffa040Ta version (gardée) :|r\n" .. cur .. (body and ("\n|cff40ff40TreeAI propose :|r\n" .. body) or ""))
    elseif body then
      f.body:SetText(body .. ("\n|cff808080%d / 255 caractères|r"):format(#body))
    else
      local miss = self:Missing(def)
      f.body:SetText("|cff808080Pas encore disponible" .. (#miss > 0 and (" : il te manque " .. tconcat(miss, ", ")) or "") .. ".|r")
    end
    f.whyT:SetText("Pourquoi elle est faite ainsi")
    f.why:SetText(def.w or "")
    f.one:SetText((st == "installed" and "Mettre à jour") or (st == "user" and "Maj+clic : écraser") or "Installer")
  end
  -- empilement de la fiche (hauteurs mesurées), la boîte de texte prend la place restante utile
  f.box:SetWidth(f.cw)
  local bodyH = math.max(40, math.min(170, (strH(f.body) or 60) + 14))
  if self._view == "diag" then bodyH = math.max(bodyH, 200) end
  f.box:SetHeight(bodyH)
  local top = 36
  f.icon:ClearAllPoints(); f.icon:SetPoint("TOPLEFT", f, "TOPLEFT", f.x, -top)
  local function lay(h)
    return stack(f, {
      { frame = f.name, name = "name", dx = 44, gap = 2 },
      { frame = f.desc, name = "desc", dx = 44, min = 22 },
      { frame = f.where, name = "where" },
      { frame = f.meta, name = "meta" },
      { frame = f.box, name = "box", h = h, gap = 10 },
      { frame = f.whyT, name = "whyT", gap = 2 },
      { frame = f.why, name = "why" },
    }, f.x, top, f.cw)
  end
  local bottom = lay(bodyH)
  local over = bottom - (H - f.contentBottom)
  if over > 0 then   -- jamais sous la grille de boutons : la boîte de texte rétrécit
    bodyH = math.max(40, bodyH - over)
    f.box:SetHeight(bodyH)
    lay(bodyH)
  end
  f.box:SetWidth(f.cw)
end
M.Stack = stack

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
  resolveCache, bookNames = {}, nil
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
      suggestReady(READY_TEXT, fresh)
    end
  end)
end

function M:OnEnable()
  -- (LITE) profil essentiel : le panneau Macros seulement, pas de relecture des sacs / de l'équipement en fond
  if not (T.Lite and T:Lite()) then
    self:RegisterEvent("BAG_UPDATE_DELAYED", scheduleConsumables)
    self:RegisterEvent("UNIT_INVENTORY_CHANGED", function(_, _, unit) if unit == nil or unit == "player" then scheduleConsumables() end end)
    self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", scheduleConsumables)
    self:RegisterEvent("UNIT_PET", function(_, _, unit) if unit == nil or unit == "player" then scheduleConsumables() end end)
  end
  self:RegisterEvent("PLAYER_LEVEL_UP", function() scheduleConsumables(); scheduleSpells() end)
  self:RegisterEvent("SKILL_LINES_CHANGED", scheduleConsumables)
  self:RegisterEvent("SPELLS_CHANGED", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_TAB", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_SKILL_LINE", scheduleSpells)   -- nom Forever / Mainline
  self:RegisterEvent("ACTIVE_TALENT_GROUP_CHANGED", scheduleSpells)
  self:RegisterEvent("CHARACTER_POINTS_CHANGED", scheduleSpells)
  self:RegisterEvent("ADDON_ACTION_FORBIDDEN", M._OnForbidden)   -- le jeu a refusé CreateMacro / EditMacro
  if not data() then return end
  if self:IsBlocked() and not char().setup then
    suggestReady("Macros prêtes à copier-coller : clique pour les voir")
  elseif char().setup then
    after(3, function() M:Refresh(false) end)
  else
    suggestReady(READY_TEXT)
  end
end

---------------------------------------------------------------------------
-- Slash : /tai macros [installer|tout|maj|forcer|liste|retirer|diag|copier|spe|solo|groupe|auto]
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
  elseif cmd == "diag" or cmd == "diagnostic" then M:Diagnose(true)
  elseif cmd == "copier" or cmd == "copie" or cmd == "copy" then M:ShowCopy(M:PlanList(cmd == "tout"))
  elseif cmd == "auto" then char().ctx, char().spec = nil, nil; say("Spé et contexte automatiques.")
  else M:TogglePanel() end
end
M.Slash = slash
if T.RegisterSlash then
  T:RegisterSlash("macros", slash,
    "macros [installer|tout|maj|forcer|liste|retirer|diag|copier|spe|solo|groupe|auto] : macros expertes")
end
