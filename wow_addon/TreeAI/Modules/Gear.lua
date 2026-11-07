-- TreeAI — module Équipement (agent GEAR) : planificateur d'améliorations par emplacement, informatif seulement.
--  • Score de chaque pièce portée (poids de stats du module Boost : M.Weights / M.ScoreStats, aucune duplication).
--  • Meilleures améliorations réalistes « bientôt » (niveau requis ≤ niveau + horizon) parmi ~5 000 objets du
--    client (Data/Gear.lua) + objets de tes sacs + récompenses des quêtes de ton journal.
--  • OÙ les obtenir : sacs, récompense de quête (journal / Pro / Questie), boss de donjon (Questie npcDrops dans
--    une zone de donjon, ou module Dungeon s'il existe), monstre / coffre, marchand (Questie vendors + prix
--    d'achat client), artisanat (tables client SkillLineAbility/SpellEffect, ou module Prof), HV (Auction.Stats).
--  • « gain +X % », effort (minutes + or), liste de courses, onglet « Équipement » emplacement par emplacement.
--  • Chasseur : DPS / vitesse de l'arme à distance, munitions assorties (flèches / balles), mailles au niv 40.
-- Rien n'est équipé, acheté ou cliqué à la place du joueur. QuestieDB en LECTURE seule. Calculs hors combat,
-- par tranches (coroutine) ; Lua 5.1, aucune globale, toutes les API testées.
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local M = T:RegisterModule("Gear", {
  title = "Équipement",
  defaults = { enabled = true, tab = true, suggest = true, horizon = 3, minGain = 3, ah = true, quests = true,
               dungeons = true, crafted = true, vendors = true, maxPerSlot = 3 },
  charDefaults = { shopping = {} },
  options = {
    { key = "tab", type = "toggle", label = "Onglet « Équipement » (emplacement par emplacement)", section = "Équipement" },
    { key = "suggest", type = "toggle", label = "Signaler la meilleure amélioration dans « Maintenant »", section = "Équipement" },
    { key = "horizon", type = "range", label = "Objets jusqu'à niveau + N", min = 0, max = 10, step = 1, section = "Équipement" },
    { key = "minGain", type = "range", label = "Gain minimal affiché (%)", min = 1, max = 20, step = 1, section = "Équipement" },
    { key = "maxPerSlot", type = "range", label = "Alternatives par emplacement (infobulle)", min = 1, max = 6, step = 1, section = "Équipement" },
    { key = "quests", type = "toggle", label = "Sources : récompenses de quête", section = "Sources" },
    { key = "dungeons", type = "toggle", label = "Sources : boss de donjon / monstres", section = "Sources" },
    { key = "vendors", type = "toggle", label = "Sources : marchands", section = "Sources" },
    { key = "crafted", type = "toggle", label = "Sources : artisanat", section = "Sources" },
    { key = "ah", type = "toggle", label = "Sources : hôtel des ventes (prix)", section = "Sources" },
  },
})

local floor, min, max, abs = math.floor, math.min, math.max, math.abs
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function opt(k) return M.db and M.db[k] end
local function mod(name) local m = T.modules and T.modules[name] if m and (not m.IsEnabled or m:IsEnabled()) then return m end end
local function boost() local B = T.modules and T.modules.Boost return (B and B.ScoreStats) and B or nil end
local function level() return tonumber(try(g("UnitLevel"), "player")) or 1 end
local function itemInfo(x) if API.ItemInfo then return API.ItemInfo(x) end return try(g("GetItemInfo"), x) end
local function itemID(link)
  if type(link) == "number" then return link end
  return tonumber(type(link) == "string" and link:match("item:(%d+)") or nil)
end
local function money(c)
  c = floor(tonumber(c) or 0)
  local gg, s, k = floor(c / 10000), floor(c / 100) % 100, c % 100
  if gg > 0 then return s > 0 and ("%d po %d pa"):format(gg, s) or ("%d po"):format(gg) end
  if s > 0 then return k > 0 and ("%d pa %d pc"):format(s, k) or ("%d pa"):format(s) end
  return ("%d pc"):format(k)
end
local function pctText(p)
  if not p then return "?" end
  if p >= 999 then return "nouveau" end
  return ("+%d %%"):format(floor(p + 0.5))
end
local function dur(m) m = floor((tonumber(m) or 0) + 0.5) if m < 60 then return ("~%d min"):format(max(1, m)) end
  return ("~%d h %02d"):format(floor(m / 60), m % 60) end

---------------------------------------------------------------------------------------------------
-- Tables
---------------------------------------------------------------------------------------------------
-- Emplacements affichés (ordre de la fiche de personnage). 4 chemise / 19 tabard ignorés ; 0 = munitions à part.
local SLOT_LIST = { 1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18 }
local SLOT_FR = { [1] = "Tête", [2] = "Cou", [3] = "Épaules", [15] = "Dos", [5] = "Torse", [9] = "Poignets",
  [10] = "Mains", [6] = "Taille", [7] = "Jambes", [8] = "Pieds", [11] = "Doigt 1", [12] = "Doigt 2",
  [13] = "Bijou 1", [14] = "Bijou 2", [16] = "Main droite", [17] = "Main gauche", [18] = "Distance" }
-- InventoryType (client) -> nom INVTYPE (clé des emplacements du module Boost)
local INV = { [1] = "INVTYPE_HEAD", [2] = "INVTYPE_NECK", [3] = "INVTYPE_SHOULDER", [5] = "INVTYPE_CHEST",
  [6] = "INVTYPE_WAIST", [7] = "INVTYPE_LEGS", [8] = "INVTYPE_FEET", [9] = "INVTYPE_WRIST", [10] = "INVTYPE_HAND",
  [11] = "INVTYPE_FINGER", [12] = "INVTYPE_TRINKET", [13] = "INVTYPE_WEAPON", [14] = "INVTYPE_SHIELD",
  [15] = "INVTYPE_RANGED", [16] = "INVTYPE_CLOAK", [17] = "INVTYPE_2HWEAPON", [20] = "INVTYPE_ROBE",
  [21] = "INVTYPE_WEAPONMAINHAND", [22] = "INVTYPE_WEAPONOFFHAND", [23] = "INVTYPE_HOLDABLE",
  [25] = "INVTYPE_THROWN", [26] = "INVTYPE_RANGEDRIGHT", [28] = "INVTYPE_RELIC" }
local INV_SLOTS_DEFAULT = { INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_CHEST = { 5 },
  INVTYPE_ROBE = { 5 }, INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 }, INVTYPE_WRIST = { 9 },
  INVTYPE_HAND = { 10 }, INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 }, INVTYPE_CLOAK = { 15 },
  INVTYPE_WEAPON = { 16, 17 }, INVTYPE_2HWEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 },
  INVTYPE_WEAPONOFFHAND = { 17 }, INVTYPE_SHIELD = { 17 }, INVTYPE_HOLDABLE = { 17 }, INVTYPE_RANGED = { 18 },
  INVTYPE_RANGEDRIGHT = { 18 }, INVTYPE_THROWN = { 18 }, INVTYPE_RELIC = { 18 } }
