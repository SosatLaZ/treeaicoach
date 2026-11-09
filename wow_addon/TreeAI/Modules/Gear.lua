-- TreeAI — module Équipement (agent GEAR, CLEAN 4.0) : tout ce qui juge un objet, informatif seulement.
--  • Moteur d'évaluation (repris du module Boost) : poids de stats par classe / arbre le plus rempli, objets portables,
--    comparaison avec ce que tu portes. Les poids sont ESTIMÉS : on dit « mieux / nettement mieux », jamais « +21 % ».
--  • Infobulles d'objets : « TreeAI : nettement mieux que ce que tu portes » / « pas pour toi : … », prix de vente.
--  • Récompense de quête : le bouton du meilleur choix est entouré (jamais choisi à ta place).
--  • Plan par emplacement : meilleures améliorations « bientôt » (niveau requis ≤ niveau + horizon) parmi ~5 000 objets
--    du client (Data/Gear.lua) + tes sacs + les récompenses des quêtes de ton journal, et OÙ les obtenir (sacs,
--    récompense, donjon, monstre, marchand, artisanat, HV) ; onglet « Équipement ».
--  • Chasseur : DPS / vitesse de l'arme à distance, munitions assorties (flèches / balles), mailles au niv 40.
-- Rien n'est équipé, acheté ou cliqué à la place du joueur. QuestieDB en LECTURE seule. Calculs hors combat,
-- par tranches (coroutine) ; Lua 5.1, aucune globale, toutes les API testées.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}
local G = _G

