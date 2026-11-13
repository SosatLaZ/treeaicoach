-- TreeAI — Équipement, partie « Valeur » (agent GEAR 5.0). Fait partie du module Gear (pas un module de plus).
-- « mieux calculer tout » : chaque objet devient UN nombre, avec son détail, et jamais une devinette.
--  • Classes / spés à dégâts physiques (chasseur, voleur, guerrier Armes / Fureur, paladin Vindicte, chaman
--    Amélioration, druide farouche) : le nombre est en DPS, avec les règles du jeu (voir research/gear5.md) :
--      14 PA = 1 DPS · chasseur : 1 Agi = 2 PA à distance · Force → PA selon la classe · 1 % de critique = +1 % des
--      dégâts · 1 % de toucher = +1 % des dégâts tant que le plafond n'est pas atteint (5 % en extérieur, 9 % au 60)
--      · scores de toucher / critique convertis par le jeu lui-même (GetCombatRatingBonusForCombatRatingValue),
--      sinon la conversion des tables d'objets (10 toucher = 1 %, 14 critique = 1 %) · arme à distance du chasseur :
--      DPS × (1 + tirs spéciaux × vitesse) → à DPS égal, une arme lente vaut plus (Flèches multiples, Visée)
--      · l'arme de mêlée du chasseur ne compte que par ses stats.
--    Endurance / Intelligence / Esprit / Armure : « confort », converties à part (solo > groupe), dites dans le détail.
--  • Lanceurs de sorts, soigneurs, tanks : points pondérés par spé et tranche de niveau, affichés en « +12 % » par
--    rapport à la pièce portée (pas de DPS inventé pour un soigneur).
--  • Stats d'un objet : GetItemStats (jeu), sinon tables du client (Data/Gear.lua, objets sans suffixe aléatoire),
--    sinon lecture de l'infobulle (C_TooltipInfo) ; rien de lisible → « inconnu », jamais de score.
--  • T.Gear.Score(lien) → { known, usable, why, slot, replaces, delta, dps, pct, unit, text, kind, lines } ; cache par
--    lien (O(1) à la 2e demande), invalidé par l'équipement, les sacs, le niveau, la spé.
-- Lua 5.1, aucune globale, toutes les API sous pcall, valeurs secrètes écartées.
local tonumber = function(v) return _G.tonumber(v) end
local T = _G.TreeAI
local M = T and T.Gear
if not (T and M and M.Engine) then return end
local API = T.API or {}
local G = _G
local ENG = M.Engine
local S = ENG.STAT_KEYS

local floor, max, min, abs = math.floor, math.max, math.min, math.abs
local fmt = string.format
local function pass(ok, ...) if ok then return ... end local sw = _G.TreeAI and _G.TreeAI.Swallow if sw then sw((...), "GearValue") end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return v ~= nil and type(f) == "function" and try(f, v) and true or false end
local function pub(v) if v == nil or secret(v) then return nil end return v end
local function num(v) v = pub(v) if type(v) == "number" then return v end return nil end
local function str(v) v = pub(v) if type(v) == "string" and v ~= "" then return v end return nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function class() return T.PlayerClass and T:PlayerClass() or nil end
local function now() return num(try(g("GetTime"))) or 0 end
local function inCombat() return (T.InCombat and T:InCombat()) and true or false end
local function itemID(x) if type(x) == "number" then return x end return tonumber(type(x) == "string" and x:match("item:(%d+)") or nil) end
local function dec(x) return (fmt("%.1f", x):gsub("%.", ",")) end
local function signed(x) return (x >= 0 and "+" or "-") .. dec(abs(x)) end

local V = {}
M.Value = V
V.AP_PER_DPS = 14          -- 14 points de puissance d'attaque = 1 DPS (règle du jeu)
V.REF_SPEED = 2.8          -- vitesse de référence des tirs du chasseur
V.HIT_CAP, V.HIT_CAP_60 = 5, 9            -- % de ratés : cible de ton niveau / boss de raid au 60
V.SPELL_HIT_CAP, V.SPELL_HIT_CAP_60 = 3, 16
-- PA par point de Force / d'Agilité (mêlée, à distance) et Agilité pour 1 % de critique au niveau 60
V.CONV = {
  HUNTER = { str = 1, agi = 1, ragi = 2, agiCrit = 53 }, ROGUE = { str = 1, agi = 1, ragi = 1, agiCrit = 29 },
  WARRIOR = { str = 2, agi = 0, ragi = 1, agiCrit = 20 }, PALADIN = { str = 2, agi = 0, agiCrit = 20 },
  SHAMAN = { str = 2, agi = 0, agiCrit = 20 }, DRUID = { str = 2, agi = 1, agiCrit = 20 },
}
-- score → % : indice du jeu (CR_*) et conversion des tables d'objets (points pour 1 %)
local RATING = {
  HIT = { melee = 6, ranged = 7, spell = 8, per = 10, perSpell = 8 },
  CRIT = { melee = 9, ranged = 10, spell = 11, per = 14, perSpell = 14 },
  DODGE = { melee = 3, per = 12 }, PARRY = { melee = 4, per = 15 }, BLOCK = { melee = 5, per = 5 },
}
-- clés de stats en plus de celles du moteur (mêmes noms que GetItemStats)
local K = {
  MP5 = "ITEM_MOD_POWER_REGEN0_SHORT", DODGE = "ITEM_MOD_DODGE_RATING_SHORT", PARRY = "ITEM_MOD_PARRY_RATING_SHORT",
  BLOCK = "ITEM_MOD_BLOCK_RATING_SHORT", XARMOR = "ITEM_MOD_EXTRA_ARMOR_SHORT",
}
for k, v in pairs(S) do K[k] = v end
V.KEYS = K
-- variantes d'une même stat selon les clients → clé du moteur
local ALIAS = {
  ITEM_MOD_HIT_MELEE_RATING_SHORT = K.HIT, ITEM_MOD_HIT_RANGED_RATING_SHORT = K.HIT, ITEM_MOD_HIT_SPELL_RATING_SHORT = K.HIT,
  ITEM_MOD_CRIT_MELEE_RATING_SHORT = K.CRIT, ITEM_MOD_CRIT_RANGED_RATING_SHORT = K.CRIT, ITEM_MOD_CRIT_SPELL_RATING_SHORT = K.CRIT,
  ITEM_MOD_MANA_REGENERATION_SHORT = K.MP5, ITEM_MOD_SPELL_DAMAGE_DONE = K.SPD, ITEM_MOD_SPELL_HEALING_DONE = K.HEAL,
}
local STAT_FR = { [K.AGI] = "Agilité", [K.STR] = "Force", [K.STA] = "Endurance", [K.INT] = "Intelligence", [K.SPI] = "Esprit",
  [K.AP] = "PA", [K.RAP] = "PA à distance", [K.SP] = "Puissance des sorts", [K.SPD] = "Dégâts des sorts", [K.HEAL] = "Soins",
  [K.HIT] = "Toucher", [K.CRIT] = "Critique", [K.DEF] = "Défense", [K.ARMOR] = "Armure", [K.XARMOR] = "Armure",
  [K.MP5] = "Mana / 5 s", [K.DODGE] = "Esquive", [K.PARRY] = "Parade", [K.BLOCK] = "Blocage" }
