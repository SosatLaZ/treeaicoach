-- TreeAI — module « Équipement du groupe » (GroupGear.lua, GEARTRADE 4.1) : voir ce que portent tes amis pour faire
-- de BONS échanges. Informatif seulement : rien n'est échangé, posé, jeté, ni tiré à ta place.
--  * Amis TreeAI : leur équipement (n° d'objet + suffixe « du Singe »…, sans enchantement) et leurs objets
--    échangeables arrivent par le canal du groupe (types GGE / GGB / GGQ), seulement quand ça change, ≤ 1 envoi / 10 s.
--  * Les autres : inspection du jeu (NotifyInspect) hors combat, jamais pendant un boss, un membre à la fois, ≥ 2 s
--    entre deux demandes, seulement à portée (CanInspect + CheckInteractDistance), réessai quand il revient à portée ;
--    lu sur INSPECT_READY (emplacements 1 à 18) puis ClearInspectPlayer. Mémoire par membre avec son âge (« vu il y a
--    3 min ») ; ce qui n'est pas connu est dit (« pas encore vu : trop loin »).
--  * Échanges conseillés : chaque objet échangeable de TES sacs (non lié, ou ramassé en groupe et encore échangeable :
--    ligne « Vous pouvez échanger cet objet… » de l'infobulle) et de ceux annoncés par tes amis TreeAI → qui y gagne le
--    plus (classe, armure / arme maniée, niveau requis, poids de stats de Gear, comparé à ce qu'il porte à cet
--    emplacement) : « ≈ +12 % pour Bravo (main droite) ». Poids ESTIMÉS (Gear) : « ≈ » toujours affiché.
--  * Page « Groupe » de la fenêtre : section « Équipement du groupe » (une carte par membre : classe, niveau d'objet
--    moyen, emplacements faibles) puis « Échanges conseillés » (clic = ouvrir la fenêtre d'échange du jeu, hors combat,
--    à portée ; tu poses l'objet toi-même). API : T.GroupGear.Section(Ly) (mise en page de la fenêtre).
--  * Infobulle d'objet : la ligne « Groupe » d'Infos objets dit « Mieux pour : Bravo (≈ +12 %, main droite) » quand
--    l'équipement des amis est connu (sinon la ligne d'origine) ; jets de butin : la ligne « Jet … » du module Groupe
--    nomme qui y gagne le plus. Jamais de jet, jamais le chat, jamais de son.
-- Coût : rien seul (le minuteur de 2 s ne tourne qu'en groupe), lectures hors combat seulement, valeurs secrètes
-- (Forever) écartées. Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local API = T.API or {}
local G = _G

-- mode sûr : lectures + inspection discrète hors combat, l'échange seulement sur un clic → allumé par défaut
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.GroupGear = true end
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.GroupGear then
  T.MODULE_INFO.GroupGear = { lite = true, gate = "group",
    desc = "Équipement du groupe : ce que portent tes amis, échanges conseillés, à qui sert un butin" }
end
do   -- page d'options rangée dans « Groupe », juste après « Groupe »
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[3]
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has, at = false, #fam.mods + 1
    for i, n in ipairs(fam.mods) do if n == "GroupGear" then has = true end if n == "Group" then at = i + 1 end end
    if not has then table.insert(fam.mods, math.min(at, #fam.mods + 1), "GroupGear") end
  end
end

local GG = T:RegisterModule("GroupGear", {
  title = "Équipement du groupe",
  defaults = { enabled = true, share = true, inspect = true, tooltip = true, rolls = true },
  options = {
    { key = "share", type = "toggle", label = "Partager mon équipement et mes objets échangeables avec mes amis TreeAI",
      section = "Équipement du groupe" },
    { key = "inspect", type = "toggle", label = "Inspecter discrètement les autres (hors combat, à portée, un à la fois)" },
    { key = "tooltip", type = "toggle", label = "Infobulles d'objets : « Mieux pour : … » (qui y gagne le plus)" },
    { key = "rolls", type = "toggle", label = "Jets de butin : pour qui c'est le mieux (jamais de jet à ta place)" },
  },
})
if not GG then return end
T.GroupGear = GG

local floor, max, min = math.floor, math.max, math.min
local fmt, tconcat, tsort = string.format, table.concat, table.sort

GG.TICK = 2
GG.INSPECT_GAP = 2          -- ≥ 2 s entre deux NotifyInspect
GG.INSPECT_TIMEOUT = 4      -- pas de réponse : ce membre plus tard
GG.INSPECT_RETRY = 30       -- après un échec (réponse vide / délai), pas avant 30 s
GG.STALE = 600              -- équipement inspecté revu après 10 min
GG.CHANGED_MIN = 60         -- « il a changé d'objet » : nouvelle inspection, pas plus d'une fois par minute
GG.SEND_GAP = 10            -- ≤ 1 envoi d'un même type / 10 s, seulement si ça a changé
GG.REQ_GAP = 30             -- « envoie-moi ton équipement » : ≤ 1 / 30 s
GG.MIN_GAIN = 3             -- en dessous : équivalent (même seuil que Gear)
GG.MAX_BAG = 12             -- objets échangeables annoncés au plus
GG.MAX_TRADES = 8

-- ===================================================================================== utilitaires (appels gardés)
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "GroupGear") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function pub(v) if v == nil or secret(v) then return nil end return v end
local function num(v) return tonumber(pub(v)) end
local function str(v) v = pub(v) if type(v) == "string" and v ~= "" then return v end return nil end
local function now() return num(try(g("GetTime"))) or 0 end
local function inCombat()
  if T.InCombat and T:InCombat() then return true end
  return pub(try(g("InCombatLockdown"))) and true or false
end
local function mod(n) local m = T.modules and T.modules[n] if type(m) == "table" and m._active then return m end return nil end
local function shortName(full) full = str(full) return full and (full:match("^([^%-]+)") or full) or nil end
local function eng() local Gm = T.Gear return type(Gm) == "table" and type(Gm.Engine) == "table" and Gm.Engine or nil end
local function q(s) return "« " .. tostring(s or "?") .. " »" end
local function andList(t, n)
  n = n or 3
  if #t == 0 then return "" end
  local out = {}
  for i = 1, min(#t, n) do out[i] = t[i] end
  local s = #out == 1 and out[1] or (tconcat(out, ", ", 1, #out - 1) .. " et " .. out[#out])
  if #t > n then s = s .. fmt(" (+%d)", #t - n) end
  return s
end

local CLASS_FR = { WARRIOR = "Guerrier", PALADIN = "Paladin", HUNTER = "Chasseur", ROGUE = "Voleur", PRIEST = "Prêtre",
  SHAMAN = "Chaman", MAGE = "Mage", WARLOCK = "Démoniste", DRUID = "Druide" }
local CLASS_ICON = { WARRIOR = "Interface\\Icons\\INV_Sword_27", PALADIN = "Interface\\Icons\\INV_Hammer_01",
  HUNTER = "Interface\\Icons\\INV_Weapon_Bow_07", ROGUE = "Interface\\Icons\\INV_ThrowingKnife_04",
  PRIEST = "Interface\\Icons\\INV_Staff_30", SHAMAN = "Interface\\Icons\\Spell_Nature_BloodLust",
  MAGE = "Interface\\Icons\\INV_Staff_13", WARLOCK = "Interface\\Icons\\Spell_Nature_FaerieFire",
  DRUID = "Interface\\Icons\\Ability_Druid_Maul" }
GG.CLASS_FR = CLASS_FR
-- emplacements lus (fiche de personnage, sans chemise ni tabard)
local SLOTS = { 1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18 }
local SLOT_FR = { [1] = "tête", [2] = "cou", [3] = "épaules", [15] = "dos", [5] = "torse", [9] = "poignets",
  [10] = "mains", [6] = "taille", [7] = "jambes", [8] = "pieds", [11] = "doigt", [12] = "doigt", [13] = "bijou",
  [14] = "bijou", [16] = "main droite", [17] = "main gauche", [18] = "distance" }
GG.SLOT_FR = SLOT_FR
-- emplacements dont le vide compte (armure, arme ; distance pour qui s'en sert)
local CORE = { [1] = true, [3] = true, [5] = true, [6] = true, [7] = true, [8] = true, [9] = true, [10] = true,
  [15] = true, [16] = true }
local WEAK_ORDER = { 16, 17, 18, 5, 7, 1, 3, 10, 8, 15, 6, 9 }   -- l'arme d'abord, puis les grosses pièces
local RANGED_USER = { HUNTER = true, WARRIOR = true, ROGUE = true, MAGE = true, PRIEST = true, WARLOCK = true }
local QUALITY_HEX = { [0] = "ff9d9d9d", "ffffffff", "ff1eff00", "ff0070dd", "ffa335ee", "ffff8000" }

-- ===================================================================================== objets : lien ↔ forme courte
--- « item:ID:ench:g1:g2:g3:g4:suffixe:… » → id, suffixe (l'enchantement est ignoré : il ne se transmet pas à l'échange)
function GG.ParseLink(link)
  link = str(link)
  local s = link and link:match("item:([%-%d:]+)")
  if not s then return nil end
  local f = {}
  for v in (s .. ":"):gmatch("([^:]*):") do f[#f + 1] = v end
  local id = tonumber(f[1])
  if not id or id <= 0 then return nil end
  local sfx = tonumber(f[7] or "")
  if sfx == 0 then sfx = nil end
  return id, sfx
end
function GG.Compact(id, sfx) if not id then return nil end return sfx and (id .. "." .. sfx) or tostring(id) end
function GG.Uncompact(c)
  local id, sfx = tostring(c or ""):match("^(%d+)%.?(%-?%d*)$")
  id, sfx = tonumber(id), tonumber(sfx)
  if not id or id <= 0 or id > 9999999 then return nil end
  if sfx and (sfx == 0 or sfx < -99999 or sfx > 99999) then sfx = nil end
  return id, sfx
end
--- forme courte → chaîne d'objet que GetItemInfo / GetItemStats acceptent
function GG.ItemString(c)
  local id, sfx = GG.Uncompact(c)
  if not id then return nil end
  return "item:" .. id .. (sfx and ("::::::" .. sfx) or "")
end

local waiting, waitN = {}, 0
--- infos de l'objet (valeurs secrètes écartées) ; nil = pas encore en cache (chargement demandé)
local function info(x)
  if x == nil then return nil end
  local name, link, quality, ilvl, minLevel, _, _, _, equipLoc, _, _, classID, subID = (API.ItemInfo or function(i)
    return try(g("GetItemInfo"), i) end)(x)
  name = str(name)
  if not name then
    local id = type(x) == "number" and x or GG.ParseLink(x) or (type(x) == "string" and tonumber(x:match("item:(%d+)")))
    if id and not waiting[id] and waitN < 40 then
      waiting[id], waitN = true, waitN + 1
      local CI = g("C_Item")
      if type(CI) == "table" then try(CI.RequestLoadItemDataByID, id) end
    end
    return nil
  end
  local il = num(ilvl)
  local CI = g("C_Item")
  local det = (type(CI) == "table" and CI.GetDetailedItemLevelInfo) or g("GetDetailedItemLevelInfo")
  il = num((try(det, link or x))) or il
  return { name = name, link = str(link), quality = num(quality) or 1, ilvl = il or 0, minLevel = num(minLevel) or 0,
    equipLoc = str(equipLoc) or "", classID = num(classID), subID = num(subID) }
end
GG.Info = info
local function itemText(it, fallback)
  if not it then return fallback or "objet" end
  return "|c" .. (QUALITY_HEX[it.quality] or "ffffffff") .. it.name .. "|r"
end

-- ===================================================================================== état
local R = {}            -- [nom] = membre { name, unit, guid, class, level, online, addon, gear = {[slot] = lien},
                        --   gearAt, src ("treeai" | "inspect"), sp, bags = { lien… }, bagsAt, nextTry, why }
local order = {}        -- noms dans l'ordre du groupe
local me = { name = "?", unit = "player", me = true, gear = {}, bags = {} }
local S = { pend = nil, lastNotify = -1e9, sent = {}, sentAt = {}, wantSend = false, lastReq = -1e9, encounter = false,
  bagsDirty = true, bagsAt = -1e9, gen = 0, viewAt = -1e9, tipCache = {}, inBucket = {}, inspects = 0, lastRoster = "" }
local myBags = {}       -- objets échangeables de mes sacs { link, bag, slot, timer = texte | nil }

local function bump()
  S.gen = S.gen + 1
  S.trades = nil
  local II = T.ItemInfo
  if type(II) == "table" and type(II.Invalidate) == "function" then try(II.Invalidate) end
  local W = T.Window
  if type(W) == "table" and W.IsOpen and try(W.IsOpen) and W.CurrentPage and try(W.CurrentPage) == "group" then
    local t = now()
    if t - S.viewAt >= 2 and W.Dirty then S.viewAt = t try(W.Dirty) end
  end
end

local function inGroup()
  if pub(try(g("IsInGroup"))) then return true end
  return (num(try(g("GetNumGroupMembers"))) or num(try(g("GetNumPartyMembers"))) or 0) > 0
end
local function inInstance() local i = pub(try(g("IsInInstance"))) return i and true or false end
local function bossFight()
  if S.encounter then return true end
  local CIE = g("C_InstanceEncounter")
  if type(CIE) == "table" and pub(try(CIE.IsEncounterInProgress)) then return true end
  return pub(try(g("IsEncounterInProgress"))) and true or false
end

--- relit le groupe (party1..4) ; renvoie les nouveaux venus
function GG:ScanRoster()
  me.name = shortName(try(g("UnitName"), "player")) or me.name
  me.class = select(2, try(g("UnitClass"), "player")) or me.class
  me.class = pub(me.class)
  me.level = num(try(g("UnitLevel"), "player")) or me.level or 1
  local keep, added = {}, {}
  order = {}
  for i = 1, 4 do
    local u = "party" .. i
    if pub(try(g("UnitExists"), u)) then
      local nm = shortName(try(g("UnitName"), u))
      if nm and nm ~= me.name and not keep[nm] then
        local m = R[nm]
        if not m then m = { name = nm, gear = {}, bags = {}, nextTry = 0 }; R[nm] = m; added[#added + 1] = nm end
        keep[nm] = true
        m.unit = u
        local guid = str(try(g("UnitGUID"), u))
        if guid and m.guid and guid ~= m.guid then m.gear, m.gearAt, m.src = {}, nil, nil end
        m.guid = guid or m.guid
        m.class = pub(select(2, try(g("UnitClass"), u))) or m.class
        m.level = num(try(g("UnitLevel"), u)) or m.level
        local con = pub(try(g("UnitIsConnected"), u))
        m.online = con ~= false
        order[#order + 1] = nm
      end
    end
  end
  for nm in pairs(R) do if not keep[nm] then R[nm] = nil end end
  local sig = tconcat(order, ",")
  if sig ~= S.lastRoster then S.lastRoster = sig bump() end
  return added
end

local function members()   -- moi d'abord, puis l'ordre du groupe
  local out = { me }
  for _, nm in ipairs(order) do if R[nm] then out[#out + 1] = R[nm] end end
  return out
end
GG.Members = members
local function hasTreeAI(m)
  if m.addon then return true end
  local Gm = T.modules and T.modules.Group
  local gm = type(Gm) == "table" and type(Gm.members) == "table" and Gm.members[m.name]
  return (type(gm) == "table" and gm.addon) and true or false
end
GG.HasTreeAI = hasTreeAI

-- ===================================================================================== mon équipement, mes sacs
-- (4.4 SMART) équipement relu seulement quand le jeu dit qu'il a changé (PLAYER_EQUIPMENT_CHANGED → S.gearDirty),
-- ou toutes les GG.GEAR_MAX s au plus (filet) ; avant : 17 lectures + analyses de liens toutes les 2 s, pour rien
GG.GEAR_MAX = 60
local function readMine()
  local t = now()
  if me.gearAt and t - me.gearAt < GG.GEAR_MAX and not S.gearDirty then return me.gear end
  me.level = num(try(g("UnitLevel"), "player")) or me.level
  local gear = {}
  for _, s in ipairs(SLOTS) do
    local l = str(try(g("GetInventoryItemLink"), "player", s))
    if l then gear[s] = l end
  end
  me.gear, me.gearAt, me.src = gear, t, "self"
  return gear
end

local function tradePattern()
  if S.tradePat ~= nil then return S.tradePat end
  local s = str(g("BIND_TRADE_TIME_REMAINING"))
  if s then
    local p = s:gsub("([%(%)%.%+%-%*%?%[%]%^%$])", "%%%1"):gsub("%%%%s", "(.+)"):gsub("%%%%d", "(%%d+)")
    S.tradePat = "^" .. p
  else
    S.tradePat = false
  end
  return S.tradePat
end
local TRADE_PREFIX = { "Vous pouvez échanger cet objet", "You may trade this item" }
--- ligne d'infobulle « Vous pouvez échanger cet objet avec … pendant les 1 h 52 min prochaines » → durée (texte) | true
function GG.TradeLine(text)
  text = str(text)
  if not text then return nil end
  local p = tradePattern()
  if p then local d = text:match(p) if d then return d end end
  for _, pre in ipairs(TRADE_PREFIX) do if text:sub(1, #pre) == pre then return text:match("pendant les (.-) prochaine") or true end end
  return nil
end
local function tipLines(data)
  local out = {}
  if type(data) ~= "table" or type(data.lines) ~= "table" then return out end
  for _, ln in ipairs(data.lines) do
    if type(ln) == "table" then
      local t = str(ln.leftText)
      if not t and type(ln.args) == "table" then
        for _, a in ipairs(ln.args) do if type(a) == "table" and a.field == "leftText" then t = str(a.stringVal) end end
      end
      if t then out[#out + 1] = t end
    end
  end
  return out
end
--- infobulle d'un objet de mes sacs : encore échangeable (ramassé en groupe < 2 h) → durée restante (texte) | true ;
--- lié (ligne « Lié ») ; lue (le jeu a répondu). Mémoire 60 s par case.
local function bagTip(bag, slot, link)
  local key = bag .. ":" .. slot
  local c = S.tipCache[key]
  local t = now()
  if c and c.link == link and t - c.t < 60 then return c end
  c = { link = link, t = t }
  local TI = g("C_TooltipInfo")
  if type(TI) == "table" and type(TI.GetBagItem) == "function" then
    local lines = tipLines(try(TI.GetBagItem, bag, slot))
    c.read = #lines > 0
    local sb = str(g("ITEM_SOULBOUND")) or "Lié"
    for _, l in ipairs(lines) do
      c.timer = c.timer or GG.TradeLine(l)
      if l == sb or l == "Soulbound" then c.bound = true end
    end
  end
  S.tipCache[key] = c
  return c
end
local function bagItem(bag, slot)
  local C = g("C_Container")
  if type(C) == "table" and type(C.GetContainerItemInfo) == "function" then
    local i = try(C.GetContainerItemInfo, bag, slot)
    if type(i) ~= "table" then return nil end
    return str(i.hyperlink), pub(i.isBound), num(i.quality)
  end
  local r = { try(g("GetContainerItemInfo"), bag, slot) }
  if r[1] == nil and r[7] == nil then return nil end
  return str(r[7]), pub(r[11]), num(r[4])
end
--- mes objets équipables (vert ou mieux) que je peux échanger : non liés, ou liés mais encore échangeables
function GG:ScanBags()
  local E = eng()
  local out = {}
  if not E or not E.EQUIP_SLOTS then myBags = out return out end
  local nb = (API.NumBags and API.NumBags()) or 4
  for bag = 0, nb do
    local n = (API.BagSlots and API.BagSlots(bag)) or 0
    for slot = 1, n do
      local link, bound, quality = bagItem(bag, slot)
      if link and (quality == nil or quality >= 2) then
        local it = info(link)
        if it and E.EQUIP_SLOTS[it.equipLoc] and it.quality >= 2 then
          local timer
          if bound ~= false then   -- lié (ou inconnu : client sans « isBound ») → l'infobulle tranche
            local tip = bagTip(bag, slot, link)
            timer = tip.timer
            if bound == nil and tip.read and not tip.bound then bound = false end
          end
          if bound == false or timer then
            out[#out + 1] = { link = link, bag = bag, slot = slot, timer = timer ~= true and timer or nil, bound = bound == true }
          end
        end
      end
    end
  end
  myBags = out
  me.bags = {}
  for i, b in ipairs(out) do me.bags[i] = b.link end
  S.bagsAt = now()
  return out
end
function GG.MyTradeable() return myBags end

-- ===================================================================================== scores (Gear.Engine, gardé)
local function weightsFor(m)
  local E = eng()
  if not E then return nil end
  if m.me and E.Weights then return try(E.Weights) end
  local cw = E.WEIGHTS and m.class and E.WEIGHTS[m.class]
  if not cw then return nil end
  return (m.sp and cw[m.sp]) or cw.default
end
local function score(link, slot, w)
  local E = eng()
  if not (E and link and w) then return nil end
  local st = try(E.ItemStats, link)
  if type(st) ~= "table" then return nil end
  return num(try(E.ScoreStats, st, slot, w))
end
--- peut-il porter / manier cet objet ? → ok, niveau où il pourra, raison
function GG.Usable(m, it)
  local E = eng()
  if not (E and it and m.class) then return false end
  local slots = E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]
  if not slots then return false, nil, "non équipable" end
  local lvl = m.level or 1
  if it.classID == 4 and it.subID and it.subID > 0 and it.equipLoc ~= "INVTYPE_CLOAK" then
    local need = ((E.ARMOR_LEVEL or {})[m.class] or {})[it.subID]
    if not need then return false, nil, "armure qu'il ne porte pas" end
    if lvl < need then return false, need, "portable au niveau " .. need end
  elseif it.classID == 2 and it.subID then
    if not ((E.WEAPON_TYPES or {})[m.class] or {})[it.subID] then return false, nil, "arme qu'il ne manie pas" end
  end
  if it.minLevel and it.minLevel > lvl then return false, it.minLevel, "niveau " .. it.minLevel .. " requis" end
  return true
end
--- écart (%) avec ce qu'il porte : nil = inconnu (raison) ; 999 = emplacement vide
function GG.Gain(m, link, it)
  local E = eng()
  it = it or info(link)
  if not (E and it) then return nil, nil, "objet pas encore chargé" end
  local slots = E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]
  if not slots then return nil end
  local gear = m.me and readMine() or (m.gearAt and m.gear) or nil
  if not gear then return nil, slots[1], "équipement pas encore vu" end
  local w = weightsFor(m)
  local new = score(link, slots[1], w)
  if not new then return nil, slots[1], "stats inconnues" end
  local old, used
  if it.equipLoc == "INVTYPE_2HWEAPON" then
    old, used = (score(gear[16], 16, w) or 0) + (score(gear[17], 17, w) or 0), 16
  else
    local cand = slots
    if it.equipLoc == "INVTYPE_WEAPON" then
      local d = (E.DUAL_LEVEL or {})[m.class]
      cand = (d and (m.level or 1) >= d) and { 16, 17 } or { 16 }
    end
    if slots[1] == 17 and gear[16] then   -- main gauche avec une arme à deux mains : rien à gagner
      local w16 = info(gear[16])
      if w16 and w16.equipLoc == "INVTYPE_2HWEAPON" then return nil, 17, "arme à deux mains" end
    end
    for _, sl in ipairs(cand) do
      local v = gear[sl] and (score(gear[sl], sl, w) or 0) or 0
      if old == nil or v < old then old, used = v, sl end
    end
  end
  old = old or 0
  if old <= 0 then return (new > 0) and 999 or 0, used end
  return (new - old) / old * 100, used
end
local function gainTxt(gn)
  if not gn then return "?" end
  if gn >= 999 then return "emplacement vide" end
  return fmt("≈ +%d %%", floor(gn + 0.5))
end
GG.GainText = gainTxt

local RANGED_HUNTER = { [2] = true, [3] = true, [18] = true }   -- arc, fusil, arbalète : l'arme du chasseur
--- pour qui l'objet est fait : ceux qui le portent / manient (arc / fusil / arbalète : le chasseur d'abord), puis, si
--- l'objet a des stats en plus du DPS, ceux qui valorisent ces stats (≥ moitié du meilleur : un bâton d'Intelligence
--- n'est pas pour le guerrier, même si son DPS l'arrange) → { [nom] = true }
function GG.Candidates(link, it)
  local E = eng()
  local out, list = {}, {}
  if not (E and it) then return out end
  local hunter = false
  for _, m in ipairs(members()) do
    local ok, lvl = GG.Usable(m, it)
    if ok or lvl then list[#list + 1] = m if m.class == "HUNTER" then hunter = true end end
  end
  if it.classID == 2 and RANGED_HUNTER[it.subID or -1] and hunter then
    local keep = {}
    for _, m in ipairs(list) do if m.class == "HUNTER" then keep[#keep + 1] = m end end
    list = keep
  end
  local st = try(E.ItemStats, link)
  local slots = E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]
  local only
  if type(st) == "table" then
    for k, v in pairs(st) do if k ~= (E.STAT_KEYS or {}).DPS then only = only or {} only[k] = v end end
  end
  if only and slots and #list > 1 then
    local sc, best = {}, 0
    for _, m in ipairs(list) do
      local v = num(try(E.ScoreStats, only, slots[1], weightsFor(m))) or 0
      sc[m.name] = v
      if v > best then best = v end
    end
    for _, m in ipairs(list) do if best <= 0 or sc[m.name] >= best * 0.5 then out[m.name] = true end end
  else
    for _, m in ipairs(list) do out[m.name] = true end
  end
  return out
end

--- classement d'un objet pour le groupe → { need = { {m, gain, slot} } (gain ≥ seuil, trié), unknown = { m… },
--- later = { {m, lvl} }, it } ; nil si l'objet n'est pas équipable ou pas encore chargé
function GG.Rank(link)
  local it = info(link)
  local E = eng()
  if not (it and E and E.EQUIP_SLOTS and E.EQUIP_SLOTS[it.equipLoc]) then return nil end
  local r = { it = it, need = {}, unknown = {}, later = {}, all = {} }
  local cands = GG.Candidates(link, it)
  for _, m in ipairs(members()) do
    local ok, lvl = GG.Usable(m, it)
    if ok and not cands[m.name] then ok, lvl = false, nil end
    if ok then
      local gn, slot, why = GG.Gain(m, link, it)
      r.all[m.name] = { m = m, gain = gn, slot = slot, why = why }
      if gn == nil then r.unknown[#r.unknown + 1] = m
      elseif gn >= GG.MIN_GAIN then r.need[#r.need + 1] = { m = m, gain = gn, slot = slot } end
    elseif lvl then
      r.later[#r.later + 1] = { m = m, lvl = lvl }
    end
  end
  tsort(r.need, function(a, b)
    if a.gain ~= b.gain then return a.gain > b.gain end
    return a.m.name < b.m.name
  end)
  return r
end
local function who(m) return m.me and "toi" or m.name end
local function needTxt(n) return who(n.m) .. " (" .. gainTxt(n.gain) .. (n.slot and SLOT_FR[n.slot] and (", " .. SLOT_FR[n.slot]) or "") .. ")" end

-- ===================================================================================== échanges conseillés
--- { link, it, from, to, gain, slot, mine (l'objet est dans mes sacs), forMe, ownGain, unknown, timer }
function GG.Trades()
  if S.trades and S.tradesGen == S.gen and now() - (S.tradesAt or 0) < 10 then return S.trades end
  local out, seen = {}, {}
  local function consider(link, owner, timer)
    if not link then return end
    local key = owner.name .. "|" .. link
    if seen[key] then return end
    seen[key] = true
    local r = GG.Rank(link)
    if not r then return end
    local best = r.need[1]
    if best and best.m.name == owner.name then best = r.need[2] end
    if not best then return end
    local own = r.all[owner.name]
    local ownGain = own and own.gain
    if ownGain and ownGain >= GG.MIN_GAIN and ownGain >= best.gain then return end   -- il le garde : c'est pour lui
    local unk = {}
    for _, m in ipairs(r.unknown) do if m.name ~= owner.name then unk[#unk + 1] = m.name end end
    out[#out + 1] = { link = link, it = r.it, from = owner, to = best.m, gain = best.gain, slot = best.slot,
      mine = owner.me or false, forMe = best.m.me or false, ownGain = ownGain, unknown = unk, timer = timer }
  end
  for _, b in ipairs(myBags) do consider(b.link, me, b.timer) end
  for _, nm in ipairs(order) do
    local m = R[nm]
    if m and m.bagsAt then for _, l in ipairs(m.bags) do consider(l, m) end end
  end
  tsort(out, function(a, b)
    if a.gain ~= b.gain then return a.gain > b.gain end
    if a.forMe ~= b.forMe then return a.forMe end
    return a.it.name < b.it.name
  end)
  while #out > GG.MAX_TRADES do table.remove(out) end
  S.trades, S.tradesGen, S.tradesAt = out, S.gen, now()
  return out
end

--- (5.0 GEAR) garde-fou de vente : un objet de MES sacs qu'un ami du groupe porterait mieux n'est jamais « à vendre »
--- → « utile à Bravo (ton groupe) » ou nil
function GG.TradeFor(id)
  if not (GG._active and id and inGroup() and #order > 0) then return nil end
  for _, tr in ipairs(GG.Trades()) do
    local tid = tr.mine and tr.link and tonumber(tostring(tr.link):match("item:(%d+)"))
    if tid == id and type(tr.to) == "table" and not tr.to.me then return "utile à " .. tostring(tr.to.name) .. " (ton groupe)" end
  end
  return nil
end

-- ===================================================================================== résumé d'un membre
local function ago(t0)
  if not t0 then return nil end
  local s = now() - t0
  if s < 60 then return "à l'instant" end
  if s < 3600 then return fmt("il y a %d min", floor(s / 60 + 0.5)) end
  return fmt("il y a %d h", floor(s / 3600 + 0.5))
end
GG.Ago = ago
--- { known, ilvl, n, weak = { "torse (vide)", … }, src, age, why }
function GG.Summary(m)
  local gear = m.me and readMine() or (m.gearAt and m.gear) or nil
  local s = { known = gear ~= nil, src = m.me and "self" or m.src, age = (not m.me) and ago(m.gearAt) or nil, why = m.why }
  if not gear then return s end
  local sum, cnt, pending = 0, 0, false
  local items = {}
  local twoHand = false
  for _, sl in ipairs(SLOTS) do
    local l = gear[sl]
    if l then
      local it = info(l)
      if it then
        if it.ilvl > 0 then sum, cnt = sum + it.ilvl, cnt + 1 end
        items[#items + 1] = { slot = sl, it = it }
        if sl == 16 and it.equipLoc == "INVTYPE_2HWEAPON" then twoHand = true end
      else
        pending = true
      end
    end
  end
  s.n, s.pending = cnt, pending
  s.ilvl = cnt > 0 and floor(sum / cnt + 0.5) or nil
  local weak = {}
  for _, sl in ipairs(WEAK_ORDER) do
    local empty = not gear[sl]
    if empty and (CORE[sl] or (sl == 18 and RANGED_USER[m.class or ""]) or (sl == 17 and not twoHand and (m.class == "WARRIOR"
        or m.class == "ROGUE" or m.class == "PALADIN" or m.class == "SHAMAN"))) then
      weak[#weak + 1] = SLOT_FR[sl] .. " (vide)"
    end
  end
  tsort(items, function(a, b)
    if a.it.quality ~= b.it.quality then return a.it.quality < b.it.quality end
    if a.it.ilvl ~= b.it.ilvl then return a.it.ilvl < b.it.ilvl end
    return a.slot < b.slot
  end)
  for _, e in ipairs(items) do
    if #weak >= 3 then break end
    if e.it.quality <= 1 or (s.ilvl and e.it.ilvl > 0 and e.it.ilvl <= s.ilvl - 6) then
      weak[#weak + 1] = fmt("%s (%s, niv %d)", SLOT_FR[e.slot], e.it.quality == 0 and "gris" or e.it.quality == 1 and "blanc"
        or "vert", e.it.ilvl)
    end
  end
  s.weak = weak
  return s
end

-- ===================================================================================== partage TreeAI (GGE / GGB / GGQ)
local payMemo = {}   -- (4.4 SMART) message calculé une fois par état (même table d'équipement / de sacs = même texte)
local function myGearPayload()
  readMine()
  local E = eng()
  local sp = E and E.SpecTab and num(try(E.SpecTab)) or 0
  if payMemo.gear == me.gear and payMemo.sp == sp and payMemo.g then return payMemo.g end
  local parts = {}
  for _, sl in ipairs(SLOTS) do
    local id, sfx = GG.ParseLink(me.gear[sl])
    if id then parts[#parts + 1] = sl .. "=" .. GG.Compact(id, sfx) end
  end
  payMemo.gear, payMemo.sp = me.gear, sp
  payMemo.g = "1;" .. floor(sp or 0) .. ";" .. tconcat(parts, ",")
  return payMemo.g
end
local function myBagsPayload()
  if payMemo.bags == myBags and payMemo.b then return payMemo.b end
  local parts = {}
  for _, b in ipairs(myBags) do
    if #parts >= GG.MAX_BAG then break end
    local id, sfx = GG.ParseLink(b.link)
    if id then parts[#parts + 1] = GG.Compact(id, sfx) end
  end
  payMemo.bags, payMemo.b = myBags, "1;" .. tconcat(parts, ",")
  return payMemo.b
end
GG.GearPayload, GG.BagsPayload = myGearPayload, myBagsPayload

local function canSend()
  if not (GG.db and GG.db.share ~= false and T.Send and inGroup() and #order > 0) or T.commPaused then return false end
  local cs = T.CommStatus and T:CommStatus()
  if type(cs) == "table" and (cs.queued or 0) > 0 then return false end   -- jamais par-dessus un envoi en file
  return true
end
--- envoie ce qui a changé (≤ 1 envoi d'un type / 10 s) ; force = réponse à une demande
function GG:Flush(force)
  if not canSend() then return false end
  local t = now()
  local anyAddon = false
  for _, nm in ipairs(order) do if R[nm] and hasTreeAI(R[nm]) then anyAddon = true end end
  if not anyAddon and not S.hello then return false end   -- personne n'a TreeAI : on se tait (après le bonjour)
  local sentAny = false
  S.flushWait = false
  for _, kind in ipairs({ "GGE", "GGB" }) do
    local p = kind == "GGE" and myGearPayload() or myBagsPayload()
    if p ~= S.sent[kind] or (force and S.wantSend) then
      if t - (S.sentAt[kind] or -1e9) >= (force and 5 or GG.SEND_GAP) and T:Send(kind, p, "PARTY") then
        S.sent[kind], S.sentAt[kind], sentAny = p, t, true
      else
        S.flushWait = true                                   -- à envoyer dès que le délai le permet : le minuteur reste vif
      end
    end
  end
  if S.wantReq and t - S.lastReq >= GG.REQ_GAP then
    if T:Send("GGQ", "1", "PARTY") then S.lastReq, S.wantReq = t, false end
  end
  if sentAny then S.hello = false S.wantSend = false end
  return sentAny
end

local function allow(name)   -- 6 jetons, +1 / 3 s par membre
  local t = now()
  local b = S.inBucket[name]
  if not b then b = { tok = 6, t = t }; S.inBucket[name] = b end
  b.tok = min(6, b.tok + (t - b.t) / 3)
  b.t = t
  if b.tok < 1 then return false end
  b.tok = b.tok - 1
  return true
end
local function senderRec(sender)
  local nm = shortName(sender)
  if not nm or nm == me.name then return nil end
  local m = R[nm]
  if not m and now() - (S.rosterAt or -1e9) >= 2 then S.rosterAt = now() GG:ScanRoster() m = R[nm] end
  return m
end
--- réception (tout revalidé : version, bornes, longueurs)
function GG:OnComm(kind, payload, sender)
  local m = senderRec(sender)
  if not m or type(payload) ~= "string" or #payload > 600 or not allow(m.name) then return end
  local ver, rest = payload:match("^(%d+);?(.*)$")
  if ver ~= "1" then return end
  m.addon = true
  if GG.Wake then GG.Wake() end
  if kind == "GGQ" then
    S.wantSend = true
    return
  end
  if kind == "GGE" then
    local sp, list = rest:match("^(%d);(.*)$")
    if rest == "off" then sp, list = "0", "off" end
    if not sp then return end
    local gear, n = {}, 0
    for sl, c in list:gmatch("(%d+)=([%d%.%-]+)") do
      sl = tonumber(sl)
      local s = GG.ItemString(c)
      if sl and SLOT_FR[sl] and s and n < 20 then gear[sl], n = s, n + 1 end
    end
    if list == "off" then   -- module coupé chez lui : on oublie (le jeu pourra l'inspecter)
      m.gear, m.gearAt, m.src, m.addon, m.addonWait = {}, nil, nil, false, nil
    else
      m.gear, m.gearAt, m.src, m.why = gear, now(), "treeai", nil
      m.sp = tonumber(sp) ~= 0 and tonumber(sp) or nil
    end
  elseif kind == "GGB" then
    local bags = {}
    for c in rest:gmatch("[^,]+") do
      local s = GG.ItemString(c)
      if s and #bags < GG.MAX_BAG then bags[#bags + 1] = s end
    end
    m.bags, m.bagsAt = bags, now()
  end
  bump()
end

-- ===================================================================================== inspection (les autres)
local function inspectFrameOpen()
  local f = g("InspectFrame")
  return type(f) == "table" and f.IsShown and try(f.IsShown, f) and true or false
end
local function wantsInspect(m, t)
  if hasTreeAI(m) then
    if m.src == "treeai" then return false end             -- il partage lui-même (en direct)
    m.addonWait = m.addonWait or t
    if t - m.addonWait < 30 then return false end          -- ami TreeAI : on attend son envoi 30 s, puis le jeu
  end
  if t < (m.nextTry or 0) then return false end
  if m.src == "inspect" and m.gearAt and t - m.gearAt < GG.STALE and not m.changed then return false end
  return true
end
--- le prochain membre à inspecter (hors combat, à portée, un à la fois, ≥ 2 s) ; → membre | nil, raison
function GG:TryInspect(t)
  t = t or now()
  if not (self.db and self.db.inspect ~= false) then return nil, "off" end
  local p = S.pend
  if p and t - p.t > GG.INSPECT_TIMEOUT then   -- pas de réponse : ce membre plus tard
    local m = R[p.name]
    if m then m.nextTry, m.why = t + GG.INSPECT_RETRY, "pas de réponse du jeu" end
    S.pend, p = nil, nil
  end
  if p then return nil, "attente" end
  if inCombat() then return nil, "combat" end
  if bossFight() then return nil, "boss" end
  if inspectFrameOpen() or type(g("NotifyInspect")) ~= "function" then return nil, "occupé" end
  if t - S.lastNotify < GG.INSPECT_GAP then return nil, "délai" end
  local Gm = T.modules and T.modules.Group
  local gs = type(Gm) == "table" and type(Gm._state) == "table" and Gm._state or nil
  if gs and type(gs.pendingInspect) == "table" and t - (tonumber(gs.pendingInspect.t) or 0) <= 5 then return nil, "groupe" end
  for _, nm in ipairs(order) do
    local m = R[nm]
    if m and m.unit and m.guid and wantsInspect(m, t) then
      if m.online == false then m.why = "hors ligne"
      elseif pub(try(g("UnitIsVisible"), m.unit)) == false then m.why = "trop loin"
      elseif type(g("CanInspect")) == "function" and not pub(try(g("CanInspect"), m.unit)) then m.why = "inspection refusée par le jeu"
      elseif type(g("CheckInteractDistance")) == "function" and not pub(try(g("CheckInteractDistance"), m.unit, 1)) then
        m.why = "trop loin"
      else
        S.lastNotify, S.inspects = t, S.inspects + 1
        S.pend = { name = nm, guid = m.guid, unit = m.unit, t = t }
        if gs then gs.lastInspect = t end   -- le module Groupe attend son tour (une demande à la fois)
        try(g("NotifyInspect"), m.unit)
        return m
      end
    end
  end
  return nil, "personne"
end
--- vrai quand ce module fait les inspections (le module Groupe n'inspecte plus : une demande à la fois, un seul cache)
function GG.OwnsInspect()
  return (GG._active and GG:IsEnabled() and GG.db and GG.db.inspect ~= false) and true or false
end
local function readUnit(m, unit)
  local gear, n = {}, 0
  for _, s in ipairs(SLOTS) do
    local l = str(try(g("GetInventoryItemLink"), unit, s))
    if l then gear[s], n = l, n + 1 end
  end
  return gear, n
end
function GG:OnInspect(guid)
  guid = str(guid)
  if not guid then return end
  local p = S.pend
  local mine = p and p.guid == guid
  local m
  for _, nm in ipairs(order) do if R[nm] and R[nm].guid == guid then m = R[nm] end end
  if mine then S.pend = nil end
  if not m or not m.unit or str(try(g("UnitGUID"), m.unit)) ~= guid then return end
  if inCombat() then if mine then m.nextTry = now() + 5 end return end
  local gear, n = readUnit(m, m.unit)
  if n == 0 then
    m.nextTry, m.why = now() + GG.INSPECT_RETRY, "réponse vide (réessai)"
  else
    m.gear, m.gearAt, m.src, m.why, m.changed = gear, now(), "inspect", nil, nil
    m.nextTry = now() + GG.CHANGED_MIN
    local Gm = T.modules and T.modules.Group   -- le module Groupe profite de la lecture (signaux, butin)
    if type(Gm) == "table" and type(Gm.ReadGear) == "function" then pcall(Gm.ReadGear, Gm, m.unit, guid) end
  end
  if mine and type(g("ClearInspectPlayer")) == "function" and not inspectFrameOpen() then try(g("ClearInspectPlayer")) end
  bump()
end

-- façade Team.GearOf (Donjon, Infos objets) : l'équipement partagé par un ami TreeAI quand le jeu n'a rien inspecté
local function gearOf(guid)
  if not guid then return nil end
  for _, nm in ipairs(order) do
    local m = R[nm]
    if m and m.guid == guid and m.gearAt then
      local n = 0
      for _ in pairs(m.gear) do n = n + 1 end
      return { t = m.gearAt, links = m.gear, n = n, missing = {}, src = m.src }
    end
  end
  return nil
end
GG.GearOf = gearOf
local function hookTeam()
  local Tm = T.Team
  if type(Tm) ~= "table" or Tm._ggWrapped or type(Tm.GearOf) ~= "function" then return end
  local orig = Tm.GearOf
  Tm._ggWrapped = true
  Tm.GearOf = function(guid)
    local c = orig(guid)
    if (type(c) ~= "table" or type(c.links) ~= "table" or next(c.links) == nil) and GG._active then return gearOf(guid) or c end
    return c
  end
end

-- ===================================================================================== infobulles (ligne « Groupe »)
--- au moins un AUTRE membre dont le gain est connu (sinon on n'a rien de plus que la classe et l'armure)
function GG.KnowsOthers(r)
  for _, e in pairs(r.all) do if e.gain ~= nil and not e.m.me then return true end end
  return false
end
local function unknownNames(r, n)
  local names = {}
  for _, m in ipairs(r.unknown) do names[#names + 1] = who(m) end
  return andList(names, n or 2)
end
--- « Besoin pour Bravo (≈ +12 %, main droite) · Cupidité pour les autres » ; un gain inconnu est dit, jamais « Cupidité »
function GG.NeedLine(r, parts)
  local unk = #r.unknown > 0 and (" · gain inconnu pour " .. unknownNames(r)) or ""
  if #parts == 0 then
    if unk ~= "" then return "aucun gain connu" .. unk end
    return "personne n'y gagne → Cupidité pour tous"
  end
  local rest = (unk == "" and #r.need < #members()) and " · Cupidité pour les autres" or ""
  return "Besoin pour " .. tconcat(parts, ", ") .. rest .. unk
end
--- la ligne « Groupe » d'une infobulle d'objet → texte, détails | nil (rien à dire de mieux que la ligne d'origine)
function GG.TooltipText(link, ctx)
  if not (GG._active and GG.db and GG.db.tooltip ~= false and inGroup() and #order > 0) then return nil end
  ctx = ctx or {}
  local r = GG.Rank(link)
  if not r then return nil end
  if not GG.KnowsOthers(r) then return nil end   -- aucun ami connu : la ligne d'origine (classe, armure) dit mieux
  local details = {}
  if #r.unknown > 0 then
    local names = {}
    for _, m in ipairs(r.unknown) do names[#names + 1] = who(m) end
    details[#details + 1] = "Gain inconnu pour " .. andList(names, 3) .. " (équipement pas encore vu)."
  end
  for _, l in ipairs(r.later) do details[#details + 1] = fmt("%s pourra s'en servir au niv %d.", l.m.me and "Toi" or l.m.name, l.lvl) end
  local parts = {}
  if ctx.bag then
    for _, n in ipairs(r.need) do if not n.m.me and #parts < 2 then parts[#parts + 1] = needTxt(n) end end
    if #parts == 0 then return nil end
    local mine = r.all[me.name]
    if mine and mine.gain and mine.gain >= GG.MIN_GAIN and r.need[1] and r.need[1].m.me then return nil end   -- c'est pour toi
    if ctx.bound and not ctx.tradeable then
      details[#details + 1] = "Lié à toi : il ne peut plus être échangé."
      return nil
    end
    return "Mieux pour : " .. tconcat(parts, ", "), details, r
  end
  for _, n in ipairs(r.need) do if #parts < 3 then parts[#parts + 1] = needTxt(n) end end
  return GG.NeedLine(r, parts), details, r
end
local function tradeableAt(bag, slot)
  for _, b in ipairs(myBags) do if b.bag == bag and b.slot == slot then return true end end
  return false
end
local function hookItemInfo()
  local II = T.ItemInfo
  local SEC = type(II) == "table" and II.Sections
  if type(SEC) ~= "table" or type(SEC.group) ~= "function" or SEC._ggWrapped then return end
  local orig = SEC.group
  SEC._ggWrapped = true
  SEC.group = function(it, c, o, st)
    if type(it) == "table" and type(c) == "table" and not (c.equipped or c.merchant or c.reward) then
      local ok, text, details, r = pcall(GG.TooltipText, it.link or it.id, { bag = c.bag ~= nil, bound = c.bound,
        tradeable = c.bag and tradeableAt(c.bag, c.slot) })
      if ok and text then
        o.label, o.tone, o.text = "Pour ton groupe", "info", text
        for _, d in ipairs(details or {}) do o.details[#o.details + 1] = d end
        if c.bag and type(r) == "table" and type(st) == "table" and not st.keep then   -- « Valeur : Donne-le à Bravo »
          for _, n in ipairs(r.need) do if not n.m.me then st.give = n.m.name break end end
        end
        return
      end
    end
    return orig(it, c, o, st)
  end
end
-- repli quand « Infos objets » est coupé : une ligne à nous (post-appel des infobulles d'objet, jamais de méthode remplacée)
local function ownTooltip(tt, data)
  if not GG._active or (tt ~= g("GameTooltip") and tt ~= g("ItemRefTooltip")) then return end
  local II = T.ItemInfo
  if type(II) == "table" and type(II.Owns) == "function" and try(II.Owns) then return end
  if tt.IsForbidden and try(tt.IsForbidden, tt) then return end
  local link
  if tt.GetItem then link = str(select(2, try(tt.GetItem, tt))) end
  if not link and type(data) == "table" then link = str(data.hyperlink) end
  if not link then return end
  local text = GG.TooltipText(link, {})
  if text then try(tt.AddLine, tt, "|cff66bbffGroupe|r : " .. text, 0.85, 0.85, 0.85, true) end
end
local hookedTip = false
local function hookTooltips()
  if hookedTip then return end
  local TDP, En = g("TooltipDataProcessor"), g("Enum")
  local E = type(En) == "table" and En.TooltipDataType or nil
  if type(TDP) == "table" and type(TDP.AddTooltipPostCall) == "function" and type(E) == "table" and E.Item ~= nil then
    hookedTip = true
    pcall(TDP.AddTooltipPostCall, E.Item, function(tt, data) pcall(ownTooltip, tt, data) end)
    return
  end
  local tt = g("GameTooltip")
  if type(tt) == "table" and tt.HookScript then
    hookedTip = true
    pcall(tt.HookScript, tt, "OnTooltipSetItem", function(self) pcall(ownTooltip, self, nil) end)
  end
end

-- ===================================================================================== jets de butin (texte seulement)
function GG.RollText(link, name)
  local r = link and GG.Rank(link)
  if not r then return nil end
  if not GG.KnowsOthers(r) then return nil end   -- aucun ami connu : la ligne d'origine (classe, armure) dit mieux
  local label = q(r.it and r.it.name or name or "objet")
  local parts = {}
  for _, n in ipairs(r.need) do if #parts < 3 then parts[#parts + 1] = needTxt(n) end end
  return fmt("Jet %s : %s", label, GG.NeedLine(r, parts))
end
function GG:OnLootRoll(rollID)
  if not (self.db and self.db.rolls ~= false) or #order == 0 or inInstance() then return end   -- en donjon : module Donjon
  local link = str(try(g("GetLootRollItemLink"), rollID))
  local TI = g("C_TooltipInfo")
  if not link and type(TI) == "table" and type(TI.GetLootRollItem) == "function" then   -- Forever : données d'infobulle
    local d = try(TI.GetLootRollItem, rollID)
    if type(d) == "table" then link = str(d.hyperlink) or (num(d.id) and ("item:" .. num(d.id))) or nil end
  end
  local name = str(select(2, try(g("GetLootRollItemInfo"), rollID)))
  local text = GG.RollText(link, name)
  if not text and link then
    -- (5.0 GEAR) personne d'autre de connu : au moins TON gain, chiffré (même verdict que l'infobulle) — jamais de jet
    local Gm = T.Gear
    local sc = type(Gm) == "table" and Gm._active and type(Gm.Score) == "function" and try(Gm.Score, link) or nil
    if type(sc) == "table" and sc.known and sc.usable and sc.kind == "good" and not sc.req then
      text = fmt("Jet %s : mieux pour toi (%s) → Besoin", q(name or (sc.item and sc.item.name) or "objet"), tostring(sc.text))
    end
  end
  if not text then return end
  S.rolls = S.rolls or {}
  S.rolls[#S.rolls + 1] = text
  local Gm = T.modules and T.modules.Group
  -- après la ligne du module Groupe (même clé : la nôtre la remplace, une seule ligne de jet)
  T:After(0.1, function()
    if type(Gm) == "table" and Gm._active and type(Gm.Offer) == "function" then
      pcall(Gm.Offer, Gm, "roll" .. tostring(rollID), "news", text, 1, { ttl = 60, cooldown = 60 })
    end
  end)
end

-- ===================================================================================== échange (clic seulement)
local function unitOf(name)
  local m = R[name]
  if m and m.unit and shortName(try(g("UnitName"), m.unit)) == name then return m.unit end
  for i = 1, 4 do
    local u = "party" .. i
    if shortName(try(g("UnitName"), u)) == name then return u end
  end
  return nil
end
local function say(text)
  local W = T.Window
  if type(W) == "table" and W.Flash then try(W.Flash, text, 8) end
  GG.lastSay = text
end
--- ouvre la fenêtre d'échange du jeu avec ce membre (clic du joueur, hors combat, à portée ; il pose l'objet lui-même)
function GG.OpenTrade(name)
  if not (T.InUserAction and T:InUserAction()) then return false end
  if inCombat() then say("Pas d'échange en combat : réessaie après le combat") return false end
  local u = unitOf(name)
  if not u then say(tostring(name) .. " n'est plus dans le groupe") return false end
  if type(g("CheckInteractDistance")) == "function" and not pub(try(g("CheckInteractDistance"), u, 2)) then
    say("Trop loin de " .. name .. " pour échanger : rapproche-toi (≈ 10 m)")
    return false
  end
  if type(g("InitiateTrade")) ~= "function" then say("Échange indisponible ici") return false end
  try(g("InitiateTrade"), u)
  say("Échange ouvert avec " .. name .. " : pose l'objet toi-même")
  return true
end

-- ===================================================================================== page « Groupe » de la fenêtre
local function classHex(class)
  local Th = T.Theme
  if type(Th) == "table" and Th.ClassColor then
    local r, gg, b = Th:ClassColor(class)
    if r then return fmt("ff%02x%02x%02x", floor(r * 255 + 0.5), floor(gg * 255 + 0.5), floor(b * 255 + 0.5)) end
  end
  return "ffffffff"
end
local function memberEntry(m)
  local s = GG.Summary(m)
  local head = "|c" .. classHex(m.class) .. (m.me and "Toi" or m.name) .. "|r · " .. (CLASS_FR[m.class or ""] or "?")
    .. " " .. tostring(m.level or "?")
  local tip = {}
  local right
  if s.known then
    head = head .. (s.ilvl and (" · niv d'objet ≈ " .. s.ilvl) or "")
    if s.weak and #s.weak > 0 then head = head .. " · faible : " .. andList(s.weak, 3) end
    right = m.me and "toi" or (s.src == "treeai" and "TreeAI, en direct") or ("vu " .. (s.age or "?"))
    tip[#tip + 1] = s.src == "treeai" and "Partagé par son TreeAI (mis à jour quand il change d'objet)."
      or s.src == "inspect" and ("Inspecté " .. (s.age or "") .. " (le jeu ne dit rien entre deux inspections).") or nil
    if s.pending then tip[#tip + 1] = "Certains objets ne sont pas encore chargés par le jeu." end
    tip[#tip + 1] = "Niveau d'objet moyen des emplacements portés ; « faible » = vide, gris / blanc, ou bien en dessous."
  else
    head = head .. " · équipement inconnu" .. (m.why and (" (" .. m.why .. ")") or "")
    right = "inconnu"
    tip[#tip + 1] = hasTreeAI(m) and "Son TreeAI n'a pas encore envoyé son équipement." or
      "Pas encore inspecté : il faut être à portée d'inspection (≈ 10 m), hors combat."
  end
  local e = { text = head, right = right, icon = CLASS_ICON[m.class or ""], kind = "gear", tooltip = tip }
  if not m.me and (not s.known or m.src == "inspect") then
    e.onClick = function() m.nextTry, m.changed = 0, true GG:TryInspect(now()) end
    e.clickHint = "Clic : l'inspecter dès qu'il est à portée"
  end
  return e
end
GG.MemberEntry = memberEntry
local function tradeEntry(tr)
  local fromTxt, toTxt = tr.from.me and "toi" or tr.from.name, tr.to.me and "toi" or tr.to.name
  local slot = tr.slot and SLOT_FR[tr.slot]
  local text = fmt("%s · %s → %s · %s%s", itemText(tr.it), fromTxt, toTxt, gainTxt(tr.gain), slot and (" (" .. slot .. ")") or "")
  local tip = {}
  local cur = tr.to.me and me.gear[tr.slot or 0] or (tr.to.gear and tr.to.gear[tr.slot or 0])
  local curIt = cur and info(cur)
  tip[#tip + 1] = (tr.to.me and "Tu portes : " or (tr.to.name .. " porte : ")) .. (curIt and curIt.name or (cur and "?" or "rien"))
  if tr.ownGain then tip[#tip + 1] = fmt("Pour %s : %s.", tr.from.me and "toi" or tr.from.name, gainTxt(tr.ownGain)) end
  if tr.timer then tip[#tip + 1] = "Encore échangeable pendant " .. tostring(tr.timer) .. "." end
  if #tr.unknown > 0 then tip[#tip + 1] = "Équipement pas encore vu : " .. andList(tr.unknown, 3) .. "." end
  tip[#tip + 1] = "Estimation : poids de stats de l'onglet Équipement (≈), comparé à ce qu'il porte."
  local e = { text = text, right = tr.forMe and "pour toi" or nil, kind = "gear", tooltip = tip }
  local other = tr.from.me and tr.to or (tr.to.me and tr.from) or nil
  if other then
    e.onClick = function() GG.OpenTrade(other.name) end
    e.clickHint = "Clic : ouvrir l'échange avec " .. other.name .. " (tu poses l'objet toi-même)"
  else
    tip[#tip + 1] = "À faire entre " .. tr.from.name .. " et " .. tr.to.name .. "."
  end
  return e
end
GG.TradeEntry = tradeEntry
--- section de la page « Groupe » : cartes des membres, puis échanges conseillés (mise en page de la fenêtre)
function GG.Section(Ly)
  if type(Ly) ~= "table" or type(Ly.header) ~= "function" or Ly._ggDrawn or not GG._active or not inGroup() or #order == 0 then
    return 0
  end
  Ly._ggDrawn = true   -- une fois par rendu (même si la page l'appelle aussi elle-même)
  local W = T.Window
  local match = (type(W) == "table" and W.Match) or function() return true end
  local list = members()
  local n = 0
  if Ly.header("Équipement du groupe", "gg:gear", #list) then
    for _, m in ipairs(list) do
      local e = memberEntry(m)
      if match(e.text, e.right) then Ly.row(e) n = n + 1 end
    end
  end
  local trades = GG.Trades()
  if Ly.header("Échanges conseillés", "gg:trade", #trades > 0 and #trades or nil) then
    if #trades == 0 then
      local seen = #myBags
      for _, nm in ipairs(order) do if R[nm] and R[nm].bagsAt then seen = seen + #R[nm].bags end end
      Ly.text(seen > 0 and fmt("Aucun échange utile : %d objet%s échangeable%s vu%s, personne n'y gagne.", seen,
        seen > 1 and "s" or "", seen > 1 and "s" or "", seen > 1 and "s" or "")
        or "Aucun objet échangeable vu (tes sacs, et ceux de tes amis TreeAI).", "S", "muted")
    else
      for _, tr in ipairs(trades) do
        local e = tradeEntry(tr)
        if match(e.text, e.right) then Ly.row(e) n = n + 1 end
      end
    end
  end
  return n
end
local function hookWindow()
  local W = T.Window
  if type(W) ~= "table" then return end
  if type(W.AddSection) == "function" and not GG._sectionAdded then   -- (LAYOUT) API de sections, si elle existe
    local ok = pcall(W.AddSection, "group", { id = "groupgear", title = "Équipement du groupe", render = GG.Section })
    if ok then GG._sectionAdded = true return end
  end
  local p = type(W.pageIndex) == "table" and W.pageIndex.group
  if type(p) ~= "table" or type(p.render) ~= "function" or p._ggWrapped == p.render then return end
  local orig = p.render
  local wrapped = function(Ly, ...)
    orig(Ly, ...)
    local ok, err = pcall(GG.Section, Ly)
    if not ok and T._errorHook then pcall(T._errorHook, err, "GroupGear:Section") end
  end
  p.render, p._ggWrapped = wrapped, wrapped
end

-- ===================================================================================== minuteur (en groupe seulement)
-- (5.0 INTEG) le minuteur n'est vif (2 s) que s'il a quelque chose à faire (équipement / sacs à relire, envoi en
-- attente, inspection en cours ou possible) ; sinon il s'étire à GG.TICK_QUIET (un ami trop loin n'est plus retenté
-- toutes les 2 s : c'était ≈ 50 kinstr / min au repos, en groupe). Un événement utile le réveille tout de suite.
GG.TICK_QUIET = 6
local setPace
function GG:Tick()
  if not inGroup() or #order == 0 then return end
  local t = now()
  if inCombat() then return end
  if S.gearDirty then S.gearDirty = false me.gearAt = nil readMine() end
  if S.bagsDirty and t - S.bagsAt >= 5 then S.bagsDirty = false self:ScanBags() bump() end
  self:Flush(S.wantSend)
  local who, why = self:TryInspect(t)
  if S.itemsLoaded and t - (S.loadedAt or -1e9) >= 2 then S.itemsLoaded, S.loadedAt = false, t bump() end
  local busy = S.gearDirty or S.bagsDirty or S.wantSend or S.wantReq or S.hello or S.flushWait or S.itemsLoaded
    or S.pend ~= nil or who ~= nil or why == "délai" or why == "attente" or why == "groupe" or why == "occupé"
  if not busy then   -- un réessai daté (pas de réponse du jeu : 30 s) garde le rythme vif à son approche
    for _, nm in ipairs(order) do
      local m = R[nm]
      if m and m.nextTry and m.nextTry > t and m.nextTry - t <= GG.TICK_QUIET then busy = true break end
    end
  end
  setPace(busy and GG.TICK or GG.TICK_QUIET)
end
local function stopTicker()
  if GG._ticker and GG._ticker.Cancel then GG._ticker:Cancel() end
  GG._ticker, GG._pace = nil, nil
end
setPace = function(sec)
  if GG._pace == sec and GG._ticker then return end
  if not T.NewTicker then return end
  if GG._ticker and GG._ticker.Cancel then GG._ticker:Cancel() end
  GG._pace = sec
  GG._ticker = T:NewTicker(sec, function() if GG:IsEnabled() then GG:Tick() end end, GG)
end
local function startTicker() if not GG._ticker then setPace(GG.TICK) end end
--- un événement utile (équipement, sacs, inspection, message d'un ami) : retour au rythme vif
local function wake() if GG._ticker and GG._pace ~= GG.TICK then setPace(GG.TICK) end end
GG.Wake = wake
function GG:OnRoster()
  local added = self:ScanRoster()
  if inGroup() and #order > 0 then
    startTicker()
    wake()
    if #added > 0 then
      S.hello, S.wantReq, S.wantSend = true, true, true
      S.gearDirty, S.bagsDirty = true, true
      for _, nm in ipairs(added) do local m = R[nm] if m then m.addonWait = now() end end
    end
  else
    stopTicker()
    S.pend, S.sent, S.sentAt = nil, {}, {}
  end
end

-- ===================================================================================== cycle de vie
function GG:OnEnable()
  hookWindow()
  hookItemInfo()
  hookTeam()
  hookTooltips()
  if T.RegisterComm then
    for _, kind in ipairs({ "GGE", "GGB", "GGQ" }) do
      T:RegisterComm(kind, GG, function(_, payload, sender) GG:OnComm(kind, payload, sender) end)
    end
  end
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function() GG:OnRoster() end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", function() GG:OnRoster() S.gearDirty, S.bagsDirty = true, true end)
  self:RegisterEvent("INSPECT_READY", function(_, _, guid) GG:OnInspect(guid) wake() end)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", function() S.gearDirty = true S.bagsDirty = true wake() end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() S.bagsDirty = true wake() end)
  self:RegisterEvent("UNIT_INVENTORY_CHANGED", function(_, _, u)
    for _, nm in ipairs(order) do
      local m = R[nm]
      if m and m.unit == u and m.src == "inspect" then m.changed = true wake() end
    end
  end)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function() S.pend = nil end)
  self:RegisterEvent("ENCOUNTER_START", function() S.encounter = true S.pend = nil end)
  self:RegisterEvent("ENCOUNTER_END", function() S.encounter = false end)
  self:RegisterEvent("START_LOOT_ROLL", function(_, _, rollID) GG:OnLootRoll(rollID) end)
  self:RegisterEvent("GET_ITEM_INFO_RECEIVED", function(_, _, id)
    id = num(id)
    if id and waiting[id] then waiting[id], waitN = nil, max(0, waitN - 1) S.itemsLoaded = true wake() end
  end)
  S.gearDirty, S.bagsDirty = true, true
  self:OnRoster()
end
function GG:OnDisable()
  stopTicker()
  if inGroup() and T.Send and #order > 0 and GG.db and GG.db.share ~= false then
    T:Send("GGE", "1;off", "PARTY")   -- module coupé : oubliez mon équipement
    T:Send("GGB", "1;", "PARTY")
  end
  S.pend, S.sent, S.sentAt, S.trades = nil, {}, {}, nil
  for _, m in pairs(R) do m.bags, m.bagsAt = {}, nil end
end
function GG:OnOptionChanged() S.trades = nil bump() end

-- tests
GG._test = { R = R, S = S, me = me, order = function() return order end, bags = function() return myBags end,
  reset = function()
    S.pend, S.lastNotify, S.sent, S.sentAt, S.lastReq, S.trades = nil, -1e9, {}, {}, -1e9, nil
    S.bagsDirty, S.gearDirty, S.bagsAt, S.tipCache = true, true, -1e9, {}
  end,
  hookWindow = hookWindow, hookItemInfo = hookItemInfo }
