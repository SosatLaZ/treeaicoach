-- TreeAI Coach - core: events, modules, SavedVariables, suggestions, comms.
-- Conseils uniquement: aucune automatisation d'action (ToS Blizzard).
-- Lua 5.1. Globales: TreeAI, TreeAIDB, SLASH_TREEAI1/2.

local ADDON_NAME = ...
if type(ADDON_NAME) ~= "string" then ADDON_NAME = "TreeAI" end

local _G = _G
local type, pairs, ipairs, tostring, tonumber, select = type, pairs, ipairs, tostring, tonumber, select
local pcall, setmetatable, next = pcall, setmetatable, next
local tinsert, tremove, tsort, tconcat = table.insert, table.remove, table.sort, table.concat
local strsub, strfind, strgsub, strformat, strbyte, strchar = string.sub, string.find, string.gsub, string.format, string.byte, string.char
local mmax, mmin = math.max, math.min

local TreeAI = _G.TreeAI or {}
_G.TreeAI = TreeAI

TreeAI.name = ADDON_NAME
TreeAI.version = "4.3.3"
TreeAI.SCHEMA = 1
TreeAI.PREFIX = "TREEAI"
TreeAI.PRIO_INFO, TreeAI.PRIO_TIP, TreeAI.PRIO_URGENT = 1, 2, 3
TreeAI.Data = TreeAI.Data or {}
TreeAI.modules = TreeAI.modules or {}
TreeAI.moduleOrder = TreeAI.moduleOrder or {}
TreeAI.API = TreeAI.API or {}

-- Locale: L[key] returns the key itself when missing.
TreeAI.L = TreeAI.L or setmetatable({}, { __index = function(_, k) return k end })
local L = TreeAI.L

local function now()
  if _G.GetTime then return _G.GetTime() end
  return 0
end
local function wallclock()
  if _G.time then return _G.time() end
  return 0
end

---------------------------------------------------------------------------
-- Printing
---------------------------------------------------------------------------
local function joinArgs(...)
  local n = select("#", ...)
  local t = {}
  for i = 1, n do t[i] = tostring((select(i, ...))) end
  return tconcat(t, " ")
end

function TreeAI:Print(...)
  local msg = "|cff33cc66TreeAI|r: " .. joinArgs(...)
  local cf = _G.DEFAULT_CHAT_FRAME
  if cf and cf.AddMessage then cf:AddMessage(msg) elseif _G.print then _G.print(msg) end
end

function TreeAI:Debug(...)
  local db = _G.TreeAIDB
  if db and db.settings and db.settings.debug then
    self:Print("|cff999999[debug]|r", ...)
  end
end

