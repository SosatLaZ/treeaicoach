-- TreeAI — module Équipement (agent GEAR ; 5.0 : « un vrai assistant pour l'armure »). Informatif : rien n'est
-- acheté, choisi ni jeté à ta place ; « équiper » = ton clic, hors combat.
--  • Gear.lua (ce fichier) : tables (armures / armes par classe, emplacements), plan par emplacement parmi ~5 000
--    objets du client (Data/Gear.lua) + tes sacs + les récompenses des quêtes du journal, et OÙ les obtenir (sacs,
--    récompense, donjon, monstre, marchand, artisanat, HV), récompense de quête entourée, infobulle de repli.
--  • GearSpec.lua : TA spé (talents lus par le jeu → sorts de talent connus → ton choix) : T.Gear.Spec().
--  • GearValue.lua : la valeur d'un objet en UN nombre (« ≈ +3,2 DPS » / « +12 % ») : T.Gear.Score(lien).
--  • GearRent.lua : « Stuff rentable » (Équipe / Achète / Fais la quête / Attends le niveau N / Garde), budget et
--    réserve monture, flèches dans les sacs, garde-fou de vente, page « Équipement » : T.Gear.BestNow().
-- Coût : le plan complet ne tourne que si l'équipement, le niveau, la spé ou le journal changent (jamais sur un
-- simple changement de sac : les sacs sont lus en direct, verdict en cache par lien) ; par tranches (coroutine), hors
-- combat ; zéro travail au repos. QuestieDB en LECTURE seule. Lua 5.1, aucune globale, toutes les API testées.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

-- (5.0) demandé par le joueur (« fais un vrai assistant pour l'armure ») : allumé en mode sûr, une fois, sauf s'il
-- l'a coupé lui-même (même mécanique que Prof / CityRun / Group en 4.1)
if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.Gear = true end
if type(T.SAFE_ADDED) == "table" then T.SAFE_ADDED.Gear = 5 end
if (tonumber(T.SAFE_V) or 0) < 5 then T.SAFE_V = 5 end

local M = T:RegisterModule("Gear", {
  title = "Équipement",
  defaults = { enabled = true, tab = true, suggest = true, horizon = 3, ah = true, quests = true,
               dungeons = true, crafted = true, vendors = true, maxPerSlot = 3, tooltip = true, rewards = true, arrows = true },
  charDefaults = { shopping = {} },
  options = {
    { key = "arrows", type = "toggle", label = "Flèche verte sur les améliorations dans tes sacs", section = "Équipement" },
    { key = "tooltip", type = "toggle", label = "Infobulles d'objets quand « Infos objets » est coupé : mieux ou pas, prix de vente", section = "Équipement" },
    { key = "rewards", type = "toggle", label = "Entourer la meilleure récompense de quête (jamais choisie à ta place)", section = "Équipement" },
    { key = "tab", type = "toggle", label = "Onglet « Équipement » (emplacement par emplacement)", section = "Équipement" },
    { key = "suggest", type = "toggle", label = "Signaler la meilleure amélioration dans « Maintenant »", section = "Équipement" },
    { key = "horizon", type = "range", label = "Objets jusqu'à niveau + N", min = 0, max = 10, step = 1, section = "Équipement" },
    { key = "maxPerSlot", type = "range", label = "Alternatives par emplacement (infobulle)", min = 1, max = 6, step = 1, section = "Équipement" },
    { key = "quests", type = "toggle", label = "Sources : récompenses de quête", section = "Sources" },
    { key = "dungeons", type = "toggle", label = "Sources : boss de donjon / monstres", section = "Sources" },
    { key = "vendors", type = "toggle", label = "Sources : marchands", section = "Sources" },
    { key = "crafted", type = "toggle", label = "Sources : artisanat", section = "Sources" },
    { key = "ah", type = "toggle", label = "Sources : hôtel des ventes (prix)", section = "Sources" },
  },
})
T.Gear = M

local floor, min, max, abs = math.floor, math.min, math.max, math.abs
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function opt(k) return M.db and M.db[k] end
local function mod(name) local m = T.modules and T.modules[name] if m and (not m.IsEnabled or m:IsEnabled()) then return m end end
local function level() return tonumber((try(g("UnitLevel"), "player"))) or 1 end
local function itemInfo(x) if API.ItemInfo then return API.ItemInfo(x) end return try(g("GetItemInfo"), x) end
local function itemID(link)
  if type(link) == "number" then return link end
  return tonumber(type(link) == "string" and link:match("item:(%d+)") or nil)
end
local function money(c)   -- format commun du panneau (T.Fmt) : « 1 po 20 pa », « 35 pa », « 8 pc »
  local F = T.Fmt
  if F and F.Money then return F.Money(c) end
  c = floor(tonumber(c) or 0)
  local gg, s, k = floor(c / 10000), floor(c / 100) % 100, c % 100
  if gg > 0 then return s > 0 and ("%d po %d pa"):format(gg, s) or ("%d po"):format(gg) end
  if s > 0 then return ("%d pa"):format(s) end
  return ("%d pc"):format(k)
end
-- Les poids de stats sont estimés : un écart en % serait une fausse précision. On le dit en mots (seuils).
local MIN_GAIN, BIG_GAIN = 3, 15
local NO_EFFORT = { craft = true, drop = true, boss = true }        -- pas de durée mesurée pour ces sources
local NOW_KIND = { bag = true, vendor = true, quest = true }        -- seules sources qui montent dans « Maintenant »
--- Verdict en mots d'un écart de score (%, 999 = emplacement vide) → texte, genre ("good" | "same" | "bad")
function M.Verdict(p)
  p = tonumber(p)
  if not p then return nil end
  if p >= 999 then return "emplacement vide", "good" end
  if p >= BIG_GAIN then return "nettement mieux", "good" end
  if p >= MIN_GAIN then return "mieux", "good" end
  if p > -MIN_GAIN then return "équivalent", "same" end
  return "moins bien", "bad"
end
local function pctText(p) return (M.Verdict(p)) or "?" end
local function dur(m) m = floor((tonumber(m) or 0) + 0.5) if m < 60 then return ("~%d min"):format(max(1, m)) end
  return ("~%d h %02d"):format(floor(m / 60), m % 60) end
local function playerClass() return T:PlayerClass() end
local function pack(...) return { n = select("#", ...), ... } end

---------------------------------------------------------------------------------------------------
-- Moteur d'évaluation (repris du module Boost, une seule copie) : poids de stats, objets portables, écart avec
-- l'équipement porté. Poids de leveling Classic, simples et assumés (stat principale = 1, endurance 0,3–0,5, PA ≈
-- 1/3 d'une stat principale, DPS d'arme fort sur la VRAIE arme de la classe : arc / fusil / arbalète du chasseur).
---------------------------------------------------------------------------------------------------
local ENG = {}
local S = {
  STR = "ITEM_MOD_STRENGTH_SHORT", AGI = "ITEM_MOD_AGILITY_SHORT", STA = "ITEM_MOD_STAMINA_SHORT",
  INT = "ITEM_MOD_INTELLECT_SHORT", SPI = "ITEM_MOD_SPIRIT_SHORT", AP = "ITEM_MOD_ATTACK_POWER_SHORT",
  RAP = "ITEM_MOD_RANGED_ATTACK_POWER_SHORT", SP = "ITEM_MOD_SPELL_POWER_SHORT",
  SPD = "ITEM_MOD_SPELL_DAMAGE_DONE_SHORT", HEAL = "ITEM_MOD_SPELL_HEALING_DONE_SHORT",
  HIT = "ITEM_MOD_HIT_RATING_SHORT", CRIT = "ITEM_MOD_CRIT_RATING_SHORT", DEF = "ITEM_MOD_DEFENSE_SKILL_RATING_SHORT",
  ARMOR = "RESISTANCE0_NAME", DPS = "ITEM_MOD_DAMAGE_PER_SECOND_SHORT",
  -- (5.0 GEAR) stats que les objets de Forever portent aussi (tables du client : 13 / 14 / 15 / 43 / 50)
  MP5 = "ITEM_MOD_POWER_REGEN0_SHORT", DODGE = "ITEM_MOD_DODGE_RATING_SHORT", PARRY = "ITEM_MOD_PARRY_RATING_SHORT",
  BLOCK = "ITEM_MOD_BLOCK_RATING_SHORT", XARMOR = "ITEM_MOD_EXTRA_ARMOR_SHORT",
}
local function W(t) -- t = { STR=1, ..., MDPS=, RDPS= }
  local w = { MDPS = t.MDPS or 0, RDPS = t.RDPS or 0 }
  for k, v in pairs(t) do if S[k] then w[S[k]] = v end end
  return w