local CLASS_BIT = { WARRIOR = 1, PALADIN = 2, HUNTER = 3, ROGUE = 4, PRIEST = 5, SHAMAN = 7, MAGE = 8, WARLOCK = 9, DRUID = 11 }
-- stat (id client) -> clé ITEM_MOD de Boost (S.*)
local STAT_ID = { [3] = "AGI", [4] = "STR", [5] = "INT", [6] = "SPI", [7] = "STA", [12] = "DEF", [31] = "HIT",
  [32] = "CRIT", [38] = "AP", [39] = "RAP", [41] = "HEAL", [42] = "SPD", [45] = "SP" }
local STAT_FR = { AGI = "Agilité", STR = "Force", INT = "Intelligence", SPI = "Esprit", STA = "Endurance",
  DEF = "Défense", HIT = "Toucher", CRIT = "Critique", AP = "Puissance d'attaque", RAP = "PA à distance",
  HEAL = "Soins", SPD = "Dégâts des sorts", SP = "Puissance des sorts", ARMOR = "Armure",
  RDPS = "DPS de l'arme à distance", MDPS = "DPS de l'arme de mêlée" }
local ARMOR_FR = { [1] = "tissu", [2] = "cuir", [3] = "mailles", [4] = "plaques", [6] = "bouclier" }
local WEAPON_FR = { [0] = "hache", [1] = "hache 2M", [2] = "arc", [3] = "fusil", [4] = "masse", [5] = "masse 2M",
  [6] = "arme d'hast", [7] = "épée", [8] = "épée 2M", [10] = "bâton", [13] = "arme de pugilat", [15] = "dague",
  [16] = "armes de jet", [18] = "arbalète", [19] = "baguette" }
-- arme à distance -> munitions (classID 6 : 2 flèches, 3 balles)
local AMMO_OF = { [2] = 2, [18] = 2, [3] = 3 }
local AMMO_FR = { [2] = "flèches", [3] = "balles" }
-- Zones de donjon (AreaTable) -> nom FR, niveaux conseillés
local DUNGEONS = {
  [2437] = { "Gouffre de Ragefeu", 13, 18 }, [718] = { "Cavernes des lamentations", 17, 24 },
  [1581] = { "Les Mortemines", 17, 26 }, [209] = { "Donjon d'Ombrecroc", 22, 30 },
  [719] = { "Profondeurs de Brassenoire", 24, 32 }, [717] = { "La Prison", 24, 32 },
  [721] = { "Gnomeregan", 29, 38 }, [491] = { "Kraal de Tranchebauge", 29, 38 },
  [796] = { "Monastère Écarlate", 34, 45 }, [722] = { "Souilles de Tranchebauge", 37, 46 },
  [1337] = { "Uldaman", 41, 51 }, [978] = { "Zul'Farrak", 44, 54 }, [2100] = { "Maraudon", 46, 55 },
  [1477] = { "Le temple d'Atal'Hakkar", 50, 56 }, [1584] = { "Profondeurs de Rochenoire", 52, 60 },
  [1583] = { "Pic de Rochenoire", 55, 60 }, [2557] = { "Hache-Tripes", 55, 60 }, [2017] = { "Stratholme", 58, 60 },
  [2057] = { "Scholomance", 58, 60 },
}
M.DUNGEONS, M.SLOT_FR, M.AMMO_OF = DUNGEONS, SLOT_FR, AMMO_OF

-- Priorités de stats expliquées (classe, arbre de talents le plus rempli ; « default » sinon)
local PRIORITY = {
  HUNTER = { default = "Agilité > DPS de l'arme à distance > Toucher / Critique > PA à distance > Endurance > Intelligence. "
    .. "Ton arc / fusil fait l'essentiel des dégâts : son DPS compte triple ; l'Agilité donne PA à distance + critique "
    .. "+ armure. Arme lente (≥ 2,8 s) = Visée plus forte. Mailles au niv 40 (cuir avant), la mêlée compte peu." },
  WARRIOR = { default = "Force > Toucher / Critique > Agilité > Endurance. Le DPS de l'arme prime : une grosse arme "
    .. "2M lente avant tout pour Frappe héroïque / Fulgurance.",
    [3] = "Endurance > Défense > Armure > Agilité / Force. Bouclier obligatoire ; la survie avant les dégâts." },
  ROGUE = { default = "Agilité > Toucher / Critique > DPS des armes > Force > Endurance. Arme principale lente, "
    .. "dague en main gauche si Attaque sournoise / Embuscade." },
  MAGE = { default = "Intelligence > Dégâts des sorts > Toucher / Critique > Endurance > Esprit. Plus de mana = moins "
    .. "de pauses ; l'Endurance évite les morts en solo." },
  WARLOCK = { default = "Dégâts des sorts > Endurance > Intelligence > Esprit. L'Endurance nourrit Connexion : elle vaut "
    .. "presque une stat principale pour toi." },
  PRIEST = { default = "Intelligence > Soins > Esprit > Endurance. L'Esprit règle la récupération de mana hors combat.",
    [3] = "Dégâts des sorts > Intelligence > Endurance > Esprit (Ombre)." },
  DRUID = { default = "Intelligence > Puissance des sorts > Esprit > Endurance, ou Force / Agilité en forme féline.",
    [1] = "Intelligence > Dégâts des sorts > Endurance > Esprit (Équilibre).",
    [2] = "Agilité / Force > Endurance > Toucher / Critique > Armure (Combat farouche).",
    [3] = "Intelligence > Soins > Esprit > Endurance (Restauration)." },
  PALADIN = { default = "Force > Endurance > Intelligence > Agilité. Une arme 2M lente pour Sceau d'autorité.",
    [1] = "Intelligence > Soins > Esprit > Endurance (Sacré).",
    [2] = "Endurance > Défense > Armure > Force (Protection)." },
  SHAMAN = { default = "Force > Intelligence > Endurance > Agilité. Arme 2M lente pour Arme Furie-des-vents ; bouclier + "
    .. "masse si tu soignes.",
    [1] = "Intelligence > Dégâts des sorts > Endurance (Élémentaire).",
    [2] = "Force > Agilité > Toucher / Critique > Endurance ; arme lente (Amélioration).",
    [3] = "Intelligence > Soins > Esprit > Endurance (Restauration)." },
}
M.PRIORITY = PRIORITY

