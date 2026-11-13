-- TreeAI — module QoL « Confort » (agent REPLACE, 4.0.1) : ce que Leatrix Plus faisait pour le joueur, en légal,
-- chaque chose une option :
--  * vendre les objets gris + réparer chez le marchand (Maj enfoncée = pause) : la barre de quête (Quick) le fait déjà
--    quand elle est allumée avec ses options « vendre / réparer » → jamais deux fois ; sinon ici, même règle des gris
--    (Quick.GreyItems / Inventaire), par paquets de 10 toutes les 0,3 s tant que la fenêtre du marchand est ouverte,
--    hors combat (UseContainerItem marchand ouvert, RepairAllItems si l'or suffit) ;
--  * butin instantané : avec le butin automatique du jeu, chaque emplacement est pris tout de suite (LootSlot à
--    LOOT_READY, comme Leatrix « Faster auto loot ») ; jamais de confirmation de butin lié acceptée à ta place ;
--  * butin automatique du jeu (CVar autoLootDefault) et recul de caméra maximal (cameraDistanceMaxZoomFactor) : un clic
--    dans les options (réglages du jeu, gardés par le jeu), hors combat.
-- Pas repris (exprès) : masquer les messages d'erreur, déplacer ou masquer des cadres de Blizzard (blocages / taint),
-- accepter des invitations ou des résurrections (fenêtres protégées). Aucune sortie dans le chat : le résultat d'une
-- vente s'affiche brièvement en haut de l'écran (comme une réponse à un clic). Lua 5.1, aucune globale.
local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local API = T.API or {}

local Q = T:RegisterModule("QoL", {
  title = "Confort",
  description = "Ce que Leatrix Plus faisait pour toi, en légal : vendre les gris et réparer, butin instantané, "
    .. "butin automatique du jeu, recul de caméra.",
  -- (4.1 LAYOUT) dialogues de quête : coupés d'office (le joueur les allume), la récompense entourée sans rien choisir
  defaults = { enabled = true, sellRepair = true, fastLoot = true, questAuto = false, questTake = false, questReward = true },
  charDefaults = {},
  options = {
    { key = "questAuto", type = "toggle", label = "Accepter / rendre les quêtes tout seul (Maj = pas cette fois)", section = "Quêtes" },
    { key = "questTake", type = "toggle", label = "Prendre la récompense tout seul quand il n'y en a qu'une (coupé d'office)", section = "Quêtes" },
    { key = "questReward", type = "toggle", label = "Récompense : entourer le meilleur choix (jamais choisi à ta place)", section = "Quêtes" },
    { key = "sellRepair", type = "toggle", label = "Vendre les objets gris et réparer chez le marchand (Maj = pause)", section = "Marchand" },
    { key = "fastLoot", type = "toggle", label = "Butin instantané (avec le butin automatique du jeu)", section = "Butin" },
    { key = "autoLootBtn", type = "button", label = "Butin automatique du jeu : activer / couper", section = "Butin",
      onClick = function() local m = T.QoL if m and m.ToggleAutoLoot then m.ToggleAutoLoot() end end },
    { key = "cameraBtn", type = "button", label = "Caméra : recul maximal / normal", section = "Caméra",
      onClick = function() local m = T.QoL if m and m.ToggleCamera then m.ToggleCamera() end end },
  },
})
T.QoL = Q
do
  local fam = type(T.OPTIONS_FAMILY) == "table" and T.OPTIONS_FAMILY[4]   -- « Sacs, or, métiers »
  if type(fam) == "table" and type(fam.mods) == "table" then
    local has = false
    for _, n in ipairs(fam.mods) do if n == "QoL" then has = true end end
    if not has then table.insert(fam.mods, 1, "QoL") end
  end
end

local floor, min = math.floor, math.min
local fmt = string.format
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function g(name) return rawget(G, name) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function num(v) if v == nil or secret(v) then return nil end return tonumber(v) end
local function opt(k) return Q.db and Q.db[k] ~= false end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function now() return try(g("GetTime")) or 0 end
local function after(s, fn) if T.After then T:After(s, fn) else fn() end end
local function shift() return try(g("IsShiftKeyDown")) and true or false end
local function money() return num(API.Money and API.Money() or try(g("GetMoney"))) or 0 end
local function Fmoney(c) return (T.Fmt and T.Fmt.Money and T.Fmt.Money(c)) or (floor((c or 0) / 100) .. " pa") end
local function note(msg) if T.ClickNote then T:ClickNote(msg) end end
local function cvar(k)
  local CV = g("C_CVar")
  local f = (type(CV) == "table" and CV.GetCVar) or g("GetCVar")
  local v = try(f, k)
  if secret(v) then return nil end
  return v ~= nil and tostring(v) or nil
end
local function setCVar(k, v)
  local f = g("SetCVar")
  if type(f) ~= "function" then local CV = g("C_CVar") f = type(CV) == "table" and CV.SetCVar or nil end
  return f and pcall(f, k, v) or false
end

-- ===================================================================== marchand : gris + réparation
Q.stats = { sold = 0, repaired = 0, looted = 0 }
local merchantOpen = false
--- la barre de quête s'en charge déjà ? (allumée + ses options vendre / réparer) → on ne fait rien (jamais deux fois)
function Q.QuickDoesIt()
  local K = T.modules and T.modules.Quick
  return (K and K._active and type(K.db) == "table" and (K.db.autoSell ~= false or K.db.autoRepair ~= false)) and true or false
end
local function greyItems()
  local K = T.Quick
  if K and type(K.GreyItems) == "function" then
    local list, value = try(K.GreyItems)
    if type(list) == "table" then return list, tonumber(value) or 0 end
  end
  local I = T.modules and T.modules.Inventory
  if I and type(I.GreyItems) == "function" then
    local list, value = try(I.GreyItems)
    if type(list) == "table" then return list, tonumber(value) or 0 end
  end
  local list, value = {}, 0
  if API.IterBags then
    for bag, slot, it in API.IterBags() do
      if it.quality == 0 and not it.noValue then
        local sell = num(select(11, try(API.ItemInfo, it.link or it.itemID))) or 0
        if sell > 0 then
          list[#list + 1] = { bag = bag, slot = slot, value = sell * (it.count or 1) }
          value = value + sell * (it.count or 1)
        end
      end
    end
  end
  return list, value
end
Q.GreyItems = greyItems
local function useSlot(bag, slot)
  local CC = g("C_Container")
  local f = (type(CC) == "table" and CC.UseContainerItem) or g("UseContainerItem")
  return f and pcall(f, bag, slot) or false
end
local function merchantShown()
  local mf = g("MerchantFrame")
  if type(mf) == "table" and type(mf.IsShown) == "function" then return try(mf.IsShown, mf) and true or false end
  return merchantOpen
end

--- vend les gris (paquets de 10 / 0,3 s) + répare si l'or suffit ; jamais en combat ni marchand fermé
function Q.SellAndRepair()
  if inCombat() or not merchantOpen then return nil end
  local parts = {}
  if try(g("CanMerchantRepair")) then
    local cost = num(try(g("GetRepairAllCost"))) or 0
    if cost > 0 and cost <= money() then
      try(g("RepairAllItems"))
      Q.stats.repaired = Q.stats.repaired + 1
      parts[#parts + 1] = "Réparé : " .. Fmoney(cost)
    elseif cost > 0 then
      parts[#parts + 1] = "Réparation : pas assez d'or (" .. Fmoney(cost) .. ")"
    end
  end
  -- (5.0 BAGS) les gris passent par l'assistant d'inventaire : avis préparés par petits paquets, chaque pile relue
  -- juste avant d'être vendue, jamais un gris qu'un gardien réclame
  local B = T.Bags
  if type(B) == "table" and B._active and type(B.SellGreys) == "function" and type(B.Warm) == "function" then
    B.Warm(function()
      if not merchantOpen or inCombat() then return end
      local n, value = B.SellGreys()
      if (tonumber(n) or 0) > 0 then
        Q.stats.sold = Q.stats.sold + n
        parts[#parts + 1] = fmt("Vendu : %d objet%s gris (%s)", n, n > 1 and "s" or "", Fmoney(value))
      end
      if #parts > 0 then note(table.concat(parts, " · ")) end
    end)
    return parts
  end
  local list, value = greyItems()
  if #list > 0 then
    local i = 0
    local function batch()
      if not merchantOpen or inCombat() then return end
      for _ = 1, 10 do
        i = i + 1
        local it = list[i]
        if not it then break end
        if useSlot(it.bag, it.slot) then Q.stats.sold = Q.stats.sold + 1 end
      end
      if list[i + 1] then after(0.3, batch) end
    end
    batch()
    parts[#parts + 1] = fmt("Vendu : %d objet%s gris (%s)", #list, #list > 1 and "s" or "", Fmoney(value))
  end
  if #parts > 0 then note(table.concat(parts, " · ")) end
  return parts
end
local function onMerchant()
  if merchantOpen then return end                  -- MERCHANT_SHOW et l'événement d'interaction : un seul passage
  merchantOpen = true
  if not opt("sellRepair") or shift() or Q.QuickDoesIt() then return end
  -- (5.1 LOOK) Leatrix Plus vend déjà les gris / répare tout seul (ses options cochées, lien branché) : jamais deux fois
  local Ad = T.Addons
  if type(Ad) == "table" and type(Ad.MerchantAuto) == "function" and try(Ad.MerchantAuto) then return end
  -- un instant plus tard : la fenêtre du marchand est vraiment ouverte (et ses données arrivées)
  after(0.15, function() if merchantShown() then Q.SellAndRepair() end end)
end

-- ===================================================================== butin instantané
local lastLoot = -1
local function autoLooting(autoloot)
  if autoloot ~= nil and not secret(autoloot) then return autoloot and true or false end
  local on = cvar("autoLootDefault") == "1"
  local mod = try(g("IsModifiedClick"), "AUTOLOOTTOGGLE") and true or false
  return on ~= mod
end
--- LOOT_READY (autoloot) : chaque emplacement pris tout de suite si le jeu allait de toute façon tout ramasser
function Q.FastLoot(autoloot)
  if not opt("fastLoot") or not autoLooting(autoloot) then return 0 end
  local t = now()
  if t - lastLoot < 0.3 then return 0 end          -- un seul passage par fenêtre de butin
  lastLoot = t
  local slot = g("LootSlot")
  local n = num(try(g("GetNumLootItems"))) or 0
  if type(slot) ~= "function" or n <= 0 then return 0 end
  local k = 0
  for i = min(n, 40), 1, -1 do
    if pcall(slot, i) then k = k + 1 end
  end
  Q.stats.looted = Q.stats.looted + k
  return k
end

-- ===================================================================== (4.1 LAYOUT) dialogues de quête
-- Option « Accepter / rendre les quêtes tout seul » (coupée d'office ; Maj enfoncée = pas cette fois) :
--  * accepte seulement les quêtes que le plan veut (cerveau des quêtes : liste « à prendre » ; sinon le trajet ;
--    sinon toute quête non grise) ; journal plein = rien ;
--  * rend les quêtes finies (jamais une quête qui demande de l'or) ;
--  * récompense : sans choix (0 ou 1 objet) elle est prise ; avec un choix, le meilleur est ENTOURÉ (amélioration selon
--    l'équipement porté, sinon la plus chère à la revente) et jamais choisi à ta place.
-- La barre de quête (Quick) fait déjà tout ça si son option est cochée : jamais deux fois. Rien dans le chat ;
-- AcceptQuest / CompleteQuest / GetQuestReward / Select*Quest ne sont pas protégées (dialogue ouvert par le joueur).
Q.qstats = { accepted = 0, turnedIn = 0, highlighted = 0 }
function Q.QuickQuests()
  local K = T.modules and T.modules.Quick
  return (K and K._active and type(K.db) == "table" and K.db.autoQuest == true) and true or false
end
local function questAuto() return Q.db and Q.db.questAuto == true and not shift() and not Q.QuickQuests() end
Q.QuestAutoOn = function() return Q.db and Q.db.questAuto == true or false end
local function logFull()
  local QL = g("C_QuestLog")
  local n
  if QL and QL.GetNumQuestLogEntries then local _, q = try(QL.GetNumQuestLogEntries) n = num(q) end
  if not n then local _, q = try(g("GetNumQuestLogEntries")) n = num(q) end
  local cap = (QL and QL.GetMaxNumQuestsCanAccept and num(try(QL.GetMaxNumQuestsCanAccept))) or num(g("MAX_QUESTLOG_QUESTS")) or 20
  return n and n >= cap or false
end
local function fold(s) return tostring(s or ""):lower():gsub("%s+", " ") end
--- le plan veut-il cette quête ? (id et / ou titre) → vrai, raison
function Q.Wanted(qid, title, trivial)
  qid = num(qid)
  local B = T.QuestBrain
  if type(B) == "table" and type(B.PickupList) == "function" and not (B.IsEnabled and not B:IsEnabled()) then
    local ok, list = pcall(B.PickupList, 60)
    if ok and type(list) == "table" then
      for _, e in ipairs(list) do
        if type(e) == "table" and ((qid and e.questID == qid) or (title and e.title and fold(e.title) == fold(title))) then
          return true, "plan"
        end
      end
      return false, "pas dans le plan"
    end
  end
  local P = T.Planner
  if qid and P and type(P.steps) == "table" then
    for _, st in ipairs(P.steps) do if type(st) == "table" and st.q == qid then return true, "trajet" end end
  end
  if trivial then return false, "grise" end
  return true, "pas de plan"
end
function Q.OnQuestDetail()
  if not questAuto() or logFull() then return false end
  if try(g("QuestGetAutoAccept")) then return false end
  local qid = num(try(g("GetQuestID")))
  local title = try(g("GetTitleText"))
  if secret(title) then title = nil end
  local QL = g("C_QuestLog")
  local trivial = qid and QL and QL.IsQuestTrivial and try(QL.IsQuestTrivial, qid) and true or false
  if not Q.Wanted(qid, title, trivial) then return false end
  if try(g("AcceptQuest")) ~= false then Q.qstats.accepted = Q.qstats.accepted + 1 return true end
  return false
end
function Q.OnQuestProgress()
  if not questAuto() then return false end
  if not try(g("IsQuestCompletable")) then return false end
  if (num(try(g("GetQuestMoneyToGet"))) or 0) > 0 then return false end   -- quête payante : au joueur de voir
  try(g("CompleteQuest"))
  return true
end
local function gear() local Gm = T.modules and T.modules.Gear return type(Gm) == "table" and Gm or nil end
--- le meilleur choix de récompense (Gear : amélioration selon l'équipement, sinon la plus chère) → index, why, valeur
function Q.BestReward()
  local Gm = gear()
  if not (Gm and type(Gm.RankQuestRewards) == "function") then return nil end
  local i, v, why = try(Gm.RankQuestRewards)
  return num(i), why, v
end
function Q.HighlightReward()
  if not opt("questReward") then return nil end
  local Gm = gear()
  -- le module Équipement allumé entoure déjà (avec sa ligne) : jamais deux fois
  if Gm and Gm._active and type(Gm.db) == "table" and Gm.db.rewards ~= false then return nil end
  local i, why = Q.BestReward()
  if not i then return nil end
  if Gm and type(Gm.RewardGlow) == "function" and Gm.RewardGlow(i) then
    Q.qstats.highlighted = Q.qstats.highlighted + 1
    Q.lastBest = { i = i, why = why }
    return i
  end
  return nil
end
function Q.OnQuestComplete()
  local n = num(try(g("GetNumQuestChoices"))) or 0
  if n > 1 then
    -- un choix : entouré seulement, jamais pris à ta place (même avec l'option)
    after(0.3, function() Q.HighlightReward() end)
    return "highlight"
  end
  if not questAuto() then return nil end
  if not (Q.db and Q.db.questTake == true) then return nil end   -- (4.1.2) la récompense : seulement si tu l'as voulu
  if (num(try(g("GetQuestMoneyToGet"))) or 0) > 0 then return nil end
  if try(g("GetQuestReward"), n) ~= false then Q.qstats.turnedIn = Q.qstats.turnedIn + 1 return "taken" end
  return nil
end
function Q.OnGossip()
  if not questAuto() then return nil end
  local GI = g("C_GossipInfo")
  if type(GI) == "table" and GI.GetActiveQuests then
    for _, q in ipairs(try(GI.GetActiveQuests) or {}) do
      if type(q) == "table" and q.isComplete and q.questID and GI.SelectActiveQuest then try(GI.SelectActiveQuest, q.questID) return "turnin" end
    end
    if logFull() then return nil end
    for _, q in ipairs((GI.GetAvailableQuests and try(GI.GetAvailableQuests)) or {}) do
      if type(q) == "table" and q.questID and GI.SelectAvailableQuest and not q.repeatable
          and Q.Wanted(q.questID, q.title, q.isTrivial) then
        try(GI.SelectAvailableQuest, q.questID) return "pickup"
      end
    end
    return nil
  end
  return Q.OnQuestGreeting()
end
function Q.OnQuestGreeting()
  if not questAuto() then return nil end
  for i = 1, num(try(g("GetNumActiveQuests"))) or 0 do
    local _, done = try(g("GetActiveTitle"), i)
    if done then try(g("SelectActiveQuest"), i) return "turnin" end
  end
  if logFull() then return nil end
  for i = 1, num(try(g("GetNumAvailableQuests"))) or 0 do
    local trivial, _, rep = try(g("GetAvailableQuestInfo"), i)
    local title = try(g("GetAvailableTitle"), i)
    if not rep and Q.Wanted(nil, not secret(title) and title or nil, trivial) then try(g("SelectAvailableQuest"), i) return "pickup" end
  end
  return nil
end
--- un clic (page Quêtes) : allumer / couper « Accepter / rendre tout seul » (le module Confort s'allume avec)
function Q.SetQuestAuto(on)
  if on and T.SetModuleEnabled and not Q._active then T:SetModuleEnabled("QoL", true) end
  if Q.db then Q.db.questAuto = on and true or false end
  return Q.db and Q.db.questAuto
end

-- ===================================================================== réglages du jeu (un clic, hors combat)
function Q.ToggleAutoLoot()
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return nil end
  local on = cvar("autoLootDefault") == "1"
  setCVar("autoLootDefault", on and "0" or "1")
  note(on and "Butin automatique du jeu : coupé" or "Butin automatique du jeu : activé")
  return not on
end
Q.CAMERA_MAX, Q.CAMERA_NORMAL = "2.6", "1.9"
function Q.ToggleCamera()
  if inCombat() then if T.CombatRefused then T:CombatRefused("Après le combat.") end return nil end
  local v = num(cvar("cameraDistanceMaxZoomFactor")) or 1.9
  local maxed = v >= 2.55
  setCVar("cameraDistanceMaxZoomFactor", maxed and Q.CAMERA_NORMAL or Q.CAMERA_MAX)
  note(maxed and "Caméra : recul normal" or "Caméra : recul maximal")
  return not maxed
end
function Q.State()
  return { autoLoot = cvar("autoLootDefault") == "1", camera = (num(cvar("cameraDistanceMaxZoomFactor")) or 1.9) >= 2.55,
    quick = Q.QuickDoesIt() }
end

-- ===================================================================== cycle de vie
function Q:OnEnable()
  -- (4.1.2) une fois chez tout le monde : quêtes et récompenses automatiques coupées (le joueur ne pouvait plus couper)
  if self.db and (tonumber(self.db.questV) or 0) < 1 then self.db.questV, self.db.questAuto, self.db.questTake = 1, false, false end
  self:RegisterEvent("MERCHANT_SHOW", function() onMerchant() end)
  self:RegisterEvent("MERCHANT_CLOSED", function() merchantOpen = false end)
  self:RegisterEvent("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", function(_, _, kind)
    local E = g("Enum") and G.Enum.PlayerInteractionType
    if type(E) == "table" and kind ~= nil and not secret(kind) and kind == E.Merchant then onMerchant() end
  end)
  self:RegisterEvent("LOOT_READY", function(_, _, autoloot) Q.FastLoot(autoloot) end)
  self:RegisterEvent("QUEST_DETAIL", function() Q.OnQuestDetail() end)
  self:RegisterEvent("QUEST_PROGRESS", function() Q.OnQuestProgress() end)
  self:RegisterEvent("QUEST_COMPLETE", function() Q.OnQuestComplete() end)
  self:RegisterEvent("GOSSIP_SHOW", function() Q.OnGossip() end)
  self:RegisterEvent("QUEST_GREETING", function() Q.OnQuestGreeting() end)
  self:RegisterEvent("QUEST_FINISHED", function()
    local Gm = gear()
    if Q.lastBest and Gm and type(Gm.HideRewardGlow) == "function" and not Gm._active then Gm.HideRewardGlow() end
    Q.lastBest = nil
  end)
end
function Q:OnDisable() merchantOpen = false end

-- (4.1.2) un interrupteur facile : /tai quetesauto on | off (récompense : /tai recompense on | off)
if T.RegisterSlash then
  T:RegisterSlash("quetesauto", function(rest)
    if not Q.db then return end
    rest = tostring(rest or ""):lower()
    Q.db.questAuto = (rest == "on") or (rest == "" and not Q.db.questAuto)
    T:Print(Q.db.questAuto and "Quêtes : accepter / rendre tout seul ACTIVÉ (Maj = pas cette fois)" or "Quêtes : accepter / rendre tout seul COUPÉ")
  end, "accepter / rendre les quêtes tout seul : on / off")
  T:RegisterSlash("recompense", function(rest)
    if not Q.db then return end
    rest = tostring(rest or ""):lower()
    Q.db.questTake = (rest == "on") or (rest == "" and not Q.db.questTake)
    T:Print(Q.db.questTake and "Récompense unique prise tout seul : ACTIVÉ" or "Récompense prise tout seul : COUPÉ (tu cliques)")
  end, "prendre la récompense unique tout seul : on / off")
end