V.STAT_FR = STAT_FR
local COMFORT = { [K.STA] = "survie", [K.INT] = "mana", [K.SPI] = "récupération", [K.ARMOR] = "survie", [K.XARMOR] = "survie",
  [K.MP5] = "mana" }

---------------------------------------------------------------------------------------------------
-- Profils par classe / arbre. unit = "dps" (modèle physique) ou "pts" (points pondérés, stat principale = 1).
-- pts : valeurs pour la montée ; at60 = ce qui change au niveau 58+ ; low = avant le niveau 20.
-- HITP / CRITP = points pour 1 % de toucher / de critique (le score de l'objet est converti en %).
---------------------------------------------------------------------------------------------------
local CASTER = { unit = "pts", role = "caster", w = { INT = 1, SP = 1.3, SPD = 1.3, SPI = 0.45, STA = 0.45, MP5 = 2, HITP = 9, CRITP = 9, RDPS = 1.5 },
  low = { SPI = 0.7, STA = 0.5 }, at60 = { SP = 2.2, SPD = 2.2, SPI = 0.2, STA = 0.3, HITP = 28, CRITP = 22 } }
local HEALER = { unit = "pts", role = "heal", w = { INT = 1, HEAL = 1, SP = 1, SPD = 0.1, SPI = 0.8, STA = 0.4, MP5 = 2.5, CRITP = 5, RDPS = 1 },
  low = { SPD = 0.8, STA = 0.5 }, at60 = { HEAL = 1.2, SP = 1.2, SPI = 0.7, MP5 = 3, STA = 0.3 } }
local TANK = { unit = "pts", role = "tank", w = { STA = 1, DEF = 1, ARMOR = 0.1, XARMOR = 0.1, AGI = 0.6, STR = 0.6, AP = 0.2, HITP = 6,
  DODGEP = 12, PARRYP = 12, BLOCKP = 6, MDPS = 1.5, OHDPS = 0 } }
local PROFILE = {
  HUNTER = { default = { unit = "dps", kind = "ranged", prim = "AGI", int = 0.15, spi = 0.05 } },
  ROGUE = { default = { unit = "dps", kind = "melee", prim = "AGI", dual = true } },
  WARRIOR = { default = { unit = "dps", kind = "melee", prim = "STR", dual = true, spi = 0.05 }, [3] = TANK },
  PALADIN = { default = { unit = "dps", kind = "melee", prim = "STR", int = 0.25, spi = 0.05, sp = 0.2 },
    [1] = { unit = "pts", role = "heal", w = { INT = 1, HEAL = 1, SP = 1, SPI = 0.3, STA = 0.4, MP5 = 2.5, CRITP = 8 } },
    [2] = { unit = "pts", role = "tank", w = { STA = 1, DEF = 1, ARMOR = 0.1, XARMOR = 0.1, STR = 0.6, AGI = 0.4, INT = 0.3, SP = 0.3,
      HITP = 6, DODGEP = 12, PARRYP = 12, BLOCKP = 8, MDPS = 1.5 } } },
  SHAMAN = { default = { unit = "dps", kind = "melee", prim = "STR", int = 0.3, spi = 0.05, sp = 0.2 },
    [1] = { unit = "pts", role = "caster", w = { INT = 1, SP = 1.2, SPD = 1.2, SPI = 0.3, STA = 0.4, MP5 = 2, HITP = 9, CRITP = 9 },
      at60 = { SP = 2, SPD = 2, HITP = 26, CRITP = 22, STA = 0.3 } },
    [3] = HEALER },
  DRUID = { default = { unit = "pts", role = "hybride", w = { STR = 0.6, AGI = 0.6, INT = 0.6, STA = 0.5, SPI = 0.4, SP = 0.5, AP = 0.3,
      ARMOR = 0.02, MDPS = 0.5 } },
    [1] = CASTER, [2] = { unit = "dps", kind = "melee", prim = "STR", form = true, int = 0.1, spi = 0.05 }, [3] = HEALER },
  MAGE = { default = CASTER },
  WARLOCK = { default = { unit = "pts", role = "caster", w = { INT = 0.8, SP = 1.3, SPD = 1.3, STA = 0.7, SPI = 0.3, MP5 = 1.5, HITP = 9, CRITP = 7, RDPS = 1.5 },
    at60 = { SP = 2.2, SPD = 2.2, STA = 0.4, INT = 0.7, HITP = 28, CRITP = 20 } } },
  PRIEST = { default = HEALER,
    [3] = { unit = "pts", role = "caster", w = { SP = 1.3, SPD = 1.3, INT = 0.8, SPI = 0.6, STA = 0.5, MP5 = 1.5, HITP = 9, CRITP = 4, RDPS = 1.5 },
      at60 = { SP = 2.2, SPD = 2.2, SPI = 0.3, STA = 0.3, HITP = 28, CRITP = 12 } } },
}
V.PROFILE = PROFILE
local ROLE_FR = { caster = "dégâts des sorts", heal = "soins", tank = "tank", hybride = "polyvalent", ranged = "dégâts à distance",
  melee = "dégâts de mêlée" }

function V.Profile(cls, tree)
  local p = PROFILE[cls] or PROFILE.WARRIOR
  return (tree and p[tree]) or p.default, (tree and p[tree]) and tree or nil
end
--- tranche de niveau des poids
function V.Bracket(lvl) lvl = lvl or level() if lvl >= 58 then return "at60" elseif lvl < 20 then return "low" end return "mid" end

---------------------------------------------------------------------------------------------------
-- Conversions lues dans le jeu
---------------------------------------------------------------------------------------------------
local ratingCache = {}
--- % donné par 1 point de score (toucher, critique…) à ton niveau → valeur, vrai si lue dans le jeu
function V.RatingPct(stat, kind)
  local key = stat .. ":" .. (kind or "melee")
  local c = ratingCache[key]
  if c then return c[1], c[2] end
  local r = RATING[stat]
  if not r then return 0, false end
  local idx = r[kind or "melee"] or r.melee
  local f = g("GetCombatRatingBonusForCombatRatingValue")
  local v = idx and num(try(f, idx, 100))
  local out, fromGame
  if v and v > 0 and v < 1000 then out, fromGame = v / 100, true
  else out, fromGame = 1 / ((kind == "spell" and r.perSpell) or r.per), false end
  ratingCache[key] = { out, fromGame }
  return out, fromGame
end

local equipGen = 0
local wCache_baseMissing   -- (défini plus bas, avec le cache des poids)
local wValid = false   -- les poids en mémoire sont bons tant qu'aucun événement ne les a périmés (zéro appel au jeu)
--- l'équipement porté a changé (ou le niveau) : dégâts de base, plafond de toucher et verdicts sont à relire
function V.EquipChanged() equipGen = equipGen + 1 wValid = false end
--- fin de combat : si les dégâts de base n'avaient pas pu être lus sur la fiche (secrets en combat), on les relira
function V.CombatEnd() if wValid and wCache_baseMissing and wCache_baseMissing() then wValid = false end end
function V.EquipGen() return equipGen end
local baseCache
--- tes dégâts de base (tir auto / coup blanc), lus sur la fiche hors combat ; en combat : le dernier relevé
function V.Base(kind)
  local bg = equipGen
  local b = baseCache
  if b and b.kind == kind and (inCombat() or (b.gen == bg and now() - b.t < 300)) then return b end
  if inCombat() then return b or { dps = 0, src = "inconnu", kind = kind, gen = bg, t = now() } end
  local dps, src
  if kind == "ranged" then
    local speed, lo, hi = try(g("UnitRangedDamage"), "player")
    speed, lo, hi = num(speed), num(lo), num(hi)
    if speed and speed > 0 and lo and hi and hi > 0 then dps, src = (lo + hi) / 2 / speed, "fiche" end
  else
    local lo, hi = try(g("UnitDamage"), "player")
    local speed = try(g("UnitAttackSpeed"), "player")
    lo, hi, speed = num(lo), num(hi), num(speed)
    if speed and speed > 0 and lo and hi and hi > 0 then dps, src = (lo + hi) / 2 / speed, "fiche" end
  end
  if not dps then   -- fiche illisible : le DPS de l'arme portée (vrai, mais sans ta puissance d'attaque)
    local it = V.Item(try(g("GetInventoryItemLink"), "player", kind == "ranged" and 18 or 16))
    dps, src = it and it.st and tonumber(it.st[K.DPS]) or 0, "arme seule"
  end
  -- arrondi à 5 DPS : un buff de PA ne fait pas danser tous les nombres
  baseCache = { dps = max(0, floor(dps / 5 + 0.5) * 5), raw = dps, src = src, kind = kind, gen = bg, t = now() }
  return baseCache