---------------------------------------------------------------------------------------------------
-- Données client (Data/Gear.lua)
---------------------------------------------------------------------------------------------------
local function data() return T.Data and T.Data.Gear end
local function rec(id)
  local D = data()
  local r = D and D.items and D.items[id]
  if not r then return nil end
  return { id = id, inv = r[1], req = r[2], q = r[3], cls = r[4], sub = r[5], statStr = r[6], dps = r[7],
    speed = r[8], armor = r[9], bind = r[10], buy = r[11], name = r[12], mask = r[13] }
end
M.Rec = rec

local statCache = {}
--- Stats (clés ITEM_MOD de Boost) d'un objet du client, calculées comme l'infobulle.
function M.StatsOf(id)
  if statCache[id] then return statCache[id] end
  local r, B = rec(id), boost()
  if not r or not B then return nil end
  local S = B.STAT_KEYS
  local st = {}
  for sid, v in tostring(r.statStr or ""):gmatch("(%d+):(%-?%d+)") do
    local k = STAT_ID[tonumber(sid)]
    if k and S[k] then st[S[k]] = (st[S[k]] or 0) + tonumber(v) end
  end
  if (r.armor or 0) > 0 then st[S.ARMOR] = r.armor end
  if (r.dps or 0) > 0 then st[S.DPS] = r.dps end
  statCache[id] = st
  return st
end

local function slotsOf(invName)
  local B = boost()
  local t = B and B.EQUIP_SLOTS or INV_SLOTS_DEFAULT
  return t[invName]
end

---------------------------------------------------------------------------------------------------
-- Contexte joueur
---------------------------------------------------------------------------------------------------
function M.Ctx()
  local B = boost()
  local c = { class = T:PlayerClass(), level = level(), horizon = tonumber(opt("horizon")) or 3 }
  c.spec = B and B.SpecTab and try(B.SpecTab) or nil
  c.w = B and try(B.Weights) or {}
  c.money = (API.Money and try(API.Money)) or tonumber(try(g("GetMoney"))) or 0
  local P = mod("Predict")
  local r = P and P.Rates and try(P.Rates)
  local gph = type(r) == "table" and tonumber(r.goldPerHour) or nil
  -- valeur du temps : or/h mesuré (en pc) sinon ~niveau² × 150 pc/h (≈ 10 po/h au niv 26)
  c.copperPerMin = max(50, (gph and gph > 0 and gph or c.level * c.level * 150) / 60)
  return c
end

--- Arme / armure utilisable par la classe (maintenant, ou dans l'horizon) -> ok, niveau dès lequel, raison
function M.Usable(r, c)
  local B = boost()
  if not r or not B then return false end
  if r.mask and r.mask > 0 then
    local bit = CLASS_BIT[c.class]
    if bit and floor(r.mask / 2 ^ (bit - 1)) % 2 == 0 then return false, nil, "réservé à d'autres classes" end
  end
  local need = r.req or 0
  if r.cls == 4 and r.sub and r.sub > 0 and r.inv ~= 16 then
    local lv = (B.ARMOR_LEVEL[c.class] or {})[r.sub]
    if not lv then return false, nil, (ARMOR_FR[r.sub] or "armure") .. " non portable" end
    need = max(need, lv)
  elseif r.cls == 2 then
    if not (B.WEAPON_TYPES[c.class] or {})[r.sub] then return false, nil, "arme non utilisable" end
  end
  if need > c.level + c.horizon then return false, need, "trop haut" end
  return true, need
end

---------------------------------------------------------------------------------------------------
-- Équipement porté
---------------------------------------------------------------------------------------------------
local function equippedLink(slot) return try(g("GetInventoryItemLink"), "player", slot) end
local function equippedID(slot)
  local id = try(g("GetInventoryItemID"), "player", slot)
  return id or itemID(equippedLink(slot))
end

--- Score d'un objet pour un emplacement : données client si connues (cohérent avec les candidats), sinon live.
function M.ScoreItem(idOrLink, slot, c)
  local B = boost()
  if not B or not idOrLink then return nil end
  local id = itemID(idOrLink)
  local st = id and M.StatsOf(id)
  if st then return B.ScoreStats(st, slot, c and c.w) end
  if type(idOrLink) == "string" then return B.ScoreLink(idOrLink, slot, c and c.w) end
end

function M.Current(c)
  local cur = {}
  for _, slot in ipairs(SLOT_LIST) do
    local link, id = equippedLink(slot), equippedID(slot)
    local name = link and (link:match("%[(.-)%]")) or (id and rec(id) and rec(id).name) or nil
    local s = (link or id) and (M.ScoreItem(link or id, slot, c) or 0) or 0
    local r = id and rec(id)
    if slot == 18 and c.class == "HUNTER" and r and (r.speed or 0) > 0 then
      s = s * (1 + max(-0.15, min(0.15, (r.speed - 2.6) * 0.15)))
    end
    local _, _, _, _, _, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link or id or 0)
    cur[slot] = { id = id, link = link, name = name, score = s, inv = r and r.inv, equipLoc = equipLoc,
      cls = (r and r.cls) or classID, sub = (r and r.sub) or subID, dps = r and r.dps, speed = r and r.speed }
  end
  local mh = cur[16]
  c.twoHand = mh and (mh.equipLoc == "INVTYPE_2HWEAPON" or mh.inv == 17) or false
  return cur