end
local WEIGHTS = {
  HUNTER  = { default = W{ AGI = 1, RAP = 0.4, AP = 0.35, STA = 0.35, INT = 0.25, SPI = 0.05, HIT = 1.5, CRIT = 1.5, ARMOR = 0.01, RDPS = 3, MDPS = 0.4 } },
  WARRIOR = { default = W{ STR = 1, AGI = 0.7, STA = 0.5, AP = 0.5, HIT = 1.5, CRIT = 1.5, ARMOR = 0.02, MDPS = 3, RDPS = 0.3 },
              [3] = W{ STA = 1, AGI = 0.6, STR = 0.6, DEF = 1, ARMOR = 0.1, AP = 0.2, MDPS = 1.5 } },
  ROGUE   = { default = W{ AGI = 1, STR = 0.5, STA = 0.35, AP = 0.5, HIT = 1.5, CRIT = 1.5, ARMOR = 0.01, MDPS = 3, RDPS = 0.3 } },
  MAGE    = { default = W{ INT = 1, SP = 1.2, SPD = 1.2, SPI = 0.4, STA = 0.45, CRIT = 1, HIT = 1, RDPS = 1.5 } },
  WARLOCK = { default = W{ INT = 0.8, SP = 1.2, SPD = 1.2, STA = 0.7, SPI = 0.3, HIT = 1, RDPS = 1.5 } },
  PRIEST  = { default = W{ INT = 1, SPI = 0.8, HEAL = 1, SP = 0.8, SPD = 0.6, STA = 0.4, RDPS = 1.5 },
              [3] = W{ INT = 0.8, SPI = 0.6, SP = 1.2, SPD = 1.2, STA = 0.5, RDPS = 1.5 } },
  DRUID   = { default = W{ STR = 0.6, AGI = 0.6, INT = 0.6, STA = 0.5, SPI = 0.4, SP = 0.5, AP = 0.3, ARMOR = 0.02, MDPS = 0.5 },
              [1] = W{ INT = 1, SP = 1.1, SPD = 1.1, SPI = 0.4, STA = 0.4 },
              [2] = W{ AGI = 1, STR = 1, STA = 0.7, AP = 0.5, ARMOR = 0.05, HIT = 1.5, CRIT = 1.5 },
              [3] = W{ INT = 1, SPI = 0.8, HEAL = 1, SP = 0.7, STA = 0.4 } },
  PALADIN = { default = W{ STR = 1, INT = 0.5, STA = 0.5, AGI = 0.4, AP = 0.45, SP = 0.3, ARMOR = 0.02, MDPS = 3 },
              [1] = W{ INT = 1, HEAL = 1, SP = 0.8, SPI = 0.4, STA = 0.4 },
              [2] = W{ STA = 1, STR = 0.6, DEF = 1, ARMOR = 0.1, AGI = 0.4, MDPS = 1.5 } },
  SHAMAN  = { default = W{ STR = 0.8, AGI = 0.5, INT = 0.8, STA = 0.5, AP = 0.4, SP = 0.5, ARMOR = 0.02, MDPS = 2.5 },
              [1] = W{ INT = 1, SP = 1.1, SPD = 1.1, SPI = 0.3, STA = 0.4 },
              [2] = W{ STR = 1, AGI = 0.6, STA = 0.5, AP = 0.5, HIT = 1.5, CRIT = 1.5, MDPS = 3 },
              [3] = W{ INT = 1, HEAL = 1, SP = 0.7, SPI = 0.4, STA = 0.4 } },
}
-- Armures (classID 4) : 1 tissu, 2 cuir, 3 mailles (niv 40 chasseur / chaman), 4 plaques (niv 40), 6 bouclier.
local ARMOR = {
  HUNTER = { [1] = 1, [2] = 1, [3] = 40 }, SHAMAN = { [1] = 1, [2] = 1, [3] = 40, [6] = 1 },
  WARRIOR = { [1] = 1, [2] = 1, [3] = 1, [4] = 40, [6] = 1 }, PALADIN = { [1] = 1, [2] = 1, [3] = 1, [4] = 40, [6] = 1 },
  ROGUE = { [1] = 1, [2] = 1 }, DRUID = { [1] = 1, [2] = 1 }, MAGE = { [1] = 1 }, PRIEST = { [1] = 1 }, WARLOCK = { [1] = 1 },
}
-- Armes (classID 2) : 0/1 hache 1M/2M, 2 arc, 3 fusil, 4/5 masse, 6 arme d'hast, 7/8 épée, 10 bâton,
-- 13 pugilat, 15 dague, 16 armes de jet, 18 arbalète, 19 baguette.
local function set(...) local t = {} for _, v in ipairs({ ... }) do t[v] = true end return t end
local WEAPONS = {
  HUNTER = set(0, 1, 2, 3, 6, 7, 8, 10, 13, 15, 16, 18), WARRIOR = set(0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18),
  ROGUE = set(2, 3, 4, 7, 13, 15, 16, 18), MAGE = set(7, 10, 15, 19), WARLOCK = set(7, 10, 15, 19),
  PRIEST = set(4, 10, 15, 19), DRUID = set(4, 5, 10, 13, 15), PALADIN = set(0, 1, 4, 5, 6, 7, 8),
  SHAMAN = set(0, 1, 4, 5, 10, 13, 15),
}
local DUAL = { WARRIOR = 1, ROGUE = 1, HUNTER = 20 }
local SLOTS = {
  INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_CHEST = { 5 }, INVTYPE_ROBE = { 5 },
  INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 }, INVTYPE_WRIST = { 9 }, INVTYPE_HAND = { 10 },
  INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 }, INVTYPE_CLOAK = { 15 },
  INVTYPE_WEAPON = { 16, 17 }, INVTYPE_2HWEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 },
  INVTYPE_WEAPONOFFHAND = { 17 }, INVTYPE_SHIELD = { 17 }, INVTYPE_HOLDABLE = { 17 },
  INVTYPE_RANGED = { 18 }, INVTYPE_RANGEDRIGHT = { 18 }, INVTYPE_THROWN = { 18 }, INVTYPE_RELIC = { 18 },
}

local function specTab()
  -- (5.0 GEAR) LA spé du joueur : GearSpec.lua (talents lus par le jeu, sorts de talent connus, sinon son choix)
  if M.SpecTree then return M.SpecTree() end
  return nil
end
local function staticWeights()
  local cw = WEAPONS[playerClass()] and WEIGHTS[playerClass()] or WEIGHTS.WARRIOR
  local tab = specTab()
  return (tab and cw[tab]) or cw.default
end
local function weights()
  -- (5.0 GEAR) poids de TA spé à TON niveau : GearValue.lua (DPS réel pour les spés physiques, points sinon)
  local V = M.Value
  local w = V and V.Weights and V.Weights()
  return w or staticWeights()
end
local liveStats = {}
local function itemStats(link)
  if not link then return nil end
  local V = M.Value
  if V and V.Item then   -- (5.0 GEAR) une seule lecture d'objet : jeu → tables du client → infobulle, en cache par lien
    local it = V.Item(link)
    return it and it.st or nil
  end
  local c = liveStats[link]
  if c then return c end
  local st
  if G.C_Item and G.C_Item.GetItemStats then st = try(G.C_Item.GetItemStats, link) end
  if not st and G.GetItemStats then st = try(G.GetItemStats, link) end
  if type(st) ~= "table" then return nil end
  liveStats[link] = st
  return st
end
--- Score d'une table de stats (clés ITEM_MOD_*, comme GetItemStats) pour un emplacement ; speed = vitesse de l'arme
--- (chasseur : une arme à distance lente vaut plus à DPS égal — poids w.a + w.b × vitesse)
local function scoreStats(st, slot, w, speed)
  if type(st) ~= "table" then return nil end
  w = w or weights()
  local sc, hard = 0, 0
  local comfort = w.comfort
  for k, v in pairs(st) do
    if k == S.DPS then
      local wt
      if slot == 18 then
        wt = w.RDPS or 0
        if w.b and wt > 0 then wt = wt * ((w.a or 1) + w.b * (tonumber(speed) or 2.8)) end
      elseif slot == 17 then wt = w.OHDPS or w.MDPS or 0
      else wt = w.MDPS or 0 end
      sc, hard = sc + (tonumber(v) or 0) * wt, hard + (tonumber(v) or 0) * wt
    elseif type(w[k]) == "number" then
      local x = (tonumber(v) or 0) * w[k]
      sc = sc + x
      if not (comfort and comfort[k]) then hard = hard + x end
    end
  end
  return sc, hard   -- valeur totale, part « dégâts » (sans Endurance / mana / armure pour les spés physiques)
end
local function scoreLink(link, slot, w)
  local V = M.Value
  local it = V and V.Item and V.Item(link)
  if it then return scoreStats(it.st, slot, w, it.speed) end
  return scoreStats(itemStats(link), slot, w)
end
local function wornLink(slot) return try(g("GetInventoryItemLink"), "player", slot) end
--- Peut-on porter cet objet ? -> ok, raison (texte court)
local function canUse(link)
  local _, _, _, _, minLevel, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link)
  if not equipLoc or equipLoc == "" or not SLOTS[equipLoc] then return false, "non équipable" end
  local cls, lvl = playerClass(), level()
  if classID == 4 and subID and subID > 0 and equipLoc ~= "INVTYPE_CLOAK" then
    local need = (ARMOR[cls] or {})[subID]
    if not need then return false, "armure que tu ne peux pas porter" end
    if lvl < need then return false, "portable au niveau " .. need end
  elseif classID == 2 and subID then
    if not (WEAPONS[cls] or {})[subID] then return false, "arme que tu ne sais pas manier" end
    -- (5.0) chasseur : Tir automatique et tous les tirs demandent un arc, un fusil ou une arbalète
    if cls == "HUNTER" and subID == 16 then return false, "arme de jet : tes tirs demandent un arc, un fusil ou une arbalète" end
  end
  if minLevel and minLevel > lvl then return true, "niveau " .. minLevel .. " requis" end
  return true
end
--- Écart de score avec l'équipement porté, en % (nil = stats inconnues ; 999 = emplacement vide)
local function upgradePct(link)
  if M.Score and type(link) == "string" then   -- (5.0 GEAR) même nombre que l'infobulle et la page
    local sc = M.Score(link)
    if type(sc) == "table" then
      if sc.pct then return sc.pct end
      if sc.known == false then return nil end   -- stats illisibles : inconnu, jamais un score
    end
  end
  local _, _, _, _, _, _, _, _, equipLoc = itemInfo(link)
  local slots = equipLoc and SLOTS[equipLoc]
  if not slots then return nil end
  local w = weights()
  local new = scoreLink(link, slots[1], w)
  if not new then return nil end
  local old
  if equipLoc == "INVTYPE_2HWEAPON" then
    old = (scoreLink(wornLink(16), 16, w) or 0) + (scoreLink(wornLink(17), 17, w) or 0)
  else
    local cand = slots
    if equipLoc == "INVTYPE_WEAPON" then
      local d = DUAL[playerClass()]
      cand = (d and level() >= d) and { 16, 17 } or { 16 }
    end
    for _, sl in ipairs(cand) do
      local l = wornLink(sl)
      local v = l and (scoreLink(l, sl, w) or 0) or 0
      if old == nil or v < old then old = v end
    end
  end
  old = old or 0
  if old <= 0 then return new > 0 and 999 or 0 end
  return (new - old) / old * 100
