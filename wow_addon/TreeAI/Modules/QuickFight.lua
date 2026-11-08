-- TreeAI — module QuickFight (agent QUICK2) : « Raccourcis » entre deux combats.
-- Une petite barre déplaçable de boutons qui n'apparaissent que quand ils servent :
--   Marque + familier (pull), Appeler / Ressusciter le familier, Soigner le familier, Nourrir le familier,
--   Changer d'aspect, Bandage (quand il va plus vite que manger), Boire / Manger (meilleur objet des sacs),
--   Acheter des munitions (marchand ouvert), + minuteur de repos et indicateur « prêt à engager ».
-- Règles Blizzard : les boutons sont des SecureActionButtonTemplate (type = macro) ; l'action part du CLIC du
-- joueur. Création, attributs, Show / Hide, placement et taille : HORS COMBAT uniquement (RunOutOfCombat).
-- En combat on ne touche qu'à l'alpha, aux textures et au texte. L'achat de munitions = clic du joueur.
-- Propriétaires : nourriture du familier (Hunter:BestFood), munitions (Hunter:AmmoInfo), meilleurs consommables
-- (Macros.NewContext) ; aucune ligne de conseil ici (la ligne « Avant d'engager » de Fight se tait quand la
-- barre affiche l'indicateur). Lua 5.1, aucune globale (boutons nommés TreeAIQuick_<clé> pour /click).
local T = _G.TreeAI
if type(T) ~= "table" or type(T.RegisterModule) ~= "function" then return end
local G = _G
local pairs, ipairs, type, tostring, tonumber, pcall, select = pairs, ipairs, type, tostring, tonumber, pcall, select
local floor, min, max, ceil = math.floor, math.min, math.max, math.ceil
local tconcat = table.concat
local next = next

T.MODULE_INFO = T.MODULE_INFO or {}
T.MODULE_INFO.QuickFight = T.MODULE_INFO.QuickFight
  or { lite = true, combat = true, desc = "Raccourcis : boutons entre les combats (manger, familier, aspect, pull)" }

local M = T:RegisterModule("QuickFight", {
  title = "Raccourcis de combat",
  defaults = {
    enabled = true, bar = true, scale = 100, timer = true, ready = true,
    eat = true, bandage = true, hp = 70, mana = 60,
    feed = true, mend = true, petCall = true, aspect = true, pull = true, ammo = true,
    petHp = 70, ammoBuy = 1000, pos = nil,
  },
  options = {
    { key = "bar", type = "toggle", label = "Afficher la barre de raccourcis", section = "Barre" },
    { key = "scale", type = "range", label = "Taille de la barre (%)", min = 60, max = 160, step = 10 },
    { key = "timer", type = "toggle", label = "Minuteur de repos (« plein dans 12 s »)" },
    { key = "ready", type = "toggle", label = "Indicateur « prêt à engager »" },
    { key = "eat", type = "toggle", label = "Boire / manger (meilleur objet des sacs)", section = "Repos" },
    { key = "bandage", type = "toggle", label = "Bandage quand il va plus vite que manger" },
    { key = "hp", type = "range", label = "Manger ou se bander sous (% PV)", min = 30, max = 95, step = 5 },
    { key = "mana", type = "range", label = "Boire sous (% mana)", min = 20, max = 95, step = 5 },
    { key = "pull", type = "toggle", label = "Marque du chasseur + envoi du familier", section = "Chasseur" },
    { key = "petCall", type = "toggle", label = "Appeler / ressusciter le familier" },
    { key = "mend", type = "toggle", label = "Soigner le familier" },
    { key = "petHp", type = "range", label = "Soigner le familier sous (% PV)", min = 30, max = 95, step = 5 },
    { key = "feed", type = "toggle", label = "Nourrir le familier (meilleure nourriture des sacs)" },
    { key = "aspect", type = "toggle", label = "Changer d'aspect (Faucon en combat, Guépard pour voyager)" },
    { key = "ammo", type = "toggle", label = "Acheter des munitions chez le marchand" },
    { key = "ammoBuy", type = "range", label = "Stock de munitions visé", min = 200, max = 4000, step = 200 },
  },
})
if not M then return end
T.QuickFight = M

---------------------------------------------------------------------------
-- Utilitaires défensifs
---------------------------------------------------------------------------
local function _ns(v) local f = rawget(G, "issecretvalue") if f and f(v) then return nil end return v end
local function try(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h, i, j = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h, i, j end
  return nil
end
local function API() return type(T.API) == "table" and T.API or {} end
local function now() return (try(G.GetTime)) or 0 end
local function inCombat()
  if T.InCombat then return T:InCombat() and true or false end
  return (try(G.InCombatLockdown)) and true or false
end
local function db() return M.db or {} end
local function opt(k) local v = db()[k] if v == nil then return M.defaults and M.defaults[k] end return v end
local function num(v) return tonumber(_ns(v)) end

local S = {
  MARK = 1130, CALL = 883, REVIVE = 982, MEND = 136, FEED = 6991, FEED_EFFECT = 1539,
  HAWK = 13165, MONKEY = 13163, CHEETAH = 5118, PACK = 13159, BANDAGED = 11196,
}
M.SPELLS = S

-- (3.9.9) noms / icônes de sorts mis en cache (fixes pour un id ; rien n'est gardé tant que le client répond nil)
local nameCache, iconCache = {}, {}
local function spellName(id)
  local v = nameCache[id]
  if v ~= nil then return v end
  local f = API().SpellName
  v = f and f(id) or nil
  if type(v) == "string" then nameCache[id] = v end
  return v
end
local function spellIcon(id)
  local v = iconCache[id]
  if v ~= nil then return v end
  local f = API().SpellIcon
  v = f and f(id) or nil
  if v ~= nil then iconCache[id] = v end
  return v
end
local function knows(id)
  local H = T.modules and T.modules.Hunter
  if H and type(H.knows) == "function" then return H.knows(id) and true or false end
  local f = API().IsSpellKnown
  return f and f(id) and true or false
end
local function itemName(id)
  local f = API().ItemName
  return (f and f(id)) or ("objet " .. tostring(id))
end
local function itemIcon(id)
  local f = API().ItemInfo
  if not f then return nil end
  return (select(10, f(id)))
end
local function itemCount(id) local f = API().ItemCount return (f and f(id)) or 0 end

local EAT_NAMES = { ["Nourriture"] = "f", ["Food"] = "f", ["Manger"] = "f", ["Boisson"] = "d", ["Drink"] = "d",
  ["Boire"] = "d", ["Rafraîchissement"] = "b", ["Refreshment"] = "b" }
local BANDAGED_NAMES = { ["Bandage récent"] = true, ["Recently Bandaged"] = true }

---------------------------------------------------------------------------
-- Valeurs des consommables (Data/Consumables.lua : { id, niveau, valeur, étiquette })
---------------------------------------------------------------------------
local values
local function valueOf(cat, id)
  if not id then return nil end
  if not values then
    values = { food = {}, water = {}, bandage = {} }
    local C = T.Data and T.Data.Consumables
    local Mc = T.Data and T.Data.Macros and T.Data.Macros.items
    for _, src in ipairs({ Mc, C }) do
      if type(src) == "table" then
        for cat2, map in pairs(values) do
          for _, row in ipairs(type(src[cat2]) == "table" and src[cat2] or {}) do
            if type(row) == "table" and row[1] and row[3] then map[row[1]] = row[3] end
          end
        end
      end
    end
  end
  return values[cat] and values[cat][id]
end
M.ValueOf = valueOf

-- Durée d'un repas / d'une boisson (Classic : 18 à 30 s selon la puissance), bandage 8 s.
function M.EatDuration(val, water)
  val = tonumber(val) or 0
  if val < 200 then return 18 elseif val < 500 then return 21 elseif val < 900 then return 24
  elseif val < (water and 1500 or 1400) then return 27 end
  return 30
end
M.BANDAGE_TIME = 8

--- Pur : secondes pour combler `deficit` avec un objet qui rend `val` en EatDuration(val).
function M.RestTime(deficit, val, water)
  deficit, val = tonumber(deficit) or 0, tonumber(val) or 0
  if deficit <= 0 then return 0 end
  if val <= 0 then return nil end
  return deficit * M.EatDuration(val, water) / val
end

---------------------------------------------------------------------------
-- Sélections des sacs (mises en cache : recalculées sur BAG_UPDATE_DELAYED / niveau / familier)
---------------------------------------------------------------------------
local picks
function M:InvalidatePicks() picks = nil end
function M:Picks()
  if picks then return picks end
  local p = {}
  local Mc = T.Macros or (T.modules and T.modules.Macros)
  if Mc and type(Mc.NewContext) == "function" then
    local ok, ctx = pcall(Mc.NewContext)
    if ok and type(ctx) == "table" and ctx.item then
      p.food, p.water, p.bandage = ctx.item("food"), ctx.item("water"), ctx.item("bandage")
    end
  end
  local H = T.modules and T.modules.Hunter
  if H and type(H.BestFood) == "function" and T:PlayerClass() == "HUNTER" then
    local ok, id, quality = pcall(H.BestFood, H)
    if ok then p.petfood, p.petfoodQ = id, quality end
  end
  picks = p
  return p
end

---------------------------------------------------------------------------
-- Instantané de l'état (lectures gardées ; valeurs secrètes → nil)
---------------------------------------------------------------------------
local lastPetHP, petDied = nil, false
local combatEndAt = 0
local eatT0, eatHP0, eatMP0

local function auraScan(unit, filter)
  local f = API().Auras
  return f and f(unit, filter) or {}
end

function M:Snapshot()
  local s = { t = now(), class = T:PlayerClass() }
  local st = (T.PlayerState and T:PlayerState()) or {}
  s.inCombat = inCombat()
  s.dead = st.dead or st.ghost
  s.mounted, s.taxi = st.mounted, st.taxi
  s.hp, s.hpMax = num(try(G.UnitHealth, "player")), num(try(G.UnitHealthMax, "player"))
  s.isMana = num(try(G.UnitPowerType, "player")) == 0
  if s.isMana then s.mp, s.mpMax = num(try(G.UnitPower, "player", 0)), num(try(G.UnitPowerMax, "player", 0)) end
  s.hunter = s.class == "HUNTER"
  local inGroup = try(G.IsInGroup)
  s.inGroup = inGroup and true or false
  -- auras du joueur : repas, boisson, bandage récent, aspects
  local combatAspect, travelAspect = {}, {}
  for _, id in ipairs({ S.HAWK, S.MONKEY }) do local n = spellName(id) if n then combatAspect[n] = true end end
  for _, id in ipairs({ S.CHEETAH, S.PACK }) do local n = spellName(id) if n then travelAspect[n] = true end end
  for _, a in ipairs(auraScan("player", "HELPFUL")) do
    local k = a.name and EAT_NAMES[a.name]
    if k then
      local left = (a.expires and a.expires > 0) and (a.expires - s.t) or nil
      if k == "f" or k == "b" then s.eating, s.eatLeft = true, left end
      if k == "d" or k == "b" then s.drinking, s.drinkLeft = true, left end
    end
    if a.name and combatAspect[a.name] then s.combatAspect = a.name end
    if a.name and travelAspect[a.name] then s.travelAspect = a.name end
  end
  for _, a in ipairs(auraScan("player", "HARMFUL")) do
    if a.spellId == S.BANDAGED or (a.name and BANDAGED_NAMES[a.name]) then
      s.bandaged, s.bandagedExp, s.bandagedDur = true, a.expires, a.duration
    end
  end
  -- familier
  if s.hunter then
    s.petKnown = knows(S.CALL)
    s.petExists = try(G.UnitExists, "pet") and true or false
    if s.petExists then
      s.petDead = try(G.UnitIsDead, "pet") and true or false
      s.petHp, s.petHpMax = num(try(G.UnitHealth, "pet")), num(try(G.UnitHealthMax, "pet"))
      if s.petDead or s.petHp == 0 then petDied = true elseif s.petHp and s.petHp > 0 then petDied = false end
      local PI = G.C_PetInfo or {}
      local h = num(try(PI.GetPetHappiness or G.GetPetHappiness))
      s.happy = h
      for _, a in ipairs(auraScan("pet", "HELPFUL")) do
        if a.spellId == S.FEED_EFFECT or a.name == spellName(S.FEED_EFFECT) then s.feeding = true end
      end
    elseif lastPetHP == 0 then
      petDied = true
    end
    s.petDied = petDied and not (s.petExists and not s.petDead)
    lastPetHP = s.petHp
    local H = T.modules and T.modules.Hunter
    if H and type(H.AmmoInfo) == "function" then
      local ok, a = pcall(H.AmmoInfo, H)
      if ok and type(a) == "table" then s.ammo = a end
    end
  end
  local tex = try(G.UnitExists, "target")
  if tex then
    s.target = {
      hostile = try(G.UnitCanAttack, "player", "target") and true or false,
      dead = try(G.UnitIsDead, "target") and true or false,
    }
  end
  s.sinceCombat = s.inCombat and 0 or (s.t - combatEndAt)
  s.picks = self:Picks()
  return s
end

---------------------------------------------------------------------------
-- Plan : quels boutons servent, leur macro, le texte d'état (PUR, testable)
---------------------------------------------------------------------------
M.ORDER = { "pull", "pet", "mend", "feed", "aspect", "bandage", "eat" }
M.COMBAT_KEYS = { pet = true, mend = true, aspect = true }
local OPT_OF = { pull = "pull", pet = "petCall", mend = "mend", feed = "feed", aspect = "aspect",
  bandage = "bandage", eat = "eat" }

local function pct(a, b) if a and b and b > 0 then return floor(a * 100 / b + 0.5) end end

function M:Plan(s, o)
  o = o or opt
  local P = { buttons = {}, reasons = {}, tips = {} }
  local B = P.buttons
  local busy = s.dead or s.taxi or s.mounted
  local hpP, mpP = pct(s.hp, s.hpMax), s.isMana and pct(s.mp, s.mpMax) or nil
  local needHP = hpP and hpP < (tonumber(o("hp")) or 70)
  local needMP = mpP and mpP < (tonumber(o("mana")) or 60)
  local pk = s.picks or {}
  local dH = (s.hp and s.hpMax) and (s.hpMax - s.hp) or 0
  local dM = (s.mp and s.mpMax) and (s.mpMax - s.mp) or 0
  local fVal, wVal, bVal = valueOf("food", pk.food), valueOf("water", pk.water), valueOf("bandage", pk.bandage)
  local tFood = needHP and pk.food and M.RestTime(dH, fVal or 552) or 0
  local tWater = needMP and pk.water and M.RestTime(dM, wVal or 835, true) or 0
  P.rest = max(tFood or 0, tWater or 0)

  -- bandage : plus rapide que manger, sans besoin de boire, pas de « Bandage récent »
  local bandWins = false
  if needHP and pk.bandage and not s.bandaged and not s.eating and not needMP then
    local covers = (bVal or 0) <= 0 or dH <= (bVal or 0) * 1.15
    bandWins = covers and (not pk.food or (tFood or 0) > M.BANDAGE_TIME + 1)
  end
  P.bandWins = bandWins

  if not busy and not s.inCombat then
    if bandWins then
      B.bandage = { useful = true, macro = "/use [@player] item:" .. pk.bandage, item = pk.bandage,
        icon = itemIcon(pk.bandage), title = "Bandage : " .. itemName(pk.bandage),
        tip = { ("≈ %d s au lieu de %s de repas."):format(M.BANDAGE_TIME,
          pk.food and ("~" .. floor((tFood or 0) + 0.5) .. " s") or "manger"),
          "Ne bouge pas pendant 8 s ; un coup reçu l'interrompt." } }
    end
    local wantFood = needHP and pk.food and not s.eating and not bandWins
    local wantWater = needMP and pk.water and not s.drinking
    if wantFood or wantWater then
      local lines = {}
      if wantWater then lines[#lines + 1] = "/use item:" .. pk.water end
      if wantFood then lines[#lines + 1] = "/use item:" .. pk.food end
      local title = (wantFood and wantWater) and "Manger et boire" or (wantWater and "Boire" or "Manger")
      local tip = {}
      if wantWater then tip[#tip + 1] = "Boisson : " .. itemName(pk.water) .. " (" .. itemCount(pk.water) .. ")" end
      if wantFood then tip[#tip + 1] = "Nourriture : " .. itemName(pk.food) .. " (" .. itemCount(pk.food) .. ")" end
      tip[#tip + 1] = ("Repos estimé : %d s."):format(floor(P.rest + 0.5))
      local wShare = (dM / max(1, s.mpMax or 1))
      local fShare = (dH / max(1, s.hpMax or 1))
      local icoItem = (wantWater and (not wantFood or wShare >= fShare)) and pk.water or pk.food
      B.eat = { useful = true, macro = tconcat(lines, "\n"), item = icoItem, icon = itemIcon(icoItem),
        title = title, tip = tip, water = wantWater and pk.water or nil, food = wantFood and pk.food or nil }
    end
  end

  if s.hunter then
    -- familier : appeler / ressusciter
    local petHpP = pct(s.petHp, s.petHpMax)
    if s.petKnown and not busy and (not s.petExists or s.petDead) then
      local rev, call = spellName(S.REVIVE), spellName(S.CALL)
      local dead = s.petDead or s.petDied
      if (dead and rev) or call then
        B.pet = { useful = true, title = dead and (rev or "Ressusciter le familier") or (call or "Appeler le familier"),
          macro = (dead and rev) and ("/cast " .. rev)
            or ("/cast " .. (rev and ("[@pet,dead] " .. rev .. "; [nopet] ") or "") .. call),
          icon = spellIcon(dead and S.REVIVE or S.CALL),
          tip = { dead and "Ton familier est mort : 1 clic hors combat." or "Pas de familier : 1 clic pour l'appeler." } }
      end
    end
    -- soigner le familier
    local mend = spellName(S.MEND)
    if mend and knows(S.MEND) and s.petExists and not s.petDead and not busy and petHpP
        and petHpP < (tonumber(o("petHp")) or 70) then
      B.mend = { useful = true, macro = "/cast " .. mend, icon = spellIcon(S.MEND),
        title = ("Soigner le familier (%d %%)"):format(petHpP), tip = { "Soigne pendant que tu manges ou bois." } }
    end
    -- nourrir
    local feed = spellName(S.FEED)
    if feed and knows(S.FEED) and s.petExists and not s.petDead and not busy and not s.inCombat
        and s.happy and s.happy < 3 and not s.feeding and pk.petfood then
      B.feed = { useful = true, macro = "/cast " .. feed .. "\n/use item:" .. pk.petfood, item = pk.petfood,
        icon = itemIcon(pk.petfood) or spellIcon(S.FEED),
        title = "Nourrir : " .. itemName(pk.petfood) .. (pk.petfoodQ == "partial" and " (un peu faible)" or ""),
        tip = { s.happy == 1 and "Mécontent : 75 % des dégâts, il peut s'enfuir." or "Heureux = +25 % de dégâts." } }
    end
    -- aspect
    local hawkId = knows(S.HAWK) and S.HAWK or (knows(S.MONKEY) and S.MONKEY or nil)
    local hawk = hawkId and spellName(hawkId)
    if hawk and not busy then
      local travelId = (s.inGroup and knows(S.PACK)) and S.PACK or (knows(S.CHEETAH) and S.CHEETAH or nil)
      local travel = travelId and spellName(travelId)
      local a
      if s.inCombat then
        if s.travelAspect or not s.combatAspect then a = { id = hawkId, travelMode = false } end
      elseif not s.combatAspect and not s.travelAspect then
        a = { id = hawkId, travelMode = false }
      elseif s.combatAspect and travel and not s.inGroup and (s.sinceCombat or 0) >= 20 and not needHP and not needMP
          and not (s.target and s.target.hostile and not s.target.dead) then
        a = { id = travelId, travelMode = true }
      end
      if a then
        local m
        if a.travelMode then m = "/cast [combat][mod:shift] " .. hawk .. "; " .. travel
        elseif travel then m = "/cast [nocombat,mod:shift] " .. travel .. "; " .. hawk
        else m = "/cast " .. hawk end
        B.aspect = { useful = true, macro = m, icon = spellIcon(a.id), title = spellName(a.id) or hawk,
          tip = { a.travelMode and "Pour voyager. Maj : " .. hawk .. ". En combat : toujours " .. hawk .. "."
            or ("Avant le pull" .. (travel and (". Maj : " .. travel .. " (hors combat).") or ".")) } }
      end
    end
    -- pull : marque + familier
    local mark = spellName(S.MARK)
    if mark and o("pull") and knows(S.MARK) and not busy and not s.inCombat and s.target and s.target.hostile
        and not s.target.dead then
      local lines = { "/cast [@target,harm,nodead] " .. mark }
      if s.petExists and not s.petDead then lines[#lines + 1] = "/petattack [@target,harm,nodead]" end
      B.pull = { useful = true, macro = tconcat(lines, "\n"), icon = spellIcon(S.MARK),
        title = (#lines > 1) and "Marque + familier" or mark,
        tip = { (#lines > 1) and "Pose la marque et envoie le familier sur la cible." or "Pose la marque sur la cible." } }
    end
    if s.petKnown and (not s.petExists or s.petDead) then P.reasons[#P.reasons + 1] = s.petDead and "familier mort" or "pas de familier"
    elseif s.petExists and petHpP and petHpP < 50 then P.reasons[#P.reasons + 1] = "familier " .. petHpP .. " %" end
    if s.petExists and not s.petDead and s.happy and s.happy < 3 then P.reasons[#P.reasons + 1] = "familier pas content" end
    if s.ammo and s.ammo.needs and (s.ammo.equipped or 0) == 0 then P.reasons[#P.reasons + 1] = "aucune munition" end
  end
  -- options : un bouton coupé n'apparaît pas
  for k, ok in pairs(OPT_OF) do if B[k] and not o(ok) then B[k] = nil end end
  if hpP and needHP then table.insert(P.reasons, 1, "PV " .. hpP .. " %") end
  if mpP and needMP then table.insert(P.reasons, (needHP and 2 or 1), "mana " .. mpP .. " %") end
  P.ready = #P.reasons == 0
  P.hpP, P.mpP, P.needHP, P.needMP = hpP, mpP, needHP, needMP
  -- texte d'état
  if busy or s.inCombat then
    P.status = nil
  elseif (s.eating or s.drinking) and o("timer") then
    local left = 0
    if s.eating and dH > 0 then
      local rate = M._rateH or (fVal and fVal / M.EatDuration(fVal)) or nil
      local t = rate and rate > 0 and dH / rate or s.eatLeft or 0
      if s.eatLeft then t = min(t, s.eatLeft) end
      left = max(left, t)
    end
    if s.drinking and dM > 0 then
      local rate = M._rateM or (wVal and wVal / M.EatDuration(wVal, true)) or nil
      local t = rate and rate > 0 and dM / rate or s.drinkLeft or 0
      if s.drinkLeft then t = min(t, s.drinkLeft) end
      left = max(left, t)
    end
    P.status, P.kind = (left >= 1) and ("Repos : plein dans " .. ceil(left) .. " s") or "Repos : presque plein", "rest"
  elseif bandWins and B.bandage and o("timer") then
    P.status, P.kind = ("Bande-toi : %d s au lieu de %d s"):format(M.BANDAGE_TIME, floor((tFood or 0) + 0.5)), "todo"
  elseif B.eat and o("timer") then
    local verb = (B.eat.food and B.eat.water) and "mange et bois" or (B.eat.water and "bois" or "mange")
    P.status, P.kind = ("Repos %d s : %s maintenant"):format(max(1, floor(P.rest + 0.5)), verb), "todo"
  elseif not P.ready and o("ready") then
    P.status, P.kind = "Pas prêt : " .. tconcat(P.reasons, ", ", 1, min(3, #P.reasons)), "todo"
  elseif P.ready and o("ready") then
    P.status, P.kind = "Prêt à engager", "ready"
  end
  return P
end

---------------------------------------------------------------------------
-- Barre : cadres (créés / placés hors combat uniquement)
---------------------------------------------------------------------------
local SIZE, GAP, GRIP = 30, 3, 10
local bar, grip, bg, dot, label
local buttons = {}
local ammoBtn
M._buttons = buttons

local function tooltip(owner, title, lines)
  local tt = G.GameTooltip
  if not tt then return end
  pcall(tt.SetOwner, tt, owner, "ANCHOR_TOP")
  pcall(tt.AddLine, tt, title or "", 1, 0.82, 0)
  for _, l in ipairs(lines or {}) do pcall(tt.AddLine, tt, l, 0.9, 0.9, 0.9, true) end
  pcall(tt.Show, tt)
end
local function hideTip() local tt = G.GameTooltip if tt then pcall(tt.Hide, tt) end end

local function savePos()
  if not bar then return end
  local p, _, rp, x, y = bar:GetPoint()
  if p then db().pos = { p, rp or p, floor((x or 0) + 0.5), floor((y or 0) + 0.5) } end
end

local function placeBar()
  if not bar or inCombat() then return end
  local p = db().pos
  bar:ClearAllPoints()
  if type(p) == "table" and p[1] then bar:SetPoint(p[1], G.UIParent, p[2] or p[1], p[3] or 0, p[4] or 0)
  else bar:SetPoint("BOTTOM", G.UIParent, "BOTTOM", 0, 190) end
  local sc = (tonumber(opt("scale")) or 100) / 100
  pcall(bar.SetScale, bar, max(0.6, min(1.6, sc)))
end

local function createBar()
  if bar or inCombat() or type(G.CreateFrame) ~= "function" then return bar end
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIQuickBar", G.UIParent)
  if not ok or not f then return nil end
  bar = f
  f:SetSize(GRIP + 4, SIZE + 6)
  f:SetFrameStrata("MEDIUM")
  if f.SetMovable then f:SetMovable(true) end
  if f.SetClampedToScreen then f:SetClampedToScreen(true) end
  bg = f:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints(f)
  if bg.SetColorTexture then bg:SetColorTexture(0, 0, 0, 0.45) else bg:SetTexture(0, 0, 0, 0.45) end
  -- poignée (glisser = déplacer, hors combat)
  local gok, gr = pcall(G.CreateFrame, "Frame", nil, f)
  if gok and gr then
    grip = gr
    gr:SetSize(GRIP, SIZE)
    gr:SetPoint("LEFT", f, "LEFT", 2, 0)
    local gt = gr:CreateTexture(nil, "ARTWORK")
    gt:SetAllPoints(gr)
    if gt.SetColorTexture then gt:SetColorTexture(1, 0.82, 0, 0.35) else gt:SetTexture(1, 0.82, 0, 0.35) end
    gr.tex = gt
    gr:EnableMouse(true)
    if gr.RegisterForDrag then gr:RegisterForDrag("LeftButton") end
    gr:SetScript("OnDragStart", function() if not inCombat() and bar then bar:StartMoving() end end)
    gr:SetScript("OnDragStop", function() if bar then bar:StopMovingOrSizing() savePos() end end)
    gr:SetScript("OnEnter", function(self)
      tooltip(self, "Raccourcis", { "Glisse pour déplacer (hors combat).",
        "Les boutons n'apparaissent que quand ils servent.", "Réglages : Options > Raccourcis." })
    end)
    gr:SetScript("OnLeave", hideTip)
  end
  dot = f:CreateTexture(nil, "OVERLAY")
  dot:SetSize(8, 8)
  if dot.SetColorTexture then dot:SetColorTexture(0.3, 0.9, 0.3, 1) end
  label = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  label:SetJustifyH("LEFT")
  placeBar()
  if T.RegisterMovable then
    T:RegisterMovable(f, "quickfight", "Raccourcis de combat", {
      onMoved = function(point, x, y) db().pos = { point, point, floor(x or 0), floor(y or 0) } end,
      onReset = function() db().pos = nil placeBar() end,
    })
  end
  return bar
end

local function setCooldown(b)
  if not (b and b.cd and b.cd.SetCooldown) then return end
  local st, du
  if b.key == "bandage" and M._lastSnap and M._lastSnap.bandaged and M._lastSnap.bandagedExp then
    du = M._lastSnap.bandagedDur or 60
    st = M._lastSnap.bandagedExp - du
  elseif b.itemID then
    local C = G.C_Container
    st, du = try(C and C.GetItemCooldown or G.GetItemCooldown, b.itemID)
  end
  if st and du and du > 1.5 then pcall(b.cd.SetCooldown, b.cd, st, du)
  elseif b.cd.Clear then pcall(b.cd.Clear, b.cd) end
end

local function createButton(key)
  if buttons[key] then return buttons[key] end
  if inCombat() or not createBar() then return nil end
  local ok, b = pcall(G.CreateFrame, "Button", "TreeAIQuick_" .. key, bar, "SecureActionButtonTemplate")
  if not ok or not b then return nil end
  b.key = key
  b:SetSize(SIZE, SIZE)
  if b.RegisterForClicks then b:RegisterForClicks("AnyUp", "AnyDown") end
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  if b.icon.SetTexCoord then b.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92) end
  b.count = b:CreateFontString(nil, "OVERLAY", "NumberFontNormal")
  b.count:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -1, 1)
  if b.SetHighlightTexture then pcall(b.SetHighlightTexture, b, "Interface\\Buttons\\ButtonHilight-Square", "ADD") end
  local cok, cd = pcall(G.CreateFrame, "Cooldown", nil, b, "CooldownFrameTemplate")
  if cok and cd then cd:SetAllPoints(b) b.cd = cd end
  b:SetScript("OnEnter", function(self)
    local w = self.want or {}
    local lines = {}
    for _, l in ipairs(w.tip or {}) do lines[#lines + 1] = l end
    lines[#lines + 1] = "|cff999999Touche : macro « /click TreeAIQuick_" .. key .. " ».|r"
    tooltip(self, w.title or key, lines)
  end)
  b:SetScript("OnLeave", hideTip)
  -- après le clic (code non sécurisé, sans effet sur l'action) : mémorise le repas pour le minuteur
  b:SetScript("PostClick", function(self) M:OnUsed(self.key, self.want) end)
  b:Hide()
  buttons[key] = b
  return b
end

local function createAmmoButton()
  if ammoBtn then return ammoBtn end
  if inCombat() or not createBar() then return nil end
  local ok, b = pcall(G.CreateFrame, "Button", "TreeAIQuick_ammo", bar)
  if not ok or not b then return nil end
  b.key = "ammo"
  b:SetSize(SIZE, SIZE)
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  b.count = b:CreateFontString(nil, "OVERLAY", "NumberFontNormal")
  b.count:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -1, 1)
  b:SetScript("OnClick", function() M:BuyAmmo() end)
  b:SetScript("OnEnter", function(self)
    local o = M._ammoOffer
    local n = M:AmmoToBuy()
    local lines = {}
    if o and o.cost then
      local c = floor(o.cost * n + 0.5)
      lines[#lines + 1] = "Prix : " .. ((T.Fmt and T.Fmt.Money) and T.Fmt.Money(c) or tostring(c))
    end
    lines[#lines + 1] = ("Stock visé : %d (Options > Raccourcis)."):format(tonumber(opt("ammoBuy")) or 1000)
    lines[#lines + 1] = "Vérifie la place dans ton carquois."
    tooltip(self, ("Acheter %d %s"):format(n, o and o.name or "munitions"), lines)
  end)
  b:SetScript("OnLeave", hideTip)
  b:Hide()
  ammoBtn = b
  return b
end

---------------------------------------------------------------------------
-- Munitions chez le marchand (achat = clic du joueur)
---------------------------------------------------------------------------
local function merchantItem(i)
  local MF = G.C_MerchantFrame
  if MF and MF.GetItemInfo then
    local info = try(MF.GetItemInfo, i)
    if type(info) == "table" then
      return info.name, info.texture, info.price, info.stackCount or info.quantity, info.numAvailable
    end
  end
  return try(G.GetMerchantItemInfo, i)
end

function M:ScanMerchant()
  M._ammoOffer = nil
  if T:PlayerClass() ~= "HUNTER" or not opt("ammo") then return end
  local H = T.modules and T.modules.Hunter
  local a = H and H.AmmoInfo and select(2, pcall(H.AmmoInfo, H))
  if type(a) ~= "table" or not a.kind then return end
  local want = a.kind == "arrow" and 2 or 3
  local n = tonumber((try(G.GetMerchantNumItems))) or 0
  local lvl = tonumber((try(G.UnitLevel, "player"))) or 1
  local best
  for i = 1, min(n, 200) do
    local id = try(G.GetMerchantItemID, i)
    if id then
      local f = API().ItemInfo
      local r = f and { f(id) } or {}
      local ilvl, minLvl, classID, subID = r[4], r[5], r[12], r[13]
      if classID == 6 and subID == want and (minLvl or 0) <= lvl then
        local name, tex, price, qty, avail = merchantItem(i)
        if (avail == nil or avail ~= 0) and (not best or (ilvl or 0) > (best.ilvl or 0)) then
          best = { index = i, id = id, name = name or r[1], icon = tex or r[10], ilvl = ilvl,
            cost = (price and qty and qty > 0) and price / qty or nil,
            maxStack = tonumber((try(G.GetMerchantItemMaxStack, i))) or 200 }
        end
      end
    end
  end
  M._ammoOffer = best
end

function M:AmmoToBuy()
  local target = tonumber(opt("ammoBuy")) or 1000
  local H = T.modules and T.modules.Hunter
  local a = H and H.AmmoInfo and select(2, pcall(H.AmmoInfo, H))
  local have = type(a) == "table" and (a.total or 0) or 0
  local need = target - have
  if need < 200 then return 0 end
  return ceil(need / 200) * 200
end

function M:BuyAmmo()
  local o = M._ammoOffer
  if not o or inCombat() then return false end
  local n = self:AmmoToBuy()
  if o.cost and o.cost > 0 then
    local money = (API().Money and API().Money()) or 0
    n = min(n, floor(money / o.cost / 200) * 200)
  end
  if n <= 0 then return false end
  local buy = G.BuyMerchantItem
  if type(buy) ~= "function" then return false end
  local guard = 0
  T:UserAction(function()
    while n > 0 and guard < 25 do
      local q = min(n, o.maxStack or 200)
      try(buy, o.index, q)
      n, guard = n - q, guard + 1
    end
  end)
  T:After(0.6, function() M:InvalidatePicks() M:Request() end)
  return true
end

---------------------------------------------------------------------------
-- Rafraîchissement (regroupé ≤ 4/s) ; disposition hors combat seulement
---------------------------------------------------------------------------
local pending = false
function M:Request(delay)
  if pending then return end
  pending = true
  T:After(delay or 0.25, function() pending = false if M:IsEnabled() then M:Refresh() end end)
end

local readySince, wasReady = 0, true
local shownKeys = {}

local function applyLayout(P, combatPrep)
  if inCombat() or not createBar() then return end
  local x = GRIP + 6
  local order = {}
  for _, key in ipairs(M.ORDER) do
    local w = P.buttons[key]
    local show = w ~= nil
    if combatPrep then show = M.COMBAT_KEYS[key] and (w ~= nil or (buttons[key] and buttons[key]._macro)) end
    local b = (show or buttons[key]) and createButton(key)
    if b then
      if show then
        if w and b._macro ~= w.macro then
          b:SetAttribute("type", "macro")
          b:SetAttribute("macrotext", w.macro)
          b._macro = w.macro
        end
        if w then
          b.want = w
          b.itemID = w.item
          b.icon:SetTexture(w.icon or 134400)
          b.count:SetText(w.item and tostring(itemCount(w.item)) or "")
        end
        b:ClearAllPoints()
        b:SetPoint("LEFT", bar, "LEFT", x, 0)
        b:Show()
        pcall(b.SetAlpha, b, 1)
        x = x + SIZE + GAP
        order[#order + 1] = key
      elseif b:IsShown() then
        b:Hide()
      end
    end
  end
  -- munitions (bouton normal, marchand ouvert)
  local wantAmmo = M._ammoOffer and opt("ammo") and M:AmmoToBuy() > 0
  if wantAmmo and not combatPrep and createAmmoButton() then
    ammoBtn.icon:SetTexture(M._ammoOffer.icon or 132382)
    ammoBtn.count:SetText(tostring(M:AmmoToBuy()))
    ammoBtn:ClearAllPoints()
    ammoBtn:SetPoint("LEFT", bar, "LEFT", x, 0)
    ammoBtn:Show()
    x = x + SIZE + GAP
    order[#order + 1] = "ammo"
  elseif ammoBtn and ammoBtn:IsShown() then
    ammoBtn:Hide()
  end
  dot:ClearAllPoints()
  dot:SetPoint("LEFT", bar, "LEFT", x + 2, 0)
  label:ClearAllPoints()
  label:SetPoint("LEFT", bar, "LEFT", x + 14, 0)
  local tw = (P.status and label.GetStringWidth and label:GetStringWidth()) or 0
  bar:SetWidth(x + (P.status and (tw + 20) or 0) + 2)
  shownKeys = {}
  for _, k in ipairs(order) do shownKeys[k] = true end
  M._shown = order
end

local DOT = { ready = { 0.3, 0.9, 0.3 }, rest = { 0.35, 0.65, 1 }, todo = { 1, 0.6, 0.1 } }

function M:Refresh()
  if not opt("bar") then
    if bar then T:RunOutOfCombat("QuickFight:hide", function() if bar then bar:Hide() end end) end
    return
  end
  local s = self:Snapshot()
  M._lastSnap = s
  -- vitesse de récupération mesurée pendant le repas (meilleure que la valeur théorique)
  if s.eating or s.drinking then
    if not eatT0 then eatT0, eatHP0, eatMP0 = s.t, s.hp, s.mp
    elseif s.t - eatT0 >= 2 then
      M._rateH = (s.eating and s.hp and eatHP0 and s.hp > eatHP0) and (s.hp - eatHP0) / (s.t - eatT0) or nil
      M._rateM = (s.drinking and s.mp and eatMP0 and s.mp > eatMP0) and (s.mp - eatMP0) / (s.t - eatT0) or nil
    end
  else
    eatT0, M._rateH, M._rateM = nil, nil, nil
  end
  local P = self:Plan(s)
  M._lastPlan = P
  -- « prêt » : montré 6 s après être redevenu prêt, puis la barre se fait discrète
  if P.ready and not wasReady then readySince = s.t T:After(6.2, function() M:Request() end) end
  wasReady = P.ready
  if P.kind == "ready" and (s.t - readySince > 6) then P.status = nil end
  if label then label:SetText(P.status or "") end
  if s.inCombat then
    -- en combat : uniquement l'alpha (jamais Show / Hide / SetPoint / SetAttribute)
    for key, b in pairs(buttons) do
      if shownKeys[key] then pcall(b.SetAlpha, b, P.buttons[key] and 1 or 0.3) end
      setCooldown(b)
    end
  else
    if not bar and not next(P.buttons) and not P.status then return end
    T:RunOutOfCombat("QuickFight:layout", function()
      if not createBar() then return end
      if not bar:IsShown() then bar:Show() end
      if label then label:SetText(P.status or "") end
      applyLayout(P)
      for _, b in pairs(buttons) do setCooldown(b) end
    end)
  end
  if bar then
    local any = (M._shown and #M._shown > 0) or P.status
    pcall(bg.SetAlpha, bg, any and 1 or 0)
    if grip and grip.tex then pcall(grip.tex.SetAlpha, grip.tex, any and 1 or 0.25) end
    local c = P.status and DOT[P.kind or "todo"]
    if dot then
      pcall(dot.SetAlpha, dot, c and 1 or 0)
      if c and dot.SetColorTexture then dot:SetColorTexture(c[1], c[2], c[3], 1) end
    end
  end
  -- minuteur : 1 s seulement pendant le repas
  if (s.eating or s.drinking) and not M._ticker and T.NewTicker then
    M._ticker = T:NewTicker(1, function() M:Refresh() end, M)
  elseif not (s.eating or s.drinking) and M._ticker then
    M._ticker:Cancel()
    M._ticker = nil
  end
end

function M:OnUsed(key, want)
  if key == "eat" and type(want) == "table" then M._lastEat = { food = want.food, water = want.water, t = now() } end
  self:Request(0.5)
end

--- Fight se tait (« Avant d'engager ») quand la barre affiche l'indicateur : un seul propriétaire.
function M:OwnsPrePull()
  return self:IsEnabled() and opt("bar") and opt("ready") and bar ~= nil and bar:IsShown() and true or false
end

---------------------------------------------------------------------------
-- Événements
---------------------------------------------------------------------------
function M:OnEnable()
  local function req() M:Request() end
  local function bags() M:InvalidatePicks() M:Request() end
  if self.RegisterUnitEvent then
    self:RegisterUnitEvent("UNIT_HEALTH", "player", "pet", req)
    self:RegisterUnitEvent("UNIT_MAXHEALTH", "player", "pet", req)
    self:RegisterUnitEvent("UNIT_POWER_UPDATE", "player", nil, req)
    self:RegisterUnitEvent("UNIT_AURA", "player", "pet", req)
    self:RegisterUnitEvent("UNIT_PET", "player", nil, function() lastPetHP = nil M:InvalidatePicks() M:Request() end)
    self:RegisterUnitEvent("UNIT_HAPPINESS", "pet", nil, req)
  end
  self:RegisterEvent("PLAYER_TARGET_CHANGED", req)
  self:RegisterEvent("BAG_UPDATE_DELAYED", bags)
  self:RegisterEvent("PLAYER_LEVEL_UP", bags)
  self:RegisterEvent("SPELLS_CHANGED", bags)
  self:RegisterEvent("PLAYER_EQUIPMENT_CHANGED", bags)
  self:RegisterEvent("SPELL_UPDATE_COOLDOWN", req)
  self:RegisterEvent("PLAYER_REGEN_DISABLED", function()
    -- le vrai client lève le verrou juste après cet événement : dernière chance de montrer les boutons de combat
    if not inCombat() and M._lastPlan then applyLayout(M._lastPlan, true) end
    M:Request(0.1)
  end)
  self:RegisterEvent("PLAYER_REGEN_ENABLED", function() combatEndAt = now() M:Request(0.1) end)
  self:RegisterEvent("MERCHANT_SHOW", function() M:ScanMerchant() M:Request(0.1) end)
  self:RegisterEvent("MERCHANT_CLOSED", function() M._ammoOffer = nil M:Request(0.1) end)
  self:RegisterEvent("PLAYER_ENTERING_WORLD", bags)
  if T.RegisterSlash then
    T:RegisterSlash("raccourcis", function()
      db().bar = not opt("bar")
      M:Print(opt("bar") and "Barre de raccourcis affichée." or "Barre de raccourcis masquée.")
      M:Request(0)
    end, "Affiche / masque la barre de raccourcis (manger, familier, aspect, pull)")
  end
  self:Request(1)
end

function M:OnDisable()
  if M._ticker then M._ticker:Cancel() M._ticker = nil end
  T:RunOutOfCombat("QuickFight:hide", function() if bar then bar:Hide() end end)
end

function M:OnOptionChanged(key)
  if key == "scale" then T:RunOutOfCombat("QuickFight:place", placeBar) end
  if key == "ammo" or key == "ammoBuy" then M:ScanMerchant() end
  self:Request(0.1)
end

M.GetBar = function() return bar end
M.GetAmmoButton = function() return ammoBtn end