end

--- Score à battre pour un candidat dans un emplacement (2M = main droite + main gauche).
local function oldScore(cur, slot, invName)
  if invName == "INVTYPE_2HWEAPON" then return (cur[16] and cur[16].score or 0) + (cur[17] and cur[17].score or 0) end
  return cur[slot] and cur[slot].score or 0
end
local function gainPct(new, old)
  if not new then return nil end
  if old <= 0 then return new > 0 and 999 or 0 end
  return (new - old) / old * 100
end
M.GainPct = gainPct

---------------------------------------------------------------------------------------------------
-- Sources (où l'obtenir) — Questie lecture seule
---------------------------------------------------------------------------------------------------
local function questie()
  local C = T.modules and T.modules.Compass
  if C and C.Questie then return (try(C.Questie)) end
end
local function areaName(area)
  if not area then return nil end
  if DUNGEONS[area] then return DUNGEONS[area][1] end
  local CM = g("C_Map")
  local n = CM and CM.GetAreaInfo and try(CM.GetAreaInfo, area)
  return n
end
local function questDone(qid)
  local CQ = g("C_QuestLog")
  local f = (CQ and CQ.IsQuestFlaggedCompleted) or g("IsQuestFlaggedCompleted")
  return f and try(f, qid) and true or false
end
local function list(v) return type(v) == "table" and v or {} end

-- métiers d'artisanat connus : SkillLine -> rang
local craftCache, craftAt = {}, -100
local function craftSkills()
  local t = (g("GetTime") and g("GetTime")()) or 0
  if t - craftAt < 10 then return craftCache end
  craftAt, craftCache = t, {}
  local D = data()
  local byName = {}
  for line, name in pairs(D and D.profs or {}) do byName[name] = line end
  local GP, GPI = g("GetProfessions"), g("GetProfessionInfo")
  if GP and GPI then
    local l = { try(GP) }
    for i = 1, 5 do
      if l[i] then
        local name, _, rank, _, _, _, line = try(GPI, l[i])
        line = line or (name and byName[name])
        if line then craftCache[line] = rank or 0 end
      end
    end
  end
  local N, I = g("GetNumSkillLines"), g("GetSkillLineInfo")
  if N and I then
    for i = 1, (try(N) or 0) do
      local name, header, _, rank = try(I, i)
      if name and not header and byName[name] then craftCache[byName[name]] = rank or 0 end
    end
  end
  return craftCache
end
M.CraftSkills = craftSkills

-- récompenses des quêtes du journal (Pro.char.rewards + API par questID) : itemID -> { qid, title, link }
local logRewards = {}
function M.ScanLogRewards()
  logRewards = {}
  local P = T.modules and T.modules.Pro
  local CQ = g("C_QuestLog")
  local function title(qid)
    local tt = CQ and CQ.GetTitleForQuestID and try(CQ.GetTitleForQuestID, qid)
    if not tt then local db = questie() tt = db and db.QueryQuestSingle and try(db.QueryQuestSingle, qid, "name") end
    return tt or ("quête " .. tostring(qid))
  end
  local function add(qid, link)
    local id = itemID(link)
    if id and not logRewards[id] then logRewards[id] = { qid = qid, title = title(qid), link = link } end
  end
  if P and P.char and type(P.char.rewards) == "table" then
    for qid, links in pairs(P.char.rewards) do for _, l in ipairs(list(links)) do add(qid, l) end end
  end
  local NC, IL = g("GetNumQuestLogChoices"), g("GetQuestLogItemLink")
  if CQ and CQ.GetNumQuestLogEntries and CQ.GetQuestIDForLogIndex and NC and IL then
    for i = 1, min(40, tonumber((try(CQ.GetNumQuestLogEntries))) or 0) do
      local qid = try(CQ.GetQuestIDForLogIndex, i)
      if qid and qid > 0 then
        for k = 1, min(6, tonumber(try(NC, qid, true)) or 0) do
          local l = try(IL, "choice", k, qid)
          if l then add(qid, l) end
        end
      end
    end
  end
  return logRewards
end

local bagItems = {}   -- itemID -> link (objets équipables dans les sacs)
local function scanBags()
  bagItems = {}
  if not API.IterBags then return bagItems end
  for _, _, info in API.IterBags() do
    local id = info and (info.itemID or info.id)
    local link = info and (info.hyperlink or info.link)
    if id and not link then link = id end
    if id then
      local _, _, _, _, _, _, _, _, equipLoc = itemInfo(link)
      if equipLoc and equipLoc ~= "" and slotsOf(equipLoc) then bagItems[id] = link end
    end
  end
  return bagItems
end

local srcCache = {}
--- Sources d'un objet : { {kind, text, min, cost}, ... } triées par effort croissant.
function M.Sources(id, c)
  c = c or M.Ctx()
  local key = id .. ":" .. c.level
  if srcCache[key] then return srcCache[key] end
  local out, r = {}, rec(id)
  local function add(kind, text, mins, cost, extra)
    local e = { kind = kind, text = text, min = mins or 30, cost = cost or 0 }
    if extra then for k, v in pairs(extra) do e[k] = v end end
    e.effort = e.min + (e.cost / c.copperPerMin)
    out[#out + 1] = e
  end
  if bagItems[id] then add("bag", "dans tes sacs : équipe-le", 0, 0) end
  local lr = logRewards[id]
  if lr and opt("quests") then add("quest", "récompense de « " .. lr.title .. " » (dans ton journal)", 10, 0, { qid = lr.qid }) end
  -- modules optionnels (contrats lus seulement s'ils existent)
  local Dg = mod("Dungeon")
  if Dg and Dg.ItemSource and opt("dungeons") then
    local s = try(Dg.ItemSource, id)
    if type(s) == "table" and s.text then add("boss", s.text, tonumber(s.min) or 60, 0) end
  end
  local Pf = mod("Prof")
  if Pf and Pf.CraftInfo and opt("crafted") then
    local s = try(Pf.CraftInfo, id)
    if type(s) == "table" and s.text then add("craft", s.text, tonumber(s.min) or 15, tonumber(s.cost) or 0) end
  end
  local db = questie()
  local qi = db and db.QueryItemSingle
  if qi then
    if opt("quests") then
      for _, qid in ipairs(list(try(qi, id, "questRewards"))) do
        if not (lr and lr.qid == qid) and not questDone(qid) then
          local ql = tonumber(db.QueryQuestSingle and try(db.QueryQuestSingle, qid, "requiredLevel")) or 1
          if ql <= c.level + c.horizon then
            local name = db.QueryQuestSingle and try(db.QueryQuestSingle, qid, "name") or ("quête " .. qid)
            add("quest", ("récompense de « %s » (niv %d)"):format(name, ql), 30 + max(0, ql - c.level) * 20, 0, { qid = qid })
          end
          break
        end
      end
    end
    local qn = db.QueryNPCSingle
    local drops = list(try(qi, id, "npcDrops"))
    if opt("dungeons") and #drops > 0 and #drops <= 6 and qn then
      for _, npc in ipairs(drops) do
        local name = try(qn, npc, "name") or "?"
        local zone = tonumber(try(qn, npc, "zoneID"))
        local rank = tonumber(try(qn, npc, "rank")) or 0
        local nl = tonumber(try(qn, npc, "maxLevel")) or tonumber(try(qn, npc, "minLevel")) or c.level
        local d = zone and DUNGEONS[zone]
        if d then
          local far = max(0, d[2] - c.level)
          add("boss", ("%s — %s%s"):format(d[1], name, rank == 3 and " (boss)" or ""), 60 + far * 15, 0,
            { dungeon = d[1], dmin = d[2], dmax = d[3] })
        else
          local z = areaName(zone)
          local tag = (rank == 4 or rank == 2) and " (rare)" or (rank == 1 and " (élite)" or "")
          add("drop", ("butin de %s%s niv %d%s"):format(name, tag, nl, z and (" — " .. z) or ""),
            (rank == 4 or rank == 2) and 45 or 25, 0)
        end
        if #out >= 6 then break end
      end
    end
    local objs = list(try(qi, id, "objectDrops"))
    if opt("dungeons") and #objs > 0 and #objs <= 4 then add("drop", "dans un coffre / objet au sol (Questie)", 25, 0) end
    if opt("vendors") and qn then
      local vend = list(try(qi, id, "vendors"))
      if #vend > 0 then
        local npc = vend[1]
        local name = try(qn, npc, "name") or "un marchand"
        local z = areaName(tonumber(try(qn, npc, "zoneID")))
        add("vendor", ("marchand %s%s%s"):format(name, z and (" — " .. z) or "", #vend > 1 and (" (+" .. (#vend - 1) .. ")") or ""),
          10, r and r.buy or 0)
      end
    end
  end
  -- artisanat (tables client)
  local D = data()
  local cr = D and D.crafted and D.crafted[id]
  if cr and opt("crafted") and not (Pf and Pf.CraftInfo) then
    local pname = (D.profs and D.profs[cr[1]]) or "artisanat"
    local have = craftSkills()[cr[1]]
    if have and have >= cr[2] then add("craft", ("à fabriquer : %s ~%d (tu as %d)"):format(pname, cr[2], have), 15, 0)
    elseif have then add("craft", ("%s ~%d requis (tu as %d)"):format(pname, cr[2], have), 15 + (cr[2] - have), 0)
    else add("craft", ("fabriqué par un artisan (%s ~%d) — ami / canal commerce"):format(pname, cr[2]), 25, 0) end
  end
  -- hôtel des ventes (jamais pour un objet lié quand ramassé)
  local A = mod("Auction")
  if opt("ah") and A and A.Stats and not (r and r.bind == 1) then
    local st = try(A.Stats, id)
    local p = type(st) == "table" and tonumber(st.market) or nil
    if p and p > 0 then add("ah", "HV ≈ " .. money(p) .. (st.src and (" (" .. st.src .. ")") or ""), 5, p, { price = p }) end
  end
  if #out == 0 then
    if r and r.bind ~= 1 then add("world", "butin aléatoire (monde) — surveille l'HV", 120, 0)
    else add("world", "source inconnue (butin rare)", 180, 0) end
  end
  table.sort(out, function(a, b) return a.effort < b.effort end)
  srcCache[key] = out
  return out
end

---------------------------------------------------------------------------------------------------
-- Planificateur
---------------------------------------------------------------------------------------------------
-- prépare un objet (données client ou lien des sacs / du journal) : utilisable ? quels emplacements ?
local function prepare(id, link, c)
  local r = rec(id)
  local invName
  if r then
    invName = INV[r.inv]
    local ok, need = M.Usable(r, c)
    if not ok then return nil end
    r.need = need
    if link then r.link = link end
  else
    if not link then return nil end
    local _, _, _, _, minLevel, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link)
    invName = equipLoc
    local B = boost()
    if not (B and B.CanUse and try(B.CanUse, link)) then return nil end
    r = { id = id, link = link, name = type(link) == "string" and link:match("%[(.-)%]") or tostring(id),
      req = minLevel or 0, cls = classID, sub = subID, need = minLevel or 0 }
  end
  r.invName = invName
  r.slots = invName and slotsOf(invName)
  if not r.slots then return nil end
  return r
end

local function candidateFor(r, slot, c, cur)
  local invName = r.invName
  if slot == 17 and (c.twoHand or invName == "INVTYPE_WEAPON") then
    local B = boost()
    local d = B and B.DUAL_LEVEL and B.DUAL_LEVEL[c.class]
    if c.twoHand or not (d and c.level >= d) then return nil end
  end
  if cur[slot] and cur[slot].id == r.id then return nil end
  local new = M.ScoreItem(M.StatsOf(r.id) and r.id or r.link, slot, c)
  -- chasseur : arme à distance lente = Visée plus forte (±5 % par 0,1 s… plafonné à ±15 %)
  if new and slot == 18 and c.class == "HUNTER" and (r.speed or 0) > 0 then
    new = new * (1 + max(-0.15, min(0.15, (r.speed - 2.6) * 0.15)))
  end
  local pct = gainPct(new, oldScore(cur, slot, invName))
  if not pct or pct < (tonumber(opt("minGain")) or 3) then return nil end
  return { id = r.id, link = r.link, name = r.name, req = r.req, need = r.need, pct = pct, score = new, inv = r.inv,
    invName = invName, cls = r.cls, sub = r.sub, dps = r.dps, speed = r.speed, bind = r.bind }
end

-- valeur réaliste : gain ÷ racine de l'effort (minutes équivalentes), malus si niveau pas encore atteint
local function value(cand, src, c)
  local wait = max(0, (cand.need or 0) - c.level) * 45
  return min(cand.pct, 300) / math.sqrt(10 + src.effort + wait)
end

local function pickBest(cands, c, n)
  for _, cd in ipairs(cands) do
    local srcs = M.Sources(cd.id, c)
    cd.src, cd.sources = srcs[1], srcs
    cd.value = value(cd, srcs[1], c)
  end
  table.sort(cands, function(a, b) if a.value ~= b.value then return a.value > b.value end return a.id < b.id end)
  while #cands > n do table.remove(cands) end
  return cands
end

local function planSlots(c, cur, ids, yieldEvery)
  local per = {}
  for _, slot in ipairs(SLOT_LIST) do per[slot] = {} end
  for i, e in ipairs(ids) do
    if yieldEvery and i % yieldEvery == 0 and coroutine.running() then coroutine.yield() end
    local r = prepare(e.id, e.link, c)
    for _, slot in ipairs(r and r.slots or {}) do
      local cd = per[slot] and candidateFor(r, slot, c, cur)
      if cd then local l = per[slot] l[#l + 1] = cd end
    end
  end
  return per
end

--- Calcul complet (synchrone). Le module l'appelle par tranches via une coroutine (yield toutes les N lignes).
function M.Plan(yieldEvery)
  local c = M.Ctx()
  if not boost() then return nil, "module Boost absent" end
  craftAt = -100
  scanBags()
  M.ScanLogRewards()
  srcCache = {}
  local cur = M.Current(c)
  local ids, seen, n = {}, {}, 0
  local function push(id, link) if id and not seen[id] then seen[id] = true ids[#ids + 1] = { id = id, link = link } end end
  for id, link in pairs(bagItems) do push(id, link) end
  for id, e in pairs(logRewards) do push(id, e.link) end
  local D = data()
  local lo, hi = c.level - 12, c.level + c.horizon
  for id, r in pairs(D and D.items or {}) do
    n = n + 1
    if yieldEvery and n % yieldEvery == 0 and coroutine.running() then coroutine.yield() end
    local req = r[2] or 0
    if req >= lo and req <= hi then push(id, nil) end
  end
  table.sort(ids, function(a, b) return a.id < b.id end)
  local per = planSlots(c, cur, ids, yieldEvery and floor(yieldEvery / 4))
  if yieldEvery and coroutine.running() then coroutine.yield() end
  -- ne garder que les 10 plus gros gains par emplacement avant de chercher les sources (coût Questie borné)
  local plan = { ctx = c, cur = cur, slots = {}, time = (g("GetTime") and g("GetTime")()) or 0 }
  local used = {}
  for _, slot in ipairs(SLOT_LIST) do
    local l = per[slot]
    table.sort(l, function(a, b) if a.pct ~= b.pct then return a.pct > b.pct end return a.id < b.id end)
    while #l > 10 do table.remove(l) end
    local best = pickBest(l, c, max(1, tonumber(opt("maxPerSlot")) or 3) + 1)
    -- anneaux / bijoux : ne pas proposer le même objet (unique) pour les deux emplacements
    if (slot == 12 or slot == 14) and best[1] and used[best[1].id] then table.remove(best, 1) end
    if best[1] then used[best[1].id] = true end
    while #best > (tonumber(opt("maxPerSlot")) or 3) do table.remove(best) end
    plan.slots[slot] = best
    if yieldEvery and coroutine.running() then coroutine.yield() end
  end
  plan.shopping = M.Shopping(plan)
  plan.hunter = M.HunterNotes(plan)
  return plan
end

function M.Shopping(plan)
  local out, total = {}, 0
  for _, slot in ipairs(SLOT_LIST) do
    local cd = plan.slots[slot] and plan.slots[slot][1]
    local s = cd and cd.src
    if s and (s.kind == "vendor" or s.kind == "ah") and (cd.need or 0) <= plan.ctx.level + 1 then
      out[#out + 1] = { slot = slot, id = cd.id, name = cd.name, pct = cd.pct, cost = s.cost, kind = s.kind, where = s.text,
        perGold = cd.pct / max(1, s.cost / 10000) }
    end
  end
  table.sort(out, function(a, b) return a.perGold > b.perGold end)
  local budget = plan.ctx.money
  for _, e in ipairs(out) do
    total = total + e.cost
    e.affordable = total <= budget
  end
  out.total = total
  return out
end

-- Chasseur : arme à distance (DPS, vitesse), munitions assorties, mailles au niv 40
function M.HunterNotes(plan)
  local c, cur = plan.ctx, plan.cur
  if c.class ~= "HUNTER" then return nil end
  local h = {}
  local r = cur[18]
  if r and r.id then
    local sub = r.sub
    h.ranged = { name = r.name, dps = r.dps, speed = r.speed, sub = sub, kind = WEAPON_FR[sub or -1] }
    local need = AMMO_OF[sub or -1]
    if need then
      h.ammoNeed = need
      local ammoID = try(g("GetInventoryItemID"), "player", 0)
      local have = {}
      if API.IterBags then
        for _, _, info in API.IterBags() do
          local id = info and (info.itemID or info.id)
          local _, _, _, _, _, _, _, _, _, _, _, classID, subID = itemInfo(id or 0)
          if classID == 6 and subID then have[subID] = (have[subID] or 0) + (info.stackCount or info.count or 1) end
        end
      end
      if ammoID then
        local _, _, _, _, _, _, _, _, _, _, _, classID, subID = itemInfo(ammoID)
        if classID == 6 and subID then h.ammoEquipped = subID end
      end
      h.ammoBags = have
      h.ammoOK = (h.ammoEquipped == need) or ((have[need] or 0) > 0)
    end
  else
    h.ranged = nil
  end
  local nxt = plan.slots[18] and plan.slots[18][1]
  if nxt then
    local nr = rec(nxt.id)
    local na = nr and AMMO_OF[nr.sub or -1]
    if na and h.ammoNeed and na ~= h.ammoNeed then
      h.ammoSwitch = ("%s → %s"):format(AMMO_FR[h.ammoNeed], AMMO_FR[na])
    end
  end
  h.mail = c.level < 40 and ("Niv 40 : apprends Mailles chez ton maître (encore %d niv) — les pièces en mailles"
    .. " deviennent des améliorations"):format(40 - c.level) or nil
  if c.level >= 40 then
    local leather = 0
    for _, s in ipairs({ 1, 3, 5, 6, 7, 8, 9, 10 }) do
      local e = cur[s]
      if e and e.cls == 4 and e.sub == 2 then leather = leather + 1 end
    end
    if leather > 0 then h.mail = ("Encore %d pièce(s) en cuir : vise les mailles (plus d'armure, mêmes stats)"):format(leather) end
  end
  return h
end

---------------------------------------------------------------------------------------------------
-- Affichage
---------------------------------------------------------------------------------------------------
local function priorityText(c)
  local p = PRIORITY[c.class] or PRIORITY.WARRIOR
  return (c.spec and p[c.spec]) or p.default
end
M.PriorityText = priorityText

local function weaponTxt(cd)
  if cd.dps and cd.dps > 0 then return (((" (%.1f DPS, %.1f s)"):format(cd.dps, cd.speed or 0):gsub("%.", ","))) end
  return ""
end

local function selectTab() if T.SelectTab then pcall(T.SelectTab, T, "gear") end if T.ShowPanel then pcall(T.ShowPanel, T) end end

M.expanded = {}
local function republish() if M.plan then M:Publish(M.plan) end end
local function rescan() T:UserAction(function() M.MarkDirty() M:ScanNow() end) end
local function bumpHorizon()
  local h = (tonumber(opt("horizon")) or 3) + 2
  if h > 10 then h = 0 end
  if M.db then M.db.horizon = h end
  rescan()
end

function M.Lines(plan)
  local lines = {}
  local c = plan.ctx
  local function add(text, prio, tip, click) lines[#lines + 1] = { text = text, prio = prio or 1, tooltip = tip, onClick = click } end
  add(("↻ Recalculer · objets jusqu'au niv %d (clic ici) · horizon +%d (clic sur la ligne suivante)"):format(c.level + c.horizon, c.horizon),
    1, { "Recalcule tout de suite (hors combat).", "Sinon : automatique à chaque changement d'équipement / de sac / de niveau." }, rescan)
  add(("Horizon : niveau +%d — clic : +2 (0 à 10)"):format(c.horizon), 1, "Montre aussi les objets que tu pourras porter bientôt.", bumpHorizon)
  add("Priorité des stats : " .. priorityText(c), 1, { "Poids utilisés (points de score par point de stat) : module Boost.",
    "Spé détectée = arbre de talents le plus rempli." })
  local h = plan.hunter
  if h then
    if h.ranged then
      local t = ("Arme à distance : %s%s"):format(h.ranged.name or "?", weaponTxt(h.ranged))
      if h.ranged.speed and h.ranged.speed > 0 and h.ranged.speed < 2.6 then t = t .. " — rapide : vise ≥ 2,8 s pour Visée" end
      add(t, 1)
    else add("Aucune arme à distance : c'est ta vraie arme, priorité absolue !", 3) end
    if h.ammoNeed and not h.ammoOK then add(("Munitions : ton arme tire des %s et tu n'en as pas !"):format(AMMO_FR[h.ammoNeed]), 3)
    elseif h.ammoNeed then add(("Munitions : %s (assorties à ton arme)"):format(AMMO_FR[h.ammoNeed]), 1) end
    if h.ammoSwitch then add("Attention : la prochaine arme change de munitions (" .. h.ammoSwitch .. ")", 2) end
    if h.mail then add(h.mail, c.level >= 38 and 2 or 1) end
  end
  local nUp = 0
  for _, slot in ipairs(SLOT_LIST) do
    local e = plan.cur[slot] or {}
    local best = plan.slots[slot] or {}
    local cd = best[1]
    local head = e.name and ("%s : %s (%d)"):format(SLOT_FR[slot] or slot, e.name, floor((e.score or 0) + 0.5))
      or ((SLOT_FR[slot] or slot) .. " : vide")
    if cd then
      nUp = nUp + 1
      local s = cd.src or {}
      local lvl = (cd.need or 0) > c.level and (" — niv " .. cd.need) or ""
      local cost = ((s.cost or 0) > 0 and s.kind ~= "ah") and (" · " .. money(s.cost)) or ""
      local text = ("%s → %s %s%s · %s%s · %s"):format(head, cd.name or "?", pctText(cd.pct), weaponTxt(cd), s.text or "?",
        cost, s.kind == "bag" and "maintenant" or dur(s.min))
      local tip = { "Gain " .. pctText(cd.pct) .. lvl, "Où : " .. (s.text or "?") }
      for i = 2, #(cd.sources or {}) do tip[#tip + 1] = "  ou " .. cd.sources[i].text end
      for i = 2, #best do
        local o = best[i]
        tip[#tip + 1] = ("Alternative : %s %s — %s"):format(o.name or "?", pctText(o.pct), o.src and o.src.text or "?")
      end
      local prio = (s.kind == "bag") and 3 or ((cd.pct >= 25 and (cd.need or 0) <= c.level) and 2 or 1)
      local open = M.expanded[slot]
      tip[#tip + 1] = open and "Clic : replier" or "Clic : voir toutes les sources et alternatives"
      add((open and "▼ " or "► ") .. text, prio, tip, function() M.expanded[slot] = not M.expanded[slot] republish() end)
      if open then
        for i = 2, #(cd.sources or {}) do add(("      ou %s · %s"):format(cd.sources[i].text, dur(cd.sources[i].min)), 1) end
        for i = 2, #best do
          local o = best[i]
          add(("      alternative : %s %s — %s"):format(o.name or "?", pctText(o.pct), o.src and o.src.text or "?"), 1)
        end
      end
    else
      add(head .. " — rien de mieux à portée", 1)
    end
  end
  local sh = plan.shopping or {}
  if #sh > 0 then
    add(("Liste de courses : %d objet(s), %s au total (tu as %s)"):format(#sh, money(sh.total or 0), money(c.money)), 2)
    for _, e in ipairs(sh) do
      add(("  %s %s — %s %s%s"):format(e.affordable and "•" or "×", e.name or "?", pctText(e.pct), e.where or "",
        e.affordable and "" or " (pas assez d'or)"), e.affordable and 1 or 1)
    end
  end
  if nUp == 0 then add("Équipement au top pour ton niveau : rien à chercher pour l'instant.", 1) end
  return lines
end

function M.Summary(plan)
  local out = { level = plan.ctx.level, spec = plan.ctx.spec, priority = priorityText(plan.ctx), slots = {}, shopping = {} }
  for _, slot in ipairs(SLOT_LIST) do
    local e, cd = plan.cur[slot] or {}, (plan.slots[slot] or {})[1]
    local row = { slot = SLOT_FR[slot], item = e.name, score = floor((e.score or 0) * 10 + 0.5) / 10 }
    if cd then
      row.next = { id = cd.id, name = cd.name, gain = floor(min(cd.pct, 999) + 0.5), need = cd.need,
        kind = cd.src and cd.src.kind, where = cd.src and cd.src.text, cost = cd.src and cd.src.cost,
        min = cd.src and floor(cd.src.min + 0.5) }
    end
    out.slots[#out.slots + 1] = row
  end
  for _, e in ipairs(plan.shopping or {}) do
    out.shopping[#out.shopping + 1] = { name = e.name, cost = e.cost, gain = floor(min(e.pct, 999) + 0.5), where = e.where }
  end
  out.total = plan.shopping and plan.shopping.total or 0
  if plan.hunter then
    out.hunter = { ranged = plan.hunter.ranged and plan.hunter.ranged.name, dps = plan.hunter.ranged and plan.hunter.ranged.dps,
      speed = plan.hunter.ranged and plan.hunter.ranged.speed, ammo = plan.hunter.ammoNeed and AMMO_FR[plan.hunter.ammoNeed],
      ammoOK = plan.hunter.ammoOK, mail = plan.hunter.mail }
  end
  return out
end

function M:Publish(plan)
  self.plan = plan
  if not plan then return end
  if opt("tab") and T.SetTabLines then pcall(T.SetTabLines, T, "gear", M.Lines(plan)) end
  local s = T.Summary and T:Summary()
  if type(s) == "table" then s.gear = M.Summary(plan) end
  self.char.shopping = {}
  for _, e in ipairs(plan.shopping or {}) do self.char.shopping[#self.char.shopping + 1] = { id = e.id, cost = e.cost } end
  if not opt("suggest") then self:Clear("upgrade") return end
  -- meilleure amélioration immédiate : sacs d'abord, sinon la plus rentable portable maintenant
  local top
  for _, slot in ipairs(SLOT_LIST) do
    local cd = (plan.slots[slot] or {})[1]
    if cd and (cd.need or 0) <= plan.ctx.level and cd.src and (not top or cd.value > top.value or cd.src.kind == "bag") then
      if not (top and top.src.kind == "bag" and cd.src.kind ~= "bag") then top = cd top.slot = slot end
    end
  end
  if top and top.name and (top.src.kind == "bag" or top.pct >= 10) then
    self:Suggest("upgrade", ("Équipe %s %s (%s) — %s"):format(top.name, pctText(top.pct), SLOT_FR[top.slot] or "?",
      top.src.text or ""), top.src.kind == "bag" and 2 or 1, { ttl = 600, cooldown = 300, onClick = selectTab })
  else
    self:Clear("upgrade")
  end
end

---------------------------------------------------------------------------------------------------
-- Exécution (hors combat, par tranches)
---------------------------------------------------------------------------------------------------
local job, dirty, lastRun = nil, true, -1000
function M:ScanNow()
  local ok, plan = pcall(M.Plan)
  if ok and plan then self:Publish(plan) return plan end
  if not ok then self:Debug("plan:", plan) end
end

local function step()
  if not job then return end
  if T:InCombat() then return end
  local ok, res = coroutine.resume(job)
  if not ok then M:Debug("plan:", res) job = nil return end
  if coroutine.status(job) == "dead" then
    job = nil
    if res then M:Publish(res) end
  end
end

function M:Start()
  if job or T:InCombat() then return end
  dirty, lastRun = false, (g("GetTime") and g("GetTime")()) or 0
  job = coroutine.create(function() return M.Plan(400) end)
  step()
end

function M:AutoRefresh()
  if T:InCombat() then return end
  if job then step() return end
  local t = (g("GetTime") and g("GetTime")()) or 0
  if dirty and t - lastRun >= 5 or t - lastRun >= 120 then self:Start() end
end

local function markDirty() dirty = true srcCache = {} craftAt = -100 end
M.MarkDirty = markDirty

function M:OnEnable()
  if T.RegisterTab then pcall(T.RegisterTab, T, "gear", "Équipement", 45) end
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", function() markDirty() end)
  self:RegisterEvent("PLAYER_LEVEL_UP", function() markDirty() statCache = {} end)
  self:RegisterEvent("BAG_UPDATE_DELAYED", function() markDirty() end)
  self:RegisterEvent("QUEST_ACCEPTED", function() markDirty() end)
  self:RegisterEvent("CHARACTER_POINTS_CHANGED", function() markDirty() end)
  self:On("COMBAT_END", function() if job then T:After(1, step) end end)
  self:On("SUMMARY", function(m) if m.plan then local s = T:Summary() if type(s) == "table" then s.gear = M.Summary(m.plan) end end end)
  if self._ticker then self._ticker:Cancel() end
  self._ticker = T:NewTicker(0.1, function() if job then step() end end)
  T:After(8, function() M:AutoRefresh() end)
end

function M:OnDisable()
  if self._ticker then self._ticker:Cancel() self._ticker = nil end
  job = nil
  self:Clear("upgrade")
end

if T.RegisterSlash then
  T:RegisterSlash("equipement", function()
    T:UserAction(function() markDirty() M:ScanNow() selectTab() end)
  end, "equipement : améliorations par emplacement (onglet « Équipement »)")
end