end
ENG.STAT_KEYS, ENG.WEIGHTS, ENG.ARMOR_LEVEL, ENG.WEAPON_TYPES, ENG.DUAL_LEVEL, ENG.EQUIP_SLOTS = S, WEIGHTS, ARMOR, WEAPONS, DUAL, SLOTS
ENG.Weights, ENG.ScoreStats, ENG.ScoreLink, ENG.ItemStats, ENG.SpecTab = weights, scoreStats, scoreLink, itemStats, specTab
ENG.StaticWeights = staticWeights
ENG.CanUse, ENG.UpgradePct = canUse, upgradePct
M.Engine = ENG
-- contrat partagé (Quick, Dungeon, Abandon… et l'ancien module Boost) : mêmes poids, une seule copie
M.CanUse, M.UpgradePct, M.ScoreStats, M.Weights = canUse, upgradePct, scoreStats, weights
local function boost() return ENG end

---------------------------------------------------------------------------------------------------
-- Tables
---------------------------------------------------------------------------------------------------
-- Emplacements affichés (ordre de la fiche de personnage). 4 chemise / 19 tabard ignorés ; 0 = munitions à part.
local SLOT_LIST = { 1, 2, 3, 15, 5, 9, 10, 6, 7, 8, 11, 12, 13, 14, 16, 17, 18 }
local SLOT_FR = { [1] = "Tête", [2] = "Cou", [3] = "Épaules", [15] = "Dos", [5] = "Torse", [9] = "Poignets",
  [10] = "Mains", [6] = "Taille", [7] = "Jambes", [8] = "Pieds", [11] = "Doigt 1", [12] = "Doigt 2",
  [13] = "Bijou 1", [14] = "Bijou 2", [16] = "Main droite", [17] = "Main gauche", [18] = "Distance" }
-- InventoryType (client) -> nom INVTYPE (clé des emplacements du moteur)
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
-- stat (id client) -> clé ITEM_MOD du moteur (S.*)
local STAT_ID = { [3] = "AGI", [4] = "STR", [5] = "INT", [6] = "SPI", [7] = "STA", [12] = "DEF", [31] = "HIT",
  [32] = "CRIT", [38] = "AP", [39] = "RAP", [41] = "HEAL", [42] = "SPD", [45] = "SP",
  [13] = "DODGE", [14] = "PARRY", [15] = "BLOCK", [43] = "MP5", [50] = "XARMOR" }
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
--- Stats (clés ITEM_MOD du moteur) d'un objet du client, calculées comme l'infobulle.
function M.StatsOf(id)
  local st = statCache[id]
  if st then return st end
  local D = data()
  local r = D and D.items and D.items[id]
  if not r then return nil end
  st = {}
  local str = r[6]
  if str and str ~= "" then
    for sid, v in str:gmatch("(%d+):(%-?%d+)") do
      local k = STAT_ID[tonumber(sid)]
      k = k and S[k]
      if k then st[k] = (st[k] or 0) + tonumber(v) end
    end
  end
  if (r[9] or 0) > 0 then st[S.ARMOR] = r[9] end
  if (r[7] or 0) > 0 then st[S.DPS] = r[7] end
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
  c.maxLevel = max(c.level, tonumber((try(g("GetMaxPlayerLevel")))) or 60)
  c.horizon = max(0, min(c.horizon, c.maxLevel - c.level))   -- (5.0) rien « au niveau 61 »
  c.spec = B and B.SpecTab and try(B.SpecTab) or nil
  c.w = B and try(B.Weights) or {}
  c.money = (API.Money and try(API.Money)) or tonumber((try(g("GetMoney")))) or 0
  local St = T.Stats   -- or/h mesuré sur toutes les sessions (Stats)
  local gph = St and St.GoldPerHour and St:GoldPerHour() or nil
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
    if c.class == "HUNTER" and r.sub == 16 then return false, nil, "arme de jet : pas pour tes tirs" end
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
  local w = c and c.w
  local id = itemID(idOrLink)
  -- un lien (sacs, récompense, porté) : ses vraies stats (suffixe aléatoire compris) ; un n° : les tables du client
  if type(idOrLink) == "string" then
    local V = M.Value
    local it = V and V.Item and V.Item(idOrLink)
    if it and it.st then return B.ScoreStats(it.st, slot, w, it.speed) end
    if it then return nil end
  end
  local st = id and M.StatsOf(id)
  if st then local r = rec(id) return B.ScoreStats(st, slot, w, r and r.speed) end   -- (total, part dégâts)
  if type(idOrLink) == "string" then return B.ScoreLink(idOrLink, slot, w) end
end

function M.Current(c)
  local cur = {}
  for _, slot in ipairs(SLOT_LIST) do
    local link, id = equippedLink(slot), equippedID(slot)
    local r = id and rec(id)
    local name = link and (link:match("%[(.-)%]")) or (r and r.name) or nil
    local s0, h0
    if link or id then s0, h0 = M.ScoreItem(link or id, slot, c) end
    local s = s0 or 0
    local _, _, _, _, _, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link or id or 0)
    cur[slot] = { id = id, link = link, name = name, score = s, hard = h0 or 0, unknown = ((link or id) and s0 == nil) or nil, inv = r and r.inv, equipLoc = equipLoc,
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
        for k = 1, min(6, tonumber((try(NC, qid, true))) or 0) do
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
  if bagItems[id] then add("bag", "dans tes sacs : équipe-le", 0, 0, { now = true }) end
  local lr = logRewards[id]
  if lr and opt("quests") then add("quest", "récompense de « " .. lr.title .. " » (dans ton journal)", 10, 0, { qid = lr.qid, log = true, now = true }) end
  -- modules optionnels (contrats lus seulement s'ils existent) ; lus une fois par plan (c.mods)
  local mods = c.mods
  if not mods then
    mods = { Dg = mod("Dungeon") or false, Pf = mod("Prof") or false, A = mod("Auction") or false, db = questie() or false }
    c.mods = mods
  end
  local Dg = mods.Dg
  if Dg and Dg.ItemSource and opt("dungeons") then
    local s = try(Dg.ItemSource, id)
    if type(s) == "table" and s.text then
      -- (4.3 V43) faisable à 4 : niveau ≥ minimum conseillé du donjon (- 2 à quatre)
      add("boss", s.text, tonumber(s.min) or 60, 0, { dkey = s.key, dmin = s.dmin,
        now = not tonumber(s.dmin) or c.level >= tonumber(s.dmin) - 2 })
    end
  end
  local Pf = mods.Pf
  if Pf and Pf.CraftInfo and opt("crafted") then
    local s = try(Pf.CraftInfo, id)
    if type(s) == "table" and s.text then add("craft", s.text, tonumber(s.min) or 15, tonumber(s.cost) or 0, { now = s.can ~= false }) end
  end
  local db = mods.db
  local qi = db and db.QueryItemSingle
  if qi then
    if opt("quests") then
      for _, qid in ipairs(list(try(qi, id, "questRewards"))) do
        if not (lr and lr.qid == qid) and not questDone(qid) then
          local ql = tonumber(db.QueryQuestSingle and try(db.QueryQuestSingle, qid, "requiredLevel")) or 1
          if ql <= c.level + c.horizon then
            local name = db.QueryQuestSingle and try(db.QueryQuestSingle, qid, "name") or ("quête " .. qid)
            add("quest", ("récompense de « %s » (niv %d)"):format(name, ql), 30 + max(0, ql - c.level) * 20, 0,
              { qid = qid, now = ql <= c.level })
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
        local zone = tonumber((try(qn, npc, "zoneID")))
        local rank = tonumber((try(qn, npc, "rank"))) or 0
        local nl = tonumber((try(qn, npc, "maxLevel"))) or tonumber((try(qn, npc, "minLevel"))) or c.level
        local d = zone and DUNGEONS[zone]
        if d then
          local far = max(0, d[2] - c.level)
          add("boss", ("%s — %s%s"):format(d[1], name, rank == 3 and " (boss)" or ""), 60 + far * 15, 0,
            { dungeon = d[1], dmin = d[2], dmax = d[3], npc = npc, now = c.level >= d[2] - 2 })
        else
          local z = areaName(zone)
          local tag = (rank == 4 or rank == 2) and " (rare)" or (rank == 1 and " (élite)" or "")
          add("drop", ("butin de %s%s niv %d%s"):format(name, tag, nl, z and (" — " .. z) or ""),
            (rank == 4 or rank == 2) and 45 or 25, 0, { npc = npc, now = nl <= c.level + 2 and rank ~= 1 })
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
        local z = areaName(tonumber((try(qn, npc, "zoneID"))))
        add("vendor", ("marchand %s%s%s"):format(name, z and (" — " .. z) or "", #vend > 1 and (" (+" .. (#vend - 1) .. ")") or ""),
          10, r and r.buy or 0, { npc = npc, now = (r and r.buy or 0) <= c.money })
      end
    end
  end
  -- artisanat (tables client)
  local D = data()
  local cr = D and D.crafted and D.crafted[id]
  if cr and opt("crafted") and not (Pf and Pf.CraftInfo) then
    local pname = (D.profs and D.profs[cr[1]]) or "artisanat"
    local have = craftSkills()[cr[1]]
    local mate = M.GroupCrafter and M.GroupCrafter(pname, cr[2])
    if have and have >= cr[2] then add("craft", ("à fabriquer : %s ~%d (tu as %d)"):format(pname, cr[2], have), 15, 0, { now = true })
    elseif mate then add("craft", ("fabriqué par %s (%s, ton groupe)"):format(mate, pname), 15, 0, { now = true })
    elseif have then add("craft", ("%s ~%d requis (tu as %d)"):format(pname, cr[2], have), 15 + (cr[2] - have), 0)
    else add("craft", ("fabriqué par un artisan (%s ~%d) — ami / canal commerce"):format(pname, cr[2]), 25, 0) end
  end
  -- hôtel des ventes (jamais pour un objet lié quand ramassé)
  local A = mods.A
  if opt("ah") and A and A.Stats and not (r and r.bind == 1) then
    local st = try(A.Stats, id)
    local p = type(st) == "table" and tonumber(st.market) or nil
    if p and p > 0 then add("ah", "HV ≈ " .. money(p) .. (st.src and (" (" .. st.src .. ")") or ""), 5, p, { price = p, now = p <= c.money }) end
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
--- objet de test ou retiré du jeu resté dans les tables du client (« [PÉRIMÉ] … », « 3800 Test 2h Axe 63 blue ») :
--- jamais proposé (il n'existe pas en jeu)
local function junkName(name)
  name = tostring(name or "")
  return name == "" or name:find("^%[") ~= nil or name:find("TEST", 1, true) ~= nil or name:find("%f[%a]Test%f[%A]") ~= nil
    or name:find("Testing", 1, true) ~= nil
end
M.JunkName = junkName
local function prepare(id, link, c)
  local r = rec(id)
  local invName
  if r and not link and junkName(r.name) then return nil end
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
  -- (5.0 GEAR) objet porté illisible : on ne compare pas (jamais « mieux » contre un score inconnu)
  if cur[slot] and cur[slot].unknown then return nil end
  if invName == "INVTYPE_2HWEAPON" and cur[17] and cur[17].unknown then return nil end
  local new, newHard = M.ScoreItem(r.link or r.id, slot, c)
  local old = oldScore(cur, slot, invName)
  local pct = gainPct(new, old)
  if not pct or pct < MIN_GAIN then return nil end
  -- (5.0) spé à dégâts : un objet qui fait PERDRE des dégâts n'est pas une amélioration, même avec plus d'Endurance
  if c.w and c.w.unit == "dps" then
    local oldHard = (invName == "INVTYPE_2HWEAPON") and ((cur[16] and cur[16].hard or 0) + (cur[17] and cur[17].hard or 0))
      or (cur[slot] and cur[slot].hard or 0)
    if (newHard or 0) < oldHard - 0.05 then return nil end
  end
  return { id = r.id, link = r.link, name = r.name, req = r.req, need = r.need, pct = pct, score = new, old = old,
    delta = new - old, inv = r.inv,
    invName = invName, cls = r.cls, sub = r.sub, dps = r.dps, speed = r.speed, bind = r.bind }
end

-- valeur réaliste : gain ÷ racine de l'effort (minutes équivalentes), malus si niveau pas encore atteint
local function value(cand, src, c)
  local wait = max(0, (cand.need or 0) - c.level) * 45
  return (cand.delta or min(cand.pct, 300)) / math.sqrt(10 + src.effort + wait)
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

M._resetIndex = function() end   -- (tests : plus d'index gardé en mémoire)
local EMPTY = {}
--- un objet des tables (ligne brute r) est-il un candidat pour un emplacement ? Sans aucune table temporaire :
--- classe (masque), armure / arme, niveau, puis le score ; la table du candidat n'est créée que s'il gagne.
local function fastCandidate(id, r, c, cur, per, only)
  if not r then return end
  local invName = INV[r[1]]
  local slots = invName and SLOTS[invName]
  if not slots then return end
  if only and not (only[slots[1]] or (slots[2] and only[slots[2]])) then return end   -- plan partiel : pas cet emplacement
  local mask = r[13]
  if mask and mask > 0 then
    local bit = CLASS_BIT[c.class]
    if bit and floor(mask / 2 ^ (bit - 1)) % 2 == 0 then return end
  end
  local need, cls, sub = r[2] or 0, r[4], r[5]
  if cls == 4 and sub and sub > 0 and r[1] ~= 16 then
    local lv = (ARMOR[c.class] or EMPTY)[sub]
    if not lv then return end
    if lv > need then need = lv end
  elseif cls == 2 then
    if not (WEAPONS[c.class] or EMPTY)[sub] then return end
    if c.class == "HUNTER" and sub == 16 then return end
  end
  if need > c.level + c.horizon then return end
  local st, w, checked
  for i = 1, #slots do
    local slot = slots[i]
    local l = per[slot]
    local e = cur[slot]
    local okSlot = l and not (only and not only[slot]) and not (e and (e.id == id or e.unknown))
    if okSlot and slot == 17 and (c.twoHand or invName == "INVTYPE_WEAPON") then
      local d = DUAL[c.class]
      if c.twoHand or not (d and c.level >= d) then okSlot = false end
    end
    if okSlot and invName == "INVTYPE_2HWEAPON" and cur[17] and cur[17].unknown then okSlot = false end
    if okSlot then
      st = st or M.StatsOf(id)
      w = w or c.w
      local new, newHard = scoreStats(st, slot, w, r[8])
      local old, oldHard
      if invName == "INVTYPE_2HWEAPON" then
        old = (cur[16] and cur[16].score or 0) + (cur[17] and cur[17].score or 0)
        oldHard = (cur[16] and cur[16].hard or 0) + (cur[17] and cur[17].hard or 0)
      else
        old, oldHard = e and e.score or 0, e and e.hard or 0
      end
      local pct
      if old <= 0 then pct = new > 0 and 999 or 0 else pct = (new - old) / old * 100 end
      if pct >= MIN_GAIN and not (w.unit == "dps" and (newHard or 0) < oldHard - 0.05) then
        if checked == nil then checked = not junkName(r[12]) end
        if checked then
          l[#l + 1] = { id = id, name = r[12], req = r[2], need = need, pct = pct, score = new, old = old, delta = new - old,
            inv = r[1], invName = invName, cls = cls, sub = sub, dps = r[7], speed = r[8], bind = r[10] }
        end
      end
    end
  end
end

--- Calcul (synchrone). Le module l'appelle par tranches via une coroutine. `only` = { [emplacement] = true } : ne
--- refaire que ces emplacements (un objet équipé ne change que le sien), le reste vient du plan précédent `prev`.
function M.Plan(yieldEvery, only, prev)
  local c = M.Ctx()
  local function pause() if yieldEvery and coroutine.running() then coroutine.yield() end end
  craftAt = -100
  scanBags()
  M.ScanLogRewards()
  srcCache = {}
  local cur = M.Current(c)
  if only and (next(only) == nil or not (prev and prev.slots and prev.ctx and prev.ctx.level == c.level and prev.ctx.horizon == c.horizon
      and prev.ctx.w and c.w and prev.ctx.w.id == c.w.id)) then
    only = nil   -- les poids ont bougé (plafond de toucher, niveau, spé…) : tout refaire
  end
  local per = {}
  for _, slot in ipairs(SLOT_LIST) do per[slot] = {} end
  -- 1. objets à lien (sacs, récompenses du journal) : peu nombreux, leurs vraies stats (suffixe aléatoire compris)
  local seen = {}
  local function linked(id, link)
    if seen[id] then return end
    seen[id] = true
    local r = prepare(id, link, c)
    for _, slot in ipairs(r and r.slots or EMPTY) do
      if per[slot] and not (only and not only[slot]) then
        local cd = candidateFor(r, slot, c, cur)
        if cd then local l = per[slot] l[#l + 1] = cd end
      end
    end
  end
  for id, link in pairs(bagItems) do linked(id, link) end
  for id, e in pairs(logRewards) do linked(id, e.link) end
  pause()
  -- 2. objets du client de niveau requis utile (index par niveau), par tranches courtes
  -- (une passe sur les ~5 000 lignes, sans index gardé en mémoire : le filtre de niveau coûte ~8 instructions par
  -- ligne ; la tranche se compte en « poids » : 1 par ligne écartée, 12 par objet examiné)
  local D = data()
  local lo, hi = c.level - 12, c.level + c.horizon
  local budget, load = yieldEvery and yieldEvery * 2 or nil, 0
  for id, r in pairs(D and D.items or EMPTY) do
    local req = r[2] or 0
    if req >= lo and req <= hi and not seen[id] then
      load = load + 12
      fastCandidate(id, r, c, cur, per, only)
    else
      load = load + 1
    end
    if budget and load >= budget then load = 0 pause() end
  end
  pause()
  -- ne garder que les 10 plus gros gains par emplacement avant de chercher les sources (coût Questie borné)
  local plan = { ctx = c, cur = cur, slots = {}, now = {}, nowUsed = {}, top = {}, time = (g("GetTime") and g("GetTime")()) or 0 }
  local used = {}
  local function byDelta(a, b) if a.delta ~= b.delta then return a.delta > b.delta end return a.id < b.id end
  --- les k meilleurs d'une liste (gain décroissant, puis n°), sans la trier en entier (les listes des emplacements
  --- vides ont des centaines d'objets)
  local function topK(l, k, wearable)
    local out, n = {}, 0
    for i = 1, #l do
      local cd = l[i]
      if not wearable or (cd.need or 0) <= c.level then
        local last = out[n]
        -- (comparaison en ligne : presque tous les objets sont moins bons que le k-ième, pas d'appel pour eux)
        if n < k or cd.delta > last.delta or (cd.delta == last.delta and cd.id < last.id) then
          local j = (n < k) and n + 1 or n
          while j > 1 and byDelta(cd, out[j - 1]) do out[j] = out[j - 1] j = j - 1 end
          out[j] = cd
          if n < k then n = n + 1 end
        end
      end
    end
    return out
  end
  for _, slot in ipairs(SLOT_LIST) do
    if only and not only[slot] then   -- emplacement inchangé : le résultat précédent (les sources n'ont pas bougé)
      plan.slots[slot], plan.now[slot], plan.top[slot] = prev.slots[slot] or {}, prev.now and prev.now[slot] or nil, prev.top and prev.top[slot] or 0
      local b = plan.slots[slot][1]
      if b then used[b.id] = true end
      local nw = plan.now[slot]
      if nw and (nw.invName == "INVTYPE_FINGER" or nw.invName == "INVTYPE_TRINKET") then plan.nowUsed[nw.id] = true end
    else
      local l = per[slot]
      -- (4.3 V43) « le meilleur maintenant » : les 6 plus gros gains portables dès aujourd'hui, à part
      local nowC = topK(l, 6, true)
      -- (5.0) le meilleur score portable aujourd'hui (barres de « Mon équipement »)
      local topNow = 0
      for i = 1, #l do
        local cd = l[i]
        if cd.score > topNow and (cd.need or 0) <= c.level then topNow = cd.score end
      end
      plan.top[slot] = topNow
      plan.now[slot] = M.PickNow(nowC, c, plan.nowUsed)
      -- durée d'utilité : niveau où un objet nettement meilleur (≥ 10 %) des tables devient portable
      local function life(cd)
        local lv
        local thr, base = cd.score * 1.10, max(cd.need or 0, c.level)
        for i = 1, #l do
          local o = l[i]
          if o.score >= thr and (o.need or 0) > base and o.id ~= cd.id and (not lv or o.need < lv) then lv = o.need end
        end
        return lv
      end
      if plan.now[slot] then plan.now[slot].life = life(plan.now[slot]) end
      pause()
      -- les 8 plus gros gains avant de chercher les sources (coût Questie borné)
      local best = pickBest(topK(l, 8), c, max(1, tonumber(opt("maxPerSlot")) or 3) + 1)
      -- anneaux / bijoux : ne pas proposer le même objet (unique) pour les deux emplacements
      if (slot == 12 or slot == 14) and best[1] and used[best[1].id] then table.remove(best, 1) end
      if best[1] then used[best[1].id] = true end
      while #best > (tonumber(opt("maxPerSlot")) or 3) do table.remove(best) end
      if best[1] and best[1].life == nil then best[1].life = life(best[1]) end
      plan.slots[slot] = best
      pause()
    end
  end
  -- (5.0) la comparaison chiffrée de chaque piste retenue, ici (par tranches) et pas à l'affichage
  if M.CompareCand then
    for _, slot in ipairs(SLOT_LIST) do
      if not (only and not only[slot]) then
        local nw = plan.now[slot]
        if nw and nw.cmp == nil then nw.cmp = M.CompareCand(nw, slot) or false end
        for _, cd in ipairs(plan.slots[slot]) do if cd.cmp == nil then cd.cmp = M.CompareCand(cd, slot) or false end end
        pause()
      end
    end
  end
  plan.shopping = M.Shopping(plan)
  plan.hunter = M.HunterNotes(plan)
  statCache = {}   -- les stats lues pour ce plan ne restent pas en mémoire (les pistes retenues gardent les leurs)
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

-- (4.3 V43) ---------------------------------------------------------------- le meilleur objet atteignable MAINTENANT
--- artisan du groupe (avec TreeAI) qui a le métier au rang voulu : son nom, sinon nil
function M.GroupCrafter(pname, need)
  local Gm = T.modules and T.modules.Group
  local okL, list = pcall(function() return Gm and Gm._active and Gm.List and Gm:List() end)
  for _, m in ipairs(okL and type(list) == "table" and list or {}) do
    local pr = not m.me and type(m.st) == "table" and m.st.pr
    for _, e in ipairs(type(pr) == "table" and pr or {}) do
      local n, rk = tostring(e):match("^(.-) (%d+)$")
      if n and n == pname and (tonumber(rk) or 0) >= (tonumber(need) or 0) and type(m.name) == "string" then return m.name end
    end
  end
end
--- parmi des candidats portables dès maintenant : le plus gros gain dont une source est faisable tout de suite
--- (sacs, quête faisable, donjon faisable à 4, marchand / HV dans tes moyens, ton artisanat ou celui du groupe)
function M.PickNow(cands, c, used)
  local best
  for _, cd in ipairs(cands) do
    if not (used and used[cd.id]) then
      local src
      for _, e in ipairs(M.Sources(cd.id, c)) do if e.now then src = e break end end
      if src and (not best or cd.delta > best.delta or (cd.delta == best.delta and src.effort < best.nowSrc.effort)) then
        best = cd
        best.nowSrc = src
      end
    end
  end
  if best and used and (best.inv == 11 or best.inv == 12 or best.invName == "INVTYPE_FINGER" or best.invName == "INVTYPE_TRINKET") then
    used[best.id] = true
  end
  return best
end
local GAIN_KEYS = { { "AGI", "Agi" }, { "RAP", "PA dist." }, { "AP", "PA" }, { "STA", "Endu" }, { "HIT", "Toucher" },
  { "CRIT", "Crit" }, { "INT", "Intel" }, { "STR", "Force" }, { "SPI", "Esprit" }, { "SP", "Puiss. sorts" } }
--- gain réel en stats (« +3,2 DPS · +4 Agi · +10 PA dist. ») : objet proposé moins objet porté (données du client)
--- (5.0) compare un candidat du plan à ce que tu portes dans `slot` (même calcul que T.Gear.Score) → résultat ou nil
function M.CompareCand(cd, slot)
  local V = M.Value
  if not (V and V.Compare and cd) then return nil end
  local it = V.Item(cd.link or cd.id)
  local w = V.Weights()
  if not (it and it.st and w) then return nil end
  local olds = {}
  local function add(s)
    local l = wornLink(s)
    if l then olds[#olds + 1] = { slot = s, link = l, it = V.Item(l) } end
  end
  if cd.invName == "INVTYPE_2HWEAPON" then add(16) add(17) else add(slot) end
  return V.Compare(it, olds, slot, w)
end
function M.GainText(cd, worn, slot)
  local head
  if cd then
    if cd.cmp == nil then cd.cmp = M.CompareCand(cd, slot) or false end
    head = cd.cmp and cd.cmp.text or nil
  end
  local a = cd and M.StatsOf(cd.id)
  if not a then return head end
  local b = (worn and worn.id and M.StatsOf(worn.id)) or (worn and worn.link and itemStats(worn.link)) or {}
  local out = {}
  local function n(t, k) return tonumber(t[k]) or 0 end
  if head then out[#out + 1] = head end
  local dd = n(a, S.DPS) - n(b, S.DPS)
  if not head and (slot == 18 or slot == 16) and math.abs(dd) >= 0.1 then
    out[#out + 1] = ((dd > 0 and "+" or "") .. ("%.1f DPS"):format(dd)):gsub("%.", ",")
  end
  local w = M.Weights and M.Weights() or {}
  for _, kk in ipairs(GAIN_KEYS) do
    local k = S[kk[1]]
    local d = n(a, k) - n(b, k)
    if d ~= 0 and (tonumber(w[k]) or 0) > 0.1 then out[#out + 1] = ("%s%d %s"):format(d > 0 and "+" or "", d, kk[2]) end
    if #out >= 4 then break end
  end
  local ar = n(a, S.ARMOR) - n(b, S.ARMOR)
  if #out < 4 and ar >= 10 then out[#out + 1] = ("+%d armure"):format(ar) end
  return #out > 0 and table.concat(out, " · ") or nil
end
--- position d'un PNJ (Questie, lecture seule) : { map, x, y } hors instance, sinon nil
local function npcPoint(npc)
  local X = T.modules and T.modules.Export
  local db = X and X.Source and select(2, pcall(X.Source, X))
  if not (type(db) == "table" and db.N and db.map and X.Cluster and npc) then return nil end
  local okC, cl = pcall(X.Cluster, db.N(npc, "spawns"), db.map, db.dungeon)
  for _, p in ipairs(okC and type(cl) == "table" and cl or {}) do if not p.inst then return p end end
end
M.NpcPoint = npcPoint
--- PNJ de la quête : celui qui la rend (quête du journal) ou qui la donne
local function questNpc(qid, log)
  local db = questie()
  local q = db and db.QueryQuestSingle
  local by = q and try(q, qid, log and "finishedBy" or "startedBy")
  local npcs = type(by) == "table" and by[1]
  return type(npcs) == "table" and npcs[1] or nil
end
--- « où l'obtenir » : un clic mène à la source (boussole vers le PNJ, page Donjons, Métiers, HV) ; jamais d'achat
function M.GoSource(src, cd)
  if type(src) ~= "table" then return false end
  local W = T.Window
  local kind = src.kind
  if kind == "boss" then
    if W and W.char and src.dkey then W.char.dungeon = src.dkey end
    if W and W.Open then W.Open("dungeon") end
    return "dungeon"
  elseif kind == "craft" then
    if W and W.Open then W.Open("prof") end
    return "prof"
  elseif kind == "ah" then
    local A = T.modules and T.modules.Auction
    local f = rawget(G, "AuctionHouseFrame") or rawget(G, "AuctionFrame")
    local open = type(f) == "table" and type(f.IsShown) == "function" and pass(pcall(f.IsShown, f))
    if open and A and A.SearchItem and cd and try(A.SearchItem, cd.id) then return "ah" end
    if W and W.Open then W.Open("hv") end
    return "hv"
  end
  local npc = src.npc or (kind == "quest" and src.qid and questNpc(src.qid, src.log))
  local pt = npc and npcPoint(npc)
  local Cm = T.Compass
  if pt and Cm and Cm.SetTarget then
    try(Cm.SetTarget, Cm, { mapID = pt.map, x = pt.x, y = pt.y, label = (cd and cd.name) or src.text, source = "gear" })
    return "compass"
  end
  return false
end
local KIND_FR = { bag = "sacs", quest = "quête", boss = "donjon", drop = "butin", vendor = "marchand", craft = "artisanat",
  ah = "HV", world = "butin" }
local GO_FR = { boss = "Clic : la page du donjon", craft = "Clic : la page Métiers", ah = "Clic : chercher à l'HV (ou y aller)",
  bag = "Dans tes sacs : clic droit pour l'équiper" }
function M.GoHint(kind) return GO_FR[kind] or "Clic : la boussole t'y mène" end
--- lignes « Le meilleur maintenant » (page Équipement) : une par emplacement, gain réel, coût, temps, clic = y aller
function M.NowRows(plan)
  plan = plan or M.plan
  local out = { cost = 0, min = 0 }
  if not (plan and plan.now) then return out end
  local c = plan.ctx
  for _, slot in ipairs(SLOT_LIST) do
    local cd = plan.now[slot]
    local src = cd and cd.nowSrc
    if src then
      local worn = plan.cur[slot] or {}
      local gain = M.GainText(cd, worn, slot) or pctText(cd.pct)
      local where = tostring(src.text or "?")
      local cost = (src.cost or 0) > 0 and money(src.cost) or nil
      local tm = src.kind == "bag" and "maintenant" or (not NO_EFFORT[src.kind] and dur(src.min)) or nil
      out.cost = out.cost + (src.cost or 0)
      out.min = out.min + ((src.kind ~= "bag" and not NO_EFFORT[src.kind]) and (src.min or 0) or 0)
      local right = cost and tm and (cost .. " · " .. tm) or cost or tm or KIND_FR[src.kind]
      local tip = { ("%s : %s → %s"):format(SLOT_FR[slot] or slot, worn.name or "(vide)", cd.name or "?"),
        "Gain : " .. gain .. " (" .. pctText(cd.pct) .. ")", "Où : " .. where }
      for i, e in ipairs(cd.sources or M.Sources(cd.id, c)) do
        if e ~= src and i <= 4 then tip[#tip + 1] = "  ou " .. e.text .. (e.now and "" or " (pas encore)") end
      end
      out[#out + 1] = { slot = slot, itemID = cd.id, src = src, srcKind = src.kind,
        text = ("%s → %s · %s"):format(SLOT_FR[slot] or slot, cd.name or "?", gain),
        sub = where, right = right, tooltip = tip, prio = (src.kind == "bag" or cd.pct >= BIG_GAIN) and 2 or 1,
        clickHint = GO_FR[src.kind] or "Clic : la boussole t'y mène",
        onClick = function() M.GoSource(src, cd) end }
    end
  end
  return out
end

-- (4.3 V43) stock de potions de soins : les potions classiques (objets du jeu), la meilleure de ton niveau
M.POTIONS = { { 118, 1 }, { 858, 3 }, { 929, 12 }, { 1710, 21 }, { 3928, 35 }, { 13446, 45 } }
M.POTION_WANT = 5
function M.PotionStock()
  local lv = level()
  local best
  for _, p in ipairs(M.POTIONS) do if p[2] <= lv then best = p[1] end end
  if not best then return nil end
  local usable = {}
  local D = T.Data and T.Data.Consumables
  for _, r in ipairs(D and D.hpot or {}) do if type(r) == "table" and (r[2] or 1) <= lv then usable[r[1]] = true end end
  usable[best] = true
  local have, haveBest = 0, 0
  if API.IterBags then
    for _, _, info in API.IterBags() do
      local id = info and (info.itemID or info.id)
      local n = info and tonumber(info.stackCount or info.count) or 1
      if id and usable[id] then have = have + n end
      if id == best then haveBest = haveBest + n end
    end
  end
  local name = itemInfo(best)
  return { best = best, name = type(name) == "string" and name ~= "" and name or nil, have = have, haveBest = haveBest,
    want = M.POTION_WANT }
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
  -- commandes = vrais petits boutons dans la barre de l'onglet (control = true), pas des lignes de texte
  add(("Recalculer (objets jusqu'au niv %d)"):format(c.level + c.horizon),
    1, { "Recalcule tout de suite (hors combat).", "Sinon : automatique quand ton équipement, ton niveau, ta spé ou ton journal change." }, rescan)
  lines[#lines].control, lines[#lines].label = true, "Recalculer"
  add(("Horizon : niveau +%d"):format(c.horizon), 1,
    { "Montre aussi les objets que tu pourras porter bientôt.", "Clic : +2 (0 à 10)." }, bumpHorizon)
  lines[#lines].control, lines[#lines].label = true, ("Horizon +%d"):format(c.horizon)
  -- (5.0) la spé et sa source, toujours ; clic = la page (3 boutons pour la changer)
  if M.SpecText then
    add(M.SpecText(), 1, { "TreeAI adapte les poids d'équipement à ta spé.", "Clic : la page Équipement pour la changer." }, selectTab)
  end
  local V = M.Value
  add("Priorité des stats : " .. priorityText(c), 1, { V and V.ModelText and V.ModelText() or "Poids de stats par classe et par spé.",
    "Le détail de chaque objet est dans son infobulle." })
  local h = plan.hunter
  if h then
    if h.ranged then
      local t = ("Arme à distance : %s%s"):format(h.ranged.name or "?", weaponTxt(h.ranged))
      if h.ranged.speed and h.ranged.speed > 0 and h.ranged.speed < 2.6 then t = t .. " — rapide : à DPS égal, une arme lente frappe plus fort" end
      add(t, 1)
    else add("Aucune arme à distance : c'est ta vraie arme, priorité absolue !", 3) end
    if h.ammoNeed and not h.ammoOK then add(("Munitions : ton arme tire des %s et tu n'en as pas !"):format(AMMO_FR[h.ammoNeed]), 3)
    elseif h.ammoNeed then add(("Munitions : %s (assorties à ton arme)"):format(AMMO_FR[h.ammoNeed]), 1) end
    if h.ammoSwitch then add("Attention : la prochaine arme change de munitions (" .. h.ammoSwitch .. ")", 2) end
    if h.mail then add(h.mail, c.level >= 38 and 2 or 1) end
  end
  -- une ligne par emplacement qui a quelque chose à faire (les « Garde » sont comptés, pas listés)
  local rows = M.Rent and M.Rent(plan) or {}
  local keep, nUp, buyN, buyCost = 0, 0, 0, 0
  for _, r in ipairs(rows) do
    if r.action == "keep" then keep = keep + 1
    else
      nUp = nUp + 1
      lines[#lines + 1] = { text = r.text, right = r.right, prio = r.prio or 1, tooltip = r.tooltip, onClick = r.onClick,
        clickHint = r.clickHint, kind = "gear", slot = r.slot, action = r.action }
      if r.action == "buy" then buyN, buyCost = buyN + 1, buyCost + (r.cost or 0) end
    end
  end
  if buyN > 0 then
    local bud = rows.budget or {}
    add(("Achats conseillés : %d objet(s), %s au total (budget libre : %s)"):format(buyN, money(buyCost), money(bud.free or c.money)), 2)
  end
  if nUp == 0 then add("Équipement au top pour ton niveau : rien à chercher pour l'instant.", 1)
  elseif keep > 0 and lines[1] and lines[1].control then
    local tip = lines[1].tooltip
    if type(tip) == "table" then tip[#tip + 1] = ("%d emplacement(s) déjà au mieux pour ton niveau."):format(keep) end
  end
  return lines
end

function M.Summary(plan)
  local out = { level = plan.ctx.level, spec = plan.ctx.spec, priority = priorityText(plan.ctx), slots = {}, shopping = {} }
  local sp = M.Spec and M.Spec()   -- (5.0) la spé et d'où elle vient (pour l'appli)
  if sp and sp.tree then out.specName, out.specSource = sp.name, sp.source end
  for _, slot in ipairs(SLOT_LIST) do
    local e, cd = plan.cur[slot] or {}, (plan.slots[slot] or {})[1]
    local row = { slot = SLOT_FR[slot], item = e.name, score = floor((e.score or 0) * 10 + 0.5) / 10 }
    if cd then
      row.next = { id = cd.id, name = cd.name, gain = floor(min(cd.pct, 999) + 0.5), verdict = M.Verdict(cd.pct), need = cd.need,
        kind = cd.src and cd.src.kind, where = cd.src and cd.src.text, cost = cd.src and cd.src.cost,
        min = cd.src and floor(cd.src.min + 0.5) }
    end
    out.slots[#out.slots + 1] = row
  end
  for _, e in ipairs(plan.shopping or {}) do
    out.shopping[#out.shopping + 1] = { name = e.name, cost = e.cost, gain = floor(min(e.pct, 999) + 0.5), verdict = M.Verdict(e.pct),
      where = e.where }
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
  M._rent, M._pubSig = nil, nil
  if opt("tab") and T.SetTabLines then pcall(T.SetTabLines, T, "gear", M.Lines(plan)) end
  M._linesFor = plan
  local s = T.Summary and T:Summary()
  if type(s) == "table" then s.gear = M.Summary(plan) end
  self.char.shopping = {}
  for _, e in ipairs(plan.shopping or {}) do self.char.shopping[#self.char.shopping + 1] = { id = e.id, cost = e.cost } end
  M.PublishNow()
  if M.AskSpecOnce then M.AskSpecOnce() end
end
--- (5.0) « Maintenant » : UNE action concrète (équiper, acheter au marchand, quête du journal), la plus rentable ;
--- appelée après un plan et après un changement de sac (les sacs sont lus en direct)
function M.PublishNow()
  if not M._active then return end
  -- les lignes de l'onglet ne sont refaites que si les améliorations des sacs ont changé (pas à chaque butin)
  local up = M.BagUpgrades and M.BagUpgrades()
  local sig = tostring(M.plan and M.plan.time)
  for _, e in ipairs(up and up.list or {}) do sig = sig .. ":" .. tostring(e.id) end
  if sig == M._pubSig then return end
  M._pubSig = sig
  if M.plan and M._linesFor ~= M.plan and opt("tab") and T.SetTabLines then pcall(T.SetTabLines, T, "gear", M.Lines(M.plan)) end
  M._linesFor = nil
  if not opt("suggest") then M:Clear("upgrade") return end
  local top
  if M.plan then
    for _, r in ipairs(M.BestNow and M.BestNow() or {}) do
      if r.action == "equip" or r.action == "buy" or (r.action == "quest" and (r.prio or 1) >= 2) then
        if not top or (r.action == "equip" and top.action ~= "equip") then top = r end
        if top.action == "equip" then break end
      end
    end
  else   -- pas encore de plan : les sacs suffisent pour dire « Équipe X »
    local best
    for _, e in pairs(up and up.bySlot or {}) do if not best or (e.sc.delta or 0) > (best.sc.delta or 0) then best = e end end
    if best then top = { action = "equip", text = ("Équipe %s · %s"):format(best.sc.item and best.sc.item.name or "?", best.sc.text or "mieux") } end
  end
  if top then
    if top.action == "buy" and M.AskMountOnce then M.AskMountOnce() end
    M:Suggest("upgrade", top.text, top.action == "equip" and 2 or 1, { ttl = 600, cooldown = 300, onClick = selectTab,
      tooltip = { "Clic : la page Équipement." } })
  else
    M:Clear("upgrade")
  end
end

---------------------------------------------------------------------------------------------------
-- Exécution (hors combat, par tranches)
---------------------------------------------------------------------------------------------------
local job, dirty, lastRun = nil, false, -1000   -- (5.0) pas de plan tant que rien ne le demande (voir M.WantPlan)
local dirtySlots   -- nil = tout ; { [emplacement] = true } = seulement ceux-là (objet équipé)
function M:ScanNow()
  -- (5.0) un calcul par tranches en cours a des entrées périmées : il ne doit pas écraser ce résultat-ci
  if M._cancelJob then M._cancelJob() end
  dirtySlots = {}
  local ok, plan = pcall(M.Plan)
  if ok and plan then self:Publish(plan) return plan end
  if not ok then self:Debug("plan:", plan) end
end

-- (4.4 SMART) la minuterie des tranches n'existe que pendant un calcul : au repos, zéro travail (avant : un rappel
-- toutes les 0,1 s pour toujours, ≈ 600 réveils / min de l'ordonnanceur pour rien)
-- (5.0) les tranches du plan passent par T:Spread : une par image, et seulement dans une image sans autre travail lourd
-- (avant : une minuterie de 0,1 s qui s'ajoutait à ce que l'image faisait déjà). Sans T:Spread : un rappel de 0,1 s.
local stepGen, stepQueued = 0, false
local step
local function spreadStep(gen)
  if gen ~= stepGen then return end   -- calcul annulé entre-temps
  stepQueued = false
  step()
end
local function queueStep()
  if stepQueued or not job then return end
  stepQueued = true
  local gen = stepGen
  if T.Spread then T:Spread(M, spreadStep, gen) else T:After(0.1, function() spreadStep(gen) end) end
end
local function stopTicker() stepGen, stepQueued = stepGen + 1, false end
step = function()
  if not job then return end
  if T:InCombat() then return end   -- repris à la fin du combat (COMBAT_END)
  local ok, res = coroutine.resume(job)
  if not ok then M:Debug("plan:", res) job = nil stopTicker() return end
  if coroutine.status(job) == "dead" then
    job = nil
    stopTicker()
    if res then M:Publish(res) end
  else
    queueStep()
  end
end

M._cancelJob = function() job = nil stopTicker() end
function M:Start()
  if job or T:InCombat() then return end
  dirty, lastRun = false, (g("GetTime") and g("GetTime")()) or 0
  local only, prev = dirtySlots, M.plan
  dirtySlots = {}
  job = coroutine.create(function() return M.Plan(400, only, prev) end)
  step()
end

-- (5.0) recalcul seulement quand une entrée a changé (équipement, niveau, spé, quête prise : markDirty) ; un simple
-- changement de sac ne relance PAS le plan (sacs lus en direct par GearRent) ; aucun plan périodique, aucune méthode
-- AutoRefresh (le module ne compte pas dans le cycle du cœur) : un seul rappel, 5 s après la demande, hors combat.
function M.Busy() return job ~= nil end
--- un plan est demandé ou en cours (tests, page)
function M.Pending() return dirty or job ~= nil end
local kickQueued = false
local function kick(delay)
  if kickQueued or not M._active then return end
  kickQueued = true
  T:After(delay or 5, function()
    kickQueued = false
    if not dirty or job or not M._active then return end
    if T:InCombat() then M._kickAfterCombat = true return end
    M:Start()
  end)
end
M.Kick = kick
local function markDirty() dirty = true dirtySlots = nil srcCache = {} craftAt = -100 M._rent = nil M._bagStale = true kick() end
M.MarkDirty = markDirty
--- le premier plan n'est calculé que quand il sert : page Équipement ouverte, niveau gagné, marchand / HV ouvert,
--- quête rendue (zéro travail pour qui ne fait rien) ; ensuite seuls les changements le relancent
function M.WantPlan() if not M.plan and not job and not dirty then markDirty() end end
local PAIR = { [11] = 12, [12] = 11, [13] = 14, [14] = 13, [16] = 17, [17] = 16 }
--- un emplacement a changé (objet équipé) : seul lui (et son jumeau : anneaux, bijoux, mains) est à refaire
local function markSlot(slot)
  slot = tonumber(slot)
  if not slot or not SLOT_FR[slot] or not M.plan then return markDirty() end
  dirty = true
  M._rent, M._bagStale = nil, true
  if dirtySlots then dirtySlots[slot] = true if PAIR[slot] then dirtySlots[PAIR[slot]] = true end end
  kick()
end
M.MarkSlot = markSlot

---------------------------------------------------------------------------------------------------
-- Récompense de quête : le meilleur choix est entouré (jamais choisi à ta place)
---------------------------------------------------------------------------------------------------
local glow
local function hideGlow() if glow then glow:Hide() end end
local function showGlow(btn)
  if not btn or not G.CreateFrame then return end
  if not glow then
    glow = G.CreateFrame("Frame", "TreeAIRewardGlow", G.UIParent)
    local tex = glow:CreateTexture(nil, "OVERLAY")
    tex:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
    if tex.SetBlendMode then tex:SetBlendMode("ADD") end
    if tex.SetVertexColor then tex:SetVertexColor(0.2, 1, 0.3) end
    tex:SetAllPoints()
    glow.label = glow:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    glow.label:SetPoint("BOTTOM", glow, "TOP", 0, -4)
    glow.label:SetText("|cff33ff55TreeAI|r")
  end
  -- jamais enfant d'un bouton de Blizzard (récompense de quête) : simplement ancré dessus
  glow:SetFrameStrata("DIALOG")
  glow:ClearAllPoints()
  local icon = (type(btn.Icon) == "table" and btn.Icon) or (type(btn.icon) == "table" and btn.icon) or btn
  glow:SetPoint("TOPLEFT", icon, "TOPLEFT", -14, 14)
  glow:SetPoint("BOTTOMRIGHT", icon, "BOTTOMRIGHT", 14, -14)
  glow:Show()
end
local function rewardButton(i)
  local f = G.QuestInfoRewardsFrame
  if f and type(f.RewardButtons) == "table" then
    for _, b in ipairs(f.RewardButtons) do
      if b and b.type == "choice" and b.GetID and b:GetID() == i then return b end
    end
  end
  return G["QuestInfoRewardsFrameQuestInfoItem" .. i] or G["QuestInfoItem" .. i] or G["QuestRewardItem" .. i]
end
--- (4.1 LAYOUT) pour QoL (dialogues de quête) quand ce module est coupé : entourer le choix i / effacer
function M.RewardGlow(i)
  local b = rewardButton(i)
  if not b then return false end
  showGlow(b)
  return true
end
M.HideRewardGlow = hideGlow
function M.RewardGlowShown() return (glow and glow:IsShown()) and true or false end
--- Classe les choix : meilleur index, écart % (ou prix de vente), "upgrade" | "sell"
function M.RankQuestRewards()
  local n = try(G.GetNumQuestChoices) or 0
  if n < 2 then return nil end
  local bestUp, bestUpPct, bestDelta, bestSc, bestSell, bestSellV = nil, MIN_GAIN, nil, nil, nil, -1
  for i = 1, n do
    local link = try(G.GetQuestItemLink, "choice", i)
    local _, _, count, _, isUsable = try(G.GetQuestItemInfo, "choice", i)
    if link then
      if canUse(link) and isUsable ~= false then
        local sc = M.Score and M.Score(link) or nil
        local pct = (sc and sc.pct) or (not sc and upgradePct(link)) or nil
        local delta = sc and sc.delta or pct
        -- (5.0) le plus gros gain en VALEUR (pas en % de la pièce : +50 % d'un anneau < +10 % d'un arc)
        if pct and pct >= MIN_GAIN and (bestDelta == nil or (delta or 0) > bestDelta) then
          bestUp, bestUpPct, bestDelta, bestSc = i, pct, delta or 0, sc
        end
      end
      local sell = (tonumber((select(11, itemInfo(link)))) or 0) * (tonumber(count) or 1)
      if sell > bestSellV then bestSell, bestSellV = i, sell end
    end
  end
  if bestUp then return bestUp, bestUpPct, "upgrade", bestSc end
  if bestSell then return bestSell, bestSellV, "sell" end
end
function M.OnQuestComplete()
  if not opt("rewards") then return end
  T:After(0.3, function()
    local i, v, why, sc = M.RankQuestRewards()
    if not i then hideGlow() return end
    local link = try(G.GetQuestItemLink, "choice", i)
    local name = (link and itemInfo(link)) or ("choix " .. i)
    showGlow(rewardButton(i))
    M.lastReward = { i = i, why = why }
    local txt
    if why == "upgrade" then
      local num = sc and sc.text and sc.text ~= "emplacement vide" and (" (" .. sc.text .. ")") or ""
      txt = v >= 999 and ("Prends « " .. name .. " » : ton emplacement est vide")
        or ("Prends « " .. name .. " » : " .. pctText(v) .. " que ce que tu portes" .. num)
    else
      txt = "Prends « " .. name .. " » : aucune amélioration, c'est elle qui se vend le plus (" .. money(v) .. ")"
    end
    local tip = { "Récompense entourée en vert : c'est toi qui cliques." }
    for _, l in ipairs(sc and sc.lines or {}) do tip[#tip + 1] = l end
    M:Suggest("reward", txt, 2, { ttl = 60, cooldown = 5, sound = false, tooltip = tip })
  end)
end

---------------------------------------------------------------------------------------------------
-- Infobulles d'objets : mieux ou pas pour toi (en mots), prix de vente (et prix Auctionator s'il est installé)
---------------------------------------------------------------------------------------------------
local function auctionValue(link)
  local A = G.Auctionator
  local f = A and A.API and A.API.v1 and A.API.v1.GetAuctionPriceByItemLink
  if type(f) ~= "function" then return nil end
  return tonumber((try(f, "TreeAI", link)))
end
local VERDICT_COLOR = { good = { 0.2, 1, 0.3 }, same = { 1, 0.9, 0.3 }, bad = { 1, 0.4, 0.4 } }
function M.DecorateItem(tip, link)
  if not opt("tooltip") or not tip or not tip.AddLine or not link then return end
  local _, _, _, _, _, _, _, _, equipLoc, _, sell = itemInfo(link)
  if equipLoc and SLOTS[equipLoc] then
    local sc = M.Score and M.Score(link) or nil
    local label, text, kind = nil, nil, nil
    if sc and M.ScoreLine then label, text, kind = M.ScoreLine(sc) end
    if label then
      local c = VERDICT_COLOR[kind] or (kind == "warn" and VERDICT_COLOR.same) or { 0.6, 0.6, 0.6 }
      tip:AddLine("TreeAI : " .. label:gsub("^%u", string.lower) .. ((text and text ~= "") and (" — " .. text) or ""), c[1], c[2], c[3])
    else
      local ok, why = canUse(link)
      if not ok then tip:AddLine("TreeAI : pas pour toi (" .. (why or "?") .. ")", 0.6, 0.6, 0.6) end
    end
  end
  local ah = auctionValue(link)
  sell = tonumber(sell) or 0
  if sell > 0 or ah then
    local txt = sell > 0 and ("Se vend " .. money(sell) .. " au marchand") or "Ne se vend pas au marchand"
    if ah then
      txt = txt .. " · HV (Auctionator) : " .. money(ah)
      if sell > 0 then txt = txt .. ((ah > sell * 1.2) and " → vends à l'HV" or " → vends au marchand") end
    end
    tip:AddLine(txt, 0.8, 0.8, 0.8)
  end
end
local hooked = false
local function hookTooltips()
  if hooked then return end
  hooked = true
  local TDP, E = G.TooltipDataProcessor, G.Enum and G.Enum.TooltipDataType
  if TDP and TDP.AddTooltipPostCall and E and E.Item then
    TDP.AddTooltipPostCall(E.Item, function(tip, data)
      if not M:IsEnabled() or (T.ItemInfo and T.ItemInfo.Owns and T.ItemInfo.Owns()) then return end   -- (4.0.2) ItemInfo écrit
      local link = tip and tip.GetItem and select(2, tip:GetItem())
      if not link and data and data.guid and G.C_Item and G.C_Item.GetItemLinkByGUID then
        link = try(G.C_Item.GetItemLinkByGUID, data.guid)
      end
      pcall(M.DecorateItem, tip, link)
    end)
  else
    for _, tip in ipairs({ G.GameTooltip, G.ItemRefTooltip }) do
      if tip and tip.HookScript then
        pcall(tip.HookScript, tip, "OnTooltipSetItem", function(self)
          if not M:IsEnabled() or (T.ItemInfo and T.ItemInfo.Owns and T.ItemInfo.Owns()) then return end
          local link = self.GetItem and select(2, self:GetItem())
          pcall(M.DecorateItem, self, link)
        end)
      end
    end
  end
end

function M:OnEnable()
  if T.RegisterTab then pcall(T.RegisterTab, T, "gear", "Équipement", 45) end
  hookTooltips()
  local function equipChanged(_, _, slot)
    local V = M.Value
    if V and V.EquipChanged then V.EquipChanged() end
    if V and V.ResetSkills then V.ResetSkills() end
    if not M.plan then M._rent, M._bagStale = nil, true return end   -- pas encore de plan : rien à refaire
    if slot then markSlot(slot) else markDirty() end
  end
  self:RegisterEvent("QUEST_COMPLETE", function() M.OnQuestComplete() end)
  self:RegisterEvent("QUEST_FINISHED", function() hideGlow() M:Clear("reward") end)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", equipChanged)
  self:RegisterEvent("PLAYER_LEVEL_UP", function()
    if M.SpecDirty then M.SpecDirty(false) end
    if M.ResetWeights then M.ResetWeights() end
    local V = M.Value
    if V and V.EquipChanged then V.EquipChanged() end
    markDirty()   -- un niveau de plus : de nouveaux objets deviennent portables
  end)
  for _, ev in ipairs({ "MERCHANT_SHOW", "AUCTION_HOUSE_SHOW", "QUEST_TURNED_IN" }) do
    self:RegisterEvent(ev, function() M.WantPlan() end)   -- moments où un achat / une récompense se décide
  end
  -- le joueur joue (il se déplace, 30 s après l'activation) : un premier plan, une fois ; immobile / absent : rien
  local enabledAt = (g("GetTime") and g("GetTime")()) or 0
  self:RegisterEvent("PLAYER_STARTED_MOVING", function()
    if M.plan or job then M:UnregisterEvent("PLAYER_STARTED_MOVING") return end
    if ((g("GetTime") and g("GetTime")()) or 0) - enabledAt < 30 then return end
    M:UnregisterEvent("PLAYER_STARTED_MOVING")
    M.WantPlan()
  end)
  -- (5.0) une quête prise ne relance le plan que si elle apporte une récompense d'équipement nouvelle
  local rewardSig, rewardQueued = nil, false
  self:RegisterEvent("QUEST_ACCEPTED", function()
    if rewardQueued then return end
    rewardQueued = true
    T:After(2, function()
      rewardQueued = false
      local ids = {}
      for id in pairs(M.ScanLogRewards()) do ids[#ids + 1] = id end
      table.sort(ids)
      local sig = table.concat(ids, ",")
      if rewardSig ~= nil and sig ~= rewardSig and M.plan then markDirty() end
      rewardSig = sig
    end)
  end)
  -- (5.0) en groupe / seul : l'Endurance ne pèse pas pareil → poids relus quand ça bascule (pas à chaque mouvement du groupe)
  local grouped = try(g("IsInGroup")) and true or false
  self:RegisterEvent("GROUP_ROSTER_UPDATE", function()
    local now = try(g("IsInGroup")) and true or false
    if now ~= grouped then grouped = now if M.ResetWeights then M.ResetWeights() end if M.plan then markDirty() end end
  end)
  self:On("COMBAT_END", function()   -- (un seul rappel par message et par module : les trois parties passent ici)
    if job then T:After(1, function() step() end)
    elseif M._kickAfterCombat then M._kickAfterCombat = false kick(2) end
    if M.SpecCombatEnd then M.SpecCombatEnd() end
    if M.RentCombatEnd then M.RentCombatEnd() end
    if M.Value and M.Value.CombatEnd then M.Value.CombatEnd() end
  end)
  self:On("SUMMARY", function(m) if m.plan then local s = T:Summary() if type(s) == "table" then s.gear = M.Summary(m.plan) end end end)
  if M.SpecEnable then M.SpecEnable() end
  if M.RentEnable then M.RentEnable() end
  if dirty then kick(8) end
end

function M:OnDisable()
  if M._cancelJob then M._cancelJob() end
  job = nil
  hideGlow()
  if M.RentDisable then M.RentDisable() end
  self:Clear("upgrade")
  self:Clear("reward")
  self:Clear("spec")
  self:Clear("mount")
end

if T.RegisterSlash then
  T:RegisterSlash("equipement", function()
    T:UserAction(function() markDirty() M:ScanNow() selectTab() end)
  end, "equipement : améliorations par emplacement (onglet « Équipement »)")
end