end

---------------------------------------------------------------------------------------------------
-- Lecture d'un objet : stats (jeu → données du client → infobulle), arme, extras d'infobulle
---------------------------------------------------------------------------------------------------
local function esc(s) return (tostring(s):gsub("[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0")) end
local function gpat(name, fallback)   -- chaîne du jeu « (%s dégâts par seconde) » → motif Lua
  local s = str(g(name)) or fallback
  s = esc(s):gsub("%%%%s", "(.+)"):gsub("%%%%d", "(%%d+)"):gsub("%%%%%.%df", "([%%d%.,]+)")
  return s
end
local function tipNum(s) return tonumber((tostring(s or ""):gsub(",", "."):gsub("[^%d%.]", ""))) end
local statByName
local function statNames()
  if statByName then return statByName end
  statByName = {}
  for key, fr in pairs(STAT_FR) do
    statByName[fr:lower()] = key
    local gs = str(g(key))
    if gs then statByName[gs:lower()] = key end
  end
  for en, key in pairs({ agility = K.AGI, strength = K.STR, stamina = K.STA, intellect = K.INT, spirit = K.SPI, armor = K.ARMOR,
      armure = K.ARMOR }) do statByName[en] = statByName[en] or key end
  return statByName
end
local tipCache, tipN = {}, 0
--- lecture de l'infobulle d'un lien (ou de l'emplacement porté `slot`) : vitesse, DPS, unique, ensemble, usure,
--- enchantement, stats « +N Nom ». nil si le client n'a pas C_TooltipInfo (ou pas encore l'objet).
function V.Tip(link, slot)
  local key = slot and ("slot" .. slot .. ":" .. tostring(link)) or link
  if not key then return nil end
  local c = tipCache[key]
  if c ~= nil then return c or nil end
  local TI = g("C_TooltipInfo")
  local data
  if type(TI) == "table" then
    if slot and TI.GetInventoryItem then data = try(TI.GetInventoryItem, "player", slot) end
    if type(data) ~= "table" and type(link) == "string" and TI.GetHyperlink then data = try(TI.GetHyperlink, link) end
  end
  if type(data) ~= "table" or type(data.lines) ~= "table" then
    return nil   -- pas mis en cache : l'objet peut arriver plus tard
  end
  local E = g("Enum")
  local LT = type(E) == "table" and type(E.TooltipDataLineType) == "table" and E.TooltipDataLineType or {}
  local out = { st = {}, nst = 0 }
  local pSpeed = (str(g("SPEED")) or "Vitesse")
  local pDps = gpat("DPS_TEMPLATE", "(%s dégâts par seconde)")
  local pSet = "^(.+) %((%d+)/(%d+)%)$"
  local pDur = gpat("DURABILITY_TEMPLATE", "Durabilité %d / %d")
  local uniq, uniqE = str(g("ITEM_UNIQUE")) or "Unique", str(g("ITEM_UNIQUE_EQUIPPABLE")) or "Unique - Équipé"
  local names = statNames()
  for _, line in ipairs(data.lines) do
    local left, right = type(line) == "table" and str(line.leftText), type(line) == "table" and str(line.rightText)
    left = left and left:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
    if left then
      local lt = num(line.type)
      if right and (right:find(pSpeed, 1, true) or right:find("Speed", 1, true)) then out.speed = tipNum(right) end
      -- (les lettres accentuées font 2 octets : « d[^%s]-g[^%s]-ts » = dégâts / degats)
      local d = left:match(pDps) or left:match("%(([%d%.,]+) d[^%s]-g[^%s]-ts par seconde%)") or left:match("%(([%d%.,]+) damage per second%)")
      if d then out.dps = tipNum(d) end
      if left == uniq or left == uniqE or left:find("^Unique") then out.unique = true end
      local a, b = left:match(pDur)
      if a and b then out.dur, out.durMax = tonumber(a), tonumber(b) end
      local sn, have, total = left:match(pSet)
      if sn and tonumber(total) and tonumber(total) >= 2 and tonumber(have) <= tonumber(total) then
        out.set, out.setHave, out.setMax = sn, tonumber(have), tonumber(total)
      end
      if lt and LT.ItemEnchantmentPermanent and lt == LT.ItemEnchantmentPermanent then
        out.ench = left
        local dmg = left:match("%+(%d+) points? de d[^%s]-g[^%s]-ts") or left:match("%+(%d+) d[^%s]-g[^%s]-ts") or left:match("%+(%d+) [Dd]amage")
        if dmg then out.enchDmg = tonumber(dmg) end
        local v, nm = left:match("^%+(%d+) (.+)$")
        if not v then nm, v = left:match("^(.+) %+(%d+)$") end
        local k = nm and names[nm:lower()]
        if k and v then out.enchStat, out.enchVal = k, tonumber(v) end
      else
        local v, nm = left:match("^%+(%d+) (.+)$")
        local k = nm and names[nm:lower()]
        if k and v then out.st[k] = (out.st[k] or 0) + tonumber(v) out.nst = out.nst + 1 end
        local ar = left:match("^(%d+) Armure$") or left:match("^Armure : (%d+)$") or left:match("^(%d+) Armor$")
        if ar then out.st[K.ARMOR] = tonumber(ar) out.nst = out.nst + 1 end
      end
    end
  end
  if out.dps then out.st[K.DPS] = out.dps end
  if tipN >= 300 then tipCache, tipN = {}, 0 end
  tipCache[key], tipN = out, tipN + 1
  return out
