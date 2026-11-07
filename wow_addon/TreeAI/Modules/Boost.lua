-- TreeAI — module Boost (agent BOOST) : les avantages LÉGITIMES les plus forts, informatifs uniquement.
--  1. Rares / élites à proximité (barres de nom, cible, survol) : ligne + son, 1 fois par GUID ;
--     clic sur la ligne = poser un crâne (SetRaidTarget, hors combat, uniquement sur clic).
--  2. Meilleure récompense de quête : surlignage du bouton (jamais de sélection automatique).
--  3. Infobulles d'objets : « Amélioration +X % » / « Pas mieux », prix vendeur vs HV (Auctionator, lecture seule).
--  4. Menace en groupe (non-tank) : alerte ≥ seuil, conseils chasseur (Feindre la mort / Grondement).
--  5. Moteur d'XP : XP par monstre/quête, XP/h, temps avant le niveau (repos inclus).
--  6. Chasseur : rappel Aspect du guépard / de la meute en déplacement hors combat (id partagé "Hunter:aspect").
--  7. Chasseur : conseiller d'apprivoisement (rangs des techniques de familier).
--  8. Rappels : pierre de foyer prête + sacs pleins, munitions pour 30 min, nourriture / boissons.
--     (la réparation est déjà gérée par le module Inventaire : pas de doublon)
--  9. Récolte : compétence vs niveau du nœud / du cadavre à dépecer (lignes d'infobulle).
-- Rien n'est fait à la place du joueur : aucune touche, aucun sort lancé, aucun objet utilisé.
-- Lua 5.1, aucune globale créée, toutes les API testées avant usage (Forever = famille Mainline, Era).
local T = _G.TreeAI
if not T or not T.RegisterModule then return end
local API = T.API or {}

local M = T:RegisterModule("Boost", {
  title = "Boost (avantages)",
  defaults = {
    enabled = true, rares = true, rareSound = true, elites = true, rewards = true, tooltip = true,
    threat = true, threatPct = 85, xp = true, travel = true, pet = true, reminders = true, gather = true,
  },
  charDefaults = { xpKills = {}, xpQuests = {} },
  options = {
    { key = "rares", type = "toggle", label = "Scanner de rares (ligne + son)" },
    { key = "rareSound", type = "toggle", label = "Son pour les rares" },
    { key = "elites", type = "toggle", label = "Signaler aussi les élites" },
    { key = "rewards", type = "toggle", label = "Surligner la meilleure récompense de quête" },
    { key = "tooltip", type = "toggle", label = "Infobulles : amélioration % et prix" },
    { key = "threat", type = "toggle", label = "Alerte de menace en groupe" },
    { key = "threatPct", type = "range", label = "Seuil d'alerte de menace (%)", min = 60, max = 100, step = 5 },
    { key = "xp", type = "toggle", label = "Moteur d'XP (monstres / quêtes avant le niveau)" },
    { key = "travel", type = "toggle", label = "Chasseur : rappel aspect de voyage" },
    { key = "pet", type = "toggle", label = "Chasseur : conseiller d'apprivoisement" },
    { key = "reminders", type = "toggle", label = "Rappels (foyer, munitions, nourriture)" },
    { key = "gather", type = "toggle", label = "Récolte : compétence vs nœud / dépeçage" },
  },
})

local G = _G
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  return pass(pcall(fn, ...))
end
local function pack(...) return { n = select("#", ...), ... } end
local function now() return (G.GetTime and G.GetTime()) or 0 end
local function opt(k) return M.db and M.db[k] end
local function inCombat() return T:InCombat() end
local function playerClass() return T:PlayerClass() end
local function level() return try(G.UnitLevel, "player") or 1 end
local function inGroup()
  if G.IsInGroup then return try(G.IsInGroup) and true or false end
  return ((try(G.GetNumPartyMembers) or 0) > 0) or ((try(G.GetNumRaidMembers) or 0) > 0)
end
local function spellName(id, fallback) return (API.SpellName and API.SpellName(id)) or fallback end
local function knows(id) return API.IsSpellKnown and API.IsSpellKnown(id) or false end
local function itemInfo(x) if API.ItemInfo then return API.ItemInfo(x) end return try(G.GetItemInfo, x) end
local function itemID(link)
  if type(link) == "number" then return link end
  return tonumber(type(link) == "string" and link:match("item:(%d+)") or nil)
end
local function money(c)
  c = math.floor(tonumber(c) or 0)
  local g, s, k = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  if g > 0 then return ("%d po %d pa"):format(g, s) end
  if s > 0 then return ("%d pa %d pc"):format(s, k) end
  return ("%d pc"):format(k)
end
local function fmtNum(n)
  n = math.floor((tonumber(n) or 0) + 0.5)
  local s = tostring(n):reverse():gsub("(%d%d%d)", "%1 "):reverse()
  return (s:gsub("^ ", ""))
end
local function fmtDur(sec)
  sec = math.floor(tonumber(sec) or 0)
  if sec <= 0 then return "?" end
  local h, m = math.floor(sec / 3600), math.floor(sec / 60) % 60
  if h > 0 then return ("%d h %02d"):format(h, m) end
  return ("%d min"):format(math.max(1, m))
end

local stats = { rares = 0, lastRare = nil, threatWarn = 0, bestReward = nil, petAdvice = nil }

---------------------------------------------------------------------------------------------------
-- 1. Rares & élites
---------------------------------------------------------------------------------------------------
local seen = {}          -- guid -> time (10 min)
local markGUID           -- dernier rare, pour le bouton « crâne »

local function findUnitByGUID(guid)
  for _, u in ipairs({ "target", "mouseover", "focus" }) do
    if try(G.UnitGUID, u) == guid then return u end
  end
  for i = 1, 40 do
    local u = "nameplate" .. i
    if try(G.UnitGUID, u) == guid then return u end
  end
end

local function markRare()
  if inCombat() then M:Print("Pas de marqueur en combat (réessaie après).") return end
  local unit = markGUID and findUnitByGUID(markGUID)
  if not unit then M:Print("Rare hors de portée : cible-le puis clique à nouveau.") return end
  if G.SetRaidTarget then try(G.SetRaidTarget, unit, 8) end -- 8 = crâne
end

local function scanUnit(unit)
  if not opt("rares") or not unit or not try(G.UnitExists, unit) then return end
  if try(G.UnitIsPlayer, unit) or try(G.UnitIsDead, unit) then return end
  if G.UnitCanAttack and not try(G.UnitCanAttack, "player", unit) then return end
  local cls = try(G.UnitClassification, unit)
  if cls ~= "rare" and cls ~= "rareelite" and cls ~= "worldboss" and not (cls == "elite" and opt("elites")) then return end
  local guid = try(G.UnitGUID, unit)
  if not guid then return end
  local t = now()
  if seen[guid] and t - seen[guid] < 600 then return end
  seen[guid] = t
  local name = try(G.UnitName, unit) or "?"
  local lvl = try(G.UnitLevel, unit) or 0
  local lv = (lvl and lvl > 0) and (" (niv " .. lvl .. ")") or " (niv ??)"
  if cls == "elite" then
    M:Suggest("elite", "Élite : " .. name .. lv .. " — à plusieurs, ou évite.", 1, { ttl = 45, cooldown = 120 })
    return
  end
  stats.rares, stats.lastRare = stats.rares + 1, name
  markGUID = guid
  local label = (cls == "rareelite") and "Rare élite" or (cls == "worldboss" and "Boss" or "Rare")
  M:Suggest("rare", label .. " : " .. name .. lv .. " à proximité ! (clic : crâne)", 2,
    { ttl = 90, cooldown = 5, sound = opt("rareSound") and true or false, onClick = markRare })
end

function M:NAME_PLATE_UNIT_ADDED(_, unit) scanUnit(unit) end
function M:PLAYER_TARGET_CHANGED() scanUnit("target") end

---------------------------------------------------------------------------------------------------
-- 2/3. Évaluation des objets (poids de stats par classe / spé)
---------------------------------------------------------------------------------------------------
-- Poids « points de score par point de stat », valeurs de leveling Classic (simples, assumées) :
--   • stat principale = 1.0 ; endurance ~0.3–0.5 (1.0 pour un tank) ; PA ~ 1/2–1/3 d'une stat principale ;
--   • DPS d'arme (ITEM_MOD_DAMAGE_PER_SECOND_SHORT) : MDPS = arme de mêlée, RDPS = emplacement à distance
--     (arc/fusil/arbalète pour chasseur = sa vraie arme -> poids fort ; baguette pour lanceurs) ;
--   • RESISTANCE0_NAME = armure (faible poids, sauf tanks).
-- Spé = arbre de talents le plus rempli (1/2/3) ; sans talents -> poids « défaut » de la classe.
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
-- Armures (classID 4) : 1 tissu, 2 cuir, 3 mailles (niv 40 chasseur/chaman), 4 plaques (niv 40), 6 bouclier.
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

local statCache = {}
local function itemStats(link)
  if not link then return nil end
  local c = statCache[link]
  if c then return c end
  local st
  if G.C_Item and G.C_Item.GetItemStats then st = try(G.C_Item.GetItemStats, link) end
  if not st and G.GetItemStats then st = try(G.GetItemStats, link) end
  if type(st) ~= "table" then return nil end
  statCache[link] = st
  return st
end

local function score(link, slot, w)
  local st = itemStats(link)
  if not st then return nil end
  local s = 0
  for k, v in pairs(st) do
    if k == S.DPS then s = s + (tonumber(v) or 0) * ((slot == 18) and w.RDPS or w.MDPS)
    elseif w[k] then s = s + (tonumber(v) or 0) * w[k] end
  end
  return s
end

local function equippedLink(slot) return try(G.GetInventoryItemLink, "player", slot) end

--- Peut-on porter cet objet ? -> ok, raison
local function canUse(link)
  local _, _, _, _, minLevel, _, _, _, equipLoc, _, _, classID, subID = itemInfo(link)
  if not equipLoc or equipLoc == "" or not SLOTS[equipLoc] then return false, "non équipable" end
  local cls, lvl = playerClass(), level()
  if classID == 4 and subID and subID > 0 and equipLoc ~= "INVTYPE_CLOAK" then
    local need = (ARMOR[cls] or {})[subID]
    if not need then return false, "armure non portable" end
    if lvl < need then return false, "porte ça au niv " .. need end
  elseif classID == 2 and subID then
    if not (WEAPONS[cls] or {})[subID] then return false, "arme non utilisable" end
  end
  if minLevel and minLevel > lvl then return true, "niv " .. minLevel .. " requis" end
  return true
end

--- Gain en % par rapport à l'équipement porté (nil si stats inconnues). 999 = emplacement vide.
local function upgradePct(link)
  local _, _, _, _, _, _, _, _, equipLoc = itemInfo(link)
  local slots = equipLoc and SLOTS[equipLoc]
  if not slots then return nil end
  local w = weights()
  local new = score(link, slots[1], w)
  if not new then return nil end
  local old
  if equipLoc == "INVTYPE_2HWEAPON" then
    old = (score(equippedLink(16), 16, w) or 0) + (score(equippedLink(17), 17, w) or 0)
  else
    local cand = slots
    if equipLoc == "INVTYPE_WEAPON" then
      local d = DUAL[playerClass()]
      cand = (d and level() >= d) and { 16, 17 } or { 16 }
    end
    for _, sl in ipairs(cand) do
      local l = equippedLink(sl)
      local v = l and (score(l, sl, w) or 0) or 0
      if old == nil or v < old then old = v end
    end
  end
  old = old or 0
  if old <= 0 then return new > 0 and 999 or 0 end
  return (new - old) / old * 100
end
M.UpgradePct, M.CanUse = upgradePct, canUse

---------------------------------------------------------------------------------------------------
-- 2. Meilleure récompense de quête (surlignage, jamais de choix automatique)
---------------------------------------------------------------------------------------------------
local glow
local function hideGlow() if glow then glow:Hide() end end
local function showGlow(btn)
  if not btn or not G.CreateFrame then return end
  if not glow then
    glow = G.CreateFrame("Frame", nil, G.UIParent)
    local tex = glow:CreateTexture(nil, "OVERLAY")
    tex:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
    if tex.SetBlendMode then tex:SetBlendMode("ADD") end
    if tex.SetVertexColor then tex:SetVertexColor(0.2, 1, 0.3) end
    tex:SetAllPoints()
    glow.label = glow:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    glow.label:SetPoint("BOTTOM", glow, "TOP", 0, -4)
    glow.label:SetText("|cff33ff55TreeAI|r")
  end
  -- (0.2) jamais enfant d'un bouton de Blizzard (récompense de quête) : simplement ancré dessus
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

--- Classe les choix : retourne best index, pct (ou nil), raison
function M:RankQuestRewards()
  local n = try(G.GetNumQuestChoices) or 0
  if n < 2 then return nil end
  local bestUp, bestUpPct, bestSell, bestSellV = nil, 0.5, nil, -1
  for i = 1, n do
    local link = try(G.GetQuestItemLink, "choice", i)
    local _, _, count, _, isUsable = try(G.GetQuestItemInfo, "choice", i)
    if link then
      local ok = canUse(link)
      if ok and isUsable ~= false then
        local pct = upgradePct(link)
        if pct and pct > bestUpPct then bestUp, bestUpPct = i, pct end
      end
      local sell = select(11, itemInfo(link)) or 0
      sell = sell * (tonumber(count) or 1)
      if sell > bestSellV then bestSell, bestSellV = i, sell end
    end
  end
  if bestUp then return bestUp, bestUpPct, "upgrade" end
  if bestSell then return bestSell, bestSellV, "sell" end
end

function M:QUEST_COMPLETE()
  if not opt("rewards") then return end
  T:After(0.3, function()
    local i, v, why = M:RankQuestRewards()
    if not i then hideGlow() return end
    local link = try(G.GetQuestItemLink, "choice", i)
    local name = (link and itemInfo(link)) or ("choix " .. i)
    showGlow(rewardButton(i))
    local txt
    if why == "upgrade" then
      txt = v >= 999 and ("Récompense conseillée : " .. name .. " (emplacement vide)")
        or ("Récompense conseillée : " .. name .. (" (+%d %%)"):format(v))
    else
      txt = "Aucune amélioration : prends " .. name .. " (vente " .. money(v) .. ")"
    end
    stats.bestReward = name
    M:Suggest("reward", txt, 2, { ttl = 60, cooldown = 5 })
  end)
end
function M:QUEST_FINISHED() hideGlow(); M:Clear("reward") end

---------------------------------------------------------------------------------------------------
-- 3. Infobulles d'objets
---------------------------------------------------------------------------------------------------
local function auctionValue(link)
  local A = G.Auctionator
  local f = A and A.API and A.API.v1 and A.API.v1.GetAuctionPriceByItemLink
  if type(f) ~= "function" then return nil end
  local v = try(f, "TreeAI", link)
  return tonumber(v)
end

local function decorateItem(tip, link)
  if not opt("tooltip") or not tip or not tip.AddLine or not link then return end
  local _, _, _, _, _, _, _, _, equipLoc, _, sell = itemInfo(link)
  if equipLoc and SLOTS[equipLoc] then
    local ok, why = canUse(link)
    if not ok then
      tip:AddLine("TreeAI : " .. (why or "pas pour toi"), 0.6, 0.6, 0.6)
    else
      local pct = upgradePct(link)
      if pct then
        if pct >= 999 then tip:AddLine("TreeAI : emplacement vide, équipe-le !", 0.2, 1, 0.3)
        elseif pct > 0.5 then tip:AddLine(("TreeAI : Amélioration +%d %%"):format(pct) .. (why and (" (" .. why .. ")") or ""), 0.2, 1, 0.3)
        elseif pct > -0.5 then tip:AddLine("TreeAI : équivalent", 1, 0.9, 0.3)
        else tip:AddLine(("TreeAI : Pas mieux (%d %%)"):format(pct), 1, 0.4, 0.4) end
      end
    end
  end
  local ah = auctionValue(link)
  if (sell and sell > 0) or ah then
    local txt = "Vendre : " .. ((sell and sell > 0) and money(sell) or "—")
    if ah then
      txt = txt .. "  ·  HV : " .. money(ah)
      if sell and sell > 0 then txt = txt .. ((ah > sell * 1.2) and "  → HV" or "  → marchand") end
    end
    tip:AddLine(txt, 0.8, 0.8, 0.8)
  end
end

-- 9. Récolte : niveaux requis (Classic). Nœuds (nom affiché FR/EN) -> compétence requise.
local NODES = {
  ["Filon de cuivre"] = 1, ["Copper Vein"] = 1, ["Filon d'étain"] = 65, ["Tin Vein"] = 65,
  ["Filon d'argent"] = 75, ["Silver Vein"] = 75, ["Gisement de fer"] = 125, ["Iron Deposit"] = 125,
  ["Filon d'or"] = 155, ["Gold Vein"] = 155, ["Gisement de mithril"] = 175, ["Mithril Deposit"] = 175,
  ["Filon de vrai-argent"] = 230, ["Truesilver Deposit"] = 230, ["Petit filon de thorium"] = 245,
  ["Small Thorium Vein"] = 245, ["Riche filon de thorium"] = 275, ["Rich Thorium Vein"] = 275,
  ["Pacifique"] = 1, ["Peacebloom"] = 1, ["Feuillargent"] = 1, ["Silverleaf"] = 1, ["Terrestrine"] = 15,
  ["Earthroot"] = 15, ["Mage royal"] = 50, ["Mageroyal"] = 50, ["Eglantine"] = 70, ["Églantine"] = 70,
  ["Briarthorn"] = 70, ["Etouffante"] = 85, ["Stranglekelp"] = 85, ["Doulourante"] = 100, ["Bruiseweed"] = 100,
  ["Aciérite sauvage"] = 115, ["Wild Steelbloom"] = 115, ["Tombeline"] = 120, ["Grave Moss"] = 120,
  ["Sang-royal"] = 125, ["Kingsblood"] = 125, ["Vietérule"] = 150, ["Liferoot"] = 150, ["Pâlerette"] = 160,
  ["Fadeleaf"] = 160, ["Dorépine"] = 170, ["Goldthorn"] = 170, ["Moustache de Khadgar"] = 185,
  ["Khadgar's Whisker"] = 185, ["Hivernale"] = 195, ["Wintersbite"] = 195, ["Fleur de feu"] = 205,
  ["Firebloom"] = 205, ["Lotus pourpre"] = 210, ["Purple Lotus"] = 210, ["Soleillette"] = 230, ["Sungrass"] = 230,
}
local PROF = { herb = { id = 182, names = { "Herboristerie", "Herbalism" } },
  mine = { id = 186, names = { "Minage", "Mining" } }, skin = { id = 393, names = { "Dépeçage", "Skinning" } } }

local profCache, profAt = {}, -100
local function profSkills()
  if now() - profAt < 10 then return profCache end
  profAt, profCache = now(), {}
  local function put(name, rank, lineID)
    for k, p in pairs(PROF) do
      if lineID == p.id or name == p.names[1] or name == p.names[2] then profCache[k] = rank end
    end
  end
  if G.GetProfessions and G.GetProfessionInfo then
    local list = { try(G.GetProfessions) }
    for i = 1, 5 do
      if list[i] then
        local name, _, rank, _, _, _, lineID = try(G.GetProfessionInfo, list[i])
        if name then put(name, rank, lineID) end
      end
    end
  end
  if G.GetNumSkillLines and G.GetSkillLineInfo then
    for i = 1, (try(G.GetNumSkillLines) or 0) do
      local name, isHeader, _, rank = try(G.GetSkillLineInfo, i)
      if name and not isHeader then put(name, rank) end
    end
  end
  return profCache
end
M.ProfSkills = profSkills

local function skinReq(lvl)
  lvl = tonumber(lvl) or 1
  if lvl <= 10 then return 1 elseif lvl <= 20 then return (lvl - 10) * 10 end
  return lvl * 5
end
M.SkinReq = skinReq

local function decorateUnit(tip, unit)
  if not opt("gather") or not tip or not tip.AddLine then return end
  unit = unit or "mouseover"
  if not try(G.UnitExists, unit) or not try(G.UnitIsDead, unit) or try(G.UnitIsPlayer, unit) then return end
  local skin = profSkills().skin
  if not skin then return end
  local ctype = try(G.UnitCreatureType, unit)
  if ctype ~= "Bête" and ctype ~= "Beast" and ctype ~= "Draconien" and ctype ~= "Dragonkin" then return end
  local req = skinReq(try(G.UnitLevel, unit))
  if skin >= req then tip:AddLine(("TreeAI : peut dépecer ce cadavre (%d/%d)"):format(skin, req), 0.2, 1, 0.3)
  else tip:AddLine(("TreeAI : dépeçage %d requis (toi %d)"):format(req, skin), 1, 0.4, 0.4) end
end

local function decorateNode(tip)
  if not opt("gather") or not tip then return end
  local line = G.GameTooltipTextLeft1
  local text = line and line.GetText and line:GetText()
  local req = text and NODES[text]
  if not req then return end
  local p = profSkills()
  local have = (text:find("[Ff]ilon") or text:find("[Gg]isement") or text:find("Vein") or text:find("Deposit")) and p.mine or p.herb
  if not have then return end
  if have >= req then tip:AddLine(("TreeAI : récoltable (%d/%d)"):format(have, req), 0.2, 1, 0.3)
  else tip:AddLine(("TreeAI : compétence %d requise (toi %d)"):format(req, have), 1, 0.4, 0.4) end
  if tip.Show then tip:Show() end
end

local hooked = false
local function hookTooltips()
  if hooked then return end
  hooked = true
  local TDP, E = G.TooltipDataProcessor, G.Enum and G.Enum.TooltipDataType
  if TDP and TDP.AddTooltipPostCall and E and E.Item then
    TDP.AddTooltipPostCall(E.Item, function(tip, data)
      if not M:IsEnabled() then return end
      local link = tip and tip.GetItem and select(2, tip:GetItem())
      if not link and data and data.guid and G.C_Item and G.C_Item.GetItemLinkByGUID then
        link = try(G.C_Item.GetItemLinkByGUID, data.guid)
      end
      pcall(decorateItem, tip, link)
    end)
    if E.Unit then
      TDP.AddTooltipPostCall(E.Unit, function(tip)
        if not M:IsEnabled() then return end
        local unit = tip and tip.GetUnit and select(2, tip:GetUnit())
        pcall(decorateUnit, tip, unit)
      end)
    end
  else
    for _, tip in ipairs({ G.GameTooltip, G.ItemRefTooltip }) do
      if tip and tip.HookScript then
        pcall(tip.HookScript, tip, "OnTooltipSetItem", function(self)
          if not M:IsEnabled() then return end
          local link = self.GetItem and select(2, self:GetItem())
          pcall(decorateItem, self, link)
        end)
      end
    end
    if G.GameTooltip and G.GameTooltip.HookScript then
      pcall(G.GameTooltip.HookScript, G.GameTooltip, "OnTooltipSetUnit", function(self)
        if not M:IsEnabled() then return end
        local unit = self.GetUnit and select(2, self:GetUnit())
        pcall(decorateUnit, self, unit)
      end)
    end
  end
  -- objets du monde (nœuds) : pas d'unité ni d'objet -> lecture du titre de l'infobulle
  if G.GameTooltip and G.GameTooltip.HookScript then
    local busy = false
    pcall(G.GameTooltip.HookScript, G.GameTooltip, "OnShow", function(self)
      if busy or not M:IsEnabled() then return end
      if self.GetUnit and self:GetUnit() then return end
      if self.GetItem and self:GetItem() then return end
      busy = true
      pcall(decorateNode, self)
      busy = false
    end)
  end
end
M.DecorateItem, M.DecorateUnit = decorateItem, decorateUnit

---------------------------------------------------------------------------------------------------
-- 4. Menace (groupe PvE, non-tank)
---------------------------------------------------------------------------------------------------
local function isTank()
  local role = G.UnitGroupRolesAssigned and try(G.UnitGroupRolesAssigned, "player")
  if role == "TANK" then return true end
  if role == "HEALER" or role == "DAMAGER" then return false end
  local cls, form = playerClass(), try(G.GetShapeshiftForm) or 0
  if cls == "WARRIOR" then return form == 2 end          -- posture défensive
  if cls == "DRUID" then return form == 1 end            -- forme d'ours
  if cls == "PALADIN" then return API.Aura and API.Aura("player", 25780) ~= nil end -- Fureur vertueuse
  return false
end
M.IsTank = isTank

function M:CheckThreat()
  if not opt("threat") or not inGroup() or isTank() then self:Clear("threat") return end
  if not try(G.UnitExists, "target") or not G.UnitDetailedThreatSituation then return end
  local tanking, _, pct = try(G.UnitDetailedThreatSituation, "player", "target")
  pct = tonumber(pct)
  if not pct then self:Clear("threat") return end
  local limit = tonumber(opt("threatPct")) or 85
  if tanking or pct >= limit then
    local txt = tanking and "Tu as l'aggro ! Arrête de taper" or ("Lâche un peu : %d %% de menace"):format(pct)
    if playerClass() == "HUNTER" then
      local fd = knows(5384) and spellName(5384, "Feindre la mort")
      local tips = {}
      if fd then tips[#tips + 1] = "« " .. fd .. " »" end
      if try(G.UnitExists, "pet") then tips[#tips + 1] = "familier : « " .. spellName(2649, "Grondement") .. " »" end
      if #tips > 0 then txt = txt .. " — " .. table.concat(tips, " ou ") end
    end
    stats.threatWarn = stats.threatWarn + 1
    self:Suggest("threat", txt, tanking and 3 or 2, { ttl = 4, cooldown = 8 })
  elseif pct < limit - 15 then
    self:Clear("threat")
  end
end

---------------------------------------------------------------------------------------------------
-- 5. Moteur d'XP
---------------------------------------------------------------------------------------------------
local gains = {}         -- { t, xp } de la session (XP/h glissante)
local lastXP

--- Analyse un message CHAT_MSG_COMBAT_XP_GAIN (FR/EN). -> xp, restedBonus, isKill
function M.ParseXP(msg)
  if type(msg) ~= "string" then return nil end
  local xp = tonumber(msg:match("(%d+)%s*[^%d%(%)%.,]-[eE]xp")) or tonumber(msg:match("(%d+)"))
  if not xp then return nil end
  local bonus = 0
  for paren in msg:gmatch("%((.-)%)") do
    if paren:find("[Rr]epos") or paren:find("[Rr]ested") then bonus = tonumber(paren:match("(%d+)")) or 0 end
  end
  local kill = (msg:find(" meurt") or msg:find(" dies") or msg:find("tué") or msg:find("slain")) and true or false
  return xp, bonus, kill
end

local function push(list, v, max) list[#list + 1] = v while #list > max do table.remove(list, 1) end end
local function avg(list)
  local s, n = 0, 0
  for _, v in ipairs(list) do s, n = s + v, n + 1 end
  return n > 0 and s / n or nil
end

function M:CHAT_MSG_COMBAT_XP_GAIN(_, msg)
  local xp, bonus, kill = M.ParseXP(msg)
  if not xp then return end
  if kill then push(self.char.xpKills, math.max(1, xp - bonus), 20) end -- XP de base (hors repos)
  self:UpdateXP()
end

function M:QUEST_TURNED_IN(_, _, xp)
  xp = tonumber(xp)
  if xp and xp > 0 then push(self.char.xpQuests, xp, 15) end
  self:UpdateXP()
end

function M:PLAYER_XP_UPDATE()
  local xp = try(G.UnitXP, "player") or 0
  if lastXP and xp > lastXP then push(gains, { t = now(), xp = xp - lastXP }, 400) end
  lastXP = xp
end
function M:PLAYER_LEVEL_UP() lastXP = 0 end

--- Calcule les estimations (table) ; nil si pas assez de données.
function M:XPStats()
  local cur, max = try(G.UnitXP, "player") or 0, try(G.UnitXPMax, "player") or 0
  if max <= 0 then return nil end
  local remaining = max - cur
  local rested = try(G.GetXPExhaustion) or 0
  local base = avg(self.char.xpKills)
  local r = { remaining = remaining, rested = rested, nextLevel = level() + 1 }
  if base and base > 0 then
    -- en repos : chaque monstre rapporte 2× sa base et consomme « base » de repos
    if remaining <= 2 * rested then r.kills = math.ceil(remaining / (2 * base))
    else r.kills = math.ceil((remaining - rested) / base) end
    r.avgKill = math.floor(base + 0.5)
  end
  local q = avg(self.char.xpQuests)
  if q and q > 0 then r.quests = math.ceil(remaining / q); r.avgQuest = math.floor(q + 0.5) end
  -- XP/h sur les 60 dernières minutes de la session (au moins 5 min de données)
  local t, sum, first = now(), 0, nil
  for _, g in ipairs(gains) do
    if t - g.t <= 3600 then sum = sum + g.xp; first = first or g.t end
  end
  if first and t - first >= 300 then
    r.perHour = math.floor(sum / math.max(t - first, 600) * 3600)
    if r.perHour > 0 then r.eta = remaining / r.perHour * 3600 end
  end
  return r
end

function M:XPText()
  local r = self:XPStats()
  if not r or (not r.kills and not r.quests) then return nil end
  local parts = {}
  if r.kills then parts[#parts + 1] = "~" .. r.kills .. " monstres" end
  if r.quests then parts[#parts + 1] = "~" .. r.quests .. " quêtes" end
  local s = table.concat(parts, " ou ") .. " avant niv " .. r.nextLevel
  if r.rested > 0 then s = s .. " (repos inclus)" end
  if r.perHour then s = s .. " · " .. fmtNum(r.perHour) .. " XP/h · ~" .. fmtDur(r.eta) end
  return s
end

function M:UpdateXP()
  if not opt("xp") then return end
  local txt = self:XPText()
  if txt then self:Suggest("xp", txt, 1, { ttl = 120, cooldown = 600, sound = false }) end
end

---------------------------------------------------------------------------------------------------
-- 6. Chasseur : aspect de voyage (partage l'id "Hunter:aspect" du module Chasseur : pas de doublon)
---------------------------------------------------------------------------------------------------
local CHEETAH, PACK, HAWK, MONKEY = 5118, 13159, 13165, 13163
local movingSince, travelShown
local function hasAura(id) return API.Aura and API.Aura("player", id) ~= nil end

function M:CheckTravel()
  if not opt("travel") or playerClass() ~= "HUNTER" or not knows(CHEETAH) then return end
  if inCombat() then
    local cur = T._suggestions and T._suggestions["Hunter:aspect"]
    if travelShown and cur and cur.module == "Boost" then T:ClearSuggestion("Hunter:aspect") end
    travelShown = nil
    movingSince = nil
    return
  end
  local speed = try(G.GetUnitSpeed, "player") or 0
  local blocked = try(G.IsMounted) or try(G.UnitOnTaxi, "player") or try(G.IsResting) or try(G.UnitIsDeadOrGhost, "player")
  if speed <= 0 or blocked then movingSince = nil return end
  movingSince = movingSince or now()
  if now() - movingSince < 5 then return end
  if hasAura(CHEETAH) or hasAura(PACK) or hasAura(HAWK) or hasAura(MONKEY) then return end
  local name
  if inGroup() and knows(PACK) then
    name = "« " .. spellName(PACK, "Aspect de la meute") .. " » pour le groupe (hébété si touché)"
  else
    name = "« " .. spellName(CHEETAH, "Aspect du guépard") .. " » (+30 % vitesse, hébété si touché)"
  end
  travelShown = true
  T:Suggest("Hunter:aspect", "Tu voyages sans aspect : " .. name .. ".", 1, { module = "Boost", ttl = 20, cooldown = 300 })
end

---------------------------------------------------------------------------------------------------
-- 7. Chasseur : conseiller d'apprivoisement (connaissance Classic, niveaux/zones INDICATIFS)
---------------------------------------------------------------------------------------------------
-- Familles (FR + EN tel que renvoyé par UnitCreatureFamily) -> clé
local FAMILY = {
  Loup = "wolf", Wolf = "wolf", ["Félin"] = "cat", Cat = "cat", Ours = "bear", Bear = "bear", Sanglier = "boar",
  Boar = "boar", ["Chauve-souris"] = "bat", Bat = "bat", Charognard = "carrion", ["Carrion Bird"] = "carrion",
  Crabe = "crab", Crab = "crab", Crocilisque = "croc", Crocolisk = "croc", Gorille = "gorilla", Gorilla = "gorilla",
  ["Hyène"] = "hyena", Hyena = "hyena", Chouette = "owl", Owl = "owl", Raptor = "raptor", Scorpide = "scorpid",
  Scorpid = "scorpid", ["Araignée"] = "spider", Spider = "spider", Tortue = "turtle", Turtle = "turtle",
  ["Serpent des vents"] = "serpent", ["Wind Serpent"] = "serpent", ["Haut-trotteur"] = "strider", Tallstrider = "strider",
}
local FAMILY_FR = { wolf = "loup", cat = "félin", bear = "ours", boar = "sanglier", bat = "chauve-souris",
  carrion = "charognard", crab = "crabe", croc = "crocilisque", gorilla = "gorille", hyena = "hyène",
  owl = "chouette", raptor = "raptor", scorpid = "scorpide", spider = "araignée", turtle = "tortue",
  serpent = "serpent des vents", strider = "haut-trotteur" }
-- ordre = priorité du conseil. lv = niveau minimal de la bête (et du familier) pour chaque rang.
local ABILITIES = {
  { key = "bite", id = 17253, fr = "Morsure", lv = { 1, 8, 16, 24, 32, 40, 48, 56 },
    fam = { "wolf", "hyena", "boar", "bat", "carrion", "croc", "raptor", "spider", "turtle", "gorilla", "serpent", "strider" },
    where = { [2] = "Forêt d'Elwynn, Bois des Pins-Argentés, Mulgore", [3] = "Marche de l'Ouest, Les Tarides (hyènes)",
      [4] = "Contreforts de Hautebrande, Bois de la Pénombre (worgs)", [5] = "Hautes-terres d'Arathi, Strangleronce",
      [6] = "Tanaris (hyènes), Féralas" } },
  { key = "claw", id = 16827, fr = "Griffe", lv = { 1, 8, 16, 24, 32, 40, 48, 56 },
    fam = { "cat", "bear", "raptor", "scorpid", "carrion", "crab", "owl" },
    where = { [2] = "Dun Morogh, Sombrivage, Durotar", [3] = "Les Tarides, Sombrivage, Marche de l'Ouest",
      [4] = "Contreforts de Hautebrande (lions des montagnes), Les Paluns (raptors)",
      [5] = "Hautes-terres d'Arathi, Marécage d'Âprefange", [6] = "Strangleronce (tigres), Tanaris" } },
  { key = "howl", id = 24604, fr = "Hurlement furieux", lv = { 10, 24, 40, 56 }, fam = { "wolf" },
    where = { [1] = "Bois des Pins-Argentés, Marche de l'Ouest", [2] = "Contreforts de Hautebrande, Bois de la Pénombre",
      [3] = "Féralas, Hinterlands" } },
  { key = "charge", id = 7371, fr = "Charge", lv = { 1, 12, 24, 36, 48, 60 }, fam = { "boar" },
    where = { [2] = "Marche de l'Ouest, Les Tarides", [3] = "Contreforts de Hautebrande, Les Carmines", [4] = "Terres ingrates" } },
  { key = "breath", id = 24844, fr = "Souffle de foudre", lv = { 1, 12, 24, 36, 48, 60 }, fam = { "serpent" },
    where = { [2] = "Les Tarides", [3] = "Mille pointes, Les Serres-Rocheuses", [4] = "Désolace" } },
  { key = "screech", id = 24423, fr = "Hurlement perçant", lv = { 8, 24, 48, 56 }, fam = { "bat", "carrion", "owl" },
    where = { [1] = "Clairières de Tirisfal, Les Tarides", [2] = "Les Serres-Rocheuses, Mille pointes" } },
  { key = "poison", id = 24640, fr = "Poison de scorpide", lv = { 8, 24, 40, 56 }, fam = { "scorpid" },
    where = { [1] = "Durotar", [2] = "Mille pointes", [3] = "Terres ingrates" } },
  { key = "dash", id = 23099, fr = "Course", lv = { 30, 40, 50 }, fam = { "cat", "hyena", "wolf" },
    where = { [1] = "Strangleronce, Désolace" } },
  { key = "dive", id = 23145, fr = "Plongeon", lv = { 30, 40, 50 }, fam = { "bat", "carrion", "owl", "serpent" },
    where = { [1] = "Désolace, Terres ingrates" } },
  { key = "prowl", id = 24450, fr = "Rôder", lv = { 30, 40, 50 }, fam = { "cat" }, where = { [1] = "Strangleronce" } },
  { key = "stomp", id = 26090, fr = "Choc de foudre", lv = { 30, 40, 50 }, fam = { "gorilla" }, where = { [1] = "Strangleronce" } },
  { key = "shell", id = 26064, fr = "Bouclier de carapace", lv = { 20 }, fam = { "turtle" }, where = { [1] = "Les Paluns" } },
}
M.ABILITIES = ABILITIES

--- Techniques connues par le familier : { [nom en minuscules] = rang }
local function petAbilities()
  local out = {}
  local n = try(G.HasPetSpells)
  n = tonumber(n) or 0
  local SB = G.C_SpellBook
  local bank = G.Enum and G.Enum.SpellBookSpellBank and G.Enum.SpellBookSpellBank.Pet
  for i = 1, math.min(n > 0 and n or 40, 60) do
    local name, sub
    if SB and SB.GetSpellBookItemName and bank then name, sub = try(SB.GetSpellBookItemName, i, bank) end
    if not name and G.GetSpellBookItemName then name, sub = try(G.GetSpellBookItemName, i, G.BOOKTYPE_PET or "pet") end
    if not name and G.GetSpellName then name, sub = try(G.GetSpellName, i, G.BOOKTYPE_PET or "pet") end
    if type(name) == "string" then
      local rank = tonumber(type(sub) == "string" and sub:match("(%d+)") or nil) or 1
      local k = name:lower()
      if not out[k] or out[k] < rank then out[k] = rank end
    elseif n == 0 then
      break
    end
  end
  return out
end

--- Conseil d'apprivoisement (texte) et liste détaillée
function M:PetAdvice()
  if playerClass() ~= "HUNTER" or not try(G.UnitExists, "pet") then return nil, {} end
  local fam = FAMILY[try(G.UnitCreatureFamily, "pet") or ""]
  local petLvl = try(G.UnitLevel, "pet") or level()
  local known = petAbilities()
  local list = {}
  for _, a in ipairs(ABILITIES) do
    local canLearn = false
    for _, f in ipairs(a.fam) do if f == fam then canLearn = true end end
    if canLearn then
      local name = spellName(a.id, a.fr)
      local have = known[name:lower()] or known[a.fr:lower()] or 0
      local best = 0
      for r, lv in ipairs(a.lv) do if lv <= petLvl then best = r end end
      if best > have then
        local lv = a.lv[best]
        local fams = {}
        for i = 1, math.min(3, #a.fam) do fams[#fams + 1] = FAMILY_FR[a.fam[i]] end
        local where = a.where and a.where[best]
        list[#list + 1] = ("Apprivoise un %s niv %d-%d%s pour %s rang %d (ton familier : rang %d)"):format(
          table.concat(fams, "/"), lv, lv + 4, where and (" à " .. where) or "", name, best, have)
      end
    end
  end
  return list[1], list
end

function M:UpdatePet()
  if not opt("pet") or playerClass() ~= "HUNTER" then return end
  local txt = self:PetAdvice()
  stats.petAdvice = txt
  if txt then self:Suggest("pet", txt .. " (Dressage des bêtes ensuite).", 1, { ttl = 120, cooldown = 3600, sound = false })
  else self:Clear("pet") end
end

---------------------------------------------------------------------------------------------------
-- 8. Rappels
---------------------------------------------------------------------------------------------------
local HEARTH = 6948
local function itemCooldownReady(id)
  local s, d
  if G.C_Container and G.C_Container.GetItemCooldown then s, d = try(G.C_Container.GetItemCooldown, id) end
  if s == nil and G.GetItemCooldown then s, d = try(G.GetItemCooldown, id) end
  s, d = tonumber(s) or 0, tonumber(d) or 0
  return s == 0 or (s + d) <= now()
end

local DRINK = { "[Ee]au", "[Ww]ater", "[Ll]ait", "[Mm]ilk", "[Jj]us", "[Jj]uice", "[Cc]idre", "[Rr]afra", "[Bb]oisson",
  "[Tt]hé ", "[Nn]ectar" }
local function isDrink(id, name)
  local D = T.Data and T.Data.Consumables
  local e = type(D) == "table" and (D[id] or (type(D.byID) == "table" and D.byID[id]))
  if type(e) == "table" then
    local k = e.kind or e.type or e.cat
    if k == "drink" or k == "water" then return true end
    if k == "food" then return false end
  end
  for _, p in ipairs(DRINK) do if name and name:find(p) then return true end end
  return false
end

function M:FoodCount()
  local food, drink = 0, 0
  if not API.IterBags then return food, drink end
  for _, _, it in API.IterBags() do
    local name, _, _, _, _, _, _, _, _, _, _, classID, subID = itemInfo(it.itemID)
    if classID == 0 and subID == 5 then
      if isDrink(it.itemID, name) then drink = drink + (it.count or 1) else food = food + (it.count or 1) end
    end
  end
  return food, drink
end

function M:CheckReminders()
  if not opt("reminders") or inCombat() then return end
  -- pierre de foyer prête + sacs pleins
  local free = API.FreeSlots and API.FreeSlots() or 99
  if free <= 1 and (API.ItemCount and API.ItemCount(HEARTH) or 0) > 0 and itemCooldownReady(HEARTH)
      and not try(G.IsResting) then
    local bind = try(G.GetBindLocation)
    self:Suggest("hearth", "Pierre de foyer prête, sacs pleins : rentre" .. (bind and (" (" .. bind .. ")") or "") .. ".", 2,
      { ttl = 120, cooldown = 900 })
  else
    self:Clear("hearth")
  end
  -- munitions pour 30 min (chasseur) : tirs/min = 60 / vitesse ; ~50 % du temps en combat en quêtant
  if playerClass() == "HUNTER" then
    local ammo = try(G.GetInventoryItemCount, "player", 0) or 0
    local speed = tonumber((try(G.UnitRangedDamage, "player"))) or 0
    local hp = T.GetModule and T:GetModule("HunterPro")
    if hp and (not hp.IsEnabled or hp:IsEnabled()) then
      self:Clear("ammo30")   -- HunterPro gives the ammo budget (same advice twice = noise)
    elseif ammo > 0 and speed > 0 then
      local need = math.floor(30 * 60 / speed * 0.5)
      local minutes = math.floor(ammo / (60 / speed * 0.5))
      if ammo < need then
        self:Suggest("ammo30", ("Munitions : ~%d min de jeu restantes, prends-en ~%d de plus."):format(minutes,
          math.ceil((need - ammo) / 200) * 200), 1, { ttl = 300, cooldown = 1200 })
      else self:Clear("ammo30") end
    end
  end
  -- nourriture / boissons
  local food, drink = self:FoodCount()
  local pt, ptoken = try(G.UnitPowerType, "player")
  local mana = (pt == 0) or (ptoken == "MANA")
  local low = {}
  if food < 5 then low[#low + 1] = food .. " nourriture" end
  if mana and drink < 5 then low[#low + 1] = drink .. " boisson" .. (drink > 1 and "s" or "") end
  if #low > 0 then
    self:Suggest("food", "Plus que " .. table.concat(low, " et ") .. " : refais le plein chez l'aubergiste.", 1,
      { ttl = 300, cooldown = 1500 })
  else self:Clear("food") end
end

---------------------------------------------------------------------------------------------------
-- Cycle de vie
---------------------------------------------------------------------------------------------------
local tick, threatTick
function M:OnEnable()
  self.char.xpKills = self.char.xpKills or {}
  self.char.xpQuests = self.char.xpQuests or {}
  lastXP = try(G.UnitXP, "player")
  self:RegisterEvent("NAME_PLATE_UNIT_ADDED")
  self:RegisterEvent("PLAYER_TARGET_CHANGED")
  self:RegisterEvent("UPDATE_MOUSEOVER_UNIT", function() scanUnit("mouseover") end)
  self:RegisterEvent("QUEST_COMPLETE")
  self:RegisterEvent("QUEST_FINISHED")
  self:RegisterEvent("CHAT_MSG_COMBAT_XP_GAIN")
  self:RegisterEvent("QUEST_TURNED_IN")
  self:RegisterEvent("PLAYER_XP_UPDATE")
  self:RegisterEvent("PLAYER_LEVEL_UP")
  self:RegisterEvent("UNIT_THREAT_LIST_UPDATE", function(m, _, unit) if unit == "target" then m:CheckThreat() end end)
  self:RegisterEvent("UNIT_PET", function(m, _, unit) if unit == "player" then T:After(2, function() m:UpdatePet() end) end end)
  self:RegisterEvent("PET_BAR_UPDATE", function(m) T:After(1, function() m:UpdatePet() end) end)
  self:On("COMBAT_START", function(m)
    m:CheckTravel()
    if threatTick then threatTick:Cancel() end
    if inGroup() then threatTick = T:NewTicker(0.5, function() pcall(m.CheckThreat, m) end) end
  end)
  self:On("COMBAT_END", function(m)
    if threatTick then threatTick:Cancel(); threatTick = nil end
    m:Clear("threat")
  end)
  self:On("SUMMARY", function(m) m:WriteSummary() end)
  hookTooltips()
  local n = 0
  if tick then tick:Cancel() end
  tick = T:NewTicker(1, function()
    n = n + 1
    pcall(M.CheckTravel, M)
    if n % 30 == 5 then pcall(M.CheckReminders, M) end
  end)
  T:After(5, function() pcall(M.UpdatePet, M); pcall(M.UpdateXP, M) end)
end

function M:OnDisable()
  if tick then tick:Cancel(); tick = nil end
  if threatTick then threatTick:Cancel(); threatTick = nil end
  hideGlow()
end

function M:WriteSummary()
  local s = T:Summary()
  if type(s) ~= "table" then return end
  local r = self:XPStats() or {}
  s.boost = {
    raresSeen = stats.rares, lastRare = stats.lastRare, threatWarnings = stats.threatWarn,
    bestReward = stats.bestReward, petAdvice = stats.petAdvice,
    avgKillXP = r.avgKill, avgQuestXP = r.avgQuest, killsToLevel = r.kills, questsToLevel = r.quests,
    xpPerHour = r.perHour, secondsToLevel = r.eta and math.floor(r.eta) or nil,
    tank = isTank() or nil,
  }
end

if T.RegisterSlash then
  T:RegisterSlash("boost", function()
    M:Print(M:XPText() or "XP : tue quelques monstres / rends une quête pour avoir une estimation.")
    if playerClass() == "HUNTER" then
      local _, list = M:PetAdvice()
      if #list == 0 then M:Print("Familier : techniques au meilleur rang pour son niveau.") end
      for _, l in ipairs(list) do M:Print(l) end
    end
    M:Print(("Rares vus cette session : %d%s"):format(stats.rares, stats.lastRare and (" (dernier : " .. stats.lastRare .. ")") or ""))
  end, "boost : XP avant le niveau, conseils d'apprivoisement, rares vus")
end

function M:AutoRefresh() pcall(M.CheckReminders, M); pcall(M.CheckTravel, M) end
