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
local function resolveSpell(id)
  local D = data()
  local fr = D and D.spells and D.spells[id]
  local name = spellName(id)
  if D and D.pet and D.pet[id] then return name or fr, true end
  if name and spellKnownId(id) then return name, true end
  -- ID inconnu ou déplacé (classes refaites dans Forever) : test par nom dans le grimoire
  local byName = (name and spellName(name)) or (fr and spellName(fr))
  if byName then return byName, true end
  return name or fr, false
end
M.ResolveSpell = resolveSpell

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

function M:Defs()
  local D = data()
  local cls = playerClass()
  return (D and D.classes and cls and D.classes[cls]) or {}
end

---------------------------------------------------------------------------
-- Écriture des macros (hors combat uniquement)
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

-- write a macro; returns "created" | "updated" | "same" | "user" | "full" | "fail"
function M:Write(def, body, force)
  if inCombat() then return "fail" end
  local ch = char()
  local idx = macroIndex(def.n)
  if idx then
    local _, _, cur = try(_G.GetMacroInfo, idx)
    if cur == body then ch.bodies[def.n] = body; return "same" end
    -- macro modifiée à la main par le joueur : on n'y touche plus (sauf forçage)
    if not force and ch.bodies[def.n] and cur and cur ~= ch.bodies[def.n] then return "user" end
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

-- Installe les macros par défaut (+ celles déjà installées) ; all=true : toutes.
function M:Install(all, force)
  runOOC("Macros:install", function()
    local ctx = newContext()
    local defs = self:Defs()
    local n, full, user = 0, 0, 0
    for _, def in ipairs(defs) do
      if def.def or all or char().installed[def.n] then
        local body = self:Render(def, ctx)
        if body then
          local r = self:Write(def, body, force)
          if r == "created" or r == "updated" then n = n + 1
          elseif r == "full" then full = full + 1
          elseif r == "user" then user = user + 1 end
        end
      end
    end
    char().setup = true
    if self.Clear then self:Clear("setup") end
    say(("%d macro(s) installée(s)/mise(s) à jour."):format(n))
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

local function usesItems(def)
  if def.c == "consumable" then return true end
  for _, v in ipairs(def.v or {}) do
    if (v.b or ""):find("{item:", 1, true) then return true end
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
        if body then self:Write(def, body, false) end
      end
    end
  end)
end

function M:Remove()
  runOOC("Macros:remove", function()
    local n = 0
    for name in pairs(char().installed) do
      local idx = macroIndex(name)
      if idx and name:sub(1, #PREFIX) == PREFIX then
        if pcall(_G.DeleteMacro, idx) then n = n + 1 end
      end
    end
    char().installed, char().bodies, char().setup = {}, {}, false
    say(("%d macro(s) TreeAI supprimée(s)."):format(n))
  end)
end

function M:List()
  local ctx = newContext()
  for _, def in ipairs(self:Defs()) do
    local body = self:Render(def, ctx)
    local state = char().installed[def.n] and "installée" or (body and "dispo" or "pas encore")
    say(("%s [%s] %s — %s"):format(def.n, def.k or "-", state, def.d or ""))
  end
end

---------------------------------------------------------------------------
-- Petit panneau : clic = prendre la macro sur le curseur (à poser dans une barre)
---------------------------------------------------------------------------
function M:TogglePanel()
  if self.panel then
    if self.panel:IsShown() then self.panel:Hide() else self:FillPanel(); self.panel:Show() end
    return
  end
  if not _G.CreateFrame then return end
  local f = _G.CreateFrame("Frame", nil, _G.UIParent, _G.BackdropTemplateMixin and "BackdropTemplate" or nil)
  f:SetSize(380, 60)
  f:SetPoint("CENTER")
  f:SetFrameStrata("DIALOG")
  f:SetMovable(true); f:EnableMouse(true); f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving); f:SetScript("OnDragStop", f.StopMovingOrSizing)
  if f.SetBackdrop then
    f:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 } })
    f:SetBackdropColor(0, 0, 0, 0.85)
  end
  local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", 10, -8)
  title:SetText("Macros TreeAI — clic : prendre, glisser dans une barre")
  local close = _G.CreateFrame("Button", nil, f, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 2, 2)
  local inst = _G.CreateFrame("Button", nil, f, "UIPanelButtonTemplate")
  inst:SetSize(90, 20); inst:SetPoint("BOTTOMRIGHT", -8, 6); inst:SetText("Installer")
  inst:SetScript("OnClick", function() M:Install(false); after(0.5, function() M:FillPanel() end) end)
  f.rows = {}
  self.panel = f
  self:FillPanel()