end

local function apiStats(link)
  local CI = g("C_Item")
  local st = type(CI) == "table" and try(CI.GetItemStats, link) or nil
  if type(st) ~= "table" then st = try(g("GetItemStats"), link) end
  if type(st) ~= "table" then return nil end
  local out = {}
  for k, v in pairs(st) do
    v = tonumber(pub(v))
    if v and type(k) == "string" then
      local kk = ALIAS[k] or k
      out[kk] = (out[kk] or 0) + v
    end
  end
  return out
end
local function suffixed(link)   -- lien à suffixe aléatoire (« du Singe ») : les tables du client n'ont que la base
  local sfx = type(link) == "string" and link:match("item:%d+:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:(%-?%d+)")
  return sfx ~= nil and tonumber(sfx) ~= 0
end
local INV = { [1] = "INVTYPE_HEAD", [2] = "INVTYPE_NECK", [3] = "INVTYPE_SHOULDER", [5] = "INVTYPE_CHEST",
  [6] = "INVTYPE_WAIST", [7] = "INVTYPE_LEGS", [8] = "INVTYPE_FEET", [9] = "INVTYPE_WRIST", [10] = "INVTYPE_HAND",
  [11] = "INVTYPE_FINGER", [12] = "INVTYPE_TRINKET", [13] = "INVTYPE_WEAPON", [14] = "INVTYPE_SHIELD",
  [15] = "INVTYPE_RANGED", [16] = "INVTYPE_CLOAK", [17] = "INVTYPE_2HWEAPON", [20] = "INVTYPE_ROBE",
  [21] = "INVTYPE_WEAPONMAINHAND", [22] = "INVTYPE_WEAPONOFFHAND", [23] = "INVTYPE_HOLDABLE",
  [25] = "INVTYPE_THROWN", [26] = "INVTYPE_RANGEDRIGHT", [28] = "INVTYPE_RELIC" }
