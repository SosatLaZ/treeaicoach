-- TreeAI — Modules/Macros.lua : gestionnaire de macros intelligentes (9 classes).
-- Données : Data/Macros.lua (généré par tools/wow_build_macros.py).
-- Règles Blizzard : création/édition UNIQUEMENT hors combat (InCombatLockdown), aucune
-- automatisation : une macro = une capacité par appui, le joueur appuie lui-même.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
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

local itemSpellMemo = {}   -- (4.0.1) le sort d'un objet ne change pas : lu une fois (réponse non vide seulement)
local function itemSpell(id)
  local c = id and itemSpellMemo[id]
  if c then return c end
  local C = _G.C_Item
  local r = (try((C and C.GetItemSpell) or _G.GetItemSpell, id))
  if r and id then itemSpellMemo[id] = r end
  return r
end

local function eachBagItem(fn)
  local A = API()
  if A.IterBags then
    for _, _, item in (A.IterBagsShared or A.IterBags)() do   -- (4.0.1) relevé partagé dans l'image
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
local function now() return (try(_G.GetTime)) or 0 end
local function stamp() return (try(_G.time)) or 0 end

---------------------------------------------------------------------------
-- Module
---------------------------------------------------------------------------
local M = T:RegisterModule("Macros", {
  title = "Macros",
  defaults = { enabled = true, autoConsumables = true, perCharacter = true, autoUpgrade = true },
  -- (MACRO3) opts[nom] = réglages de la macro (on, mods, mo, pet, guard, auto, var, tank, name, icon) ;
  -- preset = « simple » | « complet » | « pro » ; stamps[nom] = heure (time()) de la dernière écriture par TreeAI
  charDefaults = { setup = false, installed = {}, bodies = {}, opts = {}, stamps = {} },
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
local charOK   -- (MACRO3, perf) la table déjà vérifiée : char() est appelé à chaque tour des boucles de rafraîchissement
local function char()
  local c = M.char
  if c ~= nil and c == charOK and c.installed and c.bodies and c.opts and c.stamps then return c end
  if type(c) ~= "table" then c = {}; M.char = c end
  c.installed = c.installed or {}
  c.bodies = c.bodies or {}
  if type(c.opts) ~= "table" then c.opts = {} end
  -- un réglage par macro = une table (rien d'autre ne passe les lectures rapides de M:Opt / M:IsOn / M:Opts)
  for k, o in pairs(c.opts) do if type(o) ~= "table" then c.opts[k] = nil end end
  if type(c.stamps) ~= "table" then c.stamps = {} end
  charOK = c
  return c
end
M._Char = char

local function suggest(id, text, prio, opts)
  -- (4.1 LAYOUT) avis non demandé : seulement sur la page Macros de la fenêtre (pastille « Personnage »), jamais à l'écran
  opts = opts or {}
  if opts.ownPage == nil then opts.ownPage = "macros" end
  if M.Suggest then return M:Suggest(id, text, prio, opts) end
  if T.Suggest then return T:Suggest("Macros:" .. id, text, prio, opts) end
end

-- UNE seule ligne « macros » dans les conseils (installer, nouvelles, à mettre à jour : même id)
local READY_TEXT = "Nouvelles macros prêtes : clique pour installer"
local function suggestReady(text, names)
  local tip = { "Macros TreeAI", "Clic : tout mettre à jour · Maj-clic : la page Macros (état de chaque macro)" }
  if names and #names > 0 then tip[#tip + 1] = tconcat(names, ", ") end
  suggest("ready", text or READY_TEXT, 2, { ttl = 0, tooltip = tip, onClick = function()
    if inCombat() then return end   -- macros impossibles à créer en combat : la ligne reste, le clic d'après marche
    if _G.IsShiftKeyDown and _G.IsShiftKeyDown() then M:TogglePanel() return end
    M._pending = false
    if M.Clear then M:Clear("ready") end
    -- (MACRO3) un clic = « Tout mettre à jour » : installe les macros activées qui manquent, réécrit celles qui ont changé
    if M:IsBlocked() then M:ShowCopy(M:PlanList(false))
    elseif not char().setup then M:Install(false) else M:UpdateAll() end
  end })
end
M._SuggestReady = suggestReady

-- Pas de chat (règle TreeAI) : les messages vont dans la ligne d'état du panneau (M._log, 20 derniers).
-- (4.0) pluriels écrits (« 1 macro créée », « 3 macros créées »), jamais « macro(s) créée(s) »
local function pl(n, one, many) return (tonumber(n) or 0) > 1 and many or one end
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
function M:ClearSpellCache() resolveCache = {}; bookNames = nil; if M.ForgetMemo then M:ForgetMemo() end end

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
-- (4.0.1) listes de consommables fusionnées (Data/Macros + Data/Consumables, données fixes) : calculées une fois,
-- plus à chaque relevé des sacs (chaque fin de combat avec butin / munitions)
local mergedFor, mergedFor2, mergedItems, mergedKnown
local function consumableLists()
  local D = data() or {}
  local items = D.items or {}
  local C = T.Data and T.Data.Consumables
  if mergedItems and mergedFor == items and mergedFor2 == C then return mergedItems, mergedKnown end
  -- Préfère les listes générées depuis le client (Data/Consumables.lua) quand elles existent.
  -- Les niveaux requis de Data/Macros.lua (bandage = Secourisme requis pour UTILISER, pierre de soins =
  -- niveau de la pierre) restent prioritaires quand la liste client les met à 1.
  local out = items
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
    out = merged
  end
  local known = {}
  for _, rows in pairs(out) do
    for _, row in ipairs(rows) do known[row[1]] = true end
  end
  mergedFor, mergedFor2, mergedItems, mergedKnown = items, C, out, known
  return out, known
end
local rowIndex = setmetatable({}, { __mode = "k" })   -- liste -> { [id] = { positions } } (listes fixes)
local function rowIndexOf(rows)
  local ix = rowIndex[rows]
  if not ix then
    ix = {}
    for i, row in ipairs(rows) do
      local id = row[1]
      if id ~= nil then
        local l = ix[id]
        if not l then l = {}; ix[id] = l end
        l[#l + 1] = i
      end
    end
    rowIndex[rows] = ix
  end
  return ix
end
local function newContext()
  local counts, unknown = {}, {}
  eachBagItem(function(id, n) counts[id] = (counts[id] or 0) + n end)
  local items, known = consumableLists()
  for id in pairs(counts) do
    if not known[id] then unknown[#unknown + 1] = id end
  end
  tsort(unknown)
  local level = playerLevel()
  local ctx = { counts = counts, cache = {} }

  -- (4.0.1) seuls les objets du sac sont regardés (index des listes, fixes) : même choix qu'en parcourant toute la
  -- liste (meilleur score, à égalité le premier de la liste), sans les centaines de lignes à chaque relevé
  local function bestOf(rows, okRow, lowest)
    if not rows then return nil end
    local ix = rowIndexOf(rows)
    local best, bestScore, bestPos
    for id, n in pairs(counts) do
      local poss = n > 0 and ix[id]
      if poss then
        for k = 1, #poss do
          local pos = poss[k]
          local row = rows[pos]
          local req, val, tag = row[2], row[3], row[4]
          if okRow(req, tag) then
            local score = val + ((tag == "conj") and 0.5 or 0) -- invoqué = gratuit : priorité à égalité
            if lowest then score = -score end
            if not bestScore or score > bestScore or (score == bestScore and pos < bestPos) then
              best, bestScore, bestPos = id, score, pos
            end
          end
        end
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

---------------------------------------------------------------------------
-- (MACRO3) Personnalisation : le modèle (jetons) est retouché AVANT le rendu, exactement comme
-- treeaicoach/wow/macros/generator.py personalize() (les deux sont testés sur les mêmes cas) :
--   mods=false : variantes Maj/Ctrl/Alt retirées ([mod:x] -> groupe enlevé, [nomod] -> condition enlevée)
--   mo=false   : groupes @mouseover enlevés ; pet=false : lignes /pet… ; guard=false : ligne « /cast !Faucon »
--   tank=N     : @party1 -> @partyN (/assist). Une clause sans plus aucun groupe disparaît ; « [] » seul = toujours.
---------------------------------------------------------------------------
local CONDCMD = {}
for _, c in ipairs({ "/cast", "/use", "/castsequence", "/castrandom", "/stopmacro", "/petattack", "/petfollow",
  "/petpassive", "/petdefensive", "/petaggressive", "/petstay", "/equip", "/target", "/targetenemy", "/assist",
  "/startattack", "/stopattack", "/cancelaura", "/cancelform", "/dismount", "/userandom", "/equipslot",
  "/stopcasting", "/click", "/focus", "/clearfocus" }) do CONDCMD[c] = true end
local CASTLIKE = { ["/cast"] = true, ["/use"] = true, ["/castsequence"] = true, ["/castrandom"] = true,
  ["/equip"] = true, ["/userandom"] = true }

local function guardLine()
  local D = data()
  return "/cast !{" .. tostring((D and D.guard) or 13165) .. "}"
end

local function leadGroups(c)
  local groups, rest = {}, (c:gsub("^%s+", ""))
  while rest:sub(1, 1) == "[" do
    local j = rest:find("]", 1, true)
    if not j then break end
    groups[#groups + 1] = rest:sub(2, j - 1)
    rest = (rest:sub(j + 1):gsub("^%s+", ""))
  end
  return groups, rest
end
M.LeadGroups = leadGroups

local function isMod(x)
  local l = x:lower()
  return l == "mod" or l == "modifier" or l:find("^mod:") ~= nil or l:find("^modifier:") ~= nil
end
local function isNoMod(x)
  local l = x:lower()
  return l == "nomod" or l == "nomodifier" or l:find("^nomod:") ~= nil or l:find("^nomodifier:") ~= nil
end

local function personalize(body, o)
  local tank = tonumber(o.tank) or 1
  if o.mods ~= false and o.mo ~= false and o.pet ~= false and o.guard ~= false and tank == 1 then return body end
  local guard = guardLine()
  local out = {}
  for raw in ((body or "") .. "\n"):gmatch("(.-)\n") do
    local opt = raw:sub(1, 1) == "?"
    local line = opt and raw:sub(2) or raw
    if line:sub(1, 1) ~= "/" then
      out[#out + 1] = raw
    else
      local cmd, sep, rest = line:match("^(%S+)( ?)(.*)$")
      local lc = (cmd or ""):lower()
      if o.pet == false and lc:sub(1, 4) == "/pet" then
        -- ligne d'ordre au familier retirée
      elseif o.guard == false and line == guard then
        -- garde d'aspect retirée
      elseif sep == "" or lc == "/run" or lc == "/script" then
        out[#out + 1] = raw
      else
        local new, keep = rest, true
        if tank ~= 1 and lc == "/assist" then new = new:gsub("@party1", "@party" .. tank) end
        if (o.mods == false or o.mo == false) and CONDCMD[lc] then
          local clauses = {}
          for clause in (new .. ";"):gmatch("([^;]*);") do
            local c = trim(clause)
            local q = ""
            if c:sub(1, 1) == "?" then q = "?"; c = c:sub(2) end
            local groups, tail = leadGroups(c)
            if #groups == 0 then
              clauses[#clauses + 1] = q .. c
            else
              local kept = {}
              for _, g in ipairs(groups) do
                local conds, drop = {}, false
                for x in (g .. ","):gmatch("([^,]*),") do
                  x = trim(x)
                  if x ~= "" then
                    if o.mods == false and isMod(x) then drop = true end
                    if not (o.mods == false and isNoMod(x)) then conds[#conds + 1] = x end
                  end
                end
                if not drop and o.mo == false then
                  for _, x in ipairs(conds) do
                    local l = x:lower()
                    if l == "@mouseover" or l == "target=mouseover" then drop = true end
                  end
                end
                if not drop then kept[#kept + 1] = conds end
              end
              if #kept > 0 then
                if #kept == 1 and #kept[1] == 0 then kept = {} end
                local gt = {}
                for _, cs in ipairs(kept) do gt[#gt + 1] = "[" .. tconcat(cs, ",") .. "]" end
                local gtxt = tconcat(gt)
                clauses[#clauses + 1] = q .. gtxt .. ((gtxt ~= "" and tail ~= "") and " " or "") .. tail
              end
            end
          end
          if #clauses == 0 then
            keep = false
          else
            local rebuilt = tconcat(clauses, "; ")
            if rebuilt:gsub(" ", "") ~= new:gsub(" ", "") then new = rebuilt end
          end
        end
        if keep then out[#out + 1] = (new == rest) and raw or ((opt and "?" or "") .. cmd .. " " .. new) end
      end
    end
  end
  local cast = false
  for _, l in ipairs(out) do
    local c = (l:gsub("^%?+", "")):match("^(%S+)")
    if c and CASTLIKE[c:lower()] then cast = true break end
  end
  if not cast then
    local keepL = {}
    for _, l in ipairs(out) do if (l:gsub("^%?+", "")):sub(1, 1) ~= "#" then keepL[#keepL + 1] = l end end
    out = keepL
  end
  return tconcat(out, "\n")
end
M.Personalize = personalize

-- sorts / objets obligatoires d'un corps (port de generator.required) : hors lignes et clauses « ? »
local function requiredOf(b)
  local D = data() or {}
  local pets = D.pet or {}
  local spells, items, seenS, seenI = {}, {}, {}, {}
  for line in ((b or "") .. "\n"):gmatch("(.-)\n") do
    if line:sub(1, 1) ~= "?" then
      local chunks = {}
      local cmd, rest = line:match("^(/%S+) (.*)$")
      if cmd then
        for clause in (rest .. ";"):gmatch("([^;]*);") do
          if trim(clause):sub(1, 1) ~= "?" then chunks[#chunks + 1] = clause end
        end
      else
        chunks[1] = line
      end
      for _, ch in ipairs(chunks) do
        for sid in ch:gmatch("{(%d+)}") do
          sid = tonumber(sid)
          if sid and not pets[sid] and not seenS[sid] then seenS[sid] = true; spells[#spells + 1] = sid end
        end
        for cat in ch:gmatch("{item:([%a_]+)}") do
          if not seenI[cat] then seenI[cat] = true; items[#items + 1] = cat end
        end
      end
    end
  end
  return spells, items
end
M.RequiredOf = requiredOf

local pcache = setmetatable({}, { __mode = "k" })
local function optSig(o)
  return (o.mods ~= false and "m" or "") .. (o.mo ~= false and "o" or "") .. (o.pet ~= false and "p" or "")
    .. (o.guard ~= false and "g" or "") .. tostring(tonumber(o.tank) or 1)
end
local function personalVariant(v, o, sig)
  sig = sig or optSig(o)
  if sig == "mopg1" then return v end
  local per = pcache[v]
  if not per then per = {}; pcache[v] = per end
  local hit = per[sig]
  if hit then return hit end
  local b = personalize(v.b or "", o)
  if b == v.b then hit = v
  else
    local r, it = requiredOf(b)
    hit = { b = b, r = r, it = it }
  end
  per[sig] = hit
  return hit
end
M.PersonalVariant = personalVariant

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

-- (MACRO3) avec les réglages du joueur ; la variante qu'il a choisie passe d'abord (si elle est possible).
-- Renvoie le corps et l'index de la variante rendue.
function M:Render(def, ctx)
  ctx = ctx or newContext()
  if not self:Supported(def) then return nil end
  local o, sig = self:Opts(def)
  local list = def.v or {}
  local cons = def.c == "consumable"
  local pin = tonumber(self:Opt(def, "var"))
  if pin and list[pin] then
    local body = renderVariant(personalVariant(list[pin], o, sig), ctx, cons)
    if body then return body, pin end
  end
  for i = 1, #list do
    if i ~= pin then
      local body = renderVariant(personalVariant(list[i], o, sig), ctx, cons)
      if body then return body, i end
    end
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
local memo = { t = -1 }   -- (MACRO3, perf) spé / contexte / définitions : une lecture par seconde
local function memoFresh()
  local gt = _G.GetTime
  if type(gt) ~= "function" then return {} end   -- pas d'horloge : rien n'est gardé
  local t = gt() or 0
  if memo.t ~= t and (t - memo.t >= 1 or t < memo.t) then memo = { t = t } end
  return memo
end
function M:ForgetMemo() memo = { t = -1 } end
function M:Spec()
  local m = memoFresh()
  if m.spec == nil then
    local want, spec, auto = char().spec, nil, true
    if want then
      for _, s in ipairs(classSpecs()) do if s.id == want then spec, auto = want, false end end
    end
    m.spec, m.specAuto = spec or detectSpec() or false, auto
  end
  return m.spec or nil, m.specAuto
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
  local m = memoFresh()
  if m.ctx == nil then m.ctx = inGroup() and "groupe" or "solo" end
  return m.ctx, true
end

local function forSpec(def, spec)
  if not def.s or #def.s == 0 or not spec then return true end
  for _, s in ipairs(def.s) do if s == spec then return true end end
  return false
end

-- Toutes les macros de la classe pour la spé courante (une seule définition par nom).
function M:Defs()
  local m = memoFresh()
  if m.defs then return m.defs end
  local D = data()
  local cls = playerClass()
  local all = (D and D.classes and cls and D.classes[cls]) or {}
  local spec = self:Spec()
  local out = {}
  for _, def in ipairs(all) do
    if forSpec(def, spec) then out[#out + 1] = def end
  end
  m.defs = out
  return out
end

---------------------------------------------------------------------------
-- (MACRO3) Préréglages (Simple / Complet / Pro) et réglages par macro, gardés par personnage
---------------------------------------------------------------------------
local optGen = 0   -- (perf) +1 à chaque changement de réglage / préréglage : les réglages effectifs gardés sont relus
local PRESETS = {
  simple = { label = "Simple", mods = false, mo = false,
    tip = "Les macros de base, sans variantes Maj/Ctrl/Alt ni survol : un bouton = une chose." },
  complet = { label = "Complet", mods = true, mo = true,
    tip = "Le jeu conseillé : variantes Maj/Ctrl/Alt et survol (@mouseover)." },
  pro = { label = "Pro", mods = true, mo = true,
    tip = "Complet + les macros « Pro » qui tirent le maximum de chaque touche (pull en un bouton, burst, panique…)." },
}
M.PRESETS = PRESETS
M.PRESET_ORDER = { "simple", "complet", "pro" }

function M:Preset()
  local p = char().preset
  return (p and PRESETS[p]) and p or "complet"
end

-- la macro fait-elle partie du préréglage en cours ? (def = Complet, sp = Simple, pr = Pro)
function M:InPreset(def)
  local p = self:Preset()
  if p == "simple" and def.sp ~= nil then return def.sp and true or false end
  if p == "pro" and def.pr ~= nil then return def.pr and true or false end
  return def.def and true or false
end

-- un réglage : choix du joueur pour cette macro, sinon le préréglage
function M:Opt(def, key)
  local o = char().opts[def.n]
  if o ~= nil and o[key] ~= nil then return o[key] end
  if key == "on" then return self:InPreset(def) end
  if key == "mods" or key == "mo" then return PRESETS[self:Preset()][key] end
  if key == "pet" or key == "guard" or key == "auto" then return true end
  if key == "tank" then return 1 end
  return nil
end

function M:SetOpt(def, key, v)
  memo = { t = -1 }
  optGen = optGen + 1
  local c = char()
  local o = c.opts[def.n]
  if type(o) ~= "table" then o = {}; c.opts[def.n] = o end
  o[key] = v
  if next(o) == nil then c.opts[def.n] = nil end
  return v
end

-- changer de préréglage : les réglages suivent (le nom, l'icône et le tank choisis restent)
function M:SetPreset(p)
  if not PRESETS[p] then return false end
  memo = { t = -1 }
  optGen = optGen + 1
  local c = char()
  c.preset = p
  for n, o in pairs(c.opts) do
    if type(o) == "table" then
      for _, k in ipairs({ "on", "mods", "mo", "pet", "guard", "auto", "var" }) do o[k] = nil end
      if next(o) == nil then c.opts[n] = nil end
    end
  end
  return true
end

-- réglages effectifs passés à personalize() (seulement ceux que la macro propose : f = « m o p g t »)
local ALL_ON = { mods = true, mo = true, pet = true, guard = true, tank = 1 }
local ocache = setmetatable({}, { __mode = "k" })   -- (perf) def -> réglages effectifs, jusqu'au prochain changement
local function optOn(f, o, P, letter, key)
  if not f:find(letter, 1, true) then return true end
  local v = o and o[key]
  if v == nil then
    if key == "mods" or key == "mo" then return P[key] ~= false end
    return true
  end
  return v ~= false
end
function M:Opts(def)
  local f = def.f
  if f == nil or f == "" then return ALL_ON, "mopg1" end
  local c = char()
  local opts, preset = c.opts, c.preset
  local e = ocache[def]
  if e and e.gen == optGen and e.opts == opts and e.preset == preset then return e.o, e.sig end
  local o = opts[def.n]
  local P = PRESETS[self:Preset()]
  local r = { mods = optOn(f, o, P, "m", "mods"), mo = optOn(f, o, P, "o", "mo"), pet = optOn(f, o, P, "p", "pet"),
    guard = optOn(f, o, P, "g", "guard"), tank = (f:find("t", 1, true) and o and tonumber(o.tank)) or 1 }
  local sig = optSig(r)
  ocache[def] = { gen = optGen, opts = opts, preset = preset, o = r, sig = sig }
  return r, sig
end

-- le client a-t-il ce qu'il faut ? (TAI Pro Focus : la focalisation)
local function focusOK() return type(_G.FocusUnit) == "function" end
function M:Supported(def)
  if def.nf == "focus" and not focusOK() then return false end
  return true
end

-- nom de la macro dans le jeu (renommable) ; les tables installed / bodies / opts restent indexées par def.n
function M:GameName(def)
  local o = char().opts[def.n]
  local n = o ~= nil and o.name
  if n and n ~= "" and type(n) == "string" then return n end
  return def.n
end

-- activée ? (choix du joueur, sinon préréglage + contexte solo / groupe)
function M:IsOn(def)
  local o = char().opts[def.n]
  if o ~= nil and o.on ~= nil then return o.on and true or false end
  if not self:InPreset(def) then return false end
  if def.x == nil or def.x == "" then return true end
  return def.x == (self:Context())
end
-- (ancien nom, lu par Binds / le panneau) : fait partie du jeu installé = activée
function M:IsDefault(def) return self:IsOn(def) end

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

-- Index d'une macro par son nom. GetMacroIndexByName n'existe pas sur Forever (FIDELITY F9) : UN parcours de
-- GetMacroInfo (onglet compte puis personnage) remplit une table nom -> index, gardée jusqu'au prochain
-- UPDATE_MACROS ou à notre propre écriture (créer / modifier / supprimer décale les index : le jeu trie par nom).
local idxByName, idxDirty = {}, true
local function invalidateIndex() idxDirty = true end
M.InvalidateMacroIndex = invalidateIndex
local function rebuildIndex()
  for k in pairs(idxByName) do idxByName[k] = nil end
  local maxG = macroLimits()
  local g, c = macroCounts()
  for idx = 1, g do local n = macroName(idx) if n and not idxByName[n] then idxByName[n] = idx end end
  for idx = maxG + 1, maxG + c do local n = macroName(idx) if n and not idxByName[n] then idxByName[n] = idx end end
  idxDirty = false
end
local function macroIndex(name)
  if type(name) ~= "string" then return nil end
  if type(_G.GetMacroIndexByName) == "function" then
    local i = tonumber((try(_G.GetMacroIndexByName, name)))
    if i and i > 0 then return i end
    if i then return nil end
  end
  if idxDirty then rebuildIndex() end
  local idx = idxByName[name]
  -- garde-fou : l'index gardé porte-t-il encore ce nom ? (une macro bougée sans événement) -> on relit tout
  if idx and macroName(idx) ~= name then rebuildIndex() idx = idxByName[name] end
  return idx
end
M.MacroIndex = macroIndex
local function gname(def) return M:GameName(def) end

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
  local idx = macroIndex(gname(def))
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
  local gn = gname(def)
  local idx = macroIndex(gn)
  -- (MACRO3) icône : rien (le jeu garde la sienne) sauf icône fixe choisie ou changement d'icône demandé
  local iconWanted = (self:Opt(def, "icon") == "fixed" or self:Opt(def, "iconDirty")) and self:IconArg(def) or nil
  if idx then
    local _, _, cur = try(_G.GetMacroInfo, idx)
    local stored = ch.bodies[def.n]
    -- macro modifiée à la main, ou macro du joueur qui porte déjà ce nom : on n'y touche jamais
    -- (sauf « /tai macros forcer »)
    if not force and (not stored or (cur and not same(cur, stored))) then
      if same(cur, body) then
        ch.bodies[def.n] = cur; ch.installed[def.n] = true
        if not ch.stamps[def.n] then ch.stamps[def.n] = stamp() end
        return "same"
      end
      return "user"
    end
    if same(cur, body) and not iconWanted then
      ch.bodies[def.n] = cur; ch.installed[def.n] = true
      if not ch.stamps[def.n] then ch.stamps[def.n] = stamp() end
      return "same"
    end
    local ok, err = pcall(_G.EditMacro, idx, gn, iconWanted, body)
    invalidateIndex()
    if not ok then self.lastError = tostring(err); return "fail" end
    local _, _, after2 = try(_G.GetMacroInfo, macroIndex(gn) or idx)
    if not same(after2, body) or blockedCount() > before then self:SetBlocked("EditMacro"); return "blocked" end
    ch.bodies[def.n] = body
    ch.installed[def.n] = true
    ch.stamps[def.n] = stamp()
    if self:Opt(def, "iconDirty") then self:SetOpt(def, "iconDirty", nil) end
    return "updated"
  end
  local okSlot, perChar = freeSlot(db().perCharacter ~= false)
  if not okSlot then return "full" end
  local ok, ret = pcall(_G.CreateMacro, gn, iconWanted or def.i or "INV_MISC_QUESTIONMARK", body, perChar)
  invalidateIndex()   -- une macro de plus : les index suivants bougent
  if not ok then
    if isLimitError(ret) then return "full" end
    -- icône refusée : une seule autre tentative avec l'ID de fichier du « ? » (rien n'a été créé)
    ok, ret = pcall(_G.CreateMacro, gn, 134400, body, perChar)
    if not ok then
      self.lastError = tostring(ret)
      if isLimitError(ret) then return "full" end
      return "fail"
    end
  end
  ret = tonumber(ret)
  local newIdx = (ret and ret > 0 and macroName(ret) == gn) and ret or macroIndex(gn)
  if not newIdx or blockedCount() > before then
    -- appel accepté mais rien créé : le jeu l'a bloqué (ADDON_ACTION_FORBIDDEN) ou refusé en silence
    self:SetBlocked(blockedCount() > before and "CreateMacro (interdit)" or "CreateMacro (rien créé)")
    return "blocked"
  end
  ch.bodies[def.n] = body
  ch.installed[def.n] = true
  ch.stamps[def.n] = stamp()
  if self:Opt(def, "iconDirty") then self:SetOpt(def, "iconDirty", nil) end
  return "created"
end

-- (MACRO3) icône donnée au jeu : « ? » (l'icône suit le sort grâce à #showtooltip) ou l'icône fixe choisie
function M:IconArg(def, ctx)
  if self:Opt(def, "icon") == "fixed" and M.IconOf then
    local tex = M.IconOf(def, ctx)
    if tex and tex ~= "Interface\\Icons\\INV_Misc_QuestionMark" then
      if type(tex) == "string" then tex = tex:gsub("^Interface\\Icons\\", "") end
      return tex
    end
  end
  return def.i or "INV_MISC_QUESTIONMARK"
end

local function keyHints(defs)
  local parts = {}
  for _, def in ipairs(defs) do
    if def.k and def.k ~= "-" and char().installed[def.n] then
      parts[#parts + 1] = gname(def):gsub("^" .. PREFIX, "") .. "=" .. def.k
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
    say(("%d %s, %d déjà à jour (%s, %s)."):format(n, pl(n, "macro créée ou mise à jour", "macros créées ou mises à jour"),
      same0, self:SpecName(spec), (self:Context())))
    if full > 0 then
      local maxG, maxC = macroLimits()
      local g, c = macroCounts()
      say(("%d %s : plus de place (perso %d/%d, compte %d/%d). Supprime des macros inutiles (Échap > Macros) ou copie-les à la main."):format(full,
        pl(full, "macro non créée", "macros non créées"), c, maxC, g, maxG))
      M._copyList = failed
      suggest("ready", ("Plus de place pour %d %s : clique pour %s copier-coller"):format(full, pl(full, "macro", "macros"),
        pl(full, "la", "les")), 2,
        { ttl = 0, onClick = function() M:ShowCopy(M._copyList) end })
    elseif fail > 0 then
      say(("%d %s par le jeu (%s) : bouton « Diagnostic »."):format(fail, pl(fail, "macro refusée", "macros refusées"),
        tostring(self.lastError or "?")))
    end
    if user > 0 then
      say(("%d %s (Maj+clic sur « Installer » pour écraser)."):format(user,
        pl(user, "macro modifiée à la main, gardée telle quelle", "macros modifiées à la main, gardées telles quelles")))
    end
    if full == 0 and n > 0 then
      local hints = keyHints(defs)
      say("Pose-les sur ta barre : « Glisser sur la barre » (ou fais glisser une ligne de la liste)." ..
        (hints ~= "" and (" Touches conseillées : " .. hints) or ""))
      suggest("ready", ("%d %s — clique pour %s glisser sur ta barre"):format(n + same0,
        pl(n + same0, "macro TAI prête", "macros TAI prêtes"), pl(n + same0, "la", "les")), 1,
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
    local idx = macroIndex(gname(def))
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
      items[#items + 1] = { n = gname(def), ok = ok, status = code, reason = REASON[code] or code,
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
  local pn = gname(p.def)
  f.name._orig = pn; f.name:SetText(pn)
  local ic = p.def.i or "INV_Misc_QuestionMark"
  f.icon:SetText("Icône : " .. ((ic:lower() == "inv_misc_questionmark") and "« ? » (l'icône suit le sort)" or ic))
  f.body._orig = p.body; f.body:SetText(p.body)
  f.count:SetText(("Macro %d / %d — %s — %d / 255 caractères%s"):format(i, #list, p.def.d or "", #p.body,
    p.def.k and p.def.k ~= "-" and (" — touche conseillée : " .. p.def.k) or ""))
end

local usesCache = setmetatable({}, { __mode = "k" })
local function usesItems(def)
  local hit = usesCache[def]
  if hit ~= nil then return hit end
  hit = def.c == "consumable"
  if not hit then
    for _, v in ipairs(def.v or {}) do
      local b = v.b or ""
      if b:find("{item:", 1, true) or b:find("{slot:", 1, true) then hit = true break end
    end
  end
  usesCache[def] = hit
  return hit
end

-- Met à jour les macros déjà installées (kind = "consumable" ou toutes).
-- sliced = vrai pour les relectures automatiques (sac, sort appris, connexion) : 3 macros par image au plus (une
-- montée de niveau rendait toutes les macros dans la même image) ; un appel direct (commande, clic) reste immédiat
function M:Refresh(onlyConsumables, ctx0, sliced)
  local key = onlyConsumables and "Macros:consumables" or "Macros:refresh"
  runOOC(key, function()
    local ctx = (ctx0 and not inCombat()) and ctx0 or newContext()
    -- (MACRO3) hors d'un clic (Forever) : la liste de ce qui attend « clique pour mettre à jour »
    local waiting = {}
    local noClick = T.InUserAction and not T:InUserAction()
    local defs, i = self:Defs(), 1
    local function finish()
      if #waiting > 0 then
        M._pending = true
        suggestReady("Macros à mettre à jour : clique pour les mettre à jour", waiting)
      end
      if self.panel and self.panel:IsShown() then self:FillPanel() end
      local W = T.Window
      if W and W.IsOpen and W.IsOpen() and W.CurrentPage and W.CurrentPage() == "macros" and W.Dirty then W.Dirty() end
    end
    local function step()
      local c = char()
      local installed, bodies = c.installed, c.bodies
      local done = 0
      while i <= #defs do
        local def = defs[i]
        i = i + 1
        -- (MACRO3) seulement les macros activées dont la mise à jour auto n'est pas coupée
        local idx = installed[def.n] and (not onlyConsumables or usesItems(def)) and self:IsOn(def)
          and self:Opt(def, "auto") ~= false and macroIndex(gname(def))
        local body = idx and self:Render(def, ctx)
        if body then
          local _, _, cur = try(_G.GetMacroInfo, idx)
          local stored = bodies[def.n]
          -- rien à changer : pas d'écriture (donc pas de « clique pour mettre à jour » à chaque sac) ; une macro
          -- modifiée à la main n'est jamais proposée (elle n'est jamais réécrite)
          if not same(cur, body) and not self:IsBlocked() and stored and same(cur, stored) then
            if noClick then
              waiting[#waiting + 1] = gname(def)
            else
              local r = self:Write(def, body, false)
              if r == "blocked" then suggestReady("Le jeu bloque la mise à jour auto : clique pour copier-coller") return end
              if r == "pending" then waiting[#waiting + 1] = gname(def) end
            end
          end
          done = done + 1
          -- sliced : 3 macros par image au plus (une montée de niveau rendait toutes les macros dans la même image)
          if sliced and done >= 3 and i <= #defs then
            after(0, function() if not inCombat() then step() else runOOC(key, step) end end)
            return
          end
        end
      end
      finish()
    end
    step()
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
    -- (MACRO3) une macro renommée garde sa clé (nom d'origine « TAI … ») : son nom dans le jeu vient des réglages
    for name in pairs(char().installed) do
      local o = char().opts[name]
      local gn = (type(o) == "table" and type(o.name) == "string" and o.name ~= "") and o.name or name
      local idx = macroIndex(gn)
      if idx and name:sub(1, #PREFIX) == PREFIX then
        local _, _, cur = try(_G.GetMacroInfo, idx)
        local stored = char().bodies[name]
        -- jamais supprimer une macro modifiée par le joueur
        if stored and same(cur, stored) and pcall(_G.DeleteMacro, idx) then n = n + 1 invalidateIndex() end
      end
    end
    char().installed, char().bodies, char().setup = {}, {}, false
    say(("%d %s."):format(n, pl(n, "macro TreeAI supprimée", "macros TreeAI supprimées")))
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
  M:ForgetMemo()
end

local function cycleCtx()
  local cur = char().ctx
  char().ctx = (cur == nil and "solo") or (cur == "solo" and "groupe") or nil
  M:ForgetMemo()
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
  local selIdx = sel and macroIndex(gname(sel))
  if selIdx and not onBars(selIdx) then return sel, selIdx end
  for _, def in ipairs(self:Defs()) do
    local idx = char().installed[def.n] and macroIndex(gname(def))
    if idx and not onBars(idx) then return def, idx end
  end
  return nil
end

-- « Glisser sur la barre » : installe si besoin, puis met la macro sur le curseur (pose-la d'un clic)
function M:PickToBar(def)
  if inCombat() then say("Impossible en combat : refais-le après."); return end
  def = def or select(1, self:NextToPlace()) or self:SelectedDef()
  if not def then say("Toutes tes macros TAI sont déjà sur tes barres."); return end
  local idx = macroIndex(gname(def))
  if not idx then
    userDo(M.InstallOne, M, def, false)
    return
  end
  try(_G.PickupMacro, idx)
  say(gname(def) .. " est sur ton curseur : clique sur une case de ta barre pour la poser (clic droit : annuler).")
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

-- (MACRO3) « Macros » (minimap, Outils, Options, /tai macros) : la page de la fenêtre TreeAI quand elle tourne ;
-- l'ancien panneau (liste + fiche + glisser) reste : « Liste détaillée » (M:ToggleList).
local function windowPage()
  local W = T.Window
  if not (W and type(W.Toggle) == "function" and W.pageIndex and W.pageIndex.macros) then return nil end
  if W.IsEnabled and not W:IsEnabled() then return nil end
  return W
end
function M:TogglePanel()
  local W = windowPage()
  if W then W.Toggle("macros") return end
  self:ToggleList()
end
function M:OpenPage()
  local W = windowPage()
  if W then W.Open("macros") return true end
  self:OpenPanel()
  return false
end

function M:ToggleList()
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
  self:ToggleList()
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
  f.desc:SetText(("%d %s, %d à voir — mode %s."):format(rep.ok or 0, pl(rep.ok, "installée", "installées"), rep.ko or 0, rep.mode or "auto"))
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
    f.name:SetText(gname(def))
    f.desc:SetText(def.d or "")
    f.where:SetText("|cffffd100Où la mettre :|r " .. (def.o or ""))
    local ctxTxt = (def.x == "groupe" and "groupe") or (def.x == "solo" and "solo") or "solo + groupe"
    local idx = (st == "installed" or st == "user") and macroIndex(gname(def))
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
-- (MACRO3) État de chaque macro : installée ? à jour ? sur une barre ? touche ? auto ? -> pastille
--   vert « active » · orange « à installer / à mettre à jour » (clic) · gris « désactivée / plus tard /
--   modifiée par toi / figée » · rouge « erreur » + raison (le jeu bloque, plus de place, rien dans les sacs…)
-- Lu à la demande (page « Macros » ouverte) : rien ne tourne en fond pour ça.
---------------------------------------------------------------------------
local SLOT_CMD = { { 1, "ACTIONBUTTON" }, { 61, "MULTIACTIONBAR1BUTTON" }, { 49, "MULTIACTIONBAR2BUTTON" },
  { 25, "MULTIACTIONBAR3BUTTON" }, { 37, "MULTIACTIONBAR4BUTTON" }, { 145, "MULTIACTIONBAR5BUTTON" },
  { 157, "MULTIACTIONBAR6BUTTON" }, { 169, "MULTIACTIONBAR7BUTTON" } }
local function slotCommand(slot)
  for _, b in ipairs(SLOT_CMD) do
    if slot >= b[1] and slot < b[1] + 12 then return b[2] .. (slot - b[1] + 1) end
  end
  return nil
end
local function bindingKey(cmd)
  if not cmd then return nil end
  local k = try(_G.GetBindingKey, cmd)
  if type(k) == "string" and k ~= "" then return k end
  return nil
end

-- nom de macro -> { slots = {cases}, key = touche } : un relevé des barres (1-120, 145-180), gardé jusqu'à
-- ACTIONBAR_SLOT_CHANGED / UPDATE_BINDINGS / UPDATE_MACROS. Le nom vient du texte de la case (Mainline : une
-- macro qui affiche un sort rend « spell » comme sous-type, l'ID n'est alors pas un index de macro).
local placeCache
local function placement()
  if placeCache then return placeCache end
  local byName = {}
  placeCache = byName
  if type(_G.GetActionInfo) ~= "function" then return byName end
  local addonKeys = {}
  local B = T.Binds or (T.modules and T.modules.Binds)
  if B and type(B.ReadAddonBars) == "function" then
    local ok, k = pcall(B.ReadAddonBars)
    if ok and type(k) == "table" then addonKeys = k end
  end
  for slot = 1, 180 do
    if slot <= 120 or slot >= 145 then
      local kind, id, sub = try(_G.GetActionInfo, slot)
      if kind == "macro" then
        local name = try(_G.GetActionText, slot)
        if (type(name) ~= "string" or name == "") and sub ~= "spell" and type(id) == "number" then name = macroName(id) end
        if type(name) == "string" and name ~= "" then
          local e = byName[name]
          if not e then e = { slots = {} }; byName[name] = e end
          e.slots[#e.slots + 1] = slot
          if not e.key then
            local ak = addonKeys[slot]
            e.key = (type(ak) == "table" and ak.key) or bindingKey(slotCommand(slot))
          end
        end
      end
    end
  end
  return byName
end
M.Placement = placement
function M.DirtyPlacement() placeCache = nil end

local function keyText(k)
  local B = T.Binds or (T.modules and T.modules.Binds)
  if k and B and type(B.KeyText) == "function" then return B.KeyText(k) end
  return k
end
M.KeyText = keyText

local BADGE = { ok = "good", install = "warn", update = "warn", off = "muted", later = "muted", user = "muted",
  frozen = "muted", unsupported = "muted", empty = "muted", blocked = "bad", full = "bad", invalid = "bad" }
local RGB = { good = { 0.3, 0.9, 0.4 }, warn = { 1, 0.6, 0.15 }, muted = { 0.6, 0.6, 0.62 }, bad = { 1, 0.3, 0.25 } }
M.BADGE = BADGE
--- couleur d'une pastille (jetons du thème : good / warn / muted / bad)
function M.BadgeColor(tok)
  local th = T.Theme
  if th and type(th.Color) == "function" then
    local ok, r, g, b = pcall(th.Color, th, tok)
    if ok and type(r) == "number" then return r, g, b end
  end
  local c = RGB[tok] or RGB.muted
  return c[1], c[2], c[3]
end
local ROUND = "Interface\\CHARACTERFRAME\\TempPortraitAlphaMask"
M.ROUND = ROUND
--- pastille ronde dans un texte (« |T…|t » teinté)
function M.Dot(tok, size)
  local r, g, b = M.BadgeColor(tok)
  size = size or 12
  return ("|T%s:%d:%d:0:0:64:64:6:58:6:58:%d:%d:%d|t"):format(ROUND, size, size,
    math.floor(r * 255 + 0.5), math.floor(g * 255 + 0.5), math.floor(b * 255 + 0.5))
end

--- « il y a 2 min » (heure serveur time())
local function ago(t)
  t = tonumber(t)
  if not t then return nil end
  local d = stamp() - t
  if d < 0 then d = 0 end
  if d < 60 then return ("il y a %d s"):format(math.max(1, d)) end
  if d < 3600 then return ("il y a %d min"):format(math.floor(d / 60)) end
  if d < 86400 then return ("il y a %d h"):format(math.floor(d / 3600)) end
  return ("il y a %d j"):format(math.floor(d / 86400))
end
M.Ago = ago

local function changeWhat(cur, exp)
  if type(cur) ~= "string" or type(exp) ~= "string" then return "nouveau texte" end
  if (cur:match("item:(%d+)") or "") ~= (exp:match("item:(%d+)") or "") then return "meilleur objet du sac" end
  if #exp > #cur then return "nouveau sort ou réglage" end
  return "réglage changé"
end

local CLICK_KEY = "/click TreeAIMacro_(%w+)"
local function clickKeyOf(def)
  local v = def.c == "click" and def.v and def.v[1]
  return v and (v.b or ""):match(CLICK_KEY) or nil
end
M.ClickKeyOf = clickKeyOf

--- État d'une macro (page, Raccourcis, tests). place = relevé des barres (placement()).
function M:MacroState(def, ctx, place)
  ctx = ctx or newContext()
  local c = char()
  local st = { def = def, name = gname(def), on = self:IsOn(def), auto = self:Opt(def, "auto") ~= false }
  local idx = macroIndex(st.name)
  local cur
  if idx then local _, _, b = try(_G.GetMacroInfo, idx) cur = b end
  st.idx, st.installed, st.current, st.stamp = idx, idx ~= nil, cur, c.stamps[def.n]
  local body, vi = self:Render(def, ctx)
  st.expected, st.variant = body, vi
  local stored = c.bodies[def.n]
  st.user = (idx ~= nil) and (not stored or (cur ~= nil and not same(cur, stored))) and not same(cur, body) or false
  local pe = (place or placement())[st.name]
  st.slots = pe and pe.slots or {}
  st.onBar = #st.slots > 0
  st.key = (pe and pe.key) or bindingKey("MACRO " .. st.name)
  local code, text
  if not self:Supported(def) then
    code, text = "unsupported", "pas sur ce client (pas de focalisation)"
  elseif not st.on then
    code, text = "off", st.installed and "désactivée (toujours dans tes macros)" or "désactivée"
  elseif not body then
    local miss = self:Missing(def)
    code = "later"
    if def.l and def.l <= 60 and def.l > playerLevel() then text = "au niveau " .. def.l
    else text = "pas encore" .. (#miss > 0 and (" : il te manque " .. tconcat(miss, ", ")) or "") end
  elseif def.c == "consumable" and body:find("^/run print") then
    code, text = "empty", "en attente : rien pour elle dans tes sacs (elle se remplira toute seule)"
  elseif st.installed and same(cur, body) and not self:Opt(def, "iconDirty") then
    code, text = "ok", "active"
  elseif st.user then
    code, text = "user", "gardée telle quelle : modifiée par toi"
  elseif st.installed and not st.auto then
    code, text = "frozen", "figée (mise à jour auto coupée) : clic pour l'écrire une fois"
  else
    local okB, why = M.ValidBody(body)
    if not okB then
      code, text = "invalid", "texte refusé : " .. tostring(why)
    elseif self:IsBlocked() then
      code, text = "blocked", "le jeu bloque l'écriture : copier-coller"
    elseif not st.installed and not freeSlot(db().perCharacter ~= false) then
      local g, n = macroCounts()
      local maxG, maxC = macroLimits()
      code, text = "full", ("plus de place (perso %d/%d, compte %d/%d)"):format(n, maxC, g, maxG)
    elseif st.installed then
      code = "update"
      text = "à mettre à jour (" .. (self:Opt(def, "iconDirty") and same(cur, body) and "icône" or changeWhat(cur, body)) .. ") : clic"
    else
      code, text = "install", "à installer : clic"
    end
  end
  st.code, st.text, st.badge = code, text, BADGE[code] or "muted"
  -- auto : TreeAI la garde à jour (objets, sorts, rangs) ; macro « clic » : sa cible suit toute seule
  local ck = clickKeyOf(def)
  if ck and code == "ok" then
    local cs = M._clickState[ck]
    st.autoText = cs and cs.info and ("suit : " .. cs.info .. (cs.at and (" · " .. ago(cs.at)) or ""))
      or ("suit tout seul" .. ((cs and cs.why) and (" · " .. cs.why) or ""))
  elseif code == "ok" then
    st.autoText = st.auto and ("auto" .. (st.stamp and (" · mise à jour " .. ago(st.stamp)) or "")) or "auto coupé"
  end
  return st
end

--- Toutes les macros de la spé (une passe : un contexte de sacs, un relevé des barres) + compteurs
function M:States()
  local ctx, place = newContext(), placement()
  local list, n = {}, { ok = 0, todo = 0, err = 0, off = 0, nobar = 0 }
  for _, def in ipairs(self:Defs()) do
    local st = self:MacroState(def, ctx, place)
    list[#list + 1] = st
    if st.badge == "good" then n.ok = n.ok + 1; if not st.onBar then n.nobar = n.nobar + 1 end
    elseif st.badge == "warn" then n.todo = n.todo + 1
    elseif st.badge == "bad" and st.on then n.err = n.err + 1
    else n.off = n.off + 1 end
  end
  return list, n, ctx
end

---------------------------------------------------------------------------
-- (MACRO3) « Tout mettre à jour » : UN clic (événement matériel) installe les macros activées qui manquent et
-- réécrit celles qui ont changé. Rien en combat, rien hors d'un clic (Forever : EditMacro / CreateMacro).
---------------------------------------------------------------------------
function M:UpdateAll()
  if inCombat() then say("En combat : refais-le après le combat (rien n'est écrit en combat)."); return 0 end
  self:ClearSpellCache()
  if self:IsBlocked() then
    if T.InUserAction and not T:InUserAction() then suggestReady(); return 0 end
    self:ShowCopy(self:PlanList(false))
    return 0
  end
  local list = self:States()
  local made, full, fail, blocked = 0, 0, 0, false
  for _, st in ipairs(list) do
    if st.code == "install" or st.code == "update" then
      local r = self:Write(st.def, st.expected, false)
      if r == "created" or r == "updated" then made = made + 1
      elseif r == "pending" then return made
      elseif r == "full" then full = full + 1
      elseif r == "blocked" then blocked = true; break
      elseif r ~= "same" then fail = fail + 1 end
    end
  end
  if self.Clear then self:Clear("ready") end
  M._pending = false
  if blocked then
    say("Le jeu bloque l'écriture des macros : copie-les à la main (fenêtre ouverte).")
    self:ShowCopy(self:PlanList(false))
    return made
  end
  if made > 0 then char().setup = true end
  local parts = { ("%d %s"):format(made, pl(made, "macro écrite", "macros écrites")) }
  if full > 0 then parts[#parts + 1] = ("%d sans place (supprime des macros inutiles)"):format(full) end
  if fail > 0 then parts[#parts + 1] = ("%d %s par le jeu"):format(fail, pl(fail, "refusée", "refusées")) end
  say(((made == 0 and full == 0 and fail == 0) and "Tout est déjà à jour." or (tconcat(parts, ", ") .. ".")))
  M.DirtyPlacement()
  return made
end

-- une macro (clic sur sa ligne) : installe / met à jour ; force = écrase ta version modifiée (Maj+clic)
function M:UpdateOne(def, force)
  if inCombat() then say("Impossible en combat."); return "fail" end
  local body = self:Render(def)
  if not body then say(gname(def) .. " : pas encore disponible."); return "later" end
  if self:IsBlocked() and not force then self:ShowCopy({ { def = def, body = body } }); return "blocked" end
  local r = self:Write(def, body, force)
  if r == "created" or r == "updated" or r == "same" then
    char().setup = true
    say(("%s %s."):format(gname(def), (r == "created" and "créée") or (r == "updated" and "mise à jour") or "déjà à jour"))
  elseif r == "user" then say(gname(def) .. " : modifiée à la main, gardée (Maj+clic pour l'écraser).")
  elseif r == "full" then say(gname(def) .. " : plus de place dans tes macros."); self:ShowCopy({ { def = def, body = body } })
  elseif r == "blocked" then say(gname(def) .. " : bloquée par le jeu, copie-la (fenêtre ouverte)."); self:ShowCopy({ { def = def, body = body } })
  elseif r == "fail" then say(gname(def) .. " : refusée par le jeu (" .. tostring(self.lastError or "?") .. ").") end
  return r
end

-- un réglage change pendant un clic : la macro installée est réécrite tout de suite (même clic, hors combat)
function M:ApplyNow(def)
  if inCombat() or not (char().installed[def.n] and macroIndex(gname(def))) then return nil end
  if T.InUserAction and not T:InUserAction() then return nil end
  local st = self:MacroState(def)
  if st.code == "update" then return self:Write(def, st.expected, false) end
  return nil
end

--- préréglage : 1er clic = « clique encore pour confirmer » (8 s), 2e clic = réglages changés puis écrits tout de
--- suite (même clic, hors combat). Rien n'est supprimé : une macro qui sort du préréglage est seulement désactivée.
function M:ApplyPreset(p)
  if not PRESETS[p] then return end
  if p == self:Preset() then say(("Préréglage « %s » : déjà celui-ci."):format(PRESETS[p].label)) return end
  local t = now()
  local ask = M._presetAsk
  if not (ask and ask.p == p and t - ask.t < 8) then
    M._presetAsk = { p = p, t = t }
    say(("Préréglage « %s » : clique encore pour l'appliquer (tes macros sont réécrites, aucune n'est supprimée)."):format(PRESETS[p].label))
    return
  end
  M._presetAsk = nil
  self:SetPreset(p)
  say(("Préréglage « %s »."):format(PRESETS[p].label))
  if not inCombat() and (not T.InUserAction or T:InUserAction()) then self:UpdateAll() end
end

---------------------------------------------------------------------------
-- (MACRO3) Macros « Pro » dynamiques : la macro du jeu fait « /click TreeAIMacro_rare » ; le texte de ce bouton
-- sécurisé caché est réécrit HORS COMBAT (SetAttribute, comme les boutons « Raccourcis ») : jamais d'EditMacro,
-- rien à valider. useOnKeyDown = false : le clic relâché que /click envoie déclenche l'action même avec
-- « lancer à l'appui » (SecureActionButton_OnClick de Forever).
---------------------------------------------------------------------------
local clickBtns = {}
M._clickBtns = clickBtns
M._clickState = M._clickState or {}
local function cleanName(n)
  if type(n) ~= "string" then return nil end
  n = n:gsub("[\r\n;]", ""):gsub("^%s+", ""):gsub("%s+$", "")
  if n == "" or #n > 60 then return nil end
  return n
end
local CLICK_SRC = {}
M.CLICK_SRC = CLICK_SRC
function CLICK_SRC.rare()
  local Dt = T.Detect
  if not (Dt and type(Dt.Rares) == "function") then return nil, "détection coupée" end
  local ok, list = pcall(Dt.Rares, "rare")
  local e = ok and type(list) == "table" and list[1] or nil
  local name = type(e) == "table" and cleanName(e.name) or nil
  if not name then return nil, "aucun rare repéré pour l'instant" end
  return "/targetexact " .. name, name
end
function CLICK_SRC.quest()
  local lines, info = {}, {}
  local Qk = T.Quick
  if Qk and type(Qk.NextTarget) == "function" then
    local ok, name = pcall(Qk.NextTarget)
    name = ok and cleanName(name) or nil
    if name then lines[#lines + 1] = "/targetexact " .. name; info[#info + 1] = name end
  end
  local Q = T.modules and T.modules.Quests
  if Q and type(Q.PickItem) == "function" then
    local ok, q = pcall(Q.PickItem, Q.lastList)
    local id = ok and type(q) == "table" and type(q.item) == "table" and tonumber(q.item.id) or nil
    if id then
      lines[#lines + 1] = "/use item:" .. id
      local A = API()
      info[#info + 1] = (A.ItemName and A.ItemName(id)) or ("objet " .. id)
    end
  end
  if #lines == 0 then return nil, "rien à cibler ni à utiliser pour l'instant" end
  return tconcat(lines, "\n"), tconcat(info, " + ")
end

local function clickButton(key)
  local b = clickBtns[key]
  if b or inCombat() or type(_G.CreateFrame) ~= "function" then return b end
  local ok, f = pcall(_G.CreateFrame, "Button", "TreeAIMacro_" .. key, _G.UIParent, "SecureActionButtonTemplate")
  if not ok or not f then return nil end
  if f.RegisterForClicks then f:RegisterForClicks("AnyUp", "AnyDown") end
  f:SetAttribute("useOnKeyDown", false)
  f:Hide()
  clickBtns[key] = f
  return f
end

--- (hors combat) réécrit le texte des boutons des macros « clic » activées / installées
function M:UpdateClickButtons()
  if inCombat() then return end
  for _, def in ipairs(self:Defs()) do
    local key = clickKeyOf(def)
    local src = key and CLICK_SRC[key]
    if src and (char().installed[def.n] or self:IsOn(def)) then
      local b = clickButton(key)
      if b then
        local text, info = src()
        local cs = M._clickState[key] or {}
        M._clickState[key] = cs
        local want = text or ""
        if cs.text ~= want then
          b:SetAttribute("type", text and "macro" or nil)
          b:SetAttribute("macrotext", text)
          cs.text = want
          cs.at = text and stamp() or nil
        end
        cs.info = text and info or nil
        cs.why = (not text) and info or nil
      end
    end
  end
end
local clickPending = false
function M:ScheduleClickButtons(delay)
  if clickPending then return end
  clickPending = true
  after(delay or 1, function()
    clickPending = false
    runOOC("Macros:click", function() M:UpdateClickButtons() end)
  end)
end

---------------------------------------------------------------------------
-- (MACRO3) Renommer : petite fenêtre à nous (zone de texte), la validation est un clic (événement matériel)
---------------------------------------------------------------------------
function M:Rename(def, newName)
  newName = trim(tostring(newName or ""))
  if newName == "" then newName = def.n end
  if #newName > 16 then say("Nom trop long : 16 caractères au plus."); return false end
  local old = gname(def)
  if newName == old then return true end
  if macroIndex(newName) then say("Une macro s'appelle déjà « " .. newName .. " »."); return false end
  local idx = macroIndex(old)
  if idx then
    if inCombat() then say("Impossible en combat."); return false end
    if T.InUserAction and not T:InUserAction() then return false end
    local ok = pcall(_G.EditMacro, idx, newName, nil, nil)
    invalidateIndex()
    M.DirtyPlacement()
    if not ok or not macroIndex(newName) then say("Le jeu a refusé le nouveau nom."); return false end
  end
  self:SetOpt(def, "name", newName ~= def.n and newName or nil)
  say(("« %s » s'appelle maintenant « %s »."):format(old, newName))
  return true
end

function M:AskRename(def)
  if not (def and _G.CreateFrame) then return end
  if inCombat() then say("Impossible en combat."); return end
  local f = self.renameBox
  if not f then
    f = _G.CreateFrame("Frame", nil, _G.UIParent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
    f:SetSize(320, 112)
    f:SetPoint("CENTER", 0, 120)
    f:SetFrameStrata("DIALOG")
    if f.SetClampedToScreen then f:SetClampedToScreen(true) end
    f:EnableMouse(true)
    if f.SetBackdrop then
      f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
        insets = { left = 3, right = 3, top = 3, bottom = 3 } })
      f:SetBackdropColor(0, 0, 0, 0.92)
    end
    if T.Theme then pcall(T.Theme.Skin, T.Theme, f, "window") end
    f.title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    f.title:SetPoint("TOPLEFT", 12, -10); f.title:SetWidth(296); f.title:SetJustifyH("LEFT")
    f.hint = f:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    f.hint:SetPoint("TOPLEFT", 12, -28); f.hint:SetWidth(296); f.hint:SetJustifyH("LEFT")
    f.hint:SetText("16 caractères au plus ; vide = nom d'origine.")
    f.edit = _G.CreateFrame("EditBox", nil, f, "InputBoxTemplate")
    f.edit:SetSize(280, 20); f.edit:SetPoint("TOPLEFT", 18, -48)
    f.edit:SetAutoFocus(false)
    if f.edit.SetMaxLetters then f.edit:SetMaxLetters(16) end
    f.edit:SetScript("OnEscapePressed", function() f:Hide() end)
    f.ok = mkButton(f, "Renommer", 120); f.ok:SetPoint("BOTTOMLEFT", 12, 10)
    f.ok:SetScript("OnClick", function()
      if f.def and userDo(function() if M:Rename(f.def, f.edit:GetText()) then f:Hide() end end) then
        if T.Window and T.Window.Dirty then T.Window.Dirty() end
      end
    end)
    f.cancel = mkButton(f, "Annuler", 120); f.cancel:SetPoint("BOTTOMRIGHT", -12, 10)
    f.cancel:SetScript("OnClick", function() f:Hide() end)
    self.renameBox = f
  end
  f.def = def
  f.title:SetText("Renommer « " .. gname(def) .. " »")
  f.edit:SetText(gname(def))
  f:Show()
  if f.edit.SetFocus then f.edit:SetFocus() end
end

---------------------------------------------------------------------------
-- (MACRO3) Page « Macros » de la fenêtre TreeAI : pastilles, réglages, préréglages, « Tout mettre à jour »
---------------------------------------------------------------------------
local ICONS = "Interface\\Icons\\"
local ORDER = { bad = 1, warn = 2, good = 3, muted = 4 }
local function yesNo(v) return v and "oui" or "non" end

local function tipFor(st)
  local def = st.def
  local t = { def.d or "" }
  t[#t + 1] = "État : " .. st.text
  if st.autoText then t[#t + 1] = st.autoText end
  t[#t + 1] = st.onBar and ("Sur ta barre" .. (st.key and (" · touche " .. keyText(st.key)) or " · sans touche"))
    or "Pas encore sur une barre : « Sur le curseur » dans ses réglages"
  if def.t and def.t ~= "" then t[#t + 1] = "Astuce Pro : " .. def.t end
  if st.expected then
    local first = st.expected:gsub("\n", "  ·  ")
    if T.Cut then first = T.Cut(first, 160) end   -- coupe UTF-8 (jamais une lettre accentuée coupée)
    t[#t + 1] = "|cff9d9d9d" .. first .. "|r"
  end
  return t
end

local function clickOf(st)
  local def = st.def
  if st.code == "install" or st.code == "update" or st.code == "frozen" then
    return function() M._pageSel = def.n; M:UpdateOne(def) end,
      st.code == "install" and "Clic : l'installer · clic droit : la désactiver"
        or "Clic : la mettre à jour · clic droit : la désactiver"
  elseif st.code == "user" then
    return function()
      M._pageSel = def.n
      if _G.IsShiftKeyDown and _G.IsShiftKeyDown() then M:UpdateOne(def, true) end
    end, "Clic : ses réglages · Maj+clic : remplacer ta version par celle de TreeAI"
  elseif st.code == "blocked" or st.code == "full" then
    return function() M._pageSel = def.n; M:ShowCopy({ { def = def, body = st.expected } }) end,
      "Clic : son texte à copier-coller"
  end
  return function() M._pageSel = (M._pageSel ~= def.n) and def.n or nil end,
    "Clic : ses réglages · clic droit : " .. (st.on and "la désactiver" or "l'activer")
end

local function toggleOn(def)
  local on = not M:IsOn(def)
  M:SetOpt(def, "on", on)
  if on and not inCombat() then M:UpdateOne(def) end
  say(("%s %s."):format(gname(def), on and "activée" or "désactivée (rien n'est supprimé)"))
end

local function optRow(Ly, label, value, onClick, hint, icon)
  Ly.row({ text = label .. " : " .. value, icon = icon or (ICONS .. "INV_Misc_Gear_01"), onClick = onClick,
    clickHint = hint, kind = "info" })
end

function M:RenderPage(Ly)
  local W = T.Window
  if not self:IsEnabled() then
    Ly.empty("Le module Macros est coupé.", { "Allumer « Macros »", function() T:SetModuleEnabled("Macros", true) end,
      "Clic : allume les macros (sans /reload)" })
    return
  end
  local list, n, bagCtx = self:States()
  self._lastStates = list
  local spec, autoSpec = self:Spec()
  local where, autoCtx = self:Context()
  local p = self:Preset()
  Ly.hero(ICONS .. "INV_Misc_Note_01", "Tes macros TreeAI",
    ("%d %s · %d à installer ou mettre à jour%s"):format(n.ok, pl(n.ok, "active", "actives"), n.todo,
      n.err > 0 and (" · " .. n.err .. " en erreur") or ""),
    ("Préréglage %s · %s%s · %s%s%s"):format(PRESETS[p].label, self:SpecName(spec), autoSpec and " (auto)" or "",
      where, autoCtx and " (auto)" or "", n.nobar > 0 and (" · " .. n.nobar .. " pas sur une barre") or ""),
    n.todo > 0 and function() M:UpdateAll() end or nil,
    { "Vert : active et à jour. Orange : à installer ou à mettre à jour (clic).",
      "Gris : désactivée, plus tard, ou modifiée par toi. Rouge : erreur (raison dans la ligne).",
      "Rien n'est écrit sans ton clic, jamais en combat ; une macro modifiée à la main n'est jamais écrasée." },
    n.todo > 0 and "Clic : tout mettre à jour" or nil)
  local presetBtns = {}
  local ask = M._presetAsk
  local asking = ask and (now() - ask.t) < 8 and ask.p or nil
  for _, id in ipairs(M.PRESET_ORDER) do
    local P = PRESETS[id]
    local label = (id == p) and ("« " .. P.label .. " »") or (asking == id and ("Confirmer « " .. P.label .. " »") or P.label)
    presetBtns[#presetBtns + 1] = { label, function() M:ApplyPreset(id) end,
      P.tip .. (id == p and " (préréglage en cours)" or " Clic, puis clic encore : l'appliquer (écrit tout de suite).") }
  end
  Ly.buttons(presetBtns, 3)
  table.sort(list, function(a, b)
    if a.on ~= b.on then return a.on end
    local oa, ob = ORDER[a.badge] or 9, ORDER[b.badge] or 9
    if oa ~= ob then return oa < ob end
    if (a.def.l or 1) ~= (b.def.l or 1) then return (a.def.l or 1) < (b.def.l or 1) end
    return a.name < b.name
  end)
  local shown, sel = 0, nil
  local on, off = {}, {}
  for _, st in ipairs(list) do
    if st.def.n == M._pageSel then sel = st end
    if W.Match(st.name, st.text, st.def.d, st.def.t) then
      local t = st.on and on or off
      t[#t + 1] = st
    end
  end
  local function row(st)
    local fn, hint = clickOf(st)
    local def = st.def
    local right = st.onBar and (st.key and ("touche " .. keyText(st.key)) or "sans touche") or (st.code == "ok" and "pas sur une barre" or "")
    Ly.row({ text = M.Dot(st.badge) .. " " .. st.name .. "  |cffb0b0b0" .. st.text
        .. (st.autoText and (" · " .. st.autoText) or "") .. "|r",
      right = right, icon = iconOf(def, bagCtx), tooltip = tipFor(st), onClick = fn, clickHint = hint,
      onRightClick = function() toggleOn(def) end, kind = "info" })
    shown = shown + 1
  end
  if #on > 0 and Ly.header("Tes macros", "macros:list", #on, ICONS .. "INV_Misc_Note_01") then
    for _, st in ipairs(on) do row(st) end
  end
  if #off > 0 and Ly.header("Autres macros (clic droit : activer)", "macros:off", #off, ICONS .. "INV_Misc_Note_02", true) then
    for _, st in ipairs(off) do row(st) end
  end
  if shown == 0 and W.Query() ~= "" then Ly.empty("Rien ne correspond à « " .. W.Query() .. " ».") end
  if sel then self:RenderSettings(Ly, sel) end
  if Ly.header("Plus", "macros:more", nil, ICONS .. "INV_Misc_Wrench_01", true) then
    Ly.buttons({
      { "Liste détaillée", function() M:ToggleList() end, "L'ancien panneau : glisser une ligne sur ta barre, diagnostic" },
      { "Copier-coller", function() M:ShowCopy(M:PlanList(false)) end, "Le texte de chaque macro, si le jeu bloque la création" },
      { "Ouvrir les macros du jeu", function() openMacroUI() end, "La fenêtre /macro du jeu" },
      { "Diagnostic", function() M:Diagnose(true) end, "Chaque macro : installée ou non, et pourquoi" },
      { "Retirer les macros TAI", function() M:Remove() end, "Supprime les macros TAI non modifiées (les tiennes restent)" },
    }, 3)
  end
end

--- réglages de la macro choisie (une ligne = un réglage, clic = le changer ; la macro installée suit tout de suite)
function M:RenderSettings(Ly, st)
  local def = st.def
  local f = def.f or ""
  if not Ly.header("Réglages : " .. st.name, "macros:set", nil, iconOf(def)) then return end
  local function set(key, v)
    return function()
      M:SetOpt(def, key, v)
      if key == "icon" then M:SetOpt(def, "iconDirty", true) end
      local r = M:ApplyNow(def)
      say(("%s : %s%s."):format(gname(def), (key == "auto" and (v and "mise à jour auto" or "figée")) or "réglage changé",
        (r == "updated" or r == "created") and ", macro réécrite" or ""))
    end
  end
  optRow(Ly, "Activée", yesNo(st.on), function() toggleOn(def) end,
    st.on and "Clic : la désactiver (rien n'est supprimé)" or "Clic : l'activer (installée tout de suite)")
  optRow(Ly, "Mise à jour auto", st.auto and ("oui" .. (st.stamp and (" · dernière écriture " .. ago(st.stamp)) or "")) or "non (figée)",
    set("auto", not st.auto), "TreeAI la réécrit quand ton sac, tes sorts ou tes rangs changent (un clic sur Forever)")
  if f:find("m", 1, true) then
    local v = M:Opt(def, "mods") ~= false
    optRow(Ly, "Variantes Maj / Ctrl / Alt", yesNo(v), set("mods", not v), "Clic : " .. (v and "les retirer" or "les remettre"))
  end
  if f:find("o", 1, true) then
    local v = M:Opt(def, "mo") ~= false
    optRow(Ly, "Survol (@mouseover)", yesNo(v), set("mo", not v),
      "Oui : agit sur le monstre / l'allié survolé sans changer de cible")
  end
  if f:find("p", 1, true) then
    local v = M:Opt(def, "pet") ~= false
    optRow(Ly, "Ordres au familier", yesNo(v), set("pet", not v), "Les lignes /petattack, /petfollow de la macro")
  end
  if f:find("g", 1, true) then
    local v = M:Opt(def, "guard") ~= false
    optRow(Ly, "Aspect du faucon automatique", yesNo(v), set("guard", not v),
      "Oui : sans Aspect du faucon, l'appui le remet d'abord (« ! » : jamais coupé). Les aspects prennent le temps de "
      .. "recharge global : l'action suit à l'appui d'après. Un addon ne peut pas le faire sans ton appui.")
  end
  if f:find("t", 1, true) then
    local t = tonumber(M:Opt(def, "tank")) or 1
    optRow(Ly, "Tank à assister", "groupe " .. t, set("tank", (t % 4) + 1), "Clic : le membre suivant (groupe 1 à 4)")
  end
  if def.vl and #def.vl > 1 then
    local pin = tonumber(M:Opt(def, "var"))
    local label = pin and def.vl[pin] and (def.vl[pin] .. " (choisie)")
      or ("auto" .. (st.variant and def.vl[st.variant] and (" (" .. def.vl[st.variant] .. ")") or ""))
    local nextPin = (not pin and 1) or (pin < #def.vl and pin + 1) or nil
    optRow(Ly, "Variante", label, set("var", nextPin), "Clic : la suivante (auto = la meilleure que tu connais)")
  end
  optRow(Ly, "Nom", st.name, function() M:AskRename(def) end, "Clic : la renommer (16 caractères au plus)")
  local fixed = M:Opt(def, "icon") == "fixed"
  optRow(Ly, "Icône", fixed and "fixe (sort principal)" or "suit le sort (« ? »)", set("icon", (not fixed) and "fixed" or nil),
    "« ? » : l'icône montre le sort qui partira ; fixe : toujours la même")
  if st.expected then Ly.text(st.expected, "S", "text", 6) end
  if def.w and def.w ~= "" then Ly.text(def.w, "S", "muted") end
  if def.t and def.t ~= "" then Ly.text("Astuce Pro : " .. def.t, "S", "gold") end
  local btns = {}
  if st.code ~= "ok" and st.expected then btns[#btns + 1] = { st.installed and "Mettre à jour" or "Installer",
    function() M:UpdateOne(def, (_G.IsShiftKeyDown and _G.IsShiftKeyDown()) and true or false) end, "Clic : l'écrire maintenant" } end
  btns[#btns + 1] = { "Sur le curseur", function() M:PickToBar(def) end, "Clic : la macro sur ton curseur, pose-la sur ta barre" }
  if st.expected then btns[#btns + 1] = { "Copier-coller", function() M:ShowCopy({ { def = def, body = st.expected } }) end,
    "Son texte, à coller dans la fenêtre /macro" } end
  Ly.buttons(btns, 3)
end

---------------------------------------------------------------------------
-- Événements
---------------------------------------------------------------------------
local pendingCons, pendingSpells = false, false

local function scheduleConsumables()
  local c = M.char
  if type(c) == "table" and type(c.installed) == "table" and c.installed["TAI Pro Quete"] then M:ScheduleClickButtons(1) end
  if pendingCons or db().autoConsumables == false or not char().setup then return end
  pendingCons = true
  after(1, function() pendingCons = false; M:Refresh(true, nil, true) end)
end

local function scheduleSpells()
  resolveCache, bookNames = {}, nil
  M:ForgetMemo()   -- talents / spé / groupe : relus tout de suite
  if pendingSpells or db().autoUpgrade == false or not char().setup then return end
  pendingSpells = true
  after(2, function()
    pendingSpells = false
    -- (4.0, perf) UN relevé des sacs pour les deux passes, et la 2e passe dans une autre image (niveau gagné :
    -- beaucoup de modules travaillent dans la même seconde)
    local ctx = newContext()
    after(0, function() M:Refresh(false, ctx, true) end)   -- (4.0.1) relevé des sacs et rendu : deux images
    after(0.5, function()
      -- nouvelles macros par défaut devenues possibles (sort appris)
      local fresh = {}
      local installed = char().installed
      for _, def in ipairs(M:Defs()) do
        if def.c ~= "consumable" and not installed[def.n] and M:IsDefault(def)
          and not macroIndex(gname(def)) and M:Render(def, ctx) then
          fresh[#fresh + 1] = def.n
        end
      end
      if #fresh > 0 then
        suggestReady(READY_TEXT, fresh)
      end
    end)
  end)
end

--- (MACRO3) la page « Macros » de la fenêtre TreeAI (après « Outils », avant « Fonctions » dans la navigation)
function M:RegisterPage()
  local W = T.Window
  if not (W and type(W.AddPage) == "function") then return false end
  W.AddPage({ id = "macros", label = "Macros", icon = "Interface\\Icons\\INV_Misc_Note_01", title = "Macros",
    before = "modules", desc = "Tes macros : actives, à mettre à jour, réglages, préréglages Simple / Complet / Pro",
    sub = function()
      local p = M:Preset()
      local spec = M:Spec()
      return ("Préréglage %s · %s · rien n'est écrit sans ton clic"):format(PRESETS[p].label, M:SpecName(spec))
    end,
    badge = function()
      if not M._active then return nil end
      local n = 0
      for _, st in ipairs(M._lastStates or {}) do if st.on and (st.badge == "warn" or st.badge == "bad") then n = n + 1 end end
      return n > 0 and n or nil
    end,
    render = function(Ly) M:RenderPage(Ly) end,
    action = function()
      local n = 0
      for _, st in ipairs(M._lastStates or {}) do if st.badge == "warn" then n = n + 1 end end
      if n > 0 then
        return ("Tout mettre à jour (%d)"):format(n), function() M:UpdateAll() end,
          "Clic : installe les macros activées qui manquent et réécrit celles qui ont changé (hors combat)"
      end
    end,
    status = function() return M.lastMsg or "Rien n'est écrit sans ton clic ; une macro modifiée à la main n'est jamais écrasée." end,
  })
  return true
end

function M:OnEnable()
  -- (LITE) profil essentiel : le panneau Macros seulement, pas de relecture des sacs / de l'équipement en fond
  if not (T.Lite and T:Lite()) then
    self:RegisterEvent("BAG_UPDATE_DELAYED", scheduleConsumables)
    -- (4.0.1) « player » seulement, filtré par le cœur avant tout appel (repli : test dans le handler)
    local function playerEvent(ev)
      if self.RegisterUnitEvent then return self:RegisterUnitEvent(ev, "player", nil, function() scheduleConsumables() end) end
      self:RegisterEvent(ev, function(_, _, unit) if unit == nil or unit == "player" then scheduleConsumables() end end)
    end
    playerEvent("UNIT_INVENTORY_CHANGED")
    self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", scheduleConsumables)
    playerEvent("UNIT_PET")
  end
  -- niveau gagné : la passe complète (sorts) couvre aussi les consommables ; sans elle, les consommables seuls
  self:RegisterEvent("PLAYER_LEVEL_UP", function()
    if db().autoUpgrade == false then scheduleConsumables() else scheduleSpells() end
  end)
  self:RegisterEvent("SKILL_LINES_CHANGED", scheduleConsumables)
  self:RegisterEvent("SPELLS_CHANGED", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_TAB", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_SKILL_LINE", scheduleSpells)   -- nom Forever / Mainline
  self:RegisterEvent("ACTIVE_TALENT_GROUP_CHANGED", scheduleSpells)
  self:RegisterEvent("CHARACTER_POINTS_CHANGED", scheduleSpells)
  self:RegisterEvent("ADDON_ACTION_FORBIDDEN", M._OnForbidden)   -- le jeu a refusé CreateMacro / EditMacro
  -- (FIDELITY) table nom -> index à relire ; (MACRO3) relevé des barres / touches à refaire (page ouverte : redessin)
  local function pageDirty()
    M.DirtyPlacement()
    local W = T.Window
    if W and W.IsOpen and W.IsOpen() and W.CurrentPage and W.CurrentPage() == "macros" and W.Dirty then W.Dirty() end
  end
  self:RegisterEvent("UPDATE_MACROS", function() invalidateIndex() pageDirty() end)
  self:RegisterEvent("ACTIONBAR_SLOT_CHANGED", pageDirty)
  self:RegisterEvent("UPDATE_BINDINGS", pageDirty)
  if not data() then return end
  self:RegisterPage()
  -- (MACRO3) macros « Pro » dynamiques : boutons cachés réécrits hors combat (rare repéré, objectif + objet de quête)
  local Dt = T.Detect
  if Dt and type(Dt.OnChange) == "function" and not M._detectSub then
    M._detectSub = Dt.OnChange(function(kind)
      if (kind == "rares" and char().installed["TAI Pro Rare"])
        or ((kind == "quests" or kind == "questunits") and char().installed["TAI Pro Quete"]) then
        M:ScheduleClickButtons(1)
      end
    end)
  end
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function()
    if char().installed["TAI Pro Rare"] or char().installed["TAI Pro Quete"] then M:ScheduleClickButtons(1) end
  end)
  after(5, function() runOOC("Macros:click", function() M:UpdateClickButtons() end) end)
  if self:IsBlocked() and not char().setup then
    suggestReady("Macros prêtes à copier-coller : clique pour les voir")
  elseif char().setup then
    after(3, function() M:Refresh(false, nil, true) end)
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
  elseif cmd == "solo" or cmd == "groupe" then char().ctx = cmd; M:ForgetMemo(); say("Contexte : " .. cmd)
  elseif cmd == "diag" or cmd == "diagnostic" then M:Diagnose(true)
  elseif cmd == "copier" or cmd == "copie" or cmd == "copy" then M:ShowCopy(M:PlanList(cmd == "tout"))
  elseif cmd == "auto" then char().ctx, char().spec = nil, nil; M:ForgetMemo(); say("Spé et contexte automatiques.")
  else M:TogglePanel() end
end
M.Slash = slash
if T.RegisterSlash then
  T:RegisterSlash("macros", slash,
    "macros [installer|tout|maj|forcer|liste|retirer|diag|copier|spe|solo|groupe|auto] : macros expertes")
end
