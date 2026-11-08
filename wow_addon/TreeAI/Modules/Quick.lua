-- TreeAI — module Quick (agent QUICK1) : « Raccourcis », quelques secondes gagnées à chaque minute de quête.
-- Une petite barre d'icônes déplaçable (TreeAI:RegisterMovable) qui ne montre que les boutons utiles maintenant :
--   * objet de quête (bouton sécurisé type=item, l'objet choisi par le module Quêtes : son propre bouton se tait)
--   * « Rendre / Accepter tout » : interrupteur de l'automatisme de quêtes (Maj enfoncée = pause)
--   * vendre les gris + réparer (marchand ouvert ; automatique si l'option est cochée)
--   * prendre tout le courrier (boîte aux lettres ouverte ; jamais un contre-remboursement)
--   * pierre de foyer / monture (boutons sécurisés ; chasseur sans monture : Aspect du guépard)
--   * partager les quêtes au groupe (un clic : QuestLogPushQuest par quête qu'un membre n'a pas, hors combat)
--   * cibler le prochain objectif (bouton sécurisé type=macro, « /targetexact Nom » écrit hors combat)
-- Automatismes permis sans clic (comme Leatrix Plus / addons d'auto-quête), chacun une option :
--   AcceptQuest / CompleteQuest / GetQuestReward (récompense : seule amélioration selon le score de Boost/Gear,
--   sinon la plus chère à la revente ; deux améliorations = jamais de choix automatique), sélection de quêtes
--   dans les dialogues, option de dialogue unique sûre (vol, marchand, banque, maître), vente des gris,
--   réparation. Jamais : sort, cible, objet ou mouvement sans clic du joueur. Cadres sécurisés créés, modifiés,
--   montrés et cachés HORS COMBAT seulement (TreeAI:RunOutOfCombat) ; en combat, la barre ne bouge pas.
-- Événementiel (aucun ticker), aucune sortie dans le chat. Lua 5.1, aucune globale.
local T = _G.TreeAI
if not (T and T.RegisterModule) then return end
local G = _G
local API = T.API or {}

local OPTIONS = {
  { key = "bar", type = "toggle", label = "Afficher la barre de quête", section = "Barre" },
  { key = "btnItem", type = "toggle", label = "Bouton : objet de quête", section = "Barre" },
  { key = "btnTarget", type = "toggle", label = "Bouton : cibler le prochain objectif", section = "Barre" },
  { key = "btnShare", type = "toggle", label = "Bouton : partager les quêtes au groupe", section = "Barre" },
  { key = "btnHearth", type = "toggle", label = "Bouton : pierre de foyer", section = "Barre" },
  { key = "btnMount", type = "toggle", label = "Bouton : monture (ou Aspect du guépard)", section = "Barre" },
  { key = "btnMail", type = "toggle", label = "Bouton : prendre tout le courrier", section = "Barre" },
  { key = "autoQuest", type = "toggle", label = "Accepter et rendre les quêtes tout seul (Maj = pause)", section = "Quêtes" },
  { key = "autoReward", type = "toggle", label = "Choisir la récompense quand le choix est évident", section = "Quêtes" },
  { key = "skipTrivial", type = "toggle", label = "Ignorer les quêtes grises et répétables", section = "Quêtes" },
  { key = "autoGossip", type = "toggle", label = "Dialogue à un seul choix : vol, marchand, banque, maître", section = "PNJ" },
  { key = "autoSell", type = "toggle", label = "Vendre les objets gris chez le marchand", section = "Marchand et courrier" },
  { key = "autoRepair", type = "toggle", label = "Réparer chez le marchand", section = "Marchand et courrier" },
  { key = "lootHint", type = "toggle", label = "Rappel : activer le butin automatique", section = "Marchand et courrier" },
}
local defaults = { enabled = true }
for _, o in ipairs(OPTIONS) do defaults[o.key] = true end
-- (4.0 QA) les automatismes de quête et de dialogue ne s'allument jamais tout seuls (ni par « Tout allumer ») :
-- coupés par défaut, proposés UNE fois par une ligne (« oui » = clic), ou cochés dans les options
defaults.autoQuest, defaults.autoReward, defaults.autoGossip = false, false, false

local M = T:RegisterModule("Quick", {
  title = "Barre de quête",
  defaults = defaults,
  charDefaults = {},
  options = OPTIONS,
})
T.Quick = M
M.OPTIONS = OPTIONS

-- profil léger : la barre fait partie de l'essentiel ; rangée dans « Quêtes et trajet » des options
if type(T.MODULE_INFO) == "table" and not T.MODULE_INFO.Quick then
  T.MODULE_INFO.Quick = { lite = true, desc = "Barre de quête : objet de quête, cible, partage, foyer ; quêtes automatiques si tu le veux" }
end
do
  local fam = T.OPTIONS_FAMILY
  local first = type(fam) == "table" and fam[1]
  if type(first) == "table" and type(first.mods) == "table" then
    local has = false
    for _, n in ipairs(first.mods) do if n == "Quick" then has = true end end
    if not has then table.insert(first.mods, 1, "Quick") end
  end
end

local floor, max, min = math.floor, math.max, math.min
local fmt = string.format
local function g(name) return rawget(G, name) end
local function pass(ok, ...) if ok then return ... end return nil end
local function try(fn, ...) if type(fn) ~= "function" then return nil end return pass(pcall(fn, ...)) end
local function secret(v) local f = g("issecretvalue") return type(f) == "function" and try(f, v) and true or false end
local function sv(v) if v == nil or secret(v) then return nil end return v end
local function num(v) return tonumber((sv(v))) end
local function C(ns, fn) local t = g(ns) return type(t) == "table" and t[fn] or nil end
local function opt(k) return M.db and M.db[k] ~= false end
local function inCombat() return T.InCombat and T:InCombat() or false end
local function shift() return try(g("IsShiftKeyDown")) and true or false end
local function mod(n) local m = T.modules and T.modules[n] return (m and (not m.IsEnabled or m:IsEnabled())) and m or nil end
local function level() return num(try(g("UnitLevel"), "player")) or 1 end
local function money() return num(API.Money and API.Money() or try(g("GetMoney"))) or 0 end
local function Fmoney(c) return (T.Fmt and T.Fmt.Money and T.Fmt.Money(c)) or (floor((c or 0) / 100) .. " pa") end
local function after(s, fn) if T.After then T:After(s, fn) end end

local HEARTH = 6948
local CHEETAH = 5118
local SIZE, GAP, PAD = 30, 4, 4

-- ===================================================================== moteur pur (testé)
local E = {}
M.Engine = E

--- Choix de la récompense. choices = { {upgrade = pct|nil, usable = bool, equip = bool, sell = cuivre} , ... }
-- @return index|nil, raison ("single" | "upgrade" | "sell" | "conflict" | "unknown")
function E.PickReward(choices, scored)
  local n = #choices
  if n == 0 then return 0, "single" end
  if n == 1 then return 1, "single" end
  local ups, best, bestV, unknown = {}, nil, -1, false
  for i, c in ipairs(choices) do
    if c.unknown then unknown = true end
    if c.usable and (c.upgrade or 0) >= 1 then ups[#ups + 1] = i end
    local v = tonumber(c.sell) or 0
    if v > bestV then best, bestV = i, v end
  end
  if unknown then return nil, "unknown" end
  if #ups == 1 then return ups[1], "upgrade" end
  if #ups >= 2 then return nil, "conflict" end
  if not scored then
    -- sans score d'équipement : on ne devine pas s'il y a un objet portable dans le lot
    for _, c in ipairs(choices) do if c.equip and c.usable then return nil, "conflict" end end
  end
  if best and bestV > 0 then return best, "sell" end
  return nil, "conflict"
end

local SAFE_ICONS = { [132057] = "taxi", [132060] = "vendor", [132050] = "banker", [132058] = "trainer" }
local SAFE_TYPES = { taxi = true, vendor = true, banker = true, trainer = true }
--- Option de dialogue à prendre toute seule : la seule option sûre, ou le vol chez un maître de vol.
-- options = { {id=, icon=, type=, name=} }, hasQuests = le PNJ propose / attend des quêtes
function E.GossipPick(options, hasQuests)
  if type(options) ~= "table" or hasQuests then return nil end
  local function kind(o) return (o.type and SAFE_TYPES[o.type] and o.type) or SAFE_ICONS[tonumber(o.icon) or 0] end
  for _, o in ipairs(options) do if kind(o) == "taxi" then return o end end
  if #options == 1 and kind(options[1]) then return options[1] end
  return nil
end

--- Quêtes à partager : celles qu'au moins un membre n'a pas (inconnu = on partage), partageables.
function E.ShareList(quests, haveFn, members)
  local out = {}
  for _, q in ipairs(quests or {}) do
    if q.index and not q.complete and not q.failed and q.pushable ~= false then
      local missing = members == 0
      for u = 1, members do
        if haveFn(q, u) == false then missing = true break end
      end
      if missing then out[#out + 1] = q end
    end
  end
  return out
end

--- Nom du monstre d'un objectif « Ours enragé tué » / « Gnolls tués » / « Ogre slain » (casse gardée) ;
-- isMob = le jeu dit que c'est un monstre (sinon il faut le suffixe « tué »).
function E.MobName(name, isMob)
  if type(name) ~= "string" then return nil end
  local s = T.ObjectiveName and T.ObjectiveName(name) or name
  local base = s
  for _, suf in ipairs({ " tuées", " tués", " tuée", " tué", " slain", " killed" }) do
    if #base > #suf and base:sub(-#suf) == suf then base = base:sub(1, -#suf - 1) break end
  end
  if base == s and not isMob then return nil end
  if base == "" then return nil end
  return base
end

--- Texte de macro de ciblage (nom propre, ≤ 255 caractères, sans saut de ligne)
function E.TargetMacro(name)
  if type(name) ~= "string" then return nil end
  name = name:gsub("[\r\n;]", ""):gsub("^%s+", ""):gsub("%s+$", "")
  if name == "" or #name > 60 then return nil end
  return "/targetexact " .. name
end

-- ===================================================================== automatisme de quêtes
local function questLogFull()
  local QL = g("C_QuestLog")
  local n, maxN
  if QL and QL.GetNumQuestLogEntries then
    local _, q = try(QL.GetNumQuestLogEntries)
    n = num(q)
  end
  if not n then local _, q = try(g("GetNumQuestLogEntries")) n = num(q) end
  maxN = (QL and QL.GetMaxNumQuestsCanAccept and num(try(QL.GetMaxNumQuestsCanAccept))) or num(g("MAX_QUESTLOG_QUESTS")) or 20
  return n and n >= maxN or false
end

local function questOn() return opt("autoQuest") and not shift() end
M.stats = { accepted = 0, turnedIn = 0, rewards = 0, gossip = 0, sold = 0, repaired = 0, mails = 0, shared = 0 }

function M.OnQuestDetail()
  if not questOn() or questLogFull() then return end
  if try(g("QuestGetAutoAccept")) then return end            -- déjà acceptée par le jeu
  local f = g("AcceptQuest")
  if f and try(f) ~= false then M.stats.accepted = M.stats.accepted + 1 end
end

function M.OnQuestProgress()
  if not questOn() then return end
  if not try(g("IsQuestCompletable")) then return end
  if (num(try(g("GetQuestMoneyToGet"))) or 0) > 0 then return end   -- quête payante : au joueur de voir
  try(g("CompleteQuest"))
end

local function boost() return T.modules and T.modules.Boost end
function M.RewardChoices()
  local n = num(try(g("GetNumQuestChoices"))) or 0
  local B = boost()
  local scored = B and type(B.UpgradePct) == "function"
  local out = {}
  for i = 1, n do
    local link = sv(try(g("GetQuestItemLink"), "choice", i))
    local _, _, count, _, usable = try(g("GetQuestItemInfo"), "choice", i)
    local c = { unknown = link == nil, sell = 0, usable = usable ~= false }
    if link then
      local info = { try(API.ItemInfo or g("GetItemInfo"), link) }
      if info[1] == nil then c.unknown = true end
      c.sell = (tonumber(info[11]) or 0) * (tonumber(count) or 1)
      local loc = info[9]
      c.equip = type(loc) == "string" and loc ~= "" and loc ~= "INVTYPE_NON_EQUIP" and loc ~= "INVTYPE_BAG"
      if scored and c.equip then
        local okU = (type(B.CanUse) ~= "function") or try(B.CanUse, link)
        c.usable = c.usable and okU and true or false
        c.upgrade = c.usable and tonumber((try(B.UpgradePct, link))) or nil
      end
    end
    out[i] = c
  end
  return out, scored
end

function M.OnQuestComplete(retry)
  if not questOn() then return end
  local n = num(try(g("GetNumQuestChoices"))) or 0
  if n > 1 and not opt("autoReward") then return end
  local choices, scored = M.RewardChoices()
  local i, why = E.PickReward(choices, scored)
  M.lastReward = why
  if why == "unknown" and (retry or 0) < 3 then
    after(0.4, function() M.OnQuestComplete((retry or 0) + 1) end)   -- infos d'objets pas encore en cache
    return
  end
  if not i then return end
  if try(g("GetQuestReward"), i) ~= false then
    M.stats.turnedIn = M.stats.turnedIn + 1
    if n > 1 then M.stats.rewards = M.stats.rewards + 1 end
  end
end

-- dialogues : quêtes finies d'abord, puis nouvelles quêtes (pas grises / répétables si l'option le dit)
local function gossipQuests()
  local GI = g("C_GossipInfo")
  if not GI then return {}, {} end
  return (GI.GetActiveQuests and try(GI.GetActiveQuests)) or {}, (GI.GetAvailableQuests and try(GI.GetAvailableQuests)) or {}
end

function M.OnGossip()
  if shift() then return end
  local GI = g("C_GossipInfo") or {}
  local active, avail = gossipQuests()
  if opt("autoQuest") then
    for _, q in ipairs(active) do
      if type(q) == "table" and q.isComplete and q.questID and GI.SelectActiveQuest then
        try(GI.SelectActiveQuest, q.questID) return
      end
    end
    if not questLogFull() then
      for _, q in ipairs(avail) do
        if type(q) == "table" and q.questID and GI.SelectAvailableQuest
            and not (opt("skipTrivial") and (q.isTrivial or q.repeatable or q.frequency and q.frequency > 0)) then
          try(GI.SelectAvailableQuest, q.questID) return
        end
      end
    end
  end
  if not opt("autoGossip") then return end
  local hasQuests = #active > 0 or #avail > 0
  local raw = (GI.GetOptions and try(GI.GetOptions)) or {}
  local options = {}
  for i, o in ipairs(raw) do
    if type(o) == "table" then
      options[#options + 1] = { id = o.gossipOptionID or o.optionID or i, icon = o.icon, type = o.type, name = o.name, index = i }
    end
  end
  local pick = E.GossipPick(options, hasQuests)
  if not pick then return end
  M.stats.gossip = M.stats.gossip + 1
  if GI.SelectOption then try(GI.SelectOption, pick.id) else try(g("SelectGossipOption"), pick.index) end
end

function M.OnQuestGreeting()
  if not questOn() then return end
  for i = 1, num(try(g("GetNumActiveQuests"))) or 0 do
    local _, done = try(g("GetActiveTitle"), i)
    if done then try(g("SelectActiveQuest"), i) return end
  end
  if questLogFull() then return end
  for i = 1, num(try(g("GetNumAvailableQuests"))) or 0 do
    local trivial, freq, rep = try(g("GetAvailableQuestInfo"), i)
    if not (opt("skipTrivial") and (trivial or rep or (tonumber(freq) or 0) > 1)) then
      try(g("SelectAvailableQuest"), i) return
    end
  end
end

-- ===================================================================== marchand / courrier
local status   -- petite ligne d'état sous la barre (fontstring)
local function say(text)
  if not status then return end
  status:SetText(text or "")
  M._sayAt = (try(g("GetTime")) or 0)
  after(5, function()
    if status and (try(g("GetTime")) or 0) - (M._sayAt or 0) >= 4.9 then status:SetText("") end
  end)
end

local merchantOpen, mailOpen = false, false
local function greyItems()
  local I = T.modules and T.modules.Inventory
  if I and type(I.GreyItems) == "function" then
    local list, value = try(I.GreyItems)
    if type(list) == "table" then return list, tonumber(value) or 0 end
  end
  local list, value = {}, 0
  if API.IterBags then
    for bag, slot, it in API.IterBags() do
      if it.quality == 0 and not it.noValue then
        local sell = select(11, try(API.ItemInfo, it.link or it.itemID)) or 0
        list[#list + 1] = { bag = bag, slot = slot, value = sell * (it.count or 1) }
        value = value + sell * (it.count or 1)
      end
    end
  end
  return list, value
end
M.GreyItems = greyItems

local function repairCost()
  if not try(g("CanMerchantRepair")) then return 0 end
  return num(try(g("GetRepairAllCost"))) or 0
end

local function useSlot(bag, slot)
  local f = C("C_Container", "UseContainerItem") or g("UseContainerItem")
  return f and pcall(f, bag, slot) or false
end

--- Vend les gris (par paquets de 10, un paquet toutes les 0,3 s tant que le marchand est ouvert) + répare.
function M.SellAndRepair(doSell, doRepair)
  if inCombat() or not merchantOpen then return end
  local parts = {}
  if doRepair ~= false then
    local cost = repairCost()
    if cost > 0 and cost <= money() then
      try(g("RepairAllItems"))
      M.stats.repaired = M.stats.repaired + 1
      parts[#parts + 1] = "Réparé : " .. Fmoney(cost)
    end
  end
  if doSell ~= false then
    local list, value = greyItems()
    if #list > 0 then
      local i = 0
      local function batch()
        if not merchantOpen or inCombat() then return end
        for _ = 1, 10 do
          i = i + 1
          local it = list[i]
          if not it then break end
          if useSlot(it.bag, it.slot) then M.stats.sold = M.stats.sold + 1 end
        end
        if list[i + 1] then after(0.3, batch) else M.Soon() end
      end
      batch()
      parts[#parts + 1] = fmt("Vendu : %d gris (%s)", #list, Fmoney(value))
    end
  end
  if #parts > 0 then say(table.concat(parts, " · ")) end
  M.Soon()   -- (3.9.9) un seul relayout groupé (0,2 s), pas deux dans l'image de l'ouverture du marchand
end

local function inbox()
  local n = num(try(g("GetInboxNumItems"))) or 0
  local out = {}
  for i = n, 1, -1 do
    local _, _, _, _, m, cod, _, hasItem = try(g("GetInboxHeaderInfo"), i)
    m, cod, hasItem = num(m) or 0, num(cod) or 0, (hasItem == true and 1) or num(hasItem) or 0
    if cod == 0 and (m > 0 or hasItem > 0) then out[#out + 1] = { i = i, money = m, items = hasItem > 0 } end
  end
  return out
end
M.Inbox = inbox

--- Prend tout le courrier (argent + objets), une lettre toutes les 0,35 s, jamais un contre-remboursement.
function M.TakeAllMail()
  if inCombat() or not mailOpen or M._mailBusy then return end
  local list = inbox()
  if #list == 0 then return end
  M._mailBusy = true
  local k, gold = 0, 0
  local function step()
    k = k + 1
    local e = list[k]
    if not e or not mailOpen then
      M._mailBusy = false
      if gold > 0 or k > 1 then say(fmt("Courrier pris : %d lettre%s%s", k - 1, k > 2 and "s" or "",
        gold > 0 and (" · " .. Fmoney(gold)) or "")) end
      M.Layout()
      return
    end
    if e.money > 0 then try(g("TakeInboxMoney"), e.i) gold = gold + e.money end
    if e.items then
      if g("AutoLootMailItem") then try(g("AutoLootMailItem"), e.i) else try(g("TakeInboxItem"), e.i, 1) end
    end
    M.stats.mails = M.stats.mails + 1
    after(0.35, step)
  end
  step()
end

-- ===================================================================== partage de quêtes
local function members()
  if try(g("IsInRaid")) then return 0 end
  local n
  if g("GetNumGroupMembers") then
    n = (num(try(g("GetNumGroupMembers"))) or 0) - 1          -- moderne : le joueur est compté
  else
    n = num(try(g("GetNumPartyMembers"))) or 0                -- ancien : seulement les autres
  end
  return max(0, min(4, n))
end
M.Members = members

local function pushable(q)
  local QL = g("C_QuestLog")
  if QL and QL.IsPushableQuest and q.id and q.id < 100000 then return try(QL.IsPushableQuest, q.id) and true or false end
  return true
end
local function memberHas(q, u)
  local QL = g("C_QuestLog")
  local unit = "party" .. u
  if QL and QL.IsUnitOnQuest and q.id and q.id < 100000 then
    local r = try(QL.IsUnitOnQuest, unit, q.id)
    if r ~= nil then return r and true or false end
  end
  local f = g("IsUnitOnQuest")
  if f and q.index then
    local r = try(f, q.index, unit)
    if r == nil or r == false or r == 0 then return false end
    return true
  end
  return nil
end

function M.ShareCandidates()
  local Q = T.modules and T.modules.Quests
  local list = Q and Q.lastList or {}
  local qs = {}
  for _, q in ipairs(list) do
    qs[#qs + 1] = { id = q.id, index = q.index, title = q.title, complete = q.complete, failed = q.failed, pushable = pushable(q) }
  end
  return E.ShareList(qs, memberHas, members())
end

function M.ShareAll()
  if inCombat() or M._shareBusy then return end
  local list = M.ShareCandidates()
  if #list == 0 then say("Tout le groupe a déjà tes quêtes") return end
  local push = g("QuestLogPushQuest")
  if not push then return end
  M._shareBusy = true
  local k = 0
  local function step()
    k = k + 1
    local q = list[k]
    if not q or inCombat() then
      M._shareBusy = false
      say(fmt("Partagé : %d quête%s", k - 1, k > 2 and "s" or ""))
      return
    end
    try(push, q.index)          -- l'index du journal : on ne touche jamais à la sélection du journal
    M.stats.shared = M.stats.shared + 1
    after(0.6, step)
  end
  step()
end

-- ===================================================================== monture / foyer / cible
local mountCache
local function findMount()
  local MJ = g("C_MountJournal")
  if MJ and MJ.GetMountIDs and MJ.GetMountInfoByID then
    local best
    for _, id in ipairs(try(MJ.GetMountIDs) or {}) do
      local name, spellID, icon, _, usable, _, fav, _, _, hide, collected = try(MJ.GetMountInfoByID, id)
      if name and collected and usable and not hide then
        if fav then return { macro = "/cast " .. name, icon = icon, name = name } end
        best = best or { macro = "/cast " .. name, icon = icon, name = name, spell = spellID }
      end
    end
    if best then return best end
  end
  if API.IterBags then
    local lv = level()
    for _, _, it in API.IterBags() do
      local id = it.itemID
      if id then
        local name, _, _, _, minLv, _, _, _, _, icon, _, cls, sub = try(API.ItemInfo, id)
        if name and cls == 15 and sub == 5 and (tonumber(minLv) or 0) <= lv then
          return { macro = "/use item:" .. id, icon = icon, name = name, item = id }
        end
      end
    end
  end
  if API.IsSpellKnown and API.IsSpellKnown(CHEETAH) then
    local name = API.SpellName and API.SpellName(CHEETAH)
    if name then
      return { macro = "/cast !" .. name, icon = (API.SpellIcon and API.SpellIcon(CHEETAH)) or "Interface\\Icons\\Ability_Mount_JungleTiger",
        name = name, aspect = true }
    end
  end
  return nil
end

function M.NextTarget()
  local Cmp = T.modules and T.modules.Compass
  local t = Cmp and type(Cmp.Current) == "function" and try(Cmp.Current)
  if type(t) == "table" and (t.kind == "objective" or t.kind == "turnin") and type(t.npc) == "string" then
    return t.npc, t
  end
  local Q = T.modules and T.modules.Quests
  local best
  for _, q in ipairs(Q and Q.lastList or {}) do
    if not q.complete then
      for _, o in ipairs(q.obj or {}) do
        local nm = not o.finished and E.MobName(o.name, o.t == "m")
        if nm then
          local s = (q.watched and 2 or 0) + (q.dist and max(0, 1 - q.dist / 1000) or 0)
          if not best or s > best.s then best = { s = s, name = nm, q = q } end
        end
      end
    end
  end
  if best then return best.name, best.q end
  return nil
end

local function questItem()
  if not opt("btnItem") then return nil end
  local Q = mod("Quests")
  if not (Q and Q.lastList) then return nil end
  local pick = type(Q.PickItem) == "function" and Q.PickItem(Q.lastList) or nil
  if not pick then
    for _, q in ipairs(Q.lastList) do
      if q.item and (not q.complete or q.item.showComplete) then pick = q break end
    end
  end
  return pick
end
--- Le module Quêtes se tait quand notre barre porte l'objet de quête (un seul bouton à l'écran).
function M.OwnsQuestItem() return M._active and opt("bar") and opt("btnItem") and true or false end

-- ===================================================================== barre
local bar, buttons, order = nil, {}, {}
M.buttons = buttons

local function tooltip(self)
  local tt = g("GameTooltip")
  if not tt then return end
  pcall(tt.SetOwner, tt, self, "ANCHOR_TOP")
  local info = self.tip and self.tip(self)
  if type(info) == "table" then
    if info.link then pcall(tt.SetHyperlink, tt, info.link) else pcall(tt.SetText, tt, info[1] or "", 1, 0.82, 0) end
    for j = 2, #info do pcall(tt.AddLine, tt, info[j], 0.8, 0.8, 0.8, true) end
  end
  -- (UIREDO) boutons sécurisés : la touche (ou où la choisir), toujours la même ligne en bas
  local Th = T.Theme
  if self.secure and Th and Th.KeyHint then pcall(tt.AddLine, tt, Th:KeyHint(self), 0.4, 0.8, 1, true) end
  pcall(tt.Show, tt)
end

local function makeButton(key, secure, icon, tipFn, onClick)
  if buttons[key] or not bar or inCombat() then return buttons[key] end
  local name = "TreeAIQuick" .. key:gsub("^%l", string.upper)
  local ok, b = pcall(G.CreateFrame, "Button", name, bar, secure and "SecureActionButtonTemplate" or nil)
  if not ok or not b then return nil end
  b:SetSize(SIZE, SIZE)
  if b.RegisterForClicks then b:RegisterForClicks("AnyUp", "AnyDown") end
  b.icon = b:CreateTexture(nil, "ARTWORK")
  b.icon:SetAllPoints(b)
  if b.icon.SetTexCoord then b.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92) end
  b.icon:SetTexture(icon)
  local hl = b:CreateTexture(nil, "HIGHLIGHT")
  hl:SetAllPoints(b)
  hl:SetTexture("Interface\\Buttons\\ButtonHilight-Square")
  if hl.SetBlendMode then hl:SetBlendMode("ADD") end
  b.count = b:CreateFontString(nil, "OVERLAY", "NumberFontNormal")
  b.count:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -1, 2)
  local cok, cd = pcall(G.CreateFrame, "Cooldown", nil, b, "CooldownFrameTemplate")
  if cok and cd then cd:SetAllPoints(b) b.cd = cd end
  b.key, b.tip, b.secure = key, tipFn, secure
  if T.Theme and T.Theme.ShortcutStyle then T.Theme:ShortcutStyle(b) end   -- (UIREDO) même bouton partout + touche liée
  b:SetScript("OnEnter", tooltip)
  b:SetScript("OnLeave", function() local tt = g("GameTooltip") if tt then pcall(tt.Hide, tt) end end)
  if onClick then
    -- bouton simple : action d'un clic du joueur (TreeAI:UserAction) ; bouton sécurisé : le jeu fait l'action
    b:SetScript("OnClick", function() if T.UserAction then T:UserAction(onClick) else pcall(onClick) end end)
  end
  b:Hide()
  buttons[key] = b
  order[#order + 1] = key
  return b
end

local function setCooldown(b, s, d)
  if b and b.cd and s and d and d > 1.5 and b.cd.SetCooldown then pcall(b.cd.SetCooldown, b.cd, s, d) end
end
local function itemCooldown(id)
  local s, d = try(C("C_Container", "GetItemCooldown"), id)
  if not s then s, d = try(g("GetItemCooldown"), id) end
  return num(s), num(d)
end

local function ensureBar()
  if bar then return bar end
  if inCombat() or not G.CreateFrame then return nil end
  local ok, f = pcall(G.CreateFrame, "Frame", "TreeAIQuickBar", G.UIParent)
  if not ok or not f then return nil end
  bar = f
  f:SetSize(SIZE + 2 * PAD, SIZE + 2 * PAD)
  f:SetPoint("BOTTOM", G.UIParent, "BOTTOM", 0, 190)
  f:SetFrameStrata("MEDIUM")
  if f.SetClampedToScreen then f:SetClampedToScreen(true) end
  if T.Theme and T.Theme.Skin then pcall(T.Theme.Skin, T.Theme, f, "hud") end
  status = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  status:SetPoint("TOP", f, "BOTTOM", 0, -2)
  status:SetText("")
  M.bar = f
  if T.RegisterMovable then pcall(T.RegisterMovable, T, f, "quick", "Barre de quête") end
  -- (UIREDO) amarrée dans la barre « Raccourcis » (Shortcuts.lua) : elle bouge avec elle
  if T.RegisterShortcutBar then
    pcall(T.RegisterShortcutBar, T, f, { id = "quest", label = "Quêtes · voyage", order = 1, inset = PAD, movable = "quick",
      onUndock = function()
        f:ClearAllPoints()
        f:SetPoint("BOTTOM", G.UIParent, "BOTTOM", 0, 190)
        if T.RegisterMovable then pcall(T.RegisterMovable, T, f, "quick", "Barre de quête") end
      end })
  end

  makeButton("toggle", false, "Interface\\GossipFrame\\ActiveQuestIcon", function()
    local on = opt("autoQuest")
    return { on and "Quêtes automatiques : activées" or "Quêtes automatiques : coupées",
      "Accepte les quêtes, les rend et choisit la récompense quand le choix est évident.",
      "Deux améliorations possibles : tu choisis toi-même.", "Maj enfoncée chez un PNJ = pause.", "Clic : activer / couper." }
  end, function()
    M.SetAuto(not opt("autoQuest"))
  end)
  makeButton("item", true, 134400, function(self)
    return { link = self.link, "Clic : utiliser l'objet de « " .. tostring(self.questTitle or "quête") .. " »" }
  end)
  makeButton("target", true, "Interface\\Icons\\Ability_Hunter_SniperShot", function(self)
    return { "Cibler : " .. tostring(self.targetName or ""), self.questTitle and ("Pour « " .. self.questTitle .. " »") or nil,
      "Pourquoi : c'est l'objectif de ta quête en cours.", "Clic : le cibler (il doit être assez proche)." }
  end)
  makeButton("share", false, "Interface\\Icons\\INV_Scroll_03", function()
    local n = #M.ShareCandidates()
    return { "Partager les quêtes au groupe", fmt("Pourquoi : %d quête%s qu'un membre n'a pas.", n, n > 1 and "s" or ""),
      "Clic : partage une quête toutes les 0,6 s." }
  end, M.ShareAll)
  makeButton("sell", false, "Interface\\Icons\\INV_Misc_Coin_02", function()
    local list, value = greyItems()
    local cost = repairCost()
    local t = { "Vendre les gris et réparer", "Clic : vend tous les objets gris et répare." }
    if #list > 0 then t[#t + 1] = fmt("%d gris : %s", #list, Fmoney(value)) end
    if cost > 0 then t[#t + 1] = "Réparation : " .. Fmoney(cost) end
    return t
  end, function() M.SellAndRepair(true, true) end)
  makeButton("mail", false, "Interface\\Icons\\INV_Letter_02", function()
    local n = #inbox()
    return { "Prendre tout le courrier", fmt("Pourquoi : %d lettre%s avec or ou objets.", n, n > 1 and "s" or ""),
      "Clic : prend l'or et les objets (les contre-remboursements restent dans la boîte)." }
  end, M.TakeAllMail)
  makeButton("hearth", true, "Interface\\Icons\\INV_Misc_Rune_01", function()
    local bind = sv(try(g("GetBindLocation")))
    return { "Pierre de foyer", "Pourquoi : elle est prête et tu n'es pas à l'auberge.",
      bind and ("Clic : retour à " .. bind .. " (10 s d'incantation)") or "Clic : utiliser la pierre de foyer" }
  end)
  makeButton("mount", true, "Interface\\Icons\\Ability_Mount_JungleTiger", function(self)
    return { self.mountName or "Monture", self.aspect and "Pas de monture : Aspect du guépard (+30 % de vitesse)." or
      "Clic : monter / descendre." }
  end)
  return bar
end

--- Boutons utiles maintenant (pur, hors cadres) : liste ordonnée { key, data }
function M.Wanted()
  local out = {}
  if not opt("bar") then return out end
  out[#out + 1] = { key = "toggle" }
  local q = questItem()
  if q and q.item then out[#out + 1] = { key = "item", q = q } end
  if opt("btnTarget") then
    local name, src = M.NextTarget()
    local macro = E.TargetMacro(name)
    if macro then out[#out + 1] = { key = "target", macro = macro, name = name, title = src and src.title } end
  end
  if opt("btnShare") and members() > 0 and #M.ShareCandidates() > 0 then out[#out + 1] = { key = "share" } end
  if merchantOpen then
    local list = greyItems()
    if #list > 0 or repairCost() > 0 then out[#out + 1] = { key = "sell" } end
  end
  if opt("btnMail") and mailOpen and #inbox() > 0 then out[#out + 1] = { key = "mail" } end
  if opt("btnHearth") and (num(API.ItemCount and API.ItemCount(HEARTH) or try(g("GetItemCount"), HEARTH)) or 0) > 0
      and not try(g("IsResting")) then
    out[#out + 1] = { key = "hearth" }
  end
  if opt("btnMount") and not try(g("IsInInstance")) then
    mountCache = mountCache or findMount() or false
    if mountCache then out[#out + 1] = { key = "mount", m = mountCache } end
  end
  return out
end

local function apply(b, w)
  if w.key == "toggle" then
    local on = opt("autoQuest")
    b.icon:SetTexture(on and "Interface\\GossipFrame\\ActiveQuestIcon" or "Interface\\GossipFrame\\AvailableQuestIcon")
    if b.icon.SetDesaturated then b.icon:SetDesaturated(not on) end
    b.icon:SetAlpha(on and 1 or 0.55)
  elseif w.key == "item" then
    local it = w.q.item
    if b.itemID ~= it.id then
      b:SetAttribute("type", "item")
      b:SetAttribute("item", "item:" .. it.id)
      b.itemID = it.id
    end
    b.link, b.questTitle = it.link, w.q.title
    b.icon:SetTexture(it.icon or (API.ItemInfo and select(10, API.ItemInfo(it.id))) or 134400)
    b.count:SetText((it.charges and it.charges > 1) and it.charges or "")
    setCooldown(b, itemCooldown(it.id))
  elseif w.key == "target" then
    if b.macro ~= w.macro then
      b:SetAttribute("type", "macro")
      b:SetAttribute("macrotext", w.macro)
      b.macro = w.macro
    end
    b.targetName, b.questTitle = w.name, w.title
  elseif w.key == "hearth" then
    if not b.ready then
      b:SetAttribute("type", "item")
      b:SetAttribute("item", "item:" .. HEARTH)
      b.ready = true
      local icon = API.ItemInfo and select(10, API.ItemInfo(HEARTH))
      if icon then b.icon:SetTexture(icon) end
    end
    setCooldown(b, itemCooldown(HEARTH))
  elseif w.key == "mount" then
    local m = w.m
    local macro = m.aspect and m.macro or ("/dismount [mounted]\n/stopmacro [mounted]\n" .. m.macro)
    if b.macro ~= macro then
      b:SetAttribute("type", "macro")
      b:SetAttribute("macrotext", macro)
      b.macro = macro
    end
    b.mountName, b.aspect = m.name, m.aspect
    if m.icon then b.icon:SetTexture(m.icon) end
  elseif w.key == "sell" then
    local cost = repairCost()
    b.icon:SetTexture(#greyItems() == 0 and cost > 0 and "Interface\\Icons\\Trade_BlackSmithing" or "Interface\\Icons\\INV_Misc_Coin_02")
  elseif w.key == "share" then
    b.count:SetText(#M.ShareCandidates())
  elseif w.key == "mail" then
    b.count:SetText(#inbox())
  end
end

--- Relayout : seulement hors combat (cadres sécurisés) ; en combat, refait à la sortie (une seule fois).
function M.Layout()
  if not M._active then return end
  if M.db and not M.db.autoAsked and M.ProposeAuto then M.ProposeAuto() end   -- « non » (clic droit) retenu de suite
  T:RunOutOfCombat("Quick:layout", function()
    if not opt("bar") then if bar then bar:Hide() end M.shown = {} return end
    if not ensureBar() then return end
    local want = M.Wanted()
    local on = {}
    for _, w in ipairs(want) do on[w.key] = w end
    local x, shown = PAD, {}
    for _, w in ipairs(want) do
      local b = buttons[w.key]
      if b then
        pcall(apply, b, w)
        b:ClearAllPoints()
        b:SetPoint("LEFT", bar, "LEFT", x, 0)
        b:Show()
        x = x + SIZE + GAP
        shown[#shown + 1] = w.key
      end
    end
    for _, key in ipairs(order) do
      local b = buttons[key]
      if not on[key] and b:IsShown() then
        b:Hide()
        if key == "item" and b.itemID then b:SetAttribute("item", nil) b.itemID = nil end
      end
    end
    bar:SetWidth(max(SIZE + 2 * PAD, x - GAP + PAD))
    bar:Show()
    M.shown = shown
    if T.Shortcuts and T.Shortcuts.Changed then T.Shortcuts.Changed() end
  end)
end
M.shown = {}

-- regroupe les relayouts d'une rafale d'événements (sacs, journal) en un seul, 0,2 s plus tard
local pending = false
local function soon()
  if pending then return end
  pending = true
  after(0.2, function() pending = false M.Layout() end)
end
M.Soon = soon

-- ===================================================================== automatismes : proposés une fois (4.0 QA)
-- Jamais allumés en douce (ni par défaut, ni par « Tout allumer ») : une ligne pose la question une fois
-- (clic = oui, clic droit = non), une ligne confirme ce qui vient d'être allumé. « mod: » : l'orage de clics du
-- harnais laisse cette question tranquille.
local AUTO_ID = "mod:quick:auto"
M.AUTO_ID = AUTO_ID
local AUTO_TEXT = "Accepter et rendre les quêtes tout seul ? Clic : oui · clic droit : non"
function M.AutoConfirm(on)
  if on then
    M:Suggest("auto", "Quêtes automatiques activées : Maj enfoncée chez un PNJ = pause", 1,
      { ttl = 15, cooldown = 0, sound = false, tab = "now", kind = "always",
        tooltip = "Accepter, rendre, récompense évidente (deux améliorations : tu choisis). Options > Barre de quête pour couper." })
  else
    M:Clear("auto")
  end
end
--- le joueur répond (ligne, bouton de la barre) : quêtes acceptées / rendues + récompense évidente, et la confirmation
function M.SetAuto(on)
  if not M.db then return end
  on = on and true or false
  M.db.autoQuest, M.db.autoReward, M.db.autoAsked = on, on, true
  T:ClearSuggestion(AUTO_ID)
  M.AutoConfirm(on)
  M.Layout()
end
function M.ProposeAuto()
  if not (M.db and M._active) or M.db.autoAsked then return end
  if M.db.autoQuest then M.db.autoAsked = true return end                 -- déjà coché à la main
  if T.IsDismissed and T:IsDismissed(AUTO_ID, AUTO_TEXT) then              -- clic droit : non merci
    M.db.autoAsked = true
    return
  end
  T:Suggest(AUTO_ID, AUTO_TEXT, 2, { module = "Quick", ttl = 0, sound = false, tab = "now", kind = "always",
    icon = "Interface\\GossipFrame\\ActiveQuestIcon",
    tooltip = "Accepte les quêtes proposées, les rend et prend la récompense quand le choix est évident "
      .. "(deux améliorations : tu choisis). Maj enfoncée chez un PNJ = pause. Options > Barre de quête pour changer.",
    onClick = function() M.SetAuto(true) end })
end

-- ===================================================================== butin automatique
local function cvar(k)
  local f = C("C_CVar", "GetCVar") or g("GetCVar")
  return sv(try(f, k))
end
function M.LootHint()
  if not opt("lootHint") or M._lootHinted then return end
  if cvar("autoLootDefault") ~= "0" then M:Clear("loot") return end
  M._lootHinted = true
  M:Suggest("loot", "Active le butin automatique : un clic ici", 1, { ttl = 600, cooldown = 3600, sound = false,
    icon = "Interface\\Icons\\INV_Misc_Bag_10", tooltip = "Chaque monstre fouillé d'un clic au lieu de deux.",
    onClick = function()
      if inCombat() then return end
      local f = C("C_CVar", "SetCVar") or g("SetCVar")
      if f then pcall(f, "autoLootDefault", "1") end
      M:Clear("loot")
    end })
end

-- ===================================================================== branchements
local hooked = false
local function hookQuests()
  local Q = T.modules and T.modules.Quests
  if hooked or not (Q and type(Q.UpdateButton) == "function") then return end
  hooked = true
  local orig = Q.UpdateButton
  Q.UpdateButton = function(...)
    local r = orig(...)
    if M._active then soon() end
    return r
  end
end

function M:OnEnable()
  -- (3.9.9) relayout quand le journal a vraiment changé (QUESTS_UPDATED : signature zone + progression, ou 15 s),
  -- pas à chaque relecture de Quêtes (UpdateButton tourne à chaque QUEST_LOG_UPDATE)
  if self.On then self:On("QUESTS_UPDATED", function() if M._active then soon() end end) else hookQuests() end
  local function ev(e, fn) self:RegisterEvent(e, function(_, _, ...) fn(...) end) end
  ev("QUEST_DETAIL", M.OnQuestDetail)
  ev("QUEST_PROGRESS", M.OnQuestProgress)
  ev("QUEST_COMPLETE", function() M.OnQuestComplete(0) end)
  ev("QUEST_GREETING", M.OnQuestGreeting)
  ev("GOSSIP_SHOW", M.OnGossip)
  ev("MERCHANT_SHOW", function()
    merchantOpen = true
    if not shift() and (opt("autoSell") or opt("autoRepair")) then
      -- un instant plus tard : la fenêtre du marchand est vraiment ouverte (et ses données arrivées)
      after(0.15, function()
        local mf = g("MerchantFrame")
        if mf and mf.IsShown and not try(mf.IsShown, mf) then return end
        M.SellAndRepair(opt("autoSell"), opt("autoRepair"))
      end)
    end
    soon()
  end)
  ev("MERCHANT_CLOSED", function() merchantOpen = false soon() end)
  ev("MAIL_SHOW", function() mailOpen = true soon() end)
  ev("MAIL_INBOX_UPDATE", soon)
  ev("MAIL_CLOSED", function() mailOpen = false M._mailBusy = false soon() end)
  ev("BAG_UPDATE_DELAYED", function()
    if mountCache == false or (mountCache and mountCache.item and (API.ItemCount and API.ItemCount(mountCache.item) or 1) == 0) then
      mountCache = nil
    end
    soon()
  end)
  ev("GROUP_ROSTER_UPDATE", soon)
  ev("PLAYER_UPDATE_RESTING", soon)
  ev("ZONE_CHANGED_NEW_AREA", function() mountCache = nil soon() end)
  ev("PLAYER_LEVEL_UP", function() mountCache = nil soon() end)
  ev("LEARNED_SPELL_IN_TAB", function() mountCache = nil soon() end)
  ev("NEW_MOUNT_ADDED", function() mountCache = nil soon() end)
  ev("CVAR_UPDATE", function() if cvar("autoLootDefault") == "1" then M:Clear("loot") end end)
  after(2, function() M.LootHint() M.Layout() M.SyncQuests() M.ProposeAuto() end)
  M.Layout()
end

function M:OnInit()
  -- (4.0 QA) une fois : les automatismes de quête / dialogue sauvés « oui » par l'ancien défaut repartent coupés
  -- (la question ci-dessus les propose ; un clic les remet)
  if self.db and self.db.autoV ~= 1 then
    self.db.autoV = 1
    if not self.db.autoAsked then self.db.autoQuest, self.db.autoReward, self.db.autoGossip = false, false, false end
  end
end

--- redemande au module Quêtes de montrer / cacher son propre bouton d'objet (un seul à l'écran)
function M.SyncQuests()
  local Q = T.modules and T.modules.Quests
  if Q and type(Q.UpdateButton) == "function" then pcall(Q.UpdateButton) end
end

function M:OnDisable()
  T:RunOutOfCombat("Quick:layout", function() if bar then bar:Hide() end end)
  M:Clear("loot")
  T:ClearSuggestion(AUTO_ID)
  after(0.1, M.SyncQuests)
end

function M:OnOptionChanged(key)
  if key == "btnMount" then mountCache = nil end
  if key == "autoQuest" then   -- coché / décoché dans les options : réponse donnée + confirmation
    M.db.autoAsked = true
    T:ClearSuggestion(AUTO_ID)
    M.AutoConfirm(M.db.autoQuest)
  end
  if key == "lootHint" then M._lootHinted = false M.LootHint() end
  M.Layout()
  M.SyncQuests()
end

if T.RegisterSlash then
  T:RegisterSlash("raccourcis", function()
    if not M.db then return end
    M.db.bar = not opt("bar")
    M.Layout()
    M.SyncQuests()
  end, "barre de raccourcis : afficher / cacher (objet de quête, foyer, monture, partage, courrier, vente)")
end