local items, itemN = {}, 0
--- tout ce qu'on sait d'un objet (lien ou n° d'objet des tables du client) ; nil = inconnu du jeu ET des tables.
--- it.known = stats lues avec certitude ; it.src = "jeu" | "données" | "infobulle".
function V.Item(x)
  if x == nil then return nil end
  local c = items[x]
  if c then return c end
  local id = itemID(x)
  if not id then return nil end
  local r = M.Rec and M.Rec(id) or nil
  local it
  if type(x) == "string" then
    local name, link, quality, _, minLevel, _, _, _, equipLoc, _, sell, classID, subID, bind
    if API.ItemInfo then name, link, quality, _, minLevel, _, _, _, equipLoc, _, sell, classID, subID, bind = API.ItemInfo(x) end
    name = str(name)
    if not name and not r then return nil end
    it = { id = id, link = x, name = name or r.name or x:match("%[(.-)%]"), quality = num(quality) or (r and r.q), minLevel = num(minLevel) or (r and r.req) or 0,
      equipLoc = str(equipLoc) or (r and INV[r.inv]) or "", sell = num(sell), classID = num(classID) or (r and r.cls),
      subID = num(subID) or (r and r.sub), bind = num(bind) or (r and r.bind), complete = name ~= nil }
    local st = apiStats(x)
    if st then it.src = "jeu"
    elseif r and not suffixed(x) then st, it.src = M.StatsOf(id), "données" end
    local tip
    if not st then
      tip = V.Tip(x)
      if tip and tip.nst > 0 then st, it.src = tip.st, "infobulle" end
    end
    it.st, it.known = st, st ~= nil
    it.speed = r and (r.speed or 0) > 0 and r.speed or nil
    if st and not st[K.DPS] and r and (r.dps or 0) > 0 then st[K.DPS] = r.dps end
    if it.classID == 2 and not it.speed then
      tip = tip or V.Tip(x)
      it.speed = tip and tip.speed or nil
    end
    it.ench = tonumber(x:match("item:%d+:(%d+)")) or 0
    it.suffixed = suffixed(x)
  else
    if not r then return nil end
    it = { id = id, name = r.name, quality = r.q, minLevel = r.req or 0, equipLoc = INV[r.inv] or "", classID = r.cls, subID = r.sub,
      bind = r.bind, st = M.StatsOf(id), src = "données", known = true, complete = true,
      speed = (r.speed or 0) > 0 and r.speed or nil, ench = 0 }
  end
  if it.complete and it.known then   -- un objet encore illisible sera relu (ses stats peuvent arriver)
    if itemN >= 4000 then items, itemN = {}, 0 end
    items[x], itemN = it, itemN + 1
  end
  return it
end

---------------------------------------------------------------------------------------------------
-- Poids du joueur (classe, spé lue, niveau, groupe, dégâts de base, plafond de toucher)
---------------------------------------------------------------------------------------------------
local wCache, wKey, wGen = nil, nil, 0
local hitCache = {}
function M.ResetWeights() wCache, wKey, wValid = nil, nil, false ratingCache = {} hitCache = {} end
wCache_baseMissing = function() return wCache ~= nil and wCache.unit == "dps" and wCache.baseSrc ~= "fiche" end
function V.Gen() return wGen end
local function inGroup() return pub(try(g("IsInGroup"))) and true or false end
--- % de toucher apporté par l'équipement porté (somme des scores, convertie)
function V.HitPct(kind)
  local hc = hitCache[kind or "melee"]
  if hc and hc.gen == equipGen then return hc.v end
  local sum = 0
  for slot = 1, 18 do
    local it = V.Item(try(g("GetInventoryItemLink"), "player", slot))
    if it and it.st then sum = sum + (tonumber(it.st[K.HIT]) or 0) end
  end
  local v = sum * (V.RatingPct("HIT", kind))
  hitCache[kind or "melee"] = { gen = equipGen, v = v }
  return v
end
local function staFactor(lvl, grouped)
  local f = (lvl < 20 and 0.45) or (lvl < 40 and 0.40) or (lvl < 58 and 0.35) or 0.25
  return grouped and f * 0.6 or f
end
local AIMED = { 19434, 20900, 20901, 20902, 20903, 20904 }   -- Visée, tous les rangs
local function aimedKnown()
  if API.IsSpellKnown then for _, id in ipairs(AIMED) do if API.IsSpellKnown(id) then return true end end end
  local sp = M.Spec and M.Spec()   -- Précision lue dans les talents avec ≥ 11 points : Visée est prise au 3e rang
  return (sp and sp.tree == 2 and sp.class == "HUNTER" and sp.points and (sp.points[2] or 0) >= 11) and true or false
end

local function ptsWeights(prof, lvl, cls)
  local src = {}
  for k, v in pairs(prof.w) do src[k] = v end
  local br = V.Bracket(lvl)
  for k, v in pairs(prof[br] or {}) do src[k] = v end
  local kind = (prof.role == "caster" or prof.role == "heal") and "spell" or "melee"
  local w = { unit = "pts", MDPS = src.MDPS or 0, RDPS = src.RDPS or 0, OHDPS = src.OHDPS, comfort = {}, role = prof.role, kind = kind }
  for k, v in pairs(src) do if K[k] then w[K[k]] = v end end
  local capped = false
  if src.HITP then
    local cap = (kind == "spell") and (lvl >= 58 and V.SPELL_HIT_CAP_60 or V.SPELL_HIT_CAP) or (lvl >= 58 and V.HIT_CAP_60 or V.HIT_CAP)
    capped = V.HitPct(kind) >= cap
    w.hitCap = cap
    w[K.HIT] = capped and 0 or src.HITP * (V.RatingPct("HIT", kind))
  end
  if src.CRITP then w[K.CRIT] = src.CRITP * (V.RatingPct("CRIT", kind)) end
  if src.DODGEP then w[K.DODGE] = src.DODGEP * (V.RatingPct("DODGE")) end
  if src.PARRYP then w[K.PARRY] = src.PARRYP * (V.RatingPct("PARRY")) end
  if src.BLOCKP then w[K.BLOCK] = src.BLOCKP * (V.RatingPct("BLOCK")) end
  w.hitCapped = capped
  w.cls = cls
  return w
end
local function dpsWeights(prof, lvl, cls, grouped)
  local cv = V.CONV[cls] or V.CONV.WARRIOR
  local kind = prof.kind
  local base = V.Base(kind)
  local B = base.dps or 0
  local ap = 1 / V.AP_PER_DPS
  local agiCrit = cv.agiCrit * max(10, min(60, lvl)) / 60
  local one = B / 100                     -- DPS pour 1 % de critique (+100 % de dégâts) ou 1 % de toucher
  local w = { unit = "dps", kind = kind, MDPS = 0, RDPS = 0, OHDPS = 0, comfort = COMFORT, base = B, baseSrc = base.src, cls = cls,
    agiCrit = agiCrit, role = kind }
  if kind == "ranged" then
    w.RDPS = 1
    w.a, w.b = 1, (lvl >= 18) and (aimedKnown() and 0.17 or 0.10) or 0
    w.aimed = w.b > 0.11
    w[K.RAP], w[K.AP] = ap, ap
    w[K.AGI] = cv.ragi * ap + one / agiCrit
    w[K.STR] = 0
  else
    local dualLvl = ENG.DUAL_LEVEL and ENG.DUAL_LEVEL[cls]
    w.MDPS = prof.form and 0 or 1
    w.OHDPS = (prof.dual and dualLvl and lvl >= dualLvl and not prof.form) and 0.5 or 0
    w[K.AP] = ap
    w[K.STR] = cv.str * ap
    w[K.AGI] = cv.agi * ap + one / agiCrit
    w[K.RAP] = 0
  end
  local cap = lvl >= 58 and V.HIT_CAP_60 or V.HIT_CAP
  local hitNow = V.HitPct(kind)
  w.hitCap, w.hitNow, w.hitCapped = cap, hitNow, hitNow >= cap
  w[K.CRIT] = one * (V.RatingPct("CRIT", kind))
  w[K.HIT] = w.hitCapped and 0 or one * (V.RatingPct("HIT", kind))
  local prim = w[K[prof.prim]] or ap
  w.prim = prim
  w[K.STA] = prim * staFactor(lvl, grouped)
  w[K.INT] = prim * (prof.int or 0)
  w[K.SPI] = prim * (prof.spi or 0.03)
  w[K.ARMOR], w[K.XARMOR] = prim * 0.01, prim * 0.01
  w[K.SP], w[K.SPD] = prim * (prof.sp or 0), prim * (prof.sp or 0)
  w[K.MP5] = prim * (prof.int or 0) * 4
  w.sta = staFactor(lvl, grouped)
  return w
end
--- poids de TA spé maintenant (table réutilisée tant que rien ne change) ; clés = stats du jeu + MDPS / RDPS / OHDPS
function V.Weights()
  if wValid and wCache then return wCache end
  local cls = class()
  if not cls then return nil end
  local lvl, tree, grouped = level(), M.SpecTree and M.SpecTree() or nil, inGroup()
  local prof, used = V.Profile(cls, tree)
  local b = prof.unit == "dps" and V.Base(prof.kind) or nil
  local bdps = b and b.dps or false
  local wc = wCache   -- (comparaison champ par champ : aucune chaîne construite quand rien n'a changé)
  if wc and wKey == equipGen and wc.cls == cls and wc.tree == used and wc.level == lvl and wc.grouped == grouped and wc.bdps == bdps then
    wValid = true
    return wc
  end
  local w = prof.unit == "dps" and dpsWeights(prof, lvl, cls, grouped) or ptsWeights(prof, lvl, cls)
  w.tree, w.level, w.grouped, w.profile, w.bdps, w.cls = used, lvl, grouped, prof, bdps, cls
  -- identité des VALEURS des poids (sans la génération d'équipement) : le plafond de toucher et les dégâts de base
  -- peuvent basculer quand l'équipement change ; tant qu'elle ne change pas, les verdicts et le plan restent valables
  local id = cls .. ":" .. tostring(used) .. ":" .. lvl .. ":" .. (grouped and 1 or 0) .. ":" .. tostring(b and b.dps)
    .. ":" .. (w.hitCapped and 1 or 0) .. ":" .. tostring(w.b)
  if id ~= V._lastId then wGen = wGen + 1 V._lastId = id end
  w.id, w.gen = id, wGen
  wCache, wKey, wValid = w, equipGen, true
  return w
end
--- phrase courte qui dit COMMENT on compte (infobulle, page) — les règles du jeu utilisées
function V.ModelText(w)
  w = w or V.Weights()
  if not w then return nil end
  if w.unit ~= "dps" then
    return ("Points pondérés pour ta spé (%s) : « +12 %% » = 12 %% de mieux que la pièce portée."):format(ROLE_FR[w.role] or "?")
  end
  local out = { "14 PA = 1 DPS" }
  if w.kind == "ranged" then
    out[#out + 1] = "1 Agi = 2 PA à distance"
    if (w.b or 0) > 0 then out[#out + 1] = w.aimed and "arme lente = Visée et Flèches multiples plus fortes" or "arme lente = Flèches multiples plus fortes" end
  end
  out[#out + 1] = w.hitCapped and ("toucher : plafond de %d %% atteint"):format(w.hitCap) or ("toucher utile jusqu'à %d %%"):format(w.hitCap)
  out[#out + 1] = ("Endurance comptée %s"):format(w.grouped and "en groupe (moins)" or "en solo")
  return table.concat(out, " · ")
end

---------------------------------------------------------------------------------------------------
-- Valeur d'un objet dans un emplacement
---------------------------------------------------------------------------------------------------
local function weightOf(k, slot, w, it)
  if k == K.DPS then
    if slot == 18 then
      if w.b and w.RDPS > 0 then return w.RDPS * ((w.a or 1) + w.b * ((it and it.speed) or V.REF_SPEED)) end
      return w.RDPS
    elseif slot == 17 then return w.OHDPS or w.MDPS end
    return w.MDPS
  end
  return w[k]
end
--- valeur totale, part « dégâts » (sans le confort), contributions par stat
function V.ValueOf(it, slot, w, parts)
  if not (it and it.st) then return nil end
  w = w or V.Weights()
  if not w then return nil end
  local total, hard = 0, 0
  local comfort = w.comfort or COMFORT
  for k, v in pairs(it.st) do
    local wt = weightOf(k, slot, w, it)
    if wt and wt ~= 0 then
      local x = (tonumber(v) or 0) * wt
      total = total + x
      if not comfort[k] then hard = hard + x end
      if parts then parts[k] = (parts[k] or 0) + x end
    end
  end
  return total, hard
end
--- valeur de l'enchantement / de la lunette posé sur l'objet PORTÉ dans `slot` (lu dans son infobulle), ou 0
function V.EnchantValue(it, slot, w)
  if not (it and it.link and (it.ench or 0) > 0) then return 0, nil end
  local tip = V.Tip(it.link, slot)
  if not (tip and tip.ench) then return 0, nil, true end   -- enchanté, valeur illisible
  local v = 0
  if tip.enchDmg and slot == 18 and (w.RDPS or 0) > 0 then v = tip.enchDmg / (it.speed or V.REF_SPEED) * w.RDPS
  elseif tip.enchDmg and slot == 16 and (w.MDPS or 0) > 0 then v = tip.enchDmg / (it.speed or 2.6) * w.MDPS
  elseif tip.enchStat then v = (tip.enchVal or 0) * (tonumber(weightOf(tip.enchStat, slot, w, it)) or 0) end
  return v, tip.ench
end

-- armes : sort de maîtrise par sous-classe (connu = tu sais la manier). On ne dit « à apprendre » que si la
-- lecture marche sur ce client (elle dit vrai pour l'arme que tu portes).
local PROF_SPELL = { [0] = 196, [1] = 197, [2] = 264, [3] = 266, [4] = 198, [5] = 199, [6] = 200, [7] = 201, [8] = 202,
  [10] = 227, [13] = 15590, [15] = 1180, [16] = 2567, [18] = 5011, [19] = 5009 }
local skillWorks
function V.WeaponSkill(sub)
  local sp = PROF_SPELL[sub or -1]
  if not (sp and API.IsSpellKnown) then return nil end
  if skillWorks == nil then
    skillWorks = false
    for _, slot in ipairs({ 16, 18 }) do
      local it = V.Item(try(g("GetInventoryItemLink"), "player", slot))
      if it and it.classID == 2 and PROF_SPELL[it.subID or -1] and API.IsSpellKnown(PROF_SPELL[it.subID]) then skillWorks = true end
    end
  end
  if API.IsSpellKnown(sp) then return true end
  if skillWorks then return false end
  return nil
end
function V.ResetSkills() skillWorks = nil end

---------------------------------------------------------------------------------------------------
-- T.Gear.Score(lien) : LE verdict d'un objet pour toi
---------------------------------------------------------------------------------------------------
local MIN_GAIN, BIG_GAIN = 3, 15
local function wornLink(slot) return str(try(g("GetInventoryItemLink"), "player", slot)) end
--- emplacements comparés (même règle que partout : le plus faible des deux anneaux / bijoux / armes à une main ;
--- arme à deux mains = main droite + main gauche) → liste de { slot, it }, emplacement de score
local function replacedBy(it, w)
  local slots = ENG.EQUIP_SLOTS[it.equipLoc]
  if not slots then return nil end
  if it.equipLoc == "INVTYPE_2HWEAPON" then
    local out = {}
    for _, s in ipairs({ 16, 17 }) do
      local l = wornLink(s)
      if l then out[#out + 1] = { slot = s, link = l, it = V.Item(l) } end
    end
    return out, 16
  end
  local cand = slots
  local mh = wornLink(16)
  local mhIt = mh and V.Item(mh)
  local twoHand = mhIt and mhIt.equipLoc == "INVTYPE_2HWEAPON"
  if it.equipLoc == "INVTYPE_WEAPON" then
    local d = ENG.DUAL_LEVEL and ENG.DUAL_LEVEL[class() or ""]
    cand = (d and level() >= d and not twoHand) and { 16, 17 } or { 16 }
  elseif twoHand and slots[1] == 17 then
    -- main gauche / bouclier alors que tu portes une arme à deux mains : il faudrait la retirer → comparé à elle
    return { { slot = 16, link = mh, it = mhIt } }, 17
  end
  local best, bestV
  for _, s in ipairs(cand) do
    local l = wornLink(s)
    local wi = l and V.Item(l)
    local v = wi and (V.ValueOf(wi, s, w)) or (l and math.huge or 0)   -- porté mais illisible : jamais « le plus faible »
    if bestV == nil or v < bestV then best, bestV = { slot = s, link = l, it = wi }, v end
  end
  if best and not best.link then return {}, best.slot end
  return { best }, best and best.slot or slots[1]
end
V.ReplacedBy = replacedBy

local scoreCache, scoreN, scoreGen = {}, 0, -1
local function scoreKey() return equipGen * 100000 + wGen end
function M.ScoreReset() scoreCache, scoreN = {}, 0 end
local function partLines(parts, w, newIt, oldIts, slot)
  local list = {}
  for k, x in pairs(parts) do
    if abs(x) >= 0.05 then list[#list + 1] = { k = k, x = x } end   -- « +0,0 » ne dit rien : pas de ligne
  end
  table.sort(list, function(a, b) if abs(a.x) ~= abs(b.x) then return abs(a.x) > abs(b.x) end return a.k < b.k end)
  local out = {}
  local unit = w.unit == "dps" and " DPS" or " pt"
  for i = 1, min(6, #list) do
    local e = list[i]
    local dn = 0
    dn = dn + (tonumber(newIt.st[e.k]) or 0)
    for _, o in ipairs(oldIts) do dn = dn - (o.it and o.it.st and tonumber(o.it.st[e.k]) or 0) end
    local label
    if e.k == K.DPS then
      local od = 0
      for _, o in ipairs(oldIts) do od = od + (o.it and o.it.st and tonumber(o.it.st[K.DPS]) or 0) end
      label = ("Arme %s → %s DPS"):format(dec(od), dec(tonumber(newIt.st[K.DPS]) or 0))
      if slot == 18 and newIt.speed and (w.b or 0) > 0 then
        local os = oldIts[1] and oldIts[1].it and oldIts[1].it.speed
        label = label .. (os and (", vitesse %s → %s s"):format(dec(os), dec(newIt.speed)) or (", vitesse %s s"):format(dec(newIt.speed)))
          .. (w.aimed and " (tirs auto + Flèches multiples + Visée)" or " (tirs auto + Flèches multiples)")
      end
    else
      label = ("%s%d %s"):format(dn >= 0 and "+" or "-", floor(abs(dn) + 0.5), STAT_FR[e.k] or e.k)
    end
    local cf = (w.comfort or COMFORT)[e.k]
    if cf then out[#out + 1] = ("%s : compté %s (%s)"):format(label, signed(e.x), cf)
    else out[#out + 1] = ("%s : %s%s"):format(label, signed(e.x), unit) end
  end
  return out
end

--- compare un objet lu (V.Item) à ceux qu'il remplace (olds = { { slot, it } }) dans `slot` → résultat de Score,
--- ou nil si un objet porté est illisible (on ne compare jamais à un score inconnu)
function V.Compare(it, olds, slot, w)
  for _, o in ipairs(olds) do if not (o.it and o.it.known) then return nil end end
  local parts = {}
  local new, newHard = V.ValueOf(it, slot, w, parts)
  if not new then return nil end
  local old, oldHard, names = 0, 0, {}
  local neg = {}
  local enchLost, enchText, enchUnknown
  for _, o in ipairs(olds) do
    local v, h = V.ValueOf(o.it, o.slot, w, neg)
    old, oldHard = old + (v or 0), oldHard + (h or 0)
    names[#names + 1] = o.it.name or "?"
    local ev, et, eu = V.EnchantValue(o.it, o.slot, w)
    if et then enchLost, enchText = (enchLost or 0) + ev, et end
    if eu then enchUnknown = true end
  end
  for k, x in pairs(neg) do parts[k] = (parts[k] or 0) - x end
  old, oldHard = old + (enchLost or 0), oldHard + (enchLost or 0)
  local delta, hard = new - old, newHard - oldHard
  local pct
  if old <= 0 then pct = (new > 0) and 999 or 0 else pct = delta / old * 100 end
  local kind = (pct >= MIN_GAIN and "good") or (pct > -MIN_GAIN and "same") or "bad"
  -- spé à dégâts : perdre des dégâts n'est jamais « mieux », même avec plus d'Endurance (on le dit : « plus de survie »)
  local trade = w.unit == "dps" and hard < -0.05 and kind == "good"
  if trade then kind = "same" end
  local text
  if #olds == 0 and new > 0 then
    text = (w.unit == "dps" and hard >= 0.05) and ("≈ " .. signed(hard) .. " DPS (emplacement vide)") or "emplacement vide"
  elseif w.unit == "dps" and abs(hard) >= 0.05 then text = "≈ " .. signed(hard) .. " DPS" .. (trade and ", mais plus de survie" or "")
  elseif pct >= 100 and pct < 999 then text = "vaut " .. dec(new / old) .. " fois la pièce portée"
  elseif abs(pct) < 0.5 then text = nil   -- mêmes stats (ou presque) : « pareil », pas « +0 % »
  elseif pct < 999 then text = ("%s%d %%"):format(pct >= 0 and "+" or "-", floor(abs(pct) + 0.5)) end
  local lines = partLines(parts, w, it, olds, slot)
  if enchText then
    lines[#lines + 1] = ("Ton objet porte « %s »%s : à reposer sur le nouveau."):format(enchText,
      (enchLost or 0) > 0 and (" (compté : " .. dec(enchLost) .. (w.unit == "dps" and " DPS" or " pt") .. ")") or "")
  elseif enchUnknown then
    lines[#lines + 1] = "Ton objet porté est enchanté : l'enchantement n'est pas compté (illisible)."
  end
  for _, o in ipairs(olds) do
    local tw = o.link and V.Tip(o.link, o.slot) or nil
    if tw and tw.set and (tw.setHave or 0) >= 2 then
      lines[#lines + 1] = ("Ton objet fait partie de l'ensemble « %s » (%d/%d) : regarde son bonus avant de changer (non compté)."):format(
        tw.set, tw.setHave, tw.setMax or tw.setHave)
    end
  end
  local req = (it.minLevel or 0) > level() and it.minLevel or nil
  return { known = true, equip = true, usable = true, slot = slot, replaces = names, delta = delta, dps = (w.unit == "dps") and hard or nil,
    pct = pct, unit = w.unit, kind = kind, big = pct >= BIG_GAIN and kind == "good", trade = trade or nil, text = text or "pareil", lines = lines, new = new, old = old,
    src = it.src, req = req, item = it, comfortOnly = (w.unit == "dps" and abs(hard) < 0.05 and abs(delta - hard) >= 0.05) or nil }
end

--- le verdict d'un objet (lien) pour TOI, comparé à ce que tu portes. Toujours une table :
---  known = false → stats illisibles (« inconnu », aucun nombre) ; equip = false → pas un objet d'équipement ;
---  usable = false + why → pas pour toi / pas encore (later = niveau) ;
---  sinon slot, replaces (noms), delta (valeur), dps (part dégâts, spés physiques), pct (% de la pièce portée,
---  999 = emplacement vide), unit, kind ("good"|"same"|"bad"), text (« ≈ +3,2 DPS » / « +12 % »), lines (détail).
function M.Score(link)
  if link == nil then return { known = false, text = "inconnu" } end
  local gen = scoreKey()
  if gen ~= scoreGen then scoreCache, scoreN, scoreGen = {}, 0, gen end
  local c = scoreCache[link]
  if c then return c end
  local it = V.Item(link)
  local res
  if not it then
    return { known = false, text = "inconnu", why = "le jeu n'a pas encore envoyé cet objet" }   -- pas en cache : il arrivera
  elseif not ENG.EQUIP_SLOTS[it.equipLoc] then
    res = { known = true, equip = false }
  else
    local ok, why = ENG.CanUse(type(link) == "string" and link or it.id)
    local later = tonumber(tostring(why or ""):match("portable au niveau (%d+)"))
    if not ok then
      res = { known = true, equip = true, usable = false, why = why, later = later, item = it }
    elseif not it.known then
      res = { known = false, equip = true, usable = true, text = "inconnu", why = "stats illisibles pour l'instant", item = it }
    else
      local w = V.Weights()
      local olds, slot = replacedBy(it, w)
      local dup
      if it.equipLoc == "INVTYPE_FINGER" or it.equipLoc == "INVTYPE_TRINKET" or it.equipLoc == "INVTYPE_WEAPON" then
        for _, s in ipairs(ENG.EQUIP_SLOTS[it.equipLoc]) do
          local l = wornLink(s)
          if l and itemID(l) == it.id then dup = true end
        end
      end
      local tipU = dup and type(link) == "string" and V.Tip(link) or nil
      if tipU and tipU.unique then
        res = { known = true, equip = true, usable = false, why = "unique : tu le portes déjà", item = it }
      else
        res = w and V.Compare(it, olds or {}, slot, w) or nil
      end
      if not res then
        res = { known = false, equip = true, usable = true, text = "inconnu", why = "ton objet porté ne se lit pas encore", item = it }
      elseif res.usable ~= false then
        res.why = why
        local lines = res.lines
        if it.classID == 2 and V.WeaponSkill(it.subID) == false then
          res.train = true
          lines[#lines + 1] = "Arme à apprendre chez un maître d'armes avant de pouvoir l'équiper."
        end
      end
    end
  end
  if it.complete and res.known ~= false then
    if scoreN >= 500 then scoreCache, scoreN = {}, 0 end
    scoreCache[link], scoreN = res, scoreN + 1
  end
  return res
end

local WORD = { good = "Mieux", same = "Pareil", bad = "Moins bien" }
--- la ligne d'infobulle : « Mieux pour toi : ≈ +2,1 DPS (remplace Arc X) » → libellé, texte, genre
function M.ScoreLine(sc)
  if type(sc) ~= "table" or sc.equip == false then return nil end
  if sc.known == false then return "Pour toi", "inconnu" .. (sc.why and (" (" .. sc.why .. ")") or ""), "info" end
  if sc.usable == false then
    if sc.later then return "Pas encore", ("portable au niveau %d"):format(sc.later), "warn" end
    return "Pas pour toi", sc.why or "non utilisable", "bad"
  end
  local label = (sc.big and sc.kind == "good" and "Nettement mieux" or WORD[sc.kind] or "Pareil") .. " pour toi"
  local rep = sc.replaces and #sc.replaces > 0 and table.concat(sc.replaces, " + ") or nil
  local text = sc.text or ""
  if sc.kind == "good" and rep then text = text .. " (remplace " .. rep .. ")"
  elseif sc.kind == "bad" and rep then text = text .. " (" .. rep .. " reste meilleur)"
  elseif sc.kind == "same" and rep then text = (sc.text and sc.text ~= "pareil" and (sc.text .. (sc.trade and " que " or ", comme ")) or "comme ") .. rep end
  if sc.comfortOnly then text = text .. " — surtout de la survie" end
  if sc.req then text = text .. (" · niv %d requis"):format(sc.req) end
  if sc.train then text = text .. " · arme à apprendre" end
  return label, text, sc.kind
end

-- branchements du moteur (une seule vérité : les autres modules passent par Gear.Engine)
ENG.Item, ENG.ValueOf, ENG.Value = V.Item, V.ValueOf, V