local function safecall(fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then
    TreeAI:Debug("erreur:", err)
    local hook = TreeAI._errorHook   -- SelfTest : garde les 30 dernières erreurs (TreeAIDB.diag.errors)
    if hook then pcall(hook, err, TreeAI._ctx) end
  end
  return ok, err
end
TreeAI.safecall = safecall

---------------------------------------------------------------------------
-- Defensive API wrappers (Forever = Mainline family: C_* APIs; Era: globals)
---------------------------------------------------------------------------
local API = TreeAI.API
local C_Container, C_Spell, C_Item, C_UnitAuras, C_AddOns, C_Timer =
  _G.C_Container, _G.C_Spell, _G.C_Item, _G.C_UnitAuras, _G.C_AddOns, _G.C_Timer

local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h, i, j, k, l, m, n = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h, i, j, k, l, m, n end
  return nil
end

-- Valeurs secrètes (Forever = famille Midnight) : en combat, certaines valeurs (PV d'une autre unité, auras,
-- GUID / nom d'un monstre…) sont « secrètes » : les comparer, les additionner ou s'en servir de clé lève une
-- erreur Lua. API.Pub(v) rend nil pour une valeur secrète (et v sinon) : à mettre autour de ces lectures.
function API.IsSecret(v)
  local f = _G.issecretvalue
  if type(f) ~= "function" or v == nil then return false end
  local ok, r = pcall(f, v)
  return ok and r and true or false
end
function API.Pub(v)
  if v ~= nil and API.IsSecret(v) then return nil end
  return v
end
local pub = API.Pub

function API.SpellName(id)
  if id == nil then return nil end
  if C_Spell and C_Spell.GetSpellInfo then
    local info = try(C_Spell.GetSpellInfo, id)
    if type(info) == "table" then return info.name end
    if C_Spell.GetSpellName then
      local n = try(C_Spell.GetSpellName, id)
      if n then return n end
    end
  end
  return (try(_G.GetSpellInfo, id))
end

function API.SpellIcon(id)
  if id == nil then return nil end
  if C_Spell and C_Spell.GetSpellInfo then
    local info = try(C_Spell.GetSpellInfo, id)
    if type(info) == "table" then return info.iconID end
  end
  local _, _, icon = try(_G.GetSpellInfo, id)
  return icon
end

function API.IsSpellKnown(id)
  if type(id) ~= "number" then return false end
  local SB = _G.C_SpellBook   -- Forever : C_SpellBook (IsPlayerSpell / IsSpellKnown globaux = repli déprécié)
  if SB and SB.IsSpellKnown and try(SB.IsSpellKnown, id) then return true end
  if _G.IsPlayerSpell and try(_G.IsPlayerSpell, id) then return true end
  if _G.IsSpellKnown and try(_G.IsSpellKnown, id) then return true end
  if C_Spell and C_Spell.IsSpellKnown and try(C_Spell.IsSpellKnown, id) then return true end
  return false
end

function API.SpellCooldown(id)
  if C_Spell and C_Spell.GetSpellCooldown then
    local info = try(C_Spell.GetSpellCooldown, id)
    if type(info) == "table" then return info.startTime or 0, info.duration or 0 end
  end
  local s, d = try(_G.GetSpellCooldown, id)
  return s or 0, d or 0
end

function API.ItemInfo(item)
  if item == nil then return nil end
  local fn = (C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
  return try(fn, item)
end

function API.ItemName(item)
  if C_Item and C_Item.GetItemNameByID and type(item) == "number" then
    local n = try(C_Item.GetItemNameByID, item)
    if n then return n end
  end
  return (API.ItemInfo(item))
end

function API.ItemCount(itemID, includeBank)
  local fn = (C_Item and C_Item.GetItemCount) or _G.GetItemCount
  return try(fn, itemID, includeBank) or 0
end

function API.NumBags()
  local n = _G.NUM_BAG_SLOTS or 4
  return n
end

function API.BagSlots(bag)
  local fn = (C_Container and C_Container.GetContainerNumSlots) or _G.GetContainerNumSlots
  return try(fn, bag) or 0
end

function API.BagFree(bag)
  local fn = (C_Container and C_Container.GetContainerNumFreeSlots) or _G.GetContainerNumFreeSlots
  local free, bagType = try(fn, bag)
  return free or 0, bagType or 0
end

function API.BagItem(bag, slot)
  if C_Container and C_Container.GetContainerItemInfo then
    local info = try(C_Container.GetContainerItemInfo, bag, slot)
    if type(info) ~= "table" then return nil end
    return {
      itemID = info.itemID, count = info.stackCount or 1, link = info.hyperlink,
      quality = info.quality, icon = info.iconFileID, locked = info.isLocked,
      noValue = info.hasNoValue,
    }
  end
  local icon, count, locked, quality, _, _, link, _, noValue, itemID = try(_G.GetContainerItemInfo, bag, slot)
  if not icon and not link then return nil end
  if not itemID and link then itemID = tonumber(link:match("item:(%d+)")) end
  return { itemID = itemID, count = count or 1, link = link, quality = quality,
           icon = icon, locked = locked, noValue = noValue }
end

function API.IterBags()
  local bag, slot, maxBag = 0, 0, API.NumBags()
  local slots = API.BagSlots(0)
  return function()
    while bag <= maxBag do
      slot = slot + 1
      if slot > slots then
        bag = bag + 1
        slot = 0
        if bag <= maxBag then slots = API.BagSlots(bag) end
      else
        local item = API.BagItem(bag, slot)
        if item then return bag, slot, item end
      end
    end
    return nil
  end
end

function API.FreeSlots()
  local free, total = 0, 0
  for bag = 0, API.NumBags() do
    local f, bagType = API.BagFree(bag)
    if bagType == 0 then -- general bags only (not quiver/ammo pouch/soul bag)
      free = free + f
      total = total + API.BagSlots(bag)
    end
  end
  return free, total
end

-- (LITE) calculs tirés des sacs mémorisés jusqu'au prochain changement de sacs (événement), un nombre de cases
-- libres différent, ou 10 s : API.BagMemo("clé", fn, ...) rend les mêmes valeurs que fn(...)
local bagGen, bagMemo = 0, {}
function API.BagGen() return bagGen end
function API.BagChanged() bagGen = bagGen + 1 end
local function pack(...) return { n = select("#", ...), ... } end
-- (4.0.1) relevé des sacs partagé dans UNE image, tant qu'aucun événement de sac n'est passé (lecture seule) : la
-- surbrillance et les quêtes relisent les sacs 2 s après le même butin, en général dans la même image : un relevé
-- pour les deux (et les macros). Les autres lecteurs gardent API.IterBags (relecture directe).
do
  local snapT, snapG, snap = nil, nil, nil
  function API.IterBagsShared()
    local t = now()
    if not (snap and snapT == t and snapG == bagGen) then
      local list, n = {}, 0
      for bag, slot, item in API.IterBags() do
        list[n + 1], list[n + 2], list[n + 3] = bag, slot, item
        n = n + 3
      end
      list.n = n
      snap, snapT, snapG = list, t, bagGen
    end
    local list, i = snap, -2
    return function()
      i = i + 3
      if i > list.n then return nil end
      return list[i], list[i + 1], list[i + 2]
    end
  end
end
function API.BagMemo(key, fn, ...)
  local free = API.FreeSlots()
  local t = now()
  local m = bagMemo[key]
  if m and m.gen == bagGen and m.free == free and t >= m.at and t - m.at < 10 then
    return unpack(m.r, 1, m.r.n)
  end
  local r = pack(fn(...))
  bagMemo[key] = { gen = bagGen, free = free, at = t, r = r }
  return unpack(r, 1, r.n)
end

-- Only call from a player click (merchant sell button), out of combat.
function API.UseContainerItem(bag, slot)
  if TreeAI:InCombat() then return false end
  local fn = (C_Container and C_Container.UseContainerItem) or _G.UseContainerItem
  local ok = pcall(fn, bag, slot)
  return ok
end

local function auraFromData(d)
  if type(d) ~= "table" then return nil end
  local name = pub(d.name)
  if name == nil and pub(d.spellId) == nil then return { secret = true } end   -- aura masquée (combat)
  return { name = name, icon = pub(d.icon), count = pub(d.applications) or 0, duration = pub(d.duration) or 0,
           expires = pub(d.expirationTime) or 0, spellId = pub(d.spellId), source = pub(d.sourceUnit) }
end

--- -> liste des auras lisibles, nombre d'auras masquées (valeurs secrètes en combat) : « illisible » ≠ « absente »
function API.Auras(unit, filter)
  filter = filter or "HELPFUL"
  local out, hidden = {}, 0
  for i = 1, 40 do
    local a
    if C_UnitAuras and C_UnitAuras.GetAuraDataByIndex then
      a = auraFromData(try(C_UnitAuras.GetAuraDataByIndex, unit, i, filter))
    elseif _G.UnitAura then
      local name, icon, count, _, duration, expires, source, _, _, spellId = try(_G.UnitAura, unit, i, filter)
      if name then
        a = { name = name, icon = icon, count = count or 0, duration = duration or 0,
              expires = expires or 0, spellId = spellId, source = source }
      end
    end
    if not a then break end
    if a.secret then hidden = hidden + 1 else out[#out + 1] = a end
  end
  return out, hidden
end

function API.Aura(unit, spell, filter)
  local wantName = type(spell) == "string" and spell or nil
  local wantId = type(spell) == "number" and spell or nil
  local idName = wantId and API.SpellName(wantId)
  for _, a in ipairs(API.Auras(unit, filter)) do
    if (wantId and a.spellId == wantId) or (wantName and a.name == wantName) or (idName and a.name == idName) then
      return a
    end
  end
  return nil
end

function API.AddOnMeta(addon, field)
  local fn = (C_AddOns and C_AddOns.GetAddOnMetadata) or _G.GetAddOnMetadata
  return try(fn, addon, field)
end

function API.GetMacroCount()
  local g, c = try(_G.GetNumMacros)
  return g or 0, c or 0
end

function API.Money()
  return try(_G.GetMoney) or 0
end

function API.Distance(unit)
  if _G.UnitPosition then
    local y1, x1, _, m1 = try(_G.UnitPosition, "player")
    local y2, x2, _, m2 = try(_G.UnitPosition, unit)
    y1, x1, y2, x2, m1, m2 = pub(y1), pub(x1), pub(y2), pub(x2), pub(m1), pub(m2)
    if y1 and y2 and x1 and x2 and m1 == m2 then
      local dx, dy = x2 - x1, y2 - y1
      return (dx * dx + dy * dy) ^ 0.5
    end
  end
  -- (CLEAN 4.0, audit) repli « portée d'interaction » pour un membre du groupe seulement, hors combat (restreint en
  -- combat) : un PNJ hors groupe n'a pas de position dans le vrai client → nil, et l'appelant se tait (pas 10 / 28).
  if _G.CheckInteractDistance and not pub(try(_G.InCombatLockdown))
    and (pub(try(_G.UnitInParty, unit)) or pub(try(_G.UnitInRaid, unit))) then
    if pub(try(_G.CheckInteractDistance, unit, 3)) then return 10 end
    if pub(try(_G.CheckInteractDistance, unit, 4)) then return 28 end
  end
  return nil
end

-- Compatibilité Forever 1.60.1 (vérifiée sur Blizzard_APIDocumentationGenerated, branche « forever ») :
-- les globales de Classic Era (GetPetHappiness, GetNumSkillLines, GetNumSpellTabs, GetQuestLogTitle…) n'y
-- existent plus ; on lit d'abord le C_* puis on se replie sur la globale (Era).
local function petInfo(name) local P = _G.C_PetInfo return (P and P[name]) or _G[name] end
function API.PetHappiness() return try(petInfo("GetPetHappiness")) end          -- happiness, damage%, loyaltyRate
function API.PetLoyalty() return try(petInfo("GetPetLoyalty")) end
function API.PetTrainingPoints() return try(petInfo("GetPetTrainingPoints")) end  -- total, used
-- (COHERE) points de dressage LIBRES, ou nil si la lecture n'est pas fiable. Forever (PetInfoDocumentation) :
-- C_PetInfo.GetPetTrainingPoints() → totalPoints, usedPoints ; libres = total − utilisés. Un familier apprivoisé
-- a toujours des techniques (Morsure, Grondement…) donc usedPoints > 0 : « 0 utilisé » = le total brut
-- (ex. 112 = niv 28 × 4 au niveau de loyauté 5), jamais un nombre de points libres → on se tait.
function API.PetFreeTrainingPoints()
  local total, used = API.PetTrainingPoints()
  total, used = tonumber(total), tonumber(used)
  if not (total and used) or used <= 0 or total <= 0 or used > total then return nil end
  return total - used
end
function API.PetFoodTypes()                                                       -- { "Viande", ... }
  local r = { try(petInfo("GetPetFoodTypes")) }
  if type(r[1]) == "table" then return r[1] end
  return r
end

--- Lignes de compétence (armes, métiers…) : { {name=, isHeader=, rank=, maxRank=, id=}, ... }
function API.SkillLines()
  local out, SI = {}, _G.C_SkillInfo
  if SI and SI.GetNumSkillLines and SI.GetSkillLineInfo then
    for i = 1, (tonumber((try(SI.GetNumSkillLines))) or 0) do
      local d = try(SI.GetSkillLineInfo, i)
      if type(d) == "table" then
        out[#out + 1] = { name = d.name, isHeader = d.isHeader, rank = d.rank, maxRank = d.maxRank, id = d.skillID }
      end
    end
    return out
  end
  if _G.GetNumSkillLines and _G.GetSkillLineInfo then
    for i = 1, (tonumber((try(_G.GetNumSkillLines))) or 0) do
      local name, isHeader, _, rank, _, _, maxRank = try(_G.GetSkillLineInfo, i)
      if name then out[#out + 1] = { name = name, isHeader = isHeader, rank = rank, maxRank = maxRank } end
    end
  end
  return out
end

--- Sorts du grimoire : { {name=, sub=, passive=, slot=}, ... } ; pet = true pour le familier.
function API.SpellBookSpells(pet)
  local out, SB, E = {}, _G.C_SpellBook, _G.Enum
  local bankEnum = E and E.SpellBookSpellBank
  local slots = {}
  if SB and SB.GetSpellBookItemName and bankEnum then
    local bank = pet and bankEnum.Pet or bankEnum.Player
    if pet then
      for i = 1, (tonumber((try(SB.HasPetSpells))) or 0) do slots[#slots + 1] = i end
    elseif SB.GetNumSpellBookSkillLines and SB.GetSpellBookSkillLineInfo then
      for l = 1, (tonumber((try(SB.GetNumSpellBookSkillLines))) or 0) do
        local info = try(SB.GetSpellBookSkillLineInfo, l)
        if type(info) == "table" and not info.shouldHide then
          local off = tonumber(info.itemIndexOffset) or 0
          for i = off + 1, off + (tonumber(info.numSpellBookItems) or 0) do slots[#slots + 1] = i end
        end
      end
    end
    for _, i in ipairs(slots) do
      local name, sub = try(SB.GetSpellBookItemName, i, bank)
      if name then
        out[#out + 1] = { name = name, sub = sub, slot = i,
                          passive = SB.IsSpellBookItemPassive and try(SB.IsSpellBookItemPassive, i, bank) or false }
      end
    end
    return out
  end
  local bt = pet and (_G.BOOKTYPE_PET or "pet") or (_G.BOOKTYPE_SPELL or "spell")
  if pet then
    for i = 1, (tonumber((try(_G.HasPetSpells))) or 0) do slots[#slots + 1] = i end
  elseif _G.GetNumSpellTabs and _G.GetSpellTabInfo then
    for tab = 1, (tonumber((try(_G.GetNumSpellTabs))) or 0) do
      local _, _, off, num = try(_G.GetSpellTabInfo, tab)
      for i = (off or 0) + 1, (off or 0) + (num or 0) do slots[#slots + 1] = i end
    end
  end
  for _, i in ipairs(slots) do
    local name, sub = try(_G.GetSpellBookItemName, i, bt)
    if name then out[#out + 1] = { name = name, sub = sub, slot = i, passive = try(_G.IsPassiveSpell, i, bt) or false } end
  end
  return out
end

--- Entrées du journal de quêtes (lecture seule, sans toucher à la sélection Blizzard) :
--- { {title=, level=, isHeader=, isComplete=, questID=, index=}, ... }
function API.QuestLogEntries()
  local out, QL = {}, _G.C_QuestLog
  if QL and QL.GetNumQuestLogEntries and QL.GetInfo then
    for i = 1, (tonumber((try(QL.GetNumQuestLogEntries))) or 0) do
      local q = try(QL.GetInfo, i)
      if type(q) == "table" then
        local done = q.questID and QL.IsComplete and try(QL.IsComplete, q.questID)
        out[#out + 1] = { title = q.title, level = q.level, isHeader = q.isHeader, questID = q.questID, index = i,
                          isComplete = done and 1 or nil }
      end
    end
    return out
  end
  if _G.GetNumQuestLogEntries and _G.GetQuestLogTitle then
    for i = 1, (tonumber((try(_G.GetNumQuestLogEntries))) or 0) do
      local title, lvl, _, isHeader, _, isComplete, _, qid = try(_G.GetQuestLogTitle, i)
      if title then
        out[#out + 1] = { title = title, level = lvl, isHeader = isHeader, isComplete = isComplete, questID = qid, index = i }
      end
    end
  end
  return out
end

--- Sort en cours du joueur : name, startMS, endMS (UnitCastingInfo ; CastingInfo = vieux Classic).
function API.PlayerCasting()
  if _G.UnitCastingInfo then
    local n, _, _, s, e = try(_G.UnitCastingInfo, "player")
    return pub(n), pub(s), pub(e)
  end
  if _G.CastingInfo then
    local n, _, _, s, e = try(_G.CastingInfo)
    return n, s, e
  end
end

function API.HasAction(slot)
  local AB = _G.C_ActionBar
  if AB and AB.HasAction then return try(AB.HasAction, slot) and true or false end
  return try(_G.HasAction, slot) and true or false
end

---------------------------------------------------------------------------
-- Timers (LITE) : chaque minuteur appartient au module qui l'a créé (événement, message, OnEnable, autre
-- minuteur en cours). Module coupé = ses tickers sont vraiment annulés (repris tels quels au réveil) et ses
-- TreeAI:After en attente ne partent plus. Le coût de chaque rappel est compté dans TreeAI._stats (/tai perf).
---------------------------------------------------------------------------
local profile = _G.debugprofilestop
TreeAI._stats = TreeAI._stats or {}   -- [module] = { calls, ms } (SelfTest / « /tai perf »)
local curOwner                         -- module dont le code tourne en ce moment (nil = cœur)
local function isMod(o) return type(o) == "table" and o ~= TreeAI and o.IsEnabled ~= nil and o.name ~= nil end
local statOf = setmetatable({}, { __mode = "k" })   -- propriétaire -> sa ligne de TreeAI._stats (sans recalcul)
local coreKey = {}
local function addStat(owner, ms)
  local key = owner or coreKey
  local st = statOf[key]
  if not st then
    local name = isMod(owner) and owner.name or "Core"
    st = TreeAI._stats[name]
    if not st then st = { calls = 0, ms = 0 }; TreeAI._stats[name] = st end
    statOf[key] = st
  end
  st.calls = st.calls + 1
  st.ms = st.ms + (ms or 0)
end
--- appelle fn au nom de `owner` (minuteurs créés dedans = les siens), chronométré
local function runAs(owner, fn, ...)
  local prev = curOwner
  curOwner = owner
  local t0 = profile and profile()
  local ok, err = safecall(fn, ...)
  if t0 then addStat(owner, (profile() or t0) - t0) end
  curOwner = prev
  return ok, err
end
TreeAI._RunAs = runAs

local timerFrame, timers
local function ensureTimerFrame()
  if timerFrame or not _G.CreateFrame then return end
  timers = {}
  timerFrame = _G.CreateFrame("Frame")
  timerFrame:SetScript("OnUpdate", function(self)
    local t = now()
    for i = #timers, 1, -1 do
      local tm = timers[i]
      if tm.at <= t then
        tremove(timers, i)
        safecall(tm.fn)
      end
    end
    if #timers == 0 and self.Hide then self:Hide() end   -- rien en attente : plus d'OnUpdate du tout
  end)
end

TreeAI.AFTER_HEAVY = 0.25   -- (4.2 FIX42) rappels différés « lourds » confiés à l'ordonnanceur (TreeAI._sliceAfter)
function TreeAI:After(sec, fn)
  if type(fn) ~= "function" then return end
  local owner = isMod(curOwner) and curOwner or nil
  local cb = function()
    if owner and owner._suspended then return end   -- module coupé entre-temps : rien
    runAs(owner, fn)
  end
  -- (4.2 FIX42, « micro-lag toutes les secondes ») un rafraîchissement regroupé (≥ 0,25 s : Quêtes, Planificateur,
  -- Nettoyage, Suivi…) passe par l'ordonnanceur : jamais deux travaux lourds dans la même image, budget ~1,5 ms
  local sa = TreeAI._sliceAfter
  if sa and sa(sec, cb, owner) then return end
  if C_Timer and C_Timer.After then return C_Timer.After(sec or 0, cb) end
  ensureTimerFrame()
  if timers then
    timers[#timers + 1] = { at = now() + (sec or 0), fn = cb }
    if timerFrame.Show then timerFrame:Show() end
  end
end
--- rappel interne du cœur (jamais attribué à un module, jamais sauté)
local function coreLater(sec, fn)
  local prev = curOwner
  curOwner = nil
  TreeAI:After(sec, fn)
  curOwner = prev
end

local ownerTickers = setmetatable({}, { __mode = "k" })   -- module -> { proxy, … }
local Ticker = {}
Ticker.__index = Ticker
function Ticker:Cancel()
  self.cancelled = true
  if self.real then self.real:Cancel(); self.real = nil end
end
function Ticker:IsCancelled() return self.cancelled and true or false end
--- (4.0.1) minuteur « de marche » : k fois moins souvent quand le joueur est immobile (voir MO, ordonnanceur)
function Ticker:IdleStretch(k) k = tonumber(k) self.stretch = (k and k > 1) and k or nil return self end
-- SPIKE (3.9.7) : ordonnanceur unique. Avant, chaque ticker était un C_Timer.NewTicker créé au même instant
-- (OnEnable de tous les modules à la connexion) : tous les tickers d'1 s (puis 2, 3, 5 s…) tombaient dans
-- la MÊME image, chaque seconde pile → saccade régulière. Maintenant : un seul OnUpdate, chaque ticker a sa
-- phase propre (suite du nombre d'or : jamais deux tickers sur le même instant), au plus SLICE_JOBS rappels
-- « lourds » (période ≥ 1 s) par image et ~SLICE_MS de travail (debugprofilestop) ; le reste passe à l'image suivante (file par échéance).
local SLICE_JOBS, SLICE_MS, SLICE_WAIT = 3, 1.5, 0.5   -- SLICE_WAIT : image « longue »
-- (4.2 FIX42) coût mesuré de chaque travail (moyenne glissante, ms) : un travail « gros » (≥ BIG_MS) passe seul dans
-- son image ; un second travail lourd n'entre que si le coût prévu tient dans le budget de l'image
TreeAI.SLICE_MS, TreeAI.BIG_MS = SLICE_MS, 0.6
local sched, schedFrame, schedNext, schedSeq = {}, nil, math.huge, 0
-- (4.0.1) MOUVEMENT : l'OnUpdate unique de l'ordonnanceur jette un coup d'œil par seconde (position + cap). Joueur
-- immobile depuis 3 s = les minuteurs « de marche » (ticker:IdleStretch(k)) battent k fois moins vite ; dès qu'il
-- bouge (ou PLAYER_STARTED_MOVING), ils reprennent leur cadence et ceux qui dorment à l'arrêt (T:OnMotion) sont
-- réveillés. T:Moving() = a bougé ces 3 dernières secondes.
local MO = { at = -1e9, acc = 0, wakers = {}, still = 3 }
local dueBuf = {}
local function byNext(a, b) return a.next < b.next end
local runSlices, slicePending   -- file « tranches » (rafraîchissement auto) : voir StartAutoRefresh
local function schedStep(elapsed)
  local t = now()
  -- image très longue (≥ 0,5 s : chargement, tests à pas d'1 s) : rien n'est retardé, comme avant
  local coarse = (tonumber(elapsed) or 0) >= SLICE_WAIT
  local jobs, t0 = 0, profile and profile()
  if t >= schedNext then
    local still = t - MO.at > MO.still
    -- (4.0.1) un seul passage sur la liste : échéances dues + prochaine échéance des autres (avant : deux passages)
    local n, m = 0, math.huge
    for i = #sched, 1, -1 do
      local p = sched[i]
      if p.cancelled or not p.real then tremove(sched, i)
      elseif p.next <= t then n = n + 1; dueBuf[n] = p
      elseif p.next < m then m = p.next end
    end
    for i = #dueBuf, n + 1, -1 do dueBuf[i] = nil end
    if n > 1 then tsort(dueBuf, byNext) end
    schedNext = math.huge   -- un minuteur lancé par un rappel ci-dessous l'abaisse (startTicker)
    local big, BIG_MS = false, TreeAI.BIG_MS
    for i = 1, n do
      local p = dueBuf[i]
      dueBuf[i] = nil
      -- < 1 s = pas de travail découpé / minuterie de combat : léger, jamais retardé ; rappel unique (T:After) : lourd
      -- seulement si ceux de son module ont COÛTÉ ≥ 0,1 ms (mesuré) — un rappel jamais mesuré passe tout de suite
      local heavy = p.sec >= 1 or (p.once and (p.cost or 0) >= 0.1)
      local est = p.cost or 0.2
      if p.cancelled or not p.real then   -- annulé par un rappel précédent de cette image
      elseif heavy and not coarse and jobs > 0 and (jobs >= SLICE_JOBS or big or est >= BIG_MS * 0.4
          or (t0 and (profile() - t0) + est > SLICE_MS)) then
        -- reste dû : passera à l'image suivante (file par échéance, le plus en retard d'abord)
        if p.next < m then m = p.next end
      else
        if p.once then
          p.cancelled = true   -- rappel unique : retiré au prochain passage
        else
          local per = (still and p.stretch) and p.sec * p.stretch or p.sec
          local nx = p.next + per
          p.next = (nx > t) and nx or (t + per)   -- en retard : on garde la cadence sans rafale de rattrapage
        end
        local c0 = (heavy or p.once) and profile and profile()
        if heavy then jobs = jobs + 1 end
        p.cb()
        if c0 then
          local dt = (profile() or c0) - c0
          p.cost = p.cost and (p.cost * 0.6 + dt * 0.4) or dt
          if p.key then TreeAI._onceCost[p.key] = p.cost end
          if heavy and dt >= BIG_MS then big = true end
        end
        if p.real and not p.cancelled and p.next < m then m = p.next end
      end
    end
    if m < schedNext then schedNext = m end
  end
  if runSlices then runSlices(jobs, t0) end
end
TreeAI._SchedStep = schedStep   -- tests

--- (4.0.1) le joueur se met à bouger : minuteurs étirés ramenés à leur cadence, dormeurs réveillés
function TreeAI:WakeMotion()
  local t = now()
  MO.at = t
  local m = schedNext
  for i = 1, #sched do
    local p = sched[i]
    if p.stretch and p.next and p.next > t + p.sec then p.next = t + p.sec end
    if p.next and p.next < m then m = p.next end
  end
  schedNext = m
  for owner, fn in pairs(MO.wakers) do
    if not (type(owner) == "table" and owner ~= TreeAI and owner.IsEnabled) or owner:IsEnabled() then runAs(owner, fn) end
  end
end
--- fn() quand le joueur se remet à bouger après un arrêt (un par propriétaire)
function TreeAI:OnMotion(owner, fn) if owner then MO.wakers[owner] = type(fn) == "function" and fn or nil end end
function TreeAI:Moving(win) return now() - MO.at <= (win or MO.still) end
local function motionPoll()
  local map, x, y = TreeAI:Here()
  local gf, f = _G.GetPlayerFacing, nil
  if gf then
    local ok, v = pcall(gf)
    if ok then f = v end
    local sec = _G.issecretvalue
    if f ~= nil and sec and sec(f) then f = nil end
  end
  if map ~= MO.map or x ~= MO.x or y ~= MO.y or f ~= MO.f then
    MO.map, MO.x, MO.y, MO.f = map, x, y, f
    TreeAI:WakeMotion()   -- (les réveils ne coûtent rien à qui est déjà éveillé)
  end
end
TreeAI._MotionPoll = motionPoll   -- tests

local function ensureSched()
  if schedFrame ~= nil then return schedFrame end
  schedFrame = false
  if _G.CreateFrame then
    local ok, f = pcall(_G.CreateFrame, "Frame")
    if ok and f then
      f:SetScript("OnUpdate", function(_, elapsed)
        local a = MO.acc + (elapsed or 0)
        if a >= 1 then MO.acc = 0 safecall(motionPoll) else MO.acc = a end   -- (4.0.1) 1 coup d'œil / s
        if slicePending or now() >= schedNext then safecall(schedStep, elapsed) end   -- image sans travail : 1 comparaison
      end)
      schedFrame = f
    end
  end
  return schedFrame
end
local function schedHandle(p)
  local h = {}
  function h.Cancel()
    for i = #sched, 1, -1 do if sched[i] == p then tremove(sched, i) end end
    if p.real == h then p.real = nil end
  end
  return h
end
local function startTicker(p)
  if p.real or p.cancelled then return end
  local cb = function() runAs(p.owner, p.fn) end
  if ensureSched() then
    schedSeq = schedSeq + 1
    local frac = (schedSeq * 0.6180339887) % 1   -- phase propre (nombre d'or) dans la seconde
    p.cb = cb
    p.next = now() + p.sec + frac * mmin(1, p.sec)
    local h = schedHandle(p)
    p.real = h
    sched[#sched + 1] = p
    if p.next < schedNext then schedNext = p.next end
    return
  end
  if C_Timer and C_Timer.NewTicker then
    p.real = C_Timer.NewTicker(p.sec, cb)
    return
  end
  local h = { cancelled = false }
  function h:Cancel() self.cancelled = true end
  local function loop()
    if h.cancelled then return end
    cb()
    coreLater(p.sec, loop)
  end
  coreLater(p.sec, loop)
  p.real = h
end

-- (4.2 FIX42) rappel unique « lourd » dans l'ordonnanceur (T:After ≥ 0,25 s) : coût mesuré par site d'appel (même
-- fonction = même coût prévu), passe seul dans son image s'il est gros
TreeAI._onceCost = setmetatable({}, { __mode = "k" })   -- module -> coût moyen (ms) de ses rappels uniques
TreeAI._sliceAfter = function(sec, cb, owner)
  if not ensureSched() then return false end
  local key = owner or TreeAI
  local w = tonumber(sec) or 0
  local p = { sec = 0, wait = w, once = true, cb = cb, next = now() + w, key = key }
  setmetatable(p, { __index = function(_, k) if k == "cost" then return TreeAI._onceCost[key] end end })   -- coût lu à l'échéance
  p.real = { Cancel = function() p.cancelled = true end }
  sched[#sched + 1] = p
  if p.next < schedNext then schedNext = p.next end
  return true
end

--- NewTicker(sec, fn[, owner]) : renvoie un objet avec :Cancel() ; owner = module (défaut : celui qui tourne)
function TreeAI:NewTicker(sec, fn, owner)
  if type(fn) ~= "function" then return nil end
  owner = owner or curOwner
  if not isMod(owner) then owner = nil end
  local p = setmetatable({ sec = tonumber(sec) or 1, fn = fn, owner = owner }, Ticker)
  if owner then
    local list = ownerTickers[owner]
    if not list then list = {}; ownerTickers[owner] = list end
    for i = #list, 1, -1 do if list[i].cancelled then tremove(list, i) end end
    list[#list + 1] = p
  end
  if not (owner and owner._suspended) then startTicker(p) end
  return p
end

---------------------------------------------------------------------------
-- Event dispatcher (many owners per event) + internal messages
---------------------------------------------------------------------------
local frame = _G.CreateFrame and _G.CreateFrame("Frame", "TreeAICoreFrame") or nil
TreeAI.frame = frame
local handlers = {}   -- event -> { [owner] = fn }
local messages = {}   -- message -> { [owner] = fn }
local dispatchDepth = 0

local function ownerActive(owner)
  if type(owner) == "table" and owner.IsEnabled and owner ~= TreeAI then
    return owner:IsEnabled()
  end
  return true
end

local AURA_UNITS = { player = true, pet = true, target = true, focus = true }
local evGen = 0   -- (4.0.1) événements reçus : le cycle des :AutoRefresh() sait si quelque chose s'est passé
-- (4.0.1) le journal de quêtes a pu changer : compteur avancé AVANT les handlers (T:QuestGen) → les relevés du
-- journal partagés (Export.LogSnapshot) se refont, sans dépendre de l'ordre des modules
TreeAI.QUEST_EVENTS = { QUEST_LOG_UPDATE = true, QUEST_ACCEPTED = true, QUEST_REMOVED = true, QUEST_TURNED_IN = true,
  UNIT_QUEST_LOG_CHANGED = true, QUEST_WATCH_UPDATE = true, QUEST_WATCH_LIST_CHANGED = true }
TreeAI._questGen = 0
function TreeAI:QuestGen() return self._questGen end
TreeAI._ctxCache = setmetatable({}, { __mode = "k" })
-- (4.0.1) événement -> { [handler] = { unité = true } } : la vie d'un membre du groupe ou d'une barre de nom ne réveille
-- plus (ni ne chronomètre) les handlers qui ne veulent que « player » / « pet » (avant : enveloppe appelée, puis filtrée)
TreeAI._unitOf = {}
local function dispatch(event, ...)
  local list = handlers[event]
  if not list then return end
  -- (LITE) auras : seulement joueur / familier / cible (pas chaque membre du groupe ni chaque barre de nom)
  if event == "UNIT_AURA" and not AURA_UNITS[(...)] then return end
  evGen = evGen + 1
  if TreeAI.QUEST_EVENTS[event] then TreeAI._questGen = TreeAI._questGen + 1 end
  local prev = TreeAI._ctx
  -- TAINT : le jeu signale le blocage pendant l'appel fautif → garder le module qui tournait (pas « core:ADDON_… »)
  if event == "ADDON_ACTION_BLOCKED" or event == "ADDON_ACTION_FORBIDDEN" then TreeAI._blockCtx = prev end
  dispatchDepth = dispatchDepth + 1
  local cc = TreeAI._ctxCache
  local uo = TreeAI._unitOf[event]   -- (4.0.1) handlers inscrits pour certaines unités seulement (RegisterUnitEvent)
  local unit = uo and (...)
  for owner, fn in pairs(list) do
    local f = uo and uo[fn]
    if (not f or f[unit]) and ownerActive(owner) then
      -- (4.0.1) étiquette « module:événement » (erreurs, blocages) construite une fois par couple
      local co = cc[owner]
      if not co then co = {}; cc[owner] = co end
      local c = co[event]
      if not c then c = tostring(owner.name or "core") .. ":" .. tostring(event); co[event] = c end
      TreeAI._ctx = c
      runAs(owner, fn, owner, event, ...)
    end
  end
  dispatchDepth = dispatchDepth - 1
  TreeAI._ctx = prev
end

--- Forever (restrictions Midnight) : s'abonner à COMBAT_LOG_EVENT(_UNFILTERED) déclenche ADDON_ACTION_FORBIDDEN
--- (« TreeAI a été bloqué »), même dans un pcall. On ne s'y abonne que sur un client sans valeurs secrètes.
local RESTRICTED_EVENTS = { COMBAT_LOG_EVENT_UNFILTERED = true, COMBAT_LOG_EVENT = true }
function TreeAI:CombatLogAllowed()
  if type(_G.issecretvalue) == "function" then return false end
  local CL = _G.C_CombatLog
  if CL and CL.IsCombatLogRestricted then
    local ok, r = pcall(CL.IsCombatLogRestricted)
    if ok and r then return false end
  end
  return true
end
function TreeAI:EventAllowed(event)
  if RESTRICTED_EVENTS[event] then return self:CombatLogAllowed() end
  return true
end

function TreeAI:RegisterEvent(event, owner, fn)
  owner = owner or self
  if not self:EventAllowed(event) then return false end
  if type(fn) ~= "function" then
    local m = owner[fn or event]
    if type(m) ~= "function" then return false end
    fn = m
  end
  if isMod(owner) and owner._suspended then   -- module coupé : gardé de côté, rendu au réveil
    owner._stash = owner._stash or {}
    owner._stash[event] = fn
    return true
  end
  if not handlers[event] then
    handlers[event] = {}
    if frame then
      local ok = pcall(frame.RegisterEvent, frame, event)
      if not ok then self:Debug("event inconnu:", event) end
    end
  end
  handlers[event][owner] = fn
  return true
end

--- (4.0.1) le handler inscrit par `owner` pour `event` (nil sinon)
function TreeAI:GetEventHandler(event, owner) local l = handlers[event] return l and l[owner or self] end

function TreeAI:UnregisterEvent(event, owner)
  owner = owner or self
  if type(owner) == "table" and owner._stash then owner._stash[event] = nil end
  local list = handlers[event]
  if not list then return end
  list[owner] = nil
  if next(list) == nil and event ~= "ADDON_LOADED" and event ~= "PLAYER_LOGIN"
     and event ~= "PLAYER_LOGOUT" and event ~= "CHAT_MSG_ADDON"
     and event ~= "PLAYER_REGEN_ENABLED" and event ~= "PLAYER_REGEN_DISABLED" then
    handlers[event] = nil
    if frame then pcall(frame.UnregisterEvent, frame, event) end
  end
end

function TreeAI:On(message, owner, fn)
  owner = owner or self
  if type(fn) ~= "function" then return false end
  messages[message] = messages[message] or {}
  messages[message][owner] = fn
  return true
end

--- LITE : module coupé = ses événements sont vraiment désinscrits (le jeu ne nous les envoie plus s'il n'y a
--- plus personne) et ses tickers annulés ; au réveil tout revient comme avant.
local function suspendModule(mod)
  if mod._suspended then return end
  mod._suspended = true
  local stash = mod._stash or {}
  for event, list in pairs(handlers) do
    local fn = list[mod]
    if fn then stash[event] = fn; TreeAI:UnregisterEvent(event, mod) end
  end
  mod._stash = stash
  for _, p in ipairs(ownerTickers[mod] or {}) do
    if p.real then p.real:Cancel(); p.real = nil end
  end
end
local function resumeModule(mod)
  if not mod._suspended then return end
  if dispatchDepth > 0 then   -- jamais de nouvelle clé dans la table qu'on parcourt : au prochain tour
    if not mod._resumeQueued then
      mod._resumeQueued = true
      coreLater(0, function() mod._resumeQueued = nil; if mod._active then resumeModule(mod) end end)
    end
    return
  end
  mod._suspended = nil
  local stash = mod._stash
  mod._stash = nil
  for event, fn in pairs(stash or {}) do TreeAI:RegisterEvent(event, mod, fn) end
  local list = ownerTickers[mod]
  if list then
    for i = #list, 1, -1 do if list[i].cancelled then tremove(list, i) else startTicker(list[i]) end end
  end
end
TreeAI._SuspendModule, TreeAI._ResumeModule = suspendModule, resumeModule

--- les événements de jeu auxquels TreeAI est abonné (tests / perf)
function TreeAI:RegisteredEvents()
  local out = {}
  for ev, list in pairs(handlers) do if next(list) then out[#out + 1] = ev end end
  tsort(out)
  return out
end
function TreeAI:ModuleTickers(mod)
  local n = 0
  for _, p in ipairs(ownerTickers[mod] or {}) do if p.real then n = n + 1 end end
  return n
end

function TreeAI:Off(message, owner)
  if messages[message] then messages[message][owner or self] = nil end
end

--- Actualisation automatique : toutes les `settings.refresh` s, chaque module actif qui a une méthode
--- :AutoRefresh() la reçoit (conseils recalculés), les suggestions expirées partent, le panneau se redessine.
--- Actions faites par le joueur (clic / commande) : certaines fonctions du jeu (créer / modifier une macro…)
--- ne sont permises que pendant une action du joueur sur WoW Forever. TreeAI:InUserAction() le dit.
TreeAI._userAction = 0
function TreeAI:UserAction(fn, ...)
  self._userAction = (self._userAction or 0) + 1
  local ok, err = pcall(fn, ...)
  self._userAction = math.max(0, (self._userAction or 1) - 1)
  if not ok then
    self:Debug("action:", err)
    if self._errorHook then pcall(self._errorHook, err, "action") end
  end
  return ok
end
function TreeAI:InUserAction() return (self._userAction or 0) > 0 end

-- (4.2 FIX42, étude §8.9) achats / artisanat / retraits / ventes / réparation (BuyMerchantItem, CraftRecipe /
-- DoTradeSkill, UseContainerItem, RepairAllItems) : UN seul chemin. Jamais en combat, jamais hors d'un clic du
-- joueur (T:UserAction, appelé par l'OnClick d'un bouton ou une touche), un appel par clic. Chaque appel est noté
-- (diag.shop) ; un blocage du jeu dans les 5 s qui suivent = « mode sûr » automatique : le bouton reste, mais le clic
-- montre l'objet dans la fenêtre du jeu (surbrillance, page) et c'est TOI qui cliques dans la fenêtre du jeu.
TreeAI.SHOP_WINDOW = 5
function TreeAI:ShopSafe()
  local db = self.db
  return type(db) == "table" and (db.shopSafe == true or db.shopSafeAuto == true) or false
end
function TreeAI:SetShopSafe(on)
  local db = self.db
  if type(db) ~= "table" then return end
  db.shopSafe = on and true or false
  if not on then db.shopSafeAuto = nil end
end
--- appelle fn(...) pour l'action « api » (texte), ou refuse : renvoie ok, raison ("combat" | "noclick" | "safe" | "api")
function TreeAI:ShopCall(api, fn, ...)
  if type(fn) ~= "function" then return false, "api" end
  if self:InCombat() then return false, "combat" end
  local diag
  if type(self.db) == "table" then
    self.db.diag = type(self.db.diag) == "table" and self.db.diag or {}
    diag = self.db.diag
    diag.shop = type(diag.shop) == "table" and diag.shop or { n = 0, refused = 0 }
  end
  if not self:InUserAction() then
    if diag then diag.shop.refused = (tonumber(diag.shop.refused) or 0) + 1 diag.shop.lastRefused = tostring(api) end
    return false, "noclick"
  end
  if self:ShopSafe() then return false, "safe" end
  local ok = pcall(fn, ...)
  self._shopAt, self._shopApi = (GetTime and GetTime()) or 0, tostring(api)
  if diag then
    diag.shop.n = (tonumber(diag.shop.n) or 0) + 1
    diag.shop.last = { api = tostring(api), t = (time and time()) or 0, ctx = tostring(self._ctx or self._lastCtx or "?") }
  end
  if ok then return true end
  return false, "api"
end
--- blocage du jeu juste après un appel ShopCall : on passe en mode sûr (gardé), et on le note pour /tai bloque
function TreeAI:_ShopBlocked(func)
  local t = (GetTime and GetTime()) or 0
  if not (self._shopAt and t - self._shopAt <= self.SHOP_WINDOW) then return false end
  local db = self.db
  if type(db) == "table" then
    db.shopSafeAuto = true
    db.diag = type(db.diag) == "table" and db.diag or {}
    db.diag.shopTaint = { api = self._shopApi, func = tostring(func or "?"), t = (time and time()) or 0 }
  end
  return true
end
--- autocontrôle (/tai bloque achats, page Infos) : lignes lisibles
function TreeAI:ShopCheck()
  local db = type(self.db) == "table" and self.db or {}
  local d = type(db.diag) == "table" and db.diag or {}
  local sh = type(d.shop) == "table" and d.shop or {}
  local out = {}
  out[#out + 1] = ("Achats / fabrications TreeAI : %d appel%s, tous dans un clic hors combat ; %d refusé%s hors clic.")
    :format(tonumber(sh.n) or 0, (tonumber(sh.n) or 0) > 1 and "s" or "", tonumber(sh.refused) or 0, (tonumber(sh.refused) or 0) > 1 and "s" or "")
  if type(sh.last) == "table" then out[#out + 1] = "Dernier : " .. tostring(sh.last.api) .. " (" .. tostring(sh.last.ctx) .. ")" end
  if type(d.shopTaint) == "table" then
    out[#out + 1] = ("Blocage du jeu (%s) juste après « %s » : mode sûr allumé (TreeAI montre, tu cliques)."):format(
      tostring(d.shopTaint.func), tostring(d.shopTaint.api))
  else
    out[#out + 1] = "Aucun blocage du jeu après un achat TreeAI."
  end
  out[#out + 1] = self:ShopSafe() and "Mode sûr : OUI — le bouton montre l'objet dans la fenêtre du jeu, tu achètes / crées toi-même."
    or "Mode sûr : non — un clic = un achat (BuyMerchantItem) ou une fabrication, rien sans clic."
  out[#out + 1] = "Vérif. en jeu : achète via TreeAI, puis achète un objet à la main chez le marchand : s'il passe, tout va bien."
  return out
end

-- SPIKE : le cycle ne lance plus tous les :AutoRefresh() dans la même image ; il remplit une file que
-- l'ordonnanceur vide par tranches (≤ SLICE_JOBS rappels / image, budget SLICE_MS), puis REFRESH + panneau.
local sliceQ, sliceHead, sliceN, sliceFight, sliceAt = {}, 1, 0, false, 0
local function sliceDone()
  TreeAI:Fire("REFRESH")
  -- le panneau ne se redessine que si un conseil / une ligne a changé (ou un conseil a expiré)
  if TreeAI.RefreshPanel and TreeAI:PanelDirty() then TreeAI:RequestRefresh() end
end
local arCtx = setmetatable({}, { __index = function(t, k) local v = k .. ":AutoRefresh"; rawset(t, k, v); return v end })
runSlices = function(jobs, t0)
  if not slicePending then return end
  local late = jobs < 0 or now() >= sliceAt   -- échéance : avant le cycle suivant, tout est passé
  local info = TreeAI.MODULE_INFO or {}
  while sliceHead <= sliceN do
    if not late and (jobs >= SLICE_JOBS or (jobs > 0 and t0 and (profile() - t0) > SLICE_MS)) then return end
    local name = sliceQ[sliceHead]
    sliceQ[sliceHead] = nil
    sliceHead = sliceHead + 1
    local m = TreeAI.modules and TreeAI.modules[name]
    -- (LITE) en combat, seuls les modules utiles au combat se recalculent
    if m and type(m.AutoRefresh) == "function" and m._active and (not m.IsEnabled or m:IsEnabled())
        and not (sliceFight and not (info[name] and info[name].combat)) then
      TreeAI._ctx = arCtx[name]   -- (4.0.1) « Module:AutoRefresh » construite une fois par module
      runAs(m, m.AutoRefresh, m)
      TreeAI._ctx = nil
      jobs = jobs + 1
    end
  end
  slicePending = false
  sliceHead, sliceN = 1, 0
  safecall(sliceDone)
end

-- (CLEAN 4.0, repris du module Confort) ralenti auto : sous 30 images/s deux relevés de suite (toutes les 3 s),
-- le cycle des :AutoRefresh() passe à ×2 ; il revient quand le jeu remonte à 40 images/s cinq relevés de suite.
local slowBad, slowGood = 0, 0
local function perfSample()
  local fps = tonumber(pub(try(_G.GetFramerate)))
  if not fps or fps <= 0 then return end
  if fps < 30 then slowBad, slowGood = slowBad + 1, 0 elseif fps >= 40 then slowGood, slowBad = slowGood + 1, 0 end
  if not TreeAI.perfSlow and slowBad >= 2 then
    TreeAI.perfSlow = true
    TreeAI:Fire("PERF_SLOW", true)
  elseif TreeAI.perfSlow and slowGood >= 5 then
    TreeAI.perfSlow = false
    TreeAI:Fire("PERF_SLOW", false)
  end
end
TreeAI._PerfSample = perfSample

do
-- (4.0.1) les modules qui ont un :AutoRefresh() (fixé au chargement des fichiers) : la liste est gardée, le cycle ne
-- reparcourt plus les ~50 modules toutes les 3 s ; refaite si un module s'inscrit plus tard
local arMods, arN = {}, -1
local function autoRefreshMods()
  local order = TreeAI.moduleOrder or {}
  if arN ~= #order then
    arN = #order
    for i = #arMods, 1, -1 do arMods[i] = nil end
    for _, name in ipairs(order) do
      local m = TreeAI.modules[name]
      if m and type(m.AutoRefresh) == "function" then arMods[#arMods + 1] = name end
    end
  end
  return arMods
end

-- (4.0.1) le cycle bat à sa propre cadence (3 s, ×2 en combat / jeu ralenti) au lieu d'un minuteur d'1 s qui comptait
-- jusqu'à 3 : deux fois moins de réveils de l'ordonnanceur ; le relevé d'images / s suit (toutes les 3 s)
-- (4.0.1) calme plat : aucun événement depuis le dernier cycle, joueur immobile, et chaque module actif qui a un
-- :AutoRefresh() se déclare « sans travail au calme » (mod.autoIdleSafe : il vérifie lui-même ce qui a changé) →
-- le cycle n'appelle personne (seules les lignes expirées sont retirées) ; un cycle complet au moins toutes les 30 s
TreeAI.AUTO_IDLE_MAX = 30
local cycEv, cycAt, cycPos = -1, -1e9, {}
local function quietCycle(list, t)
  if evGen ~= cycEv or t - cycAt >= TreeAI.AUTO_IDLE_MAX or TreeAI:Moved(cycPos, 0.0005) then return false end
  for i = 1, #list do
    local m = TreeAI.modules[list[i]]
    if m and m._active and not m.autoIdleSafe then return false end
  end
  return true
end

function TreeAI:StartAutoRefresh()
  if self._refreshTicker or not self.NewTicker then return end
  local base = tonumber(self.settings and self.settings.refresh) or 3
  if base <= 0 then base = 3 end
  local n = 0
  self._refreshTicker = self:NewTicker(base, function()
    perfSample()
    local every = tonumber(self.settings and self.settings.refresh) or 3
    if every <= 0 then return end
    local k = math.max(1, math.floor(every / base + 0.5))
    local fighting = InCombatLockdown and InCombatLockdown()
    if fighting then k = k * 2 end
    if self.perfSlow then k = k * 2 end
    n = n + 1
    if n < k or slicePending then return end
    n = 0
    local list = autoRefreshMods()
    local t = now()
    local rt = self._refreshTicker
    if quietCycle(list, t) then
      if rt then rt.stretch = 3 end   -- calme plat et immobile : un cycle toutes les 9 s (le mouvement le ramène à 3 s)
      if self.RefreshPanel and self:PanelDirty() then self:RequestRefresh() end
      return
    end
    if rt then rt.stretch = nil end
    cycEv, cycAt = evGen, t
    self:Stamp(cycPos)
    sliceFight = fighting and true or false
    for i = 1, #list do
      local name = list[i]
      local m = self.modules[name]
      if m and m._active then sliceN = sliceN + 1; sliceQ[sliceN] = name end
    end
    sliceHead, sliceAt = 1, now() + mmax(1, base * k - 1)
    if schedFrame then slicePending = true return end
    slicePending = true
    runSlices(-math.huge, nil)   -- pas d'ordonnanceur (tests sans CreateFrame) : tout de suite
  end)
end
end

function TreeAI:Fire(message, ...)
  local list = messages[message]
  if not list then return end
  local prev = TreeAI._ctx
  dispatchDepth = dispatchDepth + 1
  local cc = TreeAI._ctxCache
  for owner, fn in pairs(list) do
    if ownerActive(owner) then
      local co = cc[owner]
      if not co then co = {}; cc[owner] = co end
      local c = co[message]
      if not c then c = tostring(type(owner) == "table" and owner.name or "core") .. ":" .. tostring(message); co[message] = c end
      TreeAI._ctx = c
      runAs(owner, fn, owner, message, ...)
    end
  end
  dispatchDepth = dispatchDepth - 1
  TreeAI._ctx = prev
end

if frame then
  frame:SetScript("OnEvent", function(_, event, ...) dispatch(event, ...) end)
end
TreeAI._dispatch = dispatch -- for tests

---------------------------------------------------------------------------
-- SavedVariables
---------------------------------------------------------------------------
local DEFAULT_SETTINGS = {
  enabled = true, sound = true, chat = false, comms = true, debug = false,
  soundTips = false,   -- sons aussi pour certains conseils (rares…) ; sinon son = important (prio 3) seulement
  minPrio = 1,
  refresh = 3,   -- actualisation automatique des conseils (s, hors combat ; 2x plus lent en combat)
  pvp = false,   -- aides PvP (OFF : l'addon aide à quêter, il se tait contre les joueurs ennemis)
  panel = { shown = true, locked = false, scale = 1, alpha = 0.9,
            point = "CENTER", x = 300, y = 120, maxLines = 8, nowMax = 5, width = 320,
            fontSize = 11, lineGap = 3, iconSize = 13 },
  alert = { enabled = true, corner = "TOPRIGHT", duration = 8, scale = 1, alpha = 0.95 },
  -- sons par genre (UI2) : kinds[genre] = true/false (nil = défaut du genre), canal, écart global (s)
  sounds = { channel = "Master", gap = 2.5, kinds = {} },
  -- disposition des cadres : "confort" (défaut) | "compacte" | "coins" ; reflow = s'écarter des cadres qui grandissent
  layoutStyle = "confort", reflow = true,
  -- guidage (COHERE) : "auto" = Suivi Forever / Questie s'ils sont là (TreeAI ne double pas leur liste ni leur
  -- flèche), "treeai" = TreeAI guide tout (boussole + étapes dans « Maintenant »)
  guide = "treeai",   -- (3.9.8) le joueur veut TreeAI seul : Suivi Forever n'est plus utilisé par défaut
  -- anti-bruit (réglé par le préréglage « calme / normal / tout », chaque valeur reste modifiable)
  preset = "normal",
  noise = { nowMinPrio = 1, perModule = 2, alertGap = 20, topicGap = 120 },
}

local function mergeDefaults(dst, src)
  if type(dst) ~= "table" then dst = {} end
  for k, v in pairs(src) do
    if type(v) == "table" then
      dst[k] = mergeDefaults(dst[k], v)
    elseif dst[k] == nil then
      dst[k] = v
    end
  end
  return dst
end
TreeAI.MergeDefaults = mergeDefaults

function TreeAI:CharKey()
  if self._charKey then return self._charKey end
  local name = _G.UnitName and _G.UnitName("player")
  local realm = _G.GetRealmName and _G.GetRealmName()
  if not name or name == "" or name == _G.UNKNOWNOBJECT then return "?" end
  self._charKey = name .. "-" .. tostring(realm or "?")
  return self._charKey
end

function TreeAI:PlayerClass()
  if self._classFile then return self._classFile end   -- (4.0.1) la classe ne change pas : lue une fois
  if not _G.UnitClass then return nil end
  local _, classFile = _G.UnitClass("player")
  if type(classFile) == "string" and classFile ~= "" then self._classFile = classFile end
  return classFile
end

local function initDB()
  local db = _G.TreeAIDB
  if type(db) ~= "table" then db = {} end
  TreeAI._freshDB = type(db.settings) ~= "table"
  -- future migrations: if (db.schema or 0) < N then ... end
  db.schema = TreeAI.SCHEMA
  db.version = TreeAI.version
  db.settings = mergeDefaults(db.settings, DEFAULT_SETTINGS)
  db.modules = type(db.modules) == "table" and db.modules or {}
  db.chars = type(db.chars) == "table" and db.chars or {}
  db.group = type(db.group) == "table" and db.group or {}
  db.group.peers = type(db.group.peers) == "table" and db.group.peers or {}
  -- (4.0) modules retirés (SafePath, Edge, HunterPro, Comfort, Economy, Predict) : leurs données sauvegardées partent
  -- une fois (taille des SavedVariables = temps de chargement) ; leurs résumés aussi (l'appli ne les lit plus)
  if not db.gone40 then
    local gone = { "SafePath", "Edge", "HunterPro", "Comfort", "Economy", "Predict" }
    for _, n in ipairs(gone) do db.modules[n] = nil end
    for _, c in pairs(db.chars) do
      if type(c) == "table" then
        for _, n in ipairs(gone) do
          if type(c.modules) == "table" then c.modules[n] = nil end
          if type(c.moduleSettings) == "table" then c.moduleSettings[n] = nil end
        end
        local sm = c.summary
        if type(sm) == "table" then sm.predict, sm.economy, sm.comfort, sm.hunterPro = nil, nil, nil, nil end
      end
    end
    db.gone40 = true
  end
  _G.TreeAIDB = db
  TreeAI.db = db
  TreeAI.settings = db.settings
  return db
end

local function deepcopy(v)
  if type(v) ~= "table" then return v end
  local t = {}
  for k, x in pairs(v) do t[k] = deepcopy(x) end
  return t
end
TreeAI.DeepCopy = deepcopy

function TreeAI:CharDB()
  local db = self.db or initDB()
  local key = self:CharKey()
  local c = db.chars[key]
  if type(c) ~= "table" then c = {}; db.chars[key] = c end
  c.summary = type(c.summary) == "table" and c.summary or {}
  c.suggestions = type(c.suggestions) == "table" and c.suggestions or {}
  c.modules = type(c.modules) == "table" and c.modules or {}
  return c
end

---------------------------------------------------------------------------
-- Modules
---------------------------------------------------------------------------
local ModuleMixin = {}

function ModuleMixin:IsEnabled()
  if self._enabled == false or self._gateOff then return false end
  local s = TreeAI.settings
  if s and s.enabled == false then return false end
  return not (self.db and self.db.enabled == false) and self._classOK ~= false
end
function ModuleMixin:RegisterEvent(event, fn) return TreeAI:RegisterEvent(event, self, fn) end
function ModuleMixin:UnregisterEvent(event) return TreeAI:UnregisterEvent(event, self) end
function ModuleMixin:UnregisterAllEvents()
  for event in pairs(handlers) do TreeAI:UnregisterEvent(event, self) end
end
function ModuleMixin:RegisterUnitEvent(event, unit1, unit2, fn)
  if type(unit2) == "function" then fn, unit2 = unit2, nil end
  local handler = fn or self[event]
  if type(handler) ~= "function" then return false end
  local ok = TreeAI:RegisterEvent(event, self, handler)
  if ok then   -- filtre lu par le répartiteur avant tout appel (gardé tel quel quand le module est coupé / rallumé)
    local uo = TreeAI._unitOf[event]
    if not uo then uo = setmetatable({}, { __mode = "k" }); TreeAI._unitOf[event] = uo end
    local set = { [unit1] = true }
    if unit2 then set[unit2] = true end
    uo[handler] = set
  end
  return ok
end
function ModuleMixin:On(message, fn)
  if type(fn) ~= "function" then fn = self[message] end
  return TreeAI:On(message, self, fn)
end
function ModuleMixin:Suggest(id, text, prio, opts)
  opts = opts or {}
  opts.module = opts.module or self.name
  return TreeAI:Suggest(self.name .. ":" .. tostring(id), text, prio, opts)
end
function ModuleMixin:Clear(id) return TreeAI:ClearSuggestion(self.name .. ":" .. tostring(id)) end
function ModuleMixin:Print(...) return TreeAI:Print("[" .. (self.title or self.name) .. "]", ...) end
function ModuleMixin:Debug(...) return TreeAI:Debug("[" .. self.name .. "]", ...) end

-- Profil : réglages du compte (défaut) ou propres à ce personnage (CharDB().profile == "char").
-- TreeAI.settings et mod.db pointent toujours vers le profil actif.
function TreeAI:ProfileMode()
  if not self.db or self:CharKey() == "?" then return "account" end
  local c = self.db.chars[self:CharKey()]
  return (type(c) == "table" and c.profile == "char") and "char" or "account"
end

local function moduleStore()
  local db = TreeAI.db
  if TreeAI:ProfileMode() == "char" then
    local c = TreeAI:CharDB()
    c.moduleSettings = type(c.moduleSettings) == "table" and c.moduleSettings or {}
    return c.moduleSettings, db.modules
  end
  return db.modules
end

local function bindSettings()
  local db = TreeAI.db
  if not db then return end
  if TreeAI:ProfileMode() == "char" then
    local c = TreeAI:CharDB()
    if type(c.settings) ~= "table" then c.settings = deepcopy(db.settings) end
    c.settings = mergeDefaults(c.settings, DEFAULT_SETTINGS)
    TreeAI.settings = c.settings
  else
    TreeAI.settings = db.settings
  end
end

local function moduleBindDB(mod)
  local db = TreeAI.db
  if not db then return end
  local defaults = mergeDefaults({ enabled = true }, mod.defaults or {})
  local store, seed = moduleStore()
  -- (REVIEW) mode sûr : un module bêta jamais vu (nouveau module, nouveau perso en profil perso) démarre coupé
  if store[mod.name] == nil and type(db.moduleProfile) == "string" and db.moduleProfile == "sur"
      and TreeAI.IsBeta and TreeAI:IsBeta(mod.name) and not (seed and type(seed[mod.name]) == "table") then
    defaults.enabled = false
  end
  if store[mod.name] == nil and seed and type(seed[mod.name]) == "table" then
    store[mod.name] = deepcopy(seed[mod.name])   -- profil perso : part des réglages du compte
  end
  store[mod.name] = mergeDefaults(store[mod.name], defaults)
  mod.db = store[mod.name]
  if TreeAI:CharKey() ~= "?" then
    local c = TreeAI:CharDB()
    c.modules[mod.name] = mergeDefaults(c.modules[mod.name], mod.charDefaults or {})
    mod.char = c.modules[mod.name]
  else
    mod.char = mod.char or mergeDefaults({}, mod.charDefaults or {})
  end
end

local function moduleClassCheck(mod)
  if type(mod.classes) == "table" then
    local cls = TreeAI:PlayerClass()
    if cls then mod._classOK = mod.classes[cls] and true or false end
  end
end

local function moduleInit(mod)
  if mod._inited then return end
  moduleBindDB(mod)
  mod._inited = true
  if type(mod.OnInit) == "function" then runAs(mod, mod.OnInit, mod) end
end

local function moduleEnable(mod)
  if mod._active then return end
  moduleClassCheck(mod)
  if not mod:IsEnabled() then
    if TreeAI._loggedIn then suspendModule(mod) end
    return
  end
  mod._active = true
  resumeModule(mod)
  if type(mod.OnEnable) == "function" then runAs(mod, mod.OnEnable, mod) end
end

local function moduleDisable(mod)
  if not mod._active then
    if TreeAI._loggedIn then suspendModule(mod) end
    return
  end
  mod._active = false
  if type(mod.OnDisable) == "function" then runAs(mod, mod.OnDisable, mod) end
  TreeAI:ClearSuggestions(mod.name .. ":")
  suspendModule(mod)
end
TreeAI._ModuleEnable, TreeAI._ModuleDisable = moduleEnable, moduleDisable

function TreeAI:RegisterModule(name, tbl)
  if type(name) ~= "string" or name == "" then return nil end
  tbl = tbl or {}
  if self.modules[name] then
    -- re-registration: merge into existing table
    for k, v in pairs(tbl) do self.modules[name][k] = v end
    return self.modules[name]
  end
  tbl.name = name
  tbl.core = self
  for k, v in pairs(ModuleMixin) do
    if tbl[k] == nil then tbl[k] = v end
  end
  self.modules[name] = tbl
  self.moduleOrder[#self.moduleOrder + 1] = name
  -- late registration (file loaded after core init)
  if self._inited then moduleInit(tbl) end
  if self._loggedIn then moduleEnable(tbl) end
  return tbl
end

function TreeAI:GetModule(name) return self.modules[name] end

function TreeAI:IterModules()
  local i = 0
  return function()
    i = i + 1
    local n = self.moduleOrder[i]
    if n then return n, self.modules[n] end
  end
end

function TreeAI:SetModuleEnabled(name, enabled)
  local mod = self.modules[name]
  if not mod or not mod.db then return false end
  mod.db.enabled = enabled and true or false
  -- (4.0.1) le choix du joueur (clic dans « Fonctions », options, pastille, /tai module) : aucune migration ne le défait
  if type(self.db) == "table" then
    if type(self.db.modChoice) ~= "table" then self.db.modChoice = {} end
    self.db.modChoice[name] = mod.db.enabled
  end
  if enabled then
    if self._loggedIn then moduleEnable(mod) end
  else
    moduleDisable(mod)
  end
  if self._modulesStarted and self._BuildModulesTab then self:_BuildModulesTab() end
  self:RequestRefresh()
  return true
end

--- Tout TreeAI on/off, en direct (sans /reload).
function TreeAI:SetEnabled(v)
  if not self.settings then return end
  self.settings.enabled = v and true or false
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if v then if self._loggedIn then moduleEnable(mod) end else moduleDisable(mod) end
  end
  if not v then
    self:ClearSuggestions()
    if self.HidePanel then safecall(self.HidePanel, self) end
  elseif self.ShowPanel and self.settings.panel and self.settings.panel.shown ~= false then
    safecall(self.ShowPanel, self)
  end
  self:RequestRefresh()
end

--- Profil "account" (réglages communs à tes persos) ou "char" (propres à ce perso), en direct.
function TreeAI:SetProfile(mode)
  mode = (mode == "char") and "char" or "account"
  if not self.db or self:CharKey() == "?" or mode == self:ProfileMode() then return false end
  local wasActive = {}
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if mod._active then wasActive[n] = true; moduleDisable(mod) end
  end
  self:CharDB().profile = (mode == "char") and "char" or nil
  bindSettings()
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    moduleBindDB(mod)
    if self._loggedIn then moduleEnable(mod) end
  end
  if self.ApplyPanelSettings then safecall(self.ApplyPanelSettings, self) end
  self:Fire("PROFILE_CHANGED", mode)
  self:RequestRefresh()
  return true
end

---------------------------------------------------------------------------
-- LITE : profils de modules. « essentiel » (défaut pour tout le monde, posé une fois) = seuls les modules utiles
-- tout le temps tournent ; les autres restent dans « Plus → Modules » (un clic pour les rallumer).
-- gate = condition pour tourner en profil essentiel : "group" (en groupe), "ah" (HV ouvert), "instance".
---------------------------------------------------------------------------
TreeAI.PROFILE_V = 2   -- 2 = (REVIEW, 3.9.9) mode sûr posé une fois pour tout le monde
-- (REVIEW) MODE SÛR : seules les fonctions robustes par construction tournent par défaut (quêtes, boussole /
-- itinéraire, course au corps, diagnostic des erreurs et blocages, panneau, pastille, macros). Tout le reste est
-- « bêta » : listé dans « Plus → Modules » et dans la pastille (Fonctions bêta), un clic pour l'allumer.
TreeAI.SAFE_MODULES = TreeAI.SAFE_MODULES or {
  Theme = true, SelfTest = true, Bugs = true,                    -- panneau, diagnostic (erreurs, blocages)
  Quests = true, Tracker = true, Todo = true, Detect = true,     -- quêtes (Detect = la seule source objectif ↔ monstre)
  Compass = true, NavTime = true, Route = true, Planner = true, Export = true,   -- boussole, trajet, données du plan
  Speed = true,                                                  -- course au corps (mort / fantôme)
  Hub = true, Macros = true,                                     -- pastille de la minimap, macros (diagnostic compris)
  Window = true,                                                 -- (UIREDO) fenêtre TreeAI + carte « Prochaine étape »
  Shortcuts = true,                                              -- (UIREDO) une seule barre « Raccourcis » (rendu)
  -- (4.0.1) ce que le joueur a demandé, allumé d'office : zone de travail + trajet préparé, chemin dessiné (carte du
  -- monde tant qu'elle est ouverte, traînée minimap seulement en marchant), objets de quête en surbrillance
  -- (Glow = la bibliothèque d'animations de Highlight, jouées par le client)
  ZonePlan = true, PathDraw = true, Highlight = true, Glow = true,
  -- (4.0.2, ITEMINFO) lignes vraies sous les infobulles d'objets : ne travaille que quand une infobulle s'affiche
  -- (≈ 0 sinon) ; module nouveau = allumé d'office (pas de migration)
  ItemInfo = true,
  -- (REPLACE 4.0.1) sans autres addons : icônes de quêtes (muettes tant que Questie dessine les siennes), Suivi TreeAI
  -- (remplace le suivi du jeu), page « Mes addons » (rien d'automatique)
  QuestPins = true, Addons = true,   -- (4.0.5) Watch retiré : le suivi du jeu reste le suivi
  -- (4.1) demandés par le joueur : métiers (montée, session métier) et « Courses en ville » (rien sans un clic)
  Prof = true, CityRun = true,
  -- (4.1) le joueur joue toujours en groupe : la page Groupe (amis, rejoindre, intention) allumée en mode sûr
  Group = true,
}
-- (4.0.1) entrés dans le mode sûr après la 4.0 : allumés une fois chez les joueurs en mode sûr (voir _ApplyProfileOnce)
-- (4.1) Prof + CityRun : allumés une fois (SAFE_V 4) chez les joueurs en mode sûr, sauf s'ils les ont coupés eux-mêmes
TreeAI.SAFE_ADDED = { ZonePlan = 1, PathDraw = 1, Highlight = 1, Prof = 4, CityRun = 4, Group = 4 }
TreeAI.SAFE_V = 4
-- (4.0.5) sortis du mode sûr : coupés UNE fois chez tout le monde (le Suivi TreeAI cachait le suivi du jeu)
-- (4.1 LAYOUT) valeur = version qui les coupe : à l'écran il ne reste que les boutons rapides, la boussole et la
-- pastille (+ ses repères) ; bande d'horizon (2e flèche + texte au centre), radar, barres de combat (« c-1… »),
-- barre de quête (remplacée par les boutons rapides), points de minimap en plus (flèche « 76 m ») : coupés UNE fois,
-- chez tout le monde, quel que soit le profil ; un clic les rallume (Réglages → Affichage)
TreeAI.SAFE_REMOVED = { Watch = 2, Horizon = 3, Radar = 3, QuickFight = 3, Quick = 3, MinimapPins = 3 }
-- (4.1 LAYOUT) fenêtres cachées une fois (le module reste allumé) : clé du réglage « afficher » de chaque module
TreeAI.SAFE_HIDDEN = { Meter = "shown", Abandon = "hint", Watch = "shown" }
--- module « bêta » (pas dans le mode sûr) ?
function TreeAI:IsBeta(name) return not self.SAFE_MODULES[name] end
TreeAI.MODULE_INFO = TreeAI.MODULE_INFO or {
  Theme = { lite = true, desc = "Apparence du panneau (couleurs, police)" },
  SelfTest = { lite = true, desc = "Autotest et diagnostic (/tai test)" },
  Bugs = { lite = true, desc = "Fichier d'erreurs pour TreeAI Coach (/tai erreurs)" },
  Quests = { lite = true, desc = "Quêtes : objectifs, rendus, quêtes proches" },
  Tracker = { lite = true, desc = "Suivi des quêtes (onglet Quêtes)" },
  Todo = { lite = true, desc = "À faire : la liste de suivi des quêtes" },
  Compass = { lite = true, desc = "Boussole : flèche vers le prochain objectif" },
  NavTime = { lite = true, desc = "Temps de trajet justes (vitesse et détours mesurés)" },
  PathLearn = { lite = true, desc = "Chemins appris sur tes déplacements (rampes, virages, relief)" },
  Horizon = { lite = true, desc = "Bande d'horizon : les prochains arrêts selon ton cap" },
  Planner = { lite = true, desc = "Itinéraire de quêtes optimisé" },
  Export = { lite = true, desc = "Export du perso et du plan vers TreeAI Coach" },
  Group = { lite = true, gate = "group", desc = "Groupe : tes amis (avec ou sans TreeAI), objectif, quêtes à partager, prêts à tirer" },
  Macros = { lite = true, liteParts = true, desc = "Macros intelligentes (panneau Macros)" },
  Auction = { lite = true, gate = "ah", desc = "Hôtel des ventes (quand l'HV est ouvert)" },
  Highlight = { lite = true, liteParts = true, desc = "Surligne les objets utiles des sacs et du butin" },
  Hunter = { lite = true, combat = true, desc = "Chasseur : familier (humeur, nourriture, dressage), munitions, aspects, pièges" },
  Abandon = { lite = true, liteParts = true, desc = "Quêtes à abandonner (calculé à la demande)" },
  Dungeon = { lite = true, gate = "instance", combat = true, desc = "Donjon (s'allume tout seul en instance)" },
  Boost = { lite = false, desc = "Rares : un rare ou un trésor tout près, clic = y aller (+ crâne)" },
  Fight = { lite = false, desc = "Combat : pourquoi je suis mort, prêt avant le pull" },
  Pro = { lite = false, desc = "Conseils avancés (repos, sacs, trajets)" },
  Session = { lite = false, desc = "Ma session : bilan, pourquoi je meurs, pause, monture" },
  Errands = { lite = false, desc = "Guichets : courrier, maître de classe (prix réel), vols, appel prêt" },
  Gear = { lite = false, desc = "Équipement : récompense de quête, plan par emplacement (onglet « Équipement »)" },
  ItemInfo = { lite = true, desc = "Infos objets (infobulles) : amélioration, groupe, valeur, quête, métiers, familier, sources" },
  Talents = { lite = false, desc = "Talents : où mettre tes points" },
  Prof = { lite = false, desc = "Métiers : montée la moins chère, récolte (nœuds, dépeçage)" },
  CityRun = { lite = false, desc = "Courses en ville : tout à acheter / faire en ville, trajet le plus court, points métiers sur la carte" },
  PvP = { lite = false, combat = true, desc = "JcJ : aides contre les joueurs" },
  PvPMark = { lite = true, combat = true, desc = "Joueurs ennemis attaquables surlignés (barres de nom)" },
  Radar = { lite = false, desc = "Radar rond des objectifs" },
  Detect = { lite = true, desc = "Détection : rares, trésors, monstres de quête, services proches" },
  MinimapPins = { lite = true, desc = "Points sur la minimap (ce que Questie ne montre pas)" },
  Binds = { lite = false, desc = "Touches : sur quoi lier tes sorts et macros, nouveaux sorts à poser" },
  QuickFight = { lite = true, combat = true, desc = "Barre de combat : manger, familier, aspect, pull, prêt ou pas" },
  Leveling = { lite = false, desc = "Progression : repos, zone dangereuse, talents, compétence d'arme" },
  Inventory = { lite = false, desc = "Inventaire : sacs, réparation, vente des gris, nourriture" },
  Classes = { lite = false, combat = true, desc = "Conseils de classe (autres classes)" },
  Stats = { lite = true, desc = "Statistiques mesurées : XP/h et or/h (avec leur durée de mesure), kills, drops" },
  Route = { lite = true, desc = "Itinéraire le plus rapide entre les étapes" },
  PathDraw = { lite = true, desc = "Chemin de l'itinéraire dessiné sur la carte et la minimap" },
  Finder = { lite = true, desc = "Chercher : meilleur familier à apprivoiser, rares, équipement, maîtres, vols" },
  ZonePlan = { lite = true, desc = "Zone de travail et trajet préparé (ordre des arrêts)" },
  QuestBrain = { lite = true, desc = "Cerveau des quêtes : rendre mes quêtes, quêtes à prendre, tout faire ici, avec le groupe" },   -- (QUESTS 4.1) sûr : QuestBrain.lua s'y inscrit
  Speed = { lite = true, desc = "Aller plus vite : mort, monture, foyer" },
  QuickTravel = { lite = true, desc = "Raccourcis : vols, auberge, courses, outils du groupe" },
  Plain = { lite = true, desc = "Conseils de quête en mots simples" },
  Region = { lite = true, desc = "Quêtes rangées par région (ici, à côté, loin)" },
  Hub = { lite = true, desc = "Pastille de la minimap : tout TreeAI en un clic" },
  Window = { lite = true, desc = "Fenêtre TreeAI (pages, recherche) + carte « Prochaine étape »" },
  Shortcuts = { lite = true, desc = "Une seule barre « Raccourcis » : quêtes, voyage, combat, familier" },
  Quick = { lite = true, desc = "Raccourcis : barre d'actions rapides, quêtes acceptées et rendues" },
  Edge2 = { lite = false, desc = "Avantages : camp disputé, objet qui ne tombe pas, corps à fouiller…" },
  Coach = { lite = true, desc = "Conseils pro : le bon geste au bon moment (repos, familier, munitions, XP…), un à la fois" },   -- (ADVICE) sûr : Coach.lua s'y inscrit
  Tour = { lite = true, desc = "Visite guidée et astuces (une fois chacune)" },   -- (ONBOARD) sûr : Tour.lua s'y inscrit
  GroupGear = { lite = true, gate = "group", desc = "Équipement du groupe : ce que portent tes amis, échanges conseillés, à qui sert un butin" },   -- (4.1 GEARTRADE) sûr : GroupGear.lua s'y inscrit
  -- (REPLACE 4.0.1) sans autres addons
  QuestPins = { lite = true, desc = "Icônes de quêtes : « ! » à prendre, « ? » à rendre, zones d'objectifs (carte + minimap)" },
  Watch = { lite = false, desc = "Suivi TreeAI : quêtes du trajet, distance, direction et durée en direct, à côté du suivi du jeu" },
  Addons = { lite = true, desc = "Mes addons : ce que TreeAI remplace, Questie et Suivi Forever en silence en un clic" },
  QoL = { lite = true, desc = "Confort : vendre les gris et réparer, butin instantané, butin automatique, recul de caméra" },
  Meter = { lite = true, desc = "Dégâts : la fenêtre des dégâts du groupe (compteur du jeu, après chaque combat)" },
  Brain = { lite = true, desc = "Pilote auto : quêtes inutiles en pause, zone et mode du trajet, courses, pourquoi" },   -- (BRAIN) sûr : Brain.lua s'y inscrit
}

TreeAI.MODULE_PROFILES = { sur = true, essentiel = true, tout = true }
TreeAI.MODULE_PROFILE_LABEL = { sur = "Mode sûr", essentiel = "Essentiel (léger)", tout = "Tout (bêta compris)" }
function TreeAI:ModuleProfile()
  local db = self.db
  local p = type(db) == "table" and db.moduleProfile
  return (p and self.MODULE_PROFILES[p]) and p or "tout"   -- (3.9.9) défaut posé une fois : mode sûr
end
--- profil léger actif ? (les modules allumés y font le minimum : pas de scans en fond)
function TreeAI:Lite() return self:ModuleProfile() == "essentiel" end

local ahOpen = false
local function gateOpen(gate)
  if gate == "group" then
    if try(_G.IsInGroup) then return true end
    return ((try(_G.GetNumGroupMembers) or 0) > 0) or ((try(_G.GetNumPartyMembers) or 0) > 0)
      or ((try(_G.GetNumRaidMembers) or 0) > 0)
  elseif gate == "instance" then
    local inst, kind = try(_G.IsInInstance)
    return (inst and kind ~= "none") and true or false
  elseif gate == "ah" then
    return ahOpen
  end
  return true
end
TreeAI._GateOpen = gateOpen

--- (ré)évalue les conditions : ouvre / ferme les modules concernés ; `event` rejoué au module qui se réveille
function TreeAI:_EvalGates(event, ...)
  local lite = self:Lite()
  local changed = false
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    local info = self.MODULE_INFO[n]
    local off = (lite and info and info.gate and not gateOpen(info.gate)) and true or nil
    if off ~= mod._gateOff then
      changed = true
      mod._gateOff = off
      if self._modulesStarted then
        if off then
          moduleDisable(mod)
        else
          moduleEnable(mod)
          local h = event and mod._active and handlers[event] and handlers[event][mod]
          if h then runAs(mod, h, mod, event, ...) end
        end
      end
    end
  end
  if changed and self._modulesStarted then self:_BuildModulesTab() end
  return changed
end

--- « essentiel » | « tout » : pose l'état de chaque module (sans /reload)
function TreeAI:SetModuleProfile(name)
  if type(self.db) ~= "table" then return false end
  if name == "tout" or name == "all" then name = "tout"
  elseif name == "sur" or name == "sûr" or name == "safe" then name = "sur"
  else name = "essentiel" end
  local changed = self:ModuleProfile() ~= name
  self.db.moduleProfile = name
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    local info = self.MODULE_INFO[n]
    local want
    if name == "tout" then want = true
    elseif name == "sur" then want = not self:IsBeta(n)
    else want = not info or info.lite ~= false end
    if mod.db then
      mod.db.enabled = want
      if self._modulesStarted then
        -- modules qui en font moins en profil essentiel : redémarrés pour prendre le nouveau profil
        if changed and want and info and info.liteParts and mod._active then moduleDisable(mod) end
        if want then moduleEnable(mod) else moduleDisable(mod) end
      end
    end
  end
  self:_EvalGates()
  if self._modulesStarted then
    self:ClearSuggestion("core:lite")
    self:ClearSuggestion("core:safe")
    self:_BuildModulesTab()
    self:RequestRefresh()
  end
  return true
end

-- premier lancement de cette version : profil essentiel pour tout le monde, une fois (avis + « Tout réactiver »)
function TreeAI:_ApplyProfileOnce()
  local db = self.db
  if type(db) ~= "table" then return end
  if (tonumber(db.profileV) or 0) < self.PROFILE_V then
    db.profileV = self.PROFILE_V
    -- (3.9.9, REVIEW) une fois pour tous (nouveaux et anciens) : mode sûr ; les fonctions bêta restent à un clic
    self:SetModuleProfile("sur")
    self._safeNotice = true
    db.safeV = self.SAFE_V   -- le mode sûr posé ici contient déjà les modules ajoutés
    return
  end
  -- (4.0.1) ZonePlan, PathDraw, Highlight rejoignent le mode sûr : allumés UNE fois chez les joueurs en mode sûr,
  -- sauf s'ils les ont coupés eux-mêmes (db.modChoice) ; aucun avis (rien de neuf à lire, juste ce qu'il a demandé)
  if (tonumber(db.safeV) or 0) < self.SAFE_V then
    local fromV = tonumber(db.safeV) or 0
    db.safeV = self.SAFE_V
    do
      -- tous les réglages enregistrés (compte + chaque perso en profil perso), pas seulement le perso connecté
      local stores = { db.modules }
      for _, c in pairs(type(db.chars) == "table" and db.chars or {}) do
        if type(c) == "table" and type(c.moduleSettings) == "table" then stores[#stores + 1] = c.moduleSettings end
      end
      for n, v in pairs(self.SAFE_REMOVED or {}) do
        if fromV < (tonumber(v) or 2) then
          for _, st in ipairs(stores) do
            local e = type(st) == "table" and st[n]
            if type(e) == "table" then e.enabled = false if e.replace ~= nil then e.replace = false end end
          end
        end
      end
      if fromV < 3 then
        for n, key in pairs(self.SAFE_HIDDEN or {}) do
          for _, st in ipairs(stores) do
            local e = type(st) == "table" and st[n]
            if type(e) == "table" then e[key] = false end
          end
        end
      end
      for n, v in pairs(self.SAFE_REMOVED or {}) do
        local mod = self.modules[n]
        if fromV < (tonumber(v) or 2) and mod and mod.db then
          mod.db.enabled = false
          if mod.db.replace ~= nil then mod.db.replace = false end
          if self._modulesStarted and mod._active and type(moduleDisable) == "function" then moduleDisable(mod) end
        end
      end
      if fromV < 3 then
        for n, key in pairs(self.SAFE_HIDDEN or {}) do
          local mod = self.modules[n]
          if mod and mod.db then mod.db[key] = false end
        end
      end
    end
    if self:ModuleProfile() ~= "sur" then return end
    local choice = type(db.modChoice) == "table" and db.modChoice or {}
    for n, v in pairs(self.SAFE_ADDED or {}) do
      local mod = self.modules[n]
      -- valeur = version qui l'ajoute (1 = 4.0.1 : reproposé à chaque migration, comme avant) ; respecte modChoice
      v = tonumber(v) or 1
      if (v <= 1 or fromV < v) and mod and mod.db and mod.db.enabled == false and choice[n] ~= false then
        mod.db.enabled = true
        if self._modulesStarted then moduleEnable(mod) end
      end
    end
  end
end

function TreeAI:_SafeNotice()
  if not self._safeNotice then return end
  self._safeNotice = nil
  self:Suggest("core:safe", "Mode sûr : les fonctions bêta sont à activer dans le menu", 2, {
    ttl = 900, tab = "now", sound = false, kind = "always", ownPage = "modules",
    tooltip = "Clic : la liste des fonctions (un clic par fonction). Aussi : pastille de la minimap → Fonctions bêta.",
    onClick = function()
      local Wn = TreeAI.Window   -- (UIREDO) la page « Fonctions » de la fenêtre TreeAI, sinon le panneau
      if Wn and Wn._active and Wn.Open then Wn.Open("modules")
      else
        if TreeAI.ShowPanel then TreeAI:ShowPanel() end
        if TreeAI.SelectTab then TreeAI:SelectTab("modules") end
      end
      TreeAI:ClearSuggestion("core:safe")
    end })
end

function TreeAI:_LiteNotice()
  if not self._liteNotice then return end
  self._liteNotice = nil
  self:Suggest("core:lite", "TreeAI allégé : seul l'essentiel tourne (moins de lag). Clic : tout réactiver", 2, {
    ttl = 900, tab = "now", sound = false, ownPage = "modules",
    tooltip = "Plus → Modules : rallumer un module à la fois (une ligne, un clic).",
    onClick = function() TreeAI:SetModuleProfile("tout") end })
end

--- onglet « Modules » (sous « Plus ») : profil, coût, un module par ligne (clic = allumer / couper)
function TreeAI:_BuildModulesTab()
  if not self.SetTabLines then return end
  local lite = self:Lite()
  local prof = self:ModuleProfile()
  local head
  if prof == "sur" then head = "Mode sûr : seules les fonctions vérifiées tournent — clic : tout allumer (bêta compris)"
  elseif lite then head = "Profil : Essentiel (léger) — clic : tout réactiver"
  else head = "Tout est allumé (bêta compris) — clic : revenir au mode sûr" end
  local lines = {
    { text = head, prio = 2, id = "core:profile",
      tooltip = "Mode sûr = quêtes, zone de travail, trajet (et son tracé), boussole, carte « Prochaine étape », fenêtre TreeAI, surbrillance des objets de quête, course au corps, erreurs, macros.\n"
        .. "Les fonctions « bêta » n'ont été testées que hors du jeu : allume-les une par une ci-dessous.",
      onClick = function() TreeAI:SetModuleProfile(prof == "sur" and "tout" or (lite and "tout" or "sur")) end },
    -- (« mod: » : l'orage de clics du harnais laisse les bascules de profil tranquilles)
    { text = lite and "Revenir au mode sûr — clic" or "Profil léger (essentiel, moins de lag) — clic", prio = 1,
      id = "mod:profile:alt", onClick = function() TreeAI:SetModuleProfile(lite and "sur" or "essentiel") end },
    { text = "Coût des fonctions — clic : afficher", prio = 1, id = "core:perf",
      onClick = function()   -- (4.0 QA) dans la page « Fonctions » de la fenêtre, jamais dans le chat
        local Wn = TreeAI.Window
        if not (Wn and Wn.ShowPerf and Wn.ShowPerf()) then TreeAI:PrintPerf() end
      end },
  }
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    local info = self.MODULE_INFO[n] or {}
    local on = mod.db and mod.db.enabled ~= false
    local state
    if not on then state = "|cff888888OFF|r"
    elseif mod._gateOff then state = "|cffddbb44ATTENTE|r"
    elseif mod._classOK == false then state = "|cff888888autre classe|r"
    else state = "|cff33dd33ON|r" end
    local beta = self:IsBeta(n)
    lines[#lines + 1] = { text = state .. " " .. tostring(mod.title or n) .. (beta and " |cffb48cff(bêta)|r" or ""),
      prio = 1, id = "mod:" .. n, beta = beta or nil,
      tooltip = (info.desc or n) .. (beta and "\nBêta : testé hors du jeu seulement, peut se tromper." or "")
        .. "\nClic : " .. (on and "couper" or "allumer") .. " ce module (sans /reload)",
      onClick = function() TreeAI:SetModuleEnabled(n, not (mod.db and mod.db.enabled ~= false)) end }
  end
  self:SetTabLines("modules", lines, "main")
end

--- /tai perf : les modules qui coûtent le plus (temps mesuré par le cœur : événements, minuteurs, messages)
function TreeAI:PerfLines()
  local out = {}
  local mem
  local upd = (_G.C_AddOns and _G.C_AddOns.UpdateAddOnMemoryUsage) or _G.UpdateAddOnMemoryUsage
  local get = (_G.C_AddOns and _G.C_AddOns.GetAddOnMemoryUsage) or _G.GetAddOnMemoryUsage
  if type(get) == "function" then try(upd); mem = API.Pub(try(get, ADDON_NAME)) end
  local up = mmax(1, now() - (self._loginAt or 0))
  local nOn, nAll = 0, 0
  for _, n in ipairs(self.moduleOrder) do
    nAll = nAll + 1
    if self.modules[n]._active then nOn = nOn + 1 end
  end
  out[#out + 1] = strformat("TreeAI : %s Ko de mémoire · %d/%d modules actifs · profil %s",
    mem and tostring(math.floor(mem + 0.5)) or "?", nOn, nAll, self:ModuleProfile())
  local list = {}
  for name, st in pairs(self._stats) do if (st.calls or 0) > 0 then list[#list + 1] = { name = name, st = st } end end
  tsort(list, function(a, b) return (a.st.ms or 0) > (b.st.ms or 0) end)
  for i, e in ipairs(list) do
    if i > 8 then break end
    out[#out + 1] = strformat("  %d. %s : %.1f ms/min (%d appels)", i, e.name, (e.st.ms or 0) / up * 60, e.st.calls)
  end
  if #list == 0 then out[#out + 1] = "  (rien de mesuré pour l'instant)" end
  out[#out + 1] = "  Page « Fonctions » : couper ce qui ne te sert pas."
  return out
end
function TreeAI:PrintPerf()
  for _, s in ipairs(self:PerfLines()) do self:Print(s) end
end

do  -- conditions des modules (groupe / instance / HV) : réévaluées après coup (jamais pendant un parcours)
  local gateOwner = {}
  local function later(event, ...)
    local a, b = ...
    coreLater(0, function() TreeAI:_EvalGates(event, a, b) end)
  end
  for _, ev in ipairs({ "GROUP_ROSTER_UPDATE", "PARTY_MEMBERS_CHANGED", "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA" }) do
    TreeAI:RegisterEvent(ev, gateOwner, function(_, event, ...) later(event, ...) end)
  end
  TreeAI:RegisterEvent("AUCTION_HOUSE_SHOW", gateOwner, function(_, event, ...) ahOpen = true; later(event, ...) end)
  TreeAI:RegisterEvent("AUCTION_HOUSE_CLOSED", gateOwner, function(_, event) ahOpen = false; later(nil) end)
end

---------------------------------------------------------------------------
-- Combat helpers
---------------------------------------------------------------------------
function TreeAI:InCombat()
  if _G.InCombatLockdown then
    local ok, v = pcall(_G.InCombatLockdown)
    return ok and v and true or false
  end
  return false
end

--- TAINT : un cadre « protégé » (modèle sécurisé, cadre Blizzard protégé comme TargetFrame / boutons d'action,
--- ou NOTRE cadre ancré sur un cadre protégé — il devient protégé lui aussi) ne peut pas être montré / caché /
--- déplacé / redimensionné en combat : le jeu bloque l'appel (ADDON_ACTION_BLOCKED, compté dans « Échec des
--- actions d'interface » de la liste des addons). TreeAI:FrameLocked(f, ...) = vrai si l'un d'eux est dans ce cas.
local function isProtectedFrame(f)
  if type(f) ~= "table" then return false end
  local fn = f.IsProtected
  if type(fn) ~= "function" then return false end
  local ok, p = pcall(fn, f)
  return ok and p and true or false
end
TreeAI.IsProtectedFrame = isProtectedFrame
TreeAI._lockSkips = 0
function TreeAI:FrameLocked(...)
  if not self:InCombat() then return false end
  for i = 1, select("#", ...) do
    if isProtectedFrame((select(i, ...))) then
      self._lockSkips = (self._lockSkips or 0) + 1
      return true
    end
  end
  return false
end

---------------------------------------------------------------------------
-- (4.0.1) « A-t-il bougé ? » : la position du joueur lue au plus une fois par instant de jeu (GetTime), valeurs
-- secrètes écartées. Les minuteurs s'en servent pour ne RIEN refaire tant que rien n'a changé (joueur immobile) :
--   T:Here() -> mapID, x, y (0..1) | nil (instance, écran de chargement)
--   T:Moved(stamp, d) -> vrai si la carte a changé ou si le joueur est à plus de d (unités de carte ; 0,002 ≈ 5 à
--   10 m selon la zone) de la dernière T:Stamp(stamp) ; stamp = une table à soi, gardée par l'appelant.
---------------------------------------------------------------------------
do
local hereT, hereMap, hereX, hereY = -1, nil, nil, nil
function TreeAI:Here()
  local t = now()
  if t == hereT then return hereMap, hereX, hereY end
  hereT, hereMap, hereX, hereY = t, nil, nil, nil
  local CM = _G.C_Map
  local gm, gp = CM and CM.GetBestMapForUnit, CM and CM.GetPlayerMapPosition
  if not (gm and gp) then return nil end
  local sec = _G.issecretvalue                       -- lecture directe (3 appels protégés, pas un par valeur)
  local ok, map = pcall(gm, "player")
  if not ok or map == nil or (sec and sec(map)) then return nil end
  local ok2, p = pcall(gp, map, "player")
  if not ok2 or type(p) ~= "table" then return nil end
  local x, y
  if type(p.GetXY) == "function" then
    local ok3
    ok3, x, y = pcall(p.GetXY, p)
    if not ok3 then return nil end
  else
    x, y = p.x, p.y
  end
  if sec and (sec(x) or sec(y)) then return nil end
  x, y = tonumber(x), tonumber(y)
  if not (x and y) or (x == 0 and y == 0) then return nil end
  hereMap, hereX, hereY = map, x, y
  return map, x, y
end
function TreeAI:Stamp(st)
  if type(st) ~= "table" then return end
  st.map, st.x, st.y = self:Here()
end
function TreeAI:Moved(st, d)
  if type(st) ~= "table" then return true end
  local map, x, y = self:Here()
  if map ~= st.map then return true end
  if not map then return false end
  d = d or 0.002
  local dx, dy = x - (st.x or 0), y - (st.y or 0)
  return dx > d or dx < -d or dy > d or dy < -d
end
end

local oocQueue, oocOrder = {}, {}
function TreeAI:RunOutOfCombat(key, fn)
  if type(fn) ~= "function" then return end
  if not self:InCombat() then return safecall(fn) end
  key = key or tostring(fn)
  if not oocQueue[key] then oocOrder[#oocOrder + 1] = key end
  oocQueue[key] = fn
end

---------------------------------------------------------------------------
-- Suggestions
---------------------------------------------------------------------------
local suggestions = {}   -- id -> entry
local announced = {}     -- id -> { text, at }
local alerted = {}       -- id -> { sk, at } (corner alert pops)
TreeAI._alerted = alerted
TreeAI._suggestions = suggestions

-- ANNOY : « clic droit = masquer » tient vraiment. Le module re-proposait la même ligne au rafraîchissement
-- suivant (quelques secondes) : elle revenait. Masquée = même sens (chiffres ignorés) caché 2 h, gardé au
-- /reload (CharDB().dismissed, horloge serveur, ≤ 40 entrées) ; un vrai danger passe toujours.
TreeAI.DISMISS_S = 2 * 3600
local function serverNow() local f = _G.time return (type(f) == "function" and f()) or 0 end
local function skeletonOf(text) return (strgsub(tostring(text or ""), "%d+", "#")) end
local function dismissStore()
  local ok, c = pcall(TreeAI.CharDB, TreeAI)
  if not ok or type(c) ~= "table" then return nil end
  if type(c.dismissed) ~= "table" then c.dismissed = {} end
  return c.dismissed
end
function TreeAI:DismissSuggestion(id, _sameTopic)
  local e = suggestions[id]
  -- une ligne fusionnée (même sujet vu par plusieurs modules) : masquer l'une masque les autres
  if e and not _sameTopic and self.TopicOf then
    local okT, topic = pcall(self.TopicOf, self, e)
    if okT and topic then
      local others = {}
      for oid, o in pairs(suggestions) do
        if oid ~= id and not o.danger then
          local okO, ot = pcall(self.TopicOf, self, o)
          if okO and ot == topic then others[#others + 1] = oid end
        end
      end
      for _, oid in ipairs(others) do self:DismissSuggestion(oid, true) end
    end
  end
  local d = e and dismissStore()
  if d then
    local n, oldest, oldK = 0, nil, nil
    for k, v in pairs(d) do
      n = n + 1
      if type(v) ~= "table" or not oldest or (tonumber(v.ts) or 0) < oldest then oldest, oldK = type(v) == "table" and (tonumber(v.ts) or 0) or -1, k end
    end
    if n >= 40 and oldK then d[oldK] = nil end
    d[id] = { sk = skeletonOf(e.text), ts = serverNow() }
  end
  self:ClearSuggestion(id)
end
function TreeAI:IsDismissed(id, text, danger)
  if danger then return false end
  local d = self.db and dismissStore()
  local v = d and d[id]
  if type(v) ~= "table" then return false end
  if serverNow() - (tonumber(v.ts) or 0) > self.DISMISS_S or (text and v.sk ~= skeletonOf(text)) then
    d[id] = nil
    return false
  end
  return true
end
function TreeAI:PruneDismissed()
  local d = dismissStore()
  if not d then return end
  local t = serverNow()
  for k, v in pairs(d) do
    if type(v) ~= "table" or t - (tonumber(v.ts) or 0) > self.DISMISS_S then d[k] = nil end
  end
end
-- encadrés déjà montrés : gardés au /reload (5 min), sinon chaque /reload rejouait l'alerte + le son
function TreeAI:_SaveRecentAlerts()
  local ok, c = pcall(self.CharDB, self)
  if not ok or type(c) ~= "table" then return end
  local t, st, out, n = now(), serverNow(), {}, 0
  for id, a in pairs(alerted) do
    if n < 20 and type(a) == "table" and t - (a.at or -1e9) < 300 then
      out[id] = { sk = a.sk, ts = st - math.floor(t - a.at) }
      n = n + 1
    end
  end
  c.recentAlerts = n > 0 and out or nil
end
function TreeAI:_RestoreRecentAlerts()
  local ok, c = pcall(self.CharDB, self)
  if not ok or type(c) ~= "table" or type(c.recentAlerts) ~= "table" then return end
  local t, st = now(), serverNow()
  for id, a in pairs(c.recentAlerts) do
    local age = st - (tonumber(type(a) == "table" and a.ts) or 0)
    if type(a) == "table" and age >= 0 and age < 300 and not alerted[id] then alerted[id] = { sk = a.sk, at = t - age } end
  end
  c.recentAlerts = nil
end

---------------------------------------------------------------------------
-- Sons (UI2) : TreeAI:Sound(kind) — un son Blizzard (SOUNDKIT) distinct et discret par genre,
-- chaque genre activable, canal au choix, cooldown global (jamais de rafale), en combat SEUL le danger.
---------------------------------------------------------------------------
-- kits = clés SOUNDKIT essayées dans l'ordre, id = repli numérique (sons du jeu, aucun fichier ajouté)
-- (4.0.1) calme par défaut : seul le danger sonne ; chaque autre genre se rallume d'un clic (Options > Sons)
TreeAI.SOUND_KINDS = {
  danger   = { label = "Danger (combat)", kits = { "RAID_WARNING" }, id = 8959, cd = 4, on = true },
  alert    = { label = "Important", kits = { "ALARM_CLOCK_WARNING_3", "RAID_WARNING" }, id = 12889, cd = 8, on = false },
  rare     = { label = "Rare / élite repéré", kits = { "UI_EPICLOOT_TOAST", "READY_CHECK" }, id = 8960, cd = 20, on = false },
  quest    = { label = "Quête terminée (à rendre)", kits = { "IG_QUEST_LIST_COMPLETE" }, id = 878, cd = 5, on = false },
  group    = { label = "Groupe / amis", kits = { "UI_BNET_TOAST", "IG_PLAYER_INVITE" }, id = 18019, cd = 15, on = false },
  ah       = { label = "Bonne affaire HV", kits = { "LOOT_WINDOW_COIN_SOUND", "AUCTION_WINDOW_OPEN" }, id = 120, cd = 20, on = false },
  levelup  = { label = "Niveau / carte de niveau", kits = { "UI_LEVEL_UP", "LEVELUPSOUND" }, id = 888, cd = 10, on = false },
  reminder = { label = "Rappel discret", kits = { "TELL_MESSAGE", "MAP_PING" }, id = 3081, cd = 30, on = false },
}
TreeAI.SOUND_ORDER = { "danger", "alert", "quest", "rare", "group", "ah", "levelup", "reminder" }
TreeAI.SOUND_CHANNELS = { "Master", "SFX", "Ambience", "Dialog" }
TreeAI.SOUND_CHANNEL_LABEL = { Master = "Général", SFX = "Effets", Ambience = "Ambiance", Dialog = "Dialogues" }
TreeAI.SOUND_GAP = 2.5   -- s minimum entre deux sons TreeAI (le danger passe quand même, son propre cd)
local soundState = { last = -1e9, kinds = {}, log = {}, played = 0, blocked = 0 }
TreeAI._soundState = soundState

function TreeAI:SoundSettings()
  local s = self.settings
  if not s then return nil end
  if type(s.sounds) ~= "table" then s.sounds = {} end
  local ss = s.sounds
  if type(ss.kinds) ~= "table" then ss.kinds = {} end
  ss.channel = ss.channel or "Master"
  return ss
end

function TreeAI:SoundKindOn(kind)
  local def = self.SOUND_KINDS[kind]
  if not def then return false end
  local ss = self:SoundSettings()
  local v = ss and ss.kinds[kind]
  if v == nil then return def.on and true or false end
  return v and true or false
end
function TreeAI:SetSoundKind(kind, on)
  local ss = self:SoundSettings()
  if ss and self.SOUND_KINDS[kind] then ss.kinds[kind] = on and true or false end
end

local function soundKit(def)
  local SK = rawget(_G, "SOUNDKIT")
  if type(SK) == "table" then
    for _, k in ipairs(def.kits or {}) do
      if type(SK[k]) == "number" then return SK[k] end
    end
  end
  return def.id
end
TreeAI._SoundKit = soundKit

local function emit(kind, def)
  local ss = TreeAI:SoundSettings()
  local ch = (ss and ss.channel) or "Master"
  local id = soundKit(def)
  local ok = false
  if type(_G.PlaySound) == "function" and id then ok = pcall(_G.PlaySound, id, ch) end
  local log = soundState.log
  log[#log + 1] = { kind = kind, id = id, channel = ch, at = now() }
  while #log > 20 do tremove(log, 1) end
  soundState.played = soundState.played + 1
  return ok
end

--- Joue le son d'un genre (`TreeAI.SOUND_KINDS`). Renvoie true, ou false + raison :
--- "off" (sons coupés) · "kind" (ce genre coupé) · "combat" (en combat : seul "danger") · "gap" (cooldown
--- global) · "cooldown" (même genre trop tôt) · "unknown". opts.force = ignore cooldowns (aperçu).
function TreeAI:Sound(kind, opts)
  opts = opts or {}
  local def = self.SOUND_KINDS[kind or ""]
  if not def then return false, "unknown" end
  local s = self.settings
  if not opts.preview then
    if not (s and s.sound ~= false) then return false, "off" end
    if not self:SoundKindOn(kind) then return false, "kind" end
    if kind ~= "danger" and self:InCombat() then soundState.blocked = soundState.blocked + 1; return false, "combat" end
    local t = now()
    local ss = self:SoundSettings()
    local gap = tonumber(ss and ss.gap) or self.SOUND_GAP
    if kind ~= "danger" and t - soundState.last < gap then soundState.blocked = soundState.blocked + 1; return false, "gap" end
    local lk = soundState.kinds[kind]
    if lk and t - lk < (def.cd or 5) then soundState.blocked = soundState.blocked + 1; return false, "cooldown" end
    soundState.last, soundState.kinds[kind] = t, t
  end
  emit(kind, def)
  return true
end
--- Aperçu (bouton des options) : joue toujours, même genre coupé.
function TreeAI:PreviewSound(kind) return self:Sound(kind, { preview = true }) end
function TreeAI:ResetSoundGate() soundState.last, soundState.kinds = -1e9, {} end
function TreeAI:SoundLog() return soundState.log end

local function playUrgentSound(e, opts)
  local kind = (opts and opts.soundKind) or ((e and TreeAI:IsDanger(e, opts)) and "danger") or "alert"
  return TreeAI:Sound(kind)
end

local function logSuggestion(e)
  if not TreeAI.db or TreeAI:CharKey() == "?" then return end
  local log = TreeAI:CharDB().suggestions
  log[#log + 1] = { id = e.id, text = e.text, prio = e.prio, time = wallclock(), module = e.module }
  while #log > 40 do tremove(log, 1) end
end

--- PvP context: an enemy player targeted / attacking, or a battleground / arena. Never raises.
function TreeAI:InPvP()
  local ok, res = pcall(function()
    if IsInInstance then
      local _, kind = IsInInstance()
      if kind == "pvp" or kind == "arena" then return true end
    end
    if UnitExists and UnitExists("target") and UnitIsPlayer and UnitIsPlayer("target")
        and UnitCanAttack and UnitCanAttack("player", "target") then
      return true
    end
    return false
  end)
  return ok and res or false
end


---------------------------------------------------------------------------
-- Anti-bruit : sujets (même sens = une seule ligne), budget « Maintenant », encadré ≤ 1 / 20 s,
-- rien dans l'encadré en combat sauf danger de mort, préréglages « calme / normal / tout ».
---------------------------------------------------------------------------
-- id complet de suggestion -> sujet. Plusieurs modules qui disent la même chose partagent un sujet :
-- « Maintenant » n'en garde qu'une ligne (la plus prioritaire), l'encadré ne la répète pas.
TreeAI.TOPIC_IDS = TreeAI.TOPIC_IDS or {
  ["Hunter:ammo"] = "ammo",
  ["Inventory:inv_bags"] = "bags",
  ["Inventory:inv_repair"] = "repair",
  ["Inventory:inv_food"] = "food", ["Inventory:inv_buy_food"] = "food", ["Inventory:inv_buy_water"] = "food",
  ["Errands:trainer"] = "train",
  ["Hunter:aspect"] = "aspect", ["Hunter:fd"] = "fd", ["Hunter:hungry"] = "petfood",
  ["Tracker:hub"] = "hub",
  ["Hunter:petdead"] = "pet", ["Hunter:nopet"] = "pet",
  ["Fight:prepull"] = "prepull",
  ["Quests:grey"] = "abandon",
}
-- repli par le texte (lignes de groupe, nouveaux modules) : motif en minuscules -> sujet
TreeAI.TOPIC_PATTERNS = TreeAI.TOPIC_PATTERNS or {
  { "munitions", "ammo" }, { "plus de flèches", "ammo" }, { "plus de balles", "ammo" },
  { "sacs pleins", "bags" }, { "sacs presque pleins", "bags" },
  { "répar", "repair" }, { "durabilité", "repair" }, { "abandonne", "abandon" },
}
-- danger de mort : seules alertes permises dans l'encadré pendant un combat
TreeAI.DANGER_IDS = TreeAI.DANGER_IDS or {
  ["Hunter:aspect"] = true, ["Hunter:fd"] = true,
}
TreeAI.PRESETS = {
  -- simple (défaut) : 3 lignes, seulement ce qu'on peut faire (clic ou conseil utile), pas de bavardage
  simple = { nowMax = 3, nowMinPrio = 1, perModule = 1, alertGap = 45, topicGap = 600, soundTips = false,
             actionable = true },
  calme = { nowMax = 3, nowMinPrio = 2, perModule = 1, alertGap = 45, topicGap = 600, soundTips = false },
  normal = { nowMax = 5, nowMinPrio = 1, perModule = 2, alertGap = 20, topicGap = 120 },
  tout   = { nowMax = 8, nowMinPrio = 1, perModule = 0, alertGap = 20, topicGap = 60 },
}
TreeAI.PRESET_ORDER = { "simple", "calme", "normal", "tout" }
TreeAI.PRESET_LABEL = { simple = "Simple", calme = "Mode calme", normal = "Normal", tout = "Tout", perso = "Perso" }
TreeAI.PRESET_HELP = {
  simple = "3 lignes, seulement ce que tu peux faire maintenant",
  calme = "3 lignes, seulement l'utile, alerte rare (45 s mini)",
  normal = "5 lignes, une alerte max toutes les 20 s",
  tout = "8 lignes, tous les conseils (même les petits)",
}

local function noiseCfg()
  local s = TreeAI.settings
  local n = s and s.noise
  if type(n) ~= "table" then return TreeAI.PRESETS.normal end
  return n
end

--- Sujet d'une ligne / suggestion (nil = sans sujet : dédoublonnée seulement au texte exact).
function TreeAI:TopicOf(e)
  if type(e) ~= "table" then return nil end
  if e.topic then return e.topic end
  local t = e.id and self.TOPIC_IDS[e.id]
  if t then return t end
  local txt = string.lower(self.PlainText and self.PlainText(e.text) or tostring(e.text or ""))
  for _, p in ipairs(self.TOPIC_PATTERNS) do
    if strfind(txt, p[1], 1, true) then return p[2] end
  end
  return nil
end

---------------------------------------------------------------------------
-- Guidage : UNE source de vérité (COHERE). Quand Shortest Path Forever (Suivi Forever + sa flèche) et / ou
-- Questie (suivi des objectifs) sont chargés, ils possèdent la liste des quêtes et le trajet : « Maintenant »
-- ne répète pas leurs lignes (objectifs, « près de la remise », « Suite », « à rendre ici »…) et garde au plus
-- UNE ligne d'étape (l'en-tête, celle du trajet = l'arrêt courant de SPF). L'onglet Quêtes reste complet.
---------------------------------------------------------------------------
TreeAI.GUIDE_IDS = TreeAI.GUIDE_IDS or {
  ["Quests:near"] = true, ["Quests:chain"] = true, ["Quests:turnin"] = true, ["Quests:left"] = true,
  ["Quests:next"] = true, ["Quests:loot"] = true, ["Tracker:pickup"] = true, ["Tracker:easy"] = true,
  ["Compass:target"] = true,
}
TreeAI.GUIDE_PREFIX = TreeAI.GUIDE_PREFIX or { "Quests:prog:", "Quests:done:" }
-- sources des lignes « étape de quête » épinglées (une seule passe dans « Maintenant »)
TreeAI.STEP_SOURCES = TreeAI.STEP_SOURCES or { plan = 1, Quests = 2 }
TreeAI.GUIDE_MODES = { "auto", "treeai" }
TreeAI.GUIDE_LABEL = { auto = "Suivi Forever / Questie (si installés)", treeai = "TreeAI seul (recommandé)" }
function TreeAI:GuideMode()
  local s = self.settings
  if s and (tonumber(s.guideV) or 0) < 1 then s.guideV, s.guide = 1, "treeai" end   -- (3.9.8) une fois : TreeAI seul
  local m = s and s.guide
  return m == "auto" and "auto" or "treeai"
end
function TreeAI:SetGuideMode(m)
  if not self.settings then return end
  self.settings.guide = (m == "treeai") and "treeai" or "auto"
  if self.MarkDirty then self:MarkDirty() end
  self:Fire("GUIDE_MODE", self.settings.guide)
  if self.RequestRefresh then self:RequestRefresh() end
end
-- addons de guidage chargés (lecture seule, détection par leurs globales publiques)
function TreeAI:GuideAddons()
  local spf = rawget(_G, "ShortestPathForever")
  local questie = rawget(_G, "QuestieLoader") or rawget(_G, "Questie")
  return { spf = type(spf) == "table" and type(spf.API) == "table" or nil, questie = type(questie) == "table" or nil }
end
-- "spf" / "questie" quand un autre addon possède la liste + le trajet, nil quand TreeAI guide
function TreeAI:ExternalGuide()
  if self:GuideMode() == "treeai" then return nil end
  local a = self:GuideAddons()
  return (a.spf and "spf") or (a.questie and "questie") or nil
end
function TreeAI:IsGuideLine(e)
  local id = type(e) == "table" and e.id
  if type(id) ~= "string" then return false end
  if self.GUIDE_IDS[id] then return true end
  for _, p in ipairs(self.GUIDE_PREFIX) do if id:sub(1, #p) == p then return true end end
  return false
end

function TreeAI:IsDanger(e, opts)
  if not e or (e.prio or 0) < 3 then return false end
  return (opts and opts.danger) or e.danger or self.DANGER_IDS[e.id or ""] or false
end

--- Préréglage : "calme" (peu de lignes, seulement l'utile), "normal", "tout". Appliqué en direct.
function TreeAI:ApplyPreset(name)
  local p = self.PRESETS[name]
  local s = self.settings
  if not (p and s) then return false end
  s.preset = name
  s.noise = type(s.noise) == "table" and s.noise or {}
  s.noise.nowMinPrio, s.noise.perModule = p.nowMinPrio, p.perModule
  s.noise.alertGap, s.noise.topicGap = p.alertGap, p.topicGap
  s.noise.actionable = p.actionable and true or nil
  if s.panel then s.panel.nowMax = p.nowMax end
  if p.soundTips ~= nil then s.soundTips = p.soundTips end
  self:Fire("PRESET_CHANGED", name)
  self:RequestRefresh()
  return true
end

---------------------------------------------------------------------------
-- État du joueur (CONTENTFIX) : mort / fantôme / vol / monture / repos… -> qui a le droit de parler.
--   TreeAI:PlayerState() -> { dead, ghost, taxi, mounted, resting, inCombat, inInstance, afk } (cache 0,25 s)
--   TreeAI:Quiet(kind) -> true, raison quand une ligne de ce genre doit se taire :
--     "buff"   (aspect, buff, familier, munitions) : mort, fantôme, vol, monture, repos
--     "advice" (tout le reste, défaut)              : mort, fantôme, vol (gardé, réaffiché après)
--     "death"  (course au corps, gardien des esprits, récap de mort) : jamais
-- Suggest applique la règle : "buff" jeté, "advice" mis en attente (caché, sans son ni encadré).
---------------------------------------------------------------------------
local stateCache, stateAt = nil, -1
local function tryState(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, v = pcall(fn, ...)
  if not ok then return nil end
  if _G.issecretvalue then
    local ok2, s = pcall(_G.issecretvalue, v)
    if ok2 and s then return nil end
  end
  return v
end
function TreeAI:PlayerState(force)
  local t = now()
  if stateCache and not force and t - stateAt < 0.25 then return stateCache end
  local ghost = tryState(_G.UnitIsGhost, "player") and true or false
  local dog = tryState(_G.UnitIsDeadOrGhost, "player") and true or false
  local inst = tryState(_G.IsInInstance)
  stateCache = {
    dead = (dog and not ghost) or false, ghost = ghost,
    taxi = tryState(_G.UnitOnTaxi, "player") and true or false,
    mounted = tryState(_G.IsMounted) and true or false,
    resting = tryState(_G.IsResting) and true or false,
    inCombat = self:InCombat(),
    inInstance = inst and true or false,
    afk = tryState(_G.UnitIsAFK, "player") and true or false,
  }
  stateAt = t
  return stateCache
end
function TreeAI:InvalidatePlayerState() stateCache = nil end

TreeAI.QUIET_RULES = TreeAI.QUIET_RULES or {
  buff = { "dead", "ghost", "taxi", "mounted", "resting" },
  advice = { "dead", "ghost", "taxi" },
  death = {}, always = {},
}
-- sujet -> genre (aspects, familier, munitions = rappels « buff »)
TreeAI.QUIET_TOPICS = TreeAI.QUIET_TOPICS or { aspect = "buff", pet = "buff", petfood = "buff", ammo = "buff", buff = "buff" }
-- modules dont tous les conseils sont des rappels de buff
TreeAI.QUIET_MODULES = TreeAI.QUIET_MODULES or { Classes = "buff" }

function TreeAI:Quiet(kind)
  local rule = self.QUIET_RULES[kind or "advice"] or self.QUIET_RULES.advice
  if #rule == 0 then return false end
  local st = self:PlayerState()
  for _, k in ipairs(rule) do if st[k] then return true, k end end
  return false
end

--- Genre d'une suggestion / ligne : opts.kind ou e.kind, sinon module / sujet, sinon "advice".
function TreeAI:QuietKindOf(e)
  if type(e) ~= "table" then return "advice" end
  if e.kind then return e.kind end
  if e.death then return "death" end
  local m = e.module and self.QUIET_MODULES[e.module]
  if m then return m end
  local topic = e.topic or (e.id and self.TOPIC_IDS[e.id])
  return (topic and self.QUIET_TOPICS[topic]) or "advice"
end

TreeAI.STATE_EVENTS = { "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST", "PLAYER_UPDATE_RESTING" }
for _, ev in ipairs(TreeAI.STATE_EVENTS) do
  if not TreeAI[ev] then
    TreeAI[ev] = function(self) stateCache = nil; self:RequestRefresh() end
  end
end

-- Glyphes absents des polices WoW (carrés vides en jeu) -> équivalents lisibles. Appliqué aux textes
-- des conseils et des lignes d'onglet.
local GLYPHS = {
  ["▶"] = "", ["►"] = "", ["→"] = ">", ["←"] = "<", ["⇒"] = ">", ["✔"] = "OK", ["✓"] = "OK",
  ["✖"] = "x", ["✗"] = "x", ["●"] = "•", ["○"] = "o", ["↑"] = "+", ["↓"] = "-", ["≠"] = "<>",
  ["★"] = "*", ["☆"] = "*", ["⚠"] = "!", ["⏱"] = "", ["⌛"] = "",
  ["▾"] = "", ["▴"] = "", ["▲"] = "^", ["▼"] = "v", ["◀"] = "<", ["◂"] = "<", ["▸"] = ">",   -- (4.0) « Zone ▾ », Todo…
}
local safeCache, safeN = {}, 0
local function safeText(s)
  if not strfind(s, "[\226\227]") then return s end
  local c = safeCache[s]
  if c then return c end
  local r = strgsub(s, "[\226\227][\128-\191][\128-\191]", GLYPHS)
  r = strgsub(strgsub(strgsub(r, "^%s+", ""), "%s+$", ""), "  +", " ")   -- « Zone : X ▾ » -> « Zone : X »
  if safeN > 400 then safeCache, safeN = {}, 0 end
  safeCache[s], safeN = r, safeN + 1
  return r
end
TreeAI.SafeText = safeText

-- ≤ 2 lignes courtes : au-delà de LINE_MAX caractères visibles, la fin (après « — », « . », « ; »…)
-- passe dans l'infobulle ; sinon coupe au dernier espace + « … » (texte complet dans l'infobulle).
TreeAI.LINE_MAX = 96
local function ulen(s)
  s = strgsub(strgsub(s, "|c%x%x%x%x%x%x%x%x", ""), "|r", "")
  if strfind(s, "|T", 1, true) then s = strgsub(s, "|T[^|]*|t", "##") end   -- (REVIEW) une icône |T…|t = ~2 caractères à l'écran, pas 45
  local _, n = strgsub(s, "[\128-\191]", "")
  return #s - n
end
TreeAI.TextLen = ulen

-- (4.0) LA coupe de texte de TreeAI, pour tous les modules : n caractères UTF-8 (« é » compte pour un et n'est
-- jamais coupé en deux ; les anciennes coupes en octets affichaient « Les loups-garous de la for\195… »).
-- Plus de n caractères : le début + tail (« … » par défaut, "" = coupe sèche), n caractères en tout, sans
-- espace avant la fin ni code |c… / |T… à moitié (une couleur ouverte est refermée). Sinon le texte tel quel.
local function charEnd(s, k)            -- dernier octet des k premiers caractères (#s s'il y en a ≤ k)
  local pos, i = 1, 0
  while true do
    local a = strfind(s, "[^\128-\191]", pos)
    if not a then return #s end
    if i == k then return a - 1 end
    i, pos = i + 1, a + 1
  end
end
local trimMemo, trimMemoN = {}, 0   -- (4.0.1) coupes déjà faites (mêmes titres redemandés à chaque rendu)
local function cutText(s, n, tail)
  s = tostring(s or "")
  n = tonumber(n)
  if not n or #s <= n then return s end
  tail = tail or "…"
  local ck = s .. "\1" .. n .. "\1" .. tail
  local c = trimMemo[ck]
  if c then return c end
  if trimMemoN > 600 then trimMemo, trimMemoN = {}, 0 end
  c = TreeAI._CutRaw(s, n, tail)
  trimMemo[ck], trimMemoN = c, trimMemoN + 1
  return c
end
function TreeAI._CutRaw(s, n, tail)
  if charEnd(s, n) >= #s then return s end
  local _, cont = strgsub(tail, "[\128-\191]", "")
  local keep = n - (#tail - cont)
  local head = keep > 0 and strgsub(strsub(s, 1, charEnd(s, keep)), "%s+$", "") or ""
  if strfind(head, "|", 1, true) then
    head = strgsub(strgsub(strgsub(head, "|c%x*$", ""), "|T[^|]*$", ""), "([^|])|$", "%1")
    if strfind(head, "|c%x%x%x%x%x%x%x%x[^|]*$") then return head .. tail .. "|r" end
  end
  return head .. tail
end
TreeAI.Cut = cutText
local SEPS = { " — ", ". ", " ; ", " : ", " (", ", " }
local function shortText(text, tooltip)
  local max = TreeAI.LINE_MAX
  if #text <= max or ulen(text) <= max then return text, tooltip end
  local head, tail
  for _, sep in ipairs(SEPS) do
    local best, init = nil, 1
    while true do
      local a, b = strfind(text, sep, init, true)
      if not a then break end
      if ulen(strsub(text, 1, a - 1)) <= max then best = a else break end
      init = b + 1
    end
    if best and ulen(strsub(text, 1, best - 1)) >= 24 then
      head = strsub(text, 1, best - 1)
      tail = strgsub(strsub(text, best), "^[%s—%.;:,]+", "")
      if sep == " (" then tail = strgsub(strgsub(tail, "^%(", ""), "%)%s*%.?$", "") end
      break
    end
  end
  if not head then
    local cutAt = 1
    local init = 1
    while true do
      local a = strfind(text, " ", init, true)
      if not a or ulen(strsub(text, 1, a - 1)) > max - 1 then break end
      cutAt, init = a, a + 1
    end
    head, tail = strsub(text, 1, cutAt - 1) .. "…", text
  end
  if (strfind(head, "|c", 1, true) or 0) > (strfind(head, "|r", 1, true) or 0) then head = head .. "|r" end
  tail = strgsub(tail, "^%s+", "")
  if tail == "" then return head, tooltip end
  local tip
  if type(tooltip) == "table" then
    tip = { tail }
    for i = 1, #tooltip do tip[#tip + 1] = tooltip[i] end
  elseif type(tooltip) == "string" and tooltip ~= "" then tip = { tail, tooltip }
  else tip = tail end
  return head, tip
end
TreeAI.ShortText = shortText

--- Nom propre d'un objectif de quête : « Atrexis : 0/1 », « 0/1 Atrexis », « Atrexis tué : 0/1 (Terminé) »
--- -> « Atrexis ». Espaces insécables du client FR compris. Jamais nil.
local objNameCache, objNameN = {}, 0   -- (4.0.1) même texte = même nom (le trajet le redemande à chaque rendu)
function TreeAI.ObjectiveName(text)
  local key = text
  if key ~= nil then
    local c = objNameCache[key]
    if c then return c end
  end
  local r = TreeAI._ObjectiveName(text)
  if key ~= nil then
    if objNameN > 400 then objNameCache, objNameN = {}, 0 end
    objNameCache[key], objNameN = r, objNameN + 1
  end
  return r
end
function TreeAI._ObjectiveName(text)
  local s = tostring(text or "")
  s = strgsub(s, "\194\160", " ")
  s = strgsub(s, "\226\128\175", " ")
  s = strgsub(s, "|c%x%x%x%x%x%x%x%x", "")
  s = strgsub(s, "|r", "")
  s = strgsub(s, "%s*%(%s*[TtCcFf][^%)]*%)%s*$", "")          -- (Terminé) (Complete) (Facultatif)
  s = strgsub(s, "%s*:?%s*%d+%s*/%s*%d+%s*$", "")             -- … : 0/1
  s = strgsub(s, "^%s*%d+%s*/%s*%d+%s*", "")                  -- 0/1 …
  s = strgsub(s, "%s*:%s*$", "")
  s = strgsub(s, "^%s+", "")
  s = strgsub(s, "%s+$", "")
  return s
end

-- Mode simple : préréglage par défaut (et proposé une fois aux anciens utilisateurs)
TreeAI.SIMPLE_VERSION = 1
function TreeAI:_OfferSimpleMode(fresh)
  local s = self.settings
  if not s or (tonumber(s.simpleV) or 0) >= self.SIMPLE_VERSION then return end
  s.simpleV = self.SIMPLE_VERSION
  local prev = s.preset
  if prev == "simple" then return end
  self:ApplyPreset("simple")
  if fresh then return end
  local back = (prev and self.PRESETS[prev]) and prev or "normal"
  self:Suggest("core:simple", "Mode simple activé : 3 conseils utiles. Clique pour revenir à « "
    .. (self.PRESET_LABEL[back] or back) .. " ».", 2, { ttl = 180, sound = false, kind = "always",
      onClick = function() self:ApplyPreset(back); self:ClearSuggestion("core:simple") end })
end

-- encadré : 1 alerte max / alertGap s (20 s par défaut), un même sujet pas avant topicGap s,
-- en combat seulement le danger de mort (le reste attend la fin du combat, s'il est encore vrai)
local alertState = { last = -1e9, topics = {}, pending = nil, shown = 0, blocked = 0 }
TreeAI._alertState = alertState
function TreeAI:ResetAlertGate()
  alertState.last, alertState.topics, alertState.pending = -1e9, {}, nil
end

function TreeAI:AlertAllowed(e, opts)
  local n, t = noiseCfg(), now()
  local danger = self:IsDanger(e, opts)
  if self:InCombat() and not danger then
    alertState.pending, alertState.blocked = e.id, alertState.blocked + 1
    return false, "combat"
  end
  if t - alertState.last < (tonumber(n.alertGap) or 20) then
    alertState.blocked = alertState.blocked + 1
    return false, "gap"
  end
  local topic = self:TopicOf(e) or e.id
  local tl = topic and alertState.topics[topic]
  if tl and t - tl < (tonumber(n.topicGap) or 120) and not danger then
    alertState.blocked = alertState.blocked + 1
    return false, "topic"
  end
  alertState.last = t
  if topic then alertState.topics[topic] = t end
  alertState.shown = alertState.shown + 1
  return true
end

-- fin de combat : l'alerte importante retenue pendant le combat, si elle est toujours d'actualité
function TreeAI:_FlushPendingAlert()
  local id = alertState.pending
  alertState.pending = nil
  local e = id and TreeAI._suggestions[id]
  if not e or (e.expires and e.expires <= now()) or e.prio < 3 then return end
  if self:AlertAllowed(e) then
    -- same « once per meaning » rule as Suggest (QA: « Familier à 30 % » popped after every fight)
    local t = now()
    local sk = strgsub(tostring(e.text or ""), "%d+", "#")
    local pa = TreeAI._alerted and TreeAI._alerted[id]
    if pa and pa.sk == sk and (t - pa.at) < 300 then return end
    if TreeAI._alerted then TreeAI._alerted[id] = { sk = sk, at = t } end
    playUrgentSound(e)
    if self.ShowAlert then safecall(self.ShowAlert, self, e) end
  end
end

-- (4.1 LAYOUT) propositions d'autres modules rangées sur une page (pas à l'écran) : la question « Accepter et rendre
-- les quêtes tout seul ? » de la barre de quête → page « Fonctions » (l'option est aussi en bas de la page Quêtes)
TreeAI.OWN_PAGE = TreeAI.OWN_PAGE or { ["mod:quick:auto"] = "modules" }
function TreeAI:Suggest(id, text, prio, opts)
  if type(id) ~= "string" then id = tostring(id) end
  if text == nil or text == "" then self:ClearSuggestion(id); return nil end
  -- aides PvP désactivées (défaut) : rien contre un joueur ennemi, rien en champ de bataille / arène,
  -- et jamais une suggestion marquée opts.pvp
  if not (self.settings and self.settings.pvp) and ((opts and opts.pvp) or self:InPvP()) then
    return nil
  end
  text = tostring(text)
  prio = tonumber(prio) or 1
  if prio < 1 then prio = 1 elseif prio > 3 then prio = 3 end
  opts = opts or {}
  -- module coupé en direct (options, /tai module X off) : ses minuteurs ne doivent plus rien afficher
  local owner = opts.module and self.modules[opts.module]
  if owner and owner._active == false then return nil end
  -- état du joueur : rappels de buff jetés (mort / vol / monture / repos), autres conseils mis en attente
  local kind = self:QuietKindOf({ kind = opts.kind, death = opts.death, module = opts.module, topic = opts.topic, id = id })
  local quiet = self:Quiet(kind)
  if quiet and kind == "buff" then self:ClearSuggestion(id); return nil end
  local tip
  text, tip = shortText(safeText(text), opts.tooltip)
  if self:IsDismissed(id, text, opts.danger) then return nil end   -- masquée par le joueur (clic droit)
  local t = now()
  local ttl = opts.ttl
  if ttl == nil then ttl = 60 end
  local isNew = suggestions[id] == nil
  local e = suggestions[id] or {}
  local changed = (e.text ~= text) or (e.prio ~= prio)
  -- LITE : le même conseil redit à l'identique (chaque actualisation) ne redessine rien
  local visible = isNew or changed or e.tab ~= opts.tab or e.icon ~= opts.icon or e.kind ~= kind
    or (e.expires ~= nil and e.expires <= t)
  -- first appearance (or new prio): the order inside a prio follows it, so a module re-suggesting the
  -- same advice every refresh does not reshuffle « Maintenant » (counters in the text do not count)
  if not e.since or e.prio ~= prio then e.since = t end
  e.id, e.text, e.prio, e.time = id, text, prio, t
  e.expires = (ttl and ttl > 0) and (t + ttl) or nil
  e.module, e.icon, e.onClick, e.macro = opts.module, opts.icon, opts.onClick, opts.macro
  e.tab, e.tooltip = opts.tab, tip
  e.topic, e.danger = opts.topic, opts.danger
  -- (4.1 LAYOUT) conseil non demandé (addons, macros, conseils pro, mode sûr) : seulement sur SA page de la fenêtre,
  -- jamais dans « Maintenant » / la carte / l'écran (sauf danger)
  e.ownPage = (not opts.danger) and (opts.ownPage or TreeAI.OWN_PAGE[id]) or nil
  e.kind = kind
  suggestions[id] = e
  if quiet then   -- en attente : ni son, ni encadré, ni journal ; réaffiché quand l'état revient
    if visible then self:RequestRefresh() end
    return e
  end

  -- announce (sound/chat) at most once per cooldown for same id+text
  local cd = opts.cooldown or 30
  local a = announced[id]
  if not a or a.text ~= text or (t - a.at) >= cd then
    announced[id] = { text = text, at = t }
    if changed or not a then logSuggestion(e) end
    local minPrio = (self.settings and self.settings.minPrio) or 1
    if prio >= minPrio then
      -- important (prio 3) : encadré + son, passés au filtre anti-bruit (≤ 1 / 20 s, sujet, combat)
      local alertOK = prio >= 3 and self:AlertAllowed(e, opts)
      local doSound = opts.sound
      if alertOK and doSound ~= false then playUrgentSound(e, opts)
      elseif prio < 3 and doSound == true and (opts.soundKind or (self.settings and self.settings.soundTips)) then
        self:Sound(opts.soundKind or "reminder")   -- genre choisi par le module (rare, quête…) ou rappel
      end
      -- corner alert: POLISH filter (≤ 1 / 20 s, topic, combat) AND once per id, again only if the meaning
      -- changed (digits ignored) or after 5 min (QA)
      if alertOK and self.ShowAlert then
        local sk = strgsub(text, "%d+", "#")
        local pa = alerted[id]
        if not pa or pa.sk ~= sk or (t - pa.at) >= 300 then
          alerted[id] = { sk = sk, at = t }
          safecall(self.ShowAlert, self, e)
        end
      end
      -- important items go to the corner alert box; the centre error text only if that box is off
      local al = self.settings and self.settings.alert
      if alertOK and al and al.enabled == false and _G.UIErrorsFrame and _G.UIErrorsFrame.AddMessage then
        pcall(_G.UIErrorsFrame.AddMessage, _G.UIErrorsFrame, text, 1, 0.3, 0.2)
      end
      -- advice never goes to the chat unless the player opted in (settings.chat, off by default)
      local doChat = self.settings and self.settings.chat and opts.chat ~= false
      if doChat then self:Print(text) end
    end
  end
  if visible then
    self:Fire("SUGGESTION", e)
    self:RequestRefresh()
  end
  return e
end

function TreeAI:ClearSuggestion(id)
  if suggestions[id] then
    suggestions[id] = nil
    self:Fire("SUGGESTION_CLEARED", id)
    self:RequestRefresh()
  end
end

--- LITE : le panneau a-t-il quelque chose de neuf à montrer ? (génération changée ou conseil expiré)
local panelGen, renderedGen = 1, 0
local contentGen = 0   -- (LITE) tout changement de contenu (même d'un onglet caché) : invalide le cache GetTabLines
local dataGen = 0      -- (UIREDO) contenu changé (lignes, conseils) — pas les simples redessins : pastilles, carte
function TreeAI:MarkDirty() panelGen = panelGen + 1; contentGen = contentGen + 1; dataGen = dataGen + 1 end
function TreeAI:DataGen() return dataGen end
function TreeAI:_PanelRendered() renderedGen = panelGen; contentGen = contentGen + 1 end   -- nouveau passage : cache neuf
function TreeAI:PanelDirty()
  if panelGen ~= renderedGen then return true end
  local t = now()
  for _, e in pairs(suggestions) do if e.expires and e.expires <= t then return true end end
  return false
end

function TreeAI:ClearSuggestions(prefix)
  local any = false
  for id in pairs(suggestions) do
    if not prefix or strsub(id, 1, #prefix) == prefix then
      suggestions[id] = nil
      any = true
      self:Fire("SUGGESTION_CLEARED", id)
    end
  end
  if any then self:RequestRefresh() end
end

function TreeAI:GetSuggestions()
  local t = now()
  local list = {}
  local minPrio = (self.settings and self.settings.minPrio) or 1
  local qAdv, qBuf = self:Quiet("advice"), self:Quiet("buff")
  for id, e in pairs(suggestions) do
    if e.expires and e.expires <= t then
      suggestions[id] = nil
      contentGen = contentGen + 1
    elseif e.prio >= minPrio and not ((e.kind == nil or e.kind == "advice") and qAdv or (e.kind == "buff" and qBuf)) then
      list[#list + 1] = e
    end
  end
  tsort(list, function(a, b)
    if a.prio ~= b.prio then return a.prio > b.prio end
    local sa, sb = a.since or a.time, b.since or b.time
    if sa ~= sb then return sa > sb end
    return a.id < b.id
  end)
  return list
end

---------------------------------------------------------------------------
-- Panel tabs (shared contract: RegisterTab / SetTabLines; Suggest -> « Maintenant »)
---------------------------------------------------------------------------
TreeAI.tabs = TreeAI.tabs or {}
local tabSources = {}   -- tabId -> { order = {source,...}, lines = { [source] = {entries} } }
local NOW_TAB, DEFAULT_TAB = "now", "info"
-- where a module's suggestions are listed (besides « Maintenant »); others -> « Infos »
TreeAI.MODULE_TAB = TreeAI.MODULE_TAB or {
  Quests = "quests", Leveling = "quests", Tracker = "quests", Compass = "quests",
  Group = "group", Squad = "group", Missions = "group",   -- (4.0) Squad / Missions = façades de Group (ids gardés)
  Auction = "hv",
}

function TreeAI:RegisterTab(id, title, order)
  if type(id) ~= "string" or id == "" then return nil end
  local t = self.tabs[id] or { id = id }
  if t.auto and title ~= nil then t.order, t.title, t.auto = nil, nil, nil end   -- auto-created by SetTabLines
  t.title = title and tostring(title) or t.title or id
  t.order = t.order or tonumber(order) or 100   -- a tab keeps its first order (core tabs stay in place)
  self.tabs[id] = t
  if self.RequestRefresh then self:RequestRefresh() end
  return t
end

function TreeAI:GetTabs()
  local list = {}
  for id, t in pairs(self.tabs) do
    if not t.virtual and not (self.IsTabOff and self:IsTabOff(id)) then list[#list + 1] = t end
  end
  tsort(list, function(a, b)
    if a.order ~= b.order then return a.order < b.order end
    return a.id < b.id
  end)
  return list
end

local function normLine(l, source)
  if type(l) == "string" then l = { text = l } end
  if type(l) ~= "table" or l.text == nil or l.text == "" then return nil end
  local prio = tonumber(l.prio) or 1
  if prio < 1 then prio = 1 elseif prio > 3 then prio = 3 end
  local text, tip = shortText(safeText(tostring(l.text)), l.tooltip)
  return { text = text, prio = prio, icon = l.icon, kind = l.kind, death = l.death,
    onClick = type(l.onClick) == "function" and l.onClick or nil,
    onRightClick = type(l.onRightClick) == "function" and l.onRightClick or nil,   -- (ZONEPLAN) clic droit : menu de la ligne
    tooltip = tip, pin = l.pin and true or nil, id = l.id, source = source, data = l.data,
    -- control = true : bouton dans la barre de l'onglet (pas une ligne) ; label = texte court du bouton
    control = (l.control and type(l.onClick) == "function") and true or nil,
    label = l.label and safeText(tostring(l.label)) or nil,
    -- MENU : quête liée (regroupement par région), en-tête de bloc, colonne de droite (chiffres), texte du clic
    questID = tonumber(l.questID) or nil, header = l.header and true or nil,
    right = l.right and safeText(tostring(l.right)) or nil, clickHint = l.clickHint,
    _rt = l.text, _rp = l.prio, _rtip = l.tooltip, _rc = l.control, _rl = l.label }
end

local function hasPin(list)
  if not list then return false end
  for i = 1, #list do if list[i].pin then return true end end
  return false
end

-- LITE : mêmes lignes qu'avant (texte, prio, icône…) ? -> on ne garde que les nouveaux clics / données,
-- sans normaliser ni redessiner le panneau (la plupart des modules renvoient la même chose à chaque tour)
local function sameLines(old, lines)
  if not old then return false end
  local j = 0
  for i = 1, #lines do
    local l = lines[i]
    local txt, prio, icon, pin, tip, kind, lid, death
    if type(l) == "table" then
      txt, prio, icon, pin, tip, kind, lid, death = l.text, l.prio, l.icon, l.pin, l.tooltip, l.kind, l.id, l.death
    elseif type(l) == "string" then
      txt = l
    end
    if txt ~= nil and txt ~= "" then
      j = j + 1
      if j > 100 then break end
      local o = old[j]
      if not o or o._rt ~= txt or o._rp ~= prio or o.icon ~= icon or (o.pin or false) ~= (pin and true or false)
        or o._rtip ~= tip or o.kind ~= kind or o.id ~= lid or o.death ~= death
        or (type(l) == "table" and (o._rc ~= l.control or o._rl ~= l.label or o.questID ~= (tonumber(l.questID) or nil)
          or o.right ~= l.right)) then return false end
    end
  end
  if math.min(j, 100) ~= #old then return false end
  j = 0
  for i = 1, #lines do
    local l = lines[i]
    if type(l) == "string" and l ~= "" then j = j + 1
    elseif type(l) == "table" and l.text ~= nil and l.text ~= "" then
      j = j + 1
      if j > 100 then break end
      old[j].onClick = type(l.onClick) == "function" and l.onClick or nil
      old[j].onRightClick = type(l.onRightClick) == "function" and l.onRightClick or nil
      old[j].data = l.data
    end
  end
  return true
end

-- lines = { {text=, prio=1..3, icon=?, onClick=?, tooltip=?, pin=?}, ... } replaces the content of
-- `source` (default "main"): several modules can fill one tab, each with its own block, in call order.
-- pin = true also offers the line to « Maintenant ».
function TreeAI:SetTabLines(id, lines, source)
  if type(id) ~= "string" then return false end
  if not self.tabs[id] then self:RegisterTab(id, nil, 100); self.tabs[id].auto = true end
  source = source and tostring(source) or "main"
  local ts = tabSources[id]
  if not ts then ts = { order = {}, lines = {} }; tabSources[id] = ts end
  if type(lines) == "table" and sameLines(ts.lines[source], lines) then return true end
  local out = {}
  if type(lines) == "table" then
    for i = 1, #lines do
      local e = normLine(lines[i], source)
      if e then out[#out + 1] = e end
      if #out >= 100 then break end
    end
  end
  local isNewSource = ts.lines[source] == nil
  if isNewSource then ts.order[#ts.order + 1] = source end
  local old = ts.lines[source]
  ts.lines[source] = out
  contentGen = contentGen + 1
  dataGen = dataGen + 1
  -- LITE : un onglet qu'on ne voit pas ne redessine pas le panneau (le clic sur l'onglet le reconstruit) ;
  -- une ligne épinglée (offerte à « Maintenant ») compte toujours
  if isNewSource or self:TabShown(id) or hasPin(out) or hasPin(old) then self:RequestRefresh() end
  return true
end

--- l'onglet est-il à l'écran en ce moment ? (panneau ouvert ; mini = « Maintenant » + 1re ligne de « Quêtes »)
function TreeAI:TabShown(id)
  local p = self.GetPanel and self:GetPanel()
  if not p or not self.GetActiveTab then return true end
  if not p:IsShown() then return false end
  if self.IsMini and self:IsMini() then return id == NOW_TAB or id == "quests" end
  local cur = self:GetActiveTab()
  if cur == id then return true end
  local vt = self.tabs[cur]
  return (vt and vt.virtual and type(vt.from) == "table" and vt.from.tab == id) and true or false
end

local function plain(text)
  text = strgsub(tostring(text or ""), "|c%x%x%x%x%x%x%x%x", "")
  text = strgsub(text, "|r", "")
  return text
end
TreeAI.PlainText = plain

-- Formats communs (tous les modules peuvent s'en servir) : 1 234 · 12 po 3 pa · 45 s / 12 min / 1 h 05 · 85 %
TreeAI.Fmt = TreeAI.Fmt or {}
local Fmt = TreeAI.Fmt
local numMemo, numMemoN = {}, 0   -- (4.0.1) « 1 234 » déjà formatés (mêmes nombres redemandés à chaque rendu)
function Fmt.Num(n)
  n = tonumber(n) or 0
  local neg = n < 0
  local k = not neg and math.floor(n + 0.5) or nil
  local c = k and numMemo[k]
  if c then return c end
  local s = tostring(math.floor(math.abs(n) + 0.5)):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  s = s:gsub("^ ", "")
  c = (neg and "-" or "") .. s
  if k then
    if numMemoN > 500 then numMemo, numMemoN = {}, 0 end
    numMemo[k], numMemoN = c, numMemoN + 1
  end
  return c
end
function Fmt.Money(c)
  c = math.floor(tonumber(c) or 0)
  local neg = c < 0
  c = math.abs(c)
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  local t = {}
  if g > 0 then t[#t + 1] = Fmt.Num(g) .. " po" end
  if s > 0 then t[#t + 1] = s .. " pa" end
  if (k > 0 and g == 0) or #t == 0 then t[#t + 1] = k .. " pc" end
  return (neg and "-" or "") .. tconcat(t, " ")
end
function Fmt.Time(sec)
  sec = tonumber(sec)
  if not sec then return "?" end
  sec = math.max(0, math.floor(sec + 0.5))
  if sec < 60 then return sec .. " s" end
  if sec < 3600 then
    local m, s = math.floor(sec / 60), sec % 60
    if m < 10 and s > 0 then return strformat("%d min %02d", m, s) end
    return math.floor(sec / 60 + 0.5) .. " min"
  end
  if sec < 86400 then return strformat("%d h %02d", math.floor(sec / 3600), math.floor(sec / 60) % 60) end
  return strformat("%d j %d h", math.floor(sec / 86400), math.floor(sec / 3600) % 24)
end
function Fmt.Pct(x) return math.floor((tonumber(x) or 0) + 0.5) .. " %" end

-- bloc de lignes -> module propriétaire (coupé = bloc masqué tout de suite, sans /reload)
TreeAI.SOURCE_OWNER = TreeAI.SOURCE_OWNER or { missions = "Group" }   -- (4.0) bloc des missions : module Groupe
TreeAI.MAIN_OWNER = TreeAI.MAIN_OWNER or { quests = "Tracker", hv = "Auction" }
TreeAI.TAB_OWNER = TreeAI.TAB_OWNER or {}
local function moduleOff(name)
  local m = name and TreeAI.modules[name]
  return (m and m._active == false) and true or false
end
local function sourceActive(tabId, src)
  if src == "main" then return not moduleOff(TreeAI.MAIN_OWNER[tabId]) end
  return not moduleOff(TreeAI.SOURCE_OWNER[src] or src)
end
TreeAI._SourceActive = sourceActive

function TreeAI:TabOf(e)
  if e.tab and self.tabs[e.tab] then return e.tab end
  return (e.module and self.MODULE_TAB[e.module]) or DEFAULT_TAB
end

function TreeAI:IsTabOff(id)
  local owner = self.TAB_OWNER and self.TAB_OWNER[id]
  local m = owner and self.modules[owner]
  return (m and m._active == false) and true or false
end

-- Entries shown in a tab. « Maintenant » = top N by prio of suggestions + pinned tab lines, deduped
-- by visible text AND by meaning (topic: ammo / bags / repair… from several modules -> one line),
-- at most `perModule` lines per module, prio >= nowMinPrio (pinned quest step exempt).
-- Other tabs = their SetTabLines blocks, then the suggestions routed to them.
local tabCache = {}   -- id -> { gen, at, out } : un redessin demande chaque onglet (couleur des boutons)
function TreeAI:GetTabLines(id)
  local c = tabCache[id]
  local t = now()
  -- l'état du joueur (mort, vol…) cache / rend des lignes sans toucher au contenu : il fait partie de la clé
  local q = (self:Quiet("advice") and 1 or 0) + (self:Quiet("buff") and 2 or 0)
  if c and c.gen == contentGen and c.q == q and t - c.at < 1 and c.n == #c.out then return c.out end
  local out = self:_GetTabLines(id)
  tabCache[id] = { gen = contentGen, at = t, out = out, n = #out, q = q }
  return out
end
function TreeAI:_GetTabLines(id)
  local out, seen = {}, {}
  local function push(e)
    local k = plain(e.text)
    if seen[k] then return false end
    seen[k] = true
    out[#out + 1] = e
    return true
  end
  local sugg = self:GetSuggestions()
  if id == NOW_TAB then
    local cand = {}
    for _, e in ipairs(sugg) do if not e.ownPage then cand[#cand + 1] = e end end
    for _, t in ipairs(self:GetTabs()) do
      local ts = tabSources[t.id]
      if ts then
        for _, src in ipairs(ts.order) do
          if sourceActive(t.id, src) then
            for _, l in ipairs(ts.lines[src] or {}) do
              if l.pin and not self:Quiet(self:QuietKindOf(l)) then cand[#cand + 1] = l end
            end
          end
        end
      end
    end
    -- (COHERE) ordre : danger (prio 3) → l'étape de quête épinglée (jamais coupée par le budget) → le reste
    local steps = self.STEP_SOURCES
    for i, e in ipairs(cand) do
      e._rank = i
      local sr = e.pin and e.prio < 3 and steps[e.source or ""]   -- 1 = trajet (arrêt courant), 2 = « Étape »
      e._key = sr and (2.5 + (3 - sr) * 0.01) or e.prio
    end
    tsort(cand, function(a, b)
      if a._key ~= b._key then return a._key > b._key end
      return a._rank < b._rank
    end)
    local max = tonumber(self.settings and self.settings.panel and self.settings.panel.nowMax) or 5
    local nz = noiseCfg()
    local minP, perMod = tonumber(nz.nowMinPrio) or 1, tonumber(nz.perModule) or 0
    local onlyAct = nz.actionable and true or false
    local topics, perCount = {}, {}
    local ext, stepSeen = self:ExternalGuide(), false
    for _, e in ipairs(cand) do
      if #out >= max then break end
      local isStep = e.pin and self.STEP_SOURCES[e.source or ""] and true or false
      local skip = (ext and self:IsGuideLine(e)) or (isStep and stepSeen)
      if isStep and not skip then stepSeen = true end
      local topic = not skip and self:TopicOf(e)
      local modName = e.module or e.source
      if skip then e._also = nil   -- (COHERE) guidé par SPF / Questie, ou 2e en-tête d'étape : pas de ligne
      elseif topic and topics[topic] then
        local kept = topics[topic]
        if modName and modName ~= (kept.module or kept.source) then
          kept._also = kept._also or {}
          kept._also[modName] = true
        end
      elseif (e.prio >= minP or e.pin)
          and not (onlyAct and e.prio < 2 and not (e.onClick or e.macro))
          and not (perMod > 0 and modName and (perCount[modName] or 0) >= perMod) then
        e._also = nil
        if push(e) then
          if topic then topics[topic] = e end
          if modName then perCount[modName] = (perCount[modName] or 0) + 1 end
        end
      end
    end
    return out
  end
  local vt = self.tabs[id]
  if vt and vt.virtual and type(vt.from) == "table" then   -- vue d'un bloc d'un autre onglet (Talents ⊂ Infos)
    local fts = tabSources[vt.from.tab]
    local src = vt.from.source
    if fts and fts.lines[src] and sourceActive(vt.from.tab, src) then
      for _, l in ipairs(fts.lines[src]) do push(l) end
    end
    return out
  end
  local ts = tabSources[id]
  if ts then
    for _, src in ipairs(ts.order) do
      if sourceActive(id, src) then
        for _, l in ipairs(ts.lines[src] or {}) do push(l) end
      end
    end
  end
  for _, e in ipairs(sugg) do
    if self:TabOf(e) == id then push(e) end
  end
  return out
end

-- coalesced panel refresh: at most 2 per second
local lastRefresh, refreshPending = -10, false
function TreeAI:RequestRefresh()
  if self.MarkDirty then self:MarkDirty() end
  if not self.RefreshPanel or refreshPending then return end
  local wait = 0.5 - (now() - lastRefresh)
  if wait <= 0 then
    lastRefresh = now()
    safecall(self.RefreshPanel, self)
    return
  end
  refreshPending = true
  self:After(wait, function()
    refreshPending = false
    lastRefresh = now()
    if TreeAI.RefreshPanel then safecall(TreeAI.RefreshPanel, TreeAI) end
  end)
end

TreeAI:RegisterTab("now", L["Maintenant"], 1)
TreeAI:RegisterTab("quests", L["Quêtes"], 2)
TreeAI:RegisterTab("group", L["Groupe"], 3)
TreeAI:RegisterTab("hv", L["HV"], 4)
TreeAI:RegisterTab("info", L["Infos"], 5)
TreeAI:RegisterTab("modules", L["Fonctions"], 9)   -- LITE : sous « Plus » : un module par ligne, clic = on / off
-- « Talents » : vue du bloc Talents d'« Infos », proposée dans le sous-menu « Plus » (pas un onglet de plus)
TreeAI.tabs.talents = { id = "talents", title = L["Talents"], order = 4.8, virtual = true,
  from = { tab = "info", source = "Talents" } }

-- per-character panel state (tab, position, mini); lock/scale/alpha stay account-wide
function TreeAI:PanelChar()
  local acc = self.settings and self.settings.panel
  if not acc then return nil end
  if not self.db or self:CharKey() == "?" then return acc end
  local c = self:CharDB()
  if type(c.panel) ~= "table" then
    c.panel = { point = acc.point or "CENTER", x = acc.x or 300, y = acc.y or 120 }
  end
  local p = c.panel
  if type(p.tab) ~= "string" then p.tab = "now" end
  if p.mini == nil then p.mini = true end   -- compact by default: one line, expand on demand
  return p
end

---------------------------------------------------------------------------
-- Character summary (read by TreeAI Coach from SavedVariables)
---------------------------------------------------------------------------
function TreeAI:Summary()
  if not self.db or self:CharKey() == "?" then
    self._tmpSummary = self._tmpSummary or {}
    return self._tmpSummary
  end
  return self:CharDB().summary
end

function TreeAI:UpdateSummary()
  local s = self:Summary()
  local function g(fn, ...) return try(_G[fn], ...) end
  local className, classFile = g("UnitClass", "player")
  s.class, s.className = classFile, className
  s.race = g("UnitRace", "player")
  s.level = g("UnitLevel", "player")
  s.xp, s.xpMax = g("UnitXP", "player"), g("UnitXPMax", "player")
  s.rested = g("GetXPExhaustion")
  s.zone = g("GetRealZoneText") or g("GetZoneText")
  s.subzone = g("GetSubZoneText")
  s.money = API.Money()
  s.bagsFree, s.bagsTotal = API.FreeSlots()
  s.guild = g("GetGuildInfo", "player")
  s.inGroup = (g("IsInGroup") or (g("GetNumPartyMembers") or 0) > 0) and true or false
  s.groupSize = g("GetNumGroupMembers") or ((g("GetNumPartyMembers") or 0) + 1)
  if _G.GetSpecialization then
    local idx = g("GetSpecialization")
    if idx and _G.GetSpecializationInfo then
      local _, specName = g("GetSpecializationInfo", idx)
      s.spec = specName
    end
  elseif _G.GetActiveTalentGroup then
    s.specGroup = g("GetActiveTalentGroup")
  end
  s.addonVersion = self.version
  s.updated = wallclock()
  self:Fire("SUMMARY", s)
  return s
end

---------------------------------------------------------------------------
-- Serializer (string/number/boolean/tables; no functions)
---------------------------------------------------------------------------
local function esc(s)
  return (strgsub(s, "[%%|^~=;{}\n\r]", function(c) return strformat("%%%02X", strbyte(c)) end))
end
local function unesc(s)
  return (strgsub(s, "%%(%x%x)", function(h) return strchar(tonumber(h, 16)) end))
end

local function ser(v, depth)
  local tv = type(v)
  if tv == "string" then return "s" .. esc(v)
  elseif tv == "number" then return "n" .. tostring(v)
  elseif tv == "boolean" then return v and "T" or "F"
  elseif tv == "table" and depth < 6 then
    local parts = {}
    for k, val in pairs(v) do
      local sk, sv = ser(k, depth + 1), ser(val, depth + 1)
      if sk and sv then parts[#parts + 1] = sk .. "=" .. sv end
    end
    return "{" .. tconcat(parts, ";") .. "}"
  end
  return nil
end

function TreeAI:Serialize(v) return ser(v, 0) or "" end

local function deser(s, i)
  local c = strsub(s, i, i)
  if c == "{" then
    local t = {}
    i = i + 1
    while i <= #s do
      if strsub(s, i, i) == "}" then return t, i + 1 end
      local k, v
      k, i = deser(s, i)
      if strsub(s, i, i) ~= "=" then return nil, #s + 1 end
      v, i = deser(s, i + 1)
      if k ~= nil then t[k] = v end
      if strsub(s, i, i) == ";" then i = i + 1 end
    end
    return t, i
  elseif c == "T" then return true, i + 1
  elseif c == "F" then return false, i + 1
  elseif c == "s" or c == "n" then
    local j = strfind(s, "[=;}]", i + 1) or (#s + 1)   -- (LITE) un find au lieu d'une boucle caractère par caractère
    local raw = strsub(s, i + 1, j - 1)
    if c == "n" then return tonumber(raw), j end
    if not strfind(raw, "%", 1, true) then return raw, j end
    return unesc(raw), j
  end
  return nil, #s + 1
end

function TreeAI:Deserialize(s)
  if type(s) ~= "string" or s == "" then return nil end
  local ok, v = pcall(deser, s, 1)
  if ok then return v end
  return nil
end

---------------------------------------------------------------------------
-- Comms (C_ChatInfo.SendAddonMessage, prefix TREEAI)
---------------------------------------------------------------------------
local commHandlers = {}  -- msgType -> { [owner] = fn }
local seq = 0
local partial = {}       -- sender..seq -> { n, parts = {} }
local CHUNK = 220
-- Envoi : le jeu tolère ~10 messages d'addon par préfixe en rafale puis 1/s ; au-delà, le message est
-- PERDU sans erreur Lua (code de retour « throttle »). D'où : seau de jetons (8, +1/s), code de retour
-- vérifié, nouvel essai avec délai croissant, deux files (état > quêtes), message d'un type pas encore
-- parti remplacé par le plus récent, pause en donjon/raid (les addons n'y parlent plus aux autres).
local BURST, REFILL = 8, 1
local LOW_PRIO = { SQQ = true, SQD = true, MSM = true, FQUEST = true }
local queues = { {}, {} }  -- [1] état / petits messages, [2] journaux de quêtes
local sending = false
local tokens, tokenAt = BURST, nil
local retryAt, backoff = 0, 1
local msgId, started = 0, {}
TreeAI.commPaused = nil  -- nil | "donjon" | "bloquée" (verrou du jeu)
TreeAI.commStats = { sent = 0, throttled = 0, dropped = 0, paused = 0 }

-- résultat de SendAddonMessage : true/0/nil = ok ; 3/8 (ou false) = limité -> réessayer ; 11 = verrou ; autre = abandon
local RES_THROTTLE, RES_LOCK = { [3] = true, [8] = true }, 11
local function classify(ok, res)
  if not ok then return "error" end
  if res == nil or res == true or res == 0 then return "ok" end
  if res == false or RES_THROTTLE[res] then return "throttle" end
  if res == RES_LOCK then return "lock" end
  return "error"
end
TreeAI._ClassifySend = classify

local function sendRaw(msg, channel, target)
  local C_ChatInfo = _G.C_ChatInfo
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then
    return pcall(C_ChatInfo.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  elseif _G.SendAddonMessage then
    return pcall(_G.SendAddonMessage, TreeAI.PREFIX, msg, channel, target)
  end
  return false
end

local function instancePaused()
  local inInst, kind = try(_G.IsInInstance)
  return (inInst and (kind == "party" or kind == "raid" or kind == "pvp" or kind == "arena")) and true or false
end

local function clearQueues()
  local n = #queues[1] + #queues[2]
  queues[1], queues[2] = {}, {}
  started = {}
  return n
end

function TreeAI:_SetCommPaused(reason)
  if self.commPaused == reason then return end
  local was = self.commPaused
  self.commPaused = reason
  if reason then
    self.commStats.dropped = self.commStats.dropped + clearQueues()
    self.commStats.paused = self.commStats.paused + 1
    -- ANNOY : expliqué une fois par compte et par raison (avant : 2 lignes de chat à CHAQUE donjon)
    local db = self.db
    local flags = type(db) == "table" and (type(db.flags) == "table" and db.flags or {}) or nil
    if type(db) == "table" then db.flags = flags end
    local fk = "commPause_" .. tostring(reason)
    local grp = self.modules and self.modules.Group   -- (4.0) rien à expliquer si « Groupe » est coupé (mode sûr)
    if grp and grp._active and (not flags or not flags[fk]) then
      if flags then flags[fk] = true end
      -- une ligne de l'onglet Groupe, jamais le chat (règle TreeAI)
      self:Suggest("core:commpause", ("Synchro du groupe en pause (%s) : elle reprend à la sortie"):format(
        reason == "donjon" and "donjon" or "bloquée par le jeu"), 1, { ttl = 60, tab = "group", sound = false })
    else
      self:Debug("synchro en pause :", reason)
    end
    self:Fire("COMM_PAUSED", reason)
  elseif was then
    self:Debug("synchro reprise")
    self:Fire("COMM_RESUMED")
  end
end

function TreeAI:_CheckCommPause()
  if instancePaused() then self:_SetCommPaused("donjon")
  elseif self.commPaused then self:_SetCommPaused(nil) end
end

function TreeAI:CommStatus()
  return { paused = self.commPaused, queued = #queues[1] + #queues[2], tokens = math.floor(tokens),
           stats = self.commStats }
end

local function refill(t)
  tokenAt = tokenAt or t
  tokens = math.min(BURST, tokens + (t - tokenAt) * REFILL)
  tokenAt = t
end

local pump
local function schedule(delay)
  if sending then return end
  sending = true
  TreeAI:After(math.max(0.05, delay), function() sending = false; pump() end)
end

pump = function()
  local q = (#queues[1] > 0 and queues[1]) or (#queues[2] > 0 and queues[2]) or nil
  if not q then return end
  if TreeAI.commPaused then TreeAI.commStats.dropped = TreeAI.commStats.dropped + clearQueues(); return end
  local t = now()
  refill(t)
  if t < retryAt then return schedule(retryAt - t) end
  if tokens < 1 then return schedule((1 - tokens) / REFILL) end
  local item = q[1]
  local status = classify(sendRaw(item[1], item[2], item[3]))
  if status == "ok" then
    tremove(q, 1)
    tokens = tokens - 1
    started[item.mid] = true
    backoff = 1
    TreeAI.commStats.sent = TreeAI.commStats.sent + 1
  elseif status == "throttle" then
    TreeAI.commStats.throttled = TreeAI.commStats.throttled + 1
    item.tries = (item.tries or 0) + 1
    tokens = 0
    if item.tries > 6 then tremove(q, 1); TreeAI.commStats.dropped = TreeAI.commStats.dropped + 1 end
    retryAt = t + backoff
    backoff = math.min(backoff * 2, 8)
  elseif status == "lock" then
    TreeAI:_SetCommPaused(instancePaused() and "donjon" or "bloquée")
    return
  else
    tremove(q, 1)
    TreeAI.commStats.dropped = TreeAI.commStats.dropped + 1
  end
  if #queues[1] + #queues[2] > 0 then schedule(0.05) end
end

function TreeAI:DefaultChannel()
  local function g(fn, ...) return try(_G[fn], ...) end
  if g("IsInRaid") or (g("GetNumRaidMembers") or 0) > 0 then return "RAID" end
  if g("IsInGroup") or (g("GetNumPartyMembers") or 0) > 0 then return "PARTY" end
  return nil
end

function TreeAI:Send(msgType, payload, channel, target)
  if not (self.settings and self.settings.comms) then return false end
  if type(msgType) ~= "string" or not msgType:match("^[A-Z0-9_]+$") then return false end
  channel = channel or self:DefaultChannel()
  if not channel then return false end
  local data
  if type(payload) == "table" then data = "t" .. self:Serialize(payload)
  else data = "r" .. tostring(payload or "") end
  if self.commPaused == nil and instancePaused() then self:_SetCommPaused("donjon") end
  if self.commPaused then return false end
  seq = (seq % 9999) + 1
  local q = queues[LOW_PRIO[msgType] and 2 or 1]
  -- même type, même destinataire, pas encore commencé : remplacé par le plus récent
  for i = #q, 1, -1 do
    local it = q[i]
    if it.type == msgType and it[2] == channel and it[3] == target and not started[it.mid] then tremove(q, i) end
  end
  msgId = msgId + 1
  local n = mmax(1, math.ceil(#data / CHUNK))
  for i = 1, n do
    local part = strsub(data, (i - 1) * CHUNK + 1, i * CHUNK)
    q[#q + 1] = { strformat("1~%s~%d~%d/%d~%s", msgType, seq, i, n, part), channel, target, type = msgType, mid = msgId }
  end
  pump()
  return true
end

function TreeAI:RegisterComm(msgType, owner, fn)
  if type(fn) ~= "function" then return false end
  commHandlers[msgType] = commHandlers[msgType] or {}
  commHandlers[msgType][owner or self] = fn
  return true
end

local function deliver(msgType, data, sender, channel)
  local payload
  local kind = strsub(data, 1, 1)
  if kind == "t" then payload = TreeAI:Deserialize(strsub(data, 2)) else payload = strsub(data, 2) end
  local list = commHandlers[msgType]
  if list then
    for owner, fn in pairs(list) do
      if ownerActive(owner) then safecall(fn, owner, payload, sender, channel, msgType) end
    end
  end
  TreeAI:Fire("COMM", msgType, payload, sender, channel)
end

local function isSelf(sender)
  if not sender then return true end
  local me = _G.UnitName and _G.UnitName("player")
  if not me then return false end
  local short = sender:match("^([^%-]+)") or sender
  local key = TreeAI:CharKey()
  return sender == me or sender == key or (short == me and not strfind(sender, "-", 1, true))
      or (key ~= "?" and strgsub(sender, "%s", "") == strgsub(key, "%s", ""))
end

function TreeAI:_OnAddonMessage(prefix, msg, channel, sender)
  if prefix ~= self.PREFIX or type(msg) ~= "string" then return end
  if isSelf(sender) then return end
  local ver, msgType, sq, i, n, data = msg:match("^(%d+)~([A-Z0-9_]+)~(%d+)~(%d+)/(%d+)~(.*)$")
  if ver ~= "1" then return end
  i, n = tonumber(i), tonumber(n)
  if not i or not n or n < 1 or n > 64 then return end
  if n == 1 then return deliver(msgType, data, sender, channel) end
  local key = tostring(sender) .. ":" .. sq
  local p = partial[key]
  if not p then p = { n = n, got = 0, parts = {}, at = now() }; partial[key] = p end
  if not p.parts[i] then p.parts[i] = data; p.got = p.got + 1 end
  if p.got >= p.n then
    partial[key] = nil
    deliver(msgType, tconcat(p.parts, "", 1, p.n), sender, channel)
  end
  -- garbage collect stale partials
  for k, v in pairs(partial) do
    if now() - v.at > 30 then partial[k] = nil end
  end
end

function TreeAI:SendHello(channel)
  local s = self:Summary()
  return self:Send("HELLO", { v = self.version, c = s.class, l = s.level, z = s.zone }, channel)
end

TreeAI:RegisterComm("HELLO", TreeAI, function(self, p, sender)
  if type(p) ~= "table" or not self.db then return end
  local peers = self.db.group.peers
  local isNew = peers[sender] == nil
  peers[sender] = { version = p.v, class = p.c, level = p.l, zone = p.z, seen = wallclock() }
  if isNew and p.r ~= true then
    -- reply once so both sides know each other
    self:After(1 + (math.random and math.random() or 0.5), function()
      local s = self:Summary()
      self:Send("HELLO", { v = self.version, c = s.class, l = s.level, z = s.zone, r = true })
    end)
  end
end)

---------------------------------------------------------------------------
-- Slash commands
---------------------------------------------------------------------------
local slash = {}
local slashOrder = {}

function TreeAI:RegisterSlash(cmd, fn, help)
  if type(cmd) ~= "string" or type(fn) ~= "function" then return false end
  cmd = cmd:lower()
  if not slash[cmd] then slashOrder[#slashOrder + 1] = cmd end
  slash[cmd] = { fn = fn, help = help }
  return true
end

--- MENU : la commande existe-t-elle ? (le menu de la pastille n'affiche que ce qui marche)
function TreeAI:HasSlash(cmd) return type(cmd) == "string" and slash[cmd:lower()] ~= nil end

-- Aide groupée : commande -> rubrique (les commandes des modules sans rubrique vont dans « Autres »)
TreeAI.SLASH_GROUPS = TreeAI.SLASH_GROUPS or {
  { "Démarrer", { "aide", "bienvenue", "mode", "profil", "options" } },
  { "Panneau", { "afficher", "cacher", "basculer", "mini", "onglet", "edit", "placer", "verrou", "reset", "alerte" } },
  { "Quêtes", { "quetes", "suivi", "itineraire", "zone", "pro", "abandon", "surbrillance", "highlight" } },
  { "Boussole", { "boussole", "aller", "cible", "way" } },
  { "Groupe", { "groupe", "objectif", "partir", "pret", "sync" } },
  { "Combat", { "cherche", "stats", "pvp" } },
  { "Macros et touches", { "macros", "binds", "touches", "raccourcis", "combat" } },
  { "HV et métiers", { "hv", "metiers" } },
  { "Outils", { "modules", "module", "statut", "perf", "selftest", "diag", "debug", "test", "bloque" } },
}

--- { { title=, items = { {cmd=, help=}, ... } }, ... } : toutes les commandes documentées, groupées.
function TreeAI:GetSlashHelp()
  local out, used = {}, {}
  for _, g in ipairs(self.SLASH_GROUPS) do
    local items = {}
    for _, c in ipairs(g[2]) do
      local e = slash[c]
      if e and e.help and not used[c] then used[c] = true; items[#items + 1] = { cmd = c, help = L[e.help] } end
    end
    if #items > 0 then out[#out + 1] = { title = L[g[1]], items = items } end
  end
  local other = {}
  for _, c in ipairs(slashOrder) do
    local e = slash[c]
    if e.help and not used[c] then other[#other + 1] = { cmd = c, help = L[e.help] } end
  end
  if #other > 0 then out[#out + 1] = { title = L["Autres"], items = other } end
  return out
end

function TreeAI:PrintHelp()
  self:Print(L["Commandes"] .. " (/tai, /treeai) :")
  for _, g in ipairs(self:GetSlashHelp()) do
    self:Print("|cff33cc66" .. g.title .. "|r")
    for _, it in ipairs(g.items) do self:Print("  |cffffff00/tai " .. it.cmd .. "|r — " .. it.help) end
  end
end

function TreeAI:HandleSlash(input)
  input = tostring(input or "")
  local cmd, rest = input:match("^%s*(%S*)%s*(.-)%s*$")
  cmd = (cmd or ""):lower()
  if cmd == "" or cmd == "aide" or cmd == "help" or cmd == "?" then
    if rest == "chat" or not (self.ShowHelp and self:ShowHelp()) then self:PrintHelp() end
    return
  end
  local entry = slash[cmd]
  if entry then
    safecall(entry.fn, rest)
  else
    self:Print(L["Commande inconnue. Tape /tai aide"])
  end
end

_G.SLASH_TREEAI1 = "/treeai"
_G.SLASH_TREEAI2 = "/tai"
if type(_G.SlashCmdList) == "table" then
  _G.SlashCmdList["TREEAI"] = function(msg) TreeAI:UserAction(TreeAI.HandleSlash, TreeAI, msg) end
end

local function onoff(v) return v and L["activé"] or L["désactivé"] end

TreeAI:RegisterSlash("aide", function() end, "cette aide (/tai aide chat : dans le chat)")
TreeAI:RegisterSlash("mode", function(rest)
  rest = tostring(rest or ""):lower()
  if rest == "" then
    local cur = TreeAI.settings.preset or "normal"
    TreeAI:Print(L["Mode"] .. " : " .. L[TreeAI.PRESET_LABEL[cur] or cur] .. "  (/tai mode simple | calme | normal | tout)")
    return
  end
  if rest == "calm" then rest = "calme" elseif rest == "all" then rest = "tout" end
  if TreeAI:ApplyPreset(rest) then
    TreeAI:Print(L["Mode"] .. " : " .. L[TreeAI.PRESET_LABEL[rest]] .. " — " .. L[TreeAI.PRESET_HELP[rest]])
  else
    TreeAI:Print("/tai mode simple | calme | normal | tout")
  end
end, "simple | calme | normal | tout : combien de conseils tu veux voir")
TreeAI:RegisterSlash("profil", function(rest)
  rest = tostring(rest or ""):lower()
  local want = (rest == "perso" or rest == "char" or rest == "personnage") and "char"
    or ((rest == "compte" or rest == "account") and "account") or nil
  if want then TreeAI:SetProfile(want) end
  TreeAI:Print(L["Réglages"] .. " : " .. (TreeAI:ProfileMode() == "char" and L["propres à ce personnage"]
    or L["communs au compte"]) .. "  (/tai profil perso | compte)")
end, "perso | compte : réglages propres à ce perso ou communs")
TreeAI:RegisterSlash("bienvenue", function() if TreeAI.ShowWizard then TreeAI:ShowWizard(1) end end,
  "relancer l'assistant de démarrage")
TreeAI:RegisterSlash("pvp", function()
  TreeAI.settings.pvp = not TreeAI.settings.pvp
  TreeAI:Print("Aides PvP : " .. (TreeAI.settings.pvp and "activées" or "désactivées (aide à quêter seulement)"))
end, "aides PvP oui/non")
TreeAI:RegisterSlash("afficher", function() TreeAI:ShowPanel() end, L["afficher le panneau"])
TreeAI:RegisterSlash("show", function() TreeAI:ShowPanel() end)
TreeAI:RegisterSlash("cacher", function() TreeAI:HidePanel() end, L["cacher le panneau"])
TreeAI:RegisterSlash("hide", function() TreeAI:HidePanel() end)
TreeAI:RegisterSlash("basculer", function() TreeAI:TogglePanel() end, L["afficher/cacher"])
TreeAI:RegisterSlash("verrou", function()
  local p = TreeAI.settings.panel
  p.locked = not p.locked
  TreeAI:Print(L["Panneau verrouillé"] .. ": " .. onoff(p.locked))
end, L["verrouiller/déverrouiller le panneau"])
TreeAI:RegisterSlash("reset", function()
  if TreeAI.ResetPanel then TreeAI:ResetPanel() end
  TreeAI:Print(L["Position du panneau réinitialisée."])
end, L["remettre le panneau au centre"])
TreeAI:RegisterSlash("mini", function() if TreeAI.ToggleMini then TreeAI:ToggleMini() end end,
  L["panneau réduit à une ligne (oui/non)"])
TreeAI:RegisterSlash("onglet", function(rest)
  rest = tostring(rest or ""):lower()
  local ids = {}
  for _, t in ipairs(TreeAI:GetTabs()) do
    if t.id == rest or TreeAI.PlainText(t.title):lower() == rest then
      if TreeAI.SelectTab then TreeAI:SelectTab(t.id) end
      return
    end
    ids[#ids + 1] = t.id
  end
  TreeAI:Print("/tai onglet " .. tconcat(ids, " | "))
end, L["choisir l'onglet du panneau"])
TreeAI:RegisterSlash("alerte", function()
  if TreeAI.ShowAlert then
    TreeAI:ShowAlert({ text = L["Exemple : encadré d'alerte (clic : ouvre le panneau)."], prio = 3, tab = "now" }, true)
  end
end, L["tester l'encadré d'alerte"])
TreeAI:RegisterSlash("options", function() if TreeAI.OpenOptions then TreeAI:OpenOptions() end end, L["ouvrir les options"])
TreeAI:RegisterSlash("perf", function() TreeAI:PrintPerf() end, "les modules qui coûtent le plus (CPU)")
-- (4.3 V43) UNE commande « profil » : la 1re (perso | compte) était écrasée par celle-ci → les deux usages ici
TreeAI._profilSaves = slash.profil and slash.profil.fn
TreeAI:RegisterSlash("profil", function(rest)
  local profilSaves = TreeAI._profilSaves
  rest = tostring(rest or ""):lower()
  if rest == "sûr" or rest == "safe" then rest = "sur" end
  if profilSaves and (rest == "perso" or rest == "char" or rest == "personnage" or rest == "compte" or rest == "account") then
    return profilSaves(rest)
  end
  if rest == "tout" or rest == "essentiel" or rest == "sur" then TreeAI:SetModuleProfile(rest) end
  local p = TreeAI:ModuleProfile()
  TreeAI:Print("Profil des modules : " .. (TreeAI.MODULE_PROFILE_LABEL[p] or p) .. "  (/tai profil sur | essentiel | tout ; perso | compte : réglages)")
end, "sur | essentiel | tout : profil des modules ; perso | compte : réglages propres à ce perso ou communs")
TreeAI:RegisterSlash("modules", function()
  for name, mod in TreeAI:IterModules() do
    TreeAI:Print("  " .. (mod.title or name) .. " (" .. name .. "): " .. onoff(mod:IsEnabled()))
  end
end, L["liste des modules"])
TreeAI:RegisterSlash("module", function(rest)
  local name, state = rest:match("^(%S+)%s*(%S*)")
  local mod
  if name then
    for n, m in TreeAI:IterModules() do
      if n:lower() == name:lower() then mod = m; name = n end
    end
  end
  if not mod then TreeAI:Print(L["Module introuvable."]); return end
  local enable = not mod:IsEnabled()
  if state == "on" then enable = true elseif state == "off" then enable = false end
  TreeAI:SetModuleEnabled(name, enable)
  TreeAI:Print((mod.title or name) .. ": " .. onoff(enable))
end, L["<Nom> on|off"])
TreeAI:RegisterSlash("sync", function()
  if TreeAI:SendHello() then TreeAI:Print(L["Synchro envoyée au groupe."])
  else TreeAI:Print(L["Pas de groupe (ou comms désactivées)."]) end
end, L["synchroniser avec le groupe"])
TreeAI:RegisterSlash("debug", function()
  TreeAI.settings.debug = not TreeAI.settings.debug
  TreeAI:Print("Debug: " .. onoff(TreeAI.settings.debug))
end, L["mode debug"])
TreeAI:RegisterSlash("statut", function()
  local s = TreeAI:UpdateSummary()
  TreeAI:Print(strformat("v%s | %s %s | %s | %s: %d/%d", TreeAI.version, tostring(s.className or "?"),
    tostring(s.level or "?"), tostring(s.zone or "?"), L["Sacs libres"], s.bagsFree or 0, s.bagsTotal or 0))
  local n = 0
  for _ in pairs(TreeAI.db.group.peers) do n = n + 1 end
  TreeAI:Print(L["Amis TreeAI connus"] .. ": " .. n)
end, L["état de l'addon"])
TreeAI:RegisterSlash("test", function()
  TreeAI:Suggest("core:test1", L["Exemple: conseil d'information."], 1, { ttl = 20 })
  TreeAI:Suggest("core:test2", L["Exemple: conseil utile."], 2, { ttl = 20 })
  TreeAI:Suggest("core:test3", L["Exemple: alerte urgente !"], 3, { ttl = 20, sound = false })
end, L["afficher des suggestions de test"])

---------------------------------------------------------------------------
-- Lifecycle
---------------------------------------------------------------------------
function TreeAI:ADDON_LOADED(_, name)
  if name ~= ADDON_NAME then return end
  initDB()
  self._inited = true
  for _, n in ipairs(self.moduleOrder) do moduleInit(self.modules[n]) end
  local C_ChatInfo = _G.C_ChatInfo
  if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
    pcall(C_ChatInfo.RegisterAddonMessagePrefix, self.PREFIX)
  elseif _G.RegisterAddonMessagePrefix then
    pcall(_G.RegisterAddonMessagePrefix, self.PREFIX)
  end
  if self.InitOptions then safecall(self.InitOptions, self) end
end

function TreeAI:PLAYER_LOGIN()
  if not self._inited then initDB(); self._inited = true end
  self._charKey = nil
  self._loggedIn = true
  -- rebind char data now that the name is known (profil du perso éventuel compris)
  bindSettings()
  for _, n in ipairs(self.moduleOrder) do
    local mod = self.modules[n]
    if not mod._inited then moduleInit(mod) else moduleBindDB(mod) end
  end
  if self._tmpSummary then
    local s = self:Summary()
    for k, v in pairs(self._tmpSummary) do if s[k] == nil then s[k] = v end end
    self._tmpSummary = nil
  end
  self:UpdateSummary()
  safecall(self._OfferSimpleMode, self, self._freshDB)
  -- LITE : profil essentiel posé une fois ; conditions (groupe / instance / HV) avant d'allumer quoi que ce soit
  safecall(self._ApplyProfileOnce, self)
  safecall(self._EvalGates, self)
  if self.CreatePanel then safecall(self.CreatePanel, self) end
  for _, n in ipairs(self.moduleOrder) do moduleEnable(self.modules[n]) end
  self._modulesStarted = true
  self._loginAt = now()
  safecall(self._BuildModulesTab, self)
  if self._liteNotice then self:After(3, function() TreeAI:_LiteNotice() end) end
  if self._safeNotice then self:After(3, function() TreeAI:_SafeNotice() end) end
  self:Fire("READY")
  self:StartAutoRefresh()
  -- premier lancement : petit assistant en 3 étapes (hors combat)
  if self.db and not self.db.onboarded and self.ShowWizard then
    self:After(4, function() self:_MaybeWizard(0) end)
  end
  safecall(self.PruneDismissed, self)
  safecall(self._RestoreRecentAlerts, self)
  self:After(5, function() self:SendHello() end)
end

function TreeAI:PLAYER_LOGOUT()
  if self.db then safecall(self.UpdateSummary, self) end
  if self.db then safecall(self._SaveRecentAlerts, self) end
end

-- ANNOY : l'assistant du 1er lancement attend un moment calme (pas mort / fantôme / en vol / en donjon /
-- en combat), et ne s'ouvre pas si le joueur l'a déjà fini (ou passé) entre-temps. Abandon après ~5 min.
function TreeAI:_MaybeWizard(tries)
  if not self.db or self.db.onboarded or not self.ShowWizard then return end
  local st = self.PlayerState and self:PlayerState(true) or {}
  if (st.dead or st.ghost or st.taxi or st.inInstance or st.inCombat) and (tries or 0) < 10 then
    self:After(30, function() self:_MaybeWizard((tries or 0) + 1) end)
    return
  end
  self:RunOutOfCombat("wizard", function()
    if self.db and not self.db.onboarded then self:ShowWizard(1) end
  end)
end

function TreeAI:PLAYER_REGEN_DISABLED()
  -- (3.9.2) combat starts → leave the edit mode at once (overlays must never stay in combat)
  if self.IsEditMode and self:IsEditMode() and self.SetEditMode then safecall(self.SetEditMode, self, false) end
  self:Fire("COMBAT_START")
end

function TreeAI:PLAYER_REGEN_ENABLED()
  local order = oocOrder
  oocOrder = {}
  for _, key in ipairs(order) do
    local fn = oocQueue[key]
    oocQueue[key] = nil
    if fn then safecall(fn) end
  end
  self:Fire("COMBAT_END")
  self:After(2, function() if not self:InCombat() then self:_FlushPendingAlert() end end)
end

function TreeAI:CHAT_MSG_ADDON(_, prefix, msg, channel, sender)
  self:_OnAddonMessage(prefix, msg, channel, sender)
end

local summaryPending = false
function TreeAI:_QueueSummary()
  if summaryPending or not self._loggedIn then return end
  summaryPending = true
  self:After(2, function() summaryPending = false; self:UpdateSummary() end)
end

function TreeAI:GROUP_ROSTER_UPDATE()
  self:_QueueSummary()
  self:Fire("GROUP_CHANGED")
end

-- Diagnostic : la fonction exacte que le jeu a bloquée (« TreeAI a été bloqué… »), dite dans le chat et gardée
-- dans TreeAIDB.diag.blocked (20 dernières) pour la corriger vite.
local function onBlocked(kind, addon, func)
  if addon ~= ADDON_NAME and addon ~= "TreeAI" then return end
  func = tostring(func or "?")
  local db = TreeAI.db
  if type(db) == "table" then
    db.diag = type(db.diag) == "table" and db.diag or {}
    local list = type(db.diag.blocked) == "table" and db.diag.blocked or {}
    list[#list + 1] = { kind = kind, func = func, t = (time and time()) or 0,
                        combat = (InCombatLockdown and InCombatLockdown()) and true or false }
    while #list > 20 do table.remove(list, 1) end
    db.diag.blocked = list
  end
  local ctx = TreeAI._blockCtx or TreeAI._lastCtx or "?"
  TreeAI._blockCtx = nil
  if TreeAI:_ShopBlocked(func) then ctx = tostring(ctx) .. " (après un achat TreeAI : mode sûr)" end
  if type(db) == "table" and db.diag and db.diag.blocked then db.diag.blocked[#db.diag.blocked].ctx = ctx end
  -- TAINT : compteurs par (fonction, module) gardés au /reload (« Blocages », /tai bloque) — pas seulement les 20 derniers
  TreeAI._blockedSession = (TreeAI._blockedSession or 0) + 1
  if type(db) == "table" and type(db.diag) == "table" then
    local top = type(db.diag.blockedTop) == "table" and db.diag.blockedTop or {}
    db.diag.blockedTop = top
    local key = func .. "|" .. tostring(ctx)
    local e = top[key]
    if type(e) ~= "table" then
      local n = 0
      for _ in pairs(top) do n = n + 1 end
      if n >= 60 then   -- plafond : on oublie la plus ancienne
        local oldK, oldT
        for k, v in pairs(top) do
          local lt = type(v) == "table" and tonumber(v.last) or 0
          if not oldT or lt < oldT then oldK, oldT = k, lt end
        end
        if oldK then top[oldK] = nil end
      end
      e = { func = func, ctx = tostring(ctx), kind = kind, n = 0, combat = 0 }
      top[key] = e
    end
    e.n = (tonumber(e.n) or 0) + 1
    e.last = (time and time()) or 0
    if InCombatLockdown and InCombatLockdown() then e.combat = (tonumber(e.combat) or 0) + 1 end
    db.diag.blockedTotal = (tonumber(db.diag.blockedTotal) or 0) + 1
  end
  -- ANNOY : une ligne de chat par fonction et par session (tout reste noté dans diag.blocked)
  TreeAI._blockedSaid = TreeAI._blockedSaid or {}
  if TreeAI._blockedSaid[func] then return end
  TreeAI._blockedSaid[func] = true
  TreeAI:Print(("Action bloquée par le jeu : %s (%s) pendant %s. Envoie ce message à TreeAI pour la correction.")
    :format(func, kind, ctx))
end
--- Les fonctions les plus bloquées : { {func, ctx, kind, n, combat, last}, ... } triées par nombre (n premières).
function TreeAI:BlockedTop(n)
  local db = self.db
  local top = type(db) == "table" and type(db.diag) == "table" and type(db.diag.blockedTop) == "table" and db.diag.blockedTop or {}
  local out = {}
  for _, e in pairs(top) do if type(e) == "table" and tonumber(e.n) then out[#out + 1] = e end end
  table.sort(out, function(a, b)
    if a.n ~= b.n then return a.n > b.n end
    return (tonumber(a.last) or 0) > (tonumber(b.last) or 0)
  end)
  if n then for i = #out, n + 1, -1 do out[i] = nil end end
  local total = type(db) == "table" and type(db.diag) == "table" and tonumber(db.diag.blockedTotal) or 0
  return out, total, self._blockedSession or 0
end
function TreeAI:ResetBlocked()
  local db = self.db
  if type(db) == "table" and type(db.diag) == "table" then db.diag.blockedTop, db.diag.blockedTotal, db.diag.blocked = {}, 0, {} end
  self._blockedSession, self._blockedSaid = 0, {}
end
function TreeAI:ADDON_ACTION_FORBIDDEN(_, addon, func) onBlocked("interdite", addon, func) end
function TreeAI:ADDON_ACTION_BLOCKED(_, addon, func) onBlocked("en combat", addon, func) end

TreeAI:RegisterEvent("ADDON_ACTION_FORBIDDEN")
TreeAI:RegisterEvent("ADDON_ACTION_BLOCKED")
TreeAI:RegisterEvent("ADDON_LOADED")
TreeAI:RegisterEvent("PLAYER_LOGIN")
for _, ev in ipairs(TreeAI.STATE_EVENTS) do TreeAI:RegisterEvent(ev) end
TreeAI:RegisterEvent("PLAYER_LOGOUT")
TreeAI:RegisterEvent("PLAYER_REGEN_DISABLED")
TreeAI:RegisterEvent("PLAYER_REGEN_ENABLED")
TreeAI:RegisterEvent("CHAT_MSG_ADDON")
do  -- pause / reprise des comms selon l'instance (propriétaire distinct : n'écrase pas les autres handlers)
  local commOwner = {}
  for _, ev in ipairs({ "PLAYER_ENTERING_WORLD", "ZONE_CHANGED_NEW_AREA" }) do
    TreeAI:RegisterEvent(ev, commOwner, function() TreeAI:_CheckCommPause() end)
  end
end
do  -- (LITE) sacs changés -> API.BagMemo recalcule
  local bagOwner = {}
  for _, ev in ipairs({ "BAG_UPDATE_DELAYED", "BAG_UPDATE", "PLAYER_ENTERING_WORLD", "MERCHANT_CLOSED", "LOOT_CLOSED",
      "PLAYER_EQUIPMENT_CHANGED" }) do
    TreeAI:RegisterEvent(ev, bagOwner, function() API.BagChanged() end)
  end
  TreeAI:RegisterEvent("UNIT_INVENTORY_CHANGED", bagOwner, function(_, _, u) if u == nil or u == "player" then API.BagChanged() end end)
  -- (« player » seulement : le répartiteur saute les autres unités sans appel)
  local f = TreeAI.GetEventHandler and TreeAI:GetEventHandler("UNIT_INVENTORY_CHANGED", bagOwner)
  if f then TreeAI._unitOf.UNIT_INVENTORY_CHANGED = TreeAI._unitOf.UNIT_INVENTORY_CHANGED or setmetatable({}, { __mode = "k" }) TreeAI._unitOf.UNIT_INVENTORY_CHANGED[f] = { player = true } end
end
TreeAI:RegisterEvent("GROUP_ROSTER_UPDATE")
TreeAI:RegisterEvent("PARTY_MEMBERS_CHANGED", TreeAI, "GROUP_ROSTER_UPDATE")
do  -- (4.0.1) le joueur se met à marcher : les minuteurs « de marche » reprennent tout de suite (sans attendre le coup d'œil)
  local moveOwner = {}
  TreeAI:RegisterEvent("PLAYER_STARTED_MOVING", moveOwner, function() TreeAI:WakeMotion() end)
end
for _, ev in ipairs({ "PLAYER_LEVEL_UP", "ZONE_CHANGED_NEW_AREA", "PLAYER_MONEY", "PLAYER_GUILD_UPDATE" }) do
  TreeAI:RegisterEvent(ev, TreeAI, function(self) self:_QueueSummary() end)
end
do  -- son « carte de niveau » : après la fanfare de Blizzard (4 s), hors combat (sinon après)
  local lvlOwner = {}
  TreeAI:RegisterEvent("PLAYER_LEVEL_UP", lvlOwner, function()
    TreeAI:After(4, function()
      if TreeAI:InCombat() then TreeAI:RunOutOfCombat("sound:levelup", function() TreeAI:Sound("levelup") end)
      else TreeAI:Sound("levelup") end
    end)
  end)
end
TreeAI:RegisterSlash("son", function(rest)
  local k = tostring(rest or ""):lower():gsub("%s+", "")
  if TreeAI.SOUND_KINDS[k] then TreeAI:PreviewSound(k); return end
  if k == "options" or k == "" then
    local parts = {}
    for _, kind in ipairs(TreeAI.SOUND_ORDER) do
      parts[#parts + 1] = kind .. (TreeAI:SoundKindOn(kind) and "" or " (off)")
    end
    TreeAI:Print(TreeAI.L["Sons"] .. " : " .. tconcat(parts, ", ") .. "  —  /tai son <genre> : écouter")
    if k == "options" and TreeAI.OpenOptions then TreeAI:OpenOptions("sounds") end
  end
end, "sons : liste des genres, /tai son <genre> pour écouter, /tai son options")
