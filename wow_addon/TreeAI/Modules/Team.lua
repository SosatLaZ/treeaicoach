-- TreeAI — module Team : conseils sur TOUS les membres du groupe, même sans TreeAI.
-- Données lues uniquement par les API Blizzard (aucun message d'addon nécessaire) :
--   niveau / classe / race / rôle (UnitGroupRolesAssigned, sinon deviné par la classe), PV / mana %
--   (hors combat ; valeurs secrètes ignorées), mort / fantôme / AFK / hors ligne / portée / zone,
--   buffs de classe manquants (toi ou un autre membre peut les donner), affections à dissiper,
--   quêtes de TON journal qu'ils ont ou non (C_QuestLog.IsUnitOnQuest), quêtes rouges pour eux,
--   équipement par inspection (NotifyInspect → INSPECT_READY : hors combat, à portée, un membre à la fois,
--   ≤ 1 / 10 s, cache 10 min) et « prêt à tirer ? » quand tu cibles un monstre hors combat.
-- Sortie : bloc « Team » de l'onglet « Groupe » ; seules les lignes actionnables (prio ≥ 2) concourent pour
-- « Maintenant » (pin). Jamais de chat, rien d'automatique : partager / buffer / dissiper = consignes.
-- Fusion : si le membre a TreeAI (Squad le connaît), Squad / Missions donnent déjà son état, ses quêtes et
-- le « prêt à tirer » → Team ne garde pour lui que ce qu'ils ne font pas (buffs, dissipation, équipement).
-- Événementiel et économe : recalcul regroupé (≥ 2 s), PV / mana seulement si l'onglet est visible ou si
-- une cible hostile est sélectionnée, balayage des quêtes ≥ 15 s, rien en combat.
local T = _G.TreeAI
if type(T) ~= "table" or not T.RegisterModule then return end

local fmt, tinsert, tconcat, tsort = string.format, table.insert, table.concat, table.sort
local floor, mmin = math.floor, math.min

local M = T:RegisterModule("Team", {
  title = "Équipe (même sans TreeAI)",
  defaults = { enabled = true, status = true, buffs = true, cleanse = true, quests = true, levels = true,
               gear = true, pull = true },
  options = {
    { key = "status",  type = "toggle", label = "État de chaque membre (PV, mana, portée, zone)", section = "Équipe" },
    { key = "pull",    type = "toggle", label = "« Prêt à tirer ? » quand tu cibles un monstre" },
    { key = "buffs",   type = "toggle", label = "Buffs de classe manquants", section = "Soutien" },
    { key = "cleanse", type = "toggle", label = "Poisons / maladies / malédictions à dissiper" },
    { key = "quests",  type = "toggle", label = "Quêtes de ton journal qu'ils n'ont pas", section = "Quêtes" },
    { key = "levels",  type = "toggle", label = "Quêtes rouges pour un membre" },
    { key = "gear",    type = "toggle", label = "Équipement (inspection discrète, hors combat)", section = "Équipement" },
  },
})
T.Team = M
if type(T.MODULE_TAB) == "table" then T.MODULE_TAB.Team = T.MODULE_TAB.Team or "group" end

local INSPECT_GAP, INSPECT_CACHE, INSPECT_TIMEOUT = 10, 600, 5
local QUEST_GAP, DEBOUNCE, NAME_MAX = 15, 2, 12

-- ------------------------------------------------------------------ appels protégés
local function pass(ok, ...) if ok then return ... end return nil end
local function call(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function pub(v)
  local f = _G.issecretvalue
  if v ~= nil and type(f) == "function" then
    local ok, s = pcall(f, v)
    if not ok or s then return nil end
  end
  return v
end
local function now() return call(_G.GetTime) or 0 end
local function inCombat() return call(_G.InCombatLockdown) and true or false end
local function cut(s, n)
  s = tostring(s or "")
  if #s <= n then return s end
  local i = n
  while i > 1 and s:byte(i + 1) and s:byte(i + 1) >= 128 and s:byte(i + 1) < 192 do i = i - 1 end
  return s:sub(1, i) .. "…"
end
local function pct(a, b)
  a, b = tonumber(pub(a)), tonumber(pub(b))
  if not a or not b or b <= 0 then return nil end
  return floor(a / b * 100 + 0.5)
end

-- ------------------------------------------------------------------ données de classe
local CLASS_FR = { WARRIOR = "Guerrier", PALADIN = "Paladin", HUNTER = "Chasseur", ROGUE = "Voleur", PRIEST = "Prêtre",
  SHAMAN = "Chaman", MAGE = "Mage", WARLOCK = "Démoniste", DRUID = "Druide" }
local ROLE_FR = { TANK = "tank", HEALER = "soigneur", DAMAGER = "dégâts" }
local ROLE_GUESS = { WARRIOR = "TANK", PRIEST = "HEALER" }   -- le reste : dégâts (hybrides : rôle assigné d'abord)
local CLASS_COLOR = { WARRIOR = "c79c6e", PALADIN = "f58cba", HUNTER = "abd473", ROGUE = "fff569", PRIEST = "ffffff",
  SHAMAN = "0070de", MAGE = "69ccf0", WARLOCK = "9482c9", DRUID = "ff7d0a" }

-- buffs de classe (tous rangs ; nom localisé via le 1er ID, repli FR)
M.BUFFS = {
  { key = "fort", class = "PRIEST", name = "Robustesse", spells = { 1243, 1244, 1245, 2791, 10937, 10938, 21562, 21564 } },
  { key = "int", class = "MAGE", name = "Intelligence des Arcanes", mana = true,
    spells = { 1459, 1460, 1461, 10156, 10157, 23028 } },
  { key = "motw", class = "DRUID", name = "Marque du fauve", spells = { 1126, 5232, 6756, 5234, 8907, 9884, 9885, 21849, 21850 } },
  { key = "bless", class = "PALADIN", name = "Bénédiction", spells = { 19740, 19834, 19835, 19836, 19837, 19838, 25291,
    19742, 19850, 19852, 19853, 19854, 25290, 20217, 1038, 20911, 19977, 25782, 25894, 25898, 25895, 25899, 25890 } },
}
-- dissipation : type → { sorts qui le retirent (classe, IDs) }
local DISPEL_FR = { Magic = "Magie", Curse = "Malédiction", Poison = "Poison", Disease = "Maladie" }
M.DISPELS = {
  Magic = { { "PRIEST", { 527, 988 }, "Dissipation de la magie" }, { "PALADIN", { 4987 }, "Épuration" } },
  Curse = { { "MAGE", { 475 }, "Délivrance de la malédiction" }, { "DRUID", { 2782 }, "Délivrance de la malédiction" } },
  Poison = { { "DRUID", { 8946, 2893 }, "Guérison du poison" }, { "PALADIN", { 1152, 4987 }, "Purification" },
             { "SHAMAN", { 526 }, "Guérison du poison" } },
  Disease = { { "PRIEST", { 528, 552 }, "Guérison des maladies" }, { "PALADIN", { 1152, 4987 }, "Purification" },
              { "SHAMAN", { 2870 }, "Guérison des maladies" } },
}
local SLOT_FR = { [1] = "tête", [3] = "épaules", [5] = "torse", [6] = "taille", [7] = "jambes", [8] = "pieds",
  [9] = "poignets", [10] = "mains", [15] = "dos", [16] = "arme" }
local GEAR_SLOTS = { 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18 }

local function spellName(id)
  local A = T.API
  local n = A and A.SpellName and call(A.SpellName, id)
  return pub(n)
end
local function knows(ids)
  local A = T.API
  for _, id in ipairs(ids) do
    if A and A.IsSpellKnown and call(A.IsSpellKnown, id) then return id end
  end
  return nil
end

-- ------------------------------------------------------------------ roster
local roster = {}      -- liste de membres (sans moi)
local auraCache = {}   -- [unit] = { ids = {}, names = {}, debuffs = { {type, name} } } | false (à relire)
local gearCache = {}   -- [guid] = { t, ilvl, missing = {}, weapon = {quality, sub, name}, n }
local questInfo = { t = -1e9, dirty = true, mine = {}, has = {} }
local pending, lastInspect = nil, -1e9
local lastSig, pullOn, dirtyAt = nil, false, nil
local statusDirty = false

local function myName() return pub((call(_G.UnitName, "player"))) or "" end
local function playerClass() return select(2, call(_G.UnitClass, "player")) end

local function addonUser(name)
  local SQ = T.Squad
  if type(SQ) ~= "table" or not SQ.State or not SQ.IsActive then return false end
  if not call(SQ.IsActive, SQ) then return false end
  return call(SQ.State, SQ, name) ~= nil
end

function M:ScanRoster()
  local old = {}
  for _, m in ipairs(roster) do old[m.unit] = m end
  roster = {}
  if call(_G.IsInRaid) then return roster end   -- petits groupes seulement (le raid a ses propres outils)
  for i = 1, 4 do
    local u = "party" .. i
    if call(_G.UnitExists, u) then
      local name = pub((call(_G.UnitName, u)))
      if name and name ~= "" then
        local _, cls = call(_G.UnitClass, u)
        cls = pub(cls)
        local m = { unit = u, name = name, class = cls, guid = pub(call(_G.UnitGUID, u)) }
        local role = pub(call(_G.UnitGroupRolesAssigned, u))
        m.role = (role and ROLE_FR[role]) and role or ROLE_GUESS[cls or ""] or "DAMAGER"
        m.roleGuessed = not (role and ROLE_FR[role])
        local p = old[u]
        if p and p.guid ~= m.guid then auraCache[u] = false end
        tinsert(roster, m)
      end
    end
  end
  questInfo.dirty = true
  return roster
end

-- état instantané d'un membre (hors combat ; valeurs secrètes → nil)
local function readStatus(m, myMap)
  local u = m.unit
  m.level = tonumber(pub(call(_G.UnitLevel, u))) or m.level
  m.online = call(_G.UnitIsConnected, u) ~= false
  m.ghost = pub(call(_G.UnitIsGhost, u)) and true or false
  m.dead = (pub(call(_G.UnitIsDeadOrGhost, u)) and true or false)
  m.afk = pub(call(_G.UnitIsAFK, u)) and true or false
  m.hp = pct(call(_G.UnitHealth, u), call(_G.UnitHealthMax, u))
  local pt = pub(call(_G.UnitPowerType, u))
  m.manaUser = pt == 0
  m.mana = m.manaUser and pct(call(_G.UnitPower, u, 0), call(_G.UnitPowerMax, u, 0)) or nil
  local inR, checked = call(_G.UnitInRange, u)
  inR, checked = pub(inR), pub(checked)
  if checked then m.far = not inR
  elseif _G.CheckInteractDistance then m.far = not call(_G.CheckInteractDistance, u, 4)
  else m.far = nil end
  m.visible = call(_G.UnitIsVisible, u)
  m.zone, m.otherZone = nil, false
  local CM = _G.C_Map
  local map = CM and pub(call(CM.GetBestMapForUnit, u))
  if map and myMap and map ~= myMap then
    m.otherZone = true
    local info = call(CM.GetMapInfo, map)
    m.zone = type(info) == "table" and pub(info.name) or nil
  elseif not map and m.online and m.visible == false and m.far then
    m.otherZone = true
  end
end

-- auras d'un membre (relues seulement après UNIT_AURA)
local function readAuras(u)
  local c = auraCache[u]
  if c then return c end
  c = { ids = {}, names = {}, debuffs = {} }
  local CU = _G.C_UnitAuras
  for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
    for i = 1, 40 do
      local name, sid, dtype
      if CU and CU.GetAuraDataByIndex then
        local d = call(CU.GetAuraDataByIndex, u, i, filter)
        if type(d) ~= "table" then break end
        name, sid, dtype = pub(d.name), pub(d.spellId), pub(d.dispelName)
      elseif _G.UnitAura then
        local n, _, _, dt, _, _, _, _, _, id = call(_G.UnitAura, u, i, filter)
        if not n then break end
        name, sid, dtype = pub(n), pub(id), pub(dt)
      else break end
      if filter == "HELPFUL" then
        if sid then c.ids[sid] = true end
        if name then c.names[name] = true end
      elseif dtype and DISPEL_FR[dtype] then
        tinsert(c.debuffs, { type = dtype, name = name })
      end
    end
  end
  auraCache[u] = c
  return c
end

local function hasBuff(c, buff)
  for _, id in ipairs(buff.spells) do
    if c.ids[id] then return true end
  end
  if not buff._names then
    buff._names = {}
    for i = 1, mmin(#buff.spells, 3) do local n = spellName(buff.spells[i]); if n then buff._names[n] = true end end
  end
  for n in pairs(buff._names) do if c.names[n] then return true end end
  return false
end

-- ------------------------------------------------------------------ quêtes (journal du joueur × membres)
local function myQuestList()
  local out, CQ = {}, _G.C_QuestLog
  if CQ and CQ.GetNumQuestLogEntries and CQ.GetInfo then
    local n = call(CQ.GetNumQuestLogEntries) or 0
    for i = 1, mmin(n, 60) do
      local q = call(CQ.GetInfo, i)
      if type(q) == "table" and not q.isHeader and not q.isHidden and q.questID and q.title then
        tinsert(out, { id = q.questID, idx = i, title = q.title, level = tonumber(q.level) or 0,
          complete = CQ.IsComplete and call(CQ.IsComplete, q.questID) and true or false })
      end
    end
  elseif _G.GetNumQuestLogEntries and _G.GetQuestLogTitle then
    local n = call(_G.GetNumQuestLogEntries) or 0
    for i = 1, mmin(n, 60) do
      local title, level, _, isHeader, _, isComplete, _, qid = call(_G.GetQuestLogTitle, i)
      if title and not isHeader then
        tinsert(out, { id = qid, idx = i, title = title, level = tonumber(level) or 0, complete = isComplete == 1 or isComplete == true })
      end
    end
  end
  return out
end

-- vrai / faux / nil (inconnu : l'API n'existe pas sur ce client)
local function unitOnQuest(u, q)
  local CQ = _G.C_QuestLog
  local f = CQ and CQ.IsUnitOnQuest
  if f and q.id then
    local ok, r = pcall(f, u, q.id)          -- (unit, questID) ; l'ordre inverse seulement si refusé
    if not ok then ok, r = pcall(f, q.id, u) end
    if ok then
      if r == nil then return nil end
      return pub(r) and true or false
    end
  end
  if _G.IsUnitOnQuest and q.idx then
    local ok, r = pcall(_G.IsUnitOnQuest, q.idx, u)   -- Classic : 1 ou nil
    if ok then return (pub(r) == 1 or pub(r) == true) end
  end
  return nil
end

function M:ScanQuests(force)
  local t = now()
  if not force and (not questInfo.dirty or t - questInfo.t < QUEST_GAP) then return questInfo end
  questInfo.t, questInfo.dirty = t, false
  questInfo.mine = myQuestList()
  questInfo.has = {}
  for _, m in ipairs(roster) do
    local h = {}
    for _, q in ipairs(questInfo.mine) do h[q.id or q.idx] = unitOnQuest(m.unit, q) end
    questInfo.has[m.name] = h
  end
  return questInfo
end

local function pushable(q)
  local CQ = _G.C_QuestLog
  if CQ and CQ.IsPushableQuest and q.id then
    local r = call(CQ.IsPushableQuest, q.id)
    if r == false then return false end
  end
  return true
end

-- ------------------------------------------------------------------ inspection (équipement)
local function inspectFrameOpen()
  local f = _G.InspectFrame
  return type(f) == "table" and f.IsShown and call(f.IsShown, f) and true or false
end

function M:TryInspect()
  if not self.db.gear or inCombat() or inspectFrameOpen() then return end
  local t = now()
  if pending and t - pending.t > INSPECT_TIMEOUT then   -- pas de réponse : réessayer ce membre plus tard
    gearCache[pending.guid] = gearCache[pending.guid] or { t = t, missing = {}, n = 0, partial = true }
    pending = nil
  end
  if pending or t - lastInspect < INSPECT_GAP or not _G.NotifyInspect then return end
  for _, m in ipairs(roster) do
    local c = m.guid and gearCache[m.guid]
    if m.guid and (not c or t - c.t > INSPECT_CACHE or (c.partial and t - c.t > 60)) and m.online ~= false
        and call(_G.UnitIsVisible, m.unit) ~= false
        and (not _G.CanInspect or call(_G.CanInspect, m.unit))
        and (not _G.CheckInteractDistance or call(_G.CheckInteractDistance, m.unit, 1)) then
      lastInspect = t
      pending = { guid = m.guid, unit = m.unit, t = t }
      call(_G.NotifyInspect, m.unit)
      return
    end
  end
end

local function itemInfo(link)
  local A = T.API
  if A and A.ItemInfo then return call(A.ItemInfo, link) end
  return call(_G.GetItemInfo, link)
end

function M:ReadGear(unit, guid)
  local c = { t = now(), missing = {}, n = 0 }
  local sum, cnt = 0, 0
  for _, slot in ipairs(GEAR_SLOTS) do
    local link = pub(call(_G.GetInventoryItemLink, unit, slot))
    if link then
      c.n = c.n + 1
      c.links = c.links or {}
      c.links[slot] = link            -- DUNGEON2 : butin du donjon comparé à l'équipement inspecté
      local name, _, quality, level, _, _, sub = itemInfo(link)
      local det = _G.C_Item and _G.C_Item.GetDetailedItemLevelInfo or _G.GetDetailedItemLevelInfo
      local il = tonumber((call(det, link))) or tonumber(level)
      if il and il > 0 then sum, cnt = sum + il, cnt + 1 end
      if not name then c.partial = true end
      if slot == 16 then c.weapon = { quality = quality, sub = sub, name = name } end
    elseif SLOT_FR[slot] then
      tinsert(c.missing, slot)
    end
  end
  c.ilvl = cnt > 0 and floor(sum / cnt + 0.5) or nil
  if c.n == 0 then c.partial = true end   -- rien reçu : réessayer plus tard
  gearCache[guid] = c
  return c
end

function M:INSPECT_READY(_, guid)
  guid = pub(guid)
  if not pending or not guid or guid ~= pending.guid then return end
  local unit = pending.unit
  pending = nil
  if pub(call(_G.UnitGUID, unit)) ~= guid then return end
  self:ReadGear(unit, guid)
  if _G.ClearInspectPlayer and not inspectFrameOpen() then call(_G.ClearInspectPlayer) end
  self:Dirty()
end

-- ------------------------------------------------------------------ calcul des conseils
local function nm(m) return cut(m.name, NAME_MAX) end
local function joinNames(list, n)
  local out = {}
  for i = 1, mmin(#list, n or 3) do out[i] = list[i] end
  local s = tconcat(out, ", ")
  if #list > (n or 3) then s = s .. fmt(" +%d", #list - (n or 3)) end
  return s
end

function M:Build()
  local lines, db = {}, self.db
  local st = T.PlayerState and T:PlayerState() or {}
  local fight = inCombat()
  local myMap = _G.C_Map and pub(call(_G.C_Map.GetBestMapForUnit, "player"))
  local myClass = playerClass()
  local myLevel = tonumber(call(_G.UnitLevel, "player")) or 1
  local others = 0
  for _, m in ipairs(roster) do
    if not fight then readStatus(m, myMap) end
    m.addon = addonUser(m.name)
    if not m.addon then others = others + 1 end
  end
  local function add(text, prio, extra)
    local l = extra or {}
    l.text, l.prio = text, prio or 1
    if (prio or 1) >= 2 and l.pin == nil then l.pin = true end
    tinsert(lines, l)
  end

  -- 1. prêt à tirer ? (membres sans TreeAI ; Squad couvre les autres)
  pullOn = false
  if db.pull and not fight and not st.dead and not st.ghost then
    local tgt = call(_G.UnitExists, "target") and call(_G.UnitCanAttack, "player", "target")
      and not call(_G.UnitIsDead, "target")
    if tgt then
      pullOn = true
      local why = {}
      for _, m in ipairs(roster) do
        if not m.addon then
          if not m.online then tinsert(why, nm(m) .. " hors ligne")
          elseif m.dead then tinsert(why, nm(m) .. (m.ghost and " fantôme" or " mort"))
          elseif m.afk then tinsert(why, nm(m) .. " AFK")
          elseif m.far then tinsert(why, nm(m) .. " loin")
          elseif m.manaUser and m.mana and m.mana < 35 and (m.role == "HEALER" or m.class == "MAGE"
              or m.class == "WARLOCK" or m.class == "PRIEST") then tinsert(why, fmt("%s %d %% mana", nm(m), m.mana))
          elseif m.hp and m.hp < 50 then tinsert(why, fmt("%s %d %% PV", nm(m), m.hp)) end
        end
      end
      if #why > 0 then
        add("Avant de tirer, attends : " .. joinNames(why, 3), 2,
          { tooltip = "Lu sur les cadres du groupe (aucun addon chez eux)." })
      end
    end
  end

  -- 2. état de chaque membre sans TreeAI
  if db.status and others > 0 then
    for _, m in ipairs(roster) do
      if not m.addon then
        local parts = { fmt("|cff%s%s|r", CLASS_COLOR[m.class or ""] or "cccccc", nm(m)),
          fmt("%s %d", CLASS_FR[m.class or ""] or "Niveau", m.level or 1), ROLE_FR[m.role] or "dégâts" }
        if not m.online then tinsert(parts, "hors ligne")
        elseif m.dead then tinsert(parts, m.ghost and "fantôme" or "mort")
        else
          if not fight and m.hp then tinsert(parts, fmt("PV %d %%", m.hp)) end
          if not fight and m.mana then tinsert(parts, fmt("mana %d %%", m.mana)) end
          if m.afk then tinsert(parts, "AFK") end
          if m.otherZone then tinsert(parts, m.zone and cut(m.zone, 18) or "autre zone")
          elseif m.far then tinsert(parts, "loin") end
        end
        add(tconcat(parts, " · "), 1, { tooltip = fmt("%s — sans TreeAI : infos lues par le jeu%s.", m.name,
          m.roleGuessed and " (rôle deviné d'après la classe)" or "") })
      end
    end
  end

  if not fight then
    local busy = st.dead or st.ghost or st.taxi or st.mounted
    -- 3. buffs de classe
    if db.buffs and not busy and not st.resting then
      for _, b in ipairs(M.BUFFS) do
        local mine = myClass == b.class and knows(b.spells)
        local giver
        if not mine then
          for _, m in ipairs(roster) do
            if m.class == b.class and m.online and not m.dead then giver = m break end
          end
        end
        if mine or giver then
          local lack = {}
          for _, m in ipairs(roster) do
            if m ~= giver and m.online and not m.dead and not m.far and (not b.mana or m.manaUser) then
              local c = readAuras(m.unit)
              if not hasBuff(c, b) then tinsert(lack, nm(m)) end
            end
          end
          local sname = spellName(mine or b.spells[1]) or b.name
          if #lack > 0 and mine then
            add(fmt("Lance %s sur %s", sname, joinNames(lack, 3)), 2,
              { tooltip = "Clique son portrait (cadre du groupe) puis lance le sort." })
          elseif #lack > 0 then
            add(fmt("%s %s pas %s : %s (%s) peut la donner", joinNames(lack, 2), #lack > 1 and "n'ont" or "n'a", sname, nm(giver),
              (CLASS_FR[giver.class] or ""):lower()), 1)
          end
        end
      end
    end
    -- 4. dissipation
    if db.cleanse and not st.dead and not st.ghost then
      for _, m in ipairs(roster) do
        if m.online and not m.dead then
          local c = readAuras(m.unit)
          local done = {}
          for _, d in ipairs(c.debuffs) do
            if not done[d.type] then
              done[d.type] = true
              local can, other
              for _, opt in ipairs(M.DISPELS[d.type] or {}) do
                if opt[1] == myClass and knows(opt[2]) then can = spellName(knows(opt[2])) or opt[3] break end
              end
              if not can then
                for _, o in ipairs(roster) do
                  for _, opt in ipairs(M.DISPELS[d.type] or {}) do
                    if o ~= m and o.class == opt[1] and o.online and not o.dead then other = o break end
                  end
                  if other then break end
                end
              end
              local what = DISPEL_FR[d.type]
              if can then
                add(fmt("Dissipe %s : %s (%s)", nm(m), what, can), 2,
                  { tooltip = d.name and fmt("%s : %s", what, d.name) or nil })
              elseif other then
                add(fmt("%s : %s à retirer, %s (%s) peut le faire", nm(m), what:lower(), nm(other),
                  (CLASS_FR[other.class] or ""):lower()), 1)
              end
            end
          end
        end
      end
    end
    -- 5. quêtes : qui n'a pas quoi + quêtes rouges
    if (db.quests or db.levels) and #roster > 0 then
      local qi = self:ScanQuests()
      for _, m in ipairs(roster) do
        local h = qi.has[m.name]
        if h and not m.addon and m.online then
          local lack, red, known = {}, {}, false
          for _, q in ipairs(qi.mine) do
            local v = h[q.id or q.idx]
            if v ~= nil then known = true end
            local diff = (q.level or 0) - (m.level or myLevel)
            if q.level and q.level > 0 and diff >= 5 then tinsert(red, q)
            elseif v == false and not q.complete and pushable(q) and diff <= 2 then tinsert(lack, q) end
          end
          if db.quests and known and #lack > 0 then
            local tips = {}
            for i = 1, mmin(#lack, 8) do tips[i] = "« " .. lack[i].title .. " »" end
            local text = #lack == 1 and fmt("%s n'a pas « %s » : partage-la (Journal > Partager)", nm(m), cut(lack[1].title, 32))
              or fmt("%s n'a pas « %s » +%d : partage-les (Journal > Partager)", nm(m), cut(lack[1].title, 26), #lack - 1)
            add(text, 2, { tooltip = tips })
          end
          if db.levels and #red > 0 then
            add(fmt("Quêtes rouges pour %s (niv %d) : « %s »%s", nm(m), m.level or 1, cut(red[1].title, 26),
              #red > 1 and fmt(" +%d", #red - 1) or ""), 1, { tooltip = "Trop dures pour lui : gardez-les pour plus tard." })
          end
        end
      end
    end
  end

  -- 6. équipement (inspection)
  if self.db.gear then
    for _, m in ipairs(roster) do
      local c = m.guid and gearCache[m.guid]
      if c and not c.partial then
        local w = c.weapon
        if w and w.quality == 0 then
          add(fmt("%s a encore une arme grise%s : conseille-lui d'en changer", nm(m), w.sub and (" (" .. cut(w.sub, 22) .. ")") or ""), 1)
        elseif w and w.quality == 1 and (m.level or 0) >= 15 then
          add(fmt("%s a une arme blanche : une verte (HV, quête) l'aidera", nm(m)), 1)
        end
        local miss = {}
        for _, s in ipairs(c.missing) do tinsert(miss, SLOT_FR[s]) end
        if #miss > 0 then add(fmt("%s n'a rien en : %s", nm(m), joinNames(miss, 4)), 1) end
        if c.ilvl and m.level and m.level >= 10 and c.ilvl < m.level - 8 then
          add(fmt("%s : équipement niv. %d pour niv %d, à renouveler", nm(m), c.ilvl, m.level), 1)
        end
      end
    end
  end
  return lines
end

function M:Push()
  dirtyAt = nil
  if not self:IsEnabled() then return end
  if #roster == 0 then self:ScanRoster() end
  local lines = (#roster > 0) and self:Build() or {}
  local parts = {}
  for i, l in ipairs(lines) do parts[i] = (l.prio or 1) .. l.text end
  local sig = tconcat(parts, "\n")
  if sig == lastSig then return end
  lastSig = sig
  if T.SetTabLines then
    T:SetTabLines("group", lines, "Team")
  end
end

function M:Dirty(delay)
  local t = now()
  local due = t + (delay or DEBOUNCE)
  if dirtyAt and dirtyAt <= due then return end
  dirtyAt = due
  if T.After then T:After(delay or DEBOUNCE, function() if dirtyAt and now() >= dirtyAt - 0.05 then M:Push() end end) end
end

local function groupTabVisible()
  local p = _G.TreeAIPanel
  if not (type(p) == "table" and p.IsShown and call(p.IsShown, p)) then return false end
  return T.GetActiveTab and T:GetActiveTab() == "group" and not (T.IsMini and T:IsMini()) or false
end

-- ------------------------------------------------------------------ événements
local function partyUnit(u) return type(u) == "string" and u:find("^party%d$") ~= nil end

function M:OnEnable()
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() M:ScanRoster(); M:Dirty() end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() M:ScanRoster(); M:Dirty() end)
  self:RegisterEvent("UNIT_AURA", function(_, _, u) if partyUnit(u) then auraCache[u] = false; if not inCombat() then M:Dirty() end end end)
  local function status(_, _, u)
    if not partyUnit(u) or inCombat() then return end
    statusDirty = true
    if pullOn then M:Dirty(3) end
  end
  for _, ev in ipairs({ "UNIT_HEALTH", "UNIT_POWER_UPDATE" }) do self:RegisterEvent(ev, status) end
  for _, ev in ipairs({ "UNIT_CONNECTION", "UNIT_LEVEL", "PLAYER_FLAGS_CHANGED", "UNIT_FLAGS" }) do
    self:RegisterEvent(ev, function(_, _, u) if partyUnit(u) then M:Dirty() end end)
  end
  self:RegisterEvent("QUEST_LOG_UPDATE", function() questInfo.dirty = true end)
  self:RegisterEvent("UNIT_QUEST_LOG_CHANGED", function(_, _, u) if partyUnit(u) or u == "player" then questInfo.dirty = true; M:Dirty(QUEST_GAP) end end)
  self:RegisterEvent("PLAYER_TARGET_CHANGED", function() M:Dirty(0.5) end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() M:Dirty() end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() if pullOn then M:Push() end end)
  self:RegisterEvent("INSPECT_READY")
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", function(_, _, u)
    if partyUnit(u) then local g = pub(call(_G.UnitGUID, u)); if g and gearCache[g] then gearCache[g].t = -1e9 end end
  end)
  if T.On then T:On("SUMMARY", self, function() M:SaveSummary() end) end
  self:ScanRoster()
  self:Dirty()
end

function M:OnDisable()
  pending, lastSig, dirtyAt = nil, nil, nil
  if T.SetTabLines then T:SetTabLines("group", {}, "Team") end
end

function M:OnOptionChanged() lastSig = nil; self:Dirty(0.2) end

-- toutes les ~3 s (Core) : quasi gratuit hors groupe ; portée / PV seulement si l'onglet est visible
local tick = 0
function M:AutoRefresh()
  if #roster == 0 or inCombat() then return end
  tick = tick + 1
  self:TryInspect()
  if tick % 3 == 0 and (statusDirty or groupTabVisible()) then
    statusDirty = false
    self:Dirty(0.1)
  elseif questInfo.dirty and now() - questInfo.t >= QUEST_GAP and tick % 5 == 0 then
    self:Dirty(0.1)
  end
end

-- instantané (sans noms) pour TreeAI Coach
--- Équipement inspecté d'un membre (cache, lecture seule) -> { t, ilvl, links = {[slot] = lien}, ... } | nil
function M.GearOf(guid) return guid and gearCache[guid] or nil end

function M:SaveSummary()
  local S = T.Summary and T:Summary()
  if type(S) ~= "table" then return end
  local out = { v = 1, members = {} }
  for _, m in ipairs(roster) do
    local c = m.guid and gearCache[m.guid]
    tinsert(out.members, { class = m.class, level = m.level, role = ROLE_FR[m.role], addon = m.addon or nil,
      dead = m.dead or nil, far = m.far or nil, otherZone = m.otherZone or nil, ilvl = c and c.ilvl or nil,
      missingSlots = c and #c.missing or nil, greyWeapon = (c and c.weapon and c.weapon.quality == 0) or nil })
  end
  S.team = out
end

-- tests
M._test = { roster = function() return roster end, gear = gearCache, quests = questInfo,
  reset = function() lastSig, dirtyAt, pending, lastInspect = nil, nil, nil, -1e9; questInfo.dirty, questInfo.t = true, -1e9
    for k in pairs(auraCache) do auraCache[k] = false end end }