end

function M:FillPanel()
  local f = self.panel
  if not f then return end
  local defs = self:Defs()
  for i, def in ipairs(defs) do
    local row = f.rows[i]
    if not row then
      row = _G.CreateFrame("Button", nil, f)
      row:SetSize(360, 16)
      row:SetPoint("TOPLEFT", 10, -26 - (i - 1) * 17)
      row.text = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
      row.text:SetAllPoints(); row.text:SetJustifyH("LEFT")
      row:SetScript("OnClick", function(b)
        if inCombat() then return end
        local idx = b.macroName and macroIndex(b.macroName)
        if idx then try(_G.PickupMacro, idx) end
      end)
      row:SetScript("OnEnter", function(b)
        if not _G.GameTooltip or not b.desc then return end
        _G.GameTooltip:SetOwner(b, "ANCHOR_RIGHT")
        _G.GameTooltip:SetText(b.macroName or "")
        _G.GameTooltip:AddLine(b.desc, 1, 1, 1, true)
        _G.GameTooltip:Show()
      end)
      row:SetScript("OnLeave", function() if _G.GameTooltip then _G.GameTooltip:Hide() end end)
      f.rows[i] = row
    end
    local installed = char().installed[def.n] and macroIndex(def.n)
    row.macroName, row.desc = def.n, def.d
    row.text:SetText(("%s%s|r  [%s]  %s"):format(installed and "|cff40ff40" or "|cff888888",
      def.n, def.k or "-", (def.d or ""):sub(1, 48)))
    row:Show()
  end
  for i = #defs + 1, #f.rows do f.rows[i]:Hide() end
  f:SetHeight(40 + #defs * 17 + 10)
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
  if pendingSpells or db().autoUpgrade == false or not char().setup then return end
  pendingSpells = true
  after(2, function()
    pendingSpells = false
    M:Refresh(false)
    -- nouvelles macros par défaut devenues possibles (sort appris)
    local ctx, fresh = newContext(), {}
    for _, def in ipairs(M:Defs()) do
      if def.def and def.c ~= "consumable" and not char().installed[def.n] and M:Render(def, ctx) then
        fresh[#fresh + 1] = def.n
      end
    end
    if #fresh > 0 then
      suggest("new", "Nouvelle(s) macro(s) dispo : " .. tconcat(fresh, ", ") .. " (/tai macros installer)", 2,
        { ttl = 300, onClick = function() M:Install(false) end })
    end
  end)
end

function M:OnEnable()
  self:RegisterEvent("BAG_UPDATE_DELAYED", scheduleConsumables)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() scheduleConsumables(); scheduleSpells() end)
  self:RegisterEvent("SKILL_LINES_CHANGED", scheduleConsumables)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", scheduleConsumables)
  self:RegisterEvent("UNIT_PET", scheduleConsumables)
  self:RegisterEvent("SPELLS_CHANGED", scheduleSpells)
  self:RegisterEvent("LEARNED_SPELL_IN_TAB", scheduleSpells)
  if not data() then return end
  if char().setup then
    after(3, function() M:Refresh(false) end)
  else
    suggest("setup", "Macros intelligentes prêtes pour ta classe : clique ou /tai macros installer", 2,
      { ttl = 0, onClick = function() M:Install(false) end })
  end
end

---------------------------------------------------------------------------
-- Slash : /tai macros [installer|tout|maj|forcer|liste|retirer]
---------------------------------------------------------------------------
local function slash(rest)
  local cmd = trim((rest or ""):lower())
  if cmd == "installer" or cmd == "install" then M:Install(false)
  elseif cmd == "tout" or cmd == "all" then M:Install(true)
  elseif cmd == "forcer" then M:Install(false, true)
  elseif cmd == "maj" or cmd == "update" then M:Refresh(false)
  elseif cmd == "liste" or cmd == "list" then M:List()
  elseif cmd == "retirer" or cmd == "remove" then M:Remove()
  else M:TogglePanel() end
end
M.Slash = slash
if T.RegisterSlash then
  T:RegisterSlash("macros", slash, "macros [installer|tout|maj|forcer|liste|retirer] : macros intelligentes")
end