local M = T:RegisterModule("Gear", {
  title = "Équipement",
  defaults = { enabled = true, tab = true, suggest = true, horizon = 3, ah = true, quests = true,
               dungeons = true, crafted = true, vendors = true, maxPerSlot = 3, tooltip = true, rewards = true },
  charDefaults = { shopping = {} },
  options = {
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
  if not G.GetTalentTabInfo then return nil end
  local best, bestPts = nil, 0
  for tab = 1, (try(G.GetNumTalentTabs) or 3) do
    local r = pack(pcall(G.GetTalentTabInfo, tab))
    if r[1] then
      for i = 2, r.n do -- Era : name, icon, points… ; Mainline : id, name, desc, icon, points…
        local v = r[i]
        if type(v) == "number" and v >= 0 and v <= 61 and i >= 3 then
          if v > bestPts then best, bestPts = tab, v end
          break
        end
      end
    end
  end
  return best
end
local function weights()
  local cw = WEAPONS[playerClass()] and WEIGHTS[playerClass()] or WEIGHTS.WARRIOR
  local tab = specTab()
  return (tab and cw[tab]) or cw.default
end
local liveStats = {}
local function itemStats(link)
  if not link then return nil end
  local c = liveStats[link]
  if c then return c end
  local st
  if G.C_Item and G.C_Item.GetItemStats then st = try(G.C_Item.GetItemStats, link) end
  if not st and G.GetItemStats then st = try(G.GetItemStats, link) end
  if type(st) ~= "table" then return nil end
  liveStats[link] = st
  return st
end
--- Score d'une table de stats (clés ITEM_MOD_*, comme GetItemStats) pour un emplacement
local function scoreStats(st, slot, w)
  if type(st) ~= "table" then return nil end
  w = w or weights()
  local sc = 0
  for k, v in pairs(st) do
    if k == S.DPS then sc = sc + (tonumber(v) or 0) * ((slot == 18) and w.RDPS or w.MDPS)
    elseif w[k] then sc = sc + (tonumber(v) or 0) * w[k] end
  end
  return sc
end
local function scoreLink(link, slot, w) return scoreStats(itemStats(link), slot, w) end
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
  end
  if minLevel and minLevel > lvl then return true, "niveau " .. minLevel .. " requis" end
  return true
end
--- Écart de score avec l'équipement porté, en % (nil = stats inconnues ; 999 = emplacement vide)
local function upgradePct(link)
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
--- Stats (clés ITEM_MOD du moteur) d'un objet du client, calculées comme l'infobulle.
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
  -- modules optionnels (contrats lus seulement s'ils existent)
  local Dg = mod("Dungeon")
  if Dg and Dg.ItemSource and opt("dungeons") then
    local s = try(Dg.ItemSource, id)
    if type(s) == "table" and s.text then
      -- (4.3 V43) faisable à 4 : niveau ≥ minimum conseillé du donjon (- 2 à quatre)
      add("boss", s.text, tonumber(s.min) or 60, 0, { dkey = s.key, dmin = s.dmin,
        now = not tonumber(s.dmin) or c.level >= tonumber(s.dmin) - 2 })
    end
  end
  local Pf = mod("Prof")
  if Pf and Pf.CraftInfo and opt("crafted") then
    local s = try(Pf.CraftInfo, id)
    if type(s) == "table" and s.text then add("craft", s.text, tonumber(s.min) or 15, tonumber(s.cost) or 0, { now = s.can ~= false }) end
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
  local A = mod("Auction")
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
  if not pct or pct < MIN_GAIN then return nil end
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
    -- (4.3 V43) « le meilleur maintenant » : les 6 plus gros gains portables dès aujourd'hui, à part (avant la coupe à 10)
    local nowC = {}
    for _, cd in ipairs(l) do if (cd.need or 0) <= c.level and #nowC < 6 then nowC[#nowC + 1] = cd end end
    plan.now, plan.nowUsed = plan.now or {}, plan.nowUsed or {}
    plan.now[slot] = M.BestNow(nowC, c, plan.nowUsed)
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
function M.BestNow(cands, c, used)
  local best
  for _, cd in ipairs(cands) do
    if not (used and used[cd.id]) then
      local src
      for _, e in ipairs(M.Sources(cd.id, c)) do if e.now then src = e break end end
      if src and (not best or cd.pct > best.pct or (cd.pct == best.pct and src.effort < best.nowSrc.effort)) then
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
function M.GainText(cd, worn, slot)
  local a = cd and M.StatsOf(cd.id)
  if not a then return nil end
  local b = (worn and worn.id and M.StatsOf(worn.id)) or (worn and worn.link and itemStats(worn.link)) or {}
  local out = {}
  local function n(t, k) return tonumber(t[k]) or 0 end
  local dd = n(a, S.DPS) - n(b, S.DPS)
  if (slot == 18 or slot == 16) and math.abs(dd) >= 0.1 then
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
    1, { "Recalcule tout de suite (hors combat).", "Sinon : automatique à chaque changement d'équipement / de sac / de niveau." }, rescan)
  lines[#lines].control, lines[#lines].label = true, "Recalculer"
  add(("Horizon : niveau +%d"):format(c.horizon), 1,
    { "Montre aussi les objets que tu pourras porter bientôt.", "Clic : +2 (0 à 10)." }, bumpHorizon)
  lines[#lines].control, lines[#lines].label = true, ("Horizon +%d"):format(c.horizon)
  add("Priorité des stats : " .. priorityText(c), 1, { "Poids de stats estimés (classe, arbre de talents le plus rempli) :",
    "les comparaisons disent « mieux » ou « nettement mieux », jamais un pourcentage.",
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
  -- règle d'affichage : une ligne par emplacement qui a une VRAIE piste (« Torse : Gilet → Gilet agile (+12 %) ·
  -- marchand ») ; durée, coût, score et alternatives dans l'infobulle ; emplacements sans piste sûre (butin
  -- aléatoire / inconnu) regroupés en UNE ligne ; « rien de mieux » = pas de ligne.
  local nUp, weak, none = 0, {}, 0
  for _, slot in ipairs(SLOT_LIST) do
    local e = plan.cur[slot] or {}
    local best = plan.slots[slot] or {}
    local cd = best[1]
    local head = e.name and ("%s : %s"):format(SLOT_FR[slot] or slot, e.name) or ((SLOT_FR[slot] or slot) .. " (vide)")
    if cd and cd.src and cd.src.kind == "world" then
      nUp = nUp + 1
      weak[#weak + 1] = ("%s : %s — %s"):format(SLOT_FR[slot] or slot, cd.name or "?", cd.src.text or "?")
    elseif cd then
      nUp = nUp + 1
      local s = cd.src or {}
      local lvl = (cd.need or 0) > c.level and (" — niv " .. cd.need) or ""
      local gain = (cd.pct and cd.pct < 999) and (" (" .. pctText(cd.pct) .. ")") or ""
      local where = tostring(s.text or "?"):gsub(" — .*$", "")
      local text = ("%s → %s%s%s · %s"):format(head, cd.name or "?", gain, weaponTxt(cd), where)
      local tip = { (cd.pct and cd.pct >= 999) and ("Emplacement vide : tout objet est un gain" .. lvl)
          or (pctText(cd.pct):gsub("^%l", string.upper) .. " que ce que tu portes" .. lvl),
        "Où : " .. (s.text or "?") }
      -- effort : seulement ce qui se mesure (sacs, marchand, quête du journal, HV) ; fabriqué / butin / boss : pas de
      -- durée inventée, le prix seul s'il est connu
      if NO_EFFORT[s.kind] then
        if (s.cost or 0) > 0 then tip[#tip + 1] = "Prix : " .. money(s.cost) end
      else
        tip[#tip + 1] = "Effort : " .. (s.kind == "bag" and "maintenant" or dur(s.min)) .. (((s.cost or 0) > 0) and (" · " .. money(s.cost)) or "")
      end
      if e.name then tip[#tip + 1] = ("Porté : %s (score %d)"):format(e.name, floor((e.score or 0) + 0.5)) end
      for i = 2, #(cd.sources or {}) do tip[#tip + 1] = "  ou " .. cd.sources[i].text end
      for i = 2, #best do
        local o = best[i]
        tip[#tip + 1] = ("Alternative : %s (%s) — %s"):format(o.name or "?", pctText(o.pct), o.src and o.src.text or "?")
      end
      local prio = (s.kind == "bag" or (cd.pct >= BIG_GAIN and (cd.need or 0) <= c.level)) and 2 or 1
      local open = M.expanded[slot]
      tip[#tip + 1] = open and "Clic : replier" or "Clic : voir toutes les sources et alternatives"
      add(text, prio, tip, function() M.expanded[slot] = not M.expanded[slot] republish() end)
      if open then
        for i = 2, #(cd.sources or {}) do add(("      ou %s · %s"):format(cd.sources[i].text, dur(cd.sources[i].min)), 1) end
        for i = 2, #best do
          local o = best[i]
          add(("      alternative : %s (%s) — %s"):format(o.name or "?", pctText(o.pct), o.src and o.src.text or "?"), 1)
        end
      end
    else
      none = none + 1
    end
  end
  if #weak > 0 then
    local names = {}
    for i, w in ipairs(weak) do if i <= 4 then names[#names + 1] = (w:match("^(.-) :")) end end
    weak[#weak + 1] = "Pas de source sûre : garde un œil sur l'HV et le butin des monstres."
    add(("Surveille l'HV et le butin : %s%s"):format(table.concat(names, ", "), #weak - 1 > 4 and "…" or ""), 1, weak)
  end
  local sh = plan.shopping or {}
  if #sh > 0 then
    add(("Liste de courses : %d objet(s), %s au total (tu as %s)"):format(#sh, money(sh.total or 0), money(c.money)), 2)
    for _, e in ipairs(sh) do
      add(("  %s %s (%s) — %s%s"):format(e.affordable and "•" or "×", e.name or "?", pctText(e.pct), e.where or "",
        e.affordable and "" or " (pas assez d'or)"), e.affordable and 1 or 1)
    end
  end
  if nUp == 0 then add("Équipement au top pour ton niveau : rien à chercher pour l'instant.", 1)
  elseif none > 0 and lines[1] and lines[1].control then
    local tip = lines[1].tooltip
    if type(tip) == "table" then tip[#tip + 1] = ("%d emplacement(s) déjà au mieux pour ton niveau."):format(none) end
  end
  return lines
end

function M.Summary(plan)
  local out = { level = plan.ctx.level, spec = plan.ctx.spec, priority = priorityText(plan.ctx), slots = {}, shopping = {} }
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
  if opt("tab") and T.SetTabLines then pcall(T.SetTabLines, T, "gear", M.Lines(plan)) end
  local s = T.Summary and T:Summary()
  if type(s) == "table" then s.gear = M.Summary(plan) end
  self.char.shopping = {}
  for _, e in ipairs(plan.shopping or {}) do self.char.shopping[#self.char.shopping + 1] = { id = e.id, cost = e.cost } end
  if not opt("suggest") then self:Clear("upgrade") return end
  -- meilleure amélioration immédiate : sacs d'abord, sinon la plus rentable portable maintenant ; « Maintenant » ne
  -- reçoit qu'une action concrète (équiper, acheter au marchand, choisir en récompense d'une quête du journal) :
  -- fabriqué / butin / boss / HV restent dans l'onglet « Équipement »
  local top
  for _, slot in ipairs(SLOT_LIST) do
    local cd
    for _, c in ipairs(plan.slots[slot] or {}) do if c.src and NOW_KIND[c.src.kind] then cd = c break end end
    if cd and (cd.need or 0) <= plan.ctx.level and cd.src and (not top or cd.value > top.value or cd.src.kind == "bag") then
      if not (top and top.src.kind == "bag" and cd.src.kind ~= "bag") then top = cd top.slot = slot end
    end
  end
  -- verbe selon la source (on n'« équipe » que ce qui est dans les sacs) ; jamais une piste vague dans « Maintenant »
  local VERB = { bag = "Équipe", vendor = "Achète", ah = "Achète à l'HV", craft = "Fais fabriquer", quest = "Choisis en récompense",
    boss = "Vise en donjon", drop = "Va chercher" }
  if top and top.name and top.src.kind ~= "world" and (top.src.kind == "bag" or top.pct >= 10) then
    local gain = top.pct < 999 and (", " .. pctText(top.pct)) or ""
    self:Suggest("upgrade", ("%s %s (%s%s)"):format(VERB[top.src.kind] or "Vise", top.name, SLOT_FR[top.slot] or "?", gain),
      top.src.kind == "bag" and 2 or 1, { ttl = 600, cooldown = 300, onClick = selectTab,
        tooltip = { "Où : " .. (top.src.text or "?"), "Clic : onglet « Équipement »." } })
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
  local bestUp, bestUpPct, bestSell, bestSellV = nil, MIN_GAIN, nil, -1
  for i = 1, n do
    local link = try(G.GetQuestItemLink, "choice", i)
    local _, _, count, _, isUsable = try(G.GetQuestItemInfo, "choice", i)
    if link then
      if canUse(link) and isUsable ~= false then
        local pct = upgradePct(link)
        if pct and pct >= bestUpPct then bestUp, bestUpPct = i, pct end
      end
      local sell = (tonumber((select(11, itemInfo(link)))) or 0) * (tonumber(count) or 1)
      if sell > bestSellV then bestSell, bestSellV = i, sell end
    end
  end
  if bestUp then return bestUp, bestUpPct, "upgrade" end
  if bestSell then return bestSell, bestSellV, "sell" end
end
function M.OnQuestComplete()
  if not opt("rewards") then return end
  T:After(0.3, function()
    local i, v, why = M.RankQuestRewards()
    if not i then hideGlow() return end
    local link = try(G.GetQuestItemLink, "choice", i)
    local name = (link and itemInfo(link)) or ("choix " .. i)
    showGlow(rewardButton(i))
    local txt
    if why == "upgrade" then
      txt = v >= 999 and ("Prends « " .. name .. " » : ton emplacement est vide")
        or ("Prends « " .. name .. " » : " .. pctText(v) .. " que ce que tu portes")
    else
      txt = "Prends « " .. name .. " » : aucune amélioration, c'est elle qui se vend le plus (" .. money(v) .. ")"
    end
    M:Suggest("reward", txt, 2, { ttl = 60, cooldown = 5, sound = false,
      tooltip = { "Récompense entourée en vert : c'est toi qui cliques.", "Poids de stats estimés pour ta classe." } })
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
    local ok, why = canUse(link)
    if not ok then
      tip:AddLine("TreeAI : pas pour toi (" .. (why or "?") .. ")", 0.6, 0.6, 0.6)
    else
      local word, kind = M.Verdict(upgradePct(link))
      if word then
        local text = (kind == "good" and word == "emplacement vide") and "TreeAI : emplacement vide, équipe-le"
          or (word == "équivalent" and "TreeAI : équivalent à ce que tu portes")
          or ("TreeAI : " .. word .. " que ce que tu portes")
        local c = VERDICT_COLOR[kind] or VERDICT_COLOR.same
        tip:AddLine(text .. (why and (" (" .. why .. ")") or ""), c[1], c[2], c[3])
      end
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
  self:RegisterEvent("QUEST_COMPLETE", function() M.OnQuestComplete() end)
  self:RegisterEvent("QUEST_FINISHED", function() hideGlow() M:Clear("reward") end)
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
  hideGlow()
  self:Clear("upgrade")
  self:Clear("reward")
end

if T.RegisterSlash then
  T:RegisterSlash("equipement", function()
    T:UserAction(function() markDirty() M:ScanNow() selectTab() end)
  end, "equipement : améliorations par emplacement (onglet « Équipement »)")
end
